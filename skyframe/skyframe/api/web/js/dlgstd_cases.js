/* SkyFrame — standard (dlgkit) load-case / analysis dialogs, ETABS layout:

     Define › Load Cases…               every case type in one list; Linear
                                        Static, Modal, Response Spectrum, Time
                                        History and Buckling open the standard
                                        "Load Case Data" form; the specialised
                                        types (pushover, nonlinear static,
                                        staged, steady state, PSD, hyperstatic)
                                        open their existing editors
     Define › Load Combinations…        model.combos (all combo types)
     Define › Mass Source…              mass_options / mass_source / mode
     Define › Diaphragms…               model.diaphragms (+ model-wide default)
     Analyze › Analysis Options…        active DOF · modal · P-Delta · model

   Same rules as js/dlgstd_defs.js: lists edit a draft, sub-dialogs edit a
   copy of one item, only fields the user touches are written, OK commits
   the changed top-level keys (byte-identical when nothing changed). */

import * as dcME from "./modeledit.js";
import dcU from "./units.js";
import * as dcCX from "./combo_refs.js";
import { lcOpenDesignCombos as dcLcDesignCombos } from "./loadcodes.js";   // design combos by code (js/loadcodes.js)
import {
  el, esc, clone, isNum, dkForm, dkGroup, dkAdvanced, dkTabs, dkList, dkNote, dkDialog,
  dkDraft, dkCommit, uniqueName,
} from "./dlgkit.js";

const fnum = v => isNum(v) ? String(+v.toPrecision(6)) : "—";
const patOptions = D => Object.keys(D.patterns || {}).map(p => [p, p]);
const factorRows = obj => Object.entries(obj || {}).map(([k, f]) => ({ k, f }));
const rowsToObj = rows => Object.fromEntries(rows.map(r => [r.k, r.f]));
const nextFree = (rows, pool) => pool.find(p => !rows.some(r => r.k === p));
function thenLegacy(dlg, sky, D, fn) { dkCommit(sky, D); dlg.close(); setTimeout(fn, 0); }

/** pattern × factor table bound to `rows` (k = pattern, f = factor). */
function patternTable(F, D, rows, o = {}) {
  return F.table(rows, [
    { key: "k", label: o.keyLabel || "Load Pattern", type: "select", options: (r) => (o.pool ? o.pool() : patOptions(D)).filter(([p]) => p === r.k || !rows.some(x => x.k === p)) },
    { key: "f", label: "Scale Factor", type: "num" },
  ], {
    id: o.id, addLabel: o.addLabel || "+ Add pattern", minRows: o.minRows || 0, minMsg: o.minMsg,
    empty: o.empty || "No load patterns — click <b>Add</b>.",
    newRow: () => { const p = nextFree(rows, (o.pool ? o.pool() : patOptions(D)).map(x => x[0])); return p ? { k: p, f: 1 } : null; },
    canAdd: () => !!nextFree(rows, (o.pool ? o.pool() : patOptions(D)).map(x => x[0])),
    validateRow: (r, i) => !isNum(r.f) ? `Row ${i + 1}: the scale factor must be a number.` : "",
    validate: rs => new Set(rs.map(r => r.k)).size !== rs.length ? "A load pattern is listed twice — merge the rows." : "",
  });
}

/* ================================================================
   Load Cases list
   ================================================================ */
const ADD_TYPES = [
  ["static", "Linear Static"], ["rs", "Response Spectrum"], ["th", "Time History"], ["buckling", "Buckling"],
  ["pushover", "Nonlinear Static (Pushover)"], ["nonlinear_static", "Nonlinear Static"], ["staged", "Staged Construction"],
  ["steady_state", "Steady State"], ["psd", "Power Spectral Density"], ["hyperstatic", "Hyperstatic"],
];
const STD_KINDS = new Set(["static", "modal", "rs", "th", "buckling"]);
const DICT = { static: "cases", rs: "rs_cases", th: "th_cases", buckling: "buckling_cases" };
const RENAME = { static: dcME.renameCase, rs: dcME.renameRsCase, th: dcME.renameThCase, buckling: dcME.renameBucklingCase };

export function openLoadCases(sky, { addKind = "static", select } = {}) {
  const D = dkDraft(sky);
  let kind = addKind;
  const typeSel = el("select", { id: "dcCaseType", "aria-label": "Type of the new case" }, ADD_TYPES.map(([v, l]) => el("option", { value: v, selected: v === kind }, [l])));
  typeSel.addEventListener("change", () => { kind = typeSel.value; });
  const err = el("p", { class: "dk-err dk-side-err", role: "alert" });
  const notRun = n => Array.isArray(D.cases_not_run) && D.cases_not_run.includes(n);
  const kindOf = n => (dcME.allAnalysisCases(D).find(c => c.name === n) || {}).kind;
  const list = dkList({
    id: "dcCaseList",
    items: () => dcME.allAnalysisCases(D).map(c => ({ key: c.name, c })),
    columns: [
      { label: "Load Case", get: it => it.key },
      { label: "Type", get: it => it.c.type },
      { label: "Run", get: it => notRun(it.key) ? "Do not Run" : "Run" },
    ],
    rowCls: it => notRun(it.key) ? "is-muted" : "",
    selected: select || null,
    onOpen: k => modify(k),
    empty: "No load cases.",
    actions: [
      { sep: "Add" },
      { label: "Add New Case…", id: "dcCaseAdd", fn: () => add() },
      { label: "Add Copy of Case…", id: "dcCaseCopy", needsSel: true, disabled: k => !k || !DICT[kindOf(k)], fn: k => edit(kindOf(k), null, { copyOf: k }) },
      { sep: "Selected" },
      { label: "Modify/Show Case…", id: "dcCaseModify", needsSel: true, fn: k => modify(k) },
      { label: "Delete Case", id: "dcCaseDelete", needsSel: true, disabled: k => !k || kindOf(k) === "modal", fn: k => del(k) },
      { label: "Run / Do not Run", id: "dcCaseRunToggle", needsSel: true, fn: k => toggleRun(k) },
    ],
    extraSide: el("div", { class: "dk-list-side" }, [el("div", { class: "dk-side-title", text: "New case type" }), typeSel, err]),
  });
  const refresh = () => { list.render(); dlg && dlg.setNote(`${dcME.allAnalysisCases(D).length} load cases`); };
  const edit = (k, name, opts = {}) => {
    const done = nm => { list.select(nm); refresh(); };
    if (k === "static") return editStaticCase(sky, D, name, { ...opts, done });
    if (k === "modal") return editModalCase(sky, D, { done: () => refresh() });
    if (k === "rs") return editRsCase(sky, D, name, { ...opts, done });
    if (k === "th") return editThCase(sky, D, name, { ...opts, done });
    if (k === "buckling") return editBucklingCase(sky, D, name, { ...opts, done });
    return null;
  };
  const LEGACY = {
    pushover: n => n ? sky.openPushoverOptions && sky.openPushoverOptions(n) : (sky.etabs && sky.etabs.clickItem("def-pushover")),
    nonlinear_static: n => sky.openNlsCase && sky.openNlsCase(n || null),
    staged: () => sky.etabs && sky.etabs.gotoLoads && sky.etabs.gotoLoads("ls-staged"),
    steady_state: n => sky.openFreqCase && sky.openFreqCase("steady_state", n || null),
    psd: n => sky.openFreqCase && sky.openFreqCase("psd", n || null),
    hyperstatic: n => sky.openHyperstaticCase && sky.openHyperstaticCase(n || null),
  };
  const add = () => {
    err.textContent = "";
    if (STD_KINDS.has(kind)) return edit(kind, null);
    if (LEGACY[kind]) thenLegacy(dlg, sky, D, () => LEGACY[kind](null));
  };
  const modify = k => {
    if (!k) return;
    const kk = kindOf(k);
    if (STD_KINDS.has(kk)) return edit(kk, k);
    if (LEGACY[kk]) thenLegacy(dlg, sky, D, () => LEGACY[kk](k));
  };
  const del = k => {
    const kk = kindOf(k);
    let refs = [];
    if (kk === "static") refs = dcME.caseRefs(D, k);
    else if (kk === "rs") refs = [...dcCX.comboMemberRefs(D, k), ...Object.values(D.rs_combos || {}).filter(c => c.name_x === k || c.name_y === k).map(c => c.name)];
    else refs = dcCX.comboMemberRefs ? dcCX.comboMemberRefs(D, k) : [];
    if (refs.length) { err.textContent = `${k} is used by ${refs.slice(0, 3).join(", ")} — remove it there first.`; return; }
    err.textContent = "";
    const fn = { static: dcME.deleteCase, rs: dcME.deleteRsCase, th: dcME.deleteThCase, buckling: dcME.deleteBucklingCase,
      pushover: dcME.deletePushoverCase, staged: dcME.deleteStagedCase }[kk];
    if (fn) { fn(D, k); refresh(); return; }
    // other kinds keep their own delete paths (they update dependents)
    thenLegacy(dlg, sky, D, () => {
      const s = window.__sky;
      if (kk === "nonlinear_static" && s.deleteNlsCase) s.deleteNlsCase(k);
      else if (kk === "hyperstatic" && s.deleteHyperstaticCase) s.deleteHyperstaticCase(k);
      else if ((kk === "steady_state" || kk === "psd")) { const d = s.store.model[kk + "_cases"]; if (d) delete d[k]; }
      s.markDirty && s.markDirty();
      s.openLoadCases && s.openLoadCases();
    });
  };
  const toggleRun = k => {
    const cur = Array.isArray(D.cases_not_run) ? D.cases_not_run.slice() : [];
    D.cases_not_run = cur.includes(k) ? cur.filter(n => n !== k) : [...cur, k];
    refresh();
  };
  const dlg = dkDialog({
    id: "dcLoadCases", title: "Define Load Cases", iconId: "exleaf-cases", size: "lg",
    intro: "A load case says how loads are analysed: linear static, modal, response spectrum, time history … Pick a type under <b>New case type</b> and click <b>Add New Case</b>.",
    body: list,
    onOk: () => { dkCommit(sky, D); },
  });
  refresh();
  return dlg;
}

/* ---------------- Linear static ---------------- */
const GEOM = [["linear", "None (linear)"], ["pdelta", "P-Delta"], ["corotational", "Large displacement (corotational)"]];
export function editStaticCase(sky, D, name, { copyOf, done } = {}) {
  const src = name ? (D.cases || {})[name] : (copyOf ? (D.cases || {})[copyOf] : null);
  const isNew = !name;
  const nm0 = name || uniqueName(D.cases || {}, copyOf ? copyOf + "-copy" : "CASE1");
  const c = src ? clone(src) : { name: nm0, patterns: {}, pdelta: false, geometric: "linear" };
  if (!src) { const p = Object.keys(D.patterns || {})[0]; if (p) c.patterns[p] = 1; }
  const draftName = { v: nm0 };
  const rows = factorRows(c.patterns);
  const grows = factorRows(c.pdelta_gravity || {});
  const st = { ownGravity: c.pdelta_gravity == null };
  const F = dkForm();
  const taken = () => new Set(dcME.allAnalysisCases(D).map(x => x.name).concat(Object.keys(D.combos || {})));
  const tbl = patternTable(F, D, rows, { id: "dcStaticLoads", minRows: 1, minMsg: "Add at least one load pattern." });
  const gBody = el("div");
  const gtbl = patternTable(F, D, grows, { id: "dcStaticPdGrav", empty: "No gravity patterns." });
  const drawG = () => { gBody.classList.toggle("dk-disabled", st.ownGravity || dcME.effectiveGeometric(c) === "linear"); };
  const adv = dkAdvanced([
    dkGroup("P-Delta gravity state", [
      F.check(st, "ownGravity", { label: "Use this case's own loads as the P-Delta gravity state", id: "dcStaticOwnGrav", onChange: drawG }),
      gBody,
    ], { note: "Only used when the geometric nonlinearity is P-Delta: the gravity patterns below are applied first, then this case's loads." }),
  ], { summary: "Advanced — P-Delta gravity", countFn: () => (st.ownGravity ? 0 : 1) });
  gBody.appendChild(gtbl);
  drawG();
  F.onTouch = () => adv.refreshBadge();
  const body = [
    dkGroup("General", [
      F.text(draftName, "v", { label: "Load Case Name", id: "dcStaticName", validate: v => v !== name && taken().has(v) ? `“${v}” is already a case or combination name.` : "" }),
      F.readout("Load Case Type", () => "Linear Static"),
      F.select(c, "geometric", GEOM, { label: "Geometric Nonlinearity", id: "dcStaticGeom", read: () => dcME.effectiveGeometric(c),
        write: v => { c.geometric = v; c.pdelta = v === "pdelta"; }, onChange: drawG }),
    ]),
    dkGroup("Loads Applied", [tbl]),
    adv,
  ];
  return dkDialog({
    id: "dcStaticCase", title: "Load Case Data — Linear Static", iconId: "exleaf-cases", size: "md", form: F, body,
    onOk: () => {
      if (!F.touched && !isNew) { done && done(name); return; }
      const nn = draftName.v.trim();
      if (name && nn !== name) dcME.renameCase(D, name, nn);
      const out = { ...c, name: nn };
      if (F.tableDirty(tbl) || isNew) out.patterns = rowsToObj(rows);
      if (F.fields.find(f => f.key === "ownGravity" && f.dirty) || F.tableDirty(gtbl)) out.pdelta_gravity = st.ownGravity ? null : rowsToObj(grows);
      D.cases = D.cases || {};
      D.cases[nn] = out;
      done && done(nn);
    },
  });
}

/* ---------------- Modal ---------------- */
export function editModalCase(sky, D, { done } = {}) {
  const F = dkForm();
  const nls = Object.keys(D.nonlinear_static_cases || {});
  const st = { from: D.modal_from_case ?? "", n: D.num_modes ?? 12 };
  const body = [
    dkGroup("General", [
      F.readout("Load Case Name", () => dcME.MODAL_CASE || "MODAL"),
      F.readout("Modal Case Subtype", () => "Eigen"),
      F.num(st, "n", { label: "Maximum Number of Modes", int: true, min: 1, max: 500, id: "dcModalModes", def: 12 }),
    ]),
    dkAdvanced([
      F.select(st, "from", [["", "Zero initial conditions (unstressed)"], ...nls.map(n => [n, "End of nonlinear case " + n])], {
        label: "Stiffness to use", id: "dcModalFrom",
        hint: nls.length ? "Tangent stiffness at the end of a nonlinear static case (P-Delta / yielded state)." : "Define a nonlinear static case to start from its stiffness.",
      }),
    ], { summary: "Advanced — initial stiffness", countFn: () => st.from ? 1 : 0 }),
  ];
  return dkDialog({
    id: "dcModalCase", title: "Load Case Data — Modal", iconId: "exleaf-modal", size: "md", form: F, body,
    intro: "The modal case gives the periods and mode shapes used by response-spectrum and modal time-history cases.",
    onOk: () => {
      if (F.fields.some(f => f.key === "n" && f.dirty)) D.num_modes = st.n;
      if (F.fields.some(f => f.key === "from" && f.dirty)) { if (st.from) D.modal_from_case = st.from; else delete D.modal_from_case; }
      done && done();
    },
  });
}

/* ---------------- Response spectrum ---------------- */
const RS_METHODS = [["CQC", "CQC"], ["SRSS", "SRSS"], ["ABS", "Absolute"], ["GMC", "GMC (Gupta)"], ["NRC10", "NRC 10 percent"], ["DSC", "Double Sum"]];
export function editRsCase(sky, D, name, { copyOf, done } = {}) {
  const src = name ? (D.rs_cases || {})[name] : (copyOf ? (D.rs_cases || {})[copyOf] : null);
  const isNew = !name;
  const nm0 = name || uniqueName(D.rs_cases || {}, copyOf ? copyOf + "-copy" : "RS1");
  const fns = Object.keys(D.spectrum_functions || {});
  const c = src ? clone(src) : { name: nm0, direction: "X", spectrum: dcME.ubcSpectrum(), combo_method: "CQC", damping: 0.05, scale: 1.0, function: fns[0] || "" };
  const draftName = { v: nm0 };
  const F = dkForm();
  const taken = () => new Set(dcME.allAnalysisCases(D).map(x => x.name).concat(Object.keys(D.combos || {})));
  const advKeys = [["gmc_f1", 1], ["gmc_f2", 33], ["dsc_td", 20], ["rigid_response", false], ["include_missing_mass", false], ["num_modes", 0]];
  const adv = dkAdvanced([
    F.num(c, "num_modes", { label: "Modes used", int: true, min: 0, def: 0, read: () => c.num_modes ?? 0, id: "dcRsModes", hint: "0 = every computed mode" }),
    F.num(c, "gmc_f1", { label: "GMC f1", kind: "frequency", gt: 0, def: 1, read: () => c.gmc_f1 ?? 1, id: "dcRsF1" }),
    F.num(c, "gmc_f2", { label: "GMC f2", kind: "frequency", gt: 0, def: 33, read: () => c.gmc_f2 ?? 33, id: "dcRsF2" }),
    F.num(c, "dsc_td", { label: "Double-sum duration td", kind: "period", gt: 0, def: 20, read: () => c.dsc_td ?? 20, id: "dcRsTd" }),
    F.check(c, "rigid_response", { label: "Include rigid response (GMC)", id: "dcRsRigid" }),
    F.check(c, "include_missing_mass", { label: "Include missing-mass correction", id: "dcRsMissing" }),
  ], { summary: "Advanced — modal combination", countFn: () => advKeys.filter(([k, d]) => (c[k] ?? d) !== d).length });
  F.onTouch = () => adv.refreshBadge();
  const body = [
    dkGroup("General", [
      F.text(draftName, "v", { label: "Load Case Name", id: "dcRsName", validate: v => v !== name && taken().has(v) ? `“${v}” is already a case or combination name.` : "" }),
      F.readout("Load Case Type", () => "Response Spectrum"),
    ]),
    dkGroup("Loads Applied", [
      F.select(c, "direction", [["X", "U1 (global X)"], ["Y", "U2 (global Y)"]], { label: "Load Direction", id: "dcRsDir" }),
      F.select(c, "function", [["", "(inline spectrum)"], ...fns.map(f => [f, f])], { label: "Function", id: "dcRsFn",
        hint: fns.length ? "" : "No spectrum functions yet — Define › Functions, or keep the inline spectrum." }),
      F.num(c, "scale", { label: "Scale Factor", id: "dcRsScale", hint: "multiplies the spectrum (Sa is in g)" }),
    ]),
    dkGroup("Other Parameters", [
      F.select(c, "combo_method", RS_METHODS, { label: "Modal Combination Method", id: "dcRsMethod" }),
      F.num(c, "damping", { label: "Modal Damping Ratio", min: 0, lt: 1, id: "dcRsDamp" }),
    ]),
    adv,
  ];
  return dkDialog({
    id: "dcRsCase", title: "Load Case Data — Response Spectrum", iconId: "exleaf-cases", size: "md", form: F, body,
    onOk: () => {
      if (!F.touched && !isNew) { done && done(name); return; }
      const nn = draftName.v.trim();
      if (name && nn !== name) dcME.renameRsCase(D, name, nn);
      D.rs_cases = D.rs_cases || {};
      D.rs_cases[nn] = { ...c, name: nn };
      done && done(nn);
    },
  });
}

/* ---------------- Time history ---------------- */
export function editThCase(sky, D, name, { copyOf, done } = {}) {
  const src = name ? (D.th_cases || {})[name] : (copyOf ? (D.th_cases || {})[copyOf] : null);
  const isNew = !name;
  const nm0 = name || uniqueName(D.th_cases || {}, copyOf ? copyOf + "-copy" : "TH1");
  const fns = Object.keys(D.th_functions || {});
  const c = src ? clone(src) : { name: nm0, direction: "X", accel: fns.length ? [] : dcME.sineRecord(), dt: 0.02, damping: 0.05, scale: 1.0, function: fns[0] || "", damping_model: "rayleigh", modal_zeta: null };
  const draftName = { v: nm0 };
  const F = dkForm();
  const grows = factorRows(c.gravity || {});
  const taken = () => new Set(dcME.allAnalysisCases(D).map(x => x.name).concat(Object.keys(D.combos || {})));
  const nlBox = el("div");
  const gtbl = patternTable(F, D, grows, { id: "dcThGrav", empty: "No gravity preload." });
  nlBox.append(
    F.select(c, "hinges", [["column_base", "Column bases"], ["all_ends", "All member ends"]], { label: "Hinge locations", id: "dcThHinges", read: () => c.hinges || "column_base" }),
    F.num(c, "default_My", { label: "Default yield moment", kind: "moment", gt: 0, allowNull: true, nullText: "from section", def: null, id: "dcThMy" }),
    F.num(c, "hardening", { label: "Post-yield hardening ratio", min: 0, lt: 1, def: 0.02, read: () => c.hardening ?? 0.02, id: "dcThHard" }),
    el("div", { class: "dk-label", text: "Gravity preload (applied first)" }), gtbl);
  const syncNl = () => nlBox.classList.toggle("dk-disabled", !c.nonlinear);
  const adv = dkAdvanced([
    F.select(c, "damping_model", [["rayleigh", "Rayleigh (mass + stiffness)"], ["modal", "Modal"]], { label: "Damping model", id: "dcThDampModel", read: () => c.damping_model || "rayleigh" }),
    F.check(c, "nonlinear", { label: "Nonlinear (lumped plastic hinges)", id: "dcThNl", onChange: syncNl }),
    nlBox,
    dkNote("Integration scheme, damping overrides and multi-component load data: select the case in <b>Define Load Cases</b> › Modify in the Loads editor."),
  ], { summary: "Advanced — damping model and nonlinearity", countFn: () => ((c.damping_model || "rayleigh") !== "rayleigh" ? 1 : 0) + (c.nonlinear ? 1 : 0) });
  syncNl();
  F.onTouch = () => adv.refreshBadge();
  const recBox = el("div");
  const syncRec = () => recBox.classList.toggle("dk-disabled", !!c.function);
  recBox.appendChild(F.num(c, "dt", { label: "Record time step, Δt", kind: "period", gt: 0, id: "dcThDt", hint: `${(c.accel || []).length} inline samples` }));
  const body = [
    dkGroup("General", [
      F.text(draftName, "v", { label: "Load Case Name", id: "dcThName", validate: v => v !== name && taken().has(v) ? `“${v}” is already a case or combination name.` : "" }),
      F.readout("Load Case Type", () => c.nonlinear ? "Time History — Nonlinear" : "Time History — Linear"),
    ]),
    dkGroup("Loads Applied", [
      F.select(c, "direction", [["X", "U1 (global X)"], ["Y", "U2 (global Y)"]], { label: "Load Direction", id: "dcThDir" }),
      F.select(c, "function", [["", "(inline record)"], ...fns.map(f => [f, f])], { label: "Function", id: "dcThFn", onChange: syncRec }),
      recBox,
      F.num(c, "scale", { label: "Scale Factor", id: "dcThScale" }),
    ]),
    dkGroup("Other Parameters", [F.num(c, "damping", { label: "Damping Ratio", min: 0, lt: 1, id: "dcThDamp" })]),
    adv,
  ];
  syncRec();
  return dkDialog({
    id: "dcThCase", title: "Load Case Data — Time History", iconId: "exleaf-th", size: "md", form: F, body,
    onOk: () => {
      if (!F.touched && !isNew) { done && done(name); return; }
      const nn = draftName.v.trim();
      if (name && nn !== name) dcME.renameThCase(D, name, nn);
      const out = { ...c, name: nn };
      if (F.tableDirty(gtbl)) out.gravity = rowsToObj(grows);
      D.th_cases = D.th_cases || {};
      D.th_cases[nn] = out;
      done && done(nn);
    },
  });
}

/* ---------------- Buckling ---------------- */
export function editBucklingCase(sky, D, name, { copyOf, done } = {}) {
  const src = name ? (D.buckling_cases || {})[name] : (copyOf ? (D.buckling_cases || {})[copyOf] : null);
  const isNew = !name;
  const nm0 = name || uniqueName(D.buckling_cases || {}, copyOf ? copyOf + "-copy" : "BUCK1");
  const c = src ? clone(src) : { name: nm0, gravity: (D.patterns && D.patterns.DEAD) ? { DEAD: 1 } : {}, num_modes: 3, base_case: null };
  const draftName = { v: nm0 };
  const rows = factorRows(c.gravity);
  const F = dkForm();
  const taken = () => new Set(dcME.allAnalysisCases(D).map(x => x.name).concat(Object.keys(D.combos || {})));
  const tbl = patternTable(F, D, rows, { id: "dcBuckLoads", minRows: 1, minMsg: "Add at least one gravity pattern." });
  const body = [
    dkGroup("General", [
      F.text(draftName, "v", { label: "Load Case Name", id: "dcBuckName", validate: v => v !== name && taken().has(v) ? `“${v}” is already a case or combination name.` : "" }),
      F.readout("Load Case Type", () => "Buckling"),
      F.num(c, "num_modes", { label: "Number of Buckling Modes", int: true, min: 1, max: 100, id: "dcBuckModes" }),
    ]),
    dkGroup("Loads Applied", [tbl]),
    dkAdvanced([
      F.select(c, "base_case", [["", "None — unstressed"], ...Object.keys(D.cases || {}).map(n => [n, "Stressed state of " + n])], {
        label: "Initial state", id: "dcBuckBase", read: () => c.base_case || "", write: v => { c.base_case = v || null; } }),
    ], { summary: "Advanced — initial state", countFn: () => c.base_case ? 1 : 0 }),
  ];
  return dkDialog({
    id: "dcBuckCase", title: "Load Case Data — Buckling", iconId: "exleaf-buckling", size: "md", form: F, body,
    onOk: () => {
      if (!F.touched && !isNew) { done && done(name); return; }
      const nn = draftName.v.trim();
      if (name && nn !== name) dcME.renameBucklingCase(D, name, nn);
      const out = { ...c, name: nn };
      if (F.tableDirty(tbl) || isNew) out.gravity = rowsToObj(rows);
      D.buckling_cases = D.buckling_cases || {};
      D.buckling_cases[nn] = out;
      done && done(nn);
    },
  });
}

/* ================================================================
   Load combinations
   ================================================================ */
const KIND_TAG = { static: "", rs: " (RS)", rs_combo: " (RS dir)", th: " (TH)", staged: " (staged)", combo: " (combo)" };
const comboFormula = (m, cb) => {
  const parts = Object.entries(cb.cases || {}).map(([n, f]) => `${fnum(f)}·${n}${KIND_TAG[dcCX.memberKind(m, n)] || ""}`);
  return parts.length ? parts.join(" + ") : "(no members)";
};

export function openCombinations(sky, { select } = {}) {
  const D = dkDraft(sky);
  const had = "combos" in D;
  D.combos = D.combos || {};
  const err = el("p", { class: "dk-err dk-side-err", role: "alert" });
  const list = dkList({
    id: "dcComboList",
    items: () => Object.entries(D.combos).map(([k, cb]) => ({ key: k, cb })),
    columns: [
      { label: "Combination", get: it => it.key },
      { label: "Type", get: it => dcCX.COMBO_TYPE_LABEL[dcCX.normComboType(it.cb.combo_type)] },
      { label: "Definition", get: it => comboFormula(D, it.cb), cls: "dk-mono" },
    ],
    selected: select || null,
    onOpen: k => edit(k),
    empty: "No load combinations yet.",
    actions: [
      { sep: "Add" },
      { label: "Add New Combo…", id: "dcComboAdd", fn: () => edit(null) },
      { label: "Add Copy of Combo…", id: "dcComboCopy", needsSel: true, fn: k => edit(null, k) },
      { label: "Add Default Design Combos…", id: "dcComboAuto", title: "ASCE 7 strength combinations from the patterns (applies these edits first)",
        fn: () => thenLegacy(dlg, sky, D, () => sky.etabs && sky.etabs.gotoLoads && sky.etabs.gotoLoads("ls-codetools")) },
      { label: "Add Design Combos by Code…", id: "dcComboCode", title: "EN 1990 · IS 456 / 875-5 · IS 800 · NBCC 2020 · AS/NZS 1170.0", fn: () => dcLcDesignCombos(sky, D, { done: () => refresh() }) },   // js/loadcodes.js
      { sep: "Selected" },
      { label: "Modify/Show Combo…", id: "dcComboModify", needsSel: true, fn: k => edit(k) },
      { label: "Delete Combo", id: "dcComboDelete", needsSel: true, fn: k => {
        const refs = dcCX.comboMemberRefs(D, k).filter(n => n !== k);
        if (refs.length) { err.textContent = `${k} is used by ${refs.join(", ")}.`; return; }
        err.textContent = ""; dcME.deleteCombo(D, k); refresh(); } },
    ],
    extraSide: err,
  });
  const refresh = () => { list.render(); dlg && dlg.setNote(`${Object.keys(D.combos).length} combinations`); };
  const edit = (name, copyOf) => editCombo(sky, D, name, { copyOf, done: nm => { list.select(nm); refresh(); } });
  const dlg = dkDialog({
    id: "dcCombos", title: "Define Load Combinations", iconId: "exleaf-combos", size: "lg",
    intro: "Combinations add or envelope the results of load cases (and other combinations) with scale factors.",
    body: list,
    onOk: () => { if (!had && !Object.keys(D.combos).length) delete D.combos; dkCommit(sky, D); },
  });
  refresh();
  return dlg;
}

export function editCombo(sky, D, name, { copyOf, done } = {}) {
  const src = name ? D.combos[name] : (copyOf ? D.combos[copyOf] : null);
  const isNew = !name;
  const taken = () => new Set(dcME.allAnalysisCases(D).map(x => x.name).concat(Object.keys(D.combos || {})));
  const nm0 = name || uniqueName(taken(), copyOf ? copyOf + "-copy" : "COMB1");
  const c = src ? clone(src) : { name: nm0, combo_type: "add", cases: {} };
  const draftName = { v: nm0 };
  const rows = factorRows(c.cases);
  const F = dkForm();
  const pool = () => dcCX.comboMemberPool(D, name || draftName.v).map(p => [p.name, p.name + (KIND_TAG[p.kind] || "")]);
  const tbl = patternTable(F, D, rows, { id: "dcComboRows", keyLabel: "Load Case / Combo", addLabel: "+ Add", pool, minRows: 1, minMsg: "Add at least one load case.",
    empty: "No members yet — click <b>Add</b>." });
  const note = el("p", { class: "muted dk-note", id: "dcComboTip" });
  const TIP = {
    add: "Linear Add — factored sum of the member results.", envelope: "Envelope — max and min over the factored members.",
    abs: "Absolute Add — Σ|member|.", srss: "SRSS — √Σ member².", range: "Range Add — max = Σ positive parts, min = Σ negative parts.",
  };
  const tip = () => { note.textContent = TIP[dcCX.normComboType(c.combo_type)] || ""; };
  const body = [
    dkGroup("Load Combination Data", [
      F.text(draftName, "v", { label: "Load Combination Name", id: "dcComboName", validate: v => v !== name && taken().has(v) ? `“${v}” is already a case or combination name.` : "" }),
      F.select(c, "combo_type", dcCX.COMBO_TYPES.map(t => [t, dcCX.COMBO_TYPE_LABEL[t]]), { label: "Combination Type", id: "dcComboType", read: () => dcCX.normComboType(c.combo_type), onChange: tip }),
      note,
    ]),
    dkGroup("Define Combination of Load Case / Combo Results", [tbl,
      dkNote("Pushover, buckling, steady-state, PSD and modal cases cannot be combined.")]),
  ];
  tip();
  return dkDialog({
    id: "dcComboData", title: "Load Combination Data", iconId: "exleaf-combos", size: "md", form: F, body,
    onOk: () => {
      if (!F.touched && !isNew) { done && done(name); return; }
      const nn = draftName.v.trim();
      const out = { ...c, name: nn };
      if (F.tableDirty(tbl) || isNew) out.cases = rowsToObj(rows);
      const view = { ...D, combos: { ...D.combos } };
      if (name && nn !== name) delete view.combos[name];
      view.combos[nn] = out;
      const bad = dcCX.validateCombo(view, nn, out);
      if (bad) return bad;
      if (name && nn !== name) dcME.renameCombo(D, name, nn);
      D.combos[nn] = out;
      done && done(nn);
    },
  });
}

/* ================================================================
   Mass source
   ================================================================ */
export function openMassSource(sky) {
  const D = dkDraft(sky);
  const F = dkForm();
  const opts = dcME.normalizeMassOptions(D.mass_options);
  const writeOpt = k => v => { opts[k] = v; D.mass_options = { ...dcME.normalizeMassOptions(D.mass_options), [k]: v }; };
  const mode = { v: D.mass_source_mode === "element_self_mass" ? "element_self_mass" : "weight" };
  const rows = factorRows(D.mass_source);
  const tblBox = el("div");
  const tbl = patternTable(F, D, rows, { id: "dcMassRows", empty: "No load patterns in the mass source.", addLabel: "+ Add pattern" });
  tblBox.appendChild(tbl);
  const sync = () => { tblBox.classList.toggle("dk-disabled", !opts.patterns); };
  const body = [
    dkGroup("Mass Source", [
      F.check(opts, "self_mass", { label: "Element self mass and additional mass", id: "dcMassSelf", write: writeOpt("self_mass") }),
      F.select(mode, "v", [["weight", "Weight / g"], ["element_self_mass", "Material mass density"]], { label: "Self mass from", id: "dcMassMode",
        write: v => { mode.v = v; D.mass_source_mode = v; } }),
      F.check(opts, "patterns", { label: "Specified load patterns (load × multiplier / g)", id: "dcMassPats", write: writeOpt("patterns"), onChange: sync }),
      tblBox,
    ]),
    dkGroup("Mass Options", [
      F.check(opts, "include_lateral", { label: "Include lateral mass (X, Y)", id: "dcMassLat", write: writeOpt("include_lateral") }),
      F.check(opts, "include_vertical", { label: "Include vertical mass (Z)", id: "dcMassVert", write: writeOpt("include_vertical") }),
      F.check(opts, "lump_at_stories", { label: "Lump lateral mass at story levels", id: "dcMassLump", write: writeOpt("lump_at_stories") }),
    ]),
  ];
  sync();
  return dkDialog({
    id: "dcMassSource", title: "Mass Source Data", iconId: "mass-source", size: "md", form: F, body,
    intro: "Mass drives the modal, response-spectrum and time-history analyses.",
    onOk: () => {
      if (!opts.include_lateral && !opts.include_vertical) return "Include at least one of lateral or vertical mass.";
      if (!opts.self_mass && !opts.patterns) return "Choose at least one mass source (self mass or load patterns).";
      if (opts.patterns && !rows.length && F.tableDirty(tbl)) return "Add at least one load pattern, or untick Specified load patterns.";
      if (F.tableDirty(tbl)) D.mass_source = rowsToObj(rows);
      dkCommit(sky, D);
    },
  });
}

/* ================================================================
   Diaphragms
   ================================================================ */
const DIA_TYPES = [["rigid", "Rigid"], ["semi_rigid", "Semi-rigid"]];
export function openDiaphragmsStd(sky, { select } = {}) {
  const D = dkDraft(sky);
  const had = "diaphragms" in D;
  const dias = () => D.diaphragms || {};
  const use = n => (D.joint_diaphragms || []).filter(e => e.diaphragm === n).length + (D.shells || []).filter(s => s.diaphragm === n).length;
  const err = el("p", { class: "dk-err dk-side-err", role: "alert" });
  const F = dkForm();
  const list = dkList({
    id: "dcDiaList",
    items: () => Object.entries(dias()).map(([k, v]) => ({ key: k, v })),
    columns: [{ label: "Diaphragm", get: it => it.key }, { label: "Rigidity", get: it => (DIA_TYPES.find(t => t[0] === it.v.type) || [0, it.v.type])[1] },
      { label: "Assigned objects", get: it => use(it.key) || "—" }],
    selected: select || null,
    onOpen: k => edit(k),
    empty: "No named diaphragms — every story uses the model-wide option below.",
    actions: [
      { label: "Add New Diaphragm…", id: "dcDiaAdd", fn: () => edit(null) },
      { label: "Modify/Show Diaphragm…", id: "dcDiaModify", needsSel: true, fn: k => edit(k) },
      { label: "Delete Diaphragm", id: "dcDiaDelete", needsSel: true, fn: k => {
        const n = use(k);
        if (n) { err.textContent = `${k} is assigned to ${n} object${n > 1 ? "s" : ""} — assign “None” first.`; return; }
        err.textContent = ""; delete D.diaphragms[k]; if (!Object.keys(D.diaphragms).length) delete D.diaphragms; list.render(); } },
    ],
    extraSide: err,
  });
  const edit = name => {
    const F2 = dkForm();
    const cur = { name: name || uniqueName(dias(), "D1"), type: name ? (dias()[name].type || "rigid") : "rigid" };
    return dkDialog({
      id: "dcDiaData", title: "Diaphragm Data", iconId: "exleaf-stories", size: "sm", form: F2,
      body: [dkGroup("Diaphragm", [
        F2.text(cur, "name", { label: "Diaphragm Name", id: "dcDiaName", validate: v => v !== name && dias()[v] ? `“${v}” already exists.` : "" }),
        F2.radio(cur, "type", [["rigid", "Rigid", "one master node per story"], ["semi_rigid", "Semi-rigid", "the slab carries in-plane stiffness"]], { label: "Rigidity", idPrefix: "dcDiaType_" }),
      ])],
      onOk: () => {
        if (!F2.touched && name) return;
        D.diaphragms = { ...dias() };
        if (name && cur.name !== name) {
          const next = {};
          for (const [k, v] of Object.entries(D.diaphragms)) next[k === name ? cur.name : k] = v;
          D.diaphragms = next;
          for (const e of D.joint_diaphragms || []) if (e.diaphragm === name) e.diaphragm = cur.name;
          for (const s of D.shells || []) if (s.diaphragm === name) s.diaphragm = cur.name;
        }
        D.diaphragms[cur.name] = { ...(D.diaphragms[cur.name] || {}), type: cur.type };
        list.select(cur.name);
      },
    });
  };
  const body = [
    list,
    dkGroup("Model-wide default (stories without named diaphragms)", [
      F.select(D, "diaphragm", [["rigid", "Rigid"], ["none", "None (semi-rigid / flexible slabs)"]], { label: "Default diaphragm", id: "dcDiaDefault", read: () => D.diaphragm === "none" ? "none" : "rigid" }),
    ]),
  ];
  return dkDialog({
    id: "dcDiaphragms", title: "Define Diaphragms", iconId: "exleaf-stories", size: "lg", form: F, body,
    intro: "Named diaphragms are assigned to joints or slabs (Assign › Joint / Shell › Diaphragm). Rigid diaphragms tie each story's floor into one in-plane body.",
    onOk: () => { if (!had && D.diaphragms && !Object.keys(D.diaphragms).length) delete D.diaphragms; dkCommit(sky, D); },
  });
}

/* ================================================================
   Analysis Options (DOF · modal · P-Delta · model)
   ================================================================ */
export function openAnalysisOptions(sky, { tab = "dof" } = {}) {
  const D = dkDraft(sky);
  const F = dkForm();
  /* ---- DOF */
  const dof = new Set(dcME.normalizeActiveDof(D.active_dof));
  const dofSt = { preset: dcME.dofPresetOf([...dof]) || "" };
  const writeDof = () => { D.active_dof = dcME.DOF_NAMES.filter(d => dof.has(d)); };
  const dofChecks = el("div", { class: "dk-group dk-cols", style: "border:0;padding:0" });
  const drawDof = () => {
    F.forget(dofChecks); dofChecks.textContent = "";
    for (const d of dcME.DOF_NAMES) dofChecks.appendChild(F.check(null, d, { label: d, id: "dcDof_" + d, read: () => dof.has(d),
      write: v => { if (v) dof.add(d); else dof.delete(d); writeDof(); dofSt.preset = dcME.dofPresetOf([...dof]) || ""; presetF.render(); } }));
  };
  const presetRow = F.radio(dofSt, "preset", Object.entries(dcME.DOF_PRESETS).map(([k, p]) => [k, p.label, p.dofs.join(" ")]), {
    label: "Fast DOFs", idPrefix: "dcDofPreset_",
    write: v => { dofSt.preset = v; dof.clear(); for (const d of dcME.DOF_PRESETS[v].dofs) dof.add(d); writeDof(); drawDof(); } });
  const presetF = F.fields[F.fields.length - 1];
  drawDof();
  const dofPanel = el("div", {}, [dkGroup("Active Degrees of Freedom", [presetRow, dofChecks],
    { note: "A plane frame (XZ) analyses only UX, UZ and RY — faster and free of out-of-plane mechanisms." })]);
  /* ---- Modal */
  const nls = Object.keys(D.nonlinear_static_cases || {});
  const modalPanel = el("div", {}, [dkGroup("Modal Case", [
    F.num(D, "num_modes", { label: "Maximum Number of Modes", int: true, min: 1, max: 500, def: 12, read: () => D.num_modes ?? 12, id: "dcOptModes" }),
    nls.length ? F.select({ v: D.modal_from_case ?? "" }, "v", [["", "Zero initial conditions"], ...nls.map(n => [n, "End of " + n])], {
      label: "Stiffness to use", id: "dcOptModalFrom", write: v => { if (v) D.modal_from_case = v; else delete D.modal_from_case; } }) : null,
  ])]);
  /* ---- P-Delta */
  const po = dcCX.pdeltaOptions(D);
  const pRows = factorRows(po.load_factors);
  const pdTouched = { v: false };
  const pdBox = el("div");
  const pdTbl = patternTable(F, D, pRows, { id: "dcPdRows", empty: "No load patterns." });
  pdBox.append(pdTbl,
    F.num(po, "max_iterations", { label: "Maximum iterations", int: true, min: 1, max: 100, def: 2, id: "dcPdIter", onChange: () => { pdTouched.v = true; } }),
    F.num(po, "tolerance", { label: "Relative tolerance", gt: 0, def: 0.001, id: "dcPdTol", onChange: () => { pdTouched.v = true; } }));
  const syncPd = () => pdBox.classList.toggle("dk-disabled", po.method !== "iterative_loads");
  const pdPanel = el("div", {}, [dkGroup("P-Delta Options", [
    F.radio(po, "method", ["none", "non_iterative_mass", "iterative_loads"].map(v => [v, dcCX.PDELTA_METHOD_LABEL[v]]), { label: "Method", idPrefix: "dcPd_",
      onChange: () => { pdTouched.v = true; syncPd(); } }),
    pdBox,
  ], { note: "Model-wide geometric stiffness for every linear analysis. Static cases with their own P-Delta setting keep it." })]);
  syncPd();
  /* ---- Model */
  const modelPanel = el("div", {}, [
    dkGroup("Model Behaviour", [
      F.select(D, "diaphragm", [["rigid", "Rigid"], ["none", "None (semi-rigid)"]], { label: "Floor diaphragm", id: "dcOptDia", read: () => D.diaphragm === "none" ? "none" : "rigid" }),
      F.select(D, "panel_zones", [["none", "Centerline (none)"], ["rigid", "Rigid"], ["scissors", "Flexible (scissors)"]], { label: "Beam-column panel zones", id: "dcOptPz", read: () => D.panel_zones || "none" }),
      F.select(D, "base_fixity", [["fixed", "Fixed"], ["pinned", "Pinned"]], { label: "Automatic base supports", id: "dcOptBase", read: () => D.base_fixity || "fixed",
        hint: "Applies to the lowest joints when no explicit supports are assigned." }),
    ]),
    dkAdvanced([
      F.check(D, "edge_constraints", { label: "Auto edge constraints (mismatched shell meshes)", id: "dcOptEdge" }),
      F.num(D, "thermal_alpha", { label: "Default thermal coefficient α", kind: "thermal_coeff", gt: 0, def: 1.2e-5, read: () => D.thermal_alpha ?? 1.2e-5, id: "dcOptAlpha" }),
      F.num(D, "deflection_limit", { label: "Deflection limit L /", gt: 0, def: 360, read: () => D.deflection_limit ?? 360, id: "dcOptDefl", hint: "serviceability check span ratio" }),
    ], { countFn: () => (D.edge_constraints ? 1 : 0) + ((D.thermal_alpha ?? 1.2e-5) !== 1.2e-5 ? 1 : 0) + ((D.deflection_limit ?? 360) !== 360 ? 1 : 0) }),
  ]);
  const tabs = dkTabs([
    { id: "dof", label: "Degrees of Freedom", body: dofPanel },
    { id: "modal", label: "Modal", body: modalPanel },
    { id: "pdelta", label: "P-Delta", body: pdPanel },
    { id: "model", label: "Model", body: modelPanel },
  ], { active: tab, id: "dcOptTabs" });
  const more = el("button", { type: "button", class: "btn btn-small dk-legacy", id: "dcOptCasesRun", title: "Pick which cases the next run analyses (applies these edits first)" }, ["Load Cases to Run…"]);
  const dlg = dkDialog({
    id: "dcAnalysisOptions", title: "Analysis Options", iconId: "active-dof", size: "md", form: F, tabs, body: tabs, extra: [more],
    onOk: () => {
      if (!dof.size) return "Keep at least one active degree of freedom.";
      if (pdTouched.v || F.tableDirty(pdTbl)) {
        const out = { method: po.method, load_factors: po.method === "iterative_loads" ? rowsToObj(pRows) : {},
          max_iterations: po.max_iterations, tolerance: po.tolerance, include_in: po.include_in };
        if (out.method === "iterative_loads" && !pRows.length) return "Iterative P-Delta needs at least one load pattern.";
        if (dcCX.pdeltaIsDefault(out)) delete D.pdelta_options; else D.pdelta_options = out;
      }
      dkCommit(sky, D);
    },
  });
  more.addEventListener("click", () => { dkCommit(sky, D); dlg.close(); setTimeout(() => sky.openCasesToRun && sky.openCasesToRun(), 0); });
  return dlg;
}
