/* SkyFrame — nonprismatic frame sections + per-joint panel zones (analysis
   only). CONTRACT "Nonprismatic sections and per-joint panel zones".

     Define → Frame Sections · Add Nonprismatic…   model.sections[name] {kind: "nonprismatic",
                                                   segments, subdivisions, + representative props}
     Section Manager                               NP rows (summary + taper thumbnail + Edit…)
     Assign → Joint · Panel Zone…                  model.joint_panel_zones [{point, property,
                                                   k | doubler_t, connectivity}]
     Properties panel                              NP variation (resolved segment table + taper)
     Plan / elevation / 3D                         "NP" badges + tapered silhouette (elevation,
                                                   3D ribbon); small square at panel-zone joints

   The model store stays SI; every number goes through js/units.js. A
   prismatic model never gets a new key (kind / segments / subdivisions are
   only written on nonprismatic sections, joint_panel_zones only while it
   is non-empty), and every dict is written in the backend's normalised,
   key-sorted shape so a save round-trips byte-identically. A dialog closed
   with OK and no edit leaves the model untouched. */

import U from "./units.js";
import { selectionJoints as npSelectionJoints } from "./assigndlg.js";
import {
  esc as npEsc, clone as npClone, isNum as npIsNum, dialog as npDialog, closeDialog as npCloseDialog,
  btn as npBtn, footBar as npFootBar, errorLine as npErrorLine, showError as npShowError,
  group as npGroup, el as npEl, numInput as npNumInput, row as npRow, select as npSelect,
  radio as npRadio, samePt as npSamePt, storyAtZ as npStoryAtZ, ptLabel as npPtLabel,
  makeCtx as npMakeCtx,
} from "./nls_ui.js";

export const NP_KIND = "nonprismatic";
export const NP_EI_VARS = [["linear", "Linear"], ["parabolic", "Parabolic"], ["cubic", "Cubic"]];
export const NP_LEN_TYPES = [["relative", "Relative"], ["absolute", "Absolute"]];
const EIN = { linear: 1, parabolic: 2, cubic: 3 };
const LTOL = 1e-6;
export const NP_MAX_SUB = 200;
export const NP_DEFAULT_SUB = 16;

export const PZ_PROPS = [["from_column", "From column"], ["elastic", "Elastic (column web + doubler plate)"], ["spring", "Spring (rotational stiffness k)"]];
export const PZ_CONN = {
  beams_to_panel: ["beam"],
  braces_to_panel: ["brace"],
  beams_and_braces_to_panel: ["beam", "brace"],
};
const PZ_CONN_LABEL = { beams_to_panel: "Beams to panel zone", braces_to_panel: "Braces to panel zone", beams_and_braces_to_panel: "Beams and braces to panel zone" };
const PZ_COLOR = "#c46be0";
const NP_COLOR = "#4fc1c9";

/* ================================================================
   model helpers (also used by js/mock_np.js)
   ================================================================ */
export const isNpSec = s => !!s && typeof s === "object" && s.kind === NP_KIND;
const memLen = mm => Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]);
const pkey = p => p.map(v => (Math.round(+v * 1e6) / 1e6).toFixed(6)).join(",");

/** Raw (A, I22, I33, J) + drawing depth/width of a prismatic section. A
    rectangle that has only b/h yet (fresh, unsaved) gets the rectangle
    formulas the backend uses. */
export function npRaw(s) {
  if (!s) return null;
  let { A, I33, I22, J } = s;
  const b = +s.b || 0, h = +s.h || 0;
  if (!(A > 0) && b > 0 && h > 0) {
    A = b * h; I33 = b * h ** 3 / 12; I22 = h * b ** 3 / 12;
    const a = Math.max(b, h) / 2, c = Math.min(b, h) / 2;
    J = a * c ** 3 * (16 / 3 - 3.36 * (c / a) * (1 - c ** 4 / (12 * a ** 4)));
  }
  const d = h > 0 ? h : (A > 0 && I33 > 0 ? Math.sqrt(12 * I33 / A) : 0);
  const w = b > 0 ? b : (A > 0 && d > 0 ? A / d : 0);
  return { A: +A, I22: +I22, I33: +I33, J: +J, h: d, b: w };
}

/** Segment in the backend normalised shape (keys sorted). */
export function npNormSeg(sg) {
  return {
    EI22: sg.EI22 || "linear",
    EI33: sg.EI33 || "linear",
    end_section: sg.end_section,
    length: +(sg.length ?? 1),
    length_type: sg.length_type || "relative",
    start_section: sg.start_section,
  };
}

/** Is the NP section's stiffness actually varying (backend is_varying)? */
export function npVarying(model, sec) {
  if (!isNpSec(sec)) return false;
  const set = new Set();
  for (const sg of sec.segments || [])
    for (const k of ["start_section", "end_section"]) {
      const r = npRaw((model.sections || {})[sg[k]]);
      if (!r) return false;
      set.add([r.A, r.I22, r.I33, r.J].join("|"));
    }
  return set.size > 1;
}

/** Absolute layout over a member of length L (backend layout()).
    → {segs: [{x0, x1, sg, p0, p1, n33, n22}], err} */
export function npLayout(model, sec, L) {
  const segs = (sec.segments || []).map(npNormSeg);
  const out = { segs: [], err: "" };
  if (!segs.length) { out.err = "at least one segment is required"; return out; }
  const absSum = segs.filter(s => s.length_type === "absolute").reduce((a, s) => a + s.length, 0);
  const relSum = segs.filter(s => s.length_type === "relative").reduce((a, s) => a + s.length, 0);
  if (absSum > L + LTOL) out.err = `absolute segment lengths (${U.fmtU("length", absSum, 3)}) exceed the member length (${U.fmtU("length", L, 3)})`;
  const rem = L - absSum;
  if (!out.err && relSum <= 0 && Math.abs(rem) > LTOL)
    out.err = `absolute segment lengths (${U.fmtU("length", absSum, 3)}) must sum to the member length (${U.fmtU("length", L, 3)}) when no segment is relative`;
  else if (!out.err && relSum > 0 && rem <= LTOL) out.err = "no length is left for the relative segments";
  let x = 0;
  segs.forEach((s, k) => {
    const ln = s.length_type === "absolute" ? s.length : (relSum > 0 ? Math.max(rem, 0) * s.length / relSum : 0);
    const x1 = k === segs.length - 1 && !out.err ? L : x + ln;
    out.segs.push({
      x0: x, x1, sg: s,
      p0: npRaw((model.sections || {})[s.start_section]), p1: npRaw((model.sections || {})[s.end_section]),
      n33: EIN[s.EI33] || 1, n22: EIN[s.EI22] || 1,
    });
    x = x1;
  });
  return out;
}

const ipow = (a, b, n, s) => {
  if (!(a > 0) || !(b > 0)) return a + s * (b - a);
  if (n === 1) return a + s * (b - a);
  const ra = a ** (1 / n), rb = b ** (1 / n);
  return (ra + s * (rb - ra)) ** n;
};
/** Interpolated properties at x (m) on a layout: A, J linear; I^(1/n)
    linear; the drawing depth follows the same rule as h³ ∝ I. */
export function npPropsAt(lay, x) {
  const segs = lay.segs;
  if (!segs.length) return null;
  let seg = segs[segs.length - 1];
  for (const s of segs) if (x <= s.x1 + 1e-12) { seg = s; break; }
  const ln = seg.x1 - seg.x0;
  const t = ln <= 0 ? 0 : Math.min(Math.max((x - seg.x0) / ln, 0), 1);
  const p0 = seg.p0 || {}, p1 = seg.p1 || {};
  return {
    A: p0.A + t * (p1.A - p0.A),
    J: p0.J + t * (p1.J - p0.J),
    I33: ipow(p0.I33, p1.I33, seg.n33, t),
    I22: ipow(p0.I22, p1.I22, seg.n22, t),
    h: ipow(p0.h ** 3, p1.h ** 3, seg.n33, t) ** (1 / 3),
    b: p0.b + t * (p1.b - p0.b),
  };
}

/** One section's backend rules (resolve_sections). "" = ok. */
export function npValidateSection(model, name, sec) {
  const where = `Section ${name}: `;
  const segs = sec.segments;
  if (!Array.isArray(segs) || !segs.length) return where + "a nonprismatic section needs a non-empty segments list";
  const KEYS = new Set(["start_section", "end_section", "length", "length_type", "EI33", "EI22"]);
  const mats = new Set();
  for (const sg of segs) {
    if (!sg || typeof sg !== "object") return where + "segment must be an object";
    const bad = Object.keys(sg).filter(k => !KEYS.has(k));
    if (bad.length) return where + `segment: unknown key(s) ${bad.join(", ")}`;
    const ln = sg.length ?? 1;
    if (!(typeof ln === "number" && isFinite(ln) && ln > 0)) return where + "segment.length must be a finite value > 0";
    if (!["relative", "absolute"].includes(sg.length_type ?? "relative")) return where + "segment.length_type must be relative or absolute";
    for (const k of ["EI33", "EI22"]) if (!EIN[sg[k] ?? "linear"]) return where + `segment.${k} must be linear, parabolic or cubic`;
    for (const k of ["start_section", "end_section"]) {
      const ref = (model.sections || {})[sg[k]];
      if (typeof sg[k] !== "string" || !sg[k]) return where + `segment.${k} must be a section name`;
      if (!ref) return where + `unknown section '${sg[k]}'`;
      if (isNpSec(ref)) return where + `segment section '${sg[k]}' must be prismatic`;
      const r = npRaw(ref);
      if (![r.A, r.I22, r.I33, r.J].every(v => isFinite(v) && v > 0)) return where + `section '${sg[k]}' needs A, I33, I22, J > 0`;
      mats.add(ref.material);
    }
  }
  if (mats.size !== 1) return where + `all segment sections must share one material (got ${[...mats].sort().join(", ")})`;
  const sub = sec.subdivisions ?? NP_DEFAULT_SUB;
  if (!(typeof sub === "number" && Number.isInteger(sub) && sub >= 1 && sub <= NP_MAX_SUB)) return where + `subdivisions must be an integer in [1, ${NP_MAX_SUB}]`;
  if (sec.shear_deformation || sec.As2 != null || sec.As3 != null) return where + "frame shear deformation (As2/As3) is not supported on nonprismatic sections";
  return "";
}

/** Every nonprismatic section + every member using a varying one. */
export function npValidateModel(model) {
  if (!model || !model.sections) return "";
  for (const [n, s] of Object.entries(model.sections)) {
    if (s && s.kind != null && s.kind !== "prismatic" && s.kind !== NP_KIND) return `Section ${n}: kind must be prismatic or nonprismatic`;
    if (isNpSec(s)) { const e = npValidateSection(model, n, s); if (e) return e; }
  }
  for (const mm of model.members || []) {
    const sec = model.sections[mm.section];
    if (!npVarying(model, sec)) continue;
    if ((mm.axial_limit || "both") !== "both") return `Member ${mm.uid}: axial-only members cannot use the nonprismatic section '${mm.section}'`;
    const lay = npLayout(model, sec, memLen(mm));
    if (lay.err) return `Member ${mm.uid} (nonprismatic section ${mm.section}): ${lay.err}`;
  }
  return "";
}

/** point key → [{mm, end}] of every bending member end. */
export function pzJointEnds(model) {
  const out = new Map();
  for (const mm of model.members || []) {
    if ((mm.axial_limit || "both") !== "both") continue;
    for (const [end, p] of [["i", mm.pi], ["j", mm.pj]]) {
      const k = pkey(p);
      if (!out.has(k)) out.set(k, []);
      out.get(k).push({ mm, end });
    }
  }
  return out;
}
const isVertical = mm => Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1]) < 1e-6;
/** Governing column / beam sizing at a joint (backend _column_sizing). */
export function pzSizing(model, at) {
  const vcols = at.map(x => x.mm).filter(mm => mm.kind === "column" && isVertical(mm));
  const beams = at.map(x => x.mm).filter(mm => mm.kind === "beam");
  if (!vcols.length || !beams.length) return null;
  const hOf = mm => +(((model.sections || {})[mm.section] || {}).h) || 0;
  const col = vcols.reduce((a, c) => (hOf(c) > hOf(a) ? c : a), vcols[0]);
  const cs = model.sections[col.section] || {};
  const mat = (model.materials || {})[cs.material] || {};
  const G = isFinite(mat.G) && mat.G > 0 ? mat.G : (isFinite(mat.E) ? mat.E / (2 * (1 + (isFinite(mat.nu) ? mat.nu : 0.2))) : NaN);
  return { col, d_c: +cs.h || 0, t_p: +cs.b || 0, d_b: Math.max(...beams.map(hOf)), G };
}
/** Why an entry can not sit at its joint ("" = ok). */
export function pzJointCheck(model, e, ends = pzJointEnds(model)) {
  const at = ends.get(pkey(e.point)) || [];
  const attach = PZ_CONN[e.connectivity || "beams_to_panel"] || ["beam"];
  const panel = at.filter(x => attach.includes(x.mm.kind));
  const other = at.filter(x => !attach.includes(x.mm.kind));
  if (!panel.length || !other.length)
    return `needs a ${attach.join(" / ")} end (panel side) and another frame member end`;
  if ((e.property || "from_column") !== "spring") {
    const sz = pzSizing(model, at);
    if (!sz) return "needs a vertical column and a beam";
    if (!(sz.d_c > 0 && sz.t_p > 0 && sz.d_b > 0)) return "needs section b/h on the column and beams";
  }
  return "";
}
/** K_theta of an entry (kN·m/rad), NaN when it can not be sized. */
export function pzStiffness(model, e, ends = pzJointEnds(model)) {
  if (e.property === "spring") return +e.k;
  const sz = pzSizing(model, ends.get(pkey(e.point)) || []);
  if (!sz) return NaN;
  return sz.G * sz.d_c * sz.d_b * (sz.t_p + (e.property === "elastic" ? +(e.doubler_t || 0) : 0));
}
/** Entry in the backend normalised shape (keys sorted). */
export function pzNorm(e) {
  const prop = e.property || "from_column";
  const o = { connectivity: e.connectivity || "beams_to_panel" };
  if (prop === "elastic") o.doubler_t = +(e.doubler_t || 0);
  if (prop === "spring") o.k = +e.k;
  o.point = e.point.map(Number);
  o.property = prop;
  return o;
}
/** joint_panel_zones backend rules (panelzones.validate_model). */
export function pzValidateModel(model) {
  const lst = model && model.joint_panel_zones;
  if (lst == null) return "";
  if (!Array.isArray(lst)) return "joint_panel_zones must be a list";
  const KEYS = new Set(["point", "property", "k", "doubler_t", "connectivity"]);
  const ends = pzJointEnds(model);
  const seen = new Set();
  for (let i = 0; i < lst.length; i++) {
    const e = lst[i], w = `joint_panel_zones[${i}]`;
    if (!e || typeof e !== "object") return `${w} must be an object`;
    const bad = Object.keys(e).filter(k => !KEYS.has(k));
    if (bad.length) return `${w}: unknown key(s) ${bad.join(", ")}`;
    if (!Array.isArray(e.point) || e.point.length !== 3 || !e.point.every(v => typeof v === "number" && isFinite(v))) return `${w}.point must be [x, y, z]`;
    const prop = e.property ?? "from_column";
    if (!PZ_PROPS.some(([v]) => v === prop)) return `${w}.property must be from_column, elastic or spring`;
    if (prop === "spring") { if (!(npIsNum(e.k) && e.k > 0)) return `${w}.k must be > 0`; }
    else if (e.k != null) return `${w}.k applies to property 'spring' only`;
    if (prop === "elastic") { if (e.doubler_t != null && !(npIsNum(e.doubler_t) && e.doubler_t >= 0)) return `${w}.doubler_t must be >= 0`; }
    else if (e.doubler_t != null && e.doubler_t !== 0) return `${w}.doubler_t applies to property 'elastic' only`;
    if (!PZ_CONN[e.connectivity ?? "beams_to_panel"]) return `${w}.connectivity must be one of ${Object.keys(PZ_CONN).join(", ")}`;
    const k = pkey(e.point);
    if (seen.has(k)) return `${w}: duplicate joint`;
    seen.add(k);
    const why = pzJointCheck(model, e, ends);
    if (why) return `${w} at (${e.point.join(", ")}): the joint ${why}`;
  }
  return "";
}

/* ================================================================
   drawing helpers
   ================================================================ */
const NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs = {}) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  return n;
}
/** Depth profile [[x, h]] sampled along a layout. */
function profile(lay, L, per = 40) {
  const pts = [];
  for (const s of lay.segs) {
    const n = s.p0 && s.p1 && (s.p0.h !== s.p1.h) ? per : 1;
    for (let i = 0; i <= n; i++) {
      const x = s.x0 + (s.x1 - s.x0) * i / n;
      const p = npPropsAt(lay, Math.min(x, L));
      pts.push([x, p ? p.h : 0, p ? p.I33 : 0]);
    }
  }
  return pts;
}

/** Elevation preview (SVG string) of the tapered member. */
export function npPreviewSvg(model, sec, L, { width = 600, height = 190, mini = false, id = "" } = {}) {
  const lay = npLayout(model, sec, L);
  if (!lay.segs.length || !(L > 0)) return `<svg class="np-svg" viewBox="0 0 ${width} ${height}"></svg>`;
  const pts = profile(lay, L, mini ? 16 : 48);
  const hmax = Math.max(...pts.map(p => p[1]), 1e-9);
  const Imax = Math.max(...pts.map(p => p[2]), 1e-12);
  const padL = mini ? 4 : 26, padR = mini ? 4 : 26;
  const W = width - padL - padR;
  const sx = W / L;
  const bandH = mini ? 0 : 38;
  const topY = mini ? 4 : 30;
  const avail = height - topY - (mini ? 4 : 34) - bandH;
  const trueSy = sx;                                 // 1:1 depth
  let sy = trueSy, exag = 1;
  if (hmax * sy > avail) sy = avail / hmax;
  else if (hmax * sy < avail * 0.35) { sy = (avail * 0.6) / hmax; exag = sy / trueSy; }
  const X = x => padL + x * sx;
  const top = pts.map(([x]) => `${X(x).toFixed(1)},${topY.toFixed(1)}`);
  const bot = pts.slice().reverse().map(([x, h]) => `${X(x).toFixed(1)},${(topY + h * sy).toFixed(1)}`);
  let s = `<svg class="np-svg${mini ? " np-svg-mini" : ""}"${id ? ` id="${id}"` : ""} viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Nonprismatic member elevation">`;
  s += `<polygon class="np-body" points="${top.join(" ")} ${bot.join(" ")}"/>`;
  if (!mini) {
    // segment boundaries + labels
    lay.segs.forEach((g, k) => {
      const xa = X(g.x0), xb = X(g.x1);
      if (k) s += `<line class="np-bound" x1="${xa}" y1="${topY - 8}" x2="${xa}" y2="${topY + hmax * sy + 8}"/>`;
      const mid = (xa + xb) / 2;
      s += `<text class="np-seglbl" x="${mid}" y="${topY - 10}" text-anchor="middle">${npEsc(g.sg.start_section === g.sg.end_section ? g.sg.start_section : `${g.sg.start_section} → ${g.sg.end_section}`)}</text>`;
      s += `<text class="np-dim" x="${mid}" y="${topY + hmax * sy + 18}" text-anchor="middle">${npEsc(U.fmtU("length", g.x1 - g.x0, 2))}</text>`;
    });
    // end depths
    const h0 = pts[0][1], h1 = pts[pts.length - 1][1];
    s += `<text class="np-dim" x="${padL - 4}" y="${topY + h0 * sy / 2}" text-anchor="end" dominant-baseline="middle">${npEsc(U.fmt("dim", h0, 3))}</text>`;
    s += `<text class="np-dim" x="${width - padR + 4}" y="${topY + h1 * sy / 2}" text-anchor="start" dominant-baseline="middle">${npEsc(U.fmt("dim", h1, 3))}</text>`;
    s += `<text class="np-ends" x="${padL}" y="${height - 4}">i</text><text class="np-ends" x="${width - padR}" y="${height - 4}" text-anchor="end">j</text>`;
    // I33 band
    const by0 = height - 16, by1 = by0 - bandH + 8;
    const ip = pts.map(([x, , I]) => `${X(x).toFixed(1)},${(by0 - (I / Imax) * (by0 - by1)).toFixed(1)}`);
    s += `<line class="np-axis" x1="${padL}" y1="${by0}" x2="${width - padR}" y2="${by0}"/>`;
    s += `<polyline class="np-icurve" points="${ip.join(" ")}"/>`;
    s += `<text class="np-dim" x="${padL + 2}" y="${by1 - 2}">I33 / I33,max</text>`;
    s += `<text class="np-dim" x="${width - padR}" y="${12}" text-anchor="end">L = ${npEsc(U.fmtU("length", L, 2))} · depth ${npEsc(U.label("dim"))}${exag > 1.05 ? ` · depth ×${exag.toFixed(1)}` : ""}</text>`;
  }
  return s + `</svg>`;
}

/* ================================================================
   Define → Frame Sections · Nonprismatic…
   ================================================================ */
function uniqueName(m, base) {
  let k = 1;
  while (m.sections[`${base}${k}`]) k++;
  return `${base}${k}`;
}
/** The full section dict, key-sorted, in the backend to_dict shape. */
function buildSection(m, name, draft, prev) {
  const ref = m.sections[draft.segments[0].start_section] || {};
  const r = npRaw(ref) || {};
  const o = {};
  const base = prev ? npClone(prev) : {
    As2: null, As3: null, mod_A: 1, mod_As2: 1, mod_As3: 1, mod_I22: 1, mod_I33: 1, mod_J: 1,
    mod_mass: 1, mod_weight: 1, shear_deformation: false,
  };
  Object.assign(base, {
    name, kind: NP_KIND, material: ref.material,
    segments: draft.segments.map(npNormSeg), subdivisions: draft.subdivisions,
  });
  if (ref.A > 0 || r.A > 0) Object.assign(base, { A: ref.A ?? r.A, I33: ref.I33 ?? r.I33, I22: ref.I22 ?? r.I22, J: ref.J ?? r.J });
  base.b = ref.b ?? 0; base.h = ref.h ?? 0;
  if (!prev) for (const k of ["mod_A", "mod_As2", "mod_As3", "mod_I22", "mod_I33", "mod_J", "mod_mass", "mod_weight"]) base[k] = 1.0;
  for (const k of Object.keys(base).sort()) o[k] = base[k];
  return o;
}

export function openNpSection(ctx, sky, editName = null) {
  const m = ctx.store.model;
  if (!m) return null;
  const prev = editName ? m.sections[editName] : null;
  if (editName && !isNpSec(prev)) return null;
  const prismatic = () => Object.entries(m.sections).filter(([, s]) => !isNpSec(s)).map(([n]) => n);
  const selMembers = (sky.selObjects ? sky.selObjects().members : []) || [];
  const first = (selMembers.find(mm => !isNpSec(m.sections[mm.section])) || {}).section || prismatic()[0] || "";
  const draft = prev ? {
    name: editName,
    subdivisions: prev.subdivisions ?? NP_DEFAULT_SUB,
    segments: (prev.segments || []).map(npNormSeg),
  } : {
    name: uniqueName(m, "NPS"),
    subdivisions: NP_DEFAULT_SUB,
    segments: [npNormSeg({ start_section: first, end_section: first, length: 1, length_type: "relative" })],
  };
  const users = editName ? (m.members || []).filter(mm => mm.section === editName) : [];
  const lenSrc = users[0] || selMembers[0];
  let previewL = lenSrc ? memLen(lenSrc) : 6;
  const body = npEl("div", "np-def");
  const err = npErrorLine("npError");
  const issues = npEl("div", "np-issues"); issues.id = "npIssues";
  const prevBox = npEl("div", "np-preview"); prevBox.id = "npPreview";

  const sumRel = () => draft.segments.filter(s => s.length_type === "relative").reduce((a, s) => a + s.length, 0);
  const problems = () => {
    const out = [];
    if (!draft.segments.length) out.push("At least one segment is required.");
    const pr = new Set(prismatic());
    const mats = new Set();
    draft.segments.forEach((s, i) => {
      for (const k of ["start_section", "end_section"]) {
        if (!s[k] || !m.sections[s[k]]) out.push(`Segment ${i + 1}: ${k === "start_section" ? "start" : "end"} section '${s[k] || ""}' does not exist.`);
        else if (!pr.has(s[k])) out.push(`Segment ${i + 1}: '${s[k]}' is nonprismatic — use prismatic sections.`);
        else {
          mats.add(m.sections[s[k]].material);
          const r = npRaw(m.sections[s[k]]);
          if (![r.A, r.I33, r.I22, r.J].every(v => isFinite(v) && v > 0)) out.push(`Segment ${i + 1}: section '${s[k]}' needs A, I33, I22, J > 0.`);
        }
      }
      if (!(s.length > 0)) out.push(`Segment ${i + 1}: length must be > 0.`);
    });
    if (mats.size > 1) out.push(`All segment sections must share one material (got ${[...mats].sort().join(", ")}).`);
    const hasRel = draft.segments.some(s => s.length_type === "relative");
    if (hasRel && Math.abs(sumRel() - 1) > 1e-6) out.push(`Relative lengths must sum to 1 (Σ = ${sumRel().toFixed(4)}) — click Normalize.`);
    if (!(Number.isInteger(draft.subdivisions) && draft.subdivisions >= 1 && draft.subdivisions <= NP_MAX_SUB)) out.push(`Subdivisions must be a whole number in 1…${NP_MAX_SUB}.`);
    if (prev && m.sections[draft.name] && draft.name !== editName) out.push(`A section named '${draft.name}' already exists.`);
    if (!prev && m.sections[draft.name]) out.push(`A section named '${draft.name}' already exists.`);
    if (!draft.name) out.push("Enter a section name.");
    return out;
  };
  const memberWarnings = () => {
    if (!users.length) return [];
    const sec = { segments: draft.segments };
    const out = [];
    for (const mm of users) {
      const lay = npLayout(m, sec, memLen(mm));
      if (lay.err) out.push(`Member ${mm.uid}: ${lay.err}.`);
    }
    return out.slice(0, 4);
  };
  const refresh = () => {
    const ps = problems();
    const mw = ps.length ? [] : memberWarnings();
    const sec = { segments: draft.segments };
    const lay = npLayout(m, sec, previewL);
    const tag = ps.length ? "" : (npVarying(m, sec) ? `<span class="asn-tag ok">varying · ${draft.segments.filter(s => s.start_section !== s.end_section).length} tapered segment(s) × ${draft.subdivisions} sub-elements</span>`
      : `<span class="asn-tag">uniform — analysed exactly as the prismatic section</span>`);
    issues.innerHTML = (ps.concat(mw).map(t => `<div class="np-issue">${npEsc(t)}</div>`).join("")) +
      (lay.err && !ps.length ? `<div class="np-issue">Preview length: ${npEsc(lay.err)}.</div>` : "") + tag;
    prevBox.innerHTML = ps.some(t => /does not exist|nonprismatic|needs A/.test(t)) || !draft.segments.length
      ? `<p class="muted">Preview unavailable — fix the segment sections.</p>`
      : npPreviewSvg(m, sec, previewL, { id: "npPreviewSvg" });
    const rs = body.querySelector("#npRelSum");
    if (rs) rs.textContent = `Σ relative = ${sumRel().toFixed(4)}`;
  };
  const draw = () => {
    body.textContent = "";
    body.appendChild(npEl("p", "muted dlg-intro",
      "ETABS Nonprismatic Section Definition. Segments run from end i to end j; each varies from its start to its end section. " +
      "Absolute lengths are fixed; the rest of the member is shared by the <b>relative</b> segments. EI variation: " +
      "<b>linear</b> I, <b>parabolic</b> √I or <b>cubic</b> ∛I varies linearly (cubic = linear depth taper of a rectangle); A and J vary linearly."));
    const g0 = npGroup("Section");
    const nameIn = document.createElement("input");
    nameIn.type = "text"; nameIn.id = "npName"; nameIn.value = draft.name; nameIn.spellcheck = false;
    nameIn.addEventListener("change", () => { draft.name = nameIn.value.trim(); refresh(); });
    g0.appendChild(npRow("Section name", nameIn, null));
    g0.appendChild(npRow("Sub-elements per varying segment", npNumInput("none", draft.subdivisions, {
      id: "npSub", int: true, min: 1, max: NP_MAX_SUB, onSet: v => { draft.subdivisions = v; refresh(); },
    }), null, "analysis sub-elements (default 16)"));
    body.appendChild(g0);

    const g1 = npGroup("Segments (end i → end j)");
    const tbl = npEl("table", "data-table np-table"); tbl.id = "npTable";
    tbl.innerHTML = `<thead><tr><th>#</th><th class="txt">Start section</th><th class="txt">End section</th><th>Length</th><th class="txt">Length type</th><th class="txt">EI33 variation</th><th class="txt">EI22 variation</th><th></th></tr></thead>`;
    const tb = document.createElement("tbody");
    const pr = prismatic().map(n => [n, `${n}${m.sections[n].h ? ` · h ${U.fmtU("dim", m.sections[n].h, 3)}` : ""}`]);
    draft.segments.forEach((s, i) => {
      const tr = document.createElement("tr");
      tr.dataset.seg = String(i);
      const td = (c, cls = "txt") => { const t = document.createElement("td"); t.className = cls; t.appendChild(c); return t; };
      const num = npEl("td", "", String(i + 1));
      const lenKind = () => (s.length_type === "absolute" ? "length" : "none");
      const lenIn = npNumInput(lenKind, s.length, { id: `npLen${i}`, gt: 0, onSet: v => { s.length = v; refresh(); } });
      const lenCell = npEl("td", "np-lencell");
      lenCell.append(lenIn, npEl("span", "cd-unit", npEsc(s.length_type === "absolute" ? U.label("length") : "")));
      const ltSel = npSelect(NP_LEN_TYPES, s.length_type, v => {
        // keep the resolved segment length when switching type (on the preview length)
        const lay = npLayout(m, { segments: draft.segments }, previewL);
        const g = lay.segs[i];
        const cur = g ? g.x1 - g.x0 : 0;
        s.length_type = v;
        if (v === "absolute" && cur > 0) s.length = +cur.toPrecision(12);
        else if (v === "relative" && previewL > 0 && cur > 0) s.length = +(cur / previewL).toPrecision(12);
        draw();
      }, `npLt${i}`);
      const x = npEl("button", "chip-x"); x.textContent = "✕"; x.id = `npDel${i}`; x.title = "Delete segment";
      x.addEventListener("click", () => { draft.segments.splice(i, 1); draw(); });
      const ins = npEl("button", "chip-x np-ins"); ins.textContent = "+"; ins.id = `npIns${i}`; ins.title = "Insert a segment after this one";
      ins.addEventListener("click", () => { draft.segments.splice(i + 1, 0, npNormSeg({ start_section: s.end_section, end_section: s.end_section, length: s.length_type === "relative" ? s.length : 1, length_type: "relative" })); draw(); });
      const act = npEl("td", "np-act"); act.append(ins, x);
      tr.append(num,
        td(npSelect(pr, s.start_section, v => { s.start_section = v; refresh(); }, `npStart${i}`)),
        td(npSelect(pr, s.end_section, v => { s.end_section = v; refresh(); }, `npEnd${i}`)),
        lenCell, td(ltSel),
        td(npSelect(NP_EI_VARS, s.EI33, v => { s.EI33 = v; refresh(); }, `npEI33_${i}`)),
        td(npSelect(NP_EI_VARS, s.EI22, v => { s.EI22 = v; refresh(); }, `npEI22_${i}`)),
        act);
      tb.appendChild(tr);
    });
    if (!draft.segments.length) tb.innerHTML = `<tr><td class="txt muted" colspan="8">No segments — add one.</td></tr>`;
    tbl.appendChild(tb);
    g1.appendChild(npEl("div", "table-scroll")).appendChild(tbl);
    const bar = npEl("div", "cd-inline np-bar");
    bar.append(
      npBtn("+ Add Segment", "btn-small", () => {
        const last = draft.segments[draft.segments.length - 1];
        const sec = last ? last.end_section : first;
        draft.segments.push(npNormSeg({ start_section: sec, end_section: sec, length: 1, length_type: "relative" }));
        draw();
      }, "Append a segment at end j", "npAdd"),
      npBtn("Normalize", "btn-small", () => {
        const t = sumRel();
        if (t > 0) for (const s of draft.segments) if (s.length_type === "relative") s.length = +(s.length / t).toPrecision(12);
        draw();
      }, "Scale the relative lengths so they sum to 1", "npNorm"),
      npEl("span", "muted np-relsum"));
    bar.lastChild.id = "npRelSum";
    g1.appendChild(bar);
    body.appendChild(g1);

    const g2 = npGroup("Elevation preview");
    const lr = npRow(users.length ? `Member length (${users[0].uid})` : "Preview member length",
      npNumInput("length", previewL, { id: "npPrevL", gt: 0, onSet: v => { previewL = v; refresh(); } }), "length",
      "resolves absolute segment lengths for the preview");
    g2.append(lr, prevBox);
    body.appendChild(g2);
    body.append(issues, err);
    refresh();
  };
  const commit = () => {
    const ps = problems();
    if (ps.length) { npShowError(err, ps[0]); return false; }
    const mw = memberWarnings();
    if (mw.length) { npShowError(err, mw[0]); return false; }
    const next = buildSection(m, draft.name, draft, prev);
    if (prev && editName === draft.name && JSON.stringify(next) === JSON.stringify(prev)) return true;   // untouched
    if (prev && editName !== draft.name) {
      // rename: rebuild the dict keeping order, retarget members / segments
      const out = {};
      for (const [k, v] of Object.entries(m.sections)) out[k === editName ? draft.name : k] = k === editName ? next : v;
      m.sections = out;
      for (const mm of m.members || []) if (mm.section === editName) mm.section = draft.name;
    } else m.sections[draft.name] = next;
    ctx.markDirty();
    ctx.onChange && ctx.onChange("sections");
    afterModelEdit(sky);
    return true;
  };
  const fb = npFootBar("Define → Frame Sections · Nonprismatic", [
    npBtn("Cancel", "", () => dlg.close(), "", "npCancel"),
    npBtn("OK", "btn-run", () => { if (commit()) dlg.close(); }, "", "npOk"),
  ]);
  const dlg = npDialog("npModal", { title: "Nonprismatic Section Definition", glyph: "chart", wide: true, body, foot: fb.wrap, onUnits: draw });
  draw();
  return dlg;
}

/** Section Manager row for a nonprismatic section. */
export function npSectionRow(sky, name, s) {
  const m = sky.store.model;
  const wrap = npEl("div", "mgr-row np-mgr-row");
  wrap.dataset.np = name;
  const segs = s.segments || [];
  const desc = segs.map(g => g.start_section === g.end_section ? g.start_section : `${g.start_section}→${g.end_section}`).join(" | ");
  const user = (m.members || []).find(mm => mm.section === name);
  const thumb = npEl("span", "np-thumb");
  try { thumb.innerHTML = npValidateSection(m, name, s) ? "" : npPreviewSvg(m, s, user ? memLen(user) : 6, { width: 120, height: 26, mini: true }); } catch (e) { /* preview only */ }
  const edit = npBtn("Edit…", "btn-small", () => openNpSection(npMakeCtx(sky), sky, name), `Edit nonprismatic section ${name}`, "");
  edit.dataset.npEdit = name;
  const used = sky.ME && sky.ME.sectionInUse ? sky.ME.sectionInUse(m, name) : (m.members || []).some(mm => mm.section === name);
  const del = document.createElement("button");
  del.className = "del"; del.textContent = "✕"; del.disabled = used;
  del.title = used ? "In use by members" : "Delete section";
  del.addEventListener("click", () => {
    delete m.sections[name];
    sky.markDirty && sky.markDirty();
    sky.renderSectionMgr && sky.renderSectionMgr();
  });
  wrap.append(npEl("span", "np-name", `<b>${npEsc(name)}</b> <span class="np-badge">NP</span>`),
    npEl("span", "sec-props np-desc", `${npEsc(desc)} <span class="dim">· ${segs.length} seg · n ${npEsc(String(s.subdivisions ?? NP_DEFAULT_SUB))}</span>`),
    thumb, edit, del);
  return wrap;
}

/* ================================================================
   Assign → Joint · Panel Zone…
   ================================================================ */
export function openPanelZone(ctx, sky) {
  const m = ctx.store.model;
  if (!m) return null;
  const joints = npSelectionJoints(m, ctx.store.selection || []).map(p => ({ p: p.slice(), on: true }));
  const cur = p => (m.joint_panel_zones || []).find(e => npSamePt(e.point, p));
  // initial state = the common current assignment of the joints ("" = model default)
  const init = (() => {
    if (!joints.length) return { mode: "", e: null };
    // joints that can carry a panel zone at all (a free cantilever tip can not)
    const ends0 = pzJointEnds(m);
    const elig = joints.filter(j => cur(j.p) || !pzJointCheck(m, { point: j.p, property: "spring" }, ends0));
    const es = (elig.length ? elig : joints).map(j => cur(j.p));
    const sig = e => (e ? JSON.stringify(pzNorm({ ...e, point: [0, 0, 0] })) : "");
    if (es.every(e => sig(e) === sig(es[0]))) return { mode: es[0] ? es[0].property || "from_column" : "", e: es[0] || null };
    return { mode: null, e: null };
  })();
  const st = {
    mode: init.mode,                                   // "" = remove override, null = mixed
    doubler_t: init.e && init.e.doubler_t != null ? init.e.doubler_t : 0,
    k: init.e && init.e.k != null ? init.e.k : null,
    connectivity: (init.e && init.e.connectivity) || "beams_to_panel",
  };
  const body = npEl("div", "np-pz");
  const err = npErrorLine("pzError");
  const entryOf = p => ({ point: p, property: st.mode || "from_column", connectivity: st.connectivity, k: st.k, doubler_t: st.doubler_t });
  let list = null;
  const renderJoints = () => {
    if (!list) return;
    list.textContent = "";
    const ends = pzJointEnds(m);
    if (!joints.length) list.appendChild(npEl("p", "muted asn-empty",
      "No joints — select frame members (their end joints are listed) or add a joint by coordinates."));
    joints.forEach((j, i) => {
      const why = st.mode ? pzJointCheck(m, entryOf(j.p), ends) : "";
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.checked = j.on; cb.id = `pzJ${i}`;
      cb.addEventListener("change", () => { j.on = cb.checked; syncNote(); });
      const lab = npEl("label", "dlg-chk");
      lab.append(cb, npEl("span", "asn-pt", npEsc(`${npStoryAtZ(m, j.p[2]) || "—"} · ${npPtLabel(j.p)}`)));
      const r = npEl("div", "asn-jrow" + (why ? " is-bad" : ""));
      r.dataset.pt = j.p.join(",");
      r.append(lab);
      if (why) r.appendChild(npEl("span", "asn-tag bad", npEsc(why) + " — skipped"));
      else if (st.mode) {
        const K = pzStiffness(m, entryOf(j.p), ends);
        if (isFinite(K) && K > 0) r.appendChild(npEl("span", "asn-tag ok", `K ${npEsc(U.fmtU("rot_stiffness", K, 0))}`));
      }
      const c = cur(j.p);
      r.appendChild(npEl("span", "asn-cur muted", c ? npEsc(`current: ${c.property}${c.property === "spring" ? " " + U.fmtU("rot_stiffness", c.k, 0) : c.property === "elastic" ? " +" + U.fmtU("dim", c.doubler_t || 0, 0) : ""}`) : "model default"));
      const x = npEl("button", "chip-x"); x.textContent = "✕"; x.title = "Remove from list";
      x.addEventListener("click", () => { joints.splice(i, 1); renderJoints(); syncNote(); });
      r.appendChild(x);
      list.appendChild(r);
    });
  };
  const draw = () => {
    body.textContent = "";
    const pzw = m.panel_zones || "none";
    body.appendChild(npEl("p", "muted dlg-intro",
      `Per-joint panel zone (ETABS Assign → Joint → Panel Zone): an elastic scissors spring K<sub>θ</sub> about global rx / ry between the joint and a ` +
      `duplicate node that the panel-side member ends connect to. Joints without an override follow the model-wide option ` +
      `(currently <b>${npEsc(pzw)}</b>).`));
    const g1 = npGroup("Joints");
    list = npEl("div", "asn-jlist np-jlist"); list.id = "pzJoints";
    const add = npEl("div", "cd-inline asn-jadd");
    const xyz = [null, null, null];
    const ins = ["X", "Y", "Z"].map((ax, k) => npNumInput("length", null, { id: "pzAdd" + ax, placeholder: ax, onSet: v => { xyz[k] = v; } }));
    add.append(npEl("span", "cd-unit", npEsc(U.label("length"))), ...ins,
      npBtn("+ Joint", "btn-small", () => {
        if (!xyz.every(npIsNum)) return;
        if (!joints.some(j => npSamePt(j.p, xyz))) joints.push({ p: xyz.slice(), on: true });
        renderJoints(); syncNote();
      }, "Add a joint by its coordinates", "pzAddJoint"),
      npBtn("Clear", "btn-small", () => { joints.length = 0; renderJoints(); syncNote(); }, "", "pzClearJoints"));
    g1.append(list, add);
    body.appendChild(g1);

    const g2 = npGroup("Panel zone property");
    const r2 = npEl("div", "np-pzprops");
    const opts = [["", "Remove override (use model default)"], ...PZ_PROPS];
    for (const [v, t] of opts)
      r2.appendChild(npRadio("pzProp", v, st.mode === v, t, () => { st.mode = v; draw(); }, "pzProp-" + (v || "none")));
    g2.appendChild(r2);
    if (st.mode === null) g2.appendChild(npEl("p", "muted cd-note", "The listed joints have different assignments — pick one to apply to all of them (OK without a choice changes nothing)."));
    if (st.mode === "elastic")
      g2.appendChild(npRow("Doubler plate thickness", npNumInput("dim", st.doubler_t, { id: "pzDoubler", min: 0, onSet: v => { st.doubler_t = v; renderJoints(); } }), "dim",
        "K<sub>θ</sub> = G·d<sub>c</sub>·d<sub>b</sub>·(t<sub>p</sub> + t<sub>doubler</sub>)"));
    if (st.mode === "spring")
      g2.appendChild(npRow("Rotational stiffness k", npNumInput("rot_stiffness", st.k, { id: "pzK", gt: 0, allowEmpty: true, onSet: v => { st.k = v; renderJoints(); } }), "rot_stiffness",
        "about global rx and ry"));
    if (st.mode === "from_column")
      g2.appendChild(npEl("p", "muted cd-note", "K<sub>θ</sub> = G·d<sub>c</sub>·d<sub>b</sub>·t<sub>p</sub>: deepest vertical column (d<sub>c</sub> = h, t<sub>p</sub> = b) and deepest beam (d<sub>b</sub>) at the joint — the model-wide scissors rule."));
    body.appendChild(g2);
    if (st.mode) {
      const g3 = npGroup("Connectivity");
      const r3 = npEl("div", "cd-inline");
      for (const [v, t] of Object.entries(PZ_CONN_LABEL))
        r3.appendChild(npRadio("pzConn", v, st.connectivity === v, t, () => { st.connectivity = v; renderJoints(); }, "pzConn-" + v));
      g3.appendChild(r3);
      g3.appendChild(npEl("p", "muted cd-note", "Member ends of these kinds connect to the panel (duplicate node); all other member ends stay on the joint. Support joints are skipped by the engine."));
      body.appendChild(g3);
    }
    body.appendChild(err);
    renderJoints(); syncNote();
  };
  function syncNote() { if (fb) fb.note.textContent = `${joints.filter(j => j.on).length} joint(s)`; }
  const commit = () => {
    const pts = joints.filter(j => j.on).map(j => j.p);
    if (!pts.length) { dlg.close(); return false; }
    if (st.mode === null) return true;                 // mixed and nothing picked → no change
    if (st.mode === "spring" && !(npIsNum(st.k) && st.k > 0)) { npShowError(err, "Enter a rotational stiffness k > 0."); return false; }
    if (st.mode === "elastic" && !(npIsNum(st.doubler_t) && st.doubler_t >= 0)) { npShowError(err, "Doubler thickness must be ≥ 0."); return false; }
    const old = m.joint_panel_zones || [];
    let next;
    if (!st.mode) next = old.filter(e => !pts.some(p => npSamePt(p, e.point)));
    else {
      const ends = pzJointEnds(m);
      const ok = pts.filter(p => !pzJointCheck(m, entryOf(p), ends));
      if (!ok.length) { npShowError(err, "None of the listed joints can take this panel zone (see the reasons)."); return false; }
      next = old.map(e => {
        const p = ok.find(q => npSamePt(q, e.point));
        return p ? pzNorm({ ...entryOf(e.point) }) : e;
      });
      for (const p of ok) if (!old.some(e => npSamePt(e.point, p))) next.push(pzNorm(entryOf(p)));
    }
    if (JSON.stringify(next) === JSON.stringify(old)) return true;
    if (next.length) m.joint_panel_zones = next; else delete m.joint_panel_zones;
    ctx.markDirty();
    ctx.onChange && ctx.onChange("joint_panel_zones");
    afterModelEdit(sky);
    return true;
  };
  const fb = npFootBar("", [
    npBtn("Cancel", "", () => dlg.close(), "", "pzCancel"),
    npBtn("OK", "btn-run", () => { if (commit()) dlg.close(); }, "", "pzOk"),
  ]);
  const dlg = npDialog("pzModal", { title: "Assign Joint Panel Zone", glyph: "diaph", wide: true, body, foot: fb.wrap, onUnits: draw });
  draw();
  return dlg;
}

/* ================================================================
   view overlays (plan / elevation SVG labels layer, 3D canvas)
   ================================================================ */
function afterModelEdit(sky) {
  try {
    if (sky.planEditor) sky.planEditor.refresh();
    if (sky.elevEditor) sky.elevEditor.refresh();
    if (sky.viewer) sky.viewer._dirty = true;
    if (sky.renderProps) sky.renderProps();
    const modal = document.getElementById("sectionModal");
    if (modal && !modal.classList.contains("hidden") && sky.renderSectionMgr) sky.renderSectionMgr();
  } catch (e) { console.error("np refresh", e); }
}

function badge(layer, px, py, text, color, ref) {
  const w = 8 + text.length * 6;
  layer.appendChild(svgEl("rect", { x: px - w / 2, y: py - 6.5, width: w, height: 13, rx: 6.5,
    fill: "rgba(79,193,201,0.16)", stroke: color, "stroke-width": 1, "data-np": ref, class: "np-badge-svg" }));
  const t = svgEl("text", { x: px, y: py + 0.5, fill: color, "font-size": 8.5, "font-weight": 700,
    "text-anchor": "middle", "dominant-baseline": "central", "font-family": "inherit", "data-np": ref });
  t.textContent = text;
  layer.appendChild(t);
}
function pzSquare(layer, px, py, e) {
  const r = svgEl("rect", { x: px - 4.5, y: py - 4.5, width: 9, height: 9, fill: "rgba(196,107,224,0.25)",
    stroke: PZ_COLOR, "stroke-width": 1.6, class: "pz-marker", "data-pz": e.point.join(",") });
  const t = svgEl("title");
  t.textContent = `Panel zone · ${e.property}${e.property === "spring" ? " k " + U.fmtU("rot_stiffness", e.k, 0) : ""}`;
  r.appendChild(t);
  layer.appendChild(r);
}

function planOverlay(ed) {
  const m = ed.opts.getModel();
  if (!m || !m.members) return;
  const story = ed.opts.getStory();
  const st = (m.stories || []).find(s => s.name === story);
  const g = svgEl("g", { class: "np-layer", "pointer-events": "none" });
  for (const mm of m.members) {
    if (mm.story !== story || !isNpSec(m.sections[mm.section])) continue;
    const [px, py] = ed.toScreen((mm.pi[0] + mm.pj[0]) / 2, (mm.pi[1] + mm.pj[1]) / 2);
    badge(g, px, mm.kind === "column" ? py + 14 : py - 11, "NP", NP_COLOR, mm.uid);
  }
  if (st)
    for (const e of m.joint_panel_zones || []) {
      if (Math.abs(e.point[2] - st.elevation) > 1e-6) continue;
      const [px, py] = ed.toScreen(e.point[0], e.point[1]);
      pzSquare(g, px, py, e);
    }
  if (g.childNodes.length) ed.gLabels.appendChild(g);
}

function elevOverlay(ed) {
  const m = ed.opts.getModel();
  if (!m || !m.members || !ed.plane || !ed.plane()) return;
  const g = svgEl("g", { class: "np-layer", "pointer-events": "none" });
  for (const mm of m.members) {
    const sec = m.sections[mm.section];
    if (!isNpSec(sec) || !ed.inPlane(mm.pi) || !ed.inPlane(mm.pj)) continue;
    const a = [ed.sOf(mm.pi), mm.pi[2]], b = [ed.sOf(mm.pj), mm.pj[2]];
    const L = memLen(mm);
    const lay = npLayout(m, sec, L);
    if (lay.segs.length && lay.segs.every(s => s.p0 && s.p1)) {
      const d = [b[0] - a[0], b[1] - a[1]];
      const dl = Math.hypot(d[0], d[1]);
      if (dl > 1e-9) {
        const t = [d[0] / dl, d[1] / dl], nrm = [-t[1], t[0]];
        const pts = profile(lay, L, 24);
        const up = [], dn = [];
        for (const [x, h] of pts) {
          const f = x / L;
          const c = [a[0] + d[0] * f, a[1] + d[1] * f];
          up.push(ed.toScreen(c[0] + nrm[0] * h / 2, c[1] + nrm[1] * h / 2));
          dn.push(ed.toScreen(c[0] - nrm[0] * h / 2, c[1] - nrm[1] * h / 2));
        }
        const poly = up.concat(dn.reverse()).map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
        g.appendChild(svgEl("polygon", { points: poly, fill: "rgba(79,193,201,0.13)", stroke: NP_COLOR,
          "stroke-width": 1, "stroke-opacity": 0.75, class: "np-taper", "data-np": mm.uid }));
      }
    }
    const [px, py] = ed.toScreen((a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
    badge(g, px + (mm.kind === "column" ? 16 : 0), py - (mm.kind === "column" ? 0 : 16), "NP", NP_COLOR, mm.uid);
  }
  for (const e of m.joint_panel_zones || []) {
    if (!ed.inPlane(e.point)) continue;
    const [px, py] = ed.toScreen(ed.sOf(e.point), e.point[2]);
    pzSquare(g, px, py, e);
  }
  if (g.childNodes.length) ed.gLabels.appendChild(g);
}

function draw3d(ctx, P, viewer) {
  const m = viewer.model;
  if (!m || !m.members) return;
  const proj = p => { const pc = P.toCam(p); return pc[2] < P.near ? null : P.proj(pc); };
  ctx.save();
  ctx.font = "700 9px -apple-system, 'Segoe UI', sans-serif";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  for (const mm of m.members) {
    const sec = m.sections[mm.section];
    if (!isNpSec(sec)) continue;
    const L = memLen(mm);
    const lay = npLayout(m, sec, L);
    const ax = [(mm.pj[0] - mm.pi[0]) / L, (mm.pj[1] - mm.pi[1]) / L, (mm.pj[2] - mm.pi[2]) / L];
    // depth direction: local 2 (vertical for beams; rotated X for columns)
    let dv;
    if (isVertical(mm)) { const a = (mm.angle || 0) * Math.PI / 180; dv = [Math.cos(a), Math.sin(a), 0]; }
    else { const dot = ax[2]; dv = [-dot * ax[0], -dot * ax[1], 1 - dot * ax[2]]; const n = Math.hypot(...dv) || 1; dv = dv.map(v => v / n); }
    if (lay.segs.length && lay.segs.every(s => s.p0 && s.p1)) {
      const pts = profile(lay, L, 16);
      const up = [], dn = [];
      let ok = true;
      for (const [x, h] of pts) {
        const c = [mm.pi[0] + ax[0] * x, mm.pi[1] + ax[1] * x, mm.pi[2] + ax[2] * x];
        const u = proj([c[0] + dv[0] * h / 2, c[1] + dv[1] * h / 2, c[2] + dv[2] * h / 2]);
        const d = proj([c[0] - dv[0] * h / 2, c[1] - dv[1] * h / 2, c[2] - dv[2] * h / 2]);
        if (!u || !d) { ok = false; break; }
        up.push(u); dn.push(d);
      }
      if (ok) {
        ctx.beginPath();
        up.forEach((q, i) => (i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y)));
        for (let i = dn.length - 1; i >= 0; i--) ctx.lineTo(dn[i].x, dn[i].y);
        ctx.closePath();
        ctx.fillStyle = "rgba(79,193,201,0.22)"; ctx.fill();
        ctx.strokeStyle = "rgba(79,193,201,0.8)"; ctx.lineWidth = 1; ctx.stroke();
      }
    }
    const mid = proj([(mm.pi[0] + mm.pj[0]) / 2, (mm.pi[1] + mm.pj[1]) / 2, (mm.pi[2] + mm.pj[2]) / 2]);
    if (mid) {
      ctx.fillStyle = "rgba(13,17,23,0.75)";
      ctx.beginPath(); ctx.rect(mid.x - 10, mid.y - 18, 20, 12); ctx.fill();
      ctx.strokeStyle = NP_COLOR; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = NP_COLOR; ctx.fillText("NP", mid.x, mid.y - 11.5);
    }
  }
  for (const e of m.joint_panel_zones || []) {
    const q = proj(e.point);
    if (!q) continue;
    ctx.fillStyle = "rgba(196,107,224,0.3)"; ctx.strokeStyle = PZ_COLOR; ctx.lineWidth = 1.6;
    ctx.beginPath(); ctx.rect(q.x - 4.5, q.y - 4.5, 9, 9); ctx.fill(); ctx.stroke();
  }
  ctx.restore();
}

/* ================================================================
   Properties panel — nonprismatic variation of the selection
   ================================================================ */
/** What the NP block of the Properties panel depends on. */
const propsSig = (m, members) => [...new Set(members.map(mm => mm.section))].filter(n => isNpSec(m.sections[n])).join("|");

export function npDecorateProps(sky, box, sel) {
  const members = (sel && sel.members) || [];
  const m = sky.store.model;
  if (!m || !members.length) return;
  const anchor = box.querySelector("#propFrameSection");
  const field = anchor && anchor.closest(".field");
  if (!field) return;
  const names = [...new Set(members.map(mm => mm.section))];
  const npNames = names.filter(n => isNpSec(m.sections[n]));
  const wrap = npEl("div", "np-props"); wrap.id = "npPropsInfo";
  wrap.dataset.sig = propsSig(m, members);
  if (npNames.length === 1 && names.length === 1) {
    const name = npNames[0], sec = m.sections[name], mm = members[0];
    const L = memLen(mm);
    const lay = npLayout(m, sec, L);
    const rows = lay.segs.map((g, i) => `<tr><td>${i + 1}</td><td class="txt">${npEsc(g.sg.start_section)} → ${npEsc(g.sg.end_section)}</td>` +
      `<td>${U.fmt("length", g.x1 - g.x0, 2)}</td><td class="txt">${npEsc(g.sg.EI33)}</td><td class="txt">${npEsc(g.sg.EI22)}</td></tr>`).join("");
    const p0 = npPropsAt(lay, 0), p1 = npPropsAt(lay, L);
    wrap.innerHTML = `<div class="np-props-head"><span class="np-badge">NP</span> <b>${npEsc(name)}</b> <span class="muted">· nonprismatic · ${npVarying(m, sec) ? `${sec.subdivisions ?? NP_DEFAULT_SUB} sub-elements / varying segment` : "uniform"}</span></div>` +
      (lay.err ? `<p class="field-error">${npEsc(lay.err)}</p>` : "") +
      (members.length > 1 ? `<p class="muted np-note">Variation shown on ${npEsc(mm.uid)} (L = ${npEsc(U.fmtU("length", L, 2))}).</p>` : "") +
      `<div class="np-props-svg">${npPreviewSvg(m, sec, L, { width: 300, height: 150, id: "npPropsSvg" })}</div>` +
      `<div class="np-props-scroll"><table class="data-table np-props-table" id="npPropsTable"><thead><tr><th>#</th><th class="txt">Sections</th><th>L ${npEsc(U.label("length"))}</th><th class="txt">EI33</th><th class="txt">EI22</th></tr></thead><tbody>${rows}</tbody></table></div>` +
      (p0 && p1 ? `<p class="muted np-note">I33: ${npEsc(U.sci("inertia", p0.I33))} (i) → ${npEsc(U.sci("inertia", p1.I33))} (j) ${npEsc(U.label("inertia"))} · depth ${npEsc(U.fmt("dim", p0.h, 3))} → ${npEsc(U.fmt("dim", p1.h, 3))} ${npEsc(U.label("dim"))}</p>` : "");
    const ed = npBtn("Edit Nonprismatic Section…", "btn-small", () => openNpSection(npMakeCtx(sky), sky, name), "", "npPropsEdit");
    wrap.appendChild(ed);
  } else if (npNames.length) {
    wrap.innerHTML = `<p class="muted np-note"><span class="np-badge">NP</span> ${npNames.length} nonprismatic section(s) in the selection: ${npNames.map(npEsc).join(", ")}.</p>`;
  } else return;
  field.after(wrap);
  // panel-zone overrides at the selected joints
  const pts = npSelectionJoints(m, sky.store.selection || []);
  const pz = (m.joint_panel_zones || []).filter(e => pts.some(p => npSamePt(p, e.point)));
  if (pz.length) wrap.appendChild(npEl("p", "muted np-note", `<span class="pz-chip"></span> ${pz.length} panel-zone override(s) at the selection's joints.`));
}

/* ================================================================
   install
   ================================================================ */
function ensureNpCss() {
  if (document.querySelector("link[data-np-css]")) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/npsect.css"; l.setAttribute("data-np-css", "1");
  document.head.appendChild(l);
}

export function initNp(sky) {
  if (!sky) return;
  ensureNpCss();
  Object.assign(sky, {
    openNonprismatic: name => openNpSection(npMakeCtx(sky), sky, name || null),
    openPanelZone: () => openPanelZone(npMakeCtx(sky), sky),
    npSectionRow: (name, s) => npSectionRow(sky, name, s),
    npDecorateProps: (box, sel) => { try { npDecorateProps(sky, box, sel); } catch (e) { console.error("np props", e); } },
    npValidate: m => npValidateModel(m) || pzValidateModel(m),
    npPreviewSvg, npLayout, npPropsAt,
    closeNpDialog: npCloseDialog,
  });
  // Section Manager: "+ Nonprismatic…" next to the frame-section "+ Add"
  const addBtn = document.getElementById("addFrameSection");
  if (addBtn && !document.getElementById("addNpSection")) {
    const b = npBtn("+ Nonprismatic…", "btn-small", () => sky.openNonprismatic(), "Add a nonprismatic (tapered / haunched) section", "addNpSection");
    addBtn.after(b);
  }
  // view overlays (hooks in draw.js / elev.js / viewer3d.js)
  if (sky.planEditor) sky.planEditor.npOverlay = planOverlay;
  if (sky.elevEditor) sky.elevEditor.npOverlay = elevOverlay;
  if (sky.viewer) sky.viewer.npOverlay = draw3d;
  const redraw = () => {
    try {
      if (sky.planEditor && sky.planEditor.w) sky.planEditor._renderLabels();
      if (sky.elevEditor && sky.elevEditor.w) sky.elevEditor._renderLabels();
      if (sky.viewer) sky.viewer._dirty = true;
    } catch (e) { /* views not ready */ }
  };
  // drop panel-zone overrides whose joint lost every member (e.g. after a delete)
  document.addEventListener("sky:model-changed", () => {
    const m = sky.store.model;
    if (m && Array.isArray(m.joint_panel_zones) && m.joint_panel_zones.length) {
      const ends = pzJointEnds(m);
      const keep = m.joint_panel_zones.filter(e => ends.has(pkey(e.point)));
      if (keep.length !== m.joint_panel_zones.length) {
        if (keep.length) m.joint_panel_zones = keep; else delete m.joint_panel_zones;
        sky.toast && sky.toast("Panel zones", "Removed panel-zone override(s) at joints that no longer have members.", "info", 4000);
      }
    }
    redraw();
    // the Properties panel's section select changes the section without a
    // re-render: refresh it when the NP block no longer matches
    try {
      if (m && document.getElementById("propFrameSection") && sky.selObjects && sky.renderProps) {
        const sig = propsSig(m, sky.selObjects().members || []);
        const cur = document.getElementById("npPropsInfo");
        if ((cur ? cur.dataset.sig : "") !== sig) sky.renderProps();
      }
    } catch (e) { /* panel not ready */ }
  });
  U.onUnitsChange(redraw);
}
