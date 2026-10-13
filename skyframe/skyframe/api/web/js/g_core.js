/* SkyFrame Wave G — results model core (pure JS, no DOM; unit-tested under
   node by tests/test_g_core_js.py).

   One VIEW-INDEPENDENT results model that every view (main 3D, View ›
   Windows panes, plan, elevation) draws from:

     case  → step / time (or envelope) → component

   * caseCatalog(results)      every displayable case: static / combo /
                               max-min combo / response spectrum (positive
                               envelope — never signed here) / staged /
                               nonlinear static / time history / pushover /
                               modal / buckling, with its step axis source;
   * stepAxis(...)             the steps of a multi-step case: recorded step
                               states (POST /api/results/steps), TH animation
                               frames (th_output.frames), the TH story
                               approximation (wc_video frame source), modes;
   * extractState(...)         the state the views draw: node_disp,
                               reactions, member_stations, shell_forces (+ the
                               *_min twins of a max/min pair);
   * envelopes, controlling stations, values at a station, projection of
     diagrams into a plan / elevation plane (in-plane components), contour
     colours, select-by-result filters and result probes.

   Units: SI throughout (the store is SI); callers convert with units.js. */

export const ST_KEYS = ["N", "V2", "V3", "T", "M2", "M3"];
export const COMP_LABEL = { N: "P", V2: "V2", V3: "V3", T: "T", M2: "M2", M3: "M3", VIN: "V (in-plane)", MIN: "M (in-plane)" };
export const COMP_KIND = { N: "force", V2: "force", V3: "force", T: "moment", M2: "moment", M3: "moment", VIN: "force", MIN: "moment" };
/** local axis the diagram of a component is drawn along (plotfn.js convention) */
export const COMP_DIR = { M3: "y", V2: "y", N: "y", T: "y", M2: "z", V3: "z" };
export const DISP_COMPS = ["UX", "UY", "UZ", "U", "UXY", "RX", "RY", "RZ"];
export const SHELL_FORCE_IDX = { F11: 0, F22: 1, F12: 2, M11: 3, M22: 4, M12: 5, V13: 6, V23: 7 };

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const mul = (a, k) => [a[0] * k, a[1] * k, a[2] * k];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = a => Math.hypot(a[0], a[1], a[2]);
const unit = a => { const l = len(a); return l > 1e-12 ? mul(a, 1 / l) : null; };
export const V3 = { sub, add, mul, dot, cross, len, unit };

/* ================================================================ catalogue */
const has = o => !!(o && typeof o === "object" && Object.keys(o).length);

/** Every displayable case: [{key, name, label, kind, multi, signed, minmax}].
    key prefixes: c: case · k: combo · rs: · st: staged · nl: · th: · po: ·
    modal · bk: buckling. */
export function caseCatalog(r) {
  const out = [];
  if (!r) return out;
  for (const n of Object.keys(r.cases || {})) out.push({ key: "c:" + n, name: n, label: n, kind: "static", multi: false, signed: true, minmax: !!r.cases[n].min });
  for (const [n, c] of Object.entries(r.combos || {})) out.push({ key: "k:" + n, name: n, label: n, kind: c && c.min ? "minmax" : "combo", multi: false, signed: true, minmax: !!(c && c.min) });
  for (const [n, c] of Object.entries(r.rs_cases || {})) out.push({ key: "rs:" + n, name: n, label: `RS: ${n}`, kind: "rs", multi: false, signed: false, minmax: !!(c && c.min) });
  for (const [n, c] of Object.entries(r.staged || {})) out.push({ key: "st:" + n, name: n, label: `Staged: ${n}`, kind: "staged", multi: true, signed: true, minmax: false, stages: (c && c.stages) || null });
  for (const [n, c] of Object.entries(r.nonlinear_static || {})) if (c && c.node_disp) out.push({ key: "nl:" + n, name: n, label: `NL: ${n}`, kind: "nl", multi: true, signed: true, minmax: false });
  for (const n of Object.keys(r.th_cases || {})) out.push({ key: "th:" + n, name: n, label: `TH: ${n}`, kind: "th", multi: true, signed: true, minmax: false });
  for (const n of Object.keys(r.pushover || {})) out.push({ key: "po:" + n, name: n, label: `Pushover: ${n}`, kind: "pushover", multi: true, signed: true, minmax: false });
  if (r.modal && r.modal.shapes && has(r.modal.shapes)) out.push({ key: "modal", name: "MODAL", label: "Modal (mode shapes)", kind: "modal", multi: true, signed: true, minmax: false });
  for (const [n, b] of Object.entries(r.buckling || {})) if (b && b.modes && has(b.modes)) out.push({ key: "bk:" + n, name: n, label: `Buckling: ${n}`, kind: "buckling", multi: true, signed: true, minmax: false });
  return out;
}
export const catalogEntry = (r, key) => caseCatalog(r).find(e => e.key === key) || null;

/** store.caseName (app.js keys: plain / "rs:" / "staged:" / "nl:") → catalogue key. */
export function keyFromStoreCase(r, name) {
  if (!r || !name) return null;
  if (name.startsWith("rs:")) return name;
  if (name.startsWith("staged:")) return "st:" + name.slice(7);
  if (name.startsWith("nl:")) return name;
  if (r.cases && r.cases[name]) return "c:" + name;
  if (r.combos && r.combos[name]) return "k:" + name;
  return null;
}

/** Steps that need the step endpoint (recorded states) for this entry. */
export const needsStepData = e => !!e && (e.kind === "th" || e.kind === "pushover" || e.kind === "nl" || e.kind === "staged");

/* ================================================================ steps */
/**
 * Step axis of a multi-step entry.
 * @returns {{n, ids:[], labels:[], values:[], axisName, source, exact}}
 *   source: "steps" (recorded states) | "frames" (TH animation frames) |
 *   "story" (TH story approximation) | "modes" | "stages" | "history" | "none"
 */
export function stepAxis(r, e, stepData, opts = {}) {
  const none = { n: 0, ids: [], labels: [], values: [], axisName: "", source: "none", exact: false };
  if (!r || !e || !e.multi) return none;
  const fmt = opts.fmt || ((k, v) => (Math.round(v * 1e4) / 1e4).toString());
  if (e.kind === "modal") {
    const T = (r.modal && r.modal.periods) || [];
    const n = Object.keys(r.modal.shapes || {}).length;
    const ids = Array.from({ length: n }, (_, i) => i + 1);
    return { n, ids, values: ids.map(i => T[i - 1]), labels: ids.map(i => `Mode ${i}${T[i - 1] != null ? ` — T = ${fmt("period", T[i - 1])}` : ""}`), axisName: "mode", source: "modes", exact: true };
  }
  if (e.kind === "buckling") {
    const b = r.buckling[e.name] || {};
    const ids = Object.keys(b.modes || {}).map(Number).sort((a, c) => a - c);
    const f = b.factors || [];
    return { n: ids.length, ids, values: ids.map(i => f[i - 1]), labels: ids.map(i => `Mode ${i}${f[i - 1] != null ? ` — λ = ${(+f[i - 1]).toFixed(3)}` : ""}`), axisName: "mode", source: "modes", exact: true };
  }
  const sd = stepData && stepData.case === e.name ? stepData : null;
  if (sd && Array.isArray(sd.steps) && sd.steps.length) {
    const vals = (sd.axis && sd.axis.values) || sd.steps.map(Number);
    const an = (sd.axis && sd.axis.name) || "step";
    const labels = sd.steps.map((s, i) => an === "t" ? `t = ${fmt("period", vals[i])}` :
      an === "roof_disp" ? `Step ${s} · ${fmt("disp", vals[i])}` :
        an === "load_factor" ? `Step ${s} · λ = ${(+vals[i]).toFixed(3)}` :
          an === "stage" ? ((sd.labels && sd.labels[i]) || `Stage ${s}`) : `Step ${s}`);
    return { n: sd.steps.length, ids: sd.steps.slice(), labels, values: vals.slice(), axisName: an, source: "steps", exact: true };
  }
  if (e.kind === "th") {
    const th = (r.th_cases || {})[e.name] || {};
    if (th.frames && th.frames.node_disp && (th.frames.steps || []).length) {
      const fr = th.frames;
      return { n: fr.steps.length, ids: fr.steps.slice(), values: fr.t.slice(), labels: fr.t.map(t => `t = ${fmt("period", t)}`), axisName: "t", source: "frames", exact: true };
    }
    if (opts.thFrames) {
      const tf = opts.thFrames(r, e.name);
      if (tf && tf.frames && tf.frames.length) {
        const ids = tf.frames.map((_, i) => i + 1);
        return { n: ids.length, ids, values: tf.frames.map(f => f.t), labels: tf.frames.map(f => `t = ${fmt("period", f.t)}`), axisName: "t", source: "story", exact: false, frames: tf.frames };
      }
    }
    return none;
  }
  if (e.kind === "staged") {
    const st = (r.staged || {})[e.name] || {};
    if (Array.isArray(st.stages) && st.stages.length) {
      const ids = st.stages.map((_, i) => i + 1);
      return { n: ids.length, ids, values: ids.slice(), labels: st.stages.map(s => s.name), axisName: "stage", source: "stages", exact: true };
    }
    return { n: 1, ids: [1], values: [1], labels: ["Final"], axisName: "stage", source: "final", exact: true };
  }
  if (e.kind === "nl") {
    const h = (((r.nonlinear_static || {})[e.name] || {}).nonlinear || {}).history || {};
    if (Array.isArray(h.step) && h.step.length) return { n: h.step.length, ids: h.step.slice(), values: (h.load_factor || []).slice(), labels: h.step.map((s, i) => `Step ${s} · λ = ${(+(h.load_factor || [])[i] || 0).toFixed(3)}`), axisName: "load_factor", source: "history", exact: false };
  }
  if (e.kind === "pushover") {
    const p = (r.pushover || {})[e.name] || {};
    const rd = p.roof_disp || [];
    if (rd.length) return { n: rd.length, ids: rd.map((_, i) => i + 1), values: rd.slice(), labels: rd.map((d, i) => `Step ${i + 1} · ${fmt("disp", d)}`), axisName: "roof_disp", source: "history", exact: false };
  }
  return none;
}

/* ================================================================ envelopes */
/** Signed per-entry max / min of a list of node maps ({tag: [6]}). */
export function envelopeMaps(maps) {
  const max = {}, min = {};
  for (const m of maps || []) for (const [t, v] of Object.entries(m || {})) {
    if (!max[t]) { max[t] = v.slice(); min[t] = v.slice(); continue; }
    const a = max[t], b = min[t];
    for (let i = 0; i < v.length; i++) { if (v[i] > a[i]) a[i] = v[i]; if (v[i] < b[i]) b[i] = v[i]; }
  }
  return { max, min };
}
/** Per-DOF value of the larger magnitude of (max, min), sign kept — the ETABS
    envelope deformed shape ("may not correspond to any particular state"). */
export function absMaxMap(maxMap, minMap) {
  const out = {};
  for (const [t, a] of Object.entries(maxMap || {})) {
    const b = (minMap || {})[t] || a;
    out[t] = a.map((v, i) => (Math.abs(b[i] || 0) > Math.abs(v) ? b[i] : v));
  }
  return out;
}
/** Signed max / min of a list of member-station blocks ({uid: {x, N, ...}}). */
export function stationsEnvelope(blocks) {
  const max = {}, min = {};
  for (const bl of blocks || []) for (const [uid, s] of Object.entries(bl || {})) {
    if (!max[uid]) {
      max[uid] = { x: s.x.slice() }; min[uid] = { x: s.x.slice() };
      for (const k of ST_KEYS) { max[uid][k] = (s[k] || []).slice(); min[uid][k] = (s[k] || []).slice(); }
      continue;
    }
    for (const k of ST_KEYS) {
      const a = max[uid][k], b = min[uid][k], v = s[k] || [];
      for (let i = 0; i < v.length; i++) { if (v[i] > a[i]) a[i] = v[i]; if (v[i] < b[i]) b[i] = v[i]; }
    }
  }
  return { max, min };
}
const scaleMap = (m, f) => { const o = {}; for (const [t, v] of Object.entries(m || {})) o[t] = v.map(x => x * f); return o; };

/* ================================================================ state */
function caseBlock(r, e) {
  if (!r || !e) return null;
  if (e.kind === "static") return (r.cases || {})[e.name] || null;
  if (e.kind === "combo" || e.kind === "minmax") return (r.combos || {})[e.name] || null;
  if (e.kind === "rs") return (r.rs_cases || {})[e.name] || null;
  if (e.kind === "staged") return (r.staged || {})[e.name] || null;
  if (e.kind === "nl") return (r.nonlinear_static || {})[e.name] || null;
  return null;
}
export { caseBlock as gCaseBlock };

/**
 * The state every view draws for a selection.
 * @param sel {key, step (index into the axis), env: "step"|"max"|"min"|"maxmin"}
 * @returns {key, kind, label, signed, env, envAvailable, node_disp, node_disp_min,
 *           reactions, reactions_min, member_stations, member_stations_min,
 *           shell_forces, shell_forces_min, member_forces, story, piers, spandrels,
 *           stepLabel, axis, exact, note} | null
 */
export function extractState(r, sel, stepData, opts = {}) {
  if (!r || !sel || !sel.key) return null;
  const e = catalogEntry(r, sel.key);
  if (!e) return null;
  const env = sel.env || "step";
  const base = { key: e.key, kind: e.kind, label: e.label, name: e.name, signed: e.signed, env: "step", envAvailable: false, note: "" };
  if (!e.multi) {
    const cd = caseBlock(r, e);
    if (!cd) return null;
    const pair = !!cd.min;
    const st = { ...base, envAvailable: pair, exact: true, stepLabel: "" };
    const useMin = pair && env === "min";
    const blk = useMin ? cd.min : cd;
    st.env = pair ? (env === "min" ? "min" : env === "maxmin" ? "maxmin" : "max") : "step";
    st.node_disp = blk.node_disp || null;
    st.reactions = blk.reactions || null;
    st.member_stations = blk.member_stations || null;
    st.member_forces = blk.member_forces || null;
    st.member_deflections = blk.member_deflections || null;
    st.shell_forces = blk.shell_forces || null;
    st.story = blk.story || null;
    st.base = blk.base || null;
    if (pair && st.env === "maxmin") {
      st.node_disp = absMaxMap(cd.node_disp, cd.min.node_disp);
      st.node_disp_max = cd.node_disp || null; st.node_disp_min = cd.min.node_disp || null;
      st.member_stations = cd.member_stations || null; st.member_stations_min = cd.min.member_stations || null;
      st.reactions = cd.reactions || null; st.reactions_min = cd.min.reactions || null;
      st.shell_forces = cd.shell_forces || null; st.shell_forces_min = cd.min.shell_forces || null;
    }
    if (e.kind === "rs") st.note = "Response-spectrum results are positive envelopes (signs are not known).";
    st.piers = ((r.piers || {})[e.name]) || null;
    st.spandrels = ((r.spandrels || {})[e.name]) || null;
    return st;
  }
  const ax = stepAxis(r, e, stepData, opts);
  const st = { ...base, axis: ax, exact: ax.exact, envAvailable: false };
  const n = ax.n;
  const idx = n ? Math.max(0, Math.min(n - 1, sel.step == null || sel.step < 0 ? n - 1 : sel.step)) : -1;
  st.stepIndex = idx;
  st.stepLabel = idx >= 0 ? ax.labels[idx] : "";
  if (e.kind === "modal") {
    st.node_disp = idx >= 0 ? r.modal.shapes[String(ax.ids[idx])] : null;
    st.animate = true;
    return st;
  }
  if (e.kind === "buckling") {
    st.node_disp = idx >= 0 ? (r.buckling[e.name].modes || {})[String(ax.ids[idx])] : null;
    st.animate = true;
    return st;
  }
  const sd = stepData && stepData.case === e.name ? stepData : null;
  const hasEnv = !!(sd && sd.envelope && sd.envelope.max);
  if (ax.source === "steps") {
    const id = String(ax.ids[idx]);
    st.envAvailable = hasEnv;
    if (hasEnv && env !== "step") {
      const E = sd.envelope;
      st.env = env;
      if (env === "max" || env === "min") {
        const b = E[env];
        st.node_disp = b.node_disp; st.reactions = b.reactions; st.member_stations = b.member_stations;
      } else {
        st.node_disp = absMaxMap(E.max.node_disp, E.min.node_disp);
        st.node_disp_max = E.max.node_disp; st.node_disp_min = E.min.node_disp;
        st.member_stations = E.max.member_stations; st.member_stations_min = E.min.member_stations;
        st.reactions = E.max.reactions; st.reactions_min = E.min.reactions;
      }
      st.stepLabel = env === "maxmin" ? "Envelope (max / min)" : env === "max" ? "Envelope max" : "Envelope min";
      if (E.stride > 1) st.note = `Envelope sampled every ${E.stride} steps.`;
    } else {
      st.node_disp = (sd.node_disp || {})[id] || null;
      st.reactions = (sd.reactions || {})[id] || null;
      st.member_stations = (sd.member_stations || {})[id] || null;
    }
    // staged: shells / piers of the final state only
    if (e.kind === "staged" && idx === n - 1) {
      const cd = caseBlock(r, e);
      if (cd) { st.member_forces = cd.member_forces || null; }
    }
    return st;
  }
  if (e.kind === "th") {
    if (ax.source === "frames") {
      const fr = r.th_cases[e.name].frames;
      st.node_disp = fr.node_disp[String(ax.ids[idx])] || null;
      st.note = "Joint displacements only (request member forces with the step data).";
    } else if (ax.source === "story") {
      st.node_disp = ax.frames[idx].disp;
      st.note = "Story-translation approximation of the TH response (no recorded frames).";
    }
    return st;
  }
  if (e.kind === "staged") {
    const cd = caseBlock(r, e);
    if (ax.source === "stages") {
      const sg = cd.stages[idx];
      st.node_disp = sg.node_disp || null; st.reactions = sg.reactions || null; st.member_forces = sg.member_forces || null;
      if (idx === n - 1) st.member_stations = cd.member_stations || null;
      else st.note = "Stage end forces only (load the step data for the station diagrams).";
    } else {
      st.node_disp = cd.node_disp; st.reactions = cd.reactions; st.member_stations = cd.member_stations; st.member_forces = cd.member_forces;
    }
    return st;
  }
  if (e.kind === "nl") {
    const cd = caseBlock(r, e);
    if (idx === n - 1 || ax.source !== "history") {
      st.node_disp = cd.node_disp; st.reactions = cd.reactions; st.member_stations = cd.member_stations;
      st.shell_forces = cd.shell_forces || null; st.member_forces = cd.member_forces;
    } else {
      // load-factor scaled final state: exact for an elastic case, a preview otherwise
      const lam = ax.values[idx] || 0, lamN = ax.values[n - 1] || 1;
      const f = lamN ? lam / lamN : 0;
      st.node_disp = scaleMap(cd.node_disp, f);
      st.note = "Preview: final state scaled by the load factor (load the step data for the recorded states).";
    }
    return st;
  }
  if (e.kind === "pushover") {
    st.note = "Load the step data (results toolbar) for the pushover states.";
    return st;
  }
  return st;
}

/* ================================================================ stations */
/** Value at x along a station block: exact at stations, linear between (ETABS). */
export function valueAt(xs, vals, x) {
  if (!xs || !xs.length || !vals) return null;
  if (x <= xs[0]) return vals[0];
  const n = xs.length;
  if (x >= xs[n - 1]) return vals[n - 1];
  for (let i = 0; i < n - 1; i++) {
    const a = xs[i], b = xs[i + 1];
    if (Math.abs(x - a) <= 1e-12) return vals[i];
    if (x < b) {
      const t = b - a > 1e-15 ? (x - a) / (b - a) : 0;
      return vals[i] + (vals[i + 1] - vals[i]) * t;
    }
  }
  return vals[n - 1];
}
/** Nearest station index to x. */
export function nearestStation(xs, x) {
  let bi = 0, bd = Infinity;
  (xs || []).forEach((v, i) => { const d = Math.abs(v - x); if (d < bd) { bd = d; bi = i; } });
  return bi;
}
/** ETABS "Values at Controlling Stations": the two ends plus the stations of
    the maximum and minimum (interior extremes only when they differ from the
    ends by more than tol × the largest magnitude). Sorted station indices. */
export function controllingStations(xs, vals, tol = 1e-6) {
  const n = (vals || []).length;
  if (!n) return [];
  if (n === 1) return [0];
  let imax = 0, imin = 0, vmax = 0;
  for (let i = 0; i < n; i++) {
    if (vals[i] > vals[imax]) imax = i;
    if (vals[i] < vals[imin]) imin = i;
    vmax = Math.max(vmax, Math.abs(vals[i]));
  }
  const out = new Set([0, n - 1]);
  const eps = tol * (vmax || 1);
  for (const i of [imax, imin]) {
    if (i === 0 || i === n - 1) continue;
    if (Math.abs(vals[i] - vals[0]) > eps && Math.abs(vals[i] - vals[n - 1]) > eps) out.add(i);
  }
  return [...out].sort((a, b) => a - b);
}
/** Location + value of the largest |value| (Show Max). */
export function maxOf(xs, vals) {
  let im = -1, vm = 0;
  (vals || []).forEach((v, i) => { if (im < 0 || Math.abs(v) > Math.abs(vm)) { im = i; vm = v; } });
  return im < 0 ? null : { i: im, x: xs[im], v: vm };
}
/** max |value| / max / min of a station component (optionally over a min twin). */
export function memberQuantity(st, comp, mode = "absmax", stMin = null) {
  const v = [...((st && st[comp]) || []), ...((stMin && stMin[comp]) || [])];
  if (!v.length) return null;
  if (mode === "max") return Math.max(...v);
  if (mode === "min") return Math.min(...v);
  return v.reduce((a, x) => Math.max(a, Math.abs(x)), 0);
}

/* ================================================================ geometry */
/** Default frame local axes (plotfn / frameaxes convention fallback): 1 along
    the member, 2 = vertical for horizontal members / global X for columns,
    3 = 1 × 2. Callers pass the app's own axes when available. */
export function defaultAxes(pi, pj) {
  const x = unit(sub(pj, pi)) || [1, 0, 0];
  let y;
  if (Math.abs(x[2]) > 0.999) y = [x[2] > 0 ? -1 : 1, 0, 0];
  else y = unit(cross(cross(x, [0, 0, 1]), x)) || [0, 0, 1];
  if (y[2] < 0 && Math.abs(x[2]) <= 0.999) y = mul(y, -1);
  const z = unit(cross(x, y)) || [0, 1, 0];
  return { x, y, z };
}

/** View plane basis: {kind, n (normal), u, v, coord?, z?}. 3D → null. */
export function viewBasis(view) {
  if (!view || view.kind === "3d") return null;
  if (view.kind === "plan") return { kind: "plan", n: [0, 0, 1], u: [1, 0, 0], v: [0, 1, 0], z: view.z };
  if (view.dir) { const d = view.dir; return { kind: "elev", n: [-d[1], d[0], 0], u: [d[0], d[1], 0], v: [0, 0, 1], origin: view.origin }; }
  if (view.axis === "x") return { kind: "elev", n: [1, 0, 0], u: [0, 1, 0], v: [0, 0, 1], coord: view.coord };
  return { kind: "elev", n: [0, 1, 0], u: [1, 0, 0], v: [0, 0, 1], coord: view.coord };
}
/** Is a world point on the view plane (plan: at the story elevation)? */
export function onViewPlane(b, p, tol = 1e-4) {
  if (!b) return true;
  if (b.kind === "plan") return Math.abs(p[2] - b.z) <= tol;
  if (b.origin) return Math.abs(dot(sub(p, [b.origin[0], b.origin[1], 0]), b.n)) <= tol;
  return Math.abs(dot(p, b.n) - b.coord) <= tol;
}
/** Member visible in a 2D view: both ends on the plane (plan: beams / slabs at
    the story level; elevation: frames in the grid plane). */
export function memberInView(b, pi, pj, tol = 1e-4) {
  return onViewPlane(b, pi, tol) && onViewPlane(b, pj, tol);
}
/** In-plane offset direction for a diagram drawn along local axis `a` of a
    member with axis e1, in a view with normal n: the projection of `a` onto
    the plane, or — when `a` is (nearly) normal to the plane — the diagram is
    rotated into the plane (perpendicular to the member, n × e1). */
export function inPlaneDir(a, e1, n) {
  const p = sub(a, mul(n, dot(a, n)));
  const lp = len(p);
  if (lp >= 0.3) return mul(p, 1 / lp);
  const q = unit(cross(n, e1));
  if (!q) return unit(p) || [0, 0, 1];
  return dot(q, a) < 0 ? mul(q, -1) : q;
}
/** In-plane component (ETABS 2D views): the bending whose moment vector is
    normal to the view plane. kind "M" → "M3" | "M2"; "V" → "V2" | "V3". */
export function inPlaneComponent(axes, n, kind) {
  const bz = Math.abs(dot(axes.z, n)), by = Math.abs(dot(axes.y, n));
  if (kind === "M") return bz >= by ? "M3" : "M2";
  return bz >= by ? "V2" : "V3";
}
/** Resolve a component key (incl. in-plane VIN / MIN) for one member and view. */
export function resolveComp(comp, axes, b) {
  if (comp === "MIN" || comp === "VIN") {
    const n = b ? b.n : [0, 0, 1];
    return inPlaneComponent(axes, n, comp === "MIN" ? "M" : "V");
  }
  return comp;
}
/**
 * Diagram polygon of one member: base points along the member and tip points
 * offset by value × k along the component's local axis — projected into the
 * view plane for plan / elevation (in-plane offset), or in 3D (b = null).
 * Moments are drawn on the tension side (ETABS; sign −1).
 * @returns {base:[[x,y,z]], tip:[[x,y,z]], dir:[x,y,z], comp}
 */
export function projectDiagram(m, axes, xs, vals, comp, k, b) {
  const c = resolveComp(comp, axes, b);
  const dkey = COMP_DIR[c] || "y";
  let d = axes[dkey];
  if (b) d = inPlaneDir(d, axes.x, b.n);
  const sgn = (c === "M3" || c === "M2") ? -1 : 1;
  const base = xs.map(x => add(m.pi, mul(axes.x, x)));
  const tip = base.map((p, i) => add(p, mul(d, sgn * (vals[i] || 0) * k)));
  return { base, tip, dir: d, comp: c };
}
/** Plane coordinates (u, v) of a world point. */
export function toPlane(b, p) {
  if (!b) return [p[0], p[1]];
  if (b.kind === "plan") return [p[0], p[1]];
  if (b.origin) return [dot(sub(p, [b.origin[0], b.origin[1], 0]), b.u), p[2]];
  return [dot(p, b.u), p[2]];
}

/** Cubic-Hermite (or straight-chord) deformed member polyline — the
    Viewer3D._deformedPolyline rule, pure. */
export function deformedPolyline(Pi, Pj, di, dj, f, cubic = true, N = 8) {
  const L = len(sub(Pj, Pi)) || 1;
  const e1 = unit(sub(Pj, Pi)) || [1, 0, 0];
  const ref = Math.abs(e1[2]) > 0.9 ? [1, 0, 0] : [0, 0, 1];
  const e2 = unit(cross(ref, e1)) || [0, 1, 0];
  const e3 = cross(e1, e2);
  const ti = [di[3] || 0, di[4] || 0, di[5] || 0], tj = [dj[3] || 0, dj[4] || 0, dj[5] || 0];
  const ui = dot(di, e1), uj = dot(dj, e1), vi = dot(di, e2), vj = dot(dj, e2), wi = dot(di, e3), wj = dot(dj, e3);
  const vpi = dot(ti, e3), vpj = dot(tj, e3), wpi = -dot(ti, e2), wpj = -dot(tj, e2);
  const out = [];
  for (let k = 0; k <= N; k++) {
    const t = k / N, t2 = t * t, t3 = t2 * t;
    let v, w;
    if (cubic) {
      const h1 = 1 - 3 * t2 + 2 * t3, h2 = t - 2 * t2 + t3, h3 = 3 * t2 - 2 * t3, h4 = -t2 + t3;
      v = h1 * vi + h2 * L * vpi + h3 * vj + h4 * L * vpj;
      w = h1 * wi + h2 * L * wpi + h3 * wj + h4 * L * wpj;
    } else { v = vi + (vj - vi) * t; w = wi + (wj - wi) * t; }
    const u = ui * (1 - t) + uj * t;
    const bp = add(Pi, mul(sub(Pj, Pi), t));
    out.push([bp[0] + f * (u * e1[0] + v * e2[0] + w * e3[0]), bp[1] + f * (u * e1[1] + v * e2[1] + w * e3[1]), bp[2] + f * (u * e1[2] + v * e2[2] + w * e3[2])]);
  }
  return out;
}
/** Peak translation of a displacement map (auto scale basis). */
export function peakDisp(map) {
  let m = 0;
  for (const d of Object.values(map || {})) m = Math.max(m, Math.abs(d[0] || 0), Math.abs(d[1] || 0), Math.abs(d[2] || 0));
  return m;
}

/* ================================================================ components */
export function dispValue(d, comp) {
  if (!d) return null;
  switch (comp) {
    case "UX": return d[0] || 0; case "UY": return d[1] || 0; case "UZ": return d[2] || 0;
    case "RX": return d[3] || 0; case "RY": return d[4] || 0; case "RZ": return d[5] || 0;
    case "UXY": return Math.hypot(d[0] || 0, d[1] || 0);
    case "U": return Math.hypot(d[0] || 0, d[1] || 0, d[2] || 0);
    default: return null;
  }
}
export const dispKind = comp => (/^R/.test(comp) ? "rotation" : "disp");

/* ================================================================ colour */
const RAMP = [[44, 127, 214], [242, 245, 248], [224, 82, 82]];
const RAINBOW = [[40, 60, 200], [30, 170, 230], [40, 190, 90], [235, 215, 40], [235, 120, 30], [215, 40, 40]];
export function rampRGB(t, palette = "diverging") {
  const stops = palette === "rainbow" ? RAINBOW : RAMP;
  t = Math.max(0, Math.min(1, isFinite(t) ? t : 0.5));
  const x = t * (stops.length - 1), i = Math.min(stops.length - 2, Math.floor(x)), f = x - i;
  return stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * f));
}
/** Auto range: symmetric about 0 for signed data, else [min, max]; a user
    range (lo < hi) wins (values outside clamp to the end colours). */
export function rangeOf(vals, user = null, symmetric = true) {
  if (user && isFinite(user[0]) && isFinite(user[1]) && user[1] > user[0]) return [user[0], user[1]];
  let lo = Infinity, hi = -Infinity;
  for (const v of vals) if (v != null && isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (!isFinite(lo)) return [-1, 1];
  if (symmetric && lo < 0 && hi > 0) { const a = Math.max(-lo, hi); return [-a, a]; }
  if (hi - lo < 1e-15) return [lo - 1, hi + 1];
  return [lo, hi];
}
/** Normalised position t ∈ [0, 1] (clamped) and the colour of a value. */
export const tOf = (v, r) => Math.max(0, Math.min(1, (v - r[0]) / ((r[1] - r[0]) || 1)));
export function valueColor(v, r, palette = "diverging") {
  if (v == null || !isFinite(v)) return "rgba(128,128,128,0.35)";
  const c = rampRGB(tOf(v, r), palette);
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

/* ================================================================ nodes / drift */
export function nodeIndex(r) {
  const byKey = new Map();
  for (const [t, p] of Object.entries((r && r.nodes) || {})) byKey.set(p.map(v => (+v).toFixed(4)).join(","), t);
  return byKey;
}
/** Node tag at a point (1e-4 m) or null. */
export function tagAt(r, p, idx = null) {
  const m = idx || nodeIndex(r);
  return m.get(p.map(v => (+v).toFixed(4)).join(",")) || null;
}
/** Joint drift ratio along dir (0 = X, 1 = Y): relative displacement to the
    nearest joint directly BELOW at the same plan point, over the height. */
export function jointDrift(r, nodeDisp, tag, dir) {
  const p = (r.nodes || {})[tag];
  if (!p || !nodeDisp || !nodeDisp[tag]) return null;
  let best = null, bz = -Infinity;
  for (const [t, q] of Object.entries(r.nodes)) {
    if (Math.abs(q[0] - p[0]) > 1e-4 || Math.abs(q[1] - p[1]) > 1e-4) continue;
    if (q[2] < p[2] - 1e-4 && q[2] > bz) { bz = q[2]; best = t; }
  }
  if (best == null) return null;
  const h = p[2] - bz;
  const ua = nodeDisp[tag][dir] || 0, ub = (nodeDisp[best] || [])[dir] || 0;
  return h > 1e-9 ? (ua - ub) / h : null;
}
/** Story drift ratio (max |joint drift| over the story's joints) per story. */
export function storyDrifts(r, nodeDisp) {
  const out = {};
  if (!r || !nodeDisp) return out;
  const elev = r.story_elev || {};
  for (const [s, z] of Object.entries(elev)) {
    let dx = 0, dy = 0;
    for (const [t, p] of Object.entries(r.nodes || {})) {
      if (Math.abs(p[2] - z) > 1e-4) continue;
      const ax = jointDrift(r, nodeDisp, t, 0), ay = jointDrift(r, nodeDisp, t, 1);
      if (ax != null) dx = Math.max(dx, Math.abs(ax));
      if (ay != null) dy = Math.max(dy, Math.abs(ay));
    }
    out[s] = { drift_x: dx, drift_y: dy };
  }
  return out;
}

/* ================================================================ shells */
/** Shell thickness of a model region (m) or null. */
export function shellThickness(model, regionUid) {
  const sh = ((model && model.shells) || []).find(s => s.uid === regionUid);
  const sec = sh && ((model.shell_sections || {})[sh.section]);
  return sec && sec.thickness > 0 ? +sec.thickness : null;
}
/** Shell force vector [F11 F22 F12 M11 M22 M12 V13 V23] of a quad. */
export function quadForces(sf, qi) { const a = sf ? (sf[qi] !== undefined ? sf[qi] : sf[String(qi)]) : null; return Array.isArray(a) ? a : null; }
/** Shell stresses at a face (top = +3 face: S = F/t + 6 M/t², bottom −):
    the SkyFrame convention of core/shell_results.py. */
export function shellStresses(f, t, face = "top") {
  if (!f || !(t > 0)) return null;
  const c = face === "top" ? 1 : face === "bot" ? -1 : 0;
  const s11 = f[0] / t + c * 6 * f[3] / (t * t), s22 = f[1] / t + c * 6 * f[4] / (t * t), s12 = f[2] / t + c * 6 * f[5] / (t * t);
  const a = (s11 + s22) / 2, rr = Math.hypot((s11 - s22) / 2, s12);
  return { S11: s11, S22: s22, S12: s12, SMax: a + rr, SMin: a - rr,
    SVM: Math.sqrt(Math.max(0, s11 * s11 - s11 * s22 + s22 * s22 + 3 * s12 * s12)), S13: (f[6] || 0) / t, S23: (f[7] || 0) / t };
}
export function shellComponent(f, t, comp, face = "top") {
  if (!f) return null;
  if (comp in SHELL_FORCE_IDX) return f[SHELL_FORCE_IDX[comp]];
  const s = shellStresses(f, t, face);
  return s ? s[comp] : null;
}

/* ================================================================ filters */
/** Tower / group object filter: {towers: "all" | [names], group: "" | name}. */
export function objectFilter(model, f) {
  const towers = f && Array.isArray(f.towers) && f.towers.length ? new Set(f.towers) : null;
  const grp = f && f.group && model && model.groups && model.groups[f.group] ? model.groups[f.group] : null;
  const tw = (model && Array.isArray(model.towers) && model.towers.length) ? model.towers : null;
  const deflt = tw ? tw[0].name : "";
  const storyTower = {};
  for (const s of (model && model.stories) || []) storyTower[s.name] = s.tower || deflt;
  const towerOf = o => (o.tower || storyTower[o.story] || deflt);
  const set = k => new Set(grp ? (grp[k] || []).map(String) : []);
  const gm = set("members"), gs = set("shells"), gl = set("links");
  const towerOk = o => !towers || !tw || towers.has(towerOf(o));
  return {
    active: !!(towers && tw) || !!grp,
    member: mm => !!mm && towerOk(mm) && (!grp || gm.has(String(mm.uid))),
    shell: sh => !!sh && towerOk(sh) && (!grp || gs.has(String(sh.uid))),
    link: lk => !!lk && towerOk(lk) && (!grp || gl.has(String(lk.uid))),
    towerOf,
  };
}

/* ================================================================ select by result */
const OPS = {
  ">": (a, b) => a > b, ">=": (a, b) => a >= b, "<": (a, b) => a < b, "<=": (a, b) => a <= b,
  "|>|": (a, b) => Math.abs(a) > b, "|>=|": (a, b) => Math.abs(a) >= b,
};
export const SBR_OPS = Object.keys(OPS);
export const SBR_QUANTITIES = {
  frame: ["N", "V2", "V3", "T", "M2", "M3"],
  joint: ["UX", "UY", "UZ", "U", "RX", "RY", "RZ", "DRIFT_X", "DRIFT_Y"],
  shell: ["F11", "F22", "F12", "M11", "M22", "M12", "V13", "V23", "S11", "S22", "S12", "SVM", "SMax", "SMin"],
  link: ["U1", "U2", "U3", "R1", "R2", "R3"],
  story: ["DRIFT_X", "DRIFT_Y"],
};
/**
 * Select objects by a result quantity.
 * ctx {model, results, state, linkForces?: {uid: [6]}, shellValues?: {quadIdx: v}}
 * spec {objType, quantity, mode: "absmax"|"max"|"min", op, threshold (SI), face?, filter?}
 * @returns {refs: [{type, uid}], rows: [{type, uid, value}]}
 */
export function selectByResult(ctx, spec) {
  const { model, results: r, state } = ctx;
  const cmp = OPS[spec.op] || OPS[">="];
  const thr = +spec.threshold;
  const rows = [];
  const flt = spec.filter ? objectFilter(model, spec.filter) : null;
  const pass = v => v != null && isFinite(v) && cmp(v, thr);
  if (!state) return { refs: [], rows };
  if (spec.objType === "frame") {
    const st = state.member_stations || {}, stMin = state.member_stations_min || null;
    for (const mm of (model && model.members) || []) {
      if (flt && !flt.member(mm)) continue;
      const q = memberQuantity(st[mm.uid], spec.quantity, spec.mode || "absmax", stMin ? stMin[mm.uid] : null);
      if (pass(q)) rows.push({ type: "member", uid: mm.uid, value: q });
    }
  } else if (spec.objType === "joint") {
    const nd = state.node_disp || {};
    for (const [t, p] of Object.entries((r && r.nodes) || {})) {
      if (!nd[t]) continue;
      let q;
      if (spec.quantity === "DRIFT_X" || spec.quantity === "DRIFT_Y") q = jointDrift(r, nd, t, spec.quantity === "DRIFT_X" ? 0 : 1);
      else q = dispValue(nd[t], spec.quantity);
      if (spec.mode !== "max" && spec.mode !== "min" && q != null) q = Math.abs(q);
      if (pass(q)) rows.push({ type: "joint", uid: p.map(v => +(+v).toFixed(6)).join(","), value: q, point: p.slice(), tag: t });
    }
  } else if (spec.objType === "link") {
    const lf = ctx.linkForces || {};
    const i = SBR_QUANTITIES.link.indexOf(spec.quantity);
    for (const lk of (model && model.links) || []) {
      if (flt && !flt.link(lk)) continue;
      const f = lf[lk.uid];
      if (!f) continue;
      let q = f[i];
      if (spec.mode !== "max" && spec.mode !== "min") q = Math.abs(q);
      if (pass(q)) rows.push({ type: "link", uid: lk.uid, value: q });
    }
  } else if (spec.objType === "shell") {
    const vals = ctx.shellValues || {};
    const best = {};
    ((r && r.shell_quads) || []).forEach((q, i) => {
      let v = vals[i] !== undefined ? vals[i] : vals[String(i)];
      if (v == null) return;
      if (spec.mode !== "max" && spec.mode !== "min") v = Math.abs(v);
      const reg = q.region;
      if (best[reg] == null || (spec.mode === "min" ? v < best[reg] : v > best[reg])) best[reg] = v;
    });
    for (const sh of (model && model.shells) || []) {
      if (flt && !flt.shell(sh)) continue;
      if (pass(best[sh.uid])) rows.push({ type: "shell", uid: sh.uid, value: best[sh.uid] });
    }
  } else if (spec.objType === "story") {
    const sd = (state.story && Object.keys(state.story).length) ? state.story : storyDrifts(r, state.node_disp);
    const key = spec.quantity === "DRIFT_Y" ? "drift_y" : "drift_x";
    const hit = new Set(Object.entries(sd).filter(([, v]) => pass(Math.abs(+v[key] || 0))).map(([s]) => s));
    for (const mm of (model && model.members) || []) if (hit.has(mm.story) && (!flt || flt.member(mm))) rows.push({ type: "member", uid: mm.uid, value: Math.abs(+sd[mm.story][key] || 0), story: mm.story });
    for (const sh of (model && model.shells) || []) if (hit.has(sh.story) && (!flt || flt.shell(sh))) rows.push({ type: "shell", uid: sh.uid, value: Math.abs(+sd[sh.story][key] || 0), story: sh.story });
  }
  return { refs: rows.map(x => ({ type: x.type, uid: x.uid })), rows };
}

/* ================================================================ probes */
export const PROBE_TYPES = ["joint_disp", "joint_drift", "member_end", "member_max", "member_at", "reaction", "pier", "shell", "story_drift", "link"];
/**
 * Current value of a pinned result probe.
 * ctx {model, results, state, shellData?, linkForces?}
 * probe {type, point?, uid?, end?, comp, label?, story?, quad?, face?, dir?}
 * @returns {value, kind} (value null when unavailable for this state)
 */
export function probeValue(ctx, p) {
  const { results: r, state, model } = ctx;
  const none = { value: null, kind: "" };
  if (!r || !state || !p) return none;
  if (p.type === "joint_disp" || p.type === "reaction" || p.type === "joint_drift") {
    const t = p.point ? tagAt(r, p.point) : p.tag;
    if (t == null) return none;
    if (p.type === "joint_drift") return { value: jointDrift(r, state.node_disp, t, p.dir === "Y" ? 1 : 0), kind: "ratio" };
    if (p.type === "reaction") {
      const v = (state.reactions || {})[t];
      const i = ["FX", "FY", "FZ", "MX", "MY", "MZ"].indexOf(p.comp);
      return { value: v && i >= 0 ? v[i] : null, kind: i >= 3 ? "moment" : "force" };
    }
    const d = (state.node_disp || {})[t];
    return { value: dispValue(d, p.comp), kind: dispKind(p.comp) };
  }
  if (p.type === "member_at") {
    const st = (state.member_stations || {})[p.uid];
    return { value: st && st[p.comp] ? valueAt(st.x, st[p.comp], +p.x || 0) : null, kind: COMP_KIND[p.comp] };
  }
  if (p.type === "member_end" || p.type === "member_max") {
    const st = (state.member_stations || {})[p.uid];
    if (!st || !st[p.comp]) return { value: null, kind: COMP_KIND[p.comp] };
    const v = st[p.comp];
    if (p.type === "member_max") return { value: memberQuantity(st, p.comp, "absmax", (state.member_stations_min || {})[p.uid] || null), kind: COMP_KIND[p.comp] };
    return { value: p.end === "j" ? v[v.length - 1] : v[0], kind: COMP_KIND[p.comp] };
  }
  if (p.type === "pier") {
    const e = ((state.piers || {})[p.label] || {})[p.story];
    return { value: e && e[p.comp] != null ? +e[p.comp] : null, kind: p.comp === "M" ? "moment" : "force" };
  }
  if (p.type === "story_drift") {
    const sd = (state.story && state.story[p.story]) ? state.story : storyDrifts(r, state.node_disp);
    const e = sd[p.story];
    return { value: e ? (p.dir === "Y" ? e.drift_y : e.drift_x) : null, kind: "ratio" };
  }
  if (p.type === "shell") {
    const f = quadForces(state.shell_forces, p.quad);
    const t = shellThickness(model, p.region);
    const v = shellComponent(f, t, p.comp, p.face || "top");
    const kind = /^S/.test(p.comp) ? "stress" : /^M/.test(p.comp) ? "line_moment" : "line_force";
    return { value: v, kind };
  }
  if (p.type === "link") {
    const f = (ctx.linkForces || {})[p.uid];
    const i = ["U1", "U2", "U3", "R1", "R2", "R3"].indexOf(p.comp);
    return { value: f && i >= 0 ? f[i] : null, kind: i >= 3 ? "moment" : "force" };
  }
  return none;
}

/* ================================================================ CSV */
const csvCell = v => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
export const toCsv = rows => rows.map(r => r.map(csvCell).join(",")).join("\n") + "\n";

/* ================================================================ member detail */
/** ETABS "equivalent loads" of a member from its station shears: a uniform
    load −ΔV/Δx on every station interval and a point load −ΔV at a repeated
    station (shear jump). Local-axis load per unit length (kN/m) / kN. */
export function equivalentLoads(xs, V) {
  const out = [];
  if (!xs || !V) return out;
  for (let i = 0; i < xs.length - 1; i++) {
    const dx = xs[i + 1] - xs[i], dv = (V[i + 1] || 0) - (V[i] || 0);
    if (Math.abs(dx) <= 1e-9) { if (Math.abs(dv) > 1e-9) out.push({ kind: "point", x: xs[i], P: -dv }); continue; }
    out.push({ kind: "dist", x0: xs[i], x1: xs[i + 1], w: -dv / dx });
  }
  return out;
}
/** Local deflected shape of a member (cubic Hermite from the end joint
    displacements and rotations): {x, d1 (axial), d2 (local 2), d3 (local 3)}. */
export function memberDeflection(Pi, Pj, di, dj, axes, n = 21) {
  const L = len(sub(Pj, Pi)) || 1;
  const t3 = d => [d[3] || 0, d[4] || 0, d[5] || 0];
  const ui = dot(di, axes.x), uj = dot(dj, axes.x);
  const vi = dot(di, axes.y), vj = dot(dj, axes.y), wi = dot(di, axes.z), wj = dot(dj, axes.z);
  const vpi = dot(t3(di), axes.z), vpj = dot(t3(dj), axes.z), wpi = -dot(t3(di), axes.y), wpj = -dot(t3(dj), axes.y);
  const x = [], d1 = [], d2 = [], d3 = [];
  for (let k = 0; k < n; k++) {
    const t = k / (n - 1), t2 = t * t, tt = t2 * t;
    const h1 = 1 - 3 * t2 + 2 * tt, h2 = t - 2 * t2 + tt, h3 = 3 * t2 - 2 * tt, h4 = -t2 + tt;
    x.push(t * L);
    d1.push(ui + (uj - ui) * t);
    d2.push(h1 * vi + h2 * L * vpi + h3 * vj + h4 * L * vpj);
    d3.push(h1 * wi + h2 * L * wpi + h3 * wj + h4 * L * wpj);
  }
  return { x, d1, d2, d3, L };
}
/** Relative deflection: minus the chord between the two end values. */
export function relativeDeflection(xs, vals) {
  if (!xs || !xs.length) return [];
  const L = xs[xs.length - 1] - xs[0] || 1, a = vals[0], b = vals[vals.length - 1];
  return vals.map((v, i) => v - (a + (b - a) * (xs[i] - xs[0]) / L));
}
