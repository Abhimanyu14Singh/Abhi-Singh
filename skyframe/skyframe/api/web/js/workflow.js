/* SkyFrame — guided workflow ("what do I do next?") + canvas context menus.

   1) Model Workflow checklist — a slim, dismissible card (bottom-right of
      the workspace) listing the ETABS modelling flow:
        Grid & Stories → Materials → Sections → Draw → Supports → Load
        Patterns → Assign Loads → Mass Source → Load Cases → Run → Results
      Each step's done / not-done state is COMPUTED FROM THE MODEL (and the
      results), the first open step is highlighted as "next", and clicking a
      step runs the matching menu command (sky.etabs.clickItem(act)), so it
      opens exactly what the menu opens. Collapse to a one-line pill, close
      with ×; Help › Model Workflow brings it back (remembered per browser).
   2) Right-click context menus on the Plan / Elevation / 3D canvases: a
      right-click WITHOUT dragging (right-drag still pans) on an object
      selects it (unless it is already in the selection) and offers ETABS-like
      Assign / Edit / Info actions for that object type — every entry is an
      existing menu act (clickItem) or the standard Info dialog. Esc, a click
      elsewhere or scrolling closes it; ↑/↓/Enter work from the keyboard.

   Additive: no existing handler is replaced. CSS in /static/workflow.css. */

import * as wfME from "./modeledit.js";
import { openObjectInfo as wfOpenObjectInfo } from "./dlgstd_assign.js";

const LS_KEY = "skyframe.workflow.v1";
const lsGet = () => { try { return JSON.parse(localStorage.getItem(LS_KEY) || "null") || {}; } catch { return {}; } };
const lsSet = v => { try { localStorage.setItem(LS_KEY, JSON.stringify(v)); } catch { /* storage blocked */ } };

function ensureCss() {
  if (document.querySelector("link[data-wf-css]")) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/workflow.css"; l.setAttribute("data-wf-css", "1");
  document.head.appendChild(l);
}
function h(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  (Array.isArray(kids) ? kids : [kids]).forEach(c => c != null && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
  return n;
}

/* ================================================================
   Step model (pure — exported for tests)
   ================================================================ */
const anyLoads = p => p && (["member_loads", "member_udls", "area_loads", "story_forces", "nodal_loads", "thermal_loads", "ground_displacements", "shell_thermal_loads", "joint_temperatures"]
  .some(k => (p[k] || []).length) || (p.self_weight_factor || 0) > 0 || !!p.wind || !!p.elf || !!p.auto_lateral);

export const WORKFLOW_STEPS = [
  { id: "grid", label: "Grid & Stories", act: "def-stories", tip: "Set story heights and grid lines",
    done: m => (m.stories || []).length > 0 && ((m.grid && (m.grid.x_lines || []).length >= 2) || (m.grid_systems || []).length > 0) },
  { id: "materials", label: "Materials", act: "def-materials", tip: "Concrete / steel properties",
    done: m => Object.keys(m.materials || {}).length > 0 },
  { id: "sections", label: "Sections", act: "def-frame", tip: "Frame and slab / wall sections",
    done: m => Object.keys(m.sections || {}).length + Object.keys(m.shell_sections || {}).length > 0 },
  { id: "draw", label: "Draw", act: "draw-column", tip: "Columns, beams, braces, walls and slabs (C, B, X, W, S)",
    done: m => (m.members || []).length + (m.shells || []).length > 0 },
  { id: "supports", label: "Supports", act: "asn-support", tip: "Restraints at the base (automatic or explicit)",
    done: m => ((m.members || []).length + (m.shells || []).length > 0) && (!!m.base_fixity || (m.supports || []).length > 0 || (m.spring_supports || []).length > 0) },
  { id: "patterns", label: "Load Patterns", act: "def-patterns", tip: "Dead, live, seismic, wind …",
    done: m => Object.keys(m.patterns || {}).length > 0 },
  { id: "loads", label: "Assign Loads", act: "asn-l116-fdist", tip: "Select objects, then Assign › Frame / Shell / Joint Loads",
    done: m => Object.values(m.patterns || {}).some(anyLoads) },
  { id: "mass", label: "Mass Source", act: "def-mass", tip: "Where the dynamic mass comes from",
    done: m => { const o = wfME.normalizeMassOptions(m.mass_options); return (o.self_mass !== false) || (o.patterns && Object.keys(m.mass_source || {}).length > 0); } },
  { id: "cases", label: "Load Cases", act: "def-loadcases", tip: "Static, modal, response spectrum …",
    done: m => wfME.allAnalysisCases(m).some(c => c.kind !== "modal") },
  { id: "run", label: "Run Analysis", act: "an-run", key: "F5", tip: "Solve the model (F5)",
    done: (m, S) => !!S.results },
  { id: "results", label: "Results", act: "dis-deformed", tip: "Deformed shape, forces, drifts, tables",
    done: (m, S, st) => !!S.results && !!st.seen },
];

export function workflowState(sky, st = {}) {
  const S = sky.store, m = S.model;
  if (!m) return { steps: [], next: null, done: 0 };
  const steps = WORKFLOW_STEPS.map(s => {
    let d = false;
    try { d = !!s.done(m, S, st); } catch { d = false; }
    return { id: s.id, label: s.label, done: d };
  });
  const stale = !!(S.results && S.modelEdited);
  if (stale) { const r = steps.find(s => s.id === "run"); r.stale = true; }
  const next = steps.find(s => !s.done || s.stale) || null;
  return { steps, next: next && next.id, done: steps.filter(s => s.done && !s.stale).length, total: steps.length, stale };
}

/* ================================================================
   Checklist panel
   ================================================================ */
export function installWorkflow(sky) {
  if (!sky || !sky.store) return null;
  ensureCss();
  const S = sky.store;
  const pref = lsGet();
  const st = { seen: false, open: pref.open !== false, collapsed: !!pref.collapsed, userExpanded: false };
  const save = () => lsSet({ open: st.open, collapsed: st.collapsed });
  const act = a => {
    if (!a) return;
    if (sky.etabs && sky.etabs.hasItem && sky.etabs.hasItem(a)) sky.etabs.clickItem(a);
    else { const n = document.querySelector(`.etabs-menu-item[data-act="${a}"]`); if (n) n.click(); }
  };

  const panel = h("aside", { class: "wf-panel", id: "wfPanel", role: "region", "aria-label": "Model workflow" });
  const head = h("div", { class: "wf-head" });
  const title = h("button", { type: "button", class: "wf-title", id: "wfToggle", "aria-expanded": "true", title: "Collapse / expand the workflow" });
  const close = h("button", { type: "button", class: "wf-x", id: "wfClose", title: "Hide (Help › Model Workflow shows it again)", "aria-label": "Hide the model workflow" }, ["×"]);
  head.append(title, close);
  const bar = h("div", { class: "wf-bar", "aria-hidden": "true" }, [h("i")]);
  const list = h("ol", { class: "wf-steps", id: "wfSteps" });
  const nextBox = h("div", { class: "wf-next", id: "wfNext" });
  panel.append(head, bar, list, nextBox);
  (document.getElementById("app") || document.body).appendChild(panel);

  const isCollapsed = ws => st.collapsed || (ws.done === ws.total && !st.userExpanded);
  title.addEventListener("click", () => {
    const ws = workflowState(sky, st);
    if (isCollapsed(ws)) { st.collapsed = false; st.userExpanded = true; } else { st.collapsed = true; st.userExpanded = false; }
    save(); render(true);
  });
  /* dock at the bottom-right of the visible drawing canvas (plan / elevation
     / 3D) — never over the Properties panel, the explorer or the menus;
     in the Loads / results-table views, of the workspace */
  const place = () => {
    const cands = [sky.planEditor && sky.planEditor.svg, sky.elevEditor && sky.elevEditor.svg, sky.viewer && sky.viewer.canvas,
      ...["loadsMain", "analyzeMain", "drawMain"].map(id => document.getElementById(id))];
    const main = cands.find(n => n && n.offsetParent !== null && n.getBoundingClientRect().width > 200) || document.getElementById("etabsCanvas");
    if (!main) return;
    const r = main.getBoundingClientRect();
    if (!r.width) return;
    panel.style.left = "auto";
    panel.style.right = Math.max(8, Math.round(window.innerWidth - r.right + 12)) + "px";
    panel.style.bottom = Math.max(8, Math.round(window.innerHeight - r.bottom + 12)) + "px";
  };
  window.addEventListener("resize", () => place());
  close.addEventListener("click", () => { st.open = false; save(); render(); });

  let lastSig = "";
  function render(force = false) {
    panel.hidden = !st.open || !S.model;
    if (panel.hidden) return;
    const ws = workflowState(sky, st);
    place();
    const collapsed = isCollapsed(ws);
    const sig = JSON.stringify(ws) + collapsed + S.mode;
    if (!force && sig === lastSig) return;
    lastSig = sig;
    panel.classList.toggle("is-collapsed", collapsed);
    title.setAttribute("aria-expanded", collapsed ? "false" : "true");
    const nx = WORKFLOW_STEPS.find(s => s.id === ws.next);
    title.innerHTML = `<span class="wf-caret">▾</span><span class="wf-name">Model workflow</span>` +
      `<span class="wf-count">${ws.done}/${ws.total}</span>` + (collapsed && nx ? `<span class="wf-pill-next">Next: ${nx.label}</span>` : "");
    bar.firstChild.style.width = Math.round(100 * ws.done / ws.total) + "%";
    list.textContent = "";
    ws.steps.forEach((s, i) => {
      const def = WORKFLOW_STEPS[i];
      const cls = "wf-step" + (s.done && !s.stale ? " is-done" : "") + (s.id === ws.next ? " is-next" : "") + (s.stale ? " is-stale" : "");
      const b = h("button", { type: "button", class: "wf-btn", "data-step": s.id, title: def.tip + (def.key ? ` (${def.key})` : "") },
        [h("span", { class: "wf-mark", "aria-hidden": "true", text: s.done && !s.stale ? "✓" : String(i + 1) }),
          h("span", { class: "wf-lbl", text: s.label }),
          h("span", { class: "wf-state", text: s.stale ? "re-run" : s.done ? "done" : s.id === ws.next ? "next" : "" })]);
      b.setAttribute("aria-label", `${i + 1}. ${s.label} — ${s.stale ? "results out of date" : s.done ? "done" : "not done"}`);
      b.addEventListener("click", () => {
        if (s.id === "results" && !S.results) { act("an-run"); return; }
        if (s.id === "results") st.seen = true;
        act(def.act);
        setTimeout(() => render(), 50);
      });
      list.appendChild(h("li", { class: cls }, [b]));
    });
    nextBox.textContent = "";
    if (nx) {
      const go = h("button", { type: "button", class: "btn btn-small wf-go", id: "wfGo" }, [ws.stale && nx.id === "run" ? "Re-run analysis (F5)" : nx.id === "run" ? "Run analysis (F5)" : `Next: ${nx.label}`]);
      go.addEventListener("click", () => list.querySelector(`[data-step="${nx.id}"]`).click());
      nextBox.append(h("span", { class: "wf-hint", text: nx.tip }), go);
    } else nextBox.append(h("span", { class: "wf-hint wf-all", text: "All steps done — explore the results or refine the model." }));
  }

  const kick = () => requestAnimationFrame(() => render());
  ["sky:model-changed", "sky:results-changed", "sky:model-replaced", "sky:model-synced", "sky:units-changed"].forEach(ev => document.addEventListener(ev, kick));
  document.addEventListener("sky:results-changed", () => { st.seen = false; kick(); });
  // results viewed: the Analyze workspace showing results counts as "seen"
  setInterval(() => {
    if (S.results && S.mode === "analyze" && !st.seen) { st.seen = true; render(); }
    else if (st.open) render();
  }, 1200);
  render(true);

  const api = {
    show: () => { st.open = true; st.collapsed = false; st.userExpanded = true; save(); render(true); },
    hide: () => { st.open = false; save(); render(true); },
    toggle: () => { st.open = !st.open; save(); render(true); },
    isOpen: () => st.open,
    state: () => workflowState(sky, st),
    render: () => render(true),
    el: panel,
  };
  return api;
}

/* ================================================================
   Context menus
   ================================================================ */
const MENU = {
  member: [
    { label: "Frame Section…", act: "asn-fsec" },
    { label: "Releases…", act: "asn-frel" },
    { label: "Local Axes…", act: "asn-forient" },
    { label: "End Offsets…", act: "asn-foff" },
    { label: "Insertion Point…", act: "asn-finsert" },
    { sep: true },
    { label: "Frame Loads · Distributed…", act: "asn-l116-fdist" },
    { label: "Frame Loads · Concentrated…", act: "asn-fconc" },
    { label: "Additional Mass…", act: "asn-fmass" },
    { sep: true },
    { label: "Supports at Joints…", act: "asn-support" },
    { label: "Assign to Group…", act: "asn-group" },
  ],
  shell: [
    { label: "Slab / Wall Section…", act: "asn-ssec" },
    { label: "Uniform Load…", act: "asn-suniform" },
    { label: "Temperature Load…", act: "asn-l116-stemp" },
    { label: "Diaphragm…", act: "asn-sdiaph" },
    { label: "Additional Mass…", act: "asn-smass" },
    { sep: true },
    { label: "Supports at Joints…", act: "asn-support" },
    { label: "Assign to Group…", act: "asn-group" },
  ],
  link: [{ label: "Link Properties…", act: "asn-b9-linkprops" }, { label: "Assign to Group…", act: "asn-group" }],
  spring: [{ label: "Joint Springs…", act: "asn-b9-jsprings" }],
  linespring: [],
};
const EDIT = [
  { label: "Copy", act: "edit-copy", key: "Ctrl+C" },
  { label: "Replicate…", act: "edit-replicate" },
  { label: "Move…", act: "edit-move" },
  { label: "Delete", act: "edit-delete", key: "Del", danger: true },
];
const EMPTY = [
  { label: "Select All", act: "sel-all", key: "Ctrl+A" },
  { label: "Paste…", act: "edit-paste", key: "Ctrl+V" },
  { sep: true },
  { label: "Draw Column", act: "draw-column", key: "C" },
  { label: "Draw Beam", act: "draw-beam", key: "B" },
  { label: "Stories and Grid…", act: "def-stories" },
  { sep: true },
  { label: "Run Analysis", act: "an-run", key: "F5" },
  { label: "Model Workflow", act: "help-workflow" },
];

export function installContextMenu(sky) {
  if (!sky || !sky.store) return null;
  ensureCss();
  const S = sky.store;
  let menu = null, lastRef = null;
  const closeMenu = () => { if (menu) { menu.remove(); menu = null; } };
  const run = a => { closeMenu(); if (sky.etabs && sky.etabs.hasItem && sky.etabs.hasItem(a)) sky.etabs.clickItem(a); else if (a === "help-workflow" && sky.workflow) sky.workflow.show(); };
  const TYPE_LABEL = { member: "Frame", shell: "Shell", link: "Link", spring: "Point spring", linespring: "Line spring" };

  function itemsFor(ref) {
    if (sky.f && sky.f.ctxItems) { try { const fi = sky.f.ctxItems(ref); if (fi) return fi; } catch (e) { console.error(e); } }   // Wave F: menus by object type (js/f_ctxmenu.js)
    const out = [];
    if (!ref) return EMPTY.filter(i => i.sep || !i.act || i.act === "help-workflow" || (sky.etabs && sky.etabs.hasItem(i.act)));
    const sel = S.selection || [];
    const n = sel.length;
    const head = n > 1 ? `${n} objects selected` : `${TYPE_LABEL[ref.type] || ref.type} ${ref.uid}`;
    out.push({ head });
    for (const i of MENU[ref.type] || []) out.push(i);
    if ((MENU[ref.type] || []).length) out.push({ sep: true });
    out.push(...EDIT);
    out.push({ sep: true });
    if (S.results && ref.type === "member") out.push({ label: "Show Member Forces", act: "dis-forces" });
    if (ref.type === "member" && sky.bucklingFem) out.push({ label: "Analyze › Member Buckling…", fn: () => sky.bucklingFem.open(ref.uid) });   // v1.24 (js/bucklingfem.js)
    out.push({ label: "Object Information…", fn: () => wfOpenObjectInfo(sky, ref), key: "" });
    return out.filter(i => i.sep || i.head || i.fn || (sky.etabs && sky.etabs.hasItem(i.act)));
  }

  function open(x, y, ref) {
    closeMenu();
    lastRef = ref;
    const items = itemsFor(ref);
    // trim leading / trailing / double separators
    const clean = items.filter((it, i, a) => !(it.sep && (i === 0 || i === a.length - 1 || (a[i - 1] && a[i - 1].sep) || (a[i - 1] && a[i - 1].head))));
    menu = h("div", { class: "wf-ctx", role: "menu", id: "wfCtxMenu", "aria-label": "Context menu" });
    for (const it of clean) {
      if (it.head) { menu.appendChild(h("div", { class: "wf-ctx-head", text: it.head })); continue; }
      if (it.sep) { menu.appendChild(h("div", { class: "wf-ctx-sep", role: "separator" })); continue; }
      const b = h("button", { type: "button", class: "wf-ctx-item" + (it.danger ? " is-danger" : ""), role: "menuitem", "data-act": it.act || null },
        [h("span", { text: it.label }), it.key ? h("kbd", { text: it.key }) : null]);
      b.addEventListener("click", () => { if (it.fn) { closeMenu(); it.fn(); } else run(it.act); });
      menu.appendChild(b);
    }
    document.body.appendChild(menu);
    const r = menu.getBoundingClientRect();
    menu.style.left = Math.max(4, Math.min(x, window.innerWidth - r.width - 6)) + "px";
    menu.style.top = Math.max(4, Math.min(y, window.innerHeight - r.height - 6)) + "px";
    const first = menu.querySelector(".wf-ctx-item");
    if (first) first.focus({ preventScroll: true });
  }

  const selectRef = ref => {
    if (!ref) return;
    const has = (S.selection || []).some(r => r.type === ref.type && r.uid === ref.uid);
    if (!has && sky.handleSelect) sky.handleSelect([ref], false);
  };

  /** Bind one canvas: hit(e) → ref | null. Right-click without drag only. */
  function bind(node, hit) {
    if (!node || node.dataset.wfCtx) return;
    node.dataset.wfCtx = "1";
    let down = null;
    node.addEventListener("pointerdown", e => { if (e.button === 2) down = { x: e.clientX, y: e.clientY }; }, true);
    node.addEventListener("pointerup", e => {
      if (e.button !== 2 || !down) return;
      const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
      down = null;
      if (moved > 4) return;
      let ref = null;
      try { ref = hit(e); } catch { ref = null; }
      selectRef(ref);
      open(e.clientX + 2, e.clientY + 2, ref);
    });
  }
  const planHit = (ed) => e => {
    const r = ed.svg.getBoundingClientRect();
    return ed.hitTest(ed.toWorld(e.clientX - r.left, e.clientY - r.top));
  };
  const bindAll = () => {
    if (sky.planEditor && sky.planEditor.svg) bind(sky.planEditor.svg, planHit(sky.planEditor));
    if (sky.elevEditor && sky.elevEditor.svg) bind(sky.elevEditor.svg, planHit(sky.elevEditor));
    const v = sky.viewer;
    if (v && v.canvas) bind(v.canvas, e => {
      if (sky.f && sky.f.ctxPick) return sky.f.ctxPick(v, e);   // Wave F: every object type (js/f_pick.js)
      const rect = v.canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      let best = null, bestD = 8;
      for (const s of v._segsScreen || []) {
        const dx = s.x2 - s.x1, dy = s.y2 - s.y1, L2 = dx * dx + dy * dy;
        const t = L2 ? Math.max(0, Math.min(1, ((mx - s.x1) * dx + (my - s.y1) * dy) / L2)) : 0;
        const d = Math.hypot(mx - (s.x1 + t * dx), my - (s.y1 + t * dy));
        if (d < bestD) { bestD = d; best = s.seg; }
      }
      if (!best || !best.uid) return null;
      const isMember = (S.model.members || []).some(m => m.uid === best.uid);
      return isMember ? { type: "member", uid: best.uid } : null;
    });
  };
  bindAll();

  document.addEventListener("pointerdown", e => { if (menu && !menu.contains(e.target)) closeMenu(); }, true);
  window.addEventListener("blur", closeMenu);
  window.addEventListener("resize", closeMenu);
  document.addEventListener("wheel", e => { if (menu && !menu.contains(e.target)) closeMenu(); }, { passive: true, capture: true });
  document.addEventListener("keydown", e => {
    if (!menu) return;
    const items = [...menu.querySelectorAll(".wf-ctx-item")];
    const i = items.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); closeMenu(); }
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); e.stopImmediatePropagation();
      const j = (i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
      items[j].focus();
    } else if (e.key === "Tab") { e.preventDefault(); }
  }, true);

  return {
    open: (x, y, ref) => { selectRef(ref); open(x, y, ref || null); },
    close: closeMenu,
    isOpen: () => !!menu,
    items: ref => itemsFor(ref).map(i => i.head || (i.sep ? "—" : i.label)),
    lastRef: () => lastRef,
    rebind: bindAll,
  };
}
