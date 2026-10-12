/* SkyFrame — mock backend for POST /api/edit/<op> (?mock=1).

   Runs the shared client geometry (js/edit_geom.js, a 1:1 port of
   skyframe/core/edit.py) and returns the exact response shape of the live
   endpoint: { model, summary }. Throws Error(message) where the backend
   answers 400. The mock-only "_mock_params" seed rides along untouched
   (key order preserved). */

import { applyEdit as meApplyEdit, EDIT_OPS as meEditOps } from "./edit_geom.js";
import { wbApplyEdit as meWbApplyEdit, WB_SHELL_OPS as meWbOps } from "./wb_shellgeom.js";   // Wave B: Edit Shells / ramp / wall stack / relabel

export function mockEdit(model, op, selection, params) {
  if (meWbOps.includes(op)) return meWbApplyEdit(model, op, selection || {}, params || {});   // Wave B ops (js/wb_shellgeom.js)
  if (!meEditOps.includes(op)) throw new Error(`unknown edit op '${op}' (one of ${meEditOps.join(", ")})`);
  return meApplyEdit(model, op, selection || {}, params || {});
}
