/* SkyFrame — ETABS "Nonlinear Static" load cases + case chaining (analysis
   only). CONTRACT "Nonlinear static load cases and case chaining".

     Define → Load Cases → Add "Nonlinear Static"   model.nonlinear_static_cases
     Define → Modal Case…                           model.modal_from_case
                                                    ("Use stiffness at end of
                                                    nonlinear case")
     Pushover → Load Application → start_from      also offers NLS cases (hook
                                                    in casedlg.js)
     Display → Nonlinear Static Results…           results.nonlinear_static
     Story tab card (case "nl:<name>")              history plot, hinges, warnings

   Every dialog edits a DRAFT and writes the model only on OK. A case is
   written with the exact key set / order of NonlinearStaticCase.to_dict()
   (the backend always emits every field), and `nonlinear_static_cases` /
   `modal_from_case` are removed when unused — so a model that does not use
   the feature, or a dialog closed with OK and no edit, round-trips
   byte-identically through POST /api/model. Numbers go through js/units.js;
   the model stays SI. */

import * as ME from "./modeledit.js";
import U from "./units.js";
import { lineChart as nlsLineChart } from "./casedlg.js";
import {
  esc, clone, isNum, dialog, closeDialog, btn, footBar, errorLine, showError,
  group, el, numInput, row, select, checkbox, radio, storyNames, modelJoints,
  snapToJoint, ptLabel, storyAtZ, makeCtx, ensureCss, svgIcon,
} from "./nls_ui.js";

/* ================================================================
   model helpers
   ================================================================ */
export const NLS_KEY_ORDER = ["name", "loads", "load_application", "steps", "control_point",
  "control_story", "control_dof", "target_disp", "geometric", "start_from", "hinges", "My",
  "default_My", "hardening", "hinge_params"];
export const NLS_DEFAULTS = {
  load_application: "load_control", steps: 10, control_point: null, control_story: null,
  control_dof: "UX", target_disp: null, geometric: "none", start_from: null,
  hinges: "column_base", My: {}, default_My: null, hardening: 0.02, hinge_params: {},
};
const DOFS = ["UX", "UY", "UZ", "RX", "RY", "RZ"];
const GEOM_LABEL = { none: "None", p_delta: "P-Delta", large_displacement: "P-Delta + Large Displacements" };
const GEOM_TO_PO = { none: "linear", p_delta: "pdelta", large_displacement: "corotational" };
const HINGE_LABEL = {
  column_base: "Column bases (Steel01 springs, My)",
  all_ends: "All member ends (Steel01 springs, My)",
  asce41: "ASCE 41 member-assigned (auto M3 / fiber PMM)",
};
const HP_KEYS = [["expected_factor", "Expected strength factor", "none", 1.1],
  ["rho", "ρ (tension steel ratio)", "none", 0.01], ["rho_prime", "ρ′ (compression steel ratio)", "none", 0.0],
  ["fy_bar", "f̄y (rebar yield)", "stress", 420000]];
export const dofKind = d => (String(d || "UX")[0] === "R" ? "rotation" : "disp");
const BASE_COMP = { UX: "FX", UY: "FY", UZ: "FZ", RX: "MX", RY: "MY", RZ: "MZ" };

export const nlsCases = m => (m && m.nonlinear_static_cases) || {};
export const nlsNames = m => Object.keys(nlsCases(m));

/** Canonical case object — exactly NonlinearStaticCase.to_dict() order. */
export function canonicalCase(c, name) {
  const out = {};
  for (const k of NLS_KEY_ORDER) {
    if (k === "name") out.name = name ?? c.name;
    else if (k === "loads") out.loads = (c.loads || []).map(l => ({ pattern: String(l.pattern), scale: +l.scale }));
    else out[k] = c[k] === undefined ? clone(NLS_DEFAULTS[k]) : clone(c[k]);
  }
  return out;
}

/** start_from chain [root … name] in `cases`; throws on a cycle / unknown. */
export function chainOf(cases, name) {
  const chain = [];
  let cur = name;
  while (cur != null && cur !== "") {
    if (!cases[cur]) throw new Error(`start_from references unknown nonlinear static case ${cur}`);
    if (chain.includes(cur)) {
      const cyc = chain.slice(chain.indexOf(cur)).concat([cur]).reverse();
      throw new Error(`circular start_from reference ${cyc.join(" -> ")}`);
    }
    chain.push(cur);
    cur = cases[cur].start_from;
  }
  return chain.reverse();
}
/** Would `name` starting from `from` close a cycle? */
export function wouldCycle(cases, name, from) {
  let cur = from;
  const seen = new Set();
  while (cur) {
    if (cur === name) return true;
    if (seen.has(cur) || !cases[cur]) return false;
    seen.add(cur);
    cur = cases[cur].start_from;
  }
  return false;
}

function takenCaseNames(m, except) {
  const s = new Set(ME.allAnalysisCases(m).map(c => c.name));
  for (const n of Object.keys(m.combos || {})) s.add(n);
  s.add(ME.MODAL_CASE || "MODAL");
  s.delete(except);
  return s;
}
function uniqueName(taken, base) {
  let i = 1;
  while (taken.has(`${base}${i}`)) i++;
  return `${base}${i}`;
}

/** Names referencing NLS case `name` (start_from / pushover / modal). */
export function nlsRefs(m, name) {
  const out = [];
  for (const [n, c] of Object.entries(nlsCases(m))) if (c.start_from === name) out.push(`nonlinear static ${n}`);
  for (const [n, p] of Object.entries(m.pushover_cases || {})) if (p.start_from === name) out.push(`pushover ${n}`);
  if (m.modal_from_case === name) out.push("MODAL (stiffness)");
  return out;
}

/** Validate every NLS case + chains + pushovers / modal against `m`. "" = ok. */
export function validateNls(m) {
  const cases = nlsCases(m);
  const pats = m.patterns || {};
  const uids = new Set((m.members || []).map(x => x.uid));
  const stories = new Set(storyNames(m));
  for (const [n, c] of Object.entries(cases)) {
    const at = `Nonlinear static case ${n}`;
    if (!n) return "Nonlinear static case: name must be non-empty.";
    if (!(c.loads || []).length) return `${at}: needs at least one load (pattern + scale).`;
    for (const l of c.loads) {
      if (!pats[l.pattern]) return `${at}: unknown load pattern ${l.pattern}.`;
      if (!isNum(l.scale)) return `${at}: scale of ${l.pattern} must be a number.`;
    }
    if (!["load_control", "displacement_control"].includes(c.load_application)) return `${at}: bad load application.`;
    if (!Number.isInteger(c.steps) || c.steps < 1 || c.steps > 10000) return `${at}: steps must be a whole number in [1, 10000].`;
    if (!["none", "p_delta", "large_displacement"].includes(c.geometric)) return `${at}: bad geometric option.`;
    if (!DOFS.includes(c.control_dof)) return `${at}: bad monitored DOF.`;
    if (c.control_point != null && c.control_story != null) return `${at}: give a control joint OR a control story, not both.`;
    if (c.control_point != null && !(Array.isArray(c.control_point) && c.control_point.length === 3 && c.control_point.every(isNum)))
      return `${at}: control joint needs x, y, z.`;
    if (c.control_story != null && !stories.has(c.control_story)) return `${at}: unknown control story ${c.control_story}.`;
    if (c.load_application === "displacement_control" && !(isNum(c.target_disp) && c.target_disp !== 0))
      return `${at}: displacement control needs a non-zero target displacement.`;
    if (!["column_base", "all_ends", "asce41"].includes(c.hinges)) return `${at}: bad hinge option.`;
    for (const [u, v] of Object.entries(c.My || {})) {
      if (!uids.has(u)) return `${at}: My references unknown member ${u}.`;
      if (!(isNum(v) && v > 0)) return `${at}: My of ${u} must be > 0.`;
    }
    if (c.default_My != null && !(isNum(c.default_My) && c.default_My > 0)) return `${at}: default My must be > 0 (or blank).`;
    if (!(isNum(c.hardening) && c.hardening >= 0 && c.hardening < 1)) return `${at}: hardening must be in [0, 1).`;
    for (const [k, v] of Object.entries(c.hinge_params || {})) {
      if (!HP_KEYS.some(h => h[0] === k)) return `${at}: unknown hinge parameter ${k}.`;
      if (!(isNum(v) && v > 0)) return `${at}: hinge parameter ${k} must be > 0.`;
    }
    if (c.start_from != null) {
      if (c.start_from === n) return `${at}: start_from cannot reference itself (cycle).`;
      if (!cases[c.start_from]) return `${at}: start_from references unknown nonlinear static case ${c.start_from}.`;
    }
  }
  for (const n of Object.keys(cases)) {
    let chain;
    try { chain = chainOf(cases, n); } catch (e) { return `Nonlinear static case chain: ${e.message}.`; }
    const geo = new Set(chain.map(k => cases[k].geometric));
    if (geo.size > 1)
      return `Nonlinear static case ${n}: every case of the start_from chain ${chain.join(" → ")} must use the same geometric nonlinearity (got ${[...geo].map(g => GEOM_LABEL[g]).join(", ")}).`;
  }
  for (const [n, p] of Object.entries(m.pushover_cases || {})) {
    const sf = p.start_from;
    if (!sf || !cases[sf]) continue;
    const g = GEOM_TO_PO[cases[sf].geometric];
    if ((p.geometric || "linear") !== g)
      return `Pushover case ${n}: starts from nonlinear static case ${sf} (${GEOM_LABEL[cases[sf].geometric]}); the pushover must use the matching geometric "${g}".`;
  }
  if (m.modal_from_case != null && !cases[m.modal_from_case])
    return `Modal stiffness: unknown nonlinear static case ${m.modal_from_case}.`;
  return "";
}

function defaultLoads(m) {
  const pats = Object.values(m.patterns || {});
  const p = pats.find(x => x.kind === "dead") || pats[0];
  return p ? [{ pattern: p.name, scale: 1.0 }] : [];
}

/** Rename an NLS case and every reference (start_from, pushover, modal, not-run). */
function renameRefs(m, oldName, newName) {
  for (const c of Object.values(nlsCases(m))) if (c.start_from === oldName) c.start_from = newName;
  for (const p of Object.values(m.pushover_cases || {})) if (p.start_from === oldName) p.start_from = newName;
  if (m.modal_from_case === oldName) m.modal_from_case = newName;
  if (Array.isArray(m.cases_not_run)) m.cases_not_run = m.cases_not_run.map(n => n === oldName ? newName : n);
}

export function deleteNlsCase(m, name, toast) {
  if (!nlsCases(m)[name]) return false;
  const refs = nlsRefs(m, name);
  if (refs.length) { toast && toast("Can't delete", `${name} is used by ${refs.join(", ")}`, "error", 5000); return false; }
  delete m.nonlinear_static_cases[name];
  if (!Object.keys(m.nonlinear_static_cases).length) delete m.nonlinear_static_cases;
  if (Array.isArray(m.cases_not_run)) m.cases_not_run = m.cases_not_run.filter(n => n !== name);
  return true;
}

/* ---- load-pattern references (modeledit.js hooks) */
export function nlsPatternRefs(m, pat) {
  return Object.entries(nlsCases(m)).filter(([, c]) => (c.loads || []).some(l => l.pattern === pat)).map(([n]) => n);
}
export function nlsPatternRename(m, oldName, newName) {
  for (const c of Object.values(nlsCases(m))) for (const l of c.loads || []) if (l.pattern === oldName) l.pattern = newName;
}

/* ================================================================
   Load Case Data — Nonlinear Static
   ================================================================ */
export function openNlsCase(ctx, name = null, opts = {}) {
  const m = ctx.store.model;
  if (!m) return null;
  const isNew = !name || !nlsCases(m)[name];
  const origName = isNew ? null : name;
  const src = isNew ? {
    name: uniqueName(takenCaseNames(m), "NLS"), loads: defaultLoads(m),
    ...clone(NLS_DEFAULTS),
  } : nlsCases(m)[name];
  const d = canonicalCase(src, src.name);
  let ctrl = d.control_point ? "point" : d.control_story ? "story" : "roof";
  let initCont = !!d.start_from;
  const joints = modelJoints(m);
  const H = Math.max(0, ...(m.stories || []).map(s => s.elevation || 0));
  const myRows = Object.entries(d.My).map(([uid, v]) => ({ uid, v }));
  const body = el("div", "cd-nls");
  const err = errorLine("nlsError");

  const others = () => nlsNames(m).filter(n => n !== origName);
  const draw = () => {
    body.textContent = "";
    body.appendChild(el("p", "muted dlg-intro",
      "ETABS <b>Nonlinear Static</b> load case: the loads are applied incrementally (Newton) from the initial " +
      "state; the end state can seed another nonlinear static case, a pushover or the modal stiffness."));
    /* ---- name + initial conditions */
    const g0 = group("Load Case Name");
    const nameIn = document.createElement("input");
    nameIn.type = "text"; nameIn.id = "nlsName"; nameIn.value = d.name; nameIn.spellcheck = false;
    nameIn.addEventListener("change", () => { d.name = nameIn.value.trim(); });
    g0.appendChild(row("Name", nameIn));
    body.appendChild(g0);

    const g1 = group("Initial Conditions");
    const r1 = el("div", "cd-inline");
    r1.append(
      radio("nlsInit", "zero", !initCont, "Zero initial conditions — start from unstressed state", () => { initCont = false; d.start_from = null; draw(); }, "nlsInitZero"),
      radio("nlsInit", "cont", initCont, "Continue from state at end of nonlinear case", () => {
        initCont = true;
        d.start_from = others().find(n => !(origName != null && wouldCycle(nlsCases(m), origName, n))) || null;
        const sc = d.start_from && nlsCases(m)[d.start_from];
        if (sc) d.geometric = sc.geometric;
        draw();
      }, "nlsInitCont"));
    g1.appendChild(r1);
    if (initCont) {
      const opts2 = others().map(n => {
        const cyc = origName != null && wouldCycle(nlsCases(m), origName, n);
        return [n, `${n}${cyc ? " (cycle)" : ""} · ${GEOM_LABEL[nlsCases(m)[n].geometric] || ""}`, cyc];
      });
      g1.appendChild(row("Start from case", select([["", others().length ? "— pick a nonlinear static case —" : "— no other nonlinear static case —", true], ...opts2], d.start_from || "", v => {
        d.start_from = v || null;
        const sc = nlsCases(m)[v];
        if (sc && sc.geometric !== d.geometric) { d.geometric = sc.geometric; }
        draw();
      }, "nlsStartFrom")));
    }
    if (d.start_from) {
      let chainTxt = "";
      try {
        const tmp = { ...nlsCases(m), [origName ?? "\u0000new"]: { ...d } };
        chainTxt = chainOf(tmp, origName ?? "\u0000new").map(n => esc(n === "\u0000new" || n === origName ? d.name : n)).join(" → ");
      } catch (e) { chainTxt = `<span class="cd-warn">${esc(e.message)}</span>`; }
      g1.appendChild(el("p", "muted cd-note", `Chain (one solution domain): <b>${chainTxt}</b>. ` +
        "Every case of a chain uses the same geometric nonlinearity; the target displacement is the TOTAL monitored displacement."));
    }
    body.appendChild(g1);

    /* ---- loads applied */
    const g2 = group("Loads Applied");
    const tbl = el("div", "cd-pts nls-loads"); tbl.id = "nlsLoads";
    tbl.appendChild(el("div", "cd-pt head", `<span>Load pattern</span><span>Scale factor</span><span></span>`));
    const pats = Object.keys(m.patterns || {});
    d.loads.forEach((l, i) => {
      const r = el("div", "cd-pt nls-load");
      r.append(
        select(pats.map(p => [p, p]), l.pattern, v => { l.pattern = v; }, `nlsPat${i}`),
        numInput("none", l.scale, { id: `nlsScale${i}`, onSet: v => { l.scale = v; } }));
      const x = el("button", "chip-x"); x.textContent = "✕"; x.title = "Remove load";
      x.addEventListener("click", () => { d.loads.splice(i, 1); draw(); });
      r.appendChild(x);
      tbl.appendChild(r);
    });
    g2.appendChild(tbl);
    g2.appendChild(btn("+ Add", "btn-small cd-add", () => {
      d.loads.push({ pattern: pats[0] || "", scale: 1.0 }); draw();
    }, "Add a load pattern row", "nlsAddLoad"));
    g2.appendChild(el("p", "muted cd-note", "A pattern may appear more than once (scales add)."));
    body.appendChild(g2);

    /* ---- geometric nonlinearity */
    const g3 = group("Geometric Nonlinearity Parameters");
    const r3 = el("div", "cd-inline");
    for (const [v, t] of Object.entries(GEOM_LABEL))
      r3.appendChild(radio("nlsGeom", v, d.geometric === v, t, () => { d.geometric = v; draw(); }, "nlsGeom-" + v));
    g3.appendChild(r3);
    g3.appendChild(el("p", "muted cd-note", "Model-wide P-Delta options do not apply to nonlinear static cases (set it here)."));
    body.appendChild(g3);

    /* ---- load application */
    const g4 = group("Load Application");
    const r4 = el("div", "cd-inline");
    r4.append(
      radio("nlsApp", "load_control", d.load_application === "load_control", "Full load (load control)", () => { d.load_application = "load_control"; draw(); }, "nlsLoadCtrl"),
      radio("nlsApp", "displacement_control", d.load_application === "displacement_control", "Displacement control", () => { d.load_application = "displacement_control"; draw(); }, "nlsDispCtrl"));
    g4.appendChild(r4);
    g4.appendChild(row("Number of steps", numInput("none", d.steps, { id: "nlsSteps", int: true, min: 1, max: 10000, onSet: v => { d.steps = v; } }),
      null, d.load_application === "load_control" ? "λ = 1/steps per step" : "Δ = (target − start)/steps per step"));
    const rr = el("div", "cd-inline");
    rr.append(
      el("span", "cd-lbl", "Monitored displacement"),
      radio("nlsCtrl", "roof", ctrl === "roof", "Roof (default)", () => { ctrl = "roof"; draw(); }, "nlsCtrlRoof"),
      radio("nlsCtrl", "story", ctrl === "story", "Story", () => { ctrl = "story"; if (!d.control_story) d.control_story = storyNames(m).slice(-1)[0] || null; draw(); }, "nlsCtrlStory"),
      radio("nlsCtrl", "point", ctrl === "point", "Joint", () => { ctrl = "point"; if (!d.control_point) d.control_point = (joints[joints.length - 1] || [0, 0, H]).slice(); draw(); }, "nlsCtrlPoint"));
    g4.appendChild(rr);
    if (ctrl === "story")
      g4.appendChild(row("Control story", select(storyNames(m).map(s => [s, s]), d.control_story || "", v => { d.control_story = v; }, "nlsStory"), null,
        "its diaphragm master (else its lowest-numbered node)"));
    if (ctrl === "point") {
      const p = d.control_point;
      const three = el("div", "cd-cols cd-cols3");
      ["X", "Y", "Z"].forEach((ax, k) => three.appendChild(
        row(ax, numInput("length", p[k], { id: "nlsP" + ax.toLowerCase(), onSet: v => { p[k] = v; } }), "length")));
      g4.appendChild(three);
      g4.appendChild(select([["", "Pick joint…"], ...joints.map((j, i) => [String(i), `${storyAtZ(m, j[2]) || "z"} · ${ptLabel(j)}`])], "", v => {
        if (v !== "") { d.control_point = joints[+v].slice(); draw(); }
      }, "nlsPickJoint"));
    }
    g4.appendChild(row("DOF", select(DOFS.map(x => [x, x]), d.control_dof, v => {
      const kOld = dofKind(d.control_dof);
      d.control_dof = v;
      if (dofKind(v) !== kOld) d.target_disp = null;
      draw();
    }, "nlsDof")));
    if (d.load_application === "displacement_control")
      g4.appendChild(row("Target displacement", numInput(dofKind(d.control_dof), d.target_disp, {
        id: "nlsTarget", allowEmpty: true, nonzero: true, placeholder: "required", onSet: v => { d.target_disp = v; },
      }), dofKind(d.control_dof), "total monitored value at the end of the case (start state included)"));
    body.appendChild(g4);

    /* ---- hinges */
    const g5 = group("Nonlinear Parameters — Hinges");
    g5.appendChild(row("Hinges", select(Object.entries(HINGE_LABEL), d.hinges, v => { d.hinges = v; draw(); }, "nlsHinges")));
    if (d.hinges !== "asce41") {
      g5.appendChild(row("Default yield moment My", numInput("moment", d.default_My, {
        id: "nlsDefMy", allowEmpty: true, gt: 0, placeholder: "none — no hinge", onSet: v => { d.default_My = v; },
      }), "moment", "blank and no member My = elastic (no hinge inserted)"));
      const mt = el("div", "cd-pts nls-my"); mt.id = "nlsMyRows";
      if (myRows.length) mt.appendChild(el("div", "cd-pt head", `<span>Member</span><span>My ${esc(U.label("moment"))}</span><span></span>`));
      const uids = (m.members || []).map(x => [x.uid, `${x.uid} · ${x.kind || ""}`]);
      myRows.forEach((r, i) => {
        const rw = el("div", "cd-pt");
        rw.append(select(uids, r.uid, v => { r.uid = v; }, `nlsMyUid${i}`),
          numInput("moment", r.v, { id: `nlsMyVal${i}`, gt: 0, onSet: v => { r.v = v; } }));
        const x = el("button", "chip-x"); x.textContent = "✕"; x.title = "Remove";
        x.addEventListener("click", () => { myRows.splice(i, 1); draw(); });
        rw.appendChild(x);
        mt.appendChild(rw);
      });
      g5.appendChild(mt);
      g5.appendChild(btn("+ Member My", "btn-small cd-add", () => {
        const used = new Set(myRows.map(r => r.uid));
        const u = (m.members || []).find(x => !used.has(x.uid));
        if (u) { myRows.push({ uid: u.uid, v: d.default_My || 100 }); draw(); }
      }, "Yield moment for one member", "nlsAddMy"));
    } else {
      g5.appendChild(el("p", "muted cd-note", "Members with Assign → Frame · Hinges = auto M3 / fiber PMM get ASCE 41-17 hinges. Optional parameters (blank = default):"));
      const cols = el("div", "cd-cols");
      for (const [k, lbl, kind, def] of HP_KEYS)
        cols.appendChild(row(lbl, numInput(kind, d.hinge_params[k] ?? null, {
          id: "nlsHp-" + k, allowEmpty: true, gt: 0, placeholder: U.inputValue(kind, def),
          onSet: v => { if (v == null) delete d.hinge_params[k]; else d.hinge_params[k] = v; },
        }), kind === "none" ? null : kind));
      g5.appendChild(cols);
    }
    g5.appendChild(row("Post-yield hardening ratio", numInput("none", d.hardening, { id: "nlsHard", min: 0, lt: 1, onSet: v => { d.hardening = v; } })));
    body.appendChild(g5);
    body.appendChild(err);
  };

  const fail = msg => { showError(err, msg); return false; };
  const commit = () => {
    showError(err, "");
    const nm = (d.name || "").trim();
    if (!nm) return fail("Enter a case name.");
    if (nm !== origName && takenCaseNames(m, origName).has(nm)) return fail(`The name ${nm} is already used by another case or combination.`);
    if (ctrl !== "story") d.control_story = null;
    if (ctrl !== "point") d.control_point = null;
    else d.control_point = snapToJoint(joints, d.control_point);
    if (d.hinges === "asce41") { /* My / default_My kept as-is (ignored by asce41) */ }
    const my = {};
    for (const r of myRows) { if (r.uid) my[r.uid] = r.v; }
    if (d.hinges !== "asce41") d.My = my;
    if (initCont && !d.start_from) return fail("Pick the nonlinear static case to continue from (or choose zero initial conditions).");
    if (d.start_from === nm) return fail("A case cannot start from itself.");
    const out = canonicalCase(d, nm);
    // trial model: apply rename + write, validate, then commit for real
    const trial = clone(m);
    trial.nonlinear_static_cases = trial.nonlinear_static_cases || {};
    if (origName && origName !== nm) {
      const rebuilt = {};
      for (const [k, v] of Object.entries(trial.nonlinear_static_cases)) rebuilt[k === origName ? nm : k] = k === origName ? out : v;
      trial.nonlinear_static_cases = rebuilt;
      renameRefs(trial, origName, nm);
    } else trial.nonlinear_static_cases[nm] = out;
    const msg = validateNls(trial);
    if (msg) return fail(msg);
    if (origName && origName === nm &&
        JSON.stringify(canonicalCase(nlsCases(m)[origName], origName)) === JSON.stringify(out)) return true;   // untouched
    m.nonlinear_static_cases = trial.nonlinear_static_cases;
    if (origName && origName !== nm) renameRefs(m, origName, nm);
    ctx.markDirty();
    ctx.onChange && ctx.onChange("nonlinear_static_cases");
    opts.onChange && opts.onChange(nm);
    return true;
  };
  const fb = footBar(isNew ? "New nonlinear static case" : "", [
    btn("Cancel", "", () => dlg.close(), "", "nlsCancel"),
    btn("OK", "btn-run", () => { if (commit()) dlg.close(); }, "", "nlsOk"),
  ]);
  const dlg = dialog("nlsCaseModal", {
    title: "Load Case Data — Nonlinear Static", glyph: "nls", wide: true, body, foot: fb.wrap, onUnits: draw,
  });
  draw();
  return dlg;
}

/* ================================================================
   Load Case Data — Modal (stiffness at end of nonlinear case)
   ================================================================ */
export function openModalCase(ctx) {
  const m = ctx.store.model;
  if (!m) return null;
  let from = m.modal_from_case ?? null;
  const body = el("div", "cd-modal");
  const err = errorLine("mdlError");
  const draw = () => {
    body.textContent = "";
    body.appendChild(el("p", "muted dlg-intro", `Modal case <b>${esc(ME.MODAL_CASE || "MODAL")}</b> · ` +
      `${esc(String(m.num_modes ?? "—"))} modes (Eigen). The modal stiffness feeds RS, modal TH / FNA, ` +
      "frequency-domain cases and load participation; masses are unchanged."));
    const g = group("Stiffness to Use");
    const names = nlsNames(m);
    g.append(
      radio("mdlStiff", "zero", !from, "Zero initial conditions — unstressed state", () => { from = null; draw(); }, "mdlZero"),
      radio("mdlStiff", "nls", !!from, "Stiffness at end of nonlinear case", () => {
        from = names[0] || null;
        if (!from) showError(err, "Define a nonlinear static case first (Define → Load Cases → Nonlinear Static).");
        draw();
      }, "mdlFromNls"));
    if (from) {
      g.appendChild(row("Nonlinear case", select(names.map(n => [n, `${n} · ${GEOM_LABEL[nlsCases(m)[n].geometric] || ""}`]), from, v => { from = v; }, "mdlCase")));
      g.appendChild(el("p", "muted cd-note", "Tangent stiffness at the end of that case's start_from chain (P-Delta stressed and / or yielded state)."));
    }
    body.appendChild(g);
    body.appendChild(err);
  };
  const commit = () => {
    if (from && !nlsCases(m)[from]) { showError(err, "Pick a nonlinear static case."); return false; }
    if ((m.modal_from_case ?? null) === from) return true;
    if (from == null) delete m.modal_from_case;
    else m.modal_from_case = from;
    ctx.markDirty();
    ctx.onChange && ctx.onChange("modal_from_case");
    return true;
  };
  const fb = footBar("", [
    btn("Cancel", "", () => dlg.close(), "", "mdlCancel"),
    btn("OK", "btn-run", () => { if (commit()) dlg.close(); }, "", "mdlOk"),
  ]);
  const dlg = dialog("modalCaseModal", { title: "Load Case Data — Modal", glyph: "modal", body, foot: fb.wrap });
  draw();
  return dlg;
}

/* ================================================================
   Results
   ================================================================ */
export const isNlCaseName = n => typeof n === "string" && n.startsWith("nl:");
export const nlsResults = r => (r && r.nonlinear_static) || {};

/** History series in DISPLAY units: {x, y, xLabel, yLabel} for a y mode
    "base" (−Σ reaction along the monitored DOF) or "lambda". */
export function historySeries(res, ymode = "base") {
  const nl = res && res.nonlinear;
  const h = nl && nl.history;
  if (!h) return null;
  const dof = (h.monitored && h.monitored.dof) || "UX";
  const xk = dofKind(dof);
  const xs = (h.disp || []).map(v => U.toDisplay(xk, v));
  let ys, yLabel;
  if (ymode === "lambda") { ys = (h.load_factor || []).slice(); yLabel = "load factor λ"; }
  else {
    const comp = BASE_COMP[dof] || "FX";
    const yk = comp[0] === "M" ? "moment" : "force";
    ys = ((h.base || {})[comp] || []).map(v => U.toDisplay(yk, -v));
    yLabel = `base ${comp[0] === "M" ? "moment" : "shear"} −Σ${comp} ${U.label(yk)}`;
  }
  return { xs, ys, dof, xLabel: `${dof} ${U.label(xk)}`, yLabel };
}

function historyChart(res, ymode, w = 620, h = 220) {
  const s = historySeries(res, ymode);
  if (!s) return `<p class="muted cd-note">No step history.</p>`;
  return nlsLineChart([{ xs: s.xs, ys: s.ys, dots: s.xs.length <= 60 }],
    { w, h, xLabel: s.xLabel, yLabel: s.yLabel, aria: "Nonlinear static step history", cls: "nls-chart" });
}

function hingeRows(nl, cdef) {
  const yielded = new Set(nl.yielded || []);
  if ((nl.hinges || []).length) {
    return nl.hinges.map(hg => {
      const st = hg.state || [];
      const rot = hg.rot || [];
      const peak = rot.reduce((a, v) => Math.max(a, Math.abs(v)), 0);
      return { uid: hg.uid, end: hg.end ?? "", kind: hg.kind || "", My: hg.My, rot: peak, state: st[st.length - 1] || "elastic" };
    });
  }
  return Object.entries(nl.hinge_rotations || {}).map(([uid, rot]) => ({
    uid, end: "", kind: "Steel01", My: cdef ? ((cdef.My || {})[uid] ?? cdef.default_My ?? null) : null, rot, state: yielded.has(uid) ? "yielded" : "elastic",
  })).sort((a, b) => b.rot - a.rot);
}
function hingeTable(nl, limit = 0, cdef = null) {
  const rows = hingeRows(nl, cdef);
  if (!rows.length) return `<p class="muted cd-note">No hinges in this case (elastic, or no My / hinge assignment).</p>`;
  const shown = limit ? rows.slice(0, limit) : rows;
  return `<div class="table-scroll nls-hinge-wrap"><table class="data-table nls-hinges"><thead><tr><th class="txt">Member</th><th class="txt">End</th>` +
    `<th class="txt">Type</th><th>My ${esc(U.label("moment"))}</th><th>peak |θ| rad</th><th class="txt">State</th></tr></thead><tbody>` +
    shown.map(r => `<tr class="${r.state !== "elastic" ? "nls-yield" : ""}"><td class="txt">${esc(r.uid)}</td><td class="txt">${esc(r.end)}</td>` +
      `<td class="txt dim">${esc(r.kind)}</td><td>${r.My == null ? "—" : U.fmt("moment", r.My, 1)}</td><td>${isNum(r.rot) ? r.rot.toExponential(3) : "—"}</td>` +
      `<td class="txt"><span class="nls-state nls-st-${esc(String(r.state).toLowerCase())}">${esc(r.state)}</span></td></tr>`).join("") +
    `</tbody></table></div>` + (limit && rows.length > limit ? `<p class="muted cd-note">${rows.length - limit} more — Display → Nonlinear Static Results…</p>` : "");
}
function summaryHtml(name, res) {
  const nl = res.nonlinear || {};
  const h = nl.history || {};
  const mon = h.monitored || {};
  const n = (h.step || []).length;
  const last = n ? (h.disp || [])[n - 1] : null;
  const k = dofKind(mon.dof);
  const warn = nl.converged === false
    ? `<div class="nls-warn" id="nlsNotConverged">⚠ Not converged — the last converged state (λ = ${isNum(nl.final_load_factor) ? (+nl.final_load_factor).toPrecision(4) : "—"}) is reported.</div>` : "";
  return warn + `<dl class="nls-kv">` +
    [["Chain", (nl.chain || [name]).map(esc).join(" → ")],
      ["Load application", nl.load_application === "displacement_control" ? "Displacement control" : "Load control"],
      ["Geometric", GEOM_LABEL[nl.geometric] || esc(nl.geometric || "none")],
      ["Converged", nl.converged === false ? `<span class="cd-warn">no</span>` : "yes"],
      ["Final load factor λ", isNum(nl.final_load_factor) ? (+nl.final_load_factor).toPrecision(5) : "—"],
      ["Monitored", `${esc(mon.dof || "")} @ ${mon.point ? esc(ptLabel(mon.point)) : "node " + esc(mon.node ?? "—")}`],
      ["Final monitored", last == null ? "—" : `${U.fmt(k, last, k === "rotation" ? 5 : 2)} ${esc(U.label(k))}`],
      ["Steps", String(Math.max(0, n - 1))],
      ["Yielded hinges", String((nl.yielded || []).length)],
    ].map(([a, b]) => `<div><dt>${esc(a)}</dt><dd>${b}</dd></div>`).join("") + `</dl>` +
    ((nl.warnings || []).length ? `<ul class="nls-warnlist">${nl.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : "");
}

/** Story-tab card, shown while a "nl:<name>" case is selected. */
export function renderNlsCard(sky) {
  ensureCss();
  const host = document.getElementById("content-story");
  if (!host) return;
  let card = document.getElementById("nlsCard");
  const st = sky.store;
  const name = isNlCaseName(st.caseName) ? st.caseName.slice(3) : null;
  const res = name && nlsResults(st.results)[name];
  if (!res) { if (card) card.remove(); return; }
  if (!card) {
    card = el("div", "diag-block nls-card");
    card.id = "nlsCard";
    host.insertBefore(card, host.firstChild);
  }
  const ymode = st.nlsYMode || "base";
  card.innerHTML = `<div class="nls-card-head"><div><h3>${svgIcon("nls", "nls-h-ico")} Nonlinear static <span class="muted">${esc(name)} · final cumulative state below</span></h3></div>` +
    `<div class="nls-card-acts"><div class="seg nls-seg" role="group"><button class="seg-btn${ymode === "base" ? " is-active" : ""}" data-ny="base">Base shear</button>` +
    `<button class="seg-btn${ymode === "lambda" ? " is-active" : ""}" data-ny="lambda">Load factor</button></div>` +
    `<button class="chip" id="nlsCardDetails" title="Display → Nonlinear Static Results…">Details…</button></div></div>` +
    `<div class="nls-card-grid"><div class="nls-plot" id="nlsCardPlot">${historyChart(res, ymode, 560, 210)}</div>` +
    `<div class="nls-sum">${summaryHtml(name, res)}</div></div>` +
    `<div class="nls-card-hinges">${hingeTable(res.nonlinear || {}, 8, nlsCases(st.model)[name])}</div>`;
  card.querySelectorAll("[data-ny]").forEach(b => b.addEventListener("click", () => {
    st.nlsYMode = b.dataset.ny; renderNlsCard(sky);
  }));
  card.querySelector("#nlsCardDetails").addEventListener("click", () => openNlsResults(makeCtx(sky), name));
}

/** Display → Nonlinear Static Results… */
export function openNlsResults(ctx, pick = null) {
  const r = ctx.store.results;
  const names = Object.keys(nlsResults(r));
  let sel = pick && names.includes(pick) ? pick : names[0] || null;
  let ymode = ctx.store.nlsYMode || "base";
  const body = el("div", "nls-res");
  const draw = () => {
    body.textContent = "";
    if (!sel) {
      body.appendChild(el("p", "muted cd-empty", r ? "No nonlinear static results — define a Nonlinear Static case (Define → Load Cases) and run."
        : "Run the analysis first."));
      return;
    }
    const res = nlsResults(r)[sel];
    const ctl = el("div", "cd-inline nls-res-ctl");
    ctl.append(row("Case", select(names.map(n => [n, n]), sel, v => { sel = v; draw(); }, "nlsResCase")),
      radio("nlsResY", "base", ymode === "base", "Base shear vs displacement", () => { ymode = "base"; draw(); }, "nlsResYBase"),
      radio("nlsResY", "lambda", ymode === "lambda", "Load factor vs displacement", () => { ymode = "lambda"; draw(); }, "nlsResYLam"));
    const view = btn("Show final state", "btn-small", () => {
      const sky = window.__sky;
      if (sky && sky.store) {
        sky.store.caseName = "nl:" + sel;
        const cs = document.getElementById("caseSelect");
        if (cs) { cs.value = "nl:" + sel; cs.dispatchEvent(new Event("change")); }
        sky.switchTab && sky.switchTab("story");
      }
      dlg.close();
    }, "Select this case in the results case list (Story / Reactions / Member Forces / 3D)", "nlsResShow");
    ctl.appendChild(view);
    body.appendChild(ctl);
    const plot = el("div", "cd-preview"); plot.id = "nlsResPlot";
    plot.innerHTML = historyChart(res, ymode, 840, 260);
    body.appendChild(plot);
    body.appendChild(el("div", "nls-sum", summaryHtml(sel, res)));
    const g = group("Step History");
    const s = historySeries(res, "base") || { xs: [], ys: [] };
    const h = res.nonlinear.history || {};
    g.appendChild(el("div", "table-scroll nls-hist-wrap",
      `<table class="data-table nls-hist" id="nlsHistTable"><thead><tr><th>Step</th><th>λ</th><th>${esc(s.xLabel || "disp")}</th><th>${esc(s.yLabel || "base")}</th></tr></thead><tbody>` +
      (h.step || []).map((st, i) => `<tr><td>${st}</td><td>${isNum(h.load_factor?.[i]) ? (+h.load_factor[i]).toPrecision(5) : "—"}</td>` +
        `<td>${isNum(s.xs[i]) ? (+s.xs[i]).toPrecision(5) : "—"}</td><td>${isNum(s.ys[i]) ? (+s.ys[i]).toPrecision(5) : "—"}</td></tr>`).join("") +
      `</tbody></table>`));
    body.appendChild(g);
    const g2 = group("Hinges");
    g2.appendChild(el("div", "", hingeTable(res.nonlinear || {}, 0, nlsCases(ctx.store.model)[sel])));
    body.appendChild(g2);
  };
  const fb = footBar("Display → Nonlinear Static Results", [btn("Close", "btn-primary", () => dlg.close(), null, "nlsResClose")]);
  const dlg = dialog("nlsResModal", { title: "Nonlinear Static Results", glyph: "chart", wide: true, body, foot: fb.wrap, onUnits: draw });
  draw();
  return dlg;
}

/* ================================================================
   app.js result-select helpers (hooks)
   ================================================================ */
/** [value, label] entries for the results case select. */
export const nlsCaseOptions = r => Object.keys(nlsResults(r)).map(n => [`nl:${n}`, `NL: ${n}`]);
/** Case dict for a "nl:<name>" selection (static-case shape), or null. */
export const nlsCaseData = (r, name) => isNlCaseName(name) ? (nlsResults(r)[name.slice(3)] || null) : null;

/* ================================================================
   install — adds the entrypoints to window.__sky (additive only)
   ================================================================ */
export function installNls(sky) {
  if (!sky) return;
  ensureCss();
  Object.assign(sky, {
    openNlsCase: (name, opts = {}) => openNlsCase(makeCtx(sky, opts), name, opts),
    deleteNlsCase: name => deleteNlsCase(sky.store.model, name, sky.toast),
    openModalCase: (opts = {}) => openModalCase(makeCtx(sky, opts)),
    openNlsResults: name => openNlsResults(makeCtx(sky), name),
    renderNlsCard: () => renderNlsCard(sky),
    validateNls,
    closeNlsDialog: closeDialog,
  });
  const upd = () => { try { renderNlsCard(sky); } catch (e) { console.warn("nls card", e); } };
  document.addEventListener("sky:results-changed", upd);
  document.addEventListener("sky:units-changed", upd);
}
