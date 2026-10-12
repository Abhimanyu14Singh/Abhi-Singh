/* SkyFrame — export analysis-results tables to Excel (.xlsx) and SQLite
   (.sqlite, the open stand-in for ETABS' "Export to Microsoft Access").

     File › Export › Results Tables to Excel (.xlsx)…    file-a8-res-xlsx
     File › Export › Results Tables to SQLite (.sqlite)… file-a8-res-sqlite
     Show Tables dialog: "⬇ XLSX" (the current table) + "Export…" buttons

   Live: POST /api/tables/export/<xlsx|sqlite> {tables, cases?, units,
   skip_empty} → {filename, mime, content_b64} (one sheet / SQL table per
   Show-Tables table; values converted to the chosen unit set, unit in each
   header).  ?mock=1: the same workbook is built in the browser
   (js/mock_restables.js); SQLite falls back to an SQL text dump. */

import reU from "./units.js";
import { el as reEl, esc as reEsc, dkForm as reForm, dkGroup as reGroup, dkNote as reNote,
  dkDialog as reDialog } from "./dlgkit.js";
import { mrtXlsx as reMockXlsx, mrtSqlDump as reMockSql } from "./mock_restables.js";

const RE = { downloads: [], last: null };
const RE_FMT = {
  xlsx: { label: "Excel (.xlsx)", mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
  sqlite: { label: "SQLite (.sqlite)", mime: "application/vnd.sqlite3" },
};
const reMock = sky => !!(sky.store && sky.store.mock);
const reSafe = s => String(s || "model").replace(/[^\w.-]+/g, "_");

function reB64(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}
function reBytes(b64) { const bin = atob(b64); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u; }

/** Save a {filename, mime, content_b64 | text} record (and remember it). */
export function reDownload(rec) {
  RE.downloads.push(rec);
  RE.last = rec;
  const blob = rec.text != null ? new Blob([rec.text], { type: rec.mime }) : new Blob([reBytes(rec.content_b64)], { type: rec.mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = rec.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  return rec;
}

async function rePost(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  let d = null;
  try { d = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((d && d.error) || `${res.status} ${res.statusText}`);
  return d;
}

/** Export now: {fmt, tables?: [keys] (null = all), cases?, units, skipEmpty}. */
export async function reExport(sky, { fmt = "xlsx", tables = null, cases = null, units = reU.getUnits(), skipEmpty = false } = {}) {
  const S = sky.store;
  if (!S.results) throw new Error("Run the analysis first — tables are computed from the last solve.");
  if (!RE_FMT[fmt]) throw new Error(`unknown format ${fmt}`);
  const name = reSafe(S.model && S.model.name);
  if (!reMock(sky)) {
    const body = { units, skip_empty: !!skipEmpty };
    if (tables) body.tables = tables;
    if (cases) body.cases = cases;
    const r = await rePost(`/api/tables/export/${fmt}`, body);
    return reDownload({ filename: r.filename, mime: r.mime, content_b64: r.content_b64, report: r.report, fmt });
  }
  // offline mock: compute each table through the mock router, build in JS
  const list = (await sky.skyApi("/api/tables/list")).tables || [];
  const keys = tables || list.map(t => t.key);
  const tabs = [];
  for (const k of list.map(t => t.key).filter(k => keys.includes(k))) {
    const t = await sky.skyApi(`/api/tables/${encodeURIComponent(k)}`, cases ? { cases } : {});
    if (skipEmpty && !(t.rows || []).length) continue;
    tabs.push(t);
  }
  const report = { format: fmt, units, tables: Object.fromEntries(tabs.map(t => [t.key, t.rows.length])), mock: true };
  if (fmt === "xlsx")
    return reDownload({ filename: `${name}_results.xlsx`, mime: RE_FMT.xlsx.mime, content_b64: reB64(reMockXlsx(tabs, units, S.model.name || "")), report, fmt });
  return reDownload({ filename: `${name}_results.sql`, mime: "application/sql", text: reMockSql(tabs, units, S.model.name || ""), report, fmt: "sql" });
}

/* ================================================================
   dialog
   ================================================================ */
export async function reOpenExport(sky, fmt = "xlsx", { preselect = null, cases = null } = {}) {
  const S = sky.store;
  const id = "reExportDlg";
  const F = reForm();
  const V = { units: reU.getUnits(), skipEmpty: true };
  const tree = reEl("div", { class: "re-tree", id: "reTableTree", style: "max-height:300px;overflow:auto;border:1px solid var(--line,#d0d8e0);border-radius:6px;padding:6px 8px" },
    [reEl("div", { class: "muted", text: "Loading tables…" })]);
  const boxes = {};
  let list = [];
  const count = reEl("span", { class: "muted", id: "reCount" });
  const sync = () => { const n = Object.values(boxes).filter(b => b.checked).length; count.textContent = `${n} of ${list.length} tables selected`; };
  const btn = (txt, idb, fn) => reEl("button", { type: "button", class: "btn btn-small", id: idb, onclick: fn }, [txt]);
  const body = [
    reGroup("Tables", [tree, reEl("div", { class: "dk-inline", style: "display:flex;gap:6px;align-items:center;margin-top:6px" }, [
      btn("All", "reAll", () => { Object.values(boxes).forEach(b => { b.checked = true; }); sync(); }),
      btn("None", "reNone", () => { Object.values(boxes).forEach(b => { b.checked = false; }); sync(); }),
      count])]),
    reGroup("Options", [
      F.select(V, "units", () => reU.UNIT_SET_NAMES.map(n => [n, n]), { label: "Units", id: "reUnits", hint: "values are converted; each header carries its unit" }),
      F.check(V, "skipEmpty", { label: "Skip tables without rows", id: "reSkipEmpty" }),
    ]),
    reNote(fmt === "sqlite"
      ? "One SQL table per results table (ETABS Access-style field names) plus “Table Index”, “Field Units” and “Program Control”. SQLite replaces ETABS’ Microsoft Access export: Access, Excel (ODBC), Python, R and BI tools read it directly." +
        (reMock(sky) ? " <b>Offline mock:</b> an SQL text dump is saved instead — run the live server for a .sqlite file." : "")
      : "One worksheet per table (sheet name = ETABS table name) plus an _index sheet; numbers are stored as numeric cells."),
  ];
  const dlg = reDialog({ id, title: `Export Results Tables — ${RE_FMT[fmt].label}`, iconId: "show-tables", size: "md", form: F, body,
    okLabel: "Export",
    intro: S.results ? "Tables are post-processed from the last analysis (the solver is not re-run)." : "<b>No results yet</b> — run the analysis first (F5).",
    onOk: async () => {
      if (!S.results) return "Run the analysis first.";
      const keys = list.map(t => t.key).filter(k => boxes[k] && boxes[k].checked);
      if (!keys.length) return "Select at least one table.";
      try {
        const rec = await reExport(sky, { fmt, tables: keys.length === list.length ? null : keys, cases, units: V.units, skipEmpty: V.skipEmpty });
        sky.toast && sky.toast("Results exported", `${rec.filename} · ${Object.keys(rec.report.tables || {}).length} tables`);
      } catch (e) { return "Export failed: " + e.message; }
    } });
  try {
    list = (await sky.skyApi("/api/tables/list")).tables || [];
  } catch (e) { tree.textContent = "Tables unavailable: " + e.message; return dlg; }
  tree.textContent = "";
  const groups = {};
  for (const t of list) (groups[t.group.replace(/^Analysis Results\s*>\s*/, "")] = groups[t.group.replace(/^Analysis Results\s*>\s*/, "")] || []).push(t);
  for (const [g, ts] of Object.entries(groups)) {
    const gb = reEl("input", { type: "checkbox", "data-group": g });
    const kids = ts.map(t => {
      const b = reEl("input", { type: "checkbox", "data-key": t.key });
      b.checked = preselect ? preselect.includes(t.key) : true;
      b.addEventListener("change", () => { gb.checked = ts.every(x => boxes[x.key].checked); sync(); });
      boxes[t.key] = b;
      return reEl("label", { class: "dk-check", style: "display:flex;gap:6px;padding:1px 0 1px 22px" }, [b, reEl("span", { text: t.title })]);
    });
    gb.checked = ts.every(x => boxes[x.key].checked);
    gb.addEventListener("change", () => { ts.forEach(x => { boxes[x.key].checked = gb.checked; }); sync(); });
    tree.appendChild(reEl("label", { class: "dk-check", style: "display:flex;gap:6px;font-weight:600;padding-top:4px" }, [gb, reEl("span", { text: g })]));
    kids.forEach(k => tree.appendChild(k));
  }
  sync();
  return dlg;
}

/* Show Tables: "⬇ XLSX" (current table) + "Export…" */
function reHookShowTables(sky, detail) {
  const tools = detail.el.querySelector(".tbl-tools");
  if (!tools || tools.querySelector("#tblXlsx")) return;
  const csv = tools.querySelector("#tblCsv");
  const x = reEl("button", { class: "chip csv-btn", id: "tblXlsx", title: "Export the current table to Excel (.xlsx) in the current display units" }, ["⬇ XLSX"]);
  x.addEventListener("click", async () => {
    const k = detail.key();
    if (!k) return;
    try { const r = await reExport(sky, { fmt: "xlsx", tables: [k], cases: detail.cases() }); sky.toast && sky.toast("Excel exported", r.filename); }
    catch (e) { sky.toast && sky.toast("Export failed", e.message, "error"); }
  });
  const more = reEl("button", { class: "chip csv-btn", id: "tblExportMore", title: "Export several tables to Excel or SQLite" }, ["Export…"]);
  more.addEventListener("click", () => reOpenExport(sky, "xlsx", { preselect: detail.key() ? [detail.key()] : null, cases: detail.cases() }));
  if (csv && csv.nextSibling) tools.insertBefore(x, csv.nextSibling); else tools.appendChild(x);
  tools.insertBefore(more, x.nextSibling);
}

export function initResExport(sky) {
  if (!sky) return;
  sky.resExport = {
    open: (fmt, o) => reOpenExport(sky, fmt, o),
    exportNow: o => reExport(sky, o),
    get downloads() { return RE.downloads; },
    get last() { return RE.last; },
    download: rec => reDownload(rec),
  };
  document.addEventListener("sky:tables-open", e => { try { reHookShowTables(sky, e.detail); } catch (err) { console.error(err); } });
  if (!(sky.menu && sky.menu.add)) return;
  sky.menu.add("File/Export", { label: "Results Tables to Excel (.xlsx)…", act: "file-a8-res-xlsx", hint: "one sheet per table", fn: () => reOpenExport(sky, "xlsx") });
  sky.menu.add("File/Export", { label: "Results Tables to SQLite (.sqlite)…", act: "file-a8-res-sqlite", hint: "Microsoft Access substitute", fn: () => reOpenExport(sky, "sqlite") });
}
