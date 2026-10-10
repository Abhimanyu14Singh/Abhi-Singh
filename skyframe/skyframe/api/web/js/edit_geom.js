/* SkyFrame — ETABS Edit utilities, client-side geometry (mock mode + previews).

   A faithful port of skyframe/core/edit.py (CONTRACT "Edit and Select
   utilities"): applyEdit(model, op, selection, params) deep-copies the model
   dict and returns { model, summary } with the exact same algorithms
   (replicate linear / radial / mirror / story, divide, merge joints, align,
   trim/extend, move, extrude, join, delete with dependent cleanup).
   Pure: never mutates its input. Throws Error(msg) on bad input (the
   backend's 400 message text). */

export const EDIT_OPS = ["replicate", "divide", "merge_joints", "align", "move",
  "extrude", "join", "delete"];
const GEOM_TOL = 1e-6;
const MAX_COPIES = 500;
export const DEFAULT_MERGE_TOL = 0.005;
const GLOBAL_DIRS = { global_x: 0, global_y: 1, global_z: 2 };

/* ------------------------------------------------ vectors */
const r6 = v => { const r = Math.round(v * 1e6) / 1e6; return r === 0 ? 0 : r; };
const r9 = v => { const r = Math.round(v * 1e9) / 1e9; return r === 0 ? 0 : r; };
const r12 = v => Math.round(v * 1e12) / 1e12;
export const pkey = p => `${r6(+p[0])},${r6(+p[1])},${r6(+p[2])}`;
const keyPt = k => k.split(",").map(Number);
const clean = p => [r9(+p[0]), r9(+p[1]), r9(+p[2])];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const mul = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = a => Math.sqrt(dot(a, a));
const lerp = (a, b, t) => [0, 1, 2].map(k => a[k] + (b[k] - a[k]) * t);
const matvec = (R, v) => R.map(row => row[0] * v[0] + row[1] * v[1] + row[2] * v[2]);
const dist = (a, b) => norm(sub(a, b));
const clone = o => JSON.parse(JSON.stringify(o));
const sortKey = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

function f3(p) {
  if (!Array.isArray(p) || p.length !== 3 || !p.every(v => typeof v === "number" && isFinite(v)))
    throw new Error(`expected a point [x, y, z], got ${JSON.stringify(p)}`);
  return p.map(Number);
}

function num(params, key, def, o = {}) {
  let v = params[key] === undefined ? def : params[key];
  if (v === undefined || v === null) throw new Error(`'${key}' is required`);
  if (typeof v !== "number" || !isFinite(v)) throw new Error(`'${key}' must be a finite number`);
  if (o.integer) { if (Math.trunc(v) !== v) throw new Error(`'${key}' must be an integer`); }
  if (o.positive && v <= 0) throw new Error(`'${key}' must be > 0`);
  if (o.lo !== undefined && v < o.lo) throw new Error(`'${key}' must be >= ${o.lo}`);
  if (o.hi !== undefined && v > o.hi) throw new Error(`'${key}' must be <= ${o.hi}`);
  return v;
}

/* ------------------------------------------------ transforms p -> R p + t */
const roundR = R => R.map(row => row.map(v => (Math.abs(v - Math.round(v)) < 1e-12 ? Math.round(v) : v)));
export class Xform {
  constructor(R, t = [0, 0, 0], rotZ = 0, mirror = false) {
    this.R = R || [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    this.t = t; this.rotZ = rotZ; this.mirror = mirror; this.det = mirror ? -1 : 1;
    this.identityR = !mirror && this.R.every((row, i) => row.every((v, j) => Math.abs(v - (i === j ? 1 : 0)) < 1e-15));
  }
  p(p) { return clean(add(matvec(this.R, p), this.t)); }
  v(v) { return matvec(this.R, v); }
  pv(v) { return mul(matvec(this.R, v), this.det); }
}
export const translation = (dx, dy, dz) => new Xform(null, [dx, dy, dz]);
export function rotation(center, axis, deg) {
  const a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
  let R;
  if (axis === "z") R = [[c, -s, 0], [s, c, 0], [0, 0, 1]];
  else if (axis === "x") R = [[1, 0, 0], [0, c, -s], [0, s, c]];
  else if (axis === "y") R = [[c, 0, s], [0, 1, 0], [-s, 0, c]];
  else throw new Error("'axis' must be x|y|z");
  R = roundR(R);
  return new Xform(R, sub(center, matvec(R, center)), axis === "z" ? deg : 0);
}
export function reflection(point, normal) {
  const ln = norm(normal);
  if (ln < 1e-12) throw new Error("mirror plane normal must be non-zero");
  const n = mul(normal, 1 / ln);
  const R = roundR([0, 1, 2].map(i => [0, 1, 2].map(j => (i === j ? 1 : 0) - 2 * n[i] * n[j])));
  return new Xform(R, mul(n, 2 * dot(point, n)), 0, true);
}

/* ------------------------------------------------ context */
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
  newUid(like, fallback = "E") {
    const m = /^[A-Za-z]+/.exec(String(like));
    const prefix = m ? m[0] : fallback;
    if (!(prefix in this.next)) {
      let mx = 0;
      const rx = new RegExp(`^${prefix}(\\d+)$`);
      for (const u of this.used) { const g = rx.exec(u); if (g) mx = Math.max(mx, +g[1]); }
      this.next[prefix] = mx + 1;
    }
    for (;;) {
      const uid = `${prefix}${this.next[prefix]++}`;
      if (!this.used.has(uid)) { this.used.add(uid); return uid; }
    }
  }
  member(u) { return this.d.members.find(m => m.uid === u) || null; }
  shell(u) { return this.d.shells.find(s => s.uid === u) || null; }
  link(u) { return this.d.links.find(l => l.uid === u) || null; }
  markModified(kind, uid) {
    if (!this.modified[kind].includes(uid) && !this.created[kind].includes(uid)) this.modified[kind].push(uid);
  }
  storyFor(zs) {
    const st = this.d.stories || [];
    if (!st.length) return null;
    const z = Math.max(...zs);
    for (const s of st) if (Math.abs(+s.elevation - z) < 1e-6) return s.name;
    for (const s of st) {
      const zt = +s.elevation, zb = zt - +s.height;
      if (zb - 1e-9 < z && z < zt) return s.name;
    }
    return null;
  }
  restory(obj, pts, fallback) {
    const st = this.storyFor(pts.map(p => p[2]));
    if (st !== null) obj.story = st;
    else if (fallback !== undefined) obj.story = fallback;
  }
  objectJoints() {
    const cnt = new Map();
    const inc = p => { const k = pkey(p); cnt.set(k, (cnt.get(k) || 0) + 1); };
    for (const m of this.d.members) { inc(m.pi); inc(m.pj); }
    for (const s of this.d.shells) for (const c of s.corners) inc(c);
    for (const l of this.d.links) { inc(l.pi); inc(l.pj); }
    return cnt;
  }
  summary(op, extra) {
    return Object.assign({ op, created: this.created, deleted: this.deleted, modified: this.modified,
      warnings: this.warnings }, extra || {});
  }
}

/* ------------------------------------------------ point-keyed records */
function* pointLists(d) {
  for (const k of ["supports", "spring_supports", "nodal_masses", "joint_diaphragms", "joint_panel_zones"])
    if (Array.isArray(d[k])) yield [d[k], k];
  for (const pat of Object.values(d.patterns || {}))
    for (const k of ["nodal_loads", "ground_displacements", "joint_temperatures"])
      if (Array.isArray(pat[k])) yield [pat[k], k];
}

function remapPoints(ctx, fn, includeTendons = false) {
  const d = ctx.d, touched = [];
  const mp = p => { const q = fn(p); return q ? clean(q) : null; };
  for (const m of d.members) {
    let ch = false;
    for (const e of ["pi", "pj"]) { const q = mp(m[e]); if (q && pkey(q) !== pkey(m[e])) { m[e] = q; ch = true; } }
    if (ch) touched.push(["members", m.uid]);
  }
  for (const s of d.shells) {
    let ch = false;
    const deltas = new Set(), out = [];
    for (const c of s.corners) {
      const q = mp(c);
      if (q && pkey(q) !== pkey(c)) { deltas.add(pkey(sub(q, c))); out.push(q); ch = true; }
      else { deltas.add(pkey([0, 0, 0])); out.push([...c]); }
    }
    if (ch) {
      s.corners = out;
      touched.push(["shells", s.uid]);
      if (deltas.size === 1) {
        const dv = keyPt([...deltas][0]);
        for (const op of s.openings || []) if (op.polygon) op.polygon = op.polygon.map(p => clean(add(p, dv)));
      } else if ((s.openings || []).some(op => op.polygon))
        ctx.warnings.push(`shell ${s.uid}: polygon openings kept in place (corners moved non-uniformly)`);
    }
  }
  for (const l of d.links) {
    let ch = false;
    for (const e of ["pi", "pj"]) { const q = mp(l[e]); if (q && pkey(q) !== pkey(l[e])) { l[e] = q; ch = true; } }
    if (ch) touched.push(["links", l.uid]);
  }
  for (const ls of d.line_springs || []) for (const e of ["p1", "p2"]) { const q = mp(ls[e]); if (q) ls[e] = q; }
  for (const [lst] of pointLists(d)) for (const r of lst) if (r && r.point) { const q = mp(r.point); if (q) r.point = q; }
  for (const g of Object.values(d.groups || {})) g.points = (g.points || []).map(p => mp(p) || [...p]);
  // analysis-case settings that name joints by coordinates (mirrors edit.py)
  for (const tc of Object.values(d.th_cases || {})) {
    const req = tc && tc.output_requests;
    if (req && Array.isArray(req.joints)) req.joints = req.joints.map(p => mp(p) || [...p]);
  }
  for (const key of ["steady_state_cases", "psd_cases"])
    for (const fc of Object.values(d[key] || {}))
      if (fc && Array.isArray(fc.output_points)) fc.output_points = fc.output_points.map(p => mp(p) || [...p]);
  for (const key of ["pushover_cases", "nonlinear_static_cases"])
    for (const pc of Object.values(d[key] || {}))
      if (pc && pc.control_point) pc.control_point = mp(pc.control_point) || [...pc.control_point];
  if (includeTendons) for (const td of d.tendons || []) td.points = (td.points || []).map(p => mp(p) || [...p]);
  return touched;
}

function dedupePointRecords(d) {
  for (const k of ["supports", "spring_supports", "joint_diaphragms", "joint_panel_zones"]) {
    if (!Array.isArray(d[k])) continue;
    const seen = new Set();
    d[k] = d[k].filter(r => { const kk = pkey(r.point); if (seen.has(kk)) return false; seen.add(kk); return true; });
  }
  for (const g of Object.values(d.groups || {})) {
    const seen = new Set();
    g.points = (g.points || []).filter(p => { const kk = pkey(p); if (seen.has(kk)) return false; seen.add(kk); return true; });
  }
}

/* ------------------------------------------------ uid-keyed references */
function replaceInList(lst, old, neu) {
  const out = [];
  for (const u of lst) {
    if (u === old) { for (const x of neu) if (!out.includes(x)) out.push(x); }
    else if (!out.includes(u)) out.push(u);
  }
  return out;
}
function replaceUidRefs(d, kind, old, neu) {
  for (const g of Object.values(d.groups || {}))
    if ((g[kind] || []).includes(old)) g[kind] = replaceInList(g[kind], old, neu);
  if (kind === "members" || kind === "shells")
    for (const td of d.tendons || []) {
      let h = td.host;
      if (typeof h === "string") h = [h];
      if (h && h.includes(old)) td.host = replaceInList(h, old, neu);
    }
  if (kind === "members")
    for (const po of Object.values(d.pushover_cases || {})) {
      const my = po.My;
      if (my && typeof my === "object" && old in my) {
        const v = my[old]; delete my[old];
        for (const u of neu) if (!(u in my)) my[u] = v;
      }
    }
}
function copyUidRefs(d, kind, old, neu) {
  for (const g of Object.values(d.groups || {})) {
    const lst = g[kind] || [];
    if (lst.includes(old) && !lst.includes(neu)) { lst.push(neu); g[kind] = lst; }
  }
  if (kind === "members")
    for (const po of Object.values(d.pushover_cases || {}))
      if (po.My && old in po.My && !(neu in po.My)) po.My[neu] = po.My[old];
}

function deleteObjects(ctx, members = [], shells = [], links = [], cleanupPoints = true) {
  const d = ctx.d;
  const ms = new Set(members), ss = new Set(shells), ls = new Set(links);
  if (!ms.size && !ss.size && !ls.size) return;
  const gone = new Set();
  for (const m of d.members) if (ms.has(m.uid)) { gone.add(pkey(m.pi)); gone.add(pkey(m.pj)); }
  for (const s of d.shells) if (ss.has(s.uid)) for (const c of s.corners) gone.add(pkey(c));
  for (const l of d.links) if (ls.has(l.uid)) { gone.add(pkey(l.pi)); gone.add(pkey(l.pj)); }
  d.members = d.members.filter(m => !ms.has(m.uid));
  d.shells = d.shells.filter(s => !ss.has(s.uid));
  d.links = d.links.filter(l => !ls.has(l.uid));
  for (const pat of Object.values(d.patterns)) {
    for (const k of ["member_loads", "member_udls", "thermal_loads"])
      if (pat[k] && pat[k].length) pat[k] = pat[k].filter(x => !ms.has(x.member_uid));
    for (const k of ["area_loads", "shell_thermal_loads"])
      if (pat[k] && pat[k].length) pat[k] = pat[k].filter(x => !ss.has(x.region_uid));
  }
  for (const u of ms) replaceUidRefs(d, "members", u, []);
  for (const u of ss) replaceUidRefs(d, "shells", u, []);
  for (const u of ls) replaceUidRefs(d, "links", u, []);
  if (d.tendons && d.tendons.length)
    d.tendons = d.tendons.filter(td => {
      if (td.host && td.host.length) return true;
      ctx.warnings.push(`tendon ${td.uid} deleted (no host left)`);
      return false;
    });
  for (const [kind, set] of [["members", ms], ["shells", ss], ["links", ls]])
    for (const u of [...set].sort(sortKey)) {
      const ci = ctx.created[kind].indexOf(u);
      if (ci >= 0) ctx.created[kind].splice(ci, 1); else ctx.deleted[kind].push(u);
      const mi = ctx.modified[kind].indexOf(u);
      if (mi >= 0) ctx.modified[kind].splice(mi, 1);
    }
  if (cleanupPoints && gone.size) {
    const live = ctx.objectJoints();
    const orphan = new Set([...gone].filter(k => !live.has(k)));
    if (orphan.size) deletePointRecords(ctx, orphan);
  }
}

function deletePointRecords(ctx, keys) {
  let n = 0;
  for (const [lst] of [...pointLists(ctx.d)]) {
    const keep = lst.filter(r => !keys.has(pkey(r.point)));
    n += lst.length - keep.length;
    lst.splice(0, lst.length, ...keep);
  }
  for (const g of Object.values(ctx.d.groups || {})) g.points = (g.points || []).filter(p => !keys.has(pkey(p)));
  return n;
}

/* ------------------------------------------------ selection */
export function normalizeSelection(d, sel) {
  sel = sel || {};
  const out = {};
  for (const kind of ["members", "shells", "links"]) {
    const uids = sel[kind] || [];
    if (!Array.isArray(uids)) throw new Error(`selection.${kind} must be a list of uids`);
    const have = new Set((d[kind] || []).map(o => o.uid));
    const seen = [];
    for (const u of uids) {
      if (!have.has(u)) throw new Error(`selection.${kind}: unknown uid '${u}'`);
      if (!seen.includes(u)) seen.push(u);
    }
    out[kind] = seen;
  }
  const pts = sel.points || [];
  if (!Array.isArray(pts)) throw new Error("selection.points must be a list of [x, y, z]");
  out.points = [];
  const sp = new Set();
  for (const p of pts) { const q = f3(p); if (!sp.has(pkey(q))) { sp.add(pkey(q)); out.points.push(q); } }
  return out;
}

export function selectionJoints(d, sel) {
  const out = [], seen = new Set();
  const addp = p => { const k = pkey(p); if (!seen.has(k)) { seen.add(k); out.push([...p]); } };
  for (const p of sel.points || []) addp(p);
  for (const u of sel.members || []) { const m = d.members.find(x => x.uid === u); if (m) { addp(m.pi); addp(m.pj); } }
  for (const u of sel.shells || []) { const s = d.shells.find(x => x.uid === u); if (s) s.corners.forEach(addp); }
  for (const u of sel.links || []) { const l = (d.links || []).find(x => x.uid === u); if (l) { addp(l.pi); addp(l.pj); } }
  return out;
}
const isEmpty = s => !(s.members.length || s.shells.length || s.links.length || s.points.length);
const newSel = () => ({ members: [], shells: [], links: [], points: [] });

/* ------------------------------------------------ transformed copies */
function xfDirLoad(ld, xf, keys = ["w", "w2"], dkey = "direction", moment = false) {
  const dirn = ld[dkey] || "gravity";
  if (xf.identityR || !(dirn in GLOBAL_DIRS)) return [clone(ld)];
  const e = [0, 0, 0]; e[GLOBAL_DIRS[dirn]] = 1;
  const v = moment ? xf.pv(e) : xf.v(e);
  const out = [];
  ["global_x", "global_y", "global_z"].forEach((name, ax) => {
    if (Math.abs(v[ax]) < 1e-12) return;
    const nl = clone(ld);
    nl[dkey] = name;
    for (const k of keys) if (typeof nl[k] === "number") nl[k] = nl[k] * v[ax];
    out.push(nl);
  });
  return out;
}

function xfNodal(rec, xf, np) {
  const nr = clone(rec);
  nr.point = np;
  if (!xf.identityR) {
    const f = xf.v([rec.fx || 0, rec.fy || 0, rec.fz || 0]);
    [nr.fx, nr.fy, nr.fz] = f;
    if (["mx", "my", "mz"].some(k => k in rec)) {
      const mm = xf.pv([rec.mx || 0, rec.my || 0, rec.mz || 0]);
      ["mx", "my", "mz"].forEach((k, i) => { if (mm[i] || k in rec) nr[k] = mm[i]; });
    }
  }
  return nr;
}

function xfMember(m, xf, uid, assignments) {
  let nm;
  if (assignments) nm = clone(m);
  else { nm = {}; for (const k of ["kind", "section", "story"]) if (k in m) nm[k] = clone(m[k]); }
  nm.uid = uid;
  nm.pi = xf.p(m.pi); nm.pj = xf.p(m.pj);
  delete nm.length;
  if (assignments) {
    if (xf.mirror) nm.angle = -(+m.angle || 0);
    else if (xf.rotZ) {
      const dx = Math.abs(m.pj[0] - m.pi[0]) + Math.abs(m.pj[1] - m.pi[1]);
      if (dx < 1e-9) {
        const sgn = m.pj[2] > m.pi[2] ? 1 : -1;
        let a = (+m.angle || 0) + sgn * xf.rotZ;
        a = ((a + 180) % 360 + 360) % 360 - 180;
        nm.angle = r9(a);
      }
    }
    const jo = nm.joint_offsets;
    if (jo && typeof jo === "object" && (jo.system || "global") === "global" && !xf.identityR)
      for (const e of ["i", "j"]) if (Array.isArray(jo[e]) && jo[e].length === 3) jo[e] = clean(xf.v(jo[e]));
  }
  return nm;
}

function xfShell(s, xf, uid, assignments) {
  let ns;
  if (assignments) ns = clone(s);
  else { ns = {}; for (const k of ["kind", "behavior", "section", "mesh_size", "story", "openings"]) if (k in s) ns[k] = clone(s[k]); }
  ns.uid = uid;
  let corners = s.corners.map(c => xf.p(c));
  const ops = ns.openings || [];
  for (const op of ops) if (op.polygon) op.polygon = op.polygon.map(p => xf.p(p));
  if (xf.mirror) {
    corners = [corners[0], ...corners.slice(1).reverse()];
    if (corners.length === 4)
      for (const op of ops) if (!op.polygon) {
        [op.u0, op.v0] = [op.v0, op.u0];
        [op.u1, op.v1] = [op.v1, op.u1];
      }
    for (const op of ops) if (op.polygon) op.polygon = op.polygon.reverse();
  }
  ns.corners = corners;
  return ns;
}

const gkMember = m => "m|" + [pkey(m.pi), pkey(m.pj)].sort(sortKey).join("|");
const gkShell = s => "s|" + s.corners.map(pkey).sort(sortKey).join("|");
const gkLink = l => "l|" + [pkey(l.pi), pkey(l.pj)].sort(sortKey).join("|");

function copySelection(ctx, sel, xf, { assignments = true, loads = true, story = null, nsel = null } = {}) {
  const d = ctx.d;
  const existing = new Set([...d.members.map(gkMember), ...d.shells.map(gkShell), ...d.links.map(gkLink)]);
  const mmap = {}, smap = {}, lmap = {};
  let made = 0, skipped = 0;
  for (const u of sel.members) {
    const m = ctx.member(u);
    const nm = xfMember(m, xf, "", assignments);
    if (existing.has(gkMember(nm))) { skipped++; continue; }
    nm.uid = ctx.newUid(u, (m.kind || "F")[0].toUpperCase());
    if (story !== null) nm.story = story; else ctx.restory(nm, [nm.pi, nm.pj], "");
    existing.add(gkMember(nm));
    d.members.push(nm);
    ctx.created.members.push(nm.uid);
    mmap[u] = nm.uid; made++;
    if (assignments) copyUidRefs(d, "members", u, nm.uid);
  }
  for (const u of sel.shells) {
    const s = ctx.shell(u);
    const ns = xfShell(s, xf, "", assignments);
    if (existing.has(gkShell(ns))) { skipped++; continue; }
    ns.uid = ctx.newUid(u, s.kind === "wall" ? "W" : "S");
    if (story !== null) ns.story = story; else ctx.restory(ns, ns.corners, "");
    existing.add(gkShell(ns));
    d.shells.push(ns);
    ctx.created.shells.push(ns.uid);
    smap[u] = ns.uid; made++;
    if (assignments) copyUidRefs(d, "shells", u, ns.uid);
  }
  for (const u of sel.links) {
    const l = ctx.link(u);
    const nl = clone(l);
    nl.pi = xf.p(l.pi); nl.pj = xf.p(l.pj);
    if (existing.has(gkLink(nl))) { skipped++; continue; }
    nl.uid = ctx.newUid(u, "L");
    existing.add(gkLink(nl));
    d.links.push(nl);
    ctx.created.links.push(nl.uid);
    lmap[u] = nl.uid; made++;
    if (assignments) copyUidRefs(d, "links", u, nl.uid);
  }
  if (skipped) ctx.warnings.push(`${skipped} object(s) skipped: an identical object already exists at the target`);
  if (loads) {
    for (const pat of Object.values(d.patterns)) {
      let addl = [];
      for (const ld of pat.member_loads || [])
        if (ld.member_uid in mmap)
          for (const nl of xfDirLoad(ld, xf, ["w", "w2"], "direction", ld.kind === "moment")) {
            nl.member_uid = mmap[ld.member_uid]; addl.push(nl);
          }
      if (addl.length) pat.member_loads = (pat.member_loads || []).concat(addl);
      for (const k of ["member_udls", "thermal_loads"]) {
        addl = [];
        for (const ld of pat[k] || []) if (ld.member_uid in mmap) { const nl = clone(ld); nl.member_uid = mmap[ld.member_uid]; addl.push(nl); }
        if (addl.length) pat[k] = (pat[k] || []).concat(addl);
      }
      addl = [];
      for (const ld of pat.area_loads || [])
        if (ld.region_uid in smap)
          for (const nl of xfDirLoad(ld, xf, ["q"])) { nl.region_uid = smap[ld.region_uid]; addl.push(nl); }
      if (addl.length) pat.area_loads = (pat.area_loads || []).concat(addl);
      addl = [];
      for (const ld of pat.shell_thermal_loads || [])
        if (ld.region_uid in smap) { const nl = clone(ld); nl.region_uid = smap[ld.region_uid]; addl.push(nl); }
      if (addl.length) pat.shell_thermal_loads = (pat.shell_thermal_loads || []).concat(addl);
    }
    const hmap = Object.assign({}, mmap, smap);
    const tuids = new Set((d.tendons || []).map(t => t.uid));
    const newT = [];
    for (const td of d.tendons || []) {
      let h = td.host || [];
      if (typeof h === "string") h = [h];
      if (h.length && h.every(x => x in hmap)) {
        const nt = clone(td);
        nt.host = h.map(x => hmap[x]);
        nt.points = (td.points || []).map(p => xf.p(p));
        if (td.drape_dir != null) nt.drape_dir = clean(xf.v(td.drape_dir));
        const base = String(td.uid || "T");
        let k = 1;
        while (tuids.has(`${base}_${k}`)) k++;
        nt.uid = `${base}_${k}`;
        tuids.add(nt.uid);
        newT.push(nt);
      }
    }
    if (newT.length) d.tendons = (d.tendons || []).concat(newT);
  }
  if (sel.points.length) {
    const keys = new Map(sel.points.map(p => [pkey(p), p]));
    const newp = new Map([...keys].map(([k, p]) => [k, xf.p(p)]));
    for (const [lst, kind] of [...pointLists(d)]) {
      const isLoad = ["nodal_loads", "ground_displacements", "joint_temperatures"].includes(kind);
      if ((isLoad && !loads) || (!isLoad && !assignments)) continue;
      const have = new Set(lst.map(r => pkey(r.point)));
      const addl = [];
      for (const r of lst) {
        const kk = pkey(r.point);
        if (!keys.has(kk)) continue;
        const np = newp.get(kk);
        if (!isLoad && have.has(pkey(np))) continue;
        let nr;
        if (kind === "nodal_loads") nr = xfNodal(r, xf, np);
        else {
          nr = clone(r); nr.point = np;
          if (kind === "spring_supports" && xf.rotZ) nr.angle_deg = (+r.angle_deg || 0) + xf.rotZ;
        }
        addl.push(nr);
      }
      lst.push(...addl);
    }
    if (assignments)
      for (const g of Object.values(d.groups || {})) {
        const gp = g.points || [];
        const gk = new Set(gp.map(pkey));
        for (const kk of [...gk]) if (keys.has(kk) && !gk.has(pkey(newp.get(kk)))) gp.push(newp.get(kk));
        g.points = gp;
      }
    for (const kk of keys.keys()) {
      ctx.created.points.push(newp.get(kk));
      if (nsel) nsel.points.push(newp.get(kk));
    }
  }
  if (nsel) {
    nsel.members.push(...Object.values(mmap));
    nsel.shells.push(...Object.values(smap));
    nsel.links.push(...Object.values(lmap));
  }
  return made;
}

/* ------------------------------------------------ operations */
function mirrorXform(params) {
  const plane = params.plane;
  if (["x", "y", "z"].includes(plane)) {
    const c = num(params, "coord", 0);
    const n = [0, 0, 0], p = [0, 0, 0];
    n["xyz".indexOf(plane)] = 1; p["xyz".indexOf(plane)] = c;
    return reflection(p, n);
  }
  if (params.p1 != null && params.p2 != null) {
    const a = [+params.p1[0], +params.p1[1], 0], b = [+params.p2[0], +params.p2[1], 0];
    const dv = sub(b, a);
    if (norm(dv) < GEOM_TOL) throw new Error("mirror: p1 and p2 coincide");
    return reflection(a, [dv[1], -dv[0], 0]);
  }
  if (params.point != null && params.normal != null) return reflection(f3(params.point), f3(params.normal));
  throw new Error("mirror: give plane x|y|z + coord, a plan line p1/p2, or point + normal");
}

/** The transforms a replicate would apply (also drives the ghost preview). */
export function replicateXforms(d, params) {
  const mode = params.mode || "linear";
  const out = [];
  if (mode === "linear") {
    const dx = num(params, "dx", 0), dy = num(params, "dy", 0), dz = num(params, "dz", 0);
    const n = num(params, "n", 1, { integer: true, lo: 1, hi: MAX_COPIES });
    if (Math.abs(dx) + Math.abs(dy) + Math.abs(dz) < GEOM_TOL) throw new Error("replicate: the offset (dx, dy, dz) is zero");
    for (let k = 1; k <= n; k++) out.push(translation(dx * k, dy * k, dz * k));
  } else if (mode === "radial") {
    const center = f3(params.center || [0, 0, 0]);
    const ang = num(params, "angle");
    const n = num(params, "n", 1, { integer: true, lo: 1, hi: MAX_COPIES });
    if (Math.abs(ang) < 1e-12) throw new Error("replicate: 'angle' must be non-zero");
    for (let k = 1; k <= n; k++) out.push(rotation(center, params.axis || "z", ang * k));
  } else if (mode === "mirror") out.push(mirrorXform(params));
  return out;
}

function opReplicate(ctx, sel, params) {
  if (isEmpty(sel)) throw new Error("replicate: nothing selected");
  const mode = params.mode || "linear";
  const assignments = params.assignments !== undefined ? !!params.assignments : true;
  const loads = params.loads !== undefined ? !!params.loads : true;
  const ns = newSel();
  let made = 0;
  if (mode === "linear" || mode === "radial" || mode === "mirror") {
    for (const xf of replicateXforms(ctx.d, params))
      made += copySelection(ctx, sel, xf, { assignments, loads, nsel: ns });
  } else if (mode === "story") {
    const targets = params.stories || [];
    if (!Array.isArray(targets) || !targets.length) throw new Error("replicate: 'stories' must be a non-empty list of story names");
    const elev = {};
    for (const s of ctx.d.stories || []) elev[s.name] = +s.elevation;
    for (const t of targets) if (!(t in elev)) throw new Error(`replicate: unknown story '${t}'`);
    const groups = new Map();
    const grp = st => { if (!groups.has(st)) groups.set(st, newSel()); return groups.get(st); };
    const srcStory = (obj, pts) => {
      const st = obj.story || ctx.storyFor(pts.map(p => p[2]));
      if (!(st in elev)) throw new Error(`replicate: object ${obj.uid} has no story`);
      return st;
    };
    for (const u of sel.members) { const m = ctx.member(u); grp(srcStory(m, [m.pi, m.pj])).members.push(u); }
    for (const u of sel.shells) { const s = ctx.shell(u); grp(srcStory(s, s.corners)).shells.push(u); }
    for (const u of sel.links) {
      const l = ctx.link(u);
      const st = ctx.storyFor([l.pi[2], l.pj[2]]);
      if (st === null) throw new Error(`replicate: link ${u} has no story`);
      grp(st).links.push(u);
    }
    for (const p of sel.points) {
      const st = ctx.storyFor([p[2]]);
      if (st === null) throw new Error(`replicate: point ${JSON.stringify(p)} is not on a story`);
      grp(st).points.push(p);
    }
    for (const [src, gsel] of groups)
      for (const t of targets) {
        if (t === src) continue;
        made += copySelection(ctx, gsel, translation(0, 0, elev[t] - elev[src]), { assignments, loads, story: t, nsel: ns });
      }
  } else throw new Error("replicate: 'mode' must be linear|radial|mirror|story");
  return { new_selection: ns, count: made };
}

/* ---- divide */
function remapMemberLoad(ld, s0, s1) {
  const r = s1 - s0, kind = ld.kind || "udl";
  const a = ld.a !== undefined ? +ld.a : 0, b = ld.b !== undefined ? +ld.b : 1;
  if (kind === "point" || kind === "moment") {
    const last = s1 >= 1 - 1e-12;
    if ((s0 - 1e-12 <= a && a < s1 - 1e-12) || (last && a >= s0 - 1e-12)) {
      const nl = clone(ld);
      nl.a = Math.min(1, Math.max(0, r12((a - s0) / r)));
      return nl;
    }
    return null;
  }
  const lo = Math.max(a, s0), hi = Math.min(b, s1);
  if (hi - lo <= 1e-12) return null;
  const nl = clone(ld);
  nl.a = Math.min(1, Math.max(0, r12((lo - s0) / r)));
  nl.b = Math.min(1, Math.max(0, r12((hi - s0) / r)));
  if (kind === "trapezoid") {
    const w = +ld.w || 0, w2 = +ld.w2 || 0, span = b - a;
    nl.w = span > 0 ? w + (w2 - w) * (lo - a) / span : w;
    nl.w2 = span > 0 ? w + (w2 - w) * (hi - a) / span : w2;
  }
  return nl;
}

function splitMember(ctx, uid, ts) {
  const d = ctx.d, m = ctx.member(uid);
  ts = ts.filter(t => t > 1e-9 && t < 1 - 1e-9).sort((a, b) => a - b);
  if (!ts.length) return [uid];
  const bounds = [0, ...ts, 1];
  const idx = d.members.indexOf(m);
  const toks = String(m.releases || "").split(",").map(t => t.trim()).filter(Boolean);
  const pieces = [], uids = [];
  for (let k = 0; k < bounds.length - 1; k++) {
    const s0 = bounds[k], s1 = bounds[k + 1];
    const pm = clone(m);
    delete pm.length;
    pm.uid = k === 0 ? uid : ctx.newUid(uid, "F");
    pm.pi = clean(lerp(m.pi, m.pj, s0));
    pm.pj = clean(lerp(m.pi, m.pj, s1));
    const first = k === 0, last = k === bounds.length - 2;
    pm.releases = toks.filter(t => (t === "Mi" && first) || (t === "Mj" && last)).join(",");
    if (!first) pm.rigid_i = 0;
    if (!last) pm.rigid_j = 0;
    const jo = pm.joint_offsets;
    if (jo && typeof jo === "object") {
      if (!first) jo.i = [0, 0, 0];
      if (!last) jo.j = [0, 0, 0];
    }
    if (Array.isArray(m.hinges)) {
      const hl = [];
      for (const h of m.hinges) {
        const rd = +h.relative_distance || 0;
        if (s0 - 1e-12 <= rd && rd <= s1 + 1e-12 && !(rd >= s1 - 1e-12 && !last))
          hl.push(Object.assign({}, h, { relative_distance: r12((rd - s0) / (s1 - s0)) }));
      }
      pm.hinges = hl.length ? hl : "none";
    }
    pieces.push(pm); uids.push(pm.uid);
    if (k) ctx.created.members.push(pm.uid);
  }
  ctx.markModified("members", uid);
  d.members.splice(idx, 1, ...pieces);
  for (const pat of Object.values(d.patterns)) {
    if (pat.member_loads && pat.member_loads.length) {
      const out = [];
      for (const ld of pat.member_loads) {
        if (ld.member_uid !== uid) { out.push(ld); continue; }
        for (let k = 0; k < bounds.length - 1; k++) {
          const nl = remapMemberLoad(ld, bounds[k], bounds[k + 1]);
          if (nl) { nl.member_uid = uids[k]; out.push(nl); }
        }
      }
      pat.member_loads = out;
    }
    for (const key of ["member_udls", "thermal_loads"])
      if (pat[key] && pat[key].length) {
        const out = [];
        for (const ld of pat[key]) {
          out.push(ld);
          if (ld.member_uid === uid) for (const u of uids.slice(1)) { const nl = clone(ld); nl.member_uid = u; out.push(nl); }
        }
        pat[key] = out;
      }
  }
  replaceUidRefs(d, "members", uid, uids);
  return uids;
}

function pointOnSegmentT(p, a, b, tol) {
  const ab = sub(b, a), L2 = dot(ab, ab);
  if (L2 < 1e-18) return null;
  const t = dot(sub(p, a), ab) / L2;
  if (t <= 0 || t >= 1) return null;
  if (norm(sub(p, lerp(a, b, t))) > tol) return null;
  return t;
}
function segmentCross(a, b, c, e, tol) {
  const u = sub(b, a), v = sub(e, c), w0 = sub(a, c);
  const A = dot(u, u), B = dot(u, v), C = dot(v, v), D = dot(u, w0), E = dot(v, w0);
  const den = A * C - B * B;
  if (den < 1e-12 * A * C) return null;
  const t = (B * E - C * D) / den, s = (A * E - B * D) / den;
  if (!(t >= -1e-9 && t <= 1 + 1e-9 && s >= -1e-9 && s <= 1 + 1e-9)) return null;
  if (norm(sub(lerp(a, b, t), lerp(c, e, s))) > tol) return null;
  return [t, s];
}

/** Split fractions per selected member (also drives the ghost preview). */
export function dividePlan(d, sel, params, warnings = []) {
  const mode = params.mode || "n";
  const plan = {};
  const byUid = u => d.members.find(m => m.uid === u);
  if (mode === "n") {
    const n = num(params, "n", 2, { integer: true, lo: 2, hi: MAX_COPIES });
    for (const u of sel.members) plan[u] = Array.from({ length: n - 1 }, (_, k) => (k + 1) / n);
  } else if (mode === "distance") {
    const dd = num(params, "distance", undefined, { positive: true });
    const frm = params.from || "i";
    if (frm !== "i" && frm !== "j") throw new Error("divide: 'from' must be i|j");
    for (const u of sel.members) {
      const m = byUid(u), L = dist(m.pi, m.pj);
      if (dd >= L - GEOM_TOL) { warnings.push(`member ${u}: distance >= length (${+L.toPrecision(4)} m), not divided`); continue; }
      plan[u] = [frm === "i" ? dd / L : 1 - dd / L];
    }
  } else if (mode === "intersections") {
    const tol = num(params, "tol", 1e-3, { positive: true });
    const cnt = new Map();
    const inc = p => cnt.set(pkey(p), 1);
    for (const m of d.members) { inc(m.pi); inc(m.pj); }
    for (const s of d.shells) s.corners.forEach(inc);
    for (const l of d.links || []) { inc(l.pi); inc(l.pj); }
    for (const p of sel.points || []) inc(p);
    const joints = [...cnt.keys()].map(keyPt);
    for (const u of sel.members) {
      const m = byUid(u), a = m.pi, b = m.pj;
      const ts = [];
      for (const j of joints) { const t = pointOnSegmentT(j, a, b, tol); if (t !== null) ts.push(t); }
      for (const o of d.members) {
        if (o.uid === u) continue;
        const r = segmentCross(a, b, o.pi, o.pj, tol);
        if (r && r[0] > 1e-9 && r[0] < 1 - 1e-9) ts.push(r[0]);
      }
      ts.sort((x, y) => x - y);
      const L = dist(a, b), uniq = [];
      for (const t of ts) if (!uniq.length || (t - uniq[uniq.length - 1]) * L > tol) uniq.push(t);
      if (uniq.length) plan[u] = uniq;
    }
  } else throw new Error("divide: 'mode' must be n|intersections|distance");
  return plan;
}

function opDivide(ctx, sel, params) {
  if (!sel.members.length) throw new Error("divide: select frame members");
  const plan = dividePlan(ctx.d, sel, params, ctx.warnings);
  const ns = newSel(), report = {};
  for (const [u, ts] of Object.entries(plan)) {
    const uids = splitMember(ctx, u, ts);
    report[u] = uids;
    ns.members.push(...uids);
  }
  return { new_selection: ns, divided: report };
}

/* ---- merge joints */
function opMergeJoints(ctx, sel, params) {
  const tol = num(params, "tolerance", DEFAULT_MERGE_TOL, { positive: true, hi: 10 });
  const d = ctx.d;
  const cnt = ctx.objectJoints();
  const allj = new Map(cnt);
  for (const [lst] of pointLists(d)) for (const r of lst) if (!allj.has(pkey(r.point))) allj.set(pkey(r.point), 0);
  const scope = isEmpty(sel) ? [...allj.keys()] : selectionJoints(d, sel).map(pkey);
  const supported = new Set([...(d.supports || []), ...(d.spring_supports || [])].map(r => pkey(r.point)));
  const pts = [...new Set(scope)].map(keyPt).sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
  const ks = pts.map(pkey);
  const parent = ks.map((_, i) => i);
  const find = i => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
  const cellOf = p => p.map(v => Math.floor(v / tol)).join(",");
  const cells = new Map();
  pts.forEach((p, i) => { const c = cellOf(p); if (!cells.has(c)) cells.set(c, []); cells.get(c).push(i); });
  pts.forEach((p, i) => {
    const c = p.map(v => Math.floor(v / tol));
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++)
      for (const j of cells.get(`${c[0] + dx},${c[1] + dy},${c[2] + dz}`) || []) {
        if (j <= i) continue;
        if (dist(p, pts[j]) <= tol + 1e-12) {
          const ra = find(i), rb = find(j);
          if (ra !== rb) parent[Math.max(ra, rb)] = Math.min(ra, rb);
        }
      }
  });
  const clusters = new Map();
  pts.forEach((p, i) => { const r = find(i); if (!clusters.has(r)) clusters.set(r, []); clusters.get(r).push(i); });
  const mapping = new Map(), merged = [];
  for (const idxs of clusters.values()) {
    if (idxs.length < 2) continue;
    const rep = [...idxs].sort((a, b) => {
      const sa = supported.has(ks[a]) ? 0 : 1, sb = supported.has(ks[b]) ? 0 : 1;
      if (sa !== sb) return sa - sb;
      const ca = -(cnt.get(ks[a]) || 0), cb = -(cnt.get(ks[b]) || 0);
      if (ca !== cb) return ca - cb;
      return a - b;
    })[0];
    for (const i of idxs) if (i !== rep) mapping.set(ks[i], [...pts[rep]]);
    merged.push({ to: [...pts[rep]], from: idxs.filter(i => i !== rep).map(i => [...pts[i]]) });
  }
  if (mapping.size) {
    for (const [kind, uid] of remapPoints(ctx, p => mapping.get(pkey(p)) || null)) ctx.markModified(kind, uid);
    cleanupDegenerate(ctx);
    dedupePointRecords(d);
  }
  return { merged, merged_count: merged.reduce((s, c) => s + c.from.length, 0), new_selection: newSel() };
}

function cleanupDegenerate(ctx) {
  const d = ctx.d;
  const zero = d.members.filter(m => dist(m.pi, m.pj) < GEOM_TOL).map(m => m.uid);
  const bad = [];
  for (const s of d.shells) {
    const out = [];
    for (const c of s.corners) if (!out.length || pkey(out[out.length - 1]) !== pkey(c)) out.push(c);
    if (out.length > 1 && pkey(out[0]) === pkey(out[out.length - 1])) out.pop();
    if (out.length !== s.corners.length) {
      if (out.length < 3) bad.push(s.uid);
      else { s.corners = out; ctx.warnings.push(`shell ${s.uid}: duplicate corners removed`); }
    }
  }
  const seen = new Set(), dup = [];
  for (const m of d.members) {
    if (zero.includes(m.uid)) continue;
    const k = gkMember(m);
    if (seen.has(k)) dup.push(m.uid);
    seen.add(k);
  }
  if (zero.length) ctx.warnings.push(`zero-length member(s) deleted: ${JSON.stringify(zero)}`);
  if (dup.length) ctx.warnings.push(`duplicate member(s) deleted: ${JSON.stringify(dup)}`);
  if (bad.length) ctx.warnings.push(`degenerate shell(s) deleted: ${JSON.stringify(bad)}`);
  deleteObjects(ctx, zero.concat(dup), bad, []);
}

/* ---- align / move */
function restoryObj(ctx, kind, uid) {
  if (kind === "members") { const m = ctx.member(uid); if (m) ctx.restory(m, [m.pi, m.pj]); }
  else if (kind === "shells") { const s = ctx.shell(uid); if (s) ctx.restory(s, s.corners); }
}

/** Align target function (also drives the ghost preview). */
export function alignTarget(params) {
  const mode = params.mode || "coordinate";
  if (mode === "coordinate") {
    const axis = params.axis || "z";
    if (!["x", "y", "z"].includes(axis)) throw new Error("align: 'axis' must be x|y|z");
    const val = num(params, "value"), ax = "xyz".indexOf(axis);
    return p => { const q = [...p]; q[ax] = val; return q; };
  }
  if (mode === "line") {
    const a = f3(params.p1), b = f3(params.p2), ab = sub(b, a);
    if (norm(ab) < GEOM_TOL) throw new Error("align: p1 and p2 coincide");
    return p => lerp(a, b, dot(sub(p, a), ab) / dot(ab, ab));
  }
  if (mode === "plane") {
    const o = f3(params.point);
    let n = f3(params.normal);
    const ln = norm(n);
    if (ln < 1e-12) throw new Error("align: plane normal must be non-zero");
    n = mul(n, 1 / ln);
    return p => sub(p, mul(n, dot(sub(p, o), n)));
  }
  throw new Error("align: 'mode' must be coordinate|line|plane|trim_extend");
}

function opAlign(ctx, sel, params) {
  const mode = params.mode || "coordinate";
  if (mode === "trim_extend") return trimExtend(ctx, sel, params);
  const pts = sel.points.length ? selectionJoints(ctx.d, { points: sel.points }) : selectionJoints(ctx.d, sel);
  if (!pts.length) throw new Error("align: select points (or objects)");
  const target = alignTarget(params);
  const mapping = new Map(pts.map(p => [pkey(p), clean(target(p))]));
  for (const [kind, uid] of remapPoints(ctx, p => mapping.get(pkey(p)) || null)) {
    ctx.markModified(kind, uid);
    restoryObj(ctx, kind, uid);
  }
  cleanupDegenerate(ctx);
  dedupePointRecords(ctx.d);
  return { moved_points: mapping.size, new_selection: sel };
}

/** member uid -> {end, point} for a trim/extend (also the ghost preview). */
export function trimExtendPlan(d, sel, params, warnings = []) {
  const a = f3(params.p1), b = f3(params.p2);
  const tol = num(params, "tol", 1e-3, { positive: true });
  const v = sub(b, a);
  if (norm(v) < GEOM_TOL) throw new Error("align: p1 and p2 coincide");
  const plan = {};
  for (const u of sel.members) {
    const m = d.members.find(x => x.uid === u);
    const pi = m.pi, pj = m.pj, uu = sub(pj, pi), w0 = sub(pi, a);
    const A = dot(uu, uu), B = dot(uu, v), C = dot(v, v), D = dot(uu, w0), E = dot(v, w0);
    const den = A * C - B * B;
    if (den < 1e-12 * A * C) { warnings.push(`member ${u}: parallel to the line`); continue; }
    const t = (B * E - C * D) / den, s = (A * E - B * D) / den;
    let x = lerp(pi, pj, t);
    if (norm(sub(x, lerp(a, b, s))) > tol) { warnings.push(`member ${u}: does not meet the line`); continue; }
    const end = t < 0.5 ? "pi" : "pj";
    x = clean(x);
    if (pkey(x) === pkey(m[end])) continue;
    if (dist(x, m[end === "pi" ? "pj" : "pi"]) < GEOM_TOL) { warnings.push(`member ${u}: would become zero length`); continue; }
    plan[u] = { end, point: x };
  }
  return plan;
}

function trimExtend(ctx, sel, params) {
  if (!sel.members.length) throw new Error("align: trim/extend needs selected frames");
  const plan = trimExtendPlan(ctx.d, sel, params, ctx.warnings);
  const changed = [];
  for (const [u, { end, point }] of Object.entries(plan)) {
    const m = ctx.member(u);
    m[end] = point;
    ctx.markModified("members", u);
    ctx.restory(m, [m.pi, m.pj]);
    changed.push(u);
  }
  return { trimmed: changed, new_selection: sel };
}

function opMove(ctx, sel, params) {
  if (isEmpty(sel)) throw new Error("move: nothing selected");
  const dv = [num(params, "dx", 0), num(params, "dy", 0), num(params, "dz", 0)];
  if (norm(dv) < GEOM_TOL) throw new Error("move: the offset (dx, dy, dz) is zero");
  const keys = new Set(selectionJoints(ctx.d, sel).map(pkey));
  const hosts = new Set([...sel.members, ...sel.shells]);
  for (const td of ctx.d.tendons || []) {
    let h = td.host || [];
    if (typeof h === "string") h = [h];
    if (h.length && h.every(x => hosts.has(x))) td.points = td.points.map(p => clean(add(p, dv)));
  }
  for (const [kind, uid] of remapPoints(ctx, p => (keys.has(pkey(p)) ? add(p, dv) : null))) {
    ctx.markModified(kind, uid);
    restoryObj(ctx, kind, uid);
  }
  cleanupDegenerate(ctx);
  dedupePointRecords(ctx.d);
  const ns = clone(sel);
  ns.points = sel.points.map(p => clean(add(p, dv)));
  return { moved_points: keys.size, new_selection: ns };
}

/* ---- extrude */
function defaultSection(d, kind) {
  const names = Object.keys(d.sections || {});
  if (!names.length) throw new Error("extrude: the model has no frame sections");
  const want = kind === "column" ? "COL" : "BEAM";
  return names.find(n => n.toUpperCase().includes(want)) || names[0];
}
export function autoKind(a, b) {
  const dv = sub(b, a), h = Math.hypot(dv[0], dv[1]);
  if (h < 1e-9) return "column";
  if (Math.abs(dv[2]) < 1e-9) return "beam";
  return "brace";
}

function opExtrude(ctx, sel, params) {
  const d = ctx.d;
  const mode = params.mode || "points_to_frames";
  const dv = [num(params, "dx", 0), num(params, "dy", 0), num(params, "dz", 0)];
  const n = num(params, "n", 1, { integer: true, lo: 1, hi: MAX_COPIES });
  if (norm(dv) < GEOM_TOL) throw new Error("extrude: the offset (dx, dy, dz) is zero");
  const ns = newSel();
  const existing = new Set([...d.members.map(gkMember), ...d.shells.map(gkShell)]);
  if (mode === "points_to_frames") {
    if (!sel.points.length) throw new Error("extrude: select points");
    const kind = params.kind || autoKind([0, 0, 0], dv);
    if (!["column", "beam", "brace"].includes(kind)) throw new Error("extrude: 'kind' must be column|beam|brace");
    const sec = params.section || defaultSection(d, kind);
    if (!(sec in (d.sections || {}))) throw new Error(`extrude: unknown section '${sec}'`);
    const prefix = { column: "C", beam: "B", brace: "D" }[kind];
    for (const p of sel.points)
      for (let k = 0; k < n; k++) {
        const a = clean(add(p, mul(dv, k))), b = clean(add(p, mul(dv, k + 1)));
        const nm = { uid: "", kind, section: sec, pi: a, pj: b };
        if (existing.has(gkMember(nm))) { ctx.warnings.push(`frame ${JSON.stringify(a)}->${JSON.stringify(b)} exists, skipped`); continue; }
        nm.uid = ctx.newUid(prefix, prefix);
        nm.story = "";
        ctx.restory(nm, [a, b]);
        existing.add(gkMember(nm));
        d.members.push(nm);
        ctx.created.members.push(nm.uid);
        ns.members.push(nm.uid);
      }
  } else if (mode === "frames_to_shells") {
    if (!sel.members.length) throw new Error("extrude: select frame members");
    const ssecs = Object.keys(d.shell_sections || {});
    const sec = params.section || ssecs[0];
    if (!sec || !(sec in (d.shell_sections || {}))) throw new Error("extrude: a shell section is required");
    const behavior = params.behavior || "shell";
    if (!["shell", "membrane"].includes(behavior)) throw new Error("extrude: 'behavior' must be shell|membrane");
    for (const u of sel.members) {
      const m = ctx.member(u);
      const horiz = Math.abs(dv[2]) < 1e-9 && Math.abs(m.pi[2] - m.pj[2]) < 1e-9;
      const kind = params.kind || (horiz ? "slab" : "wall");
      if (!["wall", "slab"].includes(kind)) throw new Error("extrude: 'kind' must be wall|slab");
      if (kind === "wall" && behavior === "membrane") throw new Error("extrude: membrane behavior is slab-only");
      for (let k = 0; k < n; k++) {
        const c0 = clean(add(m.pi, mul(dv, k))), c1 = clean(add(m.pj, mul(dv, k)));
        const c2 = clean(add(m.pj, mul(dv, k + 1))), c3 = clean(add(m.pi, mul(dv, k + 1)));
        let corners = [c0, c1, c2, c3];
        if (kind === "slab") {
          let a2 = 0;
          for (let i = 0; i < 4; i++) a2 += corners[i][0] * corners[(i + 1) % 4][1] - corners[(i + 1) % 4][0] * corners[i][1];
          if (a2 < 0) corners = [c0, c3, c2, c1];
        }
        const sh = { uid: "", kind, behavior, section: sec, corners, mesh_size: +(params.mesh_size ?? 1), openings: [] };
        if (existing.has(gkShell(sh))) { ctx.warnings.push("shell exists, skipped"); continue; }
        const p = kind === "slab" ? "S" : "W";
        sh.uid = ctx.newUid(p, p);
        sh.story = "";
        ctx.restory(sh, corners);
        existing.add(gkShell(sh));
        d.shells.push(sh);
        ctx.created.shells.push(sh.uid);
        ns.shells.push(sh.uid);
      }
    }
    if (params.delete_source) deleteObjects(ctx, [...sel.members]);
  } else throw new Error("extrude: 'mode' must be points_to_frames|frames_to_shells");
  return { new_selection: ns };
}

/* ---- join */
const JOIN_FREE = new Set(["uid", "pi", "pj", "story", "length", "releases", "rigid_i", "rigid_j", "joint_offsets", "hinges"]);
const stable = x => JSON.stringify(x, (k, v) => (v && typeof v === "object" && !Array.isArray(v)
  ? Object.keys(v).sort().reduce((o, kk) => { o[kk] = v[kk]; return o; }, {}) : v));
const relToks = m => new Set(String(m.releases || "").split(",").map(t => t.trim()).filter(Boolean));

function coalesceUdls(loads) {
  const out = [];
  for (const ld of loads) {
    let hit = null;
    if ((ld.kind || "udl") === "udl")
      for (const prev of out) {
        if ((prev.kind || "udl") !== "udl") continue;
        if (Math.abs((prev.b ?? 1) - (ld.a ?? 0)) >= 1e-9) continue;
        const keys = new Set([...Object.keys(prev), ...Object.keys(ld)]);
        if ([...keys].every(k => k === "a" || k === "b" || stable(prev[k]) === stable(ld[k]))) { hit = prev; break; }
      }
    if (hit) { hit.b = ld.b ?? 1; if (Math.abs(hit.b - 1) < 1e-9) hit.b = 1; continue; }
    out.push(ld);
  }
  return out;
}

function mapLoad(ld, f, rev, uid) {
  const nl = clone(ld);
  nl.member_uid = uid;
  const kind = ld.kind || "udl";
  const a = ld.a !== undefined ? +ld.a : 0, b = ld.b !== undefined ? +ld.b : 1;
  if (kind === "point" || kind === "moment") { nl.a = r12(f(a)); return nl; }
  let na = f(a), nb = f(b);
  if (rev) {
    [na, nb] = [nb, na];
    if (kind === "trapezoid") { nl.w = ld.w2 || 0; nl.w2 = ld.w || 0; }
  }
  nl.a = r12(na); nl.b = r12(nb);
  return nl;
}

function joinPair(ctx, A, B, J) {
  const d = ctx.d;
  for (const k of new Set([...Object.keys(A), ...Object.keys(B)])) {
    if (JOIN_FREE.has(k)) continue;
    if (stable(A[k]) !== stable(B[k])) return `different ${k}`;
  }
  const ha = A.hinges ?? "none", hb = B.hinges ?? "none";
  if (typeof ha === "string" && typeof hb === "string" && ha !== hb) return "different hinges";
  if ((typeof ha === "string") !== (typeof hb === "string") && ha !== "none" && hb !== "none") return "different hinges";
  const ra = relToks(A), rb = relToks(B);
  const aAtJ = pkey(A.pj) === J ? "pj" : "pi";
  const bAtJ = pkey(B.pi) === J ? "pi" : "pj";
  if (ra.has(aAtJ === "pj" ? "Mj" : "Mi") || rb.has(bAtJ === "pi" ? "Mi" : "Mj")) return "moment release at the shared joint";
  for (const pat of Object.values(d.patterns)) {
    const strip = uid => (pat.thermal_loads || []).filter(x => x.member_uid === uid)
      .map(x => { const c = clone(x); delete c.member_uid; return stable(c); }).sort();
    if (stable(strip(A.uid)) !== stable(strip(B.uid))) return "different temperature loads";
  }
  const La = dist(A.pi, A.pj), Lb = dist(B.pi, B.pj), L = La + Lb;
  let newPi, newPj, fa, fb, bRev;
  if (aAtJ === "pj") {
    newPi = [...A.pi]; newPj = [...(bAtJ === "pi" ? B.pj : B.pi)];
    fa = t => t * La / L;
    bRev = bAtJ === "pj";
    fb = s => (La + (bRev ? 1 - s : s) * Lb) / L;
  } else {
    newPi = [...(bAtJ === "pi" ? B.pj : B.pi)]; newPj = [...A.pj];
    fa = t => (Lb + t * La) / L;
    bRev = bAtJ === "pi";
    fb = s => ((bRev ? 1 - s : s) * Lb) / L;
  }
  const [iPiece, iEnd] = aAtJ === "pj" ? [A, "i"] : [B, bAtJ === "pi" ? "j" : "i"];
  const [jPiece, jEnd] = aAtJ === "pi" ? [A, "j"] : [B, bAtJ === "pi" ? "j" : "i"];
  const relI = relToks(iPiece).has(`M${iEnd}`), relJ = relToks(jPiece).has(`M${jEnd}`);
  const nm = A;
  nm.pi = clean(newPi); nm.pj = clean(newPj);
  nm.releases = [["Mi", relI], ["Mj", relJ]].filter(x => x[1]).map(x => x[0]).join(",");
  nm.rigid_i = +(iPiece[`rigid_${iEnd}`] || 0);
  nm.rigid_j = +(jPiece[`rigid_${jEnd}`] || 0);
  const joa = A.joint_offsets, job = B.joint_offsets;
  if (joa || job) {
    const joOf = (piece, end) => [...(((piece.joint_offsets || {})[end]) || [0, 0, 0])];
    nm.joint_offsets = { system: (joa || job).system || "global", i: joOf(iPiece, iEnd), j: joOf(jPiece, jEnd) };
  }
  if (Array.isArray(ha) || Array.isArray(hb)) {
    const hl = [];
    for (const h of Array.isArray(ha) ? ha : []) hl.push(Object.assign({}, h, { relative_distance: r12(fa(+h.relative_distance || 0)) }));
    for (const h of Array.isArray(hb) ? hb : []) hl.push(Object.assign({}, h, { relative_distance: r12(fb(+h.relative_distance || 0)) }));
    hl.sort((x, y) => x.relative_distance - y.relative_distance);
    nm.hinges = hl.length ? hl : "none";
  }
  delete nm.length;
  for (const pat of Object.values(d.patterns)) {
    const out = [], mine = [];
    let at = null;
    for (const ld of pat.member_loads || []) {
      if (ld.member_uid === A.uid || ld.member_uid === B.uid) {
        const isA = ld.member_uid === A.uid;
        const rev = !isA && bRev;
        if (at === null) at = out.length;
        mine.push(mapLoad(ld, isA ? fa : fb, rev, A.uid));
        if (rev && String(ld.direction || "").startsWith("local"))
          ctx.warnings.push(`member ${B.uid}: local-axis load copied onto reversed span`);
      } else out.push(ld);
    }
    const udls = pat.member_udls || [];
    const wa = udls.filter(x => x.member_uid === A.uid).map(x => x.w).sort((x, y) => x - y);
    const wb = udls.filter(x => x.member_uid === B.uid).map(x => x.w).sort((x, y) => x - y);
    const same = stable(wa) === stable(wb);
    const udlOut = [];
    for (const ld of udls) {
      if (same) { if (ld.member_uid !== B.uid) udlOut.push(ld); }
      else if (ld.member_uid === A.uid || ld.member_uid === B.uid) {
        const f = ld.member_uid === A.uid ? fa : fb;
        const [lo, hi] = [f(0), f(1)].sort((x, y) => x - y);
        mine.push({ member_uid: A.uid, kind: "udl", w: ld.w, w2: 0, a: r12(lo), b: r12(hi), direction: "gravity" });
      } else udlOut.push(ld);
    }
    if ("member_udls" in pat) pat.member_udls = udlOut;
    if (mine.length) out.splice(at === null ? out.length : at, 0, ...coalesceUdls(mine));
    if (out.length || "member_loads" in pat) pat.member_loads = out;
    if (pat.thermal_loads && pat.thermal_loads.length) pat.thermal_loads = pat.thermal_loads.filter(x => x.member_uid !== B.uid);
  }
  d.members = d.members.filter(m => m !== B);
  replaceUidRefs(d, "members", B.uid, [A.uid]);
  const ci = ctx.created.members.indexOf(B.uid);
  if (ci >= 0) ctx.created.members.splice(ci, 1); else ctx.deleted.members.push(B.uid);
  ctx.markModified("members", A.uid);
  return null;
}

function opJoin(ctx, sel) {
  const d = ctx.d;
  if (sel.members.length < 2) throw new Error("join: select at least two frame members");
  const pointRecs = new Set();
  for (const [lst] of pointLists(d)) for (const r of lst) pointRecs.add(pkey(r.point));
  const selected = new Set(sel.members);
  const joined = [], skipped = [];
  let progress = true;
  while (progress) {
    progress = false;
    const cnt = ctx.objectJoints();
    const ends = new Map();
    for (const m of d.members) if (selected.has(m.uid))
      for (const e of ["pi", "pj"]) { const k = pkey(m[e]); if (!ends.has(k)) ends.set(k, []); ends.get(k).push(m); }
    for (const [J, ms] of ends) {
      if (ms.length !== 2 || (cnt.get(J) || 0) !== 2) continue;
      let [A, B] = ms;
      const ua = sub(A.pj, A.pi), ub = sub(B.pj, B.pi);
      if (norm(cross(ua, ub)) > 1e-6 * norm(ua) * norm(ub)) continue;
      const Jp = keyPt(J);
      const faP = pkey(A.pj) === J ? A.pi : A.pj, fbP = pkey(B.pj) === J ? B.pi : B.pj;
      if (dot(sub(faP, Jp), sub(fbP, Jp)) >= 0) continue;
      if (pointRecs.has(J)) { skipped.push({ joint: Jp, reason: "joint carries loads / assignments" }); continue; }
      if (A.section !== B.section) { skipped.push({ joint: Jp, reason: "different sections" }); continue; }
      if (d.members.indexOf(B) < d.members.indexOf(A)) [A, B] = [B, A];
      const why = joinPair(ctx, A, B, J);
      if (why) { skipped.push({ joint: Jp, reason: why }); continue; }
      joined.push({ kept: A.uid, removed: B.uid, joint: Jp });
      selected.delete(B.uid);
      progress = true;
      break;
    }
  }
  const uniq = [], seen = new Set();
  for (const s of skipped) { const k = pkey(s.joint) + "|" + s.reason; if (!seen.has(k)) { seen.add(k); uniq.push(s); } }
  const kept = new Set(joined.map(j => j.kept));
  for (const m of d.members) if (kept.has(m.uid)) ctx.restory(m, [m.pi, m.pj]);
  const ns = newSel();
  ns.members = sel.members.filter(u => selected.has(u));
  return { joined, skipped: uniq, new_selection: ns };
}

/* ---- delete */
function opDelete(ctx, sel) {
  if (isEmpty(sel)) throw new Error("delete: nothing selected");
  deleteObjects(ctx, sel.members, sel.shells, sel.links);
  let n = 0;
  if (sel.points.length) {
    const keys = new Set(sel.points.map(pkey));
    n = deletePointRecords(ctx, keys);
    ctx.deleted.points.push(...[...keys].sort(sortKey).map(keyPt));
  }
  return { point_records_deleted: n, new_selection: newSel() };
}

const OPS = { replicate: opReplicate, divide: opDivide, merge_joints: opMergeJoints, align: opAlign,
  move: opMove, extrude: opExtrude, join: opJoin, delete: opDelete };

/** Apply edit op to a COPY of the model dict → { model, summary }. */
export function applyEdit(model, op, selection, params) {
  if (!(op in OPS)) throw new Error(`unknown edit op '${op}' (one of ${EDIT_OPS.join(", ")})`);
  params = params || {};
  if (typeof params !== "object") throw new Error("'params' must be an object");
  const d = clone(model);
  const ctx = new Ctx(d);
  const sel = normalizeSelection(d, selection);
  const extra = OPS[op](ctx, sel, params);
  return { model: d, summary: ctx.summary(op, extra) };
}
