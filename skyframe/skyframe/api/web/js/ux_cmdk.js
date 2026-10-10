/* SkyFrame UX — command palette (Ctrl+K / Ctrl+Shift+P).
   Fuzzy search over EVERY menu command, read at open time from the live menu
   bar built by js/etabs.js (so menus added later by other modules — Edit,
   plots… — are included automatically), plus a few UX commands. Enter runs
   the highlighted command through __sky.etabs.clickItem(act) (the same code
   path as a menu click). Recent commands (localStorage) are listed first. */

import { el as uxEl, esc as uxEsc, lsGet as uxLsGet, lsSet as uxLsSet } from "./ux_common.js";
import { HELP as uxHELP } from "./helpdocs.js";
import { keyLabel as uxKeyLabel } from "./ux_keys.js";

const LS_RECENT = "skyframe.ux.recentCommands";

/* search synonyms (abbreviations engineers type) → matched like the label */
const KEYWORDS = {
  "def-combos": "combo combos combination loadcombo", "def-loadcases": "case cases loadcase",
  "def-pdelta": "pdelta p-delta second order", "dis-deformed": "deflected displaced displacement shape",
  "dis-tables": "table tables results grid", "an-run": "solve analyse analyze calculate",
  "def-mass": "mass seismic weight", "opt-units": "unit units kip ft kn",
  "def-grid": "grid gridlines", "def-stories": "story stories levels floors",
  "dis-mode": "mode shape eigen animate", "dis-modal": "periods frequencies participation",
  "an-check": "validate verify warnings errors", "def-materials": "material concrete steel",
  "file-new": "new template start", "help-shortcuts": "keys hotkeys keyboard",
  "dis-story": "drift drifts story shear", "dis-reactions": "support reactions base",
};

/** Subsequence fuzzy score of query q in text t (higher is better; -1 = no match). */
export function fuzzyScore(q, t) {
  q = q.toLowerCase(); t = t.toLowerCase();
  if (!q) return 0;
  const direct = t.indexOf(q);
  if (direct >= 0) return 1000 - direct + (direct === 0 || /[\s·›(/-]/.test(t[direct - 1]) ? 200 : 0);
  let score = 0, ti = 0, prev = -2, first = -1;
  for (let qi = 0; qi < q.length; qi++) {
    const c = q[qi];
    if (c === " ") continue;
    const at = t.indexOf(c, ti);
    if (at < 0) return -1;
    if (first < 0) first = at;
    score += at === prev + 1 ? 12 : 1;                                  // contiguous run
    if (at === 0 || /[\s·›(/-]/.test(t[at - 1])) score += 10;          // word start
    prev = at; ti = at + 1;
  }
  if (prev - first + 1 > q.length * 3 + 2) return -1;                 // too scattered to be meant
  return score - first * 0.2 - (t.length * 0.02);
}
/** Best score over all space-separated words: every word must match somewhere. */
function multiScore(q, hay) {
  const words = q.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return 0;
  let s = 0;
  for (const w of words) {
    const v = fuzzyScore(w, hay);
    if (v < 0) return -1;
    s += v;
  }
  return s + (words.length > 1 ? fuzzyScore(q.replace(/\s+/g, " "), hay) * 0.3 : 0);
}

export function installPalette(sky) {
  let back = null, input = null, listEl = null, items = [], sel = 0, open = false, lastFocus = null;

  /* ---- command source: the live menu DOM + UX extras ---- */
  function commands() {
    const out = [];
    document.querySelectorAll("#etabsMenubar .etabs-menu-wrap").forEach(wrap => {
      const menu = (wrap.querySelector(".etabs-menu-btn") || {}).dataset?.menu || "";
      wrap.querySelectorAll(".etabs-menu-item[data-act]").forEach(it => {
        const act = it.dataset.act;
        const label = (it.querySelector(".mi-label") || it).textContent.trim();
        const hintEl = it.querySelector(".mi-hint");
        const kbd = it.querySelector("kbd");
        const h = uxHELP[act];
        out.push({
          id: act, act, menu, label, hint: hintEl ? hintEl.textContent.trim() : "",
          key: kbd ? kbd.textContent.trim() : "", disabled: it.classList.contains("is-disabled"),
          checked: it.classList.contains("checked"),
          etabs: h ? h.etabs : "", desc: h ? h.text : "",
        });
      });
    });
    const ux = sky.ux || {};
    const extra = [
      { id: "ux:shortcuts", alias: "help-shortcuts", menu: "Help", label: "Keyboard Shortcuts", key: "F1", run: () => ux.shortcuts && ux.shortcuts.openOverlay() },
      { id: "ux:start", alias: "help-start", menu: "File", label: "Start Screen / Templates…", run: () => ux.start && ux.start.open() },
      { id: "ux:toolbar", alias: "help-toolbar", menu: "Options", label: "Toggle Quick-Access Toolbar", key: "Alt+Q", run: () => ux.toolbar && ux.toolbar.toggle() },
      { id: "ux:explorer", menu: "View", label: "Toggle Model Explorer", run: () => sky.etabs && sky.etabs.toggleExplorer() },
      { id: "ux:mode-model", menu: "View", label: "Model mode (Plan)", run: () => sky.etabs && sky.etabs.setView("plan") },
      { id: "ux:mode-elev", menu: "View", label: "Model mode (Elevation)", run: () => sky.etabs && sky.etabs.setView("elev") },
      { id: "ux:mode-loads", menu: "View", label: "Loads mode (patterns · cases · combinations)", run: () => sky.etabs && sky.etabs.gotoLoads() },
    ];
    // explorer-only destinations (no menu item)
    if (sky.etabs && sky.etabs.explorerNodes) {
      const have = new Set(out.map(c => c.label.replace(/…$/, "").toLowerCase()));
      for (const n of sky.etabs.explorerNodes())
        if (!have.has(n.toLowerCase())) extra.push({ id: "ux:ex:" + n, menu: "Explorer", label: n, run: () => sky.etabs.explorerClick(n) });
    }
    const acts = new Set(out.map(c => c.act));
    for (const e of extra) if (!(e.alias && acts.has(e.alias))) out.push(e);
    return out.filter(c => c.act !== "help-palette");   // the palette itself
  }

  const recent = () => { const r = uxLsGet(LS_RECENT, []); return Array.isArray(r) ? r : []; };
  const pushRecent = id => uxLsSet(LS_RECENT, [id, ...recent().filter(x => x !== id)].slice(0, 8));

  function run(cmd) {
    if (!cmd || cmd.disabled) return;
    close();
    pushRecent(cmd.id);
    // let the palette disappear before a dialog grabs focus
    setTimeout(() => {
      try {
        if (cmd.run) cmd.run();
        else if (sky.etabs && sky.etabs.hasItem(cmd.act)) sky.etabs.clickItem(cmd.act);
        else { const n = document.querySelector(`.etabs-menu-item[data-act="${cmd.act}"]`); if (n) n.click(); }
      } catch (err) { console.warn("command failed", cmd.id, err); }
    }, 0);
  }

  function build() {
    back = uxEl("div", { class: "ux-cmdk-back hidden", role: "presentation" });
    const box = uxEl("div", { class: "ux-cmdk", role: "dialog", "aria-modal": "true", "aria-label": "Command palette" });
    input = uxEl("input", { type: "text", class: "ux-cmdk-input", placeholder: "Search commands…  (e.g. “combo”, “run”, “drift”, “units”)",
      "aria-label": "Search commands", "aria-controls": "uxCmdkList", autocomplete: "off", spellcheck: "false", role: "combobox", "aria-expanded": "true" });
    listEl = uxEl("div", { class: "ux-cmdk-list", id: "uxCmdkList", role: "listbox" });
    const foot = uxEl("div", { class: "ux-cmdk-foot", html: "<span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> run</span><span><kbd>Esc</kbd> close</span><span class='ux-cmdk-count'></span>" });
    box.append(uxEl("div", { class: "ux-cmdk-head" }, [uxEl("span", { class: "ux-cmdk-ico", html: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="8.5" cy="8.5" r="5"/><path d="M12.3 12.3 L16.5 16.5"/></svg>' }), input]), listEl, foot);
    back.appendChild(box);
    (document.getElementById("app") || document.body).appendChild(back);
    back.addEventListener("mousedown", e => { if (e.target === back) close(); });
    input.addEventListener("input", () => { sel = 0; render(); });
    input.addEventListener("keydown", e => {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "PageDown") { e.preventDefault(); move(8); }
      else if (e.key === "PageUp") { e.preventDefault(); move(-8); }
      else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); run(items[sel]); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
      else if (e.key === "Tab") { e.preventDefault(); move(e.shiftKey ? -1 : 1); }
      e.stopPropagation();   // never leak tool keys to the workspace
    });
    listEl.addEventListener("mousemove", e => {
      const r = e.target.closest(".ux-cmdk-item");
      if (r && +r.dataset.i !== sel) { sel = +r.dataset.i; mark(); }
    });
    listEl.addEventListener("click", e => {
      const r = e.target.closest(".ux-cmdk-item");
      if (r) run(items[+r.dataset.i]);
    });
  }
  function move(d) {
    if (!items.length) return;
    sel = Math.max(0, Math.min(items.length - 1, sel + d));
    mark();
  }
  function mark() {
    listEl.querySelectorAll(".ux-cmdk-item").forEach(r => {
      const on = +r.dataset.i === sel;
      r.classList.toggle("is-sel", on);
      r.setAttribute("aria-selected", on ? "true" : "false");
      if (on) { input.setAttribute("aria-activedescendant", r.id); r.scrollIntoView({ block: "nearest" }); }
    });
  }

  let all = [];
  function render() {
    const q = input.value.trim();
    let groups;
    if (!q) {
      const rec = recent().map(id => all.find(c => c.id === id)).filter(Boolean);
      const recIds = new Set(rec.map(c => c.id));
      groups = [["Recent", rec], ["All commands", all.filter(c => !recIds.has(c.id))]];
    } else {
      const scored = [];
      for (const c of all) {
        const lab = multiScore(q, c.label + (KEYWORDS[c.act] ? " " + KEYWORDS[c.act] : ""));
        const main = multiScore(q, `${c.menu} ${c.label} ${c.hint}`);
        // descriptions: whole-word substrings only (no scattered subsequences)
        const hay = `${c.etabs} ${c.desc}`.toLowerCase();
        const words = q.toLowerCase().split(/\s+/).filter(Boolean);
        const sub = words.length && words.every(w => w.length > 2 && hay.includes(w)) ? 400 : -1;
        const s = Math.max(lab >= 0 ? lab + 150 : -1, main >= 0 ? main * 0.6 : -1, sub >= 0 ? sub * 0.3 : -1);
        if (s >= 0) scored.push([s + (recent().includes(c.id) ? 25 : 0) - (c.disabled ? 500 : 0), c]);
      }
      scored.sort((a, b) => b[0] - a[0]);
      groups = [["", scored.slice(0, 60).map(x => x[1])]];
    }
    items = [];
    const frag = document.createDocumentFragment();
    for (const [name, cmds] of groups) {
      if (!cmds.length) continue;
      if (name) frag.appendChild(uxEl("div", { class: "ux-cmdk-group", text: name }));
      for (const c of cmds) {
        const i = items.length;
        items.push(c);
        const row = uxEl("div", {
          class: "ux-cmdk-item" + (c.disabled ? " is-disabled" : ""), role: "option", id: "uxCmdk-" + i, "data-i": i,
          title: c.desc ? `${c.desc}${c.etabs ? "\nETABS: " + c.etabs : ""}` : null,
        });
        row.innerHTML = `<span class="ux-cmdk-menu">${uxEsc(c.menu)}</span>`
          + `<span class="ux-cmdk-label">${c.checked ? "✓ " : ""}${uxEsc(c.label)}${c.hint ? ` <span class="ux-cmdk-hint">${uxEsc(c.hint)}</span>` : ""}</span>`
          + (c.key ? `<kbd>${uxEsc(uxKeyLabel(c.key))}</kbd>` : "");
        frag.appendChild(row);
      }
    }
    listEl.textContent = "";
    if (!items.length) listEl.appendChild(uxEl("div", { class: "ux-cmdk-empty", text: `No command matches “${q}”. Try a shorter word, or an ETABS term such as “mass source”.` }));
    listEl.appendChild(frag);
    back.querySelector(".ux-cmdk-count").textContent = q ? `${items.length} match${items.length === 1 ? "" : "es"}` : `${all.length} commands`;
    sel = Math.min(sel, Math.max(0, items.length - 1));
    mark();
  }

  function openPalette(prefill = "") {
    if (!back) build();
    if (sky.etabs) sky.etabs.closeMenus();
    lastFocus = document.activeElement;
    all = commands();
    back.classList.remove("hidden");
    open = true;
    input.value = prefill;
    sel = 0;
    render();
    input.focus();
  }
  function close() {
    if (!open) return;
    open = false;
    back.classList.add("hidden");
    if (lastFocus && lastFocus.focus && lastFocus.isConnected && lastFocus !== document.body) { try { lastFocus.focus({ preventScroll: true }); } catch { /* ignore */ } }
  }

  sky.ux = sky.ux || {};
  sky.ux.palette = {
    open: openPalette, close, isOpen: () => open,
    toggle: () => (open ? close() : openPalette()),
    commands, search: q => { all = commands(); if (!back) build(); input.value = q; render(); return items.map(c => c.id); },
    runById: id => { const c = commands().find(x => x.id === id); if (c) { run(c); return true; } return false; },
    recent, fuzzyScore,
  };
}
