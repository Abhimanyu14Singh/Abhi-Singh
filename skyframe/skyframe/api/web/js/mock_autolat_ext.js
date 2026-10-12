/* SkyFrame — v1.19 mock mirror of skyframe.core.seismic_codes_ext /
   wind_codes_ext (CONTRACT "Auto lateral loads: international seismic and
   wind codes, ASCE 7 wind load cases").  ?mock=1 has no backend, so this
   ports the same formulas / constants / summary shape; js/mock_loads116.js
   registers them with a few hook lines.  Everything SI. */

const G = 9.80665;
const FT = 0.3048, MPH = 0.44704;
const isNum = v => typeof v === "number" && isFinite(v);
function pos(label, v, allowZero = false) {
  if (!isNum(v) || v < 0 || (v === 0 && !allowZero))
    throw new Error(`${label} must be a finite value ${allowZero ? ">= 0" : "> 0"}`);
  return +v;
}
function num(label, v) { if (!isNum(v)) throw new Error(`${label} must be a finite number`); return +v; }
function choice(label, v, allowed) {
  if (!allowed.map(String).includes(String(v))) throw new Error(`${label} must be one of ${allowed.join(", ")}`);
  return v;
}
function common(model, direction) {
  if (direction !== "X" && direction !== "Y") throw new Error(`direction must be X|Y, got '${direction}'`);
  const st = model.stories || [];
  if (!st.length) throw new Error("model has no stories to load");
  const hn = Math.max(...st.map(s => s.elevation));
  if (!(hn > 0)) throw new Error("building height (top-story elevation) must be > 0");
  return hn;
}
function weightsOf(model, weights) {
  const st = model.stories || [];
  if (weights != null) return Object.fromEntries(st.map(s => [s.name, +(weights[s.name] || 0)]));
  const sm = model.story_masses || {};
  return Object.fromEntries(st.map(s => [s.name, (+sm[s.name] || 0) * G]));
}
function interp(x, xs, ys) {
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  for (let i = 0; i + 1 < xs.length; i++)
    if (xs[i] <= x && x <= xs[i + 1]) return ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / (xs[i + 1] - xs[i]);
  return ys[ys.length - 1];
}
const kExp = T => T <= 0.5 ? 1 : T >= 2.5 ? 2 : 1 + (T - 0.5) / 2;
function distribute(model, V, w, shape) {
  const den = model.stories.reduce((a, s) => a + w[s.name] * shape[s.name], 0);
  return model.stories.map(s => ({ story: s.name, h: s.elevation, w: w[s.name],
    F: den > 0 ? V * w[s.name] * shape[s.name] / den : 0 }));
}
function topForce(model, V, w, Ft) {
  const rows = distribute(model, V - Ft, w, Object.fromEntries(model.stories.map(s => [s.name, s.elevation])));
  const top = model.stories.reduce((a, s) => s.elevation > a.elevation ? s : a).name;
  for (const r of rows) if (r.story === top) r.F += Ft;
  return rows;
}
const summary = (code, direction, T, W, V, stories, extra = {}) => ({ code, direction, T, W, V, ...extra, stories });
const sumW = w => Object.values(w).reduce((a, b) => a + b, 0);
function planExtents(model) {
  const xs = [], ys = [];
  for (const m of model.members || []) for (const p of [m.pi, m.pj]) { xs.push(+p[0]); ys.push(+p[1]); }
  if (!xs.length) return [0, 0];
  return [Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)];
}
const KT_AUS = { steel_mrf: 0.11, concrete_mrf: 0.075, ebf: 0.06, other: 0.05 };
function empT(hn, structure, table, factor = 1, expo = 0.75) {
  choice("structure", structure, Object.keys(table));
  return factor * table[structure] * hn ** expo;
}

/* ---------------- seismic */
export function nzsCh(T, soil) {
  T = Math.max(T, 0.1);
  if (soil === "A" || soil === "B") return T < 0.3 ? 2.35 : T < 1.5 ? 1.6 * (0.5 / T) ** 0.75 : T < 3 ? 1.05 / T : 3.15 / T ** 2;
  if (soil === "C") return T < 0.3 ? 2.93 : T < 1.5 ? 2.0 * (0.5 / T) ** 0.75 : T < 3 ? 1.32 / T : 3.96 / T ** 2;
  if (soil === "D") return T < 0.56 ? 3.0 : T < 1.5 ? 2.4 * (0.75 / T) ** 0.75 : T < 3 ? 2.14 / T : 6.42 / T ** 2;
  return T < 1 ? 3.0 : T < 1.5 ? 3.0 / T ** 0.75 : T < 3 ? 3.32 / T : 9.96 / T ** 2;
}
function nzs(model, { Z, mu = 4, soil = "C", R = 1, N = 1, Sp = null, T = null, structure = "concrete_mrf", weights = null, direction = "X" }) {
  const hn = common(model, direction);
  pos("Z", Z); pos("mu", mu); pos("R", R); pos("N", N);
  choice("soil", soil, ["A", "B", "C", "D", "E"]);
  const Tu = T == null ? empT(hn, structure, KT_AUS, 1.25) : pos("T", T);
  const sp = Sp == null ? (mu >= 2 ? 0.7 : mu <= 1 ? 1 : 1.3 - 0.3 * mu) : pos("Sp", Sp);
  const Ch = nzsCh(Tu, soil), C = Ch * Math.min(Z * R, 0.7) * N;
  const T1 = Math.max(Tu, 0.4);
  const kmu = soil === "E" ? (T1 >= 1 || mu < 1.5 ? mu : (mu - 1.5) * T1 + 1.5) : (T1 >= 0.7 ? mu : (mu - 1) * T1 / 0.7 + 1);
  const CdMin = Math.max((Z / 20 + 0.02) * R, 0.03 * R);
  const Cd = Math.max(C * sp / kmu, CdMin);
  const w = weightsOf(model, weights), W = sumW(w), V = Cd * W;
  return summary("nzs1170_5", direction, Tu, W, V, topForce(model, V, w, 0.08 * V), { Ch, C, Sp: sp, kmu, Cd, Cd_min: CdMin, Ft: 0.08 * V });
}
const AS_CH = { Ae: [2.35, 0.704, 1.056], Be: [2.94, 0.88, 1.32], Ce: [3.68, 1.25, 1.874], De: [3.68, 1.98, 2.97], Ee: [3.68, 3.08, 4.62] };
function as4(model, { Z, kp = 1, site = "Ce", mu = 2, Sp = 0.77, T = null, structure = "concrete_mrf", kpZ_min = 0.08, weights = null, direction = "X" }) {
  const hn = common(model, direction);
  pos("Z", Z); pos("kp", kp); pos("mu", mu); pos("Sp", Sp);
  choice("site", site, Object.keys(AS_CH));
  const Tu = T == null ? empT(hn, structure, KT_AUS, 1.25) : pos("T", T);
  const [plat, n1, n2] = AS_CH[site];
  const Ch = Tu <= 1.5 ? (Tu > 0.1 ? Math.min(plat, n1 / Tu) : plat) : n2 / Tu ** 2;
  const kpZ = Math.max(kp * Z, kpZ_min);
  const Cs = Math.max(kpZ * Ch * Sp / mu, 0.01);
  const w = weightsOf(model, weights), W = sumW(w), V = Cs * W, k = kExp(Tu);
  return summary("as1170_4", direction, Tu, W, V, distribute(model, V, w, Object.fromEntries(model.stories.map(s => [s.name, s.elevation ** k]))), { k, Ch, kpZ, Cs });
}
const GB_AMAX = { "6": [0.04, 0.28], "7": [0.08, 0.50], "7.5": [0.12, 0.72], "8": [0.16, 0.90], "8.5": [0.24, 1.20], "9": [0.32, 1.40] };
const GB_TG = { 1: [0.20, 0.25, 0.35, 0.45, 0.65], 2: [0.25, 0.30, 0.40, 0.55, 0.75], 3: [0.30, 0.35, 0.45, 0.65, 0.90] };
const GB_SITES = ["I0", "I1", "II", "III", "IV"];
function gb11(model, { T, intensity = "8", level = "frequent", site = "II", group = 1, alpha_max = null, Tg = null, zeta = 0.05, geq_factor = null, top_force = true, weights = null, direction = "X" }) {
  common(model, direction);
  const Tu = pos("T", T);
  choice("level", level, ["frequent", "rare"]);
  const am = alpha_max == null ? (choice("intensity", intensity, Object.keys(GB_AMAX)), GB_AMAX[String(intensity)][level === "frequent" ? 0 : 1]) : pos("alpha_max", alpha_max);
  let tg = Tg;
  if (tg == null) { choice("site", site, GB_SITES); tg = GB_TG[group][GB_SITES.indexOf(site)] + (level === "rare" ? 0.05 : 0); }
  const gamma = 0.9 + (0.05 - zeta) / (0.3 + 6 * zeta);
  const eta1 = Math.max(0.02 + (0.05 - zeta) / (4 + 32 * zeta), 0);
  const eta2 = Math.max(1 + (0.05 - zeta) / (0.08 + 1.6 * zeta), 0.55);
  const a1 = Tu < 0.1 ? (0.45 + 10 * (eta2 - 0.45) * Tu) * am : Tu <= tg ? eta2 * am
    : Tu <= 5 * tg ? (tg / Tu) ** gamma * eta2 * am : (eta2 * 0.2 ** gamma - eta1 * (Tu - 5 * tg)) * am;
  const gf = geq_factor == null ? (model.stories.length > 1 ? 0.85 : 1) : geq_factor;
  const w = weightsOf(model, weights), GE = sumW(w), FEk = a1 * gf * GE;
  const dn = !top_force || Tu <= 1.4 * tg ? 0 : tg <= 0.35 ? 0.08 * Tu + 0.07 : tg <= 0.55 ? 0.08 * Tu + 0.01 : 0.08 * Tu - 0.02;
  return summary("gb50011", direction, Tu, GE, FEk, topForce(model, FEk, w, dn * FEk), { alpha1: a1, alpha_max: am, Tg: tg, Geq: gf * GE, delta_n: dn, eta1, eta2, gamma });
}
const TSC_SS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5], TSC_S1 = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6];
const TSC_FS = { ZA: Array(6).fill(0.8), ZB: Array(6).fill(0.9), ZC: [1.3, 1.3, 1.2, 1.2, 1.2, 1.2], ZD: [1.6, 1.4, 1.2, 1.1, 1.0, 1.0], ZE: [2.4, 1.7, 1.3, 1.1, 0.9, 0.8] };
const TSC_F1 = { ZA: Array(6).fill(0.8), ZB: Array(6).fill(0.8), ZC: [1.5, 1.5, 1.5, 1.5, 1.5, 1.4], ZD: [2.4, 2.2, 2.0, 1.9, 1.8, 1.7], ZE: [4.2, 3.3, 2.8, 2.4, 2.2, 2.0] };
const TSC_CT = { concrete_mrf: 0.1, steel_mrf: 0.08, ebf: 0.07, other: 0.05 };
function tsc(model, { SS, S1, R, D, I = 1, site = "ZC", FS = null, F1 = null, T = null, structure = "concrete_mrf", TL = 6, weights = null, direction = "X" }) {
  const hn = common(model, direction);
  for (const [l, v] of [["SS", SS], ["S1", S1], ["R", R], ["D", D], ["I", I], ["TL", TL]]) pos(l, v);
  choice("site", site, Object.keys(TSC_FS));
  const fs = FS == null ? interp(SS, TSC_SS, TSC_FS[site]) : pos("FS", FS);
  const f1 = F1 == null ? interp(S1, TSC_S1, TSC_F1[site]) : pos("F1", F1);
  const SDS = SS * fs, SD1 = S1 * f1, TA = 0.2 * SD1 / SDS, TB = SD1 / SDS;
  const TpA = empT(hn, structure, TSC_CT);
  const Tu = T == null ? TpA : Math.min(pos("T", T), 1.4 * TpA);
  const Sae = Tu < TA ? (0.4 + 0.6 * Tu / TA) * SDS : Tu <= TB ? SDS : Tu <= TL ? SD1 / Tu : SD1 * TL / Tu ** 2;
  const Ra = Tu > TB ? R / I : D + (R / I - D) * Tu / TB;
  const w = weightsOf(model, weights), W = sumW(w);
  const V = Math.max(W * Sae / Ra, 0.04 * W * I * SDS), dFN = 0.0075 * model.stories.length * V;
  return summary("tsc2018", direction, Tu, W, V, topForce(model, V, w, dFN), { TpA, SDS, SD1, FS: fs, F1: f1, Sae, Ra, SaR: Sae / Ra, dFN });
}
const KDS_CT = { steel_mrf: [0.0724, 0.8], concrete_mrf: [0.0466, 0.9], ebf: [0.0731, 0.75], other: [0.0488, 0.75] };
const CU = [[0.4, 1.4], [0.3, 1.4], [0.2, 1.5], [0.15, 1.6], [0.1, 1.7]];
function cuOf(SD1) {
  if (SD1 >= CU[0][0]) return CU[0][1];
  if (SD1 <= CU[CU.length - 1][0]) return CU[CU.length - 1][1];
  for (let i = 0; i + 1 < CU.length; i++) { const [s0, c0] = CU[i], [s1, c1] = CU[i + 1]; if (s1 <= SD1 && SD1 <= s0) return c1 + (c0 - c1) * (SD1 - s1) / (s0 - s1); }
  return 1.7;
}
function kds(model, { S, R, IE = 1, Fa = 1, Fv = 1, SDS = null, SD1 = null, T = null, structure = "concrete_mrf", weights = null, direction = "X" }) {
  const hn = common(model, direction);
  for (const [l, v] of [["S", S], ["R", R], ["IE", IE], ["Fa", Fa], ["Fv", Fv]]) pos(l, v);
  const sds = SDS == null ? S * 2.5 * Fa * 2 / 3 : pos("SDS", SDS);
  const sd1 = SD1 == null ? S * Fv * 2 / 3 : pos("SD1", SD1);
  choice("structure", structure, Object.keys(KDS_CT));
  const [Ct, x] = KDS_CT[structure], Ta = Ct * hn ** x, Cu = cuOf(sd1);
  const Tu = T == null ? Ta : Math.min(pos("T", T), Cu * Ta);
  const Cs = Math.max(Math.min(sd1 / (R / IE * Tu), sds / (R / IE)), 0.01);
  const w = weightsOf(model, weights), W = sumW(w), V = Cs * W, k = kExp(Tu);
  return summary("kds41", direction, Tu, W, V, distribute(model, V, w, Object.fromEntries(model.stories.map(s => [s.name, s.elevation ** k]))), { k, Ta, Cu, SDS: sds, SD1: sd1, Cs });
}
const NSCP_CA = { SA: [0.16, 0.32], SB: [0.20, 0.40], SC: [0.24, 0.40], SD: [0.28, 0.44], SE: [0.34, 0.44] };
const NSCP_CV = { SA: [0.16, 0.32], SB: [0.20, 0.40], SC: [0.32, 0.56], SD: [0.40, 0.64], SE: [0.64, 0.96] };
const NSCP_CT = { steel_mrf: 0.0853, concrete_mrf: 0.0731, ebf: 0.0731, other: 0.0488 };
function nscp(model, { R, Z = 0.4, soil = "SD", I = 1, Na = 1, Nv = 1, Ca = null, Cv = null, T = null, structure = "concrete_mrf", weights = null, direction = "X" }) {
  const hn = common(model, direction);
  for (const [l, v] of [["R", R], ["I", I], ["Na", Na], ["Nv", Nv]]) pos(l, v);
  if (Z !== 0.2 && Z !== 0.4) throw new Error("Z must be 0.2 (zone 2) or 0.4 (zone 4)");
  choice("soil", soil, Object.keys(NSCP_CA));
  const zi = Z === 0.4 ? 1 : 0;
  const ca = Ca == null ? NSCP_CA[soil][zi] * (zi ? Na : 1) : pos("Ca", Ca);
  const cv = Cv == null ? NSCP_CV[soil][zi] * (zi ? Nv : 1) : pos("Cv", Cv);
  const TA = empT(hn, structure, NSCP_CT);
  const Tu = T == null ? TA : Math.min(pos("T", T), (zi ? 1.3 : 1.4) * TA);
  const w = weightsOf(model, weights), W = sumW(w);
  let V = Math.min(cv * I * W / (R * Tu), 2.5 * ca * I * W / R);
  V = Math.max(V, 0.11 * ca * I * W);
  if (zi) V = Math.max(V, 0.8 * Z * Nv * I * W / R);
  const Ft = Tu <= 0.7 ? 0 : Math.min(0.07 * Tu * V, 0.25 * V);
  return summary("nscp2015", direction, Tu, W, V, topForce(model, V, w, Ft), { TA, Ca: ca, Cv: cv, Ft, Cs: W ? V / W : 0 });
}
function ntc(model, { T, a0, c, Ta, Tb, k = 1, Q = 2, R0 = 2, k1 = 1, beta = 1, weights = null, direction = "X" }) {
  common(model, direction);
  const Tu = pos("T", T);
  for (const [l, v] of [["a0", a0], ["c", c], ["Ta", Ta], ["Tb", Tb], ["k", k], ["Q", Q], ["R0", R0], ["k1", k1], ["beta", beta]]) pos(l, v);
  if (Tb < Ta) throw new Error("Tb must be >= Ta");
  let a, p = null;
  if (Tu < Ta) a = a0 + (beta * c - a0) * Tu / Ta;
  else if (Tu < Tb) a = beta * c;
  else { p = k + (1 - k) * (Tb / Tu) ** 2; a = beta * c * p * (Tb / Tu) ** 2; }
  const Qp = Tu <= Ta ? 1 + (Q - 1) * Math.sqrt(beta / k) * Tu / Ta : Tu <= Tb ? 1 + (Q - 1) * Math.sqrt(beta / k) : 1 + (Q - 1) * Math.sqrt(beta * p / k);
  const Rr = Tu <= Ta ? k1 * R0 + 1 - Math.sqrt(Tu / Ta) : k1 * R0;
  const Cs = a / (Qp * Rr);
  const w = weightsOf(model, weights), W = sumW(w), V = Cs * W;
  return summary("ntc_cdmx", direction, Tu, W, V, distribute(model, V, w, Object.fromEntries(model.stories.map(s => [s.name, s.elevation]))), { a, Qp, R: Rr, Cs, ...(p != null ? { p } : {}) });
}

/* ---------------- wind */
function facade(model, direction) {
  common(model, direction);
  const [lx, ly] = planExtents(model);
  const B = direction === "X" ? ly : lx, L = direction === "X" ? lx : ly;
  if (!(B > 0)) throw new Error("plan width perpendicular to the wind is zero");
  const hs = model.stories.map(s => s.height);
  const trib = hs.map((h, i) => h / 2 + (i + 1 < hs.length ? hs[i + 1] / 2 : 0));
  return { B, L, h: Math.max(...model.stories.map(s => s.elevation)), trib };
}
function rowsOf(model, fac, pfn) {
  return model.stories.map((s, i) => { const [p, qz] = pfn(s.elevation); return { story: s.name, h: s.elevation, w: null, F: p * fac.trib[i] * fac.B, qz }; });
}
const windSummary = (code, direction, rows, extra) => summary(code, direction, null, null, rows.reduce((a, r) => a + r.F, 0), rows, extra);

const A16 = { B: [7.0, 1200], C: [9.5, 900], D: [11.5, 700] };
const A22 = { B: [7.5, 3280], C: [9.8, 2460], D: [11.5, 1935] };
const GUST = { B: [0.45, 1 / 4, 0.30, 320, 1 / 3, 30], C: [0.65, 1 / 6.5, 0.20, 500, 1 / 5, 15], D: [0.80, 1 / 9, 0.15, 650, 1 / 8, 7] };
const TOPO = { ridge: [[1.30, 1.45, 1.55], 3.0, 1.5, 1.5], escarpment: [[0.75, 0.85, 0.95], 2.5, 1.5, 4.0], hill: [[0.95, 1.05, 1.15], 4.0, 1.5, 1.5] };
const kz16 = (z, e) => { const [a, zg] = A16[e]; return 2.01 * (Math.min(Math.max(z / FT, 15), zg) / zg) ** (2 / a); };
const kz22 = (z, e) => { const [a, zg] = A22[e]; return 2.41 * (Math.min(Math.max(z / FT, 15), zg) / zg) ** (2 / a); };
export function asceKzt(z, f, H, Lh, x, e, up) {
  const [k1f, gamma, muU, muD] = TOPO[f];
  let r = H / Lh, Le = Lh;
  if (r < 0.2 || H < (e === "B" ? 60 : 15) * FT) return 1;
  if (r > 0.5) { r = 0.5; Le = 2 * H; }
  const K1 = k1f["BCD".indexOf(e)] * r, K2 = Math.max(1 - Math.abs(x) / ((up ? muU : muD) * Le), 0), K3 = Math.exp(-gamma * z / Le);
  return (1 + K1 * K2 * K3) ** 2;
}
const rl = e => e <= 0 ? 1 : 1 / e - (1 - Math.exp(-2 * e)) / (2 * e * e);
export function asceGust(e, h, B, L, V, n1 = null, beta = 0.02) {
  const [bb, ab, c, l, eps, zmin] = GUST[e];
  const hf = h / FT, Bf = B / FT, Lf = L / FT, zb = Math.max(0.6 * hf, zmin);
  const Iz = c * (33 / zb) ** (1 / 6), Lz = l * (zb / 33) ** eps;
  const Q = Math.sqrt(1 / (1 + 0.63 * ((Bf + hf) / Lz) ** 0.63));
  if (n1 == null || n1 >= 1) return { G: 0.925 * (1 + 1.7 * 3.4 * Iz * Q) / (1 + 1.7 * 3.4 * Iz), gust: "rigid", Iz, Q };
  const Vz = bb * (zb / 33) ** ab * (88 / 60) * V / MPH, N1 = n1 * Lz / Vz;
  const Rn = 7.47 * N1 / (1 + 10.3 * N1) ** (5 / 3);
  const R = Math.sqrt(Rn * rl(4.6 * n1 * hf / Vz) * rl(4.6 * n1 * Bf / Vz) * (0.53 + 0.47 * rl(15.4 * n1 * Lf / Vz)) / beta);
  const lg = Math.sqrt(2 * Math.log(3600 * n1)), gR = lg + 0.577 / lg;
  return { G: 0.925 * (1 + 1.7 * Iz * Math.sqrt(3.4 ** 2 * Q ** 2 + gR ** 2 * R ** 2)) / (1 + 1.7 * 3.4 * Iz), gust: "flexible", Iz, Q, R, gR };
}
function asceDir(model, ed, dir, p) {
  const fac = facade(model, dir), kz = ed === "7-16" ? kz16 : kz22;
  const kzt = z => p.topo ? asceKzt(z, p.topo.feature, p.topo.H, p.topo.Lh, p.topo.x, p.exposure, p.topo.upwind) : p.Kzt;
  const extra = {};
  let G = p.G;
  if (p.gust) { const g = asceGust(p.exposure, fac.h, fac.B, fac.L, p.V, p.gust === "flexible" ? p.n1 : null, p.damping); G = g.G; extra.gust_factor = g; }
  else if (G == null) G = ed === "7-16" ? 0.85 : 1.0;
  const split = p.cp_total == null;
  const cpw = p.cp_windward == null ? 0.8 : p.cp_windward;
  const cpl = p.cp_leeward == null ? interp(fac.L / fac.B, [1, 2, 4], [-0.5, -0.3, -0.2]) : p.cp_leeward;
  const qh = 0.613 * kz(fac.h, p.exposure) * kzt(fac.h) * p.Ke * p.V ** 2 / 1000;
  const rows = rowsOf(model, fac, z => {
    const qz = 0.613 * kz(z, p.exposure) * kzt(z) * p.Ke * p.V ** 2 / 1000;
    return [split ? p.Kd * G * (qz * cpw + qh * Math.abs(cpl)) : qz * p.Kd * G * p.cp_total, qz];
  });
  Object.assign(extra, { G, qh, width: fac.B, depth: fac.L }, split ? { cp_windward: cpw, cp_leeward: cpl } : {});
  if (p.topo) extra.Kzt_h = kzt(fac.h);
  return { rows, extra };
}
function asceWind(model, ed, prm) {
  const { V, exposure = "C", Kzt = 1, Kd = 0.85, ze = 0, cp_total = null, direction = "X", Ke = null, G = null,
    cp_windward = null, cp_leeward = null, load_case = 1, ecc_sign = 1, transverse_sign = 1, e_ratio = 0.15,
    topo_feature = null, topo_H = null, topo_Lh = null, topo_x = null, topo_upwind = true, gust = null, n1 = null, damping = 0.02 } = prm;
  common(model, direction);
  pos("V", V); pos("Kzt", Kzt); pos("Kd", Kd); num("ze", ze);
  if (![1, 2, 3, 4].includes(load_case)) throw new Error("load_case must be 1, 2, 3 or 4 (Fig. 27.3-8)");
  if (!A16[exposure]) throw new Error("exposure must be one of ['B', 'C', 'D']");
  if (gust != null && !["rigid", "flexible"].includes(gust)) throw new Error("gust must be 'rigid' or 'flexible'");
  if (gust === "flexible" && !(n1 > 0 && n1 < 1)) throw new Error("gust 'flexible' needs n1 < 1 Hz (§26.2)");
  const topo = topo_feature ? { feature: choice("topo_feature", topo_feature, Object.keys(TOPO)), H: pos("topo_H", topo_H), Lh: pos("topo_Lh", topo_Lh), x: topo_x == null ? 0 : num("topo_x", topo_x), upwind: !!topo_upwind } : null;
  const p = { V, exposure, Kzt, Kd, Ke: Ke == null ? Math.exp(-0.000119 * ze) : pos("Ke", Ke), G, gust, n1, damping, cp_total, cp_windward, cp_leeward, topo };
  const code = ed === "7-16" ? "asce7_16_wind" : "asce7_22_wind";
  const other = direction === "X" ? "Y" : "X";
  const main = asceDir(model, ed, direction, p);
  const e = ecc_sign * e_ratio;
  const extra = { edition: ed, load_case, Ke: p.Ke, ...main.extra };
  if (load_case <= 2) {
    const f = load_case === 1 ? 1 : 0.75;
    const out = main.rows.map(r => ({ ...r, F: f * r.F, ...(load_case === 2 ? { Mz: f * r.F * e * main.extra.width } : {}) }));
    if (load_case === 2) extra.case_ecc = e;
    return windSummary(code, direction, out, extra);
  }
  const sec = asceDir(model, ed, other, p);
  const f = load_case === 3 ? 0.75 : 0.5625;
  const out = main.rows.map((r, i) => {
    const mf = f * r.F, sf = f * sec.rows[i].F * transverse_sign;
    const [fx, fy] = direction === "X" ? [mf, sf] : [sf, mf];
    return { ...r, F: mf, fx, fy, ...(load_case === 4 ? { Mz: e * (Math.abs(mf) * main.extra.width + Math.abs(sf) * sec.extra.width) } : {}) };
  });
  if (load_case === 4) {
    const sy = direction === "X" ? transverse_sign : 1, sx = direction === "X" ? 1 : transverse_sign;
    extra.case_ecc = e * sx;
    if (sx !== sy) extra.case_ecc_y = e * sy;
  }
  return windSummary(code, direction, out, extra);
}

const EN_T = { "0": [0.003, 1], I: [0.01, 1], II: [0.05, 2], III: [0.3, 5], IV: [1.0, 10] };
function enQp(z, vb, t, rho, co, kI) {
  const [z0, zmin] = EN_T[t], zz = Math.max(z, zmin);
  const kr = 0.19 * (z0 / 0.05) ** 0.07, vm = kr * Math.log(zz / z0) * co * vb, Iv = kI / (co * Math.log(zz / z0));
  return { qp: (1 + 7 * Iv) * 0.5 * rho * vm ** 2 / 1000, vm, Iv };
}
function en(model, { vb0, terrain = "II", cdir = 1, cseason = 1, rho = 1.25, co = 1, kI = 1, cpe_D = null, cpe_E = null, cscd = null, n1 = null, delta = 0.1, correlation = true, ze_rule = "fig7_4", direction = "X" }) {
  const fac = facade(model, direction);
  choice("terrain", terrain, Object.keys(EN_T));
  pos("vb0", vb0);
  const vb = cdir * cseason * vb0, { h, B: b, L: d } = fac, hd = d > 0 ? h / d : 5;
  const cD = cpe_D == null ? interp(hd, [0.25, 1, 5], [0.7, 0.8, 0.8]) : cpe_D;
  const cE = cpe_E == null ? interp(hd, [0.25, 1, 5], [-0.3, -0.5, -0.7]) : cpe_E;
  const fc = correlation ? interp(hd, [1, 5], [0.85, 1]) : 1;
  let cs = cscd == null ? 1 : cscd;
  if (cscd == null && n1 != null) {
    const [z0, zmin] = EN_T[terrain], zs = Math.max(0.6 * h, zmin);
    const Lz = 300 * (zs / 200) ** (0.67 + 0.05 * Math.log(z0)), q = enQp(zs, vb, terrain, rho, co, kI);
    const B2 = 1 / (1 + 0.9 * ((b + h) / Lz) ** 0.63), fL = n1 * Lz / q.vm;
    const SL = 6.8 * fL / (1 + 10.2 * fL) ** (5 / 3), R2 = Math.PI ** 2 / (2 * delta) * SL * rl(4.6 * h * fL / Lz) * rl(4.6 * b * fL / Lz);
    const nu = Math.max(n1 * Math.sqrt(R2 / (B2 + R2)), 0.08), lg = Math.sqrt(2 * Math.log(nu * 600));
    const kp = Math.max(lg + 0.6 / lg, 3);
    cs = (1 + 2 * kp * q.Iv * Math.sqrt(B2 + R2)) / (1 + 7 * q.Iv);
  }
  const qh = enQp(h, vb, terrain, rho, co, kI).qp;
  const zeOf = z => ze_rule !== "fig7_4" ? z : h <= b ? h : h <= 2 * b ? (z <= b ? b : h) : z <= b ? b : z >= h - b ? h : z;
  const rows = rowsOf(model, fac, z => { const qz = enQp(zeOf(z), vb, terrain, rho, co, kI).qp; return [cs * fc * (cD * qz + Math.abs(cE) * qh), qz]; });
  return windSummary("en1991_wind", direction, rows, { vb, qh, cpe_D: cD, cpe_E: cE, f_corr: fc, cscd: cs, h_over_d: hd, width: b });
}
const IS_Z = [10, 15, 20, 30, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500];
const IS_K2 = {
  1: [1.05, 1.09, 1.12, 1.15, 1.20, 1.26, 1.30, 1.32, 1.34, 1.35, 1.35, 1.35, 1.35, 1.35],
  2: [1.00, 1.05, 1.07, 1.12, 1.17, 1.24, 1.28, 1.30, 1.32, 1.34, 1.35, 1.35, 1.35, 1.35],
  3: [0.91, 0.97, 1.01, 1.06, 1.12, 1.20, 1.24, 1.27, 1.29, 1.31, 1.32, 1.33, 1.33, 1.34],
  4: [0.80, 0.80, 0.80, 0.97, 1.10, 1.20, 1.24, 1.27, 1.28, 1.30, 1.31, 1.32, 1.33, 1.34] };
function is875(model, { Vb, terrain = 2, k1 = 1, k3 = 1, k4 = 1, Kd = 0.9, Ka = 1, Kc = 0.9, cp_windward = 0.8, cp_leeward = -0.5, direction = "X" }) {
  const fac = facade(model, direction);
  if (!IS_K2[terrain]) throw new Error("terrain must be 1, 2, 3 or 4");
  pos("Vb", Vb);
  const pd = z => { const pz = 0.6 * (Vb * k1 * interp(z, IS_Z, IS_K2[terrain]) * k3 * k4) ** 2 / 1000; return [Math.max(Kd * Ka * Kc * pz, 0.7 * pz), pz]; };
  const pdh = pd(fac.h)[0];
  const rows = rowsOf(model, fac, z => { const [p] = pd(z); return [cp_windward * p + Math.abs(cp_leeward) * pdh, p]; });
  return windSummary("is875_wind", direction, rows, { pd_h: pdh, width: fac.B });
}
const AS2_Z = [3, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200];
const AS2_MZ = {
  "1": [0.99, 1.05, 1.12, 1.16, 1.19, 1.22, 1.24, 1.25, 1.27, 1.29, 1.31, 1.32],
  "2": [0.91, 0.91, 1.00, 1.05, 1.08, 1.12, 1.16, 1.18, 1.22, 1.24, 1.27, 1.29],
  "2.5": [0.87, 0.87, 0.92, 0.97, 1.01, 1.06, 1.10, 1.13, 1.17, 1.20, 1.24, 1.27],
  "3": [0.83, 0.83, 0.83, 0.89, 0.94, 1.00, 1.04, 1.07, 1.12, 1.16, 1.21, 1.24],
  "4": [0.75, 0.75, 0.75, 0.75, 0.75, 0.80, 0.85, 0.90, 0.98, 1.03, 1.11, 1.16] };
function as2(model, { VR, terrain = "2", Md = 1, Ms = 1, Mt = 1, cpe_windward = 0.7, cpe_leeward = null, Ka = 1, Kc = 1, Cdyn = 1, rho_air = 1.2, vdes_min = 30, direction = "X" }) {
  const fac = facade(model, direction);
  terrain = String(terrain);
  if (!AS2_MZ[terrain]) throw new Error("terrain must be one of 1, 2, 2.5, 3, 4");
  pos("VR", VR);
  const cpl = cpe_leeward == null ? interp(fac.L / fac.B, [1, 2, 4], [-0.5, -0.3, -0.2]) : cpe_leeward;
  const vdes = z => Math.max(VR * Md * interp(z, AS2_Z, AS2_MZ[terrain]) * Ms * Mt, vdes_min);
  const q = z => 0.5 * rho_air * vdes(z) ** 2 / 1000, qh = q(fac.h);
  const rows = rowsOf(model, fac, z => { const qz = fac.h > 25 ? q(z) : qh; return [(cpe_windward * qz + Math.abs(cpl) * qh) * Ka * Kc * Cdyn, qz]; });
  return windSummary("as1170_2_wind", direction, rows, { qh, Vdes_h: vdes(fac.h), cpe_windward, cpe_leeward: cpl, width: fac.B });
}
const GB9 = { A: [1.284, 0.24, 5], B: [1.0, 0.30, 10], C: [0.544, 0.44, 15], D: [0.262, 0.60, 30] };
const GB9G = { A: [0.12, 1.28, 0.944, 0.155], B: [0.14, 1.0, 0.670, 0.187], C: [0.23, 0.54, 0.295, 0.261], D: [0.39, 0.26, 0.112, 0.346] };
const muz = (z, t) => { const [a, e, zf] = GB9[t]; return Math.min(a * (Math.max(z, zf) / 10) ** e, 2.91); };
function gb9(model, { w0, terrain = "B", mu_s = 1.3, beta_z = null, T1 = null, zeta1 = 0.05, direction = "X" }) {
  const fac = facade(model, direction);
  if (!GB9[terrain]) throw new Error("terrain must be one of A, B, C, D");
  pos("w0", w0);
  const { h: H, B } = fac;
  const bz = z => {
    if (beta_z != null) return beta_z;
    if (T1 == null) return 1;
    const [I10, kw, k, a1] = GB9G[terrain];
    const x1 = Math.max(30 / T1 / Math.sqrt(kw * w0), 5);
    const R2 = Math.PI / (6 * zeta1) * x1 ** 2 / (1 + x1 ** 2) ** (4 / 3);
    const rz = 10 * Math.sqrt(H + 60 * Math.exp(-H / 60) - 60) / H, rx = 10 * Math.sqrt(B + 50 * Math.exp(-B / 50) - 50) / B;
    return 1 + 2 * 2.5 * I10 * (k * H ** a1 * rx * rz * (z / H) / muz(z, terrain)) * Math.sqrt(1 + R2);
  };
  const rows = rowsOf(model, fac, z => [bz(z) * mu_s * muz(z, terrain) * w0, muz(z, terrain) * w0]);
  for (const r of rows) r.beta_z = bz(r.h);
  return windSummary("gb50009_wind", direction, rows, { width: B, mu_s });
}

/* ---------------- registration (mock_loads116.js hooks) */
const ASCE_KEYS = ["load_case", "ecc_sign", "transverse_sign", "e_ratio", "topo_feature", "topo_H", "topo_Lh", "topo_x", "topo_upwind", "gust", "n1", "damping"];
export const ALX_MOCK_PARAMS = {
  nzs1170_5: ["Z", "mu", "soil", "R", "N", "Sp", "T", "structure", "weights", "direction"],
  as1170_4: ["Z", "kp", "site", "mu", "Sp", "T", "structure", "kpZ_min", "weights", "direction"],
  gb50011: ["T", "intensity", "level", "site", "group", "alpha_max", "Tg", "zeta", "geq_factor", "top_force", "weights", "direction"],
  tsc2018: ["SS", "S1", "R", "D", "I", "site", "FS", "F1", "T", "structure", "TL", "weights", "direction"],
  kds41: ["S", "R", "IE", "Fa", "Fv", "SDS", "SD1", "T", "structure", "weights", "direction"],
  nscp2015: ["R", "Z", "soil", "I", "Na", "Nv", "Ca", "Cv", "T", "structure", "weights", "direction"],
  ntc_cdmx: ["T", "a0", "c", "Ta", "Tb", "k", "Q", "R0", "k1", "beta", "weights", "direction"],
  asce7_16_wind: ["V", "exposure", "Kzt", "Kd", "ze", "cp_total", "direction", "Ke", "G", "cp_windward", "cp_leeward", ...ASCE_KEYS],
  en1991_wind: ["vb0", "terrain", "cdir", "cseason", "rho", "co", "kI", "cpe_D", "cpe_E", "cscd", "n1", "delta", "correlation", "ze_rule", "direction"],
  is875_wind: ["Vb", "terrain", "k1", "k3", "k4", "Kd", "Ka", "Kc", "cp_windward", "cp_leeward", "direction"],
  as1170_2_wind: ["VR", "terrain", "Md", "Ms", "Mt", "cpe_windward", "cpe_leeward", "Ka", "Kc", "Cdyn", "rho_air", "vdes_min", "direction"],
  gb50009_wind: ["w0", "terrain", "mu_s", "beta_z", "T1", "zeta1", "direction"],
};
export const ALX_MOCK_ASCE22_KEYS = ["Ke", "G", "cp_windward", "cp_leeward", ...ASCE_KEYS];
export const ALX_MOCK_DEFAULT_NAME = {
  nzs1170_5: "NZS1170", as1170_4: "AS1170", gb50011: "GB50011", tsc2018: "TSC2018", kds41: "KDS41",
  nscp2015: "NSCP2015", ntc_cdmx: "NTCSISMO", asce7_16_wind: "WIND16", en1991_wind: "WINDEN",
  is875_wind: "WINDIS", as1170_2_wind: "WINDAS", gb50009_wind: "WINDGB" };
export const ALX_MOCK_WIND = new Set(["asce7_22_wind", "asce7_16_wind", "en1991_wind", "is875_wind", "as1170_2_wind", "gb50009_wind"]);

/** {code: fn(model, params)}; `legacy22` = the v1.16 mock ASCE 7-22 wind,
    used when none of the v1.19 keys asks for something new. */
export function alxMockCompute(legacy22) {
  return {
    nzs1170_5: nzs, as1170_4: as4, gb50011: gb11, tsc2018: tsc, kds41: kds, nscp2015: nscp, ntc_cdmx: ntc,
    asce7_16_wind: (m, p) => asceWind(m, "7-16", p),
    asce7_22_wind: (m, p) => {
      const lc = p.load_case ?? 1;
      if (lc === 1 && p.topo_feature == null && p.gust == null && p.topo_H == null && p.topo_Lh == null && p.topo_x == null
        && p.G == null && p.Ke == null && p.cp_windward == null && p.cp_leeward == null) {
        const q = {};
        for (const k of ["V", "exposure", "Kzt", "Kd", "ze", "cp_total", "direction"]) if (k in p) q[k] = p[k];
        return legacy22(m, q);
      }
      const split = p.cp_windward != null || p.cp_leeward != null;
      return asceWind(m, "7-22", { cp_total: 1.3, G: 1.0, ...p, ...(split ? { cp_total: null } : {}), ...(p.gust ? { G: null } : {}) });
    },
    en1991_wind: en, is875_wind: is875, as1170_2_wind: as2, gb50009_wind: gb9,
  };
}
/** autolateral.generate case_torsion mirror → [ecc, ecc_y|null]. */
export function alxMockCaseTorsion(sm, ecc) {
  if (!ecc && sm.case_ecc) return [sm.case_ecc, sm.case_ecc_y ?? null];
  return [ecc, null];
}
