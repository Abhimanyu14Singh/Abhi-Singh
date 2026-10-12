/* SkyFrame — damping options (CONTRACT "Rayleigh damping on springs, links
   and hinges" and "Material-proportional (composite) modal damping"),
   dlgkit standard.

     Analyze › Advanced › Damping Options…  → Damping Options:
       • Rayleigh on springs / links / hinges (model.damping_options:
         rayleigh_on_links none | elastic_only | all, rayleigh_stiffness_basis
         committed | current | initial) — written only when not the default;
       • Material damping ratios (model.material_damping {material: ζ}) for
         composite modal damping — omitted when empty;
       • Preview… → per-mode composite ζ and strain-energy shares
         (POST /api/damping/composite; ?mock=1 → js/mock_wbdamp.js).
     TH case dialog (js/dlgstd_cases.js hook) → damping model "Composite
       (material)" + the ratios in use + Preview…
     Modal case dialog (hook) → Composite Modal Damping Preview…

   OK with defaults is byte-identical (dkCommit, changed keys only). The
   model stays SI; ζ is dimensionless. */

import {
  el as doEl, esc as doEsc, clone as doClone, isNum as doIsNum, dkForm as doForm, dkGroup as doGroup,
  dkNote as doNote, dkDialog as doDialog, dkDraft as doDraft, dkCommit as doCommit,
} from "./dlgkit.js";
import { wbMockComposite as doMockComposite } from "./mock_wbdamp.js";

export const DO_ON_LINKS = [["none", "None — links / springs / hinges undamped (legacy)"], ["elastic_only", "Elastic springs and links only"], ["all", "All springs, links and hinges (recommended)"]];
export const DO_BASIS = [["committed", "Committed (last converged) stiffness"], ["current", "Current (trial) stiffness"], ["initial", "Initial stiffness (CSI default)"]];
const DEF = { rayleigh_on_links: "none", rayleigh_stiffness_basis: "committed" };

let SKY = null;
const isMock = () => !!(SKY && SKY.store && SKY.store.mock);

/** Client mirror of core.composite_damping.validate → message | null. */
export function doValidateModel(D) {
  const md = D.material_damping;
  if (md == null) return null;
  if (typeof md !== "object" || Array.isArray(md)) return "material_damping must be a {material: zeta} dict";
  for (const [k, z] of Object.entries(md)) {
    if (!(k in (D.materials || {}))) return `material_damping: unknown material '${k}'`;
    if (!(doIsNum(z) && z >= 0 && z < 1)) return `material_damping['${k}'] must be a damping ratio in [0, 1)`;
  }
  for (const [n, tc] of Object.entries(D.th_cases || {}))
    if (tc.damping_model != null && !["rayleigh", "modal", "composite"].includes(tc.damping_model)) return `TH case ${n}: damping_model must be rayleigh|modal|composite`;
  return null;
}

async function compositePost(body) {
  if (isMock()) { await new Promise(r => setTimeout(r, 60)); return doMockComposite(body.model, body); }
  const res = await fetch("/api/damping/composite", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}

const ratioText = md => Object.entries(md || {}).map(([k, z]) => `${k} ${+(+z).toFixed(4)}`).join(", ") || "none";

/** Per-mode composite ζ preview for a draft model (read only). */
export function openCompositePreview(sky, D, { damping = 0.05, material_damping, title } = {}) {
  const box = doEl("div", { class: "do-prev", id: "doPrevBody" }, [doNote("Computing the modal strain energies…")]);
  const dlg = doDialog({ id: "doPreview", title: title || "Composite Modal Damping", iconId: "exleaf-modal", size: "md", noCancel: true, body: box,
    intro: `ζ<sub>i</sub> = Σ ζ<sub>m</sub> E<sub>m,i</sub> / Σ E<sub>m,i</sub> — each mode's strain energy split by material; links, springs and materials without a ratio take ζ = ${doEsc(String(damping))}.` });
  const model = doClone(D);
  delete model._mock_params;
  compositePost({ model, damping, ...(material_damping ? { material_damping } : {}) }).then(r => {
    box.textContent = "";
    if (!r.modes || !r.modes.length) { box.appendChild(doNote("The model has no dynamic modes (no mass on free DOFs).")); return; }
    const groups = [...new Set(r.modes.flatMap(m => Object.keys(m.fractions || {})))];
    const t = doEl("table", { class: "data-table do-prev-tbl", id: "doPrevTable" });
    t.appendChild(doEl("thead", {}, [doEl("tr", {}, [doEl("th", { text: "Mode" }), doEl("th", { text: "Period (s)" }), doEl("th", { text: "ζ" }),
      ...groups.map(g => doEl("th", { text: `${g} share` }))])]));
    const tb = doEl("tbody");
    r.modes.forEach((m, i) => tb.appendChild(doEl("tr", {}, [doEl("td", { text: String(i + 1) }), doEl("td", { class: "num", text: (+m.period).toPrecision(4) }),
      doEl("td", { class: "num", text: (+m.zeta).toFixed(4) }), ...groups.map(g => doEl("td", { class: "num", text: ((m.fractions || {})[g] ?? 0).toFixed(3) }))])));
    t.appendChild(tb);
    box.append(t, doNote(`Material ratios: ${doEsc(ratioText(r.material_damping))}; others ζ = ${+(+r.default).toFixed(4)}.${r.mock ? " (mock: shares from element volume per material)" : ""}`));
  }).catch(e => { box.textContent = ""; box.appendChild(doEl("p", { class: "dk-err is-on", role: "alert", text: e.message })); });
  return dlg;
}

/** Analyze › Advanced › Damping Options… */
export function openDampingOptions(sky) {
  const D = doDraft(sky);
  const F = doForm();
  const o = { ...DEF, ...(D.damping_options || {}) };
  const mats = Object.keys(D.materials || {});
  const rows = Object.entries(D.material_damping || {}).map(([m, z]) => ({ m, z }));
  const tbl = F.table(rows, [
    { key: "m", label: "Material", type: "select", options: r => mats.filter(x => x === r.m || !rows.some(q => q.m === x)).map(x => [x, x]) },
    { key: "z", label: "Damping ratio ζ", type: "num" },
  ], {
    id: "doMatRows", addLabel: "+ Add material", empty: "No material ratios — every mode keeps the case's damping ratio.",
    newRow: () => { const m = mats.find(x => !rows.some(r => r.m === x)); return m ? { m, z: 0.05 } : null; },
    canAdd: () => mats.some(x => !rows.some(r => r.m === x)),
    validateRow: (r, i) => !(doIsNum(r.z) && r.z >= 0 && r.z < 1) ? `Row ${i + 1}: ζ must be in [0, 1).` : "",
    validate: rs => new Set(rs.map(r => r.m)).size !== rs.length ? "A material is listed twice — merge the rows." : "",
  });
  const prevBtn = doEl("button", { type: "button", class: "btn btn-small", id: "doPreviewBtn" }, ["Preview per-mode ζ…"]);
  prevBtn.addEventListener("click", () => {
    const md = Object.fromEntries(rows.filter(r => doIsNum(r.z)).map(r => [r.m, r.z]));
    openCompositePreview(sky, D, { damping: 0.05, material_damping: md });
  });
  const body = [
    doGroup("Rayleigh Damping on Springs, Links and Hinges", [
      F.select(o, "rayleigh_on_links", DO_ON_LINKS, { label: "Elements damped", id: "doOnLinks" }),
      F.select(o, "rayleigh_stiffness_basis", DO_BASIS, { label: "Stiffness for a₁K", id: "doBasis" }),
    ], { note: "Direct-integration time history: whether the zero-length / two-node link elements take the case's stiffness-proportional Rayleigh term. Dampers and friction / rubber bearings are never damped." }),
    doGroup("Material Damping (Composite Modal Damping)", [tbl, doEl("div", { class: "dk-table-acts" }, [prevBtn])],
      { note: "Used by time-history cases whose damping model is <b>Composite (material)</b>: each mode's ratio is the strain-energy weighted average of these ratios (links, springs and unlisted materials take the case's damping ratio)." }),
  ];
  return doDialog({
    id: "doDamping", title: "Damping Options", iconId: "exleaf-th", size: "md", form: F, body,
    onOk: () => {
      const isDef = o.rayleigh_on_links === DEF.rayleigh_on_links && o.rayleigh_stiffness_basis === DEF.rayleigh_stiffness_basis;
      if (F.fields.some(f => (f.key === "rayleigh_on_links" || f.key === "rayleigh_stiffness_basis") && f.dirty)) {
        if (isDef) delete D.damping_options;
        else D.damping_options = { rayleigh_on_links: o.rayleigh_on_links, rayleigh_stiffness_basis: o.rayleigh_stiffness_basis };
      }
      if (F.tableDirty(tbl)) {
        const md = Object.fromEntries(rows.map(r => [r.m, r.z]));
        if (Object.keys(md).length) D.material_damping = md; else delete D.material_damping;
      }
      doCommit(sky, D);
    },
  });
}

/** TH case dialog fields (Advanced): ratios in use + Preview…. */
export function doThFields(F, c, D) {
  const btn = doEl("button", { type: "button", class: "btn btn-small", id: "doThPreview" }, ["Preview composite ζ…"]);
  btn.addEventListener("click", () => openCompositePreview(SKY, D, { damping: doIsNum(c.damping) ? c.damping : 0.05 }));
  return [doEl("div", { class: "dk-row dk-row-wide do-th-row" }, [
    doEl("span", { class: "dk-label", text: "Composite (material) ratios" }),
    doEl("div", { class: "dk-ctl" }, [doEl("span", { class: "muted", id: "doThRatios", text: ratioText(D.material_damping) }), btn]),
    doEl("div", { class: "dk-hint muted", html: "Used when the damping model is <b>Composite (material)</b>; edit the ratios in Analyze › Advanced › Damping Options." }),
  ])];
}

/** Modal case dialog: composite preview button. */
export function doModalFields(F, st, D) {
  const btn = doEl("button", { type: "button", class: "btn btn-small", id: "doModalPreview" }, ["Composite Modal Damping Preview…"]);
  btn.addEventListener("click", () => openCompositePreview(SKY, D, { damping: 0.05 }));
  return [doGroup("Damping", [doEl("div", { class: "dk-row dk-row-wide" }, [doEl("span", { class: "dk-label", text: `Material ratios: ${ratioText(D.material_damping)}` }), doEl("div", { class: "dk-ctl" }, [btn])])])];
}

export function initDampOpts(sky) {
  SKY = sky;
  sky.openDampingOptions = () => openDampingOptions(sky);
  sky.wbDamp = { open: sky.openDampingOptions, preview: (D, o) => openCompositePreview(sky, D || sky.store.model, o), thFields: doThFields, modalFields: doModalFields, validateModel: doValidateModel };
  return sky.wbDamp;
}
