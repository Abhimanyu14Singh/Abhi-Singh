/* SkyFrame — PT tendons: cross-reference helpers used by js/modeledit.js
   (pattern / static-case rename + delete guards, erase of host objects).
   Pure functions over the model dict; no imports (modeledit.js imports this
   file, so it must not import modeledit.js back). CONTRACT section
   "Post-tensioning tendons (as loads) and hyperstatic case". */

const tendonsOf = m => (Array.isArray(m && m.tendons) ? m.tendons : []);
const hypOf = m => (m && m.hyperstatic_cases && typeof m.hyperstatic_cases === "object") ? m.hyperstatic_cases : {};

/** Labels of tendons writing into load pattern `name` (blocks its delete). */
export function ptPatternRefs(model, name) {
  return tendonsOf(model).filter(t => t && t.pattern === name).map(t => `tendon ${t.uid}`);
}

/** Pattern rename: tendons follow. */
export function ptRenamePattern(model, oldName, newName) {
  for (const t of tendonsOf(model)) if (t && t.pattern === oldName) t.pattern = newName;
}

/** Labels of hyperstatic cases built on static case `name` (blocks its delete). */
export function ptCaseRefs(model, name) {
  return Object.entries(hypOf(model)).filter(([, h]) => h && h.case === name).map(([n]) => `hyperstatic ${n}`);
}

/** Static-case rename: hyperstatic cases follow. */
export function ptRenameCase(model, oldName, newName) {
  for (const h of Object.values(hypOf(model))) if (h && h.case === oldName) h.case = newName;
}

/** A host member / shell was erased: drop the tendons it hosted, then any
    hyperstatic case whose static case no longer applies a tendon pattern
    (the backend would reject both). */
export function ptOnObjectErased(model, ref) {
  if (!model || !Array.isArray(model.tendons) || !ref) return;
  const uid = ref.uid;
  const host = t => (Array.isArray(t.host) ? t.host : [t.host]);
  const before = model.tendons.length;
  model.tendons = model.tendons.filter(t => !(t && host(t).includes(uid)));
  if (model.tendons.length === before) return;
  if (!model.tendons.length) delete model.tendons;
  const pats = new Set(tendonsOf(model).map(t => t.pattern));
  for (const [n, h] of Object.entries(hypOf(model))) {
    const c = (model.cases || {})[h && h.case];
    if (c && Object.keys(c.patterns || {}).some(p => pats.has(p))) continue;
    delete model.hyperstatic_cases[n];
    if (Array.isArray(model.cases_not_run)) model.cases_not_run = model.cases_not_run.filter(x => x !== n);
  }
  if (model.hyperstatic_cases && !Object.keys(model.hyperstatic_cases).length) delete model.hyperstatic_cases;
}
