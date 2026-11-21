"""Minimal client for the Pixoo 64 local HTTP API and device discovery.

The device accepts JSON commands via POST http://<ip>/post. Frames are sent
with Draw/SendHttpGif as base64 RGB. The device keeps an internal picture id
counter; it must increase with every picture and degrades (lag, freezes) when
it grows large, so we reset it regularly.
"""

from __future__ import annotations

import base64
import ipaddress
import logging
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import requests

log = logging.getLogger(__name__)

SIZE = 64
FRAME_BYTES = SIZE * SIZE * 3
MAX_FRAMES = 40          # the device rejects/garbles larger animations
PIC_ID_RESET_AT = 30


class DeviceError(Exception):
    pass


class Pixoo:
    def __init__(self, ip: str, timeout: float = 4.0):
        self.ip = ip
        self.url = f"http://{ip}/post"
        self.timeout = timeout
        self._session = requests.Session()
        self._pic_id: Optional[int] = None

    def command(self, name: str, **params) -> dict:
        payload = {"Command": name, **params}
        try:
            r = self._session.post(self.url, json=payload, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise DeviceError(f"{name}: {e}") from e
        if data.get("error_code", 0) != 0:
            raise DeviceError(f"{name}: device returned {data}")
        return data

    def ping(self) -> dict:
        return self.command("Channel/GetAllConf")

    def screen(self, on: bool) -> None:
        self.command("Channel/OnOffScreen", OnOff=1 if on else 0)

    def brightness(self, value: int) -> None:
        self.command("Channel/SetBrightness", Brightness=max(0, min(100, int(value))))

    def channel(self, index: int) -> None:
        """0 = clock faces, 1 = cloud, 2 = visualizer, 3 = custom."""
        self.command("Channel/SetIndex", SelectIndex=int(index))

    def _next_pic_id(self) -> int:
        if self._pic_id is None or self._pic_id >= PIC_ID_RESET_AT:
            self.command("Draw/ResetHttpGifId")
            self._pic_id = 0
        self._pic_id += 1
        return self._pic_id

    def show(self, frames: List[bytes], speed_ms: int = 100) -> None:
        if not frames:
            raise ValueError("no frames")
        frames = frames[:MAX_FRAMES]
        for f in frames:
            if len(f) != FRAME_BYTES:
                raise ValueError(f"frame must be {FRAME_BYTES} bytes, got {len(f)}")
        pic_id = self._next_pic_id()
        try:
            for offset, frame in enumerate(frames):
                self.command(
                    "Draw/SendHttpGif",
                    PicNum=len(frames),
                    PicWidth=SIZE,
                    PicOffset=offset,
                    PicID=pic_id,
                    PicSpeed=max(20, int(speed_ms)),
                    PicData=base64.b64encode(frame).decode("ascii"),
                )
        except DeviceError:
            # the counter state on the device is now unknown
            self._pic_id = None
            raise


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def _probe(ip: str) -> Optional[str]:
    try:
        r = requests.post(f"http://{ip}/post", json={"Command": "Channel/GetAllConf"}, timeout=0.8)
        if r.ok and "error_code" in r.json():
            return ip
    except (requests.RequestException, ValueError):
        pass
    return None


def _cloud_lookup() -> List[str]:
    """Divoom's cloud returns devices that share our public IP."""
    try:
        r = requests.post("https://app.divoom-gz.com/Device/ReturnSameLANDevice", json={}, timeout=5)
        return [d["DevicePrivateIP"] for d in r.json().get("DeviceList", []) if d.get("DevicePrivateIP")]
    except Exception as e:
        log.debug("cloud discovery failed: %s", e)
        return []


def _local_networks() -> List[ipaddress.IPv4Network]:
    nets = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent for UDP connect
        ip = s.getsockname()[0]
        nets.append(ipaddress.ip_network(f"{ip}/24", strict=False))
    except OSError:
        pass
    finally:
        s.close()
    return nets


def discover(extra_subnet: str = "") -> List[str]:
    found: List[str] = []
    for ip in _cloud_lookup():
        if _probe(ip):
            found.append(ip)
    if found:
        return found

    nets = _local_networks()
    if extra_subnet:
        try:
            nets.insert(0, ipaddress.ip_network(extra_subnet, strict=False))
        except ValueError:
            pass
    for net in nets:
        if net.num_addresses > 1024:
            continue
        with ThreadPoolExecutor(max_workers=64) as pool:
            for result in pool.map(_probe, (str(h) for h in net.hosts())):
                if result:
                    found.append(result)
        if found:
            break
    return found
