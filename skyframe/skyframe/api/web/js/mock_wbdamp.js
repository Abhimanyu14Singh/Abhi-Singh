/* SkyFrame — ?mock=1 support for the wave-B link / damping features
   (CONTRACT "Link types: Plastic (Wen), BRB, bilinear damper, T/C friction
   isolator", "Frequency-dependent link properties", "Material-proportional
   (composite) modal damping").

     wbMockValidate(model)        POST /api/model checks (link params via
                                  js/linkext.js, link_freq_props,
                                  material_damping, damping_model)
     wbMockComposite(model, body) POST /api/damping/composite — same shape;
                                  shares from the element VOLUME per material
                                  (frames A·L, shells area·t; links "other")
                                  for every mode (plausible, not modal energy)
     wbMockAugment(model, res)    TH results of composite cases get
                                  composite_damping; SS / PSD results get the
                                  frequency-dependent-links warning
     wbMockTables(H)              DB-edit tables mirroring core/linkdamp_tables.py */

import { lxValidate as wbLxValidate, LX_TYPES as WB_LX_TYPES } from "./linkext.js";
import { lfValidateModel as wbLfValidate } from "./linkfreq.js";

const isNum = v => typeof v === "number" && isFinite(v);
const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);

export function wbMockValidate(model) {
  for (const l of model.links || []) {
    if (!WB_LX_TYPES[l.link_type]) continue;
    const e = wbLxValidate(l.uid, l.link_type, l.params || {});
    if (e) return e;
  }
  const md = model.material_damping;
  if (md != null) {
    for (const [k, z] of Object.entries(md)) {
      if (!(k in (model.materials || {}))) return `material_damping: unknown material '${k}'`;
      if (!(isNum(z) && z >= 0 && z < 1)) return `material_damping['${k}'] must be a damping ratio in [0, 1)`;
    }
  }
  for (const [n, tc] of Object.entries(model.th_cases || {}))
    if (tc.damping_model != null && !["rayleigh", "modal", "composite"].includes(tc.damping_model)) return `TH case ${n}: damping_model must be rayleigh|modal|composite, got '${tc.damping_model}'`;
  return wbLfValidate(model);
}

function volumes(model) {
  const V = {};
  const add = (m, v) => { if (m && v > 0) V[m] = (V[m] || 0) + v; };
  for (const mb of model.members || []) {
    const s = (model.sections || {})[mb.section] || {};
    const A = isNum(s.A) ? s.A : (isNum(s.b) && isNum(s.h) ? s.b * s.h : 0.1);
    add(s.material, A * dist(mb.pi, mb.pj));
  }
  for (const r of model.shells || []) {
    const s = (model.shell_sections || {})[r.section] || {};
    const c = r.corners || [];
    let area = 0;
    if (c.length >= 3) {
      let nx = 0, ny = 0, nz = 0;
      for (let i = 0; i < c.length; i++) {
        const p = c[i], q = c[(i + 1) % c.length];
        nx += p[1] * q[2] - p[2] * q[1]; ny += p[2] * q[0] - p[0] * q[2]; nz += p[0] * q[1] - p[1] * q[0];
      }
      area = 0.5 * Math.hypot(nx, ny, nz);
    }
    add(s.material, area * (isNum(s.thickness) ? s.thickness : 0.2));
  }
  if ((model.links || []).length) V.other = 0.02 * Object.values(V).reduce((a, b) => a + b, 0) || 1;
  return V;
}

export function wbMockComposite(model, body = {}) {
  model = model || {};
  const md = body.material_damping || model.material_damping || {};
  for (const k of Object.keys(md)) if (!(k in (model.materials || {}))) throw new Error(`material_damping: unknown material '${k}'`);
  const zdef = isNum(body.damping) ? body.damping : 0.05;
  const V = volumes(model);
  const tot = Object.values(V).reduce((a, b) => a + b, 0);
  const n = Math.max(1, Math.min(isNum(body.num_modes) ? body.num_modes : (model.num_modes || 3), 12));
  const T1 = 0.1 * Math.max(1, (model.stories || []).length) + 0.05;
  const fractions = Object.fromEntries(Object.entries(V).map(([g, v]) => [g, tot > 0 ? v / tot : 0]));
  const zeta = tot > 0 ? Object.entries(V).reduce((s, [g, v]) => s + v * (isNum(md[g]) ? md[g] : zdef), 0) / tot : zdef;
  const modes = Array.from({ length: n }, (_, i) => ({ period: +(T1 / (2 * i + 1)).toPrecision(6), zeta, fractions: { ...fractions }, energy_check: 1 }));
  return { default: zdef, material_damping: { ...md }, modes, mock: true };
}

export function wbMockAugment(model, res) {
  if (!model || !res) return res;
  for (const [n, tc] of Object.entries(model.th_cases || {}))
    if (tc.damping_model === "composite" && res.th_cases && res.th_cases[n] && !res.th_cases[n].composite_damping)
      res.th_cases[n].composite_damping = wbMockComposite(model, { damping: tc.damping });
  const props = model.link_freq_props || {};
  const labels = Object.entries(props).flatMap(([nm, p]) => (p.links || []).flatMap(u => Object.keys(p.dofs || {}).map(d => `${nm}:${u}:${d}`)));
  if (labels.length) for (const k of ["steady_state", "psd"]) for (const r of Object.values(res[k] || {})) {
    r.warnings = Array.isArray(r.warnings) ? r.warnings : [];
    const w = "frequency-dependent link properties applied: " + labels.join(", ");
    if (!r.warnings.includes(w)) r.warnings.push(w);
  }
  return res;
}

/* ---------------------------------------------------------------- DB edit */
export function wbMockTables(H) {
  const { col, obj, names, G, E, parseCell, clone } = H;
  const n = "number";
  const DOFS = ["UX", "UY", "UZ", "RX", "RY", "RZ", "AXIAL"];
  const pointsRows = (t, d) => {
    const out = [];
    for (const [pn, p] of Object.entries(d.link_freq_props || {})) for (const [dof, tab] of Object.entries(p.dofs || {}))
      tab.forEach((r, k) => out.push({ _id: [pn, dof, k], property: pn, dof, f: r[0], K: r[1], C: r[2] }));
    return out;
  };
  const linkRows = (t, d) => {
    const out = [];
    for (const [pn, p] of Object.entries(d.link_freq_props || {})) (p.links || []).forEach((u, k) => out.push({ _id: [pn, "", k], property: pn, link: u }));
    return out;
  };
  const parseRows = (t, d, rows, errs, keys) => {
    const n0 = errs.length, props = d.link_freq_props || {}, built = {};
    rows.forEach((row, i) => {
      const pn = typeof row.property === "string" ? row.property.trim() : row.property;
      if (!(pn in props)) { errs.push(E(t.key, i, "property", `unknown Property '${pn}'`)); return; }
      const vals = {};
      for (const k of keys) {
        const c = t.cols.find(x => x.key === k);
        try { vals[k] = parseCell(c, row[k] ?? (c.def ?? null), d); } catch (e) { errs.push(E(t.key, i, k, `${c.label}: ${e.message}`)); return; }
      }
      (built[pn] = built[pn] || []).push(vals);
    });
    return errs.length > n0 ? null : built;
  };
  return [
    { key: "material_damping", title: "Material Properties - Modal Damping Ratios", group: G.DEF, tkind: "child", editable: true, canAdd: true, canDelete: true, keycol: null,
      cols: [col("material", "Material", "enum", "none", { enum: names("materials"), required: true }), col("zeta", "Damping Ratio", n, "none", { required: true })],
      container: null, field: "material_damping", childCol: "material", valueCol: "zeta" },
    obj("link_freq_props", "Link Frequency-Dependent Properties", G.DEF, [col("name", "Property", "text", "none", { required: true })],
      { container: "link_freq_props", keycol: "name", nameInObj: false, newObj: () => ({ dofs: {}, links: [] }) }),
    { key: "link_freq_points", title: "Link Frequency-Dependent Properties - Tables", group: G.DEF, tkind: "wb", editable: true, canAdd: true, canDelete: true, keycol: null,
      cols: [col("property", "Property", "enum", "none", { enum: names("link_freq_props"), required: true }), col("dof", "DOF", "enum", "none", { enum: DOFS, required: true }),
        col("f", "Frequency (Hz)", n, "none", { required: true }), col("K", "Stiffness K", n, "none", { required: true }), col("C", "Damping C", n, "none", { def: 0 })],
      w8rows: pointsRows,
      w8apply: (t, d, rows, errs) => {
        const built = parseRows(t, d, rows, errs, ["dof", "f", "K", "C"]);
        if (!built) return {};
        for (const [pn, p] of Object.entries(d.link_freq_props || {})) {
          const dofs = {};
          for (const v of built[pn] || []) (dofs[v.dof] = dofs[v.dof] || []).push([v.f, v.K, v.C]);
          if (JSON.stringify(dofs) !== JSON.stringify(p.dofs || {})) p.dofs = dofs;
        }
        return { rows: rows.length };
      } },
    { key: "link_freq_links", title: "Link Frequency-Dependent Properties - Link Assignments", group: G.OBJ, tkind: "wb", editable: true, canAdd: true, canDelete: true, keycol: null,
      cols: [col("property", "Property", "enum", "none", { enum: names("link_freq_props"), required: true }), col("link", "Link", "ref", "none", { ref: "links", required: true })],
      w8rows: linkRows,
      w8apply: (t, d, rows, errs) => {
        const built = parseRows(t, d, rows, errs, ["link"]);
        if (!built) return {};
        for (const [pn, p] of Object.entries(d.link_freq_props || {})) {
          const links = (built[pn] || []).map(v => v.link);
          if (JSON.stringify(links) !== JSON.stringify(p.links || [])) p.links = clone(links);
        }
        return { rows: rows.length };
      } },
  ];
}
