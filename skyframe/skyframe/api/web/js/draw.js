/* SkyFrame plan editor — 2D story-plan SVG editor with grid snapping,
   pan/zoom, draw tools (column/beam/wall/slab/link), select & erase.
   Owns only view + interaction; model mutations happen in app.js via
   the onDraw / onErase / onSelect callbacks. */

import { zigzagPoints } from "./elev.js";
import { springKey, anyThermalMember } from "./modeledit.js";

const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
};

const C = {
  gridLine: "rgba(120, 140, 165, 0.22)",
  gridLabel: "rgba(140, 160, 185, 0.75)",
  column: "#5f8fc9",
  beam: "#77879b",
  brace: "#c98500",                       // amber — matches the 3D viewer
  braceRubber: "rgba(201, 133, 0, 0.9)",
  wall: "rgba(95, 143, 201, 0.85)",
  slabFill: "rgba(154, 167, 180, 0.14)",
  slabEdge: "rgba(154, 167, 180, 0.45)",
  link: "#34c384",                        // green — link/spring glyph (v0.5)
  linkRubber: "rgba(52, 195, 132, 0.9)",
  spring: "#2fbf74",                      // green — grounded spring support (v0.8)
  thermal: "#e5a50a",                     // amber — ΔT thermal badge (v0.8)
  rigid: "rgba(120, 170, 220, 0.55)",     // pale blue — rigid end zone (v0.9)
  sel: "#35b5e5",
  snap: "#35b5e5",
  rubber: "rgba(53, 181, 229, 0.9)",
  box: "rgba(53, 181, 229, 0.10)",
  boxEdge: "rgba(53, 181, 229, 0.55)",
  hoverErase: "#e66767",
};

const SNAP_PX = 16;       // px threshold for slab corner-vs-cell decision
const HIT_PX = 9;         // px hit-test tolerance

export class PlanEditor {
  /**
   * opts: { getModel, getStory, getSelection (Set of "type:uid"),
   *         onDraw(tool, payload), onErase(ref), onSelect(refs, additive),
   *         onReadout(text) }
   */
  constructor(svg, opts) {
    this.svg = svg;
    this.opts = opts;
    this.tool = "select";
    this.pending = null;         // first point of a two-click tool {x,y}
    this.hoverSnap = null;
    this.hoverRef = null;        // element under cursor (select / erase)
    this.box = null;             // drag box {x0,y0,x1,y1} world
    this._pan = null;
    this._downSel = null;

    // camera: world → screen. y is flipped (plan north up).
    this.scale = 40;             // px per m
    this.cx = 9; this.cy = 6;    // world center
    this.w = 800; this.h = 600;
    this._fitted = false;

    // layers
    this.gWorld = el("g");                    // world-space (grid + elements)
    this.gGrid = el("g");
    this.gElems = el("g");
    this.gLabels = el("g");                   // screen-space grid labels
    this.gOverlay = el("g");                  // screen-space interaction overlay
    this.gWorld.appendChild(this.gGrid);
    this.gWorld.appendChild(this.gElems);
    svg.appendChild(this.gWorld);
    svg.appendChild(this.gLabels);
    svg.appendChild(this.gOverlay);

    this._ro = new ResizeObserver(() => this._resize());
    this._ro.observe(svg.parentElement || svg);
    this._bind();
    this._resize();
  }

  /* ------------------------------------------------ coordinates */
  toScreen(x, y) {
    return [this.w / 2 + (x - this.cx) * this.scale,
            this.h / 2 - (y - this.cy) * this.scale];
  }
  toWorld(px, py) {
    return { x: this.cx + (px - this.w / 2) / this.scale,
             y: this.cy - (py - this.h / 2) / this.scale };
  }
  _evPx(e) {
    const r = this.svg.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  /* ------------------------------------------------ public API */
  setTool(t) {
    this.tool = t;
    this.pending = null;
    this.box = null;
    this._syncCursor();
    this.renderOverlay();
  }

  cancel() {
    this.pending = null;
    this.box = null;
    this.renderOverlay();
  }

  fit() {
    const m = this.opts.getModel();
    const g = m && m.grid;
    if (!g || !g.x_lines.length) return;
    const x0 = g.x_lines[0], x1 = g.x_lines[g.x_lines.length - 1];
    const y0 = g.y_lines[0], y1 = g.y_lines[g.y_lines.length - 1];
    this.cx = (x0 + x1) / 2; this.cy = (y0 + y1) / 2;
    const spanX = Math.max(x1 - x0, 2), spanY = Math.max(y1 - y0, 2);
    this.scale = Math.min((this.w - 130) / spanX, (this.h - 110) / spanY);
    this.scale = Math.max(6, Math.min(this.scale, 220));
    this.refresh();
  }

  /** Full re-render (model / story / selection changed). */
  refresh() {
    this._applyTransform();
    this.renderStatic();
    this.renderOverlay();
  }

  destroy() { this._ro.disconnect(); }

  /* ------------------------------------------------ view transform */
  _resize() {
    const p = this.svg.parentElement || this.svg;
    const w = p.clientWidth, h = p.clientHeight;
    if (!w || !h) return;
    this.w = w; this.h = h;
    this.svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    if (!this._fitted) { this._fitted = true; this.fit(); }
    else this.refresh();
  }

  _applyTransform() {
    this.gWorld.setAttribute("transform",
      `translate(${this.w / 2},${this.h / 2}) scale(${this.scale},${-this.scale}) translate(${-this.cx},${-this.cy})`);
    this._renderLabels();
  }

  /* ------------------------------------------------ static render */
  renderStatic() {
    const m = this.opts.getModel();
    this.gGrid.textContent = "";
    this.gElems.textContent = "";
    if (!m || !m.grid) return;
    const g = m.grid;
    const x0 = g.x_lines[0], x1 = g.x_lines[g.x_lines.length - 1];
    const y0 = g.y_lines[0], y1 = g.y_lines[g.y_lines.length - 1];
    const mX = Math.max((x1 - x0) * 0.06, 0.8), mY = Math.max((y1 - y0) * 0.06, 0.8);

    for (const x of g.x_lines)
      this.gGrid.appendChild(el("line", {
        x1: x, y1: y0 - mY, x2: x, y2: y1 + mY,
        stroke: C.gridLine, "stroke-width": 1, "vector-effect": "non-scaling-stroke",
      }));
    for (const y of g.y_lines)
      this.gGrid.appendChild(el("line", {
        x1: x0 - mX, y1: y, x2: x1 + mX, y2: y,
        stroke: C.gridLine, "stroke-width": 1, "vector-effect": "non-scaling-stroke",
      }));

    // elements of the current story — draw order: slabs, walls, beams, columns
    const story = this.opts.getStory();
    const sel = this.opts.getSelection();
    const isSel = (type, uid) => sel.has(`${type}:${uid}`);

    for (const s of (m.shells || [])) {
      if (s.story !== story || s.kind !== "slab") continue;
      const seld = isSel("shell", s.uid);
      // v0.5: slab openings render as even-odd cutouts in plan
      let d = s.corners.map((c, i) => `${i ? "L" : "M"}${c[0]},${c[1]}`).join(" ") + " Z";
      for (const o of (s.openings || [])) {
        const q = openingPlanQuad(s.corners, o);
        d += " " + q.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join(" ") + " Z";
      }
      this.gElems.appendChild(el("path", {
        d, "fill-rule": "evenodd",
        fill: seld ? "rgba(53,181,229,0.18)" : C.slabFill,
        stroke: seld ? C.sel : C.slabEdge,
        "stroke-width": seld ? 2 : 1.25, "vector-effect": "non-scaling-stroke",
        "data-ref": `shell:${s.uid}`,
      }));
    }
    for (const s of (m.shells || [])) {
      if (s.story !== story || s.kind !== "wall") continue;
      const seld = isSel("shell", s.uid);
      const [a, b] = [s.corners[0], s.corners[1]];
      this.gElems.appendChild(el("line", {
        x1: a[0], y1: a[1], x2: b[0], y2: b[1],
        stroke: seld ? C.sel : C.wall, "stroke-width": 0.24,
        "stroke-linecap": "butt", "data-ref": `shell:${s.uid}`,
      }));
    }
    for (const mm of m.members) {
      if (mm.story !== story || mm.kind === "column" || mm.kind === "brace") continue;
      const seld = isSel("member", mm.uid);
      this.gElems.appendChild(el("line", {
        x1: mm.pi[0], y1: mm.pi[1], x2: mm.pj[0], y2: mm.pj[1],
        stroke: seld ? C.sel : C.beam, "stroke-width": seld ? 3 : 2,
        "stroke-linecap": "round", "vector-effect": "non-scaling-stroke",
        "data-ref": `member:${mm.uid}`,
      }));
    }
    // braces — dashed amber diagonals ON TOP of beams (they often share an
    // edge with a beam in plan; the dashes must stay visible)
    for (const mm of m.members) {
      if (mm.story !== story || mm.kind !== "brace") continue;
      const seld = isSel("member", mm.uid);
      this.gElems.appendChild(el("line", {
        x1: mm.pi[0], y1: mm.pi[1], x2: mm.pj[0], y2: mm.pj[1],
        stroke: seld ? C.sel : C.brace, "stroke-width": seld ? 3 : 2,
        "stroke-dasharray": "7 5",
        "stroke-linecap": "round", "vector-effect": "non-scaling-stroke",
        "data-ref": `member:${mm.uid}`,
      }));
    }
    for (const mm of m.members) {
      if (mm.story !== story || mm.kind !== "column") continue;
      const seld = isSel("member", mm.uid);
      const sec = m.sections[mm.section];
      const side = Math.max(sec ? (sec.b || 0.35) : 0.35, 0.3);
      this.gElems.appendChild(el("rect", {
        x: mm.pi[0] - side / 2, y: mm.pi[1] - side / 2, width: side, height: side,
        fill: seld ? C.sel : C.column,
        stroke: seld ? "#bfeaff" : "rgba(232,237,243,0.35)",
        "stroke-width": 1, "vector-effect": "non-scaling-stroke",
        "data-ref": `member:${mm.uid}`,
      }));
    }
    // v0.9: rigid end zones — thicker hatched stubs at member ends where
    // rigid_i / rigid_j > 0 (subtle, drawn over beams/braces; non-interactive)
    for (const mm of m.members) {
      if (mm.story !== story || mm.kind === "column") continue;
      const ri = mm.rigid_i || 0, rj = mm.rigid_j || 0;
      if (ri <= 0 && rj <= 0) continue;
      const dx = mm.pj[0] - mm.pi[0], dy = mm.pj[1] - mm.pi[1];
      const L = Math.hypot(dx, dy);
      if (L < 1e-6) continue;
      const ux = dx / L, uy = dy / L;
      const stub = (x, y, len, sign) => {
        const l = Math.min(len, L * 0.49);
        this.gElems.appendChild(el("line", {
          x1: x, y1: y, x2: x + sign * ux * l, y2: y + sign * uy * l,
          stroke: C.rigid, "stroke-width": 6, "stroke-linecap": "butt",
          "stroke-dasharray": "1.5 2.5", "vector-effect": "non-scaling-stroke",
          "pointer-events": "none", "data-ref": `rigid:${mm.uid}`,
        }));
      };
      if (ri > 0) stub(mm.pi[0], mm.pi[1], ri, +1);
      if (rj > 0) stub(mm.pj[0], mm.pj[1], rj, -1);
    }

    // v0.5: links whose endpoints lie within the current story's z-span —
    // green zigzag spring glyphs on top of everything
    for (const lk of this._storyLinks()) {
      const seld = isSel("link", lk.uid);
      this.gElems.appendChild(el("polyline", {
        points: zigzagPoints(lk.pi[0], lk.pi[1], lk.pj[0], lk.pj[1], 0.16),
        fill: "none",
        stroke: seld ? C.sel : C.link, "stroke-width": seld ? 2.5 : 1.8,
        "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke",
        "data-ref": `link:${lk.uid}`,
      }));
    }

    // v0.8: spring supports — grounded green coil glyphs at their base points
    // (base anchors show on every story plan). One <path> so hover/erase clone.
    for (const sp of (m.spring_supports || [])) {
      const seld = isSel("spring", springKey(sp.point));
      this.gElems.appendChild(el("path", {
        d: springGlyphPlan(sp.point[0], sp.point[1], 0.34),
        fill: "none", stroke: seld ? C.sel : C.spring,
        "stroke-width": seld ? 2.6 : 1.8,
        "stroke-linejoin": "round", "stroke-linecap": "round",
        "vector-effect": "non-scaling-stroke",
        "data-ref": `spring:${springKey(sp.point)}`,
      }));
    }
  }

  /** Links visible on the current story plan (both endpoint z within span). */
  _storyLinks() {
    const m = this.opts.getModel();
    if (!m || !m.links || !m.links.length) return [];
    const st = m.stories.find(s => s.name === this.opts.getStory());
    if (!st) return [];
    const zb = st.elevation - st.height - 1e-6, zt = st.elevation + 1e-6;
    return m.links.filter(l =>
      l.pi[2] > zb && l.pi[2] <= zt && l.pj[2] > zb && l.pj[2] <= zt);
  }

  _renderLabels() {
    const m = this.opts.getModel();
    this.gLabels.textContent = "";
    if (!m || !m.grid) return;
    const g = m.grid;
    const x0 = g.x_lines[0], x1 = g.x_lines[g.x_lines.length - 1];
    const y0 = g.y_lines[0], y1 = g.y_lines[g.y_lines.length - 1];
    const mX = Math.max((x1 - x0) * 0.06, 0.8), mY = Math.max((y1 - y0) * 0.06, 0.8);
    const mk = (x, y, text) => {
      const [px, py] = this.toScreen(x, y);
      const t = el("text", {
        x: px, y: py, fill: C.gridLabel, "font-size": 11, "font-weight": 600,
        "text-anchor": "middle", "dominant-baseline": "middle",
        "font-family": "inherit",
      });
      t.textContent = text;
      this.gLabels.appendChild(t);
    };
    g.x_lines.forEach((x, i) => mk(x, y1 + mY + 10 / this.scale, g.x_labels[i]));
    g.y_lines.forEach((y, i) => mk(x0 - mX - 10 / this.scale, y, g.y_labels[i]));

    // v0.8: ΔT badges on current-story members carrying a thermal load
    const story = this.opts.getStory();
    for (const mm of m.members) {
      if (mm.story !== story || !anyThermalMember(m, mm.uid)) continue;
      const [px, py] = this.toScreen((mm.pi[0] + mm.pj[0]) / 2, (mm.pi[1] + mm.pj[1]) / 2);
      const bg = el("circle", {
        cx: px, cy: py, r: 8.5, fill: "rgba(229,165,10,0.16)",
        stroke: C.thermal, "stroke-width": 1,
      });
      const t = el("text", {
        x: px, y: py + 0.5, fill: C.thermal, "font-size": 9, "font-weight": 700,
        "text-anchor": "middle", "dominant-baseline": "central", "font-family": "inherit",
      });
      t.textContent = "ΔT";
      this.gLabels.appendChild(bg);
      this.gLabels.appendChild(t);
    }
  }

  /* ------------------------------------------------ snapping */
  /** Snap a world point to grid intersections and 0.5 m points on grid
      lines. Returns {x, y, dPx} (dPx = screen distance to the raw point). */
  snap(w) {
    const m = this.opts.getModel();
    const g = m && m.grid;
    if (!g) return { x: w.x, y: w.y, dPx: 0 };
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    const nearVal = (arr, v) => arr.reduce((b, a) => Math.abs(a - v) < Math.abs(b - v) ? a : b);
    const gx = nearVal(g.x_lines, w.x);
    const gy = nearVal(g.y_lines, w.y);
    const x0 = g.x_lines[0], x1 = g.x_lines[g.x_lines.length - 1];
    const y0 = g.y_lines[0], y1 = g.y_lines[g.y_lines.length - 1];
    const cands = [
      { x: gx, y: gy },                                              // intersection
      { x: gx, y: clamp(Math.round(w.y * 2) / 2, y0, y1) },          // on vertical line
      { x: clamp(Math.round(w.x * 2) / 2, x0, x1), y: gy },          // on horizontal line
    ];
    let best = null, bd = Infinity;
    for (const c of cands) {
      const d = Math.hypot(c.x - w.x, c.y - w.y);
      if (d < bd) { bd = d; best = c; }
    }
    return { x: best.x, y: best.y, dPx: bd * this.scale };
  }

  /** Grid cell containing a world point, or null. */
  cellAt(w) {
    const g = this.opts.getModel()?.grid;
    if (!g) return null;
    const xi = g.x_lines.findIndex((x, i) => i < g.x_lines.length - 1 &&
      w.x >= x && w.x <= g.x_lines[i + 1]);
    const yi = g.y_lines.findIndex((y, i) => i < g.y_lines.length - 1 &&
      w.y >= y && w.y <= g.y_lines[i + 1]);
    if (xi < 0 || yi < 0) return null;
    return { x0: g.x_lines[xi], x1: g.x_lines[xi + 1], y0: g.y_lines[yi], y1: g.y_lines[yi + 1] };
  }

  /* ------------------------------------------------ hit testing */
  /** All elements under a point, in priority order:
      columns, beams, walls, slabs. */
  _candidates(w) {
    const m = this.opts.getModel();
    if (!m) return [];
    const story = this.opts.getStory();
    const tol = HIT_PX / this.scale;
    const out = [];
    // v0.8: spring supports first (small deliberate anchors, base level)
    for (const sp of (m.spring_supports || [])) {
      if (Math.hypot(w.x - sp.point[0], w.y - sp.point[1]) <= Math.max(tol, 0.4))
        out.push({ type: "spring", uid: springKey(sp.point) });
    }
    for (const mm of m.members) {
      if (mm.story !== story || mm.kind !== "column") continue;
      const sec = m.sections[mm.section];
      const half = Math.max(sec ? (sec.b || 0.35) : 0.35, 0.3) / 2 + tol * 0.5;
      if (Math.abs(w.x - mm.pi[0]) <= half && Math.abs(w.y - mm.pi[1]) <= half)
        out.push({ type: "member", uid: mm.uid });
    }
    for (const mm of m.members) {
      if (mm.story !== story || mm.kind === "column") continue;
      if (distToSeg(w.x, w.y, mm.pi[0], mm.pi[1], mm.pj[0], mm.pj[1]) <= tol)
        out.push({ type: "member", uid: mm.uid });
    }
    for (const lk of this._storyLinks()) {
      if (distToSeg(w.x, w.y, lk.pi[0], lk.pi[1], lk.pj[0], lk.pj[1]) <= Math.max(tol, 0.2))
        out.push({ type: "link", uid: lk.uid });
    }
    for (const s of (m.shells || [])) {
      if (s.story !== story || s.kind !== "wall") continue;
      const [a, b] = [s.corners[0], s.corners[1]];
      if (distToSeg(w.x, w.y, a[0], a[1], b[0], b[1]) <= Math.max(tol, 0.15))
        out.push({ type: "shell", uid: s.uid });
    }
    for (const s of (m.shells || [])) {
      if (s.story !== story || s.kind !== "slab") continue;
      if (pointInPoly(w.x, w.y, s.corners)) out.push({ type: "shell", uid: s.uid });
    }
    return out;
  }

  hitTest(w) {
    const c = this._candidates(w);
    return c.length ? c[0] : null;
  }

  /** Click-select: repeated clicks on coincident elements cycle through
      them (e.g. a wall lying under a beam). */
  hitTestCycle(w) {
    const cands = this._candidates(w);
    if (!cands.length) return null;
    const sel = this.opts.getSelection();
    if (sel.size === 1) {
      const cur = [...sel][0];
      const i = cands.findIndex(c => `${c.type}:${c.uid}` === cur);
      if (i >= 0) return cands[(i + 1) % cands.length];
    }
    return cands[0];
  }

  _boxRefs(b) {
    const m = this.opts.getModel();
    const story = this.opts.getStory();
    const [xa, xb] = [Math.min(b.x0, b.x1), Math.max(b.x0, b.x1)];
    const [ya, yb] = [Math.min(b.y0, b.y1), Math.max(b.y0, b.y1)];
    const inBox = (x, y) => x >= xa && x <= xb && y >= ya && y <= yb;
    const refs = [];
    for (const mm of m.members) {
      if (mm.story !== story) continue;
      if (mm.kind === "column" ? inBox(mm.pi[0], mm.pi[1])
        : (inBox(mm.pi[0], mm.pi[1]) || inBox(mm.pj[0], mm.pj[1]) ||
           inBox((mm.pi[0] + mm.pj[0]) / 2, (mm.pi[1] + mm.pj[1]) / 2)))
        refs.push({ type: "member", uid: mm.uid });
    }
    for (const s of (m.shells || [])) {
      if (s.story !== story) continue;
      const cx = s.corners.reduce((a, c) => a + c[0], 0) / 4;
      const cy = s.corners.reduce((a, c) => a + c[1], 0) / 4;
      if (s.corners.some(c => inBox(c[0], c[1])) || inBox(cx, cy))
        refs.push({ type: "shell", uid: s.uid });
    }
    for (const lk of this._storyLinks()) {
      if (inBox(lk.pi[0], lk.pi[1]) || inBox(lk.pj[0], lk.pj[1]) ||
          inBox((lk.pi[0] + lk.pj[0]) / 2, (lk.pi[1] + lk.pj[1]) / 2))
        refs.push({ type: "link", uid: lk.uid });
    }
    for (const sp of (m.spring_supports || [])) {
      if (inBox(sp.point[0], sp.point[1]))
        refs.push({ type: "spring", uid: springKey(sp.point) });
    }
    return refs;
  }

  /* ------------------------------------------------ interaction */
  _bind() {
    const svg = this.svg;
    svg.addEventListener("contextmenu", e => e.preventDefault());

    svg.addEventListener("pointerdown", e => {
      svg.setPointerCapture(e.pointerId);
      const [px, py] = this._evPx(e);
      if (e.button === 1 || e.button === 2) {
        this._pan = { px, py };
        svg.classList.add("panning");
        return;
      }
      if (e.button !== 0) return;
      const w = this.toWorld(px, py);
      if (this.tool === "select") {
        this._downSel = { px, py, w, additive: e.shiftKey, boxing: false };
      } else {
        this._toolClick(w, px, py);
      }
    });

    svg.addEventListener("pointermove", e => {
      const [px, py] = this._evPx(e);
      if (this._pan) {
        this.cx -= (px - this._pan.px) / this.scale;
        this.cy += (py - this._pan.py) / this.scale;
        this._pan = { px, py };
        this._applyTransform();
        this.renderOverlay();
        return;
      }
      const w = this.toWorld(px, py);
      if (this._downSel) {
        if (!this._downSel.boxing &&
            Math.hypot(px - this._downSel.px, py - this._downSel.py) > 4)
          this._downSel.boxing = true;
        if (this._downSel.boxing)
          this.box = { x0: this._downSel.w.x, y0: this._downSel.w.y, x1: w.x, y1: w.y };
      }
      this.hoverSnap = (this.tool !== "select" && this.tool !== "erase") ? this.snap(w) : null;
      this.hoverRef = (this.tool === "select" || this.tool === "erase") ? this.hitTest(w) : null;
      this._mouseWorld = w;
      this.renderOverlay();
      this._readout(w);
    });

    const up = e => {
      if (this._pan) { this._pan = null; svg.classList.remove("panning"); return; }
      if (this._downSel) {
        const ds = this._downSel;
        this._downSel = null;
        if (ds.boxing && this.box) {
          const refs = this._boxRefs(this.box);
          this.box = null;
          this.opts.onSelect(refs, ds.additive);
        } else {
          const ref = ds.additive ? this.hitTest(ds.w) : this.hitTestCycle(ds.w);
          this.opts.onSelect(ref ? [ref] : [], ds.additive);
        }
        this.renderOverlay();
      }
    };
    svg.addEventListener("pointerup", up);
    svg.addEventListener("pointercancel", up);
    svg.addEventListener("pointerleave", () => {
      this.hoverSnap = null; this.hoverRef = null; this._mouseWorld = null;
      this.renderOverlay();
      this.opts.onReadout && this.opts.onReadout("");
    });

    svg.addEventListener("wheel", e => {
      e.preventDefault();
      const [px, py] = this._evPx(e);
      const before = this.toWorld(px, py);
      const f = Math.exp(-e.deltaY * 0.0014);
      this.scale = Math.max(5, Math.min(300, this.scale * f));
      const after = this.toWorld(px, py);
      this.cx += before.x - after.x;
      this.cy += before.y - after.y;
      this._applyTransform();
      this.renderOverlay();
    }, { passive: false });

    svg.addEventListener("dblclick", () => this.fit());
  }

  _toolClick(w, px, py) {
    const snap = this.snap(w);
    const pt = { x: snap.x, y: snap.y };
    switch (this.tool) {
      case "column":
        this.opts.onDraw("column", pt);
        break;
      case "spring":
        this.opts.onDraw("spring", pt);   // base-level spring at the snapped point
        break;
      case "beam":
      case "wall":
      case "brace":
      case "link":
        if (!this.pending) this.pending = pt;
        else if (Math.hypot(pt.x - this.pending.x, pt.y - this.pending.y) > 1e-9) {
          this.opts.onDraw(this.tool, { p1: this.pending, p2: pt });
          this.pending = this.tool === "link" ? null : pt;   // chain, Esc stops
        }
        break;
      case "slab": {
        if (snap.dPx > SNAP_PX && !this.pending) {
          const cell = this.cellAt(w);
          if (cell) this.opts.onDraw("slab", cell);
          break;
        }
        if (!this.pending) this.pending = pt;
        else {
          if (Math.abs(pt.x - this.pending.x) > 1e-9 && Math.abs(pt.y - this.pending.y) > 1e-9) {
            this.opts.onDraw("slab", {
              x0: this.pending.x, y0: this.pending.y, x1: pt.x, y1: pt.y,
            });
            this.pending = null;
          }
        }
        break;
      }
      case "erase": {
        const ref = this.hitTest(w);
        if (ref) this.opts.onErase(ref);
        break;
      }
    }
    this.renderOverlay();
  }

  _readout(w) {
    if (!this.opts.onReadout) return;
    let txt = "";
    if (this.hoverSnap) txt = `${this.hoverSnap.x.toFixed(2)}, ${this.hoverSnap.y.toFixed(2)} m`;
    else if (w) txt = `${w.x.toFixed(2)}, ${w.y.toFixed(2)} m`;
    if (this.pending) txt = `${this.pending.x.toFixed(2)}, ${this.pending.y.toFixed(2)} → ${txt}`;
    this.opts.onReadout(txt);
  }

  _syncCursor() {
    this.svg.dataset.tool = this.tool;
  }

  /* ------------------------------------------------ overlay render */
  renderOverlay() {
    const g = this.gOverlay;
    g.textContent = "";

    // hover highlight (select / erase)
    if (this.hoverRef) {
      const node = this.gElems.querySelector(
        `[data-ref="${this.hoverRef.type}:${this.hoverRef.uid}"]`);
      if (node) {
        const clone = node.cloneNode(false);
        clone.removeAttribute("data-ref");
        clone.setAttribute("fill", "none");
        clone.setAttribute("stroke", this.tool === "erase" ? C.hoverErase : C.sel);
        clone.setAttribute("stroke-width", node.tagName === "line" &&
          !node.getAttribute("vector-effect") ? 0.3 : 3.5);
        if (node.getAttribute("vector-effect"))
          clone.setAttribute("vector-effect", "non-scaling-stroke");
        clone.setAttribute("opacity", 0.85);
        const wrap = el("g", {
          transform: this.gWorld.getAttribute("transform"),
        });
        wrap.appendChild(clone);
        g.appendChild(wrap);
      }
    }

    // rubber band for two-click tools
    if (this.pending && this._mouseWorld) {
      const [x1, y1] = this.toScreen(this.pending.x, this.pending.y);
      const tgt = this.hoverSnap || this._mouseWorld;
      const [x2, y2] = this.toScreen(tgt.x, tgt.y);
      if (this.tool === "slab") {
        g.appendChild(el("rect", {
          x: Math.min(x1, x2), y: Math.min(y1, y2),
          width: Math.abs(x2 - x1), height: Math.abs(y2 - y1),
          fill: C.box, stroke: C.boxEdge, "stroke-width": 1.25, "stroke-dasharray": "6 4",
        }));
      } else {
        g.appendChild(el("line", {
          x1, y1, x2, y2,
          stroke: this.tool === "brace" ? C.braceRubber
            : this.tool === "link" ? C.linkRubber : C.rubber,
          "stroke-width": this.tool === "wall" ? 5 : 2, "stroke-dasharray": "7 5",
          "stroke-linecap": "round", opacity: 0.9,
        }));
      }
      g.appendChild(el("circle", {
        cx: x1, cy: y1, r: 4.5, fill: "none", stroke: C.snap, "stroke-width": 1.5,
      }));
    }

    // snap marker
    if (this.hoverSnap && this.tool !== "select" && this.tool !== "erase") {
      const [px, py] = this.toScreen(this.hoverSnap.x, this.hoverSnap.y);
      g.appendChild(el("circle", {
        cx: px, cy: py, r: 5, fill: "none", stroke: C.snap, "stroke-width": 1.5,
      }));
      g.appendChild(el("circle", { cx: px, cy: py, r: 1.4, fill: C.snap }));
    }

    // selection box
    if (this.box) {
      const [x1, y1] = this.toScreen(this.box.x0, this.box.y0);
      const [x2, y2] = this.toScreen(this.box.x1, this.box.y1);
      g.appendChild(el("rect", {
        x: Math.min(x1, x2), y: Math.min(y1, y2),
        width: Math.abs(x2 - x1), height: Math.abs(y2 - y1),
        fill: C.box, stroke: C.boxEdge, "stroke-width": 1, "stroke-dasharray": "4 3",
      }));
    }
  }
}

/** v0.8 — grounded spring glyph (coil + ground hatch) as one path `d`,
    in world coordinates; drawn with non-scaling stroke. */
export function springGlyphPlan(cx, cy, a) {
  const top = cy + a * 1.1;                 // node sits above the ground line
  const gy = cy - a * 1.1;                  // ground line
  const n = 4, span = top - gy;
  let d = `M${cx},${top}`;
  for (let k = 1; k <= n; k++)
    d += ` L${cx + (k % 2 ? a : -a) * 0.7},${top - span * k / (n + 1)}`;
  d += ` L${cx},${gy}`;                     // land on the ground
  d += ` M${cx - a},${gy} L${cx + a},${gy}`; // ground line
  for (let k = -1; k <= 1; k++) {           // hatches
    const x0 = cx + k * a * 0.7;
    d += ` M${x0},${gy} L${x0 - a * 0.55},${gy - a * 0.55}`;
  }
  return d;
}

/* ------------------------------------------------ geometry helpers */
/** Plan-space (x, y) quad of a slab opening via bilinear mapping. */
function openingPlanQuad(corners, o) {
  const [c0, c1, c2, c3] = corners;
  const lerp = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  const at = (u, v) => lerp(lerp(c0, c1, u), lerp(c3, c2, u), v);
  return [[o.u0, o.v0], [o.u1, o.v0], [o.u1, o.v1], [o.u0, o.v1]].map(([u, v]) => at(u, v));
}

function distToSeg(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const l2 = dx * dx + dy * dy;
  let t = l2 ? ((px - x1) * dx + (py - y1) * dy) / l2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

function pointInPoly(x, y, corners) {
  let inside = false;
  for (let i = 0, j = corners.length - 1; i < corners.length; j = i++) {
    const xi = corners[i][0], yi = corners[i][1];
    const xj = corners[j][0], yj = corners[j][1];
    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi))
      inside = !inside;
  }
  return inside;
}
