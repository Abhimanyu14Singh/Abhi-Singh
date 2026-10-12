/* SkyFrame — v0.21 SECTION DESIGNER
   SVG polygon + rebar fiber-section editor with a live shoelace property
   preview and a PMM interaction-diagram viewer. The model stores meters
   (rebar areas in m²); everything the user sees or types — grid snap, bar
   area, vertex coordinates, cursor readout, A / I / centroid, the PMM axes
   — is in the DISPLAY units (js/units.js, section-dimension length "dim").
   D3 audit: the editor used to be frozen in mm / m² / m⁴ / kN; it now
   follows the model units, with a dialog "Units" selector (js/dlgunits.js)
   that defaults to them. No frameworks.

   Wiring is callback-based (same pattern as PlanEditor / LoadsEditor):
     getModel()                 → working model dict
     onUpsert(section)          → POST /api/sections/designer upsert + adopt
     onDelete(name)             → POST /api/sections/designer delete + adopt
     onPmm(name, axis)          → POST /api/sections/designer/pmm
     isSectionInUse(name)       → companion FrameSection used by members?
     toast(title, msg, type)    → app toast
     onClose()                  → refresh views after the dialog closes      */

import { designerProps, defaultMaterial } from "./modeledit.js";
import { seDesignerBlock as dsExactBlock, seDesignerNew as dsExactNew } from "./secexact.js";   // v1.22 exact torsion (js/secexact.js)
import dsdU from "./units.js";                                       // D3: display units
import { duPicker as dsdPicker } from "./dlgunits.js";               // D3: dialog units selector

const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
/** Display value of an SI quantity (trimmed, no float noise). */
const dnum = (kind, si, sig = 6) => {
  if (si == null || !isFinite(si)) return "—";
  const v = dsdU.toDisplay(kind, si);
  return String(+(+v).toPrecision(sig));
};
const dsci = (kind, si) => (si == null || !isFinite(si)) ? "—" : dsdU.sci(kind, si, 3);
const ul = kind => dsdU.label(kind);
/** Clean an SI coordinate produced from a snapped display value. */
const clean = v => +(+v).toPrecision(15);
/** Default snap (SI m) for the current set: 10 mm / 0.01 m / 0.5 in. */
function defaultSnap() {
  const du = ul("dim");
  return du === "in" ? 0.5 * 0.0254 : du === "ft" ? 0.0254 * 6 : 0.01;
}
/** Default new-bar area (SI m²): 500 mm² / 0.79 in² (≈ #8). */
const defaultBar = () => (ul("dim") === "in" ? 0.79 * 0.0254 * 0.0254 : 500e-6);

export class SectionDesigner {
  constructor(opts) {
    this.opts = opts;
    this.svg = document.getElementById("dsSvg");
    this.sec = null;            // working copy (meters)
    this.savedName = null;      // model key this.sec was loaded from (null = new)
    this.pending = [];          // in-progress polygon vertices (SI m pairs)
    this.mode = "poly";         // "poly" | "rebar"
    this.snapSI = defaultSnap();   // grid snap (m)
    this.barAreaSI = defaultBar(); // rebar area (m²)
    this.view = { s: 550, ox: 300, oy: 260 };   // px per m + origin px
    this.cursor = null;         // snapped [y, z] m under the mouse
    this.pmmData = null;
    this.pmmAxis = "33";
    this._pan = null;
    this._du = null;            // dialog units picker (js/dlgunits.js)
    this._offU = null;
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
    this._mountUnits();
    this.snapSI = defaultSnap();
    this.barAreaSI = defaultBar();
    this._syncUnitInputs();
    this.fit();
    this.renderAll();
  }

  close() {
    this.$("designerModal").classList.add("hidden");
    window.removeEventListener("resize", this._resize);
    this.pending = [];
    this._unmountUnits();
    if (this.opts.onClose) this.opts.onClose();
  }

  /** D3: "Units" selector in the header (defaults to the model units). */
  _mountUnits() {
    this._unmountUnits();
    this._du = dsdPicker({ id: "dsUnits" });
    const head = this.$("designerTitle");
    const host = head && head.parentNode;
    if (host) host.insertBefore(this._du.el, this.$("designerClose"));
    this._offU = dsdU.onUnitsChange(() => {
      if (!this.isOpen()) return;
      this.snapSI = defaultSnap();
      this.barAreaSI = defaultBar();
      this._syncUnitInputs();
      this.renderAll();
      if (this.pmmData && !this.$("dsPmmPanel").classList.contains("hidden")) this.renderPmm();
    });
  }
  _unmountUnits() {
    if (this._offU) { this._offU(); this._offU = null; }
    if (this._du) { this._du.el.remove(); this._du.release(); this._du = null; }
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
    this.sec = dsExactNew({ name: `DS${i}`, polygons: [], rebar: [] });   // v1.22: new sections → torsion "exact"
    this.savedName = null;
    this.pending = [];
    this.pmmData = null;
    this.hidePmm();
    this._syncInputs();
  }

  _syncInputs() {
    this.$("dsName").value = this.sec.name;
    this._syncUnitInputs();
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

  /** Grid / bar-area / numeric-entry inputs + their unit labels. */
  _syncUnitInputs() {
    const snap = this.$("dsSnap"), bar = this.$("dsBarArea");
    snap.value = dnum("dim", this.snapSI);
    snap.min = "0"; snap.step = "any";
    snap.title = `Snap grid spacing (${ul("dim")})`;
    bar.value = dnum("area", this.barAreaSI);
    bar.min = "0"; bar.step = "any";
    bar.title = `Area of newly placed rebar (${ul("area")})`;
    const lab = (id, kind) => {
      const l = document.querySelector(`label[for="${id}"] .unit`);
      if (l) l.textContent = ul(kind);
    };
    lab("dsSnap", "dim");
    lab("dsBarArea", "area");
    this._ensureEntryRow();
    document.querySelectorAll(".ds-entry-unit").forEach(s => { s.textContent = ul("dim"); });
  }

  /** D3: numeric point entry (typed coordinates in display units). */
  _ensureEntryRow() {
    if (this.$("dsEntry")) return;
    const after = this.$("dsModeToggle");
    if (!after) return;
    const row = document.createElement("div");
    row.className = "field-row ds-entry"; row.id = "dsEntry";
    row.innerHTML =
      `<div class="field"><label for="dsEntryY">y <span class="unit ds-entry-unit"></span></label>` +
      `<input id="dsEntryY" type="number" step="any" title="Point y (local 3 direction, right)"></div>` +
      `<div class="field"><label for="dsEntryZ">z <span class="unit ds-entry-unit"></span></label>` +
      `<input id="dsEntryZ" type="number" step="any" title="Point z (local 2 direction, up)"></div>` +
      `<div class="field ds-entry-btns"><button class="btn btn-small" id="dsEntryAdd" title="Add the typed point (a polygon vertex, or a bar in Rebar mode)">+ Point</button>` +
      `<button class="btn btn-small" id="dsEntryClose" title="Close the pending polygon">Close</button></div>`;
    after.parentNode.insertBefore(row, after.nextSibling);
    this.$("dsEntryAdd").addEventListener("click", () => {
      const y = dsdU.parse("dim", this.$("dsEntryY").value);
      const z = dsdU.parse("dim", this.$("dsEntryZ").value);
      if (!isFinite(y) || !isFinite(z)) {
        this.opts.toast("Section Designer", `Enter y and z in ${ul("dim")}`, "error", 3500);
        return;
      }
      this._placePoint([clean(y), clean(z)]);
    });
    this.$("dsEntryClose").addEventListener("click", () => {
      if (this.pending.length >= 3) this._closePolygon();
      else this.opts.toast("Section Designer", "A polygon needs at least 3 vertices", "info", 3000);
    });
  }

  /* ------------------------------------------------ wiring */
  _wire() {
    this.$("dsNew").addEventListener("click", () => { this.newSection(); this.renderAll(); });
    this.$("dsName").addEventListener("change", e => {
      const v = e.target.value.trim();
      if (v) this.sec.name = v; else e.target.value = this.sec.name;
    });
    this.$("dsSnap").addEventListener("change", e => {
      const v = dsdU.parse("dim", e.target.value);
      if (isFinite(v) && v > 0) this.snapSI = v; else e.target.value = dnum("dim", this.snapSI);
      this.render();
    });
    this.$("dsBarArea").addEventListener("change", e => {
      const v = dsdU.parse("area", e.target.value);
      if (isFinite(v) && v > 0) this.barAreaSI = v; else e.target.value = dnum("area", this.barAreaSI);
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
      const [my, mz] = this._toM(px, py);
      const f = e.deltaY < 0 ? 1.2 : 1 / 1.2;
      this.view.s = Math.min(25000, Math.max(20, this.view.s * f));
      this.view.ox = px - my * this.view.s;
      this.view.oy = py + mz * this.view.s;
      this.render();
    }, { passive: false });
    svg.addEventListener("dblclick", () => { this.fit(); this.render(); });
    svg.addEventListener("click", e => this._onClick(e));
  }

  /* ------------------------------------------------ coordinates (m ↔ px) */
  _toPx(y, z) { return [this.view.ox + y * this.view.s, this.view.oy - z * this.view.s]; }
  _toM(px, py) { return [(px - this.view.ox) / this.view.s, (this.view.oy - py) / this.view.s]; }

  /** Mouse → nearest grid point, snapped in DISPLAY units (clean values). */
  _snapPoint(e) {
    const r = this.svg.getBoundingClientRect();
    const [y, z] = this._toM(e.clientX - r.left, e.clientY - r.top);
    const sn = dsdU.toDisplay("dim", this.snapSI);
    const snap1 = v => {
      const d = Math.round(dsdU.toDisplay("dim", v) / sn) * sn;
      return clean(dsdU.fromDisplay("dim", +d.toPrecision(12)));
    };
    return [snap1(y), snap1(z)];
  }

  fit() {
    const r = this.svg.getBoundingClientRect();
    const W = r.width || 640, H = r.height || 480;
    let ymin = Infinity, ymax = -Infinity, zmin = Infinity, zmax = -Infinity;
    for (const p of (this.sec ? this.sec.polygons : []))
      for (const [y, z] of p.vertices) {
        ymin = Math.min(ymin, y); ymax = Math.max(ymax, y);
        zmin = Math.min(zmin, z); zmax = Math.max(zmax, z);
      }
    if (!isFinite(ymin)) { ymin = -0.4; ymax = 0.4; zmin = -0.4; zmax = 0.4; }
    const span = Math.max(ymax - ymin, zmax - zmin, 0.1) * 1.35;
    this.view.s = Math.min(W, H) / span;
    this.view.ox = W / 2 - ((ymin + ymax) / 2) * this.view.s;
    this.view.oy = H / 2 + ((zmin + zmax) / 2) * this.view.s;
  }

  /* ------------------------------------------------ editing */
  _onClick(e) {
    if (!this.sec) return;
    const pt = this._snapPoint(e);
    if (this.mode === "poly" && this.pending.length >= 3) {
      // polygon mode — close on a first-vertex click (or its snap cell)
      const [fy, fz] = this.pending[0];
      const tol = Math.max(this.snapSI * 0.75, 8 / this.view.s);
      if (Math.hypot(pt[0] - fy, pt[1] - fz) <= tol) { this._closePolygon(); return; }
    }
    this._placePoint(pt);
  }

  /** A polygon vertex (poly mode) or a bar (rebar mode) at SI [y, z]. */
  _placePoint(pt) {
    if (!this.sec) return;
    if (this.mode === "rebar") {
      this.sec.rebar.push({
        y: pt[0], z: pt[1],
        area: this.barAreaSI,
        material: this.$("dsMaterial").value || defaultMaterial(this.model()),
      });
      this.renderAll();
      return;
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
      vertices: this.pending.map(([y, z]) => [y, z]),
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
      const payload = JSON.parse(JSON.stringify(this.sec));
      // v1.22: the backend needs the base material (first solid polygon's)
      if (!payload.material) payload.material = (payload.polygons.find(p => !p.hole) || payload.polygons[0]).material;
      await this.opts.onUpsert(payload);
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
    // SI [M kN·m, P kN] → display units
    const pts = data.points.filter(p => Array.isArray(p) && p.length === 2 && p.every(isFinite))
      .map(([mv, pv]) => [dsdU.toDisplay("moment", mv), dsdU.toDisplay("force", pv)]);
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
      `<text id="dsPmmXLabel" x="${(L + W - R) / 2}" y="${H - 8}" fill="var(--text-2)" font-size="11" text-anchor="middle">φMn (${esc(ul("moment"))}) — axis ${this.pmmAxis}</text>` +
      `<text id="dsPmmYLabel" x="14" y="${(T + H - B) / 2}" fill="var(--text-2)" font-size="11" text-anchor="middle" transform="rotate(-90 14 ${(T + H - B) / 2})">φPn (${esc(ul("force"))})</text>`;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = g + curve + labels;
    const pr = data.properties || {};
    this.$("dsPmmMeta").textContent =
      `A ${dsci("area", pr.A)} ${ul("area")} · I33 ${dsci("inertia", pr.I33)} · I22 ${dsci("inertia", pr.I22)} · J ${dsci("inertia", pr.J)} ${ul("inertia")}` +
      (isFinite(pr.cy) ? ` · centroid (${dnum("dim", pr.cy)}, ${dnum("dim", pr.cz)}) ${ul("dim")}` : "");
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
    const du = esc(ul("dim"));
    this.sec.polygons.forEach((p, i) => {
      row(`<b>P${i + 1}</b> · ${p.vertices.length} verts · ${esc(p.material)}` +
          (p.hole ? ` <i class="ds-hole-badge">hole</i>` : ""),
        p.vertices.map(v => `(${dnum("dim", v[0])}, ${dnum("dim", v[1])})`).join(" ") + ` ${ul("dim")}`,
        () => { this.sec.polygons.splice(i, 1); this.renderAll(); });
    });
    this.sec.rebar.forEach((b, i) => {
      row(`bar · (${dnum("dim", b.y)}, ${dnum("dim", b.z)}) ${du} · ${dnum("area", b.area, 4)} ${esc(ul("area"))}`,
        esc(b.material),
        () => { this.sec.rebar.splice(i, 1); this.renderAll(); });
    });
    if (!this.sec.polygons.length && !this.sec.rebar.length)
      box.innerHTML = `<p class="lib-none">Empty — click the canvas (or type y, z) to place vertices.</p>`;
  }

  _renderProps() {
    const box = this.$("dsProps");
    if (!this.sec) { box.textContent = ""; return; }
    const pr = designerProps(this.sec);
    const saved = this.savedName ? this.model().sections[this.savedName] : null;
    const ua = esc(ul("area")), ui = esc(ul("inertia")), ud = esc(ul("dim"));
    let html = `
      <div class="ds-prop-line"><span>preview <span class="unit">shoelace</span></span>
        <b id="dsPropA" data-si="${pr.A}">A ${dsci("area", pr.A)} ${ua}</b></div>
      <div class="ds-prop-line"><span id="dsPropI33" data-si="${pr.I33}">I33 ${dsci("inertia", pr.I33)} ${ui}</span><span id="dsPropI22" data-si="${pr.I22}">I22 ${dsci("inertia", pr.I22)} ${ui}</span></div>
      <div class="ds-prop-line muted"><span id="dsPropC">centroid (${dnum("dim", pr.cy)}, ${dnum("dim", pr.cz)}) ${ud}</span>
        <span>${this.sec.rebar.length} bar${this.sec.rebar.length === 1 ? "" : "s"}</span></div>`;
    if (saved && isFinite(saved.A)) {
      html += `
      <div class="ds-prop-line ds-saved"><span>saved <span class="unit">server</span></span>
        <b>A ${dsci("area", saved.A)} ${ua}</b></div>
      <div class="ds-prop-line ds-saved"><span>I33 ${dsci("inertia", saved.I33)} ${ui}</span><span>I22 ${dsci("inertia", saved.I22)} ${ui}</span></div>`;
    }
    box.innerHTML = html;
    dsExactBlock(this, box);          // v1.22: torsion method + exact J readout (js/secexact.js)
  }

  _syncHint() {
    const el = this.$("dsHint");
    if (this.mode === "rebar") {
      el.textContent = "Rebar mode — click (or type y, z and + Point) to place a bar (area above). Remove bars from the list.";
    } else if (this.pending.length) {
      el.textContent = `Polygon: ${this.pending.length} vertices — click the first vertex (or Close) to close · Esc cancels.`;
    } else {
      el.textContent = "Polygon mode — click to place vertices on the grid (or type y, z); close on the first vertex. Wheel zooms, right-drag pans, double-click fits.";
    }
  }

  _syncReadout() {
    const el = this.$("dsReadout");
    el.textContent = this.cursor
      ? `y ${dnum("dim", this.cursor[0])} · z ${dnum("dim", this.cursor[1])} ${ul("dim")} · grid ${dnum("dim", this.snapSI)} ${ul("dim")}`
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
    let step = this.snapSI;
    while (step * v.s < 7) step *= 2;
    const y0 = Math.floor(((0 - v.ox) / v.s) / step);
    const y1 = Math.ceil(((W - v.ox) / v.s) / step);
    const z0 = Math.floor(((v.oy - H) / v.s) / step);
    const z1 = Math.ceil((v.oy / v.s) / step);
    if ((y1 - y0) < 500 && (z1 - z0) < 500) {
      for (let i = y0; i <= y1; i++) {
        const px = v.ox + i * step * v.s;
        out += `<line x1="${px}" y1="0" x2="${px}" y2="${H}" stroke="rgba(154,167,180,${i === 0 ? 0.35 : 0.07})"/>`;
      }
      for (let j = z0; j <= z1; j++) {
        const py = v.oy - j * step * v.s;
        out += `<line x1="0" y1="${py}" x2="${W}" y2="${py}" stroke="rgba(154,167,180,${j === 0 ? 0.35 : 0.07})"/>`;
      }
    }

    // polygons (solids first, then holes so cutouts read on top)
    const poly = (p, hole) => {
      const d = p.vertices.map((vv, i) =>
        `${i ? "L" : "M"}${this._toPx(vv[0], vv[1]).join(",")}`).join(" ") + " Z";
      return hole
        ? `<path d="${d}" fill="rgba(13,17,23,0.85)" stroke="rgba(53,181,229,0.75)" stroke-width="1" stroke-dasharray="4 3"/>`
        : `<path d="${d}" fill="rgba(95,143,201,0.30)" stroke="rgba(125,168,216,0.95)" stroke-width="1.3"/>`;
    };
    for (const p of this.sec.polygons) if (!p.hole) out += poly(p, false);
    for (const p of this.sec.polygons) if (p.hole) out += poly(p, true);

    // rebar — physical radius, min 2.5 px
    for (const b of this.sec.rebar) {
      const [px, py] = this._toPx(b.y, b.z);
      const rr = Math.max(Math.sqrt(b.area / Math.PI) * v.s, 2.5);
      out += `<circle cx="${px}" cy="${py}" r="${rr}" fill="rgba(229,165,10,0.85)" stroke="rgba(229,165,10,1)" stroke-width="1"/>`;
    }

    // centroid marker
    const pr = designerProps(this.sec);
    if (pr.A > 0) {
      const [cx, cy] = this._toPx(pr.cy, pr.cz);
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
