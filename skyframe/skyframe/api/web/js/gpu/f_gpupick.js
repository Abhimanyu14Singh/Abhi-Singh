/* SkyFrame Wave F — GPU ID buffer for EVERY object type (extends the E3
   frame-only ID buffer of js/gpu/renderer.js without changing it).

   Lazily attached to a GpuBackend (be.fPick) by js/f_select3d.js the first
   time a GPU viewer is picked. One off-screen RGBA8 render target holds, depth
   tested in one pass:
     ids 1 … N            frame centerlines (the E3 pick mesh, unchanged)
     (1 << 20) + i + 1    joints   — screen-space squares (2·6 px), as points
     (2 << 20) + i + 1    shells   — triangulated polygons with openings, in
                                     their OWN target (depth-tested among shells:
                                     the nearest shell per pixel), so the
                                     translucent fills never hide a frame behind
                                     them — the Classic rule
     (3 << 20) + i + 1    links    (4 << 20) point springs
     (5 << 20) line springs        (6 << 20) tendons (8 sub-segments each)
   where i is the item index in the pick-service scene (js/f_pick.js). A
   (2·thr+1)² pixel window is read back from both; the decoded ids become the candidate
   set and js/f_pick.js applies the exact screen-space rule (same winner as
   the Classic CPU index). */

import { THREE, FatLines as fgFatLines, makeMeshMat as fgMeshMat, TriBuilder as fgTriBuilder, triangulate2 as fgTriangulate } from "./glprims.js";
import { regionFrame as fgRegionFrame } from "../polygeom.js";

const RANGE = 1 << 20;
export const GPU_TYPE_CODE = { member: 0, joint: 1, shell: 2, link: 3, spring: 4, linespring: 5, tendon: 6 };

const idRGB = id => [(id & 255) / 255, ((id >> 8) & 255) / 255, ((id >> 16) & 255) / 255, 1];
export const decodeId = rgb => rgb[0] + rgb[1] * 256 + rgb[2] * 65536;
export const encodeId = (type, i) => GPU_TYPE_CODE[type] * RANGE + i + 1;
export const splitId = id => ({ code: Math.floor(id / RANGE), index: (id % RANGE) - 1 });

function openLoop(pts) {
  if (pts.length > 2) {
    const a = pts[0], b = pts[pts.length - 1];
    if (Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2]) < 1e-12) return pts.slice(0, -1);
  }
  return pts;
}

export function createGpuPick(be) {
  const st = { scene: null, sceneRef: null, rt: null, frame: -1, objs: [] };

  function dispose() {
    for (const o of st.objs) { try { o.dispose(); } catch { /* gone */ } }
    st.objs = [];
    if (st.scene) st.scene.clear();
    if (st.shellScene) st.shellScene.clear();
  }

  /** (re)build the non-frame pick scene from the pick-service scene items. */
  function build(items) {
    dispose();
    const scene = new THREE.Scene(), shellScene = new THREE.Scene();
    const tb = new fgTriBuilder();
    const pts = [], segs = [];
    items.forEach((it, i) => {
      const t = it.ref.type;
      if (t === "member") return;                 // E3 pick mesh
      const id = encodeId(t, i);
      if (it.g === "pt") pts.push([it.pts[0], id, 2 * (it.r || 6)]);
      else if (it.g === "seg") segs.push([it.pts[0], it.pts[1], id, t === "tendon" ? 4 : 5]);
      else {
        const outer = openLoop(it.pts);
        if (outer.length < 3) return;
        const holes = (it.holes || []).map(openLoop).filter(h => h.length >= 3);
        let fr;
        try { fr = fgRegionFrame(outer); } catch { return; }
        const all = [...outer, ...holes.flat()];
        let tris;
        try { tris = fgTriangulate(outer.map(fr.to2), holes.map(h => h.map(fr.to2))); } catch { return; }
        const c = idRGB(id);
        for (const tr of tris) tb.tri(all[tr[0]], all[tr[1]], all[tr[2]], c);
      }
    });
    if (tb.count) {
      const mat = fgMeshMat({ transparent: false });
      const mesh = new THREE.Mesh(tb.build(), mat);
      mesh.frustumCulled = false;
      shellScene.add(mesh);
      st.objs.push({ dispose: () => { mesh.geometry.dispose(); mat.dispose(); } });
    }
    const lines = n => { const fl = new fgFatLines(n, { pick: true, cap: 1 }); st.objs.push(fl); return fl; };
    if (pts.length) {
      const fl = lines(pts.length);
      pts.forEach(([p, id, w], k) => { fl.set(k, p, p, [0, 0, 0, 1], w); fl.ID[k] = id; });
      fl.touch(); fl.pickUniforms.uBias.value = 0.002;   // points win over coincident lines / shells
      scene.add(fl.pickMesh);
      st.pts = fl;
    } else st.pts = null;
    if (segs.length) {
      const fl = lines(segs.length);
      segs.forEach(([a, b, id, w], k) => { fl.set(k, a, b, [0, 0, 0, 1], w); fl.ID[k] = id; });
      fl.touch(); fl.pickUniforms.uBias.value = 0.001;
      scene.add(fl.pickMesh);
      st.segs = fl;
    } else st.segs = null;
    st.scene = scene; st.shellScene = shellScene;
  }

  function render(c) {
    const w = be.w, h = be.h;
    if (!w || !h) return false;
    if (!st.rt || st.rt.width !== w || st.rt.height !== h) {
      if (st.rt) { st.rt.dispose(); st.rtS.dispose(); }
      st.rt = new THREE.WebGLRenderTarget(w, h, { depthBuffer: true, samples: 0 });
      st.rtS = new THREE.WebGLRenderTarget(w, h, { depthBuffer: true, samples: 0 });
      st.frame = -1;
    }
    if (st.sceneRef !== c.scene) { build(c.scene.items); st.sceneRef = c.scene; st.frame = -1; }
    if (st.frame === be._frame && st.cam === c.camSig) return true;
    if (!be.base) be.render();
    be.setCamera();
    const r = be.renderer;
    const prev = r.getRenderTarget();
    const ac = r.autoClear;
    r.setRenderTarget(st.rt);
    r.setClearColor(0x000000, 0);
    r.clear(true, true, false);
    r.autoClear = false;
    try {
      const L = be.base && be.base.lines;
      if (L && L.n && L.pickUniforms) {
        const u = L.pickUniforms;
        u.uRes.value.set(w, h); u.uPx.value = 1; u.uWScale.value = 2.2; u.uNear.value = 0.05;
        r.render(be.pickScene, be.camera);
      }
      for (const fl of [st.pts, st.segs]) if (fl) { const u = fl.pickUniforms; u.uRes.value.set(w, h); u.uPx.value = 1; u.uWScale.value = 1; u.uNear.value = 0.05; }
      if (st.scene) r.render(st.scene, be.camera);
      r.setRenderTarget(st.rtS);
      r.clear(true, true, false);
      if (st.shellScene) r.render(st.shellScene, be.camera);
    } finally {
      r.autoClear = ac;
      r.setRenderTarget(prev);
    }
    st.frame = be._frame; st.cam = c.camSig;
    return true;
  }

  /** Candidate projected-item indices (into c.proj) in the (2·thr+1)² window. */
  function candidates(c, mx, my, thr) {
    if (!render(c)) return null;
    const rr = Math.ceil(thr);
    const x0 = Math.max(0, Math.floor(mx) - rr), x1 = Math.min(be.w - 1, Math.floor(mx) + rr);
    const y0 = Math.max(0, Math.floor(my) - rr), y1 = Math.min(be.h - 1, Math.floor(my) + rr);
    if (x1 < x0 || y1 < y0) return new Set();
    const ww = x1 - x0 + 1, hh = y1 - y0 + 1;
    const buf = new Uint8Array(ww * hh * 4);
    const ids = new Set();
    for (const rt of [st.rt, st.rtS]) {
      be.renderer.readRenderTargetPixels(rt, x0, be.h - 1 - y1, ww, hh, buf);
      for (let i = 0; i < ww * hh; i++) { const id = decodeId([buf[i * 4], buf[i * 4 + 1], buf[i * 4 + 2]]); if (id) ids.add(id); }
    }
    st.lastIds = ids.size;
    // id → scene item(s) → projected index
    const itemIdx = c.itemProj || (c.itemProj = new Map(c.proj.map((pr, k) => [pr.it, k])));
    const items = c.scene.items;
    const memberUids = new Set();
    const out = new Set();
    for (const id of ids) {
      const { code, index } = splitId(id);
      if (code === 0) {
        const sg = be.base && be.base.lineSegs && be.base.lineSegs[id - 1];
        if (sg) memberUids.add(sg.uid);
        continue;
      }
      const it = items[index];
      if (!it) continue;
      // multi-piece objects (tendons): every piece of the same object is a candidate
      if (it.ref.type === "tendon") { items.forEach(o => { if (o.ref.type === "tendon" && o.ref.uid === it.ref.uid && itemIdx.has(o)) out.add(itemIdx.get(o)); }); continue; }
      if (itemIdx.has(it)) out.add(itemIdx.get(it));
    }
    if (memberUids.size) c.proj.forEach((pr, k) => { if (pr.it.ref.type === "member" && memberUids.has(pr.it.ref.uid)) out.add(k); });
    return out;
  }

  return { candidates, stats: () => ({ lastIds: st.lastIds || 0, objects: st.sceneRef ? st.sceneRef.items.length : 0 }), dispose: () => { dispose(); if (st.rt) { st.rt.dispose(); st.rtS.dispose(); } } };
}
