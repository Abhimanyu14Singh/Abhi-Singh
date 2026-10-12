/* SkyFrame — ?mock=1 support for the Wave C features (CONTRACT "Distributed
   frame moments, local 1 / 3 frame loads", "Frame pier labels", "Floor auto
   mesh options: no auto mesh and DB columns", "Per-object auto edge
   constraints", "Live-load reduction for beams", "Static seismic
   eccentricity overrides", "Diaphragm design forces (ASCE 7 12.10)").

   Client-side ports of the closed-form backend helpers (identical formulas):
     wcFpxCompute(model, pattern, o)      core/fpx.py compute (12.10.1 / 12.10.3)
     wcFpxComputeSettings(model, s)       core/fpx.py compute_settings
     wcBeamFactors(model, settings)       core/llr_beams.py element_factors
     wcEccTorques(model, pattern)         core/static_ecc.py story_torque_table
   plus
     wcMockAugment(model, results)        frame pier forces merged into results.piers
     wcMockValidate(payload)              POST /api/model checks of the new fields
     wcMdbExtend(T, H)                    DB-edit tables / columns (mirrors core/wavec_tables.py
                                          and core/frame_loads_ext.py)
   The model store stays SI. */

import { registerMockTable as wcRegisterMockTable } from "./mock_tables.js";

const G = 9.80665;
const isNum = v => typeof v === "number" && isFinite(v);
const clone = o => JSON.parse(JSON.stringify(o));

/* ================================================================ Fpx (ASCE 7 12.10) */
export const WC_FPX_METHODS = ["12.10.1", "12.10.3"];
function storyWeights(model, weights) {
  const out = {};
  if (weights && Object.keys(weights).length) { for (const s of model.stories || []) out[s.name] = +(weights[s.name] || 0); return out; }
  const sm = { ...(model.story_masses || {}), ...(model.explicit_story_masses || {}) };
  for (const s of model.stories || []) out[s.name] = (sm[s.name] || 0) * G;
  return out;
}
function patDir(pat, dir) {
  if (dir === "X" || dir === "Y") return dir;
  let sx = 0, sy = 0;
  for (const f of pat.story_forces || []) { sx += Math.abs(f.fx || 0); sy += Math.abs(f.fy || 0); }
  return sy > sx ? "Y" : "X";
}
export function wcFpxCompute(model, pattern, o = {}) {
  const pat = (model.patterns || {})[pattern];
  if (!pat) throw new Error(`Unknown load pattern '${pattern}'`);
  const SDS = +o.SDS, Ie = o.Ie == null ? 1 : +o.Ie, method = o.method || "12.10.1";
  if (!(SDS > 0)) throw new Error("Fpx: SDS must be a finite value > 0");
  if (!(Ie > 0)) throw new Error("Fpx: Ie must be a finite value > 0");
  const d = patDir(pat, o.direction);
  const w = storyWeights(model, o.weights);
  const F = {};
  for (const sf of pat.story_forces || []) F[sf.story] = (F[sf.story] || 0) + Math.abs(d === "X" ? (sf.fx || 0) : (sf.fy || 0));
  const st = [...(model.stories || [])].sort((a, b) => a.elevation - b.elevation);
  const hn = st.length ? Math.max(...st.map(s => s.elevation)) : 0;
  let alt = null;
  if (method === "12.10.3") {
    for (const k of ["Rs", "Cs", "Omega0", "zs", "SD1"]) if (!(+o[k] > 0)) throw new Error(`Fpx: ${k} must be a finite value > 0`);
    const N = st.length, zs = +o.zs;
    const g1 = 1 + zs / 2 * (1 - 1 / N), g2 = 0.9 * zs * (1 - 1 / N) ** 2;
    const cs2 = N === 1 ? 0 : Math.min((0.15 * N + 0.25) * Ie * SDS, Ie * SDS, Ie * o.SD1 / (0.03 * (N - 1)));
    const cp0 = 0.4 * SDS * Ie, cpi = Math.max(cp0, 0.9 * g1 * o.Omega0 * o.Cs);
    const cpn = Math.max(Math.sqrt((g1 * o.Omega0 * o.Cs) ** 2 + (g2 * cs2) ** 2), cpi);
    alt = { N, Gm1: g1, Gm2: g2, Cs2: cs2, Cp0: cp0, Cpi: cpi, Cpn: cpn, Rs: +o.Rs, Cs: +o.Cs, Omega0: +o.Omega0, zs, SD1: +o.SD1 };
  }
  const rows = st.map((s, k) => {
    const above = st.slice(k);
    const sF = above.reduce((a, t) => a + (F[t.name] || 0), 0);
    const sW = above.reduce((a, t) => a + (w[t.name] || 0), 0);
    const wp = +((o.wpx || {})[s.name] ?? w[s.name] ?? 0);
    const lo = 0.2 * SDS * Ie * wp, hi = 0.4 * SDS * Ie * wp;
    const row = { story: s.name, h: s.elevation, w: w[s.name] || 0, F: F[s.name] || 0, sum_F: sF, sum_w: sW, wpx: wp, Fpx_min: lo, Fpx_max: hi };
    if (!alt) {
      const eq = sW > 0 ? sF / sW * wp : 0;
      Object.assign(row, { Fpx_eq: eq, Fpx: Math.min(Math.max(eq, lo), hi), governs: eq >= lo && eq <= hi ? "12.10-1" : (eq < lo ? "min" : "max") });
    } else {
      const z = hn > 0 ? s.elevation / hn : 0;
      const cpx = z <= 0.8 ? alt.Cp0 + (alt.Cpi - alt.Cp0) * z / 0.8 : alt.Cpi + (alt.Cpn - alt.Cpi) * (z - 0.8) / 0.2;
      const eq = cpx / alt.Rs * wp;
      Object.assign(row, { Cpx: cpx, Fpx_eq: eq, Fpx: Math.max(eq, lo), Fpx_max: null, governs: eq >= lo ? "12.10-4" : "min" });
    }
    return row;
  }).reverse();
  const out = { pattern, direction: d, method, SDS, Ie, stories: rows };
  if (alt) out.alternative = alt;
  return out;
}
export function wcFpxPatterns(model) {
  return Object.entries(model.patterns || {}).filter(([n, p]) => ["quake", "seismic"].includes(p.kind) && (p.story_forces || []).length && !n.includes("_Fpx_")).map(([n]) => n);
}
export function wcFpxComputeSettings(model, s) {
  s = s || model.fpx_settings || {};
  if (!(+s.SDS > 0)) return [];
  const names = (s.patterns && s.patterns.length) ? s.patterns : wcFpxPatterns(model);
  return names.filter(n => (model.patterns || {})[n]).map(n => wcFpxCompute(model, n, { ...s }));
}
/** One story-force pattern per level (core/fpx.py generate_patterns). */
export function wcFpxGenerate(model, rep, { prefix = null, copyEcc = true } = {}) {
  const src = model.patterns[rep.pattern];
  const d = rep.direction;
  const tot = (src.story_forces || []).reduce((a, f) => a + (d === "X" ? (f.fx || 0) : (f.fy || 0)), 0);
  const sgn = tot < 0 ? -1 : 1;
  const base = prefix != null ? prefix : `${rep.pattern}_Fpx_`;
  const names = [];
  for (const r of rep.stories) {
    const name = `${base}${r.story}`;
    const f = sgn * r.Fpx;
    const p = { name, kind: "quake", member_udls: [], nodal_loads: [], story_forces: [{ story: r.story, fx: d === "X" ? f : 0, fy: d === "Y" ? f : 0 }],
      member_loads: [], area_loads: [], thermal_loads: [], accidental_torsion: copyEcc ? !!src.accidental_torsion : false,
      ecc: copyEcc && src.ecc != null ? src.ecc : 0.05, self_weight_factor: 0 };
    if (copyEcc && src.ecc_y != null) p.ecc_y = src.ecc_y;
    if (copyEcc && src.ecc_overrides) p.ecc_overrides = clone(src.ecc_overrides);
    model.patterns[name] = p;
    names.push(name);
  }
  return names;
}

/* ================================================================ LLR beams */
const BEAM_KLL = 2;
const llrR = (code, p, At, n) => {
  n = Math.max(1, n | 0);
  if (code === "asce7_16") { const k = (p.KLL ?? 4) * At; const R = k > 0 ? 0.25 + 4.57 / Math.sqrt(k) : 1; return Math.min(1, Math.max(n <= 1 ? 0.5 : 0.4, R)); }
  if (code === "nbcc2020") return (p.occupancy === "assembly") ? (At > 80 ? Math.min(1, 0.5 + Math.sqrt(20 / At)) : 1) : (At > 20 ? Math.min(1, 0.3 + Math.sqrt(9.8 / At)) : 1);
  if (code === "user") {
    const cv = p.curve || [[0, 1]], x = (p.basis || "area") === "area" ? At : n;
    if (x <= cv[0][0]) return cv[0][1];
    if (x >= cv[cv.length - 1][0]) return cv[cv.length - 1][1];
    for (let i = 0; i + 1 < cv.length; i++) if (cv[i][0] <= x && x <= cv[i + 1][0]) return cv[i][1] + (cv[i + 1][1] - cv[i][1]) * (x - cv[i][0]) / (cv[i + 1][0] - cv[i][0]);
  }
  return 1;
};
export function wcBeamFactor(code, params, beamParams, At) {
  if (code === "asce7_16") { const k = ((beamParams || {}).KLL ?? BEAM_KLL) * At; return Math.min(1, Math.max(0.5, k > 0 ? 0.25 + 4.57 / Math.sqrt(k) : 1)); }
  if (code === "ec1") {
    const psi0 = params.psi0 ?? 0.7, A0 = params.A0 ?? 10;
    let a = At > 0 ? Math.min(1, 5 / 7 * psi0 + A0 / At) : 1;
    if (["C", "D"].includes(params.category || "B")) a = Math.max(a, 0.6);
    return a;
  }
  if (code === "is875") return Math.max(0.75, 1 - 0.05 * Math.floor(At / 50 + 1e-12));
  return llrR(code, params, At, 1);
}
/** uid -> {At, L, w_left, w_right} (half-spacing rule; core/llr_beams.py). */
export function wcBeamAreas(model) {
  const COS5 = Math.cos(5 * Math.PI / 180), TOL = 1e-6;
  const isBeam = m => m.kind === "beam" && Math.abs(m.pj[2] - m.pi[2]) <= 1e-6 && Math.hypot(m.pj[0] - m.pi[0], m.pj[1] - m.pi[1]) > TOL;
  const plan = m => { const dx = m.pj[0] - m.pi[0], dy = m.pj[1] - m.pi[1], L = Math.hypot(dx, dy); return [m.pi[0], m.pi[1], dx / L, dy / L, L]; };
  const zk = z => Math.round(z * 1e6) / 1e6;
  const byZ = {}, pts = {};
  for (const m of model.members || []) {
    if (isBeam(m)) (byZ[zk(m.pi[2])] = byZ[zk(m.pi[2])] || []).push(m);
    for (const p of [m.pi, m.pj]) (pts[zk(p[2])] = pts[zk(p[2])] || []).push([p[0], p[1]]);
  }
  for (const r of model.shells || []) if (r.kind === "slab") for (const c of r.corners || []) (pts[zk(c[2])] = pts[zk(c[2])] || []).push([c[0], c[1]]);
  const out = {};
  for (const [z, lst] of Object.entries(byZ)) {
    const P = pts[z] || [];
    for (const b of lst) {
      const [x0, y0, ux, uy, L] = plan(b), nx = -uy, ny = ux;
      const best = { 1: null, "-1": null };
      for (const o of lst) {
        if (o === b) continue;
        const [ox, oy, oux, ouy, oL] = plan(o);
        if (Math.abs(ux * oux + uy * ouy) < COS5) continue;
        const ta = (ox - x0) * ux + (oy - y0) * uy, tb = ta + oL * (ux * oux + uy * ouy);
        if (Math.min(Math.max(ta, tb), L) - Math.max(Math.min(ta, tb), 0) <= TOL) continue;
        const mx = ox + 0.5 * oL * oux, my = oy + 0.5 * oL * ouy, d = (mx - x0) * nx + (my - y0) * ny;
        if (Math.abs(d) <= TOL) continue;
        const side = d > 0 ? 1 : -1;
        if (best[side] == null || Math.abs(d) < best[side]) best[side] = Math.abs(d);
      }
      const w = {};
      for (const side of [1, -1]) {
        if (best[side] != null) w[side] = best[side] / 2;
        else w[side] = Math.max(0, P.length ? Math.max(...P.map(([px, py]) => ((px - x0) * nx + (py - y0) * ny) * side)) : 0);
      }
      out[b.uid] = { At: L * (w[1] + w[-1]), L, w_left: w[1], w_right: w[-1] };
    }
  }
  return out;
}
export function wcBeamFactors(model, st) {
  st = st || {};
  const code = st.code || "asce7_16";
  const p = st.params || {};
  const areas = st.beam_areas || {};
  const out = {};
  for (const [uid, g] of Object.entries(wcBeamAreas(model))) {
    const At = areas[uid] != null ? +areas[uid] : g.At;
    out[uid] = { At, A1: At, n: 1, R: wcBeamFactor(code, p, st.beam_params || {}, At), L: g.L, w_left: g.w_left, w_right: g.w_right };
  }
  return out;
}

/* ================================================================ static ecc */
export function wcEccLookup(pat, story, dia) {
  const ov = pat.ecc_overrides || {};
  if (dia && (`${story}/${dia}` in ov)) return ov[`${story}/${dia}`];
  return story in ov ? ov[story] : null;
}
export function wcEccTorques(model, name) {
  const pat = (model.patterns || {})[name];
  if (!pat) throw new Error(`unknown pattern '${name}'`);
  const xs = [], ys = [];
  for (const m of model.members || []) for (const q of [m.pi, m.pj]) { xs.push(q[0]); ys.push(q[1]); }
  const lx = xs.length ? Math.max(...xs) - Math.min(...xs) : 0, ly = ys.length ? Math.max(...ys) - Math.min(...ys) : 0;
  const ecc = pat.ecc ?? 0.05, eccY = pat.ecc_y == null ? ecc : pat.ecc_y;
  const sg = e => e < 0 ? -1 : 1;
  return (pat.story_forces || []).map(sf => {
    const ov = wcEccLookup(pat, sf.story, null);
    let mx = 0, my = 0;
    if (pat.accidental_torsion) {
      if (ov == null) { mx = (sf.fx || 0) * ecc * ly; my = (sf.fy || 0) * eccY * lx; }
      else if (typeof ov === "object") { mx = (sf.fx || 0) * sg(ecc) * Math.abs(ov.length); my = (sf.fy || 0) * sg(eccY) * Math.abs(ov.length); }
      else { mx = (sf.fx || 0) * sg(ecc) * Math.abs(ov) * ly; my = (sf.fy || 0) * sg(eccY) * Math.abs(ov) * lx; }
    }
    return { story: sf.story, fx: sf.fx || 0, fy: sf.fy || 0, mz_x: mx, mz_y: my, override: ov };
  });
}

/* ================================================================ mock results: frame piers */
export function wcMockAugment(model, r) {
  const piered = (model.members || []).filter(m => m.pier && Math.abs(m.pj[2] - m.pi[2]) > 1e-9);
  if (!piered.length || !r) return r;
  const piers = r.piers = r.piers || {};
  const srcs = [...Object.entries(r.cases || {}), ...Object.entries(r.combos || {}).filter(([, b]) => !b.min)];
  const stories = model.stories || [];
  for (const [name, blk] of srcs) {
    const by = piers[name] = piers[name] || {};
    for (const m of piered) {
      const st = (blk.member_stations || {})[m.uid];
      if (!st || !st.x || !st.x.length) continue;
      const zLo = Math.min(m.pi[2], m.pj[2]);
      const s = stories.find(q => Math.abs(q.elevation - q.height - zLo) < 1e-6) || stories.find(q => q.name === m.story);
      if (!s) continue;
      const k = m.pi[2] <= m.pj[2] ? 0 : st.x.length - 1;
      const lab = by[m.pier] = by[m.pier] || {};
      const e = lab[s.name] = lab[s.name] || { P: 0, V: 0, M: 0 };
      e.P += -(st.N[k] || 0);
      e.V += Math.abs(st.V2[k] || 0);
      e.M += Math.abs(st.M3[k] || 0);
    }
    if (!Object.keys(by).length) delete piers[name];
  }
  if (!Object.keys(piers).length) delete r.piers;
  return r;
}

/* ================================================================ POST /api/model checks */
const MOMENT_DIRS = ["local_x", "local_y", "local_z", "local_1", "local_2", "local_3", "global_x", "global_y", "global_z"];
export function wcMockValidate(d) {
  if (!d || typeof d !== "object") return null;
  for (const m of d.members || []) {
    if (m.pier && Math.abs((m.pj || [0, 0, 0])[2] - (m.pi || [0, 0, 0])[2]) <= 1e-9)
      return `member ${m.uid}: pier labels apply to columns / inclined frames only (a horizontal beam is a spandrel)`;
  }
  for (const r of d.shells || []) {
    const ec = r.edge_constraint;
    if (ec != null && typeof ec !== "boolean") return `Shell ${r.uid}: edge_constraint must be true, false or null`;
    const fm = r.floor_mesh;
    if (fm && fm.mode === "none") {
      if (fm.max_size || r.mesh_divisions) return `Shell ${r.uid}: floor_mesh mode 'none' (no auto mesh) excludes mesh_divisions / max_size`;
      if ((r.corners || []).length === 4 && (r.openings || []).length) return `Shell ${r.uid}: floor_mesh mode 'none' (no auto mesh) needs a 4-corner slab without openings`;
    }
  }
  const names = new Set((d.stories || []).map(s => s.name));
  for (const [pn, p] of Object.entries(d.patterns || {})) {
    for (const l of p.member_loads || []) {
      if (l.moment_dist && !["uniform", "trapezoid"].includes(l.moment_dist)) return `Pattern ${pn}: moment_dist must be uniform | trapezoid`;
      if (l.moment_dist && l.kind !== "moment") return `Pattern ${pn}: moment_dist applies to kind 'moment' loads only`;
      if (l.moment_dist && !MOMENT_DIRS.includes(l.direction)) return `Pattern ${pn}: moment load direction must be one of ${MOMENT_DIRS.join(", ")}`;
      if (l.moment_dist && !(l.a >= 0 && l.a <= l.b && l.b <= 1)) return `Pattern ${pn}: distributed moment span must satisfy 0 <= a <= b <= 1`;
    }
    for (const [k, v] of Object.entries(p.ecc_overrides || {})) {
      const st = String(k).split("/")[0];
      if (!names.has(st)) return `Pattern ${pn}: ecc_overrides names unknown story (or story/diaphragm) '${k}'`;
      if (typeof v === "number" ? !(v >= 0 && v <= 0.5) : !(v && isNum(v.length) && v.length >= 0)) return `Pattern ${pn}: ecc_overrides['${k}'] must be a ratio in [0, 0.5] or {"length": e >= 0}`;
    }
  }
  const fs = d.fpx_settings;
  if (fs) {
    if (fs.SDS != null && !(fs.SDS > 0)) return "Fpx: SDS must be a finite value > 0";
    for (const p of fs.patterns || []) if (!(d.patterns || {})[p]) return `fpx_settings: unknown load pattern '${p}'`;
  }
  return null;
}

/* ================================================================ mock Show Tables */
const C = (key, label, quantity) => ({ key, label, quantity });
let wcTablesDone = false;
/** Register the mock Show Tables entry (called at init — not at import, the
    mock_tables ↔ mock_loadcodes ↔ mock_wavec imports form a cycle). */
export function wcRegisterMockTables() {
  if (wcTablesDone) return;
  wcTablesDone = true;
  wcRegisterMockTable("diaphragm_design_forces", "Diaphragm Design Forces (ASCE 7 12.10)", "Structure Output > Other Output Items",
  [C("pattern", "Load Pattern", "text"), C("story", "Story", "text"), C("direction", "Dir", "text"), C("method", "Method", "text"),
    C("h", "Elevation", "length"), C("w", "w_i", "force"), C("F", "F_i", "force"), C("sum_F", "Sum F", "force"), C("sum_w", "Sum w", "force"),
    C("wpx", "w_px", "force"), C("Fpx_eq", "Fpx (eq.)", "force"), C("Fpx_min", "Fpx min", "force"), C("Fpx_max", "Fpx max", "force"),
    C("Fpx", "Fpx", "force"), C("governs", "Governs", "text")],
  (model, results, filt, warnings) => {
    let reps = [];
    try { reps = wcFpxComputeSettings(model); } catch (e) { warnings && warnings.push(e.message); }
    if (!reps.length && warnings) warnings.push("Set the ASCE 7 12.10 inputs (SDS, Ie) in Define > Auto Lateral Loads > Diaphragm Design Forces (Fpx)");
    return reps.flatMap(r => r.stories.map(s => ({ pattern: r.pattern, direction: r.direction, method: r.method, story: s.story, h: s.h, w: s.w,
      F: s.F, sum_F: s.sum_F, sum_w: s.sum_w, wpx: s.wpx, Fpx_eq: s.Fpx_eq, Fpx_min: s.Fpx_min, Fpx_max: s.Fpx_max, Fpx: s.Fpx, governs: s.governs })));
  });
}

/* ================================================================ mock DB edit (core/wavec_tables.py + frame_loads_ext.py) */
const FM_MODES = ["", "default", "rectangular", "cookie_cut", "none"];
export function wcMdbExtend(T, H) {
  const { col, names, plist, G: GG, E, parseCell, RowErr } = H;
  const n = "number";
  const at = key => T.findIndex(t => t.key === key);
  const tbl = key => T[at(key)];
  /* shell objects: floor mesh + edge constraint */
  const sh = tbl("shell_objects");
  if (sh) {
    const fmGet = k => o => { const fm = o.floor_mesh; if (k === "mode") return fm ? (fm.mode || "default") : ""; if (k === "max_size") return fm ? (fm.max_size ?? null) : null; return fm ? fm[k] !== false : true; };
    const fmPut = k => (o, v) => {
      if (k === "mode") { if (v == null || v === "") delete o.floor_mesh; else o.floor_mesh = { ...(o.floor_mesh || {}), mode: v }; return; }
      if (!o.floor_mesh) return;
      o.floor_mesh = { ...o.floor_mesh, [k]: v };
    };
    sh.cols.splice(sh.cols.findIndex(c => c.key === "area"), 0,
      col("floor_mesh", "Floor Mesh", "enum", "none", { enum: FM_MODES, def: "", get: fmGet("mode"), put: fmPut("mode") }),
      col("floor_mesh_max", "Floor Max Size", n, "length", { optional: true, get: fmGet("max_size"), put: fmPut("max_size") }),
      col("mesh_at_beams", "Mesh at Beams", "bool", "none", { def: true, get: fmGet("at_beams"), put: fmPut("at_beams") }),
      col("mesh_at_walls", "Mesh at Walls", "bool", "none", { def: true, get: fmGet("at_walls"), put: fmPut("at_walls") }),
      col("mesh_at_grids", "Mesh at Grids", "bool", "none", { def: true, get: fmGet("at_grids"), put: fmPut("at_grids") }),
      col("edge_constraint", "Edge Constraint", "enum", "none", { enum: ["model", "yes", "no"], def: "model",
        get: o => o.edge_constraint == null ? "model" : (o.edge_constraint ? "yes" : "no"),
        put: (o, v) => { if (v == null || v === "" || v === "model") delete o.edge_constraint; else o.edge_constraint = v === "yes"; } }));
  }
  /* frame moment tables */
  const patCol = () => col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true });
  const frm = () => col("member_uid", "Frame", "ref", "none", { ref: "members", required: true });
  const dcol = () => col("direction", "Direction", "enum", "none", { enum: MOMENT_DIRS, def: "local_z" });
  const target = (l, o) => { const out = { ...o, kind: "moment" }; if (!out.moment_dist) delete out.moment_dist; if (!out.projected) delete out.projected; return ["member_loads", out]; };
  const iPoint = at("frame_point_loads");
  const extra = [
    plist("frame_moment_loads", "Frame Loads - Moment", [patCol(), frm(), dcol(), col("w", "M", n, "moment", { required: true }), col("a", "Rel. Dist.", n, "none", { def: 0.5 })],
      { lists: ["member_loads"], belongs: (l, e) => e.kind === "moment" && !e.moment_dist, target,
        newObj: () => ({ member_uid: "", kind: "moment", w: 0, w2: 0, a: 0.5, b: 1, direction: "local_z" }) }),
    plist("frame_distributed_moments", "Frame Loads - Distributed Moment", [patCol(), frm(),
      col("moment_dist", "Shape", "enum", "none", { enum: ["uniform", "trapezoid"], def: "uniform" }), dcol(),
      col("w", "m (start)", n, "line_moment", { required: true }), col("w2", "m (end)", n, "line_moment", { def: 0 }),
      col("a", "Rel. Dist. a", n, "none", { def: 0 }), col("b", "Rel. Dist. b", n, "none", { def: 1 })],
    { lists: ["member_loads"], belongs: (l, e) => e.kind === "moment" && !!e.moment_dist, target,
      newObj: () => ({ member_uid: "", kind: "moment", w: 0, w2: 0, a: 0, b: 1, direction: "local_z", moment_dist: "uniform" }) }),
  ];
  if (iPoint >= 0) T.splice(iPoint + 1, 0, ...extra); else T.push(...extra);
  /* pier label assignments (columns + walls) */
  const pierObjs = d => [...(d.members || []).filter(m => Math.abs((m.pj || [0, 0, 0])[2] - (m.pi || [0, 0, 0])[2]) > 1e-9).map(o => [`members:${o.uid}`, "Frame", o]),
    ...(d.shells || []).filter(s => s.kind === "wall").map(o => [`shells:${o.uid}`, "Shell", o])];
  T.push({ key: "pier_assignments", title: "Pier Label Assignments", group: GG.ASN || "Model Definition > Assignments", tkind: "wc", editable: true, canAdd: false, canDelete: false, keycol: null,
    cols: [col("object_type", "Object Type", "text", "none", { editable: false }), col("uid", "Unique Name", "text", "none", { editable: false }), col("pier", "Pier", "text", "none", { def: "" })],
    w8rows: (t, d) => pierObjs(d).map(([id, k, o]) => ({ _id: id, object_type: k, uid: o.uid, pier: o.pier || "" })),
    w8apply: (t, d, rows, errs) => {
      const objs = pierObjs(d), byId = Object.fromEntries(objs.map(([id, , o]) => [id, o])), n0 = errs.length;
      const byUid = Object.fromEntries(objs.map(([, k, o]) => [`${k}:${o.uid}`, o]));
      let changed = 0;
      rows.forEach((r, i) => {
        let o = byId[r._id] || byUid[`${r.object_type}:${r.uid}`] || (rows.length === objs.length ? objs[i][2] : null);
        const v = String(r.pier == null ? "" : r.pier).trim();
        if (!o) { if (v) errs.push(E(t.key, i, "uid", `unknown column / wall '${r.uid}'`)); return; }
        if (v && !/^[^\s,;][^,;]{0,63}$/.test(v)) { errs.push(E(t.key, i, "pier", `bad pier label '${v}'`)); return; }
        const isShell = (d.shells || []).includes(o);
        if ((o.pier || "") !== v) { if (v) o.pier = v; else if (isShell) o.pier = ""; else delete o.pier; changed++; }
      });
      return errs.length > n0 ? {} : { added: 0, modified: changed, deleted: 0 };
    } });
  /* load pattern eccentricity overrides */
  T.push({ key: "load_pattern_ecc_overrides", title: "Load Pattern Eccentricity Overrides", group: GG.LOAD || "Model Definition > Loads", tkind: "wc",
    editable: true, canAdd: true, canDelete: true, keycol: null,
    cols: [col("pattern", "Load Pattern", "enum", "none", { enum: names("patterns"), required: true }),
      col("story", "Story", "enum", "none", { enum: d => (d.stories || []).map(s => s.name), required: true }),
      col("diaphragm", "Diaphragm", "text", "none", { def: "" }),
      col("ratio", "Ecc. Ratio", n, "none", { optional: true }), col("length", "Ecc. Length", n, "length", { optional: true })],
    w8rows: (t, d) => Object.entries(d.patterns || {}).flatMap(([pn, p]) => Object.entries(p.ecc_overrides || {}).map(([k, v]) => {
      const [st, ...rest] = String(k).split("/");
      return { _id: [pn, k], pattern: pn, story: st, diaphragm: rest.join("/"), ratio: typeof v === "object" ? null : v, length: typeof v === "object" ? v.length : null };
    })),
    w8apply: (t, d, rows, errs) => {
      const n0 = errs.length, pats = d.patterns || {}, nw = {};
      rows.forEach((r, i) => {
        const vals = {};
        for (const c of t.cols) { try { vals[c.key] = parseCell(c, r[c.key] ?? c.def, d); } catch (e) { if (e instanceof RowErr) errs.push(E(t.key, i, c.key, `${c.label}: ${e.message}`)); else throw e; } }
        if (errs.length > n0) return;
        if (!pats[vals.pattern]) { errs.push(E(t.key, i, "pattern", `unknown load pattern '${vals.pattern}'`)); return; }
        const key = vals.diaphragm ? `${vals.story}/${String(vals.diaphragm).trim()}` : vals.story;
        if ((vals.ratio == null) === (vals.length == null)) { errs.push(E(t.key, i, "ratio", "give either a Ratio or a Length (exactly one)")); return; }
        const slot = nw[vals.pattern] = nw[vals.pattern] || {};
        if (key in slot) { errs.push(E(t.key, i, "story", `${vals.pattern}: override '${key}' is listed twice`)); return; }
        slot[key] = vals.length == null ? +vals.ratio : { length: +vals.length };
      });
      if (errs.length > n0) return {};
      let changed = 0;
      for (const [pn, p] of Object.entries(pats)) {
        const cur = p.ecc_overrides || {}, nv = nw[pn] || {};
        if (JSON.stringify(cur) !== JSON.stringify(nv)) { changed++; if (Object.keys(nv).length) p.ecc_overrides = nv; else delete p.ecc_overrides; }
      }
      return { rows: rows.length, changed };
    } });
  return T;
}
