/* SkyFrame Wave C — general (line) grids beyond the plan view.

   * Define › Stories, Grids & Towers › Primary Grid System…: any system —
     a general one included — can be the PRIMARY grid (grid_systems[0] =
     model.grid; ETABS "the first grid system"). Legacy consumers that read
     orthogonal x / y lines from model.grid (plan fit, slab bays, summary,
     CM / CR and take-down plots, mock results) go through
     js/wc_gridgeom.js orthoGrid().
   * Elevations along general grid lines: the elevation selector lists
     every general line ("g:<system>:<label>"); the oblique plane is
     s = (p − origin)·dir with ticks at the crossing grid lines
     (ElevEditor.wcPlane, three plane-generic lines in js/elev.js).
   * Draw › Dimensions & References › General Grid Line works in the
     ELEVATION view (two clicks on the elevation plane: the line follows the
     plane's plan trace) and in the 3D view (two snapped 3D points — joints,
     grid points at every level, object snaps — the line joins their plan
     projections); the plan tool is unchanged (js/wb_grid.js).
   E2K: general systems are written / read as ETABS GENERAL grid lines
   (skyframe/io/e2k_ggrid.py; mock js/mock_wc.js).
   CONTRACT "Wave C interface …" § General grids. */

import { dkForm as gForm, dkGroup as gGroup, dkDialog as gDialog, dkDraft as gDraft, dkCommit as gCommit,
  dkNote as gNote } from "./dlgkit.js";
import GG from "./wc_gridgeom.js";

const TOOL = "wbgridline";                       // js/wb_grid.js plan tool id (shared)

export function initWcGrid(sky) {
  const S = sky.store, ee = sky.elevEditor;
  const toast = (t, m, k = "info", ms = 4000) => sky.toast && sky.toast(t, m, k, ms);
  const systems = m => (Array.isArray(m.grid_systems) && m.grid_systems.length ? m.grid_systems : (m.grid ? [m.grid] : []));
  const isOrtho = g => !!g && g.kind !== "general" && g.kind !== "radial" && (g.x_lines || []).length > 0 && (g.y_lines || []).length > 0;
  const view3d = () => S.mode === "analyze" && S.tab === "view3d";

  /* ---------------- elevation planes along general lines */
  if (ee) {
    ee.wcPlane = (sel, m) => {
      if (sel.axis === "g") return GG.linePlane(m, sel);
      if ((sel.axis === "x" || sel.axis === "y") && !isOrtho(m.grid)) return GG.orthoPlane(m, sel);
      return undefined;                          // legacy path (primary orthogonal grid)
    };
  }
  /** rebuildElevSelect hook (js/app.js): one optgroup per general system. */
  function elevOptions(sel) {
    const m = S.model;
    if (!m) return;
    for (const sys of systems(m)) {
      if (!sys || sys.kind !== "general" || !(sys.lines || []).length) continue;
      const grp = document.createElement("optgroup");
      grp.label = `General grid ${sys.name}`;
      for (const ln of sys.lines) {
        const o = document.createElement("option");
        o.value = GG.elevValue(sys.name, ln.label);
        o.textContent = `${ln.label}  ·  ${sys.name} (general line)`;
        grp.appendChild(o);
      }
      sel.appendChild(grp);
    }
  }
  sky.wc.elevOptions = elevOptions;
  let lastSig = "";
  const sig = () => JSON.stringify(systems(S.model || {}).map(g => [g.name, g.kind, g.lines || null]));
  const syncElev = () => { const s = sig(); if (s !== lastSig) { lastSig = s; if (sky.rebuildElevSelect) sky.rebuildElevSelect(); } };
  document.addEventListener("sky:model-changed", syncElev);
  document.addEventListener("sky:model-replaced", syncElev);
  syncElev();
  function showElevation(system, label) {
    const v = GG.elevValue(system, label);
    if (sky.rebuildElevSelect) sky.rebuildElevSelect();
    sky.setMode("model");
    sky.setView("elev");
    if (sky.setElevLine) sky.setElevLine(v);
    return !!(ee && ee.plane());
  }

  /* ---------------- Primary Grid System… */
  function openPrimary() {
    const D = gDraft(sky);
    if (!Array.isArray(D.grid_systems) || !D.grid_systems.length) D.grid_systems = D.grid ? [D.grid] : [];
    const list = D.grid_systems;
    if (!list.length) { toast("Primary Grid System", "The model has no grid system.", "warn"); return null; }
    const V = { primary: list[0].name };
    const F = gForm();
    const kindTxt = g => g.kind === "general" ? `general · ${(g.lines || []).length} lines` : g.kind === "radial" ? `radial · ${(g.radii || []).length} circles`
      : `orthogonal · ${(g.x_lines || []).length} × ${(g.y_lines || []).length}`;
    const body = [gGroup("Grid Systems", [F.radio(V, "primary", list.map(g => [g.name, g.name, kindTxt(g)]), { idPrefix: "wcPrim_" })]),
      gNote("The primary system is the model's first grid system (ETABS): it names the elevations and is written first to E2K. A general system can be primary; plan fitting and slab bays then use the first orthogonal system (or the bounding box of the grid lines).")];
    return gDialog({ id: "wcPrimaryDlg", title: "Primary Grid System", iconId: "exleaf-grids", size: "sm", form: F, body,
      onOk: () => {
        const i = list.findIndex(g => g.name === V.primary);
        if (i <= 0) return;                      // unchanged → byte-identical
        list.unshift(list.splice(i, 1)[0]);
        D.grid = list[0];
        gCommit(sky, D);
        if (sky.rebuildElevSelect) sky.rebuildElevSelect();
        if (sky.planEditor) sky.planEditor.fit();
        toast("Primary grid", `${V.primary} is now the primary grid system.`, "info", 2500);
      } });
  }

  /* ---------------- General Grid Line in elevation / 3D */
  const EL = { p: null };
  const addLine = (a, b) => {
    if (Math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-6) { toast("General grid line", "The two points have the same plan position — pick points apart along the plane.", "warn"); return null; }
    if (!sky.wb || !sky.wb.grid) return null;
    return sky.wb.grid.addLineGlobal([+a[0].toFixed(9), +a[1].toFixed(9)], [+b[0].toFixed(9), +b[1].toFixed(9)]);
  };
  if (ee) {
    const prevTool = ee.wcToolHook;
    ee.wcToolHook = (tool, w, ed) => {
      if (tool !== TOOL) return prevTool ? prevTool(tool, w, ed) : false;
      const pt = ed.snap(w);
      if (!EL.p) { EL.p = pt; ed.pending = pt; return true; }
      const a = ed.world3(EL.p.s, EL.p.z), b = ed.world3(pt.s, pt.z);
      EL.p = null; ed.pending = null;
      addLine(a, b);
      return true;
    };
    const os = ee.setTool.bind(ee);
    ee.setTool = t => { EL.p = null; os(t); };
  }
  function start3d() {
    if (!sky.wc.pick3d) return false;
    toast("General grid line (3D)", "Click two points (joints, grid points at any level, object snaps): the line joins their plan positions. Esc to finish.", "info", 6000);
    return sky.wc.pick3d.start({ label: "gridline", onPick: (p, prev) => { if (prev) addLine(prev, p); } });
  }
  function startTool() {
    if (view3d()) return start3d();
    if (S.mode === "model" && S.view === "elev" && ee && ee.plane()) {
      EL.p = null;
      sky.setTool(TOOL);
      toast("General grid line (elevation)", "Click two points in the elevation; the line follows the plane between them. V or Esc to finish.");
      return true;
    }
    return sky.wb && sky.wb.grid ? sky.wb.grid.startTool() : false;
  }
  const it = sky.menu && sky.menu.item && sky.menu.item("draw-wb-gridline");
  if (it) { it.wcLegacyFn = it.fn; it.fn = startTool; it.hint = "two clicks · plan, elevation or 3D"; }
  if (sky.menu) sky.menu.add("Define/Stories, Grids & Towers", { label: "Primary Grid System…", act: "def-wc-primary-grid", hint: "any system, general included", fn: openPrimary });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && EL.p && ee) { EL.p = null; ee.pending = null; ee.renderOverlay(); } });

  sky.wc.grid = { openPrimary, startTool, start3d, showElevation, elevOptions, geom: GG, state: () => ({ elev: EL.p && { ...EL.p } }) };
}
