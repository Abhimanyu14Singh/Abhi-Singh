// Frame-time benchmark, Classic vs GPU, on a model (+ optional precomputed results).
// usage: node bench.mjs tower50.json [tower50_results.json] [frames]
import { chromium } from "/opt/node22/lib/node_modules/playwright/index.mjs";
import fs from "fs";
const PORT = process.env.PORT || 8781;
const OUT = new URL(".", import.meta.url).pathname;
const MODEL = process.argv[2] || "tower50.json";
const RES = process.argv[3] && process.argv[3] !== "-" ? process.argv[3] : null;
const FR = +(process.argv[4] || 20);
const b = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const ctx = await b.newContext({ viewport: { width: 1600, height: 1000 } });
await ctx.addInitScript(() => { try { localStorage.setItem("sky.wc.lock", JSON.stringify({ afterRun: false })); } catch {} });
const p = await ctx.newPage();
const errs = [];
p.on("pageerror", e => errs.push("PAGEERR " + e.message));
p.on("console", m => { if (m.type() === "error") errs.push(m.text()); });
await p.goto(`http://127.0.0.1:${PORT}/`);
await p.waitForFunction(() => window.__sky && window.__sky.store.model && window.__sky.ux, null, { timeout: 30000 });
await p.evaluate(() => { try { localStorage.setItem("skyframe.gpuhint.v1", window.__sky.gpu3d.probe().renderer); } catch {} });
const model = JSON.parse(fs.readFileSync(OUT + MODEL));
const R = RES ? JSON.parse(fs.readFileSync(OUT + RES)) : null;
await p.evaluate(async ([m, R]) => {
  const s = window.__sky; s.adoptModel(m, "t.json");
  s.etabs.showResult("view3d");
  await new Promise(r => setTimeout(r, 400));
  if (R) { s.store.results = R; s.viewer.setResults(R); }
  s.etabs.showResult("view3d");
  await new Promise(r => setTimeout(r, 300));
}, [model, R]);

const scen = [["lines", {}], ["extruded", { vx: { extrude: true } }]];
if (R) scen.push(
  ["contour_M11", { ct: { on: true, comp: "M11", caseName: "DEAD" } }],
  ["contour_extruded", { vx: { extrude: true }, ct: { on: true, comp: "M11", caseName: "DEAD" } }],
  ["deformed_DEAD", { ov: { deformed: true, caseName: "DEAD", scaleMult: 1 } }],
  ["mode1_anim", { ov: { modal: true, modeIndex: 0 } }],
  ["mode1_anim_extruded", { vx: { extrude: true }, ov: { modal: true, modeIndex: 0 } }],
);

async function run(mode) {
  const out = {};
  for (const [name, sc] of scen) {
    out[name] = await p.evaluate(async ([sc, mode, FR]) => {
      const s = window.__sky, v = s.viewer;
      s.vx.reset(); v.setOverlay({ deformed: false, modal: false, buckling: false }); v.setContours({ on: false });
      if (sc.vx) s.vx.set(sc.vx);
      if (sc.ov) v.setOverlay(sc.ov);
      if (sc.ct) v.setContours(sc.ct);
      v.yaw = 0.7; v.pitch = 0.42; v.fit(); v.vyaw = v.vpitch = 0;
      const pct = a => { a.sort((x, y) => x - y); const mean = a.reduce((x, y) => x + y, 0) / a.length; return { mean: +mean.toFixed(1), median: +a[a.length >> 1].toFixed(1), p95: +a[Math.floor(a.length * .95)].toFixed(1) }; };
      // first frame (includes geometry build / upload)
      let t0 = performance.now();
      if (mode === "gpu") { v.gpu.render(); v.gpu.gl.readPixels(0, 0, 1, 1, v.gpu.gl.RGBA, v.gpu.gl.UNSIGNED_BYTE, new Uint8Array(4)); }
      else { v._render(); v.ctx.getImageData(0, 0, 1, 1); }
      const first = +(performance.now() - t0).toFixed(1);
      if (mode === "gpu") { const r = v.gpu.benchmark(FR, 0.02); return { first_ms: first, ...pct([r.mean_ms]), median: r.median_ms, p95: r.p95_ms, mean: r.mean_ms, calls: r.calls, triangles: r.triangles, instances: r.instances, overlay_ms: r.overlay_ms, cpu_ms: r.cpu_ms }; }
      const t = [];
      for (let i = 0; i < FR; i++) { v.yaw += 0.02; t0 = performance.now(); v._render(); v.ctx.getImageData(0, 0, 1, 1); t.push(performance.now() - t0); }
      return { first_ms: first, ...pct(t) };
    }, [sc, mode, FR]);
    console.log(mode, name, JSON.stringify(out[name]));
  }
  return out;
}
const classic = await run("classic");
const gm = await p.evaluate(async () => { const s = window.__sky; const m = await s.gpu3d.set("gpu", false); await s.gpu3d.ready(); return m; });
if (gm !== "gpu") console.log("GPU failed");
// stop the rAF-driven loop from interfering: benchmark calls render() directly
const gpu = await run("gpu");
const info = await p.evaluate(() => ({ members: window.__sky.viewer.segs.length, shells: (window.__sky.viewer.shellPolys || []).length, stats: window.__sky.gpu3d.stats() }));
console.log(JSON.stringify({ model: MODEL, info, classic, gpu, errs }, null, 1));
await b.close();
