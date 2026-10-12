/* SkyFrame — code response spectra, default design combinations by code,
   ETABS load-pattern types and live-load reduction (CONTRACT "Code spectra,
   design combinations, pattern types and live-load reduction").

     Define › Functions › Response Spectrum › "Add From Code…"
         dsRsFnCode — one form per code (ASCE 7-16, ASCE 7-22 MPRS / two-
         period, EC8 Type 1/2 elastic + design, IS 1893:2016, NBCC 2020,
         NZS 1170.5, AS 1170.4, GB 50011). Points come from
         POST /api/function/spectrum-code/preview (live) or the JS port in
         js/mock_loadcodes.js (?mock=1); the function stores
         {name, points, damping, source: {code, params}}.
     Define › Load Combinations › "Add Default Design Combos by Code…"
         dcComboCode — EN 1990, IS 456 / IS 875-5, IS 800, NBCC 2020,
         AS/NZS 1170.0 from POST /api/combos/code/preview {model: draft}.
     Define › Load Patterns › Type — Super Dead, Reducible Live, Roof Live,
         Snow, Temperature … (LC_PAT_TYPES, hooked into dlgstd_defs.js).
     Tools › Code Checks › Live Load Reduction Factors… (dsLlr) — edits
         model.llr_settings; Display › Force Diagrams › Live Load Reduced
         Forces opens Show Tables › live_reduced_forces.

   Every dialog edits a draft; OK with nothing changed writes nothing.
   Numbers go through js/units.js (periods "period", vs30 "velocity";
   accelerations are in g and dimensionless factors have no unit). */

import lcU from "./units.js";
import * as lcME from "./modeledit.js";
import {
  el as lcEl, esc as lcEsc, clone as lcClone, dkForm as lcForm, dkGroup as lcGroup, dkAdvanced as lcAdvanced,
  dkNote as lcNote, dkDialog as lcDialog, dkDraft as lcDraft, dkCommit as lcCommit, uniqueName as lcUnique,
} from "./dlgkit.js";
import { LC_SPECTRA_CATALOGUE as lcSpecCat, LC_COMBO_CATALOGUE as lcComboCat } from "./mock_loadcodes_data.js";
import {
  lcMockSpectrum, lcMockCombos, lcApplyCombos, lcMockLlrFactors, LC_KIND_LABELS, LC_MPRS_PERIODS, LC_LLR_DEFAULTS,
} from "./mock_loadcodes.js";

/** ETABS pattern types added to Define › Load Patterns (value, label). */
export const LC_PAT_TYPES = ["superdead", "reducible_live", "roof_live", "snow", "temperature", "pattern_live",
  "construction", "rain", "ice", "move", "shrinkage", "creep", "settlement", "earth_pressure", "hyperstatic"]
  .map(k => [k, LC_KIND_LABELS[k]]);

const QKIND = { g: ["none", "g"], period: ["period", null], ratio: ["none", null], velocity: ["velocity", null] };
const fmt = (v, d = 4) => v == null || !isFinite(v) ? "—" : String(+(+v).toPrecision(d));
const modelBody = m => JSON.parse(JSON.stringify(m, (k, v) => k === "_mock_params" ? undefined : v));

async function lcPost(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) { const e = new Error((data && data.error) || `${res.status} ${res.statusText}`); e.status = res.status; throw e; }
  return data;
}
const isMock = sky => !!(sky && sky.store && sky.store.mock);

/* ================================================================
   backend (live) / mock
   ================================================================ */
export function lcSpectrum(sky, code, params) {
  if (isMock(sky)) return Promise.resolve().then(() => lcMockSpectrum(code, params));
  return lcPost("/api/function/spectrum-code/preview", { code, params });
}
export function lcCombos(sky, model, standard, params, includeRs) {
  if (isMock(sky)) return Promise.resolve().then(() => lcMockCombos(model, standard, params, includeRs));
  return lcPost("/api/combos/code/preview", { standard, params, include_rs: includeRs, model: modelBody(model) });
}
export function lcLlrFactors(sky, model, settings) {
  if (isMock(sky)) return Promise.resolve().then(() => lcMockLlrFactors(model, settings));
  return lcPost("/api/live-reduction/factors", { settings: settings || null, model: modelBody(model) });
}

/* ================================================================
   small SVG plot
   ================================================================ */
function lcSpark(points) {
  const W = 420, H = 150, P = 26;
  const box = lcEl("div", { class: "dk-chart", id: "dsRsFnCodeChart" });
  if (!points || !points.length) { box.appendChild(lcEl("p", { class: "muted dk-empty", text: "No points" })); return box; }
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const x1 = Math.max(...xs) || 1, y1 = Math.max(...ys) || 1;
  const sx = x => P + x / x1 * (W - 2 * P), sy = y => H - P + 6 - y / y1 * (H - 2 * P);
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join("");
  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Spectrum plot">
    <line x1="${P}" y1="${H - P + 6}" x2="${W - P}" y2="${H - P + 6}" stroke="var(--border)"/><line x1="${P}" y1="8" x2="${P}" y2="${H - P + 6}" stroke="var(--border)"/>
    <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="1.6"/>
    <text x="${W - P}" y="${H - 4}" text-anchor="end" font-size="10" fill="var(--text-3)">T (s) ${+x1.toPrecision(3)}</text>
    <text x="${P + 4}" y="16" font-size="10" fill="var(--text-3)">Sa (g) ${+y1.toPrecision(3)}</text></svg>`;
  return box;
}

/* ================================================================
   generic parameter form from a catalogue schema
   ================================================================ */
function paramRows(F, vals, specs, onChange, idp) {
  const basic = [], adv = [];
  for (const s of specs) {
    if (s.key === "prefix" || s.type === "table" || s.type === "psi") continue;
    let row;
    const id = idp + s.key;
    const read = () => vals[s.key] !== undefined ? vals[s.key] : s.default;
    if (s.type === "choice") row = F.select(vals, s.key, s.choices.map(([v, l]) => [v, l]), { label: s.label, id, onChange, read });
    else if (s.type === "bool") row = F.check(vals, s.key, { label: s.label, id, onChange, read });
    else if (s.type === "text") row = F.text(vals, s.key, { label: s.label, id, onChange, read });
    else {
      const [kind, unit] = QKIND[s.quantity] || ["none", null];
      row = F.num(vals, s.key, {
        label: s.label, id, kind, unit: unit || undefined, onChange,
        min: s.min, max: s.max, gt: s.gt, lt: s.lt, allowNull: !!s.nullable, nullText: "code value",
        def: s.default, read: () => vals[s.key] !== undefined ? vals[s.key] : s.default,
      });
    }
    (s.advanced ? adv : basic).push(row);
  }
  return { basic, adv };
}
/** Only the parameters that differ from their default (what is sent / stored). */
const changedParams = (specs, vals) => Object.fromEntries(Object.entries(vals).filter(([k, v]) => {
  const s = specs.find(x => x.key === k);
  return s && JSON.stringify(v) !== JSON.stringify(s.default);
}));

/* ================================================================
   1. Response spectrum function from code
   ================================================================ */
export function lcOpenSpectrumFromCode(sky, D, { name = null, done } = {}) {
  D.spectrum_functions = D.spectrum_functions || {};
  const src = name ? D.spectrum_functions[name] : null;
  const isNew = !src;
  const draftName = { v: name || lcUnique(D.spectrum_functions, "SPEC-CODE") };
  const st = { code: (src && src.source && src.source.code) || "asce7_22_two_period" };
  const perCode = {};                         // remembered values per code
  if (src && src.source) perCode[st.code] = lcClone(src.source.params || {});
  const vals = () => (perCode[st.code] = perCode[st.code] || {});
  const meta = () => lcSpecCat.find(c => c.code === st.code);
  let last = null, seq = 0;
  const F = lcForm();
  const err = lcEl("div", { class: "dk-err", role: "alert", id: "dsRsFnCodeErr" });
  const chartHost = lcEl("div");
  const summary = lcEl("div", { class: "muted dk-note", id: "dsRsFnCodeSummary" });
  const paramHost = lcEl("div");
  const dmp = { v: src ? src.damping : null };

  const recompute = async () => {
    const my = ++seq;
    const params = paramsNow();
    try {
      const r = await lcSpectrum(sky, st.code, params);
      if (my !== seq) return;
      last = r; err.textContent = "";
      chartHost.textContent = ""; chartHost.appendChild(lcSpark(r.points));
      summary.textContent = `${r.label} — ${r.clause}. ` + Object.entries(r.summary || {})
        .filter(([, v]) => typeof v === "number").map(([k, v]) => `${k} ${fmt(v)}`).join(" · ") + ` · ${r.points.length} points`;
    } catch (e) {
      if (my !== seq) return;
      last = null; err.textContent = e.message || String(e);
    }
  };
  const mprsRows = [];
  const paramsNow = () => {
    const p = lcClone(vals());
    if (st.code === "asce7_22_mprs") p.ordinates = mprsRows.map(r => r.Sa);
    return p;
  };
  const build = () => {
    for (const n of [...paramHost.childNodes]) F.forget(n);
    paramHost.textContent = "";
    const m = meta();
    const v = vals();
    const { basic, adv } = paramRows(F, v, m.params, () => recompute(), "dsRsFnCode_");
    paramHost.appendChild(lcGroup(m.label + " parameters", basic, { id: "dsRsFnCodeParams", note: lcEsc(m.clause) }));
    if (st.code === "asce7_22_mprs") {
      const init = Array.isArray(v.ordinates) && v.ordinates.every(x => typeof x === "number") ? v.ordinates : LC_MPRS_PERIODS.map(() => 0);
      mprsRows.length = 0;
      LC_MPRS_PERIODS.forEach((T, i) => mprsRows.push({ T, Sa: init[i] ?? 0 }));
      const tbl = F.table(mprsRows, [{ key: "T", label: "Period", type: "ro", get: r => fmt(r.T) + " s" }, { key: "Sa", label: "Sa (g)", type: "num" }], {
        id: "dsRsFnCodeMprs", onChange: () => recompute(),
        validateRow: (r, i) => !(r.Sa >= 0) ? `Row ${i + 1}: Sa must be zero or more.` : "",
      });
      const tp = { SDS: 1.0, SD1: 0.6 };
      const fill = lcEl("button", { type: "button", class: "btn btn-small", id: "dsRsFnCodeFill" }, ["Fill from SDS / SD1"]);
      fill.addEventListener("click", () => {
        const t0 = 0.2 * tp.SD1 / tp.SDS, ts = tp.SD1 / tp.SDS;
        LC_MPRS_PERIODS.forEach((T, i) => { mprsRows[i].Sa = +(T <= t0 ? tp.SDS * (0.4 + 0.6 * T / t0) : T <= ts ? tp.SDS : tp.SD1 / T).toPrecision(6); });
        v.basis = "design"; F.touched = true;
        const f = F.fields.find(x => x.type === "table" && x.rowEl === tbl); if (f) f.dirty = true;
        F.refresh(); recompute();
      });
      paramHost.appendChild(lcGroup("USGS multi-period ordinates (ASCE 7-22 §11.4.5.1)", [
        lcNote("Enter the 22 tabulated Sa values for the site class from the USGS Seismic Design Geodatabase (not available offline), " +
          "or fill them from the two-period design values (§11.4.5.2) as a fallback."),
        tbl,
        lcEl("div", { class: "dk-inline" }, [
          F.num(tp, "SDS", { label: "SDS", unit: "g", gt: 0, id: "dsRsFnCodeFillSDS" }),
          F.num(tp, "SD1", { label: "SD1", unit: "g", min: 0, id: "dsRsFnCodeFillSD1" }), fill]),
      ]));
    }
    if (adv.length) paramHost.appendChild(lcAdvanced(adv, { summary: "Advanced — reduction factors, overrides, last period",
      countFn: () => Object.keys(changedParams(m.params, v)).filter(k => (m.params.find(s => s.key === k) || {}).advanced).length }));
    recompute();
  };
  const codeSel = { v: st.code };
  const body = [
    lcGroup("Function", [
      F.text(draftName, "v", { label: "Function Name", id: "dsRsFnCodeName",
        validate: v => (v !== name && Object.prototype.hasOwnProperty.call(D.spectrum_functions, v)) ? `“${v}” is already used — pick another name.` : (/["\\]/.test(v) ? "Names cannot contain quotes or backslashes." : "") }),
      F.select(codeSel, "v", lcSpecCat.map(c => [c.code, c.label]), { label: "Code", id: "dsRsFnCodeCode", onChange: v => { st.code = v; build(); } }),
      F.num(dmp, "v", { label: "Function damping ratio", min: 0, lt: 1, allowNull: true, nullText: "from code", id: "dsRsFnCodeDamp",
        hint: "metadata — CQC uses the load case damping" }),
    ]),
    paramHost,
    lcGroup("Spectrum", [chartHost, summary, err]),
  ];
  build();
  return lcDialog({
    id: "dsRsFnCode", title: "Response Spectrum Function — From Code", iconId: "exleaf-functions", size: "lg", form: F, body,
    intro: "Builds the spectral acceleration (g) versus period from the code's own parameters. The parameters are kept with the function so it can be reopened here.",
    onOk: async () => {
      if (!F.touched && !isNew) { done && done(name); return; }
      await recompute();
      if (!last) return err.textContent || "The spectrum could not be computed.";
      const nn = draftName.v.trim();
      if (name && nn !== name) lcME.renameSpectrumFunction(D, name, nn);
      const damping = dmp.v != null ? dmp.v : (last.params.damping || 0.05);
      D.spectrum_functions[nn] = { name: nn, points: last.points, damping, source: { code: st.code, params: last.params } };
      done && done(nn);
    },
  });
}
export const lcHasSource = f => !!(f && f.source && f.source.code);

/* ================================================================
   2. Default design combinations by code
   ================================================================ */
export function lcOpenDesignCombos(sky, D, { done } = {}) {
  const st = { std: "en1990", includeRs: true };
  const perStd = {};
  const vals = () => (perStd[st.std] = perStd[st.std] || {});
  const meta = () => lcComboCat.find(c => c.standard === st.std);
  const F = lcForm();
  const paramHost = lcEl("div");
  const prev = lcEl("div", { class: "dk-table-wrap", id: "dcComboCodePreview" });
  const warn = lcEl("div", { class: "muted dk-note", id: "dcComboCodeWarn" });
  const err = lcEl("div", { class: "dk-err", role: "alert" });
  let last = null, seq = 0;
  const prefixOf = () => vals().prefix || (meta().params.find(s => s.key === "prefix") || {}).default;
  const recompute = async () => {
    const my = ++seq;
    try {
      const r = await lcCombos(sky, D, st.std, lcClone(vals()), st.includeRs);
      if (my !== seq) return;
      last = r; err.textContent = "";
      prev.textContent = "";
      const t = lcEl("table", { class: "data-table dk-table" });
      t.appendChild(lcEl("thead", {}, [lcEl("tr", {}, [lcEl("th", { class: "txt", text: "Combination" }), lcEl("th", { class: "txt", text: "Definition" })])]));
      const tb = lcEl("tbody");
      for (const c of r.combos) tb.appendChild(lcEl("tr", {}, [lcEl("td", { class: "txt", text: c.name }),
        lcEl("td", { class: "txt dk-mono", text: Object.entries(c.factors).map(([k, f]) => `${fmt(f, 6)}·${k}`).join(" + ") })]));
      t.appendChild(tb);
      prev.appendChild(lcEl("div", { class: "dk-table-scroll", style: "max-height:240px;overflow:auto" }, [t]));
      warn.textContent = `${r.combos.length} combination${r.combos.length === 1 ? "" : "s"}` + (r.warnings.length ? " — " + r.warnings.join(" · ") : "");
    } catch (e) { if (my !== seq) return; last = null; err.textContent = e.message || String(e); }
  };
  const build = () => {
    for (const n of [...paramHost.childNodes]) F.forget(n);
    paramHost.textContent = "";
    const m = meta(), v = vals();
    const { basic } = paramRows(F, v, m.params, () => recompute(), "dcComboCode_");
    basic.unshift(F.text(v, "prefix", { label: "Name prefix", id: "dcComboCode_prefix", read: () => prefixOf(),
      validate: x => /["\\]/.test(x) ? "Names cannot contain quotes or backslashes." : "", onChange: () => recompute() }));
    paramHost.appendChild(lcGroup(m.label, basic, { id: "dcComboCodeParams", note: lcEsc(m.clause) }));
    recompute();
  };
  const stdSel = { v: st.std };
  const body = [
    lcGroup("Design code", [
      F.select(stdSel, "v", lcComboCat.map(c => [c.standard, c.label]), { label: "Code", id: "dcComboCodeStd", onChange: v => { st.std = v; build(); } }),
      F.check(st, "includeRs", { label: "Include response-spectrum cases as seismic (E)", id: "dcComboCodeRs", onChange: () => recompute() }),
      lcNote("Cases are classified by load-pattern TYPE: Dead + Super Dead = G, Live + Reducible Live = L, Roof Live, Snow, Wind, Seismic, Temperature (Define › Load Patterns › Type)."),
    ]),
    paramHost,
    lcGroup("Combinations to add", [prev, warn, err]),
  ];
  build();
  return lcDialog({
    id: "dcComboCode", title: "Add Default Design Combos", iconId: "exleaf-combos", size: "lg", form: F, body,
    intro: "Generates the code's load combinations from the defined load cases. Earlier combinations with the same name prefix are replaced unless another combination uses them.",
    okLabel: "Add",
    onOk: async () => {
      await recompute();
      if (!last) return err.textContent || "No combinations could be generated.";
      if (!last.combos.length) return "No combinations: define a Dead (or Super Dead) load case first.";
      lcApplyCombos(D, last.combos, prefixOf());
      done && done(last.combos);
    },
  });
}

/* ================================================================
   3. Live-load reduction settings
   ================================================================ */
const LLR_CODES = [["", "None (not set)"], ["asce7_16", "ASCE 7-16 / 7-22 §4.7"], ["nbcc2020", "NBCC 2020 4.1.5.8"],
  ["ec1", "EN 1991-1-1 §6.3.1.2 (alpha_A, alpha_n)"], ["is875", "IS 875 (Part 2) §3.2.1"], ["user", "User curve"]];

export function lcOpenLlr(sky) {
  const D = lcDraft(sky);
  const had = D.llr_settings !== undefined;
  const s0 = D.llr_settings ? lcClone(D.llr_settings) : null;
  const st = { code: s0 ? (s0.code || "asce7_16") : "" };
  const params = {};
  for (const [k, v] of Object.entries(LC_LLR_DEFAULTS)) params[k] = { ...lcClone(v), ...(s0 && s0.code === k ? lcClone(s0.params || {}) : {}) };
  const kinds = { reducible: !s0 || !s0.kinds || s0.kinds.includes("reducible_live"), live: !!(s0 && s0.kinds && s0.kinds.includes("live")) };
  const apply = { columns: !s0 || !s0.apply_to || s0.apply_to.includes("columns"), piers: !s0 || !s0.apply_to || s0.apply_to.includes("piers") };
  const piers = [...new Set((D.shells || []).filter(r => r.kind === "wall" && (r.pier || D.auto_pier_walls)).map(r => r.pier || r.uid))];
  const pierRows = piers.map(p => ({ pier: p, A: s0 && s0.pier_areas && s0.pier_areas[p] != null ? s0.pier_areas[p] : null }));
  const curveRows = (params.user.curve || []).map(([x, R]) => ({ x, R }));
  const F = lcForm();
  const host = lcEl("div");
  const prev = lcEl("div", { id: "dsLlrPreview" });
  const note = lcEl("div", { class: "muted dk-note", id: "dsLlrNote" });
  let seq = 0;
  const settings = () => {
    if (!st.code) return null;
    const out = { code: st.code };
    const d = LC_LLR_DEFAULTS[st.code], p = st.code === "user" ? { ...params.user, curve: curveRows.map(r => [r.x, r.R]) } : params[st.code];
    const diff = Object.fromEntries(Object.entries(p).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(d[k])));
    if (Object.keys(diff).length) out.params = diff;
    const ks = [kinds.reducible && "reducible_live", kinds.live && "live"].filter(Boolean);
    if (!(ks.length === 1 && ks[0] === "reducible_live")) out.kinds = ks.length ? ks : ["reducible_live"];
    const ap = [apply.columns && "columns", apply.piers && "piers"].filter(Boolean);
    if (ap.length !== 2) out.apply_to = ap;
    const pa = Object.fromEntries(pierRows.filter(r => r.A != null && r.A > 0).map(r => [r.pier, r.A]));
    if (Object.keys(pa).length) out.pier_areas = pa;
    return out;
  };
  const recompute = async () => {
    const my = ++seq;
    const s = settings();
    prev.textContent = "";
    if (!s) { note.textContent = "No live-load reduction: the reduced-force table uses the ASCE 7-16 defaults."; return; }
    try {
      const f = await lcLlrFactors(sky, D, s);
      if (my !== seq) return;
      const rows = [...Object.entries(f.columns).map(([k, v]) => ["Column", k, v]),
        ...Object.entries(f.piers).flatMap(([k, by]) => Object.entries(by).map(([s2, v]) => ["Pier", `${k} @ ${s2}`, v]))];
      const t = lcEl("table", { class: "data-table dk-table" });
      t.appendChild(lcEl("thead", {}, [lcEl("tr", {}, ["Element", "Name", `Tributary area (${lcU.label("area")})`, "Floors", "RLL factor"].map(h => lcEl("th", { class: "txt", text: h })))]));
      const tb = lcEl("tbody");
      for (const [e, n, v] of rows.slice(0, 200)) tb.appendChild(lcEl("tr", {}, [e, n, lcU.fmt("area", v.At, 2), String(v.n), fmt(v.R, 4)].map(x => lcEl("td", { class: "txt", text: x }))));
      t.appendChild(tb);
      prev.appendChild(lcEl("div", { class: "dk-table-scroll", style: "max-height:220px;overflow:auto" }, [t]));
      note.textContent = `Reducible live cases: ${(f.live_cases || []).join(", ") || "none"}` + ((f.warnings || []).length ? " — " + f.warnings.slice(0, 3).join(" · ") : "");
    } catch (e) { if (my === seq) note.textContent = e.message || String(e); }
  };
  const build = () => {
    for (const n of [...host.childNodes]) F.forget(n);
    host.textContent = "";
    const c = st.code, p = c ? params[c] : null, on = () => recompute();
    const rows = [];
    if (c === "asce7_16") rows.push(F.num(p, "KLL", { label: "Live load element factor KLL", gt: 0, def: 4, id: "dsLlrKLL", onChange: on }));
    if (c === "nbcc2020") rows.push(F.select(p, "occupancy", [["general", "Other occupancies: 0.3 + √(9.8/B), B > 20 m²"], ["assembly", "Assembly / storage / retail: 0.5 + √(20/A), A > 80 m²"]], { label: "Occupancy", id: "dsLlrOcc", onChange: on }));
    if (c === "ec1") {
      rows.push(F.num(p, "psi0", { label: "ψ0", min: 0, max: 1, def: 0.7, id: "dsLlrPsi0", onChange: on }));
      rows.push(F.select(p, "category", ["A", "B", "C", "D", "E"].map(x => [x, "Category " + x]), { label: "Use category", id: "dsLlrCat", onChange: on }));
      rows.push(F.select(p, "method", [["alpha_n", "αn — storeys (Eq. 6.2)"], ["alpha_A", "αA — area (Eq. 6.1)"], ["min", "min(αn, αA)"]], { label: "Columns use", id: "dsLlrMethod", onChange: on }));
      rows.push(F.num(p, "A0", { label: "A0", kind: "area", gt: 0, def: 10, id: "dsLlrA0", onChange: on }));
    }
    if (c === "is875") rows.push(lcNote("Table 3: 1 floor 0 %, 2 floors 10 %, 3: 20 %, 4: 30 %, 5–10: 40 %, over 10: 50 %."));
    if (c === "user") {
      rows.push(F.select(p, "basis", [["area", "Tributary area"], ["floors", "Number of floors"]], { label: "Curve over", id: "dsLlrBasis", onChange: on }));
      rows.push(F.table(curveRows, [{ key: "x", label: "Area (m²) or floors", type: "num" }, { key: "R", label: "RLL factor", type: "num" }], {
        id: "dsLlrCurve", minRows: 1, addLabel: "+ Add point", onChange: on,
        newRow: r => ({ x: (r.length ? r[r.length - 1].x : 0) + 50, R: r.length ? r[r.length - 1].R : 1 }),
        validateRow: (r, i) => !(r.R > 0 && r.R <= 1) ? `Point ${i + 1}: factor must be in (0, 1].` : "",
        validate: rs => rs.some((r, i) => i && r.x <= rs[i - 1].x) ? "x must increase from row to row." : "" }));
    }
    if (c) {
      host.appendChild(lcGroup("Code parameters", rows, { id: "dsLlrParams" }));
      const scope = [
        F.check(kinds, "reducible", { label: "Reduce Reducible Live patterns", id: "dsLlrKindRL", onChange: on }),
        F.check(kinds, "live", { label: "Also reduce Live patterns", id: "dsLlrKindL", onChange: on }),
        F.check(apply, "columns", { label: "Columns", id: "dsLlrCols", onChange: on }),
        F.check(apply, "piers", { label: "Wall piers", id: "dsLlrPiers", onChange: on }),
      ];
      host.appendChild(lcGroup("Apply to", scope));
      if (pierRows.length) host.appendChild(lcAdvanced([F.table(pierRows, [{ key: "pier", label: "Pier", type: "ro", get: r => r.pier },
        { key: "A", label: "Tributary area per floor", type: "num", kind: "area" }], { id: "dsLlrPierAreas", onChange: on })],
      { summary: "Pier tributary areas (blank = plan area / vertical elements)" }));
    }
    recompute();
  };
  const codeSel = { v: st.code };
  const showBtn = lcEl("button", { type: "button", class: "btn btn-small", id: "dsLlrShow", title: "Show Tables › Column And Pier Forces (Live Load Reduced)" }, ["Show Reduced Forces…"]);
  let dlg = null;
  showBtn.addEventListener("click", () => { if (dlg && dlg.ok) dlg.ok.click(); setTimeout(() => lcShowReduced(sky), 0); });
  const body = [
    lcGroup("Live load reduction method", [
      F.select(codeSel, "v", LLR_CODES, { label: "Method", id: "dsLlrCode", onChange: v => { st.code = v; build(); } }),
      lcNote("Applied to column and wall-pier output in the results tables (Show Tables › Live Load Reduction); the analysis results themselves are not changed."),
    ]),
    host,
    lcGroup("Reduction factors", [prev, note]),
  ];
  build();
  dlg = lcDialog({
    id: "dsLlr", title: "Live Load Reduction Factors", glyph: '<path d="M3 15h14M5 15V9m5 6V5m5 10v-3"/>', size: "lg", form: F, body,
    extra: [showBtn],
    onOk: () => {
      if (!F.touched) return;
      const s = settings();
      if (s) D.llr_settings = s; else if (had) delete D.llr_settings;
      lcCommit(sky, D);
    },
  });
  return dlg;
}

export function lcShowReduced(sky) {
  if (!sky.store.results) { sky.toast && sky.toast("Run the analysis first", "Live load reduced forces are post-processed from the results (F5).", "warn", 4000); return; }
  if (sky.openShowTables) sky.openShowTables("live_reduced_forces");
}

/* ================================================================
   init — menus + __sky
   ================================================================ */
export function initLoadCodes(sky) {
  sky.lc = { openSpectrumFromCode: (D, o) => lcOpenSpectrumFromCode(sky, D, o), openDesignCombos: (D, o) => lcOpenDesignCombos(sky, D, o),
    openLlr: () => lcOpenLlr(sky), showReduced: () => lcShowReduced(sky), spectrum: (c, p) => lcSpectrum(sky, c, p) };
  if (sky.menu && sky.menu.add) {
    sky.menu.add("Tools/Code Checks", { label: "Live Load Reduction Factors…", act: "des-lc-llr", hint: "ASCE 7 · NBCC · EC1 · IS 875 · user", fn: () => lcOpenLlr(sky) });
    sky.menu.add("Display/Force Diagrams", { label: "Live Load Reduced Forces", act: "dis-lc-llrforces", needs: "results", fn: () => lcShowReduced(sky) });
  }
}
