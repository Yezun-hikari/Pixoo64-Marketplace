# Pixoo Hub

Runs small plugins that want to show something on a **Divoom Pixoo 64** and decides
which one gets the screen. When none of them has anything to show, the display turns off.

- Plugins from any GitHub repository, installed and updated from the web UI
- Each plugin runs in its own process: a crashing or hanging plugin can't take the hub down
- Priority or rotation when several plugins want the screen at once
- Quiet hours, brightness, idle behaviour (off or Divoom clock)
- Live preview of the display in the browser, also without a device
- Two plugins to start with: [Spotify](https://github.com/Yezun-hikari/Spotify-Plugin)
  (cover art, stays dark when you play on your phone or tablet) and
  [Steam](https://github.com/Yezun-hikari/Steam-Plugin) (pixel art for the game you're playing)

The web UI is in German.

## Installation

### Docker (recommended)

```bash
mkdir pixoo-hub && cd pixoo-hub
curl -O https://raw.githubusercontent.com/Yezun-hikari/Pixoo64-Marketplace/main/docker-compose.yml
docker compose up -d
```

Open `http://<host>:8000`. Everything the hub stores lives in `./data`.

Image tags: `latest` and version tags are releases, `dev` follows the `main` branch.

**Finding the Pixoo.** Leave the IP empty and the hub asks Divoom's cloud for devices on
your network, then scans the local /24. Inside Docker's default bridge network the scan
can't see your LAN; either use `network_mode: host` (see the comment in
`docker-compose.yml`) or enter the IP yourself — the Divoom app shows it under device
information.

### Without Docker

Python 3.10+ and git are required.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m pixoohub            # --port 8000, data goes to ./data
```

### Upgrading from 1.x

Version 2 changes the plugin interface; plugins written for 1.x do not run anymore and
are marked as incompatible in the catalog. To keep your settings, copy your old
`settings.json` to `data/settings.json` before the first start: the Pixoo IP, the display
mode and the plugin credentials are taken over once. The old `plugins/` folder and
`repositories.json` are no longer used; install the plugins again from the catalog.

## Using it

- **Plugins** — the order of the list is the priority. Click a plugin to change its
  settings, see its log, update or remove it.
- **Katalog** — add a GitHub URL of a plugin repository, then install it.
- **Gerät & Anzeige** — device IP, brightness, priority vs. rotation, what happens when
  nothing wants the screen, quiet hours.

## Writing a plugin

A plugin is a git repository with three files:

```
plugin.json        metadata and settings form
plugin.py          the code
icon.png           square, 128×128 is plenty
requirements.txt   optional
```

### plugin.json

```json
{
  "id": "clock",
  "name": "Clock",
  "version": "1.0.0",
  "description": "A clock.",
  "author": "you",
  "entry": "plugin.py",
  "hub_api": 2,
  "settings": [
    { "type": "header", "label": "Look" },
    { "name": "color", "label": "Color", "type": "select", "options": ["red", "green"], "default": "red" },
    { "name": "seconds", "label": "Show seconds", "type": "switch", "default": false }
  ]
}
```

`id` must be lowercase (`a-z 0-9 - _`) and stay the same forever; it names the plugin's
directories and settings. `hub_api` must be `2`.

Settings field types: `header`, `note`, `text`, `password`, `textarea`, `number`
(`min`/`max`), `slider` (`min`/`max`/`step`), `switch`, `select` and `multiselect`
(`options`: strings or `{"value", "label"}`), and `action` — a button that calls
`on_action()` in the running plugin with the current form values. Every field can have
`info` (help text below it) and `placeholder`. `password` values are never sent back to the
browser.

### plugin.py

```python
import time
from pixoohub.sdk import Plugin, ActionResult


class Clock(Plugin):
    interval = 1.0                 # seconds between tick() calls

    def setup(self):
        # self.config: saved settings. Raise here if they're unusable;
        # the error is shown in the UI and the plugin isn't restarted.
        self.color = (255, 0, 0) if self.cfg("color") == "red" else (0, 255, 0)

    def tick(self):
        img = self.canvas()        # 64×64 black PIL image
        self.text(img, time.strftime("%H:%M"), (22, 29), self.color)
        self.show(img)             # claim the screen
        # self.release()           # ...or give it back when there's nothing to show
        # return 10                # optional: seconds until the next tick

    def on_action(self, name, values):
        return ActionResult("Done.")
```

What a plugin can use:

| | |
|---|---|
| `self.show(img_or_list, speed_ms=100)` | Claim the screen with a PIL image or an animation (max. 40 frames). Other sizes are scaled. Sending the same frame again costs nothing. |
| `self.release()` | Nothing to show. If no other plugin wants the screen, it turns off. |
| `self.status(text, level)` | One line shown in the UI. `level`: `info`, `warn`, `error`. |
| `self.config`, `self.cfg(key, default, cast)` | Saved settings. Changing them restarts the plugin. |
| `self.storage` | Small persistent key/value store (`get`, `set`, `delete`) for tokens, caches, … |
| `self.data_dir` | A private directory that survives updates. |
| `self.log` | A logger; output appears in the plugin's log in the UI. |
| `self.sleep(s)`, `self.stopping` | For long-running work inside `tick()`. |
| `self.text(img, text, xy, color)` | 3×5 pixel font, one LED per pixel. |

An exception in `tick()` is logged, shown as status and retried with backoff. The process
is restarted if it dies. `on_action()` runs in a separate thread.

Dependencies from `requirements.txt` are installed into the hub's data directory (packages
the hub already ships — Pillow, requests, pycryptodome — are not installed again).

### How it works

The hub starts every plugin as `python -m pixoohub.sdk.runner` and talks to it with
newline-delimited JSON over stdin/stdout (see `pixoohub/sdk/runner.py`). Plugins only ever
send frames to the hub; a single thread in the hub owns the device, picks the winning
plugin, limits the push rate and dedupes identical frames.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
python tests/fake_pixoo.py --port 8090 --dump out/   # a fake Pixoo; set the IP to 127.0.0.1:8090
```
