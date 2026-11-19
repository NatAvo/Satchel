import hashlib
import json
import stat
import struct
import zipfile
from pathlib import Path

import pytest

from satchel import catalog, download, github, store as store_module, wow, wowinterface
from satchel.store import NotMarkedForForever, SatchelError, Store, import_local_zip, inspect_zip


@pytest.fixture
def forever(tmp_path):
    root = tmp_path / "World of Warcraft"
    path = root / "_forever_"
    path.mkdir(parents=True)
    (path / ".flavor.info").write_text("Product Flavor!STRING:0\nwowf\n")
    (path / "Wow.exe").write_bytes(b"")
    (root / ".build.info").write_text(
        "Branch!STRING:0|Version!STRING:0|Product!STRING:0\nus|1.60.1.70790|wowf\n"
    )
    return path


@pytest.fixture
def store(forever):
    return Store(wow.read_install(forever))


def toc(interface="16001", title="Sample", version="1.0"):
    return f"## Interface: {interface}\n## Title: {title}\n## Version: {version}\ncode.lua\n"


def make_zip(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def forever_zip(tmp_path, name="Sample", version="1.0", extra=None):
    files = {f"{name}/{name}_Forever.toc": toc(version=version), f"{name}/code.lua": f"-- {version}"}
    files.update(extra or {})
    return make_zip(tmp_path / f"{name}-{version}.zip", files)


# Client and TOC metadata ------------------------------------------------------


def test_reads_version_and_interface(forever):
    install = wow.read_install(forever)
    assert install.version == "1.60.1.70790"
    assert install.interface == 16001


def test_rejects_other_flavors(forever):
    (forever / ".flavor.info").write_text("Product Flavor!STRING:0\nwow\n")
    with pytest.raises(ValueError):
        wow.read_install(forever)


@pytest.mark.parametrize(
    "tocs, expected",
    [
        ({"A_Forever.toc": toc()}, wow.STATUS_OK),
        ({"A-Camelot.toc": toc()}, wow.STATUS_OK),
        ({"A.toc": toc("120105, 16001, 11509")}, wow.STATUS_OK),
        ({"A.toc": toc("16100")}, wow.STATUS_OK),
        ({"A_Forever.toc": toc("16000")}, wow.STATUS_OUTDATED),
        ({"A_Forever.toc": "## Title: A\n"}, wow.STATUS_OK),
        ({"A.toc": toc("11509")}, wow.STATUS_UNKNOWN),
        ({"A.toc": toc("00000"), "A_Mists.toc": toc("50504")}, wow.STATUS_UNKNOWN),
        ({"Other_Forever.toc": toc()}, wow.STATUS_UNKNOWN),
    ],
)
def test_classifies_forever_support(tocs, expected):
    encoded = {name: text.encode() for name, text in tocs.items()}
    assert wow.classify_folder("A", encoded, 16001).status == expected


def test_title_color_codes_are_removed():
    data = {"A.toc": b"## Title: |cff00ff00Green|r Addon\n## Interface: 16001\n"}
    assert wow.classify_folder("A", data, 16001).title == "Green Addon"


# ZIP validation ---------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["../evil.lua", "A/../../evil.lua", "C:/evil.lua", "/evil.lua", "A\\..\\..\\evil.lua", "A/CON.lua", "A/file.lua:ads"],
)
def test_rejects_unsafe_paths(tmp_path, bad):
    path = make_zip(tmp_path / "bad.zip", {"A/A_Forever.toc": toc(), bad: "x"})
    with pytest.raises(SatchelError, match="unsafe"):
        inspect_zip(path, 16001)


def test_rejects_symlinks(tmp_path):
    path = tmp_path / "link.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("A/A_Forever.toc", toc())
        link = zipfile.ZipInfo("A/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../outside")
    with pytest.raises(SatchelError, match="symbolic"):
        inspect_zip(path, 16001)


def test_rejects_source_code_zip(tmp_path):
    path = make_zip(tmp_path / "src.zip", {"Sample-main/Sample.toc": toc()})
    with pytest.raises(SatchelError, match="no matching TOC"):
        inspect_zip(path, 16001)


def test_rejects_blizzard_folders(tmp_path):
    path = make_zip(tmp_path / "b.zip", {"Blizzard_UI/Blizzard_UI.toc": toc()})
    with pytest.raises(SatchelError, match="Blizzard"):
        inspect_zip(path, 16001)


def test_rejects_corrupt_file(tmp_path):
    path = tmp_path / "bad.zip"
    path.write_bytes(b"not a zip")
    with pytest.raises(SatchelError, match="not a valid"):
        inspect_zip(path, 16001)


def test_size_limit_uses_real_output(tmp_path, store, monkeypatch):
    path = forever_zip(tmp_path, extra={"Sample/big.lua": "x" * 5000})
    infos = inspect_zip(path, 16001)
    monkeypatch.setattr(store_module, "MAX_UNPACKED_BYTES", 100)
    with pytest.raises(SatchelError, match="too large"):
        store_module._extract(path, tmp_path / "out", set(infos))


# Install, update, remove ------------------------------------------------------


def test_install_and_update_replace_old_files(tmp_path, store):
    import_local_zip(store, forever_zip(tmp_path, extra={"Sample/old.lua": "old"}))
    assert (store.addons_dir / "Sample" / "old.lua").exists()

    import_local_zip(store, forever_zip(tmp_path, version="2.0"))
    assert not (store.addons_dir / "Sample" / "old.lua").exists()
    assert (store.addons_dir / "Sample" / "code.lua").read_text() == "-- 2.0"
    rows = store.rows()
    assert [(row.version, row.status, row.managed) for row in rows] == [("2.0", wow.STATUS_OK, True)]


def test_unmarked_addon_needs_approval(tmp_path, store):
    path = make_zip(tmp_path / "old.zip", {"Old/Old.toc": toc("11509")})
    with pytest.raises(NotMarkedForForever):
        import_local_zip(store, path)
    assert store.rows() == []
    import_local_zip(store, path, allow_unverified=True)
    assert store.rows()[0].status == wow.STATUS_UNKNOWN


def test_remove_keeps_settings_and_a_backup(tmp_path, store, forever):
    settings = forever / "WTF" / "Account" / "A" / "SavedVariables" / "Sample.lua"
    settings.parent.mkdir(parents=True)
    settings.write_text("saved")
    import_local_zip(store, forever_zip(tmp_path))

    store.remove(store.rows()[0])

    assert not (store.addons_dir / "Sample").exists()
    assert store.entries() == {}
    assert settings.read_text() == "saved"
    assert list(store.backups_dir.glob("*/Sample/code.lua"))


def test_remove_from_list_keeps_the_files(tmp_path, store):
    import_local_zip(store, forever_zip(tmp_path))
    row = store.rows()[0]
    assert row.managed

    store.forget(row)

    assert store.entries() == {}
    assert (store.addons_dir / "Sample" / "code.lua").exists()
    unmanaged = store.rows()[0]
    assert not unmanaged.managed and unmanaged.source == "manual"
    with pytest.raises(SatchelError):
        store.forget(unmanaged)
    assert not store.backups_dir.exists() or not any(store.backups_dir.iterdir())


def test_uninstall_keeps_the_files_and_reinstall_puts_them_back(tmp_path, store):
    import_local_zip(store, forever_zip(tmp_path))
    store.uninstall(store.rows()[0])

    assert not (store.addons_dir / "Sample").exists()
    assert (store.shelf(store.rows()[0].id) / "Sample" / "code.lua").exists()
    [row] = store.rows()
    assert row.status == "uninstalled" and row.managed
    with pytest.raises(SatchelError):
        store.forget(row)       # its files would be stranded outside AddOns
    with pytest.raises(SatchelError):
        store.uninstall(row)

    store.reinstall(row)
    assert (store.addons_dir / "Sample" / "code.lua").exists()
    [row] = store.rows()
    assert row.status == wow.STATUS_OK and row.managed
    assert not any(store.uninstalled_dir.iterdir())
    assert not store.backups_dir.exists() or not any(store.backups_dir.iterdir())


def test_uninstalling_an_unmanaged_folder_returns_it_unmanaged(store):
    (store.addons_dir / "Loose").mkdir()
    (store.addons_dir / "Loose" / "Loose.toc").write_text(toc(title="Loose"))
    store.uninstall(store.rows()[0])
    assert store.rows()[0].status == "uninstalled"
    store.reinstall(store.rows()[0])
    [row] = store.rows()
    assert not row.managed and store.entries() == {}


def test_reinstall_refuses_to_overwrite_a_same_named_folder(tmp_path, store):
    import_local_zip(store, forever_zip(tmp_path))
    store.uninstall(store.rows()[0])
    (store.addons_dir / "Sample").mkdir()   # another copy appeared meanwhile
    uninstalled = next(r for r in store.rows() if r.status == "uninstalled")
    with pytest.raises(SatchelError, match="already has Sample"):
        store.reinstall(uninstalled)
    assert any(store.uninstalled_dir.glob("*/Sample/code.lua"))


def test_removing_or_reinstalling_fresh_sends_the_kept_copy_to_backups(tmp_path, store):
    import_local_zip(store, forever_zip(tmp_path))
    store.uninstall(store.rows()[0])
    import_local_zip(store, forever_zip(tmp_path, version="2.0"))  # installed again from scratch
    [row] = store.rows()
    assert row.status == wow.STATUS_OK and row.version == "2.0"
    assert not any(store.uninstalled_dir.iterdir()) and list(store.backups_dir.glob("*/Sample/code.lua"))

    store.uninstall(row)
    store.remove(store.rows()[0])
    assert store.entries() == {} and not any(store.uninstalled_dir.iterdir())


def test_folder_owned_by_another_source_is_protected(tmp_path, store):
    path = forever_zip(tmp_path)
    store.install_zip(path, id="github:a/sample", name="Sample", source="github", version="v1", repo="a/sample")
    with pytest.raises(SatchelError, match="owned by"):
        import_local_zip(store, path)
    assert store.entries()["github:a/sample"].version == "v1"


def test_update_removes_folders_dropped_by_package(tmp_path, store):
    both = make_zip(
        tmp_path / "both.zip",
        {"Pack/Pack_Forever.toc": toc(), "Pack_Extra/Pack_Extra_Forever.toc": toc()},
    )
    one = make_zip(tmp_path / "one.zip", {"Pack/Pack_Forever.toc": toc()})
    common = dict(id="github:a/pack", name="Pack", source="github", version="v1", repo="a/pack")
    store.install_zip(both, **common)
    store.install_zip(one, **common)
    assert not (store.addons_dir / "Pack_Extra").exists()
    assert (store.addons_dir / "Pack").exists()


def test_failure_restores_previous_version(tmp_path, store, monkeypatch):
    import_local_zip(store, forever_zip(tmp_path))

    def fail(_entries):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_save", fail)
    with pytest.raises(SatchelError, match="nothing was changed"):
        import_local_zip(store, forever_zip(tmp_path, version="2.0"))

    assert (store.addons_dir / "Sample" / "code.lua").read_text() == "-- 1.0"
    assert store.entries()["local:sample"].version == "1.0"
    assert not list(store.work_dir.glob("staging-*"))


def test_unpack_failure_keeps_previous_version(tmp_path, store, monkeypatch):
    import_local_zip(store, forever_zip(tmp_path))

    def fail(*_args):
        raise FileNotFoundError("path too long")

    monkeypatch.setattr(store_module, "_extract", fail)
    with pytest.raises(SatchelError, match="nothing was changed"):
        import_local_zip(store, forever_zip(tmp_path, version="2.0"))

    assert (store.addons_dir / "Sample" / "code.lua").read_text() == "-- 1.0"
    assert not list(store.work_dir.glob("staging-*"))


def test_unmanaged_folders_are_listed_but_blizzard_is_hidden(store):
    manual = store.addons_dir / "Manual"
    manual.mkdir()
    (manual / "Manual.toc").write_text(toc(title="Manual"))
    (store.addons_dir / "Blizzard_Thing").mkdir()
    rows = store.rows()
    assert [(row.name, row.source, row.managed) for row in rows] == [("Manual", "manual", False)]


def test_backups_are_pruned(tmp_path, store, monkeypatch):
    monkeypatch.setattr(store_module, "BACKUPS_TO_KEEP", 2)
    for version in ("1", "2", "3", "4", "5"):
        import_local_zip(store, forever_zip(tmp_path, version=version))
    assert len(list(store.backups_dir.iterdir())) == 2


# GitHub -----------------------------------------------------------------------


def assets(*names):
    return [{"name": name, "browser_download_url": f"https://x/{name}"} for name in names]


def test_selects_forever_from_release_json():
    data = {
        "releases": [
            {"filename": "A-classic.zip", "nolib": False, "metadata": [{"flavor": "classic", "interface": 11509}]},
            {"filename": "A-forever.zip", "nolib": True, "metadata": [{"flavor": "forever", "interface": 16001}]},
            {"filename": "A-all.zip", "nolib": False, "metadata": [{"flavor": "forever", "interface": 16001}]},
        ]
    }
    picked = github.select_asset(assets("A-classic.zip", "A-forever.zip", "A-all.zip", "release.json"), data)
    assert picked["name"] == "A-all.zip"


def test_selects_forever_by_file_name():
    picked = github.select_asset(assets("A-mists.zip", "A-Forever.zip"), None)
    assert picked["name"] == "A-Forever.zip"


def test_lone_plain_zip_is_a_candidate():
    assert github.select_asset(assets("A-1.0.zip"), None)["name"] == "A-1.0.zip"


def test_release_json_without_forever_is_respected():
    data = {"releases": [{"filename": "A.zip", "metadata": [{"flavor": "mainline", "interface": 120105}]}]}
    assert github.select_asset(assets("A.zip"), data) is None


def test_other_flavors_are_not_selected():
    assert github.select_asset(assets("A-classic.zip", "A-cata.zip"), None) is None


@pytest.mark.parametrize(
    "text",
    ["Questie/Questie", "https://github.com/Questie/Questie", "github.com/Questie/Questie/releases", "https://github.com/Questie/Questie.git"],
)
def test_parse_repo(text):
    assert github.parse_repo(text) == "Questie/Questie"


@pytest.mark.parametrize("text", ["nope", "https://evil.example/Questie/Questie", "a/b/c", "a/b;rm"])
def test_parse_repo_rejects(text):
    with pytest.raises(SatchelError):
        github.parse_repo(text)


def test_checksum_mismatch_blocks_install(tmp_path, store, monkeypatch):
    path = forever_zip(tmp_path)

    class FakeResponse:
        def __init__(self):
            self.handle = open(path, "rb")

        def read(self, size):
            return self.handle.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.handle.close()

    monkeypatch.setattr(download, "open_url", lambda _url: FakeResponse())
    for bad_digest in ("sha256:" + "0" * 64, "md5:" + "0" * 32):
        release = download.Release("github", "a/sample", "v1", "s.zip", "https://x/s.zip", bad_digest)
        with pytest.raises(SatchelError, match="checksum"):
            download.install_release(store, release)
    assert store.rows() == []
    assert not list(store.work_dir.glob("download-*"))

    good = hashlib.md5(path.read_bytes()).hexdigest()
    release = download.Release("wowinterface", "8092", "9.3.5", "s.zip", "https://x/s.zip", f"md5:{good}", "Titan Panel")
    entry = download.install_release(store, release)
    assert (entry.id, entry.name, entry.source, entry.repo) == ("wowinterface:8092", "Titan Panel", "wowinterface", "8092")


@pytest.fixture(autouse=True)
def no_real_github_login(monkeypatch):
    """Tests never pick up the machine's real GitHub credentials."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(github, "gh_token", lambda: None)


def test_token_lookup_prefers_env_then_gh_login_and_waits_until_needed(monkeypatch):
    calls = []
    monkeypatch.setattr(github, "gh_token", lambda: calls.append(1) or "from-gh")
    client = github.GitHub()
    assert calls == []
    assert client.token == "from-gh" and client.token == "from-gh"
    assert calls == [1]
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    assert github.GitHub().token == "from-env"
    assert github.GitHub(token="explicit").token == "explicit"


def fake_github(monkeypatch, client, private):
    asset = {"name": "Eloquence-Private-v1-forever.zip", "digest": "",
             "url": "https://api.github.com/repos/o/p/releases/assets/7",
             "browser_download_url": "https://github.com/o/p/releases/download/v1/Eloquence-Private-v1-forever.zip"}

    def api(path):
        if path == "/repos/o/p":
            return {"private": private}
        return {"tag_name": "v1", "assets": [asset]}

    monkeypatch.setattr(client, "_api", api)


def test_private_repo_release_downloads_through_the_api_with_the_token(monkeypatch):
    client = github.GitHub(token="secret")
    fake_github(monkeypatch, client, private=True)
    release = client.latest("o/p")
    assert release.url == "https://api.github.com/repos/o/p/releases/assets/7"
    assert release.token == "secret"
    assert "secret" not in repr(release)


def test_public_repo_release_stays_credential_free(monkeypatch):
    client = github.GitHub(token="secret")
    fake_github(monkeypatch, client, private=False)
    release = client.latest("o/p")
    assert release.url.startswith("https://github.com/") and release.token == ""


def test_token_download_goes_only_to_the_api_and_is_not_redirected(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request)
        raise OSError("stop")

    monkeypatch.setattr(download.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SatchelError, match="api.github.com"):
        download.open_url("https://evil.example/x.zip", "secret")
    with pytest.raises(OSError):
        download.open_url("https://api.github.com/repos/o/p/releases/assets/7", "secret")
    request = seen[0]
    assert request.unredirected_hdrs["Authorization"] == "Bearer secret"
    assert "Authorization" not in request.headers  # so a redirect can't carry it
    assert request.get_header("Accept") == "application/octet-stream"


def test_missing_release_without_a_token_mentions_logging_in(monkeypatch):
    import urllib.error

    def not_found(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(github.urllib.request, "urlopen", not_found)
    with pytest.raises(SatchelError, match="gh auth login"):
        github.GitHub().latest("o/p")


def test_token_is_not_sent_for_downloads(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request.headers)
        raise OSError("stop")

    monkeypatch.setattr(github.urllib.request, "urlopen", fake_urlopen)
    client = github.GitHub(token="secret")
    with pytest.raises(OSError):
        download.open_url("https://x/s.zip")
    with pytest.raises(OSError):
        client._open("https://api.github.com/x")
    assert "Authorization" not in seen[0]
    assert seen[1]["Authorization"] == "Bearer secret"


# WoWInterface -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "8092",
        " 8092 ",
        "https://www.wowinterface.com/downloads/info8092-TitanPanel.html",
        "wowinterface.com/downloads/info8092",
        "https://www.wowinterface.com/downloads/fileinfo.php?id=8092",
    ],
)
def test_parse_wowinterface_id(text):
    assert wowinterface.parse_id(text) == "8092"


@pytest.mark.parametrize(
    "text", ["", "abc", "https://evil.example/downloads/info8092", "https://wowinterface.com.evil.example/info8092"]
)
def test_parse_wowinterface_id_rejects(text):
    with pytest.raises(SatchelError):
        wowinterface.parse_id(text)


def wowi_response(monkeypatch, payload):
    class Response:
        def read(self, _size):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(wowinterface, "open_url", lambda _url: Response())


def test_wowinterface_release(monkeypatch):
    wowi_response(
        monkeypatch,
        [
            {
                "version": "9.3.5",
                "checksum": "45b589886044413b19f5c34b82735616",
                "fileName": "TitanPanel-9.3.5.zip",
                "downloadUri": "https://cdn.wowinterface.com/downloads/getfile.php?id=8092",
                "title": "Titan &amp; Friends",
            }
        ],
    )
    release = wowinterface.WoWInterface().latest("8092")
    assert release.source == "wowinterface"
    assert release.tag == "9.3.5"
    assert release.digest == "md5:45b589886044413b19f5c34b82735616"
    assert release.title == "Titan & Friends"


@pytest.mark.parametrize(
    "uri",
    ["http://cdn.wowinterface.com/a.zip", "https://evil.example/a.zip", "https://cdn.wowinterface.com.evil.example/a.zip"],
)
def test_wowinterface_rejects_other_download_hosts(monkeypatch, uri):
    wowi_response(monkeypatch, [{"version": "1", "downloadUri": uri}])
    with pytest.raises(SatchelError, match="unexpected"):
        wowinterface.WoWInterface().latest("8092")


def test_wowinterface_empty_response(monkeypatch):
    wowi_response(monkeypatch, [])
    with pytest.raises(SatchelError, match="no download"):
        wowinterface.WoWInterface().latest("8092")


# App icon ---------------------------------------------------------------------


def test_app_icon_is_a_multi_size_windows_icon():
    data = (Path(store_module.__file__).parent / "assets" / "satchel.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    widths = {data[6 + 16 * index] or 256 for index in range(count)}
    assert {16, 32, 48, 256} <= widths


# Addon pages ------------------------------------------------------------------


@pytest.mark.parametrize(
    "source, repo, expected",
    [
        ("github", "Questie/Questie", "https://github.com/Questie/Questie"),
        ("wowinterface", "8092", "https://www.wowinterface.com/downloads/info8092"),
        ("local", "", None),
        ("manual", "", None),
        ("github", "", None),
    ],
)
def test_page_url(source, repo, expected):
    assert download.page_url(source, repo) == expected


def test_rows_carry_the_repo_for_managed_addons_only(tmp_path, store):
    store.install_zip(forever_zip(tmp_path), id="github:a/sample", name="Sample", source="github", version="v1", repo="A/Sample")
    (store.addons_dir / "Manual").mkdir()
    rows = {row.name: row for row in store.rows()}
    assert rows["Sample"].repo == "A/Sample"
    assert rows["Manual"].repo == ""


# Catalog ----------------------------------------------------------------------


def test_catalog_groups_keep_every_item_in_category_order():
    groups = catalog.by_category(include_untested=True)
    assert list(groups) == list(catalog.CATEGORIES)
    grouped = [item for members in groups.values() for item in members]
    assert sorted(grouped, key=catalog.CATALOG.index) == list(catalog.CATALOG)
    assert len(grouped) == len(catalog.CATALOG)
    assert all(item.category == name for name, members in groups.items() for item in members)
    for members in groups.values():
        assert members == sorted(members, key=catalog.CATALOG.index)


def test_only_verified_addons_are_recommended_by_default():
    recommended = [item for members in catalog.by_category().values() for item in members]
    assert recommended and all(item.verified for item in recommended)
    assert {item.name for item in recommended} >= {"Questie", "Titan Panel"}
    untested = [item for item in catalog.CATALOG if not item.verified]
    assert untested and not set(untested) & set(recommended)
    assert all(category in catalog.CATEGORIES for category in catalog.by_category())


def test_catalog_has_no_curseforge_sources():
    assert {item.source for item in catalog.CATALOG} == {"github", "wowinterface"}
    assert not any("curseforge" in item.repo.lower() for item in catalog.CATALOG)


def test_catalog_entries_are_valid():
    ids = [download.entry_id(item.source, item.repo) for item in catalog.CATALOG]
    assert len(ids) == len(set(ids))
    for item in catalog.CATALOG:
        if item.source == "github":
            assert github.parse_repo(item.repo) == item.repo
        else:
            assert item.source == "wowinterface" and wowinterface.parse_id(item.repo) == item.repo
