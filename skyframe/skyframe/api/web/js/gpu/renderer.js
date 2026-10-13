/* SkyFrame E3 — GPU (WebGL2, three.js r170) backend for Viewer3D.

   Attached to a Viewer3D instance by js/gpu/gpuhook.js; the viewer object,
   its public API and every module hook stay exactly as they are:

   * the GPU draws, into a WebGL canvas placed UNDER the viewer's 2D canvas:
       ground grid lines · diaphragm hulls · shell fills + edges (with even-odd
       openings, triangulated) · section-cut planes (dashed edges) · frame
       centerlines (ONE instanced fat-line draw) · extruded frames (one
       instanced draw per section profile; per-instance matrix = local axes
       of the extruded view, incl. frame_axis_convention, cardinal point and
       joint offsets — taken from the VX geometry cache, so the convention is
       the very same code) · extruded shells (plates) · shell-force contours
       (per-vertex scalar + colormap in the fragment shader) · A3 shell
       contours · cracked-quad tint · FE mesh lines · deformed / mode / buckling
       shapes (displacement attribute × scale uniform in the vertex shader,
       Hermite member curves sampled into 8 sub-segments, extruded deformed
       prisms with 5 displaced stations);
   * the viewer's own _render() still runs, on its now transparent 2D canvas,
       as the overlay pass: grid labels, links, supports, springs, rigid zones,
       panel zones, foundations, badges, releases, section-cut labels, VX
       annotations (joints / axes / labels — decluttered — / loads / cut
       outline), every overlay hook (checkmodel, g2, groups, np, uh, pt, plot
       functions incl. 3D force diagrams, wc diagrams + video caption) and the
       axis triad;
   * picking: an ID buffer (frame centerlines, rendered off-screen on demand,
       a (2·thr+1)² pixel window read back) gives the candidates, the exact
       Classic distance rule picks the winner → same member as Classic;
       Ctrl+drag box / crossing selection uses a world-space uniform grid index;
   * render on demand: no loop while idle (viewer._dirty becomes a setter
       that schedules one frame); inertia and mode animation keep frames going
       only while they last.
*/
import { THREE, FatLines, makeMeshMat, TriBuilder, triangulate2, unitPrism, PrismBatch, cssRGBA } from "./glprims.js";
import { V3_STYLE as gV3Style, boxHitSeg as gBoxHitSeg } from "../viewer3d.js";
import { regionFrame as gRegionFrame, memberAxes as gMemberAxes, CP_GRID as G_CP_GRID } from "../polygeom.js";
import { isNpSec as gIsNpSec } from "../npsect.js";

const GC = gV3Style.COLORS;
// draw order inside each three.js pass (opaque first, then transparent by renderOrder). Translucent
// fills go BEFORE the frame lines (the Classic painter order puts lines over the fills they lie in;
// a depth-writing faint line drawn first would punch dark holes into a later fill).
const RO = { solid: 0, edge: 1, fills: 2, fillEdges: 3, cut: 4, contour: 5, grid: 6, lines: 7, deformed: 8, tint: 9 };

function addBox(bb, p) {
  for (let i = 0; i < 3; i++) { if (p[i] < bb[0][i]) bb[0][i] = p[i]; if (p[i] > bb[1][i]) bb[1][i] = p[i]; }
}
const sub3 = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];

/** Strip a duplicated closing vertex (keeps 2D / 3D index maps aligned). */
function openLoop(pts) {
  if (pts.length > 2) {
    const a = pts[0], b = pts[pts.length - 1];
    if (Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2]) < 1e-12) return pts.slice(0, -1);
  }
  return pts;
}

/** Triangulate a planar 3D polygon with holes into tb (flat rgba, optional lit normal / disp / scalar). */
function fillPoly(tb, corners, openings, rgba, extra) {
  const outer = openLoop(corners);
  if (outer.length < 3) return;
  const holes = (openings || []).map(openLoop).filter(h => h.length >= 3);
  let fr;
  try { fr = gRegionFrame(outer); } catch (e) { return; }
  const pts = [...outer, ...holes.flat()];
  let tris;
  try { tris = triangulate2(outer.map(fr.to2), holes.map(h => h.map(fr.to2))); } catch (e) { return; }
  for (const t of tris) tb.tri(pts[t[0]], pts[t[1]], pts[t[2]], rgba, extra);
}

/** Polygon outline segments into a list [[a, b], …]. */
function outline(list, pts) {
  const n = pts.length;
  for (let i = 0; i < n; i++) list.push([pts[i], pts[(i + 1) % n]]);
}

function linesFrom(segs, rgba, w, opts = {}) {
  const fl = new FatLines(segs.length, opts);
  segs.forEach(([a, b, c, ww], i) => fl.set(i, a, b, c || rgba, ww || w));
  return fl;
}

export class GpuBackend {
  constructor(v, getSky, onLost) {
    this.v = v;
    this.getSky = getSky || (() => window.__sky);
    this.onLost = onLost || (() => {});
    this.on = false;
    this.modelVer = 0;
    this._frame = 0;
    this._dirtyFlag = true;
    this._raf = 0;
    this.base = null;          // {group, lines, lineSegs, prisms, ...}
    this.layers = {};          // results-driven layers
    this.lastStats = {};
  }

  /* ================================================================ lifecycle */
  attach() {
    const v = this.v;
    const parent = v.canvas.parentElement;
    if (!parent) throw new Error("viewer canvas has no parent");
    const cv = document.createElement("canvas");
    cv.className = "g3-canvas";
    cv.setAttribute("aria-hidden", "true");
    cv.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block;pointer-events:none;";
    this.renderer = new THREE.WebGLRenderer({ canvas: cv, antialias: true, alpha: true, premultipliedAlpha: true,
      powerPreference: "high-performance", preserveDrawingBuffer: false, stencil: false });
    this.canvas = cv;
    // grid labels are painted UNDER the WebGL canvas, so solids hide them exactly like the Classic painter order
    this.under = document.createElement("canvas");
    this.under.className = "g3-under";
    this.under.setAttribute("aria-hidden", "true");
    this.under.style.cssText = cv.style.cssText;
    parent.insertBefore(this.under, v.canvas);
    parent.insertBefore(cv, v.canvas);
    const r = this.renderer;
    r.setClearColor(0x000000, 0);
    r.sortObjects = true;
    this.gl = r.getContext();
    this._onLost = e => { e.preventDefault(); this.onLost(); };
    cv.addEventListener("webglcontextlost", this._onLost, false);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.05, 1000);
    this.camera.matrixAutoUpdate = false;
    this.root = new THREE.Group();
    this.scene.add(this.root);
    this.pickScene = new THREE.Scene();

    // ---- viewer overrides (all restored by detach)
    this._own = {};
    for (const k of ["_pickAt", "_pickBox", "_resize", "_loop"]) this._own[k] = Object.prototype.hasOwnProperty.call(v, k) ? v[k] : undefined;
    v._pickAt = (mx, my, thr) => this.pickAt(mx, my, thr);
    v._pickBox = (x0, y0, x1, y1, crossing) => this.pickBox(x0, y0, x1, y1, crossing);
    const rs0 = v._resize;
    v._resize = () => { rs0.call(v); this.resize(); };
    cancelAnimationFrame(v._raf);
    v._loop = () => {};
    this._dirtyFlag = true;
    Object.defineProperty(v, "_dirty", { configurable: true, enumerable: true,
      get: () => this._dirtyFlag,
      set: val => { this._dirtyFlag = !!val; if (val && !this._inRender) this.schedule(); } });
    Object.defineProperty(v, "_segsScreen", { configurable: true, enumerable: true,
      get: () => this.segsScreen(), set: () => {} });
    this._onModel = () => { this.modelVer++; };
    document.addEventListener("sky:model-changed", this._onModel);
    v.gpu = this;
    this.on = true;
    this.resize();
    this.schedule();
  }

  detach() {
    const v = this.v;
    if (!this.on) return;
    this.on = false;
    cancelAnimationFrame(this._raf); this._raf = 0;
    document.removeEventListener("sky:model-changed", this._onModel);
    delete v._dirty; delete v._segsScreen;
    v._dirty = true; v._segsScreen = [];
    for (const [k, val] of Object.entries(this._own)) { if (val === undefined) delete v[k]; else v[k] = val; }
    this.disposeBase();
    for (const k of Object.keys(this.layers)) this.disposeLayer(k);
    if (this._rt) this._rt.dispose();
    try { this.canvas.removeEventListener("webglcontextlost", this._onLost); } catch (e) { /* gone */ }
    try { this.renderer.dispose(); this.renderer.forceContextLoss(); } catch (e) { /* already lost */ }
    this.canvas.remove(); this.under.remove();
    if (v.gpu === this) v.gpu = null;
    v._loop();   // Classic rAF loop resumes
  }

  schedule() {
    if (this._raf || !this.on) return;
    this._raf = requestAnimationFrame(() => this._tick());
  }

  _tick() {
    this._raf = 0;
    if (!this.on) return;
    const v = this.v;
    const animating = !!(v.overlay.modal || v.overlay.buckling);
    let more = animating;
    if (!animating && (Math.abs(v.vyaw) > 0.0004 || Math.abs(v.vpitch) > 0.0004)) {
      v.yaw += v.vyaw; v.pitch = Math.min(1.52, Math.max(-1.52, v.pitch + v.vpitch));
      v.vyaw *= 0.90; v.vpitch *= 0.90;
      more = true;
    }
    this._dirtyFlag = false;
    try { this.render(); } catch (e) { this.lastError = String(e && e.stack || e); }
    if (more || this._dirtyFlag) this.schedule();
  }

  resize() {
    const v = this.v;
    const parent = v.canvas.parentElement;
    const w = parent ? parent.clientWidth : v.canvas.clientWidth;
    const h = parent ? parent.clientHeight : v.canvas.clientHeight;
    if (!w || !h || !this.renderer) return;
    const dpr = window.devicePixelRatio || 1;
    if (w !== this.w || h !== this.h || dpr !== this.dpr) {
      this.w = w; this.h = h; this.dpr = dpr;
      this.renderer.setPixelRatio(dpr);
      this.renderer.setSize(w, h, false);
      this.under.width = Math.round(w * dpr); this.under.height = Math.round(h * dpr);
      this._idFrame = -1;
    }
    this._dirtyFlag = true;
    this.schedule();
  }

  /* ================================================================ frame */
  render() {
    const v = this.v;
    if (!this.on) return;
    this._frame++;
    if (!this.w || this.w !== (v.canvas.parentElement || v.canvas).clientWidth) this.resize();
    this._inRender = true;
    const t0 = performance.now();
    try {
      if (v.model && this.w) this.renderGL();
      else { this.renderer.clear(); this.under.getContext("2d").clearRect(0, 0, this.under.width, this.under.height); }
      const t1 = performance.now();
      v._render();   // 2D overlay pass (Classic code, heavy passes skipped in GPU mode)
      this.lastStats.overlay_ms = +(performance.now() - t1).toFixed(2);
    } finally { this._inRender = false; }
    this.lastStats.cpu_ms = +(performance.now() - t0).toFixed(2);
  }

  sky() { try { return this.getSky() || {}; } catch (e) { return {}; } }

  renderGL() {
    const v = this.v, sky = this.sky();
    const P = v._makeProjector();
    let VX = null;
    if (v.vxFrame) { try { VX = v.vxFrame(P, v); } catch (e) { VX = null; } }
    this._VX = VX;
    const ov = v.overlay;
    const overlayActive = !!(ov.deformed || ov.modal || ov.buckling);
    const vxo = VX && sky.vx && sky.vx.opts ? sky.vx.opts() : null;
    const selKey = vxo && vxo.selOnly ? ((sky.store && sky.store.selection) || []).map(r => `${r.type}:${r.uid}`).sort().join("|") : "";
    const sig = [v.model, v.segs, v.shellPolys, v.gridLines, v.sectionCutPolys, vxo ? JSON.stringify(vxo) : "", selKey,
      overlayActive && vxo && vxo.extrude ? 1 : 0, (VX && VX.lineScale) || 1, this.modelVer];
    if (!this.base || sig.some((x, i) => x !== this.base.sig[i])) {
      const tb = performance.now();
      this.buildBase(VX, vxo, overlayActive, sky);
      this.base.sig = sig;
      this.lastStats.build_ms = +(performance.now() - tb).toFixed(1);
    }
    const B = this.base;
    this.syncColors(VX);

    // ---- contour / results layers
    const contour = overlayActive ? null : this.contourState(sky);
    const deform = overlayActive && v.results ? this.deformState() : null;

    // ---- visibility (mirrors the Classic pass order / rules)
    const vis = (o, on) => { if (o) o.visible = !!on; };
    const gridOn = !(VX && !VX.grid);
    vis(B.grid && B.grid.mesh, gridOn);
    const hullsOn = !(VX && VX.hideHulls) && !contour && !overlayActive;
    vis(B.hullFill, hullsOn); vis(B.hullEdge && B.hullEdge.mesh, hullsOn);
    const shellAlpha = VX ? VX.shellAlpha : 1;
    for (const grp of [B.shellOther, B.shellShell]) {
      if (!grp) continue;
      const on = !(contour && grp === B.shellShell);
      vis(grp.fill, on && !overlayActive);
      if (grp.fill) grp.fill.material.uniforms.uAlpha.value = shellAlpha;
      if (grp.edge) {
        vis(grp.edge.mesh, on);
        const u = grp.edge.uniforms;
        u.uAlpha.value = overlayActive ? GC.ghost : shellAlpha;
        u.uWScale.value = overlayActive ? 1 / 1.2 : 1;
      }
    }
    if (B.cutFill) B.cutFill.material.uniforms.uAlpha.value = overlayActive ? 0.10 : 0.18;
    const extruding = !!(VX && VX.extrudeFrames);
    vis(B.lines && B.lines.mesh, !extruding);
    for (const pb of B.prisms || []) { vis(pb.mesh, extruding); vis(pb.edges, extruding && vxo && vxo.edges); }
    if (B.npMesh) vis(B.npMesh, extruding);
    for (const [k, m] of Object.entries(B.plates || {})) vis(m, !!(vxo && vxo.extrude && vxo.extrudeShells && !overlayActive) && !(contour && k === "shell"));
    vis(B.plateEdges && B.plateEdges.mesh, !!(vxo && vxo.extrude && vxo.extrudeShells && !overlayActive && vxo.edges));

    // ---- results layers
    this.syncMeshLines(!overlayActive && !contour);
    this.syncContour(contour);
    this.syncTint(!overlayActive);
    this.syncDeformed(deform, VX, vxo, sky);
    // Wave G hook — displacement contours on (deformed) objects: per-vertex colour + displacement attributes (js/g_overlay.js)
    if (v.gResGL) { try { v.gResGL(this, { THREE, FatLines, TriBuilder, makeMeshMat, cssRGBA, RO }); } catch (e) { this.lastError = String(e && e.stack || e); } }

    // ---- camera + uniforms
    this.setCamera();
    const minDepth = Math.max(v.dist - (v.radius || 10), 0.05), maxDepth = v.dist + (v.radius || 10);
    const L = this.light();
    this.forLineUniforms(u => {
      u.uRes.value.set(this.w * this.dpr, this.h * this.dpr); u.uPx.value = this.dpr;
      u.uFade.value.set(minDepth, maxDepth);
    });
    // under an overlay the model is a ghost: it must not hide the deformed shape (Classic paints it on top)
    for (const fl of [B.lines, B.grid, B.shellOther && B.shellOther.edge, B.shellShell && B.shellShell.edge]) if (fl) fl.mat.depthWrite = !overlayActive;
    if (B.lines) {
      B.lines.uniforms.uGhost.value = overlayActive ? GC.ghost : 0;
      B.lines.uniforms.uWScale.value = (VX && VX.lineScale) || 1;
    }
    this.root.traverse(o => { const u = o.material && o.material.uniforms; if (u && u.uLight) u.uLight.value.copy(L); });
    this.renderer.render(this.scene, this.camera);
    this.drawUnder(P, VX);
    const inf = this.renderer.info.render;
    Object.assign(this.lastStats, { calls: inf.calls, triangles: inf.triangles, lines: inf.lines,
      instances: (B.lines ? B.lines.n : 0) + (B.prisms || []).reduce((a, p) => a + p.n, 0) });
  }

  /** Ground-grid labels on the under-canvas (Classic draws them before the scene). */
  drawUnder(P, VX) {
    const v = this.v, c = this.under, g = c.getContext("2d");
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g.clearRect(0, 0, this.w, this.h);
    if (VX && (!VX.grid || VX.bubbles === false)) return;
    g.font = "600 11px -apple-system, 'Segoe UI', sans-serif";
    g.textAlign = "center"; g.textBaseline = "middle";
    for (const gl of v.gridLabels || []) {
      const pc = P.toCam(gl.p);
      if (pc[2] > P.near) { const sp = P.proj(pc); g.fillStyle = gl.color || GC.gridLabel; g.fillText(gl.text, sp.x, sp.y); }
    }
  }

  forLineUniforms(fn) {
    this.root.traverse(o => { const u = o.material && o.material.uniforms; if (u && u.uRes) fn(u); });
  }

  /** Classic extruded-view head light: L = norm(−F·0.55 + U·0.62 − R·0.38). */
  light() {
    const b = this.v._basis();
    const l = [0, 1, 2].map(k => -b.fwd[k] * 0.55 + b.up[k] * 0.62 - b.right[k] * 0.38);
    const n = Math.hypot(...l) || 1;
    return new THREE.Vector3(l[0] / n, l[1] / n, l[2] / n);
  }

  setCamera() {
    const v = this.v, cam = this.camera;
    const b = v._basis();
    const bb = this.base ? this.base.bbox : [[-1, -1, -1], [1, 1, 1]];
    const pad = (v.radius || 10) * (0.3 + 0.15 * Math.max(1, (v.overlay && v.overlay.scaleMult) || 1));
    const lo = bb[0].map(x => x - pad), hi = bb[1].map(x => x + pad);
    // distance from the eye to the padded scene box → tight near / far (depth precision)
    let d2 = 0, far2 = 0;
    for (let i = 0; i < 3; i++) {
      const e = b.eye[i];
      const dd = e < lo[i] ? lo[i] - e : e > hi[i] ? e - hi[i] : 0;
      d2 += dd * dd;
      const fd = Math.max(Math.abs(e - lo[i]), Math.abs(e - hi[i]));
      far2 += fd * fd;
    }
    cam.near = Math.max(0.05, Math.sqrt(d2) * 0.5);
    cam.far = Math.max(Math.sqrt(far2) * 1.05 + 1, cam.near * 2);
    cam.fov = v.fov * 180 / Math.PI;
    cam.aspect = this.w / this.h;
    cam.updateProjectionMatrix();
    const R = new THREE.Vector3(...b.right), U = new THREE.Vector3(...b.up), Bk = new THREE.Vector3(-b.fwd[0], -b.fwd[1], -b.fwd[2]);
    cam.matrix.makeBasis(R, U, Bk).setPosition(b.eye[0], b.eye[1], b.eye[2]);
    cam.matrixWorldNeedsUpdate = true;
    cam.updateMatrixWorld(true);
  }

  /* ================================================================ base scene */
  disposeBase() {
    if (!this.base) return;
    this.base.group.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
    this.root.remove(this.base.group);
    this.pickScene.clear();
    this.base = null;
    this._index = null;
  }

  buildBase(VX, vxo, overlayActive, sky) {
    const v = this.v;
    this.disposeBase();
    const group = new THREE.Group();
    const B = { group, sig: [], bbox: [[Infinity, Infinity, Infinity], [-Infinity, -Infinity, -Infinity]] };
    const add = (o, order) => { if (!o) return; o.renderOrder = order; group.add(o); return o; };

    // ---- grid (per-system tints)
    const gl = (v.gridLines || []).map(g => [g.a, g.b, cssRGBA(g.color || GC.grid), 1]);
    if (gl.length) { B.grid = linesFrom(gl, null, 1, { cap: 0 }); add(B.grid.mesh, RO.grid); }
    for (const g of v.gridLines || []) { addBox(B.bbox, g.a); addBox(B.bbox, g.b); }

    // ---- diaphragm hulls
    if ((v.slabs || []).length) {
      const tb = new TriBuilder(), ed = [];
      for (const q of v.slabs) { fillPoly(tb, q, [], cssRGBA(GC.slabFill)); outline(ed, q); }
      const m = new THREE.Mesh(tb.build(), makeMeshMat({ transparent: true, depthWrite: false }));
      m.frustumCulled = false; B.hullFill = add(m, RO.fills);
      B.hullEdge = linesFrom(ed, cssRGBA(GC.slabEdge), 1, { cap: 0, depthWrite: false }); add(B.hullEdge.mesh, RO.fillEdges);
    }

    // ---- shell regions (fill with openings + outline), split by behavior for the contour rule
    const shellGroups = { shell: { tb: new TriBuilder(), ed: [] }, other: { tb: new TriBuilder(), ed: [] } };
    for (const sh0 of v.shellPolys || []) {
      for (const p of sh0.corners) addBox(B.bbox, p);
      const sh = VX ? VX.shell(sh0) : sh0;
      if (!sh) continue;
      const wall = sh.kind === "wall";
      const gk = shellGroups[sh.behavior === "shell" ? "shell" : "other"];
      fillPoly(gk.tb, sh.corners, sh.openings || [], cssRGBA(wall ? GC.wallShell : GC.slabShell));
      const ec = cssRGBA(wall ? GC.wallShellEdge : GC.slabShellEdge);
      for (const loop of [sh.corners, ...(sh.openings || [])]) {
        const n = loop.length;
        for (let i = 0; i < n; i++) gk.ed.push([loop[i], loop[(i + 1) % n], ec, 1.2]);
      }
    }
    for (const [k, gk] of Object.entries(shellGroups)) {
      const grp = {};
      if (gk.tb.count) {
        grp.fill = new THREE.Mesh(gk.tb.build(), makeMeshMat({ transparent: true, depthWrite: false }));
        grp.fill.frustumCulled = false; add(grp.fill, RO.fills);
      }
      if (gk.ed.length) { grp.edge = linesFrom(gk.ed, null, 1.2, { cap: 0 }); add(grp.edge.mesh, RO.fillEdges); }
      B[k === "shell" ? "shellShell" : "shellOther"] = grp;
    }

    // ---- section-cut planes (translucent amber, dashed border)
    if ((v.sectionCutPolys || []).length) {
      const tb = new TriBuilder(), ed = [];
      for (const cut of v.sectionCutPolys) { fillPoly(tb, cut.corners, [], cssRGBA(GC.cutFill)); outline(ed, cut.corners); }
      B.cutFill = new THREE.Mesh(tb.build(), makeMeshMat({ transparent: true, depthWrite: false }));
      B.cutFill.frustumCulled = false; add(B.cutFill, RO.cut);
      B.cutEdge = linesFrom(ed, cssRGBA(GC.cutEdge), 1.4, { cap: 0, dash: 10, depthWrite: false }); add(B.cutEdge.mesh, RO.cut);
    }

    // ---- frame centerlines: ONE instanced draw (+ the pick-ID twin)
    const segs = v.segs || [];
    const visList = [];
    for (let i = 0; i < segs.length; i++) {
      const vs = VX ? VX.seg(segs[i]) : segs[i];
      addBox(B.bbox, segs[i].p1); addBox(B.bbox, segs[i].p2);
      if (vs) visList.push([segs[i], vs]);
    }
    const lines = new FatLines(visList.length, { pick: true });
    B.lineSegs = new Array(visList.length);
    visList.forEach(([seg, vs], k) => {
      lines.set(k, vs.p1, vs.p2, [0, 0, 0, -1], 1.3);
      lines.ID[k] = k + 1;
      B.lineSegs[k] = seg;
    });
    lines.touch();
    B.lines = lines;
    add(lines.mesh, RO.lines);
    this.pickScene.add(lines.pickMesh);

    for (const lk of v.linkSegs || []) { addBox(B.bbox, lk.p1); addBox(B.bbox, lk.p2); }
    for (const p of v.springPts || []) addBox(B.bbox, p);
    if (!isFinite(B.bbox[0][0])) B.bbox = [[0, 0, 0], [10, 10, 10]];

    // ---- extruded frames / shells (VX geometry cache → same axes / offsets / profiles)
    const geo = vxo && vxo.extrude && sky.vx && sky.vx.geo ? sky.vx.geo() : null;
    B.prisms = [];
    if (geo && !overlayActive) this.buildExtruded(B, geo, add);
    if (geo && vxo.extrudeShells && !overlayActive) this.buildPlates(B, geo, vxo, add);
    B.geo = geo;
    this.root.add(group);
    this.base = B;
    this._colorSig = null;
  }

  /** Extruded members: instanced unit prisms per profile; nonprismatic (tapered) members as explicit meshes. */
  buildExtruded(B, geo, add) {
    const m = this.v.model;
    const byKey = new Map();
    const npTB = new TriBuilder();
    B.npMeshes = [];
    for (const mesh of geo.memberMeshes) {
      if (!mesh.parts.length) continue;
      const mm = geo.byUid.get(mesh.uid) || { uid: mesh.uid, kind: mesh.seg.kind, section: mesh.seg.section, pi: mesh.seg.p1, pj: mesh.seg.p2 };
      const sec = (m.sections || {})[mm.section];
      const full = mesh.parts[0].full;
      if (gIsNpSec(sec)) {   // taper: explicit rings from the VX prism (exact same vertices)
        const prof = geo.prof(mm.section, "full");
        B.npMeshes.push({ mesh, from: npTB.count, prof });
        this.npPrism(npTB, full, prof, [0.5, 0.5, 0.5, 1]);
        B.npMeshes[B.npMeshes.length - 1].to = npTB.count;
        continue;
      }
      const prof = geo.prof(mm.section, "full");
      const key = mm.section + "\u0001" + prof.pts.length + "\u0001" + (prof.holes || []).length;
      let e = byKey.get(key);
      if (!e) { e = { prof, list: [] }; byKey.set(key, e); }
      const A = full.A, K = full.K;
      const T = [A[0], A[1], A[2]];
      const X = [A[(K - 1) * 3] - T[0], A[(K - 1) * 3 + 1] - T[1], A[(K - 1) * 3 + 2] - T[2]];
      const ax = gMemberAxes(mm, m);
      e.list.push({ mesh, X, Y: ax.y, Z: ax.z, T });
    }
    for (const e of byKey.values()) {
      const pb = new PrismBatch(unitPrism(e.prof), e.list.length);
      e.list.forEach((it, i) => { pb.set(i, it.X, it.Y, it.Z, it.T, [0.5, 0.5, 0.5]); pb.uids[i] = it.mesh; });
      pb.meshes = e.list.map(it => it.mesh);
      add(pb.mesh, RO.solid); add(pb.edges, RO.edge);
      B.prisms.push(pb);
    }
    if (npTB.count) {
      const g = npTB.build();
      B.npMesh = new THREE.Mesh(g, makeMeshMat({ lit: true }));
      B.npMesh.frustumCulled = false;
      add(B.npMesh, RO.solid);
    }
  }

  /** Tapered member from VX rings: sides between rings + both caps (profile triangulation). */
  npPrism(tb, ms, prof, rgba) {
    const n = ms.n, K = ms.K, V = ms.V;
    const P = (k, e) => { const o = (k * n + e) * 3; return [V[o], V[o + 1], V[o + 2]]; };
    for (let k = 0; k < K - 1; k++) for (let e = 0; e < n; e++) {
      const e1 = (e + 1) % n;
      const a = P(k, e), b = P(k, e1), c = P(k + 1, e1), d = P(k + 1, e);
      const f = (ms.SN && ms.SN.length > (k * n + e) * 3 + 2) ? [ms.SN[(k * n + e) * 3], ms.SN[(k * n + e) * 3 + 1], ms.SN[(k * n + e) * 3 + 2]] : [0, 0, 1];
      tb.tri(a, b, c, rgba, { n: f }); tb.tri(a, c, d, rgba, { n: f });
    }
    if (prof.pts.length === n) {
      const tris = triangulate2(prof.pts, []);
      const ax = ms.ax;
      for (const [k, s] of [[0, -1], [K - 1, 1]]) for (const t of tris) tb.tri(P(k, t[0]), P(k, t[1]), P(k, t[2]), rgba, { n: [ax[0] * s, ax[1] * s, ax[2] * s] });
    }
  }

  /** Extruded shells (VX plates: tiles + sides), lit, split by behavior for contours. */
  buildPlates(B, geo, vxo, add) {
    const alpha = vxo.dp && vxo.dp.fill === false ? 0.08 : 1 - vxo.shellTransp;
    const tbs = { shell: new TriBuilder(), other: new TriBuilder() };
    const rims = [];
    const loopPts = lp => { const o = []; for (let i = 0; i < lp.length / 3; i++) o.push([lp[i * 3], lp[i * 3 + 1], lp[i * 3 + 2]]); return o; };
    for (const p of geo.plates) {
      const tb = tbs[p.behavior === "shell" ? "shell" : "other"];
      const rgb = cssRGBA(p.col);
      const col = [rgb[0], rgb[1], rgb[2], 1];
      if (p.type === "tile") {
        for (const [loops, s] of [[p.top, 1], [p.bot, -1]]) {
          const ls = loops.map(loopPts);
          if (!ls.length) continue;
          fillPoly(tb, ls[0], ls.slice(1), col, { n: [p.n[0] * s, p.n[1] * s, p.n[2] * s] });
        }
      } else {
        const q = p.q, Q = [0, 1, 2, 3].map(i => [q[i * 3], q[i * 3 + 1], q[i * 3 + 2]]);
        tb.tri(Q[0], Q[1], Q[2], col, { n: p.n }); tb.tri(Q[0], Q[2], Q[3], col, { n: p.n });
        rims.push([Q[0], Q[1]], [Q[2], Q[3]]);
      }
    }
    B.plates = {};
    for (const [k, tb] of Object.entries(tbs)) {
      if (!tb.count) continue;
      const mat = makeMeshMat({ lit: true, transparent: alpha < 1, depthWrite: alpha >= 1, polyOffset: true });
      mat.uniforms.uAlpha.value = alpha;
      const mesh = new THREE.Mesh(tb.build(), mat);
      mesh.frustumCulled = false;
      B.plates[k] = add(mesh, alpha < 1 ? RO.fills : RO.solid);
    }
    if (rims.length) { B.plateEdges = linesFrom(rims, [8 / 255, 12 / 255, 18 / 255, 0.3], 0.75, { cap: 0, bias: 1e-5 }); add(B.plateEdges.mesh, RO.edge); }
  }

  /* ================================================================ colours (hover / highlight / colour-by) */
  syncColors(VX) {
    const v = this.v, B = this.base;
    const hov = v._hover ? v._hover.uid : null;
    const sig = [hov, v.highlight, v.memberColors, B];
    if (this._colorSig && sig.every((x, i) => x === this._colorSig[i])) return;
    this._colorSig = sig;
    const hl = v.highlight && v.highlight.uids, hlc = cssRGBA((v.highlight && v.highlight.color) || "#e0a020");
    const mc = v.memberColors;
    const segColor = VX && VX.segColor;
    const L = B.lines;
    if (L) {
      for (let k = 0; k < B.lineSegs.length; k++) {
        const seg = B.lineSegs[k];
        let c, a, w;
        if (hov != null && seg.uid === hov) { c = [1, 1, 1]; a = 1; w = 2.6; }
        else if (hl && hl.has(seg.uid)) { c = hlc; a = 1; w = 2.6; }
        else if (mc && mc[seg.uid]) { c = cssRGBA(mc[seg.uid]); a = -2; w = 2.3; }
        else { c = cssRGBA((segColor ? segColor(seg) : null) || GC[seg.kind] || GC.beam); a = -1; w = seg.kind === "column" ? 1.8 : 1.3; }
        const o = k * 4;
        L.C[o] = c[0]; L.C[o + 1] = c[1]; L.C[o + 2] = c[2]; L.C[o + 3] = a; L.W[k] = w;
      }
      L.touch("iCol", "iW");
    }
    const colorFor = mesh => {
      if (hov != null && hov === mesh.uid) return "#f4f7fb";
      if (hl && hl.has(mesh.uid)) return (v.highlight && v.highlight.color) || "#e0a020";
      if (mc && mc[mesh.uid]) return mc[mesh.uid];
      return mesh.col;
    };
    for (const pb of B.prisms || []) {
      pb.meshes.forEach((mesh, i) => { const c = cssRGBA(colorFor(mesh)); pb.C[i * 3] = c[0]; pb.C[i * 3 + 1] = c[1]; pb.C[i * 3 + 2] = c[2]; });
      pb.touchColors();
    }
    if (B.npMesh) {
      const col = B.npMesh.geometry.getAttribute("aCol");
      for (const np of B.npMeshes) {
        const c = cssRGBA(colorFor(np.mesh));
        for (let t = np.from * 3; t < np.to * 3; t++) { col.array[t * 4] = c[0]; col.array[t * 4 + 1] = c[1]; col.array[t * 4 + 2] = c[2]; col.array[t * 4 + 3] = 1; }
      }
      col.needsUpdate = true;
    }
  }

  /* ================================================================ results layers */
  disposeLayer(k) {
    const L = this.layers[k];
    if (!L) return;
    for (const o of L.objs || []) { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); this.root.remove(o); }
    delete this.layers[k];
  }
  setLayer(k, sig, build) {
    const L = this.layers[k];
    if (L && L.sig.length === sig.length && L.sig.every((x, i) => x === sig[i])) return L;
    this.disposeLayer(k);
    const nl = build() || { objs: [] };
    nl.sig = sig;
    for (const o of nl.objs) { o.frustumCulled = false; this.root.add(o); }
    this.layers[k] = nl;
    return nl;
  }
  showLayer(k, on) { const L = this.layers[k]; if (L) for (const o of L.objs) o.visible = !!on && !o.userData.off; }

  /** FE mesh lines (subtle) once analysed. */
  syncMeshLines(on) {
    const v = this.v, r = v.results;
    if (!(on && r && r.shell_quads && r.shell_quads.length && v._nodeXYZ)) { this.showLayer("mesh", false); return; }
    this.setLayer("mesh", [r, v._nodeXYZ], () => {
      const ed = [];
      for (const q of r.shell_quads) {
        const pts = q.nodes.map(t => v._nodeXYZ[t]);
        if (pts.some(p => !p)) continue;
        outline(ed, pts);
      }
      const fl = linesFrom(ed, cssRGBA(GC.meshLine), 0.8, { cap: 0, depthWrite: false });
      fl.mesh.renderOrder = RO.fillEdges;
      return { objs: [fl.mesh] };
    });
    this.showLayer("mesh", true);
  }

  contourState(sky) {
    const v = this.v;
    if (v.shellResExt && v.shellResExt.active()) return { ext: v.shellResExt };
    const c = v.contours, r = v.results;
    if (!c.on || !r || !r.shell_quads || !r.shell_quads.length) return null;
    const key = [r, c.comp, c.caseName];
    if (!this._cKey || key.some((x, i) => x !== this._cKey[i])) { this._cKey = key; this._cData = v._contourData(); }
    return this._cData;
  }

  /** Classic shell-force contours (flat per FE quad, colormap in the shader) or A3 cells. */
  syncContour(contour) {
    const v = this.v, r = v.results;
    if (!contour) { this.showLayer("contour", false); this.showLayer("a3", false); return; }
    if (contour.ext) {
      this.showLayer("contour", false);
      this.syncA3(contour.ext);
      return;
    }
    this.showLayer("a3", false);
    if (!v._nodeXYZ) { this.showLayer("contour", false); return; }
    const L = this.setLayer("contour", [r, v._nodeXYZ], () => {
      const tb = new TriBuilder(), ed = [], quadAt = [];
      r.shell_quads.forEach((q, i) => {
        const pts = q.nodes.map(t => v._nodeXYZ[t]);
        if (pts.some(p => !p)) return;
        const from = tb.count;
        for (let k = 1; k + 1 < pts.length; k++) tb.tri(pts[0], pts[k], pts[k + 1], [1, 1, 1, 0.92], { v: 0 });
        quadAt.push([i, from, tb.count]);
        outline(ed, pts);
      });
      if (!tb.count) return { objs: [] };
      const mat = makeMeshMat({ scalar: true, transparent: true, depthWrite: true });
      const mesh = new THREE.Mesh(tb.build(), mat);
      mesh.renderOrder = RO.contour;
      const seams = linesFrom(ed, cssRGBA("rgba(13, 17, 23, 0.45)"), 0.7, { cap: 0, bias: 1e-5 });
      seams.mesh.renderOrder = RO.contour + 0.5;
      return { objs: [mesh, seams.mesh], mesh, quadAt, vals: null };
    });
    if (!L.mesh) return;
    if (L.vals !== contour.vals) {   // switching component / case = one Float32Array upload
      const a = L.mesh.geometry.getAttribute("aVal");
      for (const [i, from, to] of L.quadAt) { const val = contour.vals[i] || 0; for (let t = from * 3; t < to * 3; t++) a.array[t] = val; }
      a.needsUpdate = true;
      L.vals = contour.vals;
    }
    L.mesh.material.uniforms.uVmax.value = contour.vmax;
    this.showLayer("contour", true);
  }

  /** A3 shell stress / force contours (js/shellres.js): its own cells and colours, captured in world space. */
  syncA3(ext) {
    const v = this.v;
    const items = [];
    const P = { near: -Infinity, toCam: p => [+p[0], +p[1], +p[2]], proj: c => ({ x: c, y: 0, z: c[2] }) };
    try { ext.items(items, P, v._nodeXYZ || {}, v.results); } catch (e) { this.showLayer("a3", false); return; }
    // cheap change signature: item count + sampled fill colours
    const polys = [], edges = [];
    let cur = [];
    const EDGE = "rgba(13, 17, 23, 0.45)";
    const fctx = { globalAlpha: 1, fillStyle: "", strokeStyle: "", lineWidth: 1,
      beginPath() { cur = []; }, moveTo(p) { cur.push(p); }, lineTo(p) { cur.push(p); }, closePath() {},
      fill() { polys.push([cur.slice(), this.fillStyle, this.globalAlpha]); },
      stroke() { if (this.strokeStyle === EDGE) edges.push(cur.slice()); } };
    const sample = [];
    const stride = Math.max(1, Math.floor(items.length / 16));
    for (let i = 0; i < items.length; i += stride) {
      const n0 = polys.length;
      try { items[i].draw(fctx); } catch (e) { /* skip */ }
      sample.push(polys.slice(n0).map(p => p[1]).join(","));
    }
    const sig = [v.results, items.length, sample.join("|")];
    polys.length = 0; edges.length = 0;
    this.setLayer("a3", sig, () => {
      for (const it of items) { try { it.draw(fctx); } catch (e) { /* skip */ } }
      const tb = new TriBuilder();
      for (const [pts, fill, ga] of polys) {
        if (pts.length < 3 || pts.some(p => !Array.isArray(p))) continue;
        const c = cssRGBA(fill);
        const rgba = [c[0], c[1], c[2], (c[3] == null ? 1 : c[3]) * ga];
        for (let k = 1; k + 1 < pts.length; k++) tb.tri(pts[0], pts[k], pts[k + 1], rgba);
      }
      const ed = [];
      for (const pts of edges) if (pts.every(p => Array.isArray(p))) outline(ed, pts);
      const objs = [];
      if (tb.count) { const m = new THREE.Mesh(tb.build(), makeMeshMat({ transparent: true })); m.renderOrder = RO.contour; objs.push(m); }
      if (ed.length) { const s = linesFrom(ed, cssRGBA(EDGE), 0.6, { cap: 0, bias: 1e-5 }); s.mesh.renderOrder = RO.contour + 0.5; objs.push(s.mesh); }
      return { objs };
    });
    this.showLayer("a3", true);
  }

  /** v0.25 cracked-quad tint (per shell_quads index). */
  syncTint(on) {
    const v = this.v, r = v.results;
    if (!(on && v.quadTint && r && r.shell_quads && r.shell_quads.length && v._nodeXYZ)) { this.showLayer("tint", false); return; }
    this.setLayer("tint", [v.quadTint, r, v._nodeXYZ], () => {
      const tb = new TriBuilder(), ed = [];
      r.shell_quads.forEach((q, i) => {
        const tint = v.quadTint[i] !== undefined ? v.quadTint[i] : v.quadTint[String(i)];
        if (!tint) return;
        const pts = q.nodes.map(t => v._nodeXYZ[t]);
        if (pts.some(p => !p)) return;
        const c = cssRGBA(typeof tint === "string" ? tint : "rgba(224, 120, 60, 0.45)");
        for (let k = 1; k + 1 < pts.length; k++) tb.tri(pts[0], pts[k], pts[k + 1], c);
        outline(ed, pts);
      });
      const objs = [];
      if (tb.count) { const m = new THREE.Mesh(tb.build(), makeMeshMat({ transparent: true, depthWrite: false, bias: 2e-5 })); m.renderOrder = RO.tint; objs.push(m); }
      if (ed.length) { const s = linesFrom(ed, cssRGBA("rgba(230, 140, 80, 0.9)"), 1, { cap: 0, bias: 2e-5 }); s.mesh.renderOrder = RO.tint; objs.push(s.mesh); }
      return { objs };
    });
    this.showLayer("tint", true);
  }

  /** dispMap + factor exactly as Viewer3D._renderOverlay. */
  deformState() {
    const v = this.v, r = v.results, ov = v.overlay;
    let dispMap = null, factor = 1, anim = false;
    const scaleOf = dm => { if (this._asMap !== dm) { this._asMap = dm; this._as = v.autoScale(dm); } return this._as; };
    if (ov.buckling && r.buckling) {
      const bc = r.buckling[ov.bucklingCase];
      dispMap = bc && bc.modes && bc.modes[String(ov.modeIndex + 1)];
      if (!dispMap) return null;
      factor = scaleOf(dispMap) * Math.sin(2 * Math.PI * ((performance.now() - v._animT0) / 1000) / 1.8); anim = true;
    } else if (ov.modal && r.modal && r.modal.shapes) {
      dispMap = r.modal.shapes[String(ov.modeIndex + 1)];
      if (!dispMap) return null;
      factor = scaleOf(dispMap) * Math.sin(2 * Math.PI * ((performance.now() - v._animT0) / 1000) / 1.8); anim = true;
    } else if (ov.deformed) {
      const cd = v._caseData();
      if (!cd) return null;
      dispMap = cd.node_disp;
      if (!dispMap) return null;
      factor = scaleOf(dispMap) * (ov.scaleMult || 1);
    }
    return dispMap ? { dispMap, factor, anim } : null;
  }

  syncDeformed(st, VX, vxo, sky) {
    const v = this.v;
    if (!st) { this.showLayer("def", false); return; }
    const extrude = !!(vxo && vxo.extrude && sky.vx && sky.vx.geo);
    const L = this.setLayer("def", [v.results, st.dispMap, extrude, this.base], () => this.buildDeformed(st.dispMap, extrude, sky));
    for (const u of L.uniforms || []) u.uDisp.value = st.factor;
    this.showLayer("def", true);
  }

  buildDeformed(dispMap, extrude, sky) {
    const v = this.v, r = v.results, nodes = r.nodes || {};
    const objs = [], uniforms = [];
    const Z3 = [0, 0, 0];
    const d3 = d => (d ? [+d[0] || 0, +d[1] || 0, +d[2] || 0] : Z3);
    // ---- shells (FE quads, else region corners matched by coordinate)
    const polys = [];
    if (r.shell_quads && r.shell_quads.length) {
      const kindOf = {};
      for (const sh of v.shellPolys || []) kindOf[sh.uid] = sh.kind;
      for (const q of r.shell_quads) {
        const pts = q.nodes.map(t => nodes[t]);
        if (pts.some(p => !p)) continue;
        polys.push({ pts, ds: q.nodes.map(t => d3(dispMap[t])), kind: kindOf[q.region] || "slab" });
      }
    } else if ((v.shellPolys || []).length && v._coordTag) {
      for (const sh of v.shellPolys) {
        const ds = sh.corners.map(p => { const t = v._coordTag.get(p.map(x => x.toFixed(4)).join(",")); return t ? d3(dispMap[t]) : Z3; });
        polys.push({ pts: sh.corners, ds, kind: sh.kind });
      }
    }
    if (polys.length) {
      const tb = new TriBuilder(), ed = [];
      for (const pq of polys) {
        const c = cssRGBA(pq.kind === "wall" ? GC.wallShellDef : GC.slabShellDef);
        for (let k = 1; k + 1 < pq.pts.length; k++) tb.tri(pq.pts[0], pq.pts[k], pq.pts[k + 1], c, { d: [pq.ds[0], pq.ds[k], pq.ds[k + 1]] });
        const n = pq.pts.length;
        for (let i = 0; i < n; i++) ed.push([pq.pts[i], pq.pts[(i + 1) % n], pq.ds[i], pq.ds[(i + 1) % n]]);
      }
      const mat = makeMeshMat({ transparent: true, depthWrite: false, disp: true });
      mat.depthTest = false;   // Classic: the deformed shape is painted over the ghosted model
      const m = new THREE.Mesh(tb.build(), mat); m.renderOrder = RO.fills;
      objs.push(m); uniforms.push(mat.uniforms);
      const fl = new FatLines(ed.length, { disp: true, cap: 0, depthWrite: false, depthTest: false });
      const ec = cssRGBA("rgba(53, 181, 229, 0.30)");
      ed.forEach(([a, b, da, db], i) => { fl.set(i, a, b, ec, 0.9); fl.DA.set(da, i * 3); fl.DB.set(db, i * 3); });
      fl.mesh.renderOrder = RO.fillEdges;
      objs.push(fl.mesh); uniforms.push(fl.uniforms);
    }
    // ---- members: Hermite curve (8 sub-segments) → per-vertex displacement at f = 1
    const list = [];
    for (const m of r.members || []) {
      const Pi = nodes[m.ni], Pj = nodes[m.nj], di = dispMap[m.ni], dj = dispMap[m.nj];
      if (!Pi || !Pj || !di || !dj) continue;
      if (v.vxMemberVisible && !v.vxMemberVisible(m)) continue;
      const pts = v._deformedPolyline(Pi, Pj, di, dj, 1);
      const N = pts.length - 1;
      const base = k => [Pi[0] + (Pj[0] - Pi[0]) * k / N, Pi[1] + (Pj[1] - Pi[1]) * k / N, Pi[2] + (Pj[2] - Pi[2]) * k / N];
      list.push({ m, Pi, Pj, N, base, D: pts.map((p, k) => sub3(p, base(k))) });
    }
    const geo = extrude ? sky.vx.geo() : null;
    if (geo) {
      // extruded deformed: unit prism with 5 stations; D at t = 0, ¼, ½, ¾, 1 (Hermite samples 0, 2, 4, 6, 8)
      const byKey = new Map();
      for (const it of list) {
        const mm = geo.byUid.get(it.m.uid);
        const secName = (mm && mm.section) || it.m.section;
        const pr = geo.prof(secName, "full");
        const pseudo = { ...(mm || {}), pi: it.Pi, pj: it.Pj, angle: mm ? mm.angle : 0, section: secName };
        const axes = gMemberAxes(pseudo, v.model);
        const cp = mm && mm.cardinal_point != null ? +mm.cardinal_point : 10;
        const gg = G_CP_GRID[cp] || [0, 0];
        const off = [0, 1, 2].map(k => -0.5 * pr.h * gg[0] * axes.y[k] - 0.5 * pr.b * gg[1] * axes.z[k]);
        const css = geo.colorOf(it.m.kind || (mm && mm.kind) || "beam", secName, ((v.model.sections || {})[secName] || {}).material, it.m.uid, mm && mm.story);
        const key = secName + "\u0001" + pr.pts.length;
        let e = byKey.get(key);
        if (!e) { e = { pr, list: [] }; byKey.set(key, e); }
        const idx = it.N === 8 ? [0, 2, 4, 6, 8] : [0, 1, 2, 3, 4].map(k => Math.round(k * it.N / 4));
        e.list.push({ X: sub3(it.Pj, it.Pi), Y: axes.y, Z: axes.z, T: [it.Pi[0] + off[0], it.Pi[1] + off[1], it.Pi[2] + off[2]], c: cssRGBA(css), D: idx.map(k => it.D[k]) });
      }
      for (const e of byKey.values()) {
        const pb = new PrismBatch(unitPrism(e.pr, [0, 0.25, 0.5, 0.75, 1]), e.list.length, { def: true });
        pb.mat.transparent = true;   // drawn after the deformed shell fills, over them (Classic: members on top)
        e.list.forEach((x, i) => { pb.set(i, x.X, x.Y, x.Z, x.T, x.c); for (let k = 0; k < 5; k++) pb.D[k].set(x.D[k], i * 3); });
        pb.mesh.renderOrder = RO.deformed; pb.edges.renderOrder = RO.deformed + 0.5;
        pb.edges.userData.off = !(sky.vx.opts().edges) || !!(v.overlay.modal || v.overlay.buckling);   // Classic: no edges while animating
        objs.push(pb.mesh, pb.edges); uniforms.push(pb.mat.uniforms, pb.edgeMat.uniforms);
        pb.mat.uniforms.uLight.value.copy(this.light());
      }
    } else if (list.length) {
      const n = list.reduce((a, it) => a + it.N, 0);
      const fl = new FatLines(n, { disp: true, depthTest: false, depthWrite: false });
      const c = cssRGBA(GC.deformed);
      const rgba = [c[0], c[1], c[2], 0.95];
      let i = 0;
      for (const it of list) for (let k = 0; k < it.N; k++, i++) {
        fl.set(i, it.base(k), it.base(k + 1), rgba, 1.6);
        fl.DA.set(it.D[k], i * 3); fl.DB.set(it.D[k + 1], i * 3);
      }
      fl.mesh.renderOrder = RO.deformed;
      objs.push(fl.mesh); uniforms.push(fl.uniforms);
    }
    return { objs, uniforms };
  }

  /* ================================================================ picking */
  /** Visible member centerlines in screen space (Classic _segsScreen, computed lazily). */
  segsScreen() {
    if (this._ssFrame === this._frame && this._ss) return this._ss;
    const v = this.v, B = this.base;
    const out = [];
    if (B && B.lines) {
      const P = v._makeProjector(), L = B.lines;
      for (let k = 0; k < B.lineSegs.length; k++) {
        const o = k * 3;
        const s = v._projSeg(P, [L.A[o], L.A[o + 1], L.A[o + 2]], [L.B[o], L.B[o + 1], L.B[o + 2]]);
        if (s) out.push({ x1: s.a.x, y1: s.a.y, x2: s.b.x, y2: s.b.y, seg: B.lineSegs[k], k });
      }
    }
    this._ss = out; this._ssFrame = this._frame;
    return out;
  }

  /** Screen segments of only the members that carry a badge / glyph in the overlay
      pass (rigid zones, ΔT, T/C-only, end releases): cost ∝ that subset, not the model. */
  segsScreenSubset() {
    const v = this.v, B = this.base;
    if (!B || !B.lines) return [];
    if (this._subFrame === this._frame && this._sub) return this._sub;
    if (!B.badgeIdx) {
      const uids = new Set([...(v.rigidUids || []), ...(v.thermalUids || []), ...((v.axialLimitUids && v.axialLimitUids.keys()) || [])]);
      for (const mm of (v.model && v.model.members) || []) if (mm.end_releases || (mm.releases && String(mm.releases).trim())) uids.add(mm.uid);
      B.badgeIdx = [];
      if (uids.size) B.lineSegs.forEach((sg, k) => { if (uids.has(sg.uid)) B.badgeIdx.push(k); });
    }
    const out = [];
    if (B.badgeIdx.length) {
      const P = v._makeProjector(), L = B.lines;
      for (const k of B.badgeIdx) {
        const o = k * 3;
        const s = v._projSeg(P, [L.A[o], L.A[o + 1], L.A[o + 2]], [L.B[o], L.B[o + 1], L.B[o + 2]]);
        if (s) out.push({ x1: s.a.x, y1: s.a.y, x2: s.b.x, y2: s.b.y, seg: B.lineSegs[k], k });
      }
    }
    this._sub = out; this._subFrame = this._frame;
    return out;
  }

  /** Render the ID buffer (frame centerlines) at CSS resolution, once per frame. */
  ensureIdBuffer() {
    const w = this.w, h = this.h;
    if (!this._rt || this._rt.width !== w || this._rt.height !== h) {
      if (this._rt) this._rt.dispose();
      this._rt = new THREE.WebGLRenderTarget(w, h, { depthBuffer: true, samples: 0 });
      this._idFrame = -1;
    }
    if (this._idFrame === this._frame) return;
    if (!this.base || !this.base.lines) return;
    this.setCamera();
    const u = this.base.lines.pickUniforms;
    u.uRes.value.set(w, h); u.uPx.value = 1; u.uWScale.value = 2.2; u.uNear.value = 0.05;
    const r = this.renderer;
    const prevRT = r.getRenderTarget();
    r.setRenderTarget(this._rt);
    r.setClearColor(0x000000, 0);
    r.clear(true, true, false);
    r.render(this.pickScene, this.camera);
    r.setRenderTarget(prevRT);
    this._idFrame = this._frame;
  }

  pickAt(mx, my, thr) {
    const v = this.v, B = this.base;
    if (!B || !B.lines || !B.lines.n || !this.w) return null;
    if (!this._ssCurrent()) this.render();
    this.ensureIdBuffer();
    const r = Math.ceil(thr);
    const x0 = Math.max(0, Math.floor(mx) - r), x1 = Math.min(this.w - 1, Math.floor(mx) + r);
    const y0 = Math.max(0, Math.floor(my) - r), y1 = Math.min(this.h - 1, Math.floor(my) + r);
    if (x1 < x0 || y1 < y0) return null;
    const ww = x1 - x0 + 1, hh = y1 - y0 + 1;
    const buf = new Uint8Array(ww * hh * 4);
    this.renderer.readRenderTargetPixels(this._rt, x0, this.h - 1 - y1, ww, hh, buf);
    const ids = new Set();
    for (let i = 0; i < ww * hh; i++) {
      const id = buf[i * 4] + buf[i * 4 + 1] * 256 + buf[i * 4 + 2] * 65536;
      if (id) ids.add(id - 1);
    }
    this.lastPickCandidates = ids.size;
    if (!ids.size) return null;
    const P = v._makeProjector(), L = B.lines;
    // Classic rule: the first strictly-nearest centerline in PAINTER order (far → near,
    // stable on model order) — ties (two members sharing the nearest joint) resolve alike
    const cands = [];
    for (const k of ids) {
      if (k < 0 || k >= B.lineSegs.length) continue;
      const o = k * 3;
      const s = v._projSeg(P, [L.A[o], L.A[o + 1], L.A[o + 2]], [L.B[o], L.B[o + 1], L.B[o + 2]]);
      if (s) cands.push({ k, s, z: (s.a.z + s.b.z) / 2 });
    }
    cands.sort((a, b) => (b.z - a.z) || (a.k - b.k));
    let best = null, bestD = thr;
    for (const c of cands) {
      const d = distToSeg2(mx, my, c.s.a.x, c.s.a.y, c.s.b.x, c.s.b.y);
      if (d < bestD) { bestD = d; best = B.lineSegs[c.k]; }
    }
    return best;
  }
  _ssCurrent() { return !!this.base; }

  /** World-space uniform grid over the visible centerlines (built lazily per base scene). */
  index() {
    const B = this.base;
    if (this._index && this._index.B === B) return this._index;
    const L = B.lines, n = B.lineSegs.length;
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (let k = 0; k < n; k++) for (const A of [L.A, L.B]) for (let i = 0; i < 3; i++) { const x = A[k * 3 + i]; if (x < lo[i]) lo[i] = x; if (x > hi[i]) hi[i] = x; }
    const span = [0, 1, 2].map(i => Math.max(hi[i] - lo[i], 1e-6));
    const target = Math.max(1, n / 8);   // ~8 segments per cell
    const vol = span[0] * span[1] * span[2];
    let cs = Math.cbrt(vol / target);
    if (!isFinite(cs) || cs <= 0) cs = Math.max(...span) / 8;
    const dims = span.map(s => Math.max(1, Math.min(256, Math.ceil(s / cs))));
    const cells = new Map();
    const ci = (x, i) => Math.min(dims[i] - 1, Math.max(0, Math.floor((x - lo[i]) / span[i] * dims[i])));
    for (let k = 0; k < n; k++) {
      const a = [L.A[k * 3], L.A[k * 3 + 1], L.A[k * 3 + 2]], b = [L.B[k * 3], L.B[k * 3 + 1], L.B[k * 3 + 2]];
      const c0 = [0, 1, 2].map(i => ci(Math.min(a[i], b[i]), i)), c1 = [0, 1, 2].map(i => ci(Math.max(a[i], b[i]), i));
      for (let x = c0[0]; x <= c1[0]; x++) for (let y = c0[1]; y <= c1[1]; y++) for (let z = c0[2]; z <= c1[2]; z++) {
        const key = (x * dims[1] + y) * dims[2] + z;
        let cell = cells.get(key);
        if (!cell) { cell = { items: [], lo: [lo[0] + x * span[0] / dims[0], lo[1] + y * span[1] / dims[1], lo[2] + z * span[2] / dims[2]] }; cell.hi = [cell.lo[0] + span[0] / dims[0], cell.lo[1] + span[1] / dims[1], cell.lo[2] + span[2] / dims[2]]; cells.set(key, cell); }
        cell.items.push(k);
      }
    }
    this._index = { B, cells, dims };
    return this._index;
  }

  pickBox(x0, y0, x1, y1, crossing) {
    const v = this.v, B = this.base;
    if (!B || !B.lines || !B.lines.n) return [];
    const rect = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
    const P = v._makeProjector(), L = B.lines;
    const idx = this.index();
    const cand = new Set();
    let cellsTested = 0;
    for (const cell of idx.cells.values()) {
      cellsTested++;
      let behind = false, sx0 = Infinity, sy0 = Infinity, sx1 = -Infinity, sy1 = -Infinity;
      for (let c = 0; c < 8 && !behind; c++) {
        const p = [c & 1 ? cell.hi[0] : cell.lo[0], c & 2 ? cell.hi[1] : cell.lo[1], c & 4 ? cell.hi[2] : cell.lo[2]];
        const pc = P.toCam(p);
        if (pc[2] < P.near) { behind = true; break; }
        const s = P.proj(pc);
        sx0 = Math.min(sx0, s.x); sy0 = Math.min(sy0, s.y); sx1 = Math.max(sx1, s.x); sy1 = Math.max(sy1, s.y);
      }
      if (!behind && (sx1 < rect[0] || sx0 > rect[2] || sy1 < rect[1] || sy0 > rect[3])) continue;
      for (const k of cell.items) cand.add(k);
    }
    this.lastBoxStats = { cells: cellsTested, candidates: cand.size, total: B.lineSegs.length };
    const out = [];
    for (const k of [...cand].sort((a, b) => a - b)) {
      const o = k * 3;
      const s = v._projSeg(P, [L.A[o], L.A[o + 1], L.A[o + 2]], [L.B[o], L.B[o + 1], L.B[o + 2]]);
      if (s && gBoxHitSeg(rect, s.a.x, s.a.y, s.b.x, s.b.y, crossing)) out.push(B.lineSegs[k]);
    }
    const seen = new Set();
    return out.filter(sg => (seen.has(sg) ? false : (seen.add(sg), true)));
  }

  /* ================================================================ labels */
  /** Run fn with ctx.fillText decluttered: off-screen labels are skipped and a
      label overlapping an already placed one is dropped (6 px occupancy grid). */
  withLabels(ctx, fn) {
    const W = this.w || 0, H = this.h || 0, CELL = 6;
    const occ = new Set();
    const orig = ctx.fillText;
    let drawn = 0, culled = 0;
    ctx.fillText = function (t, x, y, mw) {
      const s = String(t);
      const wpx = Math.min(s.length * 6, 240), hpx = 10;
      const al = this.textAlign;
      const xl = al === "left" || al === "start" ? x : al === "right" || al === "end" ? x - wpx : x - wpx / 2;
      if (xl > W || xl + wpx < 0 || y - hpx > H || y + hpx < 0) { culled++; return; }
      const cx0 = Math.floor(xl / CELL), cx1 = Math.floor((xl + wpx) / CELL);
      const cy0 = Math.floor((y - hpx / 2) / CELL), cy1 = Math.floor((y + hpx / 2) / CELL);
      for (let a = cx0; a <= cx1; a++) for (let b = cy0; b <= cy1; b++) if (occ.has(a * 65536 + b)) { culled++; return; }
      for (let a = cx0; a <= cx1; a++) for (let b = cy0; b <= cy1; b++) occ.add(a * 65536 + b);
      drawn++;
      return orig.call(this, t, x, y, mw);
    };
    try { fn(); } finally { delete ctx.fillText; if (ctx.fillText !== orig) ctx.fillText = orig; }
    this.lastStats.labels = { drawn, culled };
  }

  /* ================================================================ stats / benchmark */
  stats() {
    const p = this.gl && this.gl.getExtension("WEBGL_debug_renderer_info");
    return { ...this.lastStats, renderer: this.gl ? String(this.gl.getParameter(p ? p.UNMASKED_RENDERER_WEBGL : this.gl.RENDERER)) : "",
      dpr: this.dpr, size: [this.w, this.h], members: (this.v.segs || []).length, shells: (this.v.shellPolys || []).length,
      prism_batches: (this.base && this.base.prisms || []).length, lastError: this.lastError || null };
  }

  /** Frame time while orbiting: render + a 1-pixel GPU read-back (forces the GPU to finish = honest time). */
  benchmark(frames = 60, dyaw = 0.02) {
    const v = this.v, t = [];
    const px = new Uint8Array(4);
    const gl = this.gl;
    for (let i = 0; i < frames; i++) {
      v.yaw += dyaw;
      const t0 = performance.now();
      this.render();
      gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
      t.push(performance.now() - t0);
    }
    t.sort((a, b) => a - b);
    const mean = t.reduce((a, b) => a + b, 0) / t.length;
    return { frames, mean_ms: +mean.toFixed(2), median_ms: +t[t.length >> 1].toFixed(2), p95_ms: +t[Math.floor(t.length * 0.95)].toFixed(2),
      fps_est: +(1000 / Math.max(mean, 1000 / 240)).toFixed(1), ...this.stats() };
  }
}

function distToSeg2(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const l2 = dx * dx + dy * dy;
  let t = l2 ? ((px - x1) * dx + (py - y1) * dy) / l2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}
