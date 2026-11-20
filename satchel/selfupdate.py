"""Updates Satchel itself from the latest GitHub release of NatAvo/Satchel.

Only the packaged Satchel.exe updates itself. Run from source, Satchel just says that a
newer release exists. Windows lets a running exe be renamed but not overwritten, so the
running exe becomes Satchel.exe.old, the download takes its name, and the .old file is
deleted the next time Satchel starts.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from satchel import __version__
from satchel.download import Release, download_release
from satchel.log import log
from satchel.store import SatchelError

REPO = "NatAvo/Satchel"
ASSET = "Satchel.exe"


def parse_version(text: str) -> tuple[int, ...] | None:
    """(0, 3, 0) for "v0.3.0" or "0.3.0"; None for anything else."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(tag: str, current: str | None = None) -> bool:
    theirs, ours = parse_version(tag), parse_version(current or __version__)
    if theirs is None or ours is None:
        return False
    width = max(len(theirs), len(ours))
    return theirs + (0,) * (width - len(theirs)) > ours + (0,) * (width - len(ours))


def running_exe() -> Path | None:
    """The packaged exe this is running as, or None when running from source."""
    return Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None


def check(github) -> Release | None:
    """The latest release if it's newer than this Satchel, otherwise None."""
    release = github.latest_file(REPO, ASSET)
    return release if is_newer(release.tag) else None


def _old(exe: Path) -> Path:
    return exe.with_name(exe.name + ".old")


def install(release: Release, exe: Path) -> None:
    """Downloads the release next to `exe` and swaps it in. The running program keeps
    working until it exits; the next start runs the new version."""
    new = exe.with_name(exe.name + ".new")
    try:
        download_release(release, new)
        with open(new, "rb") as file:
            if file.read(2) != b"MZ":
                raise SatchelError(f"{release.asset_name} from {release.tag} isn't a Windows program, so it was not used.")
        old = _old(exe)
        old.unlink(missing_ok=True)
        os.replace(exe, old)
        try:
            os.replace(new, exe)
        except OSError:
            os.replace(old, exe)
            raise
    except OSError as exc:
        raise SatchelError(f"Could not replace {exe.name}, so Satchel wasn't updated: {exc}") from exc
    finally:
        new.unlink(missing_ok=True)
    log.info("Updated Satchel %s to %s (%s)", __version__, release.tag, exe)


def restart(exe: Path) -> None:
    """Starts `exe` as a fresh program. The caller then closes this one."""
    env = dict(os.environ)
    # A one-file PyInstaller exe started from another one would otherwise reuse the
    # parent's unpacked files, which are deleted when the parent exits.
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([str(exe)], env=env, close_fds=True)


def clean_up(exe: Path | None = None) -> None:
    """Deletes the exe a previous update replaced. If the old program hasn't finished
    closing yet, the next start tries again."""
    exe = exe or running_exe()
    if exe is None:
        return
    try:
        _old(exe).unlink(missing_ok=True)
    except OSError:
        pass
