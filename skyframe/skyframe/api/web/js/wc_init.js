/* SkyFrame Wave C — interface parity pass (one import + one init line in
   js/app.js, before the UX pass so the palette / tooltips see the menus).

     js/wc_ggrid.js      general (line) grids: Primary Grid System…,
                         elevations along general lines, General Grid Line
                         tool in elevation and 3D views
     js/wc_explorer.js   Model Explorer: definition / object child nodes and
                         a right-click context menu per node type
     js/wc_shells.js     vertex reshaper in elevation and 3D (on the shell's
                         plane); shells with openings (js/wc_shellholes.js)
     js/wc_snap.js       object snaps in elevation and 3D + extension and
                         parallel snaps (js/wc_snapgeom.js)
     js/wc_units.js      Options › Units: consistent set + per-quantity units
                         and decimals (js/units.js, js/wc_unitcat.js)
     js/wc_autosave.js   periodic auto-save + recovery prompt on reopen
     js/wc_lock.js       model lock after a run (Analyze › Lock / Unlock)
   ?mock=1: js/mock_wc.js. Styles: wc.css.
   CONTRACT "Wave C interface: general grids, explorer, shells with
   openings, snapping, units, auto-save, model lock". */

import { initWcGrid as wciGrid } from "./wc_ggrid.js";
import { initWcExplorer as wciExplorer } from "./wc_explorer.js";
import { initWcShells as wciShells } from "./wc_shells.js";
import { initWcSnap as wciSnap } from "./wc_snap.js";
import { initWcUnits as wciUnits } from "./wc_units.js";
import { initWcAutosave as wciAutosave } from "./wc_autosave.js";
import { initWcLock as wciLock } from "./wc_lock.js";

export function initWc(sky) {
  if (!sky || !sky.store) return;
  if (!document.querySelector("link[data-wc-css]")) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/static/wc.css"; l.dataset.wcCss = "1";
    document.head.appendChild(l);
  }
  sky.wc = sky.wc || {};
  for (const [name, fn] of [["grid", wciGrid], ["snap", wciSnap], ["shells", wciShells], ["explorer", wciExplorer],
    ["units", wciUnits], ["autosave", wciAutosave], ["lock", wciLock]]) {
    try { fn(sky); } catch (e) { console.error(`Wave C ${name} init failed`, e); }
  }
}
