import hashlib

import pytest

from satchel import selfupdate
from satchel.download import Release
from satchel.store import SatchelError


@pytest.mark.parametrize("tag, current, newer", [
    ("v0.3.0", "0.2.0", True),
    ("0.2.1", "0.2.0", True),
    ("v0.2", "0.2.0", False),
    ("v0.2.0", "0.2.0", False),
    ("v0.1.9", "0.2.0", False),
    ("v1.0.0-beta", "0.2.0", False),
    ("nightly", "0.2.0", False),
])
def test_is_newer(tag, current, newer):
    assert selfupdate.is_newer(tag, current) is newer


class FakeGitHub:
    def __init__(self, tag):
        self.tag = tag
        self.asked = None

    def latest_file(self, repo, name):
        self.asked = (repo, name)
        return Release("github", repo, self.tag, name, "https://example.invalid/Satchel.exe")


def test_check_offers_only_newer_releases(monkeypatch):
    monkeypatch.setattr(selfupdate, "__version__", "0.2.0")
    github = FakeGitHub("v0.3.0")
    assert selfupdate.check(github).tag == "v0.3.0"
    assert github.asked == ("NatAvo/Satchel", "Satchel.exe")
    assert selfupdate.check(FakeGitHub("v0.2.0")) is None


def local_release(path, content, digest=True):
    path.write_bytes(content)
    sha = f"sha256:{hashlib.sha256(content).hexdigest()}" if digest else ""
    return Release("github", "NatAvo/Satchel", "v9.0.0", "Satchel.exe", path.as_uri(), sha)


def test_install_swaps_the_exe_and_keeps_the_old_one_until_clean_up(tmp_path):
    exe = tmp_path / "app" / "Satchel.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ old")
    release = local_release(tmp_path / "download.exe", b"MZ new")

    selfupdate.install(release, exe)
    assert exe.read_bytes() == b"MZ new"
    assert (exe.parent / "Satchel.exe.old").read_bytes() == b"MZ old"
    assert not (exe.parent / "Satchel.exe.new").exists()

    selfupdate.clean_up(exe)
    assert sorted(path.name for path in exe.parent.iterdir()) == ["Satchel.exe"]


def test_install_rejects_a_file_that_is_not_a_program(tmp_path):
    exe = tmp_path / "Satchel.exe"
    exe.write_bytes(b"MZ old")
    with pytest.raises(SatchelError, match="isn't a Windows program"):
        selfupdate.install(local_release(tmp_path / "download.exe", b"<html>"), exe)
    assert exe.read_bytes() == b"MZ old"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Satchel.exe", "download.exe"]


def test_install_rejects_a_bad_checksum(tmp_path):
    exe = tmp_path / "Satchel.exe"
    exe.write_bytes(b"MZ old")
    release = local_release(tmp_path / "download.exe", b"MZ new")
    release = Release(release.source, release.repo, release.tag, release.asset_name, release.url, "sha256:" + "0" * 64)
    with pytest.raises(SatchelError, match="checksum"):
        selfupdate.install(release, exe)
    assert exe.read_bytes() == b"MZ old"
    assert not (tmp_path / "Satchel.exe.new").exists()


def test_running_from_source_has_no_exe():
    assert selfupdate.running_exe() is None
    selfupdate.clean_up()  # nothing to do, and no error
