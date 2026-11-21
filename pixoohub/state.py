"""Persistent hub state in a single JSON file, written atomically."""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)

DEFAULT_REPOSITORIES = [
    "https://github.com/Yezun-hikari/Spotify-Plugin",
    "https://github.com/Yezun-hikari/Steam-Plugin",
]

DEFAULTS: Dict[str, Any] = {
    "device": {
        "ip": "",              # empty = auto discovery
        "brightness": 80,
    },
    "display": {
        "mode": "priority",    # priority | rotation
        "rotation_seconds": 15,
        "idle": "off",         # off | clock
        "quiet_hours": {"enabled": False, "start": "23:00", "end": "07:00"},
    },
    "order": [],               # plugin ids, highest priority first
    "plugins": {},             # id -> {"enabled": bool, "config": {...}}
    "repositories": list(DEFAULT_REPOSITORIES),
}


def _merge(defaults: dict, data: dict) -> dict:
    out = copy.deepcopy(defaults)
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "plugins":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


# Plugin ids changed in 2.0 (the old hub used the repository name).
LEGACY_IDS = {"Spotify-Plugin": "spotify", "Steam-Plugin": "steam"}


def import_legacy(state: "State", legacy_file: Path) -> bool:
    """Take over settings.json from Pixoo Hub 1.x once."""
    try:
        with open(legacy_file, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(old, dict):
        return False
    dev = state.get("device")
    dev["ip"] = str(old.get("pixoo_ip") or "")
    state.set("device", dev)
    disp = state.get("display")
    if old.get("mode") in ("priority", "rotation"):
        disp["mode"] = old["mode"]
    state.set("display", disp)
    prios = old.get("plugin_priorities") or {}
    order = []
    for name, cfg in (old.get("plugin_settings") or {}).items():
        pid = LEGACY_IDS.get(name, name.lower())
        if isinstance(cfg, dict):
            state.update_plugin(pid, enabled=True, config=cfg)
        order.append((prios.get(name, 0), pid))
    if order:
        state.set("order", [pid for _, pid in sorted(order, key=lambda t: -t[0])])
    log.info("imported settings from %s", legacy_file)
    return True


class State:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._data = copy.deepcopy(DEFAULTS)
        self.fresh = not path.exists()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            self._data = _merge(DEFAULTS, raw)
        except Exception:
            # never silently overwrite a file we failed to read
            backup = self.path.with_suffix(".broken.json")
            log.exception("state file unreadable, moving it to %s", backup)
            os.replace(self.path, backup)

    def save(self) -> None:
        with self._lock:
            payload = json.dumps(self._data, indent=2, ensure_ascii=False)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp, self.path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    # Small, explicit accessors. Returned values are copies so callers can't
    # mutate shared state behind the lock.
    def get(self, key: str) -> Any:
        with self._lock:
            return copy.deepcopy(self._data[key])

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = copy.deepcopy(value)
            self.save()

    def plugin(self, pid: str) -> dict:
        with self._lock:
            entry = self._data["plugins"].get(pid) or {}
            return {"enabled": entry.get("enabled", True), "config": copy.deepcopy(entry.get("config", {}))}

    def update_plugin(self, pid: str, **fields) -> None:
        with self._lock:
            entry = self._data["plugins"].setdefault(pid, {"enabled": True, "config": {}})
            for k, v in fields.items():
                entry[k] = copy.deepcopy(v)
            if pid not in self._data["order"]:
                self._data["order"].append(pid)
            self.save()

    def forget_plugin(self, pid: str) -> None:
        with self._lock:
            self._data["plugins"].pop(pid, None)
            if pid in self._data["order"]:
                self._data["order"].remove(pid)
            self.save()
