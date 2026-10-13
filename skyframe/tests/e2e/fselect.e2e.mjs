/* Wave F — select and assign in every view: browser end-to-end check
   (Playwright + Chromium; Classic AND GPU renderer).

   Usage (server running):
     node tests/e2e/fselect.e2e.mjs http://127.0.0.1:8641 mock
     node tests/e2e/fselect.e2e.mjs http://127.0.0.1:8641 live
   Env: PLAYWRIGHT_MODULE (default /opt/node22/lib/node_modules/playwright/index.mjs).
   tests/test_f_select_e2e.py runs both modes when SKYFRAME_E2E=1.

   For each renderer (View › Renderer › Classic, then GPU):
     1. click / click-again (deselect) / Shift-click of a frame, shell, joint,
        link and point spring in the main 3D view (real mouse events);
        GPU: the Wave F ID buffer produced the candidates
     2. Ctrl-drag WINDOW (L→R) vs CROSSING (R→L) on a frame; drag mode Select;
        Intersecting Line in 3D; hover pre-highlight + label; Esc clears
     3. the selection is drawn in the main 3D view (pixels), plan, elevation
        (SVG) and a View › Windows 3D pane (pixels); joints in plan
     4. right-click menus per type: frame / shell / joint / link / background /
        mixed in the main 3D view, a 3D pane, plan and elevation
   Then (Classic): assign from 3D without leaving Analyze mode, each with
   undo — frame section, shell section, joint restraint, link property;
   Properties panel edits (section, joint restraint); OK with defaults is
   byte-identical; Similar Stories scope replicates (one undo step); Area
   Springs / Member Loads dialogs; Classic vs GPU pick agreement on a grid;
   "Show results…" after a run. Zero console errors. Exit 0 = pass. */

const PW = process.env.PLAYWRIGHT_MODULE || "/opt/node22/lib/node_modules/playwright/index.mjs";
const { chromium } = await import(PW);
const base = (process.argv[2] || "http://127.0.0.1:8641").replace(/\/$/, "");
const mode = process.argv[3] || "mock";
const url = base + (mode === "mock" ? "/?mock=1" : "/");

const fails = [];
let passes = 0;
const ok = (cond, msg) => { if (!cond) fails.push(msg); else passes++; console.log(`${cond ? "PASS" : "FAIL"} [${mode}] ${msg}`); };

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
  ...(process.env.CHROME ? { executablePath: process.env.CHROME } : {}) });
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
await page.addInitScript(() => {
  try {
    localStorage.setItem("skyframe.workflow.v1", JSON.stringify({ open: false }));
    localStorage.setItem("sky.wc.lock", JSON.stringify({ afterRun: false }));
    localStorage.removeItem("sky.f.props"); localStorage.removeItem("sky.f.dragmode"); localStorage.removeItem("sky.f.selfilter");
    localStorage.setItem("skyframe.renderer3d.v1", "classic");
  } catch { /* ignore */ }
});
const errors = [];
page.on("console", m => { if (m.type() === "error" && !/favicon|ResizeObserver loop/.test(m.text())) errors.push(m.text()); });
page.on("pageerror", e => errors.push(String(e)));
page.on("dialog", d => d.dismiss().catch(() => {}));
await page.goto(url);
await page.waitForFunction(() => window.__sky && window.__sky.f && window.__sky.store.model && window.__sky.ux, null, { timeout: 60000 });
await page.waitForTimeout(600);
await page.keyboard.press("Escape");
const W = ms => page.waitForTimeout(ms);
const ev = (fn, arg) => page.evaluate(fn, arg);

/* ---------------------------------------------------------------- model prep (live: add shells / links / springs) */
await ev(() => {
  const s = window.__sky, m = s.store.model, ME = s.ME;
  s.setMode("analyze"); s.switchTab("view3d");
  const g = m.grid, st = m.stories;
  if (!(m.shells || []).some(x => x.kind === "slab")) ME.addSlab(m, g.x_lines[0], g.y_lines[0], g.x_lines[1], g.y_lines[1], st[1].name);
  if (Object.keys(m.shell_sections || {}).length < 2) ME.addShellSection(m);
  ME.addLink(m, [g.x_lines[1], g.y_lines[1], 0], [g.x_lines[1], g.y_lines[1], -2.5]);   // a link clear of other objects
  if (!(m.spring_supports || []).length) ME.addSpringSupport(m, g.x_lines[g.x_lines.length - 1], g.y_lines[0], 0);
  s.markDirty(); s.viewer.setModel(m); s.viewer.fit();
  s.handleSelect([], false);
});
await W(300);

/* ---------------------------------------------------------------- helpers (page side) */
async function target(type, vi = 0, exclude = []) {
  return ev(([type, vi, exclude]) => {
    const s = window.__sky, F = s.f, m = s.store.model;
    const v = vi ? F.s3.viewers().find(x => x !== s.viewer && x.canvas.isConnected) : s.viewer;
    if (!v) return null;
    const P = v._makeProjector(), r = v.canvas.getBoundingClientRect();
    const proj = p => { const pc = P.toCam(p); if (pc[2] < P.near) return null; const q = P.proj(pc); return [q.x, q.y]; };
    const mid = (a, b) => a.map((x, i) => (x + b[i]) / 2);
    const lerp = (a, b, t) => a.map((x, i) => x + (b[i] - x) * t);
    let cands = [];
    if (type === "member") cands = m.members.filter(x => x.kind === "beam").flatMap(x => [0.3, 0.5, 0.7].map(t => [x.uid, lerp(x.pi, x.pj, t)]));
    if (type === "shell") for (const sh of m.shells || []) { const c = sh.corners; for (const u of [0.3, 0.5, 0.7]) for (const w of [0.3, 0.6]) cands.push([sh.uid, lerp(lerp(c[0], c[1], u), lerp(c[3], c[2], u), w)]); }
    if (type === "joint") cands = m.members.filter(x => x.kind === "column").map(x => [F.PG.jointKey(x.pj), x.pj]);
    if (type === "link") cands = (m.links || []).flatMap(x => [0.5, 0.3, 0.7, 0.15, 0.85].map(t => [x.uid, lerp(x.pi, x.pj, t)]));
    if (type === "spring") cands = (m.spring_supports || []).map(x => [null, x.point]);
    for (const [uid, p] of cands) {
      const q = proj(p);
      if (!q || q[0] < 20 || q[1] < 20 || q[0] > r.width - 20 || q[1] > r.height - 20) continue;
      const ref = F.pick.pickAt(v, q[0], q[1], 8);
      if (ref && ref.type === type && !exclude.includes(ref.uid) && (uid == null || ref.uid === uid)) return { x: r.left + q[0], y: r.top + q[1], ref, lx: q[0], ly: q[1] };
    }
    return null;
  }, [type, vi, exclude]);
}
const sel = () => ev(() => JSON.parse(JSON.stringify(window.__sky.store.selection)));
const selKeys = async () => (await sel()).map(r => `${r.type}:${r.uid}`).sort();
const clearSel = () => ev(() => { window.__sky.handleSelect([], false); window.__sky.f.selChanged(); });
async function rightClick(x, y) { await page.mouse.click(x, y, { button: "right" }); await W(150); }
const ctxInfo = () => ev(() => { const m = document.getElementById("wfCtxMenu"); return m ? { head: [...m.querySelectorAll(".wf-ctx-head")].map(h => h.textContent), items: [...m.querySelectorAll(".wf-ctx-item")].map(b => (b.querySelector("span") || b).textContent.trim()) } : null; });
const closeCtx = async () => { await page.keyboard.press("Escape"); await W(80); };
const modelJson = () => ev(() => JSON.stringify(window.__sky.store.model));
async function setRenderer(r) {
  await ev(r => window.__sky.etabs.clickItem(r === "gpu" ? "view-renderer-gpu" : "view-renderer-classic"), r);
  await W(200);
  const on = await ev(() => window.__sky.gpu3d.ready());
  await W(400);
  return on;
}

/* ================================================================ per renderer */
for (const renderer of ["classic", "gpu"]) {
  const gpuOn = await setRenderer(renderer);
  if (renderer === "gpu") ok(gpuOn, "View › Renderer › GPU (WebGL2) is active");
  else ok(!gpuOn, "View › Renderer › Classic is active");
  const R = `${renderer}:`;
  await clearSel();

  // 1. click / deselect / shift for every type
  const picked = {};
  for (const type of ["member", "shell", "joint", "link", "spring"]) {
    if (renderer === "gpu") await page.mouse.move(5, 5);
    let t = await target(type);
    if (renderer === "gpu" && t) {        // warm the lazily loaded GPU id buffer, then re-pick through it
      await page.mouse.move(t.x, t.y); await W(250);
      t = await target(type);
    }
    if (!t) { ok(false, `${R} a visible ${type} to click`); continue; }
    picked[type] = t;
    await page.mouse.click(t.x, t.y); await W(120);
    let s = await sel();
    ok(s.length === 1 && s[0].type === type && s[0].uid === t.ref.uid, `${R} click selects the ${type} ${t.ref.uid}`);
    await page.mouse.click(t.x, t.y); await W(120);
    ok((await sel()).length === 0, `${R} clicking the selected ${type} deselects it (ETABS)`);
  }
  if (renderer === "gpu") {
    const st = await ev(() => { const g = window.__sky.viewer.gpu; return g && g.fPick ? g.fPick.stats() : null; });
    ok(st && st.objects > 0 && st.lastIds > 0, `${R} the GPU ID buffer holds every object type (${st && st.objects} objects, ${st && st.lastIds} ids under the last pick)`);
  }
  if (picked.member && picked.joint) {
    await page.mouse.click(picked.member.x, picked.member.y);
    await page.keyboard.down("Shift"); await page.mouse.click(picked.joint.x, picked.joint.y); await page.keyboard.up("Shift"); await W(100);
    ok((await selKeys()).join() === [`joint:${picked.joint.ref.uid}`, `member:${picked.member.ref.uid}`].sort().join(), `${R} Shift-click adds a joint to a frame`);
    await page.keyboard.down("Control"); await page.mouse.click(picked.joint.x, picked.joint.y); await page.keyboard.up("Control"); await W(100);
    ok((await selKeys()).join() === `member:${picked.member.ref.uid}`, `${R} Ctrl-click on a selected object removes it`);
  }
  // overlapping objects (a link drawn on a beam, a slab under a beam): Ctrl+click lists them
  {
    const ov = await ev(() => {
      const s = window.__sky, v = s.viewer, r = v.canvas.getBoundingClientRect();
      for (let i = 2; i < 40; i++) for (let j = 2; j < 26; j++) {
        const x = r.width * i / 41, y = r.height * j / 27;
        const H = s.f.pick.hitsAt(v, x, y, 8), top = s.f.PG.resolvePick(H);
        if (!top || top.type === "joint" || top.type === "spring") continue;
        const L = H.filter(h => h.ref.type !== "joint" && h.ref.type !== "spring" && (h.ref.type === "shell" || h.d <= 3));
        if (L.length >= 2) return { x: r.left + x, y: r.top + y, list: L.map(h => h.ref) };
      }
      return null;
    });
    if (ov) {
      await clearSel();
      await page.keyboard.down("Control"); await page.mouse.click(ov.x, ov.y); await page.keyboard.up("Control"); await W(120);
      const items = await ev(() => [...document.querySelectorAll("#fPickList .wf-ctx-item")].map(b => b.dataset.ref));
      ok(items.length >= 2, `${R} Ctrl+click on overlapping objects lists them (${items.join(", ")})`);
      const second = items.find(k => k !== items[0]) || "";
      await page.click(`#fPickList .wf-ctx-item[data-ref="${second}"]`); await W(100);
      ok((await selKeys()).includes(second), `${R} choosing from the list selects the hidden object (${second})`);
    } else ok(false, `${R} overlapping objects to test the Ctrl+click list`);
  }
  await clearSel();

  // 2. window vs crossing
  const box = await ev(uid => {
    const s = window.__sky, v = s.viewer, m = s.store.model, P = v._makeProjector(), r = v.canvas.getBoundingClientRect();
    const mm = m.members.find(x => x.uid === uid);
    const a = P.proj(P.toCam(mm.pi)), b = P.proj(P.toCam(mm.pj));
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    return { ox: r.left, oy: r.top, x0: Math.min(a.x, b.x) - 6, x1: Math.max(a.x, b.x) + 6, y0: Math.min(a.y, b.y) - 6, y1: Math.max(a.y, b.y) + 6, mx, my };
  }, picked.member ? picked.member.ref.uid : (await ev(() => window.__sky.store.model.members.find(x => x.kind === "beam").uid)));
  const drag = async (x0, y0, x1, y1, key = "Control") => {
    if (key) await page.keyboard.down(key);
    await page.mouse.move(x0, y0); await page.mouse.down(); await page.mouse.move((x0 + x1) / 2, (y0 + y1) / 2, { steps: 3 }); await page.mouse.move(x1, y1, { steps: 3 }); await page.mouse.up();
    if (key) await page.keyboard.up(key);
    await W(120);
  };
  const uidB = picked.member ? picked.member.ref.uid : null;
  if (uidB) {
    await drag(box.ox + box.x0, box.oy + box.y0, box.ox + box.x1, box.oy + box.y1);
    let k = await selKeys();
    ok(k.includes(`member:${uidB}`), `${R} Ctrl-drag left→right WINDOW selects the frame inside it`);
    const sx = box.ox + box.mx, sy = box.oy + box.my;
    await clearSel();
    await drag(sx + 4, sy - 4, sx - 4, sy + 4);
    k = await selKeys();
    ok(k.includes(`member:${uidB}`), `${R} Ctrl-drag right→left CROSSING selects a frame it only touches`);
    await clearSel();
    await drag(sx - 4, sy - 4, sx + 4, sy + 4);
    ok(!(await selKeys()).includes(`member:${uidB}`), `${R} the same small WINDOW does not select that frame`);
    // drag mode Select: plain drag boxes, Orbit: plain drag orbits
    await ev(() => window.__sky.f.s3.setDragMode("select"));
    await clearSel();
    await drag(box.ox + box.x0, box.oy + box.y0, box.ox + box.x1, box.oy + box.y1, null);
    ok((await selKeys()).includes(`member:${uidB}`), `${R} drag mode Select: a plain left-drag draws the window`);
    await ev(() => window.__sky.f.s3.setDragMode("orbit"));
    const yaw0 = await ev(() => window.__sky.viewer.yaw);
    const n0 = (await sel()).length;
    await drag(box.ox + 300, box.oy + 300, box.ox + 380, box.oy + 320, null);
    const yaw1 = await ev(() => window.__sky.viewer.yaw);
    ok(Math.abs(yaw1 - yaw0) > 0.05 && (await sel()).length === n0, `${R} drag mode Orbit: a plain drag orbits and keeps the selection`);
    await ev(([y]) => { const v = window.__sky.viewer; v.yaw = y; v.vyaw = v.vpitch = 0; v._dirty = true; }, [yaw0]);
    await W(150);
  }
  // intersecting line in the 3D view
  await clearSel();
  const t2 = await target("member");
  if (t2) {
    await ev(() => window.__sky.etabs.clickItem("sel-w8-line"));
    ok(await ev(() => window.__sky.f.s3.lineState().on && window.__sky.store.mode === "analyze"), `${R} Select › Select Using › Intersecting Line starts in the 3D view (stays in Analyze)`);
    await page.mouse.click(t2.x - 25, t2.y - 25); await page.mouse.click(t2.x + 25, t2.y + 25); await W(120);
    ok((await selKeys()).includes(`member:${t2.ref.uid}`), `${R} the intersecting line adds the frame it crosses`);
    await page.keyboard.press("Escape"); await W(60);
    ok(!(await ev(() => window.__sky.f.s3.lineState().on)), `${R} Esc ends the intersecting line`);
    // hover pre-highlight + label
    await page.mouse.move(t2.x, t2.y); await W(200);
    const hv = await ev(() => ({ h: window.__sky.f.s3.hover(), tip: !document.getElementById("viewerTooltip").classList.contains("hidden"), txt: document.getElementById("viewerTooltip").textContent }));
    ok(hv.h && hv.h.uid === t2.ref.uid && hv.tip && hv.txt.includes(t2.ref.uid), `${R} hover pre-highlights the frame and shows its label`);
    await page.keyboard.press("Escape"); await W(80);
    ok((await sel()).length === 0, `${R} Esc clears the selection in the 3D view`);
  }

  // 3. selection drawn everywhere
  if (t2) {
    await page.mouse.click(t2.x, t2.y); await W(120);
    await page.mouse.move(5, 5); await W(250);
    const px = await ev(([lx, ly]) => {
      const c = window.__sky.viewer.canvas, ctx = c.getContext("2d"), d = window.devicePixelRatio || 1;
      const img = ctx.getImageData(Math.round(lx * d) - 4, Math.round(ly * d) - 4, 9, 9).data;
      let best = 1e9;
      for (let i = 0; i < img.length; i += 4) best = Math.min(best, Math.abs(img[i] - 0x35) + Math.abs(img[i + 1] - 0xb5) + Math.abs(img[i + 2] - 0xe5) + (img[i + 3] < 200 ? 300 : 0));
      return best;
    }, [t2.lx, t2.ly]);
    ok(px < 60, `${R} the main 3D view draws the selected frame in the selection colour (Δrgb ${px})`);
  }
}
await setRenderer("classic");
await clearSel();

/* ---------------------------------------------------------------- plan / elevation / pane highlight + joints */
const beam = await ev(() => { const m = window.__sky.store.model; const b = m.members.find(x => x.kind === "beam" && x.story === m.stories[1].name && Math.abs(x.pi[1] - x.pj[1]) < 1e-9); return b; });
const jkey = await ev(b => window.__sky.f.PG.jointKey(b.pi), beam);
await ev(([b, jk]) => { window.__sky.handleSelect([{ type: "member", uid: b.uid }, { type: "joint", uid: jk }], false); }, [beam, jkey]);
await ev(b => { const s = window.__sky; s.setMode("model"); s.setView("plan"); s.setStory(b.story); }, beam);
await W(250);
const planHl = await ev(([b, jk]) => {
  const svg = document.getElementById("planSvg");
  const n = svg.querySelector(`[data-ref="member:${b.uid}"]`);
  return { stroke: n && n.getAttribute("stroke"), joint: !!svg.querySelector(`[data-f="joint:${jk}"]`) };
}, [beam, jkey]);
ok(planHl.stroke === "#35b5e5", "plan draws the selected frame (selection colour)");
ok(planHl.joint, "plan draws the selected joint marker");
// plan: click a joint (cycle-free: a beam-beam joint away from columns is rare; use the column square + second click)
const planJ = await ev(jk => {
  const s = window.__sky, pe = s.planEditor, p = s.f.PG.jointPoint(jk);
  const [x, y] = pe.toScreen(p[0], p[1]); const r = pe.svg.getBoundingClientRect();
  return { x: r.left + x, y: r.top + y };
}, jkey);
await clearSel();
await page.mouse.click(planJ.x, planJ.y); await W(80);
const c1 = await selKeys();
await page.mouse.click(planJ.x, planJ.y); await W(80);
const c2 = await selKeys();
ok([...c1, ...c2].includes(`joint:${jkey}`), `plan: a click at a joint selects it (a column square there wins first, the next click cycles) — ${c1} → ${c2}`);
// elevation
await ev(([b, jk]) => {
  const s = window.__sky; s.handleSelect([{ type: "member", uid: b.uid }, { type: "joint", uid: jk }], false);
  const g = s.store.model.grid; const yi = g.y_lines.findIndex(y => Math.abs(y - b.pi[1]) < 1e-6);
  s.setView("elev"); s.setElevLine(`y:${yi}`);
}, [beam, jkey]);
await W(300);
const elHl = await ev(([b, jk]) => { const svg = document.getElementById("elevSvg"); const n = svg.querySelector(`[data-ref="member:${b.uid}"]`); return { stroke: n && n.getAttribute("stroke"), joint: !!svg.querySelector(`[data-f="joint:${jk}"]`) }; }, [beam, jkey]);
ok(elHl.stroke === "#35b5e5" && elHl.joint, "elevation draws the selected frame and joint");
// elevation window vs crossing (Select › Select Using › Window / Crossing Box)
await ev(() => { if (!window.__sky.w8select.boxMode()) window.__sky.etabs.clickItem("sel-w8-box"); });
const ebox = await ev(b => {
  const ed = window.__sky.elevEditor;
  const a = ed.toScreen(ed.sOf(b.pi), b.pi[2]), c = ed.toScreen(ed.sOf(b.pj), b.pj[2]); const r = ed.svg.getBoundingClientRect();
  return { ox: r.left, oy: r.top, mx: (a[0] + c[0]) / 2, my: (a[1] + c[1]) / 2 };
}, beam);
await clearSel();
await page.mouse.move(ebox.ox + ebox.mx + 6, ebox.oy + ebox.my - 8); await page.mouse.down(); await page.mouse.move(ebox.ox + ebox.mx - 6, ebox.oy + ebox.my + 8, { steps: 4 }); await page.mouse.up(); await W(100);
ok((await selKeys()).includes(`member:${beam.uid}`), "elevation: right→left CROSSING box selects the frame it touches");
await clearSel();
await page.mouse.move(ebox.ox + ebox.mx - 6, ebox.oy + ebox.my - 8); await page.mouse.down(); await page.mouse.move(ebox.ox + ebox.mx + 6, ebox.oy + ebox.my + 8, { steps: 4 }); await page.mouse.up(); await W(100);
ok(!(await selKeys()).includes(`member:${beam.uid}`), "elevation: the same left→right WINDOW does not");
await ev(() => { if (window.__sky.w8select.boxMode()) window.__sky.etabs.clickItem("sel-w8-box"); });
// elevation intersecting line
await clearSel();
await ev(() => window.__sky.etabs.clickItem("sel-w8-line"));
ok(await ev(() => window.__sky.f.s2.elevLineState().on), "Intersecting Line starts in the elevation view");
await page.mouse.click(ebox.ox + ebox.mx, ebox.oy + ebox.my - 20); await page.mouse.click(ebox.ox + ebox.mx + 3, ebox.oy + ebox.my + 20); await W(100);
ok((await selKeys()).includes(`member:${beam.uid}`), "elevation: the intersecting line adds the frame it crosses");
await page.keyboard.press("Escape"); await W(60);
// right-click in plan / elevation
const ecl = await ev(b => { const ed = window.__sky.elevEditor; const a = ed.toScreen(ed.sOf(b.pi) * 0.5 + ed.sOf(b.pj) * 0.5, b.pi[2]); const r = ed.svg.getBoundingClientRect(); return [r.left + a[0], r.top + a[1]]; }, beam);
await rightClick(ecl[0], ecl[1]);
let ci = await ctxInfo();
ok(ci && ci.items.includes("Frame Section…") && ci.items.includes("Properties"), `elevation right-click on a frame → frame menu (${ci && ci.head})`);
await closeCtx();
await ev(() => window.__sky.setView("plan"));
await W(200);
const pcl = await ev(b => { const pe = window.__sky.planEditor; const a = pe.toScreen((b.pi[0] + b.pj[0]) / 2, (b.pi[1] + b.pj[1]) / 2); const r = pe.svg.getBoundingClientRect(); return [r.left + a[0], r.top + a[1]]; }, beam);
await rightClick(pcl[0], pcl[1]);
ci = await ctxInfo();
ok(ci && ci.items.includes("Frame Section…"), "plan right-click on a frame → frame menu");
await closeCtx();
await rightClick(planJ.x, planJ.y);
await rightClick(planJ.x, planJ.y);
ci = await ctxInfo();
ok(ci && (ci.items.includes("Restraints…") || ci.items.includes("Frame Section…")), `plan right-click at a joint → joint / column menu (${ci && ci.head})`);
await closeCtx();

// View › Windows: a 3D pane draws the shared selection and has its own context menu
await ev(() => { const s = window.__sky; s.setMode("analyze"); s.switchTab("view3d"); s.wb.windows.setLayout("2v", [{ kind: "plan" }, { kind: "3d" }]); });
await W(600);
await ev(b => window.__sky.handleSelect([{ type: "member", uid: b.uid }], false), beam);
await ev(() => { const s = window.__sky, v = s.f.s3.viewers().find(x => x !== s.viewer && x.canvas.isConnected); if (v) { v._resize(); v.fit(); } });
await W(400);
const pv = await ev(b => {
  const s = window.__sky, v = s.f.s3.viewers().find(x => x !== s.viewer && x.canvas.isConnected);
  if (!v) return null;
  v._render();
  const P = v._makeProjector(), a = P.proj(P.toCam(b.pi.map((x, i) => (x + b.pj[i]) / 2)));
  const ctx = v.canvas.getContext("2d"), d = window.devicePixelRatio || 1;
  const img = ctx.getImageData(Math.round(a.x * d) - 4, Math.round(a.y * d) - 4, 9, 9).data;
  let best = 1e9;
  for (let i = 0; i < img.length; i += 4) best = Math.min(best, Math.abs(img[i] - 0x35) + Math.abs(img[i + 1] - 0xb5) + Math.abs(img[i + 2] - 0xe5) + (img[i + 3] < 200 ? 300 : 0));
  return best;
}, beam);
ok(pv != null && pv < 60, `a View › Windows 3D pane draws the shared selection (Δrgb ${pv})`);
await clearSel();
const tp = await target("member", 1);
if (tp) {
  await page.mouse.click(tp.x, tp.y); await W(100);
  ok((await selKeys()).join() === `member:${tp.ref.uid}`, "click selects in a 3D pane");
  await rightClick(tp.x, tp.y);
  ci = await ctxInfo();
  ok(ci && ci.items.includes("Frame Section…"), "a 3D pane has the right-click menu (frame)");
  await closeCtx();
} else ok(false, "a frame visible in the 3D pane");
const tpj = await target("joint", 1);
if (tpj) {
  await rightClick(tpj.x, tpj.y);
  ci = await ctxInfo();
  ok(ci && ci.items.includes("Restraints…") && /Joint/.test(ci.head.join()), "3D pane right-click on a joint → joint menu");
  await closeCtx();
}
await ev(() => window.__sky.wb.windows.setLayout("1"));
await W(400);

/* ---------------------------------------------------------------- right-click menus in the main 3D view */
await clearSel();
await ev(() => window.__sky.viewer.fit()); await W(200);
const menus = { member: ["Frame Section…", "Releases / Partial Fixity…"], shell: ["Slab / Wall Section…", "Area Springs…"], joint: ["Restraints…", "Joint Loads…"], link: ["Link Properties…"] };
for (const [type, want] of Object.entries(menus)) {
  const t = await target(type);
  if (!t) { ok(false, `main 3D: a ${type} for the context menu`); continue; }
  await rightClick(t.x, t.y);
  ci = await ctxInfo();
  ok(ci && want.every(w => ci.items.includes(w)) && ci.items.includes("Properties") && ci.items.includes("Object Information…"), `main 3D right-click on a ${type} → ${want.join(", ")} (${ci && ci.head})`);
  ok((await sel()).some(r => r.type === type && r.uid === t.ref.uid), `right-click selects the ${type} it opened on`);
  await closeCtx();
}
{
  const tj = await target("joint"), tm = await target("member");
  await ev(([a, b]) => window.__sky.handleSelect([a, b], false), [tj.ref, tm.ref]);
  await rightClick(tm.x, tm.y);
  ci = await ctxInfo();
  ok(ci && ci.head.join().includes("2 objects selected") && ci.items.includes("Joint Restraints…") && ci.items.includes("Frame Section…"), "mixed selection → mixed menu");
  await closeCtx();
  const rr = await ev(() => {     // a background point: nothing pickable under it
    const s = window.__sky, v = s.viewer, r = v.canvas.getBoundingClientRect();
    for (let i = 1; i < 20; i++) for (let j = 1; j < 12; j++) { const x = r.width * i / 20, y = r.height * j / 12; if (!s.f.pick.pickAt(v, x, y, 12)) return [r.left + x, r.top + y]; }
    return [r.left + 30, r.top + r.height - 30];
  });
  await clearSel();
  await rightClick(rr[0], rr[1]);
  ci = await ctxInfo();
  ok(ci && ci.items.includes("Select All") && ci.items.includes("Selection Filter…"), `background right-click → view menu (${JSON.stringify(ci)})`);
  await closeCtx();
}

/* ---------------------------------------------------------------- assign from 3D (Analyze mode) with undo */
const undo = () => ev(() => window.__sky.history.undo());
async function ctxRun(type, label) {
  const t = await target(type);
  await clearSel();
  await rightClick(t.x, t.y);
  await page.click(`#wfCtxMenu .wf-ctx-item:has-text("${label}")`);
  await W(250);
  return t;
}
{
  const t = await ctxRun("member", "Frame Section…");
  ok(await ev(() => !!document.getElementById("daFrame")), "right-click › Frame Section… opens Assign Frame Properties");
  const before = await modelJson();
  await page.click("#daFrameOk"); await W(150);
  ok((await modelJson()) === before, "OK with defaults leaves the model byte-identical (scope bar included)");
  await rightClick(t.x, t.y);
  await page.click(`#wfCtxMenu .wf-ctx-item:has-text("Frame Section…")`); await W(200);
  const other = await ev(uid => { const m = window.__sky.store.model; const cur = m.members.find(x => x.uid === uid).section; return Object.keys(m.sections).find(n => n !== cur); }, t.ref.uid);
  await page.selectOption("#daFrameSec", other);
  await page.click("#daFrameOk"); await W(200);
  const now = await ev(uid => ({ sec: window.__sky.store.model.members.find(x => x.uid === uid).section, mode: window.__sky.store.mode }), t.ref.uid);
  ok(now.sec === other && now.mode === "analyze", `frame section assigned from 3D (${other}), still in Analyze mode`);
  await undo(); await W(100);
  ok((await modelJson()) === before, "Undo restores the frame section");
}
{
  const t = await ctxRun("shell", "Slab / Wall Section…");
  const other = await ev(uid => { const m = window.__sky.store.model; const cur = m.shells.find(x => x.uid === uid).section; return Object.keys(m.shell_sections).find(n => n !== cur); }, t.ref.uid);
  const before = await modelJson();
  await page.selectOption("#daShellSec", other);
  await page.click("#daShellOk"); await W(200);
  ok((await ev(uid => window.__sky.store.model.shells.find(x => x.uid === uid).section, t.ref.uid)) === other, `shell section assigned from 3D (${other})`);
  await undo(); await W(100);
  ok((await modelJson()) === before, "Undo restores the shell section");
}
{
  const t = await ctxRun("joint", "Restraints…");
  const before = await modelJson();
  ok(await ev(() => !!document.getElementById("daSupports")), "right-click › Restraints… opens Assign Joint Restraints");
  await page.click("#daSupports .dk-radios input[value='pinned'], #daSupPreset_pinned").catch(() => {});
  await page.click("#daSupportsOk"); await W(200);
  const sup = await ev(jk => { const p = jk.split(",").map(Number); return (window.__sky.store.model.supports || []).find(s => s.point.every((c, i) => Math.abs(c - p[i]) < 1e-6)); }, t.ref.uid);
  ok(!!sup, `joint restraint assigned from 3D at ${t.ref.uid} (${sup && sup.restraints.join("")})`);
  await undo(); await W(100);
  ok((await modelJson()) === before, "Undo removes the joint restraint");
}
{
  const t = await ctxRun("link", "Link Properties…");
  const before = await modelJson();
  const opts = await ev(() => [...document.querySelectorAll("#lhType option")].map(o => o.value));
  const want = opts.includes("damper") ? "damper" : opts.find(o => o !== "elastic");
  await page.selectOption("#lhType", want); await W(100);
  await page.click("#lhOk"); await W(200);
  ok((await ev(uid => window.__sky.store.model.links.find(x => x.uid === uid).link_type, t.ref.uid)) === want, `link property assigned from 3D (${want})`);
  await undo(); await W(100);
  ok((await modelJson()) === before, "Undo restores the link property");
}

/* ---------------------------------------------------------------- Properties panel in Analyze mode */
{
  await clearSel();
  const t = await target("member");
  await page.mouse.click(t.x, t.y); await W(200);
  const dock = await ev(() => { const p = document.getElementById("propsPanel"); return { inRow: !!p.closest("#pane-view3d .viewer-row"), visible: p.getClientRects().length > 0, has: !!document.getElementById("propFrameSection") }; });
  ok(dock.inRow && dock.visible && dock.has, "Properties panel is docked beside the 3D view in Analyze mode with the frame fields");
  const before = await modelJson();
  const other = await ev(uid => { const m = window.__sky.store.model; const cur = m.members.find(x => x.uid === uid).section; return Object.keys(m.sections).find(n => n !== cur); }, t.ref.uid);
  const cam0 = await ev(() => [window.__sky.viewer.yaw, window.__sky.viewer.dist]);
  await page.selectOption("#propFrameSection", other); await W(200);
  const cam1 = await ev(() => [window.__sky.viewer.yaw, window.__sky.viewer.dist]);
  ok((await ev(uid => window.__sky.store.model.members.find(x => x.uid === uid).section, t.ref.uid)) === other && cam0.join() === cam1.join(), "Properties panel edit in Analyze mode (camera unchanged)");
  await undo(); await W(150);
  ok((await modelJson()) === before, "Undo restores the Properties panel edit");
  const tj = await target("joint");
  await clearSel();
  await page.mouse.click(tj.x, tj.y); await W(200);
  ok(await ev(() => !!document.getElementById("fJointDofUX")), "Properties panel shows the joint restraints for a selected joint");
  await page.click("#fJointDofUX"); await W(150);
  const jr = await ev(jk => { const p = jk.split(",").map(Number); const s = (window.__sky.store.model.supports || []).find(s => s.point.every((c, i) => Math.abs(c - p[i]) < 1e-6)); return s ? s.restraints : null; }, tj.ref.uid);
  ok(jr && jr[0] === 1, "joint restraint UX edited in the Properties panel");
  await undo(); await W(150);
  ok((await modelJson()) === before, "Undo restores the joint restraint edit (incl. the written-out base supports)");
  // float + dock toggle
  await page.click("#fPropsDock"); await W(150);
  ok(await ev(() => document.getElementById("propsPanel").classList.contains("f-float")), "the Properties panel floats over the 3D view");
  await page.click("#fPropsDock"); await W(150);
}

/* ---------------------------------------------------------------- Similar Stories scope */
{
  await ev(() => {
    const s = window.__sky, st = s.store.model.stories;
    st[0].master = true; for (const x of st.slice(1, 3)) x.similar_to = st[0].name;
    s.markDirty();
  });
  await W(100);
  const b1 = await ev(() => { const m = window.__sky.store.model; return m.members.find(x => x.kind === "beam" && x.story === m.stories[0].name).uid; });
  const before = await modelJson();
  await ev(uid => { window.__sky.handleSelect([{ type: "member", uid }], false); window.__sky.etabs.clickItem("asn-fsec"); }, b1);
  await W(250);
  ok(await ev(() => !!document.getElementById("fScopeSel")), "Assign dialog shows the This Story / Similar Stories / All Stories scope");
  await page.selectOption("#fScopeSel", "similar"); await W(80);
  const cnt = await ev(() => document.getElementById("fScopeCount").textContent);
  const other = await ev(uid => { const m = window.__sky.store.model; const cur = m.members.find(x => x.uid === uid).section; return Object.keys(m.sections).find(n => n !== cur); }, b1);
  await page.selectOption("#daFrameSec", other);
  await page.click("#daFrameOk"); await W(250);
  const res = await ev(([uid, sec]) => {
    const m = window.__sky.store.model, PG = window.__sky.f.PG;
    const b = m.members.find(x => x.uid === uid);
    const cps = PG.correspondingRefs(m, [{ type: "member", uid }], "similar").refs;
    return { n: cps.length, all: cps.every(r => m.members.find(x => x.uid === r.uid).section === sec), src: b.section === sec,
      st4: m.members.find(x => x.kind === b.kind && x.story === m.stories[3].name && Math.abs(x.pi[0] - b.pi[0]) < 1e-6 && Math.abs(x.pi[1] - b.pi[1]) < 1e-6 && Math.abs(x.pj[0] - b.pj[0]) < 1e-6).section };
  }, [b1, other]);
  ok(res.src && res.n === 2 && res.all && res.st4 !== other, `Similar Stories: the section is replicated to the 2 similar stories, not the independent one (${cnt})`);
  await undo(); await W(150);
  const after = await ev(() => { const s = window.__sky.store.model; return JSON.stringify(s.members); });
  ok(after === JSON.parse(before).members ? true : after === JSON.stringify(JSON.parse(before).members), "one Undo reverts the source and every replicated assignment");
  await ev(() => window.__sky.w8stories && window.__sky.w8stories.setScope("one"));
}

/* ---------------------------------------------------------------- converted Assign items */
{
  const t = await target("shell");
  await ev(r => window.__sky.handleSelect([r], false), t.ref);
  const before = await modelJson();
  await ev(() => window.__sky.etabs.clickItem("asn-sspring")); await W(200);
  ok(await ev(() => !!document.getElementById("fAreaSpring") && window.__sky.store.mode === "analyze"), "Assign › Shell › Area Springs opens a dialog (no mode switch)");
  await page.click("#fAreaSpringOk"); await W(120);
  ok((await modelJson()) === before, "Area Springs OK with defaults is byte-identical");
  await ev(() => window.__sky.etabs.clickItem("asn-sspring")); await W(200);
  const had = await ev(uid => !!window.__sky.store.model.shells.find(x => x.uid === uid).area_spring, t.ref.uid);
  await page.click("#fAspOn"); await W(50);
  await page.click("#fAreaSpringOk"); await W(150);
  ok(await ev(uid => !!window.__sky.store.model.shells.find(x => x.uid === uid).area_spring, t.ref.uid) === !had, `area spring ${had ? "removed from" : "assigned to"} the selected shell`);
  await undo(); await W(100);
  const tm = await target("member");
  await ev(r => window.__sky.handleSelect([r], false), tm.ref);
  await ev(() => window.__sky.etabs.clickItem("asn-fload")); await W(250);
  const opened = await ev(() => ({ dlg: [...document.querySelectorAll(".modal-backdrop")].filter(b => !b.classList.contains("hidden") && b.getClientRects().length).map(b => b.id), mode: window.__sky.store.mode }));
  ok(opened.dlg.length > 0 && opened.mode === "analyze", `Assign › Frame Loads › Member Loads opens a dialog in Analyze mode (${opened.dlg.join()})`);
  await page.keyboard.press("Escape"); await W(100);
  await ev(() => { for (const b of document.querySelectorAll(".modal-backdrop")) if (!b.classList.contains("hidden") && b.querySelector(".icon-btn")) b.querySelector(".icon-btn").click(); });
}

/* ---------------------------------------------------------------- Classic vs GPU agreement */
{
  const grid = async () => ev(() => {
    const s = window.__sky, v = s.viewer, r = v.canvas.getBoundingClientRect(), out = [];
    for (let i = 1; i < 16; i++) for (let j = 1; j < 10; j++) { const ref = s.f.pick.pickAt(v, r.width * i / 16, r.height * j / 10, 8); out.push(ref ? `${ref.type}:${ref.uid}` : ""); }
    return out;
  });
  await clearSel();
  const a = await grid();
  await setRenderer("gpu");
  await ev(() => { const s = window.__sky, v = s.viewer; s.f.pick.pickAt(v, 10, 10, 8); });
  await W(400);
  const b = await grid();
  const same = a.filter((x, i) => x === b[i]).length;
  ok(same / a.length >= 0.97 && a.some(Boolean), `Classic and GPU picks agree on a ${a.length}-point grid (${same}/${a.length})`);
  await setRenderer("classic");
}

/* ---------------------------------------------------------------- results: Show results… */
{
  await ev(() => window.__sky.etabs.clickItem("an-run"));
  await page.waitForFunction(() => !!window.__sky.store.results, null, { timeout: 180000 }).catch(() => {});
  await W(500);
  await ev(() => { const s = window.__sky; s.setMode("analyze"); s.switchTab("view3d"); s.viewer.fit(); });
  await W(300);
  const t = await target("member");
  if (t && await ev(() => !!window.__sky.store.results)) {
    await clearSel();
    await rightClick(t.x, t.y);
    ci = await ctxInfo();
    ok(ci && ci.items.includes("Show results…"), "with results the right-click menu offers Show results…");
    let hooked = null;
    await ev(() => { window.__sky.results = window.__sky.results || {}; window.__sky.results.__prev = window.__sky.results.openDetail; window.__sky.results.openDetail = o => { window.__fHook = o; }; });
    await page.click(`#wfCtxMenu .wf-ctx-item:has-text("Show results…")`); await W(100);
    hooked = await ev(() => window.__fHook || null);
    ok(hooked && hooked.uid === t.ref.uid, "Show results… calls sky.results.openDetail(obj) (Wave G hook)");
    await ev(() => { const r = window.__sky.results; r.openDetail = r.__prev; delete r.__prev; if (!r.openDetail) delete r.openDetail; });
  } else ok(false, "analysis results for the Show results… check");
}

ok(errors.length === 0, `zero console errors${errors.length ? ": " + errors.slice(0, 5).join(" | ") : ""}`);
await browser.close();
console.log(`${passes} passed, ${fails.length} failed`);
if (fails.length) { console.log("FAILED:\n  " + fails.join("\n  ")); process.exit(1); }
console.log("ALL PASSED");
