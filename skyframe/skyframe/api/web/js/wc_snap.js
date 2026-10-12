/* SkyFrame Wave C — object snaps in the ELEVATION and 3D views plus
   EXTENSION and PARALLEL snaps (ETABS Draw › Snap Options).

   * Plan: the W8 snaps (points, mid, intersections, ⊥) are unchanged; the
     extension (along a frame / edge beyond its end) and parallel (from the
     first click, parallel to a frame / edge) snaps are tried after them.
   * Elevation: the same six kinds on the frames, links and wall edges lying
     in the elevation plane (s, z), ahead of the grid-tick × level snap
     (ElevEditor.wcSnapHook, one line in js/elev.js). The beam tool only
     accepts object snaps on a story level.
   * 3D: joints, member / edge midpoints, 3D intersections, ⊥ from the
     first point, extension and parallel, by exact camera rays
     (js/wc_snapgeom.js). Used by Draw › Frames › Frames in 3D View (one
     hook line in js/wb_draw.js) and by the Wave C 3D tools through
     sky.wc.pick3d (general grid line, vertex reshaper).
   Modes are per-browser preferences (localStorage "sky.wc.snap"), never
   model data: ext / par default off (they would move existing plan
   drawing), elevation / 3D object snaps default on.
   UI: Draw › Snap Options… (extended), status-bar chips "Ext" "∥".
   CONTRACT "Wave C interface …" § Snapping. */

import SG8 from "./w8_snapgeom.js";
import WSG from "./wc_snapgeom.js";
import { el as snEl, dkForm as snForm, dkGroup as snGroup, dkDialog as snDialog, dkNote as snNote } from "./dlgkit.js";
import { gridSystems as snGridSystems, gridSystemGeometry as snGridGeom } from "./modeledit.js";

const KEY = "sky.wc.snap";
const DEF = { ext: false, par: false, elev: true, d3: true, maxExtPx: 400 };
const NS = "http://www.w3.org/2000/svg";
const W8L = { joint: "Points & ends", mid: "Midpoints", inter: "Intersections", perp: "Perpendicular" };
const GLYPH_TXT = { joint: "Point", mid: "Mid", inter: "Intersection", perp: "Perpendicular", ext: "Extension", par: "Parallel" };
const COL = "#f5be3c";

function load() {
  try { const o = JSON.parse(localStorage.getItem(KEY) || "null"); if (o && typeof o === "object") return { ...DEF, ...o }; } catch { /* ignore */ }
  return { ...DEF };
}

/** SVG snap glyph at screen (x, y) (plan / elevation overlays). */
export function snapGlyph(g, x, y, kind, ref2screen) {
  const mk = (tag, a) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); g.appendChild(e); return e; };
  if (ref2screen) mk("line", { x1: ref2screen[0][0], y1: ref2screen[0][1], x2: x, y2: y, stroke: COL, "stroke-width": 1, "stroke-dasharray": "3 4", "data-wcsnap-guide": kind });
  if (kind === "joint") mk("rect", { x: x - 6, y: y - 6, width: 12, height: 12, fill: "none", stroke: COL, "stroke-width": 1.6, "data-wcsnap": kind });
  else if (kind === "mid") mk("path", { d: `M${x},${y - 7} L${x + 7},${y + 5} L${x - 7},${y + 5} Z`, fill: "none", stroke: COL, "stroke-width": 1.6, "data-wcsnap": kind });
  else if (kind === "inter") mk("path", { d: `M${x - 6},${y - 6} L${x + 6},${y + 6} M${x + 6},${y - 6} L${x - 6},${y + 6}`, stroke: COL, "stroke-width": 2, "data-wcsnap": kind });
  else if (kind === "perp") mk("path", { d: `M${x - 7},${y + 6} L${x + 7},${y + 6} M${x},${y + 6} L${x},${y - 7}`, fill: "none", stroke: COL, "stroke-width": 1.6, "data-wcsnap": kind });
  else if (kind === "ext") mk("path", { d: `M${x - 7},${y} L${x + 7},${y} M${x + 3},${y - 3} L${x + 7},${y} L${x + 3},${y + 3}`, fill: "none", stroke: COL, "stroke-width": 1.6, "data-wcsnap": kind });
  else if (kind === "par") mk("path", { d: `M${x - 7},${y + 5} L${x - 1},${y - 6} M${x + 1},${y + 5} L${x + 7},${y - 6}`, fill: "none", stroke: COL, "stroke-width": 1.6, "data-wcsnap": kind });
  else return;
  const t = mk("text", { x: x + 9, y: y - 8, fill: COL, "font-size": 10, "font-weight": 700, "font-family": "inherit" });
  t.textContent = GLYPH_TXT[kind];
}

export function initWcSnap(sky) {
  const S = sky.store, pe = sky.planEditor, ee = sky.elevEditor;
  const st = load();
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch { /* ignore */ } };
  const w8 = () => (sky.w8snap ? sky.w8snap.modes() : { joint: true, mid: true, inter: true, perp: true, tolPx: 10 });
  const modes = () => ({ ...w8(), ext: !!st.ext, par: !!st.par });
  const tolPx = () => w8().tolPx || 10;
  let last = { kind: null, view: null };

  /* ------------------------------------------------ plan: ext / par after W8 */
  if (pe) {
    const prev = pe.w8SnapHook;
    pe.w8SnapHook = (w, ed) => {
      const r = prev ? prev(w, ed) : null;
      if (r) { last = { kind: r.kind, view: "plan" }; ed.wcSnapRef = null; return r; }
      ed.wcSnapRef = null;
      if (!(st.ext || st.par)) return null;
      const m = ed.opts.getModel();
      if (!m) return null;
      const tgt = SG8.storyTargets(m, ed.opts.getStory());
      const from = ed.pending ? [ed.pending.x, ed.pending.y] : (ed.w8From || null);
      const o = WSG.snapExtPar(w, tgt, tolPx() / ed.scale, modes(), from, (st.maxExtPx || 400) / ed.scale);
      if (!o) return null;
      last = { kind: o.kind, view: "plan" };
      ed.wcSnapRef = o.kind === "par" ? [from, [o.x, o.y]] : [(Math.hypot(o.ref[0][0] - o.x, o.ref[0][1] - o.y) < Math.hypot(o.ref[1][0] - o.x, o.ref[1][1] - o.y)) ? o.ref[0] : o.ref[1], [o.x, o.y]];
      return { x: o.x, y: o.y, dPx: o.d * ed.scale, kind: o.kind };
    };
    const prevOv = pe.w8Overlay;
    pe.w8Overlay = (g, ed) => {
      if (prevOv) prevOv(g, ed);
      const hs = ed.hoverSnap;
      if (!hs || (hs.kind !== "ext" && hs.kind !== "par") || ed.tool === "select" || ed.tool === "erase") return;
      const [x, y] = ed.toScreen(hs.x, hs.y);
      const ref = ed.wcSnapRef && ed.wcSnapRef[0] ? [ed.toScreen(ed.wcSnapRef[0][0], ed.wcSnapRef[0][1])] : null;
      snapGlyph(g, x, y, hs.kind, ref);
    };
  }

  /* ------------------------------------------------ elevation */
  let eCache = { key: null, tgt: null };
  const elevTargets = ed => {
    const pl = ed.plane();
    const key = pl ? `${pl.axis}|${pl.label}|${pl.coord}|${S.modelRev || 0}` : null;
    if (eCache.key !== key || !eCache.tgt) eCache = { key, tgt: WSG.elevTargets(ed.opts.getModel(), ed) };
    return eCache.tgt;
  };
  document.addEventListener("sky:model-changed", () => { eCache = { key: null, tgt: null }; d3Cache = null; });
  document.addEventListener("sky:model-replaced", () => { eCache = { key: null, tgt: null }; d3Cache = null; });
  if (ee) {
    ee.wcSnapHook = (w, ed, levelOnly) => {
      ed.wcSnapRef = null;
      if (!st.elev || !ed.plane()) return null;
      const md = modes();
      const tgt = elevTargets(ed);
      const from = ed.pending ? [ed.pending.s, ed.pending.z] : null;
      const o = WSG.snap2({ x: w.s, y: w.z }, tgt, tolPx() / ed.scale, md, from, (st.maxExtPx || 400) / ed.scale);
      if (!o) return null;
      if (levelOnly && !ed.levels().some(z => Math.abs(z - o.y) < 1e-6)) return null;
      last = { kind: o.kind, view: "elev" };
      if (o.kind === "ext" || o.kind === "par") ed.wcSnapRef = o.kind === "par" ? [from, [o.x, o.y]] : [(Math.hypot(o.ref[0][0] - o.x, o.ref[0][1] - o.y) < Math.hypot(o.ref[1][0] - o.x, o.ref[1][1] - o.y)) ? o.ref[0] : o.ref[1], [o.x, o.y]];
      return { s: o.x, z: o.y, dPx: o.d * ed.scale, kind: o.kind };
    };
    const prevOv = ee.wcOverlay;
    ee.wcOverlay = (g, ed) => {
      if (prevOv) prevOv(g, ed);
      const hs = ed.hoverSnap;
      if (!hs || !hs.kind || ed.tool === "select" || ed.tool === "erase") return;
      const [x, y] = ed.toScreen(hs.s, hs.z);
      const ref = ed.wcSnapRef && ed.wcSnapRef[0] ? [ed.toScreen(ed.wcSnapRef[0][0], ed.wcSnapRef[0][1])] : null;
      snapGlyph(g, x, y, hs.kind, ref);
    };
  }

  /* ------------------------------------------------ 3D */
  let d3Cache = null;
  const gridPts = m => { const out = []; for (const sys of snGridSystems(m)) { try { for (const p of snGridGeom(sys).intersections) out.push(p); } catch { /* skip */ } } return out; };
  const targets3 = () => { if (!d3Cache) d3Cache = WSG.targets3(S.model, gridPts(S.model)); return d3Cache; };
  /** Best 3D snap at (mx, my) of viewer v ({p, x, y, z, kind}) or null. */
  function pick3(v, mx, my, from = null) {
    if (!st.d3 || !v || !S.model) return null;
    const P = v._makeProjector(), R = WSG.ray(v, mx, my);
    const r = WSG.snap3(P, R, mx, my, targets3(), Math.max(tolPx(), 12), modes(), from);
    if (!r) return null;
    last = { kind: r.kind, view: "3d" };
    return r;
  }

  /* generic 3D pick service: overlay canvas + click callback */
  const PK = { on: null, a: null, hover: null, raf: 0 };
  function overlay(v) {
    if (v.wcOverlay) return v.wcOverlay;
    const parent = v.canvas.parentElement;
    if (getComputedStyle(parent).position === "static") parent.style.position = "relative";
    const c = snEl("canvas", { class: "wc-3d-overlay", "aria-hidden": "true" });
    parent.appendChild(c);
    v.wcOverlay = c;
    return c;
  }
  function draw3(v) {
    const c = overlay(v), w = v.canvas.clientWidth, h = v.canvas.clientHeight, dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    if (!PK.on) return;
    const P = v._makeProjector();
    const sp = p => { const cc = P.toCam(p); return cc[2] < P.near ? null : P.proj(cc); };
    if (PK.on.draw) PK.on.draw(g, sp, v);
    if (PK.a) {
      const a = sp(PK.a);
      if (a) {
        g.fillStyle = "#35b5e5"; g.beginPath(); g.arc(a.x, a.y, 4, 0, 7); g.fill();
        if (PK.hover) { g.strokeStyle = "rgba(224,160,32,0.95)"; g.setLineDash([7, 4]); g.lineWidth = 2; g.beginPath(); g.moveTo(a.x, a.y); g.lineTo(PK.hover.x, PK.hover.y); g.stroke(); g.setLineDash([]); }
      }
    }
    const hv = PK.hover;
    if (hv) {
      g.strokeStyle = COL; g.lineWidth = 1.6; g.beginPath();
      if (hv.kind === "joint") g.rect(hv.x - 6, hv.y - 6, 12, 12); else g.arc(hv.x, hv.y, 7, 0, 7);
      g.stroke();
      g.fillStyle = COL; g.font = "700 10px sans-serif"; g.fillText(GLYPH_TXT[hv.kind] || hv.kind, hv.x + 9, hv.y - 8);
    }
  }
  let bound = false;
  function bind3(v) {
    if (bound || !v) return;
    bound = true;
    overlay(v);
    const pos = e => { const r = v.canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    let down = null;
    v.canvas.addEventListener("pointerdown", e => { if (PK.on && e.button === 0) down = [e.clientX, e.clientY]; });
    v.canvas.addEventListener("pointermove", e => {
      if (!PK.on) return;
      const [x, y] = pos(e);
      PK.hover = pick3(v, x, y, PK.a);
      draw3(v);
    });
    v.canvas.addEventListener("pointerup", e => {
      if (!PK.on || !down) return;
      const moved = Math.hypot(e.clientX - down[0], e.clientY - down[1]);
      down = null;
      if (moved > 5) return;
      const [x, y] = pos(e);
      const h = pick3(v, x, y, PK.a);
      if (!h) return;
      const job = PK.on, p = h.p.slice();
      if (job.twoClick === false) { job.onPick(p, null, h); PK.a = null; }
      else if (!PK.a) PK.a = p;
      else { const r = job.onPick(p, PK.a.slice(), h); PK.a = r === "chain" ? p : null; }
      draw3(v);
    });
    v.canvas.addEventListener("pointerleave", () => { if (PK.on) { PK.hover = null; draw3(v); } });
  }
  const loop = () => { if (!PK.on) { PK.raf = 0; return; } if (sky.viewer) draw3(sky.viewer); PK.raf = requestAnimationFrame(loop); };
  /** Start a 3D pick job {label, onPick(p, prev, hit) → "chain" | void, twoClick? (default true: the first click only stores the point), draw?(g, sp, v), onStop?}. */
  function start(job) {
    const v = sky.viewer;
    if (!v) return false;
    if (sky.etabs && sky.etabs.showResult) sky.etabs.showResult("view3d"); else { sky.setMode("analyze"); sky.switchTab("view3d"); }
    bind3(v);
    PK.on = job; PK.a = null; PK.hover = null; d3Cache = null;
    if (!PK.raf) PK.raf = requestAnimationFrame(loop);
    return true;
  }
  function stop() { PK.on = null; PK.a = null; PK.hover = null; if (sky.viewer && sky.viewer.wcOverlay) draw3(sky.viewer); }
  document.addEventListener("keydown", e => { if (e.key === "Escape" && PK.on) { const j = PK.on; stop(); j.onStop && j.onStop(); } });

  /* ------------------------------------------------ status-bar chips */
  const chips = {};
  const syncChips = () => { for (const k of Object.keys(chips)) { chips[k].classList.toggle("is-on", !!st[k]); chips[k].setAttribute("aria-pressed", st[k] ? "true" : "false"); } };
  const grp = document.getElementById("w8OsnapChips");
  if (grp) {
    for (const [k, t, tip] of [["ext", "Ext", "extensions of frames and edges"], ["par", "∥", "parallel to a frame or edge (from the first click)"]]) {
      const b = snEl("button", { type: "button", class: "sb-chip w8-osnap-chip", id: "wcSnap_" + k, title: `Snap to ${tip} (click to toggle)` }, [t]);
      b.addEventListener("click", () => { st[k] = !st[k]; save(); syncChips(); });
      chips[k] = b;
      grp.appendChild(b);
    }
    syncChips();
  }

  /* ------------------------------------------------ Snap Options (extended) */
  function openSnapOptions() {
    const m8 = w8();
    const D = { ...m8, ...st, grid: sky.etabs && sky.etabs.snapOn ? sky.etabs.snapOn() : true };
    const F = snForm();
    const body = [
      snGroup("Snap To", [
        F.check(D, "grid", { label: "Grid lines and intersections", id: "wcSnapGrid" }),
        ...Object.keys(W8L).map(k => F.check(D, k, { label: W8L[k], id: "wcSnapChk_" + k })),
        F.check(D, "ext", { label: "Extensions", id: "wcSnapChk_ext", hint: "along a frame or edge, beyond its end" }),
        F.check(D, "par", { label: "Parallel", id: "wcSnapChk_par", hint: "from the first click, parallel to a frame or edge" }),
      ]),
      snGroup("Views", [
        F.check(D, "elev", { label: "Object snaps in elevation views", id: "wcSnapElev" }),
        F.check(D, "d3", { label: "Object snaps in 3D views", id: "wcSnapD3" }),
      ]),
      snGroup("Tolerance", [
        F.num(D, "tolPx", { label: "Snap radius", unit: "px", int: true, min: 2, max: 40, id: "wcSnapTol" }),
        F.num(D, "maxExtPx", { label: "Longest extension", unit: "px", int: true, min: 20, max: 4000, id: "wcSnapMaxExt" }),
      ]),
      snNote("Order inside the snap radius: points & ends → intersections → midpoints → perpendicular → extension → parallel; the grid snap applies when none is in range."),
    ];
    return snDialog({
      id: "wcSnapDlg", title: "Snap Options", iconId: "status-snap", size: "sm", form: F, body,
      onOk: () => {
        if (sky.w8snap) for (const k of Object.keys(W8L)) if (!!D[k] !== !!m8[k]) sky.w8snap.set(k, !!D[k]);
        if (sky.w8snap && D.tolPx !== m8.tolPx) sky.w8snap.set("tolPx", D.tolPx);
        for (const k of ["ext", "par", "elev", "d3"]) st[k] = !!D[k];
        st.maxExtPx = D.maxExtPx;
        save(); syncChips();
        const ch = document.querySelectorAll(".w8-osnap-chip[id^=w8Snap_]");
        ch.forEach(b => { const k = b.id.slice(7); if (k in W8L) b.classList.toggle("is-on", !!D[k]); });
        if (sky.etabs && sky.etabs.snapOn && sky.etabs.snapOn() !== !!D.grid) sky.etabs.toggleSnap();
      },
    });
  }
  const it = sky.menu && sky.menu.item && sky.menu.item("draw-w8-snap");
  if (it) { it.wcLegacyFn = it.fn; it.fn = openSnapOptions; it.hint = "points · ends · mid · ∩ · ⊥ · ext · ∥"; }

  sky.wc.snap = {
    modes: () => ({ ...modes(), elev: st.elev, d3: st.d3 }),
    set: (k, v) => { if (k in DEF) { st[k] = v; save(); syncChips(); } else if (sky.w8snap) sky.w8snap.set(k, v); },
    lastKind: () => ({ ...last }),
    open: openSnapOptions,
    pick3, targets3: () => targets3(), geom: WSG,
  };
  sky.wc.pick3 = pick3;                 // js/wb_draw.js hook (Frames in 3D View)
  sky.wc.pick3d = { start, stop, active: () => !!PK.on, state: () => ({ on: PK.on ? PK.on.label : null, a: PK.a && PK.a.slice(), hover: PK.hover && { p: PK.hover.p, kind: PK.hover.kind, x: PK.hover.x, y: PK.hover.y } }) };
}
