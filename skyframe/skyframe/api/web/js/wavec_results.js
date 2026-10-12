/* SkyFrame — Wave C results: one entry point for app.js.

     js/wc_diagrams.js  Display › Force Diagrams › Pier / Spandrel / Link
                        Diagrams (3D)…                       dis-wc-diag3d
     js/wc_video.js     Display › Export Animation Video…    dis-wc-video
     js/wc_compare.js   Tools › Compare Results…             tools-wc-compare
     js/wc_thout.js     Load Case Data — Time History › Output group
     js/mock_wavec.js   ?mock=1 stand-ins (cut forces for RS / TH / max-min
                        combos, TH shell forces / frames, results diff)

   Backend: engine/cut_results.py, core/th_output.py, core/th_shells.py,
   core/results_diff.py (CONTRACT "Cut, pier and spandrel forces for every
   case type, TH shell results and Compare Results"). */

import { initWcDiagrams as wcInitDiagrams } from "./wc_diagrams.js";
import { initWcVideo as wcInitVideo } from "./wc_video.js";
import { initWcCompare as wcInitCompare } from "./wc_compare.js";
import { initWcThOut as wcInitThOut } from "./wc_thout.js";

export function initWaveCResults(sky) {
  for (const [name, fn] of [["diagrams", wcInitDiagrams], ["video", wcInitVideo], ["compare", wcInitCompare], ["TH output", wcInitThOut]]) {
    try { fn(sky); } catch (err) { console.error(`Wave C ${name} init failed`, err); }
  }
  const add = (path, item) => { try { sky.menu && sky.menu.add(path, item); } catch (err) { console.error("Wave C menu", err); } };
  add("Display/Force Diagrams", { label: "Pier / Spandrel / Link Diagrams (3D)…", act: "dis-wc-diag3d", hint: "piers · spandrels · links",
    needs: "results", before: "dis-forces", fn: () => sky.wcDiagrams.open() });
  add("Display", { label: "Export Animation Video…", act: "dis-wc-video", hint: "deformed · modes · TH → WebM",
    needs: "results", before: "Force Diagrams", fn: () => sky.wcVideo.open() });
  add("Tools", { label: "Compare Results…", act: "tools-wc-compare", hint: "two runs or result files",
    fn: () => sky.wcCompare.open() });
}
