/* ================================================================
   ?mock=1 — negligible-drift gate for the mock story diagnostics, the same
   rule as the backend (skyframe/core/ratio_significance.py; CONTRACT
   "Ratio-based story checks: negligible-drift significance"):

     q negligible  <=>  |q| < max(1e-9 m, 1e-6 * ref),
     ref = largest |ux| / |uy| story displacement of the case.

   Applied in place to results.irregularity / story_stiffness and to the
   named-diaphragm rows (results.diaphragms.{cases,combos,rs_cases}).  The
   mock has no diaphragm rotation for the story block, so the torsional
   denominator is the story translation in that direction.
   ================================================================ */
const ABS_FLOOR = 1e-9, REL_FLOOR = 1e-6;
const NA = "not applicable (negligible drift)";

function caseStory(r, cn) {
  for (const g of [r.cases, r.combos, r.rs_cases]) {
    const cd = g && g[cn];
    if (cd && cd.story) return cd.story;
  }
  return null;
}
function threshold(story) {
  let ref = 0;
  for (const e of Object.values(story || {}))
    for (const k of ["ux", "uy"]) if (isFinite(e && e[k])) ref = Math.max(ref, Math.abs(e[k]));
  return Math.max(ABS_FLOOR, REL_FLOOR * ref);
}
function markNa(e, key) {
  if (!e.na_reason || typeof e.na_reason !== "object") e.na_reason = {};
  e.na_reason[key] = NA;
}
function torsFlag(vals) {
  const v = vals.filter(x => x != null && isFinite(x));
  if (!v.length) return "none";
  const m = Math.max(...v);
  return m >= 1.4 ? "extreme" : m >= 1.2 ? "torsional" : "none";
}

/** Mutates ``r`` (mock results) in place. */
export function mockRatioNaApply(model, r) {
  if (!r) return r;
  const order = r.story_order || Object.keys((r.cases && Object.values(r.cases)[0] || {}).story || {});
  for (const [cn, by] of Object.entries(r.irregularity || {})) {
    const story = caseStory(r, cn);
    if (!story) continue;
    const thr = threshold(story);
    const stiff = (r.story_stiffness || {})[cn] || {};
    const negX = new Set(), negY = new Set();
    let px = 0, py = 0;
    for (const s of order) {
      const st = story[s];
      if (!st) continue;
      const dx = (st.ux || 0) - px, dy = (st.uy || 0) - py;
      px = st.ux || 0; py = st.uy || 0;
      if (Math.abs(dx) < thr) negX.add(s);
      if (Math.abs(dy) < thr) negY.add(s);
      const k = stiff[s];
      if (k) {
        if (negX.has(s)) { k.kx = 0; markNa(k, "kx"); }
        if (negY.has(s)) { k.ky = 0; markNa(k, "ky"); }
      }
    }
    let svx = 0, svy = 0;
    for (const s of order) {
      svx += Math.abs((story[s] || {}).shear_x || 0);
      svy += Math.abs((story[s] || {}).shear_y || 0);
    }
    const negP = svx >= svy ? negX : negY;
    order.forEach((s, i) => {
      const e = by[s], st = story[s];
      if (!e || !st) return;
      if (Math.abs(st.ux || 0) < thr) { e.tors_ratio_x = null; markNa(e, "tors_ratio_x"); }
      if (Math.abs(st.uy || 0) < thr) { e.tors_ratio_y = null; markNa(e, "tors_ratio_y"); }
      e.flag = torsFlag([e.tors_ratio_x, e.tors_ratio_y]);
      const above = order[i + 1];
      if (negP.has(s) || (above && negP.has(above))) {
        e.stiff_ratio = null; e.soft_flag = ""; markNa(e, "stiff_ratio");
      }
      if (e.na_reason) { const na = e.na_reason; delete e.na_reason; e.na_reason = na; }  // last key
    });
  }
  const dia = r.diaphragms;
  if (dia) {
    for (const grp of ["cases", "combos", "rs_cases"]) {
      for (const [cn, byStory] of Object.entries(dia[grp] || {})) {
        const thr = threshold(caseStory(r, cn) || {});
        for (const byDia of Object.values(byStory || {}))
          for (const row of Object.values(byDia || {})) {
            if (Math.abs(row.ux || 0) < thr) { row.tors_ratio_x = null; markNa(row, "tors_ratio_x"); }
            if (Math.abs(row.uy || 0) < thr) { row.tors_ratio_y = null; markNa(row, "tors_ratio_y"); }
          }
      }
    }
  }
  return r;
}
