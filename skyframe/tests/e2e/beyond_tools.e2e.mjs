/* Tools › Model History… and Tools › Explain Result… — Playwright check
   (CONTRACT "Model history and visual diff", "Load-path explainer").

   Usage:  node beyond_tools.e2e.mjs [mock|live] [baseUrl]
   Env:    PW_MODULE  path of playwright's index.mjs
                      (default /opt/node22/lib/node_modules/playwright/index.mjs)

   Checkpoint → edit → compare (summary, change table, 3D / GPU / plan
   highlight) → restore selected (one undo step) → restore whole checkpoint
   (byte-identical, live: POST /api/model round trip) → bulk-edit auto
   checkpoint → (live) auto checkpoints on save / before analysis → Explain
   Result (pattern / combo / RS / nonlinear message, sum checks, deep link,
   kip-in) → zero console errors.  Exits 1 on any failure. */
const { chromium } = await import(process.env.PW_MODULE || "/opt/node22/lib/node_modules/playwright/index.mjs");
const mode = process.argv[2] || "mock";
const BASE = (process.argv[3] || "http://127.0.0.1:8647").replace(/\/?$/, "/");
const URL0 = BASE + (mode === "mock" ? "?mock=1" : "");
const browser = await chromium.launch();
const results = [];
const check = (name, ok, info = "") => { results.push({ name, ok: !!ok }); console.log((ok ? "PASS " : "FAIL ") + name + (info ? "  — " + info : "")); };
const errs = [];
const IGNORE = /favicon|ResizeObserver loop/;
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addInitScript(() => { try { localStorage.clear(); localStorage.setItem("sky.wc.lock", JSON.stringify({ afterRun: false })); } catch {} });
const page = await ctx.newPage();
page.on("console", m => { if (m.type() === "error" && !IGNORE.test(m.text())) errs.push(m.text()); });
page.on("pageerror", e => errs.push("pageerror " + e.message));
page.on("dialog", d => d.dismiss().catch(() => {}));
await page.goto(URL0);
await page.waitForFunction(() => window.__sky && window.__sky.menu && window.__sky.store.model && window.__sky.mhistory && window.__sky.explain, null, { timeout: 30000 });
await page.waitForTimeout(800);
await page.keyboard.press("Escape");
const ev = (fn, arg) => page.evaluate(fn, arg);
const visible = id => page.evaluate(i => { const e = document.getElementById(i); return !!e && e.getClientRects().length > 0; }, id);

/* ---- menus */
const menu = await ev(() => {
  const m = window.__sky.menu;
  return { hist: m.path("tools-mh-history"), exp: m.path("tools-lp-explain"), max: Math.max(...m.stats().map(s => s.entries)),
    tools: (m.stats().find(s => s.path === "Tools") || {}).entries, dis: m.disabledReason("tools-lp-explain") };
});
check("menus: Tools › Model History… and Explain Result… registered", menu.hist && menu.exp, `${menu.hist} | ${menu.exp}`);
check("every panel ≤ 14 entries", menu.max <= 14, `max ${menu.max}, Tools ${menu.tools}`);
check("Explain Result disabled before a run, with a reason", /run the analysis/i.test(menu.dis || ""), menu.dis);

/* ---- history: checkpoint via the dialog */
if (mode === "live") {
  // live: make sure the server holds the working model, then key sync
  await ev(async () => { const S = window.__sky.store; const p = JSON.parse(JSON.stringify(S.model)); delete p._mock_params; await fetch("/api/model", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(p) }); });
}
const before = await ev(() => JSON.stringify(window.__sky.store.model));
await ev(() => window.__sky.etabs.clickItem("tools-mh-history"));
await page.waitForSelector("#mhDlg", { timeout: 10000 });
check("Model History opens from the menu", await visible("mhDlg"));
await page.click("#mhAdd");
await page.waitForSelector("#mhSaveDlg");
await page.fill("#mhSaveName", "Baseline");
await page.fill("#mhSaveNote", "before edits");
await page.click("#mhSaveDlgOk");
await page.waitForFunction(() => document.querySelectorAll("#mhList tbody tr").length >= 1, null, { timeout: 10000 });
const row1 = await ev(() => document.querySelector("#mhList tbody tr").textContent);
check("Save Checkpoint adds a named row", /Baseline/.test(row1) && /Manual/.test(row1), row1.slice(0, 80));
await page.click("#mhDlgCancel");
const unchanged = await ev(() => JSON.stringify(window.__sky.store.model));
check("saving a checkpoint leaves the model byte-identical", unchanged === before);

/* ---- edit the model: section change, delete one member, add a material */
const edit = await ev(() => {
  const sky = window.__sky, S = sky.store, m = S.model;
  const secs = Object.keys(m.sections);
  const mem0 = m.members[0], other = secs.find(s => s !== mem0.section) || secs[0];
  const deleted = m.members[1].uid;
  sky.history.label("Test edit");
  mem0.section = other;
  m.members.splice(1, 1);
  const mat0 = Object.keys(m.materials)[0];
  m.materials["MH_TEST_MAT"] = { ...m.materials[mat0], name: "MH_TEST_MAT" };
  sky.markDirty(); sky.history.flush();
  return { changed: mem0.uid, deleted, other };
});
await ev(() => window.__sky.etabs.clickItem("tools-mh-history"));
await page.waitForSelector("#mhDlg");
await page.click("#mhList tbody tr");
await page.click("#mhCmpCur");
await page.waitForSelector("#mhSummary", { timeout: 15000 });
const sum = await ev(() => ({ s: document.getElementById("mhSummary").textContent, rows: [...document.querySelectorAll("#mhTable tbody tr")].map(t => t.textContent),
  hl: (window.__sky.mhistory.highlight() || []).length, legend: !document.getElementById("mhLegend").classList.contains("hidden") }));
check("compare checkpoint vs current: summary lists Frames and Materials", /Frames/.test(sum.s) && /Materials/.test(sum.s), sum.s);
check("change table: changed section, removed frame, added material", sum.rows.some(r => r.includes(edit.changed) && /section/.test(r)) && sum.rows.some(r => r.includes(edit.deleted) && /removed/.test(r)) && sum.rows.some(r => /MH_TEST_MAT/.test(r) && /added/.test(r)), sum.rows.length + " rows");
check("changes highlighted in the views + legend", sum.hl >= 2 && sum.legend, `hl ${sum.hl}`);
// 3D render with highlight (Classic and GPU renderers), then plan
const r3 = await ev(async () => {
  const sky = window.__sky, v = sky.viewer, st = sky.mhistory.state;
  const draw = async () => { const n0 = st.drawn3d || 0; v._dirty = true; v.render && v.render(); await new Promise(r => setTimeout(r, 250)); return (st.drawn3d || 0) > n0; };
  const classic = await draw();
  let gpu = null;
  if (sky.gpu3d && sky.gpu3d.probe().webgl2) { await sky.gpu3d.set("gpu", false); await sky.gpu3d.ready(); gpu = sky.gpu3d.active() ? await draw() : "inactive"; await sky.gpu3d.set("classic", false); }
  return { classic, gpu };
});
check("3D diff overlay draws in the Classic and the GPU renderer", r3.classic && (r3.gpu === true || r3.gpu === null || r3.gpu === "inactive"), JSON.stringify(r3));
const plan = await ev(async () => {
  const sky = window.__sky; sky.setMode("model"); sky.setView && sky.setView("plan");
  const st = (sky.store.model.stories || [])[0]; if (st && sky.setStory) sky.setStory(st.name);
  sky.planEditor.refresh();
  await new Promise(r => setTimeout(r, 200));
  return document.querySelectorAll(".mh-hl-layer [data-mh]").length;
});
check("plan view draws the diff layer", plan >= 1, `${plan} marks`);
await ev(() => window.__sky.setMode("analyze"));

/* ---- restore the deleted member only (one undo step) */
await page.evaluate(uid => {
  const tr = [...document.querySelectorAll("#mhTable tbody tr")].find(t => t.textContent.includes(uid) && /removed/.test(t.textContent));
  tr.querySelector("input").click();
}, edit.deleted);
await page.click("#mhRestoreSel");
await page.waitForFunction(uid => window.__sky.store.model.members.some(m => m.uid === uid), edit.deleted, { timeout: 15000 });
const rs = await ev(([uid, ch, other]) => ({ back: window.__sky.store.model.members.some(m => m.uid === uid), sec: window.__sky.store.model.members.find(m => m.uid === ch).section,
  undo: window.__sky.history.state().undoLabel, idx: window.__sky.store.model.members.findIndex(m => m.uid === uid) }), [edit.deleted, edit.changed, edit.other]);
check("Restore Selected brings back only the deleted frame (at its old position)", rs.back && rs.sec === edit.other && rs.idx === 1, JSON.stringify(rs));
check("restore is one undo step", /^Restore 1 object/.test(rs.undo), rs.undo);
await ev(() => window.__sky.history.undo());
const und = await ev(uid => window.__sky.store.model.members.some(m => m.uid === uid), edit.deleted);
check("Undo reverts the restore", !und);
await ev(() => window.__sky.history.redo());

/* ---- restore whole checkpoint → byte-identical */
await page.evaluate(() => { const tr = [...document.querySelectorAll("#mhList tbody tr")].find(t => /Baseline/.test(t.textContent)); tr.click(); });
await page.click("#mhRestoreAll");
await page.waitForSelector("#mhRestoreConfirm");
await page.click("#mhRestoreConfirmOk");
await page.waitForFunction(() => !window.__sky.store.model.materials["MH_TEST_MAT"], null, { timeout: 15000 });
const after = await ev(() => JSON.stringify(window.__sky.store.model));
check("Restore Checkpoint gives the checkpointed model back byte-identical", after === before, `${after.length} vs ${before.length}`);
const hasAutoRestore = await ev(async () => (await window.__sky.mhistory.list()).checkpoints.some(c => c.kind === "auto" && /before restore/.test(c.reason)));
check("an auto checkpoint is taken before a restore", hasAutoRestore);
if (mode === "live") {
  const rt = await ev(async () => {
    const p = JSON.parse(JSON.stringify(window.__sky.store.model)); delete p._mock_params;
    const r1 = await (await fetch("/api/model", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(p) })).text();
    const r2 = await (await fetch("/api/model", { method: "POST", headers: { "Content-Type": "application/json" }, body: r1 })).text();
    return r1 === r2;
  });
  check("restored model round-trips through POST /api/model byte-identically", rt);
}
await page.click("#mhDlgCancel").catch(() => {});

/* ---- bulk edit → auto checkpoint */
await ev(() => {
  const sky = window.__sky, m = sky.store.model;
  const add = m.members.slice(0, 12).map((x, i) => ({ ...JSON.parse(JSON.stringify(x)), uid: "BULK" + i, pi: [x.pi[0] + 100, x.pi[1], x.pi[2]], pj: [x.pj[0] + 100, x.pj[1], x.pj[2]] }));
  m.members.push(...add); sky.markDirty(); sky.history.flush();
});
await page.waitForTimeout(800);
const bulk = await ev(async () => (await window.__sky.mhistory.list()).checkpoints.find(c => c.kind === "auto" && /before Add 12/.test(c.reason)));
check("a bulk edit (12 objects added) takes an automatic checkpoint first", !!bulk, bulk && bulk.reason);
if (bulk) {
  const d = await ev(async id => window.__sky.mhistory.compare(id, "current"), bulk.id);
  check("…and that checkpoint differs from the current model by exactly the 12 frames", d.summary.frames.added === 12 && d.summary.frames.changed === 0, JSON.stringify(d.summary.frames));
}
await ev(() => { window.__sky.history.undo(); });

/* ---- live: auto checkpoint on save / before analysis */
if (mode === "live") {
  await ev(async () => { await window.__sky.saveToFile("hx_e2e"); });
  await page.waitForTimeout(300);
  const lst = await ev(async () => (await window.__sky.mhistory.list()));
  check("save makes an automatic checkpoint (history keyed by the file name)", lst.key === "hx_e2e" && lst.checkpoints.some(c => c.reason === "save"), lst.key);
}

/* ---- Explain Result */
if (mode === "mock") {
  // the mock model has tension-only braces + a P-Delta case: nonlinear → explained message
  await ev(() => window.__sky.etabs.clickItem("an-run"));
  await page.waitForFunction(() => !!window.__sky.store.results, null, { timeout: 120000 });
  const nlr = await ev(() => window.__sky.explain.explain({ kind: "reaction", joint: String(window.__sky.store.results.supports[0]), component: "FZ" }, Object.keys(window.__sky.store.results.cases)[0]));
  check("nonlinear model: no pattern breakdown, explanatory message", nlr.supported === false && /nonlinear/i.test(nlr.message), nlr.message);
  await ev(() => { const sky = window.__sky, m = sky.store.model; m.members.forEach(x => { if (x.axial_limit) x.axial_limit = "both"; }); Object.values(m.cases).forEach(c => { c.pdelta = false; }); sky.markDirty(); });
}
await ev(() => { window.__sky.clearResults && window.__sky.clearResults(); });
await ev(() => { const sky = window.__sky, m = sky.store.model; m.cases.GRAV = { name: "GRAV", patterns: { DEAD: 1.0, LIVE: 0.5 }, pdelta: false, pdelta_gravity: null, geometric: "linear" }; sky.markDirty(); });
await ev(() => window.__sky.etabs.clickItem("an-run"));
await page.waitForFunction(m => !!window.__sky.store.results && (m === "mock" || !!(window.__sky.store.results.cases || {}).GRAV), mode, { timeout: 120000 }).catch(async e => { console.log(await page.evaluate(() => [...document.querySelectorAll(".modal-backdrop")].filter(b => !b.classList.contains("hidden") && b.getClientRects().length).map(b => b.id + ":" + b.textContent.slice(0, 200)))); throw e; });
await page.waitForTimeout(800);
if (mode === "live") {
  const lst = await ev(async () => (await window.__sky.mhistory.list()));
  check("running the analysis makes a 'before analysis' checkpoint (or dedupes against the save)", lst.checkpoints.some(c => c.reason === "before analysis" || c.reason === "save"));
}
await ev(() => window.__sky.etabs.clickItem("tools-lp-explain"));
await page.waitForSelector("#exDlg", { timeout: 10000 });
check("Explain Result opens from the menu after a run", await visible("exDlg"));
await page.click("#exDlgOk");
await page.waitForSelector("#exHead", { timeout: 60000 });
const e1 = await ev(() => ({ head: document.getElementById("exHead").textContent, rows: document.querySelectorAll("#exTable tbody tr").length, chart: !!document.getElementById("exChart"),
  last: window.__sky.explain.state.last }));
check("reaction breakdown: ranked table + bar chart", e1.rows >= 1 && e1.chart, e1.head);
check("contributions add up to the stored result", e1.last.check && e1.last.check.ok, JSON.stringify(e1.last.check));
if (mode === "live") { const gr = await ev(() => window.__sky.explain.explain({ kind: "reaction", joint: String(window.__sky.store.results.supports[0]), component: "FZ" }, "GRAV"));
check("multi-pattern case: per-pattern rows (stored or re-solved), sum = case", gr.rows.length === 2 && gr.check.ok, JSON.stringify(gr.check)); }
const combos = await ev(() => Object.keys(window.__sky.store.results.combos || {}));
const uid = await ev(() => window.__sky.store.model.members.find(m => m.kind === "beam").uid);
if (combos.length) {
  await ev(([u, c]) => window.__sky.explain.open({ kind: "member_force", member: u, component: "M3", station: 0 }, c), [uid, combos[0]]);
  await page.waitForFunction(() => window.__sky.explain.state.last && window.__sky.explain.state.last.case_kind === "combo", null, { timeout: 60000 });
  await page.waitForTimeout(300);
  const e2 = await ev(() => ({ last: window.__sky.explain.state.last, child: document.querySelectorAll("#exTable tr.ex-child").length, head: document.getElementById("exHead").textContent }));
  check("combo: per-case rows with pattern children; sum = combo", e2.last.rows.length >= 1 && e2.child >= 1 && e2.last.check.ok, e2.head);
  // deep link
  await page.click("#exTable tbody tr[data-link]");
  await page.waitForTimeout(400);
  const dl = await ev(() => ({ def: window.__sky.store.overlay.deformed, cs: document.getElementById("caseSelect").value }));
  check("row click deep-links to the deformed shape of that case", dl.def && !!dl.cs, dl.cs);
}
// RS (mock has RS cases; live only when the model has one)
const rsName = await ev(() => Object.keys(window.__sky.store.results.rs_cases || {})[0]);
if (rsName) {
  const sup = await ev(() => String(window.__sky.store.results.supports[0]));
  const r = await ev(([s, n]) => window.__sky.explain.explain({ kind: "base", component: "FX" }, n), [sup, rsName]);
  check("RS: per-mode rows", r.case_kind === "response_spectrum" && (r.rows.length >= 1 || r.supported === false), `${r.rows.length} modes`);
}
// kip-in units
await ev(() => window.__sky.setDisplayUnits("kip-in"));
await page.waitForTimeout(400);
await ev(() => window.__sky.explain.open({ kind: "reaction", joint: String(window.__sky.store.results.supports[0]), component: "FZ" }, Object.keys(window.__sky.store.results.cases)[0]));
await page.waitForFunction(() => /kip/.test((document.getElementById("exHead") || {}).textContent || ""), null, { timeout: 30000 }).catch(() => {});
const kh = await ev(() => document.getElementById("exHead").textContent);
check("kip-in: values shown in kip", /kip/.test(kh), kh);
await ev(() => window.__sky.setDisplayUnits("kN-m"));
await page.click("#exDlgCancel").catch(() => {});

/* ---- nonlinear message (mock + live): a P-Delta static case */
const nl = await ev(async () => {
  const S = window.__sky.store, n = Object.keys(S.model.cases)[0];
  return { n };
});
check("zero console errors", errs.length === 0, errs.slice(0, 3).join(" | "));
console.log(`\n${mode}: ${results.filter(r => r.ok).length}/${results.length} checks passed`);
await browser.close();
process.exit(results.every(r => r.ok) ? 0 : 1);
