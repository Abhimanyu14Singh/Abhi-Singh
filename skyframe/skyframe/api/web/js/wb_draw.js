/* SkyFrame Wave B — drawing tools.

   Draw › Quick Draw ▸ Columns / Beams / Floors in a Region: two clicks in
     the plan span a rubber-band rectangle; columns go to every grid
     intersection inside it, beams on every grid-line segment between
     consecutive intersections inside it, floors in every orthogonal-grid
     bay inside it (current story, or every story with "all stories").
   Draw › Frames ▸ Frames in 3D View: click two joints (model joints and
     grid intersections at every level) in the 3D view — vertical → column,
     level → beam, otherwise brace. Works in the main 3D view and 3D windows.
   Draw › Floors & Walls ▸ Ramp… / Wall Stack… (Edit ops "ramp" /
     "wall_stack": skyframe/core/edit_shells.py).
   Draw › Dimensions & References ▸ Dimension Line (three clicks: start,
     end, offset), Reference Point (click), Dimensions & References… (the
     data dialog: dimension lines, reference points, reference planes).
     Reference points snap; x / y reference planes show as dashed plan
     lines, z planes as dashed levels in elevations.
   Edit › Auto Relabel All… (Edit op "relabel": ETABS story labels).
   Model keys: dimension_lines, reference_points, reference_planes (only
   written when used). */

import { el as dEl, esc as dEsc, dkForm as dForm, dkGroup as dGroup, dkDialog as dDialog, dkNote as dNote, dkTabs as dTabs,
  uniqueName as dUnique, clone as dClone } from "./dlgkit.js";
import dU from "./units.js";
import { gridSystems as dGridSystems, gridSystemGeometry as dGridGeom, gridTransform as dGridT } from "./modeledit.js";
import { relabelMap as dRelabelMap, RELABEL_KINDS as dRelabelKinds, rampCorners as dRampCorners, wallStackPlan as dWallPlan,
  WALL_SHAPES as dWallShapes, generalIntersections as dGenInts, generalLinesGlobal as dGenLines } from "./wb_shellgeom.js";

const NS = "http://www.w3.org/2000/svg";
const Q = { qcol: "wbqcol", qbeam: "wbqbeam", qfloor: "wbqfloor" };
const DIM = "wbdim", REFPT = "wbrefpt";
const svgEl = (tag, a) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); return e; };
const r9 = v => { const r = Math.round(v * 1e9) / 1e9; return r === 0 ? 0 : r; };

export function initWbDraw(sky) {
  const S = sky.store, pe = sky.planEditor, ee = sky.elevEditor, ME = sky.ME;
  const toast = (t, m, k = "info", ms = 4000) => sky.toast && sky.toast(t, m, k, ms);
  const storyZ = name => { const s = (S.model.stories || []).find(x => x.name === name); return s ? { zb: s.elevation - s.height, zt: s.elevation } : { zb: 0, zt: 0 }; };
  const targetStories = () => (S.applyAll ? S.model.stories.map(s => s.name) : [S.story]);
  const refreshAll = () => {
    if (sky.planEditor) sky.planEditor.refresh();
    if (sky.elevEditor) sky.elevEditor.refresh();
    if (sky.viewer) { const v = sky.viewer, cam = [v.yaw, v.pitch, v.dist, v.target]; v.setModel(S.model); [v.yaw, v.pitch, v.dist, v.target] = cam; v._dirty = true; }
    sky.renderProps && sky.renderProps();
  };
  const toPlan = () => { if (S.mode !== "model") sky.setMode("model"); if (S.view === "elev") sky.setView("plan"); };

  /* ================================================================ quick draw */
  /** Grid features inside the plan box: {points, segs, cells} (GLOBAL). */
  function gridInBox(m, b) {
    const xa = Math.min(b[0][0], b[1][0]) - 1e-6, xb = Math.max(b[0][0], b[1][0]) + 1e-6;
    const ya = Math.min(b[0][1], b[1][1]) - 1e-6, yb = Math.max(b[0][1], b[1][1]) + 1e-6;
    const inB = p => p[0] >= xa && p[0] <= xb && p[1] >= ya && p[1] <= yb;
    const points = [], segs = [], cells = [];
    const seen = new Set(), key = p => `${r9(p[0]).toFixed(6)},${r9(p[1]).toFixed(6)}`;
    const addP = p => { if (inB(p) && !seen.has(key(p))) { seen.add(key(p)); points.push([r9(p[0]), r9(p[1])]); } };
    const addS = (a, c) => { if (inB(a) && inB(c) && Math.hypot(c[0] - a[0], c[1] - a[1]) > 1e-6) segs.push([[r9(a[0]), r9(a[1])], [r9(c[0]), r9(c[1])]]); };
    for (const sys of dGridSystems(m)) {
      if (sys.kind === "general") {
        const ints = dGenInts(sys).filter(it => it.label.includes("-"));   // line crossings (end points only snap)
        ints.forEach(it => addP(it.point));
        for (const ln of dGenLines(sys)) {
          const [a, c] = ln.points, L2 = (c[0] - a[0]) ** 2 + (c[1] - a[1]) ** 2;
          const on = ints.map(it => it.point).filter(p => Math.abs((p[0] - a[0]) * (c[1] - a[1]) - (p[1] - a[1]) * (c[0] - a[0])) < 1e-6 * Math.sqrt(L2))
            .map(p => [((p[0] - a[0]) * (c[0] - a[0]) + (p[1] - a[1]) * (c[1] - a[1])) / L2, p]).filter(([t]) => t > -1e-9 && t < 1 + 1e-9).sort((u, v) => u[0] - v[0]);
          for (let k = 0; k + 1 < on.length; k++) addS(on[k][1], on[k + 1][1]);
        }
        continue;
      }
      const T = dGridT(sys);
      if (sys.kind === "radial") {
        const geo = dGridGeom(sys);
        geo.intersections.forEach(addP);
        const ox = sys.origin[0], oy = sys.origin[1], rr = [...sys.radii].sort((u, v) => u - v);
        for (const th of sys.theta_deg) { const a = (th + (sys.rotation || 0)) * Math.PI / 180; for (let k = 0; k + 1 < rr.length; k++) addS([ox + rr[k] * Math.cos(a), oy + rr[k] * Math.sin(a)], [ox + rr[k + 1] * Math.cos(a), oy + rr[k + 1] * Math.sin(a)]); }
        continue;
      }
      const xs = sys.x_lines || [], ys = sys.y_lines || [];
      for (const x of xs) for (const y of ys) addP(T(x, y));
      for (const x of xs) for (let j = 0; j + 1 < ys.length; j++) addS(T(x, ys[j]), T(x, ys[j + 1]));
      for (const y of ys) for (let i = 0; i + 1 < xs.length; i++) addS(T(xs[i], y), T(xs[i + 1], y));
      for (let i = 0; i + 1 < xs.length; i++) for (let j = 0; j + 1 < ys.length; j++) {
        const c = [T(xs[i], ys[j]), T(xs[i + 1], ys[j]), T(xs[i + 1], ys[j + 1]), T(xs[i], ys[j + 1])];
        if (c.every(inB)) cells.push({ c, rot: sys.rotation || 0 });
      }
    }
    return { points, segs, cells };
  }
  function quickDraw(kind, box) {
    const m = S.model;
    const g = gridInBox(m, box);
    let made = 0;
    for (const st of targetStories()) {
      if (kind === "qcol") for (const p of g.points) { if (ME.addColumn(m, p[0], p[1], st)) made++; }
      else if (kind === "qbeam") for (const [a, c] of g.segs) { if (ME.addBeam(m, { x: a[0], y: a[1] }, { x: c[0], y: c[1] }, st)) made++; }
      else for (const cell of g.cells) {
        if (Math.abs(cell.rot) < 1e-12) { const xs = cell.c.map(p => p[0]), ys = cell.c.map(p => p[1]); if (ME.addSlab(m, Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys), st)) made++; continue; }
        const { zt } = storyZ(st), corners = cell.c.map(p => [r9(p[0]), r9(p[1]), zt]);
        const dup = m.shells.some(s => s.kind === "slab" && s.story === st && s.corners.length === 4 && s.corners.every((q, i) => Math.hypot(q[0] - corners[i][0], q[1] - corners[i][1], q[2] - corners[i][2]) < 1e-6));
        if (dup) continue;
        m.shells.push({ uid: ME.nextUid(m, "SL"), kind: "slab", behavior: "shell", section: ME.defaultShellSection(m), corners, mesh_size: 1.0, story: st, openings: [] });
        made++;
      }
    }
    if (made) { sky.markDirty(); refreshAll(); }
    toast("Quick draw", `${made} ${kind === "qcol" ? "column" : kind === "qbeam" ? "beam" : "floor"}${made === 1 ? "" : "s"} added${S.applyAll ? " on every story" : ` on ${S.story}`}${made ? "" : " (nothing new in the region)"}`, "info", 3000);
    return made;
  }
  const qd = { tool: null, p: null };
  const startQuick = kind => {
    toPlan(); qd.tool = Q[kind]; qd.p = null;
    sky.setTool(Q[kind]);
    toast("Quick draw", "Click two opposite corners of the region in the plan (snaps). V or Esc to finish.");
  };

  /* ================================================================ dimension lines / reference points */
  const dim = { p: [], ok: false };
  const dims = () => S.model.dimension_lines || [];
  const refPts = () => S.model.reference_points || [];
  const refPlanes = () => S.model.reference_planes || [];
  function setKey(key, list) { if (list.length) S.model[key] = list; else delete S.model[key]; sky.markDirty(); refreshAll(); }
  const dimLabel = d => dU.fmtU("length", Math.hypot(d.p2[0] - d.p1[0], d.p2[1] - d.p1[1], d.p2[2] - d.p1[2]), 2);
  function dimGeom(a, b, off) {
    const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1, nx = -dy / L, ny = dx / L;
    return { a2: [a[0] + nx * off, a[1] + ny * off], b2: [b[0] + nx * off, b[1] + ny * off], n: [nx, ny] };
  }
  const offsetOf = (a, b, p) => { const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1; return ((p[0] - a[0]) * -dy + (p[1] - a[1]) * dx) / L; };
  function startDim() { toPlan(); dim.p = []; sky.setTool(DIM); toast("Dimension line", "Click the start, the end, then the offset position. V or Esc to finish."); }
  function startRefPoint() { toPlan(); sky.setTool(REFPT); toast("Reference point", "Click a point in the plan (story level). V or Esc to finish."); }

  /* ---- plan hooks */
  if (pe) {
    const prevTool = pe.w8ToolHook;
    pe.w8ToolHook = (tool, pt, w, ed) => {
      if (Object.values(Q).includes(tool)) {
        if (!qd.p) { qd.p = [pt.x, pt.y]; return; }
        const a = qd.p; qd.p = null;
        const kind = Object.keys(Q).find(k => Q[k] === tool);
        quickDraw(kind, [a, [pt.x, pt.y]]);
        return;
      }
      if (tool === DIM) {
        dim.p.push(dim.p.length === 2 ? [w.x, w.y] : [pt.x, pt.y]);   // the offset click is free (not snapped)
        if (dim.p.length < 3) return;
        const [a, b, c] = dim.p; dim.p = [];
        if (Math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9) return;
        const { zt } = storyZ(S.story);
        setKey("dimension_lines", [...dims(), { p1: [r9(a[0]), r9(a[1]), zt], p2: [r9(b[0]), r9(b[1]), zt], offset: r9(offsetOf(a, b, c)), story: S.story }]);
        return;
      }
      if (tool === REFPT) {
        const { zt } = storyZ(S.story);
        const name = dUnique(new Set(refPts().map(r => r.name)), "RP" + (refPts().length + 1));
        setKey("reference_points", [...refPts(), { name, point: [r9(pt.x), r9(pt.y), zt] }]);
        return;
      }
      return prevTool && prevTool(tool, pt, w, ed);
    };
    const prevSnap = pe.w8SnapHook;
    pe.w8SnapHook = (w, ed) => {
      const o = prevSnap ? prevSnap(w, ed) : null;
      if (ed.tool === "select" || ed.tool === "erase") return o;
      let best = null;
      for (const r of refPts()) { const d = Math.hypot(r.point[0] - w.x, r.point[1] - w.y) * ed.scale; if (d <= 12 && (!best || d < best.dPx)) best = { x: r.point[0], y: r.point[1], dPx: d }; }
      for (const pl of refPlanes()) {
        if (pl.axis === "z") continue;
        const x = pl.axis === "x" ? pl.coord : w.x, y = pl.axis === "y" ? pl.coord : w.y, d = Math.hypot(x - w.x, y - w.y) * ed.scale;
        if (d <= 8 && (!best || d < best.dPx - 4)) best = { x, y, dPx: d };
      }
      if (best && (!o || best.dPx <= o.dPx)) return best;
      return o;
    };
    const prevOv = pe.w8Overlay;
    pe.w8Overlay = (g, ed) => {
      if (prevOv) prevOv(g, ed);
      const m = ed.opts.getModel(), story = ed.opts.getStory();
      if (!m) return;
      const T = (x, y) => ed.toScreen(x, y);
      // reference planes (x / y) — dashed lines across the view
      for (const pl of m.reference_planes || []) {
        if (pl.axis === "z") continue;
        const a = pl.axis === "x" ? T(pl.coord, ed.toWorld(0, 0).y) : T(ed.toWorld(0, 0).x, pl.coord);
        const b = pl.axis === "x" ? T(pl.coord, ed.toWorld(0, ed.h).y) : T(ed.toWorld(ed.w, 0).x, pl.coord);
        g.appendChild(svgEl("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: "rgba(167,139,250,0.75)", "stroke-width": 1, "stroke-dasharray": "10 4 2 4", "data-wb-refplane": pl.name }));
        const t = svgEl("text", { x: a[0] + 4, y: a[1] + 12, fill: "rgba(167,139,250,0.95)", "font-size": 10 });
        t.textContent = pl.name; g.appendChild(t);
      }
      // reference points
      for (const r of m.reference_points || []) {
        const [x, y] = T(r.point[0], r.point[1]);
        const st = storyZ(story), on = r.point[2] >= st.zb - 1e-6 && r.point[2] <= st.zt + 1e-6;
        const col = on ? "#a78bfa" : "rgba(167,139,250,0.45)";
        g.appendChild(svgEl("circle", { cx: x, cy: y, r: 5, fill: "none", stroke: col, "stroke-width": 1.4, "data-wb-refpt": r.name }));
        g.appendChild(svgEl("path", { d: `M${x - 7},${y} L${x + 7},${y} M${x},${y - 7} L${x},${y + 7}`, stroke: col, "stroke-width": 1 }));
        const t = svgEl("text", { x: x + 8, y: y - 6, fill: col, "font-size": 10 }); t.textContent = r.name; g.appendChild(t);
      }
      // dimension lines of this story
      const all = (m.dimension_lines || []).filter(dl => !dl.story || dl.story === story);
      const pend = ed.tool === DIM && dim.p.length && ed._mouseWorld ? (() => {
        const mw = ed.hoverSnap || ed._mouseWorld;
        if (dim.p.length === 1) return { p1: [...dim.p[0], 0], p2: [mw.x, mw.y, 0], offset: 0, live: true };
        return { p1: [...dim.p[0], 0], p2: [...dim.p[1], 0], offset: offsetOf(dim.p[0], dim.p[1], [mw.x, mw.y]), live: true };
      })() : null;
      for (const dl of pend ? [...all, pend] : all) {
        if (Math.hypot(dl.p2[0] - dl.p1[0], dl.p2[1] - dl.p1[1]) < 1e-9) continue;
        const gm = dimGeom(dl.p1, dl.p2, dl.offset || 0);
        const A = T(dl.p1[0], dl.p1[1]), B = T(dl.p2[0], dl.p2[1]), A2 = T(...gm.a2), B2 = T(...gm.b2);
        const col = dl.live ? "rgba(245,190,60,0.95)" : "#f5be3c";
        const grp = svgEl("g", { "data-wb-dim": dl.live ? "live" : "1" });
        grp.appendChild(svgEl("line", { x1: A[0], y1: A[1], x2: A2[0], y2: A2[1], stroke: col, "stroke-width": 0.8, opacity: 0.7 }));
        grp.appendChild(svgEl("line", { x1: B[0], y1: B[1], x2: B2[0], y2: B2[1], stroke: col, "stroke-width": 0.8, opacity: 0.7 }));
        grp.appendChild(svgEl("line", { x1: A2[0], y1: A2[1], x2: B2[0], y2: B2[1], stroke: col, "stroke-width": 1.2 }));
        for (const P of [A2, B2]) grp.appendChild(svgEl("path", { d: `M${P[0] - 4},${P[1] + 4} L${P[0] + 4},${P[1] - 4}`, stroke: col, "stroke-width": 1.6 }));
        const ang = Math.atan2(B2[1] - A2[1], B2[0] - A2[0]) * 180 / Math.PI, flip = ang > 90 || ang < -90 ? 180 : 0;
        const mx = (A2[0] + B2[0]) / 2, my = (A2[1] + B2[1]) / 2;
        const t = svgEl("text", { x: mx, y: my - 4, fill: col, "font-size": 11, "font-weight": 600, "text-anchor": "middle", transform: `rotate(${ang + flip} ${mx} ${my})` });
        t.textContent = dimLabel(dl); grp.appendChild(t);
        g.appendChild(grp);
      }
      // quick-draw rubber band
      if (Object.values(Q).includes(ed.tool) && qd.p && ed._mouseWorld) {
        const mw = ed.hoverSnap || ed._mouseWorld, [x1, y1] = T(qd.p[0], qd.p[1]), [x2, y2] = T(mw.x, mw.y);
        g.appendChild(svgEl("rect", { x: Math.min(x1, x2), y: Math.min(y1, y2), width: Math.abs(x2 - x1), height: Math.abs(y2 - y1),
          fill: "rgba(53,181,229,0.08)", stroke: "#35b5e5", "stroke-width": 1.2, "stroke-dasharray": "6 4", "data-wb": "quickbox" }));
        const q = gridInBox(m, [qd.p, [mw.x, mw.y]]);
        const kind = Object.keys(Q).find(k => Q[k] === ed.tool);
        if (kind === "qcol") for (const p of q.points) { const [x, y] = T(p[0], p[1]); g.appendChild(svgEl("rect", { x: x - 3, y: y - 3, width: 6, height: 6, fill: "rgba(95,143,201,0.7)" })); }
        else if (kind === "qbeam") for (const [a, c] of q.segs) { const [ax, ay] = T(a[0], a[1]), [cx, cy] = T(c[0], c[1]); g.appendChild(svgEl("line", { x1: ax, y1: ay, x2: cx, y2: cy, stroke: "rgba(119,135,155,0.9)", "stroke-width": 2, "stroke-dasharray": "5 3" })); }
        else for (const cell of q.cells) g.appendChild(svgEl("polygon", { points: cell.c.map(p => T(p[0], p[1]).join(",")).join(" "), fill: "rgba(154,167,180,0.18)", stroke: "rgba(154,167,180,0.6)" }));
      }
    };
    const os = pe.setTool.bind(pe);
    pe.setTool = t => { qd.p = null; dim.p = []; os(t); };
  }
  /* elevation: z reference planes as dashed levels */
  if (ee) {
    const prevPt = ee.ptOverlay;
    ee.ptOverlay = (g, ed) => {
      if (prevPt) prevPt(g, ed);
      const m = ed.opts.getModel();
      for (const pl of (m && m.reference_planes) || []) {
        if (pl.axis !== "z") continue;
        const [, y] = ed.toScreen(0, pl.coord);
        g.appendChild(svgEl("line", { x1: 0, y1: y, x2: ed.w, y2: y, stroke: "rgba(167,139,250,0.75)", "stroke-width": 1, "stroke-dasharray": "10 4 2 4", "data-wb-refplane": pl.name }));
        const t = svgEl("text", { x: 6, y: y - 4, fill: "rgba(167,139,250,0.95)", "font-size": 10 }); t.textContent = `${pl.name} · z ${dU.fmtU("length", pl.coord, 2)}`; g.appendChild(t);
      }
    };
  }
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape" || !pe) return;
    if (qd.p || dim.p.length) { qd.p = null; dim.p = []; pe.renderOverlay(); }
  });

  /** Dimensions & References data dialog (three tables). */
  function openAids(tab = "dims") {
    const W = { dims: dClone(dims()), pts: dClone(refPts()), planes: dClone(refPlanes()) };
    for (const d of W.dims) { d.len = Math.hypot(d.p2[0] - d.p1[0], d.p2[1] - d.p1[1], d.p2[2] - d.p1[2]); }
    const F = dForm(), L = "length";
    const flat = r => { for (const [k, a, i] of [["x", "point", 0], ["y", "point", 1], ["z", "point", 2]]) Object.defineProperty(r, k, { get: () => r[a][i], set: v => { r[a][i] = v; }, enumerable: false, configurable: true }); return r; };
    W.pts.forEach(flat);
    const tDims = F.table(W.dims, [{ key: "story", label: "Story", type: "ro", get: r => r.story || "—" }, { key: "len", label: "Length", type: "ro", get: r => dimLabel(r) },
      { key: "offset", label: "Offset", type: "num", kind: L }], { id: "wbAidDims", empty: "No dimension lines — use <b>Draw › Dimensions &amp; References › Dimension Line</b>." });
    const tPts = F.table(W.pts, [{ key: "name", label: "Name", type: "text" }, { key: "x", label: "X", type: "num", kind: L }, { key: "y", label: "Y", type: "num", kind: L }, { key: "z", label: "Z", type: "num", kind: L }], {
      id: "wbAidPts", addLabel: "+ Add point", newRow: r => flat({ name: dUnique(new Set(r.map(x => x.name)), "RP" + (r.length + 1)), point: [0, 0, storyZ(S.story).zt] }),
      validate: r => { const s = new Set(); for (const x of r) { if (!x.name) return "Every point needs a name."; if (s.has(x.name)) return `“${x.name}” is used twice.`; s.add(x.name); } return ""; } });
    const tPl = F.table(W.planes, [{ key: "name", label: "Name", type: "text" }, { key: "axis", label: "Normal", type: "select", options: [["z", "Z (level)"], ["x", "X = const"], ["y", "Y = const"]] },
      { key: "coord", label: "Coordinate", type: "num", kind: L }], {
      id: "wbAidPlanes", addLabel: "+ Add plane", newRow: r => ({ name: dUnique(new Set(r.map(x => x.name)), "RPL" + (r.length + 1)), axis: "z", coord: storyZ(S.story).zt }),
      validate: r => { const s = new Set(); for (const x of r) { if (!x.name) return "Every plane needs a name."; if (s.has(x.name)) return `“${x.name}” is used twice.`; s.add(x.name); } return ""; } });
    const tabs = dTabs([{ id: "dims", label: "Dimension Lines", body: tDims }, { id: "pts", label: "Reference Points", body: tPts },
      { id: "planes", label: "Reference Planes", body: tPl }], { active: tab, id: "wbAidTabs" });
    return dDialog({ id: "wbAidsDlg", title: "Dimensions and References", iconId: "exleaf-grids", size: "md", form: F, tabs, body: [tabs,
      dNote("Drafting aids only — they never change the analysis. Reference points and X / Y planes snap while drawing.")],
    onUnits: () => F.refresh(),
    onOk: () => {
      const d2 = W.dims.map(d => { const o = { p1: d.p1, p2: d.p2, offset: +d.offset || 0 }; if (d.story) o.story = d.story; if (d.label) o.label = d.label; return o; });
      const p2 = W.pts.map(r => ({ name: r.name, point: r.point.map(Number) }));
      const l2 = W.planes.map(r => ({ name: r.name, axis: r.axis || "z", coord: +r.coord }));
      const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
      let ch = false;
      for (const [k, v] of [["dimension_lines", d2], ["reference_points", p2], ["reference_planes", l2]]) {
        if (same(v, S.model[k] || [])) continue;
        if (v.length) S.model[k] = v; else delete S.model[k];
        ch = true;
      }
      if (ch) { sky.markDirty(); refreshAll(); }
    } });
  }

  /* ================================================================ frames in the 3D view */
  const D3 = { on: false, a: null, hover: null, cands: null, viewers: new Map() };
  function candidates() {
    const m = S.model, seen = new Set(), out = [];
    const add = p => { const k = p.map(v => r9(+v).toFixed(5)).join(","); if (!seen.has(k)) { seen.add(k); out.push(p.map(Number)); } };
    for (const mm of m.members || []) { add(mm.pi); add(mm.pj); }
    for (const s of m.shells || []) for (const c of s.corners) add(c);
    const levels = [...new Set([(m.stories[0] ? m.stories[0].elevation - m.stories[0].height : 0), ...m.stories.map(s => s.elevation)])];
    for (const sys of dGridSystems(m)) for (const p of dGridGeom(sys).intersections) for (const z of levels) add([r9(p[0]), r9(p[1]), z]);
    return out;
  }
  function pick(v, mx, my) {
    const P = v._makeProjector();
    let best = null, bd = 12;
    for (const p of D3.cands || []) {
      const c = P.toCam(p);
      if (c[2] < P.near) continue;
      const s = P.proj(c), d = Math.hypot(s.x - mx, s.y - my);
      if (d < bd - 0.5 || (best && Math.abs(d - bd) <= 0.5 && c[2] < best.z)) { bd = d; best = { p, x: s.x, y: s.y, z: c[2] }; }
    }
    return best;
  }
  function drawOverlay(v) {
    const ov = D3.viewers.get(v);
    if (!ov) return;
    const c = ov.canvas, w = v.canvas.clientWidth, h = v.canvas.clientHeight, dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    if (!D3.on) return;
    const P = v._makeProjector();
    const sp = p => { const cc = P.toCam(p); return cc[2] < P.near ? null : P.proj(cc); };
    if (D3.a) {
      const a = sp(D3.a);
      if (a) {
        g.fillStyle = "#35b5e5"; g.beginPath(); g.arc(a.x, a.y, 4, 0, 7); g.fill();
        const hv = ov.hover;
        if (hv) { g.strokeStyle = "rgba(53,181,229,0.9)"; g.setLineDash([6, 4]); g.lineWidth = 2; g.beginPath(); g.moveTo(a.x, a.y); g.lineTo(hv.x, hv.y); g.stroke(); g.setLineDash([]); }
      }
    }
    if (ov.hover) { g.strokeStyle = "#35b5e5"; g.lineWidth = 1.5; g.beginPath(); g.arc(ov.hover.x, ov.hover.y, 7, 0, 7); g.stroke(); }
  }
  function addFrame3d(a, b) {
    const m = S.model;
    const dz = Math.abs(b[2] - a[2]), dxy = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (dz < 1e-6 && dxy < 1e-6) return null;
    const kind = dxy < 1e-6 ? "column" : dz < 1e-6 ? "beam" : "brace";
    const [pi, pj] = a[2] <= b[2] ? [a, b] : [b, a];
    const story = kind === "beam" ? ME.storyAtLevel(m, pi[2]) : ME.storyContainingZ(m, pj[2]);
    let mem = null;
    if (kind === "beam") mem = ME.addBeamAt(m, pi, pj, story);
    else if (kind === "brace") mem = ME.addBraceAt(m, pi, pj, story);
    else {
      if (m.members.some(x => x.kind === "column" && Math.hypot(...x.pi.map((v, k) => v - pi[k])) < 1e-6 && Math.hypot(...x.pj.map((v, k) => v - pj[k])) < 1e-6)) return null;
      mem = { uid: ME.nextUid(m, "C"), kind: "column", section: ME.defaultFrameSection(m, "column"), pi: pi.slice(), pj: pj.slice(), story,
        length: Math.hypot(pj[0] - pi[0], pj[1] - pi[1], pj[2] - pi[2]), releases: "" };
      m.members.push(mem);
    }
    if (!mem) return null;
    sky.markDirty();
    refreshAll();
    return mem;
  }
  function attach(v) {
    if (!v || D3.viewers.has(v)) return;
    const parent = v.canvas.parentElement;
    if (getComputedStyle(parent).position === "static") parent.style.position = "relative";
    const canvas = dEl("canvas", { class: "wb-3d-overlay", "aria-hidden": "true" });
    parent.appendChild(canvas);
    const ov = { canvas, hover: null, down: null };
    D3.viewers.set(v, ov);
    const pos = e => { const r = v.canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    v.canvas.addEventListener("pointerdown", e => { if (D3.on && e.button === 0) ov.down = [e.clientX, e.clientY]; });
    v.canvas.addEventListener("pointermove", e => {
      if (!D3.on) return;
      const [x, y] = pos(e);
      ov.hover = pick(v, x, y);
      drawOverlay(v);
    });
    v.canvas.addEventListener("pointerup", e => {
      if (!D3.on || !ov.down) return;
      const moved = Math.hypot(e.clientX - ov.down[0], e.clientY - ov.down[1]);
      ov.down = null;
      if (moved > 5) return;
      const [x, y] = pos(e);
      const h = pick(v, x, y);
      if (!h) return;
      if (!D3.a) { D3.a = h.p; }
      else {
        const mem = addFrame3d(D3.a, h.p);
        if (mem) { toast("Frame drawn in 3D", `${mem.kind} ${mem.uid} (${mem.story})`, "info", 2200); D3.cands = candidates(); }
        D3.a = h.p;                                  // chain from the last joint
      }
      for (const vv of D3.viewers.keys()) drawOverlay(vv);
    });
    v.canvas.addEventListener("pointerleave", () => { ov.hover = null; drawOverlay(v); });
    v.canvas.addEventListener("dblclick", () => { if (D3.on) { D3.a = null; drawOverlay(v); } });
    const om = v.onMemberClick;
    v.onMemberClick = seg => { if (D3.on) return; if (om) om(seg); };
  }
  function detach(v) {
    const ov = D3.viewers.get(v);
    if (ov) { ov.canvas.remove(); D3.viewers.delete(v); }
  }
  function start3d() {
    if (sky.etabs && sky.etabs.showResult) sky.etabs.showResult("view3d"); else { sky.setMode("analyze"); sky.switchTab("view3d"); }
    D3.on = true; D3.a = null; D3.cands = candidates();
    for (const v of D3.viewers.keys()) drawOverlay(v);
    toast("Frames in 3D view", "Click two joints (grid points at every level count): vertical → column, level → beam, else brace. Double-click restarts the chain; Esc finishes.", "info", 6000);
  }
  function stop3d() { D3.on = false; D3.a = null; for (const v of D3.viewers.keys()) { D3.viewers.get(v).hover = null; drawOverlay(v); } }
  if (sky.viewer) attach(sky.viewer);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && D3.on) stop3d(); });
  document.addEventListener("sky:model-changed", () => { if (D3.on) D3.cands = candidates(); });
  const st0 = sky.setTool;
  sky.setTool = t => { if (D3.on && t === "select") stop3d(); return st0(t); };

  /* ================================================================ ramp / wall stack / relabel */
  const runOp = async (op, params, label) => {
    const sm = await sky.edit.run(op, params, { label, sel: { members: [], shells: [], links: [], points: [] } });
    for (const w of (sm.warnings || []).slice(0, 3)) toast(label, w, "warn", 6000);
    return sm;
  };
  const stOpts = () => (S.model.stories || []).map(s => [s.name, s.name]);
  const shellOpts = () => Object.keys(S.model.shell_sections || {}).map(n => [n, n]);
  function openRamp() {
    const sts = S.model.stories || [];
    if (sts.length < 2 || !Object.keys(S.model.shell_sections || {}).length) { toast("Draw Ramp", "A ramp needs two stories and a slab section.", "warn"); return null; }
    const i = Math.max(1, sts.findIndex(s => s.name === S.story));
    const g = S.model.grid || {};
    const x0 = (g.x_lines || [0])[0], y0 = (g.y_lines || [0])[0];
    const sec = Object.keys(S.model.shell_sections).find(n => n.toUpperCase().includes("SLAB")) || Object.keys(S.model.shell_sections)[0];
    const V = { x1: x0, y1: y0 + 1.5, x2: x0 + 8, y2: y0 + 1.5, width: 3, story_from: sts[i - 1].name, story_to: sts[i].name, section: sec, behavior: "shell", mesh_size: 1 };
    const F = dForm(), pv = dEl("p", { class: "muted wb-preview", id: "wbRampPrev" });
    const params = () => ({ p1: [V.x1, V.y1], p2: [V.x2, V.y2], width: V.width, story_from: V.story_from, story_to: V.story_to, section: V.section, behavior: V.behavior, mesh_size: V.mesh_size });
    const upd = () => {
      try {
        const z1 = sts.find(s => s.name === V.story_from).elevation, z2 = sts.find(s => s.name === V.story_to).elevation;
        const c = dRampCorners([V.x1, V.y1], [V.x2, V.y2], V.width, z1, z2);
        if (sky.edit && sky.edit.ghost) sky.edit.ghost.show({ polys: [c] });
        const run = Math.hypot(V.x2 - V.x1, V.y2 - V.y1);
        pv.textContent = `Slope ${(Math.abs(z2 - z1) / run * 100).toFixed(1)} % · rise ${dU.fmtU("length", Math.abs(z2 - z1), 2)} over ${dU.fmtU("length", run, 2)}`;
      } catch (e) { pv.textContent = e.message; }
    };
    const body = [dGroup("Ramp Centre Line (plan)", [F.num(V, "x1", { label: "Start X", kind: "length", onChange: upd, id: "wbRampX1" }), F.num(V, "y1", { label: "Start Y", kind: "length", onChange: upd, id: "wbRampY1" }),
      F.num(V, "x2", { label: "End X", kind: "length", onChange: upd, id: "wbRampX2" }), F.num(V, "y2", { label: "End Y", kind: "length", onChange: upd, id: "wbRampY2" }),
      F.num(V, "width", { label: "Width", kind: "length", gt: 0, onChange: upd, id: "wbRampW" })], { cols: 2 }),
    dGroup("Levels and Property", [F.select(V, "story_from", stOpts, { label: "Start at story", onChange: upd, id: "wbRampFrom", validate: v => (v === V.story_to ? "Pick two different stories." : "") }),
      F.select(V, "story_to", stOpts, { label: "End at story", onChange: upd, id: "wbRampTo" }),
      F.select(V, "section", shellOpts, { label: "Slab section", id: "wbRampSec" }),
      F.select(V, "behavior", [["shell", "Shell (meshed)"], ["membrane", "Membrane"]], { label: "Behavior" }),
      F.num(V, "mesh_size", { label: "Mesh size", kind: "length", gt: 0 })]), pv,
    dNote("The ramp's two level edges join the story diaphragms; its sloped interior stays free (ETABS). Its story is the upper story.")];
    upd();
    return dDialog({ id: "wbRampDlg", title: "Draw Ramp", iconId: "menu-draw", size: "md", form: F, body,
      onClose: () => sky.edit && sky.edit.ghost && sky.edit.ghost.clear(), onUnits: () => { F.refresh(); upd(); },
      onOk: async () => { try { const sm = await runOp("ramp", params(), "Draw ramp"); toast("Draw Ramp", `${sm.created.shells.join(", ")} · slope ${(sm.slope * 100).toFixed(1)} %`); } catch (e) { return e.message; } } });
  }
  function openWallStack() {
    if (!Object.keys(S.model.shell_sections || {}).length) { toast("Wall Stack", "Define a wall section first.", "warn"); return null; }
    const secs = Object.keys(S.model.shell_sections);
    const V = { shape: "C", B: 3, D: 2.5, ox: 0, oy: 0, rotation: 0, section: secs.find(n => n.toUpperCase().includes("WALL")) || secs[0], pier: "", mesh_size: 1 };
    const g = S.model.grid || {};
    V.ox = (g.x_lines || [0])[0]; V.oy = (g.y_lines || [0])[0];
    const chosen = new Set((S.model.stories || []).map(s => s.name));
    const F = dForm(), pv = dEl("p", { class: "muted wb-preview", id: "wbStackPrev" });
    const upd = () => {
      try {
        const legs = dWallPlan({ shape: V.shape, B: V.B, D: V.D, origin: [V.ox, V.oy], rotation: V.rotation });
        const z = storyZ(S.story).zt;
        if (sky.edit && sky.edit.ghost) sky.edit.ghost.show({ segs: legs.map(([a, b]) => [[a[0], a[1], z], [b[0], b[1], z]]) });
        pv.textContent = `${legs.length} wall leg${legs.length === 1 ? "" : "s"} × ${chosen.size} stor${chosen.size === 1 ? "y" : "ies"} = ${legs.length * chosen.size} walls · pier ${V.pier || "auto (next P#)"}`;
      } catch (e) { pv.textContent = e.message; }
    };
    const box = dEl("div", { class: "wb-story-checks", id: "wbStackStories" });
    for (const s of [...(S.model.stories || [])].reverse()) {
      const cb = dEl("input", { type: "checkbox", checked: true, "data-story": s.name });
      cb.addEventListener("change", () => { if (cb.checked) chosen.add(s.name); else chosen.delete(s.name); upd(); });
      box.appendChild(dEl("label", { class: "dk-check" }, [cb, dEl("span", { text: s.name })]));
    }
    const body = [dGroup("Stack Shape", [F.select(V, "shape", dWallShapes.map(s => [s, s === "straight" ? "Straight" : s === "box" ? "Box (closed core)" : s + "-shape"]), { label: "Shape", id: "wbStackShape", onChange: upd }),
      F.num(V, "B", { label: "Flange / length B", kind: "length", gt: 0, onChange: upd, id: "wbStackB" }),
      F.num(V, "D", { label: "Web depth D", kind: "length", min: 0, onChange: upd, id: "wbStackD" }),
      F.num(V, "ox", { label: "Insertion X", kind: "length", onChange: upd, id: "wbStackOx" }),
      F.num(V, "oy", { label: "Insertion Y", kind: "length", onChange: upd, id: "wbStackOy" }),
      F.num(V, "rotation", { label: "Rotation (CCW)", unit: "deg", onChange: upd, id: "wbStackRot" })], { cols: 2 }),
    dGroup("Stories, Section and Pier", [box, F.select(V, "section", shellOpts, { label: "Wall section", id: "wbStackSec" }),
      F.text(V, "pier", { label: "Pier label", required: false, placeholder: "auto: next P#", id: "wbStackPier", onChange: upd }),
      F.num(V, "mesh_size", { label: "Mesh size", kind: "length", gt: 0 })]), pv];
    upd();
    return dDialog({ id: "wbStackDlg", title: "Draw Wall Stack", iconId: "menu-draw", size: "md", form: F, body,
      intro: "ETABS Draw Wall Stacks: centre-line wall legs on every chosen story, all with one pier label (flanges split at web junctions).",
      onClose: () => sky.edit && sky.edit.ghost && sky.edit.ghost.clear(), onUnits: () => { F.refresh(); upd(); },
      onOk: async () => {
        if (!chosen.size) return "Choose at least one story.";
        const p = { shape: V.shape, B: V.B, D: V.shape === "straight" ? 0 : V.D, origin: [V.ox, V.oy], rotation: V.rotation, section: V.section,
          stories: (S.model.stories || []).map(s => s.name).filter(n => chosen.has(n)), mesh_size: V.mesh_size };
        if (V.pier.trim()) p.pier = V.pier.trim();
        try { const sm = await runOp("wall_stack", p, "Wall stack"); toast("Wall Stack", `${sm.walls} walls · pier ${sm.pier}`); } catch (e) { return e.message; }
      } });
  }
  function openRelabel() {
    const V = Object.fromEntries(dRelabelKinds.map(k => [k, true]));
    const F = dForm(), pv = dEl("div", { class: "muted wb-preview", id: "wbRelabelPrev" });
    const kinds = () => dRelabelKinds.filter(k => V[k]);
    const upd = () => {
      const mp = dRelabelMap(S.model, kinds());
      const ex = Object.entries(mp).slice(0, 6).map(([k, v]) => `${dEsc(k.split(":")[1])} → <b>${dEsc(v)}</b>`).join(" · ");
      pv.innerHTML = `${Object.keys(mp).length} object${Object.keys(mp).length === 1 ? "" : "s"} will be renamed${ex ? ": " + ex + (Object.keys(mp).length > 6 ? " …" : "") : ""}.`;
    };
    const LB = { column: "Columns (C)", beam: "Beams (B)", brace: "Braces (D)", wall: "Walls (W)", slab: "Floors (F)", link: "Links (L)" };
    const body = [dGroup("Relabel", dRelabelKinds.map(k => F.check(V, k, { label: LB[k], onChange: upd, id: "wbRel_" + k })), { cols: 2 }), pv,
      dNote("Labels follow ETABS: one number per plan position, ordered by Y then X, so a column stack keeps its label on every story; the unique name is <code>label-story</code>. Loads, groups, tendons and hinge assignments follow.")];
    upd();
    return dDialog({ id: "wbRelabelDlg", title: "Auto Relabel All", iconId: "menu-edit", size: "md", form: F, body,
      onOk: async () => {
        if (!kinds().length) return "Choose at least one object type.";
        try { const sm = await runOp("relabel", { kinds: kinds() }, "Auto relabel"); toast("Auto Relabel", `${sm.count} object${sm.count === 1 ? "" : "s"} renamed`); } catch (e) { return e.message; }
      } });
  }

  if (sky.menu) {
    sky.menu.add("Draw/Quick Draw", { label: "Columns in Region", act: "draw-wb-qcol", hint: "at grid intersections", fn: () => startQuick("qcol") });
    sky.menu.add("Draw/Quick Draw", { label: "Beams in Region", act: "draw-wb-qbeam", hint: "on grid lines", fn: () => startQuick("qbeam") });
    sky.menu.add("Draw/Quick Draw", { label: "Floors in Region", act: "draw-wb-qfloor", hint: "in grid bays", fn: () => startQuick("qfloor") });
    sky.menu.add("Draw/Frames", { label: "Frames in 3D View", act: "draw-wb-3d", hint: "click two joints", fn: start3d });
    sky.menu.add("Draw/Floors & Walls", { sep: true });
    sky.menu.add("Draw/Floors & Walls", { label: "Ramp…", act: "draw-wb-ramp", hint: "sloped floor between stories", fn: openRamp });
    sky.menu.add("Draw/Floors & Walls", { label: "Wall Stack…", act: "draw-wb-wallstack", hint: "C · L · I · T · box", fn: openWallStack });
    sky.menu.add("Draw/Dimensions & References", { label: "Dimension Line", act: "draw-wb-dim", hint: "start · end · offset", fn: startDim });
    sky.menu.add("Draw/Dimensions & References", { label: "Reference Point", act: "draw-wb-refpt", hint: "click in plan", fn: startRefPoint });
    sky.menu.add("Draw/Dimensions & References", { label: "Dimensions & References…", act: "draw-wb-aids", hint: "lines · points · planes", fn: () => openAids() });
    sky.menu.add("Edit", { label: "Auto Relabel All…", act: "edit-wb-relabel", hint: "ETABS story labels", fn: openRelabel, before: "edit-dbedit" });
  }
  sky.wb.draw = { startQuick, quickDraw, gridInBox: b => gridInBox(S.model, b), startDim, startRefPoint, openAids, openRamp, openWallStack, openRelabel,
    TOOLS: { ...Q, DIM, REFPT }, state: () => ({ quick: qd.p, dim: dim.p.slice() }) };
  sky.wb.draw3d = { attach, detach, start: start3d, stop: stop3d, active: () => D3.on, pick: (v, x, y) => pick(v || sky.viewer, x, y),
    candidates: () => (D3.cands || candidates()), addFrame: addFrame3d, state: () => ({ on: D3.on, a: D3.a }) };
}
