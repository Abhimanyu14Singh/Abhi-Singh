/* SkyFrame — Wave B hinges: pure helpers (no DOM) shared by the dialogs
   (js/hingeb.js), the B10 hinge core (js/uhcore.js hooks) and the mock
   backend (js/mock_hingeb.js).  CONTRACT sections "Auto hinges from code
   tables", "Parametric interacting hinges" and "Wall fiber hinges".

     hinge_properties[name] =
       {type: "auto", auto: {code, component, dof, params, gravity, P},
        hysteresis, k_elastic, hysteresis_params}
     | {type: "PMM" | "M2M3", surface: {curves: [{angle, points: [[P, M]…]}]},
        axial: {source, value, gravity} | null, backbone, scale, acceptance,
        hysteresis: "kinematic", drop_strength: "holds", k_elastic,
        hysteresis_params}
     wall_hinges[pier] = {rho_v, rho_b, boundary_ratio, fy, fc, n_fibers,
                          lp_ratio, confined, stories}

   Every helper mirrors skyframe/design/hinge_tables.py and
   skyframe/core/{interaction_hinges,wall_hinges}.py. */

export const HB_TYPES = [["V3", "V3 — shear along local 3"], ["T", "T — torsion"],
  ["PMM", "P-M2-M3 parametric (interaction surface)"], ["M2M3", "M2-M3 parametric (interaction surface)"],
  ["auto", "Auto — code table (ASCE 41 · FEMA 356 · EC8-3)"]];
export const HB_SPECIAL = ["PMM", "M2M3", "auto"];
export const hbIsSpecial = t => HB_SPECIAL.includes(t);
/** Short label for list / option text (PMM_fiber keeps its legacy "PMM"). */
export const hbShortType = t => t === "PMM_fiber" ? "PMM" : t === "PMM" ? "P-M2-M3" : t === "M2M3" ? "M2-M3" : t === "auto" ? "Auto" : t;

const isNum = v => typeof v === "number" && isFinite(v);
const clone = o => (o == null ? o : JSON.parse(JSON.stringify(o)));

/* ---------------------------------------------------------- catalogue */
const A41 = {
  steel_beam: ["M3", "M2"], steel_column: ["M3", "M2", "PMM"], steel_brace: ["P"],
  rc_beam: ["M3", "M2"], rc_column: ["M3", "M2", "PMM"], rc_column_shear: ["V2", "V3"],
};
export const HB_SUPPORT = {
  "ASCE41-17": A41, "ASCE41-23": A41,
  "FEMA356": { steel_beam: ["M3", "M2"], steel_column: ["M3", "M2", "PMM"], rc_beam: ["M3", "M2"] },
  "EC8-3": { rc_beam: ["M3", "M2"], rc_column: ["M3", "M2", "PMM"] },
};
export const HB_CODE_LABEL = { "ASCE41-17": "ASCE 41-17", "ASCE41-23": "ASCE 41-23", "FEMA356": "FEMA 356", "EC8-3": "EN 1998-3 (EC8-3)" };
export const HB_COMP_LABEL = {
  steel_beam: "Steel beam — flexure", steel_column: "Steel column — P-M", steel_brace: "Steel brace — axial",
  rc_beam: "Concrete beam — flexure", rc_column: "Concrete column — P-M", rc_column_shear: "Concrete column — shear",
};
export const HB_DOF_LABEL = { M3: "M3", M2: "M2", PMM: "P-M2-M3 (M2 + M3 at P_G)", P: "P (axial)", V2: "V2", V3: "V3" };
/** Static mirror of GET /api/hinges/catalog (tables / clauses / verified). */
const TI = (table, clause, verified) => ({ table, clause, verified });
const TABLES = {
  steel_beam: TI("Table 9-7.1 (beams - flexure)", "ASCE/SEI 41-17 9.4.2.2, Eq. 9-1", true),
  steel_column: TI("Table 9-7.1 (columns - flexure)", "ASCE/SEI 41-17 9.4.2.3/9.4.2.4", false),
  steel_brace: TI("Table 9-7.1 (braces in compression / tension)", "ASCE/SEI 41-17 9.5.2.4; AISC 360-16 E3", false),
  rc_beam: TI("Table 10-7 (beams controlled by flexure)", "ASCE/SEI 41-17 10.4.2.2", true),
  rc_column: TI("Table 10-8 (columns)", "ASCE/SEI 41-17 10.4.2.2, Table 10-5", true),
  rc_column_shear: TI("Eq. 10-3 (force-controlled shear)", "ASCE/SEI 41-17 10.4.2.3", true),
};
export function hbTableInfo(code, comp) {
  if (code === "FEMA356") return comp === "rc_beam" ? TI("FEMA 356 Table 6-7", "FEMA 356 6.5.2.2", false)
    : TI("FEMA 356 Table 5-6", "FEMA 356 5.5.2.2, Eqs. 5-1 … 5-4", false);
  if (code === "EC8-3") return TI("EN 1998-3 Annex A (chord rotation)", "EN 1998-3 (A.1), (A.10b), (A.12)", true);
  const t = TABLES[comp] || TI("", "", false);
  if (code === "ASCE41-23") return TI(t.table, t.clause.replace("ASCE/SEI 41-17", "ASCE/SEI 41-23 (as 41-17)"), false);
  return t;
}
export function hbMockCatalog() {
  const codes = {};
  for (const [c, comps] of Object.entries(HB_SUPPORT)) {
    codes[c] = {};
    for (const [comp, dofs] of Object.entries(comps)) codes[c][comp] = { dofs: [...dofs], ...hbTableInfo(c, comp), note: "" };
  }
  return { codes, params: { ...HB_PARAM_DEFAULTS }, brace_shapes: ["auto", "W", "2L_in", "2L_out", "HSS", "pipe", "angle"],
    hinge_types: ["M3", "M2", "P", "V2", "PMM_fiber", "V3", "T", "PMM", "M2M3", "auto"], interaction_types: ["PMM", "M2M3"],
    wall_defaults: { ...HB_WALL_DEFAULTS } };
}

/* parameters: [key, label, unit kind, components] */
export const HB_PARAM_DEFAULTS = { expected_factor: null, rho: 0.01, rho_prime: null, fy_bar: 420000.0, fyt: null, rho_t: 0.002,
  s_over_d: 0.5, cover_ratio: 0.1, fc_factor: 1.0, fy_factor: 1.0, K: 1.0, shape: "auto", gamma_el: 1.5, db: 0.02,
  alpha_rho_sx: 0.0, rho_d: 0.0, av: 1.0, Lv_ratio: 0.5 };
const STEEL = ["steel_beam", "steel_column", "steel_brace"], RC = ["rc_beam", "rc_column", "rc_column_shear"];
export const HB_PARAMS = [
  ["expected_factor", "Expected strength factor Fye/Fy", "none", STEEL, "material Ry"],
  ["K", "Effective-length factor K", "none", ["steel_brace"]],
  ["rho", "Tension steel ratio ρ = As/(b d)", "none", ["rc_beam", "rc_column"]],
  ["rho_prime", "Compression steel ratio ρ'", "none", ["rc_beam", "rc_column"], "beams 0 · columns ρ"],
  ["fy_bar", "Longitudinal rebar fy", "stress", RC],
  ["fyt", "Transverse rebar fyt", "stress", ["rc_column", "rc_column_shear"], "= fy"],
  ["rho_t", "Transverse ratio ρt = Av/(b s)", "none", ["rc_column", "rc_column_shear"]],
  ["s_over_d", "Hoop spacing s/d", "none", ["rc_column", "rc_column_shear"]],
  ["cover_ratio", "Cover d'/h", "none", RC],
  ["fc_factor", "Expected / specified f'c", "none", RC],
  ["fy_factor", "Expected / specified fy", "none", RC],
  ["gamma_el", "EC8-3 γel (1.5 primary · 1.0 secondary)", "none", ["rc_beam", "rc_column"], null, "EC8-3"],
  ["db", "EC8-3 bar diameter db", "dim", ["rc_beam", "rc_column"], null, "EC8-3"],
  ["alpha_rho_sx", "EC8-3 confinement α·ρsx", "none", ["rc_beam", "rc_column"], null, "EC8-3"],
  ["rho_d", "EC8-3 diagonal steel ratio ρd", "none", ["rc_beam", "rc_column"], null, "EC8-3"],
  ["av", "EC8-3 shear-cracking flag av (0 or 1)", "none", ["rc_beam", "rc_column"], null, "EC8-3"],
  ["Lv_ratio", "EC8-3 shear span Lv / L", "none", ["rc_beam", "rc_column"], null, "EC8-3"],
];
export const hbParamsFor = (code, comp) => HB_PARAMS.filter(p => p[3].includes(comp) && (!p[5] || p[5] === code));
export const hbNeedsAxial = comp => comp === "steel_column" || comp === "rc_column" || comp === "rc_column_shear";
export const hbDefaultDistances = comp => comp === "steel_brace" || comp === "rc_column_shear" ? [0.5] : [0, 1];

/* ---------------------------------------------------------- new / validate */
const DIAMOND_PTS = m => [[-4000, 0], [-2000, 1.6 * m], [0, m], [1500, 0]];
export function hbNewProperty(typ) {
  if (typ === "auto")
    return { type: "auto", auto: { code: "ASCE41-17", component: "steel_beam", dof: "M3", params: {}, gravity: null, P: null },
      hysteresis: "kinematic", k_elastic: null, hysteresis_params: null };
  if (typ === "PMM" || typ === "M2M3") {
    const curves = [0, 90, 180, 270].map(a => ({ angle: a, points: typ === "PMM" ? DIAMOND_PTS(a % 180 ? 300 : 200) : [[0, a % 180 ? 300 : 200]] }));
    return { type: typ, surface: { curves }, axial: typ === "PMM" ? { source: "gravity", value: null, gravity: null } : null,
      backbone: [[0, 0], [0, 1.0], [0.03, 1.1]], scale: { yield_value: 1.0, yield_deformation: null },
      acceptance: { IO: 0.005, LS: 0.015, CP: 0.025 }, hysteresis: "kinematic", drop_strength: "holds",
      k_elastic: null, hysteresis_params: null };
  }
  return null;
}

/** Client mirror of the PMM / M2M3 / auto rules of normalize_property → message | null. */
export function hbValidateProperty(name, p) {
  const nm = `Hinge property "${name}"`;
  if (p.type === "auto") {
    const bad = Object.keys(p).filter(k => !["type", "auto", "hysteresis", "k_elastic", "hysteresis_params"].includes(k));
    if (bad.length) return `${nm}: an auto hinge takes no ${bad.join(", ")}.`;
    const a = p.auto || {};
    const comps = HB_SUPPORT[a.code];
    if (!comps) return `${nm}: choose a code.`;
    if (!comps[a.component]) return `${nm}: ${HB_CODE_LABEL[a.code]} has no ${HB_COMP_LABEL[a.component] || a.component} table.`;
    if (a.dof != null && !comps[a.component].includes(a.dof)) return `${nm}: DOF ${a.dof} is not available for this table.`;
    for (const [k, v] of Object.entries(a.params || {})) {
      if (!(k in HB_PARAM_DEFAULTS)) return `${nm}: unknown parameter ${k}.`;
      if (k === "shape" || v == null) continue;
      if (!isNum(v)) return `${nm}: parameter ${k} must be a number.`;
      const nonneg = ["alpha_rho_sx", "rho_d", "rho_prime", "av"].includes(k);
      if (nonneg ? v < 0 : v <= 0) return `${nm}: parameter ${k} must be ${nonneg ? "≥ 0" : "> 0"}.`;
      if (["rho", "rho_t", "rho_prime", "rho_d"].includes(k) && v >= 0.1) return `${nm}: ${k} must be < 0.1.`;
      if (k === "cover_ratio" && v >= 0.5) return `${nm}: cover ratio must be < 0.5.`;
    }
    if (a.P != null && !isNum(a.P)) return `${nm}: the fixed axial load must be a number.`;
    if (p.k_elastic != null && !(isNum(p.k_elastic) && p.k_elastic > 0)) return `${nm}: the elastic stiffness must be > 0.`;
    return null;
  }
  const curves = ((p.surface || {}).curves) || [];
  if (curves.length < 2) return `${nm}: the interaction surface needs at least two curves (angles).`;
  const angs = new Set();
  for (const c of curves) {
    if (!isNum(c.angle) || c.angle < 0 || c.angle >= 360) return `${nm}: curve angles must be in [0, 360).`;
    if (angs.has(c.angle)) return `${nm}: duplicate curve angle ${c.angle}.`;
    angs.add(c.angle);
    const pts = c.points || [];
    if (p.type === "M2M3") {
      if (pts.length !== 1 || pts[0][0] !== 0 || !(isNum(pts[0][1]) && pts[0][1] > 0)) return `${nm}: an M2-M3 curve has one moment > 0 (at P = 0).`;
    } else {
      if (pts.length < 2) return `${nm}: curve ${c.angle}° needs at least two (P, M) points.`;
      if (!pts.every(q => isNum(q[0]) && isNum(q[1]) && q[1] >= 0)) return `${nm}: curve ${c.angle}°: enter numbers, M ≥ 0.`;
      const ps = pts.map(q => q[0]).sort((x, y) => x - y);
      for (let i = 1; i < ps.length; i++) if (ps[i] === ps[i - 1]) return `${nm}: curve ${c.angle}° repeats P = ${ps[i]}.`;
    }
  }
  if (!((angs.has(0) || angs.has(180)) && (angs.has(90) || angs.has(270)))) return `${nm}: define the 0° (or 180°) and 90° (or 270°) curves.`;
  const bb = p.backbone;
  if (!Array.isArray(bb) || bb.length < 2) return `${nm}: the moment–rotation curve needs A = (0, 0) and B.`;
  if (bb.length > 6) return `${nm}: at most 6 moment–rotation points.`;
  if (!bb.every(q => Array.isArray(q) && q.length === 2 && isNum(q[0]) && isNum(q[1]))) return `${nm}: enter finite moment–rotation points.`;
  if (bb[0][0] !== 0 || bb[0][1] !== 0) return `${nm}: point A must be (0, 0).`;
  if (!(Math.abs(bb[1][1]) > 0)) return `${nm}: point B must have M/My > 0.`;
  for (let k = 1; k < bb.length; k++) if (Math.abs(bb[k][0]) < Math.abs(bb[k - 1][0])) return `${nm}: rotations must be non-decreasing.`;
  if ((p.hysteresis || "kinematic") !== "kinematic") return `${nm}: interacting hinges use kinematic hysteresis.`;
  if ((p.drop_strength || "holds") !== "holds") return `${nm}: interacting hinges hold their strength (drop strength not available).`;
  const acc = p.acceptance;
  if (acc != null) {
    const seq = ["IO", "LS", "CP"].filter(k => k in acc).map(k => acc[k]);
    if (!seq.every(v => isNum(v) && v >= 0)) return `${nm}: acceptance values must be ≥ 0.`;
    for (let i = 1; i < seq.length; i++) if (seq[i] < seq[i - 1]) return `${nm}: acceptance must satisfy IO ≤ LS ≤ CP.`;
  }
  if (p.type === "PMM") {
    const ax = p.axial || { source: "gravity" };
    if (!["gravity", "value"].includes(ax.source)) return `${nm}: axial source must be gravity or value.`;
    if (ax.source === "value" && !isNum(ax.value)) return `${nm}: enter the axial load P.`;
  } else if (p.axial != null) return `${nm}: an M2-M3 hinge has no axial setting.`;
  if (p.k_elastic != null && !(isNum(p.k_elastic) && p.k_elastic > 0)) return `${nm}: the elastic stiffness must be > 0.`;
  return null;
}

/* ---------------------------------------------------------- surface */
export function hbCurveMoment(curve, P) {
  const pts = [...(curve.points || [])].sort((a, b) => a[0] - b[0]);
  if (pts.length === 1) return pts[0][1];
  if (!pts.length || P < pts[0][0] || P > pts[pts.length - 1][0]) return 0;
  for (let i = 1; i < pts.length; i++)
    if (P <= pts[i][0]) return pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * (P - pts[i - 1][0]) / ((pts[i][0] - pts[i - 1][0]) || 1);
  return pts[pts.length - 1][1];
}
/** Mirror of core.interaction_hinges.ellipse_at. */
export function hbEllipseAt(surface, P) {
  const by = {};
  for (const c of (surface && surface.curves) || []) by[c.angle] = hbCurveMoment(c, P);
  const mean = (a, b) => { const v = [a, b].filter(x => x in by).map(x => by[x]); return v.length ? v.reduce((s, x) => s + x, 0) / v.length : 0; };
  const M2y = mean(0, 180), M3y = mean(90, 270);
  const fit = Object.entries(by).map(([a, M]) => {
    const t = +a * Math.PI / 180;
    const Me = M2y > 0 && M3y > 0 ? 1 / Math.sqrt((Math.cos(t) / M2y) ** 2 + (Math.sin(t) / M3y) ** 2) : 0;
    return { angle: +a, M_surface: M, M_ellipse: Me };
  }).sort((x, y) => x.angle - y.angle);
  return { M2y, M3y, P, fit };
}

/* ---------------------------------------------------------- wall hinges */
export const HB_WALL_DEFAULTS = { rho_v: 0.0025, rho_b: 0.0, boundary_ratio: 0.1, fy: 420000.0, fc: null, n_fibers: 20,
  lp_ratio: 0.5, confined: false, stories: null };
export function hbNormalizeWall(d) {
  const out = {};
  for (const [k, dv] of Object.entries(HB_WALL_DEFAULTS)) out[k] = d && k in d ? d[k] : dv;
  return out;
}
export function hbValidateWall(label, d, model) {
  const nm = `Wall hinge "${label}"`;
  if (!d || typeof d !== "object") return `${nm} must be an object.`;
  const bad = Object.keys(d).filter(k => !(k in HB_WALL_DEFAULTS));
  if (bad.length) return `${nm}: unknown keys ${bad.join(", ")}.`;
  const w = hbNormalizeWall(d);
  for (const k of ["rho_v", "rho_b"]) if (!(isNum(w[k]) && w[k] >= 0 && w[k] < 0.1)) return `${nm}: ${k} must be in [0, 0.1).`;
  for (const k of ["boundary_ratio", "fy", "lp_ratio"]) if (!(isNum(w[k]) && w[k] > 0)) return `${nm}: ${k} must be > 0.`;
  if (w.boundary_ratio > 0.5) return `${nm}: boundary length must be at most half the wall.`;
  if (w.lp_ratio > 2) return `${nm}: hinge length ratio must be ≤ 2.`;
  if (w.fc != null && !(isNum(w.fc) && w.fc > 0)) return `${nm}: f'c must be > 0.`;
  if (!(Number.isInteger(w.n_fibers) && w.n_fibers >= 4 && w.n_fibers <= 200)) return `${nm}: fibers must be a whole number in [4, 200].`;
  if (typeof w.confined !== "boolean") return `${nm}: confined must be on or off.`;
  if (w.stories != null) {
    if (!Array.isArray(w.stories)) return `${nm}: stories must be a list.`;
    const names = new Set(((model && model.stories) || []).map(s => s.name));
    for (const s of w.stories) if (!names.has(s)) return `${nm}: unknown story "${s}".`;
  }
  return null;
}
/** Pier labels of the model's shell walls (explicit labels; auto = uid). */
export function hbPierLabels(model) {
  const auto = !!(model && model.auto_pier_walls);
  const out = [];
  for (const r of (model && model.shells) || []) {
    if (r.kind !== "wall") continue;
    const lab = r.pier || (auto ? r.uid : "");
    if (lab && !out.includes(lab)) out.push(lab);
  }
  return out;
}

/* ---------------------------------------------------------- mock generator */
const ES = 2.0e8;
/** Approximate auto-hinge specs for ?mock=1 (plausible, NOT the engine):
    returns [{type, My, a, b, c, IO, LS, CP, thy, neg?, meta}]. */
export function hbMockGenerate(model, mm, auto, PG = 0) {
  const sec = ((model && model.sections) || {})[mm.section] || {};
  const mat = ((model && model.materials) || {})[sec.material] || {};
  const E = isNum(mat.E) ? mat.E : 2.0e8;
  const L = Math.max(1e-6, Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]));
  const prm = { ...HB_PARAM_DEFAULTS, ...((auto && auto.params) || {}) };
  const info = hbTableInfo(auto.code, auto.component);
  const meta = { code: auto.code, component: auto.component, table: info.table, clause: info.clause, preliminary: !info.verified, P_G: PG, mock: true };
  const axes = auto.dof === "PMM" ? ["M2", "M3"] : [auto.dof];
  const out = [];
  for (const ax of axes) {
    const I = ax === "M2" || ax === "V3" ? (sec.I22 || 1e-4) : (sec.I33 || 1e-4);
    const depth = ax === "M2" || ax === "V3" ? (sec.b || 0.3) : (sec.h || 0.5);
    const width = ax === "M2" || ax === "V3" ? (sec.h || 0.5) : (sec.b || 0.3);
    const A = sec.A || width * depth;
    if (auto.component.startsWith("steel")) {
      const Fye = (prm.expected_factor || mat.Ry || 1.1) * (mat.fy || 345000);
      if (auto.component === "steel_brace") {
        const r = Math.sqrt(Math.min(sec.I22 || 1e-5, sec.I33 || 1e-5) / A), lam = (prm.K || 1) * L / r;
        const Fe = Math.PI ** 2 * E / lam ** 2;
        const Fcr = lam <= 4.71 * Math.sqrt(E / Fye) ? 0.658 ** (Fye / Fe) * Fye : 0.877 * Fe;
        const k = E * A / L, T = A * Fye, Pc = Fcr * A, dt = T / k, dc = Pc / k;
        out.push({ type: "P", My: T, a: 10 * dt, b: 13 * dt, c: 0.6, IO: 0.5 * dt, LS: 10 * dt, CP: 13 * dt, thy: dt,
          neg: { My: Pc, a: 0.5 * dc, b: 9 * dc, c: 0.3, IO: 0.5 * dc, LS: 7 * dc, CP: 9 * dc }, meta: { ...meta, P_CE: Pc, T_CE: T } });
        continue;
      }
      const Z = 1.12 * I / (depth / 2);
      const r = Math.max(0, PG) / (A * Fye);
      let My = Z * Fye;
      if (auto.component === "steel_column") My *= r < 0.2 ? 1 - r / 2 : 9 / 8 * (1 - r);
      const thy = My * L / (6 * E * I);
      out.push({ type: ax, My, a: 9 * thy, b: 11 * thy, c: 0.6, IO: thy, LS: (auto.code === "FEMA356" ? 6 : 9) * thy,
        CP: (auto.code === "FEMA356" ? 8 : 11) * thy, thy, meta: { ...meta, P_ratio: r } });
      continue;
    }
    const fc = mat.fc || 30000, fy = prm.fy_bar || 420000;
    const d = (1 - prm.cover_ratio) * depth, As = prm.rho * width * d;
    const aw = As * fy / (0.85 * fc * width);
    const Mn = As * fy * (d - aw / 2) + Math.max(0, PG) * (depth / 2 - aw / 2) * 0.5;
    if (auto.component === "rc_column_shear") {
      const sq = Math.sqrt(fc / 1000) * 1000, dd = 0.8 * depth, mvd = Math.min(Math.max(L / (2 * dd), 2), 4);
      const V = prm.rho_t * width * (prm.fyt || fy) * dd + 0.5 * sq / mvd * Math.sqrt(Math.max(0, 1 + PG / (0.5 * sq * width * depth))) * 0.8 * width * depth;
      out.push({ type: ax, My: V, a: 1e-3 * L, b: 1e-3 * L, c: 0.2, IO: 0, LS: 0, CP: 0, thy: V * L ** 3 / (12 * E * I), meta: { ...meta, force_controlled: true } });
      continue;
    }
    const thy = Mn * L / (6 * E * I);
    if (auto.code === "EC8-3") {
      const phi = 2.1 * (fy / ES) / depth, Lv = prm.Lv_ratio * L, nu = Math.max(0, PG) / (width * depth * fc);
      const ty = phi * Lv / 3 + 0.0013 * (1 + 1.5 * depth / Lv);
      const tum = 0.016 * 0.3 ** nu * (fc / 1000) ** 0.225 * (Lv / depth) ** 0.35 / prm.gamma_el;
      const a = Math.max(0, tum - ty);
      out.push({ type: ax, My: Mn, a, b: a, c: 0, IO: 0, LS: Math.max(0, 0.75 * tum - ty), CP: a, thy: ty, meta: { ...meta, theta_y: ty, theta_um: tum } });
      continue;
    }
    const n = Math.max(0, PG) / (width * depth * fc);
    const a = auto.component === "rc_column" ? Math.max(0, 0.042 - 0.043 * n + 0.63 * prm.rho_t - 0.023 * 0.5) : 0.025;
    const b = auto.component === "rc_column" ? Math.max(a, 0.5 / (5 + n / 0.8 / prm.rho_t * fc / fy) - 0.01) : 0.05;
    const c = auto.component === "rc_column" ? Math.max(0, 0.24 - 0.4 * n) : 0.2;
    const acc = auto.component === "rc_column" ? { IO: Math.min(0.15 * a, 0.005), LS: 0.5 * b, CP: 0.7 * b } : { IO: 0.01, LS: 0.025, CP: 0.05 };
    out.push({ type: ax, My: Mn, a, b, c, ...acc, thy, meta: { ...meta, n } });
  }
  return out;
}
/** B10-style backbone (plastic, absolute) of a mock spec side. */
export function hbSpecBackbone(s) {
  const pts = [[0, 0], [0, s.My], [s.a, s.My * (1 + 1e-6) + 0.03 * s.My * (s.a > 0 ? 1 : 0)]];
  if (s.c > 0) { pts.push([s.a, s.c * s.My]); if (s.b > s.a) pts.push([s.b, s.c * s.My]); }
  return pts;
}
export { clone as hbClone, isNum as hbIsNum };
