"""SkyFrame desktop launcher.

Runs the Flask app on a local port and opens it in a native OS webview
window (pywebview) - an Electron-like experience at a fraction of the
footprint, with no bundled browser: Edge WebView2 on Windows, WebKitGTK
on Linux, WKWebView on macOS.

Modes:
    skyframe                  native desktop window (default)
    skyframe --server-only    headless HTTP server (CI / remote use)
    skyframe --smoke          boot, self-check /api/health, exit 0/1
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import urllib.request

from .api.server import create_app

WINDOW_TITLE = "SkyFrame 1.5 — Building Analysis Studio"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int) -> threading.Thread:
    app = create_app()
    thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, threaded=True,
                               use_reloader=False),
        daemon=True,
    )
    thread.start()
    return thread


def _wait_healthy(port: int, timeout: float = 30.0) -> bool:
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.25)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skyframe", description=__doc__)
    parser.add_argument("--server-only", action="store_true",
                        help="run the HTTP server without a window")
    parser.add_argument("--smoke", action="store_true",
                        help="boot, verify /api/health, then exit")
    parser.add_argument("--port", type=int, default=0,
                        help="port (default: 8600 for --server-only, "
                             "random for the desktop window)")
    args = parser.parse_args(argv)

    port = args.port or (8600 if args.server_only and not args.smoke
                         else _free_port())
    _start_server(port)
    if not _wait_healthy(port):
        print("SkyFrame: server failed to start", file=sys.stderr)
        if args.smoke:
            os._exit(1)
        return 1

    if args.smoke:
        print(f"SkyFrame smoke check OK on port {port}", flush=True)
        # hard exit: skip atexit hooks (the OpenSees runtime can block
        # interpreter shutdown in frozen windowed builds on Windows)
        os._exit(0)

    url = f"http://127.0.0.1:{port}/"
    if args.server_only:
        print(f"SkyFrame running at {url}  (Ctrl+C to quit)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    import webview  # deferred: GUI libs not needed for headless modes

    webview.create_window(WINDOW_TITLE, url, width=1440, height=900,
                          min_size=(1100, 700))
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
