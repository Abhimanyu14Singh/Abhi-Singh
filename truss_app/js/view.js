/* ============================================================================
 * view.js — SVG rendering of the truss
 *
 * Pure rendering: given a model, an (optional) analysis result, and display
 * options, it draws into an <svg>. It owns the world<->screen transform and a
 * few hit-test helpers, but holds no editing state — interaction.js drives it.
 *
 * Coordinate systems:
 *   world  : engineering coords, metres, y-up   (what the model stores)
 *   screen : SVG pixels, y-down                 (what the user sees)
 * ==========================================================================*/

(function (global) {
  'use strict';

  const SVGNS = 'http://www.w3.org/2000/svg';
  function el(name, attrs) {
    const e = document.createElementNS(SVGNS, name);
    if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  class TrussView {
    constructor(svg) {
      this.svg = svg;
      // world->screen: screenX = ox + s*worldX ; screenY = oy - s*worldY
      this.scale = 60; // pixels per metre
      this.ox = 80;
      this.oy = 0; // set in fit()/resize

      // Display toggles (driven by the UI).
      this.opts = {
        showGrid: true,
        showForces: true, // colour + thickness by axial force
        showDeflection: false,
        deflScale: 1, // auto-computed visual exaggeration * user factor
        userDeflScale: 1,
        showLabels: true,
        highlightMember: null, // member index to spotlight
        highlightKind: null, // 'maxForce' | 'controls' | null
      };

      // Layer groups (draw order matters).
      this.gGrid = el('g', { class: 'layer-grid' });
      this.gDefl = el('g', { class: 'layer-deflected' });
      this.gMembers = el('g', { class: 'layer-members' });
      this.gNodes = el('g', { class: 'layer-nodes' });
      this.gSupports = el('g', { class: 'layer-supports' });
      this.gLoads = el('g', { class: 'layer-loads' });
      this.gReactions = el('g', { class: 'layer-reactions' });
      this.gLabels = el('g', { class: 'layer-labels' });
      this.gOverlay = el('g', { class: 'layer-overlay' });
      [this.gGrid, this.gDefl, this.gMembers, this.gNodes, this.gSupports,
       this.gLoads, this.gReactions, this.gLabels, this.gOverlay]
        .forEach((g) => svg.appendChild(g));
    }

    /* ----- transforms ------------------------------------------------------ */
    toScreen(x, y) { return [this.ox + this.scale * x, this.oy - this.scale * y]; }
    toWorld(px, py) { return [(px - this.ox) / this.scale, (this.oy - py) / this.scale]; }

    size() {
      const r = this.svg.getBoundingClientRect();
      return { w: r.width, h: r.height };
    }

    // Frame the model nicely in the viewport (with margin). Falls back to a
    // default view when the model is empty.
    fit(model) {
      const { w, h } = this.size();
      this.oy = h - 60; // baseline near the bottom
      if (!model || model.nodes.length === 0) {
        this.scale = 60; this.ox = 80; return;
      }
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const n of model.nodes) {
        minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
        minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
      }
      const spanX = Math.max(maxX - minX, 1);
      const spanY = Math.max(maxY - minY, 1);
      const margin = 90;
      const sx = (w - 2 * margin) / spanX;
      const sy = (h - 2 * margin) / spanY;
      this.scale = Math.max(20, Math.min(sx, sy, 120));
      this.ox = margin - minX * this.scale;
      this.oy = h - margin + minY * this.scale;
    }

    /* ----- main render ----------------------------------------------------- */
    render(model, result) {
      this._clear();
      if (this.opts.showGrid) this._drawGrid();
      this._drawMembers(model, result);
      if (this.opts.showDeflection && result && result.ok) {
        this._drawDeflected(model, result);
      }
      this._drawNodes(model, result);
      this._drawSupports(model);
      this._drawLoads(model);
      if (result && result.ok) this._drawReactions(model, result);
    }

    _clear() {
      [this.gGrid, this.gDefl, this.gMembers, this.gNodes, this.gSupports,
       this.gLoads, this.gReactions, this.gLabels, this.gOverlay]
        .forEach((g) => { while (g.firstChild) g.removeChild(g.firstChild); });
    }

    _drawGrid() {
      const { w, h } = this.size();
      const step = this.scale; // 1 m grid
      const [wx0, wy0] = this.toWorld(0, h);
      const [wx1, wy1] = this.toWorld(w, 0);
      const x0 = Math.floor(wx0), x1 = Math.ceil(wx1);
      const y0 = Math.floor(wy0), y1 = Math.ceil(wy1);
      for (let gx = x0; gx <= x1; gx++) {
        const [sx] = this.toScreen(gx, 0);
        this.gGrid.appendChild(el('line', {
          x1: sx, y1: 0, x2: sx, y2: h,
          class: gx === 0 ? 'grid-axis' : 'grid-line',
        }));
      }
      for (let gy = y0; gy <= y1; gy++) {
        const [, sy] = this.toScreen(0, gy);
        this.gGrid.appendChild(el('line', {
          x1: 0, y1: sy, x2: w, y2: sy,
          class: gy === 0 ? 'grid-axis' : 'grid-line',
        }));
      }
      // void step usage lint
      void step;
    }

    _drawMembers(model, result) {
      // Determine a force scale for line thickness (heat by |N|).
      let maxAbsN = 0;
      if (result && result.ok && this.opts.showForces) {
        for (const m of result.members) maxAbsN = Math.max(maxAbsN, Math.abs(m.N));
      }
      model.members.forEach((mem, k) => {
        const ni = model.nodes[mem.i], nj = model.nodes[mem.j];
        const [x1, y1] = this.toScreen(ni.x, ni.y);
        const [x2, y2] = this.toScreen(nj.x, nj.y);

        let cls = 'member';
        let width = 4;
        let title = `Member ${k + 1}`;
        if (result && result.ok && this.opts.showForces) {
          const r = result.members[k];
          cls += ' ' + r.state; // tension | compression | zero
          if (maxAbsN > 0) width = 2 + 7 * (Math.abs(r.N) / maxAbsN);
          title += `  N = ${r.N.toFixed(2)} kN  (${r.state})`;
        }
        if (this.opts.highlightMember === k) cls += ' spotlight';

        const line = el('line', {
          x1, y1, x2, y2, 'stroke-width': width, class: cls,
          'data-member': k,
        });
        const t = el('title'); t.textContent = title; line.appendChild(t);
        this.gMembers.appendChild(line);

        // Member force / id label at midpoint.
        if (this.opts.showLabels) {
          const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
          let label = `${k + 1}`;
          if (result && result.ok && this.opts.showForces) {
            label = `${result.members[k].N >= 0 ? '+' : ''}${result.members[k].N.toFixed(1)}`;
          }
          const txt = el('text', {
            x: mx, y: my - 6, class: 'member-label', 'text-anchor': 'middle',
          });
          txt.textContent = label;
          this.gLabels.appendChild(txt);
        }
      });
    }

    _drawDeflected(model, result) {
      const scale = this._effectiveDeflScale(model, result);
      model.members.forEach((mem) => {
        const ni = model.nodes[mem.i], nj = model.nodes[mem.j];
        const di = result.displacements[mem.i], dj = result.displacements[mem.j];
        const [x1, y1] = this.toScreen(ni.x + scale * di.ux, ni.y + scale * di.uy);
        const [x2, y2] = this.toScreen(nj.x + scale * dj.ux, nj.y + scale * dj.uy);
        this.gDefl.appendChild(el('line', {
          x1, y1, x2, y2, class: 'member-deflected',
        }));
      });
    }

    // Auto-pick an exaggeration so the largest displacement is a visible
    // fraction of the model size, then multiply by the user's factor.
    _effectiveDeflScale(model, result) {
      let maxU = 0;
      for (const d of result.displacements) maxU = Math.max(maxU, d.mag);
      if (maxU <= 0) return 0;
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const n of model.nodes) {
        minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
        minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
      }
      const span = Math.max(maxX - minX, maxY - minY, 1);
      const target = 0.12 * span; // largest deflection ~12% of model span
      this.opts.deflScale = target / maxU;
      return this.opts.deflScale * this.opts.userDeflScale;
    }

    _drawNodes(model, result) {
      model.nodes.forEach((n, k) => {
        const [x, y] = this.toScreen(n.x, n.y);
        const c = el('circle', { cx: x, cy: y, r: 6, class: 'node', 'data-node': k });
        const t = el('title');
        t.textContent = `Joint ${k + 1}  (${n.x.toFixed(2)}, ${n.y.toFixed(2)}) m`;
        c.appendChild(t);
        this.gNodes.appendChild(c);
      });
      void result;
    }

    _drawSupports(model) {
      model.supports.forEach((s) => {
        const n = model.nodes[s.node];
        const [x, y] = this.toScreen(n.x, n.y);
        if (s.dx && s.dy) this._pin(x, y);
        else if (!s.dx && s.dy) this._rollerVertical(x, y); // restrains uy
        else if (s.dx && !s.dy) this._rollerHorizontal(x, y); // restrains ux
      });
    }

    _pin(x, y) {
      const g = el('g', { class: 'support pin' });
      g.appendChild(el('path', { d: `M ${x} ${y} L ${x - 12} ${y + 20} L ${x + 12} ${y + 20} Z` }));
      g.appendChild(el('line', { x1: x - 16, y1: y + 20, x2: x + 16, y2: y + 20, class: 'ground' }));
      this.gSupports.appendChild(g);
    }
    _rollerVertical(x, y) {
      const g = el('g', { class: 'support roller' });
      g.appendChild(el('path', { d: `M ${x} ${y} L ${x - 12} ${y + 16} L ${x + 12} ${y + 16} Z` }));
      g.appendChild(el('circle', { cx: x - 6, cy: y + 21, r: 4 }));
      g.appendChild(el('circle', { cx: x + 6, cy: y + 21, r: 4 }));
      g.appendChild(el('line', { x1: x - 16, y1: y + 26, x2: x + 16, y2: y + 26, class: 'ground' }));
      this.gSupports.appendChild(g);
    }
    _rollerHorizontal(x, y) {
      const g = el('g', { class: 'support roller' });
      g.appendChild(el('path', { d: `M ${x} ${y} L ${x + 16} ${y - 12} L ${x + 16} ${y + 12} Z` }));
      g.appendChild(el('circle', { cx: x + 21, cy: y - 6, r: 4 }));
      g.appendChild(el('circle', { cx: x + 21, cy: y + 6, r: 4 }));
      g.appendChild(el('line', { x1: x + 26, y1: y - 16, x2: x + 26, y2: y + 16, class: 'ground' }));
      this.gSupports.appendChild(g);
    }

    _drawLoads(model) {
      // Scale arrows by relative magnitude.
      let maxF = 0;
      for (const l of model.loads) maxF = Math.max(maxF, Math.hypot(l.fx, l.fy));
      model.loads.forEach((l) => {
        const n = model.nodes[l.node];
        const [x, y] = this.toScreen(n.x, n.y);
        const mag = Math.hypot(l.fx, l.fy);
        if (mag === 0) return;
        const len = 30 + 40 * (maxF > 0 ? mag / maxF : 1);
        // Arrow points TOWARD the joint, tail in the load direction's origin.
        const ux = l.fx / mag, uy = l.fy / mag;
        // screen: y down, so invert uy
        const tailX = x - ux * len, tailY = y + uy * len;
        const g = el('g', { class: 'load' });
        g.appendChild(el('line', { x1: tailX, y1: tailY, x2: x, y2: y, 'marker-end': 'url(#arrow)' }));
        const lab = el('text', { x: tailX, y: tailY - 4, class: 'load-label', 'text-anchor': 'middle' });
        lab.textContent = `${mag.toFixed(0)} kN`;
        g.appendChild(lab);
        this.gLoads.appendChild(g);
      });
    }

    _drawReactions(model, result) {
      let maxR = 0;
      for (const r of result.reactions) {
        if (r.rx != null) maxR = Math.max(maxR, Math.abs(r.rx));
        if (r.ry != null) maxR = Math.max(maxR, Math.abs(r.ry));
      }
      if (maxR === 0) return;
      result.reactions.forEach((r) => {
        const n = model.nodes[r.node];
        const [x, y] = this.toScreen(n.x, n.y);
        const draw = (fx, fy) => {
          const mag = Math.hypot(fx, fy);
          if (mag < 1e-6) return;
          const len = 25 + 35 * (mag / maxR);
          const ux = fx / mag, uy = fy / mag;
          const tailX = x - ux * len, tailY = y + uy * len;
          const g = el('g', { class: 'reaction' });
          g.appendChild(el('line', { x1: tailX, y1: tailY, x2: x, y2: y, 'marker-end': 'url(#arrow-react)' }));
          this.gReactions.appendChild(g);
        };
        if (r.rx != null) draw(r.rx, 0);
        if (r.ry != null) draw(0, r.ry);
      });
    }

    /* ----- hit testing (screen px) ---------------------------------------- */
    nodeIndexNear(px, py, tolPx = 12) {
      let best = -1, bestD = tolPx;
      this.svg; // noop
      this.gNodes.querySelectorAll('circle').forEach((c) => {
        const dx = +c.getAttribute('cx') - px, dy = +c.getAttribute('cy') - py;
        const d = Math.hypot(dx, dy);
        if (d <= bestD) { bestD = d; best = +c.getAttribute('data-node'); }
      });
      return best;
    }

    memberIndexNear(px, py, tolPx = 8) {
      let best = -1, bestD = tolPx;
      this.gMembers.querySelectorAll('line').forEach((ln) => {
        const x1 = +ln.getAttribute('x1'), y1 = +ln.getAttribute('y1');
        const x2 = +ln.getAttribute('x2'), y2 = +ln.getAttribute('y2');
        const d = pointSegDist(px, py, x1, y1, x2, y2);
        if (d <= bestD) { bestD = d; best = +ln.getAttribute('data-member'); }
      });
      return best;
    }
  }

  function pointSegDist(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1;
    const len2 = dx * dx + dy * dy;
    if (len2 === 0) return Math.hypot(px - x1, py - y1);
    let t = ((px - x1) * dx + (py - y1) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
  }

  global.TrussView = TrussView;
})(typeof window !== 'undefined' ? window : globalThis);
