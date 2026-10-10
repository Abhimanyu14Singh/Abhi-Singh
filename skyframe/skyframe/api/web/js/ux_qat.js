/* SkyFrame UX — quick-access toolbar (ETABS ribbon-lite) under the menu bar.
   Icon buttons with tooltips for the most used commands. Every button runs
   the SAME menu command (__sky.etabs.clickItem(act)), so behaviour stays in
   one place. Undo / Redo appear only when another module exposes them
   (an Edit-menu act "edit-undo"/"edit-redo" or __sky.undo/__sky.redo). */

import { el as uxEl, icon as uxIcon, lsGet as uxLsGet, lsSet as uxLsSet } from "./ux_common.js";
import { HELP as uxHELP } from "./helpdocs.js";
import { keyLabel as uxKeyLabel } from "./ux_keys.js";
import { UNIT_SET_NAMES as uxUNIT_SET_NAMES, getUnits as uxGetUnits } from "./units.js";

const LS_HIDE = "skyframe.ux.toolbarHidden";

export function installToolbar(sky) {
  const S = sky.store;
  const menubar = document.getElementById("etabsMenubar");
  if (!menubar) return;
  const bar = uxEl("div", { class: "ux-qat", id: "uxQat", role: "toolbar", "aria-label": "Quick access toolbar" });
  const clickAct = a => sky.etabs && sky.etabs.clickItem(a);

  const undoFn = kind => () => {
    if (sky.etabs && sky.etabs.hasItem("edit-" + kind)) return clickAct("edit-" + kind);
    if (typeof sky[kind] === "function") return sky[kind]();
  };
  const hasUndo = kind => !!((sky.etabs && sky.etabs.hasItem("edit-" + kind)) || typeof sky[kind] === "function");

  // [id, icon, label, act | fn, key, extra]
  const GROUPS = [
    [["new", "ux-new", "New model", "file-new", "Alt+N"],
     ["open", "ux-open", "Open model", "file-open", "Ctrl+O"],
     ["save", "ux-save", "Save model", "file-save", "Ctrl+S"]],
    [["undo", "ux-undo", "Undo", undoFn("undo"), "Ctrl+Z", { avail: () => hasUndo("undo") }],
     ["redo", "ux-redo", "Redo", undoFn("redo"), "Ctrl+Y", { avail: () => hasUndo("redo") }]],
    [["select", "tool-select", "Select", "draw-select", "V", { tool: "select" }],
     ["beam", "tool-beam", "Draw beam", "draw-beam", "B", { tool: "beam" }],
     ["column", "tool-column", "Draw column", "draw-column", "C", { tool: "column" }],
     ["brace", "tool-brace", "Draw brace", "draw-brace", "X", { tool: "brace" }],
     ["slab", "tool-slab", "Draw slab", "draw-slab", "S", { tool: "slab" }],
     ["wall", "tool-wall", "Draw wall", "draw-wall", "W", { tool: "wall" }]],
    [["run", "status-run", "Run analysis", "an-run", "F5", { cls: "ux-qat-run", text: "Run" }],
     ["check", "ux-check", "Check model", "an-check", "Alt+C"],
     ["tables", "ux-tables", "Show tables", "dis-tables", "Alt+T"],
     ["deformed", "ux-deformed", "Deformed shape", "dis-deformed", "F6"]],
  ];
  const btns = {};
  const left = uxEl("div", { class: "ux-qat-left" });
  GROUPS.forEach((g, gi) => {
    if (gi) left.appendChild(uxEl("span", { class: "ux-qat-sep", "aria-hidden": "true" }));
    const grp = uxEl("div", { class: "ux-qat-grp" + (gi === 1 ? " ux-qat-undo" : "") });
    for (const [id, ic, label, what, key, extra = {}] of g) {
      const h = typeof what === "string" ? uxHELP[what] : null;
      const tip = `${label}${key ? ` (${uxKeyLabel(key)})` : ""}${h ? "\n" + h.text : ""}`;
      const b = uxEl("button", {
        type: "button", class: "ux-qat-btn" + (extra.cls ? " " + extra.cls : ""), "data-qat": id,
        title: tip, "aria-label": label + (key ? ` (${key})` : ""), html: uxIcon(ic, "ux-qat-ico"),
      });
      if (extra.text) b.appendChild(uxEl("span", { class: "ux-qat-txt", text: extra.text }));
      b.addEventListener("click", () => { typeof what === "function" ? what() : clickAct(what); sync(); });
      b._extra = extra;
      btns[id] = b;
      grp.appendChild(b);
    }
    left.appendChild(grp);
  });

  // units quick switch (mirrors the status-bar selector)
  const unitsSel = uxEl("select", { class: "ux-qat-units", "aria-label": "Display units", title: "Display units — every input, table and diagram converts; the model stays SI (Alt+U: Units dialog)" });
  for (const n of uxUNIT_SET_NAMES) unitsSel.appendChild(uxEl("option", { value: n, text: n }));
  unitsSel.value = uxGetUnits();
  unitsSel.addEventListener("change", () => sky.setDisplayUnits && sky.setDisplayUnits(unitsSel.value));
  left.appendChild(uxEl("span", { class: "ux-qat-sep", "aria-hidden": "true" }));
  left.appendChild(uxEl("label", { class: "ux-qat-unitswrap", title: "Display units" }, [uxEl("span", { class: "ux-qat-ico-wrap", html: uxIcon("units", "ux-qat-ico") }), unitsSel]));

  const search = uxEl("button", { type: "button", class: "ux-qat-search", title: "Search every command (Ctrl+K / Ctrl+Shift+P)", "aria-label": "Search commands" });
  search.innerHTML = uxIcon("ux-search", "ux-qat-ico") + `<span class="ux-qat-search-txt">Search commands…</span><kbd>${uxKeyLabel("Ctrl+K")}</kbd>`;
  search.addEventListener("click", () => sky.ux.palette && sky.ux.palette.open());
  const help = uxEl("button", { type: "button", class: "ux-qat-btn", title: "Keyboard shortcuts (F1 or ?)", "aria-label": "Keyboard shortcuts", html: uxIcon("ux-keyboard", "ux-qat-ico") });
  help.addEventListener("click", () => sky.ux.shortcuts && sky.ux.shortcuts.toggleOverlay());
  const right = uxEl("div", { class: "ux-qat-right" }, [search, help]);
  bar.append(left, right);
  menubar.after(bar);

  function sync() {
    const tool = S.mode === "model" ? S.tool : null;
    for (const b of Object.values(btns)) {
      const x = b._extra;
      if (x.tool) {
        const on = x.tool === tool;
        b.classList.toggle("is-active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      }
      if (x.avail) b.classList.toggle("hidden", !x.avail());
    }
    left.querySelector(".ux-qat-undo").classList.toggle("hidden", !hasUndo("undo") && !hasUndo("redo"));
    const solved = !!S.results;
    btns.deformed.classList.toggle("is-dim", !solved);
    btns.tables.classList.toggle("is-dim", !solved);
    if (unitsSel.value !== uxGetUnits()) unitsSel.value = uxGetUnits();
    const running = document.getElementById("runBtn");
    btns.run.disabled = !!(running && running.disabled);
  }

  let hidden = !!uxLsGet(LS_HIDE, false);
  const apply = () => { bar.classList.toggle("hidden", hidden); setTimeout(() => sky.viewer && sky.viewer._resize && sky.viewer._resize(), 0); };
  apply();

  // cheap sync triggers (no polling)
  let raf = 0;
  const later = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; sync(); }); };
  document.addEventListener("sky:model-changed", later);
  document.addEventListener("sky:results-changed", later);
  document.addEventListener("sky:units-changed", later);
  document.addEventListener("keyup", later, true);
  document.addEventListener("click", later, true);
  const rb = document.getElementById("runBtn");
  if (rb) new MutationObserver(later).observe(rb, { attributes: true, attributeFilter: ["disabled"] });
  const clsObs = new MutationObserver(later);   // programmatic mode / tool switches
  for (const id of ["drawMain", "analyzeMain"]) { const n = document.getElementById(id); if (n) clsObs.observe(n, { attributes: true, attributeFilter: ["class"] }); }
  document.querySelectorAll(".tool-btn").forEach(b => clsObs.observe(b, { attributes: true, attributeFilter: ["class"] }));
  sync();
  setTimeout(sync, 0);   // modules that init after us (Edit menu / undo)

  sky.ux = sky.ux || {};
  sky.ux.toolbar = {
    el: bar, sync, buttons: () => Object.keys(btns),
    toggle: () => { hidden = !hidden; uxLsSet(LS_HIDE, hidden); apply(); return !hidden; },
    isVisible: () => !hidden,
  };
}
