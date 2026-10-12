/* SkyFrame — ?mock=1 twin of skyframe.core.spectra_codes_wavec (TSC 2018,
   KDS 41, NSCP 2015, NTC-Sismo CDMX, Italian NTC 2018, EC8 + national
   annex code spectra; spectrum file parsing) and an offline stand-in for the
   USGS design-maps lookup.  Builders return [fn, corners, summary] exactly
   like js/mock_loadcodes.js BUILD (registered there by one hook line). */

import { wvcInterp as interp, wvcEc8NaParams as naParams, wvcNtc18Params as ntcParams,
  wvcNtc18Se as ntcSe } from "./mock_wavec_alx.js";

const G0 = 9.80665;
const TSC_SS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5], TSC_S1 = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6];
const TSC_FS = { ZA: Array(6).fill(0.8), ZB: Array(6).fill(0.9), ZC: [1.3, 1.3, 1.2, 1.2, 1.2, 1.2], ZD: [1.6, 1.4, 1.2, 1.1, 1.0, 1.0], ZE: [2.4, 1.7, 1.3, 1.1, 0.9, 0.8] };
const TSC_F1 = { ZA: Array(6).fill(0.8), ZB: Array(6).fill(0.8), ZC: [1.5, 1.5, 1.5, 1.5, 1.5, 1.4], ZD: [2.4, 2.2, 2.0, 1.9, 1.8, 1.7], ZE: [4.2, 3.3, 2.8, 2.4, 2.2, 2.0] };
const NSCP_CA = { SA: [0.16, 0.32], SB: [0.2, 0.4], SC: [0.24, 0.4], SD: [0.28, 0.44], SE: [0.34, 0.44] };
const NSCP_CV = { SA: [0.16, 0.32], SB: [0.2, 0.4], SC: [0.32, 0.56], SD: [0.4, 0.64], SE: [0.64, 0.96] };

const tscSae = (T, SDS, SD1, TL) => { const TA = 0.2 * SD1 / SDS, TB = SD1 / SDS;
  return T < TA ? (0.4 + 0.6 * T / TA) * SDS : T <= TB ? SDS : T <= TL ? SD1 / T : SD1 * TL / T ** 2; };

function ntcCdmx(T, p) {
  const { a0, c, Ta, Tb, k, beta, Q, R0, k1 } = p;
  let a, pp = null;
  if (T < Ta) a = a0 + (beta * c - a0) * T / Ta;
  else if (T < Tb) a = beta * c;
  else { pp = k + (1 - k) * (Tb / T) ** 2; a = beta * c * pp * (Tb / T) ** 2; }
  const Qp = T <= Ta ? 1 + (Q - 1) * Math.sqrt(beta / k) * T / Ta : T <= Tb ? 1 + (Q - 1) * Math.sqrt(beta / k) : 1 + (Q - 1) * Math.sqrt(beta * pp / k);
  const R = T <= Ta ? k1 * R0 + 1 - Math.sqrt(T / Ta) : k1 * R0;
  return { a, Qp, R };
}

function ec8Fn(p, S, TB, TC, TD, beta) {
  if (!(TB < TC && TC < TD)) throw new Error("ec8: need TB < TC < TD");
  const ag = p.gamma_I * p.agR;
  if (p.kind === "elastic") {
    const eta = Math.max(Math.sqrt(10 / (5 + 100 * p.damping)), 0.55);
    return [T => T <= TB ? ag * S * (1 + T / TB * (eta * 2.5 - 1)) : T <= TC ? ag * S * eta * 2.5 : T <= TD ? ag * S * eta * 2.5 * TC / T : ag * S * eta * 2.5 * TC * TD / (T * T),
      [0, TB, TC, TD], { ag, S, TB, TC, TD, plateau: ag * S * eta * 2.5, eta }];
  }
  const q = p.q;
  const f = T => T <= TB ? ag * S * (2 / 3 + T / TB * (2.5 / q - 2 / 3)) : T <= TC ? ag * S * 2.5 / q
    : T <= TD ? Math.max(ag * S * 2.5 / q * TC / T, beta * ag) : Math.max(ag * S * 2.5 / q * TC * TD / (T * T), beta * ag);
  let tf = 2.5 * S * TC / (q * beta); if (tf > TD) tf = Math.sqrt(2.5 * S * TC * TD / (q * beta));
  return [f, [0, TB, TC, TD, tf], { ag, S, TB, TC, TD, plateau: ag * S * 2.5 / q, q, beta }];
}

export const WVC_SPEC_BUILD = {
  tsc2018(p) {
    const FS = p.FS ?? interp(p.SS, TSC_SS, TSC_FS[p.site]), F1 = p.F1 ?? interp(p.S1, TSC_S1, TSC_F1[p.site]);
    const SDS = p.SS * FS, SD1 = p.S1 * F1, TL = p.TL, TA = 0.2 * SD1 / SDS, TB = SD1 / SDS;
    const Ra = T => T > TB ? p.R / p.I : p.D + (p.R / p.I - p.D) * T / TB;
    return [T => p.kind === "design" ? tscSae(T, SDS, SD1, TL) / Ra(T) : tscSae(T, SDS, SD1, TL), [0, TA, TB, TL], { SDS, SD1, FS, F1, TA, TB, TL }];
  },
  kds41(p) {
    const SDS = p.SDS ?? p.S * 2.5 * p.Fa * 2 / 3, SD1 = p.SD1 ?? p.S * p.Fv * 2 / 3, TL = p.TL, Ts = SD1 / SDS, T0 = 0.2 * Ts, sc = p.IE / p.R;
    return [T => sc * (T < T0 ? SDS * (0.4 + 0.6 * T / T0) : T <= Ts ? SDS : T <= TL ? SD1 / T : SD1 * TL / (T * T)), [0, T0, Ts, TL], { SDS, SD1, T0, Ts, TL, scale: sc }];
  },
  nscp2015(p) {
    const zi = +p.Z === 0.4 ? 1 : 0;
    const Ca = p.Ca ?? NSCP_CA[p.soil][zi] * (zi ? p.Na : 1), Cv = p.Cv ?? NSCP_CV[p.soil][zi] * (zi ? p.Nv : 1);
    const Ts = Cv / (2.5 * Ca), T0 = 0.2 * Ts, sc = p.I / p.R;
    return [T => sc * (T < T0 ? Ca * (1 + 1.5 * T / T0) : T <= Ts ? 2.5 * Ca : Cv / T), [0, T0, Ts], { Ca, Cv, T0, Ts, scale: sc }];
  },
  ntc_cdmx(p) {
    if (p.Tb < p.Ta) throw new Error("ntc_cdmx: Tb must be >= Ta");
    return [T => { const d = ntcCdmx(T, p); return p.kind === "design" ? d.a / (d.Qp * d.R) : d.a; }, [0, p.Ta, p.Tb], { plateau: p.beta * p.c, Ta: p.Ta, Tb: p.Tb }];
  },
  ntc2018(p) {
    const q = ntcParams(p.ag, p.F0, p.Tc_star, p.soil, p.topography);
    const design = p.kind === "design", eta = design ? 1 / p.q : Math.max(Math.sqrt(10 / (5 + 100 * p.damping)), 0.55), floor = design ? 0.2 * p.ag : 0;
    return [T => Math.max(ntcSe(T, p.ag, p.F0, q.S, q.TB, q.TC, q.TD, eta), floor), [0, q.TB, q.TC, q.TD], { ...q, eta, plateau: p.ag * q.S * eta * p.F0 }];
  },
  ec8_na(p) {
    const na = naParams(p.annex, p.ground_type, p.spectrum_type, p.gamma_I * p.agR);
    const r = ec8Fn(p, na.S, na.TB, na.TC, na.TD, p.beta ?? na.beta);
    Object.assign(r[2], { annex: p.annex, spectrum_type: na.spectrum_type, ground_type: p.ground_type });
    return r;
  },
};

/* ---------------- spectrum file parsing (spectra_codes_wavec.parse_spectrum_text) */
export const WVC_VALUE_UNITS = { g: 1, "m/s2": 1 / G0, "cm/s2": 0.01 / G0, "mm/s2": 0.001 / G0, "ft/s2": 0.3048 / G0, "in/s2": 0.0254 / G0 };
const NUM = /^[+-]?(\d+(\.\d*)?|\.\d+)([eEdD][+-]?\d+)?$/;
const DAMP = /damp\w*\s*[=:]?\s*([0-9]*\.?[0-9]+)\s*(%?)/i;
const fnum = t => parseFloat(t.replace(/[dD]/, "e"));
function damp(m) { let v = parseFloat(m[1]); if (m[2] === "%" || v >= 1) v /= 100; if (!(v > 0 && v < 1)) throw new Error("damping in the file must be in (0, 100) %"); return v; }
export function wvcParseSpectrum(text, { period_units = "s", value_units = "g", skip_lines = 0, scale = 1 } = {}) {
  if (typeof text !== "string" || !text.trim()) throw new Error("spectrum file is empty");
  if (!["s", "Hz"].includes(period_units)) throw new Error("period_units must be one of s, Hz");
  if (!(value_units in WVC_VALUE_UNITS)) throw new Error(`value_units must be one of ${Object.keys(WVC_VALUE_UNITS).join(", ")}`);
  if (!Number.isInteger(skip_lines) || skip_lines < 0) throw new Error("skip_lines must be an integer >= 0");
  if (!(typeof scale === "number" && isFinite(scale) && scale > 0)) throw new Error("scale must be a finite value > 0");
  const lines = text.split(/\r?\n/).slice(skip_lines);
  let damping = null, header = skip_lines;
  const pts = [];
  lines.forEach((raw, i) => {
    const no = i + skip_lines + 1;
    const toks = raw.split(/#|\/\/|!|;;/)[0].trim().split(/[,;\s]+/).filter(Boolean);
    if (!toks.length) { const m = raw.match(DAMP); if (m && !pts.length) damping = damp(m); return; }
    const nums = toks.filter(t => NUM.test(t));
    if (nums.length >= 2 && NUM.test(toks[0])) {
      let x = fnum(toks[0]); const y = NUM.test(toks[1]) ? fnum(toks[1]) : fnum(nums[1]);
      if (period_units === "Hz") { if (!(x > 0)) throw new Error(`line ${no}: frequency must be > 0`); x = 1 / x; }
      if (x < 0 || y < 0) throw new Error(`line ${no}: period and value must be >= 0`);
      pts.push([x, y * WVC_VALUE_UNITS[value_units] * scale]);
      return;
    }
    if (pts.length) throw new Error(`line ${no}: expected a period-value pair, got '${raw.trim()}'`);
    header++;
    const m = raw.match(DAMP); if (m) damping = damp(m);
  });
  if (!pts.length) throw new Error("no period-value pairs found");
  pts.sort((a, b) => a[0] - b[0]);
  for (let i = 1; i < pts.length; i++) if (pts[i][0] === pts[i - 1][0]) throw new Error(`duplicate period ${pts[i][0]} s`);
  return { points: pts, n: pts.length, damping, header_lines: header, period_units, value_units, scale };
}

/** ?mock=1 has no network: the lookup always reports the service unreachable. */
export function wvcMockUsgs() {
  const e = new Error("USGS design-maps service unreachable (offline demo, ?mock=1); enter SS/S1 or the 22 multi-period ordinates by hand (they are on the USGS / ATC hazard tool printout)");
  e.status = 503; e.manual = true;
  return Promise.reject(e);
}
