/* SkyFrame — v0.21 SECTION DESIGNER
   SVG polygon + rebar fiber-section editor with a live shoelace property
   preview and a PMM interaction-diagram viewer. Editing happens in mm on a
   snapped grid; the model stores meters (rebar areas in m²). No frameworks.

   Wiring is callback-based (same pattern as PlanEditor / LoadsEditor):
     getModel()                 → working model dict
     onUpsert(section)          → POST /api/sections/designer upsert + adopt
     onDelete(name)             → POST /api/sections/designer delete + adopt
     onPmm(name, axis)          → POST /api/sections/designer/pmm
     isSectionInUse(name)       → companion FrameSection used by members?
     toast(title, msg, type)    → app toast
     onClose()                  → refresh views after the dialog closes      */

import { designerProps, defaultMaterial } from "./modeledit.js";

const MM = 1000;                                   // m → mm
const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sci = v => (v == null || !isFinite(v)) ? "—" : Number(v).toExponential(3);
const fmm = v => {
  const mm = v * MM;
  return Math.abs(mm - Math.round(mm)) < 1e-6 ? String(Math.round(mm)) : mm.toFixed(1);
};

export class SectionDesigner {
  constructor(opts) {
    this.opts = opts;
    this.svg = document.getElementById("dsSvg");
    this.sec = null;            // working copy (meters)
    this.savedName = null;      // model key this.sec was loaded from (null = new)
    this.pending = [];          // in-progress polygon vertices (mm pairs)
    this.mode = "poly";         // "poly" | "rebar"
    this.snap = 10;             // grid snap (mm)
    this.barArea = 500;         // rebar area (mm²)
    this.view = { s: 0.55, ox: 300, oy: 260 };   // px per mm + origin px
    this.cursor = null;         // snapped [y, z] mm under the mouse
    this.pmmData = null;
    this.pmmAxis = "33";
    this._pan = null;
    this._resize = () => { if (this.isOpen()) this.render(); };
    this._wire();
  }

  $(id) { return document.getElementById(id); }
  model() { return this.opts.getModel(); }
  isOpen() { return !this.$("designerModal").classList.contains("hidden"); }

  /* ------------------------------------------------ open / close */
  open(name) {
    const m = this.model();
    m.designer_sections = m.designer_sections || {};
    if (name && m.designer_sections[name]) this.load(name);
    else {
      const first = Object.keys(m.designer_sections)[0];
      if (first) this.load(first); else this.newSection();
    }
    this.$("designerModal").classList.remove("hidden");
    this.hidePmm();
    window.addEventListener("resize", this._resize);
    this.fit();
    this.renderAll();
  }

  close() {
    this.$("designerModal").classList.add("hidden");
    window.removeEventListener("resize", this._resize);
    this.pending = [];
    if (this.opts.onClose) this.opts.onClose();
  }

  /** Escape: PMM panel → pending polygon → dialog (outermost first). */
  handleEscape() {
    if (!this.$("dsPmmPanel").classList.contains("hidden")) { this.hidePmm(); return; }
    if (this.pending.length) { this.pending = []; this.render(); return; }
    this.close();
  }

  /* ------------------------------------------------ section state */
  load(name) {
    const ds = this.model().designer_sections[name];
    this.sec = JSON.parse(JSON.stringify(ds));
    this.savedName = name;
    this.pending = [];
    this.pmmData = null;
    this.hidePmm();
    this._syncInputs();
  }

  newSection() {
    const m = this.model();
    let i = 1;
    while ((m.designer_sections || {})[`DS${i}`] || m.sections[`DS${i}`]) i++;
    this.sec = { name: `DS${i}`, polygons: [], rebar: [] };
    this.savedName = null;
    this.pending = [];
    this.pmmData = null;
    this.hidePmm();
    this._syncInputs();
  }

  _syncInputs() {
    this.$("dsName").value = this.sec.name;
    this.$("dsSnap").value = String(this.snap);
    this.$("dsBarArea").value = String(this.barArea);
    const matSel = this.$("dsMaterial");
    const prev = matSel.value;
    matSel.textContent = "";
    for (const n of Object.keys(this.model().materials)) {
      const o = document.createElement("option");
      o.value = n; o.textContent = n;
      matSel.appendChild(o);
    }
    if (prev && this.model().materials[prev]) matSel.value = prev;
  }

  /* ------------------------------------------------ wiring */
  _wire() {
    this.$("dsNew").addEventListener("click", () => { this.newSection(); this.renderAll(); });
    this.$("dsName").addEventListener("change", e => {
      const v = e.target.value.trim();
      if (v) this.sec.name = v; else e.target.value = this.sec.name;
    });
    this.$("dsSnap").addEventListener("change", e => {
      const v = parseFloat(e.target.value);
      if (isFinite(v) && v > 0) this.snap = v; else e.target.value = String(this.snap);
      this.render();
    });
    this.$("dsBarArea").addEventListener("change", e => {
      const v = parseFloat(e.target.value);
      if (isFinite(v) && v > 0) this.barArea = v; else e.target.value = String(this.barArea);
    });
    document.querySelectorAll("#dsModeToggle .seg-btn").forEach(b =>
      b.addEventListener("click", () => {
        this.mode = b.dataset.dsmode === "rebar" ? "rebar" : "poly";
        document.querySelectorAll("#dsModeToggle .seg-btn").forEach(x =>
          x.classList.toggle("is-active", x === b));
        this.pending = [];
        this._syncHint();
        this.render();
      }));
    this.$("dsSave").addEventListener("click", () => this.save());
    this.$("dsDelete").addEventListener("click", () => this.remove());
    this.$("dsPmmBtn").addEventListener("click", () => this.showPmm());
    this.$("dsPmmClose").addEventListener("click", () => this.hidePmm());
    document.querySelectorAll("#dsAxisToggle .seg-btn").forEach(b =>
      b.addEventListener("click", () => {
        this.pmmAxis = b.dataset.axis === "22" ? "22" : "33";
        document.querySelectorAll("#dsAxisToggle .seg-btn").forEach(x =>
          x.classList.toggle("is-active", x === b));
        this.showPmm();
      }));

    /* canvas: click to place, wheel to zoom, right-drag to pan */
    const svg = this.svg;
    svg.addEventListener("contextmenu", e => e.preventDefault());
    svg.addEventListener("mousedown", e => {
      if (e.button !== 2) return;
      this._pan = { x: e.clientX, y: e.clientY, ox: this.view.ox, oy: this.view.oy };
    });
    window.addEventListener("mousemove", e => {
      if (!this._pan) return;
      this.view.ox = this._pan.ox + (e.clientX - this._pan.x);
      this.view.oy = this._pan.oy + (e.clientY - this._pan.y);
      this.render();
    });
    window.addEventListener("mouseup", () => { this._pan = null; });
    svg.addEventListener("mousemove", e => {
      if (this._pan) return;
      this.cursor = this._snapPoint(e);
      this._syncReadout();
      this.render();
    });
    svg.addEventListener("mouseleave", () => { this.cursor = null; this._syncReadout(); this.render(); });
    svg.addEventListener("wheel", e => {
      e.preventDefault();
      const r = svg.getBoundingClientRect();
      const px = e.clientX - r.left, py = e.clientY - r.top;
      const [my, mz] = this._toMm(px, py);
      const f = e.deltaY < 0 ? 1.2 : 1 / 1.2;
      this.view.s = Math.min(25, Math.max(0.02, this.view.s * f));
      this.view.ox = px - my * this.view.s;
      this.view.oy = py + mz * this.view.s;
      this.render();
    }, { passive: false });
    svg.addEventListener("dblclick", () => { this.fit(); this.render(); });
    svg.addEventListener("click", e => this._onClick(e));
  }

  /* ------------------------------------------------ coordinates (mm ↔ px) */
  _toPx(y, z) { return [this.view.ox + y * this.view.s, this.view.oy - z * this.view.s]; }
  _toMm(px, py) { return [(px - this.view.ox) / this.view.s, (this.view.oy - py) / this.view.s]; }

  _snapPoint(e) {
    const r = this.svg.getBoundingClientRect();
    const [y, z] = this._toMm(e.clientX - r.left, e.clientY - r.top);
    const sn = this.snap;
    return [Math.round(y / sn) * sn, Math.round(z / sn) * sn];
  }

  fit() {
    const r = this.svg.getBoundingClientRect();
    const W = r.width || 640, H = r.height || 480;
    let ymin = Infinity, ymax = -Infinity, zmin = Infinity, zmax = -Infinity;
    for (const p of (this.sec ? this.sec.polygons : []))
      for (const [y, z] of p.vertices) {
        ymin = Math.min(ymin, y * MM); ymax = Math.max(ymax, y * MM);
        zmin = Math.min(zmin, z * MM); zmax = Math.max(zmax, z * MM);
      }
    if (!isFinite(ymin)) { ymin = -400; ymax = 400; zmin = -400; zmax = 400; }
    const span = Math.max(ymax - ymin, zmax - zmin, 100) * 1.35;
    this.view.s = Math.min(W, H) / span;
    this.view.ox = W / 2 - ((ymin + ymax) / 2) * this.view.s;
    this.view.oy = H / 2 + ((zmin + zmax) / 2) * this.view.s;
  }

  /* ------------------------------------------------ editing */
  _onClick(e) {
    if (!this.sec) return;
    const pt = this._snapPoint(e);
    if (this.mode === "rebar") {
      this.sec.rebar.push({
        y: pt[0] / MM, z: pt[1] / MM,
        area: this.barArea * 1e-6,
        material: this.$("dsMaterial").value || defaultMaterial(this.model()),
      });
      this.renderAll();
      return;
    }
    // polygon mode — close on a first-vertex click (or its snap cell)
    if (this.pending.length >= 3) {
      const [fy, fz] = this.pending[0];
      const tolMm = Math.max(this.snap * 0.75, 8 / this.view.s);
      if (Math.hypot(pt[0] - fy, pt[1] - fz) <= tolMm) { this._closePolygon(); return; }
    }
    // ignore an exact duplicate of the previous vertex
    const last = this.pending[this.pending.length - 1];
    if (last && last[0] === pt[0] && last[1] === pt[1]) return;
    this.pending.push(pt);
    this.render();
    this._syncHint();
  }

  _closePolygon() {
    this.sec.polygons.push({
      vertices: this.pending.map(([y, z]) => [y / MM, z / MM]),
      material: this.$("dsMaterial").value || defaultMaterial(this.model()),
      hole: this.$("dsHole").checked,
    });
    this.pending = [];
    this.renderAll();
  }

  /* ------------------------------------------------ save / delete / list */
  async save() {
    const name = this.$("dsName").value.trim();
    if (!name) { this.opts.toast("Section Designer", "Give the section a name first", "error", 4000); return; }
    if (!this.sec.polygons.length) {
      this.opts.toast("Section Designer", "Draw at least one closed polygon before saving", "error", 4500);
      return;
    }
    this.sec.name = name;
    const btn = this.$("dsSave");
    btn.disabled = true;
    try {
      await this.opts.onUpsert(JSON.parse(JSON.stringify(this.sec)));
      this.savedName = name;
      this.opts.toast("Designer section saved",
        `${name} is available as a frame section (properties recomputed)`, "info", 4000);
    } catch (err) {
      this.opts.toast("Save failed", err.message, "error", 7000);
    } finally {
      btn.disabled = false;
      this.renderAll();
    }
  }

  async remove() {
    const name = this.savedName;
    if (!name || !this.model().designer_sections[name]) {
      this.opts.toast("Section Designer", "Nothing to delete — this section isn't saved yet", "info", 4000);
      return;
    }
    if (this.opts.isSectionInUse && this.opts.isSectionInUse(name)) {
      this.opts.toast("Delete blocked", `${name} is assigned to members — reassign them first`, "error", 5500);
      return;
    }
    const btn = this.$("dsDelete");
    btn.disabled = true;
    try {
      await this.opts.onDelete(name);
      this.opts.toast("Designer section deleted", name, "info", 3500);
      const next = Object.keys(this.model().designer_sections || {})[0];
      if (next) this.load(next); else this.newSection();
    } catch (err) {
      this.opts.toast("Delete failed", err.message, "error", 7000);
    } finally {
      btn.disabled = false;
      this.renderAll();
    }
  }

  /* ------------------------------------------------ PMM viewer */
  async showPmm() {
    const name = this.savedName;
    if (!name || !this.model().designer_sections[name]) {
      this.opts.toast("PMM diagram", "Save the section first — the curve is computed for the saved copy", "info", 4500);
      return;
    }
    const btn = this.$("dsPmmBtn");
    btn.disabled = true;
    try {
      this.pmmData = await this.opts.onPmm(name, this.pmmAxis);
      this.$("dsPmmPanel").classList.remove("hidden");
      this.renderPmm();
    } catch (err) {
      this.opts.toast("PMM failed", err.message, "error", 7000);
    } finally {
      btn.disabled = false;
    }
  }

  hidePmm() { this.$("dsPmmPanel").classList.add("hidden"); }

  renderPmm() {
    const svg = this.$("dsPmmSvg");
    const data = this.pmmData;
    if (!data || !Array.isArray(data.points) || !data.points.length) {
      svg.innerHTML = "";
      this.$("dsPmmMeta").textContent = "No PMM points returned.";
      return;
    }
    const pts = data.points.filter(p => Array.isArray(p) && p.length === 2 && p.every(isFinite));
    const W = 560, H = 340, L = 70, R = 16, T = 18, B = 42;
    const maxM = Math.max(...pts.map(p => p[0]), 1e-9) * 1.06;
    const minP = Math.min(...pts.map(p => p[1]), 0) * 1.08;
    const maxP = Math.max(...pts.map(p => p[1]), 1e-9) * 1.06;
    const X = mv => L + (mv / maxM) * (W - L - R);
    const Y = pv => T + (maxP - pv) / (maxP - minP || 1) * (H - T - B);
    const tick = range => {
      const raw = range / 5, mag = Math.pow(10, Math.floor(Math.log10(raw)));
      const r = raw / mag;
      return (r >= 5 ? 5 : r >= 2 ? 2 : 1) * mag;
    };
    let g = "";
    const tm = tick(maxM);
    for (let v = 0; v <= maxM + 1e-9; v += tm) {
      g += `<line x1="${X(v)}" y1="${T}" x2="${X(v)}" y2="${H - B}" stroke="rgba(154,167,180,0.12)"/>`;
      g += `<text x="${X(v)}" y="${H - B + 14}" fill="var(--text-3)" font-size="10" text-anchor="middle">${+v.toFixed(6)}</text>`;
    }
    const tp = tick(maxP - minP);
    for (let v = Math.ceil(minP / tp) * tp; v <= maxP + 1e-9; v += tp) {
      g += `<line x1="${L}" y1="${Y(v)}" x2="${W - R}" y2="${Y(v)}" stroke="rgba(154,167,180,0.12)"/>`;
      g += `<text x="${L - 6}" y="${Y(v) + 3}" fill="var(--text-3)" font-size="10" text-anchor="end">${+v.toFixed(6)}</text>`;
    }
    // emphasized axes: M = 0 and P = 0
    g += `<line x1="${L}" y1="${T}" x2="${L}" y2="${H - B}" stroke="rgba(154,167,180,0.45)"/>`;
    if (minP < 0)
      g += `<line x1="${L}" y1="${Y(0)}" x2="${W - R}" y2="${Y(0)}" stroke="rgba(154,167,180,0.45)"/>`;
    const path = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(2)},${Y(p[1]).toFixed(2)}`).join(" ");
    const curve =
      `<path d="${path} Z" fill="rgba(53,181,229,0.14)" stroke="none"/>` +
      `<path d="${path}" fill="none" stroke="var(--accent)" stroke-width="1.8" stroke-linejoin="round"/>`;
    const labels =
      `<text x="${(L + W - R) / 2}" y="${H - 8}" fill="var(--text-2)" font-size="11" text-anchor="middle">φMn (kN·m) — axis ${this.pmmAxis}</text>` +
      `<text x="14" y="${(T + H - B) / 2}" fill="var(--text-2)" font-size="11" text-anchor="middle" transform="rotate(-90 14 ${(T + H - B) / 2})">φPn (kN)</text>`;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = g + curve + labels;
    const pr = data.properties || {};
    this.$("dsPmmMeta").textContent =
      `A ${sci(pr.A)} m² · I33 ${sci(pr.I33)} m⁴ · I22 ${sci(pr.I22)} m⁴ · J ${sci(pr.J)} m⁴` +
      (isFinite(pr.cy) ? ` · centroid (${fmm(pr.cy)}, ${fmm(pr.cz)}) mm` : "");
  }

  /* ------------------------------------------------ side-panel rendering */
  renderAll() {
    this._renderList();
    this._renderItems();
    this._renderProps();
    this._syncHint();
    this.$("dsName").value = this.sec.name;
    this.render();
  }

  _renderList() {
    const box = this.$("dsList");
    box.textContent = "";
    const names = Object.keys(this.model().designer_sections || {});
    if (!names.length) {
      box.innerHTML = `<p class="lib-none">No saved designer sections yet.</p>`;
      return;
    }
    for (const n of names) {
      const b = document.createElement("button");
      b.className = "ds-chip" + (n === this.savedName ? " is-active" : "");
      b.textContent = n;
      b.title = `Load ${n} into the editor (unsaved edits are discarded)`;
      b.addEventListener("click", () => { this.load(n); this.renderAll(); });
      box.appendChild(b);
    }
  }

  _renderItems() {
    const box = this.$("dsItems");
    box.textContent = "";
    if (!this.sec) return;
    const row = (label, title, onDel) => {
      const div = document.createElement("div");
      div.className = "ds-item";
      const s = document.createElement("span");
      s.innerHTML = label;
      s.title = title || "";
      const x = document.createElement("button");
      x.className = "chip-x";
      x.textContent = "✕";
      x.title = "Remove";
      x.addEventListener("click", onDel);
      div.append(s, x);
      box.appendChild(div);
    };
    this.sec.polygons.forEach((p, i) => {
      row(`<b>P${i + 1}</b> · ${p.vertices.length} verts · ${esc(p.material)}` +
          (p.hole ? ` <i class="ds-hole-badge">hole</i>` : ""),
        p.vertices.map(v => `(${fmm(v[0])}, ${fmm(v[1])})`).join(" "),
        () => { this.sec.polygons.splice(i, 1); this.renderAll(); });
    });
    this.sec.rebar.forEach((b, i) => {
      row(`bar · (${fmm(b.y)}, ${fmm(b.z)}) mm · ${Math.round(b.area * 1e6)} mm²`,
        esc(b.material),
        () => { this.sec.rebar.splice(i, 1); this.renderAll(); });
    });
    if (!this.sec.polygons.length && !this.sec.rebar.length)
      box.innerHTML = `<p class="lib-none">Empty — click the canvas to place vertices.</p>`;
  }

  _renderProps() {
    const box = this.$("dsProps");
    if (!this.sec) { box.textContent = ""; return; }
    const pr = designerProps(this.sec);
    const saved = this.savedName ? this.model().sections[this.savedName] : null;
    let html = `
      <div class="ds-prop-line"><span>preview <span class="unit">shoelace</span></span>
        <b>A ${sci(pr.A)} m²</b></div>
      <div class="ds-prop-line"><span>I33 ${sci(pr.I33)} m⁴</span><span>I22 ${sci(pr.I22)} m⁴</span></div>
      <div class="ds-prop-line muted"><span>centroid (${fmm(pr.cy)}, ${fmm(pr.cz)}) mm</span>
        <span>${this.sec.rebar.length} bar${this.sec.rebar.length === 1 ? "" : "s"}</span></div>`;
    if (saved && isFinite(saved.A)) {
      html += `
      <div class="ds-prop-line ds-saved"><span>saved <span class="unit">server</span></span>
        <b>A ${sci(saved.A)} m²</b></div>
      <div class="ds-prop-line ds-saved"><span>I33 ${sci(saved.I33)} m⁴</span><span>I22 ${sci(saved.I22)} m⁴</span></div>`;
    }
    box.innerHTML = html;
  }

  _syncHint() {
    const el = this.$("dsHint");
    if (this.mode === "rebar") {
      el.textContent = "Rebar mode — click to place a bar (area above). Remove bars from the list.";
    } else if (this.pending.length) {
      el.textContent = `Polygon: ${this.pending.length} vertices — click the first vertex to close · Esc cancels.`;
    } else {
      el.textContent = "Polygon mode — click to place vertices on the grid; close on the first vertex. Wheel zooms, right-drag pans, double-click fits.";
    }
  }

  _syncReadout() {
    const el = this.$("dsReadout");
    el.textContent = this.cursor
      ? `y ${this.cursor[0]} · z ${this.cursor[1]} mm · grid ${this.snap} mm`
      : "";
  }

  /* ------------------------------------------------ canvas rendering */
  render() {
    if (!this.isOpen() || !this.sec) return;
    const r = this.svg.getBoundingClientRect();
    const W = Math.max(r.width, 50), H = Math.max(r.height, 50);
    this.svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const v = this.view;
    let out = "";

    // grid (snap-aligned, decimated to keep ≥ 7 px spacing)
    let step = this.snap;
    while (step * v.s < 7) step *= 2;
    const y0 = Math.floor(((0 - v.ox) / v.s) / step) * step;
    const y1 = Math.ceil(((W - v.ox) / v.s) / step) * step;
    const z0 = Math.floor(((v.oy - H) / v.s) / step) * step;
    const z1 = Math.ceil((v.oy / v.s) / step) * step;
    if ((y1 - y0) / step < 500 && (z1 - z0) / step < 500) {
      for (let y = y0; y <= y1; y += step) {
        const px = v.ox + y * v.s;
        out += `<line x1="${px}" y1="0" x2="${px}" y2="${H}" stroke="rgba(154,167,180,${y === 0 ? 0.35 : 0.07})"/>`;
      }
      for (let z = z0; z <= z1; z += step) {
        const py = v.oy - z * v.s;
        out += `<line x1="0" y1="${py}" x2="${W}" y2="${py}" stroke="rgba(154,167,180,${z === 0 ? 0.35 : 0.07})"/>`;
      }
    }

    // polygons (solids first, then holes so cutouts read on top)
    const poly = (p, hole) => {
      const d = p.vertices.map((vv, i) =>
        `${i ? "L" : "M"}${this._toPx(vv[0] * MM, vv[1] * MM).join(",")}`).join(" ") + " Z";
      return hole
        ? `<path d="${d}" fill="rgba(13,17,23,0.85)" stroke="rgba(53,181,229,0.75)" stroke-width="1" stroke-dasharray="4 3"/>`
        : `<path d="${d}" fill="rgba(95,143,201,0.30)" stroke="rgba(125,168,216,0.95)" stroke-width="1.3"/>`;
    };
    for (const p of this.sec.polygons) if (!p.hole) out += poly(p, false);
    for (const p of this.sec.polygons) if (p.hole) out += poly(p, true);

    // rebar — physical radius, min 2.5 px
    for (const b of this.sec.rebar) {
      const [px, py] = this._toPx(b.y * MM, b.z * MM);
      const rr = Math.max(Math.sqrt(b.area * 1e6 / Math.PI) * v.s, 2.5);
      out += `<circle cx="${px}" cy="${py}" r="${rr}" fill="rgba(229,165,10,0.85)" stroke="rgba(229,165,10,1)" stroke-width="1"/>`;
    }

    // centroid marker
    const pr = designerProps(this.sec);
    if (pr.A > 0) {
      const [cx, cy] = this._toPx(pr.cy * MM, pr.cz * MM);
      out += `<path d="M${cx - 7},${cy} L${cx + 7},${cy} M${cx},${cy - 7} L${cx},${cy + 7}"
        stroke="rgba(52,195,132,0.9)" stroke-width="1.2"/>`;
    }

    // in-progress polygon + rubber band + first-vertex target
    if (this.pending.length) {
      const pts = this.pending.map(p => this._toPx(p[0], p[1]));
      const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join(" ");
      const rubber = this.cursor
        ? ` L${this._toPx(this.cursor[0], this.cursor[1]).join(",")}` : "";
      out += `<path d="${d}${rubber}" fill="none" stroke="var(--accent)" stroke-width="1.4" stroke-dasharray="5 3"/>`;
      pts.forEach((p, i) => {
        out += `<circle cx="${p[0]}" cy="${p[1]}" r="${i === 0 ? 5 : 2.6}"
          fill="${i === 0 ? "rgba(53,181,229,0.25)" : "var(--accent)"}"
          stroke="var(--accent)" stroke-width="1.2"/>`;
      });
    }

    // snapped-cursor crosshair
    if (this.cursor && !this._pan) {
      const [px, py] = this._toPx(this.cursor[0], this.cursor[1]);
      out += `<path d="M${px - 5},${py} L${px + 5},${py} M${px},${py - 5} L${px},${py + 5}"
        stroke="rgba(232,237,243,0.7)" stroke-width="1"/>`;
    }
    this.svg.innerHTML = out;
  }
}
