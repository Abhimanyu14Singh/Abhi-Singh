/* SkyFrame Wave F — pure pick / selection / story-scope geometry (no DOM).

   Unit-tested in node by tests/test_f_select.py and mirrored by
   skyframe/core/pickgeom.py (pick math, joints, story correspondence);
   CONTRACT "Wave F — select and assign in every view".

   Pick math (screen space unless noted):
     distToSeg        point → segment distance
     pointInPoly      even-odd point-in-polygon
     rectHitPoint / rectHitSeg / rectHitPoly
                      WINDOW (crossing = false): the object lies entirely
                      inside the rectangle (every end / vertex inside);
                      CROSSING (crossing = true): any part of it is inside
                      or touches the rectangle (a vertex inside, an edge
                      crossing a rectangle edge, or the rectangle inside a
                      polygon)
     lineHitSeg / lineHitPoly / lineHitPoint
                      Intersecting Line: the picked line crosses the object
     rayTriangle      Möller–Trumbore, two-sided (world space) → t | null
     rayPolygon       ray ∩ planar polygon (any shape, holes optional)
     resolvePick      ETABS priority: joints → line objects → shells
   Model helpers:
     jointKey / modelJoints / storyOfRef / similarGroup / scopeStories /
     correspondingRefs (This Story / Similar Stories / All Stories) and
     replicateChanges (copy an assignment made on the source objects to
     their counterparts on the other stories of the scope). */

export const PICK_TYPES = ["joint", "member", "shell", "link", "spring", "linespring", "tendon"];
export const TYPE_LABEL = { joint: "Joint", member: "Frame", shell: "Shell", link: "Link", spring: "Point spring", linespring: "Line spring", tendon: "Tendon" };
/** Lower = picked first when several objects lie under the cursor. */
/** A point spring sits ON its joint and wins there (its joint assignments resolve
    through it, js/assigndlg.js selectionJoints). */
export const TYPE_PRIORITY = { spring: 0, joint: 1, member: 2, link: 2, linespring: 3, tendon: 3, shell: 5 };
export const GEOM_TOL = 1e-4;

/* ------------------------------------------------------------ 2D primitives */
export function distToSeg(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const l2 = dx * dx + dy * dy;
  let t = l2 ? ((px - ax) * dx + (py - ay) * dy) / l2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

export function pointInPoly(x, y, pts) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const xi = pts[i][0], yi = pts[i][1], xj = pts[j][0], yj = pts[j][1];
    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}

/** Segments AB and CD intersect (touching counts). */
export function segsIntersect(a, b, c, d) {
  const o = (p, q, r) => {
    const v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    return Math.abs(v) < 1e-12 ? 0 : v > 0 ? 1 : -1;
  };
  const on = (p, q, r) => Math.min(p[0], r[0]) - 1e-12 <= q[0] && q[0] <= Math.max(p[0], r[0]) + 1e-12 &&
    Math.min(p[1], r[1]) - 1e-12 <= q[1] && q[1] <= Math.max(p[1], r[1]) + 1e-12;
  const o1 = o(a, b, c), o2 = o(a, b, d), o3 = o(c, d, a), o4 = o(c, d, b);
  if (o1 !== o2 && o3 !== o4) return true;
  if (o1 === 0 && on(a, c, b)) return true;
  if (o2 === 0 && on(a, d, b)) return true;
  if (o3 === 0 && on(c, a, d)) return true;
  if (o4 === 0 && on(c, b, d)) return true;
  return false;
}

/** rect = [x0, y0, x1, y1] (any corner order). */
export const normRect = r => [Math.min(r[0], r[2]), Math.min(r[1], r[3]), Math.max(r[0], r[2]), Math.max(r[1], r[3])];
export const rectHitPoint = (r, x, y) => x >= r[0] && x <= r[2] && y >= r[1] && y <= r[3];
const rectEdges = r => [[[r[0], r[1]], [r[2], r[1]]], [[r[2], r[1]], [r[2], r[3]]], [[r[2], r[3]], [r[0], r[3]]], [[r[0], r[3]], [r[0], r[1]]]];

export function rectHitSeg(r, ax, ay, bx, by, crossing) {
  const ia = rectHitPoint(r, ax, ay), ib = rectHitPoint(r, bx, by);
  if (!crossing) return ia && ib;
  if (ia || ib) return true;
  return rectEdges(r).some(([e0, e1]) => segsIntersect([ax, ay], [bx, by], e0, e1));
}

/** pts = [[x, y], ...] (closed implicitly). */
export function rectHitPoly(r, pts, crossing) {
  if (!pts.length) return false;
  if (!crossing) return pts.every(p => rectHitPoint(r, p[0], p[1]));
  if (pts.some(p => rectHitPoint(r, p[0], p[1]))) return true;
  const E = rectEdges(r);
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    if (E.some(([e0, e1]) => segsIntersect(a, b, e0, e1))) return true;
  }
  return pointInPoly(r[0], r[1], pts);   // rectangle entirely inside the polygon
}

/* Intersecting Line: segment p→q */
export const lineHitSeg = (p, q, a, b) => segsIntersect(p, q, a, b);
export function lineHitPoly(p, q, pts) {
  for (let i = 0; i < pts.length; i++) if (segsIntersect(p, q, pts[i], pts[(i + 1) % pts.length])) return true;
  return false;
}
export const lineHitPoint = (p, q, pt, tol) => distToSeg(pt[0], pt[1], p[0], p[1], q[0], q[1]) <= tol;

/* ------------------------------------------------------------ 3D rays */
const sub3 = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross3 = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

/** Möller–Trumbore ray/triangle (two-sided). Returns the ray parameter t > 0 or null. */
export function rayTriangle(o, d, a, b, c) {
  const e1 = sub3(b, a), e2 = sub3(c, a);
  const p = cross3(d, e2);
  const det = dot3(e1, p);
  if (Math.abs(det) < 1e-14) return null;
  const inv = 1 / det;
  const s = sub3(o, a);
  const u = dot3(s, p) * inv;
  if (u < -1e-12 || u > 1 + 1e-12) return null;
  const q = cross3(s, e1);
  const v = dot3(d, q) * inv;
  if (v < -1e-12 || u + v > 1 + 1e-12) return null;
  const t = dot3(e2, q) * inv;
  return t > 1e-12 ? t : null;
}

/** Newell normal of a planar polygon. */
export function polyNormal(pts) {
  const n = [0, 0, 0];
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    n[0] += (a[1] - b[1]) * (a[2] + b[2]);
    n[1] += (a[2] - b[2]) * (a[0] + b[0]);
    n[2] += (a[0] - b[0]) * (a[1] + b[1]);
  }
  return n;
}

/** Ray ∩ planar polygon (convex or not, optional holes) → t > 0 or null. */
export function rayPolygon(o, d, pts, holes = []) {
  if (!pts || pts.length < 3) return null;
  const n = polyNormal(pts);
  const den = dot3(n, d);
  if (Math.abs(den) < 1e-14 * Math.hypot(...n) * Math.hypot(...d)) return null;
  const t = dot3(n, sub3(pts[0], o)) / den;
  if (!(t > 1e-12)) return null;
  const X = [o[0] + t * d[0], o[1] + t * d[1], o[2] + t * d[2]];
  // project onto the dominant plane
  const ax = Math.abs(n[0]), ay = Math.abs(n[1]), az = Math.abs(n[2]);
  const [i, j] = az >= ax && az >= ay ? [0, 1] : ax >= ay ? [1, 2] : [0, 2];
  const P = p => [p[i], p[j]];
  if (!pointInPoly(X[i], X[j], pts.map(P))) return null;
  for (const h of holes || []) if (h && h.length >= 3 && pointInPoly(X[i], X[j], h.map(P))) return null;
  return t;
}

/* ------------------------------------------------------------ pick resolution */
/** hits: [{ref, d (screen px, 0 for area hits), z (depth, smaller = nearer)}].
    ETABS order: a point spring / the nearest joint within its radius, else the nearest line
    object (frame / link / spring / tendon) within the threshold (ties →
    nearer depth), else the nearest shell under the cursor. */
export function resolvePick(hits) {
  if (!hits || !hits.length) return null;
  const best = [...hits].sort((a, b) =>
    (TYPE_PRIORITY[a.ref.type] ?? 9) - (TYPE_PRIORITY[b.ref.type] ?? 9) ||
    (a.ref.type === "shell" ? 0 : a.d - b.d) || (a.z - b.z) ||
    String(a.ref.uid).localeCompare(String(b.ref.uid)))[0];
  return best.ref;
}

/* ------------------------------------------------------------ joints */
const r6 = v => { const r = Math.round(v * 1e6) / 1e6; return r === 0 ? 0 : r; };
/** Joint key = coordinates rounded to 1e-6 (the Edit utilities' point key). */
export const jointKey = p => `${r6(+p[0])},${r6(+p[1])},${r6(+p[2])}`;
export const jointPoint = key => String(key).split(",").map(Number);
export const refKey = r => `${r.type}:${r.uid}`;

/** Every joint of the model (frame / link ends, shell corners, supports,
    springs, line-spring ends), deduplicated by key, in model order. */
export function modelJoints(m) {
  const out = new Map();
  const add = p => { if (Array.isArray(p) && p.length === 3 && p.every(v => isFinite(+v))) { const k = jointKey(p); if (!out.has(k)) out.set(k, p.map(Number)); } };
  for (const mm of (m && m.members) || []) { add(mm.pi); add(mm.pj); }
  for (const s of (m && m.shells) || []) for (const c of s.corners || []) add(c);
  for (const l of (m && m.links) || []) { add(l.pi); add(l.pj); }
  for (const s of (m && m.supports) || []) add(s.point);
  for (const s of (m && m.spring_supports) || []) add(s.point);
  for (const s of (m && m.line_springs) || []) { add(s.p1); add(s.p2); }
  return out;
}

/* ------------------------------------------------------------ stories / scope */
const near = (a, b, tol = GEOM_TOL) => Math.abs(a - b) <= tol;
export function storyAtZ(m, z) {
  const st = ((m && m.stories) || []).find(s => near(+s.elevation, +z));
  return st ? st.name : null;
}
export function storyOfRef(m, ref) {
  if (!m || !ref) return null;
  if (ref.type === "member") { const x = (m.members || []).find(o => o.uid === ref.uid); return x ? (x.story || storyAtZ(m, Math.max(x.pi[2], x.pj[2]))) : null; }
  if (ref.type === "shell") { const x = (m.shells || []).find(o => o.uid === ref.uid); return x ? (x.story || storyAtZ(m, Math.max(...x.corners.map(c => c[2])))) : null; }
  if (ref.type === "link") { const x = (m.links || []).find(o => o.uid === ref.uid); return x ? storyAtZ(m, Math.max(x.pi[2], x.pj[2])) : null; }
  if (ref.type === "joint" || ref.type === "spring") return storyAtZ(m, jointPoint(ref.uid)[2]);
  return null;
}
/** Stories sharing `story`'s master (core/similar_stories.similar_group). */
export function similarGroup(m, story) {
  const st = ((m && m.stories) || []);
  const s = st.find(x => x.name === story);
  if (!s) return [story];
  const master = s.master ? s.name : (s.similar_to || "");
  if (!master) return [story];
  return st.filter(t => t.name === master || (t.similar_to || "") === master).map(t => t.name);
}
export function scopeStories(m, story, scope) {
  if (scope === "all") return ((m && m.stories) || []).map(s => s.name);
  if (scope === "similar") return similarGroup(m, story);
  return [story];
}

const samePt = (a, b) => near(a[0], b[0]) && near(a[1], b[1]) && near(a[2], b[2]);
const shift = (p, dz) => [p[0], p[1], p[2] + dz];
function sameSet(A, B) {
  if (A.length !== B.length) return false;
  const used = new Array(B.length).fill(false);
  return A.every(a => { const i = B.findIndex((b, k) => !used[k] && samePt(a, b)); if (i < 0) return false; used[i] = true; return true; });
}

/** The counterpart of `ref` on story `target` (null when there is none). */
export function counterpart(m, ref, from, target) {
  const st = (m.stories || []);
  const s0 = st.find(s => s.name === from), s1 = st.find(s => s.name === target);
  if (!s0 || !s1) return null;
  const dz = s1.elevation - s0.elevation;
  if (ref.type === "member") {
    const x = (m.members || []).find(o => o.uid === ref.uid);
    if (!x) return null;
    const a = shift(x.pi, dz), b = shift(x.pj, dz);
    const y = (m.members || []).find(o => o !== x && o.kind === x.kind &&
      ((samePt(o.pi, a) && samePt(o.pj, b)) || (samePt(o.pi, b) && samePt(o.pj, a))));
    return y ? { type: "member", uid: y.uid } : null;
  }
  if (ref.type === "shell") {
    const x = (m.shells || []).find(o => o.uid === ref.uid);
    if (!x) return null;
    const cs = x.corners.map(c => shift(c, dz));
    const y = (m.shells || []).find(o => o !== x && o.kind === x.kind && sameSet(o.corners, cs));
    return y ? { type: "shell", uid: y.uid } : null;
  }
  if (ref.type === "link") {
    const x = (m.links || []).find(o => o.uid === ref.uid);
    if (!x) return null;
    const a = shift(x.pi, dz), b = shift(x.pj, dz);
    const y = (m.links || []).find(o => o !== x && ((samePt(o.pi, a) && samePt(o.pj, b)) || (samePt(o.pi, b) && samePt(o.pj, a))));
    return y ? { type: "link", uid: y.uid } : null;
  }
  if (ref.type === "joint") {
    const p = shift(jointPoint(ref.uid), dz);
    const k = jointKey(p);
    return modelJoints(m).has(k) ? { type: "joint", uid: k } : null;
  }
  if (ref.type === "spring") {
    const p = shift(jointPoint(ref.uid), dz);
    const y = (m.spring_supports || []).find(s => samePt(s.point, p));
    return y ? { type: "spring", uid: ref.uid.includes(",") ? springKeyOf(y.point) : ref.uid } : null;
  }
  return null;
}
const springKeyOf = p => p.map(v => +(+v).toFixed(4)).join(",");   // modeledit.springKey

/** Counterparts of `refs` on the other stories of the scope.
    Returns {pairs: [{src, dst, from, to, dz}], refs: [dst...], missing}. */
export function correspondingRefs(m, refs, scope) {
  const out = { pairs: [], refs: [], missing: 0 };
  if (!m || scope === "one" || !scope) return out;
  const have = new Set((refs || []).map(refKey));
  const seen = new Set();
  const byName = new Map((m.stories || []).map(s => [s.name, s]));
  for (const r of refs || []) {
    const from = storyOfRef(m, r);
    if (!from) continue;
    for (const to of scopeStories(m, from, scope)) {
      if (to === from) continue;
      const dst = counterpart(m, r, from, to);
      if (!dst) { out.missing++; continue; }
      const k = refKey(dst);
      out.pairs.push({ src: r, dst, from, to, dz: byName.get(to).elevation - byName.get(from).elevation });
      if (!have.has(k) && !seen.has(k)) { seen.add(k); out.refs.push(dst); }
    }
  }
  return out;
}

/* ------------------------------------------------------------ assignment replication */
const clone = o => (o === undefined ? undefined : JSON.parse(JSON.stringify(o)));
const SKIP = {
  member: new Set(["uid", "pi", "pj", "story", "curve", "tower", "source_ref"]),
  shell: new Set(["uid", "corners", "story", "openings", "tower", "source_ref"]),
  link: new Set(["uid", "pi", "pj", "tower", "source_ref"]),
};
const LIST = { member: "members", shell: "shells", link: "links" };

/** Model keys an assignment can touch (snapshot before the dialog writes). */
export const ASSIGN_KEYS = ["members", "shells", "links", "supports", "spring_supports", "nodal_masses", "patterns", "groups"];
export function assignSnapshot(m) {
  const o = {};
  for (const k of ASSIGN_KEYS) if (m && m[k] !== undefined) o[k] = clone(m[k]);
  return o;
}

function objDiff(a, b, skip) {
  const ch = {};
  for (const k of new Set([...Object.keys(a || {}), ...Object.keys(b || {})])) {
    if (skip.has(k)) continue;
    if (JSON.stringify((a || {})[k]) !== JSON.stringify((b || {})[k])) ch[k] = b && k in b ? { v: clone(b[k]) } : { del: true };
  }
  return ch;
}
const refsOf = (e, src) => {
  if (!e || typeof e !== "object") return false;
  if (src.type === "member" && e.member_uid === src.uid) return true;
  if (src.type === "shell" && e.region_uid === src.uid) return true;
  if (src.type === "link" && e.link_uid === src.uid) return true;
  if ((src.type === "joint" || src.type === "spring") && Array.isArray(e.point) && samePt(e.point, jointPoint(src.uid))) return true;
  return false;
};
function mapEntry(e, pair) {
  const o = clone(e);
  if (pair.src.type === "member") o.member_uid = pair.dst.uid;
  else if (pair.src.type === "shell") o.region_uid = pair.dst.uid;
  else if (pair.src.type === "link") o.link_uid = pair.dst.uid;
  else if (Array.isArray(o.point)) o.point = shift(o.point, pair.dz).map(v => r6(v));
  return o;
}
function pointList(m, key, pt) { return (m[key] || []).findIndex(s => Array.isArray(s.point) && samePt(s.point, pt)); }

/** Copy what changed on each pair's SOURCE object between `before`
    (assignSnapshot) and the current model onto its DESTINATION object:
    object fields (section, releases, link type / params, …), joint
    restraints / point springs, pattern load entries (added / removed) and
    group membership. Returns the number of destination objects changed. */
export function replicateChanges(m, before, pairs) {
  if (!m || !pairs || !pairs.length) return 0;
  const touched = new Set();
  for (const pair of pairs) {
    const { src, dst } = pair;
    const T = src.type;
    if (LIST[T]) {
      const L = LIST[T];
      const a = ((before[L] || []).find(o => o.uid === src.uid));
      const b = ((m[L] || []).find(o => o.uid === src.uid));
      const d = (m[L] || []).find(o => o.uid === dst.uid);
      if (!a || !b || !d) continue;
      const ch = objDiff(a, b, SKIP[T]);
      for (const [k, c] of Object.entries(ch)) {
        if (c.del) delete d[k]; else d[k] = clone(c.v);
        touched.add(refKey(dst));
      }
    } else if (T === "joint" || T === "spring") {
      const ps = jointPoint(src.uid), pd = shift(ps, pair.dz);
      for (const key of ["supports", "spring_supports", "nodal_masses"]) {
        const ia = (before[key] || []).findIndex(s => samePt(s.point, ps));
        const ib = pointList(m, key, ps);
        const ea = ia >= 0 ? before[key][ia] : null, eb = ib >= 0 ? m[key][ib] : null;
        if (JSON.stringify(ea) === JSON.stringify(eb)) continue;
        const idd = pointList(m, key, pd);
        if (eb) {
          const ne = clone(eb); ne.point = pd.map(v => r6(v));
          if (!m[key]) m[key] = [];
          if (idd >= 0) m[key][idd] = ne; else m[key].push(ne);
        } else if (idd >= 0) m[key].splice(idd, 1);
        touched.add(refKey(dst));
      }
    }
    // pattern load entries referencing the source object
    const pa = before.patterns || {}, pb = m.patterns || {};
    for (const pn of Object.keys(pb)) {
      const P0 = pa[pn] || {}, P1 = pb[pn];
      for (const k of Object.keys(P1)) {
        if (!Array.isArray(P1[k])) continue;
        const A = (Array.isArray(P0[k]) ? P0[k] : []).filter(e => refsOf(e, src)).map(e => JSON.stringify(e));
        const B = P1[k].filter(e => refsOf(e, src)).map(e => JSON.stringify(e));
        const added = B.filter(s => { const i = A.indexOf(s); if (i >= 0) { A.splice(i, 1); return false; } return true; });
        const removed = A;   // what is left of A was removed
        for (const s of removed) {
          const t = JSON.stringify(mapEntry(JSON.parse(s), pair));
          const i = P1[k].findIndex(e => JSON.stringify(e) === t);
          if (i >= 0) { P1[k].splice(i, 1); touched.add(refKey(dst)); }
        }
        for (const s of added) {
          const ne = mapEntry(JSON.parse(s), pair);
          if (!P1[k].some(e => JSON.stringify(e) === JSON.stringify(ne))) { P1[k].push(ne); touched.add(refKey(dst)); }
        }
      }
    }
    // group membership
    const GK = { member: "members", shell: "shells", link: "links" }[T];
    if (GK) for (const [gn, g] of Object.entries(m.groups || {})) {
      const was = (((before.groups || {})[gn] || {})[GK] || []).includes(src.uid);
      const now = (g[GK] || []).includes(src.uid);
      if (was === now) continue;
      g[GK] = g[GK] || [];
      const i = g[GK].indexOf(dst.uid);
      if (now && i < 0) g[GK].push(dst.uid);
      if (!now && i >= 0) g[GK].splice(i, 1);
      touched.add(refKey(dst));
    }
  }
  return touched.size;
}
