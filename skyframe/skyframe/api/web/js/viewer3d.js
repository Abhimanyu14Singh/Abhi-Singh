/* SkyFrame 3D viewer — hand-rolled perspective wireframe on 2D canvas.
   Orbit / pan / zoom, painter's-order depth sort, deformed-shape and
   mode-shape overlays with cubic-Hermite member curves.
   v0.4: shell-force contour quads (diverging blue–white–red about 0). */

/* Shell-force component → index in the 8-value shell_forces arrays
   [Nxx, Nyy, Nxy, Mxx, Myy, Mxy, Vxz, Vyz]. */
export const SHELL_COMPONENTS = {
  M11: { idx: 3, unit: "kN·m/m", label: "M11 — plate bending x" },
  M22: { idx: 4, unit: "kN·m/m", label: "M22 — plate bending y" },
  M12: { idx: 5, unit: "kN·m/m", label: "M12 — twisting" },
  N11: { idx: 0, unit: "kN/m", label: "N11 — membrane x" },
  N22: { idx: 1, unit: "kN/m", label: "N22 — membrane y" },
  N12: { idx: 2, unit: "kN/m", label: "N12 — membrane shear" },
};

/* Diverging scale poles (blue → white → red), symmetric about 0. */
export const CONTOUR_STOPS = ["#2c7fd6", "#f2f5f8", "#e05252"];

function hex2rgb(h) {
  return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
}
const _CSTOPS = CONTOUR_STOPS.map(hex2rgb);

/** t in [-1, 1] → rgb() through blue–white–red. */
export function divergingColor(t) {
  t = Math.max(-1, Math.min(1, isFinite(t) ? t : 0));
  const [a, b] = t < 0 ? [_CSTOPS[1], _CSTOPS[0]] : [_CSTOPS[1], _CSTOPS[2]];
  const s = Math.abs(t);
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * s));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

const COLORS = {
  column: "#5f8fc9",
  beam: "#77879b",
  brace: "#c98500",
  link: "#34c384",                             // v0.5 two-node links (springs)
  spring: "#2fbf74",                           // v0.8 grounded spring supports
  thermal: "#e5a50a",                          // v0.8 ΔT thermal badge
  rigid: "rgba(140, 185, 230, 0.75)",          // v0.9 rigid end zones
  foundation: "rgba(198, 146, 82, 0.95)",      // v0.11 Winkler soil/spring bed
  axial: "#4fd0c7",                            // v0.12 tension/compression-only
  cutFill: "rgba(229, 165, 10, 0.9)",          // v0.13 section-cut plane (amber)
  cutEdge: "rgba(245, 190, 60, 0.95)",
  grid: "rgba(120, 140, 165, 0.16)",
  gridLabel: "rgba(140, 160, 185, 0.55)",
  // v0.14 — per-grid-system tints on the ground plane (index 0 = primary)
  slabFill: "rgba(53, 181, 229, 0.045)",
  slabEdge: "rgba(53, 181, 229, 0.10)",
  wallShell: "rgba(95, 143, 201, 0.22)",       // ShellRegion walls — steel blue tint
  wallShellEdge: "rgba(125, 168, 216, 0.55)",
  slabShell: "rgba(154, 167, 180, 0.15)",      // ShellRegion slabs — neutral
  slabShellEdge: "rgba(154, 167, 180, 0.45)",
  meshLine: "rgba(200, 215, 230, 0.14)",
  wallShellDef: "rgba(53, 181, 229, 0.16)",
  slabShellDef: "rgba(53, 181, 229, 0.10)",
  support: "#8fa3ba",
  deformed: "#35b5e5",
  ghost: 0.16,          // alpha for ghosted base wireframe
  axisX: "#e66767",
  axisY: "#34c384",
  axisZ: "#35b5e5",
};

// v0.14 — per-grid-system ground-line + label tints (index 0 = primary grid)
const GRID3D_TINTS = [
  "rgba(120, 140, 165, 0.18)",
  "rgba(201, 133, 0, 0.26)",
  "rgba(52, 195, 132, 0.24)",
  "rgba(167, 139, 250, 0.26)",
  "rgba(53, 181, 229, 0.22)",
];
const GRID3D_LABEL_TINTS = [
  "rgba(140, 160, 185, 0.55)",
  "rgba(201, 133, 0, 0.7)",
  "rgba(52, 195, 132, 0.7)",
  "rgba(167, 139, 250, 0.75)",
  "rgba(53, 181, 229, 0.7)",
];

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
    this.onMemberClick = opts.onMemberClick || null;

    this.model = null;
    this.results = null;
    this.overlay = { deformed: false, modal: false, buckling: false, bucklingCase: null, caseName: null, modeIndex: 0, scaleMult: 1 };
    this.contours = { on: false, comp: "M11", caseName: null };   // v0.4
    this.labelsOn = true;
    this.highlight = { uids: null, color: "#e0a020" };            // v0.6

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
    // coordinate → node-tag map (deformed-shell fallback via region corners)
    this._coordTag = null;
    if (results && results.nodes) {
      this._coordTag = new Map();
      for (const [t, p] of Object.entries(results.nodes))
        this._coordTag.set(p.map(v => v.toFixed(4)).join(","), t);
    }
    this._dirty = true;
  }

  setOverlay(o) {
    Object.assign(this.overlay, o);
    this._dirty = true;
  }

  /** v0.4 — shell-force contours: {on, comp ("M11"…), caseName}. */
  setContours(c) {
    Object.assign(this.contours, c);
    this._dirty = true;
  }

  /** Per-quad values + symmetric range for the active contour selection,
      or null when unavailable (no results / not a static case). */
  _contourData() {
    const r = this.results, c = this.contours;
    if (!c.on || !r || !r.shell_quads || !r.shell_quads.length) return null;
    const cd = r.cases && r.cases[c.caseName];
    const sf = cd && cd.shell_forces;
    if (!sf) return null;
    const comp = SHELL_COMPONENTS[c.comp] || SHELL_COMPONENTS.M11;
    const vals = [];
    let vmax = 0;
    for (let i = 0; i < r.shell_quads.length; i++) {
      const arr = sf[i] !== undefined ? sf[i] : sf[String(i)];
      const v = (arr && isFinite(arr[comp.idx])) ? arr[comp.idx] : 0;
      vals.push(v);
      vmax = Math.max(vmax, Math.abs(v));
    }
    return { vals, vmax: vmax || 1e-9 };
  }

  setLabels(on) { this.labelsOn = on; if (!on) this._setHover(null); }

  /** v0.6 — highlight a set of member uids (amber yielded / selected member).
      Pass a falsy list to clear. */
  setHighlight(uids, color) {
    this.highlight = {
      uids: (uids && uids.length) ? new Set(uids) : null,
      color: color || "#e0a020",
    };
    this._dirty = true;
  }

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
      rigid_i: mm.rigid_i || 0, rigid_j: mm.rigid_j || 0,   // v0.9 rigid zones
    }));
    // v0.9: members carrying a rigid end offset (for the rigid-zone glyph)
    this.rigidUids = new Set();
    for (const mm of m.members)
      if ((mm.rigid_i || 0) > 0 || (mm.rigid_j || 0) > 0) this.rigidUids.add(mm.uid);

    // v0.11: members on an elastic (Winkler) foundation → soil/spring-bed glyph
    this.foundationSegs = m.members
      .filter(mm => (mm.foundation_ks || 0) > 0 && (mm.foundation_width || 0) > 0)
      .map(mm => ({ p1: mm.pi, p2: mm.pj, uid: mm.uid }));

    // v0.2 shell regions (walls / slabs) as filled quads.
    // v0.5: opening cutouts pre-computed as 3D quads (bilinear on corners).
    const bilin = (cs, u, v) => {
      const [c0, c1, c2, c3] = cs;
      const lerp = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
      return lerp(lerp(c0, c1, u), lerp(c3, c2, u), v);
    };
    this.shellPolys = (m.shells || []).map(s => ({
      corners: s.corners, kind: s.kind, uid: s.uid, behavior: s.behavior,
      openings: (s.openings || []).map(o =>
        [[o.u0, o.v0], [o.u1, o.v0], [o.u1, o.v1], [o.u0, o.v1]]
          .map(([u, v]) => bilin(s.corners, u, v))),
    }));

    // v0.5: two-node links (green glyphs); v0.15: device type → distinct
    // glyph silhouette + a tiny type letter (D/G/H/I)
    this.linkSegs = (m.links || []).map(l => ({
      p1: l.pi, p2: l.pj, uid: l.uid,
      linkType: LINK_LETTERS[l.link_type] !== undefined ? l.link_type : "elastic",
      letter: LINK_LETTERS[l.link_type] || "",
    }));

    // v0.8: grounded spring supports + members carrying thermal loads
    this.springPts = (m.spring_supports || []).map(s => s.point);
    this.thermalUids = new Set();
    for (const p of Object.values(m.patterns || {}))
      for (const t of (p.thermal_loads || [])) this.thermalUids.add(t.member_uid);

    // v0.12: members limited to tension- or compression-only → axial-limit badge
    this.axialLimitUids = new Map();
    for (const mm of m.members) {
      if (mm.axial_limit === "tension" || mm.axial_limit === "compression")
        this.axialLimitUids.set(mm.uid, mm.axial_limit === "tension" ? "T-only" : "C-only");
    }

    let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const s of this.segs) for (const p of [s.p1, s.p2]) {
      for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], p[i]); hi[i] = Math.max(hi[i], p[i]); }
    }
    for (const sh of this.shellPolys) for (const p of sh.corners) {
      for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], p[i]); hi[i] = Math.max(hi[i], p[i]); }
    }
    for (const lk of this.linkSegs) for (const p of [lk.p1, lk.p2]) {
      for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], p[i]); hi[i] = Math.max(hi[i], p[i]); }
    }
    for (const p of this.springPts) {
      for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], p[i]); hi[i] = Math.max(hi[i], p[i]); }
    }
    if (!this.segs.length && !this.shellPolys.length) { lo = [0, 0, 0]; hi = [10, 10, 10]; }
    this._bbox = [lo, hi];

    // v0.13: section cuts — each defined cutting plane becomes a translucent
    // quad at its coordinate along the axis, clipped to optional in-plane
    // ranges (else spanning the model bbox), with a name label at its centroid.
    this.sectionCutPolys = (m.section_cuts || []).map(cut => {
      const axis = (cut.axis === "x" || cut.axis === "y") ? cut.axis : "z";
      const rng = (key, idx) => {
        const r = cut[key];
        return (Array.isArray(r) && r.length === 2 && r.every(isFinite))
          ? [Math.min(r[0], r[1]), Math.max(r[0], r[1])]
          : [lo[idx], hi[idx]];
      };
      const [x0, x1] = rng("x_range", 0);
      const [y0, y1] = rng("y_range", 1);
      const [z0, z1] = rng("z_range", 2);
      const c = cut.coord;
      let corners;
      if (axis === "z") corners = [[x0, y0, c], [x1, y0, c], [x1, y1, c], [x0, y1, c]];
      else if (axis === "x") corners = [[c, y0, z0], [c, y1, z0], [c, y1, z1], [c, y0, z1]];
      else corners = [[x0, c, z0], [x1, c, z0], [x1, c, z1], [x0, c, z1]];
      const centroid = [0, 1, 2].map(i => corners.reduce((a, p) => a + p[i], 0) / 4);
      return { name: cut.name, axis, corners, centroid };
    });

    // ground grid from ALL grid systems (v0.14) — each system's lines are
    // built in LOCAL coords then transformed to GLOBAL (origin + rotation);
    // radial systems draw as tessellated circles + spokes. Each line carries a
    // per-system colour so distinct grids read apart on the ground plane.
    const g = m.grid;
    this.gridLines = []; this.gridLabels = [];
    const systems = (Array.isArray(m.grid_systems) && m.grid_systems.length)
      ? m.grid_systems : (g ? [g] : []);
    const gtf = (sys) => {
      const rot = (sys.rotation || 0) * Math.PI / 180;
      const c = Math.cos(rot), s = Math.sin(rot);
      const ox = sys.origin ? sys.origin[0] : 0, oy = sys.origin ? sys.origin[1] : 0;
      return (lx, ly) => [ox + lx * c - ly * s, oy + lx * s + ly * c, 0];
    };
    systems.forEach((sys, si) => {
      const col = GRID3D_TINTS[si % GRID3D_TINTS.length];
      const lbl = GRID3D_LABEL_TINTS[si % GRID3D_LABEL_TINTS.length];
      if (sys.kind === "radial") {
        const ox = sys.origin[0], oy = sys.origin[1], rot = sys.rotation || 0;
        const rMax = sys.radii[sys.radii.length - 1] || 1;
        const NSEG = 56;
        for (const r of sys.radii) {
          let prev = null;
          for (let k = 0; k <= NSEG; k++) {
            const a = (k / NSEG) * 2 * Math.PI;
            const p = [ox + r * Math.cos(a), oy + r * Math.sin(a), 0];
            if (prev) this.gridLines.push({ a: prev, b: p, color: col });
            prev = p;
          }
        }
        for (const th of sys.theta_deg) {
          const a = (th + rot) * Math.PI / 180, ca = Math.cos(a), sa = Math.sin(a);
          this.gridLines.push({ a: [ox, oy, 0], b: [ox + rMax * ca, oy + rMax * sa, 0], color: col });
          this.gridLabels.push({ p: [ox + rMax * 1.08 * ca, oy + rMax * 1.08 * sa, 0], text: `${th}°`, color: lbl });
        }
      } else if (Array.isArray(sys.x_lines) && sys.x_lines.length &&
                 Array.isArray(sys.y_lines) && sys.y_lines.length) {
        const T = gtf(sys);
        const xs = sys.x_lines, ys = sys.y_lines;
        const mX = Math.max((xs[xs.length - 1] - xs[0]) * 0.12, 1.8);
        const mY = Math.max((ys[ys.length - 1] - ys[0]) * 0.12, 1.8);
        const y0 = ys[0] - mY, y1 = ys[ys.length - 1] + mY;
        const x0 = xs[0] - mX, x1 = xs[xs.length - 1] + mX;
        xs.forEach((x, i) => {
          this.gridLines.push({ a: T(x, y0), b: T(x, y1), color: col });
          this.gridLabels.push({ p: T(x, y1 + mY * 0.35), text: (sys.x_labels && sys.x_labels[i]) || String(i + 1), color: lbl });
        });
        ys.forEach((y, i) => {
          this.gridLines.push({ a: T(x0, y), b: T(x1, y), color: col });
          this.gridLabels.push({ p: T(x0 - mX * 0.35, y), text: (sys.y_labels && sys.y_labels[i]) || String(i + 1), color: lbl });
        });
      }
    });

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
        button: e.button, moved: 0,
      };
      this.vyaw = this.vpitch = 0;
      c.classList.add("dragging");
      this._setHover(null);
    });

    c.addEventListener("pointermove", e => {
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        drag.x = e.clientX; drag.y = e.clientY;
        drag.moved += Math.abs(dx) + Math.abs(dy);
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
      const wasClick = drag.button === 0 && drag.moved < 5;
      drag = null;
      c.classList.remove("dragging");
      // small inertia
      if (Math.abs(this.vyaw) > 0.002 || Math.abs(this.vpitch) > 0.002) this._dirty = true;
      // plain left-click (no drag) → member pick
      if (wasClick && this.onMemberClick && e && e.type === "pointerup") {
        const rect = c.getBoundingClientRect();
        const mx = e.clientX - rect.left, my = e.clientY - rect.top;
        let best = null, bestD = 8;
        for (const s of this._segsScreen) {
          const d = distToSeg(mx, my, s.x1, s.y1, s.x2, s.y2);
          if (d < bestD) { bestD = d; best = s.seg; }
        }
        this.onMemberClick(best);
      }
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
    const animating = this.overlay.modal || this.overlay.buckling;
    if (!animating && (Math.abs(this.vyaw) > 0.0004 || Math.abs(this.vpitch) > 0.0004)) {
      this.yaw += this.vyaw; this.pitch = Math.min(1.52, Math.max(-1.52, this.pitch + this.vpitch));
      this.vyaw *= 0.90; this.vpitch *= 0.90;
      this._dirty = true;
    }
    if (animating) this._dirty = true;  // continuous animation
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

    // ---- ground grid (v0.14: multiple systems, batched by per-system colour)
    ctx.lineWidth = 1;
    let curColor = null;
    for (const gl of this.gridLines) {
      const color = gl.color || COLORS.grid;
      if (color !== curColor) {
        if (curColor !== null) ctx.stroke();
        ctx.strokeStyle = color; ctx.beginPath(); curColor = color;
      }
      const s = this._projSeg(P, gl.a, gl.b);
      if (s) { ctx.moveTo(s.a.x, s.a.y); ctx.lineTo(s.b.x, s.b.y); }
    }
    if (curColor !== null) ctx.stroke();
    ctx.font = "600 11px -apple-system, 'Segoe UI', sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const gl of this.gridLabels) {
      const pc = P.toCam(gl.p);
      if (pc[2] > P.near) {
        const s = P.proj(pc);
        ctx.fillStyle = gl.color || COLORS.gridLabel;
        ctx.fillText(gl.text, s.x, s.y);
      }
    }

    // ---- depth-sorted drawables: slabs + shell regions + members
    const overlayActive = this.overlay.deformed || this.overlay.modal || this.overlay.buckling;
    const contour = overlayActive ? null : this._contourData();   // v0.4
    const items = [];
    for (const poly of this.slabs) {
      if (contour) continue;               // declutter under contour fields
      const pts = [];
      let zsum = 0, ok = true;
      for (const p of poly) {
        const pc = P.toCam(p);
        if (pc[2] < P.near) { ok = false; break; }
        zsum += pc[2]; pts.push(P.proj(pc));
      }
      if (ok) items.push({ type: "slab", pts, z: zsum / poly.length });
    }
    for (const sh of (this.shellPolys || [])) {
      if (contour && sh.behavior === "shell") continue;   // contour quads replace the fill
      const pts = [];
      let zsum = 0, ok = true;
      for (const p of sh.corners) {
        const pc = P.toCam(p);
        if (pc[2] < P.near) { ok = false; break; }
        zsum += pc[2]; pts.push(P.proj(pc));
      }
      if (!ok) continue;
      // v0.5: project opening cutouts (skipped if any point clips the near plane)
      const holes = [];
      for (const q of (sh.openings || [])) {
        const hp = [];
        let hok = true;
        for (const p of q) {
          const pc = P.toCam(p);
          if (pc[2] < P.near) { hok = false; break; }
          hp.push(P.proj(pc));
        }
        if (hok) holes.push(hp);
      }
      items.push({ type: "shell", pts, holes, z: zsum / sh.corners.length, kind: sh.kind });
    }
    // v0.5: links — green device glyphs, depth-sorted with everything else
    for (const lk of (this.linkSegs || [])) {
      const s = this._projSeg(P, lk.p1, lk.p2);
      if (!s) continue;
      items.push({ type: "link", s, z: (s.a.z + s.b.z) / 2, lk });
    }
    // v0.13: section-cut planes — translucent amber quads, depth-sorted
    for (const cut of (this.sectionCutPolys || [])) {
      const pts = [];
      let zsum = 0, ok = true;
      for (const p of cut.corners) {
        const pc = P.toCam(p);
        if (pc[2] < P.near) { ok = false; break; }
        zsum += pc[2]; pts.push(P.proj(pc));
      }
      if (!ok) continue;
      items.push({ type: "cut", pts, z: zsum / cut.corners.length, cut });
    }
    // v0.4 — shell-force contour quads (colored by component value)
    if (contour && this.results && this._nodeXYZ) {
      this.results.shell_quads.forEach((q, i) => {
        const pts = [];
        let zsum = 0, ok = true;
        for (const t of q.nodes) {
          const p = this._nodeXYZ[t];
          if (!p) { ok = false; break; }
          const pc = P.toCam(p);
          if (pc[2] < P.near) { ok = false; break; }
          zsum += pc[2]; pts.push(P.proj(pc));
        }
        if (!ok) return;
        items.push({
          type: "cq", pts, z: zsum / q.nodes.length,
          fill: divergingColor(contour.vals[i] / contour.vmax),
        });
      });
    }
    this._segsScreen = [];
    this._linkBadges = [];   // v0.15 — link device-type letters, drawn on top
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
      } else if (it.type === "cq") {
        // contour quad: solid fill, thin dark seam between cells
        ctx.beginPath();
        it.pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
        ctx.closePath();
        ctx.fillStyle = it.fill;
        ctx.globalAlpha = 0.92;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = "rgba(13, 17, 23, 0.45)";
        ctx.lineWidth = 0.7;
        ctx.stroke();
      } else if (it.type === "shell") {
        // translucent shell region with even-odd opening cutouts (v0.5);
        // ghosted outline only under overlays
        const wall = it.kind === "wall";
        ctx.beginPath();
        it.pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
        ctx.closePath();
        for (const hp of (it.holes || [])) {
          hp.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
          ctx.closePath();
        }
        if (overlayActive) {
          ctx.globalAlpha = COLORS.ghost;
          ctx.strokeStyle = wall ? COLORS.wallShellEdge : COLORS.slabShellEdge;
          ctx.lineWidth = 1;
          ctx.stroke();
          ctx.globalAlpha = 1;
        } else {
          ctx.fillStyle = wall ? COLORS.wallShell : COLORS.slabShell;
          ctx.strokeStyle = wall ? COLORS.wallShellEdge : COLORS.slabShellEdge;
          ctx.lineWidth = 1.2;
          ctx.fill("evenodd"); ctx.stroke();
        }
      } else if (it.type === "cut") {
        // v0.13: translucent cutting plane (amber) with a dashed border
        ctx.beginPath();
        it.pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
        ctx.closePath();
        ctx.fillStyle = COLORS.cutFill;
        ctx.globalAlpha = overlayActive ? 0.10 : 0.18;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = COLORS.cutEdge;
        ctx.lineWidth = 1.4;
        ctx.setLineDash([6, 4]);
        ctx.stroke();
        ctx.setLineDash([]);
      } else if (it.type === "link") {
        const { s, lk } = it;
        ctx.globalAlpha = overlayActive ? COLORS.ghost : 1;
        ctx.strokeStyle = COLORS.link;
        ctx.lineWidth = 1.6;
        ctx.lineJoin = "round"; ctx.lineCap = "round";
        ctx.beginPath();
        drawLinkDevice(ctx, s.a.x, s.a.y, s.b.x, s.b.y, (lk && lk.linkType) || "elastic");
        ctx.stroke();
        ctx.globalAlpha = 1;
        if (lk && lk.letter && !overlayActive)
          this._linkBadges.push({
            x: (s.a.x + s.b.x) / 2, y: (s.a.y + s.b.y) / 2 - 11, letter: lk.letter });
      } else {
        const { s, seg } = it;
        const hovered = this._hover && this._hover.uid === seg.uid;
        const hl = this.highlight.uids && this.highlight.uids.has(seg.uid);
        const alpha = overlayActive ? COLORS.ghost : depthAlpha(it.z);
        ctx.globalAlpha = (hovered || hl) ? 1 : alpha;
        ctx.strokeStyle = hovered ? "#ffffff"
          : hl ? this.highlight.color : (COLORS[seg.kind] || COLORS.beam);
        ctx.lineWidth = (hovered || hl) ? 2.6 : (seg.kind === "column" ? 1.8 : 1.3);
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(s.a.x, s.a.y); ctx.lineTo(s.b.x, s.b.y);
        ctx.stroke();
        ctx.globalAlpha = 1;
        this._segsScreen.push({ x1: s.a.x, y1: s.a.y, x2: s.b.x, y2: s.b.y, seg });
      }
    }

    // ---- FE shell mesh lines (subtle, once analyzed)
    if (!overlayActive && !contour && this.results && this.results.shell_quads &&
        this.results.shell_quads.length && this._nodeXYZ) {
      ctx.strokeStyle = COLORS.meshLine;
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      for (const q of this.results.shell_quads) {
        const pts = [];
        let ok = true;
        for (const t of q.nodes) {
          const p = this._nodeXYZ[t];
          if (!p) { ok = false; break; }
          const pc = P.toCam(p);
          if (pc[2] < P.near) { ok = false; break; }
          pts.push(P.proj(pc));
        }
        if (!ok) continue;
        pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
        ctx.closePath();
      }
      ctx.stroke();
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

    // ---- v0.8 spring supports: grounded green coil + hatched ground symbol
    for (const p of (this.springPts || [])) {
      const pc = P.toCam(p);
      if (pc[2] < P.near) continue;
      const sp = P.proj(pc);
      const r = Math.min(Math.max(120 / pc[2], 5), 13);
      ctx.globalAlpha = overlayActive ? 0.4 : 1;
      drawSpringGlyph(ctx, sp.x, sp.y, r, COLORS.spring);
      ctx.globalAlpha = 1;
    }

    // ---- v0.9 rigid end zones: thicker pale-blue stubs at member ends
    if (this.rigidUids && this.rigidUids.size && !overlayActive) {
      ctx.strokeStyle = COLORS.rigid;
      ctx.lineWidth = 4.5;
      ctx.lineCap = "butt";
      for (const s of this._segsScreen) {
        if (!this.rigidUids.has(s.seg.uid)) continue;
        const p1 = s.seg.p1, p2 = s.seg.p2;
        const L = Math.hypot(p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]) || 1;
        const dx = s.x2 - s.x1, dy = s.y2 - s.y1;
        const fi = Math.min((s.seg.rigid_i || 0) / L, 0.49);
        const fj = Math.min((s.seg.rigid_j || 0) / L, 0.49);
        if (fi > 0) {
          ctx.beginPath();
          ctx.moveTo(s.x1, s.y1);
          ctx.lineTo(s.x1 + dx * fi, s.y1 + dy * fi);
          ctx.stroke();
        }
        if (fj > 0) {
          ctx.beginPath();
          ctx.moveTo(s.x2, s.y2);
          ctx.lineTo(s.x2 - dx * fj, s.y2 - dy * fj);
          ctx.stroke();
        }
      }
    }

    // ---- v0.11 elastic-foundation soil/spring bed under members on a
    // Winkler foundation: a row of small spring coils dropping to a ground
    // line, DEPTH metres below the member (world −Z).
    if (this.foundationSegs && this.foundationSegs.length && !overlayActive) {
      const DEPTH = 0.55;
      ctx.strokeStyle = COLORS.foundation;
      ctx.lineWidth = 1.4;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      for (const f of this.foundationSegs) {
        const p1 = f.p1, p2 = f.p2;
        const n = Math.max(3, Math.min(12,
          Math.round(Math.hypot(p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]) / 1.0)));
        const gpts = [];        // projected ground points (for the ground line)
        for (let k = 0; k <= n; k++) {
          const t = k / n;
          const bw = [p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t,
            p1[2] + (p2[2] - p1[2]) * t];
          const gw = [bw[0], bw[1], bw[2] - DEPTH];
          const bc = P.toCam(bw), gc = P.toCam(gw);
          if (bc[2] < P.near || gc[2] < P.near) { gpts.push(null); continue; }
          const bs = P.proj(bc), gs = P.proj(gc);
          gpts.push(gs);
          // simple 2-kink coil in screen space between member point and ground
          const mx = bs.x - gs.x, my = bs.y - gs.y;
          const perp = { x: -my, y: mx };
          const pl = Math.hypot(perp.x, perp.y) || 1;
          const amp = Math.min(6, pl * 0.16);
          const ax = perp.x / pl * amp, ay = perp.y / pl * amp;
          ctx.beginPath();
          ctx.moveTo(bs.x, bs.y);
          ctx.lineTo(bs.x - mx * 0.33 + ax, bs.y - my * 0.33 + ay);
          ctx.lineTo(bs.x - mx * 0.66 - ax, bs.y - my * 0.66 - ay);
          ctx.lineTo(gs.x, gs.y);
          ctx.stroke();
        }
        // ground line + hatch ticks
        ctx.beginPath();
        let started = false;
        for (const g of gpts) {
          if (!g) { started = false; continue; }
          if (!started) { ctx.moveTo(g.x, g.y); started = true; }
          else ctx.lineTo(g.x, g.y);
        }
        ctx.stroke();
      }
    }

    // ---- v0.8 ΔT badges on members carrying thermal loads
    if (this.thermalUids && this.thermalUids.size && !overlayActive) {
      ctx.font = "700 9px -apple-system, 'Segoe UI', sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      const drawn = new Set();
      for (const s of this._segsScreen) {
        if (!this.thermalUids.has(s.seg.uid) || drawn.has(s.seg.uid)) continue;
        drawn.add(s.seg.uid);
        const mx = (s.x1 + s.x2) / 2, my = (s.y1 + s.y2) / 2;
        ctx.beginPath();
        ctx.arc(mx, my, 8, 0, 2 * Math.PI);
        ctx.fillStyle = "rgba(229,165,10,0.18)";
        ctx.fill();
        ctx.strokeStyle = COLORS.thermal; ctx.lineWidth = 1;
        ctx.stroke();
        ctx.fillStyle = COLORS.thermal;
        ctx.fillText("ΔT", mx, my + 0.5);
      }
    }

    // ---- v0.15 link device-type letters (D/G/H/I) beside their glyphs
    if (this._linkBadges && this._linkBadges.length) {
      ctx.font = "700 8.5px -apple-system, 'Segoe UI', sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      for (const bLk of this._linkBadges) {
        ctx.beginPath();
        ctx.arc(bLk.x, bLk.y, 6.5, 0, 2 * Math.PI);
        ctx.fillStyle = "rgba(52,195,132,0.15)";
        ctx.fill();
        ctx.strokeStyle = COLORS.link; ctx.lineWidth = 1;
        ctx.stroke();
        ctx.fillStyle = COLORS.link;
        ctx.fillText(bLk.letter, bLk.x, bLk.y + 0.5);
      }
    }

    // ---- v0.12 T-only / C-only badges on axial-limited members
    if (this.axialLimitUids && this.axialLimitUids.size && !overlayActive) {
      ctx.font = "700 8.5px -apple-system, 'Segoe UI', sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      const drawn = new Set();
      for (const s of this._segsScreen) {
        const label = this.axialLimitUids.get(s.seg.uid);
        if (!label || drawn.has(s.seg.uid)) continue;
        drawn.add(s.seg.uid);
        const mx = (s.x1 + s.x2) / 2, my = (s.y1 + s.y2) / 2;
        const w = 30, h = 13;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(mx - w / 2, my - h / 2, w, h, 6.5);
        else ctx.rect(mx - w / 2, my - h / 2, w, h);
        ctx.fillStyle = "rgba(79,208,199,0.18)";
        ctx.fill();
        ctx.strokeStyle = COLORS.axial; ctx.lineWidth = 1;
        ctx.stroke();
        ctx.fillStyle = COLORS.axial;
        ctx.fillText(label, mx, my + 0.5);
      }
    }

    // ---- v0.13 section-cut plane name labels (at each plane centroid)
    if (this.sectionCutPolys && this.sectionCutPolys.length) {
      ctx.font = "700 10px -apple-system, 'Segoe UI', sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      for (const cut of this.sectionCutPolys) {
        const pc = P.toCam(cut.centroid);
        if (pc[2] < P.near) continue;
        const sp = P.proj(pc);
        const label = `✂ ${cut.name}`;
        const w = ctx.measureText(label).width + 12;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(sp.x - w / 2, sp.y - 8, w, 16, 7);
        else ctx.rect(sp.x - w / 2, sp.y - 8, w, 16);
        ctx.fillStyle = "rgba(24,20,8,0.72)";
        ctx.fill();
        ctx.strokeStyle = COLORS.cutEdge; ctx.lineWidth = 1;
        ctx.stroke();
        ctx.fillStyle = "#f5be3c";
        ctx.fillText(label, sp.x, sp.y + 0.5);
      }
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
    if (name.startsWith("rs:"))                    // v0.3 response-spectrum case
      return (r.rs_cases && r.rs_cases[name.slice(3)]) || null;
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

    if (this.overlay.buckling && r.buckling) {
      // v0.10 — animate a buckling mode shape (same shape as modal shapes)
      const bc = r.buckling[this.overlay.bucklingCase];
      const modes = bc && bc.modes;
      const k = String(this.overlay.modeIndex + 1);
      dispMap = modes && modes[k];
      if (!dispMap) return;
      const t = (performance.now() - this._animT0) / 1000;
      factor = this.autoScale(dispMap) * Math.sin(2 * Math.PI * t / 1.8);
    } else if (this.overlay.modal && r.modal && r.modal.shapes) {
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

    this._renderDeformedShells(P, dispMap, factor);

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

  /** Deformed shell regions: displace FE mesh quads when shell_quads +
      node_disp are available, else fall back to region corners matched to
      result nodes by coordinate. Painter-sorted translucent quads. */
  _renderDeformedShells(P, dispMap, factor) {
    const r = this.results;
    if (!r) return;
    const ctx = this.ctx;
    const polys = [];
    const project = world => {
      const pts = [];
      let zsum = 0;
      for (const p of world) {
        const pc = P.toCam(p);
        if (pc[2] < P.near) return null;
        zsum += pc[2]; pts.push(P.proj(pc));
      }
      return { pts, z: zsum / world.length };
    };
    const move = (p, d) => d ?
      [p[0] + factor * d[0], p[1] + factor * d[1], p[2] + factor * d[2]] : p;

    if (r.shell_quads && r.shell_quads.length) {
      const kindOf = {};
      for (const sh of (this.shellPolys || [])) kindOf[sh.uid] = sh.kind;
      for (const q of r.shell_quads) {
        const world = [];
        let ok = true;
        for (const t of q.nodes) {
          const p = r.nodes[t];
          if (!p) { ok = false; break; }
          world.push(move(p, dispMap[t]));
        }
        if (!ok) continue;
        const pr = project(world);
        if (pr) polys.push({ ...pr, kind: kindOf[q.region] || "slab" });
      }
    } else if (this.shellPolys && this.shellPolys.length && this._coordTag) {
      for (const sh of this.shellPolys) {
        const world = sh.corners.map(p => {
          const t = this._coordTag.get(p.map(v => v.toFixed(4)).join(","));
          return move(p, t ? dispMap[t] : null);
        });
        const pr = project(world);
        if (pr) polys.push({ ...pr, kind: sh.kind });
      }
    }
    if (!polys.length) return;

    polys.sort((a, b) => b.z - a.z);
    ctx.lineWidth = 0.9;
    for (const qp of polys) {
      ctx.fillStyle = qp.kind === "wall" ? COLORS.wallShellDef : COLORS.slabShellDef;
      ctx.strokeStyle = "rgba(53, 181, 229, 0.30)";
      ctx.beginPath();
      qp.pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
      ctx.closePath();
      ctx.fill(); ctx.stroke();
    }
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

/** v0.8 — grounded spring glyph in screen space: a coil hanging from the base
    point down to a hatched ground line. */
function drawSpringGlyph(ctx, x, y, r, color) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.6;
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  const gy = y + r * 1.15;                 // ground line below the node (screen y down)
  const a = r * 0.55, n = 4, span = gy - y;
  ctx.beginPath();
  ctx.moveTo(x, y);
  for (let k = 1; k <= n; k++)
    ctx.lineTo(x + (k % 2 ? a : -a), y + span * k / (n + 1));
  ctx.lineTo(x, gy);
  // ground line
  ctx.moveTo(x - r * 0.9, gy);
  ctx.lineTo(x + r * 0.9, gy);
  // hatches
  for (let k = -1; k <= 1; k++) {
    const x0 = x + k * r * 0.6;
    ctx.moveTo(x0, gy);
    ctx.lineTo(x0 - r * 0.45, gy + r * 0.45);
  }
  ctx.stroke();
  ctx.restore();
}

/* v0.15 — link device types: tiny type letter per non-elastic device. */
const LINK_LETTERS = { elastic: "", damper: "D", gap: "G", hook: "H", isolator: "I" };

/** v0.15 — screen-space link device glyph (mirrors the plan/elevation SVG
    glyphs): damper = dashpot, gap = open jaws, hook = interlocked chain
    rings, isolator = plate·roller·plate bearing; elastic = classic zigzag.
    Adds to the CURRENT path — caller begins/strokes. */
function drawLinkDevice(ctx, x1, y1, x2, y2, type, ampPx = 5) {
  if (!type || type === "elastic") { drawZigzag(ctx, x1, y1, x2, y2); return; }
  const dx = x2 - x1, dy = y2 - y1;
  const L = Math.hypot(dx, dy) || 1;
  const ux = dx / L, uy = dy / L;
  const nx = -uy, ny = ux;
  const a = Math.min(ampPx, L / 5);
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
  const Q = (d, o) => [mx + ux * d + nx * o * a, my + uy * d + ny * o * a];
  const mv = p => ctx.moveTo(p[0], p[1]);
  const ln = p => ctx.lineTo(p[0], p[1]);
  const ring = (d, r) => {
    const c = Q(d, 0);
    ctx.moveTo(c[0] + r, c[1]);
    ctx.arc(c[0], c[1], r, 0, 2 * Math.PI);
  };
  const b = Math.min(L * 0.18, a * 1.8);          // device half-length
  const rods = (dA, dB) => {
    ctx.moveTo(x1, y1); ln(Q(dA, 0));
    ctx.moveTo(x2, y2); ln(Q(dB, 0));
  };
  if (type === "damper") {                        // dashpot: piston in cylinder
    rods(-b, b * 0.3);
    mv(Q(b, 1)); ln(Q(-b, 1)); ln(Q(-b, -1)); ln(Q(b, -1));
    mv(Q(b * 0.3, 0.72)); ln(Q(b * 0.3, -0.72));
  } else if (type === "gap") {                    // open jaws with clearance
    const g = Math.min(b * 0.45, a * 0.5);
    rods(-b, b);
    mv(Q(-b, 0)); ln(Q(-g, 0)); mv(Q(-g, 0.9)); ln(Q(-g, -0.9));
    mv(Q(-g, 0.9)); ln(Q(-g * 0.1, 0.9)); mv(Q(-g, -0.9)); ln(Q(-g * 0.1, -0.9));
    mv(Q(b, 0)); ln(Q(g, 0)); mv(Q(g, 0.9)); ln(Q(g, -0.9));
    mv(Q(g, 0.9)); ln(Q(g * 0.1, 0.9)); mv(Q(g, -0.9)); ln(Q(g * 0.1, -0.9));
  } else if (type === "hook") {                   // interlocked chain rings
    const r = a * 0.8;
    rods(-r * 1.7, r * 1.7);
    ring(-r * 0.62, r); ring(r * 0.62, r);
  } else if (type === "isolator") {               // bearing: plate·roller·plate
    const r = Math.min(a * 0.6, b * 0.55);
    rods(-b * 0.7, b * 0.7);
    mv(Q(-b * 0.7, 1)); ln(Q(-b * 0.7, -1));
    mv(Q(b * 0.7, 1)); ln(Q(b * 0.7, -1));
    ring(0, r);
  } else {
    drawZigzag(ctx, x1, y1, x2, y2);
  }
}

/** v0.5 — screen-space zigzag path between two points (link/spring glyph). */
function drawZigzag(ctx, x1, y1, x2, y2, ampPx = 4, cycles = 5) {
  const dx = x2 - x1, dy = y2 - y1;
  const L = Math.hypot(dx, dy) || 1;
  const amp = Math.min(ampPx, L / 6);
  const nx = -dy / L, ny = dx / L;
  const n = cycles * 2 + 2;
  ctx.moveTo(x1, y1);
  for (let k = 1; k < n; k++) {
    const t = k / n;
    const off = (k % 2 ? 1 : -1) * amp * (k === 1 || k === n - 1 ? 0.5 : 1);
    ctx.lineTo(x1 + dx * t + nx * off, y1 + dy * t + ny * off);
  }
  ctx.lineTo(x2, y2);
}
