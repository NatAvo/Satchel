"""Reads the Forever client's identity and addon TOC metadata."""

from __future__ import annotations

import os
import re
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path

PRODUCT = "wowf"
FOREVER_SUFFIXES = {"forever", "camelot"}  # Shipping addons use both TOC names.
FOREVER_INTERFACES = range(16000, 20000)  # Forever clients are 1.60 and later.

STATUS_OK = "ok"
STATUS_OUTDATED = "outdated"
STATUS_UNKNOWN = "unknown"

_HEADER = re.compile(r"^##\s*([^:]+?)\s*:\s*(.*?)\s*$", re.MULTILINE)
_UI_ESCAPES = re.compile(r"\|c[0-9a-fA-F]{8}|\|r|\|T.*?\|t|\|A.*?\|a")


@dataclass(frozen=True)
class Install:
    path: Path
    version: str
    interface: int

    @property
    def addons_dir(self) -> Path:
        return self.path / "Interface" / "AddOns"


@dataclass(frozen=True)
class FolderInfo:
    name: str
    title: str
    version: str
    status: str
    # Where the TOC says the addon comes from: ("github", "owner/name") or
    # ("wowinterface", "1234"), GitHub first. Other sites are ignored.
    links: tuple[tuple[str, str], ...] = ()


_GITHUB_LINK = re.compile(r"github\.com/([A-Za-z0-9-]+/[A-Za-z0-9._-]+)", re.IGNORECASE)
_WOWI_LINK = re.compile(r"wowinterface\.com/downloads/(?:info|fileinfo\.php\?id=)(\d+)", re.IGNORECASE)


def _links(headers_list: list[dict[str, str]]) -> tuple[tuple[str, str], ...]:
    github, wowi = [], []
    for headers in headers_list:
        for key, value in headers.items():
            if key == "x-wowi-id" and value.strip().isdigit():
                wowi.append(("wowinterface", value.strip()))
            for match in _GITHUB_LINK.finditer(value):
                repo = match.group(1)
                repo = repo[:-4] if repo.lower().endswith(".git") else repo
                github.append(("github", repo.rstrip(".")))
            for match in _WOWI_LINK.finditer(value):
                wowi.append(("wowinterface", match.group(1)))
    return tuple(dict.fromkeys(github + wowi))


def is_forever_dir(path: Path) -> bool:
    try:
        lines = (path / ".flavor.info").read_text("utf-8-sig").split()
    except OSError:
        return False
    return PRODUCT in lines and (path / "Wow.exe").is_file()


def interface_from_version(version: str) -> int:
    """Converts a client version such as 1.60.1.70790 to its Interface value."""
    major, minor, patch = (int(part) for part in version.split(".")[:3])
    return major * 10000 + minor * 100 + patch


def read_install(path: Path | str) -> Install:
    path = Path(path)
    if not is_forever_dir(path):
        raise ValueError(f"{path} is not a World of Warcraft: Forever folder.")
    version = _read_version(path.parent / ".build.info")
    return Install(path, version, interface_from_version(version))


def _read_version(build_info: Path) -> str:
    try:
        lines = build_info.read_text("utf-8-sig").splitlines()
    except OSError as exc:
        raise ValueError(f"Could not read {build_info}: {exc}") from exc
    if not lines:
        raise ValueError(f"{build_info} is empty.")
    columns = [cell.split("!")[0] for cell in lines[0].split("|")]
    for line in lines[1:]:
        row = dict(zip(columns, line.split("|")))
        if row.get("Product") == PRODUCT and row.get("Version"):
            return row["Version"]
    raise ValueError(f"{build_info} has no Forever version entry.")


def find_installs() -> list[Path]:
    """Looks for Forever in the usual install locations on every drive."""
    parents = ("", "Program Files", "Program Files (x86)", "Games")
    found = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if not os.path.exists(drive):
            continue
        for parent in parents:
            candidate = Path(drive, parent, "World of Warcraft", "_forever_")
            if is_forever_dir(candidate):
                found.append(candidate)
    return found


def is_game_running() -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Wow.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "wow.exe" in result.stdout.lower()


def toc_suffix(folder: str, filename: str) -> str | None:
    """Returns the flavor suffix of a folder's TOC, "" for the base TOC, or
    None when the game would not load the file for this folder."""
    if not filename.lower().endswith(".toc"):
        return None
    stem, folder = filename[:-4].lower(), folder.lower()
    if stem == folder:
        return ""
    if stem.startswith(folder) and stem[len(folder):len(folder) + 1] in ("_", "-"):
        return stem[len(folder) + 1:]
    return None


def classify_folder(folder: str, tocs: dict[str, bytes], client_interface: int) -> FolderInfo:
    """Summarizes one addon folder from its TOC files, keyed by file name."""
    parsed = {}
    for filename, data in tocs.items():
        suffix = toc_suffix(folder, filename)
        if suffix is not None:
            text = data.decode("utf-8-sig", "replace")
            parsed[suffix] = {k.lower(): v for k, v in _HEADER.findall(text)}

    forever = next((parsed[s] for s in parsed if s in FOREVER_SUFFIXES), None)
    chosen = forever or parsed.get("") or next(iter(parsed.values()), {})

    interfaces = [
        value
        for headers in parsed.values()
        for value in _interfaces(headers)
        if value in FOREVER_INTERFACES
    ]
    if interfaces:
        status = STATUS_OK if max(interfaces) >= client_interface else STATUS_OUTDATED
    elif forever is not None:
        status = STATUS_OK
    else:
        status = STATUS_UNKNOWN

    title = _UI_ESCAPES.sub("", chosen.get("title", "")).strip() or folder
    others = [headers for headers in parsed.values() if headers is not chosen]
    return FolderInfo(folder, title, chosen.get("version", ""), status, _links([chosen, *others]))


FOREVER_GAME_TYPE = "camelot"  # The game type Forever checks in AllowLoadGameType.


def _game_types(value: str) -> set[str]:
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def loads_on_forever(path: Path) -> bool:
    """False when the game won't even list the folder on Forever: it has no TOC for this
    folder, or the TOC the game picks allows only other game types or excludes Forever."""
    tocs = {}
    for toc in path.glob("*.toc"):
        suffix = toc_suffix(path.name, toc.name)
        if suffix is None:
            continue
        try:
            tocs[suffix] = {k.lower(): v for k, v in _HEADER.findall(toc.read_text("utf-8-sig", "replace"))}
        except OSError:
            continue
    headers = next((tocs[s] for s in tocs if s in FOREVER_SUFFIXES), None)
    if headers is None:
        headers = tocs.get("")
    if headers is None:
        return False
    allow = _game_types(headers.get("allowloadgametype", ""))
    exclude = _game_types(headers.get("excludeloadgametype", ""))
    return (not allow or FOREVER_GAME_TYPE in allow) and FOREVER_GAME_TYPE not in exclude


def _interfaces(headers: dict[str, str]) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", headers.get("interface", ""))]


def read_folder(path: Path, client_interface: int) -> FolderInfo:
    tocs = {}
    for toc in path.glob("*.toc"):
        try:
            tocs[toc.name] = toc.read_bytes()
        except OSError:
            continue
    return classify_folder(path.name, tocs, client_interface)
