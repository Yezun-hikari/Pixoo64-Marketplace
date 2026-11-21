import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def wait_for(cond, timeout=10.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


@pytest.fixture
def fake_device():
    from fake_pixoo import serve

    server, device = serve(0)
    device.address = f"127.0.0.1:{server.server_address[1]}"
    yield device
    server.shutdown()
