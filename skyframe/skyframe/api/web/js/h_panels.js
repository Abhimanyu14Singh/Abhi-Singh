/* SkyFrame Wave H — Table and Plot window contents (docked / popped windows).

   Table window  any Display › Show Tables table (POST /api/tables/<key>, the
                 ?mock=1 router of js/tables.js) in the virtualised grid of
                 js/tables.js, filtered to the window's results case (own or
                 the toolbar's), text filter, unit-converted cells.
   Plot window   Story response (max displacement / drift ratio / story
                 shear per story, X or Y, the window's case; ETABS Display ›
                 Story Response Plots) or a time-history Plot Function
                 (js/plotfn.js plotFunctions: joint / story / base / energy
                 series of the window's TH case).
   Every value crosses js/units.js; nothing is written to the model. */

import hpU from "./units.js";
import { skyApi as hpApi, VirtualGrid as HpGrid } from "./tables.js";
import { plotFunctions as hpPlotFunctions } from "./plotfn.js";
import { el as hpEl } from "./dlgkit.js";

const SVGNS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}) => { const e = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };
let tableList = null;

/** Store case name of a Wave G case key (tables filter), or null. */
export function hpCaseName(key) {
  if (!key) return null;
  if (key.startsWith("c:") || key.startsWith("k:")) return key.slice(2);
  return null;
}
/** The case block of a Wave G key for story plots. */
export function hpCaseBlock(r, key) {
  if (!r || !key) return null;
  const [pre, ...rest] = key.split(":"); const n = rest.join(":");
  const blk = { c: r.cases, k: r.combos, rs: r.rs_cases }[pre];
  if (blk && blk[n]) return blk[n];
  if (pre === "th" && r.th_cases && r.th_cases[n]) return { story: ((r.th_cases[n].peaks || {}).story) || {} };
  return null;
}
/** Story profile points [{story, z, v}] of quantity q ("disp" | "drift" | "shear") in dir "X" | "Y". */
export function hpStoryProfile(r, key, q, dir) {
  const cd = hpCaseBlock(r, key);
  if (!cd || !cd.story) return [];
  const order = r.story_order || Object.keys(cd.story);
  const elev = r.story_elev || {};
  const d = dir.toLowerCase();
  const k = q === "disp" ? "u" + d : q === "drift" ? "drift_" + d : "shear_" + d;
  return order.map(s => ({ story: s, z: +elev[s] || 0, v: +((cd.story[s] || {})[k]) })).filter(p => isFinite(p.v));
}

export class HTablePanel {
  constructor(host, ctx) {
    this.host = host; this.ctx = ctx;
    this.key = (ctx.state && ctx.state.key) || "";
    this.filter = (ctx.state && ctx.state.filter) || "";
    this.bar = hpEl("div", { class: "h-panel-bar" });
    this.sel = hpEl("select", { class: "mini-select h-tbl-sel", "aria-label": "Table", "data-h-table": ctx.id });
    this.find = hpEl("input", { type: "search", class: "mini-input h-tbl-find", placeholder: "Filter rows…", "aria-label": "Filter rows", value: this.filter });
    this.count = hpEl("span", { class: "muted h-tbl-count" });
    this.bar.append(this.sel, this.find, this.count);
    this.gridHost = hpEl("div", { class: "h-tbl-host" });
    this.msg = hpEl("div", { class: "muted h-panel-msg hidden" });
    host.append(this.bar, this.msg, this.gridHost);
    this.grid = new HpGrid(this.gridHost, { emptyText: "No rows" });
    this.sel.addEventListener("change", () => { this.key = this.sel.value; this.load(); ctx.changed && ctx.changed(); });
    let t = 0;
    this.find.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { this.filter = this.find.value; this.grid.setFilter(this.filter); this.syncCount(); ctx.changed && ctx.changed(); }, 90); });
    this.req = 0;
    this.fill();
  }
  state() { return { key: this.key, filter: this.filter || undefined }; }
  async fill() {
    try {
      if (!tableList) tableList = (await hpApi(this.ctx.sky, "/api/tables/list")).tables || [];
    } catch (e) { tableList = null; this.showMsg(`Tables unavailable: ${e.message}`); return; }
    this.sel.textContent = "";
    const groups = new Map();
    for (const t of tableList) { const g = String(t.group || "").replace(/^Analysis Results\s*>\s*/, ""); if (!groups.has(g)) groups.set(g, []); groups.get(g).push(t); }
    for (const [g, list] of groups) {
      const og = hpEl("optgroup", { label: g || "Tables" });
      for (const t of list) og.appendChild(hpEl("option", { value: t.key }, [t.title]));
      this.sel.appendChild(og);
    }
    if (!tableList.some(t => t.key === this.key)) {
      const pref = tableList.find(t => /displacement/i.test(t.title)) || tableList[0];
      this.key = pref ? pref.key : "";
    }
    this.sel.value = this.key;
    this.load();
  }
  showMsg(t) { this.msg.textContent = t || ""; this.msg.classList.toggle("hidden", !t); }
  syncCount() { this.count.textContent = this.grid.view ? `${this.grid.view.length} rows` : ""; }
  async load() {
    const S = this.ctx.sky.store, id = ++this.req;
    if (!this.key) return;
    if (!S.results) { this.grid.setData([], []); this.showMsg("Run the analysis — tables show the last results."); this.syncCount(); return; }
    const caseName = hpCaseName(this.ctx.caseKey());
    try {
      const t = await hpApi(this.ctx.sky, `/api/tables/${encodeURIComponent(this.key)}`, caseName ? { cases: [caseName] } : {});
      if (id !== this.req) return;
      this.grid.setData(t.columns || [], t.rows || []);
      if (this.filter) this.grid.setFilter(this.filter);
      this.showMsg((t.warnings || []).join(" · "));
      this.lastCase = caseName;
      this.syncCount();
    } catch (e) { if (id === this.req) { this.grid.setData([], []); this.showMsg(`Table failed: ${e.message}`); } }
  }
  refresh() { const c = hpCaseName(this.ctx.caseKey()); if (c !== this.lastCase || !this.grid.rows.length) this.load(); }
  destroy() { this.req++; }
}

export class HPlotPanel {
  constructor(host, ctx) {
    this.host = host; this.ctx = ctx;
    const st = ctx.state || {};
    this.mode = st.mode === "fn" ? "fn" : "story";
    this.q = ["disp", "drift", "shear"].includes(st.q) ? st.q : "disp";
    this.dir = st.dir === "Y" ? "Y" : "X";
    this.fn = st.fn || "";
    this.bar = hpEl("div", { class: "h-panel-bar" });
    const mk = (opts, val, label, cls) => { const s = hpEl("select", { class: "mini-select " + cls, "aria-label": label }); for (const [k, t] of opts) s.appendChild(hpEl("option", { value: k, selected: k === val ? true : null }, [t])); return s; };
    this.modeSel = mk([["story", "Story response"], ["fn", "Plot function (TH)"]], this.mode, "Plot", "h-plot-mode");
    this.qSel = mk([["disp", "Max displacement"], ["drift", "Drift ratio"], ["shear", "Story shear"]], this.q, "Quantity", "h-plot-q");
    this.dirSel = mk([["X", "X"], ["Y", "Y"]], this.dir, "Direction", "h-plot-dir");
    this.fnSel = hpEl("select", { class: "mini-select h-plot-fn", "aria-label": "Function" });
    this.bar.append(this.modeSel, this.qSel, this.dirSel, this.fnSel);
    this.chart = hpEl("div", { class: "h-plot-host", "data-h-plot": ctx.id });
    host.append(this.bar, this.chart);
    const ch = () => { this.mode = this.modeSel.value; this.q = this.qSel.value; this.dir = this.dirSel.value; this.fn = this.fnSel.value; this.draw(); ctx.changed && ctx.changed(); };
    [this.modeSel, this.qSel, this.dirSel, this.fnSel].forEach(s => s.addEventListener("change", ch));
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(this.chart);
    this.draw();
  }
  state() { return { mode: this.mode, q: this.q, dir: this.dir, fn: this.fn || undefined }; }
  refresh() { this.draw(); }
  destroy() { try { this.ro.disconnect(); } catch { /* ignore */ } }
  draw() {
    const r = this.ctx.sky.store.results, key = this.ctx.caseKey();
    this.qSel.hidden = this.dirSel.hidden = this.mode !== "story";
    this.fnSel.hidden = this.mode !== "fn";
    this.chart.textContent = "";
    const W = Math.max(200, this.chart.clientWidth || 320), Hh = Math.max(140, this.chart.clientHeight || 220);
    const note = t => this.chart.appendChild(hpEl("div", { class: "muted h-panel-msg", text: t }));
    if (!r) return note("Run the analysis — plots show the last results.");
    let series = [], xKind = "none", yKind = "length", xLabel = "", yLabel = "";
    if (this.mode === "story") {
      const pts = hpStoryProfile(r, key, this.q, this.dir);
      if (!pts.length) return note("No story results for this case (pick a static case, combination, RS or TH case).");
      xKind = this.q === "disp" ? "disp" : this.q === "shear" ? "force" : "none";
      const base = this.q === "shear" ? [] : [{ x: 0, y: 0 }];
      series = [base.concat(pts.map(p => ({ x: p.v, y: p.z, label: p.story })))];
      xLabel = { disp: "Displacement", drift: "Drift ratio", shear: "Shear" }[this.q] + ` ${this.dir}`;
      yLabel = "Elevation";
    } else {
      const name = key && key.startsWith("th:") ? key.slice(3) : null;
      const { T, fns } = name ? hpPlotFunctions(r, name) : { T: [], fns: [] };
      const sig = fns.map(f => f.id).join("|");
      if (this.fnSel.dataset.sig !== sig) {
        this.fnSel.textContent = "";
        for (const f of fns) this.fnSel.appendChild(hpEl("option", { value: f.id }, [f.label]));
        this.fnSel.dataset.sig = sig;
      }
      if (!fns.length) return note("Pick a time-history case for this window (results case) to plot its functions.");
      const f = fns.find(x => x.id === this.fn) || fns[0];
      this.fn = f.id; this.fnSel.value = f.id;
      series = [T.map((t, i) => ({ x: t, y: f.data[i] }))];
      xKind = "period"; yKind = f.kind === "disp" ? "disp" : f.kind === "force" ? "force" : f.kind === "moment" ? "moment" : f.kind === "accel" ? "accel" : f.kind === "velocity" ? "velocity" : "none";
      xLabel = "Time"; yLabel = f.label;
    }
    const conv = (k, v) => (k === "none" ? v : hpU.toDisplay(k, v));
    const P = series.map(s => s.map(p => ({ ...p, X: conv(xKind, p.x), Y: conv(yKind, p.y) })));
    const xs = P.flat().map(p => p.X), ys = P.flat().map(p => p.Y);
    let x0 = Math.min(0, ...xs), x1 = Math.max(0, ...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    if (x1 - x0 < 1e-12) x1 = x0 + 1; if (y1 - y0 < 1e-12) y1 = y0 + 1;
    const g = { L: 56, R: 12, T: 10, B: 30 };
    const sx = x => g.L + (x - x0) / (x1 - x0) * (W - g.L - g.R), sy = y => Hh - g.B - (y - y0) / (y1 - y0) * (Hh - g.T - g.B);
    const s = svg("svg", { viewBox: `0 0 ${W} ${Hh}`, width: W, height: Hh, class: "h-plot-svg", "data-h-points": String(P.flat().length) });
    s.appendChild(svg("rect", { x: g.L, y: g.T, width: W - g.L - g.R, height: Hh - g.T - g.B, fill: "none", stroke: "rgba(140,155,175,.35)" }));
    if (x0 < 0 && x1 > 0) s.appendChild(svg("line", { x1: sx(0), x2: sx(0), y1: g.T, y2: Hh - g.B, stroke: "rgba(140,155,175,.5)", "stroke-dasharray": "3 3" }));
    for (const ser of P) {
      s.appendChild(svg("polyline", { points: ser.map(p => `${sx(p.X).toFixed(1)},${sy(p.Y).toFixed(1)}`).join(" "), fill: "none", stroke: "#35b5e5", "stroke-width": 1.8 }));
      if (this.mode === "story") for (const p of ser) if (p.label) s.appendChild(svg("circle", { cx: sx(p.X), cy: sy(p.Y), r: 2.6, fill: "#35b5e5" }));
    }
    const lab = (x, y, t, a = "middle") => { const e = svg("text", { x, y, "text-anchor": a, "font-size": 10, fill: "rgba(200,210,222,.9)" }); e.textContent = t; s.appendChild(e); };
    const ux = xKind === "none" ? "" : ` [${hpU.label(xKind)}]`, uy = yKind === "none" ? "" : ` [${hpU.label(yKind)}]`;
    const f4 = v => (Math.abs(v) >= 1e4 || (Math.abs(v) < 1e-3 && v !== 0) ? v.toExponential(2) : (+v.toPrecision(4)).toString());
    lab(g.L, Hh - 8, f4(x0), "start"); lab(W - g.R, Hh - 8, f4(x1), "end"); lab((W + g.L) / 2, Hh - 8, xLabel + ux);
    lab(g.L - 4, Hh - g.B, f4(y0), "end"); lab(g.L - 4, g.T + 8, f4(y1), "end");
    lab(g.L + 6, g.T + 12, (yLabel + uy).slice(0, 34), "start");
    this.chart.appendChild(s);
    this.lastPoints = P.flat().length;
  }
}
