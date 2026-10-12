/* SkyFrame — ?mock=1 backend of the Rhino / Grasshopper exchange
   (js/rhinolink.js; CONTRACT "Rhino / Grasshopper exchange").

   The browser cannot read a .3dm, so every mock import returns a CANNED
   skyframe.exchange/1 payload (a small frame in mm on Rhino-style layers:
   columns, edge beams, an arc beam, a slab with an opening, a mesh canopy
   and base supports).  A simplified JS port of core/exchange.py turns it
   into model objects: role resolution (explicit role/props → UserText sf_* →
   layer-glob rule → geometry), mm → m, story clustering of horizontal
   beams / slabs, source_ref on every object, merge by (session, id, part)
   keeping SkyFrame-side sections, deletion of the session's missing ids.
   The mock "linked file" keeps an mtime; mockLinkTouch() simulates a save
   in Rhino (version 2 of the payload: one beam deleted, the canopy lifted).

   Exports: mockRhinoImport, mockPreview, mockApply, mockSessions,
   mockRemoveSession, mockLinkState, mockLink, mockUnlink, mockLinkTouch,
   CANNED_LAYERS. */

import { mockSdbImport as mxSdbImport, mockSdbShapes as mxSdbShapes } from "./mock_steeldb.js";

const UNIT = { m: 1, mm: 0.001, cm: 0.01, ft: 0.3048, in: 0.0254 };
const ROLES = ["column", "beam", "brace", "frame", "wall", "slab", "shell", "support", "joint", "link", "ignore"];
const ANG = Math.sin(Math.PI / 180);
const DEFAULT_SECTIONS = { column: "W14X90", beam: "W18X50", brace: "HSS6X6X1/2" };
const PREFIX = { column: "C", beam: "B", brace: "D", wall: "W", slab: "S", link: "L" };
const clone = o => JSON.parse(JSON.stringify(o));
const r9 = v => Math.round(v * 1e9) / 1e9 + 0;
const key = p => p.map(v => Math.round(v * 1e6) / 1e6).join(",");

/* ------------------------------------------------------------ canned file */
export const CANNED_LAYERS = [
  { path: "Structure", color: "#5A5A5A" },
  { path: "Structure::Columns", color: "#DC1E1E" },
  { path: "Structure::Beams", color: "#1E3CDC" },
  { path: "Structure::Slabs", color: "#A0A0A0" },
  { path: "Structure::Canopy", color: "#1EA03C" },
  { path: "Supports", color: "#000000" },
];
function cannedPayload(version = 1, doc = "tower.3dm", session = null) {
  const L = 6000, H = 3000, objs = [];
  const C = [[0, 0], [L, 0], [L, L], [0, L]];
  C.forEach(([x, y], k) => {
    objs.push({ id: `col-${k}`, kind: "curve", layer: "Structure::Columns", attrs: {},
      geom: { type: "line", p: [[x, y, 0], [x, y, H]] } });
    objs.push({ id: `sup-${k}`, kind: "point", layer: "Supports", attrs: {},
      geom: { type: "point", p: [x, y, 0] } });
  });
  for (let k = 0; k < 3; k++) {
    if (version > 1 && k === 2) continue;                    // v2: beam-2 deleted
    const a = C[k], b = C[k + 1];
    objs.push({ id: `beam-${k}`, kind: "curve", layer: "Structure::Beams",
      attrs: k === 0 ? { sf_section: "W18X50" } : {},
      geom: { type: "line", p: [[a[0], a[1], H], [b[0], b[1], H]] } });
  }
  objs.push({ id: "beam-arc", kind: "curve", layer: "Structure::Beams", attrs: {},
    geom: { type: "arc", p: [[0, L, H], [-1000, L / 2, H], [0, 0, H]] } });
  objs.push({ id: "slab-1", kind: "surface", layer: "Structure::Slabs", attrs: {},
    geom: { type: "polygon", outer: [[0, 0, H], [L, 0, H], [L, L, H], [0, L, H]],
      holes: [[[2000, 2000, H], [3000, 2000, H], [3000, 3000, H], [2000, 3000, H]]] } });
  const z2 = H + 1500 + (version > 1 ? 200 : 0);
  objs.push({ id: "canopy", kind: "mesh", layer: "Structure::Canopy", attrs: {},
    geom: { type: "mesh", v: [[0, 0, z2], [L, 0, z2], [L, L, z2 + 400], [0, L, z2 + 400]], f: [[0, 1, 2, 3]] } });
  C.forEach(([x, y], k) => objs.push({ id: `post-${k}`, kind: "curve", layer: "Structure::Columns",
    attrs: k === 0 ? { sf_role: "brace" } : {},
    geom: { type: "line", p: [[x, y, H], [x, y, k > 1 ? z2 + 400 : z2]] } }));
  return { schema: "skyframe.exchange/1",
    source: { app: "rhino3dm", doc, units: "mm", tolerance: 0.01 },
    session: session || "rhino3dm:mock" + doc.replace(/[^a-z0-9]/gi, "").toLowerCase(),
    layers: clone(CANNED_LAYERS), objects: objs, warnings: [] };
}
function layerTable(pl) {
  const rows = new Map(pl.layers.map(l => [l.path, { path: l.path, color: l.color, count: 0, kinds: {} }]));
  for (const o of pl.objects) {
    if (!rows.has(o.layer)) rows.set(o.layer, { path: o.layer, color: "#808080", count: 0, kinds: {} });
    const r = rows.get(o.layer);
    r.count++; r.kinds[o.kind] = (r.kinds[o.kind] || 0) + 1;
  }
  return [...rows.values()];
}
/** File › Import › Rhino (.3dm)… in mock mode: the canned payload. */
export function mockRhinoImport({ fileName = "tower.3dm", path = null } = {}) {
  if (fileName && /\.json$/i.test(fileName)) return null;     // caller parses JSON itself
  const ver = path && mockLinkState().path === path ? LINK.version : 1;
  const pl = cannedPayload(ver, (path || fileName).split(/[\\/]/).pop(), path ? "rhino3dm:mock-link" : null);
  if (path && LINK.path === path) LINK.readMtime = LINK.mtime;
  return { exchange: pl, layers: layerTable(pl), warnings: [], mtime: path ? LINK.mtime : null };
}

/* ------------------------------------------------------------ mock link */
const LINK = { path: null, mtime: null, baseline: null, version: 1, readMtime: null };
export function mockLinkState() {
  if (!LINK.path) return { linked: false, path: null, mtime: null, changed: false };
  return { linked: true, path: LINK.path, mtime: LINK.baseline, current_mtime: LINK.mtime, exists: true,
    changed: LINK.mtime !== LINK.baseline, session: "rhino3dm:mock-link", auto_apply: false };
}
export function mockLink(path) {
  if (!path || !/\.3dm$/i.test(path)) return { error: "'path' must name a .3dm file" };
  Object.assign(LINK, { path, mtime: Date.now() / 1000, version: 1, readMtime: null });
  LINK.baseline = LINK.mtime;
  return mockLinkState();
}
export function mockUnlink() { LINK.path = null; return mockLinkState(); }
/** Simulate "the file was saved in Rhino" (version 2 of the canned file). */
export function mockLinkTouch() { if (LINK.path) { LINK.mtime += 5; LINK.version = 2; } return mockLinkState(); }

/* ------------------------------------------------------------ resolution */
const globRe = g => new RegExp("^" + g.toLowerCase().replace(/[.+^${}()|\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$");
function bits6(v) {
  if (Array.isArray(v) && v.length === 6) return v.map(x => (x ? 1 : 0));
  if (typeof v === "string" && /^[01]{6}$/.test(v.trim())) return [...v.trim()].map(Number);
  return null;
}
function resolve(o, rules, s, warn) {
  const ex = {};
  if (o.role) ex.role = o.role;
  const pr = o.props || {};
  for (const [k, v] of Object.entries(pr)) {
    if (v == null || v === "") continue;
    if (k === "story") ex.story_rule = v;
    else if (k === "thickness" || k === "mesh_size") ex[k] = +v * s;
    else ex[k] = v;
  }
  const A = {};
  for (const [k, v] of Object.entries(o.attrs || {})) A[k.toLowerCase()] = String(v).trim();
  const map = { sf_role: "role", sf_section: "section", sf_material: "material", sf_story: "story_rule",
    sf_angle: "angle", sf_shell_section: "shell_section", sf_restraint: "restraint" };
  for (const [ak, f] of Object.entries(map)) {
    if (!A[ak] || f in ex) continue;
    if (f === "role" && !ROLES.includes(A[ak].toLowerCase())) { warn.push(`${o.id}: UserText ${ak}=${A[ak]} ignored`); continue; }
    ex[f] = f === "angle" ? +A[ak] : f === "role" ? A[ak].toLowerCase() : A[ak];
  }
  const rule = (rules || []).find(r => globRe(r.layer).test((o.layer || "").toLowerCase()));
  const res = Object.assign({}, ex);
  if (rule) for (const k of ["role", "section", "material", "shell_section", "story_rule", "angle", "thickness", "mesh_size", "restraint"])
    if (rule[k] != null && rule[k] !== "" && !(k in res)) res[k] = rule[k];
  res.explicit = new Set(Object.keys(ex));
  res.rule = rule ? rule.layer : null;
  return res;
}
function classify(a, b) {
  const d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const L = Math.hypot(...d) || 1;
  if (Math.hypot(d[0], d[1]) <= L * ANG) return "column";
  if (Math.abs(d[2]) <= L * ANG) return "beam";
  return "brace";
}
function newell(pts) {
  const n = [0, 0, 0];
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    n[0] += (a[1] - b[1]) * (a[2] + b[2]); n[1] += (a[2] - b[2]) * (a[0] + b[0]); n[2] += (a[0] - b[0]) * (a[1] + b[1]);
  }
  const l = Math.hypot(...n) || 1;
  return n.map(v => v / l);
}
function parts(o, res, s) {
  const g = o.geom, P = p => p.map(v => r9(v * s));
  let role = res.role;
  if (role === "ignore") return ["ignore", []];
  const out = [];
  if (o.kind === "curve") {
    let segs = [];
    if (g.type === "line") segs = [[P(g.p[0]), P(g.p[1]), null]];
    else if (g.type === "polyline") for (let i = 0; i + 1 < g.p.length; i++) segs.push([P(g.p[i]), P(g.p[i + 1]), null]);
    else if (g.type === "arc") segs = [[P(g.p[0]), P(g.p[2]), { type: "arc", via: P(g.p[1]), segments: null, local2: "in_plane" }]];
    else {
      const sm = g.samples || [], n = Math.min(8, sm.length - 1), pts = [];
      for (let k = 0; k <= n; k++) pts.push(P(sm[Math.round(k * (sm.length - 1) / n)]));
      for (let k = 0; k < n; k++) segs.push([pts[k], pts[k + 1], null]);
    }
    const whole = g.type === "arc" || g.type === "nurbs" ? classify(segs[0][0], segs[segs.length - 1][1]) : null;
    if (role === "link") { segs.forEach(([a, b]) => out.push({ type: "link", pi: a, pj: b })); return ["link", out]; }
    const kinds = new Set();
    for (const [a, b, c] of segs) {
      const k = ["column", "beam", "brace"].includes(role) ? role : (whole || classify(a, b));
      kinds.add(k);
      out.push({ type: "member", kind: k, pi: a, pj: b, curve: c });
    }
    return [["column", "beam", "brace"].includes(role) ? role : (kinds.size === 1 ? [...kinds][0] : "frame"), out];
  }
  if (o.kind === "point") {
    const p = P(g.p);
    if (!role) role = res.restraint || /support/i.test(o.layer || "") ? "support" : "joint";
    out.push(role === "support" ? { type: "support", point: p, restraints: bits6(res.restraint) || [1, 1, 1, 1, 1, 1] }
      : { type: "joint", point: p });
    return [role, out];
  }
  const faces = g.type === "polygon" ? [{ c: g.outer.map(P), h: (g.holes || []).map(h => h.map(P)) }]
    : (g.type === "mesh" ? g : g.mesh).f.map(f => ({ c: f.map(i => P((g.type === "mesh" ? g : g.mesh).v[i])), h: [] }));
  const autos = new Set();
  for (const f of faces) {
    const n = newell(f.c), nz = Math.abs(n[2]);
    const auto = nz >= Math.cos(Math.PI / 180) ? "slab" : nz <= ANG ? "wall" : "shell";
    autos.add(auto);
    const kind = role === "wall" || role === "slab" ? role : (nz >= Math.SQRT1_2 ? "slab" : "wall");
    out.push({ type: "shell", kind, corners: kind === "slab" && n[2] < 0 ? f.c.slice().reverse() : f.c, holes: f.h });
  }
  return [role || (autos.size === 1 && !autos.has("shell") ? [...autos][0] : "shell"), out];
}

/* ------------------------------------------------------------ sections */
function frameSection(m, name, kind, warn) {
  m.sections = m.sections || {};
  if (name && m.sections[name]) return name;
  const steel = () => {
    m.materials = m.materials || {};
    let mat = Object.keys(m.materials).find(k => (m.materials[k].material_type || "") === "steel");
    if (!mat) { mat = "A992Fy50"; m.materials[mat] = { name: mat, material_type: "steel", E: 199947980, nu: 0.3, unit_weight: 76.9729 }; }
    return mat;
  };
  const tryDb = n => {
    const hit = (mxSdbShapes({ standard: "aisc", q: n, limit: 50 }).shapes || []).find(r => r.name.toUpperCase() === n.toUpperCase());
    if (!hit) return null;
    const r = mxSdbImport(m, { standard: "aisc", names: [hit.name], material: steel() });
    return r.error ? null : hit.name;
  };
  if (name) {
    const got = tryDb(name);
    if (got) return got;
    warn.push(`unknown section '${name}' (not in the model nor the steel databases) -- default used`);
  }
  const want = kind === "column" ? "COL" : kind === "brace" ? "BR" : "BEAM";
  const pick = Object.keys(m.sections).find(n => n.toUpperCase().includes(want));
  if (pick) return pick;
  return tryDb(DEFAULT_SECTIONS[kind]) || Object.keys(m.sections)[0];
}
function shellSection(m, name, t, kind) {
  m.shell_sections = m.shell_sections || {};
  if (name && m.shell_sections[name]) return name;
  if (!name && t == null) {
    const want = kind === "slab" ? "SLAB" : "WALL";
    const pick = Object.keys(m.shell_sections).find(n => n.toUpperCase().includes(want)) || Object.keys(m.shell_sections)[0];
    if (pick) return pick;
  }
  t = t || 0.2;
  m.materials = m.materials || {};
  let mat = Object.keys(m.materials).find(k => (m.materials[k].material_type || "concrete") === "concrete");
  if (!mat) { mat = "4000Psi"; m.materials[mat] = { name: mat, material_type: "concrete", E: 24855600, nu: 0.2, unit_weight: 23.5631 }; }
  const nm = name || `Shell${+(t * 1000).toFixed(3)}mm`;
  if (!m.shell_sections[nm]) m.shell_sections[nm] = { name: nm, material: mat, thickness: t, mod: 1 };
  return nm;
}

/* ------------------------------------------------------------ apply */
function build(model, pl, mapping, mode = "merge") {
  if (!pl || pl.schema !== "skyframe.exchange/1") return { error: "exchange.schema must be 'skyframe.exchange/1'" };
  const s = UNIT[(pl.source || {}).units || "m"];
  if (!s) return { error: "exchange.source.units must be one of m, mm, cm, ft, in" };
  if (typeof pl.session !== "string" || !pl.session) return { error: "exchange.session must be a non-empty string" };
  const m = clone(model);
  for (const k of ["members", "shells", "supports", "links", "stories"]) m[k] = m[k] || [];
  m.patterns = m.patterns || {};
  const ses = pl.session, rules = (mapping && mapping.rules) || [];
  const warn = [...(pl.warnings || [])];
  const stTol = (mapping && +mapping.story_tolerance > 0) ? +mapping.story_tolerance : 0.05;
  const items = [], objects = [];
  for (const o of pl.objects || []) {
    const res = resolve(o, rules, s, warn);
    const [role, ps] = parts(o, res, s);
    const row = { id: o.id, layer: o.layer, role, rule: res.rule, section: null, story: null, parts: ps.length, status: "skip" };
    objects.push(row);
    ps.forEach((p, k) => items.push({ o, res, row, p, k: `${o.id}#${k}`, part: k }));
  }
  // stories: horizontal beams / slabs of beam/frame/slab objects
  const levels = [];
  for (const it of items) {
    if (!["beam", "frame", "slab"].includes(it.row.role) || (it.res.story_rule && it.res.story_rule !== "auto")) continue;
    const zs = it.p.type === "member" ? [it.p.pi[2], it.p.pj[2]] : it.p.type === "shell" ? it.p.corners.map(c => c[2]) : [];
    if (zs.length && Math.max(...zs) - Math.min(...zs) <= 1e-3 && (it.p.kind === "beam" || it.p.kind === "slab")) levels.push(zs[0]);
  }
  const created_st = [];
  levels.sort((a, b) => a - b);
  const allZ = items.flatMap(it => it.p.type === "member" ? [it.p.pi[2], it.p.pj[2]] : it.p.type === "shell" ? it.p.corners.map(c => c[2]) : [it.p.point ? it.p.point[2] : it.p.pi[2]]);
  const base = m.stories.length ? m.stories[0].elevation - m.stories[0].height : Math.min(0, ...allZ);
  const newL = [];
  for (const z of levels) if (z > base + stTol && !m.stories.some(st => Math.abs(st.elevation - z) <= stTol) && !newL.some(q => Math.abs(q - z) <= stTol)) newL.push(z);
  if (newL.length) {
    const st = m.stories.map(x => Object.assign({}, x)).concat(newL.map(z => ({ name: "", height: 0, elevation: z })));
    st.sort((a, b) => a.elevation - b.elevation);
    let n = m.stories.length, prev = base;
    const used = new Set(m.stories.map(x => x.name));
    for (const x of st) {
      if (!x.name) { do { n++; } while (used.has(`Story${n}`)); x.name = `Story${n}`; used.add(x.name); created_st.push(x.name); }
      x.height = r9(x.elevation - prev); prev = x.elevation;
    }
    m.stories = st;
  }
  const storyOf = (it) => {
    const r = it.res.story_rule;
    if (r && r !== "auto" && m.stories.some(x => x.name === r)) return r;
    const zs = it.p.type === "member" ? [it.p.pi[2], it.p.pj[2]] : it.p.type === "shell" ? it.p.corners.map(c => c[2]) : [0];
    const z = Math.max(...zs);
    const hit = m.stories.find(x => Math.abs(x.elevation - z) <= stTol) || m.stories.find(x => x.elevation - x.height < z && z < x.elevation);
    if (hit) return hit.name;
    if (!m.stories.length) return "";
    const top = m.stories.reduce((a, b) => (b.elevation > a.elevation ? b : a));
    return z >= top.elevation ? top.name : m.stories.reduce((a, b) => (b.elevation < a.elevation ? b : a)).name;
  };
  // existing session objects
  const idx = coll => { const out = new Map(); for (const o of m[coll]) { const r = o.source_ref; if (r && r.session === ses) out.set(`${r.id}#${r.part || 0}`, o); } return out; };
  let exM = idx("members"), exS = idx("shells"), exP = idx("supports"), exL = idx("links");
  const reg = clone(((m.exchange_sessions || {})[ses]) || {});
  const sum = {}; for (const k of ["create", "update", "delete", "unchanged"]) sum[k] = { members: 0, shells: 0, joints: 0, links: 0 };
  const deleted = { members: [], shells: [], links: [], supports: [], joints: [] };
  const dropUids = (mem, sh, lk) => {
    const ms = new Set(mem), ss = new Set(sh), ls = new Set(lk);
    m.members = m.members.filter(o => !ms.has(o.uid)); m.shells = m.shells.filter(o => !ss.has(o.uid)); m.links = m.links.filter(o => !ls.has(o.uid));
    for (const p of Object.values(m.patterns)) {
      for (const k of ["member_loads", "member_udls", "thermal_loads"]) if (p[k]) p[k] = p[k].filter(x => !ms.has(x.member_uid));
      for (const k of ["area_loads"]) if (p[k]) p[k] = p[k].filter(x => !ss.has(x.region_uid));
    }
    for (const g of Object.values(m.groups || {})) { if (g.members) g.members = g.members.filter(u => !ms.has(u)); if (g.shells) g.shells = g.shells.filter(u => !ss.has(u)); }
  };
  if (mode === "replace") {
    deleted.members = [...exM.values()].map(o => o.uid); deleted.shells = [...exS.values()].map(o => o.uid); deleted.links = [...exL.values()].map(o => o.uid);
    sum.delete.members = exM.size; sum.delete.shells = exS.size; sum.delete.links = exL.size; sum.delete.joints = exP.size + Object.keys(reg.joints || {}).length;
    dropUids(deleted.members, deleted.shells, deleted.links);
    m.supports = m.supports.filter(x => !(x.source_ref && x.source_ref.session === ses));
    reg.joints = {}; exM = new Map(); exS = new Map(); exP = new Map(); exL = new Map();
  }
  const seen = new Set();
  const status = (row, st) => { row.status = row.status === "skip" || row.status === st ? st : "update"; };
  const nextUid = pre => { let mx = 0; const re = new RegExp(`^${pre}(\\d+)$`); for (const c of ["members", "shells", "links"]) for (const o of m[c]) { const g = re.exec(o.uid); if (g) mx = Math.max(mx, +g[1]); } return pre + (mx + 1); };
  const same = (a, b) => key(a) === key(b);
  for (const it of items) {
    const { p, res, row } = it, ref = { session: ses, id: it.o.id, part: it.part }, ex = res.explicit, story = storyOf(it);
    seen.add(p.type + "|" + it.k);
    if (p.type === "member") {
      const old = exM.get(it.k);
      if (old) {
        const ch = !same(old.pi, p.pi) || !same(old.pj, p.pj);
        old.pi = p.pi; old.pj = p.pj;
        if (p.curve) old.curve = p.curve; else delete old.curve;
        if (ex.has("section")) old.section = frameSection(m, res.section, p.kind, warn);
        if (ex.has("role") && ["column", "beam", "brace"].includes(res.role)) old.kind = p.kind;
        if (ch) old.story = story;
        sum[ch ? "update" : "unchanged"].members++;
        row.section = old.section; row.story = old.story; status(row, ch ? "update" : "unchanged");
        continue;
      }
      const sec = frameSection(m, res.section, p.kind, warn);
      const md = { uid: nextUid(PREFIX[p.kind]), kind: p.kind, section: sec, pi: p.pi, pj: p.pj, story, releases: "",
        angle: +res.angle || 0, rigid_i: 0, rigid_j: 0, rigid_factor: 1, source_ref: ref };
      if (p.curve) md.curve = p.curve;
      m.members.push(md); sum.create.members++;
      row.section = sec; row.story = story; status(row, "create");
    } else if (p.type === "shell") {
      const old = exS.get(it.k);
      const ops = p.holes.map(h => ({ u0: 0, v0: 0, u1: 1, v1: 1, polygon: h }));
      if (old) {
        const ch = old.corners.length !== p.corners.length || old.corners.some((c, i) => !same(c, p.corners[i]));
        old.corners = p.corners; old.openings = ops;
        if (ex.has("shell_section") || ex.has("thickness")) old.section = shellSection(m, res.shell_section, res.thickness, p.kind);
        if (ch) old.story = story;
        sum[ch ? "update" : "unchanged"].shells++;
        row.section = old.section; row.story = old.story; status(row, ch ? "update" : "unchanged");
        continue;
      }
      const sec = shellSection(m, res.shell_section, res.thickness, p.kind);
      m.shells.push({ uid: nextUid(PREFIX[p.kind]), kind: p.kind, behavior: "shell", section: sec, corners: p.corners,
        mesh_size: res.mesh_size || 1, story, openings: ops, pier: "", area_spring: null, wind_cp: null, source_ref: ref });
      sum.create.shells++; row.section = sec; row.story = story; status(row, "create");
    } else if (p.type === "support") {
      const old = exP.get(it.k);
      if (old) {
        const ch = !same(old.point, p.point);
        old.point = p.point; if (ex.has("restraint")) old.restraints = p.restraints;
        sum[ch ? "update" : "unchanged"].joints++; status(row, ch ? "update" : "unchanged");
        continue;
      }
      m.supports.push({ point: p.point, restraints: p.restraints, source_ref: ref });
      sum.create.joints++; status(row, "create");
    } else if (p.type === "joint") {
      reg.joints = reg.joints || {};
      const had = reg.joints[it.k];
      const ch = had && !same(had, p.point);
      reg.joints[it.k] = p.point;
      sum[had ? (ch ? "update" : "unchanged") : "create"].joints++; status(row, had ? (ch ? "update" : "unchanged") : "create");
    } else {
      const old = exL.get(it.k);
      if (old) { old.pi = p.pi; old.pj = p.pj; sum.unchanged.links++; status(row, "unchanged"); continue; }
      m.links.push({ uid: nextUid("L"), pi: p.pi, pj: p.pj, stiffness: (res.stiffness || [1e6, 1e6, 1e6, 1e6, 1e6, 1e6]), link_type: "elastic", params: {}, source_ref: ref });
      sum.create.links++; status(row, "create");
    }
  }
  if (mode === "merge") {
    const gone = { m: [], s: [], l: [] };
    const dels = new Set();
    for (const [k, o] of exM) if (!seen.has("member|" + k)) { gone.m.push(o.uid); sum.delete.members++; dels.add(o.source_ref.id); }
    for (const [k, o] of exS) if (!seen.has("shell|" + k)) { gone.s.push(o.uid); sum.delete.shells++; dels.add(o.source_ref.id); }
    for (const [k, o] of exL) if (!seen.has("link|" + k)) { gone.l.push(o.uid); sum.delete.links++; dels.add(o.source_ref.id); }
    const supGone = new Set([...exP].filter(([k]) => !seen.has("support|" + k)).map(([, o]) => o));
    for (const o of supGone) { sum.delete.joints++; deleted.supports.push(o.source_ref.id); dels.add(o.source_ref.id); }
    m.supports = m.supports.filter(o => !supGone.has(o));
    for (const k of Object.keys(reg.joints || {})) if (!seen.has("joint|" + k)) { delete reg.joints[k]; sum.delete.joints++; deleted.joints.push(k.split("#")[0]); dels.add(k.split("#")[0]); }
    dropUids(gone.m, gone.s, gone.l);
    deleted.members = gone.m; deleted.shells = gone.s; deleted.links = gone.l;
    for (const id of dels) if (!objects.some(r => r.id === id)) objects.push({ id, layer: null, role: null, rule: null, section: null, story: null, parts: 0, status: "delete" });
  }
  Object.assign(reg, { app: pl.source.app, doc: pl.source.doc, units: pl.source.units, tolerance: pl.source.tolerance,
    last_update: new Date().toISOString().replace(/\.\d+Z$/, "Z") });
  if (reg.joints && !Object.keys(reg.joints).length) delete reg.joints;
  m.exchange_sessions = Object.assign({}, m.exchange_sessions || {}, { [ses]: reg });
  const report = { schema: "skyframe.exchange/1", session: ses, mode, source: pl.source, summary: sum,
    deleted, stories_created: created_st, warnings: warn, repair: null, objects };
  return { model: m, report };
}

export function mockPreview(model, body = {}) {
  const r = build(model, body.exchange, typeof body.mapping === "object" ? body.mapping : null, body.mode || "merge");
  if (r.error) return r;
  const { summary, warnings, objects, session, stories_created } = r.report;
  return { summary, warnings, objects, session, mode: body.mode || "merge", stories_created, valid: true };
}
export function mockApply(model, body = {}) {
  const r = build(model, body.exchange, typeof body.mapping === "object" ? body.mapping : null, body.mode || "merge");
  if (r.error) return r;
  if (LINK.path && body.exchange.session === "rhino3dm:mock-link" && LINK.readMtime != null) { LINK.baseline = LINK.readMtime; LINK.readMtime = null; }
  const m = r.model;
  return { report: r.report, model: m, model_summary: { name: m.name, members: m.members.length, shells: m.shells.length,
    links: m.links.length, supports: m.supports.length, stories: m.stories.map(s => s.name), sessions: mockSessions(m) } };
}

/** Sessions present in a model (same shape as GET /api/exchange/status). */
export function mockSessions(model) {
  const reg = (model && model.exchange_sessions) || {};
  const cnt = {};
  const add = (ref, coll) => { if (!ref) return; const c = cnt[ref.session] = cnt[ref.session] || { ids: new Set(), members: 0, shells: 0, links: 0, supports: 0 }; c[coll]++; c.ids.add(ref.id); };
  for (const o of (model && model.members) || []) add(o.source_ref, "members");
  for (const o of (model && model.shells) || []) add(o.source_ref, "shells");
  for (const o of (model && model.links) || []) add(o.source_ref, "links");
  for (const o of (model && model.supports) || []) add(o.source_ref, "supports");
  const names = [...new Set([...Object.keys(reg), ...Object.keys(cnt)])];
  return names.map(ses => {
    const c = cnt[ses] || { ids: new Set(), members: 0, shells: 0, links: 0, supports: 0 }, info = reg[ses] || {};
    const joints = Object.keys(info.joints || {});
    joints.forEach(k => c.ids.add(k.replace(/#\d+$/, "")));
    return { session: ses, objects: c.ids.size, members: c.members, shells: c.shells, links: c.links, supports: c.supports,
      joints: joints.length, last_update: info.last_update || null, app: info.app || null, doc: info.doc || null, units: info.units || null };
  });
}
export function mockRemoveSession(model, session) {
  if (!mockSessions(model).some(s => s.session === session)) return { error: `unknown exchange session '${session}'` };
  const m = clone(model);
  const mine = o => o.source_ref && o.source_ref.session === session;
  const mem = new Set(m.members.filter(mine).map(o => o.uid)), sh = new Set(m.shells.filter(mine).map(o => o.uid));
  const out = { session, deleted: { members: [...mem], shells: [...sh], links: (m.links || []).filter(mine).map(o => o.uid),
    supports: (m.supports || []).filter(mine).length, joints: Object.keys(((m.exchange_sessions || {})[session] || {}).joints || {}).length }, warnings: [] };
  m.members = m.members.filter(o => !mine(o)); m.shells = m.shells.filter(o => !mine(o));
  m.links = (m.links || []).filter(o => !mine(o)); m.supports = (m.supports || []).filter(o => !mine(o));
  for (const p of Object.values(m.patterns || {})) {
    for (const k of ["member_loads", "member_udls", "thermal_loads"]) if (p[k]) p[k] = p[k].filter(x => !mem.has(x.member_uid));
    if (p.area_loads) p.area_loads = p.area_loads.filter(x => !sh.has(x.region_uid));
  }
  if (m.exchange_sessions) { delete m.exchange_sessions[session]; if (!Object.keys(m.exchange_sessions).length) delete m.exchange_sessions; }
  return { model: m, report: out };
}

export default { mockRhinoImport, mockPreview, mockApply, mockSessions, mockRemoveSession, mockLinkState, mockLink, mockUnlink, mockLinkTouch };
