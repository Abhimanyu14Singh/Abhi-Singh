/* SkyFrame — Display › Force Diagrams › Shell Stresses / Forces… (A3).

   ETABS "Shell Stresses/Forces" display: contour of a shell resultant or
   stress component on the 3D view for ANY case with shell results — static
   cases, response-spectrum cases (once the engine emits RS shell_forces),
   additive combos and max/min combos (envelope / abs / srss / range, and add
   combos with RS members; Max or Min).

     component   F11 F22 F12 FMax FMin FVM · M11 M22 M12 MMax MMin ·
                 V13 V23 VMax · S11 S22 S12 SMax SMin SVM (Top / Middle /
                 Bottom / per layer) · S13 S23 (average)
     axes        Local (element 1-2-3) | Global (slabs X/Y/up, walls
                 horizontal/up)
     averaging   None (element values) | Objects | All (nodal averaging)
     colour      Auto symmetric about 0 | Auto min–max | User min/max ·
                 continuous or banded · diverging or rainbow palette
     legend      in the current display units (js/units.js: line_force,
                 line_moment, stress)

   Data: POST /api/shell_results/{sources,contour} (live; the displayed
   results are sent so the contour always matches the view) or
   js/mock_shellres.js (?mock=1). The dialog edits VIEW settings only — the
   model is never touched (OK with defaults leaves it byte-identical).
   Hooks: viewer3d.js (3 lines: contour provider, item pass, self-drawing
   items), app.js (import + init), mock_tables.js / tables.js (tables). */

import srU from "./units.js";
import { el as srEl, esc as srEsc, dkForm as srForm, dkGroup as srGroup, dkDialog as srDialog, dkNote as srNote } from "./dlgkit.js";
import { srMockOptions, srMockSources, srMockContour, SR_COMPONENTS as srComps } from "./mock_shellres.js";

const SR_KEY = "skyframe.shellres.v1";
const SR_FORCE_COMPS = ["F11", "F22", "F12", "FMax", "FMin", "FVM", "M11", "M22", "M12", "MMax", "MMin", "V13", "V23", "VMax"];
const SR_STRESS_COMPS = ["S11", "S22", "S12", "SMax", "SMin", "SVM", "S13", "S23"];
const SR_LABEL = {
  F11: "F11 — membrane force 1-1", F22: "F22 — membrane force 2-2", F12: "F12 — membrane shear",
  FMax: "FMax — max principal force", FMin: "FMin — min principal force", FVM: "FVM — von Mises force",
  M11: "M11 — plate moment 1-1", M22: "M22 — plate moment 2-2", M12: "M12 — twisting moment",
  MMax: "MMax — max principal moment", MMin: "MMin — min principal moment",
  V13: "V13 — transverse shear 1-3", V23: "V23 — transverse shear 2-3", VMax: "VMax — max transverse shear",
  S11: "S11 — stress 1-1", S22: "S22 — stress 2-2", S12: "S12 — in-plane shear stress",
  SMax: "SMax — max principal stress", SMin: "SMin — min principal stress", SVM: "SVM — von Mises stress",
  S13: "S13Avg — avg transverse shear stress", S23: "S23Avg — avg transverse shear stress",
};
const SR_KIND = { line_force: "line_force", line_moment: "line_moment", stress: "stress" };
const SR_DEFAULTS = { case: "", bound: "max", type: "forces", component: "M11", location: "top", axes: "local",
  averaging: "none", range: "sym", umin: null, umax: null, bands: 0, palette: "diverging" };

const SR_CSS = `
.shr-legend { position: absolute; left: 12px; bottom: 12px; z-index: 6; width: 250px; pointer-events: none;
  background: rgba(17, 22, 29, 0.9); border: 1px solid var(--border-soft, #333); border-radius: 6px;
  padding: 8px 10px 7px; font-size: 10.5px; color: var(--text-2, #ccd); }
.shr-legend .shr-t { font-weight: 650; color: var(--text-1, #fff); font-size: 11px; margin-bottom: 2px; }
.shr-legend .shr-sub { color: var(--text-3, #99a); margin-bottom: 5px; }
.shr-legend .shr-bar { height: 9px; border-radius: 3px; border: 1px solid rgba(232,237,243,.14); }
.shr-legend .shr-lab { display: flex; justify-content: space-between; margin-top: 3px; font-variant-numeric: tabular-nums; }
.shr-legend .shr-note { margin-top: 4px; color: var(--warn, #e0b050); font-size: 10px; }
.shr-legend.hidden { display: none; }
.dk-dlg .shr-status { font-size: 12px; min-height: 1.4em; margin: 6px 0 0; }
.dk-dlg .shr-status.is-err { color: var(--danger, #e05252); }
.dk-dlg .shr-row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }
@media (max-width: 640px) { .dk-dlg .shr-row2 { grid-template-columns: 1fr; } }
`;

/* ---------------------------------------------------------------- colour */
const DIV = [[44, 127, 214], [242, 245, 248], [224, 82, 82]];
const RAINBOW = [[40, 60, 200], [30, 170, 230], [40, 190, 90], [235, 215, 40], [235, 120, 30], [215, 40, 40]];
function ramp(stops, t) {
  t = Math.max(0, Math.min(1, isFinite(t) ? t : 0.5));
  const x = t * (stops.length - 1), i = Math.min(stops.length - 2, Math.floor(x)), f = x - i;
  return stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * f));
}
export function srColor(st, v) {
  if (v == null || !isFinite(v)) return "rgba(128,128,128,0.35)";
  const [lo, hi] = st.scale;
  let t = hi > lo ? (v - lo) / (hi - lo) : 0.5;
  if (st.opts.bands > 1) t = (Math.min(st.opts.bands - 1, Math.floor(Math.max(0, Math.min(1, t)) * st.opts.bands)) + 0.5) / st.opts.bands;
  const c = ramp(st.opts.palette === "rainbow" ? RAINBOW : DIV, t);
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
function gradientCss(st) {
  const stops = st.opts.palette === "rainbow" ? RAINBOW : DIV;
  const n = st.opts.bands > 1 ? st.opts.bands : 0;
  if (!n) return `linear-gradient(90deg, ${stops.map((c, i) => `rgb(${c}) ${(100 * i / (stops.length - 1)).toFixed(1)}%`).join(", ")})`;
  const parts = [];
  for (let k = 0; k < n; k++) {
    const c = ramp(stops, (k + 0.5) / n);
    parts.push(`rgb(${c}) ${(100 * k / n).toFixed(2)}%`, `rgb(${c}) ${(100 * (k + 1) / n).toFixed(2)}%`);
  }
  return `linear-gradient(90deg, ${parts.join(", ")})`;
}

/* ---------------------------------------------------------------- state */
function loadOpts() {
  try { const o = JSON.parse(localStorage.getItem(SR_KEY) || "null"); if (o && typeof o === "object") return { ...SR_DEFAULTS, ...o, case: "" }; }
  catch { /* storage blocked */ }
  return { ...SR_DEFAULTS };
}
function saveOpts(o) { try { localStorage.setItem(SR_KEY, JSON.stringify(o)); } catch { /* ignore */ } }

/* ---------------------------------------------------------------- API */
async function srApi(sky, path, body) {
  const S = sky.store;
  if (S.mock) {
    await new Promise(r => setTimeout(r, 30));
    if (path.endsWith("/options")) return srMockOptions();
    if (path.endsWith("/sources")) return srMockSources(S.model, S.results);
    if (path.endsWith("/contour")) return srMockContour(S.model, S.results, body || {});
    throw new Error(`no mock for ${path}`);
  }
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...(body || {}), results: S.results }) });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}

/* ---------------------------------------------------------------- overlay */
function makeExt(st) {
  return {
    active: () => !!(st.on && st.data && st.results === st.sky.store.results),
    /** Push depth-sorted self-drawing contour cells (viewer3d.js hook). */
    items(items, P, nodeXYZ, results) {
      const d = st.data;
      if (!d || !results || !results.shell_quads) return;
      const n = results.shell_quads.length;
      const sub = d.corners ? (n > 3000 ? 2 : n > 800 ? 3 : 4) : 1;
      const edge = "rgba(13, 17, 23, 0.45)";
      results.shell_quads.forEach((q, i) => {
        const cam = [];
        for (const t of q.nodes) {
          const p = nodeXYZ[t] || nodeXYZ[String(t)];
          if (!p) return;
          const pc = P.toCam(p);
          if (pc[2] < P.near) return;
          cam.push(pc);
        }
        const zc = cam.reduce((s, c) => s + c[2], 0) / cam.length;
        const outline = cam.map(c => P.proj(c));
        if (!d.corners) {
          const fill = srColor(st, d.values[i]);
          items.push({ z: zc, draw: ctx => {
            ctx.beginPath(); outline.forEach((p, k) => k ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); ctx.closePath();
            ctx.globalAlpha = 0.94; ctx.fillStyle = fill; ctx.fill(); ctx.globalAlpha = 1;
            ctx.strokeStyle = edge; ctx.lineWidth = 0.7; ctx.stroke();
          } });
          return;
        }
        const cv = d.corners[i] || [];
        const lerp = (a, b, t) => a.map((x, k) => x + (b[k] - x) * t);
        const cells = [];
        if (cam.length === 4) {                     // bilinear sub-cells
          const at = (u, v) => {
            const p = lerp(lerp(cam[0], cam[1], u), lerp(cam[3], cam[2], u), v);
            const val = [cv[0], cv[1], cv[2], cv[3]].some(x => x == null) ? null
              : (1 - u) * (1 - v) * cv[0] + u * (1 - v) * cv[1] + u * v * cv[2] + (1 - u) * v * cv[3];
            return [p, val];
          };
          for (let a = 0; a < sub; a++) for (let b = 0; b < sub; b++) {
            const pts = [at(a / sub, b / sub), at((a + 1) / sub, b / sub), at((a + 1) / sub, (b + 1) / sub), at(a / sub, (b + 1) / sub)];
            const vals = pts.map(x => x[1]);
            cells.push([pts.map(x => P.proj(x[0])), vals.some(x => x == null) ? null : vals.reduce((s, x) => s + x, 0) / 4]);
          }
        } else {                                    // barycentric sub-triangles
          const at = (a, b) => {
            const c = 1 - a - b;
            const p = cam[0].map((x, k) => c * x + a * cam[1][k] + b * cam[2][k]);
            const val = [cv[0], cv[1], cv[2]].some(x => x == null) ? null : c * cv[0] + a * cv[1] + b * cv[2];
            return [p, val];
          };
          for (let a = 0; a < sub; a++) for (let b = 0; a + b < sub; b++) {
            const tri = [[at(a / sub, b / sub), at((a + 1) / sub, b / sub), at(a / sub, (b + 1) / sub)]];
            if (a + b + 1 < sub) tri.push([at((a + 1) / sub, b / sub), at((a + 1) / sub, (b + 1) / sub), at(a / sub, (b + 1) / sub)]);
            for (const pts of tri) {
              const vals = pts.map(x => x[1]);
              cells.push([pts.map(x => P.proj(x[0])), vals.some(x => x == null) ? null : vals.reduce((s, x) => s + x, 0) / 3]);
            }
          }
        }
        items.push({ z: zc, draw: ctx => {
          ctx.globalAlpha = 0.94;
          for (const [pts, v] of cells) {
            ctx.beginPath(); pts.forEach((p, k) => k ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); ctx.closePath();
            ctx.fillStyle = srColor(st, v); ctx.fill();
            ctx.strokeStyle = ctx.fillStyle; ctx.lineWidth = 0.6; ctx.stroke();   // hide hairline seams
          }
          ctx.globalAlpha = 1;
          ctx.beginPath(); outline.forEach((p, k) => k ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); ctx.closePath();
          ctx.strokeStyle = edge; ctx.lineWidth = 0.6; ctx.stroke();
        } });
      });
    },
  };
}

function scaleOf(st) {
  const [lo, hi] = st.data.range;
  if (st.opts.range === "user" && isFinite(st.opts.umin) && isFinite(st.opts.umax) && st.opts.umax > st.opts.umin)
    return [st.opts.umin, st.opts.umax];
  if (st.opts.range === "auto") return hi > lo ? [lo, hi] : [lo - 1, hi + 1];
  const m = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
  return [-m, m];
}

function renderLegend(st) {
  const box = st.legend;
  if (!box) return;
  const S = st.sky.store, v = st.sky.viewer;
  const over = v && v.overlay && (v.overlay.deformed || v.overlay.modal || v.overlay.buckling);
  const show = st.on && st.data && st.results === S.results && !over;
  box.classList.toggle("hidden", !show);
  if (!show) return;
  const d = st.data, kind = SR_KIND[d.quantity] || "none";
  const loc = d.location ? (d.location.startsWith("layer") ? ` Layer ${+d.location.replace(/\D/g, "") + 1}` : ` ${{ top: "Top", mid: "Middle", bot: "Bottom" }[d.location]}`) : "";
  const unit = srU.label(kind);
  const caseTxt = `${d.case}${d.bounded ? ` (${d.bound === "min" ? "Min" : "Max"})` : ""}`;
  const fmt = x => { const y = srU.toDisplay(kind, x); return isFinite(y) ? (Math.abs(y) >= 1e4 || (Math.abs(y) < 1e-2 && y !== 0) ? y.toExponential(2) : y.toFixed(Math.abs(y) < 10 ? 2 : 1)) : "—"; };
  const [lo, hi] = st.scale;
  box.innerHTML = `<div class="shr-t">${srEsc(d.component + loc)} <span class="muted">${srEsc(unit)}</span></div>
    <div class="shr-sub">${srEsc(caseTxt)} · ${d.axes === "global" ? "global" : "local"} axes · ${srEsc({ none: "no averaging", objects: "averaged (objects)", all: "averaged (all)" }[d.averaging])}</div>
    <div class="shr-bar" style="background:${gradientCss(st)}"></div>
    <div class="shr-lab"><span id="shrLegMin">${fmt(lo)}</span><span>${fmt((lo + hi) / 2)}</span><span id="shrLegMax">${fmt(hi)}</span></div>
    <div class="shr-lab muted"><span>data min ${fmt(d.range[0])}</span><span>data max ${fmt(d.range[1])}</span></div>
    ${(d.approximations || []).concat(d.warnings || []).length ? `<div class="shr-note">${(d.approximations || []).concat(d.warnings || []).map(srEsc).join("<br>")}</div>` : ""}`;
}

/* ---------------------------------------------------------------- dialog */
function openDialog(st) {
  const sky = st.sky, S = sky.store;
  if (!S.results) { sky.toast && sky.toast("Shell Stresses / Forces", "Run the analysis first (F5)", "error"); return null; }
  const o = JSON.parse(JSON.stringify(st.opts));
  const F = srForm();
  const caseSel = srEl("select", { class: "dk-select", id: "shrCase" });
  const boundSel = srEl("select", { class: "dk-select", id: "shrBound" }, [srEl("option", { value: "max" }, ["Max"]), srEl("option", { value: "min" }, ["Min"])]);
  const typeSel = srEl("select", { class: "dk-select", id: "shrType" }, [srEl("option", { value: "forces" }, ["Resultant Forces"]), srEl("option", { value: "stresses" }, ["Stresses"])]);
  const compSel = srEl("select", { class: "dk-select", id: "shrComp" });
  const locSel = srEl("select", { class: "dk-select", id: "shrLoc" });
  const axesSel = srEl("select", { class: "dk-select", id: "shrAxes" }, [srEl("option", { value: "local" }, ["Local (element 1-2-3)"]), srEl("option", { value: "global" }, ["Global (X / Y / up)"])]);
  const avgSel = srEl("select", { class: "dk-select", id: "shrAvg" }, [["none", "None (element values)"], ["objects", "Objects (same shell object)"], ["all", "All (every coplanar element)"]].map(([v, l]) => srEl("option", { value: v }, [l])));
  const rangeSel = srEl("select", { class: "dk-select", id: "shrRange" }, [["sym", "Auto — symmetric about 0"], ["auto", "Auto — data min to max"], ["user", "User defined"]].map(([v, l]) => srEl("option", { value: v }, [l])));
  const bandsSel = srEl("select", { class: "dk-select", id: "shrBands" }, [[0, "Continuous"], [6, "6 bands"], [10, "10 bands"], [16, "16 bands"]].map(([v, l]) => srEl("option", { value: String(v) }, [l])));
  const palSel = srEl("select", { class: "dk-select", id: "shrPalette" }, [["diverging", "Diverging (blue – white – red)"], ["rainbow", "Rainbow (ETABS)"]].map(([v, l]) => srEl("option", { value: v }, [l])));
  const status = srEl("p", { class: "muted shr-status", id: "shrStatus", "aria-live": "polite" });
  const kindOf = () => SR_KIND[(srComps[o.component] || [])[2]] || "none";
  // user range inputs: the unit follows the component (force/length,
  // moment/length or stress), so they are bound by hand through units.js
  const userRow = (key, label) => {
    const inp = srEl("input", { type: "text", inputmode: "decimal", class: "dk-num", id: key === "umin" ? "shrUmin" : "shrUmax", autocomplete: "off", placeholder: "auto" });
    const r = F.row(label, inp, { id: inp.id, unit: srU.label(kindOf()) });
    const err = r.querySelector(".dk-err");
    inp.addEventListener("change", () => {
      const txt = inp.value.trim();
      const v = txt === "" ? null : srU.parse(kindOf(), txt);
      if (v != null && !isFinite(v)) { err.textContent = `Enter a number in ${srU.label(kindOf())}.`; r.classList.add("is-invalid"); return; }
      err.textContent = ""; r.classList.remove("is-invalid");
      o[key] = v;
    });
    r.sync = () => {
      inp.value = o[key] == null ? "" : srU.inputValue(kindOf(), o[key]);
      r.querySelector(".dk-unit").textContent = srU.label(kindOf());
    };
    r.input = inp;
    return r;
  };
  const minRow = userRow("umin", "Minimum"), maxRow = userRow("umax", "Maximum");
  let lastKind = kindOf();
  const fixKinds = () => {
    if (kindOf() !== lastKind) { o.umin = null; o.umax = null; lastKind = kindOf(); }   // other quantity: old limits are meaningless
    minRow.sync(); maxRow.sync();
  };
  let sources = [], nLayers = 0;

  const fillComps = () => {
    const list = o.type === "stresses" ? SR_STRESS_COMPS : SR_FORCE_COMPS;
    if (!list.includes(o.component)) o.component = list[0];
    compSel.textContent = "";
    for (const k of list) compSel.appendChild(srEl("option", { value: k, selected: k === o.component }, [SR_LABEL[k] || k]));
    locSel.textContent = "";
    const locs = [["top", "Top (+3 face)"], ["mid", "Middle"], ["bot", "Bottom (−3 face)"]];
    for (let k = 0; k < nLayers; k++) locs.push([`layer:${k}`, `Layer ${k + 1} (centre)`]);
    if (!locs.some(([v]) => v === o.location)) o.location = "top";
    for (const [v, l] of locs) locSel.appendChild(srEl("option", { value: v, selected: v === o.location }, [l]));
    const stressLoc = o.type === "stresses" && !["S13", "S23"].includes(o.component);
    locSel.disabled = !stressLoc;
    locSel.closest(".dk-row").classList.toggle("dk-disabled", !stressLoc);
    fixKinds();
  };
  const syncBound = () => {
    const src = sources.find(s => s.name === o.case);
    boundSel.disabled = !(src && src.bounded);
    boundSel.closest(".dk-row").classList.toggle("dk-disabled", boundSel.disabled);
    const user = o.range === "user";
    for (const r of [minRow, maxRow]) { r.classList.toggle("dk-disabled", !user); r.querySelector("input").disabled = !user; }
  };
  caseSel.addEventListener("change", () => { o.case = caseSel.value; syncBound(); });
  boundSel.addEventListener("change", () => { o.bound = boundSel.value; });
  typeSel.addEventListener("change", () => { o.type = typeSel.value; fillComps(); });
  compSel.addEventListener("change", () => { o.component = compSel.value; fillComps(); });
  locSel.addEventListener("change", () => { o.location = locSel.value; });
  axesSel.addEventListener("change", () => { o.axes = axesSel.value; });
  avgSel.addEventListener("change", () => { o.averaging = avgSel.value; });
  rangeSel.addEventListener("change", () => { o.range = rangeSel.value; syncBound(); });
  bandsSel.addEventListener("change", () => { o.bands = +bandsSel.value; });
  palSel.addEventListener("change", () => { o.palette = palSel.value; });

  const body = [
    srGroup("Case / Load Combination", [F.row("Case / Combo", caseSel, { id: "shrCase" }), F.row("Step (max / min results)", boundSel, { id: "shrBound" })]),
    srGroup("Component", [F.row("Type", typeSel, { id: "shrType" }), F.row("Component", compSel, { id: "shrComp" }), F.row("Location", locSel, { id: "shrLoc" })]),
    srGroup("Output Options", [F.row("Axes", axesSel, { id: "shrAxes" }), F.row("Contour averaging", avgSel, { id: "shrAvg" })]),
    srGroup("Contour Range", [F.row("Range", rangeSel, { id: "shrRange" }), minRow, maxRow, F.row("Colours", bandsSel, { id: "shrBands" }), F.row("Palette", palSel, { id: "shrPalette" })]),
    srNote("Stresses from the resultants: S = N/t ± 6M/t² (Top = +3 face). Max/min sources (RS, envelopes) show the selected step; principal values of a max/min source are bounded over the resultant box."),
    status,
  ];
  const clearBtn = srEl("button", { type: "button", class: "btn", id: "shrClear", title: "Remove the contour from the 3D view" }, ["Clear Contours"]);
  clearBtn.addEventListener("click", () => { clear(st); status.textContent = "Contours cleared."; });
  const apply = async () => {
    if (!o.case) return "Select a case or combination with shell results.";
    if (o.range === "user" && (o.umin == null || o.umax == null || !(o.umax > o.umin)))
      return "User range: enter a minimum and a larger maximum.";
    status.classList.remove("is-err"); status.textContent = "Computing…";
    try {
      await show(st, o);
      const d = st.data;
      status.textContent = `${d.values.length} elements · data ${srU.fmtU(SR_KIND[d.quantity] || "none", d.range[0], 2)} … ${srU.fmtU(SR_KIND[d.quantity] || "none", d.range[1], 2)}`;
      return "";
    } catch (e) {
      status.classList.add("is-err"); status.textContent = e.message;
      return e.message;
    }
  };
  const dlg = srDialog({ id: "shrDlg", title: "Shell Stresses / Forces", iconId: null, size: "md", form: F, body,
    intro: "Contour a shell resultant or stress for a case, combination or envelope on the 3D view.",
    extra: [clearBtn], onUnits: () => fixKinds(),
    onOk: async () => { const m = await apply(); return m ? m : true; },
    onApply: () => { apply(); return ""; },
  });
  (async () => {
    try {
      const r = await srApi(sky, "/api/shell_results/sources", {});
      sources = r.sources || []; nLayers = r.layers || 0;
      caseSel.textContent = "";
      if (!sources.length) {
        caseSel.appendChild(srEl("option", { value: "" }, ["(no case with shell results)"]));
        status.textContent = "No case in these results carries shell forces (mesh walls / slabs as shell objects and rerun).";
      }
      const pref = [o.case, st.opts.case, S.caseName].find(n => n && sources.some(s => s.name === n)) || (sources[0] && sources[0].name) || "";
      o.case = pref;
      for (const s of sources) {
        const tag = s.kind === "response_spectrum" || s.kind === "rs_directional" ? "RS" : s.kind === "combo" ? (s.bounded ? "Combo · max/min" : "Combo") : s.kind === "staged" ? "Staged" : "Static";
        caseSel.appendChild(srEl("option", { value: s.name, selected: s.name === pref }, [`${s.name}  (${tag})`]));
      }
      boundSel.value = o.bound; typeSel.value = o.type; axesSel.value = o.axes; avgSel.value = o.averaging;
      rangeSel.value = o.range; bandsSel.value = String(o.bands); palSel.value = o.palette;
      fillComps(); syncBound();
    } catch (e) { status.classList.add("is-err"); status.textContent = `Could not list cases: ${e.message}`; }
  })();
  boundSel.value = o.bound; typeSel.value = o.type; axesSel.value = o.axes; avgSel.value = o.averaging;
  rangeSel.value = o.range; bandsSel.value = String(o.bands); palSel.value = o.palette;
  fillComps(); syncBound();
  dlg.api = { opts: o, status };
  return dlg;
}

async function show(st, opts, { navigate = true } = {}) {
  const sky = st.sky, S = sky.store;
  const o = { ...st.opts, ...opts };
  const data = await srApi(sky, "/api/shell_results/contour", { case: o.case, component: o.component, location: o.location,
    axes: o.axes, averaging: o.averaging, bound: o.bound });
  st.opts = o; saveOpts(o);
  st.data = data; st.results = S.results; st.on = true;
  st.scale = scaleOf(st);
  if (S.contour) S.contour.on = false;                     // the legacy chip contour yields
  try { sky.syncContoursUI && sky.syncContoursUI(); } catch { /* ignore */ }
  const v = sky.viewer;
  if (v) {
    if (v.overlay) { v.overlay.deformed = false; v.overlay.modal = false; v.overlay.buckling = false; }
    try { sky.syncOverlayUI && sky.syncOverlayUI(); } catch { /* ignore */ }
    v._dirty = true;
  }
  if (navigate) {                                         // contours live on the 3D results view
    try { sky.setMode && sky.setMode("analyze"); sky.switchTab && sky.switchTab("view3d"); } catch { /* ignore */ }
  }
  renderLegend(st);
  return data;
}

function clear(st) {
  st.on = false; st.data = null;
  if (st.sky.viewer) st.sky.viewer._dirty = true;
  renderLegend(st);
}

/** Register Display › Force Diagrams › Shell Stresses / Forces… and the
    viewer overlay (exposes __sky.shellRes). */
export function initShellRes(sky) {
  if (!document.getElementById("shrCss")) document.head.appendChild(srEl("style", { id: "shrCss", text: SR_CSS }));
  const st = { sky, opts: loadOpts(), on: false, data: null, results: null, scale: [0, 1], legend: null };
  const wrap = document.getElementById("viewerWrap");
  if (wrap) { st.legend = srEl("div", { class: "shr-legend hidden", id: "shrLegend", role: "img", "aria-label": "Shell contour legend" }); wrap.appendChild(st.legend); }
  if (sky.viewer) sky.viewer.shellResExt = makeExt(st);
  sky.shellRes = {
    open: () => openDialog(st),
    show: o => show(st, o),
    clear: () => clear(st),
    state: st,
    sources: () => srApi(sky, "/api/shell_results/sources", {}),
    contour: body => srApi(sky, "/api/shell_results/contour", body),
  };
  if (sky.menu && sky.menu.add)
    sky.menu.add("Display/Force Diagrams", { label: "Shell Stresses / Forces…", act: "dis-shr-contours", needs: "results",
      hint: "contours · RS · envelopes · stresses", fn: () => openDialog(st) });
  document.addEventListener("sky:results-changed", () => {
    if (!st.on) return;
    const o = st.opts;
    if (!sky.store.results) { clear(st); return; }
    show(st, o, { navigate: false }).catch(() => clear(st));
  });
  srU.onUnitsChange && srU.onUnitsChange(() => renderLegend(st));
  setInterval(() => { if (st.on) renderLegend(st); }, 600);   // overlays (deformed / modes) hide the legend
}
