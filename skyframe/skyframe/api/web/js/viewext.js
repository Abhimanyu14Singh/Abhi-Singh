/* SkyFrame — 3D view display options + ETABS-style "Extrude" view (VX).

     View → Set Display Options…   object visibility (beams, columns, braces,
                                   walls, slabs, links, springs, supports,
                                   joints, loads, local axes, labels, grid,
                                   section names), colour-by mode, extrude
                                   frames / shells, shell transparency, story
                                   range, group, selection-only, cut plane
     View → Extrude / Show Selection Only / Section Cut Plane   quick toggles
     3D toolbar chips              Extrude · Sel. only · Cut  (+ cut slider)

   The options are VIEW state, not model state: nothing is written to the
   model (a save round-trips byte-identically) and the options persist per
   browser session (sessionStorage, guarded).

   Hooks (all additive, js/viewer3d.js):
     viewer.vxFrame(P, viewer)          → per-frame display state or null
                                          (null = every option at its default
                                          → the legacy renderer runs unchanged)
     viewer.vxDeformed(P, disp, f, v)   → extruded deformed / mode shape
     viewer.vxMemberVisible(resMember)  → filter for the deformed line shape

   Extrusion
     * frames: the real section profile — rectangle b×h; I/W shapes (d, bf,
       tf, tw when present, else solved from A and I33 with b = bf, h = d);
       circle / pipe; rectangular tube; Section Designer outline (+ holes on
       the end caps); generic box from A, I33, I22 as the last fallback.
       Oriented by the engine local axes (polygeom.memberAxes, incl. the
       member angle), placed by the cardinal point (insertion point) and the
       joint offsets (global or local, interpolated i → j). Nonprismatic
       sections taper station by station (npsect.npLayout / npPropsAt).
     * shells: plates of the shell-section thickness (slabs hang below the
       slab plane, walls are centred on it), with polygon or rectangular
       openings cut through, tiled for depth sorting.
     * rendering: painter's algorithm shared with the legacy items (one sort),
       back-face culling, Lambert shading on face normals (quantised colour
       tables, no per-face string building), level of detail by projected
       section size (full profile → bounding box → screen-space line), all
       world geometry cached per model / filter signature.
     * results: deformed shapes and mode / buckling shapes are extruded along
       the cubic-Hermite deformed centreline of the viewer (4 sub-segments,
       2 while animating a model with > 400 members, box profile while
       animating). The section keeps the undeformed member orientation (no
       twist / slope rotation of the cross-section — documented
       approximation). The undeformed model is drawn as ghosted lines, as
       before. Shell contour quads replace the extruded shell plates of
       shell-behaviour regions, exactly like the legacy translucent fills.

   Every overlay hook (extraOverlay, g2Overlay, grpOverlay, npOverlay,
   uhOverlay, ptOverlay, pfOverlay) still runs after the solids, on top. */

import { dialog as vxDialog, btn as vxBtn, footBar as vxFootBar, closeDialog as vxCloseDialog } from "./analysisdlg.js";
import VXU from "./units.js";
import { memberAxes as vxMemberAxes, CP_GRID as VX_CP_GRID, regionFrame as vxRegionFrame,
  clipToBox2 as vxClipToBox2, pointInPoly2 as vxPointInPoly2 } from "./polygeom.js";
import { isNpSec as vxIsNpSec, npLayout as vxNpLayout, npPropsAt as vxNpPropsAt, npRaw as vxNpRaw } from "./npsect.js";

/* ================================================================
   Options (view state, persisted per session)
   ================================================================ */
const VX_STORE_KEY = "skyframe.vx.display.v1";
export const VX_SHOW_KEYS = [
  ["beam", "Beams"], ["column", "Columns"], ["brace", "Braces"], ["wall", "Walls"],
  ["slab", "Slabs / floors"], ["link", "Links"], ["spring", "Springs"], ["support", "Supports"],
  ["joint", "Joints"], ["load", "Loads"], ["axes", "Local axes"], ["label", "Object labels"],
  ["grid", "Grid"], ["secname", "Section names"],
];
export const VX_COLOR_BY = [["type", "Object type"], ["section", "Section"], ["material", "Material"]];

export function vxDefaults() {
  return {
    extrude: false, extrudeShells: true, edges: true, colorBy: "type", shellTransp: 0,
    show: { beam: true, column: true, brace: true, wall: true, slab: true, link: true, spring: true,
      support: true, joint: false, load: false, axes: false, label: false, grid: true, secname: false },
    loadPattern: "", storyFrom: "", storyTo: "", group: "", selOnly: false,
    cut: { on: false, axis: "x", coord: null, keep: "below" },
  };
}
const vxClone = o => JSON.parse(JSON.stringify(o));
function vxNormalize(o) {
  const d = vxDefaults();
  if (!o || typeof o !== "object") return d;
  const out = { ...d, ...o, show: { ...d.show, ...(o.show || {}) }, cut: { ...d.cut, ...(o.cut || {}) } };
  out.shellTransp = Math.min(0.9, Math.max(0, +out.shellTransp || 0));
  if (!VX_COLOR_BY.some(([k]) => k === out.colorBy)) out.colorBy = "type";
  if (!["x", "y", "z"].includes(out.cut.axis)) out.cut.axis = "x";
  if (!["below", "above"].includes(out.cut.keep)) out.cut.keep = "below";
  if (out.cut.coord != null && !isFinite(+out.cut.coord)) out.cut.coord = null;
  for (const k of Object.keys(out.show)) out.show[k] = !!out.show[k];
  for (const k of ["extrude", "extrudeShells", "edges", "selOnly"]) out[k] = !!out[k];
  for (const k of ["loadPattern", "storyFrom", "storyTo", "group"]) out[k] = typeof out[k] === "string" ? out[k] : "";
  return out;
}
function vxLoad() {
  try { const s = sessionStorage.getItem(VX_STORE_KEY); return vxNormalize(s ? JSON.parse(s) : null); }
  catch (e) { return vxDefaults(); }
}
function vxSave(o) { try { sessionStorage.setItem(VX_STORE_KEY, JSON.stringify(o)); } catch (e) { /* private mode */ } }
const vxIsDefault = o => JSON.stringify(vxNormalize(o)) === JSON.stringify(vxDefaults());

/* ================================================================
   Colours
   ================================================================ */
const VX_TYPE_COLORS = { column: "#5f8fc9", beam: "#8d9db1", brace: "#d39316", wall: "#7fa6d6", slab: "#a7b1bc" };
const VX_PALETTE = ["#4e9bd6", "#e0884a", "#5bbf7a", "#c9636f", "#9a7fd1", "#d1b24a", "#4fc1c9",
  "#d67fb5", "#8fb94a", "#b98a63", "#6f8fd6", "#c96f4a", "#62b39b", "#b06fc9", "#a0a0a0", "#d6d05c"];
const VX_SHADES = 16;
const vxShadeCache = new Map();
function vxParseColor(c) {
  if (typeof c !== "string") return [150, 160, 175];
  if (c[0] === "#") {
    if (c.length === 4) return [1, 2, 3].map(i => parseInt(c[i] + c[i], 16));
    return [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)];
  }
  const m = c.match(/rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)/);
  return m ? [+m[1], +m[2], +m[3]] : [150, 160, 175];
}
/** css colour → array of VX_SHADES css strings (dark → bright). */
function vxShades(css) {
  let t = vxShadeCache.get(css);
  if (t) return t;
  const rgb = vxParseColor(css);
  t = [];
  for (let q = 0; q < VX_SHADES; q++) {
    const f = 0.40 + 0.72 * q / (VX_SHADES - 1);
    t.push(`rgb(${rgb.map(v => Math.min(255, Math.round(v * f))).join(",")})`);
  }
  vxShadeCache.set(css, t);
  return t;
}
const vxHash = s => { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return Math.abs(h); };

/* ================================================================
   Section profiles (local y = axis 2 = depth h, local z = axis 3 = width b)
   ================================================================ */
const area2 = p => { let a = 0; for (let i = 0, j = p.length - 1; i < p.length; j = i++) a += p[j][0] * p[i][1] - p[i][0] * p[j][1]; return a / 2; };
const ccw = p => (area2(p) < 0 ? p.slice().reverse() : p);
const vxRect = (b, h) => [[-h / 2, -b / 2], [h / 2, -b / 2], [h / 2, b / 2], [-h / 2, b / 2]];
const vxCircle = (D, n) => { const r = D / 2, o = []; for (let k = 0; k < n; k++) { const a = 2 * Math.PI * k / n; o.push([r * Math.cos(a), r * Math.sin(a)]); } return o; };
function vxIShape(d, bf, tf, tw) {
  const a = d / 2, f = bf / 2, w = tw / 2;
  return ccw([[-a, -f], [-a, f], [-a + tf, f], [-a + tf, w], [a - tf, w], [a - tf, f], [a, f], [a, -f],
    [a - tf, -f], [a - tf, -w], [-a + tf, -w], [-a + tf, -f]]);
}
/** I-shape plate thicknesses from A and I33 (b = bf, h = d). */
export function vxSolveI(A, I33, d, bf) {
  if (!(A > 0 && d > 0 && bf > 0) || A >= bf * d) return null;
  const twOf = tf => (A - 2 * bf * tf) / (d - 2 * tf);
  const Iof = tf => { const tw = twOf(tf); return (bf * d ** 3 - (bf - tw) * (d - 2 * tf) ** 3) / 12; };
  const hi = Math.min(d / 2 * 0.98, A / (2 * bf) * 0.999);
  if (I33 > 0 && hi > 0) {
    let lo = hi * 1e-3, up = hi, flo = Iof(lo) - I33, fup = Iof(up) - I33;
    if (flo * fup < 0) {
      for (let it = 0; it < 60; it++) {
        const mid = (lo + up) / 2, fm = Iof(mid) - I33;
        if (fm * flo <= 0) { up = mid; fup = fm; } else { lo = mid; flo = fm; }
      }
      const tf = (lo + up) / 2, tw = twOf(tf);
      if (tw > 0 && tw <= bf && tf > 0) return { tf, tw };
    }
  }
  // fallback: tw = 0.6 tf → −1.2 tf² + (2 bf + 0.6 d) tf − A = 0
  const qa = -1.2, qb = 2 * bf + 0.6 * d, qc = -A, disc = qb * qb - 4 * qa * qc;
  if (disc < 0) return null;
  const tf = (-qb + Math.sqrt(disc)) / (2 * qa);
  return tf > 0 && tf < d / 2 ? { tf, tw: 0.6 * tf } : null;
}
const RX_I = /^(W|HP|S|M|HE[ABM]?|IPE|UB|UC|I)\s*\d/i;
const RX_PIPE = /^(PIPE|CHS|HSS\s*\d+(\.\d+)?\s*[Xx]\s*\d+(\.\d+)?$)/i;
const RX_TUBE = /^(HSS|RHS|SHS|TUBE|BOX)/i;

/** Profile of a prismatic section dict. → {pts, holes, b, h, convex} */
function vxProfileOf(model, name, sec, lod) {
  sec = sec || {};
  const num = k => (isFinite(+sec[k]) && +sec[k] > 0 ? +sec[k] : 0);
  const A = num("A"), I33 = num("I33"), I22 = num("I22");
  let b = num("b"), h = num("h");
  const shape = String(sec.shape || "").toLowerCase();
  const nCirc = lod === "box" ? 8 : 16;
  const box = (bb, hh) => ({ pts: ccw(vxRect(bb, hh)), holes: [], b: bb, h: hh, convex: true });
  // Section Designer: outline polygon (largest solid) + holes on the caps
  const ds = shape === "designer" || (!b && !h) ? ((model.designer_sections || {})[name]) : null;
  if (ds && Array.isArray(ds.polygons) && ds.polygons.length) {
    const solids = ds.polygons.filter(p => !p.hole && Array.isArray(p.vertices) && p.vertices.length >= 3);
    if (solids.length) {
      const outer = solids.reduce((a, p) => (Math.abs(area2(p.vertices)) > Math.abs(area2(a.vertices)) ? p : a));
      let ys = 0, zs = 0, aa = 0;
      const v = outer.vertices;
      for (let i = 0, j = v.length - 1; i < v.length; j = i++) {
        const cr = v[j][0] * v[i][1] - v[i][0] * v[j][1];
        aa += cr; ys += (v[j][0] + v[i][0]) * cr; zs += (v[j][1] + v[i][1]) * cr;
      }
      const cy = aa ? ys / (3 * aa) : 0, cz = aa ? zs / (3 * aa) : 0;
      const shift = p => [+p[0] - cy, +p[1] - cz];
      const pts = ccw(v.map(shift));
      let y0 = Infinity, y1 = -Infinity, z0 = Infinity, z1 = -Infinity;
      for (const [y, z] of pts) { y0 = Math.min(y0, y); y1 = Math.max(y1, y); z0 = Math.min(z0, z); z1 = Math.max(z1, z); }
      if (lod === "box") return { pts: ccw([[y0, z0], [y1, z0], [y1, z1], [y0, z1]]), holes: [], b: z1 - z0, h: y1 - y0, convex: true };
      const holes = ds.polygons.filter(p => p.hole && Array.isArray(p.vertices) && p.vertices.length >= 3).map(p => p.vertices.map(shift));
      return { pts, holes, b: z1 - z0, h: y1 - y0, convex: false };
    }
  }
  if ((!b || !h) && RX_I.test(name) && A > 0 && I33 > 0 && I22 > 0) {
    // library W/I name without drawing b/h: nominal depth from the name (in),
    // flange width from I22 ≈ A_flanges·bf²/12 with ~2/3 of A in the flanges
    const mN = name.match(/(\d+(\.\d+)?)/), dn = mN ? +mN[1] * 0.0254 : 0;
    if (dn > 0.05 && dn < 1.6) { h = h || dn; b = b || Math.sqrt(12 * I22 / (0.66 * A)); }
  }
  if (!b || !h) {   // generic box from A / I
    if (A > 0 && I33 > 0) h = h || Math.sqrt(12 * I33 / A);
    if (A > 0 && I22 > 0) b = b || Math.sqrt(12 * I22 / A);
    if (!h && b) h = A > 0 ? A / b : b;
    if (!b && h) b = A > 0 ? A / h : h;
    if (!b || !h) { b = 0.3; h = 0.3; }
    return box(b, h);
  }
  const fill = A > 0 ? A / (b * h) : 1;
  const isCircle = shape === "circle" || shape === "circular" ||
    (Math.abs(b - h) < 1e-6 * Math.max(b, 1) && A > 0 && Math.abs(A - Math.PI * b * b / 4) < 0.04 * A);
  const isPipe = shape === "pipe" || (RX_PIPE.test(name) && Math.abs(b - h) < 1e-6 * Math.max(b, 1) && fill < 0.7);
  if (isCircle || isPipe) {
    const pts = vxCircle(b, nCirc);
    let holes = [];
    if (isPipe && lod !== "box") {
      let t = num("t") || num("tw");
      if (!t && A > 0) { const ri2 = b * b / 4 - A / Math.PI; t = ri2 > 0 ? b / 2 - Math.sqrt(ri2) : b / 2; }
      if (t > 0 && t < b / 2) holes = [vxCircle(b - 2 * t, nCirc)];
    }
    return { pts, holes, b, h: b, convex: true, smooth: true };
  }
  const isI = shape === "i" || shape === "w" || (num("tf") > 0 && num("tw") > 0) ||
    (fill < 0.62 && RX_I.test(name) && !RX_TUBE.test(name));
  if (isI && lod !== "box") {
    const d = num("d") || h, bf = num("bf") || b;
    let tf = num("tf"), tw = num("tw");
    if (!(tf > 0 && tw > 0)) { const s = vxSolveI(A, I33, d, bf); if (s) { tf = s.tf; tw = s.tw; } }
    if (tf > 0 && tw > 0 && tf < d / 2) return { pts: vxIShape(d, bf, tf, tw), holes: [], b: bf, h: d, convex: false };
  }
  const isTube = shape === "box" || shape === "tube" || (RX_TUBE.test(name) && fill < 0.7);
  if (isTube && lod !== "box" && A > 0) {
    // uniform wall t: b h − (b − 2t)(h − 2t) = A → 4t² − 2(b + h)t + A = 0
    const qb = -2 * (b + h), disc = qb * qb - 16 * A;
    const t = num("t") || (disc >= 0 ? (-qb - Math.sqrt(disc)) / 8 : 0);
    if (t > 0 && t < Math.min(b, h) / 2) return { pts: ccw(vxRect(b, h)), holes: [ccw(vxRect(b - 2 * t, h - 2 * t))], b, h, convex: true };
  }
  return box(b, h);
}

/* ================================================================
   3D helpers
   ================================================================ */
const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const sub3 = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross3 = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm3 = a => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
const lerp3 = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

/** Sutherland–Hodgman: keep n·p ≤ d for every plane. */
function vxClipPoly3(pts, planes) {
  let out = pts;
  for (const { n, d } of planes) {
    const inp = out; out = [];
    if (!inp.length) break;
    for (let i = 0; i < inp.length; i++) {
      const cur = inp[i], prev = inp[(i + inp.length - 1) % inp.length];
      const fc = dot3(n, cur) - d, fp = dot3(n, prev) - d;
      if (fc <= 1e-9) {
        if (fp > 1e-9) out.push(lerp3(prev, cur, fp / (fp - fc)));
        out.push(cur);
      } else if (fp <= 1e-9) out.push(lerp3(prev, cur, fp / (fp - fc)));
    }
  }
  return out.length >= 3 ? out : null;
}
/** Parameter range [t0, t1] of p1→p2 inside all planes, or null. */
function vxClipSeg(p1, p2, planes) {
  let t0 = 0, t1 = 1;
  for (const { n, d } of planes) {
    const f0 = dot3(n, p1) - d, f1 = dot3(n, p2) - d;
    if (f0 > 1e-9 && f1 > 1e-9) return null;
    if (f0 > 1e-9) t0 = Math.max(t0, f0 / (f0 - f1));
    else if (f1 > 1e-9) t1 = Math.min(t1, f0 / (f0 - f1));
  }
  return t1 - t0 > 1e-9 ? [t0, t1] : null;
}

/** Clip a profile polygon to sign·(y·fy + z·fz − c) ≥ 0 (works on concave). */
function vxClipProfile(pts, fy, fz, c, sign) {
  if (!isFinite(c)) return pts;
  const f = q => sign * (q[0] * fy + q[1] * fz - c);
  const out = [];
  for (let i = 0; i < pts.length; i++) {
    const cur = pts[i], prev = pts[(i + pts.length - 1) % pts.length];
    const fc = f(cur), fp = f(prev);
    if (fc >= -1e-12) {
      if (fp < -1e-12) { const t = fp / (fp - fc); out.push([prev[0] + (cur[0] - prev[0]) * t, prev[1] + (cur[1] - prev[1]) * t]); }
      out.push(cur);
    } else if (fp >= -1e-12) { const t = fp / (fp - fc); out.push([prev[0] + (cur[0] - prev[0]) * t, prev[1] + (cur[1] - prev[1]) * t]); }
  }
  const res = [];
  for (const q of out) { const l = res[res.length - 1]; if (!l || Math.hypot(q[0] - l[0], q[1] - l[1]) > 1e-9) res.push(q); }
  if (res.length > 1 && Math.hypot(res[0][0] - res[res.length - 1][0], res[0][1] - res[res.length - 1][1]) < 1e-9) res.pop();
  return res;
}

/* ================================================================
   Mesh builders
   ================================================================ */
/** Prism mesh through rings. stations: [{s, sy, sz, off:[3]}]; profile pts
    [y,z]; axes {x,y,z}; P(s) reference point. */
function vxBuildPrism(pts, holes, convex, stations, axes, refAt, smooth = false) {
  const n = pts.length, K = stations.length;
  const V = new Float64Array(K * n * 3), A = new Float64Array(K * 3);
  const { y, z } = axes;
  stations.forEach((st, k) => {
    const c = refAt(st.s);
    const cx = c[0] + st.off[0], cy = c[1] + st.off[1], cz = c[2] + st.off[2];
    A[k * 3] = cx; A[k * 3 + 1] = cy; A[k * 3 + 2] = cz;
    for (let e = 0; e < n; e++) {
      const py = pts[e][0] * st.sy, pz = pts[e][1] * st.sz, o = (k * n + e) * 3;
      V[o] = cx + py * y[0] + pz * z[0]; V[o + 1] = cy + py * y[1] + pz * z[1]; V[o + 2] = cz + py * y[2] + pz * z[2];
    }
  });
  const SN = new Float64Array((K - 1) * n * 3), SC = new Float64Array((K - 1) * n * 3);
  for (let k = 0; k < K - 1; k++) for (let e = 0; e < n; e++) {
    const e1 = (e + 1) % n;
    const a = (k * n + e) * 3, b = (k * n + e1) * 3, c = ((k + 1) * n + e1) * 3, d = ((k + 1) * n + e) * 3;
    // diagonals cross product → outward normal (profile is CCW in (y, z), x = y × z)
    const d1 = [V[c] - V[a], V[c + 1] - V[a + 1], V[c + 2] - V[a + 2]];
    const d2 = [V[d] - V[b], V[d + 1] - V[b + 1], V[d + 2] - V[b + 2]];
    let nn = cross3(d2, d1);
    // orient with the 2D outward normal of the edge (robust for tapers)
    const ey = pts[e1][0] - pts[e][0], ez = pts[e1][1] - pts[e][1];
    const out = [ez * y[0] - ey * z[0], ez * y[1] - ey * z[1], ez * y[2] - ey * z[2]];
    if (dot3(nn, out) < 0) nn = [-nn[0], -nn[1], -nn[2]];
    nn = norm3(nn);
    const f = (k * n + e) * 3;
    SN[f] = nn[0]; SN[f + 1] = nn[1]; SN[f + 2] = nn[2];
    SC[f] = (V[a] + V[b] + V[c] + V[d]) / 4; SC[f + 1] = (V[a + 1] + V[b + 1] + V[c + 1] + V[d + 1]) / 4; SC[f + 2] = (V[a + 2] + V[b + 2] + V[c + 2] + V[d + 2]) / 4;
  }
  const capHole = k => holes.map(hp => {
    const st = stations[k], c = [A[k * 3], A[k * 3 + 1], A[k * 3 + 2]];
    const out = new Float64Array(hp.length * 3);
    hp.forEach(([py0, pz0], i) => {
      const py = py0 * st.sy, pz = pz0 * st.sz;
      out[i * 3] = c[0] + py * y[0] + pz * z[0]; out[i * 3 + 1] = c[1] + py * y[1] + pz * z[1]; out[i * 3 + 2] = c[2] + py * y[2] + pz * z[2];
    });
    return out;
  });
  const ax = norm3([A[(K - 1) * 3] - A[0], A[(K - 1) * 3 + 1] - A[1], A[(K - 1) * 3 + 2] - A[2]]);
  return { n, K, V, A, SN, SC, convex, smooth, ax, H0: capHole(0), H1: capHole(K - 1) };
}

/** Stations along a member (fractions in [t0, t1]) with taper + offsets. */
function vxMemberStations(model, mm, prof, t0, t1, chunkLen, zcuts = []) {
  const L = Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]) || 1;
  const axes = vxMemberAxes(mm);
  const sec = (model.sections || {})[mm.section];
  const cp = mm.cardinal_point == null ? 10 : +mm.cardinal_point;
  const g = VX_CP_GRID[cp] || [0, 0];
  // joint offsets (global or local), interpolated i → j
  let ei = [0, 0, 0], ej = [0, 0, 0];
  const jo = mm.joint_offsets;
  if (jo && typeof jo === "object") {
    const loc = jo.system === "local";
    const conv = d => (loc ? [0, 1, 2].map(k => d[0] * axes.x[k] + d[1] * axes.y[k] + d[2] * axes.z[k]) : d);
    ei = conv((jo.i || [0, 0, 0]).map(Number)); ej = conv((jo.j || [0, 0, 0]).map(Number));
  }
  // taper (nonprismatic): h(x), b(x) relative to the i-end profile
  let lay = null;
  if (vxIsNpSec(sec)) {
    const l = vxNpLayout(model, sec, L);
    if (l && l.segs && l.segs.length && l.segs.every(s => s.p0 && s.p1)) lay = l;
  }
  const fr = new Set([t0, t1]);
  const nCh = Math.max(1, Math.ceil(((t1 - t0) * L) / chunkLen - 0.2));
  for (let c = 1; c < nCh; c++) fr.add(t0 + (t1 - t0) * c / nCh);
  // slab-zone boundaries crossed by the axis are chunk boundaries too
  const z0 = mm.pi[2] + ei[2], dz = (mm.pj[2] + ej[2]) - z0;
  if (Math.abs(dz) > 1e-9) for (const zc of zcuts) {
    const f = (zc - z0) / dz;
    if (f > t0 + 1e-6 && f < t1 - 1e-6) fr.add(f);
  }
  const chunkFr = [...fr].sort((a, b) => a - b);
  if (lay) for (const s of lay.segs) {
    const var_ = Math.abs((s.p0.h || 0) - (s.p1.h || 0)) > 1e-9 || Math.abs((s.p0.b || 0) - (s.p1.b || 0)) > 1e-9;
    const nsub = var_ ? 6 : 1;
    for (let k = 0; k <= nsub; k++) {
      const f = (s.x0 + (s.x1 - s.x0) * k / nsub) / L;
      if (f > t0 + 1e-9 && f < t1 - 1e-9) fr.add(f);
    }
  }
  const all = [...fr].sort((a, b) => a - b).filter((v, i, a) => i === 0 || v - a[i - 1] > 1e-9);
  const stations = all.map(s => {
    let sy = 1, sz = 1, hh = prof.h, bb = prof.b;
    if (lay) {
      const p = vxNpPropsAt(lay, s * L);
      if (p && p.h > 0 && prof.h > 0) { sy = p.h / prof.h; hh = p.h; }
      if (p && p.b > 0 && prof.b > 0) { sz = p.b / prof.b; bb = p.b; }
    }
    const cy = 0.5 * hh * g[0], cz = 0.5 * bb * g[1];
    const off = [0, 1, 2].map(k => -cy * axes.y[k] - cz * axes.z[k] + ei[k] + (ej[k] - ei[k]) * s);
    return { s, sy, sz, off, size: Math.max(hh, bb) };
  });
  // chunk index ranges (ring indices)
  const chunks = [];
  for (let c = 0; c < chunkFr.length - 1; c++) {
    const k0 = all.findIndex(v => Math.abs(v - chunkFr[c]) < 1e-9);
    const k1 = all.findIndex(v => Math.abs(v - chunkFr[c + 1]) < 1e-9);
    if (k0 >= 0 && k1 > k0) chunks.push([k0, k1]);
  }
  return { stations, chunks, axes, L, size: Math.max(...stations.map(s => s.size)) };
}

/* ================================================================
   Module state + init
   ================================================================ */
export function initViewExt(sky) {
  const viewer = sky.viewer;
  if (!viewer) return;
  let opts = vxLoad();
  let isDef = vxIsDefault(opts);
  let ver = 0;                      // bumps on model / option edits
  let geo = null;                   // cached geometry
  let lastStats = { items: 0, faces: 0 };
  let lastError = null;             // swallowed hook errors (the viewer never breaks), for tests

  const S = () => sky.store || {};
  const model = () => viewer.model;
  const redraw = () => { viewer._dirty = true; };
  const setOpts = patch => {
    opts = vxNormalize({ ...opts, ...patch, show: { ...opts.show, ...((patch && patch.show) || {}) }, cut: { ...opts.cut, ...((patch && patch.cut) || {}) } });
    isDef = vxIsDefault(opts);
    vxSave(opts); ver++; redraw(); syncChips();
    return vxClone(opts);
  };
  document.addEventListener("sky:model-changed", () => { ver++; redraw(); });
  document.addEventListener("sky:results-changed", () => redraw());

  /* ---------------- filters ---------------- */
  const selKey = () => (S().selection || []).map(r => `${r.type}:${r.uid}`).sort().join("|");
  function filterSig() {
    const o = opts;
    return JSON.stringify([o.show, o.storyFrom, o.storyTo, o.group, o.selOnly ? selKey() : "", o.cut, o.extrude, o.extrudeShells]);
  }
  function makeFilter(m) {
    const o = opts, planes = [];
    let zlo = -Infinity, zhi = Infinity, baseZ = viewer._bbox ? viewer._bbox[0][2] : 0;
    const stories = (m.stories || []).slice().sort((a, b) => a.elevation - b.elevation);
    if ((o.storyFrom || o.storyTo) && stories.length) {
      const iF = o.storyFrom ? stories.findIndex(s => s.name === o.storyFrom) : 0;
      const iT = o.storyTo ? stories.findIndex(s => s.name === o.storyTo) : stories.length - 1;
      if (iF >= 0 && iT >= 0) {
        const a = Math.min(iF, iT), b = Math.max(iF, iT);
        zlo = a > 0 ? stories[a - 1].elevation : Math.min(baseZ, stories[0].elevation - 1e3);
        zhi = stories[b].elevation;
        planes.push({ n: [0, 0, 1], d: zhi + 1e-6 }, { n: [0, 0, -1], d: -(zlo - 1e-6) });
      }
    }
    const cut = o.cut;
    if (cut.on && cut.coord != null) {
      const ax = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] }[cut.axis];
      planes.push(cut.keep === "below" ? { n: ax, d: +cut.coord + 1e-6 } : { n: ax.map(v => -v), d: -(+cut.coord) + 1e-6 });
    }
    const atFloorOnly = zs => zlo > baseZ + 1e-6 && Math.max(...zs) <= zlo + 1e-6;   // belongs to the story below
    const grp = o.group && m.groups && m.groups[o.group] ? m.groups[o.group] : null;
    const gSet = k => new Set(grp ? (grp[k] || []).map(String) : []);
    const gm = gSet("members"), gs = gSet("shells"), gl = gSet("links");
    const sel = o.selOnly ? new Set((S().selection || []).map(r => `${r.type}:${r.uid}`)) : null;
    const selHas = (t, uid) => sel.has(`${t}:${uid}`) || (t === "member" && (sel.has(`frame:${uid}`) || sel.has(`beam:${uid}`) || sel.has(`column:${uid}`) || sel.has(`brace:${uid}`)));
    const show = o.show;
    const memberOk = mm => {
      if (!show[mm.kind] && show[mm.kind] !== undefined) return false;
      if (grp && !gm.has(String(mm.uid))) return false;
      if (sel && !selHas("member", mm.uid)) return false;
      if (atFloorOnly([mm.p1 ? mm.p1[2] : mm.pi[2], mm.p2 ? mm.p2[2] : mm.pj[2]])) return false;
      return true;
    };
    const shellOk = sh => {
      if (!show[sh.kind === "wall" ? "wall" : "slab"]) return false;
      if (grp && !gs.has(String(sh.uid))) return false;
      if (sel && !selHas("shell", sh.uid)) return false;
      if (atFloorOnly(sh.corners.map(p => p[2]))) return false;
      return true;
    };
    const linkOk = lk => show.link && (!grp || gl.has(String(lk.uid))) && (!sel || selHas("link", lk.uid));
    const inside = p => planes.every(({ n, d }) => dot3(n, p) - d <= 1e-6);
    return { planes, memberOk, shellOk, linkOk, inside, zlo, zhi, active: !!(planes.length || grp || sel) };
  }

  /* ---------------- geometry cache ---------------- */
  function chunkLenOf() { return Math.max((viewer.radius || 10) / 6, 0.75); }
  function buildGeo() {
    const m = model();
    const F = makeFilter(m);
    const byUid = new Map((m.members || []).map(mm => [mm.uid, mm]));
    const shellByUid = new Map((m.shells || []).map(s => [s.uid, s]));
    const chunkLen = chunkLenOf();
    const segVis = new Map();          // seg → null | seg | clipped {p1, p2}
    const memberMeshes = [];
    const profCache = new Map();
    const prof = (name, lod) => {
      const k = name + "\u0001" + lod;
      if (!profCache.has(k)) {
        let sec = (m.sections || {})[name];
        if (vxIsNpSec(sec)) {   // NP: the i-end section's shape, scaled by the taper
          const sg = (sec.segments || [])[0];
          const s0 = sg && (m.sections || {})[sg.start_section];
          const raw = vxNpRaw(s0) || {};
          sec = s0 ? { ...s0, b: s0.b || raw.b, h: s0.h || raw.h } : sec;
          profCache.set(k, vxProfileOf(m, sg ? sg.start_section : name, sec, lod));
        } else profCache.set(k, vxProfileOf(m, name, sec, lod));
      }
      return profCache.get(k);
    };
    const colorOf = (kind, secName, mat) => {
      if (opts.colorBy === "section" && secName) return VX_PALETTE[sectionIndex(m, secName) % VX_PALETTE.length];
      if (opts.colorBy === "material" && mat) return VX_PALETTE[(vxHash(mat) + 3) % VX_PALETTE.length];
      return VX_TYPE_COLORS[kind] || VX_TYPE_COLORS.beam;
    };
    // end caps buried in a joint are skipped (beam/brace into a column, column
    // continuing through a floor): fewer faces and no cap painted over a column
    const pk = q => `${(+q[0]).toFixed(4)},${(+q[1]).toFixed(4)},${(+q[2]).toFixed(4)}`;
    const colEnds = new Map();
    for (const sg of viewer.segs || []) if (sg.kind === "column") for (const q of [sg.p1, sg.p2]) colEnds.set(pk(q), (colEnds.get(pk(q)) || 0) + 1);
    const buried = (q, kind) => (colEnds.get(pk(q)) || 0) >= (kind === "column" ? 2 : 1);
    // ---- pass 1: shells → visibility, plate data, horizontal slab zones
    const shellVis = new Map();
    const plateSrc = [];
    for (const sp of viewer.shellPolys || []) {
      if (!F.shellOk(sp)) { shellVis.set(sp, null); continue; }
      let corners = sp.corners, openings = sp.openings || [];
      if (F.planes.length) {
        corners = vxClipPoly3(sp.corners, F.planes);
        if (!corners) { shellVis.set(sp, null); continue; }
        openings = openings.map(o => vxClipPoly3(o, F.planes)).filter(Boolean);
      }
      const vis = corners === sp.corners ? sp : { ...sp, corners, openings };
      shellVis.set(sp, vis);
      if (!(opts.extrude && opts.extrudeShells)) continue;
      const ms = shellByUid.get(sp.uid) || {};
      const ss = (m.shell_sections || {})[ms.section] || {};
      let t = +ss.thickness || 0;
      if (ss.layered && Array.isArray(ss.layered.layers)) t = ss.layered.layers.reduce((a, l) => a + (+l.t || 0), 0) || t;
      if (!(t > 0)) t = sp.kind === "wall" ? 0.2 : 0.15;
      const col = opts.colorBy === "section" && ms.section ? VX_PALETTE[(sectionIndex(m, ms.section, true) + 7) % VX_PALETTE.length]
        : opts.colorBy === "material" && ss.material ? VX_PALETTE[(vxHash(ss.material) + 3) % VX_PALETTE.length]
          : VX_TYPE_COLORS[sp.kind === "wall" ? "wall" : "slab"];
      plateSrc.push({ sp, vis, t, col });
    }
    // Horizontal slabs split space into z-zones (slab thickness bands and the
    // gaps between them). A horizontal plane separates: everything on the
    // eye's side of it is in front of everything on the far side, so the
    // painter's sort is zone-major (farthest zone first), depth-minor. Frames
    // are split at the zone boundaries (columns by stations, horizontal beams
    // by cutting the section profile) so no solid straddles a slab.
    const ivs = [], hslabs = [];
    for (const ps of plateSrc) {
      if (ps.sp.kind === "wall") continue;
      const n = vxRegionFrame(ps.sp.corners).e3;
      if (Math.abs(n[2]) < 0.999) continue;
      const z = ps.vis.corners[0][2];
      ivs.push([z - ps.t, z]);
      hslabs.push({ z: z - ps.t / 2, outer: ps.vis.corners.map(q => [q[0], q[1]]), holes: (ps.vis.openings || []).map(o => o.map(q => [q[0], q[1]])) });
    }
    ivs.sort((a, b) => a[0] - b[0]);
    const zones = [];
    for (const iv of ivs) {
      const last = zones[zones.length - 1];
      if (last && iv[0] <= last[1] + 1e-6) last[1] = Math.max(last[1], iv[1]); else zones.push([iv[0], iv[1]]);
    }
    const zoneOf = z => {
      for (let k = 0; k < zones.length; k++) {
        if (z < zones[k][0] - 1e-6) return 2 * k;
        if (z <= zones[k][1] + 1e-6) return 2 * k + 1;
      }
      return 2 * zones.length;
    };
    const zcuts = zones.flat();
    // plan point (x, y) covered by a slab of zone zid (inside the outline, not in an opening)
    const covered = (x, y, zid) => hslabs.some(h => zoneOf(h.z) === zid && vxPointInPoly2(x, y, h.outer) &&
      !h.holes.some(o => vxPointInPoly2(x, y, o)));
    // with slab zones the zone-major sort replaces length chunking of frames
    // (without slabs, only very long members are chunked — chunk seams show on solids)
    const memChunk = zones.length ? 1e9 : Math.max(chunkLen * 3, 10);
    // ---- pass 2: frames
    for (const seg of viewer.segs || []) {
      const mm = byUid.get(seg.uid) || { uid: seg.uid, kind: seg.kind, section: seg.section, pi: seg.p1, pj: seg.p2 };
      if (!F.memberOk({ ...mm, p1: seg.p1, p2: seg.p2, kind: seg.kind })) { segVis.set(seg, null); continue; }
      const tr = F.planes.length ? vxClipSeg(seg.p1, seg.p2, F.planes) : [0, 1];
      if (!tr) { segVis.set(seg, null); continue; }
      segVis.set(seg, tr[0] === 0 && tr[1] === 1 ? seg : { p1: lerp3(seg.p1, seg.p2, tr[0]), p2: lerp3(seg.p1, seg.p2, tr[1]) });
      if (!opts.extrude) continue;
      const sec = (m.sections || {})[mm.section] || {};
      const full = prof(mm.section, "full"), box = prof(mm.section, "box");
      const st = vxMemberStations(m, mm, full, tr[0], tr[1], memChunk, zcuts);
      const ref = s => lerp3(mm.pi, mm.pj, s);
      const col = colorOf(seg.kind, mm.section, sec.material || "");
      const nc0 = tr[0] === 0 && buried(seg.p1, seg.kind), nc1 = tr[1] === 1 && buried(seg.p2, seg.kind);
      const mesh = { uid: seg.uid, seg, size: st.size, col, parts: [] };
      // horizontal prismatic member crossing a slab band → profile pieces
      const ax = st.axes, horiz = Math.abs(ax.x[2]) < 1e-6 && !vxIsNpSec(sec);
      let pieces = null;
      if (horiz && zones.length) {
        const zc = ref(st.stations[0].s)[2] + st.stations[0].off[2];
        const wz = q => zc + q[0] * ax.y[2] + q[1] * ax.z[2];
        const zs = full.pts.map(wz), lo = Math.min(...zs), hi = Math.max(...zs);
        const cuts = zcuts.filter(c => c > lo + 1e-6 && c < hi - 1e-6);
        if (cuts.length) {
          const fy = ax.y[2], fz = ax.z[2];
          const bounds = [-Infinity, ...cuts, Infinity];
          pieces = [];
          for (let i = 0; i < bounds.length - 1; i++) {
            const pf = vxClipProfile(vxClipProfile(full.pts, fy, fz, bounds[i] - zc, 1), fy, fz, bounds[i + 1] - zc, -1);
            if (pf.length < 3 || Math.abs(area2(pf)) < 1e-10) continue;
            const pzs = pf.map(wz);
            const zid = zoneOf((Math.min(...pzs) + Math.max(...pzs)) / 2);
            if (zid % 2 === 1) {   // piece inside a slab band: drop it where the slab covers both sides
              const d = ax.x, w = st.size / 2 + 0.03, nx = -d[1], ny = d[0];
              const hidden = [0.03, 0.25, 0.5, 0.75, 0.97].every(f => {
                const q = ref(tr[0] + (tr[1] - tr[0]) * f);
                return covered(q[0] + nx * w, q[1] + ny * w, zid) && covered(q[0] - nx * w, q[1] - ny * w, zid);
              });
              if (hidden) continue;
            }
            // faces lying on a cut line are internal (inside the slab band);
            // vertices on a cut line get no longitudinal outline
            const pp = ccw(pf);
            const onCut = q => cuts.some(c => Math.abs(wz(q) - c) < 1e-7);
            const skip = pp.map((q, e) => onCut(q) && onCut(pp[(e + 1) % pp.length]));
            pieces.push({ pts: pp, skip, cutV: pp.map(onCut), zid, convex: full.convex });
          }
        }
      }
      if (pieces) {
        for (const pc of pieces) {
          const mh = vxBuildPrism(pc.pts, [], pc.convex, st.stations, ax, ref, false);
          mh.noCap0 = nc0; mh.noCap1 = nc1; mh.skip = pc.skip; mh.cutV = pc.cutV;
          mesh.parts.push({ full: mh, box: mh, zid: pc.zid });
        }
      } else {
        const meshFull = vxBuildPrism(full.pts, full.holes, full.convex, st.stations, ax, ref, !!full.smooth);
        const meshBox = (box.pts.length === full.pts.length && !full.holes.length && full.convex) ? meshFull
          : vxBuildPrism(box.pts, [], true, st.stations, ax, ref, !!box.smooth);
        for (const mh of [meshFull, meshBox]) { mh.noCap0 = nc0; mh.noCap1 = nc1; }
        mesh.parts.push({ full: meshFull, box: meshBox, zid: -1 });
      }
      mesh.chunks = [];
      for (const part of mesh.parts) for (const [k0, k1] of st.chunks) {
        const A = part.full.A;
        const c = [(A[k0 * 3] + A[k1 * 3]) / 2, (A[k0 * 3 + 1] + A[k1 * 3 + 1]) / 2, (A[k0 * 3 + 2] + A[k1 * 3 + 2]) / 2];
        const zid = part.zid >= 0 ? part.zid : zoneOf(c[2]);
        if (part.zid < 0 && zid % 2 === 1 && Math.abs(ax.x[2]) > 0.999) {   // column inside a slab band
          const w = st.size / 2 + 0.03;
          if ([[1, 1], [1, -1], [-1, 1], [-1, -1], [1, 0], [-1, 0], [0, 1], [0, -1]].every(([sx, sy]) => covered(c[0] + sx * w, c[1] + sy * w, zid))) continue;
        }
        mesh.chunks.push({ k0, k1, c, part, zid });
      }
      memberMeshes.push(mesh);
    }
    // ---- pass 3: plates (walls are cut at the zone boundaries too)
    const plates = [];
    for (const ps of plateSrc) {
      const pl = vxPlate(ps.sp, ps.vis, ps.t, chunkLen, ps.col, zcuts);
      for (const q of pl) q.zid = zoneOf(q.type === "tile" ? q.zmid : q.c[2]);
      plates.push(...pl);
    }
    const linkVis = new Map();
    for (const lk of viewer.linkSegs || []) {
      if (!F.linkOk(lk)) { linkVis.set(lk, false); continue; }
      linkVis.set(lk, !F.planes.length || !!vxClipSeg(lk.p1, lk.p2, F.planes));
    }
    // joints of the visible objects (supports / springs / joints display)
    const jkey = p => `${(+p[0]).toFixed(4)},${(+p[1]).toFixed(4)},${(+p[2]).toFixed(4)}`;
    const joints = new Map();
    for (const [seg, v] of segVis) if (v) for (const p of [seg.p1, seg.p2]) if (F.inside(p)) joints.set(jkey(p), p);
    for (const [sp, v] of shellVis) if (v) for (const p of sp.corners) if (F.inside(p)) joints.set(jkey(p), p);
    const ptOk = p => !F.active || joints.has(jkey(p)) || (!opts.group && !opts.selOnly && F.inside(p));
    const segByUid = new Map((viewer.segs || []).map(s => [s.uid, s]));
    return { segVis, shellVis, linkVis, memberMeshes, plates, joints: [...joints.values()], ptOk, F, byUid, segByUid, prof, colorOf, zones, zoneOf };
  }
  function getGeo() {
    const key = filterSig() + "|" + ver + "|" + opts.colorBy;
    if (geo && geo.key === key && geo.segs === viewer.segs && geo.shells === viewer.shellPolys && geo.model === viewer.model) return geo;
    const g = buildGeo();
    g.key = key; g.segs = viewer.segs; g.shells = viewer.shellPolys; g.model = viewer.model;
    geo = g;
    return g;
  }
  const secIdx = { model: null, map: null };
  function sectionIndex(m, name, shell) {
    if (secIdx.model !== m) {
      secIdx.model = m;
      secIdx.map = new Map();
      Object.keys(m.sections || {}).sort().forEach((k, i) => secIdx.map.set("f:" + k, i));
      Object.keys(m.shell_sections || {}).sort().forEach((k, i) => secIdx.map.set("s:" + k, i));
    }
    const v = secIdx.map.get((shell ? "s:" : "f:") + name);
    return v == null ? vxHash(name) : v;
  }

  /* ---------------- per-frame camera ---------------- */
  function camera() {
    const b = viewer._basis();
    const w = viewer.canvas.clientWidth, h = viewer.canvas.clientHeight;
    const focal = (h / 2) / Math.tan(viewer.fov / 2);
    const L = norm3([-b.fwd[0] * 0.55 + b.up[0] * 0.62 - b.right[0] * 0.38,
      -b.fwd[1] * 0.55 + b.up[1] * 0.62 - b.right[1] * 0.38, -b.fwd[2] * 0.55 + b.up[2] * 0.62 - b.right[2] * 0.38]);
    return { E: b.eye, F: b.fwd, R: b.right, U: b.up, focal, cx: w / 2, cy: h / 2, near: 0.05, L };
  }

  /* ---------------- drawing primitives ---------------- */
  let SX = new Float64Array(256), SY = new Float64Array(256);
  const faceBuf = [];
  const EDGE = "rgba(8, 12, 18, 0.38)";
  const shadeIdx = (C, nx, ny, nz) => {
    const d = nx * C.L[0] + ny * C.L[1] + nz * C.L[2];
    const s = d > 0 ? d : 0.12 * -d;   // a little back-light so hidden-side faces are not black
    return Math.min(VX_SHADES - 1, Math.round(s * (VX_SHADES - 1)));
  };
  function projRange(C, V, from, cnt) {
    if (SX.length < cnt) { SX = new Float64Array(cnt * 2); SY = new Float64Array(cnt * 2); }
    const E = C.E, R = C.R, U = C.U, F = C.F, f = C.focal;
    for (let i = 0; i < cnt; i++) {
      const o = (from + i) * 3;
      const dx = V[o] - E[0], dy = V[o + 1] - E[1], dz = V[o + 2] - E[2];
      const zc = dx * F[0] + dy * F[1] + dz * F[2];
      if (zc < C.near) return false;
      SX[i] = C.cx + (dx * R[0] + dy * R[1] + dz * R[2]) / zc * f;
      SY[i] = C.cy - (dx * U[0] + dy * U[1] + dz * U[2]) / zc * f;
    }
    return true;
  }
  function projPt(C, x, y, z) {
    const dx = x - C.E[0], dy = y - C.E[1], dz = z - C.E[2];
    const zc = dx * C.F[0] + dy * C.F[1] + dz * C.F[2];
    if (zc < C.near) return null;
    return [C.cx + (dx * C.R[0] + dy * C.R[1] + dz * C.R[2]) / zc * C.focal, C.cy - (dx * C.U[0] + dy * C.U[1] + dz * C.U[2]) / zc * C.focal, zc];
  }
  /** Draw one prism chunk (rings k0..k1) with culling + shading. */
  function drawPrism(ctx, C, ms, k0, k1, css, edges, alpha) {
    const n = ms.n, cnt = (k1 - k0 + 1) * n;
    if (!projRange(C, ms.V, k0 * n, cnt)) return 0;
    const E = C.E, SN = ms.SN, SC = ms.SC, shades = vxShades(css);
    faceBuf.length = 0;
    const skip = ms.skip;
    for (let k = k0; k < k1; k++) for (let e = 0; e < n; e++) {
      if (skip && skip[e]) continue;
      const f = (k * n + e) * 3;
      if ((E[0] - SC[f]) * SN[f] + (E[1] - SC[f + 1]) * SN[f + 1] + (E[2] - SC[f + 2]) * SN[f + 2] > 0) faceBuf.push(k * n + e);
    }
    let cap0 = false, cap1 = false;
    const A = ms.A, ax = ms.ax;
    if (k0 === 0 && !ms.noCap0) cap0 = (E[0] - A[0]) * -ax[0] + (E[1] - A[1]) * -ax[1] + (E[2] - A[2]) * -ax[2] > 0;
    if (k1 === ms.K - 1 && !ms.noCap1) { const o = k1 * 3; cap1 = (E[0] - A[o]) * ax[0] + (E[1] - A[o + 1]) * ax[1] + (E[2] - A[o + 2]) * ax[2] > 0; }
    if (!ms.convex && faceBuf.length > 1) {
      const F = C.F;
      faceBuf.sort((a, b) => ((SC[b * 3] - E[0]) * F[0] + (SC[b * 3 + 1] - E[1]) * F[1] + (SC[b * 3 + 2] - E[2]) * F[2])
        - ((SC[a * 3] - E[0]) * F[0] + (SC[a * 3 + 1] - E[1]) * F[1] + (SC[a * 3 + 2] - E[2]) * F[2]));
    }
    if (alpha < 1) ctx.globalAlpha = alpha;
    ctx.lineWidth = 0.6;
    const base = k0 * n;
    let faces = 0;
    // convex prisms: visible faces never overlap → one batched edge stroke per item
    const batch = edges && ms.convex;
    const ep = batch ? new Path2D() : null;
    const capDraw = (k, nx, ny, nz, holes) => {
      const o = (k - k0) * n;
      ctx.beginPath();
      ctx.moveTo(SX[o], SY[o]);
      for (let e = 1; e < n; e++) ctx.lineTo(SX[o + e], SY[o + e]);
      ctx.closePath();
      ctx.fillStyle = shades[shadeIdx(C, nx, ny, nz)];
      ctx.fill();
      if (batch) {
        ep.moveTo(SX[o], SY[o]);
        for (let e = 1; e < n; e++) ep.lineTo(SX[o + e], SY[o + e]);
        ep.closePath();
      } else if (edges) { ctx.strokeStyle = EDGE; ctx.stroke(); }
      for (const hv of holes) {
        ctx.beginPath();
        let ok = true;
        for (let i = 0; i < hv.length / 3; i++) {
          const q = projPt(C, hv[i * 3], hv[i * 3 + 1], hv[i * 3 + 2]);
          if (!q) { ok = false; break; }
          i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]);
        }
        if (!ok) continue;
        ctx.closePath();
        ctx.fillStyle = shades[1];
        ctx.fill();
      }
      faces++;
    };
    // caps are behind/in front of the sides only at the very ends: draw the
    // far cap first, the near cap last (a convex prism never self-overlaps).
    for (const fi of faceBuf) {
      const k = (fi / n) | 0, e = fi - k * n, e1 = e + 1 === n ? 0 : e + 1;
      const a = k * n + e - base, b = k * n + e1 - base, c = (k + 1) * n + e1 - base, d = (k + 1) * n + e - base;
      ctx.beginPath();
      ctx.moveTo(SX[a], SY[a]); ctx.lineTo(SX[b], SY[b]); ctx.lineTo(SX[c], SY[c]); ctx.lineTo(SX[d], SY[d]);
      ctx.closePath();
      ctx.fillStyle = shades[shadeIdx(C, SN[fi * 3], SN[fi * 3 + 1], SN[fi * 3 + 2])];
      ctx.fill();
      if (edges) {
        // longitudinal edges (not on smooth round profiles) + member-end rims only:
        // no seams at chunk / taper-station boundaries
        const tg = batch ? ep : ctx;
        if (!batch) ctx.beginPath();
        if (!ms.smooth) {
          const cv = ms.cutV;
          if (!cv || !cv[e1]) { tg.moveTo(SX[b], SY[b]); tg.lineTo(SX[c], SY[c]); }
          if (!cv || !cv[e]) { tg.moveTo(SX[d], SY[d]); tg.lineTo(SX[a], SY[a]); }
        }
        if (k === 0) { tg.moveTo(SX[a], SY[a]); tg.lineTo(SX[b], SY[b]); }
        if (k + 2 === ms.K) { tg.moveTo(SX[c], SY[c]); tg.lineTo(SX[d], SY[d]); }
        if (!batch) { ctx.strokeStyle = EDGE; ctx.stroke(); }
      }
      faces++;
    }
    if (cap0) capDraw(k0, -ax[0], -ax[1], -ax[2], ms.H0);
    if (cap1) capDraw(k1, ax[0], ax[1], ax[2], ms.H1);
    if (batch) { ctx.strokeStyle = EDGE; ctx.stroke(ep); }
    if (alpha < 1) ctx.globalAlpha = 1;
    return faces;
  }
  function drawSlabMerged(ctx, C, it) {
    const t0 = it.vxpm[0], E = C.E;
    const top = (E[0] - t0.ct[0]) * t0.n[0] + (E[1] - t0.ct[1]) * t0.n[1] + (E[2] - t0.ct[2]) * t0.n[2] > 0;
    ctx.beginPath();
    for (const p of it.vxpm) {
      for (const lp of (top ? p.top : p.bot)) {
        let ok = true;
        const n = lp.length / 3;
        for (let i = 0; i < n; i++) {
          const q = projPt(C, lp[i * 3], lp[i * 3 + 1], lp[i * 3 + 2]);
          if (!q) { ok = false; break; }
          i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]);
        }
        if (ok) ctx.closePath();
      }
    }
    const s = top ? 1 : -1;
    ctx.globalAlpha = it.alpha;
    ctx.fillStyle = vxShades(t0.col)[shadeIdx(C, s * t0.n[0], s * t0.n[1], s * t0.n[2])];
    ctx.fill("evenodd");
    ctx.globalAlpha = 1;
  }
  function drawPlate(ctx, C, it) {
    const p = it.vxp, shades = vxShades(p.col), E = C.E;
    ctx.globalAlpha = it.alpha;
    if (p.type === "tile") {
      const top = (E[0] - p.ct[0]) * p.n[0] + (E[1] - p.ct[1]) * p.n[1] + (E[2] - p.ct[2]) * p.n[2] > 0;
      const loops = top ? p.top : p.bot;
      ctx.beginPath();
      for (const lp of loops) {
        for (let i = 0; i < lp.length / 3; i++) {
          const q = projPt(C, lp[i * 3], lp[i * 3 + 1], lp[i * 3 + 2]);
          if (!q) { ctx.globalAlpha = 1; return; }
          i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]);
        }
        ctx.closePath();
      }
      const s = top ? 1 : -1;
      ctx.fillStyle = shades[shadeIdx(C, s * p.n[0], s * p.n[1], s * p.n[2])];
      ctx.fill("evenodd");
    } else {
      const q = p.q;
      ctx.beginPath();
      for (let i = 0; i < 4; i++) {
        const r = projPt(C, q[i * 3], q[i * 3 + 1], q[i * 3 + 2]);
        if (!r) { ctx.globalAlpha = 1; return; }
        i ? ctx.lineTo(r[0], r[1]) : ctx.moveTo(r[0], r[1]);
      }
      ctx.closePath();
      ctx.fillStyle = shades[shadeIdx(C, p.n[0], p.n[1], p.n[2])];
      ctx.fill();
      if (it.edges) {   // top + bottom rim only (no seams between edge pieces)
        const r = [0, 1, 2, 3].map(i => projPt(C, q[i * 3], q[i * 3 + 1], q[i * 3 + 2]));
        ctx.beginPath(); ctx.moveTo(r[0][0], r[0][1]); ctx.lineTo(r[1][0], r[1][1]); ctx.moveTo(r[2][0], r[2][1]); ctx.lineTo(r[3][0], r[3][1]);
        ctx.strokeStyle = EDGE; ctx.lineWidth = 0.7; ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
  }

  /* ---------------- per-frame state for viewer3d ---------------- */
  function colorFor(mesh) {
    const v = viewer;
    if (v._hover && v._hover.uid === mesh.uid) return "#f4f7fb";
    if (v.highlight && v.highlight.uids && v.highlight.uids.has(mesh.uid)) return v.highlight.color || "#e0a020";
    if (v.memberColors && v.memberColors[mesh.uid]) return v.memberColors[mesh.uid];
    return mesh.col;
  }
  const LINE_PX = 2.5, BOX_PX = 9, EDGE_PX = 9;
  // adaptive quality: while the camera moves (drag / inertia) edge outlines
  // are dropped and the box LOD reaches further; one full-quality frame is
  // drawn once the camera settles
  let forceMoving = false, settleTimer = null;
  const isMoving = () => forceMoving || viewer.canvas.classList.contains("dragging") ||
    Math.abs(viewer.vyaw) > 0.0004 || Math.abs(viewer.vpitch) > 0.0004;
  let lastCam = "";
  function movingNow() {
    const cam = `${viewer.yaw.toFixed(5)},${viewer.pitch.toFixed(5)},${viewer.dist.toFixed(4)},${viewer.target.join(",")}`;
    const changed = cam !== lastCam;
    lastCam = cam;
    const mv = isMoving() || (changed && settleTimer !== null);
    clearTimeout(settleTimer);
    settleTimer = (mv || changed) ? setTimeout(() => { settleTimer = null; redraw(); }, 140) : null;
    return mv;
  }
  function pushSolids(items, C, flags) {
    const g = geo;
    const moving = movingNow();
    let nItems = 0;
    // zone-major painter keys (see buildGeo): farthest z-zone from the eye first
    const nz = g.zones.length;
    const zoned = nz > 0 && opts.extrude && opts.extrudeShells && !flags.overlayActive && !flags.contour;
    let off = null;
    if (zoned) {
      const ze = C.E[2], ids = [];
      for (let id = 0; id <= 2 * nz; id++) {
        const k = id >> 1;
        const a = id % 2 ? g.zones[k][0] : (k > 0 ? g.zones[k - 1][1] : -Infinity);
        const b = id % 2 ? g.zones[k][1] : (k < nz ? g.zones[k][0] : Infinity);
        ids.push([id, ze >= b ? ze - b : ze <= a ? a - ze : 0]);
      }
      ids.sort((x, y) => y[1] - x[1]);
      off = new Float64Array(2 * nz + 1);
      ids.forEach(([id], r) => { off[id] = (ids.length - r) * 1e6; });
      const eyeOff = off[g.zoneOf(ze)];
      for (const it of items) {   // legacy items already queued (links, cut planes, centerlines)
        let zw = null;
        if (it.type === "link" && it.lk) zw = (it.lk.p1[2] + it.lk.p2[2]) / 2;
        else if (it.type === "seg" && it.seg) zw = (it.seg.p1[2] + it.seg.p2[2]) / 2;
        else if (it.type === "cut" && it.cut) zw = it.cut.centroid[2];
        it.z += zw == null ? eyeOff : off[g.zoneOf(zw)];
      }
    }
    if (opts.extrude && !flags.overlayActive) {
      for (const mesh of g.memberMeshes) {
        const css = colorFor(mesh);
        for (const ch of mesh.chunks) {
          const c = ch.c, part = ch.part;
          const z = (c[0] - C.E[0]) * C.F[0] + (c[1] - C.E[1]) * C.F[1] + (c[2] - C.E[2]) * C.F[2];
          if (z < C.near) continue;
          const zk = off ? z + off[ch.zid] : z;
          const px = mesh.size * C.focal / z;
          if (px < LINE_PX) {
            if (part !== mesh.parts[0]) continue;
            const A = part.full.A;
            const a = projPt(C, A[ch.k0 * 3], A[ch.k0 * 3 + 1], A[ch.k0 * 3 + 2]);
            const b = projPt(C, A[ch.k1 * 3], A[ch.k1 * 3 + 1], A[ch.k1 * 3 + 2]);
            if (!a || !b) continue;
            items.push({ type: "vx", z: zk, vxl: 1, a, b, w: Math.max(1, px), css });
          } else {
            items.push({ type: "vx", z: zk, vxm: px < (moving ? BOX_PX * 1.6 : BOX_PX) ? part.box : part.full, k0: ch.k0, k1: ch.k1, css, ed: !moving && px >= EDGE_PX });
          }
          nItems++;
        }
      }
    }
    let merged = null;
    if (opts.extrude && opts.extrudeShells && !flags.overlayActive) {
      const alpha = 1 - opts.shellTransp;
      // zone-sorted horizontal slab: its coplanar tiles never overlap and the
      // sides / members of its band only border them → one fill per slab,
      // drawn first within its zone
      merged = zoned ? new Map() : null;
      for (const p of g.plates) {
        if (flags.contour && p.behavior === "shell") continue;
        if (merged && p.type === "tile" && p.horiz) {
          const c = p.ct;
          const z = (c[0] - C.E[0]) * C.F[0] + (c[1] - C.E[1]) * C.F[1] + (c[2] - C.E[2]) * C.F[2];
          if (z < C.near) continue;
          let it = merged.get(p.uid);
          if (!it) { it = { type: "vx", z: -Infinity, vxpm: [], alpha, zid: p.zid }; merged.set(p.uid, it); }
          it.vxpm.push(p);
          if (z > it.z) it.z = z;
          continue;
        }
        const c = p.type === "tile" ? p.ct : p.c;
        if (p.type === "side" && (C.E[0] - c[0]) * p.n[0] + (C.E[1] - c[1]) * p.n[1] + (C.E[2] - c[2]) * p.n[2] <= 0) continue;
        const z = (c[0] - C.E[0]) * C.F[0] + (c[1] - C.E[1]) * C.F[1] + (c[2] - C.E[2]) * C.F[2];
        if (z < C.near) continue;
        items.push({ type: "vx", z: off ? z + off[p.zid] : z, vxp: p, alpha, edges: opts.edges && !moving });
        nItems++;
      }
    }
    if (merged) for (const it of merged.values()) { it.z += off[it.zid] + 1e-3; items.push(it); nItems++; }
    lastStats.items = nItems;
  }

  function vxFrame(P, v) {
    if (!v.model || isDef) return null;
    const g = getGeo();
    const C = camera();
    lastStats.faces = 0;
    const ov = !!(v.overlay.deformed || v.overlay.modal || v.overlay.buckling);
    const extruding = opts.extrude && !ov;
    return {
      grid: opts.show.grid,
      hideHulls: !opts.show.slab || g.F.active || (extruding && opts.extrudeShells && g.zones.length > 0),
      shellAlpha: 1 - opts.shellTransp,
      supports: opts.show.support,
      springs: opts.show.spring,
      supportPt: p => opts.show.support && g.ptOk(p),
      springPt: p => opts.show.spring && g.ptOk(p),
      extrudeFrames: extruding,
      seg: seg => (g.segVis.has(seg) ? g.segVis.get(seg) : seg),
      // extruded shells are drawn as solids → hide the legacy fill (contour
      // mode keeps the legacy behaviour: contour quads replace shell fills)
      shell: sp => {
        const vis = g.shellVis.has(sp) ? g.shellVis.get(sp) : sp;
        return vis && extruding && opts.extrudeShells ? null : vis;
      },
      link: lk => (g.linkVis.has(lk) ? g.linkVis.get(lk) : true),
      segColor: opts.colorBy !== "type" ? seg => g.colorOf(seg.kind, seg.section,
        ((g.model.sections || {})[seg.section] || {}).material) : null,
      items: (items, P2, flags) => { try { pushSolids(items, C, flags || {}); } catch (e) { lastError = String(e && e.stack || e); throw e; } },
      draw: (ctx, it) => { try { drawItem(ctx, it); } catch (e) { lastError = String(e && e.stack || e); throw e; } },
      post: (ctx) => { try { drawAnnotations(ctx, C, g); } catch (e) { lastError = String(e && e.stack || e); throw e; } },
    };
    function drawItem(ctx, it) {
        if (it.vxl) {
          ctx.strokeStyle = vxShades(it.css)[11];
          ctx.lineWidth = it.w; ctx.lineCap = "butt";
          ctx.beginPath(); ctx.moveTo(it.a[0], it.a[1]); ctx.lineTo(it.b[0], it.b[1]); ctx.stroke();
          lastStats.faces++;
        } else if (it.vxm) lastStats.faces += drawPrism(ctx, C, it.vxm, it.k0, it.k1, it.css, opts.edges && it.ed, 1);
        else if (it.vxp) { drawPlate(ctx, C, it); lastStats.faces++; }
        else if (it.vxpm) { drawSlabMerged(ctx, C, it); lastStats.faces++; }
    }
  }
  viewer.vxFrame = vxFrame;

  /* ---------------- deformed / mode shape extrusion ---------------- */
  viewer.vxMemberVisible = rm => {
    if (isDef || !viewer.model) return true;
    const g = getGeo();
    const seg = g.segByUid.get(rm.uid);
    return seg ? g.segVis.get(seg) !== null : true;
  };
  viewer.vxDeformed = (P, dispMap, factor, v) => {
    try { return vxDeformedImpl(P, dispMap, factor, v); } catch (e) { lastError = String(e && e.stack || e); throw e; }
  };
  const vxDeformedImpl = (P, dispMap, factor, v) => {
    if (!opts.extrude || !v.model || !v.results) return false;
    const r = v.results, g = getGeo(), C = camera();
    const animating = !!(v.overlay.modal || v.overlay.buckling);
    const many = (r.members || []).length > 400;
    const idx = animating && many ? [0, 4, 8] : [0, 2, 4, 6, 8];
    const segByUid = g.segByUid;
    const items = [];
    for (const rm of r.members || []) {
      const Pi = r.nodes[rm.ni], Pj = r.nodes[rm.nj];
      const di = dispMap[rm.ni], dj = dispMap[rm.nj];
      if (!Pi || !Pj || !di || !dj) continue;
      const mm = g.byUid.get(rm.uid);
      const seg = segByUid.get(rm.uid);
      if (seg && g.segVis.get(seg) === null) continue;
      const pts = v._deformedPolyline(Pi, Pj, di, dj, factor);
      const secName = (mm && mm.section) || rm.section;
      const pr = g.prof(secName, animating ? "box" : "full");
      const pseudo = { ...(mm || {}), pi: Pi, pj: Pj, angle: mm ? mm.angle : 0, section: secName };
      const axes = vxMemberAxes(pseudo);
      const cp = mm && mm.cardinal_point != null ? +mm.cardinal_point : 10;
      const gg = VX_CP_GRID[cp] || [0, 0];
      const off = [0, 1, 2].map(k => -0.5 * pr.h * gg[0] * axes.y[k] - 0.5 * pr.b * gg[1] * axes.z[k]);
      const stations = idx.map((i, k) => ({ s: k, sy: 1, sz: 1, off }));
      const ms = vxBuildPrism(pr.pts, animating ? [] : pr.holes, pr.convex, stations, axes, s => pts[idx[s]], !!pr.smooth);
      const css = g.colorOf(rm.kind || (mm && mm.kind) || "beam", secName, ((v.model.sections || {})[secName] || {}).material);
      const size = Math.max(pr.b, pr.h);
      for (let k = 0; k < idx.length - 1; k += 2) {
        const k1 = Math.min(k + 2, idx.length - 1);
        const A = ms.A;
        const c = [(A[k * 3] + A[k1 * 3]) / 2, (A[k * 3 + 1] + A[k1 * 3 + 1]) / 2, (A[k * 3 + 2] + A[k1 * 3 + 2]) / 2];
        const z = (c[0] - C.E[0]) * C.F[0] + (c[1] - C.E[1]) * C.F[1] + (c[2] - C.E[2]) * C.F[2];
        if (z < C.near) continue;
        const px = size * C.focal / z;
        if (px < LINE_PX) {
          const a = projPt(C, A[k * 3], A[k * 3 + 1], A[k * 3 + 2]), b = projPt(C, A[k1 * 3], A[k1 * 3 + 1], A[k1 * 3 + 2]);
          if (a && b) items.push({ z, line: true, a, b, w: Math.max(1, px), css });
        } else items.push({ z, ms, k0: k, k1, css });
      }
    }
    items.sort((a, b) => b.z - a.z);
    const ctx = v.ctx;
    for (const it of items) {
      if (it.line) {
        ctx.strokeStyle = vxShades(it.css)[11]; ctx.lineWidth = it.w; ctx.lineCap = "butt";
        ctx.beginPath(); ctx.moveTo(it.a[0], it.a[1]); ctx.lineTo(it.b[0], it.b[1]); ctx.stroke();
      } else drawPrism(ctx, C, it.ms, it.k0, it.k1, it.css, opts.edges && !animating, 1);
    }
    return true;
  };

  /* ---------------- annotations (joints, axes, labels, loads, cut) ---------------- */
  function drawAnnotations(ctx, C, g) {
    const o = opts, show = o.show;
    const segsVis = [];
    for (const [seg, vv] of g.segVis) if (vv) segsVis.push([seg, vv]);
    ctx.save();
    if (show.joint) {
      ctx.fillStyle = "rgba(230, 236, 244, 0.9)";
      ctx.beginPath();
      for (const p of g.joints) {
        const q = projPt(C, p[0], p[1], p[2]);
        if (q) { ctx.moveTo(q[0] + 2.2, q[1]); ctx.arc(q[0], q[1], 2.2, 0, 2 * Math.PI); }
      }
      ctx.fill();
    }
    if (show.axes) {
      const len = Math.min(Math.max((viewer.radius || 10) * 0.035, 0.25), 1.5);
      const cols = ["#e66767", "#e8edf3", "#35b5e5"];
      ctx.lineWidth = 1.4; ctx.font = "700 9px -apple-system, 'Segoe UI', sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      const triad = (c, axs) => {
        const q0 = projPt(C, c[0], c[1], c[2]);
        if (!q0) return;
        axs.forEach((a, i) => {
          const q = projPt(C, c[0] + a[0] * len, c[1] + a[1] * len, c[2] + a[2] * len);
          if (!q) return;
          ctx.strokeStyle = cols[i]; ctx.fillStyle = cols[i];
          ctx.beginPath(); ctx.moveTo(q0[0], q0[1]); ctx.lineTo(q[0], q[1]); ctx.stroke();
          ctx.fillText(String(i + 1), q[0] + (q[0] - q0[0]) * 0.25, q[1] + (q[1] - q0[1]) * 0.25);
        });
      };
      for (const [seg, vv] of segsVis) {
        const mm = g.byUid.get(seg.uid) || { pi: seg.p1, pj: seg.p2, angle: 0 };
        const ax = vxMemberAxes(mm);
        triad(lerp3(vv.p1, vv.p2, 0.5), [ax.x, ax.y, ax.z]);
      }
      for (const [sp, vv] of g.shellVis) {
        if (!vv) continue;
        const fr = vxRegionFrame(sp.corners);
        const c = [0, 1, 2].map(k => vv.corners.reduce((a, p) => a + p[k], 0) / vv.corners.length);
        triad(c, [fr.e1, fr.e2, fr.e3]);
      }
    }
    if (show.label || show.secname) {
      ctx.font = "600 10px -apple-system, 'Segoe UI', sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      const many = segsVis.length > 1500;
      for (const [seg, vv] of segsVis) {
        const c = lerp3(vv.p1, vv.p2, 0.5);
        const q = projPt(C, c[0], c[1], c[2]);
        if (!q || (many && q[2] > (viewer.dist || 30) * 1.1)) continue;
        if (show.label) { ctx.fillStyle = "rgba(225, 232, 240, 0.92)"; ctx.fillText(String(seg.uid), q[0], q[1] - 7); }
        if (show.secname) { ctx.fillStyle = "rgba(245, 190, 60, 0.95)"; ctx.fillText(String(seg.section || ""), q[0], q[1] + (show.label ? 6 : 0)); }
      }
      if (show.label) for (const [sp, vv] of g.shellVis) {
        if (!vv) continue;
        const c = [0, 1, 2].map(k => vv.corners.reduce((a, p) => a + p[k], 0) / vv.corners.length);
        const q = projPt(C, c[0], c[1], c[2]);
        if (q) { ctx.fillStyle = "rgba(160, 200, 240, 0.95)"; ctx.fillText(String(sp.uid), q[0], q[1]); }
      }
    }
    if (show.load) drawLoads(ctx, C, g);
    // cut plane outline (amber, dashed) at the active coordinate
    if (o.cut.on && o.cut.coord != null && viewer._bbox) {
      const [lo, hi] = viewer._bbox, c = +o.cut.coord, pad = 0.6;
      const L0 = lo.map(v => v - pad), H0 = hi.map(v => v + pad);
      const quad = o.cut.axis === "x" ? [[c, L0[1], L0[2]], [c, H0[1], L0[2]], [c, H0[1], H0[2]], [c, L0[1], H0[2]]]
        : o.cut.axis === "y" ? [[L0[0], c, L0[2]], [H0[0], c, L0[2]], [H0[0], c, H0[2]], [L0[0], c, H0[2]]]
          : [[L0[0], L0[1], c], [H0[0], L0[1], c], [H0[0], H0[1], c], [L0[0], H0[1], c]];
      const qs = quad.map(p => projPt(C, p[0], p[1], p[2]));
      if (qs.every(Boolean)) {
        ctx.beginPath(); qs.forEach((q, i) => (i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]))); ctx.closePath();
        ctx.fillStyle = "rgba(229, 165, 10, 0.05)"; ctx.fill();
        ctx.setLineDash([7, 5]); ctx.strokeStyle = "rgba(245, 190, 60, 0.85)"; ctx.lineWidth = 1.3; ctx.stroke(); ctx.setLineDash([]);
      }
    }
    ctx.restore();
  }
  function patternNames(m) { return Object.keys((m && m.patterns) || {}); }
  function drawLoads(ctx, C, g) {
    const m = model();
    const pats = m.patterns || {};
    const pname = opts.loadPattern && pats[opts.loadPattern] ? opts.loadPattern : patternNames(m)[0];
    const pat = pname && pats[pname];
    if (!pat) return;
    const arrows = [];   // {p (head), dir (unit, load direction), val, kind}
    const segOf = new Map();
    for (const [seg, vv] of g.segVis) if (vv) segOf.set(seg.uid, seg);
    const dirVec = (dirn, mm) => {
      if (dirn === "gravity") return [0, 0, -1];
      if (dirn === "global_x") return [1, 0, 0];
      if (dirn === "global_y") return [0, 1, 0];
      if (dirn === "global_z") return [0, 0, 1];
      if (dirn === "local_y" || dirn === "local_2") return vxMemberAxes(mm).y;
      if (dirn === "local_z" || dirn === "local_3") return vxMemberAxes(mm).z;
      if (dirn === "local_x" || dirn === "local_1") return vxMemberAxes(mm).x;
      return [0, 0, -1];
    };
    const mloads = [...(pat.member_loads || []), ...(pat.member_udls || []).map(u => ({ member_uid: u.member_uid, kind: "udl", w: u.w, a: 0, b: 1, direction: "gravity" }))];
    for (const ld of mloads) {
      const seg = segOf.get(ld.member_uid);
      if (!seg || ld.kind === "moment") continue;
      const mm = g.byUid.get(ld.member_uid) || { pi: seg.p1, pj: seg.p2 };
      const d = dirVec(ld.direction || "gravity", mm);
      const a = isFinite(+ld.a) ? +ld.a : 0, b = isFinite(+ld.b) ? +ld.b : 1;
      if (ld.kind === "point") { arrows.push({ p: lerp3(seg.p1, seg.p2, a), d, val: +ld.w || 0, unit: "force" }); continue; }
      const w1 = +ld.w || 0, w2 = ld.kind === "trapezoid" ? (+ld.w2 || 0) : w1;
      const nA = 5;
      const grp = [];
      for (let k = 0; k <= nA; k++) {
        const t = a + (b - a) * k / nA;
        grp.push({ p: lerp3(seg.p1, seg.p2, t), d, val: w1 + (w2 - w1) * k / nA, unit: "line_force", tail: true, lbl: k === Math.round(nA / 2) });
      }
      arrows.push({ group: grp });
    }
    for (const nl of pat.nodal_loads || []) {
      const f = [+nl.fx || 0, +nl.fy || 0, +nl.fz || 0], mag = Math.hypot(...f);
      if (mag > 0 && (!g.F.active || g.ptOk(nl.point))) arrows.push({ p: nl.point, d: f.map(v => v / mag), val: mag, unit: "force" });
    }
    for (const al of pat.area_loads || []) {
      const sp = (viewer.shellPolys || []).find(s => s.uid === al.region_uid);
      const vv = sp && g.shellVis.get(sp);
      if (!vv) continue;
      const c = [0, 1, 2].map(k => vv.corners.reduce((s, p) => s + p[k], 0) / vv.corners.length);
      const dn = al.direction === "global_x" ? [1, 0, 0] : al.direction === "global_y" ? [0, 1, 0] : al.direction === "global_z" ? [0, 0, 1] : [0, 0, -1];
      arrows.push({ p: c, d: dn, val: +al.q || 0, unit: "pressure" });
    }
    const flat = arrows.flatMap(a => a.group || [a]);
    const maxBy = {};
    for (const a of flat) maxBy[a.unit] = Math.max(maxBy[a.unit] || 0, Math.abs(a.val));
    const Lref = (viewer.radius || 10) * 0.07;
    ctx.lineWidth = 1.2; ctx.font = "600 9.5px -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    const COL = "rgba(240, 120, 200, 0.95)";
    ctx.strokeStyle = COL; ctx.fillStyle = COL;
    const drawArrow = a => {
      const len = Lref * (0.35 + 0.65 * Math.abs(a.val) / (maxBy[a.unit] || 1));
      const sgn = a.val >= 0 ? 1 : -1;
      const dir = a.d.map(v => v * sgn);
      const tail = [a.p[0] - dir[0] * len, a.p[1] - dir[1] * len, a.p[2] - dir[2] * len];
      const q0 = projPt(C, tail[0], tail[1], tail[2]), q1 = projPt(C, a.p[0], a.p[1], a.p[2]);
      if (!q0 || !q1) return null;
      ctx.beginPath(); ctx.moveTo(q0[0], q0[1]); ctx.lineTo(q1[0], q1[1]);
      const ang = Math.atan2(q1[1] - q0[1], q1[0] - q0[0]), hs = 5;
      ctx.moveTo(q1[0], q1[1]); ctx.lineTo(q1[0] - hs * Math.cos(ang - 0.4), q1[1] - hs * Math.sin(ang - 0.4));
      ctx.moveTo(q1[0], q1[1]); ctx.lineTo(q1[0] - hs * Math.cos(ang + 0.4), q1[1] - hs * Math.sin(ang + 0.4));
      ctx.stroke();
      return q0;
    };
    for (const a of arrows) {
      if (a.group) {
        const tails = a.group.map(drawArrow);
        ctx.beginPath();
        let st = false;
        for (const q of tails) { if (!q) { st = false; continue; } st ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); st = true; }
        ctx.stroke();
        const mid = a.group.find(x => x.lbl), qm = tails[a.group.indexOf(mid)];
        if (qm) ctx.fillText(`${VXU.fmt(mid.unit, mid.val, 1)}`, qm[0], qm[1] - 8);
      } else {
        const q = drawArrow(a);
        if (q) ctx.fillText(`${VXU.fmt(a.unit, a.val, 1)}`, q[0], q[1] - 8);
      }
    }
    ctx.fillStyle = "rgba(240, 120, 200, 0.85)"; ctx.textAlign = "left";
    ctx.fillText(`Loads · ${pname} (${VXU.label("force")}, ${VXU.label("line_force")}, ${VXU.label("pressure")})`, 12, 14);
  }

  /* ================================================================
     Plates (shells)
     ================================================================ */
  function vxPlate(sp, vis, t, tile, col, zcuts = []) {
    const fr = vxRegionFrame(sp.corners);
    let outer = vis.corners.map(fr.to2);
    if (area2(outer) < 0) outer = outer.reverse();
    const holes = (vis.openings || []).map(o => o.map(fr.to2)).filter(h => h.length >= 3).map(ccw);
    const e3 = fr.e3;
    let offT, offB;
    if (sp.kind !== "wall" && Math.abs(e3[2]) > 0.5) {
      const s = e3[2] > 0 ? 1 : -1;     // slab hangs below its plane
      offT = [0, 0, 0]; offB = e3.map(v => -v * s * t);
    } else { offT = e3.map(v => v * t / 2); offB = e3.map(v => -v * t / 2); }
    const nTop = norm3(sub3(offT, offB));
    const to3 = (p, off) => { const q = fr.to3(p[0], p[1]); return [q[0] + off[0], q[1] + off[1], q[2] + off[2]]; };
    const flat = (loop, off) => { const a = new Float64Array(loop.length * 3); loop.forEach((p, i) => { const q = to3(p, off); a[i * 3] = q[0]; a[i * 3 + 1] = q[1]; a[i * 3 + 2] = q[2]; }); return a; };
    let u0 = Infinity, u1 = -Infinity, v0 = Infinity, v1 = -Infinity;
    for (const [u, v] of outer) { u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v); }
    const nu = Math.min(24, Math.max(1, Math.ceil((u1 - u0) / tile))), nv = Math.min(24, Math.max(1, Math.ceil((v1 - v0) / tile)));
    const ub = [], vb = [];
    for (let i = 0; i <= nu; i++) ub.push(u0 + (u1 - u0) * i / nu);
    for (let j = 0; j <= nv; j++) vb.push(v0 + (v1 - v0) * j / nv);
    // vertical plates (in-plane v = ±z): extra tile rows at the slab-zone boundaries
    if (Math.abs(fr.e2[2]) > 0.999) {
      const zo = fr.to3(0, 0)[2];
      for (const zc of zcuts) { const v = (zc - zo) / fr.e2[2]; if (v > v0 + 1e-6 && v < v1 - 1e-6) vb.push(v); }
      vb.sort((a, b) => a - b);
    }
    const out = [];
    const behavior = sp.behavior;
    for (let i = 0; i < ub.length - 1; i++) for (let j = 0; j < vb.length - 1; j++) {
      const x0 = ub[i], x1 = ub[i + 1];
      const y0 = vb[j], y1 = vb[j + 1];
      if (y1 - y0 < 1e-9) continue;
      const co = vxClipToBox2(outer, x0, y0, x1, y1);
      if (co.length < 3 || Math.abs(area2(co)) < 1e-9) continue;
      const loops = [co, ...holes.map(h => vxClipToBox2(h, x0, y0, x1, y1)).filter(h => h.length >= 3)];
      let cu = 0, cv = 0;
      for (const [u, v] of co) { cu += u; cv += v; }
      cu /= co.length; cv /= co.length;
      const ct = to3([cu, cv], offT), cb = to3([cu, cv], offB);
      out.push({ type: "tile", top: loops.map(l => flat(l, offT)), bot: loops.map(l => flat(l, offB)),
        n: nTop, ct, zmid: (ct[2] + cb[2]) / 2, col, behavior, uid: sp.uid, horiz: Math.abs(e3[2]) > 0.999 && sp.kind !== "wall" });
    }
    // side faces (outer: outward; holes: into the hole)
    const sides = (loop, isHole) => {
      for (let i = 0; i < loop.length; i++) {
        const a = loop[i], b = loop[(i + 1) % loop.length];
        const du = b[0] - a[0], dv = b[1] - a[1], len = Math.hypot(du, dv);
        if (len < 1e-9) continue;
        let n2 = [dv / len, -du / len];
        if (isHole) n2 = [-n2[0], -n2[1]];
        const n = norm3([0, 1, 2].map(k => n2[0] * fr.e1[k] + n2[1] * fr.e2[k]));
        const pieces = Math.max(1, Math.ceil(len / tile));
        const fs = [];
        for (let k = 0; k <= pieces; k++) fs.push(k / pieces);
        const za = to3(a, [0, 0, 0])[2], zb = to3(b, [0, 0, 0])[2];
        if (Math.abs(zb - za) > 1e-9) for (const zc of zcuts) { const f = (zc - za) / (zb - za); if (f > 1e-6 && f < 1 - 1e-6) fs.push(f); }
        fs.sort((x, y) => x - y);
        for (let k = 0; k < fs.length - 1; k++) {
          if (fs[k + 1] - fs[k] < 1e-9) continue;
          const pa = [a[0] + du * fs[k], a[1] + dv * fs[k]], pb = [a[0] + du * fs[k + 1], a[1] + dv * fs[k + 1]];
          const q = new Float64Array([...to3(pa, offT), ...to3(pb, offT), ...to3(pb, offB), ...to3(pa, offB)]);
          const c = [(q[0] + q[3] + q[6] + q[9]) / 4, (q[1] + q[4] + q[7] + q[10]) / 4, (q[2] + q[5] + q[8] + q[11]) / 4];
          out.push({ type: "side", q, n, c, col, behavior });
        }
      }
    };
    sides(outer, false);
    holes.forEach(h => sides(h, true));
    return out;
  }

  /* ================================================================
     Display Options dialog
     ================================================================ */
  function openDisplayOptions() {
    const m = model() || (S().model);
    const draft = vxClone(opts);
    const body = document.createElement("div");
    body.className = "vx-dlg";
    const el = (tag, attrs = {}, kids = []) => {
      const e = document.createElement(tag);
      for (const [k, v] of Object.entries(attrs)) {
        if (k === "text") e.textContent = v; else if (k === "class") e.className = v; else e.setAttribute(k, v);
      }
      for (const c of kids) if (c) e.append(c);
      return e;
    };
    const chk = (label, get, set, id) => {
      const cb = el("input", { type: "checkbox", id });
      cb.checked = !!get();
      cb.addEventListener("change", () => { set(cb.checked); sync(); });
      return el("label", { class: "vx-chk" }, [cb, document.createTextNode(" " + label)]);
    };
    const sel = (options, val, onChange, id) => {
      const s = el("select", { class: "mini-select", id });
      for (const [v, l] of options) { const o = el("option", { value: v, text: l }); s.appendChild(o); }
      s.value = val;
      s.addEventListener("change", () => { onChange(s.value); sync(); });
      return s;
    };
    const group = (title, kids) => el("fieldset", { class: "vx-group" }, [el("legend", { text: title }), ...kids]);
    // objects
    const objGrid = el("div", { class: "vx-grid" });
    for (const [k, l] of VX_SHOW_KEYS) objGrid.appendChild(chk(l, () => draft.show[k], v => { draft.show[k] = v; }, "vxShow_" + k));
    const pats = patternNames(m);
    const loadSel = sel([["", "(first pattern)"], ...pats.map(p => [p, p])], draft.loadPattern, v => { draft.loadPattern = v; }, "vxLoadPat");
    const objAll = vxBtn("All", "btn-small", () => { VX_SHOW_KEYS.forEach(([k]) => { draft.show[k] = true; }); refreshChecks(); sync(); });
    const objNone = vxBtn("None", "btn-small", () => { VX_SHOW_KEYS.forEach(([k]) => { draft.show[k] = false; }); refreshChecks(); sync(); });
    body.appendChild(group("Objects & annotations", [objGrid,
      el("div", { class: "vx-row" }, [el("span", { class: "vx-lbl", text: "Load pattern" }), loadSel, el("span", { class: "vx-sp" }), objAll, objNone])]));
    // view
    const transp = el("input", { type: "range", min: "0", max: "90", step: "5", id: "vxTransp" });
    transp.value = String(Math.round(draft.shellTransp * 100));
    const transpVal = el("span", { class: "mini-value", text: transp.value + "%" });
    transp.addEventListener("input", () => { draft.shellTransp = +transp.value / 100; transpVal.textContent = transp.value + "%"; sync(); });
    body.appendChild(group("View", [
      el("div", { class: "vx-grid" }, [
        chk("Extrude frames", () => draft.extrude, v => { draft.extrude = v; }, "vxExtrude"),
        chk("Extrude shells (thickness)", () => draft.extrudeShells, v => { draft.extrudeShells = v; }, "vxExtrudeShells"),
        chk("Edge outlines", () => draft.edges, v => { draft.edges = v; }, "vxEdges"),
      ]),
      el("div", { class: "vx-row" }, [el("span", { class: "vx-lbl", text: "Colour by" }),
        sel(VX_COLOR_BY, draft.colorBy, v => { draft.colorBy = v; }, "vxColorBy")]),
      el("div", { class: "vx-row" }, [el("span", { class: "vx-lbl", text: "Shell transparency" }), transp, transpVal]),
    ]));
    // filter
    const stories = ((m && m.stories) || []).slice().sort((a, b) => a.elevation - b.elevation);
    const stOpts = [["", "(all)"], ...stories.map(s => [s.name, s.name])];
    const groups = Object.keys((m && m.groups) || {});
    body.appendChild(group("Show only", [
      el("div", { class: "vx-row" }, [el("span", { class: "vx-lbl", text: "Stories from" }),
        sel(stOpts, draft.storyFrom, v => { draft.storyFrom = v; }, "vxStoryFrom"),
        el("span", { class: "vx-lbl vx-lbl-s", text: "to" }),
        sel(stOpts, draft.storyTo, v => { draft.storyTo = v; }, "vxStoryTo")]),
      el("div", { class: "vx-row" }, [el("span", { class: "vx-lbl", text: "Group" }),
        sel([["", "(all objects)"], ...groups.map(gn => [gn, gn])], draft.group, v => { draft.group = v; }, "vxGroup")]),
      chk("Selected objects only", () => draft.selOnly, v => { draft.selOnly = v; }, "vxSelOnly"),
    ]));
    // cut plane
    const coordIn = el("input", { type: "number", step: "any", id: "vxCutCoord", class: "vx-num" });
    const bboxMid = ax => { const b = viewer._bbox; if (!b) return 0; const i = { x: 0, y: 1, z: 2 }[ax]; return (b[0][i] + b[1][i]) / 2; };
    let coordShown = "";
    const setCoordField = () => {
      const si = draft.cut.coord == null ? bboxMid(draft.cut.axis) : draft.cut.coord;
      coordShown = VXU.inputValue("length", si); coordIn.value = coordShown;
    };
    coordIn.addEventListener("change", () => {
      if (coordIn.value === coordShown) return;
      const v = VXU.parse("length", coordIn.value);
      if (isFinite(v)) { draft.cut.coord = v; coordShown = coordIn.value; }
      sync();
    });
    body.appendChild(group("Section cut plane", [
      chk("Clip the model at a plane", () => draft.cut.on, v => { draft.cut.on = v; if (v && draft.cut.coord == null) draft.cut.coord = bboxMid(draft.cut.axis); }, "vxCutOn"),
      el("div", { class: "vx-row" }, [el("span", { class: "vx-lbl", text: "Normal axis" }),
        sel([["x", "X"], ["y", "Y"], ["z", "Z"]], draft.cut.axis, v => { draft.cut.axis = v; draft.cut.coord = bboxMid(v); setCoordField(); }, "vxCutAxis"),
        el("span", { class: "vx-lbl vx-lbl-s", text: "at" }), coordIn, el("span", { class: "muted", text: VXU.label("length") }),
        el("span", { class: "vx-lbl vx-lbl-s", text: "keep" }),
        sel([["below", "≤ plane"], ["above", "≥ plane"]], draft.cut.keep, v => { draft.cut.keep = v; }, "vxCutKeep")]),
      ...(m && (m.section_cuts || []).length ? [el("div", { class: "vx-row" }, [el("span", { class: "vx-lbl", text: "From section cut" }),
        sel([["", "—"], ...(m.section_cuts || []).map((c, i) => [String(i), c.name])], "", v => {
          const c = (m.section_cuts || [])[+v];
          if (c) { draft.cut.on = true; draft.cut.axis = ["x", "y", "z"].includes(c.axis) ? c.axis : "z"; draft.cut.coord = +c.coord; setCoordField(); refreshChecks(); }
        }, "vxCutPreset")])] : []),
    ]));
    setCoordField();
    const refreshChecks = () => {
      for (const [k] of VX_SHOW_KEYS) { const c = body.querySelector("#vxShow_" + k); if (c) c.checked = draft.show[k]; }
      const co = body.querySelector("#vxCutOn"); if (co) co.checked = draft.cut.on;
      const ca = body.querySelector("#vxCutAxis"); if (ca) ca.value = draft.cut.axis;
    };
    const sync = () => {
      fb.note.textContent = vxIsDefault(draft) ? "Default display" : (draft.extrude ? "Extruded view" : "Line view") +
        (draft.storyFrom || draft.storyTo || draft.group || draft.selOnly || draft.cut.on ? " · filtered" : "");
    };
    const fb = vxFootBar("", [
      vxBtn("Reset", "", () => { const d = vxDefaults(); Object.assign(draft, d); dlg.close(); setOpts(d); openDisplayOptions(); }),
      vxBtn("Cancel", "", () => dlg.close()),
      vxBtn("Apply", "", () => setOpts(vxClone(draft))),
      vxBtn("OK", "btn-run", () => { setOpts(vxClone(draft)); dlg.close(); }),
    ]);
    const btns = fb.wrap.querySelectorAll(".btn");
    btns[0].id = "vxReset"; btns[1].id = "vxCancel"; btns[2].id = "vxApply"; btns[3].id = "vxOk";
    const dlg = vxDialog("vxDisplayModal", { title: "Set Display Options", body, foot: fb.wrap, wide: true });
    sync();
    return dlg;
  }

  /* ================================================================
     Quick toggles (toolbar chips + cut slider)
     ================================================================ */
  const chipBar = document.getElementById("viewerToolbar");
  const anchor = document.getElementById("chipLabels");
  const mkChip = (id, label, title, fn) => {
    const b = document.createElement("button");
    b.className = "chip vx-chip"; b.id = id; b.textContent = label; b.title = title;
    b.addEventListener("click", fn);
    return b;
  };
  const chipExtrude = mkChip("vxChipExtrude", "Extrude", "Extruded view — frames with their section shape, shells with thickness", () => toggleExtrude());
  const chipSel = mkChip("vxChipSelOnly", "Sel. only", "Show only the selected objects", () => toggleSelOnly());
  const chipCut = mkChip("vxChipCut", "Cut", "Section-cut-plane view: clip the model at a plane", () => toggleCut());
  const chipOpts = mkChip("vxChipOpts", "Display…", "View → Set Display Options…", () => openDisplayOptions());
  const cutGroup = document.createElement("div");
  cutGroup.className = "toolbar-group vx-cutgroup"; cutGroup.id = "vxCutGroup"; cutGroup.hidden = true;
  const cutAxis = document.createElement("select");
  cutAxis.className = "mini-select"; cutAxis.id = "vxCutAxisQ"; cutAxis.title = "Cut-plane normal axis";
  for (const a of ["x", "y", "z"]) { const o = document.createElement("option"); o.value = a; o.textContent = a.toUpperCase(); cutAxis.appendChild(o); }
  const cutRange = document.createElement("input");
  cutRange.type = "range"; cutRange.id = "vxCutRange"; cutRange.min = "0"; cutRange.max = "1000"; cutRange.step = "1"; cutRange.title = "Cut-plane position";
  const cutVal = document.createElement("span");
  cutVal.className = "mini-value"; cutVal.id = "vxCutVal";
  const cutFlip = document.createElement("button");
  cutFlip.className = "chip"; cutFlip.id = "vxCutFlip"; cutFlip.textContent = "⇅"; cutFlip.title = "Keep the other side of the plane";
  cutGroup.append(cutAxis, cutRange, cutVal, cutFlip);
  const axIdx = a => ({ x: 0, y: 1, z: 2 }[a]);
  const cutSpan = () => {
    const b = viewer._bbox; const i = axIdx(opts.cut.axis);
    return b ? [b[0][i] - 0.01, b[1][i] + 0.01] : [0, 10];
  };
  cutAxis.addEventListener("change", () => { const [a, b] = [(viewer._bbox || [[0, 0, 0], [10, 10, 10]])[0][axIdx(cutAxis.value)], (viewer._bbox || [[0, 0, 0], [10, 10, 10]])[1][axIdx(cutAxis.value)]]; setOpts({ cut: { axis: cutAxis.value, coord: (a + b) / 2 } }); });
  cutRange.addEventListener("input", () => { const [a, b] = cutSpan(); setOpts({ cut: { coord: a + (b - a) * (+cutRange.value) / 1000 } }); });
  cutFlip.addEventListener("click", () => setOpts({ cut: { keep: opts.cut.keep === "below" ? "above" : "below" } }));
  if (chipBar && anchor) {
    anchor.after(chipExtrude, chipSel, chipCut, chipOpts);
    chipOpts.after(cutGroup);
  }
  function syncChips() {
    chipExtrude.classList.toggle("is-on", opts.extrude);
    chipSel.classList.toggle("is-on", opts.selOnly);
    chipCut.classList.toggle("is-on", opts.cut.on);
    chipOpts.classList.toggle("is-on", !vxIsDefault({ ...opts, extrude: false, selOnly: false, cut: vxDefaults().cut }));
    cutGroup.hidden = !opts.cut.on;
    cutAxis.value = opts.cut.axis;
    if (opts.cut.on && opts.cut.coord != null) {
      const [a, b] = cutSpan();
      cutRange.value = String(Math.round(Math.min(1, Math.max(0, (opts.cut.coord - a) / ((b - a) || 1))) * 1000));
      cutVal.textContent = `${opts.cut.axis.toUpperCase()} ${opts.cut.keep === "below" ? "≤" : "≥"} ${VXU.fmt("length", opts.cut.coord, 2)} ${VXU.label("length")}`;
    }
    selWatch();
  }
  document.addEventListener("sky:units-changed", () => syncChips());
  // selection-only view follows selection edits (store.selection has no event)
  let selTimer = null, lastSel = "";
  function selWatch() {
    if (opts.selOnly && !selTimer) {
      selTimer = setInterval(() => { const k = selKey(); if (k !== lastSel) { lastSel = k; redraw(); } }, 250);
    } else if (!opts.selOnly && selTimer) { clearInterval(selTimer); selTimer = null; }
  }

  function toggleExtrude(on) { return setOpts({ extrude: on == null ? !opts.extrude : !!on }); }
  function toggleSelOnly(on) {
    const v = on == null ? !opts.selOnly : !!on;
    if (v && !(S().selection || []).length && sky.toast) sky.toast("Show selection only", "Nothing is selected — select objects (V) first.");
    lastSel = selKey();
    return setOpts({ selOnly: v });
  }
  function toggleCut(on) {
    const v = on == null ? !opts.cut.on : !!on;
    const patch = { on: v };
    if (v && opts.cut.coord == null) { const [a, b] = cutSpan(); patch.coord = (a + b) / 2; }
    return setOpts({ cut: patch });
  }

  /* ---------------- benchmark (FPS while rotating) ---------------- */
  function benchmark(frames = 120, dyaw = 0.02, flush = true, moving = true) {
    const t = [];
    forceMoving = moving;
    for (let i = 0; i < frames; i++) {
      viewer.yaw += dyaw;
      const t0 = performance.now();
      viewer._render();
      if (flush) viewer.ctx.getImageData(0, 0, 1, 1);   // flush the raster pipeline → honest frame time
      t.push(performance.now() - t0);
    }
    forceMoving = false;
    viewer._dirty = true;
    t.sort((a, b) => a - b);
    const mean = t.reduce((a, b) => a + b, 0) / t.length;
    return { frames, mean_ms: +mean.toFixed(2), median_ms: +t[t.length >> 1].toFixed(2), p95_ms: +t[Math.floor(t.length * 0.95)].toFixed(2),
      fps_est: +(1000 / Math.max(mean, 1000 / 240)).toFixed(1), items: lastStats.items, faces: lastStats.faces,
      members: (viewer.segs || []).length };
  }

  /* ---------------- styles ---------------- */
  if (!document.getElementById("vxStyles")) {
    const st = document.createElement("style");
    st.id = "vxStyles";
    st.textContent = `
.vx-dlg { display: flex; flex-direction: column; gap: 10px; }
.vx-group { border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px 10px; margin: 0; }
.vx-group legend { font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: var(--text-2, #8b98a8); padding: 0 4px; }
.vx-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 4px 12px; }
.vx-chk { display: flex; align-items: center; gap: 6px; font-size: 12.5px; cursor: pointer; white-space: nowrap; }
.vx-row { display: flex; align-items: center; gap: 8px; margin-top: 7px; flex-wrap: wrap; font-size: 12.5px; }
.vx-lbl { min-width: 118px; color: var(--text-2, #8b98a8); }
.vx-lbl-s { min-width: 0; }
.vx-sp { flex: 1; }
.vx-dlg input.vx-num { width: 96px; flex: 0 0 96px; }
.vx-dlg .vx-row select { width: auto; }
.vx-cutgroup input[type=range] { width: 120px; }
.vx-chip { white-space: nowrap; }`;
    document.head.appendChild(st);
  }

  syncChips();
  redraw();
  sky.vx = {
    opts: () => vxClone(opts), set: setOpts, reset: () => setOpts(vxDefaults()),
    openDisplayOptions, close: () => vxCloseDialog("vxDisplayModal"),
    toggleExtrude, toggleSelOnly, toggleCut, benchmark,
    stats: () => ({ ...lastStats }), lastError: () => lastError,
    isExtrude: () => opts.extrude, isSelOnly: () => opts.selOnly, isCut: () => opts.cut.on,
    profileOf: (name, lod = "full") => { const m = model(); return m ? vxProfileOf(m, name, (m.sections || {})[name], lod) : null; },
  };
}
