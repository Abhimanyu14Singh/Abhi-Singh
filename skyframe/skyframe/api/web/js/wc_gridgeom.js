/* SkyFrame Wave C — pure grid geometry for general (line) grids as the
   PRIMARY grid and as elevation planes (no DOM).

   orthoGrid(m)        the orthogonal grid legacy consumers read through
                       `model.grid` (plan fit, slab bays, summary footprint,
                       CM/CR and take-down plots, mock results): the primary
                       grid when it is orthogonal, else the first orthogonal
                       system, else a 2 × 2 bounding grid of every grid line
   allLines(m)         every straight grid line in GLOBAL plan coordinates
                       [{system, kind, label, a:[x,y], b:[x,y]}]
   elevValue / parseElev   "g:<system>:<label>" elevation selector values
   linePlane(m, sel)   oblique elevation plane along a general grid line:
                       {axis:"g", label, origin, dir, len, coord:0, ticks,
                        tickLabels} — s = (p − origin)·dir, ticks at every
                       crossing grid line (plus the two line ends).
   CONTRACT "Wave C interface …" § General grids. */

import { gridSystems as ggSystems, gridTransform as ggT } from "./modeledit.js";
import { generalLinesGlobal as ggGenLines } from "./wb_shellgeom.js";

const isOrtho = g => !!g && g.kind !== "general" && g.kind !== "radial" &&
  Array.isArray(g.x_lines) && g.x_lines.length > 0 && Array.isArray(g.y_lines) && g.y_lines.length > 0;

export function orthoGrid(m) {
  const g = m && m.grid;
  if (!m || !g) return g;
  if (isOrtho(g)) return g;
  const o = ggSystems(m).find(isOrtho);
  if (o) return o;
  const xs = [], ys = [];
  for (const ln of allLines(m)) for (const p of [ln.a, ln.b]) { xs.push(p[0]); ys.push(p[1]); }
  if (!xs.length) return g;
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  return { name: g.name, kind: "orthogonal", origin: [0, 0], rotation: 0,
    x_lines: x1 - x0 > 1e-9 ? [x0, x1] : [x0], y_lines: y1 - y0 > 1e-9 ? [y0, y1] : [y0],
    x_labels: x1 - x0 > 1e-9 ? ["A", "B"] : ["A"], y_labels: y1 - y0 > 1e-9 ? ["1", "2"] : ["1"], synthetic: true };
}

export function allLines(m) {
  const out = [];
  for (const sys of ggSystems(m)) {
    if (!sys) continue;
    if (sys.kind === "general") {
      for (const ln of ggGenLines(sys)) out.push({ system: sys.name, kind: "general", label: ln.label, a: ln.points[0], b: ln.points[1] });
      continue;
    }
    if (sys.kind === "radial") continue;
    const T = ggT(sys), xs = sys.x_lines || [], ys = sys.y_lines || [];
    if (!xs.length || !ys.length) continue;
    const xa = Math.min(...xs), xb = Math.max(...xs), ya = Math.min(...ys), yb = Math.max(...ys);
    xs.forEach((x, i) => out.push({ system: sys.name, kind: "x", label: (sys.x_labels || [])[i] || String(i + 1), a: T(x, ya), b: T(x, yb) }));
    ys.forEach((y, i) => out.push({ system: sys.name, kind: "y", label: (sys.y_labels || [])[i] || String(i + 1), a: T(xa, y), b: T(xb, y) }));
  }
  return out;
}

export const elevValue = (system, label) => `g:${encodeURIComponent(system)}:${encodeURIComponent(label)}`;
export function parseElev(v) {
  if (typeof v !== "string" || !v.startsWith("g:")) return null;
  const [, s, l] = v.split(":");
  try { return { axis: "g", system: decodeURIComponent(s || ""), label: decodeURIComponent(l || "") }; } catch { return null; }
}

/** Oblique elevation plane along general line {system, label} (or null). */
export function linePlane(m, sel) {
  const lines = allLines(m);
  const ln = lines.find(l => l.kind === "general" && l.system === sel.system && l.label === sel.label);
  if (!ln) return null;
  const dx = ln.b[0] - ln.a[0], dy = ln.b[1] - ln.a[1], len = Math.hypot(dx, dy);
  if (!(len > 1e-9)) return null;
  const dir = [dx / len, dy / len];
  const ticks = [{ s: 0, l: `${ln.label}.1` }, { s: len, l: `${ln.label}.2` }];
  for (const o of lines) {
    if (o === ln) continue;
    const ex = o.b[0] - o.a[0], ey = o.b[1] - o.a[1];
    const den = dx * ey - dy * ex;
    if (Math.abs(den) < 1e-12 * len * Math.hypot(ex, ey)) continue;
    const qx = o.a[0] - ln.a[0], qy = o.a[1] - ln.a[1];
    const t = (qx * ey - qy * ex) / den, u = (qx * dy - qy * dx) / den;
    if (t < -1e-9 || t > 1 + 1e-9 || u < -1e-9 || u > 1 + 1e-9) continue;
    ticks.push({ s: Math.max(0, Math.min(len, t * len)), l: o.label });
  }
  ticks.sort((p, q) => p.s - q.s);
  const uniq = [];
  for (const t of ticks) { if (!uniq.length || Math.abs(uniq[uniq.length - 1].s - t.s) > 1e-6) uniq.push(t); else if (/\.\d$/.test(uniq[uniq.length - 1].l)) uniq[uniq.length - 1] = t; }
  return { axis: "g", system: sel.system, label: `${sel.system} · ${ln.label}`, lineLabel: ln.label, coord: 0,
    origin: [ln.a[0], ln.a[1]], dir, len, ticks: uniq.map(t => t.s), tickLabels: uniq.map(t => t.l) };
}

/** Legacy x / y elevation plane resolved against orthoGrid(m) (used only
    when the primary grid is not orthogonal). */
export function orthoPlane(m, sel) {
  const g = orthoGrid(m);
  if (!g || !Array.isArray(g.x_lines)) return null;
  const lines = sel.axis === "x" ? g.x_lines : g.y_lines;
  const labels = sel.axis === "x" ? g.x_labels : g.y_labels;
  if (!(sel.index >= 0 && sel.index < lines.length)) return null;
  return { axis: sel.axis, coord: lines[sel.index], label: (labels && labels[sel.index]) || String(sel.index + 1),
    ticks: sel.axis === "x" ? g.y_lines : g.x_lines, tickLabels: sel.axis === "x" ? g.y_labels : g.x_labels };
}

/** Plane-generic helpers (mirror ElevEditor.world3 / sOf / inPlane). */
export const planeWorld3 = (pl, s, z) => pl.dir ? [pl.origin[0] + s * pl.dir[0], pl.origin[1] + s * pl.dir[1], z]
  : pl.axis === "x" ? [pl.coord, s, z] : [s, pl.coord, z];
export const planeS = (pl, p) => pl.dir ? (p[0] - pl.origin[0]) * pl.dir[0] + (p[1] - pl.origin[1]) * pl.dir[1]
  : pl.axis === "x" ? p[1] : p[0];
export const planeOff = (pl, p) => pl.dir ? -(p[0] - pl.origin[0]) * pl.dir[1] + (p[1] - pl.origin[1]) * pl.dir[0]
  : (pl.axis === "x" ? p[0] : p[1]) - pl.coord;

export default { orthoGrid, allLines, elevValue, parseElev, linePlane, orthoPlane, planeWorld3, planeS, planeOff };
