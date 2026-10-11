/* SkyFrame UX — main toolbar (ETABS / SAP2000 style, one row) under the menu bar.
   Groups, left → right:  file · edit │ view │ draw (pointer + tools) │ select │ assign │ run │ display
   Every button runs the SAME command as its menu item (__sky.etabs.clickItem(act)
   or the __sky.etabs helper the status-bar/menu use), so behaviour stays in one
   place. Each group ends in a small ▾ overflow button that lists the rest of
   the matching menu (read live from the menu bar, so it follows the menu owner).
   When the window is too narrow the lowest-priority groups fold into a "More"
   overflow (js/ux_chrome.js fit()). Undo / Redo appear only when another module
   exposes them (an Edit-menu act "edit-undo"/"edit-redo" or __sky.undo/__sky.redo).
   Ids kept from the first toolbar: new open save undo redo select beam column
   brace slab wall run check tables deformed. */

import { el as uxEl, icon as uxIcon, lsGet as uxLsGet, lsSet as uxLsSet } from "./ux_common.js";
import { ICONS as qatICONS } from "./icons.js";
import { HELP as uxHELP } from "./helpdocs.js";
import { keyLabel as uxKeyLabel } from "./ux_keys.js";

const LS_HIDE = "skyframe.ux.toolbarHidden";

/* extra glyphs in the icons.js house style (20×20, stroke=currentColor) */
Object.assign(qatICONS, {
  "sf-chev-down": '<path d="M6 8 L10 12 L14 8"/>',
  "sf-chev-up": '<path d="M6 12 L10 8 L14 12"/>',
  "sf-more": '<circle cx="5" cy="10" r="1.2" fill="currentColor" stroke="none"/><circle cx="10" cy="10" r="1.2" fill="currentColor" stroke="none"/><circle cx="15" cy="10" r="1.2" fill="currentColor" stroke="none"/>',
  "sf-sel-all": '<rect x="3.5" y="3.5" width="13" height="13" rx="1" stroke-dasharray="2.2 1.8"/><rect x="7" y="7" width="6" height="6" fill="currentColor" stroke="none" opacity=".55"/>',
  "sf-sel-clear": '<rect x="3.5" y="3.5" width="13" height="13" rx="1" stroke-dasharray="2.2 1.8"/><path d="M7.5 7.5 L12.5 12.5 M12.5 7.5 L7.5 12.5"/>',
  "sf-asg-section": '<path d="M5 4.5 H15 M5 15.5 H15 M10 4.5 V15.5"/><path d="M13.5 9 L16.5 9 M15 7.5 V10.5" stroke-width="1.3"/>',
  "sf-asg-support": '<path d="M10 3.5 V9"/><path d="M6 13 L10 9 L14 13 Z"/><path d="M4.5 16 H15.5"/>',
  "sf-asg-load": '<path d="M4 15.5 H16"/><path d="M6 4 V12 M10 4 V12 M14 4 V12"/><path d="M4.6 10.4 L6 12 L7.4 10.4 M8.6 10.4 L10 12 L11.4 10.4 M12.6 10.4 L14 12 L15.4 10.4"/>',
  "sf-mode": '<path d="M5 17 V3"/><path d="M5 17 C9 15 11 11 9 7 C8 5 9 4 11 3" stroke-dasharray="2 1.6"/><path d="M3 17 H17"/>',
  "sf-panel-left": '<rect x="3" y="4" width="14" height="12" rx="1.5"/><path d="M8 4 V16"/><path d="M4.8 7 H6.3 M4.8 9.5 H6.3"/>',
  "sf-panel-right": '<rect x="3" y="4" width="14" height="12" rx="1.5"/><path d="M12 4 V16"/><path d="M13.7 7 H15.2 M13.7 9.5 H15.2"/>',
  "sf-info": '<circle cx="10" cy="10" r="7"/><path d="M10 9 V14"/><circle cx="10" cy="6.4" r="0.9" fill="currentColor" stroke="none"/>',
  "sf-report": '<path d="M5 3 H12 L15 6 V17 H5 Z"/><path d="M7.5 9 H12.5 M7.5 11.5 H12.5 M7.5 14 H10.5"/>',
});

/* ---------------- shared overflow popup (also used by js/ux_chrome.js) ----------------
   rows: [{label, hint, ico, disabled, checked, run} | {sep:true} | {head:"…"}] */
let openPop = null;
export function closeQatPopup() {
  if (!openPop) return;
  const p = openPop; openPop = null;
  p.el.remove();
  if (p.anchor) { p.anchor.setAttribute("aria-expanded", "false"); p.anchor.classList.remove("is-open"); }
  document.removeEventListener("mousedown", p.outside, true);
  document.removeEventListener("keydown", p.keys, true);
  window.removeEventListener("resize", p.close);
}
export function openQatPopup(anchor, rows, { label = "More commands" } = {}) {
  if (openPop && openPop.anchor === anchor) { closeQatPopup(); return null; }
  closeQatPopup();
  const pop = uxEl("div", { class: "sf-pop", role: "menu", "aria-label": label });
  const items = [];
  let lastSep = true;
  for (const r of rows) {
    if (r.sep) { if (!lastSep) pop.appendChild(uxEl("div", { class: "sf-pop-sep", role: "separator" })); lastSep = true; continue; }
    if (r.head) { pop.appendChild(uxEl("div", { class: "sf-pop-head", text: r.head })); lastSep = true; continue; }
    const b = uxEl("button", {
      type: "button", class: "sf-pop-item" + (r.checked ? " is-checked" : ""), role: "menuitem",
      title: r.title || "", disabled: r.disabled ? true : null, "data-act": r.act || null,
      html: `<span class="sf-pop-ico">${r.ico ? uxIcon(r.ico, "sf-ico") : ""}</span>`,
    });
    b.appendChild(uxEl("span", { class: "sf-pop-lbl", text: r.label }));
    if (r.hint) b.appendChild(uxEl("kbd", { class: "sf-pop-kbd", text: r.hint }));
    b.addEventListener("click", () => { closeQatPopup(); r.run && r.run(); });
    pop.appendChild(b);
    items.push(b);
    lastSep = false;
  }
  if (pop.lastElementChild && pop.lastElementChild.classList.contains("sf-pop-sep")) pop.lastElementChild.remove();
  if (!items.length) pop.appendChild(uxEl("div", { class: "sf-pop-empty", text: "No further commands" }));
  document.body.appendChild(pop);
  // position under the anchor, kept inside the window
  const a = anchor.getBoundingClientRect();
  const w = pop.offsetWidth, h = pop.offsetHeight;
  let x = Math.min(a.left, window.innerWidth - w - 8);
  x = Math.max(8, x);
  let y = a.bottom + 4;
  if (y + h > window.innerHeight - 8) y = Math.max(8, window.innerHeight - h - 8);
  pop.style.left = x + "px"; pop.style.top = y + "px";
  anchor.setAttribute("aria-expanded", "true"); anchor.classList.add("is-open");
  const p = {
    el: pop, anchor,
    close: () => closeQatPopup(),
    outside: e => { if (!pop.contains(e.target) && !anchor.contains(e.target)) closeQatPopup(); },
    keys: e => {
      const en = items.filter(b => !b.disabled);
      const i = en.indexOf(document.activeElement);
      if (e.key === "Escape") { closeQatPopup(); anchor.focus(); }
      else if (e.key === "ArrowDown") en[(i + 1) % en.length]?.focus();
      else if (e.key === "ArrowUp") en[(i - 1 + en.length) % en.length]?.focus();
      else if (e.key === "Home") en[0]?.focus();
      else if (e.key === "End") en[en.length - 1]?.focus();
      else if (e.key === "Tab") { closeQatPopup(); return; }
      else return;
      e.preventDefault(); e.stopPropagation();
    },
  };
  openPop = p;
  setTimeout(() => {
    document.addEventListener("mousedown", p.outside, true);
    document.addEventListener("keydown", p.keys, true);
    window.addEventListener("resize", p.close);
  }, 0);
  const first = items.find(b => !b.disabled);
  if (first) first.focus({ preventScroll: true });
  return pop;
}

/** Rows of one menu-bar menu (read live, flattened), minus `skip` acts. */
export function menuRows(sky, menuName, skip = []) {
  const btn = document.querySelector(`#etabsMenubar .etabs-menu-btn[data-menu="${menuName}"]`);
  const wrap = btn && btn.closest(".etabs-menu-wrap");
  const dd = wrap && wrap.querySelector(".etabs-menu");
  if (!dd) return [];
  const rows = [];
  dd.querySelectorAll(".etabs-menu-sep, .etabs-menu-item[data-act]").forEach(n => {
    if (n.classList.contains("etabs-menu-sep")) { rows.push({ sep: true }); return; }
    const act = n.dataset.act;
    if (skip.includes(act)) return;
    const lbl = (n.querySelector(".mi-label") || n).textContent.trim();
    const k = n.querySelector("kbd");
    rows.push({
      label: lbl, act, hint: k ? k.textContent.trim() : "", title: n.title || "",
      disabled: n.classList.contains("is-disabled"), checked: n.classList.contains("checked"),
      run: () => sky.etabs && sky.etabs.clickItem(act),
    });
  });
  return rows;
}

export function installToolbar(sky) {
  const S = sky.store;
  const menubar = document.getElementById("etabsMenubar");
  if (!menubar) return;
  const bar = uxEl("div", { class: "ux-qat sf-tb", id: "uxQat", role: "toolbar", "aria-label": "Main toolbar" });
  const clickAct = a => sky.etabs && sky.etabs.clickItem(a);
  const E = () => sky.etabs || {};

  const undoFn = kind => () => {
    if (sky.etabs && sky.etabs.hasItem("edit-" + kind)) return clickAct("edit-" + kind);
    if (typeof sky[kind] === "function") return sky[kind]();
  };
  const hasUndo = kind => !!((sky.etabs && sky.etabs.hasItem("edit-" + kind)) || typeof sky[kind] === "function");
  const viewIs = v => (v === "view3d" ? S.mode === "analyze" && S.tab === "view3d"
    : S.mode === "model" && (S.view === "elev" ? "elev" : "plan") === v);

  // [id, icon, label, act | fn, key, extra]
  // extra: tool (draw tool id → pressed state), view (pressed state), text (visible label),
  //        cls, avail() (shown only when true), needs (act must exist), solved (dim before a solve)
  const GROUPS = [
    { id: "file", label: "File", menu: "File", prio: 2, items: [
      ["new", "ux-new", "New model", "file-new", "Alt+N"],
      ["open", "ux-open", "Open model", "file-open", "Ctrl+O"],
      ["save", "ux-save", "Save model", "file-save", "Ctrl+S"],
    ] },
    { id: "undo", label: "Edit", menu: "Edit", prio: 1, cls: "ux-qat-undo", items: [
      ["undo", "ux-undo", "Undo", undoFn("undo"), "Ctrl+Z", { avail: () => hasUndo("undo") }],
      ["redo", "ux-redo", "Redo", undoFn("redo"), "Ctrl+Y", { avail: () => hasUndo("redo") }],
    ] },
    { id: "view", label: "View", menu: "View", prio: 2, cls: "sf-tb-seg", items: [
      ["plan", "view-plan", "Plan view", () => E().setView && E().setView("plan"), "", { view: "plan", text: "Plan" }],
      ["elev", "view-elev", "Elevation view", () => E().setView && E().setView("elev"), "", { view: "elev", text: "Elev" }],
      ["view3d", "view-3d", "3D view", () => E().showResult && E().showResult("view3d"), "", { view: "view3d", text: "3D" }],
    ] },
    { id: "draw", label: "Draw", menu: "Draw", prio: 1, items: [
      ["select", "tool-select", "Select", "draw-select", "V", { tool: "select" }],
      ["column", "tool-column", "Draw column", "draw-column", "C", { tool: "column" }],
      ["beam", "tool-beam", "Draw beam", "draw-beam", "B", { tool: "beam" }],
      ["brace", "tool-brace", "Draw brace", "draw-brace", "X", { tool: "brace" }],
      ["wall", "tool-wall", "Draw wall", "draw-wall", "W", { tool: "wall" }],
      ["slab", "tool-slab", "Draw slab", "draw-slab", "S", { tool: "slab" }],
    ] },
    { id: "select", label: "Select", menu: "Select", prio: 4, items: [
      ["selall", "sf-sel-all", "Select all", "sel-all", "", { needs: true }],
      ["selclear", "sf-sel-clear", "Clear selection", "sel-clear", "Esc", { needs: true }],
    ] },
    { id: "assign", label: "Assign", menu: "Assign", prio: 5, items: [
      ["asgsec", "sf-asg-section", "Assign frame section", "asn-fsec", "", { needs: true }],
      ["asgsup", "sf-asg-support", "Assign supports / springs", "asn-support", "", { needs: true }],
      ["asgload", "sf-asg-load", "Assign frame loads", "asn-fload", "", { needs: true }],
    ] },
    { id: "run", label: "Analyze", menu: "Analyze", prio: 0, items: [
      ["run", "status-run", "Run analysis", "an-run", "F5", { cls: "ux-qat-run", text: "Run" }],
      ["check", "ux-check", "Check model", "an-check", "Alt+C"],
    ] },
    { id: "display", label: "Display", menu: "Display", prio: 3, items: [
      ["deformed", "ux-deformed", "Deformed shape", "dis-deformed", "F6", { solved: true }],
      ["modeshape", "sf-mode", "Mode shape", "dis-mode", "", { solved: true, needs: true }],
      ["tables", "ux-tables", "Show tables", "dis-tables", "Alt+T", { solved: true }],
    ] },
  ];

  const btns = {};
  const groups = {};
  const left = uxEl("div", { class: "ux-qat-left sf-tb-left" });
  GROUPS.forEach((g, gi) => {
    const grp = uxEl("div", { class: "ux-qat-grp sf-tb-grp" + (g.cls ? " " + g.cls : ""), "data-grp": g.id, "data-prio": String(g.prio), role: "group", "aria-label": g.label });
    if (gi) grp.appendChild(uxEl("span", { class: "ux-qat-sep", "aria-hidden": "true" }));
    const inner = uxEl("div", { class: "sf-tb-inner" });
    for (const [id, ic, label, what, key, extra = {}] of g.items) {
      const h = typeof what === "string" ? uxHELP[what] : null;
      const tip = `${label}${key ? ` (${uxKeyLabel(key)})` : ""}${h ? "\n" + h.text : ""}`;
      const b = uxEl("button", {
        type: "button", class: "ux-qat-btn" + (extra.cls ? " " + extra.cls : ""), "data-qat": id,
        title: tip, "aria-label": label + (key ? ` (${key})` : ""), html: uxIcon(ic, "ux-qat-ico"),
      });
      if (extra.tool || extra.view) b.setAttribute("aria-pressed", "false");
      if (extra.text) b.appendChild(uxEl("span", { class: "ux-qat-txt", text: extra.text }));
      if (extra.cls === "ux-qat-run") b.insertAdjacentHTML("afterbegin", '<span class="spinner sf-run-spin hidden" aria-hidden="true"></span>');
      b.addEventListener("click", () => { typeof what === "function" ? what() : clickAct(what); sync(); });
      b._extra = extra; b._act = typeof what === "string" ? what : null; b._label = label; b._icon = ic; b._key = key;
      btns[id] = b;
      inner.appendChild(b);
    }
    grp.appendChild(inner);
    if (g.menu) {
      const skip = g.items.map(it => (typeof it[3] === "string" ? it[3] : null)).filter(Boolean);
      const more = uxEl("button", {
        type: "button", class: "ux-qat-btn sf-tb-more", "data-qat-more": g.id, "aria-haspopup": "menu", "aria-expanded": "false",
        title: `More ${g.label} commands`, "aria-label": `More ${g.label} commands`, html: uxIcon("sf-chev-down", "ux-qat-ico sf-chev"),
      });
      more.addEventListener("click", e => { e.stopPropagation(); openQatPopup(more, menuRows(sky, g.menu, skip), { label: g.label }); });
      grp.appendChild(more);
      grp._more = more;
    }
    grp._def = g;
    groups[g.id] = grp;
    left.appendChild(grp);
  });

  const search = uxEl("button", { type: "button", class: "ux-qat-search", title: "Search every command (Ctrl+K / Ctrl+Shift+P)", "aria-label": "Search commands" });
  search.innerHTML = uxIcon("ux-search", "ux-qat-ico") + `<span class="ux-qat-search-txt">Search commands…</span><kbd>${uxKeyLabel("Ctrl+K")}</kbd>`;
  search.addEventListener("click", () => sky.ux.palette && sky.ux.palette.open());
  const help = uxEl("button", { type: "button", class: "ux-qat-btn", "data-qat": "keys", title: "Keyboard shortcuts (F1 or ?)", "aria-label": "Keyboard shortcuts", html: uxIcon("ux-keyboard", "ux-qat-ico") });
  help.addEventListener("click", () => sky.ux.shortcuts && sky.ux.shortcuts.toggleOverlay());
  const right = uxEl("div", { class: "ux-qat-right sf-tb-right" }, [search, help]);
  bar.append(left, right);
  menubar.after(bar);

  function sync() {
    const tool = S.mode === "model" ? S.tool : null;
    const solved = !!S.results;
    for (const b of Object.values(btns)) {
      const x = b._extra;
      let on = null;
      if (x.tool) on = x.tool === tool;
      else if (x.view) on = viewIs(x.view);
      if (on != null) {
        b.classList.toggle("is-active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      }
      let show = true;
      if (x.avail) show = !!x.avail();
      if (x.needs && b._act) show = show && !!(sky.etabs && sky.etabs.hasItem(b._act));
      b.classList.toggle("hidden", !show);
      if (x.solved) b.classList.toggle("is-dim", !solved);
    }
    groups.undo.classList.toggle("hidden", !hasUndo("undo") && !hasUndo("redo"));
    const rb = document.getElementById("runBtn");
    btns.run.disabled = !!(rb && rb.disabled);
    const spin = document.getElementById("runSpinner");
    const running = !!(spin && !spin.classList.contains("hidden"));
    btns.run.classList.toggle("is-running", running);
    btns.run.querySelector(".sf-run-spin").classList.toggle("hidden", !running);
    const txt = btns.run.querySelector(".ux-qat-txt");
    if (txt) txt.textContent = running ? "Running" : "Run";
    // slab / line-spring have no meaning in an elevation (app.js disables the tool there)
    btns.slab.disabled = S.mode === "model" && S.view === "elev";
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
  const rs = document.getElementById("runSpinner");
  if (rs) new MutationObserver(later).observe(rs, { attributes: true, attributeFilter: ["class"] });
  const clsObs = new MutationObserver(later);   // programmatic mode / tool / view switches
  for (const id of ["drawMain", "analyzeMain", "planSvg", "pane-view3d"]) { const n = document.getElementById(id); if (n) clsObs.observe(n, { attributes: true, attributeFilter: ["class"] }); }
  document.querySelectorAll(".tool-btn").forEach(b => clsObs.observe(b, { attributes: true, attributeFilter: ["class"] }));
  sync();
  setTimeout(sync, 0);   // modules that init after us (Edit menu / undo)

  sky.ux = sky.ux || {};
  sky.ux.toolbar = {
    el: bar, sync, buttons: () => Object.keys(btns), button: id => btns[id] || null,
    groups: () => Object.keys(groups), group: id => groups[id] || null,
    toggle: () => { hidden = !hidden; uxLsSet(LS_HIDE, hidden); apply(); return !hidden; },
    isVisible: () => !hidden,
  };
}
