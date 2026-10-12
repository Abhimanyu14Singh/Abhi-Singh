/* SkyFrame — wave-B link property types (CONTRACT "Link types: Plastic
   (Wen), BRB, bilinear damper, T/C friction isolator").

   Registers four link types in ME.LINK_TYPES (Properties panel editor,
   plan / elevation glyph letters, normalizeLinkDevice) and in the Link
   Properties dialog of js/linkhyst.js (HYST_TYPES + the LH_EXT preview /
   validation / layout hooks):

     plastic_wen      {k, Fy, ratio?, exp?, kv?}        W   Wen loop preview
     brb              {k, Fy, d_bm, beta?, omega?, R0?} R   asymmetric backbone
     damper_bilinear  {cd, Fr, ratio?, k?}              V   force–velocity
     tc_fp_isolator   {R, mu, k_init?, kv?, kt?, gap_t?} U  axial T/C law

   Optional keys are written only when set (load + save byte-identical).
   Every number goes through js/units.js (the dialog's unit kinds); the
   model stays SI. */

import { HYST_TYPES as LX_HYST, LH_EXT as LX_EXT, isolatorLayoutOk as lxIsoOk } from "./linkhyst.js";
import { LINK_TYPES as LX_LINK_TYPES } from "./modeledit.js";
import LXU from "./units.js";

const isNum = v => typeof v === "number" && isFinite(v);
const g = (p, k, dv) => isNum(p[k]) ? p[k] : dv;

/** Type defs in the js/linkhyst.js HYST_TYPES shape (params required, opt optional). */
export const LX_TYPES = {
  plastic_wen: {
    label: "Plastic (Wen)", letter: "W",
    note: "ETABS Plastic (Wen): F = ratio·k·d + (1−ratio)·Fy·z with dz/dd = (k/Fy)(1 − |z|^exp) while loading. A link with a horizontal component acts axially (U1); a vertical or zero-length link carries the law on both shear directions with elastic vertical kv. Nonlinear static, direct-integration TH and FNA.",
    params: [["k", "kN/m", 20000, "Elastic stiffness k"], ["Fy", "kN", 100, "Yield force Fy"]],
    opt: [["ratio", "–", 0, "Post-yield stiffness ratio"], ["exp", "–", 2, "Yielding exponent"], ["kv", "kN/m", 1e7, "Vertical stiffness kv (vertical / zero-length only)"]],
  },
  brb: {
    label: "Buckling-restrained brace", letter: "R",
    note: "Axial BRB (Steel4, asymmetric kinematic hardening): elastic k to Fy, then hardening calibrated so the force is ω·Fy in tension and β·ω·Fy in compression at the design deformation d_bm (AISC 341 adjustment factors). Link must have length.",
    params: [["k", "kN/m", 200000, "Axial stiffness k"], ["Fy", "kN", 500, "Yield force Fy"], ["d_bm", "m", 0.03, "Design deformation d_bm"]],
    opt: [["beta", "–", 1.1, "Compression adjustment β"], ["omega", "–", 1.3, "Strain-hardening adjustment ω"], ["R0", "–", 20, "Transition curvature R0"]],
  },
  damper_bilinear: {
    label: "Damper · bilinear", letter: "V",
    note: "ETABS Damper - Bilinear: Maxwell dashpot (series spring k) whose force is cd·v up to the relief force Fr, then grows at ratio·cd. Axial, link must have length; no static force or eigen stiffness (time history only).",
    params: [["cd", "kN·s/m", 1500, "Damping coefficient cd"], ["Fr", "kN", 300, "Relief force Fr"]],
    opt: [["ratio", "–", 0, "Post-relief coefficient ratio"], ["k", "kN/m", 1e6, "Series stiffness k"]],
  },
  tc_fp_isolator: {
    label: "T/C friction isolator", letter: "U",
    note: "Friction pendulum in compression (slip μ·N, restoring N/R); in tension the bearing lifts off — zero axial and shear force — unless a hold-down kt (after the gap gap_t) is given. Vertical or zero-length link.",
    params: [["R", "m", 2.2, "Effective radius R"], ["mu", "–", 0.06, "Friction coefficient μ"]],
    opt: [["k_init", "kN/m", 1e5, "Stick stiffness k_init"], ["kv", "kN/m", 1e7, "Compression stiffness kv"],
      ["kt", "kN/m", 0, "Tension hold-down kt (0 = free uplift)"], ["gap_t", "m", 0, "Hold-down gap gap_t"]],
  },
};

/** Same rule as js/linkhyst.js normalize: optional keys kept only when set. */
function lxNormalize(def) {
  return src => {
    src = src && typeof src === "object" ? src : {};
    const allowed = new Set([...def.params, ...def.opt].map(p => p[0]));
    const out = {};
    for (const k of Object.keys(src)) if (allowed.has(k) && isNum(src[k])) out[k] = +src[k];
    for (const [k, , dv] of def.params) if (!(k in out)) out[k] = dv;
    return out;
  };
}
const CARRY = ["points", "points_neg", "kv", "k_init"];

/* ---------------------------------------------------------------- laws */
/** Wen force along a deformation path (RK4) — mirrors core.links_ext.wen_history. */
export function wenHistory(p, path, n = 300) {
  const k = p.k, fy = p.Fy, r = g(p, "ratio", 0), ex = g(p, "exp", 2), uy = fy / k;
  const zd = (z, du) => (1 - (du * z > 0 ? Math.pow(Math.abs(z), ex) : 0)) * du / uy;
  const out = [[0, 0]];
  let u = 0, z = 0;
  for (const tgt of path) {
    const h = (tgt - u) / n;
    for (let i = 0; i < n; i++) {
      const k1 = zd(z, h), k2 = zd(z + 0.5 * k1, h), k3 = zd(z + 0.5 * k2, h), k4 = zd(z + k3, h);
      z += (k1 + 2 * k2 + 2 * k3 + k4) / 6; u += h;
      if (i % 3 === 2 || i === n - 1) out.push([u, r * k * u + (1 - r) * fy * z]);
    }
  }
  return out;
}
export function brbHardening(p) {
  const dy = p.Fy / p.k, om = g(p, "omega", 1.3), be = g(p, "beta", 1.1);
  return [(om - 1) * p.Fy / (p.k * (p.d_bm - dy)), (be * om - 1) * p.Fy / (p.k * (p.d_bm - dy))];
}
export function brbBackbone(p, d) {
  const dy = p.Fy / p.k, [bt, bc] = brbHardening(p);
  if (Math.abs(d) <= dy) return p.k * d;
  return d > 0 ? p.Fy + bt * p.k * (d - dy) : -(p.Fy + bc * p.k * (-d - dy));
}
export function damperForce(p, v) {
  const vr = p.Fr / p.cd, r = g(p, "ratio", 0);
  return Math.abs(v) <= vr ? p.cd * v : Math.sign(v) * (p.Fr + r * p.cd * (Math.abs(v) - vr));
}
export function tcAxial(p, d) {
  if (d <= 0) return g(p, "kv", 1e7) * d;
  const kt = g(p, "kt", 0), gap = g(p, "gap_t", 0);
  return d > gap ? kt * (d - gap) : 0;
}

/* ---------------------------------------------------------------- previews */
const PREVIEW = {
  plastic_wen: p => {
    if (!(p.k > 0 && p.Fy > 0)) return null;
    const D = Math.max(6 * p.Fy / p.k, 1e-4);
    const cyc = wenHistory(p, [D, -D, D]);
    const r = g(p, "ratio", 0);
    return { series: [{ pts: cyc, cls: "b9-s-cyc" }], marks: [[D, r * p.k * D + (1 - r) * p.Fy]],
      note: `Illustrative ±${LXU.fmtU("disp", D, 1)} cycle; the force tends to ratio·k·d + (1−ratio)·Fy = ${LXU.fmtU("force", r * p.k * D + (1 - r) * p.Fy, 1)} at ${LXU.fmtU("disp", D, 1)}.` };
  },
  brb: p => {
    if (!(p.k > 0 && p.Fy > 0 && p.d_bm > p.Fy / p.k)) return null;
    const D = p.d_bm, pts = [];
    for (let i = -40; i <= 40; i++) { const d = D * 1.2 * i / 40; pts.push([d, brbBackbone(p, d)]); }
    const om = g(p, "omega", 1.3), be = g(p, "beta", 1.1);
    return { series: [{ pts, cls: "b9-s-bb" }], marks: [[D, om * p.Fy], [-D, -be * om * p.Fy]],
      note: `Backbone: ω·Fy = ${LXU.fmtU("force", om * p.Fy, 1)} (tension) and β·ω·Fy = ${LXU.fmtU("force", be * om * p.Fy, 1)} (compression) at ±d_bm.` };
  },
  damper_bilinear: p => {
    if (!(p.cd > 0 && p.Fr > 0)) return null;
    const V = 3 * p.Fr / p.cd, pts = [];
    for (let i = -30; i <= 30; i++) { const v = V * i / 30; pts.push([v, damperForce(p, v)]); }
    return { series: [{ pts, cls: "b9-s-bb" }], marks: [[p.Fr / p.cd, p.Fr], [-p.Fr / p.cd, -p.Fr]], xKind: "velocity",
      note: `Force–velocity: relief at v = Fr/cd = ${LXU.fmtU("velocity", p.Fr / p.cd, 3)}.` };
  },
  tc_fp_isolator: p => {
    const kv = g(p, "kv", 1e7), kt = g(p, "kt", 0), gap = g(p, "gap_t", 0);
    const dc = 1000 / kv, dt = Math.max(3 * gap, 2 * dc, 1e-4), pts = [];
    for (let i = -20; i <= 40; i++) { const d = i < 0 ? dc * i / 20 : dt * i / 40; pts.push([d, tcAxial(p, d)]); }
    return { series: [{ pts, cls: "b9-s-bb" }], marks: [],
      note: kt > 0 ? `Axial: compression kv; tension hold-down kt past the gap ${LXU.fmtU("disp", gap, 1)}.` : "Axial: compression kv; free uplift — zero axial and shear force in tension." };
  },
};

/* ---------------------------------------------------------------- validation */
const VALIDATE = {
  plastic_wen: (w, p) => {
    if (!(p.k > 0)) return `${w}: k must be > 0.`;
    if (!(p.Fy > 0)) return `${w}: Fy must be > 0.`;
    if ("ratio" in p && !(p.ratio >= 0 && p.ratio < 1)) return `${w}: ratio must be in [0, 1).`;
    if ("exp" in p && !(p.exp > 0)) return `${w}: exp must be > 0.`;
    if ("kv" in p && !(p.kv > 0)) return `${w}: kv must be > 0.`;
    return null;
  },
  brb: (w, p) => {
    if (!(p.k > 0)) return `${w}: k must be > 0.`;
    if (!(p.Fy > 0)) return `${w}: Fy must be > 0.`;
    if (!(p.d_bm > p.Fy / p.k)) return `${w}: d_bm must exceed the yield deformation Fy/k = ${LXU.fmtU("disp", p.Fy / p.k, 3)}.`;
    const om = g(p, "omega", 1.3), be = g(p, "beta", 1.1);
    if (!(be > 0)) return `${w}: beta must be > 0.`;
    if (!(om >= 1)) return `${w}: omega must be ≥ 1.`;
    if (!(be * om >= 1)) return `${w}: beta·omega must be ≥ 1.`;
    if ("R0" in p && !(p.R0 > 0)) return `${w}: R0 must be > 0.`;
    return null;
  },
  damper_bilinear: (w, p) => {
    if (!(p.cd > 0)) return `${w}: cd must be > 0.`;
    if (!(p.Fr > 0)) return `${w}: Fr must be > 0.`;
    if ("ratio" in p && !(p.ratio >= 0 && p.ratio <= 1)) return `${w}: ratio must be in [0, 1].`;
    if ("k" in p && !(p.k > 0)) return `${w}: k must be > 0.`;
    return null;
  },
  tc_fp_isolator: (w, p) => {
    for (const k of ["R", "mu"]) if (!(p[k] > 0)) return `${w}: ${k} must be > 0.`;
    for (const k of ["k_init", "kv"]) if (k in p && !(p[k] > 0)) return `${w}: ${k} must be > 0.`;
    for (const k of ["kt", "gap_t"]) if (k in p && !(p[k] >= 0)) return `${w}: ${k} must be ≥ 0.`;
    if ("gap_t" in p && !(g(p, "kt", 0) > 0)) return `${w}: gap_t needs a hold-down stiffness kt > 0.`;
    return null;
  },
};

const hasLength = l => Math.hypot(l.pj[0] - l.pi[0], l.pj[1] - l.pi[1], l.pj[2] - l.pi[2]) > 1e-9;
const LAYOUT = {
  plastic_wen: l => !(Math.hypot(l.pj[0] - l.pi[0], l.pj[1] - l.pi[1]) > 1e-6 && hasLength(l) && l.params && "kv" in l.params),
  brb: hasLength,
  damper_bilinear: hasLength,
  tc_fp_isolator: lxIsoOk,
};
const LAYOUT_MSG = {
  plastic_wen: "kv applies only to a vertical or zero-length Plastic (Wen) link (shear layout) — clear kv for an axial link.",
  brb: "a BRB acts along the link axis — the two points must be distinct.",
  damper_bilinear: "a damper acts along the link axis — the two points must be distinct.",
  tc_fp_isolator: "the T/C isolator needs a vertical or zero-length link axis.",
};

/** Client checks mirroring core.links_ext.validate_link → message | null. */
export function lxValidate(uid, type, p) {
  const def = LX_TYPES[type];
  if (!def) return null;
  const w = `Link ${uid} (${type})`;
  for (const [k] of def.params) if (!isNum(p[k])) return `${w}: ${k} is required.`;
  return VALIDATE[type](w, p);
}

/** Register the types (idempotent). Returns the def table. */
export function initLinkExt(sky) {
  for (const [t, def] of Object.entries(LX_TYPES)) {
    if (!LX_HYST[t]) LX_HYST[t] = def;
    if (!LX_LINK_TYPES[t]) LX_LINK_TYPES[t] = { ...def, params: def.params.map(([k, u, dv]) => [k, u, dv]), normalize: lxNormalize(def), hysteresis: true, carry: CARRY };
    LX_EXT.preview[t] = PREVIEW[t];
    LX_EXT.validate[t] = (uid, p) => lxValidate(uid, t, p);
    LX_EXT.layout[t] = LAYOUT[t];
    if (LAYOUT_MSG[t]) LX_EXT.layoutMsg[t] = LAYOUT_MSG[t];
  }
  if (sky) sky.linkExt = { types: LX_TYPES, validate: lxValidate, wenHistory, brbBackbone, damperForce, tcAxial };
  return LX_TYPES;
}

/* register at import time so ME.LINK_TYPES knows the types before any model loads */
initLinkExt(null);
