/* SkyFrame — polygon shell geometry (pure math, no DOM).

   Mirrors skyframe.core.polymesh (CONTRACT "Polygon shells and auto mesh"):
     * Newell normal / exact vector-shoelace area of a 3D polygon
     * region_frame(): e3 = unit Newell normal; horizontal → e1 = +X,
       e2 = ±Y; vertical → e1 = (-n_y, n_x, 0), e2 = +Z; otherwise
       e1 = Z × e3, e2 = e3 × e1
     * opening outlines: Opening.polygon when set; 4-corner regions map
       u/v bilinearly; other regions map u/v over the polygon's local
       bounding box (ShellRegion.map_uv / polymesh.polygon_map_uv)
     * 2D validity: >= 3 points, no zero-length edge, non-zero area, no
       crossing or touching edges (self-intersection rejected).
   Used by the plan / elevation / 3D renderers, polydraw.js and mock_poly.js. */

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = a => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

/** Newell normal (length = 2 × area) of a planar 3D polygon. */
export function newell(pts) {
  let nx = 0, ny = 0, nz = 0;
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const a = pts[i], b = pts[(i + 1) % n];
    nx += (a[1] - b[1]) * (a[2] + b[2]);
    ny += (a[2] - b[2]) * (a[0] + b[0]);
    nz += (a[0] - b[0]) * (a[1] + b[1]);
  }
  return [nx, ny, nz];
}

/** Exact area of a planar 3D polygon. */
export function polyArea3(pts) {
  if (!pts || pts.length < 3) return 0;
  const n = newell(pts);
  return Math.hypot(n[0], n[1], n[2]) / 2;
}

/** In-plane frame of a region (polymesh.region_frame). */
export function regionFrame(corners) {
  const n = unit(newell(corners));
  let e1, e2, e3;
  if (Math.abs(n[2]) > 1 - 1e-9) {
    const s = n[2] > 0 ? 1 : -1;
    e3 = [0, 0, s]; e1 = [1, 0, 0]; e2 = [0, s, 0];
  } else if (Math.abs(n[2]) < 1e-9) {
    const h = Math.hypot(n[0], n[1]) || 1;
    e3 = [n[0] / h, n[1] / h, 0]; e1 = [-e3[1], e3[0], 0]; e2 = [0, 0, 1];
  } else {
    e3 = n; e1 = unit(cross([0, 0, 1], e3)); e2 = cross(e3, e1);
  }
  const w0 = corners.reduce((s, p) => s + dot(p, e3), 0) / corners.length;
  return {
    e1, e2, e3, w0,
    to2: p => [dot(p, e1), dot(p, e2)],
    to3: (u, v) => [0, 1, 2].map(k => u * e1[k] + v * e2[k] + w0 * e3[k]),
  };
}

/** True for the legacy structured-quad path: 4 corners, no polygon opening. */
export function isLegacyQuad(sh) {
  return !!sh && (sh.corners || []).length === 4 &&
    !(sh.openings || []).some(o => o && Array.isArray(o.polygon) && o.polygon.length >= 3);
}

/** Bilinear point on a 4-corner region. */
function bilin(c, u, v) {
  return [0, 1, 2].map(k => (1 - u) * (1 - v) * c[0][k] + u * (1 - v) * c[1][k] +
    u * v * c[2][k] + (1 - u) * v * c[3][k]);
}

/** Region point at parametric (u, v) — ShellRegion.map_uv. */
export function mapUV(sh, u, v) {
  const c = sh.corners;
  if (c.length === 4) return bilin(c, u, v);
  const fr = regionFrame(c);
  const p2 = c.map(fr.to2);
  const us = p2.map(p => p[0]), vs = p2.map(p => p[1]);
  const u0 = Math.min(...us), u1 = Math.max(...us), v0 = Math.min(...vs), v1 = Math.max(...vs);
  return fr.to3(u0 + u * (u1 - u0), v0 + v * (v1 - v0));
}

/** 3D outline of one opening of a region. */
export function openingPolygon3(sh, o) {
  if (o && Array.isArray(o.polygon) && o.polygon.length >= 3) return o.polygon.map(p => [+p[0], +p[1], +p[2]]);
  return [[o.u0, o.v0], [o.u1, o.v0], [o.u1, o.v1], [o.u0, o.v1]].map(([u, v]) => mapUV(sh, u, v));
}

/** {gross, openings, net} areas (m²). */
export function shellAreas(sh) {
  const gross = polyArea3(sh.corners || []);
  let op = 0;
  for (const o of (sh.openings || [])) op += polyArea3(openingPolygon3(sh, o));
  return { gross, openings: op, net: gross - op };
}

/** Plan footprint of a (vertical) wall: the two extreme corners along its
    horizontal direction. Legacy walls keep corners[0] → corners[1]. */
export function wallPlanSegment(corners) {
  if (!corners || corners.length < 2) return [[0, 0, 0], [0, 0, 0]];
  if (corners.length === 4 && Math.hypot(corners[1][0] - corners[0][0], corners[1][1] - corners[0][1]) > 1e-9)
    return [corners[0], corners[1]];
  let best = [corners[0], corners[1]], bd = -1;
  for (let i = 0; i < corners.length; i++)
    for (let j = i + 1; j < corners.length; j++) {
      const d = Math.hypot(corners[j][0] - corners[i][0], corners[j][1] - corners[i][1]);
      if (d > bd) { bd = d; best = [corners[i], corners[j]]; }
    }
  return best;
}

/* ------------------------------------------------ 2D helpers */
export function signedArea2(p) {
  let s = 0;
  for (let i = 0, n = p.length; i < n; i++) {
    const a = p[i], b = p[(i + 1) % n];
    s += a[0] * b[1] - b[0] * a[1];
  }
  return s / 2;
}

export function pointInPoly2(x, y, pts) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const xi = pts[i][0], yi = pts[i][1], xj = pts[j][0], yj = pts[j][1];
    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}

export function distToSeg2(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1, l2 = dx * dx + dy * dy;
  let t = l2 ? ((px - x1) * dx + (py - y1) * dy) / l2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

/** Closest point parameter t∈[0,1] on a segment. */
export function segParam2(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1, l2 = dx * dx + dy * dy;
  return l2 ? Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / l2)) : 0;
}

const orient = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);

/** Segments ab and cd intersect or touch (tolerance eps, scaled). */
export function segsTouch2(a, b, c, d, eps = 1e-9) {
  const d1 = orient(c, d, a), d2 = orient(c, d, b), d3 = orient(a, b, c), d4 = orient(a, b, d);
  if (((d1 > eps && d2 < -eps) || (d1 < -eps && d2 > eps)) &&
      ((d3 > eps && d4 < -eps) || (d3 < -eps && d4 > eps))) return true;
  const on = (p, q, r, o) => Math.abs(o) <= eps &&
    Math.min(p[0], q[0]) - 1e-9 <= r[0] && r[0] <= Math.max(p[0], q[0]) + 1e-9 &&
    Math.min(p[1], q[1]) - 1e-9 <= r[1] && r[1] <= Math.max(p[1], q[1]) + 1e-9;
  return on(c, d, a, d1) || on(c, d, b, d2) || on(a, b, c, d3) || on(a, b, d, d4);
}

/** First validity problem of a 2D polygon ("" when valid). */
export function polygonProblem2(p) {
  if (!p || p.length < 3) return "a polygon needs at least 3 vertices";
  const n = p.length;
  let span = 0;
  for (const q of p) span = Math.max(span, Math.abs(q[0]), Math.abs(q[1]));
  const eps = 1e-9 * Math.max(1, span * span);
  for (let i = 0; i < n; i++) {
    const a = p[i], b = p[(i + 1) % n];
    if (Math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-6) return "zero-length edge (repeated vertex)";
  }
  for (let i = 0; i < n; i++) {
    const a = p[i], b = p[(i + 1) % n];
    for (let j = i + 1; j < n; j++) {
      if (j === i + 1 || (i === 0 && j === n - 1)) continue;   // adjacent edges share a vertex
      const c = p[j], d = p[(j + 1) % n];
      if (segsTouch2(a, b, c, d, eps)) return "self-intersecting polygon (edges cross or touch)";
    }
  }
  if (Math.abs(signedArea2(p)) < 1e-9) return "zero area";
  // adjacent edges folding back onto each other
  for (let i = 0; i < n; i++) {
    const a = p[(i + n - 1) % n], b = p[i], c = p[(i + 1) % n];
    const cr = orient(a, b, c);
    const dt = (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]);
    if (Math.abs(cr) <= eps && dt < 0) return "self-intersecting polygon (edge folds back)";
  }
  return "";
}

/** Inner polygon strictly inside outer (all vertices inside, no crossings). */
export function polyInside2(inner, outer) {
  if (!inner.every(q => pointInPoly2(q[0], q[1], outer))) return false;
  for (let i = 0; i < inner.length; i++)
    for (let j = 0; j < outer.length; j++)
      if (segsTouch2(inner[i], inner[(i + 1) % inner.length], outer[j], outer[(j + 1) % outer.length])) return false;
  return true;
}

/** Two polygons overlap (any crossing, or one contains the other). */
export function polysOverlap2(a, b) {
  for (let i = 0; i < a.length; i++)
    for (let j = 0; j < b.length; j++)
      if (segsTouch2(a[i], a[(i + 1) % a.length], b[j], b[(j + 1) % b.length])) return true;
  return pointInPoly2(a[0][0], a[0][1], b) || pointInPoly2(b[0][0], b[0][1], a);
}

/** Sutherland–Hodgman clip of a (possibly concave) polygon by an axis box. */
export function clipToBox2(poly, x0, y0, x1, y1) {
  let out = poly;
  const edges = [
    [p => p[0] >= x0, (a, b) => { const t = (x0 - a[0]) / (b[0] - a[0]); return [x0, a[1] + t * (b[1] - a[1])]; }],
    [p => p[0] <= x1, (a, b) => { const t = (x1 - a[0]) / (b[0] - a[0]); return [x1, a[1] + t * (b[1] - a[1])]; }],
    [p => p[1] >= y0, (a, b) => { const t = (y0 - a[1]) / (b[1] - a[1]); return [a[0] + t * (b[0] - a[0]), y0]; }],
    [p => p[1] <= y1, (a, b) => { const t = (y1 - a[1]) / (b[1] - a[1]); return [a[0] + t * (b[0] - a[0]), y1]; }],
  ];
  for (const [inside, isect] of edges) {
    const inp = out; out = [];
    if (!inp.length) break;
    for (let i = 0; i < inp.length; i++) {
      const cur = inp[i], prev = inp[(i + inp.length - 1) % inp.length];
      const ci = inside(cur), pi = inside(prev);
      if (ci) { if (!pi) out.push(isect(prev, cur)); out.push(cur); }
      else if (pi) out.push(isect(prev, cur));
    }
  }
  // drop consecutive duplicates
  const res = [];
  for (const p of out) {
    const q = res[res.length - 1];
    if (!q || Math.hypot(p[0] - q[0], p[1] - q[1]) > 1e-9) res.push(p);
  }
  if (res.length > 1 && Math.hypot(res[0][0] - res[res.length - 1][0], res[0][1] - res[res.length - 1][1]) < 1e-9) res.pop();
  return res;
}

/** Local axes (x, y, z) of a frame member — engine convention
    (skyframe.engine _local_axes / buckling._local_axes), with angle. */
export function memberAxes(mm) {
  const d = sub(mm.pj, mm.pi);
  const x = unit(d);
  const vertical = Math.hypot(d[0], d[1]) < 1e-9;
  const vecxz = vertical ? [1, 0, 0] : unit([x[1], -x[0], 0]);
  let y = unit(cross(vecxz, x));
  let z = cross(x, y);
  const ang = +mm.angle || 0;
  if (Math.abs(ang) > 1e-12) {
    const a = ang * Math.PI / 180, ca = Math.cos(a), sa = Math.sin(a);
    const y2 = [0, 1, 2].map(k => ca * y[k] + sa * z[k]);
    const z2 = [0, 1, 2].map(k => ca * z[k] - sa * y[k]);
    y = unit(y2); z = unit(z2);
  }
  return { x, y, z };
}

/* ETABS cardinal points: (row, col) → y = row·h/2, z = col·b/2 */
export const CP_GRID = { 1: [-1, -1], 2: [-1, 0], 3: [-1, 1], 4: [0, -1], 5: [0, 0], 6: [0, 1],
  7: [1, -1], 8: [1, 0], 9: [1, 1], 10: [0, 0], 11: [0, 0] };
export const CP_NAMES = { 1: "Bottom left", 2: "Bottom center", 3: "Bottom right",
  4: "Middle left", 5: "Middle center", 6: "Middle right", 7: "Top left", 8: "Top center",
  9: "Top right", 10: "Centroid", 11: "Shear center" };

/** Global rigid offsets (e_i, e_j) from joints to the analytical axis
    (insertion.member_joint_offsets), or null when on the reference line. */
export function memberOffsets(model, mm) {
  const cp = mm.cardinal_point == null ? 10 : +mm.cardinal_point;
  const jo = mm.joint_offsets;
  if (cp === 10 && !jo) return null;
  const { x, y, z } = memberAxes(mm);
  let ei = [0, 0, 0], ej = [0, 0, 0];
  if (cp !== 10 && !mm.no_transform_stiffness) {
    const sec = (model.sections || {})[mm.section] || {};
    const g = CP_GRID[cp] || [0, 0];
    const cy = 0.5 * (+sec.h || 0) * g[0], cz = 0.5 * (+sec.b || 0) * g[1];
    const c = [0, 1, 2].map(k => -cy * y[k] - cz * z[k]);
    ei = c.slice(); ej = c.slice();
  }
  if (jo) {
    const local = jo.system === "local";
    for (const end of ["i", "j"]) {
      const d = (jo[end] || [0, 0, 0]).map(Number);
      const g = local ? [0, 1, 2].map(k => d[0] * x[k] + d[1] * y[k] + d[2] * z[k]) : d;
      if (end === "i") ei = ei.map((v, k) => v + g[k]); else ej = ej.map((v, k) => v + g[k]);
    }
  }
  if (Math.max(...ei.map(Math.abs), ...ej.map(Math.abs)) < 1e-12) return null;
  return [ei, ej];
}

/** Unfactored automatic end-offset lengths [Li, Lj] (insertion.auto_end_lengths). */
export function autoEndLengths(model, mm) {
  const key = p => p.map(v => (+v).toFixed(6)).join(",");
  const xm = memberAxes(mm).x;
  return [mm.pi, mm.pj].map(p => {
    const k = key(p);
    let best = 0;
    for (const n of model.members) {
      if (n === mm || n.uid === mm.uid) continue;
      if (key(n.pi) !== k && key(n.pj) !== k) continue;
      const an = memberAxes(n);
      if (Math.abs(Math.abs(dot(xm, an.x)) - 1) < 1e-6) continue;
      const sec = (model.sections || {})[n.section];
      if (!sec) continue;
      best = Math.max(best, 0.5 * ((+sec.h || 0) * Math.abs(dot(xm, an.y)) + (+sec.b || 0) * Math.abs(dot(xm, an.z))));
    }
    return best;
  });
}
