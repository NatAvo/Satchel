import pytest

from satchel import discover, wow
from satchel.download import Release
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


def folder(store: Store, name: str, **headers) -> None:
    path = store.addons_dir / name
    path.mkdir()
    lines = [f"## Interface: 16001", f"## Title: {name}"] + [f"## {k.replace('_', '-')}: {v}" for k, v in headers.items()]
    (path / f"{name}.toc").write_text("\n".join(lines) + "\n")


class FakeClient:
    def __init__(self, source, releases):
        self.source, self.releases, self.calls = source, releases, []

    def latest(self, repo):
        self.calls.append(repo)
        if repo.lower() not in self.releases:
            raise SatchelError("no release")
        tag, title = self.releases[repo.lower()]
        return Release(self.source, repo, tag, f"{repo}.zip", "https://example.invalid", title=title)


def test_toc_links_are_read_github_first():
    info = wow.classify_folder("A", {"A.toc": (
        b"## Title: A\n## X-WoWI-ID: 17996\n## X-Website: https://github.com/Owner/Repo.git\n"
        b"## X-Curse-Project-ID: 123\n## X-Other: https://www.wowinterface.com/downloads/info8092-Titan.html\n"
    )}, 16001)
    assert info.links == (("github", "Owner/Repo"), ("wowinterface", "17996"), ("wowinterface", "8092"))


def test_scan_groups_folders_by_source_and_skips_unusable_links(store):
    folder(store, "Titan", Version="9.3.5", X_WoWI_ID="8092")
    folder(store, "TitanBag", X_WoWI_ID="8092")
    # Its GitHub repo has no release, so the WoWInterface page is used instead.
    folder(store, "GTFO", Version="6.12.3", X_Website="https://github.com/someone/gtfo", X_WoWI_ID="17996")
    folder(store, "!Probe")
    folder(store, "OnlyCurse", X_Website="https://www.curseforge.com/wow/addons/x")
    clients = {
        "github": FakeClient("github", {}),
        "wowinterface": FakeClient("wowinterface", {"8092": ("9.3.5", "Titan Panel"), "17996": ("6.13.0", "GTFO")}),
    }
    result = discover.scan(store, clients)
    found = {d.name: d for d in result.found}
    assert found["Titan Panel"].folders == ["Titan", "TitanBag"] and found["Titan Panel"].up_to_date
    assert not found["GTFO"].up_to_date
    assert sorted(result.unmatched) == ["!Probe", "OnlyCurse"]
    assert clients["wowinterface"].calls.count("8092") == 1  # looked up once for both folders


def test_adopt_tracks_without_touching_files(store):
    folder(store, "GTFO", Version="v6.12.3", X_WoWI_ID="17996")
    clients = {"wowinterface": FakeClient("wowinterface", {"17996": ("6.12.3", "GTFO")})}
    [found] = discover.scan(store, clients).found
    entry = discover.adopt(store, found)
    assert entry.id == "wowinterface:17996" and entry.version == "6.12.3" and entry.folders == ["GTFO"]
    row = next(r for r in store.rows() if r.name == "GTFO")
    assert row.managed and (store.addons_dir / "GTFO" / "GTFO.toc").exists()
    # Already managed: a second scan finds nothing, and adopting again is refused.
    assert discover.scan(store, clients).found == []
    with pytest.raises(SatchelError):
        discover.adopt(store, found)


def test_outdated_files_keep_their_own_version_so_an_update_is_offered(store):
    folder(store, "Old", Version="1.0", X_Website="https://github.com/me/old")
    clients = {"github": FakeClient("github", {"me/old": ("v2.0", "")})}
    [found] = discover.scan(store, clients).found
    assert found.name == "Old"
    assert discover.adopt(store, found).version == "1.0"
