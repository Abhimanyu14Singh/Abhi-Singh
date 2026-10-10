/* SkyFrame — post-tensioning tendons (as loads) + hyperstatic case UI.
   CONTRACT "Post-tensioning tendons (as loads) and hyperstatic case".

     Draw    → Draw Tendon…                pick host beam(s) / a slab, then the
                                           ETABS-style Tendon Profile dialog
     Define  → Tendon Properties…          every tendon: material, area,
                                           jacking stress / end, losses, pattern
     Define  → Load Cases → Hyperstatic    model.hyperstatic_cases (js/casedlg.js
                                           hook → __sky.openHyperstaticCase)
     Display → Tendon Forces…              P(x) after losses (anchor-set zone
                                           shaded) + equivalent-load summary
     Display → Hyperstatic Results…        primary / secondary / total member
                                           diagrams + secondary reactions
     Properties panel                      tendons hosted by the selection
     Loads editor                          "PT" badge on tendon patterns
     3D / plan / elevation                 dashed magenta tendon polylines

   Every dialog edits a DRAFT and writes the model only on OK; an untouched
   tendon keeps its exact stored points, so OK with defaults leaves the model
   byte-identical. Inputs / outputs go through js/units.js; the model stays SI.
   All hooks are additive (__sky.*, viewer.ptOverlay, editor.ptOverlay). */

import * as ptME from "./modeledit.js";
import ptU from "./units.js";
import { stationDiagram as ptStationDiagram } from "./charts.js";
import * as TG from "./tendon_geom.js";

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const clone = o => JSON.parse(JSON.stringify(o));
const isNum = v => typeof v === "number" && isFinite(v);
const PT_COLOR = "#e040fb";
const COMP_KIND = { N: "force", V2: "force", V3: "force", T: "moment", M2: "moment", M3: "moment" };

/* ================================================================ dialog shell
   (same markup as js/casedlg.js, own Esc stack so a dialog opened from
   another closes first) */
const stack = [];
function onKey(e) {
  if (e.key !== "Escape" || !stack.length) return;
  e.stopImmediatePropagation(); e.preventDefault();
  stack[stack.length - 1].close();
}
function dialog(id, { title, wide = false, body, foot, onClose, onUnits }) {
  closeDialog(id);
  const back = document.createElement("div");
  back.className = "modal-backdrop sky-dlg cd-dlg pt-dlg";
  back.id = id;
  const box = document.createElement("div");
  box.className = "modal" + (wide ? " modal-wide" : "");
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-labelledby", id + "Title");
  const head = document.createElement("header");
  head.className = "modal-head";
  head.innerHTML = `<h2 id="${id}Title" class="dlg-title">${glyph()}<span>${esc(title)}</span></h2>`;
  const x = document.createElement("button");
  x.className = "icon-btn"; x.textContent = "×"; x.title = "Close";
  x.setAttribute("aria-label", "Close " + title);
  head.appendChild(x);
  const bd = document.createElement("div");
  bd.className = "modal-body";
  bd.appendChild(body);
  const ft = document.createElement("footer");
  ft.className = "modal-foot";
  ft.appendChild(foot);
  box.append(head, bd, ft);
  back.appendChild(box);
  (document.getElementById("app") || document.body).appendChild(back);
  const unsub = onUnits ? ptU.onUnitsChange(() => onUnits()) : null;
  const entry = { id, el: back };
  const close = () => {
    const i = stack.indexOf(entry);
    if (i < 0) return;
    stack.splice(i, 1);
    if (!stack.length) document.removeEventListener("keydown", onKey, true);
    if (unsub) unsub();
    back.remove();
    onClose && onClose();
  };
  entry.close = close;
  if (!stack.length) document.addEventListener("keydown", onKey, true);
  stack.push(entry);
  x.addEventListener("click", close);
  back.addEventListener("mousedown", e => { if (e.target === back) close(); });
  return { el: back, close };
}
function closeDialog(id) { const d = stack.find(s => s.id === id); if (d) d.close(); }
const glyph = () => `<svg class="dlg-ico" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><rect x="2" y="6" width="16" height="8" rx="1"/><path d="M2.5 8.5C6 13 14 13 17.5 8.5" stroke="${PT_COLOR}" stroke-dasharray="2.2 1.6"/></svg>`;

function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
}
function btn(label, cls, onClick, title, id) {
  const b = document.createElement("button");
  b.className = "btn" + (cls ? " " + cls : "");
  b.textContent = label;
  if (title) b.title = title;
  if (id) b.id = id;
  b.addEventListener("click", onClick);
  return b;
}
function footBar(note, buttons) {
  const wrap = el("div", "dlg-foot");
  const n = el("span", "muted dlg-foot-note");
  n.textContent = note || "";
  const b = el("div", "modal-btns");
  b.append(...buttons);
  wrap.append(n, b);
  return { wrap, note: n };
}
function errorLine(id) {
  const p = el("p", "field-error hidden dlg-error");
  if (id) p.id = id;
  return p;
}
const showError = (p, msg) => { p.textContent = msg || ""; p.classList.toggle("hidden", !msg); };
function group(legend, cls = "") {
  const g = el("fieldset", "dlg-group" + (cls ? " " + cls : ""));
  const l = document.createElement("legend");
  l.textContent = legend;
  g.appendChild(l);
  return g;
}
function select(options, value, onChange, id) {
  const s = document.createElement("select");
  if (id) s.id = id;
  s.innerHTML = options.map(o => {
    const [v, l, dis] = o;
    return `<option value="${esc(v)}"${String(v) === String(value) ? " selected" : ""}${dis ? " disabled" : ""}>${esc(l)}</option>`;
  }).join("");
  if (![...s.options].some(o => o.value === String(value)) && value != null && value !== "")
    s.insertAdjacentHTML("beforeend", `<option value="${esc(value)}" selected>${esc(value)} (missing)</option>`);
  s.addEventListener("change", () => onChange(s.value));
  return s;
}

/** Commit a focused input inside `root` BEFORE its DOM is rebuilt (removing a
    focused, edited input fires blur → change in the middle of the rebuild).
    Returns true when that commit already re-rendered. */
let settling = 0;
function settle(root) {
  const ae = document.activeElement;
  if (settling || !ae || !root.contains(ae) || !/^(INPUT|SELECT|TEXTAREA)$/.test(ae.tagName)) return false;
  settling++;
  try { ae.blur(); } finally { settling--; }
  return !ae.isConnected;
}

/* ---- units: U kinds + "wobble" (1/length) */
const kLabel = kind => kind === "wobble" ? `1/${ptU.label("length")}` : ptU.label(kind);
const toD = (kind, si) => kind === "wobble" ? si / ptU.factor("length") : ptU.toDisplay(kind, si);
const fromD = (kind, v) => kind === "wobble" ? v * ptU.factor("length") : ptU.fromDisplay(kind, v);
const inputVal = (kind, si) => {
  if (si == null || !isFinite(si)) return "";
  if (kind === "wobble") return ptU.isIdentity("length") ? String(si) : String(+(+toD(kind, si)).toPrecision(6));
  return ptU.inputValue(kind, si);
};
const fmtK = (kind, si, d = 1) => kind === "wobble" ? (+toD(kind, si)).toPrecision(4) : ptU.fmt(kind, si, d);

/* unit-aware number input; validates in SI; commits on change only, so an
   untouched field never rewrites its stored value */
function numInput(kind, si, o = {}) {
  const i = document.createElement("input");
  i.type = "number"; i.step = "any";
  i.className = "cd-num" + (o.cls ? " " + o.cls : "");
  if (o.id) i.id = o.id;
  i.dataset.ptq = kind;
  let cur = si;
  const show = () => { i.value = cur == null ? "" : inputVal(kind, cur); };
  show();
  if (o.disabled) i.disabled = true;
  i.addEventListener("change", () => {
    const v = fromD(kind, parseFloat(i.value));
    let msg = "";
    i.classList.remove("is-bad"); i.title = o.title || "";
    if (!isFinite(v)) msg = "Enter a number";
    else if (o.int && !Number.isInteger(v)) msg = "Enter a whole number";
    else if (o.min != null && v < o.min) msg = `Must be ≥ ${inputVal(kind, o.min)}`;
    else if (o.max != null && v > o.max) msg = `Must be ≤ ${inputVal(kind, o.max)}`;
    else if (o.gt != null && !(v > o.gt)) msg = `Must be > ${inputVal(kind, o.gt)}`;
    else if (o.lt != null && !(v < o.lt)) msg = `Must be < ${inputVal(kind, o.lt)}`;
    if (msg) { show(); i.classList.add("is-bad"); i.title = msg; return; }
    cur = v;
    o.onSet && o.onSet(v);
  });
  if (o.title) i.title = o.title;
  return i;
}
function row(label, control, kind, hint) {
  const r = el("label", "cd-row");
  const l = el("span", "cd-lbl");
  l.textContent = label;
  const u = el("span", "cd-unit");
  u.textContent = kind ? kLabel(kind) : "";
  r.append(l, control, u);
  if (hint) r.appendChild(el("span", "cd-hint muted", hint));
  return r;
}

/* ================================================================ model helpers */
const tendonsOf = m => (Array.isArray(m && m.tendons) ? m.tendons : []);
const hostsOf = t => (Array.isArray(t.host) ? t.host : t.host ? [t.host] : []);
function tendonMaterials(m) {
  const own = Object.entries(m.materials || {});
  const tend = own.filter(([, x]) => x && x.material_type === "tendon").map(([n]) => n);
  const lib = ptME.DEFAULT_LIBRARY.filter(x => x.material_type === "tendon" && !(m.materials || {})[x.name]).map(x => x.name);
  const rest = own.filter(([, x]) => !x || x.material_type !== "tendon").map(([n]) => n);
  return [
    ...tend.map(n => [n, `${n} (tendon)`]),
    ...lib.map(n => [n, `${n} (library)`]),
    ...rest.map(n => [n, n]),
  ];
}
function nextTendonUid(m, taken = new Set()) {
  const used = new Set([...tendonsOf(m).map(t => t.uid), ...taken]);
  let i = 1;
  while (used.has(`T${i}`)) i++;
  return `T${i}`;
}
function uniqueName(taken, base) {
  if (!taken.has(base)) return base;
  let i = 2;
  while (taken.has(`${base}${i}`)) i++;
  return `${base}${i}`;
}
function takenCaseNames(m, except) {
  const s = new Set(ptME.allAnalysisCases(m).map(c => c.name));
  for (const n of Object.keys(m.combos || {})) s.add(n);
  s.delete(except);
  return s;
}
/** Static cases that apply at least one pattern carrying tendons. */
function ptStaticCases(m) {
  const pats = new Set(tendonsOf(m).map(t => t.pattern));
  return Object.entries(m.cases || {}).map(([n, c]) => ({ name: n, ok: Object.keys(c.patterns || {}).some(p => pats.has(p)) }));
}
const PT_DEFAULTS = { area: 0.00099, jacking_stress: 1.395e6, jacking_end: "start",
  losses: { friction_mu: 0.2, wobble_k: 0.00066, anchor_set: 0.00635, long_term_fraction: 0.15 } };

/* ================================================================ charts */
/** P(x) after losses with the anchor-set zones shaded. rep = results.tendons[uid]. */
function pxChart(rep, o = {}) {
  const W = o.w || 840, H = o.h || 250;
  const M = { l: 62, r: 18, t: 18, b: 34 };
  const pw = W - M.l - M.r, ph = H - M.t - M.b;
  const s = rep.stations.s, P = rep.stations.P;
  const L = rep.length || s[s.length - 1] || 1;
  const P0 = rep.P0;
  const pmin = Math.min(...P), pmax = Math.max(...P, P0);
  let lo = pmin - 0.12 * (pmax - pmin), hi = pmax + 0.06 * (pmax - pmin);
  if (!(hi - lo > 1e-9)) { lo = pmin * 0.9; hi = pmax * 1.1 + 1; }
  lo = Math.max(0, lo);
  const xOf = x => M.l + (x / L) * pw;
  const yOf = v => M.t + (hi - v) / (hi - lo) * ph;
  const fu = ptU.label("force"), lu = ptU.label("length");
  const fd = v => ptU.fmt("force", v, 0), ld = v => ptU.fmt("length", v, 2);
  let g = "";
  // grid + y ticks
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4, y = yOf(v);
    g += `<line class="cd-grid" x1="${M.l}" x2="${M.l + pw}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"/>` +
      `<text class="cd-ax-text" x="${M.l - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end">${fd(v)}</text>`;
  }
  for (let i = 0; i <= 5; i++) {
    const x = L * i / 5, px = xOf(x);
    g += `<text class="cd-ax-text" x="${px.toFixed(1)}" y="${M.t + ph + 14}" text-anchor="middle">${ld(x)}</text>`;
  }
  g += `<text class="cd-ax-unit" x="${M.l - 6}" y="${M.t - 6}" text-anchor="end">P, ${esc(fu)}</text>` +
    `<text class="cd-ax-unit" x="${M.l + pw}" y="${H - 4}" text-anchor="end">x along tendon, ${esc(lu)}</text>`;
  // anchor-set zones
  const as = rep.anchor_set_length || {};
  const zone = (x0, x1, label) => {
    if (!(x1 - x0 > 1e-9)) return "";
    return `<rect class="pt-set-zone" x="${xOf(x0).toFixed(1)}" y="${M.t}" width="${(xOf(x1) - xOf(x0)).toFixed(1)}" height="${ph}"/>` +
      `<text class="pt-set-lbl" x="${((xOf(x0) + xOf(x1)) / 2).toFixed(1)}" y="${M.t + 11}" text-anchor="middle">${esc(label)}</text>`;
  };
  if (as.start > 0) g += zone(0, Math.min(L, as.start), `anchor set ℓ = ${ld(as.start)} ${lu}`);
  if (as.end > 0) g += zone(Math.max(0, L - as.end), L, `anchor set ℓ = ${ld(as.end)} ${lu}`);
  // P0 line
  if (P0 <= hi) g += `<line class="pt-p0" x1="${M.l}" x2="${M.l + pw}" y1="${yOf(P0).toFixed(1)}" y2="${yOf(P0).toFixed(1)}"/>` +
    `<text class="pt-p0-lbl" x="${M.l + pw - 4}" y="${(yOf(P0) - 4).toFixed(1)}" text-anchor="end">P0 = ${fd(P0)} ${esc(fu)}</text>`;
  g += `<rect class="cd-frame" x="${M.l}" y="${M.t}" width="${pw}" height="${ph}"/>`;
  // curve
  const d = s.map((x, i) => `${i ? "L" : "M"}${xOf(x).toFixed(1)},${yOf(P[i]).toFixed(1)}`).join(" ");
  g += `<path class="pt-pline" d="${d}"/>`;
  // min / max markers
  let iMin = 0, iMax = 0;
  P.forEach((v, i) => { if (v < P[iMin]) iMin = i; if (v > P[iMax]) iMax = i; });
  for (const [i, up] of [[iMax, true], [iMin, false]]) {
    const px = xOf(s[i]), py = yOf(P[i]);
    const tx = Math.max(M.l + 40, Math.min(px, M.l + pw - 40));
    g += `<circle class="pt-pdot" cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="3"/>` +
      `<text class="pt-pval" x="${tx.toFixed(1)}" y="${(up ? py - 7 : py + 14).toFixed(1)}" text-anchor="middle">${fd(P[i])} @ ${ld(s[i])}</text>`;
  }
  const svg = `<svg class="cd-chart pt-px" viewBox="0 0 ${W} ${H}" role="img" aria-label="Tendon force along length">${g}` +
    `<line class="pt-cursor hidden" x1="0" x2="0" y1="${M.t}" y2="${M.t + ph}"/><text class="pt-cursor-lbl hidden" x="0" y="${M.t + ph - 6}"></text></svg>`;
  const wrap = el("div", "cd-preview pt-chart-wrap", svg);
  // hover readout
  const sv = wrap.querySelector("svg"), cl = sv.querySelector(".pt-cursor"), ct = sv.querySelector(".pt-cursor-lbl");
  sv.addEventListener("mousemove", e => {
    const r = sv.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width * W;
    if (px < M.l || px > M.l + pw) { cl.classList.add("hidden"); ct.classList.add("hidden"); return; }
    const x = (px - M.l) / pw * L;
    let v = P[P.length - 1];
    for (let i = 1; i < s.length; i++) if (x <= s[i]) { const t = (x - s[i - 1]) / ((s[i] - s[i - 1]) || 1); v = P[i - 1] + t * (P[i] - P[i - 1]); break; }
    cl.setAttribute("x1", px); cl.setAttribute("x2", px);
    ct.setAttribute("x", Math.min(px + 4, M.l + pw - 120)); ct.textContent = `x = ${ld(x)} ${lu} · P = ${fd(v)} ${fu}`;
    cl.classList.remove("hidden"); ct.classList.remove("hidden");
  });
  sv.addEventListener("mouseleave", () => { cl.classList.add("hidden"); ct.classList.add("hidden"); });
  return wrap;
}

/** Same chart style as the member diagrams (charts.stationDiagram), unit-aware. */
function unitDiagram(xs, vsSi, kind, title) {
  const xd = xs.map(x => ptU.toDisplay("length", x));
  const vd = vsSi.map(v => ptU.toDisplay(kind, v));
  return ptStationDiagram(xd, vd, { title, color: PT_COLOR, unit: ptU.label(kind), dec: ptU.dec(kind, 1),
    xUnit: ptU.label("length"), xDec: ptU.dec("length", 1) });
}

/** Wide diagram along a whole tendon host chain — the member-diagram style
    (filled area, stroke, zero line, min/max "value @ x" labels) plus dashed
    joints between host members. */
function chainDiagram(xs, vsSi, kind, title, joints = []) {
  const W = 860, H = 190, M = { l: 14, r: 14, t: 14, b: 22 };
  const pw = W - M.l - M.r, ph = H - M.t - M.b;
  const L = xs[xs.length - 1] || 1;
  const vs = vsSi.map(v => ptU.toDisplay(kind, v));
  let lo = Math.min(0, ...vs), hi = Math.max(0, ...vs);
  if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.14; hi += pad; lo -= pad;
  const xOf = x => M.l + (x / L) * pw, yOf = v => M.t + (hi - v) / (hi - lo) * ph;
  const dec = ptU.dec(kind, 1), xd = ptU.dec("length", 1);
  const f = (v, d) => (+v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  let g = `<line class="pt-dz" x1="${M.l}" x2="${M.l + pw}" y1="${yOf(0).toFixed(1)}" y2="${yOf(0).toFixed(1)}"/>`;
  for (const j of joints) g += `<line class="pt-dj" x1="${xOf(j).toFixed(1)}" x2="${xOf(j).toFixed(1)}" y1="${M.t}" y2="${M.t + ph}"/>`;
  let area = `M${xOf(xs[0]).toFixed(1)},${yOf(0).toFixed(1)}`, line = "";
  xs.forEach((x, i) => { const p = `${xOf(x).toFixed(1)},${yOf(vs[i]).toFixed(1)}`; area += ` L${p}`; line += `${i ? " L" : "M"}${p}`; });
  area += ` L${xOf(xs[xs.length - 1]).toFixed(1)},${yOf(0).toFixed(1)} Z`;
  g += `<path d="${area}" fill="${PT_COLOR}" fill-opacity="0.18"/><path d="${line}" fill="none" stroke="${PT_COLOR}" stroke-width="1.8" stroke-linejoin="round"/>`;
  let iMax = 0, iMin = 0;
  vs.forEach((v, i) => { if (v > vs[iMax]) iMax = i; if (v < vs[iMin]) iMin = i; });
  for (const [i, up] of [[iMax, true], [iMin, false]]) {
    if (Math.abs(vs[i]) < 1e-9 || (!up && iMin === iMax)) continue;
    const px = xOf(xs[i]), py = yOf(vs[i]);
    g += `<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="2.6" fill="${PT_COLOR}"/>` +
      `<text class="pt-dlbl" x="${Math.max(M.l + 40, Math.min(px, M.l + pw - 40)).toFixed(1)}" y="${(up ? Math.max(py - 6, 10) : Math.min(py + 13, H - 8)).toFixed(1)}" text-anchor="middle">${f(vs[i], dec)} @ ${f(ptU.toDisplay("length", xs[i]), xd)}</text>`;
  }
  g += `<text class="pt-dax" x="${M.l}" y="${H - 5}">0</text><text class="pt-dax" x="${M.l + pw}" y="${H - 5}" text-anchor="end">${f(ptU.toDisplay("length", L), xd)} ${esc(ptU.label("length"))}</text>`;
  const card = el("div", "diagram-card pt-chain-diag");
  card.innerHTML = `<div class="diagram-title"><b>${esc(title)}</b> <span class="unit">${esc(ptU.label(kind))}</span></div>` +
    `<svg viewBox="0 0 ${W} ${H}" role="img">${g}</svg>`;
  return card;
}

/* ================================================================ init */
export function initTendons(sky) {
  const S = sky.store;
  const toast = (t, m, k, ms) => sky.toast && sky.toast(t, m, k || "info", ms || 4000);
  let ver = 0;
  document.addEventListener("sky:model-changed", () => { ver++; });

  const refreshViews = () => {
    try { sky.planEditor && sky.planEditor.refresh(); } catch (e) { console.error(e); }
    try { sky.elevEditor && sky.elevEditor.refresh(); } catch (e) { console.error(e); }
    if (sky.viewer) { if (S.model) sky.viewer.setModel(S.model); sky.viewer._dirty = true; }
  };
  const commit = what => {
    ver++;
    sky.markDirty();
    refreshViews();
    try { sky.renderProps && sky.renderProps(); } catch (e) { console.error(e); }
    if (S.mode === "loads" && sky.loadsEditor) try { sky.loadsEditor.render(); } catch (e) { console.error(e); }
    if (what) toast("Tendons", what, "info", 2500);
  };
  const results = () => S.results || null;

  /* -------------------------------------------- host chain of a draft */
  const chainOf = d => d.hostKind === "shell"
    ? TG.slabChain(S.model, d.hosts[0], d.slabLine.a, d.slabLine.b)
    : TG.frameChain(S.model, d.hosts);

  function supportsOnChain(chain) {
    const m = S.model;
    const nodes = [chain.items[0].a, ...chain.items.map(it => it.b)];
    return nodes.map((p, i) => ({
      x: i ? chain.items[i - 1].x0 + chain.items[i - 1].L : 0,
      sup: (m.members || []).some(mm => mm.kind === "column" &&
        [mm.pi, mm.pj].some(q => Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]) < 1e-4)),
    }));
  }

  /* -------------------------------------------- draft <-> canonical */
  function draftFrom(t, opts = {}) {
    const m = S.model;
    const c = t ? TG.canonical(t) : null;
    const d = {
      isNew: !t, origUid: t ? t.uid : null, dirty: !t,
      uid: t ? t.uid : nextTendonUid(m),
      hostKind: "frame", hosts: [], slabLine: null,
      cps: [], segs: [], drape: c ? c.drape_dir.slice() : TG.DOWN.slice(),
      props: c ? {
        material: c.material, area: c.area, jacking_stress: c.jacking_stress,
        jacking_end: c.jacking_end, losses: { ...c.losses }, pattern: c.pattern, n_sub: c.n_sub,
      } : {
        material: TG.DEFAULT_MATERIAL, area: PT_DEFAULTS.area, jacking_stress: PT_DEFAULTS.jacking_stress,
        jacking_end: PT_DEFAULTS.jacking_end, losses: { ...PT_DEFAULTS.losses },
        pattern: defaultPattern(m), n_sub: TG.DEFAULT_N_SUB,
      },
      newPatterns: [],
      raw: t ? clone(t) : null,
    };
    const hosts = c ? c.host : (opts.hosts || []);
    d.hosts = hosts.slice();
    d.hostKind = hosts.length && hosts.every(u => (m.shells || []).some(s => s.uid === u)) ? "shell" : "frame";
    if (d.hostKind === "shell") {
      const sh = m.shells.find(s => s.uid === hosts[0]);
      if (c && c.points.length >= 2) {
        const a = c.points[0], b = c.points[c.points.length - 1];
        d.slabLine = { a: [a[0], a[1]], b: [b[0], b[1]] };
      } else d.slabLine = TG.slabDefaultLine(sh);
    }
    const chain = chainOf(d);
    d.chainError = chain.error || null;
    if (chain.error) return d;
    if (c) {
      c.points.forEach(p => {
        const pr = TG.projectToChain(chain, p, d.drape);
        d.cps.push({ x: pr.x, e: pr.e, pt: p.slice() });
      });
      c.sags.forEach(sg => d.segs.push(sg ? { type: "parabolic", sag: sg } : { type: "straight", sag: 0 }));
    } else applyPreset(d, chain, chain.items.length > 1 ? "perspan" : "parabola");
    return d;
  }
  function defaultPattern(m) {
    const pats = Object.keys(m.patterns || {});
    const pt = pats.find(p => (m.patterns[p] || {}).kind === "prestress") ||
      pats.find(p => tendonsOf(m).some(t => t.pattern === p)) || pats.find(p => /^PT/i.test(p));
    return pt || "";
  }
  function applyPreset(d, chain, kind) {
    const h = Math.min(...chain.items.map(it => it.depth || 0.5));
    const eLow = +(0.35 * h).toFixed(4), eHigh = -+(0.3 * h).toFixed(4);
    const L = chain.L;
    d.cps = []; d.segs = [];
    if (kind === "perspan" && chain.items.length > 1) {
      const xs = [0, ...chain.items.map(it => it.x0 + it.L)];
      xs.forEach((x, i) => d.cps.push({ x, e: i === 0 || i === xs.length - 1 ? 0 : eHigh, pt: null }));
      for (let k = 0; k + 1 < xs.length; k++)
        d.segs.push({ type: "parabolic", sag: eLow - (d.cps[k].e + d.cps[k + 1].e) / 2 });
    } else if (kind === "harped") {
      d.cps.push({ x: 0, e: 0, pt: null }, { x: L, e: 0, pt: null });
      d.segs.push({ type: "harped", sag: 0, harpT: 0.5, harpE: eLow });
    } else if (kind === "straight") {
      const e = +(0.25 * h).toFixed(4);
      d.cps.push({ x: 0, e, pt: null }, { x: L, e, pt: null });
      d.segs.push({ type: "straight", sag: 0 });
    } else {
      d.cps.push({ x: 0, e: 0, pt: null }, { x: L, e: 0, pt: null });
      d.segs.push({ type: "parabolic", sag: eLow });
    }
    d.dirty = true;
  }
  /** Draft → canonical tendon dict (or {error}). */
  function emit(d, chain) {
    if (!d.uid || !/^\S+$/.test(d.uid)) return { error: "Tendon name must be a non-empty word." };
    const others = tendonsOf(S.model).filter(t => t.uid !== d.origUid).map(t => t.uid);
    if (others.includes(d.uid)) return { error: `Tendon ${d.uid} already exists.` };
    if (chain.error) return { error: chain.error };
    if (d.cps.length < 2) return { error: "The profile needs at least two control points." };
    for (let k = 0; k < d.cps.length; k++) {
      const cp = d.cps[k];
      if (cp.x < -1e-9 || cp.x > chain.L + 1e-9) return { error: `Point ${k + 1}: station must lie on the host (0 … ${ptU.fmt("length", chain.L, 2)} ${ptU.label("length")}).` };
      if (k && !(cp.x > d.cps[k - 1].x + 1e-9)) return { error: `Point ${k + 1}: stations must increase along the host.` };
    }
    for (const [k, sg] of d.segs.entries())
      if (sg.type === "harped" && !(sg.harpT > 0 && sg.harpT < 1)) return { error: `Segment ${k + 1}: harp position must be between 0 and 1.` };
    const p = d.props;
    if (!p.pattern) return { error: "Pick (or create) the load pattern the tendon writes into." };
    if (!(p.area > 0)) return { error: "Tendon area must be > 0." };
    if (!(p.jacking_stress > 0)) return { error: "Jacking stress must be > 0." };
    if (!(p.losses.long_term_fraction < 1)) return { error: "Long-term loss fraction must be < 1." };
    if (!(Number.isInteger(p.n_sub) && p.n_sub >= 1 && p.n_sub <= 200)) return { error: "Sub-pieces per segment must be 1 … 200." };
    const ptOf = cp => cp.pt ? cp.pt.slice() : TG.pointAt(chain, cp.x, cp.e, d.drape);
    const points = [], sags = [];
    d.segs.forEach((sg, k) => {
      const a = d.cps[k], b = d.cps[k + 1];
      if (!points.length) points.push(ptOf(a));
      if (sg.type === "harped") {
        points.push(TG.pointAt(chain, a.x + sg.harpT * (b.x - a.x), sg.harpE, d.drape)); sags.push(0);
        points.push(ptOf(b)); sags.push(0);
      } else { points.push(ptOf(b)); sags.push(sg.type === "parabolic" ? sg.sag : 0); }
    });
    return TG.emitCanonical({
      uid: d.uid, points, sags, drape_dir: d.drape, material: p.material, area: p.area,
      jacking_stress: p.jacking_stress, jacking_end: p.jacking_end,
      losses: p.losses, host: d.hosts, pattern: p.pattern, n_sub: p.n_sub,
    });
  }
  function ensureNewPatterns(m, names, used) {
    for (const n of names) {
      if (!used.has(n) || (m.patterns || {})[n]) continue;
      m.patterns[n] = { name: n, kind: "prestress", member_loads: [], area_loads: [], story_forces: [] };
      m.cases = m.cases || {};
      if (!m.cases[n]) m.cases[n] = { name: n, patterns: { [n]: 1 }, pdelta: false, geometric: "linear" };
    }
  }
  function newPtPatternName(m, pending) {
    const taken = new Set([...Object.keys(m.patterns || {}), ...Object.keys(m.cases || {}), ...pending,
      ...ptME.allAnalysisCases(m).map(c => c.name), ...Object.keys(m.combos || {})]);
    return uniqueName(taken, "PT");
  }

  /* -------------------------------------------- pattern picker (+ New PT) */
  function patternPicker(d, onChange) {
    const wrap = el("div", "cd-with-unit pt-pat-pick");
    const draw = () => {
      wrap.textContent = "";
      const m = S.model;
      const opts = [["", "— pick a load pattern —"],
        ...Object.keys(m.patterns || {}).map(n => [n, (m.patterns[n].kind === "prestress" ? "PT · " : "") + n]),
        ...d.newPatterns.map(n => [n, `PT · ${n} (new)`])];
      const s = select(opts, d.props.pattern, v => { d.props.pattern = v; d.dirty = true; onChange && onChange(); }, "ptPattern");
      wrap.append(s, btn("New PT Pattern", "btn-small", () => {
        const n = newPtPatternName(S.model, d.newPatterns);
        d.newPatterns.push(n);
        d.props.pattern = n; d.dirty = true;
        draw(); onChange && onChange();
      }, "Create a Prestress (PT) load pattern + a linear static case of the same name on OK", "ptNewPattern"));
    };
    draw();
    return wrap;
  }

  /* -------------------------------------------- properties block (shared) */
  function propsBlock(m, p, onDirty, ids = "pt") {
    const g = el("div", "pt-props");
    const set = (k, v) => { p[k] = v; onDirty(); };
    const setL = (k, v) => { p.losses[k] = v; onDirty(); };
    const P0 = el("span", "cd-static", "");
    P0.id = ids + "P0";
    const showP0 = () => { P0.textContent = `${ptU.fmt("force", p.area * p.jacking_stress, 1)} ${ptU.label("force")}`; };
    const c1 = el("div", "pt-col");
    c1.append(
      row("Material", select(tendonMaterials(m).map(([v, l]) => [v, l]), p.material, v => set("material", v), ids + "Material")),
      row("Tendon area", numInput("area", p.area, { gt: 0, id: ids + "Area", onSet: v => { set("area", v); showP0(); } }), "area"),
      row("Jacking stress", numInput("stress", p.jacking_stress, { gt: 0, id: ids + "Fpj", onSet: v => { set("jacking_stress", v); showP0(); } }), "stress", "≈ 0.75 fpu for A416 Gr 270"),
      row("Jacking force P0", P0, null),
      row("Jacking from", select([["start", "Start (I-end)"], ["end", "End (J-end)"], ["both", "Both ends"]], p.jacking_end, v => set("jacking_end", v), ids + "JackEnd")));
    const c2 = el("div", "pt-col");
    c2.append(
      row("Curvature friction μ", numInput("none", p.losses.friction_mu, { min: 0, id: ids + "Mu", onSet: v => setL("friction_mu", v) }), null),
      row("Wobble coefficient k", numInput("wobble", p.losses.wobble_k, { min: 0, id: ids + "Wobble", onSet: v => setL("wobble_k", v) }), "wobble"),
      row("Anchor set (draw-in)", numInput("small", p.losses.anchor_set, { min: 0, id: ids + "AnchorSet", onSet: v => setL("anchor_set", v) }), "small"),
      row("Long-term loss fraction", numInput("none", p.losses.long_term_fraction, { min: 0, lt: 1, id: ids + "LongTerm", onSet: v => setL("long_term_fraction", v) }), null, "creep + shrinkage + relaxation, 0 ≤ f < 1"),
      row("Sub-pieces / segment", numInput("none", p.n_sub, { int: true, min: 1, max: 200, id: ids + "NSub", onSet: v => set("n_sub", v) }), null));
    showP0();
    g.append(c1, c2);
    return g;
  }

  /* ================================================================
     Tendon Profile dialog
     ================================================================ */
  function openTendonProfile(uid, opts = {}) {
    const m = S.model;
    if (!m) return null;
    const t = uid ? tendonsOf(m).find(x => x.uid === uid) : null;
    if (uid && !t) { toast("Tendon", `No tendon named ${uid}.`, "error"); return null; }
    const d = draftFrom(t, opts);
    if (d.chainError) { toast("Draw Tendon", d.chainError, "error", 6000); return null; }
    let chain = chainOf(d);
    let selSeg = 0;
    const body = el("div", "pt-prof");
    const err = errorLine("ptProfErr");
    const touch = () => { d.dirty = true; };

    const head = el("div", "pt-head");
    const geo = group("Profile — control points along the host", "pt-geo");
    const prev = el("div", "pt-prev-wrap");
    const pxWrap = el("div", "pt-px-wrap");
    const props = group("Tendon properties", "pt-propgrp");
    body.append(head, prev, geo, props, pxWrap, err);

    const renderHead = () => {
      head.textContent = "";
      const nm = document.createElement("input");
      nm.type = "text"; nm.id = "ptUid"; nm.value = d.uid; nm.spellcheck = false;
      nm.addEventListener("change", () => { d.uid = nm.value.trim(); touch(); });
      const hostTxt = d.hosts.map(h => `<b>${esc(h)}</b>`).join(" → ");
      const info = el("div", "pt-hostinfo muted",
        `${d.hostKind === "shell" ? "Slab" : "Host"} ${hostTxt} · length <b>${ptU.fmt("length", chain.L || 0, 2)}</b> ${esc(ptU.label("length"))}`);
      const r1 = row("Tendon name", nm, null);
      const r2 = row("Load pattern", patternPicker(d, () => { touch(); }), null, "the tendon's equivalent loads are applied in this pattern");
      head.append(r1, r2, info);
      if (d.hostKind === "shell") {
        const sl = el("div", "pt-slabline cd-inline");
        const xyIn = (key, i, lbl) => {
          const w = el("label", "pt-xy");
          w.append(el("span", "muted", lbl), numInput("length", d.slabLine[key][i], { id: `ptSlab${key}${i}`, onSet: v => {
            d.slabLine[key][i] = v; touch();
            const nc = chainOf(d);
            if (nc.error) { showError(err, nc.error); return; }
            // keep control points proportionally on the new line
            const r = nc.L / (chain.L || 1);
            d.cps.forEach(cp => { cp.x *= r; cp.pt = null; });
            chain = nc; renderAll();
          } }));
          return w;
        };
        sl.append(el("span", "cd-lbl", "Tendon line in plan"), xyIn("a", 0, "start x"), xyIn("a", 1, "y"), xyIn("b", 0, "end x"), xyIn("b", 1, "y"),
          el("span", "cd-unit", esc(ptU.label("length"))));
        head.appendChild(sl);
      }
    };

    /* ---- control-point / segment table */
    const renderGeo = () => {
      geo.querySelectorAll(":scope > :not(legend)").forEach(n => n.remove());
      const presets = el("div", "cd-inline pt-presets");
      presets.append(el("span", "muted", "Presets:"),
        btn("Parabolic per span", "btn-small", () => { applyPreset(d, chain, "perspan"); renderAll(); }, "Low point at each mid-span, high over interior supports, centroid at the anchors", "ptPresetSpan"),
        btn("Single parabola", "btn-small", () => { applyPreset(d, chain, "parabola"); renderAll(); }, "", "ptPresetPara"),
        btn("Harped", "btn-small", () => { applyPreset(d, chain, "harped"); renderAll(); }, "", "ptPresetHarp"),
        btn("Straight", "btn-small", () => { applyPreset(d, chain, "straight"); renderAll(); }, "", "ptPresetStraight"));
      geo.appendChild(presets);
      const tbl = el("table", "data-table pt-tbl");
      tbl.id = "ptSegTable";
      const lu = esc(ptU.label("length")), du = esc(ptU.label("dim"));
      tbl.innerHTML = `<thead><tr><th class="txt">#</th><th class="txt">Type</th><th>Start x <span class="unit">${lu}</span></th><th>End x <span class="unit">${lu}</span></th>` +
        `<th>e start <span class="unit">${du}</span></th><th>e end <span class="unit">${du}</span></th>` +
        `<th>Drape s <span class="unit">${du}</span></th><th>e mid / harp <span class="unit">${du}</span></th><th>harp at <span class="unit">×Lseg</span></th></tr></thead>`;
      const tb = document.createElement("tbody");
      d.segs.forEach((sg, k) => {
        const a = d.cps[k], b = d.cps[k + 1];
        const tr = document.createElement("tr");
        tr.className = "pt-seg-row" + (k === selSeg ? " is-sel" : "");
        tr.dataset.seg = k;
        const td = (child, cls = "") => { const c = el("td", cls); if (child) c.appendChild(child); tr.appendChild(c); return c; };
        td(null, "txt").textContent = String(k + 1);
        td(select([["straight", "Straight"], ["parabolic", "Parabolic"], ["harped", "Harped"]], sg.type, v => {
          if (v === sg.type) return;
          if (v === "harped") { sg.harpT = sg.harpT || 0.5; sg.harpE = isNum(sg.harpE) ? sg.harpE : (a.e + b.e) / 2 + (sg.sag || 0); }
          if (v === "parabolic" && !sg.sag) sg.sag = 0;
          sg.type = v; touch(); renderAll();
        }, `ptSegType${k}`));
        const cpX = (cp, i) => numInput("length", cp.x, { id: `ptX${i}`, cls: "pt-in", onSet: v => { cp.x = v; cp.pt = null; touch(); renderAll(); } });
        const cpE = (cp, i) => numInput("dim", cp.e, { id: `ptE${i}`, cls: "pt-in", onSet: v => { cp.e = v; cp.pt = null; touch(); renderAll(); } });
        td(k === 0 ? cpX(a, k) : el("span", "muted", ptU.fmt("length", a.x, 3)));
        td(cpX(b, k + 1));
        td(k === 0 ? cpE(a, k) : el("span", "muted", ptU.fmt("dim", a.e, 3)));
        td(cpE(b, k + 1));
        if (sg.type === "parabolic") {
          td(numInput("dim", sg.sag, { id: `ptSag${k}`, cls: "pt-in", onSet: v => { sg.sag = v; touch(); renderAll(); } }));
          td(numInput("dim", (a.e + b.e) / 2 + sg.sag, { id: `ptEmid${k}`, cls: "pt-in", title: "eccentricity at mid-chord (drape measured from the chord)",
            onSet: v => { sg.sag = v - (a.e + b.e) / 2; touch(); renderAll(); } }));
          td(el("span", "muted", "—"));
        } else if (sg.type === "harped") {
          td(el("span", "muted", "0"));
          td(numInput("dim", sg.harpE, { id: `ptHarpE${k}`, cls: "pt-in", onSet: v => { sg.harpE = v; touch(); renderAll(); } }));
          td(numInput("none", sg.harpT, { id: `ptHarpT${k}`, cls: "pt-in", gt: 0, lt: 1, onSet: v => { sg.harpT = v; touch(); renderAll(); } }));
        } else { td(el("span", "muted", "0")); td(el("span", "muted", "—")); td(el("span", "muted", "—")); }
        tr.addEventListener("click", e => { if (e.target.closest("input,select")) return; selSeg = k; renderGeo(); });
        tb.appendChild(tr);
      });
      tbl.appendChild(tb);
      const tw = el("div", "table-scroll pt-tbl-wrap");
      tw.appendChild(tbl);
      geo.appendChild(tw);
      const acts = el("div", "cd-inline pt-seg-acts");
      acts.append(
        btn("Split segment", "btn-small", () => splitSeg(selSeg), "Insert a control point at the middle of the selected segment", "ptSplit"),
        btn("Merge with next", "btn-small", () => mergeSeg(selSeg), "Remove the control point after the selected segment", "ptMerge"),
        el("span", "muted pt-conv", `e = eccentricity below the centroid (${esc(ptU.label("dim"))}, negative = above); drape s = parabola sag from the chord at mid-segment.`));
      geo.appendChild(acts);
    };
    const eAtX = x => {
      const samp = profileXE(d, chain, 40);
      for (let i = 1; i < samp.length; i++) if (x <= samp[i].x) {
        const t = (x - samp[i - 1].x) / ((samp[i].x - samp[i - 1].x) || 1);
        return samp[i - 1].e + t * (samp[i].e - samp[i - 1].e);
      }
      return samp.length ? samp[samp.length - 1].e : 0;
    };
    function splitSeg(k) {
      const sg = d.segs[k];
      if (!sg) return;
      const a = d.cps[k], b = d.cps[k + 1];
      if (sg.type === "harped") {
        const hx = a.x + sg.harpT * (b.x - a.x);
        d.cps.splice(k + 1, 0, { x: hx, e: sg.harpE, pt: null });
        d.segs.splice(k, 1, { type: "straight", sag: 0 }, { type: "straight", sag: 0 });
      } else {
        const mx = (a.x + b.x) / 2;
        d.cps.splice(k + 1, 0, { x: mx, e: eAtX(mx), pt: null });
        const half = sg.type === "parabolic" ? sg.sag / 4 : 0;
        d.segs.splice(k, 1, { type: sg.type, sag: half }, { type: sg.type, sag: half });
      }
      touch(); renderAll();
    }
    function mergeSeg(k) {
      if (k + 1 >= d.segs.length) { showError(err, "Select a segment that has a following segment to merge."); return; }
      const a = d.cps[k], c = d.cps[k + 2];
      const mx = (a.x + c.x) / 2;
      const em = eAtX(mx);
      const both = [d.segs[k], d.segs[k + 1]];
      const curved = both.some(s => s.type !== "straight");
      d.cps.splice(k + 1, 1);
      d.segs.splice(k, 2, curved ? { type: "parabolic", sag: em - (a.e + c.e) / 2 } : { type: "straight", sag: 0 });
      selSeg = Math.min(selSeg, d.segs.length - 1);
      touch(); renderAll();
    }

    /* ---- elevation preview (tendon inside the member depth) */
    // previews validate geometry + properties only (the pattern may still be unset)
    const emitPreview = () => emit({ ...d, uid: "__preview__", origUid: "__preview__", props: { ...d.props, pattern: d.props.pattern || "_" } }, chain);
    const renderPreview = () => {
      prev.textContent = "";
      const em = emitPreview();
      prev.appendChild(profileSvg(d, chain, em.error ? null : em));
    };
    const renderPx = () => {
      pxWrap.textContent = "";
      const em = emitPreview();
      if (em.error) { pxWrap.appendChild(el("p", "muted cd-note", esc(em.error))); return; }
      let rep;
      try { rep = TG.tendonReport(TG.canonical(em), TG.materialE(S.model, em.material)); } catch (e) { return; }
      pxWrap.append(el("div", "dlg-group-title", `Force after losses (preview) · P0 = ${esc(ptU.fmtU("force", rep.P0, 1))} · min ${esc(ptU.fmtU("force", Math.min(...rep.stations.P), 1))}`),
        pxChart(rep, { w: 860, h: 190 }));
    };
    const renderProps = () => {
      props.querySelectorAll(":scope > :not(legend)").forEach(n => n.remove());
      props.appendChild(propsBlock(S.model, d.props, () => { touch(); renderPx(); }, "ptp"));
    };
    function renderAll() {
      if (settle(body)) return;   // a pending edit committed + re-rendered already
      showError(err, ""); renderHead(); renderGeo(); renderPreview(); renderProps(); renderPx();
    }

    const ok = () => {
      const em = emit(d, chain);
      if (em.error) { showError(err, em.error); return false; }
      if (!d.dirty && !d.isNew) return true;
      const mm = S.model;
      ensureNewPatterns(mm, d.newPatterns, new Set([em.pattern]));
      if (!(mm.patterns || {})[em.pattern]) { showError(err, `Unknown load pattern ${em.pattern}.`); return false; }
      const list = tendonsOf(mm).slice();
      const i = list.findIndex(x => x.uid === d.origUid);
      if (i >= 0 && !d.isNew) list[i] = em; else list.push(em);
      mm.tendons = list;
      d.isNew = false; d.origUid = em.uid; d.dirty = false;
      commit(`${em.uid} saved (${em.points.length - 1} segment${em.points.length > 2 ? "s" : ""}, pattern ${em.pattern})`);
      opts.onChange && opts.onChange();
      return true;
    };
    const fb = footBar(d.isNew ? "New tendon" : `Editing ${d.origUid}`, [
      btn("Cancel", "", () => dlg.close(), "", "ptProfCancel"),
      btn("Apply", "", () => { ok(); }, "", "ptProfApply"),
      btn("OK", "btn-run", () => { if (ok()) dlg.close(); }, "", "ptProfOk"),
    ]);
    const dlg = dialog("ptProfileDlg", { title: `Tendon Profile — ${d.isNew ? "New" : d.origUid}`, wide: true, body, foot: fb.wrap, onUnits: renderAll });
    renderAll();
    return dlg;
  }

  /** (x, e) samples of the drafted profile on the chain (for preview / split). */
  function profileXE(d, chain, perSeg = 24) {
    const ptOf = cp => cp.pt || TG.pointAt(chain, cp.x, cp.e, d.drape);
    const out = [];
    d.segs.forEach((sg, k) => {
      const a = d.cps[k], b = d.cps[k + 1];
      if (!a || !b) return;
      if (sg.type === "harped") {
        const h = { x: a.x + sg.harpT * (b.x - a.x), e: sg.harpE };
        if (!out.length) out.push({ x: a.x, e: a.e });
        out.push(h, { x: b.x, e: b.e });
        return;
      }
      const g = TG.segments({ points: [ptOf(a), ptOf(b)], sags: [sg.type === "parabolic" ? sg.sag : 0], drape_dir: d.drape })[0];
      const n = sg.type === "parabolic" ? perSeg : 1;
      for (let i = out.length ? 1 : 0; i <= n; i++) {
        const pr = TG.projectToChain(chain, TG.segP(g, i / n), d.drape);
        out.push({ x: pr.x, e: pr.e });
      }
    });
    return out;
  }

  function profileSvg(d, chain, em) {
    const W = 860, H = 210;
    const M = { l: 46, r: 16, t: 20, b: 40 };
    const pw = W - M.l - M.r, bh = H - M.t - M.b;
    const L = chain.L || 1;
    const hmax = Math.max(...chain.items.map(it => it.depth || 0.5));
    const samp = profileXE(d, chain);
    const emax = Math.max(hmax / 2, ...samp.map(s => Math.abs(s.e))) * 1.08;
    const xOf = x => M.l + x / L * pw;
    const yOf = e => M.t + bh / 2 + e / emax * (bh / 2);
    const exag = (bh / 2 / emax) / (pw / L);
    let g = "";
    for (const it of chain.items) {
      const h2 = (it.depth || 0.5) / 2;
      g += `<rect class="pt-mem" x="${xOf(it.x0).toFixed(1)}" y="${yOf(-h2).toFixed(1)}" width="${(xOf(it.x0 + it.L) - xOf(it.x0)).toFixed(1)}" height="${(yOf(h2) - yOf(-h2)).toFixed(1)}"/>` +
        `<text class="pt-mem-lbl" x="${((xOf(it.x0) + xOf(it.x0 + it.L)) / 2).toFixed(1)}" y="${(yOf(-h2) - 5).toFixed(1)}" text-anchor="middle">${esc(it.uid)}</text>`;
    }
    g += `<line class="pt-centroid" x1="${M.l}" x2="${M.l + pw}" y1="${yOf(0).toFixed(1)}" y2="${yOf(0).toFixed(1)}"/>`;
    if (chain.kind === "frame") for (const s of supportsOnChain(chain)) {
      if (!s.sup) continue;
      const x = xOf(s.x), y = yOf(hmax / 2) + 2;
      g += `<path class="pt-sup" d="M${x.toFixed(1)},${y.toFixed(1)} l-7,11 h14 z"/>`;
    }
    const path = samp.map((s, i) => `${i ? "L" : "M"}${xOf(s.x).toFixed(1)},${yOf(s.e).toFixed(1)}`).join(" ");
    g += `<path class="pt-tendon" d="${path}"/>`;
    // anchors
    if (samp.length) for (const s of [samp[0], samp[samp.length - 1]])
      g += `<rect class="pt-anchor" x="${(xOf(s.x) - 3).toFixed(1)}" y="${(yOf(s.e) - 6).toFixed(1)}" width="6" height="12"/>`;
    // control points + labels
    d.cps.forEach((cp, i) => {
      const x = xOf(cp.x), y = yOf(cp.e);
      g += `<circle class="pt-cp" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.6"><title>Point ${i + 1}: x = ${esc(ptU.fmtU("length", cp.x, 3))}, e = ${esc(ptU.fmtU("dim", cp.e, 1))}</title></circle>` +
        `<text class="pt-cp-lbl" x="${x.toFixed(1)}" y="${(cp.e >= 0 ? y + 15 : y - 8).toFixed(1)}" text-anchor="middle">${esc(ptU.fmt("dim", cp.e, 3))}</text>` +
        `<text class="cd-ax-text" x="${x.toFixed(1)}" y="${H - 22}" text-anchor="middle">${esc(ptU.fmt("length", cp.x, 2))}</text>`;
    });
    d.segs.forEach((sg, k) => {
      const a = d.cps[k], b = d.cps[k + 1];
      if (sg.type === "harped") {
        const x = xOf(a.x + sg.harpT * (b.x - a.x)), y = yOf(sg.harpE);
        g += `<rect class="pt-cp" x="${(x - 3.5).toFixed(1)}" y="${(y - 3.5).toFixed(1)}" width="7" height="7"/>` +
          `<text class="pt-cp-lbl" x="${x.toFixed(1)}" y="${(y + 15).toFixed(1)}" text-anchor="middle">${esc(ptU.fmt("dim", sg.harpE, 3))}</text>`;
      }
    });
    g += `<text class="cd-ax-unit" x="${M.l}" y="${H - 6}">x, ${esc(ptU.label("length"))} · e, ${esc(ptU.label("dim"))} (+ below centroid)</text>` +
      `<text class="cd-ax-unit" x="${M.l + pw}" y="${H - 6}" text-anchor="end">vertical scale ×${exag >= 10 ? Math.round(exag) : exag.toFixed(1)}${em ? "" : " · invalid profile"}</text>`;
    const wrap = el("div", "cd-preview pt-prev");
    wrap.innerHTML = `<svg class="cd-chart" id="ptPreviewSvg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Tendon profile elevation">${g}</svg>`;
    return wrap;
  }

  /* ================================================================
     Draw > Draw Tendon (pick hosts)
     ================================================================ */
  let pickBar = null;
  function hostsFromSelection() {
    const so = sky.selObjects ? sky.selObjects() : { members: [], shells: [] };
    const beams = (so.members || []).filter(x => x.kind !== "column");
    if (beams.length) return { hosts: beams.map(x => x.uid) };
    const slabs = (so.shells || []).filter(x => x.kind === "slab");
    if (slabs.length === 1) return { hosts: [slabs[0].uid] };
    if (slabs.length > 1) return { error: "Pick ONE slab (a slab tendon runs in a straight line through one region)." };
    return null;
  }
  function closePick() { if (pickBar) { pickBar.remove(); pickBar = null; } }
  function openDrawTendon() {
    if (!S.model) return;
    const h = hostsFromSelection();
    if (h && h.error) { toast("Draw Tendon", h.error, "error", 5000); return; }
    if (h) { closePick(); return openTendonProfile(null, { hosts: h.hosts }); }
    sky.setMode && sky.setMode("model");
    sky.setTool && sky.setTool("select");
    closePick();
    pickBar = el("div", "pt-pickbar");
    pickBar.id = "ptPickBar";
    pickBar.innerHTML = `<span class="pt-pick-ico">${glyph()}</span><span><b>Draw Tendon</b> — select the host beam(s) end to end, or one slab, then press <b>Continue</b>.</span>`;
    pickBar.append(btn("Continue", "btn-small btn-run", () => {
      const hh = hostsFromSelection();
      if (!hh) { toast("Draw Tendon", "Nothing selected — click the host beams (Shift adds) or a slab.", "error"); return; }
      if (hh.error) { toast("Draw Tendon", hh.error, "error", 5000); return; }
      closePick();
      openTendonProfile(null, { hosts: hh.hosts });
    }, "", "ptPickGo"), btn("Cancel", "btn-small", closePick, "", "ptPickCancel"));
    (document.getElementById("etabsCanvas") || document.body).appendChild(pickBar);
  }

  /* ================================================================
     Define > Tendon Properties…
     ================================================================ */
  function openTendonProperties(opts = {}) {
    const m = S.model;
    if (!m) return null;
    const orig = clone(tendonsOf(m));
    const draft = clone(orig);
    const newPatterns = [];
    let sel = opts.select || (draft[0] && draft[0].uid) || null;
    const body = el("div", "pt-propsdlg");
    const err = errorLine("ptPropsErr");
    const changed = () => JSON.stringify(draft) !== JSON.stringify(orig) || newPatterns.length;
    const refresh = () => {
      if (settle(body)) return;
      body.textContent = "";
      const grid = el("div", "cd-fn-grid pt-list-grid");
      const left = el("div", "");
      const list = el("div", "cd-list");
      list.id = "ptList";
      if (!draft.length) list.appendChild(el("p", "muted cd-empty", "No tendons yet — use <b>Draw → Draw Tendon</b>."));
      for (const t of draft) {
        const b = el("button", "cd-list-item" + (t.uid === sel ? " is-sel" : ""),
          `<span class="pt-li-dash"></span><span>${esc(t.uid)}</span><span class="cd-badge pt-badge">${esc(t.pattern || "?")}</span>`);
        b.dataset.uid = t.uid;
        b.addEventListener("click", () => { sel = t.uid; refresh(); });
        b.addEventListener("dblclick", () => { sel = t.uid; modifyProfile(); });
        list.appendChild(b);
      }
      const acts = el("div", "cd-list-acts");
      acts.append(btn("Draw New Tendon…", "btn-small", () => { dlg.close(); openDrawTendon(); }, "", "ptListNew"),
        btn("Modify Profile…", "btn-small", modifyProfile, "", "ptListProfile"),
        btn("Delete", "btn-small", del, "", "ptListDelete"));
      left.append(list, acts);
      const right = el("div", "pt-right");
      const t = draft.find(x => x.uid === sel);
      if (t) {
        const c = TG.canonical(t);
        // write-through: only the field the user changed is written to the draft
        const lp = new Proxy({ ...c.losses }, { set(o, k, v) {
          o[k] = v;
          if (t.losses && typeof t.losses === "object") t.losses[k] = v; else t.losses = { [k]: v };
          return true;
        } });
        const p = { material: c.material, area: c.area, jacking_stress: c.jacking_stress, jacking_end: c.jacking_end, losses: lp, n_sub: c.n_sub };
        const proxy = new Proxy(p, { set(o, k, v) { o[k] = v; t[k] = v; return true; } });
        const g = group(`Tendon ${t.uid}`);
        const nm = document.createElement("input");
        nm.type = "text"; nm.id = "ptPropUid"; nm.value = t.uid;
        nm.addEventListener("change", () => {
          const nu = nm.value.trim();
          if (!nu || draft.some(x => x !== t && x.uid === nu)) { nm.value = t.uid; showError(err, "Name empty or already used."); return; }
          t.uid = nu; sel = nu; refresh();
        });
        const pd = { props: { pattern: t.pattern }, newPatterns, dirty: false };
        const pick = patternPicker(pd, () => { t.pattern = pd.props.pattern; refresh(); });
        const segN = c.points.length - 1, curved = c.sags.filter(x => x).length;
        g.append(row("Name", nm, null), row("Load pattern", pick, null),
          el("div", "pt-hostinfo muted", `Host <b>${hostsOf(t).map(esc).join(" → ")}</b> · ${segN} segment${segN > 1 ? "s" : ""} (${curved} parabolic) · drape ${esc(JSON.stringify(c.drape_dir))}`));
        g.appendChild(propsBlock(S.model, proxy, () => {}, "ptd"));
        right.appendChild(g);
        try {
          const rep = TG.tendonReport(c, TG.materialE(S.model, c.material));
          right.append(el("div", "dlg-group-title", "Force after losses (estimate)"), pxChart(rep, { w: 600, h: 170 }));
        } catch (e) { /* invalid geometry */ }
      } else right.appendChild(el("p", "muted cd-empty", "Select a tendon."));
      grid.append(left, right);
      body.append(grid, err);
      fb.note.textContent = `${draft.length} tendon${draft.length === 1 ? "" : "s"}`;
    };
    function validateDraft() {
      for (const t of draft) {
        if (!(t.area > 0)) return `${t.uid}: area must be > 0`;
        if (!(t.jacking_stress > 0)) return `${t.uid}: jacking stress must be > 0`;
        if (!t.pattern) return `${t.uid}: pick a load pattern`;
      }
      const pats = new Set(draft.map(t => t.pattern));
      for (const [n, h] of Object.entries(m.hyperstatic_cases || {})) {
        const c = (m.cases || {})[h.case];
        if (!c || !Object.keys(c.patterns || {}).some(p => pats.has(p)))
          return `Hyperstatic case ${n} needs a tendon in a pattern of static case ${h.case} — delete or edit the case first.`;
      }
      return null;
    }
    function apply() {
      if (!changed()) return true;
      const e = validateDraft();
      if (e) { showError(err, e); return false; }
      ensureNewPatterns(m, newPatterns, new Set(draft.map(t => t.pattern)));
      if (draft.length) m.tendons = clone(draft); else delete m.tendons;
      orig.length = 0; orig.push(...clone(draft)); newPatterns.length = 0;
      commit(`${draft.length} tendon${draft.length === 1 ? "" : "s"} updated`);
      return true;
    }
    function modifyProfile() {
      if (!sel) return;
      if (!apply()) return;
      const u = sel;
      dlg.close();
      openTendonProfile(u, { onChange: () => {} });
    }
    function del() {
      const i = draft.findIndex(x => x.uid === sel);
      if (i < 0) return;
      draft.splice(i, 1);
      sel = draft[0] ? draft[0].uid : null;
      refresh();
    }
    const fb = footBar("", [
      btn("Cancel", "", () => dlg.close(), "", "ptPropsCancel"),
      btn("OK", "btn-run", () => { if (apply()) dlg.close(); }, "", "ptPropsOk"),
    ]);
    const dlg = dialog("ptPropsDlg", { title: "Tendon Properties", wide: true, body, foot: fb.wrap, onUnits: refresh });
    refresh();
    return dlg;
  }

  /* ================================================================
     Hyperstatic load case (Define > Load Cases > Hyperstatic)
     ================================================================ */
  function openHyperstaticCase(name, opts = {}) {
    const m = S.model;
    if (!m) return null;
    const isNew = !name || !(m.hyperstatic_cases || {})[name];
    const cands = ptStaticCases(m);
    const draft = {
      name: isNew ? uniqueName(takenCaseNames(m), "HYPER") : name,
      case: isNew ? ((cands.find(c => c.ok) || {}).name || "") : m.hyperstatic_cases[name].case,
    };
    const body = el("div", "pt-hyp");
    const err = errorLine("ptHypErr");
    const nm = document.createElement("input");
    nm.type = "text"; nm.id = "ptHypName"; nm.value = draft.name; nm.spellcheck = false;
    nm.addEventListener("change", () => { draft.name = nm.value.trim(); });
    const sc = select([["", "— pick a static case —"], ...cands.map(c => [c.name, c.ok ? `${c.name} (applies tendons)` : `${c.name} — no tendon pattern`, !c.ok])],
      draft.case, v => { draft.case = v; }, "ptHypCase");
    const g = group("Hyperstatic Load Case Data");
    g.append(row("Load case name", nm, null), row("Load case type", el("span", "cd-static", "Hyperstatic"), null),
      row("Static case with tendons", sc, null),
      el("p", "muted cd-note", "The hyperstatic (secondary) effects of post-tensioning: <b>secondary = total − primary</b>, where the " +
        "primary forces are P·e of the tendon on the concrete section alone. The static case runs as a dependency. " +
        "Results: <b>Display → Hyperstatic Results…</b>"));
    if (!cands.some(c => c.ok)) g.appendChild(el("p", "cd-note cd-warn", "No static case applies a tendon pattern yet — draw a tendon and give its pattern a static case first."));
    body.append(g, err);
    const ok = () => {
      const n = draft.name;
      if (!n) { showError(err, "Name the case."); return false; }
      if (takenCaseNames(m, isNew ? undefined : name).has(n)) { showError(err, `${n} is already used by another case or combination.`); return false; }
      const c = cands.find(x => x.name === draft.case);
      if (!c) { showError(err, "Pick the static case that contains the tendons."); return false; }
      if (!c.ok) { showError(err, `Static case ${c.name} applies no tendon load pattern.`); return false; }
      if (!isNew && n === name && m.hyperstatic_cases[name].case === draft.case) return true;   // unchanged
      m.hyperstatic_cases = m.hyperstatic_cases || {};
      if (!isNew && n !== name) {
        delete m.hyperstatic_cases[name];
        if (Array.isArray(m.cases_not_run)) m.cases_not_run = m.cases_not_run.map(x => x === name ? n : x);
      }
      m.hyperstatic_cases[n] = { case: draft.case };
      commit(`Hyperstatic case ${n} → ${draft.case}`);
      opts.onChange && opts.onChange("cases");
      return true;
    };
    const fb = footBar(isNew ? "New hyperstatic case" : `Editing ${name}`, [
      btn("Cancel", "", () => dlg.close(), "", "ptHypCancel"),
      btn("OK", "btn-run", () => { if (ok()) dlg.close(); }, "", "ptHypOk"),
    ]);
    const dlg = dialog("ptHypDlg", { title: "Load Case Data — Hyperstatic", body, foot: fb.wrap });
    return dlg;
  }
  function deleteHyperstaticCase(name) {
    const m = S.model;
    if (!m || !(m.hyperstatic_cases || {})[name]) return false;
    delete m.hyperstatic_cases[name];
    if (!Object.keys(m.hyperstatic_cases).length) delete m.hyperstatic_cases;
    if (Array.isArray(m.cases_not_run)) m.cases_not_run = m.cases_not_run.filter(x => x !== name);
    ver++;
    return true;
  }

  /* ================================================================
     Display > Tendon Forces…
     ================================================================ */
  function tendonRep(uid) {
    const r = results();
    if (r && r.tendons && r.tendons[uid]) return { rep: r.tendons[uid], live: true };
    const t = tendonsOf(S.model).find(x => x.uid === uid);
    if (!t) return null;
    try { const c = TG.canonical(t); return { rep: TG.tendonReport(c, TG.materialE(S.model, c.material)), live: false }; }
    catch (e) { return null; }
  }
  function openTendonForces(uid) {
    const m = S.model;
    if (!m) return null;
    const r = results();
    const uids = [...new Set([...tendonsOf(m).map(t => t.uid), ...Object.keys((r && r.tendons) || {})])];
    if (!uids.length) { toast("Tendon Forces", "No tendons in the model — Draw → Draw Tendon first.", "info", 5000); return null; }
    let cur = uid && uids.includes(uid) ? uid : uids[0];
    const body = el("div", "pt-forces");
    const draw = () => {
      body.textContent = "";
      const ctl = el("div", "cd-inline pt-ctl");
      ctl.append(row("Tendon", select(uids.map(u => [u, u]), cur, v => { cur = v; draw(); }, "ptForcesSel"), null));
      const got = tendonRep(cur);
      if (!got) { body.append(ctl, el("p", "muted", "No data.")); return; }
      const { rep, live } = got;
      ctl.appendChild(el("span", "cd-badge" + (live ? "" : " pt-est"), live ? "analysis results" : "client estimate — run the analysis"));
      body.appendChild(ctl);
      body.appendChild(pxChart(rep));
      const P = rep.stations.P;
      const pmin = Math.min(...P), pmax = Math.max(...P);
      const pavg = P.reduce((a, b) => a + b, 0) / P.length;
      const fu = "force";
      const cards = el("div", "pt-cards");
      const kv = (k, v) => `<div class="pt-kv"><span>${k}</span><b>${v}</b></div>`;
      const as = rep.anchor_set_length || {};
      cards.appendChild(el("div", "pt-card", `<div class="dlg-group-title">Losses</div>` +
        kv("Pattern", esc(rep.pattern)) +
        kv("Jacking force P0", esc(ptU.fmtU(fu, rep.P0, 1))) +
        kv("Tendon length", esc(ptU.fmtU("length", rep.length, 3))) +
        kv("P max / min", `${esc(ptU.fmt(fu, pmax, 1))} / ${esc(ptU.fmtU(fu, pmin, 1))}`) +
        kv("P average", esc(ptU.fmtU(fu, pavg, 1))) +
        kv("Average loss", `${((1 - pavg / rep.P0) * 100).toFixed(1)} %`) +
        kv("Anchor set length", `start ${esc(ptU.fmt("length", as.start || 0, 2))} · end ${esc(ptU.fmtU("length", as.end || 0, 2))}`)));
      const eq = rep.equivalent_loads || { points: [], lines: [], net_force: [0, 0, 0], net_moment: [0, 0, 0] };
      const pts = eq.points || [];
      const fmtV = (kind, v) => `[${v.map(x => ptU.fmt(kind, x, 2)).join(", ")}]`;
      const anchors = pts.length ? [pts[0], pts[pts.length - 1]] : [];
      const nf = eq.net_force || [0, 0, 0], nmo = eq.net_moment || [0, 0, 0];
      cards.appendChild(el("div", "pt-card", `<div class="dlg-group-title">Equivalent loads</div>` +
        kv("Anchor force (start)", anchors[0] ? `${esc(fmtV(fu, anchors[0].force))} ${esc(ptU.label(fu))}` : "—") +
        kv("Anchor force (end)", anchors[1] ? `${esc(fmtV(fu, anchors[1].force))} ${esc(ptU.label(fu))}` : "—") +
        kv("Point loads (anchors, kinks, friction steps)", String(pts.length)) +
        kv("Distributed pieces", String((eq.lines || []).length)) +
        kv("Net force", `${esc(fmtV(fu, nf))} ${esc(ptU.label(fu))}`) +
        kv("Net moment", `${esc(fmtV("moment", nmo))} ${esc(ptU.label("moment"))}`) +
        `<p class="muted cd-note">Self-equilibrated: the net force and moment vanish (to round-off).</p>`));
      body.appendChild(cards);
      // segment uplift table
      const up = rep.segment_uplift || [];
      if (up.length) {
        const tw = el("div", "table-scroll");
        const lf = esc(ptU.label("line_force"));
        tw.innerHTML = `<table class="data-table cd-res-table" id="ptUpliftTable"><thead><tr><th class="txt">Segment</th><th>Uplift w = 8·P·s/Lc² <span class="unit">${lf}</span></th><th class="txt">Shape</th></tr></thead><tbody>` +
          up.map((w, k) => `<tr><td class="txt">${k + 1}</td><td>${esc(ptU.fmt("line_force", w, 2))}</td><td class="txt dim">${Math.abs(w) > 1e-12 ? "parabolic" : "straight"}</td></tr>`).join("") +
          `</tbody></table>`;
        body.append(el("div", "dlg-group-title", "Balanced load per segment"), tw);
      }
    };
    const fb = footBar("P(x) after friction, wobble, anchor set and long-term losses", [btn("Close", "btn-run", () => dlg.close(), "", "ptForcesClose")]);
    const dlg = dialog("ptForcesDlg", { title: "Tendon Forces", wide: true, body, foot: fb.wrap, onUnits: draw });
    draw();
    return dlg;
  }

  /* ================================================================
     Display > Hyperstatic Results…
     ================================================================ */
  function openHyperstaticResults(name) {
    const m = S.model;
    if (!m) return null;
    const hy = (results() && results().hyperstatic) || {};
    const names = Object.keys(hy);
    const body = el("div", "pt-hres");
    if (!names.length) {
      body.appendChild(el("p", "muted cd-empty", Object.keys(m.hyperstatic_cases || {}).length
        ? "No hyperstatic results yet — run the analysis (Analyze → Run Analysis)."
        : "No hyperstatic case defined — Define → Load Cases… → add a <b>Hyperstatic</b> case on the static case that applies the tendons, then run."));
      const fb0 = footBar("", [btn("Close", "btn-run", () => d0.close(), "", "ptHresClose")]);
      const d0 = dialog("ptHresDlg", { title: "Hyperstatic Results", wide: true, body, foot: fb0.wrap });
      return d0;
    }
    let cur = name && hy[name] ? name : names[0];
    let comp = "M3", part = "secondary", showAll = false;
    const draw = () => {
      body.textContent = "";
      const h = hy[cur];
      const ctl = el("div", "cd-inline pt-ctl");
      const seg = el("div", "seg-toggle pt-part");
      seg.id = "ptPartToggle";
      for (const [k, l] of [["primary", "Primary"], ["secondary", "Secondary"], ["total", "Total"]]) {
        const b = el("button", "seg-btn" + (k === part ? " is-active" : ""), l);
        b.dataset.part = k;
        b.addEventListener("click", () => { part = k; draw(); });
        seg.appendChild(b);
      }
      ctl.append(row("Hyperstatic case", select(names.map(n => [n, n]), cur, v => { cur = v; draw(); }, "ptHresCase"), null),
        row("Component", select(Object.keys(COMP_KIND).map(k => [k, k]), comp, v => { comp = v; draw(); }, "ptHresComp"), null), seg);
      body.appendChild(ctl);
      body.appendChild(el("p", "muted cd-note", `Static case <b>${esc(h.case)}</b> · tendon patterns ${Object.entries(h.tendon_patterns || {}).map(([p, s]) => `<b>${esc(p)}</b> ×${s}`).join(", ") || "—"} · ` +
        "secondary = total − primary (primary = P·e on the concrete section alone)."));
      const kind = COMP_KIND[comp];
      // along each tendon host chain
      const tds = tendonsOf(m).filter(t => (h.tendon_patterns || {})[t.pattern] != null);
      const chainBox = el("div", "pt-diagrams");
      for (const t of tds) {
        const hosts = hostsOf(t);
        if (!hosts.every(u => h.members[u])) continue;
        const ch = TG.frameChain(m, hosts);
        if (ch.error) continue;
        const xs = [], vs = [];
        for (const it of ch.items) {
          const blk = h.members[it.uid];
          const arr = (blk[part] || {})[comp] || [];
          const mm = (m.members || []).find(x => x.uid === it.uid);
          const rev = mm && Math.hypot(...mm.pi.map((v, i) => v - it.a[i])) > 1e-6;
          const L = blk.x[blk.x.length - 1] || it.L;
          blk.x.forEach((x, i) => {
            const j = rev ? blk.x.length - 1 - i : i;
            xs.push(it.x0 + (rev ? L - blk.x[j] : x) * (it.L / (L || 1)));
            vs.push(arr[j] || 0);
          });
        }
        if (xs.length)
          chainBox.appendChild(chainDiagram(xs, vs, kind, `${t.uid} · ${comp} ${part} along ${ch.items.map(i => i.uid).join(" → ")}`,
            ch.items.slice(1).map(i => i.x0)));
      }
      if (chainBox.children.length) body.append(el("div", "dlg-group-title", "Along the tendon"), chainBox);
      // per member
      const mags = Object.entries(h.members || {}).map(([u, blk]) => {
        const arr = (blk[part] || {})[comp] || [];
        return { u, blk, mx: Math.max(0, ...arr.map(Math.abs)) };
      });
      const top = Math.max(0, ...mags.map(x => x.mx));
      let shown = mags.filter(x => x.blk.hosts_tendon);
      const extra = mags.filter(x => !x.blk.hosts_tendon && x.mx > 1e-6 * (top || 1)).sort((a, b) => b.mx - a.mx);
      if (showAll) shown = shown.concat(extra.slice(0, 24));
      const grid = el("div", "pt-diagrams pt-mem-grid");
      for (const x of shown) grid.appendChild(unitDiagram(x.blk.x, (x.blk[part] || {})[comp] || x.blk.x.map(() => 0), kind, `${x.u}${x.blk.hosts_tendon ? " · hosts tendon" : ""}`));
      const mt = el("div", "cd-inline");
      mt.appendChild(el("div", "dlg-group-title", `Member diagrams — ${comp} ${part}`));
      if (extra.length) {
        const cb = btn(showAll ? "Tendon hosts only" : `+ ${Math.min(extra.length, 24)} other member${extra.length > 1 ? "s" : ""} with ${part} forces`, "btn-small", () => { showAll = !showAll; draw(); }, "", "ptHresAll");
        mt.appendChild(cb);
      }
      body.append(mt, grid);
      // secondary reactions
      const nodes = (results() && results().nodes) || {};
      const R = Object.entries(h.reactions || {});
      const fu = esc(ptU.label("force")), mu = esc(ptU.label("moment")), lu = esc(ptU.label("length"));
      const sum = [0, 0, 0, 0, 0, 0];
      const rows = R.map(([tag, v]) => {
        v.forEach((x, i) => { sum[i] += x; });
        const p = nodes[tag] || [];
        return `<tr><td class="txt">${esc(tag)}</td>${[0, 1, 2].map(i => `<td>${p[i] != null ? esc(ptU.fmt("length", p[i], 2)) : "—"}</td>`).join("")}` +
          v.map((x, i) => `<td>${esc(ptU.fmt(i < 3 ? "force" : "moment", x, 2))}</td>`).join("") + `</tr>`;
      }).join("");
      const tw = el("div", "table-scroll pt-react-wrap");
      tw.innerHTML = `<table class="data-table cd-res-table" id="ptReactTable"><thead><tr><th class="txt">Node</th><th>X <span class="unit">${lu}</span></th><th>Y <span class="unit">${lu}</span></th><th>Z <span class="unit">${lu}</span></th>` +
        `<th>FX <span class="unit">${fu}</span></th><th>FY <span class="unit">${fu}</span></th><th>FZ <span class="unit">${fu}</span></th>` +
        `<th>MX <span class="unit">${mu}</span></th><th>MY <span class="unit">${mu}</span></th><th>MZ <span class="unit">${mu}</span></th></tr></thead><tbody>${rows}</tbody>` +
        `<tfoot><tr class="pt-sum"><td class="txt"><b>Σ</b></td><td></td><td></td><td></td>${sum.map((x, i) => `<td><b>${esc(ptU.fmt(i < 3 ? "force" : "moment", x, 2))}</b></td>`).join("")}</tr></tfoot></table>`;
      body.append(el("div", "dlg-group-title", "Secondary reactions (primary reactions are zero — these are the PT case reactions)"), tw,
        el("p", "muted cd-note", `Σ forces ≈ 0: the tendon load set is self-equilibrating; the secondary reactions only redistribute between supports.`));
    };
    const fb = footBar("ETABS Hyperstatic: secondary = total − primary", [btn("Close", "btn-run", () => dlg.close(), "", "ptHresClose")]);
    const dlg = dialog("ptHresDlg", { title: "Hyperstatic Results", wide: true, body, foot: fb.wrap, onUnits: draw });
    draw();
    return dlg;
  }

  /* ================================================================
     Properties panel + Loads editor decorations
     ================================================================ */
  function decorateProps(box, sel = {}) {
    const m = S.model;
    if (!m || !box) return;
    const objs = [...(sel.members || []), ...(sel.shells || [])];
    if (!objs.length) return;
    const uids = new Set(objs.map(o => o.uid));
    const hosted = tendonsOf(m).filter(t => hostsOf(t).some(h => uids.has(h)));
    const canDraw = (sel.members || []).some(x => x.kind !== "column") || (sel.shells || []).filter(x => x.kind === "slab").length === 1;
    if (!hosted.length && !canDraw) return;
    const wrap = el("div", "pt-props-card");
    wrap.id = "ptPropsCard";
    wrap.appendChild(el("h3", "group-title", `Tendons <span class="unit">${hosted.length ? `${hosted.length} on selection` : "post-tensioning"}</span>`));
    for (const t of hosted) {
      const c = TG.canonical(t);
      const r = el("div", "pt-prow");
      r.dataset.uid = t.uid;
      r.innerHTML = `<span class="pt-li-dash"></span><b>${esc(t.uid)}</b><span class="cd-badge pt-badge">${esc(t.pattern)}</span>` +
        `<span class="muted">P0 ${esc(ptU.fmtU("force", c.area * c.jacking_stress, 0))} · ${c.points.length - 1} seg</span>`;
      const b = el("div", "pt-pbtns");
      b.append(btn("Profile…", "btn-small", () => openTendonProfile(t.uid), `Edit the profile of ${t.uid}`, `ptPropEdit-${t.uid}`),
        btn("Properties…", "btn-small", () => openTendonProperties({ select: t.uid }), "", `ptPropProps-${t.uid}`),
        btn("Forces…", "btn-small", () => openTendonForces(t.uid), "", `ptPropForces-${t.uid}`),
        btn("✕", "btn-small", () => {
          const pats = new Set(tendonsOf(m).filter(x => x !== t).map(x => x.pattern));
          const bad = Object.entries(m.hyperstatic_cases || {}).find(([, hc]) => !Object.keys(((m.cases || {})[hc.case] || {}).patterns || {}).some(p => pats.has(p)));
          if (bad) { toast("Can't delete", `Hyperstatic case ${bad[0]} needs this tendon — delete the case first.`, "error", 5000); return; }
          m.tendons = tendonsOf(m).filter(x => x !== t);
          if (!m.tendons.length) delete m.tendons;
          commit(`${t.uid} deleted`);
        }, `Delete tendon ${t.uid}`, `ptPropDel-${t.uid}`));
      r.appendChild(b);
      wrap.appendChild(r);
    }
    if (canDraw) wrap.appendChild(btn("Draw Tendon on selection…", "btn-small", () => openDrawTendon(), "", "ptPropDraw"));
    const del = box.querySelector("#propDelete");
    const anchor = del && del.previousElementSibling && del.previousElementSibling.tagName === "H3" ? del.previousElementSibling : del;
    if (anchor) anchor.before(wrap); else box.appendChild(wrap);
  }
  function decoratePattern(row, m, name) {
    const n = tendonsOf(m).filter(t => t.pattern === name).length;
    if (!n || !row) return;
    const b = el("span", "pt-pat-badge", `PT · ${n} tendon${n > 1 ? "s" : ""}`);
    b.title = `Tendon equivalent loads are applied in pattern ${name} — Define → Tendon Properties…`;
    b.addEventListener("click", () => openTendonProperties({ select: tendonsOf(m).find(t => t.pattern === name).uid }));
    const counts = row.querySelector(".lp-counts");
    if (counts) counts.after(b); else row.appendChild(b);
  }

  /* ================================================================
     overlays — dashed magenta polylines
     ================================================================ */
  let cache = { model: null, ver: -1, items: [] };
  const items = model => {
    if (cache.model === model && cache.ver === ver) return cache.items;
    const out = [];
    for (const t of tendonsOf(model)) {
      try {
        const c = TG.canonical(t);
        if (c.points.length < 2) continue;
        out.push({ uid: c.uid, host: c.host, pts: TG.samplePolyline(c, 16) });
      } catch (e) { /* skip */ }
    }
    cache = { model, ver, items: out };
    return out;
  };
  const selectedHosts = () => new Set((S.selection || []).map(r => r.uid));
  function draw3d(ctx, P, viewer) {
    if (!viewer.model) return;
    const its = items(viewer.model);
    if (!its.length) return;
    const selH = selectedHosts();
    ctx.save();
    for (const it of its) {
      const hl = it.host.some(h => selH.has(h));
      ctx.strokeStyle = PT_COLOR;
      ctx.lineWidth = hl ? 3 : 1.8;
      ctx.globalAlpha = hl ? 1 : 0.9;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      for (let i = 1; i < it.pts.length; i++) {
        const s = viewer._projSeg(P, it.pts[i - 1], it.pts[i]);
        if (s) { ctx.moveTo(s.a.x, s.a.y); ctx.lineTo(s.b.x, s.b.y); }
      }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = PT_COLOR;
      for (const p of [it.pts[0], it.pts[it.pts.length - 1]]) {
        const pc = P.toCam(p);
        if (pc[2] < P.near) continue;
        const sp = P.proj(pc);
        ctx.fillRect(sp.x - 2.5, sp.y - 2.5, 5, 5);
      }
    }
    ctx.restore();
  }
  const NS = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };
  function drawPlan(g, ed) {
    const model = ed.opts.getModel();
    if (!model) return;
    const its = items(model);
    if (!its.length) return;
    const { zb, zt } = ptME.storyZ(model, ed.opts.getStory());
    const selH = selectedHosts();
    const grp = svgEl("g", { class: "pt-plan", "pointer-events": "none" });
    for (const it of its) {
      const zavg = it.pts.reduce((a, p) => a + p[2], 0) / it.pts.length;
      if (!(zavg > zb + 1e-3 && zavg <= zt + 0.5)) continue;
      const d = it.pts.map((p, i) => { const [x, y] = ed.toScreen(p[0], p[1]); return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`; }).join(" ");
      const hl = it.host.some(h => selH.has(h));
      grp.appendChild(svgEl("path", { d, fill: "none", stroke: PT_COLOR, "stroke-width": hl ? 3 : 2, "stroke-dasharray": "7 4", opacity: 0.95 }));
      const [lx, ly] = ed.toScreen(it.pts[0][0], it.pts[0][1]);
      const t = svgEl("text", { x: lx + 4, y: ly - 6, fill: PT_COLOR, "font-size": 10, "font-weight": 700 });
      t.textContent = it.uid;
      grp.appendChild(t);
    }
    g.appendChild(grp);
  }
  function drawElev(g, ed) {
    const model = ed.opts.getModel();
    if (!model || !ed.plane()) return;
    const its = items(model);
    const grp = svgEl("g", { class: "pt-elev", "pointer-events": "none" });
    for (const it of its) {
      if (!it.pts.every(p => ed.inPlane(p))) continue;
      const d = it.pts.map((p, i) => { const [x, y] = ed.toScreen(ed.sOf(p), p[2]); return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`; }).join(" ");
      grp.appendChild(svgEl("path", { d, fill: "none", stroke: PT_COLOR, "stroke-width": 2, "stroke-dasharray": "7 4" }));
    }
    if (grp.children.length) g.appendChild(grp);
  }
  if (sky.viewer) sky.viewer.ptOverlay = draw3d;
  if (sky.planEditor) sky.planEditor.ptOverlay = drawPlan;
  if (sky.elevEditor) sky.elevEditor.ptOverlay = drawElev;
  document.addEventListener("sky:model-changed", () => { if (sky.viewer) sky.viewer._dirty = true; });

  injectStyles();

  Object.assign(sky, {
    openDrawTendon, openTendonProfile, openTendonProperties,
    openHyperstaticCase, deleteHyperstaticCase,
    openTendonForces, openHyperstaticResults,
    ptDecorateProps: decorateProps, ptDecoratePattern: decoratePattern,
  });
  sky.tendons = { TG, profileXE, pxChart };
  return sky.tendons;
}

function injectStyles() {
  if (document.getElementById("ptStyles")) return;
  const s = document.createElement("style");
  s.id = "ptStyles";
  s.textContent = `
    .pt-dlg .modal-wide { width: 940px; }
    .pt-head { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; }
    .pt-head input[type="text"] { max-width: 220px; }
    .pt-hostinfo { font-size: 11.5px; }
    .pt-slabline { font-size: 12px; }
    .pt-xy { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; }
    .pt-xy input { width: 76px; }
    .pt-pat-pick select { flex: 1; min-width: 0; }
    .pt-prev, .pt-chart-wrap { margin: 0 0 10px; }
    .pt-mem { fill: rgba(154,167,180,.16); stroke: rgba(154,167,180,.55); stroke-width: 1; }
    .pt-mem-lbl { fill: var(--text-3); font-size: 9.5px; }
    .pt-centroid { stroke: var(--text-3); stroke-width: 1; stroke-dasharray: 4 4; opacity: .7; }
    .pt-sup { fill: none; stroke: var(--text-2); stroke-width: 1.2; }
    .pt-tendon { fill: none; stroke: ${PT_COLOR}; stroke-width: 2.2; stroke-dasharray: 7 4; stroke-linejoin: round; }
    .pt-anchor { fill: ${PT_COLOR}; }
    .pt-cp { fill: var(--surface-2, #161b22); stroke: ${PT_COLOR}; stroke-width: 1.4; }
    .pt-cp-lbl { fill: ${PT_COLOR}; font-size: 9.5px; font-weight: 650; font-variant-numeric: tabular-nums; }
    .pt-tbl-wrap { max-height: 30vh; }
    .pt-tbl td, .pt-tbl th { padding: 3px 5px; }
    .pt-tbl input, .pt-tbl select { width: 100%; min-width: 64px; padding: 2px 4px; }
    .pt-tbl select { min-width: 104px; }
    .pt-tbl tr.is-sel td { background: var(--accent-ghost); }
    .pt-tbl tr { cursor: pointer; }
    .pt-presets, .pt-seg-acts { font-size: 11.5px; }
    .pt-conv { font-size: 10.5px; flex: 1 1 260px; }
    .pt-props { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 18px; }
    .pt-col { display: flex; flex-direction: column; gap: 6px; }
    .pt-set-zone { fill: ${PT_COLOR}; fill-opacity: .12; }
    .pt-set-lbl { fill: ${PT_COLOR}; font-size: 9.5px; font-weight: 650; }
    .pt-p0 { stroke: var(--text-3); stroke-width: 1; stroke-dasharray: 5 4; }
    .pt-p0-lbl { fill: var(--text-2); font-size: 9.5px; }
    .pt-pline { fill: none; stroke: ${PT_COLOR}; stroke-width: 2; stroke-linejoin: round; }
    .pt-pdot { fill: ${PT_COLOR}; }
    .pt-pval { fill: var(--text-1); font-size: 9.5px; font-variant-numeric: tabular-nums; }
    .pt-cursor { stroke: var(--text-3); stroke-width: 1; }
    .pt-cursor-lbl { fill: var(--text-1); font-size: 10px; font-variant-numeric: tabular-nums; }
    .pt-cursor.hidden, .pt-cursor-lbl.hidden { display: none; }
    .pt-ctl { margin-bottom: 8px; }
    .pt-ctl .cd-row { grid-template-columns: auto minmax(120px, auto) 0; }
    .pt-part { width: auto; }
    .pt-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 16px; margin: 4px 0 10px; }
    .pt-card { background: var(--surface-2); border: 1px solid var(--border-soft); border-radius: 8px; padding: 6px 10px 8px; }
    .pt-kv { display: flex; justify-content: space-between; gap: 10px; font-size: 11.5px; padding: 2px 0; border-bottom: 1px solid var(--border-soft); }
    .pt-kv span { color: var(--text-2); } .pt-kv b { font-variant-numeric: tabular-nums; text-align: right; }
    .pt-est { color: var(--amber); border-color: rgba(229,165,10,.5); }
    .pt-diagrams { display: grid; grid-template-columns: repeat(auto-fill, minmax(270px, 1fr)); gap: 8px; margin-bottom: 10px; }
    .pt-diagrams .pt-chain-diag { grid-column: 1 / -1; }
    .pt-diagrams .pt-chain-diag svg { width: 100%; height: auto; display: block; }
    .pt-dz { stroke: var(--text-3); stroke-width: 1; }
    .pt-dj { stroke: var(--text-3); stroke-width: 1; stroke-dasharray: 3 4; opacity: .6; }
    .pt-dlbl { fill: var(--text-1); font-size: 10.5px; font-variant-numeric: tabular-nums; }
    .pt-dax { fill: var(--text-3); font-size: 10px; font-variant-numeric: tabular-nums; }
    .pt-react-wrap { max-height: 32vh; }
    .pt-sum td { border-top: 1px solid var(--border); }
    .pt-li-dash { width: 16px; height: 0; border-top: 2px dashed ${PT_COLOR}; flex: none; display: inline-block; }
    .pt-badge { color: ${PT_COLOR}; border-color: rgba(224,64,251,.45); }
    .pt-right { min-width: 0; }
    .pt-list-grid { grid-template-columns: 220px minmax(0, 1fr); }
    .pt-props-card { margin: 6px 0 8px; display: flex; flex-direction: column; gap: 6px; }
    .pt-prow { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 11.5px; }
    .pt-pbtns { display: flex; gap: 4px; flex-wrap: wrap; margin-left: auto; }
    .pt-pat-badge { font-size: 10px; font-weight: 700; color: ${PT_COLOR}; border: 1px solid rgba(224,64,251,.5); border-radius: 99px; padding: 0 7px; cursor: pointer; white-space: nowrap; }
    .kind-chip.k-prestress { color: ${PT_COLOR}; border-color: rgba(224,64,251,.55); background: rgba(224,64,251,.10); }
    .pt-pickbar { position: absolute; top: 10px; left: 50%; transform: translateX(-50%); z-index: 40; display: flex; align-items: center; gap: 10px;
      background: var(--surface-2, #161b22); border: 1px solid rgba(224,64,251,.55); border-radius: 8px; padding: 6px 10px; font-size: 12px;
      box-shadow: 0 6px 18px rgba(0,0,0,.35); max-width: calc(100% - 32px); }
    .pt-pick-ico .dlg-ico { width: 18px; height: 18px; }
    @media (max-width: 760px) { .pt-props, .pt-cards, .pt-list-grid { grid-template-columns: minmax(0, 1fr); } }
  `;
  document.head.appendChild(s);
}
