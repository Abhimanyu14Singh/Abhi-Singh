/* SkyFrame Wave C — ?mock=1 support.

   wcMockValidate(model)   POST /api/model checks of display_units_custom /
                           display_decimals (core/units_custom.validate)
   wcE2kGridLines(m, L)    GRIDSYSTEM / GRID records (orthogonal + ETABS
                           GENERAL lines) for the mock .e2k export
   wcE2kReadGrids(lines, Lf)  grid_systems from foreign mock .e2k text
   Auto-save uses localStorage in mock mode (js/wc_autosave.js); shells with
   openings run through js/wc_shellholes.js. */

import { unitCatalog as mwCatalog, MAX_DECIMALS as MW_MAXDEC } from "./wc_unitcat.js";

const KINDS = ["length", "dim", "small", "disp", "area", "inertia", "secmod", "force", "moment", "line_force", "line_moment", "pressure", "stress",
  "modulus", "unit_weight", "subgrade", "line_spring", "stiffness", "rot_stiffness", "damping_coeff", "mass", "mass_density", "mass_moi",
  "mass_per_length", "mass_per_area", "accel", "velocity", "temp", "temp_delta", "thermal_coeff", "temp_gradient", "rotation", "frequency",
  "period", "strain", "none"];

export function wcMockValidate(m) {
  if (!m || typeof m !== "object") return null;
  const cat = mwCatalog();
  const u = m.display_units_custom, d = m.display_decimals;
  if (u != null && (typeof u !== "object" || Array.isArray(u))) return "display_units_custom / display_decimals must be objects";
  if (d != null && (typeof d !== "object" || Array.isArray(d))) return "display_units_custom / display_decimals must be objects";
  for (const [k, lab] of Object.entries(u || {})) {
    if (!cat[k]) return `display_units_custom: no unit choice for '${k}'`;
    if (!cat[k].some(r => r.label === lab)) return `display_units_custom: '${lab}' is not a ${k} unit`;
  }
  for (const [k, n] of Object.entries(d || {})) {
    if (!KINDS.includes(k)) return `display_decimals: unknown quantity '${k}'`;
    if (!Number.isInteger(n) || n < 0 || n > MW_MAXDEC) return `display_decimals: ${k} must be an integer 0..${MW_MAXDEC}, got ${n}`;
  }
  return null;
}

const q = s => `"${String(s).replace(/"/g, "'")}"`;
const num = v => String(+(+v).toPrecision(15));

/** e2k GRIDS section lines (L = SI length → file length string). */
export function wcE2kGridLines(m, L) {
  const sys = (Array.isArray(m.grid_systems) && m.grid_systems.length) ? m.grid_systems : (m.grid ? [m.grid] : []);
  if (!sys.length) return [];
  const out = ["", "$ GRIDS"];
  for (const g of sys) {
    const typ = g.kind === "general" ? "GENERAL" : g.kind === "radial" ? "CYLINDRICAL" : "CARTESIAN";
    let extra = "";
    if ((g.origin || [0, 0])[0] || (g.origin || [0, 0])[1]) extra += `  UX ${L(g.origin[0])}  UY ${L(g.origin[1])}`;
    if (g.rotation) extra += `  ROTATION ${num(g.rotation)}`;
    out.push(`  GRIDSYSTEM ${q(g.name)}  TYPE ${q(typ)}  BUBBLESIZE 1.25${extra}`);
    if (g.kind === "general") for (const ln of g.lines || [])
      out.push(`  GRID ${q(g.name)}  LABEL ${q(ln.label)}  DIR "GENERAL"  X1 ${L(ln.p1[0])}  Y1 ${L(ln.p1[1])}  X2 ${L(ln.p2[0])}  Y2 ${L(ln.p2[1])}  VISIBLE "Yes"  BUBBLELOC "End"`);
    else if (g.kind !== "radial") {
      (g.x_lines || []).forEach((x, i) => out.push(`  GRID ${q(g.name)}  LABEL ${q((g.x_labels || [])[i] || i + 1)}  DIR "X"  COORD ${L(x)}  VISIBLE "Yes"  BUBBLELOC "End"`));
      (g.y_lines || []).forEach((y, i) => out.push(`  GRID ${q(g.name)}  LABEL ${q((g.y_labels || [])[i] || i + 1)}  DIR "Y"  COORD ${L(y)}  VISIBLE "Yes"  BUBBLELOC "Start"`));
    }
  }
  return out;
}

/** grid_systems from tokenised GRIDSYSTEM / GRID records ([tokens...]). */
export function wcE2kReadGrids(records, Lf) {
  const grids = new Map();
  const kv = (t, i) => { const o = {}; for (; i + 1 < t.length; i += 2) o[t[i].toUpperCase()] = t[i + 1]; return o; };
  const base = name => ({ name, kind: "orthogonal", origin: [0, 0], rotation: 0, x_lines: [], y_lines: [], x_labels: [], y_labels: [], radii: [], theta_deg: [] });
  for (const t of records) {
    const head = t[0].toUpperCase();
    if (head === "GRIDSYSTEM") {
      const o = kv(t, 2), g = base(t[1]);
      const typ = (o.TYPE || "").toUpperCase();
      g.kind = typ.startsWith("GEN") ? "general" : typ.startsWith("CYL") ? "radial" : "orthogonal";
      g.origin = [(+o.UX || 0) * Lf, (+o.UY || 0) * Lf];
      g.rotation = +o.ROTATION || 0;
      if (g.kind === "general") g.lines = [];
      grids.set(t[1], g);
    } else if (head === "GRID") {
      const o = kv(t, 2);
      if (!grids.has(t[1])) grids.set(t[1], base(t[1]));
      const g = grids.get(t[1]), dir = (o.DIR || "X").toUpperCase();
      if (dir.startsWith("GEN")) { g.kind = "general"; (g.lines = g.lines || []).push({ label: o.LABEL || `L${g.lines.length + 1}`, p1: [+o.X1 * Lf, +o.Y1 * Lf], p2: [+o.X2 * Lf, +o.Y2 * Lf] }); }
      else if (dir === "X") { g.x_lines.push(+o.COORD * Lf); g.x_labels.push(o.LABEL); }
      else if (dir === "Y") { g.y_lines.push(+o.COORD * Lf); g.y_labels.push(o.LABEL); }
    }
  }
  return [...grids.values()];
}
