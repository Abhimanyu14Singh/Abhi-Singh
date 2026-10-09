/* SkyFrame — ETABS-style analysis-setup dialogs (contract v1.13):
     Analyze → Set Load Cases to Run…          model.cases_not_run
     Analyze → Set Active Degrees of Freedom…  model.active_dof
     Define  → Mass Source…                    model.mass_options / mass_source /
                                               mass_source_mode
     Options → Units…                          model.display_units
   Every dialog edits a DRAFT and writes the model only on OK (Cancel / Esc /
   backdrop click discard), then calls ctx.markDirty(). Built on the shared
   .modal-backdrop/.modal markup; no frameworks. */

import * as ME from "./modeledit.js";
import U from "./units.js";
import { icon } from "./icons.js";

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ------------------------------------------------ generic dialog shell */
const open = new Map();          // id → {close}

export function dialog(id, { title, iconId, wide = false, narrow = false, body, foot, onClose }) {
  closeDialog(id);
  const back = document.createElement("div");
  back.className = "modal-backdrop sky-dlg";
  back.id = id;
  const box = document.createElement("div");
  box.className = "modal" + (wide ? " modal-wide" : "") + (narrow ? " modal-narrow" : "");
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-labelledby", id + "Title");
  const head = document.createElement("header");
  head.className = "modal-head";
  head.innerHTML = `<h2 id="${id}Title" class="dlg-title">${iconId ? icon(iconId, "dlg-ico") : ""}<span>${esc(title)}</span></h2>`;
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
  const close = () => {
    if (!open.has(id)) return;
    open.delete(id);
    document.removeEventListener("keydown", onKey, true);
    back.remove();
    onClose && onClose();
  };
  const onKey = e => {
    if (e.key === "Escape") { e.stopImmediatePropagation(); e.preventDefault(); close(); }
  };
  document.addEventListener("keydown", onKey, true);
  x.addEventListener("click", close);
  back.addEventListener("mousedown", e => { if (e.target === back) close(); });
  open.set(id, { close, el: back });
  return { el: back, close };
}
export function closeDialog(id) { const d = open.get(id); if (d) d.close(); }
export const isDialogOpen = id => open.has(id);

export function btn(label, cls, onClick, title) {
  const b = document.createElement("button");
  b.className = "btn" + (cls ? " " + cls : "");
  b.textContent = label;
  if (title) b.title = title;
  b.addEventListener("click", onClick);
  return b;
}
export function footBar(note, buttons) {
  const wrap = document.createElement("div");
  wrap.className = "dlg-foot";
  const n = document.createElement("span");
  n.className = "muted dlg-foot-note";
  n.textContent = note || "";
  const b = document.createElement("div");
  b.className = "modal-btns";
  b.append(...buttons);
  wrap.append(n, b);
  return { wrap, note: n };
}
export function errorLine() {
  const p = document.createElement("p");
  p.className = "field-error hidden dlg-error";
  return p;
}
export const showError = (p, msg) => { p.textContent = msg || ""; p.classList.toggle("hidden", !msg); };

/* ================================================================
   Set Load Cases to Run
   ================================================================ */
const STATUS_LABEL = {
  finished: "Finished", not_run: "Not Run", run_as_dependency: "Run as Dependency",
  failed: "Failed", skipped: "Skipped",
};
/** Last-run status of a case from the current results (null results → not run). */
export function caseRunStatus(results, name, kind) {
  if (!results) return "not_run";
  const cs = results.case_status;
  if (cs && cs[name]) return cs[name];
  // older backends: infer from presence in the result blocks
  const has = {
    static: () => !!(results.cases && results.cases[name]),
    modal: () => !!(results.modal && results.modal.periods && results.modal.periods.length),
    rs: () => !!(results.rs_cases && results.rs_cases[name]),
    th: () => !!(results.th_cases && results.th_cases[name]),
    pushover: () => !!(results.pushover && results.pushover[name]),
    buckling: () => !!(results.buckling && results.buckling[name]),
    staged: () => !!(results.staged && results.staged[name]),
    steady_state: () => !!(results.steady_state && results.steady_state[name]),
    psd: () => !!(results.psd && results.psd[name]),
    nonlinear_static: () => !!(results.nonlinear_static && results.nonlinear_static[name]),   // G3
  }[kind];
  return has && has() ? "finished" : "not_run";
}

export function openCasesToRun(ctx) {
  const m = ctx.store.model;
  if (!m) return null;
  const cases = ME.allAnalysisCases(m);
  const draft = new Set(m.cases_not_run || []);
  const selected = new Set();
  let anchor = -1;

  const body = document.createElement("div");
  body.className = "dlg-cases";
  const intro = document.createElement("p");
  intro.className = "muted dlg-intro";
  intro.innerHTML = "Pick which load cases the next analysis runs. A case set to " +
    "<b>Do not Run</b> is skipped unless another running case needs it " +
    "(it then runs as a dependency); combinations that reference a skipped case are skipped too.";
  body.appendChild(intro);

  const tableWrap = document.createElement("div");
  tableWrap.className = "table-scroll dlg-table-wrap";
  const table = document.createElement("table");
  table.className = "data-table dlg-case-table";
  table.id = "casesRunTable";
  tableWrap.appendChild(table);
  body.appendChild(tableWrap);

  const side = document.createElement("div");
  side.className = "dlg-case-actions";
  const toggleSel = btn("Run/Do Not Run Case", "btn-small", () => {
    if (!selected.size) { ctx.toast && ctx.toast("Set Load Cases to Run", "Select one or more cases first.", "info", 3000); return; }
    for (const n of selected) draft.has(n) ? draft.delete(n) : draft.add(n);
    render();
  }, "Toggle Run / Do not Run for the selected case(s)");
  toggleSel.id = "casesRunToggle";
  const toggleAll = btn("Run/Do Not Run All", "btn-small", () => {
    const anyRun = cases.some(c => !draft.has(c.name));
    draft.clear();
    if (anyRun) for (const c of cases) draft.add(c.name);
    render();
  }, "Set every case to Do not Run (or back to Run when none runs)");
  toggleAll.id = "casesRunToggleAll";
  side.append(toggleSel, toggleAll);
  body.appendChild(side);

  const comboNote = document.createElement("p");
  comboNote.className = "muted dlg-combo-note";
  body.appendChild(comboNote);

  const results = ctx.store.results;
  const render = () => {
    const runCount = cases.filter(c => !draft.has(c.name)).length;
    table.innerHTML = `<thead><tr><th class="txt">Case</th><th class="txt">Type</th>
      <th class="txt">Status</th><th class="txt">Action</th></tr></thead>`;
    const tb = document.createElement("tbody");
    cases.forEach((c, i) => {
      const tr = document.createElement("tr");
      const off = draft.has(c.name);
      tr.className = "dlg-case-row" + (off ? " is-not-run" : "") + (selected.has(c.name) ? " is-sel" : "");
      tr.dataset.case = c.name;
      const st = caseRunStatus(results, c.name, c.kind);
      tr.innerHTML = `<td class="txt"><b>${esc(c.name)}</b></td>
        <td class="txt dim">${esc(c.type)}</td>
        <td class="txt"><span class="run-st run-st-${st}">${esc(STATUS_LABEL[st] || st)}</span></td>
        <td class="txt"><button class="run-act${off ? " is-off" : ""}" title="Click to toggle">${off ? "Do not Run" : "Run"}</button></td>`;
      tr.addEventListener("click", e => {
        if (e.target.closest(".run-act")) {
          off ? draft.delete(c.name) : draft.add(c.name);
          render();
          return;
        }
        if (e.shiftKey && anchor >= 0) {
          const [a, b] = [Math.min(anchor, i), Math.max(anchor, i)];
          if (!(e.ctrlKey || e.metaKey)) selected.clear();
          for (let k = a; k <= b; k++) selected.add(cases[k].name);
        } else if (e.ctrlKey || e.metaKey) {
          selected.has(c.name) ? selected.delete(c.name) : selected.add(c.name);
          anchor = i;
        } else {
          selected.clear(); selected.add(c.name); anchor = i;
        }
        render();
      });
      tr.addEventListener("dblclick", e => {
        if (e.target.closest(".run-act")) return;
        draft.has(c.name) ? draft.delete(c.name) : draft.add(c.name);
        render();
      });
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    // combos that the draft would skip
    const skipped = Object.entries(m.combos || {})
      .filter(([, cb]) => Object.keys(cb.cases || {}).some(cn => draft.has(cn)))
      .map(([n]) => n);
    comboNote.innerHTML = skipped.length
      ? `⚠ ${skipped.length} combination${skipped.length > 1 ? "s" : ""} will be <b>skipped</b>: ${skipped.map(esc).join(", ")}`
      : "All load combinations will be evaluated.";
    fb.note.textContent = `${runCount} of ${cases.length} cases set to run`;
  };

  const commit = () => {
    m.cases_not_run = cases.map(c => c.name).filter(n => draft.has(n));
    ctx.markDirty();
    ctx.onChange && ctx.onChange("cases_not_run");
  };
  const fb = footBar("", [
    btn("Run Now", "btn-run", () => { commit(); dlg.close(); ctx.run && ctx.run(); }, "Apply and run the analysis now"),
    btn("Cancel", "", () => dlg.close()),
    btn("OK", "btn-run", () => { commit(); dlg.close(); }),
  ]);
  fb.wrap.querySelector(".btn-run").id = "casesRunNow";
  fb.wrap.querySelectorAll(".btn")[1].id = "casesRunCancel";
  fb.wrap.querySelectorAll(".btn")[2].id = "casesRunOk";
  const dlg = dialog("casesRunModal", {
    title: "Set Load Cases to Run", iconId: "cases-run", wide: true, body, foot: fb.wrap,
  });
  render();
  return dlg;
}

/* ================================================================
   Set Active Degrees of Freedom
   ================================================================ */
export function openActiveDof(ctx) {
  const m = ctx.store.model;
  if (!m) return null;
  const draft = new Set(ME.normalizeActiveDof(m.active_dof));
  const body = document.createElement("div");
  body.className = "dlg-dof";

  const presets = document.createElement("div");
  presets.className = "dof-presets";
  const pH = document.createElement("div");
  pH.className = "dlg-group-title"; pH.textContent = "Fast DOFs";
  body.appendChild(pH);
  const presetBtns = {};
  for (const [k, p] of Object.entries(ME.DOF_PRESETS)) {
    const b = document.createElement("button");
    b.className = "dof-preset";
    b.dataset.preset = k;
    b.innerHTML = `${dofGlyph(k)}<span>${esc(p.label)}</span>`;
    b.addEventListener("click", () => {
      draft.clear(); p.dofs.forEach(d => draft.add(d)); sync();
    });
    presetBtns[k] = b;
    presets.appendChild(b);
  }
  body.appendChild(presets);

  const cH = document.createElement("div");
  cH.className = "dlg-group-title"; cH.textContent = "Available DOFs";
  body.appendChild(cH);
  const grid = document.createElement("div");
  grid.className = "dof-grid";
  const boxes = {};
  for (const d of ME.DOF_NAMES) {
    const l = document.createElement("label");
    l.className = "dof-chk";
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.dataset.dof = d;
    cb.addEventListener("change", () => { cb.checked ? draft.add(d) : draft.delete(d); sync(); });
    l.append(cb, document.createTextNode(" " + d));
    boxes[d] = cb;
    grid.appendChild(l);
  }
  body.appendChild(grid);
  const err = errorLine();
  body.appendChild(err);
  const note = document.createElement("p");
  note.className = "muted dlg-intro";
  note.textContent = "Inactive DOFs are restrained at every joint — XZ Plane analyses a 2D frame in the X–Z plane (UX, UZ, RY).";
  body.appendChild(note);

  const sync = () => {
    for (const d of ME.DOF_NAMES) boxes[d].checked = draft.has(d);
    const key = ME.dofPresetOf([...draft]);
    for (const [k, b] of Object.entries(presetBtns)) b.classList.toggle("is-active", k === key && draft.size > 0);
    showError(err, draft.size ? "" : "Select at least one degree of freedom.");
    fb.note.textContent = draft.size ? `Active: ${ME.DOF_NAMES.filter(d => draft.has(d)).join(", ")}` : "";
  };
  const fb = footBar("", [
    btn("Cancel", "", () => dlg.close()),
    btn("OK", "btn-run", () => {
      if (!draft.size) { sync(); return; }
      m.active_dof = ME.DOF_NAMES.filter(d => draft.has(d));
      ctx.markDirty();
      ctx.onChange && ctx.onChange("active_dof");
      dlg.close();
    }),
  ]);
  fb.wrap.querySelectorAll(".btn")[1].id = "dofOk";
  const dlg = dialog("dofModal", {
    title: "Set Active Degrees of Freedom", iconId: "active-dof", narrow: true, body, foot: fb.wrap,
  });
  sync();
  return dlg;
}
function dofGlyph(k) {
  const g = {
    "3D": '<path d="M10 3 L16 6.5 L16 13.5 L10 17 L4 13.5 L4 6.5 Z"/><path d="M4 6.5 L10 10 L16 6.5 M10 10 V17"/>',
    XZ: '<path d="M3 16 H17 M3 16 V3"/><rect x="6" y="7" width="7" height="6"/>',
    YZ: '<path d="M3 16 H17 M3 16 V3"/><path d="M6 13 L9 7 L15 7 L12 13 Z"/>',
    XY: '<path d="M3 13 L8 8 L17 8 L12 13 Z"/><path d="M6 11 H14"/>',
  }[k] || "";
  return `<svg class="dof-ico" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${g}</svg>`;
}

/* ================================================================
   Mass Source
   ================================================================ */
export function openMassSource(ctx) {
  const m = ctx.store.model;
  if (!m) return null;
  const opts = ME.normalizeMassOptions(m.mass_options);
  const rows = Object.entries(m.mass_source || {}).map(([p, f]) => [p, f]);
  let mode = m.mass_source_mode === "element_self_mass" ? "element_self_mass" : "weight";
  const pats = ME.patternNames(m);

  const body = document.createElement("div");
  body.className = "dlg-mass";

  /* ---- Mass Source group */
  const g1 = document.createElement("fieldset");
  g1.className = "dlg-group";
  g1.innerHTML = `<legend>Mass Source</legend>`;
  const chk = (key, text, title) => {
    const l = document.createElement("label");
    l.className = "dlg-chk";
    if (title) l.title = title;
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = !!opts[key]; cb.dataset.opt = key;
    cb.addEventListener("change", () => { opts[key] = cb.checked; sync(); });
    l.append(cb, document.createTextNode(" " + text));
    return { l, cb };
  };
  const cSelf = chk("self_mass", "Element Self Mass and Additional Mass",
    "Mass of every element from its material (plus additional nodal / story masses)");
  const modeRow = document.createElement("label");
  modeRow.className = "dlg-sub-row";
  modeRow.innerHTML = `<span>Self-mass from</span>`;
  const modeSel = document.createElement("select");
  modeSel.id = "massModeSelect";
  modeSel.innerHTML = `<option value="weight">Weight / g</option>
    <option value="element_self_mass">Material Mass Density</option>`;
  modeSel.value = mode;
  modeSel.addEventListener("change", () => { mode = modeSel.value; });
  modeRow.appendChild(modeSel);
  const cPat = chk("patterns", "Specified Load Patterns",
    "Mass = Σ pattern load × multiplier / g (gravity loads converted to mass)");

  const tableWrap = document.createElement("div");
  tableWrap.className = "mass-pat-table";
  const addRow = document.createElement("div");
  addRow.className = "mass-pat-add";

  g1.append(cSelf.l, modeRow, cPat.l, tableWrap, addRow);
  body.appendChild(g1);

  /* ---- Mass Options group */
  const g2 = document.createElement("fieldset");
  g2.className = "dlg-group";
  g2.innerHTML = `<legend>Mass Options</legend>`;
  const cLat = chk("include_lateral", "Include Lateral Mass", "Mass in the horizontal X / Y directions");
  const cVert = chk("include_vertical", "Include Vertical Mass", "Mass in the vertical Z direction");
  const cLump = chk("lump_at_stories", "Lump Lateral Mass at Story Levels",
    "Lateral mass lumped at the story (diaphragm) levels");
  g2.append(cLat.l, cVert.l, cLump.l);
  body.appendChild(g2);

  const err = errorLine();
  body.appendChild(err);

  const renderTable = () => {
    tableWrap.textContent = "";
    const head = document.createElement("div");
    head.className = "mass-pat-row head";
    head.innerHTML = `<span>Load Pattern</span><span>Multiplier</span><span></span>`;
    tableWrap.appendChild(head);
    rows.forEach((r, i) => {
      const row = document.createElement("div");
      row.className = "mass-pat-row";
      row.dataset.i = String(i);
      const sel = document.createElement("select");
      const pool = [...new Set([r[0], ...pats.filter(p => p === r[0] || !rows.some(x => x[0] === p))])];
      sel.innerHTML = pool.map(p => `<option value="${esc(p)}"${p === r[0] ? " selected" : ""}>${esc(p)}</option>`).join("");
      sel.addEventListener("change", () => { r[0] = sel.value; renderTable(); });
      const num = document.createElement("input");
      num.type = "number"; num.step = "0.05"; num.value = String(r[1]);
      num.addEventListener("change", () => {
        const v = parseFloat(num.value);
        if (isFinite(v)) r[1] = v; else num.value = String(r[1]);
      });
      const del = document.createElement("button");
      del.className = "chip-x"; del.textContent = "✕"; del.title = "Remove pattern";
      del.addEventListener("click", () => { rows.splice(i, 1); renderTable(); });
      sel.disabled = num.disabled = del.disabled = !opts.patterns;
      row.append(sel, num, del);
      tableWrap.appendChild(row);
    });
    addRow.textContent = "";
    const free = pats.filter(p => !rows.some(r => r[0] === p));
    const add = btn("+ Add", "btn-small", () => {
      if (!free.length) return;
      rows.push([free[0], 1.0]);
      renderTable();
    }, free.length ? "Add a load pattern to the mass source" : "Every pattern is already listed");
    add.id = "massAddPattern";
    add.disabled = !opts.patterns || !free.length;
    addRow.appendChild(add);
  };

  const validate = () => {
    if (!opts.include_lateral && !opts.include_vertical)
      return "Include at least one of Lateral Mass or Vertical Mass.";
    if (!opts.self_mass && !opts.patterns)
      return "Select at least one mass source (element self mass or load patterns).";
    if (opts.patterns && !rows.length)
      return "Add at least one load pattern, or uncheck Specified Load Patterns.";
    return "";
  };
  const sync = () => {
    modeSel.disabled = !opts.self_mass;
    cLump.cb.disabled = !opts.include_lateral;
    renderTable();
    showError(err, validate());
  };

  const fb = footBar("Mass drives modal, response-spectrum and time-history inertia.", [
    btn("Cancel", "", () => dlg.close()),
    btn("OK", "btn-run", () => {
      const msg = validate();
      if (msg) { showError(err, msg); return; }
      m.mass_options = { ...ME.normalizeMassOptions(m.mass_options), ...opts };
      if (rows.length) m.mass_source = Object.fromEntries(rows.map(([p, f]) => [p, f]));
      m.mass_source_mode = mode;
      ctx.markDirty();
      ctx.onChange && ctx.onChange("mass_source");
      dlg.close();
    }),
  ]);
  fb.wrap.querySelectorAll(".btn")[1].id = "massOk";
  const dlg = dialog("massModal", {
    title: "Mass Source Data", iconId: "mass-source", body, foot: fb.wrap,
  });
  sync();
  return dlg;
}

/* ================================================================
   Units (Options → Units…)
   ================================================================ */
export function openUnitsDialog(ctx) {
  let pick = U.getUnits();
  const body = document.createElement("div");
  body.className = "dlg-units";
  const intro = document.createElement("p");
  intro.className = "muted dlg-intro";
  intro.textContent = "Display units for every input, table, diagram, CSV and report. " +
    "The model itself is always stored in SI (kN, m, kPa, tonne, °C, s) — switching units never changes it.";
  body.appendChild(intro);
  const list = document.createElement("div");
  list.className = "units-list";
  for (const n of U.UNIT_SET_NAMES) {
    const l = document.createElement("label");
    l.className = "units-opt";
    const r = document.createElement("input");
    r.type = "radio"; r.name = "unitsPick"; r.value = n; r.checked = n === pick;
    r.addEventListener("change", () => { pick = n; renderTable(); });
    l.append(r, document.createTextNode(` ${n}`));
    const s = document.createElement("span");
    s.className = "muted"; s.textContent = U.UNIT_SETS[n].label;
    l.appendChild(s);
    list.appendChild(l);
  }
  body.appendChild(list);
  const tableWrap = document.createElement("div");
  tableWrap.className = "table-scroll";
  const table = document.createElement("table");
  table.className = "data-table units-table";
  tableWrap.appendChild(table);
  body.appendChild(tableWrap);
  const src = document.createElement("p");
  src.className = "muted dlg-intro units-src";
  body.appendChild(src);
  const SHOW = [["length", "Length"], ["dim", "Section dimension"], ["disp", "Displacement"],
    ["force", "Force"], ["moment", "Moment"], ["line_force", "Line load"],
    ["pressure", "Area load / stress / modulus"], ["unit_weight", "Unit weight"],
    ["mass", "Mass"], ["mass_density", "Mass density"], ["accel", "Acceleration"],
    ["stiffness", "Stiffness"], ["temp_delta", "Temperature change"],
    ["thermal_coeff", "Thermal coefficient"]];
  const renderTable = () => {
    table.innerHTML = `<thead><tr><th class="txt">Quantity</th><th class="txt">Unit</th>
      <th>1 SI unit =</th></tr></thead><tbody>` +
      SHOW.map(([k, lbl]) => {
        const f = U.factor(k, pick);
        return `<tr><td class="txt">${esc(lbl)}</td><td class="txt"><b>${esc(U.label(k, pick))}</b></td>
          <td>${esc(Number(f.toPrecision(6)).toString())}</td></tr>`;
      }).join("") + `</tbody>`;
  };
  renderTable();
  if (ctx.fetchUnits) {
    src.textContent = "Conversion table: client (exact factors)…";
    ctx.fetchUnits().then(t => {
      src.textContent = t && t._source === "server"
        ? "Conversion table: client (exact factors) · backend GET /api/units available"
        : "Conversion table: client (exact factors) · offline";
    }).catch(() => { src.textContent = "Conversion table: client (exact factors) · offline"; });
  }
  const fb = footBar("", [
    btn("Cancel", "", () => dlg.close()),
    btn("OK", "btn-run", () => { ctx.setUnits(pick); dlg.close(); }),
  ]);
  fb.wrap.querySelectorAll(".btn")[1].id = "unitsOk";
  const dlg = dialog("unitsModal", { title: "Units", iconId: "units", narrow: true, body, foot: fb.wrap });
  return dlg;
}
