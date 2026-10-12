/* SkyFrame Wave B — Edit › Edit Shells ▸ (ETABS Edit Shells).

   Divide Shells… (n1 × n2 or by maximum size) · Merge Shells · Chop
   Shells… (at grid lines, selected frames, story levels or a line drawn in
   the plan) · Expand / Shrink Shells… (one edge or every edge by an
   offset) · Reshape Shell (vertex reshaper in the plan editor: drag a
   vertex, drag a "+" edge midpoint to insert one, Alt+click a vertex to
   delete it).

   Every command is an Edit op (POST /api/edit/<op>, skyframe/core/
   edit_shells.py; ?mock=1 js/wb_shellgeom.js) run through sky.edit.run, so
   it is ONE undo step, keeps every reference (area loads, groups, tendon
   hosts) and selects the result. Dialogs show a dashed ghost preview of
   the pieces computed by the same geometry. */

import { el as sEl, dkForm as sForm, dkGroup as sGroup, dkDialog as sDialog, dkNote as sNote } from "./dlgkit.js";
import sU from "./units.js";
import { wbApplyEdit as sApply, polyArea as sArea } from "./wb_shellgeom.js";

const NS = "http://www.w3.org/2000/svg";
const TOOL = "wbreshape", CUT = "wbchopline";

export function initWbShells(sky) {
  const S = sky.store, pe = sky.planEditor;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  const selShells = () => (S.selection || []).filter(r => r.type === "shell").map(r => r.uid);
  const backendSel = () => (sky.edit ? sky.edit.backendSel() : { members: [], shells: selShells(), links: [], points: [] });
  const ghost = () => sky.edit && sky.edit.ghost;

  /** Run an op (one undo step) and report it. */
  async function run(op, params, label, sel) {
    if (!sky.edit) throw new Error("Edit utilities unavailable");
    const sm = await sky.edit.run(op, params, { label, sel: sel || backendSel() });
    const c = (sm.created.shells || []).length, m = (sm.modified.shells || []).length, d = (sm.deleted.shells || []).length;
    toast(label, `${c} created · ${m} modified · ${d} deleted${S.mock ? " (mock)" : ""}`);
    for (const w of (sm.warnings || []).slice(0, 3)) toast(label, w, "warn", 6000);
    return sm;
  }
  /** Ghost of the op result (shell polygons it creates / modifies). */
  function preview(op, params, sel) {
    try {
      const r = sApply(S.model, op, sel || backendSel(), params);
      const uids = [...(r.summary.created.shells || []), ...(r.summary.modified.shells || [])];
      const polys = r.model.shells.filter(s => uids.includes(s.uid)).map(s => s.corners);
      if (ghost()) ghost().show({ polys });
      return { n: polys.length, area: polys.reduce((a, p) => a + sArea(p), 0), warnings: r.summary.warnings };
    } catch (e) { if (ghost()) ghost().clear(); return { error: e.message }; }
  }
  const needShells = (what, n = 1) => {
    const k = selShells().length;
    if (k >= n) return true;
    toast(what, `Select ${n > 1 ? "at least " + n + " shells" : "one or more shells"} first (V, click or box).`, "warn");
    return false;
  };
  const prevLine = (box, r) => { box.innerHTML = r.error ? `<span class="wb-err">${r.error}</span>` : `Preview: ${r.n} shell${r.n === 1 ? "" : "s"} · total area ${sU.fmtU("area", r.area, 2)}`; };

  /* ---------------- divide */
  function openDivide() {
    if (!needShells("Divide Shells")) return null;
    const V = { mode: "n", n1: 2, n2: 2, max_size: 1.0 };
    const F = sForm(), pv = sEl("p", { class: "muted wb-preview", id: "wbDivPrev" });
    const params = () => (V.mode === "n" ? { mode: "n", n1: V.n1, n2: V.n2 } : { mode: "size", max_size: V.max_size });
    const upd = () => prevLine(pv, preview("shell_divide", params()));
    const body = [sGroup("Divide Option", [
      F.radio(V, "mode", [["n", "Divide into n1 × n2 shells", "4-corner shells: in their parameter space; convex polygons: equal strips"], ["size", "Divide into shells no larger than"]], { onChange: upd, idPrefix: "wbDivMode_" }),
      F.num(V, "n1", { label: "n1 (along edge 1)", int: true, min: 1, max: 100, id: "wbDivN1", onChange: upd }),
      F.num(V, "n2", { label: "n2 (along edge 2)", int: true, min: 1, max: 100, id: "wbDivN2", onChange: upd }),
      F.num(V, "max_size", { label: "Maximum size", kind: "length", gt: 0, id: "wbDivMax", onChange: upd }),
    ]), pv];
    upd();
    return sDialog({ id: "wbDivideDlg", title: "Divide Shells", iconId: "menu-edit", size: "sm", form: F, body,
      intro: `${selShells().length} shell(s) selected. Area loads, groups and tendon hosts follow the pieces.`,
      onClose: () => ghost() && ghost().clear(), onUnits: () => { F.refresh(); upd(); },
      onOk: async () => { try { await run("shell_divide", params(), "Divide shells"); } catch (e) { return e.message; } } });
  }

  /* ---------------- merge */
  async function merge() {
    if (!needShells("Merge Shells", 2)) return null;
    try { return await run("shell_merge", {}, "Merge shells"); } catch (e) { toast("Merge Shells", e.message, "error", 7000); return null; }
  }

  /* ---------------- chop */
  const cut = { p: null, cb: null };
  function openChop() {
    if (!needShells("Chop Shells")) return null;
    const V = { mode: "grid" };
    const F = sForm(), pv = sEl("p", { class: "muted wb-preview", id: "wbChopPrev" });
    const nFrames = (S.selection || []).filter(r => r.type === "member").length;
    const upd = () => prevLine(pv, V.mode === "line" ? { n: 0, area: 0 } : preview("shell_chop", { mode: V.mode }));
    const body = [sGroup("Chop At", [F.radio(V, "mode", [
      ["grid", "Grid lines", "every line of every grid system (vertical cutting planes)"],
      ["frames", `Selected frames (${nFrames})`, "vertical planes through the frames' plan traces"],
      ["levels", "Story levels", "horizontal planes — walls / ramps spanning stories"],
      ["line", "A line drawn in the plan", "click two points after OK"]], { onChange: upd, idPrefix: "wbChop_" })]), pv,
    sNote("Convex shells are cut into exact pieces (area conserved); concave shells are skipped with a warning.")];
    upd();
    return sDialog({ id: "wbChopDlg", title: "Chop Shells", iconId: "menu-edit", size: "sm", form: F, body,
      onClose: () => ghost() && ghost().clear(),
      onOk: async () => {
        if (V.mode === "line") { startCut(); return true; }
        try { await run("shell_chop", { mode: V.mode }, "Chop shells"); } catch (e) { return e.message; }
      } });
  }
  function startCut() {
    const sel = backendSel();
    if (S.mode !== "model") sky.setMode("model");
    if (S.view === "elev") sky.setView("plan");
    cut.p = null;
    cut.cb = async (a, b) => {
      try { await run("shell_chop", { mode: "lines", lines: [[a, b]] }, "Chop shells", sel); } catch (e) { toast("Chop Shells", e.message, "error", 7000); }
      sky.setTool("select");
    };
    sky.setTool(CUT);
    toast("Chop shells", "Click two points of the cutting line in the plan.");
  }

  /* ---------------- expand / shrink */
  function openExpand() {
    if (!needShells("Expand / Shrink Shells")) return null;
    const ids = selShells();
    const one = ids.length === 1 ? S.model.shells.find(s => s.uid === ids[0]) : null;
    const V = { distance: 0.5, edge: "all" };
    const F = sForm(), pv = sEl("p", { class: "muted wb-preview", id: "wbExpPrev" });
    const params = () => (V.edge === "all" ? { distance: V.distance } : { distance: V.distance, edge: +V.edge });
    const upd = () => prevLine(pv, preview("shell_expand", params()));
    const edges = [["all", "Every edge"]];
    if (one) one.corners.forEach((c, i) => { const d = one.corners[(i + 1) % one.corners.length];
      edges.push([String(i), `Edge ${i + 1} (${sU.fmtU("length", Math.hypot(d[0] - c[0], d[1] - c[1], d[2] - c[2]), 2)})`]); });
    const body = [sGroup("Offset", [
      F.num(V, "distance", { label: "Offset distance", kind: "length", id: "wbExpDist", hint: "positive expands · negative shrinks", onChange: upd, validate: v => (v === 0 ? "Enter a non-zero offset." : "") }),
      F.select(V, "edge", edges, { label: "Edges", id: "wbExpEdge", onChange: upd }),
    ]), pv];
    upd();
    return sDialog({ id: "wbExpandDlg", title: "Expand / Shrink Shells", iconId: "menu-edit", size: "sm", form: F, body,
      intro: "Each moved edge stays parallel; its neighbours keep their lines (new corners at the intersections).",
      onClose: () => ghost() && ghost().clear(), onUnits: () => { F.refresh(); upd(); },
      onOk: async () => { try { await run("shell_expand", params(), "Expand / shrink shells"); } catch (e) { return e.message; } } });
  }

  /* ---------------- vertex reshaper */
  const R = { uid: null, work: null, drag: null };
  const horizontal = s => s && s.corners.every(c => Math.abs(c[2] - s.corners[0][2]) < 1e-6);
  function startReshape() {
    const ids = selShells();
    const s = ids.length === 1 ? S.model.shells.find(x => x.uid === ids[0]) : null;
    if (!s) { toast("Reshape Shell", "Select exactly one floor / slab (V) first.", "warn"); return false; }
    if (!horizontal(s)) { toast("Reshape Shell", "The reshaper edits horizontal shells in the plan view.", "warn"); return false; }
    if (S.mode !== "model") sky.setMode("model");
    if (S.view === "elev") sky.setView("plan");
    if (s.story && s.story !== S.story) sky.setStory(s.story);
    R.uid = s.uid; R.work = s.corners.map(c => c.slice()); R.drag = null;
    sky.setTool(TOOL);
    toast("Reshape Shell", "Drag a vertex · drag a ＋ midpoint to add a vertex · Alt+click a vertex to delete it · V / Esc to finish.");
    return true;
  }
  const handles = ed => {
    const out = [];
    R.work.forEach((c, i) => {
      const [x, y] = ed.toScreen(c[0], c[1]);
      out.push({ kind: "v", i, x, y });
      const d = R.work[(i + 1) % R.work.length], [mx, my] = ed.toScreen((c[0] + d[0]) / 2, (c[1] + d[1]) / 2);
      out.push({ kind: "m", i, x: mx, y: my });
    });
    return out;
  };
  async function commitReshape() {
    const s = S.model.shells.find(x => x.uid === R.uid);
    if (!s) return;
    if (JSON.stringify(s.corners) === JSON.stringify(R.work)) return;
    try {
      await run("shell_reshape", { mode: "corners", corners: R.work.map(c => c.map(Number)) }, "Reshape shell",
        { members: [], shells: [R.uid], links: [], points: [] });
    } catch (e) { toast("Reshape Shell", e.message, "error", 7000); }
    const s2 = S.model.shells.find(x => x.uid === R.uid);
    R.work = s2 ? s2.corners.map(c => c.slice()) : null;
    if (pe) pe.renderOverlay();
  }
  if (pe) {
    const svg = pe.svg;
    const hitHandle = e => {
      if (pe.tool !== TOOL || !R.work) return null;
      const r = svg.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
      let best = null, bd = 9;
      for (const h of handles(pe)) { const d = Math.hypot(h.x - px, h.y - py) - (h.kind === "v" ? 1 : 0); if (d < bd) { bd = d; best = h; } }
      return best;
    };
    svg.addEventListener("pointerdown", e => {
      if (e.button !== 0) return;
      const h = hitHandle(e);
      if (!h) return;
      e.stopImmediatePropagation(); e.preventDefault();
      if (h.kind === "v" && e.altKey) {
        if (R.work.length <= 3) { toast("Reshape Shell", "A shell keeps at least 3 corners.", "warn"); return; }
        R.work.splice(h.i, 1); commitReshape(); return;
      }
      if (h.kind === "m") { const a = R.work[h.i], b = R.work[(h.i + 1) % R.work.length]; R.work.splice(h.i + 1, 0, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, a[2]]); R.drag = h.i + 1; }
      else R.drag = h.i;
      try { svg.setPointerCapture(e.pointerId); } catch { /* ignore */ }
      pe.renderOverlay();
    }, true);
    svg.addEventListener("pointermove", e => {
      if (R.drag == null) return;
      e.stopImmediatePropagation();
      const r = svg.getBoundingClientRect();
      const w = pe.toWorld(e.clientX - r.left, e.clientY - r.top), sn = pe.snap(w);
      const p = sn && sn.dPx <= 14 ? sn : w;
      R.work[R.drag] = [+(+p.x).toFixed(9), +(+p.y).toFixed(9), R.work[R.drag][2]];
      pe._mouseWorld = w;
      pe.renderOverlay();
    }, true);
    const up = e => {
      if (R.drag == null) return;
      e.stopImmediatePropagation();
      R.drag = null;
      commitReshape();
    };
    svg.addEventListener("pointerup", up, true);
    svg.addEventListener("pointercancel", up, true);
    const prevOv = pe.w8Overlay;
    pe.w8Overlay = (g, ed) => {
      if (prevOv) prevOv(g, ed);
      if (ed !== pe) return;
      if (ed.tool === CUT && cut.p && ed._mouseWorld) {
        const t = ed.hoverSnap || ed._mouseWorld, [x1, y1] = ed.toScreen(cut.p[0], cut.p[1]), [x2, y2] = ed.toScreen(t.x, t.y);
        const l = document.createElementNS(NS, "line");
        Object.entries({ x1, y1, x2, y2, stroke: "#e66767", "stroke-width": 1.6, "stroke-dasharray": "6 4", "data-wb": "chopline" }).forEach(([k, v]) => l.setAttribute(k, v));
        g.appendChild(l);
      }
      if (ed.tool !== TOOL || !R.work) return;
      const pts = R.work.map(c => ed.toScreen(c[0], c[1]).join(",")).join(" ");
      const poly = document.createElementNS(NS, "polygon");
      Object.entries({ points: pts, fill: "rgba(53,181,229,0.10)", stroke: "#35b5e5", "stroke-width": 1.5, "stroke-dasharray": "5 3", "data-wb": "reshape" }).forEach(([k, v]) => poly.setAttribute(k, v));
      g.appendChild(poly);
      for (const h of handles(ed)) {
        if (h.kind === "v") {
          const c = document.createElementNS(NS, "rect");
          Object.entries({ x: h.x - 4.5, y: h.y - 4.5, width: 9, height: 9, fill: R.drag === h.i ? "#35b5e5" : "#0b1220", stroke: "#35b5e5", "stroke-width": 1.5, "data-wb-vertex": String(h.i) }).forEach(([k, v]) => c.setAttribute(k, v));
          g.appendChild(c);
        } else {
          const t = document.createElementNS(NS, "text");
          Object.entries({ x: h.x, y: h.y + 4, fill: "#35b5e5", "font-size": 13, "font-weight": 700, "text-anchor": "middle", "data-wb-mid": String(h.i) }).forEach(([k, v]) => t.setAttribute(k, v));
          t.textContent = "+";
          g.appendChild(t);
        }
      }
    };
    const prevTool = pe.w8ToolHook;
    pe.w8ToolHook = (tool, pt, w, ed) => {
      if (tool === CUT) {
        if (!cut.p) { cut.p = [pt.x, pt.y]; return; }
        const a = cut.p; cut.p = null;
        if (Math.hypot(pt.x - a[0], pt.y - a[1]) > 1e-9 && cut.cb) cut.cb(a, [pt.x, pt.y]);
        return;
      }
      if (tool === TOOL) return;                   // the reshaper uses its own handles
      return prevTool && prevTool(tool, pt, w, ed);
    };
    const os = pe.setTool.bind(pe);
    pe.setTool = t => { if (t !== TOOL) { R.work = null; R.uid = null; R.drag = null; } if (t !== CUT) cut.p = null; os(t); };
  }
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape" || !pe) return;
    if (pe.tool === TOOL && R.drag != null) { const s = S.model.shells.find(x => x.uid === R.uid); R.work = s ? s.corners.map(c => c.slice()) : null; R.drag = null; pe.renderOverlay(); }
    if (pe.tool === CUT && cut.p) { cut.p = null; pe.renderOverlay(); }
  });

  if (sky.menu) {
    sky.menu.add("Edit/Edit Shells", { label: "Divide Shells…", act: "edit-wb-shdivide", hint: "n1 × n2 · max size", fn: openDivide });
    sky.menu.add("Edit/Edit Shells", { label: "Merge Shells", act: "edit-wb-shmerge", hint: "coplanar, sharing edges", fn: merge });
    sky.menu.add("Edit/Edit Shells", { label: "Chop Shells…", act: "edit-wb-shchop", hint: "grids · frames · levels · line", fn: openChop });
    sky.menu.add("Edit/Edit Shells", { label: "Expand / Shrink Shells…", act: "edit-wb-shexpand", fn: openExpand });
    sky.menu.add("Edit/Edit Shells", { label: "Reshape Shell", act: "edit-wb-shreshape", hint: "vertex reshaper (plan)", fn: startReshape });
  }
  sky.wb.shells = { openDivide, merge, openChop, openExpand, startReshape, startCut, preview, run,
    reshape: { state: () => ({ uid: R.uid, work: R.work && R.work.map(c => c.slice()), drag: R.drag }), set: w => { R.work = w; }, commit: commitReshape }, TOOL, CUT };
}
