/* Wave H — windows, pop-outs and side-by-side compare: Playwright check
   (mock and live, Classic and GPU renderers).

   Usage:  node h_windows.e2e.mjs [baseUrl] [mock|live] [classic|gpu]
   Env:    PW_MODULE   playwright index.mjs (default /opt/node22/lib/node_modules/playwright/index.mjs)
           PW_CHROMIUM chromium executable (default: playwright's own)

   Asserts: View › Windows presets build the old tiles; New Window; splitter
   drag resizes; tab drag onto a group (tab) and onto an edge (split); close;
   window kinds (plan / elevation / 3D / table / plot); per-window display
   options (3D window extrude without touching the main 3D view; plan window
   colour-by) and per-window results case + step; Same as Active Window and
   Apply to All Windows; linked cameras; pop-out through window.open with
   selection, results state and model edits / undo synced both ways, dock
   back and closing the main window closing the pop-out; Compare Side by Side
   (dialog from the menu, linked orbit by mouse, difference window drawn,
   summary chip, End Compare restores the layout); named views save /
   restore the layout, an old wb_windows named view migrates; the layout is
   remembered per project / monitor across a reload; the model is unchanged
   by every window operation; zero console errors.
   Prints "ALL PASSED" or the failures and exits 1. */

const PW = process.env.PW_MODULE || "/opt/node22/lib/node_modules/playwright/index.mjs";
const { chromium } = await import(PW);
const BASE = process.argv[2] || "http://127.0.0.1:8645";
const MOCK = (process.argv[3] || "mock") !== "live";
const RENDERER = process.argv[4] || "classic";
const q = new URLSearchParams();
if (MOCK) q.set("mock", "1");
q.set("renderer", RENDERER);
const URL0 = `${BASE}/?${q}`;
const launch = RENDERER === "gpu" ? { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] } : {};
if (process.env.PW_CHROMIUM) launch.executablePath = process.env.PW_CHROMIUM;
const browser = await chromium.launch(launch);
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
await ctx.addInitScript(() => { try { localStorage.setItem("sky.wc.lock", JSON.stringify({ afterRun: false })); } catch { /* */ } });
const errors = [];
const IGNORE = /favicon|ResizeObserver loop/;
const watch = (p, tag) => {
  p.on("console", m => { if (m.type() === "error" && !IGNORE.test(m.text())) errors.push(`[${tag}] ${m.text()}`); });
  p.on("pageerror", e => errors.push(`[${tag}] ${e.message}`));
};
const page = await ctx.newPage();
watch(page, "main");
let passed = 0;
const fails = [];
const ok = (cond, what) => { if (cond) passed++; else { fails.push(what); console.error("FAIL:", what); } };
const ev = (fn, arg) => page.evaluate(fn, arg);
const settle = (ms = 300) => page.waitForTimeout(ms);
/** Poll fn on page p until pred(value) (sync over channels / slow software GL frames), max ms. */
const until = async (p, fn, pred, ms = 6000, arg) => { const t0 = Date.now(); let v; for (;;) { v = await p.evaluate(fn, arg); if (pred(v) || Date.now() - t0 > ms) return v; await p.waitForTimeout(120); } };

async function boot() {
  await page.goto(URL0);
  await page.waitForFunction(() => window.__sky && window.__sky.store.model && window.__sky.hDock && window.__sky.results, null, { timeout: 60000 });
  await settle(400);
  await page.keyboard.press("Escape");
}
await boot();
if (!MOCK) {                       // a clean server model for this run
  await ev(() => window.__sky.hDock.setPreset("1"));
}
await ev(() => window.__sky.etabs.clickItem("an-run"));
await page.waitForFunction(() => !!window.__sky.store.results, null, { timeout: 180000 });
await settle(500);
const model0 = await ev(() => JSON.stringify(window.__sky.store.model));

/* ---------------------------------------------------------------- presets */
for (const [k, n] of [["2v", 2], ["2h", 2], ["3", 3], ["4", 4]]) {
  await ev(k => window.__sky.etabs.clickItem(`vx-wb-win-${k}`), k);
  await settle(200);
  const r = await ev(() => ({ n: window.__sky.hDock.panes().length, layout: window.__sky.hDock.layout(), host: !!document.querySelector("#wbPanes.h-dock"),
    kinds: window.__sky.hDock.panes().map(w => w.kind), legacy: window.__sky.wb.windows.layout() }));
  ok(r.n === n && r.layout === k && r.host && r.legacy === k, `preset ${k}: ${n} windows (${JSON.stringify(r)})`);
}
ok((await ev(() => window.__sky.hDock.panes().map(w => w.kind).join())) === "plan,3d,elev,plan", "preset 4 kinds as the old tiles");
await ev(() => window.__sky.etabs.clickItem("vx-wb-win-1"));
ok(await ev(() => !document.querySelector("#wbPanes") && window.__sky.hDock.layout() === "1"), "Single Window removes the dock");

/* ---------------------------------------------------------------- new window, splitter, tabs, close */
await ev(() => window.__sky.etabs.clickItem("vx-wb-win-2v"));
await ev(() => window.__sky.etabs.clickItem("vx-h-new"));
await settle(300);
ok(await ev(() => window.__sky.hDock.panes().length === 3 && window.__sky.hDock.L().active === "w3"), "New Window adds window 3 (active)");
{
  const sp = page.locator(".h-splitter").first();
  const b = await sp.boundingBox();
  const s0 = await ev(() => window.__sky.hDock.L().root.sizes.slice());
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2 + 160, b.y + b.height / 2, { steps: 6 });
  await page.mouse.up();
  const s1 = await ev(() => window.__sky.hDock.L().root.sizes.slice());
  ok(s1[0] > s0[0] + 0.05, `splitter drag resizes (${s0} → ${s1})`);
}
async function dragTab(id, targetId, fx, fy) {
  const tab = page.locator(`.h-tab[data-h-tab="${id}"]`);
  const tb = await tab.boundingBox();
  const g = page.locator(`.h-group[data-cur="${targetId}"] .h-group-body`);
  const gb = await g.boundingBox();
  await page.mouse.move(tb.x + 20, tb.y + tb.height / 2);
  await page.mouse.down();
  await page.mouse.move(tb.x + 40, tb.y + 30, { steps: 3 });
  await page.mouse.move(gb.x + gb.width * fx, gb.y + gb.height * fy, { steps: 8 });
  await page.mouse.up();
  await settle(250);
}
await dragTab("w3", "w1", 0.5, 0.5);
ok(await ev(() => JSON.stringify(window.__sky.hDock.L().root).includes('"wins":["w1","w3"]')), "tab drag onto a window adds it as a tab");
await dragTab("w3", "w2", 0.5, 0.95);
ok(await ev(() => { const f = window.__sky.hDock.L().root; return JSON.stringify(f).includes('"dir":"col"') && window.__sky.hDock.panes().length === 3; }), "tab drag onto an edge splits");
await page.locator('.h-tab[data-h-tab="w3"] .h-tab-x').click();
await settle(200);
ok(await ev(() => window.__sky.hDock.panes().map(w => w.id).join() === "w1,w2"), "tab × closes the window");

/* ---------------------------------------------------------------- kinds */
await ev(() => { const d = window.__sky.hDock; d.add({ kind: "table" }); d.add({ kind: "plot" }); d.add({ kind: "elev" }); });
await settle(1200);
{
  const r = await ev(() => { const d = window.__sky.hDock; const t = d.panes().find(w => w.kind === "table"), p = d.panes().find(w => w.kind === "plot");
    return { rows: t.table.grid.rows.length, pts: p.plot.lastPoints || 0, kinds: d.panes().map(w => w.kind).join() }; });
  ok(r.rows > 0, `table window shows rows (${r.rows})`);
  ok(r.pts > 0, `plot window draws the story response (${r.pts} points)`);
  ok(r.kinds.split(",").sort().join() === "3d,elev,plan,plot,table", `window kinds ${r.kinds}`);
}
await page.selectOption('select[data-h-kind="w5"]', "3d");
await settle(300);
ok(await ev(() => window.__sky.hDock.win("w5").kind === "3d" && !!window.__sky.hDock.win("w5").v), "window kind switch (elevation → 3D)");

/* ---------------------------------------------------------------- per-window display options */
{
  const r = await ev(() => {
    const s = window.__sky, D = s.dispOpts;
    s.hDock.activate("w2");
    const main0 = JSON.stringify(s.vx.opts());
    const d = D.get("w2"); d.view.extrude = true; D.set("w2", d);
    const p = D.get("w1"); p.view.colorBy = "section"; D.set("w1", p);
    const wins = D.windows();
    return { w2: s.hDock.win("w2").vx.opts().extrude, main: JSON.stringify(s.vx.opts()) === main0, w5: s.hDock.win("w5").vx.opts().extrude,
      w1: D.get("w1").view.colorBy, plan: D.get("plan").view.colorBy, active: D.activeWin(), wins };
  });
  ok(r.w2 === true && r.main && r.w5 === false, `3D window has its own display options (extrude w2 ${r.w2}, main unchanged ${r.main}, w5 ${r.w5})`);
  ok(r.w1 === "section" && r.plan !== "section", "plan window has its own display options");
  ok(r.active === "w2" && r.wins.includes("w2") && r.wins.includes("w5"), `Set Display Options targets the active window (${r.active})`);
  await page.keyboard.press("Escape");
}

/* ---------------------------------------------------------------- per-window results */
{
  const cat = await ev(() => window.__sky.results.catalog().map(e => e.key));
  const modal = cat.includes("modal");
  await page.selectOption('select[data-h-case="w2"]', cat[1]);
  await settle(300);
  let r = await ev(() => { const s = window.__sky, R = s.results; return { w2: (s.hDock.win("w2").gView().sel || {}).key, glob: R.globalSel().key, w5: s.hDock.win("w5").gView().sel }; });
  ok(r.w2 === cat[1] && !r.w5, `window 2 shows its own case (${r.w2}; toolbar ${r.glob}; window 5 follows the toolbar)`);
  if (modal) {
    await page.selectOption('select[data-h-case="w5"]', "modal");
    await settle(300);
    await page.locator('[data-hwin="w5"] .h-win-step[data-h-step="1"]').click();
    await settle(200);
    r = await ev(() => { const v = window.__sky.hDock.win("w5").gView(); return { step: v.sel.step, lab: document.querySelector('[data-hwin="w5"] .h-win-steplab').textContent }; });
    ok(r.step === 1 && /Mode 2/.test(r.lab), `window step arrows (mode ${r.step + 1}: ${r.lab})`);
  }
  // results toolbar acts on the ACTIVE window (own case)
  await ev(() => window.__sky.hDock.activate("w2"));
  await ev(k => window.__sky.results.select(k), cat[2]);
  r = await ev(() => ({ w2: window.__sky.hDock.win("w2").gView().sel.key, glob: window.__sky.results.globalSel().key }));
  ok(r.w2 === cat[2], `results toolbar changes the active window's own case (${r.w2})`);
  // Same as Active Window (w1 ← w2) via the window menu
  await ev(() => window.__sky.results.setViewOpts("pane_w2", { deformed: true }));
  await ev(() => window.__sky.hDock.activate("w2"));
  await page.locator('[data-h-more="w1"]').click();
  ok(/\(2\)/.test(await page.locator('.h-menu-item[data-h-act="h-same"]').textContent()), "window 1 menu: Same as Active Window refers to window 2");
  await page.locator('.h-menu-item[data-h-act="h-same"]').click();
  await settle(200);
  r = await ev(() => { const v = window.__sky.hDock.win("w1").gView(); return { key: v.sel && v.sel.key, def: v.opts.deformed }; });
  ok(r.key === cat[2] && r.def === true, `Same as Active Window (${JSON.stringify(r)})`);
  // Apply to all (menu)
  await ev(() => window.__sky.hDock.activate("w2"));
  await ev(() => window.__sky.etabs.clickItem("vx-h-applyall"));
  await settle(200);
  r = await ev(() => { const s = window.__sky; return { own: s.hDock.wins().filter(w => w.gView() && w.gView().sel).length, glob: s.results.globalSel().key, def: s.hDock.wins().filter(w => w.v).every(w => w.gView().opts.deformed) }; });
  ok(r.own === 0 && r.glob === cat[2] && r.def, `Apply Active Window to All (${JSON.stringify(r)})`);
  // drawn in a pane after apply-to-all
  await settle(500);
  const drawn = await ev(() => { const s = window.__sky, w = s.hDock.win("w2"), v = s.results.view(w.gId); return { on: !!v.deformedOn, dmap: Object.keys(v.cache.dmap || {}).length }; });
  ok(drawn.on && drawn.dmap > 0, `window 2 draws the deformed shape (${JSON.stringify(drawn)})`);
}

/* ---------------------------------------------------------------- linked cameras */
{
  await ev(() => window.__sky.etabs.clickItem("vx-h-linkcam"));
  ok(await ev(() => window.__sky.hDock.allLinked()), "Link 3D Window Cameras links every 3D window");
  const c = page.locator('[data-hwin="w2"] canvas.h-win-canvas');
  const b = await c.boundingBox();
  const y0 = await ev(() => window.__sky.hDock.win("w5").v.yaw);
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2 + 120, b.y + b.height / 2 + 10, { steps: 8 });
  await page.mouse.up();
  await ev(() => { const v = window.__sky.hDock.win("w2").v; v.vyaw = v.vpitch = 0; });   // stop the inertia (software GL runs a few frames / s)
  const r = await until(page, () => ({ a: window.__sky.hDock.win("w2").v.yaw, b: window.__sky.hDock.win("w5").v.yaw }), x => Math.abs(x.a - x.b) < 1e-9);
  ok(Math.abs(r.a - r.b) < 1e-9 && Math.abs(r.b - y0) > 1e-3, `orbiting window 2 orbits window 5 (${r.a.toFixed(4)} / ${r.b.toFixed(4)})`);
  await ev(() => window.__sky.etabs.clickItem("vx-h-linkcam"));
  ok(await ev(() => !window.__sky.hDock.allLinked()), "unlink");
}

/* ---------------------------------------------------------------- pop-out (browser: window.open) */
{
  const popP = ctx.waitForEvent("page");
  await page.locator('[data-h-more="w2"]').click();
  await page.locator('.h-menu-item[data-h-act="h-popout"]').click();
  const pop = await popP;
  watch(pop, "pop");
  await pop.waitForFunction(() => window.__sky && window.__sky.hSync && window.__sky.hSync.ready(), null, { timeout: 60000 });
  const pe = (fn, a) => pop.evaluate(fn, a);
  let r = await pe(() => ({ wins: window.__sky.hDock.panes().map(w => w.id + ":" + w.kind), extrude: window.__sky.hDock.win("w2").vx.opts().extrude, full: !!document.querySelector(".h-popout-host") }));
  ok(r.wins.join() === "w2:3d" && r.full, `pop-out shows window 2 alone (${r.wins})`);
  ok(r.extrude === true, "pop-out keeps the window's display options");
  ok(await ev(() => !window.__sky.hDock.win("w2") && !!window.__sky.hDock.L().popped.w2), "main: window 2 is popped (not docked)");
  // selection both ways
  await ev(() => { const m = window.__sky.store.model.members[0]; window.__sky.handleSelect([{ type: "member", uid: m.uid }], false); });
  const uid0 = await ev(() => window.__sky.store.model.members[0].uid);
  ok((await until(pop, () => window.__sky.store.selection.map(s => s.uid).join(), v => v === uid0)) === uid0, "selection main → pop-out");
  const uid3 = await pe(() => { const m = window.__sky.store.model.members[3]; window.__sky.handleSelect([{ type: "member", uid: m.uid }], false); return m.uid; });
  ok((await until(page, () => window.__sky.store.selection.map(s => s.uid).join(), v => v === uid3)) === uid3, "selection pop-out → main");
  // results state both ways
  const cat = await ev(() => window.__sky.results.catalog().map(e => e.key));
  await ev(k => { window.__sky.hDock.activate("w1"); window.__sky.results.setGlobalSel({ key: k, step: -1, env: "step" }); }, cat[0]);
  ok((await until(pop, () => window.__sky.results.globalSel().key, v => v === cat[0])) === cat[0], "results toolbar state main → pop-out");
  await pe(k => window.__sky.results.setGlobalSel({ key: k, step: -1, env: "step" }), cat[3]);
  ok((await until(page, () => window.__sky.results.globalSel().key, v => v === cat[3])) === cat[3], "results toolbar state pop-out → main");
  await pop.selectOption('select[data-h-case="w2"]', cat[1]);
  ok((await until(page, () => ((window.__sky.hDock.L().wins.w2.res || {}).sel || {}).key, v => v === cat[1])) === cat[1], "pop-out window's own results case reaches main");
  // model edit both ways + undo in the main history
  await pe(() => { const S = window.__sky.store; S.model.h_e2e_probe = 1; window.__sky.markDirty(); });
  ok((await until(page, () => window.__sky.store.model.h_e2e_probe, v => v === 1)) === 1, "model edit pop-out → main");
  await ev(() => { const S = window.__sky.store; S.model.h_e2e_probe2 = 2; window.__sky.markDirty(); });
  ok((await until(pop, () => window.__sky.store.model.h_e2e_probe2, v => v === 2)) === 2, "model edit main → pop-out");
  await pop.locator("body").click({ position: { x: 5, y: 300 } }).catch(() => {});
  await pop.keyboard.press("Control+z");
  await until(page, () => window.__sky.store.model.h_e2e_probe2, v => v === undefined);
  await until(pop, () => window.__sky.store.model.h_e2e_probe2, v => v === undefined);
  r = { main: await ev(() => window.__sky.store.model.h_e2e_probe2), pop: await pe(() => window.__sky.store.model.h_e2e_probe2) };
  ok(r.main === undefined && r.pop === undefined, `Ctrl+Z in the pop-out undoes in the main history, both updated (${JSON.stringify(r)})`);
  await ev(() => { window.__sky.history.undo(); });
  await settle(400);
  ok((await ev(() => window.__sky.store.model.h_e2e_probe)) === undefined, "undo the pop-out's edit in main");
  const st = await pe(() => window.__sky.hSync.stats());
  ok(st.recv > 5, `messages over ${st.relay ? "the server relay" : "BroadcastChannel"} (${st.recv} received)`);
  // dock back from the pop-out
  const closed = pop.waitForEvent("close");
  await pop.locator('[data-h-more="w2"]').click();
  await pop.locator('.h-menu-item[data-h-act="h-dock-back"]').click();
  await closed;
  await settle(500);
  r = await ev(() => ({ docked: !!window.__sky.hDock.win("w2"), popped: Object.keys(window.__sky.hDock.L().popped), key: ((window.__sky.hDock.win("w2") || { gView: () => null }).gView() || {}).sel }));
  ok(r.docked && !r.popped.length && r.key && r.key.key === cat[1], `dock back restores window 2 with its own case (${JSON.stringify(r)})`);
  // OS close of a pop-out docks it back; remembered rectangle
  const p2P = ctx.waitForEvent("page");
  await ev(() => window.__sky.hSync.popout("w5"));
  const p2 = await p2P;
  watch(p2, "pop2");
  await p2.waitForFunction(() => window.__sky && window.__sky.hSync && window.__sky.hSync.ready(), null, { timeout: 60000 });
  await p2.waitForTimeout(2300);                             // one heartbeat with the rectangle
  await p2.close();
  await page.waitForFunction(() => !!window.__sky.hDock.win("w5"), null, { timeout: 10000 }).catch(() => {});
  r = await ev(() => ({ back: !!window.__sky.hDock.win("w5"), rect: window.__sky.hSync.rectFor("w5", "3d"), saved: JSON.parse(localStorage.getItem("skyframe.h.popouts.v1") || "{}") }));
  ok(r.back, "closing a pop-out window docks it back");
  ok(Object.values(r.saved).some(m => m.w5 && m.w5.w > 0), `pop-out rectangle remembered per monitor setup (${JSON.stringify(r.rect)})`);
}

/* ---------------------------------------------------------------- named views */
{
  const before = await ev(() => ({ ids: window.__sky.hDock.panes().map(w => w.id + ":" + w.kind).join(), sizes: JSON.stringify(window.__sky.hDock.L().root.sizes) }));
  await ev(() => window.__sky.wb.views.save("H layout"));
  await ev(() => window.__sky.etabs.clickItem("vx-wb-win-1"));
  ok(await ev(() => window.__sky.hDock.layout() === "1"), "single window before restore");
  await ev(() => window.__sky.wb.views.show("H layout"));
  await settle(400);
  const after = await ev(() => ({ ids: window.__sky.hDock.panes().map(w => w.id + ":" + w.kind).join(), sizes: JSON.stringify(window.__sky.hDock.L().root.sizes),
    ext: window.__sky.hDock.win("w2").vx.opts().extrude, own: (window.__sky.hDock.win("w2").gView().sel || {}).key }));
  ok(after.ids === before.ids && after.sizes === before.sizes, `named view restores the layout (${after.ids})`);
  ok(after.ext === true && !!after.own, "named view restores per-window display options and results case");
  // an old named view with wb_windows (fixed tiles) migrates
  await ev(() => { const S = window.__sky.store; S.model.named_views.push({ name: "Old", view: "plan", story: S.model.stories[0].name, camera: { cx: 0, cy: 0, scale: 30 },
    display: { wb_windows: { layout: "3", panes: [{ kind: "3d" }, { kind: "plan", story: S.model.stories[0].name }, { kind: "elev", line: "y:0" }] } } }); });
  await ev(() => window.__sky.wb.views.show("Old"));
  await settle(300);
  ok((await ev(() => window.__sky.hDock.layout() + ":" + window.__sky.hDock.panes().map(w => w.kind).join())) === "3:3d,plan,elev", "old 1–4 pane named view migrates");
  await ev(() => { const S = window.__sky.store; S.model.named_views = S.model.named_views.filter(v => v.name !== "Old" && v.name !== "H layout"); if (!S.model.named_views.length) delete S.model.named_views; window.__sky.markDirty(); });
  await settle(50);
}

/* ---------------------------------------------------------------- compare side by side */
{
  await ev(() => window.__sky.etabs.clickItem("vx-wb-win-2v"));
  const cat = await ev(() => window.__sky.results.catalog().map(e => e.key));
  await ev(() => window.__sky.etabs.clickItem("vx-h-compare"));
  await page.waitForSelector("#hCompareDlg", { timeout: 10000 });
  await page.selectOption("#hCmpA", cat[0]);
  await page.selectOption("#hCmpB", cat[2]);
  await page.selectOption("#hCmpQty", "U");
  await page.click("#hCompareDlgOk");
  await page.waitForFunction(() => window.__sky.hCompare.active() && window.__sky.hDock.panes().length === 3, null, { timeout: 15000 });
  await settle(1500);
  let r = await ev(() => { const d = window.__sky.hDock, C = window.__sky.hCompare; const [a, b, x] = d.panes();
    return { roles: d.panes().map(w => w.cmp && w.cmp.role).join(), links: d.L().links, sa: a.gView().sel.key, sb: b.gView().sel.key, chip: (x.head.querySelector(".h-cmp-sum") || {}).textContent || "",
      n: Object.keys((C.diffState() || {}).node_disp || {}).length, srv: C.state.serverUsed }; });
  ok(r.roles === "a,b,diff" && r.links.length === 1 && r.links[0].length === 3, `compare: A, B and difference windows, cameras linked (${r.roles})`);
  ok(r.sa === cat[0] && r.sb === cat[2], `compare windows show ${r.sa} and ${r.sb}`);
  ok(r.n > 0 && /ΔU|Δ max/.test(r.chip), `difference window: ${r.n} joint differences, summary "${r.chip}"`);
  if (!MOCK) ok(r.srv, "live: POST /api/results/compare/fields used");
  const c = page.locator('[data-hwin="w1"] canvas.h-win-canvas');
  const b = await c.boundingBox();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2 - 100, b.y + b.height / 2 + 30, { steps: 8 });
  await page.mouse.up();
  await ev(() => { const v = window.__sky.hDock.win("w1").v; v.vyaw = v.vpitch = 0; });
  r = await until(page, () => window.__sky.hDock.panes().map(w => w.v.yaw.toFixed(9) + "/" + w.v.pitch.toFixed(9)), x => x[0] === x[1] && x[1] === x[2]);
  ok(r[0] === r[1] && r[1] === r[2], `linked orbit in the compare view (${r.join(" ")})`);
  await settle(600);
  const drawn = await ev(() => { const s = window.__sky, x = s.hDock.panes()[2]; return { cells: (s.results.drawnIn(x.gId) || {}).cells || 0, gl: s.results.glCells(x.gId) || 0 }; });
  ok(drawn.cells > 0 || drawn.gl > 0, `difference contour drawn (${JSON.stringify(drawn)})`);
  // a pixel check: the difference window canvas is not blank
  const px = await ev(() => { const x = window.__sky.hDock.panes()[2]; const cv = x.v.canvas; try { const ctx2 = cv.getContext("2d"); if (!ctx2) return -1; const d = ctx2.getImageData(0, 0, cv.width, cv.height).data; let n = 0; for (let i = 0; i < d.length; i += 4 * 97) if (d[i] + d[i + 1] + d[i + 2] > 60) n++; return n; } catch { return -1; } });
  ok(px !== 0, `difference window has pixels (${px})`);
  // force-difference diagram
  await ev(k => window.__sky.hCompare.start({ mode: "cases", a: k[0], b: k[2], qty: "M3", diff: true }), cat);
  await settle(800);
  r = await ev(() => { const s = window.__sky, x = s.hDock.panes()[2]; return { diag: x.gView().opts.diagram, st: Object.keys((s.hCompare.diffState() || {}).member_stations || {}).length }; });
  ok(r.diag === "M3" && r.st > 0, `force difference diagram (M3 on ${r.st} members)`);
  await ev(() => window.__sky.etabs.clickItem("vx-h-compare"));
  await page.waitForSelector("#hCmpEnd:not([disabled])");
  await page.click("#hCmpEnd");
  await settle(300);
  ok(await ev(() => !window.__sky.hCompare.active() && window.__sky.hDock.layout() === "2v"), "End Compare restores the previous layout");
  // model version (checkpoint) compare
  const ck = await ev(async () => { try { const r = await window.__sky.mhistory.checkpoint("h-e2e", ""); return r && (r.checkpoint || r).id; } catch (e) { return "ERR " + e.message; } });
  if (ck && !String(ck).startsWith("ERR")) {
    await ev(() => { const S = window.__sky.store; S.model.members[0].pj = S.model.members[0].pj.map((v, i) => (i === 0 ? v + 0.5 : v)); window.__sky.markDirty(); });
    await ev(c => window.__sky.hCompare.start({ mode: "ckpt", ckpt: c, diff: true }), ck);
    await settle(800);
    r = await ev(() => { const d = window.__sky.hDock; const [a, b, x] = d.panes(); return { fixed: !!a.v.hFixedModel, chip: (x.head.querySelector(".h-cmp-sum") || {}).textContent || "", drawn: x.hDiffDrawn || 0 }; });
    ok(r.fixed && /~1|~2/.test(r.chip) && r.drawn > 0, `model version compare: checkpoint geometry + model diff (${r.chip})`);
    await ev(() => window.__sky.history.undo());
    await ev(() => window.__sky.hCompare.end());
  } else ok(false, `checkpoint for the model-version compare (${ck})`);
}

/* ---------------------------------------------------------------- remembered per project / monitor; model unchanged */
{
  await ev(() => { const d = window.__sky.hDock; d.setPreset("3"); d.persistNow(); });
  const model1 = await ev(() => JSON.stringify(window.__sky.store.model));
  const diffKeys = await ev(m0 => { const a = JSON.parse(m0), b = window.__sky.store.model; return [...new Set([...Object.keys(a), ...Object.keys(b)])].filter(k => JSON.stringify(a[k]) !== JSON.stringify(b[k])); }, model0);
  ok(model1 === model0, `window operations leave the model unchanged (named views removed again) ${diffKeys.join(",")}`);
  const ls = await ev(() => JSON.parse(localStorage.getItem("skyframe.h.layouts.v1") || "{}"));
  ok(Object.keys(ls).length >= 1, "layout stored per project and monitor setup (localStorage, not the model)");
  if (!MOCK) {
    const srv = await ev(async () => JSON.stringify(await (await fetch("/api/model")).json()));
    ok(!srv.includes("h_dock") && !srv.includes("wb_windows"), "server model has no window layout");
  }
  await boot();
  await settle(500);
  ok(await ev(() => window.__sky.hDock.layout() === "3" && window.__sky.hDock.panes().length === 3), "layout restored after a reload (same project, same monitors)");
  // closing the main window closes its pop-outs
  await ev(() => window.__sky.etabs.clickItem("an-run"));
  await page.waitForFunction(() => !!window.__sky.store.results, null, { timeout: 180000 });
  const pP = ctx.waitForEvent("page");
  await ev(() => window.__sky.hSync.popout("w2"));
  const p3 = await pP;
  watch(p3, "pop3");
  await p3.waitForFunction(() => window.__sky && window.__sky.hSync && window.__sky.hSync.ready(), null, { timeout: 60000 });
  const closed = p3.waitForEvent("close", { timeout: 10000 }).then(() => true).catch(() => false);
  await ev(() => window.__sky.hDock.setPreset("1"));
  await page.goto("about:blank");
  ok(await closed, "closing / leaving the main window closes the pop-out");
}

/* ---------------------------------------------------------------- the --selftest-windows page logic (browser: window.open) */
{
  const st = await ctx.newPage();
  watch(st, "selftest");
  await st.goto(`${BASE}/?mock=1&hselftest=1&renderer=${RENDERER}`);
  const rep = await st.waitForFunction(() => window.__hSelftest, null, { timeout: 90000 }).then(h => h.jsonValue()).catch(e => ({ ok: false, error: e.message }));
  ok(rep.ok, `self-test page: pop-out handshake + selection round trip (${JSON.stringify(rep)})`);
  await st.close();
}

console.log(`\n${MOCK ? "mock" : "live"} ${RENDERER}: ${passed} passed, ${fails.length} failed`);
if (errors.length) console.log("console errors:", errors.slice(0, 10));
ok(errors.length === 0, `zero console errors (${errors.length})`);
await browser.close();
if (fails.length) { console.log("FAILURES:\n" + fails.join("\n")); process.exit(1); }
console.log("ALL PASSED");
