"""Runs one plugin in a child process and speaks the runner protocol."""

from __future__ import annotations

import base64
import collections
import itertools
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .device import FRAME_BYTES, MAX_FRAMES
from .display import Display

log = logging.getLogger(__name__)

HUB_ROOT = str(Path(__file__).resolve().parent.parent)
STOP_GRACE = 5.0
ACTION_TIMEOUT = 90.0


class PluginProcess:
    def __init__(self, pid: str, plugin_dir: Path, entry: str, data_dir: Path, deps_dir: Path, display: Display):
        self.id = pid
        self.plugin_dir = plugin_dir
        self.entry = entry
        self.data_dir = data_dir
        self.deps_dir = deps_dir
        self.display = display

        self.state = "stopped"     # stopped | starting | running | crashed | failed
        self.status_text = ""
        self.status_level = "info"
        self.error: Optional[str] = None
        self.logs: collections.deque = collections.deque(maxlen=400)

        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._want_running = False
        self._config: dict = {}
        self._restarts = 0
        self._restart_timer: Optional[threading.Timer] = None
        self._action_ids = itertools.count(1)
        self._pending: Dict[int, dict] = {}

    # ---- control ------------------------------------------------------
    def start(self, config: dict) -> None:
        with self._lock:
            self._want_running = True
            self._config = config
            self._restarts = 0
            self._spawn()

    def restart(self, config: dict) -> None:
        self.stop()
        self.start(config)

    def stop(self) -> None:
        with self._lock:
            self._want_running = False
            if self._restart_timer:
                self._restart_timer.cancel()
                self._restart_timer = None
            proc = self._proc
        if proc and proc.poll() is None:
            self._write({"type": "stop"})
            try:
                proc.wait(timeout=STOP_GRACE)
            except subprocess.TimeoutExpired:
                log.warning("%s did not stop in time, terminating", self.id)
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        with self._lock:
            self._proc = None
            if self.state != "failed":
                self.state = "stopped"
            self.status_text, self.status_level = "", "info"
        self.display.release(self.id)

    def action(self, name: str, values: dict) -> dict:
        if self.state != "running":
            return {"ok": False, "message": "Plugin is not running.", "open_url": "", "values": {}}
        aid = next(self._action_ids)
        slot = {"event": threading.Event(), "result": None}
        self._pending[aid] = slot
        self._write({"type": "action", "id": aid, "name": name, "values": values})
        try:
            if not slot["event"].wait(ACTION_TIMEOUT):
                return {"ok": False, "message": "Plugin did not answer.", "open_url": "", "values": {}}
            return slot["result"]
        finally:
            self._pending.pop(aid, None)

    # ---- internals ----------------------------------------------------
    def _spawn(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = HUB_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.state = "starting"
        self.error = None
        self._log("hub", f"starting ({self.entry})")
        proc = subprocess.Popen(
            [sys.executable, "-m", "pixoohub.sdk.runner"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(self.data_dir), env=env, text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        self._proc = proc
        self._write({
            "type": "init", "id": self.id, "plugin_dir": str(self.plugin_dir), "entry": self.entry,
            "data_dir": str(self.data_dir), "deps_dir": str(self.deps_dir), "config": self._config,
        })
        threading.Thread(target=self._read_stdout, args=(proc,), name=f"{self.id}-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(proc,), name=f"{self.id}-err", daemon=True).start()

    def _write(self, msg: dict) -> None:
        proc = self._proc
        if not proc or proc.poll() is not None:
            return
        with self._write_lock:
            try:
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass

    def _log(self, source: str, line: str) -> None:
        self.logs.append({"t": time.time(), "src": source, "line": line})

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                self._log("plugin", line)
                log.debug("[%s] %s", self.id, line)

    def _read_stdout(self, proc: subprocess.Popen) -> None:
        fatal = None
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            fatal = self._handle(msg) or fatal
        code = proc.wait()
        self._on_exit(proc, code, fatal)

    def _handle(self, msg: dict) -> Optional[str]:
        kind = msg.get("type")
        if kind == "frame":
            frames = self._decode_frames(msg.get("frames"))
            if frames:
                self.display.claim(self.id, frames, int(msg.get("speed") or 100))
        elif kind == "release":
            self.display.release(self.id)
        elif kind == "status":
            self.status_text = str(msg.get("text", ""))[:300]
            self.status_level = msg.get("level") if msg.get("level") in ("info", "warn", "error") else "info"
        elif kind == "ready":
            self.state = "running"
            self._restarts = 0
            self._log("hub", "running")
        elif kind == "reply":
            slot = self._pending.get(msg.get("id"))
            if slot:
                slot["result"] = msg.get("result") or {}
                slot["event"].set()
        elif kind == "fatal":
            return str(msg.get("error", "unknown error"))
        return None

    def _decode_frames(self, raw) -> List[bytes]:
        if not isinstance(raw, list) or not raw:
            return []
        out = []
        for item in raw[:MAX_FRAMES]:
            try:
                data = base64.b64decode(item)
            except (ValueError, TypeError):
                return []
            if len(data) != FRAME_BYTES:
                self._log("hub", f"dropped frame with {len(data)} bytes")
                return []
            out.append(data)
        return out

    def _on_exit(self, proc: subprocess.Popen, code: int, fatal: Optional[str]) -> None:
        with self._lock:
            if proc is not self._proc and self._proc is not None:
                return  # an old process; a new one is already running
            self.display.release(self.id)
            for slot in list(self._pending.values()):
                slot["result"] = {"ok": False, "message": "Plugin exited.", "open_url": "", "values": {}}
                slot["event"].set()
            if not self._want_running:
                return
            self._proc = None
            if fatal:
                self.state, self.error = "failed", fatal
                self.status_text, self.status_level = fatal, "error"
                self._log("hub", f"setup failed: {fatal}")
                return
            self.state = "crashed"
            self._restarts += 1
            delay = min(300, 5 * 2 ** min(self._restarts - 1, 6))
            self.error = f"exited with code {code}"
            self._log("hub", f"exited with code {code}, restarting in {delay}s")
            self._restart_timer = threading.Timer(delay, self._delayed_restart)
            self._restart_timer.daemon = True
            self._restart_timer.start()

    def _delayed_restart(self) -> None:
        with self._lock:
            self._restart_timer = None
            if self._want_running and self._proc is None:
                self._spawn()

    def info(self) -> dict:
        return {
            "state": self.state,
            "status": self.status_text,
            "level": self.status_level,
            "error": self.error,
        }

    def log_lines(self, since: float = 0) -> List[dict]:
        return [l for l in list(self.logs) if l["t"] > since]
