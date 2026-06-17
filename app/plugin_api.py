import logging
from typing import List, Dict, Any

class PixooPluginBase:
    """Base class for all Pixoo plugins"""
    def __init__(self, context, config: Dict[str, Any] = None):
        self.context = context
        self.config = config or {}
        self.name = self.__class__.__name__
        self.logger = logging.getLogger(f"plugin.{self.name}")
        self.running = True

    def stop(self):
        """Called by the Hub to stop the plugin thread cleanly."""
        self.running = False

    def setup(self):
        """Called once when plugin is loaded."""
        pass

    def loop(self):
        """Called periodically in a separate thread. Implement your logic here."""
        pass

    def request_screen(self, buffer: List[int]):
        """Request the Hub to display this 64x64 buffer (RGB list of size 64*64*3=12288)."""
        self.context.request_display(self, buffer)

    def release_screen(self):
        """Release the screen so other plugins can be shown."""
        self.context.release_display(self)

    def get_pixoo_instance(self):
        """Returns a modified Pixoo instance that renders to the Hub instead of a real IP."""
        from pixoo import Pixoo
        class HubPixoo(Pixoo):
            def __init__(self, plugin, *args, **kwargs):
                self.plugin = plugin
                # Disable simulated mode to prevent Pygame simulator window from opening.
                # Mock requests to prevent network errors during initialization.
                import requests
                original_post = requests.post
                try:
                    requests.post = lambda *a, **k: type('MockResp', (object,), {'status_code': 200, 'json': lambda *args, **kwargs: {'error_code': 0, 'PicId': 1}})()
                    super().__init__("127.0.0.1", simulated=False, *args, **kwargs)
                finally:
                    requests.post = original_post
                
            def push(self):
                """Override push to send the buffer to the Hub."""
                if not self.plugin.running:
                    raise SystemExit("Plugin stopped by Marketplace")
                self.plugin.request_screen(self._Pixoo__buffer.copy())
                
            def set_screen_on(self, on):
                """Override set_screen_on to request screen state from Hub."""
                if on:
                    self.plugin.request_screen(self._Pixoo__buffer.copy())
                else:
                    self.plugin.release_screen()

        return HubPixoo(self)
