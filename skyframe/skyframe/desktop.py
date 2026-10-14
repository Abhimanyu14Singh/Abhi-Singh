"""SkyFrame desktop launcher.

Runs the Flask app on a local port and opens it in a native OS webview
window (pywebview) - an Electron-like experience at a fraction of the
footprint, with no bundled browser: Edge WebView2 on Windows, WebKitGTK
on Linux, WKWebView on macOS.

Modes:
    skyframe                  native desktop window (default)
    skyframe --server-only    headless HTTP server (CI / remote use)
    skyframe --smoke          boot, self-check /api/health, exit 0/1
    skyframe --selftest-parallel
                              run a small analysis on the spawn solver
                              worker pool, compare with serial, exit 0/1
    skyframe --selftest-pardiso   MKL PARDISO loads + solves, exit 0/1
    skyframe --selftest-windows   Wave H: main window pops out a second
                              window, checks the sync both ways, exit 0/1

Pop-out view windows (Wave H) are extra pywebview windows opened through the
``js_api`` bridge of :mod:`skyframe.desktop_windows` (pywebview routes a
page's ``window.open`` to the system browser, so it is not used); closing
the main window closes them.

If the native window can't start — most often a frozen Windows build where
pywebview's pythonnet/.NET host (``Python.Runtime.dll``) fails to initialize
under PyInstaller — the app does NOT crash: it opens in the default browser
and keeps serving. The full app is identical either way.
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

WINDOW_TITLE = "SkyFrame 1.17 — Building Analysis Studio"


# E3 — GPU 3D renderer: make sure Edge WebView2 (Windows) runs with hardware
# acceleration and prefers the high-performance (discrete) GPU. WebView2 reads
# extra Chromium switches from this environment variable when the browser
# process starts; an explicit user value is kept and only missing switches are
# appended. The Windows graphics setting (Settings › System › Display ›
# Graphics › SkyFrame.exe / msedgewebview2.exe › High performance) stays the
# authoritative way to pin the RTX GPU — see docs/PERFORMANCE.md "GPU 3D
# renderer". SKYFRAME_NO_GPU_FLAGS=1 disables this.
WEBVIEW2_ARGS_ENV = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"
WEBVIEW2_GPU_SWITCHES = ("--ignore-gpu-blocklist", "--enable-gpu-rasterization",
                         "--force_high_performance_gpu")


def webview2_gpu_env(env: dict | None = None,
                     platform: str | None = None) -> dict:
    """Environment updates that keep WebGL2 hardware-accelerated in WebView2.

    Returns ``{}`` off Windows, when ``SKYFRAME_NO_GPU_FLAGS`` is set, or when
    every switch is already present; never removes a user-supplied switch and
    never adds ``--disable-gpu`` style flags."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if not platform.startswith("win") or env.get("SKYFRAME_NO_GPU_FLAGS"):
        return {}
    cur = env.get(WEBVIEW2_ARGS_ENV, "").strip()
    have = set(cur.split())
    add = [sw for sw in WEBVIEW2_GPU_SWITCHES if sw not in have]
    if not add:
        return {}
    return {WEBVIEW2_ARGS_ENV: " ".join(([cur] if cur else []) + add)}


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


def _window_diagnostics() -> dict:
    """Facts that explain a pythonnet / native-window failure on Windows."""
    info: dict = {}
    if sys.platform != "win32":
        return info
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    dll = os.path.join(base, "pythonnet", "runtime", "Python.Runtime.dll")
    info["python_runtime_dll"] = os.path.isfile(dll)
    try:
        with open(dll + ":Zone.Identifier", encoding="utf-8",
                  errors="replace") as fh:
            info["zone_identifier"] = fh.read().strip().replace("\r", "")[:80]
    except OSError:
        info["zone_identifier"] = None
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full")
        info["netfx_release"] = winreg.QueryValueEx(k, "Release")[0]
    except Exception:  # noqa: BLE001
        info["netfx_release"] = None
    info["exe_config"] = os.path.isfile(sys.executable + ".config")
    return info


def find_app_browser() -> str | None:
    """Microsoft Edge (or Chrome / Chromium) for a chromeless app window."""
    import shutil
    cands = []
    if sys.platform == "win32":
        for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                     os.environ.get("LOCALAPPDATA")):
            if root:
                cands += [os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"),
                          os.path.join(root, "Google", "Chrome", "Application", "chrome.exe")]
    for c in cands:
        if os.path.isfile(c):
            return c
    for name in ("msedge", "microsoft-edge", "google-chrome", "chromium",
                 "chromium-browser", "chrome"):
        w = shutil.which(name)
        if w:
            return w
    return None


def open_app_window(url: str):
    """Open SkyFrame in its own Edge/Chrome *app* window (no tabs or address
    bar, GPU-accelerated, pop-outs via window.open) with a dedicated profile,
    so the process lives exactly as long as SkyFrame's windows.  Returns the
    Popen, or None when no such browser is installed."""
    import subprocess
    exe = find_app_browser()
    if not exe:
        return None
    base = (os.environ.get("LOCALAPPDATA") if sys.platform == "win32"
            else os.path.expanduser("~/.local/share")) or os.path.expanduser("~")
    prof = os.path.join(base, "SkyFrame", "app-window")
    os.makedirs(prof, exist_ok=True)
    args = [exe, f"--app={url}", f"--user-data-dir={prof}", "--no-first-run",
            "--no-default-browser-check", "--window-size=1440,900",
            "--force_high_performance_gpu"]
    try:
        return subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return None


def unblock_bundle(root: str | None = None) -> int:
    """Remove the Windows "downloaded from the internet" mark (the NTFS
    ``Zone.Identifier`` stream) from the bundled .NET / native libraries.

    A ZIP downloaded from GitHub marks every extracted file; .NET Framework
    then refuses to load pythonnet's ``Python.Runtime.dll`` ("Failed to
    resolve Python.Runtime.Loader.Initialize") and the native window falls
    back to the browser.  Only acts in a frozen Windows build; returns the
    number of files unblocked.  Never raises."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return 0
    base = root or getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    n = 0
    for sub in ("pythonnet", "clr_loader", "webview", ""):
        top = os.path.join(base, sub) if sub else base
        if not os.path.isdir(top):
            continue
        walker = os.walk(top) if sub else [(top, [], os.listdir(top))]
        for dirpath, _dirs, files in walker:
            for f in files:
                if not f.lower().endswith((".dll", ".exe", ".pyd")):
                    continue
                try:
                    os.remove(os.path.join(dirpath, f) + ":Zone.Identifier")
                    n += 1
                except OSError:
                    pass
    return n


def main(argv: list[str] | None = None) -> int:
    # E1: spawned solver workers (engine/spawn_pool.py) re-enter a FROZEN
    # executable; this must run before any GUI / server start.  No-op
    # when not frozen (packaging/launch.py calls it even earlier).
    import multiprocessing
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(prog="skyframe", description=__doc__)
    parser.add_argument("--server-only", action="store_true",
                        help="run the HTTP server without a window")
    parser.add_argument("--smoke", action="store_true",
                        help="boot, verify /api/health, then exit")
    parser.add_argument("--selftest-pardiso", action="store_true",
                        help="E2: check that MKL PARDISO loads and solves "
                             "a frame equal to OpenSees, then exit 0/1")
    parser.add_argument("--port", type=int, default=0,
                        help="port (default: 8600 for --server-only, "
                             "random for the desktop window)")
    parser.add_argument("--selftest-parallel", action="store_true",
                        help="solve a small model on the spawn worker "
                             "pool, compare with serial, exit 0/1")
    parser.add_argument("--selftest-windows", action="store_true",
                        help="Wave H: open a pop-out window, check the "
                             "main <-> pop-out sync, close both, exit 0/1")
    args = parser.parse_args(argv)
    if args.selftest_pardiso:                  # E2 frozen smoke check
        import json as _json
        from skyframe.engine import pardiso_solver as _pds
        rep = _pds.selftest()
        print("SkyFrame PARDISO selftest: " + _json.dumps(rep), flush=True)
        os._exit(0 if rep.get("ok") else 1)

    if args.selftest_parallel:              # E1: no server, no window
        from .engine.parallel_selftest import main as _selftest
        code = _selftest()
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                stream.flush()
        if getattr(sys, "frozen", False):
            os._exit(code)                  # as --smoke (frozen shutdown)
        return code

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
    print(f"SkyFrame running at {url} (Grasshopper SkyFrame Connect: "
          f"use this address)", flush=True)
    if args.server_only:
        _serve_forever(url)
        return 0

    # Default: try the native desktop window (pywebview). If the GUI stack is
    # unavailable — most commonly a frozen Windows build where pythonnet
    # (clr / Python.Runtime.dll) fails to initialize under PyInstaller — fall
    # back to opening the app in the default browser instead of crashing.
    try:
        os.environ.update(webview2_gpu_env())   # E3: WebGL2 on the GPU (Windows)
        unblock_bundle()                        # downloaded-ZIP mark blocks pythonnet
        import webview  # deferred: GUI libs not needed for headless modes
        # Wave H: pop-out view windows (skyframe/desktop_windows.py) — the
        # page opens extra pywebview windows through this js_api bridge
        from .desktop_windows import DesktopWindows, attach_main
        wins = DesktopWindows(webview, url, WINDOW_TITLE)
        main_url = url + ("?mock=1&hselftest=1" if args.selftest_windows else "")
        main_win = webview.create_window(WINDOW_TITLE, main_url, width=1440,
                                         height=900, min_size=(1100, 700),
                                         js_api=wins)
        attach_main(main_win, wins)
        if args.selftest_windows:
            return _selftest_windows(webview, wins, main_win)
        webview.start()
        return 0
    except Exception as exc:  # noqa: BLE001 — degrade gracefully, never crash
        import json as _json
        diag = _window_diagnostics()
        if args.selftest_windows:
            print(f"SkyFrame windows selftest: native windows unavailable "
                  f"({exc.__class__.__name__}: {exc})", flush=True)
            print("SkyFrame windows diagnostics: " + _json.dumps(
                dict(diag, app_window_browser=find_app_browser())), flush=True)
            os._exit(1)
        print(f"SkyFrame: native window unavailable "
              f"({exc.__class__.__name__}: {exc}); diagnostics "
              f"{_json.dumps(diag)}", file=sys.stderr)
        # second choice: SkyFrame in its own Edge / Chrome app window
        proc = open_app_window(url)
        if proc is not None:
            print("SkyFrame: opened in its own app window (Microsoft Edge "
                  "app mode).", flush=True)
            t0 = time.time()
            proc.wait()
            if time.time() - t0 > 5.0:      # the user closed the app window
                os._exit(0)
            # the launch handed over to an already-running app window
            # (same profile): keep serving until Ctrl+C / console closed
            _serve_forever(url)
            return 0
        print("Opening in your default browser instead.", file=sys.stderr)
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
        _serve_forever(url)
        return 0


def _selftest_windows(webview, wins, main_win, timeout: float = 90.0) -> int:
    """Wave H frozen check: the page (``?hselftest=1``) pops out a window,
    verifies the sync both ways and reports via ``selftest_result``."""
    import json as _json
    box = {"code": 1}

    def watch():
        rep = wins.wait_selftest(timeout)
        ok = bool(rep and rep.get("ok"))
        print("SkyFrame windows selftest: " + _json.dumps(
            rep or {"ok": False, "error": f"timeout after {timeout:.0f} s"}),
            flush=True)
        box["code"] = 0 if ok else 1
        try:
            wins.close_all()
            main_win.destroy()
        except Exception:  # noqa: BLE001
            pass
        os._exit(box["code"])

    threading.Thread(target=watch, daemon=True).start()
    webview.start()
    os._exit(box["code"])
    return box["code"]


def _serve_forever(url: str) -> None:
    """Print the local URL and block until interrupted (headless / fallback)."""
    print(f"SkyFrame running at {url}\n"
          f"Leave this running and use the browser tab; Ctrl+C to quit.",
          flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
