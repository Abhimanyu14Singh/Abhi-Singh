/* SkyFrame — named mass sources / modal cases: pure model-reference helpers
   (CONTRACT "Time-history functions, mass sources, modal cases, Ritz basis
   and FNA breadth"). No DOM. Used by js/modalx.js and by two hook lines in
   js/modeledit.js (pattern rename / pattern references). */

export const MX_ACCEL_DIRS = ["UX", "UY", "UZ", "RX", "RY", "RZ"];
export const MX_DEFAULT_STARTS = [{ type: "accel", dir: "UX" }, { type: "accel", dir: "UY" }];
export const MX_MASS_OPTS = ["self_mass", "patterns", "include_lateral", "include_vertical", "lump_at_stories"];
export const MX_MASS_DEFAULTS = { self_mass: true, patterns: true, include_lateral: true, include_vertical: false, lump_at_stories: true };

/** Normalised modal case (same rules as core.modal_cases.normalize_modal_case). */
export function mxNormCase(c) {
  const t = (c && c.type) || "eigen";
  const out = { type: t, num_modes: c && c.num_modes != null ? c.num_modes : 12, mass_source: (c && c.mass_source) || "", stiffness_case: (c && c.stiffness_case) || null };
  if (t === "ritz") {
    out.ritz_starts = JSON.parse(JSON.stringify((c && c.ritz_starts) || MX_DEFAULT_STARTS));
    out.max_cycles = c && c.max_cycles != null ? c.max_cycles : 0;
  }
  return out;
}

/** Normalised named mass source. */
export function mxNormSource(s) {
  const o = (s && s.options) || {};
  return {
    mass_source: { ...((s && s.mass_source) || {}) },
    mode: (s && s.mode) || "weight",
    options: Object.fromEntries(MX_MASS_OPTS.map(k => [k, o[k] !== undefined ? !!o[k] : MX_MASS_DEFAULTS[k]])),
  };
}

/** Modal cases using a mass source. */
export function mxSourceRefs(model, name) {
  return Object.entries(model.modal_cases || {}).filter(([, c]) => (c.mass_source || "") === name).map(([n]) => n);
}

/** RS / TH cases using a modal case ("MODAL" = the default: cases without one). */
export function mxModalRefs(model, name) {
  const out = [];
  for (const k of ["rs_cases", "th_cases"])
    for (const [n, c] of Object.entries(model[k] || {}))
      if ((c.modal_case || "MODAL") === name) out.push(n);
  return out;
}

/** Pattern references held by named mass sources / Ritz starts (for
    modeledit.patternRefs: a referenced pattern cannot be deleted). */
export function mxPatternRefs(model, name) {
  const out = [];
  for (const [n, s] of Object.entries(model.mass_sources || {}))
    if ((s.mass_source || {})[name] !== undefined) out.push(`mass source ${n}`);
  for (const [n, c] of Object.entries(model.modal_cases || {}))
    if ((c.ritz_starts || []).some(st => st.type === "pattern" && st.name === name)) out.push(`modal case ${n}`);
  return out;
}

/** Follow a pattern rename (modeledit.renamePattern hook). */
export function mxRenamePattern(model, oldName, newName) {
  for (const s of Object.values(model.mass_sources || {})) {
    const p = s.mass_source || {};
    if (p[oldName] !== undefined) s.mass_source = Object.fromEntries(Object.entries(p).map(([k, v]) => [k === oldName ? newName : k, v]));
  }
  for (const c of Object.values(model.modal_cases || {}))
    for (const st of c.ritz_starts || []) if (st.type === "pattern" && st.name === oldName) st.name = newName;
}

/** Rename a modal case (RS / TH references follow). */
export function mxRenameModalCase(model, oldName, newName) {
  const mc = model.modal_cases || {};
  if (!mc[oldName] || mc[newName]) return false;
  model.modal_cases = Object.fromEntries(Object.entries(mc).map(([k, v]) => [k === oldName ? newName : k, v]));
  for (const k of ["rs_cases", "th_cases"])
    for (const c of Object.values(model[k] || {})) if (c.modal_case === oldName) c.modal_case = newName;
  if (Array.isArray(model.cases_not_run)) model.cases_not_run = model.cases_not_run.map(n => n === oldName ? newName : n);
  return true;
}

/** Rename a named mass source (modal case references follow). */
export function mxRenameSource(model, oldName, newName) {
  const ms = model.mass_sources || {};
  if (!ms[oldName] || ms[newName]) return false;
  model.mass_sources = Object.fromEntries(Object.entries(ms).map(([k, v]) => [k === oldName ? newName : k, v]));
  for (const c of Object.values(model.modal_cases || {})) if (c.mass_source === oldName) c.mass_source = newName;
  return true;
}

/** Every case / combo name (modal case names must be unique among them). */
export function mxTakenNames(model) {
  const s = new Set(["MODAL"]);
  for (const k of ["cases", "rs_cases", "th_cases", "pushover_cases", "staged_cases", "buckling_cases", "steady_state_cases",
    "psd_cases", "nonlinear_static_cases", "hyperstatic_cases", "combos", "modal_cases"])
    for (const n of Object.keys(model[k] || {})) s.add(n);
  return s;
}

/** Ritz starts → short text ("UX, UY, DEAD, links"). */
export const mxStartsText = starts => (starts || []).map(s => s.type === "accel" ? s.dir : s.type === "pattern" ? `pattern ${s.name}` : "link DOFs").join(", ");
