"""Filesystem layout. Everything that must survive a container update lives
below DATA_DIR (mounted as a volume in Docker)."""

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("PIXOOHUB_DATA", "data")).resolve()

STATE_FILE = DATA_DIR / "state.json"
PLUGINS_DIR = DATA_DIR / "plugins"        # plugin source checkouts
PLUGIN_DATA_DIR = DATA_DIR / "plugin-data"  # per-plugin private storage
DEPS_DIR = DATA_DIR / "deps"              # per-plugin pip --target dirs

UI_DIR = Path(__file__).resolve().parent / "web" / "static"


def ensure_dirs() -> None:
    for d in (DATA_DIR, PLUGINS_DIR, PLUGIN_DATA_DIR, DEPS_DIR):
        d.mkdir(parents=True, exist_ok=True)
