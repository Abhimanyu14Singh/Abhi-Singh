/* ============================================================================
 * units.js — Unit systems & formatting
 *
 * The solver is unit-agnostic: it only needs *consistent* units in -> out. To
 * keep one source of truth, the model always stores SI base units:
 *     length  -> m
 *     force   -> kN
 *     E       -> kN/m^2
 *     A       -> m^2
 *     displacement results -> m
 *
 * This module converts user-facing input/display to/from that base, and lets
 * the user work natively in US customary units (kip, ft, in, ksi) — the world
 * AISC steel shapes live in.
 * ==========================================================================*/

(function (global) {
  'use strict';

  // Exact conversion constants.
  const FT_M = 0.3048; // m per ft
  const IN_M = 0.0254; // m per in
  const KIP_KN = 4.4482216153; // kN per kip
  const IN2_M2 = 0.00064516; // m^2 per in^2
  const KSI_KNM2 = 6894.757293; // kN/m^2 per ksi  (1 ksi = 6.894757 MPa)

  const SYSTEMS = {
    SI: {
      id: 'SI',
      label: 'Metric (kN, m)',
      // length: model metres <-> display metres
      lenUnit: 'm',
      lenToBase: 1, // multiply display -> base(m)
      grid: 1, // grid spacing in base units (1 m)
      forceUnit: 'kN',
      forceToBase: 1, // display kN -> base kN
      deflUnit: 'mm',
      deflFromBase: 1000, // base m -> display mm
      stressUnit: 'MPa',
      stressFromBase: 1 / 1000, // base kN/m^2 -> MPa  (1 MPa = 1000 kN/m^2)
      // sensible material/section defaults in BASE units
      defaultE: 2.0e8, // kN/m^2  (200 GPa)
      defaultA: 0.0020, // m^2 (20 cm^2)
      // area entry helper
      areaUnit: 'cm²',
      areaFromBase: 1e4, // base m^2 -> cm^2
      areaToBase: 1e-4, // cm^2 -> base m^2
      // modulus display
      eUnit: 'GPa',
      eFromBase: 1e-6, // base kN/m^2 -> GPa  (1 GPa = 1e6 kN/m^2)
      eToBase: 1e6,
    },
    US: {
      id: 'US',
      label: 'Imperial (kip, ft)',
      lenUnit: 'ft',
      lenToBase: FT_M, // display ft -> base m
      grid: FT_M, // 1 ft grid
      forceUnit: 'kip',
      forceToBase: KIP_KN, // display kip -> base kN
      deflUnit: 'in',
      deflFromBase: 1 / IN_M, // base m -> display in
      stressUnit: 'ksi',
      stressFromBase: 1 / KSI_KNM2, // base kN/m^2 -> ksi
      defaultE: 29000 * KSI_KNM2, // 29,000 ksi in kN/m^2  (~2.0e8)
      defaultA: 10 * IN2_M2, // ~10 in^2 in m^2
      areaUnit: 'in²',
      areaFromBase: 1 / IN2_M2, // base m^2 -> in^2
      areaToBase: IN2_M2, // in^2 -> base m^2
      eUnit: 'ksi',
      eFromBase: 1 / KSI_KNM2, // base kN/m^2 -> ksi
      eToBase: KSI_KNM2,
    },
  };

  function fmt(value, digits = 2) {
    if (value === null || value === undefined || Number.isNaN(value)) return '—';
    const abs = Math.abs(value);
    if (abs !== 0 && (abs < 1e-3 || abs >= 1e6)) return value.toExponential(2);
    return value.toFixed(digits);
  }

  class Units {
    constructor(systemId = 'SI') { this.set(systemId); }
    set(systemId) { this.sys = SYSTEMS[systemId] || SYSTEMS.SI; return this; }

    // --- conversions: display <-> base ---
    lenToBase(v) { return v * this.sys.lenToBase; }
    lenFromBase(v) { return v / this.sys.lenToBase; }
    forceToBase(v) { return v * this.sys.forceToBase; }
    forceFromBase(v) { return v / this.sys.forceToBase; }
    areaToBase(v) { return v * this.sys.areaToBase; }
    areaFromBase(v) { return v * this.sys.areaFromBase; }
    eToBase(v) { return v * this.sys.eToBase; }
    eFromBase(v) { return v * this.sys.eFromBase; }
    deflFromBase(v) { return v * this.sys.deflFromBase; }
    stressFromBase(v) { return v * this.sys.stressFromBase; }

    get gridBase() { return this.sys.grid; }
    get defaultE() { return this.sys.defaultE; }
    get defaultA() { return this.sys.defaultA; }

    // AISC areas are always in in^2 -> base m^2.
    aiscAreaToBase(in2) { return in2 * IN2_M2; }

    // --- formatted strings (value + unit) ---
    len(vBase, d = 2) { return `${fmt(this.lenFromBase(vBase), d)} ${this.sys.lenUnit}`; }
    force(vBase, d = 2) { return `${fmt(this.forceFromBase(vBase), d)} ${this.sys.forceUnit}`; }
    defl(vBase, d = 3) { return `${fmt(this.deflFromBase(vBase), d)} ${this.sys.deflUnit}`; }
    area(vBase, d = 2) { return `${fmt(this.areaFromBase(vBase), d)} ${this.sys.areaUnit}`; }
    stress(vBase, d = 1) { return `${fmt(this.stressFromBase(vBase), d)} ${this.sys.stressUnit}`; }

    get forceUnit() { return this.sys.forceUnit; }
    get lenUnit() { return this.sys.lenUnit; }
    get deflUnit() { return this.sys.deflUnit; }
    get areaUnit() { return this.sys.areaUnit; }
    get eUnit() { return this.sys.eUnit; }
    get id() { return this.sys.id; }
  }

  global.Units = Units;
  global.UNIT_SYSTEMS = SYSTEMS;
  global.fmtNum = fmt;
})(typeof window !== 'undefined' ? window : globalThis);
