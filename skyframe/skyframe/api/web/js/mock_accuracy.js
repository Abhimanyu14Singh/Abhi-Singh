/* SkyFrame — ?mock=1 stand-ins for POST /api/analyze/mesh_study and
   GET|POST /api/accuracy_advice (CONTRACT "Mesh sensitivity study and
   accuracy advisor"). Same JSON shapes as the backend; plausible numbers:

   * mesh study — level-1 values come from the regular mock solve
     (mockResults); finer levels follow f(L) = f_inf (1 - e / L^2) with a
     quantity-dependent discretisation error e (frames are exact, shells are
     not), then the backend's Richardson / verdict rules are applied;
   * advisor — the static checks ported to the model dict (structured-mesh
     aspect / coarseness, membrane slabs, plate theory, rigid diaphragm with
     openings, cracked modifiers, mass source, frame point loads).

   Pure functions; the model is never modified. */

import { mockResults as accMockResults } from "./mock.js";

export const ACC_QUANTITIES = ["roof_disp", "periods", "base_reactions", "max_shell_moment", "max_frame_moment"];
const MODAL = "MODAL";
const ORDER_RANGE = [0.5, 6.0];

/* ------------------------------------------------ Richardson (port) */
function orderResidual(p, h, r) {
  return (h[0] ** p - h[1] ** p) / (h[1] ** p - h[2] ** p) - r;
}
function observedOrder(h, f) {
  const d1 = f[0] - f[1], d2 = f[1] - f[2];
  if (d1 === 0 || d2 === 0 || d1 * d2 < 0) return null;
  const ratio = d1 / d2;
  let [lo, hi] = ORDER_RANGE;
  let glo = orderResidual(lo, h, ratio);
  const ghi = orderResidual(hi, h, ratio);
  if (glo * ghi > 0) return null;
  for (let i = 0; i < 200; i++) {
    const mid = 0.5 * (lo + hi);
    const g = orderResidual(mid, h, ratio);
    if (g === 0) return mid;
    if ((g < 0) === (glo < 0)) { lo = mid; glo = g; } else hi = mid;
  }
  return 0.5 * (lo + hi);
}
export function accRichardson(levels, values) {
  const h = levels.map(l => 1 / l);
  const f = values.map(Number);
  const n = f.length;
  if (f.some(v => !isFinite(v))) return { estimate: null, order: null, order_assumed: false };
  if (f[n - 1] === f[n - 2]) return { estimate: f[n - 1], order: null, order_assumed: false };
  let p = n >= 3 ? observedOrder(h.slice(-3), f.slice(-3)) : null, assumed = false;
  if (p == null) { p = 2; assumed = true; }
  const r = h[n - 2] / h[n - 1];
  return { estimate: f[n - 1] + (f[n - 1] - f[n - 2]) / (r ** p - 1), order: p, order_assumed: assumed };
}
function fmtPct(x) {
  if (x < 0.005) return "<0.01%";
  const scale = 10 ** (Math.floor(Math.log10(x)) - 1);
  const v = Math.ceil(x / scale - 1e-9) * scale;
  return `${+v.toPrecision(2)}%`;
}
export function accMakeRow(caseName, quantity, key, label, dim, levels, values, tol) {
  const vals = values.map(Number);
  const scale = 1e-12 * Math.max(0, ...vals.map(Math.abs));
  const rel = vals.map((v, k) => k === 0 ? null
    : (Math.max(Math.abs(v), scale) === 0 ? null : (v - vals[k - 1]) / Math.max(Math.abs(v), scale) * 100));
  const rich = accRichardson(levels, vals);
  let err = null, ok = false, verdict = "not converged (non-finite value)";
  if (rich.estimate != null) {
    const den = Math.max(Math.abs(rich.estimate), scale);
    err = den === 0 ? 0 : Math.abs(vals[vals.length - 1] - rich.estimate) / den * 100;
    ok = err <= tol;
    verdict = ok ? `converged within ${fmtPct(err)}` : `not converged (est. error ${fmtPct(err)} > ${fmtPct(tol)})`;
  }
  return { case: caseName, quantity, key, label, dim, values: vals, rel_change_pct: rel,
    richardson: rich.estimate, order: rich.order, order_assumed: rich.order_assumed,
    est_error_pct: err, converged: ok, verdict };
}

/* ------------------------------------------------ mesh study */
const isLinearStatic = c => !(c && (c.pdelta || (c.geometric && c.geometric !== "linear")));
export function accDefaultCases(model) {
  const off = new Set(model.cases_not_run || []);
  const out = Object.entries(model.cases || {}).filter(([n, c]) => isLinearStatic(c) && !off.has(n)).map(([n]) => n);
  if ((model.num_modes ?? 6) > 0 && !off.has(MODAL) && !(MODAL in (model.cases || {}))) out.push(MODAL);
  return out;
}

const bad = msg => { const e = new Error(msg); e.status = 400; throw e; };

function structuredDivs(r) {
  const c = r.corners || [];
  if (c.length !== 4) return null;
  const d = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
  const lx = 0.5 * (d(c[0], c[1]) + d(c[3], c[2])), ly = 0.5 * (d(c[0], c[3]) + d(c[1], c[2]));
  const md = r.mesh_divisions;
  if (md && md.n1) return { nx: md.n1, ny: md.n2, lx, ly };
  const h = md && md.max_size ? md.max_size : (r.mesh_size || 1);
  const f = md && md.max_size ? (v => Math.max(1, Math.ceil(v / h - 1e-9))) : (v => Math.max(1, Math.round(v / h)));
  return { nx: f(lx), ny: f(ly), lx, ly };
}

export function mockMeshStudy(model, body = {}) {
  const levels = [...new Set((body.levels ?? [1, 2, 4]).map(Number))].sort((a, b) => a - b);
  if (levels.length < 2) bad("'levels' needs at least 2 distinct refinement factors");
  if (levels.some(l => !Number.isInteger(l) || l < 1 || l > 16)) bad("'levels' entries must be integers in [1, 16]");
  if (levels.length > 6) bad("'levels' allows at most 6 factors");
  const quantities = body.quantities ? ACC_QUANTITIES.filter(q => body.quantities.includes(q)) : [...ACC_QUANTITIES];
  if (!quantities.length) bad("'quantities' must be a non-empty list");
  const tol = body.tolerance_pct ?? 5;
  if (!(tol > 0 && tol <= 100)) bad("'tolerance_pct' must be a number in (0, 100]");
  const cases = body.cases ? [...new Set(body.cases)] : accDefaultCases(model);
  if (!cases.length) bad("the model has no linear static or modal case to study");
  for (const c of cases) {
    if (c === MODAL && !(MODAL in (model.cases || {}))) { if (!((model.num_modes ?? 6) > 0)) bad("MODAL: the model requests no modes (num_modes = 0)"); }
    else if (!(model.cases || {})[c]) bad(`unknown load case '${c}'`);
    else if (!isLinearStatic(model.cases[c])) bad(`case '${c}' is not a linear static case`);
  }
  const res = accMockResults(model);
  const shells = (model.shells || []).filter(s => s.behavior === "shell");
  const nShell1 = shells.reduce((a, s) => { const d = structuredDivs(s); return a + (d ? d.nx * d.ny : Math.ceil((s.corners || []).length * 2)); }, 0);
  const nSeg1 = (model.members || []).length;
  const hasShell = shells.length > 0;
  // discretisation error of the level-1 mesh (plausible, mesh-size driven)
  const coarse = hasShell ? Math.min(0.25, 0.02 * Math.max(...shells.map(s => s.mesh_size || 1)) ** 2 + 0.03) : 0;
  const ERR = { roof_disp: coarse, max_shell_moment: 2 * coarse, periods: 0.5 * coarse, base: 0, max_frame_moment: 0.3 * coarse };
  const seq = (v1, e) => levels.map(L => e === 0 ? v1 : v1 / (1 - e) * (1 - e / (L * L) * (levels[0] ** 2)));
  const rows = [];
  const COMP = ["UX", "UY", "UZ"];
  const nodes = res.nodes || {};
  for (const c of cases) {
    if (c === MODAL && !(MODAL in (model.cases || {}))) {
      if (!quantities.includes("periods")) continue;
      const per = ((res.modal || {}).periods || []).slice(0, Math.min(3, model.num_modes ?? 6));
      per.forEach((T, i) => rows.push(accMakeRow(c, "periods", `T${i + 1}`, `Period T${i + 1}`, "time", levels, seq(T, ERR.periods), tol)));
      continue;
    }
    const cr = (res.cases || {})[c];
    if (!cr) continue;
    if (quantities.includes("roof_disp")) {
      const zmax = Math.max(...Object.values(nodes).map(p => p[2]));
      let best = null;
      for (const [t, p] of Object.entries(nodes)) {
        if (Math.abs(p[2] - zmax) > 1e-6) continue;
        const d = (cr.node_disp || {})[t];
        if (!d) continue;
        for (let k = 0; k < 3; k++) if (!best || Math.abs(d[k]) > Math.abs(best.v)) best = { k, v: d[k], p };
      }
      if (best) {
        const row = accMakeRow(c, "roof_disp", `roof_disp:${best.k}`, `Roof displacement ${COMP[best.k]}`, "length", levels, seq(best.v, ERR.roof_disp), tol);
        row.point = best.p.slice();
        rows.push(row);
      }
    }
    if (quantities.includes("base_reactions")) {
      const b = cr.base || {};
      const fmax = Math.max(0, ...["FX", "FY", "FZ"].map(k => Math.abs(b[k] || 0)));
      const mmax = Math.max(0, ...["MX", "MY", "MZ"].map(k => Math.abs(b[k] || 0)));
      for (const k of ["FX", "FY", "FZ", "MX", "MY", "MZ"]) {
        const big = k[0] === "F" ? fmax : mmax, v = b[k] || 0;
        if (!big || Math.abs(v) <= 1e-6 * big) continue;
        rows.push(accMakeRow(c, "base_reactions", `base:${k}`, `Base reaction ${k}`, k[0] === "F" ? "force" : "moment", levels, seq(v, ERR.base), tol));
      }
    }
    if (quantities.includes("max_shell_moment") && hasShell) {
      const sf = Object.values(cr.shell_forces || {});
      const v = sf.length ? Math.max(...sf.map(f => Math.max(Math.abs(f[3]), Math.abs(f[4])))) : 0;
      if (sf.length) rows.push(accMakeRow(c, "max_shell_moment", "max_shell_moment", "Max shell moment |Mxx|,|Myy|", "moment_per_length", levels, seq(v, ERR.max_shell_moment), tol));
    }
    if (quantities.includes("max_frame_moment")) {
      let v = 0, any = false;
      for (const st of Object.values(cr.member_stations || {}))
        for (const k of ["M2", "M3"]) for (const x of st[k] || []) { any = true; v = Math.max(v, Math.abs(x)); }
      if (any) rows.push(accMakeRow(c, "max_frame_moment", "max_frame_moment", "Max frame moment |M2|,|M3|", "moment", levels, seq(v, ERR.max_frame_moment), tol));
    }
  }
  const nConv = rows.filter(r => r.converged).length;
  const errs = rows.map(r => r.est_error_pct).filter(v => v != null);
  return {
    levels, cases, quantities, tolerance_pct: tol,
    mesh: levels.map(L => ({ level: L, n_nodes: Math.round((nShell1 + nSeg1) * L * L * 1.1 + 4), n_shell_elements: nShell1 * L * L,
      n_frame_segments: nSeg1 * L, elapsed_s: +(0.02 * L * L).toFixed(4) })),
    rows,
    summary: { n_rows: rows.length, n_converged: nConv, all_converged: rows.length > 0 && nConv === rows.length,
      max_est_error_pct: errs.length ? Math.max(...errs) : null },
    warnings: [],
  };
}

/* ------------------------------------------------ advisor */
export const ACC_DEFAULTS = { aspect_max: 4, warp_max: 0.01, min_elements_per_span: 4, thick_ratio: 0.1, thin_ratio: 0.02, opening_ratio: 0.25 };
const TEXT = {
  SHELL_ASPECT_RATIO: ["warning", "Shell elements with a high aspect ratio",
    "Elongated elements stiffen artificially and interpolate stresses poorly along their long side, so displacements are under-estimated and moments are smeared.",
    "Set Assign > Shell > Auto Mesh divisions (n1 x n2) or a maximum element size so that the element sides are within 1:{aspect_max}."],
  SHELL_MESH_COARSE: ["warning", "Shell mesh coarse relative to the span",
    "With fewer than {min_elements_per_span} elements across a span the deflected shape and the moment distribution are not resolved.",
    "Reduce the mesh size to at most span / {min_elements_per_span} and confirm with Analyze > Mesh Sensitivity Study."],
  MEMBRANE_SLAB_BENDING: ["warning", "Membrane slab where out-of-plane bending matters",
    "A membrane slab has no plate bending stiffness: loads are passed to beams / corner joints by tributary rules, so slab deflections and moments are not computed.",
    "Use behavior 'shell' (or shell_type shell / shell_thick) for slabs without beams on every edge and for any shell carrying out-of-plane loads."],
  PLATE_THIN_ON_THICK: ["warning", "Thin-plate (Kirchhoff) element on a thick slab",
    "Kirchhoff elements ignore transverse-shear deformation; for t / span > {thick_ratio} it is significant.",
    "Use shell_type shell_thick (Mindlin) or the default shell for thick slabs, walls and mats."],
  PLATE_THICK_ON_THIN: ["info", "Thick-plate (Mindlin) element on a very thin slab",
    "For t / span < {thin_ratio} shear deformation is negligible; thick elements are more sensitive to mesh distortion.",
    "Keep shell_thick and refine the mesh, or use shell_thin for thin plates."],
  RIGID_DIAPHRAGM_OPENINGS: ["warning", "Rigid diaphragm assumed on a slab with large openings",
    "A rigid diaphragm forces every slab joint to move together in plan; large openings make the floor flexible in-plane.",
    "Assign a semi-rigid diaphragm to that floor."],
  CRACKED_MODIFIERS_MISSING: ["warning", "No cracked-section modifiers on concrete",
    "Gross concrete stiffness over-estimates lateral and flexural stiffness: periods short, drifts and deflections low.",
    "Assign property modifiers, e.g. ACI 318 6.6.3.1.1: beams 0.35 Ig, columns 0.70 Ig, walls 0.35-0.70 Ig, flat slabs 0.25 Ig."],
  MASS_SOURCE_MISSING: ["warning", "Mass source missing",
    "Modal, response-spectrum and time-history analyses need mass; without it no modes are found.",
    "Define > Mass Source: include the self-weight / dead patterns and the seismic share of live load, or add story / joint masses."],
  FRAME_POINT_LOAD_NO_AUTOMESH: ["warning", "Frame with span point loads attached to shells without auto mesh",
    "The member is connected to the shell only at its end joints; a concentrated span load reaches the shell only through them.",
    "Assign > Frame > Auto Mesh: enable 'at intermediate joints' / 'at intersections' and a maximum segment length."],
};
const CODES = Object.keys(TEXT);
const fill = (s, th) => s.replace(/\{(\w+)\}/g, (_, k) => String(th[k]));

const vsub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const vlen = a => Math.hypot(a[0], a[1], a[2]);
const vdot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const vcross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const vunit = a => { const n = vlen(a); return n ? a.map(v => v / n) : [0, 0, 0]; };
function regionSpan(r) {
  const c = r.corners;
  let n = [0, 0, 0];
  for (let i = 0; i < c.length; i++) {
    const p = c[i], q = c[(i + 1) % c.length];
    n = [n[0] + (p[1] - q[1]) * (p[2] + q[2]), n[1] + (p[2] - q[2]) * (p[0] + q[0]), n[2] + (p[0] - q[0]) * (p[1] + q[1])];
  }
  n = vunit(n);
  const u = vunit(vsub(c[1], c[0])), v = vunit(vcross(n, u));
  const us = c.map(p => vdot(vsub(p, c[0]), u)), vs = c.map(p => vdot(vsub(p, c[0]), v));
  return Math.min(Math.max(...us) - Math.min(...us), Math.max(...vs) - Math.min(...vs));
}
const isConcrete = (model, name) => {
  const m = (model.materials || {})[name];
  return !!m && (m.material_type || "concrete") === "concrete" && m.E > 0 && m.E < 60e6;
};

export function mockAccuracyAdvice(model, thresholds) {
  const th = { ...ACC_DEFAULTS };
  for (const [k, v] of Object.entries(thresholds || {})) {
    if (!(k in ACC_DEFAULTS)) bad(`unknown threshold '${k}'`);
    if (!(typeof v === "number" && isFinite(v) && v > 0)) bad(`threshold '${k}' must be a finite number > 0`);
    th[k] = v;
  }
  const items = {};
  const add = (code, uid, type, detail) => {
    const it = items[code] || (items[code] = { objects: [], refs: [], details: [], n: 0, msg: "" });
    it.n++;
    if (!uid) return;
    if (!it.objects.includes(uid) && it.objects.length < 50) { it.objects.push(uid); it.refs.push({ type, uid }); }
    if (it.details.length < 50) it.details.push({ uid, ...detail });
  };
  const shells = model.shells || [];
  const ss = model.shell_sections || {};
  // mesh quality (structured 4-corner regions)
  for (const r of shells) {
    if (r.behavior !== "shell") continue;
    const d = structuredDivs(r);
    if (!d) continue;
    const ex = d.lx / d.nx, ey = d.ly / d.ny;
    const asp = Math.max(ex, ey) / Math.min(ex, ey);
    if (asp > th.aspect_max) add("SHELL_ASPECT_RATIO", r.uid, "shell", { max_aspect: +asp.toFixed(3), n_elements: d.nx * d.ny });
    const span = regionSpan(r), h = Math.max(ex, ey);
    if (span / h < th.min_elements_per_span - 1e-9)
      add("SHELL_MESH_COARSE", r.uid, "shell", { span: +span.toFixed(6), element_size: +h.toFixed(6), elements_across: +(span / h).toFixed(3) });
  }
  // membrane slabs: edges without collinear beams
  const onLine = (pa, pb, p) => { const L = vlen(vsub(pb, pa)), u = vsub(pb, pa).map(v => v / L), t = vdot(vsub(p, pa), u);
    return vlen(vsub(p, pa.map((v, k) => v + t * u[k]))) < 1e-6 ? t : null; };
  for (const r of shells) {
    const sec = ss[r.section] || {};
    if (r.behavior === "membrane" && r.kind === "slab") {
      const c = r.corners, bare = [];
      for (let i = 0; i < c.length; i++) {
        const pa = c[i], pb = c[(i + 1) % c.length], L = vlen(vsub(pb, pa));
        let cover = 0;
        for (const m of model.members || []) {
          const s0 = onLine(pa, pb, m.pi), s1 = onLine(pa, pb, m.pj);
          if (s0 == null || s1 == null) continue;
          cover += Math.max(0, Math.min(L, Math.max(s0, s1)) - Math.max(0, Math.min(s0, s1)));
        }
        if (cover < 0.99 * L) bare.push(i);
      }
      if (bare.length) add("MEMBRANE_SLAB_BENDING", r.uid, "shell", { reason: "edge_without_beam", edges: bare });
    } else if (r.behavior === "shell" && sec.shell_type === "membrane") {
      const loaded = r.kind === "wall" ? r.wind_cp != null
        : Object.values(model.patterns || {}).some(p => (p.area_loads || []).some(a => a.region_uid === r.uid) || p.self_weight_factor);
      if (loaded) add("MEMBRANE_SLAB_BENDING", r.uid, "shell", { reason: "membrane_type_out_of_plane_load" });
    }
    // plate theory
    if (r.behavior === "shell" && sec.material) {
      const t = sec.layered ? sec.layered.layers.reduce((a, l) => a + l.t, 0) : sec.thickness;
      const ratio = t / regionSpan(r), st = sec.shell_type || "shell";
      if ((st === "shell_thin" || st === "plate_thin") && ratio > th.thick_ratio) add("PLATE_THIN_ON_THICK", r.uid, "shell", { shell_type: st, t_over_span: +ratio.toFixed(5) });
      else if ((st === "shell_thick" || st === "plate_thick") && ratio < th.thin_ratio) add("PLATE_THICK_ON_THIN", r.uid, "shell", { shell_type: st, t_over_span: +ratio.toFixed(5) });
    }
    // rigid diaphragm with openings (rectangular openings: parametric area ratio)
    if (r.kind === "slab" && (r.openings || []).length && (r.corners || []).length === 4) {
      const ratio = r.openings.reduce((a, o) => a + (o.u1 - o.u0) * (o.v1 - o.v0), 0);
      const named = r.diaphragm ? ((model.diaphragms || {})[r.diaphragm] || {}).type || "rigid" : null;
      const story = r.story;
      const sd = (model.story_diaphragm || {})[story];
      const rigid = named ? named === "rigid" : (sd ? sd === "rigid" : (model.rigid_diaphragms !== false && (model.diaphragm || "rigid") === "rigid"));
      if (ratio >= th.opening_ratio && rigid) add("RIGID_DIAPHRAGM_OPENINGS", r.uid, "shell", { opening_ratio: +ratio.toFixed(4) });
    }
  }
  // cracked modifiers
  for (const [name, sec] of Object.entries(model.sections || {})) {
    if (!isConcrete(model, sec.material) || (sec.mod_I33 ?? 1) !== 1 || (sec.mod_I22 ?? 1) !== 1) continue;
    for (const m of model.members || []) if (m.section === name) add("CRACKED_MODIFIERS_MISSING", m.uid, "member", { section: name });
  }
  for (const [name, sec] of Object.entries(ss)) {
    if (!isConcrete(model, sec.material) || ["mod", "m11", "m22", "m12"].some(k => (sec[k] ?? 1) !== 1)) continue;
    for (const r of shells) if (r.section === name && r.behavior === "shell") add("CRACKED_MODIFIERS_MISSING", r.uid, "shell", { shell_section: name });
  }
  // mass source
  const off = new Set(model.cases_not_run || []);
  const dyn = ((model.num_modes ?? 6) > 0 && !off.has(MODAL)) ||
    Object.keys(model.rs_cases || {}).some(n => !off.has(n)) || Object.keys(model.th_cases || {}).some(n => !off.has(n));
  const src = Object.keys(model.mass_source || {}).length ? model.mass_source : (model.mass_from_patterns || {});
  const hasMass = Object.keys(src).length || Object.keys(model.story_masses || {}).length ||
    (model.nodal_masses || []).length || model.mass_source_mode === "element_self_mass";
  if (dyn && !hasMass) { add("MASS_SOURCE_MISSING", "", "model", {}); items.MASS_SOURCE_MISSING.msg = "Dynamic analysis is requested but the model has no mass (no mass source defined)."; }
  // frame point loads without auto mesh, attached to shells
  const key = p => p.map(v => Math.round(v * 1e6) / 1e6).join(",");
  const shellPts = new Set();
  for (const r of shells) if (r.behavior === "shell") for (const p of r.corners) shellPts.add(key(p));
  const loaded = {};
  for (const p of Object.values(model.patterns || {}))
    for (const ml of p.member_loads || []) if ((ml.kind === "point" || ml.kind === "moment") && ml.a > 1e-6 && ml.a < 1 - 1e-6) (loaded[ml.member_uid] ||= []).push(ml.a);
  for (const m of model.members || []) {
    if (!loaded[m.uid] || (m.axial_limit || "both") !== "both") continue;
    const am = m.auto_mesh || model.frame_auto_mesh;
    if (am && (am.at_intermediate_joints || am.at_intersections || am.max_length || (am.min_segments || 1) > 1)) continue;
    const ends = [m.pi, m.pj].filter(p => shellPts.has(key(p))).length;
    if (ends) add("FRAME_POINT_LOAD_NO_AUTOMESH", m.uid, "member", { load_positions: [...new Set(loaded[m.uid])].sort(), shell_joints_at_ends: ends, analysis_segments: 1 });
  }
  const advice = [];
  for (const code of CODES) {
    const it = items[code];
    if (!it) continue;
    const [severity, title, expl, fix] = TEXT[code];
    advice.push({ severity, code, title, message: it.msg || `${it.n} object(s) affected.`, explanation: fill(expl, th), fix: fill(fix, th),
      count: it.n, objects: it.objects, refs: it.refs, details: it.details });
  }
  advice.sort((a, b) => (a.severity === "warning" ? 0 : 1) - (b.severity === "warning" ? 0 : 1));
  const by_code = Object.fromEntries(advice.map(a => [a.code, a.count]));
  return { advice, summary: { warnings: advice.filter(a => a.severity === "warning").length, info: advice.filter(a => a.severity === "info").length,
    by_code, ok: !advice.length, n_shells: shells.length, n_members: (model.members || []).length }, thresholds: th };
}
