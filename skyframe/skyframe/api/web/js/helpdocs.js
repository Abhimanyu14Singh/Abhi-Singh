/* SkyFrame — contextual help text (UX pass).
   One short entry per important dialog / command: what it does and the
   ETABS-equivalent command path. Keyed by the ETABS menu `act` id (js/etabs.js);
   DIALOG_HELP maps a dialog backdrop id → act so the "?" button in every
   dialog header (js/ux_dialogs.js) can find its entry. TITLE_HELP is the
   last-resort lookup by (lower-case) dialog-title prefix. Pure data. */

export const HELP = {
  /* ---------------- File */
  "file-new": { etabs: "File › New Model", text: "Start a new model from a template (2D frame, steel moment frame, flat plate, braced frame, shear-wall core) with your own bays, spacing, stories and story height. Unsaved edits to the current model are discarded." },
  "file-gallery": { etabs: "File › New Model › Use Built-in Settings", text: "Pick one of the classic preset buildings (quick defaults, office, mid-rise, tall core, portal). Each card generates a complete model with gravity and seismic patterns." },
  "file-import": { etabs: "File › Import › (.e2k / DXF / IFC)", text: "Import geometry from an ETABS .e2k text file, a DXF floor plan or an IFC model. Imported members are snapped to stories; review the preview before importing." },
  "file-open": { etabs: "File › Open", text: "Open a model saved on the server. Opening replaces the working model; unsaved edits are lost after confirmation." },
  "file-save": { etabs: "File › Save", text: "Save the working model under its current file name. The first save asks for a name (Save As)." },
  "file-saveas": { etabs: "File › Save As", text: "Save the working model under a new name (letters, digits, space, _ and -)." },
  "file-report": { etabs: "File › Create Report", text: "Open a print-friendly report of the model and the last analysis (story results, modal, reactions, design). Available after a successful run." },

  /* ---------------- Define */
  "def-materials": { etabs: "Define › Material Properties", text: "Materials, frame sections and shell sections share the Section Manager. Materials hold E, ν, unit weight, strengths and an optional nonlinear stress–strain curve." },
  "def-frame": { etabs: "Define › Section Properties › Frame Sections", text: "Rectangular, library (AISC W) and Section Designer frame sections, with property modifiers (A, I33, I22, J, mass, weight) and shear-deformation options." },
  "def-shell": { etabs: "Define › Section Properties › Slab / Wall Sections", text: "Shell sections: thickness, material, membrane/bending modifiers and optional layered definitions used by walls and slabs." },
  "def-designer": { etabs: "Define › Section Properties › Frame Sections › Section Designer", text: "Draw arbitrary polygon sections with rebar; SkyFrame computes A, I, J and fiber PMM interaction surfaces used by fiber hinges." },
  "def-grid": { etabs: "Edit › Edit Stories and Grid Systems", text: "Edit grid lines (orthogonal and radial systems) and story heights/names. Changing story heights moves all objects on the story." },
  "def-stories": { etabs: "Edit › Edit Stories and Grid Systems › Story Data", text: "Story names, heights, master/similar stories and per-story diaphragm overrides." },
  "def-groups": { etabs: "Define › Group Definitions", text: "Named sets of objects used for staged construction, section cuts, selection and display. Assign objects with Assign › Assign Objects to Group." },
  "def-patterns": { etabs: "Define › Load Patterns", text: "Load patterns (Dead, Live, Seismic, Wind, Other…) with self-weight multipliers and auto-lateral generators." },
  "def-loadcases": { etabs: "Define › Load Cases", text: "Every analysis case in one list: linear static, modal, response spectrum, time history, nonlinear static, buckling, staged construction, steady state and PSD. Add, copy, modify or delete cases here." },
  "def-cases": { etabs: "Define › Load Cases › Linear Static", text: "Linear static cases: a weighted sum of load patterns, with optional P-Delta from a gravity case." },
  "def-rs": { etabs: "Define › Load Cases › Response Spectrum", text: "Response-spectrum cases: spectrum function per direction, modal combination (CQC/SRSS), directional combination and damping." },
  "def-th": { etabs: "Define › Load Cases › Time History", text: "Linear/nonlinear modal (FNA) or direct-integration time-history cases driven by TH functions." },
  "def-pushover": { etabs: "Define › Load Cases › Nonlinear Static (Pushover)", text: "Pushover cases: lateral load distribution, control node, target displacement and hinge assignments." },
  "def-nls": { etabs: "Define › Load Cases › Nonlinear Static", text: "Nonlinear static case data: initial conditions (chain from another case), loads applied, load application control (full load / displacement) and results saved (final state or multiple states)." },
  "def-modal": { etabs: "Define › Load Cases › Modal", text: "Modal case: eigen or Ritz vectors, number of modes and the stiffness to use (unstressed or from the end of a nonlinear case)." },
  "def-buckling": { etabs: "Define › Load Cases › Buckling", text: "Linear buckling cases: number of buckling modes and the load pattern(s) whose stresses drive the geometric stiffness." },
  "def-staged": { etabs: "Define › Load Cases › Nonlinear Staged Construction", text: "Staged construction: ordered stages that add/remove groups and apply loads, optionally with time-dependent effects." },
  "def-ss": { etabs: "Define › Load Cases › Steady State", text: "Steady-state (harmonic) case: loads × frequency function, frequency range, number of steps and damping." },
  "def-psd": { etabs: "Define › Load Cases › Power Spectral Density", text: "Power-spectral-density case: random loading defined by a PSD function over a frequency range." },
  "def-freqfn": { etabs: "Define › Functions › Power Spectral Density / Steady State", text: "Frequency-dependent functions (value vs. frequency) referenced by steady-state and PSD cases." },
  "def-combos": { etabs: "Define › Load Combinations", text: "Load combinations (linear add, envelope, absolute add, SRSS, range) with scale factors; add default design combinations from a code." },
  "def-functions": { etabs: "Define › Functions › Response Spectrum / Time History", text: "Response-spectrum functions (code or user period-vs-acceleration) and time-history functions (from file or periodic)." },
  "def-cuts": { etabs: "Define › Section Cuts", text: "Section cuts integrate element forces across a plane or group to report story or wall resultants." },
  "def-mass": { etabs: "Define › Mass Source", text: "Where the structure's mass comes from: element self mass, additional mass and/or specified load patterns with multipliers; lateral-only and story-lumping options." },
  "def-pdelta": { etabs: "Define › P-Delta Options", text: "Model-wide P-Delta: none, non-iterative based on mass, or iterative based on a load combination with tolerance." },
  "def-diaphragms": { etabs: "Define › Diaphragms", text: "Named diaphragms (rigid or semi-rigid). Assign them to joints or shells with Assign › Joint/Shell › Diaphragm." },
  "def-b9-springprops": { etabs: "Define › Spring Properties › Point Springs", text: "Named point-spring properties: linear stiffness per DOF, multilinear force-deformation curves or compression/tension-only gaps." },
  "def-codetools": { etabs: "Define › Load Patterns › Auto Lateral Loads", text: "Code tools: generate ASCE 7 / NBCC / Eurocode equivalent-static seismic and wind patterns, design spectra and default combinations." },

  /* ---------------- Draw */
  "draw-select": { etabs: "Select › Pointer", text: "Click an object to select it, drag a box to window-select, Shift-click to add/remove. Edit the selection in the Properties panel." },
  "draw-column": { etabs: "Draw › Draw Beam/Column/Brace › Quick Draw Columns", text: "Click a grid intersection to place a column over the active story (or all stories)." },
  "draw-beam": { etabs: "Draw › Draw Beam/Column/Brace › Draw Beams", text: "Click the first point, then the second point. Drawing continues from the last point; Esc ends the chain." },
  "draw-brace": { etabs: "Draw › Draw Beam/Column/Brace › Draw Braces", text: "Click the bottom point then the top point (plan: from story bottom to story top). Choose X / chevron options in the Draw panel." },
  "draw-wall": { etabs: "Draw › Draw Walls", text: "Click two plan points to place a wall spanning the story height." },
  "draw-slab": { etabs: "Draw › Draw Floor/Wall › Draw Rectangular Floor", text: "Click two opposite corners to place a rectangular slab at the story level." },
  "draw-polyfloor": { etabs: "Draw › Draw Floor/Wall › Draw Floor/Wall (Plan)", text: "Click polygon vertices; Enter or double-click closes the floor, Esc cancels." },
  "draw-link": { etabs: "Draw › Draw Links", text: "Click two points to place a two-joint link (gap, hook, damper, isolator, multilinear)." },
  "draw-spring": { etabs: "Assign › Joint › Springs", text: "Click a joint to add a point spring support." },

  /* ---------------- Assign */
  "asn-fsec": { etabs: "Assign › Frame › Section Property", text: "Select frames, then pick the section in the Properties panel." },
  "asn-frel": { etabs: "Assign › Frame › Releases/Partial Fixity", text: "Release moments/torsion/axial at frame ends (e.g. pinned beams). Select frames first." },
  "asn-finsert": { etabs: "Assign › Frame › Insertion Point", text: "Cardinal point and joint offsets that move the frame's analytical axis off the joint-to-joint line. Select frame members first." },
  "asn-fendoff": { etabs: "Assign › Frame › End Length Offsets", text: "Automatic (from connectivity) or user-defined rigid end zones with a rigid-zone factor. Select frame members first." },
  "asn-b9-automesh": { etabs: "Assign › Frame › Frame Auto Mesh Options", text: "Split frames internally at intermediate joints and intersections, or into a maximum segment length, for analysis." },
  "asn-b9-stations": { etabs: "Assign › Frame › Output Stations", text: "Number or spacing of stations along a frame where forces are reported." },
  "asn-b9-jsprings": { etabs: "Assign › Joint › Springs", text: "Assign a named point-spring property (with angle) to the selected joints." },
  "asn-b9-linkprops": { etabs: "Assign › Link › Link Properties", text: "Link hysteresis type and parameters (gap, hook, multilinear elastic/plastic, damper, rubber/friction isolator) for the selected links." },
  "asn-jforce": { etabs: "Assign › Joint Loads › Force", text: "Point forces and moments on the selected joints for a load pattern (add / replace / delete)." },
  "asn-jground": { etabs: "Assign › Joint Loads › Ground Displacement", text: "Imposed support displacements/rotations on the selected restrained joints." },
  "asn-fconc": { etabs: "Assign › Frame Loads › Point", text: "Concentrated forces or moments at relative or absolute distances along the selected frames." },
  "asn-suniform": { etabs: "Assign › Shell Loads › Uniform", text: "Uniform surface load on the selected shells: direction (gravity, local, global), projected option and joint-pattern scaling." },
  "asn-jdiaph": { etabs: "Assign › Joint › Diaphragms", text: "Constrain the selected joints to a named diaphragm (or disconnect them)." },
  "asn-sdiaph": { etabs: "Assign › Shell › Diaphragms", text: "Assign a named diaphragm to the selected floors." },
  "asn-jmass": { etabs: "Assign › Joint › Additional Mass", text: "Lumped translational/rotational mass on the selected joints." },
  "asn-fmass": { etabs: "Assign › Frame › Additional Mass", text: "Additional mass per unit length on the selected frames." },
  "asn-smass": { etabs: "Assign › Shell › Additional Mass", text: "Additional mass per unit area on the selected shells." },
  "asn-group": { etabs: "Assign › Assign Objects to Group", text: "Add the current selection to (or remove it from) a named group." },

  /* ---------------- Select */
  "sel-group": { etabs: "Select › Select › Groups", text: "Select every object that belongs to the chosen group(s)." },
  "sel-clear": { etabs: "Select › Clear Selection", text: "Deselect everything." },

  /* ---------------- Analyze */
  "an-cases-run": { etabs: "Analyze › Set Load Cases to Run", text: "Choose which cases run (Run / Do not Run), see the status of the last run and run now." },
  "an-dof": { etabs: "Analyze › Set Active Degrees of Freedom", text: "Restrict the analysis to a plane (XZ, YZ, XY) or full 3D. Inactive DOFs are restrained everywhere." },
  "an-run": { etabs: "Analyze › Run Analysis (F5)", text: "Run all cases set to Run, combinations and modal analysis on the backend (OpenSees). Unsaved edits are sent to the backend first." },
  "an-check": { etabs: "Analyze › Check Model", text: "Check joints, frames, shells and loads for overlaps, zero-length objects, unsupported nodes and missing assignments, with a configurable tolerance." },
  "an-stability": { etabs: "Analyze › Check Stability", text: "Detect mechanisms / singular stiffness: unrestrained DOFs, floating stories and zero-stiffness joints, before you run." },
  "an-log": { etabs: "Analyze › Last Analysis Run Log", text: "Warnings and diagnostics of the last run: P-Delta iterations, skipped combinations, convergence notes." },
  "an-opts": { etabs: "Analyze › Analysis Options / Advanced SAPFire Options", text: "P-Delta, modal count and damping are set per case in the Load Cases editor." },

  /* ---------------- Display */
  "dis-view3d": { etabs: "View › Set 3D View", text: "The 3D model view; orbit with drag, pan with Shift-drag, zoom with the wheel." },
  "dis-deformed": { etabs: "Display › Deformed Shape (F6)", text: "Show the deformed shape for the chosen case/combination (available after a run)." },
  "dis-mode": { etabs: "Display › Deformed Shape › Mode Shape", text: "Animate a mode shape from the modal case." },
  "dis-contours": { etabs: "Display › Force/Stress Diagrams › Shell Stresses/Forces", text: "Shell force contours (F11, M11…) on walls and slabs." },
  "dis-tables": { etabs: "Display › Show Tables (Ctrl+T)", text: "Tabular results: joint displacements, reactions, frame forces, story drifts, modal participation and more, with filtering and CSV export." },
  "dis-group": { etabs: "Display › Show Group", text: "Highlight the members of a group in the views." },
  "dis-stages": { etabs: "Display › Show Tables › Staged Construction", text: "Per-stage results of a staged-construction case." },
  "dis-story": { etabs: "Display › Story Response Plots", text: "Story drifts, displacements and shears per case." },
  "dis-modal": { etabs: "Display › Show Tables › Modal Information", text: "Periods, frequencies and mass-participation ratios." },
  "dis-reactions": { etabs: "Display › Show Tables › Joint Reactions", text: "Support reactions per case and combination." },
  "dis-forces": { etabs: "Display › Force/Stress Diagrams › Frame Forces", text: "Frame axial, shear and moment results; click a member for its diagrams." },
  "dis-nls": { etabs: "Display › Show Static Pushover Curve", text: "Load-step history and hinge states of nonlinear static cases." },

  /* ---------------- Design */
  "des-steel": { etabs: "Design › Steel Frame Design", text: "AISC 360 steel frame design: demand/capacity ratios per member and combination." },
  "des-concrete": { etabs: "Design › Concrete Frame Design", text: "ACI 318 concrete frame design: required longitudinal and shear reinforcement." },
  "des-wall": { etabs: "Design › Shear Wall Design", text: "Wall pier design from pier forces (P-M interaction and shear)." },

  /* ---------------- Options */
  "opt-units": { etabs: "Options › Units (status-bar units selector)", text: "Display units for every input, table, diagram and report (kN-m, kip-ft, kip-in, N-mm, tonf-m…). The model is always stored in SI." },
  "opt-snap": { etabs: "Draw › Snap Options", text: "Snap drawing clicks to grid intersections and existing joints." },
  "opt-check-before-run": { etabs: "Analyze › Check Model (before run)", text: "Automatically run Check Model before each analysis and stop on errors." },
  "opt-b9-automesh": { etabs: "Options › Preferences › Auto Mesh (frames)", text: "Model-wide default frame auto-mesh options applied to new frames." },

  /* ---------------- UX additions */
  "ux-palette": { etabs: "Help › Search for Command", text: "Type to fuzzy-search every menu command; Enter runs the highlighted one. Recent commands are listed first." },
  "ux-shortcuts": { etabs: "Help › Keyboard Shortcuts", text: "Every keyboard shortcut in one list." },
};

/** dialog backdrop id → HELP key */
export const DIALOG_HELP = {
  galleryModal: "file-gallery", importModal: "file-import", openModal: "file-open", saveAsModal: "file-saveas",
  sectionModal: "def-materials", designerModal: "def-designer", gridModal: "def-grid",
  grpDefineDlg: "def-groups", loadCasesModal: "def-loadcases", nlsCaseModal: "def-nls", modalCaseModal: "def-modal",
  freqFnModal: "def-freqfn", cxCombosDlg: "def-combos", massModal: "def-mass", cxPdDlg: "def-pdelta",
  dpDefModal: "def-diaphragms", spPropsDlg: "def-b9-springprops", fmAutoMeshDlg: "asn-b9-automesh",
  spJointDlg: "asn-b9-jsprings", jointLoadsModal: "asn-jforce", frameConcModal: "asn-fconc",
  shellUniformModal: "asn-suniform", amModal: "asn-jmass", casesRunModal: "an-cases-run", dofModal: "an-dof",
  ckOptionsModal: "an-check", ckStabModal: "an-stability", cxLogDlg: "an-log", showTablesModal: "dis-tables",
  nlsResModal: "dis-nls", unitsModal: "opt-units", uxStartModal: "file-new",
};

/** lower-case title prefix → HELP key (fallback for dialogs without a mapped id) */
export const TITLE_HELP = [
  ["load case data — steady", "def-ss"], ["load case data — power", "def-psd"],
  ["load case data — linear static", "def-cases"], ["load case data — response", "def-rs"],
  ["load case data — time", "def-th"], ["load case data — buckling", "def-buckling"],
  ["load case data — staged", "def-staged"], ["load case data — nonlinear", "def-nls"],
  ["load case data — modal", "def-modal"], ["load case data", "def-loadcases"],
  ["assign joint diaphragm", "asn-jdiaph"], ["assign shell diaphragm", "asn-sdiaph"],
  ["assign joint additional", "asn-jmass"], ["assign frame additional", "asn-fmass"], ["assign shell additional", "asn-smass"],
  ["insertion point", "asn-finsert"], ["end length", "asn-fendoff"], ["output station", "asn-b9-stations"],
  ["link propert", "asn-b9-linkprops"], ["select by group", "sel-group"], ["assign objects to group", "asn-group"],
  ["show group", "dis-group"], ["load combination", "def-combos"], ["frame auto mesh", "asn-b9-automesh"],
  ["check model", "an-check"], ["units", "opt-units"], ["mass source", "def-mass"],
];

/** Help entry for a dialog (by backdrop id, then title), or null. */
export function helpForDialog(id, title) {
  const k = DIALOG_HELP[id];
  if (k && HELP[k]) return { key: k, ...HELP[k] };
  const t = String(title || "").toLowerCase().trim();
  for (const [p, key] of TITLE_HELP) if (t.startsWith(p) && HELP[key]) return { key, ...HELP[key] };
  return null;
}
