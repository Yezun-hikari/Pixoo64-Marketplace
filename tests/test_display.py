import datetime as dt

from conftest import wait_for
from pixoohub.display import Display, in_quiet_hours

RED = bytes([255, 0, 0]) * 4096
BLUE = bytes([0, 0, 255]) * 4096


def make(device, **display):
    settings = {
        "device": {"ip": device.address, "brightness": 40},
        "display": {"mode": "priority", "rotation_seconds": 5, "idle": "off",
                    "quiet_hours": {"enabled": False, "start": "23:00", "end": "07:00"}, **display},
        "order": ["a", "b"],
        "enabled": {"a", "b"},
    }
    d = Display(lambda: settings, lambda ip: None)
    d.start()
    return d, settings


def test_priority_and_release(fake_device):
    d, _ = make(fake_device)
    try:
        assert wait_for(lambda: d.status["connection"] == "online")
        d.claim("b", [BLUE])
        assert wait_for(lambda: fake_device.frames == [BLUE] and fake_device.screen_on)
        d.claim("a", [RED])
        assert wait_for(lambda: fake_device.frames == [RED])
        d.release("a")
        assert wait_for(lambda: fake_device.frames == [BLUE])
        d.release("b")
        assert wait_for(lambda: not fake_device.screen_on)
        assert fake_device.brightness == 40
        assert not fake_device.errors
    finally:
        d.stop()


def test_disabled_plugin_never_shows(fake_device):
    d, settings = make(fake_device)
    settings["enabled"] = {"b"}
    try:
        d.claim("a", [RED])
        assert wait_for(lambda: d.status["connection"] == "online")
        assert wait_for(lambda: d.showing["kind"] == "off")
        assert RED not in fake_device.frames
    finally:
        d.stop()


def test_identical_frames_are_not_resent(fake_device):
    d, _ = make(fake_device)
    try:
        d.claim("a", [RED])
        assert wait_for(lambda: fake_device.pushes == 1)
        for _ in range(5):
            d.claim("a", [RED])
        import time
        time.sleep(1)
        assert fake_device.pushes == 1
    finally:
        d.stop()


def test_animation_and_pic_id_reset(fake_device):
    d, _ = make(fake_device)
    try:
        assert wait_for(lambda: d.status["connection"] == "online")
        for i in range(35):
            frame = bytes([i, 0, 0]) * 4096
            d.claim("a", [frame, BLUE], speed_ms=120)
            assert wait_for(lambda: fake_device.frames and fake_device.frames[0] == frame)
        assert fake_device.speed == 120 and len(fake_device.frames) == 2
        assert fake_device.commands.count("Draw/ResetHttpGifId") >= 2
        assert not fake_device.errors
    finally:
        d.stop()


def test_device_outage_recovers(fake_device, monkeypatch):
    import pixoohub.display as disp
    monkeypatch.setattr(disp, "RETRY_INTERVAL", 0.3)
    d, _ = make(fake_device)
    try:
        assert wait_for(lambda: d.status["connection"] == "online")
        fake_device.fail = True
        d.claim("a", [RED])
        assert wait_for(lambda: d.status["connection"] == "offline")
        fake_device.fail = False
        assert wait_for(lambda: fake_device.frames == [RED])
    finally:
        d.stop()


def test_quiet_hours_window():
    t = dt.time.fromisoformat
    assert in_quiet_hours(t("23:30"), "23:00", "07:00")
    assert in_quiet_hours(t("03:00"), "23:00", "07:00")
    assert not in_quiet_hours(t("12:00"), "23:00", "07:00")
    assert in_quiet_hours(t("13:00"), "12:00", "14:00")
    assert not in_quiet_hours(t("14:00"), "12:00", "14:00")
    assert not in_quiet_hours(t("10:00"), "bad", "07:00")


def test_rotation(fake_device, monkeypatch):
    d, settings = make(fake_device, mode="rotation", rotation_seconds=5)
    try:
        d.claim("a", [RED])
        d.claim("b", [BLUE])
        assert wait_for(lambda: fake_device.frames == [RED])
        assert wait_for(lambda: fake_device.frames == [BLUE], timeout=8)
        assert wait_for(lambda: fake_device.frames == [RED], timeout=8)
    finally:
        d.stop()
