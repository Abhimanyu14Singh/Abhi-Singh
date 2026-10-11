/* SkyFrame UX — application chrome / layout (ETABS · SAP2000 · Midas layout).
   One hook line in js/ux.js (runs after the toolbar / status modules).

     ┌ title bar:  brand · menu bar ··················· model name · badges ┐
     ├ toolbar:    file │ view │ select │ draw │ assign │ run │ display  ·· search · panels
     ├ explorer │            canvas (plan / elevation / 3D / results)     │ properties
     └ status:   plane · cursor · snap · hint ······ selection · DOF · units · check · analysis

   Purely presentational: it MOVES existing nodes (menu bar into the title bar,
   Save/Discard into the toolbar, the selection Properties panel into the right
   side panel) and hides duplicates with CSS (chrome.css). Every element keeps
   its id and its handlers, so every command still runs the same code:
     · topbar Run Analysis / Report  → toolbar Run (an-run) · File › Report…
     · status-bar Plan/Elev/3D + Run → toolbar View group + Run
     · status-bar Story select       → draw-toolbar Story select (+ status readout)
     · left draw palette + sidebar tool list → toolbar Draw group (+ ▾ overflow)
     · quick-toolbar units select    → status-bar units select
     · floating sidebar tab          → toolbar panel toggle (\ still works)
   Panel collapsed states live in localStorage (try/catch via ux_common lsGet/lsSet). */

import { el as chEl, icon as chIcon, lsGet as chLsGet, lsSet as chLsSet } from "./ux_common.js";
import { openQatPopup as chOpenPopup } from "./ux_qat.js";

const LS_EXPLORER = "skyframe.chrome.explorerCollapsed";
const LS_PROPS = "skyframe.chrome.propsCollapsed";       // {model, loads, analyze}
const PROPS_DEFAULT = { model: false, loads: false, analyze: true };
const PROPS_TITLE = { model: "Properties", loads: "Load definitions", analyze: "Quick model" };

let cssDone = false;
function ensureChromeCss() {
  if (cssDone || document.querySelector("link[data-sf-chrome-css]")) { cssDone = true; return; }
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/chrome.css"; l.setAttribute("data-sf-chrome-css", "1");
  document.head.appendChild(l);
  cssDone = true;
}

export function installChrome(sky) {
  const S = sky.store;
  const $ = id => document.getElementById(id);
  const root = document.documentElement;
  ensureChromeCss();
  root.classList.add("sf-chrome");
  const resize = () => setTimeout(() => sky.viewer && sky.viewer._resize && sky.viewer._resize(), 30);

  /* ================= title bar = brand + menu bar + model chip ================= */
  const topbar = document.querySelector(".topbar");
  const menubar = $("etabsMenubar");
  if (topbar && menubar) {
    topbar.classList.add("sf-titlebar");
    const brand = topbar.querySelector(".brand");
    if (brand) brand.after(menubar); else topbar.prepend(menubar);
    const brandName = topbar.querySelector(".brand-name");
    const tag = topbar.querySelector(".brand-tag");
    if (brand && tag) brand.title = tag.textContent.trim();
    if (brandName) brandName.setAttribute("aria-label", "SkyFrame");
  }

  /* ================= toolbar extras: Save/Discard, panel toggles, More ================= */
  const tb = sky.ux && sky.ux.toolbar;
  const bar = tb && tb.el;
  const right = bar && bar.querySelector(".sf-tb-right");
  const left = bar && bar.querySelector(".sf-tb-left");
  const actions = $("modelActions");
  const mkBtn = (cls, ico, label, extra = {}) => chEl("button", {
    type: "button", class: "ux-qat-btn " + cls, title: label, "aria-label": label, html: chIcon(ico, "ux-qat-ico"), ...extra,
  });
  const moreBtn = mkBtn("sf-tb-overflow hidden", "sf-more", "More tools", { "aria-haspopup": "menu", "aria-expanded": "false" });
  const exBtn = mkBtn("sf-tb-panel", "sf-panel-left", "Model Explorer — show / hide", { "aria-pressed": "true", "data-qat": "explorer" });
  const prBtn = mkBtn("sf-tb-panel", "sf-panel-right", "Properties panel — show / hide (\\)", { "aria-pressed": "true", "data-qat": "props" });
  if (right) {
    right.prepend(moreBtn);
    if (actions) { actions.classList.add("sf-tb-actions"); moreBtn.after(actions); }
    right.append(chEl("span", { class: "ux-qat-sep", "aria-hidden": "true" }), exBtn, prBtn);
  }

  /* ================= Model Explorer: header with filter + collapse ================= */
  const explorer = $("etabsExplorer");
  const workspace = document.querySelector(".workspace");
  const exHead = explorer && explorer.querySelector(".ex-head");
  let exFilter = null;
  if (exHead) {
    exHead.classList.add("sf-ex-head");
    const hideBtn = chEl("button", { type: "button", class: "sf-icon-btn sf-ex-hide", title: "Hide Model Explorer", "aria-label": "Hide Model Explorer", html: chIcon("sf-panel-left", "sf-ico") });
    hideBtn.addEventListener("click", () => toggleExplorer(true));
    exHead.appendChild(hideBtn);
    exFilter = chEl("input", { type: "search", class: "sf-ex-filter", placeholder: "Filter explorer…", "aria-label": "Filter the Model Explorer", spellcheck: "false", autocomplete: "off" });
    const wrap = chEl("div", { class: "sf-ex-search" }, [chEl("span", { class: "sf-ex-search-ico", html: chIcon("ux-search", "sf-ico") }), exFilter]);
    exHead.after(wrap);
    exFilter.addEventListener("input", applyFilter);
    exFilter.addEventListener("keydown", e => {
      if (e.key === "Escape" && exFilter.value) { exFilter.value = ""; applyFilter(); e.stopPropagation(); e.preventDefault(); }
      else if (e.key === "ArrowDown" || e.key === "Enter") {
        const first = [...explorer.querySelectorAll(".ex-body .ex-leaf, .ex-body .ex-group-head")].find(n => n.offsetParent !== null && !n.closest(".sf-nomatch"));
        if (first) { e.preventDefault(); if (e.key === "Enter" && exFilter.value && first.classList.contains("ex-leaf")) first.click(); else first.focus(); }
      }
    });
  }
  function applyFilter() {
    if (!explorer || !exFilter) return;
    const q = exFilter.value.trim().toLowerCase();
    explorer.classList.toggle("sf-filtering", !!q);
    let hits = 0;
    explorer.querySelectorAll(".ex-body .ex-group").forEach(g => {
      const head = g.querySelector(":scope > .ex-group-head");
      const gName = head ? head.textContent.trim().toLowerCase() : "";
      const gHit = !!q && gName.includes(q);
      let any = false;
      g.querySelectorAll(".ex-leaf").forEach(l => {
        const m = !q || gHit || l.textContent.trim().toLowerCase().includes(q);
        l.classList.toggle("sf-nomatch", !m);
        if (m) any = true;
      });
      const show = !q || gHit || any;
      g.classList.toggle("sf-nomatch", !show);
      if (show && q) hits++;
    });
    explorer.classList.toggle("sf-filter-empty", !!q && !hits);
  }
  const exBody = explorer && explorer.querySelector(".ex-body");
  if (exBody) {
    exBody.appendChild(chEl("div", { class: "sf-ex-empty muted", text: "No matching items" }));
    let q = 0;
    new MutationObserver(recs => {
      if (!exFilter || !exFilter.value || recs.every(r => r.type === "attributes")) return;
      if (!q) q = requestAnimationFrame(() => { q = 0; applyFilter(); });
    }).observe(exBody, { childList: true, subtree: true });
  }

  const exCollapsed = () => !!(workspace && workspace.classList.contains("explorer-collapsed"));
  function toggleExplorer(save = true, want = null) {
    if (!sky.etabs || !sky.etabs.toggleExplorer) return exCollapsed();
    const target = want == null ? !exCollapsed() : !!want;
    if (target !== exCollapsed()) sky.etabs.toggleExplorer();   // etabs.js handler (class + viewer resize)
    if (save) chLsSet(LS_EXPLORER, exCollapsed());
    syncPanels();
    scheduleFit();
    return exCollapsed();
  }
  exBtn.addEventListener("click", () => toggleExplorer(true));
  if (workspace) new MutationObserver(() => { syncPanels(); }).observe(workspace, { attributes: true, attributeFilter: ["class"] });

  /* ================= right side panel (context-sensitive properties) ================= */
  const sidebar = $("sidebar");
  const sideToggle = $("sidebarToggle");
  let prTitle = null;
  if (sidebar) {
    sidebar.classList.add("sf-props");
    sidebar.setAttribute("aria-label", "Properties");
    prTitle = chEl("span", { class: "sf-props-title", text: "Properties" });
    const hide = chEl("button", { type: "button", class: "sf-icon-btn", title: "Hide panel (\\)", "aria-label": "Hide properties panel", html: chIcon("sf-panel-right", "sf-ico") });
    hide.addEventListener("click", () => setProps(true));
    sidebar.prepend(chEl("div", { class: "sf-props-head" }, [prTitle, hide]));
    // selection Properties (Model mode) joins the right panel, above the draw options
    const props = $("propsPanel"), drawSide = $("drawSidebar");
    if (props && drawSide) {
      props.classList.add("sf-props-sel");
      drawSide.prepend(props);
      const firstOpt = drawSide.querySelector("#braceOpts") || drawSide.querySelector(".group-title");
      if (firstOpt) firstOpt.before(chEl("h3", { class: "sf-section-title", text: "Draw options" }));
    }
  }
  const propsCollapsed = () => !!(sidebar && sidebar.classList.contains("collapsed"));
  const modeKey = () => (S.mode === "model" || S.mode === "loads" ? S.mode : "analyze");
  const savedProps = () => ({ ...PROPS_DEFAULT, ...(chLsGet(LS_PROPS, {}) || {}) });
  let applying = false;
  function setProps(collapsed, save = true) {
    if (!sidebar || !sideToggle) return;
    if (collapsed !== propsCollapsed()) { applying = !save; sideToggle.click(); applying = false; }   // app.js handler
    if (save) { const s = savedProps(); s[modeKey()] = propsCollapsed(); chLsSet(LS_PROPS, s); }
    syncPanels(); scheduleFit();
  }
  prBtn.addEventListener("click", () => setProps(!propsCollapsed()));
  // "\" (app.js) or any other caller toggling the sidebar → remember it for this mode
  if (sidebar) new MutationObserver(() => {
    if (!applying) { const s = savedProps(); s[modeKey()] = propsCollapsed(); chLsSet(LS_PROPS, s); }
    syncPanels(); resize();
  }).observe(sidebar, { attributes: true, attributeFilter: ["class"] });
  let lastMode = null;
  function onMode() {
    const m = modeKey();
    if (m === lastMode) return;
    lastMode = m;
    const want = !!savedProps()[m];
    if (want !== propsCollapsed()) setProps(want, false);
    syncPanels();
  }

  function syncPanels() {
    const exOn = !exCollapsed(), prOn = !propsCollapsed();
    exBtn.classList.toggle("is-active", exOn); exBtn.setAttribute("aria-pressed", String(exOn));
    prBtn.classList.toggle("is-active", prOn); prBtn.setAttribute("aria-pressed", String(prOn));
    if (prTitle) {
      const m = modeKey();
      let t = PROPS_TITLE[m];
      if (m === "analyze") {
        const h = document.querySelector("#analyzeSidebar .panel-title");
        if (h && h.textContent.trim()) t = h.textContent.trim();
      }
      if (prTitle.textContent !== t) prTitle.textContent = t;
    }
  }

  /* ================= status bar: plane readout, single story/view source ================= */
  const status = $("etabsStatus");
  const plane = chEl("span", { class: "sb-item sf-sb-plane", id: "sfSbPlane", title: "Active view · story / plane" });
  if (status) {
    const storyItem = status.querySelector(".sb-select:not(.sb-units-sel)");
    const storyWrap = storyItem && storyItem.closest(".sb-item");
    if (storyWrap) { storyWrap.classList.add("sf-sb-story"); storyWrap.before(plane); }
    else status.prepend(plane);
    const seg = status.querySelector(".sb-seg"); if (seg) seg.classList.add("sf-moved");
    const run = status.querySelector(".sb-run"); if (run) run.classList.add("sf-moved");
    const coord = status.querySelector(".sb-coord");
    if (coord) coord.closest(".sb-item")?.classList.add("sf-sb-coord");
  }
  function syncPlane() {
    let t;
    if (S.mode === "model") {
      if (S.view === "elev") { const l = $("elevLineSelect"); t = `Elevation · ${l && l.value ? "Grid " + l.value : "grid line"}`; }
      else t = `Plan · ${S.story || "—"}`;
    } else if (S.mode === "loads") t = "Load definitions";
    else {
      const tab = document.querySelector(".tabbar .tab.is-active");
      t = S.tab === "view3d" || !tab ? "3D view" : tab.textContent.trim();
    }
    if (plane.textContent !== t) plane.textContent = t;
  }

  /* ================= canvas toolbars: hints → info icon, glyphs → SVG ================= */
  const infoFor = span => {
    if (!span || span.dataset.sfInfo) return;
    span.dataset.sfInfo = "1";
    span.classList.add("sf-hint-src");
    const i = chEl("span", { class: "sf-hint-ico", tabindex: "0", role: "note", html: chIcon("sf-info", "sf-ico") });
    const sync = () => { const t = span.textContent.trim(); i.title = t; i.setAttribute("aria-label", "Navigation: " + t); };
    span.after(i);
    sync();
    new MutationObserver(sync).observe(span, { childList: true, characterData: true, subtree: true });
  };
  document.querySelectorAll(".viewer-toolbar > .hint, .draw-toolbar > .hint").forEach(infoFor);
  const up = $("storyUp"), down = $("storyDown");
  if (up) { up.innerHTML = chIcon("sf-chev-up", "sf-ico"); up.setAttribute("aria-label", "Story above"); }
  if (down) { down.innerHTML = chIcon("sf-chev-down", "sf-ico"); down.setAttribute("aria-label", "Story below"); }

  /* ================= toolbar fit: compact, then fold low-priority groups ================= */
  const groupEls = () => (left ? [...left.querySelectorAll(":scope > .sf-tb-grp")] : []);
  const overflowing = () => !!left && left.scrollWidth > left.clientWidth + 1;
  let folded = [];
  function fit() {
    if (!bar || !left || bar.classList.contains("hidden")) return;
    bar.classList.remove("sf-compact");
    groupEls().forEach(g => g.classList.remove("sf-folded"));
    moreBtn.classList.add("hidden");
    folded = [];
    if (!overflowing()) return;
    bar.classList.add("sf-compact");
    if (!overflowing()) return;
    moreBtn.classList.remove("hidden");
    const order = groupEls().filter(g => +g.dataset.prio > 0)
      .sort((a, b) => (+b.dataset.prio - +a.dataset.prio) || (groupEls().indexOf(b) - groupEls().indexOf(a)));
    for (const g of order) {
      if (!overflowing()) break;
      g.classList.add("sf-folded");
      folded.push(g);
    }
  }
  let fitQ = 0;
  function scheduleFit() { if (!fitQ) fitQ = requestAnimationFrame(() => { fitQ = 0; fit(); }); }
  moreBtn.addEventListener("click", e => {
    e.stopPropagation();
    const rows = [];
    for (const g of groupEls().filter(x => x.classList.contains("sf-folded"))) {
      const def = g._def || {};
      rows.push({ head: def.label || g.dataset.grp });
      g.querySelectorAll(".sf-tb-inner > .ux-qat-btn").forEach(b => {
        if (b.classList.contains("hidden")) return;
        rows.push({ label: b._label || b.getAttribute("aria-label"), hint: b._key || "", ico: b._icon, disabled: b.disabled, checked: b.classList.contains("is-active"), run: () => b.click() });
      });
      if (def.menu) rows.push({ label: `All ${def.label} commands…`, ico: "sf-chev-down", run: () => sky.etabs && sky.etabs.openMenu(def.menu) });
    }
    chOpenPopup(moreBtn, rows, { label: "More tools" });
  });
  if (bar && "ResizeObserver" in window) new ResizeObserver(scheduleFit).observe(bar);
  if (actions) new MutationObserver(scheduleFit).observe(actions, { attributes: true, attributeFilter: ["class"] });
  // a button appearing / disappearing (undo, assign …) changes the width; is-active / is-open do not
  const hasHidden = v => /(^|\s)hidden(\s|$)/.test(v || "");
  if (left) new MutationObserver(recs => {
    if (recs.some(r => r.target.classList && r.target.classList.contains("ux-qat-btn") && hasHidden(r.oldValue) !== r.target.classList.contains("hidden"))) scheduleFit();
  }).observe(left, { attributes: true, attributeFilter: ["class"], attributeOldValue: true, subtree: true });

  /* ================= event wiring ================= */
  let raf = 0;
  const later = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; onMode(); syncPlane(); syncPanels(); }); };
  const modeObs = new MutationObserver(later);
  for (const id of ["drawMain", "loadsMain", "analyzeMain", "planSvg", "elevSvg"]) { const n = $(id); if (n) modeObs.observe(n, { attributes: true, attributeFilter: ["class"] }); }
  document.querySelectorAll(".tabbar .tab").forEach(t => modeObs.observe(t, { attributes: true, attributeFilter: ["class"] }));
  const badge = $("planStoryBadge");
  if (badge) modeObs.observe(badge, { childList: true, characterData: true, subtree: true });
  const els = $("elevLineSelect");
  if (els) els.addEventListener("change", later);
  for (const ev of ["sky:model-changed", "sky:results-changed"]) document.addEventListener(ev, later);
  document.addEventListener("click", later, true);
  document.addEventListener("keyup", later, true);

  {
    const saved = chLsGet(LS_EXPLORER, null);
    if (saved != null) toggleExplorer(false, !!saved);   // otherwise ux.js narrow-window default stands
  }
  onMode(); syncPlane(); syncPanels(); fit();
  setTimeout(() => { fit(); resize(); }, 0);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(scheduleFit).catch(() => {});

  sky.ux = sky.ux || {};
  sky.ux.chrome = {
    toggleExplorer: () => toggleExplorer(true),
    toggleProps: () => setProps(!propsCollapsed()),
    setProps: c => setProps(!!c),
    explorerCollapsed: exCollapsed, propsCollapsed,
    filterExplorer: q => { if (exFilter) { exFilter.value = q || ""; applyFilter(); } },
    fit, folded: () => folded.map(g => g.dataset.grp),
  };
}
