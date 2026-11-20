import logging
import time

import pytest

from satchel import log as activity
from satchel.store import import_local_zip

from test_satchel import forever, forever_zip, store  # noqa: F401  (fixtures)


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "logs" / "satchel.log"
    assert activity.setup(path) == path
    yield path
    for handler in list(activity.log.handlers):
        activity.log.removeHandler(handler)
        handler.close()


def read(path):
    for handler in activity.log.handlers:
        handler.flush()
    return path.read_text("utf-8")


def stamp(days_ago, now):
    return time.strftime(activity.STAMP, time.localtime(now - days_ago * 86400))


def test_prune_drops_entries_older_than_a_month_with_their_tracebacks(tmp_path):
    now = time.time()
    path = tmp_path / "satchel.log"
    path.write_text(
        f"{stamp(40, now)} ERROR   old failure\nTraceback (most recent call last):\n  old frame\n"
        f"{stamp(32, now)} INFO    old entry\n"
        f"{stamp(20, now)} ERROR   recent failure\nTraceback (most recent call last):\n  recent frame\n"
        f"{stamp(0, now)} INFO    today\n",
        "utf-8",
    )
    activity.prune(path, now=now)
    text = path.read_text("utf-8")
    assert "old" not in text
    assert text.splitlines()[1:3] == ["Traceback (most recent call last):", "  recent frame"]
    assert text.rstrip().endswith("today")


def test_prune_leaves_a_missing_or_current_log_alone(tmp_path):
    activity.prune(tmp_path / "missing.log")
    path = tmp_path / "satchel.log"
    path.write_text(f"{stamp(1, time.time())} INFO    kept\n", "utf-8")
    before = path.stat().st_mtime_ns
    activity.prune(path)
    assert path.stat().st_mtime_ns == before


def test_store_changes_are_logged(tmp_path, store, log_file):  # noqa: F811
    import_local_zip(store, forever_zip(tmp_path))
    import_local_zip(store, forever_zip(tmp_path, version="2.0"))
    store.uninstall(store.rows()[0])
    store.reinstall(store.rows()[0])
    store.remove(store.rows()[0])
    text = read(log_file)
    assert "Installed Sample 1.0" in text
    assert "Updated Sample 2.0" in text and "replaced Sample 1.0" in text
    assert "Moved Sample out of AddOns to" in text
    assert "Uninstalled Sample" in text and "Reinstalled Sample" in text
    assert "Removed local files of Sample" in text


def test_installs_and_updates_say_whether_auto_update_was_on(tmp_path, store, log_file, monkeypatch):  # noqa: F811
    monkeypatch.setattr(activity, "auto_update", False)
    import_local_zip(store, forever_zip(tmp_path))
    monkeypatch.setattr(activity, "auto_update", True)
    import_local_zip(store, forever_zip(tmp_path, version="2.0"))
    lines = [line for line in read(log_file).splitlines() if "Sample" in line and "AddOns" not in line]
    assert lines[0].endswith("(auto-update off)") and "Installed Sample 1.0" in lines[0]
    assert lines[1].endswith("(auto-update on)") and "Updated Sample 2.0" in lines[1]


def test_setup_survives_an_unwritable_location(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert activity.setup(blocker / "satchel.log") is None
    activity.log.info("goes nowhere")
    assert isinstance(activity.log.handlers[0], logging.NullHandler)
