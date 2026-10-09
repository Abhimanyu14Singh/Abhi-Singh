/* SkyFrame — mock (?mock=1 / offline) Check Model + stability diagnostics.

   Client-side stand-ins for POST /api/check and POST /api/check/stability
   (CONTRACT "Check Model and stability diagnostics"). Same response SHAPES as
   the backend, a subset of the checks:

     /api/check            JOINT_COINCIDENT, FRAME_ZERO_LENGTH, FRAME_DUPLICATE,
                           FRAME_/SHELL_/LINK_UNCONNECTED,
                           STRUCTURE_UNSUPPORTED_PART, NO_SUPPORTS,
                           JOINT_UNCONNECTED (support off any joint), PATTERN_EMPTY
     /api/check/stability  every connected part without a support is a rigid-body
                           mechanism (UX / UY / UZ); otherwise a plausible
                           "stable" answer; STABILITY_TOO_LARGE above max_dofs.

   Never mutates the model. */

// B9 — FRAME_INTERSECTION / FRAME_JOINT_ON_SPAN (suppressed by the frame auto mesh)
import { frameMeshIssues as b9FrameMeshIssues } from "./framemesh_geom.js";

const KEY_DEC = 6;                                   // engine dedup: 1e-6 rounding
const pkey = p => `${(+p[0]).toFixed(KEY_DEC)},${(+p[1]).toFixed(KEY_DEC)},${(+p[2]).toFixed(KEY_DEC)}`;
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const r6 = v => Math.round(v * 1e6) / 1e6;

class DSU {
  constructor(n) { this.p = Array.from({ length: n }, (_, i) => i); }
  find(i) { while (this.p[i] !== i) { this.p[i] = this.p[this.p[i]]; i = this.p[i]; } return i; }
  union(a, b) { a = this.find(a); b = this.find(b); if (a !== b) this.p[b] = a; }
}

/** Objects (frames / shells / links) and the distinct joints they reference. */
function topology(model) {
  const joints = [];               // [{p, key, objs:Set}]
  const jIndex = new Map();
  const joint = p => {
    const k = pkey(p);
    let i = jIndex.get(k);
    if (i === undefined) { i = joints.length; jIndex.set(k, i); joints.push({ p: [+p[0], +p[1], +p[2]], key: k, objs: new Set() }); }
    return i;
  };
  const objs = [];                 // [{uid, type, joints:[i…]}]
  for (const m of model.members || []) {
    if (!m || !m.pi || !m.pj) continue;
    const o = { uid: m.uid, type: "frame", joints: [joint(m.pi), joint(m.pj)], len: dist(m.pi, m.pj) };
    objs.push(o);
  }
  for (const s of model.shells || []) {
    if (!s || !Array.isArray(s.corners)) continue;
    objs.push({ uid: s.uid, type: "shell", joints: s.corners.map(joint) });
  }
  for (const l of model.links || []) {
    if (!l || !l.pi || !l.pj) continue;
    objs.push({ uid: l.uid, type: "link", joints: [joint(l.pi), joint(l.pj)] });
  }
  objs.forEach((o, oi) => o.joints.forEach(j => joints[j].objs.add(oi)));
  return { joints, jIndex, objs };
}

/** Joint clusters within `tol` (uniform spatial hash, cell = tol). */
function clusters(joints, tol) {
  const dsu = new DSU(joints.length);
  const cell = Math.max(tol, 1e-9);
  const grid = new Map();
  const ck = (a, b, c) => `${a},${b},${c}`;
  joints.forEach((j, i) => {
    const c = j.p.map(v => Math.floor(v / cell));
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
      const lst = grid.get(ck(c[0] + dx, c[1] + dy, c[2] + dz));
      if (lst) for (const k of lst) if (dist(joints[k].p, j.p) <= tol) dsu.union(i, k);
    }
    const key = ck(c[0], c[1], c[2]);
    if (!grid.has(key)) grid.set(key, []);
    grid.get(key).push(i);
  });
  return dsu;
}

/** Supported joint indices (explicit supports with any restraint, grounded
    springs; else the automatic base = joints at the lowest z). */
function supportedJoints(model, topo) {
  const sup = new Set();
  const explicit = (model.supports || []).filter(s => s && s.point);
  const restrained = explicit.filter(s => (s.restraints || []).some(r => +r));
  for (const s of restrained) { const i = topo.jIndex.get(pkey(s.point)); if (i !== undefined) sup.add(i); }
  for (const s of model.spring_supports || []) {
    if (!s || !s.point) continue;
    const i = topo.jIndex.get(pkey(s.point)); if (i !== undefined) sup.add(i);
  }
  for (const m of model.members || [])
    if ((m.foundation_ks || 0) > 0 && (m.foundation_width || 0) > 0) {
      for (const p of [m.pi, m.pj]) { const i = topo.jIndex.get(pkey(p)); if (i !== undefined) sup.add(i); }
    }
  if (!explicit.length && topo.joints.length) {
    // automatic base (the engine's rule when no explicit supports exist)
    let zmin = Infinity;
    for (const j of topo.joints) zmin = Math.min(zmin, j.p[2]);
    topo.joints.forEach((j, i) => { if (Math.abs(j.p[2] - zmin) < 1e-6) sup.add(i); });
  }
  return { sup, explicit, restrained };
}

/** Connected parts (object index groups) that touch no supported joint. */
function unsupportedParts(topo, sup, dsuJ) {
  const n = topo.objs.length;
  const dsu = new DSU(n);
  const byCluster = new Map();
  topo.objs.forEach((o, oi) => o.joints.forEach(j => {
    const c = dsuJ ? dsuJ.find(j) : j;
    if (byCluster.has(c)) dsu.union(byCluster.get(c), oi); else byCluster.set(c, oi);
  }));
  const parts = new Map();
  for (let i = 0; i < n; i++) {
    const r = dsu.find(i);
    if (!parts.has(r)) parts.set(r, []);
    parts.get(r).push(i);
  }
  const out = [];
  for (const idx of parts.values()) {
    const supported = idx.some(oi => topo.objs[oi].joints.some(j => sup.has(j)));
    if (!supported) out.push(idx);
  }
  return out;
}

const SEV_ORDER = { error: 0, warning: 1, info: 2 };

export function mockCheckModel(model, body = {}) {
  const t0 = performance.now();
  const tol = +body.tolerance_m > 0 ? +body.tolerance_m : 0.001;
  const issues = [];
  const add = (severity, code, message, objects, location) =>
    issues.push({ severity, code, message, objects: objects || [], location: location ? location.map(r6) : null });
  const topo = topology(model || {});
  const { joints, objs } = topo;

  // zero-length frames
  for (const o of objs)
    if (o.type === "frame" && o.len < tol)
      add("error", "FRAME_ZERO_LENGTH", `Frame ${o.uid} has length ${o.len.toExponential(2)} m < tolerance`, [o.uid], joints[o.joints[0]].p);

  // coincident joints
  const dsuJ = clusters(joints, tol);
  const groups = new Map();
  joints.forEach((j, i) => { const r = dsuJ.find(i); if (!groups.has(r)) groups.set(r, []); groups.get(r).push(i); });
  for (const g of groups.values()) {
    if (g.length < 2) continue;
    const names = [];
    for (const j of g) for (const oi of joints[j].objs) if (!names.includes(objs[oi].uid)) names.push(objs[oi].uid);
    let dmax = 0;
    for (const a of g) for (const b of g) dmax = Math.max(dmax, dist(joints[a].p, joints[b].p));
    add("warning", "JOINT_COINCIDENT",
      `${g.length} joints within ${dmax.toExponential(2)} m are not merged by the analysis`, names, joints[g[0]].p);
  }

  // duplicate frames (same two tolerance-merged joints)
  const pairs = new Map();
  for (const o of objs) {
    if (o.type !== "frame" || o.len < tol) continue;
    const a = dsuJ.find(o.joints[0]), b = dsuJ.find(o.joints[1]);
    const k = a < b ? `${a}|${b}` : `${b}|${a}`;
    if (!pairs.has(k)) pairs.set(k, []);
    pairs.get(k).push(o);
  }
  for (const lst of pairs.values())
    if (lst.length > 1) {
      const p = joints[lst[0].joints[0]].p, q = joints[lst[0].joints[1]].p;
      add("error", "FRAME_DUPLICATE", `Frames ${lst.map(o => o.uid).join(", ")} join the same two joints`,
        lst.map(o => o.uid), [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2, (p[2] + q[2]) / 2]);
    }

  // supports
  const { sup, restrained } = supportedJoints(model, topo);
  for (const s of restrained)
    if (!topo.jIndex.has(pkey(s.point)))
      add("error", "JOINT_UNCONNECTED", "A support is assigned at a point that is not a joint", [], s.point);
  if (!sup.size && objs.length) {
    add("error", "NO_SUPPORTS", "The model has no restrained joint and no grounded spring", [], null);
  } else {
    for (const part of unsupportedParts(topo, sup, dsuJ)) {
      const first = objs[part[0]];
      const loc = joints[first.joints[0]].p;
      if (part.length === 1) {
        const code = first.type === "frame" ? "FRAME_UNCONNECTED" : first.type === "shell" ? "SHELL_UNCONNECTED" : "LINK_UNCONNECTED";
        add("error", code, `${first.type[0].toUpperCase() + first.type.slice(1)} ${first.uid} is connected to nothing and unsupported`, [first.uid], loc);
      } else {
        add("error", "STRUCTURE_UNSUPPORTED_PART",
          `${part.length} connected objects have no path to any support`, part.map(i => objs[i].uid), loc);
      }
    }
  }

  // empty load patterns
  for (const [name, p] of Object.entries((model && model.patterns) || {})) {
    const hasLoads = Object.values(p || {}).some(v => Array.isArray(v) && v.length) || (+p.self_weight_factor || 0) !== 0;
    if (!hasLoads) add("warning", "PATTERN_EMPTY", `Load pattern ${name} has no loads and no self-weight`, [name], null);
  }

  for (const i of b9FrameMeshIssues(model || {}, tol)) add(i.severity, i.code, i.message, i.objects, i.location);   // B9 crossing / on-span frames
  issues.sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity]);
  const by_code = {};
  for (const i of issues) by_code[i.code] = (by_code[i.code] || 0) + 1;
  const cnt = s => issues.filter(i => i.severity === s).length;
  return {
    issues,
    summary: {
      errors: cnt("error"), warnings: cnt("warning"), info: cnt("info"), ok: cnt("error") === 0,
      by_code, n_joints: joints.length,
      n_frames: objs.filter(o => o.type === "frame").length,
      n_shells: objs.filter(o => o.type === "shell").length,
      n_links: objs.filter(o => o.type === "link").length,
      n_supported_joints: sup.size, tolerance_m: tol,
      elapsed_s: +((performance.now() - t0) / 1000).toFixed(4),
    },
  };
}

export function mockCheckStability(model, body = {}) {
  const t0 = performance.now();
  const maxDofs = Number.isInteger(+body.max_dofs) && +body.max_dofs > 0 ? +body.max_dofs : 3000;
  const topo = topology(model || {});
  const { sup } = supportedJoints(model || {}, topo);
  const nEq = Math.max(0, 6 * (topo.joints.length - sup.size));
  const base = {
    built: true, skipped: false, n_equations: nEq, n_mechanisms: 0, mechanisms: [], unstable_dofs: [],
    condition_number: null, scaled_condition_number: null, scaled_condition_number_nonsingular: null,
    digits_lost: null, max_diagonal_ratio: null, max_diagonal_ratio_at: null,
    n_ill_conditioned_equations: 0, lowest_eigenvalues: [],
    thresholds: { condition_warning: 1e12, diagonal_ratio_warning: 1e8, max_dofs: maxDofs, mechanism_rel_eigenvalue: 1e-12 },
    issues: [],
  };
  const done = o => ({ ...base, ...o, elapsed_s: +((performance.now() - t0) / 1000).toFixed(4) });
  if (nEq > maxDofs) {
    return done({
      stable: null, built: false, skipped: true,
      issues: [{ severity: "info", code: "STABILITY_TOO_LARGE",
        message: `About ${nEq} equations exceed max_dofs = ${maxDofs}; the dense stability analysis was skipped`,
        objects: [], location: null }],
      message: `Skipped: ~${nEq} equations > ${maxDofs}`,
    });
  }
  const parts = sup.size ? unsupportedParts(topo, sup, null) : (topo.objs.length ? [topo.objs.map((_, i) => i)] : []);
  if (parts.length) {
    const mechanisms = [];
    const issues = [];
    for (const part of parts) {
      const js = [...new Set(part.flatMap(oi => topo.objs[oi].joints))];
      const share = +(1 / js.length).toFixed(4);
      for (const dof of ["UX", "UY", "UZ"]) {
        if (mechanisms.length >= 10) break;
        const dofs = js.map(j => ({
          node: j + 1, point: topo.joints[j].p.map(r6), dof, participation: share,
          objects: [...topo.joints[j].objs].map(oi => topo.objs[oi].uid),
        }));
        mechanisms.push({ mode: mechanisms.length + 1, scaled_eigenvalue: 0, dofs });
        issues.push({ severity: "error", code: "STABILITY_MECHANISM",
          message: `Mechanism ${mechanisms.length}: rigid-body ${dof} motion of ${part.length} unsupported object(s)`,
          objects: part.map(oi => topo.objs[oi].uid), location: dofs[0].point });
      }
    }
    const unstable = mechanisms.flatMap(m => m.dofs);
    return done({
      stable: false, n_mechanisms: mechanisms.length, mechanisms, unstable_dofs: unstable, issues,
      scaled_condition_number_nonsingular: 120.5, lowest_eigenvalues: mechanisms.map(() => 0).slice(0, 6),
      message: `Structure is UNSTABLE: ${mechanisms.length} mechanism(s); largest motion at joint (${unstable[0].point.join(", ")}) DOF ${unstable[0].dof}`,
    });
  }
  // plausible stable answer — the max diagonal ratio sits at the highest joint
  let top = 0;
  topo.joints.forEach((j, i) => { if (j.p[2] > topo.joints[top].p[2]) top = i; });
  const tj = topo.joints[top];
  return done({
    stable: true, condition_number: 6.5e3 * Math.max(1, topo.joints.length / 60),
    scaled_condition_number: 96.0, scaled_condition_number_nonsingular: 96.0, digits_lost: 3.8,
    max_diagonal_ratio: 12.75,
    max_diagonal_ratio_at: tj ? { node: top + 1, point: tj.p.map(r6), dof: "UY", objects: [...tj.objs].map(oi => topo.objs[oi].uid), equation: Math.max(0, nEq - 2) } : null,
    lowest_eigenvalues: [0.0256, 0.0275, 0.0276, 0.0756, 0.0761, 0.0764],
    message: "Structure is stable",
  });
}
