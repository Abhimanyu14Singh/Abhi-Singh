"""Wave H windows / pop-outs / compare view — browser check
(tests/e2e/h_windows.e2e.mjs, Classic + GPU renderer) — opt-in.

Runs the Playwright script against a fresh server in ?mock=1 and live mode
with both 3D renderers. Needs node + the Playwright module + Chromium, so it
only runs when SKYFRAME_E2E=1 (the full suite stays browser-free and fast).
"""

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "e2e", "h_windows.e2e.mjs")
NODE = shutil.which("node") or ("/opt/node22/bin/node"
                                if os.path.exists("/opt/node22/bin/node")
                                else None)

pytestmark = pytest.mark.skipif(
    os.environ.get("SKYFRAME_E2E") != "1" or NODE is None,
    reason="browser e2e: set SKYFRAME_E2E=1 (needs node + Playwright)")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = _free_port()
    env = dict(os.environ, SKYFRAME_MODELS_DIR=str(tmp_path_factory.mktemp("m")),
               SKYFRAME_HISTORY_DIR=str(tmp_path_factory.mktemp("h")),
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    code = ("from skyframe.api.server import create_app; "
            f"create_app().run(host='127.0.0.1', port={port}, threaded=True)")
    proc = subprocess.Popen([sys.executable, "-c", code], env=env,
                            cwd=os.path.dirname(HERE),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            urllib.request.urlopen(url + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.25)
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.mark.parametrize("mode,renderer", [("mock", "classic"), ("mock", "gpu"),
                                           ("live", "classic"), ("live", "gpu")])
def test_windows_in_browser(server, mode, renderer):
    res = subprocess.run([NODE, SCRIPT, server, mode, renderer], capture_output=True,
                         text=True, timeout=1500)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ALL PASSED" in res.stdout
