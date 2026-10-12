/* SkyFrame — ?mock=1 twin of the Wave C auto-lateral generators
   (skyframe.core.seismic_codes_wavec: ec8_na, nbcc2020, ibc2006/2009/2012,
   ubc97, ntc2018; skyframe.core.wind_codes_wavec: bs6399_wind, api4f_wind,
   ntc2018_wind).  Same formulas, constants and summary shape; registered by
   a few hook lines in js/mock_autolat_ext.js.  Everything SI (kN, m, kPa). */

const G0 = 9.80665;
const isNum = v => typeof v === "number" && isFinite(v);
function pos(label, v, allowZero = false) {
  if (!isNum(v) || v < 0 || (v === 0 && !allowZero)) throw new Error(`${label} must be a finite value ${allowZero ? ">= 0" : "> 0"}`);
  return +v;
}
function num(label, v) { if (!isNum(v)) throw new Error(`${label} must be a finite number`); return +v; }
function choice(label, v, allowed) {
  if (!allowed.map(String).includes(String(v))) throw new Error(`${label} must be one of ${allowed.join(", ")}`);
  return allowed.find(a => String(a) === String(v));
}
export function wvcInterp(x, xs, ys) {
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  for (let i = 0; i + 1 < xs.length; i++)
    if (xs[i] <= x && x <= xs[i + 1]) return ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / (xs[i + 1] - xs[i]);
  return ys[ys.length - 1];
}
const interp = wvcInterp;
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
  return Object.fromEntries(st.map(s => [s.name, (+sm[s.name] || 0) * G0]));
}
const sumW = w => Object.values(w).reduce((a, b) => a + b, 0);
const kExp = T => T <= 0.5 ? 1 : T >= 2.5 ? 2 : 1 + (T - 0.5) / 2;
function distribute(model, V, w, shape) {
  const den = model.stories.reduce((a, s) => a + w[s.name] * shape[s.name], 0);
  return model.stories.map(s => ({ story: s.name, h: s.elevation, w: w[s.name], F: den > 0 ? V * w[s.name] * shape[s.name] / den : 0 }));
}
const heights = model => Object.fromEntries(model.stories.map(s => [s.name, s.elevation]));
function topForce(model, V, w, Ft) {
  const rows = distribute(model, V - Ft, w, heights(model));
  const top = model.stories.reduce((a, s) => s.elevation > a.elevation ? s : a).name;
  for (const r of rows) if (r.story === top) r.F += Ft;
  return rows;
}
const summary = (code, direction, T, W, V, stories, extra = {}) => ({ code, direction, T, W, V, ...extra, stories });
const cu = SD1 => interp(SD1, [0.1, 0.15, 0.2, 0.3, 0.4], [1.7, 1.6, 1.5, 1.4, 1.4]);

/* ---------------- EC8 + national annex (values from memory; REVIEW_FLAGS Wave C) */
const REC1 = { A: [1.0, 0.15, 0.4, 2.0], B: [1.2, 0.15, 0.5, 2.0], C: [1.15, 0.2, 0.6, 2.0], D: [1.35, 0.2, 0.8, 2.0], E: [1.4, 0.15, 0.5, 2.0] };
const REC2 = { A: [1.0, 0.05, 0.25, 1.2], B: [1.35, 0.05, 0.25, 1.2], C: [1.5, 0.1, 0.25, 1.2], D: [1.8, 0.1, 0.3, 1.2], E: [1.6, 0.05, 0.25, 1.2] };
export const WVC_EC8_NA = {
  UK: { label: "United Kingdom (NA to BS EN 1998-1:2004)", spectra: { 1: REC1, 2: REC2 }, def: 1 },
  IT: { label: "Italy (UNI EN 1998-1 NA, DM 31/07/2012)", spectra: { 1: REC1, 2: REC2 }, def: 1 },
  DE: { label: "Germany (DIN EN 1998-1/NA:2011, DIN 4149 subsoil classes)", def: 1, spectra: { 1: {
    "A-R": [1.0, 0.05, 0.2, 2.0], "B-R": [1.25, 0.05, 0.25, 2.0], "C-R": [1.5, 0.05, 0.3, 2.0],
    "B-T": [1.0, 0.1, 0.3, 2.0], "C-T": [1.25, 0.1, 0.4, 2.0], "C-S": [0.75, 0.1, 0.5, 2.0] } } },
  FR: { label: "France (NF EN 1998-1/NA, arrete du 22 octobre 2010)", def: 2, spectra: { 1: REC1, 2: {
    A: [1.0, 0.03, 0.2, 2.5], B: [1.35, 0.05, 0.25, 2.5], C: [1.5, 0.06, 0.4, 2.0], D: [1.6, 0.1, 0.6, 1.5], E: [1.8, 0.08, 0.45, 1.25] } } },
  ES: { label: "Spain (UNE EN 1998-1 NA)", spectra: { 1: REC1, 2: REC2 }, def: 1 },
  PT: { label: "Portugal (NP EN 1998-1:2010 NA)", def: 1, ptS: true, spectra: {
    1: { A: [1.0, 0.1, 0.6, 2.0], B: [1.35, 0.1, 0.6, 2.0], C: [1.6, 0.1, 0.6, 2.0], D: [2.0, 0.1, 0.8, 2.0], E: [1.8, 0.1, 0.6, 2.0] },
    2: { A: [1.0, 0.1, 0.25, 2.0], B: [1.35, 0.1, 0.25, 2.0], C: [1.6, 0.1, 0.25, 2.0], D: [2.0, 0.1, 0.3, 2.0], E: [1.8, 0.1, 0.25, 2.0] } } },
  GR: { label: "Greece (ELOT EN 1998-1 NA)", spectra: { 1: REC1 }, def: 1 },
  RO: { label: "Romania (SR EN 1998-1/NA, P100-1/2013 control periods)", def: 1, spectra: { 1: {
    "TC=0.7": [1.0, 0.14, 0.7, 3.0], "TC=1.0": [1.0, 0.2, 1.0, 3.0], "TC=1.6": [1.0, 0.32, 1.6, 2.0] } } },
};
export function wvcEc8NaParams(annex, ground, stype, ag) {
  choice("annex", annex, Object.keys(WVC_EC8_NA));
  const na = WVC_EC8_NA[annex];
  const st = stype == null || stype === "na" ? na.def : +stype;
  if (!na.spectra[st]) throw new Error(`spectrum_type must be one of ${Object.keys(na.spectra).join(", ")} for annex ${annex}`);
  choice("ground_type", ground, Object.keys(na.spectra[st]));
  let [S, TB, TC, TD] = na.spectra[st][ground];
  if (na.ptS && ag != null) { const a = ag * G0; S = a >= 4 ? 1 : a > 1 ? S - (S - 1) * (a - 1) / 3 : S; }
  return { spectrum_type: st, S, TB, TC, TD, beta: 0.2 };
}
const ec8Sd = (T, ag, q, S, TB, TC, TD, beta) => T <= TB ? ag * S * (2 / 3 + T / TB * (2.5 / q - 2 / 3)) : T <= TC ? ag * S * 2.5 / q
  : T <= TD ? Math.max(ag * S * 2.5 / q * TC / T, beta * ag) : Math.max(ag * S * 2.5 / q * TC * TD / T ** 2, beta * ag);
const EC8_CT = { steel_mrf: 0.085, concrete_mrf: 0.075, other: 0.05 };
function ec8na(model, { annex, ag, q, ground_type = "B", spectrum_type = null, gamma_I = 1, T1 = null, structure = "other",
  S = null, TB = null, TC = null, TD = null, beta = null, weights = null, direction = "X" }) {
  const H = common(model, direction);
  pos("ag", ag); pos("q", q); pos("gamma_I", gamma_I);
  const p = wvcEc8NaParams(annex, ground_type, spectrum_type, gamma_I * ag);
  choice("structure", structure, Object.keys(EC8_CT));
  const s = S ?? p.S, tb = TB ?? p.TB, tc = TC ?? p.TC, td = TD ?? p.TD, b = beta ?? p.beta;
  const agd = gamma_I * ag;
  const T = T1 == null ? EC8_CT[structure] * H ** 0.75 : pos("T1", T1);
  const Sd = ec8Sd(T, agd, q, s, tb, tc, td, b);
  const lam = T <= 2 * tc && model.stories.length > 2 ? 0.85 : 1;
  const w = weightsOf(model, weights), W = sumW(w), Fb = Sd * W * lam;
  return summary("ec8_na", direction, T, W, Fb, distribute(model, Fb, w, heights(model)),
    { Sd, lambda_: lam, ag: agd, S: s, TB: tb, TC: tc, TD: td, annex, ground_type, spectrum_type: p.spectrum_type, beta: b, preset_from_memory: true });
}

/* ---------------- NBCC 2020 Table 4.1.8.11 (OBC 2024 / NBCC 2020 text) */
const NB_R = [5, 20, 40, 70], NB_T = [0.5, 1, 2, 5];
export const WVC_NBCC_MVJ = {
  mrf: [[1, 1, 1, null, 1, 1, 0.95, null], [1, 1, 1, null, 1, 0.97, 0.88, null], [1, 1, 1, null, 1, 0.90, 0.79, null], [1, 1, 1, null, 0.98, 0.88, 0.70, null]],
  coupled_walls: [[1, 1, 1, 1, 1, 1, 0.95, 0.80], [1, 1, 1, 1.09, 1, 0.97, 0.88, 0.66], [1, 1, 1, 1.33, 1, 0.90, 0.79, 0.52], [1, 1, 1, 1.90, 0.98, 0.88, 0.70, 0.40]],
  braced: [[1, 1, 1, null, 1, 0.98, 0.93, null], [1, 1, 1, null, 1, 0.91, 0.80, null], [1, 1, 1, null, 0.91, 0.82, 0.72, null], [1, 1, 1.19, null, 0.91, 0.77, 0.61, null]],
  walls: [[1, 1, 1, 1.30, 1, 1, 0.85, 0.59], [1, 1, 1.18, 2.50, 1, 0.80, 0.60, 0.35], [1, 1.25, 1.85, 4.10, 0.80, 0.59, 0.42, 0.23], [1, 1.25, 2.30, 6.40, 0.80, 0.56, 0.30, 0.18]],
  other: [[1, 1, 1, null, 1, 1, 0.85, null], [1, 1, 1.18, null, 1, 0.80, 0.60, null], [1, 1.25, 1.85, null, 0.80, 0.59, 0.44, null], [1, 1.37, 2.30, null, 0.80, 0.56, 0.30, null]],
};
const NB_SYS = { steel_mf: "mrf", concrete_mf: "mrf", other_mf: "mrf", braced: "braced", walls: "walls", other: "other" };
const NB_CAP = { steel_mf: 1.5, concrete_mf: 1.5, other_mf: 1.5, braced: 2, walls: 2, other: 1 };
function nbRow(sys, ratio) {
  const rows = WVC_NBCC_MVJ[sys], r = Math.min(Math.max(ratio, 0), 70);
  return [0, 1, 2, 3, 4, 5, 6, 7].map(j => rows[0][j] == null ? null : interp(r, [0, ...NB_R], [1, ...rows.map(x => x[j])]));
}
function nbS(T, Sa, ll) {
  const pts = [[0.2, Math.max(Sa[0], Sa[1])], [0.5, Sa[1]], [1, Sa[2]], [2, Sa[3]], [5, Sa[4]], [10, Sa[5]]];
  if (T <= 0.2) return pts[0][1];
  if (T >= 10) return pts[5][1];
  for (let i = 0; i < 5; i++) {
    const [t0, s0] = pts[i], [t1, s1] = pts[i + 1];
    if (T >= t0 && T <= t1) {
      if (ll && s0 > 0 && s1 > 0) return Math.exp(Math.log(s0) + Math.log(T / t0) / Math.log(t1 / t0) * Math.log(s1 / s0));
      return s0 + (s1 - s0) * (T - t0) / (t1 - t0);
    }
  }
  return pts[5][1];
}
function nbMvJ(sys, ratio, Ta, S, ll) {
  const row = nbRow(sys, ratio), wall = sys === "walls" || sys === "coupled_walls";
  const ts = wall ? NB_T : NB_T.slice(0, 3), mv = row.slice(0, ts.length), jj = row.slice(4, 4 + ts.length);
  const T = Math.min(Math.max(Ta, 0.5), wall ? 4 : 2);
  const Mv = interp(T, ts, mv), J = interp(T, ts, jj);
  const SMv = Ta <= 0.5 ? S(Ta) * Mv : ll ? S(T) * Mv : interp(T, ts, ts.map((t, i) => S(t) * mv[i]));
  return { Mv, J, SMv };
}
function nbcc(model, { Sa02, Sa05, Sa10, Sa20, Sa50, Sa100, RdRo, Rd = null, IE = 1, system = "concrete_mf", table_system = null,
  T = null, interp: ip = "linear", cap = true, weights = null, direction = "X" }) {
  const hn = common(model, direction);
  const Sa = [["Sa02", Sa02], ["Sa05", Sa05], ["Sa10", Sa10], ["Sa20", Sa20], ["Sa50", Sa50], ["Sa100", Sa100]].map(([l, v]) => pos(l, v, true));
  pos("RdRo", RdRo); pos("IE", IE);
  choice("system", system, Object.keys(NB_SYS)); choice("interp", ip, ["linear", "loglog"]);
  const tsys = table_system == null ? NB_SYS[system] : choice("table_system", table_system, Object.keys(WVC_NBCC_MVJ));
  const N = model.stories.length;
  const Taf = system === "steel_mf" ? 0.085 * hn ** 0.75 : system === "concrete_mf" ? 0.075 * hn ** 0.75 : system === "other_mf" ? 0.1 * N
    : system === "braced" ? 0.025 * hn : 0.05 * hn ** 0.75;
  const Tu = T == null ? Taf : Math.min(pos("T", T), NB_CAP[system] * Taf);
  const ll = ip === "loglog", S = t => nbS(t, Sa, ll);
  const S02 = S(0.2), ratio = Sa[4] > 0 ? S02 / Sa[4] : 70;
  const mj = nbMvJ(tsys, ratio, Tu, S, ll);
  const w = weightsOf(model, weights), W = sumW(w);
  let V = mj.SMv * IE * W / RdRo;
  const Vmin = nbMvJ(tsys, ratio, tsys === "walls" || tsys === "coupled_walls" ? 4 : 2, S, ll).SMv * IE * W / RdRo;
  const Vmax = Math.max(2 / 3 * S02, S(0.5)) * IE * W / RdRo;
  const capOn = !!cap && (Rd == null || pos("Rd", Rd) >= 1.5);
  V = Math.max(V, Vmin); if (capOn) V = Math.min(V, Vmax);
  const Ft = Tu <= 0.7 ? 0 : Math.min(0.07 * Tu * V, 0.25 * V);
  const rows = topForce(model, V, w, Ft);
  for (const r of rows) {
    const Jx = r.h >= 0.6 * hn ? 1 : mj.J + (1 - mj.J) * r.h / (0.6 * hn);
    r.J = Jx; r.M = Jx * rows.filter(o => o.h > r.h).reduce((a, o) => a + o.F * (o.h - r.h), 0);
  }
  const M0 = mj.J * rows.reduce((a, o) => a + o.F * o.h, 0);
  return summary("nbcc2020", direction, Tu, W, V, rows, { Ta_formula: Taf, S_Ta: S(Tu), S02, ratio, Mv: mj.Mv, J: mj.J, SMv: mj.SMv,
    V_min: Vmin, V_max: Vmax, cap_applied: capOn, Ft, M0, table_system: tsys });
}

/* ---------------- legacy IBC (ASCE 7-05 / 7-10) */
const IBC_SS = [0.25, 0.5, 0.75, 1.0, 1.25], IBC_S1 = [0.1, 0.2, 0.3, 0.4, 0.5];
const IBC_FA = { A: [0.8, 0.8, 0.8, 0.8, 0.8], B: [1, 1, 1, 1, 1], C: [1.2, 1.2, 1.1, 1.0, 1.0], D: [1.6, 1.4, 1.2, 1.1, 1.0], E: [2.5, 1.7, 1.2, 0.9, 0.9] };
const IBC_FV = { A: [0.8, 0.8, 0.8, 0.8, 0.8], B: [1, 1, 1, 1, 1], C: [1.7, 1.6, 1.5, 1.4, 1.3], D: [2.4, 2.0, 1.8, 1.6, 1.5], E: [3.5, 3.2, 2.8, 2.4, 2.4] };
const IBC_CT = { steel_mrf: [0.0724, 0.8], concrete_mrf: [0.0466, 0.9], ebf: [0.0731, 0.75], other: [0.0488, 0.75] };
const IBC_STD = { ibc2006: "ASCE 7-05", ibc2009: "ASCE 7-05 + Suppl. 2", ibc2012: "ASCE 7-10" };
const ibc = ed => (model, { Ss, S1, R, Ie = 1, site = "D", TL = 8, Fa = null, Fv = null, T = null, structure = "concrete_mrf", weights = null, direction = "X" }) => {
  const hn = common(model, direction);
  pos("Ss", Ss, true); pos("S1", S1, true); pos("R", R); pos("Ie", Ie); pos("TL", TL);
  choice("site", site, Object.keys(IBC_FA)); choice("structure", structure, Object.keys(IBC_CT));
  const fa = Fa ?? interp(Ss, IBC_SS, IBC_FA[site]), fv = Fv ?? interp(S1, IBC_S1, IBC_FV[site]);
  const SDS = 2 / 3 * fa * Ss, SD1 = 2 / 3 * fv * S1;
  const [Ct, x] = IBC_CT[structure], Ta = Ct * hn ** x, Cu = cu(SD1);
  const Tu = T == null ? Ta : Math.min(pos("T", T), Cu * Ta), RI = R / Ie;
  const CsMax = Tu <= TL ? SD1 / (Tu * RI) : SD1 * TL / (Tu ** 2 * RI);
  let CsMin = ed === "ibc2006" ? 0.01 : Math.max(0.044 * SDS * Ie, 0.01);
  if (S1 >= 0.6) CsMin = Math.max(CsMin, 0.5 * S1 / RI);
  const Cs = Math.max(Math.min(SDS / RI, CsMax), CsMin);
  const w = weightsOf(model, weights), W = sumW(w), V = Cs * W, k = kExp(Tu);
  return summary(ed, direction, Tu, W, V, distribute(model, V, w, Object.fromEntries(model.stories.map(s => [s.name, s.elevation ** k]))),
    { k, Ta, Cu, Fa: fa, Fv: fv, SDS, SD1, Cs, Cs_max: CsMax, Cs_min: CsMin, standard: IBC_STD[ed] });
};

/* ---------------- UBC 97 */
const UBC_Z = [0.075, 0.15, 0.2, 0.3, 0.4];
const UBC_CA = { SA: [0.06, 0.12, 0.16, 0.24, 0.32], SB: [0.08, 0.15, 0.2, 0.3, 0.4], SC: [0.09, 0.18, 0.24, 0.33, 0.4], SD: [0.12, 0.22, 0.28, 0.36, 0.44], SE: [0.19, 0.3, 0.34, 0.36, 0.36] };
const UBC_CV = { SA: [0.06, 0.12, 0.16, 0.24, 0.32], SB: [0.08, 0.15, 0.2, 0.3, 0.4], SC: [0.13, 0.25, 0.32, 0.45, 0.56], SD: [0.18, 0.32, 0.4, 0.54, 0.64], SE: [0.26, 0.5, 0.64, 0.84, 0.96] };
const UBC_CT = { steel_mrf: 0.0853, concrete_mrf: 0.0731, ebf: 0.0731, other: 0.0488 };
function ubc(model, { R, Z = 0.4, soil = "SD", I = 1, Na = 1, Nv = 1, Ca = null, Cv = null, T = null, structure = "concrete_mrf", weights = null, direction = "X" }) {
  const hn = common(model, direction);
  pos("R", R); pos("I", I); pos("Na", Na); pos("Nv", Nv);
  if (!UBC_Z.includes(Z)) throw new Error(`Z must be one of ${UBC_Z.join(", ")}`);
  choice("soil", soil, Object.keys(UBC_CA)); choice("structure", structure, Object.keys(UBC_CT));
  const zi = UBC_Z.indexOf(Z), z4 = Z === 0.4;
  const ca = Ca ?? UBC_CA[soil][zi] * (z4 ? Na : 1), cv = Cv ?? UBC_CV[soil][zi] * (z4 ? Nv : 1);
  const TA = UBC_CT[structure] * hn ** 0.75, Tu = T == null ? TA : Math.min(pos("T", T), (z4 ? 1.3 : 1.4) * TA);
  const w = weightsOf(model, weights), W = sumW(w);
  let V = Math.max(Math.min(cv * I * W / (R * Tu), 2.5 * ca * I * W / R), 0.11 * ca * I * W);
  if (z4) V = Math.max(V, 0.8 * Z * Nv * I * W / R);
  const Ft = Tu <= 0.7 ? 0 : Math.min(0.07 * Tu * V, 0.25 * V);
  return summary("ubc97", direction, Tu, W, V, topForce(model, V, w, Ft), { TA, Ca: ca, Cv: cv, Ft, Cs: W ? V / W : 0 });
}

/* ---------------- Italian NTC 2018 */
const NTC_SOIL = { A: [1, 0, 1, 1, 1, 0], B: [1.4, 0.4, 1, 1.2, 1.1, -0.2], C: [1.7, 0.6, 1, 1.5, 1.05, -0.33], D: [2.4, 1.5, 0.9, 1.8, 1.25, -0.5], E: [2.0, 1.1, 1, 1.6, 1.15, -0.4] };
const NTC_TOPO = { T1: 1, T2: 1.2, T3: 1.2, T4: 1.4 };
export function wvcNtc18Params(ag, F0, Tcs, soil = "B", topography = "T1") {
  choice("soil", soil, Object.keys(NTC_SOIL)); choice("topography", topography, Object.keys(NTC_TOPO));
  const [a, b, lo, hi, cc, ex] = NTC_SOIL[soil];
  const SS = Math.min(Math.max(a - b * F0 * ag, lo), hi), CC = cc * Tcs ** ex, ST = NTC_TOPO[topography], TC = CC * Tcs;
  return { SS, CC, ST, S: SS * ST, TC, TB: TC / 3, TD: 4 * ag + 1.6 };
}
export const wvcNtc18Se = (T, ag, F0, S, TB, TC, TD, eta) => T < TB ? ag * S * eta * F0 * (T / TB + (1 - T / TB) / (eta * F0))
  : T < TC ? ag * S * eta * F0 : T < TD ? ag * S * eta * F0 * TC / T : ag * S * eta * F0 * TC * TD / T ** 2;
function ntc18(model, { ag, F0, Tc_star, q = 1.5, soil = "B", topography = "T1", T1 = null, structure = "other", weights = null, direction = "X" }) {
  const H = common(model, direction);
  pos("ag", ag); pos("F0", F0); pos("Tc_star", Tc_star); pos("q", q);
  const p = wvcNtc18Params(ag, F0, Tc_star, soil, topography);
  choice("structure", structure, Object.keys(EC8_CT));
  const T = T1 == null ? EC8_CT[structure] * H ** 0.75 : pos("T1", T1);
  const Sd = Math.max(wvcNtc18Se(T, ag, F0, p.S, p.TB, p.TC, p.TD, 1 / q), 0.2 * ag);
  const lam = T < 2 * p.TC && model.stories.length >= 3 ? 0.85 : 1;
  const w = weightsOf(model, weights), W = sumW(w), Fh = Sd * W * lam;
  return summary("ntc2018", direction, T, W, Fh, distribute(model, Fh, w, heights(model)), { Sd, lambda_: lam, ...p });
}

/* ---------------- wind */
function facade(model, direction) {
  common(model, direction);
  const xs = [], ys = [];
  for (const m of model.members || []) for (const p of [m.pi, m.pj]) { xs.push(+p[0]); ys.push(+p[1]); }
  const lx = xs.length ? Math.max(...xs) - Math.min(...xs) : 0, ly = ys.length ? Math.max(...ys) - Math.min(...ys) : 0;
  const B = direction === "X" ? ly : lx, L = direction === "X" ? lx : ly;
  if (!(B > 0)) throw new Error("plan width perpendicular to the wind is zero");
  const st = model.stories, hs = st.map(s => s.height);
  return { B, L, h: Math.max(...st.map(s => s.elevation)), trib: hs.map((h, i) => h / 2 + (i + 1 < hs.length ? hs[i + 1] / 2 : 0)) };
}
const rowsOf = (model, fac, pfn) => model.stories.map((s, i) => { const [p, qz] = pfn(s.elevation); return { story: s.name, h: s.elevation, w: null, F: p * fac.trib[i] * fac.B, qz }; });
const windSum = (code, direction, rows, extra) => ({ code, direction, T: null, W: null, V: rows.reduce((a, r) => a + r.F, 0), ...extra, stories: rows });

function bs6399(model, { Vb, Sb = 1, Sa = 1, Sd = 1, Ss = 1, Sp = 1, Ca = 1, Cr = 0, cpe_front = null, cpe_rear = null, divergence = null, direction = "X" }) {
  const fac = facade(model, direction);
  for (const [l, v] of [["Vb", Vb], ["Sa", Sa], ["Sd", Sd], ["Ss", Ss], ["Sp", Sp], ["Ca", Ca]]) pos(l, v);
  pos("Cr", Cr, true);
  let sb;
  if (isNum(Sb)) { pos("Sb", Sb); sb = () => Sb; }
  else if (Array.isArray(Sb) && Sb.length >= 2) { const xs = Sb.map(r => +r[0]), ys = Sb.map(r => +r[1]); sb = z => interp(z, xs, ys); }
  else throw new Error("Sb must be a number or [[He, Sb], ...] (>= 2 rows)");
  const DH = fac.L / fac.h;
  const cf = cpe_front ?? interp(DH, [1, 4], [0.8, 0.6]), cr = cpe_rear ?? interp(DH, [1, 4], [-0.3, -0.1]);
  const hd = fac.L > 0 ? fac.h / fac.L : 5, div = divergence ?? interp(hd, [1, 5], [0.85, 1]);
  const Vs = Vb * Sa * Sd * Ss * Sp;
  const rows = rowsOf(model, fac, z => { const qs = 0.613 * (Vs * sb(z)) ** 2 / 1000; return [div * qs * (cf + Math.abs(cr)) * Ca * (1 + Cr), qs]; });
  return windSum("bs6399_wind", direction, rows, { Vs, cpe_front: cf, cpe_rear: cr, divergence: div, width: fac.B });
}
export const WVC_API4F_K = 0.00338 * 4.4482216152605 / 0.3048 ** 2 / (1852 / 3600) ** 2;
function api4f(model, { Vref, Cs = 1.5, Ki = 1, alpha = 0.125, z_ref = 10, direction = "X" }) {
  const fac = facade(model, direction);
  for (const [l, v] of [["Vref", Vref], ["Cs", Cs], ["Ki", Ki], ["z_ref", z_ref]]) pos(l, v);
  pos("alpha", alpha, true);
  const rows = rowsOf(model, fac, z => { const vz = Vref * (Math.max(z, z_ref) / z_ref) ** alpha, q = WVC_API4F_K * vz ** 2 / 1000; return [Ki * Cs * q, q]; });
  return windSum("api4f_wind", direction, rows, { K: WVC_API4F_K, Vh: Vref * (Math.max(fac.h, z_ref) / z_ref) ** alpha, width: fac.B });
}
const NTCW_Z = { 1: [25, 1000, 0.4], 2: [25, 750, 0.45], 3: [27, 500, 0.37], 4: [28, 500, 0.36], 5: [28, 750, 0.4], 6: [28, 500, 0.36], 7: [28, 1000, 0.54], 8: [30, 1500, 0.5], 9: [31, 500, 0.32] };
const NTCW_E = { I: [0.17, 0.01, 2], II: [0.19, 0.05, 4], III: [0.2, 0.1, 5], IV: [0.22, 0.3, 8], V: [0.23, 0.7, 12] };
function ntcWind(model, { zone = 3, a_s = 0, category = "III", TR = 50, vb = null, ct = 1, cd = 1, rho = 1.25, cp_windward = 0.8, cp_leeward = -0.4, direction = "X" }) {
  const fac = facade(model, direction);
  if (!NTCW_Z[zone] || typeof zone !== "number") throw new Error("zone must be one of 1..9");
  choice("category", category, Object.keys(NTCW_E));
  pos("a_s", a_s, true);
  for (const [l, v] of [["TR", TR], ["ct", ct], ["cd", cd], ["rho", rho]]) pos(l, v);
  if (!(TR > 1)) throw new Error("TR must be > 1 year");
  const [vb0, a0, ks] = NTCW_Z[zone];
  const vbu = vb ?? vb0 * (a_s <= a0 ? 1 : 1 + ks * (a_s / a0 - 1));
  const cr = 0.75 * Math.sqrt(1 - 0.2 * Math.log(-Math.log(1 - 1 / TR))), vr = vbu * cr, qr = 0.5 * rho * vr ** 2 / 1000;
  const [kr, z0, zmin] = NTCW_E[category];
  const ce = z => { const L = ct * Math.log(Math.max(z, zmin) / z0); return kr * kr * L * (7 + L); };
  const cpw = num("cp_windward", cp_windward), cpl = num("cp_leeward", cp_leeward), ceh = ce(fac.h);
  const rows = rowsOf(model, fac, z => [qr * cd * (cpw * ce(z) + Math.abs(cpl) * ceh), qr * ce(z)]);
  return windSum("ntc2018_wind", direction, rows, { vb: vbu, cr, vr, qr, ce_h: ceh, width: fac.B });
}

export const WVC_MOCK_COMPUTE = { ec8_na: ec8na, nbcc2020: nbcc, ibc2006: ibc("ibc2006"), ibc2009: ibc("ibc2009"), ibc2012: ibc("ibc2012"),
  ubc97: ubc, ntc2018: ntc18, bs6399_wind: bs6399, api4f_wind: api4f, ntc2018_wind: ntcWind };
const IBC_KEYS = ["Ss", "S1", "R", "Ie", "site", "TL", "Fa", "Fv", "T", "structure", "weights", "direction"];
export const WVC_MOCK_PARAMS = {
  ec8_na: ["annex", "ag", "q", "ground_type", "spectrum_type", "gamma_I", "T1", "structure", "distribution", "mode_shape", "S", "TB", "TC", "TD", "beta", "weights", "direction"],
  nbcc2020: ["Sa02", "Sa05", "Sa10", "Sa20", "Sa50", "Sa100", "RdRo", "Rd", "IE", "system", "table_system", "T", "interp", "cap", "weights", "direction"],
  ibc2006: IBC_KEYS, ibc2009: IBC_KEYS, ibc2012: IBC_KEYS,
  ubc97: ["R", "Z", "soil", "I", "Na", "Nv", "Ca", "Cv", "T", "structure", "weights", "direction"],
  ntc2018: ["ag", "F0", "Tc_star", "q", "soil", "topography", "T1", "structure", "weights", "direction"],
  bs6399_wind: ["Vb", "Sb", "Sa", "Sd", "Ss", "Sp", "Ca", "Cr", "cpe_front", "cpe_rear", "divergence", "direction"],
  api4f_wind: ["Vref", "Cs", "Ki", "alpha", "z_ref", "direction"],
  ntc2018_wind: ["zone", "a_s", "category", "TR", "vb", "ct", "cd", "rho", "cp_windward", "cp_leeward", "direction"],
};
export const WVC_MOCK_DEFAULT_NAME = { ec8_na: "EC8NA", nbcc2020: "NBCC20", ibc2006: "IBC06", ibc2009: "IBC09", ibc2012: "IBC12",
  ubc97: "UBC97", ntc2018: "NTC18", bs6399_wind: "WINDBS", api4f_wind: "WINDAPI", ntc2018_wind: "WINDNTC" };
export const WVC_MOCK_WIND = ["bs6399_wind", "api4f_wind", "ntc2018_wind"];
