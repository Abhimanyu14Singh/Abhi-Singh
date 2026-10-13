/* SkyFrame Wave G — results toolbar + Results Display Options dialog.

   A compact toolbar over the drawing area (shown once results exist):

     case / combo / mode / step-case selector · ◀ step / time slider ▶ ·
     Step | Max | Min | Max/Min envelope · ▶ Play / ❚❚ Pause · speed ·
     cycles · display of the ACTIVE view (deformed, frame diagram
     component, value mode, contour) · All views · Options… · Member… ·
     Watch · ×

   Drives deformed shapes, mode / buckling shapes (mode stepping, period in
   the title), time-history steps, pushover / nonlinear static steps and
   staged-construction stages live on screen (no video needed — File ›
   Display › Export Animation Video… keeps working). Response-spectrum
   cases show their positive envelopes only. Menu: Display › Results
   Display Options… (dis-g-opts). View settings only; the model never
   changes. */

import gBU from "./units.js";
import { el as gbEl, esc as gbEsc, dkForm as gbForm, dkGroup as gbGroup, dkDialog as gbDialog, dkNote as gbNote, dkTabs as gbTabs } from "./dlgkit.js";

const BAR_KEY = "skyframe.gresbar.v1";
const CSS = `
.g-bar { position: absolute; left: 12px; right: 210px; bottom: 10px; margin: 0 auto; width: max-content; z-index: 35; display: flex; flex-wrap: wrap;
  align-items: center; justify-content: center; gap: 4px 6px; max-width: calc(100% - 222px); padding: 5px 8px; border-radius: 8px;
  background: rgba(17, 22, 29, 0.94); border: 1px solid var(--border-soft, #2a3340); box-shadow: 0 6px 18px rgba(0,0,0,.35);
  font-size: 11.5px; color: var(--text-2, #c8d0da); }
.g-bar.hidden, .g-bar-pill.hidden { display: none; }
.g-bar select, .g-bar input[type=number] { background: var(--bg-2, #141a22); color: var(--text-1, #e8edf3); border: 1px solid var(--border-soft, #2a3340);
  border-radius: 5px; font-size: 11.5px; height: 24px; padding: 0 4px; max-width: 170px; line-height: 22px; }
.g-bar input[type=number] { width: 46px; }
.g-bar button { background: var(--bg-2, #141a22); color: var(--text-1, #e8edf3); border: 1px solid var(--border-soft, #2a3340); border-radius: 5px;
  height: 24px; padding: 0 7px; font-size: 11.5px; cursor: pointer; }
.g-bar button:hover { border-color: var(--accent, #35b5e5); }
.g-bar button.is-on { background: rgba(53,181,229,.18); border-color: var(--accent, #35b5e5); color: #fff; }
.g-bar button:disabled { opacity: .45; cursor: default; }
.g-bar .g-title { font-weight: 700; color: var(--text-1, #e8edf3); letter-spacing: .02em; }
.g-bar .g-sep { width: 1px; height: 18px; background: var(--border-soft, #2a3340); }
.g-bar .g-seg { display: inline-flex; }
.g-bar .g-seg button { border-radius: 0; margin-left: -1px; }
.g-bar .g-seg button:first-child { border-radius: 5px 0 0 5px; margin-left: 0; }
.g-bar .g-seg button:last-child { border-radius: 0 5px 5px 0; }
.g-bar input[type=range] { width: 110px; accent-color: var(--accent, #35b5e5); }
.g-bar #gResSpeed { width: 64px; }
.g-bar .g-lab { min-width: 92px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-1, #e8edf3); font-variant-numeric: tabular-nums; }
.g-bar .g-status { color: var(--warn, #e0b050); max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.g-bar label { display: inline-flex; align-items: center; gap: 3px; white-space: nowrap; }
.g-bar .g-view { padding: 1px 6px; border-radius: 4px; background: rgba(53,181,229,.14); color: var(--accent, #35b5e5); font-weight: 650; }
.g-bar-pill { position: absolute; right: 12px; bottom: 54px; z-index: 35; background: rgba(17,22,29,.92); color: var(--text-1, #e8edf3);
  border: 1px solid var(--border-soft, #2a3340); border-radius: 14px; padding: 3px 10px; font-size: 11px; cursor: pointer; }
`;

const COMPS = [["", "No diagram"], ["N", "Axial P"], ["V2", "Shear V2"], ["V3", "Shear V3"], ["T", "Torsion T"], ["M2", "Moment M2"], ["M3", "Moment M3"]];
const COMPS_2D = [["VIN", "Shear (in-plane)"], ["MIN", "Moment (in-plane)"]];
const CONTOURS = [["", "No contour"], ["UX", "Ux"], ["UY", "Uy"], ["UZ", "Uz"], ["U", "|U| resultant"], ["UXY", "|U| plan"], ["RX", "Rx"], ["RY", "Ry"], ["RZ", "Rz"]];
const VALUES = [["fill", "Fill diagram"], ["values", "Show values on diagram"], ["controlling", "Values at controlling stations"]];
const ENVS = [["step", "Step"], ["max", "Max"], ["min", "Min"], ["maxmin", "Max/Min"]];
const VIEW_NAME = id => (id === "main3d" ? "3D" : id === "plan" ? "Plan" : id === "elev" ? "Elevation" : id.startsWith("pane") ? `Window ${+id.slice(4) + 1}` : id);

export function gInitBar(sky, api) {
  const S = sky.store;
  if (!document.getElementById("gResBarCss")) document.head.appendChild(gbEl("style", { id: "gResBarCss", text: CSS }));
  const host = document.getElementById("etabsCanvas") || document.getElementById("app") || document.body;
  let wanted = true;
  try { wanted = localStorage.getItem(BAR_KEY) !== "off"; } catch { /* private mode */ }

  const $ = id => document.getElementById(id);
  const bar = gbEl("div", { class: "g-bar hidden", id: "gResBar", role: "toolbar", "aria-label": "Results toolbar" });
  const pill = gbEl("button", { type: "button", class: "g-bar-pill hidden", id: "gResPill", title: "Show the results toolbar" }, ["▸ Results"]);
  const caseSel = gbEl("select", { id: "gResCase", "aria-label": "Results case", title: "Case / combination / mode / multi-step case" });
  const prev = gbEl("button", { type: "button", id: "gResPrev", title: "Previous step / mode" }, ["◀"]);
  const slider = gbEl("input", { type: "range", id: "gResStep", min: "0", max: "0", step: "1", value: "0", "aria-label": "Step / time" });
  const next = gbEl("button", { type: "button", id: "gResNext", title: "Next step / mode" }, ["▶"]);
  const lab = gbEl("span", { class: "g-lab", id: "gResStepLabel" });
  const env = gbEl("span", { class: "g-seg", id: "gResEnv", role: "group", "aria-label": "Step or envelope" },
    ENVS.map(([v, t]) => gbEl("button", { type: "button", "data-env": v, title: v === "step" ? "Results at the selected step" : v === "maxmin" ? "Envelope: max and min (two curves)" : `Envelope ${t.toLowerCase()} over the steps` }, [t])));
  const playB = gbEl("button", { type: "button", id: "gResPlay", title: "Start / stop the animation" }, ["▶ Play"]);
  const speed = gbEl("input", { type: "range", id: "gResSpeed", min: "0.25", max: "4", step: "0.25", value: "1", "aria-label": "Animation speed" });
  const speedLab = gbEl("span", { id: "gResSpeedLab", text: "×1" });
  const cycles = gbEl("input", { type: "number", id: "gResCycles", min: "0", max: "99", step: "1", value: "0", title: "Cycles to play (0 = loop until paused)", "aria-label": "Cycles" });
  const viewLab = gbEl("span", { class: "g-view", id: "gResViewLab", title: "These display settings apply to the active view" });
  const defB = gbEl("button", { type: "button", id: "gResDef", title: "Deformed shape in the active view" }, ["Deformed"]);
  const diag = gbEl("select", { id: "gResDiag", "aria-label": "Frame diagram component" });
  const vals = gbEl("select", { id: "gResVals", "aria-label": "Diagram values" }, VALUES.map(([v, t]) => gbEl("option", { value: v }, [t])));
  const cont = gbEl("select", { id: "gResCont", "aria-label": "Displacement contour" }, CONTOURS.map(([v, t]) => gbEl("option", { value: v }, [t])));
  const allB = gbEl("button", { type: "button", id: "gResAll", title: "Apply this view's display settings to every view" }, ["All views"]);
  const optB = gbEl("button", { type: "button", id: "gResOpts", title: "Results Display Options…" }, ["Options…"]);
  const detB = gbEl("button", { type: "button", id: "gResDetail", title: "Diagrams for Frame Object (member detail)" }, ["Member…"]);
  const watchB = gbEl("button", { type: "button", id: "gResWatch", title: "Result probes / watch list" }, ["Watch"]);
  const status = gbEl("span", { class: "g-status", id: "gResStatus" });
  const close = gbEl("button", { type: "button", id: "gResClose", title: "Hide the results toolbar", "aria-label": "Hide results toolbar" }, ["×"]);
  bar.append(gbEl("span", { class: "g-title", text: "Results" }), caseSel, prev, slider, next, lab, env, playB,
    gbEl("label", { title: "Animation speed" }, [speed, speedLab]), gbEl("label", { title: "Cycles (0 = loop)" }, ["×", cycles]),
    gbEl("span", { class: "g-sep" }), viewLab, defB, diag, vals, cont, allB, optB, detB, watchB, status, close);
  host.appendChild(bar);
  host.appendChild(pill);

  /* ---------------------------------------------------------------- events */
  caseSel.addEventListener("change", () => api.select(caseSel.value));
  prev.addEventListener("click", () => api.step(-1));
  next.addEventListener("click", () => api.step(1));
  slider.addEventListener("input", () => api.setStep(+slider.value));
  env.addEventListener("click", e => { const b = e.target.closest("button[data-env]"); if (b && !b.disabled) api.setEnv(b.dataset.env); });
  playB.addEventListener("click", () => api.toggle());
  speed.addEventListener("input", () => api.setSpeed(+speed.value));
  cycles.addEventListener("change", () => api.setCycles(+cycles.value));
  const setOpt = patch => api.setViewOpts(api.activeView(), patch);
  defB.addEventListener("click", () => { const o = api.viewOpts(); setOpt({ deformed: !o.deformed }); ensureSteps(); });
  diag.addEventListener("change", () => { setOpt({ diagram: diag.value }); ensureSteps(); });
  vals.addEventListener("change", () => setOpt({ values: vals.value }));
  cont.addEventListener("change", () => { setOpt({ contour: cont.value }); ensureSteps(); });
  allB.addEventListener("click", () => { const o = api.viewOpts(); api.setViewOpts(null, o, { all: true }); sky.toast && sky.toast("Results display", `${VIEW_NAME(api.activeView())} settings applied to every view`, "info", 2500); });
  optB.addEventListener("click", () => openOptions());
  detB.addEventListener("click", () => api.openDetail && api.openDetail(null));
  watchB.addEventListener("click", () => api.openWatch && api.openWatch());
  close.addEventListener("click", () => setWanted(false));
  pill.addEventListener("click", () => setWanted(true));
  function setWanted(on) { wanted = !!on; try { localStorage.setItem(BAR_KEY, on ? "on" : "off"); } catch { /* ignore */ } sync(); }
  function ensureSteps() { const e = api.entry(); if (e && api.core.needsStepData(e) && !api.stepData()) api.loadSteps(); }
  ["sky:mode-changed", "sky:view-changed"].forEach(ev => document.addEventListener(ev, () => sync()));
  document.addEventListener("pointerdown", () => setTimeout(sync, 0), true);

  /* ---------------------------------------------------------------- sync */
  let catSig = null;
  function fillCases() {
    const cat = api.catalog();
    const sig = cat.map(e => e.key).join("|");
    if (sig === catSig) return;
    catSig = sig;
    caseSel.textContent = "";
    const groups = [["Cases", ["static"]], ["Combinations", ["combo", "minmax"]], ["Response spectrum (envelopes)", ["rs"]],
      ["Modes", ["modal", "buckling"]], ["Time history", ["th"]], ["Nonlinear static / pushover", ["nl", "pushover"]], ["Staged construction", ["staged"]]];
    for (const [g, kinds] of groups) {
      const es = cat.filter(e => kinds.includes(e.kind));
      if (!es.length) continue;
      const og = gbEl("optgroup", { label: g });
      for (const e of es) og.appendChild(gbEl("option", { value: e.key }, [e.kind === "minmax" ? `${e.label} (max/min)` : e.label]));
      caseSel.appendChild(og);
    }
  }
  /** Keep the bar inside the visible view area (plan / elevation / 3D / windows) and clear of the
      bottom-right workflow badge. */
  function place() {
    const H = host.getBoundingClientRect();
    const cands = ["wbPanes", "planWrap", "viewerWrap"].map(id => document.getElementById(id)).filter(e => e && e.getClientRects().length);
    const T = cands.length ? cands[0].getBoundingClientRect() : H;
    if (!(T.width > 0) || !(H.width > 0)) return;
    const left = Math.max(0, T.left - H.left) + 10;
    let right = Math.max(0, H.right - T.right) + 10;
    if (T.right > window.innerWidth - 240) right += 200;
    bar.style.left = left + "px"; bar.style.right = right + "px";
    bar.style.maxWidth = Math.max(260, H.width - left - right) + "px";
  }
  window.addEventListener("resize", () => place());
  function sync() {
    const has = !!S.results;
    bar.classList.toggle("hidden", !(has && wanted));
    pill.classList.toggle("hidden", !(has && !wanted));
    if (!has) return;
    place();
    fillCases();
    const sel = api.selection(), st = api.state(), e = api.entry(), ax = api.axis();
    if (document.activeElement !== caseSel && sel.key) caseSel.value = sel.key;
    const multi = !!(e && e.multi);
    slider.disabled = prev.disabled = next.disabled = !(multi && ax.n > 1);
    slider.max = String(Math.max(0, ax.n - 1));
    if (document.activeElement !== slider) slider.value = String(Math.max(0, sel.step));
    lab.textContent = st ? (st.stepLabel || (e && e.kind === "rs" ? "positive envelope" : e && e.minmax ? "max / min" : "")) : "";
    lab.title = lab.textContent;
    const envOk = !!(e && (e.minmax || (multi && e.kind !== "modal" && e.kind !== "buckling" && e.kind !== "staged")));
    env.querySelectorAll("button").forEach(b => {
      const v = b.dataset.env;
      b.disabled = !envOk || (v === "step" && !multi && e && e.minmax);
      b.classList.toggle("is-on", (st && st.env ? st.env : sel.env) === v || (v === "step" && sel.env === "step" && !(e && e.minmax)));
    });
    const ps = api.playState();
    playB.textContent = ps.on ? "❚❚ Pause" : "▶ Play";
    playB.classList.toggle("is-on", ps.on);
    speedLab.textContent = `×${(+ps.speed).toFixed(2).replace(/\.?0+$/, "")}`;
    if (document.activeElement !== cycles) cycles.value = String(ps.cycles);
    const vid = api.activeView(), o = api.viewOpts(vid);
    viewLab.textContent = VIEW_NAME(vid);
    const v = api.view(vid);
    const is2d = v && v.kind !== "3d";
    const opts = is2d ? [...COMPS, ...COMPS_2D] : COMPS;
    const dsig = opts.map(x => x[0]).join();
    if (diag.dataset.sig !== dsig) { diag.textContent = ""; for (const [k, t] of opts) diag.appendChild(gbEl("option", { value: k }, [t])); diag.dataset.sig = dsig; }
    diag.value = opts.some(x => x[0] === o.diagram) ? o.diagram : "";
    vals.value = o.values; cont.value = o.contour;
    defB.classList.toggle("is-on", !!o.deformed);
    const err = api.error();
    status.textContent = api.loading() ? "loading step results…" : err ? `⚠ ${err}` : st && st.note ? st.note : "";
    status.title = status.textContent;
  }
  api.subscribe(() => sync());
  document.addEventListener("sky:results-changed", () => { catSig = null; setTimeout(sync, 0); });
  gBU.onUnitsChange && gBU.onUnitsChange(() => sync());
  sync();

  /* ---------------------------------------------------------------- options dialog */
  function openOptions(viewId) {
    if (!S.results) { sky.toast && sky.toast("Results", "Run the analysis first (F5).", "info", 3000); return null; }
    if (!wanted) setWanted(true);
    const vid = viewId || api.activeView();
    const v = api.view(vid);
    const o = { ...api.viewOpts(vid), all: false, frames: api._R.frames, lo: null, hi: null };
    if (Array.isArray(o.crange)) { o.lo = o.crange[0]; o.hi = o.crange[1]; }
    const is2d = v && v.kind !== "3d";
    const m = S.model || {};
    const towers = (m.towers || []).map(t => t.name);
    const groups = Object.keys(m.groups || {});
    const F = gbForm();
    const tw = { list: new Set(o.towers || []) };
    const towerBox = gbEl("div", { class: "dk-row", id: "gResTowers" }, [gbEl("div", { class: "dk-label" }, ["Towers"]),
      gbEl("div", { class: "dk-ctl" }, towers.length ? towers.map(n => {
        const cb = gbEl("input", { type: "checkbox", "data-tower": n }); cb.checked = !tw.list.size || tw.list.has(n);
        cb.addEventListener("change", () => { if (cb.checked) tw.list.add(n); else tw.list.delete(n); });
        return gbEl("label", { class: "dk-check" }, [cb, gbEl("span", { text: n })]);
      }) : [gbEl("span", { class: "muted", text: "one tower (Define › Towers adds more)" })])]);
    const dispKind = /^R/.test(o.contour) ? "rotation" : "disp";
    const dkind = (o.diagram && ["T", "M2", "M3", "MIN"].includes(o.diagram)) ? "moment" : "force";
    const tabs = gbTabs([
      { id: "def", label: "Deformed Shape", body: gbGroup("Deformed Shape", [
        F.check(o, "deformed", { label: "Show the deformed shape", id: "gResOptDef" }),
        F.check(o, "wire", { label: "Wire shadow (undeformed outline, plan / elevation)", id: "gResOptWire" }),
        F.check(o, "cubic", { label: "Cubic curve (frames as cubic curves, else straight chords)", id: "gResOptCubic" }),
        F.radio(o, "dscaleMode", [["auto", "Auto scale"], ["user", "User scale factor"]], { label: "Scaling", idPrefix: "gResOptDsc-" }),
        F.num(o, "dscale", { label: "Auto scale multiplier", gt: 0, id: "gResOptDscale" }),
        F.num(o, "dscaleUser", { label: "User scale factor", gt: 0, id: "gResOptDscaleUser", hint: "displacement × factor" }),
      ]) },
      { id: "diag", label: "Diagrams", body: gbGroup("Frame / Pier / Spandrel / Link Diagrams", [
        F.select(o, "diagram", is2d ? [...COMPS, ...COMPS_2D] : COMPS, { label: "Component", id: "gResOptDiag", hint: is2d ? "In-plane shear / moment: the bending in this view's plane (2D views only, as ETABS)" : "" }),
        F.radio(o, "values", VALUES, { label: "Display", idPrefix: "gResOptVal-" }),
        F.radio(o, "vscaleMode", [["auto", "Auto"], ["user", "User defined"]], { label: "Scaling", idPrefix: "gResOptVsc-" }),
        F.num(o, "vscale", { label: "Auto scale multiplier", gt: 0, id: "gResOptVscale" }),
        F.num(o, "vscaleUser", { label: `User scale (${gBU.label("length")} per 1 ${gBU.label(dkind)})`, gt: 0, id: "gResOptVscaleUser", hint: "diagram length per unit value, display units" }),
        F.check(o, "reactions", { label: "Support reactions (arrows)", id: "gResOptReac" }),
        F.check(o, "piers", { label: "Pier forces", id: "gResOptPiers" }),
        F.check(o, "spandrels", { label: "Spandrel forces", id: "gResOptSpan" }),
        F.check(o, "links", { label: "Link forces (elastic links)", id: "gResOptLinks" }),
      ]) },
      { id: "cont", label: "Contours", body: gbGroup("Contours", [
        F.select(o, "contour", CONTOURS, { label: "Displacement contour on objects", id: "gResOptCont" }),
        F.check(o, "onDeformed", { label: "Draw the contours on the deformed shape", id: "gResOptOnDef" }),
        F.num(o, "lo", { label: "User range min", kind: dispKind, allowNull: true, nullText: "auto", id: "gResOptLo" }),
        F.num(o, "hi", { label: "User range max", kind: dispKind, allowNull: true, nullText: "auto", id: "gResOptHi" }),
        F.select(o, "palette", [["rainbow", "Rainbow"], ["diverging", "Diverging (blue–white–red)"]], { label: "Colours", id: "gResOptPal" }),
        F.check(o, "shell", { label: "Shell stress / force contours (Display › Force Diagrams › Shell Stresses / Forces…)", id: "gResOptShell" }),
        F.check(o, "hinges", { label: "Hinge states at the step (pushover / nonlinear cases)", id: "gResOptHinges" }),
      ]) },
      { id: "obj", label: "Objects & Steps", body: gbEl("div", {}, [
        gbGroup("Objects Shown", [
          towerBox,
          F.select(o, "group", [["", "(all objects)"], ...groups.map(g => [g, g])], { label: "Group", id: "gResOptGroup" }),
        ]),
        gbGroup("Animation", [
          F.num(o, "frames", { label: "Recorded steps (time history / pushover)", int: true, min: 2, max: 400, id: "gResOptFrames", hint: "evenly spaced steps loaded for the step selector" }),
        ]),
      ]) },
    ], { id: "gResOptTabs" });
    const body = [
      tabs,
      F.check(o, "all", { label: "Apply to all views (ETABS “Apply to All Windows”)", id: "gResOptAll" }),
      gbNote(`Settings of the <b>${gbEsc(VIEW_NAME(vid))}</b> view. The case, step and envelope are shared by every view (results toolbar).`),
    ];
    return gbDialog({
      id: "gResOptsDlg", title: "Results Display Options", size: "md", form: F, body, tabs,
      intro: "What this view draws from the selected case and step: deformed shape, frame diagrams, contours, reactions and filters.",
      onOk: () => {
        const crange = o.lo != null && o.hi != null && isFinite(o.lo) && isFinite(o.hi) ? (o.hi > o.lo ? [o.lo, o.hi] : null) : null;
        if (o.lo != null && o.hi != null && !crange) return "The user range needs min < max.";
        const patch = { deformed: !!o.deformed, wire: !!o.wire, cubic: !!o.cubic, dscaleMode: o.dscaleMode, dscale: +o.dscale || 1, dscaleUser: +o.dscaleUser || 1,
          diagram: o.diagram || "", values: o.values, vscaleMode: o.vscaleMode, vscale: +o.vscale || 1, vscaleUser: +o.vscaleUser || 0.01,
          contour: o.contour || "", onDeformed: !!o.onDeformed, crange, palette: o.palette,
          reactions: !!o.reactions, piers: !!o.piers, spandrels: !!o.spandrels, links: !!o.links, hinges: !!o.hinges, shell: !!o.shell,
          towers: towers.length && tw.list.size && tw.list.size < towers.length ? [...tw.list] : [], group: o.group || "" };
        if (patch.shell && !(sky.shellRes && sky.shellRes.state && sky.shellRes.state.on)) setTimeout(() => sky.shellRes && sky.shellRes.open && sky.shellRes.open(), 0);
        api.setFrames(o.frames);
        api.setViewOpts(vid, patch, { all: !!o.all });
        ensureSteps();
        return true;
      },
    });
  }
  api.openOptions = openOptions;
  api.showBar = on => setWanted(on !== false);
  api.barVisible = () => !bar.classList.contains("hidden");

  if (sky.menu && sky.menu.add) {
    sky.menu.add("Display", { label: "Results Display Options…", act: "dis-g-opts", needs: "results", before: "dis-tables",
      hint: "deformed · diagrams · contours · steps · per view", fn: () => openOptions() });
  }
}
