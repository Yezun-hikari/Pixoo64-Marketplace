"""Installing, updating and running plugins."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from . import API_VERSION
from .display import Display
from .host import PluginProcess
from .paths import DEPS_DIR, PLUGIN_DATA_DIR, PLUGINS_DIR
from .state import State

log = logging.getLogger(__name__)

SECRET_MARK = "•" * 8
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")
URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:\d+)?/[A-Za-z0-9._~/-]+$")
GITHUB_RE = re.compile(r"^https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")
CATALOG_TTL = 6 * 3600
FIELD_TYPES = {"header", "text", "password", "number", "switch", "select", "multiselect",
               "slider", "textarea", "action", "note"}


class PluginError(Exception):
    pass


def normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    if not URL_RE.match(url):
        raise PluginError("Keine gültige https-Adresse eines Repositorys.")
    return url


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-")
    return s[:48] or "plugin"


def _git(*args: str, cwd: Optional[Path] = None, timeout: int = 120) -> str:
    import os
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError as e:
        raise PluginError("git is not installed") from e
    except subprocess.TimeoutExpired as e:
        raise PluginError(f"git {args[0]} timed out") from e
    if res.returncode != 0:
        raise PluginError((res.stderr or res.stdout).strip().splitlines()[-1] if (res.stderr or res.stdout) else f"git {args[0]} failed")
    return res.stdout


def read_manifest(plugin_dir: Path, fallback_id: str = "") -> dict:
    path = plugin_dir / "plugin.json"
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError as e:
        raise PluginError("plugin.json fehlt.") from e
    except ValueError as e:
        raise PluginError(f"plugin.json ist ungültig: {e}") from e
    return validate_manifest(raw, fallback_id)


def validate_manifest(raw: Any, fallback_id: str = "") -> dict:
    if not isinstance(raw, dict):
        raise PluginError("plugin.json must contain an object")
    pid = raw.get("id") or _slug(fallback_id or raw.get("name", ""))
    if not ID_RE.match(pid):
        raise PluginError(f"invalid plugin id '{pid}'")
    entry = raw.get("entry", "plugin.py")
    if "/" in entry or "\\" in entry or not entry.endswith(".py"):
        raise PluginError("entry must be a .py file in the plugin root")
    api = int(raw.get("hub_api", 1))
    fields = raw.get("settings", raw.get("settings_schema", []))
    if not isinstance(fields, list):
        raise PluginError("settings must be a list")
    clean = []
    for f in fields:
        if not isinstance(f, dict) or f.get("type") not in FIELD_TYPES:
            continue
        if f["type"] not in ("header", "note") and not f.get("name"):
            continue
        clean.append(f)
    return {
        "id": pid,
        "name": str(raw.get("name") or pid),
        "version": str(raw.get("version", "0.0.0")),
        "description": str(raw.get("description", "")),
        "author": str(raw.get("author", "")),
        "entry": entry,
        "hub_api": api,
        "settings": clean,
    }


def coerce_config(fields: List[dict], values: dict, previous: dict) -> dict:
    """Bring submitted form values into the types the schema declares."""
    out = {}
    for f in fields:
        name, typ = f.get("name"), f["type"]
        if not name or typ in ("header", "note", "action"):
            continue
        v = values.get(name, f.get("default"))
        if typ == "password":
            out[name] = previous.get(name, "") if v == SECRET_MARK else (v or "")
        elif typ in ("number", "slider"):
            try:
                n = float(v)
                if "min" in f:
                    n = max(float(f["min"]), n)
                if "max" in f:
                    n = min(float(f["max"]), n)
                out[name] = int(n) if n.is_integer() else n
            except (TypeError, ValueError):
                out[name] = f.get("default")
        elif typ == "switch":
            out[name] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on", "yes")
        elif typ == "multiselect":
            out[name] = [str(x) for x in v] if isinstance(v, list) else []
        else:
            out[name] = "" if v is None else str(v)
    return out


def with_defaults(fields: List[dict], config: dict) -> dict:
    out = dict(config)
    for f in fields:
        name = f.get("name")
        if name and name not in out and "default" in f:
            out[name] = f["default"]
    return out


def mask_secrets(fields: List[dict], config: dict) -> dict:
    out = dict(config)
    for f in fields:
        if f["type"] == "password" and out.get(f["name"]):
            out[f["name"]] = SECRET_MARK
    return out


# --------------------------------------------------------------------------
# dependencies
# --------------------------------------------------------------------------

def _unsatisfied(requirements: List[str]) -> List[str]:
    """Drop requirement lines the hub's own environment already satisfies,
    so plugins don't reinstall Pillow or requests into their target dir."""
    from importlib import metadata

    try:
        from packaging.requirements import Requirement
    except ImportError:  # pragma: no cover
        return requirements
    missing = []
    for line in requirements:
        try:
            req = Requirement(line)
        except Exception:
            missing.append(line)
            continue
        try:
            installed = metadata.version(req.name)
        except metadata.PackageNotFoundError:
            missing.append(line)
            continue
        if req.specifier and not req.specifier.contains(installed, prereleases=True):
            missing.append(line)
    return missing


def ensure_deps(pid: str, plugin_dir: Path) -> None:
    req_file = plugin_dir / "requirements.txt"
    target = DEPS_DIR / pid
    lines = []
    if req_file.exists():
        for raw in req_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                lines.append(line)
    fingerprint = hashlib.sha256(("\n".join(lines) + sys.version).encode()).hexdigest()
    marker = target / ".fingerprint"
    if marker.exists() and marker.read_text() == fingerprint:
        return
    if target.exists():
        shutil.rmtree(target)
    todo = _unsatisfied(lines)
    target.mkdir(parents=True, exist_ok=True)
    if todo:
        log.info("installing dependencies for %s: %s", pid, ", ".join(todo))
        res = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
             "--no-warn-script-location", "--target", str(target), *todo],
            capture_output=True, text=True, timeout=900,
        )
        if res.returncode != 0:
            shutil.rmtree(target, ignore_errors=True)
            tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
            raise PluginError("Installation der Abhängigkeiten fehlgeschlagen: " + " | ".join(tail))
    marker.write_text(fingerprint)


# --------------------------------------------------------------------------
# manager
# --------------------------------------------------------------------------

class PluginManager:
    def __init__(self, state: State, display: Display):
        self.state = state
        self.display = display
        self.procs: Dict[str, PluginProcess] = {}
        self.manifests: Dict[str, dict] = {}
        self.sources: Dict[str, Dict[str, str]] = {}   # id -> {"url", "commit"}
        self.catalog: Dict[str, dict] = {}              # url -> entry
        self.jobs: Dict[str, dict] = {}                 # key -> {"state", "message", "t"}
        self._pending: Dict[str, int] = {}              # key -> queued/running job count
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plugin-jobs")
        self._lock = threading.RLock()
        self._catalog_checked = 0.0

    # ---- startup / shutdown -------------------------------------------
    def load_installed(self) -> None:
        for d in sorted(PLUGINS_DIR.iterdir()) if PLUGINS_DIR.exists() else []:
            if not d.is_dir() or d.name.startswith("."):
                continue
            try:
                m = read_manifest(d, d.name)
            except PluginError as e:
                log.error("skipping %s: %s", d.name, e)
                continue
            if m["id"] != d.name:
                log.error("skipping %s: directory name must equal plugin id '%s'", d.name, m["id"])
                continue
            self._register(d, m)
        for pid in list(self.manifests):
            if self.state.plugin(pid)["enabled"]:
                self._submit(pid, f"Starte {pid}", self._start, pid)
        self.refresh_catalog_async()

    def shutdown(self) -> None:
        for p in list(self.procs.values()):
            p.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _register(self, d: Path, m: dict) -> None:
        pid = m["id"]
        self.manifests[pid] = m
        src = {"url": "", "commit": ""}
        if (d / ".git").exists():
            try:
                src["url"] = normalize_url(_git("remote", "get-url", "origin", cwd=d).strip())
                src["commit"] = _git("rev-parse", "HEAD", cwd=d).strip()
            except PluginError:
                pass
        self.sources[pid] = src
        if pid not in self.procs:
            self.procs[pid] = PluginProcess(pid, d, m["entry"], PLUGIN_DATA_DIR / pid, DEPS_DIR / pid, self.display)
        else:
            self.procs[pid].entry = m["entry"]
        if pid not in self.state.get("order"):
            self.state.update_plugin(pid)

    def _config_for(self, pid: str) -> dict:
        return with_defaults(self.manifests[pid]["settings"], self.state.plugin(pid)["config"])

    def _start(self, pid: str) -> str:
        m = self.manifests[pid]
        if m["hub_api"] < API_VERSION:
            raise PluginError(f"{m['name']} wurde für eine ältere Hub-Version geschrieben (hub_api {m['hub_api']}). Bitte das Plugin aktualisieren.")
        ensure_deps(pid, PLUGINS_DIR / pid)
        self.procs[pid].restart(self._config_for(pid))
        return f"{m['name']} läuft"

    # ---- jobs ---------------------------------------------------------
    def _submit(self, key: str, message: str, fn, *args, queue: bool = False) -> None:
        """Run fn in the (serial) job worker. Install/update/remove refuse to
        overlap; restarts may queue behind a running job (queue=True)."""
        with self._lock:
            job = self.jobs.get(key)
            if job and job["state"] == "running" and not queue:
                raise PluginError("Für dieses Plugin läuft gerade schon ein Vorgang.")
            self.jobs[key] = {"state": "running", "message": message, "t": time.time()}
            self._pending[key] = self._pending.get(key, 0) + 1

        def finish(state: str, msg: str) -> None:
            # Only the last of several queued jobs may report the final state;
            # otherwise the UI shows "done" while a restart is still pending.
            with self._lock:
                self._pending[key] -= 1
                if self._pending[key] == 0:
                    del self._pending[key]
                    self.jobs[key] = {"state": state, "message": msg, "t": time.time()}
                elif state == "error":
                    self.jobs[key] = {"state": "running", "message": f"{message} (vorheriger Schritt fehlgeschlagen: {msg})",
                                      "t": time.time()}

        def run():
            try:
                result = fn(*args)
                finish("done", result or "Fertig")
            except Exception as e:
                log.exception("job %s failed", key)
                finish("error", str(e))
                if key in self.procs and isinstance(e, PluginError):
                    self.procs[key].state = "failed"
                    self.procs[key].error = str(e)
                    self.procs[key].status_text, self.procs[key].status_level = str(e), "error"

        self._pool.submit(run)

    # ---- public operations --------------------------------------------
    def install(self, url: str) -> str:
        url = normalize_url(url)
        self._submit(url, "Wird installiert", self._install, url)
        return url

    def _install(self, url: str) -> str:
        with tempfile.TemporaryDirectory(dir=PLUGINS_DIR, prefix=".install-") as tmp:
            work = Path(tmp) / "src"
            _git("clone", "--depth", "1", "--", url, str(work), timeout=300)
            fallback = GITHUB_RE.match(url).group(2) if GITHUB_RE.match(url) else url.rsplit("/", 1)[-1]
            m = read_manifest(work, fallback)
            pid = m["id"]
            existing = self.sources.get(pid)
            if pid in self.manifests and existing and existing.get("url") != url:
                raise PluginError(f"Ein anderes Plugin mit der ID '{pid}' ist schon installiert.")
            if pid in self.procs:
                self.procs[pid].stop()
            dest = PLUGINS_DIR / pid
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(work), str(dest))
        self._register(dest, m)
        if url not in self.state.get("repositories"):
            self.state.set("repositories", self.state.get("repositories") + [url])
        self.state.update_plugin(pid, enabled=True)
        self._start(pid)
        return f"{m['name']} {m['version']} installiert"

    def update(self, pid: str) -> None:
        if pid not in self.manifests:
            raise PluginError("Unbekanntes Plugin.")
        self._submit(pid, "Wird aktualisiert", self._update, pid)

    def _update(self, pid: str) -> str:
        d = PLUGINS_DIR / pid
        if not (d / ".git").exists():
            raise PluginError("Das Plugin wurde nicht über git installiert und muss von Hand aktualisiert werden.")
        _git("fetch", "--depth", "1", "origin", "HEAD", cwd=d, timeout=300)
        _git("reset", "--hard", "FETCH_HEAD", cwd=d)
        _git("clean", "-fdx", "-e", "__pycache__", cwd=d)
        m = read_manifest(d, pid)
        if m["id"] != pid:
            raise PluginError("Die Plugin-ID hat sich geändert – bitte entfernen und neu installieren.")
        self.procs[pid].stop()
        self._register(d, m)
        if self.state.plugin(pid)["enabled"]:
            self._start(pid)
        return f"Auf {m['version']} aktualisiert"

    def remove(self, pid: str) -> None:
        if pid not in self.manifests:
            raise PluginError("Unbekanntes Plugin.")
        self._submit(pid, "Wird entfernt", self._remove, pid)

    def _remove(self, pid: str) -> str:
        self.procs[pid].stop()
        for d in (PLUGINS_DIR / pid, DEPS_DIR / pid, PLUGIN_DATA_DIR / pid):
            shutil.rmtree(d, ignore_errors=True)
        self.procs.pop(pid, None)
        self.manifests.pop(pid, None)
        self.sources.pop(pid, None)
        self.state.forget_plugin(pid)
        return "Entfernt"

    def set_enabled(self, pid: str, enabled: bool) -> None:
        if pid not in self.manifests:
            raise PluginError("Unbekanntes Plugin.")
        self.state.update_plugin(pid, enabled=enabled)
        if enabled:
            self._submit(pid, "Startet", self._start, pid, queue=True)
        else:
            self.procs[pid].stop()
        self.display.poke()

    def restart(self, pid: str) -> None:
        if pid not in self.manifests:
            raise PluginError("Unbekanntes Plugin.")
        self._submit(pid, "Startet neu", self._start, pid, queue=True)

    def save_config(self, pid: str, values: dict) -> None:
        m = self.manifests.get(pid)
        if not m:
            raise PluginError("Unbekanntes Plugin.")
        cfg = coerce_config(m["settings"], values, self.state.plugin(pid)["config"])
        self.state.update_plugin(pid, config=cfg)
        if self.state.plugin(pid)["enabled"]:
            self._submit(pid, "Übernehme Einstellungen", self._start, pid, queue=True)

    def run_action(self, pid: str, name: str, values: dict) -> dict:
        m = self.manifests.get(pid)
        if not m:
            raise PluginError("Unbekanntes Plugin.")
        if not any(f["type"] == "action" and f["name"] == name for f in m["settings"]):
            raise PluginError("Unbekannte Aktion.")
        stored = self.state.plugin(pid)["config"]
        merged = with_defaults(m["settings"], stored)
        for f in m["settings"]:
            n = f.get("name")
            if n in values and f["type"] not in ("header", "note", "action"):
                merged[n] = values[n]
        for f in m["settings"]:
            if f["type"] == "password" and merged.get(f["name"]) == SECRET_MARK:
                merged[f["name"]] = stored.get(f["name"], "")
        return self.procs[pid].action(name, merged)

    # ---- catalog ------------------------------------------------------
    def add_repository(self, url: str) -> str:
        url = normalize_url(url)
        repos = self.state.get("repositories")
        if url not in repos:
            self.state.set("repositories", repos + [url])
        self._submit("catalog:" + url, "Repository wird gelesen", self._refresh_one, url)
        return url

    def remove_repository(self, url: str) -> None:
        repos = [r for r in self.state.get("repositories") if r != url]
        self.state.set("repositories", repos)
        self.catalog.pop(url, None)

    def refresh_catalog_async(self, force: bool = False) -> None:
        if not force and time.time() - self._catalog_checked < CATALOG_TTL:
            return
        self._catalog_checked = time.time()
        threading.Thread(target=self._refresh_all, name="catalog", daemon=True).start()

    def _refresh_all(self) -> None:
        for url in self.state.get("repositories"):
            try:
                self._refresh_one(url)
            except Exception as e:
                log.warning("catalog refresh failed for %s: %s", url, e)

    def _refresh_one(self, url: str) -> str:
        entry = {"url": url, "manifest": None, "commit": "", "error": None, "checked": time.time(), "icon": ""}
        try:
            head = _git("ls-remote", "--", url, "HEAD", timeout=30).split()
            if not head:
                raise PluginError("Das Repository ist leer.")
            entry["commit"] = head[0]
            gh = GITHUB_RE.match(url)
            if gh:
                base = f"https://raw.githubusercontent.com/{gh.group(1)}/{gh.group(2)}/{entry['commit']}"
                r = requests.get(base + "/plugin.json", timeout=15)
                if r.status_code == 404:
                    raise PluginError("Im Repository gibt es keine plugin.json.")
                r.raise_for_status()
                entry["manifest"] = validate_manifest(r.json(), gh.group(2))
                entry["icon"] = base + "/icon.png"
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    _git("clone", "--depth", "1", "--", url, tmp, timeout=300)
                    entry["manifest"] = read_manifest(Path(tmp), url.rsplit("/", 1)[-1])
        except Exception as e:
            entry["error"] = str(e)
        self.catalog[url] = entry
        if entry["error"]:
            raise PluginError(entry["error"])
        return "Repository hinzugefügt"

    # ---- views --------------------------------------------------------
    def plugin_view(self, pid: str) -> dict:
        m = self.manifests[pid]
        st = self.state.plugin(pid)
        proc = self.procs.get(pid)
        src = self.sources.get(pid, {})
        cat = self.catalog.get(src.get("url", ""), {})
        update = bool(cat.get("commit") and src.get("commit") and cat["commit"] != src["commit"])
        return {
            **{k: m[k] for k in ("id", "name", "version", "description", "author", "settings")},
            "enabled": st["enabled"],
            "config": mask_secrets(m["settings"], with_defaults(m["settings"], st["config"])),
            "source": src.get("url", ""),
            "commit": src.get("commit", "")[:7],
            "update_available": update,
            "latest_version": (cat.get("manifest") or {}).get("version"),
            "outdated_api": m["hub_api"] < API_VERSION,
            "has_icon": (PLUGINS_DIR / pid / "icon.png").exists(),
            "job": self.jobs.get(pid),
            **(proc.info() if proc else {}),
        }

    def catalog_view(self) -> List[dict]:
        installed_by_url = {s["url"]: pid for pid, s in self.sources.items() if s.get("url")}
        out = []
        for url in self.state.get("repositories"):
            entry = self.catalog.get(url) or {"url": url, "manifest": None, "error": None, "commit": "", "icon": ""}
            out.append({
                "url": url,
                "manifest": entry.get("manifest"),
                "icon": entry.get("icon", ""),
                "error": entry.get("error"),
                "pending": url not in self.catalog,
                "installed": installed_by_url.get(url),
                "job": self.jobs.get(url) or self.jobs.get("catalog:" + url),
            })
        return out
