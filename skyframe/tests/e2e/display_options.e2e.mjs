/* Display Options (View › Set Display Options, Ctrl+W) — Playwright check.

   Usage:  node display_options.e2e.mjs [baseUrl] [mock=1|0]
   Env:    PW_MODULE  path of playwright's index.mjs
                      (default /opt/node22/lib/node_modules/playwright/index.mjs)
           PW_CHROMIUM chromium executable (default: playwright's own)

   Toggles options through the dialog, the toolbar, the shortcuts and the API,
   and asserts the RENDER state of the plan (SVG DOM), elevation and 3D
   (vx frame state + canvas pixels) windows. Prints one JSON line
   {"passed": n, "failed": n, "fails": [...]} and exits 1 on any failure. */

const PW = process.env.PW_MODULE || "/opt/node22/lib/node_modules/playwright/index.mjs";
const { chromium } = await import(PW);
const BASE = process.argv[2] || "http://127.0.0.1:8632";
const MOCK = (process.argv[3] || "1") !== "0";
const URL = BASE + (MOCK ? "/?mock=1" : "/");

const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
const page = await ctx.newPage();
const errors = [];
page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", e => errors.push(String(e)));
let passed = 0;
const fails = [];
const ok = (cond, what) => { if (cond) passed++; else { fails.push(what); console.error("FAIL:", what); } };
const ev = (fn, arg) => page.evaluate(fn, arg);
const settle = (ms = 250) => page.waitForTimeout(ms);

await page.goto(URL);
await page.waitForFunction(() => window.__sky && window.__sky.dispOpts && window.__sky.store.model && (window.__sky.store.model.members || []).length > 0, null, { timeout: 30000 });
await settle(800);
await ev(() => { try { localStorage.removeItem("skyframe.dispopts.v1"); sessionStorage.removeItem("skyframe.vx.display.v1"); } catch { /* */ } });
const model0 = await ev(() => JSON.stringify(window.__sky.store.model));

/* ---------- 1. plan window: dialog opens on Ctrl+W, menu and Alt+W ---------- */
await ev(() => window.__sky.etabs.clickItem("draw-plan"));
await settle();
ok(await ev(() => window.__sky.dispOpts.activeWin()) === "plan", "active window is plan in Model mode");
await page.keyboard.press("Control+w");
await settle();
ok(await ev(() => window.__sky.dispOpts.isOpen()), "Ctrl+W opens Set Display Options");
ok(await page.locator("#dpWin").inputValue() === "plan", "dialog targets the active (plan) window");
ok(await page.locator("#dpTabs .dk-tab").count() === 4, "four tabs (Objects / Object View Options / Other / Show Only)");
await page.keyboard.press("Escape");
await settle();
ok(!(await ev(() => window.__sky.dispOpts.isOpen())), "Esc cancels");
await page.keyboard.press("Alt+w");
await settle();
ok(await ev(() => window.__sky.dispOpts.isOpen()), "Alt+W opens the dialog");
await page.keyboard.press("Escape");
await ev(() => window.__sky.etabs.clickItem("vx-display-options"));
await settle();
ok(await ev(() => window.__sky.dispOpts.isOpen()), "View › Set Display Options… opens the new dialog");

/* ---------- 2. hide beams + frame labels via the dialog → plan SVG ---------- */
const beamsBefore = await ev(() => { const S = window.__sky.store; const st = S.story; return S.model.members.filter(m => m.story === st && m.kind === "beam").length; });
await page.uncheck("#dpObj_beam");
await page.check("#dpLab_frameName");
await page.click("#dpDisplayDlgOk");
await settle(400);
const plan = await ev(() => {
  const svg = document.getElementById("planSvg");
  const S = window.__sky.store;
  const beams = new Set(S.model.members.filter(m => m.kind === "beam").map(m => "member:" + m.uid));
  const nodes = [...svg.querySelectorAll("[data-ref^='member:']")].filter(n => beams.has(n.getAttribute("data-ref")));
  return { beamNodes: nodes.length, beamHidden: nodes.filter(n => n.getAttribute("display") === "none").length,
    names: svg.querySelectorAll("text[data-dp='frame-name']").length };
});
ok(beamsBefore > 0 && plan.beamNodes === plan.beamHidden, `beams hidden in plan (${plan.beamHidden}/${plan.beamNodes})`);
ok(plan.names > 0, `frame labels drawn in plan (${plan.names})`);
ok(await ev(() => !window.__sky.dispOpts.get("elev").labels.frameName), "elevation window keeps its own options");

/* ---------- 3. colour by section / fill / line thickness / shrink (plan) ---------- */
await ev(() => window.__sky.dispOpts.patch("plan", { objects: { beam: true }, view: { colorBy: "section", fill: false, shrink: true }, other: { lineScale: 2 } }));
await settle(300);
const col = await ev(() => {
  const sky = window.__sky, m = sky.store.model;
  const n = document.querySelector("#planSvg line[data-ref^='member:']:not([display])");
  const uid = n.getAttribute("data-ref").slice(7);
  const mm = m.members.find(x => String(x.uid) === uid);
  const want = sky.dispOpts.core.dpColorOf(m, "section", { type: "member", kind: mm.kind, uid, section: mm.section, story: mm.story });
  const slab = document.querySelector("#planSvg path[data-ref^='shell:']");
  const L = Math.hypot(+n.getAttribute("x2") - +n.getAttribute("x1"), +n.getAttribute("y2") - +n.getAttribute("y1"));
  const L0 = Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1]);
  return { stroke: n.getAttribute("stroke"), want, sw: +n.getAttribute("stroke-width"), slabFill: slab ? slab.getAttribute("fill") : "none", ratio: L / L0 };
});
ok(col.stroke === col.want, `colour by section applied (${col.stroke} = ${col.want})`);
ok(col.slabFill === "none", "fill objects off → slab drawn as outline");
ok(col.sw >= 4 - 1e-9, `line thickness ×2 (stroke-width ${col.sw})`);
ok(Math.abs(col.ratio - 0.8) < 1e-6, `shrink objects → 80 % length (${col.ratio.toFixed(3)})`);

/* ---------- 4. grid lines / bubbles / joints / axes ---------- */
await ev(() => window.__sky.dispOpts.patch("plan", { other: { grid: false, bubbles: false, jointSize: 6 }, objects: { joint: true }, labels: { frameAxes: true } }));
await settle(300);
const g = await ev(() => {
  const ed = window.__sky.planEditor;
  const big = [...ed.gLabels.querySelectorAll("text[font-size='11']")];
  return { grid: ed.gGrid.style.display, bubblesShown: big.filter(t => t.getAttribute("display") !== "none").length,
    joints: ed.gLabels.querySelectorAll("circle[data-dp='joint']").length, r: +(ed.gLabels.querySelector("circle[data-dp='joint']") || { getAttribute: () => 0 }).getAttribute("r"),
    axes: ed.gLabels.querySelectorAll("[data-dp='frame-axes']").length };
});
ok(g.grid === "none", "grid lines hidden");
ok(g.bubblesShown === 0, "grid bubbles hidden");
ok(g.joints > 0 && g.r === 3, `joints drawn with size 6 px (${g.joints}, r ${g.r})`);
ok(g.axes > 0, "frame local axes drawn in plan");

/* ---------- 5. toolbar quick toggles (plan) ---------- */
const bar = "[data-dp-bar='2d']";
const secBefore = await ev(() => window.__sky.dispOpts.get("plan").labels.frameSection);
await page.click(`${bar} [data-dp-chip='sections']`);
await settle(300);
ok(await ev(() => window.__sky.dispOpts.get("plan").labels.frameSection) === !secBefore, "toolbar Sections toggles section labels");
ok(await ev(() => document.querySelectorAll("#planSvg text[data-dp='frame-section']").length > 0), "section labels drawn");
await page.selectOption(`${bar} select[data-dp-chip='colorby']`, "story");
await settle(200);
ok(await ev(() => window.__sky.dispOpts.get("plan").view.colorBy) === "story", "toolbar colour-by select");

/* ---------- 6. elevation window ---------- */
await ev(() => window.__sky.etabs.clickItem("draw-elev"));
await settle(400);
ok(await ev(() => window.__sky.dispOpts.activeWin()) === "elev", "active window is elevation");
await ev(() => window.__sky.dispOpts.patch("elev", { objects: { column: false }, other: { storyLabels: false } }));
await settle(300);
const el = await ev(() => {
  const ed = window.__sky.elevEditor, m = window.__sky.store.model;
  const cols = new Set(m.members.filter(x => x.kind === "column").map(x => "member:" + x.uid));
  const nodes = [...ed.gElems.querySelectorAll("[data-ref^='member:']")].filter(n => cols.has(n.getAttribute("data-ref")));
  const story = [...ed.gLabels.querySelectorAll("text[text-anchor='end']")];
  return { cols: nodes.length, hidden: nodes.filter(n => n.getAttribute("display") === "none").length, story: story.length, storyHidden: story.filter(t => t.getAttribute("display") === "none").length };
});
ok(el.cols > 0 && el.cols === el.hidden, `columns hidden in elevation (${el.hidden}/${el.cols})`);
ok(el.story > 0 && el.story === el.storyHidden, "story labels hidden in elevation");

/* ---------- 7. 3D window = the existing vx state (no duplicates) ---------- */
await ev(() => window.__sky.etabs.clickItem("view-3d"));
await settle(400);
ok(await ev(() => window.__sky.dispOpts.activeWin()) === "3d", "active window is 3D in Analyze mode");
await page.keyboard.press("Alt+x");
await settle(300);
ok(await ev(() => window.__sky.dispOpts.get("3d").view.extrude), "Alt+X (Extruded View) shows up as 3D 'Extrude frames'");
await page.keyboard.press("Alt+x");
await settle(200);
await ev(() => window.__sky.etabs.clickItem("vx-fa-axes"));
await settle(200);
ok(await ev(() => window.__sky.dispOpts.get("3d").labels.frameAxes), "View › Show Local Axes shows up as 3D frame local axes");
await ev(() => window.__sky.etabs.clickItem("vx-fa-axes"));
await page.keyboard.press("Control+w");
await settle();
ok(await page.locator("#dpWin").inputValue() === "3d", "dialog targets the 3D window");
await page.click("#dpTabs-tab-objects");
await page.uncheck("#dpObj_column");
await page.click("#dpTabs-tab-view");
await page.selectOption("#dpColorBy", "story");
await page.click("#dpTabs-tab-other");
await page.fill("#dpLineScale", "3");
await page.click("#dpDisplayDlgApply");
await settle(300);
ok(await ev(() => window.__sky.dispOpts.isOpen()), "Apply keeps the dialog open");
await page.click("#dpDisplayDlgOk");
await settle(400);
const v3 = await ev(() => {
  const sky = window.__sky, v = sky.viewer, o = sky.vx.opts();
  const F = v.vxFrame(v._makeProjector(), v);
  const segs = v.segs || [];
  const col = segs.find(s => s.kind === "column"), bm = segs.find(s => s.kind === "beam");
  const m = sky.store.model;
  const mm = m.members.find(x => x.uid === bm.uid);
  return { colorBy: o.colorBy, colHidden: F.seg(col) === null, beamShown: !!F.seg(bm), lineScale: F.lineScale,
    segColor: F.segColor(bm), want: sky.dispOpts.core.dpColorOf(m, "story", { type: "member", kind: "beam", uid: bm.uid, section: mm.section, story: mm.story }) };
});
ok(v3.colorBy === "story", "3D colour by story stored in the vx options");
ok(v3.colHidden && v3.beamShown, "columns hidden in 3D, beams shown");
ok(v3.lineScale === 3, "3D line thickness ×3");
ok(v3.segColor === v3.want, `3D colour by story (${v3.segColor})`);
// pixels: background black is applied to the canvas
await ev(() => window.__sky.dispOpts.patch("3d", { other: { background: "black" } }));
await settle(200);
ok(await ev(() => getComputedStyle(window.__sky.viewer.canvas).backgroundColor) === "rgb(0, 0, 0)", "3D background black");
// toolbar Labels in 3D → vx object labels
await page.click("[data-dp-bar='3d'] [data-dp-chip='labels']");
await settle(200);
ok(await ev(() => window.__sky.vx.opts().show.label === true), "3D toolbar Labels → object labels on");

/* ---------- 8. apply to all windows, save as default, persistence ---------- */
await page.keyboard.press("Control+w");
await settle();
await page.click("#dpTabs-tab-objects");
await page.check("#dpLab_shellName");
await page.check("#dpAllWin");
await page.click("#dpDisplayDlgOk");
await settle(400);
ok(await ev(() => ["plan", "elev", "3d"].every(w => window.__sky.dispOpts.get(w).labels.shellName)), "Apply to all windows");
await ev(() => window.__sky.dispOpts.saveDefault(window.__sky.dispOpts.get("plan")));
const stored = await ev(() => JSON.parse(window.__sky.dispOpts.stored()));
ok(stored && stored.v === 1 && stored.default && stored.default.labels && stored.default.labels.frameName === true, "Save as Default persisted (localStorage, sparse)");
await page.reload();
await page.waitForFunction(() => window.__sky && window.__sky.dispOpts && window.__sky.store.model && (window.__sky.store.model.members || []).length > 0, null, { timeout: 30000 });
await settle(800);
ok(await ev(() => window.__sky.dispOpts.get("plan").view.colorBy === "story" && window.__sky.dispOpts.get("elev").objects.column === false), "per-window options survive a reload");
// Reset → saved default
await ev(() => window.__sky.etabs.clickItem("draw-plan"));
await settle(300);
await page.keyboard.press("Control+w");
await settle();
await page.click("#dpReset");
await settle(300);
ok(await ev(() => { const s = window.__sky.dispOpts.get("plan"); return s.labels.frameName === true && s.view.colorBy === "story"; }), "Reset returns to the saved default");
await page.keyboard.press("Escape");
await ev(() => window.__sky.dispOpts.clearDefault());

/* ---------- 9. windows (panes) and named views ---------- */
const hasWin = await ev(() => !!(window.__sky.wb && window.__sky.wb.windows));
if (hasWin) {
  await ev(() => window.__sky.wb.windows.setLayout("2v"));
  await settle(500);
  await ev(() => window.__sky.dispOpts.set("w1", window.__sky.dispOpts.core.dpMerge(window.__sky.dispOpts.core.dpDefaults(), { labels: { frameSection: true } })));
  await settle(300);
  const pw = await ev(() => { const p = window.__sky.wb.windows.pane(0); return p.kind === "plan" ? p.ed.gLabels.querySelectorAll("text[data-dp='frame-section']").length : -1; });
  ok(pw > 0, `pane 1 (plan) draws its own section labels (${pw})`);
  ok(await ev(() => window.__sky.dispOpts.windows().includes("w1")), "panes are listed as windows");
  await ev(() => window.__sky.wb.windows.setLayout("1"));
  await settle(300);
}
await ev(() => window.__sky.dispOpts.set("plan", window.__sky.dispOpts.core.dpMerge(window.__sky.dispOpts.core.dpDefaults(), { labels: { frameName: true } })));
const cap = await ev(() => window.__sky.wb && window.__sky.wb.views ? window.__sky.wb.views.capture("T") : null);
if (cap) {
  ok(cap.display && cap.display.dp_windows && cap.display.dp_windows.plan && cap.display.dp_windows.plan.labels.frameName === true, "named view captures the plan display options");
  await ev(() => window.__sky.dispOpts.set("plan", window.__sky.dispOpts.core.dpDefaults()));
  await ev(v => window.__sky.wb.views.apply(v), cap);
  await settle(300);
  ok(await ev(() => window.__sky.dispOpts.get("plan").labels.frameName === true), "named view restores the plan display options");
}

/* ---------- 10. model untouched; no console errors ---------- */
ok(await ev(m0 => {
  // display options are view state: only the named-view capture above may have run (capture does not write)
  return JSON.stringify(window.__sky.store.model) === m0;
}, model0), "model JSON byte-identical after every display change");
ok(await ev(() => !window.__sky.dispOpts.lastError() && !window.__sky.vx.lastError()), "no swallowed render errors");

/* ---------- 11. live: a saved named view with plan options round-trips through POST /api/model ---------- */
if (cap && !MOCK) {
  const rt = await ev(async () => {
    const sky = window.__sky;
    await sky.etabs.clickItem("draw-plan");
    sky.wb.views.save("D2 plan");
    const body = JSON.stringify(sky.store.model);
    const r1 = await fetch("/api/model", { method: "POST", headers: { "Content-Type": "application/json" }, body });
    const j1 = await r1.json().catch(() => null);
    const g = window.__sky.store.model.named_views.find(v => v.name === "D2 plan");
    return { status: r1.status, ok: !!g && !!(g.display && g.display.dp_windows && g.display.dp_windows.plan), err: j1 && j1.error };
  });
  ok(rt.status === 200 && rt.ok, `named view with dp_windows accepted by POST /api/model (${rt.status} ${rt.err || ""})`);
}

ok(errors.length === 0, "zero console errors: " + errors.slice(0, 3).join(" | "));

console.log(JSON.stringify({ passed, failed: fails.length, fails }));
await browser.close();
process.exit(fails.length ? 1 : 0);
