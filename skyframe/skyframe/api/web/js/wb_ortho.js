/* SkyFrame Wave B — orthotropic materials in Material Property Data.

   CONTRACT "Orthotropic materials, slab / deck property types, cold-formed
   and composite sections, named line / area springs" (skyframe/core/
   ortho.py).  Material.symmetry "orthotropic": E = E1, ν = ν12 and
   ortho = {E2, G12, G13, G23} (kPa).  Material axes 1 / 2 = shell element
   local axes 1 / 2; frames use E1 and G12.

   Hooks (js/dlgstd_defs.js editMaterial): the Directional Symmetry select
   gets the extra option + write (wbOrthoSymOptions / wbOrthoSymWrite) and
   the "Isotropic Property Data" group is replaced by wbOrthoGroup when the
   material is orthotropic.  Presets: EN 338 C24 / EN 14080 GL24h (mean
   values, same numbers as core/ortho.py TIMBER_PRESETS). */

import { dkGroup as woGroup, el as woEl, isNum as woIsNum } from "./dlgkit.js";

export const TIMBER_PRESETS = {
  C24: { E1: 11.0e6, E2: 0.37e6, nu12: 0.30, G12: 0.69e6, G13: 0.69e6, G23: 0.05e6, unit_weight: 4.20, label: "EN 338 C24 softwood" },
  GL24h: { E1: 11.5e6, E2: 0.30e6, nu12: 0.30, G12: 0.65e6, G13: 0.65e6, G23: 0.065e6, unit_weight: 4.20, label: "EN 14080 GL24h glulam" },
};
export const CLT_LAYUPS = {
  "CLT 100 L3s": ["C24", [0.03, 0.04, 0.03]], "CLT 120 L3s": ["C24", [0.04, 0.04, 0.04]],
  "CLT 160 L5s": ["C24", [0.04, 0.02, 0.04, 0.02, 0.04]], "CLT 200 L5s": ["C24", [0.04, 0.04, 0.04, 0.04, 0.04]],
};
export const wbOrthoSymOptions = [["orthotropic", "Orthotropic (shells / timber)"]];

/** Symmetry write: switching to orthotropic seeds ortho from the isotropic E / ν. */
export function wbOrthoSymWrite(mat, v) {
  mat.symmetry = v;
  if (v === "orthotropic") {
    if (!mat.ortho) { const G = mat.E / (2 * (1 + (mat.nu ?? 0.2))); mat.ortho = { E2: mat.E, G12: G, G13: G, G23: G }; }
  } else delete mat.ortho;
}

/** Plane-stress check ν12²·E2/E1 < 1 (message or ""). */
export function orthoProblem(mat) {
  const o = mat.ortho || {};
  for (const k of ["E2", "G12", "G13", "G23"]) if (!(woIsNum(o[k]) && o[k] > 0)) return `${k} must be greater than zero.`;
  if (!(mat.nu >= 0)) return "ν12 must be zero or more.";
  if (mat.nu * mat.nu * o.E2 / mat.E >= 1) return "ν12²·E2/E1 must be below 1 (positive-definite stiffness).";
  return "";
}

/** "Orthotropic Property Data" group (or null for other symmetries). */
export function wbOrthoGroup(F, mat, rebuild) {
  if (mat.symmetry !== "orthotropic") return null;
  if (!mat.ortho) wbOrthoSymWrite(mat, "orthotropic");
  const o = mat.ortho;
  const v = () => orthoProblem(mat);
  const presets = woEl("div", { class: "dk-row wbo-presets" }, [woEl("span", { class: "dk-label", text: "Timber Preset" }),
    ...Object.entries(TIMBER_PRESETS).map(([k, p]) => {
      const b = woEl("button", { type: "button", class: "btn btn-small", id: "wboPreset_" + k, title: p.label }, [k]);
      b.addEventListener("click", () => {
        Object.assign(mat, { E: p.E1, nu: p.nu12, unit_weight: p.unit_weight, mass_density: +(p.unit_weight / 9.80665).toFixed(6) });
        Object.assign(o, { E2: p.E2, G12: p.G12, G13: p.G13, G23: p.G23 });
        if (!mat.notes) mat.notes = p.label;
        F.touched = true; F.onTouch && F.onTouch();
        rebuild();
      });
      return b;
    })]);
  return woGroup("Orthotropic Property Data", [
    F.num(mat, "E", { label: "Modulus E1 (local 1)", kind: "modulus", gt: 0, id: "dsMatE", onChange: () => F.refresh() }),
    F.num(o, "E2", { label: "Modulus E2 (local 2)", kind: "modulus", gt: 0, id: "wboE2", validate: () => "" }),
    F.num(mat, "nu", { label: "Poisson's Ratio, ν12", min: 0, id: "dsMatNu", validate: x => { const t = { ...mat, nu: x }; return orthoProblem(t); } }),
    F.num(o, "G12", { label: "In-plane Shear Modulus, G12", kind: "modulus", gt: 0, id: "wboG12" }),
    F.num(o, "G13", { label: "Transverse Shear, G13", kind: "modulus", gt: 0, id: "wboG13" }),
    F.num(o, "G23", { label: "Transverse Shear, G23 (rolling)", kind: "modulus", gt: 0, id: "wboG23", validate: () => v() }),
    presets,
  ], { id: "wboGroup", note: "Axes 1 / 2 = the shell element local axes. Shells use the exact orthotropic plate (A = hQ, D = h³Q/12, S = 5/6·G·h); frames use E1 and G12." });
}

export default { wbOrthoSymOptions, wbOrthoSymWrite, wbOrthoGroup, orthoProblem, TIMBER_PRESETS, CLT_LAYUPS };
