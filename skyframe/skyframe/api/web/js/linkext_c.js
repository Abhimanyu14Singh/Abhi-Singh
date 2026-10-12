/* SkyFrame — wave-C link properties (CONTRACT "Wave C links and hinges").

   Registers in the Link Properties dialog (js/linkhyst.js HYST_TYPES +
   LH_EXT hooks) and ME.LINK_TYPES:

     linear_coupled         {K: 6×6}                     coupled linear link
     multilinear_elastic    {curves: {U1..R3: [[d,F]]}, k_lin?: 6}
     multilinear_degrading  {points, points_neg?, damage1?, damage2?, unload_beta?, kv?}
     multilinear_concrete   {points, points_neg?, pinch_x?, pinch_y?, damage1?, damage2?, unload_beta?, kv?}
     multilinear_isotropic  {points (2), kv?}
     multilinear_brb        {points (2), points_neg?, R0?, iso_b?, iso_rho?, kv?}
     hdr_isolator           {Gr Kbulk D1 D2 ts tr n a1 a2 a3 b1 b2 b3 c1 c2 c3 c4, kc?, phi_m?, ac?}

   and the extra optional keys every nonlinear type may carry (written only
   when set — load + save byte-identical): k_eff / c_eff (effective linear
   stiffness / damping for linear analyses), mu_fast / rate / n_slow /
   n_fast / mu_max_fact (velocity / normal-force dependent friction) and
   mu3 / R3 / mu_t / R_t (T/C isolator per direction, friction in tension).
   Every number goes through js/units.js; the model stays SI. */

import { HYST_TYPES as LC_HYST, LH_EXT as LC_EXT, LH_UNIT_KIND as LC_UK } from "./linkhyst.js";
import { LINK_TYPES as LC_LINK_TYPES, LINK_EXTRA as LC_LINK_EXTRA } from "./modeledit.js";
import LCU from "./units.js";
import { b9h as lcH, b9group as lcGroup, b9num as lcNum, b9esc as lcEsc } from "./b9common.js";

const isNum = v => typeof v === "number" && isFinite(v);
const DOFS = ["U1", "U2", "U3", "R1", "R2", "R3"];
const KV = ["kv", "kN/m", 1e7, "Vertical (axial) stiffness kv"];
const BB = [[0.01, 100], [0.05, 140]];

export const LC_TYPES = {
  linear_coupled: {
    label: "Linear · coupled 6×6", letter: "C",
    note: "Linear link with a full symmetric 6×6 stiffness matrix in the link local axes (U1 = axis i→j, +Z at zero length). Realised exactly by parallel springs; the trace of the U–R coupling block must be zero (no axial–torsion screw coupling). Same stiffness in every analysis.",
    params: [], opt: [], linear: true,
  },
  multilinear_elastic: {
    label: "Multilinear elastic · per DOF", letter: "E",
    note: "ETABS MultiLinear Elastic with each DOF defined separately: a (d, F) curve per local DOF (all d > 0 = mirrored, else through (0, 0)) or a linear stiffness. Loads and unloads on the curve.",
    params: [], opt: [],
  },
  multilinear_degrading: {
    label: "Multilinear plastic · degrading", letter: "D",
    note: "Takeda-type peak-oriented hysteresis with degrading unloading stiffness k0·μ^-β (β = unload_beta, default 0.5) and optional ductility / energy damage. Axial on an inclined link; both shears + kv on a vertical / zero-length link.",
    params: [], opt: [["unload_beta", "–", 0.5, "Unloading-stiffness exponent β"], ["damage1", "–", 0, "Ductility damage"], ["damage2", "–", 0, "Energy damage"], KV],
    points: true, maxPts: 3, neg: true, defaultPoints: BB,
  },
  multilinear_concrete: {
    label: "Multilinear plastic · concrete", letter: "O",
    note: "Pinched peak-oriented hysteresis (concrete): reloads through the pinch point (pinch_x, pinch_y), default 0.8 / 0.2. Axial on an inclined link; both shears + kv on a vertical / zero-length link.",
    params: [], opt: [["pinch_x", "–", 0.8, "Pinch point — deformation fraction"], ["pinch_y", "–", 0.2, "Pinch point — force fraction"],
      ["unload_beta", "–", 0, "Unloading-stiffness exponent β"], ["damage1", "–", 0, "Ductility damage"], ["damage2", "–", 0, "Energy damage"], KV],
    points: true, maxPts: 3, neg: true, defaultPoints: BB,
  },
  multilinear_isotropic: {
    label: "Multilinear plastic · isotropic", letter: "Y",
    note: "Bilinear isotropic hardening (exactly 2 points): a reversal from the peak yields at minus the largest force reached (exact at the second point). Direct integration only.",
    params: [], opt: [KV], points: true, maxPts: 2, neg: false, defaultPoints: BB,
  },
  multilinear_brb: {
    label: "Multilinear plastic · BRB hardening", letter: "Q",
    note: "BRB hardening: asymmetric kinematic hardening (Steel4) from the tension / compression second points (same yield point both sides), optional isotropic hardening iso_b.",
    params: [], opt: [["R0", "–", 20, "Transition curvature R0"], ["iso_b", "–", 0, "Isotropic hardening ratio"], ["iso_rho", "–", 1, "Isotropic rate ρ"], KV],
    points: true, maxPts: 2, neg: true, defaultPoints: [[0.002, 400], [0.03, 520]],
  },
  hdr_isolator: {
    label: "High-damping rubber isolator", letter: "H",
    note: "OpenSees HDR element (Grant et al. 2004): elastic part a1·u + a2·u³ + a3·u⁵, hysteretic b1..b3, scragging c1..c4 (parameters in kN·m units, as the element). Vertical or zero-length link. Direct integration only.",
    params: [["Gr", "kPa", 400, "Rubber shear modulus Gr"], ["Kbulk", "kPa", 2e6, "Rubber bulk modulus"], ["D1", "m", 0.1, "Inner diameter D1"],
      ["D2", "m", 0.9, "Outer diameter D2"], ["ts", "m", 0.003, "Steel shim thickness ts"], ["tr", "m", 0.008, "Rubber layer thickness tr"],
      ["n", "–", 20, "Rubber layers n"], ["a1", "kN/m", 800, "Grant a1"], ["a2", "–", 2000, "Grant a2 (kN/m³)"], ["a3", "–", 5000, "Grant a3 (kN/m⁵)"],
      ["b1", "–", 10, "Grant b1"], ["b2", "–", 5, "Grant b2"], ["b3", "–", 2, "Grant b3"], ["c1", "–", 0, "Scragging c1"], ["c2", "–", 0, "Scragging c2"],
      ["c3", "–", 0, "Scragging c3"], ["c4", "–", 0, "Scragging c4"]],
    opt: [["kc", "–", 10, "Cavitation parameter kc"], ["phi_m", "–", 0.5, "Damage index φM"], ["ac", "–", 1, "Strength-degradation ac"]],
  },
};
LC_UK["kPa"] = "modulus";

const FRICTION = ["fp_isolator", "friction_spring", "tc_fp_isolator"];
const FRICTION_KEYS = [["mu_fast", "–", "Fast friction coefficient μ_fast"], ["rate", "s/m", "Rate parameter a (μ(v) = μf − (μf − μ)·e^(−a|v|))"],
  ["n_slow", "–", "Pressure exponent (slow)"], ["n_fast", "–", "Pressure exponent (fast)"], ["mu_max_fact", "–", "Max μ factor"]];
const TC_KEYS = [["mu3", "–", "Friction μ along U3"], ["R3", "m", "Radius R along U3"], ["mu_t", "–", "Friction μ in tension"], ["R_t", "m", "Radius R in tension"]];
/** extra keys a type keeps (js/modeledit.js LINK_EXTRA hook) */
export function lcExtraKeys(t) {
  if (!t || t === "elastic" || t === "linear_coupled") return [];
  const k = ["k_eff", "c_eff"];
  if (FRICTION.includes(t)) k.push(...FRICTION_KEYS.map(r => r[0]));
  if (t === "tc_fp_isolator") k.push(...TC_KEYS.map(r => r[0]));
  return k;
}

const six = v => Array.isArray(v) && v.length === 6 && v.every(isNum);
const cleanPts = a => (Array.isArray(a) ? a : []).filter(p => Array.isArray(p) && p.length === 2 && p.every(isNum)).map(p => [+p[0], +p[1]]);
function lcNormalize(t, def) {
  return src => {
    src = src && typeof src === "object" ? src : {};
    const out = {};
    if (t === "linear_coupled") {
      const K = Array.isArray(src.K) && src.K.length === 6 && src.K.every(six) ? src.K.map(r => r.map(Number)) : null;
      out.K = K || [1e5, 1e5, 1e5, 1e4, 1e4, 1e4].map((d, i) => DOFS.map((_, j) => (i === j ? d : 0)));
      return out;
    }
    if (t === "multilinear_elastic") {
      const cv = {};
      if (src.curves && typeof src.curves === "object")
        for (const d of DOFS) { const p = cleanPts(src.curves[d]); if (p.length) cv[d] = p; }
      out.curves = Object.keys(cv).length || six(src.k_lin) ? cv : { U2: [[0.01, 50], [0.05, 80]] };
      if (six(src.k_lin)) out.k_lin = src.k_lin.map(Number);
      return out;
    }
    const allowed = new Set([...def.params.map(p => p[0]), ...def.opt.map(p => p[0]), ...(def.points ? ["points"] : []), ...(def.neg ? ["points_neg"] : [])]);
    for (const k of Object.keys(src)) {
      if (!allowed.has(k)) continue;
      if (k === "points" || k === "points_neg") { const p = cleanPts(src[k]); if (p.length) out[k] = p; }
      else if (isNum(src[k])) out[k] = +src[k];
    }
    for (const [k, , dv] of def.params) if (!(k in out)) out[k] = dv;
    if (def.points && !out.points) out.points = def.defaultPoints.map(p => [...p]);
    return out;
  };
}

/* ---------------------------------------------------------------- laws */
/** Peak-oriented law (OpenSees Hysteretic, no damage) — mirrors core/fna_devices_c.PeakOriented. */
export function peakOrientedPath(pos, neg, px, py, beta, us) {
  const env = (pts, u) => {
    const a = [[0, 0], ...pts];
    for (let i = 1; i < a.length; i++) if (Math.abs(u) <= Math.abs(a[i][0])) return a[i - 1][1] + (a[i][1] - a[i - 1][1]) * (u - a[i - 1][0]) / (a[i][0] - a[i - 1][0]);
    const [d0, f0] = a[a.length - 2], [d1, f1] = a[a.length - 1];
    return f1 + (f1 - f0) / (d1 - d0) * (u - d1);
  };
  const d1p = pos[0][0], d1n = neg[0][0], k0p = pos[0][1] / d1p, k0n = neg[0][1] / d1n;
  const kf = (u, d1) => { const r = Math.pow(u / d1, beta); return r < 1 ? 1 : 1 / r; };
  let u = 0, F = 0, umax = d1p, umin = d1n, rPu = 0, rNu = 0, load = 0;
  const out = [[0, 0]];
  for (const x of us) {
    const du = x - u;
    let Fn = F;
    if (x >= umax) { umax = x; load = 1; Fn = env(pos, x); }
    else if (x <= umin) { umin = x; load = 2; Fn = env(neg, x); }
    else if (du > 0) {
      const kn = k0n * kf(umin, d1n), kp = k0p * kf(umax, d1p);
      if (load === 2 && F <= 0) rNu = u - F / kn;
      load = 1;
      const um = Math.max(umax, d1p), fm = env(pos, um), r2 = um - (1 - py) * fm / kp, rch = rNu + (r2 - rNu) * px;
      if (x < rNu) { Fn = F + kn * du; if (Fn >= 0) Fn = 0; }
      else { const el = F + kp * du; const rl = x < rch ? (x - rNu) * fm * py / (rch - rNu) : py * fm + (x - rch) * (1 - py) * fm / (um - rch); Fn = Math.min(el, rl); }
    } else if (du < 0) {
      const kn = k0n * kf(umin, d1n), kp = k0p * kf(umax, d1p);
      if (load === 1 && F >= 0) rPu = u - F / kp;
      load = 2;
      const um = Math.min(umin, d1n), fm = env(neg, um), r2 = um - (1 - py) * fm / kn, rch = rPu + (r2 - rPu) * px;
      if (x > rPu) { Fn = F + kp * du; if (Fn <= 0) Fn = 0; }
      else { const el = F + kn * du; const rl = x > rch ? (x - rPu) * fm * py / (rch - rPu) : py * fm + (x - rch) * (1 - py) * fm / (um - rch); Fn = Math.max(el, rl); }
    }
    u = x; F = Fn; out.push([u, F]);
  }
  return out;
}
export const frictionMu = (p, v) => (isNum(p.mu_fast) && isNum(p.rate)) ? p.mu_fast - (p.mu_fast - p.mu) * Math.exp(-p.rate * Math.abs(v)) : p.mu;
const hdrElastic = (p, u) => p.a1 * u + p.a2 * u ** 3 + p.a3 * u ** 5;

const g = (p, k, dv) => isNum(p[k]) ? p[k] : dv;
const negOf = p => (p.points_neg && p.points_neg.length ? p.points_neg : p.points.map(([d, f]) => [-d, -f]));
function cyclePath(D, Dn) {
  const us = [];
  const leg = (a, b) => { for (let i = 1; i <= 40; i++) us.push(a + (b - a) * i / 40); };
  leg(0, 0.5 * D); leg(0.5 * D, 0.5 * Dn); leg(0.5 * Dn, D); leg(D, Dn); leg(Dn, D);
  return us;
}
const PREVIEW = {
  linear_coupled: () => null,
  multilinear_elastic: p => {
    const d = Object.keys(p.curves || {})[0];
    if (!d) return null;
    const pts = p.curves[d], all = pts.every(q => q[0] > 0) ? [...pts.map(([a, b]) => [-a, -b]).reverse(), [0, 0], ...pts] : pts;
    return { series: [{ pts: all, cls: "b9-s-bb" }], marks: pts, note: `Curve ${d} (elastic — loads and unloads on the curve).` };
  },
  hdr_isolator: p => {
    if (![p.a1, p.a2, p.a3].every(isNum)) return null;
    const D = 0.3, pts = [];
    for (let i = -30; i <= 30; i++) { const u = D * i / 30; pts.push([u, hdrElastic(p, u)]); }
    return { series: [{ pts, cls: "b9-s-bb" }], marks: [], note: "Grant elastic component a1·u + a2·u³ + a3·u⁵ (the hysteretic part adds the loop)." };
  },
};
for (const t of ["multilinear_degrading", "multilinear_concrete", "multilinear_isotropic", "multilinear_brb"]) {
  PREVIEW[t] = p => {
    const pos = p.points || [];
    if (pos.length < 2 || !pos.every(q => q[0] > 0 && q[1] > 0)) return null;
    const neg = negOf(p), D = pos[pos.length - 1][0], Dn = neg[neg.length - 1][0];
    const bb = [...neg.slice().reverse(), [0, 0], ...pos];
    const series = [{ pts: bb, cls: "b9-s-bb", dash: "4 3" }];
    if (t === "multilinear_degrading" || t === "multilinear_concrete") {
      const def = LC_TYPES[t].opt;
      const dv = k => def.find(r => r[0] === k)[2];
      const px = t === "multilinear_concrete" ? g(p, "pinch_x", dv("pinch_x")) : 1, py = t === "multilinear_concrete" ? g(p, "pinch_y", dv("pinch_y")) : 1;
      series.push({ pts: peakOrientedPath(pos, neg, px, py, g(p, "unload_beta", dv("unload_beta")), cyclePath(D, Dn)), cls: "b9-s-cyc" });
    }
    return { series, marks: [...pos, ...neg], note: series.length > 1 ? "Dashed: backbone · solid: illustrative cycle (same rule as the engine)." : "Backbone (monotonic)." };
  };
}

/* ---------------------------------------------------------------- validation */
function psdOk(K) {            // Cholesky with a semi-definite tolerance
  const n = 6, A = K.map(r => r.slice()), tol = 1e-9 * Math.max(...K.flat().map(Math.abs), 1e-300);
  for (let j = 0; j < n; j++) {
    let d = A[j][j];
    for (let k = 0; k < j; k++) d -= A[j][k] ** 2;
    if (d < -tol) return false;
    const s = d > tol ? Math.sqrt(d) : 0;
    A[j][j] = s;
    for (let i = j + 1; i < n; i++) {
      let v = A[i][j];
      for (let k = 0; k < j; k++) v -= A[i][k] * A[j][k];
      if (s === 0) { if (Math.abs(v) > 1e-6 * Math.max(1, Math.abs(K[i][j]))) return false; A[i][j] = 0; } else A[i][j] = v / s;
    }
  }
  return true;
}
function backboneMsg(w, pts, s, key, nmax) {
  if (!Array.isArray(pts) || pts.length < 2 || pts.length > nmax) return `${w}: ${key} needs 2..${nmax} points.`;
  let prev = 0;
  for (const [d, f] of pts) {
    if (!(s * d > s * prev)) return `${w}: ${key} displacements must move strictly away from 0.`;
    if (!(s * f > 0)) return `${w}: ${key} forces must be ${s > 0 ? "> 0" : "< 0"}.`;
    prev = d;
  }
  return null;
}
export function lcValidate(uid, t, p) {
  const w = `Link ${uid} (${t})`;
  if (t === "linear_coupled") {
    const K = p.K;
    if (!(Array.isArray(K) && K.length === 6 && K.every(six))) return `${w}: K must be a 6×6 matrix of numbers.`;
    if (K.every(r => r.every(v => v === 0))) return `${w}: K must not be all zero.`;
    const sc = Math.max(...K.flat().map(Math.abs));
    for (let i = 0; i < 6; i++) for (let j = 0; j < 6; j++) if (Math.abs(K[i][j] - K[j][i]) > 1e-9 * sc) return `${w}: K must be symmetric.`;
    if (!psdOk(K)) return `${w}: K must be positive semi-definite.`;
    const tr = K[0][3] + K[1][4] + K[2][5], ref = Math.max(...[0, 1, 2].flatMap(i => [3, 4, 5].map(j => Math.abs(K[i][j]))), Math.sqrt(Math.max(K[0][0], K[1][1], K[2][2], 0) * Math.max(K[3][3], K[4][4], K[5][5], 0)), 1e-300);
    if (Math.abs(tr) > 1e-8 * ref) return `${w}: the trace of the U–R coupling block (K14 + K25 + K36) must be 0 — axial–torsion (screw) coupling cannot be realised.`;
    return null;
  }
  if (t === "multilinear_elastic") {
    const cv = p.curves || {};
    const kl = six(p.k_lin) ? p.k_lin : [0, 0, 0, 0, 0, 0];
    if (!Object.keys(cv).length && !kl.some(v => v > 0)) return `${w}: define at least one curve or a linear stiffness.`;
    for (const [d, pts] of Object.entries(cv)) {
      if (!DOFS.includes(d)) return `${w}: unknown DOF ${d}.`;
      if (!pts.length) return `${w}: curve ${d} needs points.`;
      for (let i = 1; i < pts.length; i++) if (!(pts[i][0] > pts[i - 1][0])) return `${w}: curve ${d} displacements must be strictly increasing.`;
      if (pts.every(q => q[0] > 0)) { if (!(pts[0][1] > 0)) return `${w}: curve ${d} first force must be > 0.`; }
      else if (!pts.some(q => q[0] === 0 && q[1] === 0)) return `${w}: curve ${d} must pass through (0, 0).`;
      if (kl[DOFS.indexOf(d)] > 0) return `${w}: DOF ${d} has both a curve and k_lin.`;
    }
    if (kl.some(v => v < 0)) return `${w}: k_lin entries must be ≥ 0.`;
    return null;
  }
  const def = LC_TYPES[t];
  if (def && def.points) {
    const e = backboneMsg(w, p.points, 1, "points", def.maxPts); if (e) return e;
    if (p.points_neg) {
      const e2 = backboneMsg(w, p.points_neg, -1, "points_neg", def.maxPts); if (e2) return e2;
      if (p.points_neg.length !== p.points.length) return `${w}: points_neg must have as many points as points.`;
    }
    for (const k of ["damage1", "damage2", "unload_beta", "iso_b"]) if (k in p && !(p[k] >= 0)) return `${w}: ${k} must be ≥ 0.`;
    for (const k of ["pinch_x", "pinch_y"]) if (k in p && !(p[k] > 0 && p[k] <= 1)) return `${w}: ${k} must be in (0, 1].`;
    for (const k of ["R0", "iso_rho", "kv"]) if (k in p && !(p[k] > 0)) return `${w}: ${k} must be > 0.`;
    if ((t === "multilinear_isotropic" || t === "multilinear_brb") && p.points[1][1] < p.points[0][1]) return `${w}: the post-yield slope must be ≥ 0.`;
    if (t === "multilinear_brb" && p.points_neg) {
      const n0 = p.points_neg[0];
      if (Math.abs(Math.abs(n0[0]) - p.points[0][0]) > 1e-9 * p.points[0][0] || Math.abs(Math.abs(n0[1]) - p.points[0][1]) > 1e-9 * p.points[0][1]) return `${w}: a BRB yields at the same point on both sides.`;
    }
    return null;
  }
  if (t === "hdr_isolator") {
    for (const [k] of def.params) if (!isNum(p[k])) return `${w}: ${k} is required.`;
    for (const k of ["Gr", "Kbulk", "D2", "ts", "tr", "n"]) if (!(p[k] > 0)) return `${w}: ${k} must be > 0.`;
    if (!(p.D1 >= 0 && p.D1 < p.D2)) return `${w}: D1 must satisfy 0 ≤ D1 < D2.`;
    if (Math.abs(p.n - Math.round(p.n)) > 1e-9) return `${w}: n must be an integer.`;
    for (const k of ["kc", "phi_m", "ac"]) if (k in p && !(p[k] > 0)) return `${w}: ${k} must be > 0.`;
    return null;
  }
  return null;
}
/** extra keys on any type (k_eff / c_eff, friction, T/C) → message | null */
export function lcValidateExtra(uid, t, p) {
  const w = `Link ${uid} (${t})`;
  if ("k_eff" in p) {
    if (!six(p.k_eff) || p.k_eff.some(v => v < 0)) return `${w}: k_eff needs 6 values ≥ 0.`;
    if (!p.k_eff.some(v => v > 0)) return `${w}: k_eff needs at least one entry > 0.`;
  }
  if ("c_eff" in p) {
    if (!("k_eff" in p)) return `${w}: c_eff needs k_eff.`;
    if (!six(p.c_eff) || p.c_eff.some(v => v < 0)) return `${w}: c_eff needs 6 values ≥ 0.`;
  }
  if (FRICTION.includes(t) && FRICTION_KEYS.some(([k]) => k in p)) {
    if (!(isNum(p.mu_fast) && isNum(p.rate))) return `${w}: velocity-dependent friction needs both mu_fast and rate.`;
    for (const [k] of FRICTION_KEYS) if (k in p && !(p[k] > 0)) return `${w}: ${k} must be > 0.`;
  }
  if (t === "tc_fp_isolator") {
    for (const [k] of TC_KEYS) if (k in p && !(p[k] > 0)) return `${w}: ${k} must be > 0.`;
    if ("R_t" in p && !("mu_t" in p)) return `${w}: R_t needs mu_t.`;
    if ("mu_t" in p && !(g(p, "kt", 0) > 0)) return `${w}: friction in tension (mu_t) needs a hold-down stiffness kt > 0.`;
  }
  return null;
}

/* ---------------------------------------------------------------- editors */
const kindOfK = (i, j) => (i < 3 && j < 3 ? "stiffness" : i >= 3 && j >= 3 ? "rot_stiffness" : "force");
function matrixEditor(params, changed) {
  const gK = lcGroup("Stiffness matrix K (local U1..R3, symmetric)");
  const grid = lcH("div", { class: "lc-kgrid", id: "lcKGrid", style: "display:grid;grid-template-columns:auto repeat(6,minmax(64px,1fr));gap:2px;font-size:11px" });
  grid.appendChild(lcH("span"));
  for (const d of DOFS) grid.appendChild(lcH("span", { class: "muted", text: d }));
  const cells = [];
  DOFS.forEach((di, i) => {
    grid.appendChild(lcH("span", { class: "muted", text: di }));
    cells.push([]);
    DOFS.forEach((dj, j) => {
      const f = lcNum(kindOfK(i, j), params.K[i][j], { id: `lcK${i}${j}`, title: `K${i + 1}${j + 1} (${LCU.label(kindOfK(i, j))})`, style: "width:100%" });
      f.el.addEventListener("change", () => {
        const v = f.get();
        if (!isNum(v)) { f.set(params.K[i][j]); return; }
        params.K[i][j] = v; params.K[j][i] = v; cells[j] && cells[j][i] && cells[j][i].set(v); changed();
      });
      cells[i].push(f);
      grid.appendChild(f.el);
    });
  });
  gK.append(grid, lcH("p", { class: "muted b9-small", text: `Units: translational ${LCU.label("stiffness")}, rotational ${LCU.label("rot_stiffness")}, coupling ${LCU.label("force")}. Editing a cell also sets its symmetric partner.` }));
  return gK;
}
const ptsText = pts => (pts || []).map(([d, f]) => `${LCU.inputValue("disp", d)}, ${LCU.inputValue("force", f)}`).join("; ");
function parsePts(txt) {
  const out = [];
  for (const part of String(txt).split(";")) {
    const s = part.trim(); if (!s) continue;
    const [a, b] = s.split(",").map(x => x.trim());
    const d = LCU.parse("disp", a), f = LCU.parse("force", b);
    if (!isNum(d) || !isNum(f)) return null;
    out.push([d, f]);
  }
  return out;
}
function curvesEditor(params, changed) {
  const gC = lcGroup("DOF definitions (each DOF separately)");
  params.curves = params.curves || {};
  DOFS.forEach((d, i) => {
    const rot = i >= 3;
    const mode = params.curves[d] ? "curve" : (six(params.k_lin) && params.k_lin[i] > 0 ? "linear" : "none");
    const sel = lcH("select", { id: `lcMleMode${d}` });
    sel.innerHTML = [["none", "Free"], ["linear", "Linear k"], ["curve", "Multilinear curve"]].map(([v, l]) => `<option value="${v}"${v === mode ? " selected" : ""}>${l}</option>`).join("");
    const kf = lcNum(rot ? "rot_stiffness" : "stiffness", six(params.k_lin) ? params.k_lin[i] : 0, { id: `lcMleK${d}`, style: "width:110px" });
    const tx = lcH("input", { type: "text", id: `lcMleCurve${d}`, value: ptsText(params.curves[d]), placeholder: `d, F; d, F … (${LCU.label("disp")}, ${LCU.label("force")})`, style: "flex:1;min-width:0" });
    const sync = () => { kf.el.style.display = sel.value === "linear" ? "" : "none"; tx.style.display = sel.value === "curve" ? "" : "none"; };
    const write = () => {
      const kl = six(params.k_lin) ? params.k_lin.slice() : [0, 0, 0, 0, 0, 0];
      delete params.curves[d]; kl[i] = 0;
      if (sel.value === "linear") { const v = kf.get(); kl[i] = isNum(v) && v > 0 ? v : 0; }
      if (sel.value === "curve") { const p = parsePts(tx.value); if (p && p.length) params.curves[d] = p; }
      if (kl.some(v => v > 0)) params.k_lin = kl; else delete params.k_lin;
      changed();
    };
    sel.addEventListener("change", () => { sync(); write(); });
    kf.el.addEventListener("change", write);
    tx.addEventListener("change", write);
    gC.appendChild(lcH("div", { class: "asn-num-row b9-num-row", style: "display:flex;gap:6px;align-items:center" }, [lcH("b", { text: d, style: "width:24px" }), sel, kf.el, tx]));
    sync();
  });
  gC.appendChild(lcH("p", { class: "muted b9-small", text: "A curve with only positive d is mirrored through the origin; otherwise it must include (0, 0). Rotational curves use the same d, F columns in rad and moment units of the model." }));
  return gC;
}
/** Effective properties + friction extras for any nonlinear type. */
function extraGroups(type, params, changed) {
  if (!type || type === "elastic" || type === "linear_coupled") return null;
  const wrap = lcH("div", { id: "lcExtra" });
  const gE = lcGroup("Effective (linear) properties");
  const on = lcH("input", { type: "checkbox", id: "lcEffOn" });
  on.checked = six(params.k_eff);
  const box = lcH("div", { id: "lcEffBox" });
  const render = () => {
    box.textContent = "";
    if (!on.checked) return;
    const k = six(params.k_eff) ? params.k_eff : [0, 0, 0, 0, 0, 0], c = six(params.c_eff) ? params.c_eff : null;
    DOFS.forEach((d, i) => {
      const kf = lcNum(i < 3 ? "stiffness" : "rot_stiffness", k[i], { id: `lcKeff${d}`, min: "0", style: "width:100px" });
      const cf = lcNum(i < 3 ? "damping_coeff" : "rot_stiffness", c ? c[i] : 0, { id: `lcCeff${d}`, min: "0", style: "width:100px" });
      kf.el.addEventListener("change", () => { const v = kf.get(); const a = (six(params.k_eff) ? params.k_eff : [0, 0, 0, 0, 0, 0]).slice(); a[i] = isNum(v) && v >= 0 ? v : 0; params.k_eff = a; changed(); });
      cf.el.addEventListener("change", () => {
        const v = cf.get(); const a = (six(params.c_eff) ? params.c_eff : [0, 0, 0, 0, 0, 0]).slice(); a[i] = isNum(v) && v >= 0 ? v : 0;
        if (a.some(x => x > 0)) params.c_eff = a; else delete params.c_eff; changed();
      });
      box.appendChild(lcH("label", { class: "asn-num-row b9-num-row" }, [lcH("span", { html: `<b>${d}</b>` }), kf.el,
        lcH("span", { class: "asn-unit", text: LCU.label(i < 3 ? "stiffness" : "rot_stiffness") }), cf.el,
        lcH("span", { class: "asn-unit", text: i < 3 ? LCU.label("damping_coeff") : `${LCU.label("moment")}·s/rad` })]));
    });
  };
  on.addEventListener("change", () => {
    if (on.checked) params.k_eff = six(params.k_eff) ? params.k_eff : [g(params, "kv", 0), g(params, "k_init", g(params, "k1", 0)), g(params, "k_init", g(params, "k1", 0)), 0, 0, 0];
    else { delete params.k_eff; delete params.c_eff; }
    render(); changed();
  });
  gE.append(lcH("label", { class: "dlg-chk" }, [on, lcH("span", { text: "Use effective stiffness k_eff / damping c_eff in linear analyses (static, modal, RS, linear TH) and as the FNA basis" })]), box);
  render();
  wrap.appendChild(gE);
  const scalarRows = (title, rows) => {
    const gx = lcGroup(title);
    for (const [k, u, lbl] of rows) {
      const kind = u === "s/m" ? "none" : (LC_UK[u] || "none");
      const f = lcNum(kind, isNum(params[k]) ? params[k] : null, { id: `lcX_${k}`, placeholder: "—" });
      f.el.addEventListener("change", () => { const v = f.get(); if (v == null || !isNum(v)) delete params[k]; else params[k] = v; changed(); });
      gx.appendChild(lcH("label", { class: "asn-num-row b9-num-row" }, [lcH("span", { html: `<b>${lcEsc(k)}</b> <span class="muted">${lcEsc(lbl)} · optional</span>` }), f.el,
        lcH("span", { class: "asn-unit", text: u === "s/m" ? `s/${LCU.label("length")}` : kind === "none" ? "" : LCU.label(kind) })]));
    }
    return gx;
  };
  if (FRICTION.includes(type)) wrap.appendChild(scalarRows("Velocity-dependent friction", FRICTION_KEYS));
  if (type === "tc_fp_isolator") wrap.appendChild(scalarRows("T/C: per direction · tension", TC_KEYS));
  return wrap;
}

/** Register (idempotent). */
export function initLinkExtC(sky) {
  for (const [t, def] of Object.entries(LC_TYPES)) {
    if (!LC_HYST[t]) LC_HYST[t] = def;
    if (!LC_LINK_TYPES[t]) LC_LINK_TYPES[t] = { ...def, params: def.params.map(([k, u, dv]) => [k, u, dv]), normalize: lcNormalize(t, def), hysteresis: !def.linear, carry: ["kv"] };
    LC_EXT.preview[t] = PREVIEW[t];
    LC_EXT.validate[t] = (uid, p) => lcValidate(uid, t, p);
    if (t === "linear_coupled" || t === "multilinear_elastic" || t.startsWith("multilinear_")) LC_EXT.layout[t] = () => true;
    if (t === "hdr_isolator") LC_EXT.layoutMsg[t] = "the HDR isolator needs a vertical or zero-length link axis.";
  }
  LC_EXT.editor.linear_coupled = matrixEditor;
  LC_EXT.editor.multilinear_elastic = curvesEditor;
  if (!LC_EXT.extra.includes(extraGroups)) LC_EXT.extra.push(extraGroups);
  if (!LC_EXT.extraValidate.includes(lcValidateExtra)) LC_EXT.extraValidate.push(lcValidateExtra);
  LC_LINK_EXTRA.keys = lcExtraKeys;
  if (sky) sky.linkExtC = { types: LC_TYPES, validate: lcValidate, validateExtra: lcValidateExtra, peakOrientedPath, frictionMu };
  return LC_TYPES;
}
initLinkExtC(null);
