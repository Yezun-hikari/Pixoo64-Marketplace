<div align="center">
  <img src="https://raw.githubusercontent.com/Yezun-hikari/Pixoo64-Marketplace/main/ui/default-icon.png" width="120" alt="Pixoo Hub Logo" />
  <h1>Pixoo Hub Marketplace</h1>
  <p>A modern, extensible marketplace and display manager for your Divoom Pixoo 64.</p>

  <p>
    <a href="#features">Features</a> •
    <a href="#installation">Installation</a> •
    <a href="#usage">Usage</a> •
    <a href="#creating-a-plugin">Creating a Plugin</a>
  </p>
</div>

---

## <a id="features"></a>🌟 Features

- **Modern Glassmorphism UI:** A beautiful, dynamic Single Page Application (SPA) dashboard.
- **Plugin Marketplace:** Add GitHub repositories and browse available plugins seamlessly.
- **Priority & Rotation Display:** Choose how your plugins share the Pixoo 64 screen.
- **Over-The-Air Updates:** One-click plugin installations and updates directly from GitHub.
- **Zero Configuration:** Auto-discovery of your Pixoo 64 device on the local network.

---

## <a id="installation"></a>🚀 Installation

The recommended way to run Pixoo Hub is via **Docker**.

### Using Docker Compose (Recommended)

1. Clone this repository:
   ```bash
   git clone https://github.com/Yezun-hikari/Pixoo64-Marketplace.git
   cd Pixoo64-Marketplace
   ```

2. Start the container:
   ```bash
   docker-compose up -d
   ```

3. Open your browser and navigate to `http://localhost:8000`.

### Running Locally without Docker

If you prefer to run it locally (e.g., for development):

```powershell
# Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install requirements
pip install -r requirements.txt

# Start the application
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
*(A helper script `start_pseudo_container.ps1` is also available for Windows users).*

---

## <a id="usage"></a>💻 Usage

### 1. Dashboard Settings
When you first open the dashboard, navigate to the **Settings** tab.
- **Pixoo IP Address:** Leave this blank to automatically discover your Pixoo 64 on the network, or enter the IP manually if you are on a different subnet.
- **Display Mode:** 
  - `Priority Based`: Only the plugin with the highest priority is displayed.
  - `Rotation Based`: Rotates through all active plugins every 10 seconds.

### 2. Managing the Catalog
1. Go to the **Catalog** tab.
2. Click **Repository +** to add a new plugin repository URL (e.g., `https://github.com/user/pixoo-plugin`).
3. The plugin will appear in the catalog. You can view its version and description.
4. Click **Install** to download it. It will automatically load and appear in your Sidebar.

---

## <a id="creating-a-plugin"></a>🛠 Creating a Plugin

Creating your own plugin is extremely easy. A Pixoo Hub plugin is simply a GitHub repository containing a `plugin.json`, an `icon.png`, and a Python script.

### 1. Directory Structure
Your GitHub repository should look like this:
```
my-pixoo-plugin/
├── plugin.json
├── plugin.py
├── icon.png
└── requirements.txt  (Optional)
```

### 2. The `plugin.json`
This file provides metadata for the marketplace UI and can also define a dynamic settings schema. If you define a schema, the Pixoo Hub will automatically generate a clean UI form for your plugin and pass the saved values back to your code.

```json
{
  "name": "My Awesome Plugin",
  "version": "1.0.0",
  "description": "Displays something awesome on the Pixoo 64.",
  "entry": "plugin.py",
  "settings_schema": [
    { "type": "header", "label": "API Configuration" },
    { "name": "api_key", "label": "API Key", "type": "text", "info": "Your secret API key" },
    { "type": "header", "label": "Display Options" },
    { "name": "speed", "label": "Animation Speed", "type": "number", "default": 60, "info": "Frames per second" }
  ]
}
```
**Supported Schema Types:**
- **Text & Input:** `text`, `password`, `number`
- **UI Elements:** `header` (for section titles)
- **Controls:**
  - `switch` or `checkbox`: A toggle switch (returns true/false)
  - `slider` or `range`: A range slider. You can optionally specify `min`, `max`, and `step` fields.
  - `select`: A dropdown menu. You must provide an `options` array (e.g., `["Red", "Blue"]` or `[{"label": "High", "value": "high"}, ...]`).
  - `button` or `link_button`: A clickable button that opens a URL in a new tab. Must provide `url` and `button_text` fields.

*Tip: Use the `info` field on any setting to add a helpful hover tooltip.*

### 3. The `icon.png`
Provide a square image (preferably 128x128 or 256x256) named exactly `icon.png`. This will be shown in the marketplace catalog and the sidebar.

### 4. The Python Code (`plugin.py`)
Your plugin must contain a class that inherits from `PixooPluginBase`. 

```python
import time
from app.plugin_api import PixooPluginBase

class MyAwesomePlugin(PixooPluginBase):
    def setup(self):
        """Called once when the plugin is installed/loaded."""
        # You can access the saved settings from the UI via self.config
        api_key = self.config.get("api_key", "")
        self.logger.info(f"Setting up plugin with API key: {api_key}")

    def loop(self):
        """Runs continuously in the background."""
        pixoo = self.get_pixoo_instance()
        speed = float(self.config.get("speed", 60))
        
        while True:
            # 1. Clear the screen
            pixoo.fill((0, 0, 0))
            
            # 2. Draw something
            pixoo.draw_text("Hello", (10, 20), (0, 255, 0))
            
            # 3. Request the Hub to display your buffer
            pixoo.push()
            
            # 4. Wait before updating again
            time.sleep(5)
```

### 5. Publishing
Push your code to GitHub. Then, in your Pixoo Hub dashboard, click **Repository +** and paste your GitHub URL. Hit **Install** and enjoy!

---

<div align="center">
  <p>Built with ❤️ for Pixoo enthusiasts.</p>
</div>
