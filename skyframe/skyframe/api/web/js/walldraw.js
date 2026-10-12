/* SkyFrame D1 — Draw Walls in the plan view (ETABS Draw › Draw Walls /
   Quick Draw Walls, improved). CONTRACT "Draw Walls (plan view)".

   Tool "wall" (W, toolbar, Draw › Floors & Walls › Draw Walls) becomes a
   polyline wall tool with a floating properties box:
     * click · click · click …  — one wall panel per segment, previewed live
       (true thickness band, alignment offset, split points, length + angle);
       double-click / Enter / Esc finishes, Backspace removes the last point,
       clicking the first point closes the loop (mitred corners);
     * snaps: the plan editor's own snap (grid intersections, grid lines,
       joints / ends, frame midpoints, wall ends, intersections, ⊥ — js/w8_snap.js)
       plus the chain's first point; Shift (or F8 / the Ortho box) locks
       0° / 90°; typing while drawing ("6", "6<90", "@3,0", "12,6") places the
       next point exactly (display units);
     * properties: wall section, story scope (This / Similar / All — the same
       scope as the plan "Apply to" selector), alignment Left / Centre /
       Right of the drawn line + extra offset, height from the story below to
       the story with bottom / top offsets, pier / spandrel label, split at
       grid lines.
   Tool "wdquick" (Quick Draw Walls): click a grid line between two
   intersections → one wall panel; drag a box → a panel on every grid-line
   segment inside it. Source "Beams" draws walls under existing beams instead
   (click a beam or box several); Draw › … › Walls Along Selected Beams does
   the same for the current selection.

   Walls are ordinary ShellRegion(kind="wall") objects (corners bottom edge
   first, exactly like modeledit.addWall); "pier" / "spandrel" are written
   only when chosen, so piers / spandrels keep working unchanged. One finished
   polyline = one undo step. Settings are a per-browser preference
   (localStorage), never part of the model.

   Hooks: PlanEditor instance overrides (_toolClick / setTool / w8Overlay
   chain — no draw.js edit), one init line in app.js, one hint line in
   ux_status.js, the "Wall" label in etabs.js. Geometry: js/walldraw_geom.js
   (mirrored + unit-tested by skyframe/core/walldraw_geom.py). */

import WG from "./walldraw_geom.js";
import wdU from "./units.js";
import { ICONS as wdICONS, icon as wdIcon } from "./icons.js";
import { gridSystems as wdGridSystems, gridSystemGeometry as wdGridGeom } from "./modeledit.js";

const LS = "sky.walldraw.v1";
const QUICK = "wdquick";
const NS = "http://www.w3.org/2000/svg";
const COL = { band: "rgba(53, 181, 229, 0.42)", line: "#35b5e5", vtx: "#35b5e5", close: "#f5be3c",
  hover: "rgba(245, 190, 60, 0.85)", box: "rgba(53, 181, 229, 0.10)", boxEdge: "rgba(53, 181, 229, 0.6)",
  tagBg: "rgba(17, 22, 29, 0.92)", tagFg: "#e8edf3", split: "#f5be3c" };
const DEFAULTS = { src: "grid", section: "", align: "centre", offset: 0, botOff: 0, topOff: 0,
  pier: "", spandrel: "", split: false, ortho: false, collapsed: false, float: false, pos: null };

wdICONS["tool-wdquick"] = '<path d="M3 4 V16 M10 4 V16 M17 4 V16" stroke-dasharray="1.6 1.6" opacity=".6"/>'
  + '<rect x="3" y="8" width="7" height="4" fill="currentColor" stroke="none" opacity=".55"/><path d="M3 8 H10 V12 H3 Z"/>'
  + '<path d="M13 9.5 L16 9.5 M14.5 8 V11" stroke-width="1.3"/>';

function loadSt() {
  try { const o = JSON.parse(localStorage.getItem(LS) || "null"); if (o && typeof o === "object") return { ...DEFAULTS, ...o }; } catch { /* ignore */ }
  return { ...DEFAULTS };
}

export function initWallDraw(sky) {
  const S = sky.store, pe = sky.planEditor, ME = sky.ME;
  if (!S || !pe || !ME) return;
  if (!document.querySelector("link[data-wdcss]")) {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = "/static/walldraw.css"; l.dataset.wdcss = "1";
    document.head.appendChild(l);
  }
  const st = loadSt();
  const save = () => { try { localStorage.setItem(LS, JSON.stringify(st)); } catch { /* ignore */ } };
  const W = { pts: [], closeHover: false, shift: false, ptrDown: false, qdown: null, qmove: null, flash: null, flashUntil: 0 };
  const svg = pe.svg, wrap = svg.parentElement;
  const toast = (t, m, k = "info", ms = 2600) => sky.toast && sky.toast(t, m, k, ms);

  /* ------------------------------------------------ state helpers */
  const isPlan = () => S.mode === "model" && S.view !== "elev";
  const active = () => isPlan() && (S.tool === "wall" || S.tool === QUICK);
  const lenU = () => wdU.label("length");
  const fmtL = v => wdU.fmt("length", v, 2).replace(/,/g, "");
  const scope = () => (sky.w8stories ? sky.w8stories.scope() : (S.applyAll ? "all" : "one"));
  const setScope = v => {
    if (sky.w8stories) sky.w8stories.setScope(v);
    else { S.applyAll = v === "all"; const b = document.querySelector(`#applyToggle [data-apply="${v === "all" ? "all" : "one"}"]`); if (b) b.click(); }
    if (sky.syncStoryBadges) sky.syncStoryBadges();
  };
  const scopeNames = story => WG.scopeStories(S.model.stories, story, scope());
  const modalOpen = () => !!(document.querySelector(".modal-backdrop:not(.hidden)") ||
    (sky.ux && sky.ux.dialogs && sky.ux.dialogs.top && sky.ux.dialogs.top()));

  function sectionNames() { return Object.keys((S.model && S.model.shell_sections) || {}); }
  /** Chosen wall section, else the section most used by walls, else one named
      like a wall, else a non-floor section, else the first one. */
  function resolveSection() {
    const names = sectionNames();
    if (st.section && names.includes(st.section)) return st.section;
    const cnt = {};
    for (const s of S.model.shells || []) if (s.kind === "wall" && names.includes(s.section)) cnt[s.section] = (cnt[s.section] || 0) + 1;
    const used = Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a])[0];
    return used || names.find(n => /wall|shear|core/i.test(n)) || names.find(n => !/slab|deck|floor|plank|roof/i.test(n)) || names[0] || "";
  }
  // no shell section yet: modeledit.defaultShellSection creates SH200 (t = 0.2 m) with the first wall
  const thickness = () => { const n = resolveSection(); const s = (S.model.shell_sections || {})[n]; return s ? +s.thickness || 0 : (n ? 0 : 0.2); };
  const offsetSI = () => WG.alignOffset(st.align, thickness(), st.offset);
  function splitLines() {
    const out = [];
    for (const sys of wdGridSystems(S.model)) {
      try { for (const [ax, ay, bx, by] of wdGridGeom(sys).segments || []) out.push([[ax, ay], [bx, by]]); } catch { /* ignore */ }
    }
    return out;
  }
  function quickSegs() {
    const m = S.model;
    if (st.src === "beams") return WG.beamSegs(m, S.story).map(s => [s[0], s[1]]);
    if (sky.wb && sky.wb.draw && sky.wb.draw.gridInBox) {
      try { return sky.wb.draw.gridInBox([[-1e7, -1e7], [1e7, 1e7]]).segs; } catch { /* fall through */ }
    }
    const g = m.grid || {};
    return WG.gridSegments(g.x_lines || [], g.y_lines || []);
  }
  function refreshAll() {
    if (sky.planEditor) sky.planEditor.refresh();
    if (sky.elevEditor) sky.elevEditor.refresh();
    if (sky.viewer && sky.viewer.setModel) {
      const v = sky.viewer, cam = [v.yaw, v.pitch, v.dist, v.target];
      v.setModel(S.model); [v.yaw, v.pitch, v.dist, v.target] = cam; v._dirty = true;
    }
    if (sky.renderProps) sky.renderProps();
  }

  /** Create wall shells for plan panels on the scope of `story`. Returns the count. */
  function createWalls(panels, story, label) {
    const m = S.model;
    if (!panels.length) return 0;
    let sec = resolveSection();
    if (!sec) sec = ME.defaultShellSection(m);
    const names = scopeNames(story);
    const list = WG.buildWalls(panels, m.stories, names, st.botOff, st.topOff, m.shells);
    if (!list.length) { flash(`${label}: nothing new — those walls already exist`); return 0; }
    const pier = st.pier === "__auto__" ? WG.nextPier(m.shells) : st.pier;
    for (const w of list) {
      const sh = { uid: ME.nextUid(m, "W"), kind: "wall", behavior: "shell", section: sec,
        corners: w.corners, mesh_size: 1.0, story: w.story, openings: [] };
      if (pier) sh.pier = pier;
      if (st.spandrel) sh.spandrel = st.spandrel;
      m.shells.push(sh);
    }
    sky.markDirty();
    refreshAll();
    const ns = new Set(list.map(w => w.story)).size;
    flash(`${label}: ${list.length} wall${list.length === 1 ? "" : "s"} (${sec}) on ${ns} stor${ns === 1 ? "y" : "ies"}${pier ? ` · pier ${pier}` : ""}`);
    syncBox();
    return list.length;
  }
  function flash(t) { W.flash = t; W.flashUntil = Date.now() + 5000; const r = document.getElementById("wdRead"); if (r) r.textContent = t; }

  /* ------------------------------------------------ polyline drawing */
  function rawTarget() {
    const hs = pe.hoverSnap, mw = pe._mouseWorld;
    if (hs) return [hs.x, hs.y];
    if (mw) { const s = pe.snap(mw); return [s.x, s.y]; }
    return null;
  }
  /** Effective next point: snap → close-to-first → ortho lock. */
  function effective(p, raw) {
    W.closeHover = false;
    if (!p) return null;
    const n = W.pts.length;
    if (n >= 3 && raw) {
      const f = W.pts[0], [fx, fy] = pe.toScreen(f[0], f[1]), [rx, ry] = pe.toScreen(raw[0], raw[1]);
      if (Math.hypot(fx - rx, fy - ry) <= 10) { W.closeHover = true; return [f[0], f[1]]; }
    }
    if (n && (st.ortho !== W.shift)) return WG.orthoPoint(W.pts[n - 1], p);
    return [p[0], p[1]];
  }
  const curTarget = () => { const mw = pe._mouseWorld; return effective(rawTarget(), mw ? [mw.x, mw.y] : null); };
  const sameP = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-9;

  function addPoint(p) {
    const n = W.pts.length;
    if (n >= 3 && sameP(p, W.pts[0])) { finish(true); return; }
    if (n && sameP(p, W.pts[n - 1])) return;          // 2nd click of a double-click
    W.pts.push([p[0], p[1]]);
    pe.w8From = [p[0], p[1]];                         // ⊥ snaps from the last point
    syncBox();
  }
  function wallClick(w) {
    const s = pe.snap(w);
    const p = effective([s.x, s.y], [w.x, w.y]);
    if (p) addPoint(p);
  }
  function undoPoint() { W.pts.pop(); pe.w8From = W.pts.length ? W.pts[W.pts.length - 1] : null; syncBox(); pe.renderOverlay(); }
  function cancelChain() { W.pts = []; pe.w8From = null; syncBox(); pe.renderOverlay(); }
  function finish(closed = false) {
    const pts = W.pts.slice();
    W.pts = []; pe.w8From = null; W.closeHover = false;
    let n = 0;
    if (pts.length >= 2) {
      const panels = WG.wallPanels(pts, { closed: closed && pts.length >= 3, offset: offsetSI(), lines: st.split ? splitLines() : null });
      n = createWalls(panels, S.story, "Draw Walls");
    }
    syncBox(); pe.renderOverlay();
    return n;
  }
  function typedPoint(text) {
    const n = W.pts.length;
    const f = wdU.fromDisplay("length", 1);
    if (!n) {   // first point: absolute "x,y" only
      const mm = /^\s*([-+]?[\d.]+(?:e[-+]?\d+)?)\s*,\s*([-+]?[\d.]+(?:e[-+]?\d+)?)\s*$/i.exec(String(text));
      if (!mm || !isFinite(+mm[1]) || !isFinite(+mm[2])) return null;
      return [WG.r9(+mm[1] * f), WG.r9(+mm[2] * f)];
    }
    const last = W.pts[n - 1], tgt = curTarget();
    return WG.parseTyped(text, last, tgt && !sameP(tgt, last) ? tgt : null, f);
  }

  /* ------------------------------------------------ quick draw */
  function quickPick(p) {
    const segs = quickSegs();
    const i = WG.nearestSeg(segs, p, Math.max(12 / pe.scale, 0.05));
    if (i < 0) { flash(st.src === "beams" ? "Quick Draw Walls: no beam under the cursor on this story" : "Quick Draw Walls: click ON a grid line between two intersections"); return 0; }
    return quickCreate([segs[i]]);
  }
  function quickCreate(segs) {
    const lines = st.split ? splitLines() : null, off = offsetSI();
    const panels = [];
    for (const [a, b] of segs) panels.push(...WG.wallPanels([a, b], { offset: off, lines }));
    return createWalls(panels, S.story, "Quick Draw Walls");
  }
  function quickBox(a, b) {
    const segs = WG.segsInBox(quickSegs(), [a, b]);
    if (!segs.length) { flash("Quick Draw Walls: the box holds no complete " + (st.src === "beams" ? "beam" : "grid-line segment")); return 0; }
    return quickCreate(segs);
  }

  /** Walls under the selected beams (each on its own story + the scope). */
  function wallsAlongSelectedBeams() {
    const m = S.model;
    const sel = (S.selection || []).filter(r => r.type === "member");
    const beams = sel.map(r => m.members.find(x => x.uid === r.uid)).filter(b => b && b.kind === "beam" && !b.curve);
    if (!beams.length) { toast("Walls Along Beams", "Select one or more beams first (V), then run the command again.", "warn", 4000); return 0; }
    const lines = st.split ? splitLines() : null, off = offsetSI();
    let made = 0;
    const byStory = {};
    for (const b of beams) (byStory[b.story] = byStory[b.story] || []).push(b);
    for (const [story, list] of Object.entries(byStory)) {
      const panels = [];
      for (const b of list) panels.push(...WG.wallPanels([[b.pi[0], b.pi[1]], [b.pj[0], b.pj[1]]], { offset: off, lines }));
      made += createWalls(panels, story, "Walls Along Beams");
    }
    toast("Walls Along Beams", made ? `${made} wall${made === 1 ? "" : "s"} added under ${beams.length} beam${beams.length === 1 ? "" : "s"}` : "Nothing new — those walls already exist");
    return made;
  }

  /* ------------------------------------------------ plan editor hooks */
  const origClick = pe._toolClick;
  pe._toolClick = function (w, px, py) {
    if (this.tool === "wall") { try { wallClick(w); } catch (e) { console.error(e); } this.renderOverlay(); return; }
    if (this.tool === QUICK) {
      try {
        if (W.ptrDown) W.qdown = { w: [w.x, w.y], px, py };
        else quickPick([w.x, w.y]);               // keyboard Enter (js/ux_kbddraw.js)
      } catch (e) { console.error(e); }
      this.renderOverlay();
      return;
    }
    return origClick.call(this, w, px, py);
  };
  const origSetTool = pe.setTool;
  pe.setTool = function (t) {
    if (W.pts.length >= 2) finish(false); else if (W.pts.length) cancelChain();
    W.qdown = null;
    const r = origSetTool.call(this, t);
    if (t !== "wall" && t !== QUICK) svg.style.cursor = "";
    syncBox();
    return r;
  };

  svg.addEventListener("pointerdown", e => { W.ptrDown = e.button === 0 && S.tool === QUICK; W.shift = e.shiftKey; }, true);
  svg.addEventListener("pointermove", e => {
    if (!active()) return;
    const sh = e.shiftKey;
    if (W.qdown) { const [px, py] = pe._evPx(e); const q = pe.toWorld(px, py); W.qmove = [q.x, q.y]; }
    if (sh !== W.shift || W.qdown || S.tool === "wall") { W.shift = sh; pe.renderOverlay(); }
    updateRead();
  });
  const qUp = e => {
    W.ptrDown = false;
    if (!W.qdown) return;
    const d = W.qdown; W.qdown = null; W.qmove = null;
    const [px, py] = pe._evPx(e);
    if (Math.hypot(px - d.px, py - d.py) > 5) { const q = pe.toWorld(px, py); quickBox(d.w, [q.x, q.y]); }
    else quickPick(d.w);
    pe.renderOverlay();
  };
  svg.addEventListener("pointerup", qUp);
  svg.addEventListener("pointercancel", () => { W.ptrDown = false; W.qdown = null; W.qmove = null; pe.renderOverlay(); });
  // double-click finishes the chain instead of zoom-to-fit (capture on the wrapper)
  wrap.addEventListener("dblclick", e => {
    if (!active()) return;
    e.stopPropagation(); e.preventDefault();
    if (S.tool === "wall" && W.pts.length) finish(false);
  }, true);

  window.addEventListener("keydown", e => {
    if (!active() || modalOpen()) return;
    if (e.key === "Shift") { if (!W.shift) { W.shift = true; pe.renderOverlay(); updateRead(); } return; }
    if (e.key === "F8") { e.preventDefault(); e.stopImmediatePropagation(); st.ortho = !st.ortho; save(); syncBox(); pe.renderOverlay(); return; }
    const tag = (e.target && e.target.tagName || "").toLowerCase();
    if (["input", "select", "textarea"].includes(tag) || e.ctrlKey || e.metaKey || e.altKey) return;
    const stop = () => { e.preventDefault(); e.stopImmediatePropagation(); };
    if (S.tool === QUICK) {
      if (e.key === "Escape" && W.qdown) { stop(); W.qdown = null; W.qmove = null; pe.renderOverlay(); }
      return;
    }
    const n = W.pts.length;
    const kbdCur = sky.ux && sky.ux.kbdDraw && sky.ux.kbdDraw.cursor && sky.ux.kbdDraw.cursor();
    if (e.key === "Enter" && n && !kbdCur) { stop(); finish(false); return; }
    if (e.key === "Escape" && n) { stop(); if (n >= 2) finish(false); else cancelChain(); return; }
    if ((e.key === "Backspace" || e.key === "Delete") && n) { stop(); undoPoint(); return; }
    if (/^[0-9.@<,+-]$/.test(e.key) && (n || e.key !== "-")) {
      const inp = document.getElementById("wdTyped");
      if (inp && !box.classList.contains("hidden")) { stop(); if (st.collapsed) { st.collapsed = false; syncBox(); } inp.value = e.key; inp.focus(); inp.setSelectionRange(1, 1); updateTyped(); }
    }
  }, true);
  window.addEventListener("keyup", e => { if (e.key === "Shift" && W.shift) { W.shift = false; if (active()) { pe.renderOverlay(); updateRead(); } } }, true);
  window.addEventListener("blur", () => { W.shift = false; });

  /* ------------------------------------------------ overlay */
  const mk = (g, tag, a, text) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(a)) e.setAttribute(k, v);
    if (text != null) e.textContent = text;
    g.appendChild(e);
    return e;
  };
  function tag(g, x, y, text, color = COL.tagFg) {
    const w = 7 + text.length * 6.3;
    mk(g, "rect", { x: x - w / 2, y: y - 9, width: w, height: 18, rx: 4, fill: COL.tagBg, stroke: "rgba(53,181,229,0.5)", "stroke-width": 1, "data-wd": "tag" });
    mk(g, "text", { x, y: y + 4, fill: color, "font-size": 11, "font-weight": 650, "text-anchor": "middle", "font-family": "inherit", "data-wd": "len" }, text);
  }
  const bandPx = () => Math.max(4, Math.min(40, thickness() * pe.scale));
  function drawPanels(g, panels, opacity = 1) {
    const sw = bandPx();
    for (const [a, b] of panels) {
      const [x1, y1] = pe.toScreen(a[0], a[1]), [x2, y2] = pe.toScreen(b[0], b[1]);
      mk(g, "line", { x1, y1, x2, y2, stroke: COL.band, "stroke-width": sw, "stroke-linecap": "butt", opacity, "data-wd": "panel" });
    }
    for (const [a] of panels.slice(1)) { const [x, y] = pe.toScreen(a[0], a[1]); mk(g, "circle", { cx: x, cy: y, r: 2.6, fill: COL.split }); }
  }
  function drawOverlay(g) {
    const tgt = curTarget();
    const pts = W.pts.slice();
    const live = tgt && pts.length && !sameP(tgt, pts[pts.length - 1]) ? tgt : null;
    const all = live ? [...pts, live] : pts;
    const closing = W.closeHover && pts.length >= 3;
    if (all.length >= 2) {
      const panels = WG.wallPanels(closing ? pts : all, { closed: closing, offset: offsetSI(), lines: st.split ? splitLines() : null });
      drawPanels(g, panels);
      const d = (closing ? [...pts, pts[0]] : all).map((p, i) => { const [x, y] = pe.toScreen(p[0], p[1]); return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`; }).join(" ");
      mk(g, "path", { d, fill: "none", stroke: COL.line, "stroke-width": 1.4, "stroke-dasharray": "6 4", "data-wd": "ref" });
    }
    pts.forEach((p, i) => {
      const [x, y] = pe.toScreen(p[0], p[1]);
      if (i === 0 && pts.length >= 3) mk(g, "rect", { x: x - 5.5, y: y - 5.5, width: 11, height: 11, fill: closing ? COL.close : "none", stroke: closing ? COL.close : COL.vtx, "stroke-width": 1.6, "data-wd": "first" });
      else mk(g, "circle", { cx: x, cy: y, r: 3.6, fill: COL.tagBg, stroke: COL.vtx, "stroke-width": 1.6, "data-wd": "vtx" });
    });
    if (live) {
      const a = pts[pts.length - 1], info = WG.segInfo(a, live);
      const [x1, y1] = pe.toScreen(a[0], a[1]), [x2, y2] = pe.toScreen(live[0], live[1]);
      const L = Math.hypot(x2 - x1, y2 - y1) || 1, nx = -(y2 - y1) / L, ny = (x2 - x1) / L;
      const ortho = st.ortho !== W.shift;
      tag(g, (x1 + x2) / 2 + nx * 18, (y1 + y2) / 2 + ny * 18, `${fmtL(info.len)} ${lenU()} · ${info.ang.toFixed(1)}°${ortho ? " · ortho" : ""}`);
    }
    if (closing) { const [x, y] = pe.toScreen(pts[0][0], pts[0][1]); tag(g, x, y - 20, "close loop", COL.close); }
    // typed preview
    const inp = document.getElementById("wdTyped");
    if (inp && inp.value.trim() && pts.length) {
      const p = typedPoint(inp.value);
      if (p) { const [x, y] = pe.toScreen(p[0], p[1]); mk(g, "circle", { cx: x, cy: y, r: 6, fill: "none", stroke: COL.close, "stroke-width": 2, "data-wd": "typed" }); }
    }
    svg.style.cursor = closing ? "pointer" : "crosshair";
  }
  function quickOverlay(g) {
    const segs = quickSegs();
    const hl = (s, sw = 6) => {
      const [x1, y1] = pe.toScreen(s[0][0], s[0][1]), [x2, y2] = pe.toScreen(s[1][0], s[1][1]);
      mk(g, "line", { x1, y1, x2, y2, stroke: COL.hover, "stroke-width": sw, "stroke-linecap": "butt", opacity: 0.85, "data-wd": "qseg" });
    };
    if (W.qdown && W.qmove) {
      const [x1, y1] = pe.toScreen(W.qdown.w[0], W.qdown.w[1]), [x2, y2] = pe.toScreen(W.qmove[0], W.qmove[1]);
      mk(g, "rect", { x: Math.min(x1, x2), y: Math.min(y1, y2), width: Math.abs(x2 - x1), height: Math.abs(y2 - y1), fill: COL.box, stroke: COL.boxEdge, "stroke-width": 1.2, "stroke-dasharray": "5 3" });
      const ins = WG.segsInBox(segs, [W.qdown.w, W.qmove]);
      ins.forEach(s => hl(s, bandPx()));
      tag(g, x2 + 10 + 40, y2 + 16, `${ins.length} wall panel${ins.length === 1 ? "" : "s"}`);
      svg.style.cursor = "crosshair";
      return;
    }
    const mw = pe._mouseWorld;
    if (!mw) return;
    const i = WG.nearestSeg(segs, [mw.x, mw.y], Math.max(12 / pe.scale, 0.05));
    if (i >= 0) {
      hl(segs[i], bandPx());
      const [x, y] = pe.toScreen(mw.x, mw.y);
      tag(g, x + 52, y - 16, `+ wall ${fmtL(Math.hypot(segs[i][1][0] - segs[i][0][0], segs[i][1][1] - segs[i][0][1]))} ${lenU()}`);
    }
    svg.style.cursor = i >= 0 ? "copy" : "crosshair";
  }
  const prevOverlay = pe.w8Overlay;
  pe.w8Overlay = (g, ed) => {
    if (prevOverlay) prevOverlay(g, ed);
    if (!active()) return;
    try { if (S.tool === "wall") drawOverlay(g); else quickOverlay(g); } catch (e) { console.error(e); }
  };

  /* ------------------------------------------------ floating properties box */
  const h = (tag, a = {}, kids = []) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(a)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else if (k === "html") n.innerHTML = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v === true ? "" : v);
    }
    for (const c of [].concat(kids)) if (c != null && c !== false) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    return n;
  };
  const seg = (id, opts, get, set, label) => {
    const g = h("div", { class: "wd-seg", id, role: "radiogroup", "aria-label": label });
    for (const [v, t, tip] of opts) {
      const b = h("button", { type: "button", class: "wd-seg-btn", "data-v": v, title: tip || t, role: "radio" }, [t]);
      b.addEventListener("click", () => { set(v); syncBox(); pe.renderOverlay(); });
      g.appendChild(b);
    }
    g._sync = () => g.querySelectorAll(".wd-seg-btn").forEach(b => { const on = b.dataset.v === get(); b.classList.toggle("is-on", on); b.setAttribute("aria-checked", on ? "true" : "false"); });
    return g;
  };
  const lenInput = (id, key, tip) => {
    const i = h("input", { type: "text", class: "wd-num", id, inputmode: "decimal", title: tip, "aria-label": tip });
    i.addEventListener("change", () => {
      const v = wdU.parse("length", i.value);
      if (isFinite(v)) { st[key] = v; save(); }
      i.value = wdU.inputValue("length", st[key]);
      syncBox(); pe.renderOverlay();
    });
    i._sync = () => { if (document.activeElement !== i) i.value = wdU.inputValue("length", st[key]); };
    return i;
  };
  const row = (label, ctl, unit) => h("div", { class: "wd-row" }, [h("label", { class: "wd-lbl", for: ctl.id || null }, [label]), ctl, unit != null ? h("span", { class: "wd-unit", "data-wd-unit": "length" }, [unit]) : null]);

  const modeSeg = seg("wdMode", [["draw", "Polyline", "Draw Walls: click points (W)"], ["grid", "Grid lines", "Quick Draw Walls on grid lines: click a grid segment or drag a box"],
    ["beams", "Beams", "Quick Draw Walls under existing beams: click a beam or drag a box"]],
  () => (S.tool === QUICK ? st.src : "draw"),
  v => { if (v === "draw") sky.setTool("wall"); else { st.src = v; save(); sky.setTool(QUICK); } }, "Drawing mode");
  const secSel = h("select", { class: "wd-select", id: "wdSec", "aria-label": "Wall section" });
  secSel.addEventListener("change", () => { st.section = secSel.value; save(); syncBox(); pe.renderOverlay(); });
  const secBtn = h("button", { type: "button", class: "wd-mini", title: "Define wall sections (Section Manager)", text: "…" });
  secBtn.addEventListener("click", () => sky.openSectionMgr && sky.openSectionMgr());
  const tNote = h("div", { class: "wd-note", id: "wdThick" });
  const scopeSeg = seg("wdScope", [["one", "This story"], ["similar", "Similar", "Every story similar to this one (Story Data › Master / Similar To)"], ["all", "All stories"]],
    scope, v => setScope(v), "Stories");
  const alignSeg = seg("wdAlign", [["left", "Left", "Wall body left of the drawn line (line = right face)"], ["centre", "Centre", "Drawn line = wall centre line"],
    ["right", "Right", "Wall body right of the drawn line (line = left face)"]], () => st.align, v => { st.align = v; save(); }, "Alignment");
  const offIn = lenInput("wdOff", "offset", "Extra offset of the wall centre line, + = left of the drawing direction");
  const botIn = lenInput("wdBot", "botOff", "Bottom offset from the story below (+ = up)");
  const topIn = lenInput("wdTop", "topOff", "Top offset from the story level (− = down, e.g. stop under a beam)");
  const hNote = h("div", { class: "wd-note", id: "wdHeight" });
  const pierSel = h("select", { class: "wd-select", id: "wdPier", "aria-label": "Pier label" });
  pierSel.addEventListener("change", () => { st.pier = pierSel.value; save(); });
  const spanSel = h("select", { class: "wd-select", id: "wdSpan", "aria-label": "Spandrel label" });
  spanSel.addEventListener("change", () => { st.spandrel = spanSel.value; save(); });
  const chk = (id, key, label, tip) => {
    const i = h("input", { type: "checkbox", id });
    i.addEventListener("change", () => { st[key] = i.checked; save(); pe.renderOverlay(); syncBox(); });
    i._sync = () => { i.checked = !!st[key]; };
    return [i, h("label", { class: "wd-chk", title: tip }, [i, " " + label])];
  };
  const [splitChk, splitLbl] = chk("wdSplit", "split", "Split at grid lines", "Break each wall where a grid line crosses it (one panel per bay)");
  const [orthoChk, orthoLbl] = chk("wdOrtho", "ortho", "Ortho (F8, Shift)", "Lock segments to 0° / 90° — holding Shift inverts the lock");
  const typed = h("input", { type: "text", class: "wd-typed", id: "wdTyped", autocomplete: "off", spellcheck: "false",
    placeholder: "type: 6 · 6<90 · @3,0 · x,y", title: "Exact next point (display units): length along the cursor, length<angle, @dx,dy relative or x,y absolute — Enter places it", "aria-label": "Typed length or point" });
  function updateTyped() { pe.renderOverlay(); }
  typed.addEventListener("input", updateTyped);
  typed.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      e.preventDefault(); e.stopPropagation();
      const v = typed.value.trim();
      if (!v) { if (W.pts.length) finish(false); svg.focus({ preventScroll: true }); return; }
      const p = typedPoint(v);
      if (!p) { typed.classList.add("is-bad"); setTimeout(() => typed.classList.remove("is-bad"), 600); return; }
      typed.value = "";
      if (S.tool !== "wall") sky.setTool("wall");
      addPoint(p);
      pe.renderOverlay(); updateRead();
    } else if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation();
      typed.value = ""; svg.focus({ preventScroll: true }); pe.renderOverlay();
    } else if (e.key === "Backspace" && !typed.value && W.pts.length) { e.preventDefault(); undoPoint(); }
  });
  const read = h("div", { class: "wd-read", id: "wdRead", "aria-live": "polite" });
  const btn = (id, text, tip, fn) => { const b = h("button", { type: "button", class: "wd-btn", id, title: tip }, [text]); b.addEventListener("click", () => { fn(); pe.renderOverlay(); updateRead(); }); return b; };
  const bFinish = btn("wdFinish", "Finish", "Create the drawn walls (Enter / double-click)", () => finish(false));
  const bClose = btn("wdClose", "Close loop", "Join the last point to the first and create the walls", () => finish(true));
  const bUndo = btn("wdUndo", "Undo point", "Remove the last point (Backspace)", undoPoint);
  const bCancel = btn("wdCancel", "Discard", "Discard the points drawn so far", cancelChain);
  const bBeams = btn("wdBeamsSel", "Walls under selected beams", "Create walls under the beams that are selected now", wallsAlongSelectedBeams);
  const collapseBtn = h("button", { type: "button", class: "wd-collapse", id: "wdCollapse", title: "Collapse / expand", "aria-label": "Collapse properties" });
  collapseBtn.addEventListener("click", () => { st.collapsed = !st.collapsed; save(); syncBox(); });
  const floatBtn = h("button", { type: "button", class: "wd-collapse", id: "wdFloat", title: "Float the box over the plan / dock it in the side panel", "aria-label": "Float or dock" });
  floatBtn.addEventListener("click", () => { st.float = !st.float; save(); syncBox(); });
  const head = h("div", { class: "wd-head" }, [h("span", { class: "wd-ico", html: wdIcon("tool-wall") }), h("span", { class: "wd-title", id: "wdTitle", text: "Draw Walls" }), floatBtn, collapseBtn]);
  const drawOnly = h("div", { class: "wd-draw-only" }, [h("div", { class: "wd-row wd-row-typed" }, [typed]), h("div", { class: "wd-btns" }, [bFinish, bClose, bUndo, bCancel])]);
  const quickOnly = h("div", { class: "wd-quick-only" }, [h("div", { class: "wd-note", id: "wdQuickNote" }), h("div", { class: "wd-btns" }, [bBeams])]);
  const body = h("div", { class: "wd-body" }, [
    modeSeg,
    row("Section", h("div", { class: "wd-inline" }, [secSel, secBtn])), tNote,
    row("Stories", scopeSeg),
    row("Align", alignSeg),
    row("Offset", offIn, ""),
    h("div", { class: "wd-sub", text: "Height: story below → story" }),
    row("Bottom", botIn, ""), row("Top", topIn, ""), hNote,
    row("Pier", pierSel), row("Spandrel", spanSel),
    h("div", { class: "wd-checks" }, [splitLbl, orthoLbl]),
    drawOnly, quickOnly, read,
  ]);
  const box = h("div", { class: "wd-box hidden", id: "wdBox", role: "dialog", "aria-label": "Draw Walls properties" }, [head, body]);
  // Docked (default) at the top of the right-hand side panel so the plan stays
  // free; "float" puts it over the plan, draggable by its header.
  const planHost = wrap.parentElement || wrap;
  if (getComputedStyle(planHost).position === "static") planHost.style.position = "relative";
  let host = planHost;
  function placeBox() {
    const side = document.getElementById("sidebar");
    const dock = !st.float && side && side.offsetWidth > 150 && !side.classList.contains("collapsed");
    const want = dock ? side : planHost;
    if (box.parentElement !== want) { if (dock) want.insertBefore(box, want.firstChild); else want.appendChild(box); }
    host = want;
    box.classList.toggle("is-docked", !!dock);
    floatBtn.textContent = dock ? "⇱" : "⇲";
    if (dock) { box.style.left = ""; box.style.top = ""; box.style.right = ""; }
    else if (!st.pos) { box.style.left = ""; box.style.right = "12px"; box.style.top = "44px"; }   // under the story badge
    else if (st.pos) { box.style.right = "auto"; box.style.left = Math.min(st.pos[0], Math.max(0, host.clientWidth - 280)) + "px"; box.style.top = st.pos[1] + "px"; }
  }
  planHost.appendChild(box);

  // drag by the header
  head.addEventListener("pointerdown", e => {
    if (e.target.closest("button") || box.classList.contains("is-docked")) return;
    e.preventDefault();
    const r0 = box.getBoundingClientRect(), wr = host.getBoundingClientRect(), sx = e.clientX, sy = e.clientY;
    const mv = ev => {
      const x = Math.max(0, Math.min(wr.width - r0.width, r0.left - wr.left + ev.clientX - sx));
      const y = Math.max(0, Math.min(wr.height - 40, r0.top - wr.top + ev.clientY - sy));
      box.style.right = "auto"; box.style.left = x + "px"; box.style.top = y + "px"; st.pos = [x, y];
    };
    const upH = () => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", upH); save(); };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", upH);
  });

  function fillSelect(sel, opts, val) {
    const sig = JSON.stringify(opts);
    if (sel._sig !== sig) { sel.textContent = ""; for (const [v, t] of opts) sel.appendChild(h("option", { value: v }, [t])); sel._sig = sig; }
    sel.value = opts.some(o => o[0] === val) ? val : opts[0][0];
  }
  function updateRead() {
    if (box.classList.contains("hidden")) return;
    if (W.flash && Date.now() < W.flashUntil && !W.pts.length) { read.textContent = W.flash; return; }
    if (S.tool === QUICK) { read.textContent = `${quickSegs().length} ${st.src === "beams" ? "beams on this story" : "grid-line segments"} · click one or drag a box`; return; }
    const n = W.pts.length;
    if (!n) { read.textContent = "Click the start point — snaps to grid, joints, midpoints and wall ends."; return; }
    let tot = 0;
    for (let i = 1; i < n; i++) tot += Math.hypot(W.pts[i][0] - W.pts[i - 1][0], W.pts[i][1] - W.pts[i - 1][1]);
    const tgt = curTarget(), last = W.pts[n - 1];
    const seg = tgt && !sameP(tgt, last) ? WG.segInfo(last, tgt) : null;
    read.textContent = `${n - 1} segment${n - 1 === 1 ? "" : "s"} · ${fmtL(tot)} ${lenU()}` + (seg ? ` · next ${fmtL(seg.len)} ${lenU()} @ ${seg.ang.toFixed(1)}°` : "");
  }
  function syncBox() {
    const show = active() && !!S.model;
    box.classList.toggle("hidden", !show);
    if (!show) return;
    const quick = S.tool === QUICK;
    box.classList.toggle("is-collapsed", !!st.collapsed);
    collapseBtn.textContent = st.collapsed ? "▸" : "▾";
    placeBox();
    document.getElementById("wdTitle").textContent = quick ? "Quick Draw Walls" : "Draw Walls";
    const m = S.model;
    const secs = sectionNames().map(n => [n, `${n} · ${wdU.fmtU("length", +(m.shell_sections[n].thickness || 0), 3)}`]);
    if (!secs.length) secs.push(["", "SH200 (created on first wall)"]);
    fillSelect(secSel, secs, resolveSection());
    const t = thickness();
    tNote.textContent = t ? `thickness ${wdU.fmtU("length", t, 3)} · centre line offset ${wdU.fmtU("length", offsetSI(), 3)}` : "";
    const piers = [...new Set((m.shells || []).map(s => s.pier).filter(Boolean))].sort();
    fillSelect(pierSel, [["", "None"], ["__auto__", `Auto (new ${WG.nextPier(m.shells)})`], ...piers.map(p => [p, p])], st.pier);
    const spans = [...new Set([...(m.spandrel_labels || []), ...(m.shells || []).map(s => s.spandrel), ...(m.members || []).map(s => s.spandrel)].filter(Boolean))];
    fillSelect(spanSel, [["", "None"], ...spans.map(p => [p, p])], st.spandrel);
    const sto = (m.stories || []).find(s => s.name === S.story);
    if (sto) {
      const hh = sto.height + (+st.topOff || 0) - (+st.botOff || 0);
      const names = scopeNames(S.story);
      hNote.textContent = `wall height ${wdU.fmtU("length", hh, 2)}${hh <= 1e-6 ? " — offsets leave no height!" : ""} · ${names.length} stor${names.length === 1 ? "y" : "ies"}: ${names.length > 4 ? names[0] + " … " + names[names.length - 1] : names.join(", ")}`;
      hNote.classList.toggle("is-bad", hh <= 1e-6);
    }
    document.querySelectorAll("#wdBox [data-wd-unit]").forEach(u => { u.textContent = lenU(); });
    for (const c of [modeSeg, scopeSeg, alignSeg, offIn, botIn, topIn, splitChk, orthoChk]) c._sync && c._sync();
    if (sky.w8stories) scopeSeg.querySelector('[data-v="similar"]').disabled = false;
    else scopeSeg.querySelector('[data-v="similar"]').disabled = true;
    drawOnly.classList.toggle("hidden", quick);
    quickOnly.classList.toggle("hidden", !quick);
    bBeams.classList.toggle("hidden", st.src !== "beams");
    document.getElementById("wdQuickNote").textContent = st.src === "beams"
      ? "Click a beam to put a wall under it, or drag a box over several beams."
      : "Click a grid line between two intersections, or drag a box over grid lines.";
    const n = W.pts.length;
    bFinish.disabled = n < 2; bClose.disabled = n < 3; bUndo.disabled = !n; bCancel.disabled = !n;
    orthoLbl.classList.toggle("is-on", st.ortho !== W.shift);
    updateRead();
    syncTb();
  }

  /* ------------------------------------------------ status-bar hint (js/ux_status.js hook) */
  sky.wallDrawHint = () => {
    if (!active()) return "";
    const sc = { one: "this story", similar: "similar stories", all: "all stories" }[scope()] || "this story";
    if (S.tool === QUICK) return st.src === "beams"
      ? `Plan · Quick Draw Walls (beams): click a beam to put a wall under it · drag a box for many · ${sc} · Esc ends`
      : `Plan · Quick Draw Walls: click a grid line between two intersections · drag a box over grid lines for many · ${sc}`;
    const n = W.pts.length;
    if (!n) return `Plan · Draw Walls: click the start point (snaps to grid, joints, midpoints, wall ends) · ${sc}`;
    if (n === 1) return "Plan · Draw Walls: click the next point, or type a length (6, 6<90, @3,0) + Enter · Shift = ortho · Esc cancels";
    return `Plan · Draw Walls: ${n - 1} segment${n === 2 ? "" : "s"} — next point · double-click / Enter finishes · Backspace undoes a point · click the first point to close · Esc finishes`;
  };

  /* ------------------------------------------------ menus + toolbar */
  const startDraw = () => { if (S.mode !== "model" && sky.setMode) sky.setMode("model"); if (S.view === "elev" && sky.setView) sky.setView("plan"); sky.setTool("wall"); };
  const startQuick = (src = "grid") => { if (S.mode !== "model" && sky.setMode) sky.setMode("model"); if (S.view === "elev" && sky.setView) sky.setView("plan"); st.src = src; save(); sky.setTool(QUICK); syncBox(); };
  if (sky.menu) {
    sky.menu.add("Draw/Floors & Walls", { label: "Quick Draw Walls", act: "draw-wd-quick", hint: "grid lines · click or box", fn: () => startQuick("grid"), before: "draw-polyfloor" });
    sky.menu.add("Draw/Floors & Walls", { label: "Walls Along Beams", act: "draw-wd-beams", hint: "click / box beams · or the selection", fn: () => { if ((S.selection || []).some(r => r.type === "member")) { const n = wallsAlongSelectedBeams(); if (n) return; } startQuick("beams"); }, before: "draw-polyfloor" });
    sky.menu.add("Draw/Floors & Walls", { sep: true, before: "draw-polyfloor" });
    sky.menu.add("Draw/Quick Draw", { label: "Walls on Grid Lines", act: "draw-wd-qwall", hint: "click a grid segment · drag a box", fn: () => startQuick("grid") });
  }
  let tbBtn = null, palBtn = null;
  function syncTb() {
    const on = S.mode === "model" && S.tool === QUICK;
    for (const b of [tbBtn, palBtn]) if (b) { b.classList.toggle("is-active", on); b.setAttribute("aria-pressed", on ? "true" : "false"); }
  }
  function addButtons() {
    const after = document.querySelector('.ux-qat-btn[data-qat="wall"]');
    if (after && !document.querySelector('[data-qat="wdquick"]')) {
      tbBtn = h("button", { type: "button", class: "ux-qat-btn", "data-qat": "wdquick", title: "Quick draw walls — click a grid line or drag a box over grid lines\nDraw › Floors & Walls › Quick Draw Walls",
        "aria-label": "Quick draw walls", "aria-pressed": "false", html: wdIcon("tool-wdquick", "ux-qat-ico") });
      tbBtn.addEventListener("click", () => startQuick("grid"));
      after.after(tbBtn);
      const wb = document.querySelector('.ux-qat-btn[data-qat="wall"]');
      if (wb) wb.title = "Draw walls (W) — click points, double-click / Enter finishes; floating box sets section, alignment, offsets, stories";
    }
    const pal = document.getElementById("etabsPalette"), pw = pal && pal.querySelector('.etabs-tool[data-tool="wall"]');
    if (pw && !pal.querySelector('[data-tool="' + QUICK + '"]')) {
      palBtn = h("button", { class: "etabs-tool", "data-tool": QUICK, title: "Quick draw walls (grid lines)", "aria-label": "Quick draw walls", html: wdIcon("tool-wdquick") });
      palBtn.addEventListener("click", () => startQuick("grid"));
      pw.after(palBtn);
    }
    syncTb();
  }
  setTimeout(addButtons, 0);

  // mode / view / story / model changes
  const obs = new MutationObserver(() => syncBox());
  for (const id of ["drawMain", "planSvg", "elevSvg", "sidebar"]) { const n = document.getElementById(id); if (n) obs.observe(n, { attributes: true, attributeFilter: ["class"] }); }
  for (const ev of ["sky:model-changed", "sky:units-changed", "sky:model-replaced", "sky:model-synced"]) document.addEventListener(ev, () => { if (ev === "sky:model-replaced") cancelChain(); syncBox(); });
  const storySel = document.getElementById("storySelect");
  if (storySel) storySel.addEventListener("change", () => setTimeout(syncBox, 0));
  document.addEventListener("click", e => { if (e.target.closest && e.target.closest("#applyToggle")) setTimeout(syncBox, 0); }, true);

  sky.wallDraw = {
    start: startDraw, startQuick, finish, cancel: cancelChain, undo: undoPoint, addPoint: p => addPoint(p),
    typed: t => { const p = typedPoint(t); if (p) addPoint(p); return p; },
    quickPick: p => quickPick(p), quickBox: (a, b) => quickBox(a, b), alongSelectedBeams: wallsAlongSelectedBeams,
    points: () => W.pts.map(p => p.slice()), settings: () => ({ ...st }),
    set: (k, v) => { if (k in DEFAULTS) { st[k] = v; save(); syncBox(); pe.renderOverlay(); } },
    hint: () => sky.wallDrawHint(), geom: WG, sync: syncBox,
  };
  syncBox();
}
