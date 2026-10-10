/* SkyFrame mock backend — user-defined hinges (CONTRACT "User-defined hinges
   and hinge overwrites"): POST /api/model validation mirror and plausible
   per-hinge result histories for pushover / nonlinear static / nonlinear
   TH cases, built with the same envelope + state classification as the
   engine (js/uhcore.js mirrors core/user_hinges.py).  The deformation
   demand is a mock (drift-proportional), the force follows the envelope
   with elastic unloading at k_e. */

import { uhValidateProperty as muValidateProp, uhValidateMemberHinges as muValidateMember,
  uhBuildEnvelope as muEnvelope, uhClassify as muClassify, uhEnvForce as muEnvForce,
  uhKMember as muKMember, uhLen as muLen, isUserList as muIsUser, UH_LEGACY_STATE as MU_LEGACY } from "./uhcore.js";

/** POST /api/model mirror → error message | null. */
export function mockValidateUserHinges(model) {
  if (!model) return null;
  const hp = model.hinge_properties;
  if (hp != null) {
    if (typeof hp !== "object" || Array.isArray(hp)) return "hinge_properties must be an object";
    for (const [n, p] of Object.entries(hp)) {
      const e = muValidateProp(n, p);
      if (e) return e;
    }
  }
  for (const mm of model.members || []) {
    if (!muIsUser(mm) && mm.hinge_overwrites == null) continue;
    const e = muValidateMember(model, mm);
    if (e) return e;
  }
  return null;
}

const hash = s => { let h = 2166136261; for (const c of String(s)) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return ((h >>> 0) % 1000) / 1000; };

/** One hinge history from a total-deformation demand series. */
function history(prop, env, demand) {
  const rot = [], moment = [], rot_plastic = [], hinge_state = [], state = [];
  let dp = 0;
  const ke = env.k_e;
  for (const d of demand) {
    let F = ke * (d - dp);
    const side = d >= 0 ? env.positive : env.negative;
    if (Math.sign(F) === Math.sign(d) || d === 0) {
      const cap = muEnvForce(side, Math.abs(d));
      if (Math.abs(F) > cap) { F = Math.sign(F || 1) * cap; dp = d - F / ke; }
    } else {
      const other = d >= 0 ? env.negative : env.positive;
      const cap = other.points[0][1];
      if (Math.abs(F) > cap) { F = Math.sign(F) * cap; dp = d - F / ke; }
    }
    const st = muClassify(d, F, env, prop.acceptance);
    rot.push(d); moment.push(F);
    rot_plastic.push(Math.max(0, Math.abs(d) - Math.abs(F) / ke));
    hinge_state.push(st); state.push(MU_LEGACY[st]);
  }
  return { rot, moment, rot_plastic, hinge_state, state };
}

/** Result entries for every lumped user hinge of the model; demandOf(mm, h,
    prop, env) → total deformation per step. */
function entries(model, demandOf) {
  const out = [];
  const props = model.hinge_properties || {};
  for (const mm of model.members || []) {
    if (!muIsUser(mm)) continue;
    for (const h of mm.hinges) {
      const prop = props[h.property];
      if (!prop || prop.type === "PMM_fiber" || !prop.backbone) continue;
      const km = muKMember(prop.type, model, mm);
      const env = muEnvelope(prop, km);
      const hist = history(prop, env, demandOf(mm, h, prop, env));
      const mk = env.positive.markers, n = env.positive.n_user;
      const sf = env.sf_d, acc = prop.acceptance || {};
      const d = h.relative_distance;
      out.push({
        uid: mm.uid, end: d === 0 ? "i" : d === 1 ? "j" : String(d), relative_distance: d,
        property: h.property, type: prop.type, user: true, kind: "user",
        My: env.F_B, thy: env.positive.points[0][0],
        a: mk.length > 1 ? mk[1] : null, b: n > 2 ? mk[n - 1] : null,
        c: env.positive.points[n - 1][1] / env.F_B,
        IO: acc.IO != null ? acc.IO * sf : null, LS: acc.LS != null ? acc.LS * sf : null, CP: acc.CP != null ? acc.CP * sf : null,
        k_elastic: env.k_e, hysteresis: prop.hysteresis || "kinematic", drop_strength: prop.drop_strength || "drops",
        material: (prop.hysteresis || "kinematic") === "kinematic" || prop.hysteresis === "isotropic" ? "MultiLinear"
          : (Array.isArray(prop.backbone) && prop.backbone.length <= 4 ? "Hysteretic" : "HystereticSM"),
        envelope: { positive: env.positive.points, negative: env.negative.points },
        ...hist,
      });
    }
  }
  return out;
}

/** Hinge demand: drift ratio θ at the step → hinge total deformation. */
function demandFactory(model, thetas) {
  return (mm, h, prop, env) => {
    const L = muLen(mm);
    const sg = h.relative_distance <= 0.5 ? 1 : -1;
    const amp = 0.85 + 0.3 * hash(mm.uid + "@" + h.relative_distance);
    const beam = mm.kind === "beam";
    let f;
    if (prop.type === "M3" || prop.type === "M2") f = (beam ? 1.25 : 1.0) * amp;
    else if (prop.type === "P") f = 0.004 * L * amp;
    else f = 0.03 * L * amp;
    // make sure the hinge yields part-way: at least 1.5 x its yield deformation at the peak
    const peak = Math.max(1e-12, ...thetas.map(Math.abs));
    const dy = env.positive.points[0][0];
    if (f * peak < 1.5 * dy) f = 1.5 * dy / peak * (1 + 2 * hash(mm.uid));
    return thetas.map(t => sg * f * t);
  };
}

const roofH = model => Math.max(1e-6, ...(model.members || []).flatMap(mm => [mm.pi[2], mm.pj[2]]));

/** Append user-hinge entries to the mock results (in place). */
export function mockAugmentUserHinges(model, r) {
  if (!model || !r || !(model.members || []).some(muIsUser)) return r;
  const H = roofH(model);
  const userUids = new Set(model.members.filter(muIsUser).map(mm => mm.uid));
  const peaks = list => {
    const out = {};
    for (const e of list) if (e.type === "M3" || e.type === "M2") out[e.uid] = Math.max(out[e.uid] || 0, ...e.rot.map(Math.abs));
    return out;
  };
  for (const [n, pd] of Object.entries(r.pushover || {})) {
    if (!(model.pushover_cases || {})[n]) continue;
    const th = (pd.roof_drift || []).map(Math.abs);
    const list = entries(model, demandFactory(model, th));
    pd.hinges = (pd.hinges || []).filter(e => !userUids.has(e.uid)).concat(list);
    pd.hinge_rotations = pd.hinge_rotations || {};
    for (const u of userUids) delete pd.hinge_rotations[u];
    Object.assign(pd.hinge_rotations, peaks(list));
  }
  for (const [n, cd] of Object.entries(r.nonlinear_static || {})) {
    if (!cd || !cd.nonlinear || !(model.nonlinear_static_cases || {})[n]) continue;
    const th = ((cd.nonlinear.history || {}).disp || []).map(d => d / H);
    if (!th.length) continue;
    const list = entries(model, demandFactory(model, th));
    const nl = cd.nonlinear;
    nl.hinges = (nl.hinges || []).filter(e => !userUids.has(e.uid)).concat(list);
    nl.hinge_rotations = nl.hinge_rotations || {};
    for (const u of userUids) delete nl.hinge_rotations[u];
    Object.assign(nl.hinge_rotations, peaks(list));
    nl.yielded = [...new Set([...(nl.yielded || []).filter(u => !userUids.has(u)),
      ...list.filter(e => e.hinge_state.some(s => s !== "A-B")).map(e => e.uid)])].sort();
  }
  for (const [n, td] of Object.entries(r.th_cases || {})) {
    const tc = (model.th_cases || {})[n];
    if (!tc || !tc.nonlinear || !td) continue;
    const order = r.story_order || (model.stories || []).map(s => s.name);
    const top = order[order.length - 1];
    const tr = ((tc.direction === "Y" ? td.story_uy : td.story_ux) || {})[top] || [];
    if (!tr.length) continue;
    const list = entries(model, demandFactory(model, tr.map(u => u / H)));
    td.hinges = list;
    td.hinge_rotations = td.hinge_rotations || {};
    for (const u of userUids) delete td.hinge_rotations[u];
    Object.assign(td.hinge_rotations, peaks(list));
    td.yielded = [...new Set([...(td.yielded || []).filter(u => !userUids.has(u)),
      ...list.filter(e => e.hinge_state.some(s => s !== "A-B")).map(e => e.uid)])].sort();
  }
  return r;
}
