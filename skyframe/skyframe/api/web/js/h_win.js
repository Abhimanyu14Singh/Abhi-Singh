/* SkyFrame Wave H — one view window of the docking layout (js/h_dock.js).

   A window is a full view: Plan (own story), Elevation (own grid line),
   3D (own camera; Classic and GPU renderers; its OWN Set Display Options —
   a js/viewext.js instance), Table or Plot (js/h_panels.js). Plan /
   elevation / 3D windows are Wave G results views (id "pane_<win id>") with
   their own display choice and, optionally, their own results state (case /
   step / envelope; "Toolbar" = follows the results toolbar). The selection
   is shared; Wave F picking, typed context menus and draw tools work in
   every window (they attach to every editor / Viewer3D).

   Compatible with the View › Windows pane API the other modules use
   (.el.is-focus, .kind, .ed, .v, .story, .line) plus .gId / .dpId / .vx. */

import { PlanEditor as hwPlanEditor } from "./draw.js";
import { ElevEditor as hwElevEditor } from "./elev.js";
import { Viewer3D as hwViewer3D } from "./viewer3d.js";
import { initViewExt as hwInitViewExt } from "./viewext.js";
import { el as hwEl } from "./dlgkit.js";
import { HTablePanel as HwTable, HPlotPanel as HwPlot } from "./h_panels.js";

export const HW_KIND_LABEL = { plan: "Plan", elev: "Elevation", "3d": "3D", table: "Table", plot: "Plot" };
const PANE_HOOKS = ["w8Static", "grpOverlay", "uhOverlay", "npOverlay", "w8Overlay", "w8SnapHook", "w8ToolHook", "dpStatic", "dpLabels"];
const ELEV_HOOKS = ["grpOverlay", "uhOverlay", "npOverlay", "ptOverlay", "dpStatic", "dpLabels"];
const r6 = v => Math.round(v * 1e6) / 1e6;

export class HWin {
  /** ctx = {sky, dock}: dock gives selSet(), linesOf(), parseLine(), withState(), changed(id), menu(win, btn), shift() */
  constructor(ctx, st) {
    this.ctx = ctx;
    this.sky = ctx.sky;
    this.id = st.id;
    this.gId = "pane_" + st.id;
    this.dpId = st.id;
    this.i = 0;
    this.kind = st.kind || "plan";
    this.title = st.title || "";
    this.cmp = st.cmp || null;
    const S = this.sky.store;
    const stories = ((S.model && S.model.stories) || []).map(s => s.name);
    this.story = st.story && stories.includes(st.story) ? st.story : (S.story || stories[stories.length - 1] || "");
    this.line = st.line || (ctx.dock.linesOf()[0] || [""])[0];
    this.cam = st.camera || null;
    this.res = st.res || null;           // {sel, view} restored into the Wave G view once attached
    this.disp = st.disp || null;         // own 3D display options (viewext instance)
    this.dp = st.dp || null;             // own plan / elevation display options (js/dispopts.js window store)
    this.tableState = st.table || null;
    this.plotState = st.plot || null;
    this.el = hwEl("section", { class: "wb-pane h-win", "data-hwin": this.id, tabindex: "-1", "aria-label": `Window ${this.id.slice(1)}` });
    this.head = hwEl("header", { class: "wb-pane-head h-win-head" });
    this.body = hwEl("div", { class: "wb-pane-body h-win-body" });
    this.el.append(this.head, this.body);
    this.el.addEventListener("pointerdown", () => ctx.dock.activate(this.id), true);
    this.el.addEventListener("focusin", () => ctx.dock.activate(this.id));
    this.build();
  }

  /* ---------------------------------------------------------------- state */
  /** Camera of the window (rounded for saving; `exact` for linked cameras). */
  camera(exact = false) {
    const f = exact ? (v => v) : r6;
    if (this.v) return { yaw: f(this.v.yaw), pitch: f(this.v.pitch), dist: f(this.v.dist), target: this.v.target.map(f) };
    if (this.ed && this.kind === "plan") return { cx: f(this.ed.cx), cy: f(this.ed.cy), scale: f(this.ed.scale) };
    if (this.ed && this.kind === "elev") return { cs: f(this.ed.cs), cz: f(this.ed.cz), scale: f(this.ed.scale) };
    return null;
  }
  setCamera(c) {
    if (!c) return;
    if (this.v && [c.yaw, c.pitch, c.dist].every(Number.isFinite) && Array.isArray(c.target)) {
      Object.assign(this.v, { yaw: c.yaw, pitch: c.pitch, dist: c.dist, target: c.target.slice(0, 3), vyaw: 0, vpitch: 0 });
      this.v._dirty = true;
    } else if (this.ed && this.kind === "plan" && [c.cx, c.cy, c.scale].every(Number.isFinite)) {
      Object.assign(this.ed, { cx: c.cx, cy: c.cy, scale: c.scale }); this.ed._fitted = true; this.ed._applyTransform();
    } else if (this.ed && this.kind === "elev" && [c.cs, c.cz, c.scale].every(Number.isFinite)) {
      Object.assign(this.ed, { cs: c.cs, cz: c.cz, scale: c.scale }); this.ed._fitted = true; this.ed._applyTransform();
    }
  }
  gView() { const R = this.sky.results; return R && R.view ? R.view(this.kind === "table" || this.kind === "plot" ? "h" + this.kind + "_" + this.id : this.gId) : null; }
  /** Own results state of the window (null = follows the results toolbar) + its display choice. */
  resState() {
    const v = this.gView();
    if (!v) return this.res;
    const R = this.sky.results, def = R.defaults || {};
    const view = {};
    for (const [k, d] of Object.entries(def)) if (JSON.stringify(v.opts[k]) !== JSON.stringify(d)) view[k] = v.opts[k];
    const o = {};
    if (v.sel) o.sel = { key: v.sel.key, step: v.sel.step, env: v.sel.env };
    if (Object.keys(view).length) o.view = view;
    return Object.keys(o).length ? o : null;
  }
  state() {
    const o = { id: this.id, kind: this.kind };
    if (this.kind === "plan") o.story = this.story;
    if (this.kind === "elev") o.line = this.line;
    const c = this.camera();
    if (c) o.camera = c;
    if (this.title) o.title = this.title;
    if (this.cmp) o.cmp = this.cmp;
    const res = this.resState();
    if (res) o.res = res;
    if (this.vx) { const d = this.vx.opts(); if (this.vxDefault === undefined || JSON.stringify(d) !== this.vxDefault) o.disp = d; }
    else if (this.disp) o.disp = this.disp;
    const D = this.sky.dispOpts;
    if (this.ed && D && D.get) { try { const d = D.get(this.dpId); if (JSON.stringify(d) !== JSON.stringify(D.get("__default__"))) o.dp = d; } catch { /* optional */ } }
    else if (this.dp && !this.ed) o.dp = this.dp;
    if (this.table) o.table = this.table.state();
    if (this.plot) o.plot = this.plot.state();
    return o;
  }
  label() {
    const k = HW_KIND_LABEL[this.kind] || this.kind;
    if (this.title) return this.title;
    if (this.kind === "plan") return `${k} · ${this.story}`;
    if (this.kind === "elev") { const l = this.ctx.dock.linesOf().find(x => x[0] === this.line); return `${k} · ${l ? l[1] : this.line}`; }
    return k;
  }
  /** Wave G case key shown by this window (own or the toolbar's). */
  caseKey() {
    const v = this.gView(), R = this.sky.results;
    if (v && v.sel) return v.sel.key;
    return R && R.globalSel ? R.globalSel().key : null;
  }

  /* ---------------------------------------------------------------- build */
  destroy() {
    try { if (this.ed) this.ed.destroy(); } catch { /* ignore */ }
    try { if (this.v) this.v.destroy(); } catch { /* ignore */ }
    try { if (this.vx && this.vx.destroy) this.vx.destroy(); } catch { /* ignore */ }
    try { if (this.table) this.table.destroy(); if (this.plot) this.plot.destroy(); } catch { /* ignore */ }
    const sky = this.sky;
    if (this.v && sky.wb && sky.wb.draw3d) sky.wb.draw3d.detach(this.v);
    const R = sky.results;
    if (R && R._R) { R._R.views.delete(this.gId); R._R.views.delete("htable_" + this.id); R._R.views.delete("hplot_" + this.id); }
    this.ed = this.v = this.vx = this.table = this.plot = null;
  }
  build() {
    const keepRes = this.ed || this.v || this.table || this.plot ? this.resState() : this.res;
    this.destroy();
    this.res = keepRes;
    const sky = this.sky, S = sky.store, dock = this.ctx.dock;
    this.head.textContent = ""; this.body.textContent = "";
    this.el.dataset.kind = this.kind;
    const kindSel = hwEl("select", { class: "mini-select", "aria-label": `Window ${this.id.slice(1)} view`, "data-wb-kind": this.id, "data-h-kind": this.id });
    for (const [k, l] of Object.entries(HW_KIND_LABEL)) kindSel.appendChild(hwEl("option", { value: k, selected: k === this.kind || null }, [l]));
    kindSel.addEventListener("change", () => { this.kind = kindSel.value; this.cam = null; this.title = ""; this.build(); dock.changed(this.id, true); });
    this.head.append(kindSel);
    if (this.kind === "plan") {
      const ss = hwEl("select", { class: "mini-select", "aria-label": `Window ${this.id.slice(1)} story`, "data-wb-story": this.id });
      for (const s of [...((S.model && S.model.stories) || [])].reverse()) ss.appendChild(hwEl("option", { value: s.name, selected: s.name === this.story || null }, [s.name]));
      ss.addEventListener("change", () => { this.story = ss.value; this.ed.refresh(); dock.changed(this.id); });
      this.head.appendChild(ss);
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "wb-pane-svg");
      this.body.appendChild(svg);
      this.ed = new hwPlanEditor(svg, {
        getModel: () => S.model, getStory: () => this.story, getSelection: dock.selSet,
        onDraw: (tool, payload) => dock.withState("story", this.story, () => sky.handleDraw(tool, payload)),
        onErase: ref => sky.handleErase(ref), onSelect: (refs, add) => sky.handleSelect(refs, add),
        onReadout: () => {},
      });
      const main = sky.planEditor;
      for (const h of PANE_HOOKS) if (main && main[h]) this.ed[h] = main[h];
      this.ed.snapEnabled = main ? main.snapEnabled : true;
      this.ed.setTool(S.mode === "model" && S.view !== "elev" ? (S.tool || "select") : "select");
    } else if (this.kind === "elev") {
      const ls = hwEl("select", { class: "mini-select", "aria-label": `Window ${this.id.slice(1)} grid line`, "data-wb-line": this.id });
      for (const [v, l] of dock.linesOf()) ls.appendChild(hwEl("option", { value: v, selected: v === this.line || null }, [l]));
      ls.addEventListener("change", () => { this.line = ls.value; this.ed.fit(); dock.changed(this.id); });
      this.head.appendChild(ls);
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "wb-pane-svg");
      this.body.appendChild(svg);
      this.ed = new hwElevEditor(svg, {
        getModel: () => S.model, getPlane: () => dock.parseLine(this.line), getSelection: dock.selSet,
        onDraw: (tool, payload) => dock.withState("elevLine", this.line, () => sky.handleElevDraw(tool, payload)),
        onErase: ref => sky.handleErase(ref), onSelect: (refs, add) => sky.handleSelect(refs, add),
        onReadout: () => {},
      });
      const main = sky.elevEditor;
      for (const h of ELEV_HOOKS) if (main && main[h]) this.ed[h] = main[h];
      this.ed.setTool(S.mode === "model" && S.view === "elev" ? (S.tool || "select") : "select");
    } else if (this.kind === "3d") {
      const c = hwEl("canvas", { class: "wb-pane-canvas h-win-canvas" });
      this.body.appendChild(c);
      this.v = new hwViewer3D(c, {
        onMemberClick: seg => { if (sky.wb && sky.wb.draw3d && sky.wb.draw3d.active()) return; if (seg && seg.uid) sky.handleSelect([{ type: "member", uid: seg.uid }], dock.shift()); },
      });
      this.v.hWin = this;
      this.v.setModel(S.model);
      if (S.results) { this.v.setResults(S.results); if (sky.viewer) this.v.setOverlay({ ...sky.viewer.overlay }); }
      if (sky.wb && sky.wb.draw3d) sky.wb.draw3d.attach(this.v);
      try {                                  // own 3D display options (Set Display Options per window)
        this.vx = hwInitViewExt(sky, { viewer: this.v, opts: this.disp || (sky.vx ? sky.vx.opts() : null), onChange: () => dock.changed(this.id) }) || null;
        if (this.vx) { this.v.vxApi = this.vx; this.vxDefault = JSON.stringify(this.vx.opts()); if (this.disp) this.vxDefault = undefined; }
      } catch (e) { console.error("window display options", e); this.vx = null; }
      this.syncHighlight();
    } else if (this.kind === "table") {
      this.table = new HwTable(this.body, { sky, id: this.id, state: this.tableState, caseKey: () => this.caseKey(), changed: () => dock.changed(this.id) });
    } else if (this.kind === "plot") {
      this.plot = new HwPlot(this.body, { sky, id: this.id, state: this.plotState, caseKey: () => this.caseKey(), changed: () => dock.changed(this.id) });
    }
    if (this.cam) this.setCamera(this.cam);
    this.cam = null;                                 // a saved camera restores once
    this.resBox = hwEl("span", { class: "h-win-res" });
    this.linkBadge = hwEl("span", { class: "h-win-link hidden", title: "Camera linked with other windows", text: "⛓" });
    this.head.append(hwEl("span", { class: "toolbar-spacer" }), this.linkBadge);
    if (this.ed || this.v) {
      const fit = hwEl("button", { type: "button", class: "chip", title: "Fit (double-click the view)", text: "Fit" });
      fit.addEventListener("click", () => { if (this.ed) this.ed.fit(); if (this.v) this.v.fit(); dock.camMoved(this); });
      this.head.appendChild(fit);
    }
    const more = hwEl("button", { type: "button", class: "chip h-win-more", title: "Window commands", "aria-label": `Window ${this.id.slice(1)} commands`, "aria-haspopup": "menu", "data-h-more": this.id, text: "⋯" });
    more.addEventListener("click", e => { e.stopPropagation(); dock.menu(this, more); });
    this.head.appendChild(more);
    this.head.appendChild(this.resBox);              // last: wraps to a second header row in narrow windows
    if (this.table || this.plot) {
      const R = sky.results;
      if (R && R.registerView) {
        const vid = "h" + this.kind + "_" + this.id;
        const view = R.registerView(vid, this.kind, this.table || this.plot, { redraw: () => { const p = this.table || this.plot; if (p) p.refresh(); this.syncResBox(); } });
        if (this.res && this.res.sel) view.sel = { key: null, step: -1, env: "step", ...this.res.sel };
      }
    }
    this.syncResBox();
  }

  /** After the Wave G layer attached this window: restore its own results state + display choice. */
  applyRes() {
    const D = this.sky.dispOpts;
    if (this.dp && this.ed && D && D.set) { try { D.set(this.dpId, this.dp); } catch (e) { console.warn("window display options", e); } this.dp = null; }
    const v = this.gView(), R = this.sky.results;
    if (!v || !this.res) return;
    if (this.res.view) Object.assign(v.opts, JSON.parse(JSON.stringify(this.res.view)));
    if (this.res.sel && this.res.sel.key && R.entry(this.res.sel.key)) {
      v.sel = { key: null, step: -1, env: "step", ...this.res.sel };
      if (R.core.needsStepData(R.entry(v.sel.key)) && !R.stepData(R.entry(v.sel.key).name)) R.withView(v, () => R.loadSteps());
    }
    this.res = null;
    v.optsChanged && v.optsChanged();
    this.syncResBox();
  }

  /** Window header: own results case (or "Toolbar") + step arrows for a multi-step own case. */
  syncResBox() {
    const box = this.resBox, R = this.sky.results;
    if (!box) return;
    const view = this.gView();
    const has = !!(this.sky.store.results && R && R.catalog && view);
    box.classList.toggle("hidden", !has);
    if (!has) return;
    const cat = R.catalog();
    const sig = cat.map(e => e.key).join("|");
    let sel = box.querySelector("select");
    if (!sel || sel.dataset.sig !== sig) {
      box.textContent = "";
      sel = hwEl("select", { class: "mini-select h-win-case", "aria-label": `Window ${this.id.slice(1)} results case`, "data-h-case": this.id, title: "Results shown in this window (Toolbar = follows the results toolbar)" });
      sel.appendChild(hwEl("option", { value: "" }, ["Toolbar case"]));
      for (const e of cat) sel.appendChild(hwEl("option", { value: e.key }, [e.label]));
      sel.dataset.sig = sig;
      sel.addEventListener("change", () => this.ctx.dock.setWindowCase(this, sel.value || null));
      const prev = hwEl("button", { type: "button", class: "chip h-win-step", title: "Previous step / mode", text: "◀", "data-h-step": "-1" });
      const next = hwEl("button", { type: "button", class: "chip h-win-step", title: "Next step / mode", text: "▶", "data-h-step": "1" });
      const lab = hwEl("span", { class: "muted h-win-steplab" });
      prev.addEventListener("click", () => R.withView(this.gView(), () => R.step(-1)));
      next.addEventListener("click", () => R.withView(this.gView(), () => R.step(1)));
      box.append(sel, prev, lab, next);
    }
    box.hidden = false;
    const own = view.sel;
    if (document.activeElement !== sel) sel.value = own ? own.key : "";
    sel.disabled = !!this.cmp;                        // compare windows: set in Compare Side by Side…
    sel.title = this.cmp ? "Set in View › Windows › Compare Side by Side…" : "Results shown in this window (Toolbar = follows the results toolbar)";
    if (view.stateFn && this.cmp && this.cmp.role === "diff") { box.hidden = true; return; }
    const multi = own && R.entry(own.key) && R.entry(own.key).multi;
    box.querySelectorAll(".h-win-step").forEach(b => { b.hidden = !multi; });
    const lab = box.querySelector(".h-win-steplab");
    if (lab) { const st = multi ? R.withView(view, () => R.state()) : null; lab.textContent = st && st.stepLabel ? st.stepLabel : ""; lab.hidden = !multi; }
    this.el.classList.toggle("h-own-res", !!own);
  }

  syncHighlight() {
    if (!this.v) return;
    const S = this.sky.store;
    const uids = (S.selection || []).filter(r => r.type === "member").map(r => r.uid);
    this.v.setHighlight(uids.length ? uids : null, "#35b5e5");
  }
  refresh(modelChanged) {
    const S = this.sky.store;
    if (this.ed) {
      if (this.kind === "plan" && !((S.model && S.model.stories) || []).some(s => s.name === this.story)) { this.build(); return; }
      this.ed.refresh();
    }
    if (this.v) {
      if (modelChanged && !this.v.hFixedModel) { const cam = this.camera(); this.v.setModel(S.model); this.setCamera(cam); }
      this.syncHighlight();
    }
    if (this.table) this.table.refresh();
    if (this.plot && modelChanged) this.plot.refresh();
  }
}
