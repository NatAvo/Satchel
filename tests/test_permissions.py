import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from satchel import wow
from satchel.store import Store, import_local_zip, inherit_permissions, locked_folders, make_work_dir

from test_satchel import forever_zip

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows permissions")

# pytest's tmp_path is made with mode 0o700, so it already has the locked-down ACL these
# tests are about. Use an ordinary folder that inherits normally instead.
PLAIN_ROOT = Path(__file__).resolve().parent.parent / "build" / "permission-tests"


@pytest.fixture
def plain(request):
    folder = make_work_dir(PLAIN_ROOT, "t-")
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
def plain_store(plain):
    root = plain / "World of Warcraft"
    path = root / "_forever_"
    path.mkdir(parents=True)
    (path / ".flavor.info").write_text("Product Flavor!STRING:0\nwowf\n")
    (path / "Wow.exe").write_bytes(b"")
    (root / ".build.info").write_text("Branch!STRING:0|Version!STRING:0|Product!STRING:0\nus|1.60.1.70790|wowf\n")
    return Store(wow.read_install(path))


def locked_copy(parent: Path, name: str) -> Path:
    """What Satchel 0.3.0 and older did: unpack in a mkdtemp folder, then move it out."""
    staging = tempfile.mkdtemp(dir=parent)
    os.makedirs(os.path.join(staging, name, "Sub"))
    with open(os.path.join(staging, name, "Sub", "file.lua"), "w") as file:
        file.write("--")
    os.replace(os.path.join(staging, name), parent / "AddOns" / name)
    os.rmdir(staging)
    return parent / "AddOns" / name


def test_mkdtemp_locks_folders_and_work_dirs_do_not(plain):
    (plain / "AddOns").mkdir()
    old = locked_copy(plain, "Old")
    work = make_work_dir(plain, "staging-")
    (work / "New").mkdir()
    os.replace(work / "New", plain / "AddOns" / "New")

    assert locked_folders(plain / "AddOns") == [old]
    inherit_permissions([old])
    assert locked_folders(plain / "AddOns") == []


def test_installed_addons_are_not_locked(tmp_path, plain_store):
    import_local_zip(plain_store, forever_zip(tmp_path))
    assert locked_folders(plain_store.addons_dir) == []


def test_repair_fixes_folders_an_older_version_locked(tmp_path, plain_store):
    import_local_zip(plain_store, forever_zip(tmp_path))
    locked_copy(plain_store.addons_dir.parent, "Old")
    assert plain_store.repair_permissions() == ["Old"]
    assert locked_folders(plain_store.addons_dir) == []
    assert plain_store.repair_permissions() == []
