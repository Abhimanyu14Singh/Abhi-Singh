/* SkyFrame — pure model helpers for the extended load combinations,
   model-wide P-Delta options and multi-component time histories
   (CONTRACT "Load combinations: RS/TH/nested members and ABS/SRSS/Range
   types", "P-Delta options (model-wide)", "Multi-component and load-pattern
   time histories"). NO imports: modeledit.js, loads.js, app.js, mock and the
   dialogs (js/combodlg.js) all share these without import cycles. */

export const COMBO_TYPES = ["add", "envelope", "abs", "srss", "range"];
export const COMBO_TYPE_LABEL = {
  add: "Linear Add", envelope: "Envelope", abs: "Absolute Add", srss: "SRSS", range: "Range Add",
};
export const COMBO_TYPE_SHORT = { add: "add", envelope: "env", abs: "abs", srss: "SRSS", range: "range" };
export const normComboType = t => COMBO_TYPES.includes(t) ? t : "add";

export const MEMBER_KIND_LABEL = {
  static: "Linear Static", rs: "Response Spectrum", rs_combo: "RS Directional",
  th: "Time History", staged: "Staged Construction", combo: "Combination",
};
/* Kinds that may NOT enter a combo (backend ValueError) — never offered. */
export const REJECTED_KINDS = ["pushover", "buckling", "steady_state", "psd", "modal"];

/** Member lookup in the backend order (first match wins). */
export function memberKind(m, name) {
  if (!m || name == null) return null;
  if ((m.cases || {})[name]) return "static";
  if ((m.rs_cases || {})[name]) return "rs";
  if ((m.rs_combos || {})[name]) return "rs_combo";
  if ((m.th_cases || {})[name]) return "th";
  if ((m.staged_cases || {})[name]) return "staged";
  if ((m.combos || {})[name]) return "combo";
  if (name === "MODAL") return "modal";
  if ((m.pushover_cases || {})[name]) return "pushover";
  if ((m.buckling_cases || {})[name]) return "buckling";
  if ((m.steady_state_cases || {})[name]) return "steady_state";
  if ((m.psd_cases || {})[name]) return "psd";
  return null;
}

/** Every name a combo may reference: [{name, kind}] in lookup order;
    `except` (the combo being edited) is left out. */
export function comboMemberPool(m, except) {
  const out = [], seen = new Set();
  const push = (dict, kind) => {
    for (const n of Object.keys(dict || {}))
      if (n !== except && !seen.has(n)) { seen.add(n); out.push({ name: n, kind }); }
  };
  push(m.cases, "static");
  push(m.rs_cases, "rs");
  push(m.rs_combos, "rs_combo");
  push(m.th_cases, "th");
  push(m.staged_cases, "staged");
  push(m.combos, "combo");
  return out;
}

/** First cycle reached from combo `name` when its members are `cases`
    (defaults to the stored ones): ["A", "C", "B", "A"] or null. */
export function findComboCycle(m, name, cases) {
  const combos = m.combos || {};
  const membersOf = n => n === name ? (cases || (combos[n] || {}).cases || {}) : ((combos[n] || {}).cases || {});
  const isCombo = n => n === name || (!!combos[n] && memberKind(m, n) === "combo");
  const path = [], onPath = new Set(), done = new Set();
  const dfs = n => {
    path.push(n); onPath.add(n);
    for (const c of Object.keys(membersOf(n))) {
      if (!isCombo(c)) continue;
      if (onPath.has(c)) return [...path.slice(path.indexOf(c)), c];
      if (done.has(c)) continue;
      const r = dfs(c);
      if (r) return r;
    }
    path.pop(); onPath.delete(n); done.add(n);
    return null;
  };
  return dfs(name);
}

/** Error message mirroring the backend validation, or "". */
export function validateCombo(m, name, cb) {
  const type = cb.combo_type || "add";
  if (!COMBO_TYPES.includes(type)) return `Combo ${name}: unknown type ${type}`;
  const cases = cb.cases || {};
  const names = Object.keys(cases);
  if (type !== "add" && !names.length) return `Combo ${name}: ${COMBO_TYPE_LABEL[type]} needs at least one member`;
  for (const n of names) {
    if (n === name) return `Combo ${name}: cannot reference itself`;
    const k = memberKind(m, n);
    if (!k) return `Combo ${name}: unknown case ${n}`;
    if (REJECTED_KINDS.includes(k)) return `Combo ${name}: ${k} case '${n}' cannot enter a load combo`;
    if (!(typeof cases[n] === "number" && isFinite(cases[n]))) return `Combo ${name}: factor of ${n} must be a finite number`;
  }
  const cyc = findComboCycle(m, name, cases);
  if (cyc) return `Combo ${name}: circular combo reference ${cyc.join(" -> ")}`;
  return "";
}

/** True when the combo produces a max/min pair (backend "min" block). */
export function isMaxMinCombo(m, name, _seen) {
  const cb = (m.combos || {})[name];
  if (!cb) return false;
  if ((cb.combo_type || "add") !== "add") return true;
  const seen = _seen || new Set();
  if (seen.has(name)) return false;
  seen.add(name);
  return Object.keys(cb.cases || {}).some(n => {
    const k = memberKind(m, n);
    return k === "rs" || k === "rs_combo" || k === "th" || (k === "combo" && isMaxMinCombo(m, n, seen));
  });
}

/** Case-select text for a combo: "NAME · SRSS" (Linear Add keeps the bare name). */
export function comboOptionLabel(m, name) {
  const cb = m && (m.combos || {})[name];
  const t = cb ? (cb.combo_type || "add") : "add";
  if (t === "add") return m && isMaxMinCombo(m, name) ? `${name} · add (max/min)` : name;
  return `${name} · ${COMBO_TYPE_LABEL[t]}`;
}

/* ---------------- reference maintenance (renames / deletes) */
export function comboMemberRename(m, oldName, newName) {
  for (const cb of Object.values((m && m.combos) || {})) {
    const c = cb.cases;
    if (!c || c[oldName] === undefined || oldName === newName) continue;
    // rebuild in place so the member order is kept
    const ent = Object.entries(c).map(([k, v]) => [k === oldName ? newName : k, v]);
    for (const k of Object.keys(c)) delete c[k];
    for (const [k, v] of ent) c[k] = v;
  }
}
export function comboMemberDelete(m, name) {
  for (const cb of Object.values((m && m.combos) || {}))
    if (cb.cases && cb.cases[name] !== undefined) delete cb.cases[name];
}
/** Combos referencing `name` (any member kind). */
export function comboMemberRefs(m, name) {
  return Object.values((m && m.combos) || {})
    .filter(cb => (cb.cases || {})[name] !== undefined).map(cb => cb.name);
}

/** Pattern references held by P-Delta options and TH load-pattern components. */
export function patternRefsExtra(m, name) {
  const out = [];
  const po = m && m.pdelta_options;
  if (po && po.load_factors && po.load_factors[name] !== undefined) out.push("P-Delta options");
  for (const tc of Object.values((m && m.th_cases) || {}))
    if ((tc.components || []).some(c => c && c.pattern === name)) out.push(`TH ${tc.name}`);
  return out;
}
export function patternRefRename(m, oldName, newName) {
  const po = m && m.pdelta_options;
  if (po && po.load_factors && po.load_factors[oldName] !== undefined) {
    const ent = Object.entries(po.load_factors).map(([k, v]) => [k === oldName ? newName : k, v]);
    po.load_factors = Object.fromEntries(ent);
  }
  for (const tc of Object.values((m && m.th_cases) || {}))
    for (const c of tc.components || []) if (c && c.pattern === oldName) c.pattern = newName;
}
export function thFunctionRefsExtra(m, name) {
  return Object.values((m && m.th_cases) || {})
    .filter(tc => (tc.components || []).some(c => c && c.function === name)).map(tc => tc.name);
}
export function thFunctionRefRename(m, oldName, newName) {
  for (const tc of Object.values((m && m.th_cases) || {}))
    for (const c of tc.components || []) if (c && c.function === oldName) c.function = newName;
}

/* ---------------- P-Delta options */
export const PDELTA_DEFAULTS = { method: "none", load_factors: {}, max_iterations: 2, tolerance: 0.001, include_in: "all_linear" };
export const PDELTA_METHOD_LABEL = {
  none: "None", non_iterative_mass: "Non-iterative — based on mass", iterative_loads: "Iterative — based on loads",
};
export function pdeltaOptions(m) {
  const po = (m && m.pdelta_options) || {};
  return {
    method: po.method || PDELTA_DEFAULTS.method,
    load_factors: { ...(po.load_factors || {}) },
    max_iterations: po.max_iterations ?? PDELTA_DEFAULTS.max_iterations,
    tolerance: po.tolerance ?? PDELTA_DEFAULTS.tolerance,
    include_in: po.include_in || PDELTA_DEFAULTS.include_in,
  };
}
export function pdeltaIsDefault(po) {
  return po.method === "none" && !Object.keys(po.load_factors || {}).length &&
    po.max_iterations === 2 && po.tolerance === 0.001 && po.include_in === "all_linear";
}
