"""Screen arbitration.

Plugins *claim* the screen with a frame (or animation) and *release* it when
they have nothing to show. A single worker thread owns the device: it decides
which claim wins, pushes it, and turns the screen off when nobody claims it.
No plugin ever talks to the device directly.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .device import DeviceError, Pixoo, discover

log = logging.getLogger(__name__)

MIN_PUSH_INTERVAL = 0.35   # seconds; the Pixoo chokes on faster updates
RETRY_INTERVAL = 10.0
DISCOVERY_INTERVAL = 60.0


@dataclass
class Claim:
    frames: List[bytes]
    speed_ms: int
    since: float = field(default_factory=time.monotonic)

    @property
    def digest(self) -> str:
        h = hashlib.blake2b(digest_size=12)
        h.update(str(self.speed_ms).encode())
        for f in self.frames:
            h.update(f)
        return h.hexdigest()


def in_quiet_hours(now: dt.time, start: str, end: str) -> bool:
    try:
        s = dt.time.fromisoformat(start)
        e = dt.time.fromisoformat(end)
    except ValueError:
        return False
    if s == e:
        return False
    if s < e:
        return s <= now < e
    return now >= s or now < e  # spans midnight


class Display:
    def __init__(self, get_settings: Callable[[], dict], on_device_found: Callable[[str], None]):
        """get_settings() returns {"device": {...}, "display": {...}, "order": [...], "enabled": set()}"""
        self._get_settings = get_settings
        self._on_device_found = on_device_found
        self._claims: Dict[str, Claim] = {}
        self._cond = threading.Condition()
        self._stop = False
        self._dirty = True  # set on every change; avoids lost wakeups
        self._thread: Optional[threading.Thread] = None

        # rotation bookkeeping
        self._rot_owner: Optional[str] = None
        self._rot_since = 0.0

        # device bookkeeping (only touched by the worker thread)
        self._device: Optional[Pixoo] = None
        self._applied: Optional[Tuple] = None
        self._applied_brightness: Optional[int] = None
        self._last_push = 0.0
        self._next_attempt = 0.0
        self._last_discovery = -DISCOVERY_INTERVAL

        # published status (read by the API)
        self.status = {"connection": "idle", "ip": None, "error": None}
        self.showing: Dict = {"kind": "off", "owner": None, "reason": "nothing to show"}
        self._showing_frames: List[bytes] = []
        self._showing_speed = 0

    # ---- plugin side ---------------------------------------------------
    def claim(self, owner: str, frames: List[bytes], speed_ms: int = 100) -> None:
        with self._cond:
            old = self._claims.get(owner)
            claim = Claim(frames, speed_ms)
            if old and old.digest == claim.digest:
                return
            if old:
                claim.since = old.since
            self._claims[owner] = claim
            self._dirty = True
            self._cond.notify()

    def release(self, owner: str) -> None:
        with self._cond:
            if self._claims.pop(owner, None) is not None:
                self._dirty = True
                self._cond.notify()

    def poke(self) -> None:
        """Settings changed: re-evaluate now."""
        with self._cond:
            self._applied_brightness = None
            self._dirty = True
            self._cond.notify()

    def reconnect(self) -> None:
        with self._cond:
            self._device = None
            self._applied = None
            self._next_attempt = 0
            self._last_discovery = -DISCOVERY_INTERVAL
            self._dirty = True
            self._cond.notify()

    def claimants(self) -> List[str]:
        with self._cond:
            return list(self._claims)

    def preview(self) -> Tuple[Dict, List[bytes], int]:
        with self._cond:
            return dict(self.showing), list(self._showing_frames), self._showing_speed

    # ---- lifecycle -----------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="display", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()
        if self._thread:
            self._thread.join(timeout=5)

    # ---- decision ------------------------------------------------------
    def _pick(self, settings: dict, now: float) -> Optional[str]:
        order: List[str] = settings["order"]
        enabled = settings["enabled"]
        candidates = [p for p in order if p in self._claims and p in enabled]
        # claims from plugins missing in the order list go last
        candidates += [p for p in self._claims if p not in order and p in enabled]
        if not candidates:
            self._rot_owner = None
            return None

        disp = settings["display"]
        if disp["mode"] != "rotation" or len(candidates) == 1:
            self._rot_owner = candidates[0]
            return candidates[0]

        period = max(5, int(disp.get("rotation_seconds", 15)))
        if self._rot_owner not in candidates:
            self._rot_owner, self._rot_since = candidates[0], now
        elif now - self._rot_since >= period:
            i = candidates.index(self._rot_owner)
            self._rot_owner, self._rot_since = candidates[(i + 1) % len(candidates)], now
        return self._rot_owner

    def _target(self, settings: dict, now: float) -> Tuple:
        disp = settings["display"]
        qh = disp.get("quiet_hours", {})
        if qh.get("enabled") and in_quiet_hours(dt.datetime.now().time(), qh.get("start", ""), qh.get("end", "")):
            return ("off", None, "quiet hours")
        owner = self._pick(settings, now)
        if owner is None:
            if disp.get("idle") == "clock":
                return ("clock", None, "nothing to show")
            return ("off", None, "nothing to show")
        return ("frames", owner, self._claims[owner].digest)

    # ---- worker --------------------------------------------------------
    def _run(self) -> None:
        while True:
            with self._cond:
                if self._stop:
                    return
                self._dirty = False
                settings = self._get_settings()
                now = time.monotonic()
                target = self._target(settings, now)
                claim = self._claims.get(target[1]) if target[0] == "frames" else None
                self._publish(target, claim)

            self._ensure_device(settings, now)
            wait = 5.0
            if self._device is not None:
                wait = self._apply(target, claim, settings)
            elif self.status["connection"] != "no-device":
                wait = 2.0

            with self._cond:
                if not self._stop and not self._dirty:
                    self._cond.wait(timeout=wait)

    def _publish(self, target: Tuple, claim: Optional[Claim]) -> None:
        kind, owner, extra = target
        if kind == "frames":
            self.showing = {"kind": "frames", "owner": owner, "reason": None}
            self._showing_frames, self._showing_speed = claim.frames, claim.speed_ms
        else:
            self.showing = {"kind": kind, "owner": None, "reason": extra}
            self._showing_frames, self._showing_speed = [], 0

    def _ensure_device(self, settings: dict, now: float) -> None:
        if self._device is not None or now < self._next_attempt:
            return
        ip = settings["device"].get("ip", "").strip()
        if not ip:
            if now - self._last_discovery < DISCOVERY_INTERVAL:
                return
            self._last_discovery = now
            self.status = {"connection": "searching", "ip": None, "error": None}
            found = discover()
            if not found:
                self.status = {"connection": "no-device", "ip": None,
                               "error": "Kein Pixoo im Netzwerk gefunden. Bitte die IP von Hand eintragen."}
                return
            ip = found[0]
            log.info("found Pixoo at %s", ip)
            self._on_device_found(ip)
        dev = Pixoo(ip)
        try:
            dev.ping()
        except DeviceError as e:
            self.status = {"connection": "offline", "ip": ip, "error": str(e)}
            self._next_attempt = now + RETRY_INTERVAL
            return
        self._device = dev
        self._applied = None
        self._applied_brightness = None
        self.status = {"connection": "online", "ip": ip, "error": None}
        log.info("connected to Pixoo at %s", ip)

    def _apply(self, target: Tuple, claim: Optional[Claim], settings: dict) -> float:
        dev = self._device
        now = time.monotonic()
        try:
            want_brightness = int(settings["device"].get("brightness", 80))
            if want_brightness != self._applied_brightness:
                dev.brightness(want_brightness)
                self._applied_brightness = want_brightness

            if target == self._applied:
                return self._next_wakeup(settings)

            if target[0] == "frames":
                wait = MIN_PUSH_INTERVAL - (now - self._last_push)
                if wait > 0:
                    return wait
                dev.show(claim.frames, claim.speed_ms)
                self._last_push = time.monotonic()
                if not self._applied or self._applied[0] != "frames":
                    dev.screen(True)
            elif target[0] == "clock":
                dev.channel(0)
                dev.screen(True)
            else:
                dev.screen(False)
            self._applied = target
            return self._next_wakeup(settings)
        except DeviceError as e:
            log.warning("Pixoo unreachable: %s", e)
            self.status = {"connection": "offline", "ip": dev.ip, "error": str(e)}
            self._device = None
            self._next_attempt = now + RETRY_INTERVAL
            return RETRY_INTERVAL

    def _next_wakeup(self, settings: dict) -> float:
        # rotation and quiet hours are time-driven
        if settings["display"]["mode"] == "rotation":
            return 1.0
        return 15.0
