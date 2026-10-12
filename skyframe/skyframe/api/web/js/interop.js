/* SkyFrame — CSI / steel interop (CONTRACT "SAP2000 / SAFE / steel interop").

     File › Import ▸ SAP2000 .s2k… · SAFE .f2k… · SDNF… · CIS/2…
     File › Export ▸ SAP2000 .s2k… · SAFE Floor .f2k… · SDNF… · IDEA StatiCa… · IFC for Revit…

   Live: POST /api/interop/export|import/<fmt>. ?mock=1: js/mock_interop.js
   builds the files client-side. Downloads and the summary report reuse the
   model-exchange helpers (__sky.exchange.download / showReport, js/exchange.js).
   Exports never touch the model; imports replace it (confirm when dirty).
   Test hook: __sky.interop. */

import ioU from "./units.js";
import { el as ioEl, esc as ioEsc, dkDialog as ioDialog, dkForm as ioForm, dkGroup as ioGroup, dkNote as ioNote, dkConfirm as ioConfirm } from "./dlgkit.js";
import { mockInteropExport as ioMockExport, mockInteropImport as ioMockImport } from "./mock_interop.js";

const IO_EXPORT = {
  s2k: { label: "SAP2000 .s2k", hint: "SAP2000 v20+ database tables" },
  f2k: { label: "SAFE Floor .f2k", hint: "one story: slabs, beams, columns below, floor loads + column reactions from above" },
  sdnf: { label: "SDNF (Steel Detailing)", hint: "SDNF v3.0 Packets 00 / 10 — linear steel members" },
  idea: { label: "IDEA StatiCa (IOM)", hint: "OpenModel + OpenModelResult XML (zip): members, nodes, sections, load effects" },
  ifc_revit: { label: "IFC for Revit", hint: "IFC4 Reference View + structural analysis view, Revit family types and levels" },
};
const IO_IMPORT = {
  s2k: { label: "SAP2000 .s2k", accept: ".s2k,.$2k,.txt" },
  f2k: { label: "SAFE .f2k", accept: ".f2k,.txt" },
  sdnf: { label: "SDNF", accept: ".sdnf,.dat,.txt" },
  cis2: { label: "CIS/2 (LPM6)", accept: ".stp,.step,.p21" },
};
const IO_CSI_UNITS = [["KN,M", "kN · m"], ["KN,MM", "kN · mm"], ["N,MM", "N · mm"], ["KIP,FT", "kip · ft"], ["KIP,IN", "kip · in"]];
const IO_DISP_TO_CSI = { "kN-m": "KN,M", "kN-mm": "KN,MM", "N-mm": "N,MM", "kip-ft": "KIP,FT", "kip-in": "KIP,IN" };
const IO_LEN = [["m", "metres"], ["mm", "millimetres"], ["in", "inches"], ["ft", "feet"]];

const ioState = { lastExport: null, lastImport: null, lastReport: null, downloads: [] };
const ioMock = sky => !!(sky.store && sky.store.mock);

async function ioPost(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!data) throw new Error(`${res.status} ${res.statusText}`);
  return data;
}

export async function ioExport(sky, fmt, opts = {}) {
  const S = sky.store;
  const body = { model: S.model, soft_errors: true, ...opts };
  const r = ioMock(sky) ? ioMockExport(fmt, S.model, opts) : await ioPost(`/api/interop/export/${fmt}`, body);
  if (!r.ok) throw new Error(r.error || "export failed");
  ioState.lastExport = r;
  ioState.lastReport = r.report;
  return r;
}

export async function ioImport(sky, fmt, body) {
  const b = { soft_errors: true, ...body };
  const r = ioMock(sky) ? ioMockImport(fmt, b) : await ioPost(`/api/interop/import/${fmt}`, b);
  if (!r.ok) throw new Error(r.error || "import failed");
  ioState.lastImport = r;
  ioState.lastReport = r.report;
  return r;
}

function ioDownload(sky, r) {
  if (sky.exchange && sky.exchange.download) return sky.exchange.download(r);
  ioState.downloads.push(r);
  return r;
}
function ioReport(sky, title, rep, warnings) {
  if (sky.exchange && sky.exchange.showReport) return sky.exchange.showReport(title, rep, warnings || []);
  return ioDialog({ id: "xcReport", title, size: "md", noCancel: true, body: ioEl("pre", { text: JSON.stringify(rep, null, 1) }) });
}

/* ------------------------------------------------------------ export */
export function ioOpenExport(sky, fmt) {
  const F = ioForm();
  const S = sky.store;
  const cur = ioU.getUnits ? ioU.getUnits() : "kN-m";
  const stories = ((S.model && S.model.stories) || []).map(s => [s.name, s.name]);
  const o = { units: IO_DISP_TO_CSI[cur] || "KN,M", len: cur.startsWith("kip") ? "in" : "m", story: stories.length ? stories[0][0] : "",
    above: true, effects: true, physical: true, analysis: true, supports: true };
  const body = [];
  if (fmt === "s2k" || fmt === "f2k") body.push(ioGroup("Units", [F.select(o, "units", IO_CSI_UNITS, { label: "File units", id: "ioUnits", hint: "values are converted from SI" })]));
  if (fmt === "f2k") {
    if (!stories.length) body.push(ioNote("The model has no stories — nothing to export."));
    else body.push(ioGroup("Floor", [F.select(o, "story", stories, { label: "Story", id: "ioStory" }),
      F.check(o, "above", { label: "Column reactions from the stories above", id: "ioAbove", hint: "runs the analysis (static cases) when needed" })]));
  }
  if (fmt === "sdnf") body.push(ioGroup("Units", [F.select(o, "len", IO_LEN, { label: "Length unit", id: "ioLen" })]));
  if (fmt === "idea") body.push(ioGroup("Content", [F.check(o, "effects", { label: "Load effects (internal forces per static case)", id: "ioEffects", hint: "runs the analysis" })]));
  if (fmt === "ifc_revit") {
    body.push(ioGroup("Include", [["physical", "Physical members"], ["analysis", "Structural analysis view"], ["supports", "Supports"]].map(([k, l]) => F.check(o, k, { label: l, id: "ioInc_" + k }))));
    body.push(ioNote("Revit: Insert › Link IFC (or File › Open › IFC). Levels come from the storeys, family types from the section names. See docs/INTEROP.md."));
  }
  return ioDialog({ id: "ioExport", title: `Export ${IO_EXPORT[fmt].label}`, size: "md", form: F, okLabel: "Export",
    intro: ioEsc(IO_EXPORT[fmt].hint) + " — the model itself is not changed.", body,
    onOk: async () => {
      const opts = {};
      if (fmt === "s2k" || fmt === "f2k") opts.units = o.units.split(",");
      if (fmt === "f2k") { if (!o.story) return "The model has no stories."; opts.story = o.story; opts.loads_from_above = !!o.above; }
      if (fmt === "sdnf") opts.units = o.len;
      if (fmt === "idea") opts.load_effects = !!o.effects;
      if (fmt === "ifc_revit") opts.include = { physical: !!o.physical, analysis: !!o.analysis, supports: !!o.supports };
      try {
        const r = await ioExport(sky, fmt, opts);
        ioDownload(sky, r);
        setTimeout(() => ioReport(sky, `Exported ${r.filename}`, r.report, []), 0);
        return true;
      } catch (e) { return e.message; }
    } });
}

/* ------------------------------------------------------------ import */
export function ioOpenImport(sky, fmt) {
  const F = ioForm();
  const o = { units: "auto", fromCurrent: true };
  const st = { text: null, name: "" };
  const fname = ioEl("span", { class: "muted", id: "ioFileName", text: "No file selected." });
  const input = ioEl("input", { type: "file", accept: IO_IMPORT[fmt].accept, id: "ioFile", class: "dk-file" });
  input.addEventListener("change", () => {
    const f = input.files && input.files[0];
    if (!f) return;
    st.name = f.name;
    fname.textContent = `${f.name} · ${f.size} bytes`;
    const rd = new FileReader();
    rd.onload = () => { st.text = String(rd.result || ""); };
    rd.readAsText(f);
  });
  const body = [ioGroup("File", [ioEl("div", { class: "xc-file" }, [input, fname])])];
  if (fmt === "cis2") body.push(ioGroup("Units", [F.select(o, "units", [["auto", "from the file"], ...IO_LEN], { label: "Length unit", id: "ioImpUnits" })]));
  if (fmt === "sdnf" || fmt === "cis2") body.push(ioGroup("Sections", [F.check(o, "fromCurrent", { label: "Section definitions from the current model", id: "ioFromCur", hint: "else steel databases, then a placeholder" })]));
  const dlg = ioDialog({ id: "ioImport", title: `Import ${IO_IMPORT[fmt].label}`, size: "md", form: F, okLabel: "Import",
    intro: "Replaces the working model with the imported one.", body,
    onOk: async () => {
      if (st.text == null) return "Choose a file first.";
      if (sky.store.dirty) {
        const ok = await ioConfirm({ id: "ioConfirmImport", title: "Import model", html: "You have unsaved changes — import and discard them?", okLabel: "Import" });
        if (!ok) return false;
      }
      return ioRunImport(sky, fmt, st.text, o, st.name);
    } });
  dlg.setFile = (text, name) => { st.text = text; st.name = name || "file"; fname.textContent = st.name; };
  return dlg;
}

async function ioRunImport(sky, fmt, text, o = {}, name = "") {
  const body = { text, sections_from_current: o.fromCurrent !== false };
  if (fmt === "cis2" && o.units && o.units !== "auto") body.units = o.units;
  try {
    const r = await ioImport(sky, fmt, body);
    if (sky.adoptModel) sky.adoptModel(r.model, null);
    else sky.store.model = r.model;
    sky.toast && sky.toast("Model imported", `${IO_IMPORT[fmt].label} · ${name || "file"}`, "info", 4000);
    setTimeout(() => ioReport(sky, `Imported ${name || IO_IMPORT[fmt].label}`, r.report, r.warnings || []), 0);
    return true;
  } catch (e) { return e.message; }
}

/* ------------------------------------------------------------ init */
export function initInterop(sky) {
  if (!sky) return;
  sky.interop = {
    EXPORT: Object.keys(IO_EXPORT), IMPORT: Object.keys(IO_IMPORT),
    openExport: fmt => ioOpenExport(sky, fmt), openImport: fmt => ioOpenImport(sky, fmt),
    exportNow: (fmt, opts = {}) => ioExport(sky, fmt, opts),
    importNow: async (fmt, body) => { const r = await ioImport(sky, fmt, body); if (sky.adoptModel) sky.adoptModel(r.model, null); return r; },
    state: ioState,
  };
  if (!(sky.menu && sky.menu.add)) return;
  for (const [fmt, f] of Object.entries(IO_IMPORT)) sky.menu.add("File/Import", { label: f.label + "…", act: "file-iop-imp-" + fmt, fn: () => ioOpenImport(sky, fmt) });
  for (const [fmt, f] of Object.entries(IO_EXPORT)) sky.menu.add("File/Export", { label: f.label + "…", act: "file-iop-exp-" + fmt, fn: () => ioOpenExport(sky, fmt) });
}
