/* SkyFrame — ETABS-style chrome.
   Builds a top menu bar, a left tool palette, a dockable model explorer and a
   bottom status bar, and DELEGATES every action to the existing app entrypoints
   exposed on window.__sky. This module implements NO analysis/model logic — it
   is pure re-chrome: menus/tree/status simply drive setMode / setTool / setView
   / switchTab / doRun and the existing managers (Section Manager, Grid editor,
   Section Designer, gallery, import, loads editor sections, design sub-tabs). */

import { icon, TOOL_ICON, MENU_ICON, EXGROUP_ICON, EXLEAF_ICON, VIEW_ICON } from "./icons.js";

export function initEtabs(sky) {
  const S = sky.store;
  const $ = id => document.getElementById(id);
  const el = (tag, attrs = {}, kids = []) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k === "html") n.innerHTML = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
      else if (v != null) n.setAttribute(k, v);
    }
    (Array.isArray(kids) ? kids : [kids]).forEach(c =>
      c != null && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return n;
  };
  const toast = (t, m, k, ms) => sky.toast && sky.toast(t, m, k || "info", ms || 4000);

  /* ---------- delegating actions (thin wrappers over __sky) ---------- */
  const drawTool = t => { sky.setMode("model"); sky.setTool(t); syncStatus(); };
  const setView = v => {
    sky.setMode("model");
    sky.setView(v);
    syncStatus();
  };
  const gotoLoads = anchor => {
    sky.setMode("loads");
    syncStatus();
    requestAnimationFrame(() => {
      const s = anchor && document.getElementById(anchor);
      if (s) s.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };
  const showResult = tab => { sky.setMode("analyze"); sky.switchTab(tab); syncStatus(); };
  const showDesign = kind => {
    sky.setMode("analyze");
    sky.switchTab("design");
    sky.setDesignKind(kind);
    syncStatus();
  };
  const runNow = () => { sky.setMode("analyze"); syncStatus(); sky.doRun(); };
  const assignHint = label => {
    sky.setMode("model");
    syncStatus();
    const p = $("propsPanel");
    if (p) p.scrollIntoView({ block: "nearest" });
    const has = (S.selection || []).length;
    toast("Assign · " + label,
      has ? `Edit ${label} in the Properties panel for the current selection.`
          : `Select an element (V), then set ${label} in the Properties panel.`);
  };
  const clickChip = (id, need) => {
    sky.setMode("analyze");
    sky.switchTab("view3d");
    const c = $(id);
    if (c && !c.disabled) c.click();
    else toast("Run analysis first", `${need} becomes available after a solve.`);
    syncStatus();
  };

  /* Options toggles — drive the existing controls so app logic stays in charge */
  let snapOn = true;
  const applySnap = on => {
    snapOn = on;
    if (sky.planEditor) sky.planEditor.snapEnabled = on;
    if (sky.elevEditor) sky.elevEditor.snapEnabled = on;
    if (sky.planEditor && sky.planEditor.refresh) sky.planEditor.refresh();
    syncStatus();
  };
  const toggleSnap = () => applySnap(!snapOn);
  const dispatchChange = node => node && node.dispatchEvent(new Event("change", { bubbles: true }));
  const toggleEdge = () => {
    const c = $("edgeConstraintsChk");
    if (!c) return toast("Edge constraints", "Load a model first.");
    c.checked = !c.checked; dispatchChange(c);
    toast("Edge constraints", c.checked ? "Auto edge constraints ON" : "OFF");
  };
  const setDiaphragm = v => {
    const s = $("diaphragmSelect");
    if (!s) return; s.value = v; dispatchChange(s);
  };
  const setPanelZone = v => {
    const s = $("panelZoneSelect");
    if (!s) return; s.value = v; dispatchChange(s);
  };
  let theme = document.documentElement.getAttribute("data-theme") || "dark";
  const setTheme = t => {
    theme = t;
    document.documentElement.setAttribute("data-theme", t);
    syncStatus();
  };
  const toggleTheme = () => setTheme(theme === "dark" ? "light" : "dark");

  /* ================= TOP MENU BAR ================= */
  const menus = [
    ["File", [
      { label: "New Model…", act: "file-new", hint: "template gallery", fn: () => sky.fileNew() },
      { label: "Template Gallery…", act: "file-gallery", fn: () => sky.openGallery() },
      { label: "Import…", act: "file-import", hint: "DXF / e2k / IFC", fn: () => sky.openImportDialog() },
      { label: "Open…", act: "file-open", hint: "saved models", fn: () => sky.openFileDialog() },
      { sep: true },
      { label: "Save", act: "file-save", fn: () => sky.fileSave() },
      { label: "Save As…", act: "file-saveas", fn: () => sky.openSaveAs() },
      { sep: true },
      { label: "Report…", act: "file-report", hint: "after a solve", fn: () => { const b = $("reportBtn"); if (b && !b.disabled) b.click(); else toast("Report", "Run an analysis first."); } },
    ]],
    ["Define", [
      { label: "Materials…", act: "def-materials", fn: () => sky.openSectionMgr() },
      { label: "Frame Sections…", act: "def-frame", fn: () => sky.openSectionMgr() },
      { label: "Shell Sections…", act: "def-shell", fn: () => sky.openSectionMgr() },
      { label: "Section Designer…", act: "def-designer", hint: "polygon + rebar", fn: () => sky.openSectionDesigner() },
      { sep: true },
      { label: "Grid Systems…", act: "def-grid", fn: () => sky.openGridEditor() },
      { label: "Stories…", act: "def-stories", fn: () => sky.openGridEditor() },
      { sep: true },
      { label: "Load Patterns…", act: "def-patterns", fn: () => gotoLoads("ls-patterns") },
      { label: "Static Load Cases…", act: "def-cases", fn: () => gotoLoads("ls-cases") },
      { label: "Response-Spectrum Cases…", act: "def-rs", fn: () => gotoLoads("ls-rs") },
      { label: "Time-History Cases…", act: "def-th", fn: () => gotoLoads("ls-th") },
      { label: "Pushover Cases…", act: "def-pushover", fn: () => gotoLoads("ls-pushover") },
      { label: "Buckling Cases…", act: "def-buckling", fn: () => gotoLoads("ls-buckling") },
      { label: "Staged Cases…", act: "def-staged", fn: () => gotoLoads("ls-staged") },
      { label: "Load Combinations…", act: "def-combos", fn: () => gotoLoads("ls-combos") },
      { label: "Functions (RS / TH)…", act: "def-functions", fn: () => gotoLoads("ls-functions") },
      { label: "Section Cuts…", act: "def-cuts", fn: () => gotoLoads("ls-cuts") },
      { label: "Mass Source…", act: "def-mass", fn: () => gotoLoads("ls-mass") },
      { sep: true },
      { label: "Code Tools (ASCE 7 · NBCC · EC)…", act: "def-codetools", fn: () => gotoLoads("ls-codetools") },
    ]],
    ["Draw", [
      { label: "Select", act: "draw-select", key: "V", fn: () => drawTool("select") },
      { sep: true },
      { label: "Column", act: "draw-column", key: "C", fn: () => drawTool("column") },
      { label: "Beam", act: "draw-beam", key: "B", fn: () => drawTool("beam") },
      { label: "Brace", act: "draw-brace", key: "X", fn: () => drawTool("brace") },
      { label: "Wall", act: "draw-wall", key: "W", fn: () => drawTool("wall") },
      { label: "Slab", act: "draw-slab", key: "S", fn: () => drawTool("slab") },
      { label: "Opening / Erase", act: "draw-erase", key: "E", fn: () => drawTool("erase") },
      { sep: true },
      { label: "Point Spring", act: "draw-spring", key: "G", fn: () => drawTool("spring") },
      { label: "Line Spring", act: "draw-linespring", key: "K", fn: () => drawTool("linespring") },
      { label: "Link / Device", act: "draw-link", key: "L", fn: () => drawTool("link") },
      { sep: true },
      { label: "Section Cut…", act: "draw-cut", fn: () => gotoLoads("ls-cuts") },
      { label: "Grid…", act: "draw-grid", fn: () => sky.openGridEditor() },
      { sep: true },
      { label: "Plan Draw Mode", act: "draw-plan", fn: () => setView("plan") },
      { label: "Elevation Draw Mode", act: "draw-elev", fn: () => setView("elev") },
    ]],
    ["Assign", [
      { label: "Frame · Section", act: "asn-fsec", fn: () => assignHint("frame section") },
      { label: "Frame · Releases", act: "asn-frel", fn: () => assignHint("end releases") },
      { label: "Frame · Local Axis / Orientation", act: "asn-forient", fn: () => assignHint("orientation") },
      { label: "Frame · Rigid End Offsets", act: "asn-foff", fn: () => assignHint("rigid end offsets") },
      { label: "Frame · Axial Limit", act: "asn-faxial", fn: () => assignHint("axial limit") },
      { label: "Frame · Hinges", act: "asn-fhinge", fn: () => assignHint("plastic hinges") },
      { label: "Frame · Panel Zones", act: "asn-fpz", fn: () => assignHint("panel zones") },
      { sep: true },
      { label: "Shell · Section", act: "asn-ssec", fn: () => assignHint("shell section") },
      { label: "Shell · Area Spring", act: "asn-sspring", fn: () => assignHint("area spring") },
      { label: "Shell · Wind Cp", act: "asn-scp", fn: () => assignHint("wind Cp") },
      { label: "Shell · Layered", act: "asn-slayer", fn: () => assignHint("layered shell") },
      { sep: true },
      { label: "Supports / Springs", act: "asn-support", fn: () => assignHint("supports & springs") },
      { label: "Frame Loads", act: "asn-fload", fn: () => assignHint("member loads") },
      { label: "Area Loads", act: "asn-aload", fn: () => assignHint("area loads") },
    ]],
    ["Analyze", [
      { label: "Run Analysis", act: "an-run", key: "R", fn: () => runNow() },
      { sep: true },
      { label: "Analysis Options (P-Δ · modal · damping)…", act: "an-opts", fn: () => { gotoLoads("ls-cases"); toast("Analysis options", "P-Δ, modal count and damping are set per case in the Cases editor."); } },
      { sep: true },
      { label: "Run FNA (Time History)…", act: "an-fna", fn: () => { showResult("th"); toast("FNA", "Pick a TH case, then click Run FNA."); } },
      { label: "Run Ritz Vectors…", act: "an-ritz", fn: () => { showResult("modal"); toast("Ritz", "Use the Eigen / Ritz basis toggle on the Modal tab."); } },
      { label: "Run Cracked Analysis…", act: "an-cracked", fn: () => { showResult("story"); requestAnimationFrame(() => { const c = $("crackedCard"); if (c) c.scrollIntoView({ block: "start", behavior: "smooth" }); }); } },
      { label: "Buckling Cases…", act: "an-buck", fn: () => gotoLoads("ls-buckling") },
      { label: "Pushover Cases…", act: "an-po", fn: () => gotoLoads("ls-pushover") },
      { label: "Staged Construction…", act: "an-staged", fn: () => gotoLoads("ls-staged") },
    ]],
    ["Display", [
      { label: "3D / Deformed View", act: "dis-view3d", fn: () => showResult("view3d") },
      { label: "Deformed Shape", act: "dis-deformed", fn: () => clickChip("chipDeformed", "The deformed shape") },
      { label: "Mode Shape", act: "dis-mode", fn: () => clickChip("chipMode", "Mode-shape animation") },
      { label: "Shell Contours", act: "dis-contours", fn: () => clickChip("chipContours", "Shell force contours") },
      { sep: true },
      { label: "Story Drifts & Shears", act: "dis-story", fn: () => showResult("story") },
      { label: "Modal", act: "dis-modal", fn: () => showResult("modal") },
      { label: "Reactions", act: "dis-reactions", fn: () => showResult("reactions") },
      { label: "Member Forces", act: "dis-forces", fn: () => showResult("forces") },
      { label: "Drift Optimizer", act: "dis-drift", fn: () => showResult("drift") },
      { sep: true },
      { label: "Time History", act: "dis-th", fn: () => showResult("th") },
      { label: "Pushover + Performance", act: "dis-pushover", fn: () => showResult("pushover") },
      { label: "Buckling", act: "dis-buckling", fn: () => showResult("buckling") },
      { label: "Load Takedown", act: "dis-takedown", fn: () => showResult("takedown") },
      { label: "Section Cuts", act: "dis-cuts", fn: () => showResult("cuts") },
      { label: "Wall Piers", act: "dis-piers", fn: () => showResult("piers") },
      { label: "Serviceability", act: "dis-svc", fn: () => showResult("svc") },
    ]],
    ["Design", [
      { label: "Steel", act: "des-steel", fn: () => showDesign("steel") },
      { label: "Concrete", act: "des-concrete", fn: () => showDesign("concrete") },
      { label: "Wall Pier", act: "des-wall", fn: () => showDesign("wall") },
      { label: "Punching Shear", act: "des-punching", fn: () => showDesign("punching") },
      { label: "Composite Beam", act: "des-composite", fn: () => showDesign("composite") },
      { label: "Two-Way Slab", act: "des-slab", fn: () => showDesign("slab") },
      { label: "Seismic (AISC 341)", act: "des-s341", fn: () => showDesign("seismic341") },
      { sep: true },
      { label: "Section Optimization", act: "des-optimize", fn: () => { showDesign("steel"); requestAnimationFrame(() => { const p = $("optimizePanel"); if (p) p.scrollIntoView({ block: "start", behavior: "smooth" }); }); } },
    ]],
    ["Options", [
      { label: "Units: kN · m · s", act: "opt-units", disabled: true },
      { sep: true },
      { label: "Snap to Grid", act: "opt-snap", check: () => snapOn, fn: () => toggleSnap() },
      { label: "Auto Edge Constraints", act: "opt-edge", check: () => !!(S.model && S.model.edge_constraints), fn: () => toggleEdge() },
      { sep: true },
      { label: "Diaphragm · Rigid", act: "opt-diaph-rigid", check: () => !(S.model && S.model.diaphragm === "none"), fn: () => setDiaphragm("rigid") },
      { label: "Diaphragm · None (semi-rigid)", act: "opt-diaph-none", check: () => !!(S.model && S.model.diaphragm === "none"), fn: () => setDiaphragm("none") },
      { sep: true },
      { label: "Panel Zones · Centerline", act: "opt-pz-none", check: () => !(S.model && (S.model.panel_zones === "rigid" || S.model.panel_zones === "scissors")), fn: () => setPanelZone("none") },
      { label: "Panel Zones · Rigid", act: "opt-pz-rigid", check: () => !!(S.model && S.model.panel_zones === "rigid"), fn: () => setPanelZone("rigid") },
      { label: "Panel Zones · Flexible", act: "opt-pz-scissors", check: () => !!(S.model && S.model.panel_zones === "scissors"), fn: () => setPanelZone("scissors") },
      { sep: true },
      { label: "Theme · Dark", act: "opt-theme-dark", check: () => theme === "dark", fn: () => setTheme("dark") },
      { label: "Theme · Light", act: "opt-theme-light", check: () => theme === "light", fn: () => setTheme("light") },
    ]],
  ];

  const menubar = $("etabsMenubar");
  const actMap = {};        // data-act -> item
  let openWrap = null;
  const closeMenus = () => {
    if (!openWrap) return;
    openWrap.classList.remove("open");
    openWrap.querySelector(".etabs-menu").classList.add("hidden");
    openWrap.querySelector(".etabs-menu-btn").setAttribute("aria-expanded", "false");
    openWrap = null;
  };
  const openMenuWrap = wrap => {
    if (openWrap === wrap) return;
    closeMenus();
    wrap.classList.add("open");
    const dd = wrap.querySelector(".etabs-menu");
    dd.classList.remove("hidden");
    // refresh checkmarks against live model state each open
    dd.querySelectorAll(".etabs-menu-item").forEach(it => {
      const item = actMap[it.dataset.act];
      if (item && item.check) it.classList.toggle("checked", !!item.check());
    });
    wrap.querySelector(".etabs-menu-btn").setAttribute("aria-expanded", "true");
    openWrap = wrap;
  };

  menubar.textContent = "";
  menus.forEach(([name, items]) => {
    const btn = el("button", { class: "etabs-menu-btn", role: "menuitem", "aria-haspopup": "true", "aria-expanded": "false", "data-menu": name });
    if (MENU_ICON[name]) btn.insertAdjacentHTML("afterbegin", icon(MENU_ICON[name], "etabs-menu-ico"));
    btn.appendChild(document.createTextNode(name));
    const dd = el("div", { class: "etabs-menu hidden", role: "menu" });
    items.forEach(item => {
      if (item.sep) { dd.appendChild(el("div", { class: "etabs-menu-sep" })); return; }
      actMap[item.act] = item;
      const row = el("button", {
        class: "etabs-menu-item" + (item.disabled ? " is-disabled" : ""),
        role: "menuitem", "data-act": item.act,
      }, [
        el("span", { class: "mi-check", text: "✓" }),
        el("span", { class: "mi-label", text: item.label }),
        item.key ? el("kbd", { text: item.key }) : (item.hint ? el("span", { class: "mi-hint", text: item.hint }) : null),
      ]);
      if (!item.disabled) row.addEventListener("click", () => { closeMenus(); item.fn && item.fn(); });
      dd.appendChild(row);
    });
    const wrap = el("div", { class: "etabs-menu-wrap" }, [btn, dd]);
    btn.addEventListener("click", e => { e.stopPropagation(); openWrap === wrap ? closeMenus() : openMenuWrap(wrap); });
    btn.addEventListener("mouseenter", () => { if (openWrap && openWrap !== wrap) openMenuWrap(wrap); });
    menubar.appendChild(wrap);
  });
  document.addEventListener("click", e => { if (openWrap && !menubar.contains(e.target)) closeMenus(); });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && openWrap) { closeMenus(); e.stopImmediatePropagation(); }
  }, true);

  /* ================= LEFT TOOL PALETTE ================= */
  const PALETTE = [
    ["select", "Select", "V"], ["column", "Column", "C"], ["beam", "Beam", "B"],
    ["brace", "Brace", "X"], ["wall", "Wall", "W"], ["slab", "Slab", "S"],
    ["link", "Link", "L"], ["spring", "Point spring", "G"], ["linespring", "Line spring", "K"],
    ["erase", "Erase", "E"],
  ];
  const palette = $("etabsPalette");
  palette.textContent = "";
  PALETTE.forEach(([tool, label, key]) => {
    const b = el("button", {
      class: "etabs-tool", "data-tool": tool, title: `${label} (${key})`, "aria-label": label,
      html: icon(TOOL_ICON[tool]),
    });
    b.addEventListener("click", () => drawTool(tool));
    palette.appendChild(b);
  });
  const syncPalette = () => {
    const activeTool = S.mode === "model" ? S.tool : null;
    palette.querySelectorAll(".etabs-tool").forEach(b =>
      b.classList.toggle("is-active", b.dataset.tool === activeTool));
  };

  /* ================= MODEL EXPLORER ================= */
  const TREE = [
    ["Model", [
      ["Stories", () => sky.openGridEditor()],
      ["Grid Systems", () => sky.openGridEditor()],
      ["Frame Members", () => { sky.setMode("model"); syncStatus(); }],
      ["Shells (walls / slabs)", () => { sky.setMode("model"); syncStatus(); }],
      ["Supports & Springs", () => { sky.setMode("model"); syncStatus(); }],
      ["Links", () => { sky.setMode("model"); syncStatus(); }],
    ]],
    ["Definitions", [
      ["Materials", () => sky.openSectionMgr()],
      ["Frame Sections", () => sky.openSectionMgr()],
      ["Shell Sections", () => sky.openSectionMgr()],
      ["Section Designer", () => sky.openSectionDesigner()],
      ["Load Patterns", () => gotoLoads("ls-patterns")],
      ["Load Cases", () => gotoLoads("ls-cases")],
      ["Combinations", () => gotoLoads("ls-combos")],
      ["Functions", () => gotoLoads("ls-functions")],
      ["Mass Source", () => gotoLoads("ls-mass")],
    ]],
    ["Assignments", [
      ["Frame Assignments", () => assignHint("frame properties")],
      ["Shell Assignments", () => assignHint("shell properties")],
      ["Supports", () => assignHint("supports & springs")],
      ["Loads", () => assignHint("member / area loads")],
    ]],
    ["Analysis Results", [
      ["3D / Deformed", () => showResult("view3d")],
      ["Story Results", () => showResult("story")],
      ["Modal", () => showResult("modal")],
      ["Reactions", () => showResult("reactions")],
      ["Member Forces", () => showResult("forces")],
      ["Drift Optimizer", () => showResult("drift")],
      ["Time History", () => showResult("th")],
      ["Pushover", () => showResult("pushover")],
      ["Buckling", () => showResult("buckling")],
      ["Load Takedown", () => showResult("takedown")],
      ["Section Cuts", () => showResult("cuts")],
      ["Wall Piers", () => showResult("piers")],
      ["Serviceability", () => showResult("svc")],
      ["Design", () => showResult("design")],
    ]],
  ];
  const explorer = $("etabsExplorer");
  explorer.textContent = "";
  const exHead = el("div", { class: "ex-head" }, [
    el("span", { class: "ex-title", text: "Model Explorer" }),
    el("button", { class: "ex-collapse", title: "Collapse explorer", "aria-label": "Collapse explorer", html: "&#9664;" }),
  ]);
  explorer.appendChild(exHead);
  const exBody = el("div", { class: "ex-body" });
  explorer.appendChild(exBody);
  let exKey = 0;
  const nodeMap = {};
  TREE.forEach(([group, leaves]) => {
    const grp = el("div", { class: "ex-group open" });
    const gh = el("button", { class: "ex-group-head" }, [
      el("span", { class: "ex-caret", html: "&#9656;" }),
      el("span", { class: "ex-ico", html: icon(EXGROUP_ICON[group] || "exgroup-model") }),
      el("span", { text: group }),
    ]);
    gh.addEventListener("click", () => grp.classList.toggle("open"));
    grp.appendChild(gh);
    const list = el("div", { class: "ex-leaves" });
    leaves.forEach(([label, fn]) => {
      const key = "n" + (exKey++);
      const leaf = el("button", { class: "ex-leaf", "data-node": key }, [
        el("span", { class: "ex-ico", html: icon(EXLEAF_ICON[label] || "exleaf-frames") }),
        el("span", { class: "ex-leaf-lbl", text: label }),
      ]);
      leaf.addEventListener("click", () => { fn(); });
      nodeMap[label] = fn;
      list.appendChild(leaf);
    });
    grp.appendChild(list);
    exBody.appendChild(grp);
  });
  const canvas = $("etabsCanvas");
  const workspace = document.querySelector(".workspace");
  exHead.querySelector(".ex-collapse").addEventListener("click", () => {
    const collapsed = workspace.classList.toggle("explorer-collapsed");
    exHead.querySelector(".ex-collapse").innerHTML = collapsed ? "&#9654;" : "&#9664;";
    setTimeout(() => sky.viewer && sky.viewer._resize && sky.viewer._resize(), 220);
  });

  /* ================= BOTTOM STATUS BAR ================= */
  const status = $("etabsStatus");
  status.textContent = "";
  const storySel = el("select", { class: "sb-select", title: "Active story", "aria-label": "Active story" });
  storySel.addEventListener("change", () => sky.setStory(storySel.value));
  const coordEl = el("span", { class: "sb-coord", text: "—, — m" });
  const snapChip = el("button", { class: "sb-chip", title: "Toggle grid snap", html: icon("status-snap", "sb-ico") }, "Snap");
  snapChip.addEventListener("click", () => toggleSnap());
  const viewSeg = el("div", { class: "sb-seg", role: "group", "aria-label": "View" });
  [["plan", "Plan"], ["elev", "Elev"], ["view3d", "3D"]].forEach(([v, lbl]) => {
    const b = el("button", { class: "sb-seg-btn", "data-view": v, html: icon(VIEW_ICON[v], "sb-ico") }, lbl);
    b.addEventListener("click", () => {
      if (v === "view3d") showResult("view3d");
      else setView(v);
    });
    viewSeg.appendChild(b);
  });
  const runStatus = el("span", { class: "sb-status", id: "sbStatusText", text: "Ready" });
  const runBtn = el("button", { class: "sb-run", title: "Run analysis (R)", html: icon("status-run", "sb-ico") }, "Run");
  runBtn.addEventListener("click", () => runNow());

  status.append(
    el("span", { class: "sb-item" }, [el("label", { class: "sb-lbl", text: "Story" }), storySel]),
    el("span", { class: "sb-sepv" }),
    el("span", { class: "sb-item sb-units", title: "Model units", text: "kN · m · s" }),
    el("span", { class: "sb-sepv" }),
    el("span", { class: "sb-item" }, [el("span", { class: "sb-lbl", text: "Cursor" }), coordEl]),
    el("span", { class: "sb-sepv" }),
    snapChip,
    el("span", { class: "sb-sepv" }),
    viewSeg,
    el("span", { class: "sb-spacer" }),
    runStatus,
    runBtn,
  );

  const rebuildStorySel = () => {
    const m = S.model;
    const cur = storySel.value;
    storySel.textContent = "";
    if (!m || !m.stories) return;
    [...m.stories].reverse().forEach(s => {   // top story first, like ETABS
      const o = document.createElement("option");
      o.value = s.name; o.textContent = s.name;
      storySel.appendChild(o);
    });
    storySel.value = S.story || cur || (m.stories[m.stories.length - 1] || {}).name || "";
  };

  function syncStatus() {
    // active view segment
    let active = S.mode === "model" ? (S.view === "elev" ? "elev" : "plan")
      : (S.mode === "analyze" && S.tab === "view3d" ? "view3d" : null);
    viewSeg.querySelectorAll(".sb-seg-btn").forEach(b =>
      b.classList.toggle("is-active", b.dataset.view === active));
    snapChip.classList.toggle("is-on", snapOn);
    if (storySel.value !== (S.story || "")) storySel.value = S.story || storySel.value;
    syncPalette();
  }

  // Mirror the plan/elevation cursor readout into the status bar.
  const planReadout = $("planReadout");
  if (planReadout) {
    const mo = new MutationObserver(() => {
      const t = planReadout.textContent.trim();
      coordEl.textContent = t || "—, — m";
    });
    mo.observe(planReadout, { childList: true, characterData: true, subtree: true });
  }
  // Mirror the analysis status text + pill state.
  const statusText = $("statusText");
  const statusPill = $("statusPill");
  if (statusText) {
    const syncRun = () => {
      runStatus.textContent = statusText.textContent;
      const cls = (statusPill && statusPill.className) || "";
      runStatus.classList.toggle("is-running", /is-running/.test(cls));
      runStatus.classList.toggle("is-solved", /is-solved/.test(cls));
      runStatus.classList.toggle("is-error", /is-error/.test(cls));
    };
    new MutationObserver(syncRun).observe(statusText, { childList: true, characterData: true, subtree: true });
    if (statusPill) new MutationObserver(syncRun).observe(statusPill, { attributes: true, attributeFilter: ["class"] });
    syncRun();
  }
  // Keep the story dropdown in sync when the app rebuilds/changes stories.
  const nativeStory = $("storySelect");
  if (nativeStory) new MutationObserver(rebuildStorySel).observe(nativeStory, { childList: true });
  const storyBadge = $("planStoryBadge");
  if (storyBadge) new MutationObserver(() => { if (storySel.value !== (S.story || "")) storySel.value = S.story || storySel.value; })
    .observe(storyBadge, { childList: true, characterData: true, subtree: true });

  rebuildStorySel();
  applySnap(true);
  syncStatus();

  /* ================= TEST HOOK ================= */
  sky.etabs = {
    el: { menubar, palette, explorer, status },
    menuNames: () => menus.map(m => m[0]),
    openMenu: name => {
      const wrap = [...menubar.querySelectorAll(".etabs-menu-wrap")]
        .find(w => w.querySelector(".etabs-menu-btn").dataset.menu === name);
      if (wrap) openMenuWrap(wrap);
      return !!wrap;
    },
    closeMenus,
    isMenuOpen: () => !!openWrap,
    clickItem: act => { const it = actMap[act]; if (it && it.fn) { closeMenus(); it.fn(); return true; } return false; },
    hasItem: act => !!actMap[act],
    drawTool, setView, showResult, showDesign, gotoLoads, assignHint, runNow,
    toggleSnap, snapOn: () => snapOn,
    setTheme, theme: () => theme,
    explorerClick: label => { const fn = nodeMap[label]; if (fn) { fn(); return true; } return false; },
    explorerNodes: () => Object.keys(nodeMap),
    toggleExplorer: () => exHead.querySelector(".ex-collapse").click(),
    setStory: v => { storySel.value = v; sky.setStory(v); },
    rebuildStorySel, refresh: syncStatus,
  };
}
