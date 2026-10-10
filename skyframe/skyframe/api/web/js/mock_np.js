/* SkyFrame mock (?mock=1) for nonprismatic sections + per-joint panel zones
   (CONTRACT "Nonprismatic sections and per-joint panel zones").

     mockValidateNp(model)        mirrors the backend ValueErrors of POST /api/model
     mockNpAdjustResults(model, r) plausible results: member deflections of a
                                  varying member scaled by I33(rep) / I33(eff),
                                  where 1/I_eff is the curvature-weighted mean
                                  of 1/I(x); panel-zone joints soften the
                                  lateral sway of their story slightly.

   The mock backend echoes the payload, so every new field round-trips
   byte-identically. */

import {
  npValidateModel as mnpValidateModel, pzValidateModel as mnpPzValidate, isNpSec as mnpIsNp,
  npVarying as mnpVarying, npLayout as mnpLayout, npPropsAt as mnpPropsAt, npRaw as mnpRaw,
  pzStiffness as mnpPzK,
} from "./npsect.js";

export function mockValidateNp(model) {
  if (!model) return "";
  return mnpValidateModel(model) || mnpPzValidate(model) || "";
}

const lenOf = mm => Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]);

/** I33(rep) / I33(eff) of a member using a varying nonprismatic section. */
function flexRatio(model, mm) {
  const sec = model.sections[mm.section];
  const L = lenOf(mm);
  const lay = mnpLayout(model, sec, L);
  if (lay.err || !lay.segs.length) return 1;
  const rep = mnpRaw(model.sections[lay.segs[0].sg.start_section]);
  let num = 0, den = 0;
  const N = 64;
  for (let i = 0; i < N; i++) {
    const t = (i + 0.5) / N;
    const p = mnpPropsAt(lay, t * L);
    if (!p || !(p.I33 > 0)) return 1;
    const w = t * (1 - t) + 0.05;                       // curvature weight (sag / sway mix)
    num += w / p.I33; den += w;
  }
  const Ieff = den / num;
  return rep && rep.I33 > 0 && Ieff > 0 ? rep.I33 / Ieff : 1;
}

export function mockNpAdjustResults(model, r) {
  if (!model || !r) return r;
  const ratios = {};
  for (const mm of model.members || []) {
    const sec = (model.sections || {})[mm.section];
    if (mnpIsNp(sec) && mnpVarying(model, sec)) ratios[mm.uid] = flexRatio(model, mm);
  }
  const scaleCase = cd => {
    if (!cd || !cd.member_deflections) return;
    for (const [uid, f] of Object.entries(ratios)) {
      const md = cd.member_deflections[uid];
      if (!md) continue;
      for (const k of ["dy", "dz"]) if (Array.isArray(md[k])) md[k] = md[k].map(v => +(v * f).toFixed(7));
    }
  };
  if (Object.keys(ratios).length)
    for (const blk of [r.cases, r.combos]) for (const cd of Object.values(blk || {})) { scaleCase(cd); if (cd && cd.min) scaleCase(cd.min); }

  // panel-zone flexibility: soften node translations above override joints a touch
  const pz = model.joint_panel_zones || [];
  if (pz.length) {
    let soft = 0;
    for (const e of pz) {
      const K = mnpPzK(model, e);
      if (isFinite(K) && K > 0) soft += Math.min(0.04, 2e4 / K * 0.01);
    }
    const f = 1 + Math.min(soft, 0.12);
    for (const blk of [r.cases, r.combos])
      for (const cd of Object.values(blk || {})) {
        if (!cd || !cd.node_disp) continue;
        for (const d of Object.values(cd.node_disp)) { d[0] *= f; d[1] *= f; }
      }
  }
  return r;
}
