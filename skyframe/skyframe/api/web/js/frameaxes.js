/* SkyFrame — frame local axes: the ONE JS implementation (mirrors
   skyframe/core/frame_axes.py; CONTRACT "Frame local axes: ETABS convention").

   model.frame_axis_convention: "legacy" (key absent — old files) | "etabs".
     legacy  vertical member: vecxz = (1,0,0) → bottom-up local 2 = −Y, 3 = +X
     etabs   vertical member: local 2 = +X at angle 0 whichever way it was
             drawn, local 3 = 1 × 2 (bottom-up +Y, top-down −Y)
   Non-vertical members are identical in both (local 2 in the vertical plane,
   pointing up). The angle rotates 2/3 about local 1 (right-hand rule).

   Every viewer path (polygeom.memberAxes, viewext extrusion / overlays,
   plotfn 3D force diagrams, npsect taper display, local-axes overlay) calls
   frameLocalAxes so the viewer always agrees with the engine. */

export const FA_CONVENTIONS = ["etabs", "legacy"];
const TOL = 1e-6;
const unit = v => { const n = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / n, v[1] / n, v[2] / n]; };
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

/** "etabs" | "legacy" of a model dict (or a convention string). */
export function axisConvention(modelOrConv) {
  if (typeof modelOrConv === "string") return modelOrConv === "etabs" ? "etabs" : "legacy";
  return modelOrConv && modelOrConv.frame_axis_convention === "etabs" ? "etabs" : "legacy";
}

function exactCosSin(ang) {
  const q = ang / 90;
  if (q === Math.trunc(q)) return [[1, 0], [0, 1], [-1, 0], [0, -1]][((q % 4) + 4) % 4];
  const a = ang * Math.PI / 180;
  return [Math.cos(a), Math.sin(a)];
}

/** {x, y, z, vertical} — local 1/2/3 of member mm ({pi, pj, angle}) under
    the convention of `modelOrConv` (model dict or string; default legacy). */
export function frameLocalAxes(mm, modelOrConv) {
  const conv = axisConvention(modelOrConv);
  const d = [mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1], mm.pj[2] - mm.pi[2]];
  const x = unit(d);
  const vertical = Math.hypot(d[0], d[1]) < TOL;
  const ang = +mm.angle || 0;
  let y, z;
  if (conv === "etabs" && vertical) {
    y = [1, 0, 0]; z = cross(x, y);
    if (ang !== 0) {
      const [ca, sa] = exactCosSin(ang);
      const y2 = [0, 1, 2].map(k => ca * y[k] + sa * z[k]);
      const z2 = [0, 1, 2].map(k => ca * z[k] - sa * y[k]);
      y = y2; z = z2;
    }
    return { x, y, z, vertical };
  }
  const vecxz = vertical ? [1, 0, 0] : unit([x[1], -x[0], 0]);
  y = unit(cross(vecxz, x));
  z = cross(x, y);
  if (Math.abs(ang) > 1e-12) {
    const a = ang * Math.PI / 180, ca = Math.cos(a), sa = Math.sin(a);
    const y2 = [0, 1, 2].map(k => ca * y[k] + sa * z[k]);
    const z2 = [0, 1, 2].map(k => ca * z[k] - sa * y[k]);
    y = unit(y2); z = unit(z2);
  }
  return { x, y, z, vertical };
}

const wrap180 = a => ((a + 180) % 360 + 360) % 360 - 180;

/** Pure model-dict conversion legacy → etabs (same rule as the backend
    frame_axes.convert_model_to_etabs): every straight VERTICAL member gets
    angle − 90 (wrapped to [−180, 180)) so its local axes — and therefore the
    b/h directions and every result — are unchanged. Mutates `m`; returns
    the per-member report. */
export function convertToEtabs(m) {
  const rep = { converted: false, from: axisConvention(m), to: "etabs", members: [] };
  if (rep.from === "etabs") return rep;
  for (const mm of m.members || []) {
    if (mm.curve) continue;
    if (Math.hypot(mm.pj[0] - mm.pi[0], mm.pj[1] - mm.pi[1]) >= TOL) continue;
    const old = +mm.angle || 0;
    const nw = wrap180(old - 90);
    mm.angle = nw;
    rep.members.push({ uid: mm.uid, old_angle: old, new_angle: nw });
  }
  m.frame_axis_convention = "etabs";
  rep.converted = true;
  return rep;
}

export const conventionLabel = c => (axisConvention(c) === "etabs"
  ? "ETABS (vertical members: local 2 = +X at angle 0)"
  : "Legacy (vertical members: local 2 = −Y at angle 0)");
