/* SkyFrame — ETABS-style "Create Report", File > Model Info and the
   Analysis Log run-time extension. CONTRACT "Open structure wind, model
   info, run log and report data".

     File → Create Report…   options dialog (sections, story-plot cases,
                             envelope size, paper) → a printable / PDF-
                             friendly SELF-CONTAINED HTML report (inline CSS,
                             inline SVG plots, PNG data-URL images, no
                             external assets): Preview / Print (new tab) or
                             Download HTML.
     File → Model Info…      counts, units, extents, last analysis (time,
                             duration, warnings, results current or stale).
     Analyze → Analysis Log  + "Run times" group: per-case wall times and
                             the warnings collected by the backend (GET
                             /api/analyze/log; ?mock=1: js/mock_openwind.js).

   The report never depends on the backend: live mode merges POST
   /api/report/data (load-case equilibrium from the engine's own load
   totals) when it answers, otherwise the equilibrium check uses the
   client-side load totals. Every value goes through js/units.js. */

import RXU from "./units.js";
import { dialog as rxDialog, closeDialog as rxCloseDialog, btn as rxBtn, footBar as rxFootBar,
  errorLine as rxErrorLine, showError as rxShowError } from "./analysisdlg.js";
import { owModelInfo as rxModelInfo, owMockRunLog as rxMockRunLog, owRunWarnings as rxRunWarnings,
  owNormalize as rxOwNormalize } from "./mock_openwind.js";

const U = RXU;
let SKY = null;
const RX = { runLog: null, sig: null, resultsAt: null };

const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const isNum = v => typeof v === "number" && isFinite(v);
const num = (v, d = 2) => isNum(v) ? (+v).toFixed(d) : "—";
const uf = (kind, v, d) => isNum(v) ? U.fmt(kind, v, d) : "—";
const ul = kind => U.label(kind);
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

function ensureCss() {
  if (document.querySelector("link[data-ow-css]")) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/openwind.css"; l.setAttribute("data-ow-css", "1");
  document.head.appendChild(l);
}
function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of [].concat(kids)) if (c != null) n.append(c);
  return n;
}
function group(legend) {
  const g = el("fieldset", { class: "dlg-group" });
  g.appendChild(el("legend", { text: legend }));
  return g;
}

/* ================================================================
   run log (fetched after each solve)
   ================================================================ */
async function refreshRunLog() {
  const S = SKY.store;
  RX.sig = S.model ? JSON.stringify(S.model) : null;
  RX.resultsAt = S.results ? new Date() : null;
  if (!S.results) { RX.runLog = null; return; }
  if (S.mock) { RX.runLog = rxMockRunLog(S.model, S.results, S.lastSolveMs); }
  else {
    try {
      const res = await fetch("/api/analyze/log");
      const d = await res.json();
      RX.runLog = d && d.available ? d : null;
    } catch (e) { RX.runLog = null; }
    if (!RX.runLog) RX.runLog = rxMockRunLog(S.model, S.results, S.lastSolveMs);   // older backend
  }
  const host = document.getElementById("rxLogTimes");
  if (host) renderLogTimes(host);
}
const resultsCurrent = () => !!(SKY.store.results && RX.sig && JSON.stringify(SKY.store.model) === RX.sig);

function timesTableHtml(log) {
  const cs = (log.cases || []);
  const tmax = Math.max(1e-9, ...cs.map(c => c.time_s || 0));
  return `<table class="data-table rx-times" id="rxTimesTable"><thead><tr><th class="txt">Case</th><th class="txt">Type</th>` +
    `<th class="txt">Status</th><th>Time (s)</th><th class="txt"></th></tr></thead><tbody>` +
    cs.map(c => `<tr><td class="txt">${esc(c.name)}</td><td class="txt">${esc(String(c.kind).replace(/_/g, " "))}</td>` +
      `<td class="txt">${esc(String(c.status).replace(/_/g, " "))}</td><td>${c.time_s == null ? "—" : (+c.time_s).toFixed(3)}</td>` +
      `<td class="txt rx-bar">${c.time_s == null ? "" : `<span class="rx-barfill" style="width:${(100 * c.time_s / tmax).toFixed(1)}%"></span>`}</td></tr>`).join("") +
    `</tbody></table>`;
}
function renderLogTimes(host) {
  const log = RX.runLog;
  host.textContent = "";
  host.appendChild(el("legend", { text: "Run times & backend warnings" }));
  if (!log) { host.appendChild(el("p", { class: "muted cd-note", text: "No run log yet — run the analysis." })); return; }
  host.appendChild(el("p", { class: "cd-note", html: `Started <b>${esc(new Date(log.started).toLocaleString())}</b> · total <b>${(+log.total_s).toFixed(3)} s</b>` +
    ` · Σ cases ${(log.cases || []).reduce((a, c) => a + (c.time_s || 0), 0).toFixed(3)} s` +
    (log.mock ? ` · <span class="muted">offline mock (times estimated)</span>` : "") +
    (resultsCurrent() ? "" : ` · <span class="rx-warn">model changed since this run</span>`) }));
  host.appendChild(el("div", { html: timesTableHtml(log) }));
  const ws = log.warnings || [];
  host.appendChild(ws.length
    ? el("ul", { class: "rx-warnlist", id: "rxLogWarnList", html: ws.map(w => `<li><span class="rx-src">${esc(w.source)}</span>${esc(w.message)}</li>`).join("") })
    : el("p", { class: "muted cd-note", id: "rxLogWarnList", text: "No warnings collected from the backend." }));
}
/** Hook called by js/combodlg.js openAnalysisLog draw(). */
function analysisLogExtra(body) {
  ensureCss();
  const g = el("fieldset", { class: "dlg-group cx-group", id: "rxLogTimes" });
  renderLogTimes(g);
  body.appendChild(g);
}

/* ================================================================
   File → Model Info
   ================================================================ */
const COUNT_LABELS = [
  ["stories", "Stories"], ["joints", "Joints"], ["frames", "Frames"], ["shells", "Shells (areas)"], ["links", "Links"],
  ["materials", "Materials"], ["frame_sections", "Frame sections"], ["shell_sections", "Shell sections"],
  ["load_patterns", "Load patterns"], ["load_cases", "Static load cases"], ["load_combos", "Load combinations"],
  ["rs_cases", "Response-spectrum cases"], ["th_cases", "Time-history cases"], ["pushover_cases", "Pushover cases"],
  ["staged_cases", "Staged cases"], ["buckling_cases", "Buckling cases"], ["nonlinear_static_cases", "Nonlinear static cases"],
  ["supports", "Point supports"], ["spring_supports", "Point springs"], ["groups", "Groups"], ["section_cuts", "Section cuts"],
  ["assigned_loads", "Assigned load items"], ["open_wind_members", "Open-structure wind frames"],
];
export function openModelInfo() {
  ensureCss();
  const S = SKY.store;
  const body = el("div", { class: "rx-info" });
  const draw = () => {
    body.textContent = "";
    const info = rxModelInfo(S.model || {});
    const g0 = group("Model");
    g0.appendChild(el("dl", { class: "rx-kv", id: "rxInfoModel", html: [
      ["Name", esc(info.name || "—")], ["File", esc(S.fileName || "(no file)")],
      ["Display units", esc(U.unitSet().label)], ["Stored units", "kN · m · °C (SI)"],
      ["Height", esc(U.fmtU("length", info.height, 2))],
      ["Plan extents", `${esc(U.fmt("length", info.plan[0], 2))} × ${esc(U.fmtU("length", info.plan[1], 2))}`],
      ["Diaphragm", esc(info.diaphragm)], ["Base fixity", esc(info.base_fixity)],
      ["Frames by kind", esc(Object.entries(info.members_by_kind).map(([k, n]) => `${n} ${k}`).join(" · ") || "—")],
      ["Shells by kind", esc(Object.entries(info.shells_by_kind).map(([k, n]) => `${n} ${k}`).join(" · ") || "—")],
      ["Unsaved edits", S.dirty ? `<span class="rx-warn">yes</span>` : "no"],
      ["Cases set to not run", esc(info.cases_not_run.join(", ") || "—")],
    ].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("") }));
    body.appendChild(g0);
    const g1 = group("Object counts");
    g1.appendChild(el("dl", { class: "rx-kv", id: "rxInfoCounts", html: COUNT_LABELS.map(([k, lbl]) =>
      `<div><dt>${esc(lbl)}</dt><dd data-k="${k}">${info.counts[k]}</dd></div>`).join("") }));
    body.appendChild(g1);
    const g2 = group("Last analysis");
    g2.id = "rxInfoAnalysis";
    const r = S.results, log = RX.runLog;
    if (!r) g2.appendChild(el("p", { class: "muted cd-note", text: "Not analysed yet." }));
    else {
      const ws = log ? log.warnings : rxRunWarnings(r);
      g2.appendChild(el("dl", { class: "rx-kv", html: [
        ["Run at", esc(log ? new Date(log.started).toLocaleString() : RX.resultsAt ? RX.resultsAt.toLocaleString() : "—")],
        ["Solve time", log ? `${(+log.total_s).toFixed(3)} s` : S.lastSolveMs != null ? `${(S.lastSolveMs / 1000).toFixed(2)} s` : "—"],
        ["Results", resultsCurrent() ? `<span class="rx-ok">current</span>` : `<span class="rx-warn">model changed since the run</span>`],
        ["Static cases / combos", `${Object.keys(r.cases || {}).length} / ${Object.keys(r.combos || {}).length}`],
        ["RS / TH cases", `${Object.keys(r.rs_cases || {}).length} / ${Object.keys(r.th_cases || {}).length}`],
        ["Modes", String(((r.modal || {}).periods || []).length)],
        ["Warnings", ws.length ? `<span class="rx-warn">${ws.length}</span>` : `<span class="rx-ok">0</span>`],
      ].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("") }));
      if (ws.length) g2.appendChild(el("ul", { class: "rx-warnlist", html: ws.slice(0, 30).map(w =>
        `<li><span class="rx-src">${esc(w.source)}</span>${esc(w.message)}</li>`).join("") }));
    }
    body.appendChild(g2);
  };
  draw();
  const bLog = rxBtn("Analysis Log…", "", () => { dlg.close(); SKY.openAnalysisLog && SKY.openAnalysisLog(); });
  const bRep = rxBtn("Create Report…", "", () => { dlg.close(); openCreateReport(); });
  const bOk = rxBtn("Close", "btn-primary", () => dlg.close()); bOk.id = "rxInfoClose";
  const fb = rxFootBar("File → Model Info", [bLog, bRep, bOk]);
  const off = U.onUnitsChange(draw);
  const dlg = rxDialog("rxInfoDlg", { title: "Model Info", iconId: "menu-file", wide: true, body, foot: fb.wrap, onClose: off });
  dlg.el.classList.add("rx-dlg");
  return dlg;
}

/* ================================================================
   client-side load totals (equilibrium fallback)
   ================================================================ */
function polyArea(pts) {
  if (!pts || pts.length < 3) return 0;
  let nx = 0, ny = 0, nz = 0;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    nx += (a[1] - b[1]) * (a[2] + b[2]); ny += (a[2] - b[2]) * (a[0] + b[0]); nz += (a[0] - b[0]) * (a[1] + b[1]);
  }
  return 0.5 * Math.hypot(nx, ny, nz);
}
export function appliedTotals(model, pname) {
  const p = (model.patterns || {})[pname];
  const F = [0, 0, 0];
  if (!p) return F;
  const mem = {};
  for (const m of model.members || []) mem[m.uid] = m;
  const len = m => Math.hypot(m.pj[0] - m.pi[0], m.pj[1] - m.pi[1], m.pj[2] - m.pi[2]);
  const vertical = m => Math.hypot(m.pj[0] - m.pi[0], m.pj[1] - m.pi[1]) < 1e-9 * Math.max(1, len(m));
  const add = (m, dir, w) => {
    if (dir === "gravity") { if (!m || !vertical(m)) F[2] -= w; }
    else if (dir === "global_x") F[0] += w;
    else if (dir === "global_y") F[1] += w;
    else if (dir === "global_z") F[2] += w;
  };
  for (const u of p.member_udls || []) { const m = mem[u.member_uid]; if (m) add(m, "gravity", u.w * len(m)); }
  for (const l of p.member_loads || []) {
    const m = mem[l.member_uid];
    if (!m || l.kind === "moment") continue;
    const a = l.a ?? 0, b = l.b ?? 1;
    const w = l.kind === "point" ? l.w : l.kind === "trapezoid" ? 0.5 * (l.w + (l.w2 || 0)) * (b - a) * len(m) : l.w * (b - a) * len(m);
    add(m, l.direction || "gravity", w);
  }
  for (const n of p.nodal_loads || []) { F[0] += n.fx || 0; F[1] += n.fy || 0; F[2] += n.fz || 0; }
  for (const s of p.story_forces || []) { F[0] += s.fx || 0; F[1] += s.fy || 0; }
  const sh = {};
  for (const s of model.shells || []) sh[s.uid] = s;
  for (const a of p.area_loads || []) {
    const s = sh[a.region_uid];
    if (!s) continue;
    const A = polyArea(s.corners);
    add(null, a.direction || "gravity", a.q * A);
  }
  const sw = p.self_weight_factor || 0;
  if (sw) {
    for (const m of model.members || []) {
      const s = (model.sections || {})[m.section] || {};
      const mat = (model.materials || {})[s.material] || {};
      F[2] -= sw * (s.A || 0) * (s.mod_weight ?? 1) * (mat.unit_weight || 0) * len(m);
    }
    for (const r of model.shells || []) {
      const s = (model.shell_sections || {})[r.section] || {};
      const mat = (model.materials || {})[s.material] || {};
      F[2] -= sw * (s.thickness || 0) * (mat.unit_weight || 0) * polyArea(r.corners);
    }
  }
  return F;
}
function caseFactors(model, name) {
  const c = (model.cases || {})[name];
  if (c) return { ...(c.patterns || {}) };
  const cb = (model.combos || {})[name];
  if (!cb || (cb.combo_type && cb.combo_type !== "add")) return null;
  const out = {};
  for (const [cn, f] of Object.entries(cb.cases || {})) {
    const sub = (model.cases || {})[cn];
    if (!sub) return null;
    for (const [p, pf] of Object.entries(sub.patterns || {})) out[p] = (out[p] || 0) + f * pf;
  }
  return out;
}
function clientEquilibrium(model, r) {
  const tot = {};
  for (const p of Object.keys(model.patterns || {})) tot[p] = appliedTotals(model, p);
  const rows = [];
  const src = [...Object.entries(r.cases || {}).map(([n, cd]) => [n, "Linear Static", cd]),
    ...Object.entries(r.combos || {}).filter(([, cd]) => !cd.min).map(([n, cd]) => [n, "Combination", cd])];
  for (const [name, ctype, cd] of src) {
    const fac = caseFactors(model, name);
    if (!fac) continue;
    const A = [0, 1, 2].map(k => Object.entries(fac).reduce((a, [p, f]) => a + f * ((tot[p] || [0, 0, 0])[k]), 0));
    const b = cd.base || {};
    const R = ["FX", "FY", "FZ"].map(k => +b[k] || 0);
    const na = Math.hypot(...A), res = Math.hypot(A[0] + R[0], A[1] + R[1], A[2] + R[2]);
    rows.push({ case: name, case_type: ctype, applied_FX: A[0], applied_FY: A[1], applied_FZ: A[2],
      react_FX: R[0], react_FY: R[1], react_FZ: R[2], error_pct: na < 1e-12 ? (res < 1e-9 ? 0 : 100) : 100 * res / na });
  }
  return { rows, pattern_totals: tot };
}

/* ================================================================
   images — offscreen canvas → PNG data URL
   ================================================================ */
function drawWire(model, project, w, h, title, opts = {}) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const g = c.getContext("2d");
  g.fillStyle = "#ffffff"; g.fillRect(0, 0, w, h);
  const segs = [], polys = [];
  for (const m of model.members || []) if (!opts.filter || opts.filter(m)) segs.push([m.pi, m.pj, m.kind]);
  for (const s of model.shells || []) if (!opts.shellFilter || opts.shellFilter(s)) polys.push([s.corners || [], s.kind]);
  const pts = [];
  for (const [a, b] of segs) pts.push(project(a), project(b));
  for (const [cs] of polys) for (const p of cs) pts.push(project(p));
  if (!pts.length) { g.fillStyle = "#5c6672"; g.font = "14px sans-serif"; g.fillText("(no objects)", 20, 30); return c.toDataURL("image/png"); }
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = 36, sc = Math.min((w - 2 * pad) / Math.max(1e-9, x1 - x0), (h - 2 * pad - 18) / Math.max(1e-9, y1 - y0));
  const T = p => { const q = project(p); return [pad + (q[0] - x0) * sc + ((w - 2 * pad) - (x1 - x0) * sc) / 2, h - pad - (q[1] - y0) * sc - ((h - 2 * pad - 18) - (y1 - y0) * sc) / 2]; };
  for (const [cs, kind] of polys) {
    if (cs.length < 3) continue;
    g.beginPath();
    cs.forEach((p, i) => { const q = T(p); i ? g.lineTo(q[0], q[1]) : g.moveTo(q[0], q[1]); });
    g.closePath();
    g.fillStyle = kind === "wall" ? "rgba(95,143,201,0.18)" : "rgba(154,167,180,0.18)";
    g.fill(); g.strokeStyle = "rgba(110,125,140,0.6)"; g.lineWidth = 1; g.stroke();
  }
  const col = { column: "#2f6db0", beam: "#4a5868", brace: "#c98500" };
  for (const [a, b, kind] of segs) {
    const p = T(a), q = T(b);
    if (Math.hypot(q[0] - p[0], q[1] - p[1]) < 1.5) {          // column seen end-on (plan)
      g.fillStyle = col[kind] || "#4a5868"; g.fillRect(p[0] - 4, p[1] - 4, 8, 8);
      continue;
    }
    g.beginPath(); g.moveTo(p[0], p[1]); g.lineTo(q[0], q[1]);
    g.strokeStyle = col[kind] || "#4a5868"; g.lineWidth = kind === "column" ? 2.2 : 1.6; g.stroke();
  }
  if (opts.grid && model.grid) {
    g.fillStyle = "#5c6672"; g.font = "11px sans-serif";
    (model.grid.x_lines || []).forEach((x, i) => { const p = T([x, (model.grid.y_lines || [0])[0], opts.z || 0]); g.fillText((model.grid.x_labels || [])[i] || String(i + 1), p[0] - 4, h - 10); });
  }
  g.fillStyle = "#10344d"; g.font = "bold 13px sans-serif"; g.fillText(title, 12, 18);
  return c.toDataURL("image/png");
}
function reportImages(model, storyName) {
  const imgs = [];
  const st = (model.stories || []).find(s => s.name === storyName) || (model.stories || [])[(model.stories || []).length - 1];
  const z = st ? st.elevation : 0;
  const near = v => Math.abs(v - z) < 1e-6;
  imgs.push({ title: `Plan view — ${st ? st.name : "base"} (z = ${U.fmtU("length", z, 2)})`,
    src: drawWire(model, p => [p[0], p[1]], 900, 560, `Plan — ${st ? st.name : ""}`, {
      grid: true, z,
      filter: m => (near(m.pi[2]) && near(m.pj[2])) || (m.kind !== "beam" && (near(m.pi[2]) || near(m.pj[2])) && Math.min(m.pi[2], m.pj[2]) < z),
      shellFilter: s => (s.corners || []).every(c => near(c[2])),
    }) });
  const cy = Math.cos(0.7), sy = Math.sin(0.7), cp = Math.cos(0.42), sp = Math.sin(0.42);
  imgs.push({ title: "3D model (isometric)",
    src: drawWire(model, p => { const x = p[0] * cy - p[1] * sy, y = p[0] * sy + p[1] * cy; return [x, p[2] * cp + y * sp]; }, 900, 620, "3D model") });
  const cv = document.getElementById("viewer3d");
  if (cv && cv.width > 0 && cv.height > 0 && cv.offsetParent !== null) {
    try { imgs.push({ title: "3D view (current display)", src: cv.toDataURL("image/png") }); } catch (e) { /* tainted */ }
  }
  return imgs;
}

/* ================================================================
   inline SVG story plots
   ================================================================ */
const PALETTE = ["#1274ab", "#c0392b", "#1a7f4b", "#b26a00", "#6c3fb5", "#2c8c99"];
function storyPlotSvg(title, unit, series, elevs) {
  const W = 330, H = 260, L = 48, R = 12, T = 26, B = 34;
  const vals = series.flatMap(s => s.pts.map(p => p[0]));
  let vmin = Math.min(0, ...vals), vmax = Math.max(0, ...vals);
  if (vmax - vmin < 1e-12) vmax = vmin + 1;
  const emax = Math.max(1e-9, ...elevs);
  const X = v => L + (v - vmin) / (vmax - vmin) * (W - L - R);
  const Y = e => H - B - e / emax * (H - T - B);
  let s = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" class="splot">` +
    `<text x="${L}" y="16" font-size="12" font-weight="700" fill="#10344d">${esc(title)}</text>` +
    `<line x1="${L}" y1="${Y(0)}" x2="${W - R}" y2="${Y(0)}" stroke="#9aa5b1"/><line x1="${X(0)}" y1="${T}" x2="${X(0)}" y2="${H - B}" stroke="#9aa5b1"/>`;
  for (const e of elevs) s += `<line x1="${L}" y1="${Y(e)}" x2="${W - R}" y2="${Y(e)}" stroke="#eef1f5"/>`;
  for (let i = 0; i <= 4; i++) {
    let v = vmin + (vmax - vmin) * i / 4;
    if (Math.abs(v) < 1e-9 * (vmax - vmin)) v = 0;
    s += `<text x="${X(v)}" y="${H - B + 14}" font-size="9.5" text-anchor="middle" fill="#5c6672">${esc(+v.toPrecision(3))}</text>`;
  }
  s += `<text x="${(L + W - R) / 2}" y="${H - 4}" font-size="10" text-anchor="middle" fill="#5c6672">${esc(unit)}</text>`;
  s += `<text x="10" y="${(T + H - B) / 2}" font-size="10" fill="#5c6672" transform="rotate(-90 10 ${(T + H - B) / 2})" text-anchor="middle">${esc("elevation (" + ul("length") + ")")}</text>`;
  series.forEach((sr, i) => {
    const c = PALETTE[i % PALETTE.length];
    const d = sr.pts.map((p, k) => `${k ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
    s += `<path d="${d}" fill="none" stroke="${c}" stroke-width="1.8"${sr.step ? "" : ""}/>` +
      sr.pts.map(p => `<circle cx="${X(p[0]).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="2.4" fill="${c}"/>`).join("");
  });
  return s + `</svg>`;
}

/* ================================================================
   report body
   ================================================================ */
export const RX_SECTIONS = [
  ["model", "Model summary (stories, counts)"], ["materials", "Materials & sections"],
  ["loads", "Load patterns"], ["cases", "Load cases"], ["combos", "Load combinations"],
  ["modal", "Mass source & modal results"], ["reactions", "Base reactions & equilibrium"],
  ["story", "Story drifts & shears (plots + tables)"], ["envelopes", "Governing envelopes"],
  ["images", "Model images (plan / 3D)"], ["openwind", "Open-structure wind"], ["runlog", "Analysis log (run times, warnings)"],
];
const allResultCases = r => [
  ...Object.entries(r.cases || {}).map(([n, cd]) => [n, cd, "static"]),
  ...Object.entries(r.combos || {}).map(([n, cd]) => [n, cd, "combo"]),
  ...Object.entries(r.rs_cases || {}).map(([n, cd]) => [n, cd, "rs"]),
];
function tbl(head, rows, id) {
  return `<table class="rt"${id ? ` id="${id}"` : ""}><thead><tr>${head.map(h => `<th class="${h.t ? "txt" : ""}">${h.h ?? h}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows.map(r => `<tr>${r.map(c => (c && typeof c === "object") ? `<td class="txt">${c.v}</td>` : `<td>${c}</td>`).join("")}</tr>`).join("") ||
      `<tr><td class="txt" colspan="${head.length}">—</td></tr>`}</tbody></table>`;
}
const Tx = v => ({ v: esc(v) });
const H = (h, t) => ({ h: esc(h), t });

export async function buildEtabsReport(opts = {}) {
  const S = SKY.store, model = S.model, r = S.results || null;
  const sel = new Set(opts.sections || RX_SECTIONS.map(s => s[0]));
  const topN = Math.max(1, Math.min(500, opts.topN || 25));
  const now = new Date();
  const info = rxModelInfo(model);
  const parts = [];
  const add = (key, title, html, note) => { if (sel.has(key) && html) parts.push({ key, title, html, note }); };

  /* live backend aggregate (optional, never required) */
  let backend = null;
  if (r && !S.mock && opts.useBackend !== false && (sel.has("reactions") || sel.has("runlog"))) {
    try {
      const res = await fetch("/api/report/data", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tables: ["load_case_equilibrium", "load_pattern_summary"] }) });
      if (res.ok) backend = await res.json();
    } catch (e) { backend = null; }
  }

  /* 1 model */
  const stories = [...(model.stories || [])].reverse();
  add("model", "Model summary",
    `<div class="kv">${[["Model", esc(model.name || "—")], ["Units", esc(U.unitSet().label)],
      ["Height", esc(U.fmtU("length", info.height, 2))], ["Plan extents", `${esc(U.fmt("length", info.plan[0], 2))} × ${esc(U.fmtU("length", info.plan[1], 2))}`],
      ["Diaphragm", esc(info.diaphragm)], ["Base fixity", esc(info.base_fixity)],
      ["Analysis", r ? (resultsCurrent() ? "results current" : "model changed since the run") : "not analysed"]]
      .map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join("")}</div>` +
    `<h3>Object counts</h3>` + tbl([H("Item", 1), "Count", H("Item", 1), "Count"], (() => {
      const items = COUNT_LABELS.filter(([k]) => info.counts[k]).map(([k, l]) => [l, info.counts[k]]);
      const rows = [];
      for (let i = 0; i < items.length; i += 2) rows.push([Tx(items[i][0]), items[i][1], Tx(items[i + 1] ? items[i + 1][0] : ""), items[i + 1] ? items[i + 1][1] : ""]);
      return rows;
    })(), "rptCounts") +
    `<h3>Stories</h3>` + tbl([H("Story", 1), `Height (${esc(ul("length"))})`, `Elevation (${esc(ul("length"))})`, "Frames"],
      stories.map(s => [Tx(s.name), uf("length", s.height, 2), uf("length", s.elevation, 2),
        (model.members || []).filter(m => m.story === s.name).length]), "rptStories"));

  /* 2 materials & sections */
  add("materials", "Materials and sections",
    tbl([H("Material", 1), H("Type", 1), `E (${esc(ul("modulus"))})`, "ν", `γ (${esc(ul("unit_weight"))})`, `f'c (${esc(ul("stress"))})`, `Fy (${esc(ul("stress"))})`],
      Object.values(model.materials || {}).map(m => [Tx(m.name), Tx(m.material_type || "concrete"), uf("modulus", m.E, 0), num(m.nu, 2),
        uf("unit_weight", m.unit_weight, 2), uf("stress", m.fc, 0), uf("stress", m.fy, 0)]), "rptMaterials") +
    tbl([H("Frame section", 1), H("Material", 1), `b (${esc(ul("dim"))})`, `h (${esc(ul("dim"))})`, `A (${esc(ul("area"))})`, `I33 (${esc(ul("inertia"))})`, `I22 (${esc(ul("inertia"))})`, "Frames"],
      Object.values(model.sections || {}).map(s => [Tx(s.name), Tx(s.material), uf("dim", s.b, 3), uf("dim", s.h, 3),
        isNum(s.A) ? U.sci("area", s.A) : "—", isNum(s.I33) ? U.sci("inertia", s.I33) : "—", isNum(s.I22) ? U.sci("inertia", s.I22) : "—",
        (model.members || []).filter(m => m.section === s.name).length]), "rptSections") +
    (Object.keys(model.shell_sections || {}).length ? tbl([H("Shell section", 1), H("Material", 1), `t (${esc(ul("dim"))})`, "Areas"],
      Object.values(model.shell_sections || {}).map(s => [Tx(s.name), Tx(s.material), uf("dim", s.thickness, 3),
        (model.shells || []).filter(x => x.section === s.name).length])) : ""));

  /* 3 load patterns */
  const eqClient = r ? clientEquilibrium(model, r) : { rows: [], pattern_totals: Object.fromEntries(Object.keys(model.patterns || {}).map(p => [p, appliedTotals(model, p)])) };
  add("loads", "Load patterns",
    tbl([H("Pattern", 1), H("Type", 1), "Self-wt mult.", "Load items", `ΣFX (${esc(ul("force"))})`, `ΣFY (${esc(ul("force"))})`, `ΣFZ (${esc(ul("force"))})`],
      Object.values(model.patterns || {}).map(p => {
        const t = eqClient.pattern_totals[p.name] || appliedTotals(model, p.name);
        const n = (p.member_udls || []).length + (p.member_loads || []).length + (p.nodal_loads || []).length +
          (p.story_forces || []).length + (p.area_loads || []).length + (p.thermal_loads || []).length;
        return [Tx(p.name), Tx(p.kind || "other"), num(p.self_weight_factor || 0, 2), n, uf("force", t[0], 2), uf("force", t[1], 2), uf("force", t[2], 2)];
      }), "rptPatterns"),
    "Applied totals summed from the load definitions (global axes, FZ &lt; 0 = downward); thermal loads are self-equilibrated.");

  /* 4 load cases */
  const fstr = d => Object.entries(d || {}).map(([k, f]) => `${+(+f).toPrecision(4)}·${esc(k)}`).join(" + ") || "—";
  const caseRows = [];
  for (const c of Object.values(model.cases || {})) caseRows.push([Tx(c.name), Tx("Linear static"), { v: fstr(c.patterns) }, Tx(c.pdelta ? "P-Δ" : "")]);
  for (const c of Object.values(model.rs_cases || {})) caseRows.push([Tx(c.name), Tx("Response spectrum"),
    { v: esc(`${c.direction} · ${c.combo_method || "CQC"} · damping ${num(c.damping, 3)} · scale ${num(c.scale ?? 1, 3)}`) }, Tx("")]);
  for (const c of Object.values(model.th_cases || {})) caseRows.push([Tx(c.name), Tx("Time history"),
    { v: esc(`${c.direction} · dt ${c.dt} s · ${(c.accel || []).length} pts`) }, Tx("")]);
  for (const [k, lbl] of [["pushover_cases", "Pushover"], ["buckling_cases", "Buckling"], ["staged_cases", "Staged construction"],
    ["nonlinear_static_cases", "Nonlinear static"]])
    for (const n of Object.keys(model[k] || {})) caseRows.push([Tx(n), Tx(lbl), Tx(""), Tx("")]);
  const notRun = new Set(model.cases_not_run || []);
  for (const row of caseRows) {
    const n = row[0].v;
    const st = r && r.case_status ? (r.case_status[n] || "") : "";
    row.push(Tx(notRun.has(n) ? "do not run" : st.replace(/_/g, " ")));
  }
  add("cases", "Load cases", tbl([H("Case", 1), H("Type", 1), H("Definition", 1), H("Options", 1), H("Status", 1)], caseRows, "rptCases"));

  /* 5 combos */
  add("combos", "Load combinations", Object.keys(model.combos || {}).length
    ? tbl([H("Combination", 1), H("Type", 1), H("Definition", 1), H("Status", 1)],
      Object.values(model.combos || {}).map(cb => [Tx(cb.name), Tx(cb.combo_type || "add"), { v: fstr(cb.cases) },
        Tx(r ? ((r.combo_status || {})[cb.name] || ((r.combos || {})[cb.name] ? "finished" : "—")) : "")]), "rptCombos")
    : `<p class="note">No load combinations defined.</p>`);

  /* 6 mass & modal */
  if (sel.has("modal")) {
    const ms = Object.entries(model.mass_source || {}).map(([k, f]) => `${f} × ${esc(k)}`).join(" + ") || "1.0 × DEAD";
    const mo = model.mass_options || {};
    let h = `<div class="kv"><div><span>Mass source (patterns)</span><b>${ms}</b></div>` +
      `<div><span>Element self mass</span><b>${mo.self_mass === false ? "no" : "yes"}</b></div>` +
      `<div><span>Lateral / vertical mass</span><b>${mo.include_lateral === false ? "—" : "lateral"}${mo.include_vertical ? " + vertical" : ""}</b></div>` +
      `<div><span>Lumped at stories</span><b>${mo.lump_at_stories === false ? "no" : "yes"}</b></div></div>`;
    const md = r && r.modal;
    if (md && (md.periods || []).length) {
      let cx = 0, cy = 0, cz = 0;
      h += tbl(["Mode", "T (s)", "f (Hz)", "UX", "UY", "RZ", "ΣUX", "ΣUY", "ΣRZ"],
        (md.participation || []).map((p, i) => {
          cx += p.ux || 0; cy += p.uy || 0; cz += p.rz || 0;
          return [p.mode ?? i + 1, num(md.periods[i], 4), num((md.frequencies || [])[i] ?? 1 / md.periods[i], 3),
            num(p.ux || 0, 4), num(p.uy || 0, 4), num(p.rz || 0, 4), num(cx, 4), num(cy, 4), num(cz, 4)];
        }), "rptModal");
      const n90 = k => { let s = 0; for (let i = 0; i < (md.participation || []).length; i++) { s += md.participation[i][k] || 0; if (s >= 0.9) return i + 1; } return null; };
      h += `<p class="note">Modes to reach 90 % mass: UX ${n90("ux") ?? "not reached"} · UY ${n90("uy") ?? "not reached"}.</p>`;
    } else h += `<p class="note">No modal results${r ? "" : " — run the analysis"}.</p>`;
    add("modal", "Mass source and modal results", h);
  }

  /* 7 reactions + equilibrium */
  if (sel.has("reactions")) {
    let h = "";
    if (r) {
      const src = allResultCases(r);
      h += tbl([H("Output case", 1), H("Type", 1), `FX (${esc(ul("force"))})`, `FY (${esc(ul("force"))})`, `FZ (${esc(ul("force"))})`,
        `MX (${esc(ul("moment"))})`, `MY (${esc(ul("moment"))})`, `MZ (${esc(ul("moment"))})`],
        src.map(([n, cd, k]) => { const b = cd.base || {};
          return [Tx(n), Tx(k + (cd.min ? " (max)" : "")), uf("force", b.FX, 2), uf("force", b.FY, 2), uf("force", b.FZ, 2),
            uf("moment", b.MX, 2), uf("moment", b.MY, 2), uf("moment", b.MZ, 2)]; }), "rptBase");
      const btab = backend && backend.tables && backend.tables.load_case_equilibrium;
      const eqRows = btab && Array.isArray(btab.rows) ? btab.rows : eqClient.rows;
      const srcNote = btab && Array.isArray(btab.rows) ? "engine load totals (POST /api/report/data)" : "client-side load totals";
      h += `<h3>Equilibrium check — applied loads vs base reactions</h3>` +
        tbl([H("Output case", 1), `Applied FX`, `Applied FY`, `Applied FZ`, `Reaction FX`, `Reaction FY`, `Reaction FZ`, "Error %", H("Check", 1)],
          eqRows.map(e => [Tx(e.case), uf("force", e.applied_FX, 2), uf("force", e.applied_FY, 2), uf("force", e.applied_FZ, 2),
            uf("force", e.react_FX, 2), uf("force", e.react_FY, 2), uf("force", e.react_FZ, 2), num(e.error_pct, 4),
            { v: (e.error_pct ?? 100) < 1 ? `<span class="ok">OK</span>` : `<span class="ng">check</span>` }]), "rptEquil") +
        `<p class="note">Forces in ${esc(ul("force"))}. Source: ${srcNote}. Error = |applied + reaction| / |applied|; envelope combinations are omitted.</p>`;
    } else h = `<p class="note">Run the analysis to report base reactions.</p>`;
    add("reactions", "Base reactions and equilibrium check", h);
  }

  /* 8 story drifts / shears */
  if (sel.has("story") && r && (r.story_order || []).length) {
    const order = r.story_order, elev = r.story_elev || {};
    const all = allResultCases(r).filter(([, cd]) => cd && cd.story);
    const lat = all.filter(([, cd]) => Object.values(cd.story).some(st => Math.abs(st.drift_x || 0) + Math.abs(st.drift_y || 0) > 1e-12));
    let pick = Array.isArray(opts.storyCases) && opts.storyCases.length ? all.filter(([n]) => opts.storyCases.includes(n)) : (lat.length ? lat : all);
    pick = pick.slice(0, 6);
    const elevs = [0, ...order.map(s => elev[s] || 0)];
    const ser = (cd, key, scale = 1) => [[0, 0], ...order.map(s => [((cd.story[s] || {})[key] || 0) * scale, elev[s] || 0])];
    const plots = [["Story drift X", "drift (‰)", "drift_x", 1000], ["Story drift Y", "drift (‰)", "drift_y", 1000],
      ["Story shear X", `shear (${ul("force")})`, "shear_x", U.factor("force")], ["Story shear Y", `shear (${ul("force")})`, "shear_y", U.factor("force")]];
    let h = `<div class="legend">${pick.map(([n], i) => `<span><i style="background:${PALETTE[i % PALETTE.length]}"></i>${esc(n)}</span>`).join("")}</div>` +
      `<div class="plots">${plots.map(([t, u, k, f]) => `<div class="plot">${storyPlotSvg(t, u, pick.map(([n, cd]) => ({ name: n, pts: ser(cd, k, f) })), elevs)}</div>`).join("")}</div>`;
    for (const [n, cd, kind] of pick) {
      h += `<div class="blk"><h3>${esc(n)} <span class="tag">${esc(kind)}</span></h3>` +
        tbl([H("Story", 1), `Elev (${esc(ul("length"))})`, `ux (${esc(ul("disp"))})`, `uy (${esc(ul("disp"))})`, "drift X ‰", "drift Y ‰",
          `Vx (${esc(ul("force"))})`, `Vy (${esc(ul("force"))})`],
          [...order].reverse().map(s => { const st = cd.story[s] || {};
            return [Tx(s), uf("length", elev[s], 2), uf("disp", st.ux || 0, 3), uf("disp", st.uy || 0, 3),
              num((st.drift_x || 0) * 1000, 4), num((st.drift_y || 0) * 1000, 4), uf("force", st.shear_x || 0, 2), uf("force", st.shear_y || 0, 2)]; })) + `</div>`;
    }
    add("story", "Story drifts and shears", h, `Plotted: ${pick.length} of ${all.length} output cases (max 6).`);
  }

  /* 9 governing envelopes */
  if (sel.has("envelopes") && r) {
    const combos = Object.entries(r.combos || {});
    const srcs = combos.length ? combos.flatMap(([n, cd]) => [[n, cd], ...(cd.min ? [[n + " (min)", cd.min]] : [])])
      : allResultCases(r).map(([n, cd]) => [n, cd]);
    const env = new Map();
    const K = [["N", 0], ["V2", 1], ["V3", 2], ["T", 3], ["M2", 4], ["M3", 5]];
    for (const [n, cd] of srcs)
      for (const [uid, f] of Object.entries(cd.member_forces || {})) {
        const e = env.get(uid) || {};
        for (const [k, i] of K) {
          const v = Math.max(Math.abs(f[i] || 0), Math.abs(f[i + 6] || 0));
          if (!e[k] || v > e[k][0]) e[k] = [v, n];
        }
        env.set(uid, e);
      }
    const mem = {};
    for (const m of model.members || []) mem[m.uid] = m;
    const rows = [...env.entries()].sort((a, b) => (b[1].M3 || [0])[0] - (a[1].M3 || [0])[0]).slice(0, topN)
      .map(([uid, e]) => { const m = mem[uid] || {};
        return [Tx(uid), Tx(m.kind || ""), Tx(m.story || ""), Tx(m.section || ""),
          uf("force", (e.N || [0])[0], 2), Tx((e.N || [0, ""])[1]), uf("force", (e.V2 || [0])[0], 2),
          uf("moment", (e.M2 || [0])[0], 2), uf("moment", (e.M3 || [0])[0], 2), Tx((e.M3 || [0, ""])[1])]; });
    let h = `<h3>Frame force envelope (top ${rows.length} by |M3|)</h3>` +
      tbl([H("Frame", 1), H("Kind", 1), H("Story", 1), H("Section", 1), `|P| (${esc(ul("force"))})`, H("Gov. P", 1),
        `|V2| (${esc(ul("force"))})`, `|M2| (${esc(ul("moment"))})`, `|M3| (${esc(ul("moment"))})`, H("Gov. M3", 1)], rows, "rptEnvMembers");
    const order = r.story_order || [];
    const dr = [...order].reverse().map(s => {
      let bx = [0, ""], by = [0, ""], vx = [0, ""], vy = [0, ""];
      for (const [n, cd] of allResultCases(r)) {
        const st = (cd.story || {})[s] || {};
        if (Math.abs(st.drift_x || 0) > bx[0]) bx = [Math.abs(st.drift_x), n];
        if (Math.abs(st.drift_y || 0) > by[0]) by = [Math.abs(st.drift_y), n];
        if (Math.abs(st.shear_x || 0) > vx[0]) vx = [Math.abs(st.shear_x), n];
        if (Math.abs(st.shear_y || 0) > vy[0]) vy = [Math.abs(st.shear_y), n];
      }
      return [Tx(s), num(bx[0] * 1000, 4), Tx(bx[1]), num(by[0] * 1000, 4), Tx(by[1]), uf("force", vx[0], 2), Tx(vx[1]), uf("force", vy[0], 2), Tx(vy[1])];
    });
    h += `<h3>Story drift / shear envelope (all output cases)</h3>` +
      tbl([H("Story", 1), "max drift X ‰", H("Gov.", 1), "max drift Y ‰", H("Gov.", 1), `max Vx (${esc(ul("force"))})`, H("Gov.", 1), `max Vy (${esc(ul("force"))})`, H("Gov.", 1)], dr, "rptEnvStory");
    const bk = ["FX", "FY", "FZ", "MX", "MY", "MZ"];
    const brow = bk.map(k => {
      let mx = [-Infinity, ""], mn = [Infinity, ""];
      for (const [n, cd] of srcs) { const v = (cd.base || {})[k]; if (!isNum(v)) continue; if (v > mx[0]) mx = [v, n]; if (v < mn[0]) mn = [v, n]; }
      const kind = k[0] === "F" ? "force" : "moment";
      return [Tx(k), uf(kind, isFinite(mx[0]) ? mx[0] : null, 2), Tx(mx[1]), uf(kind, isFinite(mn[0]) ? mn[0] : null, 2), Tx(mn[1])];
    });
    h += `<h3>Base reaction envelope (${combos.length ? "combinations" : "all output cases"})</h3>` +
      tbl([H("Component", 1), "Max", H("Gov.", 1), "Min", H("Gov.", 1)], brow, "rptEnvBase");
    add("envelopes", "Governing envelopes", h, combos.length ? "Envelopes over the load combinations (max and min sides)." : "No combinations — envelopes over every output case.");
  }

  /* 10 images */
  if (sel.has("images")) {
    const imgs = reportImages(model, opts.planStory);
    add("images", "Model images", `<div class="imgs">${imgs.map(im => `<figure><img alt="${esc(im.title)}" src="${im.src}"><figcaption>${esc(im.title)}</figcaption></figure>`).join("")}</div>`);
  }

  /* 11 open-structure wind */
  const owm = (model.members || []).filter(m => m.open_wind);
  if (sel.has("openwind") && owm.length) {
    const owp = Object.values(model.patterns || {}).filter(p => p.kind === "wind" && (p.member_loads || []).length && !(p.story_forces || []).length);
    add("openwind", "Open-structure wind",
      `<p class="note">${owm.length} frame${owm.length === 1 ? "" : "s"} with Open Structure Wind Parameters` +
      (owp.length ? ` · member-load wind patterns: ${owp.map(p => esc(p.name)).join(", ")}` : "") + `.</p>` +
      tbl([H("Kind", 1), H("Section", 1), "Frames", H("Include", 1), "Cf", `Width (${esc(ul("dim"))})`, "Shielding", H("Frames (first 8)", 1)],
        (() => {                       // grouped by identical assignment (ETABS-style summary)
          const groups = new Map();
          for (const m of owm) {
            const p = rxOwNormalize(m.open_wind);
            const k = JSON.stringify([m.kind, m.section, p]);
            if (!groups.has(k)) groups.set(k, { m, p, uids: [] });
            groups.get(k).uids.push(m.uid);
          }
          return [...groups.values()].map(({ m, p, uids }) => [Tx(m.kind), Tx(m.section), uids.length, Tx(p.include ? "yes" : "no"),
            p.cf == null ? "pattern" : num(p.cf, 3), p.width === "auto" ? "depth" : uf("dim", p.width, 3), num(p.shielding, 3),
            Tx(uids.slice(0, 8).join(", ") + (uids.length > 8 ? " …" : ""))]);
        })(), "rptOpenWind"));
  }

  /* 12 analysis log */
  if (sel.has("runlog")) {
    const log = (backend && backend.run_log) || RX.runLog;
    let h;
    if (!log) h = `<p class="note">No analysis run log${r ? "" : " — run the analysis"}.</p>`;
    else {
      h = `<p class="note">Run started ${esc(new Date(log.started).toLocaleString())} · total ${(+log.total_s).toFixed(3)} s${log.mock ? " (offline mock, estimated)" : ""}.</p>` +
        tbl([H("Case", 1), H("Type", 1), H("Status", 1), "Time (s)"], (log.cases || []).map(c => [Tx(c.name), Tx(String(c.kind).replace(/_/g, " ")),
          Tx(String(c.status).replace(/_/g, " ")), c.time_s == null ? "—" : (+c.time_s).toFixed(3)]), "rptRunTimes") +
        ((log.warnings || []).length ? `<h3>Warnings</h3><ul class="warns">${log.warnings.map(w => `<li><b>${esc(w.source)}</b> ${esc(w.message)}</li>`).join("")}</ul>`
          : `<p class="note">No warnings.</p>`);
    }
    add("runlog", "Analysis log", h);
  }

  /* assemble */
  const paper = opts.paper === "Letter" ? "Letter" : "A4";
  const orient = opts.orientation === "landscape" ? "landscape" : "portrait";
  const toc = parts.map((p, i) => `<li><a href="#sec-${p.key}">${i + 1}. ${esc(p.title)}</a></li>`).join("");
  const body = parts.map((p, i) => `<section class="sec${i && ["story", "envelopes", "images"].includes(p.key) ? " newpage" : ""}" id="sec-${p.key}" data-section="${p.key}"><h2>${i + 1}. ${esc(p.title)}</h2>` +
    (p.note ? `<p class="note">${p.note}</p>` : "") + p.html + `</section>`).join("\n");
  const title = `${model.name || "Model"} — Analysis Report`;
  return `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<meta name="generator" content="SkyFrame Create Report">
<style>
@page { size: ${paper} ${orient}; margin: 14mm 12mm 16mm; }
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0 auto; max-width: 1060px; padding: 28px 36px 48px; font: 11.5px/1.45 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #1c232c; background: #fff; }
.cover { min-height: 60vh; display: flex; flex-direction: column; justify-content: center; border-bottom: 3px solid #1274ab; margin-bottom: 18px; }
.cover h1 { font-size: 28px; margin: 0 0 6px; color: #10344d; }
.cover .sub { color: #5c6672; font-size: 13px; }
.cover .meta { margin-top: 18px; display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; font-size: 12.5px; }
.cover .meta span { color: #5c6672; }
.toc ol { columns: 2; font-size: 12.5px; list-style: none; padding-left: 0; }
.toc a { color: #1274ab; text-decoration: none; }
.printbtn { font: inherit; font-weight: 600; color: #fff; background: #1274ab; border: 0; border-radius: 6px; padding: 7px 16px; cursor: pointer; margin-top: 14px; width: max-content; }
.sec { margin: 0 0 22px; }
.sec h2 { font-size: 16px; color: #10344d; border-bottom: 1.5px solid #1274ab; padding-bottom: 4px; margin: 18px 0 10px; break-after: avoid; }
.sec h3 { font-size: 12.5px; margin: 14px 0 5px; color: #38424d; break-after: avoid; }
.note { color: #5c6672; margin: 4px 0 8px; }
.kv { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 2px 22px; margin-bottom: 10px; }
.kv div { display: flex; justify-content: space-between; border-bottom: 1px dashed #d9e0e7; padding: 3px 0; }
.kv span { color: #5c6672; }
table.rt { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; margin: 0 0 10px; }
table.rt th { font-size: 10px; font-weight: 650; letter-spacing: .1px; color: #38424d; background: #eef2f6; border: 1px solid #d0d8e0; padding: 4px 6px; text-align: right; }
table.rt td { border: 1px solid #e1e6ec; padding: 3px 6px; text-align: right; }
table.rt .txt { text-align: left; }
table.rt tbody tr:nth-child(even) td { background: #f8fafc; }
thead { display: table-header-group; }
tr, figure, .plot, .blk h3 { break-inside: avoid; page-break-inside: avoid; }
.tag { font-size: 9.5px; color: #5c6672; background: #eef1f5; border: 1px solid #d9e0e7; border-radius: 99px; padding: 0 7px; }
.ok { color: #1a7f4b; font-weight: 650; } .ng { color: #c0392b; font-weight: 650; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 2px 0 8px; font-size: 11px; }
.legend i { display: inline-block; width: 14px; height: 3px; vertical-align: middle; margin-right: 5px; }
.plots { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.plot { border: 1px solid #e1e6ec; border-radius: 6px; padding: 4px; }
.plot svg { width: 100%; height: auto; display: block; }
.imgs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
figure { margin: 0; border: 1px solid #e1e6ec; border-radius: 6px; padding: 6px; }
figure img { width: 100%; height: auto; display: block; }
figcaption { color: #5c6672; font-size: 11px; margin-top: 4px; }
ul.warns { margin: 0; padding-left: 18px; } ul.warns b { font-family: ui-monospace, monospace; font-size: 10.5px; color: #8a5a00; margin-right: 6px; }
footer { border-top: 1px solid #d9e0e7; margin-top: 26px; padding-top: 8px; color: #5c6672; font-size: 10.5px; }
@media print {
  body { padding: 0; max-width: none; font-size: 10px; }
  .printbtn { display: none; }
  .cover { min-height: 0; padding: 40mm 0 10mm; break-after: page; }
  .toc { break-after: page; }
  .sec.newpage { break-before: page; }
  a { color: inherit; }
}
</style></head>
<body>
<header class="cover">
  <h1>${esc(model.name || "Untitled model")}</h1>
  <div class="sub">Structural analysis report — linear / nonlinear analysis results (analysis only, no design checks)</div>
  <div class="meta">
    <span>Date</span><b>${esc(now.toLocaleString())}</b>
    <span>Units</span><b>${esc(U.unitSet().label)}</b>
    <span>Program</span><b>SkyFrame (OpenSees engine)</b>
    <span>Objects</span><b>${info.counts.stories} stories · ${info.counts.frames} frames · ${info.counts.shells} shells · ${info.counts.joints} joints</b>
    <span>Analysis</span><b>${r ? (resultsCurrent() ? "results current" : "results stale — model changed since the run") : "not analysed"}</b>
  </div>
  <button class="printbtn" onclick="window.print()">Print / Save as PDF</button>
</header>
<nav class="toc"><h2>Contents</h2><ol>${toc}</ol></nav>
${body}
<footer>Generated by SkyFrame · File → Create Report · ${esc(now.toISOString())}</footer>
</body></html>`;
}

/* ================================================================
   File → Create Report (options dialog)
   ================================================================ */
const PREF = { sections: null, topN: 25, paper: "A4", orientation: "portrait", storyCases: null, planStory: null };

export function openCreateReport() {
  ensureCss();
  const S = SKY.store, r = S.results;
  const body = el("div", { class: "rx-create" });
  body.appendChild(el("p", { class: "muted dlg-intro", html: "ETABS-style analysis report: a single self-contained, print-ready HTML file (tables, plots and images inline). " +
    (r ? "" : "<b>No results yet</b> — result sections are skipped until the analysis is run.") }));
  const g1 = group("Sections");
  const grid = el("div", { class: "rx-sections" });
  const chosen = new Set(PREF.sections || RX_SECTIONS.map(s => s[0]));
  const boxes = {};
  for (const [k, lbl] of RX_SECTIONS) {
    const i = el("input", { type: "checkbox", id: "rxSec-" + k });
    i.checked = chosen.has(k);
    boxes[k] = i;
    grid.appendChild(el("label", { class: "dlg-chk" }, [i, el("span", { text: lbl })]));
  }
  g1.appendChild(grid);
  const allBtns = el("div", { class: "rx-row" }, [
    rxBtn("All", "btn-sm", () => Object.values(boxes).forEach(b => { b.checked = true; })),
    rxBtn("None", "btn-sm", () => Object.values(boxes).forEach(b => { b.checked = false; })),
  ]);
  g1.appendChild(allBtns);
  body.appendChild(g1);

  const g2 = group("Options");
  const names = r ? allResultCases(r).filter(([, cd]) => cd && cd.story).map(([n]) => n) : [];
  const csel = el("select", { id: "rxStoryCases", multiple: true, size: String(Math.min(6, Math.max(3, names.length))) });
  const lateral = names.filter(n => { const cd = allResultCases(r).find(x => x[0] === n)[1];
    return Object.values(cd.story || {}).some(st => Math.abs(st.drift_x || 0) + Math.abs(st.drift_y || 0) > 1e-12); });
  const defPick = PREF.storyCases && PREF.storyCases.length ? PREF.storyCases : (lateral.length ? lateral : names).slice(0, 4);
  csel.innerHTML = names.map(n => `<option value="${esc(n)}"${defPick.includes(n) ? " selected" : ""}>${esc(n)}</option>`).join("");
  g2.appendChild(el("label", { class: "rx-row" }, [el("span", { text: "Story plots for (max 6)" }), csel]));
  const topN = el("input", { type: "number", id: "rxTopN", min: "1", max: "500", step: "1", value: String(PREF.topN) });
  g2.appendChild(el("label", { class: "rx-row" }, [el("span", { text: "Frames in the force envelope" }), topN]));
  const stSel = el("select", { id: "rxPlanStory" });
  const sts = [...(S.model.stories || [])].reverse();
  stSel.innerHTML = sts.map(s => `<option${s.name === PREF.planStory ? " selected" : ""}>${esc(s.name)}</option>`).join("");
  g2.appendChild(el("label", { class: "rx-row" }, [el("span", { text: "Plan image story" }), stSel]));
  const paper = el("select", { id: "rxPaper", html: `<option${PREF.paper === "A4" ? " selected" : ""}>A4</option><option${PREF.paper === "Letter" ? " selected" : ""}>Letter</option>` });
  const orient = el("select", { id: "rxOrient", html: `<option value="portrait"${PREF.orientation === "portrait" ? " selected" : ""}>Portrait</option><option value="landscape"${PREF.orientation === "landscape" ? " selected" : ""}>Landscape</option>` });
  g2.appendChild(el("label", { class: "rx-row" }, [el("span", { text: "Paper" }), paper, orient]));
  body.appendChild(g2);
  const err = rxErrorLine();
  const status = el("p", { class: "muted rx-status", id: "rxStatus" });
  body.append(err, status);

  const opts = () => {
    const o = { sections: RX_SECTIONS.map(s => s[0]).filter(k => boxes[k].checked),
      topN: Math.max(1, Math.min(500, parseInt(topN.value, 10) || 25)), paper: paper.value, orientation: orient.value,
      storyCases: [...csel.selectedOptions].map(o2 => o2.value).slice(0, 6), planStory: stSel.value || null };
    Object.assign(PREF, o);
    return o;
  };
  const build = async () => {
    rxShowError(err, "");
    const o = opts();
    if (!o.sections.length) { rxShowError(err, "Select at least one section."); return null; }
    status.textContent = "Building report…";
    const t0 = performance.now();
    try {
      const html = await buildEtabsReport(o);
      status.textContent = `Report ready · ${o.sections.length} sections · ${(html.length / 1024).toFixed(0)} KB · ${(performance.now() - t0).toFixed(0)} ms`;
      SKY.lastReportHtml = html;
      return html;
    } catch (e) { console.error(e); rxShowError(err, "Report failed: " + e.message); status.textContent = ""; return null; }
  };
  const preview = async () => {
    const win = window.open("", "_blank");
    const html = await build();
    if (!html) { if (win) win.close(); return; }
    if (!win) { rxShowError(err, "Popup blocked — allow popups, or use Download HTML."); return; }
    win.document.open(); win.document.write(html); win.document.close();
  };
  const download = async () => {
    const html = await build();
    if (!html) return;
    const blob = new Blob([html], { type: "text/html;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${(S.model.name || "model").replace(/[^\w.-]+/g, "_")}_report.html`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  };
  const bPrev = rxBtn("Preview / Print", "", preview); bPrev.id = "rxPreviewBtn";
  const bDl = rxBtn("Download HTML", "btn-primary", download); bDl.id = "rxDownloadBtn";
  const fb = rxFootBar("File → Create Report", [rxBtn("Cancel", "", () => dlg.close()), bPrev, bDl]);
  const dlg = rxDialog("rxReportDlg", { title: "Create Report", iconId: "menu-file", wide: true, body, foot: fb.wrap });
  dlg.el.classList.add("rx-dlg");
  return dlg;
}

/* ================================================================
   install
   ================================================================ */
export function initReportX(sky) {
  if (!sky) return;
  SKY = sky;
  Object.assign(sky, {
    openCreateReport: () => openCreateReport(),
    openModelInfo: () => openModelInfo(),
    buildEtabsReport: o => buildEtabsReport(o),
    analysisLogExtra: body => analysisLogExtra(body),
    runLog: () => RX.runLog,
    refreshRunLog: () => refreshRunLog(),
    closeReportX: () => { rxCloseDialog("rxReportDlg"); rxCloseDialog("rxInfoDlg"); },
  });
  document.addEventListener("sky:results-changed", () => { refreshRunLog().catch(e => console.warn("run log", e)); });
}
