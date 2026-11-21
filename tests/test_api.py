import json

import pytest
from fastapi.testclient import TestClient

from conftest import wait_for


@pytest.fixture
def client(tmp_path, monkeypatch, fake_device):
    import pixoohub.manager as mgr
    import pixoohub.web as web
    for name in ("PLUGINS_DIR", "DEPS_DIR", "PLUGIN_DATA_DIR"):
        d = tmp_path / name.lower()
        d.mkdir()
        monkeypatch.setattr(mgr, name, d)
    monkeypatch.setattr(web, "PLUGINS_DIR", mgr.PLUGINS_DIR)
    monkeypatch.setattr(web, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(web, "DATA_DIR", tmp_path)
    monkeypatch.setattr(web, "ensure_dirs", lambda: None)
    (tmp_path / "state.json").write_text(json.dumps({"device": {"ip": fake_device.address}, "repositories": []}))

    d = mgr.PLUGINS_DIR / "demo"
    d.mkdir()
    (d / "plugin.json").write_text(json.dumps({
        "id": "demo", "name": "Demo", "hub_api": 2,
        "settings": [{"name": "token", "type": "password"}, {"name": "go", "type": "action"}],
    }))
    (d / "plugin.py").write_text(
        "from pixoohub.sdk import Plugin\n"
        "class D(Plugin):\n"
        "    interval = 0.2\n"
        "    def tick(self):\n"
        "        self.show(self.canvas((0, 0, 255)))\n"
        "    def on_action(self, name, values):\n"
        "        return 'token=' + values.get('token', '')\n")
    app = web.create_app()
    with TestClient(app) as c:
        yield c, fake_device


def test_end_to_end(client):
    c, device = client
    assert wait_for(lambda: device.frames and device.frames[0][:3] == bytes([0, 0, 255]), 15)
    st = c.get("/api/state").json()
    assert st["showing"]["owner"] == "demo" and st["connection"]["connection"] == "online"
    prev = c.get("/api/preview").json()
    assert len(prev["frames"]) == 1
    assert c.get("/api/preview", params={"digest": prev["digest"]}).json()["unchanged"]

    # secrets are masked on the way out and kept when the mask comes back
    assert c.put("/api/plugins/demo/config", json={"token": "s3cret"}).status_code == 200
    view = c.get("/api/plugins").json()["plugins"][0]
    assert view["config"]["token"] != "s3cret"
    assert c.put("/api/plugins/demo/config", json={"token": view["config"]["token"]}).status_code == 200
    def settled():
        v = c.get("/api/plugins").json()["plugins"][0]
        return v["state"] == "running" and v["job"]["state"] == "done"
    assert wait_for(settled, 15)
    r = c.post("/api/plugins/demo/action", json={"name": "go", "values": {"token": view["config"]["token"]}}).json()
    assert r["message"] == "token=s3cret"

    # disabling releases the screen and turns it off
    c.post("/api/plugins/demo/enabled", json={"enabled": False})
    assert wait_for(lambda: not device.screen_on, 10)


def test_validation(client):
    c, _ = client
    assert c.put("/api/settings", json={"device": {"ip": "1.2.3.4; rm -rf", "brightness": 50}}).status_code == 422
    assert c.put("/api/settings", json={"display": {"mode": "chaos"}}).status_code == 422
    assert c.post("/api/plugins/demo/action", json={"name": "nope"}).status_code == 400
    assert c.get("/api/plugins/../etc/icon").status_code == 404
    assert c.post("/api/install", json={"url": "file:///etc"}).status_code == 400
