/* SkyFrame mock — load-case parity features (CONTRACT: "Frequency-domain
   analysis (steady-state, PSD)", "Direct-integration options + energy",
   "Pushover load distribution and control").

   mockFrequencyResults(model, r) → {steady_state?, psd?}  — modal
     superposition on the mock modal basis (r.modal periods / shapes /
     participation): accel loads use q_i = -Γ_i H_i(f) a(f); pattern loads
     scale the pattern's mock static response by D(f) = Σ w_i ω_i² H_i(f)
     (exact static response at f = 0). Same JSON shapes as /api/analyze.
   augmentMockResults(model, r) — adds the pushover capacity_curve /
     distribution blocks and the TH direct_integration / energy blocks.
   mockSteadyState / mockPsd(model, name) — POST /api/analyze/steady_state|psd. */

const G = 9.80665;
const DOF = 6;
const BASE_KEYS = ["FX", "FY", "FZ", "MX", "MY", "MZ"];
const STORY_KEYS = ["ux", "uy", "drift_x", "drift_y"];
const r6 = v => +(+v).toPrecision(6);

/* ---------------- complex helpers ([re, im]) */
const cmul = (a, b) => [a[0] * b[0] - a[1] * b[1], a[0] * b[1] + a[1] * b[0]];
const cdiv1 = (b) => { const d = b[0] * b[0] + b[1] * b[1]; return [b[0] / d, -b[1] / d]; };   // 1/b

function fnEval(fn, f) {
  if (!fn) return 1;
  const p = fn.points || [];
  if (p.length < 2 || f < p[0][0] - 1e-12 || f > p[p.length - 1][0] + 1e-12) return 0;
  for (let i = 1; i < p.length; i++) {
    if (f <= p[i][0] + 1e-12) {
      const [f0, v0] = p[i - 1], [f1, v1] = p[i];
      return f1 === f0 ? v1 : v0 + (v1 - v0) * (f - f0) / (f1 - f0);
    }
  }
  return p[p.length - 1][1];
}

function freqGrid(c, modalF) {
  const lo = +c.freq_start_hz || 0, hi = isFinite(c.freq_end_hz) ? +c.freq_end_hz : 10;
  const n = Number.isInteger(c.n_freq) ? c.n_freq : 101;
  const set = [];
  if (n === 1) set.push(lo);
  else for (let k = 0; k < n; k++) set.push(lo + (hi - lo) * k / (n - 1));
  for (const f of c.frequencies || []) if (f >= lo && f <= hi) set.push(+f);
  if (c.modal_refine !== false) {
    const z = isFinite(c.damping) ? c.damping : 0.05;
    const ks = [0, 0.1, 0.25, 0.5, 1, 2, 4, 8, 13];
    for (const fi of modalF) for (const k of ks) for (const s of k ? [-1, 1] : [1]) {
      const f = fi * (1 + s * k * z);
      if (f >= lo && f <= hi) set.push(f);
    }
  }
  const out = [...new Set(set.map(f => +f.toPrecision(12)))].sort((a, b) => a - b);
  return out.slice(0, 20000);
}

/* ---------------- response-field layout */
function layout(r) {
  const tags = Object.keys(r.nodes || {});
  const sup = (r.supports || []).filter(t => r.nodes[t]);
  const stories = r.story_order || [];
  const idx = { node: {}, base: 0, story: {}, react: {} };
  let q = 0;
  for (const t of tags) { idx.node[t] = q; q += DOF; }
  idx.base = q; q += 6;
  for (const s of stories) { idx.story[s] = q; q += 4; }
  for (const t of sup) { idx.react[t] = q; q += DOF; }
  return { tags, sup, stories, idx, Q: q };
}

/** Real response field of mode i (per unit modal coordinate), direction d (0 = X, 1 = Y). */
function modalField(r, L, i, d, Mtot) {
  const F = new Float64Array(L.Q);
  const shape = (r.modal.shapes || {})[String(i + 1)] || {};
  for (const t of L.tags) {
    const s = shape[t] || [0, 0, 0, 0, 0, 0];
    for (let k = 0; k < DOF; k++) F[L.idx.node[t] + k] = s[k] || 0;
  }
  const part = (r.modal.participation || [])[i] || {};
  const gam = d === 0 ? part.gamma_x : part.gamma_y;
  const ratio = d === 0 ? part.ux : part.uy;
  const w = 2 * Math.PI / r.modal.periods[i];
  const H = Math.max(...Object.values(r.story_elev || { a: 1 }));
  const V = gam ? w * w * (ratio || 0) * Mtot / gam : 0;
  const order = Math.ceil((i + 1) / 3);
  const hEff = (order === 1 ? 0.7 : 0.3) * H;
  const b = L.idx.base;
  if (d === 0) { F[b] = V; F[b + 4] = V * hEff; } else { F[b + 1] = V; F[b + 3] = -V * hEff; }
  let prev = 0;
  L.stories.forEach(s => {
    const z = r.story_elev[s];
    const at = L.tags.filter(t => Math.abs(r.nodes[t][2] - z) < 1e-6);
    const u = at.length ? at.reduce((a, t) => a + (shape[t] ? shape[t][d] : 0), 0) / at.length : 0;
    const zPrev = L.stories.indexOf(s) ? r.story_elev[L.stories[L.stories.indexOf(s) - 1]] : 0;
    const o = L.idx.story[s];
    F[o + d] = u;
    F[o + 2 + d] = (u - prev) / Math.max(z - zPrev, 1e-9);
    prev = u;
  });
  const n = Math.max(L.sup.length, 1);
  for (const t of L.sup) {
    const o = L.idx.react[t];
    F[o + d] = V / n;
    if (d === 0) F[o + 4] = V * hEff / n * 0.1; else F[o + 3] = -V * hEff / n * 0.1;
  }
  return F;
}

/** Real static field of a pattern (from the mock static case that holds it). */
function staticField(model, r, L, pattern) {
  let cd = null, fac = 1;
  if (r.cases && r.cases[pattern] && model.cases && model.cases[pattern] &&
      (model.cases[pattern].patterns || {})[pattern] != null) {
    cd = r.cases[pattern]; fac = model.cases[pattern].patterns[pattern] || 1;
  } else {
    for (const [n, c] of Object.entries(model.cases || {})) {
      const p = (c.patterns || {})[pattern];
      if (p != null && r.cases && r.cases[n] && Object.keys(c.patterns).length === 1) { cd = r.cases[n]; fac = p || 1; break; }
    }
  }
  if (!cd) return null;
  const F = new Float64Array(L.Q);
  for (const t of L.tags) {
    const v = (cd.node_disp || {})[t] || [];
    for (let k = 0; k < DOF; k++) F[L.idx.node[t] + k] = (v[k] || 0) / fac;
  }
  BASE_KEYS.forEach((k, j) => { F[L.idx.base + j] = ((cd.base || {})[k] || 0) / fac; });
  for (const s of L.stories) {
    const st = (cd.story || {})[s] || {};
    STORY_KEYS.forEach((k, j) => { F[L.idx.story[s] + j] = (st[k] || 0) / fac; });
  }
  for (const t of L.sup) {
    const v = (cd.reactions || {})[t] || [];
    for (let k = 0; k < DOF; k++) F[L.idx.react[t] + k] = (v[k] || 0) / fac;
  }
  const dx = Math.abs(F[L.idx.base]), dy = Math.abs(F[L.idx.base + 1]);
  return { F, dir: dy > dx ? 1 : 0 };
}

function nearestTag(r, p) {
  let best = null, bd = Infinity;
  for (const [t, c] of Object.entries(r.nodes || {})) {
    const d = Math.hypot(c[0] - p[0], c[1] - p[1], c[2] - p[2]);
    if (d < bd) { bd = d; best = t; }
  }
  return best;
}

/** One frequency-domain case → SS or PSD result dict. */
function runFrequencyCase(model, r, kind, name) {
  const dk = kind === "psd" ? "psd_cases" : "steady_state_cases";
  const c = (model[dk] || {})[name];
  if (!c) throw new Error(`unknown ${kind} case ${name}`);
  const fns = model.frequency_functions || {};
  const periods = (r.modal && r.modal.periods) || [];
  const nAll = periods.length;
  const nm = c.method === "direct" ? nAll : Math.min(nAll, c.num_modes > 0 ? c.num_modes : nAll);
  const modalF = periods.slice(0, nm).map(T => 1 / T);
  const zeta = isFinite(c.damping) ? c.damping : 0.05;
  const hyst = c.damping_type === "hysteretic";
  const f = freqGrid(c, modalF);
  const L = layout(r);
  const warnings = [];
  const Mtot = (() => {
    const fz = Math.abs((((r.cases || {}).DEAD || {}).base || {}).FZ || 0);
    return fz > 0 ? fz / G : 500;
  })();
  const H = (fi, w) => {           // modal FRF, complex
    const wi = 2 * Math.PI * fi;
    return hyst ? cdiv1([wi * wi - w * w, 2 * zeta * wi * wi]) : cdiv1([wi * wi - w * w, 2 * zeta * wi * w]);
  };
  // basis terms: {field, coef(f) → complex}
  const terms = [];
  for (const ld of c.loads || []) {
    const fn = ld.function ? fns[ld.function] : null;
    const ph = (ld.phase_deg || 0) * Math.PI / 180;
    const sc = isFinite(ld.scale) ? ld.scale : 1;
    const amp = fq => kind === "psd" ? sc * Math.sqrt(Math.max(fnEval(fn, fq), 0)) : sc * fnEval(fn, fq);
    const a = fq => { const m = amp(fq); return [m * Math.cos(ph), m * Math.sin(ph)]; };
    if (ld.pattern === "accel") {
      const d = ld.direction === "UY" ? 1 : ld.direction === "UZ" ? 2 : 0;
      if (d === 2) { warnings.push(`mock: no vertical mass modes — UZ acceleration of ${name} gives zero response`); continue; }
      for (let i = 0; i < nm; i++) {
        const part = (r.modal.participation || [])[i] || {};
        const gam = d === 0 ? part.gamma_x : part.gamma_y;
        if (!gam) continue;
        const F = modalField(r, L, i, d, Mtot);
        terms.push({ F, coef: fq => { const h = H(modalF[i], 2 * Math.PI * fq); const q = cmul(h, a(fq)); return [-gam * q[0], -gam * q[1]]; } });
      }
    } else {
      const sf = staticField(model, r, L, ld.pattern);
      if (!sf) { warnings.push(`mock: pattern ${ld.pattern} has no single-pattern static case — ignored`); continue; }
      const parts = [];
      for (let i = 0; i < nm; i++) {
        const p = (r.modal.participation || [])[i] || {};
        const w = sf.dir === 0 ? p.ux : p.uy;
        if (w > 0) parts.push([i, w]);
      }
      const sw = parts.reduce((s, x) => s + x[1], 0) || 1;
      terms.push({ F: sf.F, coef: fq => {
        const w = 2 * Math.PI * fq;
        let D = [0, 0];
        for (const [i, wt] of parts) {
          const wi = 2 * Math.PI * modalF[i];
          const h = H(modalF[i], w);
          D = [D[0] + wt / sw * wi * wi * h[0], D[1] + wt / sw * wi * wi * h[1]];
        }
        if (!parts.length) D = [1, 0];
        return cmul(D, a(fq));
      } });
    }
  }
  // response at every frequency
  const nf = f.length;
  const Zre = Array.from({ length: nf }, () => new Float64Array(L.Q));
  const Zim = Array.from({ length: nf }, () => new Float64Array(L.Q));
  f.forEach((fq, k) => {
    for (const t of terms) {
      const cf = t.coef(fq);
      if (!cf[0] && !cf[1]) continue;
      const re = Zre[k], im = Zim[k], F = t.F;
      for (let q = 0; q < L.Q; q++) { if (F[q]) { re[q] += cf[0] * F[q]; im[q] += cf[1] * F[q]; } }
    }
  });
  const ampAt = (k, q) => Math.hypot(Zre[k][q], Zim[k][q]);
  const phAt = (k, q) => Math.atan2(Zim[k][q], Zre[k][q]) * 180 / Math.PI;
  const outTags = (c.output_points || []).length
    ? [...new Set(c.output_points.map(p => nearestTag(r, p)).filter(Boolean))] : null;
  const head = {
    case: name, type: kind, method: c.method === "direct" ? "direct" : "modal",
    damping: { type: hyst ? "hysteretic" : "modal", ratio: zeta },
    modes_used: nm, modal_frequencies_hz: modalF.map(r6), frequencies_hz: f.map(r6),
  };
  if (kind === "steady_state") {
    const series = q => ({ amp: f.map((_, k) => r6(ampAt(k, q))), phase_deg: f.map((_, k) => r6(phAt(k, q))) });
    const node_disp = {};
    for (const t of outTags || L.tags) {
      const o = L.idx.node[t];
      node_disp[t] = {
        amp: f.map((_, k) => Array.from({ length: DOF }, (_, d) => r6(ampAt(k, o + d)))),
        phase_deg: f.map((_, k) => Array.from({ length: DOF }, (_, d) => r6(phAt(k, o + d)))),
      };
    }
    const base = {};
    BASE_KEYS.forEach((key, j) => { base[key] = series(L.idx.base + j); });
    const story = {};
    for (const s of L.stories) {
      story[s] = {};
      STORY_KEYS.forEach((key, j) => { story[s][key] = series(L.idx.story[s] + j); });
    }
    const peak = q => { let m = 0, at = f[0] || 0; f.forEach((fq, k) => { const a = ampAt(k, q); if (a > m) { m = a; at = fq; } }); return [r6(m), r6(at)]; };
    const peaks = { node_disp: {}, node_disp_freq_hz: {}, reactions: {}, base: {}, base_freq_hz: {}, story: {} };
    for (const t of L.tags) {
      const pk = Array.from({ length: DOF }, (_, d) => peak(L.idx.node[t] + d));
      peaks.node_disp[t] = pk.map(p => p[0]); peaks.node_disp_freq_hz[t] = pk.map(p => p[1]);
    }
    for (const t of L.sup) peaks.reactions[t] = Array.from({ length: DOF }, (_, d) => peak(L.idx.react[t] + d)[0]);
    BASE_KEYS.forEach((key, j) => { const p = peak(L.idx.base + j); peaks.base[key] = p[0]; peaks.base_freq_hz[key] = p[1]; });
    for (const s of L.stories) {
      peaks.story[s] = {};
      STORY_KEYS.forEach((key, j) => { peaks.story[s][key] = peak(L.idx.story[s] + j)[0]; });
    }
    return { ...head, node_disp, base, story, peaks, warnings };
  }
  // PSD: |Z|² curves + RMS = sqrt(trapz(|Z|², f))
  const pw = (k, q) => Zre[k][q] ** 2 + Zim[k][q] ** 2;
  const rmsOf = q => {
    let s = 0;
    for (let k = 1; k < nf; k++) s += 0.5 * (pw(k - 1, q) + pw(k, q)) * (f[k] - f[k - 1]);
    return r6(Math.sqrt(Math.max(s, 0)));
  };
  const rms = { node_disp: {}, reactions: {}, base: {}, story: {} };
  for (const t of L.tags) rms.node_disp[t] = Array.from({ length: DOF }, (_, d) => rmsOf(L.idx.node[t] + d));
  for (const t of L.sup) rms.reactions[t] = Array.from({ length: DOF }, (_, d) => rmsOf(L.idx.react[t] + d));
  BASE_KEYS.forEach((key, j) => { rms.base[key] = rmsOf(L.idx.base + j); });
  for (const s of L.stories) {
    rms.story[s] = {};
    STORY_KEYS.forEach((key, j) => { rms.story[s][key] = rmsOf(L.idx.story[s] + j); });
  }
  const curve = q => f.map((_, k) => r6(pw(k, q)));
  const psd = { node_disp: {}, base: {}, story: {} };
  for (const t of outTags || []) {
    const o = L.idx.node[t];
    psd.node_disp[t] = f.map((_, k) => Array.from({ length: DOF }, (_, d) => r6(pw(k, o + d))));
  }
  BASE_KEYS.forEach((key, j) => { psd.base[key] = curve(L.idx.base + j); });
  for (const s of L.stories) {
    psd.story[s] = {};
    STORY_KEYS.forEach((key, j) => { psd.story[s][key] = curve(L.idx.story[s] + j); });
  }
  return { ...head, correlation: "full", rms, psd, warnings };
}

export function mockFrequencyResults(model, r) {
  const out = {};
  if (!r || !r.modal || !(r.modal.periods || []).length) return out;
  for (const [kind, dk] of [["steady_state", "steady_state_cases"], ["psd", "psd_cases"]]) {
    const names = Object.keys(model[dk] || {});
    if (!names.length) continue;
    out[kind] = {};
    for (const n of names) {
      try { out[kind][n] = runFrequencyCase(model, r, kind, n); }
      catch (e) { /* invalid draft case: skip like a failed case */ }
    }
  }
  return out;
}

/* ---------------- pushover + TH option blocks */
function poDistribution(model, r, pc) {
  const stories = r.story_order || [];
  const elev = r.story_elev || {};
  const H = Math.max(1e-9, ...stories.map(s => elev[s] || 0));
  const type = pc.load_distribution || "roof_point";
  let w = {};
  const params = {};
  if (type === "roof_point") w[stories[stories.length - 1]] = 1;
  else if (type === "uniform_accel") for (const s of stories) w[s] = 1;
  else if (type === "triangular") {
    const T = (r.modal && r.modal.periods && r.modal.periods[0]) || 0.6;
    const k = isFinite(pc.k) && pc.k > 0 ? pc.k : (T <= 0.5 ? 1 : T >= 2.5 ? 2 : 1 + (T - 0.5) / 2);
    for (const s of stories) w[s] = Math.pow(elev[s], k);
    params.k = +k.toFixed(4);
    if (!(pc.k > 0)) { params.period = +T.toFixed(4); params.mode_number = 1; }
  } else if (type === "mode") {
    const mn = pc.mode_number || (pc.direction === "Y" ? 2 : 1);
    const order = Math.ceil(mn / 3);
    for (const s of stories) w[s] = Math.sin((2 * order - 1) * Math.PI * elev[s] / (2 * H));
    params.mode_number = mn;
    params.period = +(((r.modal || {}).periods || [])[mn - 1] || 0).toFixed(4);
    params.mass_ratio = +((((r.modal || {}).participation || [])[mn - 1] || {})[pc.direction === "Y" ? "uy" : "ux"] || 0).toFixed(4);
  } else if (type === "pattern") {
    const p = (model.patterns || {})[pc.pattern] || {};
    for (const sf of p.story_forces || []) if (sf.story) w[sf.story] = (w[sf.story] || 0) + Math.abs(pc.direction === "Y" ? sf.fy || 0 : sf.fx || 0);
    if (!Object.keys(w).length) for (const s of stories) w[s] = elev[s];
    params.pattern = pc.pattern;
  }
  const sum = Object.values(w).reduce((a, b) => a + b, 0) || 1;
  const story_forces = {};
  for (const [s, v] of Object.entries(w)) story_forces[s] = +(v / sum).toFixed(6);
  return { type, story_forces,
    normalization: "sum of applied push forces = 1 (base shear = lambda * reference_base_shear)",
    reference_base_shear: 1.0, params };
}

export function augmentMockResults(model, r) {
  if (!r) return r;
  const stories = r.story_order || [];
  const elev = r.story_elev || {};
  const H = Math.max(1e-9, ...stories.map(s => elev[s] || 0));
  for (const [name, po] of Object.entries(r.pushover || {})) {
    const pc = (model.pushover_cases || {})[name] || {};
    const hCtrl = pc.control_story && elev[pc.control_story] != null ? elev[pc.control_story]
      : Array.isArray(pc.control_point) ? pc.control_point[2] : H;
    let disp = (po.roof_disp || []).map(u => u * hCtrl / H);
    if (isFinite(pc.target_disp) && pc.target_disp && disp.length) {
      const last = disp[disp.length - 1] || 1;
      disp = disp.map(u => u * pc.target_disp / last);
    }
    const top = Object.entries(r.nodes || {}).find(([, c]) => Math.abs(c[2] - hCtrl) < 1e-6);
    po.capacity_curve = {
      node: top ? +top[0] : null, dof: pc.control_dof || (pc.direction === "Y" ? "UY" : "UX"),
      mode: pc.control_mode || "displacement_control", height: +hCtrl.toFixed(6),
      start_from: pc.start_from || null,
      disp: disp.map(u => +u.toFixed(6)), base_shear: (po.base_shear || []).slice(),
    };
    po.distribution = poDistribution(model, r, pc);
  }
  for (const [name, rec] of Object.entries(r.th_cases || {})) {
    const tc = (model.th_cases || {})[name];
    if (!tc || !(tc.integration || tc.di_damping || tc.solver || tc.energy)) continue;
    const it = tc.integration || {};
    const method = it.method || "newmark";
    const integ = { method };
    if (method === "newmark") Object.assign(integ, { gamma: it.gamma ?? 0.5, beta: it.beta ?? 0.25 });
    else if (method === "hht") { const a = it.alpha ?? 0; Object.assign(integ, { alpha: a, gamma: it.gamma ?? 0.5 - a, beta: it.beta ?? (1 - a) ** 2 / 4 }); }
    else if (method === "wilson") integ.theta = it.theta ?? 1.4;
    else Object.assign(integ, { gamma: 0.5, beta: 0 });
    const di = { integration: integ,
      solver: { max_iterations: 25, tolerance: 1e-8, test: "NormDispIncr", max_halvings: 0, ...(tc.solver || {}) },
      substepped_steps: [] };
    const periods = (r.modal && r.modal.periods) || [];
    if (tc.di_damping) {
      const dd = tc.di_damping;
      let a0 = dd.mass_coeff || 0, a1 = dd.stiffness_coeff || 0;
      if (dd.type === "rayleigh_by_periods") {
        const w1 = 2 * Math.PI / dd.T1, w2 = 2 * Math.PI / dd.T2, den = w2 * w2 - w1 * w1;
        a0 = 2 * w1 * w2 * (dd.xi1 * w2 - dd.xi2 * w1) / den; a1 = 2 * (dd.xi2 * w2 - dd.xi1 * w1) / den;
      }
      di.damping = { type: dd.type, stiffness: dd.stiffness || (tc.nonlinear ? "committed" : "current"), a0, a1,
        ...(dd.type === "rayleigh_by_periods" ? { T1: dd.T1, xi1: dd.xi1, T2: dd.T2, xi2: dd.xi2 } : {}),
        ratio_at_modes: periods.map(T => { const w = 2 * Math.PI / T; return +(a0 / (2 * w) + a1 * w / 2).toFixed(6); }) };
    }
    if (2 * (integ.beta ?? 0.25) < (integ.gamma ?? 0.5)) {
      const Tmin = periods.length ? Math.min(...periods) : 0.05;
      di.T_min = +Tmin.toFixed(6);
      di.dt_limit = +(method === "central_difference" ? Tmin / Math.PI
        : Tmin / (2 * Math.PI) / Math.sqrt(integ.gamma / 2 - integ.beta)).toFixed(6);
    }
    rec.direct_integration = di;
    if (tc.energy) {
      const t = rec.t || [];
      const roof = (rec.story_ux || {})[stories[stories.length - 1]] || [];
      const roofY = (rec.story_uy || {})[stories[stories.length - 1]] || [];
      const u = roof.map((x, i) => (x || 0) + (roofY[i] || 0));
      const T = periods[0] || 0.6, w = 2 * Math.PI / T, z = tc.damping ?? 0.05;
      const Mtot = Math.abs((((r.cases || {}).DEAD || {}).base || {}).FZ || 4905) / G * 0.8;
      const dt = t.length > 1 ? t[1] - t[0] : (tc.dt || 0.02);
      const kin = [], str = [], dmp = [], inp = [], zero = [];
      let ed = 0;
      for (let i = 0; i < u.length; i++) {
        const v = i ? (u[i] - u[i - 1]) / dt : 0;
        ed += 2 * z * w * Mtot * v * v * dt;
        kin.push(+(0.5 * Mtot * v * v).toFixed(6));
        str.push(+(0.5 * Mtot * w * w * u[i] * u[i]).toFixed(6));
        dmp.push(+ed.toFixed(6));
        inp.push(+(kin[i] + str[i] + dmp[i]).toFixed(6));
        zero.push(0);
      }
      rec.energy = { t: t.slice(), input: inp, kinetic: kin, strain: str, damping: dmp,
        hysteretic: zero.slice(), error: zero.slice(), error_normalized: zero.slice(),
        max_abs_error_normalized: 0, normalization: Math.max(1e-12, ...inp), units: "kN*m",
        method: "mock: SDOF energy balance on the roof response" };
    }
  }
  return r;
}

/* mock POST /api/analyze/steady_state | psd {case} */
export function mockSteadyState(model, r, name) { return runFrequencyCase(model, r, "steady_state", name); }
export function mockPsd(model, r, name) { return runFrequencyCase(model, r, "psd", name); }
