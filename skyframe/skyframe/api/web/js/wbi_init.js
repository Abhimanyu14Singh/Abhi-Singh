/* SkyFrame Wave B — interface & modelling productivity (one import + one
   init line in js/app.js, before the UX pass so the palette sees the menus).

     js/wb_templates.js  File › New Model from Template… (waffle, ribbed,
                         two-way with beams, staggered truss, flat slab with
                         drop panels) + start-screen cards
     js/wb_grid.js       general (arbitrary line) grids: editor, pick in plan
     js/wb_views.js      View › Named Views ▸ · View › Windows ▸ (2–4 panes)
     js/wb_shells.js     Edit › Edit Shells ▸ divide / merge / chop /
                         expand-shrink / vertex reshaper
     js/wb_draw.js       Draw › Quick Draw ▸, frames in the 3D view,
                         dimension lines, reference points / planes, ramps,
                         wall stacks, Edit › Auto Relabel
     js/wb_report.js     report templates, .rtf export, Show Tables sets
   ?mock=1: js/mock_wb.js + js/wb_shellgeom.js (shared geometry). */

import { initWbTemplates as wbiTemplates } from "./wb_templates.js";
import { initWbGrid as wbiGrid } from "./wb_grid.js";
import { initWbViews as wbiViews } from "./wb_views.js";
import { initWbShells as wbiShells } from "./wb_shells.js";
import { initWbDraw as wbiDraw } from "./wb_draw.js";
import { initWbReport as wbiReport } from "./wb_report.js";

export function initWb(sky) {
  if (!sky || !sky.store) return;
  if (!document.querySelector("link[data-wb-css]")) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/static/wb.css"; l.dataset.wbCss = "1";
    document.head.appendChild(l);
  }
  sky.wb = sky.wb || {};
  for (const [name, fn] of [["templates", wbiTemplates], ["grid", wbiGrid], ["views", wbiViews], ["shells", wbiShells],
    ["draw", wbiDraw], ["report", wbiReport]]) {
    try { fn(sky); } catch (e) { console.error(`Wave B ${name} init failed`, e); }
  }
}
