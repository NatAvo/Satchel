import json
from types import SimpleNamespace

from satchel import gui, wow
from satchel.download import Release
from satchel.store import SatchelError


def release(repo, tag, source="github"):
    return Release(source, repo, tag, "a.zip", "https://x")


def entry(id, source="github", repo="a/b", version="v1", uninstalled=False):
    return SimpleNamespace(id=id, name=id, source=source, repo=repo, version=version, uninstalled=uninstalled)


class FakeStore:
    def __init__(self, entries=(), unmarked=()):
        self._entries = {item.id: item for item in entries}
        self._unmarked = set(unmarked)

    def entries(self):
        return self._entries

    def rows(self):
        return [SimpleNamespace(id=id, status=wow.STATUS_UNKNOWN) for id in self._unmarked]


class FakeClient:
    def __init__(self, latest):
        self._latest = latest

    def latest(self, repo):
        value = self._latest[repo]
        if isinstance(value, Exception):
            raise value
        return value


def test_config_keeps_other_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "CONFIG_PATH", tmp_path / "Satchel" / "config.json")
    assert gui.load_config() == {}
    gui.save_path(tmp_path / "_forever_")
    gui.save_config(auto_update=True)
    assert gui.load_saved_path() == str(tmp_path / "_forever_")
    assert gui.load_config()["auto_update"] is True
    # Choosing a folder later must not switch auto-update off.
    gui.save_path(tmp_path / "other")
    assert gui.load_config() == {"forever_dir": str(tmp_path / "other"), "auto_update": True}


def test_unreadable_config_counts_as_empty(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(gui, "CONFIG_PATH", path)
    path.write_text("not json", "utf-8")
    assert gui.load_config() == {}
    path.write_text(json.dumps(["a list"]), "utf-8")
    assert gui.load_config() == {}


def test_find_updates_skips_what_cannot_be_updated():
    store = FakeStore([
        entry("github:a/current", repo="a/current", version="v1"),
        entry("github:a/stale", repo="a/stale", version="v1"),
        entry("github:a/broken", repo="a/broken"),
        entry("github:a/gone", repo="a/gone", uninstalled=True),
        entry("local:zip", source="local"),
        entry("wowinterface:7", source="wowinterface", repo="7", version="1.0"),
    ])
    clients = {
        "github": FakeClient({
            "a/current": release("a/current", "v1"),
            "a/stale": release("a/stale", "v2"),
            "a/broken": SatchelError("offline"),
        }),
        "wowinterface": FakeClient({"7": release("7", "1.1", source="wowinterface")}),
    }
    updates, errors = gui.find_updates(store, clients)
    assert set(updates) == {"github:a/stale", "wowinterface:7"}
    assert errors == {"github:a/broken": "github:a/broken: offline"}


def test_apply_updates_only_allows_already_accepted_unmarked_addons(monkeypatch):
    calls = []

    def fake_install(store, rel, allow_unverified=False):
        calls.append((rel.repo, allow_unverified))
        if rel.repo == "a/fails":
            raise SatchelError("bad zip")

    monkeypatch.setattr(gui, "install_release", fake_install)
    pending = {
        "github:a/ok": release("a/ok", "v2"),
        "github:a/unmarked": release("a/unmarked", "v2"),
        "github:a/fails": release("a/fails", "v2"),
    }
    finished, failures = gui.apply_updates(FakeStore(unmarked={"github:a/unmarked"}), pending)
    assert finished == ["github:a/ok", "github:a/unmarked"]
    assert failures == {"github:a/fails": "bad zip"}
    assert ("a/ok", False) in calls and ("a/unmarked", True) in calls


def test_still_pending_drops_updates_that_no_longer_apply():
    store = FakeStore([
        entry("github:a/waiting", repo="a/waiting", version="v1"),
        entry("github:a/done", repo="a/done", version="v2"),  # Update all already installed it
        entry("github:a/gone", repo="a/gone", uninstalled=True),
    ])
    found = {
        "github:a/waiting": release("a/waiting", "v2"),
        "github:a/done": release("a/done", "v2"),
        "github:a/gone": release("a/gone", "v2"),
        "github:a/removed": release("a/removed", "v2"),  # no longer managed
    }
    assert set(gui.still_pending(store, found)) == {"github:a/waiting"}


class FakeRoot:
    """Runs `after` callbacks only when the test says so, recording each delay."""

    def __init__(self):
        self.jobs = []

    def after(self, delay, callback):
        self.jobs.append((delay, callback))
        return str(len(self.jobs))

    def after_cancel(self, job):
        pass

    def run_next(self):
        delay, callback = self.jobs.pop(0)
        callback()
        return delay


def test_waiting_updates_install_soon_after_the_game_closes(monkeypatch):
    running = [True]
    monkeypatch.setattr(wow, "is_game_running", lambda: running[0])
    app = SimpleNamespace(root=FakeRoot(), _auto_job=None, auto_update=SimpleNamespace(get=lambda: True))
    installs = []
    app._cancel_auto_update = lambda: None
    app._schedule_auto_update = lambda delay, waiting=False: gui.App._schedule_auto_update(app, delay, waiting)
    app._wait_for_game = lambda: gui.App._wait_for_game(app)
    app._auto_update = lambda waiting=False: installs.append(waiting)

    app._schedule_auto_update(gui.AUTO_UPDATE_GAME_POLL_MS, waiting=True)
    for _ in range(3):  # game still running: look again in 30 seconds
        assert app.root.run_next() == gui.AUTO_UPDATE_GAME_POLL_MS
        while app.root.jobs and app.root.jobs[0][0] == 100:  # waiting on the background look
            app.root.run_next()
    assert installs == []
    running[0] = False
    assert app.root.run_next() == gui.AUTO_UPDATE_GAME_POLL_MS
    while not installs:
        app.root.run_next()
    assert installs == [True]
    assert gui.AUTO_UPDATE_GAME_POLL_MS == 30 * 1000
