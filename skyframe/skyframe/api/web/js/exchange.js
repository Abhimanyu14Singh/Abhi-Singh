/* SkyFrame — File › Import ▸ / File › Export ▸ model exchange
   (CONTRACT "Model exchange: E2K / IFC / DXF / Excel").

     File › Import ▸ ETABS E2K… · IFC… · DXF… · Excel Tables…
     File › Export ▸ ETABS E2K… · IFC… · DXF… · Excel Tables…

   Export dialogs: E2K units (any display-unit system) + what to include +
   the SkyFrame extension comments (exact round trip); IFC physical /
   analysis model / supports; DXF drawing units (m / mm) + frames / shells /
   links / grids; Excel table groups. Import dialogs: file picker + E2K
   fallback units / DXF drawing units and "sections from the current
   model". Every run ends in a summary report (written / read counts,
   skipped features, warnings).

   Live: POST /api/exchange/export|import/<fmt>. ?mock=1: js/mock_exchange.js
   builds the files client-side and the downloads are stubbed (recorded in
   __sky.exchange.downloads instead of saved). Exports never touch the
   model (byte-identical); an import replaces the working model (E2K / IFC /
   DXF) or applies the Excel tables to it. Test hook: __sky.exchange. */

import xcU from "./units.js";
import { el as xcEl, esc as xcEsc, dkDialog as xcDialog, dkForm as xcForm, dkGroup as xcGroup, dkNote as xcNote, dkConfirm as xcConfirm } from "./dlgkit.js";
import { mockExchangeExport as xcMockExport, mockExchangeImport as xcMockImport } from "./mock_exchange.js";

const XC_FORMATS = {
  e2k: { label: "ETABS E2K", ext: ".e2k", accept: ".e2k,.$et,.txt", hint: "ETABS v20+ text" },
  ifc: { label: "IFC", ext: ".ifc", accept: ".ifc", hint: "IFC4 physical + analysis model" },
  dxf: { label: "DXF", ext: ".dxf", accept: ".dxf", hint: "3D lines / faces on SF-<kind>-<section> layers" },
  xlsx: { label: "Excel Tables", ext: ".xlsx", accept: ".xlsx", hint: "interactive-database tables" },
};
// display-unit system -> e2k (force, length)
const XC_E2K_UNITS = [["kN-m", "kN · m"], ["kN-mm", "kN · mm"], ["N-mm", "N · mm"], ["kip-ft", "kip · ft"],
  ["kip-in", "kip · in"], ["tonf-m", "tonf · m"], ["kgf-m", "kgf · m"]];
const XC_UNIT_PAIR = { "kN-m": ["KN", "M"], "kN-mm": ["KN", "MM"], "N-mm": ["N", "MM"], "kip-ft": ["KIP", "FT"],
  "kip-in": ["KIP", "IN"], "tonf-m": ["TONF", "M"], "kgf-m": ["KGF", "M"] };
const XC_TABLE_GROUPS = [
  ["Properties", ["materials", "frame_sections", "shell_sections", "stories", "grid_lines", "mass_source", "mass_source_options"]],
  ["Objects", ["joints", "frame_objects", "shell_objects", "links"]],
  ["Assignments", ["supports", "point_springs", "line_springs", "diaphragm_definitions", "story_diaphragms", "joint_diaphragms", "groups", "group_assignments"]],
  ["Loads", ["load_patterns", "joint_loads", "story_forces", "frame_distributed_loads", "frame_point_loads", "area_loads"]],
  ["Cases & combinations", ["load_cases", "static_cases", "static_case_loads", "rs_cases", "th_cases", "pushover_cases", "staged_cases", "buckling_cases", "load_combinations", "combo_members"]],
];
const XC_CSS_ID = "xcExchangeCss";
const XC_CSS = `
.dk-dlg .xc-file { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.dk-dlg .xc-file .xc-fname { font-size: 12px; }
.dk-dlg .xc-report table { width: 100%; border-collapse: collapse; font-size: 12px; }
.dk-dlg .xc-report td, .dk-dlg .xc-report th { padding: 2px 6px; border-bottom: 1px solid var(--border-soft); text-align: left; }
.dk-dlg .xc-report td.num { text-align: right; font-variant-numeric: tabular-nums; }
.dk-dlg .xc-report ul { margin: 4px 0 0 18px; padding: 0; font-size: 12px; max-height: 28vh; overflow: auto; }
.dk-dlg .xc-ok { color: var(--ok, #2f855a); font-weight: 600; }
.dk-dlg .xc-warn { color: var(--warn, #b7791f); }
`;
function xcEnsureCss() {
  if (document.getElementById(XC_CSS_ID)) return;
  document.head.appendChild(xcEl("style", { id: XC_CSS_ID, text: XC_CSS }));
}

const xcState = { downloads: [], lastReport: null, lastExport: null, lastImport: null };
const xcMock = sky => !!(sky.store && sky.store.mock);

async function xcPost(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!data) throw new Error(`${res.status} ${res.statusText}`);
  return data;
}

function xcB64ToBytes(b64) {
  const bin = atob(b64), out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function xcBytesToB64(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

/** Save (live) or record (mock: stubbed) a download. */
function xcDownload(sky, r) {
  const rec = { filename: r.filename, mime: r.mime, size: r.content != null ? r.content.length : xcB64ToBytes(r.content_b64).length,
    content: r.content != null ? r.content : null, content_b64: r.content_b64 || null, stubbed: xcMock(sky) };
  xcState.downloads.push(rec);
  if (rec.stubbed) return rec;
  const blob = r.content != null ? new Blob([r.content], { type: r.mime }) : new Blob([xcB64ToBytes(r.content_b64)], { type: r.mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = r.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  return rec;
}

/* ------------------------------------------------------------ core calls */
export async function xcExport(sky, fmt, opts = {}) {
  const S = sky.store;
  const body = { model: S.model, ...opts };
  const r = xcMock(sky) ? xcMockExport(fmt, S.model, opts) : await xcPost(`/api/exchange/export/${fmt}`, body);
  if (!r.ok) throw new Error(r.error || "export failed");
  xcState.lastExport = r;
  xcState.lastReport = r.report;
  return r;
}

export async function xcImport(sky, fmt, body) {
  const S = sky.store;
  const b = { soft_errors: true, ...body };
  if (fmt === "xlsx") b.model = S.model;
  const r = xcMock(sky) ? xcMockImport(fmt, b, S.model) : await xcPost(`/api/exchange/import/${fmt}`, b);
  if (!r.ok) throw new Error(r.error || "import failed");
  xcState.lastImport = r;
  xcState.lastReport = r.report;
  return r;
}

function xcAdopt(sky, model, fmt, name) {
  if (sky.adoptModel) sky.adoptModel(model, null);
  else { sky.store.model = model; sky.markDirty && sky.markDirty(); }
  if (fmt === "xlsx" && sky.markDirty) sky.markDirty();   // tables applied to the working model: unsaved
  sky.toast && sky.toast("Model imported", `${XC_FORMATS[fmt].label} · ${name || "file"}`, "info", 4000);
}

/* ------------------------------------------------------------ report */
const XC_LABEL = k => k.replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
function xcReportBody(title, rep, warnings) {
  const kids = [];
  const counts = rep.written || rep.read || {};
  const tables = rep.tables && typeof rep.tables === "object" ? rep.tables : null;
  const rows = Object.entries(counts).filter(([, v]) => typeof v === "number");
  if (rows.length) {
    kids.push(xcEl("table", {}, [xcEl("tbody", {}, rows.map(([k, v]) => xcEl("tr", {}, [xcEl("td", { text: XC_LABEL(k) }), xcEl("td", { class: "num", text: String(v) })])))]));
  }
  if (tables) {
    const tr = Object.entries(tables).map(([k, v]) => [k, v && typeof v === "object" ? Object.entries(v).filter(([, n]) => typeof n === "number" && n).map(([a, n]) => `${a} ${n}`).join(", ") || "unchanged" : String(v)]);
    kids.push(xcEl("table", {}, [xcEl("tbody", {}, tr.map(([k, v]) => xcEl("tr", {}, [xcEl("td", { text: k }), xcEl("td", { text: v })])))]));
  }
  if (rep.exact !== undefined) {
    kids.push(xcEl("p", { class: rep.exact ? "xc-ok" : "xc-warn", text: rep.exact ? "✓ Re-imports to the identical SkyFrame model." : "Native ETABS records only — SkyFrame-only data listed below is not in the file." }));
  }
  const ext = rep.extensions && typeof rep.extensions === "object" ? Object.keys(rep.extensions) : [];
  if (ext.length) kids.push(xcNote(`Carried as SkyFrame comment lines (ETABS ignores them): ${xcEsc(ext.join(", "))}.`));
  const skipped = rep.skipped || [];
  if (skipped.length) kids.push(xcGroup("Skipped / not expressible", [xcEl("ul", {}, skipped.map(s => xcEl("li", { text: s })))]));
  if (warnings && warnings.length) kids.push(xcGroup(`Warnings (${warnings.length})`, [xcEl("ul", {}, warnings.map(s => xcEl("li", { class: "xc-warn", text: s })))]));
  if (!skipped.length && !(warnings && warnings.length)) kids.push(xcEl("p", { class: "xc-ok", text: "✓ Nothing skipped, no warnings." }));
  return xcEl("div", { class: "xc-report", id: "xcReportBody" }, kids);
}
function xcShowReport(title, rep, warnings) {
  xcEnsureCss();
  return xcDialog({ id: "xcReport", title, size: "md", noCancel: true, body: xcReportBody(title, rep, warnings) });
}

/* ------------------------------------------------------------ export dialog */
export function xcOpenExport(sky, fmt) {
  xcEnsureCss();
  const F = xcForm();
  const cur = xcU.getUnits ? xcU.getUnits() : "kN-m";
  const o = { units: XC_UNIT_PAIR[cur] ? cur : "kN-m", extensions: true, unit: "m",
    grids: true, loads: true, cases: true, combos: true, groups: true, mass_source: true,
    physical: true, analysis: true, supports: true, frames: true, shells: true, links: true };
  XC_TABLE_GROUPS.forEach(([g]) => { o["tg_" + g] = true; });
  const body = [];
  if (fmt === "e2k") {
    body.push(xcGroup("Units", [F.select(o, "units", XC_E2K_UNITS, { label: "File units", id: "xcUnits", hint: "values are converted from SI on export" })]));
    body.push(xcGroup("Include", ["grids", "loads", "cases", "combos", "groups", "mass_source"].map(k =>
      F.check(o, k, { label: XC_LABEL(k), id: "xcInc_" + k })), { cols: 2 }));
    body.push(xcGroup("Round trip", [F.check(o, "extensions", { label: "SkyFrame extension comments ($SF lines)", id: "xcExt",
      hint: "exact re-import of SkyFrame-only data; ETABS treats them as comments" })]));
  } else if (fmt === "ifc") {
    body.push(xcGroup("Include", [["physical", "Physical members (IfcColumn/IfcBeam/IfcSlab/IfcWall + profiles)"],
      ["analysis", "Structural analysis model (IfcStructuralAnalysisModel)"], ["supports", "Supports (point connections)"]].map(([k, l]) =>
      F.check(o, k, { label: l, id: "xcInc_" + k }))));
  } else if (fmt === "dxf") {
    body.push(xcGroup("Drawing units", [F.select(o, "unit", [["m", "metres"], ["mm", "millimetres"]], { label: "Units", id: "xcDxfUnit" })]));
    body.push(xcGroup("Include", ["frames", "shells", "links", "grids"].map(k => F.check(o, k, { label: XC_LABEL(k), id: "xcInc_" + k })), { cols: 2 }));
  } else {
    body.push(xcGroup("Tables", XC_TABLE_GROUPS.map(([g]) => F.check(o, "tg_" + g, { label: g, id: "xcTg_" + g.replace(/\W+/g, "") })), { cols: 2,
      note: "One worksheet per table, SI values with exact doubles; re-import with File › Import › Excel Tables." }));
  }
  const dlg = xcDialog({ id: "xcExport", title: `Export ${XC_FORMATS[fmt].label}`, size: "md", form: F,
    intro: xcEsc(XC_FORMATS[fmt].hint) + " — the model itself is not changed.", body, okLabel: "Export",
    onOk: async () => {
      const opts = {};
      if (fmt === "e2k") {
        opts.units = XC_UNIT_PAIR[o.units]; opts.extensions = !!o.extensions;
        opts.include = Object.fromEntries(["grids", "loads", "cases", "combos", "groups", "mass_source"].map(k => [k, !!o[k]]));
        if (xcMock(sky)) opts.units = o.units;
      } else if (fmt === "ifc") opts.include = { physical: !!o.physical, analysis: !!o.analysis, supports: !!o.supports };
      else if (fmt === "dxf") { opts.unit_scale = o.unit === "mm" ? 1000 : 1; opts.include = Object.fromEntries(["frames", "shells", "links", "grids"].map(k => [k, !!o[k]])); }
      else {
        const keys = XC_TABLE_GROUPS.filter(([g]) => o["tg_" + g]).flatMap(([, ks]) => ks);
        if (!keys.length) return "Pick at least one table group.";
        if (keys.length < XC_TABLE_GROUPS.flatMap(([, ks]) => ks).length) opts.tables = keys;
      }
      try {
        const r = await xcExport(sky, fmt, opts);
        xcDownload(sky, r);
        setTimeout(() => xcShowReport(`Exported ${r.filename}`, r.report, []), 0);
        return true;
      } catch (e) { return e.message; }
    } });
  return dlg;
}

/* ------------------------------------------------------------ import dialog */
export function xcOpenImport(sky, fmt) {
  xcEnsureCss();
  const F = xcForm();
  const o = { units: "kN-m", unit: "m", fromCurrent: true };
  const st = { file: null, text: null, b64: null };
  const fname = xcEl("span", { class: "muted xc-fname", id: "xcFileName", text: "No file selected." });
  const input = xcEl("input", { type: "file", accept: XC_FORMATS[fmt].accept, id: "xcFile", class: "dk-file" });
  input.addEventListener("change", () => {
    const f = input.files && input.files[0];
    if (!f) return;
    st.file = f;
    fname.textContent = `${f.name} · ${f.size} bytes`;
    const rd = new FileReader();
    if (fmt === "xlsx") {
      rd.onload = () => { st.b64 = xcBytesToB64(new Uint8Array(rd.result)); };
      rd.readAsArrayBuffer(f);
    } else {
      rd.onload = () => { st.text = String(rd.result || ""); };
      rd.readAsText(f);
    }
  });
  const body = [xcGroup("File", [xcEl("div", { class: "xc-file" }, [input, fname])])];
  if (fmt === "e2k") body.push(xcGroup("Units", [F.select(o, "units", XC_E2K_UNITS, { label: "If the file has no UNITS line", id: "xcImpUnits" })]));
  if (fmt === "dxf") body.push(xcGroup("Options", [F.select(o, "unit", [["m", "metres"], ["mm", "millimetres"]], { label: "Drawing units", id: "xcImpDxfUnit" }),
    F.check(o, "fromCurrent", { label: "Section definitions from the current model", id: "xcImpFromCur", hint: "layer SF-<kind>-<section> → section of that name" })]));
  const intro = fmt === "xlsx" ? "Applies the edited tables to the working model (atomic; nothing changes on any error)."
    : "Replaces the working model with the imported one.";
  const dlg = xcDialog({ id: "xcImport", title: `Import ${XC_FORMATS[fmt].label}`, size: "md", form: F, intro, body, okLabel: "Import",
    onOk: async () => {
      if (fmt === "xlsx" ? !st.b64 : st.text == null) return "Choose a file first.";
      if (fmt !== "xlsx" && sky.store.dirty) {
        const ok = await xcConfirm({ id: "xcConfirmImport", title: "Import model", html: "You have unsaved changes — import and discard them?", okLabel: "Import" });
        if (!ok) return false;
      }
      return xcRunImport(sky, fmt, fmt === "xlsx" ? { content_b64: st.b64 } : { text: st.text }, o, st.file && st.file.name);
    } });
  dlg.setFile = (text, b64, name) => { st.text = text; st.b64 = b64; st.file = { name: name || "file" }; fname.textContent = name || "file"; };
  return dlg;
}

async function xcRunImport(sky, fmt, payload, o = {}, name = "") {
  const body = { ...payload };
  if (fmt === "e2k") body.units = XC_UNIT_PAIR[o.units || "kN-m"];
  if (fmt === "dxf") { body.unit_scale = o.unit === "mm" ? 1000 : 1; body.sections_from_current = o.fromCurrent !== false; }
  try {
    const r = await xcImport(sky, fmt, body);
    xcAdopt(sky, r.model, fmt, name);
    setTimeout(() => xcShowReport(`Imported ${name || XC_FORMATS[fmt].label}`, r.report, r.warnings || []), 0);
    return true;
  } catch (e) { return e.message; }
}

/* ------------------------------------------------------------ init */
export function initExchange(sky) {
  if (!sky) return;
  sky.exchange = {
    FORMATS: Object.keys(XC_FORMATS),
    openExport: fmt => xcOpenExport(sky, fmt),
    openImport: fmt => xcOpenImport(sky, fmt),
    exportNow: (fmt, opts = {}) => xcExport(sky, fmt, opts),
    importNow: async (fmt, payload, opts = {}) => {
      const r = await xcImport(sky, fmt, { ...payload, ...opts });
      xcAdopt(sky, r.model, fmt, payload.name || "");
      return r;
    },
    download: r => xcDownload(sky, r),
    showReport: (title, rep, w) => xcShowReport(title, rep, w),
    state: xcState,
    get downloads() { return xcState.downloads; },
    get lastReport() { return xcState.lastReport; },
  };
  if (!(sky.menu && sky.menu.add)) return;
  for (const [fmt, f] of Object.entries(XC_FORMATS)) {
    sky.menu.add("File/Import", { label: f.label + "…", act: "file-xch-imp-" + fmt, hint: f.ext, fn: () => xcOpenImport(sky, fmt) });
    sky.menu.add("File/Export", { label: f.label + "…", act: "file-xch-exp-" + fmt, hint: f.ext, fn: () => xcOpenExport(sky, fmt) });
  }
}
