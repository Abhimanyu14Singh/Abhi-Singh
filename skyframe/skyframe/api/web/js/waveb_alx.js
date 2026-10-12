/* SkyFrame — Wave B additions to Define › Auto Lateral Loads (CONTRACT
   "Irregularity checks, grouped ASCE 7 combinations, … TS 498 / KDS / CFE
   wind, NTC long-period distribution").

   Data only (no imports, no DOM): js/autolat_ext.js spreads these into its
   code list / parameter specs / summary rows / notes with a few commented
   hook lines.  Parameter spec format = the loads116 SPEC format
   [key, label, units kind, default SI, {opt, sel, int, hint, omit}]. */

export const WBX_WIND = [
  ["ts498_wind", "TS 498 Wind (Turkey, TSC 2018)"],
  ["kds41_wind", "KDS 41 10 15 Wind (Korea)"],
  ["cfe_wind", "CFE / NTC-Viento Wind (Mexico)"],
];

export const WBX_DEFAULT_NAME = { ts498_wind: "WINDTS", kds41_wind: "WINDKDS", cfe_wind: "WINDCFE" };

export const WBX_SPEC = {
  ts498_wind: [
    ["cp_windward", "c<sub>p</sub> windward", "none", 0.8],
    ["cp_leeward", "c<sub>p</sub> leeward", "none", -0.4],
    ["q_factor", "q multiplier (site study)", "none", 1, { hint: "1 = TS 498 Table 4 q(z)" }],
  ],
  kds41_wind: [
    ["V0", "Basic wind speed V<sub>0</sub>", "velocity", 30],
    ["roughness", "Surface roughness", "none", "C", { sel: [["A", "A"], ["B", "B"], ["C", "C"], ["D", "D"]] }],
    ["KD", "Directionality K<sub>D</sub>", "none", 1],
    ["Kzt", "Topography K<sub>zt</sub>", "none", 1],
    ["Iw", "Importance I<sub>w</sub>", "none", 1],
    ["GD", "Gust factor G<sub>D</sub>", "none", 2],
    ["cpe_windward", "C<sub>pe1</sub> windward", "none", 0.8],
    ["cpe_leeward", "C<sub>pe2</sub> leeward", "none", -0.5],
  ],
  cfe_wind: [
    ["VR", "Regional speed V<sub>R</sub>", "velocity", 40],
    ["category", "Terrain category", "none", 2, { int: true, sel: [[1, "1"], [2, "2"], [3, "3"], [4, "4"]] }],
    ["form", "Formulation", "none", "cfe", { sel: [["cfe", "CFE MDOC-Viento (c from Table 4.2.3)"], ["ntc", "CDMX NTC-Viento (c = 1)"]] }],
    ["FT", "Topography F<sub>T</sub> / F<sub>TR</sub>", "none", 1],
    ["G", "Air-density factor G", "none", 1],
    ["cpe_windward", "C<sub>pe</sub> windward", "none", 0.8],
    ["cpe_leeward", "C<sub>pe</sub> leeward (blank = from d/b)", "none", null, { opt: true }],
  ],
};

/** Extra NTC-Sismo keys (long-period two-term distribution). */
export const WBX_NTC_SPEC = [
  ["long_period", "T > T<sub>b</sub> distribution", "none", "reduced", { sel: [["reduced", "Reduced two-term (k1·h + k2·h²)"], ["linear", "Linear w·h (previous)"]] }],
  ["r", "Exponent r (q = (T<sub>b</sub>/T)<sup>r</sup>)", "none", 2, { hint: "2 = 2017 spectrum; NTC-2004 zones 1/2, 2/3, 1" }],
];
export const WBX_NTC_COEF = [["q", "q = (T<sub>b</sub>/T)<sup>r</sup>", "none"], ["kh1", "k<sub>1</sub> (h term)", "none"], ["kh2", "k<sub>2</sub> (h² term)", "none"]];

export const WBX_COEF_ROWS = {
  ts498_wind: [["cp_total", "c<sub>p</sub> windward + |leeward|", "none"], ["width", "Facade width", "length"]],
  kds41_wind: [["qh", "q<sub>H</sub>", "pressure"], ["GD", "G<sub>D</sub>", "none"], ["width", "Facade width", "length"]],
  cfe_wind: [["qh", "q<sub>z</sub>(h)", "pressure"], ["cpe_leeward", "C<sub>pe</sub> leeward", "none"], ["width", "Facade width", "length"]],
};

export const WBX_NOTES = {
  ts498_wind: "TS 498 Table 4: q = 0.5 / 0.8 / 1.1 / 1.3 kN/m² for 0–8 / 9–20 / 21–100 / &gt; 100 m; w = c<sub>p</sub>q, closed building 0.8 + 0.4.",
  kds41_wind: "KDS 41 10 15: V<sub>z</sub> = V<sub>0</sub>K<sub>D</sub>K<sub>zr</sub>K<sub>zt</sub>I<sub>w</sub>, q = ½ρV<sub>z</sub>²; W<sub>D</sub> = G<sub>D</sub>(q<sub>z</sub>C<sub>pe1</sub> + q<sub>H</sub>|C<sub>pe2</sub>|)·A.",
  cfe_wind: "CFE MDOC-Viento: V<sub>D</sub> = F<sub>T</sub>F<sub>rz</sub>V<sub>R</sub>, q<sub>z</sub> = 0.613·G·V<sub>D</sub>² (Pa); leeward C<sub>pe</sub> from d/b.",
};

export const WBX_NTC_NOTE = " For T &gt; T<sub>b</sub> the reduced two-term distribution F<sub>i</sub> = W<sub>i</sub>·a/(Q′R)·(k<sub>1</sub>h<sub>i</sub> + k<sub>2</sub>h<sub>i</sub>²) applies.";
