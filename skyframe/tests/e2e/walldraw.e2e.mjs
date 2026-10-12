/* D1 Draw Walls — browser end-to-end check (Playwright + Chromium).

   Usage (server running, e.g. on 8631):
     node tests/e2e/walldraw.e2e.mjs http://127.0.0.1:8631 mock
     node tests/e2e/walldraw.e2e.mjs http://127.0.0.1:8631 live
   Env: PLAYWRIGHT_MODULE (default /opt/node22/lib/node_modules/playwright/index.mjs).
   tests/test_walldraw_e2e.py runs both modes when SKYFRAME_E2E=1.

   Steps (asserting the model objects after each):
     1. Draw Walls (W): section SH200, alignment Left, top offset −0.5 m; a
        3-segment polyline (12,0) → (18,0) → (18,12) → double-click (12,12)
        → 3 walls whose centre line is mitred 0.1 m inside, z 0 … 2.7.
     2. Typed point: click (6,6), type "@6,0" Enter, Enter → 1 wall.
     3. Backspace + Esc: click, click, Backspace, Esc → nothing created.
     4. Quick Draw Walls: click grid line 3 between B and C → 1 wall (6,12)-(12,12);
        drag a box over bay C-D / 2-3 with "All stories" → 4 panels × every story.
     5. Undo (Ctrl+Z) removes the box batch in one step.
     6. live: Save Model round-trips every wall (POST /api/model echo) and a
        linear run succeeds; mock: the dirty model posts through the mock.
   Exit code 0 = pass. Zero console errors required. */

const PW = process.env.PLAYWRIGHT_MODULE || "/opt/node22/lib/node_modules/playwright/index.mjs";
const { chromium } = await import(PW);
const base = (process.argv[2] || "http://127.0.0.1:8631").replace(/\/$/, "");
const mode = process.argv[3] || "mock";
const url = base + (mode === "mock" ? "/?mock=1" : "/");

const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); console.log(`${cond ? "PASS" : "FAIL"} ${msg}`); };
const near = (a, b, tol = 1e-6) => Math.abs(a - b) <= tol;
const sameCorners = (c, exp) => c.length === exp.length && c.every((p, i) => p.every((v, k) => near(v, exp[i][k])));

const browser = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {});
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
await page.addInitScript(() => {
  try { localStorage.setItem("skyframe.workflow.v1", JSON.stringify({ open: false })); localStorage.removeItem("sky.walldraw.v1"); } catch { /* ignore */ }
});
const errors = [];
page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", e => errors.push(String(e)));
await page.goto(url);
await page.waitForFunction(() => window.__sky && window.__sky.store && window.__sky.store.model && window.__sky.wallDraw, null, { timeout: 30000 });
await page.waitForTimeout(800);

if (mode === "live") {
  // a known model on the backend: the Quick Building 4-story 3x2 grid
  await page.evaluate(async () => {
    const s = window.__sky;
    if (s.store.model.grid.x_lines.length < 4 || s.store.model.grid.y_lines.length < 3) throw new Error("live model needs a 3 x 2 bay grid (the default Quick Building)");
    // start clean: drop walls left by an earlier run and save
    const n = s.store.model.shells.length;
    s.store.model.shells = s.store.model.shells.filter(x => x.kind !== "wall");
    if (s.store.model.shells.length !== n) await s.saveModel();
  });
  await page.waitForTimeout(500);
}
await page.evaluate(() => { window.__sky.setMode("model"); window.__sky.setView && window.__sky.setView("plan"); });
await page.waitForTimeout(200);
const walls = () => page.evaluate(() => window.__sky.store.model.shells.filter(s => s.kind === "wall").map(s => ({ uid: s.uid, story: s.story, section: s.section, corners: s.corners, pier: s.pier })));
const scr = (x, y) => page.evaluate(([x, y]) => { const e = window.__sky.planEditor; const r = e.svg.getBoundingClientRect(); const [a, b] = e.toScreen(x, y); return [a + r.left, b + r.top]; }, [x, y]);
const clickAt = async (x, y) => { const [a, b] = await scr(x, y); await page.mouse.move(a, b, { steps: 3 }); await page.mouse.click(a, b); await page.waitForTimeout(80); };
const story = await page.evaluate(() => window.__sky.store.story);
const stories = await page.evaluate(() => window.__sky.store.model.stories.map(s => ({ name: s.name, zb: s.elevation - s.height, zt: s.elevation })));
const st0 = stories.find(s => s.name === story);

/* ---- 1. polyline */
await page.locator("body").click({ position: { x: 700, y: 5 } }).catch(() => {});
await page.keyboard.press("Escape");
await page.evaluate(() => window.__sky.setTool("wall"));
await page.waitForSelector("#wdBox:not(.hidden)");
ok(await page.isVisible("#wdBox"), "floating Draw Walls box shows with the wall tool");
const hint0 = await page.textContent("#uxSbHint").catch(() => "");
ok(/Draw Walls: click the start point/.test(hint0 || ""), `status hint describes the first step (${hint0})`);
await page.selectOption("#wdSec", "SH200").catch(() => {});
await page.click('#wdAlign [data-v="left"]');
await page.fill("#wdTop", "-0.5"); await page.press("#wdTop", "Tab");
const thick = await page.evaluate(() => ((window.__sky.store.model.shell_sections || {}).SH200 || { thickness: 0.2 }).thickness);   // live: SH200 is created with the first wall
const before1 = await walls();
await clickAt(12, 0); await clickAt(18, 0); await clickAt(18, 12);
const [lx, ly] = await scr(12, 12);
await page.mouse.move(lx, ly, { steps: 4 });
const tagTxt = await page.evaluate(() => [...document.querySelectorAll('#planSvg [data-wd="len"]')].map(t => t.textContent).join("|"));
ok(/6\.00 m · 180\.0°/.test(tagTxt), `live length / angle readout (${tagTxt})`);
ok(await page.evaluate(() => document.querySelectorAll('#planSvg [data-wd="panel"]').length) >= 3, "rubber-band preview draws the wall panels");
const scale0 = await page.evaluate(() => window.__sky.planEditor.scale);
await page.mouse.dblclick(lx, ly);
await page.waitForTimeout(150);
const after1 = await walls();
const new1 = after1.filter(w => !before1.some(b => b.uid === w.uid));
const o = thick / 2, zt1 = st0.zt - 0.5;
const exp1 = [
  [[12, o, st0.zb], [18 - o, o, st0.zb], [18 - o, o, zt1], [12, o, zt1]],
  [[18 - o, o, st0.zb], [18 - o, 12 - o, st0.zb], [18 - o, 12 - o, zt1], [18 - o, o, zt1]],
  [[18 - o, 12 - o, st0.zb], [12, 12 - o, st0.zb], [12, 12 - o, zt1], [18 - o, 12 - o, zt1]],
];
ok(new1.length === 3, `3-segment polyline → 3 walls (got ${new1.length})`);
ok(new1.every(w => w.story === story && w.section === "SH200" && w.pier === undefined), "walls carry the chosen section on the current story, no pier written");
ok(exp1.every(e => new1.some(w => sameCorners(w.corners, e))), "left alignment mitres the centre line t/2 inside; top offset −0.5 m");
ok(await page.evaluate(() => window.__sky.wallDraw.points().length === 0), "double-click finished the chain");
ok(scale0 === await page.evaluate(() => window.__sky.planEditor.scale), "double-click no longer zooms to fit while drawing");

/* ---- 2. typed point */
await page.click('#wdAlign [data-v="centre"]');
await page.fill("#wdTop", "0"); await page.press("#wdTop", "Tab");
await clickAt(6, 6);
await page.keyboard.type("@6,0");
ok(await page.evaluate(() => document.activeElement && document.activeElement.id === "wdTyped"), "typing while drawing goes to the length / point box");
await page.keyboard.press("Enter");
await page.keyboard.press("Enter");
await page.waitForTimeout(100);
const after2 = await walls();
const new2 = after2.filter(w => !after1.some(b => b.uid === w.uid));
ok(new2.length === 1 && sameCorners(new2[0].corners, [[6, 6, st0.zb], [12, 6, st0.zb], [12, 6, st0.zt], [6, 6, st0.zt]]), "typed @6,0 places the next point exactly");

/* ---- 3. Backspace + Esc */
await page.locator("#planSvg").focus();
await clickAt(0, 12); await clickAt(6, 12);
await page.keyboard.press("Backspace");
ok(await page.evaluate(() => window.__sky.wallDraw.points().length === 1), "Backspace removes the last point");
const selBefore = await page.evaluate(() => window.__sky.store.model.shells.length);
await page.keyboard.press("Escape");
ok(await page.evaluate(() => window.__sky.wallDraw.points().length === 0), "Esc ends the chain");
ok(selBefore === await page.evaluate(() => window.__sky.store.model.shells.length), "a 1-point chain creates nothing");

/* ---- 4. quick draw */
await page.click('[data-qat="wdquick"]');
await page.waitForTimeout(100);
ok(await page.evaluate(() => window.__sky.store.tool === "wdquick"), "toolbar button arms Quick Draw Walls");
const before4 = await walls();
await clickAt(9, 12.04);
const after4 = await walls();
const new4 = after4.filter(w => !before4.some(b => b.uid === w.uid));
ok(new4.length === 1 && sameCorners(new4[0].corners, [[6, 12, st0.zb], [12, 12, st0.zb], [12, 12, st0.zt], [6, 12, st0.zt]]), "quick click on grid line 3 between B and C → one wall panel");
await page.click('#wdScope [data-v="all"]');
const [ax, ay] = await scr(11.4, 5.4), [bx, by] = await scr(18.6, 12.6);
await page.mouse.move(ax, ay); await page.mouse.down(); await page.mouse.move((ax + bx) / 2, (ay + by) / 2, { steps: 4 });
await page.mouse.move(bx, by, { steps: 4 });
ok(/4 wall panels/.test(await page.evaluate(() => [...document.querySelectorAll('#planSvg [data-wd="len"]')].map(t => t.textContent).join("|"))), "box preview counts the panels");
await page.mouse.up();
await page.waitForTimeout(150);
const after5 = await walls();
const new5 = after5.filter(w => !after4.some(b => b.uid === w.uid));
const segs = [[[12, 6], [12, 12]], [[18, 6], [18, 12]], [[12, 6], [18, 6]], [[12, 12], [18, 12]]];
const okBox = stories.every(s => segs.every(([a, b]) => new5.some(w => w.story === s.name &&
  sameCorners(w.corners, [[a[0], a[1], s.zb], [b[0], b[1], s.zb], [b[0], b[1], s.zt], [a[0], a[1], s.zt]]))));
ok(new5.length === 4 * stories.length && okBox, `box quick draw on All Stories → ${4 * stories.length} walls (got ${new5.length})`);
await page.click('#wdScope [data-v="one"]');

/* ---- 5. undo = one step */
await page.locator("#planSvg").focus();
await page.keyboard.press("Control+z");
await page.waitForTimeout(250);
ok((await walls()).length === after4.length, "Ctrl+Z removes the whole box batch in one step");
await page.keyboard.press("Control+y");
await page.waitForTimeout(250);
ok((await walls()).length === after5.length, "Ctrl+Y restores it");

/* ---- similar stories + split at grid lines (3rd story is master, 4th similar) */
if (stories.length >= 4) {
  const [sA, sB] = [stories[2], stories[3]];
  await page.evaluate(([a, b]) => {
    const s = window.__sky, ss = s.store.model.stories;
    ss.find(x => x.name === a).master = true; ss.find(x => x.name === b).similar_to = a;
    s.setStory(a); s.setTool("wall");
  }, [sA.name, sB.name]);
  await page.click('#wdScope [data-v="similar"]');
  await page.check("#wdSplit");
  const b6 = await walls();
  await clickAt(0, 0); await clickAt(0, 12);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(100);
  const n6 = (await walls()).filter(w => !b6.some(b => b.uid === w.uid));
  const exp6 = [sA, sB].flatMap(s => [[[0, 0], [0, 6]], [[0, 6], [0, 12]]].map(([a, b]) => ({ s: s.name, c: [[a[0], a[1], s.zb], [b[0], b[1], s.zb], [b[0], b[1], s.zt], [a[0], a[1], s.zt]] })));
  ok(n6.length === 4 && exp6.every(e => n6.some(w => w.story === e.s && sameCorners(w.corners, e.c))), `split at grid lines × similar stories → 2 panels × 2 stories (got ${n6.length})`);
  await page.uncheck("#wdSplit");
  await page.click('#wdScope [data-v="one"]');
  await page.evaluate(([a, b]) => {
    const s = window.__sky, ss = s.store.model.stories;
    delete ss.find(x => x.name === a).master; delete ss.find(x => x.name === b).similar_to;
    s.setStory(ss[0].name);
  }, [sA.name, sB.name]);
}

/* ---- walls along beams (Beams source: click a beam) */
await page.click('#wdMode [data-v="beams"]');
const beam = await page.evaluate(() => {
  const s = window.__sky, m = s.store.model, st = s.store.story;
  const covered = b => m.shells.some(w => w.kind === "wall" && w.story === st && [b.pi, b.pj].every(p => w.corners.some(c => Math.hypot(c[0] - p[0], c[1] - p[1]) < 1e-6)));
  const b = m.members.find(x => x.kind === "beam" && x.story === st && !covered(x) && !x.curve);
  return b ? { uid: b.uid, a: [b.pi[0], b.pi[1]], b: [b.pj[0], b.pj[1]] } : null;
});
if (beam) {
  const b7 = await walls();
  await clickAt((beam.a[0] + beam.b[0]) / 2 + 0.02, (beam.a[1] + beam.b[1]) / 2 + 0.02);
  const n7 = (await walls()).filter(w => !b7.some(b => b.uid === w.uid));
  ok(n7.length === 1 && sameCorners(n7[0].corners, [[beam.a[0], beam.a[1], st0.zb], [beam.b[0], beam.b[1], st0.zb], [beam.b[0], beam.b[1], st0.zt], [beam.a[0], beam.a[1], st0.zt]]),
    `Beams source: click beam ${beam.uid} → wall under it`);
} else ok(false, "found an uncovered beam for the walls-along-beams check");

/* ---- display units: kip-in input converts to SI */
await page.evaluate(() => window.__sky.setTool("wall"));
const unitSel = await page.evaluateHandle(() => [...document.querySelectorAll("select")].find(s => [...s.options].some(o => o.value === "kip-in")));
if (unitSel && await unitSel.evaluate(s => !!s)) {
  const prevU = await unitSel.evaluate(s => s.value);
  await unitSel.selectOption("kip-in");
  await page.waitForTimeout(150);
  await page.fill("#wdTop", "-12"); await page.press("#wdTop", "Tab");
  const tSI = await page.evaluate(() => window.__sky.wallDraw.settings().topOff);
  ok(Math.abs(tSI + 0.3048) < 1e-9 && /in/.test(await page.textContent('#wdBox [data-wd-unit]')), `kip-in: top offset −12 in → ${tSI} m`);
  await page.fill("#wdTop", "0"); await page.press("#wdTop", "Tab");
  await unitSel.selectOption(prevU);
} else ok(false, "units selector found");

/* ---- other draw tools keep their own behaviour (beam chain) */
await page.evaluate(() => window.__sky.setTool("beam"));
const nb0 = await page.evaluate(() => {   // a rerun on a live backend: drop the beam an earlier run drew
  const m = window.__sky.store.model, st = window.__sky.store.story;
  const at = (p, x, y) => Math.abs(p[0] - x) < 1e-6 && Math.abs(p[1] - y) < 1e-6;
  m.members = m.members.filter(b => !(b.story === st && b.kind === "beam" && ((at(b.pi, 0, 3) && at(b.pj, 6, 3)) || (at(b.pi, 6, 3) && at(b.pj, 0, 3)))));
  return m.members.length;
});
await clickAt(0, 3); await clickAt(6, 3);
await page.keyboard.press("Escape");
const nb1 = await page.evaluate(() => window.__sky.store.model.members.length), boxVis = await page.isVisible("#wdBox");
ok(nb1 === nb0 + 1 && !boxVis, `beam tool unchanged; the wall box hides for other tools (members +${nb1 - nb0}, box ${boxVis})`);
await page.locator("#planSvg").focus();
await page.keyboard.press("Control+z");   // the test beam floats (no columns at y = 3): undo it before the run
await page.waitForTimeout(200);
ok(await page.evaluate(() => window.__sky.store.model.members.length) === nb0, "Ctrl+Z removes the test beam");

/* ---- menus */
const menuOk = await page.evaluate(() => {
  const s = window.__sky;
  const st = s.menu.stats();
  return { big: st.filter(x => x.entries >= 13).map(x => x.path + ":" + x.entries), max: Math.max(...st.map(x => x.entries)), q: s.menu.path("draw-wd-quick"), b: s.menu.path("draw-wd-beams"), g: s.menu.path("draw-wd-qwall") };
});
ok(menuOk.max <= 14 && /Floors & Walls/.test(menuOk.q || "") && /Floors & Walls/.test(menuOk.b || "") && /Quick Draw/.test(menuOk.g || ""), `menu entries placed, every panel <= 14 (${JSON.stringify(menuOk)})`);

/* ---- 6. save round trip */
await page.evaluate(() => window.__sky.setTool("select"));
const pre = await walls();
await page.evaluate(() => window.__sky.saveModel());
await page.waitForTimeout(600);
const post = await walls();
ok(pre.length === post.length && pre.every(w => post.some(p => p.uid === w.uid && sameCorners(p.corners, w.corners) && p.section === w.section)), "Save Model round-trips every wall");
if (mode === "live") {
  const got = await page.evaluate(async () => (await (await fetch("/api/model")).json()));
  const m = got.model || got;
  const sw = (m.shells || []).filter(s => s.kind === "wall");
  ok(sw.length === post.length, `GET /api/model returns the walls (${sw.length})`);
  await page.evaluate(() => { window.__sky.doRun(); });
  await page.waitForFunction(() => !!window.__sky.store.results, null, { timeout: 180000 }).catch(() => {});
  ok(await page.evaluate(() => !!window.__sky.store.results), "live analysis with the new walls succeeds");
}

ok(errors.length === 0, `zero console errors${errors.length ? ": " + errors.join(" | ") : ""}`);
await browser.close();
console.log(fails.length ? `\n${fails.length} FAILED` : "\nALL PASSED");
process.exit(fails.length ? 1 : 0);
