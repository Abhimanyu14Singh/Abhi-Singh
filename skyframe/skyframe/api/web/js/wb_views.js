/* SkyFrame Wave B — named views and multiple view windows.

   View › Named Views ▸
     Save Current View As… — the active view (plan + story, elevation +
       grid line, or 3D) with its camera and the Set Display Options state,
       stored in the model file under "named_views" (written only when at
       least one view exists, so untouched models stay byte-identical).
     Named Views… — list: Show, Rename, Delete.
   View › Windows ▸ — 1 / 2 (side by side, stacked) / 3 / 4 synchronised
     panes over the drawing canvas. Each pane is its own Plan / Elevation /
     3D view (own story / grid line / camera); the selection is SHARED (a
     click in any pane selects everywhere, the 3D panes highlight it), model
     edits and results refresh every pane, and the active draw tool works in
     plan / elevation panes on the pane's own story / line.
   (CONTRACT "Model templates, general grids, shell editing and drawing
   aids".) */

import { PlanEditor as vPlanEditor } from "./draw.js";
import { ElevEditor as vElevEditor } from "./elev.js";
import { Viewer3D as vViewer3D } from "./viewer3d.js";
import { el as vEl, esc as vEsc, dkForm as vForm, dkGroup as vGroup, dkList as vList, dkDialog as vDialog, dkNote as vNote,
  uniqueName as vUnique } from "./dlgkit.js";

const LAYOUTS = { 1: "Single Window", "2v": "Two Windows (Side by Side)", "2h": "Two Windows (Stacked)", 3: "Three Windows", 4: "Four Windows (2 × 2)" };
const PANE_HOOKS = ["w8Static", "grpOverlay", "uhOverlay", "npOverlay", "w8Overlay", "w8SnapHook", "w8ToolHook", "dpStatic", "dpLabels"];   // D2: + display options

export function initWbViews(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 3500) => sky.toast && sky.toast(t, m, k, ms);
  const clone = o => JSON.parse(JSON.stringify(o));
  const r6 = v => Math.round(v * 1e6) / 1e6;

  /* ================================================================ named views */
  function activeKind() {
    if (S.mode === "analyze" && (S.tab || "view3d") === "view3d") return "3d";
    return S.view === "elev" ? "elev" : "plan";
  }
  function capture(name) {
    const v = { name, view: activeKind() };
    if (v.view === "3d") {
      const w = sky.viewer;
      v.camera = { yaw: r6(w.yaw), pitch: r6(w.pitch), dist: r6(w.dist), target: w.target.map(r6) };
    } else if (v.view === "elev") {
      const e = sky.elevEditor;
      v.elev_line = S.elevLine || "";
      v.camera = { cs: r6(e.cs), cz: r6(e.cz), scale: r6(e.scale) };
    } else {
      const p = sky.planEditor;
      v.story = S.story || "";
      v.camera = { cx: r6(p.cx), cy: r6(p.cy), scale: r6(p.scale) };
    }
    if (sky.vx && sky.vx.opts) { try { v.display = clone(sky.vx.opts()); } catch { /* not JSON */ } }
    if (sky.dispOpts && v.view !== "3d") { const dw = sky.dispOpts.captureView(v.view); if (dw) v.display = Object.assign(v.display || {}, { dp_windows: dw }); }   // D2: plan / elevation display options
    if (sky.hDock) { const hl = sky.hDock.capture(); if (hl) v.display = Object.assign(v.display || {}, { h_dock: hl }); }   // Wave H: docking layout (js/h_dock.js)
    else if (WIN.layout !== "1") v.display = Object.assign(v.display || {}, { wb_windows: { layout: WIN.layout, panes: WIN.panes.map(p => p.state()) } });
    if (sky.results && sky.results.captureView) { try { const rv = sky.results.captureView(); if (rv) v.display = Object.assign(v.display || {}, { g_results: rv }); } catch (e) { /* optional */ } }   // Wave G: results view (case / step / display, js/g_results.js)
    return v;
  }
  function apply(v) {
    if (!v) return false;
    if (v.view === "3d") {
      if (sky.etabs && sky.etabs.showResult) sky.etabs.showResult("view3d"); else { sky.setMode("analyze"); sky.switchTab("view3d"); }
      const w = sky.viewer, c = v.camera || {};
      if (isFinite(c.yaw)) w.yaw = c.yaw;
      if (isFinite(c.pitch)) w.pitch = c.pitch;
      if (isFinite(c.dist)) w.dist = c.dist;
      if (Array.isArray(c.target)) w.target = c.target.slice(0, 3);
      w.vyaw = w.vpitch = 0; w._dirty = true;
    } else if (v.view === "elev") {
      sky.setMode("model"); sky.setView("elev");
      if (v.elev_line) { try { sky.setElevLine(v.elev_line); } catch { /* stale line */ } }
      const e = sky.elevEditor, c = v.camera || {};
      if (isFinite(c.cs)) e.cs = c.cs; if (isFinite(c.cz)) e.cz = c.cz; if (isFinite(c.scale)) e.scale = c.scale;
      e.refresh();
    } else {
      sky.setMode("model"); sky.setView("plan");
      if (v.story && (S.model.stories || []).some(s => s.name === v.story)) sky.setStory(v.story);
      const p = sky.planEditor, c = v.camera || {};
      if (isFinite(c.cx)) p.cx = c.cx; if (isFinite(c.cy)) p.cy = c.cy; if (isFinite(c.scale)) p.scale = c.scale;
      p.refresh();
    }
    const disp = v.display || {};
    if (sky.vx && sky.vx.set) {
      const vxo = { ...disp }; delete vxo.wb_windows; delete vxo.dp_windows; delete vxo.g_results; delete vxo.h_dock;   // Wave H   // D2: per-window 2D display options travel separately (Wave G: + results view)
      if (Object.keys(vxo).length && !("dp" in vxo)) vxo.dp = null;   // D2: a view saved without extras resets them
      if (Object.keys(vxo).length) { try { sky.vx.set(vxo); } catch (e) { console.warn("named view display", e); } }
    }
    if (sky.dispOpts && v.view !== "3d") { try { sky.dispOpts.restoreView(v.view, disp.dp_windows); } catch (e) { console.warn("named view display", e); } }   // D2
    if (sky.hDock) sky.hDock.restore(disp.h_dock || disp.wb_windows || null);   // Wave H: docking layout (old 1–4 pane layouts migrate)
    else if (disp.wb_windows) setLayout(disp.wb_windows.layout, disp.wb_windows.panes);
    else if (WIN.layout !== "1") setLayout("1");
    if (disp.g_results && sky.results && sky.results.applyView) { try { sky.results.applyView(disp.g_results); } catch (e) { console.warn("named view results", e); } }   // Wave G
    if (sky.etabs && sky.etabs.refresh) sky.etabs.refresh();
    return true;
  }
  const views = () => (S.model && S.model.named_views) || [];
  function writeViews(list) {
    if (list.length) S.model.named_views = list; else delete S.model.named_views;
    sky.markDirty();
  }
  function save(name) {
    name = String(name || "").trim();
    if (!name) throw new Error("Enter a name.");
    const list = views().filter(v => v.name !== name);
    const v = capture(name);
    const i = views().findIndex(x => x.name === name);
    if (i >= 0) list.splice(i, 0, v); else list.push(v);
    writeViews(list);
    return v;
  }
  const show = name => apply(views().find(v => v.name === name));
  function remove(name) { writeViews(views().filter(v => v.name !== name)); }
  function rename(a, b) {
    b = String(b || "").trim();
    if (!b || views().some(v => v.name === b)) return false;
    writeViews(views().map(v => (v.name === a ? { ...v, name: b } : v)));
    return true;
  }

  function openSave() {
    const V = { name: vUnique(new Set(views().map(v => v.name)), "View") };
    const F = vForm();
    const kind = activeKind();
    return vDialog({ id: "wbViewSave", title: "Save Named View", iconId: "menu-view", size: "sm", form: F,
      intro: `Saves the current <b>${kind === "3d" ? "3D" : kind === "elev" ? "elevation" : "plan"}</b> view: camera, ${kind === "plan" ? "story" : kind === "elev" ? "grid line" : "orbit"}${(sky.hDock ? sky.hDock.layout() : WIN.layout) !== "1" ? ", the window layout" : ""} and display options.`,
      body: [vGroup("Named View", [F.text(V, "name", { label: "View name", id: "wbViewName",
        hint: "An existing name is replaced by the current view." })])],
      onOk: () => { try { save(V.name); toast("Named view", `“${V.name.trim()}” saved in the model`); } catch (e) { return e.message; } } });
  }
  function openList() {
    const list = vList({ id: "wbViewList", empty: "No named views yet — use <b>Save Current View As…</b>.",
      items: () => views().map(v => ({ key: v.name, v })),
      columns: [{ label: "Name", get: it => it.v.name }, { label: "View", get: it => (it.v.view === "3d" ? "3D" : it.v.view === "elev" ? "Elevation" : "Plan") },
        { label: "Story / line", get: it => it.v.story || it.v.elev_line || "—" },
        { label: "Windows", get: it => { const d = it.v.display || {}; return d.h_dock ? `${Object.keys(d.h_dock.wins || {}).length} windows` : d.wb_windows ? LAYOUTS[d.wb_windows.layout] || "" : ""; } }],   // Wave H
      actions: [
        { label: "Show View", id: "wbViewShow", needsSel: true, fn: k => { show(k); d.close(); } },
        { label: "Save Current As…", id: "wbViewAdd", fn: () => { d.close(); openSave(); } },
        { label: "Update with Current", id: "wbViewUpd", needsSel: true, fn: k => { save(k); list.render(); } },
        { label: "Rename…", id: "wbViewRen", needsSel: true, fn: k => {
          const V = { name: k }; const F = vForm();
          vDialog({ id: "wbViewRename", title: "Rename Named View", size: "sm", form: F, body: [F.text(V, "name", { label: "New name", id: "wbViewNewName" })],
            onOk: () => (rename(k, V.name) ? (list.render(), true) : "That name is empty or already used.") });
        } },
        { label: "Delete", id: "wbViewDel", needsSel: true, fn: k => { remove(k); list.render(); } },
      ],
      onOpen: k => { show(k); d.close(); } });
    const d = vDialog({ id: "wbViewList", title: "Named Views", iconId: "menu-view", size: "md", body: [list,
      vNote("Named views are saved in the model file. They store the camera, story / grid line, the window layout and the display options.")],
    noCancel: true });
    return d;
  }

  /* ================================================================ windows */
  const WIN = { layout: "1", host: null, panes: [], raf: 0, focus: 0 };
  const canvas = () => document.getElementById("etabsCanvas");
  const selSet = () => new Set((S.selection || []).map(r => `${r.type}:${r.uid}`));
  const linesOf = () => {
    const g = S.model && S.model.grid;
    if (!g) return [];
    return [...(g.x_lines || []).map((_, i) => [`x:${i}`, `X-line ${(g.x_labels || [])[i] || i + 1}`]),
      ...(g.y_lines || []).map((_, i) => [`y:${i}`, `Y-line ${(g.y_labels || [])[i] || i + 1}`])];
  };
  const parseLine = s => { const m = /^([xy]):(\d+)$/.exec(s || ""); return m ? { axis: m[1], index: +m[2] } : null; };
  function withState(key, val, fn) {
    const old = S[key];
    S[key] = val;
    try { return fn(); } finally { S[key] = old; }
  }

  class Pane {
    constructor(i, st = {}) {
      this.i = i;
      this.kind = st.kind || ["plan", "3d", "elev", "plan"][i] || "plan";
      const stories = (S.model.stories || []).map(s => s.name);
      this.story = st.story && stories.includes(st.story) ? st.story : (i === 3 ? stories[0] : S.story) || stories[stories.length - 1];
      this.line = st.line || (linesOf()[0] || [""])[0];
      this.cam = st.camera || null;
      this.el = vEl("section", { class: "wb-pane", "data-pane": String(i), tabindex: "-1" });
      this.head = vEl("header", { class: "wb-pane-head" });
      this.body = vEl("div", { class: "wb-pane-body" });
      this.el.append(this.head, this.body);
      this.el.addEventListener("pointerdown", () => { WIN.focus = this.i; for (const p of WIN.panes) p.el.classList.toggle("is-focus", p === this); }, true);
      this.build();
    }
    state() {
      const o = { kind: this.kind };
      if (this.kind === "plan") { o.story = this.story; if (this.ed) o.camera = { cx: r6(this.ed.cx), cy: r6(this.ed.cy), scale: r6(this.ed.scale) }; }
      else if (this.kind === "elev") { o.line = this.line; if (this.ed) o.camera = { cs: r6(this.ed.cs), cz: r6(this.ed.cz), scale: r6(this.ed.scale) }; }
      else if (this.v) o.camera = { yaw: r6(this.v.yaw), pitch: r6(this.v.pitch), dist: r6(this.v.dist), target: this.v.target.map(r6) };
      return o;
    }
    destroy() {
      try { if (this.ed) this.ed.destroy(); } catch { /* ignore */ }
      try { if (this.v) this.v.destroy(); } catch { /* ignore */ }
      if (this.v && sky.wb.draw3d) sky.wb.draw3d.detach(this.v);
      this.ed = this.v = null;
    }
    build() {
      this.destroy();
      this.head.textContent = "";
      this.body.textContent = "";
      const kindSel = vEl("select", { class: "mini-select", "aria-label": `Window ${this.i + 1} view`, "data-wb-kind": String(this.i) });
      for (const [k, l] of [["plan", "Plan"], ["elev", "Elevation"], ["3d", "3D"]]) kindSel.appendChild(vEl("option", { value: k, selected: k === this.kind || null }, [l]));
      kindSel.addEventListener("change", () => { this.kind = kindSel.value; this.cam = null; this.build(); });
      this.head.append(vEl("b", { text: `${this.i + 1}` }), kindSel);
      if (this.kind === "plan") {
        const ss = vEl("select", { class: "mini-select", "aria-label": `Window ${this.i + 1} story`, "data-wb-story": String(this.i) });
        for (const s of [...(S.model.stories || [])].reverse()) ss.appendChild(vEl("option", { value: s.name, selected: s.name === this.story || null }, [s.name]));
        ss.addEventListener("change", () => { this.story = ss.value; this.ed.refresh(); });
        this.head.appendChild(ss);
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("class", "wb-pane-svg");
        this.body.appendChild(svg);
        this.ed = new vPlanEditor(svg, {
          getModel: () => S.model, getStory: () => this.story, getSelection: selSet,
          onDraw: (tool, payload) => withState("story", this.story, () => sky.handleDraw(tool, payload)),
          onErase: ref => sky.handleErase(ref), onSelect: (refs, add) => sky.handleSelect(refs, add),
          onReadout: () => {},
        });
        const main = sky.planEditor;
        for (const h of PANE_HOOKS) if (main && main[h]) this.ed[h] = main[h];
        this.ed.snapEnabled = main ? main.snapEnabled : true;
        this.ed.setTool(S.mode === "model" && S.view !== "elev" ? (S.tool || "select") : "select");
        if (this.cam && [this.cam.cx, this.cam.cy, this.cam.scale].every(isFinite)) { Object.assign(this.ed, { cx: this.cam.cx, cy: this.cam.cy, scale: this.cam.scale }); this.ed._fitted = true; this.ed.refresh(); }
      } else if (this.kind === "elev") {
        const ls = vEl("select", { class: "mini-select", "aria-label": `Window ${this.i + 1} grid line`, "data-wb-line": String(this.i) });
        for (const [v, l] of linesOf()) ls.appendChild(vEl("option", { value: v, selected: v === this.line || null }, [l]));
        ls.addEventListener("change", () => { this.line = ls.value; this.ed.fit(); });
        this.head.appendChild(ls);
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("class", "wb-pane-svg");
        this.body.appendChild(svg);
        this.ed = new vElevEditor(svg, {
          getModel: () => S.model, getPlane: () => parseLine(this.line), getSelection: selSet,
          onDraw: (tool, payload) => withState("elevLine", this.line, () => sky.handleElevDraw(tool, payload)),
          onErase: ref => sky.handleErase(ref), onSelect: (refs, add) => sky.handleSelect(refs, add),
          onReadout: () => {},
        });
        const main = sky.elevEditor;
        for (const h of ["grpOverlay", "uhOverlay", "npOverlay", "ptOverlay", "dpStatic", "dpLabels"]) if (main && main[h]) this.ed[h] = main[h];   // D2: + display options
        this.ed.setTool(S.mode === "model" && S.view === "elev" ? (S.tool || "select") : "select");
        if (this.cam && [this.cam.cs, this.cam.cz, this.cam.scale].every(isFinite)) { Object.assign(this.ed, { cs: this.cam.cs, cz: this.cam.cz, scale: this.cam.scale }); this.ed._fitted = true; this.ed.refresh(); }
      } else {
        const c = vEl("canvas", { class: "wb-pane-canvas" });
        this.body.appendChild(c);
        this.v = new vViewer3D(c, {
          onMemberClick: seg => { if (sky.wb.draw3d && sky.wb.draw3d.active()) return; if (seg && seg.uid) sky.handleSelect([{ type: "member", uid: seg.uid }], !!WIN.shift); },
        });
        this.v.setModel(S.model);
        if (S.results) { this.v.setResults(S.results); if (sky.viewer) this.v.setOverlay({ ...sky.viewer.overlay }); }
        if (this.cam && [this.cam.yaw, this.cam.pitch, this.cam.dist].every(isFinite) && Array.isArray(this.cam.target)) Object.assign(this.v, { yaw: this.cam.yaw, pitch: this.cam.pitch, dist: this.cam.dist, target: this.cam.target.slice() });
        if (sky.wb.draw3d) sky.wb.draw3d.attach(this.v);
        this.syncHighlight();
      }
      this.cam = null;                                // a saved camera restores once
      const fit = vEl("button", { type: "button", class: "chip", title: "Fit (double-click the view)", text: "Fit" });
      fit.addEventListener("click", () => { if (this.ed) this.ed.fit(); if (this.v) this.v.fit(); });
      this.head.append(vEl("span", { class: "toolbar-spacer" }), fit);
    }
    syncHighlight() {
      if (!this.v) return;
      const uids = (S.selection || []).filter(r => r.type === "member").map(r => r.uid);
      this.v.setHighlight(uids.length ? uids : null, "#35b5e5");
    }
    refresh(modelChanged) {
      if (this.ed) {
        if (this.kind === "plan" && !(S.model.stories || []).some(s => s.name === this.story)) { this.build(); return; }
        this.ed.refresh();
      }
      if (this.v) {
        if (modelChanged) { const cam = [this.v.yaw, this.v.pitch, this.v.dist, this.v.target]; this.v.setModel(S.model); [this.v.yaw, this.v.pitch, this.v.dist, this.v.target] = cam; this.v._dirty = true; }
        this.syncHighlight();
      }
    }
  }

  let pendingModel = false;
  function schedule(modelChanged) {
    if (WIN.layout === "1") return;
    pendingModel = pendingModel || !!modelChanged;
    if (WIN.raf) return;
    WIN.raf = requestAnimationFrame(() => {
      WIN.raf = 0;
      const mc = pendingModel;
      pendingModel = false;
      for (const p of WIN.panes) { try { p.refresh(mc); } catch (e) { console.error("window refresh", e); } }
    });
  }
  function setLayout(layout, paneStates) {
    if (sky.hDock) return sky.hDock.setPreset(layout, paneStates);   // Wave H: presets build docking layouts (js/h_dock.js)
    layout = String(layout || "1");
    if (!LAYOUTS[layout]) layout = "1";
    const prev = WIN.panes.map(p => p.state());
    for (const p of WIN.panes) p.destroy();
    WIN.panes = [];
    if (WIN.host) { WIN.host.remove(); WIN.host = null; }
    WIN.layout = layout;
    const cv = canvas();
    if (layout !== "1" && cv && S.model) {
      const n = layout === "2v" || layout === "2h" ? 2 : +layout;
      WIN.host = vEl("div", { class: `wb-panes wb-layout-${layout}`, id: "wbPanes", role: "region", "aria-label": "View windows" });
      cv.appendChild(WIN.host);
      for (let i = 0; i < n; i++) {
        const p = new Pane(i, (paneStates || prev)[i] || {});
        WIN.panes.push(p);
        WIN.host.appendChild(p.el);
      }
      WIN.panes[0].el.classList.add("is-focus");
      WIN.focus = 0;
    }
    if (sky.etabs && sky.etabs.refresh) sky.etabs.refresh();
    document.dispatchEvent(new CustomEvent("sky:wb-windows", { detail: { layout } }));
    return WIN.panes.length;
  }

  // keep every pane in step with the main views
  const pe = sky.planEditor;
  if (pe) {
    const rs = pe.renderStatic.bind(pe);
    pe.renderStatic = () => { rs(); schedule(false); };
  }
  document.addEventListener("sky:model-changed", () => schedule(true));
  document.addEventListener("sky:results-changed", () => {
    for (const p of WIN.panes) if (p.v) { p.v.setResults(S.results); if (sky.viewer) p.v.setOverlay({ ...sky.viewer.overlay }); }
  });
  document.addEventListener("keydown", e => { WIN.shift = e.shiftKey; }, true);
  document.addEventListener("keyup", e => { WIN.shift = e.shiftKey; }, true);
  const st = sky.setTool;
  if (typeof st === "function") {
    sky.setTool = t => {
      st(t);
      for (const p of WIN.panes) if (p.ed) { try { p.ed.setTool(p.kind === "elev" && (t === "slab" || t === "linespring") ? "select" : (S.tool || t)); } catch (e) { console.error(e); } }
    };
  }

  if (sky.menu) {
    sky.menu.add("View/Named Views", { label: "Save Current View As…", act: "vx-wb-saveview", fn: openSave });
    sky.menu.add("View/Named Views", { label: "Named Views…", act: "vx-wb-views", hint: "show · rename · delete", fn: openList });
    for (const [k, lbl] of Object.entries(LAYOUTS))
      sky.menu.add("View/Windows", { label: lbl, act: `vx-wb-win-${k}`, radio: () => WIN.layout === k, fn: () => setLayout(k) });
  }
  sky.wb.views = { capture, apply, save, show, remove, rename, list: views, openSave, openList };
  sky.wb.windows = { setLayout, layout: () => WIN.layout, panes: () => WIN.panes, pane: i => WIN.panes[i], refresh: () => schedule(true), LAYOUTS };
}
