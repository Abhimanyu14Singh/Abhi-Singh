/* SkyFrame UX — status-bar additions + tooltips.
     · tool / mode hint ("Beam: click the first point…", "Polygon: click
       vertices, Enter closes…") that follows the active tool and the
       drawing state (first point picked or not)
     · selection count chip (click = clear selection)
     · last analysis: status · solve time · clock time
     · tooltips: every menu item gets its help text (js/helpdocs.js) or its
       label + hint; palette buttons / explorer leaves get richer titles.
   Updates are event-driven (pointer/key/model/results events, rAF-throttled). */

import { el as uxEl } from "./ux_common.js";
import { HELP as uxHELP } from "./helpdocs.js";

const TOOL_HINT = {
  select: ["Select", "click an object, drag a box to window-select, Shift-click adds/removes · Delete removes · Esc clears"],
  column: ["Column", "click a grid intersection to place a column on this story"],
  beam: ["Beam", "click the first point", "click the next point (chain continues) · Esc ends"],
  brace: ["Brace", "click the bottom point", "click the top point · Esc cancels"],
  wall: ["Wall", "click the first point of the wall", "click the second point · Esc cancels"],
  slab: ["Slab", "click the first corner", "click the opposite corner · Esc cancels"],
  link: ["Link", "click the first joint", "click the second joint · Esc cancels"],
  spring: ["Point spring", "click a joint to add a spring support"],
  linespring: ["Line spring", "click the first point", "click the second point · Esc cancels"],
  erase: ["Erase", "click an object to delete it (on a wall/slab: adds an opening)"],
  g2poly: ["Polygon", "click vertices · Enter or double-click closes · Backspace removes the last vertex · Esc cancels"],
};

export function installStatus(sky) {
  const S = sky.store;
  const status = document.getElementById("etabsStatus");
  if (!status) return;

  const hint = uxEl("span", { class: "sb-item ux-sb-hint", id: "uxSbHint", "aria-live": "polite" });
  const selChip = uxEl("button", { type: "button", class: "sb-chip ux-sb-sel hidden", id: "uxSbSel", title: "Selected objects — click to clear the selection (Esc)" });
  const runInfo = uxEl("span", { class: "sb-item ux-sb-run", id: "uxSbRun", title: "Last analysis" });
  selChip.addEventListener("click", () => { sky.handleSelect && sky.handleSelect([], false); later(); });
  // after the view segment, before the spacer: hint grows/shrinks
  const spacer = status.querySelector(".sb-spacer");
  if (spacer) { spacer.before(uxEl("span", { class: "sb-sepv" }), hint, selChip); spacer.classList.add("ux-sb-spacer-min"); }
  else status.append(hint, selChip);
  const runStatus = document.getElementById("sbStatusText");
  if (runStatus) runStatus.after(runInfo); else status.append(runInfo);

  let lastRun = null;   // {ok, ms, at}
  const runBtn = document.getElementById("runBtn");
  const pill = document.getElementById("statusPill");
  if (pill) new MutationObserver(() => {
    const c = pill.className;
    if (/is-solved/.test(c)) lastRun = { ok: true, ms: S.lastSolveMs, at: new Date() };
    else if (/is-error/.test(c) && runBtn && !runBtn.disabled) lastRun = { ok: false, ms: null, at: new Date() };
    later();
  }).observe(pill, { attributes: true, attributeFilter: ["class"] });

  /* dead-end toasts ("Select … first", "Run an analysis first") → an actionable
     hint in the status bar; in Model mode the Select tool is armed directly */
  let sticky = null;   // {text, until}
  const toasts = document.getElementById("toasts");
  if (toasts) new MutationObserver(recs => {
    for (const r of recs) for (const n of r.addedNodes) {
      if (n.nodeType !== 1) continue;
      const txt = n.textContent || "";
      if (/select\b.*\bfirst/i.test(txt) && !/case/i.test(txt)) {
        if (S.mode === "model" && S.tool !== "select" && sky.setTool) sky.setTool("select");
        sticky = { text: "Nothing selected — pick objects with the Select tool (V) in Plan/Elevation or click members in 3D, then run the command again", until: Date.now() + 8000 };
      } else if (/run (an )?analysis first|after a solve/i.test(txt)) {
        sticky = { text: "No results yet — press Run (F5); result displays and tables unlock after a solve", until: Date.now() + 8000 };
      } else continue;
      later();
      setTimeout(later, 8100);
    }
  }).observe(toasts, { childList: true });

  function hintText() {
    if (sticky && Date.now() < sticky.until) return sticky.text;
    sticky = null;
    const pd = sky.polyDraw;
    if (S.mode === "model") {
      const tool = S.tool;
      const h = TOOL_HINT[tool] || (pd && tool === "g2poly" ? TOOL_HINT.g2poly : null);
      const view = S.view === "elev" ? "Elevation" : "Plan";
      if (!h) return `${view} · ${tool || "select"}`;
      const ed = S.view === "elev" ? sky.elevEditor : sky.planEditor;
      const step = ed && ed.pending && h[2] ? h[2] : h[1];
      return `${view} · ${h[0]}: ${step}`;
    }
    if (S.mode === "loads") return "Loads · edit patterns, cases, combinations and functions — Save to apply · Run (F5)";
    if (!S.results) return "Analyze · press Run (F5) to solve · Ctrl+K searches every command";
    return "Results · pick a case, then a results tab (1–9) · F6 deformed shape · Alt+T tables";
  }
  const fmtClock = d => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  function sync() {
    const t = hintText();
    if (hint.textContent !== t) { hint.textContent = t; hint.title = t; }
    hint.classList.toggle("is-warn", !!sticky);
    const n = (S.selection || []).length;
    selChip.classList.toggle("hidden", !n);
    const st = `${n} selected`;
    if (selChip.textContent !== st) selChip.textContent = st;
    let r = "";
    if (lastRun) r = lastRun.ok ? `✓ ${lastRun.ms != null ? (lastRun.ms / 1000).toFixed(2) + " s" : "solved"}` : `✕ failed ${fmtClock(lastRun.at)}`;
    else if (S.results) r = "✓ results loaded";
    else r = S.dirty && !S.mock ? "not run · unsaved edits" : "not run";
    if (runInfo.textContent !== r) runInfo.textContent = r;
    runInfo.classList.toggle("is-ok", !!(lastRun && lastRun.ok) || (!lastRun && !!S.results));
    runInfo.classList.toggle("is-err", !!(lastRun && !lastRun.ok));
    runInfo.title = lastRun ? `Last analysis ${lastRun.ok ? "finished" : "failed"} at ${lastRun.at.toLocaleTimeString()}${lastRun.ms != null ? ` · solve ${(lastRun.ms / 1000).toFixed(2)} s` : ""}` : "No analysis run in this session";
    const fi = document.getElementById("footerInfo");   // hidden on narrower screens → keep it reachable
    if (fi && fi.textContent.trim()) runInfo.title += "\n" + fi.textContent.trim();
  }
  let raf = 0;
  const later = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; sync(); }); };
  for (const ev of ["pointerup", "keyup", "click"]) document.addEventListener(ev, later, true);
  for (const ev of ["sky:model-changed", "sky:results-changed", "sky:units-changed"]) document.addEventListener(ev, later);
  // programmatic mode / view / tool changes (menus, palette, scripts) — a few class attributes
  const modeObs = new MutationObserver(later);
  for (const id of ["drawMain", "loadsMain", "analyzeMain", "planSvg", "elevSvg"]) {
    const n = document.getElementById(id);
    if (n) modeObs.observe(n, { attributes: true, attributeFilter: ["class"] });
  }
  document.querySelectorAll(".tool-btn").forEach(b => modeObs.observe(b, { attributes: true, attributeFilter: ["class"] }));
  sync();

  /* ---------------- tooltips ---------------- */
  const tipMenus = () => {
    document.querySelectorAll("#etabsMenubar .etabs-menu-item[data-act]").forEach(it => {
      if (it.title) return;
      const h = uxHELP[it.dataset.act];
      const lbl = (it.querySelector(".mi-label") || it).textContent.trim();
      const hn = it.querySelector(".mi-hint");
      it.title = h ? `${h.text}\nETABS: ${h.etabs}` : lbl + (hn && hn.textContent ? ` — ${hn.textContent.trim()}` : "");
    });
    document.querySelectorAll("#etabsMenubar .etabs-menu-btn").forEach(b => {
      if (!b.title) b.title = `${b.dataset.menu} menu · Ctrl+K to search all commands`;
    });
  };
  const tipPalette = () => {
    document.querySelectorAll("#etabsPalette .etabs-tool").forEach(b => {
      if (b.dataset.uxTip) return;
      const h = TOOL_HINT[b.dataset.tool];
      if (h) b.title = `${b.title || h[0]} — ${h[1]}`;
      b.dataset.uxTip = "1";
    });
    document.querySelectorAll("#etabsExplorer .ex-leaf:not([title])").forEach(l => {
      const t = l.textContent.trim();
      l.title = `${t} — click to open`;
    });
    document.querySelectorAll("#etabsExplorer .ex-group-head:not([title])").forEach(h => { h.title = "Expand / collapse"; });
    const TAB_KEYS = ["view3d", "story", "modal", "reactions", "forces", "design", "drift", "th", "pushover"];
    document.querySelectorAll(".tabbar .tab:not([title])").forEach(t => {
      const k = TAB_KEYS.indexOf(t.dataset.tab);
      t.title = `${t.textContent.trim()}${k >= 0 ? ` (${k + 1})` : ""} — results after Run (F5)`;
    });
  };
  tipMenus(); tipPalette();
  const mb = document.getElementById("etabsMenubar");
  // menus added later by other modules: refresh titles on first hover/open
  if (mb) mb.addEventListener("mouseenter", () => tipMenus(), { passive: true });
  setTimeout(() => { tipMenus(); tipPalette(); }, 0);

  sky.ux = sky.ux || {};
  sky.ux.status = { sync, hintText, lastRun: () => lastRun };
}
