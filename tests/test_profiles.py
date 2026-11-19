import zipfile
from pathlib import Path

import pytest

from satchel import wow
from satchel.profiles import Profiles
from satchel.store import SatchelError, Store


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "World of Warcraft"
    path = root / "_forever_"
    path.mkdir(parents=True)
    (path / ".flavor.info").write_text("Product Flavor!STRING:0\nwowf\n")
    (path / "Wow.exe").write_bytes(b"")
    (root / ".build.info").write_text(
        "Branch!STRING:0|Version!STRING:0|Product!STRING:0\nus|1.60.1.70790|wowf\n"
    )
    return Store(wow.read_install(path))


def install(store: Store, tmp_path: Path, name: str, version: str = "1.0", folders=None) -> None:
    folders = folders or [name]
    archive = tmp_path / f"{name}-{version}.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for folder in folders:
            z.writestr(f"{folder}/{folder}_Forever.toc", f"## Interface: 16001\n## Title: {folder}\n")
            z.writestr(f"{folder}/code.lua", f"-- {version}")
    store.install_zip(archive, id=f"github:test/{name.lower()}", name=name, source="github",
                      version=version, repo=f"test/{name}")


def manual(store: Store, folder: str, text: str = "manual") -> None:
    (store.addons_dir / folder).mkdir()
    (store.addons_dir / folder / "file.lua").write_text(text)


def code(store: Store, folder: str) -> str:
    return (store.addons_dir / folder / "code.lua").read_text()


def test_save_and_restore_returns_exactly_the_saved_loadout(store, tmp_path):
    install(store, tmp_path, "Keep")
    install(store, tmp_path, "Pack", folders=["Pack", "Pack_Extra"])
    manual(store, "!Probe")
    (store.addons_dir / "Blizzard_Ignored").mkdir()
    profiles = Profiles(store)
    saved = profiles.save("  My   loadout ")
    assert saved.name == "My loadout"
    assert saved.folders == ["!Probe", "Keep", "Pack", "Pack_Extra"]
    assert saved.addon_count == 3

    # Testing: add a candidate, update one addon, remove another, change a manual folder.
    install(store, tmp_path, "Candidate")
    install(store, tmp_path, "Keep", version="2.0")
    store.remove(next(r for r in store.rows() if r.name == "Pack"))
    (store.addons_dir / "!Probe" / "file.lua").write_text("changed")

    plan = profiles.plan(saved)
    assert sorted(plan.remove) == ["!Probe", "Keep"]       # the profile brings its own copies
    assert plan.uninstall == ["Candidate"]                 # not in the profile
    assert sorted(plan.add) == ["!Probe", "Keep", "Pack", "Pack_Extra"]
    profiles.restore(saved)

    on_disk = sorted(p.name for p in store.addons_dir.iterdir())
    assert on_disk == ["!Probe", "Blizzard_Ignored", "Keep", "Pack", "Pack_Extra"]
    assert code(store, "Keep") == "-- 1.0"
    assert (store.addons_dir / "!Probe" / "file.lua").read_text() == "manual"
    entries = store.entries()
    assert {e.name: (e.version, e.uninstalled) for e in entries.values()} == {
        "Keep": ("1.0", False), "Pack": ("1.0", False), "Candidate": ("1.0", True)}
    # The candidate is uninstalled, not deleted: still listed and reinstallable.
    candidate = next(r for r in store.rows() if r.name == "Candidate")
    assert candidate.status == "uninstalled"
    store.reinstall(candidate)
    assert code(store, "Candidate") == "-- 1.0"
    # What the profile replaced is recoverable from Satchel's backups.
    backed_up = {p.name for backup in store.backups_dir.iterdir() for p in backup.iterdir()}
    assert "Keep" in backed_up and "Candidate" not in backed_up


def test_switching_profiles_back_and_forth_loses_nothing(store, tmp_path):
    install(store, tmp_path, "Base")
    manual(store, "Loose")
    profiles = Profiles(store)
    lean = profiles.save("Lean")
    install(store, tmp_path, "Raid")
    full = profiles.save("Full")

    profiles.restore(lean)
    rows = {r.name: r.status for r in store.rows()}
    assert rows["Raid"] == "uninstalled" and not (store.addons_dir / "Raid").exists()

    profiles.restore(full)   # the profile's own copy comes back; the kept one goes to backups
    assert code(store, "Raid") == "-- 1.0"
    assert {r.name: r.status for r in store.rows()}["Raid"] == wow.STATUS_OK
    assert not store.entries()["github:test/raid"].uninstalled
    assert not any(store.uninstalled_dir.iterdir())

    profiles.restore(lean)
    (store.addons_dir / "Raid").mkdir()   # appears again by hand, unmanaged
    profiles.restore(full)
    assert code(store, "Raid") == "-- 1.0"
    assert not any(store.uninstalled_dir.iterdir())
    assert (store.addons_dir / "Loose" / "file.lua").exists()   # in both profiles, never moved


def test_identical_addons_are_left_in_place(store, tmp_path):
    install(store, tmp_path, "Same")
    profiles = Profiles(store)
    saved = profiles.save("Base")
    install(store, tmp_path, "Extra")
    marker = store.addons_dir / "Same" / "untouched.txt"
    marker.write_text("still here")
    plan = profiles.restore(saved)
    assert plan.keep == ["Same"] and plan.uninstall == ["Extra"] and plan.remove == [] and plan.add == []
    assert marker.exists()
    assert not profiles.plan(saved).changes


def test_profiles_list_replace_and_delete(store, tmp_path):
    install(store, tmp_path, "One")
    profiles = Profiles(store)
    profiles.save("Raid")
    install(store, tmp_path, "Two")
    profiles.save("raid")  # same name, different case: replaces
    listed = profiles.list()
    assert [p.name for p in listed] == ["raid"] and listed[0].folders == ["One", "Two"]
    with pytest.raises(SatchelError):
        profiles.save("   ")
    profiles.delete(listed[0])
    assert profiles.list() == []


def test_a_damaged_profile_is_skipped(store, tmp_path):
    profiles = Profiles(store)
    profiles.save("Good")
    broken = profiles.dir / "Broken"
    broken.mkdir()
    (broken / "profile.json").write_text("{not json")
    assert [p.name for p in profiles.list()] == ["Good"]
