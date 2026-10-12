/* SkyFrame — live member / property preview ("small screen") for the
   definition dialogs.

     Frame sections   to-scale SVG cross-section (dimension lines in display
                      units, local 2 / 3 axes, centroid, shear centre for
                      unsymmetric shapes, rebar), a tiny extruded isometric of
                      a short member and an A / I33 / I22 / J / material
                      readout. Nonprismatic: i-end and j-end profiles side by
                      side + a taper elevation. Section Designer: the actual
                      polygons, holes and bars.
     Slab / wall      layered thickness diagram, element type (shell / thin /
                      thick / membrane / plate), one-way span arrow.
     Springs / links  DOF glyph: active DOFs and their stiffness.

   ETABS section-picture convention: we look DOWN local 1 (into the screen),
   local 2 is vertical along the depth h / d, local 3 horizontal along the
   width b / bf (green = 2, blue = 3, red = 1).

   Read-only: the preview never writes to the section, the draft or the
   model (it works on clones), so OK with defaults stays byte-identical.
   Every number goes through js/units.js; the model stays SI.

   Hooks: dlgkit dialogs call spvFramePanel / spvShellPanel / spvSplit
   (js/dlgstd_defs.js, js/dlgstd_assign.js); the legacy editors (Section
   Manager, Nonprismatic, Spring Properties, Joint Springs, Link Properties)
   are decorated by initSecPreview(sky) without touching their code. */

import spvU from "./units.js";
import { rectSectionProps as spvRectProps } from "./modeledit.js";
import { buPreviewPolys as spvBuPolys } from "./builtup_geom.js";   // v1.21 built-up section outlines (js/builtup.js)

import { ssPreview as spvSsPreview } from "./secshapes.js";   // v1.21 parametric / database shapes (shape + dims)
import { seJMethod as spvJMethod } from "./secexact.js";   // v1.22 J method (exact / closed form / approx) of the readout

const NS = "http://www.w3.org/2000/svg";
const isNum = v => typeof v === "number" && isFinite(v);
const pos = v => isNum(+v) && +v > 0 && v !== null && v !== "" ? +v : 0;
const clone = o => o == null ? o : JSON.parse(JSON.stringify(o));
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const r2 = v => Math.round(v * 100) / 100;

/* ------------------------------------------------ stylesheet (once) */
let cssDone = false;
export function spvEnsureCss() {
  if (cssDone || document.querySelector("link[data-spv-css]")) { cssDone = true; return; }
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = "/static/secpreview.css"; l.setAttribute("data-spv-css", "1");
  document.head.appendChild(l);
  cssDone = true;
}

function h(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  (Array.isArray(kids) ? kids : [kids]).forEach(c => c != null && c !== false && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
  return n;
}

/* ------------------------------------------------ number formatting */
/** Display value of an SI dimension, 4 significant digits, no unit. */
const dval = si => { const v = spvU.toDisplay("dim", si); return isFinite(v) ? String(+(+v).toPrecision(4)) : "—"; };
const dimTxt = (sym, si, approx) => `${sym} ${approx ? "≈" : "="} ${dval(si)} ${spvU.label("dim")}`;
const sciU = (kind, si) => isNum(si) ? `${spvU.sci(kind, si, 4)} ${spvU.label(kind)}` : "—";

/* ================================================================
   Polygon maths (x = local 3 → right, y = local 2 → up)
   ================================================================ */
const area2 = p => { let a = 0; for (let i = 0, j = p.length - 1; i < p.length; j = i++) a += p[j][0] * p[i][1] - p[i][0] * p[j][1]; return a; };
const ccw = p => area2(p) < 0 ? p.slice().reverse() : p;
const cw = p => area2(p) > 0 ? p.slice().reverse() : p;
const rect = (b, hh, cx = 0, cy = 0) => [[cx - b / 2, cy - hh / 2], [cx + b / 2, cy - hh / 2], [cx + b / 2, cy + hh / 2], [cx - b / 2, cy + hh / 2]];
const circ = (d, n = 64, cx = 0, cy = 0) => Array.from({ length: n }, (_, i) => [cx + d / 2 * Math.cos(2 * Math.PI * i / n), cy + d / 2 * Math.sin(2 * Math.PI * i / n)]);

/** Exact shoelace integrals of solid polygons minus holes. */
export function spvPolyProps(polys) {
  let A = 0, Sx = 0, Sy = 0, Ixx = 0, Iyy = 0;
  for (const P of polys) {
    const p = P.pts, sg = (area2(p) >= 0 ? 1 : -1) * (P.hole ? -1 : 1);
    for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
      const [x0, y0] = p[j], [x1, y1] = p[i], cr = x0 * y1 - x1 * y0;
      A += sg * cr / 2; Sx += sg * (x0 + x1) * cr / 6; Sy += sg * (y0 + y1) * cr / 6;
      Iyy += sg * (x0 * x0 + x0 * x1 + x1 * x1) * cr / 12; Ixx += sg * (y0 * y0 + y0 * y1 + y1 * y1) * cr / 12;
    }
  }
  const cx = A ? Sx / A : 0, cy = A ? Sy / A : 0;
  return { A, cx, cy, I33: Ixx - A * cy * cy, I22: Iyy - A * cx * cx };
}
function bboxOf(polys) {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const P of polys) for (const [x, y] of P.pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  return { x0, x1, y0, y1, w: x1 - x0, h: y1 - y0 };
}

/** I-shape plate thicknesses from A and I33 when only d / bf are known
    (library W-shapes carry A, I and the drawing b / h only). */
function solveI(A, I33, d, bf) {
  if (!(A > 0 && d > 0 && bf > 0) || A >= bf * d) return null;
  const twOf = tf => (A - 2 * bf * tf) / (d - 2 * tf);
  const Iof = tf => { const tw = twOf(tf); return (bf * d ** 3 - (bf - tw) * (d - 2 * tf) ** 3) / 12; };
  const hi = Math.min(d / 2 * 0.98, A / (2 * bf) * 0.999);
  if (I33 > 0 && hi > 0) {
    let lo = hi * 1e-3, up = hi, flo = Iof(lo) - I33;
    if (flo * (Iof(up) - I33) < 0) {
      for (let it = 0; it < 60; it++) { const mid = (lo + up) / 2, fm = Iof(mid) - I33; if (fm * flo <= 0) up = mid; else { lo = mid; flo = fm; } }
      const tf = (lo + up) / 2, tw = twOf(tf);
      if (tw > 0 && tw <= bf && tf > 0) return { tf, tw };
    }
  }
  const qa = -1.2, qb = 2 * bf + 0.6 * d, disc = qb * qb - 4 * qa * -A;
  if (disc < 0) return null;
  const tf = (-qb + Math.sqrt(disc)) / (2 * qa);
  return tf > 0 && tf < d / 2 ? { tf, tw: 0.6 * tf } : null;
}

const RX_I = /^(W|HP|S|M|HE[ABM]?|IPE|UB|UC|I)\s*\d/i;
const RX_PIPE = /^(PIPE|CHS)/i;
const RX_TUBE = /^(HSS|RHS|SHS|TUBE|BOX)/i;
const RX_C = /^(C|MC|UPN|PFC)\s*\d/i;
const RX_L = /^L\s*\d/i;
const RX_2L = /^2L\s*\d/i;
const RX_T = /^(WT|MT|ST|T)\s*\d/i;

const SHAPE_ALIASES = {
  rect: "rect", rectangle: "rect", rectangular: "rect", circle: "circle", circular: "circle", round: "circle",
  pipe: "pipe", chs: "pipe", box: "box", tube: "box", rhs: "box", shs: "box", hss: "box",
  i: "I", w: "I", "i/wide flange": "I", wide_flange: "I", channel: "channel", c: "channel", angle: "angle", l: "angle",
  tee: "tee", t: "tee", wt: "tee", double_angle: "2L", "2l": "2L", doubleangle: "2L",
  designer: "designer", general: "general",
};
const SHAPE_LABEL = {
  rect: "Rectangular", circle: "Circular", pipe: "Pipe", box: "Box / Tube", I: "I / Wide Flange", I1: "I — singly symmetric",
  channel: "Channel", angle: "Angle", tee: "Tee", "2L": "Double Angle", designer: "Section Designer", general: "General (A, I only)",
  nonprismatic: "Nonprismatic",
  builtup: "Built-up",   // v1.21 (js/builtup.js)
};

/** Cross-section geometry of a frame section dict.
    → { shape, label, polys:[{pts, hole}], rebar:[{x,y,r}], dims:[], sc:[x,y]|null,
        geo:{A,cx,cy,I33,I22}, J, invalid, notes, approx:{} } */
export function spvGeometry(sec, model = {}, name = "") {
  sec = sec || {};
  name = name || sec.name || "";
  /* v1.21: a section with shape + dims (js/secshapes.js) is drawn from its dimensions */
  const ssx = spvSsPreview(sec);
  if (ssx && ssx.poly) {
    const o = { shape: ssx.shape, label: ssx.label, polys: [{ pts: ccw(ssx.poly), hole: false }], rebar: [], sc: ssx.sc, notes: [], approx: {}, invalid: "",
      dims: ssx.dims.map(x => ({ ...x, label: dimTxt(x.sym, x.v) })), J: isNum(sec.J) ? sec.J : null };
    o.geo = spvPolyProps(o.polys); o.bbox = bboxOf(o.polys);
    return o;
  }
  if (ssx) sec = ssx.sec;
  const g = (...ks) => { for (const k of ks) { const v = pos(sec[k]); if (v) return v; } return 0; };
  const shape0 = SHAPE_ALIASES[String(sec.shape || "").toLowerCase()] || "";
  const out = { shape: shape0 || "rect", polys: [], rebar: [], dims: [], sc: null, notes: [], approx: {}, invalid: "" };
  const A = g("A"), I33 = g("I33"), I22 = g("I22");
  let b = g("b"), hh = g("h");
  const fin = () => {
    out.label = SHAPE_LABEL[out.shape] || out.shape;
    if (!out.polys.length) return out;
    out.geo = spvPolyProps(out.polys);
    out.bbox = bboxOf(out.polys);
    return out;
  };
  const bad = msg => { out.invalid = out.invalid || msg; };

  /* ---- Section Designer: the actual polygons + bars */
  const ds = (model.designer_sections || {})[name];
  if (ds && (shape0 === "designer" || shape0 === "" || !b || !hh) && Array.isArray(ds.polygons) && ds.polygons.some(p => !p.hole)) {
    out.shape = "designer";
    for (const p of ds.polygons) {
      const v = (p.vertices || []).filter(q => Array.isArray(q) && isFinite(+q[0]) && isFinite(+q[1])).map(q => [+q[0], +q[1]]);
      if (v.length >= 3) out.polys.push({ pts: p.hole ? cw(v) : ccw(v), hole: !!p.hole, material: p.material });
    }
    for (const r of ds.rebar || []) if (isFinite(+r.y) && isFinite(+r.z) && +r.area > 0) out.rebar.push({ x: +r.y, y: +r.z, r: Math.sqrt(4 * r.area / Math.PI) / 2, area: +r.area, material: r.material });
    const bb = bboxOf(out.polys);
    out.dims.push({ dir: "h", a: bb.x0, b: bb.x1, at: bb.y0, side: -1, label: dimTxt("b", bb.w) });
    out.dims.push({ dir: "v", a: bb.y0, b: bb.y1, at: bb.x0, side: -1, label: dimTxt("h", bb.h) });
    if (out.rebar.length) out.notes.push(`${out.rebar.length} bars · As = ${spvU.sci("area", out.rebar.reduce((a, r) => a + r.area, 0), 3)} ${spvU.label("area")}`);
    const pp = spvPolyProps(out.polys), Ip = pp.I33 + pp.I22;
    out.J = Ip > 0 ? pp.A ** 4 / (40 * Ip) : null;
    return fin();
  }
  /* ---- v1.21 built-up sections: the component outlines (js/builtup_geom.js) */
  const bu = (model.builtup_sections || {})[name];
  if (bu && Array.isArray(bu.components) && bu.components.length && (out.polys = spvBuPolys(bu)).length) {
    out.shape = "builtup"; out.J = A > 0 ? sec.J : null;
    const bb = bboxOf(out.polys);
    out.dims.push({ dir: "h", a: bb.x0, b: bb.x1, at: bb.y0, side: -1, label: dimTxt("b", bb.w) }, { dir: "v", a: bb.y0, b: bb.y1, at: bb.x0, side: -1, label: dimTxt("h", bb.h) });
    return fin();
  }

  /* ---- shape inference (same rules as the 3D extruded view) */
  let shape = shape0;
  if (!shape) {
    const fill = A > 0 && b > 0 && hh > 0 ? A / (b * hh) : 1;
    if (b > 0 && hh > 0 && (!A || Math.abs(A - b * hh) <= 1e-9 * Math.max(A, b * hh))) shape = "rect";
    else if (RX_2L.test(name)) shape = "2L";
    else if (RX_PIPE.test(name) && b > 0) shape = "pipe";
    else if (b > 0 && hh > 0 && Math.abs(b - hh) < 1e-6 * Math.max(b, 1) && A > 0 && Math.abs(A - Math.PI * b * b / 4) < 0.04 * A) shape = "circle";
    else if (RX_TUBE.test(name) && fill < 0.7) shape = Math.abs(b - hh) < 1e-6 * Math.max(b, 1) && /^(HSS\s*[\d.]+\s*[Xx]\s*[\d.]+$)/i.test(name) ? "pipe" : "box";
    else if (RX_C.test(name) && fill < 0.7) shape = "channel";
    else if (RX_T.test(name) && fill < 0.7) shape = "tee";
    else if (RX_L.test(name) && fill < 0.7) shape = "angle";
    else if ((RX_I.test(name) || pos(sec.tf) && pos(sec.tw)) && (!A || fill < 0.75)) shape = "I";
    else if (b > 0 && hh > 0 && !A) shape = "rect";
    else shape = "general";
  }
  out.shape = shape;

  /* library W name without b / h: nominal depth from the name */
  if (shape === "I" && (!b || !hh) && A > 0 && I22 > 0) {
    const mN = name.match(/(\d+(\.\d+)?)/), dn = mN ? +mN[1] * 0.0254 : 0;
    if (dn > 0.05 && dn < 1.6) { hh = hh || dn; b = b || Math.sqrt(12 * I22 / (0.66 * A)); out.approx.d = out.approx.bf = true; }
  }

  if (shape === "general") {
    // equivalent rectangle with the same A, I33, I22
    const he = A > 0 && I33 > 0 ? Math.sqrt(12 * I33 / A) : hh, be = A > 0 && I22 > 0 ? Math.sqrt(12 * I22 / A) : b;
    if (!(he > 0 && be > 0)) { bad("Enter A, I33 and I22 greater than zero to draw this section."); out.polys.push({ pts: ccw(rect(1, 1)), hole: false }); out.ghost = true; return fin(); }
    out.polys.push({ pts: ccw(rect(be, he)), hole: false });
    out.ghost = true;
    out.notes.push("General section: drawn as the rectangle with the same A, I33 and I22.");
    out.dims.push({ dir: "h", a: -be / 2, b: be / 2, at: -he / 2, side: -1, label: dimTxt("b", be, true) });
    out.dims.push({ dir: "v", a: -he / 2, b: he / 2, at: -be / 2, side: -1, label: dimTxt("h", he, true) });
    return fin();
  }

  if (shape === "rect") {
    if (!(b > 0)) bad("Width b must be greater than zero.");
    if (!(hh > 0)) bad("Depth h must be greater than zero.");
    if (out.invalid) { out.polys.push({ pts: ccw(rect(b || hh || 1, hh || b || 1)), hole: false }); return fin(); }
    out.polys.push({ pts: ccw(rect(b, hh)), hole: false });
    out.dims.push({ dir: "h", a: -b / 2, b: b / 2, at: -hh / 2, side: -1, label: dimTxt("b", b) });
    out.dims.push({ dir: "v", a: -hh / 2, b: hh / 2, at: -b / 2, side: -1, label: dimTxt("h", hh) });
    const a = Math.max(b, hh) / 2, c = Math.min(b, hh) / 2;
    out.J = a * c ** 3 * (16 / 3 - 3.36 * (c / a) * (1 - c ** 4 / (12 * a ** 4)));
    return fin();
  }

  if (shape === "circle" || shape === "pipe") {
    const d = g("d", "t3", "D") || b || hh;
    if (!(d > 0)) { bad("Diameter d must be greater than zero."); out.polys.push({ pts: ccw(circ(1)), hole: false }); return fin(); }
    out.polys.push({ pts: ccw(circ(d)), hole: false });
    const dimD = { dir: "h", a: -d / 2, b: d / 2, at: -d / 2, side: -1, label: dimTxt("d", d) };
    out.dims.push(dimD);
    if (shape === "pipe") {
      let t = g("t", "tw", "tf"), ap = false;
      if (!t && A > 0) { const ri2 = d * d / 4 - A / Math.PI; t = ri2 > 0 ? d / 2 - Math.sqrt(ri2) : d / 2; ap = true; }
      if (!(t > 0)) bad("Wall thickness t must be greater than zero.");
      else if (t >= d / 2) bad("Wall thickness t must be less than d / 2.");
      else {
        out.polys.push({ pts: cw(circ(d - 2 * t)), hole: true });
        dimD.label += " · " + dimTxt("t", t, ap);   // the wall is too thin for its own dimension line
        out.J = Math.PI * (d ** 4 - (d - 2 * t) ** 4) / 32;
      }
    } else out.J = Math.PI * d ** 4 / 32;
    return fin();
  }

  if (shape === "box") {
    const B = g("b", "t2"), H = g("h", "d", "t3");
    let tf = g("tf", "t"), tw = g("tw", "t"), ap = false;
    if (!(B > 0 && H > 0)) { bad("Outside width b and depth h must be greater than zero."); out.polys.push({ pts: ccw(rect(B || 1, H || 1)), hole: false }); return fin(); }
    if (!(tf && tw) && A > 0) {
      const qb = -2 * (B + H), disc = qb * qb - 16 * A;
      const t = disc >= 0 ? (-qb - Math.sqrt(disc)) / 8 : 0;
      tf = tf || t; tw = tw || t; ap = true;
    }
    out.polys.push({ pts: ccw(rect(B, H)), hole: false });
    out.dims.push({ dir: "h", a: -B / 2, b: B / 2, at: -H / 2, side: -1, label: dimTxt("b", B) });
    out.dims.push({ dir: "v", a: -H / 2, b: H / 2, at: -B / 2, side: -1, label: dimTxt("h", H) });
    if (!(tf > 0 && tw > 0)) bad("Wall thicknesses tf and tw must be greater than zero.");
    else if (2 * tf >= H || 2 * tw >= B) bad("The walls are thicker than half the box — reduce tf / tw.");
    else {
      out.polys.push({ pts: cw(rect(B - 2 * tw, H - 2 * tf)), hole: true });
      out.dims.push({ dir: "v", a: H / 2 - tf, b: H / 2, at: B / 2, side: 1, label: dimTxt("tf", tf, ap) });
      out.dims.push({ dir: "h", a: B / 2 - tw, b: B / 2, at: -H / 4, side: 1, label: dimTxt("tw", tw, ap), inner: true });
      const t1 = tf, t2 = tw;
      out.J = 2 * t1 * t2 * (B - t2) ** 2 * (H - t1) ** 2 / (B * t2 + H * t1 - t2 * t2 - t1 * t1);
    }
    return fin();
  }

  if (shape === "I") {
    const d = g("d", "h", "t3") || hh, bft = g("bf", "bf_top", "b", "t2") || b, bfb = g("bf_bot", "bfb") || bft;
    let tft = g("tf", "tf_top"), tfb = g("tf_bot", "tfb") || tft, tw = g("tw"), ap = false;
    if (!(d > 0 && bft > 0)) { bad("Depth d and flange width bf must be greater than zero."); out.polys.push({ pts: ccw(rect(bft || 1, d || 1)), hole: false }); return fin(); }
    if (!(tft > 0 && tw > 0)) { const s = solveI(A, I33, d, bft); if (s) { tft = tft || s.tf; tfb = tfb || s.tf; tw = tw || s.tw; ap = true; } }
    if (!(tft > 0 && tfb > 0 && tw > 0)) { bad("Flange and web thicknesses must be greater than zero."); out.polys.push({ pts: ccw(rect(bft, d)), hole: false }); return fin(); }
    if (tft + tfb >= d) bad("The flanges are thicker than the depth — reduce tf.");
    if (tw >= Math.min(bft, bfb)) bad("The web is wider than a flange — reduce tw.");
    if (out.invalid) { out.polys.push({ pts: ccw(rect(Math.max(bft, bfb), d)), hole: false }); return fin(); }
    const y0 = -d / 2, y1 = d / 2, w = tw / 2;
    out.polys.push({ pts: ccw([[-bfb / 2, y0], [bfb / 2, y0], [bfb / 2, y0 + tfb], [w, y0 + tfb], [w, y1 - tft], [bft / 2, y1 - tft], [bft / 2, y1],
      [-bft / 2, y1], [-bft / 2, y1 - tft], [-w, y1 - tft], [-w, y0 + tfb], [-bfb / 2, y0 + tfb]]), hole: false });
    const single = Math.abs(bft - bfb) > 1e-9 || Math.abs(tft - tfb) > 1e-9;
    if (single) out.shape = "I1";
    out.dims.push({ dir: "h", a: -bft / 2, b: bft / 2, at: y1, side: 1, label: dimTxt(single ? "bf top" : "bf", bft, out.approx.bf) });
    if (single) out.dims.push({ dir: "h", a: -bfb / 2, b: bfb / 2, at: y0, side: -1, label: dimTxt("bf bot", bfb) });
    out.dims.push({ dir: "v", a: y0, b: y1, at: -Math.max(bft, bfb) / 2, side: -1, label: dimTxt("d", d, out.approx.d) });
    out.dims.push({ dir: "v", a: y1 - tft, b: y1, at: bft / 2, side: 1, label: dimTxt("tf", tft, ap) });
    if (single) out.dims.push({ dir: "v", a: y0, b: y0 + tfb, at: bfb / 2, side: 1, label: dimTxt("tf bot", tfb) });
    out.dims.push({ dir: "h", a: -w, b: w, at: y0 + tfb + (d - tft - tfb) * 0.22, side: 1, label: dimTxt("tw", tw, ap), inner: true });
    out.J = (bft * tft ** 3 + bfb * tfb ** 3 + (d - tft - tfb) * tw ** 3) / 3;
    if (single) {
      const yt = y1 - tft / 2, yb = y0 + tfb / 2, It = tft * bft ** 3 / 12, Ib = tfb * bfb ** 3 / 12;
      out.sc = [0, yb + (yt - yb) * It / (It + Ib)];
    }
    if (ap) out.notes.push("tf / tw derived from the library A and I33 (≈).");
    return fin();
  }

  if (shape === "channel") {
    const d = g("d", "h", "t3") || hh, bf = g("bf", "b", "t2") || b, tf = g("tf", "t"), tw = g("tw", "t");
    if (!(d > 0 && bf > 0 && tf > 0 && tw > 0)) { bad("Channel needs d, bf, tf and tw greater than zero."); out.polys.push({ pts: ccw(rect(bf || 1, d || 1)), hole: false }); return fin(); }
    if (2 * tf >= d) bad("The flanges are thicker than half the depth — reduce tf.");
    if (tw >= bf) bad("The web is wider than the flange — reduce tw.");
    if (out.invalid) { out.polys.push({ pts: ccw(rect(bf, d)), hole: false }); return fin(); }
    const y0 = -d / 2, y1 = d / 2;
    out.polys.push({ pts: ccw([[0, y0], [bf, y0], [bf, y0 + tf], [tw, y0 + tf], [tw, y1 - tf], [bf, y1 - tf], [bf, y1], [0, y1]]), hole: false });
    out.dims.push({ dir: "h", a: 0, b: bf, at: y1, side: 1, label: dimTxt("bf", bf) });
    out.dims.push({ dir: "v", a: y0, b: y1, at: 0, side: -1, label: dimTxt("d", d) });
    out.dims.push({ dir: "v", a: y1 - tf, b: y1, at: bf, side: 1, label: dimTxt("tf", tf) });
    out.dims.push({ dir: "h", a: 0, b: tw, at: -d / 4, side: 1, label: dimTxt("tw", tw), inner: true });
    const bp = bf - tw / 2, hp = d - tf, e = 3 * bp * bp * tf / (6 * bp * tf + hp * tw);
    out.sc = [tw / 2 - e, 0];
    out.J = (2 * bf * tf ** 3 + (d - 2 * tf) * tw ** 3) / 3;
    return fin();
  }

  if (shape === "angle") {
    const d = g("d", "h", "t3") || hh, bb = g("b", "bf", "t2") || b, tf = g("tf", "t"), tw = g("tw", "t");
    if (!(d > 0 && bb > 0 && tf > 0 && tw > 0)) { bad("Angle needs both legs and thicknesses greater than zero."); out.polys.push({ pts: ccw(rect(bb || 1, d || 1)), hole: false }); return fin(); }
    if (tf >= d || tw >= bb) bad("A leg is thinner than its thickness — reduce t.");
    if (out.invalid) { out.polys.push({ pts: ccw(rect(bb, d)), hole: false }); return fin(); }
    out.polys.push({ pts: ccw([[0, 0], [bb, 0], [bb, tf], [tw, tf], [tw, d], [0, d]]), hole: false });
    out.dims.push({ dir: "h", a: 0, b: bb, at: 0, side: -1, label: dimTxt("b", bb) });
    out.dims.push({ dir: "v", a: 0, b: d, at: 0, side: -1, label: dimTxt("d", d) });
    out.dims.push({ dir: "v", a: 0, b: tf, at: bb, side: 1, label: dimTxt("tf", tf) });
    out.dims.push({ dir: "h", a: 0, b: tw, at: d, side: 1, label: dimTxt("tw", tw) });
    out.sc = [tw / 2, tf / 2];
    out.J = (bb * tf ** 3 + (d - tf) * tw ** 3) / 3;
    return fin();
  }

  if (shape === "tee") {
    const d = g("d", "h", "t3") || hh, bf = g("bf", "b", "t2") || b, tf = g("tf", "t"), tw = g("tw", "t");
    if (!(d > 0 && bf > 0 && tf > 0 && tw > 0)) { bad("Tee needs d, bf, tf and tw greater than zero."); out.polys.push({ pts: ccw(rect(bf || 1, d || 1)), hole: false }); return fin(); }
    if (tf >= d) bad("The flange is thicker than the depth — reduce tf.");
    if (tw >= bf) bad("The stem is wider than the flange — reduce tw.");
    if (out.invalid) { out.polys.push({ pts: ccw(rect(bf, d)), hole: false }); return fin(); }
    out.polys.push({ pts: ccw([[-tw / 2, 0], [tw / 2, 0], [tw / 2, d - tf], [bf / 2, d - tf], [bf / 2, d], [-bf / 2, d], [-bf / 2, d - tf], [-tw / 2, d - tf]]), hole: false });
    out.dims.push({ dir: "h", a: -bf / 2, b: bf / 2, at: d, side: 1, label: dimTxt("bf", bf) });
    out.dims.push({ dir: "v", a: 0, b: d, at: -bf / 2, side: -1, label: dimTxt("d", d) });
    out.dims.push({ dir: "v", a: d - tf, b: d, at: bf / 2, side: 1, label: dimTxt("tf", tf) });
    out.dims.push({ dir: "h", a: -tw / 2, b: tw / 2, at: 0, side: -1, label: dimTxt("tw", tw) });
    out.sc = [0, d - tf / 2];
    out.J = (bf * tf ** 3 + (d - tf) * tw ** 3) / 3;
    return fin();
  }

  if (shape === "2L") {
    const d = g("d", "h", "t3") || hh, leg = g("b", "bf", "t2") || (b ? b / 2 : 0), tf = g("tf", "t"), tw = g("tw", "t"), s = pos(sec.s) || pos(sec.gap) || 0;
    if (!(d > 0 && leg > 0 && tf > 0 && tw > 0)) { bad("Double angle needs d, b, t greater than zero."); out.polys.push({ pts: ccw(rect(2 * (leg || 1), d || 1)), hole: false }); return fin(); }
    if (tf >= d || tw >= leg) bad("A leg is thinner than its thickness — reduce t.");
    if (out.invalid) { out.polys.push({ pts: ccw(rect(2 * leg + s, d)), hole: false }); return fin(); }
    const L = sg => ccw([[sg * s / 2, 0], [sg * (s / 2 + tw), 0], [sg * (s / 2 + tw), d - tf], [sg * (s / 2 + leg), d - tf], [sg * (s / 2 + leg), d], [sg * s / 2, d]]);
    out.polys.push({ pts: L(-1), hole: false }, { pts: L(1), hole: false });
    out.dims.push({ dir: "h", a: -(s / 2 + leg), b: s / 2 + leg, at: d, side: 1, label: dimTxt("B", 2 * leg + s) });
    out.dims.push({ dir: "v", a: 0, b: d, at: -(s / 2 + leg), side: -1, label: dimTxt("d", d) });
    out.dims.push({ dir: "v", a: d - tf, b: d, at: s / 2 + leg, side: 1, label: dimTxt("t", tf) });
    if (s > 0) out.dims.push({ dir: "h", a: -s / 2, b: s / 2, at: 0, side: -1, label: dimTxt("s", s) });
    out.sc = [0, d - tf / 2];
    out.J = 2 * (leg * tf ** 3 + (d - tf) * tw ** 3) / 3;
    return fin();
  }
  out.shape = "general";
  return fin();
}

/* ================================================================
   SVG renderers
   ================================================================ */
const svgEl = (w, hh, cls, label) => {
  const s = document.createElementNS(NS, "svg");
  s.setAttribute("viewBox", `0 0 ${w} ${hh}`);
  s.setAttribute("class", cls);
  s.setAttribute("role", "img");
  if (label) s.setAttribute("aria-label", label);
  return s;
};
let SVGN = 0;
/** Per-SVG marker ids (several previews can be open at once). */
const uniq = html => { const u = "_" + (++SVGN); return html.replace(/id="(spv[A-Za-z0-9]+)"/g, `id="$1${u}"`).replace(/url\(#(spv[A-Za-z0-9]+)\)/g, `url(#$1${u})`); };
const pathD = (pts, tf) => pts.map((p, i) => { const [x, y] = tf(p); return `${i ? "L" : "M"}${r2(x)},${r2(y)}`; }).join("") + "Z";
const matClass = (model, mat) => {
  const t = (((model || {}).materials || {})[mat] || {}).material_type || "";
  return t === "steel" || t === "coldformed" || t === "aluminum" ? "is-steel" : t === "concrete" || t === "masonry" ? "is-conc" : "is-other";
};

/** To-scale section picture with dimensions, 2/3 axes, centroid, shear centre. */
export function spvSectionSvg(geom, { w = 300, hgt = 200, matCls = "is-other", title = "" } = {}) {
  const m = { l: 56, r: 54, t: 26, b: 36 };
  const bb = geom.bbox || { x0: -0.5, x1: 0.5, y0: -0.5, y1: 0.5, w: 1, h: 1 };
  const sc = Math.min((w - m.l - m.r) / (bb.w || 1), (hgt - m.t - m.b) / (bb.h || 1));
  const ox = m.l + ((w - m.l - m.r) - bb.w * sc) / 2 - bb.x0 * sc;
  const oy = m.t + ((hgt - m.t - m.b) - bb.h * sc) / 2 + bb.y1 * sc;
  const T = ([x, y]) => [ox + x * sc, oy - y * sc];
  const inv = !!geom.invalid;
  let s = "";
  // outline (solid polygons + holes in one even-odd path per material group)
  const d = geom.polys.map(P => pathD(P.pts, T)).join("");
  s += `<path class="spv-outline ${matCls}${inv ? " spv-invalid" : ""}${geom.ghost ? " spv-ghost" : ""}" fill-rule="evenodd" d="${d}" data-scale="${sc}"/>`;
  // rebar
  for (const r of geom.rebar || []) {
    const [x, y] = T([r.x, r.y]);
    s += `<circle class="spv-bar" cx="${r2(x)}" cy="${r2(y)}" r="${r2(Math.max(1.6, r.r * sc))}"/>`;
  }
  // dimension lines
  const tiers = { h1: 0, "h-1": 0, v1: 0, "v-1": 0 };
  for (const dm of geom.dims || []) {
    if (inv) break;
    const key = dm.dir + dm.side;
    const off = dm.inner ? 0 : 13 + 15 * (tiers[key]++);
    if (dm.dir === "h") {
      const [xa, ya] = T([dm.a, dm.at]), [xb] = T([dm.b, dm.at]);
      const ext = dm.side > 0 ? (dm.inner ? ya : Math.min(ya, T([0, bb.y1])[1])) : (dm.inner ? ya : Math.max(ya, T([0, bb.y0])[1]));
      const y = dm.inner ? ya : ext - dm.side * off;
      if (!dm.inner) s += `<line class="spv-ext" x1="${r2(xa)}" y1="${r2(ya)}" x2="${r2(xa)}" y2="${r2(y - dm.side * 3)}"/><line class="spv-ext" x1="${r2(xb)}" y1="${r2(ya)}" x2="${r2(xb)}" y2="${r2(y - dm.side * 3)}"/>`;
      s += `<line class="spv-dim" x1="${r2(xa)}" y1="${r2(y)}" x2="${r2(xb)}" y2="${r2(y)}"/>` +
        `<line class="spv-tick" x1="${r2(xa - 3)}" y1="${r2(y + 3)}" x2="${r2(xa + 3)}" y2="${r2(y - 3)}"/><line class="spv-tick" x1="${r2(xb - 3)}" y1="${r2(y + 3)}" x2="${r2(xb + 3)}" y2="${r2(y - 3)}"/>`;
      const len = Math.abs(xb - xa), tw = dm.label.length * 5.6;
      const fits = len > tw + 8 && !dm.inner;
      let tx = fits ? (xa + xb) / 2 : Math.max(xa, xb) + 5, anchor = fits ? "middle" : "start";
      if (!fits && tx + tw > w - 2) { tx = Math.min(xa, xb) - 5; anchor = "end"; }
      const ty = dm.inner ? y + 4 : (dm.side > 0 ? y - 4 : y + 11);
      s += `<text class="spv-dimtxt" x="${r2(tx)}" y="${r2(ty)}" text-anchor="${anchor}">${esc(dm.label)}</text>`;
    } else {
      const [xa, ya] = T([dm.at, dm.a]), [, yb] = T([dm.at, dm.b]);
      const ext = dm.side > 0 ? Math.max(xa, T([bb.x1, 0])[0]) : Math.min(xa, T([bb.x0, 0])[0]);
      const x = dm.inner ? xa : ext + dm.side * off;
      if (!dm.inner) s += `<line class="spv-ext" x1="${r2(xa)}" y1="${r2(ya)}" x2="${r2(x + dm.side * 3)}" y2="${r2(ya)}"/><line class="spv-ext" x1="${r2(xa)}" y1="${r2(yb)}" x2="${r2(x + dm.side * 3)}" y2="${r2(yb)}"/>`;
      s += `<line class="spv-dim" x1="${r2(x)}" y1="${r2(ya)}" x2="${r2(x)}" y2="${r2(yb)}"/>` +
        `<line class="spv-tick" x1="${r2(x - 3)}" y1="${r2(ya + 3)}" x2="${r2(x + 3)}" y2="${r2(ya - 3)}"/><line class="spv-tick" x1="${r2(x - 3)}" y1="${r2(yb + 3)}" x2="${r2(x + 3)}" y2="${r2(yb - 3)}"/>`;
      const len = Math.abs(yb - ya), tw = dm.label.length * 5.6;
      if (len > tw + 8 && !dm.inner) {
        const tx = x + (dm.side > 0 ? 11 : -4), ty = (ya + yb) / 2;
        s += `<text class="spv-dimtxt" x="${r2(tx)}" y="${r2(ty)}" text-anchor="middle" transform="rotate(-90 ${r2(tx)} ${r2(ty)})">${esc(dm.label)}</text>`;
      } else {
        // small dimension: label beside its line, kept inside the picture
        let tx = dm.side > 0 ? x + 5 : x - 5, ty = (ya + yb) / 2 + 4, anchor = dm.side > 0 ? "start" : "end";
        if (dm.side > 0 && tx + tw > w - 2) { tx = w - 2; anchor = "end"; ty = Math.max(ya, yb) + 12; }
        if (dm.side < 0 && tx - tw < 2) { tx = 2; anchor = "start"; ty = Math.max(ya, yb) + 12; }
        s += `<text class="spv-dimtxt" x="${r2(tx)}" y="${r2(Math.max(10, ty))}" text-anchor="${anchor}">${esc(dm.label)}</text>`;
      }
    }
  }
  // centroid + local axes (2 up / green, 3 right / blue), 1 into the screen
  if (geom.geo && !inv) {
    const [cx, cy] = T([geom.geo.cx, geom.geo.cy]);
    const L = Math.max(26, Math.min(58, 0.42 * Math.min(bb.w * sc, bb.h * sc) + 18));
    // keep the arrows inside the outline box so they never run into the dimension labels
    const L2 = Math.max(22, Math.min(L, cy - T([0, bb.y1])[1] - 2)), L3 = Math.max(22, Math.min(L, T([bb.x1, 0])[0] - cx + 14));
    s += `<g class="spv-axes"><line class="spv-ax3" x1="${r2(cx)}" y1="${r2(cy)}" x2="${r2(cx + L3)}" y2="${r2(cy)}" marker-end="url(#spvArr3)"/>` +
      `<line class="spv-ax2" x1="${r2(cx)}" y1="${r2(cy)}" x2="${r2(cx)}" y2="${r2(cy - L2)}" marker-end="url(#spvArr2)"/>` +
      `<text class="spv-axl spv-axl3" x="${r2(cx + L3 + 4)}" y="${r2(cy + 4)}">3</text><text class="spv-axl spv-axl2" x="${r2(cx - 5)}" y="${r2(cy - L2 + 8)}" text-anchor="end">2</text></g>` +
      `<g class="spv-cg" data-x="${geom.geo.cx}" data-y="${geom.geo.cy}"><circle cx="${r2(cx)}" cy="${r2(cy)}" r="4.5"/><path d="M${r2(cx)},${r2(cy - 4.5)}A4.5,4.5 0 0 1 ${r2(cx + 4.5)},${r2(cy)}L${r2(cx)},${r2(cy)}Z M${r2(cx)},${r2(cy + 4.5)}A4.5,4.5 0 0 1 ${r2(cx - 4.5)},${r2(cy)}L${r2(cx)},${r2(cy)}Z" class="spv-cg-q"/></g>`;
    if (geom.sc && Math.hypot(geom.sc[0] - geom.geo.cx, geom.sc[1] - geom.geo.cy) > 1e-6 * Math.max(bb.w, bb.h)) {
      const [sx, sy] = T(geom.sc);
      s += `<g class="spv-sc" data-x="${geom.sc[0]}" data-y="${geom.sc[1]}"><path d="M${r2(sx)},${r2(sy - 5)}L${r2(sx + 5)},${r2(sy)}L${r2(sx)},${r2(sy + 5)}L${r2(sx - 5)},${r2(sy)}Z"/>` +
        `<text x="${r2(sx - 7)}" y="${r2(sy + 13)}" text-anchor="end">SC</text></g>`;
    }
  }
  // "looking down local 1" marker (⊗ = 1 into the screen)
  s += `<g class="spv-ax1"><circle cx="12" cy="12" r="6"/><path d="M8,8L16,16M16,8L8,16"/><text x="22" y="16">1</text></g>`;
  const defs = `<defs><marker id="spvArr2" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L8,4L0,8Z" class="spv-ax2f"/></marker>` +
    `<marker id="spvArr3" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L8,4L0,8Z" class="spv-ax3f"/></marker>` +
    `<marker id="spvArr1" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L8,4L0,8Z" class="spv-ax1f"/></marker></defs>`;
  const svg = svgEl(w, hgt, "spv-svg spv-sec", title || "Section cross-section, looking down local 1");
  svg.innerHTML = uniq(defs + s);
  return svg;
}

/* ---- tiny extruded isometric (same 2 / 3 convention, 1 along the member) */
const YAW = -32 * Math.PI / 180, PITCH = 22 * Math.PI / 180;
function proj([x, y, s]) {        // section (x = 3, y = 2) at member station s (local 1, into the screen)
  const X = x, Y = y, Z = -s;
  const X1 = X * Math.cos(YAW) + Z * Math.sin(YAW), Z1 = -X * Math.sin(YAW) + Z * Math.cos(YAW);
  const Y2 = Y * Math.cos(PITCH) - Z1 * Math.sin(PITCH), Z2 = Y * Math.sin(PITCH) + Z1 * Math.cos(PITCH);
  return [X1, -Y2, Z2];
}
export function spvIsoSvg(geom, { w = 300, hgt = 116, matCls = "is-other", length } = {}) {
  const svg = svgEl(w, hgt, "spv-svg spv-iso", "Short member, isometric view");
  if (!geom || !geom.polys.length || !geom.bbox) return svg;
  const bb = geom.bbox, cx = geom.geo ? geom.geo.cx : 0, cy = geom.geo ? geom.geo.cy : 0;
  const Lm = length || 2.4 * Math.max(bb.w, bb.h);
  const polys = geom.polys.map(P => ({ hole: P.hole, pts: (P.hole ? cw(P.pts) : ccw(P.pts)).map(([x, y]) => [x - cx, y - cy]) }));
  const faces = [];
  const light = (() => { const v = [-0.45, 0.75, 0.5], l = Math.hypot(...v); return v.map(c => c / l); })();
  const rotN = n => { const p0 = proj([0, 0, 0]), p1 = proj(n); return [p1[0] - p0[0], -(p1[1] - p0[1]), p1[2] - p0[2]]; };
  const shadeOf = n3 => { const n = rotN(n3); const l = Math.hypot(...n) || 1; return Math.max(0, (n[0] * light[0] + n[1] * light[1] + n[2] * light[2]) / l); };
  for (const P of polys) {
    const p = P.pts;
    for (let i = 0; i < p.length; i++) {
      const a = p[i], b2 = p[(i + 1) % p.length];
      const nx = b2[1] - a[1], ny = -(b2[0] - a[0]);
      const nr = rotN([nx, ny, 0]);
      if (nr[2] <= 1e-12) continue;             // back-facing wall
      const q = [[a[0], a[1], 0], [b2[0], b2[1], 0], [b2[0], b2[1], Lm], [a[0], a[1], Lm]].map(proj);
      faces.push({ q, depth: q.reduce((s2, v) => s2 + v[2], 0) / 4, shade: shadeOf([nx, ny, 0]) });
    }
  }
  faces.sort((a, b2) => a.depth - b2.depth);
  const all = [];
  for (const P of polys) for (const [x, y] of P.pts) { all.push(proj([x, y, 0])); all.push(proj([x, y, Lm])); }
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const [x, y] of all) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  const pad = 14, padL = 52;
  const sc = Math.min((w - padL - pad) / ((x1 - x0) || 1), (hgt - 2 * pad) / ((y1 - y0) || 1));
  const ox = padL + ((w - padL - pad) - (x1 - x0) * sc) / 2 - x0 * sc, oy = pad + ((hgt - 2 * pad) - (y1 - y0) * sc) / 2 - y0 * sc;
  const S = ([x, y]) => `${r2(ox + x * sc)},${r2(oy + y * sc)}`;
  let s = "";
  const lum = v => (0.42 + 0.58 * v).toFixed(3);
  for (const f of faces) s += `<path class="spv-face ${matCls}" style="--spv-l:${lum(f.shade)}" d="M${f.q.map(S).join("L")}Z"/>`;
  // front cap (toward the viewer, station 0)
  const capD = polys.map(P => "M" + P.pts.map(([x, y]) => S(proj([x, y, 0]))).join("L") + "Z").join("");
  s += `<path class="spv-face spv-cap ${matCls}${geom.invalid ? " spv-invalid" : ""}" fill-rule="evenodd" style="--spv-l:${lum(shadeOf([0, 0, -1]))}" d="${capD}"/>`;
  // member axis (local 1) through the centroid, from the cap into the member
  const a0 = proj([0, 0, -0.18 * Lm]), a1 = proj([0, 0, Lm * 1.12]);
  s += `<line class="spv-ax1l" x1="${S(a0).split(",")[0]}" y1="${S(a0).split(",")[1]}" x2="${S(a1).split(",")[0]}" y2="${S(a1).split(",")[1]}"/>`;
  // triad (bottom-left): 1 red along the member, 2 green, 3 blue
  const t0 = [24, hgt - 20], tl = 16;
  const dir = v => { const p0 = proj([0, 0, 0]), p1 = proj(v); const dx = p1[0] - p0[0], dy = p1[1] - p0[1], l = Math.hypot(dx, dy) || 1; return [dx / l, dy / l]; };
  const tri = [["1", [0, 0, 1], "spv-ax1"], ["2", [0, 1, 0], "spv-ax2"], ["3", [1, 0, 0], "spv-ax3"]];
  s += `<g class="spv-triad">`;
  for (const [lab, v, cls] of tri) {
    const [dx, dy] = dir(v);
    s += `<line class="${cls}l" x1="${t0[0]}" y1="${t0[1]}" x2="${r2(t0[0] + dx * tl)}" y2="${r2(t0[1] + dy * tl)}" marker-end="url(#spvArr${lab}i)"/>` +
      `<text class="spv-axl ${cls}t" x="${r2(t0[0] + dx * (tl + 7))}" y="${r2(t0[1] + dy * (tl + 7) + 3)}" text-anchor="middle">${lab}</text>`;
  }
  s += `</g>`;
  const defs = `<defs>${["1", "2", "3"].map(k => `<marker id="spvArr${k}i" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L8,4L0,8Z" class="spv-ax${k}f"/></marker>`).join("")}</defs>`;
  svg.innerHTML = uniq(defs + s);
  return svg;
}

/* ================================================================
   Frame section panel
   ================================================================ */
const val = v => (typeof v === "function" ? v() : v);

/** Effective A / I33 / I22 / J: the stored value when the section has
    one (exactly — never recomputed), else rectangle formulas, else the
    outline integrals (flagged approximate). */
export function spvProps(sec, geom) {
  const out = {};
  const rectP = sec && pos(sec.b) && pos(sec.h) && geom && geom.shape === "rect" ? spvRectProps({ b: sec.b, h: sec.h }) : null;
  for (const k of ["A", "I33", "I22", "J"]) {
    if (sec && isNum(sec[k])) out[k] = { v: sec[k], src: "stored" };
    else if (rectP) out[k] = { v: rectP[k], src: "rect" };
    else if (geom && geom.geo && !geom.ghost && k !== "J") out[k] = { v: geom.geo[k], src: "outline" };
    else if (geom && k === "J" && isNum(geom.J)) out[k] = { v: geom.J, src: "outline" };
    else out[k] = { v: null, src: "none" };
  }
  return out;
}

function propsTable(model, sec, geom, extra = []) {
  const P = spvProps(sec, geom);
  const t = h("table", { class: "spv-props" });
  const row = (lab, txt, attrs = {}) => t.appendChild(h("tr", attrs, [h("th", { text: lab }), h("td", { text: txt })]));
  const lab = { A: ["A", "area"], I33: ["I33", "inertia"], I22: ["I22", "inertia"], J: ["J", "inertia"] };
  for (const k of ["A", "I33", "I22", "J"]) {
    const p = P[k];
    row(lab[k][0], (p.src === "outline" ? "≈ " : "") + sciU(lab[k][1], p.v), { "data-prop": k, "data-si": p.v == null ? "" : String(p.v), "data-src": p.src });
  }
  const jm = spvJMethod(sec, model || {}, (sec && sec.name) || "");   // v1.22
  if (jm) row("J method", jm.label, { "data-prop": "J_method", "data-method": jm.method });
  const mat = sec && sec.material;
  const md = mat && ((model || {}).materials || {})[mat];
  row("Material", mat ? `${mat}${md && md.material_type ? " · " + md.material_type : ""}${md && isNum(md.E) ? " · E " + spvU.fmt("modulus", md.E, 0) + " " + spvU.label("modulus") : ""}` : "—", { "data-prop": "material" });
  for (const [k, v] of extra) row(k, v);
  return t;
}

/** Normalised segment list of a nonprismatic section (absolute / relative
    lengths resolved on a preview length). */
function npLayout(model, sec, Lprev) {
  const segs = (sec.segments || []).filter(Boolean);
  const absSum = segs.filter(s => s.length_type === "absolute").reduce((a, s) => a + (+s.length || 0), 0);
  const relSum = segs.filter(s => s.length_type !== "absolute").reduce((a, s) => a + (+s.length || 0), 0);
  const L = Lprev || Math.max(absSum * 1.5, 4);
  const rest = Math.max(0, L - absSum);
  let x = 0;
  return { L, segs: segs.map(s => {
    const len = s.length_type === "absolute" ? (+s.length || 0) : (relSum > 0 ? rest * (+s.length || 0) / relSum : 0);
    const o = { s, x0: x, x1: x + len };
    x += len; return o;
  }) };
}

function npContent(model, sec, name) {
  const wrap = h("div", { class: "spv-np" });
  const segs = (sec.segments || []).filter(Boolean);
  const secs = model.sections || {};
  const iName = segs.length ? segs[0].start_section : null, jName = segs.length ? segs[segs.length - 1].end_section : null;
  const gi = iName && secs[iName] ? spvGeometry(secs[iName], model, iName) : null;
  const gj = jName && secs[jName] ? spvGeometry(secs[jName], model, jName) : null;
  if (!gi || !gj || !gi.bbox || !gj.bbox) {
    wrap.appendChild(h("p", { class: "spv-msg is-bad", text: "Pick existing prismatic start / end sections to preview the taper." }));
    return { node: wrap, geom: gi || gj, invalid: true };
  }
  // i-end and j-end profiles to ONE common scale, side by side
  const W = 300, H = 150, half = W / 2;
  const span = Math.max(gi.bbox.w, gj.bbox.w, 1e-9), dep = Math.max(gi.bbox.h, gj.bbox.h, 1e-9);
  const sc = Math.min((half - 36) / span, (H - 46) / dep);
  const svg = svgEl(W, H, "spv-svg spv-np-ends", "i-end and j-end profiles to a common scale");
  let s = "";
  [[gi, iName, "i-end"], [gj, jName, "j-end"]].forEach(([g, nm, lab], k) => {
    const cx0 = half * k + half / 2, cy0 = (H - 18) / 2 + 4;
    const T = ([x, y]) => [cx0 + (x - (g.bbox.x0 + g.bbox.x1) / 2) * sc, cy0 - (y - (g.bbox.y0 + g.bbox.y1) / 2) * sc];
    s += `<path class="spv-outline ${matClass(model, secs[nm].material)}" fill-rule="evenodd" data-end="${lab}" d="${g.polys.map(P => pathD(P.pts, T)).join("")}"/>`;
    const [ax, ay] = T([g.geo.cx, g.geo.cy]);
    s += `<line class="spv-ax3" x1="${r2(ax)}" y1="${r2(ay)}" x2="${r2(ax + 20)}" y2="${r2(ay)}"/><line class="spv-ax2" x1="${r2(ax)}" y1="${r2(ay)}" x2="${r2(ax)}" y2="${r2(ay - 20)}"/>`;
    s += `<text class="spv-dimtxt" x="${r2(cx0)}" y="${H - 6}" text-anchor="middle">${esc(lab)}: ${esc(nm)} · h ${esc(dval(g.bbox.h))} ${esc(spvU.label("dim"))}</text>`;
  });
  s += `<line class="spv-ext" x1="${half}" y1="8" x2="${half}" y2="${H - 22}" stroke-dasharray="3 3"/>`;
  svg.innerHTML = s;
  wrap.appendChild(svg);
  // taper elevation: depth (local 2) along the member (local 1), true depth scale
  const lay = npLayout(model, sec, null);
  const E = svgEl(W, 92, "spv-svg spv-np-elev", "Taper elevation along the member");
  const hOf = n => { const ss = secs[n]; if (!ss) return 0; const g = spvGeometry(ss, model, n); return g.bbox ? g.bbox.h : 0; };
  const maxH = Math.max(...lay.segs.flatMap(o => [hOf(o.s.start_section), hOf(o.s.end_section)]), 1e-9);
  const xs = (W - 24) / (lay.L || 1), ys = Math.min(54 / maxH, xs * 4);
  const top = 14;
  let up = "", lo = "", e = "";
  lay.segs.forEach((o, i) => {
    const h0 = hOf(o.s.start_section) * ys, h1 = hOf(o.s.end_section) * ys;
    const xa = 12 + o.x0 * xs, xb = 12 + o.x1 * xs;
    up += `${i ? "L" : "M"}${r2(xa)},${top}L${r2(xb)},${top}`;
    lo = `L${r2(xb)},${r2(top + h1)}L${r2(xa)},${r2(top + h0)}` + lo;
    if (i) e += `<line class="spv-ext" x1="${r2(xa)}" y1="${top - 4}" x2="${r2(xa)}" y2="${r2(top + Math.max(h0, 4) + 4)}"/>`;
  });
  E.innerHTML = `<path class="spv-outline is-other" data-np-elev="1" d="${up}${lo.replace(/^L/, "L")}Z"/>${e}` +
    `<text class="spv-dimtxt" x="12" y="${top + maxH * ys + 16}">i</text><text class="spv-dimtxt" x="${W - 12}" y="${top + maxH * ys + 16}" text-anchor="end">j</text>` +
    `<text class="spv-dimtxt" x="${W / 2}" y="${top + maxH * ys + 16}" text-anchor="middle">${segs.length} segment${segs.length > 1 ? "s" : ""} · preview L ${esc(spvU.fmt("length", lay.L, 2))} ${esc(spvU.label("length"))}</text>`;
  wrap.appendChild(E);
  return { node: wrap, geom: gi, invalid: false };
}

/** Frame-section preview panel. opts: model, name, section (values or
    functions), live: [{id, key, kind, label}] inputs read on every
    keystroke, recompute(sec) (rectangles: A / I / J from b × h),
    compact (no iso), title. Returns the panel element with .update(). */
export function spvFramePanel(opts = {}) {
  spvEnsureCss();
  const panel = h("aside", { class: "spv-panel", "aria-label": "Member preview", "data-spv": "frame" });
  const head = h("div", { class: "spv-head" });
  const body = h("div", { class: "spv-body" });
  panel.append(head, body);
  const liveSection = () => {
    const base = clone(val(opts.section)) || null;
    if (!base) return { sec: null, msg: "" };
    let msg = "", changed = false;
    for (const L of opts.live || []) {
      const inp = (panel.closest(".modal") || document).querySelector("#" + CSS.escape(L.id));
      if (!inp || inp.disabled) continue;
      const txt = String(inp.value).trim();
      if (txt === "" || txt === spvU.inputValue(L.kind, base[L.key])) continue;
      const v = spvU.parse(L.kind, txt);
      if (!isFinite(v)) { msg = msg || `${L.label || L.key}: enter a number in ${spvU.label(L.kind) || "–"}.`; base[L.key] = 0; changed = true; continue; }
      if (!(v > 0)) { msg = msg || `${L.label || L.key} must be greater than zero.`; base[L.key] = 0; changed = true; continue; }
      base[L.key] = v; changed = true;
    }
    if (changed && !msg && opts.recompute) opts.recompute(base);
    return { sec: base, msg };
  };
  panel.update = () => {
    const model = val(opts.model) || {};
    const name = val(opts.name) || "";
    const { sec, msg } = liveSection();
    head.textContent = ""; body.textContent = "";
    if (!sec) {
      head.appendChild(h("span", { class: "spv-title", text: opts.emptyTitle || "Preview" }));
      body.appendChild(h("p", { class: "spv-msg", text: opts.emptyText || "Pick a section to preview it." }));
      return;
    }
    const isNp = sec.kind === "nonprismatic";
    let geom = null, invalid = msg;
    if (isNp) {
      const npc = npContent(model, sec, name);
      geom = npc.geom;
      head.append(h("span", { class: "spv-title", text: val(opts.title) || name || "Section" }), h("span", { class: "spv-chip", "data-shape": "nonprismatic", text: SHAPE_LABEL.nonprismatic }));
      body.appendChild(npc.node);
    } else {
      geom = spvGeometry(sec, model, name);
      invalid = invalid || geom.invalid;
      if (invalid) geom.invalid = invalid;
      head.append(h("span", { class: "spv-title", text: val(opts.title) || name || "Section" }), h("span", { class: "spv-chip", "data-shape": geom.shape, text: geom.label }));
      const mc = matClass(model, sec.material);
      body.appendChild(spvSectionSvg(geom, { matCls: mc }));
      body.appendChild(h("div", { class: "spv-cap-note muted", text: "Looking down local 1 · 2 along the depth, 3 along the width" }));
      if (!opts.compact) body.appendChild(spvIsoSvg(geom, { matCls: mc }));
    }
    if (invalid) body.appendChild(h("p", { class: "spv-msg is-bad", role: "alert", text: invalid }));
    for (const n of (geom && geom.notes) || []) body.appendChild(h("p", { class: "spv-msg", text: n }));
    body.appendChild(propsTable(model, isNp ? sec : (invalid ? {} : sec), invalid ? null : geom,
      isNp ? [["Segments", String((sec.segments || []).length)]] : []));
  };
  bindUnits(panel);
  panel.update();
  return panel;
}

function bindUnits(panel) {
  if (!spvU.onUnitsChange) return;
  let un = null;
  un = spvU.onUnitsChange(() => {
    if (!panel.isConnected && panel.dataset.bound) { un && un(); return; }
    panel.update();
  });
  // mark bound after first attach so a never-attached panel can unsubscribe later
  requestAnimationFrame(() => { panel.dataset.bound = "1"; });
}

/* ================================================================
   Shell (slab / wall) panel
   ================================================================ */
const SHELL_TYPES = {
  shell: ["Shell (default)", "membrane + bending", "ShellMITC4"], shell_thin: ["Shell-Thin", "membrane + bending (Kirchhoff)", "ShellDKGQ"],
  shell_thick: ["Shell-Thick", "membrane + bending (Mindlin)", "ASDShellQ4"], membrane: ["Membrane", "in-plane only", "ASDShellQ4"],
  plate_thin: ["Plate-Thin", "bending only", "ShellDKGQ"], plate_thick: ["Plate-Thick", "bending only (Mindlin)", "ASDShellQ4"],
};
export function spvShellPanel(opts = {}) {
  spvEnsureCss();
  const panel = h("aside", { class: "spv-panel", "aria-label": "Slab / wall preview", "data-spv": "shell" });
  const head = h("div", { class: "spv-head" }), body = h("div", { class: "spv-body" });
  panel.append(head, body);
  panel.update = () => {
    const model = val(opts.model) || {};
    const name = val(opts.name) || "";
    const sec0 = clone(val(opts.section));
    head.textContent = ""; body.textContent = "";
    if (!sec0) { head.appendChild(h("span", { class: "spv-title", text: "Preview" })); body.appendChild(h("p", { class: "spv-msg", text: "Pick a slab / wall section to preview it." })); return; }
    let msg = "";
    const tIn = opts.thicknessId && (panel.closest(".modal") || document).querySelector("#" + CSS.escape(opts.thicknessId));
    if (tIn && String(tIn.value).trim() !== "" && String(tIn.value).trim() !== spvU.inputValue("dim", sec0.thickness)) {
      const v = spvU.parse("dim", tIn.value);
      if (!isFinite(v)) msg = `Thickness: enter a number in ${spvU.label("dim")}.`;
      else if (!(v > 0)) msg = "Thickness must be greater than zero.";
      else sec0.thickness = v;
    }
    const st = sec0.shell_type || "shell";
    const T = SHELL_TYPES[st] || [st, "", ""];
    const layers = sec0.layered && Array.isArray(sec0.layered.layers) ? sec0.layered.layers : null;
    const t = layers ? layers.reduce((a, l) => a + (+l.t || 0), 0) : +sec0.thickness;
    const users = (model.shells || []).filter(s => s.section === name);
    const oneWay = users.filter(s => s.distribution === "one_way");
    const walls = users.filter(s => s.kind === "wall").length;
    head.append(h("span", { class: "spv-title", text: val(opts.title) || name || "Section" }), h("span", { class: "spv-chip", "data-shell-type": st, text: T[0] }));
    if (!(t > 0) && !msg) msg = "Thickness must be greater than zero.";
    body.appendChild(shellIso(model, sec0, { t: t > 0 ? t : 0.2, invalid: !!msg, st, oneWay, isWall: walls > 0 && walls === users.length }));
    body.appendChild(shellLayers(model, sec0, layers, t > 0 ? t : 0.2, !!msg));
    if (msg) body.appendChild(h("p", { class: "spv-msg is-bad", role: "alert", text: msg }));
    const tb = h("table", { class: "spv-props" });
    const row = (k, v, a = {}) => tb.appendChild(h("tr", a, [h("th", { text: k }), h("td", { text: v })]));
    row("Type", `${T[0]} · ${T[1]}`, { "data-prop": "shell_type" });
    row("Thickness", msg ? "—" : `${spvU.fmt("dim", t, 3)} ${spvU.label("dim")}${layers ? " (Σ layers)" : ""}`, { "data-prop": "thickness", "data-si": msg ? "" : String(t) });
    const md = (model.materials || {})[sec0.material];
    row("Material", sec0.material ? `${sec0.material}${md && md.material_type ? " · " + md.material_type : ""}` : "—");
    if (layers) row("Layers", `${layers.length} (nonlinear analyses)`);
    row("Used by", users.length ? `${users.length - walls ? `${users.length - walls} slab${users.length - walls > 1 ? "s" : ""}` : ""}${walls && users.length - walls ? " · " : ""}${walls ? `${walls} wall${walls > 1 ? "s" : ""}` : ""}` +
      (oneWay.length ? ` · ${oneWay.length} one-way` : "") : "—");
    body.appendChild(tb);
  };
  bindUnits(panel);
  panel.update();
  return panel;
}

function shellIso(model, sec, { t, invalid, st, oneWay, isWall }) {
  const W = 300, H = 132;
  const svg = svgEl(W, H, "spv-svg spv-shell-iso", "Slab / wall element, isometric view");
  // a square plate a × a × t, a fixed; thickness exaggerated to stay visible but kept proportional among sections
  const a = 1, tv = Math.min(0.32, Math.max(0.035, t / 1.2));
  const P = (x, y, z) => { const p = proj([x - a / 2, z, y - a / 2]); return [p[0], p[1]]; };   // plate in local 1 (x) – 2 (y) plane, thickness along z (up)
  const pts = [[0, 0], [a, 0], [a, a], [0, a]];
  const all = pts.flatMap(([x, y]) => [P(x, y, 0), P(x, y, tv)]);
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const [x, y] of all) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  const sc = Math.min((W - 132) / (x1 - x0), (H - 24) / (y1 - y0)), ox = 18 - x0 * sc, oy = 12 - y0 * sc;
  const S = ([x, y]) => `${r2(ox + x * sc)},${r2(oy + y * sc)}`;
  const cls = `spv-face ${matClass(model, sec.material)}${invalid ? " spv-invalid" : ""}`;
  const top = pts.map(([x, y]) => S(P(x, y, tv)));
  const side1 = [S(P(0, 0, 0)), S(P(a, 0, 0)), S(P(a, 0, tv)), S(P(0, 0, tv))];
  const side2 = [S(P(0, 0, 0)), S(P(0, a, 0)), S(P(0, a, tv)), S(P(0, 0, tv))];
  let s = `<path class="${cls}" style="--spv-l:.62" d="M${side1.join("L")}Z"/><path class="${cls}" style="--spv-l:.75" d="M${side2.join("L")}Z"/>` +
    `<path class="${cls} spv-cap" style="--spv-l:.92" d="M${top.join("L")}Z" data-shell-top="1"/>`;
  // local 1 / 2 on the top face
  const c = P(a / 2, a / 2, tv), e1 = P(a * 0.85, a / 2, tv), e2 = P(a / 2, a * 0.85, tv);
  const L = (p, q, k) => `<line class="spv-ax${k}l" x1="${S(p).split(",")[0]}" y1="${S(p).split(",")[1]}" x2="${S(q).split(",")[0]}" y2="${S(q).split(",")[1]}" marker-end="url(#spvSh${k})"/>`;
  s += L(c, e1, 1) + L(c, e2, 2);
  s += `<text class="spv-axl spv-ax1t" x="${S(e1).split(",")[0]}" y="${+S(e1).split(",")[1] + 12}">1</text><text class="spv-axl spv-ax2t" x="${+S(e2).split(",")[0] + 4}" y="${+S(e2).split(",")[1] - 3}">2</text>`;
  // one-way span arrow
  if (oneWay && oneWay.length) {
    const dir = oneWay[0].one_way_dir === 2 ? 2 : 1;
    const p0 = dir === 1 ? P(0.12, 0.25, tv) : P(0.25, 0.12, tv), p1 = dir === 1 ? P(0.88, 0.25, tv) : P(0.25, 0.88, tv);
    s += `<line class="spv-oneway" data-one-way="${dir}" x1="${S(p0).split(",")[0]}" y1="${S(p0).split(",")[1]}" x2="${S(p1).split(",")[0]}" y2="${S(p1).split(",")[1]}" marker-start="url(#spvOw)" marker-end="url(#spvOw)"/>`;
  }
  // stiffness-type glyphs (right): membrane = in-plane arrows, plate = bending arcs
  const gx = W - 104, gy = 30;
  const mem = !String(st).startsWith("plate"), pla = st !== "membrane";
  s += `<g class="spv-stype${mem ? "" : " is-off"}" data-membrane="${mem ? 1 : 0}"><path d="M${gx},${gy}h26M${gx + 26},${gy}l-5,-3M${gx + 26},${gy}l-5,3M${gx},${gy}l5,-3M${gx},${gy}l5,3"/><text x="${gx + 32}" y="${gy + 4}">membrane</text></g>`;
  s += `<g class="spv-stype${pla ? "" : " is-off"}" data-plate="${pla ? 1 : 0}"><path d="M${gx},${gy + 30}q13,-12 26,0M${gx + 26},${gy + 30}l-6,-1M${gx + 26},${gy + 30}l-1,-6"/><text x="${gx + 32}" y="${gy + 32}">bending</text></g>`;
  if (oneWay && oneWay.length) s += `<text class="spv-dimtxt" x="${gx}" y="${gy + 60}">one-way (span ${oneWay[0].one_way_dir === 2 ? "2" : "1"})</text>`;
  else if (isWall) s += `<text class="spv-dimtxt" x="${gx}" y="${gy + 60}">wall</text>`;
  const defs = `<defs><marker id="spvSh1" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L8,4L0,8Z" class="spv-ax1f"/></marker>` +
    `<marker id="spvSh2" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L8,4L0,8Z" class="spv-ax2f"/></marker>` +
    `<marker id="spvOw" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L8,4L0,8Z" class="spv-owf"/></marker></defs>`;
  svg.innerHTML = uniq(defs + s);
  return svg;
}

function shellLayers(model, sec, layers, t, invalid) {
  const W = 300, H = layers ? Math.min(150, 34 + 16 * layers.length) : 62;
  const svg = svgEl(W, H, "spv-svg spv-shell-layers", "Thickness diagram");
  const x0 = 64, x1 = W - 12, y0 = 8, hh = H - 24;
  let s = "";
  if (!layers) {
    s += `<rect class="spv-face ${matClass(model, sec.material)}${invalid ? " spv-invalid" : ""}" style="--spv-l:.8" x="${x0}" y="${y0}" width="${x1 - x0}" height="${hh}" data-layer="0"/>`;
    s += `<line class="spv-dim" x1="${x0 - 12}" y1="${y0}" x2="${x0 - 12}" y2="${y0 + hh}"/><line class="spv-tick" x1="${x0 - 15}" y1="${y0 + 3}" x2="${x0 - 9}" y2="${y0 - 3}"/><line class="spv-tick" x1="${x0 - 15}" y1="${y0 + hh + 3}" x2="${x0 - 9}" y2="${y0 + hh - 3}"/>`;
    s += `<text class="spv-dimtxt" x="${x0 - 16}" y="${y0 + hh / 2 + 4}" text-anchor="end">t ${esc(dval(t))}</text>`;
    s += `<text class="spv-dimtxt" x="${(x0 + x1) / 2}" y="${H - 4}" text-anchor="middle">t = ${esc(dval(t))} ${esc(spvU.label("dim"))} · ${esc(sec.material || "")}</text>`;
  } else {
    // layers stacked through the thickness, heights proportional to t_i (min 9 px)
    const tt = layers.reduce((a, l) => a + (+l.t || 0), 0) || 1;
    const raw = layers.map(l => Math.max(9, (+l.t || 0) / tt * hh));
    const k = hh / raw.reduce((a, v) => a + v, 0);
    let y = y0;
    layers.forEach((l, i) => {
      const lh = raw[i] * k;
      const steel = l.kind === "steel";
      s += `<rect class="spv-face ${steel ? "is-steel" : "is-conc"}" style="--spv-l:${steel ? ".7" : ".85"}" x="${x0}" y="${r2(y)}" width="${x1 - x0}" height="${r2(lh)}" data-layer="${i}"/>`;
      if (steel) s += `<path class="spv-hatch" d="${Array.from({ length: 14 }, (_, j) => `M${x0 + 8 + j * 16},${r2(y + lh - 1)}l${(+l.angle || 0) === 90 ? 0 : 6},${r2(-(lh - 2))}`).join("")}"/>`;
      s += `<text class="spv-dimtxt" x="${x0 - 6}" y="${r2(y + lh / 2 + 4)}" text-anchor="end">${esc(dval(+l.t || 0))}</text>`;
      if (lh >= 11) s += `<text class="spv-laytxt" x="${x0 + 6}" y="${r2(y + lh / 2 + 4)}">${esc(l.material || "")} · ${esc(l.kind || "")}${steel ? ` ${+l.angle || 0}°` : ""}</text>`;
      y += lh;
    });
    s += `<text class="spv-dimtxt" x="${(x0 + x1) / 2}" y="${H - 4}" text-anchor="middle">Σt = ${esc(dval(tt))} ${esc(spvU.label("dim"))} · ${layers.length} layers</text>`;
  }
  svg.innerHTML = s;
  return svg;
}

/* ================================================================
   Spring / link DOF glyph
   ================================================================ */
const DOFS = ["U1", "U2", "U3", "R1", "R2", "R3"];
/** k: [6] SI stiffness, nl: {dof: "gap"|...} tags, twoNode: link i–j. */
export function spvDofGlyph({ k = [], nl = {}, title = "", twoNode = false, axes = "local" } = {}) {
  spvEnsureCss();
  const box = h("div", { class: "spv-dof", "data-spv": "dof" });
  const W = 300, H = 160;
  const svg = svgEl(W, H, "spv-svg spv-dof-svg", "Active degrees of freedom");
  const o = twoNode ? [170, 88] : [140, 92];
  // U1 → X (right), U2 → Y (receding), U3 → Z (up), as the joint-spring axes
  const dirs = { 1: [1, 0, 0], 2: [0, 0, 1], 3: [0, 1, 0] };
  const pr = v => { const p0 = proj([0, 0, 0]), p1 = proj(v); const dx = p1[0] - p0[0], dy = p1[1] - p0[1], l = Math.hypot(dx, dy) || 1; return [dx / l, dy / l]; };
  let s = "";
  const on = i => isNum(k[i]) && k[i] > 0;
  const P = (dx, dy, t, n = 0) => `${r2(o[0] + dx * t - dy * n)},${r2(o[1] + dy * t + dx * n)}`;
  if (twoNode) {             // the link lies along its local 1 axis, i → j
    const [ux, uy] = pr(dirs[1]), ix = o[0] - ux * 110, iy = o[1] - uy * 110;
    s += `<line class="spv-link" x1="${r2(ix)}" y1="${r2(iy)}" x2="${o[0]}" y2="${o[1]}"/><circle class="spv-node" cx="${r2(ix)}" cy="${r2(iy)}" r="4"/>` +
      `<text class="spv-dimtxt" x="${r2(ix)}" y="${r2(iy + 16)}" text-anchor="middle">i</text><text class="spv-dimtxt" x="${o[0] - 4}" y="${o[1] + 18}" text-anchor="end">j</text>`;
  }
  else s += `<path class="spv-ground" d="M${o[0]},${o[1]}v34M${o[0] - 18},${o[1] + 34}h36${[-16, -6, 4, 14].map(x => `M${o[0] + x},${o[1] + 34}l-6,7`).join("")}"/>`;
  for (let a = 1; a <= 3; a++) {
    const [dx, dy] = pr(dirs[a]);
    const L = 64, ti = a - 1, ri = a + 2;
    const cls = `spv-ax${a}`;
    if (on(ti)) {
      // translational spring: zig-zag along the axis, arrow at the end
      const zig = Array.from({ length: 8 }, (_, j) => `L${P(dx, dy, 16 + j * 4, j % 2 ? 5 : -5)}`).join("");
      s += `<g class="spv-dofg" data-dof="${DOFS[ti]}"><path class="${cls}l" d="M${P(dx, dy, 0)}L${P(dx, dy, 14)}${zig}L${P(dx, dy, 50)}L${P(dx, dy, L)}" marker-end="url(#spvDof${a})"/>` +
        `<text class="spv-axl ${cls}t" x="${r2(o[0] + dx * (L + 14))}" y="${r2(o[1] + dy * (L + 14) + 4)}" text-anchor="middle">${DOFS[ti]}</text></g>`;
    } else {
      s += `<g class="spv-dofg is-off" data-dof="${DOFS[ti]}"><path d="M${P(dx, dy, 6)}L${P(dx, dy, 44)}"/>` +
        `<text class="spv-axl" x="${r2(o[0] + dx * 54)}" y="${r2(o[1] + dy * 54 + 4)}" text-anchor="middle">${DOFS[ti]}</text></g>`;
    }
    // rotational spring: a curled arrow around the axis (only when active)
    if (on(ri)) {
      const t = 36, r = 9;
      s += `<g class="spv-dofg" data-dof="${DOFS[ri]}"><path class="${cls}l spv-rot" d="M${P(dx, dy, t, -r)}A${r},${r} 0 1 1 ${P(dx, dy, t + r * 0.8, r * 0.6)}" marker-end="url(#spvDof${a})"/>` +
        `<text class="spv-axl ${cls}t" x="${r2(o[0] + dx * t - dy * (r + 10))}" y="${r2(o[1] + dy * t + dx * (r + 10) + 4)}" text-anchor="middle">${DOFS[ri]}</text></g>`;
    }
  }
  s += `<circle class="spv-node" cx="${o[0]}" cy="${o[1]}" r="4.5"/>`;
  const defs = `<defs>${[1, 2, 3].map(a => `<marker id="spvDof${a}" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L8,4L0,8Z" class="spv-ax${a}f"/></marker>`).join("")}</defs>`;
  s = defs + s;
  svg.innerHTML = uniq(s);
  const head = h("div", { class: "spv-head" }, [h("span", { class: "spv-title", text: title || "Degrees of freedom" }), h("span", { class: "spv-chip", text: `${k.filter((v, i) => on(i)).length} active · ${axes}` })]);
  const tb = h("table", { class: "spv-props spv-dof-tbl" });
  DOFS.forEach((d, i) => {
    const kind = i < 3 ? "stiffness" : "rot_stiffness";
    tb.appendChild(h("tr", { class: on(i) ? "" : "is-off", "data-dof": d, "data-si": isNum(k[i]) ? String(k[i]) : "" }, [h("th", { text: d }),
      h("td", { text: on(i) ? `${spvU.sci(kind, k[i], 3)} ${spvU.label(kind)}${nl[d] ? " · " + nl[d] : ""}` : (nl[d] || "free") })]));
  });
  box.append(head, svg, tb);
  return box;
}

/* ================================================================
   Layout helper for dlgkit dialogs: fields | preview, collapsing
   the preview under the fields on a narrow dialog.
   ================================================================ */
export function spvSplit(fieldNodes, panel, { onEvent } = {}) {
  spvEnsureCss();
  const fields = h("div", { class: "spv-fields" });
  (Array.isArray(fieldNodes) ? fieldNodes : [fieldNodes]).forEach(n => n && fields.appendChild(n));
  const host = h("div", { class: "spv-host" }, [h("div", { class: "spv-split" }, [fields, panel])]);
  let raf = 0;
  const kick = () => { if (raf) return; raf = requestAnimationFrame(() => { raf = 0; try { (onEvent || panel.update)(); } catch (e) { console.warn("secpreview", e); } }); };
  for (const ev of ["input", "change", "click", "keyup"]) fields.addEventListener(ev, kick);
  // list dialogs re-render their table (selection, Modify → OK): follow it
  try { new MutationObserver(kick).observe(fields, { childList: true, subtree: true }); } catch { /* ignore */ }
  host.kick = kick;
  return host;
}

/* ================================================================
   Legacy editors (decorated, never edited)
   ================================================================ */
export function initSecPreview(sky) {
  if (!sky || !sky.store || sky.secPreview) return;
  spvEnsureCss();
  const S = sky.store;
  const api = { geometry: spvGeometry, polyProps: spvPolyProps, framePanel: spvFramePanel, shellPanel: spvShellPanel, dofGlyph: spvDofGlyph, props: spvProps };
  sky.secPreview = api;
  const raf = fn => { let q = 0; return () => { if (q) return; q = requestAnimationFrame(() => { q = 0; try { fn(); } catch (e) { console.warn("secpreview", e); } }); }; };

  /* ---- Section Manager (?dlg=legacy): preview of the row under the pointer / focus */
  const mgrBody = document.getElementById("sectionModalBody");
  if (mgrBody) {
    let cur = null;                                  // {type: "frame"|"shell", name, row}
    const holder = h("div", { class: "spv-legacy spv-side", id: "spvMgr" });
    const rowSec = () => {
      if (!cur) return null;
      const m = S.model || {};
      const src = cur.type === "frame" ? (m.sections || {})[cur.name] : (m.shell_sections || {})[cur.name];
      if (!src) return null;
      const sec = clone(src);
      const ins = cur.row ? [...cur.row.querySelectorAll("input")] : [];
      if (cur.type === "frame" && !(sec.shape === "W" || !(sec.b > 0 && sec.h > 0)) && ins.length >= 3) {
        const b = spvU.parse("dim", ins[1].value), hh = spvU.parse("dim", ins[2].value);
        if (ins[1].value.trim() !== spvU.inputValue("dim", sec.b) || ins[2].value.trim() !== spvU.inputValue("dim", sec.h)) {
          sec.b = b > 0 ? b : 0; sec.h = hh > 0 ? hh : 0;
          if (sec.b && sec.h) spvRectProps(sec);
        }
      } else if (cur.type === "shell" && ins.length >= 2) {
        const t = spvU.parse("dim", ins[1].value);
        if (ins[1].value.trim() !== spvU.inputValue("dim", sec.thickness)) sec.thickness = t > 0 ? t : 0;
      }
      return sec;
    };
    const fp = spvFramePanel({ model: () => S.model, name: () => cur && cur.name, section: () => cur && cur.type === "frame" ? rowSec() : null, compact: false,
      emptyText: "Point at (or click into) a frame or slab / wall row to preview it." });
    const sp = spvShellPanel({ model: () => S.model, name: () => cur && cur.name, section: () => cur && cur.type === "shell" ? rowSec() : null });
    holder.append(fp, sp);
    const show = () => { fp.hidden = !!(cur && cur.type === "shell"); sp.hidden = !(cur && cur.type === "shell"); fp.update(); sp.update(); };
    mgrBody.classList.add("spv-legacy-split");
    mgrBody.appendChild(holder);
    show();
    const pick = e => {
      const box = e.target.closest && e.target.closest("#frameSectionRows, #shellSectionRows");
      if (!box) return;
      let row = e.target.closest(".mgr-row");
      if (!row) { let p = e.target.closest("#frameSectionRows > *, #shellSectionRows > *"); while (p && !(p.classList.contains("mgr-row") && !p.classList.contains("head"))) p = p.previousElementSibling; row = p; }
      if (!row || row.classList.contains("head")) return;
      const nameIn = row.querySelector("input");
      if (!nameIn) return;
      const type = box.id === "frameSectionRows" ? "frame" : "shell";
      const name = nameIn.defaultValue || nameIn.value;
      cur = { type, name, row };
      show();
    };
    const upd = raf(show);
    mgrBody.addEventListener("mouseover", pick);
    mgrBody.addEventListener("focusin", pick);
    mgrBody.addEventListener("input", e => { pick(e); upd(); });
    mgrBody.addEventListener("change", upd);
  }

  /* ---- dialogs that appear later: Nonprismatic, Spring Properties, Joint Springs, Link Properties */
  const decorate = back => {
    if (!back || !back.id || back.dataset.spvDone) return;
    const mb = back.querySelector(".modal-body");
    if (!mb) return;
    if (back.id === "npModal") {
      back.dataset.spvDone = "1";
      const npSection = () => {
        const segs = [];
        for (let i = 0; i < 200; i++) {
          const st = back.querySelector("#npStart" + i), en = back.querySelector("#npEnd" + i);
          if (!st || !en) break;
          const lt = back.querySelector("#npLt" + i), ln = back.querySelector("#npLen" + i);
          const type = lt ? lt.value : "relative";
          const len = ln ? spvU.parse(type === "absolute" ? "length" : "none", ln.value) : 1;
          segs.push({ start_section: st.value, end_section: en.value, length: isFinite(len) ? len : 1, length_type: type });
        }
        const nm = back.querySelector("#npName");
        const first = (S.model.sections || {})[segs.length ? segs[0].start_section : ""] || {};
        return { kind: "nonprismatic", segments: segs, material: first.material, A: first.A, I33: first.I33, I22: first.I22, J: first.J, name: nm ? nm.value : "" };
      };
      const pv = spvFramePanel({ model: () => S.model, name: () => { const nm = back.querySelector("#npName"); return nm ? nm.value : ""; }, section: npSection });
      pv.classList.add("spv-side");
      mb.classList.add("spv-legacy-split");
      mb.appendChild(pv);
      const up = raf(() => pv.update());
      for (const ev of ["input", "change", "click"]) back.addEventListener(ev, up);
    } else if (back.id === "spPropsDlg" || back.id === "spJointDlg" || back.id === "lhLinkDlg") {
      back.dataset.spvDone = "1";
      const slot = h("div", { class: "spv-side spv-panel", "data-spv": "dofwrap" });
      mb.classList.add("spv-legacy-split");
      mb.appendChild(slot);
      const read = () => {
        const kOf = sel => DOFS.map((d, i) => {
          const inp = back.querySelector(`${sel}[data-dof="${d}"]`) || back.querySelectorAll(sel)[i];
          if (!inp || String(inp.value).trim() === "") return 0;
          const v = spvU.parse(i < 3 ? "stiffness" : "rot_stiffness", inp.value);
          return isFinite(v) ? v : 0;
        });
        if (back.id === "spPropsDlg") {
          const kind = (back.querySelector("#spPropKind") || {}).value || "linear";
          const nl = {};
          if (["compression_only", "tension_only", "gap"].includes(kind)) nl[(back.querySelector("#spPropNlDof") || {}).value || "U3"] = kind === "gap" ? "gap" : kind === "compression_only" ? "compression only" : "tension only";
          if (kind === "multilinear") back.querySelectorAll(".spCurveOn").forEach(c => { if (c.checked) nl[c.dataset.dof] = "curve"; });
          const nm = (back.querySelector("#spPropName") || {}).value || "";
          return { k: kOf(".spK"), nl, title: nm ? `${nm} · ${kind.replace(/_/g, " ")}` : "Spring property", axes: "local 1-2-3" };
        }
        if (back.id === "spJointDlg") {
          const mode = (back.querySelector("input[name=jsMode]:checked") || {}).value;
          if (mode === "property") {
            const pn = (back.querySelector("#jsProperty") || {}).value;
            const p = ((S.model || {}).spring_properties || {})[pn] || {};
            return { k: (p.k || []).slice(), nl: {}, title: pn ? `Property ${pn}` : "Named property", axes: "local" };
          }
          return { k: mode === "delete" ? [0, 0, 0, 0, 0, 0] : kOf(".jsK"), nl: {}, title: mode === "delete" ? "Springs removed" : "Inline stiffness", axes: "global" };
        }
        const type = (back.querySelector("#lhType") || {}).value || "elastic";
        const ks = back.querySelectorAll(".lhK");
        let k;
        if (ks.length === 6) k = kOf(".lhK");
        else {
          const ref = (S.selection || []).find(r => r.type === "link");
          const l = ref && ((S.model || {}).links || []).find(x => x.uid === ref.uid);
          k = l && Array.isArray(l.stiffness) ? l.stiffness.slice() : [];
        }
        const nl = {};
        if (type !== "elastic") nl.U1 = type.replace(/_/g, " ");
        return { k, nl, title: `Link · ${type.replace(/_/g, " ")}`, twoNode: true, axes: "local" };
      };
      const up = raf(() => { slot.textContent = ""; slot.appendChild(spvDofGlyph(read())); });
      up();
      for (const ev of ["input", "change", "click"]) back.addEventListener(ev, up);
    }
  };
  const scan = () => document.querySelectorAll("#npModal, #spPropsDlg, #spJointDlg, #lhLinkDlg").forEach(decorate);
  const mo = new MutationObserver(muts => { for (const m of muts) for (const n of m.addedNodes) if (n.nodeType === 1 && n.classList && n.classList.contains("modal-backdrop")) decorate(n); });
  const app = document.getElementById("app");
  if (app) mo.observe(app, { childList: true });
  mo.observe(document.body, { childList: true });
  scan();
  return api;
}

export default { spvGeometry, spvPolyProps, spvFramePanel, spvShellPanel, spvDofGlyph, spvSplit, spvSectionSvg, spvIsoSvg, spvProps, initSecPreview };
