/* SkyFrame — offline (?mock=1) twins of the code spectra, code design
   combinations and live-load reduction endpoints (CONTRACT "Code spectra,
   design combinations, pattern types and live-load reduction"):

     GET  /api/codes/spectra                 → lcMockSpectraCatalogue()
     POST /api/function/spectrum-code/preview → lcMockSpectrum(code, params)
     GET  /api/codes/combos                  → lcMockCombosCatalogue()
     POST /api/combos/code/preview           → lcMockCombos(model, standard, params, includeRs)
     POST /api/live-reduction/factors        → lcMockLlrFactors(model, settings)
   plus the two results tables for js/mock_tables.js (LC_MOCK_TABLES).

   The spectrum and combination formulas are straight ports of
   skyframe/core/spectra_codes.py and combos_codes.py (same numbers); the
   live-load factors use the plan-area / column-count tributary fallback
   (the backend uses the half-bay rule) — plausible, not exact. SI. */

import { LC_SPECTRA_CATALOGUE as lcSpecCat, LC_COMBO_CATALOGUE as lcComboCat } from "./mock_loadcodes_data.js";
import { WVC_SPEC_BUILD as wvcSpecBuild } from "./mock_wavec_spectra.js";   // Wave C hook: TSC / KDS / NSCP / NTC / EC8-NA spectra

export const LC_MPRS_PERIODS = [0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5,
  0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.5, 10.0];
export const LC_PATTERN_KINDS = ["dead", "live", "quake", "wind", "other", "notional", "prestress",
  "superdead", "reducible_live", "roof_live", "snow", "temperature", "pattern_live", "construction",
  "rain", "ice", "move", "shrinkage", "creep", "settlement", "earth_pressure", "hyperstatic"];
export const LC_KIND_LABELS = {
  dead: "Dead", live: "Live", quake: "Seismic", wind: "Wind", other: "Other", notional: "Notional",
  prestress: "Prestress", superdead: "Super Dead", reducible_live: "Reducible Live", roof_live: "Roof Live",
  snow: "Snow", temperature: "Temperature", pattern_live: "Pattern Live", construction: "Construction",
  rain: "Rain", ice: "Ice", move: "Move", shrinkage: "Shrinkage", creep: "Creep", settlement: "Settlement",
  earth_pressure: "Earth Pressure", hyperstatic: "Hyperstatic" };
export const LC_KIND_GROUP = { dead: "G", superdead: "G", live: "L", reducible_live: "L", roof_live: "Lr",
  snow: "S", wind: "W", quake: "E", temperature: "T" };

const clone = o => JSON.parse(JSON.stringify(o));
const fin = v => typeof v === "number" && isFinite(v);

export function lcMockSpectraCatalogue() { return { codes: clone(lcSpecCat), mprs_periods: LC_MPRS_PERIODS.slice() }; }
export function lcMockCombosCatalogue() {
  return { standards: clone(lcComboCat), pattern_kinds: LC_PATTERN_KINDS.slice(),
    kind_labels: { ...LC_KIND_LABELS }, kind_groups: { ...LC_KIND_GROUP } };
}

/* ================================================================ params */
function normalize(cat, keyName, code, params) {
  const c = cat.find(x => x[keyName] === code);
  if (!c) throw new Error(`unknown ${keyName} ${code}`);
  const p = params || {};
  for (const k of Object.keys(p)) if (!c.params.some(s => s.key === k)) throw new Error(`${code}: unknown parameter(s) ['${k}']`);
  const out = {};
  for (const s of c.params) {
    let v = p[s.key] !== undefined ? p[s.key] : s.default;
    if (s.type === "choice") {
      const hit = s.choices.find(([cv]) => String(cv) === String(v));
      if (!hit) throw new Error(`${code}: ${s.key} must be one of ${s.choices.map(x => x[0]).join(", ")}`);
      v = hit[0];
    } else if (s.type === "number") {
      if (v == null) { if (!s.nullable) throw new Error(`${code}: ${s.key} is required`); v = null; }
      else {
        if (!fin(v)) throw new Error(`${code}: ${s.key} must be a finite number`);
        if (s.gt != null && !(v > s.gt)) throw new Error(`${code}: ${s.key} must be > ${s.gt}`);
        if (s.min != null && v < s.min) throw new Error(`${code}: ${s.key} must be >= ${s.min}`);
        if (s.lt != null && !(v < s.lt)) throw new Error(`${code}: ${s.key} must be < ${s.lt}`);
        if (s.max != null && v > s.max) throw new Error(`${code}: ${s.key} must be <= ${s.max}`);
      }
    } else if (s.type === "table") {
      if (!Array.isArray(v) || v.length < 2) throw new Error(`${code}: ${s.key} is required`);
    } else if (s.type === "bool") {
      if (typeof v !== "boolean") throw new Error(`${code}: ${s.key} must be true/false`);
    } else if (s.type === "text") {
      if (typeof v !== "string" || !v.trim() || /["\\]/.test(v)) throw new Error(`${code}: ${s.key} must be a non-empty name without quotes`);
      v = v.trim();
    }
    out[s.key] = v;
  }
  return out;
}

/* ================================================================ spectra */
const FA_SS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5], FV_S1 = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6];
const FA = { A: [0.8, 0.8, 0.8, 0.8, 0.8, 0.8], B: [0.9, 0.9, 0.9, 0.9, 0.9, 0.9], C: [1.3, 1.3, 1.2, 1.2, 1.2, 1.2],
  D: [1.6, 1.4, 1.2, 1.1, 1.0, 1.0], E: [2.4, 1.7, 1.3, 1.3, 1.3, 1.3] };
const FV = { A: [0.8, 0.8, 0.8, 0.8, 0.8, 0.8], B: [0.8, 0.8, 0.8, 0.8, 0.8, 0.8], C: [1.5, 1.5, 1.5, 1.5, 1.5, 1.4],
  D: [2.4, 2.2, 2.0, 1.9, 1.8, 1.7], E: [4.2, 3.3, 2.8, 2.4, 2.2, 2.0] };
const interp = (x, xs, ys) => {
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  for (let i = 0; i < xs.length - 1; i++) if (x >= xs[i] && x <= xs[i + 1]) return ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / (xs[i + 1] - xs[i]);
  return ys[ys.length - 1];
};
const EC8 = { 1: { A: [1.0, 0.15, 0.4, 2.0], B: [1.2, 0.15, 0.5, 2.0], C: [1.15, 0.2, 0.6, 2.0], D: [1.35, 0.2, 0.8, 2.0], E: [1.4, 0.15, 0.5, 2.0] },
  2: { A: [1.0, 0.05, 0.25, 1.2], B: [1.35, 0.05, 0.25, 1.2], C: [1.5, 0.1, 0.25, 1.2], D: [1.8, 0.1, 0.3, 1.2], E: [1.6, 0.05, 0.25, 1.2] } };
const IS_Z = { II: 0.10, III: 0.16, IV: 0.24, V: 0.36 };
const IS_SOIL = { I: [0.40, 1.00, 0.25], II: [0.55, 1.36, 0.34], III: [0.67, 1.67, 0.42] };
const IS_DF = [[0, 3.2], [2, 1.4], [5, 1.0], [7, 0.9], [10, 0.8], [15, 0.7], [20, 0.6], [25, 0.55], [30, 0.5]];
const AS = { Ae: [0.8, 15.5, 2.35, 0.704, 1.056], Be: [1.0, 19.4, 2.94, 0.88, 1.32], Ce: [1.3, 23.8, 3.68, 1.25, 1.874],
  De: [1.1, 25.8, 3.68, 1.98, 2.97], Ee: [1.1, 25.8, 3.68, 3.08, 4.62] };
const GB_AMAX = { 6: [0.04, 0.12, 0.28], 7: [0.08, 0.23, 0.50], 7.5: [0.12, 0.34, 0.72], 8: [0.16, 0.45, 0.90], 8.5: [0.24, 0.68, 1.20], 9: [0.32, 0.90, 1.40] };
const GB_LEVEL = { frequent: 0, fortification: 1, rare: 2 };
const GB_TG = { 1: { I0: 0.20, I1: 0.25, II: 0.35, III: 0.45, IV: 0.65 }, 2: { I0: 0.25, I1: 0.30, II: 0.40, III: 0.55, IV: 0.75 },
  3: { I0: 0.30, I1: 0.35, II: 0.45, III: 0.65, IV: 0.90 } };

function mprsSa(pts, T, TL) {
  if (T <= pts[0][0]) return pts[0][1];
  const [te, se] = pts[pts.length - 1];
  if (T >= te) return TL >= te ? (T <= TL ? se * te / T : se * te * TL / (T * T)) : se * (te / T) ** 2;
  for (let i = 0; i < pts.length - 1; i++) {
    const [t0, s0] = pts[i], [t1, s1] = pts[i + 1];
    if (T >= t0 && T <= t1) return t1 > t0 ? s0 + (s1 - s0) * (T - t0) / (t1 - t0) : s1;
  }
  return se;
}
export function nzsCh(T, site) {
  let c0, pl, tpl, a, tr, n15, n3;
  if (site === "A" || site === "B") [c0, pl, tpl, a, tr, n15, n3] = [1.0, 2.35, 0.3, 1.6, 0.5, 1.05, 3.15];
  else if (site === "C") [c0, pl, tpl, a, tr, n15, n3] = [1.33, 2.93, 0.3, 2.0, 0.5, 1.32, 3.96];
  else if (site === "D") [c0, pl, tpl, a, tr, n15, n3] = [1.12, 3.0, 0.56, 2.4, 0.75, 2.14, 6.42];
  else [c0, pl, tpl, a, tr, n15, n3] = [1.12, 3.0, 1.0, null, null, 3.32, 9.96];
  if (T < 0.1) return c0 + (pl - c0) * T / 0.1;
  if (T <= tpl) return pl;
  if (T <= 1.5) return a == null ? 3.0 / T ** 0.75 : a * (tr / T) ** 0.75;
  if (T <= 3.0) return n15 / T;
  return n3 / (T * T);
}

const BUILD = {
  asce7_16(p) {
    const fa = interp(p.Ss, FA_SS, FA[p.site_class]), fv = interp(p.S1, FV_S1, FV[p.site_class]);
    const sds = 2 / 3 * fa * p.Ss, sd1 = 2 / 3 * fv * p.S1;
    if (!(sds > 0)) throw new Error("SDS must be > 0 (need Ss > 0)");
    const t0 = 0.2 * sd1 / sds, ts = sd1 / sds, TL = p.TL, sc = p.Ie / p.R;
    const f = T => sc * (T <= t0 ? sds * (0.4 + 0.6 * (t0 > 0 ? T / t0 : 0)) : T <= ts ? sds : T <= TL ? sd1 / T : sd1 * TL / (T * T));
    return [f, [0, t0, ts, TL], { SDS: sds, SD1: sd1, SMS: fa * p.Ss, SM1: fv * p.S1, T0: t0, Ts: ts, TL, scale: sc }];
  },
  asce7_22_mprs(p) {
    const raw = p.ordinates.every(fin) ? LC_MPRS_PERIODS.map((t, i) => [t, p.ordinates[i]]) : p.ordinates.map(q => [+q[0], +q[1]]);
    if (raw.length < 2 || raw.some(q => !fin(q[0]) || !fin(q[1]) || q[0] < 0 || q[1] < 0)) throw new Error("asce7_22_mprs: ordinates must be >= 0");
    const f23 = p.basis === "mcer" ? 2 / 3 : 1, pts = raw.map(([t, s]) => [t, f23 * s]), sc = p.Ie / p.R, TL = p.TL;
    const cand = [0.2, 5.0, ...pts.map(q => q[0]).filter(t => t >= 0.2 && t <= 5.0)];
    return [T => sc * mprsSa(pts, T, TL), [...pts.map(q => q[0]), TL],
      { basis: p.basis, SDS: 0.9 * Math.max(...cand.map(t => mprsSa(pts, t, TL))), TL, scale: sc, T_last: pts[pts.length - 1][0], Sa_max: Math.max(...pts.map(q => q[1])) }];
  },
  asce7_22_two_period(p) {
    const { SDS: sds, SD1: sd1, TL } = p, t0 = 0.2 * sd1 / sds, ts = sd1 / sds, sc = p.Ie / p.R;
    const f = T => sc * (T <= t0 ? (t0 > 0 ? sds * (0.4 + 0.6 * T / t0) : sds) : T <= ts ? sds : T <= TL ? sd1 / T : sd1 * TL / (T * T));
    return [f, [0, t0, ts, TL], { SDS: sds, SD1: sd1, T0: t0, Ts: ts, TL, scale: sc }];
  },
  ec8(p) {
    const [S0, TB0, TC0, TD0] = EC8[p.spectrum_type][p.ground_type];
    const S = p.S ?? S0, TB = p.TB ?? TB0, TC = p.TC ?? TC0, TD = p.TD ?? TD0;
    if (!(TB < TC && TC < TD)) throw new Error("ec8: need TB < TC < TD");
    const ag = p.gamma_I * p.agR;
    let f, plateau, extra;
    if (p.kind === "elastic") {
      const eta = Math.max(Math.sqrt(10 / (5 + 100 * p.damping)), 0.55);
      f = T => T <= TB ? ag * S * (1 + T / TB * (eta * 2.5 - 1)) : T <= TC ? ag * S * eta * 2.5 : T <= TD ? ag * S * eta * 2.5 * TC / T : ag * S * eta * 2.5 * TC * TD / (T * T);
      plateau = ag * S * eta * 2.5; extra = { eta };
    } else {
      const { q, beta } = p;
      f = T => T <= TB ? ag * S * (2 / 3 + T / TB * (2.5 / q - 2 / 3)) : T <= TC ? ag * S * 2.5 / q
        : T <= TD ? Math.max(ag * S * 2.5 / q * TC / T, beta * ag) : Math.max(ag * S * 2.5 / q * TC * TD / (T * T), beta * ag);
      plateau = ag * S * 2.5 / q; extra = { q, beta };
    }
    const corners = [0, TB, TC, TD];
    if (p.kind === "design") { let tf = 2.5 * S * TC / (p.q * p.beta); if (tf > TD) tf = Math.sqrt(2.5 * S * TC * TD / (p.q * p.beta)); corners.push(tf); }
    return [f, corners, { ag, S, TB, TC, TD, plateau, ...extra }];
  },
  is1893_2016(p) {
    const Z = p.Z ?? IS_Z[p.zone], x = 100 * p.damping;
    const df = x <= 0 ? 3.2 : x >= 30 ? 0.5 : interp(x, IS_DF.map(r => r[0]), IS_DF.map(r => r[1]));
    const [tc, num, tail] = IS_SOIL[p.soil], sc = p.output === "sa_g" ? 1 : Z / 2 * p.I / p.R;
    const sag = T => T < 0.1 ? 1 + 15 * T : T <= tc ? 2.5 : T <= 4 ? num / T : tail;
    return [T => sc * df * sag(T), [0, 0.1, tc, 4], { Z, damping_factor: df, Tc: tc, scale: sc, plateau: sc * df * 2.5 }];
  },
  nbcc_2020(p) {
    const keys = [[0.2, "Sa02"], [0.5, "Sa05"], [1.0, "Sa10"], [2.0, "Sa20"], [5.0, "Sa50"], [10.0, "Sa100"]];
    const ords = [];
    for (const [t, k] of keys) { if (p[k] == null) break; ords.push([t, p[k]]); }
    if (ords.length < 4) throw new Error("nbcc_2020: give Sa02, Sa05, Sa10, Sa20 (and optionally Sa50, then Sa100)");
    const s02 = Math.max(ords[0][1], ords[1][1]), pts = [[ords[0][0], s02], ...ords.slice(1)], sc = p.Mv * p.Ie / p.RdRo;
    const f = T => sc * (T <= pts[0][0] ? s02 : T >= pts[pts.length - 1][0] ? pts[pts.length - 1][1] : interp(T, pts.map(q => q[0]), pts.map(q => q[1])));
    return [f, ords.map(q => q[0]), { S_plateau: s02, scale: sc }];
  },
  nzs1170_5(p) {
    const site = p.site_class, ZRN = p.Z * p.R * p.N;
    if (p.Z * p.R > 0.7 + 1e-12) throw new Error("nzs1170_5: Z*R must not exceed 0.7 (§3.1.6)");
    const summ = { ZRN };
    let sc = 1, floor = 0;
    if (p.kind === "design") {
      const mu = p.mu, sp = p.Sp ?? (mu <= 1 ? 1 : mu < 2 ? 1.3 - 0.3 * mu : 0.7), T1 = p.T1 ?? 10;
      const kmu = site === "E" ? ((T1 >= 1 || mu < 1.5) ? mu : (mu - 1.5) * T1 + 1.5) : (T1 >= 0.7 ? mu : (mu - 1) * T1 / 0.7 + 1);
      sc = sp / kmu; floor = Math.max((p.Z / 20 + 0.02) * p.R, 0.03 * p.R);
      Object.assign(summ, { Sp: sp, k_mu: kmu, floor });
    }
    const f = T => Math.max(ZRN * nzsCh(T, site) * sc, floor);
    summ.plateau = f(0.1);
    return [f, [0, 0.1, { A: 0.3, B: 0.3, C: 0.3, D: 0.56, E: 1.0 }[site], 1.5, 3.0], summ];
  },
  as1170_4(p) {
    const [c0, slope, cap, kmid, klong] = AS[p.site_class], kpz = p.kp * p.Z, used = Math.max(kpz, 0.08);
    const sc = p.kind === "elastic" ? 1 : p.Sp / p.mu;
    const ch = T => T <= 0.1 ? c0 + slope * T : T <= 1.5 ? Math.min(kmid / T, cap) : klong / (T * T);
    return [T => used * ch(T) * sc, [0, 0.1, kmid / cap, 1.5], { kpZ: kpz, kpZ_used: used, scale: sc, plateau: used * cap * sc }];
  },
  gb50011(p) {
    const amax = p.alpha_max ?? GB_AMAX[p.intensity][GB_LEVEL[p.level]];
    let Tg = GB_TG[p.group][p.site];
    if (p.level === "rare" && ["8", "8.5", "9"].includes(String(p.intensity))) Tg += 0.05;
    if (p.Tg != null) Tg = p.Tg;
    const z = p.damping, gamma = 0.9 + (0.05 - z) / (0.3 + 6 * z), eta1 = Math.max(0.02 + (0.05 - z) / (4 + 32 * z), 0), eta2 = Math.max(1 + (0.05 - z) / (0.08 + 1.6 * z), 0.55);
    const f = T => T < 0.1 ? amax * (0.45 + (eta2 - 0.45) * T / 0.1) : T <= Tg ? eta2 * amax : T <= 5 * Tg ? (Tg / T) ** gamma * eta2 * amax
      : Math.max(eta2 * 0.2 ** gamma - eta1 * (T - 5 * Tg), 0) * amax;
    return [f, [0, 0.1, Tg, 5 * Tg, 6.0], { alpha_max: amax, Tg, gamma, eta1, eta2, plateau: eta2 * amax }];
  },
};

Object.assign(BUILD, wvcSpecBuild);   // Wave C hook (js/mock_wavec_spectra.js)

function sample(fn, corners, tMax) {
  const ts = new Set();
  const r = t => Math.round(t * 1e10) / 1e10;
  const grid = (a, b, h) => { const n = Math.round((b - a) / h); for (let i = 0; i <= n; i++) { const t = r(a + i * h); if (t <= tMax + 1e-12) ts.add(t); } };
  grid(0, Math.min(1, tMax), 0.02);
  if (tMax > 1) grid(1, Math.min(4, tMax), 0.05);
  if (tMax > 4) grid(4, tMax, 0.25);
  for (const c of corners) if (c >= 0 && c <= tMax + 1e-12) ts.add(r(c));
  ts.add(r(tMax));
  return [...ts].sort((a, b) => a - b).map(t => [t, fn(t)]);
}

/** Mock POST /api/function/spectrum-code/preview. */
export function lcMockSpectrum(code, params) {
  const p = normalize(lcSpecCat, "code", code, params);
  const meta = lcSpecCat.find(c => c.code === code);
  const [fn, corners, summary] = BUILD[code](p);
  return { code, label: meta.label, clause: meta.clause, params: p, points: sample(fn, corners, p.T_max), summary };
}

/* ================================================================ combos */
export function lcMockClassify(model, includeRs = true) {
  const out = { G: [], L: [], Lr: [], S: [], W: [], E: [], T: [], warnings: [] };
  for (const [cname, c] of Object.entries(model.cases || {})) {
    const groups = new Set(Object.keys(c.patterns || {}).map(p => {
      const pat = (model.patterns || {})[p];
      return pat ? (LC_KIND_GROUP[pat.kind] || null) : null;
    }));
    if (!groups.size || groups.has(null)) continue;
    if (groups.size > 1) { out.warnings.push(`case '${cname}' mixes pattern types; not used by the generator`); continue; }
    const g = [...groups][0];
    if (["G", "L", "Lr", "S"].includes(g)) out[g].push(cname); else out[g].push([cname, g !== "T"]);
  }
  if (includeRs) for (const n of Object.keys(model.rs_cases || {})) out.E.push([n, false]);
  return out;
}
const ALT = ["W", "E", "T"];
function choices(cls, g, leading) {
  const ch = [];
  for (const [c, signed] of cls[g]) { ch.push([c, 1]); if (signed) ch.push([c, -1]); }
  if (!leading && ch.length && !cls[g].some(x => x[1])) ch.push([null, 0]);
  return ch;
}
function expand(cls, terms, lead) {
  if (lead != null && !cls[lead].length) return [];
  const t = Object.fromEntries(Object.entries(terms).filter(([g, f]) => f !== 0 && cls[g].length));
  const alt = Object.keys(t).filter(g => ALT.includes(g));
  const opts = alt.map(g => choices(cls, g, g === lead));
  const prod = opts.reduce((acc, o) => acc.flatMap(a => o.map(x => [...a, x])), [[]]);
  const res = [];
  for (const pick of prod) {
    const fac = {}, parts = [], signed = [];
    for (const [g, f] of Object.entries(t)) {
      if (ALT.includes(g)) continue;
      for (const c of cls[g]) fac[c] = (fac[c] || 0) + f;
      parts.push(`${+f.toPrecision(12)}${g}`);
    }
    alt.forEach((g, i) => {
      const [c, s] = pick[i];
      if (c == null) return;
      const f = t[g] * s;
      fac[c] = (fac[c] || 0) + f;
      signed.push(`${f < 0 ? "-" : "+"}${+Math.abs(f).toPrecision(12)}${c}`);
    });
    if (!Object.keys(fac).length) continue;
    for (const k of Object.keys(fac)) fac[k] = Math.round(fac[k] * 1e10) / 1e10;
    res.push([fac, (parts.join(" + ") + (signed.length ? " " + signed.join(" ") : "")).trim()]);
  }
  return res;
}
function collect(cls, templates, prefix) {
  const out = [], seen = new Set();
  for (const [terms, lead] of templates) for (const [fac, desc] of expand(cls, terms, lead)) {
    const key = JSON.stringify(Object.entries(fac).sort((a, b) => a[0] < b[0] ? -1 : 1));
    if (seen.has(key)) continue;
    seen.add(key); out.push({ factors: fac, desc });
  }
  out.forEach((c, i) => { c.name = `${prefix}${String(i + 1).padStart(2, "0")}`; });
  return out;
}
const EN_LIVE = { A: [0.7, 0.5, 0.3], B: [0.7, 0.5, 0.3], C: [0.7, 0.7, 0.6], D: [0.7, 0.7, 0.6], E: [1.0, 0.9, 0.8], F: [0.7, 0.7, 0.6], G: [0.7, 0.5, 0.3], H: [0, 0, 0] };
const AS_PSI = { residential_office: [0.7, 0.4, 0.4, 0.3], parking: [0.7, 0.4, 0.4, 0.3], retail: [0.7, 0.4, 0.4, 0.3],
  storage: [1.0, 0.6, 0.6, 0.6], other: [1.0, 0.6, 0.6, 0.6], roof_other: [0.7, 0, 0, 0] };
const GEN = {
  en1990(cls, p) {
    const psi = { L: EN_LIVE[p.live_category], Lr: EN_LIVE.H, S: p.snow_site === "high" ? [0.7, 0.5, 0.2] : [0.5, 0.2, 0], W: [0.6, 0.2, 0], T: [0.6, 0.5, 0], ...(p.psi || {}) };
    const { gamma_G_sup: gs, gamma_G_inf: gi, gamma_Q: gq, xi } = p, V = ["L", "Lr", "S", "W", "T"];
    const acc = (lead, k) => { const d = {}; for (const g of V) { if (g === lead) continue; if ([lead, g].includes("Lr") && [lead, g].some(x => x === "S" || x === "W")) continue; d[g] = psi[g][k]; } return d; };
    const sc = (o, f) => Object.fromEntries(Object.entries(o).map(([g, v]) => [g, f * v]));
    const uls = [[{ G: gs }, null]];
    if (p.equation === "6.10") {
      for (const q of V) { uls.push([{ G: gs, [q]: gq, ...sc(acc(q, 0), gq) }, q]); if (q === "W") uls.push([{ G: gi, W: gq }, "W"]); }
    } else {
      const a = { G: gs }; for (const g of V) if (!(g === "Lr" && (cls.S.length || cls.W.length))) a[g] = gq * psi[g][0];
      uls.push([a, null]);
      for (const q of V) { uls.push([{ G: xi * gs, [q]: gq, ...sc(acc(q, 0), gq) }, q]); if (q === "W") uls.push([{ G: gi, W: gq }, "W"]); }
    }
    let out = collect(cls, uls, p.prefix + "ULS");
    if (p.seismic && cls.E.length) out = out.concat(collect(cls, [[{ G: 1, E: 1, ...Object.fromEntries(V.map(g => [g, psi[g][2]])) }, "E"]], p.prefix + "SEIS"));
    if (p.sls) {
      const ch = [[{ G: 1 }, null]], fr = [[{ G: 1 }, null]];
      for (const q of V) { ch.push([{ G: 1, [q]: 1, ...acc(q, 0) }, q]); fr.push([{ G: 1, [q]: psi[q][1], ...acc(q, 2) }, q]); }
      out = out.concat(collect(cls, ch, p.prefix + "SLSC"), collect(cls, fr, p.prefix + "SLSF"),
        collect(cls, [[{ G: 1, ...Object.fromEntries(V.map(g => [g, psi[g][2]])) }, null]], p.prefix + "SLSQ"));
    }
    return out;
  },
  is456(cls, p) {
    const IL = ["L", "Lr", "S"].filter(g => cls[g].length);
    const t = (gf, ilf, x, xf, lead) => [{ G: gf, ...Object.fromEntries(IL.map(g => [g, ilf])), ...(x ? { [x]: xf } : {}) }, lead ?? null];
    const tpl = [t(1.5, 1.5)];
    for (const X of ["W", "E"]) tpl.push(t(1.2, 1.2, X, 1.2, X), t(1.5, 0, X, 1.5, X), t(0.9, 0, X, 1.5, X));
    let out = collect(cls, tpl, p.prefix);
    if (p.sls) { const s = [t(1, 1)]; for (const X of ["W", "E"]) s.push(t(1, 0, X, 1, X), t(1, 0.8, X, 0.8, X)); out = out.concat(collect(cls, s, p.prefix + "SLS")); }
    return out;
  },
  is800(cls, p) {
    const lives = ["L", "Lr", "S"].filter(g => cls[g].length), lead = lives[0], acc = lives.slice(1);
    const t = (gf, lf, af, x, xf) => [{ G: gf, ...(lead ? { [lead]: lf } : {}), ...Object.fromEntries(acc.map(g => [g, af])), ...(x ? { [x]: xf } : {}) }, x ?? null];
    let tpl = [t(1.5, 1.5, 1.05)];
    for (const X of ["W", "E"]) tpl.push(t(1.2, 1.2, 1.05, X, 0.6), t(1.2, 1.2, 0.53, X, 1.2), t(1.5, 0, 0, X, 1.5), t(0.9, 0, 0, X, 1.5));
    let out = collect(cls, tpl, p.prefix);
    if (p.sls) { const s = [t(1, 1, 1)]; for (const X of ["W", "E"]) s.push(t(1, 0.8, 0.8, X, 0.8), t(1, 0, 0, X, 1)); out = out.concat(collect(cls, s, p.prefix + "SLS")); }
    return out;
  },
  nbcc2020(cls, p) {
    const lc = p.storage ? 1 : 0.5, lt = f => ({ L: f, Lr: f }), tpl = [[{ G: 1.4 }, null]];
    const comp = (base, comps, lead) => {
      const present = comps.filter(c => Object.keys(c).some(g => cls[g].length));
      if (!present.length) tpl.push([base, lead]);
      for (const c of present) tpl.push([{ ...base, ...c }, lead]);
    };
    for (const d of [1.25, 0.9]) if (cls.L.length || cls.Lr.length) comp({ G: d, ...lt(1.5) }, [{ S: 1 }, { W: 0.4 }], null);
    for (const d of [1.25, 0.9]) comp({ G: d, S: 1.5 }, [lt(1), { W: 0.4 }], "S");
    for (const d of [1.25, 0.9]) comp({ G: d, W: 1.4 }, [lt(lc), { S: 0.5 }], "W");
    tpl.push([{ G: 1, E: 1, ...lt(lc), S: 0.25 }, "E"]);
    return collect(cls, tpl, p.prefix);
  },
  asnzs1170(cls, p) {
    let [ps, pl, pc, pe] = AS_PSI[p.occupancy];
    const [rs, rl, rc, re] = AS_PSI.roof_other;
    if (p.psi_c != null) pc = p.psi_c;
    if (p.psi_E != null) pe = p.psi_E;
    const tpl = [[{ G: 1.35 }, null], [{ G: 1.2, L: 1.5, Lr: 1.5 }, cls.L.length ? "L" : "Lr"],
      [{ G: 1.2, W: 1, L: pc, Lr: rc }, "W"], [{ G: 0.9, W: 1 }, "W"], [{ G: 1, E: 1, L: pe, Lr: re }, "E"], [{ G: 1.2, S: 1, L: pc, Lr: rc }, "S"]];
    let out = collect(cls, tpl, p.prefix);
    if (p.sls) out = out.concat(collect(cls, [[{ G: 1, L: ps, Lr: rs }, null], [{ G: 1, L: pl, Lr: rl }, null]], p.prefix + "SLS"));
    return out;
  },
};

/** Mock POST /api/combos/code/preview → {combos, warnings}. */
export function lcMockCombos(model, standard, params, includeRs = true) {
  const p = normalize(lcComboCat, "standard", standard, params);
  const cls = lcMockClassify(model, includeRs);
  const warnings = cls.warnings.map(w => `${standard} combinations: ${w}`);
  if (!cls.G.length) return { combos: [], warnings: [...warnings, `${standard} combinations: no dead / superdead case found; no combinations generated`] };
  if (cls.T.length && standard !== "en1990") { warnings.push(`${standard} combinations: temperature cases are not in the code table and are not combined`); cls.T = []; }
  return { combos: GEN[standard](cls, p), warnings };
}

/** Write generated combos into a model (draft): remove unreferenced
    `<prefix><digits>` combos first (backend `replace=True`), then add. */
export function lcApplyCombos(model, combos, prefix) {
  model.combos = model.combos || {};
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`^${esc(prefix)}(ULS|SEIS|SLSC|SLSF|SLSQ|SLS)?\\d{2,}$`);
  for (const n of Object.keys(model.combos)) {
    if (!re.test(n)) continue;
    const used = Object.entries(model.combos).some(([k, cb]) => k !== n && cb && cb.cases && n in cb.cases);
    if (!used) delete model.combos[n];
  }
  for (const c of combos) model.combos[c.name] = { name: c.name, cases: { ...c.factors }, combo_type: "add" };
}

/* ================================================================ LLR */
const LLR_DEFAULTS = { asce7_16: { KLL: 4.0 }, nbcc2020: { occupancy: "general" }, ec1: { psi0: 0.7, category: "B", method: "alpha_n", A0: 10.0 },
  is875: {}, user: { basis: "area", curve: [[0.0, 1.0]] } };
export const LC_LLR_DEFAULTS = LLR_DEFAULTS;
export function lcLlrFactor(code, p, At, n) {
  n = Math.max(Math.round(n), 1);
  if (code === "asce7_16") { const k = (p.KLL ?? 4) * At, R = k > 0 ? 0.25 + 4.57 / Math.sqrt(k) : 1; return Math.min(1, Math.max(n <= 1 ? 0.5 : 0.4, R)); }
  if (code === "nbcc2020") return p.occupancy === "assembly" ? (At > 80 ? Math.min(1, 0.5 + Math.sqrt(20 / At)) : 1) : (At > 20 ? Math.min(1, 0.3 + Math.sqrt(9.8 / At)) : 1);
  if (code === "ec1") {
    const psi0 = p.psi0 ?? 0.7, A1 = At / n;
    let aA = A1 > 0 ? Math.min(1, 5 / 7 * psi0 + (p.A0 ?? 10) / A1) : 1;
    if (["C", "D"].includes(p.category)) aA = Math.max(aA, 0.6);
    const an = n > 2 ? (2 + (n - 2) * psi0) / n : 1;
    return p.method === "alpha_A" ? aA : p.method === "min" ? Math.min(an, aA) : an;
  }
  if (code === "is875") return n <= 1 ? 1 : n === 2 ? 0.9 : n === 3 ? 0.8 : n === 4 ? 0.7 : n <= 10 ? 0.6 : 0.5;
  const cv = p.curve || [[0, 1]], x = p.basis === "floors" ? n : At;
  return interp(x, cv.map(q => q[0]), cv.map(q => q[1]));
}
/** Mock POST /api/live-reduction/factors (plan-area / column-count tributary). */
export function lcMockLlrFactors(model, settings) {
  const st = settings || {}, code = st.code || "asce7_16", p = { ...LLR_DEFAULTS[code], ...(st.params || {}) };
  const xs = [], ys = [];
  for (const m of model.members || []) for (const q of [m.pi, m.pj]) { xs.push(q[0]); ys.push(q[1]); }
  const area = xs.length ? (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys)) : 0;
  const cols = (model.members || []).filter(m => m.kind === "column");
  const elev = (model.stories || []).map(s => s.elevation);
  const out = { code, columns: {}, piers: {}, warnings: ["offline mock: tributary area = plan area / columns at the level"] };
  for (const c of cols) {
    const zt = Math.max(c.pi[2], c.pj[2]);
    const n = Math.max(elev.filter(e => e >= zt - 1e-6).length, 1);
    const nAt = cols.filter(k => Math.abs(Math.max(k.pi[2], k.pj[2]) - zt) < 1e-6).length || 1;
    const A1 = area / nAt, At = A1 * n;
    out.columns[c.uid] = { At, A1, n, R: lcLlrFactor(code, p, At, n) };
  }
  const kinds = st.kinds && st.kinds.length ? st.kinds : ["reducible_live"];
  const pick = ks => Object.entries(model.cases || {}).filter(([, c]) => {
    const ps = Object.keys(c.patterns || {}).map(n => (model.patterns || {})[n]);
    return ps.length && ps.every(x => x && ks.includes(x.kind));
  }).map(([n]) => n);
  let live = pick(kinds);
  if (!live.length && !st.kinds) { live = pick(["live"]); if (live.length) out.warnings.push(`no Reducible Live load patterns: the Live-type cases ${JSON.stringify(live)} are reduced instead`); }
  out.live_cases = live;
  return out;
}

/* ================================================================ mock tables */
const C = (key, label, quantity) => ({ key, label, quantity });
const G_LLR = "Frame Output > Live Load Reduction";
export const LC_MOCK_TABLES = {
  catalogue: [
    ["live_load_reduction_factors", "Live Load Reduction Factors", G_LLR,
      [C("story", "Story", "text"), C("element", "Element", "text"), C("name", "Unique Name", "text"), C("code", "Code", "text"),
        C("at", "Tributary Area", "area"), C("n", "Floors", "id"), C("rll", "RLL Factor", "ratio")]],
    ["live_reduced_forces", "Column And Pier Forces (Live Load Reduced)", G_LLR,
      [C("story", "Story", "text"), C("element", "Element", "text"), C("name", "Unique Name", "text"), C("case", "Output Case", "text"),
        C("case_type", "Case Type", "text"), C("station", "Station", "length"), C("rll", "RLL Factor", "ratio"),
        C("P", "P", "force"), C("V2", "V2", "force"), C("V3", "V3", "force"), C("T", "T", "moment"), C("M2", "M2", "moment"), C("M3", "M3", "moment")]],
  ],
  compute: {
    live_load_reduction_factors(model) {
      const f = lcMockLlrFactors(model, model.llr_settings);
      return Object.entries(f.columns).map(([uid, i]) => {
        const m = model.members.find(x => x.uid === uid) || {};
        return { story: m.story || "", element: "Column", name: uid, code: f.code, at: i.At, n: i.n, rll: i.R };
      });
    },
    live_reduced_forces(model, results, filt) {
      const f = lcMockLlrFactors(model, model.llr_settings), rows = [];
      const live = f.live_cases.filter(c => results.cases && results.cases[c]);
      const srcs = [...Object.entries(results.cases || {}).map(([n, b]) => [n, "LinStatic", b, live.includes(n) ? { [n]: 1 } : {}]),
        ...Object.entries(results.combos || {}).filter(([n]) => ((model.combos || {})[n] || {}).combo_type !== "envelope")
          .map(([n, b]) => [n, "Combination", b, Object.fromEntries(Object.entries(((model.combos || {})[n] || {}).cases || {}).filter(([c]) => live.includes(c)))])];
      for (const [name, ctype, blk, lf] of srcs) {
        if (filt && !filt.has(name)) continue;
        if (!Object.keys(lf).length) continue;
        for (const [uid, i] of Object.entries(f.columns)) {
          const st = (blk.member_stations || {})[uid];
          if (!st || !st.x) continue;
          const m = model.members.find(x => x.uid === uid) || {};
          for (const idx of [0, st.x.length - 1]) {
            const red = k => { let v = st[k][idx]; for (const [lc, fac] of Object.entries(lf)) { const l = ((results.cases[lc].member_stations || {})[uid] || {})[k]; if (l) v += (i.R - 1) * fac * l[idx]; } return v; };
            rows.push({ story: m.story || "", element: "Column", name: uid, case: name, case_type: ctype, station: st.x[idx], rll: i.R,
              P: red("N"), V2: red("V2"), V3: red("V3"), T: red("T"), M2: red("M2"), M3: red("M3") });
          }
        }
      }
      return rows;
    },
  },
};
