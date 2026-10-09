/* SkyFrame — polygon floors / walls / openings + vertex editing (G2).

   CONTRACT "Polygon shells and auto mesh": ShellRegion.corners takes 3..N
   points; Opening.polygon (>= 3 in-plane points) cuts an N-gon hole.

     Draw > Draw Floor (Polygon)    plan view: click vertices (grid + point
                                    snapping), double-click / Enter / click
                                    the first vertex closes, Backspace
                                    removes the last vertex, Esc cancels.
     Draw > Draw Wall (Polygon)     same, in the elevation view (s, z) of the
                                    current grid line.
     Draw > Draw Opening (Polygon)  inside the selected floor (plan) or wall
                                    (elevation) → Opening.polygon.
     Editing (Select tool, one shell selected in its view): drag vertex
     handles, double-click an edge to insert a vertex, click a handle then
     Delete/Backspace to remove it (>= 3 kept). Properties panel: corner
     count, gross / net area and an editable vertex table (display units).

   Self-intersecting / degenerate polygons are rejected with a message.
   Everything is attached through additive hooks: editor.g2Overlay (draw.js /
   elev.js renderOverlay), capture-phase listeners on the two SVGs and on
   document, and window.__sky.{polyDraw, g2DecorateProps, g2OpeningPreview}.
   The store stays SI; coordinates are shown / typed through units.js. */

import * as PME from "./modeledit.js";
import PU from "./units.js";
import { regionFrame, isLegacyQuad, polygonProblem2, signedArea2, shellAreas,
  openingPolygon3, polyInside2, polysOverlap2, distToSeg2, segParam2 } from "./polygeom.js";

const NS = "http://www.w3.org/2000/svg";
const sv = (tag, attrs = {}) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
};
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const COL = {
  line: "rgba(53, 181, 229, 0.95)",
  fill: "rgba(53, 181, 229, 0.12)",
  bad: "rgba(230, 103, 103, 0.95)",
  badFill: "rgba(230, 103, 103, 0.12)",
  open: "rgba(201, 133, 0, 0.95)",
  openFill: "rgba(201, 133, 0, 0.14)",
  handle: "#0d1117",
  handleEdge: "#35b5e5",
  active: "#f5be3c",
};
const SNAP_PT_PX = 10;     // point-snap radius (px)
const HANDLE_PX = 7;       // vertex handle hit radius (px)
const EDGE_PX = 6;         // edge hit tolerance (px)

export function initPolyDraw(sky) {
  const S = sky.store;
  const $ = id => document.getElementById(id);
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);

  /* drawing state: kind "slab" | "wall" | "opening"; pts in view 2D coords */
  const st = { kind: null, view: null, pts: [], target: null, hover: null };
  /* editing state */
  const ed = { drag: null, active: null, hoverIdx: null, hoverEdge: null };
  let hintSaved = null;

  /* ---------------------------------------------- view adapters */
  const plan = {
    name: "plan",
    get editor() { return sky.planEditor; },
    to2: c => [c[0], c[1]],
    screen(p) { return this.editor.toScreen(p[0], p[1]); },
    world(px, py) { const w = this.editor.toWorld(px, py); return [w.x, w.y]; },
    gridSnap(p) { const r = this.editor.snap({ x: p[0], y: p[1] }); return [r.x, r.y]; },
    visible: sh => sh.kind === "slab" && sh.story === S.story,
    from2(sh, p) {
      if (!sh) { const { zt } = PME.storyZ(S.model, S.story); return [p[0], p[1], zt]; }
      const fr = regionFrame(sh.corners);
      const e3 = fr.e3;
      const z = Math.abs(e3[2]) > 1e-9 ? (fr.w0 - e3[0] * p[0] - e3[1] * p[1]) / e3[2] : sh.corners[0][2];
      return [p[0], p[1], Math.abs(z - Math.round(z * 1e6) / 1e6) < 1e-9 ? Math.round(z * 1e6) / 1e6 : z];
    },
    points() {
      const m = S.model, out = [];
      const { zt } = PME.storyZ(m, S.story);
      for (const sh of m.shells || []) if (sh.story === S.story) {
        for (const c of sh.corners) out.push([c[0], c[1]]);
        for (const o of sh.openings || []) if (o.polygon) for (const c of o.polygon) out.push([c[0], c[1]]);
      }
      for (const mm of m.members || []) {
        if (Math.abs(mm.pi[2] - zt) < 1e-6) out.push([mm.pi[0], mm.pi[1]]);
        if (Math.abs(mm.pj[2] - zt) < 1e-6) out.push([mm.pj[0], mm.pj[1]]);
      }
      return out;
    },
    key: () => `plan|${S.story}`,
  };
  const elev = {
    name: "elev",
    get editor() { return sky.elevEditor; },
    to2(c) { return [this.editor.sOf(c), c[2]]; },
    screen(p) { return this.editor.toScreen(p[0], p[1]); },
    world(px, py) { const w = this.editor.toWorld(px, py); return [w.s, w.z]; },
    gridSnap(p) {
      if (this.editor.snapEnabled === false) return p;
      const r = this.editor.snap({ s: p[0], z: p[1] }); return [r.s, r.z];
    },
    visible(sh) { return sh.kind === "wall" && sh.corners.every(c => this.editor.inPlane(c)); },
    from2(sh, p) { return this.editor.world3(p[0], p[1]); },
    points() {
      const m = S.model, out = [], E = this.editor;
      for (const sh of m.shells || []) {
        for (const c of sh.corners) if (E.inPlane(c)) out.push([E.sOf(c), c[2]]);
        for (const o of sh.openings || []) if (o.polygon) for (const c of o.polygon) if (E.inPlane(c)) out.push([E.sOf(c), c[2]]);
      }
      for (const mm of m.members || []) {
        if (E.inPlane(mm.pi)) out.push([E.sOf(mm.pi), mm.pi[2]]);
        if (E.inPlane(mm.pj)) out.push([E.sOf(mm.pj), mm.pj[2]]);
      }
      return out;
    },
    key: () => `elev|${S.elevLine}`,
  };
  const adapterOf = editor => (editor === sky.elevEditor ? elev : plan);
  const curAdapter = () => (S.view === "elev" ? elev : plan);

  /* cached snap points (rebuilt on model change / story / elevation line) */
  let ptsCache = { key: null, pts: [] };
  document.addEventListener("sky:model-changed", () => { ptsCache.key = null; });
  const snapPoints = A => {
    const k = A.key() + "|" + (S.model ? S.model.members.length + S.model.shells.length : 0);
    if (ptsCache.key !== k) ptsCache = { key: k, pts: A.points() };
    return ptsCache.pts;
  };
  /** Snap: nearest model point within SNAP_PT_PX, else the editor grid snap. */
  const snapAt = (A, p, extra = []) => {
    const sc = A.editor.scale;
    let best = null, bd = SNAP_PT_PX / sc;
    for (const q of extra) { const d = Math.hypot(q[0] - p[0], q[1] - p[1]); if (d < bd) { bd = d; best = q; } }
    for (const q of snapPoints(A)) { const d = Math.hypot(q[0] - p[0], q[1] - p[1]); if (d < bd) { bd = d; best = q; } }
    return best ? [best[0], best[1]] : A.gridSnap(p);
  };
  const evWorld = (A, e) => { const [px, py] = A.editor._evPx(e); return A.world(px, py); };

  /* ---------------------------------------------- selection helpers */
  const selShell = () => {
    const sel = S.selection || [];
    if (sel.length !== 1 || sel[0].type !== "shell") return null;
    return (S.model.shells || []).find(s => s.uid === sel[0].uid) || null;
  };
  const editable = A => {
    if (S.mode !== "model" || st.kind || !A.editor || A.editor.tool !== "select") return null;
    if (A.name !== (S.view === "elev" ? "elev" : "plan")) return null;
    const sh = selShell();
    return sh && A.visible(sh) ? sh : null;
  };
  const drawing = A => !!st.kind && S.tool === "g2poly" && S.mode === "model" && st.view === A.name;

  const refresh = () => {
    if (sky.planEditor) sky.planEditor.renderStatic();
    if (sky.elevEditor) sky.elevEditor.renderStatic();
    if (sky.planEditor) sky.planEditor.renderOverlay();
    if (sky.elevEditor) sky.elevEditor.renderOverlay();
  };

  /* ---------------------------------------------- start / cancel / finish */
  function setHint(text) {
    const h = $("drawHint");
    if (!h) return;
    if (text == null) { if (hintSaved != null) h.textContent = hintSaved; hintSaved = null; return; }
    if (hintSaved == null) hintSaved = h.textContent;
    h.textContent = text;
  }
  const HINT = {
    slab: "Polygon floor: click vertices (snaps to grid & points) · dbl-click / Enter / first vertex closes · Backspace removes last · Esc cancels",
    wall: "Polygon wall: click vertices in this elevation · dbl-click / Enter closes · Backspace removes last · Esc cancels",
    opening: "Polygon opening: click vertices inside the selected shell · dbl-click / Enter closes · Backspace · Esc",
  };

  function locateWallLine(sh) {
    const g = S.model.grid;
    if (!g) return false;
    for (const [axis, lines, k] of [["x", g.x_lines, 0], ["y", g.y_lines, 1]]) {
      const i = lines.findIndex(v => sh.corners.every(c => Math.abs(c[k] - v) < 1e-4));
      if (i >= 0) { sky.setElevLine(`${axis}:${i}`); return true; }
    }
    return false;
  }

  function start(kind) {
    if (!S.model) return false;
    if (S.mode !== "model") sky.setMode("model");
    let target = null, view = kind === "wall" ? "elev" : "plan";
    if (kind === "opening") {
      target = selShell();
      if (!target) { toast("Draw Opening (Polygon)", "Select one floor or wall first (Select tool), then draw the opening inside it.", "error"); return false; }
      if (target.kind === "wall") {
        view = "elev";
        if (S.view !== "elev") sky.setView("elev");
        if (!elev.visible(target) && !locateWallLine(target)) {
          toast("Draw Opening (Polygon)", "This wall does not lie on a grid line — openings can be drawn on grid-line walls in elevation.", "error");
          return false;
        }
      } else {
        view = "plan";
        if (S.view !== "plan") sky.setView("plan");
        if (target.story !== S.story) sky.setStory(target.story);
      }
    } else if (S.view !== view) sky.setView(view);
    if (view === "elev" && !(sky.elevEditor && sky.elevEditor.plane())) {
      toast("Draw Wall (Polygon)", "Pick an elevation grid line first.", "error");
      return false;
    }
    sky.setTool("g2poly");
    Object.assign(st, { kind, view, pts: [], target: target ? target.uid : null, hover: null });
    setHint(HINT[kind]);
    refresh();
    return true;
  }

  function cancel(keepTool = false) {
    const was = !!st.kind;
    Object.assign(st, { kind: null, view: null, pts: [], target: null, hover: null });
    setHint(null);
    if (was && !keepTool && S.tool === "g2poly") sky.setTool("select");
    refresh();
  }

  const targetShell = () => st.target ? (S.model.shells || []).find(s => s.uid === st.target) || null : null;

  function finish() {
    const A = st.view === "elev" ? elev : plan;
    const m = S.model;
    let pts = st.pts.filter((p, i, a) => i === 0 || Math.hypot(p[0] - a[i - 1][0], p[1] - a[i - 1][1]) > 1e-9);
    if (pts.length > 1 && Math.hypot(pts[0][0] - pts[pts.length - 1][0], pts[0][1] - pts[pts.length - 1][1]) < 1e-9) pts.pop();
    const why = polygonProblem2(pts);
    if (why) { toast("Polygon rejected", why, "error"); return false; }
    let made = null;
    if (st.kind === "slab") {
      if (signedArea2(pts) < 0) pts = pts.slice().reverse();        // CCW about +Z
      const stories = S.applyAll ? m.stories.map(s => s.name) : [S.story];
      for (const story of stories) {
        const { zt } = PME.storyZ(m, story);
        const corners = pts.map(p => [p[0], p[1], zt]);
        const sh = addShell(m, "slab", corners, story);
        if (sh && story === S.story) made = sh;
        else if (sh && !made) made = sh;
      }
    } else if (st.kind === "wall") {
      const corners = pts.map(p => A.from2(null, p));
      const zTop = Math.max(...corners.map(c => c[2]));
      made = addShell(m, "wall", corners, PME.storyContainingZ(m, zTop));
    } else if (st.kind === "opening") {
      const sh = targetShell();
      if (!sh) { cancel(); return false; }
      const poly3 = pts.map(p => A.from2(sh, p));
      const err = openingProblem(sh, poly3, -1);
      if (err) { toast("Opening rejected", err, "error"); return false; }
      (sh.openings || (sh.openings = [])).push({ u0: 0, v0: 0, u1: 1, v1: 1, polygon: poly3 });
      made = sh;
    }
    if (!made) { toast("Polygon", "Nothing created (duplicate region?)", "error"); return false; }
    const kind = st.kind;
    st.pts = []; st.hover = null;
    sky.markDirty();
    if (kind === "opening") cancel();
    sky.handleSelect([{ type: "shell", uid: made.uid }], false);
    refresh();
    toast(kind === "opening" ? "Opening added" : "Region created",
      kind === "opening" ? `${pts.length}-point opening in ${made.uid}` : `${made.uid} · ${pts.length} corners`, "info", 2500);
    return true;
  }

  function addShell(m, kind, corners, story) {
    const near = (a, b) => Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6 && Math.abs(a[2] - b[2]) < 1e-6;
    if ((m.shells || []).some(s => s.kind === kind && s.corners.length === corners.length &&
      s.corners.every((c, i) => near(c, corners[i])))) return null;
    const sh = {
      uid: PME.nextUid(m, kind === "slab" ? "SL" : "W"), kind, behavior: "shell",
      section: PME.defaultShellSection(m), corners, mesh_size: 1.0, story, openings: [],
    };
    m.shells.push(sh);
    return sh;
  }

  /** Opening polygon (3D) problem vs. its region / other openings ("" = ok). */
  function openingProblem(sh, poly3, skipIdx) {
    const fr = regionFrame(sh.corners);
    const h = poly3.map(fr.to2), outer = sh.corners.map(fr.to2);
    const why = polygonProblem2(h);
    if (why) return why;
    if (!polyInside2(h, outer)) return "the opening must lie inside the region without touching its boundary";
    for (const [i, o] of (sh.openings || []).entries()) {
      if (i === skipIdx) continue;
      if (polysOverlap2(openingPolygon3(sh, o).map(fr.to2), h)) return "openings may not overlap each other";
    }
    return "";
  }

  /** Validate a candidate corner list for a shell (view 2D + openings). */
  function shellProblem(sh, corners3) {
    if (corners3.length < 3) return "a region needs at least 3 corners";
    const fr = regionFrame(corners3);
    for (const c of corners3)
      if (Math.abs(c[0] * fr.e3[0] + c[1] * fr.e3[1] + c[2] * fr.e3[2] - fr.w0) > 1e-6)
        return "corners are not coplanar";
    const outer = corners3.map(fr.to2);
    const why = polygonProblem2(outer);
    if (why) return why;
    const tmp = { ...sh, corners: corners3 };
    const holes = (sh.openings || []).map(o => openingPolygon3(tmp, o).map(fr.to2));
    for (const h of holes) if (!polyInside2(h, outer)) return "an opening would end up outside the region";
    return "";
  }

  function setCorners(sh, corners3, A, what) {
    const why = shellProblem(sh, corners3);
    if (why) { toast("Edit rejected", why, "error"); refresh(); return false; }
    sh.corners = corners3;
    if (sh.kind === "wall") sh.story = PME.storyContainingZ(S.model, Math.max(...corners3.map(c => c[2])));
    sky.markDirty();
    refresh();
    sky.renderProps();
    if (what) document.dispatchEvent(new CustomEvent("sky:g2-vertex", { detail: { uid: sh.uid, what } }));
    return true;
  }

  function insertVertex(sh, edgeIdx, p2, A) {
    const c = sh.corners.map(x => [...x]);
    c.splice(edgeIdx + 1, 0, A.from2(sh, p2));
    return setCorners(sh, c, A, "insert");
  }
  function deleteVertex(sh, idx, A) {
    if (sh.corners.length <= 3) { toast("Delete vertex", "A region keeps at least 3 corners.", "error"); return false; }
    const c = sh.corners.map(x => [...x]);
    c.splice(idx, 1);
    const ok = setCorners(sh, c, A, "delete");
    if (ok) ed.active = null;
    return ok;
  }

  /* ---------------------------------------------- hit helpers (editing) */
  function vertexAt(sh, A, p) {
    const tol = HANDLE_PX / A.editor.scale;
    let best = -1, bd = tol;
    sh.corners.forEach((c, i) => { const q = A.to2(c); const d = Math.hypot(q[0] - p[0], q[1] - p[1]); if (d <= bd) { bd = d; best = i; } });
    return best;
  }
  function edgeAt(sh, A, p) {
    const tol = EDGE_PX / A.editor.scale;
    const q = sh.corners.map(c => A.to2(c));
    let best = -1, bd = tol;
    for (let i = 0; i < q.length; i++) {
      const a = q[i], b = q[(i + 1) % q.length];
      const d = distToSeg2(p[0], p[1], a[0], a[1], b[0], b[1]);
      if (d <= bd) { bd = d; best = i; }
    }
    return best;
  }

  /* ---------------------------------------------- pointer listeners */
  function bindSvg(A) {
    const svg = A.editor && A.editor.svg;
    if (!svg) return;
    svg.addEventListener("pointerdown", e => {
      if (e.button !== 0) return;
      const p = evWorld(A, e);
      if (drawing(A)) {
        const sp = snapAt(A, p, st.pts.slice(0, 1));
        if (st.pts.length >= 3 && Math.hypot(sp[0] - st.pts[0][0], sp[1] - st.pts[0][1]) < 1e-9) { finish(); return; }
        const last = st.pts[st.pts.length - 1];
        if (!last || Math.hypot(sp[0] - last[0], sp[1] - last[1]) > 1e-9) st.pts.push(sp);
        st.hover = sp;
        return;                                    // editor _toolClick ignores the custom tool
      }
      const sh = editable(A);
      if (!sh) return;
      const vi = vertexAt(sh, A, p);
      if (vi >= 0) {
        e.stopImmediatePropagation(); e.preventDefault();
        try { svg.setPointerCapture(e.pointerId); } catch { /* ignore */ }
        ed.active = vi;
        ed.drag = { uid: sh.uid, idx: vi, start: A.to2(sh.corners[vi]), pt: A.to2(sh.corners[vi]), moved: false };
        A.editor.renderOverlay();
        return;
      }
      if (edgeAt(sh, A, p) >= 0) {                 // keep the selection for a dbl-click insert
        e.stopImmediatePropagation(); e.preventDefault();
        ed.active = null;
        A.editor.renderOverlay();
      }
    }, true);

    svg.addEventListener("pointermove", e => {
      const p = evWorld(A, e);
      if (drawing(A)) { st.hover = snapAt(A, p, st.pts.slice(0, 1)); return; }
      if (ed.drag) {
        const sh = selShell();
        if (!sh || sh.uid !== ed.drag.uid) { ed.drag = null; return; }
        const others = sh.corners.filter((_, i) => i !== ed.drag.idx).map(c => A.to2(c));
        const sp = snapAt(A, p, others);
        ed.drag.pt = sp;
        if (Math.hypot(sp[0] - ed.drag.start[0], sp[1] - ed.drag.start[1]) > 1e-9) ed.drag.moved = true;
        A.editor.hoverRef = null;
        A.editor.renderOverlay();
        e.stopImmediatePropagation();
        return;
      }
      const sh = editable(A);
      const vi = sh ? vertexAt(sh, A, p) : -1;
      const ei = sh && vi < 0 ? edgeAt(sh, A, p) : -1;
      if (vi !== ed.hoverIdx || ei !== ed.hoverEdge) { ed.hoverIdx = vi; ed.hoverEdge = ei; }
      svg.style.cursor = vi >= 0 ? "move" : ei >= 0 ? "copy" : "";
    }, true);

    const up = e => {
      if (!ed.drag) return;
      const d = ed.drag; ed.drag = null;
      e.stopImmediatePropagation();
      const sh = selShell();
      if (!sh || sh.uid !== d.uid || !d.moved) { A.editor.renderOverlay(); return; }
      const c = sh.corners.map(x => [...x]);
      c[d.idx] = A.from2(sh, d.pt);
      setCorners(sh, c, A, "move");
    };
    svg.addEventListener("pointerup", up, true);
    svg.addEventListener("pointercancel", up, true);

    svg.addEventListener("dblclick", e => {
      if (drawing(A)) { e.stopImmediatePropagation(); e.preventDefault(); if (st.pts.length >= 3) finish(); return; }
      const sh = editable(A);
      if (!sh) return;
      const p = evWorld(A, e);
      if (vertexAt(sh, A, p) >= 0) { e.stopImmediatePropagation(); return; }
      const ei = edgeAt(sh, A, p);
      if (ei < 0) return;
      e.stopImmediatePropagation(); e.preventDefault();
      const q = sh.corners.map(c => A.to2(c));
      const a = q[ei], b = q[(ei + 1) % q.length];
      const t = segParam2(p[0], p[1], a[0], a[1], b[0], b[1]);
      let pt = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
      const sn = A.gridSnap(pt);                   // prefer a snapped point that stays on the edge
      if (distToSeg2(sn[0], sn[1], a[0], a[1], b[0], b[1]) < 1e-6 &&
          Math.hypot(sn[0] - pt[0], sn[1] - pt[1]) < SNAP_PT_PX / A.editor.scale) pt = sn;
      if (Math.min(Math.hypot(pt[0] - a[0], pt[1] - a[1]), Math.hypot(pt[0] - b[0], pt[1] - b[1])) < 1e-6) return;
      if (insertVertex(sh, ei, pt, A)) ed.active = ei + 1;
    }, true);
  }
  bindSvg(plan);
  bindSvg(elev);

  /* ---------------------------------------------- keyboard (capture) */
  document.addEventListener("keydown", e => {
    const tag = (e.target && e.target.tagName || "").toLowerCase();
    if (["input", "select", "textarea"].includes(tag) || e.ctrlKey || e.metaKey || e.altKey) return;
    if (S.mode !== "model") return;
    if (document.querySelector(".modal-backdrop:not(.hidden)")) return;
    if (st.kind && S.tool === "g2poly") {
      if (e.key === "Enter") { e.preventDefault(); e.stopImmediatePropagation(); finish(); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); cancel(); }
      else if (e.key === "Backspace" || e.key === "Delete") {
        e.preventDefault(); e.stopImmediatePropagation();
        st.pts.pop(); refresh();
      }
      return;
    }
    const A = curAdapter();
    const sh = editable(A);
    if (!sh || ed.active == null || ed.active >= sh.corners.length) return;
    if (e.key === "Backspace" || e.key === "Delete") {
      e.preventDefault(); e.stopImmediatePropagation();
      deleteVertex(sh, ed.active, A);
    } else if (e.key === "Escape") {
      e.preventDefault(); e.stopImmediatePropagation();
      ed.active = null; A.editor.renderOverlay();
    }
  }, true);

  /* ---------------------------------------------- overlay rendering */
  const pathD = pts => pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");

  function overlay(g, editor) {
    const A = adapterOf(editor);
    if (st.kind && S.tool !== "g2poly") { cancel(true); return; }    // tool switched away
    if (drawing(A)) { drawPreview(g, A); return; }
    const sh = editable(A);
    if (!sh) { ed.active = null; return; }
    drawHandles(g, A, sh);
  }

  function drawPreview(g, A) {
    const pts = st.pts.slice();
    const hv = st.hover;
    const all = hv ? [...pts, hv] : pts;
    const scr = all.map(p => A.screen(p));
    const bad = all.length >= 3 && !!polygonProblem2(all.filter((p, i, a) => i === 0 || Math.hypot(p[0] - a[i - 1][0], p[1] - a[i - 1][1]) > 1e-9));
    const isOpen = st.kind === "opening";
    const line = bad ? COL.bad : isOpen ? COL.open : COL.line;
    const fill = bad ? COL.badFill : isOpen ? COL.openFill : COL.fill;
    if (scr.length >= 3) g.appendChild(sv("path", { d: pathD(scr) + " Z", fill, stroke: "none" }));
    if (scr.length >= 2) {
      g.appendChild(sv("path", { d: pathD(scr.slice(0, pts.length || 1)), fill: "none", stroke: line, "stroke-width": 2 }));
      if (hv && pts.length) {
        const a = A.screen(pts[pts.length - 1]), b = A.screen(hv);
        g.appendChild(sv("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: line, "stroke-width": 2, "stroke-dasharray": "7 5" }));
      }
      if (scr.length >= 3) {
        const a = scr[scr.length - 1], b = scr[0];
        g.appendChild(sv("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: line, "stroke-width": 1, "stroke-dasharray": "3 4", opacity: 0.7 }));
      }
    }
    pts.forEach((p, i) => {
      const [x, y] = A.screen(p);
      g.appendChild(sv("circle", { cx: x, cy: y, r: i === 0 ? 6 : 3.5, fill: i === 0 ? "none" : line, stroke: line, "stroke-width": 1.5 }));
    });
    if (hv) {
      const [x, y] = A.screen(hv);
      g.appendChild(sv("circle", { cx: x, cy: y, r: 5, fill: "none", stroke: line, "stroke-width": 1.5 }));
    }
    if (bad) {
      const [x, y] = A.screen(hv || pts[pts.length - 1]);
      const t = sv("text", { x: x + 10, y: y - 10, fill: COL.bad, "font-size": 11, "font-weight": 600, "font-family": "inherit" });
      t.textContent = "self-intersecting";
      g.appendChild(t);
    }
  }

  function drawHandles(g, A, sh) {
    let q = sh.corners.map(c => A.to2(c));
    let bad = false;
    if (ed.drag && ed.drag.uid === sh.uid) {
      q = q.slice(); q[ed.drag.idx] = ed.drag.pt;
      bad = !!polygonProblem2(q);
    }
    const scr = q.map(p => A.screen(p));
    g.appendChild(sv("path", {
      d: pathD(scr) + " Z", fill: ed.drag ? (bad ? COL.badFill : COL.fill) : "none",
      stroke: bad ? COL.bad : COL.line, "stroke-width": 1.5, "stroke-dasharray": ed.drag ? "6 3" : "",
      "pointer-events": "none",
    }));
    if (ed.hoverEdge != null && ed.hoverEdge >= 0 && !ed.drag && ed.hoverEdge < scr.length) {
      const a = scr[ed.hoverEdge], b = scr[(ed.hoverEdge + 1) % scr.length];
      g.appendChild(sv("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke: COL.active, "stroke-width": 3, opacity: 0.8, "pointer-events": "none" }));
    }
    // edge midpoints: insert hints (dbl-click)
    for (let i = 0; i < scr.length; i++) {
      const a = scr[i], b = scr[(i + 1) % scr.length];
      g.appendChild(sv("circle", { cx: (a[0] + b[0]) / 2, cy: (a[1] + b[1]) / 2, r: 2.2, fill: COL.handleEdge, opacity: 0.6, "pointer-events": "none" }));
    }
    scr.forEach(([x, y], i) => {
      const act = i === ed.active, hov = i === ed.hoverIdx;
      g.appendChild(sv("rect", {
        x: x - 4.5, y: y - 4.5, width: 9, height: 9,
        fill: act ? COL.active : COL.handle, stroke: hov || act ? COL.active : COL.handleEdge,
        "stroke-width": 1.5, "data-g2-vertex": i, "pointer-events": "none",
      }));
    });
  }

  // the overlay layers are purely visual: rebuilt on every pointer move, they
  // must never become the event target (a vanished target breaks dblclick)
  for (const E of [sky.planEditor, sky.elevEditor]) {
    if (!E) continue;
    E.g2Overlay = overlay;
    if (E.gOverlay) E.gOverlay.setAttribute("pointer-events", "none");
  }

  /* ---------------------------------------------- properties panel */
  const L = () => PU.label("length");
  const area2 = si => PU.toDisplay("length", PU.toDisplay("length", si));

  function decorate(box) {
    try { decorateShell(box); } catch (e) { console.error(e); }
    try { if (sky.g2DecorateInsertion) sky.g2DecorateInsertion(box); } catch (e) { console.error(e); }
  }

  function decorateShell(box) {
    const sh = selShell();
    if (!sh || !box) return;
    const head = [...box.querySelectorAll("h3.group-title")].find(h => /Shell assignments/.test(h.textContent));
    if (!head) return;
    const a = shellAreas(sh);
    const wrap = document.createElement("div");
    wrap.className = "g2-geom";
    wrap.id = "g2Geom";
    const legacy = isLegacyQuad(sh);
    const decA = PU.isIdentity("length") ? 3 : 2;
    let html = `<div class="g2-geom-sum">
      <span title="Number of region corners"><b id="g2CornerCount">${sh.corners.length}</b> corners · ${legacy ? "quad (structured mesh)" : "polygon (auto mesh)"}</span>
      <span title="Gross area / net area (openings removed)">A = <b id="g2Area">${area2(a.gross).toFixed(decA)}</b> ${esc(L())}²${a.openings > 0 ? ` · net <b id="g2NetArea">${area2(a.net).toFixed(decA)}</b>` : ""}</span>
    </div>
    <details class="g2-verts"${sh.corners.length !== 4 ? " open" : ""}><summary>Vertices <span class="unit">${esc(L())} · drag handles / dbl-click an edge in the ${sh.kind === "wall" ? "elevation" : "plan"} to reshape</span></summary>
    <div class="g2-vrows" id="g2VertRows">
      <div class="g2-vrow head"><span>#</span><span>X</span><span>Y</span><span>Z</span><span></span></div>`;
    sh.corners.forEach((c, i) => {
      html += `<div class="g2-vrow" data-i="${i}"><span>${i + 1}</span>` +
        [0, 1, 2].map(k => `<input type="number" step="any" data-k="${k}" value="${PU.inputValue("length", c[k])}">`).join("") +
        `<button class="chip-x g2-vdel" data-del="${i}" title="Delete vertex"${sh.corners.length <= 3 ? " disabled" : ""}>✕</button></div>`;
    });
    html += `</div></details>
    <button class="btn btn-small btn-block" id="g2DrawOpening" title="Draw > Draw Opening (Polygon)">+ Draw polygon opening</button>`;
    wrap.innerHTML = html;
    head.after(wrap);
    const A = sh.kind === "wall" ? elev : plan;
    wrap.querySelectorAll(".g2-vrow[data-i] input").forEach(inp => inp.addEventListener("change", () => {
      const i = +inp.closest(".g2-vrow").dataset.i, k = +inp.dataset.k;
      const v = PU.parse("length", inp.value);
      if (!isFinite(v)) { inp.value = PU.inputValue("length", sh.corners[i][k]); return; }
      const c = sh.corners.map(x => [...x]);
      c[i][k] = v;
      // keep slabs planar when only a plan coordinate changes on a sloped slab
      if (!setCorners(sh, c, A, "table"))
        inp.value = PU.inputValue("length", sh.corners[i][k]);
    }));
    wrap.querySelectorAll(".g2-vdel").forEach(b => b.addEventListener("click", () => deleteVertex(sh, +b.dataset.del, A)));
    wrap.querySelector("#g2DrawOpening").addEventListener("click", () => start("opening"));
  }
  /** N-gon opening preview in the Properties panel (non-legacy regions). */
  function openingPreview(sh, svg) {
    if (isLegacyQuad(sh) && !(sh.openings || []).some(o => o.polygon)) return false;
    const fr = regionFrame(sh.corners);
    const outer = sh.corners.map(fr.to2);
    const holes = (sh.openings || []).map(o => openingPolygon3(sh, o).map(fr.to2));
    const us = outer.map(p => p[0]), vs = outer.map(p => p[1]);
    const u0 = Math.min(...us), u1 = Math.max(...us), v0 = Math.min(...vs), v1 = Math.max(...vs);
    const W = 236, H = Math.max(48, Math.min(236, W * (v1 - v0) / ((u1 - u0) || 1))), P = 6;
    const sc = Math.min(W / ((u1 - u0) || 1), H / ((v1 - v0) || 1));
    const X = u => P + (u - u0) * sc, Y = v => P + H - (v - v0) * sc;
    const d = pts => pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ") + " Z";
    svg.setAttribute("viewBox", `0 0 ${W + 2 * P} ${H + 2 * P}`);
    svg.innerHTML =
      `<path d="${d(outer)} ${holes.map(d).join(" ")}" fill-rule="evenodd" fill="rgba(95,143,201,0.28)" stroke="rgba(125,168,216,0.9)" stroke-width="1.2"/>` +
      holes.map(h => `<path d="${d(h)}" fill="none" stroke="rgba(53,181,229,0.75)" stroke-width="1" stroke-dasharray="4 3"/>`).join("") +
      `<text x="${P + 3}" y="${P + H - 4}" fill="rgba(140,160,185,0.75)" font-size="8" font-family="inherit">${esc(sh.uid)} · ${sh.corners.length} corners · ${holes.length} opening${holes.length === 1 ? "" : "s"}</text>`;
    return true;
  }

  /* ---------------------------------------------- styles (module-local) */
  if (!document.getElementById("g2PolyStyles")) {
    const s = document.createElement("style");
    s.id = "g2PolyStyles";
    s.textContent = `
      .g2-geom { margin: 4px 0 8px; }
      .g2-geom-sum { display: flex; flex-direction: column; gap: 2px; font-size: 11.5px; color: var(--text-2, #9aa7b4); margin-bottom: 6px; }
      .g2-geom-sum b { color: var(--text, #e8edf3); font-variant-numeric: tabular-nums; }
      .g2-verts summary { cursor: pointer; font-size: 11.5px; margin-bottom: 4px; }
      .g2-vrows { display: flex; flex-direction: column; gap: 3px; margin-bottom: 6px; max-height: 220px; overflow: auto; }
      .g2-vrow { display: grid; grid-template-columns: 18px 1fr 1fr 1fr 22px; gap: 3px; align-items: center; font-size: 11px; }
      .g2-vrow.head { color: var(--text-3, #7d8a97); font-size: 10px; text-transform: uppercase; }
      .g2-vrow input { padding: 2px 4px; font-size: 11px; min-width: 0; }
    `;
    document.head.appendChild(s);
  }

  /* ---------------------------------------------- public (additive) */
  const api = {
    start, cancel, finish,
    state: () => ({ kind: st.kind, view: st.view, pts: st.pts.map(p => [...p]), target: st.target }),
    addPoint(p) { if (!st.kind) return false; st.pts.push([+p[0], +p[1]]); refresh(); return true; },
    removeLast() { st.pts.pop(); refresh(); },
    insertVertex(uid, edgeIdx, p2) {
      const sh = (S.model.shells || []).find(s => s.uid === uid);
      return !!sh && insertVertex(sh, edgeIdx, p2, sh.kind === "wall" ? elev : plan);
    },
    deleteVertex(uid, idx) {
      const sh = (S.model.shells || []).find(s => s.uid === uid);
      return !!sh && deleteVertex(sh, idx, sh.kind === "wall" ? elev : plan);
    },
    moveVertex(uid, idx, p2) {
      const sh = (S.model.shells || []).find(s => s.uid === uid);
      if (!sh) return false;
      const A = sh.kind === "wall" ? elev : plan;
      const c = sh.corners.map(x => [...x]);
      c[idx] = A.from2(sh, p2);
      return setCorners(sh, c, A, "move");
    },
    editState: () => ({ active: ed.active, dragging: !!ed.drag }),
  };
  sky.polyDraw = api;
  sky.g2DecorateProps = decorate;
  sky.g2OpeningPreview = openingPreview;
  return api;
}
