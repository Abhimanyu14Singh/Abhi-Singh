/* SkyFrame — A8 results output: one entry point for app.js.
     js/spandrels.js   Define / Assign spandrel labels + Spandrel Forces
     js/resexport.js   results tables → Excel (.xlsx) / SQLite (.sqlite)
     js/reportplus.js  Create Report: table selection, per-case 3D
                       snapshots, Word (.docx) export
   The element / wall / NL Show-Tables tables come from the backend
   catalogue (mock: js/mock_restables.js). */

import { initSpandrels as a8Spandrels } from "./spandrels.js";
import { initResExport as a8ResExport } from "./resexport.js";
import { initReportPlus as a8ReportPlus } from "./reportplus.js";

export function initA8Results(sky) {
  for (const [name, fn] of [["spandrels", a8Spandrels], ["results export", a8ResExport], ["report plus", a8ReportPlus]]) {
    try { fn(sky); } catch (err) { console.error(`A8 ${name} init failed`, err); }
  }
}
