/* SkyFrame — "Nonlinear Material Data" section of the Material Property
   Data panel (v1.13 contract §4). Edits material.stress_strain in place:
     null                         → engine default laws ("Default")
     {model, hysteresis, params, points?}  (points for "user" only; strain
                                    compression negative, stress kPa)
   and draws a live stress–strain SVG fed by POST /api/materials/curve
   (debounced; the host's fetchCurve falls back to the mock backbones).
   Stress inputs and the plot are in the current display units (units.js);
   the stored values stay SI (kPa). No frameworks. */

import U from "./units.js";
import * as ME from "./modeledit.js";
import { icon } from "./icons.js";

const NS = "http://www.w3.org/2000/svg";
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---- small DOM helpers in the mpd-* house style ---- */
function row(label, unit, control, title) {
  const r = document.createElement("div");
  r.className = "mpd-field";
  const lab = document.createElement("span");
  lab.className = "mpd-label";
  lab.textContent = label;
  if (unit) {
    const u = document.createElement("span");
    u.className = "mpd-unit"; u.textContent = unit;
    lab.appendChild(u);
  }
  if (title) r.title = title;
  r.append(lab, control);
  return r;
}
function select(options, value, onChange) {
  const s = document.createElement("select");
  for (const [v, lbl] of options) {
    const o = document.createElement("option");
    o.value = v; o.textContent = lbl; o.selected = v === value;
    s.appendChild(o);
  }
  s.addEventListener("change", () => onChange(s.value));
  return s;
}

const PARAM_DEFS = {
  concrete: [
    ["eps_c0", "Strain at f'c  εc0", "strain", "0.0001", v => v > 0 && v < 0.05],
    ["eps_cu", "Ultimate strain  εcu", "strain", "0.0005", v => v > 0 && v < 0.1],
    ["ft", "Tensile strength  ft", "stress", "100", v => v >= 0],
  ],
  steel: [
    ["eps_sh", "Strain at hardening  εsh", "strain", "0.001", v => v > 0 && v < 0.5],
    ["eps_su", "Ultimate strain  εsu", "strain", "0.01", v => v > 0 && v < 1],
    ["b", "Hardening ratio  b", "none", "0.005", v => v >= 0 && v < 1],
  ],
};

/** Build the section. opts: {markDirty(), fetchCurve(material) → Promise}. */
export function buildStressStrainSection(mat, opts = {}) {
  const markDirty = opts.markDirty || (() => {});
  const fetchCurve = opts.fetchCurve;
  const type = mat.material_type || "concrete";
  const fam = ME.ssFamily(type);

  const sec = document.createElement("div");
  sec.className = "mpd-sec mpd-ss";
  const h = document.createElement("div");
  h.className = "mpd-sec-title";
  h.innerHTML = `${icon("ss-curve", "mpd-title-ico")}<span>Nonlinear Material Data</span>`;
  sec.appendChild(h);

  const body = document.createElement("div");
  body.className = "ss-body";
  const form = document.createElement("div");
  form.className = "mpd-grid ss-form";
  const plotWrap = document.createElement("div");
  plotWrap.className = "ss-plot-wrap";
  body.append(form, plotWrap);
  sec.appendChild(body);

  let timer = null, seq = 0;
  const refreshPlot = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const my = ++seq;
      plotWrap.classList.add("is-loading");
      try {
        const payload = JSON.parse(JSON.stringify(mat));
        const res = fetchCurve ? await fetchCurve(payload) : null;
        if (my !== seq) return;
        drawPlot(plotWrap, res, mat);
      } catch (e) {
        if (my !== seq) return;
        plotWrap.textContent = "";
        const p = document.createElement("p");
        p.className = "mpd-none"; p.textContent = `Curve unavailable — ${e.message}`;
        plotWrap.appendChild(p);
      } finally {
        if (my === seq) plotWrap.classList.remove("is-loading");
      }
    }, 180);
  };

  const commit = (rebuild = false) => {
    markDirty();
    if (rebuild) renderForm();
    refreshPlot();
  };

  const renderForm = () => {
    form.textContent = "";
    const ss = mat.stress_strain || null;
    const cur = ss ? ss.model : "default";
    const models = ["default", ...ME.ssAllowedModels(type)];
    const modelSel = select(models.map(k => [k, ME.SS_MODEL_LABELS[k] || k]), cur, v => {
      // "Default" ALWAYS stores null — never an object with model "default"
      if (v === "default") mat.stress_strain = null;
      else {
        const prev = mat.stress_strain;
        const next = ME.newStressStrain(mat, v);
        if (prev && prev.hysteresis) next.hysteresis = prev.hysteresis;
        if (prev && prev.params) next.params = { ...next.params, ...prev.params };
        mat.stress_strain = ME.normalizeStressStrain(next, mat);
      }
      commit(true);
    });
    modelSel.className = "ss-model";
    form.appendChild(row("Stress-Strain Curve", "", modelSel,
      "Default = the engine's built-in law for this material type (stored as null)"));

    const hyst = select(ME.SS_HYSTERESIS.map(k => [k, k[0].toUpperCase() + k.slice(1)]),
      ss ? ss.hysteresis : ME.ssDefaultHysteresis(type), v => {
        if (!mat.stress_strain) return;
        mat.stress_strain.hysteresis = v;
        commit();
      });
    hyst.className = "ss-hyst";
    hyst.disabled = !ss;
    form.appendChild(row("Hysteresis Type", "", hyst,
      ss ? "Cyclic rule used by nonlinear (TH / hinge) analyses" : "Engine default (pick a curve type to override)"));

    if (ss && ss.model !== "user" && PARAM_DEFS[fam]) {
      for (const [key, lbl, kind, siStep, ok] of PARAM_DEFS[fam]) {
        const inp = document.createElement("input");
        inp.type = "number";
        inp.className = "ss-param";
        inp.dataset.k = key;
        inp.step = kind === "stress" ? U.step("stress", siStep) : siStep;
        const show = () => { inp.value = kind === "stress"
          ? U.inputValue("stress", ss.params[key]) : String(ss.params[key] ?? ""); };
        show();
        inp.addEventListener("change", () => {
          const v = kind === "stress" ? U.parse("stress", inp.value) : parseFloat(inp.value);
          if (isFinite(v) && ok(v)) { ss.params[key] = v; commit(); }
          else show();
        });
        form.appendChild(row(lbl, kind === "stress" ? U.label("stress") : "", inp));
      }
    }
    if (ss && ss.model === "user") form.appendChild(pointsTable(ss));
    if (!ss) {
      const p = document.createElement("p");
      p.className = "mpd-none ss-default-note";
      p.textContent = fam === "other"
        ? "Engine default: linear elastic. Only a User curve can be assigned to this material type."
        : `Engine default law for ${type}: ${fam === "concrete" ? "Hognestad parabola (simple)" : "bilinear Steel01 (simple)"} from the strengths above.`;
      form.appendChild(p);
    }
  };

  const pointsTable = ss => {
    const wrap = document.createElement("div");
    wrap.className = "ss-points mpd-field wide";
    const head = document.createElement("div");
    head.className = "ss-pt head";
    head.innerHTML = `<span>Strain</span><span>Stress <span class="mpd-unit">${esc(U.label("stress"))}</span></span><span></span>`;
    wrap.appendChild(head);
    ss.points.forEach((p, i) => {
      const r = document.createElement("div");
      r.className = "ss-pt";
      r.dataset.i = String(i);
      const eIn = document.createElement("input");
      eIn.type = "number"; eIn.step = "0.0005"; eIn.value = String(p[0]);
      eIn.dataset.c = "0";
      const sIn = document.createElement("input");
      sIn.type = "number"; sIn.step = U.step("stress", "1000"); sIn.value = U.inputValue("stress", p[1]);
      sIn.dataset.c = "1";
      const origin = Math.abs(p[0]) < 1e-15;
      eIn.disabled = sIn.disabled = origin;
      if (origin) r.title = "The origin (0, 0) is always part of a user curve";
      const onEdit = () => {
        const e = parseFloat(eIn.value), s = U.parse("stress", sIn.value);
        if (!(isFinite(e) && isFinite(s))) { eIn.value = String(p[0]); sIn.value = U.inputValue("stress", p[1]); return; }
        ss.points[i] = [e, s];
        ss.points = ME.normalizeSsPoints(ss.points);
        commit(true);
      };
      eIn.addEventListener("change", onEdit);
      sIn.addEventListener("change", onEdit);
      const del = document.createElement("button");
      del.className = "chip-x"; del.textContent = "✕"; del.title = "Remove point";
      del.disabled = origin || ss.points.length <= 2;
      del.addEventListener("click", () => {
        ss.points.splice(i, 1);
        ss.points = ME.normalizeSsPoints(ss.points);
        commit(true);
      });
      r.append(eIn, sIn, del);
      wrap.appendChild(r);
    });
    const add = document.createElement("button");
    add.className = "btn btn-small ss-add";
    add.textContent = "+ Point";
    add.title = "Append a point beyond the last tension point";
    add.addEventListener("click", () => {
      const last = ss.points[ss.points.length - 1];
      const prev = ss.points[ss.points.length - 2] || [0, 0];
      const de = Math.max(Math.abs(last[0] - prev[0]), 0.001);
      ss.points.push([+(last[0] + de).toPrecision(6), last[1]]);
      ss.points = ME.normalizeSsPoints(ss.points);
      commit(true);
    });
    wrap.appendChild(add);
    return wrap;
  };

  renderForm();
  refreshPlot();
  sec.refreshPlot = refreshPlot;
  return sec;
}

/** SVG backbone plot (stress in display units vs strain) + notes. */
function drawPlot(wrap, res, mat) {
  wrap.textContent = "";
  const strain = (res && res.strain) || [], stressSi = (res && res.stress) || [];
  const n = Math.min(strain.length, stressSi.length);
  const title = document.createElement("div");
  title.className = "chart-title ss-title";
  title.innerHTML = `Stress–strain <span class="unit">${esc(U.label("stress"))} vs strain · ${esc((res && res.model) || "default")}</span>`;
  wrap.appendChild(title);
  const W = 320, H = 204, M = { l: 54, r: 10, t: 20, b: 24 };
  const pw = W - M.l - M.r, ph = H - M.t - M.b;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "ss-svg");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Material stress-strain backbone");
  const mk = (tag, attrs, text) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text != null) e.textContent = text;
    svg.appendChild(e);
    return e;
  };
  if (n < 2) {
    mk("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "ss-axis-text" }, "No curve points");
    wrap.appendChild(svg);
    return;
  }
  const stress = stressSi.slice(0, n).map(v => U.toDisplay("stress", v));
  let e0 = Math.min(0, ...strain.slice(0, n)), e1 = Math.max(0, ...strain.slice(0, n));
  let s0 = Math.min(0, ...stress), s1 = Math.max(0, ...stress);
  if (e1 - e0 < 1e-12) { e0 -= 1e-3; e1 += 1e-3; }
  if (s1 - s0 < 1e-12) { s0 -= 1; s1 += 1; }
  const pe = (e1 - e0) * 0.04, psd = (s1 - s0) * 0.06;
  e0 -= pe; e1 += pe; s0 -= psd; s1 += psd;
  const X = e => M.l + (e - e0) / (e1 - e0) * pw;
  const Y = s => M.t + (s1 - s) / (s1 - s0) * ph;
  // frame + zero axes
  mk("rect", { x: M.l, y: M.t, width: pw, height: ph, class: "ss-frame" });
  mk("line", { x1: X(0), x2: X(0), y1: M.t, y2: M.t + ph, class: "ss-zero" });
  mk("line", { x1: M.l, x2: M.l + pw, y1: Y(0), y2: Y(0), class: "ss-zero" });
  // y labels (min / 0 / max stress)
  const sMin = Math.min(...stress), sMax = Math.max(...stress);
  const dS = U.dec("stress", 0);
  const fmtS = v => (+v).toLocaleString("en-US", { maximumFractionDigits: dS });
  for (const v of [sMin, 0, sMax]) {
    if (v !== 0 && Math.abs(v) < 1e-12) continue;
    mk("text", { x: M.l - 4, y: Y(v) + 3, "text-anchor": "end", class: "ss-axis-text" }, fmtS(v));
  }
  // x labels (min / max strain)
  const eMin = Math.min(...strain.slice(0, n)), eMax = Math.max(...strain.slice(0, n));
  for (const v of [eMin, eMax]) {
    if (Math.abs(v) < 1e-15) continue;
    mk("text", { x: X(v), y: M.t + ph + 14, "text-anchor": "middle", class: "ss-axis-text" },
      (+v).toPrecision(3));
  }
  mk("text", { x: M.l + pw, y: H - 2, "text-anchor": "end", class: "ss-axis-unit" }, "strain");
  mk("text", { x: M.l - 4, y: 10, "text-anchor": "end", class: "ss-axis-unit" }, U.label("stress"));
  // backbone
  let d = "";
  for (let i = 0; i < n; i++) d += `${i ? " L" : "M"}${X(strain[i]).toFixed(2)},${Y(stress[i]).toFixed(2)}`;
  mk("path", { d, class: "ss-path", fill: "none" });
  if ((res.model || "") === "user")
    for (let i = 0; i < n; i++) mk("circle", { cx: X(strain[i]), cy: Y(stress[i]), r: 2.6, class: "ss-pt-dot" });
  wrap.appendChild(svg);
  const notes = (res.notes || []).filter(Boolean);
  if (notes.length) {
    const ul = document.createElement("ul");
    ul.className = "ss-notes";
    for (const t of notes) {
      const li = document.createElement("li");
      li.textContent = t;
      ul.appendChild(li);
    }
    wrap.appendChild(ul);
  }
}
