/* SkyFrame — v1.16 mock mirror (CONTRACT "Temperature gradients, projected
   loads and auto-lateral generators (ASCE 7-22, EC8, IS 1893, user)").

   ?mock=1 has no backend, so this module ports skyframe.core.autolateral
   (same formulas, same constants, same summary shape) for
     POST /api/pattern/auto-lateral/preview  → mockAutoLateralPreview(model, body)
     POST /api/pattern/auto-lateral          → mockAutoLateralGenerate(model, body)
   and mirrors the v1.16 load validation (thermal_ext.validate_pattern_ext)
   so mock saves reject what the server would (mockValidateLoads116).
   Story weights = story_masses × g (the mock has no compute_story_masses),
   or an explicit `weights` {story: kN} override. Everything stays SI. */

const G = 9.80665;

export const AL_CODES = ["asce7_22", "ec8", "is1893", "user_coefficient", "user_loads", "asce7_22_wind"];
const DEFAULT_NAME = { asce7_22: "ELF22", ec8: "EC8", is1893: "IS1893",
  user_coefficient: "UCOEF", user_loads: "ULOADS", asce7_22_wind: "WIND22" };
const PARAMS = {
  asce7_22: ["SDS", "SD1", "R", "Ie", "TL", "S1", "Ct", "x", "T", "mprs", "weights", "direction"],
  ec8: ["ag", "q", "ground_type", "spectrum_type", "gamma_I", "T1", "structure", "distribution",
    "mode_shape", "S", "TB", "TC", "TD", "beta", "weights", "direction"],
  is1893: ["Z", "R", "I", "soil", "T", "structure", "d", "damping_factor", "weights", "direction"],
  user_coefficient: ["C", "k", "weights", "direction"],
  user_loads: ["loads", "direction"],
  asce7_22_wind: ["V", "exposure", "Kzt", "Kd", "ze", "cp_total", "direction"],
};

const ASCE22_CU = [[0.4, 1.4], [0.3, 1.4], [0.2, 1.5], [0.15, 1.6], [0.1, 1.7]];
export const EC8_SPECTRA = {
  1: { A: [1.0, 0.15, 0.4, 2.0], B: [1.2, 0.15, 0.5, 2.0], C: [1.15, 0.20, 0.6, 2.0],
    D: [1.35, 0.20, 0.8, 2.0], E: [1.4, 0.15, 0.5, 2.0] },
  2: { A: [1.0, 0.05, 0.25, 1.2], B: [1.35, 0.05, 0.25, 1.2], C: [1.5, 0.10, 0.25, 1.2],
    D: [1.8, 0.10, 0.30, 1.2], E: [1.6, 0.05, 0.25, 1.2] },
};
const EC8_CT = { steel_mrf: 0.085, concrete_mrf: 0.075, other: 0.050 };
const IS1893_SOIL = { I: [0.40, 1.00, 0.25], II: [0.55, 1.36, 0.34], III: [0.67, 1.67, 0.42] };
const IS1893_RHO = [[0.10, 0.007], [0.16, 0.011], [0.24, 0.016], [0.36, 0.024]];
const WIND_EXP = { B: [7.5, 3280 * 0.3048], C: [9.8, 2460 * 0.3048], D: [11.5, 1935 * 0.3048] };
const WIND_ZMIN = 15 * 0.3048;

const isNum = v => typeof v === "number" && isFinite(v);
function pos(label, v, allowZero = false) {
  if (!isNum(v) || v < 0 || (v === 0 && !allowZero))
    throw new Error(`${label} must be a finite value ${allowZero ? ">= 0" : "> 0"}`);
  return +v;
}
function common(model, direction) {
  if (direction !== "X" && direction !== "Y") throw new Error(`direction must be X|Y, got '${direction}'`);
  const st = model.stories || [];
  if (!st.length) throw new Error("model has no stories to load");
  const hn = Math.max(...st.map(s => s.elevation));
  if (!(hn > 0)) throw new Error("building height (top-story elevation) must be > 0");
  return hn;
}
export function mockStoryWeights(model, weights) {
  const st = model.stories || [];
  if (weights != null) {
    if (typeof weights !== "object" || Array.isArray(weights)) throw new Error("weights must be a {story: kN} object");
    for (const [k, v] of Object.entries(weights)) {
      if (!st.some(s => s.name === k)) throw new Error(`weights: unknown story '${k}'`);
      pos(`weight of ${k}`, v, true);
    }
    return Object.fromEntries(st.map(s => [s.name, +(weights[s.name] || 0)]));
  }
  const sm = model.story_masses || {};
  return Object.fromEntries(st.map(s => [s.name, (+sm[s.name] || 0) * G]));
}
const kExp = T => T <= 0.5 ? 1 : T >= 2.5 ? 2 : 1 + (T - 0.5) / 2;
function distribute(model, V, w, shape) {
  const denom = model.stories.reduce((a, s) => a + w[s.name] * shape[s.name], 0);
  return model.stories.map(s => ({ story: s.name, h: s.elevation, w: w[s.name],
    F: denom > 0 ? V * w[s.name] * shape[s.name] / denom : 0 }));
}
const summary = (code, direction, T, W, V, stories, extra = {}) =>
  ({ code, direction, T, W, V, ...extra, stories });
function planExtents(model) {
  const xs = [], ys = [];
  for (const m of model.members || []) for (const p of [m.pi, m.pj]) { xs.push(+p[0]); ys.push(+p[1]); }
  if (!xs.length) return [0, 0];
  return [Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)];
}

/* ---------------- ASCE 7-22 */
export function asce22Cu(SD1) {
  if (SD1 >= ASCE22_CU[0][0]) return ASCE22_CU[0][1];
  const last = ASCE22_CU[ASCE22_CU.length - 1];
  if (SD1 <= last[0]) return last[1];
  for (let i = 0; i + 1 < ASCE22_CU.length; i++) {
    const [s0, c0] = ASCE22_CU[i], [s1, c1] = ASCE22_CU[i + 1];
    if (s1 <= SD1 && SD1 <= s0) return c1 + (c0 - c1) * (SD1 - s1) / (s0 - s1);
  }
  return last[1];
}
export function mprsSa(points, T) {
  const pts = points.map(p => [+p[0], +p[1]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (T <= pts[0][0]) return pts[0][1];
  const l = pts[pts.length - 1];
  if (T >= l[0]) return T > 0 ? l[1] * l[0] / T : l[1];
  for (let i = 0; i + 1 < pts.length; i++) {
    const [t0, s0] = pts[i], [t1, s1] = pts[i + 1];
    if (t0 <= T && T <= t1) return t1 > t0 ? s0 + (s1 - s0) * (T - t0) / (t1 - t0) : s1;
  }
  return l[1];
}
function checkMprs(mprs) {
  if (!Array.isArray(mprs) || mprs.length < 2) throw new Error("mprs must be a list of >= 2 [T, Sa] pairs");
  return mprs.map(p => {
    if (!Array.isArray(p) || p.length !== 2) throw new Error("mprs entries must be [T, Sa] pairs");
    return [pos("mprs T", p[0], true), pos("mprs Sa", p[1], true)];
  });
}
function asce7_22(model, { SDS = null, SD1 = null, R = null, Ie = 1, TL = 8, S1 = 0, Ct = 0.0466, x = 0.9,
  T = null, mprs = null, weights = null, direction = "X" }) {
  const hn = common(model, direction);
  for (const [l, v] of [["SD1", SD1], ["R", R], ["Ie", Ie], ["TL", TL], ["Ct", Ct], ["x", x]]) pos(l, v);
  S1 = pos("S1", S1, true);
  const Ta = Ct * hn ** x;
  const Cu = asce22Cu(SD1);
  const Tu = T == null ? Ta : Math.min(pos("T", T), Cu * Ta);
  let Sa, spectrum;
  if (mprs != null) {
    const pts = checkMprs(mprs);
    if (SDS == null) {
      let mx = -Infinity;
      for (let i = 0; i < 481; i++) mx = Math.max(mx, mprsSa(pts, 0.2 + i * 0.01));
      SDS = 0.9 * mx;
    }
    pos("SDS", SDS);
    const tail = [Tu, ...pts.map(p => p[0]).filter(t => t > Tu)];
    Sa = Math.min(SDS, Math.max(...tail.map(t => mprsSa(pts, t))));
    spectrum = "multi_period";
  } else {
    pos("SDS", SDS);
    const Ts = SD1 / SDS;
    Sa = Tu <= Ts ? SDS : Tu <= TL ? SD1 / Tu : SD1 * TL / Tu ** 2;
    spectrum = "two_period";
  }
  const RoIe = R / Ie;
  let Cs = Sa / RoIe;
  let CsMin = Math.max(0.044 * SDS * Ie, 0.01);
  if (S1 >= 0.6) CsMin = Math.max(CsMin, 0.5 * S1 / RoIe);
  Cs = Math.max(Cs, CsMin);
  const w = mockStoryWeights(model, weights);
  const W = Object.values(w).reduce((a, b) => a + b, 0);
  const V = Cs * W;
  const k = kExp(Tu);
  const shape = Object.fromEntries(model.stories.map(s => [s.name, s.elevation ** k]));
  return summary("asce7_22", direction, Tu, W, V, distribute(model, V, w, shape),
    { k, Ta, Cu, Sa, Cs, SDS, spectrum });
}

/* ---------------- EC8 */
export function ec8Sd(T, ag, q, S, TB, TC, TD, beta = 0.2) {
  if (T <= TB) return ag * S * (2 / 3 + T / TB * (2.5 / q - 2 / 3));
  if (T <= TC) return ag * S * 2.5 / q;
  if (T <= TD) return Math.max(ag * S * 2.5 / q * TC / T, beta * ag);
  return Math.max(ag * S * 2.5 / q * TC * TD / T ** 2, beta * ag);
}
function ec8(model, { ag, q, ground_type = "B", spectrum_type = 1, gamma_I = 1, T1 = null, structure = "other",
  distribution = "height", mode_shape = null, S = null, TB = null, TC = null, TD = null, beta = 0.2,
  weights = null, direction = "X" }) {
  const H = common(model, direction);
  const agR = pos("ag", ag);
  pos("q", q); pos("gamma_I", gamma_I);
  if (!EC8_SPECTRA[spectrum_type]) throw new Error("spectrum_type must be 1 or 2");
  if (!EC8_SPECTRA[spectrum_type][ground_type]) throw new Error("ground_type must be one of A, B, C, D, E");
  if (!(structure in EC8_CT)) throw new Error(`structure must be one of ${Object.keys(EC8_CT).join(", ")}`);
  const [S0, TB0, TC0, TD0] = EC8_SPECTRA[spectrum_type][ground_type];
  S = S == null ? S0 : pos("S", S);
  TB = TB == null ? TB0 : pos("TB", TB);
  TC = TC == null ? TC0 : pos("TC", TC);
  TD = TD == null ? TD0 : pos("TD", TD);
  const agd = gamma_I * agR;
  const Tu = T1 == null ? EC8_CT[structure] * H ** 0.75 : pos("T1", T1);
  const Sd = ec8Sd(Tu, agd, q, S, TB, TC, TD, beta);
  const lam = (Tu <= 2 * TC && model.stories.length > 2) ? 0.85 : 1.0;
  const w = mockStoryWeights(model, weights);
  const W = Object.values(w).reduce((a, b) => a + b, 0);
  const Fb = Sd * W * lam;
  let shape;
  if (distribution === "height") shape = Object.fromEntries(model.stories.map(s => [s.name, s.elevation]));
  else if (distribution === "mode") {
    if (!mode_shape || typeof mode_shape !== "object") throw new Error("distribution 'mode' needs mode_shape {story: s_i}");
    shape = {};
    for (const s of model.stories) {
      const v = mode_shape[s.name] ?? 0;
      if (!isNum(v)) throw new Error("mode_shape values must be finite numbers");
      shape[s.name] = +v;
    }
  } else throw new Error("distribution must be 'height' or 'mode'");
  return summary("ec8", direction, Tu, W, Fb, distribute(model, Fb, w, shape),
    { Sd, lambda_: lam, ag: agd, S, TB, TC, TD });
}

/* ---------------- IS 1893 */
export function is1893SaG(T, soil) {
  const [tc, num, tail] = IS1893_SOIL[soil];
  return T <= tc ? 2.5 : T <= 4 ? num / T : tail;
}
export function is1893Rho(Z) {
  if (Z <= IS1893_RHO[0][0]) return IS1893_RHO[0][1];
  const l = IS1893_RHO[IS1893_RHO.length - 1];
  if (Z >= l[0]) return l[1];
  for (let i = 0; i + 1 < IS1893_RHO.length; i++) {
    const [z0, r0] = IS1893_RHO[i], [z1, r1] = IS1893_RHO[i + 1];
    if (z0 <= Z && Z <= z1) return r0 + (r1 - r0) * (Z - z0) / (z1 - z0);
  }
  return l[1];
}
function is1893(model, { Z, R, I = 1, soil = "II", T = null, structure = "rc_mrf", d = null,
  damping_factor = 1, weights = null, direction = "X" }) {
  const h = common(model, direction);
  pos("Z", Z); pos("R", R); pos("I", I); pos("damping_factor", damping_factor);
  if (!IS1893_SOIL[soil]) throw new Error("soil must be one of 'I', 'II', 'III'");
  let Tu;
  if (T != null) Tu = pos("T", T);
  else if (structure === "rc_mrf") Tu = 0.075 * h ** 0.75;
  else if (structure === "steel_mrf") Tu = 0.080 * h ** 0.75;
  else if (structure === "other") {
    if (d == null) { const [lx, ly] = planExtents(model); d = direction === "X" ? lx : ly; }
    Tu = 0.09 * h / Math.sqrt(pos("d", d));
  } else throw new Error("structure must be 'rc_mrf', 'steel_mrf' or 'other'");
  const saG = is1893SaG(Tu, soil);
  let Ah = Z / 2 * saG * damping_factor / (R / I);
  if (Tu <= 0.1) Ah = Math.max(Ah, Z / 2);
  const w = mockStoryWeights(model, weights);
  const W = Object.values(w).reduce((a, b) => a + b, 0);
  const rho = is1893Rho(Z);
  const Vb = Math.max(Ah * W, rho * W);
  const shape = Object.fromEntries(model.stories.map(s => [s.name, s.elevation ** 2]));
  return summary("is1893", direction, Tu, W, Vb, distribute(model, Vb, w, shape), { Ah, Sa_g: saG, rho });
}

/* ---------------- user */
function userCoefficient(model, { C, k = 1, weights = null, direction = "X" }) {
  common(model, direction);
  pos("C", C); pos("k", k);
  const w = mockStoryWeights(model, weights);
  const W = Object.values(w).reduce((a, b) => a + b, 0);
  const V = C * W;
  const shape = Object.fromEntries(model.stories.map(s => [s.name, s.elevation ** k]));
  return summary("user_coefficient", direction, null, W, V, distribute(model, V, w, shape), { C, k });
}
function userLoads(model, { loads, direction = "X" }) {
  if (direction !== "X" && direction !== "Y") throw new Error(`direction must be X|Y, got '${direction}'`);
  if (!Array.isArray(loads) || !loads.length) throw new Error("loads must be a non-empty list of {story, fx, fy}");
  const rows = [];
  for (const ld of loads) {
    const s = ld && (model.stories || []).find(x => x.name === ld.story);
    if (!s) throw new Error("each user load needs a known 'story'");
    const fx = ld.fx ?? 0, fy = ld.fy ?? 0;
    for (const [l, v] of [["fx", fx], ["fy", fy]]) if (!isNum(v)) throw new Error(`user load ${l} must be a finite number`);
    rows.push({ story: s.name, h: s.elevation, w: null, F: direction === "X" ? +fx : +fy, fx: +fx, fy: +fy });
  }
  return summary("user_loads", direction, null, null, rows.reduce((a, r) => a + r.F, 0), rows);
}

/* ---------------- ASCE 7-22 wind */
export function asce22Kz(z, exposure) {
  if (!WIND_EXP[exposure]) throw new Error("exposure must be one of ['B', 'C', 'D']");
  const [alpha, zg] = WIND_EXP[exposure];
  return 2.41 * (Math.max(+z, WIND_ZMIN) / zg) ** (2 / alpha);
}
function asce7_22Wind(model, { V, exposure = "C", Kzt = 1, Kd = 0.85, ze = 0, cp_total = 1.3, direction = "X" }) {
  common(model, direction);
  pos("V", V); pos("Kzt", Kzt); pos("Kd", Kd); pos("cp_total", cp_total);
  if (!isNum(ze)) throw new Error("ze must be a finite number (m)");
  const Ke = Math.exp(-0.000119 * ze);
  const [lx, ly] = planExtents(model);
  const width = direction === "X" ? ly : lx;
  if (!(width > 0)) throw new Error("plan width perpendicular to the wind is zero");
  const hs = model.stories.map(s => s.height);
  const rows = model.stories.map((s, i) => {
    const trib = hs[i] / 2 + (i + 1 < hs.length ? hs[i + 1] / 2 : 0);
    const qz = 0.613 * asce22Kz(s.elevation, exposure) * Kzt * Ke * V ** 2 / 1000;
    const p = qz * Kd * cp_total;
    return { story: s.name, h: s.elevation, w: null, F: p * trib * width, qz };
  });
  return summary("asce7_22_wind", direction, null, null, rows.reduce((a, r) => a + r.F, 0), rows, { Ke, width });
}

const COMPUTE = { asce7_22, ec8, is1893, user_coefficient: userCoefficient, user_loads: userLoads,
  asce7_22_wind: asce7_22Wind };

/** params_from_body mirror → {code, name, ecc, params}; throws the server's message. */
export function mockAlParams(body) {
  if (!body || typeof body !== "object") throw new Error("Request body must be a JSON object");
  const code = body.code;
  if (!AL_CODES.includes(code)) throw new Error(`'code' must be one of ${AL_CODES.join(", ")}`);
  const name = body.name;
  if (name != null && (typeof name !== "string" || !name.trim() || name.length > 60))
    throw new Error("'name' must be a non-empty string (max 60 chars)");
  const params = {};
  for (const [k, v] of Object.entries(body)) if (!["code", "name", "ecc", "model"].includes(k)) params[k] = v;
  const bad = Object.keys(params).filter(k => !PARAMS[code].includes(k)).sort();
  if (bad.length) throw new Error(`unknown parameter(s) for ${code}: ${bad.join(", ")}`);
  return { code, name: name ? name.trim() : null, ecc: body.ecc ?? 0, params };
}

/** POST /api/pattern/auto-lateral/preview (model unchanged). */
export function mockAutoLateralPreview(model, body) {
  const { code, params } = mockAlParams(body);
  return COMPUTE[code](model, params);
}

/** POST /api/pattern/auto-lateral — writes the story-force pattern into `model`
    (replacing a same-name pattern) exactly like autolateral.write_pattern. */
export function mockAutoLateralGenerate(model, body) {
  const { code, name, ecc, params } = mockAlParams(body);
  const sm = COMPUTE[code](model, params);
  const nm = name || DEFAULT_NAME[code];
  if (!isNum(ecc) || Math.abs(ecc) > 0.5) throw new Error("ecc must be a finite value with |ecc| <= 0.5");
  const story_forces = sm.stories.map(r => ({ story: r.story,
    fx: r.fx ?? (sm.direction === "X" ? r.F : 0), fy: r.fy ?? (sm.direction === "Y" ? r.F : 0) }));
  const pat = { name: nm, kind: code === "asce7_22_wind" ? "wind" : "quake",
    member_loads: [], area_loads: [], story_forces, thermal_loads: [], self_weight_factor: 0,
    accidental_torsion: false, ecc: 0.05 };
  if (ecc) { pat.accidental_torsion = true; pat.ecc = +ecc; }
  model.patterns = model.patterns || {};
  model.patterns[nm] = pat;
  return { model, summary: sm, pattern: pat };
}

/* ---------------- v1.16 load validation mirror (thermal_ext.validate_pattern_ext) */
const PROJ_DIRS = ["gravity", "global_x", "global_y", "global_z"];
export function mockValidateLoads116(model) {
  if (!model) return "";
  const shells = new Map((model.shells || []).map(s => [s.uid, s]));
  for (const [pn, p] of Object.entries(model.patterns || {})) {
    for (const tl of p.thermal_loads || [])
      for (const k of ["grad2", "grad3"])
        if (k in tl && !isNum(tl[k])) return `Pattern ${pn}: thermal load ${k} must be a finite number (deg C / m)`;
    for (const ml of p.member_loads || []) {
      if (!ml.projected) continue;
      if (typeof ml.projected !== "boolean") return `Pattern ${pn}: member load projected must be a boolean`;
      if (!["udl", "trapezoid"].includes(ml.kind || "udl")) return `Pattern ${pn}: projected member loads must be distributed (udl | trapezoid)`;
      if (!PROJ_DIRS.includes(ml.direction || "gravity")) return `Pattern ${pn}: projected member loads need a global direction (${PROJ_DIRS.join(", ")})`;
    }
    for (const st of p.shell_thermal_loads || []) {
      const r = shells.get(st.region_uid);
      if (!r) return `Pattern ${pn}: shell thermal load references unknown shell region '${st.region_uid}'`;
      if (r.behavior !== "shell") return `Pattern ${pn}: shell thermal load on membrane region '${st.region_uid}' (needs behavior 'shell')`;
      if (!isNum(st.dT ?? 0) || !isNum(st.grad3 ?? 0)) return `Pattern ${pn}: shell thermal dT / grad3 must be finite numbers`;
    }
    for (const jt of p.joint_temperatures || [])
      if (!Array.isArray(jt.point) || jt.point.length !== 3 || !jt.point.every(isNum) || !isNum(jt.dT))
        return `Pattern ${pn}: joint temperature needs a finite 3D point and a finite dT`;
    if (p.ecc != null && !isNum(p.ecc)) return `Pattern ${pn}: ecc must be finite`;
  }
  return "";
}
