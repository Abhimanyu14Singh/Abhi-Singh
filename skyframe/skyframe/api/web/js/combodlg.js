/* SkyFrame — ETABS parity dialogs for three analysis features (analysis only):

     Define → Load Combinations…   "Define Load Combinations" list +
                                   "Load Combination Data" (Linear Add /
                                   Envelope / Absolute Add / SRSS / Range Add;
                                   members = any static, RS, RS directional,
                                   TH, staged case or another combo)
     Define → P-Delta Options…     model.pdelta_options (None / Non-iterative
                                   based on mass / Iterative based on loads)
     Time-history case → Load Data TimeHistoryCase.components (Acceleration
                                   U1/U2/U3 or Load Pattern rows)
     Analyze → Analysis Log…       last run: P-Delta summary, case / combo
                                   status, warnings (+ a P-Delta card on the
                                   Story tab)

   Every dialog edits a DRAFT and writes the model only on OK (Cancel / Esc /
   backdrop discard), then calls ctx.markDirty(). Optional keys are written
   ONLY when they differ from the backend default (and removed otherwise), so
   the model round-trips byte-identically through POST /api/model. Numbers
   go through js/units.js; the draft and the model stay SI. Same markup as
   js/casedlg.js (.modal-backdrop.sky-dlg.cd-dlg). */

import * as ME from "./modeledit.js";
import U from "./units.js";
import * as CX from "./combo_refs.js";

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const isNum = v => typeof v === "number" && isFinite(v);
const fnum = v => isNum(v) ? String(+v.toPrecision(6)) : "—";

/* ------------------------------------------------ stylesheet (once) */
function ensureCss() {
  if (document.querySelector("link[data-cx-css]")) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/combodlg.css"; l.setAttribute("data-cx-css", "1");
  document.head.appendChild(l);
}

/* ------------------------------------------------ inline glyphs (20×20) */
const GLYPH = {
  combo: '<rect x="3" y="3.5" width="14" height="13" rx="1.5"/><path d="M6 8h3M7.5 6.5v3M11 8h3M6 13h8"/>',
  pdelta: '<path d="M6 17V4M6 4l8 1M14 5v12M3 17h14"/><path d="M10 2.5v3"/>',
  th: '<path d="M2 10h2l2-5 3 10 3-8 2 5 2-2h2"/>',
  log: '<rect x="4" y="3" width="12" height="14" rx="1.5"/><path d="M7 7h6M7 10h6M7 13h4"/>',
};
const svgIcon = k =>
  `<svg class="dlg-ico" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" ` +
  `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${GLYPH[k] || ""}</svg>`;

/* ================================================================
   dialog shell (casedlg.js markup) with a stack: Esc closes the topmost
   ================================================================ */
const stack = [];
function onKey(e) {
  if (e.key !== "Escape" || !stack.length) return;
  e.stopImmediatePropagation(); e.preventDefault();
  stack[stack.length - 1].close();
}
function dialog(id, { title, glyph, wide = false, narrow = false, body, foot, onClose, onUnits }) {
  ensureCss();
  closeDialog(id);
  const back = document.createElement("div");
  back.className = "modal-backdrop sky-dlg cd-dlg cx-dlg";
  back.id = id;
  const box = document.createElement("div");
  box.className = "modal" + (wide ? " modal-wide" : "") + (narrow ? " modal-narrow" : "");
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-labelledby", id + "Title");
  const head = document.createElement("header");
  head.className = "modal-head";
  head.innerHTML = `<h2 id="${id}Title" class="dlg-title">${glyph ? svgIcon(glyph) : ""}<span>${esc(title)}</span></h2>`;
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
  const unsub = onUnits ? U.onUnitsChange(() => onUnits()) : null;
  const entry = { id, el: back };
  const close = () => {
    const i = stack.indexOf(entry);
    if (i < 0) return;
    stack.splice(i, 1);
    if (!stack.length) document.removeEventListener("keydown", onKey, true);
    if (unsub) unsub();
    back.remove();
    onClose && onClose();
  };
  entry.close = close;
  if (!stack.length) document.addEventListener("keydown", onKey, true);
  stack.push(entry);
  x.addEventListener("click", close);
  back.addEventListener("mousedown", e => { if (e.target === back) close(); });
  return { el: back, close };
}
export function closeDialog(id) { const d = stack.find(s => s.id === id); if (d) d.close(); }
export const isDialogOpen = id => stack.some(s => s.id === id);

function btn(label, cls, onClick, title, id) {
  const b = document.createElement("button");
  b.className = "btn" + (cls ? " " + cls : "");
  b.textContent = label;
  if (title) b.title = title;
  if (id) b.id = id;
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
function errorLine(id) {
  const p = document.createElement("p");
  p.className = "field-error hidden dlg-error";
  if (id) p.id = id;
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
function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
}
/* unit-aware number input (casedlg.js contract): display text → SI on change */
function numInput(kind, si, o = {}) {
  const i = document.createElement("input");
  i.type = "number"; i.step = "any";
  i.className = "cd-num" + (o.cls ? " " + o.cls : "");
  if (o.id) i.id = o.id;
  i.dataset.cdq = kind;
  let cur = si;
  const show = () => { i.value = cur == null || cur === "" ? "" : U.inputValue(kind, cur); };
  show();
  if (o.disabled) i.disabled = true;
  if (o.title) i.title = o.title;
  i.addEventListener("change", () => {
    const t = i.value.trim();
    i.classList.remove("is-bad"); i.title = o.title || "";
    const v = U.parse(kind, t);
    let msg = "";
    if (!isFinite(v)) msg = "Enter a number";
    else if (o.int && !Number.isInteger(v)) msg = "Enter a whole number";
    else if (o.min != null && v < o.min) msg = `Must be ≥ ${U.inputValue(kind, o.min)}`;
    else if (o.max != null && v > o.max) msg = `Must be ≤ ${U.inputValue(kind, o.max)}`;
    else if (o.gt != null && !(v > o.gt)) msg = `Must be > ${U.inputValue(kind, o.gt)}`;
    if (msg) { show(); i.classList.add("is-bad"); i.title = msg; return; }
    cur = v;
    o.onSet && o.onSet(v);
  });
  return i;
}
function row(label, control, unitText, hint) {
  const r = el("label", "cd-row");
  const l = el("span", "cd-lbl");
  l.textContent = label;
  r.append(l, control);
  const u = el("span", "cd-unit");
  u.textContent = unitText || "";
  r.appendChild(u);
  if (hint) { const h = el("span", "cd-hint muted"); h.innerHTML = hint; r.appendChild(h); }
  return r;
}
/** options: [[value, label]] or [{group, items: [[v, l]]}] */
function select(options, value, onChange, id, cls) {
  const s = document.createElement("select");
  if (id) s.id = id;
  if (cls) s.className = cls;
  const opt = ([v, l]) => `<option value="${esc(v)}"${String(v) === String(value) ? " selected" : ""}>${esc(l)}</option>`;
  s.innerHTML = options.map(o => Array.isArray(o) ? opt(o)
    : `<optgroup label="${esc(o.group)}">${o.items.map(opt).join("")}</optgroup>`).join("");
  if (![...s.options].some(o => o.value === String(value)) && value != null && value !== "")
    s.insertAdjacentHTML("beforeend", `<option value="${esc(value)}" selected>${esc(value)} (missing)</option>`);
  s.addEventListener("change", () => onChange(s.value));
  return s;
}
function radio(name, value, checked, text, onPick, id) {
  const l = el("label", "dlg-chk cd-radio");
  const r = document.createElement("input");
  r.type = "radio"; r.name = name; r.value = value; r.checked = !!checked;
  if (id) r.id = id;
  r.addEventListener("change", () => { if (r.checked) onPick(value); });
  const s = el("span"); s.textContent = text;
  l.append(r, s);
  return l;
}
function takenNames(m, except) {
  const s = new Set(ME.allAnalysisCases(m).map(c => c.name));
  for (const n of Object.keys(m.combos || {})) s.add(n);
  for (const n of Object.keys(m.rs_combos || {})) s.add(n);
  s.delete(except);
  return s;
}
function uniqueName(taken, base) {
  let i = 1;
  while (taken.has(`${base}${i}`)) i++;
  return `${base}${i}`;
}

/* ================================================================
   Load combinations
   ================================================================ */
const KIND_TAG = { static: "", rs: "RS", rs_combo: "RS dir", th: "TH", staged: "staged", combo: "combo" };
export function comboFormula(m, cb) {
  const parts = Object.entries(cb.cases || {}).map(([n, f]) => {
    const k = CX.memberKind(m, n);
    const tag = KIND_TAG[k] ? ` (${KIND_TAG[k]})` : "";
    return `${fnum(f)}·${n}${tag}`;
  });
  return parts.length ? parts.join(" + ") : "(no members)";
}

function memberOptions(m, except) {
  const pool = CX.comboMemberPool(m, except);
  const groups = [];
  for (const kind of ["static", "rs", "rs_combo", "th", "staged", "combo"]) {
    const items = pool.filter(p => p.kind === kind).map(p => [p.name, p.name]);
    if (items.length) groups.push({ group: CX.MEMBER_KIND_LABEL[kind], items });
  }
  return { groups, pool };
}

/** ETABS "Load Combination Data". name null → new combo. done(name) after OK. */
export function openComboData(ctx, name = null, opts = {}) {
  const m = ctx.store.model;
  if (!m) return null;
  m.combos = m.combos || {};
  const src = name ? m.combos[name] : (opts.copyOf ? m.combos[opts.copyOf] : null);
  const taken = takenNames(m, name);
  const d = {
    name: name || uniqueName(taken, opts.copyOf ? `${opts.copyOf}-copy` : "COMB"),
    type: CX.normComboType(src ? src.combo_type : "add"),
    rows: src ? Object.entries(src.cases || {}).map(([k, f]) => ({ name: k, f })) : [],
  };
  if (opts.copyOf && !name && taken.has(d.name)) d.name = uniqueName(taken, `${opts.copyOf}-copy`);
  const body = el("div", "cx-combo");
  const err = errorLine("cxComboError");

  const draftCases = () => {
    const out = {};
    for (const r of d.rows) out[r.name] = r.f;
    return out;
  };
  const check = () => {
    const nm = d.name.trim();
    if (!nm) return "Enter a combination name.";
    if (taken.has(nm)) return `The name ${nm} is already used by another case or combination.`;
    const seen = new Set();
    for (const r of d.rows) {
      if (!r.name) return "Pick a load case / combo in every row.";
      if (seen.has(r.name)) return `${r.name} is listed twice — merge the rows.`;
      seen.add(r.name);
      if (!isNum(r.f)) return `Scale factor of ${r.name} must be a finite number.`;
    }
    // validate against a model view where the draft replaces the stored combo
    const view = { ...m, combos: { ...m.combos } };
    if (name && nm !== name) delete view.combos[name];
    view.combos[nm] = { name: nm, combo_type: d.type, cases: draftCases() };
    return CX.validateCombo(view, nm, view.combos[nm]);
  };

  const draw = () => {
    body.textContent = "";
    const g1 = group("Load Combination Data");
    const nameIn = document.createElement("input");
    nameIn.type = "text"; nameIn.id = "cxComboName"; nameIn.value = d.name;
    nameIn.addEventListener("input", () => { d.name = nameIn.value; live(); });
    g1.appendChild(row("Load Combination Name", nameIn));
    g1.appendChild(row("Combination Type", select(CX.COMBO_TYPES.map(t => [t, CX.COMBO_TYPE_LABEL[t]]),
      d.type, v => { d.type = v; draw(); }, "cxComboType")));
    const tip = {
      add: "Linear Add — factored sum. Static / staged members give one signed result; an RS member (signs lost) or any max/min member turns the sum into a <b>max/min pair</b>.",
      envelope: "Envelope — per output quantity, the max and min over the factored members.",
      abs: "Absolute Add — V = Σ|member| (largest magnitude of each member); max = +V, min = −V.",
      srss: "SRSS — V = √Σ member²; max = +V, min = −V.",
      range: "Range Add — max = Σ positive parts, min = Σ negative parts.",
    }[d.type];
    g1.appendChild(el("p", "muted cd-note", tip));
    body.appendChild(g1);

    const g2 = group("Define Combination of Load Case / Combo Results");
    const { groups, pool } = memberOptions(m, name || d.name);
    const tbl = el("div", "cx-rows");
    tbl.id = "cxComboRows";
    tbl.appendChild(el("div", "cx-row head",
      "<span>Load Name</span><span>Type</span><span>Scale Factor</span><span></span>"));
    d.rows.forEach((r, i) => {
      const line = el("div", "cx-row");
      line.dataset.row = String(i);
      const sel = select(groups, r.name, v => { r.name = v; draw(); }, null, "cx-member");
      const k = CX.memberKind(m, r.name);
      const kind = el("span", "cx-kind muted", esc(k ? (CX.MEMBER_KIND_LABEL[k] || k) : "unknown"));
      const f = numInput("none", r.f, { cls: "cx-factor", onSet: v => { r.f = v; live(); } });
      const x = btn("×", "btn-small cx-del", () => { d.rows.splice(i, 1); draw(); }, "Remove this row");
      line.append(sel, kind, f, x);
      tbl.appendChild(line);
    });
    if (!d.rows.length) tbl.appendChild(el("p", "muted cd-empty", "No members yet — click <b>Add</b>."));
    g2.appendChild(tbl);
    const acts = el("div", "cd-inline cx-acts");
    const used = new Set(d.rows.map(r => r.name));
    const free = pool.filter(p => !used.has(p.name));
    const add = btn("Add", "btn-small", () => {
      if (free.length) { d.rows.push({ name: free[0].name, f: 1.0 }); draw(); }
    }, "Add a member row", "cxComboAdd");
    add.disabled = !free.length;
    acts.appendChild(add);
    acts.appendChild(el("span", "muted cd-note",
      "Pushover, buckling, steady-state, PSD and modal cases cannot enter a load combination."));
    g2.appendChild(acts);
    body.appendChild(g2);

    const info = el("p", "muted cd-note cx-maxmin");
    info.id = "cxComboMaxMin";
    body.appendChild(info);
    body.appendChild(err);
    live();
  };
  const live = () => {
    const msg = check();
    showError(err, msg);
    const info = body.querySelector("#cxComboMaxMin");
    if (info) {
      const nm = d.name.trim() || "?";
      const view = { ...m, combos: { ...m.combos, [nm]: { name: nm, combo_type: d.type, cases: draftCases() } } };
      const mm = !msg && CX.isMaxMinCombo(view, nm);
      const th = d.rows.some(r => CX.memberKind(m, r.name) === "th");
      info.innerHTML = msg ? "" : (mm
        ? "Results: a <b>Max / Min</b> pair — result views gain a Max/Min toggle." : "Results: a single signed result.") +
        (th ? " <span class=\"cd-warn\">Time-history members record only story and base FX/FY results — member forces, reactions and joint displacements are omitted for this combo.</span>" : "");
    }
    okBtn.disabled = !!msg;
  };

  const commit = () => {
    const msg = check();
    if (msg) { showError(err, msg); return; }
    const nm = d.name.trim();
    if (name && nm !== name) ME.renameCombo(m, name, nm);
    const cb = { name: nm, combo_type: d.type, cases: draftCases() };
    if (m.combos[nm]) Object.assign(m.combos[nm], cb); else m.combos[nm] = cb;
    m.combos[nm].cases = cb.cases;
    ctx.markDirty();
    ctx.onChange && ctx.onChange("combos");
    dlg.close();
    opts.done && opts.done(nm);
  };
  const okBtn = btn("OK", "btn-primary", commit, null, "cxComboOk");
  const fb = footBar(name ? `Modify ${name}` : "New combination",
    [btn("Cancel", "", () => dlg.close(), null, "cxComboCancel"), okBtn]);
  const dlg = dialog("cxComboDlg", { title: "Load Combination Data", glyph: "combo", body, foot: fb.wrap, onUnits: draw });
  draw();
  return dlg;
}

/** ETABS "Define Load Combinations" list. */
export function openCombos(ctx) {
  const m = ctx.store.model;
  if (!m) return null;
  m.combos = m.combos || {};
  let sel = Object.keys(m.combos)[0] || null;
  const body = el("div", "cx-combos");
  const err = errorLine("cxCombosError");
  const tableWrap = el("div", "table-scroll dlg-table-wrap");
  const table = document.createElement("table");
  table.className = "data-table cx-combo-table";
  table.id = "cxCombosTable";
  tableWrap.appendChild(table);
  const side = el("div", "cd-list-acts");
  side.append(
    btn("Add New Combo…", "btn-small", () => openComboData(ctx, null, { done: n => { sel = n; refresh(); } }), "New load combination", "cxCombosAdd"),
    btn("Add Copy of Combo…", "btn-small", () => sel && openComboData(ctx, null, { copyOf: sel, done: n => { sel = n; refresh(); } }), "Copy the selected combination", "cxCombosCopy"),
    btn("Modify/Show Combo…", "btn-small", () => modify(), "Edit the selected combination", "cxCombosModify"),
    btn("Delete Combo", "btn-small", () => del(), "Delete the selected combination", "cxCombosDelete"));
  body.append(el("p", "muted dlg-intro",
    "Combinations may mix static, response-spectrum, RS directional, time-history and staged cases and other combinations. " +
    "Envelope / Absolute / SRSS / Range combos (and Linear Add combos with RS or max/min members) give a <b>Max / Min</b> result pair."),
  tableWrap, side, err);
  const modify = () => sel && openComboData(ctx, sel, { done: n => { sel = n; refresh(); } });
  const del = () => {
    if (!sel) return;
    const refs = CX.comboMemberRefs(m, sel).filter(n => n !== sel);
    if (refs.length) { showError(err, `Can't delete ${sel} — used by ${refs.join(", ")}.`); return; }
    ME.deleteCombo(m, sel);
    sel = Object.keys(m.combos)[0] || null;
    ctx.markDirty();
    ctx.onChange && ctx.onChange("combos");
    refresh();
  };
  const refresh = () => {
    showError(err, "");
    const names = Object.keys(m.combos);
    if (sel && !m.combos[sel]) sel = names[0] || null;
    table.innerHTML = `<thead><tr><th class="txt">Load Combination</th><th class="txt">Type</th><th class="txt">Definition</th><th class="txt">Result</th></tr></thead>`;
    const tb = document.createElement("tbody");
    for (const n of names) {
      const cb = m.combos[n];
      const tr = document.createElement("tr");
      tr.dataset.name = n;
      tr.className = n === sel ? "is-sel" : "";
      const bad = CX.validateCombo(m, n, cb);
      tr.innerHTML = `<td class="txt">${esc(n)}</td><td class="txt">${esc(CX.COMBO_TYPE_LABEL[cb.combo_type || "add"])}</td>` +
        `<td class="txt cx-formula">${esc(comboFormula(m, cb))}</td>` +
        `<td class="txt">${bad ? `<span class="cd-warn" title="${esc(bad)}">invalid</span>` : (CX.isMaxMinCombo(m, n) ? "max / min" : "single")}</td>`;
      tr.addEventListener("click", () => { sel = n; refresh(); });
      tr.addEventListener("dblclick", () => { sel = n; modify(); });
      tb.appendChild(tr);
    }
    table.appendChild(tb);
    tableWrap.querySelectorAll(".cd-empty").forEach(e => e.remove());
    if (!names.length) tableWrap.appendChild(el("p", "muted cd-empty", "No load combinations."));
    for (const id of ["cxCombosCopy", "cxCombosModify", "cxCombosDelete"])
      side.querySelector("#" + id).disabled = !sel;
    fb.note.textContent = `${names.length} combination${names.length === 1 ? "" : "s"}`;
  };
  const fb = footBar("", [btn("OK", "btn-primary", () => dlg.close(), null, "cxCombosOk")]);
  const dlg = dialog("cxCombosDlg", { title: "Define Load Combinations", glyph: "combo", wide: true, body, foot: fb.wrap, onUnits: refresh });
  refresh();
  return dlg;
}

/* ================================================================
   P-Delta options
   ================================================================ */
export function openPDeltaOptions(ctx) {
  const m = ctx.store.model;
  if (!m) return null;
  const po = CX.pdeltaOptions(m);
  const d = {
    method: po.method,
    rows: Object.entries(po.load_factors).map(([p, f]) => ({ p, f })),
    max_iterations: po.max_iterations, tolerance: po.tolerance, include_in: po.include_in,
  };
  const pats = Object.keys(m.patterns || {});
  if (!d.rows.length) {
    if (m.patterns && m.patterns.DEAD) d.rows.push({ p: "DEAD", f: 1.0 });
    else if (pats.length) d.rows.push({ p: pats[0], f: 1.0 });
  }
  const seeded = !Object.keys(po.load_factors).length;
  const body = el("div", "cx-pdelta");
  const err = errorLine("cxPdError");
  const check = () => {
    if (d.method !== "iterative_loads") return "";
    if (!d.rows.length) return "Iterative P-Delta needs at least one load pattern.";
    const seen = new Set();
    for (const r of d.rows) {
      if (!pats.includes(r.p)) return `Unknown load pattern ${r.p}.`;
      if (seen.has(r.p)) return `${r.p} is listed twice.`;
      seen.add(r.p);
      if (!isNum(r.f)) return `Scale factor of ${r.p} must be finite.`;
    }
    if (d.rows.every(r => r.f === 0)) return "At least one scale factor must be non-zero.";
    if (!(Number.isInteger(d.max_iterations) && d.max_iterations >= 1 && d.max_iterations <= 100)) return "Maximum iterations must be a whole number 1 – 100.";
    if (!(isNum(d.tolerance) && d.tolerance > 0)) return "Relative tolerance must be > 0.";
    return "";
  };
  const draw = () => {
    body.textContent = "";
    body.appendChild(el("p", "muted dlg-intro",
      "Model-wide geometric stiffness K<sub>g</sub> used by every linear analysis (static, modal, response spectrum, linear time history). " +
      "Static cases with their own P-Δ / large-displacement setting keep their nonlinear flow."));
    const g1 = group("P-Delta Method");
    const meth = el("div", "cx-radios");
    for (const v of ["none", "non_iterative_mass", "iterative_loads"])
      meth.appendChild(radio("cxPdMethod", v, d.method === v, CX.PDELTA_METHOD_LABEL[v], x => { d.method = x; draw(); }, "cxPd_" + v));
    g1.appendChild(meth);
    if (d.method === "non_iterative_mass")
      g1.appendChild(el("p", "muted cd-note", "Story weight P<sub>i</sub> = g·Σm above each story (from the mass source) acts as a −P/h story spring. No iteration, no torsional term."));
    body.appendChild(g1);

    const g2 = group("Iterative P-Delta Load Case — Load Pattern / Scale Factor", d.method === "iterative_loads" ? "" : "cx-disabled");
    const on = d.method === "iterative_loads";
    const tbl = el("div", "cx-rows");
    tbl.id = "cxPdRows";
    tbl.appendChild(el("div", "cx-row cx-row3 head", "<span>Load Pattern</span><span>Scale Factor</span><span></span>"));
    d.rows.forEach((r, i) => {
      const line = el("div", "cx-row cx-row3");
      const s = select(pats.map(p => [p, p]), r.p, v => { r.p = v; live(); }, null, "cx-pd-pat");
      s.disabled = !on;
      const f = numInput("none", r.f, { cls: "cx-pd-f", disabled: !on, onSet: v => { r.f = v; live(); } });
      const x = btn("×", "btn-small", () => { d.rows.splice(i, 1); draw(); }, "Remove");
      x.disabled = !on;
      line.append(s, f, x);
      tbl.appendChild(line);
    });
    g2.appendChild(tbl);
    const add = btn("Add", "btn-small cd-add", () => {
      const used = new Set(d.rows.map(r => r.p));
      const p = pats.find(q => !used.has(q));
      if (p) { d.rows.push({ p, f: 1.0 }); draw(); }
    }, "Add a load pattern", "cxPdAdd");
    add.disabled = !on || d.rows.length >= pats.length;
    g2.appendChild(add);
    const two = el("div", "cd-cols");
    two.append(
      row("Maximum iterations", numInput("none", d.max_iterations, { id: "cxPdIter", int: true, min: 1, max: 100, disabled: !on, onSet: v => { d.max_iterations = v; live(); } }), "", "1 – 100"),
      row("Relative tolerance", numInput("none", d.tolerance, { id: "cxPdTol", gt: 0, disabled: !on, onSet: v => { d.tolerance = v; live(); } }), "", "max|ΔN| / max|N|"));
    g2.appendChild(two);
    body.appendChild(g2);
    body.appendChild(err);
    live();
  };
  const live = () => { const msg = check(); showError(err, msg); okBtn.disabled = !!msg; };
  const commit = () => {
    const msg = check();
    if (msg) { showError(err, msg); return; }
    const out = {
      method: d.method,
      load_factors: d.method === "iterative_loads" || !seeded
        ? Object.fromEntries(d.rows.map(r => [r.p, r.f])) : {},
      max_iterations: d.max_iterations, tolerance: d.tolerance, include_in: d.include_in,
    };
    if (d.method === "none") out.load_factors = {};
    if (CX.pdeltaIsDefault(out)) delete m.pdelta_options;
    else m.pdelta_options = out;
    ctx.markDirty();
    ctx.onChange && ctx.onChange("pdelta");
    dlg.close();
  };
  const okBtn = btn("OK", "btn-primary", commit, null, "cxPdOk");
  const fb = footBar("Define → P-Delta Options", [btn("Cancel", "", () => dlg.close(), null, "cxPdCancel"), okBtn]);
  const dlg = dialog("cxPdDlg", { title: "P-Delta Options", glyph: "pdelta", body, foot: fb.wrap, onUnits: draw });
  draw();
  return dlg;
}

/* ================================================================
   Time-history case — Load Data (components)
   ================================================================ */
const DIR_OPTS = [["UX", "U1 (UX)"], ["UY", "U2 (UY)"], ["UZ", "U3 (UZ)"]];
const vertMassMissing = m => !(m.mass_options && m.mass_options.include_vertical);
export const VERT_WARN = "U3 (UZ) acceleration acts on vertical mass only — Mass Source “include vertical mass” is off, so only explicit nodal UZ masses respond.";

/** Clean component objects (only meaningful keys, SI). */
function cleanComponent(r) {
  if (r.type === "pattern")
    return { pattern: r.pattern, function: r.function, scale: r.scale, time_shift: r.time_shift };
  const c = { direction: r.direction };
  if (r.function) c.function = r.function;
  c.scale = r.scale;
  c.angle_deg = r.direction === "UZ" ? 0 : r.angle_deg;
  c.time_shift = r.time_shift;
  return c;
}
export function validateComponents(m, tc, rows) {
  const fns = Object.keys(m.th_functions || {});
  const pats = Object.keys(m.patterns || {});
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i], at = `Row ${i + 1}`;
    if (!isNum(r.scale)) return `${at}: scale factor must be finite.`;
    if (!(isNum(r.time_shift) && r.time_shift >= 0)) return `${at}: time shift must be ≥ 0.`;
    if (r.function && !fns.includes(r.function)) return `${at}: function ${r.function} is not defined.`;
    if (r.type === "pattern") {
      if (!pats.includes(r.pattern)) return `${at}: pick a load pattern.`;
      if (!r.function) return `${at}: a load-pattern row needs a time function.`;
    } else {
      if (!["UX", "UY", "UZ"].includes(r.direction)) return `${at}: pick a direction.`;
      if (!isNum(r.angle_deg)) return `${at}: angle must be finite.`;
      if (!r.function && !(tc.function || (tc.accel || []).length))
        return `${at}: no function and the case has no record of its own.`;
    }
  }
  return "";
}

export function openThLoadData(ctx, name) {
  const m = ctx.store.model;
  const tc = m && (m.th_cases || {})[name];
  if (!tc) return null;
  const rows = (tc.components || []).map(c => c && c.pattern !== undefined
    ? { type: "pattern", pattern: c.pattern, function: c.function || "", scale: c.scale ?? 1, time_shift: c.time_shift ?? 0, direction: "UX", angle_deg: 0 }
    : { type: "accel", direction: (c && c.direction) || "UX", function: (c && c.function) || "", scale: c?.scale ?? 1,
        angle_deg: c?.angle_deg ?? 0, time_shift: c?.time_shift ?? 0, pattern: "" });
  const fns = Object.keys(m.th_functions || {});
  const pats = Object.keys(m.patterns || {});
  const body = el("div", "cx-thld");
  const err = errorLine("cxThError");
  const draw = () => {
    body.textContent = "";
    body.appendChild(el("p", "muted dlg-intro",
      `Loads applied by time-history case <b>${esc(name)}</b> (ETABS “Load Data”). ` +
      "Acceleration rows shake the base along U1/U2/U3 (angle rotates a horizontal component counter-clockwise in plan); " +
      "Load Pattern rows apply a pattern × f(t). The case scale multiplies every row. " +
      `<b>Empty table</b> = the legacy single record (direction ${esc(tc.direction)}, ${tc.function ? "function " + esc(tc.function) : "inline record"}).`));
    const g = group("Load Data");
    const tbl = el("div", "cx-rows");
    tbl.id = "cxThRows";
    tbl.appendChild(el("div", "cx-row cx-th head",
      `<span>Load Type</span><span>Load Name</span><span>Function</span><span>Scale Factor</span><span>Angle deg</span><span>Time Shift ${esc(U.label("period"))}</span><span></span>`));
    rows.forEach((r, i) => {
      const line = el("div", "cx-row cx-th");
      line.dataset.row = String(i);
      const typ = select([["accel", "Acceleration"], ["pattern", "Load Pattern"]], r.type, v => {
        r.type = v;
        if (v === "pattern") { r.pattern = r.pattern || pats[0] || ""; r.function = r.function || fns[0] || ""; }
        draw();
      }, null, "cx-th-type");
      const nameSel = r.type === "pattern"
        ? select(pats.map(p => [p, p]), r.pattern, v => { r.pattern = v; live(); }, null, "cx-th-name")
        : select(DIR_OPTS, r.direction, v => { r.direction = v; if (v === "UZ") r.angle_deg = 0; draw(); }, null, "cx-th-name");
      const fnOpts = (r.type === "pattern" ? [] : [["", "(case record)"]]).concat(fns.map(f => [f, f]));
      const fnSel = select(fnOpts.length ? fnOpts : [["", "(no functions)"]], r.function, v => { r.function = v; live(); }, null, "cx-th-fn");
      const sc = numInput("none", r.scale, { cls: "cx-th-scale", onSet: v => { r.scale = v; live(); } });
      const ang = numInput("none", r.type === "pattern" ? null : r.angle_deg, {
        cls: "cx-th-angle", disabled: r.type === "pattern" || r.direction === "UZ",
        title: r.direction === "UZ" ? "U3 has no plan angle" : "", onSet: v => { r.angle_deg = v; live(); } });
      const ts = numInput("period", r.time_shift, { cls: "cx-th-shift", min: 0, onSet: v => { r.time_shift = v; live(); } });
      const x = btn("×", "btn-small", () => { rows.splice(i, 1); draw(); }, "Remove this row");
      line.append(typ, nameSel, fnSel, sc, ang, ts, x);
      tbl.appendChild(line);
    });
    if (!rows.length) tbl.appendChild(el("p", "muted cd-empty", "No rows — the case runs its legacy single record."));
    g.appendChild(tbl);
    const acts = el("div", "cd-inline");
    acts.append(
      btn("Add Acceleration", "btn-small", () => {
        const used = new Set(rows.filter(r => r.type === "accel").map(r => r.direction));
        const dir = ["UX", "UY", "UZ"].find(q => !used.has(q)) || "UX";
        rows.push({ type: "accel", direction: dir, function: "", scale: 1, angle_deg: 0, time_shift: 0, pattern: "" });
        draw();
      }, "Add a ground-acceleration row", "cxThAddAcc"),
      btn("Add Load Pattern", "btn-small", () => {
        rows.push({ type: "pattern", pattern: pats[0] || "", function: fns[0] || "", scale: 1, angle_deg: 0, time_shift: 0, direction: "UX" });
        draw();
      }, "Add a load-pattern row (needs a TH function)", "cxThAddPat"),
      btn("Clear All", "btn-small", () => { rows.length = 0; draw(); }, "Back to the legacy single record", "cxThClear"));
    g.appendChild(acts);
    if (!fns.length) g.appendChild(el("p", "muted cd-note cd-warn", "No TH functions defined — Load Pattern rows need one (Define → Functions)."));
    body.appendChild(g);
    const warn = el("p", "muted cd-note cd-warn hidden");
    warn.id = "cxThVertWarn";
    warn.textContent = VERT_WARN;
    body.appendChild(warn);
    body.appendChild(err);
    live();
  };
  const live = () => {
    const msg = validateComponents(m, tc, rows);
    showError(err, msg);
    okBtn.disabled = !!msg;
    const w = body.querySelector("#cxThVertWarn");
    if (w) w.classList.toggle("hidden", !(rows.some(r => r.type === "accel" && r.direction === "UZ") && vertMassMissing(m)));
  };
  const commit = () => {
    const msg = validateComponents(m, tc, rows);
    if (msg) { showError(err, msg); return; }
    if (rows.length) tc.components = rows.map(cleanComponent);
    else delete tc.components;
    ctx.markDirty();
    ctx.onChange && ctx.onChange("th");
    dlg.close();
  };
  const okBtn = btn("OK", "btn-primary", commit, null, "cxThOk");
  const fb = footBar(`${rows.length} row${rows.length === 1 ? "" : "s"}`, [btn("Cancel", "", () => dlg.close(), null, "cxThCancel"), okBtn]);
  const dlg = dialog("cxThDlg", { title: `Load Data — ${name}`, glyph: "th", wide: true, body, foot: fb.wrap, onUnits: draw });
  draw();
  return dlg;
}

/** Compact Load Data summary for the TH card in the loads editor. */
export function thLoadDataBlock(m, name) {
  const tc = (m.th_cases || {})[name];
  const wrap = el("div", "cx-thsum");
  if (!tc) return wrap;
  const comps = tc.components || [];
  const txt = comps.length
    ? comps.map(c => c.pattern !== undefined
      ? `${esc(c.pattern)}·${esc(c.function)} ×${fnum(c.scale ?? 1)}`
      : `${esc(c.direction)}${c.angle_deg ? "∠" + fnum(c.angle_deg) + "°" : ""}·${esc(c.function || "record")} ×${fnum(c.scale ?? 1)}` +
        (c.time_shift ? ` +${fnum(c.time_shift)}s` : "")).join(" · ")
    : "legacy single record (direction + record / function below)";
  const s = el("span", "cx-thsum-txt", `<b>Load Data</b> <span class="muted">${comps.length ? `${comps.length} row${comps.length > 1 ? "s" : ""} — direction field ignored:` : ""}</span> ${txt}`);
  const b = btn("Load Data…", "btn-small cx-thsum-btn", () => {
    const sky = window.__sky;
    if (sky && sky.openThLoadData) sky.openThLoadData(name);
  }, "Acceleration / load-pattern rows (multi-component time history)");
  b.dataset.th = name;
  wrap.append(s, b);
  if (comps.some(c => c.direction === "UZ") && vertMassMissing(m))
    wrap.appendChild(el("span", "cx-thsum-warn cd-warn", "⚠ " + esc(VERT_WARN)));
  return wrap;
}

/* ================================================================
   Results: P-Delta summary card + Analysis Log
   ================================================================ */
function pdeltaHtml(pd) {
  if (!pd) return "";
  const rows = [];
  rows.push(["Method", CX.PDELTA_METHOD_LABEL[pd.method] || pd.method]);
  if (pd.n_springs != null) rows.push(["Geometric-stiffness springs", String(pd.n_springs)]);
  if (pd.iterations != null) rows.push(["Iterations", String(pd.iterations)]);
  if (pd.converged != null) rows.push(["Converged", pd.converged ? "yes" : `<span class="cd-warn">no</span>`]);
  if (Array.isArray(pd.relative_change) && pd.relative_change.length)
    rows.push(["Relative change", pd.relative_change.map(v => (+v).toExponential(2)).join(" → ")]);
  if (pd.load_factors && Object.keys(pd.load_factors).length)
    rows.push(["Load combination", Object.entries(pd.load_factors).map(([p, f]) => `${fnum(f)}·${esc(p)}`).join(" + ")]);
  if ((pd.cases_own_geometric || []).length)
    rows.push(["Own-geometry cases (not doubled)", pd.cases_own_geometric.map(esc).join(", ")]);
  if ((pd.skipped_stories || []).length)
    rows.push(["Skipped stories", `<span class="cd-warn">${pd.skipped_stories.map(esc).join(", ")}</span>`]);
  let h = `<dl class="cx-kv">${rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
  if (pd.story_P && Object.keys(pd.story_P).length) {
    h += `<table class="data-table cx-storyp"><thead><tr><th class="txt">Story</th><th>P ${esc(U.label("force"))}</th></tr></thead><tbody>` +
      Object.entries(pd.story_P).map(([s, P]) => `<tr><td class="txt">${esc(s)}</td><td>${U.fmt("force", P, 1)}</td></tr>`).join("") +
      "</tbody></table>";
  }
  return h;
}

function renderPdeltaCard(sky) {
  const host = document.getElementById("content-story");
  if (!host) return;
  let card = document.getElementById("pdeltaCard");
  const pd = sky.store.results && sky.store.results.pdelta;
  if (!pd) { if (card) card.remove(); return; }
  if (!card) {
    card = el("div", "diag-block cx-pdcard");
    card.id = "pdeltaCard";
    host.insertBefore(card, host.firstChild);
  }
  card.innerHTML = `<div class="cx-pdhead"><b>P-Delta options</b> <span class="muted">model-wide geometric stiffness (last run)</span></div>` + pdeltaHtml(pd);
}

export function openAnalysisLog(ctx) {
  ensureCss();
  const body = el("div", "cx-log");
  const draw = () => {
    const r = ctx.store.results, m = ctx.store.model;
    body.textContent = "";
    if (!r) { body.appendChild(el("p", "muted cd-empty", "No analysis results yet — run the analysis first.")); return; }
    const g0 = group("Run");
    const ms = ctx.store.lastSolveMs;
    g0.appendChild(el("p", "cd-note", `Model <b>${esc(r.model_name || m?.name || "")}</b>` +
      (ms != null ? ` · solved in ${(ms / 1000).toFixed(1)} s` : "") +
      ` · ${Object.keys(r.cases || {}).length} static · ${Object.keys(r.combos || {}).length} combos · ` +
      `${Object.keys(r.rs_cases || {}).length} RS · ${Object.keys(r.th_cases || {}).length} TH`));
    body.appendChild(g0);
    const g1 = group("P-Delta");
    g1.id = "cxLogPdelta";
    if (r.pdelta) g1.appendChild(el("div", "", pdeltaHtml(r.pdelta)));
    else g1.appendChild(el("p", "muted cd-note", "P-Delta options: None (per-case P-Δ settings only)."));
    body.appendChild(g1);
    const g2 = group("Load combinations");
    const cs = r.combo_status || {};
    const names = Object.keys(m?.combos || {});
    if (names.length) {
      g2.appendChild(el("table", "data-table cx-logtbl",
        `<thead><tr><th class="txt">Combination</th><th class="txt">Type</th><th class="txt">Status</th><th class="txt">Result</th><th class="txt">Note</th></tr></thead><tbody>` +
        names.map(n => {
          const cd = (r.combos || {})[n];
          const st = cs[n] || (cd ? "finished" : "skipped");
          return `<tr><td class="txt">${esc(n)}</td><td class="txt">${esc(CX.COMBO_TYPE_LABEL[m.combos[n].combo_type || "add"])}</td>` +
            `<td class="txt">${esc(st)}</td><td class="txt">${cd ? (cd.min ? "max / min" : "single") : "—"}</td>` +
            `<td class="txt">${cd && cd.warning ? `<span class="cd-warn">${esc(cd.warning)}</span>` : ""}</td></tr>`;
        }).join("") + "</tbody>"));
    } else g2.appendChild(el("p", "muted cd-note", "No load combinations."));
    body.appendChild(g2);
    const thm = Object.entries(r.th_cases || {}).filter(([, t]) => t.multi_component);
    if (thm.length) {
      const g3 = group("Multi-component time histories");
      for (const [n, t] of thm) {
        const mc = t.multi_component;
        g3.appendChild(el("p", "cd-note", `<b>${esc(n)}</b> · dt ${fnum(mc.dt)} s · ${mc.n_steps} steps · ` +
          (mc.components || []).map(c => c.pattern !== undefined
            ? `${esc(c.pattern)} × ${fnum(c.effective_scale)}`
            : `${esc(c.direction)} × ${fnum(c.effective_scale)}${c.angle_deg ? ` ∠${fnum(c.angle_deg)}°` : ""}`).join(" · ")));
      }
      body.appendChild(g3);
    }
    const warns = [];
    if (r.warning) warns.push(...String(r.warning).split(/;\s*(?=combo|TH|time|RS|case)/));
    if (Array.isArray(r.warnings)) warns.push(...r.warnings);
    const g4 = group("Warnings");
    g4.id = "cxLogWarnings";
    g4.appendChild(warns.length ? el("ul", "cx-warnlist", warns.map(w => `<li>${esc(w)}</li>`).join(""))
      : el("p", "muted cd-note", "None."));
    body.appendChild(g4);
    try { window.__sky && window.__sky.analysisLogExtra && window.__sky.analysisLogExtra(body, r); } catch (e) { console.warn("analysis log extra", e); }   // js/reportx.js run times
  };
  const fb = footBar("Analyze → Analysis Log", [btn("Close", "btn-primary", () => dlg.close(), null, "cxLogClose")]);
  const dlg = dialog("cxLogDlg", { title: "Analysis Log", glyph: "log", wide: true, body, foot: fb.wrap, onUnits: draw });
  draw();
  return dlg;
}

/* ================================================================
   install
   ================================================================ */
function makeCtx(sky) {
  sky = sky || window.__sky;
  return {
    store: sky.store,
    markDirty: () => sky.markDirty && sky.markDirty(),
    toast: (...a) => sky.toast && sky.toast(...a),
    onChange: () => {
      if (sky.store.mode === "loads" && sky.loadsEditor) sky.loadsEditor.render();
      const ex = sky.etabs;
      if (ex && ex.refresh) ex.refresh();
    },
  };
}
export function installComboDialogs(sky) {
  if (!sky) return;
  Object.assign(sky, {
    openCombos: () => openCombos(makeCtx(sky)),
    openComboData: (name, opts) => openComboData(makeCtx(sky), name, opts),
    openPDeltaOptions: () => openPDeltaOptions(makeCtx(sky)),
    openThLoadData: name => openThLoadData(makeCtx(sky), name),
    openAnalysisLog: () => openAnalysisLog(makeCtx(sky)),
    closeComboDialog: closeDialog,
    comboRefs: CX,
  });
  const upd = () => { try { renderPdeltaCard(sky); } catch (e) { console.warn("pdelta card", e); } };
  document.addEventListener("sky:results-changed", upd);
  document.addEventListener("sky:units-changed", upd);
}
