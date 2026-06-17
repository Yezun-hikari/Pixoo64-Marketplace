import time
import logging
import threading
import requests
import base64
import socket
from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from pixoo import Pixoo

logger = logging.getLogger(__name__)

def pixoo_post(url: str, payload: dict, timeout: float = 2.0) -> dict:
    """Helper to send POST requests to the Pixoo device."""
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        if response.status_code == 200:
            try:
                data = response.json()
                if 'errors' in data:
                    return {"error_code": -1, "details": data}
                return data
            except ValueError:
                return {"error_code": 0}
        return {"error_code": -1, "status": response.status_code}
    except requests.RequestException as e:
        logger.warning(f"Pixoo POST RequestException to {url}: {e}")
        return {"error_code": -1, "details": str(e)}

class RobustPixoo(Pixoo):
    """A wrapper around the Pixoo library that prevents crashes on network drops."""
    
    def get_all_device_configurations(self) -> dict:
        try:
            return pixoo_post(self._Pixoo__url, {'Command': 'Channel/GetAllConf'})
        except Exception as e:
            logger.warning(f"Failed to get configurations: {e}")
            return {"error_code": 0}

    def _Pixoo__load_counter(self) -> None:
        try:
            data = pixoo_post(self._Pixoo__url, {'Command': 'Draw/GetHttpGifId'})
            if data.get('error_code') == 0 and 'PicId' in data:
                self._Pixoo__counter = int(data['PicId'])
                return
        except Exception as e:
            logger.warning(f"Failed to load counter: {e}")
        self._Pixoo__counter = 1

    def _Pixoo__send_buffer(self) -> None:
        self._Pixoo__counter += 1
        if self.refresh_connection_automatically and self._Pixoo__counter >= self._Pixoo__refresh_counter_limit:
            self._Pixoo__reset_counter()
            self._Pixoo__counter = 1

        if self.simulated: 
            return

        try:
            pic_data = base64.b64encode(bytearray(self._Pixoo__buffer)).decode('ascii')
            payload = {
                'Command': 'Draw/SendHttpGif',
                'PicNum': 1,
                'PicWidth': self.size,
                'PicOffset': 0,
                'PicID': self._Pixoo__counter,
                'PicSpeed': 1000,
                'PicData': pic_data
            }
            pixoo_post(self._Pixoo__url, payload)
        except Exception as e:
            logger.error(f"Pixoo Push Error: {e}")

    def _Pixoo__reset_counter(self) -> None:
        if self.simulated: 
            return
        try:
            pixoo_post(self._Pixoo__url, {'Command': 'Draw/ResetHttpGifId'})
        except Exception as e:
            logger.warning(f"Failed to reset counter: {e}")

    def set_channel(self, channel: int) -> None:
        if self.simulated: 
            return
        try:
            pixoo_post(self._Pixoo__url, {'Command': 'Channel/SetIndex', 'SelectIndex': int(channel)})
        except Exception as e:
            logger.warning(f"Failed to set channel {channel}: {e}")

    def set_screen_on(self, on: bool) -> None:
        if self.simulated: 
            return
        try:
            pixoo_post(self._Pixoo__url, {'Command': 'Channel/OnOffScreen', 'OnOff': 1 if on else 0})
        except Exception as e:
            logger.warning(f"Failed to set screen on state to {on}: {e}")

def get_my_ip() -> str:
    """Find the local machine IP."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "192.168.1.100" # Fallback
    finally:
        s.close()
    return ip

def check_ip(ip: str) -> Optional[str]:
    """Test if an IP is a Pixoo device."""
    try:
        r = requests.post(f"http://{ip}/post", json={"Command": "Device/GetDeviceTime"}, timeout=0.5)
        if r.status_code == 200 and 'error_code' in r.json():
            return ip
    except requests.RequestException:
        pass
    return None

def find_pixoo() -> Optional[str]:
    """Scans the local subnet for a Pixoo device."""
    try:
        base_ip = ".".join(get_my_ip().split('.')[:-1])
        ips_to_test = [f"{base_ip}.{i}" for i in range(1, 255)]
        
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = {executor.submit(check_ip, ip): ip for ip in ips_to_test}
            for future in as_completed(futures):
                res = future.result()
                if res:
                    for f in futures: 
                        f.cancel()
                    return res
    except Exception as e:
        logger.error(f"Auto-Discovery Error: {e}")
    return None

class DisplayManager:
    """Manages arbitration of screen access among plugins based on priority or rotation."""
    def __init__(self):
        self.pixoo_ip: Optional[str] = None
        self.pixoo: Optional[RobustPixoo] = None
        self.mode: str = "priority" # "priority" or "rotation"
        self.plugin_priorities: Dict[str, int] = {} 
        
        self.active_requests: Dict[str, dict] = {}
        self.lock = threading.Lock()
        
        self.current_plugin: Optional[str] = None
        self.screen_on: bool = False
        self.rotation_index: int = 0
        
        # Start rotation thread
        self.running: bool = True
        self.rotation_thread = threading.Thread(target=self._rotation_loop, daemon=True)
        self.rotation_thread.start()

    def connect(self, ip: Optional[str] = None) -> bool:
        """Connects to a Pixoo device, discovering it automatically if needed."""
        if not ip:
            logger.info("No IP provided. Starting Auto-Discovery...")
            ip = find_pixoo()
            if not ip:
                logger.error("No Pixoo display found on the network.")
                return False
            logger.info(f"Pixoo display found at: {ip}")

        self.pixoo_ip = ip
        try:
            self.pixoo = RobustPixoo(ip)
            self.pixoo.set_channel(3)
            self.pixoo.set_screen_on(False)
            self.screen_on = False
            logger.info(f"Connected to Pixoo at {ip}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Pixoo at {ip}: {e}")
            self.pixoo = None
            return False

    def request_display(self, plugin: Any, buffer: List[int]) -> None:
        """Called by a plugin to request drawing to the screen."""
        with self.lock:
            self.active_requests[plugin.name] = {
                "buffer": buffer,
                "timestamp": time.time(),
                "plugin": plugin
            }
        self._update_display()

    def release_display(self, plugin: Any) -> None:
        """Called by a plugin when it no longer needs the screen."""
        with self.lock:
            if plugin.name in self.active_requests:
                del self.active_requests[plugin.name]
        self._update_display()

    def set_mode(self, mode: str) -> None:
        """Sets display arbitration mode."""
        self.mode = mode
        self._update_display()

    def set_priorities(self, priorities: Dict[str, int]) -> None:
        """Sets the priority map for plugins."""
        self.plugin_priorities = priorities
        self._update_display()

    def _rotation_loop(self) -> None:
        """Background thread rotating display if in rotation mode."""
        while self.running:
            time.sleep(10) # 10 second rotation interval
            if self.mode == "rotation" and self.running:
                self._update_display(rotate=True)

    def _update_display(self, rotate: bool = False) -> None:
        """Calculates which plugin gets screen access and pushes the buffer."""
        if not self.pixoo:
            return

        with self.lock:
            if not self.active_requests:
                if self.screen_on:
                    try:
                        self.pixoo.set_screen_on(False)
                        self.screen_on = False
                    except Exception as e:
                        logger.warning(f"Failed to turn off screen: {e}")
                self.current_plugin = None
                return

            winner = None
            if self.mode == "priority":
                def get_prio(name):
                    return self.plugin_priorities.get(name, 0)
                winner_name = max(self.active_requests.keys(), key=get_prio)
                winner = self.active_requests[winner_name]
            else:
                # Rotation logic
                keys = list(self.active_requests.keys())
                if rotate or self.current_plugin not in keys:
                    self.rotation_index = (self.rotation_index + 1) % len(keys)
                else:
                    self.rotation_index = keys.index(self.current_plugin)
                
                winner_name = keys[self.rotation_index % len(keys)]
                winner = self.active_requests[winner_name]

            if winner:
                self.current_plugin = winner["plugin"].name
                try:
                    self.pixoo._Pixoo__buffer = winner["buffer"]
                    self.pixoo.push()
                    if not self.screen_on:
                        self.pixoo.set_screen_on(True)
                        self.screen_on = True
                except SystemExit:
                    # Reraise SystemExit if triggered by thread killer
                    raise
                except Exception as e:
                    logger.error(f"Failed to push buffer to display: {e}")
