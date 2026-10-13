/* SkyFrame Wave F — plan and elevation views on the shared selection model.

   The plan / elevation editors keep their SVG hit tests; this module only
   extends them (prototype wrappers, so the main editors AND every
   View › Windows pane get the same behaviour):
     * joints become selectable (type "joint", key = coordinates rounded to
       1e-6 — the key every joint assignment resolves): a click within 6 px
       of a joint picks it (a column square at the same spot wins in plan;
       click again to cycle), boxes add the joints inside them;
     * the per-browser selection filter (Select › Select Using › Selection
       Filter…) drops object types from clicks and boxes;
     * selected joints and tendons are drawn (dashed outline + tint) and the
       hover joint is pre-highlighted;
     * elevation: Window / Crossing box (when Select › Select Using › Window /
       Crossing Box is on: → window, ← crossing) and the Intersecting Line. */

import { PlanEditor as fsPlanEditor } from "./draw.js";
import { ElevEditor as fsElevEditor } from "./elev.js";
import * as PG from "./f_pickgeom.js";
import { springKey as f2SpringKey, lineSpringKey as f2LineSpringKey } from "./modeledit.js";
import { canonical as f2TgCanonical, samplePolyline as f2TgSample } from "./tendon_geom.js";

const NS = "http://www.w3.org/2000/svg";
const JPX = 6;
const svgEl = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };

export function initFSelect2d(sky, F) {
  const S = sky.store;
  const jcache = { m: null, sig: "", map: null };
  const joints = () => {
    const m = S.model;
    const sig = m ? [(m.members || []).length, (m.shells || []).length, (m.links || []).length, (m.supports || []).length, (m.spring_supports || []).length, F.modelVer || 0].join(",") : "";
    if (jcache.m !== m || jcache.sig !== sig) { jcache.m = m; jcache.sig = sig; jcache.map = m ? PG.modelJoints(m) : new Map(); }
    return jcache.map;
  };
  document.addEventListener("sky:model-changed", () => { F.modelVer = (F.modelVer || 0) + 1; });
  const passes = ref => !S.model || F.pick.passes(S.model, ref);
  const isPlan = ed => ed instanceof fsPlanEditor;

  /** Joints shown by an editor: [{key, p, x, y (screen)}]. */
  function edJoints(ed) {
    const m = ed.opts.getModel();
    if (!m) return [];
    const out = [];
    if (isPlan(ed)) {
      const st = (m.stories || []).find(s => s.name === ed.opts.getStory());
      if (!st) return out;
      for (const [k, p] of joints()) if (Math.abs(p[2] - st.elevation) < 1e-4) { const [x, y] = ed.toScreen(p[0], p[1]); out.push({ key: k, p, x, y }); }
    } else {
      if (!ed.plane()) return out;
      for (const [k, p] of joints()) if (ed.inPlane(p)) { const [x, y] = ed.toScreen(ed.sOf(p), p[2]); out.push({ key: k, p, x, y }); }
    }
    return out;
  }

  /* ---------------------------------------------------------------- hit tests */
  const WRAP = {
    _candidates(ed, orig, w) {
      let out = orig(w);
      const [px, py] = isPlan(ed) ? ed.toScreen(w.x, w.y) : ed.toScreen(w.s, w.z);
      let best = null, bd = JPX;
      for (const j of edJoints(ed)) { const d = Math.hypot(j.x - px, j.y - py); if (d <= bd) { bd = d; best = j; } }
      out = out.filter(passes);
      if (best && passes({ type: "joint", uid: best.key })) {
        const ref = { type: "joint", uid: best.key };
        // plan: a column square at the joint wins (click again to cycle to the joint)
        const colFirst = isPlan(ed) && out.length && out[0].type === "member" &&
          ((S.model.members || []).find(x => x.uid === out[0].uid) || {}).kind === "column";
        if (colFirst) out.splice(1, 0, ref); else out.unshift(ref);
      }
      return out;
    },
    _boxRefs(ed, orig, b) {
      let refs;
      if (!isPlan(ed) && F.boxMode && F.boxMode()) refs = elevBox(ed, b, b.s1 >= b.s0 ? "window" : "crossing");
      else refs = orig(b);
      refs = (refs || []).filter(passes);
      const R = isPlan(ed) ? PG.normRect([b.x0, b.y0, b.x1, b.y1]) : PG.normRect([b.s0, b.z0, b.s1, b.z1]);
      for (const j of edJoints(ed)) {
        const x = isPlan(ed) ? j.p[0] : ed.sOf(j.p), y = isPlan(ed) ? j.p[1] : j.p[2];
        const ref = { type: "joint", uid: j.key };
        if (PG.rectHitPoint(R, x, y) && passes(ref)) refs.push(ref);
      }
      return refs;
    },
    renderOverlay(ed, orig) {
      orig();
      try { drawSel(ed); } catch (e) { console.error(e); }
    },
    renderStatic(ed, orig) {
      orig();
      if (ed.gOverlay) { try { ed.renderOverlay(); } catch (e) { /* sized later */ } }
      if (F.selChanged) F.selChanged();
    },
  };
  // the main editors carry instance wrappers (W8 curved picking, View › Windows sync):
  // wrap the instances first, with the methods they resolve to NOW ...
  for (const ed of [sky.planEditor, sky.elevEditor]) {
    if (!ed) continue;
    for (const [name, fn] of Object.entries(WRAP)) { const orig = ed[name].bind(ed); ed[name] = (...a) => fn(ed, orig, ...a); }
    ed._fWrapped = true;
  }
  // ... then the prototypes, for every editor created later (View › Windows panes)
  for (const Cls of [fsPlanEditor, fsElevEditor]) {
    const proto = Cls.prototype;
    for (const [name, fn] of Object.entries(WRAP)) {
      const o = proto[name];
      proto[name] = function (...a) { return this._fWrapped ? o.apply(this, a) : fn(this, (...b) => o.apply(this, b), ...a); };
    }
  }

  /* ---------------------------------------------------------------- elevation objects (s, z) */
  function elevObjects(ed) {
    const m = ed.opts.getModel();
    const out = [];
    if (!m || !ed.plane()) return out;
    const sz = p => [ed.sOf(p), p[2]];
    const inPl = (...ps) => ps.every(p => ed.inPlane(p));
    for (const mm of m.members || []) if (inPl(mm.pi, mm.pj)) out.push({ ref: { type: "member", uid: mm.uid }, segs: [[sz(mm.pi), sz(mm.pj)]], pts: [sz(mm.pi), sz(mm.pj)] });
    for (const lk of m.links || []) if (inPl(lk.pi, lk.pj)) out.push({ ref: { type: "link", uid: lk.uid }, segs: [[sz(lk.pi), sz(lk.pj)]], pts: [sz(lk.pi), sz(lk.pj)] });
    for (const sh of m.shells || []) if (sh.kind === "wall" && inPl(...sh.corners)) { const poly = sh.corners.map(sz); out.push({ ref: { type: "shell", uid: sh.uid }, poly, pts: poly }); }
    for (const sp of m.spring_supports || []) if (inPl(sp.point)) out.push({ ref: { type: "spring", uid: f2SpringKey(sp.point) }, pts: [sz(sp.point)] });
    for (const ls of m.line_springs || []) if (inPl(ls.p1, ls.p2)) out.push({ ref: { type: "linespring", uid: f2LineSpringKey(ls) }, segs: [[sz(ls.p1), sz(ls.p2)]], pts: [sz(ls.p1), sz(ls.p2)] });
    return out;
  }
  function elevBox(ed, b, mode) {
    const R = PG.normRect([b.s0, b.z0, b.s1, b.z1]);
    const cr = mode === "crossing";
    const out = [];
    for (const o of elevObjects(ed)) {
      const hit = o.poly ? PG.rectHitPoly(R, o.poly, cr)
        : o.segs ? PG.rectHitSeg(R, o.segs[0][0][0], o.segs[0][0][1], o.segs[0][1][0], o.segs[0][1][1], cr)
          : PG.rectHitPoint(R, o.pts[0][0], o.pts[0][1]);
      if (hit) out.push(o.ref);
    }
    return out;
  }
  function elevLineRefs(ed, p, q) {
    const tol = 9 / ed.scale;
    const out = [];
    for (const o of elevObjects(ed)) {
      const hit = o.poly ? PG.lineHitPoly(p, q, o.poly) : o.segs ? PG.lineHitSeg(p, q, o.segs[0][0], o.segs[0][1]) : PG.lineHitPoint(p, q, o.pts[0], tol);
      if (hit && passes(o.ref)) out.push(o.ref);
    }
    return out;
  }

  /* ---------------------------------------------------------------- selection drawing */
  function drawSel(ed) {
    const g = ed.gOverlay;
    if (!g || !S.model) return;
    const sel = S.selection || [];
    const tok = F.tokens ? F.tokens() : { sel: "#35b5e5", selTint: "rgba(53,181,229,0.22)", hover: "#ffffff" };
    const plan = isPlan(ed);
    const jsel = new Set(sel.filter(r => r.type === "joint").map(r => r.uid));
    const hov = ed.hoverRef && ed.hoverRef.type === "joint" ? ed.hoverRef.uid : null;
    if (jsel.size || hov) {
      for (const j of edJoints(ed)) {
        const on = jsel.has(j.key), h = j.key === hov;
        if (!on && !h) continue;
        g.appendChild(svgEl("rect", { x: j.x - 5, y: j.y - 5, width: 10, height: 10, fill: on ? tok.selTint : "none",
          stroke: h ? tok.hover : tok.sel, "stroke-width": h ? 2.2 : 1.8, "stroke-dasharray": on && !h ? "3 2" : "none", "data-f": `joint:${j.key}`, "pointer-events": "none" }));
      }
    }
    // tendons
    for (const r of sel) {
      if (r.type !== "tendon") continue;
      const t = (S.model.tendons || []).find(o => o.uid === r.uid);
      if (!t) continue;
      let pts;
      try { pts = f2TgSample(f2TgCanonical(t), 8); } catch { continue; }
      if (!plan && !pts.every(p => ed.inPlane(p))) continue;
      const scr = pts.map(p => (plan ? ed.toScreen(p[0], p[1]) : ed.toScreen(ed.sOf(p), p[2])));
      g.appendChild(svgEl("polyline", { points: scr.map(p => p.join(",")).join(" "), fill: "none", stroke: tok.sel, "stroke-width": 3,
        "stroke-dasharray": "7 4", "data-f": `tendon:${r.uid}`, "pointer-events": "none" }));
    }
  }

  /* ---------------------------------------------------------------- elevation intersecting line */
  const eline = { ed: null, p: null };
  function startElevLine(ed) {
    eline.ed = ed || sky.elevEditor; eline.p = null;
    sky.toast && sky.toast("Intersecting line", "Click two points in the elevation: every object the line crosses is added to the selection. Esc ends the tool.", "info", 4000);
  }
  const endElevLine = () => { const on = !!eline.ed; if (eline.ed) { const ed = eline.ed; eline.ed = null; eline.p = null; ed.renderOverlay(); } return on; };
  document.addEventListener("pointerdown", e => {
    const ed = eline.ed;
    if (!ed || e.button !== 0 || !ed.svg.contains(e.target)) return;
    e.stopPropagation(); e.preventDefault();
    const [px, py] = ed._evPx(e);
    const w = ed.toWorld(px, py);
    if (!eline.p) { eline.p = [w.s, w.z]; return; }
    const p = eline.p; eline.p = null;
    if (Math.hypot(w.s - p[0], w.z - p[1]) < 1e-9) return;
    const refs = elevLineRefs(ed, p, [w.s, w.z]);
    if (refs.length) sky.handleSelect(refs, true);
    ed.renderOverlay();
  }, true);
  document.addEventListener("pointermove", e => {
    const ed = eline.ed;
    if (!ed || !eline.p || !ed.svg.contains(e.target)) return;
    const [px, py] = ed._evPx(e);
    ed.renderOverlay();
    const [x1, y1] = ed.toScreen(eline.p[0], eline.p[1]);
    ed.gOverlay.appendChild(svgEl("line", { x1, y1, x2: px, y2: py, stroke: "#f5be3c", "stroke-width": 1.6, "stroke-dasharray": "6 4", "data-f": "selline" }));
  }, true);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && eline.ed) endElevLine(); }, true);

  return { edJoints, elevBox, elevLineRefs, elevObjects, startElevLine, endElevLine, elevLineState: () => ({ on: !!eline.ed, p: eline.p }) };
}
