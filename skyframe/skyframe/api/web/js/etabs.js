/* SkyFrame — ETABS-style chrome.
   Builds a top menu bar, a left tool palette, a dockable model explorer and a
   bottom status bar, and DELEGATES every action to the existing app entrypoints
   exposed on window.__sky. This module implements NO analysis/model logic — it
   is pure re-chrome: menus/tree/status simply drive setMode / setTool / setView
   / switchTab / doRun and the existing managers (Section Manager, Grid editor,
   Section Designer, gallery, import, loads editor sections, design sub-tabs). */

import { icon, TOOL_ICON, MENU_ICON, EXGROUP_ICON, EXLEAF_ICON, VIEW_ICON, MENUITEM_ICON } from "./icons.js";
import U from "./units.js";                       // v1.13 — status-bar units selector
import * as ME from "./modeledit.js";             // v1.13 — case list / DOF presets

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
      // Groups — ETABS Define > Groups (js/groups.js)
      { label: "Groups…", act: "def-groups", hint: "staging · section cuts", fn: () => sky.openGroups && sky.openGroups() },
      { sep: true },
      { label: "Load Patterns…", act: "def-patterns", fn: () => gotoLoads("ls-patterns") },
      { label: "Load Cases…", act: "def-loadcases", hint: "all types", fn: () => sky.openLoadCases() },
      { label: "Static Load Cases…", act: "def-cases", fn: () => gotoLoads("ls-cases") },
      { label: "Response-Spectrum Cases…", act: "def-rs", fn: () => gotoLoads("ls-rs") },
      { label: "Time-History Cases…", act: "def-th", fn: () => gotoLoads("ls-th") },
      { label: "Pushover Cases…", act: "def-pushover", fn: () => gotoLoads("ls-pushover") },
      { label: "Nonlinear Static Case…", act: "def-nls", hint: "new · chaining", fn: () => sky.openNlsCase && sky.openNlsCase(null) },   // G3
      { label: "Modal Case…", act: "def-modal", hint: "stiffness", fn: () => sky.openModalCase && sky.openModalCase() },   // G3
      { label: "Buckling Cases…", act: "def-buckling", fn: () => gotoLoads("ls-buckling") },
      { label: "Staged Cases…", act: "def-staged", fn: () => gotoLoads("ls-staged") },
      { label: "Steady-State Cases…", act: "def-ss", fn: () => sky.openFreqCase("steady_state") },
      { label: "Power Spectral Density Cases…", act: "def-psd", fn: () => sky.openFreqCase("psd") },
      { label: "Frequency Functions (SS / PSD)…", act: "def-freqfn", fn: () => sky.openFreqFunctions() },
      { label: "Load Combinations…", act: "def-combos", fn: () => sky.openCombos ? sky.openCombos() : gotoLoads("ls-combos") },
      { label: "Functions (RS / TH)…", act: "def-functions", fn: () => gotoLoads("ls-functions") },
      { label: "Section Cuts…", act: "def-cuts", fn: () => gotoLoads("ls-cuts") },
      { label: "Mass Source…", act: "def-mass", fn: () => sky.openMassSource() },
      { label: "P-Delta Options…", act: "def-pdelta", hint: "model-wide", fn: () => sky.openPDeltaOptions && sky.openPDeltaOptions() },
      { label: "Diaphragms…", act: "def-diaphragms", hint: "named · rigid / semi-rigid", fn: () => sky.openDiaphragms && sky.openDiaphragms() },   // G3

      // B9 — named point-spring properties (js/springdlg.js)
      { label: "Spring Properties · Point Springs…", act: "def-b9-springprops", hint: "linear · multilinear · gap", fn: () => sky.openSpringProperties && sky.openSpringProperties() },
      // PT — tendon properties + hyperstatic case (js/tendons.js)
      { label: "Tendon Properties…", act: "def-pt-tendons", hint: "PT · losses", fn: () => sky.openTendonProperties && sky.openTendonProperties() },
      { label: "Hyperstatic Load Case…", act: "def-pt-hyper", hint: "PT secondary", fn: () => sky.openHyperstaticCase && sky.openHyperstaticCase(null) },
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
      // G2 — polygon floors / walls / openings (js/polydraw.js)
      { label: "Draw Floor (Polygon)", act: "draw-polyfloor", hint: "plan", fn: () => { sky.polyDraw && sky.polyDraw.start("slab"); syncStatus(); } },
      { label: "Draw Wall (Polygon)", act: "draw-polywall", hint: "elevation", fn: () => { sky.polyDraw && sky.polyDraw.start("wall"); syncStatus(); } },
      { label: "Draw Opening (Polygon)", act: "draw-polyopen", hint: "in selected shell", fn: () => { sky.polyDraw && sky.polyDraw.start("opening"); syncStatus(); } },
      { label: "Opening / Erase", act: "draw-erase", key: "E", fn: () => drawTool("erase") },
      { sep: true },
      { label: "Point Spring", act: "draw-spring", key: "G", fn: () => drawTool("spring") },
      { label: "Line Spring", act: "draw-linespring", key: "K", fn: () => drawTool("linespring") },
      { label: "Link / Device", act: "draw-link", key: "L", fn: () => drawTool("link") },
      { label: "Draw Tendon…", act: "draw-pt-tendon", hint: "PT · beams / slab", fn: () => { sky.openDrawTendon && sky.openDrawTendon(); syncStatus(); } },   // PT (js/tendons.js)
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
      // G2 — ETABS Insertion Point / End Length Offsets dialogs (js/insertdlg.js)
      { label: "Frame · Insertion Point…", act: "asn-finsert", hint: "cardinal point", fn: () => sky.openInsertionPoint && sky.openInsertionPoint() },
      { label: "Frame · End Length Offsets…", act: "asn-fendoff", hint: "auto / define", fn: () => sky.openEndOffsets && sky.openEndOffsets() },
      { label: "Frame · Axial Limit", act: "asn-faxial", fn: () => assignHint("axial limit") },
      { label: "Frame · Hinges", act: "asn-fhinge", fn: () => assignHint("plastic hinges") },
      { label: "Frame · Panel Zones", act: "asn-fpz", fn: () => assignHint("panel zones") },
      // B9 — frame auto mesh + output stations (js/framemesh.js)
      { label: "Frame · Frame Auto Mesh Options…", act: "asn-b9-automesh", hint: "joints · intersections", fn: () => sky.openFrameAutoMesh && sky.openFrameAutoMesh() },
      { label: "Frame · Output Stations…", act: "asn-b9-stations", fn: () => sky.openOutputStations && sky.openOutputStations() },
      { sep: true },
      { label: "Shell · Section", act: "asn-ssec", fn: () => assignHint("shell section") },
      { label: "Shell · Area Spring", act: "asn-sspring", fn: () => assignHint("area spring") },
      { label: "Shell · Wind Cp", act: "asn-scp", fn: () => assignHint("wind Cp") },
      { label: "Shell · Layered", act: "asn-slayer", fn: () => assignHint("layered shell") },
      { sep: true },
      { label: "Supports / Springs", act: "asn-support", fn: () => assignHint("supports & springs") },
      // B9/B11 — joint springs with named properties (js/springdlg.js) · link hysteresis (js/linkhyst.js)
      { label: "Joint · Springs…", act: "asn-b9-jsprings", hint: "named property · angle", fn: () => sky.openJointSprings && sky.openJointSprings() },
      { label: "Link · Link Properties…", act: "asn-b9-linkprops", hint: "hysteresis types", fn: () => sky.openLinkProperties && sky.openLinkProperties() },
      { label: "Frame Loads", act: "asn-fload", fn: () => assignHint("member loads") },
      { label: "Area Loads", act: "asn-aload", fn: () => assignHint("area loads") },
      { sep: true },
      { label: "Joint Loads · Force / Moment…", act: "asn-jforce", fn: () => sky.openJointLoads && sky.openJointLoads("force") },
      { label: "Joint Loads · Ground Displacement…", act: "asn-jground", fn: () => sky.openJointLoads && sky.openJointLoads("ground") },
      { label: "Frame Loads · Concentrated…", act: "asn-fconc", hint: "force / moment", fn: () => sky.openFrameConcentrated && sky.openFrameConcentrated("moment") },
      { label: "Shell Loads · Uniform…", act: "asn-suniform", hint: "direction · joint pattern", fn: () => sky.openShellUniform && sky.openShellUniform() },
      // G3 — named diaphragms + additional mass (js/diaphdlg.js)
      { sep: true },
      { label: "Joint · Diaphragm…", act: "asn-jdiaph", fn: () => sky.openAssignJointDiaphragm && sky.openAssignJointDiaphragm() },
      { label: "Shell · Diaphragm…", act: "asn-sdiaph", fn: () => sky.openAssignShellDiaphragm && sky.openAssignShellDiaphragm() },
      { label: "Joint · Additional Mass…", act: "asn-jmass", fn: () => sky.openJointAddMass && sky.openJointAddMass() },
      { label: "Frame · Additional Mass…", act: "asn-fmass", fn: () => sky.openFrameAddMass && sky.openFrameAddMass() },
      { label: "Shell · Additional Mass…", act: "asn-smass", fn: () => sky.openShellAddMass && sky.openShellAddMass() },

      // Groups — assign the current selection to a group (js/groups.js)
      { sep: true },
      { label: "Assign Objects to Group…", act: "asn-group", hint: "selection", fn: () => sky.openAssignGroup && sky.openAssignGroup() },
    ]],
    // Groups — ETABS Select menu (js/groups.js)
    ["Select", [
      { label: "Select by Group…", act: "sel-group", fn: () => sky.openSelectByGroup && sky.openSelectByGroup() },
      { label: "Clear Selection", act: "sel-clear", fn: () => sky.handleSelect && sky.handleSelect([], false) },
    ]],
    ["Analyze", [
      { label: "Set Load Cases to Run…", act: "an-cases-run", fn: () => sky.openCasesToRun() },
      { label: "Set Active Degrees of Freedom…", act: "an-dof", fn: () => sky.openActiveDof() },
      { label: "Run Analysis", act: "an-run", key: "R", fn: () => runNow() },
      { label: "Check Model…", act: "an-check", fn: () => sky.openCheckModel && sky.openCheckModel() },
      { label: "Check Stability…", act: "an-stability", fn: () => sky.openCheckStability && sky.openCheckStability() },
      { label: "Analysis Log…", act: "an-log", hint: "P-Δ · combos · warnings", fn: () => sky.openAnalysisLog && sky.openAnalysisLog() },
      { sep: true },
      { label: "Analysis Options (P-Δ · modal · damping)…", act: "an-opts", fn: () => { gotoLoads("ls-cases"); toast("Analysis options", "P-Δ, modal count and damping are set per case in the Cases editor."); } },
      { sep: true },
      { label: "Run FNA (Time History)…", act: "an-fna", fn: () => { showResult("th"); toast("FNA", "Pick a TH case, then click Run FNA."); } },
      { label: "Run Ritz Vectors…", act: "an-ritz", fn: () => { showResult("modal"); toast("Ritz", "Use the Eigen / Ritz basis toggle on the Modal tab."); } },
      { label: "Run Cracked Analysis…", act: "an-cracked", fn: () => { showResult("story"); requestAnimationFrame(() => { const c = $("crackedCard"); if (c) c.scrollIntoView({ block: "start", behavior: "smooth" }); }); } },
      { label: "Buckling Cases…", act: "an-buck", fn: () => gotoLoads("ls-buckling") },
      { label: "Pushover Cases…", act: "an-po", fn: () => gotoLoads("ls-pushover") },
      { label: "Staged Construction…", act: "an-staged", fn: () => gotoLoads("ls-staged") },
      { label: "Frequency-Domain Cases (SS / PSD)…", act: "an-freq", fn: () => gotoLoads("ls-freq") },
    ]],
    ["Display", [
      { label: "3D / Deformed View", act: "dis-view3d", fn: () => showResult("view3d") },
      { label: "Deformed Shape", act: "dis-deformed", fn: () => clickChip("chipDeformed", "The deformed shape") },
      { label: "Mode Shape", act: "dis-mode", fn: () => clickChip("chipMode", "Mode-shape animation") },
      { label: "Shell Contours", act: "dis-contours", fn: () => clickChip("chipContours", "Shell force contours") },
      { label: "Show Tables…", act: "dis-tables", hint: "analysis results", fn: () => sky.openShowTables && sky.openShowTables() },
      // Groups — highlight groups; per-stage staged results (js/groups.js, js/stagedata.js)
      { label: "Show Group…", act: "dis-group", hint: "highlight", fn: () => sky.openShowGroup && sky.openShowGroup() },
      { label: "Staged Construction Stages…", act: "dis-stages", hint: "per stage", fn: () => sky.openStageResults && sky.openStageResults() },
      { sep: true },
      { label: "Story Drifts & Shears", act: "dis-story", fn: () => showResult("story") },
      { label: "Modal", act: "dis-modal", fn: () => showResult("modal") },
      { label: "Reactions", act: "dis-reactions", fn: () => showResult("reactions") },
      { label: "Member Forces", act: "dis-forces", fn: () => showResult("forces") },
      { label: "Drift Optimizer", act: "dis-drift", fn: () => showResult("drift") },
      { sep: true },
      { label: "Time History", act: "dis-th", fn: () => showResult("th") },
      { label: "Pushover + Performance", act: "dis-pushover", fn: () => showResult("pushover") },
      { label: "Nonlinear Static Results…", act: "dis-nls", hint: "history · hinges", fn: () => sky.openNlsResults && sky.openNlsResults() },   // G3
      { label: "Frequency Domain (Steady State · PSD)", act: "dis-freq", fn: () => { showResult("freq"); sky.freq && sky.freq.renderFreq(); } },
      { label: "Buckling", act: "dis-buckling", fn: () => showResult("buckling") },
      { label: "Load Takedown", act: "dis-takedown", fn: () => showResult("takedown") },
      { label: "Section Cuts", act: "dis-cuts", fn: () => showResult("cuts") },
      { label: "Wall Piers", act: "dis-piers", fn: () => showResult("piers") },
      { label: "Serviceability", act: "dis-svc", fn: () => showResult("svc") },
      // PT — tendon force P(x) + hyperstatic results (js/tendons.js)
      { sep: true },
      { label: "Tendon Forces…", act: "dis-pt-forces", hint: "P(x) after losses", fn: () => sky.openTendonForces && sky.openTendonForces() },
      { label: "Hyperstatic Results…", act: "dis-pt-hyper", hint: "secondary moments", fn: () => sky.openHyperstaticResults && sky.openHyperstaticResults() },
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
      { label: "Units…", act: "opt-units", hint: () => U.getUnits(), fn: () => sky.openUnitsDialog() },
      { sep: true },
      { label: "Snap to Grid", act: "opt-snap", check: () => snapOn, fn: () => toggleSnap() },
      { label: "Check Model Before Run", act: "opt-check-before-run", check: () => !!(sky.checkModel && sky.checkModel.beforeRunEnabled()), fn: () => sky.checkModel && sky.checkModel.toggleBeforeRun() },
      { label: "Auto Edge Constraints", act: "opt-edge", check: () => !!(S.model && S.model.edge_constraints), fn: () => toggleEdge() },
      { label: "Frame Auto Mesh Defaults…", act: "opt-b9-automesh", hint: "model-wide", fn: () => sky.openFrameAutoMesh && sky.openFrameAutoMesh("default") },   // B9 (js/framemesh.js)
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
      if (item && typeof item.hint === "function") {
        const h = it.querySelector(".mi-hint");
        if (h) h.textContent = item.hint();
      }
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
      const lbl = el("span", { class: "mi-label", text: item.label });
      if (MENUITEM_ICON[item.act]) lbl.insertAdjacentHTML("afterbegin", icon(MENUITEM_ICON[item.act], "mi-ico"));
      const hint = typeof item.hint === "function" ? item.hint() : item.hint;
      const row = el("button", {
        class: "etabs-menu-item" + (item.disabled ? " is-disabled" : ""),
        role: "menuitem", "data-act": item.act,
      }, [
        el("span", { class: "mi-check", text: "✓" }),
        lbl,
        item.key ? el("kbd", { text: item.key }) : (hint ? el("span", { class: "mi-hint", text: hint }) : null),
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
      ["Mass Source", () => sky.openMassSource()],
      ["Set Load Cases to Run", () => sky.openCasesToRun()],
      ["Active Degrees of Freedom", () => sky.openActiveDof()],
      ["Units", () => sky.openUnitsDialog()],
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
      ["Frequency Domain", () => { showResult("freq"); sky.freq && sky.freq.renderFreq(); }],
      ["Show Tables", () => sky.openShowTables && sky.openShowTables()],
      ["Buckling", () => showResult("buckling")],
      ["Load Takedown", () => showResult("takedown")],
      ["Section Cuts", () => showResult("cuts")],
      ["Wall Piers", () => showResult("piers")],
      ["Serviceability", () => showResult("svc")],
      ["Design", () => showResult("design")],
    ]],
  ];
  /* v1.13 — "Load Cases" group: every analysis case (static, modal, RS, TH,
     pushover, buckling, staged); cases set to Do not Run are greyed with a
     badge, and the last-run status shows on hover. Rebuilt on model/results
     changes. Click → the case's editor section; double-click → Set Load
     Cases to Run. */
  const casesGroup = el("div", { class: "ex-group ex-cases open" });
  const casesHead = el("button", { class: "ex-group-head" }, [
    el("span", { class: "ex-caret", html: "&#9656;" }),
    el("span", { class: "ex-ico", html: icon("cases-run") }),
    el("span", { text: "Load Cases" }),
    el("span", { class: "ex-count", id: "exCasesCount" }),
  ]);
  casesHead.addEventListener("click", () => casesGroup.classList.toggle("open"));
  const casesList = el("div", { class: "ex-leaves", id: "exCasesList" });
  casesGroup.append(casesHead, casesList);
  const KIND_ANCHOR = { static: "ls-cases", modal: null, rs: "ls-rs", th: "ls-th",
    pushover: "ls-pushover", buckling: "ls-buckling", staged: "ls-staged" };
  const STATUS_TXT = { finished: "finished", not_run: "not run", run_as_dependency: "run as dependency", failed: "failed" };
  const refreshCases = () => {
    const m = S.model;
    casesList.textContent = "";
    const cases = m ? ME.allAnalysisCases(m) : [];
    const cnt = casesGroup.querySelector("#exCasesCount");
    const off = cases.filter(c => ME.caseNotRun(m, c.name)).length;
    cnt.textContent = cases.length ? (off ? `${cases.length - off}/${cases.length}` : String(cases.length)) : "";
    cnt.title = off ? `${off} case(s) set to Do not Run` : "";
    for (const c of cases) {
      const nr = ME.caseNotRun(m, c.name);
      const st = S.results && sky.caseRunStatus ? sky.caseRunStatus(S.results, c.name, c.kind) : null;
      const leaf = el("button", {
        class: "ex-leaf ex-case" + (nr ? " is-not-run" : ""), "data-case": c.name,
        title: `${c.name} · ${c.type}` + (nr ? " · Do not Run" : " · Run") +
          (st ? ` · last run: ${STATUS_TXT[st] || st}` : "") + "\nDouble-click: Set Load Cases to Run…",
      }, [
        el("span", { class: "ex-ico", html: icon(EXLEAF_ICON[c.kind === "modal" ? "Modal" : "Load Cases"] || "exleaf-cases") }),
        el("span", { class: "ex-leaf-lbl", text: c.name }),
        nr ? el("span", { class: "ex-notrun", text: "not run" }) : null,
      ]);
      leaf.addEventListener("click", () => {
        if ((c.kind === "steady_state" || c.kind === "psd") && sky.openFreqCase) return void sky.openFreqCase(c.kind, c.name);
        if (c.kind === "nonlinear_static" && sky.openNlsCase) return void sky.openNlsCase(c.name);   // G3
        if (c.kind === "hyperstatic" && sky.openHyperstaticCase) return void sky.openHyperstaticCase(c.name);   // PT (js/tendons.js)
        const a = KIND_ANCHOR[c.kind];
        if (a) gotoLoads(a); else showResult("modal");
      });
      leaf.addEventListener("dblclick", () => sky.openCasesToRun());
      casesList.appendChild(leaf);
    }
  };

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
    if (group === "Definitions") exBody.appendChild(casesGroup);   // v1.13
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

  /* v1.13 — ETABS-style units selector (bottom-right) + active-DOF chip */
  const unitsSel = el("select", { class: "sb-select sb-units-sel", id: "sbUnitsSelect",
    title: "Display units — every input, table, diagram, CSV and report converts; the model stays SI",
    "aria-label": "Display units" });
  for (const n of U.UNIT_SET_NAMES) {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    unitsSel.appendChild(o);
  }
  unitsSel.value = U.getUnits();
  unitsSel.addEventListener("change", () => sky.setDisplayUnits(unitsSel.value));
  const unitsItem = el("span", { class: "sb-item sb-units", title: "Display units" }, [
    el("span", { class: "sb-ico-wrap", html: icon("units", "sb-ico") }), unitsSel]);
  const dofChip = el("button", { class: "sb-chip sb-dof", id: "sbDofChip",
    title: "Active degrees of freedom — click to edit", html: icon("active-dof", "sb-ico") });
  const dofTxt = el("span", { class: "sb-dof-txt", text: "3D" });
  dofChip.appendChild(dofTxt);
  dofChip.addEventListener("click", () => sky.openActiveDof());
  const syncSetup = () => {
    if (unitsSel.value !== U.getUnits()) unitsSel.value = U.getUnits();
    const dofs = (S.model && S.model.active_dof) || ME.DOF_NAMES;
    const key = ME.dofPresetOf(dofs);
    dofTxt.textContent = key || "Custom";
    dofChip.title = `Active DOF: ${ME.normalizeActiveDof(dofs).join(", ")}` +
      (key ? ` (${ME.DOF_PRESETS[key].label})` : "") + " — click to edit";
    dofChip.classList.toggle("is-on", key !== "3D");
  };

  status.append(
    el("span", { class: "sb-item" }, [el("label", { class: "sb-lbl", text: "Story" }), storySel]),
    el("span", { class: "sb-sepv" }),
    el("span", { class: "sb-item" }, [el("span", { class: "sb-lbl", text: "Cursor" }), coordEl]),
    el("span", { class: "sb-sepv" }),
    snapChip,
    el("span", { class: "sb-sepv" }),
    viewSeg,
    el("span", { class: "sb-spacer" }),
    dofChip,
    unitsItem,
    el("span", { class: "sb-sepv" }),
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
    syncSetup();                                   // v1.13
  }

  // Mirror the plan/elevation cursor readout into the status bar.
  const planReadout = $("planReadout");
  if (planReadout) {
    const mo = new MutationObserver(() => {
      const t = planReadout.textContent.trim();
      coordEl.textContent = t || `—, — ${U.label("length")}`;
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
  // v1.13 — case list / status chips follow model + results + unit changes
  refreshCases();
  document.addEventListener("sky:model-changed", () => { refreshCases(); syncSetup(); });
  document.addEventListener("sky:results-changed", () => refreshCases());
  document.addEventListener("sky:units-changed", () => {
    syncSetup();
    if (!$("planReadout") || !$("planReadout").textContent.trim()) coordEl.textContent = `—, — ${U.label("length")}`;
  });

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
    rebuildStorySel, refresh: () => { syncStatus(); refreshCases(); },
    // v1.13
    refreshCases, unitsSelect: unitsSel, dofChip,
  };
}
