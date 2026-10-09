/* SkyFrame — small shared helpers of the B9/B11 dialogs (frame auto mesh,
   output stations, named spring properties, link hysteresis types).
   Dialog shell = js/analysisdlg.js; numbers go through js/units.js. */

import B9U from "./units.js";

export const b9esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const b9clone = o => (o == null ? o : JSON.parse(JSON.stringify(o)));
export const b9same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export function b9h(tag, attrs = {}, kids = []) {
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
export function b9group(legend, cls = "") {
  const g = b9h("fieldset", { class: "dlg-group " + cls });
  g.appendChild(b9h("legend", { text: legend }));
  return g;
}
let cssDone = false;
export function b9css() {
  if (cssDone || document.querySelector("link[data-b9-css]")) { cssDone = true; return; }
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/b9dlg.css"; l.setAttribute("data-b9-css", "1");
  document.head.appendChild(l);
  cssDone = true;
}

/** Number input bound to a units kind. get(): exact SI while untouched,
    null when blank, NaN when not numeric. */
export function b9num(kind, si, attrs = {}) {
  const i = b9h("input", { type: "number", step: "any", ...attrs });
  let base = si, shown = "";
  const set = v => { base = v; shown = v == null ? "" : B9U.inputValue(kind, v); i.value = shown; };
  set(si);
  return {
    el: i, kind,
    get: () => (i.value === shown ? base : (String(i.value).trim() === "" ? null : B9U.parse(kind, i.value))),
    set,
    unit: () => B9U.label(kind),
  };
}
/** Labelled numeric row: label · input · unit. */
export function b9numRow(label, f, extraCls = "") {
  return b9h("label", { class: "asn-num-row b9-num-row " + extraCls }, [
    b9h("span", { html: label }), f.el, b9h("span", { class: "asn-unit", text: f.unit ? f.unit() : "" })]);
}
export function b9tabs(items, onPick, id) {
  const wrap = b9h("div", { class: "asn-tabs", role: "tablist", id });
  const btns = {};
  for (const [k, t] of items) {
    const b = b9h("button", { class: "asn-tab", role: "tab", "data-tab": k, type: "button", text: t });
    b.addEventListener("click", () => onPick(k));
    btns[k] = b;
    wrap.appendChild(b);
  }
  return { el: wrap, set: k => { for (const [kk, b] of Object.entries(btns)) { b.classList.toggle("is-active", kk === k); b.setAttribute("aria-selected", kk === k ? "true" : "false"); } }, btns };
}
/** Refresh everything that draws the model after an edit. */
export function b9after(sky, evt) {
  sky.markDirty && sky.markDirty();
  try { sky.renderProps && sky.renderProps(); } catch (e) { console.error(e); }
  try { if (sky.planEditor) sky.planEditor.renderStatic(); } catch (e) { console.error(e); }
  try { if (sky.elevEditor && sky.elevEditor.refresh) sky.elevEditor.refresh(); } catch (e) { console.error(e); }
  if (sky.viewer) sky.viewer._dirty = true;
  document.dispatchEvent(new CustomEvent("sky:b9-edit", { detail: { what: evt } }));
}
export const b9fmtNum = (v, d = 3) => (v == null || !isFinite(v)) ? "—" : String(+(+v).toPrecision(d + 1));

/** Force–deformation chart (inline SVG string). series: [{pts:[[d,F],…],
    cls, dash}], marks: [[d,F],…]. Values SI; axes labelled in display units. */
export function b9curveSvg(series, { xKind = "disp", yKind = "force", marks = [], title = "", w = 300, hgt = 200, id } = {}) {
  const M = { l: 46, r: 12, t: 14, b: 26 };
  const pw = w - M.l - M.r, ph = hgt - M.t - M.b;
  const all = series.flatMap(s => s.pts).concat(marks);
  let xm = Math.max(1e-12, ...all.map(p => Math.abs(p[0]))), ym = Math.max(1e-12, ...all.map(p => Math.abs(p[1])));
  xm *= 1.08; ym *= 1.12;
  const X = d => M.l + (d + xm) / (2 * xm) * pw, Y = f => M.t + (ym - f) / (2 * ym) * ph;
  const decFor = (kind, si) => { const v = Math.abs(B9U.toDisplay(kind, si / 1.1)); if (!(v > 1e-9)) return 1; return v >= 10 ? 1 : v >= 1 ? 2 : Math.min(6, Math.ceil(-Math.log10(v)) + 2); };
  const dx = decFor(xKind, xm), dy = decFor(yKind, ym);
  const num = (kind, v, d) => (+B9U.toDisplay(kind, v)).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  const fx = v => num(xKind, v, dx), fy = v => num(yKind, v, dy);
  let s = `<svg viewBox="0 0 ${w} ${hgt}" class="b9-curve-svg" role="img"${id ? ` id="${id}"` : ""} aria-label="${b9esc(title || "force-deformation")}">`;
  s += `<rect x="${M.l}" y="${M.t}" width="${pw}" height="${ph}" class="b9-plot-bg"/>`;
  s += `<line x1="${M.l}" y1="${Y(0)}" x2="${M.l + pw}" y2="${Y(0)}" class="b9-axis"/><line x1="${X(0)}" y1="${M.t}" x2="${X(0)}" y2="${M.t + ph}" class="b9-axis"/>`;
  s += `<text x="${M.l + pw}" y="${hgt - 8}" class="b9-tick" text-anchor="end">${b9esc(fx(xm / 1.08))} ${b9esc(B9U.label(xKind))}</text>`;
  s += `<text x="${M.l}" y="${hgt - 8}" class="b9-tick">${b9esc(fx(-xm / 1.08))}</text>`;
  s += `<text x="${M.l - 4}" y="${M.t + 8}" class="b9-tick" text-anchor="end">${b9esc(fy(ym / 1.12))}</text>`;
  s += `<text x="${M.l - 4}" y="${M.t + ph}" class="b9-tick" text-anchor="end">${b9esc(fy(-ym / 1.12))}</text>`;
  s += `<text x="${M.l - 4}" y="${Y(0) + 3}" class="b9-tick" text-anchor="end">${b9esc(B9U.label(yKind))}</text>`;
  for (const se of series) {
    if (!se.pts.length) continue;
    const d = se.pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
    s += `<path d="${d}" class="b9-series ${se.cls || ""}"${se.dash ? ` stroke-dasharray="${se.dash}"` : ""}/>`;
  }
  for (const p of marks) s += `<circle cx="${X(p[0]).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="3" class="b9-mark"><title>${b9esc(fx(p[0]))}, ${b9esc(fy(p[1]))}</title></circle>`;
  if (title) s += `<text x="${M.l + 6}" y="${M.t + 11}" class="b9-tick b9-ttl">${b9esc(title)}</text>`;
  return s + "</svg>";
}
