/* SkyFrame E3 — 3D renderer switch (light module, imported by js/viewer3d.js).

   View › Renderer ▸  GPU (WebGL2) / Classic (Canvas 2D) / Graphics Adapter
   The choice is a per-browser view preference (localStorage, guarded): it is
   never written to the model. Default = Classic. `?renderer=gpu|classic` in
   the URL overrides it for one page load (tests / support).

   GPU mode lazily imports js/gpu/renderer.js (and the vendored three.js r170
   in vendor/three/0.170.0/ — no CDN, works offline in the desktop app) and
   attaches a GpuBackend to every Viewer3D (main 3D view + View › Windows
   panes). The Viewer3D public API is unchanged; switching back detaches it.
   Without WebGL2 the viewer stays Classic and a toast says why (no console
   noise). When the WebGL adapter string names an integrated / software GPU a
   one-time hint explains how to force the discrete GPU on Windows. */

const G3_KEY = "skyframe.renderer3d.v1";
const G3_HINT_KEY = "skyframe.gpuhint.v1";
const g3Viewers = new Set();
let g3Mode = null;          // "gpu" | "classic"
let g3Probe = null;         // {webgl2, renderer, vendor, integrated, software}
let g3Mod = null;           // the lazily imported renderer module (promise)
let g3Sky = null;
const g3Pending = new Set();

function g3Read() {
  try {
    const q = new URLSearchParams(location.search).get("renderer");
    if (q === "gpu" || q === "classic") return q;
  } catch (e) { /* no location */ }
  try { const v = localStorage.getItem(G3_KEY); return v === "gpu" ? "gpu" : "classic"; } catch (e) { return "classic"; }
}
function g3Write(m) { try { localStorage.setItem(G3_KEY, m); } catch (e) { /* private mode */ } }
const g3Toast = (t, m, k = "info", ms = 6000) => { const s = g3Sky || window.__sky; if (s && s.toast) s.toast(t, m, k, ms); };

/** WebGL2 availability + adapter string (WEBGL_debug_renderer_info when exposed). */
export function g3ProbeGL() {
  if (g3Probe) return g3Probe;
  const out = { webgl2: false, renderer: "", vendor: "", integrated: false, software: false };
  try {
    const c = document.createElement("canvas");
    const gl = c.getContext("webgl2", { powerPreference: "high-performance" });
    if (gl) {
      out.webgl2 = true;
      const dbg = gl.getExtension("WEBGL_debug_renderer_info");
      out.renderer = String(gl.getParameter(dbg ? dbg.UNMASKED_RENDERER_WEBGL : gl.RENDERER) || "");
      out.vendor = String(gl.getParameter(dbg ? dbg.UNMASKED_VENDOR_WEBGL : gl.VENDOR) || "");
      const lose = gl.getExtension("WEBGL_lose_context");
      if (lose) lose.loseContext();
    }
  } catch (e) { out.webgl2 = false; }
  const r = out.renderer;
  out.software = /SwiftShader|llvmpipe|softpipe|Microsoft Basic Render|Software/i.test(r);
  out.discrete = /NVIDIA|GeForce|RTX|Quadro|Radeon RX|Radeon Pro|FirePro|Arc\(TM\) A|Arc A\d/i.test(r);
  out.integrated = !out.discrete && !out.software && /Intel|Iris|UHD|HD Graphics|Radeon\(TM\) Graphics|Radeon Graphics|Vega \d|Adreno|Mali|Apple/i.test(r);
  g3Probe = out;
  return out;
}

export const g3HintText = p => p.software
  ? `3D is drawn by a software rasterizer (${p.renderer}) — hardware acceleration is off. On Windows: Settings › System › Display › Graphics › add SkyFrame.exe and msedgewebview2.exe › High performance, then restart SkyFrame.`
  : `3D is drawn on an integrated GPU (${p.renderer}). For the discrete GPU (e.g. RTX): Windows Settings › System › Display › Graphics › add SkyFrame.exe and msedgewebview2.exe › Options › High performance, then restart SkyFrame.`;

function g3MaybeHint() {
  const p = g3ProbeGL();
  if (!p.webgl2 || !(p.integrated || p.software)) return;
  try { if (localStorage.getItem(G3_HINT_KEY) === p.renderer) return; localStorage.setItem(G3_HINT_KEY, p.renderer); } catch (e) { return; }
  g3Toast("Graphics adapter", g3HintText(p), "info", 12000);
}

async function g3Enable(v) {
  if (v.gpu || g3Pending.has(v)) return true;
  const p = g3ProbeGL();
  if (!p.webgl2) return false;
  g3Pending.add(v);
  try {
    g3Mod = g3Mod || import("./renderer.js");
    const mod = await g3Mod;
    g3Pending.delete(v);
    if (g3Mode !== "gpu" || !g3Viewers.has(v)) return false;
    const be = new mod.GpuBackend(v, () => g3Sky || window.__sky, () => g3Lost(v));
    be.attach();
    return true;
  } catch (e) {
    g3Pending.delete(v);
    v.gpuError = String(e && e.message || e);
    return false;
  }
}
function g3Disable(v) {
  if (v.gpu) { try { v.gpu.detach(); } catch (e) { /* never break the view */ } }
}
function g3Lost(v) {
  g3Disable(v);
  g3Toast("3D renderer", "The WebGL context was lost — the 3D view switched to the Classic renderer.", "warn", 8000);
}

/** Called from the Viewer3D constructor (one hook line in js/viewer3d.js). */
export function gpuOnCreate(v) {
  g3Viewers.add(v);
  if (!v.onBoxSelect) v.onBoxSelect = (segs, add) => {   // Ctrl+drag window / crossing selection
    const s = g3Sky || window.__sky;
    if (!s || !s.handleSelect || (s.wb && s.wb.draw3d && s.wb.draw3d.active())) return;
    s.handleSelect((segs || []).filter(sg => sg && sg.uid).map(sg => ({ type: "member", uid: sg.uid })), add);
  };
  const d0 = v.destroy.bind(v);
  v.destroy = () => { g3Disable(v); g3Viewers.delete(v); d0(); };
  if (g3Mode === null) g3Mode = g3Read();
  if (g3Mode === "gpu") g3Enable(v);
}

/** Switch every viewer; resolves to the mode actually in use. */
export async function g3SetMode(mode, persist = true) {
  mode = mode === "gpu" ? "gpu" : "classic";
  if (mode === "gpu" && !g3ProbeGL().webgl2) {
    g3Mode = "classic";
    g3Toast("3D renderer", "WebGL2 is not available in this browser / WebView — the 3D view stays on the Classic renderer.", "warn", 8000);
    return "classic";
  }
  g3Mode = mode;
  if (persist) g3Write(mode);
  if (mode === "gpu") {
    const ok = await Promise.all([...g3Viewers].map(g3Enable));
    if (!ok.some(Boolean) && g3Viewers.size) {
      g3Mode = "classic";
      for (const v of g3Viewers) g3Disable(v);
      const err = [...g3Viewers].map(v => v.gpuError).find(Boolean);
      g3Toast("3D renderer", "The GPU renderer could not start" + (err ? ` (${err})` : "") + " — using Classic.", "warn", 8000);
      return "classic";
    }
    g3MaybeHint();
  } else for (const v of g3Viewers) g3Disable(v);
  for (const v of g3Viewers) v._dirty = true;
  return g3Mode;
}

/** Menu + __sky.gpu3d API (one init line in js/app.js). */
export function initGpuRenderer(sky) {
  g3Sky = sky;
  if (g3Mode === null) g3Mode = g3Read();
  const active = () => !!(sky.viewer && sky.viewer.gpu && sky.viewer.gpu.on);
  if (sky.menu && sky.menu.add) {
    sky.menu.add("View/Renderer", { label: "GPU (WebGL2)", act: "view-renderer-gpu", hint: "three.js · depth buffer · ID-buffer picking",
      radio: () => g3Mode === "gpu", fn: () => g3SetMode("gpu") });
    sky.menu.add("View/Renderer", { label: "Classic (Canvas 2D)", act: "view-renderer-classic", hint: "painter's algorithm",
      radio: () => g3Mode !== "gpu", fn: () => g3SetMode("classic") });
    sky.menu.add("View/Renderer", { label: "Graphics Adapter", act: "view-renderer-info", hint: "WebGL renderer string",
      fn: () => { const p = g3ProbeGL(); g3Toast("Graphics adapter", p.webgl2 ? `${p.renderer || "unknown"}${p.vendor ? " · " + p.vendor : ""}${p.integrated || p.software ? " — " + g3HintText(p) : ""}` : "WebGL2 is not available — the Classic renderer is used.", "info", 12000); } });
  }
  sky.gpu3d = {
    mode: () => g3Mode, set: g3SetMode, active, probe: g3ProbeGL,
    ready: async () => { if (g3Mod) await g3Mod; for (let i = 0; i < 50 && g3Pending.size; i++) await new Promise(r => setTimeout(r, 20)); return active(); },
    stats: () => (active() ? sky.viewer.gpu.stats() : null),
    benchmark: (...a) => (active() ? sky.viewer.gpu.benchmark(...a) : null),
  };
  if (g3Mode === "gpu") g3MaybeHint();
}
