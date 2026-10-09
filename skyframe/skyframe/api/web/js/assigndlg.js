/* SkyFrame — ETABS "Assign" + section-property parity dialogs (analysis-only).

     Section Manager → frame section  "Property Modifiers…"   mod_A … mod_weight
                                       "Shear Deformation…"    shear_deformation / As2 / As3
     Section Manager → shell section  "Stiffness Modifiers…"  f11 … v23, mass, weight
     Assign → Joint Loads · Force / Moment…          pattern.nodal_loads (fx … mz)
     Assign → Joint Loads · Ground Displacement…     pattern.ground_displacements
     Assign → Frame Loads · Concentrated…            member_loads kind "point" | "moment"
     Assign → Shell Loads · Uniform…                 area_loads direction / projected /
                                                     joint_pattern (Define Joint Patterns)

   JSON follows CONTRACT.md "Frame shear deformation and full property
   modifiers; shell membrane/bending modifiers" and "Joint moments, ground
   displacement, shell load directions and joint patterns". Every dialog edits
   a draft and writes the model only on OK, and only the fields that really
   changed — opening a dialog and pressing OK leaves the model byte-identical.
   Every number goes through js/units.js; an input the user did not touch
   returns its exact original SI value (no display round-off drift). */

import * as ME from "./modeledit.js";
import U from "./units.js";
import { icon } from "./icons.js";
import { divergingColor } from "./viewer3d.js";
import {
  isSupportPoint, baseLevel, mockValidateAssign, AREA_LOAD_DIRECTIONS,
} from "./mock_assign.js";

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const same = (a, b, tol = 1e-6) => !!a && !!b && a.length === 3 && b.length === 3 &&
  a.every((v, i) => Math.abs(+v - +b[i]) < tol);
const nz = v => typeof v === "number" && v !== 0 && isFinite(v);

let CTX = null;            // set by initAssign(sky)

/* ------------------------------------------------ dialog shell (analysisdlg.js look) */
const openDlgs = new Map();
function dialog(id, { title, iconId, wide = false, narrow = false, body, foot }) {
  closeDialog(id);
  const back = document.createElement("div");
  back.className = "modal-backdrop sky-dlg asn-dlg";
  back.id = id;
  const box = document.createElement("div");
  box.className = "modal" + (wide ? " modal-wide" : "") + (narrow ? " modal-narrow" : "");
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-labelledby", id + "Title");
  const head = document.createElement("header");
  head.className = "modal-head";
  head.innerHTML = `<h2 id="${id}Title" class="dlg-title">${iconId ? icon(iconId, "dlg-ico") : ""}<span>${esc(title)}</span></h2>`;
  const x = document.createElement("button");
  x.className = "icon-btn"; x.textContent = "×"; x.title = "Close";
  x.setAttribute("aria-label", "Close " + title);
  head.appendChild(x);
  const bd = document.createElement("div");
  bd.className = "modal-body";
  bd.appendChild(body);
  const ft = document.createElement("footer");
  ft.className = "modal-foot";
  ft.appendChild(foot);
  box.append(head, bd, ft);
  back.appendChild(box);
  (document.getElementById("app") || document.body).appendChild(back);
  const close = () => {
    if (!openDlgs.has(id)) return;
    openDlgs.delete(id);
    document.removeEventListener("keydown", onKey, true);
    back.remove();
  };
  const onKey = e => {
    if (e.key === "Escape" && openDlgs.has(id)) { e.stopImmediatePropagation(); e.preventDefault(); close(); }
  };
  document.addEventListener("keydown", onKey, true);
  x.addEventListener("click", close);
  back.addEventListener("mousedown", e => { if (e.target === back) close(); });
  openDlgs.set(id, { close, el: back });
  return { el: back, close };
}
export function closeDialog(id) { const d = openDlgs.get(id); if (d) d.close(); }
export const isDialogOpen = id => openDlgs.has(id);

function btn(label, cls, onClick, title) {
  const b = document.createElement("button");
  b.className = "btn" + (cls ? " " + cls : "");
  b.textContent = label;
  if (title) b.title = title;
  b.addEventListener("click", onClick);
  return b;
}
function footBar(note, buttons) {
  const wrap = document.createElement("div");
  wrap.className = "dlg-foot";
  const n = document.createElement("span");
  n.className = "muted dlg-foot-note";
  n.textContent = note || "";
  const b = document.createElement("div");
  b.className = "modal-btns";
  b.append(...buttons);
  wrap.append(n, b);
  return { wrap, note: n };
}
function errorLine() {
  const p = document.createElement("p");
  p.className = "field-error hidden dlg-error";
  return p;
}
const showError = (p, msg) => { p.textContent = msg || ""; p.classList.toggle("hidden", !msg); };
function group(legend, cls = "") {
  const g = document.createElement("fieldset");
  g.className = "dlg-group" + (cls ? " " + cls : "");
  const l = document.createElement("legend");
  l.textContent = legend;
  g.appendChild(l);
  return g;
}
function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "html") n.innerHTML = v;
    else n.setAttribute(k, v);
  }
  for (const c of [].concat(kids)) if (c != null) n.append(c);
  return n;
}

/** Numeric <input> bound to a units.js quantity kind. get() returns the
    exact original SI value while the text is untouched, null when blank. */
function numField(kind, si, { id, title, placeholder } = {}) {
  const i = document.createElement("input");
  i.type = "number"; i.step = "any";
  if (id) i.id = id;
  if (title) i.title = title;
  if (placeholder) i.placeholder = placeholder;
  let orig = si, shown = "", k = kind;
  const set = v => { orig = v; shown = (v == null || !isFinite(v)) ? "" : U.inputValue(k, v); i.value = shown; };
  set(si);
  const get = () => {
    if (i.value === shown) return orig;
    const t = String(i.value).trim();
    if (t === "") return null;
    return U.parse(k, t);
  };
  return {
    el: i, get, set,
    setKind(nk) { const v = get(); k = nk; set(v); },
    get kind() { return k; },
  };
}
/** label + input + unit in one grid row. */
function numRow(lbl, f, unitKind, hint) {
  const u = el("span", { class: "asn-unit", text: unitKind ? U.label(unitKind) : "" });
  const row = el("label", { class: "asn-num-row" }, [el("span", { class: "asn-num-lbl", html: lbl }), f.el, u]);
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
function patternSelect(m, id) {
  const s = el("select", { id });
  const pats = ME.patternNames(m);
  const cur = (CTX && CTX.store.loadPattern) || pats[0];
  s.innerHTML = pats.map(p => `<option value="${esc(p)}"${p === cur ? " selected" : ""}>${esc(p)}</option>`).join("");
  return s;
}
const fmtPt = p => `(${p.map(v => U.fmt("length", v, 2)).join(", ")}) ${U.label("length")}`;

function finish(what) {
  if (!CTX) return;
  CTX.markDirty();
  try { CTX.renderProps && CTX.renderProps(); } catch (e) { console.error(e); }
  const s = CTX.store;
  if (s.mode === "loads" && CTX.loadsEditor) { try { CTX.loadsEditor.render(); } catch (e) { console.error(e); } }
  verifyLive(what);
}

/** Live backend: lenient /api/check of the edited model (does NOT replace the
    server model) so a server-side rejection of the new loads is surfaced right
    away; mock mode: the client mirror of the same validation. */
const RELATED = /ground displacement|area load|moment load|joint_pattern|projected|membrane region|mod_|As2|As3|shear_deformation|f11|f22|f12|m11|m22|m12|v13|v23|joint load/i;
async function verifyLive(what) {
  if (!CTX) return;
  const m = CTX.store.model;
  if (CTX.store.mock) {
    const msg = mockValidateAssign(m);
    if (msg) CTX.toast(what || "Assign", msg, "error", 8000);
    return;
  }
  try {
    const payload = JSON.parse(JSON.stringify(m));
    delete payload._mock_params;
    const res = await fetch("/api/check", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: payload }),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      const msg = data && data.error;
      if (msg && RELATED.test(msg)) CTX.toast(what || "Assign", "Server: " + msg, "error", 8000);
      return;
    }
    const bad = ((data && data.issues) || []).filter(i => i.severity === "error" && RELATED.test(i.message || ""));
    if (bad.length) CTX.toast(what || "Assign", "Server: " + bad[0].message, "error", 8000);
  } catch { /* offline — the Save round trip reports any error */ }
}

/* ================================================================
   Frame section — Property / Stiffness Modification Factors
   ================================================================ */
export const FRAME_MODS = [
  ["mod_A", "Cross-section (axial) Area", false],
  ["mod_As2", "Shear Area in 2 direction", false],
  ["mod_As3", "Shear Area in 3 direction", false],
  ["mod_J", "Torsional Constant", false],
  ["mod_I22", "Moment of Inertia about 2 axis", false],
  ["mod_I33", "Moment of Inertia about 3 axis", false],
  ["mod_mass", "Mass", true],
  ["mod_weight", "Weight", true],
];
const eff = (o, k, d = 1) => (o && o[k] != null && isFinite(o[k])) ? +o[k] : d;

export function openFrameModifiers(name) {
  const m = CTX && CTX.store.model;
  const s = m && m.sections[name];
  if (!s) return null;
  const body = el("div", { class: "asn-mods" });
  body.appendChild(el("p", { class: "muted dlg-intro", html:
    `Property modifiers multiply the analysis properties of section <b>${esc(name)}</b> ` +
    `(every member that uses it). Stiffness factors must be &gt; 0; mass and weight factors &gt;= 0.` }));
  const g = group("Property/Stiffness Modification Factors");
  const table = el("div", { class: "asn-mod-table" });
  const fields = {};
  table.appendChild(el("div", { class: "asn-mod-row head" }, [el("span", { text: "Property" }), el("span", { text: "Value" })]));
  for (const [k, lbl] of FRAME_MODS) {
    const f = numField("none", eff(s, k), { id: "fmod_" + k, title: `${k} (default 1)` });
    fields[k] = f;
    table.appendChild(el("label", { class: "asn-mod-row" }, [el("span", { text: lbl }), f.el]));
  }
  g.appendChild(table);
  body.appendChild(g);
  const mode = m.mass_source_mode === "element_self_mass";
  body.appendChild(el("p", { class: "muted asn-note", id: "fmodMassNote", html:
    `<b>Mass</b> acts only on element self-mass, i.e. in the <i>Material Mass Density</i> self-mass mode ` +
    `(Define → Mass Source). ${mode ? "This model uses that mode." :
      "This model takes mass from weight — a mass factor ≠ 1 is reported by the backend as a warning and has no effect."} ` +
    `<b>Weight</b> scales the self-weight load (and weight-derived mass).` }));
  const err = errorLine();
  body.appendChild(err);
  const read = () => {
    const out = {};
    for (const [k, , isMass] of FRAME_MODS) {
      const v = fields[k].get();
      if (v == null || !isFinite(v) || (isMass ? v < 0 : v <= 0))
        return { error: `${FRAME_MODS.find(r => r[0] === k)[1]}: must be a finite number ${isMass ? ">= 0" : "> 0"}.` };
      out[k] = v;
    }
    return { out };
  };
  const fb = footBar("Defaults are 1.0 (unmodified).", [
    btn("Reset", "", () => { for (const [k] of FRAME_MODS) fields[k].set(1); }, "Set every factor back to 1.0"),
    btn("Cancel", "", () => dlg.close()),
    btn("OK", "btn-run", () => {
      const r = read();
      if (r.error) { showError(err, r.error); return; }
      let changed = false;
      for (const [k] of FRAME_MODS) {
        if (r.out[k] !== eff(s, k)) { s[k] = r.out[k]; changed = true; }
      }
      dlg.close();
      if (changed) { finish("Property modifiers"); CTX.renderSectionMgr && CTX.renderSectionMgr(); }
    }),
  ]);
  fb.wrap.querySelectorAll(".btn")[2].id = "fmodOk";
  const dlg = dialog("frameModsModal", { title: `Property/Stiffness Modifiers — ${name}`, iconId: "exleaf-frames", narrow: true, body, foot: fb.wrap });
  return dlg;
}

/* ================================================================
   Frame section — Shear deformation (As2 / As3)
   ================================================================ */
/** Text describing what "Auto" resolves to for this section (CONTRACT rule). */
export function autoShearHint(s) {
  if (/^W\d/i.test(s.name || "") || s.shape === "W") return "AISC W-shape: As2 = d·tw (web), As3 = 5/3·bf·tf";
  if ((s.b || 0) > 0 && (s.h || 0) > 0) return `rectangle rule: 5/6·A = ${U.sci("area", 5 / 6 * (s.b * s.h))} ${U.label("area")}`;
  return "no shape data — an unresolved direction is treated as shear-rigid";
}

export function openFrameShear(name) {
  const m = CTX && CTX.store.model;
  const s = m && m.sections[name];
  if (!s) return null;
  const init = {
    on: !!s.shear_deformation || s.As2 != null || s.As3 != null,
    As2: s.As2 == null ? null : +s.As2,
    As3: s.As3 == null ? null : +s.As3,
  };
  const body = el("div", { class: "asn-shear" });
  body.appendChild(el("p", { class: "muted dlg-intro", html:
    `Timoshenko members (ElasticTimoshenkoBeam) for section <b>${esc(name)}</b>. Shear areas include ` +
    `the shear coefficient; “Auto” fills them from the shape (${esc(autoShearHint(s))}).` }));
  const g = group("Shear Deformation");
  const onCb = el("input", { type: "checkbox", id: "fshearOn" });
  onCb.checked = init.on;
  g.appendChild(el("label", { class: "dlg-chk" }, [onCb, el("span", { text: "Include shear deformation" })]));
  const rows = {};
  for (const [k, lbl] of [["As2", "Shear area A<sub>s2</sub> (V2, major)"], ["As3", "Shear area A<sub>s3</sub> (V3, minor)"]]) {
    const f = numField("area", init[k], { id: "fshear" + k, placeholder: "auto" });
    const auto = el("input", { type: "checkbox", id: "fshearAuto" + k });
    auto.checked = init[k] == null;
    const autoL = el("label", { class: "dlg-chk asn-auto" }, [auto, el("span", { text: "Auto" })]);
    const row = el("div", { class: "asn-shear-row" }, [numRow(lbl, f, "area"), autoL]);
    rows[k] = { f, auto };
    auto.addEventListener("change", sync);
    g.appendChild(row);
  }
  onCb.addEventListener("change", sync);
  body.appendChild(g);
  body.appendChild(el("p", { class: "muted asn-note", text:
    "Members with moment releases, and P-Delta / large-displacement builds, fall back to Euler-Bernoulli elements (backend warning). " +
    "Shear-area modifiers live in Property Modifiers." }));
  const err = errorLine();
  body.appendChild(err);
  function sync() {
    for (const k of ["As2", "As3"]) {
      const { f, auto } = rows[k];
      auto.disabled = !onCb.checked;
      f.el.disabled = !onCb.checked || auto.checked;
      if (auto.checked && f.el.value !== "") f.set(null);
    }
  }
  sync();
  const fb = footBar("Auto = backend default (As null).", [
    btn("Cancel", "", () => dlg.close()),
    btn("OK", "btn-run", () => {
      const next = { on: onCb.checked, As2: null, As3: null };
      if (next.on) {
        for (const k of ["As2", "As3"]) {
          if (rows[k].auto.checked) continue;
          const v = rows[k].f.get();
          if (v == null || !isFinite(v) || v <= 0) { showError(err, `${k}: enter an area > 0 or tick Auto.`); return; }
          next[k] = v;
        }
      }
      dlg.close();
      const unchanged = next.on === init.on && next.As2 === init.As2 && next.As3 === init.As3;
      if (unchanged) return;
      if (!next.on) { s.shear_deformation = false; s.As2 = null; s.As3 = null; }
      else {
        // Auto fills only UNSET areas, and only when the flag is on
        s.shear_deformation = (next.As2 == null || next.As3 == null) ? true
          : (s.shear_deformation === true);
        s.As2 = next.As2; s.As3 = next.As3;
      }
      finish("Shear deformation");
      CTX.renderSectionMgr && CTX.renderSectionMgr();
    }),
  ]);
  fb.wrap.querySelectorAll(".btn")[1].id = "fshearOk";
  const dlg = dialog("frameShearModal", { title: `Shear Deformation — ${name}`, iconId: "exleaf-frames", narrow: true, body, foot: fb.wrap });
  return dlg;
}

/* ================================================================
   Shell section — Stiffness Modifiers (ETABS names)
   ================================================================ */
export const SHELL_MODS = [
  ["f11", "Membrane f11 Direction", false, "Membrane"],
  ["f22", "Membrane f22 Direction", false, "Membrane"],
  ["f12", "Membrane f12 Direction", false, "Membrane"],
  ["m11", "Bending m11 Direction", false, "Bending"],
  ["m22", "Bending m22 Direction", false, "Bending"],
  ["m12", "Bending m12 Direction", false, "Bending"],
  ["v13", "Shear v13 Direction", false, "Shear"],
  ["v23", "Shear v23 Direction", false, "Shear"],
  ["mass", "Mass", true, "Mass / Weight"],
  ["weight", "Weight", true, "Mass / Weight"],
];
export function openShellModifiers(name) {
  const m = CTX && CTX.store.model;
  const s = m && m.shell_sections[name];
  if (!s) return null;
  const body = el("div", { class: "asn-mods" });
  body.appendChild(el("p", { class: "muted dlg-intro", html:
    `Stiffness modifiers for shell section <b>${esc(name)}</b> (element local axes 1 = x, 2 = y, 3 = normal). ` +
    `All eight stiffness factors are honoured exactly in linear analyses.` }));
  const g = group("Property/Stiffness Modifiers for Analysis");
  const table = el("div", { class: "asn-mod-table" });
  const fields = {};
  let lastGrp = "";
  for (const [k, lbl, , grp] of SHELL_MODS) {
    if (grp !== lastGrp) { table.appendChild(el("div", { class: "asn-mod-grp", text: grp })); lastGrp = grp; }
    const f = numField("none", eff(s, k), { id: "smod_" + k, title: `${k} (default 1)` });
    fields[k] = f;
    table.appendChild(el("label", { class: "asn-mod-row" }, [el("span", { text: lbl }), f.el]));
  }
  g.appendChild(table);
  body.appendChild(g);
  const legacy = eff(s, "mod");
  body.appendChild(el("div", { class: "asn-legacy", id: "smodLegacy", html:
    `<span>Legacy modifier <code>mod</code></span><b>×${esc(String(+legacy.toPrecision(6)))}</b>` +
    `<span class="muted">read-only here (edit it in the section row) — scales E first: E′ = E·mod, then the factors above apply.</span>` }));
  if (s.layered) body.appendChild(el("p", { class: "muted asn-note", text:
    "This section is layered: nonlinear (pushover / NLTH) builds use only mod; the factors above apply to linear analyses." }));
  body.appendChild(el("p", { class: "muted asn-note", html:
    `<b>Mass</b> scales element self-mass only in the <i>Material Mass Density</i> self-mass mode; <b>Weight</b> scales self-weight.` }));
  const err = errorLine();
  body.appendChild(err);
  const fb = footBar("Defaults are 1.0 (unmodified).", [
    btn("Reset", "", () => { for (const [k] of SHELL_MODS) fields[k].set(1); }, "Set every factor back to 1.0"),
    btn("Cancel", "", () => dlg.close()),
    btn("OK", "btn-run", () => {
      const out = {};
      for (const [k, lbl, isMass] of SHELL_MODS) {
        const v = fields[k].get();
        if (v == null || !isFinite(v) || (isMass ? v < 0 : v <= 0)) {
          showError(err, `${lbl}: must be a finite number ${isMass ? ">= 0" : "> 0"}.`); return;
        }
        out[k] = v;
      }
      let changed = false;
      for (const [k] of SHELL_MODS) if (out[k] !== eff(s, k)) { s[k] = out[k]; changed = true; }
      dlg.close();
      if (changed) { finish("Shell stiffness modifiers"); CTX.renderSectionMgr && CTX.renderSectionMgr(); }
    }),
  ]);
  fb.wrap.querySelectorAll(".btn")[2].id = "smodOk";
  const dlg = dialog("shellModsModal", { title: `Shell Stiffness Modifiers — ${name}`, iconId: "exleaf-shells", narrow: true, body, foot: fb.wrap });
  return dlg;
}

/* ---- Section Manager hooks (one row of buttons under each section) */
const modSummary = (o, keys) => {
  const off = keys.filter(k => eff(o, k) !== 1).map(k => `${k.replace(/^mod_/, "")} ×${+eff(o, k).toPrecision(4)}`);
  return off.length ? off.join(" · ") : "all 1.0";
};
export function frameSectionExtras(name, s) {
  const shearOn = !!s.shear_deformation || s.As2 != null || s.As3 != null;
  const row = el("div", { class: "asn-sec-extras", "data-section": name });
  const sum = el("span", { class: "asn-sec-sum muted",
    text: `Modifiers: ${modSummary(s, FRAME_MODS.map(r => r[0]))} · Shear deformation: ${shearOn ? "on" : "off"}` });
  const b1 = btn("Property Modifiers…", "btn-small asn-fmod-btn", () => openFrameModifiers(name), "ETABS Property/Stiffness Modification Factors");
  const b2 = btn("Shear Deformation…", "btn-small asn-fshear-btn", () => openFrameShear(name), "Timoshenko shear areas As2 / As3");
  row.append(sum, b1, b2);
  return row;
}
export function shellSectionExtras(name, s) {
  const row = el("div", { class: "asn-sec-extras", "data-section": name });
  const sum = el("span", { class: "asn-sec-sum muted", text: `Modifiers: ${modSummary(s, SHELL_MODS.map(r => r[0]))}` });
  const b = btn("Stiffness Modifiers…", "btn-small asn-smod-btn", () => openShellModifiers(name), "Membrane f11/f22/f12, bending m11/m22/m12, shear v13/v23, mass, weight");
  row.append(sum, b);
  return row;
}

/* ================================================================
   Joints — candidates from the selection / supports
   ================================================================ */
function uniqPush(list, p) {
  if (p && p.length === 3 && !list.some(q => same(q, p))) list.push(p.map(Number));
}
export function selectionJoints(model, selection) {
  const pts = [];
  for (const ref of selection || []) {
    if (ref.type === "member") {
      const mm = model.members.find(x => x.uid === ref.uid);
      if (mm) { uniqPush(pts, mm.pi); uniqPush(pts, mm.pj); }
    } else if (ref.type === "spring") {
      const s = ME.springByKey(model, ref.uid);
      if (s) uniqPush(pts, s.point);
    } else if (ref.type === "link") {
      const l = (model.links || []).find(x => x.uid === ref.uid);
      if (l) { uniqPush(pts, l.pi); uniqPush(pts, l.pj); }
    } else if (ref.type === "shell") {
      const s = (model.shells || []).find(x => x.uid === ref.uid);
      if (s) for (const c of s.corners || []) uniqPush(pts, c);
    }
  }
  return pts;
}
export function supportJoints(model) {
  const pts = [];
  for (const s of model.supports || []) uniqPush(pts, s.point);
  for (const s of model.spring_supports || []) uniqPush(pts, s.point);
  if (!(model.supports || []).length) {
    const z = baseLevel(model);
    if (z != null) {
      for (const mm of model.members || []) for (const p of [mm.pi, mm.pj]) if (Math.abs(p[2] - z) < 1e-6) uniqPush(pts, p);
      for (const s of model.shells || []) for (const c of s.corners || []) if (Math.abs(c[2] - z) < 1e-6) uniqPush(pts, c);
    }
  }
  return pts;
}

const JF = [["fx", "F<sub>x</sub>", "force"], ["fy", "F<sub>y</sub>", "force"], ["fz", "F<sub>z</sub>", "force"],
  ["mx", "M<sub>x</sub>", "moment"], ["my", "M<sub>y</sub>", "moment"], ["mz", "M<sub>z</sub>", "moment"]];
const JG = [["ux", "U<sub>x</sub>", "disp"], ["uy", "U<sub>y</sub>", "disp"], ["uz", "U<sub>z</sub>", "disp"],
  ["rx", "R<sub>x</sub>", "rotation"], ["ry", "R<sub>y</sub>", "rotation"], ["rz", "R<sub>z</sub>", "rotation"]];

function jointLoadText(e, defs) {
  const parts = defs.filter(([k]) => nz(e[k])).map(([k, , kind]) => `${k.toUpperCase()} ${U.fmt(kind, e[k], kind === "rotation" ? 4 : 2)}${U.label(kind) ? " " + U.label(kind) : ""}`);
  return parts.length ? parts.join(", ") : "0";
}

/* ================================================================
   Assign → Joint Loads (Force / Moment · Ground Displacement)
   ================================================================ */
export function openJointLoads(tab = "force") {
  const m = CTX && CTX.store.model;
  if (!m) return null;
  let kind = tab === "ground" ? "ground" : "force";
  let mode = "add";
  const sel = selectionJoints(m, CTX.store.selection);
  const joints = sel.map(p => ({ p, on: true }));

  const body = el("div", { class: "asn-joint" });
  const top = el("div", { class: "asn-top" });
  const patSel = patternSelect(m, "jlPattern");
  top.append(el("label", { class: "asn-field" }, [el("span", { text: "Load Pattern" }), patSel]));
  const tabs = el("div", { class: "asn-tabs", role: "tablist" });
  const tabBtns = {};
  for (const [k, t] of [["force", "Force / Moment"], ["ground", "Ground Displacement"]]) {
    const b = el("button", { class: "asn-tab", role: "tab", "data-tab": k, id: "jlTab-" + k, text: t });
    b.addEventListener("click", () => { kind = k; renderValues(); renderJoints(); });
    tabBtns[k] = b;
    tabs.appendChild(b);
  }
  top.appendChild(tabs);
  body.appendChild(top);

  /* joints */
  const gj = group("Joints");
  const jList = el("div", { class: "asn-jlist", id: "jlJoints" });
  const jAdd = el("div", { class: "asn-jadd" });
  const ax = numField("length", null, { id: "jlAddX", placeholder: "X" });
  const ay = numField("length", null, { id: "jlAddY", placeholder: "Y" });
  const az = numField("length", null, { id: "jlAddZ", placeholder: "Z" });
  const addBtn = btn("+ Joint", "btn-small", () => {
    const p = [ax.get(), ay.get(), az.get()];
    if (p.some(v => v == null || !isFinite(v))) { showError(err, "Enter X, Y and Z of the joint to add."); return; }
    if (!joints.some(j => same(j.p, p))) joints.push({ p, on: true });
    ax.set(null); ay.set(null); az.set(null);
    showError(err, ""); renderJoints();
  }, "Add a joint by its coordinates");
  addBtn.id = "jlAddBtn";
  const supBtn = btn("+ All supports", "btn-small", () => {
    for (const p of supportJoints(m)) if (!joints.some(j => same(j.p, p))) joints.push({ p, on: true });
    renderJoints();
  }, "Add every support / spring joint");
  supBtn.id = "jlAddSupports";
  const clrBtn = btn("Clear", "btn-small", () => { joints.length = 0; renderJoints(); });
  jAdd.append(el("span", { class: "asn-unit", text: U.label("length") }), ax.el, ay.el, az.el, addBtn, supBtn, clrBtn);
  gj.append(jList, jAdd);
  body.appendChild(gj);

  /* values */
  const gv = group("Loads");
  gv.id = "jlValues";
  body.appendChild(gv);
  const gfields = {};
  const optWrap = group("Options");
  optWrap.appendChild(radios("jlMode", [["add", "Add to Existing Loads"], ["replace", "Replace Existing Loads"], ["delete", "Delete Existing Loads"]],
    mode, v => { mode = v; syncMode(); }));
  body.appendChild(optWrap);
  const err = errorLine();
  body.appendChild(err);

  const curList = () => {
    const p = m.patterns[patSel.value];
    return kind === "force" ? ((p && p.nodal_loads) || []) : ((p && p.ground_displacements) || []);
  };
  function renderJoints() {
    for (const [k, b] of Object.entries(tabBtns)) b.classList.toggle("is-active", k === kind);
    jList.textContent = "";
    if (!joints.length) {
      jList.appendChild(el("p", { class: "muted asn-empty", text:
        "No joints — select members / springs / shells (their end and corner joints are listed), add by coordinates, or add all supports." }));
    }
    const defs = kind === "force" ? JF : JG;
    const cur = curList();
    joints.forEach((j, i) => {
      const cb = el("input", { type: "checkbox" });
      cb.checked = j.on;
      cb.addEventListener("change", () => { j.on = cb.checked; syncNote(); });
      const ex = cur.find(e => same(e.point, j.p));
      const sup = isSupportPoint(m, j.p);
      const tags = [];
      if (sup) tags.push(el("span", { class: "asn-tag ok", text: "support" }));
      else if (kind === "ground") tags.push(el("span", { class: "asn-tag bad", text: "not a support" }));
      const del = el("button", { class: "chip-x", text: "✕", title: "Remove from list" });
      del.addEventListener("click", () => { joints.splice(i, 1); renderJoints(); });
      jList.appendChild(el("div", { class: "asn-jrow" + (kind === "ground" && !sup ? " is-bad" : ""), "data-i": String(i) }, [
        el("label", { class: "dlg-chk" }, [cb, el("span", { class: "asn-pt", text: fmtPt(j.p) })]),
        ...tags,
        el("span", { class: "asn-cur muted", text: ex ? "current: " + jointLoadText(ex, defs) : "" }),
        del,
      ]));
    });
    syncNote();
  }
  function renderValues() {
    gv.textContent = "";
    gv.appendChild(el("legend", { text: kind === "force" ? "Loads (global axes)" : "Ground Displacement (global axes)" }));
    const defs = kind === "force" ? JF : JG;
    const grid = el("div", { class: "asn-grid3" });
    for (const k in gfields) delete gfields[k];
    for (const [k, lbl, uk] of defs) {
      const f = numField(uk, 0, { id: "jl_" + k });
      gfields[k] = f;
      grid.appendChild(numRow(lbl, f, uk));
    }
    gv.appendChild(grid);
    gv.appendChild(el("p", { class: "muted asn-note", html: kind === "force"
      ? "Forces along and moments about the GLOBAL axes (right-hand rule), scaled with the pattern."
      : "Imposed support settlement / rotation on the <b>restrained</b> dofs of supports, or at the ground end of point springs. Allowed only at support or spring joints; a value on a free dof is ignored (backend warning)." }));
    syncMode();
  }
  function syncMode() { for (const f of Object.values(gfields)) f.el.disabled = mode === "delete"; }
  function syncNote() {
    const n = joints.filter(j => j.on).length;
    if (fb) fb.note.textContent = `${n} joint${n === 1 ? "" : "s"} · pattern ${patSel.value}`;
  }
  patSel.addEventListener("change", renderJoints);

  const apply = () => {
    const pts = joints.filter(j => j.on).map(j => j.p);
    if (!pts.length) return "Select at least one joint.";
    const defs = kind === "force" ? JF : JG;
    const vals = {};
    if (mode !== "delete") for (const [k, , uk] of defs) {
      const v = gfields[k].get();
      if (v == null) { vals[k] = 0; continue; }
      if (!isFinite(v)) return `${k.toUpperCase()}: not a number.`;
      vals[k] = v;
    }
    if (kind === "ground" && mode !== "delete") {
      const bad = pts.filter(p => !isSupportPoint(m, p));
      if (bad.length) return `Ground displacement is only allowed at supports or springs — ${bad.length} joint${bad.length > 1 ? "s are" : " is"} not: ${bad.slice(0, 3).map(fmtPt).join("; ")}${bad.length > 3 ? " …" : ""}`;
    }
    const pn = patSel.value;
    const pat = ME.ensurePattern(m, pn);
    const key = kind === "force" ? "nodal_loads" : "ground_displacements";
    const list = Array.isArray(pat[key]) ? pat[key] : [];
    let changed = false;
    for (const p of pts) {
      const idx = list.findIndex(e => same(e.point, p));
      const ex = idx >= 0 ? list[idx] : null;
      if (mode === "delete") { if (ex) { list.splice(idx, 1); changed = true; } continue; }
      const nv = {};
      for (const [k] of defs) nv[k] = (mode === "add" && ex ? (+ex[k] || 0) : 0) + vals[k];
      const allZero = defs.every(([k]) => nv[k] === 0);
      if (allZero) { if (ex && mode === "replace") { list.splice(idx, 1); changed = true; } continue; }
      if (ex && defs.every(([k]) => (+ex[k] || 0) === nv[k])) continue;
      const e = ex || { point: p.slice() };
      if (kind === "force") {
        e.fx = nv.fx; e.fy = nv.fy; e.fz = nv.fz;
        for (const k of ["mx", "my", "mz"]) { if (nv[k] !== 0) e[k] = nv[k]; else delete e[k]; }
      } else {
        for (const [k] of JG) e[k] = nv[k];
      }
      if (!ex) list.push(e);
      changed = true;
    }
    if (changed) {
      if (list.length) pat[key] = list;
      else if (kind === "ground") delete pat.ground_displacements;
      else pat[key] = list;
      CTX.store.loadPattern = pn;
    }
    return { changed };
  };

  const fb = footBar("", [
    btn("Cancel", "", () => dlg.close()),
    btn("Apply", "", () => { const r = apply(); if (typeof r === "string") { showError(err, r); return; } showError(err, ""); if (r.changed) finish("Joint loads"); renderJoints(); }),
    btn("OK", "btn-run", () => { const r = apply(); if (typeof r === "string") { showError(err, r); return; } dlg.close(); if (r.changed) finish("Joint loads"); }),
  ]);
  fb.wrap.querySelectorAll(".btn")[1].id = "jlApply";
  fb.wrap.querySelectorAll(".btn")[2].id = "jlOk";
  const dlg = dialog("jointLoadsModal", { title: "Assign Joint Loads", iconId: "exleaf-supports", wide: true, body, foot: fb.wrap });
  renderValues();
  renderJoints();
  return dlg;
}

/* ================================================================
   Assign → Frame Loads → Concentrated (Force | Moment)
   ================================================================ */
const FORCE_DIRS = [["gravity", "Gravity (−Z)"], ["local_y", "Local 2"], ["global_x", "Global X"], ["global_y", "Global Y"], ["global_z", "Global Z"]];
const MOMENT_DIRS = [["local_1", "Local 1 (torsion)"], ["local_2", "Local 2"], ["local_3", "Local 3"], ["global_x", "Global X"], ["global_y", "Global Y"], ["global_z", "Global Z"]];
const memLen = mm => mm.length || Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]);

export function openFrameConcentrated(type = "moment") {
  const m = CTX && CTX.store.model;
  if (!m) return null;
  const members = (CTX.store.selection || []).filter(r => r.type === "member")
    .map(r => m.members.find(x => x.uid === r.uid)).filter(Boolean);
  let ltype = type === "force" ? "point" : "moment";
  let mode = "add", distMode = "rel";

  const body = el("div", { class: "asn-fconc" });
  if (!members.length) body.appendChild(el("p", { class: "field-error asn-warn", text: "No frame members selected — select members (V) first." }));
  const top = el("div", { class: "asn-top" });
  const patSel = patternSelect(m, "fcPattern");
  top.append(el("label", { class: "asn-field" }, [el("span", { text: "Load Pattern" }), patSel]));
  body.appendChild(top);

  const g1 = group("Load Type and Direction");
  g1.appendChild(radios("fcType", [["point", "Forces"], ["moment", "Moments"]], ltype, v => { ltype = v; syncType(); }));
  const dirSel = el("select", { id: "fcDir" });
  g1.appendChild(el("label", { class: "asn-field" }, [el("span", { text: "Direction" }), dirSel]));
  body.appendChild(g1);

  const g2 = group("Load");
  const dist = numField("none", 0.5, { id: "fcDist" });
  const distRow = numRow("Distance", dist, null);
  g2.appendChild(radios("fcDistMode", [["rel", "Relative Distance from End-I"], ["abs", "Absolute Distance from End-I"]], distMode,
    v => { const cur = dist.get(); distMode = v; dist.setKind(v === "abs" ? "length" : "none"); if (cur == null) dist.set(v === "abs" ? 0 : 0.5); distRow._unit.textContent = v === "abs" ? U.label("length") : "0 – 1"; }));
  distRow._unit.textContent = "0 – 1";
  const val = numField("moment", 0, { id: "fcValue" });
  const valRow = numRow("Load", val, "moment");
  g2.append(distRow, valRow);
  body.appendChild(g2);

  const g3 = group("Options");
  g3.appendChild(radios("fcMode", [["add", "Add to Existing Loads"], ["replace", "Replace Existing Loads"], ["delete", "Delete Existing Loads"]], mode,
    v => { mode = v; val.el.disabled = dist.el.disabled = mode === "delete"; }));
  body.appendChild(g3);
  const note = el("p", { class: "muted asn-note" });
  body.appendChild(note);
  const err = errorLine();
  body.appendChild(err);

  function syncType() {
    const opts = ltype === "moment" ? MOMENT_DIRS : FORCE_DIRS;
    const prev = dirSel.value;
    dirSel.innerHTML = opts.map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join("");
    dirSel.value = opts.some(o => o[0] === prev) ? prev : (ltype === "moment" ? "local_3" : "gravity");
    val.setKind(ltype === "moment" ? "moment" : "force");
    valRow._unit.textContent = U.label(val.kind);
    note.innerHTML = ltype === "moment"
      ? "A concentrated couple about the chosen axis (right-hand rule) — ETABS Frame Loads › Point › Moment. Local 1 = member axis (torsion)."
      : "A concentrated force; Gravity acts downward (positive value = −Z).";
  }
  syncType();

  const apply = () => {
    if (!members.length) return "Select at least one frame member.";
    const pn = patSel.value;
    let w = 0, aRaw = 0;
    if (mode !== "delete") {
      w = val.get(); aRaw = dist.get();
      if (w == null || !isFinite(w)) return "Enter the load value.";
      if (aRaw == null || !isFinite(aRaw) || aRaw < 0) return "Enter a distance >= 0.";
      if (distMode === "rel" && aRaw > 1) return "Relative distance must be between 0 and 1.";
      for (const mm of members) if (distMode === "abs" && aRaw > memLen(mm) + 1e-9)
        return `Distance ${U.fmtU("length", aRaw, 2)} exceeds the length of ${mm.uid} (${U.fmtU("length", memLen(mm), 2)}).`;
    }
    const pat = ME.ensurePattern(m, pn);
    let changed = false;
    for (const mm of members) {
      if (mode !== "add") {
        const before = pat.member_loads.length;
        pat.member_loads = pat.member_loads.filter(l => !(l.member_uid === mm.uid && (l.kind || "udl") === ltype));
        if (pat.member_loads.length !== before) changed = true;
      }
      if (mode === "delete" || w === 0) continue;
      const a = distMode === "rel" ? aRaw : Math.min(1, aRaw / memLen(mm));
      pat.member_loads.push({ member_uid: mm.uid, kind: ltype, w, w2: 0, a, b: 1, direction: dirSel.value });
      changed = true;
    }
    if (changed) CTX.store.loadPattern = pn;
    return { changed };
  };
  const fb = footBar(`${members.length} member${members.length === 1 ? "" : "s"} selected`, [
    btn("Cancel", "", () => dlg.close()),
    btn("OK", "btn-run", () => { const r = apply(); if (typeof r === "string") { showError(err, r); return; } dlg.close(); if (r.changed) finish("Frame concentrated loads"); }),
  ]);
  fb.wrap.querySelectorAll(".btn")[1].id = "fcOk";
  const dlg = dialog("frameConcModal", { title: "Assign Frame Loads — Concentrated", iconId: "exleaf-frames", body, foot: fb.wrap });
  return dlg;
}

/* ================================================================
   Assign → Shell Loads → Uniform (+ Define Joint Patterns, non-uniform)
   ================================================================ */
const AREA_DIR_LABEL = {
  gravity: "Gravity (−Z)", global_x: "Global X", global_y: "Global Y", global_z: "Global Z",
  local_1: "Local 1", local_2: "Local 2", local_3: "Local 3 (normal)",
};
const PROJ_DIRS = ["gravity", "global_x", "global_y", "global_z"];

/* shell local axes — same rule as the backend (loads_ext.shell_local_axes) */
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const unit = a => { const n = Math.hypot(...a) || 1; return a.map(v => v / n); };
export function shellAxes(region) {
  const c = region.corners;
  const e3 = unit(cross(sub(c[1], c[0]), sub(c[3], c[0])));
  const e1 = Math.abs(e3[2]) > 1 - 1e-9 ? [1, 0, 0] : unit(cross([0, 0, 1], e3));
  return [e1, cross(e3, e1), e3];
}
export function jointPatternValue(jp, x, y, z) {
  if (!jp) return 1;
  const p = (+jp.a || 0) * x + (+jp.b || 0) * y + (+jp.c || 0) * z + (+jp.d || 0);
  if (jp.zero_negative && p < 0) return 0;
  if (jp.zero_positive && p > 0) return 0;
  return p;
}
const isDefaultArea = l => (l.direction || "gravity") === "gravity" && !l.projected && l.joint_pattern == null;

/** SVG preview of q·p over a region in its own plane (fast: 14×14 cells). */
export function patternPreviewSvg(region, q, jp, qKind) {
  const N = 14, W = 300, H = 170, pad = 14;
  const c = region.corners.map(p => p.map(Number));
  const [e1, e2] = shellAxes(region);
  const uv = p => [dot(sub(p, c[0]), e1), dot(sub(p, c[0]), e2)];
  const P = c.map(uv);
  const us = P.map(p => p[0]), vs = P.map(p => p[1]);
  const u0 = Math.min(...us), u1 = Math.max(...us), v0 = Math.min(...vs), v1 = Math.max(...vs);
  const sc = Math.min((W - 2 * pad) / Math.max(u1 - u0, 1e-9), (H - 2 * pad) / Math.max(v1 - v0, 1e-9));
  const ox = (W - sc * (u1 - u0)) / 2, oy = (H - sc * (v1 - v0)) / 2;
  const X = ([u, v]) => [ox + (u - u0) * sc, H - (oy + (v - v0) * sc)];
  const bil = (s, t) => [0, 1, 2].map(k => (1 - s) * (1 - t) * c[0][k] + s * (1 - t) * c[1][k] + s * t * c[2][k] + (1 - s) * t * c[3][k]);
  const vals = [];
  let vmax = 0, vmin = Infinity, vmx = -Infinity;
  for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) {
    const pc = bil((i + .5) / N, (j + .5) / N);
    const v = q * jointPatternValue(jp, pc[0], pc[1], pc[2]);
    vals.push(v); vmax = Math.max(vmax, Math.abs(v)); vmin = Math.min(vmin, v); vmx = Math.max(vmx, v);
  }
  let cells = "";
  for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) {
    const v = vals[i * N + j];
    const col = divergingColor(vmax > 0 ? v / vmax : 0);
    const pts = [[i, j], [i + 1, j], [i + 1, j + 1], [i, j + 1]].map(([a, b]) => X(uv(bil(a / N, b / N))));
    cells += `<polygon points="${pts.map(p => p.map(n => n.toFixed(1)).join(",")).join(" ")}" fill="${col}" stroke="${col}" stroke-width="0.5"/>`;
  }
  const outline = P.map(X).map(p => p.map(n => n.toFixed(1)).join(",")).join(" ");
  const lab = `${U.fmt("pressure", vmin, 2)} … ${U.fmt("pressure", vmx, 2)} ${U.label("pressure")}`;
  return { svg: `<svg class="asn-jp-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Load intensity preview">${cells}` +
    `<polygon points="${outline}" fill="none" class="asn-jp-outline"/>` +
    `<text x="6" y="${H - 4}" class="asn-jp-axis">1 →</text><text x="${W - 6}" y="12" text-anchor="end" class="asn-jp-axis">${esc(region.uid)}</text></svg>`,
    range: lab, vmin, vmax: vmx };
}

export function openShellUniform() {
  const m = CTX && CTX.store.model;
  if (!m) return null;
  const shells = (CTX.store.selection || []).filter(r => r.type === "shell")
    .map(r => (m.shells || []).find(x => x.uid === r.uid)).filter(Boolean);
  let mode = "add";
  let jpOn = false, clip = "none";

  const body = el("div", { class: "asn-suni" });
  if (!shells.length) body.appendChild(el("p", { class: "field-error asn-warn", text: "No walls / slabs selected — select shell regions (V) first." }));
  const top = el("div", { class: "asn-top" });
  const patSel = patternSelect(m, "suPattern");
  top.append(el("label", { class: "asn-field" }, [el("span", { text: "Load Pattern" }), patSel]));
  body.appendChild(top);

  const cols = el("div", { class: "asn-cols" });
  const left = el("div");
  const g1 = group("Uniform Load");
  const qf = numField("pressure", 0, { id: "suQ" });
  const qRow = numRow("Load", qf, "pressure");
  const dirSel = el("select", { id: "suDir" });
  dirSel.innerHTML = AREA_LOAD_DIRECTIONS.map(d => `<option value="${d}">${esc(AREA_DIR_LABEL[d])}</option>`).join("");
  const projCb = el("input", { type: "checkbox", id: "suProjected" });
  const projL = el("label", { class: "dlg-chk" }, [projCb, el("span", { text: "Projected (load per area projected normal to the direction)" })]);
  g1.append(qRow, el("label", { class: "asn-field" }, [el("span", { text: "Direction" }), dirSel]), projL);
  left.appendChild(g1);

  /* Define Joint Patterns (inline): p = A·x + B·y + C·z + D */
  const g2 = group("Joint Pattern (non-uniform)", "asn-jp");
  const jpCb = el("input", { type: "checkbox", id: "suJpOn" });
  g2.appendChild(el("label", { class: "dlg-chk" }, [jpCb, el("span", { text: "Non-uniform: load = q × pattern value p" })]));
  const jpBody = el("div", { class: "asn-jp-body" });
  jpBody.appendChild(el("p", { class: "muted asn-note asn-jp-eq", html: "p = A·x + B·y + C·z + D" }));
  const fA = numField("none", 0, { id: "suJpA" }), fB = numField("none", 0, { id: "suJpB" });
  const fC = numField("none", 0, { id: "suJpC" }), fD = numField("length", 0, { id: "suJpD" });
  const grid = el("div", { class: "asn-grid2" });
  grid.append(numRow("A", fA, null), numRow("B", fB, null), numRow("C", fC, null), numRow("D", fD, "length"));
  jpBody.appendChild(grid);
  jpBody.appendChild(radios("suClip", [["none", "Keep all values"], ["neg", "Zero negative values"], ["pos", "Zero positive values"]], clip,
    v => { clip = v; preview(); }));
  const hyd = el("div", { class: "asn-hydro" });
  const hz = numField("length", null, { id: "suHydroZ", placeholder: "surface Z" });
  const hb = btn("Hydrostatic", "btn-small", () => {
    let z = hz.get();
    if (z == null || !isFinite(z)) z = shells.length ? Math.max(...shells[0].corners.map(c => +c[2])) : 0;
    fA.set(0); fB.set(0); fC.set(-1); fD.set(z); clip = "neg";
    g2.querySelector('input[name="suClip"][value="neg"]').checked = true;
    if (qf.get() === 0) { qf.set(9.81); }
    preview();
  }, "Preset: p = depth below the free surface (C = −1, D = Z surface, zero negative); q = unit weight of the fluid");
  hb.id = "suHydro";
  hyd.append(el("span", { class: "muted", text: "Preset" }), hb, hz.el, el("span", { class: "asn-unit", text: U.label("length") }));
  jpBody.appendChild(hyd);
  jpBody.appendChild(el("p", { class: "muted asn-note", html:
    `A, B, C are unitless and D is a length, so p is a length: with a joint pattern the load q is entered per unit volume (${esc(U.label("unit_weight"))}), e.g. the fluid unit weight.` }));
  g2.appendChild(jpBody);
  left.appendChild(g2);

  const g3 = group("Options");
  g3.appendChild(radios("suMode", [["add", "Add to Existing Loads"], ["replace", "Replace Existing Loads"], ["delete", "Delete Existing Loads"]], mode,
    v => { mode = v; syncAll(); }));
  left.appendChild(g3);
  cols.appendChild(left);

  const right = el("div", { class: "asn-prev-col" });
  const prevG = group("Preview");
  const prevBox = el("div", { class: "asn-jp-prev", id: "suPreview" });
  const prevLab = el("p", { class: "muted asn-note", id: "suPreviewRange" });
  prevG.append(prevBox, prevLab);
  right.appendChild(prevG);
  const exG = group("Existing loads in pattern");
  const exBox = el("div", { class: "asn-exist", id: "suExisting" });
  exG.appendChild(exBox);
  right.appendChild(exG);
  cols.appendChild(right);
  body.appendChild(cols);
  const err = errorLine();
  body.appendChild(err);

  const membraneSel = shells.filter(s => s.behavior === "membrane");
  const jpObj = () => !jpOn ? null : {
    type: "linear", a: fA.get() ?? 0, b: fB.get() ?? 0, c: fC.get() ?? 0, d: fD.get() ?? 0,
    zero_negative: clip === "neg", zero_positive: clip === "pos",
  };
  function preview() {
    if (!shells.length) { prevBox.innerHTML = `<p class="muted asn-empty">Select a wall or slab to preview.</p>`; prevLab.textContent = ""; return; }
    const q = qf.get();
    const jp = jpObj();
    if (jp && ["a", "b", "c", "d"].some(k => !isFinite(jp[k]))) { prevLab.textContent = "Enter finite pattern coefficients."; return; }
    const r = patternPreviewSvg(shells[0], isFinite(q) && q != null ? q : 0, jp);
    prevBox.innerHTML = r.svg;
    prevLab.textContent = `${shells[0].uid}${shells.length > 1 ? ` (+${shells.length - 1} more)` : ""} · q·p ${r.range}`;
  }
  function renderExisting() {
    const p = m.patterns[patSel.value];
    const rows = [];
    for (const s of shells) for (const l of ((p && p.area_loads) || []).filter(l => l.region_uid === s.uid)) rows.push(areaLoadText(l));
    exBox.innerHTML = rows.length ? rows.map(t => `<div>${esc(t)}</div>`).join("") : `<span class="muted">none</span>`;
  }
  function syncAll() {
    const dir = dirSel.value;
    projCb.disabled = !PROJ_DIRS.includes(dir) || mode === "delete";
    if (projCb.disabled) projCb.checked = false;
    jpOn = jpCb.checked;
    jpBody.classList.toggle("is-off", !jpOn);
    for (const f of [fA, fB, fC, fD, hz]) f.el.disabled = !jpOn || mode === "delete";
    hb.disabled = !jpOn || mode === "delete";
    g2.querySelectorAll('input[name="suClip"]').forEach(r => { r.disabled = !jpOn || mode === "delete"; });
    const k = jpOn ? "unit_weight" : "pressure";
    if (qf.kind !== k) { qf.setKind(k); qRow._unit.textContent = U.label(k); }
    qf.el.disabled = dirSel.disabled = jpCb.disabled = mode === "delete";
    preview();
    renderExisting();
  }
  dirSel.addEventListener("change", syncAll);
  jpCb.addEventListener("change", syncAll);
  patSel.addEventListener("change", renderExisting);
  for (const f of [qf, fA, fB, fC, fD]) f.el.addEventListener("input", preview);

  const apply = () => {
    if (!shells.length) return "Select at least one wall or slab.";
    const pn = patSel.value;
    const dir = dirSel.value;
    let q = 0, jp = null;
    if (mode !== "delete") {
      q = qf.get();
      if (q == null || !isFinite(q)) return "Enter the load value.";
      jp = jpObj();
      if (jp) {
        for (const k of ["a", "b", "c", "d"]) if (!isFinite(jp[k])) return `Pattern ${k.toUpperCase()} must be a finite number.`;
      }
      if (membraneSel.length && (dir !== "gravity" || jp))
        return `Membrane region${membraneSel.length > 1 ? "s" : ""} ${membraneSel.map(s => s.uid).join(", ")} only take uniform gravity loads — set Behavior to shell (FE) or use Gravity without a joint pattern.`;
    }
    const pat = ME.ensurePattern(m, pn);
    let changed = false;
    for (const s of shells) {
      if (mode !== "add") {
        const before = pat.area_loads.length;
        pat.area_loads = pat.area_loads.filter(l => l.region_uid !== s.uid);
        if (before !== pat.area_loads.length) changed = true;
      }
      if (mode === "delete" || q === 0) continue;
      const l = { region_uid: s.uid, q };
      if (dir !== "gravity") l.direction = dir;
      if (projCb.checked) l.projected = true;
      if (jp) l.joint_pattern = { ...jp };
      pat.area_loads.push(l);
      changed = true;
    }
    if (changed) CTX.store.loadPattern = pn;
    return { changed };
  };
  const fb = footBar(`${shells.length} region${shells.length === 1 ? "" : "s"} selected`, [
    btn("Cancel", "", () => dlg.close()),
    btn("OK", "btn-run", () => { const r = apply(); if (typeof r === "string") { showError(err, r); return; } dlg.close(); if (r.changed) finish("Shell loads"); }),
  ]);
  fb.wrap.querySelectorAll(".btn")[1].id = "suOk";
  const dlg = dialog("shellUniformModal", { title: "Assign Shell Loads — Uniform", iconId: "exleaf-shells", wide: true, body, foot: fb.wrap });
  syncAll();
  return dlg;
}

/* ================================================================
   Properties panel — list the new loads for the selection
   ================================================================ */
export function areaLoadText(l) {
  const dir = AREA_DIR_LABEL[l.direction || "gravity"] || l.direction;
  const jp = l.joint_pattern;
  const q = jp ? U.fmtU("unit_weight", l.q, 2) : U.fmtU("pressure", l.q, 2);
  let t = `q ${q} · ${dir}${l.projected ? " · projected" : ""}`;
  if (jp) {
    t += ` · p = ${+(+jp.a || 0).toPrecision(4)}x ${fmtSigned(jp.b)}y ${fmtSigned(jp.c)}z ${fmtSigned(U.toDisplay("length", +jp.d || 0))}` +
      `${jp.zero_negative ? " (zero −)" : ""}${jp.zero_positive ? " (zero +)" : ""}`;
  }
  return t;
}
const fmtSigned = v => { v = +(+v || 0).toPrecision(4); return (v < 0 ? "− " : "+ ") + Math.abs(v); };
const MOMENT_DIR_LABEL = Object.fromEntries([...MOMENT_DIRS, ["local_x", "Local 1"], ["local_y", "Local 2"], ["local_z", "Local 3"]]);
const FORCE_DIR_LABEL = Object.fromEntries(FORCE_DIRS);

/** Rows describing the ETABS-parity loads carried by the current selection. */
export function selectionLoadRows(model, selection) {
  const rows = [];
  const mem = new Set(selection.filter(r => r.type === "member").map(r => r.uid));
  const shl = new Set(selection.filter(r => r.type === "shell").map(r => r.uid));
  const pts = selectionJoints(model, selection);
  for (const [pn, p] of Object.entries(model.patterns || {})) {
    for (const l of p.member_loads || []) {
      if (!mem.has(l.member_uid)) continue;
      if (l.kind === "moment")
        rows.push([pn, l.member_uid, `Moment ${U.fmtU("moment", l.w, 2)} about ${MOMENT_DIR_LABEL[l.direction] || l.direction} @ ${+(+l.a).toFixed(3)}·L`, "moment"]);
      else if (l.kind === "point")
        rows.push([pn, l.member_uid, `Force ${U.fmtU("force", l.w, 2)} ${FORCE_DIR_LABEL[l.direction] || l.direction} @ ${+(+l.a).toFixed(3)}·L`, "point"]);
    }
    for (const l of p.area_loads || [])
      if (shl.has(l.region_uid) && !isDefaultArea(l)) rows.push([pn, l.region_uid, areaLoadText(l), "area"]);
    for (const n of p.nodal_loads || [])
      if (pts.some(q => same(q, n.point))) rows.push([pn, fmtPt(n.point), "Joint " + jointLoadText(n, JF), "joint"]);
    for (const g of p.ground_displacements || [])
      if (pts.some(q => same(q, g.point))) rows.push([pn, fmtPt(g.point), "Settlement " + jointLoadText(g, JG), "ground"]);
  }
  return rows;
}

export function decorateProps(box) {
  if (!CTX || !box) return;
  const m = CTX.store.model;
  const sel = CTX.store.selection || [];
  if (!m || !sel.length) return;
  const del = box.querySelector("#propDelete");
  if (!del) return;
  const rows = selectionLoadRows(m, sel);
  const hasMem = sel.some(r => r.type === "member");
  const hasShell = sel.some(r => r.type === "shell");
  const wrap = el("div", { class: "asn-props", id: "asnProps" });
  wrap.appendChild(el("h3", { class: "group-title", html: `Assigned loads <span class="unit">joint · concentrated · shell (all patterns)</span>` }));
  if (rows.length) {
    const list = el("div", { class: "asn-props-list" });
    for (const [pn, who, txt, k] of rows)
      list.appendChild(el("div", { class: "asn-props-row asn-k-" + k }, [
        el("span", { class: "asn-props-pat", text: pn }), el("span", { class: "asn-props-who", text: who }), el("span", { class: "asn-props-txt", text: txt })]));
    wrap.appendChild(list);
  } else {
    wrap.appendChild(el("p", { class: "muted asn-note", text: "No joint loads, settlements, concentrated frame loads or directional shell loads on the selection." }));
  }
  const bar = el("div", { class: "asn-props-btns" });
  bar.appendChild(btn("Joint Loads…", "btn-small", () => openJointLoads("force")));
  bar.appendChild(btn("Settlement…", "btn-small", () => openJointLoads("ground")));
  if (hasMem) bar.appendChild(btn("Concentrated…", "btn-small", () => openFrameConcentrated("moment")));
  if (hasShell) bar.appendChild(btn("Shell Load…", "btn-small", () => openShellUniform()));
  wrap.appendChild(bar);
  const anchor = del.previousElementSibling && del.previousElementSibling.tagName === "H3" ? del.previousElementSibling : del;
  anchor.parentNode.insertBefore(wrap, anchor);
}

/* ================================================================
   init — publish the entry points on window.__sky (additive only)
   ================================================================ */
export function initAssign(sky) {
  CTX = sky;
  const api = {
    openFrameModifiers, openFrameShear, openShellModifiers,
    openJointLoads, openFrameConcentrated, openShellUniform,
    selectionJoints, supportJoints, selectionLoadRows, patternPreviewSvg,
    jointPatternValue, shellAxes, isSupportPoint, mockValidateAssign, closeDialog,
  };
  sky.assign = api;
  sky.openJointLoads = openJointLoads;
  sky.openFrameConcentrated = openFrameConcentrated;
  sky.openShellUniform = openShellUniform;
  sky.openFrameModifiers = openFrameModifiers;
  sky.openFrameShear = openFrameShear;
  sky.openShellModifiers = openShellModifiers;
  return api;
}
