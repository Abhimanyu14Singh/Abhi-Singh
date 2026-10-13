/* SkyFrame Wave G — beyond ETABS: Select by Result + Result Probes (watch list).

   Select › More Criteria › By Result…   (sel-g-byresult)
     Pick a case / combination / multi-step case (step or envelope), an
     object type and a quantity — member P V2 V3 T M2 M3 (|max| / max / min
     over the output stations, max/min pairs included), joint displacement /
     rotation / drift ratio, shell F11 … V23 and S11 S22 S12 SVM SMax SMin
     (top / bottom face), elastic link force, story drift — an operator and a
     threshold in display units. The matching objects are added to (or
     replace) the selection through the selection API (sky.handleSelect);
     picking is untouched. Programmatic: sky.results.selectByResult(spec).

   Tools › Result Watch List   (tool-g-watch)
     Pinned values (joint displacement / drift, member end force / station
     value / max, pier force, shell stress at an element, support reaction,
     story drift, link force) in a docked list that updates live when the
     case, step, envelope or the model results change; export to CSV.
     Pins come from the member detail window (📌), the Add Probe… dialog and
     sky.results.pin(probe) (e.g. a context menu). */

import gPU from "./units.js";
import * as gP from "./g_core.js";
import { el as gpEl, esc as gpEsc, dkForm as gpForm, dkGroup as gpGroup, dkDialog as gpDialog, dkNote as gpNote } from "./dlgkit.js";

const CSS = `
.g-watch { position: absolute; right: 12px; top: 56px; z-index: 36; width: 330px; max-height: calc(100% - 190px); display: flex; flex-direction: column;
  background: rgba(17,22,29,0.96); border: 1px solid var(--border-soft, #2a3340); border-radius: 8px; box-shadow: 0 8px 22px rgba(0,0,0,.4);
  font-size: 11.5px; color: var(--text-2, #c8d0da); }
.g-watch.hidden { display: none; }
.g-watch header { display: flex; align-items: center; gap: 5px; padding: 5px 8px; border-bottom: 1px solid var(--border-soft, #2a3340); }
.g-watch header h3 { flex: 1; margin: 0; font-size: 12px; color: var(--text-1, #e8edf3); }
.g-watch header button { background: var(--bg-2, #141a22); color: var(--text-1, #e8edf3); border: 1px solid var(--border-soft, #2a3340); border-radius: 5px; height: 21px; padding: 0 6px; cursor: pointer; font-size: 11px; }
.g-watch .g-w-body { overflow: auto; padding: 4px 6px 6px; }
.g-watch table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.g-watch td, .g-watch th { padding: 2px 4px; border-bottom: 1px solid rgba(255,255,255,.05); text-align: right; }
.g-watch td:first-child, .g-watch th:first-child { text-align: left; max-width: 170px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.g-watch .g-w-case { color: var(--text-3, #99a3b0); padding: 2px 4px 4px; }
.g-watch .g-w-x { background: none; border: none; color: var(--text-3, #99a3b0); cursor: pointer; }
.g-watch .g-w-empty { color: var(--text-3, #99a3b0); padding: 6px 4px; }
`;

const QKIND = {
  frame: q => (["T", "M2", "M3"].includes(q) ? "moment" : "force"),
  joint: q => (/^R/.test(q) ? "rotation" : /^DRIFT/.test(q) ? "none" : "disp"),
  shell: q => (/^S/.test(q) ? "stress" : /^M/.test(q) ? "line_moment" : "line_force"),
  link: q => (/^R/.test(q) ? "moment" : "force"),
  story: () => "none",
};
const QLABEL = { N: "P (axial)", V2: "V2", V3: "V3", T: "T", M2: "M2", M3: "M3", UX: "Ux", UY: "Uy", UZ: "Uz", U: "|U|", RX: "Rx", RY: "Ry", RZ: "Rz",
  DRIFT_X: "drift ratio X", DRIFT_Y: "drift ratio Y" };

export function gInitProbe(sky, api) {
  const S = sky.store;
  if (!document.getElementById("gProbeCss")) document.head.appendChild(gpEl("style", { id: "gProbeCss", text: CSS }));
  const toast = (t, m, k = "info", ms = 3500) => sky.toast && sky.toast(t, m, k, ms);

  /* ================================================================ select by result */
  function shellValues(state, comp, face) {
    const r = S.results, m = S.model, out = {};
    if (!state || !state.shell_forces) return out;
    (r.shell_quads || []).forEach((q, i) => {
      const f = gP.quadForces(state.shell_forces, i);
      if (!f) return;
      out[i] = gP.shellComponent(f, gP.shellThickness(m, q.region), comp, face || "top");
      if (state.shell_forces_min) {
        const g = gP.quadForces(state.shell_forces_min, i);
        const w = g ? gP.shellComponent(g, gP.shellThickness(m, q.region), comp, face || "top") : null;
        if (w != null && Math.abs(w) > Math.abs(out[i] || 0)) out[i] = w;
      }
    });
    return out;
  }
  function stateFor(spec) {
    if (!spec.case || spec.case === api.selection().key) return api.state();
    const e = api.entry(spec.case);
    if (!e) return null;
    const sd = api.stepData(e.name);
    return gP.extractState(S.results, { key: spec.case, step: -1, env: spec.env || "step" }, sd, { thFrames: (r, n) => (sky.wcVideo && sky.wcVideo.thFrames ? sky.wcVideo.thFrames(r, n) : null) });
  }
  /** spec {case?, env?, objType, quantity, mode, op, threshold (SI), face?, group?, add?} */
  function selectByResult(spec, { apply = true } = {}) {
    const state = stateFor(spec);
    const ctx = { model: S.model, results: S.results, state, linkForces: api.linkForces ? api.linkForces() : {} };
    if (spec.objType === "shell") ctx.shellValues = shellValues(state, spec.quantity, spec.face);
    const res = gP.selectByResult(ctx, { ...spec, filter: spec.group ? { group: spec.group } : null });
    if (apply && sky.handleSelect) sky.handleSelect(res.refs, !!spec.add);
    return res;
  }
  function openSelectByResult() {
    if (!S.results) { toast("Select by Result", "Run the analysis first (F5)."); return null; }
    const cat = api.catalog().filter(e => e.kind !== "modal" && e.kind !== "buckling");
    const cur = api.selection();
    const o = { case: cat.some(e => e.key === cur.key) ? cur.key : (cat[0] || {}).key, env: "step", objType: "frame", quantity: "M3", mode: "absmax", op: ">=", threshold: 0, face: "top", add: false, group: "" };
    const F = gpForm();
    const qOpts = () => gP.SBR_QUANTITIES[o.objType].map(q => [q, QLABEL[q] || q]);
    const unitKind = () => QKIND[o.objType](o.quantity);
    const note = gpNote("");
    const syncNote = () => {
      const e = api.entry(o.case);
      const multi = e && e.multi;
      note.innerHTML = !e ? "" : e.kind === "rs" ? "Response-spectrum values are positive envelopes: use |value|." :
        multi && o.case === cur.key ? `Uses the results toolbar state: <b>${gpEsc((api.state() || {}).stepLabel || "")}</b>.` :
          multi ? "Uses the last recorded step (or the envelope) of this case." : e.minmax ? "Max / min pair: |value| uses the larger magnitude of both." : "";
    };
    let thrRow = null;
    const thrBox = gpEl("div", {});
    const buildThr = () => {
      thrBox.textContent = "";
      thrRow = F.num(o, "threshold", { label: "Threshold", kind: unitKind(), id: "gSbrThr" });
      thrBox.appendChild(thrRow);
    };
    buildThr();
    const body = [
      gpGroup("Results", [
        F.select(o, "case", cat.map(e => [e.key, e.label]), { label: "Case / combination", id: "gSbrCase", onChange: () => syncNote() }),
        F.select(o, "env", [["step", "Selected / last step"], ["maxmin", "Envelope (max / min)"]], { label: "Multi-step", id: "gSbrEnv" }),
        note,
      ]),
      gpGroup("Criterion", [
        F.select(o, "objType", [["frame", "Frame members"], ["joint", "Joints"], ["shell", "Shells (walls / slabs)"], ["link", "Links (elastic)"], ["story", "Stories (all objects)"]],
          { label: "Object type", id: "gSbrType", onChange: () => { o.quantity = gP.SBR_QUANTITIES[o.objType][0]; F.refresh(); buildThr(); } }),
        F.select(o, "quantity", qOpts, { label: "Quantity", id: "gSbrQty", onChange: () => buildThr() }),
        F.select(o, "face", [["top", "Top (+3 face)"], ["bot", "Bottom"]], { label: "Shell face (stresses)", id: "gSbrFace" }),
        F.select(o, "mode", [["absmax", "|value| (largest magnitude)"], ["max", "maximum (signed)"], ["min", "minimum (signed)"]], { label: "Value", id: "gSbrMode" }),
        F.select(o, "op", [[">=", "≥"], [">", ">"], ["<=", "≤"], ["<", "<"]], { label: "Operator", id: "gSbrOp" }),
        thrBox,
      ]),
      gpGroup("Selection", [
        F.select(o, "group", [["", "(all objects)"], ...Object.keys((S.model && S.model.groups) || {}).map(g => [g, g])], { label: "Only objects of group", id: "gSbrGroup" }),
        F.check(o, "add", { label: "Add to the current selection (else replace it)", id: "gSbrAdd" }),
      ]),
    ];
    syncNote();
    return gpDialog({
      id: "gSbrDlg", title: "Select by Result", size: "md", form: F, body, okLabel: "Select",
      intro: "Select the objects whose analysis result meets a criterion — then assign, group or inspect them.",
      onOk: async () => {
        const e = api.entry(o.case);
        if (!e) return "Pick a case.";
        if (api.core.needsStepData(e) && !api.stepData(e.name)) await api.loadSteps({ key: o.case, envelope: o.env !== "step" });
        const res = selectByResult({ ...o, env: o.env === "step" ? (o.case === cur.key ? cur.env : "step") : "maxmin" });
        toast("Select by Result", `${res.refs.length} object(s) ${o.add ? "added to" : "selected"} — ${QLABEL[o.quantity] || o.quantity} ${o.op} ${gPU.fmt(unitKind(), +o.threshold, 3)} ${gPU.label(unitKind())}`, "info", 4500);
        api._lastSbr = res;
        return true;
      },
    });
  }

  /* ================================================================ watch list */
  const probes = [];
  const host = document.getElementById("etabsCanvas") || document.body;
  const win = gpEl("section", { class: "g-watch hidden", id: "gWatch", role: "region", "aria-label": "Result watch list" });
  const addB = gpEl("button", { type: "button", id: "gWatchAdd", title: "Add a probe" }, ["Add…"]);
  const csvB = gpEl("button", { type: "button", id: "gWatchCsv", title: "Export to CSV" }, ["CSV"]);
  const clrB = gpEl("button", { type: "button", id: "gWatchClear", title: "Remove every probe" }, ["Clear"]);
  const xB = gpEl("button", { type: "button", id: "gWatchClose", title: "Hide", "aria-label": "Hide watch list" }, ["×"]);
  const wbody = gpEl("div", { class: "g-w-body", id: "gWatchBody" });
  win.append(gpEl("header", {}, [gpEl("h3", { text: "Watch List" }), addB, csvB, clrB, xB]), wbody);
  host.appendChild(win);
  addB.addEventListener("click", () => openAddProbe());
  csvB.addEventListener("click", () => exportCsv());
  clrB.addEventListener("click", () => { probes.length = 0; renderWatch(); });
  xB.addEventListener("click", () => win.classList.add("hidden"));

  function probeLabel(p) {
    const pt = p.point ? `(${p.point.map(v => gPU.fmt("length", v, 2)).join(", ")})` : "";
    switch (p.type) {
      case "joint_disp": return `Joint ${pt} ${p.comp}`;
      case "joint_drift": return `Joint ${pt} drift ${p.dir || "X"}`;
      case "reaction": return `Reaction ${pt} ${p.comp}`;
      case "member_end": return `${p.uid} ${gP.COMP_LABEL[p.comp]} end ${p.end === "j" ? "J" : "I"}`;
      case "member_max": return `${p.uid} max |${gP.COMP_LABEL[p.comp]}|`;
      case "member_at": return `${p.uid} ${gP.COMP_LABEL[p.comp]} @ ${gPU.fmt("length", p.x, 2)} ${gPU.label("length")}`;
      case "pier": return `Pier ${p.label} ${p.story} ${p.comp === "V" ? "V2" : p.comp === "M" ? "M3" : "P"}`;
      case "shell": return `${p.region} el.${p.quad} ${p.comp}${/^S/.test(p.comp) ? " " + (p.face === "bot" ? "bot" : "top") : ""}`;
      case "story_drift": return `Story ${p.story} drift ${p.dir || "X"}`;
      case "link": return `Link ${p.uid} ${p.comp}`;
      default: return p.type;
    }
  }
  const valueOf = p => gP.probeValue({ model: S.model, results: S.results, state: api.state(), linkForces: api.linkForces ? api.linkForces() : {} }, p);
  const fmtVal = (kind, v) => (v == null || !isFinite(v) ? "—" : kind === "none" || kind === "ratio" ? (+v).toPrecision(4) : gPU.fmt(kind, v, 3));
  const unitOf = kind => (kind === "none" || kind === "ratio" ? "" : gPU.label(kind));
  function renderWatch() {
    if (win.classList.contains("hidden")) return;
    wbody.textContent = "";
    const st = api.state();
    wbody.appendChild(gpEl("div", { class: "g-w-case", id: "gWatchCase", text: st ? `${st.label}${st.stepLabel ? " · " + st.stepLabel : ""}` : "No results" }));
    if (!probes.length) { wbody.appendChild(gpEl("div", { class: "g-w-empty", html: "No probes yet — pin values with 📌 in the member detail window or <b>Add…</b>." })); return; }
    const t = gpEl("table", { id: "gWatchTable" });
    t.appendChild(gpEl("tr", {}, ["Probe", "Value", "Unit", ""].map(h => gpEl("th", { text: h }))));
    probes.forEach((p, i) => {
      const v = valueOf(p);
      const x = gpEl("button", { type: "button", class: "g-w-x", title: "Remove", "aria-label": "Remove probe" }, ["✕"]);
      x.addEventListener("click", () => { probes.splice(i, 1); renderWatch(); });
      t.appendChild(gpEl("tr", { "data-probe": String(i) }, [gpEl("td", { text: probeLabel(p), title: probeLabel(p) }), gpEl("td", { class: "g-w-val", text: fmtVal(v.kind, v.value) }), gpEl("td", { text: unitOf(v.kind) }), gpEl("td", {}, [x])]));
    });
    wbody.appendChild(t);
  }
  function rows() {
    const st = api.state();
    const out = [["probe", "case", "step", "value", "unit"]];
    for (const p of probes) {
      const v = valueOf(p);
      out.push([probeLabel(p), st ? st.label : "", st ? st.stepLabel || "" : "", v.value == null ? "" : (v.kind === "none" || v.kind === "ratio" ? v.value : gPU.toDisplay(v.kind, v.value)), unitOf(v.kind)]);
    }
    return out;
  }
  function exportCsv() {
    const csv = gP.toCsv(rows());
    api._lastWatchCsv = csv;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    a.download = `${String((S.model && S.model.name) || "model").replace(/[^\w.-]+/g, "_")}_watch.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    return csv;
  }
  function pin(p) {
    if (!p || !gP.PROBE_TYPES.includes(p.type)) return false;
    const key = JSON.stringify(p);
    if (!probes.some(q => JSON.stringify(q) === key)) probes.push(JSON.parse(key));
    openWatch();
    return true;
  }
  function openWatch() { win.classList.remove("hidden"); renderWatch(); return win; }

  function openAddProbe() {
    if (!S.results) { toast("Watch list", "Run the analysis first (F5)."); return null; }
    const m = S.model, r = S.results;
    const selM = (S.selection || []).find(x => x.type === "member");
    const o = { type: "member_end", uid: selM ? selM.uid : ((m.members || [])[0] || {}).uid, end: "i", comp: "M3", x: 0, y: 0, z: 0, label: "", story: ((m.stories || []).slice(-1)[0] || {}).name || "", dir: "X", region: ((m.shells || [])[0] || {}).uid || "", quad: 0, face: "top" };
    const sel1 = (S.selection || []).find(x => x.type === "joint");
    if (sel1 && sel1.point) [o.x, o.y, o.z] = sel1.point;
    else if (selM) { const mm = m.members.find(x => x.uid === selM.uid); if (mm) [o.x, o.y, o.z] = mm.pj; }
    const F = gpForm();
    const compsFor = () => (o.type.startsWith("member") ? gP.ST_KEYS.map(k => [k, gP.COMP_LABEL[k]]) : o.type === "joint_disp" ? gP.DISP_COMPS.map(k => [k, k]) :
      o.type === "reaction" ? ["FX", "FY", "FZ", "MX", "MY", "MZ"].map(k => [k, k]) : o.type === "pier" ? [["V", "V2"], ["M", "M3"], ["P", "P"]] :
        o.type === "shell" ? [...Object.keys(gP.SHELL_FORCE_IDX), "S11", "S22", "S12", "SMax", "SMin", "SVM"].map(k => [k, k]) : o.type === "link" ? ["U1", "U2", "U3", "R1", "R2", "R3"].map(k => [k, k]) : [["-", "—"]]);
    const body = [
      gpGroup("Probe", [
        F.select(o, "type", [["member_end", "Member end force"], ["member_max", "Member max |value|"], ["joint_disp", "Joint displacement"], ["joint_drift", "Joint drift ratio"],
          ["reaction", "Support reaction"], ["pier", "Pier force"], ["shell", "Shell element force / stress"], ["story_drift", "Story drift"], ["link", "Link force"]],
          { label: "Type", id: "gProbeType", onChange: () => { o.comp = compsFor()[0][0]; F.refresh(); } }),
        F.select(o, "comp", compsFor, { label: "Component", id: "gProbeComp" }),
        F.select(o, "uid", () => (o.type === "link" ? (m.links || []).map(l => [l.uid, l.uid]) : (m.members || []).map(x => [x.uid, x.uid])), { label: "Member / link", id: "gProbeUid" }),
        F.select(o, "end", [["i", "End I"], ["j", "End J"]], { label: "End", id: "gProbeEnd" }),
        F.num(o, "x", { label: "Joint X", kind: "length", id: "gProbeX" }),
        F.num(o, "y", { label: "Joint Y", kind: "length", id: "gProbeY" }),
        F.num(o, "z", { label: "Joint Z", kind: "length", id: "gProbeZ" }),
        F.select(o, "dir", [["X", "X"], ["Y", "Y"]], { label: "Drift direction", id: "gProbeDir" }),
        F.select(o, "label", () => [["", "—"], ...Object.keys(r.piers ? Object.values(r.piers)[0] || {} : {}).map(l => [l, l])], { label: "Pier", id: "gProbePier" }),
        F.select(o, "story", (m.stories || []).map(s => [s.name, s.name]), { label: "Story", id: "gProbeStory" }),
        F.select(o, "region", (m.shells || []).map(s => [s.uid, s.uid]), { label: "Shell", id: "gProbeShell" }),
        F.num(o, "quad", { label: "Element index", int: true, min: 0, id: "gProbeQuad" }),
        F.select(o, "face", [["top", "Top"], ["bot", "Bottom"]], { label: "Face (stresses)", id: "gProbeFace" }),
      ]),
      gpNote("Only the fields of the chosen type are used. Joints are matched by coordinates, so a probe survives a re-run."),
    ];
    return gpDialog({
      id: "gProbeDlg", title: "Add Result Probe", size: "sm", form: F, body, okLabel: "Pin",
      onOk: () => {
        const pt = [+o.x, +o.y, +o.z];
        let p;
        switch (o.type) {
          case "member_end": p = { type: o.type, uid: o.uid, end: o.end, comp: o.comp }; break;
          case "member_max": p = { type: o.type, uid: o.uid, comp: o.comp }; break;
          case "joint_disp": p = { type: o.type, point: pt, comp: o.comp }; break;
          case "joint_drift": p = { type: o.type, point: pt, dir: o.dir }; break;
          case "reaction": p = { type: o.type, point: pt, comp: o.comp }; break;
          case "pier": if (!o.label) return "Pick a pier."; p = { type: o.type, label: o.label, story: o.story, comp: o.comp }; break;
          case "shell": p = { type: o.type, region: o.region, quad: (r.shell_quads || []).findIndex((q, i) => q.region === o.region && i >= +o.quad) , comp: o.comp, face: o.face }; if (p.quad < 0) return "That shell has no meshed element at this index."; break;
          case "story_drift": p = { type: o.type, story: o.story, dir: o.dir }; break;
          case "link": p = { type: o.type, uid: o.uid, comp: o.comp }; break;
          default: return "Pick a probe type.";
        }
        pin(p);
        return true;
      },
    });
  }

  api.subscribe(() => renderWatch());
  document.addEventListener("sky:results-changed", () => setTimeout(renderWatch, 0));
  gPU.onUnitsChange && gPU.onUnitsChange(() => renderWatch());

  Object.assign(api, { pin, unpin: i => { probes.splice(i, 1); renderWatch(); }, probes: () => probes.map(p => ({ ...p, label: probeLabel(p), ...valueOf(p) })),
    openWatch, closeWatch: () => win.classList.add("hidden"), watchCsv: () => gP.toCsv(rows()), exportWatchCsv: exportCsv,
    openSelectByResult, selectByResult, openAddProbe });

  if (sky.menu && sky.menu.add) {
    // Select menu is full (14): the attribute pickers move into "More Criteria ▸" (etabs.js declares it) and By Result joins them
    const sub = "Select/More Criteria";
    for (const act of ["sel-w8-label", "sel-w8-linkprop", "sel-w8-pattern"]) {
      const it = sky.menu.item(act);
      if (!it) continue;
      const path = sky.menu.path(act) || [];
      if (path.length > 1) continue;
      if (sky.menu.remove(act)) sky.menu.add(sub, it);
    }
    sky.menu.add(sub, { label: "By Result…", act: "sel-g-byresult", needs: "results", hint: "member forces · joint drift · shell stress · links", fn: () => openSelectByResult() });
    sky.menu.add("Tools", { label: "Result Watch List", act: "tool-g-watch", needs: "results", hint: "pinned result probes · live · CSV", fn: () => openWatch() });
  }
}
