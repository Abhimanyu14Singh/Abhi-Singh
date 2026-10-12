/* SkyFrame Wave B — ?mock=1 backend for the interface & modelling tools.

   * mockTemplateCatalogue() / mockTemplate(id, params): GET /api/model/
     templates and POST /api/model/template — the same geometry as
     skyframe/core/templates.py (waffle / ribbed / two-way with beams /
     staggered truss / flat slab with drop panels, equivalent ribbed shell
     modifiers, drop-panel tessellation, checkerboard trusses) plus code
     patterns, an RS function + RSX/RSY cases, the mass source and default
     combinations (simplified ELF: V = Cs W, inverted triangle).
   * mockRtf(doc): POST /api/report/rtf (skyframe/io/rtf.py port).
   * wbMockValidate(payload): the backend's 400s for the new model keys
     (named_views, report_templates, table_sets, dimension_lines,
     reference_points, reference_planes, general grid systems).
   Edit ops (Edit Shells / ramp / wall stack / relabel) run in
   js/wb_shellgeom.js via the js/mock_edit.js hook. */

import { mockAsce7Combos as wbmAsce7Combos } from "./mock.js";

const G = 9.80665;
export const WBM_TEMPLATES = ["waffle", "ribbed", "two_way_beams", "staggered_truss", "flat_slab_drop"];
export const WBM_LABELS = { waffle: "Waffle Slab", ribbed: "Ribbed Slab (one-way joists)", two_way_beams: "Two-Way Slab with Beams",
  staggered_truss: "Staggered Truss", flat_slab_drop: "Flat Slab with Drop Panels" };
const COMMON = { name: "", bays_x: 3, bays_y: 3, bay_x: 8, bay_y: 8, stories: 4, story_height: 3.5, first_story_height: null,
  sdl: 1.5, live: 2.5, live_mass_factor: 0.25, mesh_size: null, code: "asce7", SDS: 1.0, SD1: 0.6, R: 5, Ie: 1,
  ag: 0.25, q: 3, ground_type: "B", Z: 0.24, soil: "II" };
const SPECIFIC = {
  waffle: { column: 0.6, depth: 0.40, top: 0.10, rib_width: 0.15, rib_spacing: 0.90 },
  ribbed: { column: 0.6, beam_b: 0.40, beam_h: 0.60, depth: 0.35, top: 0.075, rib_width: 0.15, rib_spacing: 0.75 },
  two_way_beams: { column: 0.6, beam_b: 0.40, beam_h: 0.60, slab: 0.18 },
  staggered_truss: { panels: 4 },
  flat_slab_drop: { column: 0.6, slab: 0.25, drop_thickness: 0.15, drop_width: null },
};
const STAGGERED = { bays_x: 6, bays_y: 1, bay_x: 6, bay_y: 18, story_height: 3.0, R: 3.25 };
const LIB = {   // A992 W shapes (SI) for the staggered truss
  W14x90: { A: 0.0171, I33: 4.16e-4, I22: 1.512e-4, J: 1.7e-6, b: 0.369, h: 0.356 },
  W18x50: { A: 0.00948, I33: 3.33e-4, I22: 1.7e-5, J: 5.2e-7, b: 0.19, h: 0.457 },
  W10x33: { A: 0.00626, I33: 7.16e-5, I22: 1.53e-5, J: 2.4e-7, b: 0.202, h: 0.247 },
  W8x31: { A: 0.00589, I33: 4.57e-5, I22: 1.54e-5, J: 2.2e-7, b: 0.203, h: 0.203 },
};

export function mockTemplateCatalogue() {
  return WBM_TEMPLATES.map(id => ({ id, label: WBM_LABELS[id],
    params: { ...COMMON, ...SPECIFIC[id], ...(id === "staggered_truss" ? STAGGERED : {}), name: WBM_LABELS[id] } }));
}

export function ribbedModifiers(D, tf, bw, s, twoWay) {
  const hw = D - tf, A = s * tf + bw * hw;
  const ybar = (s * tf * tf / 2 + bw * hw * (tf + hw / 2)) / A;
  const I = s * tf ** 3 / 12 + s * tf * (ybar - tf / 2) ** 2 + bw * hw ** 3 / 12 + bw * hw * (tf + hw / 2 - ybar) ** 2;
  const fr = A / (s * D), mr = I / (s * D ** 3 / 12), ft = tf / D, mt = (tf / D) ** 3;
  const m = twoWay ? { f11: fr, f22: fr, m11: mr, m22: mr, v13: bw / s, v23: bw / s }
    : { f11: fr, f22: ft, m11: mr, m22: mt, v13: bw / s, v23: ft };
  m.f12 = ft; m.m12 = Math.sqrt(m.m11 * m.m22);
  const vol = twoWay ? tf + bw * hw * (2 / s - bw / s ** 2) : tf + bw * hw / s;
  m.weight = m.mass = vol / D;
  return m;
}

const alpha = i => { let s = ""; i += 1; while (i) { const r = (i - 1) % 26; s = String.fromCharCode(65 + r) + s; i = Math.floor((i - 1) / 26); } return s; };
const rect = (n, b, h, mat = "CONC") => {
  const a = Math.max(b, h) / 2, c = Math.min(b, h) / 2;
  return { name: n, material: mat, A: b * h, I33: b * h ** 3 / 12, I22: h * b ** 3 / 12,
    J: a * c ** 3 * (16 / 3 - 3.36 * (c / a) * (1 - c ** 4 / (12 * a ** 4))), b, h };
};
const shellSec = (name, t, mods = {}) => ({ name, material: "CONC", thickness: t, layered: null, ...mods });

export function mockNormalizeTemplateParams(id, params = {}) {
  if (!WBM_TEMPLATES.includes(id)) throw new Error(`unknown template '${id}'; one of ${WBM_TEMPLATES.join(", ")}`);
  const spec = { ...COMMON, ...SPECIFIC[id], ...(id === "staggered_truss" ? STAGGERED : {}) };
  for (const k of Object.keys(params)) if (!(k in spec)) throw new Error(`${id}: unknown parameter(s) ['${k}']`);
  const p = { ...spec, ...params };
  for (const k of ["bays_x", "bays_y", "stories"]) if (!(Number.isInteger(p[k]) && p[k] >= 1)) throw new Error(`${id}: ${k} must be a positive integer`);
  if (!["asce7", "ec8", "is1893", "none"].includes(p.code)) throw new Error("code must be one of asce7, ec8, is1893, none");
  if ((id === "waffle" || id === "ribbed") && p.top >= p.depth) throw new Error(`${id}: top slab must be thinner than the overall depth`);
  if ((id === "waffle" || id === "ribbed") && p.rib_width >= p.rib_spacing) throw new Error(`${id}: rib width must be less than the rib spacing`);
  if (id === "staggered_truss" && p.stories < 2) throw new Error("staggered_truss: needs at least 2 stories");
  p.name = p.name || WBM_LABELS[id];
  return p;
}

export function mockTemplate(id, params = {}) {
  const p = mockNormalizeTemplateParams(id, params);
  const r9 = v => Math.round(v * 1e9) / 1e9;
  const xs = Array.from({ length: p.bays_x + 1 }, (_, i) => r9(i * p.bay_x));
  const ys = Array.from({ length: p.bays_y + 1 }, (_, j) => r9(j * p.bay_y));
  const h1 = p.first_story_height || p.story_height;
  const stories = [];
  let z = 0;
  for (let s = 0; s < p.stories; s++) { const h = s ? p.story_height : h1; z = r9(z + h); stories.push({ name: `Story${s + 1}`, height: h, elevation: z }); }
  const steel = id === "staggered_truss";
  const materials = { CONC: { name: "CONC", E: 25e6, nu: 0.2, unit_weight: 24, material_type: "concrete" } };
  if (steel) materials.A992Fy50 = { name: "A992Fy50", E: 199947961.5, nu: 0.3, unit_weight: 76.9729, material_type: "steel" };
  const grid = { kind: "orthogonal", name: "G1", origin: [0, 0], rotation: 0, x_lines: xs, y_lines: ys,
    x_labels: xs.map((_, i) => alpha(i)), y_labels: ys.map((_, j) => String(j + 1)), radii: [], theta_deg: [] };
  const m = { name: p.name, materials, sections: {}, designer_sections: {}, shell_sections: {}, grid, grid_systems: [grid], stories,
    members: [], shells: [], base_fixity: "fixed", supports: [], spring_supports: [], line_springs: [], links: [],
    rigid_diaphragms: true, diaphragm: "rigid", story_diaphragm: {}, patterns: {}, cases: {}, combos: {}, rs_cases: {},
    spectrum_functions: {}, mass_source: {}, num_modes: Math.min(3 * p.stories, 12), frame_axis_convention: "etabs" };
  const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
  const mem = (kind, section, pi, pj, story, uid) => m.members.push({ uid, kind, section, pi, pj, story, length: dist(pi, pj),
    releases: "", angle: 0, rigid_i: 0, rigid_j: 0, rigid_factor: 1, foundation_ks: 0, foundation_width: 0, axial_limit: "both" });
  const shell = (behavior, section, corners, story, uid, mesh) => { const s = { uid, kind: "slab", behavior, section, corners, mesh_size: mesh || 1, story, openings: [], pier: "", area_spring: null, wind_cp: null }; m.shells.push(s); return s; };
  const mesh = (lx, ly) => p.mesh_size || r9(Math.max(0.5, Math.min(lx, ly) / 4));
  const columns = (sec, onlyY) => stories.forEach((st, si) => ys.forEach((y, yi) => {
    if (onlyY && !onlyY.includes(yi)) return;
    xs.forEach((x, xi) => mem("column", sec, [x, y, r9(st.elevation - st.height)], [x, y, st.elevation], st.name, `C${si + 1}-${alpha(xi)}${yi + 1}`));
  }));
  const beams = (sec, yOnly, xLines = true) => stories.forEach((st, si) => {
    const zz = st.elevation;
    ys.forEach((y, yi) => { if (yOnly && !yOnly.includes(yi)) return; for (let xi = 0; xi + 1 < xs.length; xi++) mem("beam", sec, [xs[xi], y, zz], [xs[xi + 1], y, zz], st.name, `BX${si + 1}-${alpha(xi)}${yi + 1}`); });
    if (xLines) xs.forEach((x, xi) => { for (let yi = 0; yi + 1 < ys.length; yi++) mem("beam", sec, [x, ys[yi], zz], [x, ys[yi + 1], zz], st.name, `BY${si + 1}-${alpha(xi)}${yi + 1}`); });
  });
  const baySlabs = (section, behavior = "shell") => {
    const out = [];
    stories.forEach((st, si) => { for (let yi = 0; yi + 1 < ys.length; yi++) for (let xi = 0; xi + 1 < xs.length; xi++) {
      const [x0, x1, y0, y1] = [xs[xi], xs[xi + 1], ys[yi], ys[yi + 1]], zz = st.elevation;
      out.push(shell(behavior, section, [[x0, y0, zz], [x1, y0, zz], [x1, y1, zz], [x0, y1, zz]], st.name, `F${si + 1}-${alpha(xi)}${yi + 1}`, mesh(x1 - x0, y1 - y0)).uid);
    } });
    return out;
  };
  let slabs = [];
  if (id === "waffle") {
    m.sections.COL = rect("COL", p.column, p.column);
    m.shell_sections.WAFFLE = shellSec("WAFFLE", p.depth, ribbedModifiers(p.depth, p.top, p.rib_width, p.rib_spacing, true));
    columns("COL");
    slabs = baySlabs("WAFFLE");
  } else if (id === "ribbed" || id === "two_way_beams") {
    m.sections.COL = rect("COL", p.column, p.column);
    m.sections.BEAM = rect("BEAM", p.beam_b, p.beam_h);
    const sn = id === "ribbed" ? "RIBBED" : "SLAB";
    m.shell_sections[sn] = id === "ribbed" ? shellSec(sn, p.depth, ribbedModifiers(p.depth, p.top, p.rib_width, p.rib_spacing, false)) : shellSec(sn, p.slab);
    columns("COL"); beams("BEAM");
    slabs = baySlabs(sn);
  } else if (id === "flat_slab_drop") {
    m.sections.COL = rect("COL", p.column, p.column);
    m.shell_sections.SLAB = shellSec("SLAB", p.slab);
    m.shell_sections.DROP = shellSec("DROP", p.slab + p.drop_thickness);
    columns("COL");
    const dw = p.drop_width || Math.min(p.bay_x, p.bay_y) / 3;
    if (dw >= Math.min(p.bay_x, p.bay_y)) throw new Error("flat_slab_drop: drop_width must be less than the bay width");
    const lines = L => { const lo = L[0], hi = L[L.length - 1], v = new Set(L.map(r9)); for (const c of L) for (const e of [c - dw / 2, c + dw / 2]) if (e > lo + 1e-9 && e < hi - 1e-9) v.add(r9(e)); return [...v].sort((a, b) => a - b); };
    const X = lines(xs), Y = lines(ys), ms = p.mesh_size || r9(Math.max(0.5, Math.min(p.bay_x, p.bay_y) / 4));
    stories.forEach((st, si) => { let k = 0; for (let j = 0; j + 1 < Y.length; j++) for (let i = 0; i + 1 < X.length; i++) {
      const [x0, x1, y0, y1] = [X[i], X[i + 1], Y[j], Y[j + 1]], cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, zz = st.elevation;
      const drop = xs.some(x => Math.abs(cx - x) < dw / 2) && ys.some(y => Math.abs(cy - y) < dw / 2);
      k++;
      slabs.push(shell("shell", drop ? "DROP" : "SLAB", [[x0, y0, zz], [x1, y0, zz], [x1, y1, zz], [x0, y1, zz]], st.name, `${drop ? "DP" : "F"}${si + 1}-${k}`, ms).uid);
    } });
  } else {   // staggered truss
    for (const [n, s] of Object.entries(LIB)) m.sections[n] = { name: n, material: "A992Fy50", ...s, shape: "W" };
    m.shell_sections.DECK = shellSec("DECK", 0.15);
    const Ly = ys[ys.length - 1], yB = ys.length - 1;
    columns("W14x90", [0, yB]);
    beams("W18x50", [0, yB], false);
    const n = p.panels, yk = Array.from({ length: n + 1 }, (_, k) => Ly * k / n);
    xs.forEach((x, i) => {
      const tr = stories.map((_, s) => s).filter(s => s >= 1 && (s + i) % 2 === 1);
      for (const si of tr) {
        const st = stories[si], zt = st.elevation, zb = r9(st.elevation - st.height), t = `${si + 1}-${alpha(i)}`;
        for (let k = 0; k < n; k++) {
          mem("beam", "W10x33", [x, yk[k], zt], [x, yk[k + 1], zt], st.name, `TT${t}${k + 1}`);
          mem("beam", "W10x33", [x, yk[k], zb], [x, yk[k + 1], zb], stories[si - 1].name, `TB${t}${k + 1}`);
          if (k > 0) mem("column", "W8x31", [x, yk[k], zb], [x, yk[k], zt], st.name, `TV${t}${k}`);
          const [a, b] = k < n / 2 ? [[x, yk[k], zb], [x, yk[k + 1], zt]] : [[x, yk[k], zt], [x, yk[k + 1], zb]];
          mem("brace", "W8x31", a, b, st.name, `TD${t}${k + 1}`);
        }
      }
      stories.forEach((st, si) => {
        if (tr.includes(si) || tr.includes(si + 1)) return;
        for (let k = 0; k < n; k++) mem("beam", "W18x50", [x, yk[k], st.elevation], [x, yk[k + 1], st.elevation], st.name, `BT${si + 1}-${alpha(i)}${k + 1}`);
      });
    });
    stories.forEach((st, si) => { for (let xi = 0; xi + 1 < xs.length; xi++) {
      const zz = st.elevation, s = shell("membrane", "DECK", [[xs[xi], 0, zz], [xs[xi + 1], 0, zz], [xs[xi + 1], Ly, zz], [xs[xi], Ly, zz]], st.name, `F${si + 1}-${alpha(xi)}`);
      s.distribution = "one_way"; s.one_way_dir = 1; slabs.push(s.uid);
    } });
  }
  // loads + mass source
  m.patterns.DEAD = { name: "DEAD", kind: "dead", self_weight_factor: 1, member_udls: [], member_loads: [], nodal_loads: [], story_forces: [],
    area_loads: p.sdl ? slabs.map(u => ({ region_uid: u, q: p.sdl, direction: "gravity" })) : [] };
  m.patterns.LIVE = { name: "LIVE", kind: "live", self_weight_factor: 0, member_udls: [], member_loads: [], nodal_loads: [], story_forces: [],
    area_loads: p.live ? slabs.map(u => ({ region_uid: u, q: p.live, direction: "gravity" })) : [] };
  m.cases.DEAD = { name: "DEAD", patterns: { DEAD: 1 }, pdelta: false };
  m.cases.LIVE = { name: "LIVE", patterns: { LIVE: 1 }, pdelta: false };
  m.mass_source = { DEAD: 1 };
  if (p.live_mass_factor) m.mass_source.LIVE = p.live_mass_factor;
  if (p.code !== "none") {
    // seismic weight per story: frames + shells (weight modifiers) + SDL + live mass
    const W = {};
    for (const st of stories) W[st.name] = 0;
    for (const mm of m.members) { const s = m.sections[mm.section]; const mat = m.materials[s.material]; if (W[mm.story] != null) W[mm.story] += s.A * mm.length * mat.unit_weight; }
    const area = c => { let a = 0; for (let i = 0; i < c.length; i++) { const [x1, y1] = c[i], [x2, y2] = c[(i + 1) % c.length]; a += x1 * y2 - x2 * y1; } return Math.abs(a) / 2; };
    for (const s of m.shells) { const ss = m.shell_sections[s.section]; W[s.story] += area(s.corners) * (ss.thickness * 24 * (ss.weight ?? 1) + p.sdl + p.live * p.live_mass_factor); }
    const Wt = Object.values(W).reduce((a, b) => a + b, 0);
    const Cs = p.code === "asce7" ? Math.min(p.SDS / (p.R / p.Ie), p.SD1 / (0.0466 * Math.pow(stories[stories.length - 1].elevation, 0.9) * p.R / p.Ie))
      : p.code === "ec8" ? 2.5 * p.ag / p.q : (p.Z / 2) * 2.5 * p.Ie / p.R;
    const V = Math.max(0.01, Cs) * Wt;
    const wh = stories.map(st => W[st.name] * st.elevation), sw = wh.reduce((a, b) => a + b, 0) || 1;
    for (const d of ["X", "Y"]) {
      m.patterns[`EQ${d}`] = { name: `EQ${d}`, kind: "quake", member_udls: [], member_loads: [], area_loads: [], nodal_loads: [],
        story_forces: stories.map((st, i) => ({ story: st.name, fx: d === "X" ? V * wh[i] / sw : 0, fy: d === "Y" ? V * wh[i] / sw : 0 })) };
      m.cases[`EQ${d}`] = { name: `EQ${d}`, patterns: { [`EQ${d}`]: 1 }, pdelta: false };
    }
    const SDS = p.code === "asce7" ? p.SDS : p.code === "ec8" ? 2.5 * p.ag / p.q : 2.5 * p.Z / 2 * p.Ie / p.R;
    const SD1 = p.code === "asce7" ? p.SD1 : SDS * 0.4;
    const Ts = SD1 / SDS, T0 = 0.2 * Ts;
    const pts = [[0, 0.4 * SDS], [T0, SDS], [Ts, SDS]];
    for (const T of [0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 10]) if (T > Ts) pts.push([T, SD1 / T]);
    m.spectrum_functions.RSF = { name: "RSF", points: pts.map(([a, b]) => [+a.toFixed(4), +b.toFixed(5)]), damping: 0.05 };
    const scale = p.code === "asce7" ? G * p.Ie / p.R : G;
    for (const d of ["X", "Y"]) m.rs_cases[`RS${d}`] = { name: `RS${d}`, direction: d, spectrum: [], num_modes: 0, combo_method: "CQC", damping: 0.05, scale, function: "RSF" };
    if (p.code === "asce7") {
      try { wbmAsce7Combos(m, "LRFD"); } catch { m.combos = {}; }   // mock.js ASCE 7 LRFD generator
    }
    if (!Object.keys(m.combos).length) {
      const pre = p.code === "ec8" ? "EN-" : p.code === "is1893" ? "IS456-" : "";
      const add = (n, f) => { m.combos[pre + n] = { name: pre + n, cases: f, combo_type: "add" }; };
      add("ULS01", { DEAD: p.code === "ec8" ? 1.35 : 1.5, LIVE: 1.5 });
      let k = 1;
      for (const e of ["EQX", "EQY", "RSX", "RSY"]) for (const sg of [1, -1]) {
        if (!m.cases[e] && !m.rs_cases[e]) continue;
        add(`SEIS${String(k++).padStart(2, "0")}`, p.code === "ec8" ? { DEAD: 1, LIVE: 0.3, [e]: sg } : { DEAD: 1.2, LIVE: 1.2, [e]: 1.2 * sg });
      }
    }
  }
  return { model: m, summary: { template: id, params: p, code: p.code, patterns: Object.keys(m.patterns), cases: Object.keys(m.cases),
    rs_cases: Object.keys(m.rs_cases), combos: Object.keys(m.combos), mass_source: { ...m.mass_source }, members: m.members.length,
    shells: m.shells.length, stories: stories.length } };
}

/* ------------------------------------------------ .rtf (port of io/rtf.py) */
export function rtfEscape(text) {
  let out = "";
  for (const ch of String(text == null ? "" : text)) {
    const o = ch.codePointAt(0);
    if ("\\{}".includes(ch)) out += "\\" + ch;
    else if (ch === "\n") out += "\\line ";
    else if (ch === "\t") out += "\\tab ";
    else if (o >= 32 && o < 128) out += ch;
    else if (o < 32) continue;
    else for (let i = 0; i < ch.length; i++) { let u = ch.charCodeAt(i); if (u > 32767) u -= 65536; out += `\\u${u}?`; }
  }
  return out;
}
const PAGES = { A4: [11906, 16838], Letter: [12240, 15840] }, MARGIN = 1020;
function b64bytes(b) {
  let raw = b.png_b64;
  if (raw == null) {
    const url = String(b.data_url || "");
    if (!url.startsWith("data:image/png;base64,")) throw new Error("image block needs png_b64 or a PNG data_url");
    raw = url.split(",", 2)[1];
  }
  const bin = atob(String(raw));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  if (out[0] !== 0x89 || out[1] !== 0x50 || String.fromCharCode(...out.slice(12, 16)) !== "IHDR") throw new Error("image blocks must be PNG");
  return out;
}
export function mockRtf(doc) {
  if (!doc || typeof doc !== "object") throw new Error("doc must be an object");
  const blocks = doc.blocks || [];
  if (!Array.isArray(blocks)) throw new Error("blocks must be a list");
  const paper = doc.paper || "A4";
  if (!PAGES[paper]) throw new Error("paper must be A4 or Letter");
  let [pw, ph] = PAGES[paper];
  if (doc.landscape) [pw, ph] = [ph, pw];
  const tw0 = pw - 2 * MARGIN;
  const para = (t, st = "") => `{\\pard\\plain${st} ${rtfEscape(t)}\\par}\n`;
  const HEAD = { 1: "\\s1\\outlinelevel0\\sb240\\sa120\\keepn\\b\\fs32\\cf2", 2: "\\s2\\outlinelevel1\\sb200\\sa80\\keepn\\b\\fs26\\cf2", 3: "\\s3\\outlinelevel2\\sb160\\sa60\\keepn\\b\\fs22\\cf2" };
  const fmt = v => v == null ? "" : typeof v === "boolean" ? (v ? "Yes" : "No") : (typeof v === "number" && !Number.isInteger(v) ? String(+v.toPrecision(6)) : String(v));
  const table = b => {
    const cols = (b.columns || []).map(String), rows = b.rows || [];
    if (!cols.length) throw new Error("table block needs columns");
    const n = cols.length;
    let align = b.align || [];
    if (align.length !== n) align = cols.map((_, i) => typeof (rows[0] || [])[i] === "number" ? "r" : "l");
    const w = Math.max(400, Math.floor(tw0 / n));
    const bdr = "\\clbrdrt\\brdrs\\brdrw10\\clbrdrl\\brdrs\\brdrw10\\clbrdrb\\brdrs\\brdrw10\\clbrdrr\\brdrs\\brdrw10";
    const row = (cells, head) => "\\trowd\\trgaph60\\trleft0" + (head ? "\\trhdr" : "") + cols.map((_, i) => bdr + (head ? "\\clcbpat3" : "") + "\\cellx" + w * (i + 1)).join("")
      + cells.map((v, i) => "\\pard\\intbl" + (align[i] === "r" ? "\\qr" : align[i] === "c" ? "\\qc" : "\\ql") + (head ? "\\b " : " ") + rtfEscape(fmt(v)) + (head ? "\\b0" : "") + "\\cell").join("") + "\\row\n";
    let s = b.caption ? para(b.caption, "\\keepn\\i\\fs18") : "";
    s += row(cols, true);
    for (const r of rows) { if (!Array.isArray(r)) throw new Error("table rows must be lists of cells"); s += row(Array.from({ length: n }, (_, i) => r[i]), false); }
    return s + "\\pard\\par\n";
  };
  let out = "{\\rtf1\\ansi\\ansicpg1252\\deff0\\uc1{\\fonttbl{\\f0\\fswiss\\fcharset0 Calibri;}}{\\colortbl;\\red16\\green52\\blue77;\\red16\\green52\\blue77;\\red227\\green233\\blue240;\\red92\\green102\\blue114;}"
    + "{\\stylesheet{\\s0 Normal;}{\\s1\\outlinelevel0\\b\\fs32 heading 1;}{\\s2\\outlinelevel1\\b\\fs26 heading 2;}{\\s3\\outlinelevel2\\b\\fs22 heading 3;}}"
    + `{\\info{\\title ${rtfEscape(doc.title || "Report")}}}\\paperw${pw}\\paperh${ph}\\margl${MARGIN}\\margr${MARGIN}\\margt${MARGIN}\\margb${MARGIN}${doc.landscape ? "\\landscape" : ""}\\fs20\n`;
  out += para(doc.title || "Report", "\\sa120\\b\\fs52\\cf1");
  if (doc.subtitle) out += para(doc.subtitle, "\\sa200\\fs26\\cf4");
  if (doc.meta && doc.meta.length) out += table({ columns: ["Item", "Value"], rows: doc.meta.map(r => r.slice(0, 2)), align: ["l", "l"] });
  if (doc.toc !== false) out += para("Contents", HEAD[1]) + "{\\pard\\plain{\\field{\\*\\fldinst TOC \\\\o \"1-3\" \\\\h \\\\z \\\\u }{\\fldrslt {\\cf4 Right-click and choose Update Field to build the table of contents.}}}\\par}\n\\page\n";
  blocks.forEach((b, i) => {
    if (!b || typeof b !== "object") throw new Error(`block ${i} must be an object`);
    if (b.type === "heading") { const l = +(b.level || 1); if (![1, 2, 3].includes(l)) throw new Error(`block ${i}: heading level must be 1..3`); out += para(b.text || "", HEAD[l]); }
    else if (b.type === "paragraph") out += para(b.text || "", b.style === "note" ? "\\i\\fs18\\cf4" : "");
    else if (b.type === "table") { try { out += table(b); } catch (e) { throw new Error(`block ${i}: ${e.message}`); } }
    else if (b.type === "image") {
      let data;
      try { data = b64bytes(b); } catch (e) { throw new Error(`block ${i}: ${e.message}`); }
      const dv = new DataView(data.buffer);
      const w = dv.getUint32(16), h = dv.getUint32(20);
      let gw = w, gh = h;
      if (b.width_px != null) { gh = gh * +b.width_px / gw; gw = +b.width_px; }
      let tw = gw * 15, th = gh * 15;
      if (tw > tw0) { th = th * tw0 / tw; tw = tw0; }
      let hex = "";
      for (const x of data) hex += x.toString(16).padStart(2, "0");
      out += `{\\pard\\plain\\qc\\keepn{\\pict\\pngblip\\picw${w}\\pich${h}\\picwgoal${Math.round(tw)}\\pichgoal${Math.round(th)}\n${hex.match(/.{1,128}/g).join("\n")}\n}\\par}\n`;
      if (b.caption) out += para(b.caption, "\\qc\\i\\fs18");
    } else if (b.type === "page_break") out += "\\page\n";
    else throw new Error(`block ${i}: unknown type '${b.type}'`);
  });
  return out + "}";
}

/* ------------------------------------------------ validation of the new keys */
const fin = v => typeof v === "number" && isFinite(v);
const pt3 = v => Array.isArray(v) && v.length === 3 && v.every(fin);
export function wbMockValidate(m) {
  if (!m || typeof m !== "object") return null;
  const named = (lst, key) => {
    if (lst == null) return null;
    if (!Array.isArray(lst)) return `${key} must be a list`;
    const seen = new Set();
    for (const [i, e] of lst.entries()) {
      if (!e || typeof e !== "object") return `${key} entry ${i + 1} must be an object`;
      if (key === "dimension_lines") continue;
      if (typeof e.name !== "string" || !e.name.trim()) return `${key} ${i + 1}: 'name' must be a non-empty string (max 80 chars)`;
      if (seen.has(e.name.trim())) return `${key}: duplicate name '${e.name}'`;
      seen.add(e.name.trim());
    }
    return null;
  };
  for (const k of ["named_views", "report_templates", "table_sets", "dimension_lines", "reference_points", "reference_planes"]) {
    const bad = named(m[k], k);
    if (bad) return bad;
  }
  for (const v of m.named_views || []) if (!["plan", "elev", "3d"].includes(v.view)) return `named view '${v.name}': view must be one of plan, elev, 3d`;
  for (const [i, dl] of (m.dimension_lines || []).entries()) {
    if (!pt3(dl.p1) || !pt3(dl.p2)) return `dimension line ${i + 1} p1 must be [x, y, z]`;
    if (Math.hypot(dl.p1[0] - dl.p2[0], dl.p1[1] - dl.p2[1], dl.p1[2] - dl.p2[2]) < 1e-9) return `dimension line ${i + 1}: zero length`;
  }
  for (const r of m.reference_points || []) if (!pt3(r.point)) return `reference point '${r.name}' must be [x, y, z]`;
  for (const r of m.reference_planes || []) {
    if (!["x", "y", "z"].includes(r.axis || "z")) return `reference plane '${r.name}': axis must be one of x, y, z`;
    if (!fin(r.coord)) return `reference plane '${r.name}' coord must be a finite number`;
  }
  for (const k of ["report_templates", "table_sets"]) for (const t of m[k] || []) if (t.tables != null && !(Array.isArray(t.tables) && t.tables.every(x => typeof x === "string"))) return `${k.replace("_", " ")} tables must be a list of strings`;
  for (const g of m.grid_systems || []) {
    if (g.kind !== "general") continue;
    if (!Array.isArray(g.lines) || !g.lines.length) return `Grid '${g.name}': a general grid needs at least one line`;
    for (const ln of g.lines) {
      if (!(Array.isArray(ln.p1) && ln.p1.length === 2 && ln.p1.every(fin) && Array.isArray(ln.p2) && ln.p2.length === 2 && ln.p2.every(fin))) return `general grid line ${ln.label}: end points must be [x, y]`;
      if (Math.hypot(ln.p2[0] - ln.p1[0], ln.p2[1] - ln.p1[1]) < 1e-9) return `general grid line ${ln.label}: zero length`;
    }
  }
  return null;
}
