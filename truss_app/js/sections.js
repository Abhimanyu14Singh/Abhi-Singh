/* ============================================================================
 * sections.js — AISC steel shape library (cross-sectional area)
 *
 * Areas are the published AISC Shapes Database (Manual Table 1-1) cross-
 * sectional areas, in square inches. The truss solver only needs A (and E),
 * so area is the property we carry; weight is shown to give learners a feel
 * for "how much steel" a choice costs (Baker efficiency story).
 *
 * Internally the model stores A in m^2; use Units.aiscAreaToBase(in2) (or the
 * areaToBase helper) to convert.  Steel E = 29,000 ksi.
 * ==========================================================================*/

(function (global) {
  'use strict';

  // [designation, area in^2, weight lb/ft]
  const W = [
    ['W8x10', 2.96, 10], ['W8x18', 5.26, 18], ['W8x31', 9.13, 31],
    ['W10x12', 3.54, 12], ['W10x22', 6.49, 22], ['W10x33', 9.71, 33],
    ['W12x14', 4.16, 14], ['W12x26', 7.65, 26], ['W12x40', 11.7, 40],
    ['W12x53', 15.6, 53], ['W14x22', 6.49, 22], ['W14x43', 12.6, 43],
    ['W14x90', 26.5, 90], ['W16x26', 7.68, 26], ['W16x40', 11.8, 40],
    ['W18x35', 10.3, 35], ['W18x50', 14.7, 50], ['W21x44', 13.0, 44],
    ['W24x55', 16.2, 55],
  ];
  const HSS_SQ = [
    ['HSS4x4x1/4', 3.37, 12.2], ['HSS4x4x3/8', 4.78, 17.3],
    ['HSS5x5x1/4', 4.30, 15.6], ['HSS6x6x1/4', 5.24, 19.0],
    ['HSS6x6x3/8', 7.58, 27.5], ['HSS8x8x1/2', 13.5, 48.9],
  ];
  const HSS_RND = [
    ['Pipe 4 Std', 2.96, 10.8], ['Pipe 5 Std', 4.01, 14.6],
    ['Pipe 6 Std', 5.20, 19.0], ['HSS5.000x0.250', 3.49, 12.7],
  ];
  const ANGLES = [
    ['L4x4x1/4', 1.93, 6.6], ['L4x4x3/8', 2.86, 9.8],
    ['L5x5x3/8', 3.61, 12.3], ['L6x6x1/2', 5.75, 19.6],
    ['2L4x4x3/8', 5.72, 19.4],
  ];

  function toEntries(arr, group) {
    return arr.map(([name, area, wt]) => ({ name, areaIn2: area, weight: wt, group }));
  }

  const SECTIONS = [
    { group: 'W-Shapes (Wide Flange)', items: toEntries(W, 'W') },
    { group: 'HSS — Square/Rect', items: toEntries(HSS_SQ, 'HSS') },
    { group: 'HSS Round / Pipe', items: toEntries(HSS_RND, 'PIPE') },
    { group: 'Angles', items: toEntries(ANGLES, 'L') },
  ];

  // Flat lookup by designation.
  const BY_NAME = {};
  SECTIONS.forEach((g) => g.items.forEach((it) => { BY_NAME[it.name] = it; }));

  global.AISC_SECTIONS = SECTIONS;
  global.AISC_BY_NAME = BY_NAME;
  global.AISC_E_KSI = 29000;
})(typeof window !== 'undefined' ? window : globalThis);
