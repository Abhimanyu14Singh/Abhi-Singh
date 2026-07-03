/* SkyFrame 3D viewer — hand-rolled perspective wireframe on 2D canvas.
   Orbit / pan / zoom, painter's-order depth sort, deformed-shape and
   mode-shape overlays with cubic-Hermite member curves. */

const COLORS = {
  column: "#5f8fc9",
  beam: "#77879b",
  brace: "#c98500",
  grid: "rgba(120, 140, 165, 0.16)",
  gridLabel: "rgba(140, 160, 185, 0.55)",
  slabFill: "rgba(53, 181, 229, 0.045)",
  slabEdge: "rgba(53, 181, 229, 0.10)",
  support: "#8fa3ba",
  deformed: "#35b5e5",
  ghost: 0.16,          // alpha for ghosted base wireframe
  axisX: "#e66767",
  axisY: "#34c384",
  axisZ: "#35b5e5",
};

const v3 = {
  sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  add: (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]],
  scale: (a, s) => [a[0] * s, a[1] * s, a[2] * s],
  dot: (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2],
  cross: (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]],
  len: a => Math.hypot(a[0], a[1], a[2]),
  norm(a) { const l = this.len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; },
};

export class Viewer3D {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.tooltipEl = opts.tooltipEl || null;
    this.getMemberTooltip = opts.getMemberTooltip || null;

    this.model = null;
    this.results = null;
    this.overlay = { deformed: false, modal: false, caseName: null, modeIndex: 0, scaleMult: 1 };
    this.labelsOn = true;

    // camera
    this.yaw = 0.7; this.pitch = 0.42; this.dist = 40;
    this.target = [0, 0, 5];
    this.fov = Math.PI / 4;
    this.vyaw = 0; this.vpitch = 0;

    this._dirty = true;
    this._raf = null;
    this._segsScreen = [];   // for hit testing
    this._hover = null;
    this._animT0 = performance.now();

    this._bindEvents();
    this._ro = new ResizeObserver(() => { this._resize(); });
    this._ro.observe(canvas.parentElement || canvas);
    this._resize();
    this._loop();
  }

  /* ------------------------------------------------ public API */
  setModel(model) {
    this.model = model;
    this._buildScene();
    this.fit();
  }

  setResults(results) {
    this.results = results;
    this._nodeXYZ = results ? results.nodes : null;
    this._dirty = true;
  }

  setOverlay(o) {
    Object.assign(this.overlay, o);
    this._dirty = true;
  }

  setLabels(on) { this.labelsOn = on; if (!on) this._setHover(null); }

  fit() {
    if (!this._bbox) return;
    const [lo, hi] = this._bbox;
    this.target = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
    const radius = Math.max(v3.len(v3.sub(hi, lo)) / 2, 1);
    this.radius = radius;
    const h = this.canvas.clientHeight || 400, w = this.canvas.clientWidth || 600;
    const fitFov = this.fov * Math.min(1, w / h);
    this.dist = radius / Math.tan(fitFov / 2) * 1.15;
    this._dirty = true;
  }

  destroy() {
    cancelAnimationFrame(this._raf);
    this._ro.disconnect();
  }

  /* ------------------------------------------------ scene build */
  _buildScene() {
    const m = this.model;
    if (!m) return;
    this.segs = m.members.map(mm => ({
      p1: mm.pi, p2: mm.pj, kind: mm.kind, uid: mm.uid,
      section: mm.section, story: mm.story,
    }));

    let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const s of this.segs) for (const p of [s.p1, s.p2]) {
      for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], p[i]); hi[i] = Math.max(hi[i], p[i]); }
    }
    if (!this.segs.length) { lo = [0, 0, 0]; hi = [10, 10, 10]; }
    this._bbox = [lo, hi];

    // ground grid from model grid
    const g = m.grid;
    this.gridLines = []; this.gridLabels = [];
    if (g && g.x_lines.length && g.y_lines.length) {
      const mX = Math.max((g.x_lines[g.x_lines.length - 1] - g.x_lines[0]) * 0.12, 1.8);
      const mY = Math.max((g.y_lines[g.y_lines.length - 1] - g.y_lines[0]) * 0.12, 1.8);
      const x0 = g.x_lines[0] - mX, x1 = g.x_lines[g.x_lines.length - 1] + mX;
      const y0 = g.y_lines[0] - mY, y1 = g.y_lines[g.y_lines.length - 1] + mY;
      g.x_lines.forEach((x, i) => {
        this.gridLines.push([[x, y0, 0], [x, y1, 0]]);
        this.gridLabels.push({ p: [x, y1 + mY * 0.35, 0], text: g.x_labels[i] });
      });
      g.y_lines.forEach((y, i) => {
        this.gridLines.push([[x0, y, 0], [x1, y, 0]]);
        this.gridLabels.push({ p: [x0 - mX * 0.35, y, 0], text: g.y_labels[i] });
      });
    }

    // diaphragm slabs: plan hull (grid rectangle) at each story elevation
    this.slabs = [];
    if (g && m.stories) {
      const x0 = g.x_lines[0], x1 = g.x_lines[g.x_lines.length - 1];
      const y0 = g.y_lines[0], y1 = g.y_lines[g.y_lines.length - 1];
      for (const st of m.stories) {
        const z = st.elevation;
        this.slabs.push([[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]]);
      }
    }

    // support markers: column bases at lowest z
    const minZ = lo[2];
    const seen = new Set();
    this.supportPts = [];
    for (const s of this.segs) {
      if (s.kind !== "column") continue;
      const p = s.p1[2] <= s.p2[2] ? s.p1 : s.p2;
      if (Math.abs(p[2] - minZ) < 1e-6) {
        const k = `${p[0]},${p[1]}`;
        if (!seen.has(k)) { seen.add(k); this.supportPts.push(p); }
      }
    }
    this._dirty = true;
  }

  /* ------------------------------------------------ events */
  _bindEvents() {
    const c = this.canvas;
    let drag = null;
    let lastMove = null;

    c.addEventListener("contextmenu", e => e.preventDefault());

    c.addEventListener("pointerdown", e => {
      c.setPointerCapture(e.pointerId);
      drag = {
        x: e.clientX, y: e.clientY,
        pan: e.button === 2 || e.shiftKey,
      };
      this.vyaw = this.vpitch = 0;
      c.classList.add("dragging");
      this._setHover(null);
    });

    c.addEventListener("pointermove", e => {
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        drag.x = e.clientX; drag.y = e.clientY;
        if (drag.pan) this._pan(dx, dy);
        else {
          this.yaw -= dx * 0.008;
          this.pitch = Math.min(1.52, Math.max(-1.52, this.pitch + dy * 0.008));
          this.vyaw = -dx * 0.008; this.vpitch = dy * 0.008;
        }
        this._dirty = true;
      } else if (this.labelsOn) {
        lastMove = e;
        this._hitTest(e);
      }
    });

    const endDrag = e => {
      if (!drag) return;
      drag = null;
      c.classList.remove("dragging");
      // small inertia
      if (Math.abs(this.vyaw) > 0.002 || Math.abs(this.vpitch) > 0.002) this._dirty = true;
    };
    c.addEventListener("pointerup", endDrag);
    c.addEventListener("pointercancel", endDrag);
    c.addEventListener("pointerleave", () => { if (!drag) this._setHover(null); });

    c.addEventListener("wheel", e => {
      e.preventDefault();
      const f = Math.exp(e.deltaY * 0.0012);
      this.dist = Math.min((this.radius || 20) * 30, Math.max(0.4, this.dist * f));
      this._dirty = true;
    }, { passive: false });

    c.addEventListener("dblclick", () => this.fit());
  }

  _pan(dx, dy) {
    const h = this.canvas.clientHeight || 400;
    const worldPerPx = 2 * this.dist * Math.tan(this.fov / 2) / h;
    const { right, up } = this._basis();
    this.target = v3.add(this.target,
      v3.add(v3.scale(right, -dx * worldPerPx), v3.scale(up, dy * worldPerPx)));
  }

  /* ------------------------------------------------ camera math */
  _basis() {
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
    const cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const dir = [cp * cy, cp * sy, sp];               // target -> eye
    const eye = v3.add(this.target, v3.scale(dir, this.dist));
    const fwd = v3.scale(dir, -1);
    let right = v3.norm(v3.cross(fwd, [0, 0, 1]));
    if (!isFinite(right[0]) || v3.len(right) < 1e-6) right = [sy, -cy, 0];
    const up = v3.cross(right, fwd);
    return { eye, fwd, right, up };
  }

  _makeProjector() {
    const { eye, fwd, right, up } = this._basis();
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    const focal = (h / 2) / Math.tan(this.fov / 2);
    const cx = w / 2, cyy = h / 2;
    const near = 0.05;
    const toCam = p => {
      const d = v3.sub(p, eye);
      return [v3.dot(d, right), v3.dot(d, up), v3.dot(d, fwd)];
    };
    const proj = pc => ({
      x: cx + pc[0] / pc[2] * focal,
      y: cyy - pc[1] / pc[2] * focal,
      z: pc[2],
    });
    return { toCam, proj, near, focal };
  }

  /** Project a world segment, near-plane clipped → screen segment or null. */
  _projSeg(P, a, b) {
    let ca = P.toCam(a), cb = P.toCam(b);
    if (ca[2] < P.near && cb[2] < P.near) return null;
    if (ca[2] < P.near || cb[2] < P.near) {
      const t = (P.near - ca[2]) / (cb[2] - ca[2]);
      const mid = [ca[0] + t * (cb[0] - ca[0]), ca[1] + t * (cb[1] - ca[1]), P.near];
      if (ca[2] < P.near) ca = mid; else cb = mid;
    }
    return { a: P.proj(ca), b: P.proj(cb) };
  }

  /* ------------------------------------------------ render loop */
  _loop() {
    this._raf = requestAnimationFrame(() => this._loop());
    // inertia decay
    if (!this.overlay.modal && (Math.abs(this.vyaw) > 0.0004 || Math.abs(this.vpitch) > 0.0004)) {
      this.yaw += this.vyaw; this.pitch = Math.min(1.52, Math.max(-1.52, this.pitch + this.vpitch));
      this.vyaw *= 0.90; this.vpitch *= 0.90;
      this._dirty = true;
    }
    if (this.overlay.modal) this._dirty = true;  // continuous animation
    if (this._dirty) { this._dirty = false; this._render(); }
  }

  _resize() {
    const c = this.canvas;
    const parent = c.parentElement;
    const w = parent ? parent.clientWidth : c.clientWidth;
    const h = parent ? parent.clientHeight : c.clientHeight;
    if (!w || !h) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this._dirty = true;
  }

  _render() {
    const ctx = this.ctx;
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    ctx.clearRect(0, 0, w, h);
    if (!this.model) return;

    const P = this._makeProjector();
    const maxDepth = this.dist + (this.radius || 10);
    const minDepth = Math.max(this.dist - (this.radius || 10), P.near);
    const depthAlpha = z => {
      const t = (z - minDepth) / (maxDepth - minDepth || 1);
      return 1 - Math.min(Math.max(t, 0), 1) * 0.5;
    };

    // ---- ground grid
    ctx.lineWidth = 1;
    ctx.strokeStyle = COLORS.grid;
    ctx.beginPath();
    for (const [a, b] of this.gridLines) {
      const s = this._projSeg(P, a, b);
      if (s) { ctx.moveTo(s.a.x, s.a.y); ctx.lineTo(s.b.x, s.b.y); }
    }
    ctx.stroke();
    ctx.fillStyle = COLORS.gridLabel;
    ctx.font = "600 11px -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const gl of this.gridLabels) {
      const pc = P.toCam(gl.p);
      if (pc[2] > P.near) {
        const s = P.proj(pc);
        ctx.fillText(gl.text, s.x, s.y);
      }
    }

    // ---- depth-sorted drawables: slabs + members
    const overlayActive = this.overlay.deformed || this.overlay.modal;
    const items = [];
    for (const poly of this.slabs) {
      const pts = [];
      let zsum = 0, ok = true;
      for (const p of poly) {
        const pc = P.toCam(p);
        if (pc[2] < P.near) { ok = false; break; }
        zsum += pc[2]; pts.push(P.proj(pc));
      }
      if (ok) items.push({ type: "slab", pts, z: zsum / poly.length });
    }
    this._segsScreen = [];
    for (const seg of this.segs) {
      const s = this._projSeg(P, seg.p1, seg.p2);
      if (!s) continue;
      const z = (s.a.z + s.b.z) / 2;
      items.push({ type: "seg", s, z, seg });
    }
    items.sort((a, b) => b.z - a.z);

    for (const it of items) {
      if (it.type === "slab") {
        if (overlayActive) continue;  // declutter under overlays
        ctx.fillStyle = COLORS.slabFill;
        ctx.strokeStyle = COLORS.slabEdge;
        ctx.lineWidth = 1;
        ctx.beginPath();
        it.pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
        ctx.closePath(); ctx.fill(); ctx.stroke();
      } else {
        const { s, seg } = it;
        const hovered = this._hover && this._hover.uid === seg.uid;
        const alpha = overlayActive ? COLORS.ghost : depthAlpha(it.z);
        ctx.globalAlpha = hovered ? 1 : alpha;
        ctx.strokeStyle = hovered ? "#ffffff" : COLORS[seg.kind] || COLORS.beam;
        ctx.lineWidth = hovered ? 2.5 : (seg.kind === "column" ? 1.8 : 1.3);
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(s.a.x, s.a.y); ctx.lineTo(s.b.x, s.b.y);
        ctx.stroke();
        ctx.globalAlpha = 1;
        this._segsScreen.push({ x1: s.a.x, y1: s.a.y, x2: s.b.x, y2: s.b.y, seg });
      }
    }

    // ---- supports
    const fixed = (this.model.base_fixity || "fixed") === "fixed";
    ctx.fillStyle = COLORS.support;
    for (const p of this.supportPts) {
      const pc = P.toCam(p);
      if (pc[2] < P.near) continue;
      const sp = P.proj(pc);
      const r = Math.min(Math.max(90 / pc[2], 2.5), 6);
      ctx.beginPath();
      if (fixed) ctx.rect(sp.x - r, sp.y - r * 0.35, 2 * r, r * 1.25);
      else { ctx.moveTo(sp.x, sp.y - r * 0.2); ctx.lineTo(sp.x - r, sp.y + r); ctx.lineTo(sp.x + r, sp.y + r); ctx.closePath(); }
      ctx.fill();
    }

    // ---- overlays
    if (overlayActive && this.results) this._renderOverlay(P);

    // ---- axis triad
    this._renderTriad(ctx, w, h);
  }

  /* ------------------------------------------------ overlays */
  _caseData() {
    const r = this.results, name = this.overlay.caseName;
    if (!r || !name) return null;
    return (r.cases && r.cases[name]) || (r.combos && r.combos[name]) || null;
  }

  /** Auto scale so peak displacement ≈ 6% of model radius. */
  autoScale(dispMap) {
    let mx = 0;
    for (const d of Object.values(dispMap)) {
      mx = Math.max(mx, Math.abs(d[0]), Math.abs(d[1]), Math.abs(d[2]));
    }
    if (mx < 1e-12) return 1;
    return (this.radius || 10) * 0.06 / mx;
  }

  _renderOverlay(P) {
    const r = this.results;
    let dispMap = null, factor = 1;

    if (this.overlay.modal && r.modal && r.modal.shapes) {
      const k = String(this.overlay.modeIndex + 1);
      dispMap = r.modal.shapes[k];
      if (!dispMap) return;
      const t = (performance.now() - this._animT0) / 1000;
      factor = this.autoScale(dispMap) * Math.sin(2 * Math.PI * t / 1.8);
    } else if (this.overlay.deformed) {
      const cd = this._caseData();
      if (!cd) return;
      dispMap = cd.node_disp;
      factor = this.autoScale(dispMap) * (this.overlay.scaleMult || 1);
    }
    if (!dispMap) return;

    const ctx = this.ctx;
    const nodes = r.nodes;
    const polys = [];
    for (const m of r.members) {
      const Pi = nodes[m.ni], Pj = nodes[m.nj];
      const di = dispMap[m.ni], dj = dispMap[m.nj];
      if (!Pi || !Pj || !di || !dj) continue;
      const pts = this._deformedPolyline(Pi, Pj, di, dj, factor);
      // project
      const sp = [];
      let zsum = 0, n = 0;
      for (let i = 0; i < pts.length - 1; i++) {
        const s = this._projSeg(P, pts[i], pts[i + 1]);
        if (s) { sp.push(s); zsum += (s.a.z + s.b.z) / 2; n++; }
      }
      if (n) polys.push({ sp, z: zsum / n });
    }
    polys.sort((a, b) => b.z - a.z);
    ctx.strokeStyle = COLORS.deformed;
    ctx.lineWidth = 1.6;
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.globalAlpha = 0.95;
    ctx.beginPath();
    for (const pl of polys) {
      for (const s of pl.sp) { ctx.moveTo(s.a.x, s.a.y); ctx.lineTo(s.b.x, s.b.y); }
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  /** Cubic-Hermite interpolated deformed member (8 segments). */
  _deformedPolyline(Pi, Pj, di, dj, f) {
    const L = v3.len(v3.sub(Pj, Pi)) || 1;
    const e1 = v3.norm(v3.sub(Pj, Pi));
    const ref = Math.abs(e1[2]) > 0.9 ? [1, 0, 0] : [0, 0, 1];
    const e2 = v3.norm(v3.cross(ref, e1));
    const e3 = v3.cross(e1, e2);

    const ti = [di[3], di[4], di[5]], tj = [dj[3], dj[4], dj[5]];
    const ui = v3.dot(di, e1), uj = v3.dot(dj, e1);
    const vi = v3.dot(di, e2), vj = v3.dot(dj, e2);
    const wi = v3.dot(di, e3), wj = v3.dot(dj, e3);
    const vpi = v3.dot(ti, e3), vpj = v3.dot(tj, e3);       // slope of v = rot about e3
    const wpi = -v3.dot(ti, e2), wpj = -v3.dot(tj, e2);     // slope of w = -rot about e2

    const N = 8, out = [];
    for (let k = 0; k <= N; k++) {
      const t = k / N, t2 = t * t, t3 = t2 * t;
      const h1 = 1 - 3 * t2 + 2 * t3, h2 = t - 2 * t2 + t3;
      const h3 = 3 * t2 - 2 * t3, h4 = -t2 + t3;
      const u = ui * (1 - t) + uj * t;
      const v = h1 * vi + h2 * L * vpi + h3 * vj + h4 * L * vpj;
      const w = h1 * wi + h2 * L * wpi + h3 * wj + h4 * L * wpj;
      const base = v3.add(Pi, v3.scale(v3.sub(Pj, Pi), t));
      out.push([
        base[0] + f * (u * e1[0] + v * e2[0] + w * e3[0]),
        base[1] + f * (u * e1[1] + v * e2[1] + w * e3[1]),
        base[2] + f * (u * e1[2] + v * e2[2] + w * e3[2]),
      ]);
    }
    return out;
  }

  /* ------------------------------------------------ triad */
  _renderTriad(ctx, w, h) {
    const { right, up } = this._basis();
    const ox = 44, oy = h - 40, len = 22;
    const axes = [
      { v: [1, 0, 0], c: COLORS.axisX, t: "X" },
      { v: [0, 1, 0], c: COLORS.axisY, t: "Y" },
      { v: [0, 0, 1], c: COLORS.axisZ, t: "Z" },
    ];
    ctx.font = "600 10px -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.lineWidth = 1.5;
    for (const a of axes) {
      const dx = v3.dot(a.v, right), dy = -v3.dot(a.v, up);
      ctx.strokeStyle = a.c; ctx.fillStyle = a.c;
      ctx.beginPath();
      ctx.moveTo(ox, oy); ctx.lineTo(ox + dx * len, oy + dy * len);
      ctx.stroke();
      ctx.fillText(a.t, ox + dx * (len + 8), oy + dy * (len + 8));
    }
  }

  /* ------------------------------------------------ hover / hit test */
  _hitTest(e) {
    const rect = this.canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    let best = null, bestD = 7;  // px threshold
    for (const s of this._segsScreen) {
      const d = distToSeg(mx, my, s.x1, s.y1, s.x2, s.y2);
      if (d < bestD) { bestD = d; best = s.seg; }
    }
    this._setHover(best, mx, my);
  }

  _setHover(seg, mx, my) {
    const changed = (this._hover && this._hover.uid) !== (seg && seg.uid);
    this._hover = seg;
    if (changed) this._dirty = true;
    const tt = this.tooltipEl;
    if (!tt) return;
    if (!seg) { tt.classList.add("hidden"); return; }
    tt.innerHTML = this.getMemberTooltip
      ? this.getMemberTooltip(seg)
      : `<span class="tt-uid">${seg.uid}</span>`;
    tt.classList.remove("hidden");
    const wrapW = this.canvas.clientWidth;
    tt.style.left = Math.min(mx + 14, wrapW - 200) + "px";
    tt.style.top = (my + 14) + "px";
  }
}

function distToSeg(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const l2 = dx * dx + dy * dy;
  let t = l2 ? ((px - x1) * dx + (py - y1) * dy) / l2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}
