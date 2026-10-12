/* SkyFrame — ?mock=1 twins of the Wave B backend additions (CONTRACT
   "Irregularity checks, grouped ASCE 7 combinations, E2K releases / joint
   axes, joint-local reactions, RS shell forces in combos, …").

     POST /api/irregularity                 → wbMockIrregularity(model, results, options)
     POST /api/results/joint-local-reactions → wbMockJointLocal(model, results, case)
     POST /api/combos/asce7 (grouped types)  → wbMockAsce7Grouped(model, std, SDS)
     Show Tables "irregularity_checks"       → registered with registerMockTable

   The irregularity mock ports the model-based checks (mass, vertical
   geometric, column discontinuities, weak story, slenderness from the
   vertical-element extents, non-parallel braces/walls) and the
   response-based ones (engine flags, IS torsion / soft storey, EC8
   stiffness, modal); floor-outline checks need the live backend and are
   reported n/a.  Everything SI. */

import { registerMockTable as wbmRegisterTable } from "./mock_tables.js";

const TOL = 1e-3;
const NA_ZERO = "not applicable (zero denominator)";
const NA_STRENGTH = "not applicable (material strength fc / fy not set)";
const NA_MOCK_FLOOR = "offline mock: floor-outline checks run on the live backend";
const NA_RESULTS = "not applicable (needs analysis results)";
const NA_DRIFT = "not applicable (negligible drift)";
const A = "ASCE 7", E8 = "EN 1998-1", IS = "IS 1893";

const ratio = (n, d, scale) => (n == null || d == null || Math.abs(d) <= Math.max(1e-9 * Math.abs(scale), 1e-300)) ? null : n / d;

function row(rows, code, item, check, o = {}) {
  rows.push({ code, item, check, story: o.story ?? "", case: o.case ?? "", direction: o.direction ?? "",
    value: o.value ?? null, limit: o.limit ?? "", status: o.status ?? "ok", note: o.note ?? "" });
}

function vertical(model) {
  const st = model.stories || [];
  const out = st.map(() => ({ cols: [], braces: [], walls: [] }));
  const idxZ = z => st.findIndex(s => Math.abs(s.elevation - z) < TOL);
  const idxN = n => st.findIndex(s => s.name === n);
  for (const m of model.members || []) {
    if (m.kind !== "column" && m.kind !== "brace") continue;
    const zb = Math.min(m.pi[2], m.pj[2]), zt = Math.max(m.pi[2], m.pj[2]);
    if (zt - zb < TOL) continue;
    let i = idxZ(zt); if (i < 0) i = idxN(m.story);
    if (i >= 0) out[i][m.kind === "column" ? "cols" : "braces"].push(m);
  }
  for (const r of model.shells || []) {
    if (r.kind !== "wall") continue;
    const zt = Math.max(...r.corners.map(c => c[2]));
    let i = idxZ(zt); if (i < 0) i = idxN(r.story);
    if (i >= 0) out[i].walls.push(r);
  }
  return out;
}

function wallLine(r) {
  let best = [0, r.corners[0], r.corners[0]];
  for (const a of r.corners) for (const b of r.corners) {
    const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (d > best[0]) best = [d, a, b];
  }
  return { p0: best[1], p1: best[2], L: best[0] };
}

function strength(model, el, over) {
  if (over) return { x: over.x ?? null, y: over.y ?? null, src: "user" };
  let vx = 0, vy = 0;
  const secs = model.sections || {}, mats = model.materials || {}, ss = model.shell_sections || {};
  for (const m of [...el.cols, ...el.braces]) {
    const sec = secs[m.section], mat = sec && mats[sec.material];
    if (!sec || !mat) return { x: null, y: null, src: "estimated" };
    const steel = ["steel", "coldformed", "aluminum"].includes(mat.material_type);
    if (m.kind === "column") {
      let v;
      if (steel) {
        if (mat.fy == null) return { x: null, y: null, src: "estimated" };
        const av = [sec.As2, sec.As3].filter(Boolean);
        v = 0.6 * mat.fy * (av.length ? Math.min(...av) : 0.5 * sec.A);
      } else {
        if (mat.fc == null) return { x: null, y: null, src: "estimated" };
        v = 0.17 * Math.sqrt(mat.fc / 1000) * 0.8 * sec.A * 1000;
      }
      vx += v; vy += v;
    } else {
      const f = steel ? mat.fy : (mat.fc ? 0.85 * mat.fc : null);
      if (f == null) return { x: null, y: null, src: "estimated" };
      const d = [0, 1, 2].map(k => m.pj[k] - m.pi[k]), L = Math.hypot(...d);
      if (L > 0) { vx += f * sec.A * Math.abs(d[0]) / L; vy += f * sec.A * Math.abs(d[1]) / L; }
    }
  }
  for (const r of el.walls) {
    const s = ss[r.section], mat = s && mats[s.material];
    if (!s || !mat || mat.fc == null) return { x: null, y: null, src: "estimated" };
    const { p0, p1, L } = wallLine(r);
    if (!(L > 0)) continue;
    const v = 0.17 * Math.sqrt(mat.fc / 1000) * s.thickness * 0.8 * L * 1000;
    vx += v * Math.abs(p1[0] - p0[0]) / L; vy += v * Math.abs(p1[1] - p0[1]) / L;
  }
  return { x: vx, y: vy, src: "estimated" };
}

function discontinuities(model, els) {
  const st = model.stories || [], out = [];
  const sup = (model.supports || []).map(s => s.point);
  const same = (a, b, z) => Math.abs(a[0] - b[0]) < TOL && Math.abs(a[1] - b[1]) < TOL && Math.abs(a[2] - z) < TOL;
  for (let i = 1; i < st.length; i++) {
    const tops = els[i - 1].cols.map(m => m.pi[2] > m.pj[2] ? m.pi : m.pj);
    for (const m of els[i].cols) {
      const bot = m.pi[2] < m.pj[2] ? m.pi : m.pj, z = bot[2];
      if (tops.some(t => same(t, bot, z)) || sup.some(p => same(p, bot, z))) continue;
      const onWall = els[i - 1].walls.some(w => {
        const { p0, p1, L } = wallLine(w);
        if (!(L > 0)) return false;
        const ux = (p1[0] - p0[0]) / L, uy = (p1[1] - p0[1]) / L, rx = bot[0] - p0[0], ry = bot[1] - p0[1];
        const t = rx * ux + ry * uy;
        return Math.abs(-rx * uy + ry * ux) < TOL && t > -TOL && t < L + TOL;
      });
      if (onWall) continue;
      const cand = tops.filter(t => Math.abs(t[2] - z) < TOL);
      let kind = "unsupported", off = null;
      if (cand.length) {
        const near = cand.reduce((a, t) => Math.hypot(t[0] - bot[0], t[1] - bot[1]) < Math.hypot(a[0] - bot[0], a[1] - bot[1]) ? t : a);
        off = Math.hypot(near[0] - bot[0], near[1] - bot[1]);
        kind = (Math.abs(near[0] - bot[0]) < TOL || Math.abs(near[1] - bot[1]) < TOL) ? "in_plane" : "out_of_plane";
      }
      out.push({ story: st[i].name, element: m.uid, type: "column", kind, offset: off, ratio: null, point: bot.slice() });
    }
  }
  return out;
}

function lateralCases(model) {
  const out = new Set();
  for (const [n, c] of Object.entries(model.cases || {})) {
    const kinds = Object.keys(c.patterns || {}).map(p => ((model.patterns || {})[p] || {}).kind);
    if (kinds.length && kinds.every(k => k === "quake" || k === "wind")) out.add(n);
  }
  return out;
}

/** Mock POST /api/irregularity. */
export function wbMockIrregularity(model, results, options = {}) {
  const tol = options.ec8_tol ?? 0.25;
  if (!(tol > 0 && tol < 1)) throw new Error("ec8_tol must be in (0, 1)");
  const st = model.stories || [], n = st.length, els = vertical(model);
  const masses = { ...(model.story_masses || {}), ...(model.explicit_story_masses || {}) };
  const over = options.story_strength || {};
  const stories = st.map((s, i) => {
    const pts = [];
    for (const m of [...els[i].cols, ...els[i].braces]) pts.push(m.pi, m.pj);
    for (const r of els[i].walls) pts.push(...r.corners);
    const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    const v = strength(model, els[i], over[s.name]);
    return { story: s.name, elevation: s.elevation, height: s.height, mass: +(masses[s.name] || 0),
      Dx: pts.length ? Math.max(...xs) - Math.min(...xs) : null, Dy: pts.length ? Math.max(...ys) - Math.min(...ys) : null,
      cx: pts.length ? (Math.max(...xs) + Math.min(...xs)) / 2 : null, cy: pts.length ? (Math.max(...ys) + Math.min(...ys)) / 2 : null,
      n_columns: els[i].cols.length, n_braces: els[i].braces.length, n_walls: els[i].walls.length,
      Vn_x: v.x, Vn_y: v.y, strength_source: v.src, floor: null };
  });
  const disc = discontinuities(model, els);
  const rows = [];
  const mmax = Math.max(0, ...stories.map(r => r.mass));
  stories.forEach((r, i) => {
    const below = i > 0 ? stories[i - 1].mass : null, above = i + 1 < n ? stories[i + 1].mass : null;
    const cands = [];
    if (below != null) cands.push(ratio(r.mass, below, mmax));
    if (above != null && !(i + 1 === n - 1 && above < r.mass)) cands.push(ratio(r.mass, above, mmax));
    if (cands.length) {
      const vals = cands.filter(v => v != null);
      const v = vals.length ? Math.max(...vals) : null;
      row(rows, A, "Table 12.3-2 Type 2", "Weight (mass)", { story: r.story, value: v, limit: "> 1.5 adjacent",
        status: v == null ? "n/a" : v > 1.5 ? "irregular" : "ok", note: v == null ? NA_ZERO : "" });
    }
    if (below != null) {
      const rb = ratio(r.mass, below, mmax), roof = i === n - 1;
      row(rows, IS, "Table 6 (ii)", "Mass irregularity", { story: r.story, value: rb, limit: "> 1.5 below",
        status: rb == null ? "n/a" : rb > 1.5 ? "irregular" : "ok", note: rb == null ? NA_ZERO : "" });
      const bad = rb != null && (rb > 1 + tol || (rb < 1 - tol && !roof));
      row(rows, E8, "4.2.3.3(3)", "Mass gradual (upper/lower)", { story: r.story, value: rb, limit: `within [${+(1 - tol).toFixed(6)}, ${+(1 + tol).toFixed(6)}]`,
        status: rb == null ? "n/a" : bad ? "irregular" : "ok", note: rb == null ? NA_ZERO : roof ? "roof decrease not considered" : "" });
    }
  });
  for (const d of ["x", "y"]) {
    const key = "D" + d, dmax = Math.max(0, ...stories.map(r => r[key] || 0));
    stories.forEach((r, i) => {
      if (r[key] == null) return;
      const adj = [i - 1, i + 1].filter(k => k >= 0 && k < n && stories[k][key] != null).map(k => stories[k][key]);
      if (!adj.length) return;
      const vals = adj.map(a => ratio(r[key], a, dmax)).filter(v => v != null);
      const v = vals.length ? Math.max(...vals) : null;
      for (const [code, item, lim] of [[A, "Table 12.3-2 Type 3", 1.3], [IS, "Table 6 (iii)", 1.25]])
        row(rows, code, item, "Vertical geometric", { story: r.story, direction: d.toUpperCase(), value: v, limit: `> ${lim} adjacent`,
          status: v == null ? "n/a" : v > lim ? "irregular" : "ok", note: v == null ? NA_ZERO : "" });
    });
  }
  stories.forEach((r, i) => {
    if (!i) return;
    const lst = disc.filter(x => x.story === r.story);
    const inpl = lst.filter(x => x.kind === "in_plane"), outp = lst.filter(x => x.kind === "out_of_plane");
    const names = a => a.map(x => x.element).join(", ");
    row(rows, A, "Table 12.3-2 Type 4", "In-plane discontinuity", { story: r.story, value: inpl.length, limit: "any in-plane offset", status: inpl.length ? "irregular" : "ok", note: names(inpl) });
    row(rows, A, "Table 12.3-1 Type 4", "Out-of-plane offset", { story: r.story, value: outp.length, limit: "any", status: outp.length ? "irregular" : "ok", note: names(outp) });
    row(rows, IS, "Table 6 (iv)", "In-plane discontinuity", { story: r.story, value: inpl.length ? 1 : null, limit: "offset > 0.20 L", status: inpl.length ? "irregular" : "ok", note: names(inpl) });
    row(rows, IS, "Table 5 (iv)", "Out-of-plane offsets", { story: r.story, value: outp.length, limit: "any", status: outp.length ? "irregular" : "ok", note: names(outp) });
    row(rows, IS, "Table 6 (vi)", "Floating or stub columns", { story: r.story, value: lst.length, limit: "any", status: lst.length ? "irregular" : "ok", note: names(lst) });
    row(rows, E8, "4.2.3.3(2)", "Lateral systems continuous", { story: r.story, value: lst.length, limit: "no interruption", status: lst.length ? "irregular" : "ok", note: names(lst) });
  });
  for (const d of ["x", "y"]) {
    const key = "Vn_" + d, vmax = Math.max(0, ...stories.map(r => r[key] || 0));
    stories.slice(0, -1).forEach((r, i) => {
      const up = stories[i + 1][key], here = r[key];
      const v = here == null || up == null ? null : ratio(here, up, vmax);
      const note = here == null || up == null ? NA_STRENGTH : v == null ? NA_ZERO : `strength ${r.strength_source}`;
      row(rows, A, "Table 12.3-2 Type 5", "Weak story (strength)", { story: r.story, direction: d.toUpperCase(), value: v,
        limit: "< 0.80 above (< 0.65 extreme)", status: v == null ? "n/a" : v < 0.65 ? "extreme" : v < 0.8 ? "irregular" : "ok", note });
      row(rows, IS, "Table 6 (v)", "Weak storey", { story: r.story, direction: d.toUpperCase(), value: v, limit: "< 1.0 above",
        status: v == null ? "n/a" : v < 1 ? "irregular" : "ok", note });
    });
  }
  for (const r of stories) {
    for (const [code, item, chk] of [[A, "Table 12.3-1 Type 2", "Re-entrant corner"], [A, "Table 12.3-1 Type 3", "Diaphragm discontinuity"],
      [IS, "Table 5 (ii)", "Re-entrant corners"], [IS, "Table 5 (iii)", "Floor openings"], [E8, "4.2.3.2(4)", "Compactness"]])
      row(rows, code, item, chk, { story: r.story, status: "n/a", note: NA_MOCK_FLOOR });
    if (r.Dx && r.Dy) {
      const lam = Math.max(r.Dx, r.Dy) / Math.min(r.Dx, r.Dy);
      row(rows, E8, "4.2.3.2(5)", "Slenderness Lmax/Lmin", { story: r.story, value: lam, limit: "<= 4", status: lam > 4 ? "irregular" : "ok", note: "plan from the vertical-element extents" });
    }
  }
  row(rows, E8, "4.2.3.2(6)", "Torsional radius r >= 3.33 e0, r >= ls", { status: "n/a", note: "not evaluated (needs the storey torsional stiffness)" });
  const npl = [];
  for (const m of model.members || []) if (m.kind === "brace") {
    const dx = m.pj[0] - m.pi[0], dy = m.pj[1] - m.pi[1];
    if (Math.hypot(dx, dy) < TOL) continue;
    const a = ((Math.atan2(dy, dx) * 180 / Math.PI) % 90 + 90) % 90;
    if (Math.min(a, 90 - a) > 1) npl.push(m.uid);
  }
  for (const [code, item] of [[A, "Table 12.3-1 Type 5"], [IS, "Table 5 (v)"]])
    row(rows, code, item, "Nonparallel system", { value: npl.length, limit: "any element not parallel to X / Y", status: npl.length ? "irregular" : "ok", note: npl.join(", ") });
  if (results) responseRows(model, results, rows, st.map(s => s.name), tol);
  else for (const [code, item, chk] of [[A, "Table 12.3-2 Type 1", "Soft story"], [A, "Table 12.3-1 Type 1", "Torsional"],
    [E8, "4.2.3.3(3)", "Stiffness gradual"], [IS, "Table 5 (i)", "Torsional"], [IS, "Table 6 (i)", "Soft storey"], [IS, "Table 6 (vii)", "Irregular modes"]])
    row(rows, code, item, chk, { status: "n/a", note: NA_RESULTS });
  const rank = { "n/a": 0, ok: 1, irregular: 2, extreme: 3 }, summary = {};
  for (const r of rows) {
    const s = summary[r.code] = summary[r.code] || {};
    if (s[r.item] == null || rank[r.status] > rank[s[r.item]]) s[r.item] = r.status;
  }
  return { stories, discontinuities: disc, checks: rows, summary, options: { ec8_tol: tol, raster: options.raster ?? 200 },
    warnings: ["offline mock: floor-outline checks need the live backend"], has_results: !!results };
}

function responseRows(model, results, rows, names, tol) {
  const lat = lateralCases(model);
  for (const [cs, by] of Object.entries(results.irregularity || {})) {
    if (!lat.has(cs)) continue;
    for (const s of names) {
      const e = by[s]; if (!e) continue;
      const na = e.na_reason || {};
      row(rows, A, "Table 12.3-2 Type 1", "Soft story", { story: s, case: cs, value: e.stiff_ratio ?? null,
        limit: "< 0.70 above / < 0.80 avg3 (0.60/0.70)",
        status: e.stiff_ratio == null && na.stiff_ratio ? "n/a" : e.soft_flag === "extreme_soft" ? "extreme" : e.soft_flag === "soft" ? "irregular" : "ok",
        note: na.stiff_ratio || "" });
      for (const d of ["x", "y"]) {
        const tr = e[`tors_ratio_${d}`];
        if (tr == null) {
          row(rows, A, "Table 12.3-1 Type 1", "Torsional", { story: s, case: cs, direction: d.toUpperCase(), limit: ">= 1.2 (1.4 extreme)", status: "n/a", note: na[`tors_ratio_${d}`] || "" });
          row(rows, IS, "Table 5 (i)", "Torsional Dmax/Dmin", { story: s, case: cs, direction: d.toUpperCase(), limit: "> 1.5 (> 2.0 revise)", status: "n/a", note: na[`tors_ratio_${d}`] || "" });
          continue;
        }
        row(rows, A, "Table 12.3-1 Type 1", "Torsional", { story: s, case: cs, direction: d.toUpperCase(), value: tr, limit: ">= 1.2 (1.4 extreme)",
          status: tr >= 1.4 ? "extreme" : tr >= 1.2 ? "irregular" : "ok" });
        const dm = tr >= 2 - 1e-12 ? null : tr / (2 - tr);
        row(rows, IS, "Table 5 (i)", "Torsional Dmax/Dmin", { story: s, case: cs, direction: d.toUpperCase(), value: dm, limit: "> 1.5 (> 2.0 revise)",
          status: dm == null || dm > 2 ? "extreme" : dm > 1.5 ? "irregular" : "ok", note: dm == null ? "Dmin ~ 0 (one end does not move)" : "" });
      }
    }
  }
  for (const [cs, by] of Object.entries(results.story_stiffness || {})) {
    if (!lat.has(cs)) continue;
    for (const d of ["x", "y"]) {
      const kk = "k" + d, kv = names.map(s => [s, (by[s] || {})[kk]]);
      const kmax = Math.max(0, ...kv.map(([, v]) => Math.abs(v || 0)));
      for (let i = 0; i + 1 < kv.length; i++) {
        const [s, k] = kv[i], [s2, k2] = kv[i + 1];
        const naS = x => ((by[x] || {}).na_reason || {})[kk];
        const v = naS(s) || naS(s2) || !k || !k2 ? null : ratio(k, k2, kmax);
        const note = v == null ? NA_DRIFT : "";
        row(rows, IS, "Table 6 (i)", "Soft storey k/k_above", { story: s, case: cs, direction: d.toUpperCase(), value: v, limit: "< 1.0", status: v == null ? "n/a" : v < 1 ? "irregular" : "ok", note });
        const up = v == null ? null : 1 / v;
        row(rows, E8, "4.2.3.3(3)", "Stiffness gradual (upper/lower)", { story: s2, case: cs, direction: d.toUpperCase(), value: up,
          limit: `within [${+(1 - tol).toFixed(6)}, ${+(1 + tol).toFixed(6)}]`, status: up == null ? "n/a" : (up > 1 + tol || up < 1 - tol) ? "irregular" : "ok", note });
      }
    }
  }
  const part = ((results.modal || {}).participation) || [];
  if (part.length >= 2) {
    const arg = k => part.reduce((a, p) => (p[k] || 0) > (a[k] || 0) ? p : a);
    const mx = arg("ux"), my = arg("uy"), mr = arg("rz");
    const f3 = part.slice(0, 3), sx = f3.reduce((a, p) => a + (p.ux || 0), 0), sy = f3.reduce((a, p) => a + (p.uy || 0), 0);
    const close = mx !== my && Math.abs(mx.T - my.T) < 0.1 * Math.max(mx.T, my.T);
    row(rows, IS, "Table 6 (vii)", "Irregular modes", { value: Math.min(sx, sy), limit: "first 3 modes < 65 % mass; Tx, Ty within 10 %",
      status: sx < 0.65 || sy < 0.65 || close ? "irregular" : "ok", note: `sum UX ${sx.toFixed(3)}, sum UY ${sy.toFixed(3)}, Tx ${(+mx.T).toPrecision(4)} s, Ty ${(+my.T).toPrecision(4)} s` });
    const tors = mr !== mx && mr !== my && (mr.T > mx.T || mr.T > my.T);
    row(rows, IS, "Table 5 (i)(b)", "Torsional mode period", { value: mr.T, limit: "T_torsion > a translational T", status: tors ? "irregular" : "ok",
      note: `T_rz ${(+mr.T).toPrecision(4)} s, Tx ${(+mx.T).toPrecision(4)} s, Ty ${(+my.T).toPrecision(4)} s` });
  }
}

/* ---------------------------------------------------------- joint-local reactions */
const rad = d => d * Math.PI / 180;
/** Rows = local 1/2/3 axes (global components), R = Rz(a) Ry(b) Rx(c). */
export function wbAxesRows([a, b, c]) {
  const [ca, sa, cb, sb, cc, sc] = [Math.cos(rad(a)), Math.sin(rad(a)), Math.cos(rad(b)), Math.sin(rad(b)), Math.cos(rad(c)), Math.sin(rad(c))];
  const Rz = [[ca, -sa, 0], [sa, ca, 0], [0, 0, 1]], Ry = [[cb, 0, sb], [0, 1, 0], [-sb, 0, cb]], Rx = [[1, 0, 0], [0, cc, -sc], [0, sc, cc]];
  const mm = (P, Q) => P.map((r, i) => [0, 1, 2].map(j => r[0] * Q[0][j] + r[1] * Q[1][j] + r[2] * Q[2][j]));
  const R = mm(mm(Rz, Ry), Rx);
  return [0, 1, 2].map(j => [R[0][j], R[1][j], R[2][j]].map(v => Math.abs(v) < 1e-15 ? 0 : v));
}
export function wbToLocal(rows, g) {
  const dot = (r, v) => r[0] * v[0] + r[1] * v[1] + r[2] * v[2];
  return [...rows.map(r => dot(r, g.slice(0, 3))), ...rows.map(r => dot(r, g.slice(3, 6)))];
}
const pkey = p => p.map(v => (+v).toFixed(6)).join(",");
/** Mock POST /api/results/joint-local-reactions. */
export function wbMockJointLocal(model, results, onlyCase = null) {
  const axes = new Map();
  for (const e of model.joint_local_axes || []) if (e.angles.some(v => +v !== 0)) axes.set(pkey(e.point), e.angles);
  const out = {};
  if (!axes.size || !results) return { cases: out };
  const blocks = [...Object.entries(results.cases || {}), ...Object.entries(results.combos || {}).filter(([, c]) => !c.min)];
  for (const [name, cd] of blocks) {
    if (onlyCase != null && name !== onlyCase) continue;
    const per = {};
    for (const [t, g] of Object.entries(cd.reactions || {})) {
      const p = (results.nodes || {})[t]; if (!p) continue;
      const ang = axes.get(pkey(p)); if (!ang) continue;
      per[t] = { point: p.slice(), angles: ang.slice(), global: g.slice(), local: wbToLocal(wbAxesRows(ang), g) };
    }
    if (Object.keys(per).length) out[name] = per;
  }
  return { cases: out };
}

/* ---------------------------------------------------------- grouped ASCE 7 */
const GROUPED = ["superdead", "reducible_live", "roof_live", "snow"];
const KIND_GROUP = { dead: "G", superdead: "G", live: "L", reducible_live: "L", roof_live: "Lr", snow: "S", wind: "W", quake: "E" };
export function wbMockGroupedApplies(model) {
  return Object.values(model.cases || {}).some(c => Object.keys(c.patterns || {})
    .some(p => GROUPED.includes(((model.patterns || {})[p] || {}).kind)));
}
/** Mirror of skyframe.core.combos_asce7.asce7_grouped (same names / factors). */
export function wbMockAsce7Grouped(model, standard = "LRFD", SDS) {
  const cls = { G: [], L: [], Lr: [], S: [], W: [], E: [] };
  for (const [cn, c] of Object.entries(model.cases || {})) {
    const gs = new Set(Object.keys(c.patterns || {}).map(p => KIND_GROUP[((model.patterns || {})[p] || {}).kind]));
    if (gs.size !== 1 || gs.has(undefined)) continue;
    cls[[...gs][0]].push(cn);
  }
  const { G, L, S, W: winds, E: quakes } = cls;
  const sds = isFinite(SDS) && SDS > 0 ? +SDS : 0;
  const combos = {};
  const put = (name, terms, lat) => {
    const f = {};
    for (const [cs, x] of terms) if (x !== 0) for (const c of cs) f[c] = (f[c] || 0) + x;
    if (lat) f[lat[0]] = (f[lat[0]] || 0) + lat[1];
    combos[name] = f;
  };
  const g = v => String(+v.toPrecision(6));
  const roofs = ["Lr", "S"].filter(k => cls[k].length).map(k => [k, cls[k]]);
  const lt = L.length ? [[L, 1.0]] : [];
  const SG = [[1, "+"], [-1, "-"]];
  if (G.length) {
    if (standard !== "ASD") {
      put("1.4D", [[G, 1.4]]);
      const b2 = "1.2D" + (L.length ? "+1.6L" : "");
      if (roofs.length) for (const [k, cs] of roofs) put(`${b2}+0.5${k}`, [[G, 1.2], [L, 1.6], [cs, 0.5]]);
      else put(b2, [[G, 1.2], [L, 1.6]]);
      for (const [k, cs] of roofs) {
        put(L.length ? `1.2D+1.6${k}+1.0L` : `1.2D+1.6${k}`, [[G, 1.2], [cs, 1.6], [L, 1.0]]);
        for (const w of winds) for (const [s, sl] of SG) put(`1.2D+1.6${k}${sl}0.5${w}`, [[G, 1.2], [cs, 1.6]], [w, s * 0.5]);
      }
      for (const w of winds) for (const [s, sl] of SG) {
        const tail = L.length ? "+1.0L" : "";
        if (roofs.length) for (const [k, cs] of roofs) put(`1.2D${tail}+0.5${k}${sl}1.0${w}`, [[G, 1.2], ...lt, [cs, 0.5]], [w, s]);
        else put(`1.2D${tail}${sl}1.0${w}`, [[G, 1.2], ...lt], [w, s]);
        put(`0.9D${sl}1.0${w}`, [[G, 0.9]], [w, s]);
      }
      const dA = sds ? 1.2 + 0.2 * sds : 1.2, dM = sds ? 0.9 - 0.2 * sds : 0.9;
      for (const q of quakes) for (const [s, sl] of SG) {
        const tail = (L.length ? "+1.0L" : "") + (S.length ? "+0.2S" : "");
        put(`${g(dA)}D${tail}${sl}1.0${q}`, [[G, dA], ...lt, [S, 0.2]], [q, s]);
        put(`${g(dM)}D${sl}1.0${q}`, [[G, dM]], [q, s]);
      }
    } else {
      put("D", [[G, 1.0]]);
      if (L.length) put("D+L", [[G, 1.0], [L, 1.0]]);
      for (const [k, cs] of roofs) {
        put(`D+${k}`, [[G, 1.0], [cs, 1.0]]);
        if (L.length) put(`D+0.75L+0.75${k}`, [[G, 1.0], [L, 0.75], [cs, 0.75]]);
      }
      for (const w of winds) for (const [s, sl] of SG) {
        put(`D${sl}0.6${w}`, [[G, 1.0]], [w, s * 0.6]);
        const l75 = L.length ? [[L, 0.75]] : [], tl = L.length ? "+0.75L" : "";
        if (roofs.length) for (const [k, cs] of roofs) put(`D${tl}+0.75${k}${sl}0.45${w}`, [[G, 1.0], ...l75, [cs, 0.75]], [w, s * 0.45]);
        else put(`D${tl}${sl}0.45${w}`, [[G, 1.0], ...l75], [w, s * 0.45]);
        put(`0.6D${sl}0.6${w}`, [[G, 0.6]], [w, s * 0.6]);
      }
      const [da, d34, dm] = sds ? [1 + 0.14 * sds, 1 + 0.105 * sds, 0.6 - 0.14 * sds] : [1, 1, 0.6];
      for (const q of quakes) for (const [s, sl] of SG) {
        put(`${da === 1 ? "" : g(da)}D${sl}0.7${q}`, [[G, da]], [q, s * 0.7]);
        const tl = (L.length ? "+0.75L" : "") + (S.length ? "+0.75S" : "");
        put(`${d34 === 1 ? "" : g(d34)}D${tl}${sl}0.525${q}`, [[G, d34], ...(L.length ? [[L, 0.75]] : []), [S, 0.75]], [q, s * 0.525]);
        put(`${g(dm)}D${sl}0.7${q}`, [[G, dm]], [q, s * 0.7]);
      }
    }
  }
  model.combos = model.combos || {};
  const added = [];
  for (const [nm, f] of Object.entries(combos)) { model.combos[nm] = { name: nm, combo_type: "add", cases: f }; added.push(nm); }
  return { model, added };
}

/* ---------------------------------------------------------- Show Tables */
const C = (key, label, quantity) => ({ key, label, quantity });
wbmRegisterTable("irregularity_checks", "Irregularity Checks", "Structure Output > Other Output Items",
  [C("code", "Code", "text"), C("item", "Item", "text"), C("check", "Check", "text"), C("story", "Story", "text"),
    C("case", "Output Case", "text"), C("direction", "Direction", "text"), C("value", "Value", "ratio"),
    C("limit", "Limit", "text"), C("status", "Status", "text"), C("note", "Note", "text")],
  (model, results, filter) => wbMockIrregularity(model, results).checks
    .filter(r => !filter || !r.case || filter.has(r.case)));
