/* SkyFrame — offline (?mock=1) behaviour for the analysis-results tables,
   modal load participation and the frequency-domain / energy / pushover
   result blocks. Mirrors the backend contracts (CONTRACT.md):
     POST /api/tables/list                → mockTablesList()
     POST /api/tables/<key> {cases?}      → mockTable(model, results, key, body)
     POST /api/analyze/load_participation → mockLoadParticipation(model, results)
   plus two hooks used by mock.js:
     augmentMockModel(model)    adds a steady-state + PSD case (with their
                                frequency functions), energy:true on the first
                                TH case and a "mode" pushover distribution
     augmentMockResults(model, r)  adds steady_state / psd blocks, TH energy
                                series and pushover capacity_curve/distribution
   All values SI (kN, m, t, s); the frontend converts through units.js. */

const C = (key, label, quantity) => ({ key, label, quantity });
const G = 9.80665;

/* ---------------------------------------------------------------- catalogue */
const ST = (k) => C("story", "Story", "text");
const CASE = [C("case", "Output Case", "text"), C("case_type", "Case Type", "text")];
const CAT = [
  ["joint_displacements", "Joint Displacements", "Joint Output > Displacements",
    [ST(), C("label", "Label", "text"), C("joint", "Unique Name", "id"), ...CASE,
      C("ux", "UX", "length"), C("uy", "UY", "length"), C("uz", "UZ", "length"),
      C("rx", "RX", "angle"), C("ry", "RY", "angle"), C("rz", "RZ", "angle")]],
  ["joint_drifts", "Joint Drifts", "Joint Output > Displacements",
    [ST(), C("label", "Label", "text"), C("joint", "Unique Name", "id"), C("joint_below", "Joint Below", "id"), ...CASE,
      C("x", "X", "length"), C("y", "Y", "length"), C("z", "Z", "length"), C("height", "Height", "length"),
      C("disp_x", "Disp X", "length"), C("disp_y", "Disp Y", "length"),
      C("drift_x", "Drift X", "ratio"), C("drift_y", "Drift Y", "ratio")]],
  ["joint_reactions", "Joint Reactions", "Joint Output > Reactions",
    [ST(), C("label", "Label", "text"), C("joint", "Unique Name", "id"), ...CASE,
      C("x", "X", "length"), C("y", "Y", "length"), C("z", "Z", "length"),
      C("FX", "FX", "force"), C("FY", "FY", "force"), C("FZ", "FZ", "force"),
      C("MX", "MX", "moment"), C("MY", "MY", "moment"), C("MZ", "MZ", "moment")]],
  ["story_drifts", "Story Drifts", "Structure Output > Story Output",
    [ST(), ...CASE, C("ux", "UX", "length"), C("uy", "UY", "length"),
      C("drift_x", "Drift X", "ratio"), C("drift_y", "Drift Y", "ratio")]],
  ["story_forces", "Story Forces", "Structure Output > Story Output",
    [ST(), ...CASE, C("location", "Location", "text"),
      C("P", "P", "force"), C("VX", "VX", "force"), C("VY", "VY", "force"),
      C("T", "T", "moment"), C("MX", "MX", "moment"), C("MY", "MY", "moment"),
      C("n_members", "Members Cut", "id"), C("n_shells", "Shells Cut (excluded)", "id")]],
  ["story_stiffness", "Story Stiffness", "Structure Output > Story Output",
    [ST(), ...CASE, C("shear_x", "Shear X", "force"), C("drift_x", "Drift X", "length"), C("stiff_x", "Stiff X", "stiffness"),
      C("shear_y", "Shear Y", "force"), C("drift_y", "Drift Y", "length"), C("stiff_y", "Stiff Y", "stiffness")]],
  ["diaphragm_cm_displacements", "Diaphragm Center Of Mass Displacements", "Structure Output > Diaphragm Output",
    [ST(), C("diaphragm", "Diaphragm", "text"), ...CASE, C("ux", "UX", "length"), C("uy", "UY", "length"),
      C("rz", "RZ", "angle"), C("x", "X", "length"), C("y", "Y", "length"), C("z", "Z", "length")]],
  ["diaphragm_max_avg_drifts", "Diaphragm Max Over Avg Drifts", "Structure Output > Diaphragm Output",
    [ST(), ...CASE, C("direction", "Direction", "text"), C("max_disp", "Max Disp", "length"),
      C("avg_disp", "Avg Disp", "length"), C("disp_ratio", "Disp Ratio", "ratio"),
      C("max_drift", "Max Drift", "ratio"), C("avg_drift", "Avg Drift", "ratio"),
      C("drift_ratio", "Drift Ratio", "ratio"), C("label_max", "Label (max)", "id")]],
  ["centers_mass_rigidity", "Centers Of Mass And Rigidity", "Structure Output > Other Output Items",
    [ST(), C("diaphragm", "Diaphragm", "text"), C("mass", "Mass", "mass"),
      C("cm_x", "XCM", "length"), C("cm_y", "YCM", "length"), C("cr_x", "XCR", "length"), C("cr_y", "YCR", "length")]],
  ["base_reactions", "Base Reactions", "Structure Output > Base Reactions",
    [...CASE, C("FX", "FX", "force"), C("FY", "FY", "force"), C("FZ", "FZ", "force"),
      C("MX", "MX", "moment"), C("MY", "MY", "moment"), C("MZ", "MZ", "moment"),
      C("X", "X", "length"), C("Y", "Y", "length"), C("Z", "Z", "length")]],
  ["load_pattern_summary", "Load Pattern Totals And Equilibrium", "Structure Output > Base Reactions",
    [C("pattern", "Load Pattern", "text"), C("type", "Type", "text"), C("self_weight", "Self Wt Multiplier", "ratio"),
      C("FX", "Applied FX", "force"), C("FY", "Applied FY", "force"), C("FZ", "Applied FZ", "force"),
      C("case", "Check Case", "text"), C("react_FX", "Reaction FX", "force"), C("react_FY", "Reaction FY", "force"),
      C("react_FZ", "Reaction FZ", "force"), C("error_pct", "Error", "percent")]],
  ["load_case_equilibrium", "Load Case Equilibrium Check", "Structure Output > Base Reactions",
    [...CASE, C("applied_FX", "Applied FX", "force"), C("applied_FY", "Applied FY", "force"),
      C("applied_FZ", "Applied FZ", "force"), C("react_FX", "Reaction FX", "force"),
      C("react_FY", "Reaction FY", "force"), C("react_FZ", "Reaction FZ", "force"), C("error_pct", "Error", "percent")]],
  ["modal_periods", "Modal Periods And Frequencies", "Modal Results",
    [C("case", "Case", "text"), C("mode", "Mode", "id"), C("period", "Period", "time"),
      C("frequency", "Frequency", "frequency"), C("circ_freq", "CircFreq", "circular_frequency"),
      C("eigenvalue", "Eigenvalue", "eigenvalue")]],
  ["modal_mass_ratios", "Modal Participating Mass Ratios", "Modal Results",
    [C("case", "Case", "text"), C("mode", "Mode", "id"), C("period", "Period", "time"),
      ...["UX", "UY", "UZ"].map(k => C(k, k, "ratio")), ...["UX", "UY", "UZ"].map(k => C("Sum" + k, "Sum " + k, "ratio")),
      ...["RX", "RY", "RZ"].map(k => C(k, k, "ratio")), ...["RX", "RY", "RZ"].map(k => C("Sum" + k, "Sum " + k, "ratio"))]],
  ["modal_participation_factors", "Modal Participation Factors", "Modal Results",
    [C("case", "Case", "text"), C("mode", "Mode", "id"), C("period", "Period", "time"),
      ...["UX", "UY", "UZ", "RX", "RY", "RZ"].map(k => C(k, k, "factor")),
      C("modal_mass", "Modal Mass", "mass"), C("modal_stiffness", "Modal Stiff", "stiffness")]],
  ["modal_direction_factors", "Modal Direction Factors", "Modal Results",
    [C("case", "Case", "text"), C("mode", "Mode", "id"), C("period", "Period", "time"),
      ...["UX", "UY", "UZ", "RZ"].map(k => C(k, k, "ratio")), C("dominant", "Dominant", "text")]],
];
const CATALOGUE = CAT.map(([key, title, group, columns]) =>
  ({ key, title, group: "Analysis Results > " + group, columns }));
const BY_KEY = Object.fromEntries(CATALOGUE.map(t => [t.key, t]));
/** Tables the offline mock computes (the rest return no rows + a warning). */
export const MOCK_COMPUTED = ["joint_displacements", "story_drifts", "story_forces",
  "modal_periods", "modal_mass_ratios", "base_reactions", "load_pattern_summary"];

export function mockTablesList() {
  return { tables: JSON.parse(JSON.stringify(CATALOGUE)) };
}

/* ---------------------------------------------------------------- helpers */
const TOL = 1e-3;
function sources(model, r, filter) {
  const out = [];
  for (const [n, cd] of Object.entries(r.cases || {})) {
    if (filter && !filter.has(n)) continue;
    const lc = (model.cases || {})[n] || {};
    out.push([n, (lc.pdelta || (lc.geometric && lc.geometric !== "linear")) ? "NonStatic" : "LinStatic", cd]);
  }
  for (const [n, cd] of Object.entries(r.combos || {})) {
    if (filter && !filter.has(n)) continue;
    const cb = (model.combos || {})[n] || {};
    if ((cb.combo_type || "add") !== "add" || cd.min) continue;
    out.push([n, "Combination", cd]);
  }
  return out;
}
function storyAt(model, z) {
  for (const s of model.stories || []) if (Math.abs(s.elevation - z) < TOL) return s.name;
  return Math.abs(z) < TOL ? "Base" : "";
}
function gridLabel(model, x, y) {
  const g = model.grid || {};
  const i = (g.x_lines || []).findIndex(v => Math.abs(v - x) < TOL);
  const j = (g.y_lines || []).findIndex(v => Math.abs(v - y) < TOL);
  if (i < 0 || j < 0) return "";
  return `${(g.x_labels || [])[i] ?? i}${(g.y_labels || [])[j] ?? j}`;
}
function resultantLocation(F, M, zRef) {
  const f2 = F[0] ** 2 + F[1] ** 2 + F[2] ** 2;
  if (f2 < 1e-18) return [null, null, null];
  let r0 = [(F[1] * M[2] - F[2] * M[1]) / f2, (F[2] * M[0] - F[0] * M[2]) / f2, (F[0] * M[1] - F[1] * M[0]) / f2];
  if (Math.abs(F[2]) > 1e-9 * Math.sqrt(f2)) {
    const t = (zRef - r0[2]) / F[2];
    r0 = r0.map((v, k) => v + t * F[k]);
  }
  return r0;
}
const storiesTopFirst = model => [...(model.stories || [])].reverse();

/* ---------------------------------------------------------------- tables */
const T = {
  joint_displacements(model, r, f) {
    const rows = [];
    const tags = Object.keys(r.nodes || {}).sort((a, b) => {
      const pa = r.nodes[a], pb = r.nodes[b];
      return pa[2] - pb[2] || pa[0] - pb[0] || pa[1] - pb[1] || (+a) - (+b);
    });
    for (const [name, ctype, cd] of sources(model, r, f)) {
      const nd = cd.node_disp || {};
      for (const t of tags) {
        const d = nd[t];
        if (!d) continue;
        const [x, y, z] = r.nodes[t];
        rows.push({ story: storyAt(model, z), label: gridLabel(model, x, y), joint: +t,
          case: name, case_type: ctype, ux: d[0], uy: d[1], uz: d[2], rx: d[3], ry: d[4], rz: d[5] });
      }
    }
    return rows;
  },
  story_drifts(model, r, f) {
    const rows = [];
    for (const [name, ctype, cd] of sources(model, r, f))
      for (const s of storiesTopFirst(model)) {
        const st = (cd.story || {})[s.name];
        if (!st) continue;
        rows.push({ story: s.name, case: name, case_type: ctype, ux: st.ux, uy: st.uy,
          drift_x: st.drift_x, drift_y: st.drift_y });
      }
    return rows;
  },
  story_forces(model, r, f, warn) {
    const rows = [];
    const stories = model.stories || [];
    const g = model.grid || {};
    const xs = g.x_lines || [0], ys = g.y_lines || [0];
    const nCols = s => (model.members || []).filter(m => m.kind === "column" && m.story === s).length;
    let anyShell = false;
    for (const [name, ctype, cd] of sources(model, r, f)) {
      const st = cd.story || {};
      const n = stories.length;
      const FZ = (cd.base || {}).FZ || 0;
      // story lateral forces from the shear profile (shear = cumulative above)
      const Fx = [], Fy = [];
      for (let i = 0; i < n; i++) {
        const a = st[stories[i].name] || {}, b = st[(stories[i + 1] || {}).name] || {};
        Fx.push((a.shear_x || 0) - (b.shear_x || 0));
        Fy.push((a.shear_y || 0) - (b.shear_y || 0));
      }
      const out = [];
      for (let i = 0; i < n; i++) {
        const s = stories[i], a = st[s.name] || {};
        const zBot = i ? stories[i - 1].elevation : 0;
        const nSh = (model.shells || []).filter(sh => sh.kind === "wall" &&
          Math.min(...sh.corners.map(p => p[2])) < s.elevation - 1e-6 &&
          Math.max(...sh.corners.map(p => p[2])) > zBot + 1e-6).length;
        if (nSh) anyShell = true;
        for (const [loc, zc] of [["Top", s.elevation], ["Bottom", zBot]]) {
          // loads above the cut: stories j >= i (Top excludes nothing above the slab of story i)
          let MX = 0, MY = 0;
          for (let j = i; j < n; j++) {
            const lever = stories[j].elevation - zc;
            MY += Fx[j] * lever; MX -= Fy[j] * lever;
          }
          const frac = (n - i - (loc === "Top" ? 0.5 : 0)) / n;
          out.push({ story: s.name, case: name, case_type: ctype, location: loc,
            P: -Math.abs(FZ) * frac * Math.sign(FZ || 0) || 0, VX: a.shear_x || 0, VY: a.shear_y || 0,
            T: 0.02 * ((a.shear_x || 0) * ((ys[ys.length - 1] - ys[0]) / 2) - (a.shear_y || 0) * ((xs[xs.length - 1] - xs[0]) / 2)) * 0.1,
            MX, MY, n_members: nCols(s.name), n_shells: nSh });
        }
      }
      for (let i = out.length - 2; i >= 0; i -= 2) rows.push(out[i], out[i + 1]);
    }
    if (anyShell) warn.push("shell elements cross some story cuts and are EXCLUDED from the story forces (frame members only — see n_shells)");
    return rows;
  },
  modal_periods(model, r) {
    return ((r.modal || {}).periods || []).map((T, i) => {
      const w = 2 * Math.PI / T;
      return { case: "Modal", mode: i + 1, period: T, frequency: 1 / T, circ_freq: w, eigenvalue: w * w };
    });
  },
  modal_mass_ratios(model, r) {
    const cum = { UX: 0, UY: 0, UZ: 0, RX: 0, RY: 0, RZ: 0 };
    return ((r.modal || {}).participation || []).map((p, i) => {
      const v = { UX: p.ux || 0, UY: p.uy || 0, UZ: 0, RX: 0.92 * (p.uy || 0), RY: 0.92 * (p.ux || 0), RZ: p.rz || 0 };
      const row = { case: "Modal", mode: p.mode || i + 1, period: p.T };
      for (const k of ["UX", "UY", "UZ", "RX", "RY", "RZ"]) {
        cum[k] = Math.min(1, cum[k] + v[k]);
        row[k] = v[k]; row["Sum" + k] = cum[k];
      }
      return row;
    });
  },
  base_reactions(model, r, f) {
    return sources(model, r, f).map(([name, ctype, cd]) => {
      const b = cd.base || {};
      const F = ["FX", "FY", "FZ"].map(k => +b[k] || 0), M = ["MX", "MY", "MZ"].map(k => +b[k] || 0);
      const [X, Y, Z] = resultantLocation(F, M, 0);
      return { case: name, case_type: ctype, FX: F[0], FY: F[1], FZ: F[2], MX: M[0], MY: M[1], MZ: M[2], X, Y, Z };
    });
  },
  load_pattern_summary(model, r, f) {
    const rows = [];
    for (const [pname, pat] of Object.entries(model.patterns || {})) {
      const row = { pattern: pname, type: pat.kind || pat.type || "Other",
        self_weight: pat.self_weight_factor || 0, FX: 0, FY: 0, FZ: 0, case: "",
        react_FX: null, react_FY: null, react_FZ: null, error_pct: null };
      for (const [cname, lc] of Object.entries(model.cases || {})) {
        const pk = Object.keys(lc.patterns || {});
        if (pk.length !== 1 || pk[0] !== pname || !(r.cases || {})[cname]) continue;
        if (f && !f.has(cname)) continue;
        const fac = lc.patterns[pname];
        if (!fac) continue;
        const b = r.cases[cname].base || {};
        const R = ["FX", "FY", "FZ"].map(k => (+b[k] || 0) / fac);
        // applied = -reaction with a tiny deterministic discretisation residual
        const A = R.map((v, k) => -v * (1 + 1e-4 * (k + 1)));
        row.FX = A[0]; row.FY = A[1]; row.FZ = A[2];
        const num = Math.hypot(A[0] + R[0], A[1] + R[1], A[2] + R[2]);
        const den = Math.hypot(...A);
        Object.assign(row, { case: cname, react_FX: R[0], react_FY: R[1], react_FZ: R[2],
          error_pct: den > 1e-12 ? 100 * num / den : null });
        break;
      }
      rows.push(row);
    }
    return rows;
  },
};

/** Mock POST /api/tables/<key> {cases?}. Throws on unknown key / bad cases. */
export function mockTable(model, results, key, body = {}) {
  const meta = BY_KEY[key];
  if (!meta) { const e = new Error(`unknown table '${key}'`); e.status = 404; throw e; }
  if (body.cases != null && (!Array.isArray(body.cases) || body.cases.some(c => typeof c !== "string")))
    throw new Error("cases must be a list of names");
  if (!results) throw new Error("Run an analysis first");
  const warnings = [];
  const f = Array.isArray(body.cases) ? new Set(body.cases) : null;
  let rows = [];
  if (T[key]) rows = T[key](model, results, f, warnings);
  else warnings.push("offline mock: this table is computed by the live backend only");
  return { key, title: meta.title, group: meta.group,
    columns: JSON.parse(JSON.stringify(meta.columns)), rows, warnings };
}

/* ---------------------------------------------------------------- load participation */
export function mockLoadParticipation(model, results) {
  const part = ((results || {}).modal || {}).participation || [];
  if (!part.length) throw new Error("model has no modes (MODAL not run)");
  const sum = k => Math.min(100, 100 * part.reduce((a, p) => a + (p[k] || 0), 0));
  const ux = sum("ux"), uy = sum("uy");
  const acc = {
    UX: { static: Math.min(100, ux + 0.6 * (100 - ux)), dynamic: ux },
    UY: { static: Math.min(100, uy + 0.6 * (100 - uy)), dynamic: uy },
    UZ: { static: 0, dynamic: 0 },
  };
  const patterns = {};
  for (const [n, p] of Object.entries(model.patterns || {})) {
    const lateral = /^(EQ|WIND|W|E)/i.test(n) || /quake|wind|seismic/i.test(p.kind || "");
    const dirY = /Y$/i.test(n);
    const base = lateral ? (dirY ? uy : ux) : 3.5 + (n.length % 5) * 0.8;
    patterns[n] = { static: +Math.min(100, base * (lateral ? 1.06 : 1) + (lateral ? 0 : 1.2)).toFixed(3),
      dynamic: +Math.min(100, base).toFixed(3) };
  }
  return { acceleration: acc, patterns };
}

/* ---------------------------------------------------------------- model augmentation */
export function augmentMockModel(m) {
  if (!m || typeof m !== "object") return m;
  const roof = (m.stories || [])[m.stories.length - 1];
  const g = m.grid || {};
  const x1 = (g.x_lines || [0]).slice(-1)[0], y0 = (g.y_lines || [0])[0];
  m.frequency_functions = m.frequency_functions || {
    "SS-FLAT": { name: "SS-FLAT", kind: "steady_state", points: [[0, 1], [20, 1]] },
    "PSD-KT": { name: "PSD-KT", kind: "psd", points: [[0.1, 2e-4], [1, 8e-4], [2.5, 1e-3], [6, 4e-4], [15, 6e-5]] },
  };
  const out = roof ? [[x1, y0, roof.elevation]] : [];
  m.steady_state_cases = m.steady_state_cases || {
    "SS-ACC-X": { name: "SS-ACC-X", loads: [{ pattern: "accel", direction: "UX", scale: 1.0, function: "SS-FLAT", phase_deg: 0 }],
      freq_start_hz: 0, freq_end_hz: 10, n_freq: 201, frequencies: [], modal_refine: true, method: "modal",
      num_modes: 0, damping: 0.05, damping_type: "modal", output_points: out },
  };
  m.psd_cases = m.psd_cases || {
    "PSD-ACC-X": { name: "PSD-ACC-X", loads: [{ pattern: "accel", direction: "UX", scale: G, function: "PSD-KT", phase_deg: 0 }],
      freq_start_hz: 0.1, freq_end_hz: 15, n_freq: 241, frequencies: [], modal_refine: true, method: "modal",
      num_modes: 0, damping: 0.05, damping_type: "modal", output_points: out },
  };
  const th = Object.values(m.th_cases || {})[0];
  if (th && th.energy === undefined) th.energy = true;
  for (const pc of Object.values(m.pushover_cases || {}))
    if (pc.load_distribution === undefined) pc.load_distribution = "mode";
  return m;
}

/* ---------------------------------------------------------------- results augmentation */
function freqGrid(fc, modalF) {
  const set = new Set();
  const n = Math.max(0, Math.round(fc.n_freq ?? 101));
  const a = +fc.freq_start_hz || 0, b = +fc.freq_end_hz || 10;
  for (let i = 0; i < n; i++) set.add(+(n === 1 ? a : a + (b - a) * i / (n - 1)).toFixed(6));
  for (const v of fc.frequencies || []) set.add(+v);
  if (fc.modal_refine !== false)
    for (const f of modalF) for (const k of [0, -0.1, 0.1, -0.3, 0.3, -1, 1]) {
      const v = f * (1 + k * (fc.damping || 0.05));
      if (v >= a && v <= b) set.add(+v.toFixed(6));
    }
  return [...set].filter(v => v >= a && v <= b).sort((p, q) => p - q);
}
function interpFn(fn, f) {
  if (!fn) return 1;
  const p = fn.points || [];
  if (!p.length || f < p[0][0] || f > p[p.length - 1][0]) return 0;
  for (let i = 1; i < p.length; i++)
    if (f <= p[i][0]) {
      const t = (f - p[i - 1][0]) / ((p[i][0] - p[i - 1][0]) || 1);
      return p[i - 1][1] + t * (p[i][1] - p[i - 1][1]);
    }
  return p[p.length - 1][1];
}
const trapz = (y, x) => y.reduce((a, v, i) => i ? a + 0.5 * (v + y[i - 1]) * (x[i] - x[i - 1]) : 0, 0);

/** Complex modal response helper for the mock frequency-domain cases. */
function freqResponse(model, r, fc, kind) {
  const ffns = model.frequency_functions || {};
  const stories = model.stories || [];
  const H = stories.length ? stories[stories.length - 1].elevation : 1;
  const part = (r.modal || {}).participation || [];
  const periods = (r.modal || {}).periods || [];
  const load = (fc.loads || [])[0] || { direction: "UX", scale: 1 };
  const dirX = (load.direction || "UX") !== "UY";
  const modes = periods.map((T, i) => ({ T, w: 2 * Math.PI / T, p: part[i] || {} }))
    .filter(md => (dirX ? md.p.ux : md.p.uy) > 0.001);
  const modalF = periods.map(T => 1 / T);
  const fr = freqGrid(fc, modalF);
  const zeta = fc.damping || 0.05;
  const hyst = fc.damping_type === "hysteretic";
  const Mtot = Object.values(model.story_masses || {}).reduce((a, v) => a + v, 0) ||
    stories.length * 100;
  const fn = ffns[load.function];
  // per frequency complex roof displacement (relative), base shear, phase
  const out = fr.map(f => {
    const w = 2 * Math.PI * f;
    const amp = kind === "psd" ? (load.scale || 1) * Math.sqrt(Math.max(interpFn(fn, f), 0))
      : (load.scale || 1) * (fn ? interpFn(fn, f) : 1);
    let re = 0, im = 0, vre = 0, vim = 0;
    for (const md of modes) {
      const gam = dirX ? (md.p.gamma_x || 1) : (md.p.gamma_y || 1);
      const dr = hyst ? md.w * md.w - w * w : md.w * md.w - w * w;
      const di = hyst ? 2 * zeta * md.w * md.w : 2 * zeta * md.w * w;
      const den = dr * dr + di * di || 1e-12;
      const hr = dr / den, hi = -di / den;               // H = 1/(dr + i di)
      const c = -gam * amp;                              // accel load -Γ ag
      re += c * hr; im += c * hi;
      const mfrac = dirX ? md.p.ux : md.p.uy;
      vre += -mfrac * Mtot * md.w * md.w * hr * amp;     // modal base shear
      vim += -mfrac * Mtot * md.w * md.w * hi * amp;
    }
    return { f, re, im, vre, vim };
  });
  return { fr, out, dirX, modalF, modes, H, stories, zeta, hyst };
}
const cAmp = (re, im) => Math.hypot(re, im);
const cPh = (re, im) => Math.atan2(im, re) * 180 / Math.PI;

function roofTag(r, model) {
  const st = model.stories || [];
  if (!st.length) return null;
  const zr = st[st.length - 1].elevation;
  const g = model.grid || {};
  const x1 = (g.x_lines || [0]).slice(-1)[0], y0 = (g.y_lines || [0])[0];
  let best = null, bd = Infinity;
  for (const [t, p] of Object.entries(r.nodes || {})) {
    if (Math.abs(p[2] - zr) > 1e-6) continue;
    const d = Math.hypot(p[0] - x1, p[1] - y0);
    if (d < bd) { bd = d; best = t; }
  }
  return best;
}

function mockSteadyState(model, r, name, fc) {
  const R = freqResponse(model, r, fc, "ss");
  const { fr, out, dirX, stories, H } = R;
  const sway = z => Math.pow(Math.max(z, 0) / H, 1.25);
  const dofI = dirX ? 0 : 1;
  const node_disp = {};
  const outTags = [];
  for (const p of fc.output_points || []) {
    let best = null, bd = Infinity;
    for (const [t, q] of Object.entries(r.nodes || {})) {
      const d = Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]);
      if (d < bd) { bd = d; best = t; }
    }
    if (best && bd < 0.01) outTags.push(best);
  }
  const zero = () => [0, 0, 0, 0, 0, 0];
  for (const t of outTags) {
    const s = sway(r.nodes[t][2]);
    node_disp[t] = {
      amp: out.map(o => { const a = zero(); a[dofI] = cAmp(o.re, o.im) * s; a[dirX ? 4 : 3] = cAmp(o.re, o.im) * s / H * 0.8; return a; }),
      phase_deg: out.map(o => { const a = zero(); a[dofI] = cPh(o.re, o.im); a[dirX ? 4 : 3] = cPh(o.re, o.im); return a; }),
    };
  }
  const base = {};
  for (const k of ["FX", "FY", "FZ", "MX", "MY", "MZ"]) {
    const on = (dirX && (k === "FX" || k === "MY")) || (!dirX && (k === "FY" || k === "MX"));
    const lev = k[0] === "M" ? 0.7 * H : 1;
    base[k] = { amp: out.map(o => on ? cAmp(o.vre, o.vim) * lev : 0), phase_deg: out.map(o => on ? cPh(o.vre, o.vim) : 0) };
  }
  const story = {};
  stories.forEach((s, i) => {
    const zb = i ? stories[i - 1].elevation : 0;
    const su = sway(s.elevation), sd = (sway(s.elevation) - sway(zb)) / ((s.elevation - zb) || 1);
    const blk = (fac, on) => ({ amp: out.map(o => on ? cAmp(o.re, o.im) * fac : 0), phase_deg: out.map(o => on ? cPh(o.re, o.im) : 0) });
    story[s.name] = { ux: blk(su, dirX), uy: blk(su, !dirX), drift_x: blk(sd, dirX), drift_y: blk(sd, !dirX) };
  });
  const argmax = a => a.reduce((b, v, i) => v > a[b] ? i : b, 0);
  const peaks = { node_disp: {}, node_disp_freq_hz: {}, reactions: {}, base: {}, base_freq_hz: {}, story: {} };
  const allTags = Object.keys(r.nodes || {});
  const roofAmp = out.map(o => cAmp(o.re, o.im));
  const ip = argmax(roofAmp);
  for (const t of allTags) {
    const s = sway(r.nodes[t][2]);
    const a = zero(); a[dofI] = roofAmp[ip] * s;
    const ff = zero(); ff[dofI] = fr[ip];
    peaks.node_disp[t] = a; peaks.node_disp_freq_hz[t] = ff;
  }
  const nSup = (r.supports || []).length || 1;
  for (const t of r.supports || []) {
    const a = zero(); a[dofI] = Math.max(...base[dirX ? "FX" : "FY"].amp) / nSup;
    peaks.reactions[t] = a;
  }
  for (const k of Object.keys(base)) {
    const i = argmax(base[k].amp);
    peaks.base[k] = base[k].amp[i]; peaks.base_freq_hz[k] = fr[i];
  }
  for (const [s, b] of Object.entries(story))
    peaks.story[s] = Object.fromEntries(Object.entries(b).map(([k, v]) => [k, Math.max(...v.amp)]));
  return { case: name, type: "steady_state", method: fc.method || "modal",
    damping: { type: fc.damping_type || "modal", ratio: fc.damping || 0.05 },
    modes_used: R.modes.length, modal_frequencies_hz: R.modalF, frequencies_hz: fr,
    node_disp, base, story, peaks, warnings: [] };
}

function mockPsd(model, r, name, fc) {
  const R = freqResponse(model, r, fc, "psd");
  const { fr, out, dirX, stories, H } = R;
  const sway = z => Math.pow(Math.max(z, 0) / H, 1.25);
  const dofI = dirX ? 0 : 1;
  const sq = out.map(o => o.re * o.re + o.im * o.im);
  const vsq = out.map(o => o.vre * o.vre + o.vim * o.vim);
  const rmsU = Math.sqrt(trapz(sq, fr)), rmsV = Math.sqrt(trapz(vsq, fr));
  const zero = () => [0, 0, 0, 0, 0, 0];
  const rms = { node_disp: {}, reactions: {}, base: {}, story: {} };
  for (const [t, p] of Object.entries(r.nodes || {})) {
    const a = zero(); a[dofI] = rmsU * sway(p[2]); a[dirX ? 4 : 3] = rmsU * sway(p[2]) / H * 0.8;
    rms.node_disp[t] = a;
  }
  const nSup = (r.supports || []).length || 1;
  for (const t of r.supports || []) { const a = zero(); a[dofI] = rmsV / nSup; a[2] = rmsV * 0.05; rms.reactions[t] = a; }
  for (const k of ["FX", "FY", "FZ", "MX", "MY", "MZ"]) {
    const on = (dirX && (k === "FX" || k === "MY")) || (!dirX && (k === "FY" || k === "MX"));
    rms.base[k] = on ? rmsV * (k[0] === "M" ? 0.7 * H : 1) : 0;
  }
  const psd = { node_disp: {}, base: {}, story: {} };
  for (const p of fc.output_points || []) {
    let best = null, bd = Infinity;
    for (const [t, q] of Object.entries(r.nodes || {})) {
      const d = Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]);
      if (d < bd) { bd = d; best = t; }
    }
    if (!best || bd > 0.01) continue;
    const s2 = sway(r.nodes[best][2]) ** 2;
    psd.node_disp[best] = sq.map(v => { const a = zero(); a[dofI] = v * s2; a[dirX ? 4 : 3] = v * s2 / (H * H) * 0.64; return a; });
  }
  for (const k of ["FX", "FY", "FZ", "MX", "MY", "MZ"]) {
    const on = (dirX && (k === "FX" || k === "MY")) || (!dirX && (k === "FY" || k === "MX"));
    const lev = k[0] === "M" ? (0.7 * H) ** 2 : 1;
    psd.base[k] = vsq.map(v => on ? v * lev : 0);
  }
  stories.forEach((s, i) => {
    const zb = i ? stories[i - 1].elevation : 0;
    const su = sway(s.elevation), sd = (sway(s.elevation) - sway(zb)) / ((s.elevation - zb) || 1);
    rms.story[s.name] = { ux: dirX ? rmsU * su : 0, uy: dirX ? 0 : rmsU * su,
      drift_x: dirX ? rmsU * sd : 0, drift_y: dirX ? 0 : rmsU * sd };
    psd.story[s.name] = { ux: sq.map(v => dirX ? v * su * su : 0), uy: sq.map(v => dirX ? 0 : v * su * su),
      drift_x: sq.map(v => dirX ? v * sd * sd : 0), drift_y: sq.map(v => dirX ? 0 : v * sd * sd) };
  });
  return { case: name, type: "psd", method: fc.method || "modal",
    damping: { type: fc.damping_type || "modal", ratio: fc.damping || 0.05 },
    modes_used: R.modes.length, modal_frequencies_hz: R.modalF, frequencies_hz: fr,
    correlation: "full", rms, psd,
    warnings: fr[0] > 0 ? [`area below ${fr[0]} Hz (freq_start_hz) is not counted in the RMS`] : [] };
}

function mockEnergy(model, rec, tc) {
  const t = rec.t || [];
  const n = t.length;
  if (n < 3) return null;
  const roof = Object.values(rec.story_ux || {}).slice(-1)[0] || [];
  const roofY = Object.values(rec.story_uy || {}).slice(-1)[0] || [];
  const dirX = tc.direction !== "Y";
  const u = dirX ? roof : roofY;
  const Mtot = Object.values(model.story_masses || {}).reduce((a, v) => a + v, 0) || 400;
  const Meff = 0.82 * Mtot;
  const dt = t[1] - t[0];
  // effective SDOF stiffness from the peak base shear / roof displacement
  const V = dirX ? rec.base_FX : rec.base_FY;
  let k = 0;
  { let num = 0, den = 0; for (let i = 0; i < n; i++) { num += (V[i] || 0) * (u[i] || 0); den += (u[i] || 0) ** 2; } k = den > 1e-18 ? Math.abs(num / den) : 1e4; }
  const zeta = isFinite(tc.damping) ? tc.damping : 0.05;
  const c = 2 * zeta * Math.sqrt(k * Meff);
  const nl = !!tc.nonlinear;
  const v = u.map((_, i) => i === 0 ? 0 : i === n - 1 ? (u[i] - u[i - 1]) / dt : (u[i + 1] - u[i - 1]) / (2 * dt));
  const kinetic = [], strain = [], damping = [], hysteretic = [], input = [], error = [];
  let ed = 0, eh = 0;
  for (let i = 0; i < n; i++) {
    if (i) {
      ed += c * 0.5 * (v[i] ** 2 + v[i - 1] ** 2) * dt;
      if (nl) eh += 0.12 * Math.abs(k * 0.5 * (u[i] + u[i - 1]) * (u[i] - u[i - 1])) * (Math.abs(u[i]) > 0.6 * Math.max(...u.map(Math.abs)) ? 1 : 0);
    }
    const ek = 0.5 * Meff * v[i] ** 2, es = 0.5 * k * u[i] ** 2;
    kinetic.push(ek); strain.push(es); damping.push(ed); hysteretic.push(eh);
    const e = 1e-4 * Math.sin(i * 0.37) * (ek + es + ed + eh);
    error.push(e);
    input.push(ek + es + ed + eh + e);
  }
  const emax = Math.max(...input.map(Math.abs), 1e-12);
  const en = error.map(e => e / emax);
  const r6 = a => a.map(x => +x.toPrecision(8));
  return { t: t.slice(), input: r6(input), kinetic: r6(kinetic), strain: r6(strain), damping: r6(damping),
    hysteretic: r6(hysteretic), error: r6(error), error_normalized: r6(en),
    max_abs_error_normalized: Math.max(...en.map(Math.abs)), normalization: "run maximum energy",
    units: "kN*m", method: "nodal work (relative frame)" };
}

function mockPushoverExtras(model, r, name, pc, po) {
  const stories = model.stories || [];
  const H = stories.length ? stories[stories.length - 1].elevation : 1;
  const dist = pc.load_distribution || "roof_point";
  const masses = model.story_masses || {};
  const per = (r.modal || {}).periods || [];
  const part = (r.modal || {}).participation || [];
  const dirX = (pc.direction || "X") !== "Y";
  let w = stories.map(() => 0), params = {};
  if (dist === "roof_point") { if (stories.length) w[w.length - 1] = 1; }
  else if (dist === "uniform_accel") w = stories.map(s => masses[s.name] || 100);
  else if (dist === "triangular") {
    const k = pc.k || (per[0] <= 0.5 ? 1 : per[0] >= 2.5 ? 2 : 1 + (per[0] - 0.5) / 2);
    w = stories.map(s => (masses[s.name] || 100) * Math.pow(s.elevation, k));
    params = pc.k ? { k } : { k, period: per[0], mode_number: 1 };
  } else if (dist === "pattern") {
    w = stories.map(s => s.elevation); params = { pattern: pc.pattern || "" };
  } else {   // mode
    let im = pc.mode_number ? pc.mode_number - 1 : 0;
    if (!pc.mode_number) part.forEach((p, i) => { if ((dirX ? p.ux : p.uy) > (dirX ? part[im].ux : part[im].uy)) im = i; });
    const order = Math.ceil((im + 1) / 3);
    w = stories.map(s => (masses[s.name] || 100) * Math.sin((2 * order - 1) * Math.PI * s.elevation / (2 * H)));
    params = { mode_number: im + 1, period: per[im] || null, mass_ratio: dirX ? (part[im] || {}).ux : (part[im] || {}).uy };
  }
  const tot = w.reduce((a, b) => a + b, 0) || 1;
  const story_forces = Object.fromEntries(stories.map((s, i) => [s.name, w[i] / tot]));
  const tag = roofTag(r, model);
  po.capacity_curve = { node: tag != null ? +tag : null, dof: pc.control_dof || (dirX ? "UX" : "UY"),
    mode: pc.control_mode || "displacement_control", height: H, start_from: pc.start_from || null,
    disp: (po.roof_disp || []).slice(), base_shear: (po.base_shear || []).slice() };
  po.distribution = { type: dist, story_forces,
    normalization: "sum of applied push forces = 1 (base shear = lambda * reference_base_shear)",
    reference_base_shear: 1.0, params };
}

/** Adds steady_state / psd blocks, TH energy and pushover extras (mutates r). */
export function augmentMockResults(model, r) {
  if (!model || !r) return r;
  const notRun = new Set(Array.isArray(model.cases_not_run) ? model.cases_not_run : []);
  const modalOk = ((r.modal || {}).periods || []).length > 0;
  const cs = r.case_status || (r.case_status = {});
  for (const [kind, key, fn] of [["steady_state_cases", "steady_state", mockSteadyState],
                                 ["psd_cases", "psd", mockPsd]]) {
    const blk = r[key] || {};                     // never clobber blocks another mock already made
    for (const [n, fc] of Object.entries(model[kind] || {})) {
      if (notRun.has(n)) { cs[n] = "not_run"; delete blk[n]; continue; }
      if (!modalOk) { cs[n] = "failed"; continue; }
      if (!blk[n]) blk[n] = fn(model, r, n, fc);
      cs[n] = cs[n] && cs[n] !== "not_run" ? cs[n] : "finished";
    }
    if (Object.keys(blk).length) r[key] = blk;
  }
  for (const [n, rec] of Object.entries(r.th_cases || {})) {
    const tc = (model.th_cases || {})[n];
    if (tc && tc.energy && !rec.energy) { const e = mockEnergy(model, rec, tc); if (e) rec.energy = e; }
  }
  for (const [n, po] of Object.entries(r.pushover || {})) {
    const pc = (model.pushover_cases || {})[n];
    if (pc && !po.capacity_curve) mockPushoverExtras(model, r, n, pc, po);
  }
  return r;
}
