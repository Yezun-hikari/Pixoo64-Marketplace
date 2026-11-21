"""SDK for Pixoo Hub plugins.

A plugin is a subclass of :class:`Plugin`. The hub starts it in its own
process (see runner.py), so a crashing or hanging plugin can't take the hub
or other plugins down.

    from pixoohub.sdk import Plugin

    class Clock(Plugin):
        interval = 1.0

        def tick(self):
            img = self.canvas()
            self.text(img, time.strftime("%H:%M"), (22, 30))
            self.show(img)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Union

from .font import draw_text, text_width, wrap  # noqa: F401  (re-export)

SIZE = 64

__all__ = ["Plugin", "ActionResult", "Storage", "draw_text", "text_width", "wrap", "SIZE"]


class ActionResult(dict):
    """Returned from Plugin.on_action. Shown to the user in the web UI."""

    def __init__(self, message: str = "", ok: bool = True, open_url: str = "", values: Optional[dict] = None):
        super().__init__(message=message, ok=ok, open_url=open_url, values=values or {})


class Storage:
    """Small JSON key/value store in the plugin's private data directory.
    Use it for things the plugin learns at runtime (tokens, caches), not for
    user settings - those live in self.config."""

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        try:
            with open(path, encoding="utf-8") as f:
                self._data = json.load(f)
        except (OSError, ValueError):
            self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._flush()

    def delete(self, key: str) -> None:
        with self._lock:
            if self._data.pop(key, None) is not None:
                self._flush()

    def _flush(self) -> None:
        d = os.path.dirname(self._path)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".storage-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=1)
        os.replace(tmp, self._path)


class Plugin:
    #: seconds between tick() calls when tick() returns None
    interval: float = 5.0

    def __init__(self, plugin_id: str, config: Dict[str, Any], data_dir: str, bridge: "Bridge"):
        self.id = plugin_id
        self.config = config
        self.data_dir = data_dir
        self.log = logging.getLogger(plugin_id)
        self.storage = Storage(os.path.join(data_dir, "storage.json"))
        self._bridge = bridge
        self._status = None

    # ---- lifecycle hooks ----------------------------------------------
    def setup(self) -> None:
        """Called once after start. Raise to signal a configuration error."""

    def tick(self) -> Optional[float]:
        """Called repeatedly. Return the delay until the next call in seconds
        (None = self.interval)."""
        return None

    def teardown(self) -> None:
        """Called once before the process exits."""

    def on_action(self, name: str, values: Dict[str, Any]) -> Union[ActionResult, dict, str, None]:
        """Button of type "action" in the settings form was pressed. `values`
        holds the current (unsaved) form values."""
        return ActionResult(f"Unknown action: {name}", ok=False)

    # ---- screen ---------------------------------------------------------
    def show(self, image, speed_ms: int = 100) -> None:
        """Claim the screen. `image` is a PIL image or a list of images
        (an animation, max 40 frames). Images that are not 64x64 are scaled."""
        frames = image if isinstance(image, (list, tuple)) else [image]
        self._bridge.send({"type": "frame", "frames": [_to_rgb64(f) for f in frames], "speed": int(speed_ms)})

    def release(self) -> None:
        """Give the screen back. If no other plugin wants it, it turns off."""
        self._bridge.send({"type": "release"})

    def status(self, text: str, level: str = "info") -> None:
        """One-line status shown next to the plugin in the web UI.
        level: info | warn | error"""
        if self._status != (text, level):
            self._status = (text, level)
            self._bridge.send({"type": "status", "text": text, "level": level})

    # ---- helpers --------------------------------------------------------
    @staticmethod
    def canvas(color=(0, 0, 0)):
        from PIL import Image
        return Image.new("RGB", (SIZE, SIZE), color)

    @staticmethod
    def text(img, text: str, xy, color=(255, 255, 255)) -> None:
        draw_text(img, text, xy, color)

    @property
    def stopping(self) -> bool:
        return self._bridge.stop_event.is_set()

    def sleep(self, seconds: float) -> bool:
        """Interruptible sleep. Returns False when the plugin should stop."""
        return not self._bridge.stop_event.wait(max(0.0, seconds))

    def cfg(self, key: str, default: Any = None, cast: Optional[Callable] = None) -> Any:
        """Read a setting with a default and an optional type conversion
        that falls back to the default on bad input."""
        val = self.config.get(key, default)
        if val in (None, ""):
            val = default
        if cast is not None and val is not None:
            try:
                if cast is bool and isinstance(val, str):
                    return val.strip().lower() in ("1", "true", "yes", "on")
                return cast(val)
            except (TypeError, ValueError):
                return default
        return val


def _to_rgb64(img) -> str:
    """PIL image -> base64 of exactly 64*64*3 bytes."""
    import base64
    from PIL import Image

    if img.mode != "RGB":
        bg = Image.new("RGB", img.size, (0, 0, 0))
        img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[3])
        img = bg
    if img.size != (SIZE, SIZE):
        w, h = img.size
        if w == h and SIZE % w == 0:
            img = img.resize((SIZE, SIZE), Image.NEAREST)
        elif w == h and w % SIZE == 0:
            img = img.resize((SIZE, SIZE), Image.BOX)
        else:
            img = img.copy()
            img.thumbnail((SIZE, SIZE), Image.LANCZOS)
            canvas = Image.new("RGB", (SIZE, SIZE))
            canvas.paste(img, ((SIZE - img.width) // 2, (SIZE - img.height) // 2))
            img = canvas
    return base64.b64encode(img.tobytes()).decode("ascii")


class Bridge:  # implemented in runner.py; declared here for type hints
    stop_event: threading.Event

    def send(self, msg: dict) -> None:  # pragma: no cover
        raise NotImplementedError
