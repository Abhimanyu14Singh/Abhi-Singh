/* SkyFrame — ?mock=1 twin of the Wave B auto-lateral additions
   (skyframe.core.wind_codes_ext ts498_wind / kds41_wind / cfe_wind and the
   NTC-Sismo long-period two-term distribution of
   skyframe.core.seismic_codes_ext).  Same formulas, constants and summary
   shape; js/mock_autolat_ext.js registers them with a few hook lines.
   Everything SI (kN, m, kPa). */

const isNum = v => typeof v === "number" && isFinite(v);
function pos(label, v) {
  if (!isNum(v) || v <= 0) throw new Error(`${label} must be a finite value > 0`);
  return +v;
}
function num(label, v) { if (!isNum(v)) throw new Error(`${label} must be a finite number`); return +v; }
function interp(x, xs, ys) {
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  for (let i = 0; i + 1 < xs.length; i++)
    if (xs[i] <= x && x <= xs[i + 1]) return ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / (xs[i + 1] - xs[i]);
  return ys[ys.length - 1];
}
function facade(model, direction) {
  if (direction !== "X" && direction !== "Y") throw new Error(`direction must be X|Y, got '${direction}'`);
  const st = model.stories || [];
  if (!st.length) throw new Error("model has no stories to load");
  const xs = [], ys = [];
  for (const m of model.members || []) for (const p of [m.pi, m.pj]) { xs.push(+p[0]); ys.push(+p[1]); }
  const lx = xs.length ? Math.max(...xs) - Math.min(...xs) : 0, ly = ys.length ? Math.max(...ys) - Math.min(...ys) : 0;
  const B = direction === "X" ? ly : lx, L = direction === "X" ? lx : ly;
  if (!(B > 0)) throw new Error("plan width perpendicular to the wind is zero");
  const hs = st.map(s => s.height);
  const trib = hs.map((h, i) => h / 2 + (i + 1 < hs.length ? hs[i + 1] / 2 : 0));
  return { B, L, h: Math.max(...st.map(s => s.elevation)), trib };
}
const rowsOf = (model, fac, pfn) => model.stories.map((s, i) => {
  const [p, qz] = pfn(s.elevation);
  return { story: s.name, h: s.elevation, w: null, F: p * fac.trib[i] * fac.B, qz };
});
const windSummary = (code, direction, rows, extra) => ({ code, direction, T: null, W: null,
  V: rows.reduce((a, r) => a + r.F, 0), ...extra, stories: rows });

/* TS 498 Table 4 */
export const WBX_TS498_Q = [[8, 0.5], [20, 0.8], [100, 1.1], [Infinity, 1.3]];
export const wbxTs498Q = z => (WBX_TS498_Q.find(([zt]) => z <= zt) || WBX_TS498_Q[3])[1];
function ts498(model, { cp_windward = 0.8, cp_leeward = -0.4, q_factor = 1, direction = "X" }) {
  const fac = facade(model, direction);
  pos("q_factor", q_factor);
  const cpw = num("cp_windward", cp_windward), cpl = num("cp_leeward", cp_leeward);
  const rows = rowsOf(model, fac, z => { const q = wbxTs498Q(z) * q_factor; return [(cpw + Math.abs(cpl)) * q, q]; });
  return windSummary("ts498_wind", direction, rows, { width: fac.B, cp_total: cpw + Math.abs(cpl) });
}

/* KDS 41 10 15 */
export const WBX_KDS = { A: [20, 0.58, 0.33, 550], B: [15, 0.81, 0.22, 450], C: [10, 1.0, 0.15, 350], D: [5, 1.13, 0.10, 250] };
export const wbxKzr = (z, r) => { const [zb, kb, a, zg] = WBX_KDS[r]; return kb * (Math.min(Math.max(z, zb), zg) / zb) ** a; };
function kds(model, { V0, roughness = "C", KD = 1, Kzt = 1, Iw = 1, GD = 2, cpe_windward = 0.8, cpe_leeward = -0.5, rho_air = 1.225, direction = "X" }) {
  const fac = facade(model, direction);
  if (!WBX_KDS[roughness]) throw new Error("roughness must be one of A, B, C, D");
  for (const [l, v] of [["V0", V0], ["KD", KD], ["Kzt", Kzt], ["Iw", Iw], ["GD", GD], ["rho_air", rho_air]]) pos(l, v);
  const q = z => 0.5 * rho_air * (V0 * KD * wbxKzr(z, roughness) * Kzt * Iw) ** 2 / 1000, qh = q(fac.h);
  const rows = rowsOf(model, fac, z => { const qz = q(z); return [GD * (cpe_windward * qz + Math.abs(cpe_leeward) * qh), qz]; });
  return windSummary("kds41_wind", direction, rows, { qh, width: fac.B, GD });
}

/* CFE MDOC-Viento / NTC-Viento */
export const WBX_CFE = { 1: [0.099, 245, 1.137], 2: [0.128, 315, 1.0], 3: [0.156, 390, 0.881], 4: [0.170, 455, 0.815] };
export const wbxFrz = (z, cat, form = "cfe") => {
  const [a, d, c] = WBX_CFE[cat];
  return (form === "ntc" ? 1 : c) * (Math.min(Math.max(z, 10), d) / 10) ** a;
};
function cfe(model, { VR, category = 2, form = "cfe", FT = 1, G = 1, cpe_windward = 0.8, cpe_leeward = null, direction = "X" }) {
  const fac = facade(model, direction);
  if (!WBX_CFE[category] || typeof category === "boolean") throw new Error("category must be 1, 2, 3 or 4");
  if (form !== "cfe" && form !== "ntc") throw new Error("form must be 'cfe' or 'ntc'");
  for (const [l, v] of [["VR", VR], ["FT", FT], ["G", G]]) pos(l, v);
  const cpl = cpe_leeward == null ? interp(fac.L / fac.B, [1, 2, 4], [-0.5, -0.3, -0.2]) : num("cpe_leeward", cpe_leeward);
  const q = z => 0.613 * G * (FT * wbxFrz(z, category, form) * VR) ** 2 / 1000, qh = q(fac.h);
  const rows = rowsOf(model, fac, z => { const qz = q(z); return [cpe_windward * qz + Math.abs(cpl) * qh, qz]; });
  return windSummary("cfe_wind", direction, rows, { qh, cpe_windward, cpe_leeward: cpl, width: fac.B });
}

export const WBX_MOCK_COMPUTE = { ts498_wind: ts498, kds41_wind: kds, cfe_wind: cfe };
export const WBX_MOCK_PARAMS = {
  ts498_wind: ["cp_windward", "cp_leeward", "q_factor", "direction"],
  kds41_wind: ["V0", "roughness", "KD", "Kzt", "Iw", "GD", "cpe_windward", "cpe_leeward", "rho_air", "direction"],
  cfe_wind: ["VR", "category", "form", "FT", "G", "cpe_windward", "cpe_leeward", "direction"],
};
export const WBX_MOCK_DEFAULT_NAME = { ts498_wind: "WINDTS", kds41_wind: "WINDKDS", cfe_wind: "WINDCFE" };
export const WBX_NTC_KEYS = ["r", "long_period"];

/** Wrap the mock NTC-Sismo generator with the T > Tb reduced two-term
    distribution (default) — F_i = w_i Cs (kh1 h_i + kh2 h_i^2). */
export function wbxWrapNtc(ntc) {
  return (model, p = {}) => {
    const { r = 2, long_period = "reduced", ...rest } = p;
    if (long_period !== "reduced" && long_period !== "linear") throw new Error("long_period must be 'reduced' or 'linear'");
    const s = ntc(model, rest);
    if (!(s.T > rest.Tb) || long_period !== "reduced") return s;
    pos("r", r);
    const W = s.W, q = (rest.Tb / s.T) ** r;
    const swh = s.stories.reduce((a, x) => a + x.w * x.h, 0), swh2 = s.stories.reduce((a, x) => a + x.w * x.h * x.h, 0);
    const kh1 = swh > 0 ? (1 - 0.5 * r * (1 - q)) * W / swh : 0;
    const kh2 = swh2 > 0 ? 0.75 * r * (1 - q) * W / swh2 : 0;
    const stories = s.stories.map(x => ({ ...x, F: x.w * s.Cs * (kh1 * x.h + kh2 * x.h * x.h) }));
    const { stories: _o, ...head } = s;
    return { ...head, V: stories.reduce((a, x) => a + x.F, 0), q, r, kh1, kh2, long_period: "reduced", stories };
  };
}
