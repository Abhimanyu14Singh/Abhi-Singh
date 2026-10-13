/* SkyFrame — View › Set Display Options… (Ctrl+W / Alt+W), ETABS style.

   One tabbed dialog (dlgkit standard, docs/DIALOG_STANDARD.md) that edits the
   display options of ONE WINDOW — the active one by default:

     Objects              visibility of joints, beams, columns, braces, walls,
                          floors, links, tendons, supports, springs, loads;
                          per-object labels (frames: labels / sections / local
                          axes / releases / insertion points; shells: labels /
                          sections / local axes; links: labels)
     Object View Options  extrude frames / shells, fill objects, show edges,
                          shrink objects, colour by object type / section /
                          material / group / story / property type, shell
                          transparency
     Other                grid lines, grid bubbles, story labels, dimension
                          lines, background, joint size, line thickness
     Show Only            group, selected objects only; 3D: story range, load
                          pattern, section cut plane

   Windows: the main Plan, Elevation and 3D views and the plan / elevation panes
   of View › Windows (w1 … w4). "Apply to all windows" and "Save as Default"
   are in the footer.

   No duplicated state: the 3D window IS the existing 3D display state
   (js/viewext.js "vx" options: Extruded View Alt+X, Show Local Axes, the 3D
   Extrude / Sel. only / Cut chips and the QAT Extrude / Axes buttons all keep
   working and show up here); 3D-only extras travel in vx.dp
   (js/dispopts_core.js dpToVx / dpFromVx). Plan / elevation windows are drawn
   by draw.js / elev.js and get the options through two hooks (dpStatic after
   the elements, dpLabels after the screen-space labels).

   Persistence: view state, never model state (a save stays byte-identical).
   Per-window settings + the saved default live in localStorage (guarded);
   named views (View › Named Views) also carry them. The 3D options keep their
   sessionStorage copy in viewext.js as well.

   Quick-access display toolbar: Extrude · Labels · Sections · Axes · Colour by
   · Display… in the plan / elevation toolbar and next to the 3D chips. */

import { el as dpEl, dkForm as dpForm, dkGroup as dpGroup, dkTabs as dpTabs, dkDialog as dpDialog,
  dkClose as dpClose, dkIsOpen as dpIsOpen } from "./dlgkit.js";
import { memberAxes as dpMemberAxes, regionFrame as dpRegionFrame } from "./polygeom.js";
import * as DPC from "./dispopts_core.js";

const DP_LS_KEY = "skyframe.dispopts.v1";
const DP_DLG = "dpDisplayDlg";
const SVGNS = "http://www.w3.org/2000/svg";
const WIN_LABEL = { plan: "Plan View", elev: "Elevation View", "3d": "3D View", w1: "Window 1", w2: "Window 2", w3: "Window 3", w4: "Window 4" };
const ONLY_3D = "3D window only";

export function initDisplayOptions(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 3500) => sky.toast && sky.toast(t, m, k, ms);
  const lsRead = () => { try { return localStorage.getItem(DP_LS_KEY); } catch { return null; } };
  const lsWrite = txt => { try { localStorage.setItem(DP_LS_KEY, txt); } catch { /* storage blocked: session only */ } };
  const store = DPC.dpDeserializeStore(lsRead());
  let lastErr = null;

  /* ================================================================ windows */
  const W = () => sky.wb && sky.wb.windows;
  const panes = () => { const w = W(); return w && w.layout && w.layout() !== "1" ? w.panes() : []; };
  // Wave H: docked windows carry a stable id (p.dpId) and 3D windows their own 3D options (p.vx, js/viewext.js instance)
  const paneId = (p, i) => p.dpId || "w" + (i + 1);
  const paneOf = win => panes().find((p, i) => paneId(p, i) === win) || null;
  const own = p => !!p && (p.kind === "plan" || p.kind === "elev" || (p.kind === "3d" && !!p.vx));
  const vxPane = win => { const p = paneOf(win); return p && p.kind === "3d" && p.vx ? p : null; };
  const is3dWin = win => win === "3d" || !!vxPane(win);
  const winLabel = win => WIN_LABEL[win] || (/^w\d+$/.test(win || "") ? `Window ${win.slice(1)}` : String(win));
  /** The window the dialog targets by default (ETABS: the active window). */
  function activeWin() {
    const ps = panes();
    if (ps.length) {
      const f = document.querySelector(".wb-pane.is-focus");
      const i = f ? +f.dataset.pane : 0;
      const p = ps[i];
      if (own(p)) return paneId(p, i);
      if (p && p.kind === "3d") return "3d";
    }
    if (S.mode === "analyze") return "3d";
    return S.view === "elev" ? "elev" : "plan";
  }
  /** All windows that exist now. */
  function windowsNow() {
    const out = ["plan", "elev", "3d"];
    panes().forEach((p, i) => { if (own(p)) out.push(paneId(p, i)); });
    return out;
  }
  function winOfEditor(ed) {
    if (ed === sky.planEditor) return "plan";
    if (ed === sky.elevEditor) return "elev";
    const ps = panes();
    for (let i = 0; i < ps.length; i++) if (ps[i].ed === ed) return paneId(ps[i], i);
    return null;
  }
  const baseDefault = () => store.default ? DPC.dpClone(store.default) : DPC.dpDefaults();

  /* ================================================================ get / set */
  function get(win) {
    { const vp = vxPane(win); if (vp) return DPC.dpFromVx(vp.vx.opts()); }   // Wave H: a 3D window's own options
    if (win === "3d") return sky.vx && sky.vx.opts ? DPC.dpFromVx(sky.vx.opts()) : (store.windows["3d"] || baseDefault());
    return DPC.dpClone(store.windows[win] || baseDefault());
  }
  function persist() {
    const w = { ...store.windows };
    if (sky.vx && sky.vx.opts) w["3d"] = DPC.dpFromVx(sky.vx.opts());
    lsWrite(DPC.dpSerializeStore({ windows: w, default: store.default }));
  }
  function refreshWin(win) {
    if (win === "3d") { apply3dExtras(); return; }
    let ed = null;
    if (win === "plan") ed = sky.planEditor;
    else if (win === "elev") ed = sky.elevEditor;
    else { const p = paneOf(win); ed = p && p.ed; if (p && p.v) p.v._dirty = true; }
    if (ed && ed.refresh) { try { ed.refresh(); } catch (e) { console.error(e); } }
    if (win === "plan") syncStoryBadge();
  }
  function set(win, s) {
    s = DPC.dpNormalize(s);
    const vp = vxPane(win);                                 // Wave H: a 3D window's own options
    if (vp) { vp.vx.set(DPC.dpToVx(s)); if (vp.v) { vp.v.canvas.style.background = DPC.DP_BACKGROUND_CSS[s.other.background] || ""; vp.v._dirty = true; } syncBars(); return DPC.dpClone(s); }
    if (win === "3d") {
      store.windows["3d"] = s;
      if (sky.vx && sky.vx.set) sky.vx.set(DPC.dpToVx(s));   // → sky:vx-changed → persist + sync
      else persist();
      apply3dExtras();
    } else {
      if (DPC.dpEqual(s, baseDefault())) delete store.windows[win]; else store.windows[win] = s;
      persist();
      refreshWin(win);
    }
    syncBars();
    return DPC.dpClone(s);
  }
  const patch = (win, p) => set(win, DPC.dpMerge(get(win), p));

  /* ================================================================ 3D extras */
  function apply3dExtras() {
    const s = get("3d");
    const v = sky.viewer;
    if (v && v.canvas) v.canvas.style.background = DPC.DP_BACKGROUND_CSS[s.other.background] || "";
    if (v) v._dirty = true;
  }
  function wrapTendons3d() {
    const v = sky.viewer;
    if (!v || !v.ptOverlay || v.ptOverlay.__dp) return;
    const orig = v.ptOverlay;
    const w = function (...a) { if (get("3d").objects.tendon === false) return; return orig.apply(this, a); };
    w.__dp = true;
    v.ptOverlay = w;
  }
  // releases · insertion points · shell sections · link labels in the 3D view
  function annot3d(ctx, proj, g, dp) {
    const m = S.model;
    if (!m) return;
    ctx.save();
    ctx.font = "600 10px -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    const memBy = new Map((m.members || []).map(x => [String(x.uid), x]));
    for (const [seg, vv] of g.segVis) {
      if (!vv) continue;
      const mm = memBy.get(String(seg.uid));
      if (!mm) continue;
      if (dp.frameReleases) {
        const r = DPC.dpReleases(mm);
        for (const [e, t] of [["i", 0.1], ["j", 0.9]]) {
          if (!r[e].length) continue;
          const p = vv.p1.map((a, k) => a + (vv.p2[k] - a) * t);
          const q = proj(p[0], p[1], p[2]);
          if (!q) continue;
          ctx.beginPath(); ctx.arc(q[0], q[1], 3.2, 0, 2 * Math.PI);
          ctx.fillStyle = "rgba(15, 20, 26, 0.9)"; ctx.fill();
          ctx.strokeStyle = "#e5a50a"; ctx.lineWidth = 1.4; ctx.stroke();
        }
      }
      if (dp.frameInsertion) {
        const lbl = DPC.dpInsertionLabel(mm);
        if (lbl) {
          const c = vv.p1.map((a, k) => (a + vv.p2[k]) / 2);
          const q = proj(c[0], c[1], c[2]);
          if (q) { ctx.fillStyle = "rgba(79, 208, 199, 0.95)"; ctx.fillText(lbl, q[0], q[1] + 14); }
        }
      }
    }
    if (dp.shellSection) {
      const shBy = new Map((m.shells || []).map(x => [String(x.uid), x]));
      for (const [sp, vv] of g.shellVis) {
        if (!vv) continue;
        const sh = shBy.get(String(sp.uid));
        const c = [0, 1, 2].map(k => vv.corners.reduce((a, p) => a + p[k], 0) / vv.corners.length);
        const q = proj(c[0], c[1], c[2]);
        if (q && sh) { ctx.fillStyle = "rgba(245, 190, 60, 0.95)"; ctx.fillText(String(sh.section || ""), q[0], q[1] + 12); }
      }
    }
    if (dp.linkName) {
      for (const lk of (sky.viewer && sky.viewer.linkSegs) || []) {
        if (g.linkVis && g.linkVis.has(lk) && !g.linkVis.get(lk)) continue;
        const c = lk.p1.map((a, k) => (a + lk.p2[k]) / 2);
        const q = proj(c[0], c[1], c[2]);
        if (q) { ctx.fillStyle = "rgba(52, 195, 132, 0.95)"; ctx.fillText(String(lk.uid || ""), q[0] + 12, q[1] - 10); }
      }
    }
    ctx.restore();
  }
  if (sky.viewer) sky.viewer.dpAnnot = annot3d;

  /* ================================================================ 2D hooks */
  const isElev = ed => typeof ed.plane === "function";
  const projOf = ed => isElev(ed) ? p => ed.toScreen(ed.sOf(p), p[2]) : p => ed.toScreen(p[0], p[1]);
  const worldOf = ed => isElev(ed) ? p => [ed.sOf(p), p[2]] : p => [p[0], p[1]];
  const svgEl = (tag, attrs) => { const e = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };
  const OBJ_OF_REF = { spring: "spring", linespring: "spring", areaspring: "spring" };
  const MEMBER_DECOR = new Set(["panelzone", "rigid", "foundation", "axial", "w8curve"]);

  function filterSets(m, s) {
    const g = s.filter.group && m.groups && m.groups[s.filter.group] ? m.groups[s.filter.group] : null;
    const set = k => (g ? new Set((g[k] || []).map(String)) : null);
    return { gm: set("members"), gs: set("shells"), gl: set("links") };
  }
  /** After the elements are drawn: visibility, colour by, fill, line thickness, shrink. */
  function staticHook(ed) {
    try {
      const win = winOfEditor(ed);
      if (!win) return;
      const s = get(win);
      const svg = ed.svg;
      if (ed.gGrid) ed.gGrid.style.display = s.other.grid ? "" : "none";
      svg.toggleAttribute("data-dp-nodims", !s.other.dims);
      svg.toggleAttribute("data-dp-notendon", !s.objects.tendon);
      svg.setAttribute("data-dp-win", win);
      svg.style.background = DPC.DP_BACKGROUND_CSS[s.other.background] || "";
      const m = ed.opts.getModel();
      if (!m || DPC.dpIsDefault(s)) return;
      const memBy = new Map((m.members || []).map(x => [String(x.uid), x]));
      const shBy = new Map((m.shells || []).map(x => [String(x.uid), x]));
      const lkBy = new Map((m.links || []).map(x => [String(x.uid), x]));
      const sel = ed.opts.getSelection ? ed.opts.getSelection() : new Set();
      const F = filterSets(m, s);
      const selOnly = s.filter.selOnly;
      const by = s.view.colorBy, scale = s.other.lineScale, shrink = s.view.shrink;
      const W2 = worldOf(ed);
      const shrinkAround = (node, pts) => {
        const c = pts.reduce((a, p) => [a[0] + p[0] / pts.length, a[1] + p[1] / pts.length], [0, 0]);
        const f = DPC.DP_SHRINK;
        node.setAttribute("transform", `translate(${c[0]} ${c[1]}) scale(${f}) translate(${-c[0]} ${-c[1]})`);
      };
      for (const node of [...ed.gElems.children]) {
        const ref = node.getAttribute("data-ref");
        if (!ref) continue;
        const i = ref.indexOf(":");
        const type = ref.slice(0, i), uid = ref.slice(i + 1);
        let obj = null, okey = OBJ_OF_REF[type] || null;
        if (type === "member" || MEMBER_DECOR.has(type)) { obj = memBy.get(uid); okey = obj ? obj.kind : null; }
        else if (type === "shell") { obj = shBy.get(uid); okey = obj ? (obj.kind === "wall" ? "wall" : "slab") : null; }
        else if (type === "link") { obj = lkBy.get(uid); okey = "link"; }
        let vis = !okey || s.objects[okey] !== false;
        if (vis && obj) {
          const T = type === "shell" ? "shell" : type === "link" ? "link" : "member";
          const gset = T === "shell" ? F.gs : T === "link" ? F.gl : F.gm;
          if (gset && !gset.has(uid)) vis = false;
          if (selOnly && !sel.has(`${T}:${uid}`)) vis = false;
        }
        if (!vis) { node.setAttribute("display", "none"); node.setAttribute("data-dp-hidden", "1"); continue; }
        if (!obj || !(type === "member" || type === "shell" || type === "link")) continue;
        const selected = sel.has(`${type}:${uid}`);
        const tag = node.tagName.toLowerCase();
        // colour by
        if (by !== "type" && !selected) {
          const col = DPC.dpColorOf(m, by, { type, kind: type === "link" ? "link" : obj.kind, uid, section: obj.section, story: obj.story, behavior: obj.behavior, link_type: obj.link_type });
          node.setAttribute("data-dp-color", col);
          if (type === "shell" && tag === "path") { node.setAttribute("fill", col); node.setAttribute("fill-opacity", isElev(ed) ? "0.30" : "0.28"); node.setAttribute("stroke", col); }
          else if (tag === "rect") node.setAttribute("fill", col);
          else node.setAttribute("stroke", col);
        }
        // fill objects off: outlines only
        if (!s.view.fill) {
          if (tag === "path" && type === "shell") node.setAttribute("fill", "none");
          if (tag === "rect") { node.setAttribute("stroke", node.getAttribute("fill") || "#5f8fc9"); node.setAttribute("fill", "none"); node.setAttribute("stroke-width", "1.6"); }
        }
        // line thickness
        if (scale !== 1) {
          const sw = parseFloat(node.getAttribute("stroke-width"));
          if (isFinite(sw)) node.setAttribute("stroke-width", String(+(sw * scale).toFixed(4)));
        }
        // shrink objects (ETABS "Shrink Objects")
        if (shrink) {
          if (tag === "line") {
            const x1 = +node.getAttribute("x1"), y1 = +node.getAttribute("y1"), x2 = +node.getAttribute("x2"), y2 = +node.getAttribute("y2");
            const [[a, b], [c, d]] = DPC.dpShrinkPts([[x1, y1], [x2, y2]], DPC.DP_SHRINK);
            node.setAttribute("x1", a); node.setAttribute("y1", b); node.setAttribute("x2", c); node.setAttribute("y2", d);
          } else if (type === "shell" && obj.corners) shrinkAround(node, obj.corners.map(W2));
          else if (type === "link" && obj.pi) shrinkAround(node, [W2(obj.pi), W2(obj.pj)]);
        }
      }
    } catch (e) { lastErr = String(e && e.stack || e); console.error(e); }
  }

  /** After the screen-space labels: bubbles, story labels, joints, object labels, axes, releases. */
  function labelsHook(ed) {
    try {
      const win = winOfEditor(ed);
      if (!win) return;
      const s = get(win);
      const m = ed.opts.getModel();
      if (!m) return;
      const elev = isElev(ed);
      // grid bubbles (labels) and story labels — the editors draw them first, at 11 px
      for (const t of [...ed.gLabels.querySelectorAll("text")]) {
        if (t.getAttribute("font-size") !== "11") continue;
        const story = elev && t.getAttribute("text-anchor") === "end";
        const hide = story ? !s.other.storyLabels : !s.other.bubbles;
        if (hide) t.setAttribute("display", "none");
      }
      if (DPC.dpIsDefault(s)) return;
      const P = projOf(ed);
      const g = svgEl("g", { class: "dp-labels", "pointer-events": "none" });
      const hidden = new Set([...ed.gElems.querySelectorAll("[data-dp-hidden]")].map(n => n.getAttribute("data-ref")));
      const drawn = type => new Set([...ed.gElems.querySelectorAll(`[data-ref^="${type}:"]`)]
        .map(n => n.getAttribute("data-ref")).filter(r => !hidden.has(r)).map(r => r.slice(type.length + 1)));
      const mems = drawn("member"), shs = drawn("shell"), lks = drawn("link");
      const text = (x, y, txt, fill, extra = {}) => {
        const t = svgEl("text", { x, y, fill, "font-size": 10, "font-weight": 600, "text-anchor": "middle", "dominant-baseline": "middle", "font-family": "inherit", ...extra });
        t.textContent = txt; g.appendChild(t);
      };
      const L = s.labels;
      const memList = (m.members || []).filter(mm => mems.has(String(mm.uid)));
      // joints
      if (s.objects.joint) {
        const seen = new Set();
        const r = s.other.jointSize / 2;
        for (const mm of memList) for (const p of [mm.pi, mm.pj]) {
          const [x, y] = P(p);
          const k = `${x.toFixed(1)},${y.toFixed(1)}`;
          if (seen.has(k)) continue;
          seen.add(k);
          g.appendChild(svgEl("circle", { cx: x, cy: y, r, fill: "rgba(230, 236, 244, 0.9)", "data-dp": "joint" }));
        }
      }
      for (const mm of memList) {
        const a = P(mm.pi), b = P(mm.pj);
        const point = Math.hypot(b[0] - a[0], b[1] - a[1]) < 2;   // a column seen in plan
        const c = point ? [a[0] + 10, a[1] - 10] : [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
        if (L.frameName) text(c[0], c[1] - 7, String(mm.uid), "rgba(225, 232, 240, 0.95)", { "data-dp": "frame-name" });
        if (L.frameSection) text(c[0], c[1] + (L.frameName ? 6 : 0) + (point ? 4 : 0), String(mm.section || ""), "rgba(245, 190, 60, 0.95)", { "data-dp": "frame-section" });
        if (L.frameInsertion) { const lbl = DPC.dpInsertionLabel(mm); if (lbl) text(c[0], c[1] + 18, lbl, "rgba(79, 208, 199, 0.95)", { "data-dp": "frame-insertion" }); }
        if (L.frameReleases && !point) {
          const r = DPC.dpReleases(mm);
          for (const [e, t] of [["i", 0.12], ["j", 0.88]]) if (r[e].length)
            g.appendChild(svgEl("circle", { cx: a[0] + (b[0] - a[0]) * t, cy: a[1] + (b[1] - a[1]) * t, r: 3.4, fill: "rgba(15, 20, 26, 0.9)", stroke: "#e5a50a", "stroke-width": 1.4, "data-dp": "release" }));
        }
        if (L.frameAxes) {
          const ax = dpMemberAxes(mm, m);
          const mid = mm.pi.map((v, k) => (v + mm.pj[k]) / 2);
          triad(g, P, point ? mm.pi : mid, [ax.x, ax.y, ax.z], "frame-axes");
        }
      }
      for (const sh of (m.shells || [])) {
        if (!shs.has(String(sh.uid))) continue;
        const cs = sh.corners.map(P);
        const c = [cs.reduce((t, p) => t + p[0], 0) / cs.length, cs.reduce((t, p) => t + p[1], 0) / cs.length];
        if (L.shellName) text(c[0], c[1] - 6, String(sh.uid), "rgba(160, 200, 240, 0.95)", { "data-dp": "shell-name" });
        if (L.shellSection) text(c[0], c[1] + 7, String(sh.section || ""), "rgba(245, 190, 60, 0.95)", { "data-dp": "shell-section" });
        if (L.shellAxes) {
          const fr = dpRegionFrame(sh.corners);
          const c3 = [0, 1, 2].map(k => sh.corners.reduce((t, p) => t + p[k], 0) / sh.corners.length);
          triad(g, P, c3, [fr.e1, fr.e2, fr.e3], "shell-axes");
        }
      }
      if (L.linkName) for (const lk of (m.links || [])) {
        if (!lks.has(String(lk.uid))) continue;
        const a = P(lk.pi), b = P(lk.pj);
        text((a[0] + b[0]) / 2 + 12, (a[1] + b[1]) / 2 - 12, String(lk.uid), "rgba(52, 195, 132, 0.95)", { "data-dp": "link-name" });
      }
      if (g.childNodes.length) ed.gLabels.appendChild(g);
    } catch (e) { lastErr = String(e && e.stack || e); console.error(e); }
  }
  // local axis triad (1 red · 2 green · 3 blue), screen length 22 px
  function triad(g, P, c, axes, tag) {
    const q0 = P(c);
    const cols = ["#e5484d", "#30a46c", "#3e7bfa"];
    axes.forEach((a, i) => {
      const q1 = P(c.map((v, k) => v + a[k] * 1e-3));
      let dx = q1[0] - q0[0], dy = q1[1] - q0[1];
      const l = Math.hypot(dx, dy);
      if (l < 1e-9) {   // axis points at the viewer: a dot
        g.appendChild(svgEl("circle", { cx: q0[0], cy: q0[1], r: 2.4, fill: cols[i], "data-dp": tag }));
        return;
      }
      dx = dx / l * 22; dy = dy / l * 22;
      g.appendChild(svgEl("line", { x1: q0[0], y1: q0[1], x2: q0[0] + dx, y2: q0[1] + dy, stroke: cols[i], "stroke-width": 1.4, "data-dp": tag }));
      const t = svgEl("text", { x: q0[0] + dx * 1.25, y: q0[1] + dy * 1.25, fill: cols[i], "font-size": 9, "font-weight": 700, "text-anchor": "middle", "dominant-baseline": "middle" });
      t.textContent = String(i + 1); g.appendChild(t);
    });
  }
  for (const ed of [sky.planEditor, sky.elevEditor]) if (ed) { ed.dpStatic = staticHook; ed.dpLabels = labelsHook; }
  function syncStoryBadge() {
    const b = document.getElementById("planStoryBadge");
    if (b) b.style.visibility = get("plan").other.storyLabels ? "" : "hidden";
  }

  /* ================================================================ dialog */
  function open(win, tab) {
    win = win && (WIN_LABEL[win] || paneOf(win)) ? win : activeWin();
    const is3d = is3dWin(win);
    const D = get(win);
    const F = dpForm();
    const m = S.model || {};
    const only3d = nodes => { const grp = dpGroup(null, nodes, { cls: is3d ? "" : "dk-disabled dp-off" }); if (!is3d) grp.querySelectorAll("input, select").forEach(i => { i.disabled = true; }); return grp; };
    const checks = (obj, list, idp) => dpEl("div", { class: "dp-checks" }, list.map(([k, label]) => F.check(obj, k, { label, id: idp + k })));

    // window target
    const winSel = dpEl("select", { class: "dk-select", id: "dpWin", "aria-label": "Window" });
    for (const w of windowsNow()) winSel.appendChild(dpEl("option", { value: w, selected: w === win ? true : null }, [winLabel(w) + (w === activeWin() ? " (active)" : "")]));
    winSel.addEventListener("change", () => open(winSel.value, tabs.current()));
    const head = dpEl("div", { class: "dp-head" }, [dpEl("label", { class: "dk-label", for: "dpWin" }, ["Window"]), winSel,
      dpEl("span", { class: "muted dp-head-note", text: is3d ? "Same options as View › Extruded View / Show Local Axes and the 3D chips." : "Plan and elevation windows: options marked “3D” apply to the 3D window." })]);

    // Objects tab
    const objBox = checks(D.objects, DPC.DP_OBJECTS, "dpObj_");
    const setAll = v => { for (const [k] of DPC.DP_OBJECTS) D.objects[k] = v; F.refresh(); };
    const allB = dpEl("button", { type: "button", class: "btn btn-small", id: "dpObjAll" }, ["All"]);
    const noneB = dpEl("button", { type: "button", class: "btn btn-small", id: "dpObjNone" }, ["None"]);
    allB.addEventListener("click", () => setAll(true));
    noneB.addEventListener("click", () => setAll(false));
    const labGroup = (title, keys) => dpEl("div", { class: "dp-labcol" }, [dpEl("div", { class: "dp-labhead", text: title }),
      ...DPC.DP_LABELS.filter(([, , grp]) => grp === title).map(([k, label]) => F.check(D.labels, k, { label, id: "dpLab_" + k }))]);
    const tabObjects = dpEl("div", {}, [
      dpGroup("Object Visibility", [objBox, dpEl("div", { class: "dp-row-btns" }, [allB, noneB]),
        dpEl("p", { class: "muted dk-note", text: "Supports and loads are drawn in the 3D window; plan and elevation windows draw the other objects." })]),
      dpGroup("Object Labels", [dpEl("div", { class: "dp-labcols" }, [labGroup("Frames"), labGroup("Shells"), labGroup("Links")])]),
    ]);

    // Object View Options tab
    const legend = dpEl("div", { class: "dp-legend", id: "dpLegend" });
    const drawLegend = () => {
      legend.textContent = "";
      for (const [lbl, col] of DPC.dpLegend(S.model, D.view.colorBy).slice(0, 16))
        legend.appendChild(dpEl("span", { class: "dp-leg" }, [dpEl("i", { style: `background:${col}` }), lbl]));
    };
    const tabView = dpEl("div", {}, [
      dpGroup("View Type", [
        F.check(D.view, "extrude", { label: "Extrude frames (section shapes)", id: "dpExtrude", hint: is3d ? "Alt+X" : ONLY_3D }),
        F.check(D.view, "extrudeShells", { label: "Extrude shells (thickness)", id: "dpExtrudeShells", hint: is3d ? "" : ONLY_3D }),
        F.check(D.view, "fill", { label: "Fill objects", id: "dpFill" }),
        F.check(D.view, "edges", { label: "Show edges", id: "dpEdges", hint: is3d ? "outline of extruded solids" : ONLY_3D }),
        F.check(D.view, "shrink", { label: "Shrink objects", id: "dpShrink", hint: "draw every object at 80 % around its centre — separates touching objects" }),
      ]),
      dpGroup("Colour", [
        F.select(D.view, "colorBy", DPC.DP_COLOR_BY, { label: "Colour by", id: "dpColorBy", onChange: drawLegend,
          hint: "Property type = section shape (rectangular, I, pipe, tube, designer, nonprismatic); shells slab / wall; links by type." }),
        legend,
        F.num(null, "transp", { label: "Shell transparency", id: "dpTransp", unit: "%", min: 0, max: 90, def: 0,
          read: () => Math.round(D.view.shellTransp * 100), write: v => { D.view.shellTransp = (v || 0) / 100; } }),
      ]),
    ]);
    drawLegend();

    // Other tab
    const tabOther = dpEl("div", {}, [
      dpGroup("Grid and Annotations", [
        F.check(D.other, "grid", { label: "Grid lines", id: "dpGrid" }),
        F.check(D.other, "bubbles", { label: "Grid bubbles (labels)", id: "dpBubbles" }),
        F.check(D.other, "storyLabels", { label: "Story labels", id: "dpStoryLabels", hint: is3d ? "plan / elevation windows" : "" }),
        F.check(D.other, "dims", { label: "Dimension lines", id: "dpDims", hint: is3d ? "plan windows" : "" }),
      ]),
      dpGroup("Appearance", [
        F.select(D.other, "background", DPC.DP_BACKGROUNDS, { label: "Background", id: "dpBackground" }),
        F.num(D.other, "jointSize", { label: "Joint size", id: "dpJointSize", unit: "px", min: 1, max: 12, def: 4 }),
        F.num(D.other, "lineScale", { label: "Line thickness", id: "dpLineScale", unit: "×", min: 0.5, max: 4, def: 1 }),
      ]),
    ]);

    // Show Only tab
    const groups = Object.keys(m.groups || {});
    const stories = (m.stories || []).slice().sort((a, b) => a.elevation - b.elevation).map(s => [s.name, s.name]);
    const bbox = sky.viewer && sky.viewer._bbox;
    const mid = ax => { const i = { x: 0, y: 1, z: 2 }[ax]; return bbox ? (bbox[0][i] + bbox[1][i]) / 2 : 0; };
    const cut = D.filter.cut;
    const tabFilter = dpEl("div", {}, [
      dpGroup("Objects Shown", [
        F.select(D.filter, "group", [["", "(all objects)"], ...groups.map(g => [g, g])], { label: "Group", id: "dpGroupSel" }),
        F.check(D.filter, "selOnly", { label: "Selected objects only", id: "dpSelOnly" }),
      ]),
      only3d([
        dpEl("p", { class: "muted dk-note", text: "3D window" }),
        F.select(D.filter, "storyFrom", [["", "(all)"], ...stories], { label: "Stories from", id: "dpStoryFrom" }),
        F.select(D.filter, "storyTo", [["", "(all)"], ...stories], { label: "Stories to", id: "dpStoryTo" }),
        F.select(D.filter, "loadPattern", [["", "(first pattern)"], ...Object.keys(m.patterns || {}).map(p => [p, p])], { label: "Loads of pattern", id: "dpLoadPat" }),
        F.check(cut, "on", { label: "Clip the model at a section cut plane", id: "dpCutOn", onChange: v => { if (v && cut.coord == null) { cut.coord = mid(cut.axis); F.refresh(); } } }),
        F.select(cut, "axis", [["x", "X"], ["y", "Y"], ["z", "Z"]], { label: "Plane normal", id: "dpCutAxis", onChange: v => { cut.coord = mid(v); F.refresh(); } }),
        F.num(cut, "coord", { label: "Plane at", kind: "length", id: "dpCutCoord", allowNull: true, nullText: "model centre" }),
        F.select(cut, "keep", [["below", "Keep ≤ plane"], ["above", "Keep ≥ plane"]], { label: "Keep", id: "dpCutKeep" }),
      ]),
    ]);

    const tabs = dpTabs([
      { id: "objects", label: "Objects", body: tabObjects },
      { id: "view", label: "Object View Options", body: tabView },
      { id: "other", label: "Other", body: tabOther },
      { id: "filter", label: "Show Only", body: tabFilter },
    ], { id: "dpTabs", active: tab || "objects" });

    const allWin = dpEl("input", { type: "checkbox", id: "dpAllWin" });
    const allWinLab = dpEl("label", { class: "dk-check dp-allwin", title: "Apply these options to the plan, elevation and 3D windows and every pane" }, [allWin, dpEl("span", { text: "Apply to all windows" })]);
    const saveDef = dpEl("button", { type: "button", class: "btn btn-small", id: "dpSaveDefault", title: "New windows and Reset start from these options" }, ["Save as Default"]);
    const reset = dpEl("button", { type: "button", class: "btn btn-small", id: "dpReset", title: "Back to the saved default (or the factory default)" }, ["Reset"]);
    const extra = [allWinLab, saveDef, reset];
    if (is3d && sky.vx && sky.vx.openLegacyDisplayOptions) {
      const legacy = dpEl("button", { type: "button", class: "btn btn-small", id: "dpLegacy" }, ["Legacy dialog…"]);
      legacy.addEventListener("click", () => { dlg.close(); sky.vx.openLegacyDisplayOptions(); });
      extra.push(legacy);
    }
    const commit = () => {
      const s = DPC.dpNormalize(D);
      const targets = allWin.checked ? windowsNow() : [win];
      for (const w of targets) if (allWin.checked || !DPC.dpEqual(s, get(w))) set(w, s);
      if (allWin.checked) toast("Display options", `Applied to ${targets.length} windows.`);
    };
    const dlg = dpDialog({
      id: DP_DLG, title: "Set Display Options", size: "lg", iconId: "menu-display",
      intro: `Display options of the <b>${winLabel(win)}</b>. Each window keeps its own options (ETABS View › Set Display Options, Ctrl+W).`,
      body: [head, tabs], tabs, form: F, extra,
      onOk: () => { commit(); },
      onApply: () => { commit(); },
    });
    saveDef.addEventListener("click", () => {
      store.default = DPC.dpNormalize(D);
      persist();
      dlg.setNote("Saved as the default display options.");
      toast("Display options", "Saved as default — new windows and Reset use these options.");
    });
    reset.addEventListener("click", () => {
      const d = baseDefault();
      for (const k of Object.keys(d)) D[k] = d[k];
      // the check / select bindings hold the old section objects: rebuild on the reset draft
      dlg.close();
      set(win, d);
      open(win, tabs.current());
    });
    return dlg;
  }

  /* ================================================================ toolbar */
  const bars = [];
  function buildBar(host, where, before) {
    if (!host) return null;
    const bar = dpEl("div", { class: "dp-bar", role: "toolbar", "aria-label": "Display options", "data-dp-bar": where });
    const chip = (id, text, title, fn) => { const b = dpEl("button", { type: "button", class: "chip dp-chip", "data-dp-chip": id, title }, [text]); b.addEventListener("click", fn); bar.appendChild(b); return b; };
    if (where !== "3d") chip("extrude", "Extrude", "Extruded 3D view (Alt+X) — opens the 3D view", () => {
      if (sky.etabs && sky.etabs.hasItem && sky.etabs.hasItem("vx-extrude")) sky.etabs.clickItem("vx-extrude");
    });
    const both = (keys) => () => { const w = activeWin(); const s = get(w); const on = !keys.every(k => s.labels[k]); patch(w, { labels: Object.fromEntries(keys.map(k => [k, on])) }); };
    chip("labels", "Labels", "Object labels (frames, shells, links) in the active window", both(["frameName", "shellName", "linkName"]));
    chip("sections", "Sections", "Section names in the active window", both(["frameSection", "shellSection"]));
    chip("axes", "Axes", "Local axes 1 / 2 / 3 (red / green / blue) in the active window", both(["frameAxes", "shellAxes"]));
    const sel = dpEl("select", { class: "mini-select dp-colorby", "data-dp-chip": "colorby", title: "Colour by (active window)", "aria-label": "Colour by" });
    for (const [k, l] of DPC.DP_COLOR_BY) sel.appendChild(dpEl("option", { value: k }, [l]));
    sel.addEventListener("change", () => patch(activeWin(), { view: { colorBy: sel.value } }));
    bar.appendChild(sel);
    chip("open", "Display…", "View › Set Display Options… (Ctrl+W / Alt+W)", () => open());
    if (before && before.parentNode === host) host.insertBefore(bar, before); else host.appendChild(bar);
    bars.push({ bar, where });
    return bar;
  }
  function syncBars() {
    const w = activeWin();
    const s = get(w);
    for (const { bar, where } of bars) {
      const sw = where === "3d" ? get("3d") : s;
      const on = { extrude: get("3d").view.extrude, labels: sw.labels.frameName || sw.labels.shellName, sections: sw.labels.frameSection || sw.labels.shellSection, axes: sw.labels.frameAxes || sw.labels.shellAxes };
      for (const b of bar.querySelectorAll("button[data-dp-chip]")) {
        const k = b.dataset.dpChip;
        if (k in on) { b.classList.toggle("is-on", !!on[k]); b.setAttribute("aria-pressed", on[k] ? "true" : "false"); }
      }
      const sel = bar.querySelector("select");
      if (sel) sel.value = sw.view.colorBy;
      const op = bar.querySelector('[data-dp-chip="open"]');
      if (op) op.classList.toggle("is-on", !DPC.dpIsDefault(sw));
    }
  }
  const drawBar = document.querySelector("#drawMain .draw-toolbar");
  buildBar(drawBar, "2d", drawBar && drawBar.querySelector(".toolbar-spacer"));
  const vt = document.getElementById("viewerToolbar");
  const vxOpts = document.getElementById("vxChipOpts");
  if (vt) {
    const b3 = buildBar(vt, "3d", null);
    if (b3) {
      const anchor = vxOpts || document.getElementById("chipLabels");
      if (anchor && anchor.parentNode === vt) anchor.after(b3);
      // the 3D "Display…" chip opens the new dialog (the old one: Legacy dialog… in its footer)
      if (vxOpts) vxOpts.hidden = true;
    }
  }
  // "Labels" in the 3D toolbar means hover tooltips — say so (it is not the object-label option)
  const cl = document.getElementById("chipLabels");
  if (cl && cl.textContent.trim() === "Labels") cl.textContent = "Hover info";

  /* ================================================================ wiring */
  // the View menu command and the 3D API both open the new dialog
  if (sky.vx && sky.vx.openDisplayOptions) {
    sky.vx.openLegacyDisplayOptions = sky.vx.openDisplayOptions;   // the previous 3D-only dialog stays one click away
    sky.vx.openDisplayOptions = () => open();
  }

  document.addEventListener("sky:vx-changed", () => { persist(); apply3dExtras(); syncBars(); });
  document.addEventListener("sky:model-changed", () => setTimeout(syncBars, 0));
  document.addEventListener("sky:wb-windows", () => setTimeout(syncBars, 0));
  document.addEventListener("click", () => setTimeout(syncBars, 0), true);

  // first run: restore the stored 3D options when the session has none
  try {
    const st3 = store.windows["3d"] || store.default;
    if (st3 && sky.vx && sky.vx.opts && DPC.dpIsDefault(DPC.dpFromVx(sky.vx.opts()))) sky.vx.set(DPC.dpToVx(st3));
  } catch (e) { console.error(e); }
  wrapTendons3d();
  setTimeout(wrapTendons3d, 0);
  apply3dExtras();
  syncStoryBadge();
  if (sky.planEditor) sky.planEditor.refresh();

  // shortcut: Ctrl+W (ETABS) + Alt+W (browsers reserve Ctrl+W for "close tab")
  function installShortcut() {
    const sc = sky.ux && sky.ux.shortcuts;
    if (!sc || !sc.register) return;
    const keys = ["Ctrl+W", "Alt+W"].filter(k => !sc.list().some(s => (s.keys || []).includes(k)));
    if (keys.length) sc.register({ id: "dp-open", keys, desc: "Set Display Options (active window)", group: "View", run: () => open() });
  }
  setTimeout(installShortcut, 0);

  /* ================================================================ styles */
  if (!document.getElementById("dpStyles")) {
    const st = document.createElement("style");
    st.id = "dpStyles";
    st.textContent = `
.dp-head { display: flex; align-items: center; gap: 10px; margin: 0 0 8px; flex-wrap: wrap; }
.dp-head .dk-label { min-width: 0; }
.dp-head select { width: auto; min-width: 170px; }
.dp-head-note { font-size: 11.5px; }
.dp-checks { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 2px 12px; }
.dp-checks .dk-row { margin: 0; }
.dp-row-btns { display: flex; gap: 6px; justify-content: flex-end; margin-top: 4px; }
.dp-labcols { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px 16px; }
.dp-labhead { font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: var(--text-2, #8b98a8); margin: 2px 0 4px; }
.dp-legend { display: flex; flex-wrap: wrap; gap: 4px 12px; margin: 2px 0 8px; font-size: 11.5px; }
.dp-leg { display: inline-flex; align-items: center; gap: 5px; }
.dp-leg i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.dp-off { opacity: .55; }
.dp-allwin { margin-left: 4px; font-size: 12px; white-space: nowrap; }
.dp-bar { display: inline-flex; align-items: center; gap: 4px; margin: 0 6px; flex-wrap: nowrap; }
.dp-bar .mini-select { width: auto; max-width: 128px; }
.dp-chip { white-space: nowrap; }
svg[data-dp-nodims] [data-wb-dim] { display: none; }
svg[data-dp-notendon] .pt-plan, svg[data-dp-notendon] .pt-elev { display: none; }
@media (max-width: 720px) { .dp-labcols { grid-template-columns: 1fr; } .dp-bar .dp-chip[data-dp-chip="sections"] { display: none; } }`;
    document.head.appendChild(st);
  }

  syncBars();
  sky.dispOpts = {
    open, close: () => dpClose(DP_DLG), isOpen: () => dpIsOpen(DP_DLG),
    get, set, patch, activeWin, windows: windowsNow,
    saveDefault: s => { store.default = DPC.dpNormalize(s || get(activeWin())); persist(); },
    clearDefault: () => { store.default = null; persist(); },
    stored: () => lsRead(),
    lastError: () => lastErr,
    // named views (js/wb_views.js): plan / elevation options travel with the view
    captureView: kind => { const w = kind === "elev" ? "elev" : "plan"; const s = get(w); return DPC.dpIsDefault(s) ? null : { [w]: DPC.dpDiff(s) }; },
    restoreView: (kind, dw) => { const w = kind === "elev" ? "elev" : "plan"; set(w, dw && dw[w] ? DPC.dpMerge(DPC.dpDefaults(), dw[w]) : get(w)); },
    core: DPC,
  };
}
