import os
import sys
import json
import logging
import uvicorn
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.display_manager import DisplayManager
from app.plugin_manager import PluginManager
from app.repo_manager import RepoManager
from app.settings_manager import SettingsManager

# Configure clean logging for Docker environments
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout
)
logger = logging.getLogger("main")

app = FastAPI(title="Pixoo Hub")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class HubContext:
    def __init__(self, display_mgr: DisplayManager):
        self.display_mgr = display_mgr
        
    def request_display(self, plugin, buffer: List[int]):
        self.display_mgr.request_display(plugin, buffer)
        
    def release_display(self, plugin):
        self.display_mgr.release_display(plugin)

display_mgr = DisplayManager()
context = HubContext(display_mgr)
settings_mgr = SettingsManager()
plugin_mgr = PluginManager(context, settings_mgr, plugins_dir="plugins")
repo_mgr = RepoManager(plugins_dir="plugins", repos_file="repositories.json")

@app.on_event("startup")
def startup_event():
    logger.info("Starting Pixoo Hub Marketplace...")
    global_settings = settings_mgr.get_global()
    display_mgr.set_mode(global_settings.get("mode", "priority"))
    display_mgr.set_priorities(global_settings.get("plugin_priorities", {}))
    
    pixoo_ip = os.getenv("PIXOO_IP") or global_settings.get("pixoo_ip")
    if pixoo_ip:
        display_mgr.connect(pixoo_ip)
    
    plugin_mgr.load_all_plugins()

@app.on_event("shutdown")
def shutdown_event():
    logger.info("Shutting down Pixoo Hub. Stopping all plugins...")
    for plugin_name, plugin in list(plugin_mgr.active_plugins.items()):
        plugin.stop()
    if hasattr(display_mgr, "running"):
        display_mgr.running = False

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled API error on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})

class InstallRequest(BaseModel):
    github_url: str

class RepoNameRequest(BaseModel):
    repo_name: str

class SettingsRequest(BaseModel):
    pixoo_ip: Optional[str] = None
    mode: str
    priorities: Dict[str, int]

@app.get("/api/repositories")
def get_repositories():
    repos = repo_mgr.get_all_repositories()
    res = []
    for url, data in repos.items():
        repo_name = data["name"]
        installed = repo_name in plugin_mgr.active_plugins
        local_hash = repo_mgr.get_local_hash(repo_name) if installed else None
        remote_hash = data.get("remote_hash")
        
        update_available = False
        if installed and local_hash and remote_hash and local_hash != remote_hash:
            update_available = True
            
        meta = data.get("metadata", {})
        if installed and repo_name in plugin_mgr.plugin_metadata:
            meta = plugin_mgr.plugin_metadata[repo_name]
            
        res.append({
            "url": url,
            "name": repo_name,
            "metadata": meta,
            "installed": installed,
            "update_available": update_available
        })
    return {"repositories": res}

@app.post("/api/repositories/add")
def add_repository(req: InstallRequest):
    data = repo_mgr.add_repository(req.github_url)
    return {"status": "ok", "data": data}

@app.post("/api/repositories/remove")
def remove_repository(req: InstallRequest):
    repo_mgr.remove_repository(req.github_url)
    return {"status": "ok"}

@app.post("/api/repositories/scan")
def scan_repositories():
    repo_mgr.force_scan()
    return {"status": "ok"}

@app.post("/api/plugins/install")
def install_plugin(req: InstallRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(plugin_mgr.install_from_github, req.github_url)
    return {"status": "Installation started"}

@app.post("/api/plugins/uninstall")
def uninstall_plugin(req: RepoNameRequest):
    success = plugin_mgr.uninstall_plugin(req.repo_name)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to uninstall plugin")
    return {"status": "ok"}

def background_update(repo_name: str):
    logger.info(f"Starting background update for {repo_name}...")
    success = plugin_mgr.update_plugin(repo_name)
    if success:
        local_hash = repo_mgr.get_local_hash(repo_name)
        local_meta = {}
        meta_path = os.path.join(plugin_mgr.plugins_dir, repo_name, "plugin.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    local_meta = json.load(f)
            except Exception as e:
                logger.error(f"Error reading updated plugin.json for {repo_name}: {e}")

        for url, data in repo_mgr.repositories.items():
            if data["name"] == repo_name:
                data["remote_hash"] = local_hash
                if local_meta:
                    data["metadata"] = local_meta
                repo_mgr.save_repos()
                break
        logger.info(f"Background update for {repo_name} completed successfully.")
    else:
        logger.error(f"Background update for {repo_name} failed.")

@app.post("/api/plugins/update")
def update_plugin(req: RepoNameRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(background_update, req.repo_name)
    return {"status": "Update started"}

@app.get("/api/plugins")
def get_plugins():
    return {
        "plugins": [
            {
                "name": name,
                "priority": display_mgr.plugin_priorities.get(name, 0),
                "settings": settings_mgr.get_plugin_settings(name),
                "settings_schema": plugin_mgr.plugin_metadata.get(name, {}).get("settings_schema", [])
            }
            for name in list(plugin_mgr.active_plugins.keys())
        ]
    }

@app.post("/api/plugins/{repo_name}/settings")
def save_plugin_settings(repo_name: str, config: dict):
    settings_mgr.set_plugin_settings(repo_name, config)
    if repo_name in plugin_mgr.active_plugins:
        plugin_mgr.active_plugins[repo_name].config = config
    return {"status": "ok"}

@app.get("/api/plugins/{repo_name}/icon.png")
def get_plugin_icon(repo_name: str):
    icon_path = os.path.join(plugin_mgr.plugins_dir, repo_name, "icon.png")
    if os.path.exists(icon_path):
        return FileResponse(icon_path)
    raise HTTPException(status_code=404, detail="Icon not found")

@app.get("/api/settings")
def get_settings():
    return {
        "pixoo_ip": display_mgr.pixoo_ip,
        "mode": display_mgr.mode,
        "priorities": display_mgr.plugin_priorities
    }

@app.post("/api/settings")
def save_settings(req: SettingsRequest):
    ip_to_connect = req.pixoo_ip if req.pixoo_ip else None
    if ip_to_connect != display_mgr.pixoo_ip:
        display_mgr.connect(ip_to_connect)
    display_mgr.set_mode(req.mode)
    display_mgr.set_priorities(req.priorities)
    settings_mgr.set_global(req.pixoo_ip, req.mode, req.priorities)
    return {"status": "ok"}

ui_dir = os.path.join(os.path.dirname(__file__), "..", "ui")
if os.path.exists(ui_dir):
    app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, access_log=False)
