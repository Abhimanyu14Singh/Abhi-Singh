/* SkyFrame — PT tendon geometry + mechanics (client side, SI units).
   Mirrors skyframe/core/tendons.py closely enough for the live dialog
   previews and the ?mock=1 backend (CONTRACT "Post-tensioning tendons (as
   loads) and hyperstatic case"):

     segment k:  p(u) = A + u (B - A) + 4 s u (1 - u) d     (d = unit part of
                 drape_dir perpendicular to the chord)
     friction:   P_f = P0 exp(-(mu alpha + k x))
     anchor set: P = min(P_f, 2 P* - P_f) with 2 int max(P_f - P*, 0) dx = delta E A
     "both":     max of the two one-end profiles;  long-term: (1 - f) P

   Also: host chains (ordered frame members / a slab line), the
   (station x, eccentricity e) <-> global point mapping used by the Tendon
   Profile dialog, equivalent loads and a continuous-beam hyperstatic
   estimate for the mock backend. */

export const LOSS_KEYS = ["friction_mu", "wobble_k", "anchor_set", "long_term_fraction"];
export const JACKING_ENDS = ["start", "end", "both"];
export const DEFAULT_N_SUB = 16;
export const DEFAULT_MATERIAL = "A416Gr270";
export const LIBRARY_TENDON_E = { A416Gr270: 196501000, A992Fy50: 199947980, A615Gr60: 199947980, "4000Psi": 24855600 };
export const DOWN = [0, 0, -1];

/* ------------------------------------------------ vectors */
export const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const mul = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
export const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
export const norm = a => Math.hypot(a[0], a[1], a[2]);
const unit = a => { const n = norm(a); return n > 0 ? mul(a, 1 / n) : [0, 0, 0]; };
const angle = (a, b) => {
  const na = norm(a), nb = norm(b);
  if (!(na > 0 && nb > 0)) return 0;
  return Math.atan2(norm(cross(a, b)), dot(a, b));
};
const isNum = v => typeof v === "number" && isFinite(v);

/* ------------------------------------------------ canonical form */
/** Read-only canonical view of a tendon dict (accepts the profile / segments
    shortcuts and fills the documented defaults). Never mutates `t`. */
export function canonical(t) {
  if (!t || typeof t !== "object") return null;
  let points = t.points, sags = t.sags;
  const prof = t.profile != null ? t.profile : t.segments;
  if (prof != null && points == null) {
    const list = Array.isArray(prof) ? prof : [prof];
    points = []; sags = [];
    list.forEach((sg, k) => {
      if (!k) points.push(sg.start);
      points.push(sg.end);
      sags.push(isNum(sg.sag) ? sg.sag : isNum(sg.drape) ? sg.drape : 0);
    });
  }
  points = Array.isArray(points) ? points.map(p => [+p[0], +p[1], +p[2]]) : [];
  if (!Array.isArray(sags)) sags = points.slice(1).map(() => 0);
  const lo = t.losses && typeof t.losses === "object" ? t.losses : {};
  return {
    uid: t.uid,
    points, sags: sags.map(Number),
    drape_dir: Array.isArray(t.drape_dir) ? t.drape_dir.map(Number) : DOWN.slice(),
    material: t.material || DEFAULT_MATERIAL,
    area: +t.area, jacking_stress: +t.jacking_stress,
    jacking_end: JACKING_ENDS.includes(t.jacking_end) ? t.jacking_end : "start",
    losses: Object.fromEntries(LOSS_KEYS.map(k => [k, isNum(lo[k]) ? lo[k] : 0])),
    host: Array.isArray(t.host) ? t.host.slice() : (t.host ? [t.host] : []),
    pattern: t.pattern,
    n_sub: Number.isInteger(t.n_sub) ? t.n_sub : DEFAULT_N_SUB,
  };
}

/** Ordered canonical dict (the exact key order / shape validate() emits). */
export function emitCanonical(c) {
  return {
    uid: c.uid,
    points: c.points.map(p => [p[0], p[1], p[2]]),
    sags: c.sags.slice(),
    drape_dir: c.drape_dir.slice(),
    material: c.material,
    area: c.area,
    jacking_stress: c.jacking_stress,
    jacking_end: c.jacking_end,
    losses: Object.fromEntries(LOSS_KEYS.map(k => [k, c.losses[k]])),
    host: c.host.slice(),
    pattern: c.pattern,
    n_sub: c.n_sub,
  };
}

/* ------------------------------------------------ segments */
export function segments(c) {
  const out = [];
  for (let k = 0; k + 1 < c.points.length; k++) {
    const A = c.points[k], B = c.points[k + 1];
    const ch = sub(B, A), Lc = norm(ch);
    const cu = Lc > 0 ? mul(ch, 1 / Lc) : [1, 0, 0];
    const dd = c.drape_dir || DOWN;
    const d = unit(sub(dd, mul(cu, dot(dd, cu))));
    out.push({ A, B, s: +c.sags[k] || 0, d, Lc, ch });
  }
  return out;
}
export const segP = (g, u) => add(add(g.A, mul(g.ch, u)), mul(g.d, 4 * g.s * u * (1 - u)));
export const segDP = (g, u) => add(g.ch, mul(g.d, 4 * g.s * (1 - 2 * u)));
const segArc = (g, u1, u2, n = 32) => {
  // Simpson on |p'(u)|
  const h = (u2 - u1) / n;
  let acc = 0;
  for (let i = 0; i <= n; i++) {
    const w = (i === 0 || i === n) ? 1 : (i % 2 ? 4 : 2);
    acc += w * norm(segDP(g, u1 + i * h));
  }
  return acc * h / 3;
};

/** Sampled polyline of the whole tendon (for drawing). */
export function samplePolyline(c, perSeg = 16) {
  const out = [];
  segments(c).forEach((g, k) => {
    const n = g.s ? perSeg : 1;
    for (let i = k ? 1 : 0; i <= n; i++) out.push(segP(g, i / n));
  });
  return out;
}

/* ------------------------------------------------ force profile */
function onePiecewiseIntegral(xs, ys) {
  let a = 0;
  for (let i = 1; i < xs.length; i++) a += 0.5 * (ys[i] + ys[i - 1]) * (xs[i] - xs[i - 1]);
  return a;
}
/** Area of max(P_f - P*, 0) (exact for the piecewise-linear interpolation). */
function excessArea(xs, pf, ps) {
  let a = 0;
  for (let i = 1; i < xs.length; i++) {
    const y0 = pf[i - 1] - ps, y1 = pf[i] - ps, dx = xs[i] - xs[i - 1];
    if (y0 >= 0 && y1 >= 0) a += 0.5 * (y0 + y1) * dx;
    else if (y0 > 0 || y1 > 0) {
      const hi = Math.max(y0, y1), lo = Math.min(y0, y1);
      a += 0.5 * hi * dx * hi / (hi - lo);
    }
  }
  return a;
}
function anchorSet(xs, pf, target) {
  // xs ascending from the jacking end, pf decreasing-ish.
  if (!(target > 0)) return { P: pf.slice(), len: 0 };
  const L = xs[xs.length - 1];
  const pmin = Math.min(...pf);
  if (2 * excessArea(xs, pf, pmin) <= target) {
    // the set covers the whole tendon
    const ps = (2 * onePiecewiseIntegral(xs, pf) - target) / (2 * L);
    return { P: pf.map(p => 2 * ps - p), len: L };
  }
  let lo = pmin, hi = Math.max(...pf);
  for (let it = 0; it < 80; it++) {
    const mid = 0.5 * (lo + hi);
    if (2 * excessArea(xs, pf, mid) > target) lo = mid; else hi = mid;
  }
  const ps = 0.5 * (lo + hi);
  let len = L;
  for (let i = 1; i < xs.length; i++) {
    if (pf[i - 1] >= ps && pf[i] < ps) {
      len = xs[i - 1] + (pf[i - 1] - ps) / (pf[i - 1] - pf[i]) * (xs[i] - xs[i - 1]);
      break;
    }
  }
  return { P: pf.map(p => Math.min(p, 2 * ps - p)), len };
}

/** Tendon force profile at the sub-piece boundaries:
    {s, alpha, P, points, P_mid, pieces, P0, length, anchor_set_length}. */
export function forceProfile(c, E) {
  const segs = segments(c);
  const n = Math.max(1, c.n_sub | 0);
  const pieces = [];
  segs.forEach((g, k) => { for (let i = 0; i < n; i++) pieces.push({ seg: k, u1: i / n, u2: (i + 1) / n }); });
  // boundaries (s, alpha) + mid-points (sm, am); a kink adds to the angle
  // just AFTER the joint boundary (same convention as core/tendons.py)
  const s = [0], alpha = [0], sm = [], am = [], points = [segP(segs[0], 0)];
  let prevTan = null;
  for (const pc of pieces) {
    const g = segs[pc.seg];
    const t1 = segDP(g, pc.u1), t2 = segDP(g, pc.u2);
    const a0 = alpha[alpha.length - 1] + (prevTan ? angle(prevTan, t1) : 0);
    const um = 0.5 * (pc.u1 + pc.u2);
    sm.push(s[s.length - 1] + segArc(g, pc.u1, um, 8));
    am.push(a0 + angle(t1, segDP(g, um)));
    s.push(s[s.length - 1] + segArc(g, pc.u1, pc.u2, 8));
    alpha.push(a0 + angle(t1, t2));
    points.push(segP(g, pc.u2));
    prevTan = t2;
  }
  const L = s[s.length - 1], A = alpha[alpha.length - 1];
  const P0 = c.jacking_stress * c.area;
  const mu = c.losses.friction_mu, kw = c.losses.wobble_k;
  const delta = c.losses.anchor_set, f = c.losses.long_term_fraction;
  // dense grid: boundaries + mid-points, ascending
  const grid = [];
  s.forEach((x, i) => { grid.push([x, alpha[i], "b", i]); if (i < sm.length) grid.push([sm[i], am[i], "m", i]); });
  const gx = grid.map(g => g[0]), ga = grid.map(g => g[1]);
  const fromStart = () => {
    const pf = gx.map((x, i) => P0 * Math.exp(-(mu * ga[i] + kw * x)));
    return anchorSet(gx, pf, delta * E * c.area);
  };
  const fromEnd = () => {
    const xs = gx.map(x => L - x).reverse();
    const al = ga.map(a => A - a).reverse();
    const pf = xs.map((x, i) => P0 * Math.exp(-(mu * al[i] + kw * x)));
    const r = anchorSet(xs, pf, delta * E * c.area);
    return { P: r.P.slice().reverse(), len: r.len };
  };
  let pv, setLen = { start: 0, end: 0 };
  if (c.jacking_end === "start") { const r = fromStart(); pv = r.P; setLen.start = r.len; }
  else if (c.jacking_end === "end") { const r = fromEnd(); pv = r.P; setLen.end = r.len; }
  else {
    const a = fromStart(), b = fromEnd();
    pv = a.P.map((v, i) => Math.max(v, b.P[i]));
    setLen = { start: a.len, end: b.len };
  }
  const P = [], P_mid = [];
  grid.forEach((g, i) => { const v = Math.max(pv[i], 0) * (1 - f); if (g[2] === "b") P.push(v); else P_mid.push(v); });
  return { s, alpha, P, points, P_mid, pieces, segs, P0, length: L, anchor_set_length: setLen };
}

/** Equivalent loads on the concrete (self-equilibrated):
    points [{point, force}], lines [{segment, u1, u2, force (per unit chord)}],
    net_force, net_moment, segment_uplift. */
export function equivalentLoads(c, E, prof) {
  prof = prof || forceProfile(c, E);
  const { pieces, segs, P_mid } = prof;
  const T = (g, u, P) => mul(segDP(g, u), P / g.Lc);
  const points = [], lines = [];
  for (let i = 0; i <= pieces.length; i++) {
    const L = i > 0 ? pieces[i - 1] : null, R = i < pieces.length ? pieces[i] : null;
    const tl = L ? T(segs[L.seg], L.u2, P_mid[i - 1]) : [0, 0, 0];
    const tr = R ? T(segs[R.seg], R.u1, P_mid[i]) : [0, 0, 0];
    const F = sub(tr, tl);
    const pt = R ? segP(segs[R.seg], R.u1) : segP(segs[L.seg], L.u2);
    if (norm(F) > 1e-12) points.push({ point: pt, force: F });
  }
  pieces.forEach((pc, i) => {
    const g = segs[pc.seg];
    if (!g.s) return;
    lines.push({ segment: pc.seg, u1: pc.u1, u2: pc.u2, force: mul(g.d, -8 * P_mid[i] * g.s / (g.Lc * g.Lc)) });
  });
  let F = [0, 0, 0], M = [0, 0, 0];
  for (const p of points) { F = add(F, p.force); M = add(M, cross(p.point, p.force)); }
  for (const l of lines) {
    const g = segs[l.segment];
    // integrate the uniform per-chord load over the piece (Simpson in u)
    for (const [w, u] of [[1, l.u1], [4, 0.5 * (l.u1 + l.u2)], [1, l.u2]]) {
      const dF = mul(l.force, g.Lc * (l.u2 - l.u1) * w / 6);
      F = add(F, dF); M = add(M, cross(segP(g, u), dF));
    }
  }
  const uplift = segs.map((g, k) => {
    const pk = P_mid.filter((_, i) => pieces[i].seg === k);
    const avg = pk.reduce((a, b) => a + b, 0) / (pk.length || 1);
    return 8 * avg * g.s / (g.Lc * g.Lc);
  });
  return { points, lines, net_force: F, net_moment: M, segment_uplift: uplift };
}

/** results["tendons"][uid] shape (client-side estimate / mock). */
export function tendonReport(c, E) {
  const prof = forceProfile(c, E);
  const eq = equivalentLoads(c, E, prof);
  return {
    pattern: c.pattern, P0: prof.P0, length: prof.length,
    anchor_set_length: { ...prof.anchor_set_length },
    stations: { s: prof.s, alpha: prof.alpha, P: prof.P, points: prof.points },
    segment_uplift: eq.segment_uplift,
    equivalent_loads: {
      points: eq.points.map(p => ({ point: p.point, force: p.force })),
      lines: eq.lines.map(l => ({ segment: l.segment, u1: l.u1, u2: l.u2, force: l.force })),
      net_force: eq.net_force, net_moment: eq.net_moment,
    },
  };
}

export function materialE(model, name) {
  const m = model && model.materials && model.materials[name];
  if (m && isNum(m.E)) return m.E;
  return LIBRARY_TENDON_E[name] || LIBRARY_TENDON_E[DEFAULT_MATERIAL];
}

/* ------------------------------------------------ host chains */
const nearP = (a, b, tol = 1e-6) => Math.abs(a[0] - b[0]) < tol && Math.abs(a[1] - b[1]) < tol && Math.abs(a[2] - b[2]) < tol;

/** Order frame members into one continuous chain.
    → {kind:"frame", items:[{uid, a, b, L, x0, depth}], L} or {error}. */
export function frameChain(model, uids) {
  const mems = uids.map(u => (model.members || []).find(m => m.uid === u));
  if (mems.some(m => !m)) return { error: "Unknown host member." };
  if (!mems.length) return { error: "Pick at least one host member." };
  const left = mems.slice();
  // start at a member with a free end (an end shared with no other host)
  const shared = (p, self) => left.some(o => o !== self && (nearP(o.pi, p) || nearP(o.pj, p)));
  let first = left.find(m => !shared(m.pi, m) || !shared(m.pj, m)) || left[0];
  let a, b;
  if (!shared(first.pi, first) || shared(first.pj, first)) { a = first.pi; b = first.pj; } else { a = first.pj; b = first.pi; }
  // keep the global direction stable: start from the lower x (then y) end on a lone member
  if (left.length === 1 && (b[0] < a[0] - 1e-9 || (Math.abs(b[0] - a[0]) < 1e-9 && b[1] < a[1] - 1e-9))) [a, b] = [b, a];
  const items = [];
  let x0 = 0;
  const push = (m, pa, pb) => {
    const L = norm(sub(pb, pa));
    items.push({ uid: m.uid, a: pa, b: pb, L, x0, depth: sectionDepth(model, m) });
    x0 += L;
  };
  push(first, a, b);
  left.splice(left.indexOf(first), 1);
  while (left.length) {
    const tail = items[items.length - 1].b;
    const nx = left.find(m => nearP(m.pi, tail) || nearP(m.pj, tail));
    if (!nx) return { error: "Host members must form one continuous line (end to end)." };
    if (nearP(nx.pi, tail)) push(nx, nx.pi, nx.pj); else push(nx, nx.pj, nx.pi);
    left.splice(left.indexOf(nx), 1);
  }
  // reverse a multi-member chain that runs toward -x (display left → right)
  if (items.length > 1) {
    const s0 = items[0].a, s1 = items[items.length - 1].b;
    if (s1[0] < s0[0] - 1e-9 || (Math.abs(s1[0] - s0[0]) < 1e-9 && s1[1] < s0[1] - 1e-9)) {
      const rev = items.reverse().map(it => ({ ...it, a: it.b, b: it.a }));
      let acc = 0;
      for (const it of rev) { it.x0 = acc; acc += it.L; }
      return { kind: "frame", items: rev, L: acc };
    }
  }
  return { kind: "frame", items, L: x0 };
}

/** A straight line through a slab (mid-plane) between plan points a, b. */
export function slabChain(model, uid, a, b) {
  const sh = (model.shells || []).find(s => s.uid === uid);
  if (!sh) return { error: "Unknown host slab." };
  const z = sh.corners.reduce((acc, c) => acc + c[2], 0) / sh.corners.length;
  const A = [a[0], a[1], z], B = [b[0], b[1], z];
  const L = norm(sub(B, A));
  if (!(L > 1e-6)) return { error: "Tendon line through the slab has zero length." };
  const sec = (model.shell_sections || {})[sh.section] || {};
  const depth = isNum(sec.thickness) && sec.thickness > 0 ? sec.thickness : 0.2;
  return { kind: "shell", items: [{ uid, a: A, b: B, L, x0: 0, depth }], L };
}

/** Default slab tendon line: centre line along the slab's longer side. */
export function slabDefaultLine(sh) {
  const xs = sh.corners.map(c => c[0]), ys = sh.corners.map(c => c[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  return (x1 - x0) >= (y1 - y0)
    ? { a: [x0, (y0 + y1) / 2], b: [x1, (y0 + y1) / 2] }
    : { a: [(x0 + x1) / 2, y0], b: [(x0 + x1) / 2, y1] };
}

export function sectionDepth(model, m) {
  const sec = (model.sections || {})[m.section] || {};
  if (isNum(sec.h) && sec.h > 0) return sec.h;
  if (isNum(sec.I33) && isNum(sec.A) && sec.A > 0 && sec.I33 > 0) return Math.sqrt(12 * sec.I33 / sec.A);
  return 0.5;
}

export function chainPoint(chain, x) {
  const its = chain.items;
  let it = its[its.length - 1];
  for (const c of its) if (x <= c.x0 + c.L + 1e-12) { it = c; break; }
  const t = it.L > 0 ? Math.max(0, Math.min(1, (x - it.x0) / it.L)) : 0;
  return add(it.a, mul(sub(it.b, it.a), t));
}
export function chainItemAt(chain, x) {
  for (const c of chain.items) if (x <= c.x0 + c.L + 1e-12) return c;
  return chain.items[chain.items.length - 1];
}

/** Global point → {x, e, lat}: station along the chain, eccentricity along
    the drape direction (+ = below the centroid for drape down) and the
    lateral residual. */
export function projectToChain(chain, p, drape = DOWN) {
  let best = null;
  const du = unit(drape);
  for (const it of chain.items) {
    const ax = sub(it.b, it.a);
    const t = it.L > 0 ? Math.max(0, Math.min(1, dot(sub(p, it.a), ax) / (it.L * it.L))) : 0;
    const q = add(it.a, mul(ax, t));
    const r = sub(p, q);
    const e = dot(r, du);
    const lat = norm(sub(r, mul(du, e)));
    const score = lat + (t <= 0 || t >= 1 ? Math.abs(dot(sub(p, q), unit(ax))) : 0);
    if (!best || score < best.score - 1e-12) best = { x: it.x0 + t * it.L, e, lat, score };
  }
  return best;
}
export const pointAt = (chain, x, e, drape = DOWN) => add(chainPoint(chain, x), mul(unit(drape), e));

/* ------------------------------------------------ mock hyperstatic */
/** Continuous-beam estimate along a frame chain: every interior chain node is
    a support, the chain ends are simple, EI constant. Primary M3 = -P e(x);
    secondary moments are linear between supports with the support values
    that restore zero deflection at the interior supports.
    → {xs, primary, secondary, nodes:[x...], reactions:[R...]} (R = vertical,
    + up, self-equilibrating). */
export function chainHyperstatic(chain, c, prof, scale = 1) {
  const L = chain.L, N = 240;
  const xs = [];
  for (let i = 0; i <= N; i++) xs.push(L * i / N);
  // e(x), P(x) from the stations projected on the chain
  const st = prof.points.map((p, i) => ({ ...projectToChain(chain, p, c.drape_dir), P: prof.P[i] }))
    .sort((a, b) => a.x - b.x);
  const interp = (x, key) => {
    if (!st.length) return 0;
    if (x <= st[0].x) return st[0].x - x > 1e-6 ? 0 : st[0][key];
    if (x >= st[st.length - 1].x) return x - st[st.length - 1].x > 1e-6 ? 0 : st[st.length - 1][key];
    for (let i = 1; i < st.length; i++) if (x <= st[i].x) {
      const t = (x - st[i - 1].x) / ((st[i].x - st[i - 1].x) || 1);
      return st[i - 1][key] + t * (st[i][key] - st[i - 1][key]);
    }
    return 0;
  };
  const M1 = xs.map(x => -scale * interp(x, "P") * interp(x, "e"));
  const knots = [0, ...chain.items.map(it => it.x0 + it.L)];
  const inner = knots.slice(1, -1);
  const hat = (j, x) => {   // hat function at interior knot j (1-based in knots)
    const a = knots[j - 1], b = knots[j], cc = knots[j + 1];
    if (x <= a || x >= cc) return 0;
    return x <= b ? (x - a) / (b - a) : (cc - x) / (cc - b);
  };
  const G = (a, x) => (x <= a ? x * (L - a) / L : a * (L - x) / L);
  const integ = f => { let acc = 0; for (let i = 1; i <= N; i++) acc += 0.5 * (f(i) + f(i - 1)) * (xs[i] - xs[i - 1]); return acc; };
  const n = inner.length;
  let m = [];
  if (n) {
    const Am = inner.map(a => inner.map((_, j) => integ(i => G(a, xs[i]) * hat(j + 1, xs[i]))));
    const bv = inner.map(a => -integ(i => G(a, xs[i]) * M1[i]));
    m = solve(Am, bv);
  }
  const mk = [0, ...m, 0];
  const M2 = xs.map(x => mk.reduce((acc, v, j) => acc + (j && j < mk.length - 1 ? v * hat(j, x) : 0), 0));
  const slopes = chain.items.map((it, j) => (mk[j + 1] - mk[j]) / it.L);
  const R = knots.map((_, i) => (i === 0 ? slopes[0] : i === knots.length - 1 ? -slopes[slopes.length - 1] : slopes[i] - slopes[i - 1]));
  return { xs, primary: M1, secondary: M2, knots, supportMoments: mk, reactions: R };
}

function solve(A, b) {
  const n = b.length, M = A.map((r, i) => [...r, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    [M[c], M[p]] = [M[p], M[c]];
    const d = M[c][c] || 1e-30;
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = M[r][c] / d;
      for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k];
    }
  }
  return M.map((r, i) => r[n] / (r[i] || 1e-30));
}
