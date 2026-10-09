/* SkyFrame — mock-mode support for polygon shells and frame insertion
   points (CONTRACT "Polygon shells and auto mesh" + "Insertion point, joint
   offsets and automatic end offsets").

   * mockPolyMesh(sh, tagFor): a plausible ETABS-like auto mesh for polygon
     regions (3 or 5+ corners, or polygon openings) — the region's local
     bounding box is cut into mesh_size cells, each cell is clipped by the
     polygon; full cells stay quads, cut cells become triangle fans, so the
     ?mock=1 results carry 3-node shell_quads entries to render.
   * mockValidatePoly(model): the backend's ValueError wording for bad
     polygons and bad insertion / end-offset fields, so mock saves reject
     what the live server would. */

import { regionFrame, isLegacyQuad, polygonProblem2, clipToBox2, signedArea2,
  pointInPoly2, openingPolygon3, polyInside2, polysOverlap2, newell } from "./polygeom.js";

/** Mesh one polygon region → [{region, nodes:[tag,...]}] (3 or 4 tags). */
export function mockPolyMesh(sh, tagFor) {
  const out = [];
  const corners = sh.corners || [];
  if (corners.length < 3) return out;
  const fr = regionFrame(corners);
  const poly = corners.map(fr.to2);
  const holes = (sh.openings || []).map(o => openingPolygon3(sh, o).map(fr.to2));
  const us = poly.map(p => p[0]), vs = poly.map(p => p[1]);
  const u0 = Math.min(...us), u1 = Math.max(...us), v0 = Math.min(...vs), v1 = Math.max(...vs);
  const size = Math.max(+sh.mesh_size || 1, 0.05);
  const nu = Math.max(1, Math.min(60, Math.ceil((u1 - u0) / size - 1e-9)));
  const nv = Math.max(1, Math.min(60, Math.ceil((v1 - v0) / size - 1e-9)));
  const du = (u1 - u0) / nu, dv = (v1 - v0) / nv;
  const tag = p => tagFor(fr.to3(p[0], p[1]).map(v => Math.round(v * 1e6) / 1e6));
  for (let j = 0; j < nv; j++)
    for (let i = 0; i < nu; i++) {
      const xa = u0 + i * du, xb = xa + du, ya = v0 + j * dv, yb = ya + dv;
      let piece = clipToBox2(poly, xa, ya, xb, yb);
      if (piece.length < 3 || Math.abs(signedArea2(piece)) < 1e-9 * du * dv) continue;
      const cx = piece.reduce((s, p) => s + p[0], 0) / piece.length;
      const cy = piece.reduce((s, p) => s + p[1], 0) / piece.length;
      if (!pointInPoly2(cx, cy, poly)) continue;
      if (holes.some(h => pointInPoly2(cx, cy, h))) continue;
      if (signedArea2(piece) < 0) piece = piece.reverse();
      const full = piece.length === 4 && Math.abs(signedArea2(piece) - du * dv) < 1e-9 * Math.max(1, du * dv);
      if (full) { out.push({ region: sh.uid, nodes: piece.map(tag) }); continue; }
      const tags = piece.map(tag);
      for (let k = 1; k + 1 < piece.length; k++)
        out.push({ region: sh.uid, nodes: [tags[0], tags[k], tags[k + 1]] });
    }
  return out;
}

export { isLegacyQuad };

const fin = v => typeof v === "number" && isFinite(v);

/** First validation error (backend wording) or "". */
export function mockValidatePoly(model) {
  if (!model) return "";
  for (const sh of (model.shells || [])) {
    const c = sh.corners || [];
    if (c.length < 3) return `Shell ${sh.uid}: needs at least 3 corners (got ${c.length})`;
    if (c.length === 4 && isLegacyQuad(sh)) continue;   // legacy path: unchanged checks
    const fr = regionFrame(c);
    for (const p of c)
      if (Math.abs(p[0] * fr.e3[0] + p[1] * fr.e3[1] + p[2] * fr.e3[2] - fr.w0) > 1e-6)
        return `Shell ${sh.uid}: corners are not coplanar (vertex off the best-fit plane by more than 1e-6 m)`;
    const n = newell(c);
    if (Math.hypot(n[0], n[1], n[2]) < 1e-12) return `Shell ${sh.uid}: zero area`;
    const p2 = c.map(fr.to2);
    const why = polygonProblem2(p2);
    if (why) return `Shell ${sh.uid}: ${why}`;
    const hs = [];
    for (const o of (sh.openings || [])) {
      if (o.polygon != null && (!Array.isArray(o.polygon) || o.polygon.length < 3))
        return `Shell ${sh.uid}: opening polygon needs at least 3 points`;
      const h = openingPolygon3(sh, o).map(fr.to2);
      const hw = polygonProblem2(h);
      if (hw) return `Shell ${sh.uid}: opening is degenerate (${hw})`;
      if (!polyInside2(h, p2)) return `Shell ${sh.uid}: opening outside the region or crossing its boundary`;
      if (hs.some(x => polysOverlap2(x, h))) return `Shell ${sh.uid}: openings overlap each other`;
      hs.push(h);
    }
  }
  for (const m of (model.members || [])) {
    const cp = m.cardinal_point;
    if (cp != null && !(Number.isInteger(cp) && cp >= 1 && cp <= 11))
      return `Member ${m.uid}: cardinal_point must be an integer 1..11 (got ${cp})`;
    const jo = m.joint_offsets;
    if (jo != null) {
      if (typeof jo !== "object" || Array.isArray(jo)) return `Member ${m.uid}: joint_offsets must be a dict`;
      const bad = Object.keys(jo).filter(k => !["i", "j", "system"].includes(k));
      if (bad.length) return `Member ${m.uid}: joint_offsets has unknown keys ${JSON.stringify(bad.sort())}`;
      if (!["global", "local"].includes(jo.system || "global"))
        return `Member ${m.uid}: joint_offsets system must be one of ('global', 'local')`;
      for (const e of ["i", "j"]) {
        const v = jo[e];
        if (v != null && !(Array.isArray(v) && v.length === 3 && v.every(fin)))
          return `Member ${m.uid}: joint_offsets['${e}'] must be 3 finite numbers`;
      }
    }
    if (m.no_transform_stiffness != null && typeof m.no_transform_stiffness !== "boolean")
      return `Member ${m.uid}: no_transform_stiffness must be a bool`;
    if (m.end_offsets != null && !["manual", "auto"].includes(m.end_offsets))
      return `Member ${m.uid}: end_offsets must be one of ('manual', 'auto') (got '${m.end_offsets}')`;
    if (m.auto_rigid_factor != null && !(fin(m.auto_rigid_factor) && m.auto_rigid_factor >= 0 && m.auto_rigid_factor <= 1))
      return `Member ${m.uid}: auto_rigid_factor must be in [0, 1] (got ${m.auto_rigid_factor})`;
    const ins = (cp != null && cp !== 10) || !!jo;
    if (ins && m.axial_limit && m.axial_limit !== "both")
      return `Member ${m.uid}: axial-only members do not support insertion points / joint offsets`;
  }
  return "";
}
