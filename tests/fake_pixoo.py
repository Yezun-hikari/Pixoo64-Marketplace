"""A tiny emulator of the Pixoo 64 HTTP API for tests and local development.

    python tests/fake_pixoo.py --port 8090 --dump out/

Then set the hub's device IP to 127.0.0.1:8090.
"""

from __future__ import annotations

import argparse
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional


class FakePixoo:
    def __init__(self, dump_dir: Optional[Path] = None):
        self.screen_on = True
        self.brightness = 50
        self.channel = 0
        self.pic_id = 0
        self.frames: List[bytes] = []
        self.speed = 0
        self.commands: List[str] = []
        self.pushes = 0
        self.errors: List[str] = []
        self.dump_dir = dump_dir
        self._pending = {}
        self.lock = threading.Lock()
        self.fail = False  # simulate an unreachable device

    def handle(self, cmd: dict) -> dict:
        name = cmd.get("Command", "")
        with self.lock:
            self.commands.append(name)
            if name == "Channel/GetAllConf":
                return {"error_code": 0, "Brightness": self.brightness, "LightSwitch": int(self.screen_on)}
            if name == "Channel/OnOffScreen":
                self.screen_on = bool(cmd["OnOff"])
            elif name == "Channel/SetBrightness":
                self.brightness = int(cmd["Brightness"])
            elif name == "Channel/SetIndex":
                self.channel = int(cmd["SelectIndex"])
                self.frames = []
            elif name == "Draw/ResetHttpGifId":
                self.pic_id = 0
            elif name == "Draw/GetHttpGifId":
                return {"error_code": 0, "PicId": self.pic_id}
            elif name == "Draw/SendHttpGif":
                pid, num, off = cmd["PicID"], cmd["PicNum"], cmd["PicOffset"]
                if pid <= self.pic_id and off == 0:
                    self.errors.append(f"PicID {pid} not increasing (last {self.pic_id})")
                data = base64.b64decode(cmd["PicData"])
                if len(data) != 64 * 64 * 3:
                    self.errors.append(f"bad frame size {len(data)}")
                    return {"error_code": 1}
                buf = self._pending.setdefault(pid, [None] * num)
                buf[off] = data
                if all(f is not None for f in buf):
                    self.pic_id = pid
                    self.frames = buf
                    self.speed = cmd["PicSpeed"]
                    self.pushes += 1
                    self._pending.pop(pid)
                    self._dump()
            return {"error_code": 0}

    def _dump(self) -> None:
        if not self.dump_dir:
            return
        try:
            from PIL import Image
        except ImportError:
            return
        self.dump_dir.mkdir(parents=True, exist_ok=True)
        img = Image.frombytes("RGB", (64, 64), self.frames[0]).resize((256, 256), Image.NEAREST)
        img.save(self.dump_dir / f"push-{self.pushes:04d}.png")


def serve(port: int = 0, dump_dir: Optional[Path] = None):
    device = FakePixoo(dump_dir)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if device.fail:
                self.send_response(500)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            out = json.dumps(device.handle(body)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, device


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--dump", type=Path)
    a = p.parse_args()
    srv, _ = serve(a.port, a.dump)
    print(f"fake Pixoo listening on 127.0.0.1:{srv.server_address[1]}")
    threading.Event().wait()
