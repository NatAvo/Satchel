"""Finds addons in AddOns that Satchel could manage.

A folder Satchel doesn't manage is matched to a source only when its own TOC names one
(X-WoWI-ID, or a github.com / wowinterface.com link in a header such as X-Website), and
that source has a release Satchel can install. Folders whose TOCs name the same source are
grouped into one addon. Nothing is guessed from folder names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from satchel import wow
from satchel.download import Release, entry_id
from satchel.log import folders as _names, log
from satchel.store import Entry, SatchelError, Store


@dataclass
class Discovery:
    name: str
    folders: list[str]
    release: Release
    installed_version: str

    @property
    def id(self) -> str:
        return entry_id(self.release.source, self.release.repo)

    @property
    def up_to_date(self) -> bool:
        return _normal(self.installed_version) == _normal(self.release.tag)


@dataclass
class Scan:
    found: list[Discovery] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)  # unmanaged folders with no usable source


def _normal(version: str) -> str:
    return re.sub(r"^v(?=\d)", "", version.strip().lower())


def scan(store: Store, clients: dict) -> Scan:
    """Checks every unmanaged folder. Network lookups are cached per source."""
    unmanaged = [row.folders[0] for row in store.rows() if not row.managed]
    managed_ids = set(store.entries())
    infos = {folder: wow.read_folder(store.addons_dir / folder, store.install.interface) for folder in unmanaged}

    lookups: dict[tuple[str, str], Release | None] = {}
    groups: dict[tuple[str, str], list[wow.FolderInfo]] = {}
    result = Scan()
    for folder in unmanaged:
        info = infos[folder]
        chosen = None
        for source, repo in info.links:
            if source not in clients:
                continue
            key = (source, repo.lower())
            if key not in lookups:
                try:
                    lookups[key] = clients[source].latest(repo)
                except SatchelError:
                    lookups[key] = None
            release = lookups[key]
            if release is not None and entry_id(release.source, release.repo) not in managed_ids:
                chosen = key
                break
        if chosen is None:
            result.unmatched.append(folder)
        else:
            groups.setdefault(chosen, []).append(info)

    for key, members in groups.items():
        release = lookups[key]
        lead = min(members, key=lambda info: (len(info.name), info.name.lower()))
        result.found.append(
            Discovery(
                name=release.title or lead.title,
                folders=sorted(info.name for info in members),
                release=release,
                installed_version=lead.version,
            )
        )
    result.found.sort(key=lambda d: d.name.lower())
    return result


def adopt(store: Store, discovery: Discovery) -> Entry:
    """Starts managing the discovered folders as they are; nothing is downloaded or moved."""
    entries = store.entries()
    if discovery.id in entries:
        raise SatchelError(f"{discovery.name} is already managed by Satchel.")
    owned = {folder.lower() for entry in entries.values() for folder in entry.folders}
    taken = [folder for folder in discovery.folders if folder.lower() in owned]
    if taken:
        raise SatchelError(f"{', '.join(taken)} already belong to another addon in Satchel.")
    missing = [folder for folder in discovery.folders if not (store.addons_dir / folder).is_dir()]
    if missing:
        raise SatchelError(f"{', '.join(missing)} is no longer in AddOns. Discover again.")
    # When the files match the latest release, record its tag so no update is offered.
    version = discovery.release.tag if discovery.up_to_date else (discovery.installed_version or "unknown")
    entry = Entry(
        id=discovery.id,
        name=discovery.name,
        source=discovery.release.source,
        version=version,
        folders=list(discovery.folders),
        repo=discovery.release.repo,
        asset=discovery.release.asset_name,
    )
    entries[entry.id] = entry
    store._save(entries)
    log.info("Started managing %s [%s] as %s: %s", entry.name, entry.id, version, _names(entry.folders))
    return entry
