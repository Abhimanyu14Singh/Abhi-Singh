/* SkyFrame — time-dependent materials, step-by-step staged creep and bonded
   tendon elements: the ?mock=1 mirror + the client-side law curves.

   CONTRACT "Time-dependent materials, incremental creep and tendon
   elements" (skyframe/core/timedep.py, engine/staged_creep.py,
   engine/tendon_elements.py).

   * tdLaw*  — JS port of the backend laws (fib MC2010, ACI 209R-92,
     GL2000, user curves, EN 1992 / Magura relaxation).  Used by the mock
     POST /api/timedep/curves and as the offline preview.
   * tdMockValidate(model)  — the backend ValueErrors for
     material_time_dependent, the step-method staged keys and the tendon
     model_as / es_factor keys (POST /api/model).
   * tdMockAugment(model, r) — plausible results: staged step cases gain
     shortening + time_history (column tops follow sigma J(t, t0)),
     element tendons gain tendons[uid].element (elastic-shortening loss). */

export const TD_LAWS = [["cebfip2010", "CEB-FIP 2010 (fib Model Code)"], ["aci209", "ACI 209R-92"],
  ["gl2000", "GL2000 (Gardner-Lockman)"], ["user", "User curves"]];
export const TD_CEMENTS = { cebfip2010: ["SL", "N", "R"], aci209: ["I", "III"], gl2000: ["I", "II", "III"], user: ["N"] };
const DEF_CEMENT = { cebfip2010: "N", aci209: "I", gl2000: "I", user: "N" };
const KEYS = ["law", "rh", "h0", "cure_days", "cement", "curing", "fcm", "creep", "shrinkage", "aging",
  "phi_u", "eps_shu", "creep_curve", "shrinkage_curve", "modulus_curve", "strength_curve", "relaxation"];
const isNum = v => typeof v === "number" && isFinite(v);

/** Canonical props (defaults filled) or throws Error(message). */
export function tdNormalize(d, what = "time-dependent properties") {
  if (!d || typeof d !== "object") throw new Error(`${what} must be an object`);
  const bad = Object.keys(d).filter(k => !KEYS.includes(k));
  if (bad.length) throw new Error(`${what}: unknown keys ${JSON.stringify(bad)}`);
  const law = d.law ?? "cebfip2010";
  if (!TD_LAWS.some(([k]) => k === law)) throw new Error(`${what}: law must be one of cebfip2010, aci209, gl2000, user`);
  const cement = d.cement ?? DEF_CEMENT[law];
  if (law !== "user" && !TD_CEMENTS[law].includes(cement)) throw new Error(`${what}: cement for ${law} must be one of ${TD_CEMENTS[law].join(", ")}`);
  const num = (k, def, lo, hi, open) => {
    const v = d[k] ?? def;
    if (v == null) return null;
    if (!isNum(v)) throw new Error(`${what}: ${k} must be a finite number`);
    if (lo != null && (v < lo || (open && v <= lo))) throw new Error(`${what}: ${k} must be ${open ? ">" : ">="} ${lo}`);
    if (hi != null && v > hi) throw new Error(`${what}: ${k} must be <= ${hi}`);
    return v;
  };
  const p = { law, rh: num("rh", 70, 40, 100), h0: num("h0", 0.15, 0, null, true), cure_days: num("cure_days", 7, 0, null, true),
    cement, curing: d.curing ?? "moist", fcm: num("fcm", null, 0, null, true), phi_u: num("phi_u", null, 0), eps_shu: num("eps_shu", null, 0) };
  if (!["moist", "steam"].includes(p.curing)) throw new Error(`${what}: curing must be moist or steam`);
  for (const k of ["creep", "shrinkage", "aging"]) {
    const v = d[k] ?? true;
    if (typeof v !== "boolean") throw new Error(`${what}: ${k} must be a bool`);
    p[k] = v;
  }
  for (const k of ["creep_curve", "shrinkage_curve", "modulus_curve", "strength_curve"]) {
    const c = d[k];
    if (c == null) { p[k] = null; continue; }
    if (!Array.isArray(c) || c.length < 2 || !c.every(r => Array.isArray(r) && r.length === 2 && isNum(r[0]) && isNum(r[1]) && r[0] >= 0))
      throw new Error(`${what}: ${k} must list >= 2 [t, value] pairs`);
    for (let i = 1; i < c.length; i++) if (!(c[i][0] > c[i - 1][0])) throw new Error(`${what}: ${k}: t must be strictly ascending`);
    if ((k === "modulus_curve" || k === "strength_curve") && !(c[0][0] > 0)) throw new Error(`${what}: ${k}: ages must be > 0`);
    p[k] = c.map(r => [+r[0], +r[1]]);
  }
  if (law === "user" && p.creep && !p.creep_curve) throw new Error(`${what}: law 'user' needs creep_curve (or creep: false)`);
  if (law === "user" && p.shrinkage && !p.shrinkage_curve) throw new Error(`${what}: law 'user' needs shrinkage_curve (or shrinkage: false)`);
  const r = d.relaxation;
  if (r == null) p.relaxation = null;
  else {
    if (typeof r !== "object") throw new Error(`${what}: relaxation must be an object or null`);
    const rl = r.law ?? "ec2";
    if (rl === "ec2") {
      const cls = r.class ?? 2;
      if (![1, 2, 3].includes(cls)) throw new Error(`${what}: relaxation.class must be 1, 2 or 3`);
      p.relaxation = { law: "ec2", class: cls, rho1000: r.rho1000 ?? { 1: 8, 2: 2.5, 3: 4 }[cls], fpk: r.fpk ?? null };
    } else if (rl === "aci") p.relaxation = { law: "aci", K: r.K ?? 45, fpy: r.fpy ?? null };
    else throw new Error(`${what}: relaxation.law must be one of ec2, aci`);
  }
  return p;
}

/* ------------------------------------------------ laws (ages in days, SI kPa) */
const interp = (c, x) => {
  if (x <= c[0][0]) return c[0][1];
  if (x >= c[c.length - 1][0]) return c[c.length - 1][1];
  for (let i = 0; i + 1 < c.length; i++)
    if (x <= c[i + 1][0]) return c[i][1] + (c[i + 1][1] - c[i][1]) * (x - c[i][0]) / (c[i + 1][0] - c[i][0]);
  return c[c.length - 1][1];
};
const MC = { s: { SL: 0.6, N: 0.5, R: 0.2 }, a: { SL: -1, N: 0, R: 1 }, as: { SL: 800, N: 700, R: 600 }, ds1: { SL: 3, N: 4, R: 6 }, ds2: { SL: 0.013, N: 0.012, R: 0.012 } };
const ACI_AB = { "I|moist": [4, 0.85], "III|moist": [2.3, 0.92], "I|steam": [1, 0.95], "III|steam": [0.7, 0.98] };
const GL = { s: { I: 0.335, II: 0.4, III: 0.13 }, k: { I: 1, II: 0.75, III: 1.15 } };
export function tdFcm(p, fc) {
  if (p.fcm != null) return p.fcm / 1000;
  const f = (fc || 30000) / 1000;
  return p.law === "gl2000" ? 1.1 * f + 5 : f + 8;
}
const hmm = p => p.h0 * 1000, vsmm = p => p.h0 * 500;
function mcT0(p, t0) { return Math.max(t0 * (9 / (2 + t0 ** 1.2) + 1) ** MC.a[p.cement], 0.5); }
function mcCreep(p, fcm, t, t0) {
  if (t <= t0) return 0;
  const dt = t - t0, t0a = mcT0(p, t0), h = hmm(p), af = Math.sqrt(35 / fcm);
  const bh = Math.min(1.5 * h + 250 * af, 1500 * af), gam = 1 / (2.3 + 3.5 / Math.sqrt(t0a));
  return 1.8 / fcm ** 0.7 * Math.log((30 / t0a + 0.035) ** 2 * dt + 1)
    + 412 / fcm ** 1.4 * (1 - p.rh / 100) / Math.cbrt(0.1 * h) / (0.1 + t0a ** 0.2) * (dt / (bh + dt)) ** gam;
}
function mcShrink(p, fcm, t, ts) {
  const c = p.cement;
  let e = MC.as[c] * ((0.1 * fcm) / (6 + 0.1 * fcm)) ** 2.5 * 1e-6 * (1 - Math.exp(-0.2 * Math.sqrt(Math.max(t, 0))));
  if (t > ts) {
    const e0 = (220 + 110 * MC.ds1[c]) * Math.exp(-MC.ds2[c] * fcm) * 1e-6;
    const bs1 = Math.min((35 / fcm) ** 0.1, 1);
    const brh = p.rh >= 99 * bs1 ? 0.25 : -1.55 * (1 - (p.rh / 100) ** 3);
    const h = hmm(p);
    e += -e0 * brh * Math.sqrt((t - ts) / (0.035 * h * h + (t - ts)));
  }
  return e;
}
function aciCreep(p, t, t0) {
  if (t <= t0) return 0;
  let phiU = p.phi_u;
  if (phiU == null) {
    const gla = p.curing === "moist" ? 1.25 * t0 ** -0.118 : 1.13 * t0 ** -0.094;
    const h = p.rh / 100;
    phiU = 2.35 * gla * (h > 0.4 ? 1.27 - 0.67 * h : 1) * (2 / 3) * (1 + 1.13 * Math.exp(-0.0213 * vsmm(p)));
  }
  const x = (t - t0) ** 0.6;
  return x / (10 + x) * phiU;
}
function aciShrink(p, t, ts) {
  if (t <= ts) return 0;
  const f = p.curing === "moist" ? 35 : 55;
  let eu = p.eps_shu;
  if (eu == null) {
    const h = p.rh / 100;
    const grh = h <= 0.4 ? 1 : h <= 0.8 ? 1.4 - 1.02 * h : 3 - 3 * h;
    const gtc = p.curing === "moist" ? 1.202 - 0.2337 * Math.log10(ts) : 1;
    eu = 780e-6 * grh * gtc * 1.2 * Math.exp(-0.00472 * vsmm(p));
  }
  return (t - ts) / (f + (t - ts)) * eu;
}
function glCreep(p, t, t0) {
  if (t <= t0) return 0;
  const dt = t - t0, vs = vsmm(p), h = p.rh / 100, tc = p.cure_days;
  const Phi = t0 > tc ? Math.sqrt(1 - Math.sqrt((t0 - tc) / (t0 - tc + 0.12 * vs * vs))) : 1;
  return Phi * (2 * dt ** 0.3 / (dt ** 0.3 + 14) + Math.sqrt(7 / t0) * Math.sqrt(dt / (dt + 7))
    + 2.5 * (1 - 1.086 * h * h) * Math.sqrt(dt / (dt + 0.12 * vs * vs)));
}
function glShrink(p, fcm, t, ts) {
  if (t <= ts) return 0;
  const vs = vsmm(p), h = p.rh / 100;
  return 900 * GL.k[p.cement] * Math.sqrt(30 / fcm) * 1e-6 * (1 - 1.18 * h ** 4) * Math.sqrt((t - ts) / (t - ts + 0.12 * vs * vs));
}
export function tdStrengthRatio(p, t) {
  if (!p.aging) return 1;
  if (t <= 0) return 0;
  if (p.law === "cebfip2010") return Math.exp(MC.s[p.cement] * (1 - Math.sqrt(28 / t)));
  if (p.law === "aci209") { const [a, b] = ACI_AB[`${p.cement}|${p.curing}`]; return t / (a + b * t); }
  if (p.law === "gl2000") return Math.exp(GL.s[p.cement] * (1 - Math.sqrt(28 / t)));
  return p.strength_curve ? interp(p.strength_curve, t) : 1;
}
export function tdModulusRatio(p, t, fc) {
  if (!p.aging) return 1;
  if (t <= 0) return 0;
  if (p.law === "cebfip2010" || p.law === "aci209") return Math.sqrt(tdStrengthRatio(p, t));
  if (p.law === "gl2000") {
    const f28 = tdFcm(p, fc), ft = f28 * tdStrengthRatio(p, t);
    return (3500 + 4300 * Math.sqrt(ft)) / (3500 + 4300 * Math.sqrt(f28));
  }
  return p.modulus_curve ? interp(p.modulus_curve, t) : 1;
}
export function tdCreep(p, t, t0, fc) {
  if (!p.creep || t <= t0) return 0;
  if (p.law === "cebfip2010") return mcCreep(p, tdFcm(p, fc), t, t0);
  if (p.law === "aci209") return aciCreep(p, t, t0);
  if (p.law === "gl2000") return glCreep(p, t, t0);
  return interp(p.creep_curve, t - t0);
}
export function tdCompliance(p, E28, t, t0, fc) {
  const e0 = E28 * tdModulusRatio(p, t0, fc), phi = tdCreep(p, t, t0, fc);
  return (p.law === "cebfip2010" || p.law === "gl2000") ? 1 / e0 + phi / E28 : (1 + phi) / e0;
}
export function tdShrinkage(p, t, fc) {
  if (!p.shrinkage) return 0;
  const ts = p.cure_days;
  if (p.law === "cebfip2010") return mcShrink(p, tdFcm(p, fc), t, ts);
  if (p.law === "aci209") return aciShrink(p, t, ts);
  if (p.law === "gl2000") return glShrink(p, tdFcm(p, fc), t, ts);
  return t > ts ? interp(p.shrinkage_curve, t - ts) : 0;
}
export function tdRelaxation(r, sigmaPi, tDays, fpk, fpy) {
  if (!r || tDays <= 0 || sigmaPi <= 0) return 0;
  const th = 24 * tDays;
  if (r.law === "ec2") {
    const fk = r.fpk || fpk;
    if (!fk) return 0;
    const mu = sigmaPi / fk;
    const [c, k] = { 1: [5.39, 6.7], 2: [0.66, 9.1], 3: [1.98, 8.0] }[r.class];
    return c * r.rho1000 * Math.exp(k * mu) * (th / 1000) ** (0.75 * (1 - mu)) * 1e-5;
  }
  const fy = r.fpy || fpy;
  if (!fy) return 0;
  const x = sigmaPi / fy - 0.55;
  return x <= 0 || th <= 1 ? 0 : Math.log10(th) / r.K * x;
}
/** Mock POST /api/timedep/curves. */
export function tdMockCurves(props, E = 30e6, fc = null, t0 = 28, times = null) {
  const p = tdNormalize(props || {});
  const ts = times || Array.from({ length: 33 }, (_, k) => +(10 ** (k / 8)).toFixed(6));
  return { t0, times: ts,
    creep: ts.map(dt => tdCreep(p, t0 + dt, t0, fc)),
    compliance: ts.map(dt => tdCompliance(p, E, t0 + dt, t0, fc)),
    shrinkage: ts.map(t => tdShrinkage(p, t, fc)),
    modulus_ratio: ts.map(t => tdModulusRatio(p, t, fc)),
    strength_ratio: ts.map(t => tdStrengthRatio(p, t)) };
}

/* ------------------------------------------------ validation (POST /api/model) */
export function tdMockValidate(m) {
  if (!m || typeof m !== "object") return null;
  const td = m.material_time_dependent;
  if (td != null) {
    if (typeof td !== "object" || Array.isArray(td)) return "material_time_dependent must be an object";
    for (const [name, p] of Object.entries(td)) {
      if (!(m.materials || {})[name]) return `material_time_dependent: unknown material '${name}'`;
      try { tdNormalize(p, `Material '${name}' time-dependent properties`); } catch (e) { return e.message; }
    }
  }
  for (const sc of Object.values(m.staged_cases || {})) {
    const t = sc && sc.time_dependent;
    if (!t || typeof t !== "object") continue;
    const method = t.method ?? "aaem";
    if (!["aaem", "step"].includes(method)) return `Staged case ${sc.name}: time_dependent['method'] must be one of aaem, step`;
    const stepKeys = "steps_per_stage" in t || "output_times" in t;
    if (method !== "step") { if (stepKeys) return `Staged case ${sc.name}: steps_per_stage / output_times need method 'step'`; continue; }
    if (!Array.isArray(sc.stages)) return `Staged case ${sc.name}: method 'step' needs a user-defined stage list (stages as a list)`;
    const n = t.steps_per_stage ?? 4;
    if (!Number.isInteger(n) || n < 1 || n > 50) return `Staged case ${sc.name}: steps_per_stage must be an integer in 1..50`;
    if (t.output_times != null && (!Array.isArray(t.output_times) || !t.output_times.every(v => isNum(v) && v >= 0)))
      return `Staged case ${sc.name}: output_times must be a list of days >= 0`;
  }
  for (const t of m.tendons || []) {
    if (!t || typeof t !== "object") continue;
    if (t.model_as != null && !["loads", "elements"].includes(t.model_as)) return `Tendon '${t.uid}': model_as must be one of loads, elements`;
    const es = t.losses && t.losses.es_factor;
    if (es != null && !(isNum(es) && es >= 0)) return `Tendon '${t.uid}': losses.es_factor must be >= 0`;
  }
  return null;
}

/* ------------------------------------------------ DB-edit tables (mirrors core/modeltables_td.py) */
export function tdMdbExtend(T, H) {
  const { col, obj, names } = H;
  const n = "number";
  const DEFV = { law: "cebfip2010", rh: 70, h0: 0.15, cure_days: 7, curing: "moist", creep: true, shrinkage: true, aging: true };
  const g = k => o => o[k] ?? (k in DEFV ? DEFV[k] : k === "cement" ? DEF_CEMENT[o.law || "cebfip2010"] : null);
  const p = k => (o, v) => { if (v == null || v === "") delete o[k]; else if (k in o || v !== DEFV[k]) o[k] = v; };
  const c = (k, label, type = n, q = "none", o = {}) => col(k, label, type, q, { get: g(k), put: p(k), ...o });
  const tbl = obj("material_time_dependent", "Material Properties - Time Dependent", "Model Definition > Properties", [
    col("name", "Material", "enum", "none", { enum: names("materials"), required: true }),
    c("law", "Law", "enum", "none", { enum: TD_LAWS.map(([k]) => k), def: "cebfip2010" }),
    c("rh", "Relative Humidity (%)", n, "none", { def: 70 }),
    c("h0", "Notional Size h0", n, "dim", { def: 0.15 }),
    c("cure_days", "Curing Age (days)", n, "none", { def: 7 }),
    c("cement", "Cement", "text", "none", { def: "" }),
    c("curing", "Curing", "enum", "none", { enum: ["moist", "steam"], def: "moist" }),
    c("fcm", "Mean Strength fcm", n, "stress", { optional: true }),
    c("creep", "Creep", "bool", "none", { def: true }),
    c("shrinkage", "Shrinkage", "bool", "none", { def: true }),
    c("aging", "Ageing", "bool", "none", { def: true }),
    c("phi_u", "ACI phi_u", n, "none", { optional: true }),
    c("eps_shu", "ACI eps_shu", n, "none", { optional: true }),
  ], { container: "material_time_dependent", keycol: "name", nameInObj: false, newObj: () => ({ law: "cebfip2010" }) });
  const i = T.findIndex(t => t.key === "materials");
  T.splice(i >= 0 ? i + 1 : T.length, 0, tbl);
  const td = T.find(t => t.key === "tendons");
  if (td) td.cols.push(
    col("model_as", "Model As", "enum", "none", { enum: ["loads", "elements"], def: "loads", get: o => o.model_as || "loads",
      put: (o, v) => { if (v === "elements") o.model_as = "elements"; else delete o.model_as; } }),
    col("es_factor", "ES Loss Factor", n, "none", { def: 0, get: o => (o.losses || {}).es_factor ?? 0,
      put: (o, v) => { const ls = { ...(o.losses || {}) }; if (!v) { if (!("es_factor" in ls)) return; delete ls.es_factor; } else ls.es_factor = +v; o.losses = ls; } }));
  return T;
}

/* ------------------------------------------------ results (mockResults hook) */
function stepGrid(t0, t1, n, extra) {
  if (t1 <= t0) return [];
  const D = t1 - t0, s = new Set();
  for (let k = 1; k <= n; k++) s.add(t0 + D * (2 ** k - 1) / (2 ** n - 1));
  for (const t of extra) if (t > t0 && t <= t1) s.add(t);
  s.add(t1);
  return [...s].sort((a, b) => a - b);
}

export function tdMockAugment(model, r) {
  if (!model || !r) return;
  // element tendons: ES loss ~ (Ep/Ec) fcgp with a nominal fcgp of 6 MPa
  for (const t of model.tendons || []) {
    if (!t || t.model_as !== "elements" || !r.tendons || !r.tendons[t.uid]) continue;
    const rep = r.tendons[t.uid];
    const P = (rep.stations && rep.stations.P) || [];
    const n = Math.max(1, P.length - 1);
    const Pm = Array.from({ length: n }, (_, i) => 0.5 * ((P[i] || 0) + (P[i + 1] ?? P[i] ?? 0)));
    const loss = Pm.map(() => 6.5 * 6000 * (t.area || 0));           // n = 6.5, fcgp = 6 MPa
    const cases = {};
    for (const [cn, c] of Object.entries(model.cases || {})) {
      const sc = (c.patterns || {})[t.pattern] || 0;
      if (!sc) continue;
      cases[cn] = { P: Pm.map((v, i) => sc * v - sc * loss[i]), P_initial: Pm.map(v => sc * v), loss: loss.map(v => sc * v),
        P_mean: Pm.reduce((a, v, i) => a + sc * (v - loss[i]), 0) / n };
    }
    const pts = (rep.stations && rep.stations.points) || [];
    rep.element = { n, points: Pm.map((_, i) => [pts[i] || [0, 0, 0], pts[i + 1] || pts[i] || [0, 0, 0]]),
      length: Pm.map(() => (rep.length || 0) / n), P_initial: Pm, stiffness: Pm.map(() => 0), cases };
  }
  // staged step-method cases: shortening + time history
  for (const [name, sc] of Object.entries(model.staged_cases || {})) {
    const td = sc && sc.time_dependent;
    if (!td || td.method !== "step" || !Array.isArray(sc.stages) || !r.staged || !r.staged[name]) continue;
    const cd = r.staged[name];
    const mats = model.material_time_dependent || {};
    const firstMat = Object.keys(mats)[0];
    let p;
    try { p = firstMat ? tdNormalize(mats[firstMat]) : tdNormalize({ law: "user", creep_curve: [[0, 0], [10000, td.creep_coeff ?? 2]], shrinkage: false, aging: false }); }
    catch { continue; }
    const E = (model.materials[firstMat] || {}).E || 30e6, fc = (model.materials[firstMat] || {}).fc || null;
    const n = td.steps_per_stage ?? 4, outs = (td.output_times || []).map(Number);
    let t = 0;
    const times = [], stage = [];
    sc.stages.forEach(st => {
      times.push(t); stage.push(st.name);
      for (const tb of stepGrid(t, t + (+st.duration_days || 0), n, outs)) { times.push(tb); stage.push(st.name); }
      t += +st.duration_days || 0;
    });
    const tEnd = td.t_eval != null ? +td.t_eval : t;
    for (const tb of stepGrid(t, tEnd, n, outs)) { times.push(tb); stage.push("(long term)"); }
    const order = r.story_order || [];
    const story_uz = {}, shortening = {};
    const t0 = 28;
    order.forEach((s, j) => {
      const uzEl = -2e-3 * (j + 1) / Math.max(order.length, 1);     // nominal elastic column-top settlement
      const ratio = tt => tdCompliance(p, E, t0 + tt, t0, fc) * E * tdModulusRatio(p, t0, fc);
      story_uz[s] = times.map(tt => uzEl * ratio(tt) - (tdShrinkage(p, t0 + tt, fc) - tdShrinkage(p, t0, fc)) * 3 * (j + 1));
      const last = story_uz[s][story_uz[s].length - 1];
      shortening[s] = { uz_elastic: uzEl, uz_time_dependent: last, delta: last - uzEl };
    });
    const tendon_force = {};
    for (const tt of model.tendons || []) {
      if (tt.model_as !== "elements") continue;
      const P0 = (tt.area || 0) * (tt.jacking_stress || 0) * 0.94;
      tendon_force[tt.uid] = times.map(x => P0 * (1 - 0.08 * Math.log10(1 + x) / 4));
    }
    cd.shortening = shortening;
    cd.time_history = { method: "step", times, stage, story_uz, tendon_force,
      outputs: outs.filter(x => times.some(y => Math.abs(y - x) < 1e-9)).map(x => ({ t: x, node_disp: cd.node_disp || {}, member_forces: cd.member_forces || {}, base: cd.base || {}, tendon_force: {} })),
      tendons: {} };
  }
}
