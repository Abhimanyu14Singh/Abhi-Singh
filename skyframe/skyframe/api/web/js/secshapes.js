/* SkyFrame — parametric frame-section shapes (v1.21), pure functions.

   A line-by-line port of skyframe/core/shape_props.py (CONTRACT "Steel
   section database and parametric shapes"): exact properties from the
   dimensions of every section `type`, the shear-area rules, the principal
   axes of unsymmetric sections, plus
     * SS_TYPES      UI metadata (labels, dimension fields, defaults)
     * ssSection     a model section dict from type + dims (A, I33, I22, J,
                     b, h, As2, As3, shape, dims, principal)
     * ssFromRecord  a model section dict from a steel-database record
                     (mirror of skyframe.core.steel_db.section_from_db)
     * ssPreview     the secpreview.js drawing hook (shape + dims →
                     outline fields / polygon)
   Coordinates: x = local 3, y = local 2; standard placements as in the
   Python module. Everything SI (m); the model stays SI. */

import { CF_TYPES as wbCfTypes, CF_SS_TYPES as wbCfSsTypes, CF_REQ as wbCfReq, CF_OPT as wbCfOpt, CF_SHEAR as wbCfShear,
  cfIs as wbCfIs, cfCheck as wbCfCheck, cfProps as wbCfProps, cfShearAreas as wbCfShearAreas, cfPreview as wbCfPreview } from "./cfshapes.js";   // Wave B cold-formed (js/cfshapes.js)

const PI = Math.PI;

export const SS_TYPES = [
  { key: "I", group: "Steel", label: "I / Wide Flange", dims: [["d", "Depth, d"], ["bf", "Flange Width, bf"], ["tf", "Flange Thickness, tf"], ["tw", "Web Thickness, tw"]], def: { d: 0.4, bf: 0.2, tf: 0.015, tw: 0.01 } },
  { key: "I_mono", group: "Steel", label: "I — Unequal Flanges", dims: [["d", "Depth, d"], ["bf_top", "Top Flange Width"], ["tf_top", "Top Flange Thickness"], ["bf_bot", "Bottom Flange Width"], ["tf_bot", "Bottom Flange Thickness"], ["tw", "Web Thickness, tw"]], def: { d: 0.5, bf_top: 0.25, tf_top: 0.016, bf_bot: 0.2, tf_bot: 0.014, tw: 0.01 } },
  { key: "channel", group: "Steel", label: "Channel", dims: [["d", "Depth, d"], ["bf", "Flange Width, bf"], ["tf", "Flange Thickness, tf"], ["tw", "Web Thickness, tw"]], def: { d: 0.3, bf: 0.09, tf: 0.012, tw: 0.008 } },
  { key: "angle", group: "Steel", label: "Angle", dims: [["d", "Vertical Leg, d"], ["b", "Horizontal Leg, b"], ["t", "Thickness, t"]], def: { d: 0.15, b: 0.1, t: 0.012 } },
  { key: "double_angle", group: "Steel", label: "Double Angle", dims: [["d", "Back-to-back Leg, d"], ["b", "Outstanding Leg, b"], ["t", "Thickness, t"], ["s", "Gap Between Angles, s", true]], def: { d: 0.15, b: 0.1, t: 0.012, s: 0.01 } },
  { key: "tee", group: "Steel", label: "Tee", dims: [["d", "Depth, d"], ["bf", "Flange Width, bf"], ["tf", "Flange Thickness, tf"], ["tw", "Stem Thickness, tw"]], def: { d: 0.2, bf: 0.2, tf: 0.015, tw: 0.01 } },
  { key: "rect_tube", group: "Steel", label: "Rectangular Tube (HSS / RHS)", dims: [["d", "Depth, d"], ["b", "Width, b"], ["t", "Wall Thickness, t"], ["r_out", "Outside Corner Radius", true]], def: { d: 0.3, b: 0.2, t: 0.01, r_out: 0.02 } },
  { key: "round_tube", group: "Steel", label: "Round Tube / Pipe", dims: [["D", "Outside Diameter, D"], ["t", "Wall Thickness, t"]], def: { D: 0.3, t: 0.01 } },
  { key: "rect_solid", group: "Steel", label: "Solid Rectangle / Plate", dims: [["d", "Depth, d"], ["b", "Width, b"]], def: { d: 0.1, b: 0.05 } },
  { key: "round_solid", group: "Steel", label: "Solid Circle (steel or concrete)", dims: [["D", "Diameter, D"]], def: { D: 0.5 } },
  { key: "plate", group: "Steel", label: "Plate", hidden: true, dims: [["d", "Thickness, d"], ["b", "Width, b"]], def: { d: 0.02, b: 0.3 } },
  { key: "rect", group: "Concrete", label: "Rectangular (b × h)", dims: null },
  { key: "round_solid_c", group: "Concrete", label: "Circle", alias: "round_solid", dims: [["D", "Diameter, D"]], def: { D: 0.5 } },
  { key: "tee_concrete", group: "Concrete", label: "T-Beam", dims: [["bf", "Flange Width, bf"], ["hf", "Flange Depth, hf"], ["bw", "Web Width, bw"], ["h", "Total Depth, h"]], def: { bf: 1.2, hf: 0.15, bw: 0.3, h: 0.6 } },
  { key: "L_concrete", group: "Concrete", label: "L-Beam", dims: [["bf", "Flange Width, bf"], ["hf", "Flange Depth, hf"], ["bw", "Web Width, bw"], ["h", "Total Depth, h"]], def: { bf: 0.8, hf: 0.15, bw: 0.3, h: 0.6 } },
  { key: "general", group: "Other", label: "General (enter A, I, J)", dims: null },
];
SS_TYPES.splice(SS_TYPES.findIndex(t => t.key === "rect"), 0, ...wbCfSsTypes);   // Wave B cold-formed shapes (js/cfshapes.js)
export const SS_PARAM = ["I", "I_mono", "channel", "angle", "double_angle", "tee", "rect_tube", "round_tube", "rect_solid", "plate", "round_solid", "tee_concrete", "L_concrete", ...wbCfTypes];
export const SS_LABEL = Object.fromEntries(SS_TYPES.filter(t => !t.alias).map(t => [t.key, t.label]));
export const ssTypeInfo = key => SS_TYPES.find(t => t.key === key) || SS_TYPES.find(t => t.alias === key) || null;
export const SHEAR_AREA_FORMULAS = {
  I: "As2 = d·tw; As3 = 5/3·bf·tf", I_mono: "As2 = d·tw; As3 = 5/6·(bf_top·tf_top + bf_bot·tf_bot)",
  channel: "As2 = d·tw; As3 = 5/3·bf·tf", tee: "As2 = d·tw; As3 = 5/6·bf·tf", angle: "As2 = 5/6·d·t; As3 = 5/6·b·t",
  double_angle: "As2 = 5/3·d·t; As3 = 5/3·b·t", rect_tube: "As2 = 2·d·t; As3 = 2·b·t", round_tube: "As2 = As3 = A/2",
  rect_solid: "As2 = As3 = 5/6·A", plate: "As2 = As3 = 5/6·A", round_solid: "As2 = As3 = 0.9·A",
  tee_concrete: "As2 = 5/6·bw·h; As3 = 5/6·bf·hf", L_concrete: "As2 = 5/6·bw·h; As3 = 5/6·bf·hf",
  ...wbCfShear,   // Wave B cold-formed
};

const REQ = {
  I: ["d", "bf", "tf", "tw"], I_mono: ["d", "bf_top", "tf_top", "bf_bot", "tf_bot", "tw"], channel: ["d", "bf", "tf", "tw"],
  angle: ["d", "b", "t"], double_angle: ["d", "b", "t"], tee: ["d", "bf", "tf", "tw"], rect_tube: ["d", "b", "t"],
  round_tube: ["D", "t"], rect_solid: ["d", "b"], plate: ["d", "b"], round_solid: ["D"],
  tee_concrete: ["bf", "hf", "bw", "h"], L_concrete: ["bf", "hf", "bw", "h"],
  ...wbCfReq,   // Wave B cold-formed
};
const OPT = { double_angle: ["s"], rect_tube: ["r_out"], ...wbCfOpt };

/** Checked dims or a plain-language error string. */
export function ssValidate(typ, dims) {
  if (!REQ[typ]) return { error: `Unknown section type ${typ}.` };
  const g = {};
  for (const k of REQ[typ]) {
    const v = dims ? dims[k] : undefined;
    if (typeof v !== "number" || !isFinite(v)) return { error: `Enter ${k}.` };
    if (!(v > 0)) return { error: `${k} must be greater than zero.` };
    g[k] = v;
  }
  for (const k of OPT[typ] || []) {
    const v = dims ? dims[k] : undefined;
    if (v == null || v === "") continue;
    if (typeof v !== "number" || !isFinite(v) || v < 0) return { error: `${k} must be zero or more.` };
    g[k] = v;
  }
  let bad = "";
  if (typ === "I" || typ === "channel" || typ === "tee") {
    if ((typ === "tee" ? 1 : 2) * g.tf >= g.d) bad = "The flanges are thicker than the depth — reduce tf.";
    else if (g.tw >= g.bf) bad = "The web is wider than the flange — reduce tw.";
  } else if (typ === "I_mono") {
    if (g.tf_top + g.tf_bot >= g.d) bad = "The flanges are thicker than the depth — reduce tf.";
    else if (g.tw >= Math.min(g.bf_top, g.bf_bot)) bad = "The web is wider than a flange — reduce tw.";
  } else if (typ === "angle" || typ === "double_angle") {
    if (g.t >= Math.min(g.d, g.b)) bad = "The thickness must be smaller than both legs.";
  } else if (typ === "rect_tube") {
    if (2 * g.t >= Math.min(g.d, g.b)) bad = "The wall is thicker than half the tube — reduce t.";
    else if ((g.r_out || 0) > Math.min(g.d, g.b) / 2) bad = "The corner radius exceeds half the tube.";
  } else if (typ === "round_tube") {
    if (2 * g.t >= g.D) bad = "The wall is thicker than the radius — reduce t.";
  } else if (typ === "tee_concrete" || typ === "L_concrete") {
    if (g.hf >= g.h) bad = "The flange is deeper than the whole section.";
    else if (g.bw > g.bf) bad = "The web is wider than the flange.";
  } else if (wbCfIs(typ)) bad = wbCfCheck(typ, g);   // Wave B cold-formed
  return bad ? { error: bad } : { g };
}

/* ---- exact integrals of axis-aligned rectangles [x0, x1, y0, y1] */
function plastic(rects, k) {
  const spans = rects.map(r => k === 0 ? [r[0], r[1], r[3] - r[2]] : [r[2], r[3], r[1] - r[0]]);
  const total = spans.reduce((s, [lo, hi, w]) => s + (hi - lo) * w, 0);
  const below = p => spans.reduce((s, [lo, hi, w]) => s + w * Math.min(Math.max(p - lo, 0), hi - lo), 0);
  const pts = [...new Set(spans.flatMap(([lo, hi]) => [lo, hi]))].sort((a, b) => a - b);
  let p = pts[pts.length - 1];
  for (let i = 0; i + 1 < pts.length; i++) {
    const a = pts[i], b = pts[i + 1], fa = below(a), fb = below(b);
    if (fb >= total / 2) { p = fb > fa ? a + (total / 2 - fa) / (fb - fa) * (b - a) : a; break; }
  }
  let Z = 0;
  for (const [lo, hi, w] of spans) {
    if (p <= lo) Z += w * ((hi - p) ** 2 - (lo - p) ** 2) / 2;
    else if (p >= hi) Z += w * ((p - lo) ** 2 - (p - hi) ** 2) / 2;
    else Z += w * ((p - lo) ** 2 + (hi - p) ** 2) / 2;
  }
  return Z;
}
function rectSet(rects) {
  let A = 0, Sx = 0, Sy = 0;
  for (const [x0, x1, y0, y1] of rects) { const a = (x1 - x0) * (y1 - y0); A += a; Sx += a * (x0 + x1) / 2; Sy += a * (y0 + y1) / 2; }
  const cx = Sx / A, cy = Sy / A;
  let I33 = 0, I22 = 0, I23 = 0;
  for (const [x0, x1, y0, y1] of rects) {
    const w = x1 - x0, h = y1 - y0, a = w * h, dx = (x0 + x1) / 2 - cx, dy = (y0 + y1) / 2 - cy;
    I33 += w * h ** 3 / 12 + a * dy * dy; I22 += h * w ** 3 / 12 + a * dx * dx; I23 += a * dx * dy;
  }
  const xs = rects.flatMap(r => [r[0], r[1]]), ys = rects.flatMap(r => [r[2], r[3]]);
  const xmin = Math.min(...xs), xmax = Math.max(...xs), ymin = Math.min(...ys), ymax = Math.max(...ys);
  return { A, cx, cy, I33, I22, I23, c2: Math.max(ymax - cy, cy - ymin), c3: Math.max(xmax - cx, cx - xmin),
    Z33: plastic(rects, 2), Z22: plastic(rects, 0), xmin, xmax, ymin, ymax };
}
const roarkJ = (b, h) => { const a = Math.max(b, h) / 2, c = Math.min(b, h) / 2; return a * c ** 3 * (16 / 3 - 3.36 * (c / a) * (1 - c ** 4 / (12 * a ** 4))); };
function roundRect(b, h, r) {
  const spA = (1 - PI / 4) * r * r, spM = r ** 3 * (5 / 6 - PI / 4), spI = r ** 4 * (1 - 5 * PI / 16);
  const cI = half => 4 * (half * half * spA - 2 * half * spM + spI);
  const Z = (bb, hh) => bb * hh * hh / 4 - 4 * (hh / 2 - r) * (1 - PI / 4) * r * r - 2 / 3 * r ** 3;
  return { A: b * h - 4 * spA, I33: b * h ** 3 / 12 - cI(h / 2), I22: h * b ** 3 / 12 - cI(b / 2), Z33: Z(b, h), Z22: Z(h, b) };
}

/** Outline rectangles of the rectangle-built types (standard placement). */
export function ssRects(typ, g) {
  switch (typ) {
    case "I": { const { d, bf, tf, tw } = g; return [[-bf / 2, bf / 2, 0, tf], [-tw / 2, tw / 2, tf, d - tf], [-bf / 2, bf / 2, d - tf, d]]; }
    case "I_mono": { const { d, tw, bf_top: bt, tf_top: tt, bf_bot: bb, tf_bot: tb } = g; return [[-bb / 2, bb / 2, 0, tb], [-tw / 2, tw / 2, tb, d - tt], [-bt / 2, bt / 2, d - tt, d]]; }
    case "channel": { const { d, bf, tf, tw } = g; return [[0, bf, 0, tf], [0, tw, tf, d - tf], [0, bf, d - tf, d]]; }
    case "angle": { const { d, b, t } = g; return [[0, b, 0, t], [0, t, t, d]]; }
    case "double_angle": {
      const { d, b, t } = g, s = g.s || 0, out = [];
      for (const sg of [-1, 1]) {
        const xa = sg * s / 2, xb = sg * (s / 2 + b), xw = sg * (s / 2 + t);
        out.push([Math.min(xa, xb), Math.max(xa, xb), d - t, d], [Math.min(xa, xw), Math.max(xa, xw), 0, d - t]);
      }
      return out;
    }
    case "tee": { const { d, bf, tf, tw } = g; return [[-tw / 2, tw / 2, 0, d - tf], [-bf / 2, bf / 2, d - tf, d]]; }
    case "rect_solid": case "plate": return [[-g.b / 2, g.b / 2, -g.d / 2, g.d / 2]];
    case "tee_concrete": { const { bf, hf, bw, h } = g; return [[-bw / 2, bw / 2, 0, h - hf], [-bf / 2, bf / 2, h - hf, h]]; }
    case "L_concrete": { const { bf, hf, bw, h } = g; return [[0, bw, 0, h - hf], [0, bf, h - hf, h]]; }
  }
  return null;
}

function torsion(typ, g, cx, cy) {
  if (typ === "I") { const { d, bf, tf, tw } = g, ho = d - tf; return [(2 * bf * tf ** 3 + ho * tw ** 3) / 3, tf * bf ** 3 * ho * ho / 24, [cx, cy]]; }
  if (typ === "I_mono") {
    const { d, tw, bf_top: bt, tf_top: tt, bf_bot: bb, tf_bot: tb } = g, ho = d - (tt + tb) / 2, It = tt * bt ** 3 / 12, Ib = tb * bb ** 3 / 12;
    return [(bt * tt ** 3 + bb * tb ** 3 + ho * tw ** 3) / 3, ho * ho * It * Ib / (It + Ib), [cx, d - tt / 2 - ho * Ib / (It + Ib)]];
  }
  if (typ === "channel") {
    const { d, bf, tf, tw } = g, bp = bf - tw / 2, hp = d - tf;
    const e = 3 * bp * bp * tf / (6 * bp * tf + hp * tw);
    return [(2 * bp * tf ** 3 + hp * tw ** 3) / 3, tf * bp ** 3 * hp * hp / 12 * (3 * bp * tf + 2 * hp * tw) / (6 * bp * tf + hp * tw), [tw / 2 - e, cy]];
  }
  if (typ === "tee") { const { d, bf, tf, tw } = g, dp = d - tf / 2; return [(bf * tf ** 3 + dp * tw ** 3) / 3, bf ** 3 * tf ** 3 / 144 + dp ** 3 * tw ** 3 / 36, [cx, d - tf / 2]]; }
  if (typ === "angle") { const { d, b, t } = g, dp = d - t / 2, bp = b - t / 2; return [(dp + bp) * t ** 3 / 3, t ** 3 * (bp ** 3 + dp ** 3) / 36, [t / 2, t / 2]]; }
  if (typ === "double_angle") { const { d, b, t } = g, dp = d - t / 2, bp = b - t / 2; return [2 * (dp + bp) * t ** 3 / 3, 0, [cx, d - t / 2]]; }
  if (typ === "rect_solid" || typ === "plate") return [roarkJ(g.b, g.d), 0, [cx, cy]];
  if (typ === "tee_concrete" || typ === "L_concrete") {
    const { bf, hf, bw, h } = g, nov = typ === "tee_concrete" ? 2 : 1, ov = (bf - bw) / nov;
    const J = Math.max(roarkJ(bf, hf) + roarkJ(bw, h - hf), roarkJ(bw, h) + (ov > 0 ? nov * roarkJ(ov, hf) : 0));
    return [J, 0, [typ === "tee_concrete" ? cx : bw / 2, h - hf / 2]];
  }
  throw new Error(typ);
}

export function ssShearAreas(typ, g, A) {
  switch (typ) {
    case "I": case "channel": return [g.d * g.tw, 5 / 3 * g.bf * g.tf];
    case "I_mono": return [g.d * g.tw, 5 / 6 * (g.bf_top * g.tf_top + g.bf_bot * g.tf_bot)];
    case "tee": return [g.d * g.tw, 5 / 6 * g.bf * g.tf];
    case "angle": return [5 / 6 * g.d * g.t, 5 / 6 * g.b * g.t];
    case "double_angle": return [5 / 3 * g.d * g.t, 5 / 3 * g.b * g.t];
    case "rect_tube": return [2 * g.d * g.t, 2 * g.b * g.t];
    case "round_tube": return [A / 2, A / 2];
    case "rect_solid": case "plate": return [5 / 6 * A, 5 / 6 * A];
    case "round_solid": return [0.9 * A, 0.9 * A];
    case "tee_concrete": case "L_concrete": return [5 / 6 * g.bw * g.h, 5 / 6 * g.bf * g.hf];
  }
  if (wbCfIs(typ)) return wbCfShearAreas(typ, g);   // Wave B cold-formed
  throw new Error(typ);
}

/** (I_major, I_minor, alpha): alpha from local 3 to the major axis (+ toward 2). */
export function ssPrincipal(I33, I22, I23) {
  const c = (I33 + I22) / 2, r = Math.hypot((I33 - I22) / 2, I23);
  return { I_major: c + r, I_minor: c - r, alpha: 0.5 * Math.atan2(-2 * I23, I33 - I22) };
}

function finish(typ, g, A, I33, I22, I23, J, Cw, S33, S22, Z33, Z22, x0, y0, cx, cy, b, h) {
  const [As2, As3] = ssShearAreas(typ, g, A);
  const out = { A, I33, I22, I23, J, Cw, S33, S22, Z33, Z22, As2, As3, r33: Math.sqrt(I33 / A), r22: Math.sqrt(I22 / A), x0, y0, cx, cy, b, h };
  if (typ === "angle" || typ === "L_concrete" || typ === "Z_lipped") Object.assign(out, ssPrincipal(I33, I22, I23));
  return out;
}

/** Exact properties of a parametric shape (SI) or { error }. */
export function ssProps(typ, dims) {
  const v = ssValidate(typ, dims);
  if (v.error) return { error: v.error };
  const g = v.g;
  if (typ === "round_tube") {
    const { D, t } = g, di = D - 2 * t, A = PI * (D * D - di * di) / 4, I = PI * (D ** 4 - di ** 4) / 64, Z = (D ** 3 - di ** 3) / 6;
    return finish(typ, g, A, I, I, 0, 2 * I, 0, I / (D / 2), I / (D / 2), Z, Z, 0, 0, D / 2, D / 2, D, D);
  }
  if (typ === "round_solid") {
    const { D } = g, A = PI * D * D / 4, I = PI * D ** 4 / 64;
    return finish(typ, g, A, I, I, 0, 2 * I, 0, I / (D / 2), I / (D / 2), D ** 3 / 6, D ** 3 / 6, 0, 0, D / 2, D / 2, D, D);
  }
  if (typ === "rect_tube") {
    const { d, b, t } = g, ro = g.r_out || 0, ri = Math.max(ro - t, 0);
    const o = roundRect(b, d, ro), i = roundRect(b - 2 * t, d - 2 * t, ri);
    const rm = Math.max(ro - t / 2, 0), bm = b - t, dm = d - t;
    const Am = bm * dm - (4 - PI) * rm * rm, pm = 2 * (bm + dm) - (8 - 2 * PI) * rm;
    const I33 = o.I33 - i.I33, I22 = o.I22 - i.I22;
    return finish(typ, g, o.A - i.A, I33, I22, 0, 4 * Am * Am * t / pm, 0, I33 / (d / 2), I22 / (b / 2), o.Z33 - i.Z33, o.Z22 - i.Z22, 0, 0, b / 2, d / 2, b, d);
  }
  if (wbCfIs(typ)) { const c = wbCfProps(typ, g); return finish(typ, g, c.A, c.I33, c.I22, c.I23, c.J, c.Cw, c.S33, c.S22, c.Z33, c.Z22, c.x0, c.y0, c.cx, c.cy, c.b, c.h); }   // Wave B
  const p = rectSet(ssRects(typ, g));
  const [J, Cw, sc] = torsion(typ, g, p.cx, p.cy);
  return finish(typ, g, p.A, p.I33, p.I22, p.I23, J, Cw, p.I33 / p.c2, p.I22 / p.c3, p.Z33, p.Z22, sc[0] - p.cx, sc[1] - p.cy, p.cx, p.cy, p.xmax - p.xmin, p.ymax - p.ymin);
}

const numDims = dims => Object.fromEntries(Object.entries(dims || {}).filter(([, v]) => typeof v === "number"));

/** Write the parametric analysis fields of `typ` + `dims` onto section dict s
    (A, I33, I22, J, b, h, As2, As3, shape, dims, principal). Returns the
    props or { error } (s untouched on error). */
export function ssApply(s, typ, dims) {
  const p = ssProps(typ, numDims(dims));
  if (p.error) return p;
  Object.assign(s, { A: p.A, I33: p.I33, I22: p.I22, J: p.J, b: p.b, h: p.h, As2: p.As2, As3: p.As3, shape: typ, dims: { ...dims } });
  if (p.I_major != null) s.principal = { I_major: p.I_major, I_minor: p.I_minor, alpha: p.alpha };
  else { delete s.principal; delete s.use_principal; }
  delete s.db_ref;
  return p;
}

/** Section dict for one steel-database record (mirror of section_from_db). */
export function ssFromRecord(rec, standard, material, name) {
  const p = rec.props || {}, dims = { ...(rec.dims || {}) };
  const geo = ssProps(rec.type, numDims(dims));
  const b = geo.error ? (dims.bf || dims.b || dims.D || 0) : geo.b, h = geo.error ? (dims.d || dims.h || dims.D || 0) : geo.h;
  const s = {
    name: name || rec.name, material, A: p.A, I33: p.I33, I22: p.I22, J: p.J != null ? p.J : (geo.error ? 0 : geo.J), b, h,
    mod_A: 1, mod_I33: 1, mod_I22: 1, mod_J: 1, As2: p.As2 ?? null, As3: p.As3 ?? null, shear_deformation: false,
    mod_As2: 1, mod_As3: 1, mod_mass: 1, mod_weight: 1, shape: rec.type, dims, db_ref: { standard, name: rec.name },
  };
  if (p.I_major != null && p.I_minor != null && p.alpha != null) s.principal = { I_major: p.I_major, I_minor: p.I_minor, alpha: p.alpha };
  return s;
}

/* ---- secpreview hook: shape + dims → drawing fields / polygon
   Returns null (not a parametric section), { sec } (flattened fields for
   the existing secpreview shape branches) or { poly, shape, dims, sc }
   (an outline secpreview draws as-is: the concrete L-beam). */
const PREVIEW_SHAPE = { I: "I", I_mono: "I", channel: "channel", angle: "angle", double_angle: "2L", tee: "tee", rect_tube: "box",
  round_tube: "pipe", rect_solid: "rect", plate: "rect", round_solid: "circle", tee_concrete: "tee" };
export function ssPreview(sec) {
  if (!sec || !sec.dims || typeof sec.dims !== "object" || !SS_PARAM.includes(sec.shape)) return null;
  const d = sec.dims, typ = sec.shape, f = { ...sec };
  delete f.dims;
  if (wbCfIs(typ)) { const pv = wbCfPreview(typ, d); return pv || { sec: { ...f, shape: "general" } }; }   // Wave B cold-formed outline
  const set = o => Object.assign(f, o, { shape: PREVIEW_SHAPE[typ] });
  switch (typ) {
    case "I": set({ d: d.d, bf: d.bf, tf: d.tf, tw: d.tw }); break;
    case "I_mono": set({ d: d.d, bf: d.bf_top, tf: d.tf_top, bf_bot: d.bf_bot, tf_bot: d.tf_bot, tw: d.tw }); break;
    case "channel": case "tee": set({ d: d.d, bf: d.bf, tf: d.tf, tw: d.tw }); break;
    case "angle": set({ d: d.d, b: d.b, tf: d.t, tw: d.t }); break;
    case "double_angle": set({ d: d.d, b: d.b, tf: d.t, tw: d.t, s: d.s || 0 }); break;
    case "rect_tube": set({ b: d.b, h: d.d, tf: d.t, tw: d.t }); break;
    case "round_tube": set({ d: d.D, t: d.t }); break;
    case "rect_solid": case "plate": set({ b: d.b, h: d.d }); break;
    case "round_solid": set({ d: d.D, b: d.D, h: d.D }); break;
    case "tee_concrete": set({ d: d.h, bf: d.bf, tf: d.hf, tw: d.bw }); break;
    case "L_concrete": {
      const { bf, hf, bw, h } = d;
      if (!(bf > 0 && hf > 0 && bw > 0 && h > hf && bf >= bw)) return { sec: { ...f, shape: "general" } };
      return { shape: "L_concrete", label: "Concrete L-Beam",
        poly: [[0, 0], [bw, 0], [bw, h - hf], [bf, h - hf], [bf, h], [0, h]],
        dims: [{ dir: "h", a: 0, b: bf, at: h, side: 1, sym: "bf", v: bf }, { dir: "v", a: 0, b: h, at: 0, side: -1, sym: "h", v: h },
          { dir: "v", a: h - hf, b: h, at: bf, side: 1, sym: "hf", v: hf }, { dir: "h", a: 0, b: bw, at: 0, side: -1, sym: "bw", v: bw }],
        sc: [bw / 2, h - hf / 2] };
    }
  }
  return { sec: f };
}

export default { SS_TYPES, SS_PARAM, SS_LABEL, ssTypeInfo, ssValidate, ssProps, ssApply, ssFromRecord, ssPreview, ssPrincipal, ssRects, ssShearAreas, SHEAR_AREA_FORMULAS };
