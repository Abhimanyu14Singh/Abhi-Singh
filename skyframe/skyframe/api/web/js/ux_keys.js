/* SkyFrame UX — keyboard-shortcut registry + Help › Keyboard Shortcuts overlay.
   The registry documents the app's existing single-key shortcuts (tool keys,
   R, F, 1–9, arrows …, still handled by app.js) and OWNS the new chorded ones
   (Ctrl+K palette, F1 / ? help, F5 run, F6 deformed shape, Ctrl+S/O …).
   Other modules may add entries: __sky.ux.shortcuts.register({keys, desc, group, run}). */

import { el as uxEl, uxDialog, closeUxDialog, isUxDialogOpen } from "./ux_common.js";

const isMac = /Mac|iPhone|iPad/.test(navigator.platform || "");

/** "Ctrl+Shift+P" style chord of a keyboard event (Meta counts as Ctrl). */
export function chordOf(e) {
  const parts = [];
  if (e.ctrlKey || e.metaKey) parts.push("Ctrl");
  if (e.altKey) parts.push("Alt");
  let k = e.key;
  if (k === " ") k = "Space";
  const printable = k.length === 1;
  if (e.shiftKey && !(printable && !/[a-z]/i.test(k))) parts.push("Shift");   // "?" already implies Shift
  parts.push(printable ? k.toUpperCase() : k);
  return parts.join("+");
}
const editable = t => !!(t && t.closest && t.closest("input, select, textarea, [contenteditable=''], [contenteditable=true]"));
export const keyLabel = k => isMac ? k.replace(/Ctrl\+/g, "⌘").replace(/Alt\+/g, "⌥").replace(/Shift\+/g, "⇧") : k;

export function installKeys(sky) {
  const S = sky.store;
  const list = [];
  const act = a => () => sky.etabs && sky.etabs.clickItem(a);
  const register = sc => { list.push({ group: "General", ...sc, keys: [].concat(sc.keys) }); return sc; };

  /* ---- owned (handled here) ---- */
  register({ id: "palette", keys: ["Ctrl+K", "Ctrl+Shift+P"], desc: "Command palette — search every menu command", group: "General", anywhere: true,
    run: () => sky.ux.palette && sky.ux.palette.toggle() });
  register({ id: "help", keys: ["F1", "?"], desc: "Keyboard shortcuts (this list)", group: "General", anywhere: true, run: () => toggleOverlay() });
  register({ id: "new", keys: ["Alt+N"], desc: "New model (start screen / templates)", group: "File", run: act("file-new") });
  register({ id: "open", keys: ["Ctrl+O"], desc: "Open model…", group: "File", run: act("file-open") });
  register({ id: "save", keys: ["Ctrl+S"], desc: "Save model", group: "File", run: act("file-save") });
  register({ id: "saveas", keys: ["Ctrl+Shift+S"], desc: "Save model as…", group: "File", run: act("file-saveas") });
  register({ id: "run", keys: ["F5"], desc: "Run analysis (ETABS F5)", group: "Analyze", run: act("an-run") });
  register({ id: "check", keys: ["Alt+C"], desc: "Check model…", group: "Analyze", run: act("an-check") });
  register({ id: "deformed", keys: ["F6"], desc: "Deformed shape (ETABS F6)", group: "Display", run: act("dis-deformed") });
  register({ id: "tables", keys: ["Alt+T"], desc: "Show tables…", group: "Display", run: act("dis-tables") });
  register({ id: "units", keys: ["Alt+U"], desc: "Units…", group: "Options", run: act("opt-units") });
  register({ id: "toolbar", keys: ["Alt+Q"], desc: "Show / hide the quick-access toolbar", group: "Options", run: () => sky.ux.toolbar && sky.ux.toolbar.toggle() });

  /* ---- documented (handled by app.js / dialogs) ---- */
  const doc = (keys, desc, group) => register({ keys, desc, group, doc: true });
  doc("V", "Select tool", "Draw (Model mode)");
  doc("C", "Draw column", "Draw (Model mode)");
  doc("B", "Draw beam", "Draw (Model mode)");
  doc("X", "Draw brace", "Draw (Model mode)");
  doc("W", "Draw wall", "Draw (Model mode)");
  doc("S", "Draw slab", "Draw (Model mode)");
  doc("L", "Draw link", "Draw (Model mode)");
  doc("G", "Point spring", "Draw (Model mode)");
  doc("K", "Line spring", "Draw (Model mode)");
  doc("E", "Erase / opening", "Draw (Model mode)");
  doc("F", "Zoom to fit (plan / elevation / 3D)", "View");
  doc(["↑", "↓"], "Story up / down (plan)", "View");
  doc(["Delete", "Backspace"], "Delete selection", "Draw (Model mode)");
  doc("Esc", "Cancel the current drawing / clear selection / close dialog", "General");
  doc("\\", "Toggle the side panel", "View");
  doc(["1", "…", "9"], "Results tabs (Analyze mode)", "Display");
  doc("R", "Run analysis (Analyze mode)", "Analyze");
  doc("Enter", "OK in a dialog (when not typing in a text area)", "Dialogs");
  doc("Esc", "Cancel a dialog", "Dialogs");
  doc("Double-click title", "Reset a dialog's size and position", "Dialogs");

  const owned = () => list.filter(s => s.run && !s.doc);
  const findFor = e => {
    const c = chordOf(e);
    return owned().find(s => s.keys.includes(c) || (e.key === "?" && s.keys.includes("?")));
  };

  window.addEventListener("keydown", e => {
    if (e.isComposing) return;
    const sc = findFor(e);
    if (!sc) return;
    const plain = e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey;
    if (plain && editable(e.target)) return;                  // typing "?" in a field
    const dlgOpen = !!(sky.ux.dialogs && sky.ux.dialogs.top());
    const palOpen = !!(sky.ux.palette && sky.ux.palette.isOpen());
    if ((dlgOpen || palOpen) && !sc.anywhere) {
      if (sc.id === "save" || sc.id === "open" || sc.id === "run") e.preventDefault();   // no browser save / reload behind a dialog
      return;
    }
    if (sc.id === "help" && dlgOpen && !isUxDialogOpen("uxKeysModal")) { /* F1 over a dialog → its "?" */
      const hb = sky.ux.dialogs.top().querySelector(".ux-help-btn");
      if (hb) { e.preventDefault(); e.stopPropagation(); hb.click(); return; }
    }
    e.preventDefault();
    e.stopPropagation();
    sc.run();
  }, true);

  /* ---- overlay ---- */
  function toggleOverlay() {
    if (isUxDialogOpen("uxKeysModal")) return closeUxDialog("uxKeysModal");
    if (sky.ux.palette && sky.ux.palette.isOpen()) sky.ux.palette.close();
    const filter = uxEl("input", { type: "search", class: "ux-keys-filter", placeholder: "Filter shortcuts…", "aria-label": "Filter shortcuts" });
    const grid = uxEl("div", { class: "ux-keys-grid" });
    const render = () => {
      const q = filter.value.trim().toLowerCase();
      grid.textContent = "";
      const extra = [];
      if (typeof sky.undo === "function" || (sky.etabs && sky.etabs.hasItem("edit-undo")))
        extra.push({ keys: ["Ctrl+Z"], desc: "Undo", group: "Edit" }, { keys: ["Ctrl+Y", "Ctrl+Shift+Z"], desc: "Redo", group: "Edit" });
      const all = list.concat(extra).filter(s => !q || (s.desc + " " + s.keys.join(" ") + " " + s.group).toLowerCase().includes(q));
      const groups = [...new Set(all.map(s => s.group))];
      for (const g of groups) {
        const sec = uxEl("section", { class: "ux-keys-group" }, [uxEl("h3", { text: g })]);
        for (const s of all.filter(x => x.group === g)) {
          const keys = uxEl("span", { class: "ux-keys-k" });
          s.keys.forEach((k, i) => {
            if (i) keys.appendChild(uxEl("span", { class: "ux-or", text: "/" }));
            keys.appendChild(uxEl("kbd", { text: keyLabel(k) }));
          });
          sec.appendChild(uxEl("div", { class: "ux-keys-row" }, [uxEl("span", { class: "ux-keys-d", text: s.desc }), keys]));
        }
        grid.appendChild(sec);
      }
      if (!all.length) grid.appendChild(uxEl("p", { class: "muted", text: "No shortcut matches." }));
    };
    filter.addEventListener("input", render);
    render();
    const close = uxEl("button", { class: "btn btn-run", type: "button", text: "Close" });
    const d = uxDialog("uxKeysModal", {
      title: "Keyboard Shortcuts", iconId: "ux-keyboard", cls: "ux-keys-dlg",
      body: [filter, grid],
      foot: [uxEl("span", { class: "muted", text: "Press F1 or ? any time · Ctrl+K searches every command" }), uxEl("div", { class: "modal-btns" }, [close])],
    });
    close.addEventListener("click", d.close);
    requestAnimationFrame(() => filter.focus());
  }

  /* ---- keyboard navigation of the menu bar (F10 focuses it; arrows move) ---- */
  const mb = document.getElementById("etabsMenubar");
  if (mb) {
    const wraps = () => [...mb.querySelectorAll(".etabs-menu-wrap")];
    const itemsOf = w => [...w.querySelectorAll(".etabs-menu-item:not(.is-disabled)")];
    const openAt = (w, focusFirst) => {
      const name = w.querySelector(".etabs-menu-btn").dataset.menu;
      sky.etabs.openMenu(name);
      if (focusFirst) { const it = itemsOf(w)[0]; if (it) it.focus(); }
      else w.querySelector(".etabs-menu-btn").focus();
    };
    mb.addEventListener("keydown", e => {
      const btn = e.target.closest(".etabs-menu-btn"), item = e.target.closest(".etabs-menu-item");
      const w = e.target.closest(".etabs-menu-wrap");
      if (!w || e.ctrlKey || e.metaKey || e.altKey) return;
      const all = wraps(), wi = all.indexOf(w);
      const k = e.key;
      if (btn && (k === "ArrowDown" || k === "Enter" || k === " ")) { e.preventDefault(); openAt(w, true); return; }
      if (k === "ArrowRight" || k === "ArrowLeft") {
        e.preventDefault();
        const nw = all[(wi + (k === "ArrowRight" ? 1 : -1) + all.length) % all.length];
        openAt(nw, !!item);
        return;
      }
      if (item && (k === "ArrowDown" || k === "ArrowUp" || k === "Home" || k === "End")) {
        e.preventDefault();
        const its = itemsOf(w), i = its.indexOf(item);
        const n = k === "Home" ? 0 : k === "End" ? its.length - 1 : (i + (k === "ArrowDown" ? 1 : -1) + its.length) % its.length;
        its[n].focus();
        return;
      }
      if (k === "Escape") {
        // etabs.js closes the menu in its capture handler; keep focus on the bar
        setTimeout(() => { const b = w.querySelector(".etabs-menu-btn"); if (b) b.focus(); }, 0);
      }
    });
    register({ id: "menubar", keys: ["F10"], desc: "Focus the menu bar (then ← → ↑ ↓, Enter, Esc)", group: "General",
      run: () => { const b = mb.querySelector(".etabs-menu-btn"); if (b) b.focus(); } });
  }

  sky.ux = sky.ux || {};
  sky.ux.shortcuts = { list: () => list.slice(), register, chordOf, openOverlay: () => { if (!isUxDialogOpen("uxKeysModal")) toggleOverlay(); }, toggleOverlay };
}
