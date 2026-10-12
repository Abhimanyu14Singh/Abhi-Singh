/* SkyFrame Wave C — Edit › Edit Shells ▸ Reshape Shell in the ELEVATION and
   3D views (ETABS Reshaper on any shell plane).

   Edit › Edit Shells ▸ Reshape Shell routes by view and shell:
     plan view + horizontal shell      → the plan reshaper (js/wb_shells.js)
     elevation + shell in the plane    → elevation reshaper: (s, z) handles,
                                         snaps (grid ticks × levels + object
                                         snaps, js/wc_snap.js)
     3D view (any shell)               → 3D reshaper: handles projected by the
                                         camera; a drag follows the camera ray
                                         on the SHELL'S PLANE (exact ray / plane
                                         intersection), snapping to joints
     otherwise a vertical shell opens the elevation of its plane when one
     exists (grid line or general line), else the 3D view.
   Drag a vertex · drag a ＋ midpoint to insert a vertex · Alt+click a
   vertex to delete it · V / Esc to finish. Each drop is one Edit op
   shell_reshape {mode: "corners", corners, on_plane: true} (one undo step):
   the backend projects the corners onto the shell's plane, so the shell
   stays planar; openings keep their absolute position (core/shell_holes.py).
   Divide / merge / chop / expand on shells with openings run unchanged
   through the edit ops (js/wc_shellholes.js mirror for ?mock=1).
   CONTRACT "Wave C interface …" § Edit Shells. */

import WSG from "./wc_snapgeom.js";
import { Frame as rFrame } from "./wc_shellholes.js";

const TOOL = "wbreshape";                        // js/wb_shells.js tool id (shared)
const NS = "http://www.w3.org/2000/svg";
const r9 = v => { const r = Math.round(v * 1e9) / 1e9; return r === 0 ? 0 : r; };

export function initWcShells(sky) {
  const S = sky.store, ee = sky.elevEditor;
  const toast = (t, m, k = "info", ms = 4500) => sky.toast && sky.toast(t, m, k, ms);
  const selShell = () => { const ids = (S.selection || []).filter(r => r.type === "shell").map(r => r.uid); return ids.length === 1 ? S.model.shells.find(s => s.uid === ids[0]) : null; };
  const horizontal = s => s.corners.every(c => Math.abs(c[2] - s.corners[0][2]) < 1e-6);
  const view3d = () => S.mode === "analyze" && S.tab === "view3d";
  const R = { mode: null, uid: null, work: null, drag: null };   // mode: "elev" | "3d"

  async function commit() {
    const s = S.model.shells.find(x => x.uid === R.uid);
    if (!s || !R.work) return;
    if (JSON.stringify(s.corners) === JSON.stringify(R.work)) return;
    try {
      if (!sky.wb || !sky.wb.shells) throw new Error("Edit Shells unavailable");
      await sky.wb.shells.run("shell_reshape", { mode: "corners", corners: R.work.map(c => c.map(Number)), on_plane: true }, "Reshape shell",
        { members: [], shells: [R.uid], links: [], points: [] });
    } catch (e) { toast("Reshape Shell", e.message, "error", 7000); }
    const s2 = S.model.shells.find(x => x.uid === R.uid);
    R.work = s2 ? s2.corners.map(c => c.slice()) : null;
    redraw();
  }
  const redraw = () => { if (R.mode === "elev" && ee) ee.renderOverlay(); if (R.mode === "3d" && sky.viewer) draw3(); };
  function stop() { R.mode = null; R.uid = null; R.work = null; R.drag = null; if (sky.viewer && sky.viewer.wcOverlay) draw3(); }
  const insertMid = i => { const a = R.work[i], b = R.work[(i + 1) % R.work.length]; R.work.splice(i + 1, 0, [0, 1, 2].map(k => (a[k] + b[k]) / 2)); return i + 1; };
  const delVertex = i => { if (R.work.length <= 3) { toast("Reshape Shell", "A shell keeps at least 3 corners.", "warn"); return false; } R.work.splice(i, 1); return true; };

  /* ================= elevation */
  const elevHandles = ed => {
    const out = [];
    R.work.forEach((c, i) => {
      const [x, y] = ed.toScreen(ed.sOf(c), c[2]);
      out.push({ kind: "v", i, x, y });
      const d = R.work[(i + 1) % R.work.length], [mx, my] = ed.toScreen((ed.sOf(c) + ed.sOf(d)) / 2, (c[2] + d[2]) / 2);
      out.push({ kind: "m", i, x: mx, y: my });
    });
    return out;
  };
  function startElev(s) {
    if (!ee || !ee.plane() || !s.corners.every(c => ee.inPlane(c))) return false;
    R.mode = "elev"; R.uid = s.uid; R.work = s.corners.map(c => c.slice()); R.drag = null;
    sky.setTool(TOOL);
    ee.renderOverlay();
    toast("Reshape Shell (elevation)", "Drag a vertex · drag a ＋ midpoint to add one · Alt+click a vertex to delete it · V / Esc to finish.");
    return true;
  }
  if (ee) {
    const svg = ee.svg;
    const hit = e => {
      if (R.mode !== "elev" || ee.tool !== TOOL || !R.work) return null;
      const r = svg.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
      let best = null, bd = 9;
      for (const h of elevHandles(ee)) { const d = Math.hypot(h.x - px, h.y - py) - (h.kind === "v" ? 1 : 0); if (d < bd) { bd = d; best = h; } }
      return best;
    };
    svg.addEventListener("pointerdown", e => {
      if (e.button !== 0) return;
      const h = hit(e);
      if (!h) return;
      e.stopImmediatePropagation(); e.preventDefault();
      if (h.kind === "v" && e.altKey) { if (delVertex(h.i)) commit(); return; }
      R.drag = h.kind === "m" ? insertMid(h.i) : h.i;
      try { svg.setPointerCapture(e.pointerId); } catch { /* ignore */ }
      ee.renderOverlay();
    }, true);
    svg.addEventListener("pointermove", e => {
      if (R.mode !== "elev" || R.drag == null) return;
      e.stopImmediatePropagation();
      const r = svg.getBoundingClientRect();
      const w = ee.toWorld(e.clientX - r.left, e.clientY - r.top), sn = ee.snap(w);
      const p = sn && sn.dPx <= 14 ? sn : w;
      R.work[R.drag] = ee.world3(+p.s, +p.z).map(r9);
      ee.hoverSnap = sn && sn.dPx <= 14 ? sn : null;
      ee.renderOverlay();
    }, true);
    const up = e => { if (R.mode !== "elev" || R.drag == null) return; e.stopImmediatePropagation(); R.drag = null; commit(); };
    svg.addEventListener("pointerup", up, true);
    svg.addEventListener("pointercancel", up, true);
    const prevTool = ee.wcToolHook;
    ee.wcToolHook = (tool, w, ed) => (tool === TOOL ? true : (prevTool ? prevTool(tool, w, ed) : false));
    const prevOv = ee.wcOverlay;
    ee.wcOverlay = (g, ed) => {
      if (prevOv) prevOv(g, ed);
      if (R.mode !== "elev" || ed.tool !== TOOL || !R.work) return;
      const mk = (tag, a) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(a)) n.setAttribute(k, v); g.appendChild(n); return n; };
      mk("polygon", { points: R.work.map(c => ed.toScreen(ed.sOf(c), c[2]).join(",")).join(" "), fill: "rgba(53,181,229,0.10)", stroke: "#35b5e5", "stroke-width": 1.5, "stroke-dasharray": "5 3", "data-wc": "reshape" });
      for (const h of elevHandles(ed)) {
        if (h.kind === "v") mk("rect", { x: h.x - 4.5, y: h.y - 4.5, width: 9, height: 9, fill: R.drag === h.i ? "#35b5e5" : "#0b1220", stroke: "#35b5e5", "stroke-width": 1.5, "data-wc-vertex": String(h.i) });
        else { const t = mk("text", { x: h.x, y: h.y + 4, fill: "#35b5e5", "font-size": 13, "font-weight": 700, "text-anchor": "middle", "data-wc-mid": String(h.i) }); t.textContent = "+"; }
      }
    };
    const os = ee.setTool.bind(ee);
    ee.setTool = t => { if (t !== TOOL && R.mode === "elev") stop(); os(t); };
  }

  /* ================= 3D */
  let fr = null;                                 // plane frame of the shell
  const proj3 = () => { const v = sky.viewer, P = v._makeProjector(); return p => { const c = P.toCam(p); return c[2] < P.near ? null : P.proj(c); }; };
  const handles3 = sp => {
    const out = [];
    R.work.forEach((c, i) => {
      const a = sp(c); if (a) out.push({ kind: "v", i, x: a.x, y: a.y });
      const d = R.work[(i + 1) % R.work.length], m = sp(c.map((v, k) => (v + d[k]) / 2)); if (m) out.push({ kind: "m", i, x: m.x, y: m.y });
    });
    return out;
  };
  function draw3() {
    const v = sky.viewer;
    if (!v) return;
    let c = v.wcReshapeOv;
    if (!c) {
      const parent = v.canvas.parentElement;
      if (getComputedStyle(parent).position === "static") parent.style.position = "relative";
      c = document.createElement("canvas"); c.className = "wc-3d-overlay"; c.setAttribute("aria-hidden", "true");
      parent.appendChild(c); v.wcReshapeOv = c;
    }
    const w = v.canvas.clientWidth, h = v.canvas.clientHeight, dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
    if (R.mode !== "3d" || !R.work) return;
    const sp = proj3(), pts = R.work.map(sp);
    if (pts.every(Boolean)) {
      g.fillStyle = "rgba(53,181,229,0.12)"; g.strokeStyle = "#35b5e5"; g.lineWidth = 1.5; g.setLineDash([5, 3]);
      g.beginPath(); pts.forEach((p, i) => (i ? g.lineTo(p.x, p.y) : g.moveTo(p.x, p.y))); g.closePath(); g.fill(); g.stroke(); g.setLineDash([]);
    }
    for (const hd of handles3(sp)) {
      if (hd.kind === "v") { g.fillStyle = R.drag === hd.i ? "#35b5e5" : "#0b1220"; g.strokeStyle = "#35b5e5"; g.fillRect(hd.x - 4.5, hd.y - 4.5, 9, 9); g.strokeRect(hd.x - 4.5, hd.y - 4.5, 9, 9); }
      else { g.fillStyle = "#35b5e5"; g.font = "700 13px sans-serif"; g.textAlign = "center"; g.fillText("+", hd.x, hd.y + 4); g.textAlign = "start"; }
    }
  }
  let bound3 = false, raf = 0;
  const loop = () => { if (R.mode !== "3d") { raf = 0; return; } draw3(); raf = requestAnimationFrame(loop); };
  /** 3D point under the cursor on the shell plane (snapped joints projected). */
  function onPlane(mx, my) {
    const v = sky.viewer, ray = WSG.ray(v, mx, my);
    let p = WSG.planeHit(ray.o, ray.d, fr.o, fr.n);
    const sn = sky.wc.snap ? sky.wc.snap.pick3(v, mx, my, null) : null;
    if (sn) p = fr.project(sn.p);
    return p ? p.map(r9) : null;
  }
  function bind3() {
    const v = sky.viewer;
    if (bound3 || !v) return;
    bound3 = true;
    const parent = v.canvas.parentElement;
    const pos = e => { const r = v.canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    const hit = e => {
      if (R.mode !== "3d" || !R.work) return null;
      const [px, py] = pos(e);
      let best = null, bd = 9;
      for (const h of handles3(proj3())) { const d = Math.hypot(h.x - px, h.y - py) - (h.kind === "v" ? 1 : 0); if (d < bd) { bd = d; best = h; } }
      return best;
    };
    parent.addEventListener("pointerdown", e => {
      if (e.button !== 0 || e.target !== v.canvas) return;
      const h = hit(e);
      if (!h) return;
      e.stopPropagation(); e.preventDefault();
      if (h.kind === "v" && e.altKey) { if (delVertex(h.i)) commit(); return; }
      R.drag = h.kind === "m" ? insertMid(h.i) : h.i;
      try { v.canvas.setPointerCapture(e.pointerId); } catch { /* ignore */ }
    }, true);
    parent.addEventListener("pointermove", e => {
      if (R.mode !== "3d" || R.drag == null) return;
      e.stopPropagation();
      const [x, y] = pos(e), p = onPlane(x, y);
      if (p) R.work[R.drag] = p;
    }, true);
    const up = e => { if (R.mode !== "3d" || R.drag == null) return; e.stopPropagation(); R.drag = null; commit(); };
    parent.addEventListener("pointerup", up, true);
    parent.addEventListener("pointercancel", up, true);
  }
  function start3(s) {
    if (!sky.viewer) return false;
    if (sky.etabs && sky.etabs.showResult) sky.etabs.showResult("view3d"); else { sky.setMode("analyze"); sky.switchTab("view3d"); }
    if (sky.wc.pick3d && sky.wc.pick3d.active()) sky.wc.pick3d.stop();
    bind3();
    fr = new rFrame(s.corners);
    R.mode = "3d"; R.uid = s.uid; R.work = s.corners.map(c => c.slice()); R.drag = null;
    if (!raf) raf = requestAnimationFrame(loop);
    toast("Reshape Shell (3D)", "Drag a vertex on the shell's plane · drag a ＋ midpoint to add one · Alt+click a vertex to delete it · Esc to finish.");
    return true;
  }
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape" || !R.mode) return;
    if (R.drag != null) { const s = S.model.shells.find(x => x.uid === R.uid); R.work = s ? s.corners.map(c => c.slice()) : null; R.drag = null; redraw(); return; }
    if (R.mode === "3d") stop();
  });
  const st0 = sky.setTool;
  sky.setTool = t => { if (R.mode === "3d" && t === "select") stop(); return st0(t); };

  /* ================= routing (Edit › Edit Shells › Reshape Shell) */
  function elevLineFor(s) {
    if (!sky.rebuildElevSelect || !ee) return null;
    const sel = document.getElementById("elevLineSelect");
    if (!sel) return null;
    const cur = S.elevLine;
    for (const o of sel.options) {
      sky.setElevLine(o.value);
      if (ee.plane() && s.corners.every(c => ee.inPlane(c))) return o.value;
    }
    if (cur) sky.setElevLine(cur);
    return null;
  }
  function start() {
    const s = selShell();
    if (!s) { toast("Reshape Shell", "Select exactly one shell (V) first.", "warn"); return false; }
    if (view3d()) return start3(s);
    if (S.mode === "model" && S.view === "elev" && ee && ee.plane() && s.corners.every(c => ee.inPlane(c))) return startElev(s);
    if (horizontal(s)) return sky.wb && sky.wb.shells ? sky.wb.shells.startReshape() : false;
    const n = new rFrame(s.corners).n;
    if (Math.abs(n[2]) < 1e-6) {                  // vertical: find its elevation
      sky.setMode("model"); sky.setView("elev");
      if (elevLineFor(s)) return startElev(s);
    }
    return start3(s);
  }
  const it = sky.menu && sky.menu.item && sky.menu.item("edit-wb-shreshape");
  if (it) { it.wcLegacyFn = it.fn; it.fn = start; it.hint = "vertex reshaper (plan · elevation · 3D)"; }

  sky.wc.shells = { start, startElev, start3, stop, commit,
    state: () => ({ mode: R.mode, uid: R.uid, work: R.work && R.work.map(c => c.slice()), drag: R.drag }),
    set: w => { R.work = w; redraw(); }, TOOL };
}
