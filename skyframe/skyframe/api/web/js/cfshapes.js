/* SkyFrame Wave B — cold-formed steel shapes (lipped C, lipped Z, hat).

   A line-by-line port of skyframe/core/coldformed.py (CONTRACT "Orthotropic
   materials, slab / deck property types, cold-formed and composite
   sections, named line / area springs"): AISI "linear method" with rounded
   corners — centre line (straight elements + 90° arcs of radius r + t/2)
   times t; A / centroid / I33 / I22 / I23 exact line integrals (+ t³/12
   self terms); J = t³L/3; shear centre + Cw from the thin-walled sectorial
   coordinate (arcs 64 chords, straights 40 pieces); S = I / c with c the
   extreme fibre of the outline; Z from the equal-area axis.

   Hooked into js/secshapes.js (SS_TYPES / ssValidate / ssProps /
   ssShearAreas / ssPreview) so the Frame Section Property Data shape list,
   the readouts and the js/secpreview.js drawing pick the shapes up. SI. */

export const CF_TYPES = ["C_lipped", "Z_lipped", "hat"];
export const CF_REQ = { C_lipped: ["d", "b", "dl", "t"], Z_lipped: ["d", "b", "dl", "t"], hat: ["d", "b", "bf", "t"] };
export const CF_OPT = { C_lipped: ["r"], Z_lipped: ["r"], hat: ["r"] };
export const CF_SHEAR = { C_lipped: "As2 = d·t; As3 = 5/3·b·t", Z_lipped: "As2 = d·t; As3 = 5/3·b·t", hat: "As2 = 2·d·t; As3 = 5/6·(b + 2·bf)·t" };
export const CF_SS_TYPES = [
  { key: "C_lipped", group: "Cold-Formed", label: "Lipped Channel (C)", dims: [["d", "Depth, d"], ["b", "Flange Width, b"], ["dl", "Lip Length, dl"], ["t", "Thickness, t"], ["r", "Inside Bend Radius, r", true]], def: { d: 0.2032, b: 0.0635, dl: 0.0159, t: 0.00152, r: 0.00229 } },
  { key: "Z_lipped", group: "Cold-Formed", label: "Lipped Zee (Z)", dims: [["d", "Depth, d"], ["b", "Flange Width, b"], ["dl", "Lip Length, dl"], ["t", "Thickness, t"], ["r", "Inside Bend Radius, r", true]], def: { d: 0.2032, b: 0.0635, dl: 0.0191, t: 0.00152, r: 0.00229 } },
  { key: "hat", group: "Cold-Formed", label: "Hat Section", dims: [["d", "Depth, d"], ["b", "Crown Width, b"], ["bf", "Flange Width, bf"], ["t", "Thickness, t"], ["r", "Inside Bend Radius, r", true]], def: { d: 0.0762, b: 0.0762, bf: 0.0381, t: 0.00152, r: 0.00229 } },
];
const ARC = 64;
export const cfIs = typ => CF_TYPES.includes(typ);

/** "" or a geometry problem (dims already > 0). */
export function cfCheck(typ, g) {
  const t = g.t, r = g.r || 0, rc = r + t / 2;
  if (typ !== "hat") {
    if (2 * (r + t) >= g.d) return "The bends do not fit in the depth.";
    if (2 * (r + t) >= g.b) return "The bends do not fit in the flange width.";
    if (g.dl <= r + t) return "The lip is shorter than the bend (dl > r + t).";
    if (2 * g.dl >= g.d) return "The lips overlap (2·dl ≥ d).";
  } else {
    if (2 * (r + t) >= g.d) return "The bends do not fit in the depth.";
    if (2 * (r + t) >= g.b) return "The bends do not fit in the crown width.";
    if (g.bf <= rc + t / 2) return "The flange is shorter than the bend.";
  }
  return "";
}

function centerline(typ, g) {
  const { d, t } = g, h = t / 2;
  if (typ === "C_lipped") { const { b, dl } = g; return [[b - h, d - dl], [b - h, d - h], [h, d - h], [h, h], [b - h, h], [b - h, dl]]; }
  if (typ === "Z_lipped") { const { b, dl } = g; return [[b - t, d - dl], [b - t, d - h], [0, d - h], [0, h], [-(b - t), h], [-(b - t), dl]]; }
  const { b, bf } = g, xw = b / 2 - h;
  return [[-(b / 2 + bf), h], [-xw, h], [-xw, d - h], [xw, d - h], [xw, h], [b / 2 + bf, h]];
}
const unit = (a, b) => { const dx = b[0] - a[0], dy = b[1] - a[1], n = Math.hypot(dx, dy); return [dx / n, dy / n]; };

function pieces(typ, g) {
  const pts = centerline(typ, g), r = g.r || 0, rc = r + g.t / 2, out = [];
  let start = pts[0];
  for (let i = 1; i < pts.length - 1; i++) {
    const P = pts[i], ui = unit(pts[i - 1], P), uo = unit(P, pts[i + 1]);
    if (r <= 0) { out.push(["line", start, P]); start = P; continue; }
    const T1 = [P[0] - rc * ui[0], P[1] - rc * ui[1]], T2 = [P[0] + rc * uo[0], P[1] + rc * uo[1]];
    const C = [T1[0] + rc * uo[0], T1[1] + rc * uo[1]];
    out.push(["line", start, T1]);
    const a0 = Math.atan2(T1[1] - C[1], T1[0] - C[0]), a1 = Math.atan2(T2[1] - C[1], T2[0] - C[0]);
    const cr = ui[0] * uo[1] - ui[1] * uo[0];
    let da = a1 - a0;
    if (cr > 0) { while (da <= 0) da += 2 * Math.PI; } else { while (da >= 0) da -= 2 * Math.PI; }
    out.push(["arc", C[0], C[1], rc, a0, a0 + da]);
    start = T2;
  }
  out.push(["line", start, pts[pts.length - 1]]);
  return out;
}

function moments(pcs, t) {
  let L = 0, Sx = 0, Sy = 0, Ixx = 0, Iyy = 0, Ixy = 0;
  const k3 = t ** 3 / 12;
  for (const p of pcs) {
    if (p[0] === "line") {
      const [x0, y0] = p[1], [x1, y1] = p[2], l = Math.hypot(x1 - x0, y1 - y0);
      if (l <= 0) continue;
      const xm = (x0 + x1) / 2, ym = (y0 + y1) / 2, dx = x1 - x0, dy = y1 - y0, nx = -dy / l, ny = dx / l;
      L += l; Sx += l * xm; Sy += l * ym;
      Ixx += t * l * (ym * ym + dy * dy / 12) + k3 * l * ny * ny;
      Iyy += t * l * (xm * xm + dx * dx / 12) + k3 * l * nx * nx;
      Ixy += t * l * (xm * ym + dx * dy / 12) + k3 * l * nx * ny;
    } else {
      const [, cx, cy, R, a, b] = p, s = b > a ? 1 : -1, D = b - a;
      const ic = Math.sin(b) - Math.sin(a), is = -Math.cos(b) + Math.cos(a);
      const icc = D / 2 + (Math.sin(2 * b) - Math.sin(2 * a)) / 4, iss = D / 2 - (Math.sin(2 * b) - Math.sin(2 * a)) / 4;
      const isc = (Math.sin(b) ** 2 - Math.sin(a) ** 2) / 2, f = R * s;
      L += Math.abs(D) * R;
      Sx += f * (cx * D + R * ic); Sy += f * (cy * D + R * is);
      Ixx += t * f * (cy * cy * D + 2 * cy * R * is + R * R * iss) + k3 * f * iss;
      Iyy += t * f * (cx * cx * D + 2 * cx * R * ic + R * R * icc) + k3 * f * icc;
      Ixy += t * f * (cx * cy * D + cx * R * is + cy * R * ic + R * R * isc) + k3 * f * isc;
    }
  }
  return { L, Sx, Sy, Ixx, Iyy, Ixy };
}

function nodes(pcs, nLine = 1) {
  const out = [];
  for (const p of pcs) {
    let seg;
    if (p[0] === "line") { const [x0, y0] = p[1], [x1, y1] = p[2]; seg = Array.from({ length: nLine + 1 }, (_, k) => [x0 + (x1 - x0) * k / nLine, y0 + (y1 - y0) * k / nLine]); }
    else { const [, cx, cy, R, a, b] = p; seg = Array.from({ length: ARC + 1 }, (_, k) => [cx + R * Math.cos(a + (b - a) * k / ARC), cy + R * Math.sin(a + (b - a) * k / ARC)]); }
    for (const q of seg) if (!out.length || Math.hypot(q[0] - out[out.length - 1][0], q[1] - out[out.length - 1][1]) > 1e-15) out.push(q);
  }
  return out;
}

function sectorial(nd, t, xc, yc, Ixx, Iyy, Ixy) {
  const X = nd.map(p => p[0] - xc), Y = nd.map(p => p[1] - yc), n = nd.length;
  const w = [0];
  for (let i = 0; i < n - 1; i++) w.push(w[w.length - 1] + X[i] * Y[i + 1] - X[i + 1] * Y[i]);
  let Iwx = 0, Iwy = 0;
  for (let i = 0; i < n - 1; i++) {
    const a = t * Math.hypot(X[i + 1] - X[i], Y[i + 1] - Y[i]) / 6;
    Iwx += a * (2 * X[i] * w[i] + X[i] * w[i + 1] + X[i + 1] * w[i] + 2 * X[i + 1] * w[i + 1]);
    Iwy += a * (2 * Y[i] * w[i] + Y[i] * w[i + 1] + Y[i + 1] * w[i] + 2 * Y[i + 1] * w[i + 1]);
  }
  const det = (-Ixy) * Ixy - Iyy * (-Ixx);
  const xs = ((-Iwx) * Ixy - Iyy * (-Iwy)) / det, ys = ((-Ixy) * (-Iwy) - (-Ixx) * (-Iwx)) / det;
  const ws = w.map((v, i) => v + ys * X[i] - xs * Y[i]);
  let A = 0, wm = 0;
  for (let i = 0; i < n - 1; i++) { const l = Math.hypot(X[i + 1] - X[i], Y[i + 1] - Y[i]); A += t * l; wm += t * l * (ws[i] + ws[i + 1]) / 2; }
  wm /= A;
  let Cw = 0;
  for (let i = 0; i < n - 1; i++) { const l = Math.hypot(X[i + 1] - X[i], Y[i + 1] - Y[i]), a = ws[i] - wm, b = ws[i + 1] - wm; Cw += t * l * (a * a + a * b + b * b) / 3; }
  return [xs, ys, Cw];
}

function plastic(nd, t, ax) {
  const segs = [];
  for (let i = 0; i < nd.length - 1; i++) segs.push([nd[i][ax], nd[i + 1][ax], t * Math.hypot(nd[i + 1][0] - nd[i][0], nd[i + 1][1] - nd[i][1])]);
  const total = segs.reduce((s, x) => s + x[2], 0);
  const below = p => segs.reduce((s, [a, b, w]) => { const lo = Math.min(a, b), hi = Math.max(a, b); return s + (hi - lo < 1e-15 ? (a <= p ? w : 0) : w * Math.min(Math.max(p - lo, 0), hi - lo) / (hi - lo)); }, 0);
  let lo = Math.min(...segs.map(s => Math.min(s[0], s[1]))), hi = Math.max(...segs.map(s => Math.max(s[0], s[1])));
  for (let k = 0; k < 200; k++) { const mid = (lo + hi) / 2; if (below(mid) < total / 2) lo = mid; else hi = mid; }
  const p = (lo + hi) / 2;
  let Z = 0;
  for (const [a, b, w] of segs) {
    const l0 = Math.min(a, b), h0 = Math.max(a, b);
    if (h0 - l0 < 1e-15) { Z += w * Math.abs(a - p); continue; }
    Z += w * (p <= l0 ? (l0 + h0) / 2 - p : p >= h0 ? p - (l0 + h0) / 2 : ((p - l0) ** 2 + (h0 - p) ** 2) / (2 * (h0 - l0)));
  }
  return Z;
}

/** Closed outline (counter-clockwise) of the formed strip. */
export function cfOutline(typ, g) {
  const nd = nodes(pieces(typ, g), 1), h = g.t / 2, n = nd.length;
  const off = sg => nd.map((p, i) => {
    if (i === 0 || i === n - 1) { const u = i === 0 ? unit(nd[0], nd[1]) : unit(nd[n - 2], nd[n - 1]); return [p[0] - sg * h * u[1], p[1] + sg * h * u[0]]; }
    const u1 = unit(nd[i - 1], p), u2 = unit(p, nd[i + 1]), n1 = [-u1[1], u1[0]], n2 = [-u2[1], u2[0]];
    let bx = n1[0] + n2[0], by = n1[1] + n2[1];
    const bn = Math.hypot(bx, by); bx /= bn; by /= bn;
    const c = bx * n1[0] + by * n1[1];
    return [p[0] + sg * h * bx / c, p[1] + sg * h * by / c];
  });
  const poly = off(1).concat(off(-1).reverse());
  let a = 0;
  for (let i = 0; i < poly.length; i++) { const p = poly[i], q = poly[(i + 1) % poly.length]; a += p[0] * q[1] - q[0] * p[1]; }
  return a < 0 ? poly.reverse() : poly;
}

/** Linear-method properties (placement coordinates). */
export function cfProps(typ, g) {
  const t = g.t, pcs = pieces(typ, g), m = moments(pcs, t);
  const A = t * m.L, xc = m.Sx / m.L, yc = m.Sy / m.L;
  const I33 = m.Ixx - A * yc * yc, I22 = m.Iyy - A * xc * xc;
  let I23 = m.Ixy - A * xc * yc;
  const nd = nodes(pcs, 40);
  let [xs, ys, Cw] = sectorial(nd, t, xc, yc, I33, I22, I23);
  if (typ === "C_lipped") { ys = 0; I23 = 0; } else if (typ === "hat") { xs = 0; I23 = 0; } else { xs = 0; ys = 0; }
  const ol = cfOutline(typ, g), xsO = ol.map(p => p[0]), ysO = ol.map(p => p[1]);
  const xmin = Math.min(...xsO), xmax = Math.max(...xsO), ymin = Math.min(...ysO), ymax = Math.max(...ysO);
  const c2 = Math.max(ymax - yc, yc - ymin), c3 = Math.max(xmax - xc, xc - xmin);
  return { A, I33, I22, I23, J: m.L * t ** 3 / 3, Cw, S33: I33 / c2, S22: I22 / c3, Z33: plastic(nd, t, 1), Z22: plastic(nd, t, 0),
    x0: xs, y0: ys, cx: xc, cy: yc, b: xmax - xmin, h: ymax - ymin };
}

export function cfShearAreas(typ, g) {
  return typ === "hat" ? [2 * g.d * g.t, 5 / 6 * (g.b + 2 * g.bf) * g.t] : [g.d * g.t, 5 / 3 * g.b * g.t];
}

/** secpreview drawing ({poly, shape, label, dims, sc}) of a CF section dict. */
export function cfPreview(typ, d) {
  const g = { ...d };
  for (const k of CF_REQ[typ]) if (!(g[k] > 0)) return null;
  if (cfCheck(typ, g)) return null;
  const poly = cfOutline(typ, g), p = cfProps(typ, g);
  const xs = poly.map(q => q[0]), ys = poly.map(q => q[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const label = { C_lipped: "Cold-Formed Lipped C", Z_lipped: "Cold-Formed Lipped Z", hat: "Cold-Formed Hat" }[typ];
  const dims = [{ dir: "v", a: y0, b: y1, at: x0, side: -1, sym: "d", v: g.d }, { dir: "h", a: x0, b: x1, at: y1, side: 1, sym: typ === "Z_lipped" ? "2b−t" : typ === "hat" ? "b+2bf" : "b", v: x1 - x0 }];
  if (typ !== "hat") dims.push({ dir: "v", a: y1 - g.dl, b: y1, at: x1, side: 1, sym: "dl", v: g.dl });
  return { shape: typ, label, poly, dims, sc: [p.cx + p.x0, p.cy + p.y0] };
}

export default { CF_TYPES, CF_SS_TYPES, cfIs, cfCheck, cfProps, cfOutline, cfShearAreas, cfPreview };
