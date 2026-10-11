/* SkyFrame — dlgkit: the ONE standard dialog layout (docs/DIALOG_STANDARD.md).

   ETABS-style anatomy, identical in every migrated dialog:
     header   icon + Title Case title + "×"                (.modal-head)
     intro    one muted sentence saying what the dialog does (optional)
     tabs     only when the content would scroll (> ~10 fields)
     groups   <fieldset> per topic with a short legend
     rows     label · control · unit label (every number shows its unit)
     Advanced a collapsed <details> holding rarely used parameters, each with
              its default shown; the summary says how many differ from default
     footer   [note · extra buttons]            OK · Cancel · (Apply)
   Keys: Enter = OK (commits the field being typed in first), Esc = Cancel.
   Validation: inline, under the field, in plain language; OK focuses the
   first invalid field (switching tab / opening Advanced as needed).

   Data model: dialogs edit a DRAFT (dkDraft = deep clone of the working
   model). A field writes its bound key ONLY when the user changes it — an
   untouched field never writes — and dkCommit copies back only the
   top-level keys whose JSON differs. So OK with defaults leaves the model
   byte-identical (no markDirty at all), and an edit is one undo step.
   Every number goes through js/units.js; the draft and model stay SI. */

import dkU from "./units.js";
import { icon as dkIcon } from "./icons.js";

export const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const clone = o => o === undefined ? undefined : JSON.parse(JSON.stringify(o));
export const isNum = v => typeof v === "number" && isFinite(v);
const UNITLESS = new Set(["none", "strain"]);
/** Unit column text for a dimensionless number (ratio, factor, count). */
export const DIMLESS = "–";

/* ------------------------------------------------ stylesheet (once) */
let cssDone = false;
export function ensureCss() {
  if (cssDone || document.querySelector("link[data-dk-css]")) { cssDone = true; return; }
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/dlgkit.css"; l.setAttribute("data-dk-css", "1");
  document.head.appendChild(l);
  cssDone = true;
}

export function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  (Array.isArray(kids) ? kids : [kids]).forEach(c => {
    if (c == null || c === false) return;
    n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  });
  return n;
}

/** Unit label for a quantity kind ("" for unitless). */
export const unitLabel = kind => (!kind || UNITLESS.has(kind)) ? "" : dkU.label(kind);
/** Display string of an SI value (6 significant digits, exact when factor 1). */
export const showNum = (kind, si) => si == null || !isFinite(si) ? "" : (kind && !UNITLESS.has(kind) ? dkU.inputValue(kind, si) : String(+(+si).toPrecision(10)));
const parseNum = (kind, txt) => kind && !UNITLESS.has(kind) ? dkU.parse(kind, txt) : (String(txt).trim() === "" ? NaN : Number(txt));

/* ================================================================
   Model draft / commit
   ================================================================ */
export function dkDraft(sky) { return clone(sky.store.model); }

/** Copy the top-level keys of `draft` that differ (JSON) back into the
    working model. Returns the changed keys ([] → nothing written, no
    markDirty — byte-identical). */
export function dkCommit(sky, draft) {
  const S = sky.store, m = S.model;
  if (!m || !draft) return [];
  const changed = [];
  for (const k of Object.keys(draft)) {
    if (k === "_mock_params") continue;
    if (JSON.stringify(draft[k]) !== JSON.stringify(m[k])) { m[k] = draft[k]; changed.push(k); }
  }
  for (const k of Object.keys(m)) if (!(k in draft) && k !== "_mock_params") { delete m[k]; changed.push(k); }
  if (!changed.length) return changed;
  // the legacy single grid stays identity-shared with grid_systems[0]
  if ((changed.includes("grid_systems") || changed.includes("grid")) && sky.ME && sky.ME.ensureGridSystems) {
    if (changed.includes("grid_systems") && Array.isArray(m.grid_systems) && m.grid_systems[0] && m.grid_systems[0].kind !== "radial") m.grid = m.grid_systems[0];
    sky.ME.ensureGridSystems(m);
  }
  sky.markDirty && sky.markDirty();
  dkRefresh(sky, changed);
  return changed;
}

const GEOM_KEYS = ["members", "shells", "sections", "shell_sections", "materials", "stories", "grid", "grid_systems",
  "links", "spring_supports", "line_springs", "supports", "base_fixity"];
/** Refresh the views that show the changed keys (cheap, best-effort). */
export function dkRefresh(sky, changed = []) {
  const S = sky.store;
  try {
    if (changed.some(k => GEOM_KEYS.includes(k))) {
      if (sky.viewer && sky.viewer.setModel) sky.viewer.setModel(S.model);
      if (changed.includes("stories") && sky.rebuildStorySelect) sky.rebuildStorySelect();
      if (changed.includes("stories") && sky.rebuildElevSelect) sky.rebuildElevSelect();
    }
    if (sky.rerenderAll) sky.rerenderAll();
    else {
      if (sky.renderProps) sky.renderProps();
      if (S.mode === "loads" && sky.loadsEditor) sky.loadsEditor.render();
    }
    if (sky.etabs && sky.etabs.refresh) sky.etabs.refresh();
  } catch (e) { console.warn("dlgkit refresh", e); }
}

/* ================================================================
   Form — field registry (validation, unit refresh, touched tracking)
   ================================================================ */
export function dkForm() {
  const fields = [];
  const F = { fields, touched: false };
  const reg = f => { fields.push(f); return f; };
  const markTouched = () => { F.touched = true; F.onTouch && F.onTouch(); };

  /** Generic labelled row: label · control · unit · (hint) + inline error. */
  function row(label, control, { unit = "", hint = "", id, title, wide = false, cls = "" } = {}) {
    const lab = el("label", { class: "dk-label", for: id || null, title: title || null }, [label]);
    const u = el("span", { class: "dk-unit", text: unit });
    const err = el("div", { class: "dk-err", role: "alert", "aria-live": "polite" });
    const r = el("div", { class: "dk-row" + (wide ? " dk-row-wide" : "") + (cls ? " " + cls : "") }, [lab, el("div", { class: "dk-ctl" }, [control]), u]);
    if (hint) r.appendChild(el("div", { class: "dk-hint muted", html: hint }));
    r.appendChild(err);
    return { row: r, err, unit: u, label: lab };
  }
  F.row = (label, control, opts) => row(label, control, opts).row;

  function setErr(f, msg) {
    f.error = msg || "";
    f.errEl.textContent = f.error;
    f.rowEl.classList.toggle("is-invalid", !!f.error);
    if (f.input) f.input.setAttribute("aria-invalid", f.error ? "true" : "false");
  }

  /** Number field bound to obj[key] (SI). opts: label, kind, min, max, gt,
      lt, int, allowNull (empty → null), nullText, def (default, shown as a
      hint), mixed (multi-selection: start blank), onChange(v), validate(v)
      → message, id, step, readonly, hint, title, write(v) (custom setter). */
  F.num = (obj, key, o = {}) => {
    const kind = o.kind || "none";
    const inp = el("input", { type: "text", inputmode: "decimal", class: "dk-num", id: o.id || null, autocomplete: "off", spellcheck: "false" });
    if (o.readonly) inp.readOnly = true;
    if (o.title) inp.title = o.title;
    const get = () => o.read ? o.read() : (obj ? obj[key] : undefined);
    const hintParts = [];
    if (o.hint) hintParts.push(o.hint);
    if (o.def !== undefined) hintParts.push(`Default ${o.def === null ? (o.nullText || "auto") : esc(showNum(kind, o.def)) + (unitLabel(kind) ? " " + esc(unitLabel(kind)) : "")}`);
    const r = row(o.label || key, inp, { unit: o.unit != null ? o.unit : (unitLabel(kind) || DIMLESS), hint: hintParts.join(" · "), id: o.id, title: o.title });
    const f = reg({ type: "num", key, obj, kind, input: inp, rowEl: r.row, errEl: r.err, unitEl: r.unit, o, dirty: false, value: undefined });
    const placeholder = () => o.mixed ? "— mixed —" : (get() == null && o.allowNull ? (o.nullText || "auto") : (o.placeholder || ""));
    f.render = () => {
      const v = get();
      inp.value = o.mixed && !f.dirty ? "" : showNum(kind, v);
      inp.placeholder = placeholder();
      if (o.unit == null) r.unit.textContent = unitLabel(kind) || DIMLESS;
    };
    f.check = () => {
      const txt = inp.value.trim();
      if (txt === "") {
        if (o.mixed && !f.dirty) return "";
        if (o.allowNull) return "";
        if (o.optional) return "";
        return "Enter a value.";
      }
      const v = parseNum(kind, txt);
      if (!isFinite(v)) return "Enter a number" + (unitLabel(kind) ? ` in ${unitLabel(kind)}` : "") + ".";
      const disp = x => showNum(kind, x) + (unitLabel(kind) ? " " + unitLabel(kind) : "");
      if (o.int && !Number.isInteger(v)) return "Enter a whole number.";
      if (o.min != null && v < o.min) return `Must be at least ${disp(o.min)}.`;
      if (o.max != null && v > o.max) return `Must be at most ${disp(o.max)}.`;
      if (o.gt != null && !(v > o.gt)) return o.gt === 0 ? "Must be greater than zero." : `Must be greater than ${disp(o.gt)}.`;
      if (o.lt != null && !(v < o.lt)) return `Must be less than ${disp(o.lt)}.`;
      if (o.validate) { const m = o.validate(v); if (m) return m; }
      return "";
    };
    inp.addEventListener("change", () => {
      const msg = f.check();
      setErr(f, msg);
      if (msg) return;
      const txt = inp.value.trim();
      if (txt === "" && o.mixed && !f.dirty) return;
      const v = txt === "" ? null : parseNum(kind, txt);
      f.dirty = true; f.value = v;
      if (o.write) o.write(v); else if (obj) obj[key] = v;
      markTouched();
      o.onChange && o.onChange(v);
    });
    inp.addEventListener("input", () => { if (f.error) setErr(f, f.check()); });
    f.setErr = m => setErr(f, m);
    f.render();
    return r.row;
  };

  /** Text field. opts: label, validate(v) → message, required, onChange, id. */
  F.text = (obj, key, o = {}) => {
    const inp = el("input", { type: "text", class: "dk-text", id: o.id || null, autocomplete: "off", spellcheck: "false" });
    const get = () => o.read ? o.read() : obj[key];
    const r = row(o.label || key, inp, { unit: o.unit || "", hint: o.hint, id: o.id, wide: o.wide });
    const f = reg({ type: "text", key, obj, input: inp, rowEl: r.row, errEl: r.err, o, dirty: false });
    f.render = () => { inp.value = get() == null ? "" : String(get()); if (o.placeholder) inp.placeholder = o.placeholder; };
    f.check = () => {
      const v = inp.value.trim();
      if (o.required !== false && !v) return o.requiredMsg || "Enter a name.";
      return o.validate ? (o.validate(v) || "") : "";
    };
    inp.addEventListener("input", () => { setErr(f, f.check()); });
    inp.addEventListener("change", () => {
      const msg = f.check();
      setErr(f, msg);
      if (msg) return;
      const v = o.raw ? inp.value : inp.value.trim();
      if (v === String(get() == null ? "" : get())) return;
      f.dirty = true;
      if (o.write) o.write(v); else obj[key] = v;
      markTouched();
      o.onChange && o.onChange(v);
    });
    f.setErr = m => setErr(f, m);
    f.render();
    return r.row;
  };

  /** Select bound to obj[key]. options: [[value, label], …] or a function. */
  F.select = (obj, key, options, o = {}) => {
    const sel = el("select", { class: "dk-select", id: o.id || null });
    const get = () => o.read ? o.read() : obj[key];
    const r = row(o.label || key, sel, { unit: o.unit || "", hint: o.hint, id: o.id, title: o.title });
    const f = reg({ type: "select", key, obj, input: sel, rowEl: r.row, errEl: r.err, o, dirty: false });
    f.render = () => {
      const opts = typeof options === "function" ? options() : options;
      const cur = get();
      sel.textContent = "";
      if (o.mixed && !f.dirty) sel.appendChild(el("option", { value: "", selected: true, disabled: true }, ["— mixed —"]));
      let found = false;
      for (const [v, lbl] of opts) {
        const op = el("option", { value: String(v) }, [lbl]);
        if (!(o.mixed && !f.dirty) && String(v) === String(cur)) { op.selected = true; found = true; }
        sel.appendChild(op);
      }
      if (!found && !(o.mixed && !f.dirty) && cur != null && cur !== "") {
        sel.insertBefore(el("option", { value: String(cur), selected: true }, [String(cur) + " (current)"]), sel.firstChild);
      }
    };
    f.check = () => o.validate ? (o.validate(sel.value) || "") : "";
    sel.addEventListener("change", () => {
      const opts = typeof options === "function" ? options() : options;
      const hit = opts.find(([v]) => String(v) === sel.value);
      const v = hit ? hit[0] : sel.value;
      f.dirty = true;
      if (o.write) o.write(v); else obj[key] = v;
      setErr(f, f.check());
      markTouched();
      o.onChange && o.onChange(v);
    });
    f.setErr = m => setErr(f, m);
    f.render();
    return r.row;
  };

  /** Checkbox bound to obj[key] (boolean). */
  F.check = (obj, key, o = {}) => {
    const cb = el("input", { type: "checkbox", id: o.id || null });
    const get = () => o.read ? o.read() : !!obj[key];
    const lab = el("label", { class: "dk-check", title: o.title || null }, [cb, el("span", { html: o.labelHtml || esc(o.label || key) })]);
    const err = el("div", { class: "dk-err", role: "alert" });
    const r = el("div", { class: "dk-row dk-row-check" + (o.indent ? " dk-indent" : "") }, [lab]);
    if (o.hint) r.appendChild(el("div", { class: "dk-hint muted", html: o.hint }));
    r.appendChild(err);
    const f = reg({ type: "check", key, obj, input: cb, rowEl: r, errEl: err, o, dirty: false });
    f.render = () => {
      if (o.mixed && !f.dirty) { cb.indeterminate = true; cb.checked = false; }
      else { cb.indeterminate = false; cb.checked = !!get(); }
    };
    f.check = () => "";
    cb.addEventListener("change", () => {
      f.dirty = true;
      if (o.write) o.write(cb.checked); else obj[key] = cb.checked;
      markTouched();
      o.onChange && o.onChange(cb.checked);
    });
    f.setErr = m => setErr(f, m);
    f.render();
    return r;
  };

  /** Radio group bound to obj[key]. options: [[value, label, hint?], …]. */
  F.radio = (obj, key, options, o = {}) => {
    const name = "dkr" + Math.random().toString(36).slice(2, 8);
    const get = () => o.read ? o.read() : obj[key];
    const box = el("div", { class: "dk-radios", role: "radiogroup", "aria-label": o.label || key });
    const err = el("div", { class: "dk-err", role: "alert" });
    const r = el("div", { class: "dk-row dk-row-radio" }, [o.label ? el("div", { class: "dk-label" }, [o.label]) : null, box, err]);
    const f = reg({ type: "radio", key, obj, rowEl: r, errEl: err, o, dirty: false, input: null });
    const inputs = [];
    for (const [v, lbl, hint] of options) {
      const rb = el("input", { type: "radio", name, value: String(v), id: o.idPrefix ? o.idPrefix + v : null });
      inputs.push(rb);
      rb.addEventListener("change", () => {
        if (!rb.checked) return;
        f.dirty = true;
        if (o.write) o.write(v); else obj[key] = v;
        markTouched();
        o.onChange && o.onChange(v);
      });
      box.appendChild(el("label", { class: "dk-check" }, [rb, el("span", {}, [lbl]), hint ? el("span", { class: "muted dk-radio-hint", text: hint }) : null]));
    }
    f.input = inputs[0];
    f.render = () => { const cur = get(); inputs.forEach(rb => { rb.checked = rb.value === String(cur); }); };
    f.check = () => "";
    f.setErr = m => setErr(f, m);
    f.render();
    return r;
  };

  /** Read-only derived value with unit (refreshes on F.refresh()). */
  F.readout = (label, getSi, o = {}) => {
    const kind = o.kind || "none";
    const out = el("output", { class: "dk-readout", id: o.id || null });
    const r = row(label, out, { unit: o.unit != null ? o.unit : unitLabel(kind), hint: o.hint, title: o.title });
    const f = reg({ type: "readout", rowEl: r.row, errEl: r.err, input: null, o, dirty: false });
    f.render = () => {
      const v = getSi();
      out.textContent = typeof v === "string" ? v : (v == null || !isFinite(v) ? "—" : (kind === "none" ? String(+(+v).toPrecision(5)) : o.sci ? dkU.sci(kind, v, 4) : dkU.fmt(kind, v, o.dec ?? 3)));
      if (o.unit == null) r.unit.textContent = unitLabel(kind);
    };
    f.check = () => "";
    f.render();
    return r.row;
  };

  /** Editable rows (pattern × factor, T × Sa, …). `rows` is edited IN PLACE
      only after the user touches the table (F.tableDirty(t)). columns:
      [{key, label, type: "select"|"num"|"text", kind, options:()=>[[v,l]], width}].
      opts: addLabel, newRow() → row|null, minRows, onChange(rows), id,
      empty (text when no rows), validateRow(r, i) → message. */
  F.table = (rows, columns, o = {}) => {
    const wrap = el("div", { class: "dk-table-wrap" + (o.cls ? " " + o.cls : ""), id: o.id || null });
    const err = el("div", { class: "dk-err", role: "alert" });
    const f = reg({ type: "table", rows, rowEl: wrap, errEl: err, o, dirty: false, input: null });
    const tbl = el("table", { class: "dk-table" });
    const addBtn = o.newRow ? el("button", { type: "button", class: "btn btn-small dk-add", id: o.id ? o.id + "Add" : null }, [o.addLabel || "+ Add row"]) : null;
    const touch = () => { f.dirty = true; markTouched(); o.onChange && o.onChange(rows); };
    f.render = () => {
      tbl.textContent = "";
      const head = el("tr", {}, columns.map(c => el("th", { class: c.type === "num" ? "num" : "txt", style: c.width ? `width:${c.width}` : null },
        [c.label + (c.kind && unitLabel(c.kind) ? ` (${unitLabel(c.kind)})` : "")])));
      head.appendChild(el("th", { class: "dk-x-col" }));
      tbl.appendChild(el("thead", {}, [head]));
      const tb = el("tbody");
      rows.forEach((r, i) => {
        const tr = el("tr", { "data-row": String(i) });
        for (const c of columns) {
          let ctl;
          if (c.type === "select") {
            ctl = el("select", { class: "dk-select", "aria-label": c.label });
            const opts = typeof c.options === "function" ? c.options(r, i) : c.options;
            let found = false;
            for (const [v, lbl] of opts) { const op = el("option", { value: String(v) }, [lbl]); if (String(v) === String(r[c.key])) { op.selected = true; found = true; } ctl.appendChild(op); }
            if (!found && r[c.key] != null) ctl.insertBefore(el("option", { value: String(r[c.key]), selected: true }, [String(r[c.key])]), ctl.firstChild);
            ctl.addEventListener("change", () => { r[c.key] = ctl.value; touch(); f.render(); });
          } else if (c.type === "num") {
            ctl = el("input", { type: "text", inputmode: "decimal", class: "dk-num", "aria-label": c.label + (unitLabel(c.kind) ? ` (${unitLabel(c.kind)})` : "") });
            ctl.value = showNum(c.kind, r[c.key]);
            ctl.addEventListener("change", () => {
              const v = parseNum(c.kind, ctl.value);
              if (!isFinite(v)) { ctl.classList.add("is-invalid"); err.textContent = `Row ${i + 1}: ${c.label} must be a number.`; return; }
              ctl.classList.remove("is-invalid");
              r[c.key] = v; touch(); f.validate();
            });
          } else if (c.type === "text") {
            ctl = el("input", { type: "text", class: "dk-text", "aria-label": c.label });
            ctl.value = r[c.key] == null ? "" : String(r[c.key]);
            ctl.addEventListener("change", () => { r[c.key] = ctl.value.trim(); touch(); f.validate(); });
          } else {
            ctl = el("span", { class: "dk-cell-ro", text: typeof c.get === "function" ? c.get(r, i) : String(r[c.key] ?? "") });
          }
          tr.appendChild(el("td", { class: c.type === "num" ? "num" : "txt" }, [ctl]));
        }
        const x = el("button", { type: "button", class: "dk-x", title: "Remove row", "aria-label": "Remove row " + (i + 1) }, ["×"]);
        x.disabled = rows.length <= (o.minRows || 0) || !!o.readonly;
        x.addEventListener("click", () => { rows.splice(i, 1); touch(); f.render(); f.validate(); });
        tr.appendChild(el("td", { class: "dk-x-col" }, [x]));
        tb.appendChild(tr);
      });
      tbl.appendChild(tb);
      wrap.querySelectorAll(".dk-empty").forEach(n => n.remove());
      if (!rows.length && o.empty) wrap.insertBefore(el("p", { class: "muted dk-empty", html: o.empty }), err);
      if (addBtn) { const nr = o.canAdd ? o.canAdd(rows) : true; addBtn.disabled = !nr || !!o.readonly; }
    };
    f.validate = () => {
      let msg = "";
      if (o.minRows && rows.length < o.minRows) msg = o.minMsg || `Add at least ${o.minRows} row${o.minRows > 1 ? "s" : ""}.`;
      if (!msg && o.validateRow) for (let i = 0; i < rows.length && !msg; i++) msg = o.validateRow(rows[i], i) || "";
      if (!msg && o.validate) msg = o.validate(rows) || "";
      err.textContent = msg;
      wrap.classList.toggle("is-invalid", !!msg);
      f.error = msg;
      return msg;
    };
    f.check = () => f.validate();
    if (addBtn) addBtn.addEventListener("click", () => { const nr = o.newRow(rows); if (nr) { rows.push(nr); touch(); f.render(); f.validate(); } });
    wrap.append(el("div", { class: "dk-table-scroll" }, [tbl]));
    if (addBtn) wrap.appendChild(el("div", { class: "dk-table-acts" }, [addBtn, o.extraActions || null]));
    wrap.appendChild(err);
    f.setErr = m => { err.textContent = m || ""; f.error = m || ""; };
    f.render();
    return wrap;
  };
  F.tableDirty = node => { const f = fields.find(x => x.rowEl === node); return !!(f && f.dirty); };

  /** Re-render every field (units changed / a dependent value changed). */
  F.refresh = () => { for (const f of fields) if (f.rowEl.isConnected || true) f.render(); };
  /** Validate every field (visible or inside a closed tab / Advanced).
      Returns the first invalid field (or null). */
  F.validate = () => {
    let first = null;
    for (const f of fields) {
      if (!f.rowEl.isConnected) continue;
      if (f.rowEl.closest(".dk-disabled")) { if (f.type !== "table") setErr(f, ""); continue; }
      const msg = f.check ? f.check() : "";
      if (f.type !== "table") setErr(f, msg);
      if (msg && !first) first = f;
    }
    return first;
  };
  F.forget = node => { for (let i = fields.length - 1; i >= 0; i--) if (node.contains(fields[i].rowEl)) fields.splice(i, 1); };
  return F;
}

/* ================================================================
   Layout helpers
   ================================================================ */
/** <fieldset> group with a legend; cols = 2 lays rows out side by side. */
export function dkGroup(legend, kids, { cols = 1, id, cls = "", note } = {}) {
  const fs = el("fieldset", { class: "dk-group" + (cols > 1 ? " dk-cols" : "") + (cls ? " " + cls : ""), id: id || null });
  if (legend) fs.appendChild(el("legend", {}, [legend]));
  if (note) fs.appendChild(el("p", { class: "muted dk-note", html: note }));
  (Array.isArray(kids) ? kids : [kids]).forEach(k => k && fs.appendChild(k));
  return fs;
}

/** Collapsed "Advanced" section. `countFn` → how many values differ from
    their default (shown in the summary so nothing is hidden silently). */
export function dkAdvanced(kids, { summary = "Advanced", note, countFn, open = false, id } = {}) {
  const d = el("details", { class: "dk-adv", id: id || null });
  if (open) d.open = true;
  const badge = el("span", { class: "dk-adv-badge" });
  d.appendChild(el("summary", {}, [el("span", { class: "dk-adv-title", text: summary }), badge]));
  const body = el("div", { class: "dk-adv-body" });
  if (note) body.appendChild(el("p", { class: "muted dk-note", html: note }));
  (Array.isArray(kids) ? kids : [kids]).forEach(k => k && body.appendChild(k));
  d.appendChild(body);
  d.refreshBadge = () => {
    if (!countFn) { badge.textContent = "defaults shown inside"; return; }
    const n = countFn();
    badge.textContent = n ? `${n} changed from default` : "all at defaults";
    badge.classList.toggle("is-changed", !!n);
  };
  d.refreshBadge();
  return d;
}

/** Tab strip + panels. tabs: [{id, label, body: Node | () => Node}]. */
export function dkTabs(tabs, { active, onSwitch, id } = {}) {
  const wrap = el("div", { class: "dk-tabs-wrap", id: id || null });
  const bar = el("div", { class: "dk-tabs", role: "tablist" });
  const panels = el("div", { class: "dk-panels" });
  const btns = {}, pans = {};
  let cur = active || (tabs[0] && tabs[0].id);
  const show = tid => {
    cur = tid;
    for (const t of tabs) {
      btns[t.id].classList.toggle("is-active", t.id === tid);
      btns[t.id].setAttribute("aria-selected", t.id === tid ? "true" : "false");
      btns[t.id].tabIndex = t.id === tid ? 0 : -1;
      pans[t.id].hidden = t.id !== tid;
    }
    onSwitch && onSwitch(tid);
  };
  tabs.forEach((t, i) => {
    const b = el("button", { type: "button", class: "dk-tab", role: "tab", id: (id || "dk") + "-tab-" + t.id, "data-tab": t.id }, [t.label]);
    b.addEventListener("click", () => show(t.id));
    b.addEventListener("keydown", e => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      e.preventDefault();
      const j = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
      show(tabs[j].id); btns[tabs[j].id].focus();
    });
    btns[t.id] = b; bar.appendChild(b);
    const p = el("div", { class: "dk-panel", role: "tabpanel", "aria-labelledby": b.id, "data-panel": t.id });
    p.appendChild(typeof t.body === "function" ? t.body() : t.body);
    pans[t.id] = p; panels.appendChild(p);
  });
  wrap.append(bar, panels);
  show(cur);
  wrap.show = show;
  wrap.current = () => cur;
  wrap.panelOf = node => { for (const [k, p] of Object.entries(pans)) if (p.contains(node)) return k; return null; };
  wrap.mark = (tid, bad) => btns[tid] && btns[tid].classList.toggle("has-error", !!bad);
  return wrap;
}

/** ETABS "Define …" list: table on the left, "Click to:" buttons on the
    right. columns: [{label, get(item) → text, cls}]. actions: [{label, id,
    fn(sel), needsSel, disabled(sel) }]. items(): [{key, …}]. */
export function dkList({ items, columns, actions = [], selected = null, onOpen, empty = "Nothing defined yet.", id, extraSide, rowCls } = {}) {
  let sel = selected;
  const wrap = el("div", { class: "dk-list", id: id || null });
  const tWrap = el("div", { class: "dk-list-table table-scroll" });
  const side = el("div", { class: "dk-list-side" }, [el("div", { class: "dk-side-title", text: "Click to:" })]);
  const btns = actions.map(a => {
    if (a.sep) { side.appendChild(el("div", { class: "dk-side-title", text: a.sep })); return null; }
    const b = el("button", { type: "button", class: "btn btn-small dk-side-btn", id: a.id || null, title: a.title || null }, [a.label]);
    b.addEventListener("click", () => a.fn(sel));
    side.appendChild(b);
    return [a, b];
  }).filter(Boolean);
  if (extraSide) side.appendChild(extraSide);
  wrap.append(tWrap, side);
  const render = () => {
    const list = items();
    if (sel != null && !list.some(it => it.key === sel)) sel = list.length ? list[0].key : null;
    if (sel == null && list.length) sel = list[0].key;
    tWrap.textContent = "";
    if (!list.length) tWrap.appendChild(el("p", { class: "muted dk-empty", html: empty }));
    else {
      const t = el("table", { class: "data-table dk-list-tbl" });
      t.appendChild(el("thead", {}, [el("tr", {}, columns.map(c => el("th", { class: "txt" }, [c.label])))]));
      const tb = el("tbody");
      for (const it of list) {
        const tr = el("tr", { class: (it.key === sel ? "is-sel" : "") + (rowCls ? " " + (rowCls(it) || "") : ""), "data-key": it.key, tabindex: it.key === sel ? "0" : "-1" },
          columns.map(c => el("td", { class: "txt " + (c.cls || "") }, [String(c.get(it) ?? "")])));
        tr.addEventListener("click", () => { sel = it.key; render(); });
        tr.addEventListener("dblclick", () => { sel = it.key; onOpen && onOpen(sel); });
        tr.addEventListener("keydown", e => {
          const i = list.findIndex(x => x.key === sel);
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            const j = Math.max(0, Math.min(list.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)));
            sel = list[j].key; render();
            const n = tWrap.querySelector(`tr[data-key="${CSS.escape(sel)}"]`); if (n) n.focus();
          }
        });
        tb.appendChild(tr);
      }
      t.appendChild(tb);
      tWrap.appendChild(t);
    }
    for (const [a, b] of btns) b.disabled = (a.needsSel && sel == null) || (a.disabled ? !!a.disabled(sel) : false);
  };
  wrap.render = render;
  wrap.selected = () => sel;
  wrap.select = k => { sel = k; render(); };
  render();
  return wrap;
}

/** Small read-only key/value grid (Info dialogs). rows: [[label, value, unit?]]. */
export function dkInfo(rows) {
  const t = el("table", { class: "dk-info" });
  for (const [k, v, u] of rows) t.appendChild(el("tr", {}, [el("th", { text: k }), el("td", { text: v == null ? "—" : String(v) }), el("td", { class: "dk-unit", text: u || "" })]));
  return t;
}

export function dkNote(html, cls = "") { return el("p", { class: "muted dk-note " + cls, html }); }

/* ================================================================
   Dialog shell
   ================================================================ */
const stack = [];
export const dkTop = () => stack[stack.length - 1] || null;
export const dkOpenIds = () => stack.map(s => s.id);
export function dkClose(id) { const d = stack.find(s => s.id === id); if (d) d.close(); }
export const dkIsOpen = id => stack.some(s => s.id === id);

/** Open a standard dialog.
    opts: id, title, iconId (icons.js name) | glyph (svg path data), size
    ("sm" | "md" | "lg"), intro (html), body (Node | Node[]), form (dkForm),
    tabs (the dkTabs node, for error focus), footNote, extra (left-side
    footer buttons), okLabel, onOk() → true/undefined to close, false or a
    message to stay open, onApply() (adds Apply), onCancel(), onClose(),
    cancelLabel, noCancel (single "Close" button). */
export function dkDialog(o) {
  ensureCss();
  dkClose(o.id);
  const back = el("div", { class: "modal-backdrop sky-dlg dk-dlg", id: o.id });
  const modal = el("div", { class: "modal dk-modal dk-" + (o.size || "md"), role: "dialog", "aria-modal": "true", "aria-labelledby": o.id + "Title" });
  const ico = o.iconId ? dkIcon(o.iconId, "dlg-ico") : (o.glyph ? `<svg class="dlg-ico" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${o.glyph}</svg>` : "");
  const head = el("header", { class: "modal-head" }, [el("h2", { id: o.id + "Title", class: "dlg-title", html: ico + `<span>${esc(o.title)}</span>` })]);
  const x = el("button", { type: "button", class: "icon-btn", title: "Close (Esc)", "aria-label": "Close " + o.title }, ["×"]);
  head.appendChild(x);
  const body = el("div", { class: "modal-body dk-body" });
  if (o.intro) body.appendChild(el("p", { class: "muted dk-intro", html: o.intro }));
  (Array.isArray(o.body) ? o.body : [o.body]).forEach(n => n && body.appendChild(n));
  const formErr = el("div", { class: "dk-form-err", role: "alert", "aria-live": "assertive" });
  body.appendChild(formErr);

  const note = el("span", { class: "muted dk-foot-note", html: o.footNote || "" });
  const left = el("div", { class: "dk-foot-left" }, [note, ...(o.extra || [])]);
  const btns = el("div", { class: "modal-btns dk-btns" });
  const okB = el("button", { type: "button", class: "btn btn-run dk-ok", id: o.id + "Ok", title: (o.okLabel || "OK") + " (Enter)" }, [o.okLabel || "OK"]);
  const cancelB = el("button", { type: "button", class: "btn dk-cancel", id: o.id + "Cancel", title: (o.noCancel ? "Close" : (o.cancelLabel || "Cancel")) + " (Esc)" }, [o.noCancel ? "Close" : (o.cancelLabel || "Cancel")]);
  const applyB = o.onApply ? el("button", { type: "button", class: "btn dk-apply", id: o.id + "Apply", title: "Apply without closing" }, ["Apply"]) : null;
  if (o.noCancel && !o.onOk) btns.append(cancelB);
  else btns.append(...[okB, cancelB, applyB].filter(Boolean));
  const foot = el("footer", { class: "modal-foot dk-foot" }, [left, btns]);
  modal.append(head, body, foot);
  back.appendChild(modal);
  (document.getElementById("app") || document.body).appendChild(back);

  const F = o.form;
  const unsubUnits = dkU.onUnitsChange ? dkU.onUnitsChange(() => { F && F.refresh(); o.onUnits && o.onUnits(); }) : null;
  const entry = { id: o.id, el: back };
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    const i = stack.indexOf(entry);
    if (i >= 0) stack.splice(i, 1);
    unsubUnits && unsubUnits();
    back.remove();
    o.onClose && o.onClose();
  };
  entry.close = close;
  stack.push(entry);

  const setFormErr = msg => { formErr.textContent = msg || ""; formErr.classList.toggle("is-on", !!msg); };
  /** Validate the form; on error focus the first bad field. */
  const runValidate = () => {
    if (!F) return true;
    const bad = F.validate();
    if (o.tabs && o.tabs.mark) {
      for (const p of o.tabs.querySelectorAll(".dk-panel")) o.tabs.mark(p.dataset.panel, !!p.querySelector(".is-invalid"));
    }
    if (!bad) return true;
    const node = bad.input || bad.rowEl;
    if (o.tabs && o.tabs.panelOf) { const t = o.tabs.panelOf(bad.rowEl); if (t && t !== o.tabs.current()) o.tabs.show(t); }
    const adv = bad.rowEl.closest("details");
    if (adv && !adv.open) adv.open = true;
    setFormErr("Fix the highlighted field" + (bad.o && bad.o.label ? ` (${bad.o.label})` : "") + " before pressing OK.");
    try { node.focus({ preventScroll: false }); } catch { /* ignore */ }
    return false;
  };
  const doOk = async () => {
    // commit the field being typed in (inputs read on "change")
    const ae = document.activeElement;
    if (ae && back.contains(ae) && ae.matches("input, select")) ae.blur();
    setFormErr("");
    if (!runValidate()) return;
    if (o.onOk) {
      const r = await o.onOk();
      if (r === false) return;
      if (typeof r === "string" && r) { setFormErr(r); return; }
    }
    close();
  };
  const doCancel = () => { o.onCancel && o.onCancel(); close(); };
  okB.addEventListener("click", doOk);
  cancelB.addEventListener("click", o.noCancel && o.onOk ? doOk : doCancel);
  if (applyB) applyB.addEventListener("click", () => {
    const ae = document.activeElement;
    if (ae && back.contains(ae) && ae.matches("input, select")) ae.blur();
    setFormErr("");
    if (!runValidate()) return;
    const r = o.onApply();
    if (typeof r === "string" && r) setFormErr(r);
  });
  x.addEventListener("click", doCancel);
  back.addEventListener("mousedown", e => { if (e.target === back) doCancel(); });
  // Enter = OK / Esc = Cancel, handled here (preventDefault tells the global
  // ux handler the dialog took care of it)
  back.addEventListener("keydown", e => {
    if (dkTop() !== entry || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
    if (e.key === "Escape") {
      if (back.querySelector(".ux-help-pop")) return;
      e.preventDefault(); e.stopPropagation(); doCancel();
    } else if (e.key === "Enter") {
      const t = e.target;
      if (t.closest("textarea, button, a, summary, .dk-table-wrap, [data-no-enter]")) return;
      e.preventDefault(); e.stopPropagation();
      if (o.noCancel && !o.onOk) return;
      doOk();
    }
  });
  requestAnimationFrame(() => {
    if (closed) return;
    const first = body.querySelector("input:not([readonly]):not([disabled]):not([type=checkbox]):not([type=radio]), select:not([disabled]), .dk-list tr.is-sel");
    try { (first || okB).focus({ preventScroll: true }); } catch { /* ignore */ }
  });
  return { el: back, modal, body, foot, ok: okB, cancel: cancelB, apply: applyB, close, setNote: h => { note.innerHTML = h; }, setError: setFormErr, validate: runValidate };
}

/** Plain message / confirm dialog in the standard shell. */
export function dkConfirm({ id = "dkConfirm", title = "Confirm", html, okLabel = "OK" }) {
  return new Promise(resolve => {
    let done = false;
    dkDialog({ id, title, size: "sm", body: el("p", { class: "dk-msg", html }), okLabel,
      onOk: () => { done = true; resolve(true); }, onClose: () => { if (!done) resolve(false); } });
  });
}

/** Unique name helper: base, base2, base3 … not in `taken` (Set | object). */
export function uniqueName(taken, base) {
  const has = n => taken instanceof Set ? taken.has(n) : Object.prototype.hasOwnProperty.call(taken || {}, n);
  if (!has(base)) return base;
  let i = 2;
  while (has(base + i)) i++;
  return base + i;
}

export default { el, esc, clone, isNum, dkForm, dkGroup, dkAdvanced, dkTabs, dkList, dkInfo, dkNote, dkDialog, dkConfirm, dkDraft, dkCommit, dkRefresh, unitLabel, showNum, uniqueName, dkClose, dkIsOpen, dkTop };
