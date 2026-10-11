/* SkyFrame — standard (dlgkit) "Define" dialogs, ETABS layout:

     Define › Material Properties…        model.materials
     Define › Frame Sections…             model.sections (+ library import)
     Define › Slab / Wall Sections…       model.shell_sections
     Define › Load Patterns…              model.patterns (name · type · self
                                          weight multiplier · auto lateral)
     Define › Functions (RS / TH)…        model.spectrum_functions / th_functions
     Define › Stories and Grid Systems…   model.stories / grid_systems

   Every "Define …" list edits a DRAFT of the model (dkDraft); the per-item
   "… Data" dialog edits a copy of one item and writes it back into the draft
   on OK; only the list's OK commits (dkCommit: changed keys only — OK with
   defaults is byte-identical, Cancel discards). The full legacy editors stay
   one click away ("More…" buttons) for the rarely used tools (stress–strain
   laws, section designer, layered shells, multiple grid systems). */

import * as dsME from "./modeledit.js";
import dsU from "./units.js";
import { FRAME_MODS as dsFRAME_MODS, SHELL_MODS as dsSHELL_MODS } from "./assigndlg.js";
import {
  el, esc, clone, isNum, dkForm, dkGroup, dkAdvanced, dkTabs, dkList, dkNote, dkDialog,
  dkDraft, dkCommit, showNum, unitLabel, uniqueName,
} from "./dlgkit.js";
import { spvFramePanel as dsSpvFrame, spvShellPanel as dsSpvShell, spvSplit as dsSpvSplit } from "./secpreview.js";   // live member / property preview (js/secpreview.js)
import { ssShapeEditor as dsSsEditor, ssShapeLabel as dsSsLabel } from "./secshape_ui.js";   // v1.21 Shape selector + parametric forms
import { openSteelDb as dsOpenSteelDb } from "./steeldb.js";   // v1.21 Import from Database…

const G = 9.80665;
const eff = (o, k, d = 1) => (o && o[k] != null && isFinite(o[k])) ? +o[k] : d;
const cap = s => String(s || "").replace(/(^|[_\s])(\w)/g, (m, a, b) => (a ? " " : "") + b.toUpperCase()).trim();
const nameCheck = (taken, self) => v => (v !== self && taken().has(v)) ? `“${v}” is already used — pick another name.` : (/["\\]/.test(v) ? "Names cannot contain quotes or backslashes." : "");

/** Close the list (committing the draft) and run a legacy editor. */
function thenLegacy(dlg, sky, D, fn) {
  dkCommit(sky, D);
  dlg.close();
  setTimeout(fn, 0);
}

/* ================================================================
   Materials
   ================================================================ */
const MAT_TYPE_LABEL = { steel: "Steel", concrete: "Concrete", rebar: "Rebar", tendon: "Tendon", masonry: "Masonry",
  aluminum: "Aluminum", coldformed: "Cold Formed", other: "Other" };

export function openMaterials(sky, { select } = {}) {
  const D = dkDraft(sky);
  D.materials = D.materials || {};
  let newType = "concrete";
  const usedBy = n => Object.values(D.sections || {}).filter(s => s.material === n).length + Object.values(D.shell_sections || {}).filter(s => s.material === n).length;
  const typeSel = el("select", { id: "dsMatNewType", "aria-label": "Type of the new material" },
    dsME.MATERIAL_TYPES.map(t => el("option", { value: t, selected: t === newType }, [MAT_TYPE_LABEL[t] || t])));
  typeSel.addEventListener("change", () => { newType = typeSel.value; });
  const libSel = el("select", { id: "dsMatLib", "aria-label": "Library material" },
    dsME.DEFAULT_LIBRARY.map(e => el("option", { value: e.name }, [`${e.name} · ${MAT_TYPE_LABEL[e.material_type] || e.material_type}`])));
  const libBtn = el("button", { type: "button", class: "btn btn-small dk-side-btn", id: "dsMatAddLib" }, ["Add from Library"]);
  const err = el("p", { class: "dk-err dk-side-err", role: "alert" });
  const list = dkList({
    id: "dsMatList",
    items: () => Object.entries(D.materials).map(([k, m]) => ({ key: k, m })),
    columns: [
      { label: "Material", get: it => it.key },
      { label: "Type", get: it => MAT_TYPE_LABEL[it.m.material_type] || it.m.material_type || "—" },
      { label: `E (${dsU.label("modulus")})`, get: it => isNum(it.m.E) ? dsU.fmt("modulus", it.m.E, 0) : "—" },
      { label: "Used by", get: it => { const n = usedBy(it.key); return n ? `${n} section${n > 1 ? "s" : ""}` : "—"; } },
    ],
    selected: select || null,
    onOpen: k => modify(k),
    empty: "No materials yet — add one or pick one from the library.",
    actions: [
      { sep: "Add" },
      { label: "Add New Material…", id: "dsMatAdd", fn: () => edit(null, { type: newType }) },
      { label: "Add Copy of Material…", id: "dsMatCopy", needsSel: true, fn: k => edit(null, { copyOf: k }) },
      { sep: "Selected" },
      { label: "Modify/Show Material…", id: "dsMatModify", needsSel: true, fn: k => modify(k) },
      { label: "Delete Material", id: "dsMatDelete", needsSel: true, fn: k => del(k) },
    ],
    extraSide: el("div", { class: "dk-list-side" }, [el("div", { class: "dk-side-title", text: "New material type" }), typeSel,
      el("div", { class: "dk-side-title", text: "Library" }), libSel, libBtn, err]),
  });
  const modify = k => k && edit(k);
  const del = k => {
    if (!k) return;
    const n = usedBy(k);
    if (n) { err.textContent = `${k} is used by ${n} section${n > 1 ? "s" : ""} — reassign them first.`; return; }
    err.textContent = "";
    delete D.materials[k];
    list.render(); sync();
  };
  libBtn.addEventListener("click", () => {
    const entry = dsME.DEFAULT_LIBRARY.find(e => e.name === libSel.value);
    if (!entry) return;
    const nm = dsME.addLibraryMaterial(D, entry);
    list.select(nm); sync();
  });
  const edit = (name, opts = {}) => editMaterial(sky, D, name, { ...opts, done: nm => { list.select(nm); sync(); } });
  const sync = () => dlg && dlg.setNote(`${Object.keys(D.materials).length} material${Object.keys(D.materials).length === 1 ? "" : "s"}`);
  const more = el("button", { type: "button", class: "btn btn-small dk-legacy", id: "dsMatMore", title: "Nonlinear stress–strain laws and the full material sheet (applies these edits first)" }, ["Stress–Strain…"]);
  const dlg = dkDialog({
    id: "dsMaterials", title: "Define Materials", iconId: "exleaf-materials", size: "lg",
    intro: "Materials give sections their stiffness, weight and strength. Select one and click <b>Modify/Show</b> (or double-click) to edit it.",
    body: list, extra: [more],
    onOk: () => { dkCommit(sky, D); },
  });
  more.addEventListener("click", () => thenLegacy(dlg, sky, D, () => sky.openSectionMgr && sky.openSectionMgr()));
  sync();
  return dlg;
}

/** "Material Property Data" for one material of draft D (name null = new). */
export function editMaterial(sky, D, name, { copyOf, type = "concrete", done } = {}) {
  const src = name ? D.materials[name] : (copyOf ? D.materials[copyOf] : null);
  const isNew = !name;
  const nm0 = name || uniqueName(D.materials, copyOf ? copyOf + "-copy" : (type === "steel" ? "STEEL" : type === "concrete" ? "CONC" : type.toUpperCase()));
  const mat = src ? clone(src) : dsME.blankMaterial(nm0, type);
  mat.name = nm0;
  const draftName = { v: nm0 };
  const F = dkForm();
  const body = el("div", { class: "ds-mat" });
  const build = () => {
    F.fields.length = 0;
    body.textContent = "";
    const t = mat.material_type || "concrete";
    const uniax = mat.symmetry === "uniaxial";
    const massAuto = () => mat.mass_density == null;
    const gTxt = () => uniax ? "n/a" : (isNum(mat.E) && isNum(mat.nu) ? mat.E / (2 * (1 + mat.nu)) : null);
    body.appendChild(dkGroup("General Data", [
      F.text(draftName, "v", { label: "Material Name", id: "dsMatName", validate: nameCheck(() => new Set(Object.keys(D.materials)), name) }),
      F.select(mat, "material_type", dsME.MATERIAL_TYPES.map(x => [x, MAT_TYPE_LABEL[x] || x]), {
        label: "Material Type", id: "dsMatType",
        hint: "Changing the type re-stamps E, ν, weight and strengths with typical values.",
        write: v => { dsME.applyMaterialType(mat, v); }, onChange: () => build(),
      }),
    ]));
    body.appendChild(dkGroup("Weight and Mass", [
      F.num(mat, "unit_weight", { label: "Weight per Unit Volume", kind: "unit_weight", gt: 0, id: "dsMatUw", onChange: () => F.refresh() }),
      F.check(null, "massAuto", { label: "Mass from weight (W / g)", id: "dsMatMassAuto",
        read: massAuto, write: v => { mat.mass_density = v ? null : +((mat.unit_weight || 0) / G).toFixed(4); }, onChange: () => build() }),
      massAuto()
        ? F.readout("Mass per Unit Volume", () => isNum(mat.unit_weight) ? mat.unit_weight / G : null, { kind: "mass_density", hint: "auto = weight / g" })
        : F.num(mat, "mass_density", { label: "Mass per Unit Volume", kind: "mass_density", gt: 0, id: "dsMatMass" }),
    ]));
    body.appendChild(dkGroup((uniax ? "Uniaxial" : "Isotropic") + " Property Data", [
      F.num(mat, "E", { label: "Modulus of Elasticity, E", kind: "modulus", gt: 0, id: "dsMatE", onChange: () => F.refresh() }),
      uniax ? null : F.num(mat, "nu", { label: "Poisson's Ratio, ν", min: 0, lt: 0.5, id: "dsMatNu", onChange: () => F.refresh() }),
      uniax ? null : F.readout("Shear Modulus, G", gTxt, { kind: "modulus", hint: "E / 2(1 + ν)" }),
    ]));
    let strength = [];
    if (t === "concrete" || t === "masonry") strength = [
      F.num(mat, "fc", { label: "Specified Concrete Strength, f'c", kind: "stress", gt: 0, allowNull: true, nullText: "auto (from E)", id: "dsMatFc" }),
    ];
    else if (t === "steel" || t === "coldformed" || t === "rebar" || t === "tendon") strength = [
      F.num(mat, "fy", { label: "Minimum Yield Stress, Fy", kind: "stress", gt: 0, allowNull: true, id: "dsMatFy", onChange: () => F.refresh() }),
      F.num(mat, "fu", { label: "Minimum Tensile Stress, Fu", kind: "stress", gt: 0, allowNull: true, id: "dsMatFu" }),
      t === "tendon" ? null : F.num(mat, "Ry", { label: "Expected Yield Ratio, Ry", gt: 0, max: 2, def: 1.1, id: "dsMatRy", onChange: () => F.refresh() }),
      t === "tendon" ? null : F.readout("Expected Yield Stress, Fye", () => isNum(mat.fy) && isNum(mat.Ry) ? mat.fy * mat.Ry : null, { kind: "stress", hint: "Ry · Fy" }),
    ];
    if (strength.length) body.appendChild(dkGroup("Design Strength", strength));
    const advFields = [
      ["symmetry", "isotropic"], ["alpha", null], ["damping", 0], ["lightweight", false], ["lam", 1],
    ];
    const adv = dkAdvanced([
      F.select(mat, "symmetry", [["isotropic", "Isotropic"], ["uniaxial", "Uniaxial (rebar / tendon)"]], { label: "Directional Symmetry", id: "dsMatSym", onChange: () => build() }),
      F.num(mat, "alpha", { label: "Thermal Expansion, α", kind: "thermal_coeff", gt: 0, allowNull: true, def: null,
        nullText: `from model (${dsU.toDisplay("thermal_coeff", D.thermal_alpha ?? 1.2e-5).toExponential(2)})`, id: "dsMatAlpha" }),
      F.num(mat, "damping", { label: "Modal Damping Ratio", min: 0, lt: 1, def: 0, read: () => mat.damping ?? 0, id: "dsMatDamp",
        hint: "0 = use the load case damping only" }),
      (t === "concrete" || t === "masonry") ? F.check(mat, "lightweight", { label: "Lightweight concrete", id: "dsMatLw" }) : null,
      (t === "concrete" || t === "masonry") ? F.num(mat, "lam", { label: "Lightweight factor, λ", gt: 0, max: 1, def: 1, read: () => mat.lam ?? 1, id: "dsMatLam" }) : null,
      F.text(mat, "color", { label: "Display Color", required: false, id: "dsMatColor", read: () => mat.color || "",
        validate: v => v && !/^#[0-9a-f]{6}$/i.test(v) ? "Use a #rrggbb colour, e.g. #8a8f98." : "" }),
      F.text(mat, "notes", { label: "Notes", required: false, raw: true, id: "dsMatNotes", read: () => mat.notes || "" }),
      mat.stress_strain ? dkNote(`Nonlinear stress–strain law: <b>${esc(mat.stress_strain.model || "custom")}</b> — edit it with <b>Stress–Strain…</b> in Define Materials.`) : null,
    ], { countFn: () => advFields.filter(([k, d]) => (mat[k] ?? d) !== d).length });
    body.appendChild(adv);
    F.onTouch = () => adv.refreshBadge();
  };
  build();
  return dkDialog({
    id: "dsMaterialData", title: "Material Property Data", iconId: "exleaf-materials", size: "md", form: F,
    intro: isNew ? (copyOf ? `New material copied from <b>${esc(copyOf)}</b>.` : "New material — values are typical for the chosen type.") : null,
    body,
    onOk: () => {
      if (!F.touched && !isNew) { done && done(name); return; }
      const nn = draftName.v.trim();
      if (name && nn !== name) dsME.renameMaterial(D, name, nn);
      D.materials[nn] = { ...mat, name: nn };
      done && done(nn);
    },
  });
}

/* ================================================================
   Frame sections
   ================================================================ */
const secShape = s => s.kind === "nonprismatic" ? "Nonprismatic" : dsSsLabel(s) ? dsSsLabel(s) : s.shape === "W" ? "Steel W-shape" : s.shape === "designer" ? "Section Designer"
  : (s.b > 0 && s.h > 0) ? "Rectangular" : "General";

export function openFrameSections(sky, { select } = {}) {
  const D = dkDraft(sky);
  D.sections = D.sections || {};
  const usedBy = n => (D.members || []).filter(m => m.section === n).length;
  const err = el("p", { class: "dk-err dk-side-err", role: "alert" });
  const list = dkList({
    id: "dsSecList",
    items: () => Object.entries(D.sections).map(([k, s]) => ({ key: k, s })),
    columns: [
      { label: "Section", get: it => it.key },
      { label: "Shape", get: it => secShape(it.s) },
      { label: "Material", get: it => it.s.material || "—" },
      { label: `A (${dsU.label("area")})`, get: it => isNum(it.s.A) ? dsU.sci("area", it.s.A) : (it.s.b > 0 && it.s.h > 0 ? dsU.sci("area", it.s.b * it.s.h) : "—") },
      { label: "Members", get: it => usedBy(it.key) || "—" },
    ],
    selected: select || null,
    onOpen: k => modify(k),
    empty: "No frame sections yet.",
    actions: [
      { sep: "Add" },
      { label: "Add New Section…", id: "dsSecAdd", fn: () => edit(null) },
      { label: "Add Copy of Section…", id: "dsSecCopy", needsSel: true, fn: k => edit(null, { copyOf: k }), disabled: k => k && D.sections[k] && D.sections[k].kind === "nonprismatic" },
      { label: "Import from Library…", id: "dsSecLib", fn: () => openSectionLibrary(sky, D, { done: nm => { list.select(nm); sync(); } }) },
      { label: "Import from Database…", id: "dsSecDb", fn: () => dsOpenSteelDb(sky, D, { done: nm => { list.select(nm); sync(); } }) },   // v1.21 (js/steeldb.js)
      { sep: "Selected" },
      { label: "Modify/Show Section…", id: "dsSecModify", needsSel: true, fn: k => modify(k) },
      { label: "Delete Section", id: "dsSecDelete", needsSel: true, fn: k => del(k) },
    ],
    extraSide: err,
  });
  const modify = k => {
    if (!k) return;
    if (D.sections[k] && D.sections[k].kind === "nonprismatic") { thenLegacy(dlg, sky, D, () => sky.openNonprismatic && sky.openNonprismatic(k)); return; }
    edit(k);
  };
  const del = k => {
    const n = usedBy(k);
    if (n || dsME.sectionInUse(D, k)) { err.textContent = `${k} is used by ${n || "a nonprismatic section"}${n ? " member" + (n > 1 ? "s" : "") : ""} — reassign first.`; return; }
    err.textContent = "";
    delete D.sections[k];
    if (D.designer_sections && D.designer_sections[k]) delete D.designer_sections[k];
    list.render(); sync();
  };
  const edit = (name, opts = {}) => editFrameSection(sky, D, name, { ...opts, done: nm => { list.select(nm); sync(); } });
  const sync = () => dlg && dlg.setNote(`${Object.keys(D.sections).length} frame sections`);
  const des = el("button", { type: "button", class: "btn btn-small dk-legacy", id: "dsSecDesigner", title: "Draw a polygon + rebar section (applies these edits first)" }, ["Section Designer…"]);
  const np = el("button", { type: "button", class: "btn btn-small dk-legacy", id: "dsSecNp", title: "Tapered / haunched member section (applies these edits first)" }, ["Nonprismatic…"]);
  const lpv = dsSpvFrame({ model: D, name: () => list.selected(), section: () => D.sections[list.selected()] || null });   // preview of the selected section
  const dlg = dkDialog({
    id: "dsFrameSections", title: "Frame Properties", iconId: "exleaf-frames", size: "lg",
    intro: "Frame sections assigned to columns, beams and braces. Rectangular sections compute A, I and J from b × h.",
    body: dsSpvSplit(list, lpv), extra: [des, np],
    onOk: () => { dkCommit(sky, D); },
  });
  des.addEventListener("click", () => thenLegacy(dlg, sky, D, () => sky.openSectionDesigner && sky.openSectionDesigner()));
  np.addEventListener("click", () => thenLegacy(dlg, sky, D, () => sky.openNonprismatic && sky.openNonprismatic()));
  sync();
  return dlg;
}

export function editFrameSection(sky, D, name, { copyOf, done } = {}) {
  const src = name ? D.sections[name] : (copyOf ? D.sections[copyOf] : null);
  const isNew = !name;
  const nm0 = name || uniqueName(D.sections, copyOf ? copyOf + "-copy" : "FSEC1");
  const s = src ? clone(src) : dsME.rectSectionProps({ name: nm0, material: dsME.defaultMaterial(D), b: 0.3, h: 0.6 });
  s.name = nm0;
  const draftName = { v: nm0 };
  const F = dkForm();
  const mats = () => Object.keys(D.materials || {}).map(n => [n, n]);
  // v1.21: Shape selector + dimensions + properties for every shape (js/secshape_ui.js)
  const ssEd = dsSsEditor(F, s, { model: D, onChange: () => pv && pv.update() });
  const advMods = dsFRAME_MODS.map(([k, lbl, isMass]) => F.num(s, k, { label: lbl, read: () => eff(s, k), def: 1, id: "dsSecMod_" + k, ...(isMass ? { min: 0 } : { gt: 0 }) }));
  const shear = [
    F.check(s, "shear_deformation", { label: "Include shear deformation (auto-fill unset shear areas)", id: "dsSecShear" }),
    F.num(s, "As2", { label: "Shear Area in 2 direction", kind: "area", gt: 0, allowNull: true, nullText: "auto", def: null, id: "dsSecAs2" }),
    F.num(s, "As3", { label: "Shear Area in 3 direction", kind: "area", gt: 0, allowNull: true, nullText: "auto", def: null, id: "dsSecAs3" }),
  ];
  const adv = dkAdvanced([dkGroup("Property / Stiffness Modifiers", advMods, { cols: 1, note: "Multiply the analysis properties of every member using this section (cracked sections: e.g. I33 × 0.35 for beams)." }),
    dkGroup("Shear Deformation", shear)], {
    summary: "Advanced — modifiers and shear areas",
    countFn: () => dsFRAME_MODS.filter(([k]) => eff(s, k) !== 1).length + (s.shear_deformation ? 1 : 0) + (s.As2 != null ? 1 : 0) + (s.As3 != null ? 1 : 0),
  });
  F.onTouch = () => adv.refreshBadge();
  const body = [
    dkGroup("General", [
      F.text(draftName, "v", { label: "Section Name", id: "dsSecName", validate: nameCheck(() => new Set(Object.keys(D.sections)), name) }),
      F.select(s, "material", mats, { label: "Material", id: "dsSecMat" }),
      ssEd.select,
    ]),
    ssEd.node, adv,
  ];
  const pv = dsSpvFrame({ model: D, name: nm0, title: () => draftName.v, section: () => ssEd.current(), recompute: x => ssEd.key() === "rect" ? dsME.rectSectionProps(x) : x,   // live preview (read-only)
    live: [["dsSecH", "h", "dim", "Depth h"], ["dsSecB", "b", "dim", "Width b"], ["dsSecA", "A", "area", "A"], ["dsSecI33", "I33", "inertia", "I33"], ["dsSecI22", "I22", "inertia", "I22"], ["dsSecJ", "J", "inertia", "J"]].map(([id, key, kind, label]) => ({ id, key, kind, label })) });
  return dkDialog({
    id: "dsFrameSectionData", title: "Frame Section Property Data", iconId: "exleaf-frames", size: "md", form: F, body: dsSpvSplit(body, pv),
    intro: isNew ? (copyOf ? `New section copied from <b>${esc(copyOf)}</b>.` : "New rectangular section.") : null,
    onOk: () => {
      if (!F.touched && !isNew) { done && done(name); return; }
      const nn = draftName.v.trim();
      if (name && nn !== name) {
        dsME.renameFrameSection(D, name, nn);
        if (D.designer_sections && D.designer_sections[name]) { D.designer_sections[nn] = { ...D.designer_sections[name], name: nn }; delete D.designer_sections[name]; }
      }
      D.sections[nn] = { ...s, name: nn };
      done && done(nn);
    },
  });
}

/** Import steel shapes from the section library into draft D. */
export function openSectionLibrary(sky, D, { done } = {}) {
  const F = dkForm();
  const st = { q: "", mat: Object.keys(D.materials || {}).find(n => (D.materials[n].material_type || "") === "steel") || dsME.defaultMaterial(D) };
  const search = el("input", { type: "search", class: "dk-search", id: "dsLibSearch", placeholder: "Search shapes, e.g. W14", "aria-label": "Search library" });
  const box = el("div", { class: "dk-lib-list table-scroll" });
  const picked = new Set();
  let lib = sky.store.sectionLib || [];
  const draw = () => {
    box.textContent = "";
    const q = st.q.trim().toLowerCase();
    const rows = lib.filter(e => !q || e.name.toLowerCase().includes(q)).slice(0, 300);
    if (!rows.length) { box.appendChild(el("p", { class: "muted dk-empty", text: lib.length ? "No shapes match." : "Loading library…" })); return; }
    const t = el("table", { class: "data-table dk-list-tbl" });
    t.appendChild(el("thead", {}, [el("tr", {}, ["", "Shape", `A (${dsU.label("area")})`, `I33 (${dsU.label("inertia")})`, `I22 (${dsU.label("inertia")})`].map(h => el("th", { class: "txt" }, [h])))]));
    const tb = el("tbody");
    for (const e of rows) {
      const exists = !!D.sections[e.name];
      const cb = el("input", { type: "checkbox", disabled: exists, "aria-label": "Import " + e.name });
      cb.checked = picked.has(e.name) || exists;
      cb.addEventListener("change", () => { if (cb.checked) picked.add(e.name); else picked.delete(e.name); });
      const tr = el("tr", { class: exists ? "is-muted" : "" }, [el("td", {}, [cb]), el("td", { class: "txt", text: e.name + (exists ? " (defined)" : "") }),
        el("td", { class: "txt", text: dsU.sci("area", e.A) }), el("td", { class: "txt", text: dsU.sci("inertia", e.I33) }), el("td", { class: "txt", text: dsU.sci("inertia", e.I22) })]);
      tr.addEventListener("dblclick", () => { if (!exists) { cb.checked = !cb.checked; cb.dispatchEvent(new Event("change")); } });
      tb.appendChild(tr);
    }
    t.appendChild(tb); box.appendChild(t);
  };
  search.addEventListener("input", () => { st.q = search.value; draw(); });
  const body = [
    dkGroup("Material", [F.select(st, "mat", () => Object.keys(D.materials || {}).map(n => [n, n]), { label: "Material for imported shapes", id: "dsLibMat" })]),
    dkGroup("Steel Shape Library", [search, box]),
  ];
  const dlg = dkDialog({
    id: "dsSectionLib", title: "Import Frame Sections", iconId: "exleaf-frames", size: "lg", form: F, body,
    intro: "Tick the shapes to import (double-click a row to toggle).",
    onOk: () => {
      let last = null;
      for (const n of picked) { const e = lib.find(x => x.name === n); if (e && dsME.addLibraryFrameSection(D, e, st.mat)) last = n; }
      done && done(last);
    },
  });
  draw();
  if (!lib.length && sky.fetchSectionLibrary) sky.fetchSectionLibrary().then(l => { lib = l || []; draw(); }).catch(() => { box.textContent = "Library unavailable."; });
  return dlg;
}

/* ================================================================
   Shell (slab / wall) sections
   ================================================================ */
export function openShellSections(sky, { select } = {}) {
  const D = dkDraft(sky);
  D.shell_sections = D.shell_sections || {};
  const usedBy = n => (D.shells || []).filter(s => s.section === n);
  const err = el("p", { class: "dk-err dk-side-err", role: "alert" });
  const list = dkList({
    id: "dsShellList",
    items: () => Object.entries(D.shell_sections).map(([k, s]) => ({ key: k, s })),
    columns: [
      { label: "Section", get: it => it.key },
      { label: "Used as", get: it => { const u = usedBy(it.key); const w = u.filter(x => x.kind === "wall").length, sl = u.length - w; return u.length ? [w ? `${w} wall` : "", sl ? `${sl} slab` : ""].filter(Boolean).join(" · ") : "—"; } },
      { label: `Thickness (${dsU.label("dim")})`, get: it => isNum(it.s.thickness) ? dsU.fmt("dim", it.s.thickness, 3) : "—" },
      { label: "Material", get: it => it.s.material || "—" },
      { label: "Layered", get: it => it.s.layered ? `${(it.s.layered.layers || []).length} layers` : "—" },
    ],
    selected: select || null,
    onOpen: k => edit(k),
    empty: "No slab / wall sections yet.",
    actions: [
      { sep: "Add" },
      { label: "Add New Section…", id: "dsShellAdd", fn: () => edit(null) },
      { label: "Add Copy of Section…", id: "dsShellCopy", needsSel: true, fn: k => edit(null, { copyOf: k }) },
      { sep: "Selected" },
      { label: "Modify/Show Section…", id: "dsShellModify", needsSel: true, fn: k => edit(k) },
      { label: "Delete Section", id: "dsShellDelete", needsSel: true, fn: k => del(k) },
    ],
    extraSide: err,
  });
  const del = k => {
    const n = usedBy(k).length;
    if (n) { err.textContent = `${k} is used by ${n} shell${n > 1 ? "s" : ""} — reassign first.`; return; }
    err.textContent = "";
    delete D.shell_sections[k];
    list.render(); sync();
  };
  const edit = (name, opts = {}) => editShellSection(sky, D, name, { ...opts, done: nm => { list.select(nm); sync(); } });
  const sync = () => dlg && dlg.setNote(`${Object.keys(D.shell_sections).length} slab / wall sections`);
  const more = el("button", { type: "button", class: "btn btn-small dk-legacy", id: "dsShellMore", title: "Layered shell definitions (applies these edits first)" }, ["Layered Sections…"]);
  const lpv = dsSpvShell({ model: D, name: () => list.selected(), section: () => D.shell_sections[list.selected()] || null });   // preview of the selected section
  const dlg = dkDialog({
    id: "dsShellSections", title: "Slab / Wall Properties", iconId: "exleaf-shells", size: "lg",
    intro: "Area sections for floors and walls: thickness, material and stiffness modifiers.",
    body: dsSpvSplit(list, lpv), extra: [more],
    onOk: () => { dkCommit(sky, D); },
  });
  more.addEventListener("click", () => thenLegacy(dlg, sky, D, () => sky.openSectionMgr && sky.openSectionMgr()));
  sync();
  return dlg;
}

export function editShellSection(sky, D, name, { copyOf, done } = {}) {
  const src = name ? D.shell_sections[name] : (copyOf ? D.shell_sections[copyOf] : null);
  const isNew = !name;
  const nm0 = name || uniqueName(D.shell_sections, copyOf ? copyOf + "-copy" : "SSEC1");
  const s = src ? clone(src) : { name: nm0, material: dsME.defaultMaterial(D), thickness: 0.2 };
  s.name = nm0;
  const draftName = { v: nm0 };
  const F = dkForm();
  const groups = {};
  for (const [k, lbl, isMass, grp] of dsSHELL_MODS) (groups[grp] = groups[grp] || []).push(F.num(s, k, { label: lbl, read: () => eff(s, k), def: 1, id: "dsShMod_" + k, ...(isMass ? { min: 0 } : { gt: 0 }) }));
  const adv = dkAdvanced([
    F.num(s, "mod", { label: "Overall stiffness modifier (scales E)", read: () => eff(s, "mod"), def: 1, gt: 0, id: "dsShMod" }),
    ...Object.entries(groups).map(([g, rows]) => dkGroup(g + " Modifiers", rows, { cols: rows.length > 2 ? 1 : 1 })),
  ], { summary: "Advanced — stiffness modifiers", countFn: () => dsSHELL_MODS.filter(([k]) => eff(s, k) !== 1).length + (eff(s, "mod") !== 1 ? 1 : 0) });
  F.onTouch = () => adv.refreshBadge();
  const body = [
    dkGroup("General", [
      F.text(draftName, "v", { label: "Section Name", id: "dsShName", validate: nameCheck(() => new Set(Object.keys(D.shell_sections)), name) }),
      F.select(s, "material", () => Object.keys(D.materials || {}).map(n => [n, n]), { label: "Material", id: "dsShMat" }),
    ]),
    dkGroup("Property Data", [
      F.num(s, "thickness", { label: "Thickness", kind: "dim", gt: 0, id: "dsShT" }),
      s.layered ? dkNote(`Layered section: <b>${(s.layered.layers || []).length}</b> layers (total ${dsU.fmt("dim", (s.layered.layers || []).reduce((a, l) => a + (+l.t || 0), 0), 3)} ${dsU.label("dim")}) — edit the layers with <b>Layered Sections…</b>.`) : null,
    ]),
    adv,
  ];
  const pv = dsSpvShell({ model: D, name: nm0, title: () => draftName.v, section: () => s, thicknessId: "dsShT" });   // live preview (read-only)
  return dkDialog({
    id: "dsShellSectionData", title: "Slab / Wall Property Data", iconId: "exleaf-shells", size: "md", form: F, body: dsSpvSplit(body, pv),
    intro: isNew ? (copyOf ? `New section copied from <b>${esc(copyOf)}</b>.` : "New shell section.") : null,
    onOk: () => {
      if (!F.touched && !isNew) { done && done(name); return; }
      const nn = draftName.v.trim();
      if (name && nn !== name) dsME.renameShellSection(D, name, nn);
      D.shell_sections[nn] = { ...s, name: nn };
      done && done(nn);
    },
  });
}

/* ================================================================
   Load patterns (ETABS: Name · Type · Self Weight Multiplier · Auto Lateral)
   ================================================================ */
const PAT_TYPES = [["dead", "Dead"], ["live", "Live"], ["quake", "Seismic"], ["wind", "Wind"], ["other", "Other"]];
const PAT_LABEL = Object.fromEntries([...PAT_TYPES, ["notional", "Notional"], ["prestress", "Prestress"]]);
const patLoadCount = p => ["member_loads", "area_loads", "story_forces", "nodal_loads", "thermal_loads", "member_udls", "ground_displacements"]
  .reduce((a, k) => a + ((p && p[k]) || []).length, 0);

export function openLoadPatterns(sky, { select } = {}) {
  const D = dkDraft(sky);
  D.patterns = D.patterns || {};
  const err = el("p", { class: "dk-err dk-side-err", role: "alert" });
  const list = dkList({
    id: "dsPatList",
    items: () => Object.entries(D.patterns).map(([k, p]) => ({ key: k, p })),
    columns: [
      { label: "Load Pattern", get: it => it.key },
      { label: "Type", get: it => PAT_LABEL[it.p.kind] || cap(it.p.kind) || "Other" },
      { label: "Self Weight Multiplier", get: it => String(+(it.p.self_weight_factor || 0).toPrecision(6)) },
      { label: "Auto Lateral", get: it => it.p.auto_lateral ? (it.p.auto_lateral.code || it.p.auto_lateral.type || "Yes") : (it.p.elf ? "ELF" : it.p.wind ? "Wind" : "—") },
      { label: "Loads", get: it => patLoadCount(it.p) || "—" },
    ],
    selected: select || null,
    onOpen: k => edit(k),
    empty: "No load patterns yet — add DEAD (self weight 1) and LIVE to start.",
    actions: [
      { sep: "Add" },
      { label: "Add New Load Pattern…", id: "dsPatAdd", fn: () => edit(null) },
      { label: "Add Copy…", id: "dsPatCopy", needsSel: true, fn: k => edit(null, { copyOf: k }) },
      { sep: "Selected" },
      { label: "Modify/Show Load Pattern…", id: "dsPatModify", needsSel: true, fn: k => edit(k) },
      { label: "Delete Load Pattern", id: "dsPatDelete", needsSel: true, fn: k => del(k) },
      { label: "Modify Lateral Load…", id: "dsPatLateral", needsSel: true, title: "Auto lateral (ELF / wind) parameters — applies these edits first",
        disabled: k => !k || !["quake", "wind"].includes((D.patterns[k] || {}).kind), fn: () => thenLegacy(dlg, sky, D, () => sky.openAutoLateral && sky.openAutoLateral()) },
    ],
    extraSide: err,
  });
  const del = k => {
    const refs = dsME.patternRefs(D, k);
    if (refs.length) { err.textContent = `${k} is used by ${refs.slice(0, 3).join(", ")}${refs.length > 3 ? "…" : ""} — remove it there first.`; return; }
    const n = patLoadCount(D.patterns[k]);
    err.textContent = n ? `Deleted ${k} with its ${n} assigned load${n > 1 ? "s" : ""} (Cancel to undo).` : "";
    dsME.deletePattern(D, k);
    if (D.mass_source && D.mass_source[k] !== undefined) delete D.mass_source[k];
    list.render(); sync();
  };
  const edit = (name, opts = {}) => editLoadPattern(sky, D, name, { ...opts, done: nm => { list.select(nm); sync(); } });
  const sync = () => dlg && dlg.setNote(`${Object.keys(D.patterns).length} load patterns`);
  const dlg = dkDialog({
    id: "dsLoadPatterns", title: "Define Load Patterns", iconId: "exleaf-patterns", size: "lg",
    intro: "A load pattern is a named set of loads. Assign member / area / joint loads to it from the Assign menu, then combine patterns in load cases.",
    body: list,
    onOk: () => { dkCommit(sky, D); },
  });
  sync();
  return dlg;
}

export function editLoadPattern(sky, D, name, { copyOf, done } = {}) {
  const src = name ? D.patterns[name] : (copyOf ? D.patterns[copyOf] : null);
  const isNew = !name;
  const nm0 = name || uniqueName(D.patterns, copyOf ? copyOf + "-copy" : "PAT1");
  const p = src ? clone(src) : { name: nm0, kind: "other", member_loads: [], area_loads: [], story_forces: [] };
  p.name = nm0;
  const draftName = { v: nm0 };
  const F = dkForm();
  const special = p.kind && !PAT_TYPES.some(([k]) => k === p.kind);
  const lateral = () => p.kind === "quake" || p.kind === "wind" || !!p.wind || !!p.elf;
  const body = el("div");
  const build = () => {
    F.fields.length = 0; body.textContent = "";
    body.appendChild(dkGroup("Load Pattern", [
      F.text(draftName, "v", { label: "Load Pattern Name", id: "dsPatName", validate: nameCheck(() => new Set(Object.keys(D.patterns)), name) }),
      special ? F.readout("Type", () => PAT_LABEL[p.kind] || cap(p.kind), { hint: "generated pattern — type is fixed" })
        : F.select(p, "kind", PAT_TYPES, { label: "Type", id: "dsPatType", onChange: () => build() }),
      F.num(p, "self_weight_factor", { label: "Self Weight Multiplier", min: 0, def: 0, read: () => p.self_weight_factor ?? 0, id: "dsPatSw",
        hint: "1 on the DEAD pattern adds the structure's own weight" }),
    ]));
    if (lateral()) {
      const adv = dkAdvanced([
        F.check(p, "accidental_torsion", { label: "Accidental torsion (± eccentricity)", id: "dsPatAt", onChange: () => build() }),
        F.num(p, "ecc", { label: "Eccentricity ratio", min: -0.5, max: 0.5, def: 0.05, read: () => p.ecc ?? 0.05, id: "dsPatEcc",
          hint: "fraction of the plan dimension perpendicular to the load (ASCE 7 §12.8.4.2)" }),
      ], { summary: "Advanced — accidental torsion", countFn: () => (p.accidental_torsion ? 1 : 0) + ((p.ecc ?? 0.05) !== 0.05 ? 1 : 0) });
      body.appendChild(adv);
      F.onTouch = () => adv.refreshBadge();
    }
    const n = patLoadCount(p);
    body.appendChild(dkNote(n ? `${n} load${n > 1 ? "s are" : " is"} assigned to this pattern (Assign menu).` : "No loads assigned yet — select objects and use Assign › Frame / Shell / Joint Loads."));
  };
  build();
  return dkDialog({
    id: "dsLoadPatternData", title: "Load Pattern Data", iconId: "exleaf-patterns", size: "md", form: F, body,
    onOk: () => {
      if (!F.touched && !isNew) { done && done(name); return; }
      const nn = draftName.v.trim();
      if (name && nn !== name) {
        dsME.renamePattern(D, name, nn);
        if (D.mass_source && D.mass_source[name] !== undefined) { D.mass_source[nn] = D.mass_source[name]; delete D.mass_source[name]; }
      }
      D.patterns[nn] = { ...p, name: nn };
      done && done(nn);
    },
  });
}

/* ================================================================
   Functions — response spectrum + time history
   ================================================================ */
function spark(points, { xLabel = "", yLabel = "" } = {}) {
  const W = 420, H = 150, P = 26;
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  if (!points.length) return el("div", { class: "muted dk-empty", text: "No points" });
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || 1, y0 = Math.min(0, ...ys), y1 = Math.max(...ys) || 1;
  const sx = x => P + (x - x0) / ((x1 - x0) || 1) * (W - 2 * P), sy = y => H - P + 6 - (y - y0) / ((y1 - y0) || 1) * (H - 2 * P);
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join("");
  const box = el("div", { class: "dk-chart" });
  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Function plot">
    <line x1="${P}" y1="${H - P + 6}" x2="${W - P}" y2="${H - P + 6}" stroke="var(--border)"/><line x1="${P}" y1="8" x2="${P}" y2="${H - P + 6}" stroke="var(--border)"/>
    ${y0 < 0 ? `<line x1="${P}" y1="${sy(0)}" x2="${W - P}" y2="${sy(0)}" stroke="var(--border-soft)" stroke-dasharray="3 3"/>` : ""}
    <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="1.6"/>
    <text x="${W - P}" y="${H - 4}" text-anchor="end" font-size="10" fill="var(--text-3)">${esc(xLabel)} ${+x1.toPrecision(3)}</text>
    <text x="${P + 4}" y="16" font-size="10" fill="var(--text-3)">${esc(yLabel)} ${+y1.toPrecision(3)}</text></svg>`;
  return box;
}

export function openFunctions(sky, { tab = "rs", select } = {}) {
  const D = dkDraft(sky);
  const rsRefs = n => Object.values(D.rs_cases || {}).filter(c => c.function === n).map(c => c.name);
  const thRefs = n => Object.values(D.th_cases || {}).filter(c => c.function === n).map(c => c.name);
  const errRs = el("p", { class: "dk-err dk-side-err", role: "alert" }), errTh = el("p", { class: "dk-err dk-side-err", role: "alert" });
  const rsList = dkList({
    id: "dsRsFnList",
    items: () => Object.entries(D.spectrum_functions || {}).map(([k, f]) => ({ key: k, f })),
    columns: [
      { label: "Function", get: it => it.key }, { label: "Points", get: it => (it.f.points || []).length },
      { label: "Peak Sa (g)", get: it => (it.f.points || []).length ? +Math.max(...it.f.points.map(p => p[1])).toPrecision(4) : "—" },
      { label: "Used by", get: it => rsRefs(it.key).join(", ") || "—" },
    ],
    selected: tab === "rs" ? select : null, onOpen: k => editRs(k), empty: "No response-spectrum functions.",
    actions: [
      { label: "Add New Function…", id: "dsRsFnAdd", fn: () => editRs(null) },
      { label: "Add Copy…", id: "dsRsFnCopy", needsSel: true, fn: k => editRs(null, k) },
      { label: "Modify/Show…", id: "dsRsFnModify", needsSel: true, fn: k => editRs(k) },
      { label: "Delete", id: "dsRsFnDelete", needsSel: true, fn: k => {
        const r = rsRefs(k); if (r.length) { errRs.textContent = `${k} is used by ${r.join(", ")}.`; return; }
        errRs.textContent = ""; delete D.spectrum_functions[k]; rsList.render(); } },
    ],
    extraSide: errRs,
  });
  const thList = dkList({
    id: "dsThFnList",
    items: () => Object.entries(D.th_functions || {}).map(([k, f]) => ({ key: k, f })),
    columns: [
      { label: "Function", get: it => it.key }, { label: "Steps", get: it => (it.f.values || []).length },
      { label: "Δt (s)", get: it => it.f.dt ?? "—" },
      { label: "Duration (s)", get: it => isNum(it.f.dt) ? +(((it.f.values || []).length - 1) * it.f.dt).toPrecision(4) : "—" },
      { label: "Used by", get: it => thRefs(it.key).join(", ") || "—" },
    ],
    selected: tab === "th" ? select : null, onOpen: k => editTh(k), empty: "No time-history functions.",
    actions: [
      { label: "Add New Function…", id: "dsThFnAdd", fn: () => editTh(null) },
      { label: "Add Copy…", id: "dsThFnCopy", needsSel: true, fn: k => editTh(null, k) },
      { label: "Modify/Show…", id: "dsThFnModify", needsSel: true, fn: k => editTh(k) },
      { label: "Delete", id: "dsThFnDelete", needsSel: true, fn: k => {
        const r = thRefs(k); if (r.length) { errTh.textContent = `${k} is used by ${r.join(", ")}.`; return; }
        errTh.textContent = ""; delete D.th_functions[k]; thList.render(); } },
    ],
    extraSide: errTh,
  });
  const editRs = (name, copyOf) => editSpectrumFunction(sky, D, name, { copyOf, done: nm => { rsList.select(nm); } });
  const editTh = (name, copyOf) => editThFunction(sky, D, name, { copyOf, done: nm => { thList.select(nm); } });
  const tabs = dkTabs([
    { id: "rs", label: "Response Spectrum", body: rsList },
    { id: "th", label: "Time History", body: thList },
  ], { active: tab, id: "dsFnTabs" });
  return dkDialog({
    id: "dsFunctions", title: "Define Functions", iconId: "exleaf-functions", size: "lg", tabs,
    intro: "Reusable spectra and ground-motion records. A response-spectrum or time-history load case references a function by name.",
    body: tabs,
    onOk: () => { dkCommit(sky, D); },
  });
}

export function editSpectrumFunction(sky, D, name, { copyOf, done } = {}) {
  D.spectrum_functions = D.spectrum_functions || {};
  const src = name ? D.spectrum_functions[name] : (copyOf ? D.spectrum_functions[copyOf] : null);
  const isNew = !name;
  const nm0 = name || uniqueName(D.spectrum_functions, copyOf ? copyOf + "-copy" : "SPEC1");
  const f = src ? clone(src) : { name: nm0, points: dsME.ubcSpectrum(), damping: 0.05 };
  const draftName = { v: nm0 };
  const rows = (f.points || []).map(([T, Sa]) => ({ T, Sa }));
  const F = dkForm();
  const chart = el("div");
  const redraw = () => { chart.textContent = ""; chart.appendChild(spark(rows.map(r => [r.T, r.Sa]).sort((a, b) => a[0] - b[0]), { xLabel: "T (s)", yLabel: "Sa (g)" })); };
  const tableNode = F.table(rows, [{ key: "T", label: "Period", type: "num", kind: "period" }, { key: "Sa", label: "Sa (g)", type: "num" }], {
    id: "dsRsFnTable", minRows: 2, minMsg: "A spectrum needs at least two points.", addLabel: "+ Add point",
    newRow: r => { const last = r[r.length - 1]; return { T: last ? +(last.T + 0.5).toFixed(3) : 0, Sa: last ? last.Sa : 0.1 }; },
    validateRow: (r, i) => !(r.T >= 0) ? `Point ${i + 1}: period must be zero or more.` : !(r.Sa >= 0) ? `Point ${i + 1}: Sa must be zero or more.` : "",
    validate: rs => rs.some((r, i) => i && r.T <= rs[i - 1].T) ? "Periods must increase from row to row." : "",
    onChange: redraw,
  });
  const ec8 = { ag: 0.25, ground: "C" };
  const genBtn = el("button", { type: "button", class: "btn btn-small", id: "dsRsFnEc8" }, ["Generate"]);
  genBtn.addEventListener("click", () => {
    const [S, TB, TC, TD] = { A: [1.0, 0.15, 0.4, 2.0], B: [1.2, 0.15, 0.5, 2.0], C: [1.15, 0.2, 0.6, 2.0], D: [1.35, 0.2, 0.8, 2.0], E: [1.4, 0.15, 0.5, 2.0] }[ec8.ground];
    const pts = dsME.ec8Spectrum ? dsME.ec8Spectrum({ ag: ec8.ag, S, TB, TC, TD, damping: f.damping ?? 0.05 }) : null;
    if (!Array.isArray(pts) || !pts.length) return;
    rows.length = 0; for (const [T, Sa] of pts) rows.push({ T, Sa });
    F.fields.find(x => x.type === "table").dirty = true; F.touched = true;
    F.refresh(); redraw();
  });
  const adv = dkAdvanced([
    F.num(f, "damping", { label: "Function damping ratio", min: 0, lt: 1, def: 0.05, read: () => f.damping ?? 0.05, id: "dsRsFnDamp", hint: "metadata — CQC uses the load case damping" }),
    dkGroup("Generate EC8 Type 1 spectrum", [
      F.num(ec8, "ag", { label: "Design ground acceleration ag", unit: "g", gt: 0, id: "dsRsFnAg" }),
      F.select(ec8, "ground", ["A", "B", "C", "D", "E"].map(x => [x, "Ground type " + x]), { label: "Ground type", id: "dsRsFnGround" }),
      el("div", { class: "dk-inline" }, [genBtn, el("span", { class: "muted dk-note", text: "replaces the points above" })]),
    ]),
  ], { countFn: () => ((f.damping ?? 0.05) !== 0.05 ? 1 : 0) });
  F.onTouch = () => adv.refreshBadge();
  const body = [
    dkGroup("Function", [F.text(draftName, "v", { label: "Function Name", id: "dsRsFnName", validate: nameCheck(() => new Set(Object.keys(D.spectrum_functions)), name) })]),
    dkGroup("Spectral acceleration vs period", [tableNode, chart]),
    adv,
  ];
  redraw();
  return dkDialog({
    id: "dsRsFunctionData", title: "Response Spectrum Function", iconId: "exleaf-functions", size: "md", form: F, body,
    onOk: () => {
      if (!F.touched && !isNew) { done && done(name); return; }
      const nn = draftName.v.trim();
      if (name && nn !== name) dsME.renameSpectrumFunction(D, name, nn);
      const out = { ...f, name: nn };
      if (F.tableDirty(tableNode) || isNew) out.points = rows.map(r => [r.T, r.Sa]);
      D.spectrum_functions[nn] = out;
      done && done(nn);
    },
  });
}

export function editThFunction(sky, D, name, { copyOf, done } = {}) {
  D.th_functions = D.th_functions || {};
  const src = name ? D.th_functions[name] : (copyOf ? D.th_functions[copyOf] : null);
  const isNew = !name;
  const nm0 = name || uniqueName(D.th_functions, copyOf ? copyOf + "-copy" : "REC1");
  const f = src ? clone(src) : { name: nm0, values: dsME.sineRecord(), dt: 0.02 };
  const draftName = { v: nm0 };
  const F = dkForm();
  const ta = el("textarea", { class: "dk-text", rows: "6", id: "dsThFnValues", "aria-label": "Acceleration values", spellcheck: "false", style: "width:100%;font-family:var(--mono);font-size:11.5px" });
  const toText = vals => (vals || []).map(v => +(+dsU.toDisplay("accel", v)).toPrecision(6)).join(", ");
  ta.value = toText(f.values);
  let valsDirty = false;
  const taErr = el("div", { class: "dk-err", role: "alert" });
  const chart = el("div");
  const parsed = () => ta.value.split(/[\s,;]+/).filter(Boolean).map(t => dsU.parse("accel", t));
  const redraw = () => { chart.textContent = ""; const v = valsDirty ? parsed() : f.values; chart.appendChild(spark((v || []).map((a, i) => [i * (f.dt || 0.02), valsDirty ? a : a]).map(([t, a]) => [t, dsU.toDisplay("accel", a)]), { xLabel: "t (s)", yLabel: `a (${dsU.label("accel")})` })); };
  ta.addEventListener("change", () => {
    const v = parsed();
    if (v.length < 2 || v.some(x => !isFinite(x))) { taErr.textContent = "Enter at least two numeric values separated by commas or spaces."; return; }
    taErr.textContent = ""; valsDirty = true; F.touched = true; redraw();
  });
  const body = [
    dkGroup("Function", [
      F.text(draftName, "v", { label: "Function Name", id: "dsThFnName", validate: nameCheck(() => new Set(Object.keys(D.th_functions)), name) }),
      F.num(f, "dt", { label: "Time step, Δt", kind: "period", gt: 0, id: "dsThFnDt", onChange: redraw }),
      F.readout("Number of steps", () => (valsDirty ? parsed() : f.values || []).length),
    ]),
    dkGroup(`Ground acceleration values (${unitLabel("accel")})`, [ta, taErr, chart]),
  ];
  redraw();
  return dkDialog({
    id: "dsThFunctionData", title: "Time History Function", iconId: "exleaf-functions", size: "md", form: F, body,
    onOk: () => {
      if (taErr.textContent) return "Fix the acceleration values first.";
      if (!F.touched && !isNew) { done && done(name); return; }
      const nn = draftName.v.trim();
      if (name && nn !== name) dsME.renameThFunction(D, name, nn);
      const out = { ...f, name: nn };
      if (valsDirty) out.values = parsed();
      D.th_functions[nn] = out;
      done && done(nn);
    },
  });
}

/* ================================================================
   Stories and grid systems
   ================================================================ */
export function openGridStories(sky, { tab = "stories" } = {}) {
  const D = dkDraft(sky);
  const F = dkForm();
  const st = { sys: 0 };
  /* ---- stories (top first, ETABS order) */
  const storyBox = el("div");
  const errS = el("div", { class: "dk-err", role: "alert" });
  const drawStories = () => {
    storyBox.textContent = "";
    const t = el("table", { class: "data-table dk-list-tbl", id: "dsStoryTable" });
    t.appendChild(el("thead", {}, [el("tr", {}, ["Story", `Height (${unitLabel("length")})`, `Elevation (${unitLabel("length")})`, "Objects", ""].map(h => el("th", { class: "txt" }, [h])))]));
    const tb = el("tbody");
    const stories = D.stories || [];
    for (let i = stories.length - 1; i >= 0; i--) {
      const s = stories[i];
      const nameIn = el("input", { type: "text", class: "dk-text", value: s.name, "aria-label": "Story name" });
      nameIn.addEventListener("change", () => {
        const nn = nameIn.value.trim();
        if (!nn || (nn !== s.name && stories.some(x => x.name === nn))) { errS.textContent = nn ? `Story “${nn}” already exists.` : "A story needs a name."; nameIn.value = s.name; return; }
        errS.textContent = ""; dsME.renameStory(D, s.name, nn); F.touched = true; drawStories();
      });
      const hIn = el("input", { type: "text", inputmode: "decimal", class: "dk-num", value: showNum("length", s.height), "aria-label": `Height of ${s.name}` });
      hIn.addEventListener("change", () => {
        const v = dsU.parse("length", hIn.value);
        if (!(v > 0)) { errS.textContent = "Story height must be greater than zero."; hIn.classList.add("is-invalid"); return; }
        errS.textContent = ""; dsME.setStoryHeight(D, s.name, v); F.touched = true; drawStories();
      });
      const cnt = dsME.storyElementCounts(D, s.name);
      const ins = el("button", { type: "button", class: "btn btn-small", title: "Insert a story above" }, ["+ Above"]);
      ins.addEventListener("click", () => { dsME.insertStory(D, s.name, "above"); F.touched = true; drawStories(); });
      const del = el("button", { type: "button", class: "dk-x", title: `Delete ${s.name} and its ${cnt.members + cnt.shells} objects`, "aria-label": "Delete story " + s.name }, ["×"]);
      del.disabled = stories.length <= 1;
      del.addEventListener("click", () => { dsME.deleteStory(D, s.name); F.touched = true; drawStories(); });
      tb.appendChild(el("tr", {}, [el("td", {}, [nameIn]), el("td", {}, [hIn]), el("td", { class: "txt", text: dsU.fmt("length", s.elevation, 3) }),
        el("td", { class: "txt", text: `${cnt.members + cnt.shells}` }), el("td", { class: "dk-inline" }, [ins, del])]));
    }
    tb.appendChild(el("tr", { class: "is-muted" }, [el("td", { class: "txt", text: "Base" }), el("td"), el("td", { class: "txt", text: dsU.fmt("length", 0, 3) }), el("td"), el("td")]));
    t.appendChild(tb);
    storyBox.appendChild(t);
  };
  const storyPanel = el("div", {}, [dkGroup("Story Data", [dkNote("Heights are measured from the story below. Changing a height moves every object above it."), storyBox, errS])]);
  /* ---- grid lines of the selected system */
  const gridBox = el("div");
  const sysSel = el("select", { id: "dsGridSys", "aria-label": "Grid system" });
  sysSel.addEventListener("change", () => { st.sys = +sysSel.value; drawGrid(); });
  const errG = el("div", { class: "dk-err", role: "alert" });
  const lineTable = (sys, axis) => {
    const lines = sys[axis + "_lines"], labels = sys[axis + "_labels"] || [];
    const t = el("table", { class: "data-table dk-list-tbl", id: "dsGrid" + axis.toUpperCase() });
    t.appendChild(el("thead", {}, [el("tr", {}, [`${axis.toUpperCase()} grid`, `Ordinate (${unitLabel("length")})`, `Spacing (${unitLabel("length")})`, ""].map(h => el("th", { class: "txt" }, [h])))]));
    const tb = el("tbody");
    lines.forEach((v, i) => {
      const inp = el("input", { type: "text", inputmode: "decimal", class: "dk-num", value: showNum("length", v), "aria-label": `${axis} line ${labels[i] || i + 1}` });
      inp.addEventListener("change", () => {
        const nv = dsU.parse("length", inp.value);
        if (!isFinite(nv) || !dsME.setSysLine(sys, axis, i, nv)) { errG.textContent = "Enter a number that does not coincide with another grid line."; inp.classList.add("is-invalid"); return; }
        errG.textContent = ""; F.touched = true; drawGrid();
      });
      const x = el("button", { type: "button", class: "dk-x", title: "Delete grid line", "aria-label": `Delete ${axis} line ${labels[i] || i + 1}` }, ["×"]);
      x.disabled = lines.length <= 2;
      x.addEventListener("click", () => { dsME.removeSysLine(sys, axis, i); F.touched = true; drawGrid(); });
      tb.appendChild(el("tr", {}, [el("td", { class: "txt", text: labels[i] || String(i + 1) }), el("td", {}, [inp]),
        el("td", { class: "txt", text: i ? dsU.fmt("length", v - lines[i - 1], 3) : "—" }), el("td", {}, [x])]));
    });
    t.appendChild(tb);
    const add = el("button", { type: "button", class: "btn btn-small", id: "dsGridAdd" + axis.toUpperCase() }, [`+ Add ${axis.toUpperCase()} line`]);
    add.addEventListener("click", () => { dsME.addSysLine(sys, axis); F.touched = true; drawGrid(); });
    return el("div", { class: "dk-table-wrap" }, [el("div", { class: "dk-table-scroll" }, [t]), add]);
  };
  const drawGrid = () => {
    const systems = D.grid_systems && D.grid_systems.length ? D.grid_systems : [D.grid];
    if (st.sys >= systems.length) st.sys = 0;
    sysSel.textContent = "";
    systems.forEach((s, i) => sysSel.appendChild(el("option", { value: String(i), selected: i === st.sys }, [`${s.name || "G" + (i + 1)} · ${s.kind === "radial" ? "radial" : "orthogonal"}${i === 0 ? " (primary)" : ""}`])));
    gridBox.textContent = "";
    const sys = systems[st.sys];
    if (sys.kind === "radial") {
      gridBox.appendChild(dkNote(`Radial system: ${sys.radii.length} circles, ${sys.theta_deg.length} spokes — edit it with <b>Multiple Grid Systems…</b>.`));
      return;
    }
    gridBox.appendChild(el("div", { class: "dk-two" }, [lineTable(sys, "x"), lineTable(sys, "y")]));
  };
  const gridPanel = el("div", {}, [dkGroup("Grid System", [F.row("Grid system", sysSel), gridBox, errG])]);
  drawStories(); drawGrid();
  const tabs = dkTabs([{ id: "stories", label: "Story Data", body: storyPanel }, { id: "grid", label: "Grid Data", body: gridPanel }], { active: tab, id: "dsGridTabs" });
  const more = el("button", { type: "button", class: "btn btn-small dk-legacy", id: "dsGridMore", title: "Add rotated / radial grid systems, live preview (applies these edits first)" }, ["Multiple Grid Systems…"]);
  const dlg = dkDialog({
    id: "dsGridStories", title: "Stories and Grid Systems", iconId: "exleaf-grids", size: "lg", tabs,
    intro: "Story heights and grid-line positions. Grids are drawing aids; stories set the levels objects are drawn on.",
    body: tabs, extra: [more],
    onUnits: () => { drawStories(); drawGrid(); },
    onOk: () => { dkCommit(sky, D); },
  });
  more.addEventListener("click", () => thenLegacy(dlg, sky, D, () => sky.openGridEditor && sky.openGridEditor()));
  return dlg;
}
