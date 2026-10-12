/* SkyFrame Wave C — per-quantity unit catalogue (pure; mirror of
   skyframe/core/units_custom.py unit_catalog(), checked against GET
   /api/units/catalog). kind → [{label, si}] where SI = display × si
   (temperatures: °F affine, handled in js/units.js). */

const KIP = 4.4482216152605, LBF = KIP / 1000, TONF = 9.80665, KGF = 9.80665e-3;
export const FORCE = [["N", 1e-3], ["kN", 1], ["kgf", KGF], ["tonf", TONF], ["lb", LBF], ["kip", KIP]];
export const LENGTH = [["mm", 1e-3], ["cm", 1e-2], ["m", 1], ["in", 0.0254], ["ft", 0.3048]];
const PAIRS = [["N", "mm"], ["N", "m"], ["kN", "mm"], ["kN", "m"], ["kgf", "cm"], ["kgf", "m"], ["tonf", "m"], ["lb", "in"], ["lb", "ft"], ["kip", "in"], ["kip", "ft"]];
const F = Object.fromEntries(FORCE), L = Object.fromEntries(LENGTH);
const PAIR_KINDS = {
  moment: [1, 1, "{f}·{l}"], line_force: [1, -1, "{f}/{l}"], line_moment: [1, 0, "{f}·{l}/{l}"],
  unit_weight: [1, -3, "{f}/{l}³"], subgrade: [1, -3, "{f}/{l}³"], line_spring: [1, -2, "{f}/{l}/{l}"],
  stiffness: [1, -1, "{f}/{l}"], rot_stiffness: [1, 1, "{f}·{l}/rad"], damping_coeff: [1, -1, "{f}·s/{l}"],
  mass: [1, -1, "{f}·s²/{l}"], mass_density: [1, -4, "{f}·s²/{l}⁴"], mass_moi: [1, 1, "{f}·s²·{l}"],
  mass_per_length: [1, -2, "{f}·s²/{l}²"], mass_per_area: [1, -3, "{f}·s²/{l}³"],
};
const LEN_KINDS = { length: [1, ""], dim: [1, ""], small: [1, ""], disp: [1, ""], area: [2, "²"], inertia: [4, "⁴"], secmod: [3, "³"], accel: [1, "/s²"], velocity: [1, "/s"] };
const STRESS = ["Pa", "kPa", "MPa", "GPa", "N/mm²", "kN/m²", "kN/mm²", "kgf/cm²", "tonf/m²", "psi", "ksi", "psf", "ksf"];
const STRESS_SI = { "Pa": 1e-3, "kPa": 1, "MPa": 1e3, "GPa": 1e6, "N/mm²": 1e-3 / 1e-6, "kN/m²": 1, "kN/mm²": 1 / 1e-6, "kgf/cm²": KGF / 1e-4, "tonf/m²": TONF,
  "psi": LBF / 0.0254 ** 2, "ksi": KIP / 0.0254 ** 2, "psf": LBF / 0.3048 ** 2, "ksf": KIP / 0.3048 ** 2 };
const NAMED_MASS = { mass: [["kg", 1e-3], ["t", 1]], mass_density: [["kg/m³", 1e-3], ["t/m³", 1]], mass_moi: [["kg·m²", 1e-3], ["t·m²", 1]],
  mass_per_length: [["kg/m", 1e-3], ["t/m", 1]], mass_per_area: [["kg/m²", 1e-3], ["t/m²", 1]] };
export const MAX_DECIMALS = 10;

let CACHE = null;
export function unitCatalog() {
  if (CACHE) return CACHE;
  const out = {};
  for (const [kind, [p, suf]] of Object.entries(LEN_KINDS)) {
    out[kind] = LENGTH.map(([n, f]) => ({ label: `${n}${suf}`, si: f ** p }));
    if (kind === "accel") out[kind].push({ label: "g", si: 9.80665 });
  }
  out.force = FORCE.map(([n, f]) => ({ label: n, si: f }));
  for (const kind of ["pressure", "stress", "modulus"]) out[kind] = STRESS.map(n => ({ label: n, si: STRESS_SI[n] }));
  for (const [kind, [a, b, tpl]] of Object.entries(PAIR_KINDS)) {
    const rows = (NAMED_MASS[kind] || []).map(([n, s]) => ({ label: n, si: s }));
    const seen = new Set(rows.map(r => r.label));
    for (const [fn, ln] of PAIRS) {
      const lab = tpl.replace("{f}", fn).replace(/\{l\}/g, ln);
      if (seen.has(lab)) continue;
      seen.add(lab);
      rows.push({ label: lab, si: F[fn] ** a * L[ln] ** b });
    }
    out[kind] = rows;
  }
  out.temp = [{ label: "°C", si: 1 }, { label: "°F", si: 1 / 1.8 }];
  out.temp_delta = [{ label: "°C", si: 1 }, { label: "°F", si: 1 / 1.8 }];
  out.thermal_coeff = [{ label: "1/°C", si: 1 }, { label: "1/°F", si: 1.8 }];
  out.rotation = [{ label: "rad", si: 1 }, { label: "deg", si: Math.PI / 180 }];
  out.period = [{ label: "s", si: 1 }, { label: "ms", si: 1e-3 }];
  CACHE = out;
  return out;
}
export const unitRow = (kind, label) => (unitCatalog()[kind] || []).find(r => r.label === label) || null;
