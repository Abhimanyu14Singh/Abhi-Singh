/* SkyFrame Wave F — select and assign in every view (one init line in
   js/app.js, after the menus and the standard dialogs).

     js/f_pickgeom.js   pure pick / scope math (node-tested, Python mirror)
     js/f_pick.js       ONE pick service (all object types, CPU index / GPU ids)
     js/gpu/f_gpupick.js  GPU ID buffer for joints / shells / links / springs / tendons
     js/f_select3d.js   ETABS mouse grammar + selection drawing in every 3D view
     js/f_select2d.js   plan / elevation: joints, filter, window / crossing, line
     js/f_props.js      dockable Properties panel (Model + Analyze) + joint section
     js/f_assign.js     remaining Assign items → dialogs; story scope in Assign dialogs
     js/f_ctxmenu.js    right-click menus by object type, Show results… hook

   Menus (≤ 14 entries per panel):
     Select › Select Using ▸  Intersecting Line (any view) · Window / Crossing
                              Box · Selection Filter… · 3D Drag Selects (Box)
     View › Windows ▸         Properties Panel (in the 3D view)
   __sky.f — test / integration API (additive); sky.f.onShowResults(obj) is the
   hook Wave G's results detail window plugs into (sky.results.openDetail). */

import { createPickService as fiCreatePick, FILTER_TYPES as FI_FILTER_TYPES, defaultFilter as fiDefaultFilter } from "./f_pick.js";
import { initFSelect3d as fiSelect3d } from "./f_select3d.js";
import { initFSelect2d as fiSelect2d } from "./f_select2d.js";
import { initFProps as fiProps } from "./f_props.js";
import { initFAssign as fiAssign } from "./f_assign.js";
import { initFCtxMenu as fiCtxMenu } from "./f_ctxmenu.js";
import * as FIPG from "./f_pickgeom.js";
import { el as fiEl, dkForm as fiForm, dkGroup as fiGroup, dkNote as fiNote, dkDialog as fiDialog, dkInfo as fiInfo } from "./dlgkit.js";

function fiCss() {
  if (document.querySelector("link[data-f-css]")) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/fsel.css"; l.dataset.fCss = "1";
  document.head.appendChild(l);
}

export function initF(sky) {
  if (!sky || !sky.store) return null;
  fiCss();
  const S = sky.store;
  const F = sky.f = sky.f || {};
  F.selectInPlace = true;                      // Select commands keep the current view (js/editmenu.js hook)
  F.PG = FIPG;
  F.pick = fiCreatePick(sky);
  F.boxMode = () => !!(sky.w8select && sky.w8select.boxMode && sky.w8select.boxMode());
  F.infoDialog = (title, rows) => fiDialog({ id: "fInfo", title, iconId: "exleaf-supports", size: "sm", noCancel: true, body: [fiInfo(rows)] });

  F.s3 = fiSelect3d(sky, F);
  F.s2 = fiSelect2d(sky, F);
  F.ctx = fiCtxMenu(sky, F);
  F.props = fiProps(sky, F);
  F.assign = fiAssign(sky, F);
  F.onSelection = () => { if (F.props) F.props.place(); };

  /* ---------------------------------------------------------------- selection filter */
  function openFilter() {
    const cur = F.pick.filter();
    const V = { ...cur };
    const F2 = fiForm();
    const checks = FI_FILTER_TYPES.map(([k, l]) => F2.check(V, k, { label: l, id: "fFilt_" + k }));
    const allBtn = fiEl("button", { type: "button", class: "btn btn-small", id: "fFiltAll", onclick: () => { for (const [k] of FI_FILTER_TYPES) V[k] = true; F2.refresh(); } }, ["All"]);
    const noneBtn = fiEl("button", { type: "button", class: "btn btn-small", id: "fFiltNone", onclick: () => { for (const [k] of FI_FILTER_TYPES) V[k] = false; F2.refresh(); } }, ["None"]);
    return fiDialog({
      id: "fFilter", title: "Selection Filter", iconId: "exleaf-groups", size: "sm", form: F2,
      intro: "Object types that clicks, boxes and the intersecting line can select, in every view. Hidden objects are never selected.",
      body: [fiGroup("Selectable objects", checks, { cols: 2 }), fiEl("div", { class: "dk-inline" }, [allBtn, noneBtn]),
        fiNote("A per-computer preference (not saved in the model). Select by property / group / story ignore the filter.")],
      onOk: () => { F.pick.setFilter(V); for (const v of F.s3.viewers()) { F.pick.invalidate(v); v._dirty = true; } },
    });
  }
  F.openFilter = openFilter;

  /* ---------------------------------------------------------------- menus */
  if (sky.menu && sky.menu.add) {
    sky.menu.add("Select/Select Using", { label: "Selection Filter…", act: "sel-f-filter", hint: "object types", fn: openFilter });
    sky.menu.add("Select/Select Using", { label: "3D Drag Selects (Box)", act: "sel-f-dragbox", hint: "off: drag orbits · Ctrl-drag boxes",
      check: () => F.s3.dragMode() === "select", fn: () => F.s3.setDragMode(F.s3.dragMode() === "select" ? "orbit" : "select") });
    sky.menu.add("View/Windows", { label: "Properties Panel", act: "vx-f-props", hint: "beside the 3D view", check: () => F.props.pref().on,
      fn: () => F.props.setOn(!F.props.pref().on) });
    // Intersecting Line in any view: 3D (main / pane) → screen line; elevation → (s, z) line; plan → W8 tool
    const it = sky.menu.item("sel-w8-line");
    if (it && typeof it.fn === "function") {
      const plan = it.fn;
      it.fn = () => {
        const v = F.s3.activeViewer();
        if (v) return F.s3.startLine(v);
        const W = sky.wb && sky.wb.windows;
        if (W && W.layout && W.layout() !== "1") {
          const p = W.panes().find(x => x.el && x.el.classList.contains("is-focus"));
          if (p && p.kind === "elev" && p.ed) return F.s2.startElevLine(p.ed);
        }
        if (S.mode === "model" && S.view === "elev") return F.s2.startElevLine(sky.elevEditor);
        return plan();
      };
    }
  }

  F.api = { filterTypes: FI_FILTER_TYPES.map(([k]) => k), defaultFilter: fiDefaultFilter };
  return F;
}
