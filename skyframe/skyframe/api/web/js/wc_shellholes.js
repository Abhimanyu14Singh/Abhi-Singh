/* SkyFrame Wave C — Edit Shells on shells WITH OPENINGS (client port of
   skyframe/core/shell_holes.py; ?mock=1 and the dialog previews run the
   same algorithm through js/wb_shellgeom.js hooks).

   Openings become absolute 3D polygons (holes3); divide / chop pieces are
   "cell minus openings" (subtract), merge is "union minus openings"
   (union), expand / reshape keep every opening where it is (openingsFor
   re-expresses it on the new outline: a parametric rectangle on a
   parallelogram, else a polygon opening). The region boolean traces the
   directed boundary sub-segments (inside on the left) with the clockwise
   turning rule; CCW loops are pieces, CW loops are holes.
   CONTRACT "Wave C interface …" § Edit Shells with openings. */

import { openingPolygon3 as shOpening3 } from "./polygeom.js";

const r9 = v => { const r = Math.round(v * 1e9) / 1e9; return r === 0 ? 0 : r; };
const clean = p => [r9(+p[0]), r9(+p[1]), r9(+p[2])];
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = a => Math.sqrt(dot(a, a));
const unit = a => { const n = norm(a); if (n < 1e-15) throw new Error("degenerate shell plane"); return [a[0] / n, a[1] / n, a[2] / n]; };
// Python round(v, 9) on a 2D key (shared vertex identity)
const k2 = p => [r9(p[0]), r9(p[1])];
const ks = p => `${p[0]},${p[1]}`;

export function newell(pts) {
  const n = [0, 0, 0];
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    n[0] += (a[1] - b[1]) * (a[2] + b[2]); n[1] += (a[2] - b[2]) * (a[0] + b[0]); n[2] += (a[0] - b[0]) * (a[1] + b[1]);
  }
  return n;
}

export class Frame {
  constructor(corners) {
    const c = corners.map(p => p.map(Number));
    this.o = c[0];
    this.n = unit(newell(c));
    let e1 = null;
    for (let i = 0; i < c.length; i++) { const d = sub(c[(i + 1) % c.length], c[i]); if (norm(d) > 1e-12) { e1 = unit(d); break; } }
    if (!e1) throw new Error("degenerate shell");
    this.e1 = e1; this.e2 = cross(this.n, e1);
  }
  to2(p) { const d = sub(p.map(Number), this.o); return [dot(d, this.e1), dot(d, this.e2)]; }
  to3(q) { return [0, 1, 2].map(k => this.o[k] + q[0] * this.e1[k] + q[1] * this.e2[k]); }
  project(p) { return this.to3(this.to2(p)); }
}

export function signedArea(poly) {
  let s = 0;
  for (let i = 0; i < poly.length; i++) { const a = poly[i], b = poly[(i + 1) % poly.length]; s += a[0] * b[1] - b[0] * a[1]; }
  return 0.5 * s;
}
export function pointIn(p, poly) {
  const [x, y] = p;
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y)) { const xc = xi + (y - yi) * (xj - xi) / (yj - yi); if (x < xc) inside = !inside; }
  }
  return inside;
}
function segCross(a, b, c, d) {
  const rx = b[0] - a[0], ry = b[1] - a[1], sx = d[0] - c[0], sy = d[1] - c[1];
  const den = rx * sy - ry * sx;
  if (Math.abs(den) < 1e-15 * Math.max(1, Math.hypot(rx, ry) * Math.hypot(sx, sy))) return null;
  const qx = c[0] - a[0], qy = c[1] - a[1];
  const t = (qx * sy - qy * sx) / den, u = (qx * ry - qy * rx) / den, e = 1e-12;
  return (t >= -e && t <= 1 + e && u >= -e && u <= 1 + e) ? [a[0] + t * rx, a[1] + t * ry] : null;
}

function boundaryLoops(polys, inside) {
  const pts = polys.flat();
  if (!pts.length) return [];
  const scale = Math.max(1, ...pts.map(q => Math.max(Math.abs(q[0]), Math.abs(q[1]))));
  const tolOn = 1e-8 * scale;
  const segs = [];
  for (const pg of polys) for (let i = 0; i < pg.length; i++) {
    const a = pg[i], b = pg[(i + 1) % pg.length];
    if (Math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-12 * scale) segs.push([a, b]);
  }
  const vert = new Map();
  for (const q of pts) { const k = k2(q); if (!vert.has(ks(k))) vert.set(ks(k), k); }
  for (let i = 0; i < segs.length; i++) for (let j = i + 1; j < segs.length; j++) {
    const x = segCross(segs[i][0], segs[i][1], segs[j][0], segs[j][1]);
    if (x) { const k = k2(x); if (!vert.has(ks(k))) vert.set(ks(k), k); }
  }
  const allv = [...vert.values()];
  const subs = new Map();
  for (const [a, b] of segs) {
    const ab = [b[0] - a[0], b[1] - a[1]], L2 = ab[0] ** 2 + ab[1] ** 2;
    const on = [];
    for (const q of allv) {
      const t = ((q[0] - a[0]) * ab[0] + (q[1] - a[1]) * ab[1]) / L2;
      if (t >= -1e-12 && t <= 1 + 1e-12) {
        const fx = a[0] + t * ab[0], fy = a[1] + t * ab[1];
        if (Math.hypot(q[0] - fx, q[1] - fy) <= tolOn) on.push([t, q]);
      }
    }
    on.sort((u, v) => u[0] - v[0] || u[1][0] - v[1][0] || u[1][1] - v[1][1]);
    const chain = [];
    for (const [, q] of on) if (!chain.length || ks(k2(chain[chain.length - 1])) !== ks(k2(q))) chain.push(q);
    for (let k = 0; k + 1 < chain.length; k++) {
      const p = chain[k], q = chain[k + 1];
      if (Math.hypot(q[0] - p[0], q[1] - p[1]) <= 1e-9 * scale) continue;
      const ka = ks(k2(p)), kb = ks(k2(q));
      const key = ka < kb ? ka + "|" + kb : kb + "|" + ka;
      if (!subs.has(key)) subs.set(key, [p, q]);
    }
  }
  const out = new Map();
  let nedge = 0;
  for (const [p, q] of subs.values()) {
    const dx = q[0] - p[0], dy = q[1] - p[1], L = Math.hypot(dx, dy);
    const eps = Math.min(1e-6 * scale, 0.05 * L);
    const mx = (p[0] + q[0]) / 2, my = (p[1] + q[1]) / 2, nx = -dy / L, ny = dx / L;
    const left = inside([mx + eps * nx, my + eps * ny]), right = inside([mx - eps * nx, my - eps * ny]);
    let a, b;
    if (left && !right) { a = p; b = q; } else if (right && !left) { a = q; b = p; } else continue;
    const ka = ks(k2(a));
    if (!out.has(ka)) out.set(ka, { p: k2(a), to: [] });
    out.get(ka).to.push(k2(b));
    nedge++;
  }
  const used = new Set(), loops = [];
  const ek = (u, v) => ks(u) + ">" + ks(v);
  const nxt = (u, v) => {
    const rx = u[0] - v[0], ry = u[1] - v[1];
    let best = null, bang = null;
    for (const w of (out.get(ks(v)) || { to: [] }).to) {
      if (used.has(ek(v, w))) continue;
      const wx = w[0] - v[0], wy = w[1] - v[1];
      const ccw = Math.atan2(rx * wy - ry * wx, rx * wx + ry * wy);
      let cw = ((-ccw) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI);
      if (cw <= 1e-12) cw = 2 * Math.PI;
      if (bang === null || cw < bang) { best = w; bang = cw; }
    }
    return best;
  };
  // deterministic start order = Python sorted() over (x, y) tuples
  const starts = [...out.values()].sort((u, v) => u.p[0] - v.p[0] || u.p[1] - v.p[1]);
  for (const st of starts) for (const b of st.to.slice()) {
    const a = st.p;
    if (used.has(ek(a, b))) continue;
    const loop = [a];
    used.add(ek(a, b));
    let u = a, v = b, guard = 0;
    while (ks(v) !== ks(a)) {
      loop.push(v);
      const w = nxt(u, v);
      if (!w) throw new Error("could not trace the shell boundary around the openings");
      used.add(ek(v, w));
      u = v; v = w;
      if (++guard > nedge + 2) throw new Error("could not trace the shell boundary around the openings");
    }
    loops.push(...splitPinches(loop));
  }
  return loops.filter(lp => lp.length >= 3).map(dropCollinear2);
}
function splitPinches(loop) {
  const seen = new Map();
  for (let j = 0; j < loop.length; j++) {
    const k = ks(loop[j]);
    if (seen.has(k)) { const i = seen.get(k); return [...splitPinches(loop.slice(i, j)), ...splitPinches(loop.slice(0, i).concat(loop.slice(j)))]; }
    seen.set(k, j);
  }
  return [loop];
}
function dropCollinear2(pts) {
  const out = pts.slice();
  let changed = true;
  while (changed && out.length > 3) {
    changed = false;
    for (let i = 0; i < out.length; i++) {
      const a = out[(i - 1 + out.length) % out.length], b = out[i], c = out[(i + 1) % out.length];
      const abx = b[0] - a[0], aby = b[1] - a[1], bcx = c[0] - b[0], bcy = c[1] - b[1];
      const la = Math.hypot(abx, aby), lb = Math.hypot(bcx, bcy);
      if (la < 1e-12 || (Math.abs(abx * bcy - aby * bcx) <= 1e-12 * Math.max(1, la * lb) && abx * bcx + aby * bcy > 0)) { out.splice(i, 1); changed = true; break; }
    }
  }
  return out;
}
function regions(loops) {
  const res = loops.filter(lp => signedArea(lp) > 0).map(o => [o, []]);
  for (const h of loops.filter(lp => signedArea(lp) < 0)) {
    const a = h[0], b = h[1], dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy), eps = Math.min(1e-6, 0.05 * L);
    const p = [(a[0] + b[0]) / 2 + eps * dy / L, (a[1] + b[1]) / 2 - eps * dx / L];
    let host = -1;
    res.forEach(([o], k) => { if (pointIn(p, o) && (host < 0 || signedArea(o) < signedArea(res[host][0]))) host = k; });
    if (host < 0) throw new Error("an opening loop has no host piece");
    res[host][1].push(h);
  }
  return res;
}
const bboxOverlap = (a, b) => {
  const e = 1e-9, mn = (p, i) => Math.min(...p.map(q => q[i])), mx = (p, i) => Math.max(...p.map(q => q[i]));
  return !(mx(a, 0) < mn(b, 0) - e || mx(b, 0) < mn(a, 0) - e || mx(a, 1) < mn(b, 1) - e || mx(b, 1) < mn(a, 1) - e);
};
export function subtract(cell, holes) {
  let c = cell.map(p => [p[0], p[1]]);
  if (signedArea(c) < 0) c = c.reverse();
  const hs = holes.map(h => h.map(p => [p[0], p[1]]));
  const inside = p => pointIn(p, c) && !hs.some(h => pointIn(p, h));
  const rel = hs.filter(h => bboxOverlap(h, c));
  if (!rel.length) return [[c, []]];
  return regions(boundaryLoops([c, ...rel], inside));
}
export function union(polys, holes = []) {
  const ps = polys.map(pg => { const q = pg.map(p => [p[0], p[1]]); return signedArea(q) < 0 ? q.reverse() : q; });
  const hs = holes.map(h => h.map(p => [p[0], p[1]]));
  const inside = p => ps.some(pg => pointIn(p, pg)) && !hs.some(h => pointIn(p, h));
  return regions(boundaryLoops([...ps, ...hs], inside));
}
export const netArea2 = regs => regs.reduce((a, [o, hs]) => a + signedArea(o) - hs.reduce((b, h) => b + Math.abs(signedArea(h)), 0), 0);

/* ------------------------------------------------ shell dict helpers */
export const holes3 = s => (s.openings || []).map(o => shOpening3(s, o).map(p => p.map(Number)));

function parallelogram(c) {
  if (c.length !== 4) return false;
  const s = Math.max(1, ...c.flat().map(Math.abs));
  return [0, 1, 2].every(k => Math.abs(c[0][k] + c[2][k] - c[1][k] - c[3][k]) <= 1e-9 * s);
}
function paramRect(corners, hole) {
  if (hole.length !== 4 || !parallelogram(corners)) return null;
  const c0 = corners[0].map(Number), a = sub(corners[1], c0), b = sub(corners[3], c0);
  const aa = dot(a, a), bb = dot(b, b), ab = dot(a, b), det = aa * bb - ab * ab;
  if (det <= 1e-24) return null;
  const uv = hole.map(p => { const d = sub(p, c0), da = dot(d, a), db = dot(d, b); return [(da * bb - db * ab) / det, (db * aa - da * ab) / det]; });
  const r9u = v => Math.round(v * 1e9) / 1e9;
  const us = [...new Set(uv.map(q => r9u(q[0])))].sort((x, y) => x - y), vs = [...new Set(uv.map(q => r9u(q[1])))].sort((x, y) => x - y);
  if (us.length !== 2 || vs.length !== 2) return null;
  const have = new Set(uv.map(q => `${r9u(q[0])},${r9u(q[1])}`));
  for (const u of us) for (const v of vs) if (!have.has(`${u},${v}`)) return null;
  const u0 = Math.min(...uv.map(q => q[0])), u1 = Math.max(...uv.map(q => q[0])), v0 = Math.min(...uv.map(q => q[1])), v1 = Math.max(...uv.map(q => q[1]));
  if (!(u0 >= -1e-12 && u0 < u1 && u1 <= 1 + 1e-12 && v0 >= -1e-12 && v0 < v1 && v1 <= 1 + 1e-12)) return null;
  const cl = x => (Math.abs(x) <= 1e-12 ? 0 : Math.abs(x - 1) <= 1e-12 ? 1 : x);
  return { u0: cl(u0), v0: cl(v0), u1: cl(u1), v1: cl(v1) };
}
export function openingsFor(corners, holes) {
  return holes.map(h => paramRect(corners, h) || { u0: 0, v0: 0, u1: 1, v1: 1, polygon: h.map(clean) });
}
/** Cheap client check: every opening vertex inside the closed outline. */
export function checkInside(s, what) {
  if (!(s.openings || []).length) return;
  const fr = new Frame(s.corners), outer = s.corners.map(p => fr.to2(p));
  const scale = Math.max(1, ...outer.flat().map(Math.abs)), tol = 1e-9 * scale;
  const onB = q => outer.some((a, i) => { const b = outer[(i + 1) % outer.length], dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
    const t = Math.max(0, Math.min(1, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / (L2 || 1))); return Math.hypot(q[0] - a[0] - t * dx, q[1] - a[1] - t * dy) <= tol; });
  for (const h of holes3(s)) for (let i = 0; i < h.length; i++) {
    const q = fr.to2(h[i]), m = fr.to2(h[i].map((v, k) => (v + h[(i + 1) % h.length][k]) / 2));
    for (const p of [q, m]) if (!onB(p) && !pointIn(p, outer))
      throw new Error(`${what}: shell ${s.uid}: the openings must stay inside the shell`);
  }
}

/** Divide / chop hook: replace s by `cells` minus its openings. */
export function replaceWithOpenings(ctx, s, cells, replacePieces) {
  const fr = new Frame(s.corners);
  const hs2 = holes3(s).map(h => h.map(p => fr.to2(p)));
  const pieces3 = [], holesOf = [];
  for (const cell of cells) {
    const c2 = cell.map(p => fr.to2(p));
    for (const [outer, hs] of subtract(c2, hs2)) {
      if (Math.abs(signedArea(outer)) <= 1e-12) continue;
      pieces3.push(outer.map(q => clean(fr.to3(q))));
      holesOf.push(hs.map(h => h.map(q => clean(fr.to3(q)))));
    }
  }
  if (!pieces3.length) throw new Error(`shell ${s.uid}: nothing left outside the openings`);
  const uids = replacePieces(ctx, s, pieces3);
  uids.forEach((uid, k) => { const sh = ctx.shell(uid); sh.openings = openingsFor(sh.corners, holesOf[k]); });
  return uids;
}
/** Merge hook: [outline3, holes3] of the union minus openings. */
export function mergeWithOpenings(shells, polys) {
  const fr = new Frame(polys[0]);
  const p2 = polys.map(pg => pg.map(p => fr.to2(p)));
  const own = shells.flatMap(s => holes3(s).map(h => h.map(p => fr.to2(p))));
  const regs = union(p2, own);
  if (regs.length !== 1) throw new Error("shell_merge: the shells do not form one region (they must share edges)");
  const want = p2.reduce((a, pg) => a + Math.abs(signedArea(pg)), 0) - own.reduce((a, h) => a + Math.abs(signedArea(h)), 0);
  if (Math.abs(netArea2(regs) - want) > 1e-9 * Math.max(1, want)) throw new Error("shell_merge: shells overlap");
  const [outer, hs] = regs[0];
  return [outer.map(q => clean(fr.to3(q))), hs.map(h => h.map(q => clean(fr.to3(q))))];
}
export const projectOnPlane = (s, pts) => { const fr = new Frame(s.corners); return pts.map(p => fr.project(p)); };

export default { Frame, subtract, union, netArea2, holes3, openingsFor, checkInside, replaceWithOpenings, mergeWithOpenings, projectOnPlane, signedArea };
