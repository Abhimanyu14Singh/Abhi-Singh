/* SkyFrame — ?mock=1 stand-ins for the built-up section endpoints
   (CONTRACT "Built-up sections"):
     POST /api/sections/builtup {action: properties|upsert|delete}
     GET  /api/sections/builtup/templates
     POST /api/sections/builtup/template
     GET  /api/sections/builtup/db/{standards,families,shapes}
   Properties come from js/builtup_geom.js (the same exact formulas as the
   backend). The shape database is the backend STUB: the W-shapes of
   skyframe/core/sections_library.py (AISC Manual 15th ed. Table 1-1: A, Ix,
   Iy, J, d, bf) with tw/tf of skyframe/core/modifiers.py, plus angles whose
   dims are their designation. */

import { buSectionProperties as mbuProps, buFrameSection as mbuFrame, buNormSection as mbuNorm,
  buTemplateSection as mbuTemplateSection, BU_TEMPLATES as MBU_TEMPLATES, buSnapshotRecord as mbuSnap } from "./builtup_geom.js";

const IN = 0.0254;
// label: [A in², Ix in⁴, Iy in⁴, J in⁴, d in, bf in, tw in, tf in]
const W = {
  W8x31: [9.13, 110, 37.1, 0.536, 8.00, 8.00, 0.285, 0.435], W8x40: [11.7, 146, 49.1, 1.12, 8.25, 8.07, 0.360, 0.560],
  W10x33: [9.71, 171, 36.6, 0.583, 9.73, 7.96, 0.290, 0.435], W10x49: [14.4, 272, 93.4, 1.39, 9.98, 10.00, 0.340, 0.560],
  W12x26: [7.65, 204, 17.3, 0.300, 12.20, 6.49, 0.230, 0.380], W12x40: [11.7, 307, 44.1, 0.906, 11.90, 8.01, 0.295, 0.515],
  W12x65: [19.1, 533, 174, 2.18, 12.10, 12.00, 0.390, 0.605], W14x30: [8.85, 291, 19.6, 0.380, 13.80, 6.73, 0.270, 0.385],
  W14x48: [14.1, 484, 51.4, 1.45, 13.80, 8.03, 0.340, 0.595], W14x90: [26.5, 999, 362, 4.06, 14.00, 14.50, 0.440, 0.710],
  W16x36: [10.6, 448, 24.5, 0.545, 15.90, 6.99, 0.295, 0.430], W16x57: [16.8, 758, 43.1, 2.22, 16.40, 7.12, 0.430, 0.715],
  W18x50: [14.7, 800, 40.1, 1.24, 18.00, 7.50, 0.355, 0.570], W18x76: [22.3, 1330, 152, 2.83, 18.20, 11.00, 0.425, 0.680],
  W21x62: [18.3, 1330, 57.5, 1.83, 21.00, 8.24, 0.400, 0.615], W21x93: [27.3, 2070, 92.9, 6.03, 21.60, 8.42, 0.580, 0.930],
  W24x76: [22.4, 2100, 82.5, 2.68, 23.90, 8.99, 0.440, 0.680], W24x104: [30.7, 3100, 259, 4.72, 24.10, 12.80, 0.500, 0.750],
  W27x94: [27.6, 3270, 124, 4.03, 26.90, 10.00, 0.490, 0.745], W30x116: [34.2, 4930, 164, 6.43, 30.00, 10.50, 0.565, 0.850],
  W33x130: [38.3, 6710, 218, 7.37, 33.10, 11.50, 0.580, 0.855], W36x150: [44.3, 9040, 270, 10.10, 35.90, 12.00, 0.625, 0.940],
};
const L = [[3, 3, 0.25, "L3X3X1/4"], [3, 3, 0.375, "L3X3X3/8"], [4, 4, 0.375, "L4X4X3/8"], [4, 4, 0.5, "L4X4X1/2"],
  [5, 5, 0.5, "L5X5X1/2"], [6, 6, 0.5, "L6X6X1/2"], [6, 4, 0.5, "L6X4X1/2"], [5, 3, 0.375, "L5X3X3/8"]];

let RECS = null;
function recs() {
  if (RECS) return RECS;
  RECS = Object.entries(W).map(([lab, [A, Ix, Iy, J, d, bf, tw, tf]]) => ({
    name: lab.toUpperCase(), family: "W", type: "I",
    dims: { d: d * IN, bf: bf * IN, tf: tf * IN, tw: tw * IN },
    props: { A: A * IN ** 2, I33: Ix * IN ** 4, I22: Iy * IN ** 4, J: J * IN ** 4 },
  }));
  for (const [d, b, t, lab] of L)
    RECS.push({ name: lab, family: "L", type: "angle", dims: { d: d * IN, b: b * IN, t: t * IN }, props: {}, computed: ["A", "I33", "I22", "J"] });
  return RECS;
}
const key = s => String(s).toUpperCase().replace(/\s/g, "");
const chkStd = s => { if (String(s).toLowerCase() !== "aisc") throw new Error(`unknown steel standard ${s} (stub database: only 'aisc')`); };

export function mbuStandards() {
  return { standards: [{ id: "aisc", standard: "AISC", edition: "stub: sections_library W-shapes + designation angles", count: recs().length }], source: "stub" };
}
export function mbuFamilies(standard) { chkStd(standard); return { families: [...new Set(recs().map(r => r.family))] }; }
export function mbuShapes(standard, family, q, limit = 200) {
  chkStd(standard);
  const qq = key(q || "");
  const rows = recs().filter(r => (!family || r.family === family) && (!qq || key(r.name).includes(qq)));
  return { shapes: rows.slice(0, Math.max(1, limit)).map(r => JSON.parse(JSON.stringify(r))), total: rows.length };
}
export function mbuShape(standard, name) {
  chkStd(String(standard).toLowerCase().replace(/[\s/]/g, "_"));
  const r = recs().find(x => key(x.name) === key(name));
  if (!r) throw new Error(`unknown shape ${name} in standard ${standard}`);
  return JSON.parse(JSON.stringify(r));
}
export function mbuTemplates() {
  return { templates: Object.entries(MBU_TEMPLATES).map(([id, t]) => ({ id, label: t.label, params: { ...t.params } })), fem: false, db_source: "stub" };
}
export function mbuTemplate(model, { id, params, name, material } = {}) {
  const section = mbuTemplateSection(id, params || {}, name || "BU1", material || "", mbuShape);
  const mats = (model && model.materials) || {};
  return { section, properties: mats[section.material] ? mbuProps(section, mats) : null };
}
/** Resolve db components that lack a snapshot (like resolve_components). */
export function mbuResolve(section) {
  for (const c of section.components || [])
    if (c.kind === "db" && !(c.type && c.dims)) mbuSnap(c, mbuShape(c.standard, c.name));
  return section;
}
export function mbuProperties(model, section) {
  const sec = mbuResolve(mbuNorm(section));
  return { properties: mbuProps(sec, model.materials || {}), section: sec };
}
/** POST /api/sections/builtup upsert: store + mirror (keeps modifiers); returns the model. */
export function mbuUpsert(model, section) {
  const sec = mbuResolve(mbuNorm(section));
  if ((model.designer_sections || {})[sec.name]) throw new Error(`Built-up section ${sec.name}: the name is used by a Section Designer section`);
  const props = mbuProps(sec, model.materials || {});
  model.builtup_sections = model.builtup_sections || {};
  model.builtup_sections[sec.name] = sec;
  model.sections[sec.name] = mbuFrame(sec, props, model.sections[sec.name]);
  return { model, properties: props, section: sec };
}
export function mbuDelete(model, name) {
  if (!(model.builtup_sections || {})[name]) throw new Error(`unknown built-up section ${name}`);
  const used = (model.members || []).filter(m => m.section === name).map(m => m.uid);
  if (used.length) throw new Error(`Built-up section ${name}: still used by member(s) ${used.join(", ")}`);
  delete model.builtup_sections[name];
  if (!Object.keys(model.builtup_sections).length) delete model.builtup_sections;
  delete model.sections[name];
  return { model };
}
