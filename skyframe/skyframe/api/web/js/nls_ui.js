/* SkyFrame — shared dialog kit for js/nlsdlg.js (Nonlinear Static cases,
   Modal stiffness) and js/diaphdlg.js (Diaphragms, Additional Mass).

   Same markup / classes as js/casedlg.js (.modal-backdrop.sky-dlg.cd-dlg,
   .dlg-group, .cd-row …) so the dialogs look identical; a dialog stack makes
   Esc close only the topmost one. Every numeric input goes through
   js/units.js: the input shows U.inputValue(kind, si), the draft stays SI,
   and an untouched input returns its exact original SI value. Inline SVG
   only (CSP). Loads its small stylesheet (/static/nlsdlg.css) once. */

import U from "./units.js";

export const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const clone = o => JSON.parse(JSON.stringify(o));
export const isNum = v => typeof v === "number" && isFinite(v);

export function ensureCss() {
  if (document.querySelector("link[data-nls-css]")) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/nlsdlg.css"; l.setAttribute("data-nls-css", "1");
  document.head.appendChild(l);
}

/* ------------------------------------------------ inline glyphs (20×20) */
const GLYPH = {
  nls: '<path d="M3 16V4M3 16h14"/><path d="M4 15l4-6 3 2 5-6"/><circle cx="8" cy="9" r="1"/>',
  modal: '<path d="M3 10c2-5 4-5 6 0s4 5 6 0"/><path d="M3 16h14"/>',
  diaph: '<path d="M3 7l7-3 7 3-7 3z"/><path d="M3 13l7-3 7 3-7 3z"/>',
  mass: '<path d="M7 7h6l2 9H5z"/><circle cx="10" cy="5" r="2"/>',
  chart: '<path d="M3 16V4M3 16h14"/><path d="M4 14c3-6 6-8 12-9"/>',
};
export const svgIcon = (k, cls = "dlg-ico") =>
  `<svg class="${cls}" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" ` +
  `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${GLYPH[k] || ""}</svg>`;

/* ------------------------------------------------ dialog shell + stack */
const stack = [];
function onKey(e) {
  if (e.key !== "Escape" || !stack.length) return;
  e.stopImmediatePropagation(); e.preventDefault();
  stack[stack.length - 1].close();
}
export function dialog(id, { title, glyph, wide = false, narrow = false, body, foot, onClose, onUnits }) {
  ensureCss();
  closeDialog(id);
  const back = document.createElement("div");
  back.className = "modal-backdrop sky-dlg cd-dlg nls-dlg";
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

export function btn(label, cls, onClick, title, id) {
  const b = document.createElement("button");
  b.className = "btn" + (cls ? " " + cls : "");
  b.textContent = label;
  if (title) b.title = title;
  if (id) b.id = id;
  b.addEventListener("click", onClick);
  return b;
}
export function footBar(note, buttons) {
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
export function errorLine(id) {
  const p = document.createElement("p");
  p.className = "field-error hidden dlg-error";
  if (id) p.id = id;
  return p;
}
export const showError = (p, msg) => { p.textContent = msg || ""; p.classList.toggle("hidden", !msg); };
export function group(legend, cls = "") {
  const g = document.createElement("fieldset");
  g.className = "dlg-group" + (cls ? " " + cls : "");
  const l = document.createElement("legend");
  l.textContent = legend;
  g.appendChild(l);
  return g;
}
export function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
}

/** Unit-aware number input (casedlg.js numInput semantics): shows
    U.inputValue(kind, si); a change parses display → SI and validates in SI;
    an invalid entry reverts and flags the field. `o.allowEmpty` maps a blank
    entry to null. `kind` may be a function (re-evaluated on every show). */
export function numInput(kind, si, o = {}) {
  const i = document.createElement("input");
  i.type = "number"; i.step = "any";
  i.className = "cd-num" + (o.cls ? " " + o.cls : "");
  if (o.id) i.id = o.id;
  const K = () => (typeof kind === "function" ? kind() : kind);
  i.dataset.nlq = K();
  if (o.placeholder != null) i.placeholder = o.placeholder;
  let cur = si;
  const show = () => { i.value = cur == null || cur === "" ? "" : U.inputValue(K(), cur); };
  show();
  if (o.disabled) i.disabled = true;
  i.addEventListener("change", () => {
    const t = i.value.trim();
    i.classList.remove("is-bad"); i.title = o.title || "";
    if (t === "" && o.allowEmpty) { cur = null; o.onSet && o.onSet(null); return; }
    const v = U.parse(K(), t);
    let msg = "";
    if (!isFinite(v)) msg = "Enter a number";
    else if (o.int && !Number.isInteger(v)) msg = "Enter a whole number";
    else if (o.min != null && v < o.min) msg = `Must be ≥ ${U.inputValue(K(), o.min)}`;
    else if (o.max != null && v > o.max) msg = `Must be ≤ ${U.inputValue(K(), o.max)}`;
    else if (o.gt != null && !(v > o.gt)) msg = `Must be > ${U.inputValue(K(), o.gt)}`;
    else if (o.lt != null && !(v < o.lt)) msg = `Must be < ${U.inputValue(K(), o.lt)}`;
    else if (o.nonzero && v === 0) msg = "Must be non-zero";
    if (msg) {
      show();
      i.classList.add("is-bad"); i.title = msg;
      o.onError && o.onError(msg);
      return;
    }
    cur = v;
    o.onSet && o.onSet(v);
  });
  if (o.title) i.title = o.title;
  return i;
}
/** label · control · unit row */
export function row(label, control, kind, hint) {
  const r = el("label", "cd-row");
  const l = el("span", "cd-lbl");
  l.textContent = label;
  r.append(l, control);
  const u = el("span", "cd-unit");
  u.textContent = kind ? U.label(kind) : "";
  r.appendChild(u);
  if (hint) { const h = el("span", "cd-hint muted"); h.innerHTML = hint; r.appendChild(h); }
  return r;
}
export function select(options, value, onChange, id) {
  const s = document.createElement("select");
  if (id) s.id = id;
  s.innerHTML = options.map(([v, l, dis]) =>
    `<option value="${esc(v)}"${String(v) === String(value ?? "") ? " selected" : ""}${dis ? " disabled" : ""}>${esc(l)}</option>`).join("");
  if (![...s.options].some(o => o.value === String(value ?? "")) && value != null && value !== "")
    s.insertAdjacentHTML("beforeend", `<option value="${esc(value)}" selected>${esc(value)} (missing)</option>`);
  s.addEventListener("change", () => onChange(s.value));
  return s;
}
export function checkbox(text, checked, onChange, id, title) {
  const l = el("label", "dlg-chk");
  if (title) l.title = title;
  const cb = document.createElement("input");
  cb.type = "checkbox"; cb.checked = !!checked;
  if (id) cb.id = id;
  cb.addEventListener("change", () => onChange(cb.checked));
  const s = el("span"); s.textContent = text;
  l.append(cb, s);
  return l;
}
export function radio(name, value, checked, text, onPick, id) {
  const l = el("label", "dlg-chk cd-radio");
  const r = document.createElement("input");
  r.type = "radio"; r.name = name; r.value = value; r.checked = !!checked;
  if (id) r.id = id;
  r.addEventListener("change", () => { if (r.checked) onPick(value); });
  const s = el("span"); s.textContent = text;
  l.append(r, s);
  return l;
}

/* ------------------------------------------------ model geometry helpers */
export const samePt = (a, b, tol = 1e-6) => Array.isArray(a) && Array.isArray(b) &&
  a.length === 3 && b.length === 3 && a.every((v, i) => Math.abs(+v - +b[i]) < tol);
export const storyNames = m => (m.stories || []).map(s => s.name);
export const storyAtZ = (m, z) => {
  const s = (m.stories || []).find(st => Math.abs((st.elevation ?? 0) - z) < 1e-6);
  return s ? s.name : "";
};
/** Every structural joint (member ends, shell corners, link ends), sorted
    by z, y, x. */
export function modelJoints(m) {
  const seen = new Map();
  const add = p => {
    if (!Array.isArray(p) || p.length !== 3 || !p.every(isFinite)) return;
    const k = p.map(v => (+v).toFixed(6)).join(",");
    if (!seen.has(k)) seen.set(k, p.map(Number));
  };
  for (const mm of m.members || []) { add(mm.pi); add(mm.pj); }
  for (const sh of m.shells || []) for (const c of sh.corners || []) add(c);
  for (const l of m.links || []) { add(l.pi); add(l.pj); }
  return [...seen.values()].sort((a, b) => a[2] - b[2] || a[1] - b[1] || a[0] - b[0]);
}
export function snapToJoint(joints, p, tol = 2e-3) {
  let best = null, bd = Infinity;
  for (const j of joints) {
    const d = Math.hypot(j[0] - p[0], j[1] - p[1], j[2] - p[2]);
    if (d < bd) { bd = d; best = j; }
  }
  return best && bd <= tol ? best.slice() : p;
}
export const ptLabel = p => `(${p.map(v => U.fmt("length", v, 2)).join(", ")}) ${U.label("length")}`;

/** Context passed to every dialog: store + markDirty + a refresh of the
    loads pane / model explorer (same as casedlg.js makeCtx). */
export function makeCtx(sky, extra = {}) {
  sky = sky || window.__sky;
  return {
    store: sky.store,
    markDirty: () => sky.markDirty && sky.markDirty(),
    toast: (...a) => sky.toast && sky.toast(...a),
    onChange: key => {
      if (sky.store.mode === "loads" && sky.loadsEditor) sky.loadsEditor.render();
      const ex = sky.etabs;
      if (ex && ex.refresh) ex.refresh();
      extra.onChange && extra.onChange(key);
    },
  };
}
