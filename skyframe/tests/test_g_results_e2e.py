"""Wave G results in every view — browser check (opt-in: SKYFRAME_E2E=1).

Runs tests/e2e/g_results.e2e.mjs against a fresh server in ?mock=1 and live
mode with the Classic and the GPU renderer (needs playwright + chromium)."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
NODE = shutil.which("node") or ("/opt/node22/bin/node"
                                if os.path.exists("/opt/node22/bin/node")
                                else None)
pytestmark = [
    pytest.mark.skipif(NODE is None, reason="node not available"),
    pytest.mark.skipif(os.environ.get("SKYFRAME_E2E") != "1",
                       reason="browser check: set SKYFRAME_E2E=1 (needs playwright + chromium)"),
]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.mark.parametrize("mock,renderer", [("1", "classic"), ("1", "gpu"), ("0", "classic"), ("0", "gpu")])
def test_e2e_results_in_every_view(tmp_path, mock, renderer):
    port = _free_port()
    env = dict(os.environ, SKYFRAME_MODELS_DIR=str(tmp_path), OMP_NUM_THREADS="1",
               OPENBLAS_NUM_THREADS="1")
    srv = subprocess.Popen(
        [sys.executable, "-c",
         "from skyframe.api.server import create_app; "
         f"create_app().run(host='127.0.0.1', port={port}, threaded=True)"],
        cwd=os.path.join(HERE, ".."), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(150):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        res = subprocess.run(
            [NODE, os.path.join(HERE, "e2e", "g_results.e2e.mjs"),
             f"http://127.0.0.1:{port}", mock, renderer],
            capture_output=True, text=True, timeout=1500, env=env)
        assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-3000:]
    finally:
        srv.terminate()
        srv.wait(10)
