// Scale test: tower50 replicated N×N in plan (default 8×8 → ~282k members + ~90k shells) set directly on the viewer.
import { chromium } from "/opt/node22/lib/node_modules/playwright/index.mjs";
import fs from "fs";
const PORT = process.env.PORT || 8781;
const OUT = new URL(".", import.meta.url).pathname;
const N = +(process.argv[2] || 8);
const b = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--js-flags=--max-old-space-size=8192"] });
const p = await (await b.newContext({ viewport: { width: 1600, height: 1000 } })).newPage();
const errs = []; p.on("pageerror", e => errs.push(e.message));
await p.goto(`http://127.0.0.1:${PORT}/?mock=1`);
await p.waitForFunction(() => window.__sky && window.__sky.store.model && window.__sky.ux, null, { timeout: 30000 });
const m = JSON.parse(fs.readFileSync(OUT + "tower50.json"));
const r = await p.evaluate(async ([m, N]) => {
  const s = window.__sky, v = s.viewer;
  s.etabs.showResult("view3d"); await new Promise(r => setTimeout(r, 300));
  const xs = m.grid.x_lines, ys = m.grid.y_lines, dx = (xs[xs.length - 1] - xs[0]) + 12, dy = (ys[ys.length - 1] - ys[0]) + 12;
  const big = { ...m, members: [], shells: [], section_cuts: [] };
  for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) {
    const sh = p => [p[0] + i * dx, p[1] + j * dy, p[2]];
    for (const mm of m.members) big.members.push({ ...mm, uid: `${mm.uid}#${i}_${j}`, pi: sh(mm.pi), pj: sh(mm.pj) });
    for (const sp of m.shells || []) big.shells.push({ ...sp, uid: `${sp.uid}#${i}_${j}`, corners: sp.corners.map(sh), openings: sp.openings });
  }
  const out = { members: big.members.length, shells: big.shells.length };
  let t0 = performance.now(); v.setModel(big); out.classic_setModel_ms = +(performance.now() - t0).toFixed(0);
  t0 = performance.now(); if (N <= 1) { v._render(); v.ctx.getImageData(0, 0, 1, 1); } out.classic_frame_ms = +(performance.now() - t0).toFixed(0);
  await s.gpu3d.set("gpu", false); await s.gpu3d.ready();
  t0 = performance.now(); v.gpu.render(); out.gpu_first_frame_cpu_ms = +(performance.now() - t0).toFixed(0);
  const bm = v.gpu.benchmark(5, 0.02);
  // CPU-side cost only (what stays on the main thread on a real GPU): render() without the read-back
  const tc = []; for (let i = 0; i < 10; i++) { v.yaw += 0.02; const t = performance.now(); v.gpu.render(); tc.push(performance.now() - t); }
  tc.sort((a, b) => a - b);
  v.gpu.gl.readPixels(0, 0, 1, 1, v.gpu.gl.RGBA, v.gpu.gl.UNSIGNED_BYTE, new Uint8Array(4));   // drain the queued frames before timing picks
  Object.assign(out, { gpu_build_ms: bm.build_ms, gpu_cpu_ms_median: +tc[5].toFixed(1), gpu_swiftshader_frame_median_ms: bm.median_ms, calls: bm.calls, triangles: bm.triangles, instances: bm.instances });
  let t1 = performance.now(); v.gpu.ensureIdBuffer(); v.gpu.gl.readPixels(0, 0, 1, 1, v.gpu.gl.RGBA, v.gpu.gl.UNSIGNED_BYTE, new Uint8Array(4)); out.idpass_ms = +(performance.now() - t1).toFixed(1);
  t1 = performance.now(); const hit = v._pickAt(800, 500, 8); out.pick_ms = +(performance.now() - t1).toFixed(1); out.cands = v.gpu.lastPickCandidates;
  t1 = performance.now(); v._pickAt(810, 505, 8); out.pick2_ms = +(performance.now() - t1).toFixed(1);
  const t2 = performance.now(); const box = v._pickBox(700, 400, 900, 600, false); out.box_ms = +(performance.now() - t2).toFixed(1); out.box_n = box.length; out.box_stats = v.gpu.lastBoxStats;
  return out;
}, [m, N]);
console.log(JSON.stringify({ ...r, errs }, null, 1));
await b.close();
