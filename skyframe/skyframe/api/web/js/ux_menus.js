/* SkyFrame UX pass 2 — menu ownership + ETABS naming + Model Explorer keys.
     · Materials / Frame Sections / Shell Sections open the Section Manager on
       THAT tab (the other groups are filtered out until "All" is picked);
       Stories / Grid Systems open the grid editor on the right tab
       (UX audit N3). Routed for menu clicks, the command palette / toolbar /
       shortcuts (__sky.etabs.clickItem) and the Model Explorer alike.
     · Menu labels follow ETABS naming (runtime relabel keyed by `act`, so the
       menu definitions in etabs.js and every act id stay untouched); items
       that only navigate say where they go (audit N4).
     · Analyze › Analysis Options… opens a small ETABS-style hub (active DOF,
       P-Delta, mass source, modal case, cases to run) instead of a toast
       (audit F5).
     · Model Explorer: WAI-ARIA tree with roving tabindex — ↑/↓ move, → opens
       a group / enters it, ← closes / returns to the group, Home/End,
       Enter/Space activate, type-ahead by first letter.
   Additive only; no menu item is removed. */

import { HELP as umHELP } from "./helpdocs.js";
import { el as umEl, uxDialog as umDialog } from "./ux_common.js";

/* act → ETABS-style label (only the items whose label differs) */
export const ETABS_LABELS = {
  "file-new": "New Model…",
  "file-report": "Create Report…",
  "def-materials": "Material Properties…",
  "def-frame": "Section Properties · Frame Sections…",
  "def-np-section": "Section Properties · Nonprismatic Frame Section…",
  "def-shell": "Section Properties · Slab / Wall Sections…",
  "def-designer": "Section Properties · Section Designer…",
  "def-grid": "Stories and Grid Systems · Grid Data…",
  "def-stories": "Stories and Grid Systems · Story Data…",
  "def-groups": "Group Definitions…",
  "def-cases": "Load Cases · Linear Static…",
  "def-rs": "Load Cases · Response Spectrum…",
  "def-th": "Load Cases · Time History…",
  "def-pushover": "Load Cases · Pushover…",
  "def-nls": "Load Cases · Nonlinear Static…",
  "def-modal": "Load Cases · Modal…",
  "def-buckling": "Load Cases · Buckling…",
  "def-staged": "Load Cases · Nonlinear Staged Construction…",
  "def-ss": "Load Cases · Steady State…",
  "def-psd": "Load Cases · Power Spectral Density…",
  "def-pt-hyper": "Load Cases · Hyperstatic…",
  "def-freqfn": "Functions · Steady State / PSD…",
  "def-functions": "Functions · Response Spectrum / Time History…",
  "def-b9-springprops": "Spring Properties · Point Springs…",
  "def-uh-hingeprops": "Frame Hinge Properties…",
  "def-codetools": "Auto Lateral Loads · Code Tools…",
  "draw-select": "Select Object",
  "draw-column": "Quick Draw Columns",
  "draw-beam": "Draw Beams",
  "draw-brace": "Draw Braces",
  "draw-wall": "Draw Walls",
  "draw-slab": "Draw Rectangular Floor",
  "draw-polyfloor": "Draw Floor / Wall (Plan) · Floor",
  "draw-polywall": "Draw Floor / Wall (Elevation) · Wall",
  "draw-polyopen": "Draw Opening (Polygon)",
  "draw-erase": "Erase Object / Add Opening (tool)",
  "draw-spring": "Draw Point Spring",
  "draw-linespring": "Draw Line Spring",
  "draw-link": "Draw Links",
  "draw-cut": "Draw Section Cut…",
  "draw-grid": "Edit Grid Data…",
  "draw-plan": "Plan View (draw)",
  "draw-elev": "Elevation View (draw)",
  "asn-fsec": "Frame · Section Property",
  "asn-frel": "Frame · Releases / Partial Fixity",
  "asn-forient": "Frame · Local Axes",
  "asn-foff": "Frame · End Length Offsets (Properties panel)",
  "asn-faxial": "Frame · Tension / Compression Limits",
  "asn-fhinge": "Frame · Hinges (Properties panel)",
  "asn-fpz": "Frame · Panel Zone",
  "asn-ssec": "Shell · Slab / Wall Section",
  "asn-sspring": "Shell · Area Springs",
  "asn-scp": "Shell · Wind Pressure Coefficients",
  "asn-slayer": "Shell · Layered Section",
  "asn-support": "Joint · Restraints / Springs",
  "asn-fload": "Frame Loads (Properties panel)",
  "asn-aload": "Shell Loads (Properties panel)",
  "an-log": "Last Analysis Run Log…",
  "an-opts": "Analysis Options…",
  "an-fna": "Time History (FNA) · go to TH results",
  "an-ritz": "Ritz Vectors · go to Modal results",
  "an-cracked": "Cracked Analysis · go to Story results",
  "dis-view3d": "Undeformed / 3D View",
  "dis-contours": "Shell Forces / Stresses",
  "dis-story": "Story Response · Drifts & Shears",
  "dis-modal": "Modal Information",
  "dis-reactions": "Joint Reactions",
  "dis-forces": "Frame Forces",
  "dis-th": "Time History Results",
  "dis-pushover": "Static Pushover Curve",
  "dis-nls": "Nonlinear Static Results…",
  "dis-stages": "Staged Construction Results…",
  "dis-buckling": "Buckling Factors",
  "dis-cuts": "Section Cut Forces",
  "dis-piers": "Pier Forces",
  "des-steel": "Steel Frame Design",
  "des-concrete": "Concrete Frame Design",
  "des-wall": "Shear Wall Design",
  "des-punching": "Slab Design · Punching Shear",
  "des-composite": "Composite Beam Design",
  "des-slab": "Slab Design · Two-Way",
  "des-s341": "Steel Frame Design · Seismic (AISC 341)",
  "opt-units": "Units…",
};

/* explorer leaf label → routed action key */
const EXPLORER_ROUTES = {
  "Materials": "sec:materials", "Frame Sections": "sec:frame", "Shell Sections": "sec:shell",
  "Stories": "grid:stories", "Grid Systems": "grid:grid",
};
const ACT_ROUTES = {
  "def-materials": "sec:materials", "def-frame": "sec:frame", "def-shell": "sec:shell",
  "def-grid": "grid:grid", "def-stories": "grid:stories", "draw-grid": "grid:grid",
  "an-opts": "hub:analysis",
};

/* ------------------------------------------------ Section Manager tabs */
const SEC_TABS = [
  ["all", "All"], ["materials", "Materials"], ["frame", "Frame Sections"], ["shell", "Shell Sections"],
];
const SEC_GROUP_OF = { frameSectionRows: "frame", designerSectionRows: "frame", libRows: "frame", shellSectionRows: "shell", materialRows: "materials" };
const SEC_TITLE = { all: "Section Manager", materials: "Material Properties", frame: "Frame Sections", shell: "Slab / Wall Sections" };

function tabStrip(id, tabs, onPick, label) {
  const strip = umEl("div", { class: "ux-tabstrip", role: "tablist", id, "aria-label": label });
  tabs.forEach(([k, t]) => {
    const b = umEl("button", { type: "button", class: "ux-tab", role: "tab", "data-k": k, "aria-selected": "false", tabindex: "-1", text: t });
    b.addEventListener("click", () => onPick(k));
    strip.appendChild(b);
  });
  strip.addEventListener("keydown", e => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const bs = [...strip.querySelectorAll(".ux-tab")];
    let i = bs.indexOf(document.activeElement);
    if (i < 0) i = bs.findIndex(b => b.getAttribute("aria-selected") === "true");
    i = e.key === "Home" ? 0 : e.key === "End" ? bs.length - 1 : (i + (e.key === "ArrowRight" ? 1 : -1) + bs.length) % bs.length;
    e.preventDefault(); e.stopPropagation();
    bs[i].click(); bs[i].focus();
  });
  return strip;
}
const markTab = (strip, k) => strip.querySelectorAll(".ux-tab").forEach(b => {
  const on = b.dataset.k === k;
  b.classList.toggle("is-active", on);
  b.setAttribute("aria-selected", on ? "true" : "false");
  b.tabIndex = on ? 0 : -1;
});

export function installMenus(sky) {
  const $ = id => document.getElementById(id);
  const api = {};

  /* ---------- Section Manager: filter by tab ---------- */
  let secTab = "all";
  function setSecTab(k) {
    const modal = $("sectionModal");
    if (!modal) return;
    secTab = SEC_TITLE[k] ? k : "all";
    const body = $("sectionModalBody") || modal.querySelector(".modal-body");
    let strip = $("uxSecTabs");
    if (!strip && body) {
      strip = tabStrip("uxSecTabs", SEC_TABS, setSecTab, "Section Manager tabs");
      body.before(strip);
    }
    if (strip) markTab(strip, secTab);
    if (body) body.querySelectorAll(":scope > .mgr-group").forEach(g => {
      const rows = g.querySelector(".mgr-table");
      const grp = rows && SEC_GROUP_OF[rows.id];
      g.classList.toggle("ux-tab-hide", secTab !== "all" && grp !== secTab);
    });
    const t = $("sectionModalTitle");
    if (t) t.textContent = SEC_TITLE[secTab];
  }
  /* ---------- Grid editor: Grid Data / Story Data ---------- */
  let gridTab = "both";
  const GRID_TITLE = { both: "Edit Grid & Stories", grid: "Edit Grid Data", stories: "Edit Story Data" };
  function setGridTab(k) {
    const modal = $("gridModal");
    if (!modal) return;
    gridTab = GRID_TITLE[k] ? k : "both";
    const body = modal.querySelector(".grid-modal-body");
    let strip = $("uxGridTabs");
    if (!strip && body) {
      strip = tabStrip("uxGridTabs", [["both", "Both"], ["grid", "Grid Systems"], ["stories", "Stories"]], setGridTab, "Grid editor tabs");
      body.before(strip);
    }
    if (strip) markTab(strip, gridTab);
    if (body) {
      body.classList.toggle("ux-grid-one", gridTab !== "both");
      const gp = body.querySelector(".grid-pane"), sp = body.querySelector(".story-pane-edit");
      if (gp) gp.classList.toggle("ux-tab-hide", gridTab === "stories");
      if (sp) sp.classList.toggle("ux-tab-hide", gridTab === "grid");
    }
    const t = $("gridModalTitle");
    if (t) t.textContent = GRID_TITLE[gridTab];
  }
  // any open from elsewhere (toolbar buttons, app code) starts on "All" / "Both"
  const watchOpen = (id, fn) => {
    const n = $(id);
    if (!n) return;
    new MutationObserver(recs => {
      for (const r of recs) {
        const was = r.oldValue != null && !/\bhidden\b/.test(r.oldValue);
        if (!n.classList.contains("hidden") && !was) { fn(pendingFor(id) || "all"); }
      }
    }).observe(n, { attributes: true, attributeFilter: ["class"], attributeOldValue: true });
  };
  // a routed open leaves its tab here for the (asynchronous) open observer
  const pending = {};
  const pendingFor = id => {
    const p = pending[id]; pending[id] = null;
    return p && performance.now() - p.t < 1500 ? p.tab : null;
  };
  watchOpen("sectionModal", k => setSecTab(k));
  watchOpen("gridModal", k => setGridTab(k === "all" ? "both" : k));

  function openSection(tab) {
    pending.sectionModal = { tab, t: performance.now() };
    sky.openSectionMgr();
    setSecTab(tab);            // already open → just switch the tab
  }
  function openGrid(tab) {
    pending.gridModal = { tab, t: performance.now() };
    sky.openGridEditor();
    setGridTab(tab);
  }

  /* ---------- Analysis Options hub (F5) ---------- */
  function openAnalysisOptions() {
    const rows = [
      ["Active Degrees of Freedom…", "Analyze › Set Active Degrees of Freedom", "Restrict the analysis to a plane (XZ / YZ / XY) or full 3D.", () => sky.openActiveDof && sky.openActiveDof()],
      ["P-Delta Options…", "Define › P-Delta Options", "Model-wide P-Delta: none, non-iterative from mass, or iterative from load patterns.", () => sky.openPDeltaOptions && sky.openPDeltaOptions()],
      ["Mass Source…", "Define › Mass Source", "Where the mass comes from: element self mass, additional mass, load patterns.", () => sky.openMassSource && sky.openMassSource()],
      ["Modal Case…", "Define › Load Cases › Modal", "Number of modes, eigen / Ritz vectors and the stiffness used.", () => sky.openModalCase && sky.openModalCase()],
      ["Load Cases to Run…", "Analyze › Set Load Cases to Run", "Choose which cases the next run solves.", () => sky.openCasesToRun && sky.openCasesToRun()],
    ];
    const list = umEl("div", { class: "ux-hub", role: "list" });
    let dlg = null;
    rows.forEach(([t, etabs, d, fn]) => {
      const b = umEl("button", { type: "button", class: "ux-hub-row", role: "listitem", title: "ETABS: " + etabs }, [
        umEl("b", { text: t }), umEl("span", { class: "muted", text: d })]);
      b.addEventListener("click", () => { dlg && dlg.close(); setTimeout(fn, 0); });
      list.appendChild(b);
    });
    const note = umEl("p", { class: "muted ux-hub-note", text: "Damping, time steps and nonlinear parameters are set per load case (Define › Load Cases)." });
    const close = umEl("button", { type: "button", class: "btn", text: "Close" });
    dlg = umDialog("uxAnalysisOptions", {
      title: "Analysis Options", iconId: "ux-check", cls: "ux-hub-dlg",
      body: [list, note],
      foot: [umEl("span", { class: "muted", text: "Each option opens its own dialog." }), umEl("div", { class: "modal-btns" }, [close])],
    });
    close.addEventListener("click", () => dlg.close());
    return dlg;
  }

  const ROUTE_FN = {
    "sec:materials": () => openSection("materials"), "sec:frame": () => openSection("frame"),
    "sec:shell": () => openSection("shell"), "grid:grid": () => openGrid("grid"),
    "grid:stories": () => openGrid("stories"), "hub:analysis": () => openAnalysisOptions(),
  };

  /* menu DOM clicks (capture, before etabs.js's own row handler) */
  const mb = $("etabsMenubar");
  if (mb) mb.addEventListener("click", e => {
    const it = e.target.closest && e.target.closest(".etabs-menu-item[data-act]");
    const r = it && ACT_ROUTES[it.dataset.act];
    if (!r) return;
    e.stopPropagation(); e.preventDefault();
    sky.etabs && sky.etabs.closeMenus();
    ROUTE_FN[r]();
  }, true);
  /* programmatic: palette / toolbar / shortcuts / scripts */
  if (sky.etabs) {
    const origClick = sky.etabs.clickItem;
    sky.etabs.clickItem = act => {
      const r = ACT_ROUTES[act];
      if (r) { sky.etabs.closeMenus(); ROUTE_FN[r](); return true; }
      return origClick(act);
    };
    const origEx = sky.etabs.explorerClick;
    sky.etabs.explorerClick = label => {
      const r = EXPLORER_ROUTES[label];
      if (r) { ROUTE_FN[r](); return true; }
      return origEx(label);
    };
  }
  const ex = $("etabsExplorer");
  if (ex) ex.addEventListener("click", e => {
    const leaf = e.target.closest && e.target.closest(".ex-leaf");
    if (!leaf || leaf.classList.contains("ex-case")) return;
    const lbl = (leaf.querySelector(".ex-leaf-lbl") || leaf).textContent.trim();
    const r = EXPLORER_ROUTES[lbl];
    if (!r) return;
    e.stopPropagation(); e.preventDefault();
    ROUTE_FN[r]();
  }, true);

  /* ---------- ETABS labels + a tooltip on every item ---------- */
  function relabel() {
    document.querySelectorAll("#etabsMenubar .etabs-menu-item[data-act]").forEach(it => {
      const act = it.dataset.act;
      const lbl = it.querySelector(".mi-label");
      const want = ETABS_LABELS[act];
      if (lbl && want && !it.dataset.uxLbl) {
        // keep the glyph (first child svg), replace the text node(s)
        const old = lbl.textContent.trim();
        [...lbl.childNodes].forEach(n => { if (n.nodeType === 3) n.remove(); });
        lbl.appendChild(document.createTextNode(want));
        it.dataset.uxLbl = old;      // the original SkyFrame label (palette synonyms)
      }
      const h = umHELP[act];
      const name = (lbl || it).textContent.trim();
      if (!it.title || it.dataset.uxTipV !== "2") {
        it.title = h ? `${h.text}\nETABS: ${h.etabs}` : name;
        it.dataset.uxTipV = "2";
      }
      if (!it.getAttribute("aria-label")) {
        it.setAttribute("aria-label", name + (h ? " — " + h.text : ""));
      }
    });
  }
  relabel();
  if (mb) { mb.addEventListener("mouseenter", relabel, { passive: true }); mb.addEventListener("focusin", relabel); }
  setTimeout(relabel, 0);

  /* ---------- Model Explorer keyboard tree ---------- */
  installExplorerTree(sky);

  Object.assign(api, { openSection, openGrid, setSecTab, setGridTab, secTab: () => secTab, gridTab: () => gridTab, openAnalysisOptions, relabel, labels: ETABS_LABELS });
  sky.ux = sky.ux || {};
  sky.ux.menus = api;
}

/* ================= Model Explorer = WAI-ARIA tree ================= */
function installExplorerTree(sky) {
  const ex = document.getElementById("etabsExplorer");
  const body = ex && ex.querySelector(".ex-body");
  if (!body) return;
  let current = null;
  const items = () => [...body.querySelectorAll(".ex-group-head, .ex-leaf")].filter(n => n.offsetParent !== null);
  function decorate() {
    body.setAttribute("role", "tree");
    body.setAttribute("aria-label", "Model Explorer");
    body.querySelectorAll(".ex-group").forEach(g => {
      g.setAttribute("role", "none");
      const h = g.querySelector(":scope > .ex-group-head");
      const l = g.querySelector(":scope > .ex-leaves");
      if (h) {
        h.setAttribute("role", "treeitem");
        h.setAttribute("aria-level", "1");
        h.setAttribute("aria-expanded", g.classList.contains("open") ? "true" : "false");
      }
      if (l) l.setAttribute("role", "group");
    });
    body.querySelectorAll(".ex-leaf").forEach(n => { n.setAttribute("role", "treeitem"); n.setAttribute("aria-level", "2"); });
    const all = [...body.querySelectorAll(".ex-group-head, .ex-leaf")];
    // (no layout reads here: this runs on every explorer mutation)
    if (!current || !current.isConnected || (current.classList.contains("ex-leaf") && !current.closest(".ex-group.open"))) current = all[0] || null;
    all.forEach(n => { n.tabIndex = n === current ? 0 : -1; });
  }
  const focusItem = n => { if (!n) return; current = n; decorate(); n.focus({ preventScroll: false }); };
  let q = 0;
  const later = () => { if (!q) q = requestAnimationFrame(() => { q = 0; decorate(); }); };
  new MutationObserver(later).observe(body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
  body.addEventListener("focusin", e => {
    const n = e.target.closest(".ex-group-head, .ex-leaf");
    if (n && n !== current) { current = n; decorate(); }
  });
  body.addEventListener("keydown", e => {
    const n = e.target.closest && e.target.closest(".ex-group-head, .ex-leaf");
    if (!n || e.ctrlKey || e.metaKey || e.altKey) return;
    const list = items();
    const i = list.indexOf(n);
    const isHead = n.classList.contains("ex-group-head");
    const grp = n.closest(".ex-group");
    let handled = true;
    switch (e.key) {
      case "ArrowDown": focusItem(list[Math.min(i + 1, list.length - 1)]); break;
      case "ArrowUp": focusItem(list[Math.max(i - 1, 0)]); break;
      case "Home": focusItem(list[0]); break;
      case "End": focusItem(list[list.length - 1]); break;
      case "ArrowRight":
        if (isHead && grp && !grp.classList.contains("open")) { grp.classList.add("open"); decorate(); }
        else if (isHead) { const f = grp.querySelector(".ex-leaf"); if (f && f.offsetParent !== null) focusItem(f); }
        break;
      case "ArrowLeft":
        if (isHead && grp && grp.classList.contains("open")) { grp.classList.remove("open"); decorate(); }
        else if (!isHead && grp) focusItem(grp.querySelector(":scope > .ex-group-head"));
        break;
      case "*": body.querySelectorAll(".ex-group").forEach(g => g.classList.add("open")); decorate(); break;
      default:
        if (e.key.length === 1 && /\S/.test(e.key)) {
          const ch = e.key.toLowerCase();
          const rot = list.slice(i + 1).concat(list.slice(0, i + 1));
          const hit = rot.find(x => x.textContent.trim().toLowerCase().startsWith(ch));
          if (hit) focusItem(hit); else handled = false;
        } else handled = false;
    }
    if (handled) { e.preventDefault(); e.stopPropagation(); }
  });
  decorate();
  sky.ux = sky.ux || {};
  sky.ux.explorerTree = { refresh: decorate, focus: () => focusItem(current || items()[0]) };
}
