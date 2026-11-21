import textwrap
import time

import pytest

from conftest import wait_for
from pixoohub.host import PluginProcess


class RecordingDisplay:
    def __init__(self):
        self.claims = {}
        self.released = []

    def claim(self, owner, frames, speed_ms=100):
        self.claims[owner] = (frames, speed_ms)

    def release(self, owner):
        self.claims.pop(owner, None)
        self.released.append(owner)


def make_plugin(tmp_path, body: str) -> PluginProcess:
    d = tmp_path / "plug"
    d.mkdir()
    (d / "plugin.py").write_text(textwrap.dedent(body))
    return PluginProcess("demo", d, "plugin.py", tmp_path / "data", tmp_path / "deps", RecordingDisplay())


BASIC = """
    from pixoohub.sdk import Plugin, ActionResult

    class Demo(Plugin):
        interval = 0.1

        def setup(self):
            self.n = 0
            print("stdout must not break the protocol")

        def tick(self):
            self.n += 1
            img = self.canvas((255, 0, 0))
            self.show(img)
            self.status(f"tick {self.n}")

        def on_action(self, name, values):
            self.storage.set("last", values.get("x"))
            return ActionResult(f"got {values.get('x')}", open_url="https://example.com")
"""


def test_frames_status_and_actions(tmp_path):
    p = make_plugin(tmp_path, BASIC)
    p.start({"a": 1})
    try:
        assert wait_for(lambda: p.state == "running")
        assert wait_for(lambda: "demo" in p.display.claims)
        frames, _ = p.display.claims["demo"]
        assert frames[0] == bytes([255, 0, 0]) * 4096
        assert wait_for(lambda: p.status_text.startswith("tick"))
        res = p.action("go", {"x": 5})
        assert res == {"message": "got 5", "ok": True, "open_url": "https://example.com", "values": {}}
        assert (tmp_path / "data" / "storage.json").exists()
        assert any("stdout must not" in l["line"] for l in p.logs)
    finally:
        p.stop()
    assert p.state == "stopped"
    assert "demo" not in p.display.claims


def test_crash_restarts(tmp_path, monkeypatch):
    p = make_plugin(tmp_path, """
        import os
        from pixoohub.sdk import Plugin

        class Crash(Plugin):
            def tick(self):
                os._exit(3)
    """)
    p.start({})
    try:
        assert wait_for(lambda: p.state == "crashed")
        assert "code 3" in p.error
    finally:
        p.stop()


def test_setup_error_is_reported_and_not_restarted(tmp_path):
    p = make_plugin(tmp_path, """
        from pixoohub.sdk import Plugin

        class Bad(Plugin):
            def setup(self):
                raise ValueError("API key missing")
    """)
    p.start({})
    try:
        assert wait_for(lambda: p.state == "failed")
        assert "API key missing" in p.error
        time.sleep(1)
        assert p.state == "failed"
    finally:
        p.stop()


def test_tick_exception_does_not_kill_plugin(tmp_path):
    p = make_plugin(tmp_path, """
        from pixoohub.sdk import Plugin

        class Flaky(Plugin):
            def tick(self):
                raise RuntimeError("network down")
    """)
    p.start({})
    try:
        assert wait_for(lambda: p.status_level == "error")
        assert "network down" in p.status_text
        assert p.state == "running"
    finally:
        p.stop()


def test_hung_plugin_is_killed(tmp_path, monkeypatch):
    import pixoohub.host as host
    monkeypatch.setattr(host, "STOP_GRACE", 0.5)
    p = make_plugin(tmp_path, """
        import time
        from pixoohub.sdk import Plugin

        class Hang(Plugin):
            def tick(self):
                while True:
                    time.sleep(1)
    """)
    p.start({})
    assert wait_for(lambda: p.state == "running")
    t = time.monotonic()
    p.stop()
    assert time.monotonic() - t < 6
    assert p._proc is None
