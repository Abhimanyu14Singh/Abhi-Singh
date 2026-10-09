/* SkyFrame — frame auto-mesh + output-station geometry (client mirror of
   skyframe/core/framemesh.py, CONTRACT "Frame auto-mesh and output stations").

   Pure functions over the SI model dict; used by the dialogs' previews
   (js/framemesh.js), the mock backend (js/mock_fmesh.js) and the mock
   Check Model hint. No DOM. */

export const AUTO_MESH_TOL = 1e-3;          // m (= Check Model default tolerance)
export const DEFAULT_N_STATIONS = 11;
const END_TOL = 1e-6;
const EPS_STATION = 1e-9;

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
export const memberLength = m => dist(m.pi, m.pj);

/** Canonical auto-mesh dict (all four keys) or null. */
export function canonAutoMesh(d) {
  if (d == null || typeof d !== "object") return null;
  const ml = d.max_length, ns = d.min_segments;
  return {
    at_intermediate_joints: !!d.at_intermediate_joints,
    at_intersections: !!d.at_intersections,
    max_length: (ml == null || !isFinite(ml)) ? null : +ml,
    min_segments: (ns == null || !isFinite(ns)) ? null : Math.round(+ns),
  };
}
/** True when an auto-mesh dict divides anything. */
export const autoMeshActive = am => !!am && (am.at_intermediate_joints || am.at_intersections ||
  am.max_length != null || (am.min_segments || 1) > 1);

/** Effective options of a member (its own dict replaces the model default). */
export function effectiveAutoMesh(model, m) {
  const am = canonAutoMesh(m.auto_mesh != null ? m.auto_mesh : model.frame_auto_mesh);
  return autoMeshActive(am) ? am : null;
}
const meshable = m => (m.axial_limit || "both") === "both";

export function connectsIntersection(model, a, b) {
  if (!(meshable(a) && meshable(b))) return false;
  return [a, b].some(m => { const am = effectiveAutoMesh(model, m); return !!(am && am.at_intersections); });
}
export function connectsJointsOnSpan(model, m) {
  const am = effectiveAutoMesh(model, m);
  return !!(am && am.at_intermediate_joints && meshable(m));
}

/** Closest points of segments p1q1 / p2q2 → {s, t, c1, c2}. */
export function segClosest(p1, q1, p2, q2) {
  const d1 = sub(q1, p1), d2 = sub(q2, p2), r = sub(p1, p2);
  const a = dot(d1, d1), e = dot(d2, d2), f = dot(d2, r), c = dot(d1, r), b = dot(d1, d2);
  const den = a * e - b * b;
  const cl = v => v < 0 ? 0 : v > 1 ? 1 : v;
  let s = den > 1e-14 * a * e ? cl((b * f - c * e) / den) : 0;
  let t = (b * s + f) / e;
  if (t < 0) { t = 0; s = cl(-c / a); } else if (t > 1) { t = 1; s = cl((b - c) / a); }
  return { s, t, c1: [0, 1, 2].map(k => p1[k] + s * d1[k]), c2: [0, 1, 2].map(k => p2[k] + t * d2[k]) };
}

function jointPoints(model) {
  const pts = [], seen = new Set();
  const push = p => {
    if (!Array.isArray(p) || p.length !== 3) return;
    const k = p.map(v => (+v).toFixed(9)).join(",");
    if (!seen.has(k)) { seen.add(k); pts.push(p.map(Number)); }
  };
  for (const m of model.members || []) { push(m.pi); push(m.pj); }
  for (const l of model.links || []) { push(l.pi); push(l.pj); }
  for (const s of model.supports || []) push(s.point);
  for (const s of model.spring_supports || []) push(s.point);
  for (const r of model.shells || []) for (const c of r.corners || []) push(c);
  return pts;
}

/** Per member uid: sorted interior auto-mesh cuts [{t, p, why}] (t = m from end i).
    why ∈ "joint" | "intersection" | "division". */
export function autoMeshPoints(model) {
  const members = model.members || [];
  const eff = new Map(members.map(m => [m.uid, effectiveAutoMesh(model, m)]));
  const cuts = new Map();
  if (![...eff.values()].some(Boolean)) return cuts;
  const tol = AUTO_MESH_TOL;
  const add = (m, t, p, why) => {
    if (!meshable(m)) return;
    const L = memberLength(m);
    if (t <= END_TOL || t >= L - END_TOL) return;
    if (!cuts.has(m.uid)) cuts.set(m.uid, []);
    cuts.get(m.uid).push({ t, p, why });
  };
  let joints = null;
  for (const m of members) {
    const am = eff.get(m.uid);
    if (!am || !am.at_intermediate_joints) continue;
    joints = joints || jointPoints(model);
    const L = memberLength(m);
    if (L < 1e-12) continue;
    const u = sub(m.pj, m.pi).map(v => v / L);
    for (const p of joints) {
      const t = dot(sub(p, m.pi), u);
      if (t <= tol || t >= L - tol) continue;
      const foot = [0, 1, 2].map(k => m.pi[k] + t * u[k]);
      if (dist(p, foot) <= tol) add(m, t, p, "joint");
    }
  }
  const inter = members.filter(m => { const am = eff.get(m.uid); return am && am.at_intersections; });
  if (inter.length) {
    const done = new Set();
    for (const a of inter) for (const b of members) {
      if (a === b) continue;
      const key = a.uid < b.uid ? a.uid + "|" + b.uid : b.uid + "|" + a.uid;
      if (done.has(key)) continue;
      done.add(key);
      const La = memberLength(a), Lb = memberLength(b);
      const da = sub(a.pj, a.pi), db = sub(b.pj, b.pi);
      const cr = [da[1] * db[2] - da[2] * db[1], da[2] * db[0] - da[0] * db[2], da[0] * db[1] - da[1] * db[0]];
      if (Math.hypot(...cr) / (La * Lb) < 1e-6) continue;
      const { s, t, c1, c2 } = segClosest(a.pi, a.pj, b.pi, b.pj);
      if (dist(c1, c2) > tol) continue;
      if (s * La <= tol || (1 - s) * La <= tol || t * Lb <= tol || (1 - t) * Lb <= tol) continue;
      if (!(meshable(a) && meshable(b))) continue;
      const P = [0, 1, 2].map(k => 0.5 * (c1[k] + c2[k]));
      add(a, s * La, P, "intersection");
      add(b, t * Lb, P, "intersection");
    }
  }
  for (const m of members) {
    const am = eff.get(m.uid);
    if (!am || !meshable(m)) continue;
    const ns = am.min_segments || 1, ml = am.max_length;
    if (ns <= 1 && ml == null) continue;
    const L = memberLength(m);
    const at = t => [0, 1, 2].map(k => m.pi[k] + (m.pj[k] - m.pi[k]) * t / L);
    const hard = (cuts.get(m.uid) || []).slice();
    for (let i = 1; i < ns; i++) hard.push({ t: i * L / ns, p: at(i * L / ns), why: "division" });
    hard.sort((x, y) => x.t - y.t);
    const merged = [];
    for (const c of hard) if (!merged.length || c.t - merged[merged.length - 1].t > END_TOL) merged.push(c);
    if (ml != null && ml > 0) {
      const ts = [0, ...merged.map(c => c.t), L];
      for (let i = 0; i + 1 < ts.length; i++) {
        const n = Math.ceil((ts[i + 1] - ts[i]) / ml - 1e-9);
        for (let j = 1; j < n; j++) {
          const t = ts[i] + j * (ts[i + 1] - ts[i]) / n;
          merged.push({ t, p: at(t), why: "division" });
        }
      }
    }
    if (merged.length) cuts.set(m.uid, merged);
  }
  for (const [uid, lst] of cuts) {
    lst.sort((x, y) => x.t - y.t);
    const clean = [];
    for (const c of lst) if (!clean.length || c.t - clean[clean.length - 1].t > END_TOL) clean.push(c);
    if (clean.length) cuts.set(uid, clean); else cuts.delete(uid);
  }
  return cuts;
}

/** Concentrated force / moment positions on a member (any pattern), m from i. */
export function concentratedLoadXs(model, m) {
  const L = memberLength(m), xs = [];
  for (const p of Object.values(model.patterns || {}))
    for (const ml of p.member_loads || [])
      if (ml.member_uid === m.uid && (ml.kind === "point" || ml.kind === "moment"))
        xs.push(Math.min(Math.max(+ml.a || 0, 0), 1) * L);
  return xs;
}

/** Output station list (m from end i). segEnds = analysis segment cut
    distances (interior). Default (no option) = 11 equal stations. */
export function stationXs(model, m, segEnds = [], os = m.output_stations) {
  const L = memberLength(m);
  if (os == null) return Array.from({ length: DEFAULT_N_STATIONS }, (_, k) => k * L / (DEFAULT_N_STATIONS - 1));
  const n = os.max_spacing != null ? Math.max(1, Math.ceil(L / os.max_spacing - 1e-9))
    : Math.max(1, Math.round(os.min_number) - 1);
  let xs = Array.from({ length: n + 1 }, (_, k) => k * L / n);
  xs = xs.concat(segEnds, concentratedLoadXs(model, m)).map(x => Math.min(Math.max(x, 0), L)).sort((a, b) => a - b);
  const tol = EPS_STATION * Math.max(1, L);
  const out = [];
  for (const x of xs) if (!out.length || x - out[out.length - 1] > tol) out.push(x);
  out[0] = 0;
  if (L - out[out.length - 1] <= tol) out[out.length - 1] = L;
  return out;
}

/** Linear interpolation of vals(xs) at xq. */
export function valueAt(xs, vals, xq) {
  if (!xs.length) return 0;
  if (xq <= xs[0]) return vals[0];
  for (let i = 1; i < xs.length; i++)
    if (xq <= xs[i]) {
      const d = xs[i] - xs[i - 1];
      return d > 0 ? vals[i - 1] + (vals[i] - vals[i - 1]) * (xq - xs[i - 1]) / d : vals[i];
    }
  return vals[vals.length - 1];
}

/** Mock Check Model issues for crossing frames / ends on a span (the two
    codes the auto mesh suppresses). Mirrors skyframe/core/checks.py. */
export function frameMeshIssues(model, tol = AUTO_MESH_TOL) {
  const out = [];
  const ms = (model.members || []).filter(m => memberLength(m) > tol);
  const fmt = p => "(" + p.map(v => (+v).toFixed(3)).join(", ") + ")";
  for (let i = 0; i < ms.length; i++) for (let j = i + 1; j < ms.length; j++) {
    const A = ms[i], B = ms[j];
    const La = memberLength(A), Lb = memberLength(B);
    const { s, t, c1, c2 } = segClosest(A.pi, A.pj, B.pi, B.pj);
    if (dist(c1, c2) > tol) continue;
    const endA = s * La <= tol || (1 - s) * La <= tol;
    const endB = t * Lb <= tol || (1 - t) * Lb <= tol;
    if (endA && endB) continue;                       // shared joint
    const da = sub(A.pj, A.pi), db = sub(B.pj, B.pi);
    const cr = [da[1] * db[2] - da[2] * db[1], da[2] * db[0] - da[0] * db[2], da[0] * db[1] - da[1] * db[0]];
    if (Math.hypot(...cr) / (La * Lb) < 1e-6) continue;   // collinear: overlap check elsewhere
    const loc = [0, 1, 2].map(k => +(0.5 * (c1[k] + c2[k])).toFixed(6));
    if (!endA && !endB) {
      if (connectsIntersection(model, A, B)) continue;
      out.push({ severity: "warning", code: "FRAME_INTERSECTION",
        message: `Frames ${A.uid} and ${B.uid} cross at ${fmt(loc)} without a shared joint (they are NOT connected there; divide both frames at the intersection)`,
        objects: [A.uid, B.uid], location: loc });
    } else {
      const [span, end] = !endA ? [A, B] : [B, A];
      if (connectsJointsOnSpan(model, span)) continue;
      out.push({ severity: "warning", code: "FRAME_JOINT_ON_SPAN",
        message: `An end of frame ${end.uid} lies on the span of frame ${span.uid} at ${fmt(loc)} but ${span.uid} is not divided there (no connectivity)`,
        objects: [end.uid, span.uid], location: loc });
    }
  }
  return out;
}
