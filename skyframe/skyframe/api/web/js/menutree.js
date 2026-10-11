/* SkyFrame — cascading menu bar (menu architecture, v1.20).

   Renders the ETABS-style menu TREE declared in js/etabs.js:
     · top-level menus → drop-down panels → cascading submenus (any depth),
       separators between task groups, an optional section header (File ›
       Recent), "…" only on items that open a dialog, keyboard shortcuts
       right-aligned, check / radio marks, disabled states with a tooltip
       that says WHY (e.g. result displays before a run);
     · mouse (hover-intent + click), touch (tap toggles a submenu) and
       keyboard (WAI-ARIA menubar: ← → ↑ ↓ Home End Enter Space Esc,
       type-ahead, Alt+letter accelerators with underlines shown while Alt
       is held);
     · every panel is clamped to the viewport (submenus flip to the left at
       the right edge and shift up at the bottom edge);
     · a registry so modules can add commands at runtime:
         __sky.menu.add("Assign/Frame Loads", { label: "Wind…", act, fn })
       unknown paths land in that menu's "More" submenu (or Tools › More).

   Every item keeps its `act` id and stays a `.etabs-menu-item[data-act]`
   button INSIDE its `.etabs-menu-wrap`, so the command palette, toolbar,
   shortcuts, tests and the routing in js/ux_menus.js work unchanged.
   Activation always goes through a DOM click on the row (keyboard too), so
   capture-phase routes installed by other modules still apply. */

import { HELP as mtHELP } from "./helpdocs.js";

const MT_CSS_ID = "skyMenuTreeCss";
const HOVER_OPEN_MS = 140;
const HOVER_CLOSE_MS = 260;
const EDGE = 4;
let mtUid = 0;

function mtEnsureCss() {
  if (document.getElementById(MT_CSS_ID)) return;
  const l = document.createElement("link");
  l.id = MT_CSS_ID; l.rel = "stylesheet"; l.href = "/static/menutree.css";
  document.head.appendChild(l);
}

const mtEl = (tag, attrs = {}, kids = []) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "html") n.innerHTML = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  (Array.isArray(kids) ? kids : [kids]).forEach(c => c != null && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
  return n;
};
const valOf = v => (typeof v === "function" ? v() : v);
const norm = s => String(s || "").toLowerCase().replace(/[…….]+$/, "").replace(/[^a-z0-9]+/g, " ").trim();

/**
 * Build the menu bar.
 * @param {object} o
 *   menubar  — the #etabsMenubar element
 *   menus    — [{ name, accel, items: [node…] }]; node = { sep } | { header } |
 *              { label, sub: [node…] } | { label, act, fn, key, hint, check,
 *              radio, needs, dialog }
 *   iconFor  — act → svg html (optional leading glyph)
 *   needs    — { name: () => reason|null } named disabled rules
 *   dynamic  — { name: () => [node…] } dynamic sections (e.g. "recent")
 */
export function buildMenuTree(o) {
  mtEnsureCss();
  const { menubar } = o;
  const menus = o.menus;
  const actMap = {};            // act → item
  const pathOf = {};            // act → ["Assign", "Frame", …]
  const rowOf = {};             // act → row element
  let openWrap = null;          // open top-level wrap
  const stack = [];             // open submenu panels (deepest last)
  let hoverT = 0;
  let kbMode = false;           // last interaction from the keyboard

  /* ------------------------------------------------------------ disabled */
  const reasonOf = item => {
    if (!item.needs) return null;
    const rules = [].concat(item.needs);
    for (const r of rules) {
      const fn = typeof r === "function" ? r : o.needs && o.needs[r];
      const why = fn ? fn(item) : null;
      if (why) return why;
    }
    return null;
  };
  const baseTitle = (item, label) => {
    const h = mtHELP[item.act];
    const extra = valOf(item.hint);
    return [h ? h.text : label, extra && (!h || !h.text.includes(extra)) ? extra : null, h ? `ETABS: ${h.etabs}` : null]
      .filter(Boolean).join("\n");
  };
  function refreshRow(row) {
    const item = actMap[row.dataset.act];
    if (!item) return;
    if (item.check || item.radio) {
      const on = !!valOf(item.check || item.radio);
      row.classList.toggle("checked", on);
      row.setAttribute("aria-checked", on ? "true" : "false");
    }
    const why = reasonOf(item);
    row.classList.toggle("is-disabled", !!why);
    if (why) row.setAttribute("aria-disabled", "true"); else row.removeAttribute("aria-disabled");
    const lbl = row.dataset.mtLabel;
    const t = baseTitle(item, lbl);
    row.title = why ? `Unavailable — ${why}\n${t}` : t;
    row.dataset.uxTipV = "2";   // js/ux_menus.js: tooltip already set
    const k = row.querySelector(".mi-key");
    if (k && typeof item.key === "function") k.textContent = item.key() || "";
  }
  const refreshPanel = panel => panel.querySelectorAll(":scope > .etabs-menu-item[data-act]").forEach(refreshRow);
  const refreshAll = () => menubar.querySelectorAll(".etabs-menu-item[data-act]").forEach(refreshRow);

  /* ------------------------------------------------------------ rendering */
  function accelLabel(name, letter) {
    // one inline span (the button is a flex box with a gap between children)
    const i = letter ? name.toLowerCase().indexOf(letter.toLowerCase()) : -1;
    if (i < 0) return mtEl("span", { class: "mb-label", text: name });
    return mtEl("span", { class: "mb-label" }, [name.slice(0, i), mtEl("span", { class: "sky-accel", text: name[i] }), name.slice(i + 1)].filter(Boolean));
  }

  function renderItem(item, path) {
    if (item.act in actMap && actMap[item.act] !== item) {
      console.warn(`menu: duplicate act "${item.act}" ignored`);
      return null;
    }
    actMap[item.act] = item;
    pathOf[item.act] = path.slice();
    const label = valOf(item.label);
    const lbl = mtEl("span", { class: "mi-label" });
    const ico = o.iconFor && o.iconFor(item.act);
    if (ico) lbl.insertAdjacentHTML("afterbegin", ico);
    // context prefix (hidden in the menu, visible to the command palette
    // and text searches: "Frame › Section Property")
    if (path.length > 1) lbl.appendChild(mtEl("span", { class: "mi-ctx", text: path.slice(1).join(" › ") + " › " }));
    lbl.appendChild(document.createTextNode(label));
    const key = valOf(item.key);
    const row = mtEl("button", {
      type: "button",
      class: "etabs-menu-item" + (item.radio ? " is-radio" : ""),
      role: item.check ? "menuitemcheckbox" : item.radio ? "menuitemradio" : "menuitem",
      "data-act": item.act, tabindex: "-1",
      "data-ux-lbl": item.oldLabel || label,     // js/ux_menus.js: keep this label (no relabel)
      "data-menu-path": path.join(" › "),
      "aria-label": (path.length > 1 ? path.slice(1).join(" › ") + " › " : "") + label + (key ? ` (${key})` : ""),
    }, [
      mtEl("span", { class: "mi-check", text: item.radio ? "●" : "✓", "aria-hidden": "true" }),
      lbl,
      mtEl("span", { class: "mi-key", text: key || "" }),
    ]);
    row.dataset.mtLabel = label;
    if (key) row.setAttribute("aria-keyshortcuts", String(key).replace(/Ctrl/g, "Control"));
    row.addEventListener("click", e => {
      if (row.classList.contains("is-disabled")) { e.preventDefault(); return; }
      closeMenus();
      item.fn && item.fn();
    });
    row.addEventListener("pointerenter", e => { if (e.pointerType !== "touch") hoverItem(row); });
    rowOf[item.act] = row;
    refreshRow(row);
    return row;
  }

  function renderPanel(panel, nodes, path) {
    panel.textContent = "";
    let lastSep = true;
    for (const node of nodes) {
      if (!node) continue;
      if (node.sep) {
        if (!lastSep) panel.appendChild(mtEl("div", { class: "etabs-menu-sep", role: "separator" }));
        lastSep = true;
        continue;
      }
      if (node.dynamic) {
        const kids = (o.dynamic && o.dynamic[node.dynamic] && o.dynamic[node.dynamic]()) || [];
        const box = mtEl("div", { class: "sky-menu-dyn", "data-dyn": node.dynamic, role: "none" });
        kids.forEach(k => box.appendChild(k));
        panel.appendChild(box);
        lastSep = false;
        continue;
      }
      if (node.header) { panel.appendChild(mtEl("div", { class: "sky-menu-header", role: "presentation", text: node.header })); lastSep = false; continue; }
      if (node.sub) {
        if (node.hideEmpty && !node.sub.some(n => n && !n.sep)) continue;
        panel.appendChild(renderTrigger(node, path));
        lastSep = false;
        continue;
      }
      const row = renderItem(node, path);
      if (row) { panel.appendChild(row); lastSep = false; }
    }
    const last = panel.lastElementChild;
    if (last && last.classList.contains("etabs-menu-sep")) last.remove();
  }

  function renderTrigger(node, path) {
    const id = "skySub" + (++mtUid);
    const label = valOf(node.label);
    const trig = mtEl("button", {
      type: "button", class: "etabs-menu-item sky-sub-trigger", role: "menuitem", tabindex: "-1",
      "aria-haspopup": "menu", "aria-expanded": "false", "aria-controls": id,
      "data-sub": label, "data-menu-path": path.join(" › "),
    }, [
      mtEl("span", { class: "mi-check", "aria-hidden": "true" }),
      mtEl("span", { class: "mi-label", text: label }),
      mtEl("span", { class: "mi-chev", "aria-hidden": "true" }),
    ]);
    const panel = mtEl("div", { class: "sky-submenu hidden", role: "menu", id, "aria-label": label, "data-depth": String(path.length) });
    node._panel = panel; node._trigger = trig;
    const subPath = [...path, label];
    renderPanel(panel, node.sub, subPath);
    trig._panel = panel;
    trig.title = node.title || `${label} ▸`;
    trig.addEventListener("pointerenter", e => {
      if (e.pointerType === "touch") return;
      hoverItem(trig);
    });
    let lastPointer = "mouse";
    trig.addEventListener("pointerdown", e => { lastPointer = e.pointerType || "mouse"; });
    trig.addEventListener("click", e => {
      e.stopPropagation();
      clearTimeout(hoverT);
      // touch: a second tap closes; mouse: a click on a hover-opened trigger keeps it open
      if (trig.getAttribute("aria-expanded") === "true" && e.detail !== 0 && lastPointer === "touch") closeFrom(panel);
      else openSub(trig, e.detail === 0);
    });
    panel.addEventListener("pointerenter", () => clearTimeout(hoverT));
    // the panel stays inside the top-level wrap in the DOM (palette / tests), positioned fixed
    return mtEl("div", { class: "sky-sub-host", role: "none" }, [trig, panel]);
  }

  /* ------------------------------------------------------------ opening */
  const panelOfRow = row => row.closest(".sky-submenu, .etabs-menu");
  const depthOf = panel => stack.indexOf(panel);   // -1 for the top panel

  function closeFrom(panel) {
    // close `panel` and everything opened after it
    const i = stack.indexOf(panel);
    if (i < 0) return;
    for (const p of stack.splice(i)) {
      p.classList.add("hidden");
      const t = p.previousElementSibling;
      if (t) { t.setAttribute("aria-expanded", "false"); t.classList.remove("is-open"); }
    }
  }
  function closeSubsAbove(parentPanel) {
    // close submenus that are deeper than parentPanel
    const i = parentPanel && parentPanel.classList.contains("sky-submenu") ? stack.indexOf(parentPanel) + 1 : 0;
    if (i < stack.length) closeFrom(stack[i]);
  }

  function place(panel, trig) {
    const vw = document.documentElement.clientWidth || window.innerWidth;
    const vh = window.innerHeight;
    panel.style.maxHeight = (vh - 2 * EDGE) + "px";
    panel.style.left = "0px"; panel.style.top = "0px";
    // measure where (0,0) lands (fixed containing block may not be the viewport)
    const origin = panel.getBoundingClientRect();
    const w = panel.offsetWidth, h = panel.offsetHeight;
    const tr = trig.getBoundingClientRect();
    const pr = panelOfRow(trig).getBoundingClientRect();
    let x = pr.right - 3, flipped = false;
    if (x + w > vw - EDGE) { x = pr.left - w + 3; flipped = true; }
    if (x < EDGE) x = Math.max(EDGE, Math.min(vw - EDGE - w, x));
    let y = tr.top - 5;
    if (y + h > vh - EDGE) y = vh - EDGE - h;
    if (y < EDGE) y = EDGE;
    panel.style.left = (x - origin.left) + "px";
    panel.style.top = (y - origin.top) + "px";
    panel.classList.toggle("is-flipped", flipped);
  }
  function placeTop(wrap) {
    const dd = wrap.querySelector(":scope > .etabs-menu");
    dd.style.left = ""; dd.style.maxHeight = "";
    const vw = document.documentElement.clientWidth || window.innerWidth;
    const r = dd.getBoundingClientRect();
    if (r.right > vw - EDGE) dd.style.left = Math.min(0, vw - EDGE - r.right) + "px";
    const avail = window.innerHeight - r.top - EDGE;
    if (r.height > avail) dd.style.maxHeight = Math.max(160, avail) + "px";
  }

  function openSub(trig, focusFirst) {
    const panel = trig._panel;
    const parent = panelOfRow(trig);
    closeSubsAbove(parent);
    if (!panel.classList.contains("hidden") && stack.includes(panel)) {
      if (focusFirst) focusIn(panel, 0);
      return;
    }
    panel.classList.remove("hidden");
    refreshPanel(panel);
    refreshDynamic(panel);
    trig.setAttribute("aria-expanded", "true");
    trig.classList.add("is-open");
    stack.push(panel);
    place(panel, trig);
    if (focusFirst) focusIn(panel, 0);
  }

  function hoverItem(row) {
    if (kbMode) kbMode = false;
    const parent = panelOfRow(row);
    clearTimeout(hoverT);
    if (row.classList.contains("sky-sub-trigger")) {
      if (row.getAttribute("aria-expanded") === "true") { closeSubsAbove(row._panel); return; }
      hoverT = setTimeout(() => openSub(row, false), HOVER_OPEN_MS);
    } else {
      // leaving a branch: close sibling submenus after a grace period
      const deeper = parent.classList.contains("sky-submenu") ? stack.indexOf(parent) + 1 : 0;
      if (deeper < stack.length) hoverT = setTimeout(() => closeSubsAbove(parent), HOVER_CLOSE_MS);
    }
  }

  function closeMenus() {
    clearTimeout(hoverT);
    if (stack.length) closeFrom(stack[0]);
    if (!openWrap) return;
    openWrap.classList.remove("open");
    openWrap.querySelector(":scope > .etabs-menu").classList.add("hidden");
    openWrap.querySelector(".etabs-menu-btn").setAttribute("aria-expanded", "false");
    openWrap = null;
  }
  function openMenuWrap(wrap, focusFirst) {
    if (openWrap !== wrap) {
      closeMenus();
      wrap.classList.add("open");
      const dd = wrap.querySelector(":scope > .etabs-menu");
      dd.classList.remove("hidden");
      refreshPanel(dd);
      refreshDynamic(dd);
      wrap.querySelector(".etabs-menu-btn").setAttribute("aria-expanded", "true");
      openWrap = wrap;
      placeTop(wrap);
    }
    if (focusFirst) focusIn(wrap.querySelector(":scope > .etabs-menu"), focusFirst === "last" ? -1 : 0);
  }

  function refreshDynamic(panel) {
    panel.querySelectorAll(":scope > .sky-menu-dyn").forEach(box => {
      const f = o.dynamic && o.dynamic[box.dataset.dyn];
      if (!f) return;
      box.textContent = "";
      (f() || []).forEach(k => box.appendChild(k));
    });
  }

  /* ------------------------------------------------------------ keyboard */
  const rowsOf = panel => [...panel.querySelectorAll(":scope > .etabs-menu-item, :scope > .sky-sub-host > .sky-sub-trigger, :scope > .sky-menu-dyn > .etabs-menu-item")];
  const enabledRows = panel => rowsOf(panel).filter(r => !r.classList.contains("is-disabled"));
  function focusIn(panel, idx) {
    const rows = enabledRows(panel);
    if (!rows.length) return;
    const r = rows[(idx + rows.length) % rows.length];
    r.focus({ preventScroll: true });
    r.scrollIntoView({ block: "nearest" });
  }
  const wraps = () => [...menubar.querySelectorAll(":scope > .etabs-menu-wrap")];
  function moveTop(delta, focusFirst) {
    const all = wraps();
    const cur = openWrap || document.activeElement.closest(".etabs-menu-wrap");
    const i = all.indexOf(cur);
    const nw = all[(i + delta + all.length) % all.length];
    if (openWrap || focusFirst) openMenuWrap(nw, focusFirst);
    else nw.querySelector(".etabs-menu-btn").focus();
    if (!focusFirst) nw.querySelector(".etabs-menu-btn").focus();
  }

  function onKey(e) {
    const t = e.target;
    const btn = t.closest && t.closest(".etabs-menu-btn");
    const row = t.closest && t.closest(".etabs-menu-item");
    if (!btn && !row) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    kbMode = true;
    const k = e.key;
    let handled = true;
    if (btn) {
      const w = btn.closest(".etabs-menu-wrap");
      if (k === "ArrowDown" || k === "Enter" || k === " ") openMenuWrap(w, true);
      else if (k === "ArrowUp") openMenuWrap(w, "last");
      else if (k === "ArrowRight") moveTop(1, !!openWrap);
      else if (k === "ArrowLeft") moveTop(-1, !!openWrap);
      else if (k === "Escape") { if (openWrap) closeMenus(); else { btn.blur(); handled = true; } }
      else if (k === "Home") wraps()[0].querySelector(".etabs-menu-btn").focus();
      else if (k === "End") wraps().slice(-1)[0].querySelector(".etabs-menu-btn").focus();
      else handled = false;
    } else {
      const panel = panelOfRow(row);
      const rows = enabledRows(panel);
      const i = rows.indexOf(row);
      const isTop = panel.classList.contains("etabs-menu");
      const isTrig = row.classList.contains("sky-sub-trigger");
      switch (k) {
        case "ArrowDown": focusIn(panel, i + 1); break;
        case "ArrowUp": focusIn(panel, i < 0 ? -1 : i - 1); break;
        case "Home": focusIn(panel, 0); break;
        case "End": focusIn(panel, -1); break;
        case "ArrowRight":
          if (isTrig) openSub(row, true);
          else moveTop(1, true);
          break;
        case "ArrowLeft":
          if (!isTop) { const trig = panel.previousElementSibling; closeFrom(panel); trig && trig.focus(); }
          else moveTop(-1, true);
          break;
        case "Enter": case " ":
          if (isTrig) openSub(row, true);
          else if (!row.classList.contains("is-disabled")) {
            const w = openWrap;
            row.click();                                       // detail 0 = keyboard
            if (!openWrap && w && document.activeElement === row) w.querySelector(".etabs-menu-btn").blur();
          }
          break;
        case "Escape":
          if (!isTop) { const trig = panel.previousElementSibling; closeFrom(panel); trig && trig.focus(); }
          else { const w = openWrap; closeMenus(); w && w.querySelector(".etabs-menu-btn").focus(); }
          break;
        case "Tab": closeMenus(); handled = false; break;
        default:
          if (k.length === 1 && /\S/.test(k)) {
            const ch = k.toLowerCase();
            const rot = rows.slice(i + 1).concat(rows.slice(0, i + 1));
            const hit = rot.find(r => (r.dataset.mtLabel || r.dataset.sub || r.textContent).trim().toLowerCase().startsWith(ch));
            if (hit) hit.focus(); else handled = false;
          } else handled = false;
      }
    }
    if (handled) { e.preventDefault(); e.stopImmediatePropagation(); }
  }

  /* ------------------------------------------------------------ build */
  function buildTop(menu) {
    const name = menu.name;
    const btn = mtEl("button", {
      type: "button", class: "etabs-menu-btn", role: "menuitem", "aria-haspopup": "menu", "aria-expanded": "false",
      "data-menu": name, "aria-keyshortcuts": menu.accel ? `Alt+${menu.accel.toUpperCase()}` : null,
    }, accelLabel(name, menu.accel));
    btn.title = `${name} menu${menu.accel ? ` (Alt+${menu.accel.toUpperCase()})` : ""} · Ctrl+K searches every command`;
    const dd = mtEl("div", { class: "etabs-menu hidden", role: "menu", "aria-label": name });
    const wrap = mtEl("div", { class: "etabs-menu-wrap", role: "none" }, [btn, dd]);
    menu._wrap = wrap; menu._panel = dd;
    renderPanel(dd, menu.items, [name]);
    btn.addEventListener("click", e => {
      e.stopPropagation();
      kbMode = e.detail === 0;
      openWrap === wrap ? closeMenus() : openMenuWrap(wrap, e.detail === 0);
    });
    btn.addEventListener("pointerenter", e => { if (e.pointerType !== "touch" && openWrap && openWrap !== wrap) openMenuWrap(wrap); });
    dd.addEventListener("scroll", () => { if (stack.length) closeFrom(stack[0]); }, { passive: true });
    return wrap;
  }
  function rebuildTop(menu) {
    // re-render one top-level menu (after a runtime add); keeps the wrap element
    for (const [a, p] of Object.entries(pathOf)) if (p[0] === menu.name) { delete actMap[a]; delete pathOf[a]; delete rowOf[a]; }
    const wasOpen = openWrap === menu._wrap;
    if (wasOpen) closeMenus();
    renderPanel(menu._panel, menu.items, [menu.name]);
    if (wasOpen) openMenuWrap(menu._wrap);
  }

  menubar.textContent = "";
  menubar.setAttribute("role", "menubar");
  menus.forEach(m => menubar.appendChild(buildTop(m)));

  // capture: disabled rows swallow the click before any other module routes it
  menubar.addEventListener("click", e => {
    const row = e.target.closest && e.target.closest(".etabs-menu-item.is-disabled");
    if (row) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  menubar.addEventListener("keydown", onKey, true);
  menubar.addEventListener("focusout", () => setTimeout(() => {
    if (openWrap && !menubar.contains(document.activeElement) && kbMode && document.activeElement !== document.body) closeMenus();
  }, 0));
  document.addEventListener("click", e => { if (openWrap && !menubar.contains(e.target)) closeMenus(); });
  document.addEventListener("pointerdown", e => { if (openWrap && !menubar.contains(e.target)) closeMenus(); }, true);
  window.addEventListener("resize", () => closeMenus());
  // Escape anywhere closes the menus (deepest level first when focus is inside)
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && openWrap && !menubar.contains(e.target)) { closeMenus(); e.stopImmediatePropagation(); }
  }, true);

  /* Alt+letter accelerators; underline them while Alt is held */
  const accelOf = {};
  menus.forEach(m => { if (m.accel) accelOf[m.accel.toLowerCase()] = m; });
  document.addEventListener("keydown", e => {
    if (e.key === "Alt") { menubar.classList.add("show-accel"); return; }
    if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const code = /^Key([A-Z])$/.exec(e.code || "");
    const ch = (code ? code[1] : e.key || "").toLowerCase();
    const m = accelOf[ch];
    if (!m) return;
    if (document.querySelector(".modal-backdrop:not(.hidden), .ux-dlg-back:not(.hidden)")) return;
    e.preventDefault(); e.stopImmediatePropagation();
    kbMode = true;
    if (openWrap === m._wrap) closeMenus();
    else openMenuWrap(m._wrap, true);
  }, true);
  const hideAccel = () => menubar.classList.remove("show-accel");
  document.addEventListener("keyup", e => { if (e.key === "Alt") hideAccel(); });
  window.addEventListener("blur", hideAccel);

  // keep disabled / checked state current for the palette and screen readers
  let rq = 0;
  const later = () => { if (!rq) rq = requestAnimationFrame(() => { rq = 0; refreshAll(); }); };
  ["sky:model-changed", "sky:results-changed", "sky:units-changed", "sky:model-synced"].forEach(ev => document.addEventListener(ev, later));

  /* ------------------------------------------------------------ registry */
  const PREFIX_MENU = { file: "File", edit: "Edit", vx: "View", view: "View", def: "Define", draw: "Draw", sel: "Select",
    asn: "Assign", an: "Analyze", dis: "Display", des: "Tools", opt: "Options", tool: "Tools", help: "Help" };
  const findChild = (nodes, name) => {
    const n = norm(name);
    return nodes.find(x => x && x.sub && (norm(valOf(x.label)) === n || (x.aliases || []).some(a => norm(a) === n)));
  };
  const moreOf = parent => {
    let more = parent.find(x => x && x.sub && x.more);
    if (!more) {
      more = { label: "More", more: true, hideEmpty: true, sub: [] };
      parent.push({ sep: true }, more);
    }
    return more;
  };
  /**
   * Add a command at runtime.
   * @param {string|string[]|null} path — "Assign/Frame Loads" (labels, case- and
   *        "…"-insensitive; aliases accepted). null → inferred from the act prefix.
   * @param {object} item — { label, act, fn, key?, hint?, check?, radio?, needs?, sep? }
   * @returns {string} the path the item landed in, or "" when refused.
   */
  function add(path, item) {
    if (!item || (!item.sep && (!item.act || typeof item.fn !== "function"))) { console.warn("menu.add: item needs act + fn"); return ""; }
    if (item.act && actMap[item.act]) { console.warn(`menu.add: act "${item.act}" already exists`); return ""; }
    let parts = Array.isArray(path) ? path.slice() : String(path || "").split(/\s*[/›>]\s*/).filter(Boolean);
    if (!parts.length) {
      // no path: the act prefix picks the menu, the item goes to its "More"
      const pre = item.act ? item.act.split("-")[0] : "";
      parts = [PREFIX_MENU[pre] || "Tools", "__more__"];
    }
    let top = menus.find(m => norm(m.name) === norm(parts[0]));
    let rest = parts.slice(1);
    if (!top) { top = menus.find(m => m.name === "Tools") || menus[menus.length - 1]; rest = ["__more__"]; }
    let nodes = top.items;
    const landed = [top.name];
    for (const seg of rest) {
      const child = seg === "__more__" ? null : findChild(nodes, seg);
      if (child) { nodes = child.sub; landed.push(valOf(child.label)); continue; }
      const more = moreOf(nodes);
      nodes = more.sub; landed.push("More");
      break;
    }
    nodes.push(item);
    rebuildTop(top);
    return landed.join("/");
  }
  function remove(act) {
    const p = pathOf[act];
    if (!p) return false;
    const top = menus.find(m => m.name === p[0]);
    const strip = list => {
      const i = list.findIndex(x => x && x.act === act);
      if (i >= 0) { list.splice(i, 1); return true; }
      return list.some(x => x && x.sub && strip(x.sub));
    };
    const ok = strip(top.items);
    if (ok) rebuildTop(top);
    return ok;
  }
  /** per-panel entry counts (rows + submenu triggers) — for docs / tests */
  function stats() {
    const out = [];
    const walk = (nodes, path) => {
      const n = nodes.filter(x => x && !x.sep && !x.header && !x.dynamic && !(x.hideEmpty && !x.sub.some(y => y && !y.sep))).length;
      out.push({ path: path.join(" › "), entries: n, depth: path.length });
      nodes.forEach(x => { if (x && x.sub && !(x.hideEmpty && !x.sub.some(y => y && !y.sep))) walk(x.sub, [...path, valOf(x.label)]); });
    };
    menus.forEach(m => walk(m.items, [m.name]));
    return out;
  }
  /** open the menu path (top › sub › …) — used by tests and the palette */
  function openPath(path) {
    const parts = Array.isArray(path) ? path : String(path).split(/\s*[/›>]\s*/).filter(Boolean);
    const top = menus.find(m => norm(m.name) === norm(parts[0]));
    if (!top) return false;
    openMenuWrap(top._wrap);
    let nodes = top.items;
    for (const seg of parts.slice(1)) {
      const child = findChild(nodes, seg);
      if (!child) return false;
      openSub(child._trigger, false);
      nodes = child.sub;
    }
    return true;
  }

  return {
    actMap, pathOf: act => (pathOf[act] ? pathOf[act].slice() : null), rowOf: act => rowOf[act] || null,
    closeMenus, openMenuWrap, isOpen: () => !!openWrap, openDepth: () => stack.length,
    openPath, add, remove, stats, refresh: refreshAll, reasonOf: act => (actMap[act] ? reasonOf(actMap[act]) : null),
    menus,
  };
}
