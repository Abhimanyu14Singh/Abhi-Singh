/* SkyFrame — standard dialogs + guided workflow: installer and routing
   (one hook line in app.js, after the UX pass).

   Routes the most-used menu commands to the dlgkit dialogs (js/dlgstd_*.js)
   for every entry point — menu clicks, the command palette / toolbar /
   shortcuts (__sky.etabs.clickItem) and the Model Explorer — WITHOUT editing
   the menu definitions: act ids and labels stay exactly as js/etabs.js and
   js/ux_menus.js define them. The previous editors stay reachable from each
   dialog's "More…" footer buttons, and ?dlg=legacy turns the routing off.

   Also installs the Model Workflow checklist and the canvas context menus
   (js/workflow.js), and exposes everything on __sky.std / __sky.workflow /
   __sky.ctxMenu (additive). */

import * as SD from "./dlgstd_defs.js";
import * as SC from "./dlgstd_cases.js";
import * as SA from "./dlgstd_assign.js";
import { dkDraft as stdDraft, dkCommit as stdCommit, dkTop as stdTop, ensureCss as stdEnsureCss } from "./dlgkit.js";
import { installWorkflow as stdInstallWorkflow, installContextMenu as stdInstallContextMenu, workflowState as stdWorkflowState } from "./workflow.js";

/** Modal case on its own (Define › Modal Case…). */
function openModalStd(sky) {
  const D = stdDraft(sky);
  return SC.editModalCase(sky, D, { done: () => stdCommit(sky, D) });
}

export function initDlgStd(sky) {
  if (!sky || !sky.store) return;
  stdEnsureCss();
  let legacy = false;
  try { legacy = new URLSearchParams(location.search).get("dlg") === "legacy"; } catch { /* ignore */ }

  const std = {
    materials: o => SD.openMaterials(sky, o), frameSections: o => SD.openFrameSections(sky, o), shellSections: o => SD.openShellSections(sky, o),
    loadPatterns: o => SD.openLoadPatterns(sky, o), functions: o => SD.openFunctions(sky, o), gridStories: o => SD.openGridStories(sky, o),
    loadCases: o => SC.openLoadCases(sky, o), combinations: o => SC.openCombinations(sky, o), massSource: () => SC.openMassSource(sky),
    diaphragms: o => SC.openDiaphragmsStd(sky, o), analysisOptions: o => SC.openAnalysisOptions(sky, o), modalCase: () => openModalStd(sky),
    assignFrame: o => SA.openAssignFrame(sky, o), assignShell: () => SA.openAssignShell(sky), assignSupports: () => SA.openAssignSupports(sky),
    info: ref => SA.openObjectInfo(sky, ref), top: stdTop,
  };
  sky.std = std;

  /* act → dialog (only acts that exist in the menus are routed) */
  const ROUTES = {
    "def-materials": () => std.materials(), "def-frame": () => std.frameSections(), "def-shell": () => std.shellSections(),
    "def-patterns": () => std.loadPatterns(), "def-functions": () => std.functions(),
    "def-grid": () => std.gridStories({ tab: "grid" }), "def-stories": () => std.gridStories({ tab: "stories" }), "draw-grid": () => std.gridStories({ tab: "grid" }),
    "def-loadcases": () => std.loadCases(), "def-cases": () => std.loadCases({ addKind: "static" }), "def-rs": () => std.loadCases({ addKind: "rs" }),
    "def-th": () => std.loadCases({ addKind: "th" }), "def-buckling": () => std.loadCases({ addKind: "buckling" }), "def-modal": () => std.modalCase(),
    "def-combos": () => std.combinations(), "def-mass": () => std.massSource(), "def-diaphragms": () => std.diaphragms(),
    "an-opts": () => std.analysisOptions(), "an-dof": () => std.analysisOptions({ tab: "dof" }), "def-pdelta": () => std.analysisOptions({ tab: "pdelta" }),
    "asn-fsec": () => std.assignFrame({ tab: "section" }), "asn-frel": () => std.assignFrame({ tab: "releases" }),
    "asn-forient": () => std.assignFrame({ tab: "axes" }), "asn-foff": () => std.assignFrame({ tab: "offsets" }),
    "asn-faxial": () => std.assignFrame({ tab: "nonlinear" }), "asn-fhinge": () => std.assignFrame({ tab: "nonlinear" }),
    "asn-ssec": () => std.assignShell(), "asn-scp": () => std.assignShell(), "asn-support": () => std.assignSupports(),
  };
  const EXPLORER = {
    "Materials": "def-materials", "Frame Sections": "def-frame", "Shell Sections": "def-shell", "Load Patterns": "def-patterns",
    "Load Cases": "def-loadcases", "Combinations": "def-combos", "Functions": "def-functions", "Mass Source": "def-mass",
    "Stories": "def-stories", "Grid Systems": "def-grid", "Active Degrees of Freedom": "an-dof",
    "Frame Assignments": "asn-fsec", "Shell Assignments": "asn-ssec", "Supports": "asn-support",
  };
  const runRoute = act => {
    try { ROUTES[act](); } catch (e) { console.error("standard dialog failed", act, e); return false; }
    return true;
  };

  if (!legacy && sky.etabs) {
    /* programmatic entry points (palette, toolbar, shortcuts, workflow) */
    const origClick = sky.etabs.clickItem;
    sky.etabs.clickItem = act => {
      if (act === "help-workflow" && sky.workflow) { sky.etabs.closeMenus && sky.etabs.closeMenus(); sky.workflow.show(); return true; }
      if (ROUTES[act] && sky.etabs.hasItem(act)) { sky.etabs.closeMenus && sky.etabs.closeMenus(); return runRoute(act); }
      return origClick(act);
    };
    /* route at the menu item itself: every activation path calls item.fn */
    if (sky.menu && sky.menu.item)
      for (const act of Object.keys(ROUTES)) {
        const it = sky.menu.item(act);
        if (it && typeof it.fn === "function") { it.legacyFn = it.fn; it.fn = () => { runRoute(act); }; }
      }
    const origEx = sky.etabs.explorerClick;
    sky.etabs.explorerClick = label => (EXPLORER[label] && ROUTES[EXPLORER[label]]) ? runRoute(EXPLORER[label]) : origEx(label);
    /* DOM clicks — window capture runs before the menubar / explorer handlers */
    window.addEventListener("click", e => {
      const t = e.target && e.target.closest ? e.target : null;
      if (!t) return;
      // menu items are routed at the item level (below), so mouse, keyboard,
      // touch, palette and toolbar all open the same standard dialog
      const leaf = t.closest("#etabsExplorer .ex-leaf");
      if (leaf && !leaf.classList.contains("ex-case")) {
        const lbl = (leaf.querySelector(".ex-leaf-lbl") || leaf).textContent.trim();
        if (EXPLORER[lbl]) { e.stopPropagation(); e.preventDefault(); runRoute(EXPLORER[lbl]); }
      }
    }, true);
    /* keyboard activation of explorer leaves (Enter / Space) */
    window.addEventListener("keydown", e => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const leaf = e.target && e.target.closest && e.target.closest("#etabsExplorer .ex-leaf");
      if (!leaf || leaf.classList.contains("ex-case")) return;
      const lbl = (leaf.querySelector(".ex-leaf-lbl") || leaf).textContent.trim();
      if (EXPLORER[lbl]) { e.stopPropagation(); e.preventDefault(); runRoute(EXPLORER[lbl]); }
    }, true);
  }

  /* guided workflow + context menus */
  try { sky.workflow = stdInstallWorkflow(sky); } catch (e) { console.error("workflow init failed", e); }
  try { sky.ctxMenu = stdInstallContextMenu(sky); } catch (e) { console.error("context menu init failed", e); }
  sky.workflowState = () => (sky.workflow ? sky.workflow.state() : stdWorkflowState(sky));
  // Help › Model Workflow when the menu line is absent (merged menus)
  if (sky.etabs && !sky.etabs.hasItem("help-workflow")) {
    const help = [...document.querySelectorAll("#etabsMenubar .etabs-menu-wrap")].find(w => (w.querySelector(".etabs-menu-btn") || {}).dataset?.menu === "Help");
    const dd = help && help.querySelector(".etabs-menu");
    if (dd && !dd.querySelector('[data-act="help-workflow"]')) {
      const b = document.createElement("button");
      b.className = "etabs-menu-item"; b.setAttribute("role", "menuitem"); b.dataset.act = "help-workflow";
      b.innerHTML = '<span class="mi-check">✓</span><span class="mi-label">Model Workflow</span>';
      b.addEventListener("click", () => { sky.etabs.closeMenus && sky.etabs.closeMenus(); sky.workflow && sky.workflow.show(); });
      dd.appendChild(b);
    }
  }
}
