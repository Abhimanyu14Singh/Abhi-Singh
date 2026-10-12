/* SkyFrame Wave C — Options › Units… (ETABS: consistent units plus
   per-quantity units and decimal places).

   The dialog edits three model keys (display only — the model stays SI):
     display_units          the consistent set (kN-m … kip-in), as before
     display_units_custom   {kind: unit label} per-quantity overrides from
                            the catalogue (js/wc_unitcat.js = GET
                            /api/units/catalog)
     display_decimals       {kind: decimals 0..10}
   Overrides go through js/units.js (setCustom), so every input, table,
   CSV, report and dialog uses them; "Consistent" (blank) removes an
   override. OK with defaults writes nothing (byte-identical model); one OK
   is one undo step. The previous dialog stays as "Conversion Table…".
   CONTRACT "Wave C interface …" § Units. */

import { el as uEl, esc as uEsc, dkGroup as uGroup, dkDialog as uDialog, dkDraft as uDraft, dkCommit as uCommit, dkNote as uNote } from "./dlgkit.js";
import UU from "./units.js";
import { unitCatalog as uCatalog, MAX_DECIMALS as U_MAXDEC } from "./wc_unitcat.js";

const NAMES = [
  ["Geometry", [["length", "Length (structure, grids, coordinates)"], ["dim", "Section dimensions"], ["small", "Small lengths (thickness, cover)"],
    ["disp", "Displacements"], ["area", "Section area"], ["inertia", "Moment of inertia"], ["secmod", "Section modulus"]]],
  ["Forces", [["force", "Force"], ["moment", "Moment"], ["line_force", "Line load / force per length"], ["line_moment", "Moment per length"]]],
  ["Stresses", [["pressure", "Area load / pressure"], ["stress", "Stress"], ["modulus", "Modulus (E, G)"], ["unit_weight", "Unit weight"], ["subgrade", "Subgrade modulus"]]],
  ["Stiffness", [["stiffness", "Translational stiffness"], ["rot_stiffness", "Rotational stiffness"], ["line_spring", "Line spring"], ["damping_coeff", "Damping coefficient"]]],
  ["Mass", [["mass", "Mass"], ["mass_density", "Mass density"], ["mass_moi", "Mass moment of inertia"], ["mass_per_length", "Mass per length"], ["mass_per_area", "Mass per area"]]],
  ["Dynamics & other", [["accel", "Acceleration"], ["velocity", "Velocity"], ["period", "Time / period"], ["rotation", "Rotation"]]],
  ["Temperature", [["temp", "Temperature"], ["temp_delta", "Temperature change"], ["thermal_coeff", "Thermal coefficient"]]],
];
const SAMPLE = { length: 6, dim: 0.45, small: 0.02, disp: 0.0123, area: 0.18, inertia: 3.2e-3, secmod: 1.4e-2, force: 1234.5, moment: 456.78,
  line_force: 25, line_moment: 12.5, pressure: 4.8, stress: 2.5e5, modulus: 2.5e7, unit_weight: 24, subgrade: 4e4, stiffness: 1.2e5,
  rot_stiffness: 3.4e4, line_spring: 5e3, damping_coeff: 120, mass: 12.3, mass_density: 2.4, mass_moi: 55, mass_per_length: 0.35,
  mass_per_area: 0.5, accel: 9.80665, velocity: 0.3, period: 0.85, rotation: 0.0123, temp: 20, temp_delta: 15, thermal_coeff: 1e-5 };

export function initWcUnits(sky) {
  const S = sky.store;
  const toast = (t, m, k = "info", ms = 4000) => sky.toast && sky.toast(t, m, k, ms);
  const sync = () => { const m = S.model; if (m) UU.setCustom(m.display_units_custom || {}, m.display_decimals || {}); };
  for (const ev of ["sky:model-changed", "sky:model-replaced", "sky:model-synced"]) document.addEventListener(ev, sync);
  sync();
  const legacy = sky.openUnitsDialog;

  function open() {
    const m = S.model;
    if (!m) return null;
    const cat = uCatalog();
    const V = { set: UU.getUnits(), units: { ...(m.display_units_custom || {}) }, decimals: { ...(m.display_decimals || {}) } };
    const setSel = uEl("select", { id: "wcUnitsSet", class: "wc-units-set" }, UU.UNIT_SET_NAMES.map(n => uEl("option", { value: n, text: `${n}  —  ${UU.UNIT_SETS[n].label}` })));
    setSel.value = V.set;
    const tbody = uEl("tbody");
    const table = uEl("table", { class: "data-table wc-units-tbl", id: "wcUnitsTbl" }, [
      uEl("thead", {}, [uEl("tr", {}, [uEl("th", { class: "txt", text: "Quantity" }), uEl("th", { class: "txt", text: "Units" }),
        uEl("th", { class: "txt", text: "Decimals" }), uEl("th", { class: "txt", text: "Example" })])]), tbody]);
    const rows = {};
    const consistentLabel = k => UU.label(k, V.set) || "–";
    // display of a sample with the DRAFT choices (not yet applied)
    const sample = k => {
      const lab = V.units[k], row = lab ? cat[k].find(r => r.label === lab) : null;
      const si = SAMPLE[k];
      let v, u;
      if (row) { v = (k === "temp" && lab === "°F") ? si * 1.8 + 32 : si / row.si; u = lab; }
      else { v = UU.toDisplay(k, si, V.set); u = UU.label(k, V.set); }
      const d = V.decimals[k] != null ? V.decimals[k] : (row ? Math.max(0, Math.min(8, 3 - Math.floor(Math.log10(Math.abs(v) || 1)))) : UU.dec(k, 3, V.set));
      return `${(+v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })} ${u}`;
    };
    for (const [grp, items] of NAMES) {
      tbody.appendChild(uEl("tr", { class: "wc-units-grp" }, [uEl("td", { class: "txt", colspan: "4", text: grp })]));
      for (const [k, name] of items) {
        if (!cat[k]) continue;
        const sel = uEl("select", { id: "wcUnit_" + k, "data-kind": k }, [uEl("option", { value: "", text: `Consistent (${consistentLabel(k)})` }),
          ...cat[k].map(r => uEl("option", { value: r.label, text: r.label }))]);
        sel.value = V.units[k] || "";
        const dec = uEl("input", { id: "wcDec_" + k, type: "number", min: "0", max: String(U_MAXDEC), step: "1", placeholder: "auto", "data-kind": k, class: "wc-dec" });
        dec.value = V.decimals[k] != null ? String(V.decimals[k]) : "";
        const ex = uEl("td", { class: "txt muted wc-units-ex", id: "wcEx_" + k });
        const tr = uEl("tr", { "data-kind": k }, [uEl("td", { class: "txt", text: name }), uEl("td", { class: "txt" }, [sel]), uEl("td", { class: "txt" }, [dec]), ex]);
        sel.addEventListener("change", () => { if (sel.value) V.units[k] = sel.value; else delete V.units[k]; ex.textContent = sample(k); });
        dec.addEventListener("change", () => {
          const t = dec.value.trim();
          if (t === "") { delete V.decimals[k]; dec.classList.remove("is-invalid"); }
          else { const n = Number(t); if (Number.isInteger(n) && n >= 0 && n <= U_MAXDEC) { V.decimals[k] = n; dec.classList.remove("is-invalid"); } else dec.classList.add("is-invalid"); }
          ex.textContent = sample(k);
        });
        rows[k] = { sel, dec, ex };
        ex.textContent = sample(k);
        tbody.appendChild(tr);
      }
    }
    setSel.addEventListener("change", () => {
      V.set = setSel.value;
      for (const [k, r] of Object.entries(rows)) { r.sel.options[0].textContent = `Consistent (${consistentLabel(k)})`; r.ex.textContent = sample(k); }
    });
    const reset = uEl("button", { type: "button", class: "btn btn-small", id: "wcUnitsReset", text: "Reset to Consistent" });
    reset.addEventListener("click", () => { V.units = {}; V.decimals = {}; for (const [k, r] of Object.entries(rows)) { r.sel.value = ""; r.dec.value = ""; r.dec.classList.remove("is-invalid"); r.ex.textContent = sample(k); } });
    const tblBtn = uEl("button", { type: "button", class: "btn btn-small", id: "wcUnitsTable", text: "Conversion Table…" });
    tblBtn.addEventListener("click", () => { dlg.close(); legacy && legacy(); });
    const body = [
      uGroup("Consistent Units", [uEl("div", { class: "dk-row" }, [uEl("label", { class: "dk-label", for: "wcUnitsSet", text: "Unit system" }), uEl("div", { class: "dk-ctl" }, [setSel]), uEl("span", { class: "dk-unit" })])]),
      uGroup("Per-Quantity Units and Decimals", [uEl("div", { class: "table-scroll wc-units-scroll" }, [table])],
        { note: "Blank = the consistent unit and automatic decimals. Display only: the model is always stored in SI." }),
      uNote("Every input, table, diagram, CSV and report uses these units (js/units.js)."),
    ];
    const dlg = uDialog({ id: "wcUnitsDlg", title: "Units", iconId: "units", size: "lg", body, extra: [reset, tblBtn],
      intro: "ETABS Options › Units: a consistent unit set plus optional per-quantity units and decimal places.",
      onOk: () => {
        if (tbody.querySelector(".is-invalid")) return `Decimals must be whole numbers 0–${U_MAXDEC}.`;
        const D = uDraft(sky);
        const same = (a, b) => JSON.stringify(a || {}) === JSON.stringify(b || {});
        if ((D.display_units || UU.DEFAULT_UNITS) !== V.set) D.display_units = V.set;
        if (!same(D.display_units_custom, V.units)) { if (Object.keys(V.units).length) D.display_units_custom = { ...V.units }; else delete D.display_units_custom; }
        if (!same(D.display_decimals, V.decimals)) { if (Object.keys(V.decimals).length) D.display_decimals = { ...V.decimals }; else delete D.display_decimals; }
        uCommit(sky, D);
        UU.setUnits(V.set);
        sync();
        if (sky.etabs && sky.etabs.refresh) sky.etabs.refresh();
      } });
    return dlg;
  }
  sky.openUnitsDialog = open;

  /** Browser catalogue = backend catalogue (live) — for the tests / status. */
  async function catalogCheck() {
    if (S.mock) return { ok: true, mock: true };
    const r = await fetch("/api/units/catalog");
    const srv = await r.json();
    const loc = uCatalog(), diffs = [];
    for (const k of new Set([...Object.keys(srv.kinds || {}), ...Object.keys(loc)])) {
      const a = (srv.kinds || {})[k] || [], b = loc[k] || [];
      if (a.length !== b.length) { diffs.push(k); continue; }
      a.forEach((x, i) => { if (x.label !== b[i].label || Math.abs(x.si - b[i].si) > 1e-12 * Math.abs(x.si)) diffs.push(`${k}:${x.label}`); });
    }
    if (diffs.length) console.warn("units catalogue differs from the backend", diffs);
    return { ok: !diffs.length, diffs };
  }
  sky.wc.units = { open, sync, catalog: uCatalog, catalogCheck, legacy };
  void toast; void uEsc;
}
