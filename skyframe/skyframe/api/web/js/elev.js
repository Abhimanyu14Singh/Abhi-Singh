/* SkyFrame elevation editor (v0.5) — 2D section editor for one grid-line
   frame plane. Horizontal axis = in-plane coordinate (crossing grid lines
   ticked), vertical = elevation (story levels ruled + labeled).
   Tools: select / column / beam / brace / wall / link / erase.
   Owns only view + interaction; model mutations happen in app.js via the
   onDraw / onErase / onSelect callbacks (same contract as PlanEditor). */

import { springKey, linkTypeOf, LINK_TYPES } from "./modeledit.js";

const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
};

const C = {
  gridLine: "rgba(120, 140, 165, 0.22)",
  levelLine: "rgba(120, 140, 165, 0.34)",
  gridLabel: "rgba(140, 160, 185, 0.75)",
  column: "#5f8fc9",
  beam: "#77879b",
  brace: "#c98500",
  braceRubber: "rgba(201, 133, 0, 0.9)",
  link: "#34c384",
  linkRubber: "rgba(52, 195, 132, 0.9)",
  spring: "#2fbf74",                              // grounded spring support (v0.8)
  wallFill: "rgba(95, 143, 201, 0.20)",
  wallEdge: "rgba(125, 168, 216, 0.8)",
  openEdge: "rgba(53, 181, 229, 0.7)",
  sel: "#35b5e5",
  snap: "#35b5e5",
  rubber: "rgba(53, 181, 229, 0.9)",
  box: "rgba(53, 181, 229, 0.10)",
  boxEdge: "rgba(53, 181, 229, 0.55)",
  hoverErase: "#e66767",
};

const HIT_PX = 9;

export class ElevEditor {
  /**
   * opts: { getModel, getPlane () => {axis:"x"|"y", index} | null,
   *         getSelection (Set of "type:uid"),
   *         onDraw(tool, payload), onErase(ref), onSelect(refs, additive),
   *         onReadout(text) }
   * Draw payloads carry fully-resolved 3D points + the target story:
   *   column {x, y, story} · beam {p1, p2, story} · brace {p1, p2, story}
   *   wall {corners, story} · link {p1, p2}
   */
  constructor(svg, opts) {
    this.svg = svg;
    this.opts = opts;
    this.tool = "select";
    this.pending = null;          // first snapped point {s, z}
    this.hoverSnap = null;
    this.hoverRef = null;
    this.box = null;
    this._pan = null;
    this._downSel = null;

    // camera: (s = in-plane coord, z = elevation) → screen, z up.
    this.scale = 40;
    this.cs = 9; this.cz = 6;
    this.w = 800; this.h = 600;
    this._fitted = false;

    this.gWorld = el("g");
    this.gGrid = el("g");
    this.gElems = el("g");
    this.gLabels = el("g");
    this.gOverlay = el("g");
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

  /* ------------------------------------------------ plane helpers */
  /** Resolved plane: {axis, coord, label, ticks, tickLabels} or null. */
  plane() {
    const m = this.opts.getModel();
    const sel = this.opts.getPlane();
    const g = m && m.grid;
    if (!g || !sel) return null;
    const lines = sel.axis === "x" ? g.x_lines : g.y_lines;
    const labels = sel.axis === "x" ? g.x_labels : g.y_labels;
    if (sel.index < 0 || sel.index >= lines.length) return null;
    return {
      axis: sel.axis, coord: lines[sel.index],
      label: (labels && labels[sel.index]) || String(sel.index + 1),
      ticks: sel.axis === "x" ? g.y_lines : g.x_lines,
      tickLabels: sel.axis === "x" ? g.y_labels : g.x_labels,
    };
  }

  /** (s, z) → world 3D point on the current plane. */
  world3(s, z) {
    const pl = this.plane();
    if (!pl) return [s, 0, z];
    return pl.axis === "x" ? [pl.coord, s, z] : [s, pl.coord, z];
  }
  sOf(p) {
    const pl = this.plane();
    return pl && pl.axis === "x" ? p[1] : p[0];
  }
  inPlane(p) {
    const pl = this.plane();
    if (!pl) return false;
    return Math.abs((pl.axis === "x" ? p[0] : p[1]) - pl.coord) < 1e-4;
  }
  /** Story level elevations bottom→top, including the base (0-ish). */
  levels() {
    const m = this.opts.getModel();
    if (!m) return [0];
    const base = m.stories.length ? m.stories[0].elevation - m.stories[0].height : 0;
    return [base, ...m.stories.map(s => s.elevation)];
  }

  /* ------------------------------------------------ coordinates */
  toScreen(s, z) {
    return [this.w / 2 + (s - this.cs) * this.scale,
            this.h / 2 - (z - this.cz) * this.scale];
  }
  toWorld(px, py) {
    return { s: this.cs + (px - this.w / 2) / this.scale,
             z: this.cz - (py - this.h / 2) / this.scale };
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
    this.svg.dataset.tool = t;
    this.renderOverlay();
  }

  cancel() {
    this.pending = null;
    this.box = null;
    this.renderOverlay();
  }

  fit() {
    const pl = this.plane();
    if (!pl || !pl.ticks.length) return;
    const s0 = pl.ticks[0], s1 = pl.ticks[pl.ticks.length - 1];
    const lv = this.levels();
    const z0 = lv[0], z1 = lv[lv.length - 1] || 3;
    const spanS = Math.max(s1 - s0, 2), spanZ = Math.max(z1 - z0, 2);
    // reserve a ~130px gutter on the left for the story-name labels
    this.scale = Math.min((this.w - 260) / spanS, (this.h - 110) / spanZ);
    this.scale = Math.max(6, Math.min(this.scale, 220));
    this.cs = (s0 + s1) / 2 - 65 / this.scale;
    this.cz = (z0 + z1) / 2;
    this.refresh();
  }

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
      `translate(${this.w / 2},${this.h / 2}) scale(${this.scale},${-this.scale}) translate(${-this.cs},${-this.cz})`);
    this._renderLabels();
  }

  /* ------------------------------------------------ static render */
  renderStatic() {
    const m = this.opts.getModel();
    const pl = this.plane();
    this.gGrid.textContent = "";
    this.gElems.textContent = "";
    if (!m || !pl) return;
    const s0 = pl.ticks[0], s1 = pl.ticks[pl.ticks.length - 1];
    const lv = this.levels();
    const z0 = lv[0], z1 = lv[lv.length - 1];
    const mS = Math.max((s1 - s0) * 0.06, 0.8), mZ = Math.max((z1 - z0) * 0.06, 0.8);

    // crossing grid lines (vertical ticks)
    for (const s of pl.ticks)
      this.gGrid.appendChild(el("line", {
        x1: s, y1: z0 - mZ, x2: s, y2: z1 + mZ,
        stroke: C.gridLine, "stroke-width": 1, "vector-effect": "non-scaling-stroke",
      }));
    // story levels (horizontal rules, slightly stronger)
    for (const z of lv)
      this.gGrid.appendChild(el("line", {
        x1: s0 - mS, y1: z, x2: s1 + mS, y2: z,
        stroke: C.levelLine, "stroke-width": 1, "vector-effect": "non-scaling-stroke",
        "stroke-dasharray": z === z0 ? "" : "5 4",
      }));

    const sel = this.opts.getSelection();
    const isSel = (type, uid) => sel.has(`${type}:${uid}`);

    // walls lying in the plane — filled quads with opening cutouts (even-odd)
    for (const sh of (m.shells || [])) {
      if (sh.kind !== "wall" || !sh.corners.every(c => this.inPlane(c))) continue;
      const seld = isSel("shell", sh.uid);
      const pts = sh.corners.map(c => [this.sOf(c), c[2]]);
      let d = pts.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join(" ") + " Z";
      let dOpen = "";
      for (const o of (sh.openings || [])) {
        const q = this._openingQuad(sh, o);
        const sub = q.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join(" ") + " Z";
        d += " " + sub;
        dOpen += (dOpen ? " " : "") + sub;
      }
      this.gElems.appendChild(el("path", {
        d, "fill-rule": "evenodd",
        fill: seld ? "rgba(53,181,229,0.22)" : C.wallFill,
        stroke: seld ? C.sel : C.wallEdge,
        "stroke-width": seld ? 2 : 1.25, "vector-effect": "non-scaling-stroke",
        "data-ref": `shell:${sh.uid}`,
      }));
      if (dOpen)
        this.gElems.appendChild(el("path", {
          d: dOpen, fill: "none", stroke: C.openEdge, "stroke-width": 1,
          "stroke-dasharray": "4 3", "vector-effect": "non-scaling-stroke",
        }));
    }

    // members in the plane — beams, braces, then columns on top
    const inPlaneMember = mm => this.inPlane(mm.pi) && this.inPlane(mm.pj);
    for (const mm of m.members) {
      if (mm.kind === "column" || mm.kind === "brace" || !inPlaneMember(mm)) continue;
      const seld = isSel("member", mm.uid);
      this.gElems.appendChild(el("line", {
        x1: this.sOf(mm.pi), y1: mm.pi[2], x2: this.sOf(mm.pj), y2: mm.pj[2],
        stroke: seld ? C.sel : C.beam, "stroke-width": seld ? 3 : 2,
        "stroke-linecap": "round", "vector-effect": "non-scaling-stroke",
        "data-ref": `member:${mm.uid}`,
      }));
    }
    for (const mm of m.members) {
      if (mm.kind !== "brace" || !inPlaneMember(mm)) continue;
      const seld = isSel("member", mm.uid);
      this.gElems.appendChild(el("line", {
        x1: this.sOf(mm.pi), y1: mm.pi[2], x2: this.sOf(mm.pj), y2: mm.pj[2],
        stroke: seld ? C.sel : C.brace, "stroke-width": seld ? 3 : 2,
        "stroke-dasharray": "7 5",
        "stroke-linecap": "round", "vector-effect": "non-scaling-stroke",
        "data-ref": `member:${mm.uid}`,
      }));
    }
    for (const mm of m.members) {
      if (mm.kind !== "column" || !inPlaneMember(mm)) continue;
      const seld = isSel("member", mm.uid);
      this.gElems.appendChild(el("line", {
        x1: this.sOf(mm.pi), y1: mm.pi[2], x2: this.sOf(mm.pj), y2: mm.pj[2],
        stroke: seld ? C.sel : C.column, "stroke-width": seld ? 4 : 3,
        "stroke-linecap": "round", "vector-effect": "non-scaling-stroke",
        "data-ref": `member:${mm.uid}`,
      }));
    }

    // links in the plane — green device glyphs (v0.15: per link_type)
    for (const lk of (m.links || [])) {
      if (!this.inPlane(lk.pi) || !this.inPlane(lk.pj)) continue;
      const seld = isSel("link", lk.uid);
      this.gElems.appendChild(el("path", {
        d: linkGlyphPath(this.sOf(lk.pi), lk.pi[2], this.sOf(lk.pj), lk.pj[2],
          linkTypeOf(lk), 0.16),
        fill: "none",
        stroke: seld ? C.sel : C.link, "stroke-width": seld ? 2.5 : 1.8,
        "stroke-linejoin": "round", "stroke-linecap": "round",
        "vector-effect": "non-scaling-stroke",
        "data-ref": `link:${lk.uid}`,
      }));
    }

    // v0.8: spring supports in the plane — grounded green coil glyphs
    for (const sp of (m.spring_supports || [])) {
      if (!this.inPlane(sp.point)) continue;
      const seld = isSel("spring", springKey(sp.point));
      this.gElems.appendChild(el("path", {
        d: springGlyphElev(this.sOf(sp.point), sp.point[2], 0.34),
        fill: "none", stroke: seld ? C.sel : C.spring,
        "stroke-width": seld ? 2.6 : 1.8,
        "stroke-linejoin": "round", "stroke-linecap": "round",
        "vector-effect": "non-scaling-stroke",
        "data-ref": `spring:${springKey(sp.point)}`,
      }));
    }
  }

  /** Opening rectangle of a wall in (s, z) space (bilinear on corners). */
  _openingQuad(sh, o) {
    const [c0, c1, c2, c3] = sh.corners;
    const lerp = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
    const at = (u, v) => lerp(lerp(c0, c1, u), lerp(c3, c2, u), v);
    return [[o.u0, o.v0], [o.u1, o.v0], [o.u1, o.v1], [o.u0, o.v1]]
      .map(([u, v]) => { const p = at(u, v); return [this.sOf(p), p[2]]; });
  }

  _renderLabels() {
    const pl = this.plane();
    this.gLabels.textContent = "";
    if (!pl) return;
    const m = this.opts.getModel();
    const s0 = pl.ticks[0], s1 = pl.ticks[pl.ticks.length - 1];
    const lv = this.levels();
    const z0 = lv[0], z1 = lv[lv.length - 1];
    const mS = Math.max((s1 - s0) * 0.06, 0.8), mZ = Math.max((z1 - z0) * 0.06, 0.8);
    const mk = (s, z, text, anchor = "middle") => {
      const [px, py] = this.toScreen(s, z);
      const t = el("text", {
        x: px, y: py, fill: C.gridLabel, "font-size": 11, "font-weight": 600,
        "text-anchor": anchor, "dominant-baseline": "middle", "font-family": "inherit",
      });
      t.textContent = text;
      this.gLabels.appendChild(t);
    };
    // crossing grid line labels along the bottom
    pl.ticks.forEach((s, i) =>
      mk(s, z0 - mZ - 10 / this.scale, (pl.tickLabels && pl.tickLabels[i]) || String(i + 1)));
    // story labels on the left: name + elevation
    mk(s0 - mS - 12 / this.scale, z0, "Base", "end");
    for (const st of (m ? m.stories : []))
      mk(s0 - mS - 12 / this.scale, st.elevation, `${st.name} · ${st.elevation.toFixed(1)}`, "end");

    // v0.15: link device-type letters (D/G/H/I) on in-plane non-elastic links
    for (const lk of ((m && m.links) || [])) {
      if (!this.inPlane(lk.pi) || !this.inPlane(lk.pj)) continue;
      const letter = LINK_TYPES[linkTypeOf(lk)].letter;
      if (!letter) continue;
      const [px, py] = this.toScreen(
        (this.sOf(lk.pi) + this.sOf(lk.pj)) / 2, (lk.pi[2] + lk.pj[2]) / 2);
      const oy = py - 12;                          // sit just above the glyph
      this.gLabels.appendChild(el("circle", {
        cx: px, cy: oy, r: 6.5, fill: "rgba(52,195,132,0.14)",
        stroke: C.link, "stroke-width": 1,
      }));
      const t = el("text", {
        x: px, y: oy + 0.5, fill: C.link, "font-size": 8.5, "font-weight": 700,
        "text-anchor": "middle", "dominant-baseline": "central", "font-family": "inherit",
      });
      t.textContent = letter;
      this.gLabels.appendChild(t);
    }
  }

  /* ------------------------------------------------ snapping */
  /** Snap (s, z) to grid-tick × story-level nodes plus 0.5 m increments.
      levelOnly forces z onto a story level (beam tool). */
  snap(w, levelOnly = false) {
    const pl = this.plane();
    if (!pl) return { s: w.s, z: w.z, dPx: 0 };
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    const nearVal = (arr, v) => arr.reduce((b, a) => Math.abs(a - v) < Math.abs(b - v) ? a : b);
    const lv = this.levels();
    const s0 = pl.ticks[0], s1 = pl.ticks[pl.ticks.length - 1];
    const z0 = lv[0], z1 = lv[lv.length - 1];
    const tickS = nearVal(pl.ticks, w.s);
    const levelZ = nearVal(lv, w.z);
    const halfS = clamp(Math.round(w.s * 2) / 2, s0, s1);
    const halfZ = clamp(Math.round(w.z * 2) / 2, z0, z1);
    const cands = levelOnly
      ? [{ s: tickS, z: levelZ }, { s: halfS, z: levelZ }]
      : [
          { s: tickS, z: levelZ },        // grid intersection × story level
          { s: tickS, z: halfZ },         // on a grid line, 0.5 m z
          { s: halfS, z: levelZ },        // on a story level, 0.5 m s
        ];
    let best = null, bd = Infinity;
    for (const c of cands) {
      const d = Math.hypot(c.s - w.s, c.z - w.z);
      if (d < bd) { bd = d; best = c; }
    }
    return { s: best.s, z: best.z, dPx: bd * this.scale };
  }

  /* ------------------------------------------------ hit testing */
  _candidates(w) {
    const m = this.opts.getModel();
    const pl = this.plane();
    if (!m || !pl) return [];
    const tol = HIT_PX / this.scale;
    const out = [];
    for (const sp of (m.spring_supports || [])) {
      if (this.inPlane(sp.point) &&
          Math.hypot(w.s - this.sOf(sp.point), w.z - sp.point[2]) <= Math.max(tol, 0.4))
        out.push({ type: "spring", uid: springKey(sp.point) });
    }
    const seg = (mm) => distToSeg(w.s, w.z, this.sOf(mm.pi), mm.pi[2], this.sOf(mm.pj), mm.pj[2]);
    const inPl = mm => this.inPlane(mm.pi) && this.inPlane(mm.pj);
    for (const mm of m.members)
      if (mm.kind === "column" && inPl(mm) && seg(mm) <= tol)
        out.push({ type: "member", uid: mm.uid });
    for (const mm of m.members)
      if (mm.kind !== "column" && inPl(mm) && seg(mm) <= tol)
        out.push({ type: "member", uid: mm.uid });
    for (const lk of (m.links || []))
      if (this.inPlane(lk.pi) && this.inPlane(lk.pj) &&
          distToSeg(w.s, w.z, this.sOf(lk.pi), lk.pi[2], this.sOf(lk.pj), lk.pj[2]) <= tol)
        out.push({ type: "link", uid: lk.uid });
    for (const sh of (m.shells || []))
      if (sh.kind === "wall" && sh.corners.every(c => this.inPlane(c)) &&
          pointInPoly(w.s, w.z, sh.corners.map(c => [this.sOf(c), c[2]])))
        out.push({ type: "shell", uid: sh.uid });
    return out;
  }

  hitTest(w) {
    const c = this._candidates(w);
    return c.length ? c[0] : null;
  }

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
    const [sa, sb] = [Math.min(b.s0, b.s1), Math.max(b.s0, b.s1)];
    const [za, zb] = [Math.min(b.z0, b.z1), Math.max(b.z0, b.z1)];
    const inBox = (s, z) => s >= sa && s <= sb && z >= za && z <= zb;
    const refs = [];
    const endpts = (pi, pj) =>
      inBox(this.sOf(pi), pi[2]) || inBox(this.sOf(pj), pj[2]) ||
      inBox((this.sOf(pi) + this.sOf(pj)) / 2, (pi[2] + pj[2]) / 2);
    for (const mm of m.members)
      if (this.inPlane(mm.pi) && this.inPlane(mm.pj) && endpts(mm.pi, mm.pj))
        refs.push({ type: "member", uid: mm.uid });
    for (const lk of (m.links || []))
      if (this.inPlane(lk.pi) && this.inPlane(lk.pj) && endpts(lk.pi, lk.pj))
        refs.push({ type: "link", uid: lk.uid });
    for (const sh of (m.shells || [])) {
      if (sh.kind !== "wall" || !sh.corners.every(c => this.inPlane(c))) continue;
      const cs = this.sOf(sh.corners[0]);
      const cx = sh.corners.reduce((a, c) => a + this.sOf(c), 0) / 4;
      const cz = sh.corners.reduce((a, c) => a + c[2], 0) / 4;
      if (sh.corners.some(c => inBox(this.sOf(c), c[2])) || inBox(cx, cz) || inBox(cs, cz))
        refs.push({ type: "shell", uid: sh.uid });
    }
    for (const sp of (m.spring_supports || [])) {
      if (this.inPlane(sp.point) && inBox(this.sOf(sp.point), sp.point[2]))
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
        this._toolClick(w);
      }
    });

    svg.addEventListener("pointermove", e => {
      const [px, py] = this._evPx(e);
      if (this._pan) {
        this.cs -= (px - this._pan.px) / this.scale;
        this.cz += (py - this._pan.py) / this.scale;
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
          this.box = { s0: this._downSel.w.s, z0: this._downSel.w.z, s1: w.s, z1: w.z };
      }
      const drawing = this.tool !== "select" && this.tool !== "erase";
      this.hoverSnap = drawing ? this.snap(w, this.tool === "beam") : null;
      this.hoverRef = drawing ? null : this.hitTest(w);
      this._mouseWorld = w;
      this.renderOverlay();
      this._readout(w);
    });

    const up = () => {
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
      this.cs += before.s - after.s;
      this.cz += before.z - after.z;
      this._applyTransform();
      this.renderOverlay();
    }, { passive: false });

    svg.addEventListener("dblclick", () => this.fit());
  }

  _toolClick(w) {
    const m = this.opts.getModel();
    const pl = this.plane();
    if (!m || !pl) return;
    const nearVal = (arr, v) => arr.reduce((b, a) => Math.abs(a - v) < Math.abs(b - v) ? a : b);

    switch (this.tool) {
      case "column": {
        // story cell at a grid intersection: nearest tick + story containing z
        const s = nearVal(pl.ticks, w.s);
        const p = this.world3(s, 0);
        this.opts.onDraw("column", { x: p[0], y: p[1], z: w.z, elev: true });
        break;
      }
      case "spring": {
        // grounded spring at a grid intersection on the base level
        const s = nearVal(pl.ticks, w.s);
        const base = this.levels()[0];
        this.opts.onDraw("spring", { p: this.world3(s, base), elev: true });
        break;
      }
      case "beam": {
        const pt = this.snap(w, true);           // z locked to story levels
        if (!this.pending) this.pending = pt;
        else {
          const z = this.pending.z;              // beam stays at the first level
          if (Math.abs(pt.s - this.pending.s) > 1e-9) {
            this.opts.onDraw("beam", {
              p1: this.world3(this.pending.s, z), p2: this.world3(pt.s, z),
              z, elev: true,
            });
            this.pending = { s: pt.s, z };
          }
        }
        break;
      }
      case "brace": {
        const pt = this.snap(w);
        if (!this.pending) this.pending = pt;
        else if (Math.abs(pt.z - this.pending.z) > 1e-6 &&
                 Math.hypot(pt.s - this.pending.s, pt.z - this.pending.z) > 1e-9) {
          this.opts.onDraw("brace", {
            p1: this.world3(this.pending.s, this.pending.z),
            p2: this.world3(pt.s, pt.z),
            s1: this.pending.s, s2: pt.s, z1: this.pending.z, z2: pt.z,
            elev: true,
          });
          this.pending = pt;                     // chain drawing, Esc to stop
        }
        break;
      }
      case "wall": {
        const pt = this.snap(w);
        if (!this.pending) this.pending = pt;
        else if (Math.abs(pt.s - this.pending.s) > 1e-9) {
          this.opts.onDraw("wall", {
            s1: this.pending.s, s2: pt.s,
            z1: this.pending.z, z2: pt.z, elev: true,
          });
          this.pending = null;
        }
        break;
      }
      case "link": {
        const pt = this.snap(w);
        if (!this.pending) this.pending = pt;
        else if (Math.hypot(pt.s - this.pending.s, pt.z - this.pending.z) > 1e-9) {
          this.opts.onDraw("link", {
            p1: this.world3(this.pending.s, this.pending.z),
            p2: this.world3(pt.s, pt.z), elev: true,
          });
          this.pending = null;
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
    const t = this.hoverSnap || w;
    const val = this.hoverSnap
      ? `${t.s.toFixed(2)}, z ${t.z.toFixed(2)} m`
      : `${w.s.toFixed(2)}, z ${w.z.toFixed(2)} m`;
    this.opts.onReadout(this.pending
      ? `${this.pending.s.toFixed(2)}, z ${this.pending.z.toFixed(2)} → ${val}`
      : val);
  }

  /* ------------------------------------------------ overlay render */
  renderOverlay() {
    const g = this.gOverlay;
    g.textContent = "";

    if (this.hoverRef) {
      const node = this.gElems.querySelector(
        `[data-ref="${this.hoverRef.type}:${this.hoverRef.uid}"]`);
      if (node) {
        const clone = node.cloneNode(false);
        clone.removeAttribute("data-ref");
        clone.setAttribute("fill", "none");
        clone.setAttribute("stroke", this.tool === "erase" ? C.hoverErase : C.sel);
        clone.setAttribute("stroke-width", 3.5);
        clone.setAttribute("vector-effect", "non-scaling-stroke");
        clone.setAttribute("opacity", 0.85);
        const wrap = el("g", { transform: this.gWorld.getAttribute("transform") });
        wrap.appendChild(clone);
        g.appendChild(wrap);
      }
    }

    if (this.pending && this._mouseWorld) {
      const [x1, y1] = this.toScreen(this.pending.s, this.pending.z);
      const tgt = this.hoverSnap || { s: this._mouseWorld.s, z: this._mouseWorld.z };
      const [x2, y2] = this.toScreen(tgt.s, tgt.z);
      g.appendChild(el("line", {
        x1, y1, x2, y2,
        stroke: this.tool === "brace" ? C.braceRubber
          : this.tool === "link" ? C.linkRubber : C.rubber,
        "stroke-width": this.tool === "wall" ? 5 : 2, "stroke-dasharray": "7 5",
        "stroke-linecap": "round", opacity: 0.9,
      }));
      g.appendChild(el("circle", {
        cx: x1, cy: y1, r: 4.5, fill: "none", stroke: C.snap, "stroke-width": 1.5,
      }));
    }

    if (this.hoverSnap && this.tool !== "select" && this.tool !== "erase") {
      const [px, py] = this.toScreen(this.hoverSnap.s, this.hoverSnap.z);
      g.appendChild(el("circle", {
        cx: px, cy: py, r: 5, fill: "none", stroke: C.snap, "stroke-width": 1.5,
      }));
      g.appendChild(el("circle", { cx: px, cy: py, r: 1.4, fill: C.snap }));
    }

    if (this.box) {
      const [x1, y1] = this.toScreen(this.box.s0, this.box.z0);
      const [x2, y2] = this.toScreen(this.box.s1, this.box.z1);
      g.appendChild(el("rect", {
        x: Math.min(x1, x2), y: Math.min(y1, y2),
        width: Math.abs(x2 - x1), height: Math.abs(y2 - y1),
        fill: C.box, stroke: C.boxEdge, "stroke-width": 1, "stroke-dasharray": "4 3",
      }));
    }
  }
}

/** v0.8 — grounded spring glyph in (s, z) space, one path `d`; z grows up so
    the ground line sits BELOW the node. Non-scaling stroke. */
export function springGlyphElev(s, z, a) {
  const top = z + a * 2.2;                  // node above the ground line
  const n = 4, span = top - z;
  let d = `M${s},${top}`;
  for (let k = 1; k <= n; k++)
    d += ` L${s + (k % 2 ? a : -a) * 0.7},${top - span * k / (n + 1)}`;
  d += ` L${s},${z}`;                       // land on the ground (z = base)
  d += ` M${s - a},${z} L${s + a},${z}`;    // ground line
  for (let k = -1; k <= 1; k++) {           // hatches below the ground
    const s0 = s + k * a * 0.7;
    d += ` M${s0},${z} L${s0 - a * 0.55},${z - a * 0.55}`;
  }
  return d;
}

/* ------------------------------------------------ helpers */
/** Zigzag polyline points between (x1,y1)→(x2,y2), amplitude in world units. */
export function zigzagPoints(x1, y1, x2, y2, amp = 0.16, cycles = 5) {
  const dx = x2 - x1, dy = y2 - y1;
  const L = Math.hypot(dx, dy) || 1;
  const nx = -dy / L, ny = dx / L;                 // unit normal
  const n = cycles * 2 + 2;
  const pts = [[x1, y1]];
  for (let k = 1; k < n; k++) {
    const t = k / n;
    const off = (k % 2 ? 1 : -1) * amp * (k === 1 || k === n - 1 ? 0.5 : 1);
    pts.push([x1 + dx * t + nx * off, y1 + dy * t + ny * off]);
  }
  pts.push([x2, y2]);
  return pts.map(p => `${p[0]},${p[1]}`).join(" ");
}

/** v0.15 — link-device glyph as one SVG path `d` between two points.
    type: "elastic" | "damper" | "gap" | "hook" | "isolator". `amp` sets the
    glyph half-width in the caller's units (world m for plan/elevation SVGs,
    px for the 3D canvas via Path2D). Distinct silhouettes, one colour family:
    damper = dashpot (piston in an open cylinder), gap = open jaws with a
    clearance, hook = two interlocked chain rings, isolator = bearing
    (plate · roller · plate); elastic keeps the classic zigzag spring. */
export function linkGlyphPath(x1, y1, x2, y2, type, amp = 0.16) {
  const dx = x2 - x1, dy = y2 - y1;
  const L = Math.hypot(dx, dy) || 1;
  const ux = dx / L, uy = dy / L;
  const nx = -uy, ny = ux;                        // unit normal
  const a = Math.min(amp, L / 5);                 // glyph half-width
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
  // point at signed distance d along the axis from midpoint, offset o·a normal
  const Q = (d, o) => `${mx + ux * d + nx * o * a},${my + uy * d + ny * o * a}`;
  const M = (d, o) => `M${Q(d, o)}`, Ln = (d, o) => `L${Q(d, o)}`;
  const ring = (d, r) => {                        // circle centred on the axis
    const cx = mx + ux * d, cy = my + uy * d;
    return `M${cx - r},${cy} a${r},${r} 0 1,0 ${2 * r},0 a${r},${r} 0 1,0 ${-2 * r},0`;
  };
  const b = Math.min(L * 0.18, a * 1.8);          // device half-length
  const rod = (dA, dB) =>
    `M${x1},${y1} ${Ln(dA, 0)} M${x2},${y2} ${Ln(dB, 0)}`;
  switch (type) {
    case "damper":                                 // dashpot: ─[▮ ]─
      return rod(-b, b * 0.3) +
        ` ${M(b, 1)} ${Ln(-b, 1)} ${Ln(-b, -1)} ${Ln(b, -1)}` +   // open cylinder
        ` ${M(b * 0.3, 0.72)} ${Ln(b * 0.3, -0.72)}`;             // piston plate
    case "gap": {                                  // open jaws: ─[  ]─
      const g = Math.min(b * 0.45, a * 0.5);       // half clearance
      return rod(-b, b) +
        ` ${M(-b, 0)} ${Ln(-g, 0)} ${M(-g, 0.9)} ${Ln(-g, -0.9)}` +
        ` ${M(-g, 0.9)} ${Ln(-g + g * 0.9, 0.9)} ${M(-g, -0.9)} ${Ln(-g + g * 0.9, -0.9)}` +
        ` ${M(b, 0)} ${Ln(g, 0)} ${M(g, 0.9)} ${Ln(g, -0.9)}` +
        ` ${M(g, 0.9)} ${Ln(g - g * 0.9, 0.9)} ${M(g, -0.9)} ${Ln(g - g * 0.9, -0.9)}`;
    }
    case "hook": {                                 // interlocked chain rings
      const r = a * 0.8;
      return rod(-r * 1.7, r * 1.7) +
        ` ${ring(-r * 0.62, r)} ${ring(r * 0.62, r)}`;
    }
    case "isolator": {                             // bearing: ─|o|─
      const r = Math.min(a * 0.6, b * 0.55);
      return rod(-b * 0.7, b * 0.7) +
        ` ${M(-b * 0.7, 1)} ${Ln(-b * 0.7, -1)}` +                // plates
        ` ${M(b * 0.7, 1)} ${Ln(b * 0.7, -1)}` +
        ` ${ring(0, r)}`;                                          // roller/pad
    }
    case "fp_isolator": {                          // v0.21 — pendulum: dish + slider
      const w = b * 0.95;
      const r = Math.min(a * 0.4, w * 0.4);
      return rod(-w * 0.8, w * 0.8) +
        ` ${M(-w, 0.9)} Q${Q(0, -1.1)} ${Q(w, 0.9)}` +            // concave dish
        ` ${ring(0, r)}`;                                          // slider
    }
    case "triple_fp": {                            // v0.21 — three nested dishes
      const w = b;
      return rod(-w * 0.85, w * 0.85) +
        ` ${M(-w, 1)} Q${Q(0, -1.3)} ${Q(w, 1)}` +
        ` ${M(-w * 0.66, 0.75)} Q${Q(0, -0.95)} ${Q(w * 0.66, 0.75)}` +
        ` ${M(-w * 0.33, 0.5)} Q${Q(0, -0.6)} ${Q(w * 0.33, 0.5)}`;
    }
    case "multilinear": {                          // v0.21 — piecewise F–d line
      const w = b * 0.95;
      return rod(-w, w) +
        ` ${M(-w, -0.9)} ${Ln(-w * 0.34, 0.55)} ${Ln(w * 0.3, -0.15)} ${Ln(w, 0.9)}`;
    }
    default:                                       // elastic zigzag spring
      return "M" + zigzagPoints(x1, y1, x2, y2, a).split(" ").join(" L");
  }
}

function distToSeg(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const l2 = dx * dx + dy * dy;
  let t = l2 ? ((px - x1) * dx + (py - y1) * dy) / l2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

function pointInPoly(x, y, pts) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const xi = pts[i][0], yi = pts[i][1];
    const xj = pts[j][0], yj = pts[j][1];
    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi))
      inside = !inside;
  }
  return inside;
}
