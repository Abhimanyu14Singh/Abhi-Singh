/* SkyFrame — ETABS-style chrome.
   Builds a top menu bar, a left tool palette, a dockable model explorer and a
   bottom status bar, and DELEGATES every action to the existing app entrypoints
   exposed on window.__sky. This module implements NO analysis/model logic — it
   is pure re-chrome: menus/tree/status simply drive setMode / setTool / setView
   / switchTab / doRun and the existing managers (Section Manager, Grid editor,
   Section Designer, gallery, import, loads editor sections, design sub-tabs). */

import { icon, TOOL_ICON, EXGROUP_ICON, EXLEAF_ICON, VIEW_ICON } from "./icons.js";
import { buildMenuTree } from "./menutree.js";       // v1.20 — cascading menu tree (menu architecture)
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

  /* ================= TOP MENU BAR =================
     Menu architecture v1.20 (docs/MENU_ARCHITECTURE.md): the ETABS menu flow
     File · Edit · View · Define · Draw · Select · Assign · Analyze · Display ·
     Options · Tools · Help, with cascading submenus for families (≤ 14
     entries per level), separators between task groups, "…" only on items
     that open a dialog, shortcuts right-aligned, and disabled states that
     say why. Rendering / keyboard / touch / viewport clamping / runtime
     registry live in js/menutree.js. EVERY act id is unchanged — only the
     place in the tree (and the label shown in context) moved. */
  const hasResults = () => !!S.results;
  const NEEDS = {
    results: () => (hasResults() ? null : "run the analysis first (F5) — no results yet"),
    selection: () => ((S.selection || []).length ? null : "select one or more objects first (V)"),
    undo: () => (sky.edit && sky.edit.history && sky.edit.history.state && !sky.edit.history.state().canUndo ? "nothing to undo" : null),
    redo: () => (sky.edit && sky.edit.history && sky.edit.history.state && !sky.edit.history.state().canRedo ? "nothing to redo" : null),
    clipboard: () => (sky.edit && sky.edit.clipboard && !sky.edit.clipboard() ? "copy objects first (Ctrl+C)" : null),
  };
  const fileReport = () => { const b = $("reportBtn"); if (b && !b.disabled) b.click(); else toast("Report", "Run an analysis first."); };
  const scrollToCard = (tab, id) => { showResult(tab); requestAnimationFrame(() => { const c = $(id); if (c) c.scrollIntoView({ block: "start", behavior: "smooth" }); }); };
  const SEP = { sep: true };

  const menus = [
    { name: "File", accel: "f", items: [
      { label: "New Model…", act: "file-new", key: "Alt+N", fn: () => sky.fileNew() },
      { label: "Open…", act: "file-open", key: "Ctrl+O", fn: () => sky.openFileDialog() },
      SEP,
      { label: "Save", act: "file-save", key: "Ctrl+S", fn: () => sky.fileSave() },
      { label: "Save As…", act: "file-saveas", key: "Ctrl+Shift+S", fn: () => sky.openSaveAs() },
      SEP,
      { label: "Import…", act: "file-import", hint: "ETABS .e2k · DXF · IFC", fn: () => sky.openImportDialog() },
      { label: "Template Gallery…", act: "file-gallery", hint: "preset buildings", fn: () => sky.openGallery() },
      SEP,
      { label: "Create Report…", act: "file-rx-create", hint: "print · PDF · HTML", fn: () => sky.openCreateReport && sky.openCreateReport() },   // js/reportx.js
      { label: "Quick Results Report…", act: "file-report", hint: "print-friendly summary of the last run", needs: "results", fn: fileReport },
      { label: "Model Information…", act: "file-rx-info", hint: "counts · last analysis", fn: () => sky.openModelInfo && sky.openModelInfo() },   // js/reportx.js
      SEP,
      { dynamic: "recent" },
    ]},
    // Edit utilities + Undo/Redo (js/editmenu.js, js/history.js) — delegates to sky.edit
    { name: "Edit", accel: "e", items: [
      { label: "Undo", act: "edit-undo", key: "Ctrl+Z", hint: () => (sky.edit ? sky.edit.undoHint() : ""), needs: "undo", fn: () => sky.edit && sky.edit.undo() },
      { label: "Redo", act: "edit-redo", key: "Ctrl+Y", hint: () => (sky.edit ? sky.edit.redoHint() : ""), needs: "redo", fn: () => sky.edit && sky.edit.redo() },
      SEP,
      { label: "Copy", act: "edit-copy", key: "Ctrl+C", needs: "selection", fn: () => sky.edit && sky.edit.copy() },
      { label: "Paste…", act: "edit-paste", key: "Ctrl+V", hint: "with offset", needs: "clipboard", fn: () => sky.edit && sky.edit.paste() },
      { label: "Delete", act: "edit-delete", key: "Del", hint: "with dependents", needs: "selection", fn: () => sky.edit && sky.edit.deleteSelection() },
      SEP,
      { label: "Replicate…", act: "edit-replicate", hint: "linear · radial · mirror · story", fn: () => sky.edit && sky.edit.openReplicate() },
      { label: "Move…", act: "edit-move", fn: () => sky.edit && sky.edit.openMove() },
      { label: "Extrude…", act: "edit-extrude", hint: "points → frames · frames → shells", fn: () => sky.edit && sky.edit.openExtrude() },
      { label: "Edit Points", sub: [
        { label: "Merge Joints…", act: "edit-merge", hint: "tolerance", fn: () => sky.edit && sky.edit.openMerge() },
        { label: "Align Points / Trim-Extend…", act: "edit-align", fn: () => sky.edit && sky.edit.openAlign() },
      ]},
      { label: "Edit Frames", sub: [
        { label: "Divide Frames…", act: "edit-divide", fn: () => sky.edit && sky.edit.openDivide() },
        { label: "Join Frames", act: "edit-join", hint: "collinear members in the selection", fn: () => sky.edit && sky.edit.join() },
      ]},
      SEP,   // Interactive Database Editing (js/dbedit.js) — model tables
      { label: "Interactive Database Editing…", act: "edit-dbedit", hint: "model tables", fn: () => sky.openDbEdit && sky.openDbEdit() },
    ]},
    // View — views, display options, extruded view, selection-only, cut plane (js/viewext.js)
    { name: "View", accel: "v", items: [
      { label: "Set Plan View", act: "draw-plan", fn: () => setView("plan") },
      { label: "Set Elevation View", act: "draw-elev", fn: () => setView("elev") },
      { label: "Set 3D View", act: "view-3d", fn: () => showResult("view3d") },
      SEP,
      { label: "Set Display Options…", act: "vx-display-options", hint: "objects · colours · stories", fn: () => sky.vx && sky.vx.openDisplayOptions() },
      { label: "Extruded View", act: "vx-extrude", key: "Alt+X", hint: "section shapes", check: () => !!(sky.vx && sky.vx.isExtrude()), fn: () => { if (sky.vx && !sky.vx.isExtrude()) showResult("view3d"); sky.vx && sky.vx.toggleExtrude(); } },
      { label: "Show Selection Only", act: "vx-selonly", check: () => !!(sky.vx && sky.vx.isSelOnly()), fn: () => { if (sky.vx && !sky.vx.isSelOnly()) showResult("view3d"); sky.vx && sky.vx.toggleSelOnly(); } },
      { label: "Section Cut Plane View", act: "vx-cut", hint: "clip at a plane", check: () => !!(sky.vx && sky.vx.isCut()), fn: () => { if (sky.vx && !sky.vx.isCut()) showResult("view3d"); sky.vx && sky.vx.toggleCut(); } },
      { label: "Reset Display Options", act: "vx-reset", fn: () => sky.vx && sky.vx.reset() },
      SEP,
      { label: "Model Explorer", act: "view-explorer", check: () => !document.querySelector(".workspace.explorer-collapsed"), fn: () => exHead && exHead.querySelector(".ex-collapse").click() },
      { label: "Quick-Access Toolbar", act: "help-toolbar", key: "Alt+Q", check: () => !!(sky.ux && sky.ux.toolbar && sky.ux.toolbar.isVisible()), fn: () => sky.ux && sky.ux.toolbar && sky.ux.toolbar.toggle() },
    ]},
    { name: "Define", accel: "d", items: [
      { label: "Material Properties…", act: "def-materials", fn: () => sky.openSectionMgr() },
      { label: "Section Properties", sub: [
        { label: "Frame Sections…", act: "def-frame", fn: () => sky.openSectionMgr() },
        { label: "Nonprismatic Frame Sections…", act: "def-np-section", hint: "tapered / haunched", fn: () => sky.openNonprismatic && sky.openNonprismatic() },   // js/npsect.js
        { label: "Section Designer…", act: "def-designer", hint: "polygon + rebar", fn: () => sky.openSectionDesigner() },
        { label: "Slab / Wall Sections…", act: "def-shell", fn: () => sky.openSectionMgr() },
        SEP,
        { label: "Spring Properties…", act: "def-b9-springprops", hint: "point springs · linear · multilinear · gap", fn: () => sky.openSpringProperties && sky.openSpringProperties() },   // B9 (js/springdlg.js)
        { label: "Frame Hinge Properties…", act: "def-uh-hingeprops", hint: "user backbones A–E", fn: () => sky.openHingeProperties && sky.openHingeProperties() },   // B10 (js/uhdlg.js)
        { label: "Tendon Properties…", act: "def-pt-tendons", hint: "PT · losses", fn: () => sky.openTendonProperties && sky.openTendonProperties() },   // PT (js/tendons.js)
      ]},
      { label: "Stories, Grids & Towers", aliases: ["Stories", "Grids", "Stories and Grid Systems"], sub: [
        { label: "Grid Data…", act: "def-grid", fn: () => sky.openGridEditor() },
        { label: "Story Data…", act: "def-stories", fn: () => sky.openGridEditor() },
        { label: "Towers…", act: "def-c9-towers", hint: "multi-tower · podium", fn: () => sky.openTowers && sky.openTowers() },   // C9 (js/towers.js)
      ]},
      { label: "Diaphragms…", act: "def-diaphragms", hint: "named · rigid / semi-rigid", fn: () => sky.openDiaphragms && sky.openDiaphragms() },   // G3
      { label: "Groups…", act: "def-groups", hint: "staging · section cuts", fn: () => sky.openGroups && sky.openGroups() },   // js/groups.js
      { label: "Section Cuts…", act: "def-cuts", fn: () => gotoLoads("ls-cuts") },
      SEP,
      { label: "Mass Source…", act: "def-mass", fn: () => sky.openMassSource() },
      { label: "P-Delta Options…", act: "def-pdelta", hint: "model-wide", fn: () => sky.openPDeltaOptions && sky.openPDeltaOptions() },
      SEP,
      { label: "Functions", sub: [
        { label: "Response Spectrum / Time History…", act: "def-functions", fn: () => gotoLoads("ls-functions") },
        { label: "Steady State / PSD Frequency…", act: "def-freqfn", fn: () => sky.openFreqFunctions() },
      ]},
      { label: "Load Patterns…", act: "def-patterns", fn: () => gotoLoads("ls-patterns") },
      { label: "Auto Lateral Loads", aliases: ["Lateral Loads"], sub: [
        { label: "Seismic / Wind Generators…", act: "def-l116-autolat", hint: "ASCE 7-22 · EC8 · IS 1893 · user", fn: () => sky.openAutoLateral && sky.openAutoLateral() },   // v1.16 (js/loads116.js)
        { label: "Open Structure Wind Pattern…", act: "def-ow-pattern", hint: "ASCE 7 Ch. 29 · frames", fn: () => sky.openOpenWindPattern && sky.openOpenWindPattern() },   // js/openwind.js
        { label: "Code Tools (ASCE 7 · NBCC · EC)…", act: "def-codetools", fn: () => gotoLoads("ls-codetools") },
      ]},
      { label: "Load Cases", sub: [
        { label: "All Load Cases…", act: "def-loadcases", hint: "every case type in one list", fn: () => sky.openLoadCases() },
        SEP,
        { label: "Linear Static…", act: "def-cases", fn: () => gotoLoads("ls-cases") },
        { label: "Modal…", act: "def-modal", hint: "stiffness", fn: () => sky.openModalCase && sky.openModalCase() },   // G3
        { label: "Response Spectrum…", act: "def-rs", fn: () => gotoLoads("ls-rs") },
        { label: "Time History…", act: "def-th", fn: () => gotoLoads("ls-th") },
        { label: "Buckling…", act: "def-buckling", fn: () => gotoLoads("ls-buckling") },
        SEP,
        { label: "Nonlinear Static…", act: "def-nls", hint: "new · chaining", fn: () => sky.openNlsCase && sky.openNlsCase(null) },   // G3
        { label: "Pushover…", act: "def-pushover", fn: () => gotoLoads("ls-pushover") },
        { label: "Staged Construction…", act: "def-staged", fn: () => gotoLoads("ls-staged") },
        SEP,
        { label: "Steady State…", act: "def-ss", fn: () => sky.openFreqCase("steady_state") },
        { label: "Power Spectral Density…", act: "def-psd", fn: () => sky.openFreqCase("psd") },
        { label: "Hyperstatic (PT)…", act: "def-pt-hyper", hint: "PT secondary", fn: () => sky.openHyperstaticCase && sky.openHyperstaticCase(null) },
      ]},
      { label: "Load Combinations…", act: "def-combos", fn: () => sky.openCombos ? sky.openCombos() : gotoLoads("ls-combos") },
    ]},
    { name: "Draw", accel: "r", items: [
      { label: "Select Object", act: "draw-select", key: "V", fn: () => drawTool("select") },
      SEP,
      { label: "Frames", aliases: ["Beam/Column/Brace"], sub: [
        { label: "Columns", act: "draw-column", key: "C", fn: () => drawTool("column") },
        { label: "Beams", act: "draw-beam", key: "B", fn: () => drawTool("beam") },
        { label: "Braces", act: "draw-brace", key: "X", fn: () => drawTool("brace") },
      ]},
      { label: "Floors & Walls", aliases: ["Shells", "Floor/Wall"], sub: [
        { label: "Rectangular Floor", act: "draw-slab", key: "S", fn: () => drawTool("slab") },
        { label: "Wall", act: "draw-wall", key: "W", fn: () => drawTool("wall") },
        SEP,   // G2 — polygon floors / walls / openings (js/polydraw.js)
        { label: "Floor (Polygon, Plan)", act: "draw-polyfloor", fn: () => { sky.polyDraw && sky.polyDraw.start("slab"); syncStatus(); } },
        { label: "Wall (Polygon, Elevation)", act: "draw-polywall", fn: () => { sky.polyDraw && sky.polyDraw.start("wall"); syncStatus(); } },
        { label: "Opening (Polygon)", act: "draw-polyopen", hint: "in the selected shell", fn: () => { sky.polyDraw && sky.polyDraw.start("opening"); syncStatus(); } },
      ]},
      { label: "Links & Springs", aliases: ["Links", "Springs"], sub: [
        { label: "Link / Device", act: "draw-link", key: "L", fn: () => drawTool("link") },
        { label: "Point Spring", act: "draw-spring", key: "G", fn: () => drawTool("spring") },
        { label: "Line Spring", act: "draw-linespring", key: "K", fn: () => drawTool("linespring") },
      ]},
      { label: "Tendon…", act: "draw-pt-tendon", hint: "PT · beams / slab", fn: () => { sky.openDrawTendon && sky.openDrawTendon(); syncStatus(); } },   // PT (js/tendons.js)
      SEP,
      { label: "Section Cut…", act: "draw-cut", fn: () => gotoLoads("ls-cuts") },
      { label: "Grid Lines…", act: "draw-grid", fn: () => sky.openGridEditor() },
      SEP,
      { label: "Erase Object / Add Opening", act: "draw-erase", key: "E", fn: () => drawTool("erase") },
    ]},
    // Select utilities (js/editmenu.js → sky.selectx) + groups (js/groups.js)
    { name: "Select", accel: "s", items: [
      { label: "Select All", act: "sel-all", key: "Ctrl+A", fn: () => sky.selectx && sky.selectx.all() },
      { label: "Invert Selection", act: "sel-invert", fn: () => sky.selectx && sky.selectx.invert() },
      { label: "Previous Selection", act: "sel-prev", fn: () => sky.selectx && sky.selectx.previous() },
      { label: "Clear Selection", act: "sel-clear", needs: "selection", fn: () => sky.handleSelect && sky.handleSelect([], false) },
      SEP,
      { label: "By Property…", act: "sel-prop", hint: "section · material", fn: () => sky.selectx && sky.selectx.openByProperty() },
      { label: "By Story…", act: "sel-story", fn: () => sky.selectx && sky.selectx.openByStory() },
      { label: "By Plane…", act: "sel-plane", hint: "XY · XZ · YZ", fn: () => sky.selectx && sky.selectx.openByPlane() },
      { label: "By Group…", act: "sel-group", fn: () => sky.openSelectByGroup && sky.openSelectByGroup() },
    ]},
    { name: "Assign", accel: "a", items: [
      { label: "Joint", sub: [
        { label: "Restraints / Springs", act: "asn-support", fn: () => assignHint("supports & springs") },
        { label: "Springs (Named Property)…", act: "asn-b9-jsprings", hint: "named property · angle", fn: () => sky.openJointSprings && sky.openJointSprings() },   // B9 (js/springdlg.js)
        { label: "Panel Zone…", act: "asn-np-jpanelzone", hint: "per-joint override", fn: () => sky.openPanelZone && sky.openPanelZone() },   // js/npsect.js
        { label: "Diaphragm…", act: "asn-jdiaph", fn: () => sky.openAssignJointDiaphragm && sky.openAssignJointDiaphragm() },   // G3 (js/diaphdlg.js)
        { label: "Additional Mass…", act: "asn-jmass", fn: () => sky.openJointAddMass && sky.openJointAddMass() },
      ]},
      { label: "Frame", sub: [
        { label: "Section Property", act: "asn-fsec", fn: () => assignHint("frame section") },
        { label: "Releases / Partial Fixity", act: "asn-frel", fn: () => assignHint("end releases") },
        { label: "Local Axes", act: "asn-forient", fn: () => assignHint("orientation") },
        SEP,   // G2 — insertion point / end length offsets (js/insertdlg.js)
        { label: "Insertion Point…", act: "asn-finsert", hint: "cardinal point", fn: () => sky.openInsertionPoint && sky.openInsertionPoint() },
        { label: "End Length Offsets…", act: "asn-fendoff", hint: "auto / define", fn: () => sky.openEndOffsets && sky.openEndOffsets() },
        { label: "Rigid End Offsets", act: "asn-foff", hint: "in the Properties panel", fn: () => assignHint("rigid end offsets") },
        { label: "Tension / Compression Limits", act: "asn-faxial", fn: () => assignHint("axial limit") },
        { label: "Panel Zone", act: "asn-fpz", fn: () => assignHint("panel zones") },
        { label: "Additional Mass…", act: "asn-fmass", fn: () => sky.openFrameAddMass && sky.openFrameAddMass() },
        SEP,
        { label: "Hinges", sub: [
          { label: "Hinges (User-Defined)…", act: "asn-uh-hinges", hint: "property · rel. distance", fn: () => sky.openFrameHinges && sky.openFrameHinges("hinges") },   // B10 (js/uhdlg.js)
          { label: "Hinge Overwrites…", act: "asn-uh-overwrites", fn: () => sky.openFrameHinges && sky.openFrameHinges("overwrites") },
          { label: "Auto Hinges (Properties Panel)", act: "asn-fhinge", fn: () => assignHint("plastic hinges") },
        ]},
        { label: "Frame Auto Mesh Options…", act: "asn-b9-automesh", hint: "joints · intersections", fn: () => sky.openFrameAutoMesh && sky.openFrameAutoMesh() },   // B9 (js/framemesh.js)
        { label: "Output Stations…", act: "asn-b9-stations", fn: () => sky.openOutputStations && sky.openOutputStations() },
      ]},
      { label: "Shell", aliases: ["Area"], sub: [
        { label: "Slab / Wall Section", act: "asn-ssec", fn: () => assignHint("shell section") },
        { label: "Layered Section", act: "asn-slayer", fn: () => assignHint("layered shell") },
        { label: "Area Springs", act: "asn-sspring", fn: () => assignHint("area spring") },
        { label: "Wind Pressure Coefficients", act: "asn-scp", fn: () => assignHint("wind Cp") },
        SEP,
        { label: "Diaphragm…", act: "asn-sdiaph", fn: () => sky.openAssignShellDiaphragm && sky.openAssignShellDiaphragm() },
        { label: "Additional Mass…", act: "asn-smass", fn: () => sky.openShellAddMass && sky.openShellAddMass() },
      ]},
      { label: "Link", sub: [
        { label: "Link Properties…", act: "asn-b9-linkprops", hint: "hysteresis types", fn: () => sky.openLinkProperties && sky.openLinkProperties() },   // B11 (js/linkhyst.js)
      ]},
      SEP,
      { label: "Joint Loads", sub: [
        { label: "Force / Moment…", act: "asn-jforce", fn: () => sky.openJointLoads && sky.openJointLoads("force") },
        { label: "Ground Displacement…", act: "asn-jground", fn: () => sky.openJointLoads && sky.openJointLoads("ground") },
        { label: "Temperature…", act: "asn-l116-jtemp", fn: () => sky.openJointTemperature && sky.openJointTemperature() },
      ]},
      { label: "Frame Loads", sub: [
        { label: "Distributed…", act: "asn-l116-fdist", hint: "uniform / trapezoid · projected", fn: () => sky.openFrameDistributed && sky.openFrameDistributed() },   // v1.16 (js/loads116.js)
        { label: "Point (Concentrated)…", act: "asn-fconc", hint: "force / moment", fn: () => sky.openFrameConcentrated && sky.openFrameConcentrated("moment") },
        { label: "Temperature…", act: "asn-l116-ftemp", hint: "ΔT · gradients", fn: () => sky.openFrameTemperature && sky.openFrameTemperature() },
        { label: "Open Structure Wind Parameters…", act: "asn-ow-params", hint: "Cf · width · shielding", fn: () => sky.openOpenWindParams && sky.openOpenWindParams() },   // js/openwind.js
        SEP,
        { label: "Member Loads (Properties Panel)", act: "asn-fload", fn: () => assignHint("member loads") },
      ]},
      { label: "Shell Loads", aliases: ["Area Loads"], sub: [
        { label: "Uniform…", act: "asn-suniform", hint: "direction · joint pattern", fn: () => sky.openShellUniform && sky.openShellUniform() },
        { label: "Temperature…", act: "asn-l116-stemp", hint: "ΔT · gradient", fn: () => sky.openShellTemperature && sky.openShellTemperature() },
        SEP,
        { label: "Area Loads (Properties Panel)", act: "asn-aload", fn: () => assignHint("area loads") },
      ]},
      SEP,   // Groups — assign the current selection to a group (js/groups.js)
      { label: "Assign Objects to Group…", act: "asn-group", hint: "current selection", fn: () => sky.openAssignGroup && sky.openAssignGroup() },
    ]},
    { name: "Analyze", accel: "y", items: [
      { label: "Set Active Degrees of Freedom…", act: "an-dof", fn: () => sky.openActiveDof() },
      { label: "Set Load Cases to Run…", act: "an-cases-run", fn: () => sky.openCasesToRun() },
      SEP,
      { label: "Check Model…", act: "an-check", key: "Alt+C", fn: () => sky.openCheckModel && sky.openCheckModel() },
      { label: "Check Stability…", act: "an-stability", fn: () => sky.openCheckStability && sky.openCheckStability() },
      { label: "Fix Model…", act: "an-repair", hint: "merge · split · clean", fn: () => sky.openRepair && sky.openRepair() },   // js/repairdlg.js
      SEP,
      { label: "Run Analysis", act: "an-run", key: "F5", fn: () => runNow() },
      { label: "Last Analysis Run Log…", act: "an-log", hint: "P-Δ · combos · warnings", fn: () => sky.openAnalysisLog && sky.openAnalysisLog() },
      SEP,
      { label: "Advanced", sub: [
        { label: "Analysis Options…", act: "an-opts", hint: "DOF · P-Δ · mass · modal", fn: () => { gotoLoads("ls-cases"); toast("Analysis options", "P-Δ, modal count and damping are set per case in the Cases editor."); } },
        { label: "Accuracy Advisor…", act: "an-acc-advisor", hint: "modelling choices", fn: () => sky.openAccuracyAdvisor && sky.openAccuracyAdvisor() },   // js/accuracy.js
        { label: "Mesh Sensitivity Study…", act: "an-acc-meshstudy", hint: "refine · Richardson", fn: () => sky.openMeshStudy && sky.openMeshStudy() },   // js/accuracy.js
        SEP,
        { label: "Time History (FNA)", act: "an-fna", hint: "opens the Time History results to run FNA", fn: () => { showResult("th"); toast("FNA", "Pick a TH case, then click Run FNA."); } },
        { label: "Ritz Vectors", act: "an-ritz", hint: "opens the Modal results (Eigen / Ritz toggle)", fn: () => { showResult("modal"); toast("Ritz", "Use the Eigen / Ritz basis toggle on the Modal tab."); } },
        { label: "Cracked Analysis", act: "an-cracked", hint: "opens the Story results cracked-section card", fn: () => scrollToCard("story", "crackedCard") },
      ]},
      // case definitions live under Define only (ETABS layout; UX audit N3)
    ]},
    { name: "Display", accel: "p", items: [
      { label: "Undeformed Shape", act: "dis-view3d", fn: () => showResult("view3d") },
      { label: "Deformed Shape", act: "dis-deformed", key: "F6", needs: "results", fn: () => clickChip("chipDeformed", "The deformed shape") },
      { label: "Mode Shapes", act: "dis-mode", needs: "results", fn: () => clickChip("chipMode", "Mode-shape animation") },
      { label: "Force Diagrams", aliases: ["Force/Stress Diagrams", "Forces"], sub: [
        { label: "Frame Force Diagrams (3D)…", act: "dis-pf-forces3d", hint: "M3 · V2 · P · reactions", needs: "results", fn: () => sky.openForces3d && sky.openForces3d() },   // js/plotfn.js
        { label: "Show Forces in 3D View", act: "dis-pf-forces3d-toggle", needs: "results", check: () => !!(sky.forces3d && sky.forces3d.on()), fn: () => sky.forces3d && sky.forces3d.toggle() },
        SEP,
        { label: "Frame Forces", act: "dis-forces", needs: "results", fn: () => showResult("forces") },
        { label: "Joint Reactions", act: "dis-reactions", needs: "results", fn: () => showResult("reactions") },
        { label: "Section Cut Forces", act: "dis-cuts", needs: "results", fn: () => showResult("cuts") },
        { label: "Pier Forces", act: "dis-piers", needs: "results", fn: () => showResult("piers") },
        { label: "Tendon Forces…", act: "dis-pt-forces", hint: "P(x) after losses", fn: () => sky.openTendonForces && sky.openTendonForces() },   // PT (js/tendons.js)
      ]},
      { label: "Shell Forces / Stresses", act: "dis-contours", needs: "results", fn: () => clickChip("chipContours", "Shell force contours") },
      SEP,
      { label: "Story Response", sub: [
        { label: "Story Response Plots…", act: "dis-pf-story", hint: "disp · drift · shear · OTM", fn: () => sky.openStoryResponse && sky.openStoryResponse() },   // js/plotfn.js
        { label: "Drifts & Shears", act: "dis-story", needs: "results", fn: () => showResult("story") },
        { label: "Drift Optimizer", act: "dis-drift", needs: "results", fn: () => showResult("drift") },
        { label: "Load Takedown", act: "dis-takedown", needs: "results", fn: () => showResult("takedown") },
        { label: "Serviceability", act: "dis-svc", needs: "results", fn: () => showResult("svc") },
      ]},
      { label: "Modal & Buckling", sub: [
        { label: "Modal Information", act: "dis-modal", needs: "results", fn: () => showResult("modal") },
        { label: "Buckling Factors", act: "dis-buckling", needs: "results", fn: () => showResult("buckling") },
      ]},
      { label: "Plot Functions", sub: [
        { label: "Show Plot Functions…", act: "dis-pf-plotfn", hint: "TH · hysteresis · floor spectra", fn: () => sky.openPlotFunctions && sky.openPlotFunctions() },   // js/plotfn.js
        { label: "Time History Results", act: "dis-th", needs: "results", fn: () => showResult("th") },
        { label: "Frequency Domain (SS · PSD)", act: "dis-freq", needs: "results", fn: () => { showResult("freq"); sky.freq && sky.freq.renderFreq(); } },
      ]},
      { label: "Nonlinear Results", aliases: ["Nonlinear"], sub: [
        { label: "Static Pushover Curve", act: "dis-pushover", needs: "results", fn: () => showResult("pushover") },
        { label: "Nonlinear Static Results…", act: "dis-nls", hint: "history · hinges", fn: () => sky.openNlsResults && sky.openNlsResults() },   // G3
        { label: "Hinge Results…", act: "dis-uh-hinges", hint: "states · step slider", fn: () => sky.openHingeResults && sky.openHingeResults() },   // B10 (js/uhresults.js)
        { label: "Staged Construction Results…", act: "dis-stages", hint: "per stage", fn: () => sky.openStageResults && sky.openStageResults() },   // js/stagedata.js
        { label: "Hyperstatic Results…", act: "dis-pt-hyper", hint: "PT secondary moments", fn: () => sky.openHyperstaticResults && sky.openHyperstaticResults() },
      ]},
      SEP,
      { label: "Show Tables…", act: "dis-tables", key: "Alt+T", hint: "model and analysis results", fn: () => sky.openShowTables && sky.openShowTables() },
      { label: "Show Group…", act: "dis-group", hint: "highlight", fn: () => sky.openShowGroup && sky.openShowGroup() },
    ]},
    { name: "Options", accel: "o", items: [
      { label: "Units…", act: "opt-units", key: "Alt+U", hint: () => "current: " + U.getUnits(), fn: () => sky.openUnitsDialog() },
      SEP,
      { label: "Snap to Grid", act: "opt-snap", check: () => snapOn, fn: () => toggleSnap() },
      { label: "Check Model Before Run", act: "opt-check-before-run", check: () => !!(sky.checkModel && sky.checkModel.beforeRunEnabled()), fn: () => sky.checkModel && sky.checkModel.toggleBeforeRun() },
      { label: "Auto Edge Constraints", act: "opt-edge", check: () => !!(S.model && S.model.edge_constraints), fn: () => toggleEdge() },
      { label: "Frame Auto Mesh Defaults…", act: "opt-b9-automesh", hint: "model-wide", fn: () => sky.openFrameAutoMesh && sky.openFrameAutoMesh("default") },   // B9 (js/framemesh.js)
      SEP,
      { label: "Default Diaphragm", sub: [
        { label: "Rigid", act: "opt-diaph-rigid", radio: () => !(S.model && S.model.diaphragm === "none"), fn: () => setDiaphragm("rigid") },
        { label: "None (Semi-Rigid)", act: "opt-diaph-none", radio: () => !!(S.model && S.model.diaphragm === "none"), fn: () => setDiaphragm("none") },
      ]},
      { label: "Panel Zones", sub: [
        { label: "Centerline", act: "opt-pz-none", radio: () => !(S.model && (S.model.panel_zones === "rigid" || S.model.panel_zones === "scissors")), fn: () => setPanelZone("none") },
        { label: "Rigid", act: "opt-pz-rigid", radio: () => !!(S.model && S.model.panel_zones === "rigid"), fn: () => setPanelZone("rigid") },
        { label: "Flexible (Scissors)", act: "opt-pz-scissors", radio: () => !!(S.model && S.model.panel_zones === "scissors"), fn: () => setPanelZone("scissors") },
      ]},
      { label: "Theme", sub: [
        { label: "Dark", act: "opt-theme-dark", radio: () => theme === "dark", fn: () => setTheme("dark") },
        { label: "Light", act: "opt-theme-light", radio: () => theme === "light", fn: () => setTheme("light") },
      ]},
    ]},
    // Tools — secondary utilities; also hosts "More" for commands added at runtime
    { name: "Tools", accel: "l", items: [
      { label: "Code Checks", aliases: ["Design"], title: "Member code checks on the analysis results (secondary to the analysis)", sub: [
        { label: "Steel Frame", act: "des-steel", needs: "results", fn: () => showDesign("steel") },
        { label: "Steel Frame · Seismic (AISC 341)", act: "des-s341", needs: "results", fn: () => showDesign("seismic341") },
        { label: "Concrete Frame", act: "des-concrete", needs: "results", fn: () => showDesign("concrete") },
        { label: "Composite Beam", act: "des-composite", needs: "results", fn: () => showDesign("composite") },
        SEP,
        { label: "Shear Wall (Pier)", act: "des-wall", needs: "results", fn: () => showDesign("wall") },
        { label: "Slab · Punching Shear", act: "des-punching", needs: "results", fn: () => showDesign("punching") },
        { label: "Slab · Two-Way", act: "des-slab", needs: "results", fn: () => showDesign("slab") },
      ]},
      { label: "Section Optimization", act: "des-optimize", needs: "results", fn: () => { showDesign("steel"); requestAnimationFrame(() => { const p = $("optimizePanel"); if (p) p.scrollIntoView({ block: "start", behavior: "smooth" }); }); } },
    ]},
    // UX pass — Help menu (js/ux.js: palette / shortcuts / start screen)
    { name: "Help", accel: "h", items: [
      { label: "Search Commands…", act: "help-palette", key: "Ctrl+K", fn: () => sky.ux && sky.ux.palette && sky.ux.palette.open() },
      { label: "Keyboard Shortcuts…", act: "help-shortcuts", key: "F1", fn: () => sky.ux && sky.ux.shortcuts && sky.ux.shortcuts.openOverlay() },
      { label: "Start Screen / Templates…", act: "help-start", fn: () => sky.ux && sky.ux.start ? sky.ux.start.open() : sky.fileNew() },
      { label: "Model Workflow", act: "help-workflow", hint: "next steps", fn: () => sky.workflow && sky.workflow.show() },   // guided workflow checklist (js/workflow.js)
    ]},
  ];

  /* File › Recent — most-recently opened / saved models (js/ux_start.js keeps the list) */
  const recentRows = () => {
    let names = [];
    try { const v = JSON.parse(localStorage.getItem("skyframe.ux.recentFiles") || "[]"); if (Array.isArray(v)) names = v; } catch { names = []; }
    names = names.filter(n => typeof n === "string" && n).slice(0, 5);
    const out = [el("div", { class: "sky-menu-header", role: "presentation", text: "Recent" })];
    if (!names.length) {
      out.push(el("button", { type: "button", class: "etabs-menu-item sky-recent is-disabled", role: "menuitem", tabindex: "-1", "aria-disabled": "true",
        title: "Models you open or save appear here" }, [el("span", { class: "mi-check" }), el("span", { class: "mi-label", text: "No recent models" }), el("span", { class: "mi-key" })]));
      return out;
    }
    names.forEach(n => {
      const cur = n === S.fileName;
      const b = el("button", { type: "button", class: "etabs-menu-item sky-recent" + (cur ? " checked" : ""), role: "menuitem", tabindex: "-1",
        "data-recent": n, title: cur ? `“${n}” is the open model` : `Open “${n}”` }, [
        el("span", { class: "mi-check", text: "✓" }), el("span", { class: "mi-label", text: n }), el("span", { class: "mi-key" })]);
      b.addEventListener("click", () => {
        menuApi.closeMenus();
        if (cur) return;
        openRecent(n);
      });
      out.push(b);
    });
    return out;
  };
  async function openRecent(name) {
    if (S.dirty && !window.confirm(`Open “${name}”? Unsaved edits to the current model will be lost.`)) return;
    try {
      const m = await sky.filesApi.open(name);
      sky.adoptModel(m, name);
      toast("Model opened", `“${name}” is now the working model`);
    } catch (e) { toast("Open failed", e.message, "error", 6000); }
  }

  const menubar = $("etabsMenubar");
  const menuApi = buildMenuTree({ menubar, menus, needs: NEEDS, dynamic: { recent: recentRows } });
  const actMap = menuApi.actMap;        // data-act -> item (shared with the registry)
  const closeMenus = menuApi.closeMenus;
  const openMenuWrap = w => menuApi.openMenuWrap(w);
  // runtime registry for other modules: __sky.menu.add("Assign/Frame Loads", item)
  sky.menu = {
    add: (path, item) => menuApi.add(path, item),
    remove: act => menuApi.remove(act),
    path: act => menuApi.pathOf(act),
    open: path => menuApi.openPath(path),
    close: () => closeMenus(),
    stats: () => menuApi.stats(),
    refresh: () => menuApi.refresh(),
    disabledReason: act => menuApi.reasonOf(act),
    acts: () => Object.keys(actMap),
    item: act => actMap[act] || null,
  };

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
      ["Accuracy Advisor", () => sky.openAccuracyAdvisor && sky.openAccuracyAdvisor()],   // Model > Accuracy Advisor (js/accuracy.js)
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
    menuNames: () => menus.map(m => m.name),
    openMenu: name => {
      const wrap = [...menubar.querySelectorAll(".etabs-menu-wrap")]
        .find(w => w.querySelector(".etabs-menu-btn").dataset.menu === name);
      if (wrap) openMenuWrap(wrap);
      return !!wrap;
    },
    closeMenus,
    isMenuOpen: () => menuApi.isOpen(),
    openPath: path => menuApi.openPath(path),     // v1.20 — "Assign/Frame/Hinges"
    menuPath: act => menuApi.pathOf(act),
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
