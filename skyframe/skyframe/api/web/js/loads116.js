/* SkyFrame — v1.16 load generation UI (CONTRACT "Temperature gradients,
   projected loads and auto-lateral generators (ASCE 7-22, EC8, IS 1893, user)").

     Assign → Frame Loads · Distributed…   member_loads udl / trapezoid + "projected"
     Assign → Frame Loads · Temperature…   thermal_loads {member_uid, dT, grad2?, grad3?}
     Assign → Shell Loads · Temperature…   shell_thermal_loads {region_uid, dT, grad3}
     Assign → Joint Loads · Temperature…   joint_temperatures {point, dT}
     Define → Auto Lateral Loads…          ETABS "Auto Lateral Load" per pattern:
        ASCE 7-16 (existing POST /api/pattern/elf), ASCE 7-22 (+ multi-period
        spectrum), EN 1998-1, IS 1893:2016, User Coefficient, User Loads,
        ASCE 7-22 wind — live Preview (POST /api/pattern/auto-lateral/preview)
        before Generate (POST /api/pattern/auto-lateral) writes the pattern.

   Every dialog edits a draft and writes the model only on OK / Generate, and
   only what changed (OK with defaults leaves the model byte-identical). The
   model store stays SI; every number goes through js/units.js (temperature
   gradients use the additive `temp_gradient` kind: °C/m, °F/in …).
   app.js calls initLoads116(window.__sky) once; js/etabs.js menu items and the
   js/loads.js pattern rows call the __sky entry points lazily. ?mock=1 uses
   js/mock_loads116.js (same formulas). */

import L116U from "./units.js";
import * as L116ME from "./modeledit.js";
import { dialog as l116Dialog, closeDialog as l116CloseDialog, btn as l116Btn, footBar as l116FootBar,
  errorLine as l116ErrorLine, showError as l116ShowError } from "./analysisdlg.js";
import { mockAutoLateralPreview as l116MockPreview, mockAutoLateralGenerate as l116MockGenerate,
  EC8_SPECTRA as L116_EC8_SPECTRA } from "./mock_loads116.js";

const U = L116U;
const ME = L116ME;
let SKY = null;

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const isNum = v => typeof v === "number" && isFinite(v);
const clone = o => JSON.parse(JSON.stringify(o));
const samePt = (a, b, tol = 1e-6) => !!a && !!b && a.length === 3 && b.length === 3 && a.every((v, i) => Math.abs(+v - +b[i]) < tol);
const G = 9.80665;

/* ------------------------------------------------ css + small DOM kit */
function ensureCss() {
  if (document.querySelector("link[data-l116-css]")) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/loads116.css"; l.setAttribute("data-l116-css", "1");
  document.head.appendChild(l);
}
function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of [].concat(kids)) if (c != null) n.append(c);
  return n;
}
function group(legend, cls = "") {
  const g = el("fieldset", { class: "dlg-group " + cls });
  g.appendChild(el("legend", { text: legend }));
  return g;
}
/** Number input bound to a units kind. get(): exact SI while untouched, null
    when blank, NaN when not numeric. */
function numField(kind, si, attrs = {}) {
  const i = el("input", { type: "number", step: "any", ...attrs });
  let base = si, shown = "", k = kind;
  const set = v => { base = v; shown = (v == null || !isFinite(v)) ? "" : U.inputValue(k, v); i.value = shown; };
  set(si);
  const get = () => {
    if (i.value === shown) return base;
    const t = String(i.value).trim();
    if (t === "") return null;
    return U.parse(k, t);
  };
  return { el: i, get, set, get kind() { return k; }, setKind(nk) { const v = get(); k = nk; set(v); } };
}
function numRow(lbl, f, hint) {
  const u = el("span", { class: "asn-unit", text: f.kind && f.kind !== "none" ? U.label(f.kind) : "" });
  const row = el("label", { class: "asn-num-row l116-num-row" }, [el("span", { class: "asn-num-lbl", html: lbl }), f.el, u]);
  if (hint) row.title = hint;
  row._unit = u;
  return row;
}
function radios(name, opts, value, onChange) {
  const wrap = el("div", { class: "asn-radios" });
  for (const [v, text] of opts) {
    const r = el("input", { type: "radio", name, value: v });
    r.checked = v === value;
    r.addEventListener("change", () => { if (r.checked) onChange(v); });
    wrap.appendChild(el("label", { class: "dlg-chk" }, [r, el("span", { text })]));
  }
  return wrap;
}
function selectEl(id, opts, value) {
  const s = el("select", { id });
  s.innerHTML = opts.map(([v, t]) => `<option value="${esc(v)}"${String(v) === String(value) ? " selected" : ""}>${esc(t)}</option>`).join("");
  return s;
}
function patternSelect(m, id) {
  const pats = ME.patternNames(m);
  const cur = (SKY && SKY.store.loadPattern) || pats[0];
  return selectEl(id, pats.map(p => [p, p]), pats.includes(cur) ? cur : pats[0]);
}
function modeRadios(name, mode, onChange) {
  return radios(name, [["add", "Add to Existing Loads"], ["replace", "Replace Existing Loads"], ["delete", "Delete Existing Loads"]], mode, onChange);
}
function openDlg(id, opts) {
  ensureCss();
  const d = l116Dialog(id, opts);
  d.el.classList.add("asn-dlg", "l116-dlg");
  return d;
}
/** Model edited → dirty + refresh every view that shows loads. */
function finish(what) {
  if (!SKY) return;
  SKY.markDirty && SKY.markDirty();
  try { SKY.renderProps && SKY.renderProps(); } catch (e) { console.error(e); }
  try { if (SKY.planEditor) SKY.planEditor.renderStatic(); } catch (e) { console.error(e); }
  try { if (SKY.elevEditor && SKY.elevEditor.refresh) SKY.elevEditor.refresh(); } catch (e) { console.error(e); }
  const s = SKY.store;
  if (s.mode === "loads" && SKY.loadsEditor) { try { SKY.loadsEditor.render(); } catch (e) { console.error(e); } }
  document.dispatchEvent(new CustomEvent("sky:l116-edit", { detail: { what } }));
}
const selMembers = m => (SKY.store.selection || []).filter(r => r.type === "member")
  .map(r => m.members.find(x => x.uid === r.uid)).filter(Boolean);
const selShells = m => (SKY.store.selection || []).filter(r => r.type === "shell")
  .map(r => (m.shells || []).find(x => x.uid === r.uid)).filter(Boolean);
const memLen = mm => Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]);
const fmtPt = p => `(${p.map(v => U.fmt("length", v, 2)).join(", ")})`;
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

/* ================================================================
   Assign → Frame Loads → Temperature (uniform ΔT + gradients 2-2 / 3-3)
   ================================================================ */
export function thermalText(t) {
  const parts = [];
  if (t.dT) parts.push(`ΔT ${U.fmtU("temp_delta", t.dT, 1)}`);
  if (t.grad2) parts.push(`grad 2-2 ${U.fmtU("temp_gradient", t.grad2, 2)}`);
  if (t.grad3) parts.push(`grad 3-3 ${U.fmtU("temp_gradient", t.grad3, 2)}`);
  return parts.join(" · ") || "ΔT 0";
}
/** Canonical frame thermal entry (backend shape: dT always, grads only when ≠ 0). */
function thermalEntry(uid, dT, g2, g3) {
  const e = { member_uid: uid, dT: dT || 0 };
  if (g2) e.grad2 = g2;
  if (g3) e.grad3 = g3;
  return e;
}

export function openFrameTemperature() {
  const m = SKY && SKY.store.model;
  if (!m) return null;
  const members = selMembers(m);
  let mode = "add";
  const body = el("div", { class: "l116-ftemp" });
  if (!members.length) body.appendChild(el("p", { class: "field-error asn-warn", text: "No frame members selected — select members (V) first." }));
  const patSel = patternSelect(m, "l116FtPattern");
  body.appendChild(el("div", { class: "asn-top" }, [el("label", { class: "asn-field" }, [el("span", { text: "Load Pattern" }), patSel])]));

  const g1 = group("Temperature");
  const fdT = numField("temp_delta", 0, { id: "l116FtDT" });
  const fg2 = numField("temp_gradient", 0, { id: "l116FtG2" });
  const fg3 = numField("temp_gradient", 0, { id: "l116FtG3" });
  g1.append(
    numRow("Uniform temperature change ΔT", fdT, "Uniform temperature change (positive = rise)"),
    numRow("Temperature gradient 2-2", fg2, "ΔT across the depth along local 2 ÷ section depth; the member curves away from the hot (+2) face"),
    numRow("Temperature gradient 3-3", fg3, "ΔT across the width along local 3 ÷ section width"));
  g1.appendChild(el("p", { class: "muted asn-note", html:
    `Gradient = temperature difference across the section ÷ its depth (ETABS “Temperature Gradient”). Free curvature ` +
    `κ = α·g; a fully restrained member carries M = E·I·α·g. α = material α, else the model α ` +
    `${esc(U.toDisplay("thermal_coeff", m.thermal_alpha ?? 1.2e-5).toExponential(2))} ${esc(U.label("thermal_coeff"))}. ` +
    `Truss (axial-only) members ignore gradients.` }));
  body.appendChild(g1);
  const g3 = group("Options");
  g3.appendChild(modeRadios("l116FtMode", mode, v => { mode = v; for (const f of [fdT, fg2, fg3]) f.el.disabled = v === "delete"; }));
  body.appendChild(g3);
  const exG = group("Existing temperature loads in pattern");
  const exBox = el("div", { class: "asn-exist", id: "l116FtExisting" });
  exG.appendChild(exBox);
  body.appendChild(exG);
  const err = l116ErrorLine();
  body.appendChild(err);
  const renderExisting = () => {
    const p = m.patterns[patSel.value];
    const rows = [];
    for (const mm of members) for (const t of ((p && p.thermal_loads) || []).filter(t => t.member_uid === mm.uid))
      rows.push(`${mm.uid}: ${thermalText(t)}`);
    exBox.innerHTML = rows.length ? rows.map(t => `<div>${esc(t)}</div>`).join("") : `<span class="muted">none</span>`;
  };
  patSel.addEventListener("change", renderExisting);
  renderExisting();

  const apply = () => {
    if (!members.length) return "Select at least one frame member.";
    const vals = [fdT.get(), fg2.get(), fg3.get()].map(v => v == null ? 0 : v);
    if (mode !== "delete" && !vals.every(isFinite)) return "ΔT and the gradients must be finite numbers.";
    const [dT, g2, g3v] = vals;
    const pn = patSel.value;
    const nothing = dT === 0 && g2 === 0 && g3v === 0;
    if (mode === "add" && nothing) return { changed: false };
    const exists = !!m.patterns[pn];
    if (!exists && (mode === "delete" || nothing)) return { changed: false };
    const pat = ME.ensurePattern(m, pn);
    let changed = !exists;
    for (const mm of members) {
      const cur = pat.thermal_loads.filter(t => t.member_uid === mm.uid);
      if (mode === "add") {
        if (cur.length) {
          const t = cur[0];
          const n = thermalEntry(mm.uid, (t.dT || 0) + dT, (t.grad2 || 0) + g2, (t.grad3 || 0) + g3v);
          for (const k of ["dT", "grad2", "grad3"]) { if (k in n) t[k] = n[k]; else delete t[k]; }
        } else pat.thermal_loads.push(thermalEntry(mm.uid, dT, g2, g3v));
        changed = true;
        continue;
      }
      if (mode === "replace" && !nothing && cur.length === 1) {       // in place (keeps order)
        const n = thermalEntry(mm.uid, dT, g2, g3v);
        if (JSON.stringify(n) !== JSON.stringify(thermalEntry(mm.uid, cur[0].dT, cur[0].grad2, cur[0].grad3))) {
          for (const k of ["dT", "grad2", "grad3"]) { if (k in n) cur[0][k] = n[k]; else delete cur[0][k]; }
          changed = true;
        }
        continue;
      }
      if (cur.length) { pat.thermal_loads = pat.thermal_loads.filter(t => t.member_uid !== mm.uid); changed = true; }
      if (mode === "replace" && !nothing) { pat.thermal_loads.push(thermalEntry(mm.uid, dT, g2, g3v)); changed = true; }
    }
    if (changed) SKY.store.loadPattern = pn;
    return { changed };
  };
  const fb = l116FootBar(`${plural(members.length, "member")} selected`, [
    l116Btn("Cancel", "", () => dlg.close()),
    l116Btn("OK", "btn-run", () => { const r = apply(); if (typeof r === "string") { l116ShowError(err, r); return; } dlg.close(); if (r.changed) finish("Frame temperature"); }),
  ]);
  fb.wrap.querySelectorAll(".btn")[1].id = "l116FtOk";
  const dlg = openDlg("l116FrameTempModal", { title: "Assign Frame Loads — Temperature", iconId: "exleaf-frames", body, foot: fb.wrap });
  return dlg;
}

/* ================================================================
   Assign → Shell Loads → Temperature (ΔT + gradient through thickness)
   ================================================================ */
export function openShellTemperature() {
  const m = SKY && SKY.store.model;
  if (!m) return null;
  const shells = selShells(m);
  let mode = "add";
  const body = el("div", { class: "l116-stemp" });
  if (!shells.length) body.appendChild(el("p", { class: "field-error asn-warn", text: "No walls / slabs selected — select shell regions (V) first." }));
  const membrane = shells.filter(s => s.behavior !== "shell");
  if (membrane.length) body.appendChild(el("p", { class: "field-error asn-warn", id: "l116StMembrane",
    text: `Membrane region${membrane.length > 1 ? "s" : ""} ${membrane.map(s => s.uid).join(", ")} cannot take shell temperature loads — set Behavior to shell (FE).` }));
  const patSel = patternSelect(m, "l116StPattern");
  body.appendChild(el("div", { class: "asn-top" }, [el("label", { class: "asn-field" }, [el("span", { text: "Load Pattern" }), patSel])]));
  const g1 = group("Temperature");
  const fdT = numField("temp_delta", 0, { id: "l116StDT" });
  const fg3 = numField("temp_gradient", 0, { id: "l116StG3" });
  g1.append(numRow("Uniform temperature change ΔT", fdT), numRow("Temperature gradient 3-3 (through thickness)", fg3,
    "ΔT between the faces ÷ thickness along the region local 3 (corner-ordering normal); +3 face hotter for a positive value"));
  g1.appendChild(el("p", { class: "muted asn-note", text:
    "ΔT expands the membrane; the gradient curves the plate (κ = α·g, both directions). Clamped plates report N = −E·t·α·ΔT/(1−ν) and m = E·t³·α·g/(12(1−ν))." }));
  body.appendChild(g1);
  const g3 = group("Options");
  g3.appendChild(modeRadios("l116StMode", mode, v => { mode = v; fdT.el.disabled = fg3.el.disabled = v === "delete"; }));
  body.appendChild(g3);
  const exG = group("Existing shell temperature loads in pattern");
  const exBox = el("div", { class: "asn-exist", id: "l116StExisting" });
  exG.appendChild(exBox);
  body.appendChild(exG);
  const err = l116ErrorLine();
  body.appendChild(err);
  const renderExisting = () => {
    const p = m.patterns[patSel.value];
    const rows = [];
    for (const s of shells) for (const t of ((p && p.shell_thermal_loads) || []).filter(t => t.region_uid === s.uid))
      rows.push(`${s.uid}: ${thermalText(t)}`);
    exBox.innerHTML = rows.length ? rows.map(t => `<div>${esc(t)}</div>`).join("") : `<span class="muted">none</span>`;
  };
  patSel.addEventListener("change", renderExisting);
  renderExisting();

  const apply = () => {
    if (!shells.length) return "Select at least one wall or slab.";
    const dT = fdT.get() ?? 0, g3v = fg3.get() ?? 0;
    if (mode !== "delete" && !(isFinite(dT) && isFinite(g3v))) return "ΔT and the gradient must be finite numbers.";
    const nothing = dT === 0 && g3v === 0;
    if (mode !== "delete" && !nothing && membrane.length)
      return `Membrane region${membrane.length > 1 ? "s" : ""} ${membrane.map(s => s.uid).join(", ")} cannot take shell temperature loads (needs behavior 'shell').`;
    const pn = patSel.value;
    const p0 = m.patterns[pn];
    if (mode === "add" && nothing) return { changed: false };
    if (!p0 && (mode === "delete" || nothing)) return { changed: false };
    const pat = ME.ensurePattern(m, pn);
    let list = Array.isArray(pat.shell_thermal_loads) ? pat.shell_thermal_loads : [];
    let changed = !p0;
    for (const s of shells) {
      const cur = list.filter(t => t.region_uid === s.uid);
      if (mode === "add") {
        if (cur.length) { cur[0].dT = (cur[0].dT || 0) + dT; cur[0].grad3 = (cur[0].grad3 || 0) + g3v; }
        else list.push({ region_uid: s.uid, dT, grad3: g3v });
        changed = true;
        continue;
      }
      if (mode === "replace" && !nothing && cur.length === 1) {
        if (cur[0].dT !== dT || cur[0].grad3 !== g3v) { cur[0].dT = dT; cur[0].grad3 = g3v; changed = true; }
        continue;
      }
      if (cur.length) { list = list.filter(t => t.region_uid !== s.uid); changed = true; }
      if (mode === "replace" && !nothing) { list.push({ region_uid: s.uid, dT, grad3: g3v }); changed = true; }
    }
    if (list.length) pat.shell_thermal_loads = list; else delete pat.shell_thermal_loads;   // backend omits an empty list
    if (changed) SKY.store.loadPattern = pn;
    return { changed };
  };
  const fb = l116FootBar(`${plural(shells.length, "region")} selected`, [
    l116Btn("Cancel", "", () => dlg.close()),
    l116Btn("OK", "btn-run", () => { const r = apply(); if (typeof r === "string") { l116ShowError(err, r); return; } dlg.close(); if (r.changed) finish("Shell temperature"); }),
  ]);
  fb.wrap.querySelectorAll(".btn")[1].id = "l116StOk";
  const dlg = openDlg("l116ShellTempModal", { title: "Assign Shell Loads — Temperature", iconId: "exleaf-shells", body, foot: fb.wrap });
  return dlg;
}

/* ================================================================
   Assign → Joint Loads → Temperature (joint-pattern temperatures)
   ================================================================ */
function selectionJointsL116(m) {
  if (SKY.assign && SKY.assign.selectionJoints) return SKY.assign.selectionJoints(m, SKY.store.selection || []);
  const pts = [];
  for (const mm of selMembers(m)) for (const p of [mm.pi, mm.pj]) if (!pts.some(q => samePt(q, p))) pts.push(p);
  return pts;
}
export function openJointTemperature() {
  const m = SKY && SKY.store.model;
  if (!m) return null;
  const pts = selectionJointsL116(m).map(p => p.map(Number));
  let mode = "add";
  const body = el("div", { class: "l116-jtemp" });
  if (!pts.length) body.appendChild(el("p", { class: "field-error asn-warn", text: "No joints selected — select members / shells (V); their end joints receive the temperature." }));
  const patSel = patternSelect(m, "l116JtPattern");
  body.appendChild(el("div", { class: "asn-top" }, [el("label", { class: "asn-field" }, [el("span", { text: "Load Pattern" }), patSel])]));
  const g1 = group("Joint temperature");
  const fdT = numField("temp_delta", 0, { id: "l116JtDT" });
  g1.appendChild(numRow("Temperature change at each joint", fdT));
  g1.appendChild(el("p", { class: "muted asn-note", text:
    "A frame member takes the average of its two end-joint temperatures as a uniform ΔT (a joint without a value counts as 0). Shells do not read joint temperatures — use Shell Loads › Temperature." }));
  body.appendChild(g1);
  const g3 = group("Options");
  g3.appendChild(modeRadios("l116JtMode", mode, v => { mode = v; fdT.el.disabled = v === "delete"; }));
  body.appendChild(g3);
  const jG = group(`Joints (${pts.length})`);
  const jBox = el("div", { class: "asn-jlist", id: "l116JtList" });
  jG.appendChild(jBox);
  body.appendChild(jG);
  const err = l116ErrorLine();
  body.appendChild(err);
  const renderList = () => {
    const p = m.patterns[patSel.value];
    const jt = (p && p.joint_temperatures) || [];
    jBox.innerHTML = pts.map(q => {
      const cur = jt.filter(j => samePt(j.point, q));
      return `<div class="asn-jrow"><span class="asn-pt">${esc(fmtPt(q))} ${esc(U.label("length"))}</span>` +
        `<span class="asn-cur muted">${cur.length ? esc(cur.map(j => U.fmtU("temp_delta", j.dT, 1)).join(" + ")) : "—"}</span></div>`;
    }).join("") || `<span class="muted">none</span>`;
  };
  patSel.addEventListener("change", renderList);
  renderList();

  const apply = () => {
    if (!pts.length) return "Select at least one joint (members / shells).";
    const dT = fdT.get() ?? 0;
    if (mode !== "delete" && !isFinite(dT)) return "ΔT must be a finite number.";
    const pn = patSel.value;
    const p0 = m.patterns[pn];
    if (mode === "add" && dT === 0) return { changed: false };
    if (!p0 && (mode === "delete" || dT === 0)) return { changed: false };
    const pat = ME.ensurePattern(m, pn);
    let list = Array.isArray(pat.joint_temperatures) ? pat.joint_temperatures : [];
    let changed = !p0;
    for (const q of pts) {
      const cur = list.filter(j => samePt(j.point, q));
      if (mode === "add") {
        if (cur.length) cur[0].dT = (cur[0].dT || 0) + dT;
        else list.push({ point: q.slice(), dT });
        changed = true;
        continue;
      }
      if (mode === "replace" && dT !== 0 && cur.length === 1) {
        if (cur[0].dT !== dT) { cur[0].dT = dT; changed = true; }
        continue;
      }
      if (cur.length) { list = list.filter(j => !samePt(j.point, q)); changed = true; }
      if (mode === "replace" && dT !== 0) { list.push({ point: q.slice(), dT }); changed = true; }
    }
    if (list.length) pat.joint_temperatures = list; else delete pat.joint_temperatures;
    if (changed) SKY.store.loadPattern = pn;
    return { changed };
  };
  const fb = l116FootBar(`${plural(pts.length, "joint")}`, [
    l116Btn("Cancel", "", () => dlg.close()),
    l116Btn("OK", "btn-run", () => { const r = apply(); if (typeof r === "string") { l116ShowError(err, r); return; } dlg.close(); if (r.changed) finish("Joint temperature"); }),
  ]);
  fb.wrap.querySelectorAll(".btn")[1].id = "l116JtOk";
  const dlg = openDlg("l116JointTempModal", { title: "Assign Joint Loads — Temperature", iconId: "exleaf-supports", body, foot: fb.wrap });
  return dlg;
}

/* ================================================================
   Assign → Frame Loads → Distributed (uniform / trapezoidal + Projected)
   ================================================================ */
const DIST_DIRS = [["gravity", "Gravity (−Z)"], ["local_y", "Local 2"], ["global_x", "Global X"], ["global_y", "Global Y"], ["global_z", "Global Z"]];
const PROJ_DIRS = ["gravity", "global_x", "global_y", "global_z"];
const DIR_VEC = { gravity: [0, 0, -1], global_x: [1, 0, 0], global_y: [0, 1, 0], global_z: [0, 0, 1] };
/** sqrt(1 − (x·d)²) — thermal_ext.projected_factor (1 when not projected). */
export function projectedFactor(mm, l) {
  if (!l.projected) return 1;
  const d = DIR_VEC[l.direction || "gravity"];
  if (!d) return 1;
  const L = memLen(mm);
  if (!(L > 0)) return 0;
  const c = ((mm.pj[0] - mm.pi[0]) * d[0] + (mm.pj[1] - mm.pi[1]) * d[1] + (mm.pj[2] - mm.pi[2]) * d[2]) / L;
  return Math.sqrt(Math.max(0, 1 - c * c));
}
const distLoadText = l => {
  const dir = (DIST_DIRS.find(d => d[0] === (l.direction || "gravity")) || [0, l.direction])[1];
  const w = (l.kind || "udl") === "trapezoid"
    ? `${U.fmt("line_force", l.w, 2)} → ${U.fmtU("line_force", l.w2, 2)} on ${+(+l.a).toFixed(3)}–${+(+l.b).toFixed(3)}·L`
    : `${U.fmtU("line_force", l.w, 2)}${(+l.a || 0) !== 0 || (l.b ?? 1) !== 1 ? ` on ${+(+l.a).toFixed(3)}–${+(+l.b).toFixed(3)}·L` : ""}`;
  return `${w} · ${dir}${l.projected ? " · projected" : ""}`;
};
export function openFrameDistributed() {
  const m = SKY && SKY.store.model;
  if (!m) return null;
  const members = selMembers(m);
  let mode = "add", ltype = "udl";
  const body = el("div", { class: "l116-fdist" });
  if (!members.length) body.appendChild(el("p", { class: "field-error asn-warn", text: "No frame members selected — select members (V) first." }));
  const patSel = patternSelect(m, "l116FdPattern");
  body.appendChild(el("div", { class: "asn-top" }, [el("label", { class: "asn-field" }, [el("span", { text: "Load Pattern" }), patSel])]));
  const g1 = group("Load Type and Direction");
  g1.appendChild(radios("l116FdType", [["udl", "Uniform"], ["trapezoid", "Trapezoidal"]], ltype, v => { ltype = v; sync(); }));
  const dirSel = selectEl("l116FdDir", DIST_DIRS, "gravity");
  g1.appendChild(el("label", { class: "asn-field" }, [el("span", { text: "Direction" }), dirSel]));
  const projCb = el("input", { type: "checkbox", id: "l116FdProjected" });
  g1.appendChild(el("label", { class: "dlg-chk", title: "Load per unit length projected on the plane normal to the load direction (gravity on a rafter: total w·L·cosθ)" },
    [projCb, el("span", { text: "Projected (gravity / global loads on inclined members)" })]));
  body.appendChild(g1);
  const g2 = group("Load");
  const fw = numField("line_force", 0, { id: "l116FdW" });
  const fw2 = numField("line_force", 0, { id: "l116FdW2" });
  const fa = numField("none", 0, { id: "l116FdA" });
  const fbb = numField("none", 1, { id: "l116FdB" });
  const rW2 = numRow("End load w<sub>2</sub>", fw2), rA = numRow("Start distance a (0–1)", fa), rB = numRow("End distance b (0–1)", fbb);
  const rW = numRow("Load w", fw);
  g2.append(rW, rW2, rA, rB);
  body.appendChild(g2);
  const g3 = group("Options");
  g3.appendChild(modeRadios("l116FdMode", mode, v => { mode = v; sync(); }));
  body.appendChild(g3);
  const note = el("p", { class: "muted asn-note", id: "l116FdNote" });
  body.appendChild(note);
  const exG = group("Existing distributed loads in pattern");
  const exBox = el("div", { class: "asn-exist", id: "l116FdExisting" });
  exG.appendChild(exBox);
  body.appendChild(exG);
  const err = l116ErrorLine();
  body.appendChild(err);
  function sync() {
    const trap = ltype === "trapezoid";
    rW.querySelector(".asn-num-lbl").innerHTML = trap ? "Start load w<sub>1</sub>" : "Load w";
    for (const r of [rW2]) r.classList.toggle("hidden", !trap);
    const dir = dirSel.value;
    projCb.disabled = !PROJ_DIRS.includes(dir) || mode === "delete";
    if (projCb.disabled) projCb.checked = false;
    for (const f of [fw, fw2, fa, fbb]) f.el.disabled = mode === "delete";
    dirSel.disabled = mode === "delete";
    const w = fw.get();
    const mm = members[0];
    if (mm && isNum(w) && projCb.checked) {
      const f = projectedFactor(mm, { projected: true, direction: dir });
      note.innerHTML = `${esc(mm.uid)}: L = ${esc(U.fmtU("length", memLen(mm), 2))}, projection factor √(1−(x·d)²) = <b>${f.toFixed(4)}</b> — ` +
        `a uniform w gives a total of ${esc(U.fmtU("force", w * memLen(mm) * f, 2))} (unprojected ${esc(U.fmtU("force", w * memLen(mm), 2))}).`;
    } else {
      note.textContent = dir === "gravity" ? "Gravity acts downward (positive value = −Z), per true member length unless Projected." : "Per true member length unless Projected.";
    }
    renderExisting();
  }
  function renderExisting() {
    const p = m.patterns[patSel.value];
    const rows = [];
    for (const mm of members) for (const l of ((p && p.member_loads) || []).filter(l => l.member_uid === mm.uid && ["udl", "trapezoid"].includes(l.kind || "udl")))
      rows.push(`${mm.uid}: ${distLoadText(l)}`);
    exBox.innerHTML = rows.length ? rows.map(t => `<div>${esc(t)}</div>`).join("") : `<span class="muted">none</span>`;
  }
  dirSel.addEventListener("change", sync);
  projCb.addEventListener("change", sync);
  fw.el.addEventListener("input", sync);
  patSel.addEventListener("change", renderExisting);
  sync();

  const apply = () => {
    if (!members.length) return "Select at least one frame member.";
    const trap = ltype === "trapezoid";
    let w = 0, w2 = 0, a = 0, b = 1;
    if (mode !== "delete") {
      w = fw.get() ?? 0; w2 = trap ? (fw2.get() ?? 0) : 0; a = fa.get() ?? 0; b = fbb.get() ?? 1;
      if (![w, w2, a, b].every(isFinite)) return "Enter finite load values and distances.";
      if (!(a >= 0 && a <= b && b <= 1)) return "Distances must satisfy 0 ≤ a ≤ b ≤ 1.";
    }
    const pn = patSel.value;
    const p0 = m.patterns[pn];
    const zero = w === 0 && w2 === 0;
    if (mode === "add" && zero) return { changed: false };
    if (!p0 && (mode === "delete" || zero)) return { changed: false };
    const pat = ME.ensurePattern(m, pn);
    let changed = !p0;
    for (const mm of members) {
      if (mode !== "add") {
        const before = pat.member_loads.length;
        pat.member_loads = pat.member_loads.filter(l => !(l.member_uid === mm.uid && ["udl", "trapezoid"].includes(l.kind || "udl")));
        if (before !== pat.member_loads.length) changed = true;
      }
      if (mode === "delete" || zero) continue;
      const l = { member_uid: mm.uid, kind: trap ? "trapezoid" : "udl", w, w2, a, b, direction: dirSel.value };
      if (projCb.checked) l.projected = true;
      pat.member_loads.push(l);
      changed = true;
    }
    if (changed) SKY.store.loadPattern = pn;
    return { changed };
  };
  const fb = l116FootBar(`${plural(members.length, "member")} selected`, [
    l116Btn("Cancel", "", () => dlg.close()),
    l116Btn("OK", "btn-run", () => { const r = apply(); if (typeof r === "string") { l116ShowError(err, r); return; } dlg.close(); if (r.changed) finish("Frame distributed loads"); }),
  ]);
  fb.wrap.querySelectorAll(".btn")[1].id = "l116FdOk";
  const dlg = openDlg("l116FrameDistModal", { title: "Assign Frame Loads — Distributed", iconId: "exleaf-frames", body, foot: fb.wrap });
  return dlg;
}

/* ================================================================
   Define → Load Patterns → Auto Lateral Load
   ================================================================ */
export const AL_OPTIONS = [
  ["none", "None"],
  ["asce7_16", "ASCE 7-16"],
  ["asce7_22", "ASCE 7-22"],
  ["ec8", "EN 1998-1 (EC8)"],
  ["is1893", "IS 1893:2016"],
  ["user_coefficient", "User Coefficient"],
  ["user_loads", "User Loads"],
  ["asce7_22_wind", "ASCE 7-22 Wind"],
];
const AL_LABEL = Object.fromEntries(AL_OPTIONS);
const AL_DEFAULT_NAME = { asce7_16: "ELF", asce7_22: "ELF22", ec8: "EC8", is1893: "IS1893",
  user_coefficient: "UCOEF", user_loads: "ULOADS", asce7_22_wind: "WIND22" };

/* Parameter specs: [key, label, units kind, default SI, {opt (blank → omitted), sel: [[v,t]…], int, hint}] */
const SPEC = {
  asce7_16: [
    ["SDS", "S<sub>DS</sub>", "none", 1.0, { hint: "Design short-period spectral acceleration (g)" }],
    ["SD1", "S<sub>D1</sub>", "none", 0.6, { hint: "Design 1-s spectral acceleration (g)" }],
    ["R", "Response modification R", "none", 8],
    ["Ie", "Importance factor I<sub>e</sub>", "none", 1],
  ],
  asce7_22: [
    ["SDS", "S<sub>DS</sub>", "none", 1.0, { opt: true, hint: "Blank with a multi-period spectrum → 0.9·max Sa over 0.2–5 s" }],
    ["SD1", "S<sub>D1</sub>", "none", 0.6],
    ["R", "Response modification R", "none", 8],
    ["Ie", "Importance factor I<sub>e</sub>", "none", 1],
    ["TL", "Long-period transition T<sub>L</sub>", "period", 8],
    ["S1", "S<sub>1</sub> (0.5·S1/(R/Ie) floor when ≥ 0.6)", "none", 0],
    ["Ct", "C<sub>t</sub> (SI, h<sub>n</sub> in m)", "none", 0.0466, { hint: "Table 12.8-2 (SI): steel MRF 0.0724 / x 0.8 · concrete MRF 0.0466 / x 0.9 · EBF/BRBF 0.0731 / x 0.75 · all other 0.0488 / x 0.75" }],
    ["x", "x", "none", 0.9],
    ["T", "Program period T (capped at C<sub>u</sub>·T<sub>a</sub>)", "period", null, { opt: true, hint: "Blank → approximate period Ta = Ct·hn^x" }],
  ],
  ec8: [
    ["ag", "a<sub>gR</sub> (in g)", "none", 0.25],
    ["q", "Behaviour factor q", "none", 4],
    ["gamma_I", "Importance γ<sub>I</sub>", "none", 1],
    ["ground_type", "Ground type", "none", "B", { sel: ["A", "B", "C", "D", "E"].map(g => [g, g]) }],
    ["spectrum_type", "Spectrum type", "none", 1, { sel: [[1, "Type 1"], [2, "Type 2"]], int: true }],
    ["structure", "T<sub>1</sub> = C<sub>t</sub>·H<sup>0.75</sup>", "none", "other", { sel: [["steel_mrf", "Steel MRF (0.085)"], ["concrete_mrf", "Concrete MRF (0.075)"], ["other", "Other (0.050)"]] }],
    ["T1", "Period T<sub>1</sub> (override)", "period", null, { opt: true }],
    ["beta", "Lower bound β", "none", 0.2],
    ["S", "Soil factor S (override)", "none", null, { opt: true }],
    ["TB", "T<sub>B</sub> (override)", "period", null, { opt: true }],
    ["TC", "T<sub>C</sub> (override)", "period", null, { opt: true }],
    ["TD", "T<sub>D</sub> (override)", "period", null, { opt: true }],
    ["distribution", "Vertical distribution", "none", "height", { sel: [["height", "Heights z (Eq. 4.11)"], ["mode", "Mode shape s (Eq. 4.10)"]] }],
  ],
  is1893: [
    ["Z", "Zone factor Z", "none", 0.24, { sel: [[0.10, "II — 0.10"], [0.16, "III — 0.16"], [0.24, "IV — 0.24"], [0.36, "V — 0.36"]] }],
    ["R", "Response reduction R", "none", 5],
    ["I", "Importance factor I", "none", 1],
    ["soil", "Soil type", "none", "II", { sel: [["I", "I — rock / hard"], ["II", "II — medium"], ["III", "III — soft"]] }],
    ["structure", "Period formula", "none", "rc_mrf", { sel: [["rc_mrf", "RC MRF 0.075·h^0.75"], ["steel_mrf", "Steel MRF 0.080·h^0.75"], ["other", "Other 0.09·h/√d"]] }],
    ["d", "Base dimension d (other; blank = plan extent)", "length", null, { opt: true }],
    ["T", "Period T (override)", "period", null, { opt: true }],
    ["damping_factor", "Damping factor", "none", 1],
  ],
  user_coefficient: [
    ["C", "Base shear coefficient C", "none", 0.1],
    ["k", "Height exponent k", "none", 1],
  ],
  user_loads: [],
  asce7_22_wind: [
    ["V", "Basic wind speed V", "velocity", 50],
    ["exposure", "Exposure", "none", "C", { sel: [["B", "B"], ["C", "C"], ["D", "D"]] }],
    ["Kzt", "K<sub>zt</sub>", "none", 1],
    ["Kd", "K<sub>d</sub>", "none", 0.85],
    ["ze", "Ground elevation z<sub>e</sub> (K<sub>e</sub>)", "length", 0],
    ["cp_total", "G·C<sub>p</sub> windward + leeward", "none", 1.3],
  ],
};

const DEFAULT_MPRS = [[0, 0.4], [0.2, 1.0], [0.5, 1.0], [1, 0.6], [2, 0.3], [4, 0.12], [8, 0.035]];
const alMemory = new Map();      // pattern name → {code, params, ecc, direction} (this session)
export const autoLateralOf = name => (alMemory.get(name) || {}).code || "none";

/** ASCE 7-16 §12.8 (the existing /api/pattern/elf formulas, Ct 0.0466, x 0.9). */
export function asce716Coeffs(model, { SDS, SD1, R, Ie }) {
  const hn = Math.max(...(model.stories || []).map(s => s.elevation), 0);
  const Ta = 0.0466 * hn ** 0.9;
  const RoIe = R / Ie;
  const Cs = Math.max(Math.min(SDS / RoIe, SD1 / (Ta * RoIe)), Math.max(0.044 * SDS * Ie, 0.01));
  const k = Ta <= 0.5 ? 1 : Ta >= 2.5 ? 2 : 1 + (Ta - 0.5) / 2;
  return { Ta, Cs, k, hn };
}

/* backend I/O — live: sync the working model once, then preview / generate;
   mock: js/mock_loads116.js */
async function apiPost(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}
async function syncModel() {
  const payload = clone(SKY.store.model);
  delete payload._mock_params;
  if (SKY.l116PostModel) return SKY.l116PostModel(payload);
  return apiPost("/api/model", payload);
}
/** {code, …} request body (ASCE 7-16 previews through user_coefficient C = Cs, k). */
function previewBody(code, body) {
  if (code !== "asce7_16") return body;
  const c = asce716Coeffs(SKY.store.model, body);
  return { code: "user_coefficient", C: c.Cs, k: c.k, direction: body.direction };
}
async function alPreview(code, body) {
  const b = previewBody(code, body);
  const r = SKY.store.mock ? l116MockPreview(SKY.store.model, b) : await apiPost("/api/pattern/auto-lateral/preview", b);
  if (code === "asce7_16") {
    const c = asce716Coeffs(SKY.store.model, body);
    return { ...r, code: "asce7_16", T: c.Ta, Ta: c.Ta, Cs: c.Cs, k: c.k, C: undefined };
  }
  return r;
}
async function alGenerate(code, body, ecc) {
  const st = SKY.store;
  if (code === "asce7_16") {
    const name = body.name;
    if (st.mock) {
      const c = asce716Coeffs(st.model, body);
      l116MockGenerate(st.model, { code: "user_coefficient", name, C: c.Cs, k: c.k, direction: body.direction, ecc });
    } else {
      await SKY.l116CodeToolLive("/api/pattern/elf", { name, SDS: body.SDS, SD1: body.SD1, R: body.R, Ie: body.Ie, direction: body.direction });
      if (ecc) { const p = st.model.patterns[name]; p.accidental_torsion = true; p.ecc = ecc; }
    }
  } else if (st.mock) {
    l116MockGenerate(st.model, { ...body, ecc });
  } else {
    await SKY.l116CodeToolLive("/api/pattern/auto-lateral", { ...body, ...(ecc ? { ecc } : {}) });
  }
  ME.normalizeModel(st.model);
  if (st.mock || (code === "asce7_16" && ecc)) SKY.markDirty && SKY.markDirty();
  else { st.modelEdited = true; document.dispatchEvent(new CustomEvent("sky:model-changed")); }
}

/** Horizontal story-force bar chart (inline SVG; top story on top). */
export function storyForceSvg(rows, { kind = "force", id } = {}) {
  const n = rows.length;
  const W = 340, rowH = Math.max(14, Math.min(26, 220 / Math.max(n, 1))), H = n * rowH + 26, L = 64, Rp = 70;
  const fmax = Math.max(1e-12, ...rows.map(r => Math.abs(r.F)));
  const sx = (W - L - Rp) / fmax;
  let s = `<svg viewBox="0 0 ${W} ${H}" class="l116-bars" role="img" aria-label="Story forces"${id ? ` id="${id}"` : ""}>`;
  s += `<line x1="${L}" y1="6" x2="${L}" y2="${H - 18}" class="l116-axis"/>`;
  [...rows].reverse().forEach((r, i) => {
    const y = 8 + i * rowH, bw = Math.abs(r.F) * sx, x = r.F >= 0 ? L : L - bw;
    s += `<text x="${L - 6}" y="${y + rowH * 0.62}" text-anchor="end" class="l116-tick">${esc(r.story)}</text>`;
    s += `<rect x="${x.toFixed(1)}" y="${(y + 2).toFixed(1)}" width="${Math.max(bw, 0.5).toFixed(1)}" height="${(rowH - 5).toFixed(1)}" rx="2" class="l116-bar" data-story="${esc(r.story)}"><title>${esc(r.story)}: ${esc(U.fmtU(kind, r.F, 2))}</title></rect>`;
    s += `<text x="${(L + Math.max(bw, 0) + 5).toFixed(1)}" y="${y + rowH * 0.62}" class="l116-val">${esc(U.fmt(kind, r.F, 1))}</text>`;
  });
  s += `<text x="${W - 4}" y="${H - 4}" text-anchor="end" class="l116-tick">${esc(U.label(kind))}</text>`;
  return s + "</svg>";
}

const COEF_ROWS = {   // [key, label, kind | "fmt"]
  asce7_16: [["Ta", "T<sub>a</sub>", "period"], ["k", "k", "none"], ["Cs", "C<sub>s</sub>", "none"]],
  asce7_22: [["Ta", "T<sub>a</sub>", "period"], ["Cu", "C<sub>u</sub>", "none"], ["k", "k", "none"], ["SDS", "S<sub>DS</sub>", "none"], ["Sa", "S<sub>a</sub>", "none"], ["Cs", "C<sub>s</sub>", "none"], ["spectrum", "Spectrum", "text"]],
  ec8: [["Sd", "S<sub>d</sub>(T<sub>1</sub>) (g)", "none"], ["lambda_", "λ", "none"], ["ag", "a<sub>g</sub> = γ<sub>I</sub>·a<sub>gR</sub>", "none"], ["S", "S", "none"], ["TB", "T<sub>B</sub>", "period"], ["TC", "T<sub>C</sub>", "period"], ["TD", "T<sub>D</sub>", "period"]],
  is1893: [["Sa_g", "S<sub>a</sub>/g", "none"], ["Ah", "A<sub>h</sub>", "none"], ["rho", "ρ<sub>min</sub>", "none"]],
  user_coefficient: [["C", "C", "none"], ["k", "k", "none"]],
  user_loads: [],
  asce7_22_wind: [["Ke", "K<sub>e</sub>", "none"], ["width", "Facade width", "length"]],
};
const fmtSig = (v, d = 4) => (v == null || !isFinite(v)) ? "—" : String(+(+v).toPrecision(d));

export function openAutoLateral(patternName, code) {
  const m = SKY && SKY.store.model;
  if (!m) return null;
  const mem = (patternName && alMemory.get(patternName)) || null;
  code = code && code !== "none" ? code : (mem && mem.code) || "asce7_22";
  const st = {
    code,
    name: patternName || AL_DEFAULT_NAME[code],
    direction: (mem && mem.direction) || "X",
    eccMode: mem && mem.ecc ? (mem.ecc < 0 ? "neg" : "pos") : "none",
    ecc: mem && mem.ecc ? Math.abs(mem.ecc) : 0.05,
    mprsOn: false, mprs: clone(DEFAULT_MPRS),
    modeShape: null, userLoads: null,
    last: null, synced: false, seq: 0,
  };
  const params = {};            // code → {key: SI value}
  if (mem) {
    params[mem.code] = clone(mem.params);
    if (mem.params.mprs) { st.mprsOn = true; st.mprs = clone(mem.params.mprs); }
  }
  const body = el("div", { class: "l116-al" });
  body.appendChild(el("p", { class: "muted dlg-intro", html:
    "ETABS <b>Auto Lateral Load</b>: story forces computed from the story weights (mass source × g) and elevations, stored in the pattern " +
    "(replacing its loads). <b>Preview</b> shows the hand calculation before <b>Generate</b> writes it." }));
  const top = el("div", { class: "asn-top l116-al-top" });
  const nameIn = el("input", { type: "text", id: "l116AlName", value: st.name, spellcheck: "false", maxlength: "60" });
  const codeSel = selectEl("l116AlCode", AL_OPTIONS.filter(o => o[0] !== "none"), st.code);
  const dirSel = selectEl("l116AlDir", [["X", "X"], ["Y", "Y"]], st.direction);
  top.append(
    el("label", { class: "asn-field" }, [el("span", { text: "Load Pattern" }), nameIn]),
    el("label", { class: "asn-field" }, [el("span", { text: "Auto Lateral Load" }), codeSel]),
    el("label", { class: "asn-field l116-dir" }, [el("span", { text: "Direction" }), dirSel]));
  body.appendChild(top);
  const cols = el("div", { class: "asn-cols l116-al-cols" });
  const left = el("div", { class: "l116-al-left" });
  const paramG = group("Parameters", "l116-al-params");
  const paramBox = el("div", { id: "l116AlParams" });
  paramG.appendChild(paramBox);
  left.appendChild(paramG);
  const eccG = group("Eccentricity", "l116-al-ecc");
  const eccIn = numField("none", st.ecc, { id: "l116AlEcc", min: "0", max: "0.5" });
  eccG.appendChild(radios("l116AlEccMode", [["none", "None"], ["pos", "+ Eccentricity"], ["neg", "− Eccentricity"]], st.eccMode,
    v => { st.eccMode = v; eccIn.el.disabled = v === "none"; }));
  eccG.appendChild(numRow("Ratio (fraction of the plan dimension ⊥ load)", eccIn));
  eccIn.el.disabled = st.eccMode === "none";
  eccG.appendChild(el("p", { class: "muted asn-note", text: "Story torque Mz = F·ecc·B⊥ (sign follows ±). Sets the pattern's accidental torsion." }));
  left.appendChild(eccG);
  cols.appendChild(left);
  const right = el("div", { class: "asn-prev-col l116-al-right" });
  const prevG = group("Preview", "l116-al-prev");
  const prevStatus = el("p", { class: "muted asn-note", id: "l116AlStatus", text: "" });
  const prevSum = el("div", { class: "l116-sum", id: "l116AlSummary" });
  const prevChart = el("div", { class: "l116-chart", id: "l116AlChart" });
  const prevTable = el("div", { class: "l116-tbl", id: "l116AlTable" });
  prevG.append(prevStatus, prevSum, prevChart, prevTable);
  right.appendChild(prevG);
  cols.appendChild(right);
  body.appendChild(cols);
  const err = l116ErrorLine();
  body.appendChild(err);

  let fields = {};
  const stories = () => (SKY.store.model.stories || []);
  function buildParams() {
    paramBox.innerHTML = "";
    fields = {};
    const c = st.code;
    const saved = params[c] || {};
    for (const [key, lbl, kind, def, o = {}] of SPEC[c]) {
      const v0 = key in saved ? saved[key] : def;
      if (o.sel) {
        const s = selectEl("l116Al_" + key, o.sel.map(([v, t]) => [v, t]), v0);
        if (![...s.options].some(op => op.value === String(v0))) { s.appendChild(el("option", { value: String(v0), text: String(v0) })); s.value = String(v0); }
        s.addEventListener("change", () => { if (key === "distribution") buildExtras(); schedule(); });
        fields[key] = { get: () => { const t = s.value; return o.int || typeof def === "number" ? +t : t; }, el: s };
        paramBox.appendChild(el("label", { class: "asn-num-row l116-num-row", title: o.hint || null }, [el("span", { class: "asn-num-lbl", html: lbl }), s, el("span", { class: "asn-unit" })]));
      } else {
        const f = numField(kind, v0, { id: "l116Al_" + key, placeholder: o.opt ? "auto" : null });
        f.el.addEventListener("input", schedule);
        fields[key] = { ...f, opt: !!o.opt };
        paramBox.appendChild(numRow(lbl, f, o.hint));
      }
    }
    buildExtras();
  }
  const extras = el("div", { class: "l116-extras", id: "l116AlExtras" });
  function buildExtras() {
    extras.innerHTML = "";
    const c = st.code;
    if (c === "asce7_22") {
      const cb = el("input", { type: "checkbox", id: "l116AlMprsOn" });
      cb.checked = st.mprsOn;
      extras.appendChild(el("label", { class: "dlg-chk" }, [cb, el("span", { html: "Multi-period response spectrum (MPRS, §11.4.5.1) — descending envelope" })]));
      const tb = el("div", { class: "l116-grid-tbl" + (st.mprsOn ? "" : " hidden"), id: "l116AlMprs" });
      const draw = () => {
        tb.innerHTML = "";
        tb.appendChild(el("div", { class: "l116-grid-row head" }, [el("span", { text: "T (s)" }), el("span", { text: "Sa (g)" }), el("span")]));
        st.mprs.forEach((p, i) => {
          const t = el("input", { type: "number", step: "any", value: String(p[0]), "data-i": i, class: "l116-mprs-t" });
          const s = el("input", { type: "number", step: "any", value: String(p[1]), "data-i": i, class: "l116-mprs-s" });
          t.addEventListener("input", () => { st.mprs[i][0] = parseFloat(t.value); schedule(); });
          s.addEventListener("input", () => { st.mprs[i][1] = parseFloat(s.value); schedule(); });
          const del = el("button", { class: "del", type: "button", title: "Remove row", text: "✕" });
          del.disabled = st.mprs.length <= 2;
          del.addEventListener("click", () => { st.mprs.splice(i, 1); draw(); schedule(); });
          tb.appendChild(el("div", { class: "l116-grid-row" }, [t, s, del]));
        });
        const add = l116Btn("+ Row", "btn-small", () => { const l = st.mprs[st.mprs.length - 1] || [0, 0]; st.mprs.push([+(l[0] + 1).toFixed(3), l[1]]); draw(); schedule(); });
        add.id = "l116AlMprsAdd";
        tb.appendChild(add);
        for (const i of tb.querySelectorAll("input, button")) i.disabled = !st.mprsOn || (i.classList.contains("del") && st.mprs.length <= 2);
      };
      cb.addEventListener("change", () => { st.mprsOn = cb.checked; tb.classList.toggle("hidden", !st.mprsOn); draw(); schedule(); });
      draw();
      extras.appendChild(tb);
    } else if (c === "ec8" && fields.distribution && fields.distribution.get() === "mode") {
      const H = Math.max(...stories().map(s => s.elevation), 1);
      if (!st.modeShape) st.modeShape = Object.fromEntries(stories().map(s => [s.name, +(s.elevation / H).toFixed(4)]));
      const tb = el("div", { class: "l116-grid-tbl", id: "l116AlMode" });
      tb.appendChild(el("div", { class: "l116-grid-row head" }, [el("span", { text: "Story" }), el("span", { text: "Mode shape s" })]));
      for (const s of stories()) {
        const i = el("input", { type: "number", step: "any", value: String(st.modeShape[s.name] ?? 0), "data-story": s.name });
        i.addEventListener("input", () => { st.modeShape[s.name] = parseFloat(i.value); schedule(); });
        tb.appendChild(el("div", { class: "l116-grid-row" }, [el("span", { text: s.name }), i]));
      }
      extras.appendChild(tb);
    } else if (c === "user_loads") {
      if (!st.userLoads) {
        const p = m.patterns[st.name];
        const sf = (p && p.story_forces) || [];
        st.userLoads = Object.fromEntries(stories().map(s => {
          const f = sf.find(x => x.story === s.name);
          return [s.name, { fx: f ? +f.fx || 0 : 0, fy: f ? +f.fy || 0 : 0 }];
        }));
      }
      const tb = el("div", { class: "l116-grid-tbl l116-ul", id: "l116AlUserLoads" });
      tb.appendChild(el("div", { class: "l116-grid-row head" }, [el("span", { text: "Story" }), el("span", { text: `Fx (${U.label("force")})` }), el("span", { text: `Fy (${U.label("force")})` })]));
      for (const s of [...stories()].reverse()) {
        const row = el("div", { class: "l116-grid-row" }, [el("span", { text: s.name })]);
        for (const k of ["fx", "fy"]) {
          const f = numField("force", st.userLoads[s.name][k], { "data-story": s.name, "data-k": k, class: "l116-ul-" + k });
          f.el.addEventListener("input", () => { const v = f.get(); st.userLoads[s.name][k] = v == null ? 0 : v; schedule(); });
          row.appendChild(f.el);
        }
        tb.appendChild(row);
      }
      extras.appendChild(tb);
      extras.appendChild(el("p", { class: "muted asn-note", text: "Applied verbatim at each story (fx and fy together; the direction picks which one is the reported shear)." }));
    } else if (c === "asce7_16") {
      extras.appendChild(el("p", { class: "muted asn-note", html: "Existing ASCE 7-16 §12.8 ELF (POST /api/pattern/elf): T<sub>a</sub> = 0.0466·h<sub>n</sub><sup>0.9</sup>, C<sub>s</sub> = min(S<sub>DS</sub>, S<sub>D1</sub>/T<sub>a</sub>)/(R/I<sub>e</sub>) ≥ max(0.044·S<sub>DS</sub>·I<sub>e</sub>, 0.01)." }));
    } else if (c === "asce7_22_wind") {
      extras.appendChild(el("p", { class: "muted asn-note", html: "q<sub>z</sub> = 0.613·K<sub>z</sub>·K<sub>zt</sub>·K<sub>e</sub>·V² (Table 26.10-1, z ≥ 15 ft); p = q<sub>z</sub>·K<sub>d</sub>·GC<sub>p</sub>; story force = p × tributary facade." }));
    }
  }
  paramG.appendChild(extras);

  /** Request body from the current inputs ({error} when invalid). */
  function readBody() {
    const c = st.code;
    const b = { code: c, direction: dirSel.value };
    for (const [key, lbl, , , o = {}] of SPEC[c]) {
      const f = fields[key];
      if (!f) continue;
      const v = f.get();
      if (o.sel) { b[key] = v; continue; }
      if (v == null) { if (o.opt) continue; return { error: `${lbl.replace(/<[^>]+>/g, "")}: enter a value.` }; }
      if (!isFinite(v)) return { error: `${lbl.replace(/<[^>]+>/g, "")}: must be a number.` };
      b[key] = v;
    }
    if (c === "asce7_22") {
      if (st.mprsOn) {
        if (st.mprs.some(p => !isFinite(p[0]) || !isFinite(p[1]))) return { error: "MPRS: every T and Sa must be a number." };
        b.mprs = st.mprs.map(p => [+p[0], +p[1]]);
      } else if (b.SDS == null) return { error: "SDS: enter a value (or use a multi-period spectrum)." };
    }
    if (c === "ec8" && b.distribution === "mode") b.mode_shape = { ...st.modeShape };
    if (c === "user_loads") b.loads = stories().map(s => ({ story: s.name, fx: st.userLoads[s.name].fx, fy: st.userLoads[s.name].fy }));
    return { body: b };
  }

  let timer = null;
  function schedule() { clearTimeout(timer); timer = setTimeout(runPreview, 250); }
  async function runPreview() {
    const r = readBody();
    l116ShowError(err, "");
    if (r.error) { prevStatus.textContent = r.error; prevStatus.classList.add("is-err"); return null; }
    const seq = ++st.seq;
    prevStatus.classList.remove("is-err");
    prevStatus.textContent = "Computing…";
    try {
      if (!SKY.store.mock && !st.synced) { await syncModel(); st.synced = true; }
      const sm = await alPreview(st.code, r.body);
      if (seq !== st.seq) return null;
      st.last = { body: r.body, summary: sm };
      renderSummary(sm);
      prevStatus.textContent = `${AL_LABEL[st.code]} · ${SKY.store.mock ? "mock (client formulas)" : "POST /api/pattern/auto-lateral/preview"}`;
      return sm;
    } catch (e) {
      if (seq !== st.seq) return null;
      st.last = null;
      prevStatus.textContent = "Preview failed: " + e.message;
      prevStatus.classList.add("is-err");
      prevSum.innerHTML = ""; prevChart.innerHTML = ""; prevTable.innerHTML = "";
      return null;
    }
  }
  function renderSummary(sm) {
    const kind = st.code === "asce7_22_wind" ? "pressure" : null;
    const items = [];
    if (sm.T != null) items.push(["T", "T (used)", U.fmtU("period", sm.T, 3)]);
    if (sm.W != null) items.push(["W", "W", U.fmtU("force", sm.W, 1)]);
    items.push(["V", st.code === "ec8" ? "F<sub>b</sub> (V)" : st.code === "is1893" ? "V<sub>b</sub>" : "V", U.fmtU("force", sm.V, 2)]);
    for (const [k, lbl, kd] of COEF_ROWS[st.code] || []) {
      if (sm[k] == null) continue;
      items.push([k, lbl, kd === "text" ? String(sm[k]).replace("_", "-") : kd === "none" ? fmtSig(sm[k]) : U.fmtU(kd, sm[k], 3)]);
    }
    if (sm.W) items.push(["VW", "V / W", fmtSig(sm.V / sm.W)]);
    prevSum.innerHTML = items.map(([k, l, v]) => `<div class="l116-kv" data-k="${esc(k)}"><span>${l}</span><b>${esc(v)}</b></div>`).join("");
    prevChart.innerHTML = storyForceSvg(sm.stories, { id: "l116AlBars" });
    const hasW = sm.stories.some(r => r.w != null);
    const hasQ = sm.stories.some(r => r.qz != null);
    let shear = 0;
    const shears = [...sm.stories].reverse().map(r => (shear += r.F)).reverse();
    prevTable.innerHTML = `<table class="data-table l116-ftbl" id="l116AlForces"><thead><tr><th>Story</th><th>h (${esc(U.label("length"))})</th>` +
      (hasW ? `<th>w (${esc(U.label("force"))})</th>` : "") + (hasQ ? `<th>q<sub>z</sub> (${esc(U.label("pressure"))})</th>` : "") +
      `<th>F (${esc(U.label("force"))})</th><th>Shear (${esc(U.label("force"))})</th></tr></thead><tbody>` +
      [...sm.stories].map((r, i) => [r, shears[i]]).reverse().map(([r, v]) => `<tr data-story="${esc(r.story)}"><td>${esc(r.story)}</td><td>${U.fmt("length", r.h, 2)}</td>` +
        (hasW ? `<td>${U.fmt("force", r.w, 1)}</td>` : "") + (hasQ ? `<td>${U.fmt("pressure", r.qz, 3)}</td>` : "") +
        `<td class="l116-F" data-si="${r.F}">${U.fmt("force", r.F, 2)}</td><td>${U.fmt("force", v, 2)}</td></tr>`).join("") + "</tbody></table>";
    void kind;
  }

  function setCode(c) {
    // keep the typed values of the previous code for this dialog session
    const prev = readBody();
    if (prev.body) params[st.code] = Object.fromEntries(Object.entries(prev.body).filter(([k]) => !["code", "direction"].includes(k)));
    const oldDefault = AL_DEFAULT_NAME[st.code];
    st.code = c;
    if (!patternName && (nameIn.value.trim() === oldDefault || !nameIn.value.trim())) nameIn.value = AL_DEFAULT_NAME[c];
    buildParams();
    schedule();
  }
  codeSel.addEventListener("change", () => setCode(codeSel.value));
  dirSel.addEventListener("change", schedule);

  async function generate(btnEl) {
    const name = nameIn.value.trim();
    if (!name || name.length > 60) { l116ShowError(err, "Enter a pattern name (max 60 characters)."); return; }
    let ecc = 0;
    if (st.eccMode !== "none") {
      const e = eccIn.get();
      if (!(isFinite(e) && e > 0 && e <= 0.5)) { l116ShowError(err, "Eccentricity ratio must be in (0, 0.5]."); return; }
      ecc = st.eccMode === "neg" ? -e : e;
    }
    btnEl.disabled = true;
    try {
      const sm = await runPreview();
      if (!sm) { l116ShowError(err, "Fix the preview error first."); return; }
      const body = { ...st.last.body, name };
      await alGenerate(st.code, body, ecc);
      const pat = SKY.store.model.patterns[name];
      // preview ↔ generated pattern check
      let dmax = 0;
      for (const r of sm.stories) {
        const f = (pat.story_forces || []).find(x => x.story === r.story);
        const g = f ? (st.code === "user_loads" ? Math.max(Math.abs(f.fx - r.fx), Math.abs(f.fy - r.fy)) : Math.abs((body.direction === "Y" ? f.fy : f.fx) - r.F)) : Math.abs(r.F);
        dmax = Math.max(dmax, g);
      }
      const mp = { ...st.last.body };
      delete mp.code; delete mp.direction;
      alMemory.set(name, { code: st.code, params: mp, ecc, direction: body.direction });
      SKY.store.loadPattern = name;
      dlg.close();
      try { if (SKY.store.mode === "loads" && SKY.loadsEditor) SKY.loadsEditor.render(); } catch (e2) { console.error(e2); }
      try { SKY.renderProps && SKY.renderProps(); } catch (e2) { console.error(e2); }
      SKY.toast && SKY.toast("Auto lateral load generated",
        `“${name}” · ${AL_LABEL[st.code]} · ${body.direction} · V = ${U.fmtU("force", sm.V, 2)}${ecc ? ` · ecc ${ecc > 0 ? "+" : "−"}${Math.abs(ecc)}` : ""} · ` +
        (dmax < 1e-6 * Math.max(1, Math.abs(sm.V)) ? "story forces match the preview" : `max story-force diff ${U.fmtU("force", dmax, 3)}`),
        "info", 6000);
      document.dispatchEvent(new CustomEvent("sky:l116-autolateral", { detail: { name, code: st.code, summary: sm, maxDiff: dmax } }));
    } catch (e) {
      l116ShowError(err, "Generate failed: " + e.message);
    } finally { btnEl.disabled = false; }
  }
  const prevBtn = l116Btn("Preview", "", () => runPreview());
  prevBtn.id = "l116AlPreview";
  const genBtn = l116Btn("Generate", "btn-run", () => generate(genBtn));
  genBtn.id = "l116AlGenerate";
  const fb = l116FootBar("Generate replaces any pattern of the same name.", [prevBtn, l116Btn("Cancel", "", () => dlg.close()), genBtn]);
  const dlg = openDlg("l116AutoLateralModal", { title: "Auto Lateral Load", iconId: "exleaf-patterns", wide: true, body, foot: fb.wrap,
    onClose: () => clearTimeout(timer) });
  buildParams();
  runPreview();
  return dlg;
}

/* ================================================================
   Load Patterns rows (js/loads.js hook) — Auto Lateral selector + v1.16 counts
   ================================================================ */
export function decoratePatternRow(wrap, m, name) {
  if (!wrap || !m || !m.patterns[name]) return;
  ensureCss();
  const p = m.patterns[name];
  const row = wrap.querySelector(".lp-row") || wrap;
  const nst = (p.shell_thermal_loads || []).length, njt = (p.joint_temperatures || []).length;
  const ngr = (p.thermal_loads || []).filter(t => t.grad2 || t.grad3).length;
  const npj = (p.member_loads || []).filter(l => l.projected).length;
  const counts = row.querySelector(".lp-counts");
  if (counts && (nst || njt || ngr || npj)) {
    const extra = [];
    if (ngr) extra.push(`<b>${ngr}</b> grad`);
    if (nst) extra.push(`<b>${nst}</b> shell ΔT`);
    if (njt) extra.push(`<b>${njt}</b> joint T`);
    if (npj) extra.push(`<b>${npj}</b> proj`);
    counts.insertAdjacentHTML("beforeend", ` <span class="l116-cnt">· ${extra.join(" · ")}</span>`);
  }
  // ETABS: the Auto Lateral Load column applies to seismic / wind (and other) patterns
  if (!["quake", "wind", "other"].includes(p.kind) && autoLateralOf(name) === "none") return;
  const box = el("div", { class: "l116-al-row" });
  const sel = selectEl("", AL_OPTIONS, autoLateralOf(name));
  sel.className = "l116-al-sel";
  sel.setAttribute("data-pattern", name);
  sel.title = "Auto Lateral Load (ETABS) — pick a code to open its parameter dialog";
  sel.addEventListener("change", () => {
    const v = sel.value;
    if (v === "none") { alMemory.delete(name); return; }
    openAutoLateral(name, v);
    sel.value = autoLateralOf(name);
  });
  const mod = l116Btn("Modify Lateral Load…", "btn-small l116-al-mod", () => openAutoLateral(name, autoLateralOf(name)));
  mod.disabled = autoLateralOf(name) === "none";
  box.append(el("span", { class: "muted l116-al-lbl", text: "Auto lateral" }), sel, mod);
  wrap.appendChild(box);
}

/* ================================================================
   init — publish entry points on window.__sky (additive only)
   ================================================================ */
export function initLoads116(sky) {
  SKY = sky;
  const api = {
    openFrameTemperature, openShellTemperature, openJointTemperature, openFrameDistributed,
    openAutoLateral, decoratePatternRow, storyForceSvg, projectedFactor, asce716Coeffs, autoLateralOf,
    closeDialog: l116CloseDialog, memory: alMemory, G,
    ec8Spectra: L116_EC8_SPECTRA,
  };
  sky.loads116 = api;
  sky.openFrameTemperature = openFrameTemperature;
  sky.openShellTemperature = openShellTemperature;
  sky.openJointTemperature = openJointTemperature;
  sky.openFrameDistributed = openFrameDistributed;
  sky.openAutoLateral = openAutoLateral;
  sky.l116PatternRow = decoratePatternRow;
  return api;
}
