/* SkyFrame W8 — curved frames (ETABS Draw › Draw Curved Beams + frame curve
   data). CONTRACT "Curved frames, wall/floor mesh options …" (core/curved.py:
   FrameMember.curve = {type: "arc", via | center + plane_normal, segments,
   local2}; the engine replaces the arc by chords at analysis time).

   * Draw › Frames › Curved Beam (3-point arc): click the start, the end, then
     any point on the arc (plan view, the story's floor level; respects the
     One / Similar / All stories scope and every snap). The beam is created
     by the normal beam tool and then given curve.via = the third point.
   * Assign › Frame › Curved Geometry…: Straight / 3rd point (one member) /
     plan radius + side (any number of horizontal members), segments
     (auto = 10° chords) and local axis 2 (in plane / normal).
   * Plan: arcs drawn and picked along the arc; 3D: chord polyline.
   Hooks: PlanEditor.w8ToolHook / w8Overlay / w8Static (js/draw.js),
   Viewer3D.w8Segs (js/viewer3d.js). */

import G from "./w8_snapgeom.js";
import cvU from "./units.js";
import { dkForm as cvForm, dkGroup as cvGroup, dkNote as cvNote, dkDialog as cvDialog,
  dkDraft as cvDraft, dkCommit as cvCommit } from "./dlgkit.js";

const NS = "http://www.w3.org/2000/svg";
const TOOL = "w8curve";

/** Arc polyline of a curved member (or null). */
export function memberArc(mm, n) {
  const c = mm && mm.curve;
  if (!c) return null;
  if (c.via) return G.arcPoints3(mm.pi, c.via, mm.pj, n || c.segments || 24);
  if (c.center && c.plane_normal) {
    // center form: CCW about plane_normal from pi to pj
    const nrm = c.plane_normal, ln = Math.hypot(...nrm);
    const k = nrm.map(v => v / ln), cc = c.center;
    const e1v = [0, 1, 2].map(i => mm.pi[i] - cc[i]);
    const r = Math.hypot(...e1v);
    if (!(r > 0)) return null;
    const e1 = e1v.map(v => v / r);
    const e2 = [k[1] * e1[2] - k[2] * e1[1], k[2] * e1[0] - k[0] * e1[2], k[0] * e1[1] - k[1] * e1[0]];
    const v = [0, 1, 2].map(i => mm.pj[i] - cc[i]);
    let t = Math.atan2(v[0] * e2[0] + v[1] * e2[1] + v[2] * e2[2], v[0] * e1[0] + v[1] * e1[1] + v[2] * e1[2]);
    if (t <= 0) t += 2 * Math.PI;
    const m = n || c.segments || 24, pts = [];
    for (let q = 0; q <= m; q++) { const a = t * q / m; pts.push([0, 1, 2].map(i => cc[i] + r * (Math.cos(a) * e1[i] + Math.sin(a) * e2[i]))); }
    return { points: pts, center: cc, r, sweep: t };
  }
  return null;
}

/** Normalized curve dict as the backend stores it (core/curved.normalize_curve). */
export function normCurve(c) {
  const out = { type: "arc" };
  if (c.via) out.via = c.via.map(Number);
  else { out.center = c.center.map(Number); out.plane_normal = c.plane_normal.map(Number); }
  out.segments = c.segments == null ? null : +c.segments;
  out.local2 = c.local2 || "in_plane";
  return out;
}

export function initW8Curved(sky) {
  const S = sky.store, pe = sky.planEditor;
  const toast = (t, m, k = "info") => sky.toast && sky.toast(t, m, k, 4000);
  const st = { pts: [] };     // world {x, y} clicks of the curved-beam tool

  /* ---------------------------------------------- tool */
  const reset = () => { st.pts = []; if (pe) { pe.w8From = null; pe.renderOverlay(); } };
  const startTool = () => {
    if (S.mode !== "model" && sky.setMode) sky.setMode("model");
    if (S.view === "elev" && sky.setView) sky.setView("plan");
    sky.setTool(TOOL);
    reset();
    toast("Curved beam", "Click the start point, the end point, then a point on the arc (Esc cancels).");
  };
  const finish = (p1, p2, p3) => {
    const m = S.model;
    const before = new Set(m.members.map(x => x.uid));
    pe.opts.onDraw("beam", { p1, p2 });                    // scope + dedupe via the normal beam tool
    const made = m.members.filter(x => !before.has(x.uid));
    if (!made.length) { toast("Curved beam", "No beam created (a frame already joins these points).", "warn"); return; }
    for (const mm of made) mm.curve = normCurve({ via: [p3.x, p3.y, mm.pi[2]] });
    if (sky.markDirty) sky.markDirty();
    if (sky.handleSelect) sky.handleSelect(made.map(x => ({ type: "member", uid: x.uid })), false);
    else pe.refresh();
  };
  if (pe) {
    const prevTool = pe.w8ToolHook;
    pe.w8ToolHook = (tool, pt, w, ed) => {
      if (tool !== TOOL) return prevTool && prevTool(tool, pt, w, ed);
      if (st.pts.length && Math.hypot(pt.x - st.pts[st.pts.length - 1].x, pt.y - st.pts[st.pts.length - 1].y) < 1e-9) return;
      st.pts.push({ x: pt.x, y: pt.y });
      ed.w8From = [st.pts[0].x, st.pts[0].y];
      if (st.pts.length < 3) return;
      const [a, b, c] = st.pts;
      reset();
      if (!G.circle3([a.x, a.y, 0], [c.x, c.y, 0], [b.x, b.y, 0])) { toast("Curved beam", "The three points are collinear — draw a straight beam instead.", "warn"); return; }
      finish(a, b, c);
    };
    const prevOv = pe.w8Overlay;
    pe.w8Overlay = (g, ed) => {
      if (prevOv) prevOv(g, ed);
      if (ed.tool !== TOOL || !st.pts.length || !ed._mouseWorld) return;
      const cur = ed.hoverSnap || ed._mouseWorld;
      let pts;
      if (st.pts.length === 1) pts = [[st.pts[0].x, st.pts[0].y], [cur.x, cur.y]];
      else {
        const a = G.arcPoints3([st.pts[0].x, st.pts[0].y, 0], [cur.x, cur.y, 0], [st.pts[1].x, st.pts[1].y, 0], 48);
        pts = a ? a.points : [[st.pts[0].x, st.pts[0].y], [st.pts[1].x, st.pts[1].y]];
      }
      const d = pts.map((p, i) => { const [x, y] = ed.toScreen(p[0], p[1]); return `${i ? "L" : "M"}${x},${y}`; }).join(" ");
      const path = document.createElementNS(NS, "path");
      for (const [k, v] of Object.entries({ d, fill: "none", stroke: "rgba(53,181,229,0.9)", "stroke-width": 2, "stroke-dasharray": "7 5", "data-w8": "curve-rubber" })) path.setAttribute(k, v);
      g.appendChild(path);
    };
    // plan: replace the chord line of curved members by their arc
    const prevStatic = pe.w8Static;
    pe.w8Static = ed => {
      if (prevStatic) prevStatic(ed);
      const m = ed.opts.getModel(), story = ed.opts.getStory();
      for (const mm of (m && m.members) || []) {
        if (!mm.curve || mm.story !== story) continue;
        const node = ed.gElems.querySelector(`[data-ref="member:${CSS.escape(mm.uid)}"]`);
        const a = memberArc(mm, 48);
        if (!node || !a) continue;
        const path = document.createElementNS(NS, "path");
        for (const at of node.attributes) if (!["x1", "y1", "x2", "y2"].includes(at.name)) path.setAttribute(at.name, at.value);
        path.setAttribute("d", a.points.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join(" "));
        path.setAttribute("fill", "none");
        path.setAttribute("data-w8-curved", "1");
        node.replaceWith(path);
      }
    };
    // picking along the arc (instance wrapper of the plan hit test)
    const origCand = pe._candidates.bind(pe);
    pe._candidates = w => {
      const out = origCand(w);
      const m = pe.opts.getModel(), story = pe.opts.getStory();
      const tol = 9 / pe.scale;
      const curved = new Set();
      for (const mm of (m && m.members) || []) {
        if (!mm.curve || mm.story !== story) continue;
        curved.add(mm.uid);
        const a = memberArc(mm, 48);
        if (!a) continue;
        let dmin = Infinity;
        for (let i = 0; i + 1 < a.points.length; i++) dmin = Math.min(dmin, G.distToSeg([w.x, w.y], a.points[i], a.points[i + 1]));
        if (dmin <= tol && !out.some(c => c.type === "member" && c.uid === mm.uid)) {
          const i = out.findIndex(c => c.type === "shell");      // frames rank before shells
          out.splice(i < 0 ? out.length : i, 0, { type: "member", uid: mm.uid });
        }
      }
      // a chord hit far from the arc is not a hit
      return out.filter(c => {
        if (c.type !== "member" || !curved.has(c.uid)) return true;
        const mm = m.members.find(x => x.uid === c.uid), a = memberArc(mm, 48);
        let dmin = Infinity;
        for (let i = 0; i + 1 < a.points.length; i++) dmin = Math.min(dmin, G.distToSeg([w.x, w.y], a.points[i], a.points[i + 1]));
        return dmin <= tol;
      });
    };
  }
  if (pe) { const os = pe.setTool.bind(pe); pe.setTool = t => { st.pts = []; pe.w8From = null; os(t); }; }
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && pe && pe.tool === TOOL && st.pts.length) { reset(); }
  });
  if (sky.viewer) sky.viewer.w8Segs = (segs, m) => {
    const out = [];
    const byUid = new Map(m.members.map(x => [x.uid, x]));
    for (const s of segs) {
      const mm = byUid.get(s.uid);
      const a = mm && mm.curve ? memberArc(mm) : null;
      if (!a) { out.push(s); continue; }
      for (let i = 0; i + 1 < a.points.length; i++) out.push({ ...s, p1: a.points[i], p2: a.points[i + 1], rigid_i: i ? 0 : s.rigid_i, rigid_j: i + 2 === a.points.length ? s.rigid_j : 0 });
    }
    return out;
  };
  if (sky.viewer && S.model) { try { sky.viewer.setModel(S.model); } catch { /* ignore */ } }

  /* ---------------------------------------------- Assign › Frame › Curved Geometry */
  function openAssignCurve() {
    const refs = (S.selection || []).filter(r => r.type === "member");
    const mem = refs.map(r => S.model.members.find(x => x.uid === r.uid)).filter(Boolean);
    if (!mem.length) return cvDialog({ id: "w8AsnCurve", title: "Assign Frame Curved Geometry", iconId: "exleaf-frames", size: "sm", noCancel: true,
      body: [cvNote("Nothing to assign — select one or more <b>frame objects</b> first (V).")] });
    const one = mem.length === 1 ? mem[0] : null;
    const c0 = one && one.curve ? one.curve : null;
    const modeOf = x => !x.curve ? "straight" : (x.curve.via ? "via" : "center");
    const modes = [...new Set(mem.map(modeOf))];
    const V = {
      mode: modes.length === 1 ? (modes[0] === "center" ? "via" : modes[0]) : undefined,
      vx: c0 && c0.via ? c0.via[0] : (one ? (one.pi[0] + one.pj[0]) / 2 : 0),
      vy: c0 && c0.via ? c0.via[1] : (one ? (one.pi[1] + one.pj[1]) / 2 : 0),
      vz: c0 && c0.via ? c0.via[2] : (one ? (one.pi[2] + one.pj[2]) / 2 : 0),
      R: one ? Math.hypot(one.pj[0] - one.pi[0], one.pj[1] - one.pi[1]) : 5, side: "1",
      seg: c0 ? c0.segments : null, local2: c0 ? c0.local2 || "in_plane" : "in_plane",
    };
    const touched = {};
    const F = cvForm();
    const modeOpts = [["straight", "Straight (no curve)"], ...(one ? [["via", "Circular arc — 3rd point coordinates"]] : []), ["radius", "Circular arc — radius (plan)"]];
    const tw = k => v => { V[k] = v; touched[k] = true; };
    const body = [
      cvGroup("Curve Type", [F.select(V, "mode", modeOpts, { label: "Curve", id: "w8CvMode", mixed: V.mode === undefined, write: tw("mode") })]),
      one ? cvGroup("Arc — 3rd Point", [
        F.num(V, "vx", { label: "X", kind: "length", id: "w8CvVx", write: tw("vx") }),
        F.num(V, "vy", { label: "Y", kind: "length", id: "w8CvVy", write: tw("vy") }),
        F.num(V, "vz", { label: "Z", kind: "length", id: "w8CvVz", write: tw("vz") }),
      ], { note: "Any point on the arc between the two ends (the arc runs I-end → point → J-end)." }) : null,
      cvGroup("Arc — Radius (plan)", [
        F.num(V, "R", { label: "Radius", kind: "length", gt: 0, id: "w8CvR", write: tw("R") }),
        F.select(V, "side", [["1", "Bulge to the left of I → J"], ["-1", "Bulge to the right of I → J"]], { label: "Side", id: "w8CvSide", write: tw("side") }),
      ], { note: "Horizontal members only; the radius must be at least half the chord." }),
      cvGroup("Analysis", [
        F.num(V, "seg", { label: "Segments (chords)", int: true, min: 1, max: 720, allowNull: true, nullText: "auto (10° chords)", id: "w8CvSeg", write: tw("seg") }),
        F.select(V, "local2", [["in_plane", "In plane of curvature (radial)"], ["normal", "Normal to the plane"]], { label: "Local axis 2", id: "w8CvL2", write: tw("local2") }),
      ]),
    ];
    const apply = () => {
      if (!Object.keys(touched).length) return "";
      const D = cvDraft(sky);
      const uids = new Set(mem.map(x => x.uid));
      const mode = touched.mode ? V.mode : null;
      for (const mm of D.members) {
        if (!uids.has(mm.uid)) continue;
        const m0 = mode || modeOf(mm);
        if (m0 === "straight") { delete mm.curve; continue; }
        let base = mm.curve ? { ...mm.curve } : null;
        if (m0 === "via" && (touched.vx || touched.vy || touched.vz || touched.mode || !base)) {
          base = { ...(base || {}), via: [V.vx, V.vy, V.vz] }; delete base.center; delete base.plane_normal;
        }
        if (m0 === "radius" && (touched.R || touched.side || touched.mode || !base)) {
          if (Math.abs(mm.pi[2] - mm.pj[2]) > 1e-9) return `Frame ${mm.uid} is not horizontal — use the 3rd-point form.`;
          const via = G.viaFromRadius(mm.pi, mm.pj, V.R, +V.side);
          if (!via) return `Frame ${mm.uid}: the radius must be at least half the chord (${cvU.fmt("length", Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1]) / 2, 3)} ${cvU.label("length")}).`;
          base = { ...(base || {}), via }; delete base.center; delete base.plane_normal;
        }
        if (!base) continue;
        if (touched.seg || !("segments" in base)) base.segments = V.seg == null ? null : V.seg;
        if (touched.local2 || !base.local2) base.local2 = V.local2;
        const c = normCurve(base);
        if (c.via && !G.circle3(mm.pi, c.via, mm.pj)) return `Frame ${mm.uid}: the 3rd point is collinear with the ends.`;
        if (JSON.stringify(c) !== JSON.stringify(mm.curve)) mm.curve = c;
      }
      cvCommit(sky, D);
      return "";
    };
    return cvDialog({ id: "w8AsnCurve", title: "Assign Frame Curved Geometry", iconId: "exleaf-frames", size: "md", form: F, body,
      intro: `${mem.length} frame object${mem.length > 1 ? "s" : ""} selected. Only the values you change are assigned.`,
      onOk: () => { const m = apply(); if (m) return m; }, onApply: () => apply() });
  }

  if (sky.menu) {
    sky.menu.add("Draw/Frames", { label: "Curved Beam (3-point arc)", act: "draw-w8-curvebeam", fn: startTool });
    sky.menu.add("Edit/Edit Frames", { label: "Curved Frame Geometry…", act: "asn-w8-fcurve", hint: "3rd point · radius · segments", fn: openAssignCurve });
  }
  sky.w8curved = { startTool, openAssign: openAssignCurve, memberArc, state: () => ({ pts: st.pts.slice() }), TOOL };
}
