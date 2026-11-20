"""Satchel's activity log: what it changed on disk, what started each change, and what failed.

The log is %LOCALAPPDATA%\\Satchel\\satchel.log. Entries older than KEEP_DAYS are dropped
each time Satchel starts.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Satchel"
LOG_PATH = LOG_DIR / "satchel.log"
KEEP_DAYS = 31
STAMP = "%Y-%m-%d %H:%M:%S"

log = logging.getLogger("satchel")

# Whether Auto-update is ticked, so install and update entries can say so. None until the
# window has read the setting (tests and scripts that use the store directly).
auto_update: bool | None = None


def auto_update_note() -> str:
    if auto_update is None:
        return ""
    return f" (auto-update {'on' if auto_update else 'off'})"


def _entry_time(line: str) -> float | None:
    """The time an entry starts with, or None for a continuation line (such as a traceback)."""
    try:
        return time.mktime(time.strptime(line[:19], STAMP))
    except ValueError:
        return None


def prune(path: Path, keep_days: int = KEEP_DAYS, now: float | None = None) -> None:
    """Drops entries older than keep_days. Lines that continue an entry go with it."""
    try:
        lines = path.read_text("utf-8", errors="replace").splitlines(keepends=True)
    except OSError:
        return
    cutoff = (now if now is not None else time.time()) - keep_days * 86400
    kept, keeping = [], False
    for line in lines:
        started = _entry_time(line)
        if started is not None:
            keeping = started >= cutoff
        if keeping:
            kept.append(line)
    if len(kept) == len(lines):
        return
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text("".join(kept), "utf-8")
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)


def setup(path: Path | None = None) -> Path | None:
    """Starts writing the log. Returns its path, or None if it couldn't be opened.
    Satchel keeps working without a log."""
    path = path or LOG_PATH
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()
    log.setLevel(logging.INFO)
    log.propagate = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        prune(path)  # before opening it: Windows can't replace a file that's open
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        log.addHandler(logging.NullHandler())
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", STAMP))
    log.addHandler(handler)
    return path


def folders(names) -> str:
    return ", ".join(sorted(names)) or "(none)"
