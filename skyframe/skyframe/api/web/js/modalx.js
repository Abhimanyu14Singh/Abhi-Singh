/* SkyFrame — time-history function tools, named mass sources and modal
   cases (CONTRACT "Time-history functions, mass sources, modal cases,
   Ritz basis and FNA breadth"), dlgkit standard (docs/DIALOG_STANDARD.md).

     Define › Mass Source…            → Define Mass Sources (list): the model
                                        default source + named sources
     Define › Load Cases › Modal…     → Define Modal Cases (list): MODAL +
                                        named eigen / Ritz cases (mass source,
                                        modes, Ritz starting vectors)
     RS / TH case dialogs             → "Modal Case" selector (+ TH "Initial
                                        Conditions (FNA)") via mxRsCaseFields /
                                        mxThCaseFields (hook lines in
                                        js/dlgstd_cases.js)
     Time History Function dialog     → Import from File… (PEER AT2 / time-value
                                        pairs / equal intervals), Generate…
                                        (sine, cosine, ramp, sawtooth,
                                        triangular, user periodic), Match to
                                        Spectrum… (mxThFunctionTools, hook in
                                        js/dlgstd_defs.js)
     Modal results tab                → "Modal case" selector when the results
                                        carry results.modal_cases

   Lists edit a draft; only the list's OK commits (dkCommit: changed keys
   only — OK with defaults is byte-identical). Numbers go through
   js/units.js; the model stays SI. ?mock=1 → js/mock_modalx.js.
   Additive: __sky.modalx. */

import mxU from "./units.js";
import {
  el as mxEl, esc as mxEsc, clone as mxClone, isNum as mxIsNum, dkForm as mxForm, dkGroup as mxGroup,
  dkAdvanced as mxAdvanced, dkList as mxList, dkNote as mxNote, dkDialog as mxDialog, dkDraft as mxDraft,
  dkCommit as mxCommit, uniqueName as mxUnique,
} from "./dlgkit.js";
import { editModalCase as mxEditDefaultModal } from "./dlgstd_cases.js";
import * as MXR from "./modalx_refs.js";
import { mxMockImport, mxMockGenerate, mxMockMatch, mxMockModalCases } from "./mock_modalx.js";

let SKY = null;
const isMock = () => !!(SKY && SKY.store && SKY.store.mock);

async function mxPost(path, body, mockFn) {
  if (isMock()) {
    await new Promise(r => setTimeout(r, 60));
    return mockFn(body);
  }
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}

const fnum = (v, p = 4) => mxIsNum(v) ? String(+v.toPrecision(p)) : "—";
const patOpts = D => Object.keys(D.patterns || {}).map(p => [p, p]);

/** Tiny SVG line chart: series [{pts: [[x, y]], cls}] in display values. */
function mxSpark(series, { w = 480, h = 120, xLabel = "", yLabel = "", logx = false } = {}) {
  const all = series.flatMap(s => s.pts).filter(p => isFinite(p[0]) && isFinite(p[1]));
  const box = mxEl("div", { class: "mx-spark" });
  if (all.length < 2) return box;
  const fx = x => logx ? Math.log(Math.max(x, 1e-9)) : x;
  const xs = all.map(p => fx(p[0])), ys = all.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(0, ...ys), y1 = Math.max(0, ...ys) || 1;
  const sx = x => 30 + (fx(x) - x0) / ((x1 - x0) || 1) * (w - 40), sy = y => h - 18 - (y - y0) / ((y1 - y0) || 1) * (h - 28);
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("width", "100%"); svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${yLabel} vs ${xLabel}`);
  const line = (x1_, y1_, x2_, y2_) => { const l = document.createElementNS(NS, "line"); Object.entries({ x1: x1_, y1: y1_, x2: x2_, y2: y2_, stroke: "currentColor", "stroke-opacity": "0.25" }).forEach(([k, v]) => l.setAttribute(k, v)); svg.appendChild(l); };
  line(30, sy(0), w - 10, sy(0)); line(30, 8, 30, h - 18);
  const COL = ["var(--accent, #3b82f6)", "#e8833a", "#8a8f98"];
  series.forEach((s, i) => {
    const pts = s.pts.filter(p => isFinite(p[0]) && isFinite(p[1]));
    const step = Math.max(1, Math.floor(pts.length / 800));
    const d = pts.filter((_, k) => k % step === 0).map(p => `${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(" ");
    const pl = document.createElementNS(NS, "polyline");
    pl.setAttribute("points", d); pl.setAttribute("fill", "none"); pl.setAttribute("stroke", s.color || COL[i % 3]);
    pl.setAttribute("stroke-width", "1.4"); if (s.dash) pl.setAttribute("stroke-dasharray", "4 3");
    svg.appendChild(pl);
  });
  const t = (x, y, txt, anchor = "start") => { const n = document.createElementNS(NS, "text"); n.setAttribute("x", x); n.setAttribute("y", y); n.setAttribute("font-size", "10"); n.setAttribute("fill", "currentColor"); n.setAttribute("text-anchor", anchor); n.textContent = txt; svg.appendChild(n); };
  t(w - 10, h - 4, xLabel, "end"); t(2, 10, yLabel);
  box.appendChild(svg);
  if (series.some(s => s.label)) box.appendChild(mxEl("div", { class: "muted mx-legend", html: series.filter(s => s.label).map((s, i) => `<span style="color:${s.color || COL[i % 3]}">━</span> ${mxEsc(s.label)}`).join(" &nbsp; ") }));
  return box;
}

/* ================================================================
   Mass sources
   ================================================================ */
const MODE_OPTS = [["weight", "Weight / g"], ["element_self_mass", "Material mass density"]];
const DEF_LABEL = "MsSrc (default)";

function sourceSummary(src) {
  const pats = Object.entries(src.mass_source || {}).map(([p, f]) => `${p}×${fnum(f, 3)}`).join(", ");
  const o = src.options || {};
  return { self: o.self_mass === false ? "No" : (src.mode === "element_self_mass" ? "Yes (density)" : "Yes"), pats: o.patterns === false ? "—" : (pats || "—") };
}

function defaultSource(D) {
  return {
    mass_source: (D.mass_source && Object.keys(D.mass_source).length) ? D.mass_source : (D.mass_from_patterns || {}),
    mode: D.mass_source_mode || "weight",
    options: MXR.mxNormSource({ options: D.mass_options }).options,
  };
}

export function openMassSources(sky, { select } = {}) {
  const D = mxDraft(sky);
  const had = "mass_sources" in D;
  D.mass_sources = D.mass_sources || {};
  const err = mxEl("p", { class: "dk-err dk-side-err", role: "alert" });
  const list = mxList({
    id: "mxMsList",
    items: () => [{ key: "", s: defaultSource(D) }, ...Object.entries(D.mass_sources).map(([k, s]) => ({ key: k, s: MXR.mxNormSource(s) }))],
    columns: [
      { label: "Mass Source", get: it => it.key || DEF_LABEL },
      { label: "Self Mass", get: it => sourceSummary(it.s).self },
      { label: "Load Patterns", get: it => sourceSummary(it.s).pats, cls: "dk-mono" },
      { label: "Used by", get: it => (it.key ? MXR.mxSourceRefs(D, it.key) : ["MODAL", ...Object.entries(D.modal_cases || {}).filter(([, c]) => !c.mass_source).map(([n]) => n)]).join(", ") || "—" },
    ],
    selected: select ?? "",
    onOpen: k => edit(k),
    actions: [
      { sep: "Add" },
      { label: "Add New Mass Source…", id: "mxMsAdd", fn: () => edit(null) },
      { label: "Add Copy of Mass Source…", id: "mxMsCopy", needsSel: false, fn: k => edit(null, k ?? "") },
      { sep: "Selected" },
      { label: "Modify/Show Mass Source…", id: "mxMsModify", fn: k => edit(k ?? "") },
      { label: "Delete Mass Source", id: "mxMsDelete", disabled: k => !k, fn: k => {
        const refs = MXR.mxSourceRefs(D, k);
        if (refs.length) { err.textContent = `${k} is used by ${refs.join(", ")} — pick another mass source there first.`; return; }
        err.textContent = ""; delete D.mass_sources[k]; refresh(); } },
    ],
    extraSide: err,
  });
  const refresh = () => { list.render(); dlg && dlg.setNote(`${1 + Object.keys(D.mass_sources).length} mass sources`); };
  const edit = (name, copyOf) => editMassSource(sky, D, name, { copyOf, done: nm => { list.select(nm); refresh(); } });
  const dlg = mxDialog({
    id: "mxMassSources", title: "Define Mass Sources", iconId: "mass-source", size: "lg",
    intro: "Each modal case uses one mass source. <b>MsSrc (default)</b> is the model's mass source, used by the MODAL case; add named sources for other modal cases.",
    body: list,
    onOk: () => { if (!had && !Object.keys(D.mass_sources).length) delete D.mass_sources; mxCommit(sky, D); },
  });
  refresh();
  return dlg;
}

/** Mass Source Data. name "" = the default (model-level) source. */
export function editMassSource(sky, D, name, { copyOf, done } = {}) {
  const isDefault = name === "";
  const isNew = name == null;
  const taken = () => new Set(Object.keys(D.mass_sources || {}));
  const nm0 = isNew ? mxUnique(taken(), copyOf ? (copyOf || "MsSrc") + "-copy" : "MsSrc2") : name;
  const srcObj = isDefault ? defaultSource(D) : MXR.mxNormSource(isNew ? (copyOf != null ? (copyOf === "" ? defaultSource(D) : D.mass_sources[copyOf]) : null) : D.mass_sources[name]);
  const s = mxClone(srcObj);
  const draftName = { v: nm0 };
  const F = mxForm();
  const rows = Object.entries(s.mass_source || {}).map(([k, f]) => ({ k, f }));
  const tblBox = mxEl("div");
  const tbl = F.table(rows, [
    { key: "k", label: "Load Pattern", type: "select", options: r => patOpts(D).filter(([p]) => p === r.k || !rows.some(x => x.k === p)) },
    { key: "f", label: "Multiplier", type: "num" },
  ], {
    id: "mxMsRows", addLabel: "+ Add pattern", empty: "No load patterns in this mass source.",
    newRow: () => { const p = patOpts(D).map(x => x[0]).find(p_ => !rows.some(r => r.k === p_)); return p ? { k: p, f: 1 } : null; },
    canAdd: () => patOpts(D).some(([p]) => !rows.some(r => r.k === p)),
    validateRow: (r, i) => !(mxIsNum(r.f) && r.f >= 0) ? `Row ${i + 1}: the multiplier must be zero or more.` : "",
    validate: rs => new Set(rs.map(r => r.k)).size !== rs.length ? "A load pattern is listed twice — merge the rows." : "",
  });
  tblBox.appendChild(tbl);
  const sync = () => tblBox.classList.toggle("dk-disabled", !s.options.patterns);
  const opt = (k, label, id) => F.check(s.options, k, { label, id });
  const body = [
    mxGroup("General", [
      isDefault ? F.readout("Mass Source Name", () => DEF_LABEL)
        : F.text(draftName, "v", { label: "Mass Source Name", id: "mxMsName", validate: v => v !== name && taken().has(v) ? `“${v}” is already a mass source — pick another name.` : "" }),
    ]),
    mxGroup("Mass Source", [
      opt("self_mass", "Element self mass and additional mass", "mxMsSelf"),
      F.select(s, "mode", MODE_OPTS, { label: "Self mass from", id: "mxMsMode" }),
      F.check(s.options, "patterns", { label: "Specified load patterns (load × multiplier / g)", id: "mxMsPats", onChange: sync }),
      tblBox,
    ]),
    mxGroup("Mass Options", [
      opt("include_lateral", "Include lateral mass (X, Y)", "mxMsLat"),
      opt("include_vertical", "Include vertical mass (Z)", "mxMsVert"),
      opt("lump_at_stories", "Lump lateral mass at story levels", "mxMsLump"),
    ]),
  ];
  sync();
  return mxDialog({
    id: "mxMassSourceData", title: "Mass Source Data", iconId: "mass-source", size: "md", form: F, body,
    intro: isDefault ? "The model's mass source (used by the MODAL case and every case without a modal case)." : "A named mass source for modal cases.",
    onOk: () => {
      const o = s.options;
      if (!o.include_lateral && !o.include_vertical) return "Include at least one of lateral or vertical mass.";
      if (!o.self_mass && !o.patterns) return "Choose at least one mass source (self mass or load patterns).";
      if (!F.touched && !isNew) { done && done(name); return; }
      if (isDefault) {
        const dirty = k => F.fields.some(f => f.key === k && f.dirty);
        if (F.tableDirty(tbl)) D.mass_source = Object.fromEntries(rows.map(r => [r.k, r.f]));
        if (dirty("mode")) D.mass_source_mode = s.mode;
        if (MXR.MX_MASS_OPTS.some(dirty)) D.mass_options = { ...MXR.mxNormSource({ options: D.mass_options }).options, ...o };
        done && done("");
        return;
      }
      const nn = draftName.v.trim();
      if (!nn) return "Enter a name.";
      if (name && nn !== name) MXR.mxRenameSource(D, name, nn);
      D.mass_sources = D.mass_sources || {};
      D.mass_sources[nn] = { mass_source: Object.fromEntries(rows.map(r => [r.k, r.f])), mode: s.mode, options: { ...o } };
      done && done(nn);
    },
  });
}

/* ================================================================
   Modal cases
   ================================================================ */
const TYPE_LABEL = { eigen: "Eigen", ritz: "Ritz" };
const START_TYPES = [["accel", "Acceleration"], ["pattern", "Load Pattern"], ["links", "Link DOFs (nonlinear links)"]];

export function openModalCases(sky, { select } = {}) {
  const D = mxDraft(sky);
  const had = "modal_cases" in D;
  D.modal_cases = D.modal_cases || {};
  const err = mxEl("p", { class: "dk-err dk-side-err", role: "alert" });
  const items = () => [
    { key: "MODAL", c: { type: "eigen", num_modes: D.num_modes ?? 12, mass_source: "", stiffness_case: D.modal_from_case || null }, def: true },
    ...Object.entries(D.modal_cases).map(([k, c]) => ({ key: k, c: MXR.mxNormCase(c) })),
  ];
  const list = mxList({
    id: "mxMcList",
    items,
    columns: [
      { label: "Modal Case", get: it => it.key + (it.def ? " (default)" : "") },
      { label: "Type", get: it => TYPE_LABEL[it.c.type] || it.c.type },
      { label: "Modes", get: it => it.c.num_modes },
      { label: "Mass Source", get: it => it.c.mass_source || DEF_LABEL },
      { label: "Starting Vectors / Stiffness", get: it => it.c.type === "ritz" ? MXR.mxStartsText(it.c.ritz_starts) : (it.c.stiffness_case ? `end of ${it.c.stiffness_case}` : "unstressed") },
      { label: "Used by", get: it => MXR.mxModalRefs(D, it.key).join(", ") || "—" },
    ],
    selected: select || "MODAL",
    onOpen: k => edit(k),
    actions: [
      { sep: "Add" },
      { label: "Add New Case…", id: "mxMcAdd", fn: () => edit(null, null, "eigen") },
      { label: "Add New Ritz Case…", id: "mxMcAddRitz", fn: () => edit(null, null, "ritz") },
      { label: "Add Copy of Case…", id: "mxMcCopy", needsSel: true, disabled: k => k === "MODAL", fn: k => edit(null, k) },
      { sep: "Selected" },
      { label: "Modify/Show Case…", id: "mxMcModify", needsSel: true, fn: k => edit(k) },
      { label: "Delete Case", id: "mxMcDelete", needsSel: true, disabled: k => !k || k === "MODAL", fn: k => {
        const refs = MXR.mxModalRefs(D, k);
        if (refs.length) { err.textContent = `${k} is used by ${refs.join(", ")} — pick another modal case there first.`; return; }
        err.textContent = ""; delete D.modal_cases[k];
        if (Array.isArray(D.cases_not_run)) D.cases_not_run = D.cases_not_run.filter(n => n !== k);
        refresh(); } },
    ],
    extraSide: err,
  });
  const refresh = () => { list.render(); dlg && dlg.setNote(`${1 + Object.keys(D.modal_cases).length} modal cases`); };
  const edit = (name, copyOf, type) => {
    if (name === "MODAL") return mxEditDefaultModal(sky, D, { done: () => refresh() });
    return editModalCaseX(sky, D, name, { copyOf, type, done: nm => { list.select(nm); refresh(); } });
  };
  const dlg = mxDialog({
    id: "mxModalCases", title: "Define Modal Cases", iconId: "exleaf-modal", size: "lg",
    intro: "A modal case gives the periods and mode shapes (eigenvectors or load-dependent Ritz vectors) that response-spectrum, modal time-history and FNA cases use. Each case has its own mass source.",
    body: list,
    onOk: () => { if (!had && !Object.keys(D.modal_cases).length) delete D.modal_cases; mxCommit(sky, D); },
  });
  refresh();
  return dlg;
}

/** Modal Case Data (named eigen / Ritz case). */
export function editModalCaseX(sky, D, name, { copyOf, type, done } = {}) {
  const isNew = !name;
  const taken = () => MXR.mxTakenNames(D);
  const nm0 = name || mxUnique(taken(), copyOf ? copyOf + "-copy" : (type === "ritz" ? "RITZ" : "MODAL2"));
  const src = name ? D.modal_cases[name] : (copyOf ? D.modal_cases[copyOf] : { type: type || "eigen", num_modes: D.num_modes ?? 12 });
  const c = MXR.mxNormCase(src);
  const draftName = { v: nm0 };
  const F = mxForm();
  const nls = Object.keys(D.nonlinear_static_cases || {});
  const starts = (c.ritz_starts || MXR.MX_DEFAULT_STARTS).map(s => ({ t: s.type, v: s.type === "accel" ? s.dir : s.type === "pattern" ? s.name : "" }));
  const vOpts = r => r.t === "accel" ? MXR.MX_ACCEL_DIRS.map(d => [d, d]) : r.t === "pattern" ? patOpts(D) : [["", "— all nonlinear link DOFs —"]];
  const stbl = F.table(starts, [
    { key: "t", label: "Load Type", type: "select", options: () => START_TYPES },
    { key: "v", label: "Load Name", type: "select", options: r => vOpts(r) },
  ], {
    id: "mxMcStarts", addLabel: "+ Add starting vector", minRows: 1, minMsg: "A Ritz case needs at least one starting load vector.",
    newRow: () => ({ t: "accel", v: "UY" }),
    validateRow: (r, i) => (r.t === "pattern" && !(D.patterns || {})[r.v]) ? `Row ${i + 1}: pick a load pattern.` : "",
  });
  const ritzBox = mxEl("div", {}, [stbl]);
  const cyc = { v: c.max_cycles ?? 0 };
  const advRitz = mxAdvanced([
    F.num(cyc, "v", { label: "Maximum cycles per starting vector", int: true, min: 0, def: 0, id: "mxMcCycles", hint: "0 = no limit" }),
  ], { summary: "Advanced — Ritz", countFn: () => (cyc.v ? 1 : 0) });
  const stiff = { v: c.stiffness_case || "" };
  const advEig = mxAdvanced([
    F.select(stiff, "v", [["", "Zero initial conditions (unstressed)"], ...nls.map(n => [n, "End of nonlinear case " + n])], { label: "Stiffness to use", id: "mxMcStiff",
      hint: nls.length ? "Tangent stiffness at the end of a nonlinear static case." : "Define a nonlinear static case to start from its stiffness." }),
  ], { summary: "Advanced — initial stiffness", countFn: () => (stiff.v ? 1 : 0) });
  const ritzGroup = mxGroup("Loads Applied (starting load vectors)", [ritzBox, advRitz], { note: "Acceleration loads are mass-proportional; a load pattern starts from its static deflection (exact static response with one vector); link DOFs add a unit force pair per nonlinear link DOF (recommended for FNA)." });
  const syncType = () => { ritzGroup.hidden = c.type !== "ritz"; advEig.hidden = c.type === "ritz"; };
  const msOpts = () => [["", DEF_LABEL], ...Object.keys(D.mass_sources || {}).map(n => [n, n])];
  const body = [
    mxGroup("General", [
      F.text(draftName, "v", { label: "Modal Case Name", id: "mxMcName", validate: v => v !== name && taken().has(v) ? `“${v}” is already a case or combination name.` : "" }),
      F.radio(c, "type", [["eigen", "Eigen"], ["ritz", "Ritz"]], { label: "Modal Case Subtype", idPrefix: "mxMcType-", onChange: syncType }),
      F.select(c, "mass_source", msOpts, { label: "Mass Source", id: "mxMcMs" }),
      F.num(c, "num_modes", { label: "Maximum Number of Modes", int: true, min: 1, max: 1000, id: "mxMcModes" }),
    ]),
    ritzGroup,
    advEig,
  ];
  syncType();
  return mxDialog({
    id: "mxModalCaseData", title: "Modal Case Data", iconId: "exleaf-modal", size: "md", form: F, body,
    onOk: () => {
      if (!F.touched && !isNew) { done && done(name); return; }
      const nn = draftName.v.trim();
      if (!nn) return "Enter a name.";
      if (name && nn !== name) MXR.mxRenameModalCase(D, name, nn);
      const out = { type: c.type, num_modes: c.num_modes, mass_source: c.mass_source || "", stiffness_case: null };
      if (c.type === "ritz") {
        if (!starts.length) return "A Ritz case needs at least one starting load vector.";
        out.ritz_starts = starts.map(r => r.t === "accel" ? { type: "accel", dir: MXR.MX_ACCEL_DIRS.includes(r.v) ? r.v : "UX" }
          : r.t === "pattern" ? { type: "pattern", name: r.v } : { type: "links" });
        out.max_cycles = cyc.v || 0;
      } else if (stiff.v) out.stiffness_case = stiff.v;
      D.modal_cases = D.modal_cases || {};
      D.modal_cases[nn] = out;
      done && done(nn);
    },
  });
}

/* ================================================================
   RS / TH case dialog fields (hook lines in js/dlgstd_cases.js)
   ================================================================ */
const mcOpts = D => [["", "MODAL (default)"], ...Object.entries(D.modal_cases || {}).map(([n, c]) => [n, `${n} — ${TYPE_LABEL[(c.type || "eigen")]}`])];
const writeOpt = (c, key) => v => { if (v) c[key] = v; else delete c[key]; };

export function mxRsCaseFields(F, c, D) {
  return [F.select(c, "modal_case", () => mcOpts(D), { label: "Modal Case", id: "dcRsModalCase", read: () => c.modal_case || "", write: writeOpt(c, "modal_case"),
    hint: "Basis and mass source of the modal combination (Define › Modal Cases)." })];
}

export function mxThCaseFields(F, c, D) {
  const nls = Object.keys(D.nonlinear_static_cases || {});
  return [
    F.select(c, "modal_case", () => mcOpts(D), { label: "Modal Case", id: "dcThModalCase", read: () => c.modal_case || "", write: writeOpt(c, "modal_case"),
      hint: "Mass source and damping periods (direct integration); the modal basis of FNA." }),
    F.select(c, "initial_case", [["", "Zero initial conditions"], ...nls.map(n => [n, "Continue from end of " + n])], { label: "Initial Conditions", id: "dcThInitial",
      read: () => c.initial_case || "", write: writeOpt(c, "initial_case"),
      hint: nls.length ? "FNA starts from this nonlinear static state (link axial loads, gravity); direct integration applies its loads first." : "Define a nonlinear static case (e.g. gravity) to continue from it." }),
  ];
}

/* ================================================================
   Time-history function tools (hook in js/dlgstd_defs.js)
   ================================================================ */
const FILE_UNITS = [["", "From file header (AT2) / g"], ["g", "g"], ["m/s2", "m/s²"], ["cm/s2", "cm/s²"], ["mm/s2", "mm/s²"], ["in/s2", "in/s²"], ["ft/s2", "ft/s²"]];

/** Buttons for the Time History Function dialog. ctx: {D, values() → SI
    array, dt() → s, setValues(values, dt, source)}. */
export function mxThFunctionTools(ctx) {
  const b = (label, id, fn) => { const x = mxEl("button", { type: "button", class: "btn btn-small", id }, [label]); x.addEventListener("click", fn); return x; };
  return mxGroup("Define From", [mxEl("div", { class: "dk-inline mx-tools" }, [
    b("Import from File…", "mxThImport", () => openThImport(ctx)),
    b("Generate…", "mxThGenerate", () => openThGenerate(ctx)),
    b("Match to Spectrum…", "mxThMatch", () => openThMatch(ctx)),
  ])], { note: "PEER NGA .AT2, time–value pairs or values at equal intervals; periodic / ramp generators; spectral matching of the current record to a response-spectrum function." });
}

function previewChart(box, values, dt) {
  box.textContent = "";
  if (!values || values.length < 2) return;
  const pk = Math.max(...values.map(Math.abs));
  box.appendChild(mxEl("p", { class: "muted dk-note", id: "mxPreviewInfo", text: `${values.length} points · Δt ${fnum(dt)} s · duration ${fnum((values.length - 1) * dt)} s · peak ${mxU.fmt("accel", pk, 3)} ${mxU.label("accel")}` }));
  box.appendChild(mxSpark([{ pts: values.map((a, i) => [i * dt, mxU.toDisplay("accel", a)]) }], { xLabel: "t (s)", yLabel: `a (${mxU.label("accel")})` }));
}

function openThImport(ctx) {
  const st = { format: "at2", header_lines: 0, dt: 0.01, units: "", scale: 1 };
  const F = mxForm();
  const ta = mxEl("textarea", { class: "dk-text", rows: "7", id: "mxImpText", spellcheck: "false", "aria-label": "File contents", style: "width:100%;font-family:var(--mono);font-size:11px" });
  const file = mxEl("input", { type: "file", id: "mxImpFile", accept: ".at2,.AT2,.txt,.csv,.dat,.th" });
  const err = mxEl("div", { class: "dk-err", role: "alert" });
  const prev = mxEl("div", { id: "mxImpPreview" });
  let result = null;
  file.addEventListener("change", async () => {
    const f = file.files && file.files[0];
    if (!f) return;
    ta.value = await f.text();
    if (/\.at2$/i.test(f.name)) { st.format = "at2"; F.refresh(); syncFmt(); }
    run();
  });
  const dtRow = mxEl("div");
  const hdrRow = mxEl("div");
  const syncFmt = () => { dtRow.hidden = st.format === "at2"; hdrRow.hidden = st.format === "at2"; };
  dtRow.appendChild(F.num(st, "dt", { label: "Time step, Δt", kind: "period", gt: 0, id: "mxImpDt", hint: "Equal intervals: required · pairs: resampling step (blank-free uniform pairs keep their own)" }));
  hdrRow.appendChild(F.num(st, "header_lines", { label: "Header lines to skip", int: true, min: 0, id: "mxImpHdr" }));
  const run = async () => {
    err.textContent = ""; result = null; prev.textContent = "";
    if (!ta.value.trim()) return;
    const body = { text: ta.value, format: st.format, scale: st.scale };
    if (st.format !== "at2") body.header_lines = st.header_lines;
    if (st.format === "equal" || (st.format === "time_value" && F.fields.some(f => f.key === "dt" && f.dirty))) body.dt = st.dt;
    if (st.units) body.units = st.units;
    try {
      result = await mxPost("/api/th_function/import", body, mxMockImport);
      previewChart(prev, result.values, result.dt);
      if (result.warnings && result.warnings.length) prev.appendChild(mxNote(result.warnings.map(mxEsc).join("<br>")));
    } catch (e) { err.textContent = e.message; }
  };
  const readBtn = mxEl("button", { type: "button", class: "btn btn-small", id: "mxImpRead" }, ["Read / Preview"]);
  readBtn.addEventListener("click", run);
  const body = [
    mxGroup("File", [
      F.radio(st, "format", [["at2", "PEER NGA (.AT2)"], ["time_value", "Time and function values"], ["equal", "Values at equal intervals"]], { label: "Format", idPrefix: "mxImpFmt-", onChange: () => { syncFmt(); run(); } }),
      F.row("Browse", file), ta,
    ]),
    mxGroup("Values", [dtRow, hdrRow,
      F.select(st, "units", FILE_UNITS, { label: "Units of the values", id: "mxImpUnits", onChange: run }),
      F.num(st, "scale", { label: "Scale factor", id: "mxImpScale", onChange: run, validate: v => v === 0 ? "Must not be zero." : "" }),
      mxEl("div", { class: "dk-inline" }, [readBtn]), err, prev]),
  ];
  syncFmt();
  return mxDialog({
    id: "mxThImport", title: "Time History Function — From File", iconId: "exleaf-functions", size: "md", form: F, body,
    intro: "Paste the file or browse for it; the record is converted to the model's SI acceleration (m/s²).",
    okLabel: "Use Record",
    onOk: async () => {
      if (!result) await run();
      if (!result) return err.textContent || "Read a record first.";
      ctx.setValues(result.values, result.dt, result.source);
    },
  });
}

const GEN_KINDS = [["sine", "Sine"], ["cosine", "Cosine"], ["ramp", "Ramp"], ["sawtooth", "Sawtooth"], ["triangular", "Triangular"], ["user_periodic", "User Periodic"]];
function openThGenerate(ctx) {
  const st = { kind: "sine", period: 1, steps_per_cycle: 16, cycles: 1, amplitude: 1, ramp_time: 1, max_time: 2, dt: 0.05, saw_ramp: 0.125 };
  const F = mxForm();
  const prev = mxEl("div", { id: "mxGenPreview" });
  const err = mxEl("div", { class: "dk-err", role: "alert" });
  const uv = mxEl("textarea", { class: "dk-text", rows: "3", id: "mxGenUser", spellcheck: "false", "aria-label": "One cycle of values", style: "width:100%;font-family:var(--mono);font-size:11px" });
  uv.value = "0, 1, 0, -1, 0";
  const perBox = mxEl("div"), rampBox = mxEl("div"), sawBox = mxEl("div"), userBox = mxEl("div"), dtBox = mxEl("div");
  perBox.append(
    F.num(st, "period", { label: "Period", kind: "period", gt: 0, id: "mxGenPeriod", onChange: () => draw() }),
    F.num(st, "steps_per_cycle", { label: "Number of steps per cycle", int: true, min: 4, id: "mxGenSteps", onChange: () => draw() }),
    F.num(st, "cycles", { label: "Number of cycles", int: true, min: 1, id: "mxGenCycles", onChange: () => draw() }));
  sawBox.append(F.num(st, "saw_ramp", { label: "Ramp time", kind: "period", gt: 0, id: "mxGenSawRamp", onChange: () => draw(), hint: "at most period / 4" }));
  rampBox.append(
    F.num(st, "ramp_time", { label: "Ramp time", kind: "period", gt: 0, id: "mxGenRampTime", onChange: () => draw() }),
    F.num(st, "max_time", { label: "Maximum time", kind: "period", gt: 0, id: "mxGenMaxTime", onChange: () => draw() }));
  dtBox.append(F.num(st, "dt", { label: "Time step, Δt", kind: "period", gt: 0, id: "mxGenDt", onChange: () => draw() }));
  userBox.append(mxEl("div", { class: "dk-label", text: `One cycle of values (${mxU.label("accel")}), at Δt` }), uv,
    F.num(st, "cycles", { label: "Number of cycles", int: true, min: 1, id: "mxGenUCycles", onChange: () => draw() }));
  uv.addEventListener("change", () => draw());
  let result = null;
  const req = () => {
    const k = st.kind, b = { kind: k, amplitude: st.amplitude };
    if (["sine", "cosine", "sawtooth", "triangular"].includes(k)) Object.assign(b, { period: st.period, steps_per_cycle: st.steps_per_cycle, cycles: st.cycles });
    if (k === "sawtooth") b.ramp_time = st.saw_ramp;
    if (k === "ramp") Object.assign(b, { ramp_time: st.ramp_time, max_time: st.max_time, dt: st.dt });
    if (k === "user_periodic") {
      const vals = uv.value.split(/[\s,;]+/).filter(Boolean).map(t => mxU.parse("accel", t));
      Object.assign(b, { values: vals.map(v => v / (st.amplitude || 1)), dt: st.dt, cycles: st.cycles });
    }
    return b;
  };
  const sync = () => {
    const k = st.kind;
    perBox.hidden = !["sine", "cosine", "sawtooth", "triangular"].includes(k);
    sawBox.hidden = k !== "sawtooth"; rampBox.hidden = k !== "ramp";
    dtBox.hidden = !(k === "ramp" || k === "user_periodic"); userBox.hidden = k !== "user_periodic";
  };
  const draw = async () => {
    err.textContent = ""; result = null;
    try { result = await mxPost("/api/th_function/generate", req(), mxMockGenerate); previewChart(prev, result.values, result.dt); }
    catch (e) { prev.textContent = ""; err.textContent = e.message; }
  };
  const body = [
    mxGroup("Function", [
      F.select(st, "kind", GEN_KINDS, { label: "Function type", id: "mxGenKind", onChange: () => { sync(); draw(); } }),
      F.num(st, "amplitude", { label: "Amplitude", kind: "accel", id: "mxGenAmp", onChange: () => draw() }),
      perBox, sawBox, rampBox, dtBox, userBox,
    ]),
    mxGroup("Preview", [err, prev]),
  ];
  sync();
  draw();
  return mxDialog({
    id: "mxThGenerate", title: "Time History Function — Generate", iconId: "exleaf-functions", size: "md", form: F, body,
    okLabel: "Use Record",
    onOk: async () => {
      if (!result) await draw();
      if (!result) return err.textContent || "Fix the parameters first.";
      ctx.setValues(result.values, result.dt, result.source);
    },
  });
}

function openThMatch(ctx) {
  const D = ctx.D;
  const fns = Object.keys(D.spectrum_functions || {});
  const st = { fn: fns[0] || "", t_min: 0.05, t_max: 3, damping: 0.05 };
  const F = mxForm();
  const out = mxEl("div", { id: "mxMatchOut" });
  const err = mxEl("div", { class: "dk-err", role: "alert" });
  let result = null;
  const runBtn = mxEl("button", { type: "button", class: "btn btn-small", id: "mxMatchRun" }, ["Match"]);
  const run = async () => {
    err.textContent = ""; out.textContent = ""; result = null;
    const sf = (D.spectrum_functions || {})[st.fn];
    if (!sf) { err.textContent = "Pick a response-spectrum function (Define › Functions › Response Spectrum)."; return; }
    const vals = ctx.values(), dt = ctx.dt();
    if (!vals || vals.length < 2) { err.textContent = "The current record is empty."; return; }
    runBtn.disabled = true; out.appendChild(mxEl("p", { class: "muted", text: "Matching…" }));
    try {
      result = await mxPost("/api/th_function/match", { values: vals, dt, target: sf.points, t_min: st.t_min, t_max: st.t_max, damping: st.damping }, mxMockMatch);
      out.textContent = "";
      const pct = result.max_misfit * 100;
      out.appendChild(mxEl("p", { class: "dk-note " + (pct <= 10 ? "" : "dk-err"), id: "mxMatchMisfit",
        text: `Max misfit ${pct.toFixed(1)} % over ${fnum(st.t_min)}–${fnum(st.t_max)} s after ${result.iterations} iterations ${pct <= 10 ? "(within ±10 %)" : "(outside ±10 % — widen the range or use a longer seed)"}` }));
      const g = mxU.toDisplay("accel", 1);
      out.appendChild(mxSpark([
        { pts: result.periods.map((T, i) => [T, result.target[i] * g]), label: "target", color: "#8a8f98", dash: true },
        { pts: result.periods.map((T, i) => [T, result.matched[i] * g]), label: "matched" },
        { pts: result.periods.map((T, i) => [T, result.original[i] * g]), label: "seed", color: "#e8833a" },
      ], { xLabel: "T (s, log)", yLabel: `PSA (${mxU.label("accel")})`, logx: true }));
    } catch (e) { out.textContent = ""; err.textContent = e.message; }
    finally { runBtn.disabled = false; }
  };
  runBtn.addEventListener("click", run);
  const body = [
    mxGroup("Target Spectrum", [
      F.select(st, "fn", fns.map(f => [f, f]), { label: "Response spectrum function", id: "mxMatchFn",
        hint: fns.length ? "Sa in g (the function's points)" : "No spectrum functions yet — add one on the Response Spectrum tab." }),
      F.num(st, "t_min", { label: "Period range from", kind: "period", gt: 0, id: "mxMatchTmin" }),
      F.num(st, "t_max", { label: "Period range to", kind: "period", gt: 0, id: "mxMatchTmax", validate: v => v <= st.t_min ? "Must exceed the lower period." : "" }),
      F.num(st, "damping", { label: "Damping ratio", gt: 0, lt: 1, def: 0.05, id: "mxMatchDamp" }),
      mxEl("div", { class: "dk-inline" }, [runBtn]),
    ], { note: "Frequency-domain spectral matching (iterative Fourier-amplitude scaling with baseline correction) of the CURRENT record; the matched record replaces it on OK." }),
    mxGroup("Result", [err, out]),
  ];
  return mxDialog({
    id: "mxThMatch", title: "Time History Function — Match to Spectrum", iconId: "exleaf-functions", size: "md", form: F, body,
    okLabel: "Use Matched Record",
    onOk: async () => {
      if (!result) await run();
      if (!result) return err.textContent || "Run the match first.";
      ctx.setValues(result.values, result.dt, result.source);
    },
  });
}

/* ================================================================
   Modal results tab: named modal case selector
   ================================================================ */
function syncModalTab(sky) {
  const S = sky.store, r = S.results;
  const tools = document.querySelector("#content-modal .pane-tools");
  if (!tools) return;
  let wrap = document.getElementById("mxModalCaseWrap");
  const cases = r && r.modal_cases ? Object.keys(r.modal_cases) : [];
  if (!cases.length) { if (wrap) wrap.hidden = true; return; }
  if (!wrap) {
    wrap = mxEl("label", { class: "rs-field", id: "mxModalCaseWrap", title: "Show the results of a named modal case (Define › Modal Cases)" },
      [mxEl("span", { text: "case" }), mxEl("select", { id: "mxModalCaseSel", "aria-label": "Modal case" })]);
    tools.insertBefore(wrap, tools.firstChild);
    wrap.querySelector("select").addEventListener("change", e => showCase(sky, e.target.value));
  }
  wrap.hidden = false;
  const sel = wrap.querySelector("select");
  const cur = S.mxModalCase && cases.includes(S.mxModalCase) ? S.mxModalCase : "MODAL";
  sel.textContent = "";
  for (const n of ["MODAL", ...cases]) sel.appendChild(mxEl("option", { value: n, selected: n === cur }, [n === "MODAL" ? "MODAL" : `${n} (${TYPE_LABEL[r.modal_cases[n].type] || ""})`]));
}

function showCase(sky, name) {
  const S = sky.store, r = S.results;
  if (!r) return;
  if (!r._mxDefaultModal) r._mxDefaultModal = r._eigenModal || r.modal;
  S.mxModalCase = name;
  if (name === "MODAL") { r.modal = r._mxDefaultModal; r._eigenModal = r._mxDefaultModal; }
  else { r._eigenModal = r.modal_cases[name]; r.modal = r.modal_cases[name]; }
  S.modalBasis = "eigen";
  if (S.overlay && S.overlay.modeIndex >= (r.modal.periods || []).length) S.overlay.modeIndex = 0;
  try { sky.rerenderAll && sky.rerenderAll(); sky.syncOverlayUI && sky.syncOverlayUI(); } catch (e) { console.warn("modal case view", e); }
  const note = document.getElementById("modalBasisNote");
  if (note && name !== "MODAL") {
    const mc = r.modal_cases[name];
    note.textContent = `Modal case ${name} · ${TYPE_LABEL[mc.type] || mc.type} · mass source ${mc.mass_source || DEF_LABEL}` + ((mc.warnings || []).length ? ` · ⚠ ${mc.warnings.join("; ")}` : "");
  }
  syncModalTab(sky);
}

/* ================================================================
   install
   ================================================================ */
export function initModalX(sky) {
  if (!sky || !sky.store) return;
  SKY = sky;
  if (sky.std) {                       // dialog routes (js/dlgstd.js) → lists
    sky.std.massSource = () => openMassSources(sky);
    sky.std.modalCase = () => openModalCases(sky);
  }
  document.addEventListener("sky:results-changed", () => {
    const S = sky.store;
    if (S.results && S.mock && S.model && S.model.modal_cases && Object.keys(S.model.modal_cases).length && !S.results.modal_cases)
      S.results.modal_cases = mxMockModalCases(S.model, S.results);
    S.mxModalCase = "MODAL";
    syncModalTab(sky);
  });
  sky.modalx = { openMassSources: o => openMassSources(sky, o), openModalCases: o => openModalCases(sky, o), showCase: n => showCase(sky, n),
    rsFields: mxRsCaseFields, thFields: mxThCaseFields, thTools: mxThFunctionTools,
    refs: MXR, mock: { mxMockImport, mxMockGenerate, mxMockMatch, mxMockModalCases } };
}
