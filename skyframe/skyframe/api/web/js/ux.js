/* SkyFrame UX pass — entry point (one hook line in app.js).
   Installs, additively and without removing any feature:
     ux_dialogs.js  shared dialog chrome (drag / resize / remember / "?" help /
                    Enter = OK, Esc = Cancel) applied to every existing dialog
     ux_keys.js     shortcut registry + Help › Keyboard Shortcuts (F1 / ?)
     ux_cmdk.js     command palette (Ctrl+K / Ctrl+Shift+P)
     ux_qat.js      quick-access toolbar under the menu bar
     ux_status.js   status-bar tool hints · selection count · last run; tooltips
     ux_start.js    start screen / templates (File › New, empty model)
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

export function initUx(sky) {
  let off = false;
  try { off = new URLSearchParams(location.search).get("ux") === "off"; } catch { /* ignore */ }
  if (off || !sky) return;
  const t0 = performance.now();
  sky.ux = sky.ux || {};
  uxEnsureCss();
  const step = (name, fn) => { try { fn(sky); } catch (e) { console.warn("ux " + name + " init failed", e); } };
  step("dialogs", uxInstallDialogs);
  step("keys", uxInstallKeys);
  step("palette", uxInstallPalette);
  step("toolbar", uxInstallToolbar);
  step("status", uxInstallStatus);
  step("start", uxInstallStart);
  step("run-saves", installRunSaves);
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
