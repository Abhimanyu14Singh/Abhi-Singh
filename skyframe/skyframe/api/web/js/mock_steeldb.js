/* SkyFrame — ?mock=1 stand-ins for the steel section database endpoints
   (CONTRACT "Steel section database and parametric shapes"):

     GET  /api/steel_db/standards   → mockSdbStandards()
     GET  /api/steel_db/shapes      → mockSdbShapes({ standard, family, q, limit })
     POST /api/steel_db/import      → mockSdbImport(model, body)   (apply false: dicts only)

   Same JSON shapes as the backend, served from an embedded, verbatim
   42-shape subset of the AISC v16.0 file (js/mock_steeldb_data.js).
   Pure functions; the model is modified only by an applied import. */

import { MOCK_AISC as sdbMockAisc } from "./mock_steeldb_data.js";
import { ssFromRecord as sdbMockFromRecord } from "./secshapes.js";

const DBS = { aisc: sdbMockAisc };
const norm = s => String(s || "").replace(/\s+/g, "").toUpperCase();
const famsOf = d => [...new Set(d.shapes.map(s => s.family))];
const idOf = std => {
  const low = String(std || "aisc").toLowerCase();
  return Object.keys(DBS).find(k => k === low || DBS[k].standard.toLowerCase() === low) || null;
};

export function mockSdbStandards() {
  return Object.entries(DBS).map(([id, d]) => ({ id, standard: d.standard, edition: d.edition, count: d.shapes.length, source: d.source, families: famsOf(d) }));
}

export function mockSdbShapes({ standard = "aisc", family = null, q = "", limit = 0 } = {}) {
  const id = idOf(standard);
  if (!id) return { error: `unknown steel database ${standard}` };
  const fam = family ? String(family).toLowerCase() : null;
  const terms = String(q || "").split(/\s+/).filter(Boolean).map(norm);
  let recs = DBS[id].shapes.filter(s => (!fam || s.family.toLowerCase() === fam)
    && terms.every(t => (norm(s.name) + " " + norm(s.edi)).includes(t)));
  const total = recs.length;
  if (limit > 0) recs = recs.slice(0, limit);
  return { standard: id, family: family || null, q: q || "", total, count: recs.length, shapes: JSON.parse(JSON.stringify(recs)) };
}

export function mockSdbImport(model, body = {}) {
  const id = idOf(body.standard);
  if (!id) return { error: `unknown steel database ${body.standard}` };
  const names = Array.isArray(body.names) ? body.names : [];
  if (!names.length) return { error: "names must be a non-empty list of shape names" };
  if (!body.material) return { error: "material is required" };
  const apply = body.apply !== false;
  const recs = [];
  for (const n of names) {
    const r = DBS[id].shapes.find(s => s.name === n) || DBS[id].shapes.find(s => norm(s.name) === norm(n) || norm(s.edi) === norm(n));
    if (!r) return { error: `${DBS[id].standard}: unknown shape ${n}` };
    recs.push(r);
  }
  const out = { added: [], skipped: [], sections: {} };
  for (const r of recs) {
    const s = sdbMockFromRecord(r, id, body.material);
    out.sections[s.name] = s;
    if (apply && model) {
      model.sections = model.sections || {};
      if (model.sections[s.name]) { out.skipped.push(s.name); continue; }
      model.sections[s.name] = JSON.parse(JSON.stringify(s));
    }
    out.added.push(s.name);
  }
  if (apply && model) out.model = model;
  return out;
}

export default { mockSdbStandards, mockSdbShapes, mockSdbImport };
