/* SkyFrame — ETABS "Define > Load Cases" parity dialogs (analysis only).

     Define → Load Cases…                    every analysis case (add / modify / delete)
     Define → Frequency Functions…           model.frequency_functions (steady_state | psd)
     Define → Steady-State Cases…            model.steady_state_cases
     Define → Power Spectral Density Cases…  model.psd_cases
     Time-history case  → Other Parameters   integration / di_damping / solver / energy
     Response-spectrum  → Modal Combination  combo_method + GMC / DSC / rigid / missing mass
     Pushover case      → Load Application   load_distribution / control / start_from
     Display → Frequency-Domain Results…     results.steady_state / results.psd

   Every dialog edits a DRAFT and writes the model only on OK (Cancel / Esc /
   backdrop discard), then calls ctx.markDirty(). Optional keys are written
   ONLY when they differ from the backend default (and removed otherwise), so
   a model edited with defaults round-trips byte-identically through
   POST /api/model. Every numeric input / output goes through js/units.js;
   the draft and the model stay SI. Inline SVG only (CSP). */

import * as ME from "./modeledit.js";
import U from "./units.js";

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const clone = o => JSON.parse(JSON.stringify(o));
const isNum = v => typeof v === "number" && isFinite(v);
const G = 9.80665;

/* ------------------------------------------------ inline glyphs (20×20) */
const GLYPH = {
  cases: '<rect x="3" y="3.5" width="14" height="13" rx="1.5"/><path d="M6 7.5h8M6 10.5h8M6 13.5h5"/>',
  freq: '<path d="M2 14c2 0 2-8 4-8s2 8 4 8 2-8 4-8 2 8 4 8"/>',
  psd: '<path d="M3 16V4M3 16h14"/><path d="M4 13c3 0 3-7 5-7s2 5 4 6 3 1 4 1"/>',
  fn: '<path d="M3 16V4M3 16h14"/><path d="M4 12l3-4 3 2 3-5 3 3"/>',
  th: '<path d="M2 10h2l2-5 3 10 3-8 2 5 2-2h2"/>',
  rs: '<path d="M3 16V4M3 16h14"/><path d="M4 13l2-7h3l7 8"/>',
  push: '<path d="M3 16V4M3 16h14"/><path d="M4 15c3-6 6-8 12-9"/>',
};
const svgIcon = (k, cls = "dlg-ico") =>
  `<svg class="${cls}" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" ` +
  `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${GLYPH[k] || ""}</svg>`;

/* ================================================================
   dialog shell (same markup as js/analysisdlg.js) with a stack so Esc
   closes only the topmost dialog when one opens another
   ================================================================ */
const stack = [];
function onKey(e) {
  if (e.key !== "Escape" || !stack.length) return;
  e.stopImmediatePropagation(); e.preventDefault();
  stack[stack.length - 1].close();
}
function dialog(id, { title, glyph, wide = false, narrow = false, body, foot, onClose, onUnits }) {
  closeDialog(id);
  const back = document.createElement("div");
  back.className = "modal-backdrop sky-dlg cd-dlg";
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

/* ---- unit-aware number input: shows U.inputValue(kind, si); on change parses
   display text → SI (U.parse) and validates IN SI; an invalid entry reverts to
   the previous value and flags the field. The SI value stays authoritative:
   re-rendering after a unit switch re-displays from the draft. */
function numInput(kind, si, o = {}) {
  const i = document.createElement("input");
  i.type = "number"; i.step = "any";
  i.className = "cd-num" + (o.cls ? " " + o.cls : "");
  if (o.id) i.id = o.id;
  i.dataset.cdq = kind;
  if (o.placeholder != null) i.placeholder = o.placeholder;
  let cur = si;
  const show = () => { i.value = cur == null || cur === "" ? "" : U.inputValue(kind, cur); };
  show();
  if (o.disabled) i.disabled = true;
  i.addEventListener("change", () => {
    const t = i.value.trim();
    i.classList.remove("is-bad"); i.title = o.title || "";
    if (t === "" && o.allowEmpty) { cur = null; o.onSet && o.onSet(null); return; }
    const v = U.parse(kind, t);
    let msg = "";
    if (!isFinite(v)) msg = "Enter a number";
    else if (o.int && !Number.isInteger(v)) msg = "Enter a whole number";
    else if (o.min != null && v < o.min) msg = `Must be ≥ ${U.inputValue(kind, o.min)}`;
    else if (o.max != null && v > o.max) msg = `Must be ≤ ${U.inputValue(kind, o.max)}`;
    else if (o.gt != null && !(v > o.gt)) msg = `Must be > ${U.inputValue(kind, o.gt)}`;
    else if (o.lt != null && !(v < o.lt)) msg = `Must be < ${U.inputValue(kind, o.lt)}`;
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
function row(label, control, kind, hint) {
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
function select(options, value, onChange, id) {
  const s = document.createElement("select");
  if (id) s.id = id;
  s.innerHTML = options.map(([v, l]) =>
    `<option value="${esc(v)}"${String(v) === String(value) ? " selected" : ""}>${esc(l)}</option>`).join("");
  if (![...s.options].some(o => o.value === String(value)) && value != null && value !== "")
    s.insertAdjacentHTML("beforeend", `<option value="${esc(value)}" selected>${esc(value)} (missing)</option>`);
  s.addEventListener("change", () => onChange(s.value));
  return s;
}
function checkbox(text, checked, onChange, id, title) {
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

/* ---- model helpers */
const patternNames = m => Object.keys(m.patterns || {});
const staticCaseNames = m => Object.keys(m.cases || {});
const storyNames = m => (m.stories || []).map(s => s.name);
function takenCaseNames(m, except) {
  const s = new Set(ME.allAnalysisCases(m).map(c => c.name));
  for (const n of Object.keys(m.combos || {})) s.add(n);
  s.delete(except);
  return s;
}
function uniqueName(taken, base) {
  let i = 1;
  while (taken.has(`${base}${i}`)) i++;
  return `${base}${i}`;
}
/** Every structural joint of the model (member ends, shell corners, links). */
function modelJoints(m) {
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
/** Snap a point to the nearest joint within `tol` m (display-unit round-off). */
function snapToJoint(joints, p, tol = 2e-3) {
  let best = null, bd = Infinity;
  for (const j of joints) {
    const d = Math.hypot(j[0] - p[0], j[1] - p[1], j[2] - p[2]);
    if (d < bd) { bd = d; best = j; }
  }
  return best && bd <= tol ? best.slice() : p;
}
const ptLabel = p => `(${p.map(v => U.fmt("length", v, 2)).join(", ")}) ${U.label("length")}`;
const storyAt = (m, z) => {
  const s = (m.stories || []).find(st => Math.abs((st.elevation ?? 0) - z) < 1e-6);
  return s ? s.name : (Math.abs(z) < 1e-6 ? "Base" : "");
};

/* ================================================================
   small SVG line chart (function previews, damping curve, results)
   ================================================================ */
function niceTicks(lo, hi, n = 5) {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / n, p = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(k => k * p).find(s => s >= raw) || raw;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toPrecision(12));
  return out;
}
const tickTxt = v => {
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a >= 1e5 || a < 1e-3) return v.toExponential(0).replace("+", "");
  return String(+v.toPrecision(3));
};
export function lineChart(series, o = {}) {
  const W = o.w || 440, H = o.h || 180, L = 50, R = 10, T = 10, B = 28;
  const logX = !!o.logX, logY = !!o.logY;
  const tx = v => logX ? Math.log10(v) : v, ty = v => logY ? Math.log10(v) : v;
  const okX = v => isFinite(v) && (!logX || v > 0), okY = v => isFinite(v) && (!logY || v > 0);
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const s of series) s.xs.forEach((x, i) => {
    const y = s.ys[i];
    if (!okX(x) || !okY(y)) return;
    x0 = Math.min(x0, tx(x)); x1 = Math.max(x1, tx(x));
    y0 = Math.min(y0, ty(y)); y1 = Math.max(y1, ty(y));
  });
  const svgOpen = `<svg class="cd-chart${o.cls ? " " + o.cls : ""}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.aria || "chart")}">`;
  if (!isFinite(x0)) return `${svgOpen}<text class="cd-ax-text" x="${W / 2}" y="${H / 2}" text-anchor="middle">no data</text></svg>`;
  if (!logY && o.zeroY !== false) { y0 = Math.min(y0, 0); y1 = Math.max(y1, 0); }
  if (x1 === x0) { x0 -= 0.5; x1 += 0.5; }
  if (y1 === y0) { y0 -= logY ? 1 : (Math.abs(y0) || 1) * 0.5; y1 += logY ? 1 : (Math.abs(y1) || 1) * 0.5; }
  const pad = (y1 - y0) * 0.06; y1 += pad; if (!logY && y0 < 0) y0 -= pad;
  const X = v => L + (tx(v) - x0) / (x1 - x0) * (W - L - R);
  const Y = v => T + (1 - (ty(v) - y0) / (y1 - y0)) * (H - T - B);
  let g = `<rect class="cd-frame" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"/>`;
  const xt = logX ? niceTicks(Math.floor(x0), Math.ceil(x1), 6).filter(Number.isInteger).map(e => 10 ** e).filter(v => tx(v) >= x0 - 1e-9 && tx(v) <= x1 + 1e-9)
    : niceTicks(x0, x1, 6);
  const yt = logY ? niceTicks(Math.floor(y0), Math.ceil(y1), 5).filter(Number.isInteger).map(e => 10 ** e).filter(v => ty(v) >= y0 - 1e-9 && ty(v) <= y1 + 1e-9)
    : niceTicks(y0, y1, 5);
  for (const v of xt) g += `<line class="cd-grid" x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${H - B}"/><text class="cd-ax-text" x="${X(v)}" y="${H - B + 12}" text-anchor="middle">${tickTxt(v)}</text>`;
  for (const v of yt) g += `<line class="cd-grid" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/><text class="cd-ax-text" x="${L - 4}" y="${Y(v) + 3}" text-anchor="end">${tickTxt(v)}</text>`;
  for (const v of (o.vlines || [])) {
    if (!okX(v) || tx(v) < x0 || tx(v) > x1) continue;
    g += `<line class="cd-vline" x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${H - B}"/>`;
  }
  series.forEach((s, k) => {
    let d = "", pen = false;
    s.xs.forEach((x, i) => {
      const y = s.ys[i];
      if (!okX(x) || !okY(y)) { pen = false; return; }
      d += `${pen ? "L" : "M"}${X(x).toFixed(1)},${Y(y).toFixed(1)}`;
      pen = true;
    });
    g += `<path class="cd-line cd-line-${k}${s.cls ? " " + s.cls : ""}" d="${d}"/>`;
    if (s.dots) s.xs.forEach((x, i) => {
      if (okX(x) && okY(s.ys[i])) g += `<circle class="cd-dot" cx="${X(x).toFixed(1)}" cy="${Y(s.ys[i]).toFixed(1)}" r="2.6"/>`;
    });
  });
  if (o.xLabel) g += `<text class="cd-ax-unit" x="${W - R}" y="${H - 3}" text-anchor="end">${esc(o.xLabel)}</text>`;
  if (o.yLabel) g += `<text class="cd-ax-unit" x="${L + 4}" y="${T + 10}">${esc(o.yLabel)}</text>`;
  return svgOpen + g + "</svg>";
}

/* ================================================================
   Frequency functions (model.frequency_functions)
   ================================================================ */
const FN_KIND_LABEL = { steady_state: "Steady State", psd: "Power Spectral Density" };
const FN_DEFAULT_POINTS = {
  steady_state: [[0, 1], [20, 1]],
  psd: [[0.5, 0.01], [20, 0.01]],
};
/** Names of the frequency functions referenced by frequency-case loads. */
function fnRefs(m, fname) {
  const out = [];
  for (const [dk, label] of [["steady_state_cases", "SS"], ["psd_cases", "PSD"]])
    for (const [n, c] of Object.entries(m[dk] || {}))
      if ((c.loads || []).some(l => l.function === fname)) out.push(`${label} ${n}`);
  return out;
}
export function validateFrequencyFunction(fn) {
  const pts = fn.points || [];
  if (!fn.name) return "Function name is required.";
  if (pts.length < 2) return `${fn.name}: needs at least 2 points.`;
  let prev = -1;
  for (const p of pts) {
    if (!isNum(p[0]) || !isNum(p[1])) return `${fn.name}: every point needs a numeric frequency and value.`;
    if (p[0] < 0 || p[0] <= prev) return `${fn.name}: frequencies must be ≥ 0 and strictly increasing.`;
    if (fn.kind === "psd" && p[1] < 0) return `${fn.name}: PSD values must be ≥ 0.`;
    prev = p[0];
  }
  return "";
}

export function openFreqFunctions(ctx, opts = {}) {
  const m = ctx.store.model;
  if (!m) return null;
  const draft = clone(m.frequency_functions || {});
  const renames = {};                       // original name → current name
  for (const n of Object.keys(draft)) renames[n] = n;
  let sel = opts.select && draft[opts.select] ? opts.select : Object.keys(draft)[0] || null;
  let logScale = false, textMode = false;

  const body = el("div", "cd-fn");
  const err = errorLine("ffError");
  const draw = () => {
    body.textContent = "";
    const intro = el("p", "muted dlg-intro",
      "Frequency functions for <b>Steady State</b> (load amplitude multiplier vs f) and " +
      "<b>Power Spectral Density</b> (one-sided PSD of the load multiplier per Hz) load cases. " +
      "Linear interpolation; zero outside the defined range.");
    body.appendChild(intro);
    const grid = el("div", "cd-fn-grid");
    /* ---- list */
    const left = el("div", "cd-fn-list");
    const lt = el("div", "dlg-group-title"); lt.textContent = "Functions";
    left.appendChild(lt);
    const list = el("div", "cd-list"); list.id = "ffList";
    const names = Object.keys(draft);
    if (!names.length) list.innerHTML = `<p class="muted cd-empty">No frequency functions.</p>`;
    for (const n of names) {
      const b = el("button", "cd-list-item" + (n === sel ? " is-sel" : ""));
      b.dataset.fn = n;
      b.innerHTML = `${svgIcon(draft[n].kind === "psd" ? "psd" : "freq", "cd-li-ico")}<span>${esc(n)}</span>` +
        `<span class="cd-badge">${draft[n].kind === "psd" ? "PSD" : "SS"}</span>`;
      b.addEventListener("click", () => { sel = n; draw(); });
      list.appendChild(b);
    }
    left.appendChild(list);
    const addNew = kind => {
      const taken = new Set(Object.keys(draft));
      const n = uniqueName(taken, kind === "psd" ? "PSDFN" : "SSFN");
      draft[n] = { name: n, kind, points: clone(FN_DEFAULT_POINTS[kind]) };
      sel = n; draw();
    };
    const acts = el("div", "cd-list-acts");
    acts.append(
      btn("+ Steady State", "btn-small", () => addNew("steady_state"), "Add a steady-state amplitude function", "ffAddSS"),
      btn("+ PSD", "btn-small", () => addNew("psd"), "Add a power-spectral-density function", "ffAddPsd"));
    const del = btn("Delete", "btn-small", () => {
      if (!sel) return;
      const orig = Object.keys(renames).find(k => renames[k] === sel);
      const refs = orig ? fnRefs(m, orig) : [];
      if (refs.length) { showError(err, `Can't delete ${sel} — used by ${refs.join(", ")}.`); return; }
      delete draft[sel];
      if (orig) delete renames[orig];
      sel = Object.keys(draft)[0] || null; draw();
    }, "Delete the selected function", "ffDelete");
    del.disabled = !sel;
    acts.appendChild(del);
    left.appendChild(acts);
    grid.appendChild(left);

    /* ---- editor */
    const right = el("div", "cd-fn-edit");
    if (!sel) {
      right.innerHTML = `<p class="muted cd-empty">Add a function to edit its points.</p>`;
    } else {
      const fn = draft[sel];
      const g1 = group("Function");
      const nameIn = document.createElement("input");
      nameIn.type = "text"; nameIn.id = "ffName"; nameIn.value = fn.name; nameIn.spellcheck = false;
      nameIn.addEventListener("change", () => {
        const nu = nameIn.value.trim();
        if (!nu || (nu !== sel && draft[nu])) { nameIn.value = sel; showError(err, "Name empty or already in use."); return; }
        if (nu === sel) return;
        const d = { ...draft[sel], name: nu };
        const rebuilt = {};
        for (const [k, v] of Object.entries(draft)) rebuilt[k === sel ? nu : k] = k === sel ? d : v;
        for (const k of Object.keys(draft)) delete draft[k];
        Object.assign(draft, rebuilt);
        for (const o of Object.keys(renames)) if (renames[o] === sel) renames[o] = nu;
        sel = nu; draw();
      });
      g1.appendChild(row("Name", nameIn));
      const orig = Object.keys(renames).find(k => renames[k] === sel);
      const refs = orig ? fnRefs(m, orig) : [];
      const kindSel = select([["steady_state", FN_KIND_LABEL.steady_state], ["psd", FN_KIND_LABEL.psd]], fn.kind, v => {
        if (refs.length) { kindSel.value = fn.kind; showError(err, `Kind is fixed while used by ${refs.join(", ")}.`); return; }
        fn.kind = v; draw();
      }, "ffKind");
      g1.appendChild(row("Function type", kindSel));
      const vu = fn.kind === "psd" ? "(multiplier)²/Hz" : "× load";
      const note = el("p", "muted cd-note", fn.kind === "psd"
        ? "Value = one-sided PSD of the load multiplier per Hz. For ground acceleration in g²/Hz use the case scale factor g."
        : "Value = load amplitude multiplier at frequency f.");
      g1.appendChild(note);
      if (refs.length) g1.appendChild(el("p", "muted cd-note", `Used by ${esc(refs.join(", "))}.`));
      right.appendChild(g1);

      const g2 = group("Function Points");
      const tools = el("div", "cd-inline");
      tools.append(
        checkbox("Edit as text", textMode, v => { textMode = v; draw(); }, "ffTextMode"),
        checkbox("Log scale preview", logScale, v => { logScale = v; draw(); }, "ffLog"));
      g2.appendChild(tools);
      if (textMode) {
        const ta = document.createElement("textarea");
        ta.className = "cd-text"; ta.id = "ffText"; ta.rows = 8; ta.spellcheck = false;
        ta.value = fn.points.map(p => `${U.inputValue("frequency", p[0])}, ${p[1]}`).join("\n");
        ta.addEventListener("change", () => {
          const pts = ta.value.split(/\n+/).map(s => s.trim()).filter(Boolean)
            .map(s => s.split(/[\s,;\t]+/).map(Number));
          if (pts.some(p => p.length < 2 || !isFinite(p[0]) || !isFinite(p[1]))) {
            showError(err, "Each line needs two numbers: frequency (Hz), value."); return;
          }
          fn.points = pts.map(p => [U.fromDisplay("frequency", p[0]), p[1]]);
          showError(err, ""); draw();
        });
        g2.appendChild(ta);
      } else {
        const tbl = el("div", "cd-pts"); tbl.id = "ffPoints";
        tbl.appendChild(el("div", "cd-pt head", `<span>Frequency ${esc(U.label("frequency"))}</span><span>Value ${esc(vu)}</span><span></span>`));
        fn.points.forEach((p, i) => {
          const r = el("div", "cd-pt");
          r.append(
            numInput("frequency", p[0], { min: 0, onSet: v => { p[0] = v; drawPreview(); } }),
            numInput("none", p[1], { min: fn.kind === "psd" ? 0 : undefined, onSet: v => { p[1] = v; drawPreview(); } }));
          const x = el("button", "chip-x"); x.textContent = "✕"; x.title = "Remove point";
          x.disabled = fn.points.length <= 2;
          x.addEventListener("click", () => { fn.points.splice(i, 1); draw(); });
          r.appendChild(x);
          tbl.appendChild(r);
        });
        g2.appendChild(tbl);
        g2.appendChild(btn("+ Point", "btn-small cd-add", () => {
          const last = fn.points[fn.points.length - 1] || [0, 1];
          fn.points.push([+(last[0] + 5).toPrecision(12), last[1]]);
          draw();
        }, "Append a point", "ffAddPt"));
      }
      right.appendChild(g2);
      const prev = el("div", "cd-preview"); prev.id = "ffPreview";
      right.appendChild(prev);
      const drawPreview = () => {
        const xs = fn.points.map(p => p[0]), ys = fn.points.map(p => p[1]);
        prev.innerHTML = `<div class="chart-title">Preview <span class="unit">${esc(vu)} vs f ${esc(U.label("frequency"))}</span></div>` +
          lineChart([{ xs, ys, dots: true }], { w: 600, h: 200, logX: logScale, logY: logScale, xLabel: "f, Hz", aria: "Frequency function preview" });
      };
      drawPreview();
    }
    grid.appendChild(right);
    body.appendChild(grid);
    body.appendChild(err);
  };

  const commit = () => {
    for (const fn of Object.values(draft)) {
      const msg = validateFrequencyFunction(fn);
      if (msg) { showError(err, msg); sel = fn.name; return false; }
    }
    // kind consistency with every case load that uses the function
    for (const [orig, nu] of Object.entries(renames)) {
      const fn = draft[nu];
      for (const [dk, kind] of [["steady_state_cases", "steady_state"], ["psd_cases", "psd"]])
        for (const c of Object.values(m[dk] || {}))
          if ((c.loads || []).some(l => l.function === orig) && fn.kind !== kind) {
            showError(err, `${nu} is used by ${kind === "psd" ? "PSD" : "steady-state"} case ${c.name} — kind must stay ${FN_KIND_LABEL[kind]}.`);
            return false;
          }
    }
    // renames follow into the case loads
    for (const [orig, nu] of Object.entries(renames)) {
      if (orig === nu) continue;
      for (const dk of ["steady_state_cases", "psd_cases"])
        for (const c of Object.values(m[dk] || {}))
          for (const l of c.loads || []) if (l.function === orig) l.function = nu;
    }
    const out = {};
    for (const [n, fn] of Object.entries(draft))
      out[n] = { name: n, kind: fn.kind, points: fn.points.map(p => [+p[0], +p[1]]) };
    if (Object.keys(out).length) m.frequency_functions = out;
    else delete m.frequency_functions;
    ctx.markDirty();
    ctx.onChange && ctx.onChange("frequency_functions");
    return true;
  };
  const fb = footBar("Functions are shared by every steady-state / PSD case.", [
    btn("Cancel", "", () => dlg.close(), "", "ffCancel"),
    btn("OK", "btn-run", () => { if (commit()) dlg.close(); }, "", "ffOk"),
  ]);
  const dlg = dialog("freqFnModal", {
    title: "Frequency Functions", glyph: "fn", wide: true, body, foot: fb.wrap,
    onClose: opts.onClose, onUnits: draw,
  });
  draw();
  return dlg;
}

/* ================================================================
   Steady-state / PSD load cases
   ================================================================ */
export const FREQ_CASE_DEFAULTS = {
  loads: [], freq_start_hz: 0, freq_end_hz: 10, n_freq: 101, frequencies: [],
  modal_refine: true, method: "modal", num_modes: 0, damping: 0.05,
  damping_type: "modal", output_points: [],
};
const FREQ_DICT = { steady_state: "steady_state_cases", psd: "psd_cases" };
const FREQ_TITLE = { steady_state: "Steady State", psd: "Power Spectral Density" };

function normFreqCase(c, name) {
  const out = { name: c.name || name };
  for (const [k, d] of Object.entries(FREQ_CASE_DEFAULTS)) out[k] = c[k] === undefined ? clone(d) : c[k];
  out.loads = (out.loads || []).map(l => ({
    pattern: String(l.pattern || ""), direction: String(l.direction || ""),
    scale: isNum(l.scale) ? l.scale : 1.0, function: String(l.function || ""),
    phase_deg: isNum(l.phase_deg) ? l.phase_deg : 0.0,
  }));
  return out;
}
function newFreqCase(m, kind) {
  const name = uniqueName(takenCaseNames(m), kind === "psd" ? "PSD" : "SS");
  const c = normFreqCase({ name }, name);
  if (kind === "psd") {
    const fn = Object.values(m.frequency_functions || {}).find(f => f.kind === "psd");
    c.loads = [{ pattern: "accel", direction: "UX", scale: G, function: fn ? fn.name : "", phase_deg: 0 }];
  } else {
    const pats = Object.values(m.patterns || {});
    const lat = pats.find(p => p.kind === "quake" || p.kind === "wind") || pats[0];
    c.loads = lat ? [{ pattern: lat.name, direction: "", scale: 1, function: "", phase_deg: 0 }]
      : [{ pattern: "accel", direction: "UX", scale: 1, function: "", phase_deg: 0 }];
  }
  return c;
}
export function validateFreqCase(m, kind, c, oldName) {
  const label = kind === "psd" ? "PSD" : "Steady-state";
  if (!c.name || !String(c.name).trim()) return "Case name is required.";
  if (takenCaseNames(m, oldName).has(c.name)) return `A case or combination named ${c.name} already exists.`;
  if (!c.loads.length) return `${label} case needs at least one load.`;
  const fns = m.frequency_functions || {};
  for (const [i, l] of c.loads.entries()) {
    const at = `Load ${i + 1}`;
    if (l.pattern === "accel") {
      if (!["UX", "UY", "UZ"].includes(l.direction)) return `${at}: pick an acceleration direction.`;
    } else if (!(m.patterns || {})[l.pattern]) return `${at}: unknown load pattern ${l.pattern || "(none)"}.`;
    if (!isNum(l.scale) || !isNum(l.phase_deg)) return `${at}: scale and phase must be numbers.`;
    if (l.function) {
      const fn = fns[l.function];
      if (!fn) return `${at}: unknown frequency function ${l.function}.`;
      if (fn.kind !== kind) return `${at}: function ${l.function} is ${FN_KIND_LABEL[fn.kind]}, not ${FREQ_TITLE[kind]}.`;
    } else if (kind === "psd") return `${at}: every PSD load must name a PSD function.`;
  }
  if (!(isNum(c.freq_start_hz) && isNum(c.freq_end_hz) && c.freq_start_hz >= 0 && c.freq_start_hz <= c.freq_end_hz))
    return "Need 0 ≤ start frequency ≤ end frequency.";
  if (!Number.isInteger(c.n_freq) || c.n_freq < 0 || c.n_freq > 20000) return "Number of frequencies must be a whole number 0 – 20000.";
  if (!c.frequencies.every(f => isNum(f) && f >= 0)) return "Additional frequencies must be numbers ≥ 0.";
  if (c.n_freq === 0 && !c.frequencies.length) return "Empty frequency grid — set the number of frequencies or add explicit frequencies.";
  if (!(isNum(c.damping) && c.damping > 0 && c.damping < 1)) return "Damping ratio must be in (0, 1).";
  if (!Number.isInteger(c.num_modes) || c.num_modes < 0) return "Number of modes must be a whole number ≥ 0.";
  if (!c.output_points.every(p => Array.isArray(p) && p.length === 3 && p.every(isNum))) return "Output joints need x, y, z.";
  return "";
}

export function openFreqCase(ctx, kind, name = null) {
  const m = ctx.store.model;
  if (!m) return null;
  kind = kind === "psd" ? "psd" : "steady_state";
  const dk = FREQ_DICT[kind];
  const isNew = !name || !(m[dk] || {})[name];
  const draft = isNew ? newFreqCase(m, kind) : normFreqCase(clone(m[dk][name]), name);
  const oldName = isNew ? null : name;
  const joints = modelJoints(m);
  const body = el("div", "cd-freq");
  const err = errorLine("fcError");

  const fnOptions = () => {
    const fns = Object.values(m.frequency_functions || {}).filter(f => f.kind === kind).map(f => [f.name, f.name]);
    return kind === "psd" ? [["", "— pick a PSD function —"], ...fns] : [["", "(constant 1)"], ...fns];
  };
  const draw = () => {
    body.textContent = "";
    /* ---- general */
    const g0 = group("Load Case");
    const nameIn = document.createElement("input");
    nameIn.type = "text"; nameIn.id = "fcName"; nameIn.value = draft.name; nameIn.spellcheck = false;
    nameIn.addEventListener("change", () => { draft.name = nameIn.value.trim(); });
    g0.appendChild(row("Load case name", nameIn));
    g0.appendChild(row("Load case type", el("span", "cd-static", esc(FREQ_TITLE[kind]))));
    g0.appendChild(el("p", "muted cd-note", kind === "psd"
      ? "All loads of one PSD case are <b>fully correlated</b> (one underlying process). For uncorrelated inputs use one PSD case each and SRSS the RMS values."
      : "Harmonic loading at every frequency of the grid; results are amplitude and phase (a response lagging the load has a negative phase)."));
    body.appendChild(g0);

    /* ---- loads */
    const g1 = group("Loads Applied");
    const tbl = el("div", "cd-loads"); tbl.id = "fcLoads";
    tbl.appendChild(el("div", "cd-load head",
      `<span>Load type</span><span>Load name</span><span>Function</span><span>Scale factor</span><span>Phase °</span><span></span>`));
    const pats = patternNames(m);
    draft.loads.forEach((l, i) => {
      const r = el("div", "cd-load");
      r.dataset.i = String(i);
      const accel = l.pattern === "accel";
      const typeSel = select([["pattern", "Load Pattern"], ["accel", "Acceleration"]], accel ? "accel" : "pattern", v => {
        if (v === "accel") { l.pattern = "accel"; l.direction = l.direction || "UX"; }
        else { l.pattern = pats[0] || ""; l.direction = ""; }
        draw();
      });
      typeSel.className = "fc-type";
      const nameSel = accel
        ? select([["UX", "U1 (UX)"], ["UY", "U2 (UY)"], ["UZ", "U3 (UZ)"]], l.direction, v => { l.direction = v; })
        : select(pats.map(p => [p, p]), l.pattern, v => { l.pattern = v; });
      nameSel.className = "fc-lname";
      const fnSel = select(fnOptions(), l.function, v => { l.function = v; });
      fnSel.className = "fc-fn";
      // pattern loads: dimensionless multiplier; ground acceleration: accel units
      const sc = numInput(accel ? "accel" : "none", l.scale, { cls: "fc-scale", onSet: v => { l.scale = v; } });
      const scWrap = el("span", "cd-with-unit");
      scWrap.appendChild(sc);
      if (accel) scWrap.appendChild(el("span", "cd-unit", esc(U.label("accel"))));
      const ph = numInput("none", l.phase_deg, { cls: "fc-phase", onSet: v => { l.phase_deg = v; } });
      const x = el("button", "chip-x"); x.textContent = "✕"; x.title = "Remove load";
      x.addEventListener("click", () => { draft.loads.splice(i, 1); draw(); });
      r.append(typeSel, nameSel, fnSel, scWrap, ph, x);
      tbl.appendChild(r);
    });
    g1.appendChild(tbl);
    const la = el("div", "cd-inline");
    la.append(
      btn("+ Add load", "btn-small", () => {
        const fn = fnOptions().find(o => o[0]);
        draft.loads.push(kind === "psd"
          ? { pattern: "accel", direction: "UX", scale: G, function: fn ? fn[0] : "", phase_deg: 0 }
          : { pattern: pats[0] || "accel", direction: pats[0] ? "" : "UX", scale: 1, function: "", phase_deg: 0 });
        draw();
      }, "Add a load to this case", "fcAddLoad"),
      btn("Frequency Functions…", "btn-small", () => openFreqFunctions(ctx, {
        select: (draft.loads.find(l => l.function) || {}).function, onClose: draw,
      }), "Define / edit frequency functions", "fcFunctions"));
    g1.appendChild(la);
    if (kind === "psd" && !fnOptions().some(o => o[0]))
      g1.appendChild(el("p", "muted cd-note cd-warn", "No PSD functions yet — define one with <b>Frequency Functions…</b>."));
    g1.appendChild(el("p", "muted cd-note",
      "Pattern load = the pattern's static distribution × scale × function(f). Acceleration = uniform ground acceleration " +
      `scale × function(f) in ${esc(U.label("accel"))} (responses relative to the ground; for g-based functions use scale = g = ${esc(U.inputValue("accel", G))} ${esc(U.label("accel"))}).`));
    body.appendChild(g1);

    /* ---- frequency steps */
    const g2 = group("Frequency Steps");
    const two = el("div", "cd-cols");
    two.append(
      row("First frequency", numInput("frequency", draft.freq_start_hz, { id: "fcStart", min: 0, onSet: v => { draft.freq_start_hz = v; } }), "frequency"),
      row("Last frequency", numInput("frequency", draft.freq_end_hz, { id: "fcEnd", min: 0, onSet: v => { draft.freq_end_hz = v; } }), "frequency"),
      row("Number of increments", numInput("none", draft.n_freq, { id: "fcNfreq", int: true, min: 0, max: 20000, onSet: v => { draft.n_freq = v; } }), null,
        "linspace points (0 = none, 1 = first only)"));
    g2.appendChild(two);
    const fq = document.createElement("input");
    fq.type = "text"; fq.id = "fcFreqs"; fq.spellcheck = false; fq.placeholder = "e.g. 1.25, 2.5, 4";
    fq.value = draft.frequencies.map(f => U.inputValue("frequency", f)).join(", ");
    fq.addEventListener("change", () => {
      const parts = fq.value.split(/[\s,;]+/).filter(Boolean);
      const vals = parts.map(s => U.parse("frequency", s));
      if (vals.some(v => !isFinite(v) || v < 0)) { fq.classList.add("is-bad"); showError(err, "Additional frequencies must be numbers ≥ 0."); return; }
      fq.classList.remove("is-bad"); showError(err, "");
      draft.frequencies = vals;
    });
    g2.appendChild(row("Additional frequencies", fq, "frequency"));
    g2.appendChild(checkbox("Add modal frequencies and refinement points (f·(1 ± kζ)) inside the range", draft.modal_refine,
      v => { draft.modal_refine = v; }, "fcRefine"));
    body.appendChild(g2);

    /* ---- solution */
    const g3 = group("Solution");
    const meth = el("div", "cd-inline");
    meth.append(
      radio("fcMethod", "modal", draft.method === "modal", "Modal superposition", v => { draft.method = v; draw(); }, "fcMethodModal"),
      radio("fcMethod", "direct", draft.method === "direct", "Direct (all modes + static residual)", v => { draft.method = v; draw(); }, "fcMethodDirect"));
    g3.appendChild(row("Solution method", meth));
    const nm = numInput("none", draft.num_modes, { id: "fcNumModes", int: true, min: 0, disabled: draft.method !== "modal", onSet: v => { draft.num_modes = v; } });
    g3.appendChild(row("Number of modes", nm, null, `0 = model modes (${esc(m.num_modes ?? "default")})`));
    const two3 = el("div", "cd-cols");
    two3.append(
      row("Damping ratio", numInput("none", draft.damping, { id: "fcDamping", gt: 0, lt: 1, onSet: v => { draft.damping = v; } })),
      row("Damping type", select([["modal", "Modal (viscous)"], ["hysteretic", "Hysteretic (complex stiffness)"]], draft.damping_type,
        v => { draft.damping_type = v; }, "fcDampType")));
    g3.appendChild(two3);
    body.appendChild(g3);

    /* ---- output joints */
    const g4 = group("Output Joints");
    g4.appendChild(el("p", "muted cd-note", kind === "psd"
      ? "Response PSD curves are kept for these joints (RMS values are reported for every joint). Empty = no joint curves."
      : "Amplitude / phase histories for these joints. Empty = every joint."));
    const pt = el("div", "cd-pts cd-pts3"); pt.id = "fcPoints";
    if (draft.output_points.length)
      pt.appendChild(el("div", "cd-pt head", `<span>X ${esc(U.label("length"))}</span><span>Y ${esc(U.label("length"))}</span><span>Z ${esc(U.label("length"))}</span><span>Story</span><span></span>`));
    draft.output_points.forEach((p, i) => {
      const r = el("div", "cd-pt");
      for (let k = 0; k < 3; k++) r.appendChild(numInput("length", p[k], { onSet: v => { p[k] = v; } }));
      r.appendChild(el("span", "muted cd-story", esc(storyAt(m, p[2]) || "—")));
      const x = el("button", "chip-x"); x.textContent = "✕"; x.title = "Remove joint";
      x.addEventListener("click", () => { draft.output_points.splice(i, 1); draw(); });
      r.appendChild(x);
      pt.appendChild(r);
    });
    g4.appendChild(pt);
    const pick = select([["", "+ Add joint…"], ...joints.map((j, i) => [String(i), `${storyAt(m, j[2]) || "z"} · ${ptLabel(j)}`])], "", v => {
      if (v === "") return;
      draft.output_points.push(joints[+v].slice());
      draw();
    }, "fcAddPoint");
    g4.appendChild(pick);
    body.appendChild(g4);
    body.appendChild(err);
  };

  const commit = () => {
    draft.name = String(draft.name || "").trim();
    draft.output_points = draft.output_points.map(p => snapToJoint(joints, p));
    const msg = validateFreqCase(m, kind, draft, oldName);
    if (msg) { showError(err, msg); return false; }
    const out = { name: draft.name };
    for (const k of Object.keys(FREQ_CASE_DEFAULTS)) out[k] = clone(draft[k]);
    out.loads = draft.loads.map(l => ({
      pattern: l.pattern, direction: l.pattern === "accel" ? l.direction : "",
      scale: +l.scale, function: l.function || "", phase_deg: +l.phase_deg,
    }));
    m[dk] = m[dk] || {};
    if (oldName && oldName !== out.name) {
      // keep the dict order: rebuild with the renamed key in place
      const rebuilt = {};
      for (const [k, v] of Object.entries(m[dk])) rebuilt[k === oldName ? out.name : k] = k === oldName ? out : v;
      m[dk] = rebuilt;
      if (Array.isArray(m.cases_not_run)) m.cases_not_run = m.cases_not_run.map(n => n === oldName ? out.name : n);
    } else m[dk][out.name] = out;
    ctx.markDirty();
    ctx.onChange && ctx.onChange(dk);
    return true;
  };
  const fb = footBar(isNew ? "New case — OK adds it to the model." : `Editing ${name}`, [
    btn("Cancel", "", () => dlg.close(), "", "fcCancel"),
    btn("OK", "btn-run", () => { if (commit()) dlg.close(); }, "", "fcOk"),
  ]);
  const dlg = dialog("freqCaseModal", {
    title: `Load Case Data — ${FREQ_TITLE[kind]}`, glyph: kind === "psd" ? "psd" : "freq", wide: true,
    body, foot: fb.wrap, onUnits: draw,
  });
  draw();
  return dlg;
}

/** Delete a steady-state / PSD case (and its Do-not-Run flag). */
export function deleteFreqCase(m, kind, name) {
  const dk = FREQ_DICT[kind];
  if (!m[dk] || !m[dk][name]) return false;
  delete m[dk][name];
  if (!Object.keys(m[dk]).length) delete m[dk];
  if (Array.isArray(m.cases_not_run)) m.cases_not_run = m.cases_not_run.filter(n => n !== name);
  return true;
}

/* ================================================================
   Time-history "Other Parameters" (direct-integration options)
   ================================================================ */
const SOLVER_DEFAULTS = { max_iterations: 25, tolerance: 1e-8, test: "NormDispIncr", max_halvings: 0 };
const INTEG_LABEL = {
  "": "Default — Newmark (γ = 0.5, β = 0.25)",
  newmark: "Newmark",
  hht: "Hilber-Hughes-Taylor (HHT-α)",
  wilson: "Wilson θ",
  central_difference: "Central Difference (explicit)",
};
export function rayleighFromPeriods(T1, xi1, T2, xi2) {
  const w1 = 2 * Math.PI / T1, w2 = 2 * Math.PI / T2;
  const den = w2 * w2 - w1 * w1;
  return [2 * w1 * w2 * (xi1 * w2 - xi2 * w1) / den, 2 * (xi2 * w2 - xi1 * w1) / den];
}

export function openThOptions(ctx, name) {
  const m = ctx.store.model;
  const tc = m && (m.th_cases || {})[name];
  if (!tc) return null;
  const integ = tc.integration ? clone(tc.integration) : null;
  const d = {
    method: integ ? integ.method || "newmark" : "",
    gamma: integ && isNum(integ.gamma) ? integ.gamma : null,
    beta: integ && isNum(integ.beta) ? integ.beta : null,
    alpha: integ && isNum(integ.alpha) ? integ.alpha : -0.05,
    theta: integ && isNum(integ.theta) ? integ.theta : 1.4,
    dtype: tc.di_damping ? tc.di_damping.type || "" : "",
    T1: 1.0, xi1: tc.damping ?? 0.05, T2: 0.1, xi2: tc.damping ?? 0.05,
    a0: 0, a1: 0, basis: "",
    solver: { ...SOLVER_DEFAULTS, ...(tc.solver || {}) },
    energy: !!tc.energy,
  };
  if (tc.di_damping) {
    const dd = tc.di_damping;
    for (const k of ["T1", "xi1", "T2", "xi2"]) if (isNum(dd[k])) d[k] = dd[k];
    if (isNum(dd.mass_coeff)) d.a0 = dd.mass_coeff;
    if (isNum(dd.stiffness_coeff)) d.a1 = dd.stiffness_coeff;
    d.basis = dd.stiffness || "";
  }
  const modalDamping = tc.damping_model === "modal";
  const body = el("div", "cd-tho");
  const err = errorLine("thoError");

  const draw = () => {
    body.textContent = "";
    body.appendChild(el("p", "muted dlg-intro",
      `Direct-integration options for time-history case <b>${esc(name)}</b> (ETABS “Other Parameters”). ` +
      "Leaving everything at Default keeps the legacy solution path and the saved model unchanged."));
    /* ---- integration */
    const g1 = group("Time Integration");
    g1.appendChild(row("Method", select(Object.entries(INTEG_LABEL), d.method, v => {
      d.method = v;
      if (v === "newmark") { if (d.gamma == null) d.gamma = 0.5; if (d.beta == null) d.beta = 0.25; }
      if (v === "hht") { d.gamma = null; d.beta = null; }
      draw();
    }, "thoMethod")));
    if (d.method === "newmark") {
      const two = el("div", "cd-cols");
      two.append(
        row("Gamma γ", numInput("none", d.gamma, { id: "thoGamma", min: 0.5, max: 1, onSet: v => { d.gamma = v; } })),
        row("Beta β", numInput("none", d.beta, { id: "thoBeta", gt: 0, max: 0.5, onSet: v => { d.beta = v; } })));
      g1.appendChild(two);
      const pre = el("div", "cd-inline");
      pre.append(
        btn("Average acceleration", "btn-small", () => { d.gamma = 0.5; d.beta = 0.25; draw(); }, "γ = 1/2, β = 1/4 (unconditionally stable)"),
        btn("Linear acceleration", "btn-small", () => { d.gamma = 0.5; d.beta = 1 / 6; draw(); }, "γ = 1/2, β = 1/6 (conditionally stable)"));
      g1.appendChild(pre);
      if (isNum(d.gamma) && isNum(d.beta) && 2 * d.beta < d.gamma)
        g1.appendChild(el("p", "muted cd-note cd-warn", "Conditionally stable (2β &lt; γ): the solver checks Δt against the shortest period before running."));
    } else if (d.method === "hht") {
      g1.appendChild(row("Alpha α", numInput("none", d.alpha, { id: "thoAlpha", min: -1 / 3, max: 0, onSet: v => { d.alpha = v; draw(); } }), null, "−1/3 ≤ α ≤ 0"));
      const two = el("div", "cd-cols");
      two.append(
        row("Gamma γ", numInput("none", d.gamma, { id: "thoGamma", allowEmpty: true, placeholder: String(+(0.5 - d.alpha).toPrecision(6)), onSet: v => { d.gamma = v; } }), null, "blank = ½ − α"),
        row("Beta β", numInput("none", d.beta, { id: "thoBeta", allowEmpty: true, placeholder: String(+((1 - d.alpha) ** 2 / 4).toPrecision(6)), onSet: v => { d.beta = v; } }), null, "blank = (1 − α)²/4"));
      g1.appendChild(two);
    } else if (d.method === "wilson") {
      g1.appendChild(row("Theta θ", numInput("none", d.theta, { id: "thoTheta", min: 1, max: 2, onSet: v => { d.theta = v; } }), null, "≥ 1.37 unconditionally stable"));
    } else if (d.method === "central_difference") {
      g1.appendChild(el("p", "muted cd-note cd-warn",
        "Explicit scheme: needs mass on every free degree of freedom (frames with massless rotations are rejected) and Δt ≤ T<sub>min</sub>/π."));
    }
    body.appendChild(g1);

    /* ---- damping */
    const g2 = group("Damping (Direct Integration)");
    if (modalDamping) {
      g2.appendChild(el("p", "muted cd-note", "This case uses <b>modal damping</b> (Time-history card). Proportional Rayleigh damping cannot be combined with it."));
    } else {
      g2.appendChild(row("Damping", select([
        ["", `Default — case damping ratio (${U.inputValue("none", tc.damping ?? 0.05)})`],
        ["rayleigh_by_periods", "Mass and stiffness proportional — by periods"],
        ["rayleigh_coefficients", "Mass and stiffness proportional — by coefficients"],
      ], d.dtype, v => { d.dtype = v; draw(); }, "thoDampType")));
      let a0 = null, a1 = null;
      if (d.dtype === "rayleigh_by_periods") {
        const grid = el("div", "cd-cols");
        grid.append(
          row("Period T₁", numInput("period", d.T1, { id: "thoT1", gt: 0, onSet: v => { d.T1 = v; draw(); } }), "period"),
          row("Damping ξ₁", numInput("none", d.xi1, { id: "thoXi1", min: 0, lt: 1, onSet: v => { d.xi1 = v; draw(); } })),
          row("Period T₂", numInput("period", d.T2, { id: "thoT2", gt: 0, onSet: v => { d.T2 = v; draw(); } }), "period"),
          row("Damping ξ₂", numInput("none", d.xi2, { id: "thoXi2", min: 0, lt: 1, onSet: v => { d.xi2 = v; draw(); } })));
        g2.appendChild(grid);
        if (Math.abs(d.T1 - d.T2) > 1e-9 * Math.max(d.T1, d.T2)) [a0, a1] = rayleighFromPeriods(d.T1, d.xi1, d.T2, d.xi2);
      } else if (d.dtype === "rayleigh_coefficients") {
        const grid = el("div", "cd-cols");
        grid.append(
          row("Mass coefficient a₀", numInput("none", d.a0, { id: "thoA0", min: 0, onSet: v => { d.a0 = v; draw(); } }), null, "1/s"),
          row("Stiffness coefficient a₁", numInput("none", d.a1, { id: "thoA1", min: 0, onSet: v => { d.a1 = v; draw(); } }), null, "s"));
        g2.appendChild(grid);
        a0 = d.a0; a1 = d.a1;
      }
      if (d.dtype) {
        g2.appendChild(row("Stiffness basis", select([
          ["", tc.nonlinear ? "Default (committed)" : "Default (current)"],
          ["initial", "Initial stiffness"], ["current", "Current (tangent) stiffness"], ["committed", "Last committed stiffness"],
        ], d.basis, v => { d.basis = v; }, "thoBasis")));
        if (a0 != null && a1 != null) {
          const neg = a0 < -1e-15 || a1 < -1e-15;
          const info = el("p", "muted cd-note" + (neg ? " cd-warn" : ""));
          info.id = "thoCoeffs";
          info.innerHTML = `a₀ = ${(+a0).toPrecision(5)} 1/s · a₁ = ${(+a1).toPrecision(5)} s` +
            (neg ? " — negative coefficient: choose ratios closer together." : "");
          g2.appendChild(info);
          const Ts = [], xs = [];
          for (let k = 0; k <= 80; k++) {
            const T = 0.01 * Math.pow(10, 3 * k / 80);        // 0.01 … 10 s (log)
            const w = 2 * Math.PI / T;
            Ts.push(T); xs.push(a0 / (2 * w) + a1 * w / 2);
          }
          const prev = el("div", "cd-preview");
          prev.innerHTML = `<div class="chart-title">Damping ratio vs period <span class="unit">ξ vs T s</span></div>` +
            lineChart([{ xs: Ts, ys: xs }], { logX: true, w: 820, h: 170, xLabel: "T, s",
              vlines: d.dtype === "rayleigh_by_periods" ? [d.T1, d.T2] : [], aria: "Rayleigh damping ratio vs period" });
          g2.appendChild(prev);
        }
      }
    }
    body.appendChild(g2);

    /* ---- solver */
    const g3 = group("Nonlinear Solution Control");
    const grid = el("div", "cd-cols");
    const sv = d.solver;
    grid.append(
      row("Max iterations / step", numInput("none", sv.max_iterations, { id: "thoMaxIter", int: true, min: 1, onSet: v => { sv.max_iterations = v; } })),
      row("Convergence tolerance", numInput("none", sv.tolerance, { id: "thoTol", gt: 0, onSet: v => { sv.tolerance = v; } })),
      row("Convergence test", select([["NormDispIncr", "Displacement increment norm"], ["NormUnbalance", "Unbalanced force norm"], ["EnergyIncr", "Energy increment"]],
        sv.test, v => { sv.test = v; }, "thoTest")),
      row("Max step halvings", numInput("none", sv.max_halvings, { id: "thoHalvings", int: true, min: 0, max: 12, onSet: v => { sv.max_halvings = v; } }), null, "0 – 12"));
    g3.appendChild(grid);
    body.appendChild(g3);

    /* ---- energy */
    const g4 = group("Output");
    g4.appendChild(checkbox("Record energy time series (input, kinetic, strain, damping, hysteretic, error)", d.energy,
      v => { d.energy = v; }, "thoEnergy"));
    body.appendChild(g4);
    body.appendChild(err);
  };

  const commit = () => {
    /* integration */
    let integration = null;
    if (d.method === "newmark") {
      if (!(d.gamma >= 0.5 && d.gamma <= 1)) return fail("Newmark γ must be in [0.5, 1].");
      if (!(d.beta > 0 && d.beta <= 0.5)) return fail("Newmark β must be in (0, 0.5].");
      integration = { method: "newmark", gamma: d.gamma, beta: d.beta };
    } else if (d.method === "hht") {
      if (!(d.alpha >= -1 / 3 - 1e-12 && d.alpha <= 0)) return fail("HHT α must be in [−1/3, 0].");
      integration = { method: "hht", alpha: d.alpha };
      if (isNum(d.gamma)) integration.gamma = d.gamma;
      if (isNum(d.beta)) integration.beta = d.beta;
    } else if (d.method === "wilson") {
      if (!(d.theta >= 1 && d.theta <= 2)) return fail("Wilson θ must be in [1, 2].");
      integration = { method: "wilson", theta: d.theta };
    } else if (d.method === "central_difference") integration = { method: "central_difference" };
    /* damping */
    let di = null;
    if (!modalDamping && d.dtype === "rayleigh_by_periods") {
      if (!(d.T1 > 0 && d.T2 > 0)) return fail("Periods T₁ and T₂ must be > 0.");
      if (Math.abs(d.T1 - d.T2) <= 1e-9 * Math.max(d.T1, d.T2)) return fail("Periods T₁ and T₂ must differ.");
      if (!(d.xi1 >= 0 && d.xi1 < 1 && d.xi2 >= 0 && d.xi2 < 1)) return fail("Damping ratios must be in [0, 1).");
      const [a0, a1] = rayleighFromPeriods(d.T1, d.xi1, d.T2, d.xi2);
      if (a0 < -1e-15 || a1 < -1e-15) return fail("These ratios give a negative Rayleigh coefficient — choose ratios closer together.");
      di = { type: "rayleigh_by_periods", T1: d.T1, xi1: d.xi1, T2: d.T2, xi2: d.xi2 };
    } else if (!modalDamping && d.dtype === "rayleigh_coefficients") {
      if (!(d.a0 >= 0 && d.a1 >= 0)) return fail("Rayleigh coefficients must be ≥ 0.");
      di = { type: "rayleigh_coefficients", mass_coeff: d.a0, stiffness_coeff: d.a1 };
    }
    if (di && d.basis) di.stiffness = d.basis;
    /* solver — only the keys that differ from the defaults */
    const solver = {};
    for (const [k, v] of Object.entries(SOLVER_DEFAULTS)) if (d.solver[k] !== v) solver[k] = d.solver[k];
    const set = (k, v) => { if (v == null) delete tc[k]; else tc[k] = v; };
    set("integration", integration);
    if (!modalDamping) set("di_damping", di);
    set("solver", Object.keys(solver).length ? solver : null);
    set("energy", d.energy ? true : null);
    ctx.markDirty();
    ctx.onChange && ctx.onChange("th_cases");
    return true;
  };
  const fail = msg => { showError(err, msg); return false; };
  const fb = footBar("Applies to direct integration (FNA ignores these options).", [
    btn("Reset to Defaults", "", () => {
      d.method = ""; d.dtype = ""; d.basis = ""; d.energy = false;
      d.solver = { ...SOLVER_DEFAULTS }; showError(err, ""); draw();
    }, "Clear every option (legacy solution)", "thoReset"),
    btn("Cancel", "", () => dlg.close(), "", "thoCancel"),
    btn("OK", "btn-run", () => { if (commit()) dlg.close(); }, "", "thoOk"),
  ]);
  const dlg = dialog("thOptModal", {
    title: `Time History — Other Parameters · ${name}`, glyph: "th", wide: true, body, foot: fb.wrap, onUnits: draw,
  });
  draw();
  return dlg;
}

/* ================================================================
   Response spectrum — modal combination
   ================================================================ */
export const RS_COMBO_METHODS = ["CQC", "SRSS", "ABS", "GMC", "NRC10", "DSC"];
const RS_COMBO_LABEL = {
  CQC: "CQC — complete quadratic combination",
  SRSS: "SRSS — square root of sum of squares",
  ABS: "ABS — absolute sum",
  GMC: "GMC — general modal combination (Gupta)",
  NRC10: "NRC 10 percent (Reg. Guide 1.92)",
  DSC: "Double sum (Rosenblueth)",
};
const RS_DEFAULTS = { gmc_f1: 1.0, gmc_f2: 33.0, dsc_td: 20.0, rigid_response: false, include_missing_mass: false };

export function openRsOptions(ctx, name) {
  const m = ctx.store.model;
  const rc = m && (m.rs_cases || {})[name];
  if (!rc) return null;
  const d = { combo_method: RS_COMBO_METHODS.includes(rc.combo_method) ? rc.combo_method : "CQC" };
  for (const [k, v] of Object.entries(RS_DEFAULTS)) d[k] = rc[k] === undefined || rc[k] === null ? v : rc[k];
  const body = el("div", "cd-rso");
  const err = errorLine("rsoError");
  const draw = () => {
    body.textContent = "";
    body.appendChild(el("p", "muted dlg-intro", `Modal combination for response-spectrum case <b>${esc(name)}</b>. ` +
      "Results stay positive envelopes, so directional combinations (100/30, SRSS) work with every method."));
    const g1 = group("Modal Combination");
    g1.appendChild(row("Method", select(RS_COMBO_METHODS.map(k => [k, RS_COMBO_LABEL[k]]), d.combo_method,
      v => { d.combo_method = v; draw(); }, "rsoMethod")));
    if (d.combo_method === "GMC" || d.rigid_response) {
      const two = el("div", "cd-cols");
      two.append(
        row("Rigid frequency f₁", numInput("frequency", d.gmc_f1, { id: "rsoF1", gt: 0, onSet: v => { d.gmc_f1 = v; } }), "frequency"),
        row("Rigid frequency f₂", numInput("frequency", d.gmc_f2, { id: "rsoF2", gt: 0, onSet: v => { d.gmc_f2 = v; } }), "frequency"));
      g1.appendChild(two);
      g1.appendChild(el("p", "muted cd-note", "Rigid-response coefficient α = ln(f/f₁)/ln(f₂/f₁), clamped to [0, 1]."));
    }
    if (d.combo_method === "DSC")
      g1.appendChild(row("Strong-motion duration td", numInput("period", d.dsc_td, { id: "rsoTd", gt: 0, onSet: v => { d.dsc_td = v; } }), "period"));
    if (d.combo_method === "NRC10")
      g1.appendChild(el("p", "muted cd-note", "Modes are closely spaced when their frequencies differ by ≤ 10 %."));
    body.appendChild(g1);
    const g2 = group("Rigid Response");
    g2.appendChild(checkbox("Periodic + rigid split (applies to any method)", d.rigid_response,
      v => { d.rigid_response = v; draw(); }, "rsoRigid"));
    g2.appendChild(checkbox("Include missing mass (residual-mass static correction as a rigid mode)", d.include_missing_mass,
      v => { d.include_missing_mass = v; }, "rsoMissing"));
    body.appendChild(g2);
    body.appendChild(err);
  };
  const commit = () => {
    if (!(d.gmc_f1 > 0 && d.gmc_f2 > d.gmc_f1)) { showError(err, "Need 0 < f₁ < f₂."); return false; }
    if (!(d.dsc_td > 0)) { showError(err, "Duration must be > 0."); return false; }
    rc.combo_method = d.combo_method;
    for (const [k, v] of Object.entries(RS_DEFAULTS)) {
      if (d[k] === v) delete rc[k];
      else rc[k] = d[k];
    }
    ctx.markDirty();
    ctx.onChange && ctx.onChange("rs_cases");
    return true;
  };
  const fb = footBar("", [
    btn("Cancel", "", () => dlg.close(), "", "rsoCancel"),
    btn("OK", "btn-run", () => { if (commit()) dlg.close(); }, "", "rsoOk"),
  ]);
  const dlg = dialog("rsOptModal", {
    title: `Response Spectrum — Modal Combination · ${name}`, glyph: "rs", body, foot: fb.wrap, onUnits: draw,
  });
  draw();
  return dlg;
}

/* ================================================================
   Pushover — load application, distribution, control, initial state
   ================================================================ */
export const PO_DEFAULTS = {
  load_distribution: "roof_point", pattern: null, mode_number: null, k: null,
  control_story: null, control_point: null, control_dof: null, target_disp: null,
  control_mode: "displacement_control", target_load: 1.0, start_from: null,
};
const PO_DIST_LABEL = {
  roof_point: "Roof point load (default)",
  pattern: "Load pattern",
  mode: "Mode shape (m·φ)",
  uniform_accel: "Uniform acceleration (m)",
  triangular: "Triangular / ASCE 7 (m·hᵏ)",
};

export function openPushoverOptions(ctx, name) {
  const m = ctx.store.model;
  const pc = m && (m.pushover_cases || {})[name];
  if (!pc) return null;
  const d = {};
  for (const [k, v] of Object.entries(PO_DEFAULTS)) d[k] = pc[k] === undefined ? clone(v) : clone(pc[k]);
  let gravity = clone(pc.gravity || {});
  let ctrl = d.control_point ? "point" : d.control_story ? "story" : "roof";
  const joints = modelJoints(m);
  const H = Math.max(0, ...(m.stories || []).map(s => s.elevation || 0));
  const body = el("div", "cd-poo");
  const err = errorLine("pooError");
  const draw = () => {
    body.textContent = "";
    body.appendChild(el("p", "muted dlg-intro", `Load application for pushover case <b>${esc(name)}</b> (ETABS Nonlinear Static). ` +
      "Every default reproduces the unit roof push."));
    /* ---- distribution */
    const g1 = group("Lateral Load Distribution");
    g1.appendChild(row("Distribution", select(Object.entries(PO_DIST_LABEL), d.load_distribution, v => {
      d.load_distribution = v;
      if (v === "pattern" && !d.pattern) d.pattern = patternNames(m)[0] || null;
      draw();
    }, "pooDist")));
    if (d.load_distribution === "pattern")
      g1.appendChild(row("Load pattern", select(patternNames(m).map(p => [p, p]), d.pattern || "", v => { d.pattern = v; }, "pooPattern")));
    if (d.load_distribution === "mode")
      g1.appendChild(row("Mode number", numInput("none", d.mode_number, { id: "pooMode", int: true, min: 1, allowEmpty: true,
        placeholder: "auto", onSet: v => { d.mode_number = v; } }), null, `blank = dominant mode in ${esc(pc.direction || "X")}`));
    if (d.load_distribution === "triangular")
      g1.appendChild(row("Exponent k", numInput("none", d.k, { id: "pooK", gt: 0, allowEmpty: true, placeholder: "auto",
        onSet: v => { d.k = v; } }), null, "blank = ASCE 7 §12.8.3 k(T)"));
    g1.appendChild(el("p", "muted cd-note", {
      roof_point: "Unit lateral force at the roof control node.",
      pattern: "The pattern is pushed at scale λ; base shear = λ × the pattern's net lateral force.",
      mode: "f = m·φ of the chosen mode, normalised so Σf = 1.",
      uniform_accel: "f = m at every massed node (uniform acceleration), Σf = 1.",
      triangular: "f = m·hᵏ (ASCE 7 Eq. 12.8-12 node by node), Σf = 1.",
    }[d.load_distribution] || ""));
    body.appendChild(g1);

    /* ---- monitored displacement */
    const g2 = group("Monitored Displacement");
    const rr = el("div", "cd-inline");
    rr.append(
      radio("pooCtrl", "roof", ctrl === "roof", "Roof (default)", v => { ctrl = v; draw(); }, "pooCtrlRoof"),
      radio("pooCtrl", "story", ctrl === "story", "Story", v => { ctrl = v; if (!d.control_story) d.control_story = storyNames(m).slice(-1)[0] || null; draw(); }, "pooCtrlStory"),
      radio("pooCtrl", "point", ctrl === "point", "Joint", v => { ctrl = v; if (!d.control_point) d.control_point = (joints[joints.length - 1] || [0, 0, H]).slice(); draw(); }, "pooCtrlPoint"));
    g2.appendChild(rr);
    if (ctrl === "story")
      g2.appendChild(row("Control story", select(storyNames(m).map(s => [s, s]), d.control_story || "", v => { d.control_story = v; }, "pooStory")));
    if (ctrl === "point") {
      const p = d.control_point;
      const three = el("div", "cd-cols cd-cols3");
      ["X", "Y", "Z"].forEach((ax, k) => three.appendChild(
        row(ax, numInput("length", p[k], { id: "pooP" + ax.toLowerCase(), onSet: v => { p[k] = v; } }), "length")));
      g2.appendChild(three);
      g2.appendChild(select([["", "Pick joint…"], ...joints.map((j, i) => [String(i), `${storyAt(m, j[2]) || "z"} · ${ptLabel(j)}`])], "", v => {
        if (v !== "") { d.control_point = joints[+v].slice(); draw(); }
      }, "pooPickJoint"));
    }
    g2.appendChild(row("Monitored DOF", select([["", `Push direction (U${pc.direction === "Y" ? "Y" : "X"})`], ["UX", "UX"], ["UY", "UY"]],
      d.control_dof || "", v => { d.control_dof = v || null; }, "pooDof")));
    const tgtDefault = (pc.target_drift ?? 0.02) * H;
    g2.appendChild(row("Target displacement", numInput("disp", d.target_disp, { id: "pooTarget", allowEmpty: true, nonzero: true,
      placeholder: `${U.inputValue("disp", tgtDefault)} (drift × H)`, onSet: v => { d.target_disp = v; } }), "disp",
      "blank = target drift × control height"));
    body.appendChild(g2);

    /* ---- control */
    const g3 = group("Load Application Control");
    const cr = el("div", "cd-inline");
    cr.append(
      radio("pooCtrlMode", "displacement_control", d.control_mode !== "load_control", "Displacement control", v => { d.control_mode = v; draw(); }, "pooDispCtrl"),
      radio("pooCtrlMode", "load_control", d.control_mode === "load_control", "Full load (load control)", v => { d.control_mode = v; draw(); }, "pooLoadCtrl"));
    g3.appendChild(cr);
    if (d.control_mode === "load_control")
      g3.appendChild(row("Target load factor λ", numInput("none", d.target_load, { id: "pooTargetLoad", nonzero: true, onSet: v => { d.target_load = v; } }),
        null, `λ/${esc(pc.steps ?? 100)} per step`));
    body.appendChild(g3);

    /* ---- initial conditions */
    const g4 = group("Initial Conditions");
    const gk = Object.keys(gravity);
    const hasGrav = gk.length > 0;
    g4.appendChild(row("Continue from static case", select([["", "— zero initial conditions / case gravity —"], ...staticCaseNames(m).map(c => [c, c]),
      ...Object.keys(m.nonlinear_static_cases || {}).map(c => [c, `${c} (nonlinear static — end state)`])],   // G3
      d.start_from || "", v => { d.start_from = v || null; draw(); }, "pooStart")));
    const startSel = g4.querySelector("#pooStart");
    if (hasGrav) {
      startSel.disabled = true;
      const note = el("div", "cd-inline");
      note.appendChild(el("span", "muted cd-note", `Gravity patterns set on the case (${esc(gk.map(k => `${k} × ${gravity[k]}`).join(", "))}) — start-from is unavailable.`));
      note.appendChild(btn("Clear gravity", "btn-small", () => { gravity = {}; draw(); },
        "Remove the case's gravity patterns so a static case can be the initial state", "pooClearGrav"));
      g4.appendChild(note);
    } else if (d.start_from)
      g4.appendChild(el("p", "muted cd-note", `The pattern factors of <b>${esc(d.start_from)}</b> are applied first and held (loadConst).`));
    body.appendChild(g4);
    body.appendChild(err);
  };
  const commit = () => {
    if (ctrl !== "story") d.control_story = null;
    if (ctrl !== "point") d.control_point = null;
    else d.control_point = snapToJoint(joints, d.control_point);
    if (d.load_distribution !== "pattern") d.pattern = null;
    if (d.load_distribution !== "mode") d.mode_number = null;
    if (d.load_distribution !== "triangular") d.k = null;
    if (d.control_mode !== "load_control") d.target_load = 1.0;
    if (d.load_distribution === "pattern" && !(m.patterns || {})[d.pattern]) return fail("Pick the load pattern to push.");
    if (ctrl === "story" && !storyNames(m).includes(d.control_story)) return fail("Pick a control story.");
    if (ctrl === "point" && !(d.control_point || []).every(isNum)) return fail("Control joint needs x, y, z.");
    if (d.start_from && Object.keys(gravity).length) return fail("Give gravity patterns OR a start-from case, not both.");
    for (const [k, v] of Object.entries(PO_DEFAULTS)) {
      if (JSON.stringify(d[k]) === JSON.stringify(v)) delete pc[k];
      else pc[k] = clone(d[k]);
    }
    pc.gravity = gravity;
    ctx.markDirty();
    ctx.onChange && ctx.onChange("pushover_cases");
    return true;
  };
  const fail = msg => { showError(err, msg); return false; };
  const fb = footBar("", [
    btn("Cancel", "", () => dlg.close(), "", "pooCancel"),
    btn("OK", "btn-run", () => { if (commit()) dlg.close(); }, "", "pooOk"),
  ]);
  const dlg = dialog("poOptModal", {
    title: `Nonlinear Static — Load Application · ${name}`, glyph: "push", wide: true, body, foot: fb.wrap, onUnits: draw,
  });
  draw();
  return dlg;
}

/* ================================================================
   Define → Load Cases (master list)
   ================================================================ */
const ADD_TYPES = [
  ["steady_state", "Steady State"], ["psd", "Power Spectral Density"],
  ["static", "Linear Static"], ["rs", "Response Spectrum"], ["th", "Time History"],
  ["pushover", "Nonlinear Static (Pushover)"],
  ["nonlinear_static", "Nonlinear Static"],   // G3 — js/nlsdlg.js
  ["hyperstatic", "Hyperstatic"],             // PT — js/tendons.js
];
const KIND_ANCHOR = { static: "ls-cases", rs: "ls-rs", th: "ls-th", pushover: "ls-pushover",
  buckling: "ls-buckling", staged: "ls-staged", steady_state: "ls-freq", psd: "ls-freq" };

export function openLoadCases(ctx) {
  const m = ctx.store.model;
  if (!m) return null;
  let sel = null;
  let addKind = "steady_state";
  const body = el("div", "cd-lc");
  const refresh = () => {
    body.textContent = "";
    const cases = ME.allAnalysisCases(m);
    if (sel && !cases.some(c => c.name === sel)) sel = null;
    const wrap = el("div", "table-scroll dlg-table-wrap");
    const table = el("table", "data-table dlg-case-table");
    table.id = "lcTable";
    table.innerHTML = `<thead><tr><th class="txt">Load Case Name</th><th class="txt">Load Case Type</th><th class="txt">Run</th></tr></thead>`;
    const tb = document.createElement("tbody");
    for (const c of cases) {
      const tr = document.createElement("tr");
      tr.dataset.case = c.name; tr.dataset.kind = c.kind;
      tr.className = "dlg-case-row" + (c.name === sel ? " is-sel" : "") + (ME.caseNotRun(m, c.name) ? " is-not-run" : "");
      tr.innerHTML = `<td class="txt"><b>${esc(c.name)}</b></td><td class="txt dim">${esc(c.type)}</td>` +
        `<td class="txt dim">${ME.caseNotRun(m, c.name) ? "Do not Run" : "Run"}</td>`;
      tr.addEventListener("click", () => { sel = c.name; refresh(); });
      tr.addEventListener("dblclick", () => { sel = c.name; modify(); });
      tb.appendChild(tr);
    }
    table.appendChild(tb);
    wrap.appendChild(table);
    const side = el("div", "dlg-case-actions");
    const addSel = select(ADD_TYPES, addKind, v => { addKind = v; }, "lcAddType");
    side.append(
      el("div", "dlg-group-title", "Click to:"),
      addSel,
      btn("Add New Case…", "btn-small", add, "Add a case of the selected type", "lcAdd"),
      btn("Modify/Show Case…", "btn-small", modify, "Edit the selected case", "lcModify"),
      btn("Delete Case", "btn-small", del, "Delete the selected case", "lcDelete"),
      el("div", "dlg-group-title", "Definitions"),
      btn("Frequency Functions…", "btn-small", () => openFreqFunctions(ctx, { onClose: refresh }), "", "lcFunctions"));
    const grid = el("div", "dlg-cases");
    grid.append(wrap, side);
    body.appendChild(grid);
    const cur = cases.find(c => c.name === sel);
    side.querySelector("#lcModify").disabled = !cur || (cur.kind === "modal" && !window.__sky?.openModalCase);   // G3: Modal → stiffness dialog
    side.querySelector("#lcDelete").disabled = !cur || cur.kind === "modal";
    fb.note.textContent = `${cases.length} cases`;
  };
  const kindOf = n => (ME.allAnalysisCases(m).find(c => c.name === n) || {}).kind;
  const gotoSection = kind => {
    dlg.close();
    const sky = window.__sky;
    if (!sky || !KIND_ANCHOR[kind]) return;
    sky.setMode("loads");
    requestAnimationFrame(() => {
      const s = document.getElementById(KIND_ANCHOR[kind]);
      if (s) s.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };
  function add() {
    if (addKind === "nonlinear_static") { window.__sky?.openNlsCase?.(null, { onChange: () => { refresh(); ctx.onChange && ctx.onChange("cases"); } }); return; }   // G3
    if (addKind === "hyperstatic") { window.__sky?.openHyperstaticCase?.(null, { onChange: () => { refresh(); ctx.onChange && ctx.onChange("cases"); } }); return; }   // PT
    if (addKind === "steady_state" || addKind === "psd") {
      openFreqCase({ ...ctx, onChange: k => { ctx.onChange && ctx.onChange(k); refresh(); } }, addKind, null);
      return;
    }
    const fnAdd = { static: ME.addCase, rs: ME.addRsCase, th: ME.addThCase, pushover: ME.addPushoverCase }[addKind];
    if (!fnAdd) return;
    for (const k of ["cases", "rs_cases", "th_cases", "pushover_cases"]) m[k] = m[k] || {};
    sel = fnAdd(m);
    ctx.markDirty();
    ctx.onChange && ctx.onChange("cases");
    refresh();
  }
  function modify() {
    if (!sel) return;
    const k = kindOf(sel);
    const sub = { ...ctx, onChange: key => { ctx.onChange && ctx.onChange(key); refresh(); } };
    if (k === "steady_state" || k === "psd") openFreqCase(sub, k, sel);
    else if (k === "rs") openRsOptions(sub, sel);
    else if (k === "th") openThOptions(sub, sel);
    else if (k === "pushover") openPushoverOptions(sub, sel);
    else if (k === "nonlinear_static" && window.__sky?.openNlsCase) window.__sky.openNlsCase(sel, { onChange: () => refresh() });   // G3
    else if (k === "modal" && window.__sky?.openModalCase) window.__sky.openModalCase({ onChange: () => refresh() });   // G3
    else if (k === "hyperstatic" && window.__sky?.openHyperstaticCase) window.__sky.openHyperstaticCase(sel, { onChange: () => refresh() });   // PT
    else gotoSection(k);
  }
  function del() {
    if (!sel) return;
    const k = kindOf(sel);
    let ok = false;
    if (k === "steady_state" || k === "psd") ok = deleteFreqCase(m, k, sel);
    else if (k === "static") {
      const refs = ME.caseRefs(m, sel);
      if (refs.length) { ctx.toast && ctx.toast("Can't delete", `${sel} is referenced by ${refs.join(", ")}`, "error", 5000); return; }
      ok = ME.deleteCase(m, sel);
    } else if (k === "rs") ok = ME.deleteRsCase(m, sel);
    else if (k === "th") ok = ME.deleteThCase(m, sel);
    else if (k === "pushover") ok = ME.deletePushoverCase(m, sel);
    else if (k === "nonlinear_static" && window.__sky?.deleteNlsCase) ok = window.__sky.deleteNlsCase(sel);   // G3
    else if (k === "hyperstatic" && window.__sky?.deleteHyperstaticCase) ok = window.__sky.deleteHyperstaticCase(sel);   // PT
    else if (k === "buckling" && ME.deleteBucklingCase) ok = ME.deleteBucklingCase(m, sel);
    else if (k === "staged" && ME.deleteStagedCase) ok = ME.deleteStagedCase(m, sel);
    if (!ok) return;
    sel = null;
    ctx.markDirty();
    ctx.onChange && ctx.onChange("cases");
    refresh();
  }
  const fb = footBar("", [btn("OK", "btn-run", () => dlg.close(), "", "lcOk")]);
  const dlg = dialog("loadCasesModal", { title: "Load Cases", glyph: "cases", wide: true, body, foot: fb.wrap });
  refresh();
  return dlg;
}

/* ================================================================
   Loads-pane section: steady-state / PSD cases + frequency functions
   (rendered by LoadsEditor.render through one hook line)
   ================================================================ */
export function frequencySection(m) {
  const sky = window.__sky;
  const c = () => makeCtx(sky);
  const sec = el("section", "loads-section");
  sec.id = "ls-freq";
  const head = el("header", "loads-section-head",
    `<div><h3>Frequency-domain cases</h3><p class="muted">Steady-state (harmonic) and power-spectral-density ` +
    `cases with their frequency functions. Results land under <b>Display → Frequency-Domain Results</b> after a solve.</p></div>`);
  const hb = el("div", "cd-inline");
  hb.append(
    btn("+ Steady State", "btn-small", () => openFreqCase(c(), "steady_state"), "Add a steady-state case", "lsAddSS"),
    btn("+ PSD", "btn-small", () => openFreqCase(c(), "psd"), "Add a PSD case", "lsAddPsd"),
    btn("Functions…", "btn-small", () => openFreqFunctions(c()), "Frequency functions", "lsFreqFns"));
  head.appendChild(hb);
  sec.appendChild(head);
  const list = el("div", "loads-rows");
  const rows = [];
  for (const kind of ["steady_state", "psd"])
    for (const [n, fc] of Object.entries(m[FREQ_DICT[kind]] || {})) rows.push([kind, n, fc]);
  if (!rows.length) list.innerHTML = `<p class="muted loads-empty">No steady-state or PSD cases yet.</p>`;
  for (const [kind, n, fc] of rows) {
    const card = el("div", "rs-card cd-fcard" + (ME.caseNotRun(m, n) ? " is-not-run" : ""));
    card.dataset.case = n;
    const loads = (fc.loads || []).map(l => (l.pattern === "accel" ? `accel ${l.direction}` : l.pattern) +
      (l.function ? ` · ${l.function}` : "")).join(" + ") || "no loads";
    const h = el("div", "rs-head cd-fhead",
      `${svgIcon(kind === "psd" ? "psd" : "freq", "cd-li-ico")}<b class="rs-name cd-fname">${esc(n)}</b>` +
      `<span class="cd-badge">${kind === "psd" ? "PSD" : "Steady State"}</span>` +
      `<span class="muted cd-fsum">${esc(loads)} · ${esc(U.fmt("frequency", fc.freq_start_hz ?? 0, 2))}–${esc(U.fmt("frequency", fc.freq_end_hz ?? 10, 2))} ${esc(U.label("frequency"))}` +
      ` · ${esc(fc.method || "modal")} · ζ ${esc(String(fc.damping ?? 0.05))}</span>` +
      (ME.caseNotRun(m, n) ? `<span class="notrun-badge">not run</span>` : ""));
    const mod = btn("Modify…", "btn-small", () => openFreqCase(c(), kind, n), "Edit this case");
    mod.classList.add("cd-fmod");
    const x = el("button", "del"); x.textContent = "✕"; x.title = `Delete ${n}`;
    x.addEventListener("click", () => {
      const cx = c();
      if (deleteFreqCase(cx.store.model, kind, n)) { cx.markDirty(); cx.onChange(FREQ_DICT[kind]); }
    });
    h.append(mod, x);
    card.appendChild(h);
    list.appendChild(card);
  }
  sec.appendChild(list);
  const fns = Object.values(m.frequency_functions || {});
  const fl = el("p", "muted cd-fnlist");
  fl.innerHTML = fns.length
    ? "Functions: " + fns.map(f => `<b>${esc(f.name)}</b> <span class="cd-badge">${f.kind === "psd" ? "PSD" : "SS"}</span>`).join(" ")
    : "No frequency functions yet.";
  sec.appendChild(fl);
  const cnt = document.getElementById("cnt-freq");
  if (cnt) cnt.textContent = String(rows.length);
  return sec;
}

/** "Options…" button for an RS / TH / pushover card in the loads editor. */
export function caseOptionsButton(kind, name) {
  const label = { rs: "Combination…", th: "Other Parameters…", pushover: "Load Application…" }[kind] || "Options…";
  const b = el("button", "btn btn-small cd-optbtn");
  b.textContent = label;
  b.dataset.optKind = kind; b.dataset.optCase = name;
  b.title = { rs: "Modal combination method, rigid response, missing mass",
    th: "Integration method, direct-integration damping, solver control, energy",
    pushover: "Load distribution, monitored displacement, load control, start from a static case" }[kind] || "";
  b.addEventListener("click", () => {
    const c = makeCtx(window.__sky);
    if (kind === "rs") openRsOptions(c, name);
    else if (kind === "th") openThOptions(c, name);
    else if (kind === "pushover") openPushoverOptions(c, name);
  });
  return b;
}

/* ================================================================
   Display → Frequency-Domain Results
   ================================================================ */
const BASE_KIND = { FX: "force", FY: "force", FZ: "force", MX: "moment", MY: "moment", MZ: "moment" };
const STORY_KIND = { ux: "disp", uy: "disp", drift_x: "none", drift_y: "none" };
const DOFS = ["UX", "UY", "UZ", "RX", "RY", "RZ"];
const dofKind = i => i < 3 ? "disp" : "rotation";
const psdDisp = (kind, v) => v * U.factor(kind) ** 2;
const psdLabel = kind => (U.label(kind) ? `${U.label(kind)}²` : "1") + "/Hz";

export function openFreqResults(ctx) {
  const r = ctx.store.results;
  const entries = [];
  for (const [n, v] of Object.entries((r && r.steady_state) || {})) entries.push(["steady_state", n, v]);
  for (const [n, v] of Object.entries((r && r.psd) || {})) entries.push(["psd", n, v]);
  const st = { key: entries[0] ? `${entries[0][0]}:${entries[0][1]}` : "", out: "base", item: "FX", comp: 0, logY: false };
  const body = el("div", "cd-res");
  const draw = () => {
    body.textContent = "";
    if (!entries.length) {
      body.appendChild(el("p", "muted dlg-intro", r
        ? "The last analysis has no steady-state or PSD results. Define a case (Define → Steady-State / PSD Cases…) and run the analysis."
        : "Run the analysis first."));
      return;
    }
    const [kind, name, res] = entries.find(e => `${e[0]}:${e[1]}` === st.key) || entries[0];
    st.key = `${kind}:${name}`;
    const ctl = el("div", "cd-inline cd-res-ctl");
    ctl.appendChild(row("Case", select(entries.map(e => [`${e[0]}:${e[1]}`, `${e[1]} (${e[0] === "psd" ? "PSD" : "Steady State"})`]), st.key,
      v => { st.key = v; draw(); }, "frCase")));
    const nodeSrc = kind === "psd" ? ((res.psd || {}).node_disp || {}) : (res.node_disp || {});
    const outs = [["base", "Base reactions"], ["story", "Story response"]];
    if (Object.keys(nodeSrc).length) outs.push(["node", "Joint displacement"]);
    if (!outs.some(o => o[0] === st.out)) st.out = "base";
    ctl.appendChild(row("Output", select(outs, st.out, v => {
      st.out = v;
      st.item = v === "base" ? "FX" : v === "story" ? Object.keys(res.story || (res.psd || {}).story || {})[0] : Object.keys(nodeSrc)[0];
      st.comp = v === "story" ? "ux" : 0;
      draw();
    }, "frOut")));
    let items = [];
    if (st.out === "base") items = Object.keys(BASE_KIND).map(k => [k, k]);
    else if (st.out === "story") items = Object.keys(kind === "psd" ? (res.psd || {}).story || {} : res.story || {}).map(s => [s, s]);
    else items = Object.keys(nodeSrc).map(t => [t, `Joint ${t}`]);
    if (!items.some(i => i[0] === st.item)) st.item = (items[0] || [])[0];
    ctl.appendChild(row("Item", select(items, st.item, v => { st.item = v; draw(); }, "frItem")));
    if (st.out === "story") {
      if (!(st.comp in STORY_KIND)) st.comp = "ux";
      ctl.appendChild(row("Component", select(Object.keys(STORY_KIND).map(k => [k, k]), st.comp, v => { st.comp = v; draw(); }, "frComp")));
    } else if (st.out === "node") {
      if (!(+st.comp >= 0 && +st.comp < 6)) st.comp = 0;
      ctl.appendChild(row("Component", select(DOFS.map((k, i) => [String(i), k]), String(st.comp), v => { st.comp = +v; draw(); }, "frComp")));
    }
    if (kind === "psd") ctl.appendChild(checkbox("Log scale", st.logY, v => { st.logY = v; draw(); }, "frLog"));
    body.appendChild(ctl);

    const f = res.frequencies_hz || [];
    let qKind = "none", amp = [], phase = null, rms = null, pk = null;
    if (kind === "steady_state") {
      let rec = null;
      if (st.out === "base") { rec = (res.base || {})[st.item]; qKind = BASE_KIND[st.item]; pk = ((res.peaks || {}).base || {})[st.item]; }
      else if (st.out === "story") { rec = ((res.story || {})[st.item] || {})[st.comp]; qKind = STORY_KIND[st.comp]; pk = (((res.peaks || {}).story || {})[st.item] || {})[st.comp]; }
      else {
        const nd = (res.node_disp || {})[st.item];
        if (nd) rec = { amp: (nd.amp || []).map(a => a[st.comp]), phase_deg: (nd.phase_deg || []).map(a => a[st.comp]) };
        qKind = dofKind(st.comp);
        pk = (((res.peaks || {}).node_disp || {})[st.item] || [])[st.comp];
      }
      amp = ((rec && rec.amp) || []).map(v => U.toDisplay(qKind, v));
      phase = (rec && rec.phase_deg) || [];
    } else {
      const P = res.psd || {};
      if (st.out === "base") { amp = (P.base || {})[st.item] || []; qKind = BASE_KIND[st.item]; rms = ((res.rms || {}).base || {})[st.item]; }
      else if (st.out === "story") { amp = ((P.story || {})[st.item] || {})[st.comp] || []; qKind = STORY_KIND[st.comp]; rms = (((res.rms || {}).story || {})[st.item] || {})[st.comp]; }
      else { amp = ((P.node_disp || {})[st.item] || []).map(a => a[st.comp]); qKind = dofKind(st.comp); rms = (((res.rms || {}).node_disp || {})[st.item] || [])[st.comp]; }
      amp = amp.map(v => psdDisp(qKind, v));
    }
    const ul = kind === "psd" ? psdLabel(qKind) : U.label(qKind);
    const chart = el("div", "cd-preview cd-res-chart");
    chart.id = "frChart";
    chart.innerHTML = `<div class="chart-title">${kind === "psd" ? "Response PSD" : "Amplitude"} <span class="unit">${esc(ul || "ratio")} vs f Hz · dashed = modal frequencies</span></div>` +
      lineChart([{ xs: f, ys: amp }], { w: 840, h: 250, logY: kind === "psd" && st.logY, vlines: res.modal_frequencies_hz || [], xLabel: "f, Hz",
        aria: "Frequency response" });
    body.appendChild(chart);
    if (phase && phase.length) {
      const ph = el("div", "cd-preview");
      ph.innerHTML = `<div class="chart-title">Phase <span class="unit">deg vs f Hz</span></div>` +
        lineChart([{ xs: f, ys: phase }], { w: 840, h: 140, xLabel: "f, Hz", aria: "Phase" });
      body.appendChild(ph);
    }
    const info = el("p", "muted cd-note");
    info.id = "frInfo";
    const meta = `${res.method || "modal"} · ${(res.damping || {}).type || "modal"} ζ = ${(res.damping || {}).ratio ?? "—"} · ` +
      `${res.modes_used ?? "—"} modes · ${f.length} frequencies`;
    info.innerHTML = (kind === "psd"
      ? `RMS = <b>${esc(U.fmt(qKind, rms, qKind === "none" ? 5 : 2))}</b> ${esc(U.label(qKind))} · correlation ${esc(res.correlation || "full")}`
      : `Peak = <b>${esc(U.fmt(qKind, pk, qKind === "none" ? 5 : 2))}</b> ${esc(U.label(qKind))}`) + ` · ${esc(meta)}`;
    body.appendChild(info);
    // base summary table
    const tbl = el("table", "data-table cd-res-table");
    const baseVals = kind === "psd" ? ((res.rms || {}).base || {}) : ((res.peaks || {}).base || {});
    const baseF = kind === "psd" ? null : ((res.peaks || {}).base_freq_hz || {});
    tbl.innerHTML = `<thead><tr><th class="txt">${kind === "psd" ? "Base RMS" : "Base peak"}</th>${Object.keys(BASE_KIND).map(k => `<th>${k} <span class="unit">${esc(U.label(BASE_KIND[k]))}</span></th>`).join("")}</tr></thead>` +
      `<tbody><tr><td class="txt">${kind === "psd" ? "RMS" : "Peak"}</td>${Object.keys(BASE_KIND).map(k => `<td>${esc(U.fmt(BASE_KIND[k], baseVals[k], 1))}</td>`).join("")}</tr>` +
      (baseF ? `<tr><td class="txt">at f (Hz)</td>${Object.keys(BASE_KIND).map(k => `<td>${esc(U.fmt("frequency", baseF[k], 3))}</td>`).join("")}</tr>` : "") +
      `</tbody>`;
    const tw = el("div", "table-scroll");
    tw.appendChild(tbl);
    body.appendChild(tw);
    if ((res.warnings || []).length)
      body.appendChild(el("p", "muted cd-note cd-warn", res.warnings.map(esc).join("<br>")));
  };
  const fb = footBar("", [btn("Close", "btn-run", () => dlg.close(), "", "frClose")]);
  const dlg = dialog("freqResModal", { title: "Frequency-Domain Results", glyph: "freq", wide: true, body, foot: fb.wrap, onUnits: draw });
  draw();
  return dlg;
}

/* ================================================================
   install — adds the entrypoints to window.__sky (additive only)
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
export function installCaseDialogs(sky) {
  if (!sky) return;
  Object.assign(sky, {
    openLoadCases: () => openLoadCases(makeCtx(sky)),
    openFreqCase: (kind, name) => openFreqCase(makeCtx(sky), kind, name),
    openFreqFunctions: opts => openFreqFunctions(makeCtx(sky), opts),
    openThOptions: name => openThOptions(makeCtx(sky), name),
    openRsOptions: name => openRsOptions(makeCtx(sky), name),
    openPushoverOptions: name => openPushoverOptions(makeCtx(sky), name),
    openFreqResults: () => openFreqResults(makeCtx(sky)),
    closeCaseDialog: closeDialog,
  });
}
