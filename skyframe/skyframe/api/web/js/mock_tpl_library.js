/* SkyFrame D4 — ?mock=1 backend of the structural-system template library
   (GET /api/model/templates system entries + POST /api/model/template for
   the 14 system ids of skyframe/core/templates_sys.py).

   * mockTplSysCatalogue()           the backend catalogue entries (generated
                                     data, js/mock_tpl_library_data.js)
   * mockTplSysNormalize(id, params) parameter validation (same messages as
                                     the backend for the common cases)
   * mockTplSysBuild(id, params)     {model, summary}: the backend geometry
                                     (js/tpl_lib_geom.js port, same uids),
                                     database sections / library materials,
                                     DEAD / SDL / LIVE / LROOF, mass source,
                                     simplified code loads (ELF V = Cs W with
                                     an inverted triangle, a two-period
                                     spectrum RSF, RSX / RSY) and grouped
                                     LRFD / code combinations.
   Isolator links get K = 4 pi^2 M / T^2 like the backend. */

import { TPL_SYS_CATALOGUE as mtCat, TPL_SYS_SHAPES as mtShapes, TPL_SYS_MATERIALS as mtMats } from "./mock_tpl_library_data.js";
import { tplSysGeometry as mtGeometry, tplAlpha as mtAlpha, tplOutriggerLevels as mtLevels } from "./tpl_lib_geom.js";
import { MOCK_AISC as mtAisc } from "./mock_steeldb_data.js";

const G = 9.80665;
const r9 = v => Math.round(v * 1e9) / 1e9;
const clone = o => JSON.parse(JSON.stringify(o));
export const MT_SYSTEM_IDS = mtCat.map(c => c.id);
const MAT_DEFAULTS = { astm_aci: ["A992", "4000psi"], eurocode: ["S355", "C30/37"], is: ["E250", "M30"], bs: ["S355-t16", "C40-BS8110"], as_nzs: ["350-AS3678", "N40"] };

export function mockTplSysCatalogue() { return clone(mtCat); }
export const mockTplSysHas = id => MT_SYSTEM_IDS.includes(id);

export function mockTplSysNormalize(id, params = {}) {
  const ent = mtCat.find(c => c.id === id);
  if (!ent) throw new Error(`unknown template '${id}'`);
  const meta = Object.fromEntries(ent.meta.map(m => [m.key, m]));
  const bad = Object.keys(params).filter(k => !(k in meta)).sort();
  if (bad.length) throw new Error(`${id}: unknown parameter(s) ['${bad.join("', '")}']`);
  const p = {};
  for (const [k, m] of Object.entries(meta)) {
    let v = k in params ? params[k] : ent.params[k];
    if (v == null) { p[k] = null; continue; }
    if (["str", "steel", "choice"].includes(m.kind)) {
      if (typeof v !== "string") throw new Error(`${id}: ${k} must be a string`);
      v = v.trim();
      if (m.kind === "choice" && !m.options.includes(v)) throw new Error(`${id}: ${k} must be one of ${JSON.stringify(m.options)} (got '${v}')`);
      p[k] = m.kind === "steel" ? (v || null) : v;
      continue;
    }
    if (typeof v !== "number" || !isFinite(v)) throw new Error(`${id}: ${k} must be a number`);
    if (m.kind === "int" && !Number.isInteger(v)) throw new Error(`${id}: ${k} must be an integer`);
    if ((m.min != null && v < m.min) || (m.max != null && v > m.max)) throw new Error(`${id}: ${k} must be in [${m.min}, ${m.max}] (got ${v})`);
    p[k] = v;
  }
  if (!["asce7", "ec8", "is1893", "none"].includes(p.code)) throw new Error("code must be one of asce7, ec8, is1893, none");
  if (id === "diagrid") { if (!("bays_y" in params)) p.bays_y = p.bays_x; if (!("bay_y" in params)) p.bay_y = p.bay_x; }
  p.name = p.name || ent.label;
  const md = MAT_DEFAULTS[p.material_standard];
  p.steel_material = p.steel_material || md[0];
  p.concrete_material = p.concrete_material || md[1];
  checkSystem(id, p);
  return p;
}

function checkSystem(t, p) {
  const need = (ok, msg) => { if (!ok) throw new Error(`${t}: ${msg}`); };
  if (t === "rc_dual_core") {
    need(p.core_bays_x < p.bays_x && p.core_bays_y < p.bays_y, "the core must leave at least one bay of frame");
    need((p.bays_x - p.core_bays_x) % 2 === 0 && (p.bays_y - p.core_bays_y) % 2 === 0, "bays - core bays must be even (a centred core)");
  }
  if (t === "rc_framed_tube" && p.inner_core === "yes") {
    need(p.bays_x % 2 === p.bays_y % 2, "bays_x and bays_y must have the same parity for the core");
    const lo = p.bays_x % 2 ? 3 : 4;
    need(p.bays_x >= lo && p.bays_y >= lo, "inner core needs >= 3 bays each way (odd) or >= 4 (even)");
  }
  if (t === "rc_shear_wall") {
    need(2 * p.pier_length + p.opening < p.bays_x * p.bay_x, "coupled wall (2 piers + opening) longer than the building");
    need(p.planar_length < p.bays_y * p.bay_y, "planar wall longer than the end face");
    need(p.spandrel_depth < Math.min(p.story_height, p.first_story_height || p.story_height) - 0.5, "spandrel depth must leave a 0.5 m door opening");
  }
  if (t === "outrigger") {
    need(p.bays_x >= 3 && p.bays_y >= 3 && p.bays_x % 2 === 1 && p.bays_y % 2 === 1, "needs an odd number (>= 3) of bays each way (central core bay)");
    mtLevels(p.outrigger_levels, p.stories);
  }
  if (t === "podium_tower") {
    const nt = p.towers;
    need(p.tower_bays_x * nt + (nt - 1) <= p.bays_x && p.tower_bays_y <= p.bays_y, "the tower(s) do not fit on the podium");
    if (nt === 1) need((p.bays_x - p.tower_bays_x) % 2 === 0 && (p.bays_y - p.tower_bays_y) % 2 === 0, "podium bays - tower bays must be even (centred tower)");
    else need((p.bays_y - p.tower_bays_y) % 2 === 0, "podium bays_y - tower bays_y must be even");
  }
  if (t === "diagrid") {
    need(p.bays_x === p.bays_y && p.bay_x === p.bay_y, "the diagrid tower is square: bays_x = bays_y, bay_x = bay_y");
    need(p.bays_x >= 2, "needs >= 2 panels per face");
    need(p.core_size < 0.8 * p.bays_x * p.bay_x, "core larger than 80 % of the plan");
  }
  if (t === "industrial_shed") {
    need(p.stories === 1, "a shed has exactly one story");
    need(p.bays_y >= 2, "needs >= 2 bays along Y (frame spacing)");
    if (p.roof === "truss") need(p.truss_panels % 2 === 0, "truss_panels must be even");
  }
  if (t === "steel_ebf") need(p.link_length < 0.5 * Math.min(p.bay_x, p.bay_y), "link length must be < half the bay");
}

function shapeRecord(name) {
  const up = String(name).replace(/\s+/g, "").toUpperCase();
  for (const [k, v] of Object.entries(mtShapes)) if (k.toUpperCase() === up) return { name: k, sec: v };
  const r = (mtAisc.shapes || []).find(s => s.name.toUpperCase() === up);
  if (r) {
    const pr = r.props;
    return { name: r.name, sec: { name: r.name, material: "", A: pr.A, I33: pr.I33, I22: pr.I22, J: pr.J, b: r.dims.bf || r.dims.b || r.dims.OD || 0.2,
      h: r.dims.d || r.dims.Ht || r.dims.OD || 0.2, As2: pr.As2, As3: pr.As3, shape: r.type, db_ref: { standard: "aisc", name: r.name } } };
  }
  return null;
}

const rectSec = (n, b, h, mat) => {
  const a = Math.max(b, h) / 2, c = Math.min(b, h) / 2;
  return { name: n, material: mat, A: b * h, I33: b * h ** 3 / 12, I22: h * b ** 3 / 12, J: a * c ** 3 * (16 / 3 - 3.36 * (c / a) * (1 - c ** 4 / (12 * a ** 4))), b, h };
};
const area3 = c => {   // planar polygon area (Newell)
  let nx = 0, ny = 0, nz = 0;
  for (let i = 0; i < c.length; i++) { const a = c[i], b = c[(i + 1) % c.length]; nx += (a[1] - b[1]) * (a[2] + b[2]); ny += (a[2] - b[2]) * (a[0] + b[0]); nz += (a[0] - b[0]) * (a[1] + b[1]); }
  return 0.5 * Math.hypot(nx, ny, nz);
};
const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);

function gridDict(name, xs, ys, origin = [0, 0]) {
  return { kind: "orthogonal", name, origin, rotation: 0, x_lines: xs, y_lines: ys, x_labels: xs.map((_, i) => mtAlpha(i)), y_labels: ys.map((_, j) => String(j + 1)), radii: [], theta_deg: [] };
}

export function mockTplSysBuild(id, params = {}) {
  const p = mockTplSysNormalize(id, params);
  const ent = mtCat.find(c => c.id === id);
  const std = p.material_standard;
  const steelM = mtMats[`${std}:${p.steel_material}`] || (std === "astm_aci" && p.steel_material === "A992" ? mtMats["astm_aci:A992"] : null);
  const concM = mtMats[`${std}:${p.concrete_material}`];
  const uses = new Set();
  const steelName = key => {
    const meta = ent.meta.find(m => m.key === key);
    const nm = p[key] || meta.aisc_default;
    const rec = shapeRecord(nm);
    if (!rec) throw new Error(`${id}: ${key}: shape '${nm}' is not in the ${p.steel_db} database (mock: AISC defaults only)`);
    return rec.name;
  };
  const g = mtGeometry(id, p, steelName);
  const m = { name: p.name, materials: {}, sections: {}, designer_sections: {}, shell_sections: {}, grid: null, grid_systems: [], stories: [],
    members: [], shells: [], base_fixity: "fixed", supports: [], spring_supports: [], line_springs: [], links: [],
    rigid_diaphragms: g.diaphragm !== "none", diaphragm: g.diaphragm === "none" ? "none" : "rigid", story_diaphragm: {}, patterns: {}, cases: {}, combos: {}, rs_cases: {},
    spectrum_functions: {}, mass_source: {}, num_modes: 12, frame_axis_convention: "etabs", display_units: p.units };
  for (const s of Object.values(g.secs)) uses.add(s.type === "steel" ? "steel" : "conc");
  for (const s of Object.values(g.shellSecs)) uses.add(s.mat);
  const matName = kind => {
    const rec = kind === "steel" ? steelM : concM;
    const nm = kind === "steel" ? p.steel_material : p.concrete_material;
    if (!rec) throw new Error(`${id}: material '${nm}' is not in the ${std} library (mock: default grades only)`);
    m.materials[rec.name] = clone(rec);
    return rec.name;
  };
  const sMat = uses.has("steel") ? matName("steel") : null, cMat = uses.has("conc") ? matName("conc") : null;
  for (const [nm, s] of Object.entries(g.secs)) {
    if (s.type === "steel") { const r = shapeRecord(nm); m.sections[nm] = { ...clone(r.sec), material: sMat }; }
    else m.sections[nm] = rectSec(nm, s.b, s.h, cMat);
  }
  for (const [nm, s] of Object.entries(g.shellSecs)) {
    let t = s.t;
    const ss = { name: nm, material: s.mat === "steel" ? sMat : cMat, thickness: t, layered: null };
    if (s.deck) {
      const d = s.deck, ar = 0.5 * (d.rib_width_top + d.rib_width_bottom) * d.rib_depth;
      ss.thickness = r9(d.slab_depth + ar / d.rib_spacing + d.deck_unit_weight / m.materials[cMat].unit_weight);
      ss.slab = clone(d);
    }
    m.shell_sections[nm] = ss;
  }
  m.grid = gridDict("G1", g.grid.xs, g.grid.ys);
  m.grid_systems = g.grid_systems ? g.grid_systems.map(gs => gridDict(gs.name, gs.xs, gs.ys, gs.origin)) : [m.grid];
  if (g.grid_systems) m.grid = m.grid_systems[0];
  m.stories = g.stories.map(s => ({ name: s.name, height: s.height, elevation: s.elevation, ...(s.tower ? { tower: s.tower } : {}) }));
  if (g.towers.length) m.towers = clone(g.towers);
  for (const mm of g.members) m.members.push({ uid: mm.uid, kind: mm.kind, section: mm.section, pi: mm.pi, pj: mm.pj, story: mm.story,
    releases: mm.releases, angle: mm.angle, rigid_i: 0, rigid_j: 0, rigid_factor: 1, foundation_ks: 0, foundation_width: 0, axial_limit: "both", ...(mm.tower ? { tower: mm.tower } : {}) });
  for (const s of g.shells) {
    const r = { uid: s.uid, kind: s.kind, behavior: s.behavior, section: s.section, corners: s.corners, mesh_size: s.mesh_size, story: s.story, openings: [],
      pier: s.pier || "", area_spring: null, wind_cp: null, ...(s.spandrel ? { spandrel: s.spandrel } : {}), ...(s.tower ? { tower: s.tower } : {}) };
    if (s.one_way) { r.distribution = "one_way"; r.one_way_dir = s.one_way; }
    m.shells.push(r);
  }
  if (g.spandrel_labels.length) m.spandrel_labels = g.spandrel_labels.slice();
  if (g.active_dof) m.active_dof = g.active_dof.slice();
  for (const s of g.supports) m.supports.push({ point: s, restraints: [1, 1, 1, 1, 1, 1] });
  // ---- loads
  const pat = (name, kind, sw = 0) => (m.patterns[name] = { name, kind, self_weight_factor: sw, member_udls: [], member_loads: [], nodal_loads: [], story_forces: [], area_loads: [] });
  pat("DEAD", "dead", 1);
  const sdl = pat("SDL", "superdead");
  const floors = g.shells.filter(s => s.kind === "slab");
  const beamLines = g.beamLines ? g.members.filter(mm => mm.kind === "beam") : [];
  const roofOf = mm => mm.story === g.stories[g.stories.length - 1].name;
  const hasLive = floors.some(s => !s.roof) || beamLines.some(b => !roofOf(b));
  const live = p.live && hasLive ? pat("LIVE", "live") : null;
  const lroof = p.roof_live ? pat("LROOF", "roof_live") : null;
  for (const s of floors) {
    if (p.sdl) sdl.area_loads.push({ region_uid: s.uid, q: p.sdl, direction: "gravity" });
    if (s.roof && lroof) lroof.area_loads.push({ region_uid: s.uid, q: p.roof_live, direction: "gravity" });
    else if (!s.roof && live) live.area_loads.push({ region_uid: s.uid, q: p.live, direction: "gravity" });
  }
  for (const b of beamLines) {
    const w = p.trib_width;
    if (p.sdl) sdl.member_udls.push({ member_uid: b.uid, w: r9(p.sdl * w) });
    if (roofOf(b) && lroof) lroof.member_udls.push({ member_uid: b.uid, w: r9(p.roof_live * w) });
    else if (!roofOf(b) && live) live.member_udls.push({ member_uid: b.uid, w: r9(p.live * w) });
  }
  for (const n of ["DEAD", "SDL", "LIVE", "LROOF"]) if (m.patterns[n]) m.cases[n] = { name: n, patterns: { [n]: 1 }, pdelta: false };
  m.mass_source = { DEAD: 1, SDL: 1 };
  if (live && p.live_mass_factor) m.mass_source.LIVE = p.live_mass_factor;
  m.num_modes = id === "frame_2d" ? Math.min(p.stories, 6) : id === "industrial_shed" ? 12 : Math.min(3 * m.stories.length, 12);
  // ---- story weights (non-column self weight + SDL + live mass; columns
  // are not lumped, like the backend story masses)
  const W = Object.fromEntries(m.stories.map(s => [s.name, 0]));
  for (const mm of m.members) {
    if (mm.kind === "column") continue;
    const s = m.sections[mm.section], mat = m.materials[s.material];
    W[mm.story] += s.A * dist(mm.pi, mm.pj) * mat.unit_weight;
  }
  for (const s of m.shells) {
    const ss = m.shell_sections[s.section], mat = m.materials[ss.material], a = area3(s.corners);
    W[s.story] += a * ss.thickness * mat.unit_weight;
    if (s.kind === "slab") W[s.story] += a * ((p.sdl || 0) + (floors.find(f => f.uid === s.uid).roof ? 0 : (live ? p.live * p.live_mass_factor : 0)));
  }
  for (const b of beamLines) W[b.story] += p.trib_width * ((p.sdl || 0) + (roofOf(b) ? 0 : (live ? p.live * p.live_mass_factor : 0))) * dist(b.pi, b.pj);
  const Wt = Object.values(W).reduce((a, b) => a + b, 0);
  const summary = { template: id, params: p, code: p.code, members: m.members.length, shells: m.shells.length, links: 0, stories: m.stories.length,
    piers: [...new Set(m.shells.map(s => s.pier).filter(Boolean))].sort(), spandrels: [...new Set(m.shells.map(s => s.spandrel).filter(Boolean))].sort(),
    towers: (m.towers || []).map(t => t.name), diaphragm: m.diaphragm === "none" ? "none" : "rigid", system: ent.label };
  // ---- isolators: K = 4 pi^2 M / T^2 shared equally
  if (g.links.length) {
    const k = r9(4 * Math.PI ** 2 * (Wt / G) / (p.isolator_period ** 2) / g.links.length);
    for (const lk of g.links) m.links.push({ uid: lk.uid, pi: lk.pi, pj: lk.pj, stiffness: [k, k, p.isolator_kv, 0, 0, 0], link_type: "elastic", params: {} });
    summary.links = m.links.length; summary.isolator_keff = k; summary.isolated_mass = Wt / G;
  }
  // ---- code loads
  const dirs = id === "frame_2d" ? ["X"] : ["X", "Y"];
  if (p.code !== "none") {
    const hn = Math.max(...m.stories.map(s => s.elevation));
    const Cs = p.code === "asce7" ? Math.min(p.SDS / (p.R / p.Ie), p.SD1 / (0.0488 * Math.pow(hn, 0.75) * p.R / p.Ie))
      : p.code === "ec8" ? 2.5 * p.ag / p.q : (p.Z / 2) * 2.5 * p.Ie / p.R;
    const V = Math.max(0.01, Cs) * Wt;
    const wh = m.stories.map(st => W[st.name] * st.elevation), sw = wh.reduce((a, b) => a + b, 0) || 1;
    for (const d of dirs) {
      const q = pat(`EQ${d}`, "quake");
      q.story_forces = m.stories.map((st, i) => ({ story: st.name, fx: d === "X" ? V * wh[i] / sw : 0, fy: d === "Y" ? V * wh[i] / sw : 0 }));
      m.cases[`EQ${d}`] = { name: `EQ${d}`, patterns: { [`EQ${d}`]: 1 }, pdelta: false };
    }
    const SDS = p.code === "asce7" ? p.SDS : p.code === "ec8" ? 2.5 * p.ag / p.q : 2.5 * p.Z / 2 * p.Ie / p.R;
    const SD1 = p.code === "asce7" ? p.SD1 : SDS * 0.4;
    const Ts = SD1 / SDS, T0 = 0.2 * Ts, pts = [[0, 0.4 * SDS], [T0, SDS], [Ts, SDS]];
    for (const T of [0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 10]) if (T > Ts) pts.push([T, SD1 / T]);
    m.spectrum_functions.RSF = { name: "RSF", points: pts.map(([a, b]) => [+a.toFixed(4), +b.toFixed(5)]), damping: 0.05 };
    const scale = p.code === "asce7" ? G * p.Ie / p.R : G;
    for (const d of dirs) m.rs_cases[`RS${d}`] = { name: `RS${d}`, direction: d, spectrum: [], num_modes: 0, combo_method: "CQC", damping: 0.05, scale, function: "RSF" };
    const D = { DEAD: 1, SDL: 1 }, f = (o, k) => Object.fromEntries(Object.entries(o).map(([n, v]) => [n, r9(v * k)]));
    const add = (n, c) => { m.combos[n] = { name: n, cases: c, combo_type: "add" }; };
    const L = live ? "LIVE" : null, Lr = lroof ? "LROOF" : null;
    if (p.code === "asce7") {
      add("1.4D", f(D, 1.4));
      if (L) add(`1.2D+1.6L${Lr ? "+0.5Lr" : ""}`, { ...f(D, 1.2), [L]: 1.6, ...(Lr ? { [Lr]: 0.5 } : {}) });
      else if (Lr) add("1.2D+0.5Lr", { ...f(D, 1.2), [Lr]: 0.5 });
      if (Lr) add(`1.2D+1.6Lr${L ? "+1.0L" : ""}`, { ...f(D, 1.2), [Lr]: 1.6, ...(L ? { [L]: 1 } : {}) });
      for (const d of dirs) for (const sg of [1, -1]) {
        const e = `EQ${d}`, s = sg > 0 ? "+" : "-";
        add(`1.2D${L ? "+1.0L" : ""}${s}1.0${e}`, { ...f(D, 1.2), ...(L ? { [L]: 1 } : {}), [e]: sg });
        add(`0.9D${s}1.0${e}`, { ...f(D, 0.9), [e]: sg });
      }
    } else {
      const pre = p.code === "ec8" ? "EN-" : "IS456-";
      add(`${pre}ULS01`, { ...f(D, p.code === "ec8" ? 1.35 : 1.5), ...(L ? { [L]: 1.5 } : {}) });
      let k = 1;
      for (const e of [...dirs.map(d => `EQ${d}`), ...dirs.map(d => `RS${d}`)]) for (const sg of [1, -1])
        add(`${pre}SEIS${String(k++).padStart(2, "0")}`, p.code === "ec8" ? { ...D, ...(L ? { [L]: 0.3 } : {}), [e]: sg } : { ...f(D, 1.2), ...(L ? { [L]: 1.2 } : {}), [e]: 1.2 * sg });
    }
  }
  Object.assign(summary, { patterns: Object.keys(m.patterns), cases: Object.keys(m.cases), rs_cases: Object.keys(m.rs_cases), combos: Object.keys(m.combos), mass_source: { ...m.mass_source } });
  return { model: m, summary };
}

export default { mockTplSysCatalogue, mockTplSysNormalize, mockTplSysBuild, mockTplSysHas, MT_SYSTEM_IDS };
