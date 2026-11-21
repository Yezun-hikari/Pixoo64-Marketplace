"""HTTP API and static web UI."""

from __future__ import annotations

import base64
import hashlib
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..device import discover
from ..display import Display
from ..manager import PluginError, PluginManager
from ..paths import DATA_DIR, PLUGINS_DIR, STATE_FILE, UI_DIR, ensure_dirs
from ..state import State, import_legacy

log = logging.getLogger(__name__)


class Hub:
    def __init__(self):
        ensure_dirs()
        self.state = State(STATE_FILE)
        legacy = DATA_DIR / "settings.json"
        if self.state.fresh and legacy.exists() and import_legacy(self.state, legacy):
            legacy.rename(legacy.with_suffix(".imported.json"))
        self.display = Display(self._display_settings, self._device_found)
        self.plugins = PluginManager(self.state, self.display)

    def _display_settings(self) -> dict:
        plugins = self.state.get("plugins")
        return {
            "device": self.state.get("device"),
            "display": self.state.get("display"),
            "order": self.state.get("order"),
            "enabled": {pid for pid, p in plugins.items() if p.get("enabled", True)},
        }

    def _device_found(self, ip: str) -> None:
        # Remember an auto-discovered device so restarts don't rescan.
        dev = self.state.get("device")
        dev["ip"] = ip
        self.state.set("device", dev)

    def start(self) -> None:
        self.display.start()
        self.plugins.load_installed()

    def stop(self) -> None:
        self.plugins.shutdown()
        self.display.stop()


# ---- request models ------------------------------------------------------

class QuietHours(BaseModel):
    enabled: bool = False
    start: str = Field("23:00", pattern=r"^\d{2}:\d{2}$")
    end: str = Field("07:00", pattern=r"^\d{2}:\d{2}$")


class DisplaySettings(BaseModel):
    mode: str = Field("priority", pattern="^(priority|rotation)$")
    rotation_seconds: int = Field(15, ge=5, le=3600)
    idle: str = Field("off", pattern="^(off|clock)$")
    quiet_hours: QuietHours = QuietHours()


class DeviceSettings(BaseModel):
    ip: str = Field("", pattern=r"^$|^[A-Za-z0-9.:-]{1,64}$")
    brightness: int = Field(80, ge=0, le=100)


class SettingsBody(BaseModel):
    device: Optional[DeviceSettings] = None
    display: Optional[DisplaySettings] = None


class UrlBody(BaseModel):
    url: str


class ActionBody(BaseModel):
    name: str
    values: Dict[str, Any] = {}


def create_app(hub: Optional[Hub] = None) -> FastAPI:
    hub = hub or Hub()

    @asynccontextmanager
    async def lifespan(_app):
        hub.start()
        try:
            yield
        finally:
            await run_in_threadpool(hub.stop)

    app = FastAPI(title="Pixoo Hub", version=__version__, lifespan=lifespan)
    app.state.hub = hub

    @app.exception_handler(PluginError)
    async def plugin_error(_req: Request, exc: PluginError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    def need_plugin(pid: str):
        if pid not in hub.plugins.manifests:
            raise HTTPException(404, "Unknown plugin")

    # ---- overview -----------------------------------------------------
    @app.get("/api/state")
    def get_state():
        return {
            "version": __version__,
            "device": hub.state.get("device"),
            "display": hub.state.get("display"),
            "connection": hub.display.status,
            "showing": hub.display.showing,
            "claimants": hub.display.claimants(),
            "order": hub.state.get("order"),
        }

    @app.get("/api/preview")
    def preview(digest: str = ""):
        showing, frames, speed = hub.display.preview()
        h = hashlib.blake2b(digest_size=8)
        h.update(showing["kind"].encode())
        for f in frames:
            h.update(f)
        d = h.hexdigest()
        if d == digest:
            return {"digest": d, "unchanged": True}
        return {
            "digest": d,
            "showing": showing,
            "speed": speed,
            "frames": [base64.b64encode(f).decode("ascii") for f in frames],
        }

    @app.put("/api/settings")
    def put_settings(body: SettingsBody):
        if body.device is not None:
            old_ip = hub.state.get("device").get("ip", "")
            hub.state.set("device", body.device.model_dump())
            if body.device.ip != old_ip:
                hub.display.reconnect()
        if body.display is not None:
            hub.state.set("display", body.display.model_dump())
        hub.display.poke()
        return {"ok": True}

    @app.put("/api/order")
    def put_order(order: List[str] = Body(...)):
        known = set(hub.plugins.manifests)
        clean = [p for p in order if p in known]
        clean += [p for p in hub.state.get("order") if p not in clean]
        hub.state.set("order", clean)
        hub.display.poke()
        return {"ok": True}

    @app.post("/api/device/discover")
    async def device_discover():
        return {"found": await run_in_threadpool(discover)}

    @app.post("/api/device/reconnect")
    def device_reconnect():
        hub.display.reconnect()
        return {"ok": True}

    # ---- installed plugins --------------------------------------------
    @app.get("/api/plugins")
    def list_plugins():
        order = hub.state.get("order")
        ids = sorted(hub.plugins.manifests, key=lambda p: order.index(p) if p in order else 999)
        return {"plugins": [hub.plugins.plugin_view(p) for p in ids]}

    @app.get("/api/plugins/{pid}/icon")
    def plugin_icon(pid: str):
        need_plugin(pid)
        path = PLUGINS_DIR / pid / "icon.png"
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "max-age=300"})

    @app.put("/api/plugins/{pid}/config")
    def plugin_config(pid: str, values: Dict[str, Any] = Body(...)):
        need_plugin(pid)
        hub.plugins.save_config(pid, values)
        return {"ok": True}

    @app.post("/api/plugins/{pid}/enabled")
    def plugin_enabled(pid: str, enabled: bool = Body(..., embed=True)):
        need_plugin(pid)
        hub.plugins.set_enabled(pid, enabled)
        return {"ok": True}

    @app.post("/api/plugins/{pid}/action")
    async def plugin_action(pid: str, body: ActionBody):
        need_plugin(pid)
        return await run_in_threadpool(hub.plugins.run_action, pid, body.name, body.values)

    @app.post("/api/plugins/{pid}/update")
    def plugin_update(pid: str):
        need_plugin(pid)
        hub.plugins.update(pid)
        return {"ok": True}

    @app.post("/api/plugins/{pid}/restart")
    def plugin_restart(pid: str):
        need_plugin(pid)
        hub.plugins.restart(pid)
        return {"ok": True}

    @app.delete("/api/plugins/{pid}")
    def plugin_remove(pid: str):
        need_plugin(pid)
        hub.plugins.remove(pid)
        return {"ok": True}

    @app.get("/api/plugins/{pid}/logs")
    def plugin_logs(pid: str, since: float = 0):
        need_plugin(pid)
        return {"lines": hub.plugins.procs[pid].log_lines(since)}

    # ---- catalog ------------------------------------------------------
    @app.get("/api/catalog")
    def catalog():
        return {"repositories": hub.plugins.catalog_view()}

    @app.post("/api/catalog/refresh")
    def catalog_refresh():
        hub.plugins.refresh_catalog_async(force=True)
        return {"ok": True}

    @app.post("/api/repositories")
    def repo_add(body: UrlBody):
        return {"url": hub.plugins.add_repository(body.url)}

    @app.delete("/api/repositories")
    def repo_remove(url: str):
        hub.plugins.remove_repository(url)
        return {"ok": True}

    @app.post("/api/install")
    def install(body: UrlBody):
        return {"url": hub.plugins.install(body.url)}

    @app.get("/api/jobs")
    def jobs():
        return {"jobs": hub.plugins.jobs}

    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
    return app
