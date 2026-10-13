/* SkyFrame — beyond-ETABS tools: one entry point for app.js.

     js/mhistory.js   Tools › Model History…     tools-mh-history
     js/explain.js    Tools › Explain Result…    tools-lp-explain   (needs results)
     js/mock_mhistory.js, js/mock_explain.js     ?mock=1 stand-ins

   Backend: core/model_diff.py, core/model_history.py, core/load_path.py,
   api/model_history_api.py, api/load_path_api.py (CONTRACT "Model history
   and visual diff", "Load-path explainer"). */

import { initMHistory as bxInitMHistory } from "./mhistory.js";
import { initExplain as bxInitExplain } from "./explain.js";

export function initBeyondTools(sky) {
  for (const [name, fn] of [["model history", bxInitMHistory], ["explain", bxInitExplain]]) {
    try { fn(sky); } catch (err) { console.error(`beyond tools: ${name} init failed`, err); }
  }
  const add = (path, item) => { try { sky.menu && sky.menu.add(path, item); } catch (err) { console.error("beyond tools menu", err); } };
  add("Tools", { label: "Model History…", act: "tools-mh-history", hint: "checkpoints · visual diff · restore",
    fn: () => sky.mhistory.open() });
  add("Tools", { label: "Explain Result…", act: "tools-lp-explain", hint: "load path: patterns · combos · modes",
    needs: "results", fn: () => sky.explain.open() });
}
