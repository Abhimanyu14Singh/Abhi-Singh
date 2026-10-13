"""E3 — GPU (WebGL2) 3D renderer: static guarantees + desktop GPU switches.

The renderer itself is browser code (js/gpu/*.js); its behaviour (picking
equality with Classic, screenshot parity, frame times) is verified with
Playwright + SwiftShader and recorded in docs/PERFORMANCE.md. These tests pin
what must never regress silently: offline vendoring (no CDN), the pinned
three.js build, the Viewer3D hook lines, packaging globs, the default renderer
(Classic) and the WebView2 GPU switches of the desktop shell.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from skyframe.desktop import (WEBVIEW2_ARGS_ENV, WEBVIEW2_GPU_SWITCHES,
                              webview2_gpu_env)

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "skyframe" / "api" / "web"
THREE = WEB / "vendor" / "three" / "0.170.0" / "three.module.min.js"
THREE_SHA256 = "08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7"
GPU_JS = sorted((WEB / "js" / "gpu").glob("*.js"))


def test_three_is_vendored_and_pinned():
    assert THREE.is_file()
    assert hashlib.sha256(THREE.read_bytes()).hexdigest() == THREE_SHA256
    assert 'const t="170"' in THREE.read_text(encoding="utf-8")
    lic = (THREE.parent / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in lic and "three.js authors" in lic


def test_gpu_modules_load_only_local_code():
    names = {p.name for p in GPU_JS}
    assert {"gpuhook.js", "renderer.js", "glprims.js"} <= names
    for p in GPU_JS:
        src = p.read_text(encoding="utf-8")
        assert not re.search(r"https?://", src.split("*/", 1)[-1]), f"{p.name}: remote URL"
        for spec in re.findall(r'(?:from|import\()\s*"([^"]+)"', src):
            assert spec.startswith("."), f"{p.name}: bare import {spec}"
            assert (p.parent / spec).resolve().is_file(), f"{p.name}: {spec} missing"
    assert "../../vendor/three/0.170.0/three.module.min.js" in (WEB / "js" / "gpu" / "glprims.js").read_text(encoding="utf-8")


def test_viewer_hooks_and_classic_default():
    v = (WEB / "js" / "viewer3d.js").read_text(encoding="utf-8")
    assert 'import { gpuOnCreate as g3OnCreate } from "./gpu/gpuhook.js";' in v
    assert "g3OnCreate(this)" in v
    assert "const G = !!(this.gpu && this.gpu.on);" in v
    for m in ("_pickAt(mx, my, thr)", "_pickBox(x0, y0, x1, y1, crossing)", "export function boxHitSeg"):
        assert m in v
    hook = (WEB / "js" / "gpu" / "gpuhook.js").read_text(encoding="utf-8")
    # default = Classic until parity is proven; the choice is a per-browser view preference
    assert 'return v === "gpu" ? "gpu" : "classic";' in hook
    assert "localStorage" in hook and "try {" in hook
    assert "WEBGL_debug_renderer_info" in hook
    app = (WEB / "js" / "app.js").read_text(encoding="utf-8")
    assert "g3InitGpuRenderer(window.__sky)" in app
    et = (WEB / "js" / "etabs.js").read_text(encoding="utf-8")
    assert '{ label: "Renderer", aliases: ["3D Renderer"], sub: [] }' in et


def test_packaging_ships_gpu_assets():
    pp = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"web/js/gpu/*"' in pp and '"web/vendor/*/*/*"' in pp


def test_webview2_gpu_switches():
    assert webview2_gpu_env({}, "linux") == {}
    assert webview2_gpu_env({}, "darwin") == {}
    full = webview2_gpu_env({}, "win32")
    assert full == {WEBVIEW2_ARGS_ENV: " ".join(WEBVIEW2_GPU_SWITCHES)}
    assert "--disable-gpu" not in full[WEBVIEW2_ARGS_ENV]
    # a user value is kept, only missing switches are appended
    cur = {WEBVIEW2_ARGS_ENV: "--lang=de --ignore-gpu-blocklist"}
    out = webview2_gpu_env(cur, "win32")[WEBVIEW2_ARGS_ENV]
    assert out.startswith("--lang=de --ignore-gpu-blocklist ")
    assert out.split().count("--ignore-gpu-blocklist") == 1
    assert all(sw in out.split() for sw in WEBVIEW2_GPU_SWITCHES)
    # nothing to add / opt-out
    assert webview2_gpu_env({WEBVIEW2_ARGS_ENV: " ".join(WEBVIEW2_GPU_SWITCHES)}, "win32") == {}
    assert webview2_gpu_env({"SKYFRAME_NO_GPU_FLAGS": "1"}, "win32") == {}
