/* SkyFrame — per-dialog display-unit selector (audit D3, "the automatic
   designer has frozen SI units").

   Section / material editors (Section Designer, Built-up Section editor,
   Frame / Slab-Wall Section Property Data with live preview, Import Frame
   Sections, Import from Database, Material Library, Material Property Data,
   Composite Column data, Nonprismatic section) carry a "Units" <select> that
   DEFAULTS TO THE MODEL'S UNITS (model.display_units). Picking another set
   switches the display set while the dialog is open (every input, label,
   preview and property readout re-renders through js/units.js; the model
   store stays SI, so nothing is converted twice and nothing drifts); closing
   the last such dialog restores the model's units. model.display_units is
   never written here — OK with defaults leaves the saved model byte-identical.

     duPicker()                 → { el, release() }  (explicit lifetime)
     duAttach(rootEl, host?)    → picker placed in host (default: the root's
                                  footer), released automatically when the
                                  dialog element leaves the DOM
     duAuto(id, rootEl, host)   → duAttach for the dlgkit dialog ids below
                                  (hook in js/dlgkit.js dkDialog)            */

import duU from "./units.js";

/** dlgkit dialog ids that get the selector automatically. */
export const DU_DIALOG_IDS = new Set([
  "dsFrameSectionData", "dsShellSectionData", "dsSectionLib", "dsSteelDb",
  "mlMatLib", "dsMaterialData", "wbCompositeData",
]);

let depth = 0;          // open dialogs holding a picker
let entrySet = null;    // display set when the first one opened

const modelUnits = () => {
  const m = window.__sky && window.__sky.store && window.__sky.store.model;
  return duU.normalizeUnits((m && m.display_units) || entrySet || duU.getUnits());
};

function optionText(name) {
  const s = duU.UNIT_SETS[name];
  return `${name}  (${s.label})` + (name === modelUnits() ? " · model" : "");
}

function ensureCss() {
  if (document.getElementById("duUnitsCss")) return;
  const st = document.createElement("style");
  st.id = "duUnitsCss";
  st.textContent = ".du-units-wrap{display:inline-flex;align-items:center;gap:6px;margin-right:12px;font-size:12px;color:var(--text-2,inherit);white-space:nowrap}" +
    ".du-units-wrap select{max-width:210px}";
  document.head.appendChild(st);
}

export function duPicker({ id } = {}) {
  ensureCss();
  if (depth++ === 0) entrySet = duU.getUnits();
  const sel = document.createElement("select");
  sel.className = "du-units mini-select";
  if (id) sel.id = id;
  sel.setAttribute("aria-label", "Display units for this dialog");
  sel.title = "Display units for this dialog — defaults to the model units; inputs, labels, previews and properties follow it (the model is stored in SI)";
  for (const n of duU.UNIT_SET_NAMES) {
    const o = document.createElement("option");
    o.value = n; o.textContent = optionText(n);
    sel.appendChild(o);
  }
  sel.value = duU.getUnits();
  sel.addEventListener("change", () => duU.setUnits(sel.value));
  const off = duU.onUnitsChange(n => { sel.value = n; });
  const el = document.createElement("label");
  el.className = "du-units-wrap";
  el.append("Units ", sel);
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    off();
    if (--depth === 0) {
      const back = modelUnits();
      entrySet = null;
      if (duU.getUnits() !== back) duU.setUnits(back);
    }
  };
  return { el, select: sel, release };
}

/** Place a picker in ``host`` (default: the dialog's footer / its left
    part) and release it when ``rootEl`` is removed from the document. */
export function duAttach(rootEl, host = null, opts = {}) {
  if (!rootEl) return null;
  const p = duPicker(opts);
  const where = host || rootEl.querySelector(".dk-foot-left, .modal-foot") || rootEl;
  where.prepend(p.el);
  const parent = rootEl.parentNode || document.body;
  const mo = new MutationObserver(() => {
    if (!rootEl.isConnected) { mo.disconnect(); p.release(); }
  });
  mo.observe(parent, { childList: true });
  return p;
}

/** dlgkit hook: attach to the section / material dialogs. */
export function duAuto(id, rootEl, host) {
  return DU_DIALOG_IDS.has(id) ? duAttach(rootEl, host, { id: id + "Units" }) : null;
}
