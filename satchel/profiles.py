"""Saved addon loadouts.

A profile is a snapshot: a copy of every addon folder in AddOns (managed or not) plus
Satchel's records for them, kept in _satchel/profiles. Restoring puts AddOns back to
exactly that set. Addons the profile doesn't have are uninstalled (kept in
_satchel/uninstalled and still listed), folders the profile replaces with its own copy go
to Satchel's backups, and folders that are already identical are left alone.
WTF, which holds settings and SavedVariables, is never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from satchel import wow
from satchel.log import folders as _names, log
from satchel.store import Entry, SatchelError, Store, _slug

PROFILE_FILE = "profile.json"
MAX_NAME = 60


@dataclass
class Profile:
    name: str
    created: float
    entries: list[Entry]
    folders: list[str]
    path: Path = field(repr=False)

    @property
    def addon_count(self) -> int:
        """Managed addons plus folders Satchel doesn't manage."""
        owned = {folder.lower() for entry in self.entries for folder in entry.folders}
        return len(self.entries) + sum(folder.lower() not in owned for folder in self.folders)


@dataclass
class RestorePlan:
    remove: list[str]  # folders the profile replaces with its own copy (to backups)
    add: list[str]  # folders copied in from the profile
    keep: list[str]  # already identical
    uninstall: list[str] = field(default_factory=list)  # not in the profile: uninstalled, files kept

    @property
    def changes(self) -> bool:
        return bool(self.remove or self.add or self.uninstall)


class Profiles:
    def __init__(self, store: Store):
        self.store = store
        self.dir = store.work_dir / "profiles"

    # Reading ------------------------------------------------------------------

    def list(self) -> list[Profile]:
        if not self.dir.exists():
            return []
        profiles = []
        for path in self.dir.iterdir():
            unfinished = path.name.startswith(("saving-", "replacing-"))
            if path.is_dir() and not unfinished and (path / PROFILE_FILE).is_file():
                try:
                    profiles.append(self._read(path))
                except (OSError, ValueError, KeyError, TypeError):
                    continue  # A damaged profile is skipped rather than breaking the list.
        return sorted(profiles, key=lambda profile: profile.name.lower())

    def _read(self, path: Path) -> Profile:
        data = json.loads((path / PROFILE_FILE).read_text("utf-8"))
        return Profile(
            name=data["name"],
            created=data["created"],
            entries=[Entry(**item) for item in data["entries"]],
            folders=list(data["folders"]),
            path=path,
        )

    def _on_disk(self) -> list[str]:
        return sorted(
            path.name
            for path in self.store.addons_dir.iterdir()
            if path.is_dir() and not path.name.lower().startswith("blizzard_")
        )

    # Saving -------------------------------------------------------------------

    def save(self, name: str) -> Profile:
        """Saves the current AddOns as `name`, replacing a profile with that name."""
        name = " ".join(name.split())
        if not name:
            raise SatchelError("Give the profile a name.")
        if len(name) > MAX_NAME:
            raise SatchelError(f"Profile names can be at most {MAX_NAME} characters.")
        target = self.dir / _slug(name)
        if (target / PROFILE_FILE).is_file():
            existing = self._read(target)
            if existing.name.lower() != name.lower():
                raise SatchelError(f'That name is too close to the profile "{existing.name}". Pick another.')

        folders = self._on_disk()
        entries = list(self.store.entries().values())
        self.dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="saving-", dir=self.dir))
        try:
            try:
                for folder in folders:
                    shutil.copytree(self.store.addons_dir / folder, staging / "addons" / folder)
            except OSError as exc:
                raise SatchelError(f"Could not copy the addons into the profile: {exc}") from exc
            payload = {
                "name": name,
                "created": time.time(),
                "entries": [asdict(entry) for entry in entries],
                "folders": folders,
            }
            (staging / PROFILE_FILE).write_text(json.dumps(payload, indent=2), "utf-8")
            old = None
            if target.exists():
                old = self.dir / f"replacing-{os.getpid()}-{time.time_ns()}"
                os.replace(target, old)
            os.replace(staging, target)
            if old is not None:
                shutil.rmtree(old, ignore_errors=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        log.info('Saved profile "%s" with %s', name, _names(folders))
        return self._read(target)

    def delete(self, profile: Profile) -> None:
        if profile.path.parent != self.dir:
            raise SatchelError("That profile isn't in Satchel's profile folder.")
        shutil.rmtree(profile.path)
        log.info('Deleted profile "%s"', profile.name)

    # Restoring ----------------------------------------------------------------

    def plan(self, profile: Profile) -> RestorePlan:
        current = self._on_disk()
        now = _owners(self.store.entries().values())
        then = _owners(profile.entries)
        profile_ids = {entry.id for entry in profile.entries if not entry.uninstalled}
        wanted = {folder.lower(): folder for folder in profile.folders}
        keep, remove, uninstall = [], [], []
        for folder in current:
            key = folder.lower()
            same = key in wanted and now.get(key) is not None and now.get(key) == then.get(key)
            if same:
                keep.append(folder)
            elif key in wanted or (now.get(key) or ("",))[0] in profile_ids:
                remove.append(folder)  # the profile brings its own copy of it
            else:
                uninstall.append(folder)
        kept = {folder.lower() for folder in keep}
        add = [folder for folder in profile.folders if folder.lower() not in kept]
        return RestorePlan(remove=remove, add=add, keep=keep, uninstall=uninstall)

    def _shelved_entries(self, plan: RestorePlan) -> dict[str, tuple[Entry, list[str]]]:
        """The records that the plan's uninstalled folders are kept under, one per addon."""
        current = {entry.id: entry for entry in self.store.entries().values() if not entry.uninstalled}
        owner = {folder.lower(): entry for entry in current.values() for folder in entry.folders}
        groups: dict[str, tuple[Entry, list[str]]] = {}
        for folder in plan.uninstall:
            entry = owner.get(folder.lower())
            if entry is None:  # not managed: it gets a record just for keeping it
                info = wow.read_folder(self.store.addons_dir / folder, self.store.install.interface)
                entry = Entry(id=f"folder:{folder}", name=info.title, source="manual",
                              version=info.version, folders=[folder])
            groups.setdefault(entry.id, (entry, []))[1].append(folder)
        return {
            addon_id: (replace(entry, folders=folders, uninstalled=True), folders)
            for addon_id, (entry, folders) in groups.items()
        }

    def restore(self, profile: Profile) -> RestorePlan:
        """Puts AddOns back to the profile. Addons the profile doesn't have are uninstalled
        (kept, and still listed), so switching back and forth loses nothing."""
        plan = self.plan(profile)
        if not plan.changes:
            log.info('Restoring profile "%s": nothing to change', profile.name)
            return plan
        log.info('Restoring profile "%s": uninstalling %s; replacing %s; adding %s', profile.name,
                 _names(plan.uninstall), _names(plan.remove), _names(plan.add))
        store = self.store
        before = store.entries()
        shelved = self._shelved_entries(plan)
        entries = {entry.id: entry for entry in profile.entries}
        # Addons already uninstalled stay that way unless the profile installs them. Where the
        # profile also lists one as uninstalled, the current record describes the kept files.
        def describes_kept_files(addon_id: str) -> bool:
            return addon_id not in entries or entries[addon_id].uninstalled

        for entry in before.values():
            if entry.uninstalled and describes_kept_files(entry.id):
                entries[entry.id] = entry
        for addon_id, (entry, _folders) in shelved.items():
            if describes_kept_files(addon_id):
                entries[addon_id] = entry
        superseded = [entry for entry in before.values()
                      if entry.uninstalled and entry.id in entries and not entries[entry.id].uninstalled]

        source = profile.path / "addons"
        staging = Path(tempfile.mkdtemp(prefix="staging-", dir=store.work_dir))
        moved: list[tuple[list[str], Path]] = []
        try:
            try:
                for folder in plan.add:
                    shutil.copytree(source / folder, staging / folder)
            except OSError as exc:
                raise SatchelError(f'Could not read the profile "{profile.name}", so nothing was changed: {exc}') from exc
            try:
                for addon_id, (entry, folders) in shelved.items():
                    shelf = store.shelf(addon_id)
                    if shelf.exists() and any(shelf.iterdir()):
                        store._discard_shelf(entry)  # an older kept copy of the same addon
                    store._move_all(folders, store.addons_dir, shelf)
                    moved.append((folders, shelf))
                store._swap(set(plan.remove), staging, entries, label=f"before-profile-{profile.name}")
            except SatchelError:
                for folders, shelf in moved:
                    store._move_all(folders, shelf, store.addons_dir)
                raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        for entry in superseded:
            store._discard_shelf(entry)  # the profile's own copy replaced the kept one
        return plan


def _owners(entries) -> dict[str, tuple[str, str]]:
    """folder (lower-case) -> (addon id, version) for managed folders."""
    owners = {}
    for entry in entries:
        if entry.uninstalled:
            continue  # Its folders are kept outside AddOns.
        for folder in entry.folders:
            owners[folder.lower()] = (entry.id, entry.version)
    return owners
