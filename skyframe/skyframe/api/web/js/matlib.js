/* SkyFrame — Material Library browser (v1.21, CONTRACT "v1.21 material
   library").

     Define › Material Library…                  (act "def-matlib", standalone)
     Define Materials › "Add from Library…"      (imports into that dialog's draft)

   Standard (US · Eurocode · IS · BS · AS/NZS) → kind (Steel, Concrete,
   Rebar, Tendon, Aluminium, Cold-formed) → grade list with key properties in
   the current display units → multi-select import. A name already in the
   model gets a "-2" suffix, or replaces it when "Replace existing" is
   chosen. The info line shows the source clause / formula of the focused
   grade, and its stress–strain backbone is previewed with the existing
   plot (js/sscurve.js, POST /api/materials/curve; mock: mockMaterialCurve).

   Data: GET /api/material_db/standards + /materials?standard= (live), or
   js/mock_matlib.js (?mock=1 / endpoint unavailable). The imported material
   is the exact Material.to_dict() the backend builds from the record, so a
   save round-trips byte-identically. Imports go into a dlgkit draft;
   standalone they are committed on Import, inside Define Materials on that
   list's OK. */

import mlU from "./units.js";
import { el as mlEl, esc as mlEsc, clone as mlClone, dkDialog as mlDialog, dkGroup as mlGroup, dkDraft as mlDraft, dkCommit as mlCommit } from "./dlgkit.js";
import { ssDrawPlot as mlDrawPlot } from "./sscurve.js";
import { mlMockStandards, mlMockMaterials, ML_KINDS as mlKINDS } from "./mock_matlib.js";

const ML_KIND_LABEL = { steel: "Steel", concrete: "Concrete", rebar: "Rebar", tendon: "Tendon", aluminum: "Aluminium", cold_formed: "Cold-formed" };
const ML_CSS_ID = "mlMatLibCss";
const ML_CSS = `
.dk-dlg .ml-bar { display: grid; grid-template-columns: 1.6fr 1fr 1fr; gap: 8px; align-items: end; }
.dk-dlg .ml-bar label { display: flex; flex-direction: column; gap: 3px; font-size: 12px; }
.dk-dlg .ml-main { display: grid; grid-template-columns: minmax(0, 1fr) 250px; gap: 10px; margin-top: 8px; }
.dk-dlg .ml-table { max-height: 46vh; overflow: auto; border: 1px solid var(--border-soft); border-radius: 6px; min-height: 160px; }
.dk-dlg .ml-table table { width: 100%; }
.dk-dlg .ml-table tbody tr { cursor: pointer; }
.dk-dlg .ml-table tbody tr:hover td { background: var(--surface-3); }
.dk-dlg .ml-table tbody tr.is-focus td { background: var(--accent-ghost); }
.dk-dlg .ml-table td.num, .dk-dlg .ml-table th.num { text-align: right; white-space: nowrap; }
.dk-dlg .ml-table .ml-clash { color: var(--warn, #b7791f); font-size: 11px; margin-left: 4px; }
.dk-dlg .ml-table .ml-group { color: var(--muted, #888); font-size: 11px; }
.dk-dlg .ml-info { font-size: 12px; margin: 8px 0 0; line-height: 1.4; min-height: 2.8em; }
.dk-dlg .ml-info b { font-weight: 600; }
.dk-dlg .ml-side .ml-props { font-size: 12px; display: grid; grid-template-columns: auto 1fr; gap: 2px 8px; margin-top: 6px; }
.dk-dlg .ml-side .ml-props span:nth-child(odd) { color: var(--muted, #888); }
.dk-dlg .ml-clashrow { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; font-size: 12px; }
@media (max-width: 760px) { .dk-dlg .ml-main { grid-template-columns: 1fr; } .dk-dlg .ml-bar { grid-template-columns: 1fr; } }
`;
function mlEnsureCss() {
  if (document.getElementById(ML_CSS_ID)) return;
  document.head.appendChild(mlEl("style", { id: ML_CSS_ID, text: ML_CSS }));
}

/* ---- data access (live endpoint → mock fallback) ---- */
const mlCache = { standards: null, mats: {} };
async function mlGet(sky, path) {
  const res = await fetch(path);
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}
const mlUseMock = sky => !!(sky.store && sky.store.mock);
export async function mlStandards(sky) {
  if (mlCache.standards) return mlCache.standards;
  let st = null;
  if (!mlUseMock(sky)) {
    try { st = await mlGet(sky, "/api/material_db/standards"); }
    catch (e) { console.warn("Material library endpoint unavailable, using the bundled copy:", e.message); }
  }
  mlCache.standards = st || await mlMockStandards();
  return mlCache.standards;
}
export async function mlMaterials(sky, std) {
  if (mlCache.mats[std]) return mlCache.mats[std];
  let r = null;
  if (!mlUseMock(sky)) {
    try { r = await mlGet(sky, `/api/material_db/materials?standard=${encodeURIComponent(std)}`); }
    catch (e) { console.warn("Material library endpoint unavailable, using the bundled copy:", e.message); }
  }
  r = r || await mlMockMaterials(std);
  mlCache.mats[std] = r.materials;
  return r.materials;
}

/* ---- display helpers (units.js; the store stays SI) ---- */
function mlNum(kind, si, sig = 5) {
  if (si == null || !isFinite(si)) return "—";
  const v = mlU.toDisplay(kind, si);
  if (v !== 0 && Math.abs(v) < 0.01) return v.toExponential(3);
  return Number(v.toPrecision(sig)).toLocaleString("en-US", { maximumFractionDigits: 6 });
}
const mlUnit = kind => mlU.label(kind);

/** Next free "NAME-2", "NAME-3" … */
function mlFree(taken, base) {
  let i = 2;
  while (taken.has(`${base}-${i}`)) i++;
  return `${base}-${i}`;
}

/** Import picked records into draft D (mutates D.materials). Returns [{name, as, replaced}]. */
export function mlImportInto(D, picks, onClash = "suffix") {
  D.materials = D.materials || {};
  const taken = new Set(Object.keys(D.materials));
  const out = [];
  for (const p of picks) {
    let target = p.rec.name, replaced = false;
    if (taken.has(target)) {
      if (onClash === "replace" && D.materials[target]) replaced = true;
      else target = mlFree(taken, target);
    }
    taken.add(target);
    D.materials[target] = { ...mlClone(p.rec.material), name: target };
    out.push({ name: p.rec.name, as: target, replaced, standard: p.std });
  }
  return out;
}

/** Open the browser. opts.D = a dlgkit draft to import into (Define Materials);
    without it the browser drafts and commits by itself. opts.done(results). */
export function openMatLib(sky, { D = null, done } = {}) {
  mlEnsureCss();
  const standalone = !D;
  const draft = D || mlDraft(sky);
  draft.materials = draft.materials || {};
  const st = { std: null, kind: "steel", q: "", focus: null, clash: "suffix", standards: [], recs: [], curve: null, curveFor: null };
  const picked = new Map();            // "std|name" → {std, rec}
  const key = (std, name) => `${std}|${name}`;

  const stdSel = mlEl("select", { id: "mlStd", "aria-label": "Standard" });
  const kindSel = mlEl("select", { id: "mlKind", "aria-label": "Material kind" });
  const search = mlEl("input", { id: "mlSearch", type: "search", placeholder: "Filter grades…", "aria-label": "Filter grades", "data-no-enter": "" });
  const bar = mlEl("div", { class: "ml-bar" }, [
    mlEl("label", {}, ["Standard", stdSel]),
    mlEl("label", {}, ["Material", kindSel]),
    mlEl("label", {}, ["Search", search]),
  ]);
  const tableWrap = mlEl("div", { class: "ml-table", id: "mlTable", "data-no-enter": "" }, [mlEl("p", { class: "muted dk-empty", text: "Loading the library…" })]);
  const info = mlEl("p", { class: "muted ml-info", id: "mlInfo" });
  const plotWrap = mlEl("div", { class: "ss-plot ml-plot", id: "mlPreview" });
  const props = mlEl("div", { class: "ml-props", id: "mlProps" });
  const side = mlEl("div", { class: "ml-side" }, [plotWrap, props]);
  const main = mlEl("div", { class: "ml-main" }, [tableWrap, side]);
  const rSuffix = mlEl("input", { type: "radio", name: "mlClash", id: "mlClashSuffix", value: "suffix", checked: true });
  const rReplace = mlEl("input", { type: "radio", name: "mlClash", id: "mlClashReplace", value: "replace" });
  const clashNote = mlEl("span", { class: "muted", id: "mlClashNote" });
  const clashRow = mlEl("div", { class: "ml-clashrow" }, [
    mlEl("span", { text: "If the name exists:" }),
    mlEl("label", {}, [rSuffix, " Add with a “-2” suffix"]),
    mlEl("label", {}, [rReplace, " Replace existing"]),
    clashNote,
  ]);
  [rSuffix, rReplace].forEach(r => r.addEventListener("change", () => { if (r.checked) { st.clash = r.value; renderClash(); } }));

  const body = [
    bar, main, info,
    mlGroup("Import", [clashRow]),
  ];

  const visible = () => {
    const q = st.q.trim().toLowerCase();
    return st.recs.filter(r => (!st.kind || r.kind === st.kind) && (!q || r.name.toLowerCase().includes(q) || r.group.toLowerCase().includes(q)));
  };
  const focusRec = () => st.recs.find(r => r.name === st.focus) || null;

  function renderKinds() {
    const s = st.standards.find(x => x.id === st.std);
    const kinds = mlKINDS.filter(k => s && s.kinds[k]);
    if (!kinds.includes(st.kind)) st.kind = kinds[0] || "steel";
    kindSel.textContent = "";
    for (const k of kinds) kindSel.appendChild(mlEl("option", { value: k, selected: k === st.kind }, [`${ML_KIND_LABEL[k]} (${s.kinds[k]})`]));
  }

  function renderTable() {
    const rows = visible();
    if (!rows.some(r => r.name === st.focus)) st.focus = rows.length ? rows[0].name : null;
    tableWrap.textContent = "";
    if (!rows.length) { tableWrap.appendChild(mlEl("p", { class: "muted dk-empty", text: "No grades match." })); renderFocus(); return; }
    const conc = st.kind === "concrete";
    const sU = mlUnit("stress"), eU = mlUnit("modulus"), wU = mlUnit("unit_weight");
    const all = mlEl("input", { type: "checkbox", id: "mlAll", "aria-label": "Select all listed grades", title: "Select all listed grades" });
    all.checked = rows.every(r => picked.has(key(st.std, r.name)));
    all.addEventListener("change", () => {
      for (const r of rows) all.checked ? picked.set(key(st.std, r.name), { std: st.std, rec: r }) : picked.delete(key(st.std, r.name));
      renderTable(); syncNote();
    });
    const head = mlEl("tr", {}, [
      mlEl("th", {}, [all]), mlEl("th", { class: "txt" }, ["Grade"]),
      mlEl("th", { class: "num" }, [conc ? `f'c (${sU})` : `Fy (${sU})`]),
      conc ? null : mlEl("th", { class: "num" }, [`Fu (${sU})`]),
      mlEl("th", { class: "num" }, [`E (${eU})`]),
      mlEl("th", { class: "num" }, [`Weight (${wU})`]),
    ]);
    const tb = mlEl("tbody");
    for (const r of rows) {
      const k = key(st.std, r.name);
      const m = r.material;
      const cb = mlEl("input", { type: "checkbox", class: "ml-pick", "data-name": r.name, "aria-label": `Import ${r.name}` });
      cb.checked = picked.has(k);
      cb.addEventListener("click", e => e.stopPropagation());
      cb.addEventListener("change", () => { cb.checked ? picked.set(k, { std: st.std, rec: r }) : picked.delete(k); all.checked = rows.every(x => picked.has(key(st.std, x.name))); syncNote(); renderClash(); });
      const clash = draft.materials[r.name] ? mlEl("span", { class: "ml-clash", title: "A material with this name already exists" }, ["exists"]) : null;
      const tr = mlEl("tr", { class: r.name === st.focus ? "is-focus" : "", "data-name": r.name, tabindex: r.name === st.focus ? "0" : "-1" }, [
        mlEl("td", {}, [cb]),
        mlEl("td", { class: "txt" }, [r.name, clash, mlEl("div", { class: "ml-group" }, [r.group])]),
        mlEl("td", { class: "num" }, [mlNum("stress", conc ? m.fc : m.fy, 4)]),
        conc ? null : mlEl("td", { class: "num" }, [mlNum("stress", m.fu, 4)]),
        mlEl("td", { class: "num" }, [mlNum("modulus", m.E, 5)]),
        mlEl("td", { class: "num" }, [mlNum("unit_weight", m.unit_weight, 4)]),
      ]);
      tr.addEventListener("click", () => { st.focus = r.name; renderTable(); });
      tr.addEventListener("dblclick", () => { cb.checked = !cb.checked; cb.dispatchEvent(new Event("change")); });
      tr.addEventListener("keydown", e => {
        if (e.key === " ") { e.preventDefault(); cb.checked = !cb.checked; cb.dispatchEvent(new Event("change")); return; }
        if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
        e.preventDefault();
        const i = rows.findIndex(x => x.name === st.focus);
        const j = Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)));
        st.focus = rows[j].name; renderTable();
        const n = tableWrap.querySelector(`tr[data-name="${CSS.escape(st.focus)}"]`); if (n) n.focus();
      });
      tb.appendChild(tr);
    }
    tableWrap.appendChild(mlEl("table", { class: "data-table" }, [mlEl("thead", {}, [head]), tb]));
    renderFocus();
  }

  function renderFocus() {
    const r = focusRec();
    if (!r) { info.textContent = ""; props.textContent = ""; plotWrap.textContent = ""; return; }
    info.innerHTML = `<b>${mlEsc(r.name)}</b> · ${mlEsc(r.group)} — ${mlEsc(r.source)}<br>${mlEsc(r.formula)}`;
    const m = r.material;
    const rows = [
      ["E", `${mlNum("modulus", m.E, 6)} ${mlUnit("modulus")}`],
      ["ν", String(m.nu)],
      ["G = E/2(1+ν)", `${mlNum("modulus", m.G, 6)} ${mlUnit("modulus")}`],
      ["Weight / volume", `${mlNum("unit_weight", m.unit_weight, 5)} ${mlUnit("unit_weight")}`],
      ["Mass / volume", `${mlNum("mass_density", m.mass_density, 5)} ${mlUnit("mass_density")}`],
      ["α", `${mlU.toDisplay("thermal_coeff", m.alpha).toExponential(3)} ${mlUnit("thermal_coeff")}`],
    ];
    if (m.fc != null) rows.push(["f'c", `${mlNum("stress", m.fc, 5)} ${mlUnit("stress")}`]);
    if (m.fy != null) rows.push(["Fy", `${mlNum("stress", m.fy, 5)} ${mlUnit("stress")}`]);
    if (m.fu != null) rows.push(["Fu", `${mlNum("stress", m.fu, 5)} ${mlUnit("stress")}`]);
    if (r.E_table != null && r.E_formula != null) {
      rows.push(["E (table)", `${mlNum("modulus", r.E_table / 1000, 6)} ${mlUnit("modulus")}`]);
      rows.push(["E (formula)", `${mlNum("modulus", r.E_formula / 1000, 6)} ${mlUnit("modulus")}`]);
      rows.push(["Used", r.E_basis]);
    }
    props.textContent = "";
    for (const [a, b] of rows) props.append(mlEl("span", { text: a }), mlEl("span", { text: b }));
    refreshCurve(r);
  }

  let curveTimer = 0;
  function refreshCurve(r) {
    const id = key(st.std, r.name);
    if (st.curveFor === id && st.curve) { mlDrawPlot(plotWrap, st.curve, r.material); return; }
    clearTimeout(curveTimer);
    curveTimer = setTimeout(async () => {
      if (!sky.fetchMaterialCurve) return;
      try {
        const res = await sky.fetchMaterialCurve(r.material);
        if (focusRec() !== r) return;
        st.curve = res; st.curveFor = id;
        mlDrawPlot(plotWrap, res, r.material);
      } catch (e) { plotWrap.textContent = ""; plotWrap.appendChild(mlEl("p", { class: "muted", text: `No preview: ${e.message}` })); }
    }, 60);
  }

  function renderClash() {
    const names = [...picked.values()].map(p => p.rec.name).filter(n => draft.materials[n]);
    clashNote.textContent = names.length
      ? `${names.length} of the selected name${names.length > 1 ? "s" : ""} already exist${names.length > 1 ? "" : "s"} (${names.slice(0, 4).join(", ")}${names.length > 4 ? " …" : ""}) — ${st.clash === "replace" ? "they will be replaced" : "a suffix will be added"}.`
      : "";
  }
  const syncNote = () => dlg && dlg.setNote(`${picked.size} grade${picked.size === 1 ? "" : "s"} selected`);

  async function loadStd(std) {
    st.std = std;
    st.curve = null; st.curveFor = null;
    tableWrap.textContent = "";
    tableWrap.appendChild(mlEl("p", { class: "muted dk-empty", text: "Loading…" }));
    renderKinds();
    st.recs = await mlMaterials(sky, std);
    if (st.std !== std) return;
    renderTable();
  }

  stdSel.addEventListener("change", () => { loadStd(stdSel.value); });
  kindSel.addEventListener("change", () => { st.kind = kindSel.value; st.focus = null; renderTable(); });
  search.addEventListener("input", () => { st.q = search.value; renderTable(); });

  const dlg = mlDialog({
    id: "mlMatLib", title: "Material Library", iconId: "exleaf-materials", size: "lg",
    intro: "Code material grades (analysis properties E, ν, G, weight, mass, α plus strengths). Tick the grades to add, then <b>Import</b>.",
    body, okLabel: "Import",
    onUnits: () => { renderTable(); },
    onOk: () => {
      if (!picked.size) return "Select at least one grade to import.";
      const res = mlImportInto(draft, [...picked.values()], st.clash);
      if (standalone) {
        mlCommit(sky, draft);
        if (sky.toast) sky.toast("Material library", `Imported ${res.map(x => x.as).join(", ")}`, "info", 5000);
      }
      done && done(res);
    },
  });
  syncNote();

  (async () => {
    try {
      st.standards = await mlStandards(sky);
      stdSel.textContent = "";
      for (const s of st.standards) stdSel.appendChild(mlEl("option", { value: s.id }, [`${s.standard} — ${s.count} grades`]));
      await loadStd(st.standards[0].id);
    } catch (e) {
      tableWrap.textContent = "";
      tableWrap.appendChild(mlEl("p", { class: "dk-err", text: `Could not load the material library: ${e.message}` }));
    }
  })();
  dlg.api = { picked, state: st, loadStd, setKind: k => { st.kind = k; kindSel.value = k; st.focus = null; renderTable(); } };
  return dlg;
}

/** Register Define › Material Library… (and expose __sky.matLib). */
export function initMatLib(sky) {
  sky.matLib = { open: o => openMatLib(sky, o), standards: () => mlStandards(sky), materials: s => mlMaterials(sky, s), importInto: mlImportInto };
  if (sky.menu && sky.menu.add) {
    sky.menu.add("Define", { label: "Material Library…", act: "def-matlib", before: "Section Properties", hint: "ASTM/ACI · EN · IS · BS · AS/NZS", fn: () => openMatLib(sky) });
  }
}
