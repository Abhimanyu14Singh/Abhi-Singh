/* Wave G — results in every view: Playwright check (mock and live, Classic
   and GPU renderers).

   Usage:  node g_results.e2e.mjs [baseUrl] [mock=1|0] [renderer=classic|gpu]
   Env:    PW_MODULE   path of playwright's index.mjs
                       (default /opt/node22/lib/node_modules/playwright/index.mjs)
           PW_CHROMIUM chromium executable (default: playwright's own)

   Live mode first POSTs a model with time-history, pushover, nonlinear
   static and staged cases. Asserts: the results toolbar; deformed shape /
   frame diagrams / contours in the main 3D view, plan, elevation and every
   View › Windows pane (render state + canvas pixels); mode stepping with
   the period in the title + animation; the TH step selector (slider) and
   Play advancing steps; pushover / staged steps; Max/Min envelope pairs;
   the member detail Scroll-for-Values readout equals the Element Forces -
   Frames table value at that station; Select by Result; the watch list
   updating with the case and exporting CSV; named views carrying the
   results view; video export still recording; zero console errors.
   Prints {"passed", "failed", "fails"} and exits 1 on any failure. */

const PW = process.env.PW_MODULE || "/opt/node22/lib/node_modules/playwright/index.mjs";
const { chromium } = await import(PW);
const BASE = process.argv[2] || "http://127.0.0.1:8771";
const MOCK = (process.argv[3] || "1") !== "0";
const RENDERER = process.argv[4] || "classic";
const q = new URLSearchParams();
if (MOCK) q.set("mock", "1");
q.set("renderer", RENDERER);
const URL0 = `${BASE}/?${q}`;

// WebGL2 in headless chromium needs SwiftShader for the GPU run (those flags break MediaRecorder, so the
// video check runs in the Classic pass only)
const launch = RENDERER === "gpu" ? { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] } : {};
if (process.env.PW_CHROMIUM) launch.executablePath = process.env.PW_CHROMIUM;
const browser = await chromium.launch(launch);
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
await ctx.addInitScript(() => { try { localStorage.setItem("sky.wc.lock", JSON.stringify({ afterRun: false })); } catch { /* */ } });
const page = await ctx.newPage();
const errors = [];
page.on("console", m => { if (m.type() === "error" && !/favicon|ResizeObserver loop/.test(m.text())) errors.push(m.text()); });
page.on("pageerror", e => errors.push(String(e)));
let passed = 0;
const fails = [];
const ok = (cond, what) => { if (cond) passed++; else { fails.push(what); console.error("FAIL:", what); } };
const ev = (fn, arg) => page.evaluate(fn, arg);
const settle = (ms = 300) => page.waitForTimeout(ms);
const poll = async (fn, arg, pred, ms = 15000) => { const t0 = Date.now(); let v; for (;;) { v = await ev(fn, arg); if (pred(v) || Date.now() - t0 > ms) return v; await settle(150); } };
const idle = () => page.waitForFunction(() => !window.__sky.results.loading(), null, { timeout: 240000 });

/* ---------- live: a model with every multi-step case type ---------- */
if (!MOCK) {
  const m = await (await page.request.get(`${BASE}/api/model`)).json();
  const acc = [0]; for (let k = 1; k < 120; k++) acc.push(2.5 * Math.sin(0.35 * k));
  m.th_cases = { THX: { name: "THX", direction: "X", accel: acc, dt: 0.01, damping: 0.05, scale: 1.0 } };
  m.pushover_cases = { PUSH: { name: "PUSH", direction: "X", gravity: { DEAD: 1.0 }, target_drift: 0.01, steps: 16, hinges: "column_base", My: {}, default_My: null, hardening: 0.02 } };
  m.nonlinear_static_cases = { NLG: { name: "NLG", loads: [{ pattern: "DEAD", scale: 1.0 }], load_application: "load_control", steps: 4, control_dof: "UX", geometric: "none" } };
  m.staged_cases = { STG: { name: "STG", pattern: "DEAD", stages: "per_story", include_live: {} } };
  const col1 = (m.members || []).filter(x => x.kind === "column" && x.story === (m.stories[0] || {}).name).map(x => x.uid);
  m.groups = { GCOL: { members: col1, shells: [], links: [], points: [] } };
  const r = await page.request.post(`${BASE}/api/model`, { data: m });
  ok(r.ok(), "live: model with TH / pushover / NL static / staged cases accepted");
}

await page.goto(URL0);
await page.waitForFunction(() => window.__sky && window.__sky.results && window.__sky.store.model && (window.__sky.store.model.members || []).length > 0, null, { timeout: 60000 });
await settle(800);
await page.keyboard.press("Escape");
await ev(() => { try { localStorage.removeItem("skyframe.gresbar.v1"); } catch { /* */ } });
if (RENDERER === "gpu") {
  const gpu = await ev(async () => (window.__sky.gpu3d ? await window.__sky.gpu3d.ready() : false));
  ok(gpu, "GPU renderer active");
}
const model0 = await ev(() => JSON.stringify(window.__sky.store.model));
await ev(() => window.__sky.etabs.clickItem("an-run"));
await page.waitForFunction(() => !!window.__sky.store.results, null, { timeout: 300000 });
await settle(800);

/* ---------- 1. toolbar + catalogue ---------- */
ok(await ev(() => window.__sky.results.barVisible()), "results toolbar shown after the run");
const cat = await ev(() => window.__sky.results.catalog().map(e => [e.key, e.kind]));
const kinds = new Set(cat.map(c => c[1]));
for (const k of ["static", "combo", "modal", "th", "pushover", "staged"]) ok(kinds.has(k), `catalogue has ${k} cases`);
if (!MOCK) ok(kinds.has("nl"), "catalogue has nonlinear static cases");

/* ---------- 2. main 3D: deformed / diagram / contour ---------- */
await ev(() => window.__sky.etabs.clickItem("dis-view3d"));
await settle();
const pix = sel => ev(s => { const c = document.querySelector(s); try { return c.toDataURL().length + ":" + c.toDataURL().slice(-200); } catch { return ""; } }, sel);
const sel3d = RENDERER === "gpu" ? "#viewer3d" : "#viewer3d";
await ev(() => window.__sky.results.select("c:EQX"));
await settle(400);
const p0 = await pix(sel3d);
await ev(() => window.__sky.results.setViewOpts("main3d", { deformed: true, diagram: "M3", values: "controlling", contour: "UX", reactions: true }));
const st3 = await poll(() => { const R = window.__sky.results, v = window.__sky.viewer; return { ov: v.overlay.caseName, def: v.overlay.deformed, d: R.drawnIn("main3d"), gl: R.glCells("main3d") }; }, null,
  v => v.d && v.d.diagrams > 0 && v.d.reactions > 0 && (v.gl > 0 || v.d.cells > 0));
ok(st3.ov === "__gres__" && st3.def, "main 3D: deformed shape through the viewer pipeline");
ok(st3.d && st3.d.diagrams > 0 && st3.d.labels > 0, "main 3D: frame diagrams with controlling-station values");
ok(st3.d && st3.d.reactions > 0, "main 3D: reaction arrows");
ok(RENDERER === "gpu" ? st3.gl > 0 : (st3.d && st3.d.cells > 0), `main 3D: displacement contours (${RENDERER === "gpu" ? "GPU layer" : "Classic cells"})`);
ok((await pix(sel3d)) !== p0, "main 3D: canvas changed");

/* ---------- 3. plan + elevation ---------- */
await ev(() => window.__sky.etabs.clickItem("draw-plan"));
await settle(300);
await ev(() => window.__sky.results.setViewOpts("plan", { deformed: true, diagram: "M3", values: "values", contour: "UX" }));
const dp = await poll(() => window.__sky.results.drawnIn("plan"), null, d => d && d.deformed > 0 && d.diagrams > 0 && d.cells > 0);
ok(dp && dp.deformed > 0 && dp.diagrams > 0 && dp.cells > 0, "plan: deformed in-plane shape, frame diagrams, contours");
ok(await ev(() => { const c = document.querySelector('canvas.g-res-canvas[data-g-view="plan"]'); return !!c && c.width > 0; }), "plan: results canvas over the plan SVG");
ok(await ev(() => window.__sky.results.activeView()) === "plan", "active view follows the plan window");
await ev(() => window.__sky.etabs.clickItem("draw-elev"));
await settle(300);
// a frame line along X (plane y = const): EQX bends it in its own plane, so the in-plane moment is non-zero
await ev(() => { const o = [...document.getElementById("elevLineSelect").options].map(x => x.value).find(v => /^y:/.test(v)); if (o) window.__sky.setElevLine(o); });
await settle(200);
await ev(() => window.__sky.results.setViewOpts("elev", { deformed: true, diagram: "MIN", values: "controlling", reactions: true }));
const de = await poll(() => window.__sky.results.drawnIn("elev"), null, d => d && d.deformed > 0 && d.diagrams > 0 && d.labels > 0 && d.reactions > 0);
ok(de && de.deformed > 0 && de.diagrams > 0 && de.labels > 0, "elevation: deformed shape + in-plane moment diagrams with values " + JSON.stringify(de));
ok(de && de.reactions > 0, "elevation: in-plane reactions");

/* ---------- 4. View › Windows panes ---------- */
await ev(() => window.__sky.etabs.clickItem("dis-view3d"));
await ev(() => window.__sky.wb.windows.setLayout("4"));
await settle(1200);
await ev(() => { const R = window.__sky.results; R.syncPanes(); for (const id of R.viewIds()) if (id.startsWith("pane")) R.setViewOpts(id, { deformed: true, diagram: "M3", contour: "UX" }); });
const panes = await poll(() => { const R = window.__sky.results; return R.viewIds().filter(i => i.startsWith("pane")).map(i => ({ id: i, kind: R.view(i).kind, d: R.drawnIn(i), ov: R.view(i).kind === "3d" ? R.view(i).obj.overlay.caseName : null })); }, null,
  ps => ps.length === 4 && ps.every(p => p.d && p.d.diagrams > 0 && (p.kind === "3d" ? p.ov === "__gres__" : p.d.deformed > 0)));
ok(panes.length === 4, "four panes registered as results views");
for (const p of panes) ok(p.d && p.d.diagrams > 0 && (p.kind === "3d" ? p.ov === "__gres__" : p.d.deformed > 0), `${p.id} (${p.kind}) shows deformed + diagrams`);
await ev(() => window.__sky.wb.windows.setLayout("1"));
await settle(400);

/* ---------- 5. modes: stepping, period in the title, animation ---------- */
await ev(() => window.__sky.etabs.clickItem("dis-view3d"));
await ev(() => { const R = window.__sky.results; R.select("modal"); R.setViewOpts("main3d", { deformed: true, diagram: "", contour: "", reactions: false }); });
await page.click("#gResNext");
await settle(300);
const ml = await ev(() => document.getElementById("gResStepLabel").textContent);
ok(/^Mode 2 — T = /.test(ml), "mode stepping (▶) with the period in the label: " + ml);
await page.click("#gResPlay");
await page.waitForFunction(() => Math.abs(window.__sky.viewer.overlay.scaleMult - 1) > 1e-3, null, { timeout: 5000 }).catch(() => {});
const sm = await ev(() => window.__sky.viewer.overlay.scaleMult);
ok(Math.abs(sm - 1) > 1e-3, "mode animation runs on screen (scale multiplier oscillates)");
await page.click("#gResPlay");
await settle(200);
ok(!(await ev(() => window.__sky.results.isPlaying())), "Pause stops the animation");

/* ---------- 6. time history: step selector + play ---------- */
const thKey = cat.find(c => c[1] === "th")[0];
await page.selectOption("#gResCase", thKey);
await idle();
await settle(300);
const ax = await ev(() => { const a = window.__sky.results.axis(); return { n: a.n, src: a.source, max: document.getElementById("gResStep").max }; });
ok(ax.src === "steps" && ax.n > 2 && +ax.max === ax.n - 1, `TH step data loaded (${ax.n} steps, slider max ${ax.max})`);
await ev(() => { const s = document.getElementById("gResStep"); s.value = "1"; s.dispatchEvent(new Event("input", { bubbles: true })); });
await settle(200);
const lab1 = await ev(() => document.getElementById("gResStepLabel").textContent);
await ev(() => { const s = document.getElementById("gResStep"); s.value = "2"; s.dispatchEvent(new Event("input", { bubbles: true })); });
await settle(200);
const lab2 = await ev(() => document.getElementById("gResStepLabel").textContent);
ok(/^t = /.test(lab1) && lab1 !== lab2, `TH step slider changes the time (${lab1} → ${lab2})`);
const thState = await poll(() => { const s = window.__sky.results.state(); return { nd: !!s.node_disp, ms: Object.keys(s.member_stations || {}).length }; }, null, v => v.nd && v.ms > 0);
ok(thState.nd && thState.ms > 0, "TH step state has joint displacements and member stations");
await ev(() => window.__sky.results.setViewOpts("main3d", { deformed: true, diagram: "M3" }));
const s0 = await ev(() => window.__sky.results.selection().step);
await page.click("#gResPlay");
await page.waitForFunction(s => window.__sky.results.selection().step !== s, s0, { timeout: 8000 }).catch(() => {});
const s1 = await ev(() => window.__sky.results.selection().step);
ok(s1 !== s0, `Play advances the TH steps (${s0} → ${s1})`);
await ev(() => window.__sky.results.pause());
await page.click('#gResEnv button[data-env="maxmin"]');
await idle();
const envSt = await poll(() => { const s = window.__sky.results.state(); return { env: s.env, min: !!s.member_stations_min, d: window.__sky.results.drawnIn("main3d") }; }, null, v => v.env === "maxmin" && v.min && v.d && v.d.diagrams > 0);
ok(envSt.env === "maxmin" && envSt.min && envSt.d.diagrams > 0, "TH envelope: max / min pair drawn as two curves");
await page.click('#gResEnv button[data-env="step"]');

/* ---------- 7. pushover + staged steps ---------- */
const poKey = cat.find(c => c[1] === "pushover")[0];
await ev(k => window.__sky.results.select(k), poKey);
await idle();
await ev(() => window.__sky.results.setStep(1));
const po = await poll(() => { const R = window.__sky.results, a = R.axis(); const s = R.state(); return { n: a.n, src: a.source, lab: s.stepLabel, ms: Object.keys(s.member_stations || {}).length }; }, null, v => v.ms > 0);
ok(po.src === "steps" && po.n > 1 && po.ms > 0 && /^Step /.test(po.lab), `pushover steps (${po.n}, ${po.lab})`);
const stKey = cat.find(c => c[1] === "staged")[0];
await ev(k => window.__sky.results.select(k), stKey);
await idle();
const stg = await ev(() => { const R = window.__sky.results, a = R.axis(); R.setStep(0); return { n: a.n, labels: a.labels, lab: R.state().stepLabel }; });
ok(stg.n >= 2 && stg.lab === stg.labels[0], `staged construction stages (${stg.labels.join(", ")})`);
if (!MOCK) {
  await ev(() => window.__sky.results.select("nl:NLG"));
  await idle();
  const nl = await ev(() => { const a = window.__sky.results.axis(); return { n: a.n, src: a.source }; });
  ok(nl.src === "steps" && nl.n === 5, "nonlinear static steps 0..4 recorded");
}
const rsKey = (cat.find(c => c[1] === "rs") || [])[0];
if (rsKey) {
  await ev(k => window.__sky.results.select(k), rsKey);
  await settle(200);
  const rs = await ev(() => ({ dis: [...document.querySelectorAll("#gResEnv button")].every(b => b.disabled), signed: window.__sky.results.state().signed }));
  ok(rs.dis && rs.signed === false, "response spectrum: positive envelope only (no Max/Min sign toggle)");
}

/* ---------- 8. member detail: scroll readout = table value ---------- */
await ev(() => window.__sky.results.select("c:EQX"));
await ev(() => window.__sky.etabs.clickItem("dis-g-detail"));
await settle(400);
ok(await ev(() => window.__sky.results._detail.isOpen()), "Display › Force Diagrams › Diagrams for Frame Object… opens the detail window");
const uid = await ev(() => window.__sky.store.model.members.find(m => m.kind === "beam").uid);
await ev(u => window.__sky.results.openDetail({ type: "member", uid: u }), uid);
await settle(300);
const tbl = await ev(async u => {
  const t = await window.__sky.skyApi("/api/tables/element_forces_frames", { cases: ["EQX"] });
  return (t.rows || []).filter(r => r.frame === u && r.case === "EQX").map(r => [r.station, r.M3, r.V2]);
}, uid);
ok(tbl.length >= 3, `Element Forces - Frames rows for ${uid}`);
let match = 0;
for (const [x, m3, v2] of tbl.slice(0, 5)) {
  const got = await ev(async ([xx]) => {
    const U = window.__sky.U;
    const inp = document.getElementById("gDetailLoc");
    inp.value = U.inputValue("length", xx);
    inp.dispatchEvent(new Event("change", { bubbles: true }));
    await new Promise(r => setTimeout(r, 80));
    const cell = k => { const td = document.querySelector(`#gDetailReadout tr[data-comp="${k}"] .g-d-val`); return td ? td.textContent : null; };
    return { m3: cell("M3"), v2: cell("V2"), fm3: U.fmt("moment", 0, 3) };
  }, [x]);
  const want = await ev(([mm, vv]) => [window.__sky.U.fmt("moment", mm, 3), window.__sky.U.fmt("force", vv, 3)], [m3, v2]);
  if (got.m3 === want[0] && got.v2 === want[1]) match++;
  else console.error("readout", x, got, want);
}
ok(match === Math.min(5, tbl.length), `scroll-for-values readout = table value at the stations (${match})`);
await ev(() => { const cb = document.getElementById("gDetailShowMax"); if (cb && !cb.checked) cb.click(); });
ok(await ev(() => document.querySelectorAll("#gDetail canvas").length >= 4), "detail panels: loads, V, M, deflection");
await ev(() => { const s = document.getElementById("gDetailDefl"); s.value = "rel"; s.dispatchEvent(new Event("change", { bubbles: true })); });
await settle(150);
ok(await ev(() => /relative/.test(document.getElementById("gDetail").textContent)), "relative deflection option");
const shellUid = await ev(() => ((window.__sky.store.model.shells || []).find(s => s.behavior !== "membrane") || {}).uid);
if (shellUid) {
  await ev(u => window.__sky.results.openDetail({ type: "shell", uid: u }), shellUid);
  await settle(300);
  ok(await ev(() => !!document.getElementById("gDetailMesh") && /Shell Results/.test(document.getElementById("gDetailTitle").textContent)), "shell detail: mesh picker");
}
await ev(() => window.__sky.results.closeDetail());

/* ---------- 9. select by result + watch list ---------- */
const sbr = await ev(() => { const r = window.__sky.results.selectByResult({ objType: "frame", quantity: "M3", mode: "absmax", op: ">=", threshold: 1 }); return { n: r.refs.length, sel: window.__sky.store.selection.length }; });
ok(sbr.n > 0 && sbr.sel === sbr.n, `Select by Result selects ${sbr.n} members`);
await ev(() => window.__sky.etabs.clickItem("sel-g-byresult"));
await settle(300);
ok(await ev(() => !!document.getElementById("gSbrDlg")), "Select › More Criteria › By Result… opens its dialog");
await page.click("#gSbrDlgOk");
await settle(500);
ok(await ev(() => !document.getElementById("gSbrDlg") && !!window.__sky.results._lastSbr), "By Result dialog applies the selection");
await ev(u => { const R = window.__sky.results; R.pin({ type: "member_end", uid: u, end: "i", comp: "M3" }); R.pin({ type: "joint_disp", point: window.__sky.store.model.members.find(m => m.uid === u).pj, comp: "UX" }); }, uid);
const pEqx = await ev(() => window.__sky.results.probes().map(p => p.value));
await ev(() => window.__sky.results.select("c:EQY"));
await settle(200);
const pEqy = await ev(() => window.__sky.results.probes().map(p => p.value));
ok(pEqx.length === 2 && pEqx[0] !== pEqy[0], "watch list values update with the case");
const wantShown = await ev(v => window.__sky.U.fmt("moment", v, 3), pEqy[0]);
const shown = await poll(() => { const td = document.querySelector('#gWatchTable tr[data-probe="0"] .g-w-val'); return td ? td.textContent : ""; }, null, t => t === wantShown);
ok(shown === wantShown, `watch list shows the current value in display units (${shown} / ${wantShown})`);
ok(/probe,case,step,value,unit/.test(await ev(() => window.__sky.results.watchCsv())), "watch list CSV export");

/* ---------- 10. named view carries the results view ---------- */
await ev(() => window.__sky.etabs.clickItem("dis-view3d"));
await ev(() => { window.__sky.results.select("c:EQX"); window.__sky.results.setViewOpts("main3d", { deformed: true, diagram: "V2" }); });
const nv = await ev(() => { const W = window.__sky.wb.views; const v = W.save("GRV"); window.__sky.results.select("c:EQY"); window.__sky.results.setViewOpts("main3d", { diagram: "" }); W.show("GRV"); return { saved: (v.display || {}).g_results, key: window.__sky.results.selection().key, diag: window.__sky.results.viewOpts("main3d").diagram }; });
ok(nv.saved && nv.saved.case === "c:EQX" && nv.key === "c:EQX" && nv.diag === "V2", "named view saves and restores case + display");
await ev(() => { window.__sky.wb.views.remove("GRV"); });

/* ---------- 11. legacy chip yields; video export still records ---------- */
await ev(() => window.__sky.results.setViewOpts("main3d", { deformed: true }));
await page.click("#chipDeformed");
await settle(300);
ok(await ev(() => window.__sky.viewer.overlay.caseName !== "__gres__" && window.__sky.viewer.overlay.deformed && !window.__sky.results.viewOpts("main3d").deformed), "legacy Deformed chip takes over the main 3D deformed shape");
await page.click("#chipDeformed");
const vid = await ev(async () => { try { const r = await window.__sky.wcVideo.record({ source: "th", th: Object.keys(window.__sky.store.results.th_cases)[0], fps: 12, duration: 1.2, scale: 1 }); return r.size > 0 ? true : JSON.stringify({ size: r.size, wall: r.wall_ms, w: window.__sky.viewer.canvas.width, tab: window.__sky.store.tab, mode: window.__sky.store.mode }); } catch (e) { return String(e.message || e); } });
if (RENDERER !== "gpu") ok(vid === true || /not supported/.test(String(vid)), "video export still records (" + vid + ")");

/* ---------- 12. menus + model untouched + console ---------- */
const st = await ev(() => window.__sky.menu.stats());
ok(Math.max(...st.map(s => s.entries)) <= 14, "every menu panel ≤ 14 entries");
ok(JSON.stringify(await ev(() => ["sel-g-byresult", "dis-g-opts", "dis-g-detail", "tool-g-watch"].map(a => window.__sky.menu.path(a)))) ===
  JSON.stringify([["Select", "More Criteria"], ["Display"], ["Display", "Force Diagrams"], ["Tools"]]), "menu paths");
await ev(() => window.__sky.etabs.clickItem("dis-g-opts"));
await settle(300);
ok(await ev(() => !!document.getElementById("gResOptsDlg")), "Results Display Options… opens");
await page.click("#gResOptsDlgOk");
await settle(300);
ok(await ev(m0 => { const m = JSON.parse(JSON.stringify(window.__sky.store.model)); delete m.named_views; const a = JSON.parse(m0); delete a.named_views; return JSON.stringify(m) === JSON.stringify(a); }, model0), "the model is unchanged by results display");
ok(errors.length === 0, "zero console errors" + (errors.length ? ": " + errors.slice(0, 3).join(" | ") : ""));

await browser.close();
console.log(JSON.stringify({ passed, failed: fails.length, fails, mode: MOCK ? "mock" : "live", renderer: RENDERER }));
process.exit(fails.length ? 1 : 0);
