/* SkyFrame — open-structure wind (ETABS Assign > Frame Loads > Open Structure
   Wind Parameters + the open-structure wind load pattern). CONTRACT "Open
   structure wind, model info, run log and report data".

     Assign → Frame Loads · Open Structure Wind Parameters…
         per-frame FrameMember.open_wind = {include, cf, width, shielding}
         (canonical backend form; null = not assigned)
     Define → Open Structure Wind Pattern…
         ASCE 7-16 / 7-22 Ch. 29 generator: w(z) = qz(z)·(Kd)·G·Cf·width·
         shielding·proj as frame distributed loads along the wind. Preview
         (POST /api/pattern/open-wind/preview) then Generate (POST
         /api/pattern/open-wind). ?mock=1 uses js/mock_openwind.js.

   Dialogs edit a draft and write the model only on OK / Generate; OK with
   nothing changed leaves the model byte-identical. The store stays SI; every
   number goes through js/units.js. */

import OWU from "./units.js";
import * as OWME from "./modeledit.js";
import { dialog as owDialog, closeDialog as owCloseDialog, btn as owBtn, footBar as owFootBar,
  errorLine as owErrorLine, showError as owShowError } from "./analysisdlg.js";
import { OW_DEFAULT, owNormalize, owParamError, owMockCompute, owMockGenerate } from "./mock_openwind.js";

const U = OWU;
let SKY = null;

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const isNum = v => typeof v === "number" && isFinite(v);
const clone = o => JSON.parse(JSON.stringify(o));

function ensureCss() {
  if (document.querySelector("link[data-ow-css]")) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/openwind.css"; l.setAttribute("data-ow-css", "1");
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
function group(legend) {
  const g = el("fieldset", { class: "dlg-group ow-group" });
  g.appendChild(el("legend", { text: legend }));
  return g;
}
/** Number input bound to a units kind: get() = exact SI while untouched. */
function numField(kind, si, attrs = {}) {
  const i = el("input", { type: "number", step: "any", ...attrs });
  let base = si, shown = "";
  const set = v => { base = v; shown = (v == null || !isFinite(v)) ? "" : U.inputValue(kind, v); i.value = shown; };
  set(si);
  const get = () => {
    if (i.value === shown) return base;
    const t = String(i.value).trim();
    return t === "" ? null : U.parse(kind, t);
  };
  return { el: i, get, set, kind };
}
function numRow(lbl, f, hint) {
  const row = el("label", { class: "asn-num-row ow-num-row" }, [el("span", { class: "asn-num-lbl", html: lbl }), f.el,
    el("span", { class: "asn-unit", text: f.kind && f.kind !== "none" ? U.label(f.kind) : "" })]);
  if (hint) row.title = hint;
  return row;
}
function selectEl(id, opts, value) {
  const s = el("select", { id });
  s.innerHTML = opts.map(([v, t]) => `<option value="${esc(v)}"${String(v) === String(value) ? " selected" : ""}>${esc(t)}</option>`).join("");
  return s;
}
function selectRow(lbl, s) {
  return el("label", { class: "asn-num-row ow-num-row" }, [el("span", { class: "asn-num-lbl", text: lbl }), s, el("span")]);
}
function chk(id, text, checked) {
  const i = el("input", { type: "checkbox", id });
  i.checked = !!checked;
  return { input: i, row: el("label", { class: "dlg-chk" }, [i, el("span", { html: text })]) };
}
function finish() {
  if (!SKY) return;
  SKY.markDirty && SKY.markDirty();
  try { SKY.renderProps && SKY.renderProps(); } catch (e) { console.error(e); }
  try { if (SKY.planEditor) SKY.planEditor.renderStatic(); } catch (e) { console.error(e); }
  const s = SKY.store;
  if (s.mode === "loads" && SKY.loadsEditor) { try { SKY.loadsEditor.render(); } catch (e) { console.error(e); } }
  document.dispatchEvent(new CustomEvent("sky:ow-edit"));
}
const selFrames = m => (SKY.store.selection || []).filter(r => r.type === "member")
  .map(r => m.members.find(x => x.uid === r.uid)).filter(Boolean);
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

/* ================================================================
   Assign → Frame Loads → Open Structure Wind Parameters
   ================================================================ */
export function openOpenWindParams() {
  ensureCss();
  const m = SKY.store.model;
  if (!m) return null;
  const sel = selFrames(m);
  let scope = sel.length ? "selected" : "all";
  const targets = () => scope === "selected" ? sel : m.members;
  const first = (sel[0] || m.members.find(x => x.open_wind) || {}).open_wind;
  const init = owNormalize(first) || { ...OW_DEFAULT };
  const allAssigned = () => targets().length > 0 && targets().every(x => x.open_wind);
  let dirty = false;
  const touch = () => { dirty = true; };

  const body = el("div", { class: "ow-params" });
  body.appendChild(el("p", { class: "muted dlg-intro", html:
    "Ambient wind exposure of open frames (lattice towers, pipe racks, open structures). An <b>Open Structure Wind</b> pattern " +
    "(Define menu) applies <i>q<sub>z</sub>·G·C<sub>f</sub>·width</i> as distributed frame loads along the wind." }));

  const gScope = group("Apply to");
  const scopeSel = selectEl("owScope", [["selected", `Selected frames (${sel.length})`], ["all", `All frames (${m.members.length})`]], scope);
  if (!sel.length) scopeSel.querySelector('option[value="selected"]').disabled = true;
  gScope.appendChild(selectRow("Frames", scopeSel));
  const assigned = chk("owAssign", "Frames are exposed to open-structure wind (assign parameters)", allAssigned());
  gScope.appendChild(assigned.row);
  const status = el("p", { class: "muted ow-status" });
  gScope.appendChild(status);
  body.appendChild(gScope);

  const gPar = group("Parameters");
  const include = chk("owInclude", "Include in wind loading", init.include);
  gPar.appendChild(include.row);
  const cfUser = chk("owCfUser", "User drag coefficient C<sub>f</sub> (else the pattern default)", init.cf != null);
  const cf = numField("none", init.cf != null ? init.cf : 2.0, { id: "owCf", min: "0" });
  gPar.append(cfUser.row, numRow("C<sub>f</sub>", cf, "Force (drag) coefficient — ASCE 7 Fig. 29.4-x / Table 29.4-2"));
  const wUser = chk("owWidthUser", "User exposed width (else the section depth)", init.width !== "auto");
  const width = numField("dim", init.width !== "auto" ? init.width : 0.3, { id: "owWidth", min: "0" });
  gPar.append(wUser.row, numRow("Exposed width", width));
  const shield = numField("none", init.shielding, { id: "owShield", min: "0", max: "1" });
  gPar.appendChild(numRow("Shielding / solidity factor", shield, "0 < s ≤ 1 — multiplies the member load (shielded leeward members)"));
  body.appendChild(gPar);
  const err = owErrorLine();
  body.appendChild(err);

  const sync = () => {
    const on = assigned.input.checked;
    for (const i of gPar.querySelectorAll("input")) i.disabled = !on;
    if (on) { cf.el.disabled = !cfUser.input.checked; width.el.disabled = !wUser.input.checked; }
    const t = targets();
    const nA = t.filter(x => x.open_wind).length;
    status.textContent = `${plural(t.length, "frame")} · ${nA} currently assigned`;
  };
  scopeSel.addEventListener("change", () => {
    scope = scopeSel.value; touch();
    assigned.input.checked = allAssigned() || assigned.input.checked;
    sync();
  });
  body.addEventListener("input", touch);
  body.addEventListener("change", () => { touch(); sync(); });
  sync();

  const read = () => {
    const p = { include: include.input.checked, cf: null, width: "auto", shielding: shield.get() };
    if (cfUser.input.checked) p.cf = cf.get();
    if (wUser.input.checked) p.width = width.get();
    for (const [k, v] of [["cf", p.cf], ["shielding", p.shielding]])
      if (v != null && !isNum(v)) return `${k} must be a number`;
    if (p.width !== "auto" && !isNum(p.width)) return "width must be a number";
    if (p.shielding == null) return "shielding is required";
    const e = owParamError(p);
    return e || owNormalize(p);
  };
  const ok = () => {
    if (!dirty) { dlg.close(); return; }
    const t = targets();
    if (!t.length) { owShowError(err, "No frames to assign."); return; }
    if (!assigned.input.checked) {
      let n = 0;
      for (const x of t) if (x.open_wind) { delete x.open_wind; n++; }
      dlg.close();
      if (n) { finish(); SKY.toast && SKY.toast("Open structure wind", `Removed from ${plural(n, "frame")}`, "info", 3500); }
      return;
    }
    const p = read();
    if (typeof p === "string") { owShowError(err, p); return; }
    let n = 0;
    for (const x of t) {
      if (JSON.stringify(x.open_wind || null) === JSON.stringify(p)) continue;
      x.open_wind = { ...p }; n++;
    }
    dlg.close();
    if (n) { finish(); SKY.toast && SKY.toast("Open structure wind", `Parameters assigned to ${plural(n, "frame")}`, "info", 3500); }
  };
  const fb = owFootBar("Assign → Frame Loads → Open Structure Wind", [
    owBtn("Cancel", "", () => dlg.close()),
    owBtn("OK", "btn-primary", ok),
  ]);
  fb.wrap && (fb.wrap.querySelector(".btn-primary").id = "owParamsOk");
  const dlg = owDialog("owParamsDlg", { title: "Open Structure Wind Parameters", iconId: "exleaf-patterns", body, foot: fb.wrap || fb });
  dlg.el.classList.add("asn-dlg", "ow-dlg");
  return dlg;
}

/* ================================================================
   Define → Open Structure Wind Pattern (generator)
   ================================================================ */
const LAST = { name: "OWIND", code: "asce7_22", exposure: "C", dir: "X", angle: 45, V: 40, Kzt: 1, Kd: 0.85,
  G: 0.85, cf: 2.0, ze: 0, z_ground: 0, members: "assigned", segments: 4, tower: false, shape: "square", solidity: 0.2 };

async function liveOrMock(path, body) {
  const S = SKY.store;
  if (S.mock) {
    const m = clone(S.model);
    if (path === "preview") return owMockCompute(m, (({ name, add_case, ...p }) => p)(body));
    const s = owMockGenerate(S.model, body);
    OWME.normalizeModel(S.model);
    finish();
    return s;
  }
  if (path === "preview") {
    const payload = clone(S.model);
    delete payload._mock_params;
    await SKY.l116PostModel(payload);
    const res = await fetch("/api/pattern/open-wind/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => null);
    if (!res.ok) throw new Error((d && d.error) || `${res.status}`);
    return d;
  }
  await SKY.l116CodeToolLive("/api/pattern/open-wind", body);
  document.dispatchEvent(new CustomEvent("sky:ow-edit"));
  return null;
}

export function openOpenWindPattern() {
  ensureCss();
  const m = SKY.store.model;
  if (!m) return null;
  const st = { ...LAST };
  const body = el("div", { class: "ow-pattern" });
  const nAssigned = m.members.filter(x => x.open_wind).length;
  body.appendChild(el("p", { class: "muted dlg-intro", html:
    `ASCE 7 Ch. 29 (other structures): <i>F = q<sub>z</sub> (K<sub>d</sub>) G C<sub>f</sub> A<sub>f</sub></i> applied per unit length on every exposed frame, ` +
    `with q<sub>z</sub>(z) from the code K<sub>z</sub> profile. <b>${nAssigned}</b> frame${nAssigned === 1 ? "" : "s"} carry Open Structure Wind Parameters.` }));

  const g1 = group("Pattern");
  const name = el("input", { id: "owName", type: "text", value: st.name, maxlength: "60" });
  g1.appendChild(selectRow("Load pattern name", name));
  const code = selectEl("owCode", [["asce7_22", "ASCE 7-22"], ["asce7_16", "ASCE 7-16"]], st.code);
  g1.appendChild(selectRow("Code", code));
  const memSel = selectEl("owMembers", [["assigned", `Frames with assigned parameters (${nAssigned})`], ["all", `All frames (${m.members.length}, defaults where unassigned)`]],
    nAssigned ? st.members : "all");
  g1.appendChild(selectRow("Loaded frames", memSel));
  body.appendChild(g1);

  const g2 = group("Wind");
  const V = numField("velocity", st.V, { id: "owV", min: "0" });
  g2.appendChild(numRow("Basic wind speed V", V));
  const exp = selectEl("owExposure", [["B", "B"], ["C", "C"], ["D", "D"]], st.exposure);
  g2.appendChild(selectRow("Exposure category", exp));
  const dir = selectEl("owDir", [["X", "+X"], ["Y", "+Y"], ["-X", "−X"], ["-Y", "−Y"], ["angle", "Angle…"]], st.dir);
  g2.appendChild(selectRow("Wind direction", dir));
  const angle = numField("none", st.angle, { id: "owAngle" });
  const angleRow = numRow("Angle from +X (deg)", angle);
  g2.appendChild(angleRow);
  const Kzt = numField("none", st.Kzt, { id: "owKzt", min: "0" });
  const Kd = numField("none", st.Kd, { id: "owKd", min: "0" });
  const G = numField("none", st.G, { id: "owG", min: "0" });
  const ze = numField("length", st.ze, { id: "owZe" });
  const zg = numField("length", st.z_ground, { id: "owZg" });
  g2.append(numRow("K<sub>zt</sub> topographic", Kzt), numRow("K<sub>d</sub> directionality", Kd),
    numRow("G gust-effect factor", G), numRow("Ground elevation z<sub>e</sub> (K<sub>e</sub>)", ze),
    numRow("Ground level z (base of profile)", zg));
  body.appendChild(g2);

  const g3 = group("Force coefficient");
  const cf = numField("none", st.cf, { id: "owCfDefault", min: "0" });
  g3.appendChild(numRow("Default C<sub>f</sub> (frames without a user C<sub>f</sub>)", cf));
  const tower = chk("owTower", "Trussed tower: C<sub>f</sub> from solidity ratio (ASCE 7 Table 29.4-2)", st.tower);
  g3.appendChild(tower.row);
  const shape = selectEl("owShape", [["square", "Square cross-section"], ["triangle", "Triangular cross-section"]], st.shape);
  const solid = numField("none", st.solidity, { id: "owSolidity", min: "0", max: "1" });
  const shapeRow = selectRow("Tower shape", shape), solidRow = numRow("Solidity ratio ε", solid);
  g3.append(shapeRow, solidRow);
  const segs = numField("none", st.segments, { id: "owSegments", min: "1", max: "50", step: "1" });
  g3.appendChild(numRow("Segments per inclined frame", segs, "q_z varies with height: inclined / vertical frames get this many trapezoids"));
  body.appendChild(g3);

  const err = owErrorLine();
  body.appendChild(err);
  const prev = el("div", { class: "ow-preview", id: "owPreview" });
  body.appendChild(prev);

  const sync = () => {
    angleRow.classList.toggle("hidden", dir.value !== "angle");
    shapeRow.classList.toggle("hidden", !tower.input.checked);
    solidRow.classList.toggle("hidden", !tower.input.checked);
    cf.el.disabled = tower.input.checked;
  };
  body.addEventListener("change", sync);
  sync();

  const read = () => {
    const nm = name.value.trim();
    if (!nm) return "Pattern name is required";
    const b = { name: nm, code: code.value, V: V.get(), exposure: exp.value, Kzt: Kzt.get(), Kd: Kd.get(), G: G.get(),
      cf: cf.get(), ze: ze.get(), z_ground: zg.get(), members: memSel.value, segments: segs.get() };
    if (dir.value === "angle") b.angle = angle.get(); else b.direction = dir.value;
    for (const k of ["V", "Kzt", "Kd", "G", "cf", "ze", "z_ground", "segments", ...(b.angle !== undefined ? ["angle"] : [])])
      if (!isNum(b[k])) return `${k} must be a number`;
    if (!(b.V > 0)) return "V must be > 0";
    if (!Number.isInteger(b.segments) || b.segments < 1 || b.segments > 50) return "segments must be an integer 1..50";
    if (tower.input.checked) {
      const e = solid.get();
      if (!(isNum(e) && e > 0 && e <= 1)) return "solidity ratio must be in (0, 1]";
      b.tower = { shape: shape.value, solidity: e };
    }
    Object.assign(LAST, { name: nm, code: b.code, exposure: b.exposure, dir: dir.value, V: b.V, Kzt: b.Kzt, Kd: b.Kd, G: b.G,
      cf: b.cf, ze: b.ze, z_ground: b.z_ground, members: b.members, segments: b.segments, tower: tower.input.checked,
      shape: shape.value, solidity: solid.get(), angle: b.angle ?? LAST.angle });
    return b;
  };
  const renderPreview = s => {
    const rows = s.members.slice(0, 200);
    prev.innerHTML = `<p class="ow-tot"><b>${esc(String(s.members.length))}</b> loaded frames · ΣFx <b>${U.fmtU("force", s.FX, 2)}</b> · ΣFy <b>${U.fmtU("force", s.FY, 2)}</b>` +
      ` · K<sub>e</sub> ${(+s.Ke).toFixed(4)} · C<sub>f</sub> default ${(+s.cf_default).toFixed(3)}</p>` +
      `<div class="ow-table-wrap"><table class="data-table ow-table" id="owPreviewTable"><thead><tr><th class="txt">Frame</th>` +
      `<th>z<sub>i</sub> ${esc(U.label("length"))}</th><th>z<sub>j</sub> ${esc(U.label("length"))}</th>` +
      `<th>q<sub>z,i</sub> ${esc(U.label("pressure"))}</th><th>C<sub>f</sub></th><th>width ${esc(U.label("dim"))}</th><th>proj</th>` +
      `<th>w<sub>i</sub> ${esc(U.label("line_force"))}</th><th>w<sub>j</sub> ${esc(U.label("line_force"))}</th><th>F ${esc(U.label("force"))}</th></tr></thead><tbody>` +
      rows.map(r => `<tr><td class="txt">${esc(r.uid)}</td><td>${U.fmt("length", r.z_i, 2)}</td><td>${U.fmt("length", r.z_j, 2)}</td>` +
        `<td>${U.fmt("pressure", r.qz_i, 3)}</td><td>${(+r.cf).toFixed(3)}</td><td>${U.fmt("dim", r.width, 3)}</td><td>${(+r.proj).toFixed(3)}</td>` +
        `<td>${U.fmt("line_force", r.w_i, 4)}</td><td>${U.fmt("line_force", r.w_j, 4)}</td><td>${U.fmt("force", r.F, 3)}</td></tr>`).join("") +
      `</tbody></table></div>` + (s.members.length > rows.length ? `<p class="muted">… ${s.members.length - rows.length} more</p>` : "");
  };
  const doPreview = async () => {
    owShowError(err, "");
    const b = read();
    if (typeof b === "string") { owShowError(err, b); return; }
    try { renderPreview(await liveOrMock("preview", b)); }
    catch (e) { owShowError(err, e.message); }
  };
  const doGenerate = async () => {
    owShowError(err, "");
    const b = read();
    if (typeof b === "string") { owShowError(err, b); return; }
    try {
      await liveOrMock("generate", b);
      dlg.close();
      SKY.toast && SKY.toast("Open structure wind", `Pattern “${b.name}” generated (case “${b.name}”)`, "info", 4500);
    } catch (e) { owShowError(err, e.message); }
  };
  const bPrev = owBtn("Preview", "", doPreview); bPrev.id = "owPreviewBtn";
  const bGen = owBtn("Generate", "btn-primary", doGenerate); bGen.id = "owGenerateBtn";
  const fb = owFootBar("Define → Open Structure Wind Pattern", [owBtn("Cancel", "", () => dlg.close()), bPrev, bGen]);
  const dlg = owDialog("owPatternDlg", { title: "Open Structure Wind Pattern", iconId: "exleaf-patterns", wide: true, body, foot: fb.wrap || fb });
  dlg.el.classList.add("asn-dlg", "ow-dlg");
  return dlg;
}

export function initOpenWind(sky) {
  if (!sky) return;
  SKY = sky;
  Object.assign(sky, {
    openOpenWindParams: () => openOpenWindParams(),
    openOpenWindPattern: () => openOpenWindPattern(),
    closeOpenWind: () => { owCloseDialog("owParamsDlg"); owCloseDialog("owPatternDlg"); },
    owCompute: (model, p) => owMockCompute(model, p),
  });
}
