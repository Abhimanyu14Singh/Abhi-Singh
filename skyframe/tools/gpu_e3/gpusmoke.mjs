// GPU-mode smoke: mock + live, menu switching, results overlays, box select, no-WebGL fallback, zero console errors.
import { chromium } from "/opt/node22/lib/node_modules/playwright/index.mjs";
const PORT = process.env.PORT || 8781;
const res = [];
const check = (n, ok, info = "") => { res.push(ok); console.log((ok ? "PASS " : "FAIL ") + n + (info ? " — " + info : "")); };
async function session(args, url) {
  const b = await chromium.launch({ args });
  const ctx = await b.newContext({ viewport: { width: 1400, height: 900 } });
  await ctx.addInitScript(() => { try { localStorage.setItem("sky.wc.lock", JSON.stringify({ afterRun: false })); } catch {} });
  const p = await ctx.newPage();
  const errs = [];
  p.on("pageerror", e => errs.push("PAGEERR " + e.message));
  p.on("console", m => { if ((m.type() === "error" || m.type() === "warning") && !/favicon|ResizeObserver|Backend unreachable|GPU stall|WebGL|GL Driver|swiftshader/i.test(m.text())) errs.push(m.type() + " " + m.text()); });
  await p.goto(url);
  await p.waitForFunction(() => window.__sky && window.__sky.store.model && window.__sky.ux && window.__sky.gpu3d, null, { timeout: 30000 });
  await p.waitForTimeout(500);
  return { b, p, errs };
}
for (const mode of ["mock", "live"]) {
  const { b, p, errs } = await session(["--use-angle=swiftshader", "--enable-unsafe-swiftshader"], `http://127.0.0.1:${PORT}/?renderer=gpu${mode === "mock" ? "&mock=1" : ""}`);
  const r = await p.evaluate(async () => {
    const s = window.__sky; const out = {};
    await s.gpu3d.ready();
    s.etabs.showResult("view3d"); await new Promise(r => setTimeout(r, 400));
    out.gpu = s.gpu3d.active();
    out.glCanvas = !!document.querySelector(".viewer-wrap canvas.g3-canvas");
    // menu: renderer radio + switch round trip
    out.acts = ["view-renderer-gpu", "view-renderer-classic", "view-renderer-info"].every(a => s.menu.acts().includes(a));
    out.path = s.menu.path("view-renderer-gpu").join("/");
    s.etabs.clickItem("view-renderer-classic"); await new Promise(r => setTimeout(r, 300));
    out.classicAfter = !s.gpu3d.active() && !document.querySelector(".viewer-wrap canvas.g3-canvas");
    s.etabs.clickItem("view-renderer-gpu"); await s.gpu3d.ready(); await new Promise(r => setTimeout(r, 300));
    out.gpuAgain = s.gpu3d.active();
    out.persist = localStorage.getItem("skyframe.renderer3d.v1");
    // run + overlays
    s.saveModel && !s.store.mock && await s.saveModel();
    s.etabs.clickItem("an-run");
    for (let i = 0; i < 600 && !s.store.results; i++) await new Promise(r => setTimeout(r, 200));
    s.etabs.showResult("view3d"); await new Promise(r => setTimeout(r, 300));
    const v = s.viewer;
    const cs = Object.keys(s.store.results.cases || {}).filter(k => s.store.results.cases[k].node_disp && Object.keys(s.store.results.cases[k].node_disp).length);
    out.cases = cs.join(",");
    v.setOverlay({ deformed: true, caseName: cs[0], scaleMult: 1 }); v._dirty = true;
    for (let i = 0; i < 30 && !(v.gpu.layers.def && v.gpu.layers.def.objs.length); i++) await new Promise(r => setTimeout(r, 100));
    out.defLayer = !!(v.gpu.layers.def && v.gpu.layers.def.objs.length);
    v.setOverlay({ deformed: false, modal: true, modeIndex: 0 }); await new Promise(r => setTimeout(r, 500));
    const f0 = v.gpu._frame; await new Promise(r => setTimeout(r, 400)); out.animating = v.gpu._frame > f0;
    v.setOverlay({ modal: false });
    await new Promise(r => setTimeout(r, 300));
    const f1 = v.gpu._frame; await new Promise(r => setTimeout(r, 500)); out.idleNoFrames = v.gpu._frame === f1;
    s.vx.toggleExtrude(true); v._dirty = true; await new Promise(r => setTimeout(r, 400));
    out.prisms = (v.gpu.base.prisms || []).length;
    s.vx.toggleExtrude(false);
    // Ctrl+drag box selection through the real pointer path
    const c = v.canvas.getBoundingClientRect();
    return { out, rect: [c.left, c.top, c.width, c.height] };
  });
  const [x, y, w, h] = r.rect;
  await p.keyboard.down("Control");
  await p.mouse.move(x + w * 0.1, y + h * 0.1); await p.mouse.down();
  await p.mouse.move(x + w * 0.5, y + h * 0.5, { steps: 5 }); await p.mouse.move(x + w * 0.9, y + h * 0.9, { steps: 5 });
  await p.mouse.up(); await p.keyboard.up("Control");
  const sel = await p.evaluate(() => (window.__sky.store.selection || []).length);
  const o = r.out;
  check(`[${mode}] GPU renderer active from ?renderer=gpu`, o.gpu && o.glCanvas);
  check(`[${mode}] View › Renderer items`, o.acts && o.path === "View/Renderer", o.path);
  check(`[${mode}] switch Classic → GPU round trip`, o.classicAfter && o.gpuAgain && o.persist === "gpu");
  check(`[${mode}] deformed shape layer`, o.defLayer, o.cases);
  check(`[${mode}] mode animation renders frames, idle renders none`, o.animating && o.idleNoFrames);
  check(`[${mode}] extruded prism batches`, o.prisms > 0, String(o.prisms));
  check(`[${mode}] Ctrl+drag window selection selects members`, sel > 0, String(sel));
  check(`[${mode}] zero console errors`, errs.length === 0, errs.slice(0, 3).join(" | "));
  await b.close();
}
{ // no WebGL → Classic + toast, no console error
  const { b, p, errs } = await session(["--disable-webgl", "--disable-3d-apis"], `http://127.0.0.1:${PORT}/?mock=1&renderer=gpu`);
  const r = await p.evaluate(async () => { const s = window.__sky; const m = await s.gpu3d.set("gpu"); await new Promise(r => setTimeout(r, 300)); return { m, probe: s.gpu3d.probe().webgl2, toast: [...document.querySelectorAll("#toasts > *")].map(t => t.textContent).join(" ") }; });
  check("no WebGL2 → stays Classic with a toast", r.m === "classic" && !r.probe && /WebGL2 is not available/.test(r.toast), r.m);
  check("no WebGL2 → zero console errors", errs.length === 0, errs.slice(0, 3).join(" | "));
  await b.close();
}
console.log(`${res.filter(Boolean).length}/${res.length}`);
