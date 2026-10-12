/* SkyFrame — Wave C dynamics options (CONTRACT "Eigen shift / cutoff /
   tolerance, modal history and FNA case types, TH initial conditions,
   RS / steady-state modal damping"), dlgkit standard.

     Analyze › Advanced › Eigen Parameters…        (an-wc-eigen)
        model.eigen_options {shift, cutoff, tolerance} of MODAL and the same
        keys on every named eigen modal case (modal_cases[n]).
     Analyze › Advanced › Modal Damping (RS / SS)…  (an-wc-mdamp)
        rs_cases / steady_state_cases / psd_cases [n].modal_damping
        {type, points, a0, a1, overrides, sa_correction}.
     Define › Load Cases › Time History Solution Type… (def-wc-thtype)
        th_cases[n].th_type (direct | modal | fna) and initial_case
        (nonlinear static or staged case).

   Every write happens only for a field the user changed (dkCommit, changed
   top-level keys only) so OK with defaults is byte-identical.  The model is
   SI; frequencies are Hz, periods s, damping ratios dimensionless.
   ?mock=1: js/mock_wavec_dyn.js decorates the mock results. */

import {
  el as wcEl, esc as wcEsc, isNum as wcIsNum, dkForm as wcForm, dkGroup as wcGroup, dkNote as wcNote,
  dkDialog as wcDialog, dkDraft as wcDraft, dkCommit as wcCommit, dkAdvanced as wcAdvanced,
} from "./dlgkit.js";
import { wcMockAugment as wcMockAug } from "./mock_wavec_dyn.js";

export const WC_TH_TYPES = [["direct", "Direct integration"], ["modal", "Linear modal history"], ["fna", "Nonlinear modal history (FNA)"]];
export const WC_MD_TYPES = [["constant", "Constant (case damping)"], ["interpolated_period", "Interpolated by period"],
  ["interpolated_frequency", "Interpolated by frequency"], ["mass_stiffness", "Mass and stiffness proportional"], ["composite", "Material proportional (composite)"]];
const EIG = ["shift", "cutoff", "tolerance"];

/* ------------------------------------------------------------ eigen */
export function openEigenParams(sky) {
  const D = wcDraft(sky);
  const F = wcForm();
  const touched = new Set();
  const eo = { ...(D.eigen_options || {}) };
  const mcs = Object.entries(D.modal_cases || {}).filter(([, c]) => (c.type || "eigen") === "eigen" && !c.stiffness_case);
  const work = Object.fromEntries(mcs.map(([n, c]) => [n, Object.fromEntries(EIG.filter(k => k in c).map(k => [k, c[k]]))]));
  const fields = (obj, tag) => [
    F.num(obj, "shift", { label: "Frequency shift (center)", kind: "frequency", min: 0, allowNull: true, nullText: "none", id: `wcEig${tag}Shift`,
      write: v => { if (v == null) delete obj.shift; else obj.shift = v; touched.add(tag); } }),
    F.num(obj, "cutoff", { label: "Cutoff frequency (radius)", kind: "frequency", min: 0, allowNull: true, nullText: "none", id: `wcEig${tag}Cut`,
      write: v => { if (v == null) delete obj.cutoff; else obj.cutoff = v; touched.add(tag); }, hint: "only modes with |f − shift| ≤ cutoff" }),
    F.num(obj, "tolerance", { label: "Convergence tolerance", gt: 0, max: 0.01, allowNull: true, nullText: "1e-9 (default)", id: `wcEig${tag}Tol`,
      write: v => { if (v == null) delete obj.tolerance; else obj.tolerance = v; touched.add(tag); } }),
  ];
  const body = [wcGroup("MODAL (default modal case)", fields(eo, "MODAL"),
    { note: "Empty fields keep the default eigen solver. With a shift the modes nearest the shift frequency are found by shift-invert subspace iteration." })];
  for (const [n] of mcs) body.push(wcGroup(`Modal case ${wcEsc(n)}`, fields(work[n], "mc_" + n)));
  if (D.modal_from_case) body.push(wcNote("MODAL uses the stiffness at the end of a nonlinear case — the shift options cannot be combined with it."));
  return wcDialog({
    id: "wcEigenDlg", title: "Eigen Parameters", iconId: "exleaf-modal", size: "md", form: F, body,
    intro: "Frequency shift, cutoff frequency and convergence tolerance of eigen modal cases (ETABS Modal Case › Eigen).",
    onOk: () => {
      if (touched.has("MODAL")) { if (Object.keys(eo).length) D.eigen_options = eo; else delete D.eigen_options; }
      for (const [n] of mcs) if (touched.has("mc_" + n)) {
        const c = D.modal_cases[n];
        for (const k of EIG) delete c[k];
        Object.assign(c, work[n]);
      }
      wcCommit(sky, D);
    },
  });
}

/* ---------------------------------------------- TH solution type */
export function openThTypes(sky) {
  const D = wcDraft(sky);
  const F = wcForm();
  const names = Object.keys(D.th_cases || {});
  const inits = [["", "None (zero / own gravity stage)"],
    ...Object.keys(D.nonlinear_static_cases || {}).map(n => [n, `${n} (nonlinear static)`]),
    ...Object.keys(D.staged_cases || {}).map(n => [n, `${n} (staged construction)`])];
  const body = names.length ? names.map(n => {
    const c = D.th_cases[n];
    return wcGroup(`Time history ${wcEsc(n)}`, [
      F.select(c, "th_type", WC_TH_TYPES, { label: "Solution type", id: "wcThType_" + n, read: () => c.th_type || "direct",
        write: v => { if (v === "direct") delete c.th_type; else c.th_type = v; } }),
      F.select(c, "initial_case", inits, { label: "Initial conditions from", id: "wcThInit_" + n, read: () => c.initial_case || "",
        write: v => { if (v) c.initial_case = v; else delete c.initial_case; },
        validate: v => (v && (c.th_type || "direct") === "modal") ? "A linear modal history starts from zero — choose FNA or direct integration." : "" }),
    ]);
  }) : [wcNote("No time-history cases yet — add one in Define › Load Cases › Time History….")];
  return wcDialog({
    id: "wcThTypeDlg", title: "Time History Solution Type", iconId: "exleaf-th", size: "md", form: F, body,
    intro: "Linear modal history and FNA run as their own case types (Run All, Set Load Cases to Run, combinations). Nonlinear direct integration can continue from a staged or a hinged nonlinear static case.",
    onOk: () => { wcCommit(sky, D); },
  });
}

/* ---------------------------------------------------- modal damping */
export function openModalDamping(sky, caseKey) {
  const D = wcDraft(sky);
  const F = wcForm();
  const all = [...Object.keys(D.rs_cases || {}).map(n => ["rs_cases", n, "Response spectrum"]),
    ...Object.keys(D.steady_state_cases || {}).map(n => ["steady_state_cases", n, "Steady state"]),
    ...Object.keys(D.psd_cases || {}).map(n => ["psd_cases", n, "PSD"])];
  if (!all.length) return wcDialog({ id: "wcMdDlg", title: "Modal Damping", size: "sm", noCancel: true, body: [wcNote("No response-spectrum, steady-state or PSD cases yet.")] });
  const state = { key: caseKey && all.some(a => a[0] + "/" + a[1] === caseKey) ? caseKey : all[0][0] + "/" + all[0][1] };
  const work = {}, touched = new Set();
  const workOf = k => {
    if (!(k in work)) { const [c, n] = k.split(/\/(.*)/s); const md = D[c][n].modal_damping; work[k] = md ? JSON.parse(JSON.stringify(md)) : { type: "constant" }; }
    return work[k];
  };
  const host = wcEl("div", { class: "wc-md-body" });
  const caseRow = F.select(state, "key", all.map(([c, n, l]) => [c + "/" + n, `${n} — ${l}`]), { label: "Load case", id: "wcMdCase", onChange: () => render() });
  function render() {
    if (F.forget) F.forget(host);
    host.textContent = "";
    const k = state.key, w = workOf(k), isRs = k.startsWith("rs_cases/");
    const mark = () => touched.add(k);
    const rows = [F.select(w, "type", WC_MD_TYPES, { label: "Damping type", id: "wcMdType", write: v => { const keep = w.overrides; for (const x of Object.keys(w)) delete w[x]; w.type = v; if (keep) w.overrides = keep; if (v.startsWith("interpolated")) w.points = [[v === "interpolated_period" ? 0.1 : 1.0, 0.05], [v === "interpolated_period" ? 2.0 : 10.0, 0.05]]; if (v === "mass_stiffness") { w.periods = [1.0, 0.1]; w.ratios = [0.05, 0.05]; } mark(); }, onChange: () => render() })];
    if ((w.type || "").startsWith("interpolated")) {
      const pts = w.points.map(p => ({ x: p[0], z: p[1] }));
      rows.push(F.table(pts, [{ key: "x", label: w.type === "interpolated_period" ? "Period (s)" : "Frequency (Hz)", type: "num" }, { key: "z", label: "Damping ratio", type: "num" }], {
        id: "wcMdPts", addLabel: "+ Add point", minRows: 1, newRow: r => ({ x: r.length ? r[r.length - 1].x * 2 : 1, z: 0.05 }),
        onChange: () => { w.points = pts.map(p => [p.x, p.z]); mark(); },
        validateRow: (r, i) => !(wcIsNum(r.z) && r.z >= 0 && r.z < 1) ? `Row ${i + 1}: ratio must be in [0, 1).` : (!(wcIsNum(r.x) && r.x >= 0) ? `Row ${i + 1}: value ≥ 0.` : ""),
        validate: rs => rs.some((r, i) => i && r.x <= rs[i - 1].x) ? "Values must increase strictly." : "" }));
    }
    if (w.type === "mass_stiffness") {
      const pz = { T1: w.periods[0], T2: w.periods[1], z1: w.ratios[0], z2: w.ratios[1] };
      const sync = () => { w.periods = [pz.T1, pz.T2]; w.ratios = [pz.z1, pz.z2]; mark(); };
      rows.push(F.num(pz, "T1", { label: "Period 1", kind: "period", gt: 0, id: "wcMdT1", onChange: sync }), F.num(pz, "z1", { label: "Damping at period 1", min: 0, lt: 1, id: "wcMdZ1", onChange: sync }),
        F.num(pz, "T2", { label: "Period 2", kind: "period", gt: 0, id: "wcMdT2", onChange: sync }), F.num(pz, "z2", { label: "Damping at period 2", min: 0, lt: 1, id: "wcMdZ2", onChange: sync }));
    }
    if (w.type === "composite") rows.push(wcNote("Ratios from Analyze › Advanced › Damping Options › Material damping (strain-energy weighted per mode)."));
    const ov = Object.entries(w.overrides || {}).map(([m, z]) => ({ m: +m, z }));
    const ovTbl = F.table(ov, [{ key: "m", label: "Mode", type: "num" }, { key: "z", label: "Damping ratio", type: "num" }], {
      id: "wcMdOv", addLabel: "+ Add override", empty: "No per-mode overrides.", newRow: r => ({ m: r.length ? r[r.length - 1].m + 1 : 1, z: 0.05 }),
      onChange: () => { if (ov.length) w.overrides = Object.fromEntries(ov.map(r => [String(Math.round(r.m)), r.z])); else delete w.overrides; mark(); },
      validateRow: (r, i) => !(Number.isInteger(r.m) && r.m >= 1) ? `Row ${i + 1}: mode number ≥ 1.` : (!(wcIsNum(r.z) && r.z >= 0 && r.z < 1) ? `Row ${i + 1}: ratio in [0, 1).` : "") });
    const adv = [ovTbl];
    if (isRs) adv.push(F.select(w, "sa_correction", [["none", "None"], ["ec8", "EN 1998-1 η(ζ) relative to the function damping"]], { label: "Spectrum damping correction", id: "wcMdSa",
      read: () => w.sa_correction || "none", write: v => { if (v === "none") delete w.sa_correction; else w.sa_correction = v; mark(); } }));
    host.append(wcGroup("Modal Damping", rows), wcAdvanced(adv, { summary: "Advanced — per-mode overrides" + (isRs ? " · spectrum correction" : ""), countFn: () => Object.keys(w.overrides || {}).length + (w.sa_correction ? 1 : 0) }));
  }
  render();
  return wcDialog({
    id: "wcMdDlg", title: "Modal Damping — RS / Steady State", iconId: "exleaf-rs", size: "md", form: F, body: [wcGroup(null, [caseRow]), host],
    intro: "Per-mode damping of response-spectrum (CQC / DSC correlation, optional spectrum correction) and steady-state / PSD cases (modal receptances).",
    onOk: () => {
      for (const k of touched) {
        const [c, n] = k.split(/\/(.*)/s), w = work[k];
        const plain = (w.type || "constant") === "constant" && !w.overrides && !w.sa_correction;
        if (plain) delete D[c][n].modal_damping; else D[c][n].modal_damping = w;
      }
      wcCommit(sky, D);
    },
  });
}

export function initWaveCDyn(sky) {
  if (!sky || !sky.store) return null;
  sky.wcDyn = { eigen: () => openEigenParams(sky), thTypes: () => openThTypes(sky), modalDamping: k => openModalDamping(sky, k) };
  if (sky.menu && sky.menu.add) {
    const add = (path, item) => { try { sky.menu.add(path, item); } catch (e) { console.warn("menu add " + item.act, e); } };
    add("Analyze/Advanced", { label: "Eigen Parameters…", act: "an-wc-eigen", hint: "frequency shift · cutoff · tolerance", fn: () => openEigenParams(sky) });
    add("Analyze/Advanced", { label: "Modal Damping (RS / SS)…", act: "an-wc-mdamp", hint: "interpolated · proportional · composite · overrides", fn: () => openModalDamping(sky) });
    add("Define/Load Cases", { label: "Time History Solution Type…", act: "def-wc-thtype", hint: "direct · linear modal · FNA · initial conditions", fn: () => openThTypes(sky) });
  }
  document.addEventListener("sky:results-changed", () => {
    const S = sky.store;
    if (S.mock && S.results && S.model) wcMockAug(S.model, S.results);
  });
  return sky.wcDyn;
}
