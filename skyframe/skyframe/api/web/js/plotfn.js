/* SkyFrame — ETABS Display menu result plots (analysis only):

     Display → Show Plot Functions…        TH plot functions: joint DOFs (rel /
                                           abs), link force / deformation
                                           (hysteresis X-vs-Y), frame end
                                           forces, hinges, base reaction,
                                           story displacement / drift,
                                           energy, ground motion; overlay,
                                           zoom / pan, CSV; floor response
                                           spectrum tab (POST
                                           /api/plotfn/spectrum); output
                                           requests editor
                                           (TimeHistoryCase.output_requests)
     Display → Story Response Plots…       vertical story profiles: max
                                           displacement, drift ratio, shear,
                                           overturning moment, stiffness, with
                                           a limit line
     Display → Force/Stress Diagrams (3D)… M3 / M2 / V2 / V3 / P / T diagrams on
                                           the 3D members + support reaction
                                           arrows (Viewer3D.pfOverlay hook)

   CONTRACT section: "Plot functions, floor response spectra and story
   response plots". Data come from the last /api/analyze results; the model
   store stays SI and every number crosses js/units.js. The output-request
   editor edits a draft and writes the model ONLY on OK, and only when the
   request changed (OK with defaults leaves the model byte-identical). */

import U from "./units.js";
import { dialog as pfDialog, btn as pfBtn, footBar as pfFootBar,
         errorLine as pfErrorLine, showError as pfShowError } from "./analysisdlg.js";
import { pfMockSpectrum, pfMockExtend } from "./mock_plotfn.js";

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const isNum = v => typeof v === "number" && isFinite(v);
const SVGNS = "http://www.w3.org/2000/svg";
const PALETTE = ["#35b5e5", "#e5a50a", "#d55181", "#34c384", "#a78bfa", "#e66767", "#8fa3ba", "#f08c3c", "#4fd0c7", "#c98500"];

function ensureCss() {
  if (document.querySelector("link[data-pf-css]")) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/plotfn.css"; l.setAttribute("data-pf-css", "1");
  document.head.appendChild(l);
}
function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (v != null && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  (Array.isArray(kids) ? kids : [kids]).forEach(c =>
    c != null && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
  return n;
}
function svg(tag, attrs = {}) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}
const option = (value, text, sel) => { const o = el("option", { value, text }); if (sel) o.selected = true; return o; };
function niceTicks(a, b, n = 6) {
  if (!(isFinite(a) && isFinite(b)) || a === b) { const c = isFinite(a) ? a : 0; return [c]; }
  const span = Math.abs(b - a), raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(f => f * mag).find(s => span / s <= n) || 10 * mag;
  const lo = Math.ceil(Math.min(a, b) / step) * step, out = [];
  for (let v = lo; v <= Math.max(a, b) + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}
const tickFmt = v => { const a = Math.abs(v); return a === 0 ? "0" : (a >= 1e4 || a < 1e-3) ? v.toExponential(1) : String(+v.toPrecision(4)); };
function downloadText(name, text) {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
/** key-order independent JSON (the backend echoes sorted keys). */
const canon = v => JSON.stringify(v, (k, x) => (x && typeof x === "object" && !Array.isArray(x))
  ? Object.fromEntries(Object.keys(x).sort().map(kk => [kk, x[kk]])) : x);
const csvCell = v => typeof v === "number" ? String(+v.toPrecision(10)) : `"${String(v).replace(/"/g, '""')}"`;

/* ================================================================
   XY line chart with zoom (wheel) / pan (drag) / reset (dblclick)
   ================================================================ */
class PfChart {
  constructor(host, { height = 340 } = {}) {
    this.host = host; this.height = height;
    this.svg = svg("svg", { class: "pf-chart" });
    this.legend = el("div", { class: "pf-legend" });
    const tools = el("div", { class: "pf-chart-tools" }, [
      el("button", { text: "+", title: "Zoom in", onclick: () => this.zoom(0.7) }),
      el("button", { text: "−", title: "Zoom out", onclick: () => this.zoom(1 / 0.7) }),
      el("button", { text: "Reset", title: "Reset zoom / pan (double-click)", class: "pf-reset", onclick: () => this.reset() }),
    ]);
    host.textContent = "";
    host.append(this.svg, tools);
    host.after(this.legend);
    this.data = null; this.view = null; this.full = null;
    this._bind();
  }
  setData(d) {
    this.data = d;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const s of d.series) for (let i = 0; i < s.x.length; i++) {
      const x = s.x[i], y = s.y[i];
      if (!isFinite(x) || !isFinite(y)) continue;
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    if (!isFinite(x0)) { x0 = 0; x1 = 1; y0 = -1; y1 = 1; }
    if (x1 - x0 < 1e-300) { x0 -= 1; x1 += 1; }
    if (y1 - y0 < 1e-300) { const c = y0; y0 = c - (Math.abs(c) || 1) * 0.1; y1 = c + (Math.abs(c) || 1) * 0.1; }
    const py = (y1 - y0) * 0.06, px = d.padX ? (x1 - x0) * 0.03 : 0;
    this.full = [x0 - px, x1 + px, y0 - py, y1 + py];
    this.view = this.full.slice();
    this.render();
  }
  reset() { if (this.full) { this.view = this.full.slice(); this.render(); } }
  zoom(f, cx = 0.5, cy = 0.5) {
    if (!this.view) return;
    const [x0, x1, y0, y1] = this.view;
    const xc = x0 + (x1 - x0) * cx, yc = y0 + (y1 - y0) * cy;
    this.view = [xc - (xc - x0) * f, xc + (x1 - xc) * f, yc - (yc - y0) * f, yc + (y1 - yc) * f];
    this.render();
  }
  _geom() {
    const w = Math.max(this.host.clientWidth || 640, 320), h = this.height;
    return { w, h, L: 64, R: 14, T: 14, B: 40 };
  }
  _bind() {
    let drag = null;
    this.svg.addEventListener("wheel", e => {
      if (!this.view) return;
      e.preventDefault();
      const g = this._geom(), r = this.svg.getBoundingClientRect();
      const cx = (e.clientX - r.left - g.L) / (g.w - g.L - g.R);
      const cy = 1 - (e.clientY - r.top - g.T) / (g.h - g.T - g.B);
      this.zoom(e.deltaY > 0 ? 1.15 : 1 / 1.15, Math.min(1, Math.max(0, cx)), Math.min(1, Math.max(0, cy)));
    }, { passive: false });
    this.svg.addEventListener("mousedown", e => {
      if (!this.view) return;
      drag = { x: e.clientX, y: e.clientY, v: this.view.slice() };
      this.svg.classList.add("is-panning");
    });
    window.addEventListener("mousemove", e => {
      if (!drag) {
        this._hover(e);
        return;
      }
      const g = this._geom(), [x0, x1, y0, y1] = drag.v;
      const dx = (e.clientX - drag.x) / (g.w - g.L - g.R) * (x1 - x0);
      const dy = (e.clientY - drag.y) / (g.h - g.T - g.B) * (y1 - y0);
      this.view = [x0 - dx, x1 - dx, y0 + dy, y1 + dy];
      this.render();
    });
    window.addEventListener("mouseup", () => { drag = null; this.svg.classList.remove("is-panning"); });
    this.svg.addEventListener("dblclick", () => this.reset());
  }
  _hover(e) {
    if (!this.view || !this.readout || !this.svg.isConnected) return;
    const r = this.svg.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) return;
    const g = this._geom(), [x0, x1, y0, y1] = this.view;
    const x = x0 + (e.clientX - r.left - g.L) / (g.w - g.L - g.R) * (x1 - x0);
    const y = y1 - (e.clientY - r.top - g.T) / (g.h - g.T - g.B) * (y1 - y0);
    this.readout.textContent = `x ${tickFmt(x)}   y ${tickFmt(y)}`;
  }
  render() {
    const d = this.data, s = this.svg;
    while (s.firstChild) s.removeChild(s.firstChild);
    this.legend.textContent = "";
    if (!d) return;
    const g = this._geom();
    s.setAttribute("viewBox", `0 0 ${g.w} ${g.h}`);
    s.style.height = g.h + "px";
    const [x0, x1, y0, y1] = this.view;
    const pw = g.w - g.L - g.R, ph = g.h - g.T - g.B;
    const X = x => g.L + (x - x0) / (x1 - x0) * pw;
    const Y = y => g.T + (1 - (y - y0) / (y1 - y0)) * ph;
    const cid = "pfclip" + Math.random().toString(36).slice(2, 8);
    const defs = svg("defs"), cp = svg("clipPath", { id: cid });
    cp.appendChild(svg("rect", { x: g.L, y: g.T, width: pw, height: ph }));
    defs.appendChild(cp); s.appendChild(defs);
    const ax = svg("g", { class: "pf-axis" });
    const logX = !!d.logX;
    for (const v of niceTicks(x0, x1, Math.max(3, Math.round(pw / 90)))) {
      const xx = X(v);
      ax.appendChild(svg("line", { x1: xx, x2: xx, y1: g.T, y2: g.T + ph, class: "pf-gridline" }));
      const t = svg("text", { x: xx, y: g.T + ph + 14, "text-anchor": "middle" });
      t.textContent = logX ? tickFmt(Math.pow(10, v)) : tickFmt(v); ax.appendChild(t);
    }
    for (const v of niceTicks(y0, y1, Math.max(3, Math.round(ph / 50)))) {
      const yy = Y(v);
      ax.appendChild(svg("line", { x1: g.L, x2: g.L + pw, y1: yy, y2: yy, class: "pf-gridline" }));
      const t = svg("text", { x: g.L - 6, y: yy + 3, "text-anchor": "end" });
      t.textContent = tickFmt(v); ax.appendChild(t);
    }
    ax.appendChild(svg("rect", { x: g.L, y: g.T, width: pw, height: ph, fill: "none" }));
    s.appendChild(ax);
    if (y0 < 0 && y1 > 0) s.appendChild(svg("line", { x1: g.L, x2: g.L + pw, y1: Y(0), y2: Y(0), class: "pf-zero" }));
    if (!logX && x0 < 0 && x1 > 0) s.appendChild(svg("line", { y1: g.T, y2: g.T + ph, x1: X(0), x2: X(0), class: "pf-zero" }));
    const lines = svg("g", { "clip-path": `url(#${cid})` });
    d.series.forEach((sr, i) => {
      const color = sr.color || PALETTE[i % PALETTE.length];
      let path = "", pen = false;
      const n = sr.x.length, stride = n > 6000 ? Math.ceil(n / 6000) : 1;
      for (let k = 0; k < n; k += stride) {
        const xv = sr.x[k], yv = sr.y[k];
        if (!isFinite(xv) || !isFinite(yv)) { pen = false; continue; }
        path += (pen ? "L" : "M") + X(xv).toFixed(1) + " " + Y(yv).toFixed(1);
        pen = true;
      }
      lines.appendChild(svg("path", { d: path, fill: "none", stroke: color, "stroke-width": 1.5,
        "stroke-linejoin": "round", class: "pf-series", "data-name": sr.name }));
      const li = el("span", {}, [el("i", { style: `background:${color}` }), sr.name]);
      this.legend.appendChild(li);
    });
    s.appendChild(lines);
    const xl = svg("text", { x: g.L + pw / 2, y: g.h - 6, "text-anchor": "middle", class: "pf-axlabel" });
    xl.textContent = d.xLabel || ""; s.appendChild(xl);
    const yl = svg("text", { x: 12, y: g.T + ph / 2, "text-anchor": "middle", class: "pf-axlabel",
      transform: `rotate(-90 12 ${g.T + ph / 2})` });
    yl.textContent = d.yLabel || ""; s.appendChild(yl);
    this.readout = svg("text", { x: g.L + 6, y: g.T + 12, class: "pf-readout" });
    s.appendChild(this.readout);
  }
}

/* ================================================================
   vertical story-profile chart (ETABS Story Response Plots)
   ================================================================ */
function profileChart(host, { levels, series, limits = [], xLabel, showValues }) {
  host.textContent = "";
  const w = Math.max(host.clientWidth || 640, 360), h = 380;
  const g = { L: 78, R: 18, T: 16, B: 40 };
  const pw = w - g.L - g.R, ph = h - g.T - g.B;
  const s = svg("svg", { viewBox: `0 0 ${w} ${h}`, class: "pf-profile" });
  s.style.height = h + "px";
  let v0 = 0, v1 = 0;
  for (const sr of series) for (const [v] of sr.pts) { if (isFinite(v)) { v0 = Math.min(v0, v); v1 = Math.max(v1, v); } }
  for (const l of limits) { v0 = Math.min(v0, l.value); v1 = Math.max(v1, l.value); }
  if (v1 - v0 < 1e-300) { v1 = v0 + 1; }
  const pad = (v1 - v0) * 0.08; v1 += pad; if (v0 < 0) v0 -= pad;
  const z0 = Math.min(...levels.map(l => l.z)), z1 = Math.max(...levels.map(l => l.z));
  const X = v => g.L + (v - v0) / (v1 - v0) * pw;
  const Y = z => g.T + (1 - (z - z0) / ((z1 - z0) || 1)) * ph;
  const ax = svg("g", { class: "pf-axis" });
  for (const v of niceTicks(v0, v1, Math.max(3, Math.round(pw / 90)))) {
    ax.appendChild(svg("line", { x1: X(v), x2: X(v), y1: g.T, y2: g.T + ph, class: "pf-gridline" }));
    const t = svg("text", { x: X(v), y: g.T + ph + 14, "text-anchor": "middle" });
    t.textContent = tickFmt(v); ax.appendChild(t);
  }
  for (const l of levels) {
    ax.appendChild(svg("line", { x1: g.L, x2: g.L + pw, y1: Y(l.z), y2: Y(l.z), class: "pf-gridline" }));
    const t = svg("text", { x: g.L - 6, y: Y(l.z) + 3.5, "text-anchor": "end", class: "pf-story-label" });
    t.textContent = l.name; ax.appendChild(t);
  }
  ax.appendChild(svg("rect", { x: g.L, y: g.T, width: pw, height: ph, fill: "none" }));
  s.appendChild(ax);
  if (v0 < 0) s.appendChild(svg("line", { x1: X(0), x2: X(0), y1: g.T, y2: g.T + ph, class: "pf-zero" }));
  for (const l of limits) {
    s.appendChild(svg("line", { x1: X(l.value), x2: X(l.value), y1: g.T, y2: g.T + ph, class: "pf-limit" }));
    const t = svg("text", { x: X(l.value) + 4, y: g.T + 11, class: "pf-limit-label" });
    t.textContent = l.label; s.appendChild(t);
  }
  const legend = el("div", { class: "pf-legend" });
  series.forEach((sr, i) => {
    const color = sr.color || PALETTE[i % PALETTE.length];
    const d = sr.pts.filter(p => isFinite(p[0])).map((p, k) => (k ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1)).join("");
    s.appendChild(svg("path", { d, fill: "none", stroke: color, "stroke-width": 1.8, class: "pf-series", "data-name": sr.name }));
    for (const p of sr.marks || sr.pts) {
      if (!isFinite(p[0])) continue;
      s.appendChild(svg("circle", { cx: X(p[0]), cy: Y(p[1]), r: 2.6, fill: color }));
      if (showValues) {
        const t = svg("text", { x: X(p[0]) + 5, y: Y(p[1]) - 4, class: "pf-value" });
        t.textContent = tickFmt(p[0]); s.appendChild(t);
      }
    }
    legend.appendChild(el("span", {}, [el("i", { style: `background:${color}` }), sr.name]));
  });
  const xl = svg("text", { x: g.L + pw / 2, y: h - 6, "text-anchor": "middle", class: "pf-axlabel" });
  xl.textContent = xLabel || ""; s.appendChild(xl);
  host.appendChild(s);
  host.appendChild(legend);
  return s;
}

/* ================================================================
   function registry for one TH case (all values SI)
   ================================================================ */
const DOF_KIND = { UX: "disp", UY: "disp", UZ: "disp", RX: "rotation", RY: "rotation", RZ: "rotation" };
const linkKind = (c, isForce) => {
  if (/^(T|M2|M3|R\d)$/.test(c)) return isForce ? "moment" : "rotation";
  return isForce ? "force" : "disp";
};
const frameKind = k => /^(T|M2|M3)_/.test(k) ? "moment" : "force";
const ptLabel = p => `(${p.map(v => U.fmt("length", v, 2)).join(", ")})`;

export function plotFunctions(results, caseName) {
  const th = results && results.th_cases && results.th_cases[caseName];
  if (!th) return { T: [], fns: [] };
  const pf = th.plot_functions || null;
  const T = pf ? pf.t : [0].concat(th.t || []);
  const N = T.length;
  const pad = a => (Array.isArray(a) && a.length === N - 1) ? [0].concat(a) : (Array.isArray(a) && a.length === N ? a : null);
  const fns = [];
  const add = (group, id, label, kind, data) => { if (data && data.length === N) fns.push({ group, id, label, kind, data }); };
  const order = results.story_order || Object.keys(th.story_ux || {});
  const elev = results.story_elev || {};
  for (const [dir, key] of [["X", "story_ux"], ["Y", "story_uy"]]) {
    let prev = null, prevZ = 0;
    for (const s of order) {
      const u = pad((th[key] || {})[s]);
      add("Story displacement", `story:${dir}:${s}`, `${s} U${dir}`, "disp", u);
      const z = +elev[s] || 0, hgt = z - prevZ || 1;
      if (u) add("Story drift", `drift:${dir}:${s}`, `${s} drift ${dir}`, "none",
        u.map((v, i) => (v - (prev ? prev[i] : 0)) / hgt));
      prev = u; prevZ = z;
    }
  }
  add("Base reaction", "base:FX", "Base FX", "force", pad(th.base_FX));
  add("Base reaction", "base:FY", "Base FY", "force", pad(th.base_FY));
  if (th.energy) for (const k of ["input", "kinetic", "strain", "damping", "hysteretic", "error"])
    add("Energy", `energy:${k}`, `Energy ${k}`, "moment", pad(th.energy[k]));
  if (pf) {
    const gK = { acc: "accel", vel: "velocity", disp: "disp" };
    for (const grp of ["acc", "vel", "disp"]) for (const k of ["UX", "UY", "UZ"]) {
      const a = pf.ground[grp][k];
      if (a && a.some(v => v !== 0)) add("Ground motion", `ground:${grp}:${k}`, `Ground ${grp} ${k}`, gK[grp], a);
    }
    (pf.joints || []).forEach((j, ji) => {
      const nm = `Joint ${ptLabel(j.point)}`;
      for (const k of Object.keys(j.disp)) add(nm, `joint:${ji}:disp:${k}`, `${nm} ${k} rel`, DOF_KIND[k] || "disp", j.disp[k]);
      for (const k of ["UX", "UY", "UZ"]) {
        add(nm, `joint:${ji}:vel:${k}`, `${nm} V${k.slice(1)} rel`, "velocity", j.vel[k]);
        add(nm, `joint:${ji}:acc:${k}`, `${nm} A${k.slice(1)} rel`, "accel", j.acc[k]);
      }
      for (const k of ["UX", "UY", "UZ"]) {
        add(nm, `joint:${ji}:disp_abs:${k}`, `${nm} ${k} abs`, "disp", j.disp_abs[k]);
        add(nm, `joint:${ji}:vel_abs:${k}`, `${nm} V${k.slice(1)} abs`, "velocity", j.vel_abs[k]);
        add(nm, `joint:${ji}:acc_abs:${k}`, `${nm} A${k.slice(1)} abs`, "accel", j.acc_abs[k]);
      }
    });
    for (const [uid, lk] of Object.entries(pf.links || {})) {
      const nm = `Link ${uid}`;
      for (const c of lk.components) {
        add(nm, `link:${uid}:force:${c}`, `${nm} force ${c}`, linkKind(c, true), lk.force[c]);
        add(nm, `link:${uid}:def:${c}`, `${nm} deformation ${c}`, linkKind(c, false), lk.deformation[c]);
      }
      add(nm, `link:${uid}:work`, `${nm} work (loop area)`, "moment", lk.work);
    }
    for (const [uid, fr] of Object.entries(pf.frames || {}))
      for (const [k, v] of Object.entries(fr)) add(`Frame ${uid}`, `frame:${uid}:${k}`, `Frame ${uid} ${k}`, frameKind(k), v);
    for (const [key, hz] of Object.entries(pf.hinges || {})) {
      for (const k of ["R2", "R3"]) add(`Hinge ${key}`, `hinge:${key}:rot:${k}`, `Hinge ${key} ${k}`, "rotation", hz.rotation[k]);
      for (const k of ["M2", "M3"]) add(`Hinge ${key}`, `hinge:${key}:mom:${k}`, `Hinge ${key} ${k}`, "moment", hz.moment[k]);
    }
  }
  return { T, fns, pf };
}
const unitTag = kind => { const l = U.label(kind); return l ? ` (${l})` : ""; };

/* ================================================================
   module init
   ================================================================ */
export function initPlotFn(sky) {
  ensureCss();
  const S = sky.store;
  const toast = (t, m, k) => sky.toast && sky.toast(t, m, k || "info", 4000);
  const results = () => S.results;
  const state = { case: null, sel: [], mode: "time", xfn: null, tab: "fns",
    spec: { point: "ground", dir: null, damping: "0.02, 0.05", ord: "Sa", logT: false, data: null } };
  const api = async (path, body) => {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null; try { data = await res.json(); } catch { /* */ }
    if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
    return data;
  };

  /* ---------------- Show Plot Functions… ---------------- */
  function openPlotFunctions(initialTab) {
    const r = results();
    const m = S.model;
    const thNames = Object.keys((r && r.th_cases) || {});
    const modelTh = Object.keys((m && m.th_cases) || {});
    if (!thNames.includes(state.case)) state.case = thNames[0] || modelTh[0] || null;
    state.tab = initialTab || (thNames.length || !modelTh.length ? "fns" : "req");
    const body = el("div", { class: "pf-body" });
    const tabs = el("div", { class: "pf-tabs" });
    const panes = {};
    const mkTab = (id, label) => {
      const b = el("button", { class: "pf-tab", text: label, "data-tab": id, onclick: () => { state.tab = id; sync(); } });
      tabs.appendChild(b);
      panes[id] = el("div", { class: "pf-pane", "data-pane": id });
    };
    mkTab("fns", "Plot Functions");
    mkTab("spec", "Floor Response Spectrum");
    mkTab("req", "Output Requests");
    body.append(tabs, ...Object.values(panes));
    const err = pfErrorLine();
    body.appendChild(err);

    const caseSel = el("select", { id: "pfCase", onchange: () => { state.case = caseSel.value; state.sel = []; state.xfn = null; state.spec.data = null; renderFns(); renderSpec(); renderReq(); } });
    const allCases = [...new Set(thNames.concat(modelTh))];
    for (const n of allCases) caseSel.appendChild(option(n, thNames.includes(n) ? n : `${n} (not run)`, n === state.case));
    const caseRow = el("div", { class: "pf-row" }, [el("label", {}, ["Time-history case", caseSel]),
      el("span", { class: "pf-note", text: thNames.length ? "" : "Run the analysis to plot results." })]);
    body.insertBefore(caseRow, tabs);

    /* ---- functions pane ---- */
    let chart = null;
    function renderFns() {
      const p = panes.fns; p.textContent = "";
      const { T, fns } = plotFunctions(results(), state.case);
      if (!fns.length) { p.appendChild(el("div", { class: "pf-empty", text: "No time-history results for this case — run the analysis (Output Requests adds joint / link / frame series)." })); chart = null; return; }
      const byId = new Map(fns.map(f => [f.id, f]));
      state.sel = state.sel.filter(id => byId.has(id));
      if (!state.sel.length) {
        const th = ((S.model || {}).th_cases || {})[state.case] || {};
        const dir = th.direction === "Y" ? "Y" : "X";
        const order = results().story_order || [];
        const roof = order[order.length - 1];
        const def = (roof && byId.has(`story:${dir}:${roof}`)) ? `story:${dir}:${roof}` : fns[0].id;
        state.sel = [def];
      }
      const groups = [...new Set(fns.map(f => f.group))];
      const grpSel = el("select", { class: "pf-group", id: "pfGroup" });
      grpSel.appendChild(option("*", "All functions"));
      for (const gname of groups) grpSel.appendChild(option(gname, gname));
      const list = el("select", { multiple: true, size: 14, id: "pfFnList" });
      const fillList = () => {
        list.textContent = "";
        for (const f of fns) if (grpSel.value === "*" || f.group === grpSel.value)
          list.appendChild(option(f.id, f.label + unitTag(f.kind)));
      };
      grpSel.addEventListener("change", fillList);
      fillList();
      const addSel = () => {
        for (const o of list.selectedOptions) if (!state.sel.includes(o.value)) state.sel.push(o.value);
        draw(); renderSelList();
      };
      list.addEventListener("dblclick", addSel);
      const selList = el("ul", { class: "pf-sel-list", id: "pfSelList" });
      const renderSelList = () => {
        selList.textContent = "";
        state.sel.forEach((id, i) => {
          const f = byId.get(id); if (!f) return;
          selList.appendChild(el("li", { "data-id": id }, [
            el("span", { class: "pf-chip", style: `background:${PALETTE[i % PALETTE.length]}` }),
            el("span", { class: "pf-name", text: f.label, title: f.label }),
            el("button", { text: "×", title: "Remove", onclick: () => { state.sel.splice(i, 1); renderSelList(); draw(); } }),
          ]));
        });
      };
      // X-vs-Y mode
      const modeSel = el("select", { id: "pfMode" }, [option("time", "vs Time", state.mode === "time"), option("xy", "X vs Y", state.mode === "xy")]);
      const xSel = el("select", { id: "pfXfn" });
      for (const f of fns) xSel.appendChild(option(f.id, f.label + unitTag(f.kind), f.id === state.xfn));
      if (!state.xfn || !byId.has(state.xfn)) state.xfn = fns[0].id;
      xSel.value = state.xfn;
      modeSel.addEventListener("change", () => { state.mode = modeSel.value; xRow.hidden = state.mode !== "xy"; draw(); });
      xSel.addEventListener("change", () => { state.xfn = xSel.value; draw(); });
      const xRow = el("label", {}, ["X axis", xSel]); xRow.hidden = state.mode !== "xy";
      // quick hysteresis presets
      const hyst = el("select", { id: "pfHyst" }, [option("", "Hysteresis loop…")]);
      // largest loop first (per link / hinge, the governing component leads)
      const amp = f => f.data.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
      const presets = [];
      for (const f of fns) {
        const mm = /^link:(.+):force:(.+)$/.exec(f.id);
        const dz = mm && byId.get(`link:${mm[1]}:def:${mm[2]}`);
        if (mm && amp(f) > 1e-12 && dz && amp(dz) > 1e-12)
          presets.push([`${mm[1]}|${mm[2]}`, `Link ${mm[1]} ${mm[2]}`, mm[1], amp(f) * amp(dz)]);
        const hm = /^hinge:(.+):mom:(M\d)$/.exec(f.id);
        if (hm && amp(f) > 1e-12) presets.push([`h|${hm[1]}|${hm[2]}`, `Hinge ${hm[1]} ${hm[2]}`, "h" + hm[1], amp(f)]);
      }
      const gIdx = new Map();
      presets.forEach(p => { if (!gIdx.has(p[2])) gIdx.set(p[2], gIdx.size); });
      presets.sort((a, b) => (gIdx.get(a[2]) - gIdx.get(b[2])) || (b[3] - a[3]));
      for (const [v, t] of presets) hyst.appendChild(option(v, t));
      hyst.addEventListener("change", () => {
        const v = hyst.value; if (!v) return;
        const parts = v.split("|");
        if (parts[0] === "h") { state.xfn = `hinge:${parts[1]}:rot:R${parts[2].slice(1)}`; state.sel = [`hinge:${parts[1]}:mom:${parts[2]}`]; }
        else { state.xfn = `link:${parts[0]}:def:${parts[1]}`; state.sel = [`link:${parts[0]}:force:${parts[1]}`]; }
        state.mode = "xy"; modeSel.value = "xy"; xSel.value = state.xfn; xRow.hidden = false;
        hyst.value = ""; renderSelList(); draw();
      });
      const side = el("div", { class: "pf-side" }, [
        grpSel, list,
        el("div", { class: "pf-row" }, [
          pfBtn("Add ↓", "btn-small", addSel, "Add the highlighted functions to the plot"),
          pfBtn("Clear", "btn-small", () => { state.sel = []; renderSelList(); draw(); }, "Remove all plotted functions"),
        ]),
        el("div", { class: "pf-note", text: "Plotted (overlaid):" }), selList,
      ]);
      side.querySelector("button").id = "pfAdd";
      const chartHost = el("div", { class: "pf-chart-host", id: "pfChart" });
      const main = el("div", { class: "pf-main" }, [
        el("div", { class: "pf-row" }, [el("label", {}, ["Plot", modeSel]), xRow, hyst,
          pfBtn("Export CSV", "btn-small", exportCsv, "Download the plotted series (display units)")]),
        chartHost,
        el("div", { class: "pf-note", text: "Wheel = zoom · drag = pan · double-click = reset" }),
      ]);
      main.querySelector(".pf-row button").id = "pfCsv";
      p.appendChild(el("div", { class: "pf-grid" }, [side, main]));
      chart = new PfChart(chartHost);
      renderSelList();
      function draw() {
        const xf = byId.get(state.xfn);
        const series = state.sel.map(id => byId.get(id)).filter(Boolean).map((f, i) => ({
          name: f.label + unitTag(f.kind), color: PALETTE[i % PALETTE.length],
          x: state.mode === "xy" && xf ? xf.data.map(v => U.toDisplay(xf.kind, v)) : T,
          y: f.data.map(v => U.toDisplay(f.kind, v)),
        }));
        const first = byId.get(state.sel[0]);
        chart.setData({ series,
          xLabel: state.mode === "xy" && xf ? xf.label + unitTag(xf.kind) : "Time (s)",
          yLabel: first ? (state.sel.length > 1 ? "Value" : first.label) + unitTag(first.kind) : "",
          padX: state.mode === "xy" });
      }
      function exportCsv() {
        const sel = state.sel.map(id => byId.get(id)).filter(Boolean);
        if (!sel.length) return toast("Plot functions", "Nothing plotted.");
        const head = ["t (s)"].concat(sel.map(f => f.label + unitTag(f.kind)));
        const rows = [head.map(csvCell).join(",")];
        for (let i = 0; i < T.length; i++)
          rows.push([T[i]].concat(sel.map(f => U.toDisplay(f.kind, f.data[i]))).map(csvCell).join(","));
        const text = rows.join("\n") + "\n";
        api_.lastCsv = text;
        downloadText(`plotfn_${state.case}.csv`, text);
      }
      requestAnimationFrame(draw);
      api_.redraw = draw;
    }

    /* ---- floor response spectrum pane ---- */
    function renderSpec() {
      const p = panes.spec; p.textContent = "";
      const r = results();
      const th = ((S.model || {}).th_cases || {})[state.case];
      const res = r && r.th_cases && r.th_cases[state.case];
      if (!th || !res) { p.appendChild(el("div", { class: "pf-empty", text: "Run the analysis to compute floor response spectra for this case." })); return; }
      const pf = res.plot_functions;
      const ptSel = el("select", { id: "pfSpecPoint" }, [option("ground", "Ground motion (base)")]);
      for (const j of (pf && pf.joints) || []) ptSel.appendChild(option(JSON.stringify(j.point), `Joint ${ptLabel(j.point)} (recorded)`));
      const custom = el("input", { type: "text", id: "pfSpecXYZ", placeholder: `x, y, z (${U.label("length")})`, size: 18 });
      ptSel.value = typeof state.spec.point === "string" ? state.spec.point : JSON.stringify(state.spec.point);
      if (!ptSel.value) ptSel.value = "ground";
      const dirSel = el("select", { id: "pfSpecDir" }, ["X", "Y", "Z"].map(d => option(d, d, d === (state.spec.dir || th.direction || "X"))));
      const damp = el("input", { type: "text", id: "pfSpecDamp", value: state.spec.damping, size: 12, title: "Damping ratios, comma separated (e.g. 0.02, 0.05)" });
      const ordSel = el("select", { id: "pfSpecOrd" }, [["Sa", "Sa — absolute acceleration"], ["PSa", "PSa — pseudo acceleration"], ["Sv", "Sv — relative velocity"], ["PSv", "PSv — pseudo velocity"], ["Sd", "Sd — relative displacement"]].map(([v, t]) => option(v, t, v === state.spec.ord)));
      const logChk = el("input", { type: "checkbox", id: "pfSpecLog" }); logChk.checked = state.spec.logT;
      const host = el("div", { class: "pf-chart-host", id: "pfSpecChart" });
      const info = el("div", { class: "pf-note", id: "pfSpecInfo" });
      const go = pfBtn("Compute", "btn-run btn-small", () => compute(), "Compute the response spectrum (Nigam–Jennings exact recurrence)");
      go.id = "pfSpecGo";
      const csvB = pfBtn("Export CSV", "btn-small", () => {
        const d = state.spec.data; if (!d) return toast("Floor spectrum", "Compute a spectrum first.");
        const kind = ordKind(state.spec.ord);
        const head = ["T (s)"].concat(d.spectra.map(s => `${state.spec.ord} ζ=${s.damping}${unitTag(kind)}`));
        const rows = [head.map(csvCell).join(",")];
        d.periods.forEach((T, i) => rows.push([T].concat(d.spectra.map(s => U.toDisplay(kind, s[state.spec.ord][i]))).map(csvCell).join(",")));
        api_.lastSpecCsv = rows.join("\n") + "\n";
        downloadText(`floor_spectrum_${state.case}.csv`, api_.lastSpecCsv);
      });
      csvB.id = "pfSpecCsv";
      p.append(
        el("div", { class: "pf-row" }, [el("label", {}, ["Point", ptSel]), el("label", {}, ["or", custom]),
          el("label", {}, ["Direction", dirSel]), el("label", {}, ["Damping", damp])]),
        el("div", { class: "pf-row" }, [el("label", {}, ["Ordinate", ordSel]), el("label", {}, [logChk, "log period axis"]), go, csvB]),
        host, info);
      const chart = new PfChart(host);
      const ordKind = o => (o === "Sa" || o === "PSa") ? "accel" : (o === "Sd" ? "disp" : "velocity");
      const draw = () => {
        const d = state.spec.data;
        if (!d) { chart.setData({ series: [], xLabel: "Period T (s)", yLabel: "" }); return; }
        const kind = ordKind(state.spec.ord), log = state.spec.logT;
        const keep = d.periods.map((T, i) => (!log || T > 0) ? i : -1).filter(i => i >= 0);
        chart.setData({
          series: d.spectra.map((s, i) => ({ name: `${state.spec.ord} ζ = ${s.damping}`, color: PALETTE[i % PALETTE.length],
            x: keep.map(k => log ? Math.log10(d.periods[k]) : d.periods[k]), y: keep.map(k => U.toDisplay(kind, s[state.spec.ord][k])) })),
          xLabel: log ? "Period T (s, log)" : "Period T (s)", yLabel: state.spec.ord + unitTag(kind), logX: log });
        const pt = d.point === "ground" ? "ground" : `joint ${ptLabel(d.point)}`;
        info.textContent = `${d.case || ""} · ${pt} · ${d.direction} · PGA ${U.fmtU("accel", d.pga, 3)} · ${d.method} · source: ${d.source}`;
      };
      ordSel.addEventListener("change", () => { state.spec.ord = ordSel.value; draw(); });
      logChk.addEventListener("change", () => { state.spec.logT = logChk.checked; draw(); });
      async function compute() {
        pfShowError(err, "");
        let point = ptSel.value === "ground" ? "ground" : JSON.parse(ptSel.value);
        if (custom.value.trim()) {
          const parts = custom.value.split(/[,\s]+/).filter(Boolean).map(t => U.parse("length", t));
          if (parts.length !== 3 || !parts.every(isFinite)) return pfShowError(err, "Point must be x, y, z");
          point = parts;
        }
        const damping = damp.value.split(/[,\s]+/).filter(Boolean).map(Number);
        if (!damping.length || !damping.every(z => isFinite(z) && z >= 0 && z < 1)) return pfShowError(err, "Damping ratios must be in [0, 1)");
        state.spec.point = point; state.spec.dir = dirSel.value; state.spec.damping = damp.value;
        const req = { case: state.case, point, direction: dirSel.value, damping };
        go.disabled = true;
        try {
          state.spec.data = S.mock ? pfMockSpectrum(S.model, results(), req) : await api("/api/plotfn/spectrum", req);
          draw();
        } catch (e) { pfShowError(err, e.message); }
        finally { go.disabled = false; }
      }
      api_.computeSpectrum = compute;
      requestAnimationFrame(draw);
    }

    /* ---- output requests pane (model edit, written on OK) ---- */
    let draftReq = null, origJson = null;
    function renderReq() {
      const p = panes.req; p.textContent = "";
      const th = ((S.model || {}).th_cases || {})[state.case];
      if (!th) { p.appendChild(el("div", { class: "pf-empty", text: "Define a time-history case first (Define → Time-History Cases…)." })); draftReq = null; return; }
      origJson = canon(th.output_requests ?? null);
      draftReq = th.output_requests ? JSON.parse(JSON.stringify(th.output_requests)) : {};
      const wrap = el("div", { class: "pf-req" });
      wrap.appendChild(el("p", { class: "pf-note", html: `Full time series to record for <b>${esc(state.case)}</b> (ETABS plot-function outputs). ` +
        "Story displacements, drifts, base reactions and energy are always available; save the model and re-run to record these." }));
      // joints
      wrap.appendChild(el("h4", { text: `Joints — one "x, y, z" per line (${U.label("length")})` }));
      const ta = el("textarea", { id: "pfReqJoints" });
      ta.value = (draftReq.joints || []).map(pt => pt.map(v => U.inputValue("length", v)).join(", ")).join("\n");
      const pts = jointCandidates();
      const pick = el("select", { id: "pfReqPick" }, [option("", "Add a model joint…")]);
      for (const pt of pts) pick.appendChild(option(JSON.stringify(pt), ptLabel(pt)));
      pick.addEventListener("change", () => {
        if (!pick.value) return;
        const pt = JSON.parse(pick.value);
        ta.value = (ta.value.trim() ? ta.value.trim() + "\n" : "") + pt.map(v => U.inputValue("length", v)).join(", ");
        pick.value = "";
      });
      wrap.append(ta, el("div", { class: "pf-row" }, [pick]));
      // links
      const links = (S.model.links || []);
      wrap.appendChild(el("h4", { text: "Links — force / deformation (hysteresis)" }));
      const lbox = el("div", { class: "pf-checks", id: "pfReqLinks" });
      if (!links.length) lbox.appendChild(el("span", { class: "pf-note", text: "No links in the model." }));
      for (const lk of links) {
        const c = el("input", { type: "checkbox", value: lk.uid });
        c.checked = (draftReq.links || []).includes(lk.uid);
        lbox.appendChild(el("label", {}, [c, `${lk.uid} · ${lk.link_type || "elastic"}`]));
      }
      wrap.appendChild(lbox);
      // frames
      wrap.appendChild(el("h4", { text: "Frames — end forces (comma-separated uids)" }));
      const fin = el("input", { type: "text", id: "pfReqFrames", size: 50 });
      fin.value = (draftReq.frames || []).join(", ");
      const addSel = pfBtn("Add selected frames", "btn-small", () => {
        const uids = (S.selection || []).filter(rf => rf.type === "member").map(rf => rf.uid);
        if (!uids.length) return toast("Output requests", "Select frame members in the model first.");
        const cur = fin.value.split(/[,\s]+/).filter(Boolean);
        fin.value = [...new Set(cur.concat(uids))].join(", ");
      });
      wrap.appendChild(el("div", { class: "pf-row" }, [fin, addSel]));
      // hinges
      const hc = el("input", { type: "checkbox", id: "pfReqHinges" }); hc.checked = !!draftReq.hinges;
      wrap.appendChild(el("div", { class: "pf-row" }, [el("label", {}, [hc, "Hinge springs (rotation / moment) — nonlinear cases"])]));
      p.appendChild(wrap);
      const taOrig = ta.value, finOrig = fin.value;
      api_.readReq = () => {
        const out = {};
        const lines = ta.value.split("\n").map(l => l.trim()).filter(Boolean);
        // lines left untouched keep their exact SI point (no display round-off)
        const origLines = new Map();
        taOrig.split("\n").forEach((l, i) => { if (draftReq.joints && draftReq.joints[i]) origLines.set(l.trim(), draftReq.joints[i]); });
        if (ta.value === taOrig && draftReq.joints) out.joints = draftReq.joints;
        else if (lines.length) {
          out.joints = lines.map(l => {
            if (origLines.has(l)) return origLines.get(l);
            const v = l.split(/[,\s]+/).filter(Boolean).map(t => U.parse("length", t));
            if (v.length !== 3 || !v.every(isFinite)) throw new Error(`Joint "${l}" must be x, y, z`);
            return v;
          });
        }
        const lsel = [...lbox.querySelectorAll("input:checked")].map(c => c.value);
        const lorig = draftReq.links || [];
        if (lsel.length) out.links = (lsel.length === lorig.length && lsel.every(u => lorig.includes(u))) ? lorig : lsel;
        const fr = fin.value === finOrig && draftReq.frames ? draftReq.frames : fin.value.split(/[,\s]+/).filter(Boolean);
        const known = new Set((S.model.members || []).map(mm => mm.uid));
        const bad = fr.find(u => !known.has(u));
        if (bad) throw new Error(`Unknown frame member '${bad}'`);
        if (fr.length) out.frames = [...new Set(fr)];
        if (hc.checked) out.hinges = true;
        else if (draftReq.hinges === false) out.hinges = false;
        return out;
      };
    }
    function jointCandidates() {
      const seen = new Map();
      const addP = p => { const k = p.map(v => (+v).toFixed(4)).join(","); if (!seen.has(k)) seen.set(k, p.map(Number)); };
      for (const mm of S.model.members || []) { addP(mm.pi); addP(mm.pj); }
      for (const lk of S.model.links || []) { addP(lk.pi); addP(lk.pj); }
      return [...seen.values()].sort((a, b) => b[2] - a[2] || a[0] - b[0] || a[1] - b[1]);
    }

    function sync() {
      tabs.querySelectorAll(".pf-tab").forEach(b => b.classList.toggle("is-active", b.dataset.tab === state.tab));
      for (const [id, pane] of Object.entries(panes)) pane.hidden = id !== state.tab;
      if (state.tab === "fns" && api_.redraw) requestAnimationFrame(api_.redraw);
    }

    const ok = pfBtn("OK", "btn-run", () => {
      pfShowError(err, "");
      const th = ((S.model || {}).th_cases || {})[state.case];
      if (th && api_.readReq) {
        let req;
        try { req = api_.readReq(); } catch (e) { state.tab = "req"; sync(); return pfShowError(err, e.message); }
        const now = Object.keys(req).length ? canon(req) : "null";
        if (now !== origJson && !(now === "null" && origJson === "{}")) {
          if (now !== "null") th.output_requests = req;
          else delete th.output_requests;
          sky.markDirty && sky.markDirty();
          toast("Output requests", `${state.case}: saved to the model — Save Model and re-run to record the series.`);
        }
      }
      d.close();
    });
    ok.id = "pfOk";
    const cancel = pfBtn("Close", "", () => d.close());
    const fb = pfFootBar("Plot functions use the last analysis results; units follow Options → Units.", [cancel, ok]);
    const d = pfDialog("pfDlg", { title: "Plot Functions", wide: true, body, foot: fb.wrap });
    d.el.classList.add("pf-dlg");
    renderFns(); renderSpec(); renderReq(); sync();
    return d;
  }

  /* ---------------- Story Response Plots… ---------------- */
  const SR_QTY = [
    ["disp", "Max story displacement", "disp"],
    ["drift", "Story drift ratio", "none"],
    ["shear", "Story shear", "force"],
    ["otm", "Overturning moment", "moment"],
    ["stiff", "Story stiffness", "stiffness"],
  ];
  const srState = { case: null, dir: "X", qty: "disp", limit: "", values: false };
  function storySources(r) {
    const out = [];
    for (const n of Object.keys(r.cases || {})) out.push([`c:${n}`, `Case: ${n}`, r.cases[n]]);
    for (const n of Object.keys(r.combos || {})) out.push([`k:${n}`, `Combo: ${n}${r.combos[n].min ? " (envelope)" : ""}`, r.combos[n]]);
    for (const n of Object.keys(r.rs_cases || {})) out.push([`rs:${n}`, `RS: ${n}`, r.rs_cases[n]]);
    for (const n of Object.keys(r.th_cases || {})) out.push([`th:${n}`, `TH: ${n} (max |·|)`, { story: (r.th_cases[n].peaks || {}).story || {}, _th: true }]);
    return out;
  }
  function storyProfile(r, key, dir, qty) {
    const src = storySources(r).find(s => s[0] === key);
    if (!src) return null;
    const order = r.story_order || [];
    const elev = r.story_elev || {};
    const stories = order.map(s => ({ name: s, z: +elev[s] || 0 }));
    const baseZ = 0;
    const levels = [{ name: "Base", z: baseZ }].concat(stories);
    const lc = dir.toLowerCase();
    const make = (cd, tag) => {
      const st = cd.story || {};
      const get = (s, k) => { const v = (st[s] || {})[k]; return isNum(v) ? v : NaN; };
      let prevZ = baseZ;
      const hs = stories.map(s => { const h = s.z - prevZ; prevZ = s.z; return h || 1; });
      const vals = {};
      const at = (pts) => { stories.forEach((s, i) => { vals[s.name] = pts[i][0]; }); return pts; };
      if (qty === "disp") {
        const pts = at(stories.map(s => [get(s.name, "u" + lc), s.z]));
        vals.Base = 0;
        return { pts: [[0, baseZ]].concat(pts), tag, vals };
      }
      if (qty === "drift") return { pts: at(stories.map(s => [get(s.name, "drift_" + lc), s.z])), tag, vals };
      const V = stories.map(s => get(s.name, "shear_" + lc));
      if (qty === "shear") {
        const pts = [], marks = [];
        let zb = baseZ;
        stories.forEach((s, i) => { pts.push([V[i], zb], [V[i], s.z]); marks.push([V[i], (zb + s.z) / 2]); vals[s.name] = V[i]; zb = s.z; });
        return { pts, marks, tag, vals };
      }
      if (qty === "otm") {
        // M at the bottom of story i = sum_{j >= i} V_j h_j (shear integrated
        // down the height; for RS / TH envelopes an upper bound of the
        // modal-combined moment)
        const M = new Array(stories.length).fill(0);
        for (let i = stories.length - 1; i >= 0; i--) M[i] = (i + 1 < stories.length ? M[i + 1] : 0) + V[i] * hs[i];
        const pts = stories.map((s, i) => [M[i], i ? stories[i - 1].z : baseZ]);
        stories.forEach((s, i) => { vals[i ? stories[i - 1].name : "Base"] = M[i]; });
        vals[stories[stories.length - 1].name] = 0;
        pts.push([0, stories[stories.length - 1].z]);
        return { pts, tag, vals };
      }
      if (qty === "stiff") {
        const ks = (r.story_stiffness || {})[key.slice(key.indexOf(":") + 1)];
        return { pts: at(stories.map((s, i) => {
          const kk = ks && ks[s.name] && ks[s.name]["k" + lc];
          if (isNum(kk) && kk > 0) return [kk, s.z];
          const dr = get(s.name, "drift_" + lc) * hs[i];
          return [Math.abs(dr) > 1e-15 ? Math.abs(V[i] / dr) : NaN, s.z];
        })), tag, vals };
      }
      return null;
    };
    const cd = src[2];
    const out = [make(cd, "max")];
    if (cd.min) out.push(make(cd.min, "min"));
    return { levels, series: out.filter(Boolean), label: src[1] };
  }
  function openStoryResponse() {
    const r = results();
    const body = el("div", { class: "pf-body" });
    const err = pfErrorLine();
    if (!r) {
      body.appendChild(el("div", { class: "pf-empty", text: "Run the analysis first — story response plots use the static, combination, response-spectrum and time-history story results." }));
    }
    const srcs = r ? storySources(r) : [];
    if (srcs.length && !srcs.some(s => s[0] === srState.case)) {
      const lat = srcs.find(s => Object.values(s[2].story || {}).some(v => Math.abs(v.drift_x || 0) + Math.abs(v.drift_y || 0) > 1e-12));
      srState.case = (lat || srcs[0])[0];
    }
    const caseSel = el("select", { id: "srCase" });
    for (const [k, lab] of srcs) caseSel.appendChild(option(k, lab, k === srState.case));
    const dirSel = el("select", { id: "srDir" }, ["X", "Y"].map(dd => option(dd, dd, dd === srState.dir)));
    const qSel = el("select", { id: "srQty" }, SR_QTY.map(([k, lab]) => option(k, lab, k === srState.qty)));
    const lim = el("input", { type: "text", id: "srLimit", size: 10, value: srState.limit });
    const limLab = el("span", { id: "srLimitUnit" });
    const vals = el("input", { type: "checkbox", id: "srValues" }); vals.checked = srState.values;
    const host = el("div", { class: "pf-chart-host", id: "srChart" });
    const tbl = el("div", { class: "table-scroll", id: "srTable" });
    if (r) body.append(
      el("div", { class: "pf-row" }, [el("label", {}, ["Case / combo", caseSel]), el("label", {}, ["Direction", dirSel]), el("label", {}, ["Display", qSel])]),
      el("div", { class: "pf-row" }, [el("label", {}, ["Limit line", lim, limLab]), el("label", {}, [vals, "show values"]),
        pfBtn("Export CSV", "btn-small", () => exportSr(), "Download the plotted profile")]),
      host, tbl, err);
    let last = null;
    const draw = () => {
      if (!r) return;
      pfShowError(err, "");
      srState.case = caseSel.value; srState.dir = dirSel.value; srState.qty = qSel.value; srState.values = vals.checked;
      const q = SR_QTY.find(x => x[0] === srState.qty);
      const kind = q[2];
      limLab.textContent = kind === "none" ? "(ratio, e.g. 0.02)" : `(${U.label(kind)})`;
      const prof = storyProfile(r, srState.case, srState.dir, srState.qty);
      if (!prof) { host.textContent = ""; return; }
      const series = prof.series.map((s, i) => ({
        name: `${prof.label} ${srState.dir}${prof.series.length > 1 ? " " + s.tag : ""}`,
        color: PALETTE[i % PALETTE.length],
        pts: s.pts.map(([v, z]) => [U.toDisplay(kind, v), z]),
        marks: s.marks ? s.marks.map(([v, z]) => [U.toDisplay(kind, v), z]) : null,
      }));
      const limits = [];
      srState.limit = lim.value;
      if (lim.value.trim()) {
        const lv = kind === "none" ? Number(lim.value) : U.parse(kind, lim.value);
        if (!isFinite(lv)) pfShowError(err, "Limit must be a number");
        else limits.push({ value: U.toDisplay(kind, lv), label: `limit ${lim.value.trim()}` },
          ...(kind === "none" || prof.series.length > 1 ? [{ value: -U.toDisplay(kind, lv), label: "" }] : []));
      }
      if (kind === "none" && limits.length && !series.some(s => s.pts.some(p => p[0] < 0))) limits.splice(1);
      profileChart(host, { levels: prof.levels.map(l => ({ name: l.name, z: l.z })), series, limits,
        xLabel: q[1] + unitTag(kind), showValues: srState.values });
      last = { prof, kind, q };
      // table
      const rows = prof.levels.slice().reverse().map(l => {
        const cells = prof.series.map(s => {
          const v = s.vals[l.name];
          return isNum(v) ? (kind === "none" ? v.toExponential(3) : U.fmt(kind, v, 3)) : "—";
        });
        return `<tr><td class="txt">${esc(l.name)}</td><td>${U.fmt("length", l.z, 2)}</td>${cells.map(c => `<td>${c}</td>`).join("")}</tr>`;
      });
      tbl.innerHTML = `<table class="data-table"><thead><tr><th class="txt">Story</th><th>Elevation (${esc(U.label("length"))})</th>${
        prof.series.map(s => `<th>${esc(q[1])} ${esc(s.tag)}${esc(unitTag(kind))}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`;
    };
    function exportSr() {
      if (!last) return;
      const { prof, kind, q } = last;
      const head = ["Story", "Elevation" + unitTag("length")].concat(prof.series.map(s => `${q[1]} ${s.tag}${unitTag(kind)}`));
      const lines = [head.map(csvCell).join(",")];
      prof.levels.forEach(l => {
        const vals2 = prof.series.map(s => isNum(s.vals[l.name]) ? U.toDisplay(kind, s.vals[l.name]) : "");
        lines.push([l.name, U.toDisplay("length", l.z)].concat(vals2).map(csvCell).join(","));
      });
      api_.lastStoryCsv = lines.join("\n") + "\n";
      downloadText(`story_response_${srState.qty}.csv`, api_.lastStoryCsv);
    }
    [caseSel, dirSel, qSel, vals].forEach(x => x.addEventListener("change", draw));
    lim.addEventListener("input", draw);
    const close = pfBtn("Close", "btn-run", () => d.close());
    close.id = "srClose";
    const fb = pfFootBar("Profiles from the last analysis (static / combo / RS envelopes, TH peaks).", [close]);
    const d = pfDialog("srDlg", { title: "Story Response Plots", wide: true, body, foot: fb.wrap });
    d.el.classList.add("pf-dlg");
    api_.drawStory = draw;
    requestAnimationFrame(draw);
    return d;
  }

  /* ---------------- 3D force / stress diagrams ---------------- */
  const f3 = { on: false, case: null, comp: "M3", scale: 1, reactions: true, labels: true, fill: true };
  const COMP_DIR = { M3: "y", V2: "y", N: "y", T: "y", M2: "z", V3: "z" };
  const COMP_KIND = { M3: "moment", M2: "moment", T: "moment", V2: "force", V3: "force", N: "force" };
  function caseOptions(r) {
    const out = [];
    for (const n of Object.keys(r.cases || {})) out.push([n, n]);
    for (const n of Object.keys(r.combos || {})) out.push([n, `Combo: ${n}`]);
    for (const n of Object.keys(r.rs_cases || {})) out.push([`rs:${n}`, `RS: ${n}`]);
    return out;
  }
  function f3CaseData(r, name) {
    if (!r || !name) return null;
    if (name.startsWith("rs:")) return (r.rs_cases || {})[name.slice(3)] || null;
    return (r.cases || {})[name] || (r.combos || {})[name] || null;
  }
  function localAxes(m) {
    const d = [m.pj[0] - m.pi[0], m.pj[1] - m.pi[1], m.pj[2] - m.pi[2]];
    const L = Math.hypot(...d) || 1;
    const x = d.map(v => v / L);
    const vertical = Math.hypot(d[0], d[1]) < 1e-6;
    let vxz = vertical ? [1, 0, 0] : [x[1], -x[0], 0];
    const nv = Math.hypot(...vxz) || 1; vxz = vxz.map(v => v / nv);
    const cr = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    let y = cr(vxz, x); const ny = Math.hypot(...y) || 1; y = y.map(v => v / ny);
    let z = cr(x, y);
    const ang = (m.angle || 0) * Math.PI / 180;
    if (Math.abs(ang) > 1e-12) {
      const c = Math.cos(ang), s = Math.sin(ang);
      const y2 = y.map((v, i) => c * v + s * z[i]), z2 = z.map((v, i) => c * v - s * y[i]);
      y = y2; z = z2;
    }
    return { x, y, z, L };
  }
  function f3Overlay(ctx, P, viewer) {
    if (!f3.on) return;
    const r = results(), m = S.model;
    if (!r || !m) return;
    const cd = f3CaseData(r, f3.case);
    if (!cd) return;
    const st = cd.member_stations || {};
    const comp = f3.comp, kind = COMP_KIND[comp];
    let vmax = 0;
    for (const s of Object.values(st)) for (const v of (s[comp] || [])) vmax = Math.max(vmax, Math.abs(v));
    const R = viewer.radius || 10;
    const k = vmax > 1e-12 ? R * 0.07 * (f3.scale || 1) / vmax : 0;
    const drawn = { members: 0, arrows: 0 };
    const proj = p => { const c = P.toCam(p); return c[2] > P.near ? P.proj(c) : null; };
    ctx.save();
    ctx.lineWidth = 1.2;
    ctx.font = "600 10px -apple-system, 'Segoe UI', sans-serif";
    // ETABS convention: moments drawn on the tension side (-M), shears / axial as +
    const sgn = comp === "M3" || comp === "M2" ? -1 : 1;
    if (k > 0) for (const mm of m.members || []) {
      const s = st[mm.uid];
      if (!s || !s.x || !s[comp]) continue;
      const ax = localAxes(mm), dir = ax[COMP_DIR[comp]];
      const base = s.x.map(x => [0, 1, 2].map(i => mm.pi[i] + ax.x[i] * x));
      const tip = s.x.map((x, i) => base[i].map((b, j) => b + dir[j] * sgn * s[comp][i] * k));
      const pb = base.map(proj), pt = tip.map(proj);
      if (pb.some(p => !p) || pt.some(p => !p)) continue;
      drawn.members++;
      for (let i = 0; i < pb.length - 1; i++) {
        const pos = (s[comp][i] + s[comp][i + 1]) >= 0;
        ctx.fillStyle = pos ? "rgba(53,181,229,0.28)" : "rgba(229,103,103,0.28)";
        ctx.strokeStyle = pos ? "rgba(53,181,229,0.95)" : "rgba(229,103,103,0.95)";
        if (f3.fill) {
          ctx.beginPath(); ctx.moveTo(pb[i].x, pb[i].y); ctx.lineTo(pt[i].x, pt[i].y);
          ctx.lineTo(pt[i + 1].x, pt[i + 1].y); ctx.lineTo(pb[i + 1].x, pb[i + 1].y); ctx.closePath(); ctx.fill();
        }
        ctx.beginPath(); ctx.moveTo(pt[i].x, pt[i].y); ctx.lineTo(pt[i + 1].x, pt[i + 1].y); ctx.stroke();
      }
      if (f3.labels) {
        let im = 0;
        s[comp].forEach((v, i) => { if (Math.abs(v) > Math.abs(s[comp][im])) im = i; });
        const v = s[comp][im];
        if (Math.abs(v) > vmax * 0.5) {
          ctx.fillStyle = "rgba(232,237,243,0.92)";
          ctx.textAlign = "center"; ctx.textBaseline = "bottom";
          ctx.fillText(U.fmt(kind, v, 1), pt[im].x, pt[im].y - 2);
        }
      }
    }
    // reaction arrows
    if (f3.reactions && cd.reactions && r.nodes) {
      let fmax = 0;
      for (const v of Object.values(cd.reactions)) for (let i = 0; i < 3; i++) fmax = Math.max(fmax, Math.abs(v[i] || 0));
      const ka = fmax > 1e-12 ? R * 0.09 / fmax : 0;
      const cols = ["#e66767", "#34c384", "#35b5e5"];
      if (ka > 0) for (const [tag, v] of Object.entries(cd.reactions)) {
        const p0 = r.nodes[tag]; if (!p0) continue;
        for (let i = 0; i < 3; i++) {
          const f = v[i] || 0;
          if (Math.abs(f) < fmax * 0.02) continue;
          const e = [0, 0, 0]; e[i] = 1;
          const len = Math.max(Math.abs(f) * ka, R * 0.02);
          // arrow points in the reaction direction, head at the support
          const tail = p0.map((c, j) => c - e[j] * Math.sign(f) * len);
          const a = proj(tail), b = proj(p0);
          if (!a || !b) continue;
          drawn.arrows++;
          ctx.strokeStyle = cols[i]; ctx.fillStyle = cols[i]; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
          const ang = Math.atan2(b.y - a.y, b.x - a.x), hl = 7;
          ctx.beginPath(); ctx.moveTo(b.x, b.y);
          ctx.lineTo(b.x - hl * Math.cos(ang - 0.4), b.y - hl * Math.sin(ang - 0.4));
          ctx.lineTo(b.x - hl * Math.cos(ang + 0.4), b.y - hl * Math.sin(ang + 0.4)); ctx.closePath(); ctx.fill();
          if (f3.labels) {
            ctx.textAlign = "center"; ctx.textBaseline = "middle";
            ctx.fillText(`${"XYZ"[i]} ${U.fmt("force", f, 1)}`, a.x, a.y - 7);
          }
        }
      }
    }
    // legend box
    ctx.textAlign = "left"; ctx.textBaseline = "top";
    ctx.fillStyle = "rgba(154,167,180,0.95)";
    ctx.fillText(`${comp} diagram · ${f3.case}${vmax ? ` · max ${U.fmtU(kind, vmax, 1)}` : ""}${f3.reactions ? " · reactions" : ""}`, 12, 10);
    ctx.restore();
    api_.f3Drawn = drawn;
  }
  function installOverlay() {
    const v = sky.viewer;
    if (v && v.pfOverlay !== f3Overlay) v.pfOverlay = f3Overlay;
  }
  function refresh3d() { installOverlay(); if (sky.viewer) sky.viewer._dirty = true; }
  function openForces3d() {
    const r = results();
    const body = el("div", { class: "pf-body" });
    const err = pfErrorLine();
    if (!r) {
      body.appendChild(el("div", { class: "pf-empty", text: "Run the analysis first — the diagrams use the member station forces and support reactions." }));
    }
    const opts = r ? caseOptions(r) : [];
    if (opts.length && !opts.some(o => o[0] === f3.case)) f3.case = (opts.find(o => o[0] === S.caseName) || opts[0])[0];
    const caseSel = el("select", { id: "f3Case" }, opts.map(([v, t]) => option(v, t, v === f3.case)));
    const compSel = el("select", { id: "f3Comp" }, [["M3", "Moment 3-3 (M3)"], ["M2", "Moment 2-2 (M2)"], ["V2", "Shear 2-2 (V2)"], ["V3", "Shear 3-3 (V3)"], ["N", "Axial (P)"], ["T", "Torsion (T)"]].map(([v, t]) => option(v, t, v === f3.comp)));
    const scale = el("input", { type: "number", id: "f3Scale", step: "0.1", min: "0.05", value: String(f3.scale) });
    const reac = el("input", { type: "checkbox", id: "f3Reac" }); reac.checked = f3.reactions;
    const lab = el("input", { type: "checkbox", id: "f3Labels" }); lab.checked = f3.labels;
    const fill = el("input", { type: "checkbox", id: "f3Fill" }); fill.checked = f3.fill;
    if (r) body.append(
      el("div", { class: "pf-row" }, [el("label", {}, ["Case / combo", caseSel]), el("label", {}, ["Component", compSel])]),
      el("div", { class: "pf-row" }, [el("label", {}, ["Scale ×", scale]), el("label", {}, [fill, "fill diagram"]),
        el("label", {}, [lab, "show values"]), el("label", {}, [reac, "support reactions (arrows)"])]),
      el("p", { class: "pf-note", text: "Moments are drawn on the tension side (ETABS); shear / axial with their sign. Shown in the 3D view; toggle with Display → Force Diagrams in 3D View." }),
      err);
    const apply = () => {
      const sc = Number(scale.value);
      if (!(isFinite(sc) && sc > 0)) { pfShowError(err, "Scale must be > 0"); return false; }
      Object.assign(f3, { case: caseSel.value, comp: compSel.value, scale: sc, reactions: reac.checked, labels: lab.checked, fill: fill.checked, on: true });
      sky.setMode && sky.setMode("analyze");
      sky.switchTab && sky.switchTab("view3d");
      refresh3d();
      return true;
    };
    const ok = pfBtn("Show", "btn-run", () => { if (!r) return d.close(); if (apply()) d.close(); });
    ok.id = "f3Ok";
    const off = pfBtn("Hide diagrams", "", () => { f3.on = false; refresh3d(); d.close(); });
    off.id = "f3Off";
    const cancel = pfBtn("Cancel", "", () => d.close());
    const fb = pfFootBar("Force / stress diagrams in the 3D view.", [cancel, off, ok]);
    const d = pfDialog("f3Dlg", { title: "Force/Stress Diagrams (3D)", body, foot: fb.wrap });
    d.el.classList.add("pf-dlg", "pf-dlg-narrow");
    return d;
  }

  const api_ = {
    openPlotFunctions, openStoryResponse, openForces3d, plotFunctions, storyProfile,
    lastCsv: null, lastSpecCsv: null, lastStoryCsv: null, f3,
    mockExtend: pfMockExtend,
  };
  sky.pf = api_;
  sky.openPlotFunctions = openPlotFunctions;
  sky.openStoryResponse = openStoryResponse;
  sky.openForces3d = openForces3d;
  sky.forces3d = {
    on: () => f3.on,
    toggle: () => {
      if (!f3.on && !results()) { toast("Force diagrams", "Run the analysis first."); return; }
      if (!f3.on && !f3.case) { openForces3d(); return; }
      f3.on = !f3.on;
      if (f3.on) { sky.setMode && sky.setMode("analyze"); sky.switchTab && sky.switchTab("view3d"); }
      refresh3d();
    },
    state: f3,
  };
  installOverlay();
}
