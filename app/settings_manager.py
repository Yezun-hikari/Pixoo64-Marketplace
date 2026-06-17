import json
import os
import threading
import logging
import tempfile
import shutil
from typing import Dict, Any

logger = logging.getLogger(__name__)

class SettingsManager:
    """Manages persistence of global application and plugin-specific settings."""
    def __init__(self, settings_file: str = "settings.json"):
        self.settings_file = settings_file
        self.lock = threading.Lock()
        self.settings: Dict[str, Any] = {
            "pixoo_ip": "",
            "mode": "priority",
            "plugin_priorities": {},
            "plugin_settings": {}
        }
        self.load()

    def load(self) -> None:
        """Loads settings from the JSON file."""
        with self.lock:
            if os.path.exists(self.settings_file):
                try:
                    with open(self.settings_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self.settings.update(data)
                except Exception as e:
                    logger.error(f"Error loading settings: {e}", exc_info=True)

    def save(self) -> None:
        """Atomically saves settings to prevent data corruption."""
        with self.lock:
            try:
                # Use a temp file for atomic saving
                fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.settings_file)), prefix="settings_", suffix=".json")
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(self.settings, f, indent=4)
                
                shutil.move(temp_path, self.settings_file)
            except Exception as e:
                logger.error(f"Error saving settings: {e}", exc_info=True)

    def get_global(self) -> Dict[str, Any]:
        """Returns global application settings."""
        return {
            "pixoo_ip": self.settings.get("pixoo_ip", ""),
            "mode": self.settings.get("mode", "priority"),
            "plugin_priorities": self.settings.get("plugin_priorities", {})
        }

    def set_global(self, pixoo_ip: str, mode: str, priorities: Dict[str, int]) -> None:
        """Updates and saves global settings."""
        self.settings["pixoo_ip"] = pixoo_ip
        self.settings["mode"] = mode
        self.settings["plugin_priorities"] = priorities
        self.save()

    def get_plugin_settings(self, plugin_name: str) -> dict:
        """Returns specific settings for a given plugin."""
        return self.settings.get("plugin_settings", {}).get(plugin_name, {})

    def set_plugin_settings(self, plugin_name: str, config: dict) -> None:
        """Updates and saves settings for a given plugin."""
        if "plugin_settings" not in self.settings:
            self.settings["plugin_settings"] = {}
        self.settings["plugin_settings"][plugin_name] = config
        self.save()
