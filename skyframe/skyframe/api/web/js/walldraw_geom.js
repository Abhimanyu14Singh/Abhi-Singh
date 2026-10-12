/* SkyFrame D1 — Draw Walls (plan) geometry. Pure functions, no DOM.

   Mirrored line-for-line by skyframe/core/walldraw_geom.py (same formulas,
   same tolerances, same rounding) and cross-checked by
   tests/test_walldraw_geom.py through node.

   Conventions (plan, SI metres, angles in degrees CCW from +X):
     * a drawn line runs a → b; its LEFT normal is (-dy, dx) / L.
     * alignment "left"  → the wall body lies LEFT of the drawn line (the line
       is the wall's right face): centre-line offset +t/2 to the left;
       "right" → −t/2; "centre" → 0. An extra signed offset adds (+ = left).
     * offset polylines are mitred: interior vertices are the intersection of
       the two neighbouring offset lines (parallel / reversed neighbours fall
       back to the plain perpendicular offset).
     * walls are ShellRegion(kind="wall") corner lists in the SAME order as
       modeledit.addWall: [p1 bottom, p2 bottom, p2 top, p1 top].
   Coordinates leaving this module are rounded to 1e-9 m (r9) so grid points
   stay exact (6, not 5.999999999). */

export const TOL = 1e-6;

/** Round to 1e-9 (half away from zero like Math.round on positives; −0 → 0). */
export function r9(v) {
  const r = Math.round(v * 1e9) / 1e9;
  return r === 0 ? 0 : r;
}
const P = (x, y) => [r9(x), r9(y)];
const same = (a, b, tol = TOL) => Math.abs(a[0] - b[0]) <= tol && Math.abs(a[1] - b[1]) <= tol;

/** Signed left offset of the wall centre line from the drawn line. */
export function alignOffset(align, thickness, extra = 0) {
  const h = (+thickness || 0) / 2;
  const base = align === "left" ? h : align === "right" ? -h : 0;
  return base + (+extra || 0);
}

/** Drop consecutive duplicate points (and the closing duplicate when closed). */
export function cleanPolyline(pts, closed = false) {
  const out = [];
  for (const p of pts || []) if (!out.length || !same(out[out.length - 1], p)) out.push([+p[0], +p[1]]);
  if (closed && out.length > 1 && same(out[0], out[out.length - 1])) out.pop();
  return out;
}

/** Length (m) and direction (deg, [0, 360)) of a → b. */
export function segInfo(a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  let ang = Math.atan2(dy, dx) * 180 / Math.PI;
  if (ang < 0) ang += 360;
  if (ang >= 360 - 1e-9) ang = 0;
  return { len: Math.hypot(dx, dy), ang };
}

/** Offset a polyline by the signed left distance s (mitred joins). */
export function offsetPolyline(pts, s, closed = false) {
  const q = cleanPolyline(pts, closed);
  const n = q.length;
  if (n < 2 || Math.abs(s) < 1e-12) return q.map(p => P(p[0], p[1]));
  const nseg = closed ? n : n - 1;
  const segs = [];
  for (let i = 0; i < nseg; i++) {
    const a = q[i], b = q[(i + 1) % n];
    const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy);
    const ux = dx / L, uy = dy / L;
    segs.push({ a: [a[0] - uy * s, a[1] + ux * s], u: [ux, uy] });
  }
  const meet = (s1, s2, vtx) => {
    const cr = s1.u[0] * s2.u[1] - s1.u[1] * s2.u[0];
    if (Math.abs(cr) < 1e-9) return [vtx[0] - s2.u[1] * s, vtx[1] + s2.u[0] * s];
    const dx = s2.a[0] - s1.a[0], dy = s2.a[1] - s1.a[1];
    const t = (dx * s2.u[1] - dy * s2.u[0]) / cr;
    return [s1.a[0] + s1.u[0] * t, s1.a[1] + s1.u[1] * t];
  };
  const out = [];
  for (let k = 0; k < n; k++) {
    let p;
    if (!closed && k === 0) p = segs[0].a;
    else if (!closed && k === n - 1) { const sg = segs[n - 2]; p = [q[k][0] - sg.u[1] * s, q[k][1] + sg.u[0] * s]; }
    else p = meet(segs[(k - 1 + nseg) % nseg], segs[k % nseg], q[k]);
    out.push(P(p[0], p[1]));
  }
  return out;
}

/** Points along a → b split where the (finite) lines cross it strictly inside.
    lines: [[[ax, ay], [bx, by]], …]. Parallel / collinear lines never split. */
export function splitAtLines(a, b, lines, tol = TOL) {
  const rx = b[0] - a[0], ry = b[1] - a[1], L = Math.hypot(rx, ry);
  if (!(L > tol)) return [P(a[0], a[1]), P(b[0], b[1])];
  const ts = [];
  for (const ln of lines || []) {
    const c = ln[0], d = ln[1];
    const sx = d[0] - c[0], sy = d[1] - c[1], M = Math.hypot(sx, sy);
    if (!(M > tol)) continue;
    const den = rx * sy - ry * sx;
    if (Math.abs(den) <= 1e-9 * L * M) continue;
    const qx = c[0] - a[0], qy = c[1] - a[1];
    const t = (qx * sy - qy * sx) / den, u = (qx * ry - qy * rx) / den;
    if (t * L <= tol || (1 - t) * L <= tol) continue;
    if (u * M < -tol || (u - 1) * M > tol) continue;
    ts.push(t);
  }
  ts.sort((x, y) => x - y);
  const out = [P(a[0], a[1])];
  let last = 0;
  for (const t of ts) {
    if ((t - last) * L <= tol) continue;
    out.push(P(a[0] + rx * t, a[1] + ry * t));
    last = t;
  }
  out.push(P(b[0], b[1]));
  return out;
}

/** Wall panels (plan segments) of a drawn polyline.
    opts: { closed, offset (signed left, m), lines (split lines or null) }. */
export function wallPanels(pts, opts = {}) {
  const closed = !!opts.closed;
  const q = cleanPolyline(pts, closed);
  if (q.length < 2 || (closed && q.length < 3)) return [];
  const o = offsetPolyline(q, +opts.offset || 0, closed);
  const nseg = closed ? o.length : o.length - 1;
  const out = [];
  for (let i = 0; i < nseg; i++) {
    const a = o[i], b = o[(i + 1) % o.length];
    const parts = opts.lines && opts.lines.length ? splitAtLines(a, b, opts.lines) : [a, b];
    for (let k = 0; k + 1 < parts.length; k++)
      if (Math.hypot(parts[k + 1][0] - parts[k][0], parts[k + 1][1] - parts[k][1]) > TOL) out.push([parts[k], parts[k + 1]]);
  }
  return out;
}

/** Four wall corners (addWall order) between story levels zb..zt with
    bottom / top offsets (+ = up). null when the wall has no height or run. */
export function wallCorners(p1, p2, zb, zt, botOff = 0, topOff = 0) {
  const z0 = r9(zb + (+botOff || 0)), z1 = r9(zt + (+topOff || 0));
  if (!(z1 - z0 > TOL)) return null;
  if (!(Math.hypot(p2[0] - p1[0], p2[1] - p1[1]) > TOL)) return null;
  return [[p1[0], p1[1], z0], [p2[0], p2[1], z0], [p2[0], p2[1], z1], [p1[0], p1[1], z1]];
}

/** Orthogonal lock: keep the dominant axis of from → p. */
export function orthoPoint(from, p) {
  const dx = p[0] - from[0], dy = p[1] - from[1];
  return Math.abs(dx) >= Math.abs(dy) ? [p[0], from[1]] : [from[0], p[1]];
}

/** Typed point while drawing (display units × f = metres):
      "6"        length along the cursor direction (from → toward)
      "6<90"     length and angle (deg CCW from +X)
      "@3,4"     relative offset dx, dy
      "12,6"     absolute x, y
    Returns [x, y] (r9) or null when the text is not a usable point. */
export function parseTyped(text, from, toward, f = 1) {
  const s = String(text == null ? "" : text).trim().replace(/\s+/g, "");
  if (!s || !from) return null;
  const num = v => (/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(v) ? parseFloat(v) : NaN);
  let x, y;
  if (s[0] === "@") {
    const parts = s.slice(1).split(",");
    if (parts.length !== 2) return null;
    const dx = num(parts[0]), dy = num(parts[1]);
    if (!isFinite(dx) || !isFinite(dy)) return null;
    x = from[0] + dx * f; y = from[1] + dy * f;
  } else if (s.includes("<")) {
    const parts = s.split("<");
    if (parts.length !== 2) return null;
    const L = num(parts[0]), A = num(parts[1]);
    if (!isFinite(L) || !isFinite(A) || L <= 0) return null;
    const a = A * Math.PI / 180;
    x = from[0] + L * f * Math.cos(a); y = from[1] + L * f * Math.sin(a);
  } else if (s.includes(",")) {
    const parts = s.split(",");
    if (parts.length !== 2) return null;
    const ax = num(parts[0]), ay = num(parts[1]);
    if (!isFinite(ax) || !isFinite(ay)) return null;
    x = ax * f; y = ay * f;
  } else {
    const L = num(s);
    if (!isFinite(L) || L <= 0) return null;
    let ux = 1, uy = 0;
    if (toward) {
      const dx = toward[0] - from[0], dy = toward[1] - from[1], d = Math.hypot(dx, dy);
      if (d > 1e-12) { ux = dx / d; uy = dy / d; }
    }
    x = from[0] + L * f * ux; y = from[1] + L * f * uy;
  }
  if (Math.hypot(x - from[0], y - from[1]) <= TOL) return null;
  return P(x, y);
}

/** Distance from p to segment a–b. */
export function distToSeg(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy;
  let t = l2 ? ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

/** Index of the segment nearest to p within tol (first wins ties), or −1. */
export function nearestSeg(segs, p, tol) {
  let best = -1, bd = Infinity;
  (segs || []).forEach((s, i) => {
    const d = distToSeg(p, s[0], s[1]);
    if (d <= tol && (best < 0 || d < bd - 1e-12)) { bd = d; best = i; }
  });
  return best;
}

/** Segments with both ends inside the box [[x0, y0], [x1, y1]] (any corner order). */
export function segsInBox(segs, box) {
  const xa = Math.min(box[0][0], box[1][0]) - TOL, xb = Math.max(box[0][0], box[1][0]) + TOL;
  const ya = Math.min(box[0][1], box[1][1]) - TOL, yb = Math.max(box[0][1], box[1][1]) + TOL;
  const inB = p => p[0] >= xa && p[0] <= xb && p[1] >= ya && p[1] <= yb;
  return (segs || []).filter(s => inB(s[0]) && inB(s[1]));
}

/** Orthogonal grid: one segment per grid line between consecutive crossing
    lines (the panels Quick Draw Walls places). xs / ys are local line
    coordinates; T maps local → global (identity when omitted). */
export function gridSegments(xs, ys, T) {
  const tf = T || ((x, y) => [x, y]);
  const X = [...(xs || [])].map(Number).sort((a, b) => a - b), Y = [...(ys || [])].map(Number).sort((a, b) => a - b);
  const out = [];
  const add = (a, b) => { const p = tf(a[0], a[1]), q = tf(b[0], b[1]); out.push([P(p[0], p[1]), P(q[0], q[1])]); };
  for (const x of X) for (let j = 0; j + 1 < Y.length; j++) add([x, Y[j]], [x, Y[j + 1]]);
  for (const y of Y) for (let i = 0; i + 1 < X.length; i++) add([X[i], y], [X[i + 1], y]);
  return out;
}

/** Plan segments of the beams of one story (walls drawn under beams). */
export function beamSegs(model, story) {
  const out = [];
  for (const m of (model && model.members) || []) {
    if (m.kind !== "beam" || m.story !== story || m.curve) continue;
    const a = [m.pi[0], m.pi[1]], b = [m.pj[0], m.pj[1]];
    if (Math.hypot(b[0] - a[0], b[1] - a[1]) > TOL) out.push([P(a[0], a[1]), P(b[0], b[1]), m.uid]);
  }
  return out;
}

/** Stories a draw on `story` replicates to: "one" | "similar" | "all"
    (the similar group = the master + every story similar to it, bottom → top). */
export function scopeStories(stories, story, scope) {
  const ss = stories || [];
  if (scope === "all") return ss.map(s => s.name);
  if (scope !== "similar") return [story];
  const s = ss.find(x => x.name === story);
  if (!s) return [story];
  const master = s.master ? s.name : (s.similar_to || "");
  if (!master) return [story];
  return ss.filter(t => t.name === master || (t.similar_to || "") === master).map(t => t.name);
}

/** Next free pier label P1, P2, … (same rule as Draw Wall Stack). */
export function nextPier(shells) {
  const used = new Set((shells || []).map(s => s.pier).filter(Boolean));
  let k = 1;
  while (used.has(`P${k}`)) k++;
  return `P${k}`;
}

/** Order-free corner key (dedupe walls drawn twice). */
export function cornerKey(corners) {
  return corners.map(c => c.map(v => r9(v).toFixed(6)).join(",")).sort().join(";");
}

/** Walls for panels × stories: [{story, corners}] skipping duplicates of
    `existing` (shells) and of each other. stories: [{name, elevation, height}]. */
export function buildWalls(panels, stories, names, botOff = 0, topOff = 0, existing = []) {
  const keys = new Set((existing || []).filter(s => s.kind === "wall").map(s => s.story + "|" + cornerKey(s.corners)));
  const out = [];
  for (const name of names || []) {
    const st = (stories || []).find(s => s.name === name);
    if (!st) continue;
    const zb = st.elevation - st.height, zt = st.elevation;
    for (const [a, b] of panels || []) {
      const c = wallCorners(a, b, zb, zt, botOff, topOff);
      if (!c) continue;
      const k = name + "|" + cornerKey(c);
      if (keys.has(k)) continue;
      keys.add(k);
      out.push({ story: name, corners: c });
    }
  }
  return out;
}

export default { TOL, r9, alignOffset, cleanPolyline, segInfo, offsetPolyline, splitAtLines, wallPanels, wallCorners,
  orthoPoint, parseTyped, distToSeg, nearestSeg, segsInBox, gridSegments, beamSegs, scopeStories, nextPier, cornerKey, buildWalls };
