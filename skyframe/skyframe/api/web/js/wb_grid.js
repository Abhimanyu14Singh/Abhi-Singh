/* SkyFrame Wave B — general (arbitrary line) grid systems (ETABS grid type
   "General").

   Define › Stories, Grids & Towers › General Grid…: list of the general
   systems (Add / Modify / Delete); the data dialog edits the name, origin,
   rotation and the line table (label, start / end point in the system's
   LOCAL frame, display units). Draw › Dimensions & References › General
   Grid Line: two clicks in the plan add a line to the selected general
   system (global → local by the inverse origin / rotation transform).
   Display (plan + 3D), snapping (line end points, crossings and 0.5 m
   stations along every line) come from js/wb_shellgeom.js through the
   modeledit.js / viewer3d.js hooks. Model key: grid_systems[i].lines
   (CONTRACT "Model templates, general grids, shell editing and drawing
   aids"). */

import { el as gEl, dkForm as gForm, dkGroup as gGroup, dkList as gList, dkDialog as gDialog, dkDraft as gDraft,
  dkCommit as gCommit, dkNote as gNote, uniqueName as gUnique, clone as gClone } from "./dlgkit.js";
import gU from "./units.js";
import { generalIntersections as gIntersections, gridToGlobal as gToGlobal } from "./wb_shellgeom.js";

const NS = "http://www.w3.org/2000/svg";
const TOOL = "wbgridline";

export function initWbGrid(sky) {
  const S = sky.store, pe = sky.planEditor;
  const toast = (t, m, k = "info", ms = 4000) => sky.toast && sky.toast(t, m, k, ms);
  const generals = m => (m.grid_systems || []).filter(g => g && g.kind === "general");

  function newSystem(D) {
    if (!Array.isArray(D.grid_systems) || !D.grid_systems.length) D.grid_systems = D.grid ? [D.grid] : [];
    const name = gUnique(new Set(D.grid_systems.map(g => g.name)), "GL1".replace(/1$/, "") + (generals(D).length + 1));
    const sys = { name, kind: "general", origin: [0, 0], rotation: 0, x_lines: [], y_lines: [], x_labels: [], y_labels: [], radii: [], theta_deg: [],
      lines: [{ label: "L1", p1: [0, 0], p2: [6, 0] }] };
    D.grid_systems.push(sys);
    return sys;
  }

  /** General Grid System Data (one system, edits a copy written back on OK). */
  function openData(D, sys, onDone) {
    const c = gClone(sys);
    const F = gForm();
    const taken = new Set((D.grid_systems || []).filter(g => g !== sys).map(g => g.name));
    const rows = c.lines;
    const L = "length";
    const preview = gEl("svg", { class: "wb-grid-prev", id: "wbGridPrev", viewBox: "0 0 320 200" });
    const drawPrev = () => {
      preview.textContent = "";
      const T = gToGlobal(c), segs = rows.map(l => [T(+l.p1[0], +l.p1[1]), T(+l.p2[0], +l.p2[1])]);
      const pts = segs.flat();
      if (!pts.length || pts.some(p => !isFinite(p[0]) || !isFinite(p[1]))) return;
      const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
      const sc = Math.min(280 / Math.max(x1 - x0, 1e-6), 160 / Math.max(y1 - y0, 1e-6));
      const X = x => 20 + (x - x0) * sc, Y = y => 180 - (y - y0) * sc;
      for (const [a, b] of segs) { const e = document.createElementNS(NS, "line"); Object.entries({ x1: X(a[0]), y1: Y(a[1]), x2: X(b[0]), y2: Y(b[1]), stroke: "#e0a020", "stroke-width": 1.4 }).forEach(([k, v]) => e.setAttribute(k, v)); preview.appendChild(e); }
      try {
        for (const it of gIntersections(c)) { const e = document.createElementNS(NS, "circle"); Object.entries({ cx: X(it.point[0]), cy: Y(it.point[1]), r: 2.2, fill: "#35b5e5" }).forEach(([k, v]) => e.setAttribute(k, v)); preview.appendChild(e); }
      } catch { /* bad rows: skip */ }
    };
    const tbl = F.table(rows, [
      { key: "label", label: "Label", type: "text", width: "70px" },
      { key: "x1", label: "X start", type: "num", kind: L },
      { key: "y1", label: "Y start", type: "num", kind: L },
      { key: "x2", label: "X end", type: "num", kind: L },
      { key: "y2", label: "Y end", type: "num", kind: L },
    ].map(col => col), {
      id: "wbGridLines", minRows: 1, addLabel: "+ Add line",
      newRow: r => { const last = r[r.length - 1] || { p1: [0, 0], p2: [6, 0] }; const o = { label: gUnique(new Set(r.map(x => x.label)), "L" + (r.length + 1)), p1: [last.p1[0], +last.p1[1] + 3], p2: [last.p2[0], +last.p2[1] + 3] }; wrap(o); return o; },
      validateRow: (r, i) => (Math.hypot(r.p2[0] - r.p1[0], r.p2[1] - r.p1[1]) < 1e-9 ? `Line ${i + 1}: start and end coincide.` : ""),
      validate: r => { const s = new Set(); for (const x of r) { if (!x.label) return "Every line needs a label."; if (s.has(x.label)) return `“${x.label}” is used twice.`; s.add(x.label); } return ""; },
      onChange: drawPrev,
    });
    // table columns bind flat keys: expose p1 / p2 components as accessors
    function wrap(o) {
      for (const [k, arr, i] of [["x1", "p1", 0], ["y1", "p1", 1], ["x2", "p2", 0], ["y2", "p2", 1]])
        Object.defineProperty(o, k, { get: () => o[arr][i], set: v => { o[arr][i] = v; }, enumerable: false, configurable: true });
      return o;
    }
    rows.forEach(wrap);
    F.refresh();
    const body = [
      gGroup("Grid System", [
        F.text(c, "name", { label: "System name", id: "wbGridName", validate: v => (taken.has(v) ? `“${v}” is already used — pick another name.` : "") }),
        F.num(c.origin, 0, { label: "Origin X", kind: L, id: "wbGridOx", onChange: drawPrev }),
        F.num(c.origin, 1, { label: "Origin Y", kind: L, id: "wbGridOy", onChange: drawPrev }),
        F.num(c, "rotation", { label: "Rotation (CCW)", unit: "deg", id: "wbGridRot", onChange: drawPrev }),
      ], { cols: 2 }),
      gGroup("Grid Lines (local coordinates)", [tbl, preview], { note: "Each line is drawn between its two end points; crossings and end points are snap points." }),
    ];
    drawPrev();
    return gDialog({ id: "wbGridData", title: "General Grid System Data", iconId: "exleaf-grids", size: "lg", form: F, body,
      onUnits: () => F.refresh(),
      onOk: () => {
        const out = { ...sys, name: c.name, origin: [+c.origin[0], +c.origin[1]], rotation: +c.rotation || 0,
          lines: rows.map(r => ({ label: r.label, p1: [+r.p1[0], +r.p1[1]], p2: [+r.p2[0], +r.p2[1]] })) };
        Object.keys(sys).forEach(k => delete sys[k]);
        Object.assign(sys, out);
        onDone && onDone();
      } });
  }

  /** Define › General Grid… (list). */
  function open() {
    const D = gDraft(sky);
    const list = gList({
      id: "wbGridList", empty: "No general grid systems yet — <b>Add New</b> creates one with a first line.",
      items: () => generals(D).map(g => ({ key: g.name, g })),
      columns: [{ label: "Name", get: it => it.g.name }, { label: "Lines", get: it => (it.g.lines || []).length },
        { label: "Origin", get: it => `${gU.fmt("length", it.g.origin[0], 2)}, ${gU.fmt("length", it.g.origin[1], 2)} ${gU.label("length")}` },
        { label: "Rotation", get: it => `${+it.g.rotation || 0}°` }],
      actions: [
        { label: "Add New System…", id: "wbGridAdd", fn: () => { const s = newSystem(D); openData(D, s, () => list.render()); list.select(s.name); } },
        { label: "Modify/Show System…", id: "wbGridMod", needsSel: true, fn: k => { const s = generals(D).find(g => g.name === k); if (s) openData(D, s, () => { list.render(); list.select(s.name); }); } },
        { label: "Delete System", id: "wbGridDel", needsSel: true, fn: k => { D.grid_systems = D.grid_systems.filter(g => !(g.kind === "general" && g.name === k)); list.render(); } },
      ],
      onOpen: k => { const s = generals(D).find(g => g.name === k); if (s) openData(D, s, () => list.render()); },
    });
    return gDialog({ id: "wbGridDlg", title: "Define General Grid Systems", iconId: "exleaf-grids", size: "md", body: [list,
      gNote("Grids are drawing aids only. Use <b>Draw › Dimensions &amp; References › General Grid Line</b> to add lines by clicking in the plan.")],
    intro: "ETABS grid type “General”: grid lines by two end points.",
    onOk: () => { gCommit(sky, D); } });
  }

  /* ---- plan tool: two clicks add a line to the active general system */
  const st = { p: null, target: null };
  const startTool = () => {
    if (S.mode !== "model") sky.setMode("model");
    if (S.view === "elev") sky.setView("plan");
    st.p = null;
    sky.setTool(TOOL);
    toast("General grid line", "Click the start and end points in the plan (snaps to grids / objects). V or Esc to finish.");
  };
  if (pe) {
    const prevTool = pe.w8ToolHook;
    pe.w8ToolHook = (tool, pt, w, ed) => {
      if (tool !== TOOL) return prevTool && prevTool(tool, pt, w, ed);
      if (!st.p) { st.p = [pt.x, pt.y]; return; }
      const a = st.p, b = [pt.x, pt.y];
      st.p = null;
      if (Math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9) return;
      addLineGlobal(a, b);
    };
    const prevOv = pe.w8Overlay;
    pe.w8Overlay = (g, ed) => {
      if (prevOv) prevOv(g, ed);
      if (ed.tool !== TOOL || !st.p || !ed._mouseWorld) return;
      const t = ed.hoverSnap || ed._mouseWorld;
      const [x1, y1] = ed.toScreen(st.p[0], st.p[1]), [x2, y2] = ed.toScreen(t.x, t.y);
      const e = document.createElementNS(NS, "line");
      Object.entries({ x1, y1, x2, y2, stroke: "#e0a020", "stroke-width": 1.5, "stroke-dasharray": "8 4", "data-wb": "gridline" }).forEach(([k, v]) => e.setAttribute(k, v));
      g.appendChild(e);
    };
    const os = pe.setTool.bind(pe);
    pe.setTool = t => { st.p = null; os(t); };
  }
  /** Add a GLOBAL plan segment as a line of the first general system
      (created as "GL1" when none exists). One undo step. */
  function addLineGlobal(a, b, sysName) {
    const m = S.model;
    if (!Array.isArray(m.grid_systems) || !m.grid_systems.length) m.grid_systems = m.grid ? [m.grid] : [];
    let sys = generals(m).find(g => !sysName || g.name === sysName);
    if (!sys) {
      sys = { name: gUnique(new Set(m.grid_systems.map(g => g.name)), "GL1"), kind: "general", origin: [0, 0], rotation: 0,
        x_lines: [], y_lines: [], x_labels: [], y_labels: [], radii: [], theta_deg: [], lines: [] };
      m.grid_systems.push(sys);
    }
    const r = (sys.rotation || 0) * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
    const inv = p => { const dx = p[0] - sys.origin[0], dy = p[1] - sys.origin[1]; return [+(dx * c + dy * s).toFixed(9), +(-dx * s + dy * c).toFixed(9)]; };
    const label = gUnique(new Set((sys.lines || []).map(l => l.label)), "L" + ((sys.lines || []).length + 1));
    sys.lines = [...(sys.lines || []), { label, p1: inv(a), p2: inv(b) }];
    sky.markDirty();
    if (sky.planEditor) sky.planEditor.refresh();
    if (sky.viewer) { const v = sky.viewer; const cam = [v.yaw, v.pitch, v.dist, v.target]; v.setModel(S.model); [v.yaw, v.pitch, v.dist, v.target] = cam; v._dirty = true; }
    toast("General grid", `Line ${label} added to ${sys.name}`, "info", 2500);
    return { system: sys.name, label };
  }
  document.addEventListener("keydown", e => { if (e.key === "Escape" && pe && pe.tool === TOOL && st.p) { st.p = null; pe.renderOverlay(); } });

  if (sky.menu) {
    sky.menu.add("Define/Stories, Grids & Towers", { label: "General Grid…", act: "def-wb-general-grid", hint: "lines by end points", fn: open });
    sky.menu.add("Draw/Dimensions & References", { label: "General Grid Line", act: "draw-wb-gridline", hint: "two clicks in plan", fn: startTool });
  }
  sky.wb.grid = { open, openData, addLineGlobal, startTool, TOOL, state: () => ({ ...st }) };
}
