/* SkyFrame — Wave C additions to Define › Auto Lateral Loads (CONTRACT
   "Wave C: regional seismic / wind generators, code spectra, spectrum file
   import, USGS lookup and regional material / steel libraries").

   Data only (no imports, no DOM): js/autolat_ext.js spreads these into its
   code list / parameter specs / summary rows / notes with a few commented
   hook lines.  Spec format = the loads116 SPEC format
   [key, label, units kind, default SI, {opt, sel, int, hint, omit}]. */

const STRUCT = [["steel_mrf", "Steel MRF"], ["concrete_mrf", "Concrete MRF"], ["ebf", "Steel EBF"], ["other", "Other"]];
const STRUCT3 = [["steel_mrf", "Steel MRF"], ["concrete_mrf", "Concrete MRF"], ["other", "Other"]];
const EC8_GROUNDS = ["A", "B", "C", "D", "E", "A-R", "B-R", "C-R", "B-T", "C-T", "C-S", "TC=0.7", "TC=1.0", "TC=1.6"];
const IBC_SPEC = [
  ["Ss", "S<sub>s</sub> (g)", "none", 1.5],
  ["S1", "S<sub>1</sub> (g)", "none", 0.6],
  ["site", "Site class", "none", "D", { sel: ["A", "B", "C", "D", "E"].map(s => [s, s]) }],
  ["R", "Response modification R", "none", 8],
  ["Ie", "Importance factor I<sub>e</sub>", "none", 1],
  ["TL", "Long-period T<sub>L</sub>", "period", 8],
  ["Fa", "F<sub>a</sub> (override)", "none", null, { opt: true }],
  ["Fv", "F<sub>v</sub> (override)", "none", null, { opt: true }],
  ["structure", "T<sub>a</sub> = C<sub>t</sub>·h<sub>n</sub><sup>x</sup>", "none", "concrete_mrf", { sel: STRUCT }],
  ["T", "Period T (≤ C<sub>u</sub>·T<sub>a</sub>)", "period", null, { opt: true }],
];

export const WVC_SEISMIC = [
  ["ec8_na", "EN 1998-1 + National Annex"],
  ["nbcc2020", "NBCC 2020 (full Mv / J)"],
  ["ibc2012", "IBC 2012 (ASCE 7-10)"],
  ["ibc2009", "IBC 2009 (ASCE 7-05 + S2)"],
  ["ibc2006", "IBC 2006 (ASCE 7-05)"],
  ["ubc97", "UBC 1997"],
  ["ntc2018", "Italian NTC 2018"],
];
export const WVC_WIND = [
  ["bs6399_wind", "BS 6399-2 Wind"],
  ["api4f_wind", "API 4F Wind"],
  ["ntc2018_wind", "Italian NTC 2018 Wind"],
];
export const WVC_DEFAULT_NAME = { ec8_na: "EC8NA", nbcc2020: "NBCC20", ibc2006: "IBC06", ibc2009: "IBC09", ibc2012: "IBC12",
  ubc97: "UBC97", ntc2018: "NTC18", bs6399_wind: "WINDBS", api4f_wind: "WINDAPI", ntc2018_wind: "WINDNTC" };

export const WVC_SPEC = {
  ec8_na: [
    ["annex", "National annex", "none", "UK", { sel: [["UK", "United Kingdom"], ["IT", "Italy"], ["DE", "Germany"], ["FR", "France"], ["ES", "Spain"], ["PT", "Portugal"], ["GR", "Greece"], ["RO", "Romania"]],
      hint: "NA parameter sets entered from memory — check before use (docs/REVIEW_FLAGS.md)" }],
    ["ground_type", "Ground type", "none", "B", { sel: EC8_GROUNDS.map(g => [g, g]), hint: "DE uses A-R … C-S, RO the P100 TC zones" }],
    ["spectrum_type", "Spectrum type", "none", "", { omit: "", sel: [["", "NA default"], [1, "Type 1"], [2, "Type 2"]] }],
    ["ag", "Reference PGA a<sub>gR</sub> (g)", "none", 0.1],
    ["gamma_I", "Importance factor γ<sub>I</sub>", "none", 1],
    ["q", "Behaviour factor q", "none", 3.9],
    ["structure", "T<sub>1</sub> = C<sub>t</sub>·H<sup>3/4</sup>", "none", "other", { sel: STRUCT3 }],
    ["T1", "Period T<sub>1</sub> (override)", "period", null, { opt: true }],
    ["beta", "Lower bound β (blank = NA)", "none", null, { opt: true }],
  ],
  nbcc2020: [
    ["Sa02", "S<sub>a</sub>(0.2) (g)", "none", 0.848], ["Sa05", "S<sub>a</sub>(0.5) (g)", "none", 0.751],
    ["Sa10", "S<sub>a</sub>(1.0) (g)", "none", 0.425], ["Sa20", "S<sub>a</sub>(2.0) (g)", "none", 0.257],
    ["Sa50", "S<sub>a</sub>(5.0) (g)", "none", 0.080], ["Sa100", "S<sub>a</sub>(10) (g)", "none", 0.029],
    ["RdRo", "R<sub>d</sub>·R<sub>o</sub>", "none", 6.8],
    ["Rd", "R<sub>d</sub> (cap test, blank = apply cap)", "none", null, { opt: true }],
    ["IE", "Importance I<sub>E</sub>", "none", 1],
    ["system", "SFRS / period rule (4.1.8.11.(3))", "none", "concrete_mf", { sel: [["steel_mf", "Steel moment frame"], ["concrete_mf", "Concrete moment frame"], ["other_mf", "Other moment frame (0.1N)"], ["braced", "Braced frame"], ["walls", "Shear walls"], ["other", "Other"]] }],
    ["table_system", "Table 4.1.8.11 row", "none", "", { omit: "", sel: [["", "From SFRS"], ["mrf", "Moment-resisting frames"], ["coupled_walls", "Coupled walls"], ["braced", "Braced frames"], ["walls", "Walls, wall-frame"], ["other", "Other systems"]] }],
    ["interp", "S(T) interpolation", "none", "linear", { sel: [["linear", "Linear"], ["loglog", "Log–log"]] }],
    ["T", "Period T<sub>a</sub> (override, capped)", "period", null, { opt: true }],
  ],
  ibc2012: IBC_SPEC, ibc2009: IBC_SPEC, ibc2006: IBC_SPEC,
  ubc97: [
    ["Z", "Seismic zone factor Z", "none", 0.4, { sel: [[0.075, "1 — 0.075"], [0.15, "2A — 0.15"], [0.2, "2B — 0.2"], [0.3, "3 — 0.3"], [0.4, "4 — 0.4"]] }],
    ["soil", "Soil profile", "none", "SD", { sel: ["SA", "SB", "SC", "SD", "SE"].map(s => [s, s]) }],
    ["R", "Response modification R", "none", 8.5],
    ["I", "Importance factor I", "none", 1],
    ["Na", "Near-source N<sub>a</sub>", "none", 1], ["Nv", "Near-source N<sub>v</sub>", "none", 1],
    ["Ca", "C<sub>a</sub> (override)", "none", null, { opt: true }], ["Cv", "C<sub>v</sub> (override)", "none", null, { opt: true }],
    ["structure", "T = C<sub>t</sub>·h<sub>n</sub><sup>3/4</sup>", "none", "concrete_mrf", { sel: STRUCT }],
    ["T", "Period T (≤ 1.3/1.4·T<sub>A</sub>)", "period", null, { opt: true }],
  ],
  ntc2018: [
    ["ag", "a<sub>g</sub> (g)", "none", 0.25], ["F0", "F<sub>0</sub>", "none", 2.4], ["Tc_star", "T<sub>C</sub>*", "period", 0.3],
    ["soil", "Soil category", "none", "B", { sel: ["A", "B", "C", "D", "E"].map(s => [s, s]) }],
    ["topography", "Topography", "none", "T1", { sel: ["T1", "T2", "T3", "T4"].map(s => [s, s]) }],
    ["q", "Behaviour factor q", "none", 3.9],
    ["structure", "T<sub>1</sub> = C<sub>1</sub>·H<sup>3/4</sup>", "none", "other", { sel: STRUCT3 }],
    ["T1", "Period T<sub>1</sub> (override)", "period", null, { opt: true }],
  ],
  bs6399_wind: [
    ["Vb", "Basic wind speed V<sub>b</sub>", "velocity", 21],
    ["Sb", "Terrain & building factor S<sub>b</sub>", "none", 1.8, { hint: "BS 6399-2 Table 4 at the effective height (one value; a He table via the API)" }],
    ["Sa", "Altitude S<sub>a</sub>", "none", 1], ["Sd", "Direction S<sub>d</sub>", "none", 1],
    ["Ss", "Seasonal S<sub>s</sub>", "none", 1], ["Sp", "Probability S<sub>p</sub>", "none", 1],
    ["Ca", "Size effect C<sub>a</sub>", "none", 1], ["Cr", "Dynamic augmentation C<sub>r</sub>", "none", 0],
    ["cpe_front", "C<sub>pe</sub> front (blank = Table 5)", "none", null, { opt: true }],
    ["cpe_rear", "C<sub>pe</sub> rear (blank = Table 5)", "none", null, { opt: true }],
    ["divergence", "Front/rear divergence (blank = 0.85…1)", "none", null, { opt: true }],
  ],
  api4f_wind: [
    ["Vref", "Reference wind speed V<sub>ref</sub>", "velocity", 30],
    ["Cs", "Shape coefficient C<sub>s</sub>", "none", 1.5],
    ["Ki", "Inclination / shielding K<sub>i</sub>", "none", 1],
    ["alpha", "Elevation exponent (β = (z/10)<sup>α</sup>)", "none", 0.125],
  ],
  ntc2018_wind: [
    ["zone", "Wind zone (Table 3.3.I)", "none", 3, { int: true, sel: [1, 2, 3, 4, 5, 6, 7, 8, 9].map(z => [z, String(z)]) }],
    ["a_s", "Site altitude a<sub>s</sub>", "length", 0],
    ["category", "Exposure category", "none", "III", { sel: ["I", "II", "III", "IV", "V"].map(s => [s, s]) }],
    ["TR", "Return period T<sub>R</sub> (years)", "none", 50],
    ["vb", "v<sub>b</sub> (override)", "velocity", null, { opt: true }],
    ["cd", "Dynamic coefficient c<sub>d</sub>", "none", 1],
    ["cp_windward", "c<sub>p</sub> windward", "none", 0.8], ["cp_leeward", "c<sub>p</sub> leeward", "none", -0.4],
  ],
};

export const WVC_COEF_ROWS = {
  ec8_na: [["Sd", "S<sub>d</sub>(T<sub>1</sub>) (g)", "none"], ["S", "S", "none"], ["TC", "T<sub>C</sub>", "period"], ["lambda_", "λ", "none"]],
  nbcc2020: [["S_Ta", "S(T<sub>a</sub>)", "none"], ["ratio", "S(0.2)/S(5.0)", "none"], ["Mv", "M<sub>v</sub>", "none"], ["J", "J", "none"],
    ["V_min", "V floor", "force"], ["V_max", "V cap", "force"], ["Ft", "F<sub>t</sub>", "force"]],
  ibc2012: [["Ta", "T<sub>a</sub>", "period"], ["SDS", "S<sub>DS</sub>", "none"], ["SD1", "S<sub>D1</sub>", "none"], ["Cs", "C<sub>s</sub>", "none"], ["k", "k", "none"]],
  ubc97: [["TA", "T<sub>A</sub>", "period"], ["Ca", "C<sub>a</sub>", "none"], ["Cv", "C<sub>v</sub>", "none"], ["Ft", "F<sub>t</sub>", "force"], ["Cs", "V / W", "none"]],
  ntc2018: [["Sd", "S<sub>d</sub>(T<sub>1</sub>) (g)", "none"], ["S", "S", "none"], ["TC", "T<sub>C</sub>", "period"], ["lambda_", "λ", "none"]],
  bs6399_wind: [["Vs", "V<sub>s</sub>", "velocity"], ["cpe_front", "C<sub>pe</sub> front", "none"], ["cpe_rear", "C<sub>pe</sub> rear", "none"], ["width", "Facade width", "length"]],
  api4f_wind: [["Vh", "V<sub>z</sub>(h)", "velocity"], ["width", "Facade width", "length"]],
  ntc2018_wind: [["qr", "q<sub>r</sub>", "pressure"], ["ce_h", "c<sub>e</sub>(h)", "none"], ["width", "Facade width", "length"]],
};
WVC_COEF_ROWS.ibc2009 = WVC_COEF_ROWS.ibc2012;
WVC_COEF_ROWS.ibc2006 = WVC_COEF_ROWS.ibc2012;

export const WVC_NOTES = {
  ec8_na: "EN 1998-1 §4.3.3.2 with the national-annex S / T<sub>B</sub> / T<sub>C</sub> / T<sub>D</sub> (presets from memory — flagged for review).",
  nbcc2020: "NBCC 2020 4.1.8.11: V = S(T<sub>a</sub>)M<sub>v</sub>I<sub>E</sub>W/(R<sub>d</sub>R<sub>o</sub>) with Table 4.1.8.11 M<sub>v</sub> / J; floors S(2.0) or S(4.0), cap max(⅔S(0.2), S(0.5)).",
  ibc2012: "ASCE 7-10 §12.8: C<sub>s</sub> = S<sub>DS</sub>/(R/I<sub>e</sub>) ≤ S<sub>D1</sub>/(T R/I<sub>e</sub>), ≥ 0.044 S<sub>DS</sub>I<sub>e</sub>.",
  ibc2009: "ASCE 7-05 + Supplement 2 §12.8 (same as 7-10): C<sub>s</sub> ≥ 0.044 S<sub>DS</sub>I<sub>e</sub>.",
  ibc2006: "ASCE 7-05 §12.8 as first published: C<sub>s</sub> ≥ 0.01.",
  ubc97: "UBC 1997 §1630.2: V = C<sub>v</sub>IW/(RT) ≤ 2.5C<sub>a</sub>IW/R, ≥ 0.11C<sub>a</sub>IW; F<sub>t</sub> = 0.07TV.",
  ntc2018: "NTC 2018 §7.3.3.2: F<sub>h</sub> = S<sub>d</sub>(T<sub>1</sub>)Wλ; spectrum §3.2.3 from a<sub>g</sub>, F<sub>0</sub>, T<sub>C</sub>*.",
  bs6399_wind: "BS 6399-2: V<sub>e</sub> = V<sub>b</sub>S<sub>a</sub>S<sub>d</sub>S<sub>s</sub>S<sub>p</sub>S<sub>b</sub>, q<sub>s</sub> = 0.613V<sub>e</sub>²; p = q<sub>s</sub>(C<sub>pe,f</sub> − C<sub>pe,r</sub>)C<sub>a</sub>(1 + C<sub>r</sub>).",
  api4f_wind: "API 4F: F = 0.00338·K<sub>i</sub>V<sub>z</sub>²C<sub>s</sub>A (lbf, knots, ft²), V<sub>z</sub> = V<sub>ref</sub>(z/10)<sup>α</sup>.",
  ntc2018_wind: "NTC 2018 §3.3: p = q<sub>r</sub>c<sub>e</sub>c<sub>p</sub>c<sub>d</sub>, c<sub>e</sub> = k<sub>r</sub>²c<sub>t</sub>ln(z/z<sub>0</sub>)(7 + c<sub>t</sub>ln(z/z<sub>0</sub>)).",
};
