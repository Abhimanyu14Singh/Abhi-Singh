/* SkyFrame W8 — installer (one import + one init line in js/app.js).
   ETABS-parity UI for engine features + modelling productivity:
     js/w8_snap.js     object snaps (Draw › Snap Options…, status chips)
     js/w8_select.js   intersecting line, window/crossing box, select by attribute
     js/w8_stories.js  master / similar stories + One / Similar / All scope
     js/w8_curved.js   Draw › Curved Beam, Assign › Frame › Curved Geometry
     js/w8_shell.js    shell type, one-way distribution, auto mesh n1×n2, load sets
   ?mock=1: js/mock_w8.js (validation + DB-edit tables). */

import { initW8Snap as w8iSnap } from "./w8_snap.js";
import { initW8Select as w8iSelect } from "./w8_select.js";
import { initW8Stories as w8iStories } from "./w8_stories.js";
import { initW8Curved as w8iCurved } from "./w8_curved.js";
import { initW8Shell as w8iShell } from "./w8_shell.js";

export function initW8(sky) {
  if (!sky || !sky.store) return;
  if (!document.querySelector("link[data-w8css]")) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/static/w8.css"; l.dataset.w8css = "1";
    document.head.appendChild(l);
  }
  for (const [name, fn] of [["snap", w8iSnap], ["stories", w8iStories], ["curved", w8iCurved], ["select", w8iSelect], ["shell", w8iShell]]) {
    try { fn(sky); } catch (e) { console.error(`W8 ${name} init failed`, e); }
  }
}
