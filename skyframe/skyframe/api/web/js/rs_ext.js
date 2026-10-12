/* SkyFrame — response-spectrum completeness in the Load Case Data dialog
   (ETABS parity, CONTRACT "Response spectrum: eccentricity, U3 / angle,
   directional combination, shell forces").

     Loads Applied            U1 / U2 / U3 direction + excitation angle,
                              optional several directions in one case
                              (dir · function · scale table)
     Directional Combination  SRSS · CQC3 · Absolute (+ scale factor)
     Diaphragm Eccentricity   ratio · ASCE 7 Ax amplification

   Hooked into js/dlgstd_cases.js editRsCase (direction options + one body
   line). Every key is written only when it differs from the backend default
   and removed otherwise, so OK with defaults leaves the model byte-identical.
   The model stays SI (angles are degrees in the model and in every unit
   system; the eccentricity is a ratio). */

import { el as rxEl, isNum as rxIsNum, dkGroup as rxGroup, dkNote as rxNote } from "./dlgkit.js";

export const RSX_DIRS = [["X", "U1 (global X)"], ["Y", "U2 (global Y)"], ["Z", "U3 (vertical Z)"]];
export const rsxDirOptions = RSX_DIRS;
export const RSX_DIR_COMBOS = [["SRSS", "SRSS"], ["CQC3", "CQC3"], ["ABS", "Absolute"]];
export const RSX_DEFAULTS = { angle: 0, dir_combo: "SRSS", dir_sf: 1, ecc: 0, ecc_ax: false };
const QUAD = new Set(["CQC", "SRSS", "DSC"]);

/** Set obj[key] = v, or delete it when v equals the backend default. */
function setOpt(obj, key, v) {
  if (v === null || v === undefined || v === RSX_DEFAULTS[key]) delete obj[key];
  else obj[key] = v;
}

/** The case's effective loads (mirrors core/rs_ext.effective_loads). */
export function rsxLoads(c) {
  if (Array.isArray(c.loads) && c.loads.length)
    return c.loads.map(l => ({ dir: l.dir, scale: rxIsNum(l.scale) ? l.scale : 1, function: l.function || "" }));
  return [{ dir: c.direction || "X", scale: 1, function: "" }];
}

/** Validation of the extension keys (same rules as core/rs_ext.validate). */
export function rsxValidate(c) {
  const dirs = rsxLoads(c).map(l => l.dir);
  if (new Set(dirs).size !== dirs.length) return "A direction is listed twice — each of U1, U2, U3 at most once.";
  if ((c.dir_combo || "SRSS") === "CQC3") {
    if (!(dirs.includes("X") && dirs.includes("Y"))) return "CQC3 needs both U1 and U2 in the directions table.";
    if (!QUAD.has(c.combo_method || "CQC") || c.rigid_response || c.include_missing_mass)
      return "CQC3 needs CQC, SRSS or Double Sum modal combination without rigid response or missing mass.";
  }
  if (rxIsNum(c.dir_sf) && (c.dir_sf < 0 || c.dir_sf > 1)) return "The absolute scale factor must be between 0 and 1.";
  if (rxIsNum(c.ecc) && c.ecc > 0 && !dirs.some(d => d === "X" || d === "Y")) return "Eccentricity needs a horizontal (U1 or U2) direction.";
  return "";
}

/** Extra dialog groups for editRsCase. F = dkForm, c = draft case, D = draft model. */
export function rsxGroups(F, c, D) {
  const fns = Object.keys((D && D.spectrum_functions) || {});
  const fnOpts = [["", "(case function)"], ...fns.map(f => [f, f])];
  const multi = { on: Array.isArray(c.loads) && c.loads.length > 0 };
  const rows = multi.on ? c.loads.map(l => ({ dir: l.dir, function: l.function || "", scale: rxIsNum(l.scale) ? l.scale : 1 })) : [];
  const writeRows = () => {
    if (!multi.on || !rows.length) { delete c.loads; return; }
    c.loads = rows.map(r => ({ dir: r.dir, scale: +r.scale, function: r.function || "" }));
    c.direction = rows[0].dir;                       // label stays valid
  };
  const vertNote = () => {
    const vert = rsxLoads(c).some(l => l.dir === "Z");
    const mo = (D && D.mass_options) || {};
    return vert && !mo.include_vertical ? "U3 needs vertical mass: Define › Mass Source › include vertical (or nodal mz)." : "";
  };
  const angle = F.num(c, "angle", { label: "Excitation Angle", unit: "deg", id: "rsxAngle", def: 0,
    read: () => c.angle ?? 0, write: v => setOpt(c, "angle", v),
    hint: "rotates U1/U2 in plan (CCW from +X); U3 is unaffected" });
  const tbl = F.table(rows, [
    { key: "dir", label: "Direction", type: "select", options: RSX_DIRS },
    { key: "function", label: "Function", type: "select", options: fnOpts },
    { key: "scale", label: "Scale Factor", type: "num" },
  ], {
    id: "rsxLoads", addLabel: "+ Add direction", empty: "One direction only (the Load Direction above).",
    newRow: () => { const free = ["X", "Y", "Z"].find(d => !rows.some(r => r.dir === d)); return free ? { dir: free, function: "", scale: 1 } : null; },
    canAdd: () => rows.length < 3,
    validateRow: (r, i) => !rxIsNum(r.scale) ? `Row ${i + 1}: the scale factor must be a number.` : "",
    validate: () => rsxValidate({ ...c, loads: rows.length ? rows : undefined }),
    onChange: () => { writeRows(); refresh(); },
  });
  const tblWrap = rxEl("div", { class: "rsx-loads", id: "rsxLoadsWrap" }, [tbl]);
  const multiCb = F.check(multi, "on", { label: "Several directions in this case (ETABS U1 / U2 / U3 table)", id: "rsxMulti",
    write: v => {
      multi.on = v;
      if (v && !rows.length) rows.push({ dir: c.direction || "X", function: "", scale: 1 });
      writeRows(); F.refresh(); refresh();
    } });
  const vnote = rxNote("", "rsx-vnote");
  vnote.id = "rsxVertNote";

  const combo = F.select(c, "dir_combo", RSX_DIR_COMBOS, { label: "Directional Combination", id: "rsxDirCombo",
    read: () => c.dir_combo || "SRSS", write: v => { setOpt(c, "dir_combo", v); refresh(); },
    validate: () => rsxValidate(c),
    hint: "SRSS · CQC3 (Menun–Der Kiureghian, U1/U2 critical angle, U3 by SRSS) · Absolute" });
  const sf = F.num(c, "dir_sf", { label: "Absolute Scale Factor", id: "rsxDirSf", min: 0, max: 1, def: 1,
    read: () => c.dir_sf ?? 1, write: v => setOpt(c, "dir_sf", v),
    hint: "1 = absolute sum · 0.3 = the 100/30 rule" });
  const sfWrap = rxEl("div", { id: "rsxSfWrap" }, [sf]);
  const comboGroup = rxGroup("Directional Combination", [combo, sfWrap], { id: "rsxComboGroup" });

  const ecc = F.num(c, "ecc", { label: "Eccentricity Ratio", id: "rsxEcc", min: 0, max: 0.5, def: 0,
    read: () => c.ecc ?? 0, write: v => { setOpt(c, "ecc", v); refresh(); },
    hint: "ETABS diaphragm eccentricity (e.g. 0.05): ± story torques from the RS story forces × ratio × perpendicular dimension, added to the RS envelope" });
  const ax = F.check(c, "ecc_ax", { label: "Amplify torques by ASCE 7 Ax = (δmax / 1.2 δavg)²", id: "rsxEccAx",
    write: v => setOpt(c, "ecc_ax", !!v) });
  const eccGroup = rxGroup("Diaphragm Eccentricity", [ecc, ax], { id: "rsxEccGroup" });

  function refresh() {
    const hide = (node, h) => { node.hidden = h; node.classList.toggle("dk-disabled", h); };
    hide(tblWrap, !multi.on);
    hide(comboGroup, !multi.on);
    hide(sfWrap, (c.dir_combo || "SRSS") !== "ABS");
    vnote.textContent = vertNote();
    hide(vnote, !vnote.textContent);
  }
  const loadsGroup = rxGroup("Directions & Angle", [angle, multiCb, tblWrap, vnote], { id: "rsxDirGroup" });
  // follow the dialog's own Load Direction select (U3 note) via the touch hook
  const prevTouch = F.onTouch;
  F.onTouch = () => { if (prevTouch) prevTouch(); refresh(); };
  refresh();
  return [loadsGroup, comboGroup, eccGroup];
}

/** Short summary used in lists ("U1 @30° · ecc 5%"). */
export function rsxSummary(c) {
  const lab = { X: "U1", Y: "U2", Z: "U3" };
  const parts = [rsxLoads(c).map(l => lab[l.dir] || l.dir).join("+")];
  if (rxIsNum(c.angle) && c.angle) parts.push(`@${+c.angle.toFixed(2)}°`);
  if (Array.isArray(c.loads) && c.loads.length > 1) parts.push(c.dir_combo || "SRSS");
  if (rxIsNum(c.ecc) && c.ecc) parts.push(`ecc ${+(c.ecc * 100).toFixed(2)}%`);
  return parts.join(" · ");
}

export default { rsxGroups, rsxDirOptions, rsxValidate, rsxLoads, rsxSummary, RSX_DIRS, RSX_DIR_COMBOS };
