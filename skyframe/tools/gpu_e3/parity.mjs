// GPU vs Classic parity: screenshots per scenario, % differing pixels, picking + box equality.
// usage: node parity.mjs model.json [run]
import { chromium } from "/opt/node22/lib/node_modules/playwright/index.mjs";
import fs from "fs";
const PORT = process.env.PORT || 8781;
const MODEL = process.argv[2] || "tower5.json";
const RUN = !!process.argv[3];
const RESFILE = process.argv[3] && process.argv[3] !== "run" ? process.argv[3] : null;
const OUT = new URL(".", import.meta.url).pathname;
const SHOTS = OUT + "shots/";
fs.mkdirSync(SHOTS, { recursive: true });
const b = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const ctx = await b.newContext({ viewport: { width: 1400, height: 900 } });
await ctx.addInitScript(() => { try { localStorage.setItem("sky.wc.lock", JSON.stringify({ afterRun: false })); localStorage.setItem("skyframe.gpuhint.v1", "x"); } catch {} });
const p = await ctx.newPage();
const errs = [];
p.on("pageerror", e => errs.push("PAGEERR " + e.message));
p.on("console", m => { if (m.type() === "error") errs.push(m.type() + " " + m.text()); });
await p.goto(`http://127.0.0.1:${PORT}/`);
await p.waitForFunction(() => window.__sky && window.__sky.store.model && window.__sky.ux, null, { timeout: 30000 });
await p.evaluate(() => { try { localStorage.setItem("skyframe.gpuhint.v1", window.__sky.gpu3d.probe().renderer); } catch {} });
const model = JSON.parse(fs.readFileSync(OUT + MODEL));
await p.evaluate(async (m) => {
  const s = window.__sky; s.adoptModel(m, "t.json");
  s.etabs && s.etabs.showResult ? s.etabs.showResult("view3d") : s.switchTab("view3d");
  await new Promise(r => setTimeout(r, 400));
  const wf = document.querySelector(".wf-panel, #wfPanel, .workflow-panel"); if (wf) wf.style.display = "none";
}, model);
await p.keyboard.press("Escape");
if (RESFILE) {
  const R = JSON.parse(fs.readFileSync(OUT + RESFILE));
  await p.evaluate(async (R) => { const s = window.__sky; s.store.results = R; s.viewer.setResults(R); document.dispatchEvent(new CustomEvent("sky:results-changed")); await new Promise(r => setTimeout(r, 300)); }, R);
} else if (RUN) {
  await p.evaluate(async () => { await window.__sky.saveModel(); window.__sky.etabs.clickItem("an-run"); });
  await p.waitForFunction(() => !!window.__sky.store.results, null, { timeout: 600000 });
  await p.waitForTimeout(800);
}
// hide floating panels that cover the view (same in both modes, but toasts are timing dependent)
await p.addStyleTag({ content: "#toasts, .wf-dock, .workflow, [class*='workflow'] { display: none !important; }" });

const scenarios = [
  ["lines", {}],
  ["colorby_section", { vx: { colorBy: "section" } }],
  ["extrude", { vx: { extrude: true } }],
  ["extrude_colorsec_transp", { vx: { extrude: true, colorBy: "section", shellTransp: 0.5 } }],
  ["cut_x", { vx: { cut: { on: true, axis: "x", coord: "mid" } } }],
  ["extrude_cut_z", { vx: { extrude: true, cut: { on: true, axis: "z", coord: "mid" } } }],
  ["highlight", { hl: true }],
  ["joints_axes", { vx: { show: { joint: true, axes: true } } }],
  ["labels", { vx: { show: { label: true } } }],
];
if (RUN) scenarios.push(
  ["deformed", { ov: { deformed: true, caseName: "DEAD", scaleMult: 1 } }],
  ["deformed_extruded", { vx: { extrude: true }, ov: { deformed: true, caseName: "DEAD", scaleMult: 1 } }],
  ["mode1", { ov: { modal: true, modeIndex: 0 } }],
  ["mode2_extruded", { vx: { extrude: true }, ov: { modal: true, modeIndex: 1 } }],
  ["contour_M11", { ct: { on: true, comp: "M11", caseName: "DEAD" } }],
  ["contour_N22_extruded", { vx: { extrude: true }, ct: { on: true, comp: "N22", caseName: "DEAD" } }],
);

async function apply(sc) {
  await p.evaluate(async (sc) => {
    const s = window.__sky, v = s.viewer;
    if (!v.canvas.offsetParent) { s.etabs && s.etabs.showResult ? s.etabs.showResult("view3d") : s.switchTab("view3d"); await new Promise(r => setTimeout(r, 300)); }
    s.vx.reset();
    v.setHighlight(null);
    v.setOverlay({ deformed: false, modal: false, buckling: false });
    v.setContours({ on: false });
    if (sc.vx) {
      const o = JSON.parse(JSON.stringify(sc.vx));
      if (o.cut && o.cut.coord === "mid") { const i = { x: 0, y: 1, z: 2 }[o.cut.axis]; o.cut.coord = (v._bbox[0][i] + v._bbox[1][i]) / 2 + 0.37; }
      s.vx.set(o);
    }
    if (sc.hl) v.setHighlight(v.segs.filter((_, i) => i % 7 === 0).map(x => x.uid), "#35b5e5");
    if (sc.ov) v.setOverlay(sc.ov);
    if (sc.ct) v.setContours(sc.ct);
    if (sc.ov && (sc.ov.modal || sc.ov.buckling)) {
      const T = performance.now();
      window.__realNow = window.__realNow || performance.now.bind(performance);
      performance.now = () => T;
      v._animT0 = T - 450;   // sin(2π·0.45/1.8) = 1 → peak of the mode cycle in both renderers
    }
    v.yaw = 0.7; v.pitch = 0.42; v.fit(); v.vyaw = 0; v.vpitch = 0;
    v._dirty = true;
    await new Promise(r => setTimeout(r, 350));
    v._dirty = true;
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    await new Promise(r => setTimeout(r, 120));
  }, sc);
}
async function restoreNow() { await p.evaluate(() => { if (window.__realNow) { performance.now = window.__realNow; delete window.__realNow; } }); }

async function shotAll(tag) {
  const shots = {};
  for (const [name, sc] of scenarios) {
    await apply(sc);
    const bb = await p.evaluate(() => { const r = document.querySelector(".viewer-wrap").getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; });
    const buf = await p.screenshot({ clip: bb, timeout: 180000 });
    fs.writeFileSync(`${SHOTS}${MODEL.replace(".json", "")}_${name}_${tag}.png`, buf);
    shots[name] = buf.toString("base64");
    await restoreNow();
  }
  return shots;
}
async function pickSamples() {
  return p.evaluate(async () => {
    const s = window.__sky, v = s.viewer;
    s.vx.reset(); v.setOverlay({ deformed: false, modal: false, buckling: false }); v.setContours({ on: false }); v.setHighlight(null);
    v.yaw = 0.7; v.pitch = 0.42; v.fit(); v.vyaw = 0; v.vpitch = 0; v._dirty = true;
    await new Promise(r => setTimeout(r, 300));
    if (v.gpu && v.gpu.on) v.gpu.render(); else v._render();
    const W = v.canvas.clientWidth, H = v.canvas.clientHeight;
    const picks = [];
    let seed = 12345; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    // random points + points right on member midpoints (hits)
    for (let i = 0; i < 1500; i++) picks.push([rnd() * W, rnd() * H]);
    const P = v._makeProjector();
    v.segs.forEach((sg, i) => { if (i % 3) return; const m = [(sg.p1[0] + sg.p2[0]) / 2, (sg.p1[1] + sg.p2[1]) / 2, (sg.p1[2] + sg.p2[2]) / 2]; const c = P.toCam(m); if (c[2] > P.near) { const q = P.proj(c); picks.push([q.x + (rnd() - 0.5) * 6, q.y + (rnd() - 0.5) * 6]); } });
    const t0 = performance.now();
    const res = picks.map(([x, y]) => { const sg = v._pickAt(x, y, 8); return sg ? sg.uid : null; });
    const pickMs = (performance.now() - t0) / picks.length;
    const boxes = [[0.2, 0.2, 0.6, 0.7], [0.6, 0.7, 0.2, 0.2], [0.4, 0.1, 0.55, 0.9], [0.55, 0.9, 0.4, 0.1], [0, 0, 1, 1], [0.45, 0.45, 0.5, 0.52]];
    const t1 = performance.now();
    const box = boxes.map(([a, b2, c, d]) => v._pickBox(a * W, b2 * H, c * W, d * H, c < a).map(x => x.uid).join(","));
    const boxMs = (performance.now() - t1) / boxes.length;
    return { res, box, pickMs, boxMs, n: picks.length, hits: res.filter(Boolean).length };
  });
}

const classic = await shotAll("classic");
const pickC = await pickSamples();
const gmode = await p.evaluate(async () => { const s = window.__sky; const m = await s.gpu3d.set("gpu", false); await s.gpu3d.ready(); return m; });
if (gmode !== "gpu") { console.log("GPU mode failed", gmode); }
const gpu = await shotAll("gpu");
const pickG = await pickSamples();
const gstats = await p.evaluate(() => window.__sky.gpu3d.stats());

// pixel diff in a blank page
const dp = await ctx.newPage();
await dp.setContent("<canvas id=c></canvas>");
const rows = [];
for (const [name] of scenarios) {
  const r = await dp.evaluate(async ([a, bb]) => {
    const load = src => new Promise(res => { const im = new Image(); im.onload = () => res(im); im.src = "data:image/png;base64," + src; });
    const [ia, ib] = await Promise.all([load(a), load(bb)]);
    const W = ia.width, H = ia.height;
    const c = document.createElement("canvas"); c.width = W; c.height = H; const g = c.getContext("2d");
    g.drawImage(ia, 0, 0); const A = g.getImageData(0, 0, W, H).data;
    g.clearRect(0, 0, W, H); g.drawImage(ib, 0, 0); const Bd = g.getImageData(0, 0, W, H).data;
    let diff = 0, diffStrict = 0, fg = 0;
    const bg = [A[0], A[1], A[2]];
    for (let i = 0; i < A.length; i += 4) {
      const d = Math.max(Math.abs(A[i] - Bd[i]), Math.abs(A[i + 1] - Bd[i + 1]), Math.abs(A[i + 2] - Bd[i + 2]));
      const isFg = Math.max(Math.abs(A[i] - bg[0]), Math.abs(A[i + 1] - bg[1]), Math.abs(A[i + 2] - bg[2])) > 6 || Math.max(Math.abs(Bd[i] - bg[0]), Math.abs(Bd[i + 1] - bg[1]), Math.abs(Bd[i + 2] - bg[2])) > 6;
      if (isFg) fg++;
      if (d > 48) diff++;
      if (d > 16) diffStrict++;
    }
    const N = W * H;
    return { pct: +(100 * diff / N).toFixed(2), pctStrict: +(100 * diffStrict / N).toFixed(2), pctOfDrawn: +(100 * diff / Math.max(fg, 1)).toFixed(1), drawnPct: +(100 * fg / N).toFixed(1) };
  }, [classic[name], gpu[name]]);
  rows.push({ scenario: name, ...r });
}
let same = 0;
for (let i = 0; i < pickC.res.length; i++) if (pickC.res[i] === pickG.res[i]) same++;
const boxSame = pickC.box.map((x, i) => x === pickG.box[i]); console.log("BOXDBG", pickC.box.map((x,i)=>[x.split(",").length, pickG.box[i].split(",").length, x.slice(0,60), pickG.box[i].slice(0,60)]));
console.log(JSON.stringify({ model: MODEL, rows,
  pick: { samples: pickC.n, hitsClassic: pickC.hits, hitsGpu: pickG.hits, equal: same, classic_ms: +pickC.pickMs.toFixed(3), gpu_ms: +pickG.pickMs.toFixed(3) },
  box: { equal: boxSame, sizes: pickC.box.map(x => x ? x.split(",").length : 0), classic_ms: +pickC.boxMs.toFixed(2), gpu_ms: +pickG.boxMs.toFixed(2) },
  gstats, errs }, null, 1));
const mism = []; for (let i = 0; i < pickC.res.length && mism.length < 10; i++) if (pickC.res[i] !== pickG.res[i]) mism.push([i, pickC.res[i], pickG.res[i]]);
if (mism.length) console.log("pick mismatches", JSON.stringify(mism));
await b.close();
