/* SkyFrame — user-defined plastic hinges (CONTRACT "User-defined hinges and
   hinge overwrites", B10): pure helpers shared by the dialogs
   (js/uhdlg.js), the hinge-results viewer (js/uhresults.js) and the mock
   backend (js/mock_userhinge.js).  No DOM access here.

     model.hinge_properties  {name: {type, backbone, scale, acceptance,
                                     hysteresis, drop_strength, k_elastic,
                                     hysteresis_params}}   (absent when empty)
     member.hinges           "none"|"auto_m3"|"fiber_pmm"  (legacy) or
                             [{property, relative_distance}]  (user list)
     member.hinge_overwrites {auto_subdivide, relative_length}  (absent = none)

   buildEnvelope / classify mirror skyframe/core/user_hinges.py exactly
   (same constants) so previews and mock results match the engine. */

export const UH_TYPES = [["M3", "M3 — moment about local 3"], ["M2", "M2 — moment about local 2"],
  ["P", "P — axial"], ["V2", "V2 — shear along local 2"], ["PMM_fiber", "P-M2-M3 fiber (PMM)"]];
export const UH_HYST = [["kinematic", "Kinematic"], ["takeda", "Takeda"], ["pivot", "Pivot"],
  ["isotropic", "Isotropic"], ["concrete", "Concrete"]];
export const UH_DROPS = [["drops", "Drops to zero"], ["holds", "Is extrapolated (holds)"]];
export const UH_HYST_DEFAULTS = {
  takeda: { pinch_x: 1.0, pinch_y: 1.0, damage1: 0.0, damage2: 0.0, beta: 0.0 },
  pivot: { pinch_x: 0.5, pinch_y: 0.25, damage1: 0.0, damage2: 0.0, beta: 0.0 },
  concrete: { pinch_x: 0.8, pinch_y: 0.2, damage1: 0.0, damage2: 0.0, beta: 0.0 },
};
export const UH_HP_KEYS = ["pinch_x", "pinch_y", "damage1", "damage2", "beta"];
export const UH_RIGID_FACTOR = 10.0;
export const UH_DROP_RATIO = 0.1;
export const UH_RESIDUAL = 1.0e-6;
export const UH_MAX_POINTS = 6;
export const UH_POINT_LABELS = ["A", "B", "C", "D", "E", "F"];

/* ETABS hinge states (order) + display colours. */
export const UH_STATES = ["A-B", "B-IO", "IO-LS", "LS-CP", ">CP", "C-D", "D-E", ">E"];
export const UH_STATE_COLORS = {
  "A-B": "#8b95a1", "B-IO": "#3b82f6", "IO-LS": "#22c55e", "LS-CP": "#eab308",
  ">CP": "#ef4444", "C-D": "#b91c1c", "D-E": "#991b1b", ">E": "#6b1111",
};
export const UH_LEGACY_STATE = { "A-B": "elastic", "B-IO": "IO", "IO-LS": "LS", "LS-CP": "CP",
  ">CP": "collapse", "C-D": "collapse", "D-E": "collapse", ">E": "collapse" };
const FROM_LEGACY = { elastic: "A-B", IO: "B-IO", LS: "IO-LS", CP: "LS-CP", collapse: ">CP" };
/** ETABS state of a result entry at step k (legacy-only entries mapped). */
export function uhStateAt(h, k) {
  if (Array.isArray(h.hinge_state) && h.hinge_state.length) return h.hinge_state[Math.min(k, h.hinge_state.length - 1)] || "A-B";
  if (Array.isArray(h.state) && h.state.length) return FROM_LEGACY[h.state[Math.min(k, h.state.length - 1)]] || "A-B";
  return "A-B";
}

export const isNum = v => typeof v === "number" && isFinite(v);
export const uhClone = o => (o == null ? o : JSON.parse(JSON.stringify(o)));
export const isUserList = mm => Array.isArray(mm && mm.hinges);
/** Force / deformation / stiffness units kinds of a hinge type. */
export const uhKinds = typ => (typ === "P" || typ === "V2")
  ? { f: "force", d: "disp", k: "stiffness" }
  : { f: "moment", d: "rotation", k: "rot_stiffness" };

/** A fresh canonical property (engine normalize_property output shape). */
export function uhNewProperty(typ = "M3") {
  if (typ === "PMM_fiber")
    return { type: "PMM_fiber", scale: { yield_value: 1.0, yield_deformation: null },
      acceptance: { IO: 0.004, LS: 0.01, CP: 0.02 }, hysteresis: "kinematic", drop_strength: "drops",
      k_elastic: null, hysteresis_params: null };
  const yv = typ === "P" ? 1500.0 : typ === "V2" ? 300.0 : 250.0;
  return {
    type: typ,
    backbone: [[0, 0], [0, 1.0], [0.02, 1.1], [0.02, 0.2], [0.04, 0.2]],
    scale: { yield_value: yv, yield_deformation: typ === "M3" || typ === "M2" ? null : "auto" },
    acceptance: { IO: 0.005, LS: 0.015, CP: 0.02 },
    hysteresis: "kinematic", drop_strength: "drops", k_elastic: null, hysteresis_params: null,
  };
}

/** Sides [positive, negative] as magnitude point lists. */
export function uhSides(p) {
  const bb = p && p.backbone;
  if (!bb) return [null, null];
  if (Array.isArray(bb)) return [bb, bb];
  return [bb.positive, bb.negative];
}

/** Client mirror of core.user_hinges.normalize_property → message | null. */
export function uhValidateProperty(name, p) {
  const nm = `Hinge property "${name}"`;
  if (!String(name || "").trim()) return "Hinge property names must be non-empty.";
  if (!p || typeof p !== "object") return `${nm} must be an object.`;
  if (!UH_TYPES.some(([t]) => t === p.type)) return `${nm}: choose a hinge type.`;
  const pts = (raw, what) => {
    if (!Array.isArray(raw) || raw.length < 2) return `${nm} ${what}: needs A = (0, 0) and at least point B.`;
    if (raw.length > UH_MAX_POINTS) return `${nm} ${what}: at most ${UH_MAX_POINTS} points.`;
    for (const q of raw) if (!Array.isArray(q) || q.length !== 2 || !q.every(isNum)) return `${nm} ${what}: enter finite (deformation, force) pairs.`;
    if (Math.abs(raw[0][0]) !== 0 || Math.abs(raw[0][1]) !== 0) return `${nm} ${what}: point A must be (0, 0).`;
    if (!(Math.abs(raw[1][1]) > 0)) return `${nm} ${what}: point B must have a force > 0.`;
    for (let k = 1; k < raw.length; k++) if (Math.abs(raw[k][0]) < Math.abs(raw[k - 1][0])) return `${nm} ${what}: deformations must be non-decreasing (${UH_POINT_LABELS[k]}).`;
    return null;
  };
  const bb = p.backbone;
  if (p.type !== "PMM_fiber" || bb != null) {
    if (bb && !Array.isArray(bb)) {
      const e = pts(bb.positive, "positive backbone") || pts(bb.negative, "negative backbone");
      if (e) return e;
    } else {
      const e = pts(bb, "backbone");
      if (e) return e;
    }
  }
  const sc = p.scale || {};
  const yv = sc.yield_value == null ? 1.0 : sc.yield_value;
  if (!(isNum(yv) && yv > 0)) return `${nm}: the force scale factor (yield value) must be > 0.`;
  const yd = sc.yield_deformation;
  if (yd != null && yd !== "auto" && !(isNum(yd) && yd > 0)) return `${nm}: the deformation scale factor must be > 0 (or absolute / auto).`;
  const acc = p.acceptance;
  if (acc != null) {
    const seq = [];
    for (const k of ["IO", "LS", "CP"]) {
      if (!(k in acc)) continue;
      if (!(isNum(acc[k]) && acc[k] >= 0)) return `${nm}: acceptance ${k} must be a number ≥ 0.`;
      seq.push(acc[k]);
    }
    for (let i = 1; i < seq.length; i++) if (seq[i] < seq[i - 1]) return `${nm}: acceptance must satisfy IO ≤ LS ≤ CP.`;
  }
  if (!UH_HYST.some(([h]) => h === (p.hysteresis || "kinematic"))) return `${nm}: unknown hysteresis type.`;
  if ((p.hysteresis === "kinematic" || p.hysteresis === "isotropic") && bb && !Array.isArray(bb)
      && JSON.stringify(bb.positive.map(q => q.map(Math.abs))) !== JSON.stringify(bb.negative.map(q => q.map(Math.abs))))
    return `${nm}: ${p.hysteresis} hysteresis needs a symmetric backbone — use Takeda / Pivot / Concrete for different negative values.`;
  if (!UH_DROPS.some(([d]) => d === (p.drop_strength || "drops"))) return `${nm}: unknown drop-strength option.`;
  if (p.k_elastic != null && !(isNum(p.k_elastic) && p.k_elastic > 0)) return `${nm}: the hinge elastic stiffness must be > 0.`;
  if (p.hysteresis_params != null) {
    for (const [k, v] of Object.entries(p.hysteresis_params)) {
      if (!UH_HP_KEYS.includes(k)) return `${nm}: unknown hysteresis parameter ${k}.`;
      if (!isNum(v)) return `${nm}: hysteresis parameter ${k} must be a number.`;
    }
  }
  return null;
}

/** Member length (joint to joint). */
export const uhLen = mm => Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]);
/** World point at relative distance d. */
export const uhPointAt = (mm, d) => [0, 1, 2].map(i => mm.pi[i] + d * (mm.pj[i] - mm.pi[i]));

/** Client mirror of the member-hinge validation (core.user_hinges.validate_model). */
export function uhValidateMemberHinges(model, mm) {
  const props = (model && model.hinge_properties) || {};
  if (isUserList(mm)) {
    const seen = new Set();
    const types = new Set();
    for (const [k, h] of mm.hinges.entries()) {
      if (!h || typeof h !== "object" || typeof h.property !== "string" || !h.property)
        return `Member ${mm.uid}: hinge ${k + 1} needs a hinge property.`;
      const d = h.relative_distance;
      if (!(isNum(d) && d >= 0 && d <= 1)) return `Member ${mm.uid}: hinge ${k + 1} relative distance must be in [0, 1].`;
      const key = h.property + "@" + d;
      if (seen.has(key)) return `Member ${mm.uid}: duplicate hinge "${h.property}" at ${d}.`;
      seen.add(key);
      const p = props[h.property];
      if (!p) return `Member ${mm.uid}: unknown hinge property "${h.property}".`;
      types.add(p.type);
      if (p.type === "PMM_fiber" && d !== 0 && d !== 1) return `Member ${mm.uid}: a PMM fiber hinge must sit at relative distance 0 or 1.`;
      if (d > 0 && d < 1 && ((mm.rigid_i || 0) + (mm.rigid_j || 0) > 1e-9 && (mm.rigid_factor ?? 1) > 0
          || mm.joint_offsets || (mm.cardinal_point ?? 10) !== 10 || mm.end_offsets === "auto"))
        return `Member ${mm.uid}: interior hinges are not supported on members with rigid end / insertion offsets.`;
    }
    if (mm.hinges.length && (mm.axial_limit || "both") !== "both") return `Member ${mm.uid}: an axial-only (tension / compression-only) member cannot take hinges.`;
    if (types.has("PMM_fiber") && types.size > 1) return `Member ${mm.uid}: PMM fiber hinges cannot be mixed with lumped (M3/M2/P/V2) hinges.`;
  }
  const ov = mm.hinge_overwrites;
  if (ov != null) {
    if (typeof ov !== "object") return `Member ${mm.uid}: bad hinge overwrites.`;
    if ("auto_subdivide" in ov && typeof ov.auto_subdivide !== "boolean") return `Member ${mm.uid}: auto subdivide must be on or off.`;
    if (ov.relative_length != null && !(isNum(ov.relative_length) && ov.relative_length > 0 && ov.relative_length <= 0.5))
      return `Member ${mm.uid}: hinge relative length must be in (0, 0.5].`;
  }
  return null;
}

/* ------------------------------------------------ member stiffness */
export function uhSectionProps(model, mm) {
  const s = (model.sections || {})[mm.section] || (model.designer_sections || {})[mm.section] || {};
  const mat = (model.materials || {})[s.material] || {};
  const E = isNum(mat.E) ? mat.E : 25e6;
  const pick = (k, mk, d) => (isNum(s[k]) ? s[k] : d) * (isNum(s[mk]) ? s[mk] : 1);
  return { E, A: pick("A", "mod_A", 0.16), I33: pick("I33", "mod_I33", 2.1e-3), I22: pick("I22", "mod_I22", 2.1e-3) };
}
export function uhKMember(typ, model, mm) {
  const { E, A, I22, I33 } = uhSectionProps(model, mm);
  const L = Math.max(uhLen(mm), 1e-6);
  if (typ === "M3") return 6 * E * I33 / L;
  if (typ === "M2") return 6 * E * I22 / L;
  if (typ === "P") return E * A / L;
  return 12 * E * I33 / L ** 3;
}

/** Mirror of core.user_hinges.build_envelope (total-deformation envelope). */
export function uhBuildEnvelope(prop, km) {
  const sc = prop.scale || {};
  const sfF = sc.yield_value == null ? 1.0 : sc.yield_value;
  const [posRaw, negRaw] = uhSides(prop);
  const pos = posRaw.map(q => [Math.abs(q[0]), Math.abs(q[1])]);
  const neg = negRaw.map(q => [Math.abs(q[0]), Math.abs(q[1])]);
  const FB = pos[1][1] * sfF;
  const yd = sc.yield_deformation;
  const sfD = yd == null ? 1.0 : yd === "auto" ? FB / km : yd;
  const ke = prop.k_elastic || UH_RIGID_FACTOR * km;
  const kDrop = UH_DROP_RATIO * km;
  const out = { k_e: ke, k_member: km, sf_d: sfD, F_B: FB };
  for (const [side, raw] of [["positive", pos], ["negative", neg]]) {
    const b0 = raw[1][0];
    const pts = raw.slice(1).map(([d, f]) => [Math.max(0, d - b0) * sfD, f * sfF]);
    const Fb = pts[0][1];
    const tiny = 1e-6 * Fb / ke;
    const tot = [];
    for (const [dp, F] of pts) {
      let d = dp + F / ke;
      if (tot.length && d <= tot[tot.length - 1][0] + tiny)
        d = tot[tot.length - 1][0] + Math.max(Math.abs(tot[tot.length - 1][1] - F) / kDrop, tiny);
      tot.push([d, F]);
    }
    const markers = tot.map(([d, F]) => d - F / ke);
    const nUser = tot.length;
    const last = tot[tot.length - 1];
    if ((prop.drop_strength || "drops") === "drops") {
      const Fr = UH_RESIDUAL * Fb;
      const dt = last[0] + Math.max((last[1] - Fr) / kDrop, tiny);
      tot.push([dt, Fr], [2 * dt, Fr]);
    } else tot.push([2 * last[0], last[1]]);
    out[side] = { points: tot, markers, n_user: nUser };
  }
  return out;
}

/** Mirror of core.user_hinges.classify. */
export function uhClassify(d, F, env, acc) {
  const side = d >= 0 ? env.positive : env.negative;
  const dp = Math.abs(d) - Math.abs(F) / env.k_e;
  const tol = 1e-6 * side.points[0][0];
  if (dp <= tol) return "A-B";
  const mk = side.markers;
  const mC = mk.length > 1 ? mk[1] : Infinity, mD = mk.length > 2 ? mk[2] : Infinity, mE = mk.length > 3 ? mk[3] : Infinity;
  if (dp <= mC + tol) {
    const a = acc || {};
    for (const [k, st] of [["IO", "B-IO"], ["LS", "IO-LS"], ["CP", "LS-CP"]])
      if (a[k] == null || dp <= a[k] * env.sf_d + tol) return st;
    return ">CP";
  }
  if (dp <= mD + tol) return "C-D";
  if (dp <= mE + tol) return "D-E";
  return ">E";
}

/** Envelope force magnitude at total-deformation magnitude |d| on one side. */
export function uhEnvForce(side, ad) {
  const p = [[0, 0], ...side.points];
  for (let i = 1; i < p.length; i++)
    if (ad <= p[i][0]) return p[i - 1][1] + (p[i][1] - p[i - 1][1]) * (ad - p[i - 1][0]) / ((p[i][0] - p[i - 1][0]) || 1);
  return p[p.length - 1][1];
}

/** Total deformation on an envelope side where the plastic deformation equals dp. */
export function uhEnvAtPlastic(side, ke, dp) {
  const p = side.points;
  let prev = null;
  for (const [d, F] of p) {
    const pl = d - F / ke;
    if (pl >= dp - 1e-15) {
      if (!prev) return [d, F];
      const [d0, F0, pl0] = prev;
      const t = (dp - pl0) / ((pl - pl0) || 1);
      return [d0 + t * (d - d0), F0 + t * (F - F0)];
    }
    prev = [d, F, pl];
  }
  return null;
}

/** All user hinge locations of the model: [{mm, h, d, p, prop}]. */
export function uhLocations(model) {
  const out = [];
  if (!model) return out;
  const props = model.hinge_properties || {};
  for (const mm of model.members || []) {
    if (!isUserList(mm)) continue;
    for (const h of mm.hinges) {
      if (!h || !isNum(h.relative_distance)) continue;
      out.push({ mm, h, d: h.relative_distance, p: uhPointAt(mm, h.relative_distance), prop: props[h.property] || null });
    }
  }
  return out;
}

/** Location label of a result entry ("i", "j", or a relative distance). */
export function uhEntryDistance(h) {
  if (isNum(h.relative_distance)) return h.relative_distance;
  if (h.end === "i") return 0;
  if (h.end === "j") return 1;
  const v = parseFloat(h.end);
  return isFinite(v) ? v : 0;
}

/** Hinge result entries of a results case: {kind, name, hinges, steps, x, xLabel}. */
export function uhCaseList(results) {
  const out = [];
  if (!results) return out;
  for (const [n, pd] of Object.entries(results.pushover || {}))
    if ((pd.hinges || []).length) out.push({ key: "po:" + n, kind: "pushover", name: n, label: `${n} (pushover)`, data: pd, hinges: pd.hinges });
  for (const [n, cd] of Object.entries(results.nonlinear_static || {}))
    if (cd && cd.nonlinear && (cd.nonlinear.hinges || []).length) out.push({ key: "nl:" + n, kind: "nonlinear_static", name: n, label: `${n} (nonlinear static)`, data: cd, hinges: cd.nonlinear.hinges });
  for (const [n, td] of Object.entries(results.th_cases || {}))
    if (td && (td.hinges || []).length) out.push({ key: "th:" + n, kind: "th", name: n, label: `${n} (nonlinear time history)`, data: td, hinges: td.hinges });
  return out;
}

/** Number of steps of a case entry + the step axis values / label. */
export function uhSteps(c) {
  const n = Math.max(0, ...c.hinges.map(h => (h.rot || []).length));
  let axis = null, label = "Step", kind = "none";
  if (c.kind === "pushover") { axis = c.data.roof_disp; label = "Control displacement"; kind = "disp"; }
  else if (c.kind === "nonlinear_static") { axis = c.data.nonlinear.history && c.data.nonlinear.history.disp; label = "Monitored displacement"; kind = "disp"; }
  else if (c.kind === "th") { axis = c.data.t; label = "Time"; kind = "period"; }
  return { n, axis: Array.isArray(axis) ? axis : null, label, kind };
}

/** Model height (highest point) and base. */
function zRange(model) {
  let lo = Infinity, hi = -Infinity;
  for (const mm of model.members || []) for (const p of [mm.pi, mm.pj]) { lo = Math.min(lo, p[2]); hi = Math.max(hi, p[2]); }
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  return [lo, hi];
}

/** displacement function (point, step) → [ux, uy, uz] for a case entry, plus a
    note describing how the deformed shape is built. */
export function uhDeformer(model, results, c) {
  const [z0, z1] = zRange(model);
  const H = Math.max(z1 - z0, 1e-6);
  if (c.kind === "nonlinear_static") {
    const nd = c.data.node_disp || {};
    const nodes = (results && results.nodes) || {};
    const hist = (c.data.nonlinear.history || {}).disp || [];
    const last = hist.length ? hist[hist.length - 1] : 0;
    const ids = Object.keys(nd).filter(t => nodes[t]);
    const near = p => {
      let best = null, bd = Infinity;
      for (const t of ids) {
        const q = nodes[t];
        const dd = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2;
        if (dd < bd) { bd = dd; best = t; }
      }
      return bd < 1e-6 ? nd[best] : null;
    };
    const cache = new Map();
    if (ids.length) return {
      note: "final node displacements scaled by the monitored displacement of each step",
      at(p, k) {
        const key = p.join(",");
        if (!cache.has(key)) cache.set(key, near(p));
        const u = cache.get(key);
        if (!u) return null;
        const f = Math.abs(last) > 1e-15 ? (hist[k] ?? last) / last : 1;
        return [u[0] * f, u[1] * f, u[2] * f];
      },
    };
  }
  if (c.kind === "th") {
    const order = (results && results.story_order) || (model.stories || []).map(s => s.name);
    const elev = (results && results.story_elev) || Object.fromEntries((model.stories || []).map(s => [s.name, s.elevation]));
    const lv = order.filter(s => elev[s] != null).map(s => ({ z: elev[s], ux: (c.data.story_ux || {})[s] || [], uy: (c.data.story_uy || {})[s] || [] }))
      .sort((a, b) => a.z - b.z);
    return {
      note: "story displacement histories, interpolated over height",
      at(p, k) {
        let lo = { z: z0, ux: null, uy: null };
        for (const l of lv) {
          if (p[2] <= l.z + 1e-9) {
            const t = (p[2] - lo.z) / ((l.z - lo.z) || 1);
            const ux0 = lo.ux ? lo.ux[k] || 0 : 0, uy0 = lo.uy ? lo.uy[k] || 0 : 0;
            return [ux0 + t * ((l.ux[k] || 0) - ux0), uy0 + t * ((l.uy[k] || 0) - uy0), 0];
          }
          lo = l;
        }
        return lo.ux ? [lo.ux[k] || 0, lo.uy[k] || 0, 0] : [0, 0, 0];
      },
    };
  }
  // pushover (or fallback): lateral displacement linear over height, scaled to the control displacement
  const pc = (model.pushover_cases || {})[c.name] || {};
  const dirY = pc.direction === "Y";
  const roof = c.kind === "pushover" ? (c.data.roof_disp || []) : [];
  const n = Math.max(1, ...c.hinges.map(h => (h.rot || []).length));
  return {
    note: c.kind === "pushover" ? "schematic: lateral displacement linear over height, scaled to the control displacement of each step"
      : "schematic deformed shape",
    at(p, k) {
      const u = roof.length ? (roof[Math.min(k, roof.length - 1)] || 0) : 0.02 * H * k / n;
      const s = u * (p[2] - z0) / H;
      return dirY ? [0, s, 0] : [s, 0, 0];
    },
  };
}

/** Small chart helper: nice axis limit. */
export function uhNice(v) {
  if (!(v > 0)) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(v)));
  const m = v / e;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * e;
}
