"""Entry point of a plugin process: ``python -m pixoohub.sdk.runner``.

Protocol: newline-delimited JSON. The hub writes to our stdin, we write to the
original stdout. sys.stdout is redirected to stderr so a stray print() in a
plugin can't corrupt the protocol. stderr is captured by the hub as the
plugin's log.

hub -> plugin
    {"type": "init", "id", "plugin_dir", "entry", "data_dir", "deps_dir", "config"}
    {"type": "action", "id": <n>, "name": str, "values": {...}}
    {"type": "stop"}

plugin -> hub
    {"type": "ready"}
    {"type": "frame", "frames": [b64...], "speed": ms}
    {"type": "release"}
    {"type": "status", "text": str, "level": "info|warn|error"}
    {"type": "reply", "id": <n>, "result": {...}}
    {"type": "fatal", "error": str}
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import os
import sys
import threading
import traceback

from . import ActionResult, Plugin

MAX_BACKOFF = 300.0


class StdioBridge:
    def __init__(self, out):
        self._out = out
        self._lock = threading.Lock()
        self.stop_event = threading.Event()

    def send(self, msg: dict) -> None:
        line = json.dumps(msg, separators=(",", ":")) + "\n"
        with self._lock:
            try:
                self._out.write(line)
                self._out.flush()
            except (BrokenPipeError, ValueError):
                self.stop_event.set()


def _load_plugin_class(plugin_dir: str, entry: str, plugin_id: str):
    path = os.path.join(plugin_dir, entry)
    mod_name = "pixoo_plugin_" + "".join(c if c.isalnum() else "_" for c in plugin_id)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    classes = [
        obj for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, Plugin) and obj is not Plugin and obj.__module__ == mod_name
    ]
    if len(classes) != 1:
        raise RuntimeError(f"{entry} must define exactly one Plugin subclass, found {len(classes)}")
    return classes[0]


def _normalize_result(result) -> dict:
    if result is None:
        return dict(ActionResult("Done."))
    if isinstance(result, str):
        return dict(ActionResult(result))
    if isinstance(result, dict):
        return dict(ActionResult(**{k: result[k] for k in ("message", "ok", "open_url", "values") if k in result}))
    raise TypeError(f"on_action returned {type(result).__name__}")


def _reader(plugin_holder: dict, bridge: StdioBridge) -> None:
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        kind = msg.get("type")
        if kind == "stop":
            bridge.stop_event.set()
        elif kind == "action":
            threading.Thread(target=_run_action, args=(plugin_holder, bridge, msg), daemon=True).start()
    bridge.stop_event.set()  # hub went away


def _run_action(holder: dict, bridge: StdioBridge, msg: dict) -> None:
    plugin = holder.get("plugin")
    try:
        if plugin is None:
            raise RuntimeError("plugin is not running")
        result = _normalize_result(plugin.on_action(msg.get("name", ""), msg.get("values") or {}))
    except Exception as e:  # report, don't die
        logging.getLogger("runner").exception("action %s failed", msg.get("name"))
        result = dict(ActionResult(f"Error: {e}", ok=False))
    bridge.send({"type": "reply", "id": msg.get("id"), "result": result})


def main() -> int:
    proto_out = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    bridge = StdioBridge(proto_out)

    init = json.loads(sys.stdin.readline() or "{}")
    if init.get("type") != "init":
        bridge.send({"type": "fatal", "error": "missing init message"})
        return 2

    deps = init.get("deps_dir")
    if deps and os.path.isdir(deps):
        sys.path.insert(0, deps)
    sys.path.insert(0, init["plugin_dir"])
    os.makedirs(init["data_dir"], exist_ok=True)

    holder: dict = {}
    threading.Thread(target=_reader, args=(holder, bridge), daemon=True).start()

    try:
        cls = _load_plugin_class(init["plugin_dir"], init["entry"], init["id"])
        plugin = cls(init["id"], init.get("config") or {}, init["data_dir"], bridge)
        plugin.setup()
    except Exception as e:
        traceback.print_exc()
        bridge.send({"type": "fatal", "error": f"{type(e).__name__}: {e}"})
        return 1

    holder["plugin"] = plugin
    bridge.send({"type": "ready"})

    log = logging.getLogger(init["id"])
    failures = 0
    while not bridge.stop_event.is_set():
        try:
            delay = plugin.tick()
            failures = 0
        except Exception as e:
            failures += 1
            delay = min(MAX_BACKOFF, 5 * 2 ** min(failures, 6))
            log.exception("tick failed (retry in %.0fs)", delay)
            plugin.status(f"{type(e).__name__}: {e}", "error")
        if delay is None:
            delay = plugin.interval
        bridge.stop_event.wait(max(0.05, float(delay)))

    try:
        plugin.teardown()
    except Exception:
        log.exception("teardown failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
