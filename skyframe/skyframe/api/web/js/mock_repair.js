/* SkyFrame — mock backend for POST /api/repair (?mock=1).

   Client-side approximation of skyframe/core/repair.py (CONTRACT "Model
   repair"): same fix ids, canonical order, request validation and response
   shape { model, report }. The geometry edits run through the shared Edit
   port (js/edit_geom.js: merge_joints / divide / delete keep loads and
   references consistent). Simplifications vs the backend: shell corners snap
   to frame / link ends only, unconnected-joint detection treats any point on
   a shell's plane inside its outline as a mesh node, mechanisms come from the
   mock stability check. Pure: never mutates its input; a model with nothing
   to fix comes back byte-identical. Throws Error(msg) where the backend
   answers 400. */

import { applyEdit as mrApplyEdit, pkey as mrPkey } from "./edit_geom.js";
import { mockCheckModel as mrCheckModel, mockCheckStability as mrCheckStability } from "./mock_check.js";

export const REPAIR_FIXES = ["merge_joints", "remove_zero_length", "remove_duplicates",
  "snap_shell_corners", "split_at_joints", "split_at_crossings", "remove_unconnected_joints",
  "report_mechanisms"];
export const DEFAULT_FIXES = ["merge_joints", "remove_zero_length", "remove_duplicates",
  "snap_shell_corners", "split_at_joints", "remove_unconnected_joints"];
export const FIX_TITLES = {
  merge_joints: "Merge coincident / near joints",
  remove_zero_length: "Remove zero-length frames",
  remove_duplicates: "Remove duplicate / overlapping objects",
  snap_shell_corners: "Snap shell corners to joints",
  split_at_joints: "Split frames at joints on their span",
  split_at_crossings: "Split frames at frame crossings",
  remove_unconnected_joints: "Remove unconnected joints",
  report_mechanisms: "Report mechanisms (no change)",
};
export const FIX_HINTS = {
  merge_joints: "joints closer than the tolerance become one; every reference follows",
  remove_zero_length: "frames shorter than the tolerance; their two joints are merged",
  remove_duplicates: "same joints / collinear overlaps; the loaded copy is kept",
  snap_shell_corners: "shell corners move onto nearby frame ends so meshes connect",
  split_at_joints: "divide frames at joints lying on them; member loads carry over",
  split_at_crossings: "divide both frames where they cross without a joint",
  remove_unconnected_joints: "supports / masses / loads at points that connect to nothing",
  report_mechanisms: "list the near-zero-stiffness mechanisms; the model is not changed",
};
const MAX_PASSES = 20;

const clone = o => JSON.parse(JSON.stringify(o));
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const mul = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = a => Math.sqrt(dot(a, a));
const dist = (a, b) => norm(sub(a, b));
const lerp = (a, b, t) => [0, 1, 2].map(k => a[k] + (b[k] - a[k]) * t);
const r9 = v => { const r = Math.round(v * 1e9) / 1e9; return r === 0 ? 0 : r; };
const r6 = v => { const r = Math.round(v * 1e6) / 1e6; return r === 0 ? 0 : r; };
const clean = p => p.map(v => r9(+v));
const keyPt = k => k.split(",").map(Number);
const fmtP = p => `(${p.map(v => +(+v).toPrecision(4)).join(", ")})`;
const len = m => dist(m.pi, m.pj);
const emptyLists = () => ({ members: [], shells: [], links: [], points: [] });

function pointDist(p, a, b) {          // {d, t} with t clamped to [0, 1]
  const ab = sub(b, a), L2 = dot(ab, ab);
  const t = L2 <= 0 ? 0 : Math.max(0, Math.min(1, dot(sub(p, a), ab) / L2));
  return { d: dist(p, add(a, mul(ab, t))), t };
}
function segClosest(p1, q1, p2, q2) {  // Ericson RTCD 5.1.9 (checks._seg_closest)
  const d1 = sub(q1, p1), d2 = sub(q2, p2), r = sub(p1, p2);
  const a = dot(d1, d1), e = dot(d2, d2), f = dot(d2, r), c = dot(d1, r), b = dot(d1, d2);
  const den = a * e - b * b;
  const cl = v => (v < 0 ? 0 : v > 1 ? 1 : v);
  let s = den > 1e-14 * a * e ? cl((b * f - c * e) / den) : 0;
  let t = (b * s + f) / e;
  if (t < 0) { t = 0; s = cl(-c / a); } else if (t > 1) { t = 1; s = cl((b - c) / a); }
  return { s, t, c1: add(p1, mul(d1, s)), c2: add(p2, mul(d2, t)) };
}

function pointLists(d) {
  const out = [];
  for (const k of ["supports", "spring_supports", "nodal_masses", "joint_diaphragms", "joint_panel_zones"])
    if (Array.isArray(d[k])) out.push([d[k], k]);
  for (const p of Object.values(d.patterns || {}))
    for (const k of ["nodal_loads", "ground_displacements", "joint_temperatures"])
      if (Array.isArray(p[k])) out.push([p[k], k]);
  return out;
}
function jointCoords(d, withRecords = true) {
  const out = new Map();
  const put = p => { const k = mrPkey(p); if (!out.has(k)) out.set(k, p.map(Number)); };
  for (const m of d.members || []) { put(m.pi); put(m.pj); }
  for (const s of d.shells || []) s.corners.forEach(put);
  for (const l of d.links || []) { put(l.pi); put(l.pj); }
  if (withRecords) for (const [lst] of pointLists(d)) for (const r of lst) if (r && r.point) put(r.point);
  return out;
}
function unsplittable(d, m) {
  if (String(m.axial_limit || "both") !== "both") return "axial-only (Truss) member";
  if (m.curve) return "curved member";
  const sec = (d.sections || {})[m.section];
  if (sec && (sec.kind || "prismatic") !== "prismatic") return "nonprismatic section";
  if (typeof m.hinges === "string" && m.hinges && m.hinges !== "none") return `automatic hinges (${m.hinges})`;
  return null;
}
function loadCount(d, uid, keys, field) {
  let n = 0;
  for (const p of Object.values(d.patterns || {})) for (const k of keys) n += (p[k] || []).filter(x => x[field] === uid).length;
  return n;
}

class Rep {
  constructor(fix) { this.fix = fix; this.actions = []; this.warnings = []; this.created = emptyLists(); this.deleted = emptyLists(); this.modified = { members: [], shells: [], links: [] }; }
  add(message, objects = [], location = null, extra = {}) {
    this.actions.push(Object.assign({ fix: this.fix, message, objects: [...new Set(objects)].slice(0, 50),
      location: location ? location.map(r6) : null }, extra));
  }
  absorb(summary) {             // edit_geom summary → created / deleted / modified / warnings
    if (!summary) return;
    for (const k of ["members", "shells", "links", "points"]) {
      for (const u of (summary.created || {})[k] || []) this.created[k].push(u);
      for (const u of (summary.deleted || {})[k] || []) this.deleted[k].push(u);
    }
    for (const k of ["members", "shells", "links"]) for (const u of (summary.modified || {})[k] || []) this.modified[k].push(u);
    for (const w of summary.warnings || []) this.warnings.push(w);
  }
  report(changed, extra = {}) {
    return Object.assign({ fix: this.fix, title: FIX_TITLES[this.fix], changed: !!changed, count: this.actions.length,
      actions: this.actions, warnings: this.warnings, created: this.created, deleted: this.deleted,
      modified: this.modified }, extra);
  }
}

/** Divide member uid at [[t, point]...] via Edit > Divide (distance from i), then put
    the piece ends exactly on the points. Returns { model, uids }. */
function splitAt(d, uid, cuts, tol, rep) {
  const m0 = d.members.find(m => m.uid === uid);
  const L = len(m0);
  const kept = [];
  for (const [t, p] of [...cuts].sort((a, b) => a[0] - b[0])) {
    if (!(t > 1e-9 && t < 1 - 1e-9) || t * L <= tol || (1 - t) * L <= tol) continue;
    if (kept.length && (t - kept[kept.length - 1][0]) * L <= tol) continue;
    kept.push([t, p]);
  }
  if (!kept.length) return { model: d, uids: [uid] };
  let cur = d;
  const uids = [uid];
  for (const [t] of [...kept].reverse()) {
    const res = mrApplyEdit(cur, "divide", { members: [uid] }, { mode: "distance", distance: t * L, from: "i" });
    const made = ((res.summary.divided || {})[uid] || []).slice(1);
    if (!made.length) continue;
    cur = res.model;
    rep.absorb(res.summary);
    uids.splice(1, 0, made[0]);
  }
  if (uids.length === kept.length + 1)
    kept.forEach(([, p], k) => {
      const q = clean(p);
      cur.members.find(m => m.uid === uids[k]).pj = [...q];
      cur.members.find(m => m.uid === uids[k + 1]).pi = [...q];
    });
  return { model: cur, uids };
}

/* ------------------------------------------------ fixes */
function fixMergeJoints(d, tol) {
  const rep = new Rep("merge_joints");
  const res = mrApplyEdit(d, "merge_joints", {}, { tolerance: tol });
  const merged = res.summary.merged || [];
  if (!merged.length) return { model: d, report: rep.report(false) };
  rep.absorb(res.summary);
  for (const c of merged) rep.add(`Merged ${c.from.length} joint(s) into ${fmtP(c.to)}`, [], c.to, { to: c.to, merged: c.from });
  for (const u of rep.deleted.members) rep.add(`Deleted frame ${u} (zero-length, degenerate or duplicate after the merge)`, [u], null, { removed: [u] });
  for (const u of rep.deleted.shells) rep.add(`Deleted shell ${u} (zero-length, degenerate or duplicate after the merge)`, [u], null, { removed: [u] });
  return { model: res.model, report: rep.report(true) };
}

function fixRemoveZeroLength(d, tol) {
  const rep = new Rep("remove_zero_length");
  let cur = d, changed = false;
  for (let pass = 0; pass < MAX_PASSES; pass++) {
    const z = cur.members.find(m => len(m) < tol);
    if (!z) break;
    const L = len(z), nl = loadCount(cur, z.uid, ["member_loads", "member_udls", "thermal_loads"], "member_uid");
    let msg = `Deleted zero-length frame ${z.uid} (${+L.toPrecision(3)} m)`;
    if (mrPkey(z.pi) !== mrPkey(z.pj)) {
      const res = mrApplyEdit(cur, "merge_joints", { points: [z.pi, z.pj] }, { tolerance: L + 1e-9 });
      cur = res.model; rep.absorb(res.summary);
      msg += "; its two joints merged";
    }
    if (cur.members.some(m => m.uid === z.uid)) {
      const res = mrApplyEdit(cur, "delete", { members: [z.uid] }, {});
      cur = res.model; rep.absorb(res.summary);
    }
    if (nl) { msg += `; ${nl} load(s) on it dropped`; rep.warnings.push(`frame ${z.uid}: ${nl} load(s) dropped with the zero-length frame`); }
    rep.add(msg, [z.uid], z.pi, { loads_dropped: nl });
    changed = true;
  }
  return { model: cur, report: rep.report(changed) };
}

function overlapCuts(d, tol) {
  const live = d.members.filter(m => len(m) >= tol), cuts = {};
  for (let a = 0; a < live.length; a++) for (let b = a + 1; b < live.length; b++) {
    const A = live[a], B = live[b];
    const da = sub(A.pj, A.pi), db = sub(B.pj, B.pi), La = norm(da), Lb = norm(db);
    if (norm(cross(da, db)) / (La * Lb) >= 1e-6) continue;
    if (norm(cross(sub(B.pi, A.pi), da)) / La > tol) continue;
    const ua = mul(da, 1 / La);
    const [t0, t1] = [dot(sub(B.pi, A.pi), ua), dot(sub(B.pj, A.pi), ua)].sort((x, y) => x - y);
    if (Math.min(La, t1) - Math.max(0, t0) <= tol) continue;
    for (const [X, Y, LX] of [[A, B, La], [B, A, Lb]]) for (const e of ["pi", "pj"]) {
      const { d: dd, t } = pointDist(Y[e], X.pi, X.pj);
      if (dd <= tol && t * LX > tol && (1 - t) * LX > tol) (cuts[X.uid] = cuts[X.uid] || []).push([t, Y[e].map(Number)]);
    }
  }
  return cuts;
}

function replaceGroupRefs(d, kind, old, keep) {
  for (const g of Object.values(d.groups || {})) {
    const lst = g[kind] || [];
    if (!lst.includes(old)) continue;
    const out = [];
    for (const u of lst) { const v = u === old ? keep : u; if (!out.includes(v)) out.push(v); }
    g[kind] = out;
  }
}

function fixRemoveDuplicates(d, tol) {
  const rep = new Rep("remove_duplicates");
  let cur = d, changed = false;
  for (let pass = 0; pass < MAX_PASSES; pass++) {
    let did = false;
    const cuts = overlapCuts(cur, tol);
    for (const uid of cur.members.map(m => m.uid).filter(u => cuts[u])) {
      const m = cur.members.find(x => x.uid === uid);
      const why = unsplittable(cur, m);
      if (why) { rep.warnings.push(`frame ${uid}: overlapping but not divided (${why})`); continue; }
      const r = splitAt(cur, uid, cuts[uid], tol, rep);
      if (r.uids.length > 1) { cur = r.model; did = true; rep.add(`Divided overlapping frame ${uid} into ${r.uids.join(", ")}`, r.uids, m.pi, { pieces: r.uids }); }
    }
    for (const [coll, kind, keys, field, label] of [
      ["members", "members", ["member_loads", "member_udls", "thermal_loads"], "member_uid", "frame"],
      ["shells", "shells", ["area_loads", "shell_thermal_loads"], "region_uid", "shell"]]) {
      const groups = new Map();
      for (const o of cur[coll] || []) {
        const pts = coll === "members" ? [o.pi, o.pj] : o.corners;
        const ks = pts.map(mrPkey);
        if (coll === "members" && ks[0] === ks[1]) continue;
        const gk = ks.sort().join("|");
        if (!groups.has(gk)) groups.set(gk, []);
        groups.get(gk).push(o);
      }
      for (const os of groups.values()) {
        if (os.length < 2) continue;
        let keep = os[0];
        for (const o of os) if (loadCount(cur, o.uid, keys, field) > loadCount(cur, keep.uid, keys, field)) keep = o;
        const drop = os.filter(o => o !== keep).map(o => o.uid);
        const nl = drop.reduce((s, u) => s + loadCount(cur, u, keys, field), 0);
        const work = clone(cur);
        for (const u of drop) replaceGroupRefs(work, kind, u, keep.uid);
        const res = mrApplyEdit(work, "delete", { [kind]: drop }, {});
        cur = res.model; rep.absorb(res.summary);
        let msg = `Deleted duplicate ${label}(s) ${drop.join(", ")} (kept ${keep.uid})`;
        if (nl) { msg += `; ${nl} load(s) on the deleted copies dropped`; rep.warnings.push(`duplicate ${label}(s) ${JSON.stringify(drop)}: ${nl} load(s) dropped`); }
        const loc = coll === "members" ? lerp(keep.pi, keep.pj, 0.5) : keep.corners[0];
        rep.add(msg, [keep.uid, ...drop], loc, { kept: keep.uid, removed: drop, loads_dropped: nl });
        did = true;
      }
    }
    if (!did) break;
    changed = true;
  }
  return { model: cur, report: rep.report(changed) };
}

function shellOk(corners) {
  const ks = corners.map(mrPkey);
  if (new Set(ks).size !== ks.length) return false;
  if (corners.length !== 4) return true;
  const c = corners.map(p => p.map(Number));
  const n = cross(sub(c[1], c[0]), sub(c[2], c[0])), nn = norm(n);
  return nn >= 1e-12 && Math.abs(dot(sub(c[3], c[0]), n)) / nn <= 1e-6;
}

function remapPoints(d, mapping) {     // key → coords on shell corners + point records
  const mp = p => { const q = mapping.get(mrPkey(p)); return q ? [...q] : p; };
  const touched = [];
  for (const s of d.shells || []) {
    const nc = s.corners.map(mp);
    if (nc.some((p, i) => p !== s.corners[i])) { s.corners = nc; touched.push(s.uid); }
  }
  for (const [lst] of pointLists(d)) for (const r of lst) if (r && r.point) r.point = mp(r.point);
  for (const g of Object.values(d.groups || {})) if (Array.isArray(g.points)) g.points = g.points.map(mp);
  return touched;
}

function fixSnapShellCorners(d, tol) {
  const rep = new Rep("snap_shell_corners");
  const fl = new Map();
  for (const coll of ["members", "links"]) for (const o of d[coll] || []) for (const e of ["pi", "pj"]) if (!fl.has(mrPkey(o[e]))) fl.set(mrPkey(o[e]), o[e].map(Number));
  const corners = new Map();
  for (const s of d.shells || []) for (const c of s.corners) if (!fl.has(mrPkey(c))) corners.set(mrPkey(c), c.map(Number));
  const mapping = new Map();
  for (const [k, p] of corners) {
    let best = null;
    for (const [q, qp] of fl) { const dd = dist(p, qp); if (dd <= tol && (!best || dd < best[0])) best = [dd, q, qp]; }
    if (best) mapping.set(k, best[2]);
  }
  for (let pass = 0; pass < MAX_PASSES; pass++) {
    let bad = false;
    for (const s of d.shells || []) {
      const ks = s.corners.map(mrPkey);
      if (!ks.some(k => mapping.has(k))) continue;
      if (!shellOk(s.corners.map(c => mapping.get(mrPkey(c)) || c))) {
        for (const k of ks) mapping.delete(k);
        rep.warnings.push(`shell ${s.uid}: corners not snapped (the snap would warp, fold or collapse it)`);
        bad = true;
      }
    }
    if (!bad) break;
  }
  if (!mapping.size) return { model: d, report: rep.report(false) };
  const out = clone(d);
  rep.modified.shells.push(...remapPoints(out, mapping));
  for (const [k, q] of [...mapping].sort()) {
    const p = keyPt(k);
    const owners = (out.shells || []).filter(s => s.corners.some(c => mrPkey(c) === mrPkey(q))).map(s => s.uid);
    rep.add(`Snapped shell corner ${fmtP(p)} to frame/link end ${fmtP(q)} (${+dist(p, q).toPrecision(3)} m)`, owners, q, { from: p, to: q });
  }
  return { model: out, report: rep.report(true) };
}

function fixSplitAtJoints(d, tol) {
  const rep = new Rep("split_at_joints");
  let cur = d, changed = false;
  const warned = new Set();
  for (let pass = 0; pass < MAX_PASSES; pass++) {
    const joints = [...jointCoords(cur).entries()];
    const plan = {};
    for (const m of cur.members) {
      const L = len(m);
      if (L <= 2 * tol) continue;
      const hits = [];
      for (const [k, p] of joints) {
        const kp = keyPt(k);
        const { d: dd, t } = pointDist(kp, m.pi, m.pj);
        if (dd <= tol && t * L > tol && (1 - t) * L > tol) hits.push([t, p]);
      }
      if (!hits.length) continue;
      const why = unsplittable(cur, m);
      if (why) { if (!warned.has(m.uid)) { warned.add(m.uid); rep.warnings.push(`frame ${m.uid}: joint(s) on its span left unconnected (${why})`); } continue; }
      plan[m.uid] = hits;
    }
    let did = false;
    for (const uid of cur.members.map(m => m.uid).filter(u => plan[u])) {
      const m0 = cur.members.find(m => m.uid === uid);
      const mid = lerp(m0.pi, m0.pj, 0.5);
      const r = splitAt(cur, uid, plan[uid], tol, rep);
      if (r.uids.length > 1) {
        cur = r.model; did = true;
        const at = r.uids.slice(1).map(u => cur.members.find(m => m.uid === u).pi);
        rep.add(`Divided frame ${uid} at ${at.length} joint(s) into ${r.uids.join(", ")}`, r.uids, mid, { pieces: r.uids, at });
      }
    }
    if (!did) break;
    changed = true;
  }
  return { model: cur, report: rep.report(changed) };
}

function fixSplitAtCrossings(d, tol) {
  const rep = new Rep("split_at_crossings");
  let cur = d, changed = false;
  for (let pass = 0; pass < MAX_PASSES; pass++) {
    const live = cur.members.filter(m => len(m) > 2 * tol);
    const plan = {}, pairs = [];
    for (let a = 0; a < live.length; a++) for (let b = a + 1; b < live.length; b++) {
      const A = live[a], B = live[b];
      if ([0, 1, 2].some(k => Math.min(A.pi[k], A.pj[k]) - tol > Math.max(B.pi[k], B.pj[k])
        || Math.min(B.pi[k], B.pj[k]) - tol > Math.max(A.pi[k], A.pj[k]))) continue;
      const da = sub(A.pj, A.pi), db = sub(B.pj, B.pi), La = norm(da), Lb = norm(db);
      if (norm(cross(da, db)) / (La * Lb) < 1e-6) continue;
      const { s, t, c1, c2 } = segClosest(A.pi, A.pj, B.pi, B.pj);
      if (dist(c1, c2) > tol) continue;
      if (!(s * La > tol && (1 - s) * La > tol && t * Lb > tol && (1 - t) * Lb > tol)) continue;
      const why = unsplittable(cur, A) || unsplittable(cur, B);
      if (why) { rep.warnings.push(`frames ${A.uid} / ${B.uid}: crossing left unconnected (${why})`); continue; }
      const X = clean(mul(add(c1, c2), 0.5));
      (plan[A.uid] = plan[A.uid] || []).push([s, X]);
      (plan[B.uid] = plan[B.uid] || []).push([t, X]);
      pairs.push([A.uid, B.uid, X]);
    }
    if (!pairs.length) break;
    for (const uid of cur.members.map(m => m.uid).filter(u => plan[u])) cur = splitAt(cur, uid, plan[uid], tol, rep).model;
    for (const [ua, ub, X] of pairs) rep.add(`Divided crossing frames ${ua} and ${ub} at ${fmtP(X)}`, [ua, ub], X, { at: X });
    changed = true;
  }
  return { model: cur, report: rep.report(changed) };
}

const RECORD_LABEL = { supports: "support", spring_supports: "spring support", nodal_masses: "nodal mass",
  joint_diaphragms: "joint diaphragm", joint_panel_zones: "joint panel zone", nodal_loads: "nodal load",
  ground_displacements: "ground displacement", joint_temperatures: "joint temperature" };

function onShell(p, s, tol) {            // point on the shell plane, inside its outline (≈ mesh node)
  const c = s.corners.map(q => q.map(Number));
  if (c.length < 3) return false;
  let n = [0, 0, 0];
  for (let i = 0; i < c.length; i++) n = add(n, cross(c[i], c[(i + 1) % c.length]));
  const nn = norm(n);
  if (nn < 1e-12) return false;
  n = mul(n, 1 / nn);
  if (Math.abs(dot(sub(p, c[0]), n)) > tol) return false;
  const ref = Math.abs(n[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
  let u = cross(n, ref); u = mul(u, 1 / norm(u));
  const v = cross(n, u);
  const P = q => [dot(sub(q, c[0]), u), dot(sub(q, c[0]), v)];
  const poly = c.map(P), q = P(p);
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    if (pointDist([q[0], q[1], 0], [a[0], a[1], 0], [b[0], b[1], 0]).d <= tol) return true;
  }
  let inside = false;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    if ((a[1] > q[1]) !== (b[1] > q[1]) && q[0] < a[0] + (q[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])) inside = !inside;
  }
  return inside;
}

function fixRemoveUnconnected(d, tol) {
  const rep = new Rep("remove_unconnected_joints");
  const live = new Set(jointCoords(d, false).keys());
  const dead = new Map();
  const consider = p => {
    const k = mrPkey(p);
    if (live.has(k) || dead.has(k)) return;
    if ((d.shells || []).some(s => onShell(p.map(Number), s, Math.max(tol, 1e-6)))) { live.add(k); return; }
    dead.set(k, []);
  };
  for (const [lst] of pointLists(d)) for (const r of lst) if (r && r.point) consider(r.point);
  for (const g of Object.values(d.groups || {})) for (const p of g.points || []) consider(p);
  if (!dead.size) return { model: d, report: rep.report(false) };
  const out = clone(d);
  for (const [lst, kind] of pointLists(out)) {
    const keep = [];
    for (const r of lst) { const k = mrPkey(r.point); if (dead.has(k)) dead.get(k).push(RECORD_LABEL[kind] || kind); else keep.push(r); }
    lst.splice(0, lst.length, ...keep);
  }
  for (const [name, g] of Object.entries(out.groups || {})) if (Array.isArray(g.points)) {
    g.points = g.points.filter(p => { const k = mrPkey(p); if (dead.has(k)) { dead.get(k).push(`group ${name} point`); return false; } return true; });
  }
  for (const [k, recs] of [...dead].sort()) {
    const p = keyPt(k);
    rep.deleted.points.push(p);
    const counts = {};
    for (const x of recs) counts[x] = (counts[x] || 0) + 1;
    const desc = Object.entries(counts).map(([l, n]) => `${n} ${l}${n > 1 ? "s" : ""}`).join(", ");
    rep.add(`Removed unconnected joint ${fmtP(p)} (${desc})`, [], p, { records: recs });
    if (recs.some(x => ["nodal load", "ground displacement", "joint temperature"].includes(x)))
      rep.warnings.push(`joint ${fmtP(p)}: loads on the unconnected joint removed`);
  }
  return { model: out, report: rep.report(true) };
}

const INSTABILITY_CODES = ["NO_SUPPORTS", "FRAME_UNCONNECTED", "SHELL_UNCONNECTED", "LINK_UNCONNECTED",
  "STRUCTURE_UNSUPPORTED_PART", "STORY_NO_SUPPORT_PATH"];
function reportMechanisms(d, tol, maxDofs) {
  const rep = new Rep("report_mechanisms");
  let stab = null;
  try {
    for (const iss of mrCheckModel(d, { tolerance_m: tol }).issues || [])
      if (INSTABILITY_CODES.includes(iss.code)) rep.add(iss.message, iss.objects, iss.location, { code: iss.code, severity: iss.severity });
  } catch (e) { rep.warnings.push(`check model failed: ${e.message}`); }
  try {
    const full = mrCheckStability(d, { max_dofs: maxDofs });
    stab = {};
    for (const k of ["stable", "built", "skipped", "n_equations", "n_mechanisms", "mechanisms", "unstable_dofs", "message", "issues"]) stab[k] = full[k] === undefined ? null : full[k];
    for (const iss of full.issues || []) rep.add(iss.message, iss.objects || [], iss.location || null, { code: iss.code, severity: iss.severity });
  } catch (e) { rep.warnings.push(`stability diagnostics unavailable: ${e.message}`); }
  return { model: d, report: rep.report(false, { stability: stab }) };
}

const FIX_FUNCS = { merge_joints: fixMergeJoints, remove_zero_length: fixRemoveZeroLength,
  remove_duplicates: fixRemoveDuplicates, snap_shell_corners: fixSnapShellCorners,
  split_at_joints: fixSplitAtJoints, split_at_crossings: fixSplitAtCrossings,
  remove_unconnected_joints: fixRemoveUnconnected };

/** Validate a fix list → canonical order (backend normalize_fixes). */
export function normalizeFixes(fixes) {
  if (fixes === undefined || fixes === null) return [...DEFAULT_FIXES];
  if (fixes === "all") return [...REPAIR_FIXES];
  if (!Array.isArray(fixes)) throw new Error("'fixes' must be a list of fix ids");
  const want = new Set();
  for (const f of fixes) {
    if (f === "all") REPAIR_FIXES.forEach(x => want.add(x));
    else if (!REPAIR_FIXES.includes(f)) throw new Error(`unknown fix '${f}' (one of ${REPAIR_FIXES.join(", ")})`);
    else want.add(f);
  }
  return REPAIR_FIXES.filter(f => want.has(f));
}

function checkSummary(d, tol) {
  try {
    const s = mrCheckModel(d, { tolerance_m: tol }).summary;
    return { errors: s.errors, warnings: s.warnings, info: s.info, by_code: s.by_code, ok: s.ok };
  } catch (e) { return null; }
}

/** POST /api/repair body → { model, report } (same shapes as the backend). */
export function mockRepair(model, body = {}) {
  if (!model || typeof model !== "object") throw new Error("model must be a JSON object");
  const tol = body.tolerance === undefined ? 0.001 : body.tolerance;
  if (typeof tol !== "number" || !Number.isFinite(tol) || !(tol > 0 && tol <= 1))
    throw new Error("'tolerance' must be a number in (0, 1] m");
  const order = normalizeFixes(body.fixes);
  const maxDofs = Number.isInteger(body.max_dofs) ? body.max_dofs : 3000;
  const src = clone(model);
  let cur = src;
  const per = [];
  for (const f of order) {
    if (f === "report_mechanisms") { per.push(reportMechanisms(cur, tol, maxDofs).report); continue; }
    const r = FIX_FUNCS[f](cur, tol);
    if (r.report.changed) cur = r.model;
    per.push(r.report);
  }
  const changed = cur !== src;
  const agg = { created: emptyLists(), deleted: emptyLists(), modified: { members: [], shells: [], links: [] } };
  for (const r of per) for (const part of ["created", "deleted", "modified"])
    for (const k of Object.keys(agg[part])) agg[part][k].push(...((r[part] || {})[k] || []));
  for (const k of ["members", "shells", "links"]) {
    const both = new Set(agg.created[k].filter(u => agg.deleted[k].includes(u)));
    agg.created[k] = [...new Set(agg.created[k])].filter(u => !both.has(u));
    agg.deleted[k] = [...new Set(agg.deleted[k])].filter(u => !both.has(u));
    const gone = new Set([...agg.created[k], ...agg.deleted[k]]);
    agg.modified[k] = [...new Set(agg.modified[k])].filter(u => !gone.has(u));
  }
  const before = checkSummary(src, tol);
  const report = {
    changed, tolerance_m: tol, dry_run: !!body.dry_run, fixes: order, results: per,
    actions: per.flatMap(r => r.actions), warnings: per.flatMap(r => r.warnings),
    counts: Object.fromEntries(per.map(r => [r.fix, r.count])),
    created: agg.created, deleted: agg.deleted, modified: agg.modified,
    stability: (per.find(r => r.fix === "report_mechanisms") || {}).stability || null,
    check_before: before, check_after: changed ? checkSummary(cur, tol) : before,
  };
  return { model: changed ? cur : clone(model), report };
}
