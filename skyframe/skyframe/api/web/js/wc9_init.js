/* SkyFrame Wave C (C9) — init (one line in app.js) + the results CSV
   folder export.

     Assign › Shell › Layered Section…               asn-slayer (js/wc9_layers.js)
     Tools › Section Moment-Curvature…               tool-wc9-mcurv (js/wc9_mcurv.js)
     File › Export › Results Tables to CSV Folder…   file-wc9-res-csv
     Material Property Data: Anisotropic + Material Angle (js/wc9_aniso.js
     via js/wb_ortho.js); Slab / Wall Property Data: Material Angle and
     laminate layers at any angle (via js/wb_slab.js).

   CSV folder: live POST /api/tables/export/csv → ZIP of
   <model>_results/{_index,_units,_meta}.csv + one CSV per table
   (skyframe/io/results_csv.py).  ?mock=1: the same folder is zipped in the
   browser (stored ZIP, wc9Zip) from the mock tables. */

import { initWc9Layers as wiLayers } from "./wc9_layers.js";
import { initWc9MomentCurvature as wiMc } from "./wc9_mcurv.js";

/* ---------------------------------------------- tiny ZIP (stored) writer */
const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
const crc32 = b => { let c = 0xffffffff; for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
/** files: [[name, text], …] → Uint8Array ZIP (no compression, DOS date 2000-01-01). */
export function wc9Zip(files) {
  const enc = new TextEncoder(), parts = [], central = [];
  let off = 0;
  const u16 = v => [v & 255, (v >>> 8) & 255], u32 = v => [v & 255, (v >>> 8) & 255, (v >>> 16) & 255, (v >>> 24) & 255];
  const DATE = (20 << 9) | (1 << 5) | 1;
  for (const [name, text] of files) {
    const nb = enc.encode(name), data = enc.encode(text), crc = crc32(data);
    const head = [...u32(0x04034b50), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(0), ...u16(DATE), ...u32(crc), ...u32(data.length), ...u32(data.length), ...u16(nb.length), ...u16(0)];
    parts.push(new Uint8Array(head), nb, data);
    central.push(new Uint8Array([...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(0), ...u16(DATE), ...u32(crc), ...u32(data.length), ...u32(data.length), ...u16(nb.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0), ...u32(off)]), nb);
    off += head.length + nb.length + data.length;
  }
  const cdSize = central.reduce((a, b) => a + b.length, 0);
  const end = new Uint8Array([...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(files.length), ...u16(files.length), ...u32(cdSize), ...u32(off), ...u16(0)]);
  const all = [...parts, ...central, end], out = new Uint8Array(all.reduce((a, b) => a + b.length, 0));
  let p = 0; for (const b of all) { out.set(b, p); p += b.length; }
  return out;
}

const q = v => { const s = v == null ? "" : String(v); return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
const csv = rows => rows.map(r => r.map(q).join(",")).join("\r\n") + "\r\n";
/** ?mock=1 CSV folder ZIP from Show-Tables table payloads (values as shown by the mock router, SI). */
export function wc9MockCsvZip(tabs, units, modelName) {
  const root = `${String(modelName || "model").replace(/[^\w.-]+/g, "_")}_results`;
  const files = [], idx = [["TableKey", "TableName", "TableGroup", "NumRows", "File"]], un = [["File", "Field", "Quantity", "Unit"]];
  const used = new Set();
  for (const t of tabs) {
    let fn = `${String(t.title || t.key).replace(/[\\/:*?"<>|[\]]+/g, "_")}.csv`;
    while (used.has(fn.toLowerCase())) fn = fn.replace(/\.csv$/, "_.csv");
    used.add(fn.toLowerCase());
    const cols = t.columns || [];
    files.push([`${root}/${fn}`, csv([cols.map(c => c.label), ...(t.rows || []).map(r => cols.map(c => r[c.key]))])]);
    idx.push([t.key, t.title, t.group, (t.rows || []).length, fn]);
    for (const c of cols) un.push([fn, c.label, c.quantity || "", ""]);
  }
  files.unshift([`${root}/_index.csv`, csv(idx)], [`${root}/_units.csv`, csv(un)],
    [`${root}/_meta.csv`, csv([["Item", "Value"], ["ProgramName", "SkyFrame"], ["Format", "skyframe-results-csv"], ["Version", 1], ["Units", "SI (mock)"], ["Model", modelName || ""]])],
    [`${root}/README.txt`, "SkyFrame analysis results - CSV folder (offline mock: SI values).\r\n"]);
  return wc9Zip(files);
}

export function initWc9(sky) {
  if (!sky) return;
  wiLayers(sky);
  wiMc(sky);
  if (sky.menu && sky.menu.add)
    sky.menu.add("File/Export", { label: "Results Tables to CSV Folder (.zip)…", act: "file-wc9-res-csv", hint: "one CSV per table · open format",
      fn: () => sky.resExport && sky.resExport.open("csv") });
}

export default { initWc9, wc9Zip, wc9MockCsvZip };
