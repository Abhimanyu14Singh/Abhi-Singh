/* SkyFrame Wave B — client geometry for Edit Shells / Draw Ramp / Wall
   Stacks / Auto Relabel and general (line) grids.

   A port of skyframe/core/edit_shells.py and skyframe/core/grid_general.py
   (CONTRACT "Model templates, general grids, shell editing and drawing
   aids"): wbApplyEdit(model, op, selection, params) deep-copies the model
   and returns { model, summary } exactly like POST /api/edit/<op>, so
   ?mock=1 and the previews run the same algorithms. Pure: never mutates its
   input; throws Error(message) where the backend answers 400. */

export const WB_SHELL_OPS = ["shell_divide", "shell_merge", "shell_chop", "shell_expand",
  "shell_reshape", "ramp", "wall_stack", "relabel"];
export const WALL_SHAPES = ["straight", "L", "C", "I", "T", "box"];
export const RELABEL_KINDS = ["column", "beam", "brace", "wall", "slab", "link"];
const PREFIX = { column: "C", beam: "B", brace: "D", wall: "W", slab: "F", link: "L" };
import WCH from "./wc_shellholes.js";   // Wave C: Edit Shells on shells with openings (port of core/shell_holes.py)
const TOL = 1e-9;
const MAX_PIECES = 400;

/* ------------------------------------------------ vectors */
const r6 = v => { const r = Math.round(v * 1e6) / 1e6; return r === 0 ? 0 : r; };
const r9 = v => { const r = Math.round(v * 1e9) / 1e9; return r === 0 ? 0 : r; };
const pkey = p => `${r6(+p[0])},${r6(+p[1])},${r6(+p[2])}`;
const clean = p => [r9(+p[0]), r9(+p[1]), r9(+p[2])];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const mul = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = a => Math.sqrt(dot(a, a));
const clone = o => JSON.parse(JSON.stringify(o));
const unit = v => { const n = norm(v); if (n < 1e-15) throw new Error("degenerate direction"); return [v[0] / n, v[1] / n, v[2] / n]; };

function f3(p) {
  if (!Array.isArray(p) || p.length !== 3 || !p.every(v => typeof v === "number" && isFinite(v)))
    throw new Error(`expected a point [x, y, z], got ${JSON.stringify(p)}`);
  return p.map(Number);
}
function num(params, key, def, o = {}) {
  const v = params[key] === undefined ? def : params[key];
  if (v === undefined || v === null) throw new Error(`'${key}' is required`);
  if (typeof v !== "number" || !isFinite(v)) throw new Error(`'${key}' must be a finite number`);
  if (o.integer && Math.trunc(v) !== v) throw new Error(`'${key}' must be an integer`);
  if (o.positive && v <= 0) throw new Error(`'${key}' must be > 0`);
  if (o.lo !== undefined && v < o.lo) throw new Error(`'${key}' must be >= ${o.lo}`);
  if (o.hi !== undefined && v > o.hi) throw new Error(`'${key}' must be <= ${o.hi}`);
  return v;
}

/* ------------------------------------------------ polygons */
export function newell(pts) {
  const n = [0, 0, 0];
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    n[0] += (a[1] - b[1]) * (a[2] + b[2]);
    n[1] += (a[2] - b[2]) * (a[0] + b[0]);
    n[2] += (a[0] - b[0]) * (a[1] + b[1]);
  }
  return n;
}
export const polyArea = pts => 0.5 * norm(newell(pts));
export function isConvex(pts) {
  const n = newell(pts);
  let sgn = 0;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[(i - 1 + pts.length) % pts.length], b = pts[i], c = pts[(i + 1) % pts.length];
    const z = dot(cross(sub(b, a), sub(c, b)), n);
    if (Math.abs(z) < 1e-12) continue;
    const s = z > 0 ? 1 : -1;
    if (sgn && s !== sgn) return false;
    sgn = s;
  }
  return true;
}
export function dropCollinear(pts, tol = 1e-9) {
  const out = pts.slice();
  let changed = true;
  while (changed && out.length > 3) {
    changed = false;
    for (let i = 0; i < out.length; i++) {
      const a = out[(i - 1 + out.length) % out.length], b = out[i], c = out[(i + 1) % out.length];
      const ab = sub(b, a), bc = sub(c, b);
      if (norm(ab) < tol || (norm(cross(ab, bc)) <= tol * Math.max(1, norm(ab) * norm(bc)) && dot(ab, bc) > 0)) {
        out.splice(i, 1); changed = true; break;
      }
    }
  }
  return out;
}
function dedupe(pts) {
  const out = [];
  for (const p of pts) if (!out.length || pkey(out[out.length - 1]) !== pkey(p)) out.push(clean(p));
  while (out.length > 1 && pkey(out[0]) === pkey(out[out.length - 1])) out.pop();
  return out;
}
export function clipPlane(pts, P0, N) {
  const s = pts.map(p => dot(sub(p, P0), N));
  const front = [], back = [];
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const a = pts[i], b = pts[(i + 1) % n], sa = s[i], sb = s[(i + 1) % n];
    if (sa >= -TOL) front.push(a);
    if (sa <= TOL) back.push(a);
    if ((sa > TOL && sb < -TOL) || (sa < -TOL && sb > TOL)) {
      const t = sa / (sa - sb);
      const p = clean([0, 1, 2].map(k => a[k] + t * (b[k] - a[k])));
      front.push(p); back.push(p);
    }
  }
  return [dedupe(front), dedupe(back)];
}
function orientLike(pts, e1) {
  let best = -2, bi = 0;
  for (let i = 0; i < pts.length; i++) {
    const d = sub(pts[(i + 1) % pts.length], pts[i]);
    if (norm(d) < 1e-12) continue;
    const c = dot(unit(d), e1);
    if (c > best + 1e-9) { best = c; bi = i; }
  }
  return pts.slice(bi).concat(pts.slice(0, bi));
}
function lineLine(P, D1, Q, D2) {
  const c = cross(D1, D2), cc = dot(c, c);
  if (cc < 1e-20 * Math.max(1, dot(D1, D1) * dot(D2, D2))) throw new Error("adjacent edges are parallel to the moved edge");
  const s = dot(cross(sub(Q, P), D2), c) / cc;
  return clean(add(P, mul(D1, s)));
}

/* ------------------------------------------------ edit context */
class Ctx {
  constructor(d) {
    this.d = d;
    for (const k of ["members", "shells", "links"]) if (!Array.isArray(d[k])) d[k] = [];
    if (!d.patterns) d.patterns = {};
    this.warnings = [];
    this.created = { members: [], shells: [], links: [], points: [] };
    this.deleted = { members: [], shells: [], links: [], points: [] };
    this.modified = { members: [], shells: [], links: [] };
    this.used = new Set();
    for (const k of ["members", "shells", "links"]) for (const o of d[k]) this.used.add(o.uid);
    this.next = {};
  }
  newUid(like, fallback = "S") {
    const m = /^[A-Za-z]+/.exec(String(like));
    const prefix = m ? m[0] : fallback;
    if (!(prefix in this.next)) {
      let mx = 0;
      const rx = new RegExp(`^${prefix}(\\d+)$`);
      for (const u of this.used) { const g = rx.exec(u); if (g) mx = Math.max(mx, +g[1]); }
      this.next[prefix] = mx + 1;
    }
    for (;;) { const uid = `${prefix}${this.next[prefix]++}`; if (!this.used.has(uid)) { this.used.add(uid); return uid; } }
  }
  shell(u) { return this.d.shells.find(s => s.uid === u) || null; }
  member(u) { return this.d.members.find(m => m.uid === u) || null; }
  markModified(kind, uid) { if (!this.modified[kind].includes(uid) && !this.created[kind].includes(uid)) this.modified[kind].push(uid); }
  storyFor(zs) {
    const st = this.d.stories || [];
    if (!st.length) return null;
    const z = Math.max(...zs);
    for (const s of st) if (Math.abs(+s.elevation - z) < 1e-6) return s.name;
    for (const s of st) { const zt = +s.elevation, zb = zt - +s.height; if (zb - 1e-9 < z && z < zt) return s.name; }
    return null;
  }
  restory(obj, pts) { const st = this.storyFor(pts.map(p => p[2])); if (st !== null) obj.story = st; }
  summary(op, extra) { return Object.assign({ op, created: this.created, deleted: this.deleted, modified: this.modified, warnings: this.warnings }, extra || {}); }
}
const newSel = () => ({ members: [], shells: [], links: [], points: [] });

function normalizeSelection(d, sel) {
  sel = sel || {};
  const out = {};
  for (const kind of ["members", "shells", "links"]) {
    const have = new Set((d[kind] || []).map(o => o.uid));
    const seen = [];
    for (const u of sel[kind] || []) {
      if (!have.has(u)) throw new Error(`selection.${kind}: unknown uid '${u}'`);
      if (!seen.includes(u)) seen.push(u);
    }
    out[kind] = seen;
  }
  out.points = (sel.points || []).map(f3);
  return out;
}
function replaceInList(lst, old, neu) {
  const out = [];
  for (const u of lst) {
    if (u === old) { for (const x of neu) if (!out.includes(x)) out.push(x); }
    else if (!out.includes(u)) out.push(u);
  }
  return out;
}
function replaceUidRefs(d, kind, old, neu) {
  for (const g of Object.values(d.groups || {})) if ((g[kind] || []).includes(old)) g[kind] = replaceInList(g[kind], old, neu);
  if (kind === "shells")
    for (const td of d.tendons || []) {
      let h = td.host;
      if (typeof h === "string") h = [h];
      if (h && h.includes(old)) td.host = replaceInList(h, old, neu);
    }
}
function shellLoads(d, uid) {
  const out = [];
  for (const [pn, pat] of Object.entries(d.patterns || {}))
    for (const k of ["area_loads", "shell_thermal_loads"])
      for (const ld of pat[k] || []) if (ld.region_uid === uid) out.push([pn, k, ld]);
  return out;
}
function copyLoads(d, src, dst) { for (const [pn, k, ld] of shellLoads(d, src)) { const nl = clone(ld); nl.region_uid = dst; d.patterns[pn][k].push(nl); } }
function dropLoads(d, uid) {
  for (const pat of Object.values(d.patterns || {}))
    for (const k of ["area_loads", "shell_thermal_loads"]) if (pat[k]) pat[k] = pat[k].filter(x => x.region_uid !== uid);
}
function noOpenings(s) { if ((s.openings || []).length) throw new Error(`shell ${s.uid}: shells with openings cannot be edited this way (remove the openings first)`); }
function replacePieces(ctx, s, pieces) {
  const d = ctx.d;
  if (pieces.length > MAX_PIECES) throw new Error(`too many pieces (> ${MAX_PIECES})`);
  const e1 = unit(sub(s.corners[1], s.corners[0]));
  const uids = [], fresh = [];
  const idx = d.shells.indexOf(s);
  pieces.forEach((pc0, k) => {
    const pc = orientLike(pc0.map(clean), e1);
    if (k === 0) { s.corners = pc; ctx.restory(s, pc); uids.push(s.uid); ctx.markModified("shells", s.uid); return; }
    const ns = clone(s);
    ns.uid = ctx.newUid(s.uid, "S");
    ns.corners = pc;
    ctx.restory(ns, pc);
    fresh.push(ns); uids.push(ns.uid);
    ctx.created.shells.push(ns.uid);
    copyLoads(d, s.uid, ns.uid);
  });
  d.shells.splice(idx + 1, 0, ...fresh);
  if (uids.length > 1) replaceUidRefs(d, "shells", s.uid, uids);
  return uids;
}
function selShells(ctx, sel, what, need = 1) {
  if (!sel.shells.length) throw new Error(`${what}: select at least ${need} shell${need > 1 ? "s" : ""}`);
  if (sel.shells.length < need) throw new Error(`${what}: select at least ${need} shells`);
  return sel.shells.map(u => ctx.shell(u));
}

/* ------------------------------------------------ divide */
const bilinear = (c, u, v) => [0, 1, 2].map(k => (1 - u) * (1 - v) * c[0][k] + u * (1 - v) * c[1][k] + u * v * c[2][k] + (1 - u) * v * c[3][k]);
export function divideQuad(c, n1, n2) {
  const out = [];
  for (let j = 0; j < n2; j++) for (let i = 0; i < n1; i++) {
    const u0 = i / n1, u1 = (i + 1) / n1, v0 = j / n2, v1 = (j + 1) / n2;
    out.push([clean(bilinear(c, u0, v0)), clean(bilinear(c, u1, v0)), clean(bilinear(c, u1, v1)), clean(bilinear(c, u0, v1))]);
  }
  return out;
}
function localAxes(c) {
  const n = unit(newell(c)), e1 = unit(sub(c[1], c[0]));
  return [e1, cross(n, e1), n];
}
function dividePolygon(c, n1, n2) {
  const [e1, e2] = localAxes(c);
  let pieces = [c];
  for (const [axis, n] of [[e1, n1], [e2, n2]]) {
    const proj = c.map(p => dot(p, axis));
    const lo = Math.min(...proj), hi = Math.max(...proj);
    for (let k = 1; k < n; k++) {
      const off = lo + (hi - lo) * k / n;
      const nxt = [];
      for (const pc of pieces) {
        const [f, b] = clipPlane(pc, mul(axis, off), axis);
        for (const q of [b, f]) if (q.length >= 3 && polyArea(q) > 1e-12) nxt.push(q);
      }
      pieces = nxt;
    }
  }
  return pieces;
}
function opShellDivide(ctx, sel, params) {
  const shells = selShells(ctx, sel, "shell_divide");
  const mode = params.mode || "n";
  const divided = {};
  for (const s of shells) {
    const c = s.corners.map(p => p.map(Number));
    let n1, n2;
    if (mode === "n") { n1 = num(params, "n1", 2, { integer: true, lo: 1, hi: 100 }); n2 = num(params, "n2", 2, { integer: true, lo: 1, hi: 100 }); }
    else if (mode === "size") {
      const h = num(params, "max_size", undefined, { positive: true });
      const [e1, e2] = localAxes(c);
      let l1 = Math.max(...c.map(p => dot(p, e1))) - Math.min(...c.map(p => dot(p, e1)));
      let l2 = Math.max(...c.map(p => dot(p, e2))) - Math.min(...c.map(p => dot(p, e2)));
      if (c.length === 4) {
        l1 = Math.max(norm(sub(c[1], c[0])), norm(sub(c[2], c[3])));
        l2 = Math.max(norm(sub(c[3], c[0])), norm(sub(c[2], c[1])));
      }
      n1 = Math.max(1, Math.ceil(l1 / h - 1e-9)); n2 = Math.max(1, Math.ceil(l2 / h - 1e-9));
    } else throw new Error("shell_divide: mode must be 'n' or 'size'");
    if (n1 * n2 <= 1) { ctx.warnings.push(`shell ${s.uid}: nothing to divide`); continue; }
    let pieces;
    if (c.length === 4) pieces = divideQuad(c, n1, n2);
    else {
      if (!isConvex(c)) throw new Error(`shell ${s.uid}: only 4-corner or convex polygon shells can be divided`);
      pieces = dividePolygon(c, n1, n2);
    }
    divided[s.uid] = (s.openings || []).length ? WCH.replaceWithOpenings(ctx, s, pieces, replacePieces) : replacePieces(ctx, s, pieces);   // Wave C
  }
  const ns = newSel(); ns.shells = Object.values(divided).flat();
  return { divided, new_selection: ns };
}

/* ------------------------------------------------ merge */
export function mergePolygons(polys) {
  const pts = new Map();
  for (const pg of polys) for (const p of pg) pts.set(pkey(p), p.slice());
  const edges = [];
  for (const pg of polys) for (let i = 0; i < pg.length; i++) {
    const a = pg[i], b = pg[(i + 1) % pg.length];
    const ab = sub(b, a), L2 = dot(ab, ab);
    const inner = [];
    for (const q of pts.values()) {
      const t = dot(sub(q, a), ab) / L2;
      if (t > 1e-9 && t < 1 - 1e-9 && norm(sub(q, add(a, mul(ab, t)))) < 1e-6) inner.push([t, q]);
    }
    inner.sort((x, y) => x[0] - y[0]);
    const chain = [a, ...inner.map(x => x[1]), b];
    for (let k = 0; k + 1 < chain.length; k++) edges.push([pkey(chain[k]), pkey(chain[k + 1])]);
  }
  const cnt = new Map();
  for (const [a, b] of edges) { const k = a + "|" + b; cnt.set(k, (cnt.get(k) || 0) + 1); }
  if ([...cnt.values()].some(v => v > 1)) throw new Error("shells overlap (an edge is used twice in the same direction)");
  const bnd = edges.filter(([a, b]) => !cnt.has(b + "|" + a));
  const nxt = new Map();
  for (const [a, b] of bnd) { if (nxt.has(a)) throw new Error("the merged region would touch itself at a point"); nxt.set(a, b); }
  const start = bnd[0][0];
  const loop = [start];
  let cur = nxt.get(start);
  while (cur !== start) {
    loop.push(cur);
    if (!nxt.has(cur) || loop.length > bnd.length) throw new Error("could not trace the merged boundary");
    cur = nxt.get(cur);
  }
  if (loop.length !== bnd.length) throw new Error("the shells do not form one region without holes (they must share edges)");
  return dropCollinear(loop.map(k => pts.get(k)));
}
const loadsSig = (d, uid) => shellLoads(d, uid).map(([pn, k, ld]) => {
  const x = { ...ld }; delete x.region_uid;
  return `${pn}|${k}|${JSON.stringify(Object.keys(x).sort().map(kk => [kk, x[kk]]))}`;
}).sort().join("\n");
function opShellMerge(ctx, sel) {
  const shells = selShells(ctx, sel, "shell_merge", 2);
  const s0 = shells[0], n0 = unit(newell(s0.corners));
  const polys = [];
  for (const s of shells) {
    for (const key of ["kind", "behavior", "section"])
      if (s[key] !== s0[key]) throw new Error(`shell_merge: ${s.uid} has a different ${key} than ${s0.uid}`);
    let c = s.corners.map(p => p.map(Number));
    const n = unit(newell(c));
    if (Math.abs(Math.abs(dot(n, n0)) - 1) > 1e-9 || c.some(p => Math.abs(dot(sub(p, s0.corners[0]), n0)) > 1e-6))
      throw new Error(`shell_merge: ${s.uid} is not coplanar with ${s0.uid}`);
    if (dot(n, n0) < 0) c = c.slice().reverse();
    polys.push(c);
    if (loadsSig(ctx.d, s.uid) !== loadsSig(ctx.d, s0.uid))
      throw new Error(`shell_merge: ${s.uid} carries different area loads than ${s0.uid} — make them equal first`);
  }
  let merged, holes = null;   // Wave C: openings kept / a ring of shells → one shell + opening
  if (shells.some(s => (s.openings || []).length)) [merged, holes] = WCH.mergeWithOpenings(shells, polys);
  else {
    try { merged = mergePolygons(polys); }
    catch (e) { if (!/without holes/.test(e.message)) throw e; [merged, holes] = WCH.mergeWithOpenings(shells, polys); if (!holes.length) throw e; }
    const aSum = polys.reduce((a, p) => a + polyArea(p), 0);
    if (holes === null && Math.abs(polyArea(merged) - aSum) > 1e-9 * Math.max(1, aSum)) throw new Error("shell_merge: shells overlap");
  }
  const ks = merged.map(pkey), i0 = ks.indexOf(pkey(s0.corners[0]));
  if (i0 >= 0) merged = merged.slice(i0).concat(merged.slice(0, i0));
  s0.corners = merged.map(clean);
  if (holes !== null) s0.openings = WCH.openingsFor(s0.corners, holes);   // Wave C
  ctx.markModified("shells", s0.uid);
  const gone = shells.slice(1).map(s => s.uid);
  for (const u of gone) replaceUidRefs(ctx.d, "shells", u, [s0.uid]);
  ctx.d.shells = ctx.d.shells.filter(s => !gone.includes(s.uid));
  for (const u of gone) { dropLoads(ctx.d, u); ctx.deleted.shells.push(u); }
  const ns = newSel(); ns.shells = [s0.uid];
  return { merged: { [s0.uid]: shells.map(s => s.uid) }, new_selection: ns };
}

/* ------------------------------------------------ general grids (port of core/grid_general.py) */
export function gridToGlobal(sys) {
  const a = (sys.rotation || 0) * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
  const ox = sys.origin ? +sys.origin[0] : 0, oy = sys.origin ? +sys.origin[1] : 0;
  return (lx, ly) => [ox + lx * c - ly * s, oy + lx * s + ly * c];
}
export function segmentIntersection(p1, p2, q1, q2, eps = 1e-9) {
  const rx = p2[0] - p1[0], ry = p2[1] - p1[1], sx = q2[0] - q1[0], sy = q2[1] - q1[1];
  const den = rx * sy - ry * sx;
  if (Math.abs(den) < 1e-12 * Math.max(1, Math.hypot(rx, ry) * Math.hypot(sx, sy))) return null;
  const qpx = q1[0] - p1[0], qpy = q1[1] - p1[1];
  const t = (qpx * sy - qpy * sx) / den, u = (qpx * ry - qpy * rx) / den;
  if (t >= -eps && t <= 1 + eps && u >= -eps && u <= 1 + eps) return [t, u, [p1[0] + t * rx, p1[1] + t * ry]];
  return null;
}
export function generalLinesGlobal(sys) {
  const T = gridToGlobal(sys);
  return (sys.lines || []).map(ln => ({ label: ln.label, points: [T(+ln.p1[0], +ln.p1[1]), T(+ln.p2[0], +ln.p2[1])] }));
}
export function generalIntersections(sys) {
  const T = gridToGlobal(sys), L = sys.lines || [], out = [];
  for (const ln of L) for (const [k, key] of [[1, "p1"], [2, "p2"]]) out.push({ label: `${ln.label}.${k}`, point: T(+ln[key][0], +ln[key][1]) });
  for (let i = 0; i < L.length; i++) for (let j = i + 1; j < L.length; j++) {
    const hit = segmentIntersection(L[i].p1, L[i].p2, L[j].p1, L[j].p2);
    if (hit) out.push({ label: `${L[i].label}-${L[j].label}`, point: T(hit[2][0], hit[2][1]) });
  }
  return out;
}
/** modeledit.gridSystemGeometry() shape for a general system. */
export function generalGeometry(sys) {
  const g = { kind: "general", name: sys.name, segments: [], circles: [], intersections: [], labels: [] };
  for (const ln of generalLinesGlobal(sys)) {
    const [a, b] = ln.points;
    g.segments.push([a[0], a[1], b[0], b[1]]);
    const L = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    g.labels.push({ x: b[0] + (b[0] - a[0]) / L * 0.7, y: b[1] + (b[1] - a[1]) / L * 0.7, text: ln.label });
  }
  for (const it of generalIntersections(sys)) g.intersections.push(it.point);
  return g;
}
/** Normalise a general system in place (labels, finite end points). */
export function normalizeGeneral(s, i = 0) {
  s.kind = "general";
  if (!(typeof s.name === "string" && s.name.trim())) s.name = `GL${i + 1}`;
  if (!(Array.isArray(s.origin) && s.origin.length === 2 && s.origin.every(isFinite))) s.origin = [0, 0];
  if (!isFinite(s.rotation)) s.rotation = 0;
  const seen = new Set();
  s.lines = (Array.isArray(s.lines) ? s.lines : []).filter(l => l && Array.isArray(l.p1) && Array.isArray(l.p2))
    .map((l, k) => {
      let lab = (typeof l.label === "string" && l.label.trim()) ? l.label.trim() : `L${k + 1}`;
      while (seen.has(lab)) lab += "'";
      seen.add(lab);
      return { label: lab, p1: [+l.p1[0], +l.p1[1]], p2: [+l.p2[0], +l.p2[1]] };
    });
  if (!s.lines.length) s.lines = [{ label: "L1", p1: [0, 0], p2: [6, 0] }];
  if (!Array.isArray(s.x_lines)) s.x_lines = [];
  if (!Array.isArray(s.y_lines)) s.y_lines = [];
  if (!Array.isArray(s.radii)) s.radii = [];
  if (!Array.isArray(s.theta_deg)) s.theta_deg = [];
  // canonical key order = the backend's GridSystem.to_dict (stable echo)
  const o = {};
  for (const k of ["name", "kind", "origin", "rotation", "x_lines", "y_lines", "x_labels", "y_labels", "radii", "theta_deg", "lines"]) o[k] = s[k];
  for (const k of Object.keys(s)) { if (!(k in o)) o[k] = s[k]; delete s[k]; }
  Object.assign(s, o);
  if (!Array.isArray(s.x_labels)) s.x_labels = [];
  if (!Array.isArray(s.y_labels)) s.y_labels = [];
  return s;
}
/** Nearest general-grid snap point to w (or null). */
export function snapGeneral(sys, w) {
  let best = null, bd = Infinity;
  for (const it of generalIntersections(sys)) {
    const d = Math.hypot(it.point[0] - w.x, it.point[1] - w.y);
    if (d < bd) { bd = d; best = it.point; }
  }
  for (const ln of generalLinesGlobal(sys)) {                    // 0.5 m stations along each line
    const [a, b] = ln.points, L = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (L < 1e-9) continue;
    const t = Math.max(0, Math.min(1, ((w.x - a[0]) * (b[0] - a[0]) + (w.y - a[1]) * (b[1] - a[1])) / (L * L)));
    const ts = Math.round(t * L * 2) / 2 / L;
    const tt = Math.max(0, Math.min(1, ts));
    const p = [a[0] + tt * (b[0] - a[0]), a[1] + tt * (b[1] - a[1])];
    const d = Math.hypot(p[0] - w.x, p[1] - w.y);
    if (d < bd) { bd = d; best = p; }
  }
  return best ? { x: best[0], y: best[1], d: bd } : null;
}

/* ------------------------------------------------ chop */
function vplane(a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy);
  return L < 1e-9 ? null : [[+a[0], +a[1], 0], [-dy / L, dx / L, 0]];
}
function gridPlanes(d) {
  const grids = (Array.isArray(d.grid_systems) && d.grid_systems.length) ? d.grid_systems : (d.grid ? [d.grid] : []);
  const planes = [];
  for (const g of grids) {
    const T = gridToGlobal(g);
    if (g.kind === "general") { for (const ln of generalLinesGlobal(g)) planes.push(vplane(ln.points[0], ln.points[1])); continue; }
    if (g.kind === "radial") {
      const rMax = Math.max(...(g.radii || [1]));
      for (const t of g.theta_deg || []) { const a = t * Math.PI / 180; planes.push(vplane(T(0, 0), T(rMax * Math.cos(a), rMax * Math.sin(a)))); }
      continue;
    }
    const xs = g.x_lines || [], ys = g.y_lines || [];
    const y0 = ys.length ? ys[0] : 0, y1 = ys.length ? ys[ys.length - 1] : 0, x0 = xs.length ? xs[0] : 0, x1 = xs.length ? xs[xs.length - 1] : 0;
    for (const x of xs) planes.push(vplane(T(x, y0), T(x, y1 === y0 ? y0 + 1 : y1)));
    for (const y of ys) planes.push(vplane(T(x0, y), T(x1 === x0 ? x0 + 1 : x1, y)));
  }
  return planes.filter(Boolean);
}
function opShellChop(ctx, sel, params) {
  const shells = selShells(ctx, sel, "shell_chop");
  const mode = params.mode || "grid";
  let planes;
  if (mode === "grid") planes = gridPlanes(ctx.d);
  else if (mode === "lines") {
    if (!Array.isArray(params.lines) || !params.lines.length) throw new Error("shell_chop: 'lines' must be a non-empty list of [[x, y], [x, y]]");
    planes = params.lines.map(ln => { if (!Array.isArray(ln) || ln.length !== 2) throw new Error("shell_chop: each line is [[x, y], [x, y]]"); return vplane(ln[0], ln[1]); }).filter(Boolean);
  } else if (mode === "frames") {
    planes = sel.members.map(u => { const m = ctx.member(u); return vplane(m.pi, m.pj); }).filter(Boolean);
    if (!planes.length) throw new Error("shell_chop: select non-vertical frames to chop at");
  } else if (mode === "levels") {
    let zs = params.z;
    if (zs == null) zs = (ctx.d.stories || []).flatMap(s => [+s.elevation, +s.elevation - +s.height]);
    if (!Array.isArray(zs)) throw new Error("shell_chop: 'z' must be a list of elevations");
    planes = [...new Set(zs.map(z => r9(+z)))].sort((a, b) => a - b).map(z => [[0, 0, z], [0, 0, 1]]);
  } else throw new Error("shell_chop: mode must be grid|lines|frames|levels");
  const chopped = {};
  for (const s of shells) {
    const c = s.corners.map(p => p.map(Number));
    if (!isConvex(c)) { ctx.warnings.push(`shell ${s.uid}: concave shells are not chopped`); continue; }
    let pieces = [c];
    for (const [P0, N] of planes) {
      const nxt = [];
      for (const pc of pieces) {
        const [f, b] = clipPlane(pc, P0, N);
        if (f.length >= 3 && b.length >= 3 && polyArea(f) > 1e-9 && polyArea(b) > 1e-9) nxt.push(dropCollinear(b), dropCollinear(f));
        else nxt.push(pc);
      }
      pieces = nxt;
    }
    if (pieces.length > 1) chopped[s.uid] = (s.openings || []).length ? WCH.replaceWithOpenings(ctx, s, pieces, replacePieces) : replacePieces(ctx, s, pieces);   // Wave C
  }
  if (!Object.keys(chopped).length) ctx.warnings.push("shell_chop: no shell was crossed by a cutting plane");
  const ns = newSel(); ns.shells = Object.values(chopped).flat();
  return { chopped, new_selection: ns };
}

/* ------------------------------------------------ expand / shrink */
export function offsetEdges(c, dist, edges) {
  const n = unit(newell(c)), N = c.length;
  const lines = c.map((a, i) => {
    const b = c[(i + 1) % N], D = sub(b, a);
    return [edges.includes(i) ? add(a, mul(unit(cross(D, n)), dist)) : a, D];
  });
  return c.map((p, i) => {
    const im = (i - 1 + N) % N;
    if (!edges.includes(im) && !edges.includes(i)) return p.slice();
    return lineLine(lines[im][0], lines[im][1], lines[i][0], lines[i][1]);
  });
}
function opShellExpand(ctx, sel, params) {
  const shells = selShells(ctx, sel, "shell_expand");
  const dist = num(params, "distance");
  const out = {};
  for (const s of shells) {
    const holes = WCH.holes3(s);   // Wave C: openings stay put
    const c = s.corners.map(p => p.map(Number));
    const edges = params.edge == null ? c.map((_, i) => i) : [num(params, "edge", undefined, { integer: true, lo: 0, hi: c.length - 1 })];
    const nc = offsetEdges(c, dist, edges);
    const a = polyArea(nc);
    if (a < 1e-9 || dot(newell(nc), newell(c)) <= 0) throw new Error(`shell_expand: ${s.uid} would collapse`);
    s.corners = nc.map(clean);
    if (holes.length) { s.openings = WCH.openingsFor(s.corners, holes); WCH.checkInside(s, "shell_expand"); }   // Wave C
    ctx.markModified("shells", s.uid);
    out[s.uid] = { area_before: polyArea(c), area_after: a };
  }
  const ns = newSel(); ns.shells = Object.keys(out);
  return { expanded: out, new_selection: ns };
}

/* ------------------------------------------------ reshape */
function opShellReshape(ctx, sel, params) {
  const shells = selShells(ctx, sel, "shell_reshape");
  if (shells.length !== 1) throw new Error("shell_reshape: select exactly one shell");
  const s = shells[0];
  let c = s.corners.map(p => p.map(Number));
  const holes = WCH.holes3(s);   // Wave C: openings stay put
  const mode = params.mode || "corners";
  if (mode === "corners") {
    if (!Array.isArray(params.corners) || params.corners.length < 3) throw new Error("shell_reshape: 'corners' needs >= 3 points");
    c = params.corners.map(f3);
  } else if (mode === "move") {
    const i = num(params, "index", undefined, { integer: true, lo: 0, hi: c.length - 1 });
    c[i] = f3(params.to);
  } else if (mode === "insert") {
    const i = num(params, "edge", undefined, { integer: true, lo: 0, hi: c.length - 1 });
    const a = c[i], b = c[(i + 1) % c.length];
    c.splice(i + 1, 0, params.point != null ? f3(params.point) : [0, 1, 2].map(k => (a[k] + b[k]) / 2));
  } else if (mode === "delete") {
    if (c.length <= 3) throw new Error("shell_reshape: a shell keeps at least 3 corners");
    c.splice(num(params, "index", undefined, { integer: true, lo: 0, hi: c.length - 1 }), 1);
  } else throw new Error("shell_reshape: mode must be corners|move|insert|delete");
  if (params.on_plane) c = WCH.projectOnPlane(s, c);   // Wave C: elevation / 3D reshaper
  if (new Set(c.map(pkey)).size !== c.length) throw new Error("shell_reshape: duplicate corners");
  s.corners = c.map(clean);
  if (holes.length) { s.openings = WCH.openingsFor(s.corners, holes); WCH.checkInside(s, "shell_reshape"); }   // Wave C
  const st = ctx.storyFor(c.map(p => p[2]));
  if (st !== null) s.story = st;
  ctx.markModified("shells", s.uid);
  const ns = newSel(); ns.shells = [s.uid];
  return { area: polyArea(c), new_selection: ns };
}

/* ------------------------------------------------ ramp */
function story(d, name) {
  const s = (d.stories || []).find(x => x.name === name);
  if (!s) throw new Error(`unknown story '${name}'`);
  return s;
}
function defaultShellSection(d) {
  const names = Object.keys(d.shell_sections || {});
  const n = names.find(x => x.toUpperCase().includes("SLAB"));
  if (n) return n;
  if (!names.length) throw new Error("define a slab section first");
  return names[0];
}
export function rampCorners(p1, p2, width, z1, z2) {
  const dx = p2[0] - p1[0], dy = p2[1] - p1[1], L = Math.hypot(dx, dy);
  if (L < 1e-9) throw new Error("ramp: p1 and p2 coincide");
  const nx = -dy / L * width / 2, ny = dx / L * width / 2;
  return [[p1[0] - nx, p1[1] - ny, z1], [p2[0] - nx, p2[1] - ny, z2], [p2[0] + nx, p2[1] + ny, z2], [p1[0] + nx, p1[1] + ny, z1]].map(clean);
}
function opRamp(ctx, sel, params) {
  const d = ctx.d;
  for (const nm of ["p1", "p2"]) if (!Array.isArray(params[nm]) || params[nm].length < 2) throw new Error(`ramp: '${nm}' must be [x, y]`);
  const width = num(params, "width", undefined, { positive: true });
  const sf = story(d, params.story_from), stt = story(d, params.story_to);
  const z1 = +sf.elevation, z2 = +stt.elevation;
  if (Math.abs(z2 - z1) < 1e-9) throw new Error("ramp: story_from and story_to must differ");
  const section = params.section || defaultShellSection(d);
  if (!(d.shell_sections || {})[section]) throw new Error(`ramp: unknown shell section '${section}'`);
  const behavior = params.behavior || "shell";
  if (!["shell", "membrane"].includes(behavior)) throw new Error("ramp: behavior must be shell|membrane");
  const corners = rampCorners([+params.p1[0], +params.p1[1]], [+params.p2[0], +params.p2[1]], width, z1, z2);
  const uid = ctx.newUid("RAMP");
  d.shells.push({ uid, kind: "slab", behavior, section, corners, mesh_size: +(params.mesh_size ?? 1), story: z2 > z1 ? stt.name : sf.name,
    openings: [], pier: "", area_spring: null, wind_cp: null });
  ctx.created.shells.push(uid);
  const ns = newSel(); ns.shells = [uid];
  return { slope: Math.abs(z2 - z1) / Math.hypot(corners[1][0] - corners[0][0], corners[1][1] - corners[0][1]),
    length: norm(sub(corners[1], corners[0])), area: polyArea(corners), new_selection: ns };
}

/* ------------------------------------------------ wall stacks */
export function wallStackLegs(shape, B, D) {
  const h = B / 2;
  if (shape === "straight") return [[[0, 0], [B, 0]]];
  if (shape === "L") return [[[0, D], [0, 0]], [[0, 0], [B, 0]]];
  if (shape === "C") return [[[B, D], [0, D]], [[0, D], [0, 0]], [[0, 0], [B, 0]]];
  if (shape === "I") return [[[-h, 0], [0, 0]], [[0, 0], [h, 0]], [[0, 0], [0, D]], [[-h, D], [0, D]], [[0, D], [h, D]]];
  if (shape === "T") return [[[0, 0], [0, D]], [[-h, D], [0, D]], [[0, D], [h, D]]];
  if (shape === "box") return [[[0, 0], [B, 0]], [[B, 0], [B, D]], [[B, D], [0, D]], [[0, D], [0, 0]]];
  throw new Error(`wall_stack: shape must be one of ${WALL_SHAPES.join(", ")}`);
}
const nextPier = d => { const used = new Set((d.shells || []).map(s => s.pier)); let k = 1; while (used.has(`P${k}`)) k++; return `P${k}`; };
/** Global plan legs of a stack (for previews): [[[x, y], [x, y]], …]. */
export function wallStackPlan(p) {
  const rot = (p.rotation || 0) * Math.PI / 180, ca = Math.cos(rot), sa = Math.sin(rot);
  const ox = +(p.origin || [0, 0])[0], oy = +(p.origin || [0, 0])[1];
  const G = q => [ox + q[0] * ca - q[1] * sa, oy + q[0] * sa + q[1] * ca];
  return wallStackLegs(p.shape, p.B, p.D || 0).map(([a, b]) => [G(a), G(b)]);
}
function opWallStack(ctx, sel, params) {
  const d = ctx.d;
  const shape = params.shape || "C";
  if (!WALL_SHAPES.includes(shape)) throw new Error(`wall_stack: shape must be one of ${WALL_SHAPES.join(", ")}`);
  const B = num(params, "B", undefined, { positive: true });
  const D = shape === "straight" ? num(params, "D", 0, { lo: 0 }) : num(params, "D", undefined, { positive: true });
  const org = params.origin || [0, 0];
  if (!Array.isArray(org) || org.length < 2) throw new Error("wall_stack: 'origin' must be [x, y]");
  const rotation = num(params, "rotation", 0);
  if (!Array.isArray(params.stories) || !params.stories.length) throw new Error("wall_stack: 'stories' must list story names");
  const stories = params.stories.map(n => story(d, n));
  let section = params.section;
  if (section == null) { const secs = Object.keys(d.shell_sections || {}); section = secs.find(n => n.toUpperCase().includes("WALL")) || secs[0]; }
  if (!(d.shell_sections || {})[section]) throw new Error(`wall_stack: unknown wall section '${section}'`);
  const pier = params.pier || nextPier(d);
  if (typeof pier !== "string" || !pier.trim()) throw new Error("wall_stack: 'pier' must be a label");
  const mesh = +(params.mesh_size ?? 1);
  const plan = wallStackPlan({ shape, B, D, origin: org, rotation });
  const made = [];
  for (const st of stories) {
    const zt = +st.elevation, zb = zt - +st.height;
    for (const [ga, gb] of plan) {
      const uid = ctx.newUid("W");
      d.shells.push({ uid, kind: "wall", behavior: "shell", section,
        corners: [clean([ga[0], ga[1], zb]), clean([gb[0], gb[1], zb]), clean([gb[0], gb[1], zt]), clean([ga[0], ga[1], zt])],
        mesh_size: mesh, story: st.name, openings: [], pier: pier.trim(), area_spring: null, wind_cp: null });
      ctx.created.shells.push(uid);
      made.push(uid);
    }
  }
  const ns = newSel(); ns.shells = made;
  return { pier: pier.trim(), walls: made.length, new_selection: ns };
}

/* ------------------------------------------------ auto relabel */
const r4 = v => { const r = Math.round(v * 1e4) / 1e4; return r === 0 ? 0 : r; };
function planKey(pts, kind) {
  if (kind === "column") return [r4(pts[0][1]), r4(pts[0][0])];
  const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length, cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;
  return [r4(cy), r4(cx)];
}
const cmpKey = (a, b) => a[0] - b[0] || a[1] - b[1];
/** {"members:uid": new uid, …} for the changed uids (Python relabel_map). */
export function relabelMap(d, kinds = RELABEL_KINDS) {
  const groups = Object.fromEntries(RELABEL_KINDS.map(k => [k, []]));
  const sz = (d.stories || []).map(s => [+s.elevation - +s.height, +s.elevation, s.name]);
  const storyOfZ = z => { for (const [zb, zt, n] of sz) if (zb + 1e-6 < z && z <= zt + 1e-6) return n; return ""; };
  for (const m of d.members || []) if (kinds.includes(m.kind) && groups[m.kind]) groups[m.kind].push(["members", m.uid, [m.pi, m.pj], m.story || ""]);
  for (const s of d.shells || []) if (kinds.includes(s.kind) && groups[s.kind]) groups[s.kind].push(["shells", s.uid, s.corners, s.story || ""]);
  if (kinds.includes("link")) for (const l of d.links || []) groups.link.push(["links", l.uid, [l.pi, l.pj], storyOfZ(Math.max(l.pi[2], l.pj[2]))]);
  const out = {}, taken = new Set();
  for (const kind of RELABEL_KINDS) {
    const objs = groups[kind];
    const keys = [];
    for (const o of objs) { const k = planKey(o[2], kind); if (!keys.some(x => x[0] === k[0] && x[1] === k[1])) keys.push(k); }
    keys.sort(cmpKey);
    for (const [coll, uid, pts, st] of objs) {
      const k = planKey(pts, kind);
      const n = keys.findIndex(x => x[0] === k[0] && x[1] === k[1]) + 1;
      let nu = st ? `${PREFIX[kind]}${n}-${st}` : `${PREFIX[kind]}${n}`;
      const base = nu; let c = 2;
      while (taken.has(nu)) nu = `${base}.${c++}`;
      taken.add(nu);
      if (nu !== uid) out[`${coll}:${uid}`] = nu;
    }
  }
  return out;
}
function renameRefs(d, kind, mp) {
  if (!Object.keys(mp).length) return;
  const f = u => (u in mp ? mp[u] : u);
  const key = kind === "members" ? "member_uid" : "region_uid";
  const lists = kind === "members" ? ["member_loads", "member_udls", "thermal_loads"] : kind === "shells" ? ["area_loads", "shell_thermal_loads"] : [];
  for (const pat of Object.values(d.patterns || {})) for (const k of lists) for (const ld of pat[k] || []) ld[key] = f(ld[key]);
  for (const g of Object.values(d.groups || {})) if (g[kind]) g[kind] = g[kind].map(f);
  if (kind === "members" || kind === "shells")
    for (const td of d.tendons || []) { if (typeof td.host === "string") td.host = f(td.host); else if (Array.isArray(td.host)) td.host = td.host.map(f); }
  if (kind === "members")
    for (const coll of ["pushover_cases", "th_cases"])
      for (const c of Object.values(d[coll] || {})) if (c.My && typeof c.My === "object") c.My = Object.fromEntries(Object.entries(c.My).map(([u, v]) => [f(u), v]));
}
function opRelabel(ctx, sel, params) {
  const kinds = params.kinds || RELABEL_KINDS;
  if (!Array.isArray(kinds) || kinds.some(k => !RELABEL_KINDS.includes(k))) throw new Error(`relabel: kinds must be a subset of ${RELABEL_KINDS.join(", ")}`);
  const d = ctx.d, mp = relabelMap(d, kinds);
  const final = new Set();
  for (const coll of ["members", "shells", "links"]) for (const o of d[coll] || []) {
    const u = mp[`${coll}:${o.uid}`] || o.uid;
    if (final.has(u)) throw new Error(`relabel: '${u}' would not be unique; include every kind`);
    final.add(u);
  }
  const by = { members: {}, shells: {}, links: {} };
  for (const [k, nu] of Object.entries(mp)) { const i = k.indexOf(":"); by[k.slice(0, i)][k.slice(i + 1)] = nu; }
  for (const [coll, m] of Object.entries(by)) {
    for (const o of d[coll] || []) if (o.uid in m) o.uid = m[o.uid];
    renameRefs(d, coll, m);
  }
  return { renamed: mp, count: Object.keys(mp).length, new_selection: newSel() };
}

const OPS = { shell_divide: opShellDivide, shell_merge: opShellMerge, shell_chop: opShellChop, shell_expand: opShellExpand,
  shell_reshape: opShellReshape, ramp: opRamp, wall_stack: opWallStack, relabel: opRelabel };

/** POST /api/edit/<op> twin for the Wave B ops → { model, summary }. */
export function wbApplyEdit(model, op, selection, params) {
  if (!OPS[op]) throw new Error(`unknown edit op '${op}'`);
  params = params || {};
  if (typeof params !== "object") throw new Error("'params' must be an object");
  const d = clone(model);
  const ctx = new Ctx(d);
  const sel = normalizeSelection(d, selection);
  const extra = OPS[op](ctx, sel, params);
  return { model: d, summary: ctx.summary(op, extra) };
}
