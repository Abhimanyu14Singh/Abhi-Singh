"""Edit Shells on shells WITH OPENINGS (Wave C, ETABS parity).

CONTRACT "Wave C interface: general grids, explorer, shells with openings,
snapping, units, auto-save, model lock".

Divide / chop / merge / expand-shrink and the vertex reshaper used to
refuse shells that carry ``openings``.  This module keeps the openings:

* every opening is first turned into an ABSOLUTE 3D polygon
  (:func:`holes3`: rectangular openings through the region's own
  parametric map, polygon openings as given), so an opening stays where it
  is whatever happens to the outline;
* divide / chop: each cutting cell ``C`` (a convex piece of the parent
  outline) becomes ``C minus every opening`` (:func:`subtract`); the pieces
  of a cell are the counter-clockwise loops of that region, the openings
  that lie wholly inside a piece become its own openings, and an opening
  crossed by a cut line becomes a notch in the neighbouring pieces;
* merge: the union of the coplanar shells minus their openings
  (:func:`union`): exactly one outer loop (else 400), every inner loop is
  an opening of the merged shell (so divide -> merge restores the hole);
* expand / reshape: the outline changes, the openings keep their absolute
  position (:func:`openings_for` re-expresses them) and must stay inside.

Region boolean (exact up to the 1e-9 m point cleaning of the edit ops):
the boundaries of the cell (or shells) and of the openings are split at
every mutual intersection / touching vertex; a sub-segment is a boundary
of the region R when the point just left of its midpoint is in R and the
point just right is not (then it is oriented with R on its left).  The
directed boundary edges are traced into loops, always turning to the
first outgoing edge clockwise from the reverse of the arriving edge, so
regions that touch at a point stay separate loops.  Loops that revisit a
vertex are split there.  Counter-clockwise loops are pieces, clockwise
loops are holes (assigned to the smallest piece containing them).  The
net area therefore satisfies  sum(pieces) - sum(holes) = area(C) -
area(C n openings)  exactly (it is the same set).

Opening records: when a piece is a 4-corner parallelogram and an opening
is a rectangle aligned with its parametric directions, the opening is the
legacy ``{u0, v0, u1, v1}`` record (structured mesh); otherwise it is a
polygon opening ``{u0: 0, v0: 0, u1: 1, v1: 1, polygon}`` (CONTRACT
"Polygon shells and auto mesh").  ``js/wc_shellholes.js`` mirrors this
module for ``?mock=1``.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

Vec2 = Tuple[float, float]


# --------------------------------------------------------------------------- #
# small vector helpers
# --------------------------------------------------------------------------- #
def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    if n < 1e-15:
        raise ValueError("degenerate shell plane")
    return [a[0] / n, a[1] / n, a[2] / n]


def _clean(p) -> List[float]:
    return [round(float(v), 9) + 0.0 for v in p]


def newell(pts) -> List[float]:
    n = [0.0, 0.0, 0.0]
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return n


class Frame:
    """In-plane frame of a shell: origin corner 0, e1 along the first
    non-degenerate edge, e3 the Newell normal, e2 = e3 x e1."""

    def __init__(self, corners):
        c = [[float(v) for v in p] for p in corners]
        self.o = c[0]
        self.n = _unit(newell(c))
        e1 = None
        for i in range(len(c)):
            d = _sub(c[(i + 1) % len(c)], c[i])
            if _norm(d) > 1e-12:
                e1 = _unit(d)
                break
        if e1 is None:
            raise ValueError("degenerate shell")
        self.e1 = e1
        self.e2 = _cross(self.n, e1)

    def to2(self, p) -> Vec2:
        d = _sub([float(v) for v in p], self.o)
        return (_dot(d, self.e1), _dot(d, self.e2))

    def to3(self, q) -> List[float]:
        return [self.o[k] + q[0] * self.e1[k] + q[1] * self.e2[k]
                for k in range(3)]

    def project(self, p) -> List[float]:
        """Orthogonal projection of a 3D point onto the shell plane."""
        return self.to3(self.to2(p))


# --------------------------------------------------------------------------- #
# 2D polygon helpers
# --------------------------------------------------------------------------- #
def signed_area(poly: Sequence[Vec2]) -> float:
    s = 0.0
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        s += a[0] * b[1] - b[0] * a[1]
    return 0.5 * s


def point_in(p: Vec2, poly: Sequence[Vec2]) -> bool:
    """Strict-ish ray-casting point-in-polygon (boundary undefined)."""
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xc = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < xc:
                inside = not inside
        j = i
    return inside


def _key2(p) -> Tuple[float, float]:
    return (round(p[0], 9) + 0.0, round(p[1], 9) + 0.0)


def _seg_cross(a, b, c, d):
    """Proper or touching intersection point of segments ab, cd (not
    collinear), or None."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-15 * max(1.0, math.hypot(rx, ry) * math.hypot(sx, sy)):
        return None
    qx, qy = c[0] - a[0], c[1] - a[1]
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    e = 1e-12
    if -e <= t <= 1 + e and -e <= u <= 1 + e:
        return (a[0] + t * rx, a[1] + t * ry)
    return None


def _boundary_loops(polys: Sequence[Sequence[Vec2]], inside) -> List[list]:
    """Directed boundary loops of the region ``inside(p)`` whose boundary
    lies on the edges of ``polys`` (module doc)."""
    pts = [q for pg in polys for q in pg]
    if not pts:
        return []
    scale = max(1.0, max(max(abs(q[0]), abs(q[1])) for q in pts))
    tol_on = 1e-8 * scale
    segs = []
    for pg in polys:
        for i in range(len(pg)):
            a, b = pg[i], pg[(i + 1) % len(pg)]
            if math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-12 * scale:
                segs.append((tuple(a), tuple(b)))
    # every vertex and every crossing point, rounded ONCE (shared keys)
    vert: Dict[Tuple[float, float], Vec2] = {}
    for q in pts:
        vert.setdefault(_key2(q), _key2(q))
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            x = _seg_cross(*segs[i], *segs[j])
            if x is not None:
                vert.setdefault(_key2(x), _key2(x))
    allv = list(vert.values())
    sub: Dict[tuple, Tuple[Vec2, Vec2]] = {}
    for a, b in segs:
        ab = (b[0] - a[0], b[1] - a[1])
        L2 = ab[0] ** 2 + ab[1] ** 2
        on = []
        for q in allv:
            t = ((q[0] - a[0]) * ab[0] + (q[1] - a[1]) * ab[1]) / L2
            if -1e-12 <= t <= 1 + 1e-12:
                fx, fy = a[0] + t * ab[0], a[1] + t * ab[1]
                if math.hypot(q[0] - fx, q[1] - fy) <= tol_on:
                    on.append((t, q))
        on.sort()
        chain = []
        for t, q in on:
            if not chain or _key2(chain[-1]) != _key2(q):
                chain.append(q)
        for k in range(len(chain) - 1):
            p, q = chain[k], chain[k + 1]
            if math.hypot(q[0] - p[0], q[1] - p[1]) <= 1e-9 * scale:
                continue
            kk = tuple(sorted((_key2(p), _key2(q))))
            sub.setdefault(kk, (p, q))
    # classify each undirected sub-segment
    out_edges: Dict[tuple, list] = {}
    nedge = 0
    for p, q in sub.values():
        dx, dy = q[0] - p[0], q[1] - p[1]
        L = math.hypot(dx, dy)
        eps = min(1e-6 * scale, 0.05 * L)
        mx, my = (p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0
        nx, ny = -dy / L, dx / L
        left = inside((mx + eps * nx, my + eps * ny))
        right = inside((mx - eps * nx, my - eps * ny))
        if left and not right:
            a, b = p, q
        elif right and not left:
            a, b = q, p
        else:
            continue
        out_edges.setdefault(_key2(a), []).append(_key2(b))
        nedge += 1
    used = set()
    loops = []

    def nxt(u, v):
        rx, ry = u[0] - v[0], u[1] - v[1]
        best, bang = None, None
        for w in out_edges.get(v, []):
            if (v, w) in used:
                continue
            wx, wy = w[0] - v[0], w[1] - v[1]
            ccw = math.atan2(rx * wy - ry * wx, rx * wx + ry * wy)
            cw = (-ccw) % (2.0 * math.pi)
            if cw <= 1e-12:
                cw = 2.0 * math.pi
            if bang is None or cw < bang:
                best, bang = w, cw
        return best

    for a in sorted(out_edges):
        for b in list(out_edges[a]):
            if (a, b) in used:
                continue
            loop = [a]
            used.add((a, b))
            u, v = a, b
            guard = 0
            while v != a:
                loop.append(v)
                w = nxt(u, v)
                if w is None:
                    raise ValueError("could not trace the shell boundary "
                                     "around the openings")
                used.add((v, w))
                u, v = v, w
                guard += 1
                if guard > nedge + 2:
                    raise ValueError("could not trace the shell boundary "
                                     "around the openings")
            loops.extend(_split_pinches(loop))
    return [_drop_collinear2(lp) for lp in loops if len(lp) >= 3]


def _split_pinches(loop: list) -> List[list]:
    seen: Dict[tuple, int] = {}
    for j, p in enumerate(loop):
        if p in seen:
            i = seen[p]
            return (_split_pinches(loop[i:j])
                    + _split_pinches(loop[:i] + loop[j:]))
        seen[p] = j
    return [loop]


def _drop_collinear2(pts: list) -> list:
    out = list(pts)
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
            abx, aby = b[0] - a[0], b[1] - a[1]
            bcx, bcy = c[0] - b[0], c[1] - b[1]
            la, lb = math.hypot(abx, aby), math.hypot(bcx, bcy)
            if la < 1e-12 or (abs(abx * bcy - aby * bcx) <= 1e-12 * max(
                    1.0, la * lb) and abx * bcx + aby * bcy > 0):
                out.pop(i)
                changed = True
                break
    return out


def _regions(loops: List[list]) -> List[Tuple[list, List[list]]]:
    """CCW loops -> pieces; CW loops -> holes of the smallest containing
    piece."""
    outers = [lp for lp in loops if signed_area(lp) > 0]
    holes = [lp for lp in loops if signed_area(lp) < 0]
    res = [(o, []) for o in outers]
    for h in holes:
        # a point just inside the hole (right of its first edge)
        a, b = h[0], h[1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        eps = min(1e-6, 0.05 * L)
        p = ((a[0] + b[0]) / 2.0 + eps * dy / L,
             (a[1] + b[1]) / 2.0 - eps * dx / L)
        host = None
        for k, (o, _) in enumerate(res):
            if point_in(p, o) and (host is None or signed_area(o)
                                   < signed_area(res[host][0])):
                host = k
        if host is None:
            raise ValueError("an opening loop has no host piece")
        res[host][1].append(h)
    return res


def subtract(cell: Sequence[Vec2], holes: Sequence[Sequence[Vec2]]
             ) -> List[Tuple[list, List[list]]]:
    """``cell`` minus every opening -> ``[(outer, [holes...]), ...]``
    (outer CCW, holes CW)."""
    cell = [tuple(p) for p in cell]
    if signed_area(cell) < 0:
        cell = cell[::-1]
    hs = [[tuple(p) for p in h] for h in holes]

    def inside(p):
        return point_in(p, cell) and not any(point_in(p, h) for h in hs)

    rel = [h for h in hs if _bbox_overlap(h, cell)]
    if not rel:
        return [(list(cell), [])]
    return _regions(_boundary_loops([cell] + rel, inside))


def union(polys: Sequence[Sequence[Vec2]],
          holes: Sequence[Sequence[Vec2]] = ()
          ) -> List[Tuple[list, List[list]]]:
    """Union of polygons minus ``holes`` -> ``[(outer, [holes...])]``."""
    ps = []
    for pg in polys:
        pg = [tuple(p) for p in pg]
        ps.append(pg[::-1] if signed_area(pg) < 0 else pg)
    hs = [[tuple(p) for p in h] for h in holes]

    def inside(p):
        return (any(point_in(p, pg) for pg in ps)
                and not any(point_in(p, h) for h in hs))

    return _regions(_boundary_loops(ps + hs, inside))


def _bbox_overlap(a, b) -> bool:
    ax0, ax1 = min(p[0] for p in a), max(p[0] for p in a)
    ay0, ay1 = min(p[1] for p in a), max(p[1] for p in a)
    bx0, bx1 = min(p[0] for p in b), max(p[0] for p in b)
    by0, by1 = min(p[1] for p in b), max(p[1] for p in b)
    e = 1e-9
    return not (ax1 < bx0 - e or bx1 < ax0 - e or ay1 < by0 - e
                or by1 < ay0 - e)


def net_area2(regions) -> float:
    return sum(signed_area(o) - sum(abs(signed_area(h)) for h in hs)
               for o, hs in regions)


# --------------------------------------------------------------------------- #
# shell dict <-> absolute opening polygons
# --------------------------------------------------------------------------- #
def _region(sd: dict, openings=None):
    from .model import Opening, ShellRegion
    ops = []
    for o in (sd.get("openings") if openings is None else openings) or []:
        poly = o.get("polygon")
        ops.append(Opening(float(o.get("u0", 0.0)), float(o.get("v0", 0.0)),
                           float(o.get("u1", 1.0)), float(o.get("v1", 1.0)),
                           polygon=([tuple(float(v) for v in p) for p in poly]
                                    if poly else None)))
    return ShellRegion(uid=str(sd.get("uid", "")), kind=sd.get("kind", "slab"),
                       behavior=sd.get("behavior", "shell"),
                       section=sd.get("section", ""),
                       corners=[tuple(float(v) for v in p)
                                for p in sd["corners"]], openings=ops)


def holes3(sd: dict) -> List[List[List[float]]]:
    """Every opening of shell dict ``sd`` as an absolute 3D polygon."""
    if not sd.get("openings"):
        return []
    from .polymesh import opening_polygons3
    return [[[float(v) for v in p] for p in h]
            for h in opening_polygons3(_region(sd))]


def _parallelogram(c) -> bool:
    if len(c) != 4:
        return False
    s = max(1.0, max(abs(v) for p in c for v in p))
    return all(abs(c[0][k] + c[2][k] - c[1][k] - c[3][k]) <= 1e-9 * s
               for k in range(3))


def _param_rect(corners, hole) -> Optional[dict]:
    """``{u0, v0, u1, v1}`` when ``hole`` is a rectangle aligned with the
    parametric directions of the parallelogram ``corners``."""
    if len(hole) != 4 or not _parallelogram(corners):
        return None
    c0 = [float(v) for v in corners[0]]
    a = _sub(corners[1], c0)
    b = _sub(corners[3], c0)
    aa, bb, ab = _dot(a, a), _dot(b, b), _dot(a, b)
    det = aa * bb - ab * ab
    if det <= 1e-24:
        return None
    uv = []
    for p in hole:
        d = _sub(p, c0)
        da, db = _dot(d, a), _dot(d, b)
        u = (da * bb - db * ab) / det
        v = (db * aa - da * ab) / det
        uv.append((u, v))
    us = sorted({round(u, 9) for u, _ in uv})
    vs = sorted({round(v, 9) for _, v in uv})
    if len(us) != 2 or len(vs) != 2:
        return None
    corners_uv = {(round(u, 9), round(v, 9)) for u, v in uv}
    if corners_uv != {(us[0], vs[0]), (us[1], vs[0]), (us[1], vs[1]),
                      (us[0], vs[1])}:
        return None
    u0 = min(u for u, _ in uv)
    u1 = max(u for u, _ in uv)
    v0 = min(v for _, v in uv)
    v1 = max(v for _, v in uv)
    if not (-1e-12 <= u0 < u1 <= 1 + 1e-12 and -1e-12 <= v0 < v1
            <= 1 + 1e-12):
        return None
    def cl(x):          # snap float noise at the parametric ends
        if abs(x) <= 1e-12:
            return 0.0
        if abs(x - 1.0) <= 1e-12:
            return 1.0
        return float(x)

    return {"u0": cl(u0), "v0": cl(v0), "u1": cl(u1), "v1": cl(v1)}


def openings_for(corners, holes) -> List[dict]:
    """Opening records for absolute hole polygons on a shell outline."""
    out = []
    for h in holes:
        rect = _param_rect(corners, h)
        if rect is not None:
            out.append(rect)
        else:
            out.append({"u0": 0.0, "v0": 0.0, "u1": 1.0, "v1": 1.0,
                        "polygon": [_clean(p) for p in h]})
    return out


def check_inside(sd: dict, what: str) -> None:
    """ValueError when an opening of ``sd`` leaves its outline."""
    if not sd.get("openings"):
        return
    from .polymesh import validate_polygon_region
    polys = [{"u0": 0.0, "v0": 0.0, "u1": 1.0, "v1": 1.0, "polygon": h}
             for h in holes3(sd)]
    try:
        validate_polygon_region(_region(sd, polys))
    except ValueError as exc:
        raise ValueError(f"{what}: shell {sd.get('uid')}: the openings must "
                         f"stay inside the shell ({exc})") from None


# --------------------------------------------------------------------------- #
# edit-op entry points (hooks in core/edit_shells.py)
# --------------------------------------------------------------------------- #
def replace_with_openings(ctx, s: dict, cells: Sequence[Sequence]) -> List[str]:
    """Divide / chop: replace shell ``s`` by ``cells`` (convex 3D pieces of
    its outline) minus its openings; returns the piece uids."""
    from . import edit_shells as _es
    fr = Frame(s["corners"])
    hs2 = [[fr.to2(p) for p in h] for h in holes3(s)]
    pieces3, holes_of = [], []
    for cell in cells:
        c2 = [fr.to2(p) for p in cell]
        for outer, hs in subtract(c2, hs2):
            if abs(signed_area(outer)) <= 1e-12:
                continue
            pieces3.append([_clean(fr.to3(q)) for q in outer])
            holes_of.append([[_clean(fr.to3(q)) for q in h] for h in hs])
    if not pieces3:
        raise ValueError(f"shell {s['uid']}: nothing left outside the "
                         "openings")
    uids = _es._replace_pieces(ctx, s, pieces3)
    for uid, hs in zip(uids, holes_of):
        sh = ctx.shell(uid)
        sh["openings"] = openings_for(sh["corners"], hs)
    return uids


def merge_with_openings(shells: Sequence[dict], polys: Sequence[Sequence]
                        ) -> Tuple[List[list], List[list]]:
    """Merge: ``(outline3, holes3)`` of the union of the coplanar shells
    (``polys`` = their outlines, all oriented like the first) minus their
    openings."""
    fr = Frame(polys[0])
    p2 = [[fr.to2(p) for p in pg] for pg in polys]
    own = [[fr.to2(p) for p in h] for s in shells for h in holes3(s)]
    regs = union(p2, own)
    if len(regs) != 1:
        raise ValueError("shell_merge: the shells do not form one region "
                         "(they must share edges)")
    want = sum(abs(signed_area(pg)) for pg in p2) - sum(
        abs(signed_area(h)) for h in own)
    got = net_area2(regs)
    if abs(got - want) > 1e-9 * max(1.0, want):
        raise ValueError("shell_merge: shells overlap")
    outer, hs = regs[0]
    return ([_clean(fr.to3(q)) for q in outer],
            [[_clean(fr.to3(q)) for q in h] for h in hs])


def project_on_plane(s: dict, pts) -> List[List[float]]:
    """Reshaper ``on_plane``: orthogonal projection onto the shell plane."""
    fr = Frame(s["corners"])
    return [fr.project(p) for p in pts]
