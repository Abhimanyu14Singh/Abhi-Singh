/* SkyFrame Wave G — the shared results overlay layer (drawing).

   Every view subscribes to the one results model of js/g_results.js and
   draws it with its OWN display choice (sky.results.viewOpts(id)):

     main 3D view      id "main3d"  Viewer3D (Classic canvas or GPU)
     plan / elevation  ids "plan" / "elev"  (a canvas laid over the SVG
                       editor, repainted with its pan / zoom)
     View › Windows    ids "pane0".."pane3" (3D panes and plan / elevation
                       panes alike)

   Drawn per view:
     deformed shape    3D: the viewer's own deformed pipeline (both
                       renderers; GPU = displacement attribute × scale
                       uniform) fed through its grpCaseData / autoScale
                       hooks, so modes, steps and envelopes animate with one
                       fixed scale; 2D: the in-plane projection (cubic or
                       straight chords, wire shadow);
     frame diagrams    P V2 V3 T M2 M3 (+ in-plane V / M in 2D views, the
                       ETABS rule) — fill / values on diagram / values at
                       controlling stations; max / min pairs as two curves;
                       auto (fixed over the steps) or user scale;
     contours          joint displacement components on objects or on the
                       deformed shape (GPU: per-vertex displacement +
                       colour attributes, js/gpu/renderer.js hook; Classic:
                       painter-sorted cells); shell force / stress contours
                       of js/shellres.js on slabs in plan, walls in
                       elevation, 3D panes and on the deformed shape;
     reactions, pier / spandrel / link diagrams, hinge states (js/uhresults
                       overlay installed on the panes and stepped with the
                       toolbar); tower / group filter per view.
   Values go through js/units.js. Nothing in the model changes. */

import gOU from "./units.js";
import * as gO from "./g_core.js";
import { frameLocalAxes as gOFrameAxes } from "./frameaxes.js";
import { wcPierGeometry as gOPierGeo, wcSpandrelGeometry as gOSpandrelGeo } from "./wc_diagrams.js";
import { srColor as gOSrColor } from "./shellres.js";

const GKEY = "__gres__";
const POS = "rgba(53,181,229,", NEG = "rgba(229,103,103,";

export function gInitOverlay(sky, api) {
  const S = sky.store;
  const attached = new WeakSet();
  const results = () => S.results || null;
  const model = () => S.model || null;

  /* ================================================================ helpers */
  let axesMemo = { m: null, map: new Map() };
  function axesOf(mm) {
    const m = model();
    if (axesMemo.m !== m) axesMemo = { m, map: new Map() };
    let a = axesMemo.map.get(mm.uid);
    if (!a) {
      try { const r = gOFrameAxes(mm, m); a = { x: r.x, y: r.y, z: r.z }; } catch { a = gO.defaultAxes(mm.pi, mm.pj); }
      axesMemo.map.set(mm.uid, a);
    }
    return a;
  }
  let rmMemo = { r: null, byUid: new Map(), nidx: null };
  function rmOf(uid) {
    const r = results();
    if (rmMemo.r !== r) { rmMemo = { r, byUid: new Map(((r && r.members) || []).map(x => [x.uid, x])), nidx: null }; }
    return rmMemo.byUid.get(uid) || null;
  }
  function nodeIdx() { const r = results(); if (rmMemo.r !== r) rmOf(""); if (!rmMemo.nidx) rmMemo.nidx = gO.nodeIndex(r); return rmMemo.nidx; }
  const ok = p => p && isFinite(p.x) && isFinite(p.y);
  function text(ctx, s, x, y, { align = "center", base = "bottom", color = "rgba(232,237,243,0.95)", bg = true } = {}) {
    ctx.textAlign = align; ctx.textBaseline = base;
    if (bg) {
      const w = s.length * 5.7 + 6;                       // approximate width (no measureText per label)
      const x0 = align === "center" ? x - w / 2 : align === "right" ? x - w + 3 : x - 3;
      const y0 = base === "bottom" ? y - 12 : base === "top" ? y - 1 : y - 7;
      ctx.fillStyle = "rgba(13,17,23,0.62)";
      ctx.fillRect(x0, y0, w, 13);
    }
    ctx.fillStyle = color;
    ctx.fillText(s, x, y);
  }
  const fmtV = (kind, v) => gOU.fmt(kind, v, kind === "rotation" ? 5 : 2);
  function filterOf(view) { return gO.objectFilter(model(), { towers: view.opts.towers, group: view.opts.group }); }

  /* ================================================================ deformed (3D) */
  function visibleNodes(view) {
    const r = results(), m = model();
    const flt = filterOf(view);
    if (!flt.active || !r) return null;
    const set = new Set();
    const byUid = new Map((m.members || []).map(x => [x.uid, x]));
    for (const rm of r.members || []) { const mm = byUid.get(rm.uid); if (mm && flt.member(mm)) { set.add(String(rm.ni)); set.add(String(rm.nj)); } }
    const shells = new Map((m.shells || []).map(s => [s.uid, s]));
    for (const q of r.shell_quads || []) { const sh = shells.get(q.region); if (sh && flt.shell(sh)) for (const t of q.nodes) set.add(String(t)); }
    return set;
  }
  function deformedFactor(view, R) {
    const o = view.opts;
    if (o.dscaleMode === "user") return +o.dscaleUser || 1;
    const ref = api.deformedRef();
    return ref > 1e-15 ? R * 0.06 * (+o.dscale || 1) / ref : 1;
  }
  function applyDeformed(view) {
    const v = view.obj, st = api.state();
    const on = !!(view.opts.deformed && st && st.node_disp && results() && v.results);
    if (on) {
      const src = st.node_disp;
      const factor = deformedFactor(view, v.radius || 10);
      const fsig = JSON.stringify([view.opts.towers, view.opts.group]);
      const c = view.cache;
      if (src !== c.dsrc || factor !== c.dfactor || fsig !== c.fsig || !c.dmap) {
        const keep = visibleNodes(view);
        const dm = {};
        for (const [t, d] of Object.entries(src)) if (!keep || keep.has(String(t))) dm[t] = d;
        c.dmap = dm; c.dsrc = src; c.dfactor = factor; c.fsig = fsig;
      }
      const ph = api.phase();
      view.deformedOn = true;
      view.so({ deformed: true, modal: false, buckling: false, caseName: GKEY, scaleMult: Math.abs(ph) < 1e-6 ? 1e-6 : ph });
    } else {
      view.deformedOn = false;
      view.cache.dmap = null;
      if (v.overlay && v.overlay.caseName === GKEY) view.so({ deformed: false, caseName: S.caseName });
    }
  }
  function yieldLegacy() {
    const o = S.overlay;
    if (o && (o.deformed || o.modal || o.buckling)) {
      o.deformed = false; o.modal = false; o.buckling = false;
      try { sky.syncOverlayUI && sky.syncOverlayUI(); } catch { /* ignore */ }
    }
  }

  /* ================================================================ attach: 3D */
  function attach3d(v, id) {
    if (!v || attached.has(v)) { if (v) api.registerView(id, "3d", v); return; }
    attached.add(v);
    const base = id !== "main3d" ? api.view("main3d") : null;
    const view = api.registerView(id, "3d", v);
    if (base && !view.copied) { Object.assign(view.opts, JSON.parse(JSON.stringify(base.opts))); view.copied = true; }
    view.cache = { dmap: null, dsrc: null, dfactor: 1, fsig: "" };
    view.deformedOn = false;
    view.so = v.setOverlay.bind(v);
    const prevCase = v.grpCaseData;
    v.grpCaseData = (name, r) => (name === GKEY ? (view.cache.dmap ? { node_disp: view.cache.dmap } : null) : (prevCase ? prevCase(name, r) : null));
    const as0 = v.autoScale.bind(v);
    v.autoScale = dm => (dm && dm === view.cache.dmap ? view.cache.dfactor : as0(dm));
    const dp0 = v._deformedPolyline.bind(v);
    v._deformedPolyline = (Pi, Pj, di, dj, f) => (view.deformedOn && !view.opts.cubic && v.overlay.caseName === GKEY
      ? gO.deformedPolyline(Pi, Pj, di, dj, f, false) : dp0(Pi, Pj, di, dj, f));
    v.setOverlay = o => {
      o = o || {};
      if (view.deformedOn && (o.modal || o.buckling || (o.deformed && o.caseName && o.caseName !== GKEY))) {
        view.opts.deformed = false; view.deformedOn = false; view.cache.dmap = null;
        setTimeout(() => api.redraw(), 0);
      }
      if (!view.deformedOn && o.caseName === GKEY) o = { ...o, deformed: false, caseName: S.caseName };
      view.so(o);
      if (view.deformedOn) applyDeformed(view);
    };
    v.gResOverlay = (ctx, P, viewer) => draw3d(ctx, P, viewer, view);
    v.gResGL = (be, G) => syncGL(be, G, v, view);
    view.redraw = () => { syncExtras(view); applyDeformed(view); v._dirty = true; };
    view.animate = () => { applyDeformed(view); v._dirty = true; };
    view.optsChanged = () => { if (view.opts.deformed && id === "main3d") yieldLegacy(); };
    view.redraw();
  }
  /** Pane extras: shell contours (js/shellres.js ext) and hinge states (js/uhdlg.js). */
  function syncExtras(view) {
    const v = view.obj, main = sky.viewer;
    if (!main || v === main) return;
    v.shellResExt = view.opts.shell && main.shellResExt ? main.shellResExt : null;
    v.uhOverlay = view.opts.hinges && main.uhOverlay ? main.uhOverlay : null;
  }

  /* ================================================================ attach: 2D */
  function attach2d(ed, id, kind) {
    if (!ed || attached.has(ed)) { if (ed) api.registerView(id, kind, ed); return; }
    attached.add(ed);
    const base = id.startsWith("pane") ? api.view(kind) : null;
    const view = api.registerView(id, kind, ed);
    if (base && !view.copied) { Object.assign(view.opts, JSON.parse(JSON.stringify(base.opts))); view.copied = true; }
    const cv = document.createElement("canvas");
    cv.className = "g-res-canvas";
    cv.setAttribute("aria-hidden", "true");
    cv.dataset.gView = id;
    cv.style.cssText = "position:absolute;left:0;top:0;width:0;height:0;pointer-events:none;z-index:2;";
    ed.svg.insertAdjacentElement("afterend", cv);
    view.canvas = cv;
    const at0 = ed._applyTransform.bind(ed);
    ed._applyTransform = () => { at0(); try { draw2d(view); } catch (e) { /* never break the editor */ } };
    view.redraw = () => draw2d(view);
    view.animate = () => draw2d(view);
    draw2d(view);
  }

  /* ================================================================ panes */
  function syncPanes() {
    const W = sky.wb && sky.wb.windows;
    const panes = W && W.panes ? W.panes() : [];
    panes.forEach((p, i) => {
      const id = "pane" + i;
      if (p.v) attach3d(p.v, id);
      else if (p.ed) attach2d(p.ed, id, p.kind === "elev" ? "elev" : "plan");
      const view = api.view(id);
      if (view && view.obj !== (p.v || p.ed)) { if (p.v) { attached.delete(p.v); attach3d(p.v, id); } else if (p.ed) { attached.delete(p.ed); attach2d(p.ed, id, p.kind === "elev" ? "elev" : "plan"); } }
    });
    for (const id of api.viewIds()) if (/^pane\d+$/.test(id) && +id.slice(4) >= panes.length) api._R.views.delete(id);
  }

  /* ================================================================ 3D draw */
  function draw3d(ctx, P, viewer, view) {
    const st = api.state();
    if (!st || !results() || !model()) return;
    const cam = {
      kind: "3d", b: null, P, R: viewer.radius || 10, gpu: !!(viewer.gpu && viewer.gpu.on), w: viewer.canvas.clientWidth, h: viewer.canvas.clientHeight,
      proj: p => { const c = P.toCam(p); return c[2] > P.near ? P.proj(c) : null; },
      depth: p => P.toCam(p)[2],
      inView: () => true,
      factor: view.deformedOn ? view.cache.dfactor * (Math.abs(api.phase()) < 1e-6 ? 1e-6 : api.phase()) : 0,
    };
    ctx.save();
    try { drawCommon(ctx, cam, view, st, viewer); } finally { ctx.restore(); }
  }

  /* ================================================================ 2D draw */
  function planZ(ed) {
    const m = model(), name = ed.opts && ed.opts.getStory ? ed.opts.getStory() : S.story;
    const s = ((m && m.stories) || []).find(x => x.name === name);
    return s ? +s.elevation : null;
  }
  function draw2d(view) {
    const ed = view.obj, cv = view.canvas;
    if (!cv || !ed || !ed.svg) return;
    const svg = ed.svg;
    const visible = svg.getClientRects().length > 0 && !svg.classList.contains("hidden");
    const st = visible ? api.state() : null;
    const o = view.opts;
    const wants = o.deformed || o.diagram || o.contour || o.reactions || o.piers || o.spandrels || o.links || o.shell;
    const ctx = cv.getContext("2d");
    if (!visible || !st || !results() || !wants) {
      if (cv.width) { cv.width = 0; cv.height = 0; cv.style.width = "0px"; cv.style.height = "0px"; }
      view.drawn = null;
      return;
    }
    const w = svg.clientWidth || svg.getBoundingClientRect().width, h = svg.clientHeight || svg.getBoundingClientRect().height;
    const dpr = window.devicePixelRatio || 1;
    cv.style.left = svg.offsetLeft + "px"; cv.style.top = svg.offsetTop + "px";
    cv.style.width = w + "px"; cv.style.height = h + "px";
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    let cam;
    if (view.kind === "plan") {
      const z = planZ(ed);
      if (z == null) return;
      const b = gO.viewBasis({ kind: "plan", z });
      cam = { kind: "plan", b, z, proj: p => { const s = ed.toScreen(p[0], p[1]); return { x: s[0], y: s[1] }; } };
    } else {
      const pl = ed.plane && ed.plane();
      if (!pl) return;
      const b = gO.viewBasis(pl.dir ? { kind: "elev", dir: pl.dir, origin: pl.origin } : { kind: "elev", axis: pl.axis, coord: pl.coord });
      cam = { kind: "elev", b, proj: p => { const s = ed.toScreen(ed.sOf(p), p[2]); return { x: s[0], y: s[1] }; } };
    }
    cam.w = w; cam.h = h; cam.gpu = false; cam.depth = () => 0;
    cam.inView = (pi, pj) => gO.memberInView(cam.b, pi, pj, 1e-3);
    cam.R = viewRadius(view.kind);
    cam.factor = o.deformed ? deformedFactor(view, cam.R) * (api.phase()) : 0;
    ctx.save();
    try { drawCommon(ctx, cam, view, st, null); } finally { ctx.restore(); }
  }
  let radMemo = { m: null, plan: 10, elev: 10 };
  function viewRadius(kind) {
    const m = model();
    if (radMemo.m !== m) {
      let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
      for (const mm of (m && m.members) || []) for (const p of [mm.pi, mm.pj]) for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], p[i]); hi[i] = Math.max(hi[i], p[i]); }
      for (const sh of (m && m.shells) || []) for (const p of sh.corners) for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], p[i]); hi[i] = Math.max(hi[i], p[i]); }
      const d = i => (isFinite(hi[i] - lo[i]) ? hi[i] - lo[i] : 10);
      radMemo = { m, plan: Math.max(d(0), d(1), 1) / 2, elev: Math.max(d(0), d(1), d(2), 1) / 2 };
    }
    return kind === "plan" ? radMemo.plan : radMemo.elev;
  }

  /* ================================================================ common */
  function drawCommon(ctx, cam, view, st, viewer) {
    const o = view.opts;
    const drawn = { deformed: 0, diagrams: 0, cells: 0, reactions: 0, piers: 0, spandrels: 0, links: 0, shells: 0, labels: 0 };
    ctx.font = "600 10px -apple-system, 'Segoe UI', sans-serif";
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    if (cam.kind !== "3d" && o.deformed && st.node_disp) drawDeformed2d(ctx, cam, view, st, drawn);
    let legend = null;
    if (o.contour && st.node_disp) legend = drawDispContour(ctx, cam, view, st, drawn);
    if (o.shell) drawShellContour(ctx, cam, view, st, drawn, viewer);
    if (o.diagram && (st.member_stations || st.member_forces)) drawDiagrams(ctx, cam, view, st, drawn);
    if (o.reactions && st.reactions) drawReactions(ctx, cam, view, st, drawn);
    if (o.piers && st.piers) drawPiers(ctx, cam, view, st, drawn);
    if (o.spandrels && st.spandrels) drawSpandrels(ctx, cam, view, st, drawn);
    if (o.links && st.node_disp) drawLinks(ctx, cam, view, st, drawn);
    drawTitle(ctx, cam, view, st, legend);
    view.drawn = drawn;
  }

  function drawTitle(ctx, cam, view, st, legend) {
    const o = view.opts;
    if (!(o.deformed || o.diagram || o.contour || o.reactions || o.piers || o.spandrels || o.links || o.shell)) return;
    const parts = [st.label];
    if (st.stepLabel) parts.push(st.stepLabel);
    if (o.diagram) parts.push(gO.COMP_LABEL[o.diagram] || o.diagram);
    if (o.contour) parts.push(o.contour + " contour");
    const s = parts.join("  ·  ");
    ctx.font = "650 11px -apple-system, 'Segoe UI', sans-serif";
    text(ctx, s, cam.w / 2, 8, { base: "top", color: "rgba(232,237,243,0.98)" });
    if (st.note) { ctx.font = "500 10px -apple-system, 'Segoe UI', sans-serif"; text(ctx, st.note, cam.w / 2, 24, { base: "top", color: "rgba(224,176,80,0.95)" }); }
    if (legend) drawLegend(ctx, cam, legend);
  }
  function drawLegend(ctx, cam, L) {
    const x = 12, y = cam.kind === "3d" ? 44 : 12, w = 150, h = 9;
    ctx.save();
    ctx.fillStyle = "rgba(17,22,29,0.86)";
    ctx.fillRect(x - 6, y - 16, w + 12, h + 34);
    ctx.font = "600 10px -apple-system, 'Segoe UI', sans-serif";
    ctx.fillStyle = "rgba(232,237,243,0.95)"; ctx.textAlign = "left"; ctx.textBaseline = "bottom";
    ctx.fillText(L.title, x, y - 3);
    for (let i = 0; i < w; i++) {
      const c = gO.rampRGB(i / (w - 1), L.palette);
      ctx.fillStyle = `rgb(${c[0]},${c[1]},${c[2]})`; ctx.fillRect(x + i, y, 1, h);
    }
    ctx.fillStyle = "rgba(200,208,218,0.95)"; ctx.textBaseline = "top";
    ctx.fillText(L.lo, x, y + h + 2);
    ctx.textAlign = "right"; ctx.fillText(L.hi, x + w, y + h + 2);
    ctx.restore();
  }

  /* ---------------------------------------------------------------- 2D deformed */
  function drawDeformed2d(ctx, cam, view, st, drawn) {
    const r = results(), m = model(), o = view.opts, flt = filterOf(view);
    const nd = st.node_disp, f = cam.factor;
    const lines = [];
    for (const mm of m.members || []) {
      if (!flt.member(mm) || !cam.inView(mm.pi, mm.pj)) continue;
      const rm = rmOf(mm.uid);
      if (!rm) continue;
      const Pi = r.nodes[rm.ni], Pj = r.nodes[rm.nj], di = nd[rm.ni], dj = nd[rm.nj];
      if (!Pi || !Pj || !di || !dj) continue;
      lines.push({ Pi, Pj, pts: gO.deformedPolyline(Pi, Pj, di, dj, f, o.cubic) });
    }
    if (o.wire) {
      ctx.strokeStyle = "rgba(154,167,180,0.45)"; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
      ctx.beginPath();
      for (const L of lines) { const a = cam.proj(L.Pi), b = cam.proj(L.Pj); if (ok(a) && ok(b)) { ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); } }
      ctx.stroke(); ctx.setLineDash([]);
    }
    // shells in the plane (slabs in plan / walls in elevation)
    const shells = new Map((m.shells || []).map(s => [s.uid, s]));
    ctx.lineWidth = 0.9;
    for (const q of r.shell_quads || []) {
      const sh = shells.get(q.region);
      if (sh && !flt.shell(sh)) continue;
      const P0 = q.nodes.map(t => r.nodes[t]);
      if (P0.some(p => !p) || !P0.every(p => gO.onViewPlane(cam.b, p, 1e-3))) continue;
      const pts = q.nodes.map((t, i) => { const d = nd[t] || [0, 0, 0]; return cam.proj([P0[i][0] + f * d[0], P0[i][1] + f * d[1], P0[i][2] + f * d[2]]); });
      if (pts.some(p => !ok(p))) continue;
      ctx.beginPath(); pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y))); ctx.closePath();
      ctx.fillStyle = "rgba(53,181,229,0.10)"; ctx.fill();
      ctx.strokeStyle = "rgba(53,181,229,0.35)"; ctx.stroke();
    }
    ctx.strokeStyle = "#35b5e5"; ctx.lineWidth = 1.8;
    ctx.beginPath();
    for (const L of lines) {
      const sp = L.pts.map(cam.proj);
      if (sp.some(p => !ok(p))) continue;
      sp.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      drawn.deformed++;
    }
    ctx.stroke();
    // plan: displaced column tops at this level
    if (cam.kind === "plan") {
      ctx.fillStyle = "#35b5e5";
      for (const mm of m.members || []) {
        if (mm.kind !== "column" || !flt.member(mm)) continue;
        const top = mm.pi[2] > mm.pj[2] ? mm.pi : mm.pj;
        if (Math.abs(top[2] - cam.z) > 1e-3) continue;
        const rm = rmOf(mm.uid);
        if (!rm) continue;
        const t = mm.pi[2] > mm.pj[2] ? rm.ni : rm.nj, d = nd[t];
        if (!d) continue;
        const p = cam.proj([top[0] + f * d[0], top[1] + f * d[1], top[2]]);
        if (ok(p)) ctx.fillRect(p.x - 3, p.y - 3, 6, 6);
      }
    }
  }

  /* ---------------------------------------------------------------- displacement contours */
  /** Node tags drawn by a view (2D: on the plane; 3D: the tower / group filter). */
  function viewNodeSet(view, cam) {
    const r = results(), m = model();
    if (!cam || cam.kind === "3d") return visibleNodes(view);
    const flt = filterOf(view), set = new Set();
    const byUid = new Map((m.members || []).map(x => [x.uid, x]));
    for (const rm of r.members || []) {
      const mm = byUid.get(rm.uid);
      if (mm && flt.member(mm) && cam.inView(mm.pi, mm.pj)) { set.add(String(rm.ni)); set.add(String(rm.nj)); }
    }
    for (const q of r.shell_quads || []) {
      const P0 = q.nodes.map(t => r.nodes[t]);
      if (P0.every(p => p && gO.onViewPlane(cam.b, p, 1e-3))) for (const t of q.nodes) set.add(String(t));
    }
    return set;
  }
  function dispContourData(view, st, cam) {
    const o = view.opts, r = results();
    const c = view.cdata || (view.cdata = {});
    const pk = cam && cam.b ? JSON.stringify([cam.b.kind, cam.b.z, cam.b.coord, cam.b.n]) : "3d";
    const sig = [st.node_disp, o.contour, JSON.stringify(o.crange), o.palette, r, pk, JSON.stringify([o.towers, o.group])];
    if (c.sig && sig.every((x, i) => x === c.sig[i])) return c;
    const vals = {};
    const keep = viewNodeSet(view, cam);
    const arr = [];
    for (const [t, d] of Object.entries(st.node_disp || {})) {
      const v = gO.dispValue(d, o.contour);
      vals[t] = v;
      if (v != null && (!keep || keep.has(String(t)))) arr.push(v);
    }
    const signed = !/^(U|UXY)$/.test(o.contour);
    const range = gO.rangeOf(arr, Array.isArray(o.crange) && o.crange.length === 2 ? o.crange : null, signed);
    Object.assign(c, { sig, vals, range, kind: gO.dispKind(o.contour) });
    return c;
  }
  function drawDispContour(ctx, cam, view, st, drawn) {
    const o = view.opts, r = results(), m = model(), flt = filterOf(view);
    const cd = dispContourData(view, st, cam);
    const legend = { title: `${o.contour} (${gOU.label(cd.kind === "rotation" ? "rotation" : "disp")})`, lo: fmtV(cd.kind === "rotation" ? "rotation" : "disp", cd.range[0]), hi: fmtV(cd.kind === "rotation" ? "rotation" : "disp", cd.range[1]), palette: o.palette };
    if (cam.kind === "3d" && cam.gpu) return legend;            // the GPU layer draws the cells
    const nd = st.node_disp;
    const def = o.onDeformed && (cam.kind === "3d" ? view.deformedOn : o.deformed) ? cam.factor : 0;
    const pos = (p, t) => { const d = def ? nd[t] : null; return d ? [p[0] + def * d[0], p[1] + def * d[1], p[2] + def * d[2]] : p; };
    const items = [];
    const shells = new Map((m.shells || []).map(s => [s.uid, s]));
    const SUB = 2;
    for (const q of r.shell_quads || []) {
      const sh = shells.get(q.region);
      if (sh && !flt.shell(sh)) continue;
      const P0 = q.nodes.map(t => r.nodes[t]);
      if (P0.some(p => !p)) continue;
      if (cam.kind !== "3d" && !P0.every(p => gO.onViewPlane(cam.b, p, 1e-3))) continue;
      const W = q.nodes.map((t, i) => pos(P0[i], t));
      const vv = q.nodes.map(t => cd.vals[t]);
      if (vv.some(v => v == null)) continue;
      const z = cam.depth(W.reduce((a, p) => [a[0] + p[0] / W.length, a[1] + p[1] / W.length, a[2] + p[2] / W.length], [0, 0, 0]));
      if (W.length === 4) {
        const lerp = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
        const at = (u, v) => [lerp(lerp(W[0], W[1], u), lerp(W[3], W[2], u), v), (1 - u) * (1 - v) * vv[0] + u * (1 - v) * vv[1] + u * v * vv[2] + (1 - u) * v * vv[3]];
        for (let a = 0; a < SUB; a++) for (let b = 0; b < SUB; b++) {
          const c4 = [at(a / SUB, b / SUB), at((a + 1) / SUB, b / SUB), at((a + 1) / SUB, (b + 1) / SUB), at(a / SUB, (b + 1) / SUB)];
          items.push({ z, pts: c4.map(x => x[0]), v: (c4[0][1] + c4[1][1] + c4[2][1] + c4[3][1]) / 4 });
        }
      } else items.push({ z, pts: W, v: vv.reduce((a, x) => a + x, 0) / vv.length });
    }
    items.sort((a, b) => b.z - a.z);
    for (const it of items) {
      const sp = it.pts.map(cam.proj);
      if (sp.some(p => !ok(p))) continue;
      ctx.beginPath(); sp.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y))); ctx.closePath();
      ctx.fillStyle = gO.valueColor(it.v, cd.range, o.palette); ctx.globalAlpha = 0.92; ctx.fill(); ctx.globalAlpha = 1;
      ctx.strokeStyle = ctx.fillStyle; ctx.lineWidth = 0.6; ctx.stroke();
      drawn.cells++;
    }
    // frames: coloured sub-segments (end values interpolated), batched into 64 colour buckets
    ctx.lineWidth = cam.kind === "3d" ? 3 : 3.5;
    const NB = 64, buckets = new Map();
    const seg = (a, b, v) => {
      const k = Math.round(gO.tOf(v, cd.range) * (NB - 1));
      let P = buckets.get(k);
      if (!P) { P = new Path2D(); buckets.set(k, P); }
      P.moveTo(a.x, a.y); P.lineTo(b.x, b.y);
    };
    for (const mm of m.members || []) {
      if (!flt.member(mm)) continue;
      if (cam.kind !== "3d" && !cam.inView(mm.pi, mm.pj)) continue;
      const rm = rmOf(mm.uid);
      if (!rm) continue;
      const Pi = r.nodes[rm.ni], Pj = r.nodes[rm.nj], vi = cd.vals[rm.ni], vj = cd.vals[rm.nj];
      if (!Pi || !Pj || vi == null || vj == null) continue;
      const di = nd[rm.ni] || [0, 0, 0, 0, 0, 0], dj = nd[rm.nj] || [0, 0, 0, 0, 0, 0];
      const pts = def ? gO.deformedPolyline(Pi, Pj, di, dj, def, o.cubic) : gO.deformedPolyline(Pi, Pj, di, dj, 0, false);
      const sp = pts.map(cam.proj);
      for (let k = 0; k < sp.length - 1; k++) {
        if (!ok(sp[k]) || !ok(sp[k + 1])) continue;
        seg(sp[k], sp[k + 1], vi + (vj - vi) * (k + 0.5) / (sp.length - 1));
      }
      drawn.cells++;
    }
    for (const [k, P] of buckets) { const c = gO.rampRGB(k / (NB - 1), o.palette); ctx.strokeStyle = `rgb(${c[0]},${c[1]},${c[2]})`; ctx.stroke(P); }
    return legend;
  }

  /* ---------------------------------------------------------------- shell contours */
  function drawShellContour(ctx, cam, view, st, drawn, viewer) {
    const sr = sky.shellRes && sky.shellRes.state;
    if (!sr || !sr.on || !sr.data || sr.results !== results()) return;
    const r = results(), m = model(), flt = filterOf(view);
    const d = sr.data;
    const shells = new Map((m.shells || []).map(s => [s.uid, s]));
    if (cam.kind === "3d") {
      // contours on the deformed shape (Classic): the viewer draws them undeformed only
      if (!view.deformedOn || cam.gpu || !viewer || !viewer.shellResExt) return;
      const f = cam.factor, nd = st.node_disp || {};
      const xyz = {};
      for (const [t, p] of Object.entries(r.nodes || {})) { const dd = nd[t]; xyz[t] = dd ? [p[0] + f * dd[0], p[1] + f * dd[1], p[2] + f * dd[2]] : p; }
      const items = [];
      try { viewer.shellResExt.items(items, cam.P, xyz, r); } catch { return; }
      items.sort((a, b) => b.z - a.z);
      for (const it of items) { try { it.draw(ctx); drawn.shells++; } catch { ctx.globalAlpha = 1; } }
      return;
    }
    r.shell_quads.forEach((q, i) => {
      const sh = shells.get(q.region);
      if (sh && !flt.shell(sh)) return;
      const P0 = q.nodes.map(t => r.nodes[t]);
      if (P0.some(p => !p) || !P0.every(p => gO.onViewPlane(cam.b, p, 1e-3))) return;
      const sp = P0.map(cam.proj);
      if (sp.some(p => !ok(p))) return;
      ctx.beginPath(); sp.forEach((p, k) => (k ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y))); ctx.closePath();
      ctx.fillStyle = gOSrColor(sr, d.values ? d.values[i] : null); ctx.globalAlpha = 0.9; ctx.fill(); ctx.globalAlpha = 1;
      ctx.strokeStyle = "rgba(13,17,23,0.45)"; ctx.lineWidth = 0.6; ctx.stroke();
      drawn.shells++;
    });
  }

  /* ---------------------------------------------------------------- frame diagrams */
  function stationsFromForces(mm, f, L) {
    // linear between the end forces (stage states carry end forces only)
    const xs = [0, L];
    const s = { x: xs };
    const map = { N: [0, 6], V2: [1, 7], V3: [2, 8], T: [3, 9], M2: [4, 10], M3: [5, 11] };
    for (const [k, [a, b]] of Object.entries(map)) s[k] = [-(f[a] || 0), f[b] || 0];
    return s;
  }
  function drawDiagrams(ctx, cam, view, st, drawn) {
    const o = view.opts, m = model(), flt = filterOf(view);
    let comp = o.diagram;
    if (cam.kind === "3d" && (comp === "MIN" || comp === "VIN")) comp = comp === "MIN" ? "M3" : "V2";
    const smax = st.member_stations || {}, smin = st.member_stations_min || null;
    const list = [];
    for (const mm of m.members || []) {
      if (!flt.member(mm)) continue;
      if (cam.kind !== "3d" && !cam.inView(mm.pi, mm.pj)) continue;
      let s = smax[mm.uid];
      if (!s && st.member_forces && st.member_forces[mm.uid]) s = stationsFromForces(mm, st.member_forces[mm.uid], gO.V3.len(gO.V3.sub(mm.pj, mm.pi)));
      if (!s || !s.x) continue;
      list.push({ mm, s, smin: smin ? smin[mm.uid] : null });
    }
    if (!list.length) return;
    const vis = new Set(list.map(x => x.mm.uid));
    let k;
    const kind = gO.COMP_KIND[comp] || "force";
    if (o.vscaleMode === "user") {
      k = gOU.fromDisplay("length", +o.vscaleUser || 0.01) / (gOU.fromDisplay(kind, 1) || 1);
    } else {
      const cOf = new Map(list.map(it => [it.mm.uid, gO.resolveComp(comp, axesOf(it.mm), cam.b)]));
      const ref = api.diagramRef(comp, uid => vis.has(uid), uid => cOf.get(uid),
        `${view.id}|${cam.kind}|${cam.b ? JSON.stringify([cam.b.z, cam.b.coord, cam.b.n]) : ""}|${JSON.stringify([o.towers, o.group])}`);
      if (!(ref > 1e-12)) return;
      k = cam.R * 0.07 * (+o.vscale || 1) / ref;
    }
    const mode = o.values;
    // batched paths: one fill + one outline per sign, one path for the end ticks (thousands of members per frame)
    const PS = new Path2D(), NS = new Path2D(), ENDS = new Path2D(), MINP = new Path2D();
    const labels = [];
    for (const it of list) {
      const axes = axesOf(it.mm);
      const c = gO.resolveComp(comp, axes, cam.b);
      const vals = it.s[c];
      if (!vals) continue;
      const dg = gO.projectDiagram(it.mm, axes, it.s.x, vals, c, k, cam.b);
      const pb = dg.base.map(cam.proj), pt = dg.tip.map(cam.proj);
      if (pb.some(p => !ok(p)) || pt.some(p => !ok(p))) continue;
      drawn.diagrams++;
      const fill = mode === "fill" || mode === "values";
      for (const sign of [true, false]) {
        let any = false;
        if (fill) ctx.beginPath();
        for (let i = 0; i < pb.length - 1; i++) {
          if (((vals[i] + vals[i + 1]) >= 0) !== sign) continue;
          any = true;
          if (fill) { ctx.moveTo(pb[i].x, pb[i].y); ctx.lineTo(pt[i].x, pt[i].y); ctx.lineTo(pt[i + 1].x, pt[i + 1].y); ctx.lineTo(pb[i + 1].x, pb[i + 1].y); ctx.closePath(); }
          const L = sign ? PS : NS;
          L.moveTo(pt[i].x, pt[i].y); L.lineTo(pt[i + 1].x, pt[i + 1].y);
        }
        if (fill && any) { ctx.fillStyle = (sign ? POS : NEG) + "0.26)"; ctx.fill(); }
      }
      ENDS.moveTo(pb[0].x, pb[0].y); ENDS.lineTo(pt[0].x, pt[0].y); ENDS.moveTo(pb[pb.length - 1].x, pb[pb.length - 1].y); ENDS.lineTo(pt[pt.length - 1].x, pt[pt.length - 1].y);
      let minVals = null, ptm = null;
      if (it.smin && it.smin[c]) {
        minVals = it.smin[c];
        const dm = gO.projectDiagram(it.mm, axes, it.s.x, minVals, c, k, cam.b);
        ptm = dm.tip.map(cam.proj);
        if (ptm.every(ok)) ptm.forEach((p, i) => (i ? MINP.lineTo(p.x, p.y) : MINP.moveTo(p.x, p.y)));
        else ptm = null;
      }
      if (mode === "values" || mode === "controlling") {
        // skip the text on members drawn shorter than ~24 px (unreadable clutter)
        if (Math.hypot(pb[pb.length - 1].x - pb[0].x, pb[pb.length - 1].y - pb[0].y) < 24) continue;
        const idx = mode === "values" ? vals.map((_, i) => i) : gO.controllingStations(it.s.x, vals);
        const step = mode === "values" && vals.length > 7 ? Math.ceil(vals.length / 6) : 1;
        idx.forEach((i, n) => {
          if (mode === "values" && n % step && i !== vals.length - 1) return;
          labels.push([fmtV(kind, vals[i]), pt[i].x, pt[i].y - 2, null]);
        });
        if (minVals && ptm) {
          const im = mode === "values" ? [0, minVals.length - 1] : gO.controllingStations(it.s.x, minVals);
          for (const i of im) labels.push(["min " + fmtV(kind, minVals[i]), ptm[i].x, ptm[i].y + 12, "rgba(240,190,80,0.98)"]);
        }
      }
    }
    ctx.lineWidth = 1.3;
    ctx.strokeStyle = POS + "0.95)"; ctx.stroke(PS);
    ctx.strokeStyle = NEG + "0.95)"; ctx.stroke(NS);
    ctx.strokeStyle = "rgba(154,167,180,0.6)"; ctx.lineWidth = 1; ctx.stroke(ENDS);
    ctx.setLineDash([5, 4]); ctx.strokeStyle = "rgba(229,165,10,0.95)"; ctx.lineWidth = 1.3; ctx.stroke(MINP); ctx.setLineDash([]);
    if (labels.length) {
      ctx.font = "600 10px -apple-system, 'Segoe UI', sans-serif";
      // declutter: a label overlapping one already placed (8 px occupancy grid) or off screen is skipped
      const occ = new Set(), CELL = 8;
      for (const [t, x, y, c] of labels) {
        const w = t.length * 5.7 + 6, x0 = x - w / 2, y0 = y - 12;
        if (x0 > cam.w || x0 + w < 0 || y0 > cam.h || y < 0) continue;
        const a0 = Math.floor(x0 / CELL), a1 = Math.floor((x0 + w) / CELL), b0 = Math.floor(y0 / CELL), b1 = Math.floor(y / CELL);
        let hit = false;
        for (let a = a0; a <= a1 && !hit; a++) for (let b = b0; b <= b1; b++) if (occ.has(a * 100003 + b)) { hit = true; break; }
        if (hit) continue;
        for (let a = a0; a <= a1; a++) for (let b = b0; b <= b1; b++) occ.add(a * 100003 + b);
        text(ctx, t, x, y, c ? { color: c } : {});
        drawn.labels++;
      }
    }
  }

  /* ---------------------------------------------------------------- reactions */
  function drawReactions(ctx, cam, view, st, drawn) {
    const r = results(), reac = st.reactions || {};
    const comps = [0, 1, 2].filter(i => { if (!cam.b) return true; const e = [0, 0, 0]; e[i] = 1; return Math.abs(gO.V3.dot(e, cam.b.n)) < 0.5; });
    let fmax = 0;
    for (const v of Object.values(reac)) for (const i of comps) fmax = Math.max(fmax, Math.abs(v[i] || 0));
    if (!(fmax > 1e-12)) return;
    const ka = cam.R * 0.09 / fmax;
    const cols = ["#e66767", "#34c384", "#35b5e5"];
    const zs = Object.values(r.nodes || {}).map(p => p[2]);
    const zmin = zs.length ? Math.min(...zs) : 0;
    const stories = (model().stories || []).slice().sort((a, b) => a.elevation - b.elevation);
    if (cam.kind === "plan" && stories.length && Math.abs(cam.z - stories[0].elevation) > 1e-3) return;   // plan: lowest story shows the base reactions
    ctx.font = "600 10px -apple-system, 'Segoe UI', sans-serif";
    for (const [tag, v] of Object.entries(reac)) {
      const p0 = r.nodes[tag];
      if (!p0) continue;
      if (cam.kind === "elev" && !gO.onViewPlane(cam.b, p0, 1e-3)) continue;
      if (cam.kind === "plan" && p0[2] > zmin + 1e-3) continue;
      for (const i of comps) {
        const fv = v[i] || 0;
        if (Math.abs(fv) < fmax * 0.02) continue;
        const e = [0, 0, 0]; e[i] = 1;
        const L = Math.max(Math.abs(fv) * ka, cam.R * 0.02);
        const tail = [p0[0] - e[0] * Math.sign(fv) * L, p0[1] - e[1] * Math.sign(fv) * L, p0[2] - e[2] * Math.sign(fv) * L];
        const a = cam.proj(tail), b = cam.proj(p0);
        if (!ok(a) || !ok(b)) continue;
        ctx.strokeStyle = cols[i]; ctx.fillStyle = cols[i]; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        const ang = Math.atan2(b.y - a.y, b.x - a.x), hl = 7;
        ctx.beginPath(); ctx.moveTo(b.x, b.y); ctx.lineTo(b.x - hl * Math.cos(ang - 0.4), b.y - hl * Math.sin(ang - 0.4)); ctx.lineTo(b.x - hl * Math.cos(ang + 0.4), b.y - hl * Math.sin(ang + 0.4)); ctx.closePath(); ctx.fill();
        text(ctx, `F${"XYZ"[i]} ${fmtV("force", fv)}`, a.x, a.y - 4, { color: cols[i] });
        drawn.reactions++;
      }
    }
  }

  /* ---------------------------------------------------------------- piers / spandrels / links */
  function storyBands() {
    const m = model(), r = results(), out = {};
    for (const s of m.stories || []) { const top = r.story_elev && r.story_elev[s.name] != null ? +r.story_elev[s.name] : +s.elevation; out[s.name] = [top - (+s.height || 0), top]; }
    return out;
  }
  function block(ctx, cam, base, tip, vals, kind, dashed, labels) {
    const pb = base.map(cam.proj), pt = tip.map(cam.proj);
    if (pb.some(p => !ok(p)) || pt.some(p => !ok(p))) return false;
    const pos = (vals[0] + vals[vals.length - 1]) >= 0;
    ctx.setLineDash(dashed ? [5, 4] : []);
    ctx.fillStyle = (pos ? POS : NEG) + "0.24)"; ctx.strokeStyle = dashed ? "rgba(229,165,10,0.95)" : (pos ? POS : NEG) + "0.95)";
    ctx.lineWidth = 1.4;
    if (!dashed) { ctx.beginPath(); ctx.moveTo(pb[0].x, pb[0].y); pt.forEach(p => ctx.lineTo(p.x, p.y)); ctx.lineTo(pb[pb.length - 1].x, pb[pb.length - 1].y); ctx.closePath(); ctx.fill(); }
    ctx.beginPath(); pt.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y))); ctx.lineTo(pb[pb.length - 1].x, pb[pb.length - 1].y); ctx.moveTo(pb[0].x, pb[0].y); ctx.lineTo(pt[0].x, pt[0].y); ctx.stroke();
    ctx.setLineDash([]);
    if (labels) { const s = new Set(); vals.forEach((v, i) => { const t = (dashed ? "min " : "") + fmtV(kind, v); if (s.has(t)) return; s.add(t); text(ctx, t, pt[i].x, pt[i].y + (dashed ? 12 : -2), { color: dashed ? "rgba(240,190,80,0.98)" : undefined }); }); }
    return true;
  }
  function drawPiers(ctx, cam, view, st, drawn) {
    const m = model(), geo = gOPierGeo(m), bands = storyBands();
    const comp = view.opts.diagram === "MIN" || view.opts.diagram === "M3" || view.opts.diagram === "M2" ? "M" : view.opts.diagram === "N" ? "P" : "V";
    const kind = comp === "M" ? "moment" : "force";
    let vmax = 0;
    for (const byStory of Object.values(st.piers || {})) for (const f of Object.values(byStory)) vmax = Math.max(vmax, Math.abs(+f[comp] || 0), f.min ? Math.abs(+f.min[comp] || 0) : 0);
    if (!(vmax > 1e-12)) return;
    const k = cam.R * 0.08 / vmax;
    for (const [lab, byStory] of Object.entries(st.piers || {})) {
      const g = geo[lab];
      if (!g) continue;
      if (cam.kind === "elev" && (Math.abs(gO.V3.dot(g.n, cam.b.n)) < 0.99 || !gO.onViewPlane(cam.b, [g.c[0], g.c[1], 0], 0.6))) continue;
      for (const [s, f] of Object.entries(byStory)) {
        const b = bands[s];
        if (!b) continue;
        if (cam.kind === "plan") {
          if (Math.abs(b[1] - cam.z) > 1e-3) continue;
          const p = cam.proj([g.c[0], g.c[1], b[1]]);
          if (ok(p)) { text(ctx, `${lab} ${comp} ${fmtV(kind, +f[comp] || 0)}`, p.x, p.y - 4); drawn.piers++; }
          continue;
        }
        let dir = comp === "P" ? g.n : g.h;
        if (cam.b) dir = gO.inPlaneDir(dir, [0, 0, 1], cam.b.n);
        const base = [[g.c[0], g.c[1], b[0]], [g.c[0], g.c[1], b[1]]];
        const v = +f[comp] || 0;
        if (block(ctx, cam, base, base.map(p => gO.V3.add(p, gO.V3.mul(dir, v * k))), [v, v], kind, false, view.opts.values !== "fill")) drawn.piers++;
        if (f.min) { const w = +f.min[comp] || 0; block(ctx, cam, base, base.map(p => gO.V3.add(p, gO.V3.mul(dir, w * k))), [w, w], kind, true, view.opts.values !== "fill"); }
      }
    }
  }
  function drawSpandrels(ctx, cam, view, st, drawn) {
    if (cam.kind === "plan") return;
    const m = model(), geo = gOSpandrelGeo(m, storyBands());
    const k0 = view.opts.diagram && ["P", "V2", "V3", "T", "M2", "M3"].includes(view.opts.diagram === "N" ? "P" : view.opts.diagram) ? (view.opts.diagram === "N" ? "P" : view.opts.diagram) : (view.opts.diagram === "MIN" ? "M3" : "V2");
    const kind = /^M|^T/.test(k0) ? "moment" : "force";
    let vmax = 0;
    for (const byStory of Object.values(st.spandrels || {})) for (const e of Object.values(byStory)) if (e.Left && e.Right) vmax = Math.max(vmax, Math.abs(+e.Left[k0] || 0), Math.abs(+e.Right[k0] || 0));
    if (!(vmax > 1e-12)) return;
    const k = cam.R * 0.08 / vmax;
    for (const [lab, byStory] of Object.entries(st.spandrels || {})) for (const [s, e] of Object.entries(byStory)) {
      const g = geo[lab + "|" + s];
      if (!g || !e.Left || !e.Right) continue;
      const at = sv => { const p = gO.V3.add(gO.V3.mul(g.e1, sv), gO.V3.mul(g.e3, g.nbar)); return [p[0], p[1], g.z]; };
      const base = [at(g.sLo), at(g.sHi)];
      if (cam.kind === "elev" && !(gO.onViewPlane(cam.b, base[0], 0.6) && gO.onViewPlane(cam.b, base[1], 0.6))) continue;
      const vals = [+e.Left[k0] || 0, +e.Right[k0] || 0];
      if (block(ctx, cam, base, base.map((p, j) => gO.V3.add(p, [0, 0, vals[j] * k])), vals, kind, false, view.opts.values !== "fill")) drawn.spandrels++;
      if (e.min && e.min.Left) { const w = [+e.min.Left[k0] || 0, +e.min.Right[k0] || 0]; block(ctx, cam, base, base.map((p, j) => gO.V3.add(p, [0, 0, w[j] * k])), w, kind, true, view.opts.values !== "fill"); }
    }
  }
  /** Elastic link forces k (u_j − u_i) of the current state (global axes). */
  function linkForces(st) {
    const m = model(), r = results(), out = {};
    if (!st || !st.node_disp || !st.signed) return out;
    const idx = nodeIdx();
    for (const lk of m.links || []) {
      if ((lk.link_type || "elastic") !== "elastic") continue;
      const ti = gO.tagAt(r, lk.pi, idx), tj = gO.tagAt(r, lk.pj, idx);
      if (ti == null || tj == null) continue;
      const di = st.node_disp[ti] || [0, 0, 0, 0, 0, 0], dj = st.node_disp[tj] || [0, 0, 0, 0, 0, 0];
      out[lk.uid] = (lk.stiffness || []).map((kk, i) => (+kk || 0) * ((dj[i] || 0) - (di[i] || 0)));
    }
    return out;
  }
  function drawLinks(ctx, cam, view, st, drawn) {
    const m = model(), F = linkForces(st), flt = filterOf(view);
    let vmax = 0;
    for (const f of Object.values(F)) vmax = Math.max(vmax, Math.abs(f[0] || 0), Math.abs(f[1] || 0), Math.abs(f[2] || 0));
    if (!(vmax > 1e-12)) return;
    const k = cam.R * 0.08 / vmax;
    for (const lk of m.links || []) {
      const f = F[lk.uid];
      if (!f || !flt.link(lk)) continue;
      if (cam.kind !== "3d" && !cam.inView(lk.pi, lk.pj)) continue;
      const ax = gO.V3.sub(lk.pj, lk.pi), L = gO.V3.len(ax);
      if (L < 1e-6) continue;
      const e = gO.V3.mul(ax, 1 / L);
      let i = 0; for (let j = 1; j < 3; j++) if (Math.abs(f[j]) > Math.abs(f[i])) i = j;
      let dir = gO.V3.unit(Math.abs(e[2]) > 0.9 ? gO.V3.cross(e, [1, 0, 0]) : gO.V3.cross(e, [0, 0, 1])) || [0, 0, 1];
      if (cam.b) dir = gO.inPlaneDir(dir, e, cam.b.n);
      const base = [lk.pi, lk.pj];
      if (block(ctx, cam, base, base.map(p => gO.V3.add(p, gO.V3.mul(dir, f[i] * k))), [f[i], f[i]], "force", false, true)) drawn.links++;
    }
  }

  /* ================================================================ GPU layer */
  function syncGL(be, G, v, view) {
    const o = view.opts, st = api.state(), r = results(), m = model();
    const on = !!(o.contour && st && st.node_disp && r && m);
    if (!on) { be.showLayer("gres", false); return; }
    const cd = dispContourData(view, st, null);
    const fsig = JSON.stringify([o.towers, o.group, o.palette]);
    const L = be.setLayer("gres", [r, st.node_disp, o.contour, cd.range[0], cd.range[1], fsig, o.cubic, m], () => {
      const { TriBuilder, FatLines, makeMeshMat, cssRGBA, THREE, RO } = G;
      const objs = [], uniforms = [];
      const flt = filterOf(view);
      const nd = st.node_disp;
      const d3 = d => (d ? [+d[0] || 0, +d[1] || 0, +d[2] || 0] : [0, 0, 0]);
      const col = vv => { const c = gO.rampRGB(gO.tOf(vv, cd.range), o.palette); return [c[0] / 255, c[1] / 255, c[2] / 255, 0.94]; };
      const shells = new Map((m.shells || []).map(s => [s.uid, s]));
      const tb = new TriBuilder();
      const SUB = 3;
      for (const q of r.shell_quads || []) {
        const sh = shells.get(q.region);
        if (sh && !flt.shell(sh)) continue;
        const P0 = q.nodes.map(t => r.nodes[t]);
        if (P0.some(p => !p)) continue;
        const D = q.nodes.map(t => d3(nd[t]));
        const vv = q.nodes.map(t => cd.vals[t]);
        if (vv.some(x => x == null)) continue;
        if (P0.length === 4) {
          const lerp = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
          const bil = (A, u, w) => lerp(lerp(A[0], A[1], u), lerp(A[3], A[2], u), w);
          const val = (u, w) => (1 - u) * (1 - w) * vv[0] + u * (1 - w) * vv[1] + u * w * vv[2] + (1 - u) * w * vv[3];
          for (let a = 0; a < SUB; a++) for (let b = 0; b < SUB; b++) {
            const uv = [[a / SUB, b / SUB], [(a + 1) / SUB, b / SUB], [(a + 1) / SUB, (b + 1) / SUB], [a / SUB, (b + 1) / SUB]];
            const P = uv.map(([u, w]) => bil(P0, u, w)), Dd = uv.map(([u, w]) => bil(D, u, w));
            const c = col(uv.reduce((s, [u, w]) => s + val(u, w), 0) / 4);
            tb.tri(P[0], P[1], P[2], c, { d: [Dd[0], Dd[1], Dd[2]] });
            tb.tri(P[0], P[2], P[3], c, { d: [Dd[0], Dd[2], Dd[3]] });
          }
        } else if (P0.length === 3) tb.tri(P0[0], P0[1], P0[2], col((vv[0] + vv[1] + vv[2]) / 3), { d: D });
      }
      if (tb.count) {
        const mat = makeMeshMat({ disp: true, transparent: true, depthWrite: false });
        mat.depthTest = false;
        const mesh = new THREE.Mesh(tb.build(), mat);
        mesh.renderOrder = (RO && RO.deformed != null ? RO.deformed : 8) + 1;
        objs.push(mesh); uniforms.push(mat.uniforms);
      }
      const segs = [];
      for (const mm of m.members || []) {
        if (!flt.member(mm)) continue;
        const rm = rmOf(mm.uid);
        if (!rm) continue;
        const Pi = r.nodes[rm.ni], Pj = r.nodes[rm.nj], vi = cd.vals[rm.ni], vj = cd.vals[rm.nj];
        if (!Pi || !Pj || vi == null || vj == null) continue;
        const di = nd[rm.ni] || [0, 0, 0, 0, 0, 0], dj = nd[rm.nj] || [0, 0, 0, 0, 0, 0];
        const pts = gO.deformedPolyline(Pi, Pj, di, dj, 1, o.cubic);
        const N = pts.length - 1;
        const basePt = k => [Pi[0] + (Pj[0] - Pi[0]) * k / N, Pi[1] + (Pj[1] - Pi[1]) * k / N, Pi[2] + (Pj[2] - Pi[2]) * k / N];
        for (let k = 0; k < N; k++) {
          const a = basePt(k), b = basePt(k + 1);
          segs.push([a, b, gO.V3.sub(pts[k], a), gO.V3.sub(pts[k + 1], b), col(vi + (vj - vi) * (k + 0.5) / N)]);
        }
      }
      if (segs.length) {
        const fl = new FatLines(segs.length, { disp: true, depthTest: false, depthWrite: false });
        segs.forEach(([a, b, da, db, c], i) => { fl.set(i, a, b, c, 3); fl.DA.set(da, i * 3); fl.DB.set(db, i * 3); });
        fl.mesh.renderOrder = (RO && RO.deformed != null ? RO.deformed : 8) + 2;
        objs.push(fl.mesh); uniforms.push(fl.uniforms);
      }
      void cssRGBA;
      return { objs, uniforms };
    });
    const def = o.onDeformed && view.deformedOn ? view.cache.dfactor * (Math.abs(api.phase()) < 1e-6 ? 1e-6 : api.phase()) : 0;
    for (const u of L.uniforms || []) { if (u.uDisp) u.uDisp.value = def; }
    view.glCells = (L.objs || []).length;
    be.showLayer("gres", true);
  }

  /* ================================================================ install */
  function installMain() {
    if (sky.viewer) attach3d(sky.viewer, "main3d");
    if (sky.planEditor) attach2d(sky.planEditor, "plan", "plan");
    if (sky.elevEditor) attach2d(sky.elevEditor, "elev", "elev");
  }
  installMain();
  syncPanes();
  document.addEventListener("sky:wb-windows", () => setTimeout(() => { syncPanes(); api.redraw(); }, 0));
  document.addEventListener("sky:results-changed", () => setTimeout(() => { syncPanes(); api.redraw(); }, 0));
  setInterval(() => { try { syncPanes(); } catch { /* panes come and go */ } }, 800);
  const css = document.createElement("style");
  css.textContent = ".g-res-canvas{display:block}";
  document.head.appendChild(css);
  api.linkForces = () => linkForces(api.state());
  api.drawnIn = id => { const v = api.view(id); return v ? v.drawn || null : null; };
  api.glCells = id => { const v = api.view(id); return v ? v.glCells || 0 : 0; };
  api.syncPanes = syncPanes;
}
