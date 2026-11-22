import json
import subprocess
import textwrap

import pytest

from conftest import wait_for
from pixoohub import manager as mgr
from pixoohub.manager import (SECRET_MARK, PluginError, coerce_config, mask_secrets, normalize_url,
                              validate_manifest)

FIELDS = [
    {"type": "header", "label": "x"},
    {"name": "key", "type": "password"},
    {"name": "n", "type": "number", "min": 1, "max": 10, "default": 3},
    {"name": "on", "type": "switch", "default": False},
    {"name": "devs", "type": "multiselect", "options": ["a", "b"]},
    {"name": "go", "type": "action", "label": "Go"},
]


def test_normalize_url():
    assert normalize_url(" https://github.com/a/b.git/ ") == "https://github.com/a/b"
    for bad in ("--upload-pack=evil", "file:///etc", "http://x/y", "https://x/y z"):
        with pytest.raises(PluginError):
            normalize_url(bad)


def test_validate_manifest():
    m = validate_manifest({"name": "My Plugin", "settings_schema": FIELDS + [{"type": "bogus"}]})
    assert m["id"] == "my-plugin"
    assert len(m["settings"]) == len(FIELDS)
    assert m["hub_api"] == 1
    with pytest.raises(PluginError):
        validate_manifest({"id": "x", "entry": "../evil.py"})
    with pytest.raises(PluginError):
        validate_manifest({"id": "Bad Id"})


def test_coerce_and_secrets():
    prev = {"key": "s3cret"}
    out = coerce_config(FIELDS, {"key": SECRET_MARK, "n": "42", "on": "true", "devs": ["a"], "go": "x"}, prev)
    assert out == {"key": "s3cret", "n": 10, "on": True, "devs": ["a"]}
    out = coerce_config(FIELDS, {"key": "new", "n": "abc"}, prev)
    assert out["key"] == "new" and out["n"] == 3
    assert mask_secrets(FIELDS, {"key": "s3cret"})["key"] == SECRET_MARK
    assert mask_secrets(FIELDS, {"key": ""})["key"] == ""


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


PLUGIN_V1 = """
from pixoohub.sdk import Plugin

class P(Plugin):
    interval = 0.1
    def tick(self):
        self.status("v1 " + str(self.cfg("n", 0)))
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("PLUGINS_DIR", "DEPS_DIR", "PLUGIN_DATA_DIR"):
        d = tmp_path / name.lower()
        d.mkdir()
        monkeypatch.setattr(mgr, name, d)
    from pixoohub.state import State

    class NullDisplay:
        def claim(self, *a, **k): pass
        def release(self, *a): pass
        def poke(self): pass

    state = State(tmp_path / "state.json")
    state.set("repositories", [])
    m = mgr.PluginManager(state, NullDisplay())
    yield m, tmp_path
    m.shutdown()


def test_install_update_remove(env):
    m, tmp = env
    repo = tmp / "repo"
    repo.mkdir()
    (repo / "plugin.json").write_text(json.dumps({
        "id": "demo", "name": "Demo", "version": "1.0.0", "hub_api": 2,
        "settings": [{"name": "n", "type": "number", "default": 7}],
    }))
    (repo / "plugin.py").write_text(PLUGIN_V1)
    (repo / "requirements.txt").write_text("requests\n")  # already satisfied -> no pip run
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "v1", "--allow-empty", cwd=repo)
    _git("add", ".", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "v1", cwd=repo)

    url = str(repo)
    m._submit(url, "Installing", m._install, url)
    assert wait_for(lambda: m.jobs[url]["state"] != "running", 30), m.jobs
    assert m.jobs[url]["state"] == "done", m.jobs[url]
    proc = m.procs["demo"]
    assert wait_for(lambda: proc.status_text == "v1 7")

    m.save_config("demo", {"n": "9"})
    assert wait_for(lambda: proc.status_text == "v1 9")

    (repo / "plugin.py").write_text(PLUGIN_V1.replace("v1", "v2"))
    data = json.loads((repo / "plugin.json").read_text())
    data["version"] = "2.0.0"
    (repo / "plugin.json").write_text(json.dumps(data))
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "v2", cwd=repo)

    m.update("demo")
    assert wait_for(lambda: m.jobs["demo"]["state"] != "running", 30)
    assert m.jobs["demo"]["state"] == "done", m.jobs["demo"]
    assert m.manifests["demo"]["version"] == "2.0.0"
    assert wait_for(lambda: m.procs["demo"].status_text == "v2 9")

    m.set_enabled("demo", False)
    assert m.procs["demo"].state == "stopped"

    m.remove("demo")
    assert wait_for(lambda: m.jobs["demo"]["state"] == "done")
    assert "demo" not in m.manifests
    assert not (mgr.PLUGINS_DIR / "demo").exists()


def test_outdated_plugin_is_refused(env):
    m, tmp = env
    d = mgr.PLUGINS_DIR / "old"
    d.mkdir()
    (d / "plugin.json").write_text(json.dumps({"id": "old", "name": "Old"}))
    (d / "plugin.py").write_text("")
    m.load_installed()
    assert wait_for(lambda: m.jobs.get("old", {}).get("state") == "error")
    assert "ältere Hub-Version" in m.jobs["old"]["message"]
    assert m.plugin_view("old")["outdated_api"]


def test_import_legacy_settings(tmp_path):
    from pixoohub.state import State, import_legacy
    legacy = tmp_path / "settings.json"
    legacy.write_text(json.dumps({
        "pixoo_ip": "192.168.1.50", "mode": "rotation",
        "plugin_priorities": {"Spotify-Plugin": 10, "Steam-Plugin": 20},
        "plugin_settings": {"Spotify-Plugin": {"client_id": "abc", "refresh_token": "r"},
                            "Steam-Plugin": {"steam_id": "1"}},
    }))
    st = State(tmp_path / "state.json")
    assert st.fresh
    assert import_legacy(st, legacy)
    assert st.get("device")["ip"] == "192.168.1.50"
    assert st.get("display")["mode"] == "rotation"
    assert st.get("order") == ["steam", "spotify"]
    assert st.plugin("spotify")["config"]["refresh_token"] == "r"


def test_queued_jobs_report_done_only_at_the_end(env):
    import threading
    m, _ = env
    gate = threading.Event()
    seen = []
    m._submit("x", "first", lambda: (gate.wait(5), seen.append(1)))
    m._submit("x", "second", lambda: seen.append(2), queue=True)
    assert m.jobs["x"]["state"] == "running"
    gate.set()
    assert wait_for(lambda: seen == [1, 2])
    assert wait_for(lambda: m.jobs["x"]["state"] == "done")
    # while job 2 was pending, the status never flipped to done early
    assert "x" not in m._pending
