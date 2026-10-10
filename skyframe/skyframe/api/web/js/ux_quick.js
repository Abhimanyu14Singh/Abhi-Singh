/* SkyFrame UX pass 2 — the sidebar quick-model form (UX audit F8).
   The form in the Analyze sidebar always showed quick-building defaults
   ("Quick Building / 3 bays") whatever model was open. Now:
     · it is clearly labelled as a NEW-model generator that replaces the
       working model, with a link to the start-screen templates;
     · until the user edits it, it mirrors the current model: name, bays and
       bay widths (from the primary grid), story count and typical height,
       column / beam sizes and E (from the column / beam sections), base
       fixity — so "Generate" re-creates a regular building like the one on
       screen instead of silently resetting to 3 × 2 bays;
     · "Defaults" restores the original markup values.
   Values are written in display units through js/units.js (data-si keeps the
   SI value authoritative, exactly like units.applyStatic). */

import { el as uqEl } from "./ux_common.js";
import uqU from "./units.js";

const mode = arr => {
  const c = new Map();
  for (const v of arr) { const k = Math.round(v * 1e6) / 1e6; c.set(k, (c.get(k) || 0) + 1); }
  let best = null, n = 0;
  for (const [k, v] of c) if (v > n) { best = k; n = v; }
  return best;
};

/** Quick-form values (SI) that describe model m, or null. */
export function quickValuesOf(m) {
  if (!m || !Array.isArray(m.stories) || !m.stories.length) return null;
  const v = { name: m.name || "Model" };
  const g = m.grid || (Array.isArray(m.grid_systems) && m.grid_systems[0]) || null;
  const bays = lines => {
    const xs = [...(lines || [])].map(Number).filter(isFinite).sort((a, b) => a - b);
    if (xs.length < 2) return null;
    const d = xs.slice(1).map((x, i) => x - xs[i]);
    return { n: d.length, w: mode(d), uneven: d.some(x => Math.abs(x - d[0]) > 1e-6) };
  };
  const bx = g && bays(g.x_lines), by = g && bays(g.y_lines);
  if (bx) { v.bays_x = bx.n; v.bay_width_x = bx.w; }
  if (by) { v.bays_y = by.n; v.bay_width_y = by.w; }
  v.uneven = !!((bx && bx.uneven) || (by && by.uneven));
  v.stories = m.stories.length;
  v.story_height = mode(m.stories.map(s => +s.height).filter(isFinite));
  const secOf = kind => {
    const cnt = new Map();
    for (const mb of m.members || []) if (mb.kind === kind && mb.section) cnt.set(mb.section, (cnt.get(mb.section) || 0) + 1);
    let best = null, n = 0;
    for (const [k, c] of cnt) if (c > n) { best = k; n = c; }
    return best && m.sections ? m.sections[best] : null;
  };
  const col = secOf("column"), beam = secOf("beam");
  if (col && isFinite(col.b) && isFinite(col.h) && Math.abs(col.b - col.h) < 1e-9 && col.b > 0) v.column_size = col.b;
  if (beam && isFinite(beam.b) && beam.b > 0) v.beam_b = beam.b;
  if (beam && isFinite(beam.h) && beam.h > 0) v.beam_h = beam.h;
  const mat = (col && col.material && m.materials && m.materials[col.material]) || null;
  if (mat && isFinite(mat.E) && mat.E > 0) v.E = mat.E;
  if (m.base_fixity === "fixed" || m.base_fixity === "pinned") v.base_fixity = m.base_fixity;
  return v;
}

export function installQuick(sky) {
  const form = document.getElementById("quickForm");
  if (!form) return;
  const S = sky.store;
  const els = form.elements;
  // the markup defaults (SI), captured once
  const defaults = {};
  for (const inp of form.querySelectorAll("input[name], select[name]")) {
    defaults[inp.name] = inp.dataset.uq ? (inp.dataset.si !== undefined ? inp.dataset.si : inp.value) : inp.value;
  }
  let touched = false, source = "defaults";

  /* ---- labelling ---- */
  const side = form.closest(".sidebar-inner");
  const title = side && side.querySelector(".panel-title");
  if (title) { title.textContent = "New Model Generator"; title.title = "Generates a NEW regular building — it replaces the working model"; }
  const srcLine = uqEl("span", { class: "ux-quick-src", id: "uxQuickSrc" });
  const tplBtn = uqEl("button", { type: "button", class: "btn btn-small", id: "uxQuickTemplates", title: "Steel / flat-plate / braced / core templates and recent models (Alt+N)" }, ["Templates…"]);
  const curBtn = uqEl("button", { type: "button", class: "btn btn-small", id: "uxQuickCurrent", title: "Fill the form from the model on screen" }, ["Use current"]);
  const defBtn = uqEl("button", { type: "button", class: "btn btn-small", id: "uxQuickDefaults", title: "Restore the quick-building defaults" }, ["Defaults"]);
  const note = uqEl("div", { class: "ux-quick-note", role: "note" }, [
    uqEl("p", {}, [uqEl("b", { text: "Creates a new model" }), " and replaces the working one (save first to keep it). "]),
    srcLine,
    uqEl("div", { class: "ux-quick-btns" }, [tplBtn, curBtn, defBtn]),
  ]);
  form.before(note);
  const gen = document.getElementById("generateBtn");
  if (gen) { gen.textContent = "Generate New Model"; gen.title = "Replace the working model with a regular building built from these values"; }
  tplBtn.addEventListener("click", () => { if (sky.ux && sky.ux.start) sky.ux.start.open(); else if (sky.fileNew) sky.fileNew(); });
  curBtn.addEventListener("click", () => { touched = false; fill(quickValuesOf(S.model), "model"); });
  defBtn.addEventListener("click", () => { touched = true; fill(defaults, "defaults"); });

  function setVal(name, si) {
    const inp = els[name];
    if (!inp || si == null || si === "") return;
    if (inp.tagName === "SELECT" || inp.type === "text") { inp.value = String(si); return; }
    if (inp.dataset.uq) {
      inp.dataset.si = String(si);
      inp.value = uqU.inputValue(inp.dataset.uq, +si);
    } else inp.value = String(si);
  }
  function fill(v, src) {
    if (!v) return;
    for (const k of Object.keys(defaults)) if (v[k] != null) setVal(k, v[k]);
    source = src;
    sync(v);
  }
  function sync(v) {
    const m = S.model;
    if (source === "model" && m) {
      srcLine.textContent = `Values read from “${m.name || "current model"}”` + (v && v.uneven ? " (uneven grid → typical spacing)." : ".");
    } else if (source === "defaults") srcLine.textContent = "Quick-building default values.";
    else srcLine.textContent = "Your values (edited).";
  }
  form.addEventListener("input", () => { touched = true; source = "user"; sync(); });
  form.addEventListener("change", () => { touched = true; source = "user"; sync(); });
  form.addEventListener("submit", () => { touched = false; }, true);

  const refresh = () => { if (!touched) fill(quickValuesOf(S.model), "model"); };
  document.addEventListener("sky:model-changed", refresh);
  document.addEventListener("sky:units-changed", () => sync());
  refresh();
  if (!S.model) sync();

  sky.ux = sky.ux || {};
  sky.ux.quick = { refresh: () => { touched = false; refresh(); }, valuesOf: quickValuesOf, source: () => source, touched: () => touched };
}
