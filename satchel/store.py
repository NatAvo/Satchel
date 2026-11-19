"""Validates addon ZIPs and swaps folders into AddOns with rollback.

Only folders in Interface/AddOns and Satchel's own _satchel folder are changed.
WTF, which holds settings and SavedVariables, is never touched.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from satchel import wow

MAX_ENTRIES = 100_000
MAX_UNPACKED_BYTES = 2 * 1024**3
BACKUPS_TO_KEEP = 10

_RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {
    f"{device}{n}" for device in ("com", "lpt") for n in range(1, 10)
}


class SatchelError(Exception):
    """A problem to show the user as written."""


class NotMarkedForForever(SatchelError):
    """The package is valid but does not declare Forever support."""


@dataclass
class Entry:
    id: str
    name: str
    source: str  # "github", "wowinterface", or "local"
    version: str
    folders: list[str]
    repo: str = ""
    asset: str = ""
    installed_at: float = field(default_factory=time.time)
    # Uninstalled: its folders are kept in _satchel/uninstalled instead of AddOns.
    uninstalled: bool = False


@dataclass(frozen=True)
class Row:
    """One line in the addon list."""

    id: str
    name: str
    version: str
    source: str
    status: str
    folders: tuple[str, ...]
    managed: bool
    repo: str = ""


def _safe_parts(raw_name: str) -> tuple[str, ...]:
    name = raw_name.replace("\\", "/")
    path = PurePosixPath(name)
    parts = path.parts
    if path.is_absolute() or not parts:
        raise SatchelError(f"The ZIP contains an unsafe path: {raw_name}")
    for part in parts:
        stem = part.split(".")[0].lower()
        if part in ("..", ".") or ":" in part or part != part.rstrip(" .") or stem in _RESERVED_NAMES:
            raise SatchelError(f"The ZIP contains an unsafe path: {raw_name}")
    return parts


def inspect_zip(zip_path: Path, client_interface: int) -> dict[str, wow.FolderInfo]:
    """Validates a package without extracting it and describes its addon folders."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise SatchelError("The ZIP contains too many files.")
            if sum(info.file_size for info in infos) > MAX_UNPACKED_BYTES:
                raise SatchelError("The ZIP is too large when unpacked.")

            tocs: dict[str, dict[str, bytes]] = {}
            for info in infos:
                parts = _safe_parts(info.filename)
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise SatchelError(f"The ZIP contains a symbolic link: {info.filename}")
                if parts[0] == "__MACOSX" or (len(parts) == 1 and not info.is_dir()):
                    continue
                folder = tocs.setdefault(parts[0], {})
                if len(parts) == 2 and wow.toc_suffix(parts[0], parts[1]) is not None:
                    folder[parts[1]] = archive.read(info)
    except zipfile.BadZipFile as exc:
        raise SatchelError(f"{zip_path.name} is not a valid ZIP file.") from exc

    if not tocs:
        raise SatchelError("The ZIP does not contain any addon folders.")
    for name, folder_tocs in tocs.items():
        if not folder_tocs:
            raise SatchelError(
                f'"{name}" has no matching TOC file, so it is not an installable addon. '
                "Source-code ZIPs from GitHub usually look like this; use a release ZIP."
            )
        if name.lower().startswith("blizzard_"):
            raise SatchelError(f'The ZIP tries to replace Blizzard\'s own "{name}" addon.')
    return {
        name: wow.classify_folder(name, folder_tocs, client_interface)
        for name, folder_tocs in tocs.items()
    }


def _extract(zip_path: Path, destination: Path, folders: set[str]) -> None:
    written = 0
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            parts = _safe_parts(info.filename)
            if parts[0] not in folders or (len(parts) == 1 and not info.is_dir()):
                continue
            target = root.joinpath(*parts)
            if not target.resolve().is_relative_to(root):
                raise SatchelError(f"The ZIP contains an unsafe path: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as output:
                while chunk := source.read(1024 * 1024):
                    written += len(chunk)
                    # Sizes in ZIP headers can lie, so count the real output.
                    if written > MAX_UNPACKED_BYTES:
                        raise SatchelError("The ZIP is too large when unpacked.")
                    output.write(chunk)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")[:60] or "addon"


class Store:
    def __init__(self, install: wow.Install):
        self.install = install
        self.addons_dir = install.addons_dir
        self.work_dir = install.path / "_satchel"
        self.backups_dir = self.work_dir / "backups"
        self.uninstalled_dir = self.work_dir / "uninstalled"
        self.manifest_path = self.work_dir / "manifest.json"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.addons_dir.mkdir(parents=True, exist_ok=True)

    # Manifest -----------------------------------------------------------------

    def entries(self) -> dict[str, Entry]:
        try:
            data = json.loads(self.manifest_path.read_text("utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise SatchelError(f"Could not read {self.manifest_path}: {exc}") from exc
        return {item["id"]: Entry(**item) for item in data["addons"]}

    def _save(self, entries: dict[str, Entry]) -> None:
        payload = {"addons": [asdict(entry) for entry in entries.values()]}
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), "utf-8")
        os.replace(temporary, self.manifest_path)

    # Install ------------------------------------------------------------------

    def install_zip(
        self,
        zip_path: Path,
        *,
        id: str,
        name: str,
        source: str,
        version: str,
        repo: str = "",
        asset: str = "",
        allow_unverified: bool = False,
    ) -> Entry:
        folders = inspect_zip(zip_path, self.install.interface)
        if not allow_unverified and all(
            info.status == wow.STATUS_UNKNOWN for info in folders.values()
        ):
            raise NotMarkedForForever(
                f"{name} does not declare support for Forever, so it may not work."
            )

        entries = self.entries()
        wanted = {folder.lower() for folder in folders}
        replaced = []
        for entry in entries.values():
            if not wanted & {folder.lower() for folder in entry.folders}:
                continue
            if entry.id != id and not (entry.source == "local" and source == "local"):
                raise SatchelError(
                    f'This package includes folders owned by "{entry.name}". Remove that addon first.'
                )
            replaced.append(entry)
        same = entries.get(id)
        if same is not None and same not in replaced:
            replaced.append(same)

        to_remove = {folder for entry in replaced for folder in entry.folders} | set(folders)
        staging = Path(tempfile.mkdtemp(prefix="staging-", dir=self.work_dir))
        try:
            try:
                _extract(zip_path, staging, set(folders))
            except OSError as exc:
                raise SatchelError(
                    f"Could not unpack {name}, so nothing was changed: {exc}. "
                    "Windows limits paths to 260 characters, so a very long install path can cause this."
                ) from exc
            entry = Entry(
                id=id,
                name=name,
                source=source,
                version=version,
                folders=sorted(folders),
                repo=repo,
                asset=asset,
            )
            remaining = {key: value for key, value in entries.items() if value not in replaced}
            remaining[id] = entry
            self._swap(to_remove, staging, remaining, label=name)
            # A fresh install supersedes a kept uninstalled copy; it goes to backups.
            for old in replaced:
                if old.uninstalled:
                    self._discard_shelf(old)
            return entry
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _swap(
        self,
        to_remove: set[str],
        staging: Path | None,
        entries: dict[str, Entry],
        label: str,
    ) -> None:
        """Moves folders in and out of AddOns, undoing everything if a step fails."""
        backup = self._new_backup_path(label)
        moved_out: list[str] = []
        moved_in: list[str] = []
        try:
            for folder in sorted(to_remove):
                current = self.addons_dir / folder
                if current.exists():
                    backup.mkdir(parents=True, exist_ok=True)
                    os.replace(current, backup / folder)
                    moved_out.append(folder)
            if staging is not None:
                for incoming in sorted(staging.iterdir()):
                    os.replace(incoming, self.addons_dir / incoming.name)
                    moved_in.append(incoming.name)
            self._save(entries)
        except OSError as exc:
            for folder in moved_in:
                shutil.rmtree(self.addons_dir / folder, ignore_errors=True)
            for folder in moved_out:
                os.replace(backup / folder, self.addons_dir / folder)
            if backup.exists() and not any(backup.iterdir()):
                backup.rmdir()
            raise SatchelError(
                f"Could not change the addon files, so nothing was changed: {exc}. "
                "If the game is running, close it and try again."
            ) from exc
        self._prune_backups()

    def _new_backup_path(self, label: str) -> Path:
        return self.backups_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}-{_slug(label)}"

    def _prune_backups(self) -> None:
        if not self.backups_dir.exists():
            return
        backups = sorted(path for path in self.backups_dir.iterdir() if path.is_dir())
        for old in backups[:-BACKUPS_TO_KEEP]:
            shutil.rmtree(old, ignore_errors=True)

    # Remove -------------------------------------------------------------------

    def remove(self, row: Row) -> None:
        """Removes the addon's folders (to backups) and stops managing it."""
        entries = self.entries()
        entry = entries.get(row.id) if row.managed else None
        if entry is not None and entry.uninstalled:
            self._discard_shelf(entry)
            entries.pop(entry.id, None)
            self._save(entries)
            return
        if row.managed:
            entries.pop(row.id, None)
        self._swap(set(row.folders), None, entries, label=row.name)

    def forget(self, row: Row) -> None:
        """Stops managing the addon. Its folders stay in AddOns and show as not managed."""
        if not row.managed:
            raise SatchelError(f"{row.name} isn't managed by Satchel, so there's nothing to remove from the list.")
        if row.status == "uninstalled":
            raise SatchelError(f"{row.name} is uninstalled. Reinstall it, or remove its files instead.")
        entries = self.entries()
        entries.pop(row.id, None)
        self._save(entries)

    # Uninstall ----------------------------------------------------------------

    def shelf(self, addon_id: str) -> Path:
        """Where an uninstalled addon's folders are kept."""
        return self.uninstalled_dir / _slug(addon_id)

    def _move_all(self, folders: list[str], source: Path, target: Path) -> list[str]:
        """Moves folders between directories, putting them all back if one move fails."""
        moved = []
        try:
            target.mkdir(parents=True, exist_ok=True)
            for folder in folders:
                os.replace(source / folder, target / folder)
                moved.append(folder)
        except OSError as exc:
            for folder in moved:
                os.replace(target / folder, source / folder)
            raise SatchelError(
                f"Could not move the addon's folders, so nothing was changed: {exc}. "
                "If the game is running, close it and try again."
            ) from exc
        return moved

    def uninstall(self, row: Row) -> Entry:
        """Takes the addon out of AddOns, so the game stops loading it, but keeps its
        folders in _satchel/uninstalled and keeps it on the list so it can be reinstalled."""
        if row.status in ("missing", "uninstalled"):
            raise SatchelError(f"{row.name} isn't installed.")
        entries = self.entries()
        entry = entries.get(row.id) if row.managed else None
        if entry is None:
            # A folder Satchel doesn't manage gets a record just for keeping it.
            entry = Entry(id=row.id, name=row.name, source="manual", version=row.version, folders=list(row.folders))
        shelf = self.shelf(entry.id)
        if shelf.exists() and any(shelf.iterdir()):
            raise SatchelError(f"An uninstalled copy of {row.name} is already kept in {shelf}. Move it away first.")
        present = [folder for folder in entry.folders if (self.addons_dir / folder).is_dir()]
        self._move_all(present, self.addons_dir, shelf)
        entry.uninstalled = True
        entries[entry.id] = entry
        try:
            self._save(entries)
        except OSError as exc:
            self._move_all(present, shelf, self.addons_dir)
            raise SatchelError(f"Could not update Satchel's list, so nothing was changed: {exc}") from exc
        return entry

    def reinstall(self, row: Row) -> None:
        """Puts an uninstalled addon's kept folders back into AddOns."""
        entries = self.entries()
        entry = entries.get(row.id)
        if entry is None or not entry.uninstalled:
            raise SatchelError(f"{row.name} isn't uninstalled.")
        shelf = self.shelf(entry.id)
        kept = [folder for folder in entry.folders if (shelf / folder).is_dir()]
        if not kept:
            raise SatchelError(f"The kept files for {row.name} are gone, so it can't be reinstalled. Install it again instead.")
        clash = [folder for folder in kept if (self.addons_dir / folder).exists()]
        if clash:
            raise SatchelError(
                f"AddOns already has {', '.join(clash)}, so {row.name} can't go back. Remove that addon first."
            )
        self._move_all(kept, shelf, self.addons_dir)
        if entry.source == "manual":
            entries.pop(entry.id)  # It was never managed; it goes back to Not managed.
        else:
            entry.uninstalled = False
        try:
            self._save(entries)
        except OSError as exc:
            self._move_all(kept, self.addons_dir, shelf)
            raise SatchelError(f"Could not update Satchel's list, so nothing was changed: {exc}") from exc
        if not any(shelf.iterdir()):
            shelf.rmdir()

    def _discard_shelf(self, entry: Entry) -> None:
        """Moves an uninstalled addon's kept folders to backups."""
        shelf = self.shelf(entry.id)
        if shelf.exists():
            self.backups_dir.mkdir(parents=True, exist_ok=True)
            os.replace(shelf, self._new_backup_path(f"uninstalled-{entry.name}"))
            self._prune_backups()

    # List ---------------------------------------------------------------------

    def rows(self) -> list[Row]:
        entries = self.entries()
        on_disk = {
            path.name: path
            for path in self.addons_dir.iterdir()
            if path.is_dir() and not path.name.lower().startswith("blizzard_")
        }
        rows = []
        claimed = set()
        for entry in entries.values():
            if entry.uninstalled:
                # Its folder names aren't claimed: a same-named folder in AddOns is another copy.
                shelf = self.shelf(entry.id)
                kept = all((shelf / folder).is_dir() for folder in entry.folders)
                rows.append(Row(entry.id, entry.name, entry.version, entry.source,
                                "uninstalled" if kept else "missing", tuple(entry.folders), True, entry.repo))
                continue
            infos = []
            missing = False
            for folder in entry.folders:
                claimed.add(folder)
                if folder in on_disk:
                    infos.append(wow.read_folder(on_disk[folder], self.install.interface))
                else:
                    missing = True
            rows.append(
                Row(
                    entry.id,
                    entry.name,
                    entry.version,
                    entry.source,
                    "missing" if missing else _package_status(infos),
                    tuple(entry.folders),
                    True,
                    entry.repo,
                )
            )
        for folder, path in on_disk.items():
            if folder not in claimed:
                info = wow.read_folder(path, self.install.interface)
                rows.append(
                    Row(f"folder:{folder}", info.title, info.version, "manual", info.status, (folder,), False)
                )
        return sorted(rows, key=lambda row: row.name.lower())


def _package_status(infos: list[wow.FolderInfo]) -> str:
    statuses = {info.status for info in infos}
    for status in (wow.STATUS_OK, wow.STATUS_OUTDATED):
        if status in statuses:
            return status
    return wow.STATUS_UNKNOWN


def import_local_zip(store: Store, zip_path: Path, allow_unverified: bool = False) -> Entry:
    folders = inspect_zip(zip_path, store.install.interface)
    primary = min(folders, key=lambda folder: (len(folder), folder.lower()))
    info = folders[primary]
    return store.install_zip(
        zip_path,
        id=f"local:{primary.lower()}",
        name=info.title,
        source="local",
        version=info.version or zip_path.stem,
        asset=zip_path.name,
        allow_unverified=allow_unverified,
    )
