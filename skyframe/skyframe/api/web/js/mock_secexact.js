/* SkyFrame — ?mock=1 stand-in for POST /api/sections/exact (v1.22,
   CONTRACT "Exact section solver"). The browser has no finite-element
   solver: the mock answers with the CLOSED-FORM values — parametric shapes
   from js/secshapes.js (thin-walled / Bredt / Roark), built-up sections from
   js/builtup_geom.js ("auto"), designer polygons and raw regions from the
   St. Venant approximation J = A⁴ / (40·Ip) — and marks them
   method / J_method = "approx", so the UI never presents them as exact. */

import { ssProps as mseShapeProps } from "./secshapes.js";
import { buSectionProperties as mseBuProps, buNormSection as mseBuNorm } from "./builtup_geom.js";

function msePoly(verts) {
  let A = 0, Sx = 0, Sy = 0, Ixx = 0, Iyy = 0;
  const n = verts.length;
  for (let i = 0; i < n; i++) {
    const [x1, y1] = verts[i], [x2, y2] = verts[(i + 1) % n];
    const cr = x1 * y2 - x2 * y1;
    A += cr; Sx += (x1 + x2) * cr; Sy += (y1 + y2) * cr;
    Ixx += (y1 * y1 + y1 * y2 + y2 * y2) * cr; Iyy += (x1 * x1 + x1 * x2 + x2 * x2) * cr;
  }
  const s = A < 0 ? -1 : 1;
  return { A: s * A / 2, Sx: s * Sx / 6, Sy: s * Sy / 6, Ixx: s * Ixx / 12, Iyy: s * Iyy / 12 };
}

/** Gross polygon properties + the approximate J (holes subtract). */
function mseApprox(loops) {
  let A = 0, Sx = 0, Sy = 0, Ixx = 0, Iyy = 0;
  for (const [sgn, v] of loops) {
    if (!Array.isArray(v) || v.length < 3) continue;
    const g = msePoly(v.map(q => [+q[0], +q[1]]));
    A += sgn * g.A; Sx += sgn * g.Sx; Sy += sgn * g.Sy; Ixx += sgn * g.Ixx; Iyy += sgn * g.Iyy;
  }
  if (!(A > 0)) return { A: 0, J: null, J_method: "approx" };
  const cx = Sx / A, cy = Sy / A, I33 = Ixx - A * cy * cy, I22 = Iyy - A * cx * cx, Ip = I33 + I22;
  return { A, Cy: cx, Cz: cy, I33, I22, Ip, J: Ip > 0 ? A ** 4 / (40 * Ip) : null, J_method: "approx", Cw: null, As2: null, As3: null };
}

export function mseExact(body = {}, model = {}) {
  const kind = body.kind;
  if (kind === "shape") {
    const p = mseShapeProps(body.type, body.dims || {});
    if (p.error) { const e = new Error(p.error); e.status = 400; throw e; }
    return { kind, method: "approx", props: { ...p, J_method: "approx" } };
  }
  if (kind === "builtup") {
    const mats = body.materials || (model && model.materials) || {};
    const sec = mseBuNorm({ ...(body.section || {}), torsion: "auto" });
    const p = mseBuProps(sec, mats);
    return { kind, method: "approx", props: { ...p, J_method: "approx" } };
  }
  if (kind === "designer") {
    const polys = (body.section && body.section.polygons) || [];
    return { kind, method: "approx", props: mseApprox(polys.map(p => [p.hole ? -1 : 1, p.vertices])) };
  }
  if (kind === "regions") {
    const loops = [];
    for (const r of body.regions || []) { loops.push([1, r.outer]); for (const h of r.holes || []) loops.push([-1, h]); }
    return { kind, method: "approx", props: mseApprox(loops) };
  }
  const e = new Error("kind must be 'shape', 'builtup', 'designer' or 'regions'"); e.status = 400; throw e;
}

export default { mseExact };
