/* SkyFrame — ETABS Define > Spring Properties > Point Springs… and
   Assign > Joint > Springs… (CONTRACT "Named spring properties and link
   hysteresis types", B9).

     model.spring_properties  {name: {kind, k?, curves?, nonlinear_dof?, gap?,
                                      local_axes?}}   (absent when empty)
     spring_supports[i]       {point, stiffness, property?, angle_deg?}
                              property set ⇒ inline stiffness all zero

   Every dialog edits a draft and writes only on OK (Cancel / Esc discard);
   untouched keys are never added, so OK with defaults is byte-identical.
   Stiffness / displacement / force go through units.js; the store is SI. */

import { dialog as spDialog, btn as spBtn, footBar as spFootBar, errorLine as spErrorLine,
  showError as spShowError } from "./analysisdlg.js";
import SPU from "./units.js";
import { selectionJoints as spSelectionJoints, supportJoints as spSupportJoints } from "./assigndlg.js";
import { b9h as spH, b9group as spGroup, b9num as spNum, b9after as spAfter, b9css as spCss,
  b9esc as spEsc, b9clone as spClone, b9same as spSame, b9curveSvg as spCurveSvg } from "./b9common.js";

export const SPRING_KINDS = [
  ["linear", "Linear"], ["multilinear", "Multilinear elastic"], ["compression_only", "Compression only"],
  ["tension_only", "Tension only"], ["gap", "Gap (compression after opening)"]];
export const DOF_LABELS = ["U1", "U2", "U3", "R1", "R2", "R3"];
const kKind = i => i < 3 ? "stiffness" : "rot_stiffness";
const dKind = i => i < 3 ? "disp" : "rotation";
const fKind = i => i < 3 ? "force" : "moment";
const isNum = v => typeof v === "number" && isFinite(v);
const near = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) < 1e-6;
const fmtPt = p => "(" + p.map(v => SPU.fmt("length", v, 2)).join(", ") + ") " + SPU.label("length");

/* ------------------------------------------------ force laws (preview) */
export function normalizeCurve(pts) {
  let p = pts.map(q => [+q[0], +q[1]]);
  if (p.length && p.every(q => q[0] > 0)) p = p.slice().reverse().map(q => [-q[0], -q[1]]).concat([[0, 0]], p);
  return p;
}
/** Sample a DOF law → {pts, marks}. */
export function lawPoints(prop, i) {
  const k = (prop.k || [0, 0, 0, 0, 0, 0])[i] || 0;
  const lab = DOF_LABELS[i];
  const D0 = i < 3 ? 0.02 : 0.01;
  if (prop.kind === "multilinear" && prop.curves && Array.isArray(prop.curves[lab]) && prop.curves[lab].length) {
    const c = normalizeCurve(prop.curves[lab]);
    const ext = (a, b, d) => [d, a[1] + (b[1] - a[1]) * (d - a[0]) / ((b[0] - a[0]) || 1)];
    const lo = c[0][0] - 0.15 * Math.abs(c[c.length - 1][0] - c[0][0]);
    const hi = c[c.length - 1][0] + 0.15 * Math.abs(c[c.length - 1][0] - c[0][0]);
    const pts = c.length > 1 ? [ext(c[0], c[1], lo), ...c, ext(c[c.length - 2], c[c.length - 1], hi)] : c;
    return { pts, marks: prop.curves[lab].map(q => [+q[0], +q[1]]) };
  }
  const nl = prop.nonlinear_dof || "U3";
  if (["compression_only", "tension_only", "gap"].includes(prop.kind) && lab === nl) {
    if (prop.kind === "gap") {
      const g = isNum(prop.gap) ? prop.gap : 0;
      const D = Math.max(3 * g, D0);
      return { pts: [[D, 1e-6 * k * D], [-g, -1e-6 * k * g], [-D, -k * (D - g) - 1e-6 * k * D]], marks: [[-g, 0]] };
    }
    const D = D0;
    return prop.kind === "compression_only"
      ? { pts: [[D, 1e-6 * k * D], [0, 0], [-D, -k * D]], marks: [] }
      : { pts: [[-D, -1e-6 * k * D], [0, 0], [D, k * D]], marks: [] };
  }
  return { pts: [[-D0, -k * D0], [D0, k * D0]], marks: [] };
}

/** Client mirror of springprops.validate_property → message | null. */
export function validateSpringProp(name, p) {
  const w = `Spring property "${name}"`;
  if (!String(name || "").trim()) return "Spring property names must be non-empty.";
  if (!SPRING_KINDS.some(([k]) => k === p.kind)) return `${w}: choose a kind.`;
  const k = p.k;
  if (k == null && p.kind !== "multilinear") return `${w}: the 6 stiffnesses k are required.`;
  if (k != null && !(Array.isArray(k) && k.length === 6 && k.every(v => isNum(v) && v >= 0))) return `${w}: k must be 6 values ≥ 0.`;
  const kv = k || [0, 0, 0, 0, 0, 0];
  if (p.kind === "linear" && !kv.some(v => v > 0)) return `${w}: at least one stiffness must be > 0.`;
  if (p.kind === "multilinear") {
    const cs = p.curves || {};
    if (!Object.keys(cs).length) return `${w}: a multilinear spring needs at least one DOF curve.`;
    for (const [lab, pts] of Object.entries(cs)) {
      if (!pts.length || !pts.every(q => q.length === 2 && q.every(isNum))) return `${w} ${lab}: enter finite (d, F) pairs.`;
      for (let j = 1; j < pts.length; j++) if (!(pts[j][0] > pts[j - 1][0])) return `${w} ${lab}: d must be strictly increasing.`;
      if (pts.every(q => q[0] > 0)) { if (!(pts[0][1] > 0)) return `${w} ${lab}: the first force must be > 0.`; continue; }
      if (!pts.some(q => q[0] === 0 && q[1] === 0)) return `${w} ${lab}: a curve with d ≤ 0 points must pass through (0, 0).`;
      if (pts.length < 2) return `${w} ${lab}: a curve needs at least 2 points.`;
      const c = normalizeCurve(pts);
      const j0 = c.findIndex(q => q[0] === 0);
      const s = j0 < c.length - 1 ? (c[j0 + 1][1] - c[j0][1]) / (c[j0 + 1][0] - c[j0][0]) : (c[j0][1] - c[j0 - 1][1]) / (c[j0][0] - c[j0 - 1][0]);
      if (!(s > 0)) return `${w} ${lab}: the curve must have a positive slope at the origin.`;
    }
  } else if (p.kind !== "linear") {
    const lab = p.nonlinear_dof || "U3";
    if (!(kv[DOF_LABELS.indexOf(lab)] > 0)) return `${w}: k on the nonlinear DOF ${lab} must be > 0.`;
    if (p.kind === "gap" && !(isNum(p.gap) && p.gap >= 0)) return `${w}: gap must be ≥ 0.`;
  }
  return null;
}

export function initSprings(sky) {
  spCss();
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);

  /* ================= Define > Spring Properties > Point Springs ================= */
  function openSpringProperties(pick) {
    const m = S.model;
    if (!m) return null;
    const orig = m.spring_properties || {};
    const draft = spClone(orig);
    const renames = {};                          // new name -> original name
    let cur = pick && draft[pick] ? pick : Object.keys(draft)[0] || null;
    let curDof = 0;
    /** DOF worth plotting first: the nonlinear one, a curve, else the first k > 0. */
    const pickDof = p => {
      if (!p) return 0;
      if (["compression_only", "tension_only", "gap"].includes(p.kind)) return DOF_LABELS.indexOf(p.nonlinear_dof || "U3");
      if (p.kind === "multilinear" && p.curves) { const i = DOF_LABELS.findIndex(l => p.curves[l]); if (i >= 0) return i; }
      const i = (p.k || []).findIndex(v => v > 0);
      return i >= 0 ? i : 0;
    };
    curDof = pickDof(draft[cur]);
    const body = spH("div", { class: "b9-sp" });
    body.appendChild(spH("p", { class: "muted dlg-intro", text:
      "Named point-spring properties (LOCAL axes 1-2-3; compression = negative local deformation). Assign them to joints with Assign > Joint > Springs…. Nonlinear kinds act nonlinearly in static, pushover, staged and direct-integration time-history cases; modal / RS / buckling use the initial stiffness." }));
    const grid = spH("div", { class: "b9-sp-grid" });
    const left = spH("div", { class: "b9-sp-list-wrap" });
    const list = spH("div", { class: "b9-sp-list", id: "spPropList", role: "listbox" });
    const nameOf = base => { let i = 1, n = base; while (draft[n]) n = base + (++i); return n; };
    const addBtn = spBtn("Add", "btn-small", () => {
      const n = nameOf("SPR");
      draft[n] = { kind: "linear", k: [0, 0, 10000, 0, 0, 0] };
      cur = n; curDof = pickDof(draft[n]); renderAll();
    }, "Add a new linear spring property");
    addBtn.id = "spPropAdd";
    const copyBtn = spBtn("Copy", "btn-small", () => {
      if (!cur) return;
      const n = nameOf(cur + "_COPY");
      draft[n] = spClone(draft[cur]); cur = n; curDof = pickDof(draft[n]); renderAll();
    });
    copyBtn.id = "spPropCopy";
    const delBtn = spBtn("Delete", "btn-small", () => {
      if (!cur) return;
      const on = (m.spring_supports || []).filter(s => s.property === (renames[cur] || cur)).length;
      if (on) { spShowError(err, `"${cur}" is assigned to ${on} spring support${on > 1 ? "s" : ""} — reassign them first (Assign > Joint > Springs…).`); return; }
      delete draft[cur]; delete renames[cur];
      cur = Object.keys(draft)[0] || null; renderAll();
    });
    delBtn.id = "spPropDelete";
    left.append(list, spH("div", { class: "b9-sp-list-btns" }, [addBtn, copyBtn, delBtn]));
    const right = spH("div", { class: "b9-sp-edit", id: "spPropEditor" });
    grid.append(left, right);
    body.appendChild(grid);
    const err = spErrorLine();
    body.appendChild(err);

    function renderList() {
      list.textContent = "";
      const names = Object.keys(draft);
      if (!names.length) list.appendChild(spH("p", { class: "muted b9-small", text: "No spring properties. Click Add." }));
      for (const n of names) {
        const used = (m.spring_supports || []).filter(s => s.property === (renames[n] || n)).length;
        const b = spH("button", { type: "button", class: "b9-sp-item" + (n === cur ? " is-sel" : ""), "data-name": n, role: "option" }, [
          spH("b", { text: n }), spH("span", { class: "muted", text: (SPRING_KINDS.find(x => x[0] === draft[n].kind) || ["", draft[n].kind])[1] + (used ? ` · ${used}×` : "") })]);
        b.addEventListener("click", () => { cur = n; curDof = pickDof(draft[n]); renderAll(); });
        list.appendChild(b);
      }
    }

    function renderEditor() {
      right.textContent = "";
      if (!cur) { right.appendChild(spH("p", { class: "muted", text: "Select or add a spring property." })); return; }
      const p = draft[cur];
      /* name + kind + axes */
      const nameIn = spH("input", { type: "text", id: "spPropName", value: cur, spellcheck: "false", autocomplete: "off" });
      nameIn.addEventListener("change", () => {
        const nn = nameIn.value.trim();
        if (!nn || nn === cur) { nameIn.value = cur; return; }
        if (draft[nn]) { spShowError(err, `A property named "${nn}" already exists.`); nameIn.value = cur; return; }
        const rebuilt = {};
        for (const [k, v] of Object.entries(draft)) rebuilt[k === cur ? nn : k] = v;
        for (const k of Object.keys(draft)) delete draft[k];
        Object.assign(draft, rebuilt);
        renames[nn] = renames[cur] || cur; delete renames[cur];
        cur = nn; renderAll();
      });
      const kindSel = spH("select", { id: "spPropKind" });
      kindSel.innerHTML = SPRING_KINDS.map(([k, t]) => `<option value="${k}"${k === p.kind ? " selected" : ""}>${spEsc(t)}</option>`).join("");
      kindSel.addEventListener("change", () => {
        const kd = kindSel.value;
        p.kind = kd;
        if (kd !== "multilinear") { delete p.curves; if (!p.k) p.k = [0, 0, 10000, 0, 0, 0]; }
        else if (!p.curves || !Object.keys(p.curves).length) p.curves = { U1: [[0.01, 100], [0.03, 150]] };
        if (!["compression_only", "tension_only", "gap"].includes(kd)) { delete p.nonlinear_dof; delete p.gap; }
        if (kd === "gap" && !isNum(p.gap)) p.gap = 0.01;
        if (kd !== "gap") delete p.gap;
        if (kd !== "linear" && kd !== "multilinear") {
          const lab = p.nonlinear_dof || "U3", i = DOF_LABELS.indexOf(lab);
          if (!(p.k[i] > 0)) p.k[i] = 10000;
        }
        curDof = pickDof(p);
        renderAll();
      });
      const la = p.local_axes;
      const isMatrix = Array.isArray(la);
      const ang = spNum("none", la && !isMatrix ? (la.angle_deg ?? 0) : 0, { id: "spPropAngle", step: "any" });
      ang.el.disabled = isMatrix;
      ang.el.addEventListener("change", () => {
        const v = ang.get();
        if (v == null || v === 0) delete p.local_axes;
        else if (isFinite(v)) p.local_axes = { angle_deg: v };
        else ang.set(la && !isMatrix ? (la.angle_deg ?? 0) : 0);
        renderPreview();
      });
      const top = spH("div", { class: "asn-grid2 b9-sp-top" }, [
        spH("label", { class: "asn-field" }, [spH("span", { text: "Property name" }), nameIn]),
        spH("label", { class: "asn-field" }, [spH("span", { text: "Kind" }), kindSel]),
        spH("label", { class: "asn-field" }, [spH("span", { text: "Local axes angle (plan, about +Z) · deg" }), ang.el]),
      ]);
      if (isMatrix) {
        const rm = spBtn("Use angle instead", "btn-small", () => { delete p.local_axes; renderAll(); });
        top.appendChild(spH("div", { class: "asn-field" }, [spH("span", { class: "muted", text: "3×3 local-axes matrix (kept as given)" }), rm]));
      }
      /* nonlinear dof + gap */
      if (["compression_only", "tension_only", "gap"].includes(p.kind)) {
        const nd = spH("select", { id: "spPropNlDof" });
        nd.innerHTML = DOF_LABELS.map(l => `<option${l === (p.nonlinear_dof || "U3") ? " selected" : ""}>${l}</option>`).join("");
        nd.addEventListener("change", () => {
          if (nd.value === "U3" && !("nonlinear_dof" in p)) return;
          p.nonlinear_dof = nd.value; curDof = DOF_LABELS.indexOf(nd.value); renderAll();
        });
        top.appendChild(spH("label", { class: "asn-field" }, [spH("span", { text: "Nonlinear DOF" }), nd]));
        if (p.kind === "gap") {
          const g = spNum("disp", p.gap, { id: "spPropGap", min: "0" });
          g.el.addEventListener("change", () => { const v = g.get(); if (isFinite(v) && v >= 0) p.gap = v; renderPreview(); });
          top.appendChild(spH("label", { class: "asn-field" }, [spH("span", { text: `Gap opening · ${SPU.label("disp")}` }), g.el]));
        }
      }
      right.appendChild(top);

      /* stiffness table */
      const gk = spGroup(p.kind === "multilinear" ? "Stiffness (DOFs without a curve) and curves" : "Stiffness (local axes)");
      const tbl = spH("div", { class: "b9-sp-k", id: "spPropK" });
      tbl.appendChild(spH("div", { class: "b9-sp-krow head" }, [spH("span", { text: "DOF" }), spH("span", { text: "k" }), spH("span", { text: "" }),
        p.kind === "multilinear" ? spH("span", { text: "Curve" }) : spH("span", { text: "Law" }), spH("span", { text: "" })]));
      DOF_LABELS.forEach((lab, i) => {
        const kv = p.k ? p.k[i] : null;
        const f = spNum(kKind(i), kv, { "data-dof": lab, class: "spK", min: "0", placeholder: p.kind === "multilinear" ? "—" : "0" });
        f.el.addEventListener("change", () => {
          const v = f.get();
          if (v == null) { if (p.kind === "multilinear" && p.k) { p.k[i] = 0; if (p.k.every(x => x === 0)) delete p.k; } else if (p.k) p.k[i] = 0; }
          else if (isFinite(v) && v >= 0) { p.k = p.k || [0, 0, 0, 0, 0, 0]; p.k[i] = v; }
          else { f.set(kv); return; }
          renderPreview(); renderList();
        });
        let law;
        if (p.kind === "multilinear") {
          const has = !!(p.curves && p.curves[lab]);
          const c = spH("input", { type: "checkbox", "data-dof": lab, class: "spCurveOn" });
          c.checked = has;
          c.addEventListener("change", () => {
            p.curves = p.curves || {};
            if (c.checked) p.curves[lab] = i < 3 ? [[0.01, 100], [0.03, 150]] : [[0.002, 50], [0.01, 80]];
            else delete p.curves[lab];
            curDof = i; renderAll();
          });
          law = spH("label", { class: "dlg-chk" }, [c, spH("span", { text: has ? "curve" : "" })]);
        } else {
          const nl = ["compression_only", "tension_only", "gap"].includes(p.kind) && lab === (p.nonlinear_dof || "U3");
          law = spH("span", { class: nl ? "b9-nl" : "muted", text: nl ? (p.kind === "gap" ? "gap" : p.kind === "compression_only" ? "C-only" : "T-only") : (kv > 0 ? "linear" : "free") });
        }
        const view = spH("button", { type: "button", class: "b9-sp-view" + (i === curDof ? " is-sel" : ""), text: "plot", title: `Preview ${lab}` });
        view.addEventListener("click", () => { curDof = i; renderAll(); });
        tbl.appendChild(spH("div", { class: "b9-sp-krow" + (i === curDof ? " is-sel" : "") }, [spH("b", { text: lab }), f.el,
          spH("span", { class: "asn-unit", text: SPU.label(kKind(i)) }), law, view]));
      });
      gk.appendChild(tbl);

      /* curve table + preview */
      const lower = spH("div", { class: "b9-sp-lower" });
      const lab = DOF_LABELS[curDof];
      if (p.kind === "multilinear" && p.curves && p.curves[lab]) {
        const pts = p.curves[lab];
        const gc = spGroup(`Curve ${lab} — (d, F) points`);
        gc.appendChild(spH("p", { class: "muted b9-small", text: "All d > 0: mirrored through the origin. Otherwise include (0, 0). Elastic: loads and unloads on the curve; the end segments extrapolate." }));
        const ct = spH("div", { class: "b9-pts", id: "spCurveTable" });
        ct.appendChild(spH("div", { class: "b9-pts-row head" }, [spH("span", { text: `d (${SPU.label(dKind(curDof))})` }), spH("span", { text: `F (${SPU.label(fKind(curDof))})` }), spH("span")]));
        pts.forEach((q, j) => {
          const fd = spNum(dKind(curDof), q[0], { "data-j": j, "data-c": 0 });
          const ff = spNum(fKind(curDof), q[1], { "data-j": j, "data-c": 1 });
          for (const [f, c] of [[fd, 0], [ff, 1]]) f.el.addEventListener("change", () => {
            const v = f.get();
            if (!isFinite(v)) { f.set(q[c]); return; }
            q[c] = v; renderPreview();
          });
          const x = spH("button", { type: "button", class: "chip-x", text: "✕", title: "Remove point" });
          x.addEventListener("click", () => { if (pts.length <= 1) return; pts.splice(j, 1); renderAll(); });
          ct.appendChild(spH("div", { class: "b9-pts-row" }, [fd.el, ff.el, x]));
        });
        const add = spBtn("+ Point", "btn-small", () => {
          const last = pts[pts.length - 1] || [0, 0];
          pts.push([+(last[0] + (curDof < 3 ? 0.02 : 0.005)).toPrecision(6) * 1, +(last[1] * 1.1 + 10).toPrecision(6) * 1]);
          renderAll();
        });
        add.id = "spCurveAdd";
        gc.append(ct, add);
        lower.appendChild(gc);
      }
      const gp = spGroup(`Force–deformation · ${lab}`, "b9-prev");
      gp.appendChild(spH("div", { class: "b9-prev-box", id: "spPreview" }));
      lower.appendChild(gp);
      right.append(gk, lower);
      renderPreview();
    }
    function renderPreview() {
      const box = right.querySelector("#spPreview");
      if (!box || !cur) return;
      const p = draft[cur];
      const { pts, marks } = lawPoints(p, curDof);
      box.innerHTML = spCurveSvg([{ pts, cls: "b9-s-main" }], { xKind: dKind(curDof), yKind: fKind(curDof), marks, title: `${cur} · ${DOF_LABELS[curDof]}` });
      const msg = validateSpringProp(cur, p);
      spShowError(err, msg || "");
    }
    function renderAll() { renderList(); renderEditor(); }

    const apply = () => {
      for (const [n, p] of Object.entries(draft)) {
        const msg = validateSpringProp(n, p);
        if (msg) { cur = n; renderAll(); spShowError(err, msg); return false; }
      }
      const changed = !spSame(draft, orig) || Object.keys(renames).some(k => renames[k] !== k);
      if (!changed) return true;
      for (const s of m.spring_supports || []) {
        if (!s.property) continue;
        const nn = Object.keys(renames).find(k => renames[k] === s.property);
        if (nn) s.property = nn;
      }
      if (Object.keys(draft).length) m.spring_properties = draft; else delete m.spring_properties;
      spAfter(sky, "spring-properties");
      return true;
    };
    const fb = spFootBar("Stiffness kN/m · kN·m/rad in the model (shown in display units).", [
      spBtn("Cancel", "", () => dlg.close()),
      spBtn("OK", "btn-primary", () => { if (apply()) dlg.close(); }),
    ]);
    fb.wrap.querySelector(".btn-primary").id = "spPropOk";
    const dlg = spDialog("spPropsDlg", { title: "Define Point Spring Properties", iconId: "tool-spring", wide: true, body, foot: fb.wrap });
    renderAll();
    return dlg;
  }

  /* ================= Assign > Joint > Springs ================= */
  function openJointSprings() {
    const m = S.model;
    if (!m) return null;
    const props = Object.keys(m.spring_properties || {});
    const joints = spSelectionJoints(m, S.selection).map(p => ({ p, on: true }));
    const springAt = p => (m.spring_supports || []).find(s => near(s.point, p)) || null;
    const first = joints.map(j => springAt(j.p)).find(Boolean) || null;
    let mode = first ? (first.property ? "property" : "inline") : null;   // nothing picked → OK writes nothing
    let touched = false;
    const body = spH("div", { class: "b9-js" });
    body.appendChild(spH("p", { class: "muted dlg-intro", text: "Grounded point springs at the joints below. A named property acts on its local axes (rotated further by the angle); inline stiffness acts on the global axes, rotated about Z by the angle when it is not 0." }));
    const gj = spGroup("Joints");
    const jl = spH("div", { class: "b9-jlist", id: "jsJoints" });
    const addSup = spBtn("+ Base / support joints", "btn-small", () => {
      for (const p of spSupportJoints(m)) if (!joints.some(j => near(j.p, p))) joints.push({ p, on: true });
      renderJoints();
    }, "Add every support / spring joint");
    addSup.id = "jsAddSupports";
    const clr = spBtn("Clear", "btn-small", () => { joints.length = 0; renderJoints(); });
    gj.append(jl, spH("div", { class: "b9-jbtns" }, [addSup, clr]));
    body.appendChild(gj);
    function renderJoints() {
      jl.textContent = "";
      if (!joints.length) jl.appendChild(spH("p", { class: "muted b9-small", text: "No joints — select frames / springs / supports first, or add the base joints." }));
      joints.forEach(j => {
        const s = springAt(j.p);
        const c = spH("input", { type: "checkbox" });
        c.checked = j.on;
        c.addEventListener("change", () => { j.on = c.checked; });
        const st = !s ? "no spring" : s.property ? `property ${s.property}` : "inline " + s.stiffness.map(v => SPU.fmt("stiffness", v, 0)).join("/");
        jl.appendChild(spH("label", { class: "dlg-chk b9-jrow" }, [c, spH("span", { text: fmtPt(j.p) }),
          spH("span", { class: "muted", text: st + (s && s.angle_deg ? ` · ${s.angle_deg}°` : "") })]));
      });
    }
    renderJoints();

    const gs = spGroup("Spring");
    const radio = (k, t) => {
      const r = spH("input", { type: "radio", name: "jsMode", value: k, id: "jsMode_" + k });
      r.checked = mode === k;
      r.addEventListener("change", () => { if (r.checked) { mode = k; touched = true; sync(); } });
      return spH("label", { class: "dlg-chk" }, [r, spH("span", { text: t })]);
    };
    const propSel = spH("select", { id: "jsProperty" });
    propSel.innerHTML = props.length ? props.map(n => `<option${first && first.property === n ? " selected" : ""}>${spEsc(n)}</option>`).join("")
      : `<option value="" disabled selected>— none defined —</option>`;
    propSel.addEventListener("change", () => { touched = true; });
    const defBtn = spBtn("Define…", "btn-small", () => { dlg.close(); openSpringProperties(); }, "Define > Spring Properties > Point Springs");
    const k0 = first && !first.property ? first.stiffness : [1e5, 1e5, 1e5, 0, 0, 0];
    const kf = DOF_LABELS.map((l, i) => {
      const f = spNum(kKind(i), k0[i], { min: "0", "data-dof": l, class: "jsK" });
      f.el.addEventListener("input", () => { touched = true; });
      return f;
    });
    const ang = spNum("none", first ? (first.angle_deg || 0) : 0, { id: "jsAngle" });
    ang.el.addEventListener("input", () => { touched = true; });
    const kGrid = spH("div", { class: "asn-grid3 b9-js-k", id: "jsInlineK" }, kf.map((f, i) =>
      spH("label", { class: "asn-num-row" }, [spH("span", { text: ["Kx", "Ky", "Kz", "Krx", "Kry", "Krz"][i] }), f.el, spH("span", { class: "asn-unit", text: SPU.label(kKind(i)) })])));
    gs.append(radio("property", "Named spring property"),
      spH("div", { class: "dlg-sub-row" }, [propSel, defBtn]),
      radio("inline", "Inline stiffness (global axes)"), kGrid,
      spH("label", { class: "asn-num-row b9-js-ang" }, [spH("span", { text: "Angle about +Z (rotates the spring axes)" }), ang.el, spH("span", { class: "asn-unit", text: "deg" })]),
      radio("delete", "Delete the springs at these joints"));
    body.appendChild(gs);
    const err = spErrorLine();
    body.appendChild(err);
    function sync() {
      propSel.disabled = mode !== "property" || !props.length;
      kf.forEach(f => { f.el.disabled = mode !== "inline"; });
      ang.el.disabled = !mode || mode === "delete";
    }
    sync();

    const apply = () => {
      if (!touched || !mode) return true;
      const pts = joints.filter(j => j.on).map(j => j.p);
      if (!pts.length) { spShowError(err, "Select at least one joint."); return false; }
      const a = ang.get();
      if (mode !== "delete" && !(a == null || isFinite(a))) { spShowError(err, "Angle must be a number."); return false; }
      if (mode === "property" && !propSel.value) { spShowError(err, "Define a spring property first."); return false; }
      let kv = null;
      if (mode === "inline") {
        kv = kf.map(f => f.get() == null ? 0 : f.get());
        if (!kv.every(v => isFinite(v) && v >= 0)) { spShowError(err, "Stiffnesses must be numbers ≥ 0."); return false; }
        if (!kv.some(v => v > 0)) { spShowError(err, "At least one stiffness must be > 0."); return false; }
      }
      m.spring_supports = m.spring_supports || [];
      let n = 0;
      for (const p of pts) {
        let s = springAt(p);
        if (mode === "delete") {
          if (s) { m.spring_supports.splice(m.spring_supports.indexOf(s), 1); n++; }
          continue;
        }
        if (!s) { s = { point: p.map(Number), stiffness: [0, 0, 0, 0, 0, 0] }; m.spring_supports.push(s); }
        if (mode === "property") { s.property = propSel.value; s.stiffness = [0, 0, 0, 0, 0, 0]; }
        else { delete s.property; s.stiffness = kv.slice(); }
        if (a == null || a === 0) delete s.angle_deg; else s.angle_deg = a;
        n++;
      }
      spAfter(sky, "joint-springs");
      toast("Joint Springs", `${mode === "delete" ? "Removed" : "Assigned"} ${n} spring${n === 1 ? "" : "s"}.`);
      return true;
    };
    const fb = spFootBar("A spring frees the joint's base fixity in the sprung DOFs.", [
      spBtn("Cancel", "", () => dlg.close()),
      spBtn("OK", "btn-primary", () => { if (apply()) dlg.close(); }),
    ]);
    fb.wrap.querySelector(".btn-primary").id = "jsOk";
    const dlg = spDialog("spJointDlg", { title: "Assign Joint Springs", iconId: "tool-spring", body, foot: fb.wrap });
    return dlg;
  }

  /* ================= properties panel (spring supports) ================= */
  function decorateProps(box, sel) {
    const sp = (sel && sel.springs) || [];
    if (!sp.length) return;
    const named = sp.filter(s => s.property);
    if (named.length) box.querySelectorAll(".springK").forEach(i => { i.disabled = true; i.title = "Uses a named spring property — edit it with Assign > Joint > Springs…"; });
    const one = arr => arr.every(v => v === arr[0]) ? arr[0] : "mixed";
    const wrap = spH("div", { class: "b9-props", id: "spPropsBlock" }, [
      spH("div", { class: "b9-props-row" }, [spH("span", { class: "muted", text: "Spring property" }), spH("b", { text: one(sp.map(s => s.property || "inline stiffness")) })]),
      spH("div", { class: "b9-props-row" }, [spH("span", { class: "muted", text: "Axes angle" }), spH("b", { text: one(sp.map(s => `${s.angle_deg || 0}°`)) })]),
      spH("div", { class: "b9-props-btns" }, [
        spBtn("Springs…", "btn-small", () => openJointSprings(), "Assign > Joint > Springs"),
        spBtn("Spring Properties…", "btn-small", () => openSpringProperties(named[0] && named[0].property), "Define > Spring Properties > Point Springs"),
      ]),
    ]);
    const grid = box.querySelector(".spring-stiff");
    if (grid && grid.nextElementSibling) grid.parentNode.insertBefore(wrap, grid.nextElementSibling.nextSibling);
    else box.appendChild(wrap);
  }

  sky.openSpringProperties = openSpringProperties;
  sky.openJointSprings = openJointSprings;
  sky.springProps = { openSpringProperties, openJointSprings, decorateProps, lawPoints, validateSpringProp };
  return sky.springProps;
}

