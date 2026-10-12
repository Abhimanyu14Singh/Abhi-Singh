/* SkyFrame — offline (?mock=1) twin of skyframe/core/shell_results.py and
   core/shell_tables.py (A3 shell force / stress contours + tables).

     POST /api/shell_results/options   → srMockOptions()
     POST /api/shell_results/sources   → srMockSources(model, results)
     POST /api/shell_results/contour   → srMockContour(model, results, body)
     tables "shell_forces" / "shell_stresses" → srMockTable(model, results, key, filter)

   Same algorithm as the backend (CONTRACT "Shell force contours and shell
   stresses"): per-element resultant boxes (signed / RS ±r / combo interval
   rules), element or global-aligned output frames, nodal averaging with the
   neighbour tensors rotated into the receiving frame, principal / von Mises
   over the box corners, S = N/t ± 6M/t², layered per-layer approximation.
   The mock results have no RS shell forces: an RS case without them gets a
   plausible stand-in (|EQX| or |EQY| shell forces × 1.12 × scale), exactly
   the per-element shape the engine uses. All values SI (kN, m, kPa). */

import { wcMockThStates as srWcThStates, wcMockThStepStates as srWcThStep, wcMockThSourceNames as srWcThNames,
  wcMockThStepKind as srWcStepKind } from "./mock_wavec.js";   // Wave C TH shell sources
export const SR_BASE = ["F11", "F22", "F12", "M11", "M22", "M12", "V13", "V23"];
const NB = 8;
const PAR_COS = 0.9998;
export const SR_COMPONENTS = {};
for (const [p, q] of [["F", "line_force"], ["M", "line_moment"], ["S", "stress"]])
  for (const s of ["11", "22", "12", "Max", "Min", "VM", "Angle"]) SR_COMPONENTS[p + s] = [p, s, s === "Angle" ? "angle" : q];
for (const s of ["13", "23", "Max"]) SR_COMPONENTS["V" + s] = ["V", s, "line_force"];
SR_COMPONENTS.S13 = ["T", "13", "stress"];
SR_COMPONENTS.S23 = ["T", "23", "stress"];
export const SR_CONTOUR_COMPONENTS = Object.keys(SR_COMPONENTS).filter(k => !k.endsWith("Angle"));
export const SR_FACES = ["top", "mid", "bot"];
export const SR_AXES = ["local", "global"];
export const SR_AVERAGING = ["none", "objects", "all"];
const QUAD_ASD = new Set(["shell_thick", "membrane", "plate_thick"]);

/* ---------------------------------------------------------------- vectors */
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scl = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = a => { const n = Math.hypot(a[0], a[1], a[2]) || 1; return scl(a, 1 / n); };

export function srElementFrame(pts, asdTri) {
  let v1, v2;
  if (pts.length === 4) {
    v1 = scl(sub(add(pts[1], pts[2]), add(pts[0], pts[3])), 0.5);
    v2 = scl(sub(add(pts[2], pts[3]), add(pts[0], pts[1])), 0.5);
  } else { v1 = sub(pts[1], pts[0]); v2 = sub(pts[2], pts[0]); }
  let e1 = unit(v1);
  const e3 = unit(cross(v1, v2));
  let e2 = cross(e3, e1);
  if (pts.length === 3 && asdTri) { const t = e1; e1 = scl(e2, -1); e2 = t; }
  return [e1, e2, e3];
}
export function srGlobalFrame(n) {
  if (Math.abs(n[2]) >= Math.SQRT1_2) {
    const a3 = n[2] > 0 ? n : scl(n, -1);
    const a1 = unit(sub([1, 0, 0], scl(a3, a3[0])));
    return [a1, cross(a3, a1), a3];
  }
  const a2 = unit(sub([0, 0, 1], scl(n, n[2])));
  let a1 = unit(cross(a2, n));
  if (Math.abs(a1[0]) > 1e-6 ? a1[0] < 0 : a1[1] < 0) a1 = scl(a1, -1);
  return [a1, a2, cross(a1, a2)];
}
/** 8×8 map for an in-plane frame change B (2×2, B_ab = a_a·e_b) + normal sign. */
function tensorMap(B, sg) {
  const [[b11, b12], [b21, b22]] = B;
  const S = [[b11 * b11, b12 * b12, 2 * b11 * b12], [b21 * b21, b22 * b22, 2 * b21 * b22],
    [b11 * b21, b12 * b22, b11 * b22 + b12 * b21]];
  const T = Array.from({ length: NB }, () => new Array(NB).fill(0));
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) { T[i][j] = S[i][j]; T[3 + i][3 + j] = sg * S[i][j]; }
  for (let i = 0; i < 2; i++) for (let j = 0; j < 2; j++) T[6 + i][6 + j] = sg * B[i][j];
  return T;
}
const frameChange = (A, E) => [
  [[dot(A[0], E[0]), dot(A[0], E[1])], [dot(A[1], E[0]), dot(A[1], E[1])]],
  Math.sign(dot(A[2], E[2])) || 1];
const mv = (T, v) => T.map(r => r.reduce((s, x, j) => s + x * v[j], 0));
const mvAbs = (T, v) => T.map(r => r.reduce((s, x, j) => s + Math.abs(x) * v[j], 0));

/* ---------------------------------------------------------------- geometry */
function geometry(model, r) {
  const quads = r.shell_quads || [], nodes = r.nodes || {};
  const regions = {};
  for (const sh of (model.shells || [])) regions[sh.uid] = sh;
  const g = { n: quads.length, region: [], nodes: [], E: [], t: [], sec: [], story: [], model };
  for (const q of quads) {
    const reg = regions[q.region];
    const ssec = reg ? (model.shell_sections || {})[reg.section] : null;
    const pts = q.nodes.map(t => (nodes[String(t)] || nodes[t] || [0, 0, 0]).map(Number));
    g.region.push(String(q.region || ""));
    g.nodes.push(q.nodes.map(Number));
    g.E.push(srElementFrame(pts, !!(ssec && QUAD_ASD.has(ssec.shell_type))));
    const tt = ssec ? (ssec.layered && ssec.layered.layers ? ssec.layered.layers.reduce((s, l) => s + +l.t, 0) : +ssec.thickness) : NaN;
    g.t.push(tt);
    g.sec.push(ssec);
    g.story.push(reg ? (reg.story || "") : "");
  }
  g.out = axes => (g["_" + axes] = g["_" + axes] || g.E.map(E => axes === "local" ? E : srGlobalFrame(E[2])));
  g.layers = i => {
    const s = g.sec[i];
    if (!s || !s.layered || !s.layered.layers) return [];
    let z0 = -0.5 * g.t[i];
    return s.layered.layers.map((la, k) => { const o = { index: k, t: +la.t, z: z0 + 0.5 * la.t, material: la.material, kind: la.kind, angle: +(la.angle || 0) }; z0 += +la.t; return o; });
  };
  return g;
}

/* ---------------------------------------------------------------- sources */
function memberKind(model, name, r) {
  if ((model.cases || {})[name]) return "static";
  if ((model.rs_cases || {})[name]) return "response_spectrum";
  if ((model.rs_combos || {})[name]) return "rs_directional";
  if ((model.th_cases || {})[name]) return "time_history";
  if ((model.staged_cases || {})[name]) return "staged";
  if ((model.combos || {})[name]) return "combo";
  if ((r.rs_cases || {})[name]) return "response_spectrum";
  if ((r.cases || {})[name]) return "static";
  return null;
}
class Unavailable extends Error {}
const arrOf = (sf, n) => Array.from({ length: n }, (_, i) => {
  const v = sf[i] !== undefined ? sf[i] : sf[String(i)];
  return v && v.length >= NB ? v.slice(0, NB).map(Number) : new Array(NB).fill(0);
});
const scaleState = ([lo, hi], f) => [lo.map((r, i) => r.map((x, k) => Math.min(f * x, f * hi[i][k]))),
  lo.map((r, i) => r.map((x, k) => Math.max(f * x, f * hi[i][k])))];
const bbox = sts => [sts[0][0].map((r, i) => r.map((_, k) => Math.min(...sts.map(s => s[0][i][k])))),
  sts[0][1].map((r, i) => r.map((_, k) => Math.max(...sts.map(s => s[1][i][k]))))];
const mapEl = (a, fn) => a.map((r, i) => r.map((x, k) => fn(x, i, k)));

function rsStandIn(model, r, name) {
  const rc = (model.rs_cases || {})[name] || {};
  const src = (r.cases || {})[rc.direction === "Y" ? "EQY" : "EQX"];
  if (!src || !src.shell_forces) return null;
  const f = (rc.scale || 1) * 1.12;
  const out = {};
  for (const [k, v] of Object.entries(src.shell_forces)) out[k] = v.map(x => Math.abs(x) * f);
  return out;
}

function leafFactors(model, name) {
  const out = {};
  const walk = (cn, s, d) => {
    if (d > 64) throw new Unavailable(`Combo ${name}: circular combo reference`);
    for (const [c, f] of Object.entries(model.combos[cn].cases || {})) {
      const sub = (model.combos || {})[c];
      if (memberKind(model, c, {}) === "combo" && sub && (sub.combo_type || "add") === "add") walk(c, s * f, d + 1);
      else out[c] = (out[c] || 0) + s * f;
    }
  };
  walk(name, 1, 0);
  return out;
}

function states(model, r, name, n, depth = 0) {
  if (depth > 64) throw new Unavailable(`${name}: combo nesting too deep`);
  const kind = memberKind(model, name, r);
  if (kind === "static" || kind === "staged") {
    const cd = (kind === "static" ? r.cases : r.staged || {})[name];
    if (!cd || !cd.shell_forces) throw new Unavailable(`case '${name}' has no shell forces`);
    const a = arrOf(cd.shell_forces, n);
    return [[[a, a]], false];
  }
  if (kind === "response_spectrum" || kind === "rs_directional") {
    const cd = (r.rs_cases || {})[name];
    const sf = cd && (cd.shell_forces || rsStandIn(model, r, name));
    if (!sf) throw new Unavailable(`response-spectrum case '${name}' has no shell forces in these results`);
    const hi = arrOf(sf, n).map(v => v.map(Math.abs));
    return [[[mapEl(hi, x => -x + 0), hi]], true];
  }
  if (kind === "time_history") return srWcThStates(r, name, n, arrOf, Unavailable);   // Wave C (js/mock_wavec.js)
  if (kind === "combo") {
    const cd = (r.combos || {})[name];
    if (cd && cd.shell_forces && !cd.min) { const a = arrOf(cd.shell_forces, n); return [[[a, a]], false]; }
    const cb = model.combos[name];
    const ctype = cb.combo_type || "add";
    const members = Object.entries(ctype === "add" ? leafFactors(model, name) : cb.cases);
    let bounded = false;
    const per = members.map(([c, f]) => { const [st, bd] = states(model, r, c, n, depth + 1); bounded = bounded || bd; return st.map(s => scaleState(s, +f)); });
    if (ctype === "envelope") return [per.flat(), true];
    const boxes = per.map(bbox);
    const sum = (sel) => mapEl(boxes[0][0], (_, i, k) => boxes.reduce((s, b) => s + sel(b, i, k), 0) + 0);
    if (ctype === "add") return [[[sum((b, i, k) => b[0][i][k]), sum((b, i, k) => b[1][i][k])]], bounded];
    if (ctype === "abs" || ctype === "srss") {
      const mag = (b, i, k) => Math.max(Math.abs(b[0][i][k]), Math.abs(b[1][i][k]));
      const v = ctype === "abs" ? sum(mag) : mapEl(sum((b, i, k) => mag(b, i, k) ** 2), Math.sqrt);
      return [[[mapEl(v, x => -x + 0), v]], true];
    }
    if (ctype === "range") return [[[sum((b, i, k) => Math.min(b[0][i][k], 0)), sum((b, i, k) => Math.max(b[1][i][k], 0))]], true];
    throw new Unavailable(`combo '${name}': unknown type ${ctype}`);
  }
  const wcStep = srWcThStep(model, r, name, n, arrOf);   // Wave C TH snapshot "<case> @ step k"
  if (wcStep) return wcStep;
  throw new Unavailable(`'${name}' is not a case with shell results`);
}

export function srMockSources(model, r) {
  const out = [];
  if (!r || !(r.shell_quads || []).length) return { sources: out, layers: 0, n_elements: 0 };
  const n = r.shell_quads.length;
  const names = [];
  for (const blk of ["cases", "rs_cases", "combos", "staged"])
    for (const nm of Object.keys(r[blk] || {})) if (!names.includes(nm)) names.push(nm);
  for (const nm of srWcThNames(r)) if (!names.includes(nm)) names.push(nm);   // Wave C TH shell sources
  for (const nm of names) {
    try { const [, bd] = states(model, r, nm, n); out.push({ name: nm, kind: memberKind(model, nm, r) || srWcStepKind(model, nm) || "response_spectrum", bounded: bd }); }
    catch (e) { if (!(e instanceof Unavailable) && !(e instanceof TypeError)) throw e; }
  }
  const g = geometry(model, r);
  let nl = 0;
  for (let i = 0; i < g.n; i++) nl = Math.max(nl, g.layers(i).length);
  return { sources: out, layers: nl, n_elements: n };
}

export function srMockOptions() {
  return { components: SR_CONTOUR_COMPONENTS.map(k => ({ key: k, family: SR_COMPONENTS[k][0], quantity: SR_COMPONENTS[k][2] })),
    locations: SR_FACES, axes: SR_AXES, averaging: SR_AVERAGING, bounds: ["max", "min"] };
}

/* ---------------------------------------------------------------- quantities */
function layerMap(g, i, k, Tmap) {
  const lays = g.layers(i);
  if (k >= lays.length) return [new Array(NB).fill(NaN), new Array(NB).fill(NaN), new Array(NB).fill(NaN)];
  const la = lays[k], s = g.sec[i], mats = g.model.materials || {};
  const m0 = mats[s.material] || {}, E0 = (+m0.E || 1) * (+s.mod || 1), nu0 = +m0.nu || 0, t = g.t[i];
  const C = [[1, -nu0, 0], [-nu0, 1, 0], [0, 0, 2 * (1 + nu0)]];
  let d = [Math.cos(la.angle * Math.PI / 180), Math.sin(la.angle * Math.PI / 180)], sg = 1;
  if (Tmap) {
    sg = Math.abs(Tmap[3][3] - Tmap[0][0]) < 1e-12 && Math.abs(Tmap[3][4] - Tmap[0][1]) < 1e-12 && Math.abs(Tmap[4][3] - Tmap[1][0]) < 1e-12 ? 1 : -1;
    d = [sg * (Tmap[6][6] * d[0] + Tmap[6][7] * d[1]), sg * (Tmap[7][6] * d[0] + Tmap[7][7] * d[1])];
  }
  const z = sg * la.z;
  const Em = [0, 1, 2].map(r => { const row = new Array(NB).fill(0); for (let c = 0; c < 3; c++) { row[c] = C[r][c] / (E0 * t); row[3 + c] = z * 12 * C[r][c] / (E0 * t ** 3); } return row; });
  const lm = mats[la.material] || {};
  if (la.kind === "steel") {
    const w = [d[0] * d[0], d[1] * d[1], d[0] * d[1]];
    const sig = Em[0].map((_, c) => (+lm.E || 0) * (w[0] * Em[0][c] + w[1] * Em[1][c] + w[2] * Em[2][c]));
    return w.map(x => sig.map(v => x * v));
  }
  const E = +lm.E || 0, nu = +lm.nu || 0, f = E / (1 - nu * nu);
  const Q = [[f, f * nu, 0], [f * nu, f, 0], [0, 0, f * 0.5 * (1 - nu)]];
  return Q.map(qr => Em[0].map((_, c) => qr[0] * Em[0][c] + qr[1] * Em[1][c] + qr[2] * Em[2][c]));
}
function familyMap(g, fam, loc, i, Tmap) {
  const z8 = () => new Array(NB).fill(0);
  if (fam === "F" || fam === "M" || fam === "V") {
    const idx = { F: [0, 1, 2], M: [3, 4, 5], V: [6, 7] }[fam];
    return idx.map(c => { const r = z8(); r[c] = 1; return r; });
  }
  const t = g.t[i];
  if (fam === "T") { const a = z8(), b = z8(); a[6] = 1 / t; b[7] = 1 / t; return [a, b]; }
  if (loc.startsWith("layer")) return layerMap(g, i, +(loc.slice(5).replace(":", "") || 0), Tmap);
  const c = { top: 1, mid: 0, bot: -1 }[loc];
  return [0, 1, 2].map(r => { const row = z8(); row[r] = 1 / t; row[3 + r] = c * 6 / t ** 2; return row; });
}
function scalar(s, a, b, c) {
  if (s === "Max") return 0.5 * (a + b) + Math.hypot(0.5 * (a - b), c);
  if (s === "Min") return 0.5 * (a + b) - Math.hypot(0.5 * (a - b), c);
  if (s === "VM") return Math.sqrt(Math.max(a * a - a * b + b * b + 3 * c * c, 0));
  return 0.5 * Math.atan2(2 * c, a - b) * 180 / Math.PI;
}
function derive(s, fam, lo, hi) {
  if (fam === "V" || fam === "T") {
    if (s === "13" || s === "23") { const k = s === "13" ? 0 : 1; return [lo[k], hi[k]]; }
    const mh = Math.sqrt(lo.reduce((acc, x, k) => acc + Math.max(x * x, hi[k] * hi[k]), 0));
    const ml = Math.sqrt(lo.reduce((acc, x, k) => acc + Math.max(0, x, -hi[k]) ** 2, 0));
    return [ml, mh];
  }
  if (s === "11" || s === "22" || s === "12") { const k = { 11: 0, 22: 1, 12: 2 }[s]; return [lo[k], hi[k]]; }
  if (lo.every((x, k) => x === hi[k])) { const v = scalar(s, lo[0], lo[1], lo[2]); return [v, v]; }
  if (s === "Angle") return [NaN, NaN];
  let mn = Infinity, mx = -Infinity;
  for (const a of [lo[0], hi[0]]) for (const b of [lo[1], hi[1]]) for (const c of [lo[2], hi[2]]) {
    const v = scalar(s, a, b, c); if (v < mn) mn = v; if (v > mx) mx = v;
  }
  return [mn, mx];
}
function quantity(L, C, R, fam, s) {
  const c = mv(L, C), r = mvAbs(L, R);
  return derive(s, fam, c.map((x, k) => x - r[k]), c.map((x, k) => x + r[k]));
}

function evaluate(model, r, name, comp, { location = "top", axes = "local", averaging = "none" } = {}, g0) {
  const n = (r.shell_quads || []).length;
  if (!n) throw new Unavailable("the results have no meshed shell elements");
  const [sts, bounded] = states(model, r, name, n);
  const g = g0 || geometry(model, r);
  const [fam, s] = SR_COMPONENTS[comp];
  const A = g.out(axes);
  const T = axes === "local" ? null : g.E.map((E, i) => tensorMap(...frameChange(A[i], E)));
  const L = g.E.map((_, i) => familyMap(g, fam, location, i, T && T[i]));
  let eLo = null, eHi = null, cLo = null, cHi = null;
  let nb = null;
  if (averaging !== "none") {
    const byNode = {};
    g.nodes.forEach((nd, i) => nd.forEach(t => (byNode[t] = byNode[t] || []).push(i)));
    nb = g.nodes.map((nd, i) => nd.map(t => byNode[t].filter(j => (averaging === "all" || g.region[j] === g.region[i]) && Math.abs(dot(A[i][2], A[j][2])) >= PAR_COS)
      .map(j => { if (j === i) return [j, null]; const [B, sg] = frameChange(A[i], A[j]);
        const id = Math.abs(B[0][0] - 1) + Math.abs(B[0][1]) + Math.abs(B[1][0]) + Math.abs(B[1][1] - 1) < 1e-12 && sg > 0;
        return [j, id ? null : tensorMap(B, sg)]; })));
  }
  for (const [lo, hi] of sts) {
    let C = lo.map((row, i) => row.map((x, k) => 0.5 * (x + hi[i][k])));
    let R = lo.map((row, i) => row.map((x, k) => 0.5 * (hi[i][k] - x)));
    if (T) { C = C.map((v, i) => mv(T[i], v)); R = R.map((v, i) => mvAbs(T[i], v)); }
    const ev = C.map((_, i) => quantity(L[i], C[i], R[i], fam, s));
    eLo = eLo ? eLo.map((x, i) => Math.min(x, ev[i][0])) : ev.map(v => v[0]);
    eHi = eHi ? eHi.map((x, i) => Math.max(x, ev[i][1])) : ev.map(v => v[1]);
    if (nb) {
      const cv = nb.map((corners, i) => corners.map(list => {
        const cc = new Array(NB).fill(0), rr = new Array(NB).fill(0), w = 1 / list.length;
        for (const [j, Tm] of list) {
          const cj = Tm ? mv(Tm, C[j]) : C[j], rj = Tm ? mvAbs(Tm, R[j]) : R[j];
          for (let k = 0; k < NB; k++) { cc[k] += w * cj[k]; rr[k] += w * rj[k]; }
        }
        return quantity(L[i], cc, rr, fam, s);
      }));
      cLo = cLo ? cLo.map((cs, i) => cs.map((x, k) => Math.min(x, cv[i][k][0]))) : cv.map(cs => cs.map(v => v[0]));
      cHi = cHi ? cHi.map((cs, i) => cs.map((x, k) => Math.max(x, cv[i][k][1]))) : cv.map(cs => cs.map(v => v[1]));
    }
  }
  return { eLo, eHi, cLo, cHi, bounded, kind: memberKind(model, name, r) || "response_spectrum", g };
}

const clean = v => (v == null || !isFinite(v)) ? null : v + 0;

export function srMockContour(model, r, body = {}) {
  const comp = body.component || "M11", location = body.location || "top", axes = body.axes || "local",
    averaging = body.averaging || "none", bound = body.bound || "max", name = body.case;
  if (!name) throw new Error("'case' must be a case or combo name");
  if (!SR_COMPONENTS[comp]) throw new Error(`unknown component '${comp}'`);
  if (!SR_FACES.includes(location) && !/^layer:?\d+$/.test(location)) throw new Error("location must be top | mid | bot | layer:<i>");
  if (!SR_AXES.includes(axes)) throw new Error("axes must be local or global");
  if (!SR_AVERAGING.includes(averaging)) throw new Error("averaging must be none, objects or all");
  if (bound !== "max" && bound !== "min") throw new Error("bound must be max or min");
  if (!r) throw new Error("Run an analysis first");
  const ev = evaluate(model, r, name, comp, { location, axes, averaging });
  const hi = bound === "max" || !ev.bounded;
  const values = (hi ? ev.eHi : ev.eLo).map(clean);
  const corners = ev.cLo ? (hi ? ev.cHi : ev.cLo).map(cs => cs.map(clean)) : null;
  const all = values.concat(corners ? corners.flat() : []).filter(v => v != null);
  const [fam, s, q] = SR_COMPONENTS[comp];
  const approximations = [], warnings = [];
  if (location.startsWith("layer") && fam === "S") approximations.push("layer stresses: strain from the homogeneous elastic section used by linear analyses, layer material law applied (no per-layer FE results in linear runs)");
  if (ev.bounded && ["Max", "Min", "VM"].includes(s)) approximations.push("principal / von Mises of a max/min source: governing bound exact over the resultant box, opposite bound evaluated at the box corners");
  if (ev.bounded && (fam === "S" || fam === "T")) approximations.push("stresses of a max/min source bound N and M independently (conservative for RS)");
  const rc = (r.rs_cases || {})[name];
  if (rc && !rc.shell_forces) warnings.push("offline mock: RS shell forces stand-in (|EQX/EQY| × 1.12)");
  return { case: name, kind: ev.kind, bounded: ev.bounded, bound: ev.bounded ? bound : null, component: comp, quantity: q,
    location: fam === "S" ? location : null, axes, averaging, values, corners,
    range: all.length ? [Math.min(...all), Math.max(...all)] : [0, 0], approximations, warnings };
}

/* ---------------------------------------------------------------- tables */
const C = (key, label, quantity) => ({ key, label, quantity });
const HEAD = [C("story", "Story", "text"), C("area", "Shell Object", "text"), C("element", "Element", "id"),
  C("case", "Output Case", "text"), C("case_type", "Case Type", "text"), C("step_type", "Step Type", "text")];
const qc = k => C(k, k, SR_COMPONENTS[k][2] === "angle" ? "angle_deg" : SR_COMPONENTS[k][2]);
export const SR_FORCE_TABLE_COMPS = ["F11", "F22", "F12", "FMax", "FMin", "FAngle", "FVM", "M11", "M22", "M12", "MMax", "MMin", "MAngle", "V13", "V23", "VMax"];
export const SR_STRESS_TABLE_COMPS = ["S11", "S22", "S12", "SMax", "SMin", "SAngle", "SVM"];
const G = "Element Output > Area Output";
export const SR_TABLE_CATALOGUE = [
  ["shell_forces", "Shell Forces", G, [...HEAD, ...SR_FORCE_TABLE_COMPS.map(qc)]],
  ["shell_stresses", "Shell Stresses", G, [...HEAD, C("location", "Location", "text"), ...SR_STRESS_TABLE_COMPS.map(qc),
    C("S13", "S13Avg", "stress"), C("S23", "S23Avg", "stress")]],
];
const CASE_TYPE = { static: "LinStatic", response_spectrum: "LinRespSpec", rs_directional: "LinRespSpec", combo: "Combination", staged: "NonStaged" };

function rowsFor(model, r, info, comps, loc, g) {
  const cols = {};
  for (const k of comps) cols[k] = evaluate(model, r, info.name, k, { location: loc }, g);
  const steps = info.bounded ? [["Max", "eHi"], ["Min", "eLo"]] : [["", "eHi"]];
  const out = [];
  for (const [st, key] of steps)
    for (let i = 0; i < g.n; i++) {
      const row = { story: g.story[i], area: g.region[i], element: i, case: info.name, case_type: CASE_TYPE[info.kind] || info.kind, step_type: st };
      for (const k of comps) row[k] = clean(cols[k][key][i]);
      out.push(row);
    }
  return out;
}

export function srMockTable(model, r, key, filter, warnings) {
  if (!r || !(r.shell_quads || []).length) return [];
  const g = geometry(model, r);
  const srcs = srMockSources(model, r).sources.filter(s => !filter || filter.has(s.name));
  let rows = [];
  if (key === "shell_forces") {
    for (const info of srcs) rows = rows.concat(rowsFor(model, r, info, SR_FORCE_TABLE_COMPS, "top", g));
    rows.sort((a, b) => (a.case < b.case ? -1 : a.case > b.case ? 1 : 0) || (a.step_type < b.step_type ? -1 : a.step_type > b.step_type ? 1 : 0) || a.element - b.element);
    return rows;
  }
  let nl = 0;
  for (let i = 0; i < g.n; i++) nl = Math.max(nl, g.layers(i).length);
  if (nl && warnings) warnings.push("layered shells: layer stresses are recovered from the homogeneous elastic section strain of the linear analysis (approximation, see CONTRACT)");
  for (const info of srcs) {
    const tr = rowsFor(model, r, info, ["S13", "S23"], "top", g);
    const locs = [["top", "Top"], ["bot", "Bottom"]];
    for (let k = 0; k < nl; k++) locs.push([`layer:${k}`, `Layer ${k + 1}`]);
    for (const [loc, lab] of locs) {
      let part = rowsFor(model, r, info, SR_STRESS_TABLE_COMPS, loc, g).map((row, idx) => ({ ...row, location: lab, S13: tr[idx].S13, S23: tr[idx].S23 }));
      if (loc.startsWith("layer")) {
        const k = +loc.slice(6);
        part = part.filter(row => k < g.layers(row.element).length).map(row => {
          const la = g.layers(row.element)[k];
          return { ...row, location: `Layer ${k + 1} (${la.material}, ${la.kind})` };
        });
      }
      rows = rows.concat(part);
    }
  }
  const ord = { Top: 0, Bottom: 1 };
  rows.sort((a, b) => (a.case < b.case ? -1 : a.case > b.case ? 1 : 0) || (a.step_type < b.step_type ? -1 : a.step_type > b.step_type ? 1 : 0)
    || a.element - b.element || ((ord[a.location] ?? 2) - (ord[b.location] ?? 2)) || (a.location < b.location ? -1 : a.location > b.location ? 1 : 0));
  return rows;
}
