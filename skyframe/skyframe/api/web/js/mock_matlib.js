/* SkyFrame — ?mock=1 stand-ins for the v1.21 material-library endpoints
   (CONTRACT "v1.21 material library"):

     GET /api/material_db/standards              → mlMockStandards()
     GET /api/material_db/materials?standard=…   → mlMockMaterials(std, kind, q)
     POST /api/material_db/import                → mlMockImport(model, body)

   Same JSON shapes as the backend. The records come from js/matlib_data.js
   (a generated copy of skyframe/data/materials/*.json, loaded lazily) and
   the per-record `material` is the exact Material.to_dict() the backend
   stores (record SI → kN/m/tonne/kPa: E, fy, fu, fc, unit_weight,
   mass_density ÷ 1000; G / mass_per_volume / Fye / Fue echoes). */

export const ML_STANDARD_IDS = ["astm_aci", "eurocode", "is", "bs", "as_nzs"];
ML_STANDARD_IDS.push("gb", "csa", "jis", "ks", "gost");   // Wave C hook: regional libraries (data/materials/*.json)
export const ML_KINDS = ["steel", "concrete", "rebar", "tendon", "aluminum", "cold_formed"];
const ML_KIND_TO_TYPE = { steel: "steel", concrete: "concrete", rebar: "rebar", tendon: "tendon", aluminum: "aluminum", cold_formed: "coldformed" };
const ML_KIND_COLOR = { steel: "#3b6ea5", concrete: "#8a8f98", rebar: "#c0603a", tendon: "#9a7bce", aluminum: "#9aa3ad", cold_formed: "#5a8fa5" };

let mlData = null;
async function mlLoad() {
  if (!mlData) mlData = (await import("./matlib_data.js")).MATLIB_DATA;
  return mlData;
}
const mlCopy = o => JSON.parse(JSON.stringify(o));
const mlKpa = v => v == null ? null : v / 1000;

/** Library record → the Material.to_dict() an import stores (backend parity). */
export function mlRecordToMaterial(rec, stdTitle, name) {
  const kind = rec.kind;
  const m = {
    name: name || rec.name,
    E: rec.E / 1000,
    nu: rec.nu,
    unit_weight: rec.unit_weight / 1000,
    material_type: ML_KIND_TO_TYPE[kind],
    symmetry: (kind === "rebar" || kind === "tendon") ? "uniaxial" : "isotropic",
    mass_density: rec.mass_density / 1000,
    alpha: rec.alpha,
    fc: mlKpa(rec.fc), fy: mlKpa(rec.fy), fu: mlKpa(rec.fu),
    Ry: rec.Ry != null ? rec.Ry : 1.1,
    damping: 0, lightweight: false, lam: 1,
    color: ML_KIND_COLOR[kind],
    notes: `${stdTitle ? stdTitle + " library: " : "Library: "}${rec.name} — ${rec.source}`,
    stress_strain: null,
  };
  m.G = m.E / (2 * (1 + m.nu));
  m.mass_per_volume = m.mass_density;
  m.Fye = m.fy != null ? m.Ry * m.fy : null;
  m.Fue = m.fu != null ? m.Ry * m.fu : null;
  return m;
}

export async function mlMockStandards() {
  const D = await mlLoad();
  return ML_STANDARD_IDS.map(id => {
    const d = D[id];
    const kinds = {};
    for (const r of d.materials) kinds[r.kind] = (kinds[r.kind] || 0) + 1;
    return { id, standard: d.standard, edition: d.edition, count: d.materials.length, kinds, notes: d.notes || "" };
  });
}

export async function mlMockMaterials(std, kind = null, q = "") {
  const D = await mlLoad();
  const d = D[std];
  if (!d) throw new Error(`unknown material standard '${std}'`);
  if (kind && !ML_KINDS.includes(kind)) throw new Error(`kind must be one of ${ML_KINDS.join(", ")}`);
  const qq = String(q || "").trim().toLowerCase();
  const out = d.materials
    .filter(r => (!kind || r.kind === kind) && (!qq || r.name.toLowerCase().includes(qq) || r.group.toLowerCase().includes(qq)))
    .map(r => ({ ...mlCopy(r), material: mlRecordToMaterial(r, d.standard) }));
  return { standard: std, materials: out };
}

/** Import into a model dict (mock POST /api/material_db/import). */
export async function mlMockImport(model, { standard, names, on_clash = "suffix", rename = {} } = {}) {
  const D = await mlLoad();
  const d = D[standard];
  if (!d) throw new Error(`unknown material standard '${standard}'`);
  if (!Array.isArray(names) || !names.length) throw new Error("'names' must be a non-empty list");
  model.materials = model.materials || {};
  const taken = new Set(Object.keys(model.materials));
  const plan = [];
  for (const nm of names) {
    const rec = d.materials.find(r => r.name === nm);
    if (!rec) throw new Error(`no material '${nm}' in the '${standard}' library`);
    let target = String((rename && rename[nm]) || nm), replaced = false;
    if (taken.has(target)) {
      if (on_clash === "error") throw new Error(`material '${target}' already exists`);
      if (on_clash === "replace" && model.materials[target]) replaced = true;
      else { let i = 2; while (taken.has(`${target}-${i}`)) i++; target = `${target}-${i}`; }
    }
    taken.add(target);
    plan.push([rec, target, replaced]);
  }
  for (const [rec, target] of plan) model.materials[target] = mlRecordToMaterial(rec, d.standard, target);
  return { imported: plan.map(([rec, target, replaced]) => ({ name: rec.name, as: target, replaced })), model };
}
