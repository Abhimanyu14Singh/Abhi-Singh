/* SkyFrame — model-level Undo / Redo history (Edit > Undo / Redo, Ctrl+Z / Ctrl+Y).

   Snapshot diffs: the working model (store.model, SI JSON) is serialised
   PER TOP-LEVEL KEY; an undo entry keeps only the keys an action changed
   ({key: json-before}, {key: json-after}), so drawing one beam on a 10-story
   model stores just the "members" array, not the whole model. Every model
   mutation path funnels through markDirty (→ "sky:model-changed") or, for
   backend-echo adopters, clearDirty (→ "sky:model-synced"); both schedule a
   coalesced checkpoint (one entry per JS task, so a dialog OK that touches
   ten fields is ONE undo step). "sky:model-replaced" (New / Open / Discard)
   resets the history. Capped at CAP entries and MAX_CHARS stored JSON. */

const CAP = 50;
const MAX_CHARS = 60e6;          // ~120 MB of UTF-16 at worst — then oldest dropped
const DRAW_TOOLS = { column: "Draw column", beam: "Draw beam", brace: "Draw brace", wall: "Draw wall",
  slab: "Draw slab", link: "Draw link", spring: "Draw point spring", linespring: "Draw line spring",
  erase: "Erase" };
const KEY_LABEL = {
  patterns: "Edit loads", cases: "Edit load cases", combos: "Edit load combinations",
  rs_cases: "Edit response-spectrum cases", th_cases: "Edit time-history cases",
  pushover_cases: "Edit pushover cases", staged_cases: "Edit staged cases",
  buckling_cases: "Edit buckling cases", sections: "Edit frame sections",
  shell_sections: "Edit shell sections", materials: "Edit materials", groups: "Edit groups",
  stories: "Edit stories", grid: "Edit grid", grid_systems: "Edit grid", section_cuts: "Edit section cuts",
  supports: "Edit supports", spring_supports: "Edit springs", line_springs: "Edit line springs",
  links: "Edit links", diaphragms: "Edit diaphragms", mass_source: "Edit mass source",
  mass_options: "Edit mass source", cases_not_run: "Set load cases to run", active_dof: "Set active DOF",
  display_units: "Change units", tendons: "Edit tendons", pdelta_options: "Edit P-Delta options",
};

const countUids = s => (s ? (s.match(/"uid":/g) || []).length : 0);

export function createHistory(sky) {
  const S = sky.store;
  let base = {};                 // key -> JSON string of the committed model state
  let undoStack = [], redoStack = [];
  let pendingLabel = null, scheduled = false, applying = false;
  let lastLabel = "";
  let chars = 0;
  const listeners = new Set();

  const snapshot = () => {
    const out = {};
    const m = S.model;
    if (!m) return out;
    for (const k of Object.keys(m)) {
      if (k === "_mock_params") continue;
      const v = m[k];
      if (v === undefined || typeof v === "function") continue;
      out[k] = JSON.stringify(v);
    }
    return out;
  };
  const notify = () => { for (const fn of listeners) { try { fn(state()); } catch (e) { console.error(e); } } };
  const state = () => ({
    canUndo: undoStack.length > 0, canRedo: redoStack.length > 0,
    undoLabel: undoStack.length ? undoStack[undoStack.length - 1].label : "",
    redoLabel: redoStack.length ? redoStack[redoStack.length - 1].label : "",
    lastLabel, size: undoStack.length,
  });
  const entryChars = e => Object.values(e.before).reduce((s, v) => s + (v ? v.length : 0), 0) +
    Object.values(e.after).reduce((s, v) => s + (v ? v.length : 0), 0);

  function guessLabel(before, after) {
    const keys = Object.keys(after);
    if (keys.includes("members") || keys.includes("shells") || keys.includes("links")) {
      const d = ["members", "shells", "links"].reduce((s, k) => s + countUids(after[k]) - countUids(before[k]), 0);
      if (d > 0 && S.mode === "model" && DRAW_TOOLS[S.tool]) return DRAW_TOOLS[S.tool];
      if (d > 0) return `Add ${d} object${d > 1 ? "s" : ""}`;
      if (d < 0) return S.tool === "erase" ? "Erase" : `Delete ${-d} object${d < -1 ? "s" : ""}`;
      return "Edit objects";
    }
    for (const k of keys) if (KEY_LABEL[k]) return KEY_LABEL[k];
    return "Edit model";
  }

  function checkpoint() {
    scheduled = false;
    if (applying || !S.model) { pendingLabel = null; return; }
    const cur = snapshot();
    const before = {}, after = {};
    let changed = false;
    for (const k of new Set([...Object.keys(base), ...Object.keys(cur)])) {
      if (base[k] !== cur[k]) { before[k] = base[k]; after[k] = cur[k]; changed = true; }
    }
    if (!changed) { pendingLabel = null; return; }
    const label = pendingLabel || guessLabel(before, after);
    pendingLabel = null;
    const e = { label, before, after, selBefore: sky.__histSel || null, selAfter: JSON.parse(JSON.stringify(S.selection || [])) };
    undoStack.push(e);
    chars += entryChars(e);
    for (const r of redoStack) chars -= entryChars(r);
    redoStack = [];
    while (undoStack.length > CAP || (chars > MAX_CHARS && undoStack.length > 1)) chars -= entryChars(undoStack.shift());
    base = cur;
    lastLabel = label;
    sky.__histSel = JSON.parse(JSON.stringify(S.selection || []));
    notify();
  }

  function schedule(label) {
    if (applying) return;
    if (label) pendingLabel = label;
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(checkpoint);
  }
  /** Flush a pending checkpoint now (before undo / redo / reset). */
  const flush = () => { if (scheduled) checkpoint(); };

  function refreshViews() {
    S.modelEdited = true;
    try { sky.markDirty(); } catch (e) { console.error(e); }   // summary + dirty chips + chrome
    try { sky.rebuildStorySelect && sky.rebuildStorySelect(); } catch (e) { /* optional */ }
    try { sky.planEditor && sky.planEditor.refresh(); } catch (e) { console.error(e); }
    try { sky.elevEditor && sky.elevEditor.refresh(); } catch (e) { console.error(e); }
    try { sky.renderProps && sky.renderProps(); } catch (e) { console.error(e); }
    try { if (S.mode === "loads" && sky.loadsEditor) sky.loadsEditor.render(); } catch (e) { console.error(e); }
    try {
      if (S.mode === "analyze" && sky.viewer) { sky.viewer.setModel(S.model); S.modelEdited = false; }
    } catch (e) { console.error(e); }
  }

  function filterSel(sel) {
    const m = S.model;
    if (!m || !Array.isArray(sel)) return [];
    const has = {
      member: new Set((m.members || []).map(x => x.uid)),
      shell: new Set((m.shells || []).map(x => x.uid)),
      link: new Set((m.links || []).map(x => x.uid)),
    };
    return sel.filter(r => !has[r.type] || has[r.type].has(r.uid));
  }

  function apply(states, sel) {
    applying = true;
    try {
      const m = S.model;
      for (const [k, v] of Object.entries(states)) {
        if (v === undefined) delete m[k];
        else m[k] = JSON.parse(v);
        if (v === undefined) delete base[k]; else base[k] = v;
      }
      S.selection = filterSel(sel || S.selection);
      sky.__histSel = JSON.parse(JSON.stringify(S.selection));
    } finally { applying = false; }
    refreshViews();
  }

  function undo() {
    flush();
    const e = undoStack.pop();
    if (!e) { sky.toast && sky.toast("Undo", "Nothing to undo.", "info", 2500); return false; }
    redoStack.push(e);
    apply(e.before, e.selBefore);
    lastLabel = `Undo ${e.label}`;
    notify();
    return true;
  }
  function redo() {
    flush();
    const e = redoStack.pop();
    if (!e) { sky.toast && sky.toast("Redo", "Nothing to redo.", "info", 2500); return false; }
    undoStack.push(e);
    apply(e.after, e.selAfter);
    lastLabel = `Redo ${e.label}`;
    notify();
    return true;
  }
  function reset() {
    scheduled = false; pendingLabel = null;
    undoStack = []; redoStack = []; chars = 0; lastLabel = "";
    base = snapshot();
    sky.__histSel = JSON.parse(JSON.stringify(S.selection || []));
    notify();
  }

  document.addEventListener("sky:model-changed", () => schedule());
  document.addEventListener("sky:model-synced", () => schedule());
  document.addEventListener("sky:model-replaced", () => reset());
  reset();

  return {
    undo, redo, reset, flush, state,
    /** Name the next recorded action (e.g. "Replicate"). */
    label: name => { pendingLabel = name; },
    /** Record now (normally automatic via markDirty). */
    checkpoint: name => { schedule(name); flush(); },
    onChange: fn => { listeners.add(fn); return () => listeners.delete(fn); },
    entries: () => undoStack.map(e => ({ label: e.label, keys: Object.keys(e.after) })),
    redoEntries: () => redoStack.map(e => ({ label: e.label, keys: Object.keys(e.after) })),
    storedChars: () => chars,
  };
}
