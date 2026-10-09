/* SkyFrame mock (?mock=1) for Nonlinear Static cases + named diaphragms /
   additional mass (CONTRACT "Nonlinear static load cases and case chaining",
   "Multiple diaphragms per story and additional mass").

     mockNlsValidate(model)            mirrors the backend ValueErrors (POST /api/model)
     mockNlsExtendResults(model, r)    results.nonlinear_static + results.diaphragms,
                                       case_status for chained dependencies

   The final state of a nonlinear static case is a plausible linear
   superposition of the mock static case dicts of its patterns (start_from
   chain included) with a bilinear-softening step history; per-diaphragm
   results come from the mock story results split by diaphragm mass. */

import { validateNls as mnValidateNls, chainOf as mnChainOf } from "./nlsdlg.js";
import { validateDiaphragms as mnValidateDiaphragms } from "./diaphdlg.js";

const isObj = v => v && typeof v === "object" && !Array.isArray(v);

export function mockNlsValidate(model) {
  if (!model) return "";
  return mnValidateNls(model) || mnValidateDiaphragms(model) || "";
}

/** Σ f_i · dict_i over numbers / numeric arrays / nested objects. */
function lin(parts) {
  const out = {};
  const addInto = (dst, src, f) => {
    for (const [k, v] of Object.entries(src)) {
      if (typeof v === "number") dst[k] = (dst[k] || 0) + f * v;
      else if (Array.isArray(v)) {
        if (k === "x") { dst[k] = v.slice(); continue; }          // station abscissae
        if (!Array.isArray(dst[k])) dst[k] = new Array(v.length).fill(0);
        v.forEach((x, i) => { if (typeof x === "number") dst[k][i] += f * x; });
      } else if (isObj(v)) { dst[k] = dst[k] || {}; addInto(dst[k], v, f); }
    }
  };
  for (const [d, f] of parts) if (d) addInto(out, d, f);
  return out;
}

/** Static mock case dict representing one pattern (case named like the
    pattern, else any case whose only pattern is it). */
function patternCase(model, r, pat) {
  if (r.cases && r.cases[pat]) {
    const c = (model.cases || {})[pat];
    if (!c || (c.patterns && c.patterns[pat] === 1 && Object.keys(c.patterns).length === 1)) return r.cases[pat];
  }
  for (const [n, c] of Object.entries(model.cases || {}))
    if (r.cases && r.cases[n] && Object.keys(c.patterns || {}).length === 1 && c.patterns[pat] != null)
      return [r.cases[n], 1 / c.patterns[pat]];
  return r.cases && r.cases[pat] ? r.cases[pat] : null;
}
function loadParts(model, r, c, lam) {
  const parts = [];
  for (const l of c.loads || []) {
    const pc = patternCase(model, r, l.pattern);
    if (!pc) continue;
    if (Array.isArray(pc)) parts.push([pc[0], lam * l.scale * pc[1]]);
    else parts.push([pc, lam * l.scale]);
  }
  return parts;
}
const BASE = { UX: "FX", UY: "FY", UZ: "FZ", RX: "MX", RY: "MY", RZ: "MZ" };
const DOF_I = { UX: 0, UY: 1, UZ: 2, RX: 3, RY: 4, RZ: 5 };

function monitored(model, r, c, ref) {
  const stories = model.stories || [];
  let point = c.control_point;
  if (!point) {
    const zs = c.control_story ? (stories.find(s => s.name === c.control_story) || {}).elevation
      : Math.max(0, ...stories.map(s => s.elevation || 0));
    // the node closest to the plan centre at that level
    let best = null, bd = Infinity;
    const xs = [], ys = [];
    for (const p of Object.values(r.nodes || {})) if (Math.abs(p[2] - zs) < 1e-6) { xs.push(p[0]); ys.push(p[1]); }
    const cx = xs.length ? (Math.min(...xs) + Math.max(...xs)) / 2 : 0, cy = ys.length ? (Math.min(...ys) + Math.max(...ys)) / 2 : 0;
    for (const [t, p] of Object.entries(r.nodes || {})) {
      if (Math.abs(p[2] - zs) > 1e-6) continue;
      const d = Math.hypot(p[0] - cx, p[1] - cy);
      if (d < bd) { bd = d; best = t; }
    }
    point = best ? r.nodes[best] : [0, 0, zs || 0];
  }
  let tag = null;
  for (const [t, p] of Object.entries(r.nodes || {}))
    if (Math.abs(p[0] - point[0]) < 1e-6 && Math.abs(p[1] - point[1]) < 1e-6 && Math.abs(p[2] - point[2]) < 1e-6) { tag = t; break; }
  const i = DOF_I[c.control_dof] ?? 0;
  const u = tag && ref && ref.node_disp && ref.node_disp[tag] ? ref.node_disp[tag][i] || 0 : 0;
  return { node: tag != null ? +tag : null, dof: c.control_dof, point: point.map(Number), unit: u };
}

function solveCase(model, r, name, memo) {
  if (memo[name]) return memo[name];
  const cases = model.nonlinear_static_cases;
  const c = cases[name];
  const prev = c.start_from ? solveCase(model, r, c.start_from, memo) : null;
  const unit = lin(loadParts(model, r, c, 1));
  const mon = monitored(model, r, c, unit);
  const i = DOF_I[c.control_dof] ?? 0;
  const startDisp = prev && mon.node != null && prev.state.node_disp && prev.state.node_disp[mon.node]
    ? prev.state.node_disp[mon.node][i] || 0 : 0;
  const steps = c.steps || 10;
  const geoAmp = c.geometric === "none" ? 1 : 1.06;
  let lamEnd = 1, converged = true;
  const warnings = [];
  const hinged = c.hinges === "asce41" || c.default_My != null || Object.keys(c.My || {}).length > 0;
  if (c.load_application === "displacement_control") {
    const k = mon.unit * geoAmp;
    if (Math.abs(k) < 1e-12) {
      converged = false; lamEnd = 0;
      warnings.push(`monitored ${c.control_dof} does not respond to the case loads; displacement control stopped at step 0`);
    } else lamEnd = (c.target_disp - startDisp) / k;
  }
  // bilinear softening (yield at 55 % of the final displacement when hinged)
  const hist = { step: [], load_factor: [], disp: [], base: { FX: [], FY: [], FZ: [], MX: [], MY: [], MZ: [] }, monitored: { node: mon.node, dof: mon.dof, point: mon.point } };
  const baseRef = unit.base || {};
  const prevBase = prev ? prev.state.base || {} : {};
  const dEnd = startDisp + lamEnd * mon.unit * geoAmp;
  for (let s = 0; s <= steps; s++) {
    const t = s / steps;
    const lamLin = lamEnd * t;
    const soft = hinged && t > 0.55 ? 0.55 + (t - 0.55) * 0.18 : t;
    const lam = c.load_application === "displacement_control" ? lamEnd * (hinged ? soft : t) : lamLin;
    hist.step.push(s);
    hist.load_factor.push(+lam.toPrecision(10));
    hist.disp.push(c.load_application === "displacement_control" ? startDisp + (dEnd - startDisp) * t : startDisp + lam * mon.unit * geoAmp);
    for (const k of Object.keys(hist.base)) hist.base[k].push((prevBase[k] || 0) + lam * (baseRef[k] || 0));
  }
  const lamFinal = hist.load_factor[hist.load_factor.length - 1];
  const own = lin(loadParts(model, r, c, lamFinal * geoAmp));
  const state = prev ? lin([[prev.state, 1], [own, 1]]) : own;
  const hinge_rotations = {}, yielded = [];
  if (hinged && c.hinges !== "asce41") {
    const cols = (model.members || []).filter(mm => mm.kind === "column" && (c.hinges === "all_ends" || Math.min(mm.pi[2], mm.pj[2]) < 1e-6));
    for (const mm of cols) {
      if (!(c.My && c.My[mm.uid]) && c.default_My == null) continue;
      const rot = Math.abs(dEnd) / Math.max(1, Math.max(...(model.stories || [{ elevation: 3 }]).map(s => s.elevation || 0))) * 0.6;
      hinge_rotations[mm.uid] = +rot.toPrecision(6);
      if (rot > 0.002) yielded.push(mm.uid);
    }
  }
  const hinges = [];
  if (c.hinges === "asce41") {
    for (const mm of (model.members || []).filter(x => x.hinges === "auto_m3" || x.hinges === "fiber_pmm")) {
      for (const end of ["i", "j"]) {
        const rot = hist.step.map(s => +(Math.abs(dEnd) * 0.004 * s / steps).toPrecision(6));
        const state = rot.map(v => v < 0.0015 ? "elastic" : v < 0.004 ? "IO" : v < 0.008 ? "LS" : "CP");
        hinges.push({ uid: mm.uid, end, kind: mm.hinges, My: 250, thy: 0.0015, rot, moment: rot.map(v => Math.min(250, v / 0.0015 * 250)), state });
        if (state[state.length - 1] !== "elastic") yielded.push(mm.uid);
      }
    }
  }
  const res = {
    ...state,
    nonlinear: {
      chain: mnChainOf(model.nonlinear_static_cases, name), load_application: c.load_application, geometric: c.geometric,
      converged, final_load_factor: lamFinal, history: hist, hinge_rotations, yielded: [...new Set(yielded)], hinges, warnings,
    },
  };
  for (const k of ["node_disp", "reactions", "base", "member_forces", "member_stations", "member_deflections", "story"])
    if (!res[k]) res[k] = {};
  memo[name] = { state, res };
  return memo[name];
}

function mockDiaphragms(model, r) {
  const dias = model.diaphragms || {};
  const named = (model.joint_diaphragms || []).length || (model.shells || []).some(s => s.diaphragm);
  if (!named) return null;
  const stories = model.stories || [];
  const byStory = {};
  const addPt = (st, n, p, w) => {
    byStory[st] = byStory[st] || {};
    const b = byStory[st][n] = byStory[st][n] || { xs: [], ys: [], w: 0 };
    b.xs.push(p[0]); b.ys.push(p[1]); b.w += w;
  };
  for (const s of model.shells || []) {
    if (!s.diaphragm) continue;
    const z = s.corners[0][2];
    const st = stories.find(x => Math.abs(x.elevation - z) < 1e-6);
    if (!st) continue;
    let A = 0;
    const cs = s.corners;
    for (let i = 0; i < cs.length; i++) { const a = cs[i], b = cs[(i + 1) % cs.length]; A += a[0] * b[1] - b[0] * a[1]; }
    A = Math.abs(A) / 2;
    const q = (s.additional_mass || 0) + 0.6;                 // ≈ slab self mass t/m² + additional
    for (const c of cs) addPt(st.name, s.diaphragm, c, q * A / cs.length);
  }
  for (const e of model.joint_diaphragms || []) {
    const st = stories.find(x => Math.abs(x.elevation - e.point[2]) < 1e-6);
    if (st) addPt(st.name, e.diaphragm, e.point, 1.0);
  }
  const out = { definitions: {}, stories: {}, cases: {}, combos: {}, rs_cases: {} };
  for (const [n, v] of Object.entries(dias)) out.definitions[n] = { type: v.type };
  let tag = 9000;
  for (const [st, dd] of Object.entries(byStory)) {
    out.stories[st] = {};
    for (const [n, b] of Object.entries(dd)) {
      const x0 = Math.min(...b.xs), x1 = Math.max(...b.xs), y0 = Math.min(...b.ys), y1 = Math.max(...b.ys);
      const cmx = b.xs.reduce((a, v) => a + v, 0) / b.xs.length, cmy = b.ys.reduce((a, v) => a + v, 0) / b.ys.length;
      const rigid = (dias[n] || {}).type !== "semi_rigid";
      out.stories[st][n] = {
        type: rigid ? "rigid" : "semi_rigid", master: rigid ? tag++ : null, n_nodes: b.xs.length,
        mass: +b.w.toFixed(4), mass_rz: +(b.w * ((x1 - x0) ** 2 + (y1 - y0) ** 2) / 12).toFixed(4),
        cm_x: +cmx.toFixed(4), cm_y: +cmy.toFixed(4), extent_x: x1 - x0, extent_y: y1 - y0, x: cmx, y: cmy,
        cr_x: +(cmx + 0.04 * (x1 - x0)).toFixed(4), cr_y: +(cmy - 0.03 * (y1 - y0)).toFixed(4), k_theta: 1.5e6 * (1 + (x1 - x0) / 10),
      };
    }
  }
  const per = (group, src) => {
    for (const [cn, cd] of Object.entries(src || {})) {
      if (!cd || !cd.story || cd.min) continue;
      const o = {};
      for (const [st, dd] of Object.entries(out.stories)) {
        const sr = cd.story[st] || {};
        const tot = Object.values(dd).reduce((a, p) => a + p.mass, 0) || 1;
        o[st] = {};
        let k = 0;
        for (const [n, p] of Object.entries(dd)) {
          const sh = 0.85 + 0.3 * (p.mass / tot) + 0.05 * k++;
          o[st][n] = {
            ux: (sr.ux || 0) * sh, uy: (sr.uy || 0) * sh, rz: ((sr.ux || 0) + (sr.uy || 0)) * 1e-3 * (k % 2 ? 1 : -1),
            drift_x: (sr.drift_x || 0) * sh, drift_y: (sr.drift_y || 0) * sh,
            tors_ratio_x: +(1.02 + 0.06 * k).toFixed(4), tors_ratio_y: +(1.01 + 0.05 * k).toFixed(4),
          };
        }
      }
      out[group][cn] = o;
    }
  };
  per("cases", r.cases); per("combos", r.combos); per("rs_cases", r.rs_cases);
  return out;
}

/** Hook (mock.js mockResults, after case_status): add the blocks. */
export function mockNlsExtendResults(model, r) {
  if (!model || !r) return r;
  const cases = model.nonlinear_static_cases || {};
  const names = Object.keys(cases);
  if (names.length && !mnValidateNls(model)) {
    const notRun = new Set(Array.isArray(model.cases_not_run) ? model.cases_not_run : []);
    const need = new Set();
    const addChain = n => { try { for (const k of mnChainOf(cases, n)) need.add(k); } catch { /* cycle */ } };
    for (const n of names) if (!notRun.has(n)) addChain(n);
    for (const [n, p] of Object.entries(model.pushover_cases || {})) if (!notRun.has(n) && p.start_from && cases[p.start_from]) addChain(p.start_from);
    if (model.modal_from_case && cases[model.modal_from_case]) addChain(model.modal_from_case);
    const memo = {};
    const out = {};
    r.case_status = r.case_status || {};
    for (const n of names) {
      if (!need.has(n)) { r.case_status[n] = "not_run"; continue; }
      out[n] = solveCase(model, r, n, memo).res;
      r.case_status[n] = notRun.has(n) ? "run_as_dependency" : "finished";
    }
    if (Object.keys(out).length) r.nonlinear_static = out;
    if (model.modal_from_case && r.modal && Array.isArray(r.modal.periods)) {
      const g = cases[model.modal_from_case].geometric;
      const f = g === "none" ? 1.0 : 1.08;                       // stressed (P-Delta) state softens the modes
      r.modal.periods = r.modal.periods.map(T => T * f);
      if (Array.isArray(r.modal.frequencies)) r.modal.frequencies = r.modal.frequencies.map(x => x / f);
    }
  }
  const dia = mockDiaphragms(model, r);
  if (dia) r.diaphragms = dia;
  return r;
}
