/* SkyFrame Wave G — ?mock=1 stand-in for POST /api/results/steps
   (CONTRACT "Step and envelope results for graphics").

   Plausible step states built from the mock results: the lateral static
   case in the case direction is the response shape, scaled per step by the
   case's own history (TH roof displacement, pushover roof displacement,
   nonlinear static load factor, staged story count). Same JSON shape as the
   live endpoint; the envelope covers the recorded steps (stride reported). */

import { envelopeMaps as gmEnvMaps, stationsEnvelope as gmStEnv, ST_KEYS as GM_ST_KEYS } from "./g_core.js";

function pickSteps(n, frames) {
  if (n <= 0 || frames <= 0) return [];
  const nf = Math.min(frames, n), s = new Set();
  for (let k = 0; k < nf; k++) s.add(Math.max(1, Math.round(n * (k + 1) / nf)));
  return [...s].sort((a, b) => a - b);
}
const scaleMap = (m, f) => { const o = {}; for (const [t, v] of Object.entries(m || {})) o[t] = v.map(x => x * f); return o; };
const scaleSt = (m, f) => {
  const o = {};
  for (const [u, s] of Object.entries(m || {})) { o[u] = { x: s.x.slice() }; for (const k of GM_ST_KEYS) o[u][k] = (s[k] || s.x.map(() => 0)).map(x => x * f); }
  return o;
};
const addMaps = (a, b) => { const o = {}; for (const [t, v] of Object.entries(a || {})) { const w = (b || {})[t] || []; o[t] = v.map((x, i) => x + (w[i] || 0)); } return o; };
const addSt = (a, b) => {
  const o = {};
  for (const [u, s] of Object.entries(a || {})) { const w = (b || {})[u]; o[u] = { x: s.x.slice() }; for (const k of GM_ST_KEYS) o[u][k] = (s[k] || []).map((x, i) => x + ((w && w[k] && w[k][i]) || 0)); }
  return o;
};

/** Reference lateral case + its roof displacement in direction d (0 = X). */
function lateralRef(r, d) {
  let best = null, bv = 0;
  for (const [n, c] of Object.entries(r.cases || {})) {
    if (!c.node_disp) continue;
    let m = 0;
    for (const v of Object.values(c.node_disp)) m = Math.max(m, Math.abs(v[d] || 0));
    if (m > bv) { bv = m; best = { name: n, c, roof: m }; }
  }
  return best;
}
function signedRoof(c, d) {
  let m = 0;
  for (const v of Object.values(c.node_disp || {})) if (Math.abs(v[d] || 0) > Math.abs(m)) m = v[d] || 0;
  return m || 1;
}

export function gMockSteps(model, r, req) {
  const name = req.case, frames = Math.max(1, Math.min(400, +req.frames || 40));
  const out = (kind, n, steps, values, axisName, states, extra = {}) => {
    const p = { case: name, kind, n_steps: n, steps, axis: { name: axisName, values }, node_disp: {}, reactions: {}, frames, elapsed_s: 0.01, cached: false, ...extra };
    if (req.members !== false) p.member_stations = {};
    steps.forEach((s, i) => {
      const st = states[i];
      p.node_disp[String(s)] = st.node_disp; p.reactions[String(s)] = st.reactions || {};
      if (p.member_stations) p.member_stations[String(s)] = st.member_stations || {};
    });
    if (req.envelope) {
      const nd = gmEnvMaps(states.map(x => x.node_disp)), rc = gmEnvMaps(states.map(x => x.reactions || {}));
      const ms = gmStEnv(states.map(x => x.member_stations || {}));
      p.envelope = { max: { node_disp: nd.max, reactions: rc.max, member_stations: ms.max },
        min: { node_disp: nd.min, reactions: rc.min, member_stations: ms.min }, samples: steps.length, stride: Math.max(1, Math.ceil(n / steps.length)) };
    }
    return p;
  };
  const th = (model.th_cases || {})[name];
  if (th && r.th_cases && r.th_cases[name]) {
    const d = th.direction === "Y" ? 1 : 0;
    const ref = lateralRef(r, d);
    if (!ref) throw new Error(`no lateral reference case for ${name}`);
    const rec = r.th_cases[name];
    const top = (r.story_order || []).slice(-1)[0];
    const hist = ((d ? rec.story_uy : rec.story_ux) || {})[top] || [];
    const n = hist.length, steps = pickSteps(n, frames), f0 = signedRoof(ref.c, d);
    const states = steps.map(s => { const f = (hist[s - 1] || 0) / f0; return { node_disp: scaleMap(ref.c.node_disp, f), reactions: scaleMap(ref.c.reactions, f), member_stations: scaleSt(ref.c.member_stations, f) }; });
    return out("th", n, steps, steps.map(s => rec.t[s - 1] != null ? rec.t[s - 1] : s * 0.01), "t", states);
  }
  const po = (model.pushover_cases || {})[name];
  if (po && r.pushover && r.pushover[name]) {
    const d = po.direction === "Y" ? 1 : 0;
    const ref = lateralRef(r, d);
    if (!ref) throw new Error(`no lateral reference case for ${name}`);
    const rd = r.pushover[name].roof_disp || [];
    const n = rd.length, steps = pickSteps(n, frames), f0 = signedRoof(ref.c, d);
    const grav = (r.cases || {}).DEAD || null;
    const states = steps.map(s => {
      const f = (rd[s - 1] || 0) / f0;
      const nd = scaleMap(ref.c.node_disp, f), st = scaleSt(ref.c.member_stations, f), rc = scaleMap(ref.c.reactions, f);
      return grav ? { node_disp: addMaps(nd, grav.node_disp), member_stations: addSt(st, grav.member_stations), reactions: addMaps(rc, grav.reactions) } : { node_disp: nd, member_stations: st, reactions: rc };
    });
    return out("pushover", n, steps, steps.map(s => rd[s - 1]), "roof_disp", states, { base_shear: steps.map(s => (r.pushover[name].base_shear || [])[s - 1] || 0) });
  }
  const nl = (model.nonlinear_static_cases || {})[name];
  if (nl && r.nonlinear_static && r.nonlinear_static[name]) {
    const fin = r.nonlinear_static[name];
    const n = +nl.steps || 10, steps = [0, ...pickSteps(n, frames)];
    const states = steps.map(s => { const f = s / n; return { node_disp: scaleMap(fin.node_disp, f), reactions: scaleMap(fin.reactions, f), member_stations: scaleSt(fin.member_stations, f) }; });
    return out("nonlinear_static", n, steps, steps.map(s => s / n), "load_factor", states);
  }
  const sc = (model.staged_cases || {})[name];
  if (sc && r.staged && r.staged[name]) {
    const fin = r.staged[name];
    const labels = Array.isArray(sc.stages) ? sc.stages.map(s => s.name) : (model.stories || []).map(s => s.name);
    const n = Math.max(1, labels.length), steps = labels.map((_, i) => i + 1);
    const states = steps.map(s => { const f = s / n; return { node_disp: scaleMap(fin.node_disp, f), reactions: scaleMap(fin.reactions, f), member_stations: scaleSt(fin.member_stations, f) }; });
    return out("staged", n, steps, steps.map(Number), "stage", states, { labels });
  }
  throw new Error(`${JSON.stringify(name)} is not a time-history, pushover, nonlinear static or staged case of this model`);
}
