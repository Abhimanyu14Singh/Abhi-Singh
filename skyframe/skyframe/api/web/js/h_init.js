/* SkyFrame Wave H — windows, pop-outs and side-by-side compare (entry).

   app.js: one import + one init line (after Wave G). Installs
     js/h_dock.js     docking layout (splits, tabs, any number of windows,
                      per-window display options and results state)
     js/h_sync.js     pop-out OS windows + main ↔ pop-out sync
     js/h_compare.js  View › Windows › Compare Side by Side…
   and the View › Windows commands (every panel stays ≤ 14 entries):

     New Window                    vx-h-new        same view as the active window, to its right
     Arrange Windows               vx-h-arrange    ETABS: ≤ 10 tiles, extras as tabs
     Pop Out Active Window         vx-h-popout     own OS window (desktop: pywebview)
     Dock All Pop-outs             vx-h-dockall
     Link 3D Window Cameras        vx-h-linkcam    orbit / pan / zoom together (check)
     Apply Active Window to All    vx-h-applyall   results case / step / display (ETABS Apply to All Windows)
     Compare Side by Side…         vx-h-compare    two linked windows + difference (beyond ETABS)

   The five View › Windows presets (1 / 2 / 2 / 3 / 4) keep their act ids and
   build docking layouts of the old tile arrangement. */

import { initHDock as hiInitDock } from "./h_dock.js";
import { initHSync as hiInitSync } from "./h_sync.js";
import { initHCompare as hiInitCompare } from "./h_compare.js";
import { H_PRESETS as HI_PRESETS } from "./h_dock_core.js";

function hiCss() {
  if (document.querySelector("link[data-h-css]")) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/h.css"; l.setAttribute("data-h-css", "1");
  document.head.appendChild(l);
}

export function initH(sky) {
  hiCss();
  const isPop = (() => { try { return /^w\d{1,4}$/.test(new URLSearchParams(location.search).get("hpop") || ""); } catch { return false; } })();
  const dock = hiInitDock(sky, { popout: isPop || null });
  const sync = hiInitSync(sky, dock);
  const cmp = hiInitCompare(sky, dock);
  const toast = (t, m, k = "info", ms = 3500) => sky.toast && sky.toast(t, m, k, ms);

  if (sky.menu) {
    // presets: keep the act ids, radio follows the docking layout
    for (const k of Object.keys(HI_PRESETS)) {
      const it = sky.menu.item(`vx-wb-win-${k}`);
      if (it) { it.fn = () => dock.setPreset(k); it.radio = () => dock.layout() === k; }
    }
    const W = "View/Windows";
    sky.menu.add(W, { label: "New Window", act: "vx-h-new", hint: "same view as the active window · drag tabs to dock", before: "vx-f-props",
      fn: () => { const id = dock.add(); if (id) toast("New window", `Window ${id.slice(1)} added — drag its tab to dock it elsewhere, drag the splitters to resize.`, "info", 2500); } });
    sky.menu.add(W, { label: "Arrange Windows", act: "vx-h-arrange", hint: "up to 10 tiles, extras as tabs", before: "vx-f-props", fn: () => dock.arrange() });
    sky.menu.add(W, { label: "Pop Out Active Window", act: "vx-h-popout", hint: "own OS window · second monitor", before: "vx-f-props",
      fn: () => {
        if (!dock.L().root) dock.add({}, null, "right");
        const a = dock.active();
        if (a) sync.popout(a.id);
      } });
    sky.menu.add(W, { label: "Dock All Pop-outs", act: "vx-h-dockall", before: "vx-f-props",
      fn: () => { const n = Object.keys(sync.pops()).length; sync.dockAll(); if (!n) toast("Dock all", "No window is popped out."); } });
    sky.menu.add(W, { label: "Link 3D Window Cameras", act: "vx-h-linkcam", hint: "orbit / pan / zoom together", before: "vx-f-props",
      check: () => dock.allLinked(), fn: () => dock.linkAll3d() });
    sky.menu.add(W, { label: "Apply Active Window to All", act: "vx-h-applyall", hint: "results case · step · display", before: "vx-f-props",
      fn: () => { if (!dock.L().root) { toast("Apply to all windows", "Only one window is open."); return; } dock.applyToAll(); } });
    sky.menu.add(W, { label: "Compare Side by Side…", act: "vx-h-compare", hint: "two linked windows + difference", before: "vx-f-props", fn: () => cmp.open() });
  }

  // layout remembered for this project on this monitor setup
  const selftest = (() => { try { return new URLSearchParams(location.search).has("hselftest"); } catch { return false; } })();
  if (!isPop && !selftest) setTimeout(() => {
    try { if (!dock.L().root && !Object.keys(sync.pops()).length) dock.restoreRemembered(); } catch (e) { console.warn("window layout", e); }
  }, 0);
  sky.h = { dock, sync, compare: cmp };
  return sky.h;
}
