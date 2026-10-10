/* SkyFrame — Edit > Interactive Database Editing… (ETABS-style model tables).
   CONTRACT "Interactive database editing (model tables)".

   Left: tree of the model-definition tables (POST /api/modeltables/list).
   Right: an editable virtual grid of one table (POST /api/modeltables/<key>):
   only the visible rows are in the DOM. In-cell editors by column type
   (number in display units via units.js, enum <select>, bool toggle, text,
   object-uid text with a datalist, point lists), add / delete rows, range
   selection (click / shift-click / drag / shift+arrows), Ctrl+C copies the
   range as TSV, Ctrl+V pastes Excel TSV into the range (a single value fills
   the whole range; rows past the end are appended), Delete clears cells.
   Per-table CSV export / import (SI, exact) and a whole-model workbook (zip
   of CSVs) export / import.

   Edits stay local until Apply / OK: every edited table is posted at once to
   POST /api/modeltables/apply {model, tables}; errors come back per
   {table,row,col} and are highlighted per cell (nothing is applied). On
   success the echoed model is adopted as ONE undo step ("Interactive
   database edit", js/history.js via sky.history + markDirty). ?mock=1 runs
   the same logic client-side (js/mock_dbedit.js). The model store stays SI;
   every number is converted at the cell boundary through units.js. */

import dbeU from "./units.js";
import { ICONS as dbeICONS, MENUITEM_ICON as dbeMENUITEM_ICON, icon as dbeIcon } from "./icons.js";
import { downloadText as dbeDownloadText, copyText as dbeCopyText } from "./tables.js";
import { normalizeModel as dbeNormalizeModel } from "./modeledit.js";
import { mockDbEdit as dbeMockDbEdit } from "./mock_dbedit.js";

dbeICONS["db-edit"] = '<rect x="3" y="4" width="14" height="12" rx="1.2"/><path d="M3 8 H17 M8 4 V16"/><path d="M11.5 14.5 L15.5 10.5 L17 12 L13 16 H11.5 Z"/>';
dbeMENUITEM_ICON["edit-dbedit"] = "db-edit";

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const clone = o => JSON.parse(JSON.stringify(o));
const ROW_H = 24;
const HDR_W = 46;

/* ------------------------------------------------------------ cell specs */
/** {kind, unit} for a column (kind = units.js quantity kind or null). */
export function dbeColSpec(c) {
  const q = c.quantity || "none";
  if (c.type === "number" || c.type === "points") {
    if (q === "angle_deg") return { kind: null, unit: "deg" };
    if (q !== "none" && dbeU.QUANTITY_KINDS.includes(q)) return { kind: q, unit: dbeU.label(q) };
  }
  return { kind: null, unit: "" };
}
/* 12 significant digits hide float noise (9.600000000000001 → 9.6); an
   untouched / re-typed-identical cell keeps its exact SI value. */
const fmtNum = (kind, v) => {
  if (v == null || v === "") return "";
  if (typeof v !== "number") return String(v);
  if (kind && !dbeU.isIdentity(kind)) return dbeU.inputValue(kind, v);
  return String(+v.toPrecision(12));
};
/** Display text of an SI cell value. */
export function dbeCellText(c, spec, v) {
  if (v == null) return "";
  if (c.type === "bool") return v === true ? "✓" : v === false ? "" : String(v);
  if (c.type === "number") return fmtNum(spec.kind, v);
  if (c.type === "points") return Array.isArray(v) ? v.map(p => Array.isArray(p) ? p.map(x => fmtNum(spec.kind, x)).join(", ") : String(p)).join("; ") : String(v);
  return String(v);
}
const TRUE = new Set(["true", "yes", "y", "1", "t", "on", "x", "✓", "☑"]);
const FALSE = new Set(["false", "no", "n", "0", "f", "off", "", "☐"]);
/** Display text typed / pasted into a cell → SI value (invalid text is kept
    raw so the backend reports the precise error; `err` flags it locally). */
export function dbeParseText(c, spec, text) {
  const t = String(text ?? "").trim();
  if (c.type === "number" || c.type === "int") {
    if (!t) return { v: null };
    const n = Number(t.replace(/,/g, "").replace("−", "-"));
    if (!isFinite(n)) return { v: t, err: `'${t}' is not a number` };
    if (c.type === "int") return Number.isInteger(n) ? { v: n } : { v: t, err: "must be a whole number" };
    return { v: spec.kind ? dbeU.fromDisplay(spec.kind, n) : n };
  }
  if (c.type === "bool") {
    const s = t.toLowerCase();
    if (TRUE.has(s)) return { v: true };
    if (FALSE.has(s)) return { v: false };
    return { v: t, err: "must be true / false" };
  }
  if (c.type === "points") {
    if (!t) return { v: t, err: "points are required" };
    const pts = t.split(";").map(s => s.trim()).filter(Boolean).map(s => s.split(/[,\s]+/).filter(Boolean).map(Number));
    if (!pts.length || pts.some(p => p.length !== 3 || !p.every(isFinite))) return { v: t, err: "expected 'x,y,z; x,y,z; …'" };
    return { v: pts.map(p => p.map(x => spec.kind ? dbeU.fromDisplay(spec.kind, x) : x)) };
  }
  if (c.type === "enum" && c.enum) {
    const hit = c.enum.find(o => String(o) === t) ?? c.enum.find(o => String(o).toLowerCase() === t.toLowerCase());
    if (hit !== undefined) return { v: hit };
    // not (yet) a known value: kept as typed — a name added by another table
    // in the same Apply is valid; otherwise the server reports the cell
    const n = Number(t);
    return { v: t !== "" && c.enum.every(o => typeof o === "number") && isFinite(n) ? n : t };
  }
  return { v: t };
}

/* ------------------------------------------------------------ editable grid */
class DbeGrid {
  constructor(host, owner) {
    this.owner = owner;
    this.el = document.createElement("div");
    this.el.className = "dbe-grid";
    this.el.tabIndex = 0;
    this.el.setAttribute("role", "grid");
    this.head = document.createElement("div");
    this.head.className = "dbe-head";
    this.body = document.createElement("div");
    this.body.className = "dbe-body";
    this.win = document.createElement("div");
    this.win.className = "dbe-win";
    this.body.appendChild(this.win);
    this.empty = document.createElement("div");
    this.empty.className = "dbe-empty hidden";
    this.el.append(this.head, this.body, this.empty);
    host.appendChild(this.el);
    this.t = null; this.view = []; this.sel = null; this.editor = null; this.drag = false;
    this.el.addEventListener("scroll", () => {
      if (this._raf) return;
      this._raf = requestAnimationFrame(() => { this._raf = 0; this.renderRows(); });
    }, { passive: true });
    this.win.addEventListener("mousedown", e => this.onDown(e));
    this.win.addEventListener("dblclick", e => { const p = this.cellAt(e); if (p) this.startEdit(p.r, p.c); });
    document.addEventListener("mousemove", this._mm = e => this.onMove(e));
    document.addEventListener("mouseup", this._mu = () => { this.drag = false; });
    this.el.addEventListener("keydown", e => this.onKey(e));
    this.el.addEventListener("paste", e => this.onPaste(e));
    this.el.addEventListener("copy", e => this.onCopy(e));
    if (typeof ResizeObserver !== "undefined") { this._ro = new ResizeObserver(() => this.renderRows(true)); this._ro.observe(this.el); }
  }
  destroy() {
    document.removeEventListener("mousemove", this._mm);
    document.removeEventListener("mouseup", this._mu);
    if (this._ro) this._ro.disconnect();
  }
  setTable(t) {
    this.cancelEdit();
    this.t = t;
    this.sel = t && t.rows.length ? { r0: 0, c0: 0, r1: 0, c1: 0 } : null;
    this.el.scrollTop = 0;
    this.refresh();
  }
  /** Recompute specs / widths / view (after units change, edits, filter). */
  refresh() {
    const t = this.t;
    if (!t) { this.head.innerHTML = ""; this.win.innerHTML = ""; return; }
    this.specs = t.columns.map(dbeColSpec);
    const sample = t.rows.length > 300 ? t.rows.filter((_, i) => i % Math.ceil(t.rows.length / 300) === 0) : t.rows;
    const widths = t.columns.map((c, i) => {
      let ch = Math.max(c.label.length, (this.specs[i].unit || "").length + 2);
      for (const r of sample) ch = Math.max(ch, dbeCellText(c, this.specs[i], r[c.key]).length);
      return Math.max(64, Math.min(c.type === "points" ? 320 : 220, ch * 7.4 + 22));
    });
    this.template = `${HDR_W}px ` + widths.map(w => `${Math.round(w)}px`).join(" ");
    const total = HDR_W + widths.reduce((a, b) => a + b, 0);
    this.el.style.setProperty("--dbe-cols", this.template);
    this.head.style.minWidth = this.body.style.minWidth = this.win.style.minWidth = total + "px";
    this.head.innerHTML = `<div class="dbe-h dbe-rh">#</div>` + t.columns.map((c, i) => {
      const s = this.specs[i];
      const cls = ["dbe-h", c.editable ? "" : "ro", c.required ? "req" : ""].filter(Boolean).join(" ");
      const tip = `${c.label}${s.unit ? ` [${s.unit}]` : ""}${c.editable ? "" : " — read only"}${c.required ? " — required" : ""}`;
      return `<div class="${cls}" data-c="${i}" title="${esc(tip)}"><span class="dbe-hl">${esc(c.label)}</span><span class="dbe-hu">${esc(s.unit || " ")}</span></div>`;
    }).join("");
    this.applyView();
  }
  applyView() {
    const t = this.t, f = (this.owner.filter || "").trim().toLowerCase();
    let idx = t.rows.map((_, i) => i);
    if (f) {
      const terms = f.split(/\s+/);
      idx = idx.filter(i => { const r = t.rows[i]; const s = t.columns.map((c, j) => dbeCellText(c, this.specs[j], r[c.key])).join("\u0001").toLowerCase(); return terms.every(x => s.includes(x)); });
    }
    this.view = idx;
    this.body.style.height = idx.length * ROW_H + "px";
    this.empty.textContent = t.rows.length ? "No rows match the filter" : (t.editable && t.can_add ? "No rows — use + Add Row or paste from Excel" : "No rows");
    this.empty.classList.toggle("hidden", idx.length > 0);
    if (this.sel) { const n = idx.length - 1; for (const k of ["r0", "r1"]) this.sel[k] = Math.max(0, Math.min(n, this.sel[k])); if (n < 0) this.sel = null; }
    this._start = -1;
    this.renderRows(true);
  }
  renderRows(force = false) {
    const t = this.t;
    if (!t) return;
    const n = this.view.length, vh = this.el.clientHeight || 400;
    const first = Math.floor(this.el.scrollTop / ROW_H);
    const start = Math.max(0, first - 6), end = Math.min(n, first + Math.ceil(vh / ROW_H) + 6);
    if (!force && start === this._start && end === this._end) return;
    this._start = start; this._end = end;
    const sel = this.sel ? { ra: Math.min(this.sel.r0, this.sel.r1), rb: Math.max(this.sel.r0, this.sel.r1), ca: Math.min(this.sel.c0, this.sel.c1), cb: Math.max(this.sel.c0, this.sel.c1) } : null;
    let html = "";
    for (let k = start; k < end; k++) {
      const ri = this.view[k], r = t.rows[ri];
      const rowErr = t.rowErrs.get(ri);
      const isNew = r._id === undefined;
      html += `<div class="dbe-r${k & 1 ? " odd" : ""}${isNew ? " is-new" : ""}" data-v="${k}">`;
      html += `<div class="dbe-c dbe-rh${rowErr ? " err" : ""}"${rowErr ? ` title="${esc(rowErr)}"` : ""}>${ri + 1}</div>`;
      t.columns.forEach((c, j) => {
        const txt = dbeCellText(c, this.specs[j], r[c.key]);
        const e = t.cellErrs.get(`${ri}|${c.key}`) || (t.localErrs.get(`${ri}|${c.key}`));
        const changed = !isNew && t.orig.has(r._idKey) && JSON.stringify(t.orig.get(r._idKey)[c.key]) !== JSON.stringify(r[c.key]);
        const cls = ["dbe-c", c.type === "number" || c.type === "int" ? "num" : "", c.type === "bool" ? "bool" : "",
          c.editable && t.editable ? "" : "ro", e ? "err" : "", changed ? "chg" : "",
          sel && k >= sel.ra && k <= sel.rb && j >= sel.ca && j <= sel.cb ? "sel" : "",
          this.sel && k === this.sel.r1 && j === this.sel.c1 ? "cur" : ""].filter(Boolean).join(" ");
        html += `<div class="${cls}" data-c="${j}"${e ? ` title="${esc(e)}"` : ""}>${esc(txt)}</div>`;
      });
      html += "</div>";
    }
    this.win.style.transform = `translateY(${start * ROW_H}px)`;
    this.win.innerHTML = html;
    if (this.editor) this.placeEditor();
  }
  cellAt(e) {
    const cell = e.target.closest(".dbe-c");
    const row = e.target.closest(".dbe-r");
    if (!cell || !row) return null;
    const k = +row.dataset.v;
    return { r: k, c: cell.classList.contains("dbe-rh") ? -1 : +cell.dataset.c };
  }
  onDown(e) {
    if (e.button !== 0) return;
    const p = this.cellAt(e);
    if (!p) return;
    if (this.editor && this.editor.input.contains(e.target)) return;
    this.commitEdit();
    e.preventDefault();
    this.el.focus({ preventScroll: true });
    if (p.c === -1) {                          // row header → whole row(s)
      const last = this.t.columns.length - 1;
      if (e.shiftKey && this.sel) this.sel = { r0: this.sel.r0, c0: 0, r1: p.r, c1: last };
      else this.sel = { r0: p.r, c0: 0, r1: p.r, c1: last };
    } else if (e.shiftKey && this.sel) { this.sel.r1 = p.r; this.sel.c1 = p.c; }
    else {
      const same = this.sel && this.sel.r0 === p.r && this.sel.r1 === p.r && this.sel.c0 === p.c && this.sel.c1 === p.c;
      this.sel = { r0: p.r, c0: p.c, r1: p.r, c1: p.c };
      const c = this.t.columns[p.c];
      if (same && c.type === "bool" && this.canEdit(p.r, p.c)) { this.setCell(p.r, p.c, !(this.t.rows[this.view[p.r]][c.key] === true)); }
      else if (same && this.canEdit(p.r, p.c)) { this.startEdit(p.r, p.c); return; }
    }
    this.drag = p.c !== -1;
    this.renderRows(true);
    this.owner.onSelection();
  }
  onMove(e) {
    if (!this.drag || !this.sel) return;
    const el = document.elementFromPoint(e.clientX, e.clientY);
    if (!el || !this.win.contains(el)) return;
    const p = this.cellAt({ target: el });
    if (!p || p.c < 0) return;
    if (p.r !== this.sel.r1 || p.c !== this.sel.c1) { this.sel.r1 = p.r; this.sel.c1 = p.c; this.renderRows(true); }
  }
  canEdit(k, j) {
    const t = this.t, c = t.columns[j];
    if (!t.editable || !c || !c.editable) return false;
    return k >= 0 && k < this.view.length;
  }
  ensureVisible(k) {
    const top = k * ROW_H, vh = this.el.clientHeight - 40;
    if (top < this.el.scrollTop) this.el.scrollTop = top;
    else if (top + ROW_H > this.el.scrollTop + vh) this.el.scrollTop = top + ROW_H - vh;
    const cell = this.win.querySelector(`.dbe-r[data-v="${k}"] .dbe-c[data-c="${this.sel ? this.sel.c1 : 0}"]`);
    if (cell) {
      const l = cell.offsetLeft, w = cell.offsetWidth;
      if (l - HDR_W < this.el.scrollLeft) this.el.scrollLeft = Math.max(0, l - HDR_W);
      else if (l + w > this.el.scrollLeft + this.el.clientWidth) this.el.scrollLeft = l + w - this.el.clientWidth;
    }
  }
  move(dr, dc, extend) {
    if (!this.sel) { if (this.view.length) this.sel = { r0: 0, c0: 0, r1: 0, c1: 0 }; else return; }
    const nr = Math.max(0, Math.min(this.view.length - 1, this.sel.r1 + dr));
    const nc = Math.max(0, Math.min(this.t.columns.length - 1, this.sel.c1 + dc));
    if (extend) { this.sel.r1 = nr; this.sel.c1 = nc; } else this.sel = { r0: nr, c0: nc, r1: nr, c1: nc };
    this.renderRows(true);
    this.ensureVisible(nr);
    this.renderRows();
    this.owner.onSelection();
  }
  onKey(e) {
    if (!this.t) return;
    if (this.editor) return;                       // the editor handles its own keys
    const k = e.key, mod = e.ctrlKey || e.metaKey;
    const arrows = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    if (arrows[k]) { e.preventDefault(); this.move(...arrows[k], e.shiftKey); return; }
    if (k === "Tab") { e.preventDefault(); this.move(0, e.shiftKey ? -1 : 1, false); return; }
    if (k === "PageDown" || k === "PageUp") { e.preventDefault(); const n = Math.floor((this.el.clientHeight - 40) / ROW_H); this.move(k === "PageDown" ? n : -n, 0, e.shiftKey); return; }
    if (k === "Home" || k === "End") { e.preventDefault(); if (mod) this.move(k === "Home" ? -1e9 : 1e9, 0, e.shiftKey); else this.move(0, k === "Home" ? -1e9 : 1e9, e.shiftKey); return; }
    if (!this.sel) return;
    if (k === "Enter" || k === "F2") {
      e.preventDefault(); e.stopPropagation();
      if (this.canEdit(this.sel.r1, this.sel.c1)) this.startEdit(this.sel.r1, this.sel.c1);
      else this.move(1, 0, false);
      return;
    }
    if (k === "Delete" || k === "Backspace") { e.preventDefault(); this.fillRange(""); return; }
    if (k === " " && this.t.columns[this.sel.c1] && this.t.columns[this.sel.c1].type === "bool") {
      e.preventDefault();
      const cur = this.t.rows[this.view[this.sel.r1]][this.t.columns[this.sel.c1].key] === true;
      this.fillRange(cur ? "false" : "true");
      return;
    }
    if (mod && k.toLowerCase() === "a") { e.preventDefault(); this.sel = { r0: 0, c0: 0, r1: this.view.length - 1, c1: this.t.columns.length - 1 }; this.renderRows(true); this.owner.onSelection(); return; }
    if (!mod && !e.altKey && k.length === 1 && this.canEdit(this.sel.r1, this.sel.c1)) {
      const c = this.t.columns[this.sel.c1];
      if (c.type === "bool") return;
      e.preventDefault();
      this.startEdit(this.sel.r1, this.sel.c1, k);
    }
  }
  /* ---- in-cell editor */
  startEdit(k, j, initial) {
    if (!this.canEdit(k, j)) return;
    this.commitEdit();
    const t = this.t, c = t.columns[j], ri = this.view[k], r = t.rows[ri];
    this.sel = { r0: k, c0: j, r1: k, c1: j };
    this.ensureVisible(k);
    let input;
    if (c.type === "bool") {
      input = document.createElement("select");
      input.innerHTML = `<option value="true">Yes</option><option value="false">No</option>`;
      input.value = r[c.key] === true ? "true" : "false";
    } else {
      // enum columns: free text + suggestions, so a name added in another
      // table of the same Apply can be referenced (the server validates)
      input = document.createElement("input");
      input.type = "text";
      input.spellcheck = false;
      input.autocomplete = "off";
      input.value = initial != null ? initial : dbeCellText(c, this.specs[j], r[c.key]);
      if (c.type === "ref") { input.setAttribute("list", "dbeRefList"); this.owner.fillRefList(c.ref); }
      if (c.type === "enum") { input.setAttribute("list", "dbeEnumList"); this.owner.fillEnumList(c); }
    }
    input.className = "dbe-editor";
    input.setAttribute("aria-label", `${c.label} (row ${ri + 1})`);
    this.editor = { k, j, input, orig: input.value };
    this.el.appendChild(input);
    this.placeEditor();
    input.focus();
    if (initial == null && input.select) input.select();
    input.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); this.commitEdit(); this.move(e.shiftKey ? -1 : 1, 0, false); this.el.focus({ preventScroll: true }); }
      else if (e.key === "Tab") { e.preventDefault(); e.stopPropagation(); this.commitEdit(); this.move(0, e.shiftKey ? -1 : 1, false); this.el.focus({ preventScroll: true }); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); this.cancelEdit(); this.el.focus({ preventScroll: true }); }
      else if ((e.key === "ArrowUp" || e.key === "ArrowDown") && input.tagName === "INPUT") {
        e.preventDefault(); this.commitEdit(); this.move(e.key === "ArrowUp" ? -1 : 1, 0, false); this.el.focus({ preventScroll: true });
      }
    });
    input.addEventListener("blur", () => setTimeout(() => { if (this.editor && this.editor.input === input) this.commitEdit(); }, 0));
    if (input.tagName === "SELECT") input.addEventListener("change", () => { this.commitEdit(); this.el.focus({ preventScroll: true }); });
  }
  placeEditor() {
    const ed = this.editor;
    const cell = this.win.querySelector(`.dbe-r[data-v="${ed.k}"] .dbe-c[data-c="${ed.j}"]`);
    if (!cell) { ed.input.style.display = "none"; return; }
    ed.input.style.display = "";
    const er = this.el.getBoundingClientRect(), cr = cell.getBoundingClientRect();
    Object.assign(ed.input.style, { left: `${cr.left - er.left + this.el.scrollLeft}px`, top: `${cr.top - er.top + this.el.scrollTop}px`,
      width: `${Math.max(cr.width, 90)}px`, height: `${cr.height}px` });
  }
  commitEdit() {
    const ed = this.editor;
    if (!ed) return;
    this.editor = null;
    const val = ed.input.value;
    ed.input.remove();
    if (val !== ed.orig) this.setCellText(ed.k, ed.j, val);
    this.renderRows(true);
  }
  cancelEdit() {
    const ed = this.editor;
    if (!ed) return;
    this.editor = null;
    ed.input.remove();
    this.renderRows(true);
  }
  /* ---- value writes */
  setCellText(k, j, text) {
    const t = this.t, c = t.columns[j], ri = this.view[k], r = t.rows[ri];
    if (!this.canEdit(k, j)) return false;
    if (text === dbeCellText(c, this.specs[j], r[c.key])) return false;     // unchanged display → keep exact SI
    const { v, err } = dbeParseText(c, this.specs[j], text);
    this.owner.writeCell(ri, c.key, v, err);
    return true;
  }
  setCell(k, j, v) {
    const t = this.t, c = t.columns[j], ri = this.view[k];
    if (!this.canEdit(k, j)) return;
    this.owner.writeCell(ri, c.key, v, null);
    this.renderRows(true);
  }
  fillRange(text) {
    if (!this.sel) return;
    const ra = Math.min(this.sel.r0, this.sel.r1), rb = Math.max(this.sel.r0, this.sel.r1);
    const ca = Math.min(this.sel.c0, this.sel.c1), cb = Math.max(this.sel.c0, this.sel.c1);
    let n = 0;
    this.owner.batch(() => { for (let k = ra; k <= rb; k++) for (let j = ca; j <= cb; j++) if (this.setCellText(k, j, text)) n++; });
    this.renderRows(true);
    return n;
  }
  /* ---- clipboard */
  rangeTsv() {
    if (!this.sel) return "";
    const ra = Math.min(this.sel.r0, this.sel.r1), rb = Math.max(this.sel.r0, this.sel.r1);
    const ca = Math.min(this.sel.c0, this.sel.c1), cb = Math.max(this.sel.c0, this.sel.c1);
    const lines = [];
    for (let k = ra; k <= rb; k++) {
      const r = this.t.rows[this.view[k]], cells = [];
      for (let j = ca; j <= cb; j++) { const c = this.t.columns[j]; cells.push(c.type === "bool" ? (r[c.key] === true ? "TRUE" : "FALSE") : dbeCellText(c, this.specs[j], r[c.key])); }
      lines.push(cells.join("\t"));
    }
    return lines.join("\n");
  }
  onCopy(e) {
    if (this.editor || !this.sel) return;
    e.preventDefault();
    e.clipboardData.setData("text/plain", this.rangeTsv());
    this.owner.status(`Copied ${this.selSize()} cell(s) as tab-separated text (${dbeU.getUnits()})`);
  }
  selSize() {
    if (!this.sel) return 0;
    return (Math.abs(this.sel.r1 - this.sel.r0) + 1) * (Math.abs(this.sel.c1 - this.sel.c0) + 1);
  }
  onPaste(e) {
    if (this.editor) return;
    const text = (e.clipboardData || window.clipboardData).getData("text/plain");
    if (text == null) return;
    e.preventDefault();
    this.pasteText(text);
  }
  /** Paste TSV (Excel) at the selection: 1 value fills the range; a block
      pastes from the top-left, appending rows past the end when allowed. */
  pasteText(text) {
    const t = this.t;
    if (!t || !t.editable) { this.owner.status("This table is read only", true); return 0; }
    if (!this.sel) this.sel = { r0: 0, c0: 0, r1: 0, c1: 0 };
    const grid = text.replace(/\r\n?/g, "\n").replace(/\n$/, "").split("\n").map(l => l.split("\t"));
    const ra = Math.min(this.sel.r0, this.sel.r1), rb = Math.max(this.sel.r0, this.sel.r1);
    const ca = Math.min(this.sel.c0, this.sel.c1), cb = Math.max(this.sel.c0, this.sel.c1);
    let n = 0;
    if (grid.length === 1 && grid[0].length === 1) {
      n = this.fillRange(grid[0][0]);
    } else {
      const filtered = this.view.length !== t.rows.length;
      const need = ra + grid.length - this.view.length;
      if (need > 0) {
        if (!t.can_add || filtered) { this.owner.status(filtered ? "Clear the filter to paste past the last row" : "Rows cannot be added to this table", true); return 0; }
        for (let i = 0; i < need; i++) this.owner.addRow(false);
        this.applyView();
      }
      this.owner.batch(() => {
        grid.forEach((cells, di) => cells.forEach((txt, dj) => {
          const j = ca + dj;
          if (j < t.columns.length && this.setCellText(ra + di, j, txt)) n++;
        }));
      });
      this.sel = { r0: ra, c0: ca, r1: ra + grid.length - 1, c1: Math.min(t.columns.length - 1, ca + Math.max(...grid.map(g => g.length)) - 1) };
    }
    this.refresh();
    this.owner.status(`Pasted ${n} cell(s)`);
    return n;
  }
}

/* ------------------------------------------------------------ the dialog */
export function initDbEdit(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  if (!document.querySelector("link[data-dbedit-css]")) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/static/dbedit.css"; l.setAttribute("data-dbedit-css", "1");
    document.head.appendChild(l);
  }
  const ST = { dlg: null, list: null, key: null, tables: new Map(), base: null, openGroups: null, filter: "", busy: false };
  let els = {}, grid = null;

  const modelPayload = () => { const m = clone(S.model); delete m._mock_params; return m; };
  async function api(path, body = {}) {
    if (S.mock) { await new Promise(r => setTimeout(r, 30)); return dbeMockDbEdit(ST.base || S.model, path, body); }
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON */ }
    if (!res.ok || (data && data.ok === false)) { const e = new Error((data && data.error) || `${res.status} ${res.statusText}`); e.errors = data && data.errors; throw e; }
    return data;
  }

  /* ---- table state */
  function makeState(tb) {
    const rows = clone(tb.rows).map(r => { r._idKey = JSON.stringify(r._id); return r; });
    const orig = new Map(rows.map(r => [r._idKey, clone(r)]));
    return { ...tb, rows, orig, base: sig(rows), cellErrs: new Map(), rowErrs: new Map(), tableErrs: [], localErrs: new Map(),
      dirty: false };
  }
  const sig = rows => JSON.stringify(rows.map(r => { const o = { ...r }; delete o._idKey; return o; }));
  const cur = () => ST.tables.get(ST.key);
  const outRows = t => t.rows.map(r => { const o = {}; for (const [k, v] of Object.entries(r)) if (k !== "_idKey" && !(k === "_id" && v === undefined)) o[k] = v; return o; });
  function markDirtyState(t) {
    t.dirty = sig(t.rows) !== t.base;
    if (els.errors && !els.errors.classList.contains("hidden")) renderErrors(); else renderTreeMarks();
    syncFoot();
  }
  const owner = {
    get filter() { return ST.filter; },
    writeCell(ri, key, v, err) {
      const t = cur();
      t.rows[ri][key] = v;
      const k = `${ri}|${key}`;
      t.cellErrs.delete(k);
      if (err) t.localErrs.set(k, err); else t.localErrs.delete(k);
      if (!this._batch) markDirtyState(t);
    },
    batch(fn) { this._batch = true; try { fn(); } finally { this._batch = false; markDirtyState(cur()); } },
    addRow(render = true) { return addRow(render); },
    onSelection() { syncSelInfo(); },
    status(msg, bad) { setStatus(msg, bad); },
    fillEnumList(c) {
      const dl = document.getElementById("dbeEnumList");
      if (dl) dl.innerHTML = (c.enum || []).map(o => `<option value="${esc(String(o))}">${o === "" ? "(none)" : ""}</option>`).join("");
    },
    fillRefList(ref) {
      const dl = document.getElementById("dbeRefList");
      if (!dl) return;
      const pool = ref === "members" ? (S.model.members || []) : ref === "shells" ? (S.model.shells || []) : (S.model.links || []);
      dl.innerHTML = pool.slice(0, 5000).map(o => `<option value="${esc(o.uid)}">`).join("");
    },
  };

  function addRow(render = true) {
    const t = cur();
    if (!t || !t.editable || !t.can_add) return null;
    const r = {};
    for (const c of t.columns) if (c.editable && c.default !== undefined) r[c.key] = clone(c.default);
    const pc = t.columns.find(c => c.key === "pattern" && c.type === "enum");
    if (pc && pc.enum && pc.enum.length) r.pattern = pc.enum[0];
    for (const pk of ["case", "combo", "group", "system"]) {
      const c = t.columns.find(x => x.key === pk);
      if (c && c.enum && c.enum.length && r[pk] === undefined) r[pk] = c.enum[c.enum.length - 1];
    }
    if (t.key === "grid_lines" && t.rows.length) { const last = t.rows[t.rows.length - 1]; r.system = last.system; r.axis = last.axis; }
    t.rows.push(r);
    if (render) {
      grid.refresh();
      const k = grid.view.length - 1;
      const j = Math.max(0, t.columns.findIndex(c => c.editable));
      grid.sel = { r0: k, c0: j, r1: k, c1: j };
      grid.renderRows(true); grid.ensureVisible(k); grid.renderRows(true);
      grid.el.focus({ preventScroll: true });
    }
    markDirtyState(t);
    return r;
  }
  function deleteRows() {
    const t = cur();
    if (!t || !grid.sel) return;
    if (!t.editable || !t.can_delete) { setStatus("Rows cannot be deleted from this table", true); return; }
    const ra = Math.min(grid.sel.r0, grid.sel.r1), rb = Math.max(grid.sel.r0, grid.sel.r1);
    const kill = new Set(grid.view.slice(ra, rb + 1));
    t.rows = t.rows.filter((_, i) => !kill.has(i));
    t.cellErrs.clear(); t.rowErrs.clear(); t.localErrs.clear();
    grid.sel = t.rows.length ? { r0: Math.min(ra, t.rows.length - 1), c0: 0, r1: Math.min(ra, t.rows.length - 1), c1: 0 } : null;
    grid.refresh();
    markDirtyState(t);
    setStatus(`Deleted ${kill.size} row(s) — applied on Apply / OK`);
  }

  /* ---- dialog shell (same markup as js/tables.js / analysisdlg.js) */
  function close() {
    if (!ST.dlg) return;
    document.removeEventListener("keydown", ST.dlg.onKey, true);
    if (grid) grid.destroy();
    ST.dlg.el.remove();
    ST.dlg = null; grid = null; ST.tables.clear();
  }
  async function open(key) {
    if (ST.dlg) { if (key) select(key); return api; }
    if (!S.model) { toast("Interactive Database Editing", "No model loaded.", "error"); return null; }
    ST.base = modelPayload();
    ST.tables.clear();
    const back = document.createElement("div");
    back.className = "modal-backdrop sky-dlg";
    back.id = "dbEditModal";
    back.innerHTML = `
      <div class="modal tbl-modal dbe-modal" role="dialog" aria-labelledby="dbEditTitle">
        <header class="modal-head">
          <h2 id="dbEditTitle" class="dlg-title">${dbeIcon("db-edit", "dlg-ico")}<span>Interactive Database Editing — Model Definition</span></h2>
          <button class="icon-btn" title="Close" aria-label="Close Interactive Database Editing">×</button>
        </header>
        <div class="modal-body tbl-body">
          <aside class="tbl-tree" id="dbeTree" aria-label="Model tables"><div class="muted tbl-loading">Loading tables…</div></aside>
          <section class="tbl-main">
            <div class="tbl-titlebar">
              <div class="tbl-title"><b id="dbeTitle">Select a table</b><span class="muted" id="dbeGroup"></span></div>
              <span class="tbl-unitset" title="Display units (Options → Units…)">Units <b class="dbe-units">${esc(dbeU.getUnits())}</b></span>
            </div>
            <div class="tbl-tools dbe-tools">
              <button class="btn btn-small" id="dbeAdd" title="Append a new row (defaults filled in)">+ Add Row</button>
              <button class="btn btn-small" id="dbeDel" title="Delete the selected rows (Apply to commit)">Delete Rows</button>
              <button class="btn btn-small" id="dbeRevert" title="Discard the unapplied edits of this table">Revert Table</button>
              <input type="search" id="dbeFilter" class="tbl-filter dbe-filter" placeholder="Filter rows…" aria-label="Filter rows">
              <span class="muted tbl-count" id="dbeCount"></span>
              <span class="toolbar-spacer"></span>
              <button class="chip csv-btn" id="dbeCopy" title="Copy the selected range as tab-separated text (Ctrl+C)">Copy</button>
              <button class="chip csv-btn" id="dbePasteBtn" title="Paste tab-separated text from the clipboard into the selection (Ctrl+V)">Paste</button>
              <button class="chip csv-btn" id="dbeCsvOut" title="Export this table as CSV (SI units, exact)">⬇ CSV</button>
              <button class="chip csv-btn" id="dbeCsvIn" title="Import this table from CSV (replaces the grid rows; Apply to commit)">⬆ CSV</button>
              <button class="chip csv-btn" id="dbeZipOut" title="Export every table as a workbook (zip of CSVs, SI)">⬇ Workbook</button>
              <button class="chip csv-btn" id="dbeZipIn" title="Import a workbook (zip of CSVs) and apply it to the model">⬆ Workbook</button>
              <input type="file" id="dbeFileCsv" accept=".csv,text/csv" class="hidden">
              <input type="file" id="dbeFileZip" accept=".zip,application/zip" class="hidden">
            </div>
            <div class="dbe-status muted" id="dbeStatus" aria-live="polite"></div>
            <div class="tbl-grid-host dbe-grid-host" id="dbeGridHost"></div>
            <div class="dbe-errors hidden" id="dbeErrors" role="alert"></div>
            <datalist id="dbeRefList"></datalist><datalist id="dbeEnumList"></datalist>
          </section>
        </div>
        <footer class="modal-foot"><div class="dlg-foot">
          <span class="muted dlg-foot-note" id="dbeFoot">Edits are validated and applied atomically on Apply / OK (one undo step).</span>
          <div class="modal-btns">
            <button class="btn" id="dbeCancel">Cancel</button>
            <button class="btn" id="dbeApply">Apply</button>
            <button class="btn btn-run" id="dbeOk">OK</button>
          </div>
        </div></footer>
      </div>`;
    (document.getElementById("app") || document.body).appendChild(back);
    const $ = id => back.querySelector("#" + id);
    const onKey = e => {
      if (!ST.dlg) return;
      if (e.key === "Escape") {
        if (grid && grid.editor) return;            // the cell editor cancels itself
        e.stopImmediatePropagation(); e.preventDefault();
        close();
      } else if (e.key === "Enter" && grid && (document.activeElement === grid.el || grid.editor)) {
        // Enter inside the grid edits / moves — never the dialog's OK
      }
    };
    document.addEventListener("keydown", onKey, true);
    ST.dlg = { el: back, onKey };
    els = { tree: $("dbeTree"), title: $("dbeTitle"), group: $("dbeGroup"), count: $("dbeCount"), status: $("dbeStatus"),
      host: $("dbeGridHost"), errors: $("dbeErrors"), foot: $("dbeFoot"), filter: $("dbeFilter"), apply: $("dbeApply"), ok: $("dbeOk"),
      add: $("dbeAdd"), del: $("dbeDel"), revert: $("dbeRevert") };
    back.querySelector(".modal-head .icon-btn").addEventListener("click", close);
    $("dbeCancel").addEventListener("click", close);
    back.addEventListener("mousedown", e => { if (e.target === back) close(); });
    grid = new DbeGrid(els.host, owner);
    grid.el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === "Escape") e.preventDefault(); });
    els.add.addEventListener("click", () => addRow(true));
    els.del.addEventListener("click", deleteRows);
    els.revert.addEventListener("click", revertTable);
    let ft = 0;
    els.filter.addEventListener("input", () => { clearTimeout(ft); ft = setTimeout(() => { ST.filter = els.filter.value; grid.applyView(); syncCount(); }, 90); });
    $("dbeCopy").addEventListener("click", async () => {
      const ok = await dbeCopyText(grid.rangeTsv());
      setStatus(ok ? `Copied ${grid.selSize()} cell(s) (tab-separated, ${dbeU.getUnits()})` : "The browser blocked clipboard access", !ok);
    });
    $("dbePasteBtn").addEventListener("click", async () => {
      try { const txt = await navigator.clipboard.readText(); grid.pasteText(txt); }
      catch { setStatus("Clipboard read blocked — click a cell and press Ctrl+V", true); }
    });
    $("dbeCsvOut").addEventListener("click", exportCsv);
    $("dbeCsvIn").addEventListener("click", () => $("dbeFileCsv").click());
    $("dbeFileCsv").addEventListener("change", async e => { const f = e.target.files[0]; e.target.value = ""; if (f) importCsv(await f.text()); });
    $("dbeZipOut").addEventListener("click", exportZip);
    $("dbeZipIn").addEventListener("click", () => $("dbeFileZip").click());
    $("dbeFileZip").addEventListener("change", async e => { const f = e.target.files[0]; e.target.value = ""; if (f) importZip(await f.arrayBuffer()); });
    els.apply.addEventListener("click", () => applyAll(false));
    els.ok.addEventListener("click", () => applyAll(true));
    await loadList();
    await select(key || ST.key || "frame_sections");
    return api;
  }

  /* ---- tree */
  async function loadList() {
    try { ST.list = (await api("/api/modeltables/list", { model: ST.base })).tables || []; }
    catch (err) { if (els.tree) els.tree.innerHTML = `<div class="field-error">Tables unavailable: ${esc(err.message)}</div>`; return; }
    renderTree();
  }
  function renderTree() {
    if (!ST.dlg || !ST.list) return;
    const groups = new Map();
    for (const t of ST.list) { if (!groups.has(t.group)) groups.set(t.group, []); groups.get(t.group).push(t); }
    if (!ST.openGroups) ST.openGroups = new Set(groups.keys());
    let html = "";
    for (const [g, list] of groups) {
      const open = ST.openGroups.has(g);
      html += `<div class="tbl-node${open ? " open" : ""}" data-g="${esc(g)}"><button class="tbl-group"><span class="ex-caret">▶</span>${esc(g.replace(/^Model Definition > /, ""))}</button><div class="tbl-kids">`;
      for (const t of list) html += `<button class="tbl-leaf dbe-leaf${t.key === ST.key ? " is-sel" : ""}" data-key="${esc(t.key)}" title="${esc(t.title)}${t.editable ? "" : " (read only)"}">`
        + `<span class="dbe-leaf-t">${esc(t.title)}</span><span class="dbe-mark" data-mark="${esc(t.key)}"></span><span class="dbe-n muted">${t.rows ?? ""}</span></button>`;
      html += "</div></div>";
    }
    html += `<div class="muted tbl-tree-foot">${ST.list.length} tables · edits apply atomically</div>`;
    els.tree.innerHTML = html;
    els.tree.querySelectorAll(".tbl-group").forEach(b => b.addEventListener("click", () => {
      const n = b.parentElement, g = n.dataset.g;
      n.classList.toggle("open");
      if (n.classList.contains("open")) ST.openGroups.add(g); else ST.openGroups.delete(g);
    }));
    els.tree.querySelectorAll(".tbl-leaf").forEach(b => b.addEventListener("click", () => select(b.dataset.key)));
    renderTreeMarks();
  }
  function renderTreeMarks() {
    if (!els.tree) return;
    els.tree.querySelectorAll("[data-mark]").forEach(sp => {
      const t = ST.tables.get(sp.dataset.mark);
      const nerr = t ? t.cellErrs.size + t.rowErrs.size + t.tableErrs.length + t.localErrs.size : 0;
      sp.className = "dbe-mark" + (nerr ? " is-err" : t && t.dirty ? " is-dirty" : "");
      sp.textContent = nerr ? `⚠ ${nerr}` : t && t.dirty ? "●" : "";
      sp.title = nerr ? `${nerr} error(s)` : t && t.dirty ? "edited (not applied)" : "";
    });
  }

  async function select(key) {
    if (!ST.dlg || !key) return;
    if (grid && grid.editor) grid.commitEdit();
    ST.key = key;
    els.tree.querySelectorAll(".tbl-leaf").forEach(b => b.classList.toggle("is-sel", b.dataset.key === key));
    let t = ST.tables.get(key);
    if (!t) {
      els.host.classList.add("is-loading");
      try { t = makeState(await api(`/api/modeltables/${encodeURIComponent(key)}`, { model: ST.base })); ST.tables.set(key, t); }
      catch (err) { setStatus(`Could not load ${key}: ${err.message}`, true); els.host.classList.remove("is-loading"); return; }
      els.host.classList.remove("is-loading");
      if (ST.key !== key) return;
    }
    els.title.textContent = t.title;
    els.group.textContent = `${t.group}${t.editable ? "" : " · read only"}`;
    els.add.disabled = !(t.editable && t.can_add);
    els.del.disabled = !(t.editable && t.can_delete);
    els.revert.disabled = !t.editable;
    grid.setTable(t);
    syncCount(); renderErrors(); syncFoot();
    setStatus(t.editable ? "Double-click / Enter / type to edit · Ctrl+V pastes Excel ranges · Delete clears" : "Read-only table (derived from the model)");
  }
  function revertTable() {
    const t = cur();
    if (!t) return;
    const fresh = makeState({ ...t, rows: JSON.parse(t.base) });
    ST.tables.set(t.key, fresh);
    grid.setTable(fresh);
    markDirtyState(fresh);
    renderErrors(); syncCount();
    setStatus("Reverted the unapplied edits of this table");
  }

  /* ---- status / errors */
  function setStatus(msg, bad = false) { if (els.status) { els.status.textContent = msg || ""; els.status.classList.toggle("is-bad", !!bad); } }
  function syncCount() {
    const t = cur();
    if (!t || !els.count) return;
    els.count.textContent = grid.view.length === t.rows.length ? `${t.rows.length} rows` : `${grid.view.length} of ${t.rows.length} rows`;
  }
  function syncSelInfo() {
    const t = cur();
    if (!t || !grid.sel) return;
    const c = t.columns[grid.sel.c1], ri = grid.view[grid.sel.r1];
    if (!c || ri == null) return;
    const e = t.cellErrs.get(`${ri}|${c.key}`) || t.localErrs.get(`${ri}|${c.key}`) || t.rowErrs.get(ri);
    if (e) setStatus(`Row ${ri + 1}, ${c.label}: ${e}`, true);
    else {
      const sp = dbeColSpec(c);
      setStatus(`Row ${ri + 1} · ${c.label}${sp.unit ? ` [${sp.unit}]` : ""} · ${c.type}${c.editable && t.editable ? "" : " (read only)"}${c.required ? " · required" : ""}${grid.selSize() > 1 ? ` · ${grid.selSize()} cells selected` : ""}`);
    }
  }
  function syncFoot() {
    const dirty = [...ST.tables.values()].filter(t => t.dirty);
    if (!els.foot) return;
    els.foot.textContent = dirty.length ? `${dirty.length} table(s) edited: ${dirty.map(t => t.title).join(", ")} — Apply / OK to commit (one undo step).`
      : "Edits are validated and applied atomically on Apply / OK (one undo step).";
  }
  const colLabel = e => (e.t.columns.find(c => c.key === e.col) || {}).label || e.col;
  function renderErrors() {
    const all = [];
    for (const t of ST.tables.values()) {
      for (const m of t.tableErrs) all.push({ t, row: null, col: null, m });
      for (const [ri, m] of t.rowErrs) all.push({ t, row: ri, col: null, m });
      for (const [k, m] of [...t.cellErrs, ...t.localErrs]) { const [ri, col] = k.split("|"); all.push({ t, row: +ri, col, m }); }
    }
    if (!els.errors) return;
    els.errors.classList.toggle("hidden", !all.length);
    if (!all.length) { els.errors.innerHTML = ""; renderTreeMarks(); return; }
    els.errors.innerHTML = `<div class="dbe-err-h">${all.length} error(s) — nothing was applied. Click an error to go to the cell.</div>`
      + all.slice(0, 200).map((e, i) => `<button class="dbe-err" data-i="${i}">`
        + `<b>${esc(e.t.title)}</b>${e.row != null ? ` · row ${e.row + 1}` : ""}${e.col ? ` · ${esc(colLabel(e))}` : ""}: ${esc(e.col && e.m.startsWith(colLabel(e) + ": ") ? e.m.slice(colLabel(e).length + 2) : e.m)}</button>`).join("");
    els.errors.querySelectorAll(".dbe-err").forEach(b => b.addEventListener("click", async () => {
      const e = all[+b.dataset.i];
      await select(e.t.key);
      if (e.row == null) return;
      ST.filter = ""; els.filter.value = ""; grid.applyView();
      const k = grid.view.indexOf(e.row), j = Math.max(0, e.t.columns.findIndex(c => c.key === e.col));
      if (k < 0) return;
      grid.sel = { r0: k, c0: j, r1: k, c1: j };
      grid.renderRows(true); grid.ensureVisible(k); grid.renderRows(true);
      grid.el.focus({ preventScroll: true });
      syncSelInfo();
    }));
    renderTreeMarks();
  }
  function clearServerErrors() { for (const t of ST.tables.values()) { t.cellErrs.clear(); t.rowErrs.clear(); t.tableErrs = []; } }

  /* ---- apply */
  async function applyAll(closeAfter) {
    if (ST.busy) return;
    if (grid && grid.editor) grid.commitEdit();
    const dirty = [...ST.tables.values()].filter(t => t.dirty);
    const local = dirty.reduce((s, t) => s + t.localErrs.size, 0);
    if (local) { renderErrors(); setStatus(`${local} cell(s) hold invalid values — fix them first`, true); return; }
    if (!dirty.length) { if (closeAfter) close(); else setStatus("No edits to apply"); return; }
    const tables = Object.fromEntries(dirty.map(t => [t.key, outRows(t)]));
    ST.busy = true; els.apply.disabled = els.ok.disabled = true;
    setStatus("Validating and applying…");
    try {
      const res = await api("/api/modeltables/apply", { model: ST.base, tables, soft_errors: true });
      adopt(res, `Applied ${dirty.length} table(s)`, closeAfter);
    } catch (err) { showServerErrors(err); }
    finally { ST.busy = false; if (els.apply) els.apply.disabled = els.ok.disabled = false; }
  }
  function showServerErrors(err) {
    clearServerErrors();
    const errs = err.errors || [{ table: ST.key, row: null, col: null, message: err.message }];
    for (const e of errs) {
      const t = ST.tables.get(e.table) || cur();
      if (!t) continue;
      if (e.row == null) t.tableErrs.push(e.message);
      else if (e.col) t.cellErrs.set(`${e.row}|${e.col}`, e.message);
      else t.rowErrs.set(e.row, e.message);
    }
    const first = errs[0];
    if (first && first.table && first.table !== ST.key && ST.tables.has(first.table)) select(first.table);
    else if (grid) grid.renderRows(true);
    renderErrors();
    setStatus(err.message, true);
  }
  function refreshViews() {
    S.modelEdited = true;
    try { sky.rebuildStorySelect && sky.rebuildStorySelect(); } catch (e) { console.error(e); }
    try { sky.planEditor && sky.planEditor.refresh(); } catch (e) { console.error(e); }
    try { sky.elevEditor && sky.elevEditor.refresh(); } catch (e) { console.error(e); }
    try { sky.renderProps && sky.renderProps(); } catch (e) { console.error(e); }
    try { if (S.mode === "loads" && sky.loadsEditor) sky.loadsEditor.render(); } catch (e) { console.error(e); }
    try { if (S.mode === "analyze" && sky.viewer) { sky.viewer.setModel(S.model); S.modelEdited = false; } } catch (e) { console.error(e); }
  }
  function adopt(res, what, closeAfter) {
    const md = S.mock ? res.model : (sky.keepSetup ? sky.keepSetup(res.model) : res.model);
    if (sky.history) sky.history.label("Interactive database edit");
    S.model = dbeNormalizeModel(md);
    sky.markDirty && sky.markDirty();             // → one labelled undo step + chrome refresh
    refreshViews();
    const sm = res.summary || {};
    const parts = Object.entries(sm).map(([k, v]) => {
      if (!v) return null;
      const bits = [];
      if (v.added) bits.push(`+${v.added}`);
      if (v.modified) bits.push(`~${v.modified}`);
      if (v.deleted) bits.push(`−${v.deleted}`);
      return bits.length ? `${k} ${bits.join(" ")}` : null;
    }).filter(Boolean);
    toast("Interactive Database Editing", `${what}${parts.length ? ": " + parts.join(", ") : ""}. Edit › Undo reverts it.`, "info", 5000);
    if (closeAfter) { close(); return; }
    ST.base = modelPayload();
    const key = ST.key;
    ST.tables.clear();
    loadList().then(() => select(key));
    if (els.errors) { els.errors.classList.add("hidden"); els.errors.innerHTML = ""; }
  }

  /* ---- CSV / workbook */
  async function exportCsv() {
    const t = cur();
    if (!t) return;
    try {
      const r = await api(`/api/modeltables/${encodeURIComponent(t.key)}/csv`, { model: ST.base });
      dbeDownloadText(r.csv, r.filename);
      setStatus(`Exported ${r.filename} (SI units; the last applied state)`);
    } catch (err) { setStatus(err.message, true); }
  }
  async function importCsv(text) {
    const t = cur();
    if (!t) return;
    if (!t.editable) { setStatus("This table is read only", true); return; }
    try {
      const r = await api(`/api/modeltables/${encodeURIComponent(t.key)}/csv_import`, { model: ST.base, csv: text });
      t.rows = r.rows.map(x => { x._idKey = JSON.stringify(x._id); return x; });
      t.cellErrs.clear(); t.rowErrs.clear(); t.localErrs.clear(); t.tableErrs = [];
      grid.setTable(t);
      markDirtyState(t); syncCount();
      setStatus(`Imported ${r.rows.length} row(s) from CSV — review, then Apply / OK`);
    } catch (err) { setStatus(err.message, true); }
  }
  async function exportZip() {
    try {
      let blob;
      if (S.mock) blob = (await api("/api/modeltables/export", { model: ST.base })).blob;
      else {
        const res = await fetch("/api/modeltables/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model: ST.base }) });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        blob = await res.blob();
      }
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "model_tables.zip";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      setStatus("Exported model_tables.zip (one CSV per table, SI units)");
    } catch (err) { setStatus(err.message, true); }
  }
  async function importZip(buf) {
    setStatus("Importing workbook…");
    try {
      let res;
      if (S.mock) res = await api("/api/modeltables/import", { model: ST.base, buffer: buf });
      else {
        let bin = "";
        const u8 = new Uint8Array(buf);
        for (let i = 0; i < u8.length; i += 0x8000) bin += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
        res = await api("/api/modeltables/import", { model: ST.base, zip_b64: btoa(bin), soft_errors: true });
      }
      adopt(res, "Imported the workbook", false);
    } catch (err) { showServerErrors(err); }
  }

  document.addEventListener("sky:units-changed", () => {
    if (!ST.dlg || !grid) return;
    grid.refresh();
    const u = ST.dlg.el.querySelector(".dbe-units");
    if (u) u.textContent = dbeU.getUnits();
  });

  const pub = {
    open, close, select,
    isOpen: () => !!ST.dlg,
    state: () => ({ key: ST.key, dirty: [...ST.tables.values()].filter(t => t.dirty).map(t => t.key),
      errors: [...ST.tables.values()].reduce((s, t) => s + t.cellErrs.size + t.rowErrs.size + t.tableErrs.length + t.localErrs.size, 0) }),
    table: key => { const t = ST.tables.get(key || ST.key); return t ? { columns: t.columns, rows: outRows(t) } : null; },
    grid: () => grid,
    addRow: () => addRow(true), deleteRows, apply: () => applyAll(false), ok: () => applyAll(true),
    paste: text => grid && grid.pasteText(text),
    selectRange: (r0, c0, r1 = r0, c1 = c0) => { if (!grid) return; grid.sel = { r0, c0, r1, c1 }; grid.renderRows(true); grid.ensureVisible(r1); grid.renderRows(true); },
    colIndex: key => { const t = cur(); return t ? t.columns.findIndex(c => c.key === key) : -1; },
  };
  sky.dbedit = pub;
  sky.openDbEdit = key => open(key).catch(err => toast("Interactive Database Editing", err.message, "error", 7000));
  return pub;
}
