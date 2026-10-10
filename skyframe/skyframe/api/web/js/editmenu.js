/* SkyFrame — ETABS Edit + Select menus (CONTRACT "Edit and Select utilities").

   Edit:   Undo / Redo (model history, js/history.js), Copy / Paste (offset
           dialog), Replicate (linear / radial / mirror / story), Divide Frames,
           Merge Joints, Align Points / Trim-Extend, Move, Extrude, Join Frames,
           Delete (with dependent cleanup).
   Select: Select All, Invert Selection, by Property, by Story, by Plane,
           Previous Selection.
   Live mode posts {model, selection, params} to POST /api/edit/<op> and adopts
   the echoed model; ?mock=1 runs the same algorithms locally (js/edit_geom.js
   via js/mock_edit.js). Each dialog draws a dashed "ghost" preview of the
   result in the plan / elevation editors. Every length goes through units.js;
   the model store stays SI. Installs window.__sky.edit / .selectx / .history. */

import { dialog as emDialog, btn as emBtn, footBar as emFootBar, errorLine as emErrorLine,
  showError as emShowError, closeDialog as emCloseDialog } from "./analysisdlg.js";
import emU from "./units.js";
import { springKey as emSpringKey, lineSpringKey as emLineSpringKey,
  eraseElement as emEraseElement, normalizeModel as emNormalizeModel } from "./modeledit.js";
import { replicateXforms as emReplicateXforms, dividePlan as emDividePlan, alignTarget as emAlignTarget,
  trimExtendPlan as emTrimExtendPlan, selectionJoints as emSelectionJoints, pkey as emPkey,
  translation as emTranslation, autoKind as emAutoKind, normalizeSelection as emNormalizeSelection,
  DEFAULT_MERGE_TOL as emDefaultMergeTol } from "./edit_geom.js";
import { mockEdit as emMockEdit } from "./mock_edit.js";
import { createHistory as emCreateHistory } from "./history.js";

const SVGNS = "http://www.w3.org/2000/svg";
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const OP_LABEL = { replicate: "Replicate", divide: "Divide frames", merge_joints: "Merge joints",
  align: "Align", move: "Move", extrude: "Extrude", join: "Join frames", delete: "Delete" };

export function initEditMenu(sky) {
  const S = sky.store;
  const $ = id => document.getElementById(id);
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);

  if (!document.querySelector("link[data-edit-css]")) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/static/editmenu.css"; l.setAttribute("data-edit-css", "1");
    document.head.appendChild(l);
  }

  const history = emCreateHistory(sky);
  sky.history = history;

  /* ---------------------------------------------------------------- selection */
  /** store.selection → backend selection (springs become points). */
  function backendSel(refs = S.selection || []) {
    const m = S.model;
    const out = { members: [], shells: [], links: [], points: [] };
    for (const r of refs) {
      if (r.type === "member") out.members.push(r.uid);
      else if (r.type === "shell") out.shells.push(r.uid);
      else if (r.type === "link") out.links.push(r.uid);
      else if (r.type === "spring") {
        const sp = (m.spring_supports || []).find(s => emSpringKey(s.point) === r.uid);
        if (sp) out.points.push(sp.point.map(Number));
      }
    }
    return out;
  }
  function refsFrom(sel) {
    const refs = [];
    for (const u of sel.members || []) refs.push({ type: "member", uid: u });
    for (const u of sel.shells || []) refs.push({ type: "shell", uid: u });
    for (const u of sel.links || []) refs.push({ type: "link", uid: u });
    const sk = new Set((S.model.spring_supports || []).map(s => emSpringKey(s.point)));
    for (const p of sel.points || []) { const k = emSpringKey(p); if (sk.has(k)) refs.push({ type: "spring", uid: k }); }
    return refs;
  }
  const selCount = () => (S.selection || []).length;

  // previous-selection tracking (any click / command that changes the selection)
  let curSig = JSON.stringify(S.selection || []), prevSel = [];
  function trackSel() {
    const sig = JSON.stringify(S.selection || []);
    if (sig !== curSig) { prevSel = JSON.parse(curSig); curSig = sig; sky.__histSel = JSON.parse(sig); }
  }
  for (const id of ["planSvg", "elevSvg"]) {
    const svg = $(id);
    if (!svg) continue;
    svg.addEventListener("pointerdown", trackSel, true);
    svg.addEventListener("pointerup", () => setTimeout(trackSel, 0));
  }
  document.addEventListener("keyup", () => setTimeout(trackSel, 0));
  function setSelection(refs) {
    trackSel();
    if (S.mode !== "model") sky.setMode("model");
    sky.handleSelect(refs, false);
    trackSel();
    const ex = sky.etabs;
    if (ex && ex.refresh) ex.refresh();
  }

  /* ---------------------------------------------------------------- run an op */
  async function api(path, body) {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
    return data;
  }

  function refreshAfterEdit() {
    S.modelEdited = true;
    try { sky.rebuildStorySelect && sky.rebuildStorySelect(); } catch (e) { console.error(e); }
    if (sky.planEditor) sky.planEditor.refresh();
    if (sky.elevEditor) sky.elevEditor.refresh();
    sky.renderProps && sky.renderProps();
    if (S.mode === "analyze" && sky.viewer) { sky.viewer.setModel(S.model); S.modelEdited = false; }
    if (S.mode === "loads" && sky.loadsEditor) sky.loadsEditor.render();
  }

  /** Run edit op → adopt the model, record one undo step, select the result. */
  async function runOp(op, params, opts = {}) {
    if (!S.model) throw new Error("No model loaded");
    const sel = opts.sel || backendSel();
    let res;
    if (S.mock) res = emMockEdit(S.model, op, sel, params);
    else {
      const payload = JSON.parse(JSON.stringify(S.model));
      delete payload._mock_params;
      res = await api(`/api/edit/${op}`, { model: payload, selection: sel, params });
    }
    const md = S.mock ? res.model : (sky.keepSetup ? sky.keepSetup(res.model) : res.model);
    history.label(opts.label || OP_LABEL[op] || op);
    trackSel();
    S.model = emNormalizeModel(md);
    const ns = res.summary && res.summary.new_selection;
    const nrefs = ns ? refsFrom(ns) : [];
    S.selection = opts.keepSelection ? (S.selection || []).filter(r => r.type !== "member" && r.type !== "shell" && r.type !== "link"
      || (S.model[r.type + "s"] || []).some(o => o.uid === r.uid)) : nrefs;
    sky.markDirty();                     // → history checkpoint (labelled) + chrome refresh
    refreshAfterEdit();
    trackSel();
    return res.summary;
  }

  function summaryText(sm) {
    const c = sm.created || {}, d = sm.deleted || {}, md = sm.modified || {};
    const n = o => (o.members || []).length + (o.shells || []).length + (o.links || []).length;
    const parts = [];
    if (n(c)) parts.push(`${n(c)} created`);
    if (n(md)) parts.push(`${n(md)} modified`);
    if (n(d)) parts.push(`${n(d)} deleted`);
    if (sm.merged_count !== undefined) parts.push(`${sm.merged_count} joint${sm.merged_count === 1 ? "" : "s"} merged`);
    if (sm.joined) parts.push(`${sm.joined.length} join${sm.joined.length === 1 ? "" : "s"}`);
    if (sm.point_records_deleted) parts.push(`${sm.point_records_deleted} joint record(s) removed`);
    return (parts.join(" · ") || "no change") + (S.mock ? " (mock)" : "");
  }
  function report(title, sm) {
    toast(title, summaryText(sm), "info", 5000);
    for (const w of (sm.warnings || []).slice(0, 3)) toast(title, w, "warn", 7000);
  }

  /* ---------------------------------------------------------------- ghost preview */
  const ghost = {
    nodes: [],
    clear() { for (const n of this.nodes) n.remove(); this.nodes = []; },
    /** segs: [[p,q]], polys: [[p..]], pts: [p] — SI 3D points. */
    show({ segs = [], polys = [], pts = [] } = {}) {
      this.clear();
      const pe = sky.planEditor, ee = sky.elevEditor;
      const views = [];
      if (pe && pe.gWorld) views.push({ g: pe.gWorld, map: p => [p[0], p[1]] });
      if (ee && ee.gWorld && ee.plane && ee.plane()) views.push({ g: ee.gWorld, map: p => [ee.sOf(p), p[2]] });
      for (const v of views) {
        const g = document.createElementNS(SVGNS, "g");
        g.setAttribute("class", "edit-ghost");
        g.setAttribute("data-edit-ghost", "1");
        for (const poly of polys) {
          const e = document.createElementNS(SVGNS, "polygon");
          e.setAttribute("points", poly.map(p => v.map(p).join(",")).join(" "));
          e.setAttribute("class", "edit-ghost-poly");
          e.setAttribute("vector-effect", "non-scaling-stroke");
          g.appendChild(e);
        }
        for (const [a, b] of segs) {
          const [x1, y1] = v.map(a), [x2, y2] = v.map(b);
          const e = document.createElementNS(SVGNS, "line");
          Object.entries({ x1, y1, x2, y2 }).forEach(([k, val]) => e.setAttribute(k, val));
          e.setAttribute("class", "edit-ghost-line");
          e.setAttribute("vector-effect", "non-scaling-stroke");
          g.appendChild(e);
        }
        const scale = (v.g === (pe && pe.gWorld) ? pe.scale : ee.scale) || 40;
        for (const p of pts) {
          const [x, y] = v.map(p);
          const e = document.createElementNS(SVGNS, "circle");
          e.setAttribute("cx", x); e.setAttribute("cy", y); e.setAttribute("r", 4 / scale);
          e.setAttribute("class", "edit-ghost-pt");
          e.setAttribute("vector-effect", "non-scaling-stroke");
          g.appendChild(e);
        }
        v.g.appendChild(g);
        this.nodes.push(g);
      }
      return { segs: segs.length, polys: polys.length, pts: pts.length };
    },
  };

  /** Geometry of a backend selection: segs (members, links), polys (shells), pts. */
  function selGeom(sel) {
    const m = S.model;
    const segs = [], polys = [], pts = [...(sel.points || [])];
    for (const u of sel.members || []) { const x = m.members.find(o => o.uid === u); if (x) segs.push([x.pi, x.pj]); }
    for (const u of sel.links || []) { const x = (m.links || []).find(o => o.uid === u); if (x) segs.push([x.pi, x.pj]); }
    for (const u of sel.shells || []) { const x = m.shells.find(o => o.uid === u); if (x) polys.push(x.corners); }
    return { segs, polys, pts };
  }
  const xformGeom = (g, xf) => ({
    segs: g.segs.map(([a, b]) => [xf.p(a), xf.p(b)]),
    polys: g.polys.map(c => c.map(p => xf.p(p))),
    pts: g.pts.map(p => xf.p(p)),
  });
  const mergeGeom = list => list.reduce((acc, g) => ({ segs: acc.segs.concat(g.segs), polys: acc.polys.concat(g.polys),
    pts: acc.pts.concat(g.pts) }), { segs: [], polys: [], pts: [] });

  /* ---------------------------------------------------------------- dialog kit */
  const LEN = "length";
  function field(label, kind, si, attrs = {}) {
    const row = document.createElement("label");
    row.className = "ed-field";
    const unit = kind === "deg" ? "deg" : kind === "count" ? "" : emU.label(kind);
    row.innerHTML = `<span class="ed-lbl">${esc(label)}</span>`;
    const inp = document.createElement("input");
    inp.type = "number"; inp.step = "any";
    inp.value = (kind === "deg" || kind === "count") ? String(si) : emU.inputValue(kind, si);
    for (const [k, v] of Object.entries(attrs)) inp.setAttribute(k, v);
    row.appendChild(inp);
    const u = document.createElement("span");
    u.className = "ed-unit"; u.textContent = unit;
    row.appendChild(u);
    const get = () => {
      if (kind === "deg" || kind === "count") { const v = parseFloat(inp.value); return isFinite(v) ? v : NaN; }
      return emU.parse(kind, inp.value);
    };
    return { row, inp, get };
  }
  function selectField(label, options, value) {
    const row = document.createElement("label");
    row.className = "ed-field";
    row.innerHTML = `<span class="ed-lbl">${esc(label)}</span>`;
    const s = document.createElement("select");
    for (const [v, t] of options) {
      const o = document.createElement("option");
      o.value = v; o.textContent = t;
      s.appendChild(o);
    }
    s.value = value;
    row.appendChild(s);
    return { row, sel: s, get: () => s.value };
  }
  function check(label, on) {
    const l = document.createElement("label");
    l.className = "dlg-chk";
    const c = document.createElement("input");
    c.type = "checkbox"; c.checked = !!on;
    const t = document.createElement("span"); t.textContent = label;
    l.append(c, t);
    return { row: l, inp: c, get: () => c.checked };
  }
  function group(title, kids) {
    const fs = document.createElement("fieldset");
    fs.className = "dlg-group ed-group";
    const lg = document.createElement("legend"); lg.textContent = title;
    fs.appendChild(lg);
    for (const k of kids) if (k) fs.appendChild(k.row || k);
    return fs;
  }
  function radioTabs(name, options, value, onChange) {
    const wrap = document.createElement("div");
    wrap.className = "ed-tabs";
    wrap.setAttribute("role", "tablist");
    const btns = {};
    for (const [v, t] of options) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "ed-tab"; b.textContent = t; b.dataset.mode = v;
      b.addEventListener("click", () => { set(v); onChange(v); });
      btns[v] = b;
      wrap.appendChild(b);
    }
    let cur = value;
    const set = v => { cur = v; for (const [k, b] of Object.entries(btns)) b.classList.toggle("is-on", k === v); };
    set(value);
    return { row: wrap, get: () => cur, set };
  }
  const reqNum = (v, what) => { if (!isFinite(v)) throw new Error(`${what} must be a number`); return v; };

  /** Standard edit dialog: body + preview + OK/Apply/Cancel → runOp. */
  function editDialog(id, title, body, { build, preview, ok, okLabel = "OK", note = "" }) {
    const err = emErrorLine();
    const pv = document.createElement("p");
    pv.className = "muted ed-preview-note";
    body.append(pv, err);
    let busy = false;
    const doPreview = () => {
      emShowError(err, "");
      try {
        const params = build();
        const g = preview ? preview(params) : null;
        if (g) {
          const n = ghost.show(g);
          pv.textContent = `Preview: ${n.segs} frame/link${n.segs === 1 ? "" : "s"}, ${n.polys} shell${n.polys === 1 ? "" : "s"}` +
            (n.pts ? `, ${n.pts} point${n.pts === 1 ? "" : "s"}` : "") + " (dashed)";
        } else { ghost.clear(); pv.textContent = ""; }
      } catch (e) { ghost.clear(); pv.textContent = ""; emShowError(err, e.message); }
    };
    const run = async close => {
      if (busy) return;
      busy = true;
      try {
        const params = build();
        const sm = await ok(params);
        if (close) d.close(); else doPreview();
        return sm;
      } catch (e) { emShowError(err, e.message); }
      finally { busy = false; }
    };
    const okB = emBtn(okLabel, "btn-primary", () => run(true));
    okB.dataset.edOk = "1";
    const apB = emBtn("Apply", "", () => run(false));
    apB.dataset.edApply = "1";
    const { wrap } = emFootBar(note, [emBtn("Cancel", "", () => d.close()), apB, okB]);
    const d = emDialog(id, { title, iconId: null, body, foot: wrap, onClose: () => ghost.clear() });
    body.addEventListener("input", doPreview);
    body.addEventListener("change", doPreview);
    body.addEventListener("click", e => { if (e.target.closest(".ed-tab")) doPreview(); });
    doPreview();
    return { d, doPreview, err };
  }
  const needSel = (what = "objects") => {
    if (selCount()) return true;
    toast("Edit", `Select ${what} first (Draw > Select, then click or box-select).`);
    return false;
  };

  /* ================================================================ Replicate */
  function openReplicate() {
    if (!needSel()) return null;
    const sel = backendSel();
    const m = S.model;
    const body = document.createElement("div");
    body.className = "ed-body";
    const tabs = radioTabs("rep", [["linear", "Linear"], ["radial", "Radial"], ["mirror", "Mirror"], ["story", "Story"]], "linear", show);
    body.appendChild(tabs.row);
    // linear
    const dx = field("dx", LEN, 0), dy = field("dy", LEN, 0), dz = field("dz", LEN, 0);
    const n = field("Number", "count", 1, { min: 1, step: 1 });
    const gLin = group("Increments", [dx, dy, dz, n]);
    // radial
    const cx = field("Center x", LEN, 0), cy = field("Center y", LEN, 0);
    const axis = selectField("Rotate about", [["z", "Z axis"], ["x", "X axis"], ["y", "Y axis"]], "z");
    const ang = field("Angle", "deg", 90), nr = field("Number", "count", 1, { min: 1, step: 1 });
    const gRad = group("Rotation", [axis, cx, cy, ang, nr]);
    // mirror
    const mplane = selectField("Mirror plane", [["x", "Parallel to Y-Z (x = const)"], ["y", "Parallel to X-Z (y = const)"],
      ["z", "Parallel to X-Y (z = const)"], ["line", "Vertical plane through plan line"]], "x");
    const mc = field("Coordinate", LEN, 0);
    const lx1 = field("Line x1", LEN, 0), ly1 = field("Line y1", LEN, 0), lx2 = field("Line x2", LEN, 0), ly2 = field("Line y2", LEN, 1);
    const gMir = group("Mirror", [mplane, mc, lx1, ly1, lx2, ly2]);
    // story
    const stWrap = document.createElement("div");
    stWrap.className = "ed-list";
    const stChecks = [...(m.stories || [])].reverse().map(s => {
      const c = check(`${s.name}  (z = ${emU.fmtU(LEN, s.elevation, 2)})`, false);
      c.inp.dataset.story = s.name;
      stWrap.appendChild(c.row);
      return c;
    });
    const gSt = group("Replicate to stories", [stWrap]);
    const asg = check("Replicate assignments (releases, offsets, hinges, groups …)", true);
    const lds = check("Replicate loads (frame / shell loads, joint loads at selected springs)", true);
    const gOpt = group("Options", [asg, lds]);
    body.append(gLin, gRad, gMir, gSt, gOpt);
    function show(mode) {
      gLin.classList.toggle("hidden", mode !== "linear");
      gRad.classList.toggle("hidden", mode !== "radial");
      gMir.classList.toggle("hidden", mode !== "mirror");
      gSt.classList.toggle("hidden", mode !== "story");
      const line = mplane.get() === "line";
      mc.row.classList.toggle("hidden", line);
      for (const f of [lx1, ly1, lx2, ly2]) f.row.classList.toggle("hidden", !line);
    }
    mplane.sel.addEventListener("change", () => show(tabs.get()));
    show("linear");
    const build = () => {
      const mode = tabs.get();
      const p = { mode, assignments: asg.get(), loads: lds.get() };
      if (mode === "linear") {
        p.dx = reqNum(dx.get(), "dx"); p.dy = reqNum(dy.get(), "dy"); p.dz = reqNum(dz.get(), "dz");
        p.n = reqNum(n.get(), "Number");
      } else if (mode === "radial") {
        p.center = [reqNum(cx.get(), "Center x"), reqNum(cy.get(), "Center y"), 0];
        p.axis = axis.get(); p.angle = reqNum(ang.get(), "Angle"); p.n = reqNum(nr.get(), "Number");
      } else if (mode === "mirror") {
        if (mplane.get() === "line") {
          p.p1 = [reqNum(lx1.get(), "x1"), reqNum(ly1.get(), "y1")];
          p.p2 = [reqNum(lx2.get(), "x2"), reqNum(ly2.get(), "y2")];
        } else { p.plane = mplane.get(); p.coord = reqNum(mc.get(), "Coordinate"); }
      } else {
        p.stories = stChecks.filter(c => c.get()).map(c => c.inp.dataset.story);
        if (!p.stories.length) throw new Error("Pick at least one target story");
      }
      return p;
    };
    const preview = p => {
      const g = selGeom(sel);
      if (p.mode === "story") {
        const elev = Object.fromEntries((m.stories || []).map(s => [s.name, +s.elevation]));
        const parts = [];
        for (const t of p.stories) {
          const per = { segs: [], polys: [], pts: [] };
          for (const u of sel.members) {
            const x = m.members.find(o => o.uid === u);
            if (x && x.story in elev && x.story !== t) { const xf = emTranslation(0, 0, elev[t] - elev[x.story]); per.segs.push([xf.p(x.pi), xf.p(x.pj)]); }
          }
          for (const u of sel.shells) {
            const x = m.shells.find(o => o.uid === u);
            if (x && x.story in elev && x.story !== t) { const xf = emTranslation(0, 0, elev[t] - elev[x.story]); per.polys.push(x.corners.map(q => xf.p(q))); }
          }
          parts.push(per);
        }
        return mergeGeom(parts);
      }
      return mergeGeom(emReplicateXforms(m, p).map(xf => xformGeom(g, xf)));
    };
    return editDialog("edReplicateDlg", "Replicate", body, {
      build, preview, note: `${selCount()} object(s) selected`,
      ok: async p => { const sm = await runOp("replicate", p, { sel, label: `Replicate (${p.mode})` }); report("Replicate", sm); return sm; },
    });
  }

  /* ================================================================ Divide */
  function openDivide() {
    const sel = backendSel();
    if (!sel.members.length) { toast("Divide Frames", "Select one or more frame members first."); return null; }
    const body = document.createElement("div");
    body.className = "ed-body";
    const tabs = radioTabs("div", [["n", "Into n equal"], ["intersections", "At intersections"], ["distance", "At a distance"]], "n", show);
    const n = field("Number of pieces", "count", 2, { min: 2, step: 1 });
    const dd = field("Distance", LEN, 1);
    const from = selectField("Measured from", [["i", "End I"], ["j", "End J"]], "i");
    const tol = field("Tolerance", LEN, 0.001);
    const gN = group("Divide into", [n]), gD = group("Break at distance", [dd, from]),
      gI = group("Break at intersections with other frames / joints", [tol]);
    body.append(tabs.row, gN, gI, gD);
    function show(mode) {
      gN.classList.toggle("hidden", mode !== "n");
      gD.classList.toggle("hidden", mode !== "distance");
      gI.classList.toggle("hidden", mode !== "intersections");
    }
    show("n");
    const build = () => {
      const mode = tabs.get();
      const p = { mode };
      if (mode === "n") p.n = reqNum(n.get(), "Number");
      else if (mode === "distance") { p.distance = reqNum(dd.get(), "Distance"); p.from = from.get(); }
      else p.tol = reqNum(tol.get(), "Tolerance");
      return p;
    };
    const preview = p => {
      const plan = emDividePlan(S.model, emNormalizeSelection(S.model, sel), p);
      const pts = [];
      for (const [u, ts] of Object.entries(plan)) {
        const x = S.model.members.find(o => o.uid === u);
        for (const t of ts) pts.push([0, 1, 2].map(k => x.pi[k] + (x.pj[k] - x.pi[k]) * t));
      }
      return { pts };
    };
    return editDialog("edDivideDlg", "Divide Frames", body, {
      build, preview, note: `${sel.members.length} frame(s) selected`,
      ok: async p => { const sm = await runOp("divide", p, { sel }); report("Divide Frames", sm); return sm; },
    });
  }

  /* ================================================================ Merge joints */
  function openMerge() {
    const sel = backendSel();
    const body = document.createElement("div");
    body.className = "ed-body";
    const tol = field("Merge tolerance", LEN, emDefaultMergeTol);
    const scope = selectField("Scope", [["sel", "Joints of the selected objects"], ["all", "All joints in the model"]],
      selCount() ? "sel" : "all");
    body.append(group("Merge Joints", [tol, scope]));
    const intro = document.createElement("p");
    intro.className = "muted dlg-intro";
    intro.textContent = "Joints closer than the tolerance are merged into one (a supported joint wins); " +
      "zero-length frames and duplicate objects created by the merge are deleted with their loads.";
    body.prepend(intro);
    const build = () => ({ tolerance: reqNum(tol.get(), "Tolerance") });
    return editDialog("edMergeDlg", "Merge Joints", body, {
      build, preview: null,
      ok: async p => {
        const s = scope.get() === "all" ? { members: [], shells: [], links: [], points: [] } : sel;
        const sm = await runOp("merge_joints", p, { sel: s, keepSelection: true });
        report("Merge Joints", sm);
        for (const c of (sm.merged || []).slice(0, 4))
          toast("Merged", `${c.from.length} → (${c.to.map(v => emU.fmt(LEN, v, 3)).join(", ")}) ${emU.label(LEN)}`, "info", 6000);
        return sm;
      },
    });
  }

  /* ================================================================ Align */
  function openAlign() {
    if (!needSel()) return null;
    const sel = backendSel();
    const body = document.createElement("div");
    body.className = "ed-body";
    const tabs = radioTabs("aln", [["coordinate", "To ordinate"], ["line", "To line"], ["plane", "To plane"],
      ["trim_extend", "Trim / Extend frames"]], "coordinate", show);
    const axis = selectField("Ordinate", [["x", "X"], ["y", "Y"], ["z", "Z"]], "z");
    const j0 = emSelectionJoints(S.model, sel)[0] || [0, 0, 0];
    const val = field("Value", LEN, j0[2]);
    const P = (lbl, v) => [field(`${lbl} x`, LEN, v[0]), field(`${lbl} y`, LEN, v[1]), field(`${lbl} z`, LEN, v[2])];
    const p1 = P("Point 1", j0), p2 = P("Point 2", [j0[0] + 1, j0[1], j0[2]]);
    const po = P("Point", j0), nn = [field("Normal x", "count", 0), field("Normal y", "count", 0), field("Normal z", "count", 1)];
    const gC = group("Align points to an ordinate", [axis, val]);
    const gL = group("Line (P1 → P2)", [...p1, ...p2]);
    const gP = group("Plane (point + normal)", [...po, ...nn]);
    const tip = document.createElement("p");
    tip.className = "muted dlg-intro";
    body.append(tabs.row, tip, gC, gL, gP);
    function show(mode) {
      gC.classList.toggle("hidden", mode !== "coordinate");
      gL.classList.toggle("hidden", mode !== "line" && mode !== "trim_extend");
      gP.classList.toggle("hidden", mode !== "plane");
      tip.textContent = mode === "trim_extend"
        ? "Each selected frame's nearer end is moved to its intersection with the line (frames are trimmed or extended)."
        : "The joints of the selection move onto the target; connected objects stretch with them.";
    }
    show("coordinate");
    const v3 = f => f.map((x, i) => reqNum(x.get(), "xyz"[i]));
    const build = () => {
      const mode = tabs.get();
      if (mode === "coordinate") return { mode, axis: axis.get(), value: reqNum(val.get(), "Value") };
      if (mode === "plane") return { mode, point: v3(po), normal: v3(nn) };
      return { mode, p1: v3(p1), p2: v3(p2) };
    };
    const preview = p => {
      const m = S.model;
      const ns = emNormalizeSelection(m, sel);
      if (p.mode === "trim_extend") {
        const plan = emTrimExtendPlan(m, ns, p);
        const segs = [[p.p1, p.p2]];
        for (const [u, { end, point }] of Object.entries(plan)) {
          const x = m.members.find(o => o.uid === u);
          segs.push(end === "pi" ? [point, x.pj] : [x.pi, point]);
        }
        return { segs };
      }
      const f = emAlignTarget(p);
      const keys = new Set(emSelectionJoints(m, ns).map(emPkey));
      const mp = q => (keys.has(emPkey(q)) ? f(q) : q);
      const segs = [];
      for (const x of m.members) if (keys.has(emPkey(x.pi)) || keys.has(emPkey(x.pj))) segs.push([mp(x.pi), mp(x.pj)]);
      return { segs, pts: [...keys].map(k => f(k.split(",").map(Number))) };
    };
    return editDialog("edAlignDlg", "Align Points / Trim-Extend Frames", body, {
      build, preview,
      ok: async p => { const sm = await runOp("align", p, { sel, keepSelection: true, label: p.mode === "trim_extend" ? "Trim / Extend" : "Align points" }); report("Align", sm); return sm; },
    });
  }

  /* ================================================================ Move */
  function openMove() {
    if (!needSel()) return null;
    const sel = backendSel();
    const body = document.createElement("div");
    body.className = "ed-body";
    const dx = field("dx", LEN, 0), dy = field("dy", LEN, 0), dz = field("dz", LEN, 0);
    body.append(group("Move by", [dx, dy, dz]));
    const tip = document.createElement("p");
    tip.className = "muted dlg-intro";
    tip.textContent = "The selected objects' joints move; unselected objects connected to them stretch (ETABS behavior). " +
      "Supports, springs and joint loads at those joints move along.";
    body.prepend(tip);
    const build = () => ({ dx: reqNum(dx.get(), "dx"), dy: reqNum(dy.get(), "dy"), dz: reqNum(dz.get(), "dz") });
    const preview = p => xformGeom(selGeom(sel), emTranslation(p.dx, p.dy, p.dz));
    return editDialog("edMoveDlg", "Move", body, {
      build, preview, note: `${selCount()} object(s) selected`,
      ok: async p => { const sm = await runOp("move", p, { sel }); report("Move", sm); return sm; },
    });
  }

  /* ================================================================ Extrude */
  function parsePoints(text) {
    const pts = [];
    for (const line of String(text).split(/[\n;]+/)) {
      const t = line.trim();
      if (!t) continue;
      const v = t.split(/[\s,]+/).filter(Boolean).map(x => emU.parse(LEN, x));
      if (v.length !== 3 || !v.every(isFinite)) throw new Error(`Bad point "${t}" (x, y, z)`);
      pts.push(v);
    }
    return pts;
  }
  function openExtrude() {
    const sel = backendSel();
    const m = S.model;
    const body = document.createElement("div");
    body.className = "ed-body";
    const tabs = radioTabs("ext", [["points_to_frames", "Points → Frames"], ["frames_to_shells", "Frames → Shells"]],
      sel.members.length && !sel.points.length ? "frames_to_shells" : "points_to_frames", show);
    const dx = field("dx", LEN, 0), dy = field("dy", LEN, 0), dz = field("dz", LEN, (m.stories[0] || {}).height || 3);
    const n = field("Number", "count", 1, { min: 1, step: 1 });
    const ptsTa = document.createElement("textarea");
    ptsTa.className = "ed-points"; ptsTa.rows = 4;
    const joints = sel.points.length ? sel.points : emSelectionJoints(m, sel);
    ptsTa.value = joints.map(p => p.map(v => emU.inputValue(LEN, v)).join(", ")).join("\n");
    const ptsRow = document.createElement("label");
    ptsRow.className = "ed-field ed-field-col";
    ptsRow.innerHTML = `<span class="ed-lbl">Points (x, y, z per line, ${esc(emU.label(LEN))})</span>`;
    ptsRow.appendChild(ptsTa);
    const fkind = selectField("Frame type", [["", "Automatic"], ["column", "Column"], ["beam", "Beam"], ["brace", "Brace"]], "");
    const fsec = selectField("Frame section", [["", "Default"], ...Object.keys(m.sections || {}).map(s => [s, s])], "");
    const skind = selectField("Shell type", [["", "Automatic"], ["wall", "Wall"], ["slab", "Slab"]], "");
    const ssec = selectField("Shell section", Object.keys(m.shell_sections || {}).map(s => [s, s]), Object.keys(m.shell_sections || {})[0] || "");
    const beh = selectField("Behavior", [["shell", "Shell (meshed)"], ["membrane", "Membrane (slabs)"]], "shell");
    const del = check("Delete source frames", false);
    const gPts = group("Points → Frames", [ptsRow, fkind, fsec]);
    const gSh = group("Frames → Shells", [skind, ssec, beh, del]);
    body.append(tabs.row, group("Extrusion", [dx, dy, dz, n]), gPts, gSh);
    function show(mode) {
      gPts.classList.toggle("hidden", mode !== "points_to_frames");
      gSh.classList.toggle("hidden", mode !== "frames_to_shells");
    }
    show(tabs.get());
    let pointsSel = null;
    const build = () => {
      const mode = tabs.get();
      const p = { mode, dx: reqNum(dx.get(), "dx"), dy: reqNum(dy.get(), "dy"), dz: reqNum(dz.get(), "dz"), n: reqNum(n.get(), "Number") };
      if (mode === "points_to_frames") {
        pointsSel = parsePoints(ptsTa.value);
        if (!pointsSel.length) throw new Error("Enter at least one point");
        if (fkind.get()) p.kind = fkind.get();
        if (fsec.get()) p.section = fsec.get();
      } else {
        if (!sel.members.length) throw new Error("Select frame members to extrude");
        if (!Object.keys(m.shell_sections || {}).length) throw new Error("Define a shell section first (Define > Shell Sections)");
        if (skind.get()) p.kind = skind.get();
        if (ssec.get()) p.section = ssec.get();
        p.behavior = beh.get();
        if (del.get()) p.delete_source = true;
      }
      return p;
    };
    const preview = p => {
      const d = [p.dx, p.dy, p.dz];
      const segs = [], polys = [];
      if (p.mode === "points_to_frames") {
        for (const q of pointsSel) for (let k = 0; k < p.n; k++)
          segs.push([q.map((v, i) => v + d[i] * k), q.map((v, i) => v + d[i] * (k + 1))]);
      } else {
        for (const u of sel.members) {
          const x = m.members.find(o => o.uid === u);
          for (let k = 0; k < p.n; k++) {
            const s = (q, f) => q.map((v, i) => v + d[i] * f);
            polys.push([s(x.pi, k), s(x.pj, k), s(x.pj, k + 1), s(x.pi, k + 1)]);
          }
        }
      }
      return { segs, polys };
    };
    return editDialog("edExtrudeDlg", "Extrude", body, {
      build, preview,
      ok: async p => {
        const s = p.mode === "points_to_frames" ? { members: [], shells: [], links: [], points: pointsSel } : sel;
        const sm = await runOp("extrude", p, { sel: s, label: p.mode === "points_to_frames" ? "Extrude points to frames" : "Extrude frames to shells" });
        report("Extrude", sm);
        return sm;
      },
    });
  }

  /* ================================================================ Join / Delete */
  async function joinFrames() {
    const sel = backendSel();
    if (sel.members.length < 2) { toast("Join Frames", "Select two or more collinear frame members."); return null; }
    try {
      const sm = await runOp("join", {}, { sel });
      report("Join Frames", sm);
      for (const s of (sm.skipped || []).slice(0, 3))
        toast("Join skipped", `at (${s.joint.map(v => emU.fmt(LEN, v, 2)).join(", ")}): ${s.reason}`, "warn", 6000);
      return sm;
    } catch (e) { toast("Join Frames failed", e.message, "error", 7000); return null; }
  }
  async function deleteSel() {
    if (!selCount()) { toast("Delete", "Nothing selected."); return null; }
    const lineSprings = (S.selection || []).filter(r => r.type === "linespring");
    const sel = backendSel();
    try {
      let sm = null;
      if (sel.members.length || sel.shells.length || sel.links.length || sel.points.length) {
        if (lineSprings.length) history.label("Delete");
        sm = await runOp("delete", {}, { sel, label: "Delete" });
      }
      if (lineSprings.length) {                    // line springs: no joint identity → local erase
        history.label("Delete");
        for (const r of lineSprings) emEraseElement(S.model, r);
        S.selection = [];
        sky.markDirty();
        refreshAfterEdit();
      }
      toast("Delete", sm ? summaryText(sm) : `${lineSprings.length} line spring(s) deleted`, "info", 4000);
      return sm;
    } catch (e) { toast("Delete failed", e.message, "error", 7000); return null; }
  }

  /* ================================================================ Copy / Paste */
  let clipboard = null;
  let lastPaste = null;
  function copySel() {
    if (!selCount()) { toast("Copy", "Nothing selected."); return false; }
    const sel = backendSel();
    if (!(sel.members.length || sel.shells.length || sel.links.length || sel.points.length)) {
      toast("Copy", "Only frames, shells, links and point springs can be copied."); return false;
    }
    clipboard = sel;
    toast("Copied", `${sel.members.length + sel.shells.length + sel.links.length + sel.points.length} object(s) — Edit > Paste to place a copy`, "info", 3500);
    return true;
  }
  function liveClipboard() {
    if (!clipboard) return null;
    const m = S.model;
    const has = k => new Set((m[k] || []).map(o => o.uid));
    const hm = has("members"), hs = has("shells"), hl = has("links");
    const sk = new Set((m.spring_supports || []).map(s => emPkey(s.point)));
    const c = { members: clipboard.members.filter(u => hm.has(u)), shells: clipboard.shells.filter(u => hs.has(u)),
      links: clipboard.links.filter(u => hl.has(u)), points: clipboard.points.filter(p => sk.has(emPkey(p))) };
    return (c.members.length + c.shells.length + c.links.length + c.points.length) ? c : null;
  }
  function openPaste() {
    const sel = liveClipboard();
    if (!sel) { toast("Paste", clipboard ? "The copied objects no longer exist." : "Nothing copied (Edit > Copy)."); return null; }
    const body = document.createElement("div");
    body.className = "ed-body";
    const o = lastPaste || { dx: 0, dy: 0, dz: 0 };
    const dx = field("dx", LEN, o.dx), dy = field("dy", LEN, o.dy), dz = field("dz", LEN, o.dz);
    const lds = check("Paste loads", true);
    body.append(group("Paste offset", [dx, dy, dz]), group("Options", [lds]));
    const build = () => ({ mode: "linear", n: 1, dx: reqNum(dx.get(), "dx"), dy: reqNum(dy.get(), "dy"), dz: reqNum(dz.get(), "dz"), loads: lds.get() });
    const preview = p => xformGeom(selGeom(sel), emTranslation(p.dx, p.dy, p.dz));
    return editDialog("edPasteDlg", "Paste", body, {
      build, preview, okLabel: "Paste",
      ok: async p => {
        const sm = await runOp("replicate", p, { sel, label: "Paste" });
        lastPaste = { dx: p.dx, dy: p.dy, dz: p.dz };
        report("Paste", sm);
        return sm;
      },
    });
  }

  /* ================================================================ Select menu */
  function allRefs() {
    const m = S.model;
    return [
      ...m.members.map(x => ({ type: "member", uid: x.uid })),
      ...m.shells.map(x => ({ type: "shell", uid: x.uid })),
      ...(m.links || []).map(x => ({ type: "link", uid: x.uid })),
      ...(m.spring_supports || []).map(x => ({ type: "spring", uid: emSpringKey(x.point) })),
      ...(m.line_springs || []).map(x => ({ type: "linespring", uid: emLineSpringKey(x) })),
    ];
  }
  const refKey = r => `${r.type}:${r.uid}`;
  function selectAll() { if (!S.model) return 0; const r = allRefs(); setSelection(r); return r.length; }
  function invertSelection() {
    if (!S.model) return 0;
    const cur = new Set((S.selection || []).map(refKey));
    const r = allRefs().filter(x => !cur.has(refKey(x)));
    setSelection(r);
    return r.length;
  }
  function previousSelection() {
    trackSel();
    const p = prevSel.filter(r => allRefs().some(x => refKey(x) === refKey(r)));
    setSelection(p);
    return p.length;
  }
  function clearSelection() { setSelection([]); }

  /** refs of objects whose property matches. kind: frame_section|shell_section|material. */
  function refsByProperty(kind, names) {
    const m = S.model, set = new Set(names);
    const out = [];
    const secMat = s => ((m.sections || {})[s] || {}).material;
    const shMat = s => ((m.shell_sections || {})[s] || {}).material;
    for (const x of m.members) {
      if ((kind === "frame_section" && set.has(x.section)) || (kind === "material" && set.has(secMat(x.section))))
        out.push({ type: "member", uid: x.uid });
    }
    for (const x of m.shells) {
      if ((kind === "shell_section" && set.has(x.section)) || (kind === "material" && set.has(shMat(x.section))))
        out.push({ type: "shell", uid: x.uid });
    }
    return out;
  }
  function selectByProperty(kind, names, additive = false) {
    const r = refsByProperty(kind, names);
    if (additive) { const cur = new Map((S.selection || []).map(x => [refKey(x), x])); for (const x of r) cur.set(refKey(x), x); setSelection([...cur.values()]); }
    else setSelection(r);
    return r.length;
  }
  function storyOfLink(l) {
    const st = S.model.stories || [];
    const z = Math.max(l.pi[2], l.pj[2]);
    const hit = st.find(s => Math.abs(s.elevation - z) < 1e-6) || st.find(s => s.elevation - s.height < z && z < s.elevation);
    return hit ? hit.name : "";
  }
  function refsByStory(names) {
    const m = S.model, set = new Set(names);
    return [
      ...m.members.filter(x => set.has(x.story)).map(x => ({ type: "member", uid: x.uid })),
      ...m.shells.filter(x => set.has(x.story)).map(x => ({ type: "shell", uid: x.uid })),
      ...(m.links || []).filter(x => set.has(storyOfLink(x))).map(x => ({ type: "link", uid: x.uid })),
    ];
  }
  function selectByStory(names, additive = false) {
    const r = refsByStory(names);
    if (additive) { const cur = new Map((S.selection || []).map(x => [refKey(x), x])); for (const x of r) cur.set(refKey(x), x); setSelection([...cur.values()]); }
    else setSelection(r);
    return r.length;
  }
  /** plane: "xy" (z = c) | "xz" (y = c) | "yz" (x = c). Objects lying fully in it. */
  function refsByPlane(plane, c, tol = 1e-3) {
    const ax = { xy: 2, xz: 1, yz: 0 }[plane];
    if (ax === undefined) throw new Error("plane must be xy|xz|yz");
    const on = p => Math.abs(p[ax] - c) <= tol;
    const m = S.model;
    return [
      ...m.members.filter(x => on(x.pi) && on(x.pj)).map(x => ({ type: "member", uid: x.uid })),
      ...m.shells.filter(x => x.corners.every(on)).map(x => ({ type: "shell", uid: x.uid })),
      ...(m.links || []).filter(x => on(x.pi) && on(x.pj)).map(x => ({ type: "link", uid: x.uid })),
      ...(m.spring_supports || []).filter(x => on(x.point)).map(x => ({ type: "spring", uid: emSpringKey(x.point) })),
    ];
  }
  function selectByPlane(plane, c, additive = false) {
    const r = refsByPlane(plane, c);
    if (additive) { const cur = new Map((S.selection || []).map(x => [refKey(x), x])); for (const x of r) cur.set(refKey(x), x); setSelection([...cur.values()]); }
    else setSelection(r);
    return r.length;
  }

  function listDialog(id, title, intro, items, onOk, extra) {
    const body = document.createElement("div");
    body.className = "ed-body";
    if (intro) { const p = document.createElement("p"); p.className = "muted dlg-intro"; p.textContent = intro; body.appendChild(p); }
    if (extra) body.appendChild(extra.row);
    const list = document.createElement("div");
    list.className = "ed-list ed-list-tall";
    body.appendChild(list);
    let checks = [];
    const fill = its => {
      list.textContent = "";
      checks = its.map(([v, t]) => { const c = check(t, false); c.inp.dataset.value = v; list.appendChild(c.row); return c; });
      if (!its.length) list.innerHTML = `<p class="muted">Nothing to list.</p>`;
    };
    fill(items);
    const add = check("Add to the current selection", false);
    body.appendChild(add.row);
    const err = emErrorLine();
    body.appendChild(err);
    const okB = emBtn("Select", "btn-primary", () => {
      const vals = checks.filter(c => c.get()).map(c => c.inp.dataset.value);
      if (!vals.length) return emShowError(err, "Pick at least one entry.");
      const n = onOk(vals, add.get());
      d.close();
      toast(title, `${n} object(s) selected`, "info", 2500);
    });
    okB.dataset.edOk = "1";
    const allB = emBtn("All", "", () => checks.forEach(c => { c.inp.checked = true; }));
    const { wrap } = emFootBar("", [allB, emBtn("Cancel", "", () => d.close()), okB]);
    const d = emDialog(id, { title, body, foot: wrap });
    return { d, fill, checks: () => checks };
  }

  function openSelectByProperty() {
    const m = S.model;
    if (!m) return null;
    const kindSel = selectField("Property type", [["frame_section", "Frame sections"], ["shell_section", "Shell sections"], ["material", "Materials"]], "frame_section");
    const itemsFor = k => {
      const names = k === "frame_section" ? Object.keys(m.sections || {}) : k === "shell_section" ? Object.keys(m.shell_sections || {}) : Object.keys(m.materials || {});
      return names.map(nm => [nm, `${nm}  (${refsByProperty(k, [nm]).length})`]);
    };
    let kind = "frame_section";
    const dlg = listDialog("edSelPropDlg", "Select by Property", "Select every frame / shell object using the checked properties.",
      itemsFor(kind), (vals, add) => selectByProperty(kind, vals, add), kindSel);
    kindSel.sel.addEventListener("change", () => { kind = kindSel.get(); dlg.fill(itemsFor(kind)); });
    return dlg;
  }
  function openSelectByStory() {
    const m = S.model;
    if (!m) return null;
    const items = [...m.stories].reverse().map(s => [s.name, `${s.name}  (${refsByStory([s.name]).length})`]);
    return listDialog("edSelStoryDlg", "Select by Story", "Select every frame, shell and link object of the checked stories.",
      items, (vals, add) => selectByStory(vals, add));
  }

  function openSelectByPlane() {
    const m = S.model;
    if (!m) return null;
    const body = document.createElement("div");
    body.className = "ed-body";
    const plane = selectField("Plane", [["xy", "XY plane (z = const)"], ["xz", "XZ plane (y = const)"], ["yz", "YZ plane (x = const)"]], "xy");
    const st = (S.story && m.stories.find(s => s.name === S.story)) || m.stories[m.stories.length - 1];
    const coord = field("Through coordinate", LEN, st ? st.elevation : 0);
    const levelSel = selectField("…or story level", [["", "—"], ...[...m.stories].reverse().map(s => [String(s.elevation), `${s.name} (${emU.fmtU(LEN, s.elevation, 2)})`]),
      [String(m.stories.length ? m.stories[0].elevation - m.stories[0].height : 0), "Base"]], "");
    const pick = emBtn("Pick point…", "", () => startPick());
    pick.title = "Click a point in the plan / elevation view; the plane passes through it";
    const add = check("Add to the current selection", false);
    body.append(group("Select objects lying in a plane", [plane, coord, levelSel, pick]), add.row);
    const err = emErrorLine();
    body.appendChild(err);
    levelSel.sel.addEventListener("change", () => { if (levelSel.get() !== "") { plane.sel.value = "xy"; coord.inp.value = emU.inputValue(LEN, +levelSel.get()); } });
    let picked = null;
    const okB = emBtn("Select", "btn-primary", () => {
      const c = coord.get();
      if (!isFinite(c)) return emShowError(err, "Coordinate must be a number");
      const n = selectByPlane(plane.get(), c, add.get());
      d.close();
      toast("Select by Plane", `${n} object(s) selected`, "info", 2500);
    });
    okB.dataset.edOk = "1";
    const { wrap } = emFootBar("", [emBtn("Cancel", "", () => d.close()), okB]);
    const d = emDialog("edSelPlaneDlg", { title: "Select by Plane", body, foot: wrap });
    function startPick() {
      const back = d.el;
      back.classList.add("ed-picking");
      const targets = [sky.planEditor && sky.planEditor.svg, sky.elevEditor && sky.elevEditor.svg].filter(Boolean);
      toast("Pick point", "Click a point in the plan or elevation view (Esc cancels).", "info", 3000);
      const done = () => { back.classList.remove("ed-picking"); targets.forEach(t => t.removeEventListener("pointerdown", onDown, true)); document.removeEventListener("keydown", onEsc, true); };
      const onEsc = e => { if (e.key === "Escape") { e.stopImmediatePropagation(); done(); } };
      const onDown = e => {
        e.stopPropagation(); e.preventDefault();
        const isPlan = sky.planEditor && e.currentTarget === sky.planEditor.svg;
        const ed = isPlan ? sky.planEditor : sky.elevEditor;
        const r = ed.svg.getBoundingClientRect();
        const w = ed.toWorld(e.clientX - r.left, e.clientY - r.top);
        let p;
        if (isPlan) {
          const s = ed.snap(w);
          const z = (m.stories.find(x => x.name === S.story) || {}).elevation || 0;
          p = [s.x, s.y, z];
        } else {
          const s = ed.snap ? ed.snap(w) : w;
          p = ed.world3(s.s !== undefined ? s.s : w.s, s.z !== undefined ? s.z : w.z);
        }
        picked = p;
        const ax = { xy: 2, xz: 1, yz: 0 }[plane.get()];
        coord.inp.value = emU.inputValue(LEN, +p[ax].toFixed(6));
        done();
      };
      targets.forEach(t => t.addEventListener("pointerdown", onDown, true));
      document.addEventListener("keydown", onEsc, true);
    }
    plane.sel.addEventListener("change", () => {
      if (!picked) return;
      const ax = { xy: 2, xz: 1, yz: 0 }[plane.get()];
      coord.inp.value = emU.inputValue(LEN, +picked[ax].toFixed(6));
    });
    return { d, startPick };
  }

  /* ================================================================ undo / redo */
  function undo() { const ok = history.undo(); trackSel(); syncChip(); return ok; }
  function redo() { const ok = history.redo(); trackSel(); syncChip(); return ok; }

  // status-bar chip: last action + undo/redo buttons
  const status = $("etabsStatus");
  let chip = null;
  if (status) {
    chip = document.createElement("span");
    chip.className = "sb-item ed-hist";
    chip.id = "sbHistory";
    chip.innerHTML = `<button class="sb-chip ed-hist-btn" data-h="undo" title="Undo (Ctrl+Z)">↶</button>` +
      `<button class="sb-chip ed-hist-btn" data-h="redo" title="Redo (Ctrl+Y)">↷</button>` +
      `<span class="ed-hist-txt" id="sbHistoryText">No edits</span>`;
    chip.querySelector('[data-h="undo"]').addEventListener("click", undo);
    chip.querySelector('[data-h="redo"]').addEventListener("click", redo);
    const sep = document.createElement("span");
    sep.className = "sb-sepv";
    const spacer = status.querySelector(".sb-spacer");
    if (spacer) { status.insertBefore(sep, spacer); status.insertBefore(chip, spacer); }
    else status.append(sep, chip);
  }
  function syncChip() {
    if (!chip) return;
    const st = history.state();
    chip.querySelector("#sbHistoryText").textContent = st.lastLabel || "No edits";
    chip.querySelector('[data-h="undo"]').disabled = !st.canUndo;
    chip.querySelector('[data-h="redo"]').disabled = !st.canRedo;
    chip.querySelector('[data-h="undo"]').title = st.canUndo ? `Undo ${st.undoLabel} (Ctrl+Z)` : "Nothing to undo";
    chip.querySelector('[data-h="redo"]').title = st.canRedo ? `Redo ${st.redoLabel} (Ctrl+Y)` : "Nothing to redo";
  }
  history.onChange(syncChip);
  syncChip();

  /* ================================================================ keyboard */
  const anyModalOpen = () => [...document.querySelectorAll(".modal-backdrop")].some(b => !b.classList.contains("hidden"));
  document.addEventListener("keydown", e => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const t = e.target, tag = (t.tagName || "").toLowerCase();
    if (["input", "select", "textarea"].includes(tag) || (t.isContentEditable)) return;
    if (anyModalOpen()) return;
    const k = e.key.toLowerCase();
    if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
    else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); redo(); }
    else if (k === "c") {
      const txt = window.getSelection && String(window.getSelection());
      if (txt) return;                                   // let the browser copy page text
      if (selCount()) { e.preventDefault(); copySel(); }
    } else if (k === "v") { if (clipboard) { e.preventDefault(); openPaste(); } }
    else if (k === "a" && S.mode === "model") { e.preventDefault(); selectAll(); }
  });

  /* ================================================================ public */
  const guard = fn => (...a) => {
    try {
      const r = fn(...a);
      if (r && typeof r.catch === "function") r.catch(err => toast("Edit failed", err.message, "error", 7000));
      return r;
    } catch (err) { toast("Edit failed", err.message, "error", 7000); return null; }
  };
  sky.edit = {
    undo, redo, copy: copySel, paste: guard(openPaste),
    openReplicate: guard(openReplicate), openDivide: guard(openDivide), openMerge: guard(openMerge),
    openAlign: guard(openAlign), openMove: guard(openMove), openExtrude: guard(openExtrude),
    join: joinFrames, deleteSelection: deleteSel,
    run: runOp, backendSel, ghost, history,
    clipboard: () => clipboard,
    closeAll: () => ["edReplicateDlg", "edDivideDlg", "edMergeDlg", "edAlignDlg", "edMoveDlg", "edExtrudeDlg",
      "edPasteDlg", "edSelPropDlg", "edSelStoryDlg", "edSelPlaneDlg"].forEach(emCloseDialog),
    undoHint: () => { const s = history.state(); return s.canUndo ? `Ctrl+Z · ${s.undoLabel}` : "Ctrl+Z"; },
    redoHint: () => { const s = history.state(); return s.canRedo ? `Ctrl+Y · ${s.redoLabel}` : "Ctrl+Y"; },
  };
  sky.selectx = {
    all: selectAll, invert: invertSelection, previous: previousSelection, clear: clearSelection,
    byProperty: selectByProperty, byStory: selectByStory, byPlane: selectByPlane,
    refsByProperty, refsByStory, refsByPlane,
    openByProperty: openSelectByProperty, openByStory: openSelectByStory, openByPlane: openSelectByPlane,
    prev: () => prevSel,
  };
  return sky.edit;
}
