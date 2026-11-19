"""Turns Satchel Probe's saved sessions into a per-addon pass/fail report.

    python probe/report.py                     # latest session, default WoW install
    python probe/report.py --session 2         # second-latest session
    python probe/report.py --wow "E:\\World of Warcraft\\_forever_"
    python probe/report.py --errors            # every recorded error, grouped by addon

An addon passes a session when every folder that should load did load, and it caused no
Lua errors and no blocked actions. Folders whose TOC doesn't declare Forever (retail-only
modules that ship in the same package) are expected not to load and don't count against it.
Passing the probe is necessary but not sufficient: also try the addon's main features.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from lupa import luajit21

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from satchel import wow  # noqa: E402
from satchel.store import Store  # noqa: E402

DEFAULT_WOW = Path(r"E:\World of Warcraft\_forever_")
PROBE_FOLDER = "!SatchelProbe"
# Not loaded, but nothing is wrong: it waits to be loaded, or the player switched it off.
QUIET_REASONS = {"DEMAND_LOADED", "DISABLED"}
NOT_FOR_THIS_CLIENT = {"INTERFACE_VERSION", "INCOMPATIBLE"}


def load_sessions(saved_variables: Path) -> list[dict]:
    """Reads SatchelProbeDB without letting the file reach anything but plain tables."""
    runtime = luajit21.LuaRuntime(unpack_returned_tuples=True, register_eval=False)
    runtime.execute(
        "for _, name in ipairs({'os','io','require','dofile','loadfile','load','loadstring',"
        "'package','debug','collectgarbage','jit','module','setfenv','getfenv'}) do _G[name] = nil end"
    )
    runtime.execute(saved_variables.read_text("utf-8", errors="replace"))
    db = runtime.globals().SatchelProbeDB
    if db is None or db.sessions is None:
        return []

    def plain(value):
        if luajit21.lua_type(value) == "table":
            keys = list(value.keys())
            if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
                return [plain(value[k]) for k in sorted(keys)]
            return {k: plain(v) for k, v in value.items()}
        return value

    return plain(db.sessions) or []


@dataclass
class AddonResult:
    name: str
    folders: list[str]
    loaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)     # not meant for Forever
    missing: list[str] = field(default_factory=list)     # should have loaded, didn't
    errors: list[dict] = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.errors or self.blocked or self.missing:
            return "FAIL"
        if not self.loaded:
            return "NOT LOADED"
        return "PASS"


_ADDON_PATH = re.compile(r"[Aa]dd[Oo]ns[/\\]([^/\\:\]]+)[/\\]")
# "…while tainted by 'Questie'" / "Lua Taint: Questie": the game names the addon, not a file.
_TAINTED_BY = re.compile(r"tainted by '([^']+)'")


def attribute(error: dict) -> str:
    """The addon an error belongs to. WoW shortens long paths ("...erface/AddOns/X/..."),
    so only "AddOns/<folder>/" is required. The message names the failing file; failing
    that, the first stack frame that isn't Blizzard's code or an error collector."""
    match = _ADDON_PATH.search(error.get("message") or "")
    if match:
        return match.group(1)
    for line in (error.get("stack") or "").splitlines():
        match = _ADDON_PATH.search(line)
        if match and not match.group(1).startswith("Blizzard_") and "BugGrabber" not in line \
                and match.group(1) != PROBE_FOLDER:
            return match.group(1)
    match = _TAINTED_BY.search(error.get("message") or "")
    if match:
        return match.group(1)
    return error.get("addon") or ""


def evaluate(session: dict, entries: dict[str, list[str]], addons_dir: Path | None = None) -> list[AddonResult]:
    """`entries` maps an addon's display name to its folders. `addons_dir` holds the folders
    as they were for that session; it explains folders the game never listed."""
    addons = session.get("addons") or {}
    lowered = {name.lower(): name for name in addons}
    errors = [{**e, "addon": attribute(e)} for e in session.get("errors") or []]
    results = []
    for name, folders in entries.items():
        result = AddonResult(name, folders)
        owned = {folder.lower() for folder in folders}
        for folder in folders:
            info = addons.get(lowered.get(folder.lower(), ""), None)
            if info is None:
                path = addons_dir / folder if addons_dir else None
                if path is not None and not path.is_dir():
                    result.missing.append(f"{folder} (not seen by the game, and no longer on disk to check why)")
                elif path is not None and not wow.loads_on_forever(path):
                    result.skipped.append(folder)  # the game hides it on Forever
                else:
                    result.missing.append(f"{folder} (not seen by the game; restart WoW after installing)")
            elif info.get("loaded"):
                result.loaded.append(folder)
            elif info.get("reason") in NOT_FOR_THIS_CLIENT:
                result.skipped.append(folder)
            elif info.get("reason") not in QUIET_REASONS:
                result.missing.append(f"{folder} ({info.get('reason') or 'not loaded'})")
        result.errors = [e for e in errors if e["addon"].lower() in owned]
        result.blocked = [b for b in session.get("blocked") or [] if (b.get("addon") or "").lower() in owned]
        results.append(result)
    return results


@dataclass
class ErrorGroup:
    message: str
    addon: str
    count: int = 0
    combat: int = 0
    sessions: int = 0
    stack: str = ""


def collect_errors(sessions: list[dict]) -> dict[str, list[ErrorGroup]]:
    """Every distinct error across `sessions`, grouped by the addon it belongs to. Within an
    addon, and among addons, the most frequent come first."""
    groups: dict[str, ErrorGroup] = {}
    for session in sessions:
        for error in session.get("errors") or []:
            message = error.get("message") or ""
            group = groups.get(message)
            if group is None:
                group = groups[message] = ErrorGroup(message, attribute(error) or "?")
            group.count += error.get("count") or 0
            group.combat += error.get("combat") or 0
            group.sessions += 1
            group.stack = group.stack or error.get("stack") or ""
    by_addon: dict[str, list[ErrorGroup]] = {}
    for group in groups.values():
        by_addon.setdefault(group.addon, []).append(group)
    for errors in by_addon.values():
        errors.sort(key=lambda g: -g.count)
    return dict(sorted(by_addon.items(), key=lambda item: (-sum(g.count for g in item[1]), item[0].lower())))


def stack_lines(stack: str, limit: int = 4) -> list[str]:
    """The stack frames worth reading: addon code, not Blizzard's or the error collectors'."""
    lines = []
    for line in stack.splitlines():
        match = _ADDON_PATH.search(line)
        if match and not match.group(1).startswith("Blizzard_") and match.group(1) != PROBE_FOLDER \
                and "BugGrabber" not in line:
            lines.append(line.strip())
        if len(lines) == limit:
            break
    return lines


def print_errors(sessions: list[dict]) -> None:
    grouped = collect_errors(sessions)
    total = sum(g.count for errors in grouped.values() for g in errors)
    print(f"{total} error(s), {sum(map(len, grouped.values()))} different, "
          f"from {len(grouped)} addon(s) across {len(sessions)} session(s).")
    for addon, errors in grouped.items():
        print(f"\n== {addon}: {sum(g.count for g in errors)} error(s), {len(errors)} different")
        for n, group in enumerate(errors, 1):
            when = f", {group.combat} in combat" if group.combat else ""
            print(f"  {n}. x{group.count}{when}, in {group.sessions} session(s)")
            print(f"     {group.message}")
            for line in stack_lines(group.stack):
                print(f"       at {line}")


def satchel_entries(wow_dir: Path) -> dict[str, list[str]]:
    store = Store(wow.read_install(wow_dir))
    return {entry.name: entry.folders for entry in store.entries().values()}


def saved_variables_files(wow_dir: Path) -> list[Path]:
    return sorted((wow_dir / "WTF" / "Account").glob(f"*/SavedVariables/{PROBE_FOLDER}.lua"),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wow", type=Path, default=DEFAULT_WOW)
    parser.add_argument("--session", type=int, default=None, help="1 = latest (the default)")
    parser.add_argument("--addons", type=Path, default=None,
                        help="the AddOns folders as they were for that session, e.g. a Satchel backup; "
                             "folders there that Satchel has no record of are reported one by one")
    parser.add_argument("--errors", action="store_true",
                        help="list every recorded error, grouped by addon, across all saved sessions "
                             "(or only the one chosen with --session)")
    args = parser.parse_args(argv)

    files = saved_variables_files(args.wow)
    if not files:
        print("No probe data yet. Install !SatchelProbe, play, then log out or /reload.")
        return 1
    sessions = load_sessions(files[0])
    if args.errors and args.session is None:
        print_errors(sessions)
        return 0
    args.session = args.session or 1
    if len(sessions) < args.session:
        print(f"Only {len(sessions)} session(s) recorded.")
        return 1
    session = sessions[-args.session]
    if args.errors:
        print_errors([session])
        return 0
    print(f"Session: {session.get('character')}-{session.get('realm')}, client {session.get('client')}, "
          f"{len(session.get('errors') or [])} error(s), {len(session.get('blocked') or [])} blocked action(s)")

    entries = satchel_entries(args.wow)
    addons_dir = args.addons or wow.read_install(args.wow).addons_dir
    if args.addons:
        owned = {folder.lower() for folders in entries.values() for folder in folders}
        for path in sorted(args.addons.iterdir()):
            if path.is_dir() and path.name.lower() not in owned and not path.name.startswith("Blizzard_") \
                    and path.name != PROBE_FOLDER:
                entries[f"{path.name} (folder)"] = [path.name]
    results = evaluate(session, entries, addons_dir)
    for result in sorted(results, key=lambda r: (r.verdict != "FAIL", r.name.lower())):
        print(f"\n[{result.verdict}] {result.name}")
        if result.skipped:
            print(f"    not for Forever (expected): {', '.join(result.skipped)}")
        for problem in result.missing:
            print(f"    did not load: {problem}")
        for error in result.errors:
            print(f"    error x{error.get('count')}: {error.get('message')}")
        for block in result.blocked:
            print(f"    {block.get('kind')} x{block.get('count')}: {block.get('action')}")

    known = {folder.lower() for folders in entries.values() for folder in folders}
    stray = [{**e, "addon": attribute(e)} for e in session.get("errors") or []]
    stray = [e for e in stray if e["addon"].lower() not in known]
    if stray:
        print("\nErrors not tied to a Satchel addon:")
        for error in stray:
            print(f"    [{error.get('addon') or '?'}] x{error.get('count')}: {error.get('message')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
