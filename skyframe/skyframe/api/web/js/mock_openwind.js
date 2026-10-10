/* SkyFrame — mock (?mock=1 / offline) open-structure wind + run log.

   Client-side stand-ins for (CONTRACT "Open structure wind, model info, run
   log and report data"):
     POST /api/pattern/open-wind/preview   owMockCompute(model, params)
     POST /api/pattern/open-wind           owMockGenerate(model, body)
     POST /api/model (validation)          owMockValidate(payload)
     GET  /api/analyze/log                 owMockRunLog(model, results, ms)
     GET  /api/model/info                  owModelInfo(model)   (pure counts)
   Same formulas as skyframe/core/openwind.py (ASCE 7-16 / 7-22 Kz, Ke). */

export const OW_DEFAULT = { include: true, cf: null, width: "auto", shielding: 1.0 };
export const OW_DIRECTIONS = { X: 0, Y: 90, "-X": 180, "-Y": 270 };
const FT = 0.3048;
const E16 = { B: [7.0, 365.76], C: [9.5, 274.32], D: [11.5, 213.36] };
const E22 = { B: [7.5, 3280 * FT], C: [9.8, 2460 * FT], D: [11.5, 1935 * FT] };

const isNum = v => typeof v === "number" && isFinite(v);

export function owKz(code, z, exposure) {
  if (code === "asce7_16") {
    const [a, zg] = E16[exposure];
    return 2.01 * (Math.max(z, 4.6) / zg) ** (2 / a);
  }
  const [a, zg] = E22[exposure];
  return 2.41 * (Math.min(Math.max(z, 15 * FT), zg) / zg) ** (2 / a);
}
export const owKe = ze => Math.exp(-0.000119 * ze);
export function owTowerCf(e, shape) {
  return shape === "triangle" ? 3.4 * e * e - 4.7 * e + 3.4 : 4.0 * e * e - 5.9 * e + 4.0;
}

/** Validate one open_wind dict; returns an error string or "". */
export function owParamError(p) {
  if (p == null) return "";
  if (typeof p !== "object" || Array.isArray(p)) return "open_wind must be an object or null";
  const bad = Object.keys(p).filter(k => !(k in OW_DEFAULT));
  if (bad.length) return `open_wind: unknown key(s) ${bad.join(", ")}`;
  if ("include" in p && typeof p.include !== "boolean") return "open_wind.include must be true/false";
  if (p.cf != null && !(isNum(p.cf) && p.cf > 0)) return "open_wind.cf must be > 0";
  if ("width" in p && p.width !== "auto" && !(isNum(p.width) && p.width > 0)) return "open_wind.width must be \"auto\" or > 0";
  if ("shielding" in p && !(isNum(p.shielding) && p.shielding > 0 && p.shielding <= 1)) return "open_wind.shielding must be in (0, 1]";
  return "";
}
export function owMockValidate(payload) {
  for (const m of (payload && payload.members) || []) {
    const e = owParamError(m && m.open_wind);
    if (e) return `Member ${m.uid}: ${e}`;
  }
  return "";
}
/** Canonical (backend-normalised) form, keys in the order the backend echo
    carries them (Flask sorts JSON keys) so a save round-trips byte-identically. */
export function owNormalize(p) {
  if (p == null) return null;
  return {
    cf: p.cf == null ? null : +p.cf,
    include: p.include !== false,
    shielding: p.shielding == null ? 1.0 : +p.shielding,
    width: p.width == null || p.width === "auto" ? "auto" : +p.width,
  };
}

function exposedWidth(model, m, p) {
  if (p.width !== "auto") return +p.width;
  const s = (model.sections || {})[m.section] || {};
  if (s.h > 0) return +s.h;
  if (s.b > 0) return +s.b;
  throw new Error(`member '${m.uid}': section '${m.section}' has no depth - give an explicit open_wind width`);
}

export function owMockCompute(model, params) {
  const P = Object.assign({ code: "asce7_22", exposure: "C", direction: "X", angle: null, Kzt: 1, Kd: 0.85,
    ze: 0, Ke: null, G: 0.85, cf: 2.0, members: "assigned", segments: 4, z_ground: 0, tower: null }, params || {});
  if (!(isNum(P.V) && P.V > 0)) throw new Error("V must be a finite number > 0");
  if (!["asce7_16", "asce7_22"].includes(P.code)) throw new Error("code must be asce7_16 | asce7_22");
  if (!["B", "C", "D"].includes(P.exposure)) throw new Error("exposure must be B | C | D");
  const Ke = P.Ke == null ? owKe(+P.ze || 0) : +P.Ke;
  let cf = +P.cf;
  if (P.tower) cf = owTowerCf(+P.tower.solidity, P.tower.shape || "square");
  const ang = P.angle == null ? OW_DIRECTIONS[P.direction] : +P.angle;
  if (ang == null || !isFinite(ang)) throw new Error("direction must be X | Y | -X | -Y");
  const rad = ang * Math.PI / 180;
  let dx = Math.cos(rad), dy = Math.sin(rad);
  if (Math.abs(dx) < 1e-12) dx = 0;
  if (Math.abs(dy) < 1e-12) dy = 0;
  const kfac = P.code === "asce7_16" ? 1 : +P.Kd;
  const qz = z => 0.613 * owKz(P.code, z, P.exposure) * P.Kzt * Ke * P.V * P.V / 1000 * (P.code === "asce7_16" ? +P.Kd : 1);
  const sel = [];
  const list = Array.isArray(P.members) ? new Set(P.members) : null;
  for (const m of model.members || []) {
    let p = m.open_wind ? owNormalize(m.open_wind) : null;
    if (list) { if (!list.has(m.uid)) continue; p = p || { ...OW_DEFAULT }; }
    else if (!p) { if (P.members !== "all") continue; p = { ...OW_DEFAULT }; }
    if (p.include) sel.push([m, p]);
  }
  const rows = [];
  let FX = 0, FY = 0;
  for (const [m, p] of sel) {
    const L = Math.hypot(m.pj[0] - m.pi[0], m.pj[1] - m.pi[1], m.pj[2] - m.pi[2]);
    if (L <= 0) continue;
    const c = ((m.pj[0] - m.pi[0]) * dx + (m.pj[1] - m.pi[1]) * dy) / L;
    const proj = Math.sqrt(Math.max(0, 1 - c * c));
    if (proj < 1e-9) continue;
    const mcf = p.cf != null ? p.cf : cf;
    const width = exposedWidth(model, m, p);
    const k = kfac * P.G * mcf * width * p.shielding * proj;
    const zh = s => Math.max(0, m.pi[2] + s * (m.pj[2] - m.pi[2]) - P.z_ground);
    const horiz = Math.abs(m.pj[2] - m.pi[2]) <= 1e-9;
    const n = horiz ? 1 : P.segments;
    const segs = [];
    let F = 0;
    for (let i = 0; i < n; i++) {
      const a = i / n, b = (i + 1) / n;
      const wa = qz(zh(a)) * k, wb = horiz ? wa : qz(zh(b)) * k;
      segs.push([a, b, wa, wb]);
      F += 0.5 * (wa + wb) * (b - a) * L;
    }
    const qi = qz(zh(0)), qj = qz(zh(1));
    rows.push({ uid: m.uid, cf: mcf, width, shielding: p.shielding, proj, z_i: zh(0), z_j: zh(1),
      qz_i: qi, qz_j: qj, p_i: qi * kfac * P.G * mcf, p_j: qj * kfac * P.G * mcf,
      w_i: qi * k, w_j: qj * k, F, segments: segs });
    FX += F * dx; FY += F * dy;
  }
  return { code: P.code, V: P.V, exposure: P.exposure, angle: ang, d: [dx, dy], G: P.G, Kd: P.Kd,
    Kzt: P.Kzt, Ke, cf_default: cf, segments: P.segments, members: rows, FX, FY, F: Math.hypot(FX, FY) };
}

/** Write the pattern into `model` (mutates); mirrors openwind.write_pattern. */
export function owMockGenerate(model, body) {
  const { name = "OWIND", add_case = true, ...params } = body || {};
  const s = owMockCompute(model, params);
  if (!s.members.length) throw new Error("no frame member receives open-structure wind (assign Open Structure Wind Parameters or use members='all')");
  const [dx, dy] = s.d;
  const loads = [];
  for (const r of s.members)
    for (const [a, b, wa, wb] of r.segments)
      for (const [comp, dir] of [[dx, "global_x"], [dy, "global_y"]]) {
        if (comp === 0) continue;
        loads.push(wa === wb
          ? { member_uid: r.uid, kind: "udl", w: wa * comp, w2: 0, a, b, direction: dir }
          : { member_uid: r.uid, kind: "trapezoid", w: wa * comp, w2: wb * comp, a, b, direction: dir });
      }
  model.patterns = model.patterns || {};
  model.patterns[name] = { name, kind: "wind", member_udls: [], nodal_loads: [], story_forces: [],
    member_loads: loads, area_loads: [], thermal_loads: [], accidental_torsion: false, ecc: 0.05,
    self_weight_factor: 0 };
  model.cases = model.cases || {};
  if (add_case && !model.cases[name]) model.cases[name] = { name, patterns: { [name]: 1.0 }, pdelta: false };
  return s;
}

/* ------------------------------------------------ model info + run log */
export function owModelInfo(model) {
  const m = model || {};
  const kinds = {};
  for (const mm of m.members || []) kinds[mm.kind] = (kinds[mm.kind] || 0) + 1;
  const shellKinds = {};
  for (const s of m.shells || []) shellKinds[s.kind] = (shellKinds[s.kind] || 0) + 1;
  const joints = new Set();
  const k = p => p.map(v => (+v).toFixed(6)).join(",");
  for (const mm of m.members || []) { joints.add(k(mm.pi)); joints.add(k(mm.pj)); }
  for (const s of m.shells || []) for (const c of s.corners || []) joints.add(k(c));
  let nLoads = 0;
  for (const p of Object.values(m.patterns || {}))
    nLoads += (p.member_udls || []).length + (p.member_loads || []).length + (p.nodal_loads || []).length +
      (p.story_forces || []).length + (p.area_loads || []).length + (p.thermal_loads || []).length;
  const n = o => Object.keys(o || {}).length;
  const xs = [], ys = [];
  for (const mm of m.members || []) { xs.push(mm.pi[0], mm.pj[0]); ys.push(mm.pi[1], mm.pj[1]); }
  for (const s of m.shells || []) for (const c of s.corners || []) { xs.push(c[0]); ys.push(c[1]); }
  const span = a => a.length ? Math.max(...a) - Math.min(...a) : 0;
  return {
    name: m.name || "", units: "kN, m, C (SI store)", display_units: m.display_units || "kN-m",
    counts: {
      stories: (m.stories || []).length, joints: joints.size, frames: (m.members || []).length,
      shells: (m.shells || []).length, links: (m.links || []).length, materials: n(m.materials),
      frame_sections: n(m.sections), shell_sections: n(m.shell_sections), load_patterns: n(m.patterns),
      load_cases: n(m.cases), load_combos: n(m.combos), rs_cases: n(m.rs_cases), th_cases: n(m.th_cases),
      pushover_cases: n(m.pushover_cases), staged_cases: n(m.staged_cases), buckling_cases: n(m.buckling_cases),
      nonlinear_static_cases: n(m.nonlinear_static_cases), supports: (m.supports || []).length,
      spring_supports: (m.spring_supports || []).length, groups: n(m.groups),
      section_cuts: (m.section_cuts || []).length, assigned_loads: nLoads,
      open_wind_members: (m.members || []).filter(x => x.open_wind).length,
    },
    members_by_kind: kinds, shells_by_kind: shellKinds,
    height: Math.max(0, ...(m.stories || []).map(s => s.elevation || 0)),
    plan: [span(xs), span(ys)], diaphragm: m.diaphragm || "rigid", base_fixity: m.base_fixity || "fixed",
    cases_not_run: [...(m.cases_not_run || [])],
  };
}

const SPLIT = /;\s+(?=(?:combo|case|RS|TH|time|pushover|buckling|staged)\b)/;
export function owRunWarnings(results, extra = []) {
  const out = [], seen = new Set();
  const add = (source, msg) => {
    msg = String(msg || "").trim();
    const key = source + "\u0000" + msg;
    if (!msg || seen.has(key)) return;
    seen.add(key); out.push({ source, message: msg });
  };
  const r = results || {};
  if (r.warning) for (const p of String(r.warning).split(SPLIT)) add("run", p);
  for (const grp of ["cases", "combos", "rs_cases", "th_cases", "pushover", "staged"])
    for (const [name, cd] of Object.entries(r[grp] || {})) {
      if (!cd || typeof cd !== "object") continue;
      if (cd.warning) add(name, cd.warning);
      for (const x of cd.warnings || []) add(name, x);
    }
  for (const x of extra) add("engine", x);
  return out;
}

/** Plausible run log for ?mock=1: the measured client solve time split over
    the cases that ran (weights: static 1, modal 2, RS 1.5, TH 6, other 3). */
export function owMockRunLog(model, results, ms) {
  const r = results || {};
  const status = r.case_status || {};
  const kindOf = name => {
    if ((model.cases || {})[name]) return "static";
    if (name === "MODAL") return "modal";
    if ((model.rs_cases || {})[name]) return "response_spectrum";
    if ((model.th_cases || {})[name]) return "time_history";
    if ((model.pushover_cases || {})[name]) return "pushover";
    if ((model.buckling_cases || {})[name]) return "buckling";
    if ((model.staged_cases || {})[name]) return "staged";
    return "other";
  };
  const W = { static: 1, modal: 2, response_spectrum: 1.5, time_history: 6 };
  const names = [...new Set([...Object.keys(model.cases || {}), "MODAL", ...Object.keys(status)])]
    .filter(n => n !== "MODAL" || status.MODAL || (r.modal && (r.modal.periods || []).length));
  const ran = n => (status[n] || "finished") === "finished" || status[n] === "run_as_dependency";
  const total = Math.max(1, ms == null ? 500 : ms) / 1000;
  const wsum = names.filter(ran).reduce((a, n) => a + (W[kindOf(n)] || 3), 0) || 1;
  const cases = names.map(n => ({ name: n, kind: kindOf(n), status: status[n] || "finished",
    time_s: ran(n) ? +(0.8 * total * (W[kindOf(n)] || 3) / wsum).toFixed(6) : null }));
  return {
    available: true, current: true, version: 1, started: new Date().toISOString().replace(/\.\d+Z$/, "+00:00"),
    total_s: +total.toFixed(6), model_name: r.model_name || model.name || "",
    counts: { cases: Object.keys(r.cases || {}).length, combos: Object.keys(r.combos || {}).length,
      rs_cases: Object.keys(r.rs_cases || {}).length, th_cases: Object.keys(r.th_cases || {}).length,
      modes: ((r.modal || {}).periods || []).length },
    cases, combos: Object.entries(r.combo_status || {}).map(([name, st]) => ({ name, status: st })),
    warnings: owRunWarnings(r), mock: true,
  };
}

/* ------------------------------------------------ mock results for OW cases
   The offline mock engine only builds DEAD / LIVE / EQX / EQY. A static case
   whose patterns are all member-load wind patterns (the open-structure wind
   generator's output) gets a plausible result: EQX / EQY scaled so the base
   reaction balances the pattern's applied FX / FY exactly. */
function owLin(parts) {
  const out = { node_disp: {}, reactions: {}, member_forces: {}, member_stations: {}, member_deflections: {}, story: {},
    base: { FX: 0, FY: 0, FZ: 0, MX: 0, MY: 0, MZ: 0 } };
  const addArr = (dst, k, arr, f) => {
    if (!Array.isArray(arr)) return;
    if (!dst[k]) dst[k] = new Array(arr.length).fill(0);
    arr.forEach((v, i) => { dst[k][i] += f * (+v || 0); });
  };
  for (const [c, f] of parts) {
    if (!c) continue;
    for (const key of ["node_disp", "reactions", "member_forces"])
      for (const [t, a] of Object.entries(c[key] || {})) addArr(out[key], t, a, f);
    for (const k of Object.keys(out.base)) out.base[k] += f * (+(c.base || {})[k] || 0);
    for (const [s, st] of Object.entries(c.story || {})) {
      const o = out.story[s] = out.story[s] || {};
      for (const [k, v] of Object.entries(st)) if (typeof v === "number") o[k] = (o[k] || 0) + f * v;
    }
    for (const key of ["member_stations", "member_deflections"])
      for (const [u, st] of Object.entries(c[key] || {})) {
        const o = out[key][u] = out[key][u] || {};
        for (const [k, v] of Object.entries(st || {})) {
          if (!Array.isArray(v)) continue;
          if (k === "x") { o.x = v.slice(); continue; }
          addArr(o, k, v, f);
        }
      }
  }
  return out;
}
export function owAugmentMockResults(model, r) {
  if (!r || !r.cases || !model) return r;
  const pats = model.patterns || {};
  const isOw = p => p && p.kind === "wind" && (p.member_loads || []).length && !(p.story_forces || []).length;
  const len = {};
  for (const m of model.members || []) len[m.uid] = Math.hypot(m.pj[0] - m.pi[0], m.pj[1] - m.pi[1], m.pj[2] - m.pi[2]);
  for (const [name, c] of Object.entries(model.cases || {})) {
    if (r.cases[name]) continue;
    const pn = Object.keys(c.patterns || {});
    if (!pn.length || !pn.every(p => isOw(pats[p]))) continue;
    let FX = 0, FY = 0;
    for (const p of pn) for (const l of pats[p].member_loads) {
      const L = len[l.member_uid] || 0, a = l.a ?? 0, b = l.b ?? 1;
      const w = (l.kind === "trapezoid" ? 0.5 * (l.w + (l.w2 || 0)) : l.w) * (b - a) * L * c.patterns[p];
      if (l.direction === "global_x") FX += w; else if (l.direction === "global_y") FY += w;
    }
    const ex = r.cases.EQX, ey = r.cases.EQY;
    const sx = ex && ex.base && ex.base.FX ? FX / -ex.base.FX : 0;
    const sy = ey && ey.base && ey.base.FY ? FY / -ey.base.FY : 0;
    r.cases[name] = owLin([[ex, sx], [ey, sy]]);
  }
  return r;
}
