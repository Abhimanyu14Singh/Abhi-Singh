/* SkyFrame W8 — pure snap / arc geometry (no DOM; unit-tested in node and
   mirrored exactly by skyframe/core/snapgeom.py).

   CONTRACT "Master / similar stories, selection and snapping".

   Plan-view object snaps (ETABS Draw › Snap Options):
     joint   — frame / link / wall ends, column points, slab corners
     mid     — mid-points of frame / wall / slab edges
     inter   — intersections of two plan segments (inside both)
     perp    — foot of the perpendicular from the pending point (the first
               click of a two-click tool) onto a segment (inside it)
   Candidates within `tol` (world units) are ranked by priority
   joint < inter < mid < perp, then by distance; the grid snap is used only
   when no object snap is in range. Everything is exact floating-point
   geometry: intersections by Cramer's rule, perpendicular feet by
   projection, arcs by the circumcircle of three points. */

export const SNAP_KINDS = ["joint", "mid", "inter", "perp"];
export const SNAP_PRIORITY = { joint: 0, inter: 1, mid: 2, perp: 3 };
const EPS = 1e-12;

/** Proper or touching intersection of segments AB and CD (2D), or null for
    parallel / collinear / disjoint segments. Returns {x, y, t, u}. */
export function segIntersect(a, b, c, d) {
  const rx = b[0] - a[0], ry = b[1] - a[1];
  const sx = d[0] - c[0], sy = d[1] - c[1];
  const den = rx * sy - ry * sx;
  const scale = Math.hypot(rx, ry) * Math.hypot(sx, sy);
  if (!(scale > 0) || Math.abs(den) <= EPS * scale) return null;
  const qx = c[0] - a[0], qy = c[1] - a[1];
  const t = (qx * sy - qy * sx) / den;
  const u = (qx * ry - qy * rx) / den;
  const tol = 1e-9;
  if (t < -tol || t > 1 + tol || u < -tol || u > 1 + tol) return null;
  return { x: a[0] + t * rx, y: a[1] + t * ry, t, u };
}

/** Foot of the perpendicular from P onto segment AB, or null when it falls
    outside the segment (or AB is degenerate). Returns {x, y, t}. */
export function perpFoot(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const l2 = dx * dx + dy * dy;
  if (!(l2 > 0)) return null;
  const t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2;
  if (t < -1e-9 || t > 1 + 1e-9) return null;
  return { x: a[0] + t * dx, y: a[1] + t * dy, t };
}

export const midpoint = (a, b) => ({ x: (a[0] + b[0]) / 2, y: (a[1] + b[1]) / 2 });

/** Distance from P to segment AB. */
export function distToSeg(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const l2 = dx * dx + dy * dy;
  let t = l2 ? ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

/** Plan snap targets of one story: {points: [[x, y]], segs: [[a, b]]}. */
export function storyTargets(m, story) {
  const points = [], segs = [];
  if (!m) return { points, segs };
  const st = (m.stories || []).find(s => s.name === story);
  const zb = st ? st.elevation - st.height - 1e-6 : -Infinity, zt = st ? st.elevation + 1e-6 : Infinity;
  for (const mm of m.members || []) {
    if (mm.story !== story) continue;
    const a = [mm.pi[0], mm.pi[1]], b = [mm.pj[0], mm.pj[1]];
    points.push(a);
    if (mm.kind === "column" && Math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9) continue;
    points.push(b);
    if (!mm.curve) segs.push([a, b]);
  }
  for (const s of m.shells || []) {
    if (s.story !== story) continue;
    const cs = s.corners.map(c => [c[0], c[1]]);
    if (s.kind === "wall") {
      // plan trace: the two extreme corners
      let best = [cs[0], cs[1]], bl = -1;
      for (let i = 0; i < cs.length; i++) for (let j = i + 1; j < cs.length; j++) {
        const l = Math.hypot(cs[j][0] - cs[i][0], cs[j][1] - cs[i][1]);
        if (l > bl + 1e-12) { bl = l; best = [cs[i], cs[j]]; }
      }
      points.push(best[0], best[1]);
      if (bl > 1e-9) segs.push(best);
    } else {
      for (let i = 0; i < cs.length; i++) {
        points.push(cs[i]);
        segs.push([cs[i], cs[(i + 1) % cs.length]]);
      }
    }
  }
  for (const lk of m.links || []) {
    const inz = z => z > zb && z <= zt;
    if (!(inz(lk.pi[2]) && inz(lk.pj[2]))) continue;
    points.push([lk.pi[0], lk.pi[1]], [lk.pj[0], lk.pj[1]]);
  }
  return { points, segs };
}

/** Best object snap near world point W (within tol), or null.
    modes: {joint, mid, inter, perp} booleans; from: [x, y] pending point. */
export function snapObjects(w, targets, tol, modes, from = null) {
  const cands = [];
  const p = [w.x, w.y];
  const near = (x, y, kind) => {
    const d = Math.hypot(x - p[0], y - p[1]);
    if (d <= tol) cands.push({ x, y, kind, d });
  };
  if (modes.joint) for (const q of targets.points) near(q[0], q[1], "joint");
  if (modes.mid) for (const [a, b] of targets.segs) { const m = midpoint(a, b); near(m.x, m.y, "mid"); }
  if (modes.inter) {
    // only segments passing near W can produce an intersection in range
    const close = targets.segs.filter(([a, b]) => distToSeg(p, a, b) <= tol);
    for (let i = 0; i < close.length; i++) for (let j = i + 1; j < close.length; j++) {
      const r = segIntersect(close[i][0], close[i][1], close[j][0], close[j][1]);
      if (r) near(r.x, r.y, "inter");
    }
  }
  if (modes.perp && from) for (const [a, b] of targets.segs) {
    const f = perpFoot(from, a, b);
    if (f && Math.hypot(f.x - from[0], f.y - from[1]) > 1e-9) near(f.x, f.y, "perp");
  }
  if (!cands.length) return null;
  cands.sort((u, v) => (SNAP_PRIORITY[u.kind] - SNAP_PRIORITY[v.kind]) || (u.d - v.d));
  return cands[0];
}

/* ------------------------------------------------------------ arcs */
/** Circle through three plan/3D points (in their common plane).
    Returns {center:[x,y,z], r, normal:[nx,ny,nz]} or null (collinear). */
export function circle3(p1, p2, p3) {
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const a = sub(p1, p3), b = sub(p2, p3);
  const axb = cross(a, b);
  const n2 = dot(axb, axb);
  if (!(n2 > 1e-18 * dot(a, a) * dot(b, b))) return null;
  const t1 = cross(b, axb), t2 = cross(axb, a);
  const k1 = dot(a, a) / (2 * n2), k2 = dot(b, b) / (2 * n2);
  const c = [p3[0] + k1 * t1[0] + k2 * t2[0], p3[1] + k1 * t1[1] + k2 * t2[1], p3[2] + k1 * t1[2] + k2 * t2[2]];
  const nn = Math.sqrt(n2);
  return { center: c, r: Math.hypot(...sub(p1, c)), normal: axb.map(v => v / nn) };
}

/** Points along the arc from pi through via to pj (n chords, n+1 points),
    or null when the three points are collinear. */
export function arcPoints3(pi, via, pj, n = 24) {
  const C = circle3(pi, via, pj);
  if (!C) return null;
  const c = C.center, nrm = C.normal;
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const e1 = sub(pi, c).map(v => v / C.r);
  const e2 = cross(nrm, e1);
  const ang = p => { const v = sub(p, c); let t = Math.atan2(dot(v, e2), dot(v, e1)); if (t < 0) t += 2 * Math.PI; return t; };
  // nrm = (p1-p3) x (p2-p3) orients the circle so that pi -> via -> pj is CCW
  const sweep = ang(pj) || 2 * Math.PI;
  const out = [];
  for (let k = 0; k <= n; k++) {
    const t = sweep * k / n;
    out.push([0, 1, 2].map(i => c[i] + C.r * (Math.cos(t) * e1[i] + Math.sin(t) * e2[i])));
  }
  out[0] = [...pi]; out[n] = [...pj];
  return { points: out, center: c, r: C.r, sweep, normal: nrm };
}

/** Mid-arc "via" point of a horizontal arc of radius R between plan ends
    pi, pj (same z), bulging to the LEFT (side=+1) or RIGHT (side=-1) of
    pi -> pj. Returns [x, y, z] or null when R < chord / 2. */
export function viaFromRadius(pi, pj, R, side = 1) {
  const dx = pj[0] - pi[0], dy = pj[1] - pi[1];
  const c = Math.hypot(dx, dy);
  if (!(c > 0) || !(R >= c / 2)) return null;
  const s = R - Math.sqrt(Math.max(R * R - c * c / 4, 0));   // sagitta
  const nx = -dy / c, ny = dx / c;
  return [(pi[0] + pj[0]) / 2 + side * s * nx, (pi[1] + pj[1]) / 2 + side * s * ny, (pi[2] + pj[2]) / 2];
}

export default { SNAP_KINDS, SNAP_PRIORITY, segIntersect, perpFoot, midpoint, distToSeg, storyTargets, snapObjects, circle3, arcPoints3, viaFromRadius };
