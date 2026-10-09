"""Polygon shell regions: geometry helpers + ETABS-style floor/wall auto mesh.

A ShellRegion with exactly 4 corners and only rectangular (parametric)
openings keeps the legacy structured quad mesher in :mod:`skyframe.core.mesh`
bit-for-bit.  Every OTHER region (3 or 5+ corners, convex or concave, or any
region carrying a polygon opening) is a POLYGON REGION handled here.

Auto mesh (polygon regions, ``behavior == "shell"``)
---------------------------------------------------
1. Local plane frame (:func:`region_frame`): ``e3`` = unit Newell normal of
   the corner ordering; horizontal regions use ``e1 = +X`` (so ``u = x``,
   ``v = +-y``); every other plane uses the horizontal ``e1 = Z x e3`` and
   ``e2 = e3 x e1`` (vertical walls: ``v = z``).  The same axes as
   :func:`skyframe.core.loads_ext.shell_local_axes`.
2. HARD mesh lines (always mesh lines, in ``u`` and ``v``) through: every
   polygon / opening vertex; every frame end, link end, support / spring
   point and nodal-load point lying in the region plane inside the region;
   every point where a frame member PIERCES the plane inside the region;
   the in-region ends of every CONSTRAINT SEGMENT.  Constraint segments are
   frame members lying in the plane (beams), and the in-plane traces of the
   other shell regions (coplanar neighbours' edges, walls meeting a slab,
   a slab meeting a wall).
3. OPTIONAL mesh lines: grid lines (every grid system's straight lines,
   taken as vertical planes) and, for non-horizontal regions, story
   elevations — whenever their in-plane trace is parallel to ``u`` or
   ``v``.  An optional line closer than ``OPTIONAL_LINE_GAP * mesh_size``
   to another line is dropped (no sliver strips).  Oblique grid lines are
   ignored (documented).
4. Each interval between consecutive lines is divided into
   ``ceil(len / mesh_size)`` equal parts (``mesh_size`` is the MAXIMUM
   element size).  Every rectangular cell is then cut by the polygon
   edges, opening edges and constraint segments crossing it — each cut is
   a straight chord, so the cell decomposes into CONVEX pieces; pieces
   whose centroid lies inside the region (and outside every opening) are
   kept.  Chord/mesh-line intersection points are computed per line with
   one canonical formula, so neighbouring cells share them exactly.
5. Conformity pass (after all regions are meshed): every pool point
   (other regions' mesh nodes, frame/link ends) lying strictly inside a
   polygon-piece edge is inserted into that piece, so the polygon mesh
   never leaves hanging nodes against neighbouring shells or frames.
6. Element generation per piece: triangle -> one ShellDKGT triangle; a
   convex quadrilateral -> one ShellMITC4 quad; a convex k-gon (k >= 5) ->
   a quad fan (+ one triangle when k is odd); a piece that received
   inserted nodes -> a triangle fan from a clean corner (or from an added
   centroid node).  ``QUAD_DOMINANT = False`` splits every quad into two
   triangles (triangle-only mesh; a module knob used by the validation
   tests).  Element node order is counter-clockwise about ``e3`` starting
   at the vertex whose first edge best follows ``+e1``.

Nodal tributary areas: a third of each triangle's exact area / a quarter of
each quad's exact area per node — the totals equal the exact meshed net
area, so uniform area loads and self-weight are conserved exactly.

Membrane polygon slabs: see :func:`membrane_edge_profiles` (nearest-edge
tributary rule; reduces to the 45-degree two-way rule on a rectangle).
"""

from __future__ import annotations

import bisect
import math
from typing import Dict, List, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]
Vec2 = Tuple[float, float]

PLANAR_TOL = 1e-6          # m, same as BuildingModel validation
POINT_TOL = 1e-6           # m, the point-pool merge tolerance
QUAD_DOMINANT = True       # False -> triangle-only mesh (tests / studies)
OPTIONAL_LINE_GAP = 0.1    # x mesh_size: optional-line drop distance
MEMBRANE_SAMPLES = 80      # samples across the short bbox side (membrane)

_EPS = 1e-10


# --------------------------------------------------------------------------- #
# small vector helpers
# --------------------------------------------------------------------------- #
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    if n < 1e-14:
        raise ValueError("degenerate vector")
    return (a[0] / n, a[1] / n, a[2] / n)


def _f3(p) -> Vec3:
    return (float(p[0]), float(p[1]), float(p[2]))


def newell_normal(pts: Sequence[Vec3]) -> Vec3:
    """Newell normal (length = 2 x polygon area) of a planar 3D polygon."""
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return (nx, ny, nz)


def has_polygon_openings(region) -> bool:
    return any(getattr(op, "polygon", None)
               for op in (getattr(region, "openings", None) or []))


def is_polygon_region(region) -> bool:
    """True when the region takes the polygon path (not the legacy quad)."""
    return len(region.corners) != 4 or has_polygon_openings(region)


def region_normal(region) -> Vec3:
    """Unit plane normal from the corner ordering.

    4 corners: the legacy ``(c1 - c0) x (c3 - c0)`` (bit-identical to every
    pre-polygon consumer); otherwise the Newell normal."""
    c = [_f3(p) for p in region.corners]
    if len(c) == 4:
        n = _cross(_sub(c[1], c[0]), _sub(c[3], c[0]))
    else:
        n = newell_normal(c)
    ln = _norm(n)
    if ln < 1e-12:
        raise ValueError(f"Shell {region.uid}: degenerate corner geometry")
    return (n[0] / ln, n[1] / ln, n[2] / ln)


# --------------------------------------------------------------------------- #
# local plane frame
# --------------------------------------------------------------------------- #
class PlaneFrame:
    """Orthonormal in-plane frame: ``u = p.e1``, ``v = p.e2``, plane
    ``p.e3 = w0`` (absolute coordinates, so coplanar regions share u/v)."""

    def __init__(self, e1: Vec3, e2: Vec3, e3: Vec3, w0: float):
        self.e1, self.e2, self.e3, self.w0 = e1, e2, e3, w0

    def to2(self, p) -> Vec2:
        return (_dot(p, self.e1), _dot(p, self.e2))

    def to3(self, u: float, v: float) -> Vec3:
        e1, e2, e3, w0 = self.e1, self.e2, self.e3, self.w0
        return tuple(u * e1[k] + v * e2[k] + w0 * e3[k] for k in range(3))

    def offset(self, p) -> float:
        return _dot(p, self.e3) - self.w0


def frame_from_normal(n: Vec3, w0: float) -> PlaneFrame:
    if abs(n[2]) > 1.0 - 1e-9:
        s = 1.0 if n[2] > 0 else -1.0
        e3 = (0.0, 0.0, s)
        e1 = (1.0, 0.0, 0.0)
        e2 = (0.0, s, 0.0)
        return PlaneFrame(e1, e2, e3, w0 * 1.0)
    if abs(n[2]) < 1e-9:
        h = math.hypot(n[0], n[1])
        e3 = (n[0] / h, n[1] / h, 0.0)
        e1 = (-e3[1], e3[0], 0.0)
        return PlaneFrame(e1, (0.0, 0.0, 1.0), e3, w0)
    e3 = n
    e1 = _unit(_cross((0.0, 0.0, 1.0), e3))
    e2 = _cross(e3, e1)
    return PlaneFrame(e1, e2, e3, w0)


def region_frame(region) -> PlaneFrame:
    c = [_f3(p) for p in region.corners]
    n = _unit(newell_normal(c))
    fr = frame_from_normal(n, 0.0)
    fr.w0 = sum(_dot(p, fr.e3) for p in c) / len(c)
    return fr


def polygon_map_uv(region, u: float, v: float) -> Vec3:
    """Parametric (u, v) in [0,1]^2 over the polygon's local bounding box
    (``u`` along ``e1``, ``v`` along ``e2``) -> 3D point.  Rectangular
    openings of polygon regions are defined in this frame."""
    fr = region_frame(region)
    p2 = [fr.to2(_f3(p)) for p in region.corners]
    us = [p[0] for p in p2]
    vs = [p[1] for p in p2]
    return fr.to3(min(us) + u * (max(us) - min(us)),
                  min(vs) + v * (max(vs) - min(vs)))


def opening_polygons3(region) -> List[List[Vec3]]:
    """Every opening of the region as a 3D polygon (rect openings mapped by
    ``region.map_uv``; polygon openings as given)."""
    out: List[List[Vec3]] = []
    for op in getattr(region, "openings", None) or []:
        poly = getattr(op, "polygon", None)
        if poly:
            out.append([_f3(p) for p in poly])
        else:
            out.append([_f3(region.map_uv(op.u0, op.v0)),
                        _f3(region.map_uv(op.u1, op.v0)),
                        _f3(region.map_uv(op.u1, op.v1)),
                        _f3(region.map_uv(op.u0, op.v1))])
    return out


# --------------------------------------------------------------------------- #
# 2D polygon helpers
# --------------------------------------------------------------------------- #
def signed_area2(poly: Sequence[Vec2]) -> float:
    s = 0.0
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        s += a[0] * b[1] - b[0] * a[1]
    return 0.5 * s


def point_in_polygon2(p: Vec2, poly: Sequence[Vec2]) -> bool:
    """Even-odd ray cast (boundary points: undefined; use on_boundary2)."""
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xc > x:
                inside = not inside
    return inside


def _point_seg_dist2(p: Vec2, a: Vec2, b: Vec2) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 <= 0.0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
    t = min(max(t, 0.0), 1.0)
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def on_boundary2(p: Vec2, poly: Sequence[Vec2], tol: float) -> bool:
    n = len(poly)
    return any(_point_seg_dist2(p, poly[i], poly[(i + 1) % n]) <= tol
               for i in range(n))


def inside_closed2(p: Vec2, poly: Sequence[Vec2], tol: float) -> bool:
    return on_boundary2(p, poly, tol) or point_in_polygon2(p, poly)


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect2(a, b, c, d, tol: float = 1e-12) -> bool:
    """Closed-segment intersection test (touching counts)."""
    o1, o2 = _orient(a, b, c), _orient(a, b, d)
    o3, o4 = _orient(c, d, a), _orient(c, d, b)
    if ((o1 > tol and o2 < -tol) or (o1 < -tol and o2 > tol)) and \
            ((o3 > tol and o4 < -tol) or (o3 < -tol and o4 > tol)):
        return True

    def on(p, q, r):   # r on segment pq (collinear assumed)
        return (min(p[0], q[0]) - tol <= r[0] <= max(p[0], q[0]) + tol
                and min(p[1], q[1]) - tol <= r[1] <= max(p[1], q[1]) + tol)
    if abs(o1) <= tol and on(a, b, c):
        return True
    if abs(o2) <= tol and on(a, b, d):
        return True
    if abs(o3) <= tol and on(c, d, a):
        return True
    if abs(o4) <= tol and on(c, d, b):
        return True
    return False


def self_intersection2(poly: Sequence[Vec2]) -> Optional[Tuple[int, int]]:
    """First pair of edges (i, j) of a closed polygon that intersect
    illegally (non-adjacent edges touching/crossing, or adjacent edges
    folding back over each other), else None."""
    n = len(poly)
    scale = max(1.0, max(max(abs(p[0]), abs(p[1])) for p in poly))
    tol = 1e-12 * scale * scale
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for j in range(i + 1, n):
            c, d = poly[j], poly[(j + 1) % n]
            if j == i + 1 or (i == 0 and j == n - 1):
                # adjacent: only an overlap (fold-back) is illegal
                shared = b if j == i + 1 else a
                other_i = a if j == i + 1 else b
                other_j = d if j == i + 1 else c
                if abs(_orient(shared, other_i, other_j)) <= tol:
                    u = (other_i[0] - shared[0], other_i[1] - shared[1])
                    w = (other_j[0] - shared[0], other_j[1] - shared[1])
                    if u[0] * w[0] + u[1] * w[1] > 0.0:
                        return (i, j)
                continue
            if segments_intersect2(a, b, c, d, tol):
                return (i, j)
    return None


def ear_clip(poly: Sequence[Vec2]) -> List[Tuple[int, int, int]]:
    """Ear-clipping triangulation of a simple polygon (either orientation)."""
    n = len(poly)
    idx = list(range(n))
    if signed_area2(poly) < 0.0:
        idx.reverse()
    tris: List[Tuple[int, int, int]] = []
    guard = 0
    while len(idx) > 3 and guard < 10 * n * n:
        guard += 1
        m = len(idx)
        clipped = False
        for k in range(m):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % m]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if _orient(a, b, c) <= 1e-14:
                continue
            if any(j not in (i0, i1, i2)
                   and _orient(a, b, poly[j]) >= 0.0
                   and _orient(b, c, poly[j]) >= 0.0
                   and _orient(c, a, poly[j]) >= 0.0
                   for j in idx):
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            clipped = True
            break
        if not clipped:
            # collinear leftovers: drop a zero-area vertex and continue
            for k in range(m):
                a, b, c = poly[idx[k - 1]], poly[idx[k]], poly[idx[(k + 1) % m]]
                if abs(_orient(a, b, c)) <= 1e-14:
                    idx.pop(k)
                    break
            else:
                break
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


# --------------------------------------------------------------------------- #
# validation / Check Model geometry
# --------------------------------------------------------------------------- #
def polygon_problems(corners: Sequence) -> List[Tuple[str, str, Optional[Vec3]]]:
    """Geometry problems of a polygon corner list: [(code, message, loc)].

    Codes: ``SHELL_CORNER_COUNT`` (< 3 corners), ``SHELL_ZERO_AREA``
    (degenerate / zero-length edge), ``SHELL_WARPED`` (a vertex farther
    than PLANAR_TOL from the best-fit Newell plane through the centroid),
    ``SHELL_SELF_INTERSECTING`` (edges cross / touch / fold back)."""
    c = [_f3(p) for p in corners]
    if len(c) < 3:
        return [("SHELL_CORNER_COUNT",
                 f"has {len(c)} corners (at least 3 are required)",
                 c[0] if c else None)]
    out: List[Tuple[str, str, Optional[Vec3]]] = []
    n = len(c)
    edges = [math.dist(c[i], c[(i + 1) % n]) for i in range(n)]
    nrm = newell_normal(c)
    area = 0.5 * _norm(nrm)
    lmax = max(edges)
    cen = tuple(sum(p[k] for p in c) / n for k in range(3))
    if min(edges) < 1e-9 or area <= max(1e-9, 1e-9 * lmax * lmax):
        k = min(range(n), key=lambda i: edges[i])
        out.append(("SHELL_ZERO_AREA",
                    f"is degenerate (area {area:.3g} m^2, shortest edge "
                    f"{edges[k]:.3g} m)", cen))
        return out
    nh = _unit(nrm)
    warp = max(abs(_dot(_sub(p, cen), nh)) for p in c)
    if warp > PLANAR_TOL:
        k = max(range(n), key=lambda i: abs(_dot(_sub(c[i], cen), nh)))
        out.append(("SHELL_WARPED",
                    f"corners are not planar (corner {k + 1} is {warp:.3g} m "
                    f"off the best-fit plane; analysis requires <= "
                    f"{PLANAR_TOL:g} m)", c[k]))
    fr = frame_from_normal(nh, 0.0)
    p2 = [fr.to2(p) for p in c]
    hit = self_intersection2(p2)
    if hit is not None:
        out.append(("SHELL_SELF_INTERSECTING",
                    f"is self-intersecting (edges {hit[0] + 1} and "
                    f"{hit[1] + 1} cross or touch)", c[hit[0]]))
    return out


def validate_polygon_region(region) -> None:
    """ValueError on an invalid polygon region (shape and openings)."""
    if len(region.corners) != 4:
        probs = polygon_problems(region.corners)
        if probs:
            code, msg, _ = probs[0]
            if code == "SHELL_CORNER_COUNT":
                raise ValueError(f"Shell {region.uid}: needs at least 3 "
                                 "corners")
            raise ValueError(f"Shell {region.uid}: polygon {msg}")
    fr = region_frame(region)
    outer = [fr.to2(_f3(p)) for p in region.corners]
    scale = max(1.0, max(max(abs(p[0]), abs(p[1])) for p in outer))
    tol = 1e-9 * scale
    holes: List[List[Vec2]] = []
    for k, op in enumerate(region.openings):
        poly = getattr(op, "polygon", None)
        if poly is not None:
            if len(poly) < 3:
                raise ValueError(f"Shell {region.uid}: opening {k}: polygon "
                                 "needs at least 3 points")
            for p in poly:
                if len(p) != 3 or not all(
                        isinstance(v, (int, float)) and math.isfinite(v)
                        for v in p):
                    raise ValueError(f"Shell {region.uid}: opening {k}: "
                                     "polygon points must be finite (x, y, z)")
                if abs(fr.offset(_f3(p))) > PLANAR_TOL:
                    raise ValueError(f"Shell {region.uid}: opening {k}: "
                                     "polygon is not in the region plane")
        h3 = opening_polygons3(region)[k]
        h2 = [fr.to2(p) for p in h3]
        if abs(signed_area2(h2)) < 1e-12 or self_intersection2(h2):
            raise ValueError(f"Shell {region.uid}: opening {k} is degenerate "
                             "or self-intersecting")
        for p in h2:
            if not inside_closed2(p, outer, tol):
                raise ValueError(f"Shell {region.uid}: opening {k} extends "
                                 "outside the region")
        m, no = len(h2), len(outer)
        for i in range(m):
            a, b = h2[i], h2[(i + 1) % m]
            mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            if not inside_closed2(mid, outer, tol):
                raise ValueError(f"Shell {region.uid}: opening {k} extends "
                                 "outside the region")
            for j in range(no):
                c, d = outer[j], outer[(j + 1) % no]
                if _proper_cross(a, b, c, d):
                    raise ValueError(f"Shell {region.uid}: opening {k} "
                                     "crosses the region boundary")
        for j, g in enumerate(holes):
            if _polys_overlap(h2, g):
                raise ValueError(f"Shell {region.uid}: openings {j} and {k} "
                                 "overlap")
        holes.append(h2)
    net = abs(signed_area2(outer)) - sum(abs(signed_area2(h)) for h in holes)
    if net <= 1e-9:
        raise ValueError(f"Shell {region.uid}: openings remove the whole "
                         "region")


def _proper_cross(a, b, c, d) -> bool:
    o1, o2 = _orient(a, b, c), _orient(a, b, d)
    o3, o4 = _orient(c, d, a), _orient(c, d, b)
    e = 1e-12
    return (((o1 > e and o2 < -e) or (o1 < -e and o2 > e))
            and ((o3 > e and o4 < -e) or (o3 < -e and o4 > e)))


def _polys_overlap(p: Sequence[Vec2], q: Sequence[Vec2]) -> bool:
    """Interiors overlap (edge proper crossings, or one inside the other)."""
    for i in range(len(p)):
        for j in range(len(q)):
            if _proper_cross(p[i], p[(i + 1) % len(p)],
                             q[j], q[(j + 1) % len(q)]):
                return True
    cp = (sum(x for x, _ in p) / len(p), sum(y for _, y in p) / len(p))
    cq = (sum(x for x, _ in q) / len(q), sum(y for _, y in q) / len(q))
    return point_in_polygon2(cp, q) or point_in_polygon2(cq, p)


def polygon_opening_area(region) -> float:
    """Exact total opening area (polygon regions / polygon openings)."""
    from .model import _polygon_area3d
    return sum(_polygon_area3d(h) for h in opening_polygons3(region))


# --------------------------------------------------------------------------- #
# constraint collection
# --------------------------------------------------------------------------- #
def _grid_plan_lines(model) -> List[Tuple[Vec2, Vec2]]:
    """Straight grid lines as (point, unit direction) in plan."""
    out: List[Tuple[Vec2, Vec2]] = []
    try:
        grids = model.effective_grids()
    except Exception:                                    # pragma: no cover
        grids = []
    for g in grids:
        a = math.radians(float(getattr(g, "rotation", 0.0)))
        ca, sa = math.cos(a), math.sin(a)
        if getattr(g, "kind", "orthogonal") == "radial":
            o = g.to_global(0.0, 0.0)
            for t in g.theta_deg:
                b = math.radians(t) + a
                out.append((o, (math.cos(b), math.sin(b))))
            continue
        for xv in g.x_lines:
            out.append((g.to_global(xv, 0.0), (-sa, ca)))
        for yv in g.y_lines:
            out.append((g.to_global(0.0, yv), (ca, sa)))
    return out


def _plane_trace(fr: PlaneFrame, n_other: Vec3, d_other: float
                 ) -> Optional[Tuple[str, float]]:
    """Trace of the plane ``x.n_other = d_other`` in the region plane as an
    axis-parallel local line: ("u", value) / ("v", value) / None."""
    L = _cross(fr.e3, n_other)
    ln = _norm(L)
    if ln < 1e-9:
        return None
    lu, lv = _dot(L, fr.e1) / ln, _dot(L, fr.e2) / ln
    # a point on both planes: solve in the (e1, e2) plane
    a1, a2 = _dot(n_other, fr.e1), _dot(n_other, fr.e2)
    rhs = d_other - fr.w0 * _dot(n_other, fr.e3)
    if abs(lv) < 1e-9:          # trace parallel to u -> constant v
        if abs(a2) < 1e-12:
            return None
        return ("v", rhs / a2)
    if abs(lu) < 1e-9:          # trace parallel to v -> constant u
        if abs(a1) < 1e-12:
            return None
        return ("u", rhs / a1)
    return None


class _Constraints:
    def __init__(self):
        self.points: List[Tuple[Vec2, Vec3]] = []      # hard points
        self.segments: List[Tuple[Vec2, Vec2]] = []    # constraint segments
        self.anchors: Dict[Tuple[float, float], Vec3] = {}
        self.opt_u: List[float] = []
        self.opt_v: List[float] = []

    def anchor(self, p2: Vec2, p3: Vec3) -> None:
        self.anchors.setdefault((round(p2[0], 9), round(p2[1], 9)), p3)


def _collect_constraints(model, region, fr: PlaneFrame) -> _Constraints:
    cons = _Constraints()
    tol = PLANAR_TOL

    def in_plane(p) -> bool:
        return abs(fr.offset(p)) <= tol

    def add_point(p3: Vec3) -> None:
        p2 = fr.to2(p3)
        cons.points.append((p2, p3))
        cons.anchor(p2, p3)

    def add_seg(a3: Vec3, b3: Vec3) -> None:
        a2, b2 = fr.to2(a3), fr.to2(b3)
        if math.hypot(b2[0] - a2[0], b2[1] - a2[1]) <= POINT_TOL:
            return
        cons.segments.append((a2, b2))
        cons.anchor(a2, a3)
        cons.anchor(b2, b3)

    for m in getattr(model, "members", []):
        a, b = _f3(m.pi), _f3(m.pj)
        da, db = fr.offset(a), fr.offset(b)
        ia, ib = abs(da) <= tol, abs(db) <= tol
        if ia and ib:
            add_seg(a, b)
        elif ia:
            add_point(a)
        elif ib:
            add_point(b)
        elif da * db < 0.0:
            t = da / (da - db)
            add_point(tuple(a[k] + t * (b[k] - a[k]) for k in range(3)))
    for lk in getattr(model, "links", []):
        for p in (_f3(lk.pi), _f3(lk.pj)):
            if in_plane(p):
                add_point(p)
    for attr in ("supports", "spring_supports"):
        for s in getattr(model, attr, []) or []:
            p = getattr(s, "point", None)
            if p is not None and len(p) == 3 and in_plane(_f3(p)):
                add_point(_f3(p))
    for pat in (getattr(model, "patterns", {}) or {}).values():
        for nl in getattr(pat, "nodal_loads", []) or []:
            p = getattr(nl, "point", None)
            if p is not None and len(p) == 3 and in_plane(_f3(p)):
                add_point(_f3(p))
    # other shells: in-plane edges, or the trace of their polygon
    for r2 in getattr(model, "shells", []):
        if r2 is region:
            continue
        c2 = [_f3(p) for p in r2.corners]
        if len(c2) < 3:
            continue
        d2 = [fr.offset(p) for p in c2]
        n2 = len(c2)
        if all(abs(d) <= tol for d in d2):
            for i in range(n2):
                add_seg(c2[i], c2[(i + 1) % n2])
            continue
        pts: List[Vec3] = []
        for i in range(n2):
            j = (i + 1) % n2
            if abs(d2[i]) <= tol:
                pts.append(c2[i])
                if abs(d2[j]) <= tol:
                    add_seg(c2[i], c2[j])
            elif abs(d2[j]) > tol and d2[i] * d2[j] < 0.0:
                t = d2[i] / (d2[i] - d2[j])
                pts.append(tuple(c2[i][k] + t * (c2[j][k] - c2[i][k])
                                 for k in range(3)))
        if len(pts) >= 2:
            try:
                nn = _unit(newell_normal(c2))
                L = _unit(_cross(fr.e3, nn))
            except ValueError:
                continue
            pts.sort(key=lambda p: _dot(p, L))
            for i in range(0, len(pts) - 1, 2):
                add_seg(pts[i], pts[i + 1])
    # optional lines: grid lines (vertical planes) and story levels
    for q, d in _grid_plan_lines(model):
        nrm = (-d[1], d[0], 0.0)
        tr = _plane_trace(fr, nrm, nrm[0] * q[0] + nrm[1] * q[1])
        if tr is not None:
            (cons.opt_u if tr[0] == "u" else cons.opt_v).append(tr[1])
    if abs(fr.e3[2]) < 1.0 - 1e-9:
        for s in getattr(model, "stories", []) or []:
            for z in (float(s.elevation), float(s.elevation - s.height)):
                tr = _plane_trace(fr, (0.0, 0.0, 1.0), z)
                if tr is not None:
                    (cons.opt_u if tr[0] == "u" else cons.opt_v).append(tr[1])
    return cons


# --------------------------------------------------------------------------- #
# the mesher
# --------------------------------------------------------------------------- #
def _mesh_lines(mandatory: List[float], optional: List[float], h: float,
                lo: float, hi: float) -> List[float]:
    vals: List[float] = []
    for x in sorted(mandatory):
        if lo - POINT_TOL <= x <= hi + POINT_TOL:
            if not vals or x - vals[-1] > POINT_TOL:
                vals.append(x)
    gap = OPTIONAL_LINE_GAP * h
    for x in sorted(set(optional)):
        if not (lo + gap < x < hi - gap):
            continue
        k = bisect.bisect_left(vals, x)
        near = [vals[i] for i in (k - 1, k) if 0 <= i < len(vals)]
        if all(abs(x - y) > gap for y in near):
            vals.insert(k, x)
    out: List[float] = []
    for a, b in zip(vals[:-1], vals[1:]):
        n = max(1, math.ceil((b - a) / h - 1e-6))
        out.extend(a + (b - a) * k / n for k in range(n))
    out.append(vals[-1])
    return out


def _canon(a: Vec2, b: Vec2) -> Tuple[Vec2, Vec2]:
    return (a, b) if a <= b else (b, a)


def _clip_chord(A: Vec2, B: Vec2, u0, u1, v0, v1
                ) -> Optional[Tuple[Vec2, Vec2]]:
    """Clip segment AB (canonical order) to the cell; intersection points
    on a mesh line are computed by ONE per-line formula (shared exactly by
    the two cells on either side of the line)."""
    du, dv = B[0] - A[0], B[1] - A[1]
    t0, t1 = 0.0, 1.0
    s0, s1 = None, None          # which boundary clipped the entry/exit
    for p, q, tag in ((-du, A[0] - u0, ("u", u0)), (du, u1 - A[0], ("u", u1)),
                      (-dv, A[1] - v0, ("v", v0)), (dv, v1 - A[1], ("v", v1))):
        if abs(p) < 1e-15:
            if q < -1e-12:
                return None
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return None
            if r > t0:
                t0, s0 = r, tag
        else:
            if r < t0:
                return None
            if r < t1:
                t1, s1 = r, tag
    if t1 - t0 <= 1e-12:
        return None

    def at(tag, t, end):
        if tag is None:
            return end
        if tag[0] == "u":
            U = tag[1]
            return (U, A[1] + (U - A[0]) * dv / du)
        V = tag[1]
        return (A[0] + (V - A[1]) * du / dv, V)
    P = at(s0, t0, A)
    Q = at(s1, t1, B)
    return P, Q


def _split_convex(piece: List[Vec2], P: Vec2, Q: Vec2
                  ) -> Optional[Tuple[List[Vec2], List[Vec2]]]:
    """Split a convex piece by line PQ when the chord PQ actually crosses
    the piece interior; None when it does not."""
    nx, ny = -(Q[1] - P[1]), Q[0] - P[0]
    ln = math.hypot(nx, ny)
    nx, ny = nx / ln, ny / ln
    ds = [(x - P[0]) * nx + (y - P[1]) * ny for x, y in piece]
    if max(ds) <= _EPS or min(ds) >= -_EPS:
        return None
    pos: List[Vec2] = []
    neg: List[Vec2] = []
    cut: List[Vec2] = []
    n = len(piece)
    for k in range(n):
        a, b = piece[k], piece[(k + 1) % n]
        da, db = ds[k], ds[(k + 1) % n]
        if da >= -_EPS:
            pos.append(a)
        if da <= _EPS:
            neg.append(a)
            if da >= -_EPS:
                cut.append(a)
        if (da > _EPS and db < -_EPS) or (da < -_EPS and db > _EPS):
            (ca, cda), (cb, cdb) = sorted(((a, da), (b, db)))
            f = cda / (cda - cdb)
            X = (ca[0] + (cb[0] - ca[0]) * f, ca[1] + (cb[1] - ca[1]) * f)
            if ca[0] == cb[0]:
                X = (ca[0], X[1])
            if ca[1] == cb[1]:
                X = (X[0], ca[1])
            for S in (P, Q):
                if abs(X[0] - S[0]) <= 1e-9 and abs(X[1] - S[1]) <= 1e-9:
                    X = S
            pos.append(X)
            neg.append(X)
            cut.append(X)
    # the chord segment itself must overlap the piece's cut line
    dx, dy = Q[0] - P[0], Q[1] - P[1]
    L2 = dx * dx + dy * dy
    ts = [((x - P[0]) * dx + (y - P[1]) * dy) / L2 for x, y in cut]
    if not ts or max(ts) <= 1e-9 or min(ts) >= 1.0 - 1e-9:
        return None
    if len(pos) < 3 or len(neg) < 3:
        return None
    return pos, neg


def _piece_area(p: Sequence[Vec2]) -> float:
    return signed_area2(p)


def _region_pieces2(region, fr: PlaneFrame, cons: _Constraints
                    ) -> List[List[Vec2]]:
    """Convex 2D pieces (CCW in the local frame) covering the region."""
    outer = [fr.to2(_f3(p)) for p in region.corners]
    for p2, p3 in zip(outer, region.corners):
        cons.anchor(p2, _f3(p3))
    holes3 = opening_polygons3(region)
    holes = [[fr.to2(p) for p in h] for h in holes3]
    for h2, h3 in zip(holes, holes3):
        for p2, p3 in zip(h2, h3):
            cons.anchor(p2, p3)
    us = [p[0] for p in outer]
    vs = [p[1] for p in outer]
    ulo, uhi, vlo, vhi = min(us), max(us), min(vs), max(vs)
    scale = max(1.0, uhi - ulo, vhi - vlo)
    btol = 1e-9 * scale

    def in_region(p: Vec2, closed: bool = True) -> bool:
        if closed:
            if not inside_closed2(p, outer, btol):
                return False
        elif not point_in_polygon2(p, outer):
            return False
        return not any(point_in_polygon2(p, h) and not
                       on_boundary2(p, h, btol) for h in holes)

    mand_u = list(us) + [p[0] for h in holes for p in h]
    mand_v = list(vs) + [p[1] for h in holes for p in h]
    for p2, _ in cons.points:
        if in_region(p2):
            mand_u.append(p2[0])
            mand_v.append(p2[1])
    segs: List[Tuple[Vec2, Vec2]] = []
    for a, b in cons.segments:
        if (max(a[0], b[0]) < ulo - btol or min(a[0], b[0]) > uhi + btol
                or max(a[1], b[1]) < vlo - btol
                or min(a[1], b[1]) > vhi + btol):
            continue
        touches = False
        for p in (a, b, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)):
            if in_region(p):
                touches = True
        if not touches:
            n = len(outer)
            touches = any(_proper_cross(a, b, outer[i], outer[(i + 1) % n])
                          for i in range(n))
        if not touches:
            continue
        segs.append(_canon(a, b))
        for p in (a, b):
            if in_region(p):
                mand_u.append(p[0])
                mand_v.append(p[1])
    h = float(region.mesh_size)
    ul = _mesh_lines(mand_u, cons.opt_u, h, ulo, uhi)
    vl = _mesh_lines(mand_v, cons.opt_v, h, vlo, vhi)
    nx, ny = len(ul) - 1, len(vl) - 1

    # chords: polygon edges, hole edges, constraint segments
    all_segs: List[Tuple[Vec2, Vec2]] = []
    for poly in [outer] + holes:
        n = len(poly)
        for i in range(n):
            all_segs.append(_canon(poly[i], poly[(i + 1) % n]))
    all_segs.extend(segs)
    buckets: Dict[Tuple[int, int], List[int]] = {}
    for si, (a, b) in enumerate(all_segs):
        i0 = max(0, bisect.bisect_right(ul, min(a[0], b[0]) + btol) - 1)
        i1 = min(nx - 1, bisect.bisect_left(ul, max(a[0], b[0]) - btol))
        j0 = max(0, bisect.bisect_right(vl, min(a[1], b[1]) + btol) - 1)
        j1 = min(ny - 1, bisect.bisect_left(vl, max(a[1], b[1]) - btol))
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                buckets.setdefault((i, j), []).append(si)

    pieces_out: List[List[Vec2]] = []
    for i in range(nx):
        u0, u1 = ul[i], ul[i + 1]
        for j in range(ny):
            v0, v1 = vl[j], vl[j + 1]
            rect = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
            sids = buckets.get((i, j))
            if not sids:
                c = ((u0 + u1) / 2.0, (v0 + v1) / 2.0)
                if in_region(c, closed=False):
                    pieces_out.append(rect)
                continue
            pieces = [rect]
            for si in sids:
                A, B = all_segs[si]
                ch = _clip_chord(A, B, u0, u1, v0, v1)
                if ch is None:
                    continue
                P, Q = ch
                if math.hypot(Q[0] - P[0], Q[1] - P[1]) <= 1e-9:
                    continue
                if ((P[0] == Q[0] and P[0] in (u0, u1))
                        or (P[1] == Q[1] and P[1] in (v0, v1))):
                    continue                 # along the cell boundary
                nxt: List[List[Vec2]] = []
                for pc in pieces:
                    sp = _split_convex(pc, P, Q)
                    if sp is None:
                        nxt.append(pc)
                    else:
                        nxt.extend(sp)
                pieces = nxt
            for pc in pieces:
                if _piece_area(pc) <= 1e-12 * scale * scale:
                    continue
                c = (sum(p[0] for p in pc) / len(pc),
                     sum(p[1] for p in pc) / len(pc))
                if in_region(c, closed=False):
                    pieces_out.append(pc)
    return pieces_out


def mesh_polygon_pieces(model, region, pool) -> List[List[int]]:
    """Mesh one polygon region into convex pieces of pool point indices
    (counter-clockwise about the region normal).  Elements are generated
    later by :func:`finalize_pieces` (after the conformity pass)."""
    fr = region_frame(region)
    cons = _collect_constraints(model, region, fr)
    pieces2 = _region_pieces2(region, fr, cons)
    anchors = cons.anchors
    out: List[List[int]] = []
    for pc in pieces2:
        ids: List[int] = []
        for p in pc:
            p3 = anchors.get((round(p[0], 9), round(p[1], 9)))
            if p3 is None:
                p3 = fr.to3(p[0], p[1])
            idx = pool.add(p3)
            if not ids or ids[-1] != idx:
                ids.append(idx)
        while len(ids) > 1 and ids[0] == ids[-1]:
            ids.pop()
        if len(ids) >= 3:
            out.append(ids)
    return out


# --------------------------------------------------------------------------- #
# conformity pass + element generation
# --------------------------------------------------------------------------- #
class _Hash:
    def __init__(self, pts: Sequence[Vec3], cell: float):
        self.cell = cell
        self.d: Dict[Tuple[int, int, int], List[int]] = {}
        for i, p in enumerate(pts):
            self.d.setdefault(self._k(p), []).append(i)

    def _k(self, p):
        c = self.cell
        return (math.floor(p[0] / c), math.floor(p[1] / c),
                math.floor(p[2] / c))

    def query(self, lo, hi) -> List[int]:
        k0, k1 = self._k(lo), self._k(hi)
        out: List[int] = []
        span = ((k1[0] - k0[0] + 1) * (k1[1] - k0[1] + 1)
                * (k1[2] - k0[2] + 1))
        if span > 4 * len(self.d):
            for v in self.d.values():
                out.extend(v)
            return out
        for a in range(k0[0], k1[0] + 1):
            for b in range(k0[1], k1[1] + 1):
                for c in range(k0[2], k1[2] + 1):
                    out.extend(self.d.get((a, b, c), ()))
        return out


def _hanging_on(pts, hsh: _Hash, a: int, b: int) -> List[int]:
    pa, pb = pts[a], pts[b]
    d = _sub(pb, pa)
    L = _norm(d)
    if L <= POINT_TOL:
        return []
    u = (d[0] / L, d[1] / L, d[2] / L)
    lo = tuple(min(pa[k], pb[k]) - POINT_TOL for k in range(3))
    hi = tuple(max(pa[k], pb[k]) + POINT_TOL for k in range(3))
    found: List[Tuple[float, int]] = []
    for idx in hsh.query(lo, hi):
        if idx == a or idx == b:
            continue
        p = pts[idx]
        if not all(lo[k] <= p[k] <= hi[k] for k in range(3)):
            continue
        t = _dot(_sub(p, pa), u)
        if t <= POINT_TOL or t >= L - POINT_TOL:
            continue
        foot = (pa[0] + t * u[0], pa[1] + t * u[1], pa[2] + t * u[2])
        if _norm(_sub(p, foot)) < POINT_TOL:
            found.append((t, idx))
    found.sort()
    return [i for _, i in found]


def _start_rot(ids: List[int], p2: Dict[int, Vec2]) -> List[int]:
    """Rotate a CCW element so its first edge best follows +e1."""
    n = len(ids)
    best, bk = None, 0
    for k in range(n):
        a, b = p2[ids[k]], p2[ids[(k + 1) % n]]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        score = (round(dx / L, 9), -round(a[1], 9), -round(a[0], 9))
        if best is None or score > best:
            best, bk = score, k
    return ids[bk:] + ids[:bk]


def _tri_area2(p2, a, b, c) -> float:
    return _orient(p2[a], p2[b], p2[c]) / 2.0


def _elements(ids: List[int], inserted: bool, p2: Dict[int, Vec2],
              pool) -> List[List[int]]:
    n = len(ids)
    if n < 3:
        return []
    scale = max(1e-12, max(
        math.hypot(p2[ids[k]][0] - p2[ids[(k + 1) % n]][0],
                   p2[ids[k]][1] - p2[ids[(k + 1) % n]][1])
        for k in range(n)))
    atol = 1e-9 * scale * scale
    flat = [abs(_orient(p2[ids[k - 1]], p2[ids[k]], p2[ids[(k + 1) % n]]))
            <= 1e-6 * scale * scale for k in range(n)]
    if n == 3:
        return [ids] if _tri_area2(p2, *ids) > atol else []
    if not inserted and not any(flat):
        if n == 4:
            return [ids]
        # convex k-gon: quad fan from the vertex with the largest angle
        def angle(k):
            a, b, c = p2[ids[k - 1]], p2[ids[k]], p2[ids[(k + 1) % n]]
            v1 = (a[0] - b[0], a[1] - b[1])
            v2 = (c[0] - b[0], c[1] - b[1])
            return math.atan2(abs(v1[0] * v2[1] - v1[1] * v2[0]),
                              v1[0] * v2[0] + v1[1] * v2[1])
        k0 = max(range(n), key=angle)
        r = ids[k0:] + ids[:k0]
        out: List[List[int]] = []
        k = 1
        while k + 2 < n:
            out.append([r[0], r[k], r[k + 1], r[k + 2]])
            k += 2
        if k + 1 < n:
            out.append([r[0], r[k], r[k + 1]])
        return out
    # inserted / collinear vertices: triangle fan from a clean corner whose
    # two adjacent edges are straight (no flat neighbour), else centroid
    for k in range(n):
        if flat[k] or flat[k - 1] or flat[(k + 1) % n]:
            continue
        r = ids[k:] + ids[:k]
        tris = [[r[0], r[i], r[i + 1]] for i in range(1, n - 1)]
        if all(_tri_area2(p2, *t) > atol for t in tris):
            return tris
    pts = pool.points
    cen = tuple(sum(pts[i][c] for i in ids) / n for c in range(3))
    ci = pool.add(cen)
    p2[ci] = (sum(p2[i][0] for i in ids) / n, sum(p2[i][1] for i in ids) / n)
    return [[ci, ids[k], ids[(k + 1) % n]] for k in range(n)
            if _tri_area2(p2, ci, ids[k], ids[(k + 1) % n]) > atol]


def finalize_pieces(model, pieces: Dict[str, List[List[int]]], pool,
                    quads: list, region_trib: Dict[str, Dict[int, float]],
                    make_quad) -> None:
    """Conformity pass + element generation for every polygon region.

    ``make_quad(region_uid, nodes_tuple)`` builds the mesh element record;
    elements are appended to ``quads`` and nodal tributary areas (exact
    element area / node count) written to ``region_trib``."""
    from .model import _polygon_area3d
    regions = {r.uid: r for r in model.shells}
    pts = pool.points
    sizes = sorted(float(regions[u].mesh_size) for u in pieces)
    cell = max(sizes[len(sizes) // 2] if sizes else 1.0, 1e-3)
    hsh = _Hash(pts, cell)
    for ruid, plist in pieces.items():
        region = regions[ruid]
        fr = region_frame(region)
        trib: Dict[int, float] = {}
        for ids in plist:
            aug: List[int] = []
            inserted = False
            n = len(ids)
            for k in range(n):
                aug.append(ids[k])
                hang = _hanging_on(pts, hsh, ids[k], ids[(k + 1) % n])
                if hang:
                    inserted = True
                    aug.extend(hang)
            p2 = {i: fr.to2(pts[i]) for i in aug}
            if signed_area2([p2[i] for i in aug]) < 0.0:
                aug.reverse()
            for el in _elements(aug, inserted, p2, pool):
                if not QUAD_DOMINANT and len(el) == 4:
                    a, b, c, d = el
                    if math.dist(pts[a], pts[c]) <= math.dist(pts[b], pts[d]):
                        subs = [[a, b, c], [a, c, d]]
                    else:
                        subs = [[a, b, d], [b, c, d]]
                else:
                    subs = [el]
                for e in subs:
                    e = _start_rot(e, p2)
                    quads.append(make_quad(ruid, tuple(e)))
                    share = _polygon_area3d([pool.points[i] for i in e]) \
                        / len(e)
                    for i in e:
                        trib[i] = trib.get(i, 0.0) + share
        # the pool rounds coordinates to 1e-6 m, so boundary points on
        # oblique edges sit up to 5e-7 m off the exact outline: rescale the
        # tributaries to the EXACT net area (a ~1e-8 relative correction)
        # so uniform area loads / self-weight conserve q * net_area exactly
        tot = sum(trib.values())
        exact = float(region.net_area)
        if tot > 0.0 and exact > 0.0 and abs(tot - exact) > 1e-15 * exact:
            f = exact / tot
            trib = {i: a * f for i, a in trib.items()}
        region_trib[ruid] = trib


# --------------------------------------------------------------------------- #
# membrane polygon slabs: nearest-edge tributary profiles
# --------------------------------------------------------------------------- #
def membrane_edge_profiles(region) -> Tuple[List[Vec3], List[List[Tuple[float, float]]]]:
    """Per-edge tributary load profiles of a polygon membrane slab (per
    unit q), as piecewise-constant breakpoint lists ``[(s, w), ...]``.

    TRIBUTARY RULE (documented): every point of the slab (net of openings)
    sends its load to the NEAREST boundary edge (shortest distance to the
    edge segment), at the foot of its perpendicular (clamped to the edge).
    For a convex polygon these tributary zones are exactly the faces of
    the 45-degree "roof" (straight skeleton), so on a rectangle the rule
    reproduces the classic two-way trapezoid/triangle distribution; on
    concave polygons it is the natural medial-axis generalisation.  The
    field is integrated numerically (``MEMBRANE_SAMPLES`` sample cells
    across the short side of the bounding box) and binned into ``>= 8``
    equal bins per edge (step profile); the caller rescales the profiles
    so the load total is conserved exactly."""
    fr = region_frame(region)
    c3 = [_f3(p) for p in region.corners]
    outer = [fr.to2(p) for p in c3]
    holes = [[fr.to2(p) for p in h] for h in opening_polygons3(region)]
    n = len(outer)
    us = [p[0] for p in outer]
    vs = [p[1] for p in outer]
    lu, lv = max(us) - min(us), max(vs) - min(vs)
    hs = min(lu, lv) / MEMBRANE_SAMPLES
    nu = max(1, int(math.ceil(lu / hs)))
    nv = max(1, int(math.ceil(lv / hs)))
    du, dv = lu / nu, lv / nv
    lens = [math.hypot(outer[(k + 1) % n][0] - outer[k][0],
                       outer[(k + 1) % n][1] - outer[k][1]) for k in range(n)]
    nbins = [max(8, int(math.ceil(L / (2.0 * hs)))) for L in lens]
    acc = [[0.0] * nb for nb in nbins]
    for i in range(nu):
        x = min(us) + (i + 0.5) * du
        for j in range(nv):
            y = min(vs) + (j + 0.5) * dv
            p = (x, y)
            if not point_in_polygon2(p, outer):
                continue
            if any(point_in_polygon2(p, hh) for hh in holes):
                continue
            best, bk, bt = None, 0, 0.0
            for k in range(n):
                a, b = outer[k], outer[(k + 1) % n]
                ex, ey = b[0] - a[0], b[1] - a[1]
                t = ((x - a[0]) * ex + (y - a[1]) * ey) / (lens[k] ** 2)
                t = min(max(t, 0.0), 1.0)
                dd = math.hypot(x - a[0] - t * ex, y - a[1] - t * ey)
                if best is None or dd < best - 1e-12:
                    best, bk, bt = dd, k, t
            bi = min(nbins[bk] - 1, int(bt * nbins[bk]))
            acc[bk][bi] += du * dv
    profiles: List[List[Tuple[float, float]]] = []
    for k in range(n):
        L, nb = lens[k], nbins[k]
        bl = L / nb
        bps: List[Tuple[float, float]] = []
        for b in range(nb):
            w = acc[k][b] / bl
            bps.append((b * bl, w))
            bps.append(((b + 1) * bl if b < nb - 1 else L, w))
        profiles.append(bps)
    return c3, profiles


def polygon_area_triangles(region) -> List[Tuple[Vec3, Vec3, Vec3]]:
    """Ear-clipped 3D triangles of the region's OUTER polygon (gross)."""
    fr = region_frame(region)
    c3 = [_f3(p) for p in region.corners]
    p2 = [fr.to2(p) for p in c3]
    return [(c3[a], c3[b], c3[c]) for a, b, c in ear_clip(p2)]
