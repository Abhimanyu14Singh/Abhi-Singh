/* SkyFrame — "Import from Database…" (v1.21): browse the steel section
   databases (AISC v16.0, and every other skyframe/data/steel/*.json the
   backend serves) and import shapes into the frame-section draft.

     standard (select) → family (tab strip) → search → table of key
     properties in display units → tick rows (multi-select) → OK.
     Clicking a row highlights it and shows the live section preview
     (js/secpreview.js) on the right.

   Backend: GET /api/steel_db/standards, GET /api/steel_db/shapes,
   POST /api/steel_db/import {apply: false} (the server builds the exact
   section dicts; the draft only changes on the list's OK, like every
   dlgkit "Define" dialog). ?mock=1 / no backend: js/mock_steeldb.js.

   Hook: one action line in js/dlgstd_defs.js (Frame Properties list). */

import sdbU from "./units.js";
import { el as sdbEl, esc as sdbEsc, dkForm as sdbForm, dkGroup as sdbGroup, dkDialog as sdbDialog } from "./dlgkit.js";
import { spvFramePanel as sdbPanel, spvSplit as sdbSplit } from "./secpreview.js";
import { ssFromRecord as sdbFromRecord, SS_LABEL as sdbLabels } from "./secshapes.js";
import { mockSdbStandards, mockSdbShapes, mockSdbImport } from "./mock_steeldb.js";
import { mlMaterials as sdbMlMaterials, mlImportInto as sdbMlImportInto } from "./matlib.js";   // default steel grade per database

/* steel grade added from the material library when the model has no steel
   material yet: database id -> [material standard, grade] */
const SDB_AUTO_MAT = { aisc: ["astm_aci", "A992"], eurocode: ["eurocode", "S355"], is808: ["is", "E250"],
  bs: ["bs", "S355-t16"], as_nzs: ["as_nzs", "300-AS3679.1"] };
const SDB_AUTO = "__auto_steel__";

const MAX_ROWS = 400;
let cssDone = false;
function ensureCss() {
  if (cssDone || document.querySelector("link[data-sdb-css]")) { cssDone = true; return; }
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/steeldb.css"; l.setAttribute("data-sdb-css", "1");
  document.head.appendChild(l);
  cssDone = true;
}

const cache = { standards: null, shapes: new Map() };
const isMock = sky => !!(sky && sky.store && sky.store.mock);

async function getJson(url, body) {
  const r = await fetch(url, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let d = null;
  try { d = await r.json(); } catch { /* non-JSON */ }
  if (!r.ok) throw new Error((d && d.error) || `${r.status} ${r.statusText}`);
  return d;
}

/** Database list (live, else the mock subset). */
export async function sdbStandards(sky) {
  if (cache.standards) return cache.standards;
  let st = null;
  if (!isMock(sky)) {
    try { st = await getJson("/api/steel_db/standards"); } catch (e) { console.warn("steel_db standards unavailable, using mock:", e.message); }
  }
  cache.standards = Array.isArray(st) && st.length ? st : mockSdbStandards();
  return cache.standards;
}

/** Records of one family (cached per standard + family). */
export async function sdbShapes(sky, standard, family) {
  const key = `${isMock(sky) ? "m" : "l"}|${standard}|${family}`;
  if (cache.shapes.has(key)) return cache.shapes.get(key);
  let res = null;
  if (!isMock(sky)) {
    try { res = await getJson(`/api/steel_db/shapes?standard=${encodeURIComponent(standard)}&family=${encodeURIComponent(family)}`); }
    catch (e) { console.warn("steel_db shapes unavailable, using mock:", e.message); }
  }
  if (!res) res = mockSdbShapes({ standard, family });
  const recs = (res && res.shapes) || [];
  cache.shapes.set(key, recs);
  return recs;
}

/** Section dicts for `names` (server-built when live). */
async function buildSections(sky, standard, names, material) {
  const body = { standard, names, material, apply: false };
  if (!isMock(sky)) {
    try { return (await getJson("/api/steel_db/import", body)).sections; }
    catch (e) { console.warn("steel_db import unavailable, using mock:", e.message); }
  }
  const r = mockSdbImport(null, body);
  if (r.error) throw new Error(r.error);
  return r.sections;
}

const dimOf = (r, ...ks) => { for (const k of ks) { const v = r.dims && r.dims[k]; if (typeof v === "number") return v; } return null; };
const COLS = [
  { label: "Shape", txt: true, get: r => r.name },
  { label: "d", kind: "dim", get: r => dimOf(r, "d", "D", "h"), fmt: "dim" },
  { label: "b", kind: "dim", get: r => dimOf(r, "bf", "b", "D"), fmt: "dim" },
  { label: "t", kind: "dim", get: r => dimOf(r, "tf", "t"), fmt: "dim" },
  { label: "A", kind: "area", get: r => r.props.A },
  { label: "I33", kind: "inertia", get: r => r.props.I33 },
  { label: "I22", kind: "inertia", get: r => r.props.I22 },
  { label: "J", kind: "inertia", get: r => r.props.J },
  { label: "S33", kind: "secmod", get: r => r.props.S33 },
  { label: "Z33", kind: "secmod", get: r => r.props.Z33 },
  { label: "Weight", kind: "line_force", get: r => r.props.mass != null ? r.props.mass * 9.80665 / 1000 : null },   // kg/m → kN/m (model SI)
];
const show = (c, v) => v == null || !isFinite(v) ? "—" : c.fmt === "dim" ? String(+sdbU.toDisplay("dim", v).toPrecision(4)) : sdbU.sci(c.kind, v, 3);

/** Open the browser on draft D. opts.done(lastImportedName). */
export function openSteelDb(sky, D, { done } = {}) {
  ensureCss();
  D.sections = D.sections || {};
  const F = sdbForm();
  const mats = Object.keys(D.materials || {});
  const st = {
    std: "aisc", fam: "", q: "",
    mat: mats.find(n => (D.materials[n].material_type || "") === "steel") || SDB_AUTO,
  };
  const autoLabel = () => { const a = SDB_AUTO_MAT[st.std] || SDB_AUTO_MAT.aisc; return `+ ${a[1]} (standard steel grade, added from the material library)`; };
  const picked = new Map();            // name → record (across families)
  let recs = [], hi = null, standards = [];
  const stdSel = sdbEl("select", { class: "dk-select", id: "sdbStd", "aria-label": "Section database" });
  const famBar = sdbEl("div", { class: "sdb-fams", role: "tablist", id: "sdbFams", "aria-label": "Shape family" });
  const search = sdbEl("input", { type: "search", class: "dk-search", id: "sdbSearch", placeholder: "Search, e.g. W14 or 5/8", "aria-label": "Search shapes", "data-no-enter": "1" });
  const box = sdbEl("div", { class: "sdb-table table-scroll", id: "sdbTableBox" });
  const status = sdbEl("p", { class: "muted sdb-status", id: "sdbStatus", "aria-live": "polite" });
  const matRow = F.select(st, "mat", () => [[SDB_AUTO, autoLabel()], ...Object.keys(D.materials || {}).map(n => [n, n])], { label: "Material for imported sections", id: "sdbMat" });
  const edition = sdbEl("p", { class: "muted sdb-edition", id: "sdbEdition" });

  const filtered = () => {
    const terms = st.q.split(/\s+/).filter(Boolean).map(t => t.replace(/\s+/g, "").toUpperCase());
    return recs.filter(r => terms.every(t => (r.name + " " + (r.edi || "")).toUpperCase().includes(t)));
  };
  const setStatus = (shown, total) => {
    status.textContent = `${picked.size} selected` + (total != null ? ` · showing ${shown} of ${total}${total > shown ? " — refine the search" : ""}` : "");
    dlg && dlg.setNote(picked.size ? `${picked.size} section${picked.size > 1 ? "s" : ""} to import` : "Tick the shapes to import.");
  };
  const highlight = name => {
    hi = name;
    box.querySelectorAll("tr[data-name]").forEach(tr => tr.classList.toggle("is-sel", tr.dataset.name === name));
    panel.update();
  };
  const drawTable = () => {
    box.textContent = "";
    if (!recs.length) { box.appendChild(sdbEl("p", { class: "muted dk-empty", text: st.fam ? "Loading…" : "Pick a family." })); setStatus(0, null); return; }
    const all = filtered(), rows = all.slice(0, MAX_ROWS);
    if (!rows.length) { box.appendChild(sdbEl("p", { class: "muted dk-empty", text: "No shapes match." })); setStatus(0, all.length); return; }
    const t = sdbEl("table", { class: "data-table dk-list-tbl sdb-tbl", id: "sdbTable" });
    const allCb = sdbEl("input", { type: "checkbox", id: "sdbAll", "aria-label": "Select all shown" });
    const free = rows.filter(r => !D.sections[r.name]);
    allCb.checked = free.length > 0 && free.every(r => picked.has(r.name));
    allCb.addEventListener("change", () => { for (const r of free) { if (allCb.checked) picked.set(r.name, r); else picked.delete(r.name); } drawTable(); });
    t.appendChild(sdbEl("thead", {}, [sdbEl("tr", {}, [sdbEl("th", {}, [allCb]),
      ...COLS.map(c => sdbEl("th", { class: c.txt ? "txt" : "num", text: c.kind ? `${c.label} (${sdbU.label(c.kind)})` : c.label }))])]));
    const tb = sdbEl("tbody");
    for (const r of rows) {
      const exists = !!D.sections[r.name];
      const cb = sdbEl("input", { type: "checkbox", disabled: exists, "aria-label": "Import " + r.name, "data-name": r.name });
      cb.checked = exists || picked.has(r.name);
      cb.addEventListener("change", () => { if (cb.checked) picked.set(r.name, r); else picked.delete(r.name); setStatus(rows.length, all.length); });
      const tr = sdbEl("tr", { class: (exists ? "is-muted " : "") + (r.name === hi ? "is-sel" : ""), "data-name": r.name, tabindex: "-1" }, [sdbEl("td", {}, [cb]),
        ...COLS.map(c => sdbEl("td", { class: c.txt ? "txt" : "num", text: c.txt ? r.name + (exists ? " (defined)" : "") : show(c, c.get(r)) }))]);
      tr.addEventListener("click", e => { if (e.target !== cb) highlight(r.name); });
      tr.addEventListener("dblclick", e => { if (e.target !== cb && !exists) { cb.checked = !cb.checked; cb.dispatchEvent(new Event("change")); } });
      tb.appendChild(tr);
    }
    t.appendChild(tb);
    box.appendChild(t);
    setStatus(rows.length, all.length);
  };
  box.addEventListener("keydown", e => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const names = filtered().slice(0, MAX_ROWS).map(r => r.name);
    if (!names.length) return;
    e.preventDefault();
    const i = names.indexOf(hi), j = Math.max(0, Math.min(names.length - 1, i < 0 ? 0 : i + (e.key === "ArrowDown" ? 1 : -1)));
    highlight(names[j]);
    const tr = box.querySelector(`tr[data-name="${CSS.escape(names[j])}"]`);
    if (tr) tr.scrollIntoView({ block: "nearest" });
  });
  box.tabIndex = 0;
  const loadFamily = async fam => {
    st.fam = fam; recs = [];
    famBar.querySelectorAll("button").forEach(b => { const on = b.dataset.fam === fam; b.classList.toggle("is-on", on); b.setAttribute("aria-selected", on ? "true" : "false"); });
    drawTable();
    const std = st.std;
    const got = await sdbShapes(sky, std, fam);
    if (st.fam !== fam || st.std !== std) return;             // superseded
    recs = got;
    if (!hi || !recs.some(r => r.name === hi)) hi = recs.length ? recs[0].name : null;
    drawTable();
    panel.update();
  };
  const loadStandard = std => {
    st.std = std;
    { const o = matRow.querySelector && matRow.querySelector(`option[value="${SDB_AUTO}"]`); if (o) o.textContent = autoLabel(); }
    const s = standards.find(x => x.id === std) || {};
    edition.innerHTML = s.edition ? `${sdbEsc(s.edition)} · ${s.count} shapes` : "";
    famBar.textContent = "";
    for (const f of s.families || []) {
      const b = sdbEl("button", { type: "button", class: "sdb-fam", role: "tab", "data-fam": f, id: "sdbFam-" + f.replace(/[^A-Za-z0-9]+/g, "_") }, [f]);
      b.addEventListener("click", () => loadFamily(f));
      famBar.appendChild(b);
    }
    if ((s.families || []).length) loadFamily(s.families[0]);
  };
  stdSel.addEventListener("change", () => loadStandard(stdSel.value));
  search.addEventListener("input", () => { st.q = search.value; drawTable(); });

  const hiRecord = () => (hi && recs.find(r => r.name === hi)) || (hi && picked.get(hi)) || null;
  const panel = sdbPanel({
    model: D, name: () => hi || "",
    section: () => { const r = hiRecord(); return r ? sdbFromRecord(r, st.std, st.mat) : null; },
    emptyTitle: "Preview", emptyText: "Click a row to preview the shape.",
  });
  const body = [
    sdbGroup("Database", [F.row("Standard", stdSel, { id: "sdbStd" }), edition, matRow]),
    sdbGroup("Shapes", [famBar, search, box, status]),
  ];
  const dlg = sdbDialog({
    id: "dsSteelDb", title: "Import Sections from Database", iconId: "exleaf-frames", size: "lg", form: F,
    body: sdbSplit(body, panel, { onEvent: () => {} }),
    intro: "Pick a standard and a family, search, then tick the shapes to import. Click a row to preview it. Properties are the published (tabulated) values.",
    onOk: async () => {
      if (!picked.size) { done && done(null); return; }
      if (!st.mat) return "Define a material first.";
      if (st.mat === SDB_AUTO) {                  // add the database's standard steel grade
        const [mstd, grade] = SDB_AUTO_MAT[st.std] || SDB_AUTO_MAT.aisc;
        try {
          const recsM = await sdbMlMaterials(sky, mstd);
          const rec = (recsM || []).find(r => r.name === grade);
          if (!rec) return `Steel grade ${grade} not found in the material library.`;
          const existing = Object.keys(D.materials || {}).find(n => n === grade);
          st.mat = existing || sdbMlImportInto(D, [{ rec, std: mstd }])[0].as;
        } catch (e) { return "Could not add the steel material: " + e.message; }
      }
      let secs;
      try { secs = await buildSections(sky, st.std, [...picked.keys()], st.mat); }
      catch (e) { return "Import failed: " + e.message; }
      let last = null;
      for (const [nm, s] of Object.entries(secs || {})) {
        if (D.sections[nm]) continue;
        D.sections[nm] = s;
        last = nm;
      }
      done && done(last);
    },
  });
  dlg.el.querySelector(".dk-modal").classList.add("sdb-modal");
  setStatus(0, null);
  sdbStandards(sky).then(list => {
    standards = list;
    stdSel.textContent = "";
    for (const s of list) stdSel.appendChild(sdbEl("option", { value: s.id, selected: s.id === st.std }, [`${s.standard} — ${s.edition || s.id}`]));
    if (!list.some(s => s.id === st.std) && list.length) st.std = list[0].id;
    stdSel.value = st.std;
    loadStandard(st.std);
  }).catch(e => { box.textContent = "Database unavailable: " + e.message; });
  return dlg;
}

/** Family / type label for a section imported from a database. */
export const sdbShapeLabel = s => s && s.db_ref ? `${(sdbLabels[s.shape] || s.shape || "Shape")} · ${String(s.db_ref.standard).toUpperCase()}` : null;

export default { openSteelDb, sdbStandards, sdbShapes, sdbShapeLabel };
