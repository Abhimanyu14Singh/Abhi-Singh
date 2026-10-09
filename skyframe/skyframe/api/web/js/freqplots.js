/* SkyFrame — analysis-result displays for the frequency-domain, energy,
   pushover-control and modal-load-participation backends (CONTRACT.md):

     • Frequency tab (new): Steady State — frequency-response plot (amplitude
       or phase vs Hz) for a chosen joint/DOF, base reaction or story response
       + peak tables; PSD — RMS tables (joint / base / story) and response-PSD
       curves (log-log option).
     • Time History tab: Energy card — input, kinetic, strain, damping,
       hysteretic (stacked or lines) and the energy error over time.
     • Pushover tab: capacity curve labelled with the control joint / DOF and
       the applied distribution shape (normalised story forces).
     • Modal tab: Load Participation (on demand, POST
       /api/analyze/load_participation), cached until the next analysis.

   Inline SVG only; every number is converted through units.js. Cases that
   did not run (case_status) are listed in each view. */

import U from "./units.js";
import { esc, skyApi, VirtualGrid, statusChip, downloadText } from "./tables.js";

const NS = "http://www.w3.org/2000/svg";
function S(tag, attrs = {}, text) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}
const h = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
};

/* ---------------------------------------------------------------- formatting */
/** Short display number with ~4 significant digits (axis ticks, tooltips). */
export function num(v, sig = 4) {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a >= 1e6 || a < 1e-3) return v.toExponential(Math.max(0, sig - 2));
  const d = Math.max(0, sig - 1 - Math.floor(Math.log10(a)));
  return (+v.toFixed(Math.min(d, 6))).toString();
}
/** units.js-formatted display string for an SI value (kind "none" = unitless). */
const fmtK = (kind, si, dec) => kind === "none" ? num(si) : U.fmt(kind, si, dec);
const DOFS = ["UX", "UY", "UZ", "RX", "RY", "RZ"];
const dofKind = i => i < 3 ? "disp" : "rotation";
const baseKind = k => k[0] === "M" ? "moment" : "force";
const storyKind = k => k.startsWith("drift") ? "none" : "disp";
/** Response-PSD display: (unit)²/Hz — converted by the SQUARE of the factor. */
const psdDisp = (kind, v) => kind === "none" ? v : Math.pow(U.toDisplay(kind, Math.sqrt(Math.max(v, 0))), 2);
const psdLabel = kind => { const l = kind === "none" ? "" : U.label(kind); return l ? `${l}²/Hz` : "1/Hz"; };

/* ---------------------------------------------------------------- charts */
function niceStep(span, n) {
  const raw = Math.max(span, 1e-300) / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const r = raw / mag;
  return (r <= 1 ? 1 : r <= 2 ? 2 : r <= 2.5 ? 2.5 : r <= 5 ? 5 : 10) * mag;
}
function linTicks(lo, hi, n = 5) {
  const st = niceStep(hi - lo, n);
  const out = [];
  for (let v = Math.ceil(lo / st - 1e-9) * st; v <= hi + st * 1e-6 && out.length < 14; v += st) out.push(+v.toPrecision(12));
  return out;
}
function logTicks(lo, hi) {
  const out = [];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) {
    const v = Math.pow(10, e);
    if (v >= lo * 0.999 && v <= hi * 1.001) out.push(v);
  }
  return out;
}
/** Min/max bucket decimation so long traces stay light (≤ ~2·cap points). */
function decimate(xs, ys, cap = 1600) {
  const n = xs.length;
  if (n <= cap * 2) return xs.map((x, i) => [x, ys[i]]);
  const out = [];
  const b = n / cap;
  for (let k = 0; k < cap; k++) {
    const i0 = Math.floor(k * b), i1 = Math.min(n, Math.floor((k + 1) * b));
    let imin = i0, imax = i0;
    for (let i = i0; i < i1; i++) {
      if (ys[i] < ys[imin]) imin = i;
      if (ys[i] > ys[imax]) imax = i;
    }
    const [a, c] = imin < imax ? [imin, imax] : [imax, imin];
    out.push([xs[a], ys[a]]);
    if (c !== a) out.push([xs[c], ys[c]]);
  }
  return out;
}

let tipEl = null;
function tip() {
  if (!tipEl) { tipEl = h("div", "chart-tooltip hidden"); document.body.appendChild(tipEl); }
  return tipEl;
}
function showTip(html, cx, cy) {
  const t = tip();
  t.innerHTML = html;
  t.classList.remove("hidden");
  const r = t.getBoundingClientRect();
  let x = cx + 14, y = cy - r.height / 2;
  if (x + r.width > window.innerWidth - 8) x = cx - r.width - 14;
  y = Math.max(8, Math.min(y, window.innerHeight - r.height - 8));
  t.style.left = x + "px"; t.style.top = y + "px";
}
const hideTip = () => tip().classList.add("hidden");

/**
 * Generic x–y line / stacked-area chart. Values are DISPLAY units.
 * o: {title, unit, xLabel, yLabel, series:[{label, y, x?, area?}], x (shared),
 *     logX, logY, W, H, markers:[{x, label}], zeroLine, yFmt, xFmt, cls}
 * Series with area:true are stacked (cumulative) in order.
 */
export function xyChart(o) {
  const W = o.W || 640, H = o.H || 240;
  const M = { l: 62, r: 14, t: 12, b: 34 };
  const pw = W - M.l - M.r, ph = H - M.t - M.b;
  const xs0 = o.x || [];
  const series = (o.series || []).map((s, i) => ({ ...s, i, x: s.x || xs0 }));
  // stack areas
  let acc = null;
  for (const s of series) {
    if (!s.area) continue;
    const base = acc ? acc.slice() : s.y.map(() => 0);
    s.base = base;
    s.top = s.y.map((v, k) => (isFinite(v) ? v : 0) + base[k]);
    acc = s.top;
  }
  const okX = v => isFinite(v) && (!o.logX || v > 0);
  const okY = v => isFinite(v) && (!o.logY || v > 0);
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const s of series) {
    const ys = s.top || s.y;
    for (let k = 0; k < s.x.length; k++) {
      if (!okX(s.x[k]) || !okY(ys[k])) continue;
      x0 = Math.min(x0, s.x[k]); x1 = Math.max(x1, s.x[k]);
      y0 = Math.min(y0, ys[k]); y1 = Math.max(y1, ys[k]);
    }
  }
  const card = h("div", "chart-card fp-card" + (o.cls ? " " + o.cls : ""));
  card.appendChild(h("div", "chart-title", `${esc(o.title || "")} <span class="unit">${esc(o.unit || "")}</span>`));
  if (!isFinite(x0) || !isFinite(y0)) {
    card.appendChild(h("div", "muted fp-nodata", o.logY ? "No positive values to plot on a log axis" : "No data"));
    return card;
  }
  if (!o.logY && o.zeroLine !== false) { y0 = Math.min(y0, 0); y1 = Math.max(y1, 0); }
  if (o.logY) { if (y1 / y0 < 10) { y0 /= 3; y1 *= 3; } }
  else if (y1 - y0 < 1e-300) { y1 += 1; y0 -= 1; }
  else { const pad = (y1 - y0) * 0.06; y1 += pad; if (y0 < 0) y0 -= pad; }
  if (x1 - x0 < 1e-300) { x1 = x0 + 1; }
  const L = Math.log10;
  const xOf = v => M.l + (o.logX ? (L(v) - L(x0)) / (L(x1) - L(x0)) : (v - x0) / (x1 - x0)) * pw;
  const yOf = v => M.t + (o.logY ? (L(y1) - L(v)) / (L(y1) - L(y0)) : (y1 - v) / (y1 - y0)) * ph;
  const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", class: "fp-svg", "aria-label": o.title || "chart" });
  const yt = o.logY ? logTicks(y0, y1) : linTicks(y0, y1, 5);
  for (const v of yt) {
    svg.appendChild(S("line", { x1: M.l, x2: M.l + pw, y1: yOf(v), y2: yOf(v), class: v === 0 ? "fp-axis" : "fp-grid" }));
    svg.appendChild(S("text", { x: M.l - 6, y: yOf(v) + 3, class: "fp-tick", "text-anchor": "end" }, (o.yFmt || num)(v)));
  }
  const xt = o.logX ? logTicks(x0, x1) : linTicks(x0, x1, 7);
  for (const v of xt) {
    svg.appendChild(S("line", { x1: xOf(v), x2: xOf(v), y1: M.t, y2: M.t + ph, class: "fp-grid fp-vgrid" }));
    svg.appendChild(S("text", { x: xOf(v), y: M.t + ph + 14, class: "fp-tick", "text-anchor": "middle" }, (o.xFmt || num)(v)));
  }
  svg.appendChild(S("line", { x1: M.l, x2: M.l, y1: M.t, y2: M.t + ph, class: "fp-axis" }));
  svg.appendChild(S("line", { x1: M.l, x2: M.l + pw, y1: M.t + ph, y2: M.t + ph, class: "fp-axis" }));
  svg.appendChild(S("text", { x: M.l + pw, y: H - 4, class: "fp-axis-lbl", "text-anchor": "end" }, o.xLabel || ""));
  if (o.yLabel) svg.appendChild(S("text", { x: 12, y: M.t + ph / 2, class: "fp-axis-lbl", "text-anchor": "middle",
    transform: `rotate(-90 12 ${M.t + ph / 2})` }, o.yLabel));
  const clipId = "fpclip" + Math.random().toString(36).slice(2, 8);
  const defs = S("defs");
  const cp = S("clipPath", { id: clipId });
  cp.appendChild(S("rect", { x: M.l, y: M.t, width: pw, height: ph }));
  defs.appendChild(cp); svg.appendChild(defs);
  const g = S("g", { "clip-path": `url(#${clipId})` });
  svg.appendChild(g);
  // areas first (bottom → top), then lines
  for (const s of series.filter(s => s.area)) {
    const pts = [];
    for (let k = 0; k < s.x.length; k++) if (okX(s.x[k])) pts.push(k);
    if (pts.length < 2) continue;
    const step = Math.max(1, Math.floor(pts.length / 1600));
    const idx = pts.filter((_, j) => j % step === 0 || j === pts.length - 1);
    let d = idx.map((k, j) => `${j ? "L" : "M"}${xOf(s.x[k]).toFixed(1)},${yOf(s.top[k]).toFixed(1)}`).join("");
    for (let j = idx.length - 1; j >= 0; j--) d += `L${xOf(s.x[idx[j]]).toFixed(1)},${yOf(s.base[idx[j]]).toFixed(1)}`;
    g.appendChild(S("path", { d: d + "Z", class: `fp-area fp-s${s.i % 8}` }));
  }
  for (const s of series) {
    if (s.area) continue;
    const xs = [], ys = [];
    for (let k = 0; k < s.x.length; k++) if (okX(s.x[k]) && okY(s.y[k])) { xs.push(s.x[k]); ys.push(s.y[k]); }
    if (!xs.length) continue;
    const pts = decimate(xs, ys);
    const d = pts.map(([x, y], j) => `${j ? "L" : "M"}${xOf(x).toFixed(1)},${yOf(y).toFixed(1)}`).join("");
    g.appendChild(S("path", { d, class: `fp-line fp-s${s.i % 8}${s.dash ? " fp-dash" : ""}` }));
  }
  for (const mk of o.markers || []) {
    if (!(mk.x >= x0 && mk.x <= x1)) continue;
    g.appendChild(S("line", { x1: xOf(mk.x), x2: xOf(mk.x), y1: M.t, y2: M.t + ph, class: "fp-marker" }));
    if (mk.label) svg.appendChild(S("text", { x: xOf(mk.x) + 3, y: M.t + ph - 4, class: "fp-marker-lbl" }, mk.label));
  }
  if (o.points) for (const p of o.points) {
    if (!okX(p.x) || !okY(p.y)) continue;
    svg.appendChild(S("circle", { cx: xOf(p.x), cy: yOf(p.y), r: 3.4, class: `fp-pt fp-s${(p.s || 0) % 8}` }));
    if (p.label) svg.appendChild(S("text", { x: Math.min(xOf(p.x) + 6, M.l + pw - 4), y: Math.max(yOf(p.y) - 6, 10),
      class: "fp-pt-lbl", "text-anchor": xOf(p.x) > M.l + pw - 80 ? "end" : "start" }, p.label));
  }
  // crosshair + tooltip
  const cross = S("line", { x1: 0, x2: 0, y1: M.t, y2: M.t + ph, class: "fp-cross", visibility: "hidden" });
  svg.appendChild(cross);
  const hot = S("rect", { x: M.l, y: M.t, width: pw, height: ph, fill: "transparent" });
  const ref = series[0];
  hot.addEventListener("mousemove", e => {
    if (!ref) return;
    const rect = svg.getBoundingClientRect();
    const sx = (e.clientX - rect.left) * (W / rect.width);
    const f = (sx - M.l) / pw;
    const xv = o.logX ? Math.pow(10, L(x0) + f * (L(x1) - L(x0))) : x0 + f * (x1 - x0);
    let lo = 0, hi = ref.x.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (ref.x[mid] < xv) lo = mid; else hi = mid; }
    const k = Math.abs(ref.x[hi] - xv) < Math.abs(ref.x[lo] - xv) ? hi : lo;
    if (!okX(ref.x[k])) return;
    cross.setAttribute("x1", xOf(ref.x[k])); cross.setAttribute("x2", xOf(ref.x[k]));
    cross.setAttribute("visibility", "visible");
    showTip(`<b>${esc(o.xName || "x")} = ${num(ref.x[k], 5)} ${esc(o.xUnit || "")}</b><br>` +
      series.map(s => `<span class="fp-sw fp-s${s.i % 8}">●</span> ${esc(s.label)} ${(o.yFmt || num)(s.y[k])} ${esc(o.unit || "")}`).join("<br>"),
      e.clientX, e.clientY);
  });
  hot.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
  svg.appendChild(hot);
  card.appendChild(svg);
  if (series.length > 1 || o.legend) {
    const lg = h("div", "fp-legend");
    lg.innerHTML = series.map(s =>
      `<span class="key"><i class="fp-key fp-s${s.i % 8}${s.area ? " is-area" : ""}"></i>${esc(s.label)}</span>`).join("");
    card.appendChild(lg);
  }
  return card;
}

/** Horizontal bar chart (story distribution), items top → bottom. */
export function barChart(items, o = {}) {
  const W = o.W || 340, rowH = 26;
  const M = { l: 70, r: 50, t: 8, b: 22 };
  const H = M.t + M.b + Math.max(1, items.length) * rowH;
  const pw = W - M.l - M.r;
  const vmax = Math.max(...items.map(i => Math.abs(i.value)), 1e-12);
  const card = h("div", "chart-card fp-card");
  card.appendChild(h("div", "chart-title", `${esc(o.title || "")} <span class="unit">${esc(o.unit || "")}</span>`));
  const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, class: "fp-svg", role: "img", "aria-label": o.title || "bar chart" });
  for (const v of linTicks(0, vmax, 4)) {
    const x = M.l + v / vmax * pw;
    if (x > M.l + pw + 0.5) continue;
    svg.appendChild(S("line", { x1: x, x2: x, y1: M.t, y2: H - M.b, class: v === 0 ? "fp-axis" : "fp-grid" }));
    svg.appendChild(S("text", { x, y: H - 8, class: "fp-tick", "text-anchor": "middle" }, num(v, 3)));
  }
  items.forEach((it, i) => {
    const y = M.t + i * rowH;
    const w = Math.abs(it.value) / vmax * pw;
    svg.appendChild(S("text", { x: M.l - 6, y: y + rowH / 2 + 3, class: "fp-tick fp-bar-lbl", "text-anchor": "end" }, it.label));
    svg.appendChild(S("rect", { x: M.l, y: y + 4, width: Math.max(w, 0.5), height: rowH - 8, rx: 2, class: "fp-bar" }));
    svg.appendChild(S("text", { x: M.l + w + 5, y: y + rowH / 2 + 3, class: "fp-tick fp-bar-val" }, (o.fmt || num)(it.value)));
  });
  card.appendChild(svg);
  return card;
}

/* ---------------------------------------------------------------- small table helper */
function table(cols, rows, cls = "") {
  const t = h("table", "data-table fp-table " + cls);
  t.innerHTML = `<thead><tr>${cols.map(c => `<th class="${c.txt ? "txt" : ""}">${esc(c.label)}${c.unit ? ` <span class="fp-th-unit">${esc(c.unit)}</span>` : ""}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows.map(r => `<tr>${r.map((v, i) => `<td class="${cols[i].txt ? "txt" : ""}${v === "—" ? " dim" : ""}">${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  const w = h("div", "table-scroll fp-tablewrap");
  w.appendChild(t);
  return w;
}
const seg = (opts, cur, onPick, title) => {
  const d = h("div", "seg-toggle");
  if (title) d.title = title;
  for (const [v, l] of opts) {
    const b = h("button", "seg-btn" + (v === cur ? " is-active" : ""), esc(l));
    b.dataset.v = v;
    b.addEventListener("click", () => onPick(v));
    d.appendChild(b);
  }
  return d;
};
const select = (opts, cur, onPick, cls = "case-select") => {
  const s = document.createElement("select");
  s.className = cls;
  for (const [v, l] of opts) {
    const o = document.createElement("option");
    o.value = v; o.textContent = l;
    s.appendChild(o);
  }
  if (cur != null) s.value = cur;
  s.addEventListener("change", () => onPick(s.value));
  return s;
};
const labeled = (txt, ctl) => { const l = h("label", "case-select-wrap"); l.append(h("span", "", esc(txt)), ctl); return l; };

/* ================================================================
   init — wires the four displays into the existing results tabs
   ================================================================ */
export function initResultDisplays(sky) {
  const store = sky.store;
  const $ = id => document.getElementById(id);
  const st = {
    ss: { case: null, src: "joint", item: null, comp: 0, phase: false, logY: false },
    psd: { case: null, src: "joint", item: null, comp: 0, logX: true, logY: true },
    energy: { stacked: true },
    lp: { results: null, data: null, error: null, busy: false },
  };
  const statusOf = n => ((store.results && store.results.case_status) || {})[n];
  const idleChips = names => names.map(n => [n, statusOf(n)]).filter(([, s]) => s && s !== "finished")
    .map(([n, s]) => statusChip(n, s)).join("");

  /* ---------------- Frequency tab (button + pane, created once) */
  const tabbar = document.querySelector("#analyzeMain .tabbar");
  const tabBtn = h("button", "tab hidden", "Frequency");
  tabBtn.dataset.tab = "freq"; tabBtn.id = "freqTabBtn"; tabBtn.setAttribute("role", "tab");
  tabBtn.title = "Frequency-domain results — steady-state response and power spectral density";
  if (tabbar) tabbar.insertBefore(tabBtn, tabbar.querySelector(".tabbar-spacer"));
  tabBtn.addEventListener("click", () => { sky.switchTab("freq"); renderFreq(); });
  const pane = h("section", "tabpane");
  pane.id = "pane-freq"; pane.setAttribute("role", "tabpanel");
  pane.innerHTML = `<div class="empty-state" id="empty-freq"><div class="empty-art">≈</div>
      <p>Run analysis with a steady-state or PSD case to see frequency-domain results</p>
      <div class="fp-idle" id="freqIdleEmpty"></div></div>
    <div class="pane-content hidden" id="content-freq">
      <div class="fp-idle" id="freqIdle"></div>
      <section class="fp-sec" id="ssSec"></section>
      <section class="fp-sec" id="psdSec"></section>
    </div>`;
  const main = $("analyzeMain");
  if (main) main.appendChild(pane);

  function freqNames(kind) {
    const m = store.model || {};
    return Object.keys(m[kind === "ss" ? "steady_state_cases" : "psd_cases"] || {});
  }
  function syncFreqTab() {
    const r = store.results;
    const ss = (r && r.steady_state) || {}, ps = (r && r.psd) || {};
    const defined = freqNames("ss").length + freqNames("psd").length;
    const has = !!(Object.keys(ss).length || Object.keys(ps).length);
    tabBtn.classList.toggle("hidden", !(r && (has || defined)));
    $("empty-freq").classList.toggle("hidden", has);
    $("content-freq").classList.toggle("hidden", !has);
    const idle = idleChips([...freqNames("ss"), ...freqNames("psd")]);
    const msg = idle ? `<span class="muted">Not available:</span> ${idle}` : "";
    $("freqIdle").innerHTML = msg; $("freqIdleEmpty").innerHTML = msg;
    if (!r && store.tab === "freq") sky.switchTab("view3d");
  }

  function renderFreq() {
    syncFreqTab();
    if (!store.results) return;
    renderSS();
    renderPSD();
  }

  /* -------- steady state */
  function respOptions(blk, kind) {
    // joint list: node_disp keys (output points or every joint)
    const joints = Object.keys((kind === "ss" ? blk.node_disp : (blk.psd || {}).node_disp) || {});
    const stories = Object.keys((kind === "ss" ? blk.story : (blk.psd || {}).story) || {});
    return { joints, stories };
  }
  function renderSS() {
    const sec = $("ssSec");
    const all = (store.results && store.results.steady_state) || {};
    const names = Object.keys(all);
    sec.innerHTML = "";
    sec.classList.toggle("hidden", !names.length && !freqNames("ss").length);
    const head = h("div", "fp-sec-head", `<b>Steady State</b><span class="muted">frequency response |Z(f)| and phase — ETABS Steady State load case</span>`);
    sec.appendChild(head);
    if (!names.length) {
      sec.appendChild(h("div", "muted fp-note", `No steady-state results. ${idleChips(freqNames("ss")) || ""}`));
      return;
    }
    const c = st.ss;
    if (!names.includes(c.case)) c.case = names[0];
    const blk = all[c.case];
    const { joints, stories } = respOptions(blk, "ss");
    if (c.src === "joint" && !joints.length) c.src = "base";
    if (c.src === "joint" && !joints.includes(c.item)) c.item = joints[0];
    if (c.src === "story" && !stories.includes(c.item)) c.item = stories[stories.length - 1];
    if (c.src === "base" && !["FX", "FY", "FZ", "MX", "MY", "MZ"].includes(c.item)) {
      const b = (blk.peaks || {}).base || {};
      c.item = ["FX", "FY", "FZ", "MX", "MY", "MZ"].reduce((a, k) => Math.abs(b[k] || 0) > Math.abs(b[a] || 0) ? k : a, "FX");
    }
    const ctl = h("div", "charts-controls fp-controls");
    const left = h("div", "th-controls");
    left.appendChild(labeled("Case", select(names.map(n => [n, n]), c.case, v => { c.case = v; c.item = null; renderSS(); })));
    left.appendChild(labeled("Response", select([...(joints.length ? [["joint", "Joint"]] : []), ["base", "Base reaction"],
      ...(stories.length ? [["story", "Story"]] : [])], c.src, v => { c.src = v; c.item = null; c.comp = v === "story" ? "ux" : 0; renderSS(); })));
    if (c.src === "joint") {
      left.appendChild(labeled("Joint", select(joints.map(j => [j, j]), c.item, v => { c.item = v; renderSS(); })));
      if (typeof c.comp !== "number") c.comp = 0;
      if (c.autoComp !== c.item) {             // first view of a joint: pick its dominant DOF
        const pk = ((blk.peaks || {}).node_disp || {})[c.item] || [];
        c.comp = pk.length ? pk.slice(0, 3).reduce((a, v, i) => Math.abs(v) > Math.abs(pk[a]) ? i : a, 0) : 0;
        c.autoComp = c.item;
      }
      left.appendChild(labeled("DOF", select(DOFS.map((d, i) => [String(i), d]), String(c.comp), v => { c.comp = +v; renderSS(); })));
    } else if (c.src === "base") {
      left.appendChild(labeled("Component", select(["FX", "FY", "FZ", "MX", "MY", "MZ"].map(k => [k, k]), c.item, v => { c.item = v; renderSS(); })));
    } else {
      left.appendChild(labeled("Story", select([...stories].reverse().map(s => [s, s]), c.item, v => { c.item = v; renderSS(); })));
      if (!["ux", "uy", "drift_x", "drift_y"].includes(c.comp)) c.comp = "ux";
      left.appendChild(labeled("Quantity", select([["ux", "UX"], ["uy", "UY"], ["drift_x", "Drift X"], ["drift_y", "Drift Y"]], c.comp, v => { c.comp = v; renderSS(); })));
    }
    const right = h("div", "th-controls");
    right.appendChild(seg([["amp", "Amplitude"], ["phase", "Phase"]], c.phase ? "phase" : "amp", v => { c.phase = v === "phase"; renderSS(); }, "Plot the amplitude |Z| or the phase angle"));
    const lg = h("label", "fp-chk", `<input type="checkbox"${c.logY ? " checked" : ""}${c.phase ? " disabled" : ""}> log amplitude`);
    lg.querySelector("input").addEventListener("change", e => { c.logY = e.target.checked; renderSS(); });
    right.appendChild(lg);
    const csv = h("button", "chip csv-btn", "⬇ CSV");
    csv.title = "Download this response (all frequencies, display units) as CSV";
    right.appendChild(csv);
    ctl.append(left, right);
    sec.appendChild(ctl);

    // series
    let curve, kind, label;
    if (c.src === "joint") { curve = blk.node_disp[c.item]; kind = dofKind(c.comp); label = `Joint ${c.item} ${DOFS[c.comp]}`; }
    else if (c.src === "base") { curve = blk.base[c.item]; kind = baseKind(c.item); label = `Base ${c.item}`; }
    else { curve = (blk.story[c.item] || {})[c.comp]; kind = storyKind(c.comp); label = `${c.item} ${c.comp.replace("_", " ").toUpperCase()}`; }
    const f = blk.frequencies_hz || [];
    const pick = arr => (arr || []).map(v => c.src === "joint" ? (v || [])[c.comp] : v);
    const amp = pick(curve && curve.amp).map(v => kind === "none" ? v : U.toDisplay(kind, v));
    const ph = pick(curve && curve.phase_deg);
    const unit = c.phase ? "°" : (kind === "none" ? "" : U.label(kind));
    const ip = amp.reduce((a, v, i) => Math.abs(v) > Math.abs(amp[a] || 0) ? i : a, 0);
    const chart = xyChart({
      title: `${c.phase ? "Phase" : "Amplitude"} — ${label} · ${c.case}`, unit,
      x: f, xLabel: "f  Hz", xName: "f", xUnit: "Hz",
      series: [{ label: c.phase ? "phase" : "|Z|", y: c.phase ? ph : amp }],
      logY: !c.phase && c.logY, zeroLine: true, W: 720, H: 260,
      markers: (blk.modal_frequencies_hz || []).filter(v => v >= f[0] && v <= f[f.length - 1]).slice(0, 12)
        .map((v, i) => ({ x: v, label: i < 6 ? `f${i + 1}` : "" })),
      points: !c.phase && amp.length ? [{ x: f[ip], y: amp[ip], label: `peak ${num(amp[ip])} @ ${num(f[ip])} Hz` }] : [],
    });
    chart.id = "ssChart";
    const grid = h("div", "th-charts fp-charts");
    grid.appendChild(chart);
    sec.appendChild(grid);
    csv.addEventListener("click", () => {
      const lines = [`f [Hz],${label} amplitude${unit && !c.phase ? ` [${U.label(kind)}]` : ""},${label} phase [deg]`];
      f.forEach((fv, i) => lines.push(`${fv},${amp[i] ?? ""},${ph[i] ?? ""}`));
      downloadText(lines.join("\n") + "\n", `steady_state_${c.case}_${U.getUnits()}.csv`.replace(/[^\w.-]+/g, "_"));
    });
    sec.appendChild(h("div", "muted fp-meta",
      `${esc(blk.method || "modal")} · ${esc((blk.damping || {}).type || "modal")} damping ${num(100 * ((blk.damping || {}).ratio || 0), 3)} % · ` +
      `${blk.modes_used ?? "—"} modes · ${f.length} frequencies (${num(f[0])}–${num(f[f.length - 1])} Hz)`));
    (blk.warnings || []).forEach(w => sec.appendChild(h("div", "po-warn-item", "⚠ " + esc(w))));

    // peak tables
    const pk = blk.peaks || {};
    const tabs = h("div", "fp-peaks");
    const bk = ["FX", "FY", "FZ", "MX", "MY", "MZ"];
    tabs.appendChild(h("div", "fp-sub", "Peak base reactions"));
    tabs.appendChild(table([{ label: "Component", txt: true }, { label: "Peak", unit: "" }, { label: "Unit", txt: true }, { label: "f at peak", unit: "Hz" }],
      bk.map(k => [k, fmtK(baseKind(k), (pk.base || {})[k], 2), U.label(baseKind(k)), num((pk.base_freq_hz || {})[k])]), "fp-peak-base"));
    const sts = Object.keys(pk.story || {}).reverse();
    if (sts.length) {
      tabs.appendChild(h("div", "fp-sub", "Peak story response"));
      tabs.appendChild(table([{ label: "Story", txt: true }, { label: "UX", unit: U.label("disp") }, { label: "UY", unit: U.label("disp") },
        { label: "Drift X" }, { label: "Drift Y" }],
        sts.map(s => { const p = pk.story[s]; return [s, fmtK("disp", p.ux, 3), fmtK("disp", p.uy, 3), num(p.drift_x), num(p.drift_y)]; }), "fp-peak-story"));
    }
    const nd = pk.node_disp || {};
    const top = Object.entries(nd).map(([t, a]) => [t, Math.hypot(a[0] || 0, a[1] || 0, a[2] || 0), a])
      .sort((a, b) => b[1] - a[1]).slice(0, 10);
    if (top.length) {
      tabs.appendChild(h("div", "fp-sub", `Peak joint displacements — top ${top.length} of ${Object.keys(nd).length} joints`));
      tabs.appendChild(table([{ label: "Joint", txt: true }, ...DOFS.map((d, i) => ({ label: d, unit: U.label(dofKind(i)) })), { label: "f (UX)", unit: "Hz" }],
        top.map(([t, , a]) => [t, ...a.map((v, i) => fmtK(dofKind(i), v, i < 3 ? 3 : 6)), num(((pk.node_disp_freq_hz || {})[t] || [])[0])]), "fp-peak-joint"));
    }
    sec.appendChild(tabs);
  }

  /* -------- PSD */
  function renderPSD() {
    const sec = $("psdSec");
    const all = (store.results && store.results.psd) || {};
    const names = Object.keys(all);
    sec.innerHTML = "";
    sec.classList.toggle("hidden", !names.length && !freqNames("psd").length);
    sec.appendChild(h("div", "fp-sec-head", `<b>Power Spectral Density</b><span class="muted">RMS responses and response PSD curves — fully correlated loads</span>`));
    if (!names.length) {
      sec.appendChild(h("div", "muted fp-note", `No PSD results. ${idleChips(freqNames("psd")) || ""}`));
      return;
    }
    const c = st.psd;
    if (!names.includes(c.case)) c.case = names[0];
    const blk = all[c.case];
    const curves = blk.psd || {};
    const joints = Object.keys(curves.node_disp || {}), stories = Object.keys(curves.story || {});
    if (c.src === "joint" && !joints.length) c.src = "base";
    if (c.src === "joint" && !joints.includes(c.item)) c.item = joints[0];
    if (c.src === "story" && !stories.includes(c.item)) c.item = stories[stories.length - 1];
    if (c.src === "base" && !["FX", "FY", "FZ", "MX", "MY", "MZ"].includes(c.item)) {
      const b = (blk.rms || {}).base || {};
      c.item = ["FX", "FY", "FZ", "MX", "MY", "MZ"].reduce((a, k) => Math.abs(b[k] || 0) > Math.abs(b[a] || 0) ? k : a, "FX");
    }
    const ctl = h("div", "charts-controls fp-controls");
    const left = h("div", "th-controls");
    left.appendChild(labeled("Case", select(names.map(n => [n, n]), c.case, v => { c.case = v; c.item = null; renderPSD(); })));
    left.appendChild(labeled("Curve", select([...(joints.length ? [["joint", "Joint"]] : []), ["base", "Base reaction"],
      ...(stories.length ? [["story", "Story"]] : [])], c.src, v => { c.src = v; c.item = null; c.comp = v === "story" ? "ux" : 0; renderPSD(); })));
    if (c.src === "joint") {
      if (typeof c.comp !== "number") c.comp = 0;
      if (c.autoComp !== c.item) {
        const rm = ((blk.rms || {}).node_disp || {})[c.item] || [];
        c.comp = rm.length ? rm.slice(0, 3).reduce((a, v, i) => Math.abs(v) > Math.abs(rm[a]) ? i : a, 0) : 0;
        c.autoComp = c.item;
      }
      left.appendChild(labeled("Joint", select(joints.map(j => [j, j]), c.item, v => { c.item = v; renderPSD(); })));
      left.appendChild(labeled("DOF", select(DOFS.map((d, i) => [String(i), d]), String(c.comp), v => { c.comp = +v; renderPSD(); })));
    } else if (c.src === "base") {
      left.appendChild(labeled("Component", select(["FX", "FY", "FZ", "MX", "MY", "MZ"].map(k => [k, k]), c.item, v => { c.item = v; renderPSD(); })));
    } else {
      if (!["ux", "uy", "drift_x", "drift_y"].includes(c.comp)) c.comp = "ux";
      left.appendChild(labeled("Story", select([...stories].reverse().map(s => [s, s]), c.item, v => { c.item = v; renderPSD(); })));
      left.appendChild(labeled("Quantity", select([["ux", "UX"], ["uy", "UY"], ["drift_x", "Drift X"], ["drift_y", "Drift Y"]], c.comp, v => { c.comp = v; renderPSD(); })));
    }
    const right = h("div", "th-controls");
    right.appendChild(seg([["loglog", "Log-log"], ["linlin", "Linear"]], c.logY ? "loglog" : "linlin",
      v => { c.logX = c.logY = v === "loglog"; renderPSD(); }, "Axis scaling of the response PSD plot"));
    ctl.append(left, right);
    sec.appendChild(ctl);

    let raw, kind, label;
    if (c.src === "joint") { raw = (curves.node_disp[c.item] || []).map(a => (a || [])[c.comp]); kind = dofKind(c.comp); label = `Joint ${c.item} ${DOFS[c.comp]}`; }
    else if (c.src === "base") { raw = (curves.base || {})[c.item] || []; kind = baseKind(c.item); label = `Base ${c.item}`; }
    else { raw = ((curves.story || {})[c.item] || {})[c.comp] || []; kind = storyKind(c.comp); label = `${c.item} ${c.comp.replace("_", " ").toUpperCase()}`; }
    const f = blk.frequencies_hz || [];
    const y = raw.map(v => psdDisp(kind, v));
    const rms = Math.sqrt(raw.reduce((a, v, i) => i ? a + 0.5 * ((v || 0) + (raw[i - 1] || 0)) * (f[i] - f[i - 1]) : 0, 0));
    const chart = xyChart({
      title: `Response PSD — ${label} · ${c.case}`, unit: psdLabel(kind), x: f,
      xLabel: "f  Hz", xName: "f", xUnit: "Hz", series: [{ label: "S(f)", y }],
      logX: c.logX, logY: c.logY, W: 720, H: 260, zeroLine: !c.logY,
      markers: (blk.modal_frequencies_hz || []).filter(v => v > 0 && v >= f[0] && v <= f[f.length - 1]).slice(0, 12)
        .map((v, i) => ({ x: v, label: i < 6 ? `f${i + 1}` : "" })),
    });
    chart.id = "psdChart";
    const grid = h("div", "th-charts fp-charts");
    grid.appendChild(chart);
    sec.appendChild(grid);
    sec.appendChild(h("div", "muted fp-meta",
      `RMS of this curve ${esc(fmtK(kind, rms, kind === "rotation" ? 6 : 3))} ${esc(kind === "none" ? "" : U.label(kind))} · ` +
      `${esc(blk.method || "modal")} · ${esc((blk.damping || {}).type || "modal")} damping ${num(100 * ((blk.damping || {}).ratio || 0), 3)} % · ` +
      `correlation ${esc(blk.correlation || "full")} · ${f.length} frequencies`));
    (blk.warnings || []).forEach(w => sec.appendChild(h("div", "po-warn-item", "⚠ " + esc(w))));

    // RMS tables
    const R = blk.rms || {};
    const wrap = h("div", "fp-peaks");
    wrap.appendChild(h("div", "fp-sub", "RMS base reactions"));
    wrap.appendChild(table(["FX", "FY", "FZ", "MX", "MY", "MZ"].map(k => ({ label: k, unit: U.label(baseKind(k)) })),
      [["FX", "FY", "FZ", "MX", "MY", "MZ"].map(k => fmtK(baseKind(k), (R.base || {})[k], 2))], "fp-rms-base"));
    const sts = Object.keys(R.story || {}).reverse();
    if (sts.length) {
      wrap.appendChild(h("div", "fp-sub", "RMS story response"));
      wrap.appendChild(table([{ label: "Story", txt: true }, { label: "UX", unit: U.label("disp") }, { label: "UY", unit: U.label("disp") },
        { label: "Drift X" }, { label: "Drift Y" }],
        sts.map(s => { const p = R.story[s]; return [s, fmtK("disp", p.ux, 3), fmtK("disp", p.uy, 3), num(p.drift_x), num(p.drift_y)]; }), "fp-rms-story"));
    }
    const jt = Object.entries(R.node_disp || {});
    if (jt.length) {
      wrap.appendChild(h("div", "fp-sub", `RMS joint displacements — ${jt.length} joints`));
      const host = h("div", "fp-vg-host");
      wrap.appendChild(host);
      sec.appendChild(wrap);
      const g = new VirtualGrid(host, { rowH: 24 });
      g.setData([{ key: "joint", label: "Joint", quantity: "id" },
        ...["ux", "uy", "uz"].map(k => ({ key: k, label: k.toUpperCase(), quantity: "length" })),
        ...["rx", "ry", "rz"].map(k => ({ key: k, label: k.toUpperCase(), quantity: "angle" }))],
      jt.map(([t, a]) => ({ joint: +t, ux: a[0], uy: a[1], uz: a[2], rx: a[3], ry: a[4], rz: a[5] })));
      g.sortBy("ux", -1);
      st.psd.grid = g;
      return;
    }
    sec.appendChild(wrap);
  }

  /* ---------------- Energy card (Time History tab) */
  const thContent = $("content-th");
  const energyCard = h("div", "fp-card-block");
  energyCard.id = "energyCard";
  if (thContent) thContent.appendChild(energyCard);
  function renderEnergy() {
    if (!thContent) return;
    const r = store.results;
    const ths = (r && r.th_cases) || {};
    const name = store.thCase && ths[store.thCase] ? store.thCase : Object.keys(ths)[0];
    energyCard.innerHTML = "";
    if (!r || !name) { energyCard.classList.add("hidden"); return; }
    energyCard.classList.remove("hidden");
    const idle = idleChips(Object.keys((store.model || {}).th_cases || {}));
    const head = h("div", "perf-head fp-head", `<b>Energy</b><span class="muted">cumulative energy balance (direct integration) — ${esc(name)}</span>`);
    energyCard.appendChild(head);
    const e = ths[name].energy;
    if (!e || !(e.t || []).length) {
      energyCard.appendChild(h("div", "muted fp-note",
        `Energy was not recorded for <b>${esc(name)}</b> — enable <i>energy</i> in the time-history case (direct-integration options) and re-run.` +
        (idle ? `<br>${idle}` : "")));
      return;
    }
    const ctl = h("div", "charts-controls fp-controls");
    const left = h("div", "th-controls");
    left.appendChild(seg([["stacked", "Stacked"], ["lines", "Lines"]], st.energy.stacked ? "stacked" : "lines",
      v => { st.energy.stacked = v === "stacked"; renderEnergy(); }, "Stacked dissipation/storage components with the input energy on top, or plain lines"));
    if (idle) left.appendChild(h("span", "", idle));
    const csv = h("button", "chip csv-btn", "⬇ CSV");
    csv.title = "Download the energy time series (display units)";
    ctl.append(left, csv);
    energyCard.appendChild(ctl);
    const kind = "moment";                         // energy = force × length
    const unit = U.label(kind);
    const cv = a => (a || []).map(v => U.toDisplay(kind, v));
    const comps = [["kinetic", "Kinetic"], ["strain", "Strain"], ["damping", "Damping"], ["hysteretic", "Hysteretic"]];
    const series = comps.map(([k, l]) => ({ label: l, y: cv(e[k]), area: st.energy.stacked }));
    series.push({ label: "Input", y: cv(e.input), dash: st.energy.stacked });
    const grid = h("div", "th-charts fp-charts");
    const ch = xyChart({ title: `Energy — ${name}`, unit, x: e.t, xLabel: "t  s", xName: "t", xUnit: "s",
      series, W: 720, H: 260, legend: true });
    ch.id = "energyChart";
    grid.appendChild(ch);
    const errN = (e.error_normalized || []).map(v => 100 * v);
    const ch2 = xyChart({ title: "Energy error (input − Σ components) / max energy", unit: "%", x: e.t,
      xLabel: "t  s", xName: "t", xUnit: "s", series: [{ label: "error", y: errN }], W: 720, H: 180 });
    ch2.id = "energyErrChart";
    grid.appendChild(ch2);
    energyCard.appendChild(grid);
    const last = k => (e[k] || [])[(e[k] || []).length - 1];
    energyCard.appendChild(table([{ label: "Component", txt: true }, { label: "Final", unit }, { label: "Share of input", unit: "%" }],
      [...comps, ["input", "Input"], ["error", "Error"]].map(([k, l]) => [l, U.fmt(kind, last(k), 3),
        Math.abs(last("input")) > 1e-12 ? num(100 * last(k) / last("input"), 4) : "—"]), "fp-energy-table"));
    energyCard.appendChild(h("div", "muted fp-meta",
      `max |error| ${num(100 * (e.max_abs_error_normalized || 0), 4)} % of the run maximum energy · ` +
      `${esc(e.method || "")} · normalisation: ${esc(e.normalization || "")}`));
    csv.addEventListener("click", () => {
      const keys = ["input", "kinetic", "strain", "damping", "hysteretic", "error"];
      const lines = [["t [s]", ...keys.map(k => `${k} [${unit}]`), "error_normalized"].join(",")];
      e.t.forEach((tv, i) => lines.push([tv, ...keys.map(k => U.toDisplay(kind, (e[k] || [])[i])), (e.error_normalized || [])[i]].join(",")));
      downloadText(lines.join("\n") + "\n", `energy_${name}_${U.getUnits()}.csv`.replace(/[^\w.-]+/g, "_"));
    });
  }

  /* ---------------- Pushover card */
  const poContent = $("content-pushover");
  const poCard = h("div", "fp-card-block");
  poCard.id = "poControlCard";
  if (poContent) {
    const anchor = $("perfCard");
    poContent.insertBefore(poCard, anchor || null);
  }
  function renderPushover() {
    if (!poContent) return;
    const r = store.results;
    const po = (r && r.pushover) || {};
    const name = store.poCase && po[store.poCase] ? store.poCase : Object.keys(po)[0];
    poCard.innerHTML = "";
    if (!r || !name) { poCard.classList.add("hidden"); return; }
    poCard.classList.remove("hidden");
    const pd = po[name];
    const pc = ((store.model || {}).pushover_cases || {})[name] || {};
    const idle = idleChips(Object.keys((store.model || {}).pushover_cases || {}));
    poCard.appendChild(h("div", "perf-head fp-head", `<b>Load distribution &amp; control</b><span class="muted">ETABS Nonlinear Static — ${esc(name)}</span>${idle}`));
    const cc = pd.capacity_curve, dist = pd.distribution;
    if (!cc && !dist) {
      poCard.appendChild(h("div", "muted fp-note", "This backend did not report capacity_curve / distribution for this case."));
      return;
    }
    const grid = h("div", "fp-po-grid");
    if (cc && (cc.disp || []).length) {
      const dk = "disp";
      const lab = `joint ${cc.node ?? "—"} · ${cc.dof || ""}`;
      const x = cc.disp.map(v => U.toDisplay(dk, v)), y = cc.base_shear.map(v => U.toDisplay("force", v));
      const ch = xyChart({ title: `Capacity curve — control ${lab}`, unit: U.label("force"),
        x, xLabel: `control displacement ${cc.dof || ""}  ${U.label(dk)}`, yLabel: `base shear  ${U.label("force")}`,
        xName: "u", xUnit: U.label(dk), series: [{ label: "V", y }], W: 520, H: 260,
        points: x.length ? [{ x: x[x.length - 1], y: y[y.length - 1], label: `${num(y[y.length - 1])} ${U.label("force")}` }] : [] });
      ch.id = "poCapChart";
      grid.appendChild(ch);
    }
    if (dist && dist.story_forces) {
      const order = ((store.model || {}).stories || []).map(s => s.name).reverse();
      const keys = Object.keys(dist.story_forces);
      const items = [...order.filter(s => keys.includes(s)), ...keys.filter(s => !order.includes(s))]
        .map(s => ({ label: s, value: dist.story_forces[s] }));
      const ch = barChart(items, { title: `Applied distribution — ${dist.type}`, unit: "normalised (Σ = 1)", fmt: v => num(v, 3) });
      ch.id = "poDistChart";
      grid.appendChild(ch);
    }
    poCard.appendChild(grid);
    const p = (dist && dist.params) || {};
    const bits = [];
    if (dist) bits.push(`distribution <b>${esc(dist.type)}</b>`);
    if (p.mode_number != null) bits.push(`mode ${esc(p.mode_number)}`);
    if (p.period != null) bits.push(`T = ${esc(num(p.period))} s`);
    if (p.mass_ratio != null) bits.push(`mass ratio ${esc(num(100 * p.mass_ratio, 4))} %`);
    if (p.k != null) bits.push(`k = ${esc(num(p.k))}`);
    if (p.pattern) bits.push(`pattern ${esc(p.pattern)}`);
    if (dist && dist.reference_base_shear != null) bits.push(`reference base shear ${esc(U.fmtU("force", dist.reference_base_shear, 3))}`);
    if (cc) {
      bits.push(`${esc((cc.mode || "").replace("_", " "))}`);
      if (cc.height != null) bits.push(`H<sub>ctrl</sub> ${esc(U.fmtU("length", cc.height, 2))}`);
      bits.push(`start from ${esc(cc.start_from || pc.start_from || (Object.keys(pc.gravity || {}).length ? "gravity stage" : "zero"))}`);
    }
    poCard.appendChild(h("div", "muted fp-meta", bits.join(" · ")));
  }

  /* ---------------- Load participation (Modal tab) */
  const modalContent = $("content-modal");
  const lpCard = h("div", "fp-card-block");
  lpCard.id = "loadPartCard";
  if (modalContent) modalContent.appendChild(lpCard);
  function renderLP() {
    if (!modalContent) return;
    const r = store.results;
    lpCard.innerHTML = "";
    if (!r) return;
    if (st.lp.results !== r) { st.lp = { results: r, data: null, error: null, busy: false }; }
    const modalSt = statusOf("MODAL");
    const hasModes = ((r.modal || {}).periods || []).length > 0;
    const head = h("div", "perf-head fp-head", `<b>Modal Load Participation Ratios</b><span class="muted">static / dynamic % per acceleration and load pattern (Wilson) — computed on demand</span>`);
    const btn = h("button", "btn btn-small", st.lp.busy ? `<span class="spinner"></span>Computing…` : (st.lp.data ? "Recompute" : "Load Participation"));
    btn.id = "loadPartBtn";
    btn.disabled = st.lp.busy || !hasModes;
    btn.title = hasModes ? "POST /api/analyze/load_participation — costs 3 + n_patterns extra linear solves; cached until the next analysis"
      : "No modes in the last run (MODAL not run)";
    head.appendChild(h("span", "toolbar-spacer"));
    head.appendChild(btn);
    lpCard.appendChild(head);
    btn.addEventListener("click", async () => {
      st.lp.busy = true; st.lp.error = null; renderLP();
      const mine = st.lp;
      try { mine.data = await skyApi(sky, "/api/analyze/load_participation"); }
      catch (err) { mine.error = err.message; }
      mine.busy = false;
      if (st.lp === mine) renderLP();
    });
    if (!hasModes) {
      lpCard.appendChild(h("div", "muted fp-note", `No modal results${modalSt && modalSt !== "finished" ? " — " + statusChip("MODAL", modalSt) : ""}.`));
      return;
    }
    if (st.lp.error) lpCard.appendChild(h("div", "po-warn-item", "⚠ " + esc(st.lp.error)));
    const d = st.lp.data;
    if (!d) {
      lpCard.appendChild(h("div", "muted fp-note", "Not computed for this analysis yet."));
      return;
    }
    const rows = [];
    for (const k of ["UX", "UY", "UZ"]) {
      const a = (d.acceleration || {})[k];
      if (a) rows.push(["Acceleration", k, a.static, a.dynamic]);
    }
    for (const [p, a] of Object.entries(d.patterns || {})) rows.push(["Load Pattern", p, a.static, a.dynamic]);
    const pc = v => v == null ? "—" : (+U.toDisplay("none", v)).toFixed(3);
    lpCard.appendChild(table([{ label: "Item Type", txt: true }, { label: "Item", txt: true },
      { label: "Static", unit: "%" }, { label: "Dynamic", unit: "%" }],
    rows.map(([a, b, s, dy]) => [a, b, pc(s), pc(dy)]), "fp-lp-table"));
    lpCard.appendChild(h("div", "muted fp-meta", `${(r.modal.periods || []).length} modes · static = Σ (φᵢᵀr)²/(ωᵢ² mᵢ) / rᵀK⁻¹r · dynamic = cumulative modal mass ratio for accelerations`));
  }

  /* ---------------- wiring */
  const renderAll = () => {
    try { syncFreqTab(); if (store.tab === "freq") renderFreq(); else if (store.results) { renderSS(); renderPSD(); } }
    catch (err) { console.error("frequency view", err); }
    try { renderEnergy(); } catch (err) { console.error("energy view", err); }
    try { renderPushover(); } catch (err) { console.error("pushover view", err); }
    try { renderLP(); } catch (err) { console.error("load participation", err); }
  };
  document.addEventListener("sky:results-changed", renderAll);
  document.addEventListener("sky:units-changed", renderAll);
  document.addEventListener("sky:model-changed", () => { try { syncFreqTab(); } catch (err) { console.error(err); } });
  const thSel = $("thCaseSelect"), poSel = $("poCaseSelect");
  if (thSel) thSel.addEventListener("change", () => renderEnergy());
  if (poSel) poSel.addEventListener("change", () => renderPushover());
  renderAll();

  sky.freq = {
    render: renderAll, renderFreq, renderSS, renderPSD, renderEnergy, renderPushover, renderLP, state: st,
    showFreq: () => { sky.setMode && sky.setMode("analyze"); sky.switchTab("freq"); renderFreq(); },
    xyChart, barChart,
  };
}
