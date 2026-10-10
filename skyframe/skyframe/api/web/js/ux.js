/* SkyFrame UX pass — entry point (one hook line in app.js).
   Installs, additively and without removing any feature:
     ux_dialogs.js  shared dialog chrome (drag / resize / remember / "?" help /
                    Enter = OK, Esc = Cancel) applied to every existing dialog
     ux_keys.js     shortcut registry + Help › Keyboard Shortcuts (F1 / ?)
     ux_cmdk.js     command palette (Ctrl+K / Ctrl+Shift+P)
     ux_qat.js      quick-access toolbar under the menu bar
     ux_status.js   status-bar tool hints · selection count · last run; tooltips
     ux_start.js    start screen / templates (File › New, empty model)
     ux_menus.js    menu ownership (tabs), ETABS labels, Analysis Options hub,
                    keyboard Model Explorer tree                  (pass 2)
     ux_empty.js    empty states with Run / Add… calls to action  (pass 2)
     ux_errors.js   plain-language backend errors + field highlight (pass 2)
     ux_quick.js    sidebar quick-model form mirrors the model    (pass 2)
     ux_a11y.js     aria labels, tablist, live regions            (pass 2)
     ux_kbddraw.js  keyboard cursor: draw / select in the Plan view (pass 2)
   plus "Run saves first": with unsaved edits the live backend would otherwise
   analyse the last POSTed model, so __sky.beforeRun now saves first.
   Disable everything with ?ux=off (used by the before/after audit). */

import { ensureCss as uxEnsureCss } from "./ux_common.js";
import { installDialogs as uxInstallDialogs } from "./ux_dialogs.js";
import { installKeys as uxInstallKeys } from "./ux_keys.js";
import { installPalette as uxInstallPalette } from "./ux_cmdk.js";
import { installToolbar as uxInstallToolbar } from "./ux_qat.js";
import { installStatus as uxInstallStatus } from "./ux_status.js";
import { installStart as uxInstallStart } from "./ux_start.js";
// UX pass 2 — menu ownership / ETABS names / explorer tree, empty states,
// plain-language errors, quick-model form, accessibility sweep
import { installMenus as uxInstallMenus } from "./ux_menus.js";
import { installEmpty as uxInstallEmpty } from "./ux_empty.js";
import { installErrors as uxInstallErrors } from "./ux_errors.js";
import { installQuick as uxInstallQuick } from "./ux_quick.js";
import { installA11y as uxInstallA11y } from "./ux_a11y.js";
import { installKbdDraw as uxInstallKbdDraw } from "./ux_kbddraw.js";

export function initUx(sky) {
  let off = false;
  try { off = new URLSearchParams(location.search).get("ux") === "off"; } catch { /* ignore */ }
  if (off || !sky) return;
  const t0 = performance.now();
  sky.ux = sky.ux || {};
  uxEnsureCss();
  sky.ux.stepMs = {};
  const step = (name, fn) => {
    const s0 = performance.now();
    try { fn(sky); } catch (e) { console.warn("ux " + name + " init failed", e); }
    sky.ux.stepMs[name] = Math.round((performance.now() - s0) * 10) / 10;
  };
  step("dialogs", uxInstallDialogs);
  step("keys", uxInstallKeys);
  step("palette", uxInstallPalette);
  step("toolbar", uxInstallToolbar);
  step("status", uxInstallStatus);
  step("start", uxInstallStart);
  step("run-saves", installRunSaves);
  step("menus", uxInstallMenus);
  step("empty", uxInstallEmpty);
  step("errors", uxInstallErrors);
  step("quick", uxInstallQuick);
  step("a11y", uxInstallA11y);
  step("kbd-draw", uxInstallKbdDraw);
  // narrow windows (< 1200 px): start with the Model Explorer collapsed so the
  // plan / 3D canvas is usable (the toolbar, palette and Ctrl+K cover its links)
  step("narrow", s => {
    const ws = document.querySelector(".workspace");
    if (window.innerWidth < 1200 && ws && !ws.classList.contains("explorer-collapsed") && s.etabs) s.etabs.toggleExplorer();
  });
  document.documentElement.classList.add("ux-on");
  sky.ux.initMs = Math.round((performance.now() - t0) * 10) / 10;
}

/* Run with unsaved edits (live backend): POST the working model first so the
   solve sees what is on screen; a failed save cancels the run. Chains to the
   existing hook (Check Model before run). */
function installRunSaves(sky) {
  const S = sky.store;
  const prev = sky.beforeRun;
  sky.beforeRun = async () => {
    if (S.dirty && !S.mock && typeof sky.saveModel === "function") {
      await sky.saveModel();
      if (S.dirty) return false;          // save failed → toast already shown, do not run stale
    }
    return typeof prev === "function" ? prev() : true;
  };
}
