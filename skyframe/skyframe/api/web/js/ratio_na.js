/* ================================================================
   Ratio-based story checks — "n/a (negligible drift)" display helpers.
   Backend contract: CONTRACT.md "Ratio-based story checks: negligible-drift
   significance".  A ratio slot (tors_ratio_x/_y, stiff_ratio, diaphragm
   tors_ratio_*) or a stiffness slot (kx/ky) is NOT APPLICABLE when its
   entry carries na_reason[key] (the ratio is then null, a stiffness 0.0).
   These helpers render "n/a" with the reason as a tooltip.
   ================================================================ */
export const RNA_TEXT = "n/a";
/** Table columns (Show Tables) whose null cell means n/a, not "no value". */
export const RNA_TABLE_KEYS = new Set(["disp_ratio", "drift_ratio"]);

const escA = s => String(s).replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** The n/a reason string for obj[key], or "" when the slot applies. */
export function rnaReason(obj, key) {
  const r = obj && obj.na_reason;
  return r && typeof r === "object" && r[key] ? String(r[key]) : "";
}
export const rnaIsNa = (obj, key) => !!rnaReason(obj, key);
/** "n/a" span with the reason as tooltip. */
export function rnaHtml(obj, key) {
  return `<span class="dim rna-na" title="${escA(rnaReason(obj, key))}">${RNA_TEXT}</span>`;
}
/** ``html`` unless obj[key] is n/a (then the n/a span). */
export const rnaCell = (obj, key, html) => rnaIsNa(obj, key) ? rnaHtml(obj, key) : html;
/** CSV value: "n/a" for an n/a slot, else ``v``. */
export const rnaCsv = (obj, key, v) => rnaIsNa(obj, key) ? RNA_TEXT : v;
