/* SkyFrame Wave C — pure snap geometry for extension / parallel snaps and
   object snaps in the ELEVATION and 3D views (no DOM; mirrored exactly by
   skyframe/core/snapgeom_ext.py and unit-tested there).

   2D (plan x-y or elevation s-z) — targets {points, segs} as in
   js/w8_snapgeom.js:
     ext  — extension: the foot F of W on the infinite line of a segment AB
            with parameter t < 0 or t > 1 (beyond an end), |W − F| ≤ tol and
            the gap to the nearer end ≤ maxExt;
     par  — parallel: from the pending point P, the foot F of W on the line
            through P parallel to a segment AB, |W − F| ≤ tol, |F − P| > tol.
   snap2(w, tgt, tol, modes, from) = w8 snapObjects (joint < inter < mid <
   perp) and then ext < par — object snaps first, the new ones last.

   3D — exact rays: ray(viewer, mx, my) is the pinhole ray of the viewer
   projector; lineRay(A, u, O, d) the parameters of the closest points of
   the line A + t·u and the ray O + s·d (Gram solve); targets3(m) gives
   joints / edges of the whole model plus grid points at every level; snap3
   ranks joint < inter < mid < perp < ext < par within `tolPx` on screen.
   planeHit(O, d, P0, n) — ray / plane intersection (vertex reshaper).
   CONTRACT "Wave C interface …" § Snapping. */

import SG from "./w8_snapgeom.js";

export const WC_SNAP_KINDS = ["joint", "mid", "inter", "perp", "ext", "par"];
export const WC_PRIORITY = { joint: 0, inter: 1, mid: 2, perp: 3, ext: 4, par: 5 };

/** Extension / parallel candidates near w (2D), best first, or null. */
export function snapExtPar(w, tgt, tol, modes, from = null, maxExt = Infinity) {
  const p = [w.x, w.y], cands = [];
  if (modes.ext) for (const [a, b] of tgt.segs) {
    const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
    if (!(L2 > 1e-18)) continue;
    const t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2;
    if (t >= -1e-9 && t <= 1 + 1e-9) continue;
    const fx = a[0] + t * dx, fy = a[1] + t * dy, d = Math.hypot(p[0] - fx, p[1] - fy);
    const gap = (t < 0 ? -t : t - 1) * Math.sqrt(L2);
    if (d <= tol && gap <= maxExt) cands.push({ x: fx, y: fy, kind: "ext", d, ref: [a, b] });
  }
  if (modes.par && from) for (const [a, b] of tgt.segs) {
    const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy);
    if (!(L > 1e-9)) continue;
    const ux = dx / L, uy = dy / L;
    const s = (p[0] - from[0]) * ux + (p[1] - from[1]) * uy;
    const fx = from[0] + s * ux, fy = from[1] + s * uy, d = Math.hypot(p[0] - fx, p[1] - fy);
    if (d <= tol && Math.abs(s) > tol) cands.push({ x: fx, y: fy, kind: "par", d, ref: [a, b] });
  }
  if (!cands.length) return null;
  cands.sort((u, v) => (WC_PRIORITY[u.kind] - WC_PRIORITY[v.kind]) || (u.d - v.d));
  return cands[0];
}

/** Full 2D object snap: w8 kinds first, then extension / parallel. */
export function snap2(w, tgt, tol, modes, from = null, maxExt = Infinity) {
  const r = (modes.joint || modes.mid || modes.inter || modes.perp) ? SG.snapObjects(w, tgt, tol, modes, from) : null;
  if (r) return r;
  return snapExtPar(w, tgt, tol, modes, from, maxExt);
}

/** Elevation snap targets on plane `pl` (ElevEditor.plane()) in (s, z):
    frames / links lying in the plane, wall edges, story level lines. */
export function elevTargets(m, ed) {
  const points = [], segs = [];
  if (!m) return { points, segs };
  const inP = p => ed.inPlane(p), S = p => [ed.sOf(p), p[2]];
  for (const mm of m.members || []) {
    if (!(inP(mm.pi) && inP(mm.pj))) continue;
    const a = S(mm.pi), b = S(mm.pj);
    points.push(a, b);
    if (!mm.curve && Math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-9) segs.push([a, b]);
  }
  for (const lk of m.links || []) if (inP(lk.pi) && inP(lk.pj)) points.push(S(lk.pi), S(lk.pj));
  for (const sh of m.shells || []) {
    if (!sh.corners.every(inP)) continue;
    const cs = sh.corners.map(S);
    for (let i = 0; i < cs.length; i++) { points.push(cs[i]); const j = (i + 1) % cs.length; if (Math.hypot(cs[j][0] - cs[i][0], cs[j][1] - cs[i][1]) > 1e-9) segs.push([cs[i], cs[j]]); }
  }
  return { points, segs };
}

/* ------------------------------------------------------------ 3D */
const sub3 = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add3 = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const mul3 = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const norm3 = a => Math.sqrt(dot3(a, a));

/** Pinhole ray through screen pixel (mx, my) of a Viewer3D. */
export function ray(v, mx, my) {
  const { eye, fwd, right, up } = v._basis();
  const h = v.canvas.clientHeight, w = v.canvas.clientWidth;
  const focal = (h / 2) / Math.tan(v.fov / 2);
  const x = (mx - w / 2) / focal, y = -(my - h / 2) / focal;
  const d = [right[0] * x + up[0] * y + fwd[0], right[1] * x + up[1] * y + fwd[1], right[2] * x + up[2] * y + fwd[2]];
  const n = norm3(d);
  return { o: eye.slice(), d: mul3(d, 1 / n) };
}

/** Closest points of line A + t·u and ray O + s·d: {t, s, dist} (null when
    parallel). */
export function lineRay(A, u, O, d) {
  const w0 = sub3(A, O);
  const a = dot3(u, u), b = dot3(u, d), c = dot3(d, d), dd = dot3(u, w0), e = dot3(d, w0);
  const den = a * c - b * b;
  if (!(Math.abs(den) > 1e-15 * a * c)) return null;
  const t = (b * e - c * dd) / den, s = (a * e - b * dd) / den;
  const p = add3(A, mul3(u, t)), q = add3(O, mul3(d, s));
  return { t, s, dist: norm3(sub3(p, q)), p };
}

/** Closest points of two 3D segments: {t, u, dist, p} (t, u clamped). */
export function segSeg(a, b, c, e) {
  const u = sub3(b, a), v = sub3(e, c), w0 = sub3(a, c);
  const A = dot3(u, u), B = dot3(u, v), C = dot3(v, v), D = dot3(u, w0), E = dot3(v, w0);
  const den = A * C - B * B;
  if (!(den > 1e-18 * A * C)) return null;
  let t = (B * E - C * D) / den, s = (A * E - B * D) / den;
  if (t < -1e-9 || t > 1 + 1e-9 || s < -1e-9 || s > 1 + 1e-9) return null;
  t = Math.max(0, Math.min(1, t)); s = Math.max(0, Math.min(1, s));
  const p = add3(a, mul3(u, t)), q = add3(c, mul3(v, s));
  return { t, u: s, dist: norm3(sub3(p, q)), p: mul3(add3(p, q), 0.5) };
}

/** Ray / plane intersection point (null when parallel or behind). */
export function planeHit(O, d, P0, n) {
  const den = dot3(d, n);
  if (Math.abs(den) < 1e-12) return null;
  const s = dot3(sub3(P0, O), n) / den;
  if (!(s > 0)) return null;
  return add3(O, mul3(d, s));
}

/** 3D snap targets: joints / members / links / shell edges + grid points
    at every story level. */
export function targets3(m, gridPts = []) {
  const points = [], segs = [], seen = new Set();
  const key = p => p.map(v => (Math.round(+v * 1e6) / 1e6).toFixed(6)).join(",");
  const addP = p => { const k = key(p); if (!seen.has(k)) { seen.add(k); points.push(p.map(Number)); } };
  if (!m) return { points, segs };
  for (const mm of m.members || []) { addP(mm.pi); addP(mm.pj); if (!mm.curve) segs.push([mm.pi.map(Number), mm.pj.map(Number)]); }
  for (const lk of m.links || []) { addP(lk.pi); addP(lk.pj); }
  for (const s of m.shells || []) s.corners.forEach((c, i) => { addP(c); segs.push([c.map(Number), s.corners[(i + 1) % s.corners.length].map(Number)]); });
  const st = m.stories || [];
  const levels = [...new Set([(st[0] ? st[0].elevation - st[0].height : 0), ...st.map(s => s.elevation)])];
  for (const g of gridPts) for (const z of levels) addP([g[0], g[1], z]);
  return { points, segs };
}

/** Best 3D snap at screen (mx, my): {p:[x,y,z], kind, x, y (screen), ref?}.
    P = viewer projector, R = ray(viewer, mx, my); from = pending 3D point. */
export function snap3(P, R, mx, my, tgt, tolPx, modes, from = null) {
  const scr = p => { const c = P.toCam(p); return c[2] < P.near ? null : P.proj(c); };
  const cands = [];
  const consider = (p, kind, ref) => {
    const s = scr(p);
    if (!s) return;
    const d = Math.hypot(s.x - mx, s.y - my);
    if (d <= tolPx) cands.push({ p, kind, x: s.x, y: s.y, d, z: s.z, ref });
  };
  if (modes.joint) for (const q of tgt.points) consider(q, "joint");
  // segments whose screen trace passes near the cursor
  const near = [];
  for (const sg of tgt.segs) {
    const a = scr(sg[0]), b = scr(sg[1]);
    if (!a || !b) continue;
    sg._scr = [a, b];
    if (SG.distToSeg([mx, my], [a.x, a.y], [b.x, b.y]) <= tolPx) near.push(sg);
  }
  if (modes.mid) for (const [a, b] of tgt.segs) consider(mul3(add3(a, b), 0.5), "mid");
  if (modes.inter) for (let i = 0; i < near.length; i++) for (let j = i + 1; j < near.length; j++) {
    const r = segSeg(near[i][0], near[i][1], near[j][0], near[j][1]);
    if (r && r.dist <= 1e-6 * Math.max(1, norm3(sub3(near[i][1], near[i][0])))) consider(r.p, "inter");
  }
  if (modes.perp && from) for (const [a, b] of tgt.segs) {
    const u = sub3(b, a), L2 = dot3(u, u);
    if (!(L2 > 1e-18)) continue;
    const t = dot3(sub3(from, a), u) / L2;
    if (t < -1e-9 || t > 1 + 1e-9) continue;
    const f = add3(a, mul3(u, t));
    if (norm3(sub3(f, from)) > 1e-9) consider(f, "perp");
  }
  if (modes.ext) for (const sg of tgt.segs) {
    const [a, b] = sg, u = sub3(b, a), L = norm3(u);
    if (!(L > 1e-9)) continue;
    const r = lineRay(a, u, R.o, R.d);
    if (!r || r.s <= 0 || (r.t >= -1e-9 && r.t <= 1 + 1e-9)) continue;
    consider(r.p, "ext", sg);
  }
  if (modes.par && from) for (const sg of tgt.segs) {
    const u = sub3(sg[1], sg[0]);
    if (!(norm3(u) > 1e-9)) continue;
    const r = lineRay(from, u, R.o, R.d);
    if (!r || r.s <= 0 || Math.abs(r.t) * norm3(u) < 1e-6) continue;
    consider(r.p, "par", sg);
  }
  if (!cands.length) return null;
  cands.sort((u, v) => (WC_PRIORITY[u.kind] - WC_PRIORITY[v.kind]) || (u.d - v.d) || (u.z - v.z));
  return cands[0];
}

export default { WC_SNAP_KINDS, WC_PRIORITY, snapExtPar, snap2, elevTargets, ray, lineRay, segSeg, planeHit, targets3, snap3 };
