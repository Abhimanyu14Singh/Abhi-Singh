/* SkyFrame Wave C (C9) — anisotropic shell materials and the material angle.

   CONTRACT "Anisotropic shells, material angle, Section Designer
   moment-curvature, layered shells with any material, results CSV folder"
   (skyframe/core/aniso.py).

     Material.symmetry "anisotropic"  aniso = {Q11, Q22, Q12, Q16, Q26, Q66,
                                               G13, G23} (kPa, material axes)
     Material.material_angle          deg, default angle of every shell of
                                      the material (null = 0, omitted)
     ShellSection.material_angle      deg, overrides the material's angle
                                      (null = the material's, omitted)

   The angle runs from the shell element local axis 1 to the material axis
   1, positive about local 3.  Qbar = Tᵀ Q T (90° swaps 1 ↔ 2).

   Hooks: js/wb_ortho.js (symmetry option + write + group: the Material
   Property Data dialog), js/wb_slab.js (Slab / Wall Property Data: the
   Material Angle row; laminate layers at any angle), js/mock_wb.js (mock
   validation).  Moduli through js/units.js ("modulus"); SI store. */

import { el as wcEl, esc as wcEsc, isNum as wcIsNum, dkGroup as wcGroup, dkNote as wcNote } from "./dlgkit.js";
import wcU from "./units.js";

export const WC9_ANISO_KEYS = ["Q11", "Q22", "Q12", "Q16", "Q26", "Q66", "G13", "G23"];
export const WC9_SYM_OPTION = ["anisotropic", "Anisotropic (user Q matrix, shells)"];

/* ---------------------------------------------------------------- math */
/** Plane-stress Q (3×3) of an orthotropic / isotropic material dict. */
export function wc9OrthoQ(mat) {
  const o = mat.ortho || {};
  const E1 = mat.E, nu = mat.nu ?? 0.2;
  const E2 = mat.symmetry === "orthotropic" ? o.E2 : E1;
  const G12 = mat.symmetry === "orthotropic" ? o.G12 : E1 / (2 * (1 + nu));
  const n21 = nu * E2 / E1, d = 1 - nu * n21;
  return [[E1 / d, nu * E2 / d, 0], [nu * E2 / d, E2 / d, 0], [0, 0, G12]];
}
export function wc9AnisoQ(a) {
  return [[a.Q11, a.Q12, a.Q16], [a.Q12, a.Q22, a.Q26], [a.Q16, a.Q26, a.Q66]];
}
/** Qbar = Tᵀ Q T, T = element → material strain transformation. */
export function wc9Rotate(Q, deg) {
  const t = (deg || 0) * Math.PI / 180, c = Math.cos(t), s = Math.sin(t);
  const T = [[c * c, s * s, c * s], [s * s, c * c, -c * s], [-2 * c * s, 2 * c * s, c * c - s * s]];
  const QT = Q.map(r => [0, 1, 2].map(j => r[0] * T[0][j] + r[1] * T[1][j] + r[2] * T[2][j]));
  return [0, 1, 2].map(i => [0, 1, 2].map(j => T[0][i] * QT[0][j] + T[1][i] * QT[1][j] + T[2][i] * QT[2][j]));
}
const det3 = M => M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1]) - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0]) + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0]);
/** Effective in-plane modulus along element axis 1: 1 / (Q⁻¹)₁₁. */
export function wc9Ex(Q) {
  const d = det3(Q);
  const c11 = Q[1][1] * Q[2][2] - Q[1][2] * Q[2][1];
  return d / c11;
}

/** "" or the first problem of an anisotropic material (backend wording). */
export function anisoProblem(mat) {
  const a = mat.aniso;
  if (!a || typeof a !== "object") return "symmetry 'anisotropic' needs aniso = {Q11, Q22, Q12, Q16, Q26, Q66, G13, G23} (kPa)";
  for (const k of WC9_ANISO_KEYS) if (!wcIsNum(a[k])) return `aniso ${k} must be a finite value in kPa`;
  for (const k of ["Q11", "Q22", "Q66", "G13", "G23"]) if (!(a[k] > 0)) return `aniso ${k} must be > 0 kPa`;
  const Q = wc9AnisoQ(a);
  const m2 = Q[0][0] * Q[1][1] - Q[0][1] * Q[0][1];
  if (!(m2 > 0 && det3(Q) > 0)) return "the anisotropic matrix Q must be positive definite";
  return "";
}

/** Symmetry write hook (js/wb_ortho.js): switching to anisotropic seeds Q
    from the current orthotropic / isotropic constants; leaving it drops
    the matrix.  Returns true when it handled the anisotropic case. */
export function wc9SymWrite(mat, v) {
  if (v === "anisotropic") {
    if (!mat.aniso) {
      const Q = wc9OrthoQ(mat);
      const o = mat.ortho || {};
      const G = mat.E / (2 * (1 + (mat.nu ?? 0.2)));
      mat.aniso = { Q11: Q[0][0], Q22: Q[1][1], Q12: Q[0][1], Q16: 0, Q26: 0, Q66: Q[2][2],
        G13: mat.symmetry === "orthotropic" ? o.G13 : G, G23: mat.symmetry === "orthotropic" ? o.G23 : G };
    }
    mat.symmetry = v;
    delete mat.ortho;
    return true;
  }
  delete mat.aniso;
  return false;
}

/** Material Angle field (deg) bound to obj.material_angle; blank = unset. */
export function wc9AngleRow(F, obj, { id = "wc9MatAngle", label = "Material Angle", nullText = "0 (axes 1/2 = element 1/2)", hint, onChange } = {}) {
  return F.num(obj, "material_angle", {
    label, unit: "deg", allowNull: true, nullText, id, hint,
    title: "Angle from the shell element local axis 1 to the material axis 1 (about local 3)",
    write: v => { if (v == null) delete obj.material_angle; else obj.material_angle = v; },
    onChange,
  });
}

/** Material Property Data group for an anisotropic material. */
export function wc9AnisoGroup(F, mat, rebuild) {
  if (!mat.aniso) wc9SymWrite(mat, "anisotropic");
  const a = mat.aniso;
  const v = () => anisoProblem(mat);
  const prev = wcEl("p", { class: "muted dk-note", id: "wc9AnisoPreview" });
  const paint = () => {
    const e = v();
    if (e) { prev.textContent = e; return; }
    const ang = mat.material_angle || 0;
    const Qb = wc9Rotate(wc9AnisoQ(a), ang);
    const f = x => wcEsc(wcU.fmt("modulus", x, 0));
    prev.innerHTML = `In the element axes at ${wcEsc(String(+ang.toFixed(3)))}°: Q̄11 ${f(Qb[0][0])}, Q̄16 ${f(Qb[0][2])}, ` +
      `E<sub>x</sub> ${f(wc9Ex(Qb))} ${wcEsc(wcU.label("modulus"))} (shell stiffness is exact; transverse shear rotates without its cross term).`;
  };
  const q = (k, label, extra = {}) => F.num(a, k, { label, kind: "modulus", id: "wc9Q_" + k, onChange: () => { F.refresh(); paint(); }, validate: x => { const t = { ...mat, aniso: { ...a, [k]: x } }; return anisoProblem(t); }, ...extra });
  const fromOrtho = wcEl("button", { type: "button", class: "btn btn-small", id: "wc9FromOrtho", title: "Fill Q from the orthotropic constants E1 = E, E2 = E, ν12 = ν rotated by the material angle" }, ["Q from E, ν (isotropic)"]);
  fromOrtho.addEventListener("click", () => {
    const Q = wc9OrthoQ({ ...mat, symmetry: "isotropic" });
    Object.assign(a, { Q11: Q[0][0], Q22: Q[1][1], Q12: Q[0][1], Q16: 0, Q26: 0, Q66: Q[2][2] });
    F.touched = true; F.onTouch && F.onTouch();
    rebuild();
  });
  const g = wcGroup("Anisotropic Property Data", [
    F.num(mat, "E", { label: "Frame Modulus, E", kind: "modulus", gt: 0, id: "dsMatE", hint: "frames use E and G = Q66" }),
    F.num(mat, "nu", { label: "Frame Poisson's Ratio, ν", min: 0, lt: 0.5, id: "dsMatNu" }),
    q("Q11", "Q11 (σ11 / ε11)", { gt: 0 }), q("Q22", "Q22 (σ22 / ε22)", { gt: 0 }), q("Q12", "Q12 (σ11 / ε22)"),
    q("Q16", "Q16 (σ11 / γ12) — coupling"), q("Q26", "Q26 (σ22 / γ12) — coupling"), q("Q66", "Q66 (σ12 / γ12)", { gt: 0 }),
    q("G13", "Transverse Shear, G13", { gt: 0 }), q("G23", "Transverse Shear, G23", { gt: 0 }),
    wc9AngleRow(F, mat, { onChange: paint }),
    wcEl("div", { class: "dk-row" }, [wcEl("span", { class: "dk-label", text: "Helpers" }), fromOrtho]),
    prev,
  ], { id: "wc9AnisoGroup", note: "User plane-stress matrix in the MATERIAL axes 1 / 2 (engineering shear γ12). Must be positive definite. Shells only — layered and laminated sections take it per layer." });
  paint();
  return g;
}

/* ---------------------------------------------------- shell sections */
/** Slab / Wall Property Data: the section Material Angle row (blank = the
    material's).  Shown for every plain / laminate section; it only acts on
    orthotropic / anisotropic materials and laminates. */
export function wc9ShellAngleRow(F, s, D, { onChange } = {}) {
  return wc9AngleRow(F, s, {
    id: "wc9ShAngle", label: "Material Angle",
    nullText: "from the material", onChange,
    hint: "blank = the material's angle; acts on orthotropic / anisotropic materials and laminates",
  });
}

/* ---------------------------------------------------------- mock */
/** ?mock=1 mirror of core/aniso.validate_model (first problem or ""). */
export function wc9MockValidate(m) {
  if (!m || typeof m !== "object") return "";
  const mats = m.materials || {};
  for (const [n, mt] of Object.entries(mats)) {
    if (mt.material_angle != null && !wcIsNum(mt.material_angle)) return `Material '${n}': material_angle must be a finite angle in degrees`;
    if (mt.symmetry === "anisotropic") { const e = anisoProblem(mt); if (e) return `Material '${n}': ${e}`; }
    else if (mt.aniso != null) return `Material '${n}': 'aniso' constants need symmetry 'anisotropic'`;
  }
  for (const [n, s] of Object.entries(m.shell_sections || {})) {
    if (s.material_angle != null) {
      if (!wcIsNum(s.material_angle)) return `Shell section ${n}: material_angle must be a finite angle in degrees`;
      if (s.slab) return `Shell section ${n}: material_angle is not supported on ribbed / waffle / deck / drop slab types`;
      if (s.layered) return `Shell section ${n}: a layered section sets the angle per layer (layer 'angle'), not material_angle`;
    }
  }
  return "";
}

export default { wc9Rotate, wc9OrthoQ, wc9AnisoQ, wc9Ex, anisoProblem, wc9SymWrite, wc9AngleRow, wc9AnisoGroup, wc9ShellAngleRow, wc9MockValidate };
