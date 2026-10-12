/* SkyFrame — mock of the pushover performance methods (?mock=1).

   A line-by-line port of skyframe/core/pushover_methods.py (ADRS
   conversion, ATC-40 procedures A/B, FEMA 440 EL + MADRS, EN 1998-1
   Annex B N2, ASCE 41 coefficient method) so the mock returns the SAME
   JSON shape as POST /api/results/pushover-performance and the
   `performance` block of a pushover result. Only the modal properties
   are mocked (dominant mode T / alpha1 from the mock modal results,
   PF1·phi_roof = 1.3, W = stories × 500 t × g). */

const G = 9.81;
export const POX_METHODS = ["asce41", "atc40", "fema440", "n2"];
export const POX_LABELS = {
  asce41: "ASCE 41-17 coefficient method", atc40: "ATC-40 capacity spectrum",
  fema440: "FEMA 440 equivalent linearization", n2: "EN 1998-1 Annex B (N2)",
};
export const POX_DEFAULTS = {
  methods: ["asce41", "atc40", "fema440", "n2"],
  spectrum: { code: "asce7", SDS: 1.0, SD1: 0.6 },
  site_class: "D", behavior: "A", atc40_procedure: "A",
};
export const EC8_TABLE = {
  1: { A: [1.0, 0.15, 0.4, 2.0], B: [1.2, 0.15, 0.5, 2.0], C: [1.15, 0.20, 0.6, 2.0], D: [1.35, 0.20, 0.8, 2.0], E: [1.4, 0.15, 0.5, 2.0] },
  2: { A: [1.0, 0.05, 0.25, 1.2], B: [1.35, 0.05, 0.25, 1.2], C: [1.5, 0.10, 0.25, 1.2], D: [1.8, 0.10, 0.30, 1.2], E: [1.6, 0.05, 0.25, 1.2] },
};
const SR_MIN = { A: [0.33, 0.50], B: [0.44, 0.56], C: [0.56, 0.67] };
const T_MAX = 10.0;
const num = v => typeof v === "number" && isFinite(v);

/** Options with defaults filled; throws on invalid input (backend rules). */
export function poxNormalize(perf) {
  const p = perf == null ? {} : perf;
  const allowed = Object.keys(POX_DEFAULTS);
  for (const k of Object.keys(p)) if (!allowed.includes(k)) throw new Error(`performance: unknown key ${k}`);
  const o = JSON.parse(JSON.stringify({ ...POX_DEFAULTS, ...p }));
  if (!Array.isArray(o.methods) || !o.methods.length || o.methods.some(m => !POX_METHODS.includes(m)) ||
      new Set(o.methods).size !== o.methods.length) throw new Error("performance.methods must be a non-empty list of distinct methods");
  const sp = o.spectrum || {};
  if (sp.code === "ec8") {
    if (!(num(sp.ag) && sp.ag > 0)) throw new Error("performance.spectrum.ag must be > 0 (g)");
    const st = sp.spectrum_type ?? 1, gt = sp.ground_type ?? "C";
    if (![1, 2].includes(st)) throw new Error("spectrum_type must be 1 or 2");
    if (!EC8_TABLE[st][gt]) throw new Error("ground_type must be A..E");
  } else if (sp.code === "asce7" || sp.code == null) {
    for (const k of ["SDS", "SD1"]) if (!(num(sp[k]) && sp[k] > 0)) throw new Error(`performance.spectrum.${k} must be > 0 (g)`);
  } else throw new Error("performance.spectrum.code must be asce7 or ec8");
  if (!["A", "B", "C", "D", "E", "F"].includes(o.site_class)) throw new Error("site_class must be A..F");
  if (!["A", "B", "C"].includes(o.behavior)) throw new Error("behavior must be A, B or C");
  if (!["A", "B"].includes(o.atc40_procedure)) throw new Error("atc40_procedure must be A or B");
  return o;
}

export function poxSpectrum(sp) {
  if (sp.code === "ec8") {
    const st = sp.spectrum_type ?? 1, gt = sp.ground_type ?? "C";
    const [S0, TB0, TC0, TD0] = EC8_TABLE[st][gt];
    const s = { code: "ec8", ag: sp.ag, S: sp.S ?? S0, TB: sp.TB ?? TB0, TC: sp.TC ?? TC0, TD: sp.TD ?? TD0 };
    s.sa = T => {
      T = Math.max(T, 0); const a = s.ag * s.S;
      if (T <= s.TB) return a * (1 + T / s.TB * 1.5);
      if (T <= s.TC) return a * 2.5;
      if (T <= s.TD) return a * 2.5 * s.TC / T;
      return a * 2.5 * s.TC * s.TD / (T * T);
    };
    s.describe = () => ({ code: "ec8", ag: s.ag, S: s.S, TB: s.TB, TC: s.TC, TD: s.TD, ground_type: gt, spectrum_type: st });
    return s;
  }
  const s = { code: "asce7", SDS: sp.SDS, SD1: sp.SD1, TL: sp.TL ?? null };
  s.TC = s.SD1 / s.SDS; s.T0 = 0.2 * s.TC;
  s.sa = T => {
    T = Math.max(T, 0);
    if (T < s.T0) return s.SDS * (0.4 + 0.6 * T / s.T0);
    if (T <= s.TC) return s.SDS;
    if (s.TL != null && T > s.TL) return s.SD1 * s.TL / (T * T);
    return s.SD1 / T;
  };
  s.describe = () => ({ code: "asce7", SDS: s.SDS, SD1: s.SD1, TL: s.TL, Ts: s.TC, T0: s.T0 });
  return s;
}

const sdOf = (sa, T) => sa * G * T * T / (4 * Math.PI ** 2);
function demand(saFn, mult = 1) {
  const point = T => { const s = saFn(T); return [sdOf(s, T), mult * s]; };
  return {
    point,
    saAtSd(sd) {
      if (sd <= 0) return point(0)[1];
      let lo = 0, hi = T_MAX;
      if (point(hi)[0] < sd) return null;
      for (let i = 0; i < 200 && hi - lo > 1e-12; i++) { const mid = (lo + hi) / 2; if (point(mid)[0] < sd) lo = mid; else hi = mid; }
      return point((lo + hi) / 2)[1];
    },
    curve() {
      const Sd = [], Sa = [];
      for (let i = 0; i <= 160; i++) { const [d, a] = point(T_MAX * (i / 160) ** 2); Sd.push(d); Sa.push(a); }
      return { Sd, Sa };
    },
  };
}

function capacity(sd, sa) {
  const d = [0], a = [0];
  sd.forEach((x, i) => { if (x > d[d.length - 1]) { d.push(x); a.push(sa[i]); } });
  if (d.length < 3 || a[1] <= 0) throw new Error("capacity curve: need at least 2 positive points");
  const K = a[1] / d[1], dEnd = d[d.length - 1];
  const saAt = x => {
    if (x < 0 || x > dEnd * (1 + 1e-12)) return null;
    for (let i = 0; i + 1 < d.length; i++) if (x <= d[i + 1]) return a[i] + (a[i + 1] - a[i]) * (x - d[i]) / (d[i + 1] - d[i]);
    return a[a.length - 1];
  };
  const area = up => {
    let A = 0;
    for (let i = 0; i + 1 < d.length; i++) {
      if (up <= d[i]) break;
      if (up < d[i + 1]) { const au = saAt(up); A += 0.5 * (a[i] + au) * (up - d[i]); break; }
      A += 0.5 * (a[i] + a[i + 1]) * (d[i + 1] - d[i]);
    }
    return A;
  };
  const bilinear = dpi0 => {
    const dpi = Math.min(Math.max(dpi0, dEnd * 1e-12), dEnd), api = saAt(dpi), A = area(dpi);
    const den = K * dpi - api;
    let dy = den <= 1e-12 * K * dpi ? dpi : Math.min(Math.max((2 * A - dpi * api) / den, 0), dpi);
    if (dy <= 0) throw new Error("bilinear: degenerate capacity curve");
    const ay = K * dy;
    const alpha = dpi - dy > 1e-12 * dpi ? (api - ay) / (dpi - dy) / K : 0;
    return { K, dy, ay, dpi, api, alpha, mu: dpi / dy, T0: 2 * Math.PI / Math.sqrt(K * G), area: A };
  };
  const intersect = dem => {
    const f = x => { const s = dem.saAtSd(x); return saAt(x) - (s == null ? -1 : s); };
    const pts = new Set();
    for (let k = 1; k < 8; k++) pts.add(d[1] * k / 8);
    for (let i = 1; i + 1 < d.length; i++) for (let k = 0; k < 8; k++) pts.add(d[i] + (d[i + 1] - d[i]) * k / 8);
    pts.add(dEnd);
    let px = 0, pf = -1;
    for (const x of [...pts].sort((u, v) => u - v)) {
      const fx = f(x);
      if (fx >= 0 && pf < 0) {
        let lo = px, hi = x;
        for (let i = 0; i < 200 && hi - lo > 1e-13 * Math.max(1, hi); i++) { const m = (lo + hi) / 2; if (f(m) >= 0) hi = m; else lo = m; }
        const s = (lo + hi) / 2;
        return [s, saAt(s)];
      }
      px = x; pf = fx;
    }
    return null;
  };
  return { d, a, K, dEnd, saAt, area, bilinear, intersect, toDict: () => ({ Sd: d.slice(1), Sa: a.slice(1) }) };
}

export function atc40Kappa(b, beta0, x) {
  if (b === "A") return beta0 <= 16.25 ? 1.0 : 1.13 - 0.51 * x;
  if (b === "B") return beta0 <= 25 ? 0.67 : 0.845 - 0.446 * x;
  return 0.33;
}
function atc40Damping(bl, b) {
  const x = Math.max((bl.ay * bl.dpi - bl.dy * bl.api) / (bl.api * bl.dpi), 0);
  const beta0 = 63.7 * x, kappa = atc40Kappa(b, beta0, x), beta_eff = kappa * beta0 + 5;
  const SRA = Math.min(Math.max((3.21 - 0.68 * Math.log(beta_eff)) / 2.12, SR_MIN[b][0]), 1);
  const SRV = Math.min(Math.max((2.31 - 0.41 * Math.log(beta_eff)) / 1.65, SR_MIN[b][1]), 1);
  return { beta0, kappa, beta_eff, SRA, SRV };
}
const atc40Reduced = (spec, sra, srv) => { const pl = spec.sa(spec.TC); return T => T <= spec.TC ? sra * spec.sa(T) : Math.min(sra * pl, srv * spec.sa(T)); };

export function fema440El(mu0, alpha, T0) {
  const mu = Math.max(mu0, 1), m = mu - 1;
  let beta, r;
  if (mu < 4) { beta = 4.9 * m * m - 1.1 * m ** 3 + 5; r = 0.20 * m * m - 0.038 * m ** 3 + 1; }
  else if (mu <= 6.5) { beta = 14 + 0.32 * m + 5; r = 0.28 + 0.13 * m + 1; }
  else { r = 0.89 * (Math.sqrt(m / (1 + 0.05 * (mu - 2))) - 1) + 1; const q = 0.64 * m; beta = 19 * ((q - 1) / (q * q)) * r * r + 5; }
  const sec2 = (1 + alpha * (mu - 1)) / mu;
  return { mu, alpha, T0, Teff: r * T0, Tsec: T0 / Math.sqrt(sec2), beta_eff: beta, B: 4 / (5.6 - Math.log(beta)), M: r * r * sec2 };
}

function solve(cap, trial, d0) {
  const its = [];
  let dpi = Math.min(Math.max(d0, 1e-9 * cap.dEnd), cap.dEnd);
  for (let k = 0; k < 60; k++) {
    const [di, info] = trial(dpi);
    its.push({ ...info, dpi, di });
    if (di == null) break;
    if (Math.abs(di - dpi) <= 1e-6 * Math.max(dpi, 1e-12)) return { converged: true, solver: "iteration", d: di, info, its };
    dpi = Math.min(Math.max(di, 1e-9 * cap.dEnd), cap.dEnd);
  }
  const h = x => { const [di, info] = trial(x); return [di == null ? Infinity : di - x, di, info]; };
  const xs = [...new Set([...[1, 2, 3, 4, 5, 6, 7].map(k => cap.d[1] * k / 8), ...cap.d.slice(1)])].sort((u, v) => u - v);
  let prev = null;
  for (const x of xs) {
    const hx = h(x);
    if (prev && prev[1][0] > 0 && hx[0] <= 0) {
      let lo = prev[0], hi = x;
      for (let i = 0; i < 200 && hi - lo > 1e-12 * hi; i++) { const m = (lo + hi) / 2; if (h(m)[0] > 0) lo = m; else hi = m; }
      const dd = (lo + hi) / 2, [di, info] = trial(dd);
      its.push({ ...info, dpi: dd, di });
      return { converged: true, solver: "bisection", d: di == null ? dd : di, info, its };
    }
    prev = [x, hx];
  }
  return { converged: false, solver: "none", d: null, info: its.length ? its[its.length - 1] : {}, its };
}
const strip = its => its.map(it => { const o = { ...it }; delete o.bilinear; return o; });
const start = (cap, spec) => { const h = cap.intersect(demand(spec.sa)); return h ? h[0] : cap.dEnd; };

function atcOut(cap, spec, res, behavior, procedure) {
  const info = res.info || {};
  const out = { procedure, behavior, converged: res.converged, solver: res.solver, iterations: strip(res.its) };
  for (const k of ["beta0", "kappa", "beta_eff", "SRA", "SRV"]) out[k] = info[k] ?? null;
  out.bilinear = info.bilinear || null;
  if (info.SRA != null) out.reduced_demand = demand(atc40Reduced(spec, info.SRA, info.SRV)).curve();
  const d = res.d;
  out.performance_point = d == null ? null : { Sd: d, Sa: cap.saAt(Math.min(d, cap.dEnd)) };
  if (d != null) { const a = cap.saAt(Math.min(d, cap.dEnd)); out.Teff = a ? 2 * Math.PI * Math.sqrt(d / (a * G)) : null; }
  return out;
}

function atc40(cap, spec, behavior, procedure) {
  if (procedure === "B") {
    const bl0 = cap.bilinear(cap.dEnd), { K, dy, ay } = bl0, slope = bl0.alpha * K;
    const bil = x => x <= dy ? K * x : ay + slope * (x - dy);
    const locus = x => {
      const api = bil(x), bl = { ...bl0, dpi: x, api, mu: x / dy };
      const dm = x > dy ? atc40Damping(bl, behavior) : { beta0: 0, kappa: atc40Kappa(behavior, 0, 0), beta_eff: 5,
        SRA: Math.min((3.21 - 0.68 * Math.log(5)) / 2.12, 1), SRV: Math.min((2.31 - 0.41 * Math.log(5)) / 1.65, 1) };
      const ar = demand(atc40Reduced(spec, dm.SRA, dm.SRV)).saAtSd(x);
      return [(ar == null ? -1 : ar) - api, dm, bl];
    };
    let prev = null, found = null;
    const lsd = [], lsa = [];
    for (let k = 1; k <= 400; k++) {
      const x = cap.dEnd * k / 400, [fx, , bl] = locus(x);
      lsd.push(x); lsa.push(fx + bl.api);
      if (found == null && prev && prev[1] > 0 && fx <= 0) {
        let lo = prev[0], hi = x;
        for (let i = 0; i < 200 && hi - lo > 1e-12 * hi; i++) { const m = (lo + hi) / 2; if (locus(m)[0] > 0) lo = m; else hi = m; }
        found = (lo + hi) / 2;
      }
      prev = [x, fx];
    }
    let res;
    if (found == null) res = { converged: false, solver: "locus", d: null, info: { ...locus(cap.dEnd)[1], bilinear: bl0 }, its: [] };
    else { const [, dm, bl] = locus(found); res = { converged: true, solver: "locus", d: found, info: { ...dm, bilinear: bl }, its: [] }; }
    const out = atcOut(cap, spec, res, behavior, "B");
    out.locus = { Sd: lsd, Sa: lsa };
    out.bilinear_fixed = bl0;
    if (found != null) out.performance_point.Sa = bil(found);
    return out;
  }
  const trial = dpi => {
    const bl = cap.bilinear(dpi), dm = atc40Damping(bl, behavior);
    const hit = cap.intersect(demand(atc40Reduced(spec, dm.SRA, dm.SRV)));
    return [hit ? hit[0] : null, { ...dm, bilinear: bl }];
  };
  return atcOut(cap, spec, solve(cap, trial, start(cap, spec)), behavior, "A");
}

function fema440(cap, spec) {
  const trial = dpi => {
    const bl = cap.bilinear(dpi), el = fema440El(bl.mu, bl.alpha, bl.T0);
    const hit = cap.intersect(demand(T => spec.sa(T) / el.B, el.M));
    return [hit ? hit[0] : null, { ...el, bilinear: bl }];
  };
  const res = solve(cap, trial, start(cap, spec)), info = res.info || {};
  const out = { converged: res.converged, solver: res.solver, iterations: strip(res.its), bilinear: info.bilinear || null };
  for (const k of ["mu", "alpha", "T0", "Teff", "Tsec", "beta_eff", "B", "M"]) out[k] = info[k] ?? null;
  if (info.B != null) {
    out.reduced_demand = demand(T => spec.sa(T) / info.B).curve();
    out.madrs = demand(T => spec.sa(T) / info.B, info.M).curve();
  }
  out.performance_point = res.d == null ? null : { Sd: res.d, Sa: cap.saAt(res.d) };
  if (res.d != null && info.Teff) out.d_max_radial = sdOf(spec.sa(info.Teff) / info.B, info.Teff);
  return out;
}

function n2(disp, shear, gamma, mStar, spec) {
  const ds = [0, ...disp.map(d => d / gamma)], fs = [0, ...shear.map(v => v / gamma)];
  const fmax = Math.max(...fs);
  let ip = 0;
  fs.forEach((f, i) => { if (f >= fmax * (1 - 1e-9)) ip = i; });
  if (ip < 1 || fs[ip] <= 0) throw new Error("n2: the capacity curve has no positive force");
  const Fy = fs[ip], dm = ds[ip];
  let Em = 0;
  for (let i = 0; i < ip; i++) Em += 0.5 * (fs[i] + fs[i + 1]) * (ds[i + 1] - ds[i]);
  const dy = 2 * (dm - Em / Fy);
  if (dy <= 0) throw new Error("n2: non-positive idealised yield displacement");
  const T = 2 * Math.PI * Math.sqrt(mStar * dy / Fy), Se = spec.sa(T);
  const det = Se * G * (T / (2 * Math.PI)) ** 2, TC = spec.TC, qu = Se * G * mStar / Fy;
  let rule, dt;
  if (T >= TC) { rule = "equal_displacement"; dt = det; }
  else if (Fy / mStar >= Se * G) { rule = "elastic"; dt = det; }
  else { rule = "short_period"; dt = Math.max(det / qu * (1 + (qu - 1) * TC / T), det); }
  return { gamma, m_star: mStar, F_star_y: Fy, d_star_y: dy, d_star_m: dm, E_star_m: Em, T_star: T, TC, Se,
    d_star_et: det, qu, d_star_t: dt, rule, dt: gamma * dt, epp: { d_star: [0, dy, Math.max(dm, dt)], F_star: [0, Fy, Fy] } };
}

function asce41(disp, shear, Ti, W, spec, site, stories) {
  const d = [0, ...disp], v = [0, ...shear];
  let ip = 0;
  v.forEach((x, i) => { if (x > v[ip]) ip = i; });
  const dd = d.slice(0, ip + 1), vv = v.slice(0, ip + 1), du = dd[dd.length - 1], Vu = vv[vv.length - 1];
  const Ki = vv[1] / dd[1];
  let area = 0;
  for (let i = 0; i + 1 < dd.length; i++) area += (vv[i] + vv[i + 1]) * (dd[i + 1] - dd[i]) / 2;
  const interpD = q => { for (let i = 0; i + 1 < dd.length; i++) if ((vv[i] - q) * (vv[i + 1] - q) <= 0 && vv[i + 1] !== vv[i]) return dd[i] + (dd[i + 1] - dd[i]) * (q - vv[i]) / (vv[i + 1] - vv[i]); return du * q / Vu; };
  let Vy = Vu;
  for (let it = 0; it < 200; it++) {
    const Ke = 0.6 * Vy / interpD(0.6 * Vy), den = du / 2 - Vu / (2 * Ke);
    if (Math.abs(den) < 1e-12 * du) { Vy = Vu; break; }
    const nx = (area - du * Vu / 2) / den;
    if (nx >= Vu) { Vy = Vu; break; }
    if (Math.abs(nx - Vy) <= 1e-10 * Math.max(1, Math.abs(Vy))) { Vy = nx; break; }
    Vy = nx;
  }
  const elastic = Vy >= Vu * (1 - 1e-12);
  const Ke = elastic ? Ki : 0.6 * Vy / interpD(0.6 * Vy);
  const Te = Ti * Math.sqrt(Ki / Ke), Sa = spec.sa(Te), mu = Sa / (Vy / W);
  const aF = { A: 130, B: 130, C: 90, D: 60, E: 60, F: 60 }[site];
  const C1 = Te >= 1 ? 1 : 1 + (mu - 1) / (aF * Math.max(Te, 0.2) ** 2);
  const C2 = Te > 0.7 ? 1 : 1 + ((mu - 1) / Te) ** 2 / 800;
  const C0T = [[1, 1.0], [2, 1.2], [3, 1.3], [5, 1.4], [10, 1.5]];
  const n = Math.max(stories, 1);
  let C0 = 1.5;
  for (let i = 0; i + 1 < C0T.length; i++) { const [n1, c1] = C0T[i], [n2_, c2] = C0T[i + 1]; if (n <= n2_) { C0 = n <= n1 ? c1 : c1 + (c2 - c1) * (n - n1) / (n2_ - n1); break; } }
  const delta_t = C0 * C1 * C2 * Sa * (Te / (2 * Math.PI)) ** 2 * G;
  return { Ki, Ke, Vy, dy: Vy / Ke, du, Vu, area, elastic, Te, Ti, Sa, mu, Cm: 1, C0, C1, C2, W, site_class: site, delta_t };
}

const interp = (xs, ys, x) => {
  if (!xs.length) return null;
  if (x <= xs[0]) return xs[0] ? ys[0] * x / xs[0] : ys[0];
  for (let i = 0; i + 1 < xs.length; i++) if (x <= xs[i + 1]) return ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / (xs[i + 1] - xs[i]);
  return null;
};

/** Mirror of skyframe.core.pushover_methods.evaluate. */
export function poxEvaluate(disp, shear, modal, options, numStories = 1) {
  const opts = poxNormalize(options);
  const spec = poxSpectrum(opts.spectrum);
  const W = modal.W, alpha = modal.alpha, pf = Math.abs(modal.pf_phi);
  if (disp.length < 2) throw new Error("pushover performance: fewer than 2 converged steps");
  const Sd = disp.map(d => d / pf), Sa = shear.map(v => v / (alpha * W));
  const cap = capacity(Sd, Sa);
  const warnings = [], methods = {}, summary = [];
  const row = (key, sd, extra) => {
    const r = { method: key, label: POX_LABELS[key] };
    if (sd == null) Object.assign(r, { roof_disp: null, base_shear: null, Sd: null, Sa: null, beyond_curve: true });
    else { const dr = sd * pf, v = interp(disp, shear, dr); Object.assign(r, { roof_disp: dr, base_shear: v, Sd: sd, Sa: v == null ? null : v / (alpha * W), beyond_curve: v == null }); }
    Object.assign(r, extra);
    summary.push(r);
  };
  for (const key of opts.methods) {
    try {
      if (key === "asce41") {
        const res = asce41(disp, shear, modal.T, W, spec, opts.site_class, numStories);
        methods[key] = res; row(key, res.delta_t / pf, { Teff: res.Te, beta_eff: null, converged: true });
      } else if (key === "atc40") {
        const res = atc40(cap, spec, opts.behavior, opts.atc40_procedure);
        methods[key] = res; row(key, res.performance_point ? res.performance_point.Sd : null, { Teff: res.Teff ?? null, beta_eff: res.beta_eff, converged: res.converged });
      } else if (key === "fema440") {
        const res = fema440(cap, spec);
        methods[key] = res; row(key, res.performance_point ? res.performance_point.Sd : null, { Teff: res.Teff, beta_eff: res.beta_eff, converged: res.converged });
      } else {
        const res = n2(disp, shear, pf, alpha * modal.mass / pf, spec);
        methods[key] = res; row(key, res.d_star_t, { Teff: res.T_star, beta_eff: null, converged: true });
      }
      const last = summary[summary.length - 1];
      if (last && last.method === key && last.roof_disp == null) warnings.push(`${POX_LABELS[key]}: the demand exceeds the capacity curve (no performance point within the pushover)`);
      else if (last && last.method === key && last.beyond_curve) warnings.push(`${POX_LABELS[key]}: the target lies beyond the end of the capacity curve`);
    } catch (e) { methods[key] = { error: e.message }; warnings.push(`${POX_LABELS[key]}: ${e.message}`); }
  }
  return { options: opts, modal: { ...modal, pf_phi: pf }, spectrum: spec.describe(), capacity: cap.toDict(),
    demand: demand(spec.sa).curve(), methods, summary, warnings };
}

/** Mock modal properties of a pushover case (see header). */
export function poxMockModal(model, results, caseName) {
  const pc = (model.pushover_cases || {})[caseName] || {};
  const key = (pc.direction || "X") === "X" ? "ux" : "uy";
  const parts = (results && results.modal && results.modal.participation) || [];
  const p = parts.length ? parts.reduce((a, b) => ((b[key] || 0) > (a[key] || 0) ? b : a)) : { mode: 1, T: 0.8, [key]: 0.8 };
  const mass = Math.max((model.stories || []).length, 1) * 500;
  return { mode: p.mode || 1, T: p.T || 0.8, gamma: 1.3, phi_ctrl: 1.0, pf_phi: 1.3,
    alpha: Math.min(Math.max(p[key] || 0.8, 0.5), 1), mass, W: mass * G, control_node: 0 };
}

/** POST /api/results/pushover-performance on the mock. */
export function mockPushoverPerformance(model, results, body = {}) {
  const pd = ((results && results.pushover) || {})[body.case];
  if (!pd) throw new Error(`Unknown pushover case ${JSON.stringify(body.case)}`);
  let rd = pd.roof_disp || [], bs = pd.base_shear || [];
  while (rd.length && rd[0] <= 0) { rd = rd.slice(1); bs = bs.slice(1); }
  const pc = (model.pushover_cases || {})[body.case] || {};
  const opts = "performance" in body ? body.performance : pc.performance;
  const out = poxEvaluate(rd, bs, poxMockModal(model, results, body.case), opts, (model.stories || []).length);
  for (const r of out.summary) {
    if (r.roof_disp == null) { r.step = null; continue; }
    let step = 0, bd = Infinity;
    (pd.roof_disp || []).forEach((u, i) => { const e = Math.abs(u - r.roof_disp); if (e < bd) { bd = e; step = i; } });
    r.step = step;
  }
  return Object.assign(out, { case: body.case, direction: pc.direction || "X", curve: { disp: rd, base_shear: bs } });
}

/** mockResults hook: attach `performance` to pushover cases that carry
    options (the live run() hook). */
export function poxMockAugment(model, results) {
  const po = results && results.pushover;
  if (!po) return results;
  for (const [name, pc] of Object.entries(model.pushover_cases || {})) {
    if (!pc || pc.performance == null || !po[name]) continue;
    try {
      const out = mockPushoverPerformance(model, results, { case: name });
      delete out.case; delete out.direction; delete out.curve;
      po[name].performance = out;
    } catch (e) { po[name].performance = { error: e.message }; }
  }
  return results;
}
