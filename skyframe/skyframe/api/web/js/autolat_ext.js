/* SkyFrame — v1.19 Auto Lateral Loads: international seismic / wind codes
   and the ASCE 7 Fig. 27.3-8 wind load cases (CONTRACT "Auto lateral loads:
   international seismic and wind codes, ASCE 7 wind load cases").

   Plugged into the Define › Auto Lateral Loads dialog of js/loads116.js by a
   few commented hook lines: this module only supplies data (code list,
   default pattern names, parameter specs in the loads116 SPEC format,
   summary rows) plus the per-code notes / "Create all 12 cases" extras.
   Parameter spec: [key, label, units kind, default SI, {opt, sel, int, hint,
   omit}] — `omit` = a select value that is NOT sent (backend default).
   Every number goes through js/units.js (numField in loads116.js). */

// Wave B hook: TS 498 / KDS 41 / CFE wind + NTC long-period keys (js/waveb_alx.js)
import { WBX_WIND as wbxWind, WBX_DEFAULT_NAME as wbxDefaultName, WBX_SPEC as wbxSpec,
  WBX_NTC_SPEC as wbxNtcSpec, WBX_COEF_ROWS as wbxCoefRows, WBX_NTC_COEF as wbxNtcCoef,
  WBX_NOTES as wbxNotes, WBX_NTC_NOTE as wbxNtcNote } from "./waveb_alx.js";

const ALX_SEISMIC = [
  ["nzs1170_5", "NZS 1170.5:2004"],
  ["as1170_4", "AS 1170.4-2007"],
  ["gb50011", "GB 50011-2010"],
  ["tsc2018", "TSC 2018 (TBDY)"],
  ["kds41", "KDS 41 17 00:2019"],
  ["nscp2015", "NSCP 2015"],
  ["ntc_cdmx", "Mexico CDMX NTC-Sismo"],
];
const ALX_WIND = [
  ["asce7_16_wind", "ASCE 7-16 Wind"],
  ["en1991_wind", "EN 1991-1-4 Wind"],
  ["is875_wind", "IS 875-3 Wind"],
  ["as1170_2_wind", "AS/NZS 1170.2 Wind"],
  ["gb50009_wind", "GB 50009 Wind"],
  ...wbxWind,                                       // Wave B hook: TS 498 / KDS / CFE wind
];
export const ALX_OPTIONS = [...ALX_SEISMIC, ...ALX_WIND];
export const ALX_WIND_CODES = new Set(["asce7_22_wind", ...ALX_WIND.map(o => o[0])]);
export const ALX_DEFAULT_NAME = {
  nzs1170_5: "NZS1170", as1170_4: "AS1170", gb50011: "GB50011", tsc2018: "TSC2018",
  kds41: "KDS41", nscp2015: "NSCP2015", ntc_cdmx: "NTCSISMO",
  asce7_16_wind: "WIND16", en1991_wind: "WINDEN", is875_wind: "WINDIS",
  as1170_2_wind: "WINDAS", gb50009_wind: "WINDGB",
  ...wbxDefaultName,                                // Wave B hook
};

const STRUCT_SEL = [["steel_mrf", "Steel MRF"], ["concrete_mrf", "Concrete MRF"], ["ebf", "Steel EBF"], ["other", "Other"]];

/* ASCE 7 case / topography / gust keys (shared by 7-16 and the extended 7-22) */
const ASCE_CASE_SPEC = [
  ["load_case", "Load case (Fig. 27.3-8)", "none", 1, { int: true, sel: [[1, "Case 1 — full P"], [2, "Case 2 — 0.75 P + torsion"], [3, "Case 3 — 0.75 Px + 0.75 Py"], [4, "Case 4 — 0.563 (Px + Py) + torsion"]] }],
  ["ecc_sign", "Case 2/4 eccentricity", "none", 1, { int: true, sel: [[1, "+ e"], [-1, "− e"]] }],
  ["transverse_sign", "Case 3/4 transverse component", "none", 1, { int: true, sel: [[1, "+ (same sign)"], [-1, "− (opposite)"]] }],
  ["e_ratio", "Eccentricity e / B", "none", 0.15],
  ["topo_feature", "Topography K<sub>zt</sub> (§26.8)", "none", "", { omit: "", sel: [["", "None — use K<sub>zt</sub> above"], ["ridge", "2-D ridge"], ["escarpment", "2-D escarpment"], ["hill", "3-D axisymmetric hill"]].map(([v, t]) => [v, t.replace(/<[^>]+>/g, "")]) }],
  ["topo_H", "Hill height H", "length", null, { opt: true }],
  ["topo_Lh", "Half-length L<sub>h</sub>", "length", null, { opt: true }],
  ["topo_x", "Distance from crest x", "length", null, { opt: true }],
  ["topo_upwind", "Site position", "none", 1, { int: true, sel: [[1, "Upwind of crest"], [0, "Downwind of crest"]] }],
  ["gust", "Gust-effect factor (§26.11)", "none", "", { omit: "", sel: [["", "Given G"], ["rigid", "Rigid — calculated"], ["flexible", "Flexible — calculated (n1 < 1 Hz)"]] }],
  ["n1", "Natural frequency n<sub>1</sub> (Hz)", "none", null, { opt: true }],
  ["damping", "Damping ratio β", "none", 0.02],
];

export const ALX_SPEC = {
  nzs1170_5: [
    ["Z", "Hazard factor Z", "none", 0.4],
    ["soil", "Site subsoil class", "none", "C", { sel: ["A", "B", "C", "D", "E"].map(s => [s, s]) }],
    ["mu", "Ductility μ", "none", 4],
    ["Sp", "S<sub>p</sub> (blank = from μ)", "none", null, { opt: true }],
    ["R", "Return period factor R", "none", 1],
    ["N", "Near-fault factor N(T,D)", "none", 1],
    ["structure", "T<sub>1</sub> = 1.25·k<sub>t</sub>·h<sup>0.75</sup>", "none", "concrete_mrf", { sel: STRUCT_SEL }],
    ["T", "Period T<sub>1</sub> (override)", "period", null, { opt: true }],
  ],
  as1170_4: [
    ["Z", "Hazard factor Z", "none", 0.08],
    ["kp", "Probability factor k<sub>p</sub>", "none", 1],
    ["site", "Site sub-soil class", "none", "Ce", { sel: ["Ae", "Be", "Ce", "De", "Ee"].map(s => [s, s]) }],
    ["mu", "Ductility μ", "none", 2],
    ["Sp", "S<sub>p</sub>", "none", 0.77],
    ["kpZ_min", "Minimum k<sub>p</sub>Z (Table 3.3)", "none", 0.08],
    ["structure", "T<sub>1</sub> = 1.25·k<sub>t</sub>·h<sub>n</sub><sup>0.75</sup>", "none", "concrete_mrf", { sel: STRUCT_SEL }],
    ["T", "Period T<sub>1</sub> (override)", "period", null, { opt: true }],
  ],
  gb50011: [
    ["T", "Period T<sub>1</sub>", "period", 1.0, { hint: "GB 50011 has no empirical period formula — enter the analysed T1" }],
    ["intensity", "Seismic intensity", "none", "8", { sel: [["6", "6 (0.05g)"], ["7", "7 (0.10g)"], ["7.5", "7 (0.15g)"], ["8", "8 (0.20g)"], ["8.5", "8 (0.30g)"], ["9", "9 (0.40g)"]] }],
    ["level", "Earthquake level", "none", "frequent", { sel: [["frequent", "Frequent (多遇)"], ["rare", "Rare (罕遇)"]] }],
    ["site", "Site class", "none", "II", { sel: ["I0", "I1", "II", "III", "IV"].map(s => [s, s]) }],
    ["group", "Design group", "none", 1, { int: true, sel: [[1, "1"], [2, "2"], [3, "3"]] }],
    ["alpha_max", "α<sub>max</sub> (override)", "none", null, { opt: true }],
    ["Tg", "T<sub>g</sub> (override)", "period", null, { opt: true }],
    ["zeta", "Damping ratio ζ", "none", 0.05],
  ],
  tsc2018: [
    ["SS", "S<sub>S</sub> (g)", "none", 1.0],
    ["S1", "S<sub>1</sub> (g)", "none", 0.3],
    ["site", "Local soil class", "none", "ZC", { sel: ["ZA", "ZB", "ZC", "ZD", "ZE"].map(s => [s, s]) }],
    ["R", "Response modification R", "none", 8],
    ["D", "Overstrength D", "none", 3],
    ["I", "Importance factor I", "none", 1],
    ["FS", "F<sub>S</sub> (override)", "none", null, { opt: true }],
    ["F1", "F<sub>1</sub> (override)", "none", null, { opt: true }],
    ["structure", "T<sub>pA</sub> = C<sub>t</sub>·H<sub>N</sub><sup>0.75</sup>", "none", "concrete_mrf", { sel: STRUCT_SEL }],
    ["T", "Period T<sub>p</sub> (≤ 1.4·T<sub>pA</sub>)", "period", null, { opt: true }],
  ],
  kds41: [
    ["S", "Effective PGA S (g)", "none", 0.22],
    ["Fa", "Site coefficient F<sub>a</sub>", "none", 1.0],
    ["Fv", "Site coefficient F<sub>v</sub>", "none", 1.0],
    ["SDS", "S<sub>DS</sub> (override)", "none", null, { opt: true }],
    ["SD1", "S<sub>D1</sub> (override)", "none", null, { opt: true }],
    ["R", "Response modification R", "none", 5],
    ["IE", "Importance factor I<sub>E</sub>", "none", 1],
    ["structure", "T<sub>a</sub> = C<sub>t</sub>·h<sub>n</sub><sup>x</sup>", "none", "concrete_mrf", { sel: STRUCT_SEL }],
    ["T", "Period T (≤ C<sub>u</sub>·T<sub>a</sub>)", "period", null, { opt: true }],
  ],
  nscp2015: [
    ["Z", "Seismic zone Z", "none", 0.4, { sel: [[0.2, "Zone 2 — 0.2"], [0.4, "Zone 4 — 0.4"]] }],
    ["soil", "Soil profile", "none", "SD", { sel: ["SA", "SB", "SC", "SD", "SE"].map(s => [s, s]) }],
    ["R", "Response modification R", "none", 8.5],
    ["I", "Importance factor I", "none", 1],
    ["Na", "Near-source N<sub>a</sub>", "none", 1],
    ["Nv", "Near-source N<sub>v</sub>", "none", 1],
    ["Ca", "C<sub>a</sub> (override)", "none", null, { opt: true }],
    ["Cv", "C<sub>v</sub> (override)", "none", null, { opt: true }],
    ["structure", "T = C<sub>t</sub>·h<sub>n</sub><sup>0.75</sup>", "none", "concrete_mrf", { sel: STRUCT_SEL }],
    ["T", "Period T (≤ 1.3/1.4·T<sub>A</sub>)", "period", null, { opt: true }],
  ],
  ntc_cdmx: [
    ["T", "Period T", "period", 1.0],
    ["a0", "a<sub>0</sub> (g)", "none", 0.2, { hint: "Site spectrum parameters from SASID" }],
    ["c", "c (g)", "none", 0.6],
    ["Ta", "T<sub>a</sub>", "period", 0.4],
    ["Tb", "T<sub>b</sub>", "period", 1.0],
    ["k", "k", "none", 1.0],
    ["beta", "Damping factor β", "none", 1.0],
    ["Q", "Seismic behaviour factor Q", "none", 2],
    ["R0", "Overstrength R<sub>0</sub>", "none", 2],
    ["k1", "Redundancy k<sub>1</sub>", "none", 1],
  ],
  asce7_16_wind: [
    ["V", "Basic wind speed V", "velocity", 50],
    ["exposure", "Exposure", "none", "C", { sel: [["B", "B"], ["C", "C"], ["D", "D"]] }],
    ["Kzt", "K<sub>zt</sub> (no topography)", "none", 1],
    ["Kd", "K<sub>d</sub>", "none", 0.85],
    ["ze", "Ground elevation z<sub>e</sub> (K<sub>e</sub>)", "length", 0],
    ["G", "Gust factor G (blank = 0.85)", "none", null, { opt: true }],
    ["cp_windward", "C<sub>p</sub> windward (blank = 0.8)", "none", null, { opt: true }],
    ["cp_leeward", "C<sub>p</sub> leeward (blank = from L/B)", "none", null, { opt: true }],
    ...ASCE_CASE_SPEC,
  ],
  en1991_wind: [
    ["vb0", "Fundamental v<sub>b,0</sub>", "velocity", 27],
    ["terrain", "Terrain category", "none", "II", { sel: [["0", "0"], ["I", "I"], ["II", "II"], ["III", "III"], ["IV", "IV"]] }],
    ["cdir", "c<sub>dir</sub>", "none", 1],
    ["cseason", "c<sub>season</sub>", "none", 1],
    ["co", "Orography c<sub>o</sub>", "none", 1],
    ["kI", "Turbulence factor k<sub>I</sub>", "none", 1],
    ["rho", "Air density ρ (kg/m³)", "none", 1.25],
    ["cpe_D", "c<sub>pe</sub> zone D (blank = Table 7.1)", "none", null, { opt: true }],
    ["cpe_E", "c<sub>pe</sub> zone E (blank = Table 7.1)", "none", null, { opt: true }],
    ["cscd", "c<sub>s</sub>c<sub>d</sub> (blank = Annex B / 1.0)", "none", null, { opt: true }],
    ["n1", "n<sub>1</sub> (Hz) for Annex B", "none", null, { opt: true }],
    ["delta", "Log decrement δ", "none", 0.1],
    ["ze_rule", "Reference height z<sub>e</sub>", "none", "fig7_4", { sel: [["fig7_4", "Fig. 7.4 (b / h strips)"], ["story", "Story height"]] }],
  ],
  is875_wind: [
    ["Vb", "Basic wind speed V<sub>b</sub>", "velocity", 44],
    ["terrain", "Terrain category", "none", 2, { int: true, sel: [[1, "1"], [2, "2"], [3, "3"], [4, "4"]] }],
    ["k1", "Risk coefficient k<sub>1</sub>", "none", 1],
    ["k3", "Topography k<sub>3</sub>", "none", 1],
    ["k4", "Importance (cyclonic) k<sub>4</sub>", "none", 1],
    ["Kd", "Directionality K<sub>d</sub>", "none", 0.9],
    ["Ka", "Area averaging K<sub>a</sub>", "none", 1],
    ["Kc", "Combination K<sub>c</sub>", "none", 0.9],
    ["cp_windward", "C<sub>p</sub> windward", "none", 0.8],
    ["cp_leeward", "C<sub>p</sub> leeward", "none", -0.5],
  ],
  as1170_2_wind: [
    ["VR", "Regional speed V<sub>R</sub>", "velocity", 45],
    ["terrain", "Terrain category", "none", "2", { sel: [["1", "TC1"], ["2", "TC2"], ["2.5", "TC2.5"], ["3", "TC3"], ["4", "TC4"]] }],
    ["Md", "Direction M<sub>d</sub>", "none", 1],
    ["Ms", "Shielding M<sub>s</sub>", "none", 1],
    ["Mt", "Topographic M<sub>t</sub>", "none", 1],
    ["cpe_windward", "C<sub>p,e</sub> windward", "none", 0.7],
    ["cpe_leeward", "C<sub>p,e</sub> leeward (blank = from d/b)", "none", null, { opt: true }],
    ["Ka", "K<sub>a</sub>", "none", 1],
    ["Kc", "K<sub>c</sub>", "none", 1],
    ["Cdyn", "C<sub>dyn</sub>", "none", 1],
    ["vdes_min", "Minimum V<sub>des</sub> (ULS 30 m/s)", "velocity", 30],
  ],
  gb50009_wind: [
    ["w0", "Basic pressure w<sub>0</sub>", "pressure", 0.55],
    ["terrain", "Terrain roughness", "none", "B", { sel: [["A", "A"], ["B", "B"], ["C", "C"], ["D", "D"]] }],
    ["mu_s", "Shape coefficient μ<sub>s</sub>", "none", 1.3],
    ["T1", "Period T<sub>1</sub> (β<sub>z</sub> §8.4.3)", "period", null, { opt: true }],
    ["zeta1", "Damping ratio ζ<sub>1</sub>", "none", 0.05],
    ["beta_z", "β<sub>z</sub> (override)", "none", null, { opt: true }],
  ],
  ...wbxSpec,                                       // Wave B hook: TS 498 / KDS / CFE wind
};
ALX_SPEC.ntc_cdmx.push(...wbxNtcSpec);              // Wave B hook: NTC T > Tb distribution
/** Extra keys appended to the v1.16 ASCE 7-22 wind spec. */
export const ALX_ASCE22_EXTRA = ASCE_CASE_SPEC;

export const ALX_COEF_ROWS = {
  nzs1170_5: [["Ch", "C<sub>h</sub>(T)", "none"], ["C", "C(T)", "none"], ["Sp", "S<sub>p</sub>", "none"], ["kmu", "k<sub>μ</sub>", "none"], ["Cd", "C<sub>d</sub>(T<sub>1</sub>)", "none"], ["Ft", "F<sub>t</sub> (top)", "force"]],
  as1170_4: [["Ch", "C<sub>h</sub>(T<sub>1</sub>)", "none"], ["kpZ", "k<sub>p</sub>Z", "none"], ["k", "k", "none"], ["Cs", "V / W", "none"]],
  gb50011: [["alpha1", "α<sub>1</sub>", "none"], ["alpha_max", "α<sub>max</sub>", "none"], ["Tg", "T<sub>g</sub>", "period"], ["Geq", "G<sub>eq</sub>", "force"], ["delta_n", "δ<sub>n</sub>", "none"]],
  tsc2018: [["TpA", "T<sub>pA</sub>", "period"], ["SDS", "S<sub>DS</sub>", "none"], ["SD1", "S<sub>D1</sub>", "none"], ["Sae", "S<sub>ae</sub>(T)", "none"], ["Ra", "R<sub>a</sub>(T)", "none"], ["dFN", "ΔF<sub>N</sub>", "force"]],
  kds41: [["Ta", "T<sub>a</sub>", "period"], ["Cu", "C<sub>u</sub>", "none"], ["SDS", "S<sub>DS</sub>", "none"], ["SD1", "S<sub>D1</sub>", "none"], ["k", "k", "none"], ["Cs", "C<sub>s</sub>", "none"]],
  nscp2015: [["TA", "T<sub>A</sub>", "period"], ["Ca", "C<sub>a</sub>", "none"], ["Cv", "C<sub>v</sub>", "none"], ["Ft", "F<sub>t</sub>", "force"], ["Cs", "V / W", "none"]],
  ntc_cdmx: [["a", "a(T) (g)", "none"], ["Qp", "Q′", "none"], ["R", "R", "none"], ["Cs", "a / (Q′R)", "none"]],
  asce7_16_wind: [["G", "G", "none"], ["qh", "q<sub>h</sub>", "pressure"], ["cp_leeward", "C<sub>p</sub> leeward", "none"], ["Kzt_h", "K<sub>zt</sub>(h)", "none"], ["case_ecc", "Case e / B", "none"], ["width", "Facade width", "length"]],
  en1991_wind: [["vb", "v<sub>b</sub>", "velocity"], ["qh", "q<sub>p</sub>(h)", "pressure"], ["cpe_D", "c<sub>pe</sub> D", "none"], ["cpe_E", "c<sub>pe</sub> E", "none"], ["f_corr", "Correlation", "none"], ["cscd", "c<sub>s</sub>c<sub>d</sub>", "none"], ["width", "Facade width", "length"]],
  is875_wind: [["pd_h", "p<sub>d</sub>(h)", "pressure"], ["width", "Facade width", "length"]],
  as1170_2_wind: [["Vdes_h", "V<sub>des</sub>(h)", "velocity"], ["qh", "q<sub>h</sub>", "pressure"], ["cpe_leeward", "C<sub>p,e</sub> leeward", "none"], ["width", "Facade width", "length"]],
  gb50009_wind: [["mu_s", "μ<sub>s</sub>", "none"], ["width", "Facade width", "length"]],
  ...wbxCoefRows,                                   // Wave B hook
};
ALX_COEF_ROWS.ntc_cdmx.push(...wbxNtcCoef);         // Wave B hook: q, k1, k2 (T > Tb)
/** Extra summary rows shown for the extended ASCE 7-22 wind. */
export const ALX_ASCE22_COEF = [["G", "G", "none"], ["Kzt_h", "K<sub>zt</sub>(h)", "none"], ["case_ecc", "Case e / B", "none"]];

const NOTES = {
  nzs1170_5: "NZS 1170.5 §6.2: C<sub>d</sub> = C<sub>h</sub>(T<sub>1</sub>)·Z·R·N·S<sub>p</sub>/k<sub>μ</sub> ≥ max((Z/20+0.02)R, 0.03R); F<sub>t</sub> = 0.08V at the top, 0.92V by w·h.",
  as1170_4: "AS 1170.4 §6.2: V = k<sub>p</sub>Z·C<sub>h</sub>(T<sub>1</sub>)·S<sub>p</sub>/μ·W (≥ 0.01W); F ∝ w·h<sup>k</sup>.",
  gb50011: "GB 50011 §5.2.1: F<sub>Ek</sub> = α<sub>1</sub>·0.85·G<sub>E</sub>; δ<sub>n</sub>·F<sub>Ek</sub> added at the top (Table 5.2.1).",
  tsc2018: "TBDY 2018 §4.7: V = W·S<sub>ae</sub>/R<sub>a</sub> ≥ 0.04·W·I·S<sub>DS</sub>; ΔF<sub>N</sub> = 0.0075·N·V at the top.",
  kds41: "KDS 41 17 00 §7.2: C<sub>s</sub> = S<sub>D1</sub>/((R/I<sub>E</sub>)T) ≤ S<sub>DS</sub>/(R/I<sub>E</sub>), ≥ 0.01.",
  nscp2015: "NSCP 2015 §208.5: V = C<sub>v</sub>IW/(RT), ≤ 2.5C<sub>a</sub>IW/R, ≥ 0.11C<sub>a</sub>IW (zone 4 ≥ 0.8ZN<sub>v</sub>IW/R); F<sub>t</sub> = 0.07TV.",
  ntc_cdmx: "NTC-Sismo CDMX: V = a(T)/(Q′R)·W with the SASID site spectrum; F ∝ w·h (static method).",
  asce7_16_wind: "q<sub>z</sub> = 0.613·K<sub>z</sub>·K<sub>zt</sub>·K<sub>d</sub>·K<sub>e</sub>·V² (7-16 Table 26.10-1); p = G(q<sub>z</sub>C<sub>p,w</sub> + q<sub>h</sub>|C<sub>p,l</sub>|); story force = p × tributary facade.",
  en1991_wind: "q<sub>p</sub>(z) = (1 + 7I<sub>v</sub>)·½ρv<sub>m</sub>² (§4.5); F = c<sub>s</sub>c<sub>d</sub>·(c<sub>pe,D</sub>q<sub>p</sub>(z<sub>e</sub>) + |c<sub>pe,E</sub>|q<sub>p</sub>(h))·A, with the lack-of-correlation factor.",
  is875_wind: "V<sub>z</sub> = V<sub>b</sub>k<sub>1</sub>k<sub>2</sub>k<sub>3</sub>k<sub>4</sub>; p<sub>d</sub> = K<sub>d</sub>K<sub>a</sub>K<sub>c</sub>·0.6V<sub>z</sub>² ≥ 0.7p<sub>z</sub>.",
  as1170_2_wind: "V<sub>des</sub> = V<sub>R</sub>M<sub>d</sub>M<sub>z,cat</sub>M<sub>s</sub>M<sub>t</sub>; p = 0.5ρV<sub>des</sub>²C<sub>fig</sub>C<sub>dyn</sub>.",
  gb50009_wind: "w<sub>k</sub> = β<sub>z</sub>μ<sub>s</sub>μ<sub>z</sub>w<sub>0</sub> (Eq. 8.1.1-1); β<sub>z</sub> from T<sub>1</sub> per §8.4.3.",
  ...wbxNotes,                                      // Wave B hook
};
NOTES.ntc_cdmx += wbxNtcNote;                       // Wave B hook
const CASE_NOTE = "Cases 2 and 4 set the pattern's accidental torsion to ± e/B automatically (leave Eccentricity at None). " +
  "Case 4 with a negative component also sets a separate F<sub>y</sub> eccentricity so the two torques add.";

/* ASCE Fig. 27.3-8 pattern set: [suffix, direction, case, ecc sign, transverse sign] */
export const ALX_CASE_SET = [
  ["1X", "X", 1, 1, 1], ["1Y", "Y", 1, 1, 1],
  ["2X+e", "X", 2, 1, 1], ["2X-e", "X", 2, -1, 1], ["2Y+e", "Y", 2, 1, 1], ["2Y-e", "Y", 2, -1, 1],
  ["3X+Y", "X", 3, 1, 1], ["3X-Y", "X", 3, 1, -1],
  ["4X+Y+e", "X", 4, 1, 1], ["4X+Y-e", "X", 4, -1, 1], ["4X-Y+e", "X", 4, 1, -1], ["4X-Y-e", "X", 4, -1, -1],
];

/** Notes + extras for a code (loads116 buildExtras hook). `api`:
    {el, btn, readBody, generate(body, ecc), name(), done(names), error(msg)}.
    Returns true when the code is handled here. */
export function alxBuildExtras(code, extras, api) {
  const { el } = api;
  if (NOTES[code]) extras.appendChild(el("p", { class: "muted asn-note", html: NOTES[code] }));
  if (code === "asce7_16_wind" || code === "asce7_22_wind") {
    extras.appendChild(el("p", { class: "muted asn-note", html: CASE_NOTE }));
    const b = api.btn("Create all 12 cases…", "btn-small", () => alxCreateAllCases(code, api, b));
    b.id = "alxAllCases";
    b.title = "Write <name>-1X … <name>-4X-Y-e (Fig. 27.3-8 cases 1–4, both directions and signs)";
    extras.appendChild(b);
  }
  return !!NOTES[code];
}

async function alxCreateAllCases(code, api, b) {
  const r = api.readBody();
  if (r.error) { api.error(r.error); return; }
  const base = api.name();
  if (!base || base.length > 52) { api.error("Enter a base pattern name (max 52 characters)."); return; }
  b.disabled = true;
  const names = [];
  try {
    for (const [suf, dir, lc, es, ts] of ALX_CASE_SET) {
      const name = `${base}-${suf}`;
      const body = { ...r.body, name, direction: dir, load_case: lc, ecc_sign: es, transverse_sign: ts };
      await api.generate(body, 0);
      names.push(name);
    }
    api.done(names);
  } catch (e) {
    api.error("Create all cases failed: " + e.message);
  } finally { b.disabled = false; }
}
