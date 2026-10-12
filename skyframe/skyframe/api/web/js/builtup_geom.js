/* SkyFrame — built-up section geometry + exact composite properties
   (CONTRACT "Built-up sections"). A 1:1 port of skyframe/core/builtup.py so
   the editor's live panel and ?mock=1 give the SAME numbers as the backend
   (FEM torsion excepted: that one is backend-only).

   Pure module: no DOM, no imports. All values SI (m, m², m⁴).
   Coordinates: x = width (local 3 direction), y = depth (local 2, up);
   I33 = ∫y² dA, I22 = ∫x² dA about the centroid. */

export const BU_SHAPE_TYPES = ["I", "I_mono", "channel", "angle", "double_angle", "tee",
  "rect_tube", "round_tube", "rect_solid", "round_solid", "plate"];
export const BU_REQ = {
  I: ["d", "bf", "tf", "tw"],
  I_mono: ["d", "bf_top", "tf_top", "bf_bot", "tf_bot", "tw"],
  channel: ["d", "bf", "tf", "tw"],
  angle: ["d", "b", "t"],
  double_angle: ["d", "b", "t", "s"],
  tee: ["d", "bf", "tf", "tw"],
  rect_tube: ["d", "b", "t"],
  round_tube: ["D", "t"],
  rect_solid: ["d", "b"],
  round_solid: ["D"],
  plate: ["b", "t"],
};
export const BU_TYPE_LABEL = {
  I: "I / W", I_mono: "I (unequal flanges)", channel: "Channel", angle: "Angle",
  double_angle: "Double angle", tee: "Tee", rect_tube: "Rect. tube", round_tube: "Round tube",
  rect_solid: "Rect. solid", round_solid: "Round solid", plate: "Plate",
};
export const BU_DIM_LABEL = {
  d: "Depth d", bf: "Flange width bf", tf: "Flange thk tf", tw: "Web thk tw", b: "Width / leg b",
  t: "Thickness t", s: "Gap s", D: "Diameter D", bf_top: "Top flange width", tf_top: "Top flange thk",
  bf_bot: "Bottom flange width", tf_bot: "Bottom flange thk",
};
/* default parametric dims (m) for a newly added shape — example values only */
export const BU_DEFAULT_DIMS = {
  I: { d: 0.3, bf: 0.15, tf: 0.012, tw: 0.008 },
  I_mono: { d: 0.5, bf_top: 0.2, tf_top: 0.015, bf_bot: 0.3, tf_bot: 0.02, tw: 0.01 },
  channel: { d: 0.25, bf: 0.08, tf: 0.012, tw: 0.008 },
  angle: { d: 0.1, b: 0.1, t: 0.01 },
  double_angle: { d: 0.1, b: 0.075, t: 0.008, s: 0.01, orientation: "LLBB" },
  tee: { d: 0.15, bf: 0.15, tf: 0.012, tw: 0.008 },
  rect_tube: { d: 0.2, b: 0.15, t: 0.008 },
  round_tube: { D: 0.168, t: 0.008 },
  rect_solid: { d: 0.1, b: 0.05 },
  round_solid: { D: 0.05 },
  plate: { b: 0.2, t: 0.012 },
};
const ALIAS = { bft: "bf_top", tft: "tf_top", bfb: "bf_bot", tfb: "tf_bot", OD: "D", h: "d" };
const TAB_KEYS = ["A", "I33", "I22", "J", "I_major", "I_minor"];
const CIRCLE_N = 360;
const IN = 0.0254;
const fin = v => typeof v === "number" && isFinite(v);

export function buNormDims(raw) {
  const out = {};
  for (const [k0, v] of Object.entries(raw || {})) {
    const k = ALIAS[k0] || k0;
    if (typeof v === "string") out[k] = v;
    else if (fin(v)) out[k] = +v;
  }
  return out;
}

/** Snapshot type / dims / tabulated props of a database record into a db
    component (same rule as builtup.resolve_components). */
export function buSnapshotRecord(c, rec) {
  c.type = String(rec.type);
  c.dims = buNormDims(rec.dims);
  const props = rec.props || {}, computed = new Set(rec.computed || []);
  const tab = {};
  for (const k of TAB_KEYS) if (fin(props[k]) && !computed.has(k)) tab[k] = +props[k];
  if (Object.keys(tab).length) c.tab = tab; else delete c.tab;
  return c;
}

/* ------------------------------------------------ validation */
export function buCheckDims(tag, typ, dims) {
  if (!BU_REQ[typ]) return `${tag}: unknown shape type ${typ}`;
  for (const k of BU_REQ[typ]) {
    const v = dims[k];
    if (!fin(v)) return `${tag}: ${BU_DIM_LABEL[k] || k} is required`;
    if (v < 0 || (v === 0 && k !== "s")) return `${tag}: ${BU_DIM_LABEL[k] || k} must be > 0`;
  }
  const g = k => dims[k];
  if (["I", "tee", "channel"].includes(typ)) {
    const nf = typ === "tee" ? 1 : 2;
    if (nf * g("tf") >= g("d")) return `${tag}: flange thickness exceeds the depth`;
    if (g("tw") >= g("bf")) return `${tag}: web thickness exceeds the flange width`;
  } else if (typ === "I_mono") {
    if (g("tf_top") + g("tf_bot") >= g("d")) return `${tag}: flange thicknesses exceed the depth`;
    if (g("tw") >= Math.min(g("bf_top"), g("bf_bot"))) return `${tag}: web thickness exceeds a flange width`;
  } else if (typ === "angle" || typ === "double_angle") {
    if (g("t") >= Math.min(g("d"), g("b"))) return `${tag}: leg thickness exceeds a leg length`;
    if (typ === "double_angle" && !["LLBB", "SLBB"].includes(dims.orientation || "LLBB")) return `${tag}: orientation must be LLBB or SLBB`;
  } else if (typ === "rect_tube") {
    if (2 * g("t") >= Math.min(g("d"), g("b"))) return `${tag}: wall thickness too large for the tube`;
  } else if (typ === "round_tube") {
    if (2 * g("t") >= g("D")) return `${tag}: wall thickness too large for the tube`;
  }
  return "";
}

/** "" when valid, else the first problem (mirrors validate_builtup_section). */
export function buValidate(sec, materials) {
  const tag0 = `Built-up section ${sec.name || ""}`;
  if (!sec.name) return "built-up section: name is required";
  if (!materials[sec.material]) return `${tag0}: unknown material ${sec.material}`;
  if (!(+materials[sec.material].E > 0)) return `${tag0}: material ${sec.material} needs E > 0`;
  if (!["geometric", "principal"].includes(sec.axes || "geometric")) return `${tag0}: axes must be geometric or principal`;
  if (!["auto", "open", "fem", "exact"].includes(sec.torsion || "auto")) return `${tag0}: torsion must be auto, open, exact or fem`;
  if (!(sec.components || []).length) return `${tag0}: needs at least one component`;
  for (const [i, c] of sec.components.entries()) {
    const tag = `${tag0}, component ${i + 1}`;
    if (c.material && !materials[c.material]) return `${tag}: unknown material ${c.material}`;
    if (c.kind === "plate") {
      for (const k of ["b", "t", "x", "y", "angle"]) if (!fin(c[k])) return `${tag}: ${k} must be a finite number`;
      if (c.b <= 0 || c.t <= 0) return `${tag}: plate b and t must be > 0`;
      continue;
    }
    if (c.kind !== "db" && c.kind !== "param") return `${tag}: unknown kind ${c.kind}`;
    for (const k of ["x", "y", "rotation"]) if (!fin(c[k])) return `${tag}: ${k} must be a finite number`;
    if (!c.type || !c.dims) return `${tag}: shape type and dims are required`;
    const e = buCheckDims(tag, c.type, c.dims);
    if (e) return e;
  }
  return "";
}

/* ------------------------------------------------ local pieces */
const rect = (x0, x1, y0, y1, sign = 1) => ({ t: "poly", pts: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], sign, wall: true });
const circle = (cx, cy, r, sign = 1) => ({ t: "circle", c: [cx, cy], r, sign });
const srt = (a, b) => a <= b ? [a, b] : [b, a];

export function buLocalPieces(typ, d) {
  const g = k => d[k];
  if (typ === "plate") { const b = g("b"), t = g("t"); return [[rect(-b / 2, b / 2, -t / 2, t / 2)], "open"]; }
  if (typ === "rect_solid") { const b = g("b"), h = g("d"); const p = rect(-b / 2, b / 2, -h / 2, h / 2); p.wall = false; return [[p], "solid"]; }
  if (typ === "round_solid") return [[circle(0, 0, g("D") / 2)], "solid"];
  if (typ === "round_tube") { const R = g("D") / 2; return [[circle(0, 0, R), circle(0, 0, R - g("t"), -1)], "closed"]; }
  if (typ === "rect_tube") {
    const b = g("b"), h = g("d"), t = g("t");
    const o = rect(-b / 2, b / 2, -h / 2, h / 2), i = rect(-b / 2 + t, b / 2 - t, -h / 2 + t, h / 2 - t, -1);
    o.wall = i.wall = false;
    return [[o, i], "closed"];
  }
  if (typ === "I") {
    const h = g("d"), bf = g("bf"), tf = g("tf"), tw = g("tw");
    return [[rect(-bf / 2, bf / 2, h / 2 - tf, h / 2), rect(-bf / 2, bf / 2, -h / 2, -h / 2 + tf),
      rect(-tw / 2, tw / 2, -h / 2 + tf, h / 2 - tf)], "open"];
  }
  if (typ === "I_mono") {
    const h = g("d"), tw = g("tw"), bt = g("bf_top"), tt = g("tf_top"), bb = g("bf_bot"), tb = g("tf_bot");
    return [[rect(-bt / 2, bt / 2, h / 2 - tt, h / 2), rect(-bb / 2, bb / 2, -h / 2, -h / 2 + tb),
      rect(-tw / 2, tw / 2, -h / 2 + tb, h / 2 - tt)], "open"];
  }
  if (typ === "tee") {
    const h = g("d"), bf = g("bf"), tf = g("tf"), tw = g("tw");
    return [[rect(-bf / 2, bf / 2, h / 2 - tf, h / 2), rect(-tw / 2, tw / 2, -h / 2, h / 2 - tf)], "open"];
  }
  if (typ === "channel") {
    const h = g("d"), bf = g("bf"), tf = g("tf"), tw = g("tw"), x0 = -bf / 2;
    return [[rect(x0, x0 + tw, -h / 2, h / 2), rect(x0 + tw, bf / 2, h / 2 - tf, h / 2),
      rect(x0 + tw, bf / 2, -h / 2, -h / 2 + tf)], "open"];
  }
  if (typ === "angle") {
    const h = g("d"), b = g("b"), t = g("t");
    return [[rect(-b / 2, -b / 2 + t, -h / 2, h / 2), rect(-b / 2 + t, b / 2, -h / 2, -h / 2 + t)], "open"];
  }
  if (typ === "double_angle") {
    const a = g("d"), bb = g("b"), t = g("t"), s = g("s"), o = d.orientation || "LLBB";
    const [vert, hor] = o === "LLBB" ? [Math.max(a, bb), Math.min(a, bb)] : [Math.min(a, bb), Math.max(a, bb)];
    const pcs = [];
    for (const sg of [1, -1]) {
      const xh = s / 2;
      pcs.push(rect(...srt(sg * xh, sg * (xh + t)), -vert / 2, vert / 2));
      pcs.push(rect(...srt(sg * (xh + t), sg * (xh + hor)), -vert / 2, -vert / 2 + t));
    }
    return [pcs, "open"];
  }
  throw new Error(`unknown shape type ${typ}`);
}

/* ------------------------------------------------ integrals */
export function buPolyIntegrals(pts) {
  let A = 0, Sx = 0, Sy = 0, Ixx = 0, Iyy = 0, Ixy = 0;
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % n];
    const cr = x1 * y2 - x2 * y1;
    A += cr; Sx += (x1 + x2) * cr; Sy += (y1 + y2) * cr;
    Ixx += (y1 * y1 + y1 * y2 + y2 * y2) * cr;
    Iyy += (x1 * x1 + x1 * x2 + x2 * x2) * cr;
    Ixy += (x1 * y2 + 2 * x1 * y1 + 2 * x2 * y2 + x2 * y1) * cr;
  }
  return [A / 2, Sx / 6, Sy / 6, Ixx / 12, Iyy / 12, Ixy / 24];
}
const polyArea = pts => buPolyIntegrals(pts)[0];
function pieceIntegrals(p) {
  if (p.t === "circle") {
    const [cx, cy] = p.c, r = p.r, A = Math.PI * r * r, I0 = Math.PI * r ** 4 / 4;
    return [A, A * cx, A * cy, I0 + A * cy * cy, I0 + A * cx * cx, A * cx * cy];
  }
  let out = buPolyIntegrals(p.pts);
  if (out[0] < 0) out = out.map(v => -v);
  return out;
}
function central(pieces) {
  let A = 0, Sx = 0, Sy = 0, Ixx = 0, Iyy = 0, Ixy = 0;
  for (const p of pieces) {
    const [a, sx, sy, ixx, iyy, ixy] = pieceIntegrals(p), s = p.sign;
    A += s * a; Sx += s * sx; Sy += s * sy; Ixx += s * ixx; Iyy += s * iyy; Ixy += s * ixy;
  }
  const cx = Sx / A, cy = Sy / A;
  return [A, cx, cy, Ixx - A * cy * cy, Iyy - A * cx * cx, Ixy - A * cx * cy];
}
function rotMoments(Ixx, Iyy, Ixy, th) {
  const c = Math.cos(th), s = Math.sin(th);
  return [c * c * Ixx + s * s * Iyy + 2 * s * c * Ixy, s * s * Ixx + c * c * Iyy - 2 * s * c * Ixy,
    s * c * (Iyy - Ixx) + (c * c - s * s) * Ixy];
}
function wallDims(p) {
  if (p.t !== "poly" || p.pts.length !== 4) return null;
  const [[x0, y0], [x1, y1], [x2, y2]] = p.pts;
  const e1 = Math.hypot(x1 - x0, y1 - y0), e2 = Math.hypot(x2 - x1, y2 - y1);
  return e1 >= e2 ? [e1, e2, ((y1 - y0) / e1) ** 2] : [e2, e1, ((y2 - y1) / e2) ** 2];
}
const roarkRectJ = (b, h) => { const a = Math.max(b, h) / 2, c = Math.min(b, h) / 2; return a * c ** 3 * (16 / 3 - 3.36 * (c / a) * (1 - c ** 4 / (12 * a ** 4))); };
function axisAligned(p) {
  if (p.t !== "poly" || p.pts.length !== 4) return false;
  for (let i = 0; i < 4; i++) {
    const [x1, y1] = p.pts[i], [x2, y2] = p.pts[(i + 1) % 4];
    if (Math.abs(x1 - x2) > 1e-12 && Math.abs(y1 - y2) > 1e-12) return false;
  }
  return true;
}
function bounds(p) {
  const xs = p.pts.map(v => v[0]), ys = p.pts.map(v => v[1]);
  return [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
}

/** One component → section-frame pieces + own (raw) properties. */
export function buComponentGeometry(c) {
  let typ, dims, rotDeg, mirror;
  if (c.kind === "plate") { typ = "plate"; dims = { b: c.b, t: c.t }; rotDeg = c.angle; mirror = false; }
  else { typ = c.type; dims = c.dims; rotDeg = c.rotation; mirror = !!c.mirror; }
  const th = rotDeg * Math.PI / 180;
  let cs = Math.cos(th), sn = Math.sin(th);
  if (Math.abs(cs) < 1e-15) cs = 0;
  if (Math.abs(sn) < 1e-15) sn = 0;
  const X = +c.x, Y = +c.y;
  const [loc, tclass] = buLocalPieces(typ, dims);
  let [A, lcx, lcy, Ixx, Iyy, Ixy] = central(loc);
  const tab = c.tab || {};
  const useTab = c.kind === "db" && c.props_from !== "geometry" && ["A", "I33", "I22"].every(k => k in tab);
  if (useTab) {
    A = tab.A; Ixx = tab.I33; Iyy = tab.I22;
    if ("I_major" in tab && "I_minor" in tab) {
      const q = ((tab.I_major - tab.I_minor) / 2) ** 2 - ((Ixx - Iyy) / 2) ** 2;
      Ixy = Math.abs(Ixy) > 0 ? Math.sign(Ixy) * Math.sqrt(Math.max(q, 0)) : 0;
    } else Ixy = typ !== "angle" ? 0 : Ixy;
  }
  const walls = loc.filter(p => p.t === "poly" && p.wall).map(wallDims).filter(Boolean);
  let J;
  if (tclass === "open") J = walls.reduce((s, [L, t]) => s + L * t ** 3 / 3, 0);
  else if (typ === "rect_tube") { const t = dims.t, am = (dims.b - t) * (dims.d - t); J = 4 * am * am * t / (2 * ((dims.b - t) + (dims.d - t))); }
  else if (typ === "round_tube") J = Math.PI * (dims.D ** 4 - (dims.D - 2 * dims.t) ** 4) / 32;
  else if (typ === "rect_solid") J = roarkRectJ(dims.b, dims.d);
  else J = Math.PI * dims.D ** 4 / 32;
  if (useTab && "J" in tab) J = tab.J;

  if (mirror) { lcx = -lcx; Ixy = -Ixy; }
  [Ixx, Iyy, Ixy] = rotMoments(Ixx, Iyy, Ixy, th);
  const gcx = lcx * cs - lcy * sn + X, gcy = lcx * sn + lcy * cs + Y;
  const xf = ([x, y]) => { if (mirror) x = -x; return [x * cs - y * sn + X, x * sn + y * cs + Y]; };
  const pieces = loc.map(p => {
    if (p.t === "circle") return { ...p, c: xf(p.c) };
    let pts = p.pts.map(xf);
    if (mirror) pts = pts.reverse();
    return { ...p, pts };
  });
  let Asv, Ash;
  if (typ === "rect_solid") Asv = Ash = 5 / 6 * A;
  else if (typ === "round_solid") Asv = Ash = 0.9 * A;
  else if (typ === "round_tube") Asv = Ash = 0.5 * A;
  else if (typ === "rect_tube") {
    const t = dims.t, vl = 2 * dims.d * t, hl = 2 * dims.b * t;
    Asv = vl * cs * cs + hl * sn * sn; Ash = vl * sn * sn + hl * cs * cs;
  } else {
    Asv = Ash = 0;
    const geomA = walls.reduce((s, [L, t]) => s + L * t, 0) || 1, scale = A / geomA;
    for (const p of pieces) {
      const w = p.wall ? wallDims(p) : null;
      if (!w) continue;
      const [L, t, s2] = w, a = L * t * scale;
      if (L >= 2 * t) { Asv += a * s2; Ash += a * (1 - s2); }
      else { Asv += 5 / 6 * a; Ash += 5 / 6 * a; }
    }
  }
  const xs = [], ys = [];
  for (const p of pieces) {
    if (p.sign < 0) continue;
    if (p.t === "circle") { xs.push(p.c[0] - p.r, p.c[0] + p.r); ys.push(p.c[1] - p.r, p.c[1] + p.r); }
    else for (const v of p.pts) { xs.push(v[0]); ys.push(v[1]); }
  }
  const closedRect = typ === "rect_tube" && axisAligned(pieces[0]) ? bounds(pieces[0]) : null;
  return { pieces, A, cx: gcx, cy: gcy, Ixx, Iyy, Ixy, J, tclass, type: typ, As_v: Asv, As_h: Ash,
    bbox: [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)], closed_rect: closedRect };
}

/* ------------------------------------------------ closed cells */
function cellsOf(geoms) {
  const rects = [];
  geoms.forEach((g, ci) => {
    if (g.closed_rect) { rects.push([...g.closed_rect, ci]); return; }
    for (const p of g.pieces) if (p.sign > 0 && p.t === "poly" && axisAligned(p)) rects.push([...bounds(p), ci]);
  });
  if (rects.length < 2) return [[], rects];
  const uniq = vals => { const out = []; for (const v of [...vals].sort((a, b) => a - b)) if (!out.length || v - out[out.length - 1] > 1e-9) out.push(v); return out; };
  const xs = uniq(rects.flatMap(r => [r[0], r[1]])), ys = uniq(rects.flatMap(r => [r[2], r[3]]));
  const nx = xs.length - 1, ny = ys.length - 1;
  const fill = [], seen = [];
  for (let i = 0; i < nx; i++) {
    fill.push([]); seen.push([]);
    const xm = 0.5 * (xs[i] + xs[i + 1]);
    for (let j = 0; j < ny; j++) {
      const ym = 0.5 * (ys[j] + ys[j + 1]);
      fill[i].push(rects.some(r => r[0] < xm && xm < r[1] && r[2] < ym && ym < r[3]));
      seen[i].push(false);
    }
  }
  const stack = [];
  for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++)
    if ((i === 0 || i === nx - 1 || j === 0 || j === ny - 1) && !fill[i][j]) { stack.push([i, j]); seen[i][j] = true; }
  const nb = (i, j) => [[i + 1, j], [i - 1, j], [i, j + 1], [i, j - 1]];
  while (stack.length) {
    const [i, j] = stack.pop();
    for (const [a, b] of nb(i, j)) if (a >= 0 && a < nx && b >= 0 && b < ny && !fill[a][b] && !seen[a][b]) { seen[a][b] = true; stack.push([a, b]); }
  }
  const cells = [];
  for (let i0 = 0; i0 < nx; i0++) for (let j0 = 0; j0 < ny; j0++) {
    if (fill[i0][j0] || seen[i0][j0]) continue;
    const reg = [[i0, j0]]; seen[i0][j0] = true;
    for (let k = 0; k < reg.length; k++) {
      const [i, j] = reg[k];
      for (const [a, b] of nb(i, j)) if (a >= 0 && a < nx && b >= 0 && b < ny && !fill[a][b] && !seen[a][b]) { seen[a][b] = true; reg.push([a, b]); }
    }
    const ia = Math.min(...reg.map(r => r[0])), ib = Math.max(...reg.map(r => r[0]));
    const ja = Math.min(...reg.map(r => r[1])), jb = Math.max(...reg.map(r => r[1]));
    const cell = { x0: xs[ia], x1: xs[ib + 1], y0: ys[ja], y1: ys[jb + 1], rect: reg.length === (ib - ia + 1) * (jb - ja + 1) };
    if (cell.rect) {
      const im = Math.floor((ia + ib) / 2), jm = Math.floor((ja + jb) / 2);
      const run = (i, j, di, dj) => { let tot = 0; while (i >= 0 && i < nx && j >= 0 && j < ny && fill[i][j]) { tot += di ? xs[i + 1] - xs[i] : ys[j + 1] - ys[j]; i += di; j += dj; } return tot; };
      cell.t_top = run(im, jb + 1, 0, 1); cell.t_bot = run(im, ja - 1, 0, -1);
      cell.t_right = run(ib + 1, jm, 1, 0); cell.t_left = run(ia - 1, jm, -1, 0);
      const walls = new Set(), e = 1e-9;
      for (const [x0, x1, y0, y1, ci] of rects) {
        const th = x0 < cell.x1 - e && x1 > cell.x0 + e && (Math.abs(y0 - cell.y1) < e || Math.abs(y1 - cell.y0) < e);
        const tv = y0 < cell.y1 - e && y1 > cell.y0 + e && (Math.abs(x0 - cell.x1) < e || Math.abs(x1 - cell.x0) < e);
        if (th || tv) walls.add(ci);
      }
      cell.walls = walls;
    }
    cells.push(cell);
  }
  return [cells, rects];
}
function overlapArea(rects) {
  let tot = 0;
  for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
    const a = rects[i], b = rects[j];
    const w = Math.min(a[1], b[1]) - Math.max(a[0], b[0]), h = Math.min(a[3], b[3]) - Math.max(a[2], b[2]);
    if (w > 1e-9 && h > 1e-9) tot += w * h;
  }
  return tot;
}

/* ------------------------------------------------ plastic modulus */
function clipAbove(pts, v0) {
  const out = [], n = pts.length;
  for (let i = 0; i < n; i++) {
    const [y1, z1] = pts[i], [y2, z2] = pts[(i + 1) % n];
    const in1 = z1 - v0 >= 0, in2 = z2 - v0 >= 0;
    if (in1) out.push([y1, z1]);
    if (in1 !== in2) { const t = (v0 - z1) / (z2 - z1); out.push([y1 + t * (y2 - y1), v0]); }
  }
  return out.length >= 3 ? out : [];
}
function polysForPlastic(geoms, ns, cx, cy, th) {
  const c = Math.cos(th), s = Math.sin(th), out = [];
  geoms.forEach((g, gi) => {
    for (const p of g.pieces) {
      let pts;
      if (p.t === "circle") {
        const [px, py] = p.c, r = p.r;
        pts = [];
        for (let k = 0; k < CIRCLE_N; k++) pts.push([px + r * Math.cos(2 * Math.PI * k / CIRCLE_N), py + r * Math.sin(2 * Math.PI * k / CIRCLE_N)]);
      } else pts = p.pts;
      let uv = pts.map(([x, y]) => [(x - cx) * c + (y - cy) * s, -(x - cx) * s + (y - cy) * c]);
      if (polyArea(uv) < 0) uv = uv.reverse();
      out.push([p.sign * ns[gi], uv]);
    }
  });
  return out;
}
function plasticZ(polys) {
  const above = v0 => {
    let A = 0, S = 0;
    for (const [w, pts] of polys) {
      const part = clipAbove(pts, v0);
      if (part.length) { const r = buPolyIntegrals(part); A += w * r[0]; S += w * r[2]; }
    }
    return [A, S];
  };
  let totA = 0, totS = 0;
  for (const [w, pts] of polys) { const r = buPolyIntegrals(pts); totA += w * r[0]; totS += w * r[2]; }
  const vs = polys.flatMap(([, pts]) => pts.map(q => q[1]));
  let lo = Math.min(...vs), hi = Math.max(...vs);
  for (let k = 0; k < 200; k++) { const mid = 0.5 * (lo + hi); if (above(mid)[0] > 0.5 * totA) lo = mid; else hi = mid; }
  const v0 = 0.5 * (lo + hi), [Aa, Sa] = above(v0), Ab = totA - Aa, Sb = totS - Sa;
  return (Sa - v0 * Aa) - (Sb - v0 * Ab);
}

/* ------------------------------------------------ symmetry / shear centre */
const rnd = v => Math.round(v * 1e7) / 1e7 + 0;
function signature(geoms, ns, fx) {
  const sig = [];
  geoms.forEach((g, gi) => {
    const n = Math.round(ns[gi] * 1e9) / 1e9;
    for (const p of g.pieces) {
      if (p.t === "circle") { const [x, y] = fx(...p.c); sig.push(`c|${rnd(x)}|${rnd(y)}|${rnd(p.r)}|${p.sign}|${n}`); }
      else {
        const pts = p.pts.map(v => fx(...v)).map(([a, b]) => [rnd(a), rnd(b)]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
        sig.push(`p|${pts.map(q => q.join(",")).join(";")}|${p.sign}|${n}`);
      }
    }
  });
  return sig.sort().join("#");
}
function shearCentre(geoms, ns, cx, cy) {
  const id = signature(geoms, ns, (x, y) => [x, y]);
  const sv = signature(geoms, ns, (x, y) => [2 * cx - x, y]) === id;
  const sh = signature(geoms, ns, (x, y) => [x, 2 * cy - y]) === id;
  const sp = signature(geoms, ns, (x, y) => [2 * cx - x, 2 * cy - y]) === id;
  if ((sv && sh) || sp) return { x: cx, y: cy, method: "symmetry" };
  if (sv) {
    let num = 0, den = 0;
    for (const [gi, g] of geoms.entries()) for (const p of g.pieces) {
      if (p.t !== "poly" || !axisAligned(p) || p.sign < 0) return null;
      const [x0, x1, y0, y1] = bounds(p), w = x1 - x0, h = y1 - y0, xm = 0.5 * (x0 + x1);
      const If = ns[gi] * (h * w ** 3 / 12 + w * h * (xm - cx) ** 2);
      num += If * 0.5 * (y0 + y1); den += If;
    }
    if (den > 0) return { x: cx, y: num / den, method: "monosymmetric" };
  }
  return null;
}

/** Exact composite properties — same keys/values as builtup.section_properties
    (torsion "exact" / "fem" is backend-only — skyframe.core.section_fem: here
    it reports the closed-form "auto" value, J_method "approx", with a note). */
export function buSectionProperties(sec, materials) {
  const err = buValidate(sec, materials);
  if (err) throw new Error(err);
  const Eref = +materials[sec.material].E;
  const ns = sec.components.map(c => +materials[c.material || sec.material].E / Eref);
  const geoms = sec.components.map(buComponentGeometry);
  const warnings = [];
  const sum = f => geoms.reduce((s, g, i) => s + f(g, ns[i]), 0);
  const A = sum((g, n) => n * g.A);
  if (!(A > 1e-15)) throw new Error(`Built-up section ${sec.name}: area must be > 0`);
  const cx = sum((g, n) => n * g.A * g.cx) / A, cy = sum((g, n) => n * g.A * g.cy) / A;
  const Ixx = sum((g, n) => n * (g.Ixx + g.A * (g.cy - cy) ** 2));
  const Iyy = sum((g, n) => n * (g.Iyy + g.A * (g.cx - cx) ** 2));
  let Ixy = sum((g, n) => n * (g.Ixy + g.A * (g.cx - cx) * (g.cy - cy)));
  if (Math.abs(Ixy) < 1e-12 * Math.max(Ixx, Iyy)) Ixy = 0;
  const avg = 0.5 * (Ixx + Iyy), R = Math.hypot(0.5 * (Ixx - Iyy), Ixy);
  let theta = (Ixy || Ixx < Iyy) ? 0.5 * Math.atan2(-2 * Ixy, Ixx - Iyy) : 0;
  if (theta <= -0.5 * Math.PI + 1e-12) theta += Math.PI;
  const axes = sec.axes || "geometric", torsion = sec.torsion || "auto";
  const Imaj = avg + R, Imin = avg - R, thAx = axes === "principal" ? theta : 0;
  const [I33, I22] = axes === "principal" ? [Imaj, Imin] : [Ixx, Iyy];

  const [cells, rects] = cellsOf(geoms);
  const own = geoms.map((g, i) => ns[i] * g.J);
  let Jmethod = geoms.every(g => g.tclass === "open") ? "open" : "closed";
  let J = own.reduce((a, b) => a + b, 0);
  if (torsion !== "open" && cells.length) {
    if (cells.length === 1 && cells[0].rect) {
      const c = cells[0];
      const Wm = (c.x1 - c.x0) + 0.5 * (c.t_left + c.t_right), Hm = (c.y1 - c.y0) + 0.5 * (c.t_top + c.t_bot);
      const dsT = Wm / c.t_top + Wm / c.t_bot + Hm / c.t_left + Hm / c.t_right;
      const wi = [...c.walls].sort((a, b) => a - b);
      const wA = wi.length ? wi.map(i => geoms[i].A) : [1], wn = wi.length ? wi.map(i => ns[i]) : [1];
      const nCell = wA.reduce((s, a, k) => s + a * wn[k], 0) / wA.reduce((s, a) => s + a, 0);
      const Jc = 4 * (Wm * Hm) ** 2 / dsT;
      J = nCell * Jc + own.reduce((s, v, i) => s + (c.walls.has(i) ? 0 : v), 0);
      Jmethod = "closed_cell"; c.Am = Wm * Hm; c.J = Jc;
    } else {
      Jmethod = "approx";
      warnings.push("Several or non-rectangular closed cells: J is the conservative sum of the component J values (use torsion 'exact' for the exact value).");
    }
  }
  if (overlapArea(rects.map(r => r.slice(0, 4))) > 1e-9 * A) warnings.push("Some components overlap: overlapping area is counted twice.");
  const Asv = sum((g, n) => n * g.As_v), Ash = sum((g, n) => n * g.As_h);
  const sc = shearCentre(geoms, ns, cx, cy);
  if (torsion === "fem" || torsion === "exact") {   // v1.22: exact = backend finite elements (skyframe.core.section_fem)
    Jmethod = "approx";
    warnings.push("Exact torsion is computed by the backend (finite elements); showing the closed-form value until it arrives.");
  }
  let As2 = Asv, As3 = Ash;
  if (axes === "principal") { const c2 = Math.cos(thAx) ** 2, s2 = Math.sin(thAx) ** 2; As2 = Asv * c2 + Ash * s2; As3 = Asv * s2 + Ash * c2; }

  const polys = polysForPlastic(geoms, ns, cx, cy, thAx);
  const us = [], vs = [];
  for (const [w, pts] of polys) if (w > 0) for (const q of pts) { us.push(q[0]); vs.push(q[1]); }
  const ct = Math.cos(thAx), st = Math.sin(thAx);
  for (const g of geoms) for (const p of g.pieces) if (p.t === "circle" && p.sign > 0) {
    const x = p.c[0] - cx, y = p.c[1] - cy, u = x * ct + y * st, v = -x * st + y * ct;
    us.push(u - p.r, u + p.r); vs.push(v - p.r, v + p.r);
  }
  const vTop = Math.max(...vs), vBot = Math.min(...vs), uR = Math.max(...us), uL = Math.min(...us);
  const S33t = I33 / vTop, S33b = I33 / -vBot, S22r = I22 / uR, S22l = I22 / -uL;
  const Z33 = plasticZ(polys);
  const Z22 = plasticZ(polys.map(([w, pts]) => { let q = pts.map(p => [p[1], p[0]]); if (polyArea(q) < 0) q = q.reverse(); return [w, q]; }));
  const bbox = [Math.min(...geoms.map(g => g.bbox[0])), Math.max(...geoms.map(g => g.bbox[1])),
    Math.min(...geoms.map(g => g.bbox[2])), Math.max(...geoms.map(g => g.bbox[3]))];
  return {
    A, cx, cy, Ixx, Iyy, Ixy, I_major: Imaj, I_minor: Imin, theta, theta_deg: theta * 180 / Math.PI, axes,
    I33, I22, J, J_method: Jmethod, S33: Math.min(S33t, S33b), S33_top: S33t, S33_bot: S33b,
    S22: Math.min(S22l, S22r), S22_left: S22l, S22_right: S22r, Z33, Z22,
    r33: Math.sqrt(I33 / A), r22: Math.sqrt(Math.max(I22, 0) / A), As2, As3,
    shear_centre: sc, x0: sc ? sc.x - cx : null, y0: sc ? sc.y - cy : null,
    b: bbox[1] - bbox[0], h: bbox[3] - bbox[2], bbox, E_ref: Eref, n: ns,
    cells: cells.map(c => ({ ...c, ...(c.walls ? { walls: [...c.walls].sort((a, b) => a - b) } : {}) })), warnings,
  };
}

/** Mirrored FrameSection dict (builtup.make_frame_section; keeps modifiers). */
export function buFrameSection(sec, props, prev) {
  const p = prev || {};
  // key order = FrameSection.to_dict (backend echo compares equal)
  const out = { name: sec.name, material: sec.material, A: props.A, I33: props.I33, I22: props.I22, J: props.J,
    b: props.b, h: props.h };
  for (const [k, d] of [["mod_A", 1], ["mod_I33", 1], ["mod_I22", 1], ["mod_J", 1], ["As2", null], ["As3", null],
    ["shear_deformation", false], ["mod_As2", 1], ["mod_As3", 1], ["mod_mass", 1], ["mod_weight", 1]])
    out[k] = k in p ? p[k] : d;
  out.shape = "builtup";
  return out;
}

/** Normalized component (field order + defaults of BuiltupSection.to_dict). */
export function buNormComponent(c) {
  let d;
  if (c.kind === "plate") d = { kind: "plate", b: +c.b, t: +c.t, x: +(c.x || 0), y: +(c.y || 0), angle: +(c.angle || 0) };
  else {
    d = { kind: c.kind };
    if (c.kind === "db") { d.standard = String(c.standard || ""); d.name = String(c.name || ""); }
    if (c.type != null) d.type = String(c.type);
    if (c.dims != null) d.dims = buNormDims(c.dims);
    d.x = +(c.x || 0); d.y = +(c.y || 0); d.rotation = +(c.rotation || 0); d.mirror = !!c.mirror;
    if (c.kind === "db" && c.tab) d.tab = { ...c.tab };
    if (c.props_from === "geometry") d.props_from = "geometry";
  }
  if (c.material) d.material = String(c.material);
  if (c.label) d.label = String(c.label);
  return d;
}
export function buNormSection(s) {
  const d = { name: String(s.name || ""), material: String(s.material || ""),
    components: (s.components || []).map(buNormComponent), axes: s.axes || "geometric", torsion: s.torsion || "auto" };
  if (s.template) d.template = JSON.parse(JSON.stringify(s.template));
  if (s.notes) d.notes = String(s.notes);
  return d;
}

/* ------------------------------------------------ templates (builtup.TEMPLATES) */
export const BU_TEMPLATES = {
  plate_girder: { label: "Plate girder (I from plates)", params: { d: 1.2, tw: 0.012, bf_top: 0.40, tf_top: 0.025, bf_bot: 0.50, tf_bot: 0.030 } },
  box_girder: { label: "Box girder from plates", params: { b: 0.60, d: 1.0, tf: 0.025, tw: 0.016, s: 0.50 } },
  w_cover: { label: "W with cover plate(s)", params: { standard: "aisc", name: "W14X90", plate_b: 0.40, plate_t: 0.020, where: "both" } },
  double_angle: { label: "Double angle (2L back-to-back)", params: { d: 6 * IN, b: 4 * IN, t: 0.5 * IN, s: 0.375 * IN, orientation: "LLBB" } },
  double_channel: { label: "Double channel", params: { d: 0.30, bf: 0.09, tf: 0.0135, tw: 0.008, s: 0.010, arrangement: "back_to_back" } },
  starred_angles: { label: "Starred angles", params: { b: 0.10, t: 0.010, s: 0.010 } },
  w_channel_cap: { label: "W + channel cap (crane beam)", params: { standard: "aisc", name: "W24X76", d: 0.38, bf: 0.10, tf: 0.016, tw: 0.0105 } },
  laced_column: { label: "Laced / battened column (2 channels)", params: { d: 0.30, bf: 0.09, tf: 0.0135, tw: 0.008, s: 0.20, arrangement: "toe_out", lacing: "laced" } },
  tube_stiffener: { label: "Tube with stiffener plate", params: { tube: "rect", d: 0.20, b: 0.20, t: 0.010, D: 0.2191, plate_h: 0.15, plate_t: 0.012, side: "bottom" } },
};
/* string-valued template params → their choices (UI selects) */
export const BU_TEMPLATE_CHOICES = {
  where: ["top", "bottom", "both"], orientation: ["LLBB", "SLBB"], arrangement: ["back_to_back", "toe_to_toe", "toe_out", "toe_in"],
  lacing: ["laced", "battened"], tube: ["rect", "round"], side: ["bottom", "top"],
};
const tPlate = (b, t, x, y, angle = 0, label) => ({ kind: "plate", b, t, x, y, angle, ...(label ? { label } : {}) });
const tParam = (type, dims, x = 0, y = 0, rotation = 0, mirror = false, label) => ({ kind: "param", type, dims: { ...dims }, x, y, rotation, mirror, ...(label ? { label } : {}) });
const tDb = (standard, name, label) => ({ kind: "db", standard, name, x: 0, y: 0, rotation: 0, mirror: false, ...(label ? { label } : {}) });
function channelPair(p, arr) {
  const dims = { d: p.d, bf: p.bf, tf: p.tf, tw: p.tw }, xr = p.s / 2 + p.bf / 2;
  if (arr === "back_to_back" || arr === "toe_out") return [tParam("channel", dims, xr, 0, 0, false, "Channel R"), tParam("channel", dims, -xr, 0, 0, true, "Channel L")];
  if (arr === "toe_to_toe" || arr === "toe_in") return [tParam("channel", dims, xr, 0, 0, true, "Channel R"), tParam("channel", dims, -xr, 0, 0, false, "Channel L")];
  throw new Error("arrangement must be back_to_back | toe_to_toe");
}
const cap = s => s.charAt(0).toUpperCase() + s.slice(1);

/** Build a template section. ``dbLookup(standard, name)`` → database record
    (sync) for the templates that use a database W shape. */
export function buTemplateSection(id, params, name, material, dbLookup) {
  const T = BU_TEMPLATES[id];
  if (!T) throw new Error(`unknown built-up template ${id}`);
  const p = { ...T.params };
  for (const [k, v] of Object.entries(params || {})) if (k in p) p[k] = typeof v === "string" ? v : +v;
  let comps = [], notes = "";
  if (id === "plate_girder") {
    const hw = p.d - p.tf_top - p.tf_bot;
    comps = [tPlate(p.bf_top, p.tf_top, 0, p.d / 2 - p.tf_top / 2, 0, "Top flange"), tPlate(hw, p.tw, 0, -p.d / 2 + p.tf_bot + hw / 2, 90, "Web"),
      tPlate(p.bf_bot, p.tf_bot, 0, -p.d / 2 + p.tf_bot / 2, 0, "Bottom flange")];
  } else if (id === "box_girder") {
    const hw = p.d - 2 * p.tf;
    comps = [tPlate(p.b, p.tf, 0, p.d / 2 - p.tf / 2, 0, "Top flange"), tPlate(p.b, p.tf, 0, -p.d / 2 + p.tf / 2, 0, "Bottom flange"),
      tPlate(hw, p.tw, -p.s / 2, 0, 90, "Web L"), tPlate(hw, p.tw, p.s / 2, 0, 90, "Web R")];
  } else if (id === "w_cover") {
    const rec = dbLookup(p.standard, p.name), dW = +rec.dims.d;
    if (!["top", "bottom", "both"].includes(p.where)) throw new Error("where must be top | bottom | both");
    comps = [tDb(p.standard, p.name, p.name)];
    if (p.where !== "bottom") comps.push(tPlate(p.plate_b, p.plate_t, 0, dW / 2 + p.plate_t / 2, 0, "Top cover"));
    if (p.where !== "top") comps.push(tPlate(p.plate_b, p.plate_t, 0, -dW / 2 - p.plate_t / 2, 0, "Bottom cover"));
  } else if (id === "double_angle") {
    if (!["LLBB", "SLBB"].includes(p.orientation)) throw new Error("orientation must be LLBB or SLBB");
    const [vert, hor] = p.orientation === "LLBB" ? [Math.max(p.d, p.b), Math.min(p.d, p.b)] : [Math.min(p.d, p.b), Math.max(p.d, p.b)];
    const dims = { d: vert, b: hor, t: p.t }, x = p.s / 2 + hor / 2;
    comps = [tParam("angle", dims, x, 0, 0, false, "Angle R"), tParam("angle", dims, -x, 0, 0, true, "Angle L")];
  } else if (id === "double_channel") comps = channelPair(p, p.arrangement);
  else if (id === "starred_angles") {
    const dims = { d: p.b, b: p.b, t: p.t }, xa = p.s / 2 + p.b / 2, ya = -p.t / 2 + p.b / 2;
    comps = [tParam("angle", dims, xa, ya, 0, false, "Angle A"), tParam("angle", dims, -xa, -ya, 180, false, "Angle B")];
  } else if (id === "w_channel_cap") {
    const rec = dbLookup(p.standard, p.name), dW = +rec.dims.d;
    comps = [tDb(p.standard, p.name, p.name), tParam("channel", { d: p.d, bf: p.bf, tf: p.tf, tw: p.tw }, 0, dW / 2 + p.tw - p.bf / 2, -90, false, "Cap channel")];
  } else if (id === "laced_column") {
    const arr = { toe_out: "back_to_back", toe_in: "toe_to_toe" }[p.arrangement] || p.arrangement;
    comps = channelPair(p, arr);
    notes = `${cap(p.lacing)} column: properties are those of the two chords only (lacing/battens carry shear, not bending). ` +
      `The frame element assumes the chords act compositely with NO shear flexibility of the ${p.lacing} system; for buckling use a modified slenderness (e.g. AISC 360 E6) outside this analysis.`;
  } else if (id === "tube_stiffener") {
    let tube, half;
    if (p.tube === "round") { tube = tParam("round_tube", { D: p.D, t: p.t }, 0, 0, 0, false, "Tube"); half = p.D / 2; }
    else { tube = tParam("rect_tube", { d: p.d, b: p.b, t: p.t }, 0, 0, 0, false, "Tube"); half = p.d / 2; }
    const sg = p.side === "bottom" ? -1 : 1;
    comps = [tube, tPlate(p.plate_h, p.plate_t, 0, sg * (half + p.plate_h / 2), 90, "Stiffener")];
  }
  for (const c of comps) if (c.kind === "db") buSnapshotRecord(c, dbLookup(c.standard, c.name));
  return buNormSection({ name, material, components: comps, axes: "geometric", torsion: "auto", template: { id, params: p }, notes });
}

/** SVG path data of a component's pieces (section coords → screen via T). */
export function buPiecesPath(pieces, T) {
  let d = "";
  for (const p of pieces) {
    if (p.t === "circle") {
      const [cx, cy] = T(p.c), r = Math.abs(T([p.c[0] + p.r, p.c[1]])[0] - cx);
      d += `M${cx - r},${cy}a${r},${r} 0 1,0 ${2 * r},0a${r},${r} 0 1,0 ${-2 * r},0Z`;
    } else d += "M" + p.pts.map(v => T(v).map(q => +q.toFixed(2)).join(",")).join("L") + "Z";
  }
  return d;
}

/** Outline polygons for js/secpreview.js (holes flagged; circles as 72-gons). */
export function buPreviewPolys(sec) {
  const out = [];
  for (const c of (sec && sec.components) || []) {
    let g;
    try { g = buComponentGeometry(c); } catch (e) { continue; }
    for (const p of g.pieces) {
      let pts = p.t === "circle"
        ? Array.from({ length: 72 }, (_, k) => [p.c[0] + p.r * Math.cos(2 * Math.PI * k / 72), p.c[1] + p.r * Math.sin(2 * Math.PI * k / 72)])
        : p.pts.map(v => [v[0], v[1]]);
      const ccw = polyArea(pts) > 0;
      if ((p.sign > 0) !== ccw) pts = pts.reverse();
      out.push({ pts, hole: p.sign < 0, material: c.material || sec.material });
    }
  }
  return out;
}
