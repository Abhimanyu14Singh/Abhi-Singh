/* SkyFrame — mock-mode mirror of the backend validation for the ETABS
   Assign-parity fields (CONTRACT "Joint moments, ground displacement, shell
   load directions and joint patterns" + "Frame shear deformation and full
   property modifiers; shell membrane/bending modifiers").

   In ?mock=1 the "backend" accepts the model locally (POST /api/model echoes
   the payload), so every new field round-trips untouched. This module only
   adds the server's ValueError messages so mock saves reject the same bad
   input the live backend would (and the UI surfaces them the same way).
   Mock static solves ignore the new load effects (moments, settlements,
   directional / patterned shell loads, modifiers) — they never break. */

export const AREA_LOAD_DIRECTIONS = ["gravity", "global_x", "global_y", "global_z",
  "local_1", "local_2", "local_3"];
export const MEMBER_MOMENT_DIRECTIONS = ["local_x", "local_y", "local_z",
  "local_1", "local_2", "local_3", "global_x", "global_y", "global_z"];
export const MEMBER_FORCE_DIRECTIONS = ["gravity", "local_y", "global_x", "global_y", "global_z"];
export const GD_DOFS = ["ux", "uy", "uz", "rx", "ry", "rz"];

const fin = v => typeof v === "number" && isFinite(v);
const same = (a, b, tol = 1e-6) => a && b && a.length === 3 && b.length === 3 &&
  a.every((v, i) => Math.abs(+v - +b[i]) < tol);

/** Support test used by the backend for ground displacements: an explicit
    support, a point spring, or (no explicit supports) the auto base level. */
export function isSupportPoint(model, pt) {
  if (!pt || pt.length !== 3) return false;
  if ((model.supports || []).some(s => same(s.point, pt))) return true;
  if ((model.spring_supports || []).some(s => same(s.point, pt))) return true;
  if ((model.supports || []).length) return false;
  const z = baseLevel(model);
  return z != null && Math.abs(+pt[2] - z) < 1e-6;
}
export function baseLevel(model) {
  let z = Infinity;
  for (const m of model.members || []) z = Math.min(z, m.pi[2], m.pj[2]);
  for (const s of model.shells || []) for (const c of s.corners || []) z = Math.min(z, c[2]);
  return isFinite(z) ? z : null;
}

/** First validation error (backend wording) or "" when the new fields are valid. */
export function mockValidateAssign(model) {
  if (!model) return "";
  for (const s of Object.values(model.sections || {})) {
    for (const k of ["mod_A", "mod_I33", "mod_I22", "mod_J", "mod_As2", "mod_As3"])
      if (k in s && !(fin(s[k]) && s[k] > 0)) return `Section ${s.name}: ${k} must be finite and > 0 (got ${s[k]})`;
    for (const k of ["mod_mass", "mod_weight"])
      if (k in s && !(fin(s[k]) && s[k] >= 0)) return `Section ${s.name}: ${k} must be finite and >= 0 (got ${s[k]})`;
    for (const k of ["As2", "As3"])
      if (s[k] != null && !(fin(s[k]) && s[k] > 0)) return `Section ${s.name}: ${k} must be null or finite and > 0`;
    if ("shear_deformation" in s && typeof s.shear_deformation !== "boolean")
      return `Section ${s.name}: shear_deformation must be a bool`;
  }
  for (const s of Object.values(model.shell_sections || {})) {
    for (const k of ["f11", "f22", "f12", "m11", "m22", "m12", "v13", "v23"])
      if (k in s && !(fin(s[k]) && s[k] > 0)) return `Shell section ${s.name}: ${k} must be finite and > 0 (got ${s[k]})`;
    for (const k of ["mass", "weight"])
      if (k in s && !(fin(s[k]) && s[k] >= 0)) return `Shell section ${s.name}: ${k} must be finite and >= 0 (got ${s[k]})`;
  }
  const shells = new Map((model.shells || []).map(s => [s.uid, s]));
  for (const [pn, p] of Object.entries(model.patterns || {})) {
    for (const ml of p.member_loads || []) {
      if (ml.kind !== "moment") continue;
      if (!MEMBER_MOMENT_DIRECTIONS.includes(ml.direction))
        return `Pattern ${pn}: moment load direction must be one of ${MEMBER_MOMENT_DIRECTIONS.join(", ")}, got '${ml.direction}'`;
      if (!(fin(ml.a) && ml.a >= 0 && ml.a <= 1))
        return `Pattern ${pn}: moment load position a=${ml.a} outside [0, 1]`;
    }
    for (const al of p.area_loads || []) {
      const dir = al.direction || "gravity";
      if (!AREA_LOAD_DIRECTIONS.includes(dir))
        return `Pattern ${pn}: area load direction must be one of ${AREA_LOAD_DIRECTIONS.join(", ")}, got '${dir}'`;
      if (al.projected && !["gravity", "global_x", "global_y", "global_z"].includes(dir))
        return `Pattern ${pn}: projected area loads need a gravity/global direction`;
      const jp = al.joint_pattern;
      if (jp != null) {
        for (const k of ["a", "b", "c", "d"])
          if (k in jp && !fin(jp[k])) return `Pattern ${pn}: joint_pattern ${k} must be finite (got ${jp[k]})`;
        if (jp.zero_negative && jp.zero_positive) return `Pattern ${pn}: joint_pattern cannot zero both signs`;
      }
      const r = shells.get(al.region_uid);
      if (r && r.behavior === "membrane" && (dir !== "gravity" || jp != null))
        return `Pattern ${pn}: membrane region '${r.uid}' only takes uniform gravity area loads (direction/joint_pattern need shell behavior)`;
    }
    for (const g of p.ground_displacements || []) {
      if (!g.point || g.point.length !== 3) return `Pattern ${pn}: ground displacement needs a 3-coordinate point`;
      for (const k of GD_DOFS) if (k in g && !fin(g[k])) return `Pattern ${pn}: ground displacement values must be finite (got ${g[k]})`;
      if (!isSupportPoint(model, g.point))
        return `Pattern ${pn}: ground displacement at (${g.point.join(", ")}) is not at a support or spring point`;
    }
    for (const n of p.nodal_loads || [])
      for (const k of ["fx", "fy", "fz", "mx", "my", "mz"])
        if (k in n && !fin(n[k])) return `Pattern ${pn}: joint load ${k} must be finite`;
  }
  return "";
}
