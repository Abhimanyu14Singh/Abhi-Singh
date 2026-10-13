/* SkyFrame Wave H — docking window layout (replaces the fixed View › Windows tiles).

     resizable splits   drag the splitter between two windows (or focus it and
                        use the arrow keys); sizes are saved with the layout
     tab groups         drag a window tab onto another group (centre = add as
                        a tab, edge = split left / right / top / bottom); drop
                        it outside the window area to pop it out
     any number         View › Windows › New Window (same view as the active
                        window); Arrange Windows tiles up to 10 (ETABS rule:
                        the 3rd window splits the 2nd, the 4th the 1st, …),
                        further windows become tabs of the first
     every window       Plan / Elevation / 3D / Table / Plot (js/h_win.js) with
                        its own story / grid line / camera, display options and
                        results state; ⋯ menu: Pop Out, Same as Active Window,
                        Apply to All Windows, Link Camera, Split, Close
     active window      highlighted; Set Display Options, the results toolbar,
                        Select commands and Intersecting Line act on it
     docked panels      Model Explorer on the left (js/etabs.js), Properties
                        panel (Wave F) in a column on the right of the layout
     saved              in named views (display.h_dock; old display.wb_windows
                        1–4 pane layouts still load) and automatically per
                        project and per monitor setup (localStorage — never in
                        the model file)

   View › Windows presets 1 / 2 / 3 / 4 build the old tile arrangements
   (js/h_dock_core.js hPreset). API: window.__sky.hDock (also exposed as the
   old sky.wb.windows pane API). CONTRACT "Windows, pop-outs and
   side-by-side compare (Wave H)". */

import * as HD from "./h_dock_core.js";
import { HWin, HW_KIND_LABEL as HD_KIND_LABEL } from "./h_win.js";
import { el as hdEl } from "./dlgkit.js";

const LS_LAYOUTS = "skyframe.h.layouts.v1";
const MAX_PROJECTS = 30;

export function initHDock(sky, { popout = null } = {}) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 3500) => sky.toast && sky.toast(t, m, k, ms);
  let L = HD.hEmpty();
  const wins = new Map();              // id → HWin (docked windows of THIS page)
  let host = null, mainEl = null, propsEl = null;
  let shift = false;
  const canvas = () => document.getElementById("etabsCanvas");

  /* ---------------------------------------------------------------- helpers for windows */
  const linesOf = () => {
    const g = S.model && S.model.grid;
    if (!g) return [];
    return [...(g.x_lines || []).map((_, i) => [`x:${i}`, `X-line ${(g.x_labels || [])[i] || i + 1}`]),
      ...(g.y_lines || []).map((_, i) => [`y:${i}`, `Y-line ${(g.y_labels || [])[i] || i + 1}`])];
  };
  const parseLine = s => { const m = /^([xy]):(\d+)$/.exec(s || ""); return m ? { axis: m[1], index: +m[2] } : null; };
  function withState(key, val, fn) { const old = S[key]; S[key] = val; try { return fn(); } finally { S[key] = old; } }
  const selSet = () => new Set((S.selection || []).map(r => `${r.type}:${r.uid}`));
  const ctx = {
    sky,
    dock: {
      linesOf, parseLine, withState, selSet, shift: () => shift,
      activate: id => activate(id),
      changed: (id, structural) => changed(id, structural),
      menu: (w, btn) => openWinMenu(w, btn),
      camMoved: w => camMoved(w),
      setWindowCase: (w, key) => setWindowCase(w, key),
    },
  };

  /* ---------------------------------------------------------------- layout state */
  const isOn = () => !!L.root || Object.keys(L.popped || {}).length > 0 || !!popout;
  const layoutKey = () => (popout ? "popout" : !L.root ? "1" : L.preset || "custom");
  const panes = () => HD.hWinIds(L).map(id => wins.get(id)).filter(Boolean);
  const active = () => (L.active && wins.get(L.active)) || null;
  function activeGId() {
    if (!L.root) return null;
    const w = active();
    if (!w) return null;
    return w.kind === "table" || w.kind === "plot" ? "h" + w.kind + "_" + w.id : w.gId;
  }
  /** Current layout with every window's live state. */
  function snapshot() {
    const o = HD.hSerialize(L);
    for (const [id, w] of wins) o.wins[id] = HD.hWinState(id, w.state());
    return o;
  }

  /* ---------------------------------------------------------------- render */
  function ensureHost() {
    const cv = canvas();
    if (!cv) return null;
    if (!host) {
      host = hdEl("div", { class: "wb-panes h-dock", id: "wbPanes", role: "region", "aria-label": "View windows" });
      mainEl = hdEl("div", { class: "h-dock-main" });
      propsEl = hdEl("div", { class: "h-dock-props hidden", id: "hDockProps", "aria-label": "Properties" });
      host.append(mainEl, propsEl);
    }
    if (popout) { if (host.parentElement !== document.body) document.body.appendChild(host); host.classList.add("h-popout-host"); }
    else if (host.parentElement !== cv) cv.appendChild(host);
    return host;
  }
  function removeHost() {
    if (host) { host.remove(); }
  }
  function render() {
    if (!isOn() || !L.root) {
      for (const w of wins.values()) w.destroy();
      wins.clear();
      removeHost();
      after();
      return;
    }
    ensureHost();
    const ids = new Set(HD.hWinIds(L));
    for (const [id, w] of wins) if (!ids.has(id)) { w.destroy(); w.el.remove(); wins.delete(id); }
    const fresh = [];
    for (const id of ids) if (!wins.has(id)) { const w = new HWin(ctx, L.wins[id]); wins.set(id, w); fresh.push(w); }
    mainEl.textContent = "";
    mainEl.appendChild(renderNode(L.root, []));
    panes().forEach((w, i) => { w.i = i; w.el.dataset.pane = String(i); w.el.classList.toggle("is-focus", w.id === L.active); });
    const R = sky.results;
    if (R && R.syncPanes) { try { R.syncPanes(); } catch (e) { console.error(e); } }
    for (const w of fresh) { try { w.applyRes(); if (w.cmp && sky.hCompare) sky.hCompare.hydrate(w); } catch (e) { console.error("window results state", e); } }
    syncLinks();
    after();
  }
  function after() {
    document.dispatchEvent(new CustomEvent("sky:wb-windows", { detail: { layout: layoutKey() } }));
    if (sky.etabs && sky.etabs.refresh) { try { sky.etabs.refresh(); } catch { /* chrome optional */ } }
    if (sky.results && sky.results.redraw) sky.results.redraw();
    persistSoon();
  }
  function renderNode(n, path) {
    if (n.t === "tabs") return renderGroup(n);
    const box = hdEl("div", { class: `h-split h-split-${n.dir}`, "data-path": path.join(".") });
    n.kids.forEach((k, i) => {
      if (i) box.appendChild(splitter(n, path, i));
      const c = renderNode(k, path.concat(i));
      c.style.flex = `${n.sizes[i]} 1 0px`;
      box.appendChild(c);
    });
    return box;
  }
  function renderGroup(g) {
    const box = hdEl("div", { class: "h-group", "data-cur": g.cur });
    const strip = hdEl("div", { class: "h-tabs", role: "tablist", "aria-label": "Window tabs" });
    for (const id of g.wins) {
      const w = wins.get(id);
      if (!w) continue;
      const tab = hdEl("div", { class: "h-tab" + (id === g.cur ? " is-cur" : "") + (id === L.active ? " is-active" : ""), role: "tab", tabindex: "0",
        "aria-selected": id === g.cur ? "true" : "false", "data-h-tab": id, title: `${w.label()} — drag onto another window to move / split, outside to pop out` });
      tab.append(hdEl("span", { class: "h-tab-num", text: id.slice(1) }), hdEl("span", { class: "h-tab-lbl", text: w.label() }));
      const x = hdEl("button", { type: "button", class: "h-tab-x", title: "Close window", "aria-label": `Close window ${id.slice(1)}`, text: "×" });
      x.addEventListener("click", e => { e.stopPropagation(); close(id); });
      tab.appendChild(x);
      tab.addEventListener("pointerdown", e => tabDown(e, id));
      tab.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); showTab(id); } });
      strip.appendChild(tab);
    }
    const plus = hdEl("button", { type: "button", class: "h-tab-add", title: "New window as a tab here", "aria-label": "New tab", text: "+" });
    plus.addEventListener("click", () => add({ kind: (wins.get(g.cur) || {}).kind || "3d" }, g.cur, "center"));
    strip.appendChild(plus);
    const body = hdEl("div", { class: "h-group-body" });
    for (const id of g.wins) { const w = wins.get(id); if (!w) continue; w.el.classList.toggle("h-hidden", id !== g.cur); body.appendChild(w.el); }
    box.append(strip, body);
    return box;
  }

  /* ---------------------------------------------------------------- splitters */
  function splitter(n, path, i) {
    const sp = hdEl("div", { class: `h-splitter h-splitter-${n.dir}`, role: "separator", tabindex: "0", "aria-orientation": n.dir === "row" ? "vertical" : "horizontal",
      "aria-label": "Resize windows", "data-h-split": path.join(".") + ":" + i, "aria-valuenow": String(Math.round(n.sizes[i - 1] * 100)) });
    const apply = sizes => {
      L = HD.hReduce(L, { type: "resize", path, sizes });
      const box = sp.parentElement;
      const kids = [...box.children].filter(c => !c.classList.contains("h-splitter"));
      const node = HD.hNodeAt(L, path);
      kids.forEach((c, k) => { c.style.flex = `${node.sizes[k]} 1 0px`; });
      sp.setAttribute("aria-valuenow", String(Math.round(node.sizes[i - 1] * 100)));
    };
    sp.addEventListener("pointerdown", e => {
      e.preventDefault();
      sp.setPointerCapture(e.pointerId);
      const box = sp.parentElement.getBoundingClientRect();
      const node = HD.hNodeAt(L, path);
      const span = n.dir === "row" ? box.width : box.height;
      const start = n.dir === "row" ? e.clientX : e.clientY;
      const s0 = node.sizes.slice();
      sp.classList.add("is-drag");
      const move = ev => {
        const d = ((n.dir === "row" ? ev.clientX : ev.clientY) - start) / Math.max(1, span);
        const s = s0.slice();
        const pair = s0[i - 1] + s0[i];
        s[i - 1] = Math.min(pair - 0.05, Math.max(0.05, s0[i - 1] + d));
        s[i] = pair - s[i - 1];
        apply(s);
      };
      const up = () => { sp.removeEventListener("pointermove", move); sp.removeEventListener("pointerup", up); sp.classList.remove("is-drag"); persistSoon(); };
      sp.addEventListener("pointermove", move);
      sp.addEventListener("pointerup", up);
    });
    sp.addEventListener("keydown", e => {
      const step = e.shiftKey ? 0.1 : 0.02;
      const d = (n.dir === "row" ? { ArrowLeft: -1, ArrowRight: 1 } : { ArrowUp: -1, ArrowDown: 1 })[e.key];
      if (!d) return;
      e.preventDefault();
      const node = HD.hNodeAt(L, path), s = node.sizes.slice(), pair = s[i - 1] + s[i];
      s[i - 1] = Math.min(pair - 0.05, Math.max(0.05, s[i - 1] + d * step)); s[i] = pair - s[i - 1];
      apply(s); persistSoon();
    });
    return sp;
  }

  /* ---------------------------------------------------------------- tab drag */
  let drop = null;
  function zoneAt(groupEl, x, y) {
    const r = groupEl.getBoundingClientRect();
    const fx = (x - r.left) / r.width, fy = (y - r.top) / r.height;
    const m = Math.min(fx, 1 - fx, fy, 1 - fy);
    if (m > 0.25) return "center";
    return m === fx ? "left" : m === 1 - fx ? "right" : m === fy ? "top" : "bottom";
  }
  function tabDown(e, id) {
    if (e.button !== 0 || e.target.closest(".h-tab-x")) return;
    const x0 = e.clientX, y0 = e.clientY;
    let dragging = false, ghost = null, ind = null;
    const move = ev => {
      if (!dragging && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 6) return;
      if (!dragging) {
        dragging = true;
        ghost = hdEl("div", { class: "h-drag-ghost", text: (wins.get(id) || { label: () => id }).label() });
        ind = hdEl("div", { class: "h-drop-ind hidden" });
        document.body.append(ghost, ind);
      }
      ghost.style.left = ev.clientX + 12 + "px"; ghost.style.top = ev.clientY + 8 + "px";
      // geometric hit test (overlays such as the results toolbar sit above the windows)
      const inside = (r, x, y) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
      const g = host ? [...host.querySelectorAll(".h-group")].find(n => inside(n.getBoundingClientRect(), ev.clientX, ev.clientY)) : null;
      if (g) {
        const zone = zoneAt(g, ev.clientX, ev.clientY);
        const target = g.dataset.cur;
        drop = { target, zone };
        const r = g.getBoundingClientRect();
        const box = { center: [0, 0, 1, 1], left: [0, 0, 0.5, 1], right: [0.5, 0, 0.5, 1], top: [0, 0, 1, 0.5], bottom: [0, 0.5, 1, 0.5] }[zone];
        Object.assign(ind.style, { left: r.left + box[0] * r.width + "px", top: r.top + box[1] * r.height + "px", width: box[2] * r.width + "px", height: box[3] * r.height + "px" });
        ind.classList.remove("hidden");
      } else {
        const out = host && !inside(host.getBoundingClientRect(), ev.clientX, ev.clientY);
        drop = out ? { out: true } : null;
        ind.classList.add("hidden");
      }
    };
    const up = () => {
      document.removeEventListener("pointermove", move, true);
      document.removeEventListener("pointerup", up, true);
      if (ghost) ghost.remove();
      if (ind) ind.remove();
      if (!dragging) { showTab(id); return; }
      const d = drop; drop = null;
      if (!d) return;
      if (d.out) { if (!popout && sky.hSync) sky.hSync.popout(id); return; }
      move2(id, d.target, d.zone);
    };
    document.addEventListener("pointermove", move, true);
    document.addEventListener("pointerup", up, true);
  }

  /* ---------------------------------------------------------------- operations */
  function commit(L2, { rerender = true } = {}) {
    L = L2;
    if (rerender) render(); else { syncActive(); persistSoon(); }
  }
  function syncActive() {
    for (const w of wins.values()) w.el.classList.toggle("is-focus", w.id === L.active);
    if (host) host.querySelectorAll(".h-tab").forEach(t => t.classList.toggle("is-active", t.dataset.hTab === L.active));
  }
  let prevActive = null;          // the window that was active before (window ⋯ menu: "Same as Active Window")
  function activate(id) {
    if (!wins.has(id) || L.active === id) return;
    if (L.active && wins.has(L.active)) prevActive = L.active;
    L = HD.hReduce(L, { type: "activate", id });
    syncActive();
    if (sky.results && sky.results.redraw) sky.results.redraw();
    document.dispatchEvent(new CustomEvent("sky:h-active", { detail: { id } }));
  }
  function showTab(id) {
    const f = HD.hFind(L, id);
    if (!f) return;
    if (f.node.cur !== id) { L = HD.hReduce(L, { type: "activate", id }); render(); }
    else activate(id);
  }
  /** Start the docking layout from the single main view (window 1 = what the main view shows). */
  function startFromMain() {
    const kind = S.mode === "analyze" && (S.tab || "view3d") === "view3d" ? "3d" : S.view === "elev" ? "elev" : "plan";
    const st = { kind };
    if (kind === "plan") st.story = S.story;
    if (kind === "elev") st.line = S.elevLine;
    try {
      if (kind === "3d" && sky.viewer) { const v = sky.viewer; st.camera = { yaw: v.yaw, pitch: v.pitch, dist: v.dist, target: v.target.slice() }; }
      else if (kind === "plan" && sky.planEditor) { const p = sky.planEditor; st.camera = { cx: p.cx, cy: p.cy, scale: p.scale }; }
    } catch { /* camera optional */ }
    L = HD.hReduce(HD.hEmpty(), { type: "add", win: st });
  }
  function add(win = {}, target = null, zone = null) {
    if (!S.model) return null;
    if (!L.root) startFromMain();
    const a = active();
    const st = { kind: win.kind || (a ? a.kind : "3d"), ...win };
    if (!win.kind && a) { if (a.kind === "plan") st.story = a.story; if (a.kind === "elev") st.line = a.line; }
    L = HD.hReduce(L, { type: "add", win: st, target: target || L.active, zone: zone || "right" });
    render();
    return L.active;
  }
  function close(id) {
    if (popout) { if (sky.hSync) sky.hSync.dockBack(); return; }
    if (!L.wins[id]) return;
    const w = wins.get(id);
    if (w) { w.destroy(); w.el.remove(); wins.delete(id); }
    L = HD.hReduce(L, { type: "close", id });
    if (!L.root && !Object.keys(L.popped).length) L = HD.hEmpty();
    render();
  }
  function move2(id, target, zone) { if (!L.wins[id] || !L.wins[target]) return; commit(HD.hReduce(L, { type: "move", id, target, zone })); }
  function setPreset(key, paneStates) {
    key = String(key || "1");
    if (!HD.H_PRESETS[key]) key = "1";
    if (key === "1") { if (sky.hSync) sky.hSync.closeAll(); L = HD.hEmpty(); render(); return 0; }
    if (!S.model) return 0;
    const prev = panes().map(w => w.state()).map(({ id, ...st }) => st);
    const sts = (paneStates && paneStates.length ? paneStates : prev);
    if (sky.hSync) sky.hSync.closeAll();
    for (const w of wins.values()) { w.destroy(); w.el.remove(); }
    wins.clear();
    L = HD.hPreset(key, sts);
    render();
    return panes().length;
  }
  function arrange() {
    if (!L.root) { toast("Arrange Windows", "Open a second window first (View › Windows › New Window)."); return; }
    commit(HD.hReduce(L, { type: "arrange" }));
  }
  function restore(obj) {
    let L2 = null;
    try { L2 = HD.hMigrate(obj); } catch (e) { console.warn("window layout", e.message); L2 = null; }
    if (sky.hSync) sky.hSync.closeAll();
    for (const w of wins.values()) { w.destroy(); w.el.remove(); }
    wins.clear();
    const pop = L2 ? { ...L2.popped } : {};
    if (L2) for (const id of Object.keys(pop)) L2 = HD.hReduce(L2, { type: "dock", id });   // pop-outs reopen below
    L = L2 && L2.root ? L2 : HD.hEmpty();
    render();
    if (sky.hSync) for (const id of Object.keys(pop)) sky.hSync.popout(id);
    return !!L.root;
  }
  function capture() { return L.root || Object.keys(L.popped).length ? snapshot() : null; }

  /* ---------------------------------------------------------------- results per window */
  function setWindowCase(w, key) {
    const R = sky.results, v = w.gView();
    if (!R || !v) return;
    if (!key) { v.sel = null; R.redraw(); w.syncResBox(); changed(w.id); return; }
    const e = R.entry(key);
    if (!e) return;
    v.sel = { key, step: e.kind === "modal" || e.kind === "buckling" ? 0 : -1, env: "step" };
    R.withView(v, () => R.select(key, { quiet: true }));
    w.syncResBox();
    changed(w.id);
  }
  /** ETABS "Apply to All Windows": every window shows the active window's case / step and display choice. */
  function applyToAll() {
    const R = sky.results, a = active();
    if (!R || !a) return 0;
    const av = a.gView();
    const sel = av ? R.withView(av, () => R.selection()) : R.selection();
    const opts = av ? JSON.parse(JSON.stringify(av.opts)) : null;
    if (sel && sel.key) R.setGlobalSel({ key: sel.key, step: sel.step, env: sel.env });
    let n = 0;
    for (const w of wins.values()) {
      const v = w.gView();
      if (!v) continue;
      v.sel = null;
      if (opts && w !== a && (w.kind === a.kind || (w.kind !== "table" && w.kind !== "plot" && a.kind !== "table" && a.kind !== "plot"))) Object.assign(v.opts, JSON.parse(JSON.stringify(opts)));
      n++;
      w.syncResBox();
    }
    if (sky.dispOpts && sky.dispOpts.get && a.dpId && (a.ed || a.vx)) {
      try { const s = sky.dispOpts.get(a.dpId); for (const w of wins.values()) if (w !== a && (w.ed || w.vx)) sky.dispOpts.set(w.dpId, s); } catch (e) { console.warn("apply display options", e); }
    }
    if (sky.hSync) sky.hSync.resOut(true);
    R.redraw();
    persistSoon();
    toast("Apply to all windows", `Window ${a.id.slice(1)}: results case, step and display applied to ${n} window(s)`, "info", 2500);
    return n;
  }
  /** "Same as active window" for window `w` (source: the active window, or — when `w` is the
      active one because its ⋯ menu was just used — the window active before it). */
  function sameAsActive(w, srcId = null) {
    const R = sky.results;
    const a = wins.get(srcId || (L.active !== w.id ? L.active : prevActive)) || null;
    if (!R || !a || a === w) return false;
    const av = a.gView(), v = w.gView();
    if (!v) return false;
    const sel = av ? R.withView(av, () => R.selection()) : R.selection();
    v.sel = sel && sel.key ? { key: sel.key, step: sel.step, env: sel.env } : null;
    if (av) Object.assign(v.opts, JSON.parse(JSON.stringify(av.opts)));
    if (sky.dispOpts && sky.dispOpts.get && (a.ed || a.vx) && (w.ed || w.vx)) { try { sky.dispOpts.set(w.dpId, sky.dispOpts.get(a.dpId)); } catch (e) { console.warn(e); } }
    if (a.kind === w.kind) { const c = a.camera(); if (c) w.setCamera(c); }
    w.syncResBox();
    R.redraw();
    persistSoon();
    return true;
  }

  /* ---------------------------------------------------------------- linked cameras */
  const camSig = new Map();
  let linkRaf = 0;
  function syncLinks() {
    for (const w of wins.values()) w.linkBadge && w.linkBadge.classList.toggle("hidden", !HD.hLinkedWith(L, w.id).length);
    const want = (L.links || []).length > 0;
    if (want && !linkRaf) linkRaf = requestAnimationFrame(linkTick);
  }
  function camOf(w) { const c = w.camera(true); return c ? JSON.stringify(c) : ""; }
  function linkTick() {
    linkRaf = 0;
    if (!(L.links || []).length) return;
    for (const g of L.links) {
      const ws = g.map(id => wins.get(id)).filter(Boolean);
      const moved = ws.find(w => { const s = camOf(w); return s && s !== camSig.get(w.id); });
      if (moved) propagate(moved, ws);
    }
    linkRaf = requestAnimationFrame(linkTick);
  }
  function propagate(src, ws) {
    const c = src.camera(true);
    camSig.set(src.id, JSON.stringify(c));
    for (const w of ws) {
      if (w === src) continue;
      if (sameCamKind(w, src)) { w.setCamera(c); camSig.set(w.id, camOf(w)); }
    }
    if (sky.hSync) sky.hSync.camOut(src.id, c, HD.hLinkedWith(L, src.id).filter(id => L.popped[id] || !wins.has(id)));
  }
  const sameCamKind = (a, b) => (a.v && b.v) || (a.kind === b.kind && (a.kind === "plan" || a.kind === "elev"));
  function camMoved(w) { const ws = [...wins.values()].filter(x => HD.hLinkedWith(L, w.id).includes(x.id)); if (ws.length) propagate(w, [w, ...ws]); }
  function link(ids) {
    L = HD.hReduce(L, { type: "link", ids });
    const g = L.links.find(x => x.includes(ids[0]));
    const src = wins.get(L.active && g && g.includes(L.active) ? L.active : ids[0]);
    if (src && g) propagate(src, g.map(id => wins.get(id)).filter(Boolean));
    syncLinks(); persistSoon();
  }
  function unlink(id) { L = HD.hReduce(L, { type: "unlink", id }); syncLinks(); persistSoon(); }
  function linkAll3d(on) {
    const ids = panes().filter(w => w.kind === "3d").map(w => w.id);
    if (on === undefined) on = !allLinked();
    if (on) { if (ids.length < 2) { toast("Link cameras", "Open two or more 3D windows first."); return false; } link(ids); }
    else ids.forEach(unlink);
    return on;
  }
  const allLinked = () => { const ids = panes().filter(w => w.kind === "3d").map(w => w.id); return ids.length > 1 && (L.links || []).some(g => ids.every(id => g.includes(id))); };
  /** A camera from a linked pop-out window. */
  function camIn(fromId, cam) {
    for (const id of HD.hLinkedWith(L, fromId)) { const w = wins.get(id); if (w) { w.setCamera(cam); camSig.set(id, camOf(w)); } }
  }

  /* ---------------------------------------------------------------- window ⋯ menu */
  let menuEl = null;
  function closeMenu() { if (menuEl) { menuEl.remove(); menuEl = null; } }
  function openWinMenu(w, btn) {
    closeMenu();
    const prevWin = L.active !== w.id ? L.active : prevActive;
    activate(w.id);
    const items = [];
    if (popout) items.push(["Dock Back", "h-dock-back", () => sky.hSync && sky.hSync.dockBack()]);
    else items.push(["Pop Out", "h-popout", () => sky.hSync && sky.hSync.popout(w.id)]);
    const src = prevWin !== w.id ? prevWin : null;
    items.push([src ? `Same as Active Window (${src.slice(1)})` : "Same as Active Window", "h-same", () => sameAsActive(w, src), src && wins.has(src) ? "" : "no other active window yet"]);
    items.push(["Apply to All Windows", "h-applyall", () => { activate(w.id); applyToAll(); }]);
    if (!popout) {
      for (const o of panes()) if (o !== w && sameCamKind(o, w) && !HD.hLinkedWith(L, w.id).includes(o.id)) items.push([`Link Camera with Window ${o.id.slice(1)}`, "h-link-" + o.id, () => link([w.id, o.id])]);
      if (HD.hLinkedWith(L, w.id).length) items.push(["Unlink Camera", "h-unlink", () => unlink(w.id)]);
      items.push(["Split Right (New Window)", "h-split-r", () => add({ kind: w.kind, story: w.story, line: w.line }, w.id, "right")]);
      items.push(["Split Down (New Window)", "h-split-d", () => add({ kind: w.kind, story: w.story, line: w.line }, w.id, "bottom")]);
      items.push(["Close Window", "h-close", () => close(w.id)]);
    }
    menuEl = hdEl("div", { class: "h-menu", role: "menu", "aria-label": `Window ${w.id.slice(1)} commands` });
    for (const [label, act, fn, dis] of items) {
      const b = hdEl("button", { type: "button", role: "menuitem", class: "h-menu-item", "data-h-act": act, disabled: dis ? true : null, title: dis || null, text: label });
      b.addEventListener("click", () => { closeMenu(); try { fn(); } catch (e) { console.error(e); toast("Window", e.message, "error"); } });
      menuEl.appendChild(b);
    }
    document.body.appendChild(menuEl);
    const r = btn.getBoundingClientRect();
    const mw = menuEl.offsetWidth || 220, mh = menuEl.offsetHeight || 200;
    menuEl.style.left = Math.max(4, Math.min(innerWidth - mw - 4, r.right - mw)) + "px";
    menuEl.style.top = Math.max(4, Math.min(innerHeight - mh - 4, r.bottom + 2)) + "px";
    const first = menuEl.querySelector("button:not([disabled])");
    if (first) first.focus();
    setTimeout(() => document.addEventListener("pointerdown", function off(e) { if (menuEl && !menuEl.contains(e.target)) { closeMenu(); } document.removeEventListener("pointerdown", off, true); }, true), 0);
  }
  document.addEventListener("keydown", e => { if (e.key === "Escape" && menuEl) { closeMenu(); e.stopPropagation(); } }, true);

  /* ---------------------------------------------------------------- change tracking */
  let pendingModel = false, raf = 0;
  function schedule(modelChanged) {
    if (!wins.size) return;
    pendingModel = pendingModel || !!modelChanged;
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const mc = pendingModel; pendingModel = false;
      for (const w of wins.values()) { try { w.refresh(mc); } catch (e) { console.error("window refresh", e); } }
      if (mc && host) host.querySelectorAll(".h-tab").forEach(t => { const w = wins.get(t.dataset.hTab); const l = t.querySelector(".h-tab-lbl"); if (w && l) l.textContent = w.label(); });
    });
  }
  function changed(id, structural) {
    if (structural) { render(); return; }
    const t = host && host.querySelector(`.h-tab[data-h-tab="${id}"] .h-tab-lbl`);
    const w = wins.get(id);
    if (t && w) t.textContent = w.label();
    persistSoon();
  }
  const pe = sky.planEditor;
  if (pe) { const rs = pe.renderStatic.bind(pe); pe.renderStatic = () => { rs(); schedule(false); }; }
  document.addEventListener("sky:model-changed", () => schedule(true));
  document.addEventListener("sky:results-changed", () => {
    for (const w of wins.values()) {
      if (w.v) { w.v.setResults(S.results); if (sky.viewer) w.v.setOverlay({ ...sky.viewer.overlay }); }
      if (w.table || w.plot) { try { (w.table || w.plot).refresh(); } catch (e) { console.error(e); } }
      setTimeout(() => w.syncResBox(), 0);
    }
  });
  if (sky.results && sky.results.subscribe) sky.results.subscribe(() => { for (const w of wins.values()) w.syncResBox(); });
  document.addEventListener("keydown", e => { shift = e.shiftKey; }, true);
  document.addEventListener("keyup", e => { shift = e.shiftKey; }, true);
  const st0 = sky.setTool;
  if (typeof st0 === "function") {
    sky.setTool = t => {
      st0(t);
      for (const w of wins.values()) if (w.ed) { try { w.ed.setTool(w.kind === "elev" && (t === "slab" || t === "linespring") ? "select" : (S.tool || t)); } catch (e) { console.error(e); } }
    };
  }

  /* ---------------------------------------------------------------- per project / monitor persistence */
  const projectKey = () => String(S.fileName || (S.model && S.model.name) || "Untitled");
  const monKey = () => (sky.hSync && sky.hSync.monitorKey ? sky.hSync.monitorKey() : HD.hMonitorKey({ x: 0, y: 0, width: screen.width, height: screen.height }));
  const lsRead = () => { try { return JSON.parse(localStorage.getItem(LS_LAYOUTS) || "{}") || {}; } catch { return {}; } };
  let saveT = 0, suspendSave = false;
  function persistSoon() {
    if (popout || suspendSave) return;
    clearTimeout(saveT);
    saveT = setTimeout(persistNow, 400);
  }
  function persistNow() {
    if (popout || !S.model) return;
    const all = lsRead(), pk = projectKey(), mk = monKey();
    const cur = capture();
    all[pk] = all[pk] || {};
    if (cur) all[pk][mk] = { t: Date.now(), layout: cur }; else delete all[pk][mk];
    if (!Object.keys(all[pk]).length) delete all[pk];
    const keys = Object.keys(all);
    if (keys.length > MAX_PROJECTS) keys.sort((a, b) => Math.max(0, ...Object.values(all[a]).map(x => x.t || 0)) - Math.max(0, ...Object.values(all[b]).map(x => x.t || 0))).slice(0, keys.length - MAX_PROJECTS).forEach(k => delete all[k]);
    try { localStorage.setItem(LS_LAYOUTS, JSON.stringify(all)); } catch { /* storage full / blocked: session only */ }
  }
  /** The layout remembered for this project on this monitor setup (null = none). */
  function remembered() { const e = (lsRead()[projectKey()] || {})[monKey()]; return e ? e.layout : null; }
  function restoreRemembered() {
    if (popout) return false;
    const r = remembered();
    if (!r) return false;
    suspendSave = true;
    try { return restore(r); } finally { suspendSave = false; }
  }
  document.addEventListener("sky:model-replaced", () => { setTimeout(() => { if (!restoreRemembered() && L.root) render(); }, 0); });

  /* ---------------------------------------------------------------- API */
  const api = {
    layout: layoutKey, isOn, panes, pane: i => panes()[i], win: id => wins.get(id) || null, wins: () => [...wins.values()],
    L: () => L, setL: L2 => { L = L2; render(); }, snapshot, capture, restore, setPreset, add, close, activate, showTab, move: move2, arrange,
    applyToAll, sameAsActive, link, unlink, linkAll3d, allLinked, camIn, setWindowCase, activeGId, active,
    propsSlot: on => { if (!host || !L.root || !on || popout) { if (propsEl) propsEl.classList.add("hidden"); return null; } propsEl.classList.remove("hidden"); return propsEl; },
    popoutMode: () => popout, render, refresh: () => schedule(true), remembered, restoreRemembered, persistNow, projectKey, monKey,
    _reduce: a => { L = HD.hReduce(L, a); render(); return L; },
  };
  sky.hDock = api;
  // the old View › Windows pane API (Wave F / G / display options use it)
  if (sky.wb) sky.wb.windows = { setLayout: setPreset, layout: layoutKey, panes, pane: i => panes()[i], refresh: () => schedule(true), LAYOUTS: HD.H_PRESETS };
  api.KIND_LABEL = HD_KIND_LABEL;
  return api;
}
