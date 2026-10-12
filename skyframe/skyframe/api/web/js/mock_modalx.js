/* SkyFrame — ?mock=1 stand-ins for the TH-function tools and named modal
   cases (js/modalx.js). Same JSON shapes as the backend (CONTRACT "Time-
   history functions, mass sources, modal cases, Ritz basis and FNA
   breadth"); the numbers are plausible, not analysis results:
     POST /api/th_function/import    → mxMockImport   (real AT2 / pairs / equal parsing)
     POST /api/th_function/generate  → mxMockGenerate (exact generator formulas)
     POST /api/th_function/match     → mxMockMatch    (record rescaled; target echoed ±3 %)
     results.modal_cases             → mxMockModalCases (default modes, √(mass ratio) periods) */

const G = 9.80665;
const UNIT = { g: G, "m/s2": 1, "cm/s2": 0.01, "mm/s2": 0.001, "in/s2": 0.0254, "ft/s2": 0.3048 };
const NUM = /[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?/g;
const nums = t => (String(t).match(NUM) || []).map(s => Number(s.replace(/[Dd]/, "e")));

export function mxMockImport(b = {}) {
  const text = String(b.text || "");
  if (!text.trim()) throw new Error("the file text is empty");
  const fmt = b.format || "at2";
  const warnings = [];
  let raw, dt, units = b.units || null;
  const src = { kind: "file", format: fmt };
  if (fmt === "at2") {
    const lines = text.split(/\r?\n/);
    if (lines.length < 4) throw new Error("AT2: expected a 4-line header");
    const m = /NPTS\s*=\s*(\d+)\s*,?\s*DT\s*=\s*([-+0-9.EeDd]+)/i.exec(lines[3]);
    let npts;
    if (m) { npts = +m[1]; dt = Number(m[2].replace(/[Dd]/, "e")); }
    else { const h = nums(lines[3]); if (h.length < 2 || !/NPTS/i.test(lines[3])) throw new Error("AT2: line 4 must give NPTS and DT"); [npts, dt] = h; }
    const v = nums(lines.slice(4).join("\n"));
    if (v.length < npts) throw new Error(`AT2: header says NPTS=${npts} but only ${v.length} values follow`);
    raw = v.slice(0, npts);
    if (!units) units = /CM\/S/i.test(lines[2]) ? "cm/s2" : "g";
    src.npts = npts; src.title = lines.slice(0, 2).map(s => s.trim()).filter(Boolean).join(" / ");
  } else {
    const body = text.split(/\r?\n/).slice(b.header_lines || 0).join("\n");
    const v = nums(body);
    units = units || "g";
    if (fmt === "equal") {
      dt = +b.dt; if (!(dt > 0)) throw new Error("dt must be > 0");
      raw = v;
    } else {
      if (v.length % 2) throw new Error(`time-value pairs: an odd count of numbers (${v.length})`);
      let ts = v.filter((_, i) => i % 2 === 0), av = v.filter((_, i) => i % 2 === 1);
      if (ts.some((t, i) => i && t <= ts[i - 1])) throw new Error("time-value pairs: times must strictly increase");
      if (ts[0] > 0) { ts = [0, ...ts]; av = [0, ...av]; warnings.push(`record starts at t = ${v[0]} s: a (0, 0) point was prepended`); }
      const d = ts.slice(1).map((t, i) => t - ts[i]);
      const uniform = Math.max(...d) - Math.min(...d) <= 1e-6 * Math.max(...d);
      if (b.dt == null && uniform) { dt = (ts[ts.length - 1] - ts[0]) / (ts.length - 1); raw = av; }
      else {
        dt = b.dt != null ? +b.dt : Math.min(...d);
        const n = Math.floor(ts[ts.length - 1] / dt + 1e-9) + 1;
        raw = [];
        for (let k = 0, j = 0; k < n; k++) {
          const t = k * dt;
          while (j < ts.length - 2 && ts[j + 1] < t) j++;
          const w = (t - ts[j]) / (ts[j + 1] - ts[j]);
          raw.push(av[j] + Math.min(Math.max(w, 0), 1) * (av[j + 1] - av[j]));
        }
        warnings.push(`resampled to dt = ${dt} s (linear interpolation)`);
      }
    }
    src.header_lines = b.header_lines || 0;
  }
  if (!(units in UNIT)) throw new Error("units must be one of " + Object.keys(UNIT).join(", "));
  if (!raw || raw.length < 2) throw new Error("a time-history function needs at least 2 values");
  const sc = b.scale == null ? 1 : +b.scale;
  const f = UNIT[units] * sc;
  Object.assign(src, { units, scale: sc });
  return { values: raw.map(x => x * f), dt, source: src, warnings };
}

export function mxMockGenerate(b = {}) {
  const kind = b.kind, A = b.amplitude == null ? 1 : +b.amplitude;
  const src = { kind };
  let values = [], dt;
  const per = (T, n, cyc, wave) => {
    dt = T / n;
    for (let k = 0; k < n * cyc; k++) values.push(wave((k % n) / n));
    values.push(wave(1));
  };
  const pl = pts => s => { for (let i = 1; i < pts.length; i++) if (s <= pts[i][0]) { const [x0, y0] = pts[i - 1], [x1, y1] = pts[i]; return y0 + (s - x0) / (x1 - x0) * (y1 - y0); } return pts[pts.length - 1][1]; };
  if (["sine", "cosine", "sawtooth", "triangular"].includes(kind)) {
    const T = +(b.period ?? 1), n = +(b.steps_per_cycle ?? (kind === "sine" || kind === "cosine" ? 16 : 40)), cyc = +(b.cycles ?? 1);
    if (!(T > 0)) throw new Error("period must be > 0");
    if (!(n >= 4) || !Number.isInteger(n)) throw new Error("steps_per_cycle must be an integer >= 4");
    const r = (b.ramp_time ?? T / 8) / T;
    if (kind === "sawtooth" && r > 0.25 + 1e-12) throw new Error("sawtooth: ramp_time must be <= period/4");
    const wave = kind === "sine" ? s => A * Math.sin(2 * Math.PI * s) : kind === "cosine" ? s => A * Math.cos(2 * Math.PI * s)
      : kind === "triangular" ? pl([[0, 0], [0.25, A], [0.75, -A], [1, 0]]) : pl([[0, 0], [r, A], [0.5 - r, A], [0.5 + r, -A], [1 - r, -A], [1, 0]]);
    per(T, n, cyc, wave);
    if (kind === "sine") values = values.map((v, k) => (k % n) === 0 ? 0 : v);
    Object.assign(src, { period: T, steps_per_cycle: n, cycles: cyc, amplitude: A });
  } else if (kind === "ramp") {
    const tr = +(b.ramp_time ?? 1), tm = +(b.max_time ?? 2 * tr);
    dt = +(b.dt ?? tr / 20);
    if (!(tr > 0 && tm >= tr && dt > 0)) throw new Error("ramp: ramp_time > 0, max_time >= ramp_time and dt > 0");
    const n = Math.floor(tm / dt + 1e-9) + 1;
    for (let k = 0; k < n; k++) values.push(A * Math.min(k * dt / tr, 1));
    Object.assign(src, { ramp_time: tr, max_time: tm, amplitude: A });
  } else if (kind === "user_periodic") {
    const cv = (b.values || []).map(Number), cyc = +(b.cycles ?? 1);
    dt = +b.dt;
    if (cv.length < 2 || !(dt > 0)) throw new Error("user_periodic: give at least 2 values and dt > 0");
    for (let c = 0; c < cyc; c++) values.push(...(c === 0 || cv[0] !== cv[cv.length - 1] ? cv : cv.slice(1)));
    values = values.map(v => A * v);
    Object.assign(src, { cycles: cyc, amplitude: A, cycle_values: cv });
  } else throw new Error(`generator must be one of sine, cosine, ramp, sawtooth, triangular, user_periodic (got '${kind}')`);
  src.dt = dt;
  return { values, dt, source: src };
}

/** Plausible spectral match: the record is rescaled so its peak matches the
    target's zero-period ordinate; the returned "matched" spectrum is the
    target with a ±3 % ripple (the backend reports the true misfit). */
export function mxMockMatch(b = {}) {
  const vals = (b.values || []).map(Number), dt = +b.dt;
  const tgt = (b.target || []).map(p => [+p[0], +p[1]]).sort((x, y) => x[0] - y[0]);
  if (vals.length < 2 || !(dt > 0) || tgt.length < 2) throw new Error("give the seed record (values + dt) and the target (at least 2 points)");
  const tMin = +(b.t_min ?? 0.05), tMax = +(b.t_max ?? 3);
  const pga = Math.max(...vals.map(Math.abs)) || 1;
  const s = (tgt[0][1] * G) / pga;
  const periods = [], target = [], matched = [], original = [];
  const interp = T => { for (let i = 1; i < tgt.length; i++) if (T <= tgt[i][0]) { const [a, sa] = tgt[i - 1], [c, sc] = tgt[i]; return sa + (T - a) / (c - a) * (sc - sa); } return tgt[tgt.length - 1][1]; };
  for (let i = 0; i < 40; i++) {
    const T = tMin * Math.pow(tMax / tMin, i / 39);
    const t = interp(T) * G;
    periods.push(T); target.push(t); matched.push(t * (1 + 0.03 * Math.sin(i))); original.push(t * (0.6 + 0.5 * Math.abs(Math.cos(i * 0.7))));
  }
  const values = vals.map(v => v * s);
  return { values, dt, periods, target, matched, original, max_misfit: 0.03, iterations: 12,
    source: { kind: "matched", method: "frequency_domain", damping: b.damping ?? 0.05, t_min: tMin, t_max: tMax, iterations: 12,
      max_misfit: 0.03, target: tgt, target_units: "g" } };
}

/** results.modal_cases for the mock: the default modes truncated to the
    case's count, periods scaled by √(named-source pattern factor sum /
    default factor sum) — a uniform mass scaling — and a Ritz case keeps the
    X/Y-dominant modes first. */
export function mxMockModalCases(model, results) {
  const out = {};
  const base = results && results.modal;
  if (!base || !base.periods) return out;
  const sum = o => Object.values(o || {}).reduce((a, v) => a + (+v || 0), 0);
  const def = sum(model.mass_source && Object.keys(model.mass_source).length ? model.mass_source : model.mass_from_patterns) || 1;
  for (const [name, c] of Object.entries(model.modal_cases || {})) {
    const src = c.mass_source ? (model.mass_sources || {})[c.mass_source] : null;
    const ratio = src ? (sum(src.mass_source) || def) / def : 1;
    const f = Math.sqrt(ratio > 0 ? ratio : 1);
    let idx = base.periods.map((_, i) => i);
    if ((c.type || "eigen") === "ritz")
      idx = idx.filter(i => { const p = base.participation[i] || {}; return (p.ux || 0) + (p.uy || 0) >= (p.rz || 0); }).concat(idx.filter(i => { const p = base.participation[i] || {}; return (p.ux || 0) + (p.uy || 0) < (p.rz || 0); }));
    idx = idx.slice(0, Math.max(1, c.num_modes || 12));
    out[name] = {
      periods: idx.map(i => base.periods[i] * f),
      frequencies: idx.map(i => base.frequencies[i] / f),
      participation: idx.map((i, k) => ({ ...base.participation[i], mode: k + 1, T: base.periods[i] * f })),
      shapes: Object.fromEntries(idx.map((i, k) => [String(k + 1), JSON.parse(JSON.stringify((base.shapes || {})[String(i + 1)] || {}))])),
      type: c.type || "eigen", mass_source: c.mass_source || "",
      warnings: (c.type === "ritz" && idx.length < (c.num_modes || 12)) ? [`only ${idx.length} Ritz vector(s) available (requested ${c.num_modes})`] : [],
    };
  }
  return out;
}
