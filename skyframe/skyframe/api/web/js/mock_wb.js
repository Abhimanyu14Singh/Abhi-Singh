/* SkyFrame Wave B — ?mock=1 mirror of the backend validation of the new
   fields (core/props_ext.validate_model): orthotropic materials, slab
   property types / laminates, section-level distribution, drop panels,
   named line / area springs and composite column sections.  The mock
   backend otherwise round-trips the model unchanged (POST /api/model). */

import { orthoProblem as mwbOrtho } from "./wb_ortho.js";
import { slabProblem as mwbSlab, weightThickness as mwbWeightT } from "./wb_slab.js";
import { wspProblem as mwbSpring } from "./wb_springs.js";
import { ccCheck as mwbCc, CC_TYPES as mwbCcTypes } from "./wb_composite.js";

const SLAB_TYPES = ["ribbed", "waffle", "deck_filled", "deck_unfilled", "deck_solid", "drop"];

/** First problem (backend wording) or "" — chained into app.js postModel. */
export function wbMockValidate(m) {
  if (!m || typeof m !== "object") return "";
  const mats = m.materials || {};
  for (const [n, mt] of Object.entries(mats)) {
    if (mt.symmetry === "orthotropic") { const e = mwbOrtho(mt); if (e) return `Material '${n}': ${e}`; }
    else if (mt.ortho != null) return `Material '${n}': 'ortho' constants need symmetry 'orthotropic'`;
  }
  for (const [n, s] of Object.entries(m.shell_sections || {})) {
    if (s.slab) {
      if (!SLAB_TYPES.includes(s.slab.type)) return `Shell section ${n}: slab.type must be one of ${SLAB_TYPES.join(", ")}`;
      const e = mwbSlab(s); if (e) return `Shell section ${n}: ${e}`;
      if (s.slab.type !== "drop" && mats[s.material]) {
        const t = mwbWeightT(s, mats);
        if (Math.abs(s.thickness - t) > 1e-6 * t) return `Shell section ${n}: thickness must be the weight-equivalent ${t.toPrecision(6)} m`;
      }
    }
    if (s.laminate) for (const l of s.laminate.layers || []) if (!mats[l.material]) return `Shell section ${n}: laminate material ${l.material} is not defined`;
    if (s.distribution != null && !["two_way", "one_way"].includes(s.distribution)) return `Shell section ${n}: distribution must be two_way|one_way`;
    if (s.one_way_dir != null && ![1, 2].includes(s.one_way_dir)) return `Shell section ${n}: one_way_dir must be 1 or 2`;
  }
  const lp = m.line_spring_properties || {}, ap = m.area_spring_properties || {};
  for (const [n, p] of Object.entries(lp)) { const e = mwbSpring(p); if (e) return `Line spring property '${n}': ${e}`; }
  for (const [n, p] of Object.entries(ap)) { const e = mwbSpring(p); if (e) return `Area spring property '${n}': ${e}`; }
  for (const ls of m.line_springs || []) {
    if (ls.property == null) continue;
    if (!lp[ls.property]) return `line spring: unknown line spring property '${ls.property}'`;
    if (ls.kx || ls.ky || ls.kz || ls.compression_only) return "line spring: a named property needs the inline kx / ky / kz = 0";
  }
  for (const r of m.shells || []) {
    const asp = r.area_spring;
    if (asp && asp.property != null) {
      if (!ap[asp.property]) return `Shell ${r.uid}: unknown area spring property '${asp.property}'`;
      if ((r.behavior || "shell") !== "shell") return `Shell ${r.uid}: area_spring needs shell behavior`;
    }
    const sec = (m.shell_sections || {})[r.section];
    if (sec && sec.slab && sec.slab.type === "drop" && r.kind === "slab" && r.behavior !== "shell") return `Shell ${r.uid}: a drop panel needs shell behavior`;
  }
  for (const [n, s] of Object.entries(m.sections || {})) {
    if (mwbCcTypes.includes(s.shape) && s.dims) { const e = mwbCc(s.shape, s.dims, mats); if (e) return `Section ${n}: ${e}`; }
  }
  return "";
}

export default { wbMockValidate };
