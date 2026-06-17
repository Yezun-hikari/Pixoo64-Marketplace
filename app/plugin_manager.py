import os
import sys
import json
import subprocess
import threading
import importlib.util
import logging
import shutil
import stat
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class PluginManager:
    """Manages the lifecycle, loading, and installation of Pixoo Hub plugins."""
    
    def __init__(self, context: Any, settings_mgr: Any, plugins_dir: str = "plugins"):
        self.context = context
        self.settings_mgr = settings_mgr
        self.plugins_dir = plugins_dir
        
        self.active_plugins: Dict[str, Any] = {}
        self.plugin_threads: Dict[str, threading.Thread] = {}
        self.plugin_metadata: Dict[str, dict] = {}
        
        self.lock = threading.Lock()
        
        if not os.path.exists(self.plugins_dir):
            os.makedirs(self.plugins_dir)

    def load_all_plugins(self) -> None:
        """Loads all installed plugins from the plugins directory."""
        for plugin_name in os.listdir(self.plugins_dir):
            self.load_plugin(plugin_name)

    def load_plugin(self, plugin_name: str) -> bool:
        """Loads and starts a specific plugin."""
        plugin_path = os.path.join(self.plugins_dir, plugin_name)
        if not os.path.isdir(plugin_path):
            return False

        json_path = os.path.join(plugin_path, "plugin.json")
        if not os.path.exists(json_path):
            logger.error(f"No plugin.json found in {plugin_name}")
            return False

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
                
            with self.lock:
                self.plugin_metadata[plugin_name] = metadata
            
            entry_file = metadata.get("entry", "plugin.py")
            entry_path = os.path.join(plugin_path, entry_file)
            
            if not os.path.exists(entry_path):
                logger.error(f"Entry file {entry_file} not found in {plugin_name}")
                return False
                
            # Unload old module to avoid caching issues during updates
            module_name = f"plugins.{plugin_name}"
            if module_name in sys.modules:
                del sys.modules[module_name]

            # Load module dynamically
            spec = importlib.util.spec_from_file_location(module_name, entry_path)
            if spec is None or spec.loader is None:
                logger.error(f"Failed to create module spec for {plugin_name}")
                return False
                
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            from app.plugin_api import PixooPluginBase
            plugin_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, PixooPluginBase) and attr is not PixooPluginBase:
                    plugin_class = attr
                    break

            if not plugin_class:
                logger.error(f"No valid PixooPluginBase subclass found in {plugin_name}")
                return False

            config = self.settings_mgr.get_plugin_settings(plugin_name)
            plugin_instance = plugin_class(self.context, config=config)
            plugin_instance.name = plugin_name
            
            with self.lock:
                self.active_plugins[plugin_name] = plugin_instance
            
            plugin_instance.setup()
            
            thread = threading.Thread(target=self._run_plugin_loop, args=(plugin_instance,), daemon=True)
            with self.lock:
                self.plugin_threads[plugin_name] = thread
            thread.start()
            
            logger.info(f"Loaded plugin: {plugin_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load plugin {plugin_name}: {e}", exc_info=True)
            return False

    def _run_plugin_loop(self, plugin_instance: Any) -> None:
        """Wrapper to catch unhandled exceptions and handle SystemExit gracefully."""
        try:
            plugin_instance.loop()
        except SystemExit as e:
            logger.info(f"Plugin thread for {plugin_instance.name} terminated gracefully: {e}")
        except Exception as e:
            logger.error(f"Plugin {plugin_instance.name} crashed in loop: {e}", exc_info=True)

    def install_from_github(self, repo_url: str) -> bool:
        """Clones a plugin from GitHub and installs it."""
        repo_name = repo_url.rstrip('/').split('/')[-1]
        if repo_name.endswith('.git'):
            repo_name = repo_name[:-4]
            
        target_dir = os.path.join(self.plugins_dir, repo_name)
        
        if os.path.exists(target_dir):
            logger.info(f"Plugin {repo_name} already exists. Pulling latest...")
            try:
                subprocess.run(["git", "-C", target_dir, "pull"], check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                logger.error(f"Git pull failed: {e.stderr.decode()}")
                return False
        else:
            try:
                subprocess.run(["git", "clone", repo_url, target_dir], check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to clone plugin from {repo_url}: {e.stderr.decode()}")
                return False
                
        self._install_dependencies(target_dir)
        return self.load_plugin(repo_name)

    def _install_dependencies(self, target_dir: str) -> None:
        """Installs pip dependencies if requirements.txt is present."""
        req_path = os.path.join(target_dir, "requirements.txt")
        if os.path.exists(req_path):
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_path], check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to install dependencies: {e.stderr.decode()}")

    def _stop_and_remove_plugin(self, repo_name: str) -> None:
        """Safely stops a plugin thread and removes it from active tracking."""
        with self.lock:
            plugin = self.active_plugins.get(repo_name)
            if plugin:
                plugin.stop()
                self.context.release_display(plugin)
                del self.active_plugins[repo_name]
            
            if repo_name in self.plugin_metadata:
                del self.plugin_metadata[repo_name]

        # Wait briefly for thread to die if possible
        thread = self.plugin_threads.get(repo_name)
        if thread and thread.is_alive():
            thread.join(timeout=1.0)

    def uninstall_plugin(self, repo_name: str) -> bool:
        """Stops and completely uninstalls a plugin from disk."""
        self._stop_and_remove_plugin(repo_name)
            
        target_dir = os.path.join(self.plugins_dir, repo_name)
        if os.path.exists(target_dir):
            try:
                def on_rm_error(func, path, exc_info):
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                shutil.rmtree(target_dir, onerror=on_rm_error)
            except Exception as e:
                logger.error(f"Failed to delete plugin directory {target_dir}: {e}")
                return False
        return True

    def update_plugin(self, repo_name: str) -> bool:
        """Pulls the latest code for an existing plugin and reloads it."""
        target_dir = os.path.join(self.plugins_dir, repo_name)
        if os.path.exists(target_dir):
            try:
                subprocess.run(["git", "-C", target_dir, "pull"], check=True, capture_output=True)
                self._install_dependencies(target_dir)
                self._stop_and_remove_plugin(repo_name)
                return self.load_plugin(repo_name)
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to update plugin {repo_name}: {e.stderr.decode()}")
                return False
            except Exception as e:
                logger.error(f"Failed to update plugin {repo_name}: {e}")
                return False
        return False
