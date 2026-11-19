from satchel import catalog, wow
from satchel.download import Release
from satchel.gui import forever_text, status_text
from satchel.store import Row


def row(id="github:questie/questie", status=wow.STATUS_OK, managed=True):
    return Row(id, "Addon", "1.0", "github", status, ("Addon",), managed)


def test_status_says_whether_it_is_on_disk_and_current():
    update = Release("github", "questie/questie", "v2", "a.zip", "https://x")
    assert status_text(row(), {}, {}) == "Installed"
    assert status_text(row(), {"github:questie/questie": update}, {}) == "Update available: v2"
    assert status_text(row(), {}, {"github:questie/questie": "offline"}) == "Couldn't check for updates"
    # Missing files matter more than a pending update.
    assert status_text(row(status="missing"), {"github:questie/questie": update}, {}) == "Files missing (not on disk)"


def test_forever_column_separates_tested_from_declared():
    verified = catalog.verified_ids()
    assert "github:questie/questie" in verified and "wowinterface:8092" in verified
    assert forever_text(row(), verified) == "Verified"
    assert forever_text(row(id="github:bigwigsmods/bigwigs"), verified) == "Declared"
    assert forever_text(row(id="github:x/y", status=wow.STATUS_OUTDATED), verified) == "Older client"
    assert forever_text(row(id="github:x/y", status=wow.STATUS_UNKNOWN), verified) == "Not declared"
    assert forever_text(row(status="missing"), verified) == "—"
    # An unmanaged folder that happens to share a name isn't the verified package.
    assert forever_text(row(id="github:questie/questie", managed=False), verified) == "Declared"
