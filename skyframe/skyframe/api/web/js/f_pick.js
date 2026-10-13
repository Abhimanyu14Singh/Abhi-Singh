/* SkyFrame Wave F — ONE pick service for every 3D view (main 3D view and
   every View › Windows 3D pane, Classic and GPU renderers).

   Object types: joints (screen-space dots), frames, shells (slabs / walls /
   ramps, rectangular or polygon, with openings), links, point springs, line
   springs and tendons. A per-browser SELECTION FILTER (object-type mask)
   and the view's visibility (Set Display Options object toggles, story
   range, groups, cut plane, Show Selection Only) decide what can be picked:
   hidden objects are never selected (ETABS rule). A joint is pickable when
   it belongs to a visible object.

   Classic renderer: the scene is projected once per camera / model / display
   state and bucketed in a 48-px screen grid (CPU spatial index); a click or
   hover only tests the objects in the buckets around the cursor.
   GPU renderer: the ID buffer (js/gpu/f_gpupick.js) gives the candidate ids
   under the cursor — frames (ids 1…N, the E3 buffer) plus joints / shells /
   links / springs / line springs / tendons in their own id ranges, depth
   tested — and the SAME exact screen-space rule picks the winner.

   Priority (f_pickgeom.resolvePick): joint within 6 px → nearest line object
   within the threshold → nearest shell under the cursor (ray ∩ polygon). */

import * as PG from "./f_pickgeom.js";
import { canonical as fpTgCanonical, samplePolyline as fpTgSample } from "./tendon_geom.js";
import { springKey as fpSpringKey, lineSpringKey as fpLineSpringKey } from "./modeledit.js";

const CELL = 48;
const JOINT_PX = 6;
export const FILTER_KEY = "sky.f.selfilter";
export const FILTER_TYPES = [["joint", "Joints"], ["column", "Columns"], ["beam", "Beams"], ["brace", "Braces"], ["wall", "Walls"],
  ["slab", "Slabs / ramps"], ["link", "Links"], ["spring", "Point springs"], ["linespring", "Line springs"], ["tendon", "Tendons"]];

export function defaultFilter() { return Object.fromEntries(FILTER_TYPES.map(([k]) => [k, true])); }
export function readFilter() {
  const d = defaultFilter();
  try { const o = JSON.parse(localStorage.getItem(FILTER_KEY) || "null"); if (o && typeof o === "object") for (const k of Object.keys(d)) if (typeof o[k] === "boolean") d[k] = o[k]; } catch { /* private mode */ }
  return d;
}
export function writeFilter(f) { try { localStorage.setItem(FILTER_KEY, JSON.stringify(f)); } catch { /* private mode */ } }

/** filter key of an object ("column" / "slab" / "joint" …). */
export function filterKind(m, ref) {
  if (ref.type === "member") { const x = (m.members || []).find(o => o.uid === ref.uid); return x ? x.kind : "beam"; }
  if (ref.type === "shell") { const x = (m.shells || []).find(o => o.uid === ref.uid); return x && x.kind === "wall" ? "wall" : "slab"; }
  return ref.type;
}

export function createPickService(sky) {
  let filter = readFilter();
  const S = sky.store;
  const passes = (kind) => filter[kind] !== false;

  /* ---------------------------------------------------------------- scene (world) */
  function visibility(v) {
    let VX = null;
    if (v.vxFrame) { try { VX = v.vxFrame(v._makeProjector(), v); } catch { VX = null; } }
    let geo = null;
    if (VX && sky.vx && v === sky.viewer && sky.vx.geo) { try { geo = sky.vx.geo(); } catch { geo = null; } }
    const vxo = VX && sky.vx && v === sky.viewer ? sky.vx.opts() : null;
    return { VX, geo, vxo };
  }

  function buildScene(v) {
    const m = v.model;
    const items = [];
    if (!m) return { items, sig: "" };
    const { VX, geo, vxo } = visibility(v);
    const overlay = !!(v.overlay && (v.overlay.deformed || v.overlay.modal || v.overlay.buckling));
    const joints = new Map();
    const addJ = p => { if (p) { const k = PG.jointKey(p); if (!joints.has(k)) joints.set(k, p); } };
    const kindOf = new Map((m.members || []).map(x => [x.uid, x.kind]));
    // frames
    for (const seg of v.segs || []) {
      const vs = VX ? VX.seg(seg) : seg;
      if (!vs) continue;
      const kind = kindOf.get(seg.uid) || seg.kind || "beam";
      // joints are taken from the real member ends (not the shrunk / clipped drawing)
      const mm = (m.members || []).find(x => x.uid === seg.uid);
      if (mm) { addJ(mm.pi); addJ(mm.pj); }
      if (!passes(kind)) continue;
      items.push({ ref: { type: "member", uid: seg.uid }, g: "seg", pts: [vs.p1, vs.p2], fk: kind });
    }
    // shells
    for (const sh0 of v.shellPolys || []) {
      let sh = VX ? VX.shell(sh0) : sh0;
      if (!sh && geo && geo.shellVis) { const g = geo.shellVis.has(sh0) ? geo.shellVis.get(sh0) : sh0; sh = g || null; }
      if (!sh && VX && VX.extrudeFrames && vxo && vxo.show && vxo.show[sh0.kind === "wall" ? "wall" : "slab"] !== false && !(geo && geo.shellVis && geo.shellVis.get(sh0) === null)) sh = sh0;
      if (!sh) continue;
      for (const c of sh0.corners) addJ(c);
      const fk = sh0.kind === "wall" ? "wall" : "slab";
      if (!passes(fk)) continue;
      items.push({ ref: { type: "shell", uid: sh0.uid }, g: "poly", pts: sh.corners, holes: sh.openings || [], fk });
    }
    // links
    for (const lk of v.linkSegs || []) {
      if (VX && !VX.link(lk)) continue;
      addJ(lk.p1); addJ(lk.p2);
      if (!passes("link")) continue;
      items.push({ ref: { type: "link", uid: lk.uid }, g: "seg", pts: [lk.p1, lk.p2], fk: "link" });
    }
    // point springs
    for (const sp of m.spring_supports || []) {
      if (VX && !VX.springPt(sp.point)) continue;
      addJ(sp.point);
      if (!passes("spring")) continue;
      items.push({ ref: { type: "spring", uid: fpSpringKey(sp.point) }, g: "pt", pts: [sp.point], fk: "spring", r: 9 });
    }
    // line springs (drawn by the Wave F overlay)
    const showSpr = !vxo || !vxo.show || vxo.show.spring !== false;
    for (const ls of m.line_springs || []) {
      if (!showSpr) continue;
      if (!passes("linespring")) continue;
      items.push({ ref: { type: "linespring", uid: fpLineSpringKey(ls) }, g: "seg", pts: [ls.p1, ls.p2], fk: "linespring" });
    }
    // supports: their joints are pickable while supports are shown
    for (const p of v.supportPts || []) if (!VX || VX.supportPt(p)) addJ(p);
    for (const s of m.supports || []) if (!VX || VX.supportPt(s.point)) addJ(s.point);
    // tendons (dashed overlay; hidden under result overlays like the drawing)
    if (!overlay && passes("tendon")) {
      for (const t of m.tendons || []) {
        try {
          const c = fpTgCanonical(t);
          if (!c || c.points.length < 2) continue;
          const pts = fpTgSample(c, 8);
          for (let i = 1; i < pts.length; i++) items.push({ ref: { type: "tendon", uid: c.uid }, g: "seg", pts: [pts[i - 1], pts[i]], fk: "tendon" });
        } catch { /* skip a malformed tendon */ }
      }
    }
    if (passes("joint")) for (const [k, p] of joints) items.push({ ref: { type: "joint", uid: k }, g: "pt", pts: [p], fk: "joint", r: JOINT_PX });
    return { items, joints };
  }

  /* ---------------------------------------------------------------- screen cache + index */
  const cache = new WeakMap();
  const sceneSig = v => {
    const vx = v === sky.viewer && sky.vx ? JSON.stringify(sky.vx.opts()) : "";
    return [v.segs && v.segs.length, v.shellPolys && v.shellPolys.length, v._fModelVer || 0, vx, JSON.stringify(filter),
      !!(v.overlay && (v.overlay.deformed || v.overlay.modal || v.overlay.buckling)), (S.selection || []).length && sky.vx && sky.vx.isSelOnly && sky.vx.isSelOnly() ? JSON.stringify(S.selection) : ""].join("|");
  };
  const camSig = v => [v.yaw, v.pitch, v.dist, v.target[0], v.target[1], v.target[2], v.canvas.clientWidth, v.canvas.clientHeight].join(",");

  function screen(v) {
    let c = cache.get(v);
    const ss = sceneSig(v);
    if (!c || c.sceneSig !== ss || c.model !== v.model || c.segsRef !== v.segs) {
      c = { sceneSig: ss, model: v.model, segsRef: v.segs, scene: buildScene(v), camSig: null };
      cache.set(v, c);
    }
    const cs = camSig(v);
    if (c.camSig !== cs) {
      const P = v._makeProjector();
      const proj = [];
      const grid = new Map();
      const put = (k, b) => {
        const x0 = Math.floor(b[0] / CELL), y0 = Math.floor(b[1] / CELL), x1 = Math.floor(b[2] / CELL), y1 = Math.floor(b[3] / CELL);
        if ((x1 - x0 + 1) * (y1 - y0 + 1) > 4096) { (grid.get("big") || grid.set("big", []).get("big")).push(k); return; }
        for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) { const key = x * 100003 + y; (grid.get(key) || grid.set(key, []).get(key)).push(k); }
      };
      for (const it of c.scene.items) {
        let sp = null;
        if (it.g === "pt") {
          const pc = P.toCam(it.pts[0]);
          if (pc[2] < P.near) continue;
          const q = P.proj(pc);
          sp = { pts: [[q.x, q.y]], z: pc[2] };
        } else if (it.g === "seg") {
          const s = v._projSeg(P, it.pts[0], it.pts[1]);
          if (!s) continue;
          sp = { pts: [[s.a.x, s.a.y], [s.b.x, s.b.y]], z: (s.a.z + s.b.z) / 2 };
        } else {
          const pts = [];
          let ok = true, zs = 0;
          for (const p of it.pts) { const pc = P.toCam(p); if (pc[2] < P.near) { ok = false; break; } const q = P.proj(pc); pts.push([q.x, q.y]); zs += pc[2]; }
          if (!ok || pts.length < 3) continue;
          sp = { pts, z: zs / pts.length };
        }
        const k = proj.length;
        proj.push({ it, ...sp });
        const xs = sp.pts.map(p => p[0]), ys = sp.pts.map(p => p[1]);
        const pad = it.g === "pt" ? (it.r || JOINT_PX) : 10;
        put(k, [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) + pad, Math.max(...ys) + pad]);
      }
      c.proj = proj; c.grid = grid; c.camSig = cs; c.P = P; c.itemProj = null;
    }
    return c;
  }
  const invalidate = v => { if (v) v._fModelVer = (v._fModelVer || 0) + 1; };

  /** World ray through a screen pixel: {o, d} (d unit length). */
  function ray(v, mx, my) {
    const { eye, fwd, right, up } = v._basis();
    const w = v.canvas.clientWidth, h = v.canvas.clientHeight;
    const focal = (h / 2) / Math.tan(v.fov / 2);
    const dx = (mx - w / 2) / focal, dy = -(my - h / 2) / focal;
    const d = [fwd[0] + right[0] * dx + up[0] * dy, fwd[1] + right[1] * dx + up[1] * dy, fwd[2] + right[2] * dx + up[2] * dy];
    const L = Math.hypot(...d);
    return { o: eye, d: d.map(x => x / L) };
  }

  /** Exact hit of one projected item at (mx, my) → {ref, d, z} | null. */
  function hitOf(v, pr, mx, my, thr, R) {
    const it = pr.it;
    if (it.g === "pt") {
      const d = Math.hypot(mx - pr.pts[0][0], my - pr.pts[0][1]);
      return d <= (it.r || JOINT_PX) ? { ref: it.ref, d, z: pr.z } : null;
    }
    if (it.g === "seg") {
      const d = PG.distToSeg(mx, my, pr.pts[0][0], pr.pts[0][1], pr.pts[1][0], pr.pts[1][1]);
      return d <= thr ? { ref: it.ref, d, z: pr.z } : null;
    }
    if (!PG.pointInPoly(mx, my, pr.pts)) return null;
    const r = R || ray(v, mx, my);
    const t = PG.rayPolygon(r.o, r.d, it.pts, it.holes || []);
    return t == null ? null : { ref: it.ref, d: 0, z: t };
  }

  function candidatesCpu(c, mx, my, thr) {
    const out = new Set();
    const r = Math.max(thr, 10);
    for (let x = Math.floor((mx - r) / CELL); x <= Math.floor((mx + r) / CELL); x++)
      for (let y = Math.floor((my - r) / CELL); y <= Math.floor((my + r) / CELL); y++)
        for (const k of c.grid.get(x * 100003 + y) || []) out.add(k);
    for (const k of c.grid.get("big") || []) out.add(k);
    return out;
  }

  /** All hits at a pixel (unsorted). */
  function hitsAt(v, mx, my, thr = 7) {
    if (!v || !v.model) return [];
    const c = screen(v);
    let cand;
    if (v.gpu && v.gpu.on && v.gpu.fPick) {
      try { cand = v.gpu.fPick.candidates(c, mx, my, Math.max(thr, JOINT_PX)); } catch { cand = null; }
    }
    if (!cand) cand = candidatesCpu(c, mx, my, thr);
    const R = ray(v, mx, my);
    const hits = [];
    for (const k of cand) { const h = hitOf(v, c.proj[k], mx, my, thr, R); if (h) hits.push(h); }
    // one hit per object (curved frames / tendons have several pieces)
    const best = new Map();
    for (const h of hits) { const k = PG.refKey(h.ref); const b = best.get(k); if (!b || h.d < b.d || (h.d === b.d && h.z < b.z)) best.set(k, h); }
    return [...best.values()];
  }
  const pickAt = (v, mx, my, thr = 7) => PG.resolvePick(hitsAt(v, mx, my, thr));
  /** Ordered list (priority) — repeated clicks cycle through coincident objects. */
  const pickList = (v, mx, my, thr = 7) => hitsAt(v, mx, my, thr).sort((a, b) =>
    (PG.TYPE_PRIORITY[a.ref.type] - PG.TYPE_PRIORITY[b.ref.type]) || (a.ref.type === "shell" ? 0 : a.d - b.d) || (a.z - b.z)).map(h => h.ref);

  function pickBox(v, x0, y0, x1, y1, crossing) {
    if (!v || !v.model) return [];
    const c = screen(v);
    const r = PG.normRect([x0, y0, x1, y1]);
    // multi-piece objects (curved frames, tendons): WINDOW needs every piece
    // inside, CROSSING any piece touching
    const acc = new Map();
    for (const pr of c.proj) {
      const k = PG.refKey(pr.it.ref);
      const g = pr.it.g;
      const hit = g === "pt" ? PG.rectHitPoint(r, pr.pts[0][0], pr.pts[0][1])
        : g === "seg" ? PG.rectHitSeg(r, pr.pts[0][0], pr.pts[0][1], pr.pts[1][0], pr.pts[1][1], crossing)
          : PG.rectHitPoly(r, pr.pts, crossing);
      let a = acc.get(k);
      if (!a) acc.set(k, a = { ref: pr.it.ref, all: true, any: false });
      a.all = a.all && hit; a.any = a.any || hit;
    }
    return order(v.model, [...acc.values()].filter(a => (crossing ? a.any : a.all)).map(a => a.ref));
  }

  function pickLine(v, p, q) {
    if (!v || !v.model) return [];
    const c = screen(v);
    const out = new Map();
    for (const pr of c.proj) {
      const g = pr.it.g;
      if (pr.it.ref.type === "joint") continue;          // ETABS: the line selects line / area objects (and springs), not joints
      const hit = g === "pt" ? PG.lineHitPoint(p, q, pr.pts[0], 4)
        : g === "seg" ? PG.lineHitSeg(p, q, pr.pts[0], pr.pts[1]) : PG.lineHitPoly(p, q, pr.pts);
      if (hit) out.set(PG.refKey(pr.it.ref), pr.it.ref);
    }
    return order(v.model, [...out.values()]);
  }

  /** model order: frames, shells, links, springs, line springs, tendons, joints. */
  function order(m, refs) {
    const rank = { member: 0, shell: 1, link: 2, spring: 3, linespring: 4, tendon: 5, joint: 6 };
    const idx = {
      member: new Map((m.members || []).map((x, i) => [x.uid, i])), shell: new Map((m.shells || []).map((x, i) => [x.uid, i])),
      link: new Map((m.links || []).map((x, i) => [x.uid, i])), tendon: new Map((m.tendons || []).map((x, i) => [x.uid, i])),
    };
    return refs.sort((a, b) => (rank[a.type] - rank[b.type]) ||
      (((idx[a.type] && idx[a.type].get(a.uid)) ?? 0) - ((idx[b.type] && idx[b.type].get(b.uid)) ?? 0)) || String(a.uid).localeCompare(String(b.uid)));
  }

  return {
    pickAt, pickList, hitsAt, pickBox, pickLine, ray, screen, invalidate,
    filter: () => ({ ...filter }),
    setFilter: f => { filter = { ...defaultFilter(), ...(f || {}) }; writeFilter(filter); },
    passes: (m, ref) => passes(filterKind(m, ref)),
    joints: v => screen(v).scene.joints,
    JOINT_PX,
  };
}
