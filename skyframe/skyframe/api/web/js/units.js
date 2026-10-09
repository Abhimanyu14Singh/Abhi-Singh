/* SkyFrame — display-unit conversion (v1.13 contract §5).

   The model store and every backend payload stay SI ALWAYS:
     force kN · length m · stress kPa · mass tonne (= kN·s²/m) · temperature °C · time s.
   Conversion happens ONLY at display / input boundaries through this module:

     toDisplay(kind, si)        SI value → number in the current display set
     fromDisplay(kind, v)       display number → SI value
     label(kind)                unit label for the current set ("kip·in", "ksi", …)
     fmt(kind, si, baseDec)     formatted display string; baseDec = the decimals the
                                default kN-m set would use (adapted per set by the
                                order of magnitude of the conversion factor)
     inputValue(kind, si)       string for an <input value> (exact SI string when the
                                factor is 1, else 6 significant digits — the SI value
                                stays authoritative, so switching units and back
                                never drifts)
     parse(kind, text)          <input> text → SI number (NaN when not numeric)

   Unit sets (model.display_units): "kN-m" (default), "kN-mm", "N-mm", "tonf-m",
   "kip-ft", "kip-in". Exact factors: 1 kip = 4.4482216152605 kN,
   1 tonf = 9.80665 kN, 1 ft = 0.3048 m, 1 in = 0.0254 m.
   SI sets use °C; US sets (kip-ft, kip-in) use °F — temperature CHANGES convert
   by ΔT_F = 1.8·ΔT_C and thermal coefficients by α/°F = (α/°C)/1.8.

   Each set has three length flavours (ETABS-like display conventions):
     L  — model length (grids, stories, coordinates, spans)
     Ld — section dimensions (b, h, thickness; A = Ld², I = Ld⁴, S = Ld³)
     Ls — small lengths (displacements, deflections, layer t, cover)
   e.g. kN-m: L = m, Ld = m, Ls = mm · kip-ft: L = ft, Ld = in, Ls = in.

   Masses are CONSISTENT-unit masses (like ETABS): mass = force·s²/length.
     kN-m   → t (= kN·s²/m)            kN-mm → kN·s²/mm (= 1000 t)
     N-mm   → N·s²/mm (= 1 t)          tonf-m → tonf·s²/m (= 9.80665 t)
     kip-ft → kip·s²/ft (≈ 14.5939 t)  kip-in → kip·s²/in (≈ 175.127 t)
   Mass density = mass/L³ (kN-m: t/m³), mass moment of inertia = mass·L².

   Quantity kinds (QUANTITY_KINDS): length, dim, small, disp, area, inertia,
   secmod, force, moment, line_force, line_moment, pressure, stress, modulus,
   unit_weight, subgrade, line_spring, stiffness, rot_stiffness, damping_coeff,
   mass, mass_density, mass_moi, accel, velocity, temp, temp_delta,
   thermal_coeff, rotation, frequency, period, strain, none. Unitless kinds
   (rotation, frequency, period, strain, none) are never scaled. */

const KIP = 4.4482216152605;     // kN
const TONF = 9.80665;            // kN
const FT = 0.3048;               // m
const IN = 0.0254;               // m

/* F = kN per display force unit; L/Ld/Ls = m per display length unit. */
export const UNIT_SETS = {
  "kN-m":   { label: "kN · m · °C",     F: 1,     fu: "kN",   L: 1,     lu: "m",  Ld: 1,     du: "m",  Ls: 0.001, su: "mm", temp: "C",
              pressure: "kPa", mass: "t", massDen: "t/m³", massMoi: "t·m²" },
  "kN-mm":  { label: "kN · mm · °C",    F: 1,     fu: "kN",   L: 0.001, lu: "mm", Ld: 0.001, du: "mm", Ls: 0.001, su: "mm", temp: "C",
              pressure: "kN/mm²", mass: "kN·s²/mm", massDen: "kN·s²/mm⁴", massMoi: "kN·s²·mm" },
  "N-mm":   { label: "N · mm · °C",     F: 0.001, fu: "N",    L: 0.001, lu: "mm", Ld: 0.001, du: "mm", Ls: 0.001, su: "mm", temp: "C",
              pressure: "N/mm²", mass: "N·s²/mm", massDen: "N·s²/mm⁴", massMoi: "N·s²·mm" },
  "tonf-m": { label: "tonf · m · °C",   F: TONF,  fu: "tonf", L: 1,     lu: "m",  Ld: 1,     du: "m",  Ls: 0.001, su: "mm", temp: "C",
              pressure: "tonf/m²", mass: "tonf·s²/m", massDen: "tonf·s²/m⁴", massMoi: "tonf·s²·m" },
  "kip-ft": { label: "kip · ft · °F",   F: KIP,   fu: "kip",  L: FT,    lu: "ft", Ld: IN,    du: "in", Ls: IN,    su: "in", temp: "F",
              pressure: "ksf", mass: "kip·s²/ft", massDen: "kip·s²/ft⁴", massMoi: "kip·s²·ft" },
  "kip-in": { label: "kip · in · °F",   F: KIP,   fu: "kip",  L: IN,    lu: "in", Ld: IN,    du: "in", Ls: IN,    su: "in", temp: "F",
              pressure: "ksi", mass: "kip·s²/in", massDen: "kip·s²/in⁴", massMoi: "kip·s²·in" },
};
export const UNIT_SET_NAMES = Object.keys(UNIT_SETS);
export const DEFAULT_UNITS = "kN-m";

/* kind → (set) => [siPerDisplay, label]. siPerDisplay = how many SI units one
   display unit is worth (SI = display × siPerDisplay). */
const KINDS = {
  length:        s => [s.L, s.lu],
  dim:           s => [s.Ld, s.du],
  small:         s => [s.Ls, s.su],
  disp:          s => [s.Ls, s.su],
  area:          s => [s.Ld ** 2, `${s.du}²`],
  inertia:       s => [s.Ld ** 4, `${s.du}⁴`],
  secmod:        s => [s.Ld ** 3, `${s.du}³`],
  force:         s => [s.F, s.fu],
  moment:        s => [s.F * s.L, `${s.fu}·${s.lu}`],
  line_force:    s => [s.F / s.L, `${s.fu}/${s.lu}`],
  line_moment:   s => [s.F, `${s.fu}·${s.lu}/${s.lu}`],
  pressure:      s => [s.F / s.L ** 2, s.pressure],
  stress:        s => [s.F / s.L ** 2, s.pressure],
  modulus:       s => [s.F / s.L ** 2, s.pressure],
  unit_weight:   s => [s.F / s.L ** 3, `${s.fu}/${s.lu}³`],
  subgrade:      s => [s.F / s.L ** 3, `${s.fu}/${s.lu}³`],
  line_spring:   s => [s.F / s.L ** 2, `${s.fu}/${s.lu}/${s.lu}`],
  stiffness:     s => [s.F / s.L, `${s.fu}/${s.lu}`],
  rot_stiffness: s => [s.F * s.L, `${s.fu}·${s.lu}/rad`],
  damping_coeff: s => [s.F / s.L, `${s.fu}·s/${s.lu}`],
  mass:          s => [s.F / s.L, s.mass],
  mass_density:  s => [s.F / s.L ** 4, s.massDen],
  mass_moi:      s => [s.F * s.L, s.massMoi],
  accel:         s => [s.L, `${s.lu}/s²`],
  velocity:      s => [s.L, `${s.lu}/s`],
  temp:          s => [s.temp === "F" ? 1 / 1.8 : 1, s.temp === "F" ? "°F" : "°C"],
  temp_delta:    s => [s.temp === "F" ? 1 / 1.8 : 1, s.temp === "F" ? "°F" : "°C"],
  thermal_coeff: s => [s.temp === "F" ? 1.8 : 1, s.temp === "F" ? "1/°F" : "1/°C"],
  rotation:      () => [1, "rad"],
  frequency:     () => [1, "Hz"],
  period:        () => [1, "s"],
  strain:        () => [1, ""],
  none:          () => [1, ""],
};
export const QUANTITY_KINDS = Object.keys(KINDS);

let current = DEFAULT_UNITS;
const listeners = new Set();

/** Valid set name (anything else falls back to the kN-m default). */
export const normalizeUnits = name => UNIT_SETS[name] ? name : DEFAULT_UNITS;

export function getUnits() { return current; }
export function unitSet(name = current) { return UNIT_SETS[normalizeUnits(name)]; }

/** Switch the display set; listeners fire only on a real change. */
export function setUnits(name, { silent = false } = {}) {
  const nu = normalizeUnits(name);
  if (nu === current) return false;
  current = nu;
  if (!silent) for (const fn of [...listeners]) { try { fn(nu); } catch (e) { console.error(e); } }
  return true;
}
export function onUnitsChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

const spec = (kind, setName = current) => {
  const k = KINDS[kind] || KINDS.none;
  return k(unitSet(setName));
};

/** SI value → display multiplier (display = SI × factor), offsets excluded. */
export function factor(kind, setName = current) { return 1 / spec(kind, setName)[0]; }
export function label(kind, setName = current) { return spec(kind, setName)[1]; }
export const isIdentity = (kind, setName = current) =>
  spec(kind, setName)[0] === 1 && !(kind === "temp" && unitSet(setName).temp === "F");

export function toDisplay(kind, si, setName = current) {
  if (si == null || si === "" || !isFinite(si)) return si;
  const v = +si;
  if (kind === "temp" && unitSet(setName).temp === "F") return v * 1.8 + 32;
  const k = spec(kind, setName)[0];
  return k === 1 ? v : v / k;
}

export function fromDisplay(kind, v, setName = current) {
  if (v == null || v === "" || !isFinite(v)) return v;
  const x = +v;
  if (kind === "temp" && unitSet(setName).temp === "F") return (x - 32) / 1.8;
  const k = spec(kind, setName)[0];
  return k === 1 ? x : x * k;
}

/** Decimals for `kind` in the current set, given the decimals the default
    kN-m set uses: scaled by the order of magnitude of the relative factor
    (kN → N drops 3 decimals, kN → kip adds 1, mm → in adds 1 …). */
export function dec(kind, baseDec = 1, setName = current) {
  const rel = factor(kind, setName) / factor(kind, DEFAULT_UNITS);
  if (!isFinite(rel) || rel <= 0 || rel === 1) return baseDec;
  // floor (not round): never show fewer significant digits than the kN-m view
  const d = baseDec - Math.floor(Math.log10(rel) + 1e-9);
  return Math.max(0, Math.min(8, d));
}

const num = (v, d) => (v == null || !isFinite(v)) ? "—"
  : (+v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

/** Formatted display string (no unit suffix). */
export function fmt(kind, si, baseDec = 1) {
  if (si == null || !isFinite(si)) return "—";
  return num(toDisplay(kind, si), dec(kind, baseDec));
}
/** Formatted display string with the unit label appended. */
export function fmtU(kind, si, baseDec = 1) {
  const l = label(kind);
  return fmt(kind, si, baseDec) + (l ? ` ${l}` : "");
}
/** Scientific display string (section properties). */
export function sci(kind, si, digits = 2) {
  if (si == null || !isFinite(si)) return "—";
  return Number(toDisplay(kind, si)).toExponential(digits);
}

/** <input value> string for an SI value (exact SI when the factor is 1). */
export function inputValue(kind, si) {
  if (si == null || si === "" || !isFinite(si)) return "";
  if (isIdentity(kind)) return String(si);
  const v = toDisplay(kind, si);
  return String(+(+v).toPrecision(6));
}
/** Parse an <input> text in display units → SI (NaN when not numeric). */
export function parse(kind, text) {
  const v = parseFloat(String(text).trim());
  return isFinite(v) ? fromDisplay(kind, v) : NaN;
}
/** Display step for a number input given its SI-set step. */
export function step(kind, siStep) {
  if (siStep === "any" || !isFinite(+siStep)) return "any";
  if (isIdentity(kind)) return String(siStep);
  const v = +siStep * factor(kind) / factor(kind, DEFAULT_UNITS);
  const p = Math.pow(10, Math.floor(Math.log10(Math.abs(v) || 1)));
  return String(+(Math.round(v / p) * p).toPrecision(3));
}

/** ASCII-ish CSV header tag for a unit label ("kN·m" → "kNm", "kN/m" → "kN_m"). */
export function csvTag(kind) {
  return label(kind).replace(/·/g, "").replace(/\//g, "_").replace(/²/g, "2")
    .replace(/³/g, "3").replace(/⁴/g, "4").replace(/°/g, "deg");
}

/** Full conversion table for a set (GET /api/units mirror, client side). */
export function unitsTable(setName = current) {
  const out = {};
  for (const k of QUANTITY_KINDS) out[k] = { label: label(k, setName), factor: factor(k, setName) };
  return { system: normalizeUnits(setName), label: unitSet(setName).label, quantities: out };
}
export function allUnitsTables() {
  return {
    default: DEFAULT_UNITS,
    si_base: { force: "kN", length: "m", stress: "kPa", mass: "t", temperature: "C", time: "s" },
    systems: Object.fromEntries(UNIT_SET_NAMES.map(n => [n, unitsTable(n)])),
  };
}

/* ---------------- static-markup bindings (index.html) ----------------
   <span data-ul="length">m</span> gets the current label;
   <input data-uq="length" value="3.2"> holds an SI default in its markup — the
   first apply stores it in data-si, every later apply re-displays from data-si,
   and a user edit refreshes data-si (so the SI value stays authoritative). */
export function applyStatic(root = document) {
  root.querySelectorAll("[data-ul]").forEach(el => { el.textContent = label(el.dataset.ul); });
  root.querySelectorAll("input[data-uq]").forEach(inp => {
    const kind = inp.dataset.uq;
    if (inp.dataset.si === undefined) {
      inp.dataset.si = inp.value;
      if (inp.step) inp.dataset.siStep = inp.step;
      inp.addEventListener("change", () => {
        const si = parse(kind, inp.value);
        if (isFinite(si)) inp.dataset.si = String(si);
      });
    }
    inp.value = inputValue(kind, parseFloat(inp.dataset.si));
    if (inp.dataset.siStep) inp.step = step(kind, inp.dataset.siStep);
  });
}
/** SI value of a static-bound input (falls back to parsing its text). */
export function readStatic(inp) {
  if (!inp) return NaN;
  const kind = inp.dataset.uq;
  if (!kind) return parseFloat(inp.value);
  const si = parse(kind, inp.value);
  return isFinite(si) ? si : parseFloat(inp.dataset.si);
}

/* Default export: a namespace object so callers can write U.fmt(...). */
const U = {
  UNIT_SETS, UNIT_SET_NAMES, DEFAULT_UNITS, QUANTITY_KINDS,
  normalizeUnits, getUnits, unitSet, setUnits, onUnitsChange, factor, label,
  isIdentity, toDisplay, fromDisplay, dec, fmt, fmtU, sci, inputValue, parse,
  step, csvTag, unitsTable, allUnitsTables, applyStatic, readStatic,
};
export default U;
