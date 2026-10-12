"""Exact cross-section properties by finite elements, numpy only (v1.22).

An in-house replacement for the optional ``sectionproperties`` package:
the Saint-Venant warping / shear-function analysis of an arbitrary
composite cross-section on a mesh of 6-node quadratic triangles.  It needs
NOTHING beyond numpy (the app's runtime dependencies stay openseespy,
numpy and flask).

Input
-----
``regions``: a list of ``{"outer": [[x, y], ...], "holes": [[[x, y],
...], ...], "n": float}``.  A point belongs to a region when it lies
inside ``outer`` and inside none of its ``holes``; its weight is the SUM
of ``n`` over the regions containing it (``n = E_i / E_ref``, the modular
ratio; overlapping regions add up exactly like the closed-form shoelace
sums of the callers).  Loops may touch, share edges or overlap in any way:
every boundary edge is split at every crossing / T-junction (the planar
arrangement) before meshing, so the mesh conforms to all of them.
Circles enter as polygons (:func:`circle_loop`, equal-AREA radius so the
area is exact).  Coordinates are any consistent length unit.

Mesh
----
A constrained Delaunay triangulation written in pure Python (incremental
Lawson-flip insertion inside a bounding box, constraint segments
recovered by midpoint insertion, faces classified by flood fill between
constrained edges), refined Ruppert-style: triangles larger than
``A_total / target`` or with a minimum angle below ~25 deg get their
circumcentre inserted, boundary segments whose diametral circle that
point encroaches are split instead.  Every triangle then gets its three
edge mid-nodes (Tri6, straight sides).

Solve
-----
Six-point (degree 4) Gauss quadrature, modulus-weighted exactly like
sectionproperties (Pilkey, "Analysis and Design of Elastic Beams", 2002):

* warping function ``omega``: ``K omega = f``, ``K = sum n int grad N .
  grad N^T``, ``f = sum n int grad N . (y, -x)``; ``J = Ixx + Iyy -
  omega . f`` (centroidal, transformed);
* shear functions ``Psi`` / ``Phi`` (same K): the Pilkey load vectors
  with Poisson's ratio ``nu``; shear-deformation coefficients ``kappa``;
  shear areas ``As_x = Delta_s^2 / kappa_x``, ``As_y = Delta_s^2 /
  kappa_y`` with ``Delta_s = 2 (1 + nu) (Ixx Iyy - Ixy^2)``;
* shear centre: elasticity method ``x_s = (nu/2 sc_x - f . Phi) /
  Delta_s``, ``y_s = (nu/2 sc_y + f . Psi) / Delta_s`` and the Trefftz
  values ``x_t = (Ixy I_xw - Iyy I_yw) / (Ixx Iyy - Ixy^2)``, ``y_t =
  (Ixx I_xw - Ixy I_yw) / (Ixx Iyy - Ixy^2)``;
* warping constant ``Cw = I_w - Q_w^2 / A - y_t I_xw + x_t I_yw``
  (warping about the Trefftz shear centre, zero-mean);
* A, centroid, Ixx (= int y^2), Iyy (= int x^2), Ixy, principal values
  and angle, elastic moduli (extreme mesh nodes) and plastic moduli (exact
  clipping of the straight triangles at the equal-area axis).

The singular Neumann problems are made definite by pinning one node per
connected mesh part (all right-hand sides are self-equilibrated, so the
pin only fixes the free constant).  The system is reordered by reverse
Cuthill-McKee and factorised ONCE by a block-banded Cholesky
(numpy.linalg.cholesky on bandwidth-sized blocks); the three right-hand
sides reuse the factor.  Sections made of several DISCONNECTED parts get
J (the sum of the parts) but no shear functions: the shear centre, shear
areas and Cw are returned as ``None`` with a warning.

Results are cached (LRU, 256 entries) by a SHA-1 hash of the rounded
input geometry and options.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = ["solve", "MeshError", "circle_loop", "rect_loop", "arc_points",
           "mesh_regions", "shape_regions", "cache_clear", "cache_info",
           "DEFAULT_TARGET", "CIRCLE_SEGMENTS"]

DEFAULT_TARGET = 600           # target number of Tri6 elements
CIRCLE_SEGMENTS = 96           # polygon sides of a full circle
FILLET_SEGMENTS = 8            # sides of a quarter-circle root fillet
_MIN_ANGLE_DEG = 25.0
_B_RATIO = 1.0 / (2.0 * math.sin(math.radians(_MIN_ANGLE_DEG)))
_EPS = 1e-13                   # orientation tolerance (normalised units)
_SNAP = 1e-9                   # point merge tolerance (normalised units)


class MeshError(ValueError):
    """The geometry could not be meshed (degenerate / invalid input)."""


# --------------------------------------------------------------------------- #
# small geometry helpers (public: used by builtup / designer / shape_props)
# --------------------------------------------------------------------------- #
def rect_loop(x0: float, x1: float, y0: float, y1: float) -> List[list]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def circle_loop(cx: float, cy: float, r: float,
                n: int = CIRCLE_SEGMENTS) -> List[list]:
    """Regular ``n``-gon with the circle's AREA (radius scaled by
    ``sqrt(2 pi / (n sin(2 pi / n)))``)."""
    re = r * math.sqrt(2.0 * math.pi / (n * math.sin(2.0 * math.pi / n)))
    return [[cx + re * math.cos(2 * math.pi * k / n),
             cy + re * math.sin(2 * math.pi * k / n)] for k in range(n)]


def arc_points(cx: float, cy: float, r: float, a0: float, a1: float,
               n: int) -> List[list]:
    """``n + 1`` points on the arc from angle ``a0`` to ``a1`` (rad)."""
    return [[cx + r * math.cos(a0 + (a1 - a0) * k / n),
             cy + r * math.sin(a0 + (a1 - a0) * k / n)] for k in range(n + 1)]


def _fillet_patch(corner, u1, u2, r, n=FILLET_SEGMENTS) -> List[list]:
    """Curvilinear triangle filling the re-entrant corner between two wall
    faces leaving ``corner`` along the unit directions ``u1`` / ``u2``."""
    cx, cy = corner
    ox, oy = cx + r * (u1[0] + u2[0]), cy + r * (u1[1] + u2[1])
    a0 = math.atan2(-u2[1], -u2[0])           # centre -> tangent point on u1
    a1 = math.atan2(-u1[1], -u1[0])           # centre -> tangent point on u2
    d = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi   # shorter way round
    pts = [[cx, cy]] + arc_points(ox, oy, r, a0, a0 + d, n)
    return pts


def shape_regions(typ: str, dims: dict, n: float = 1.0,
                  fillets: bool = True) -> List[dict]:
    """Regions of a parametric shape in its BOUNDING-BOX-CENTRED frame
    (x = width, y = depth; channel web on the left, angle heel at the
    bottom-left, tee flange on top - the built-up / shape_props
    orientation).  Rolled-shape root fillets ``r`` (I, channel, tee,
    angle) are added as curvilinear patches when ``fillets``."""
    g = {k: float(v) for k, v in dims.items()
         if isinstance(v, (int, float)) and not isinstance(v, bool)}
    R = []

    def reg(loop, holes=()):
        R.append({"outer": loop, "holes": [h for h in holes], "n": n})
    rr = g.get("r", 0.0) if fillets else 0.0
    if typ in ("rect_solid", "plate") and "d" in g:
        b, h = g["b"], g["d"]
        reg(rect_loop(-b / 2, b / 2, -h / 2, h / 2))
    elif typ == "plate":
        b, t = g["b"], g["t"]
        reg(rect_loop(-b / 2, b / 2, -t / 2, t / 2))
    elif typ == "round_solid":
        reg(circle_loop(0.0, 0.0, g["D"] / 2))
    elif typ == "round_tube":
        Ro = g["D"] / 2
        reg(circle_loop(0.0, 0.0, Ro), [circle_loop(0.0, 0.0, Ro - g["t"])])
    elif typ == "rect_tube":
        b, h, t = g["b"], g["d"], g["t"]
        ro = g.get("r_out", 0.0)
        reg(_rounded_rect(b, h, ro), [_rounded_rect(b - 2 * t, h - 2 * t,
                                                    max(ro - t, 0.0))])
    elif typ == "I":
        h, bf, tf, tw = g["d"], g["bf"], g["tf"], g["tw"]
        reg(rect_loop(-bf / 2, bf / 2, h / 2 - tf, h / 2))
        reg(rect_loop(-bf / 2, bf / 2, -h / 2, -h / 2 + tf))
        reg(rect_loop(-tw / 2, tw / 2, -h / 2 + tf, h / 2 - tf))
        r = min(rr, (bf - tw) / 2 * 0.95, (h / 2 - tf) * 0.95)
        if r > 0:
            for sx in (1, -1):
                for sy in (1, -1):
                    reg(_fillet_patch((sx * tw / 2, sy * (h / 2 - tf)),
                                      (sx, 0.0), (0.0, -sy), r))
    elif typ == "I_mono":
        h, tw = g["d"], g["tw"]
        bt, tt, bb, tb = g["bf_top"], g["tf_top"], g["bf_bot"], g["tf_bot"]
        reg(rect_loop(-bt / 2, bt / 2, h / 2 - tt, h / 2))
        reg(rect_loop(-bb / 2, bb / 2, -h / 2, -h / 2 + tb))
        reg(rect_loop(-tw / 2, tw / 2, -h / 2 + tb, h / 2 - tt))
    elif typ == "tee":
        h, bf, tf, tw = g["d"], g["bf"], g["tf"], g["tw"]
        reg(rect_loop(-bf / 2, bf / 2, h / 2 - tf, h / 2))
        reg(rect_loop(-tw / 2, tw / 2, -h / 2, h / 2 - tf))
        r = min(rr, (bf - tw) / 2 * 0.95, (h - tf) * 0.95)
        if r > 0:
            for sx in (1, -1):
                reg(_fillet_patch((sx * tw / 2, h / 2 - tf), (sx, 0.0),
                                  (0.0, -1.0), r))
    elif typ == "channel":
        h, bf, tf, tw = g["d"], g["bf"], g["tf"], g["tw"]
        x0 = -bf / 2
        reg(rect_loop(x0, x0 + tw, -h / 2, h / 2))
        reg(rect_loop(x0 + tw, bf / 2, h / 2 - tf, h / 2))
        reg(rect_loop(x0 + tw, bf / 2, -h / 2, -h / 2 + tf))
        r = min(rr, (bf - tw) * 0.95, (h / 2 - tf) * 0.95)
        if r > 0:
            for sy in (1, -1):
                reg(_fillet_patch((x0 + tw, sy * (h / 2 - tf)), (1.0, 0.0),
                                  (0.0, -sy), r))
    elif typ == "angle":
        h, b, t = g["d"], g["b"], g["t"]
        reg(rect_loop(-b / 2, -b / 2 + t, -h / 2, h / 2))
        reg(rect_loop(-b / 2 + t, b / 2, -h / 2, -h / 2 + t))
        r = min(rr, (b - t) * 0.95, (h - t) * 0.95)
        if r > 0:
            reg(_fillet_patch((-b / 2 + t, -h / 2 + t), (1.0, 0.0),
                              (0.0, 1.0), r))
    else:
        raise ValueError(f"shape_regions: unsupported type {typ!r}")
    return R


def _rounded_rect(b: float, h: float, r: float) -> List[list]:
    if r <= 1e-15 * max(b, h):
        return rect_loop(-b / 2, b / 2, -h / 2, h / 2)
    r = min(r, b / 2, h / 2)
    # equal-area polygonal quarter arcs
    m = max(4, CIRCLE_SEGMENTS // 4)
    re = r * math.sqrt(2.0 * math.pi / (4 * m * math.sin(2 * math.pi
                                                         / (4 * m))))
    pts: List[list] = []
    for (cx, cy, a0) in ((b / 2 - r, -h / 2 + r, -math.pi / 2),
                         (b / 2 - r, h / 2 - r, 0.0),
                         (-b / 2 + r, h / 2 - r, math.pi / 2),
                         (-b / 2 + r, -h / 2 + r, math.pi)):
        arc = arc_points(cx, cy, re, a0, a0 + math.pi / 2, m)
        # keep the straight sides tangent: endpoints on the true radius
        arc[0] = [cx + r * math.cos(a0), cy + r * math.sin(a0)]
        arc[-1] = [cx + r * math.cos(a0 + math.pi / 2),
                   cy + r * math.sin(a0 + math.pi / 2)]
        for p in arc:
            if not pts or math.dist(pts[-1], p) > 1e-12 * max(b, h):
                pts.append(p)
    if math.dist(pts[0], pts[-1]) <= 1e-12 * max(b, h):
        pts.pop()
    return pts


# --------------------------------------------------------------------------- #
# input normalisation + planar arrangement
# --------------------------------------------------------------------------- #
def _loop_area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _clean_loop(pts, what: str) -> np.ndarray:
    try:
        a = np.asarray(pts, dtype=float)
    except (TypeError, ValueError):
        raise MeshError(f"{what}: vertices must be [x, y] number pairs") \
            from None
    if a.ndim != 2 or a.shape[1] != 2 or a.shape[0] < 3 \
            or not np.all(np.isfinite(a)):
        raise MeshError(f"{what}: needs >= 3 finite [x, y] vertices")
    keep = [0]
    for i in range(1, len(a)):
        if np.hypot(*(a[i] - a[keep[-1]])) > 0.0:
            keep.append(i)
    a = a[keep]
    if len(a) > 1 and np.hypot(*(a[0] - a[-1])) == 0.0:
        a = a[:-1]
    if len(a) < 3 or abs(_loop_area(a)) <= 0.0:
        raise MeshError(f"{what}: zero area")
    return a


def _normalise(regions) -> List[dict]:
    if not isinstance(regions, (list, tuple)) or not regions:
        raise MeshError("section has no regions")
    out = []
    for i, r in enumerate(regions):
        if not isinstance(r, dict):
            raise MeshError(f"region {i + 1} must be an object")
        n = r.get("n", 1.0)
        if isinstance(n, bool) or not isinstance(n, (int, float)) \
                or not math.isfinite(n) or n <= 0:
            raise MeshError(f"region {i + 1}: n must be a number > 0")
        outer = _clean_loop(r.get("outer"), f"region {i + 1}")
        holes = [_clean_loop(h, f"region {i + 1} hole")
                 for h in (r.get("holes") or [])]
        out.append({"outer": outer, "holes": holes, "n": float(n)})
    return out


def _arrangement(loops: List[np.ndarray], tol: float
                 ) -> Tuple[np.ndarray, List[Tuple[int, int]]]:
    """Points + non-crossing segments of the union of all loop edges."""
    A = np.concatenate([lp for lp in loops])
    B = np.concatenate([np.roll(lp, -1, axis=0) for lp in loops])
    m = len(A)
    D = B - A
    L2 = np.einsum("ij,ij->i", D, D)
    params: List[List[float]] = [[0.0, 1.0] for _ in range(m)]
    ends = np.concatenate([A, B])
    # 1. proper crossings (chunked pairwise)
    CH = 256
    for i0 in range(0, m, CH):
        p, r = A[i0:i0 + CH, None, :], D[i0:i0 + CH, None, :]
        q, s = A[None, :, :], D[None, :, :]
        den = r[..., 0] * s[..., 1] - r[..., 1] * s[..., 0]
        qp = q - p
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (qp[..., 0] * s[..., 1] - qp[..., 1] * s[..., 0]) / den
            u = (qp[..., 0] * r[..., 1] - qp[..., 1] * r[..., 0]) / den
        big = np.abs(den) > 1e-14
        ok = big & (t > 1e-12) & (t < 1 - 1e-12) & (u > 1e-12) \
            & (u < 1 - 1e-12)
        ii, jj = np.nonzero(ok)
        for a_, b_ in zip(ii, jj):
            params[i0 + a_].append(float(t[a_, b_]))
        # 2. T-junctions / collinear overlaps: endpoints on segment interiors
        e = ends[None, :, :] - A[i0:i0 + CH, None, :]
        Lc = L2[i0:i0 + CH, None]
        with np.errstate(divide="ignore", invalid="ignore"):
            tp = (e[..., 0] * r[..., 0] + e[..., 1] * r[..., 1]) / Lc
        cr = e[..., 0] * r[..., 1] - e[..., 1] * r[..., 0]
        dist = np.abs(cr) / np.sqrt(Lc)
        Lr = np.sqrt(Lc)
        on = (dist < tol) & (tp * Lr > tol) & ((1 - tp) * Lr > tol)
        ii, jj = np.nonzero(on)
        for a_, b_ in zip(ii, jj):
            params[i0 + a_].append(float(tp[a_, b_]))
    # 3. split + merge points on a snap grid
    key2id: Dict[Tuple[int, int], int] = {}
    pts: List[Tuple[float, float]] = []

    def pid(x: float, y: float) -> int:
        k = (int(round(x / tol)), int(round(y / tol)))
        v = key2id.get(k)
        if v is None:
            # neighbouring cells (rounding boundary)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    v = key2id.get((k[0] + dx, k[1] + dy))
                    if v is not None:
                        break
                if v is not None:
                    break
        if v is None:
            v = len(pts)
            pts.append((x, y))
            key2id[k] = v
        return v
    # register original endpoints first (so they keep exact coordinates)
    for x, y in A:
        pid(float(x), float(y))
    segs = set()
    for i in range(m):
        ts = sorted(set(params[i]))
        ids = [pid(float(A[i, 0] + t * D[i, 0]), float(A[i, 1] + t * D[i, 1]))
               for t in ts]
        for a_, b_ in zip(ids, ids[1:]):
            if a_ != b_:
                segs.add((min(a_, b_), max(a_, b_)))
    return np.array(pts, dtype=float), sorted(segs)


def _point_in_loop(px: float, py: float, lp: np.ndarray) -> bool:
    x, y = lp[:, 0], lp[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    cond = (y > py) != (y2 > py)
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = x + (py - y) * (x2 - x) / (y2 - y)
    return bool(np.count_nonzero(cond & (px < xc)) % 2)


def _weight_at(px: float, py: float, regs: List[dict]) -> float:
    w = 0.0
    for r in regs:
        if _point_in_loop(px, py, r["outer"]) and not any(
                _point_in_loop(px, py, h) for h in r["holes"]):
            w += r["n"]
    return w


# --------------------------------------------------------------------------- #
# constrained Delaunay triangulation + Ruppert refinement (pure Python)
# --------------------------------------------------------------------------- #
def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _incircle(a, b, c, d) -> float:
    adx, ady = a[0] - d[0], a[1] - d[1]
    bdx, bdy = b[0] - d[0], b[1] - d[1]
    cdx, cdy = c[0] - d[0], c[1] - d[1]
    ad = adx * adx + ady * ady
    bd = bdx * bdx + bdy * bdy
    cd = cdx * cdx + cdy * cdy
    return (adx * (bdy * cd - bd * cdy) - ady * (bdx * cd - bd * cdx)
            + ad * (bdx * cdy - bdy * cdx))


class _CDT:
    """Triangle soup with adjacency: T[t] = [a, b, c] (CCW), N[t][i] =
    neighbour across the edge opposite T[t][i], C[t][i] its constraint
    flag, W[t] the face weight (None until classified)."""

    def __init__(self, lo, hi):
        span = max(hi[0] - lo[0], hi[1] - lo[1], 1e-30)
        cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
        R = 4.0 * span
        self.P: List[Tuple[float, float]] = [
            (cx - R, cy - R), (cx + R, cy - R), (cx + R, cy + R),
            (cx - R, cy + R)]
        self.T = [[0, 1, 2], [0, 2, 3]]
        self.N = [[-1, 1, -1], [-1, -1, 0]]
        self.C = [[False] * 3, [False] * 3]
        self.W: List[Optional[float]] = [None, None]
        self.ver = [0, 0]
        self.vt = [0, 0, 0, 1]
        self.last = 0
        self.seg: Dict[Tuple[int, int], bool] = {}
        self.segv = 0
        self.new_tris: List[int] = []

    # ---- low-level helpers ----
    def _touch(self, t):
        self.ver[t] += 1
        for v in self.T[t]:
            self.vt[v] = t
        self.new_tris.append(t)

    def _new(self, tri, nb, cs, w) -> int:
        self.T.append(tri)
        self.N.append(nb)
        self.C.append(cs)
        self.W.append(w)
        self.ver.append(0)
        return len(self.T) - 1

    def _link(self, u, p, q, t):
        """Neighbour u (across edge p-q) now points back to t."""
        if u < 0:
            return
        Tu = self.T[u]
        for k in range(3):
            if Tu[k] != p and Tu[k] != q:
                self.N[u][k] = t
                return

    def _opp(self, u, p, q) -> int:
        Tu = self.T[u]
        for k in range(3):
            if Tu[k] != p and Tu[k] != q:
                return k
        raise MeshError("corrupt triangulation")

    # ---- point location ----
    def locate(self, p, t=None):
        """(t, k): p inside triangle t (k = -1) or on its edge k."""
        P, T, N = self.P, self.T, self.N
        t = self.last if t is None else t
        steps = 0
        nT = len(T)
        while True:
            steps += 1
            if steps > 4 * nT + 100:
                return self._locate_brute(p)
            tri = T[t]
            rot = steps % 3
            moved = False
            for kk in range(3):
                k = (kk + rot) % 3
                a, b = P[tri[(k + 1) % 3]], P[tri[(k + 2) % 3]]
                if _orient(a, b, p) < -_EPS:
                    nt = N[t][k]
                    if nt < 0:
                        raise MeshError("point outside the triangulation")
                    t = nt
                    moved = True
                    break
            if not moved:
                break
        self.last = t
        tri = T[t]
        for k in range(3):
            a, b = P[tri[(k + 1) % 3]], P[tri[(k + 2) % 3]]
            if abs(_orient(a, b, p)) <= _EPS:
                return t, k
        return t, -1

    def _locate_brute(self, p):
        P = self.P
        for t, tri in enumerate(self.T):
            a, b, c = P[tri[0]], P[tri[1]], P[tri[2]]
            if _orient(a, b, p) >= -_EPS and _orient(b, c, p) >= -_EPS \
                    and _orient(c, a, p) >= -_EPS:
                self.last = t
                for k in range(3):
                    if abs(_orient(P[tri[(k + 1) % 3]], P[tri[(k + 2) % 3]],
                                   p)) <= _EPS:
                        return t, k
                return t, -1
        raise MeshError("point location failed")

    # ---- insertion ----
    def insert(self, p, t_hint=None) -> int:
        t, k = self.locate(p, t_hint)
        tri = self.T[t]
        for v in tri:
            q = self.P[v]
            if abs(q[0] - p[0]) <= _SNAP and abs(q[1] - p[1]) <= _SNAP:
                return v
        if k >= 0:
            return self.split_edge(t, k, p)
        return self._insert_in(t, p)

    def _insert_in(self, t, p) -> int:
        v = len(self.P)
        self.P.append((p[0], p[1]))
        self.vt.append(t)
        a, b, c = self.T[t]
        na, nb, nc = self.N[t]
        ca, cb, cc = self.C[t]
        w = self.W[t]
        t0 = t
        t1 = self._new([b, c, v], [-1, -1, na], [False, False, ca], w)
        t2 = self._new([c, a, v], [-1, -1, nb], [False, False, cb], w)
        self.T[t0] = [a, b, v]
        self.N[t0] = [t1, t2, nc]
        self.C[t0] = [False, False, cc]
        self.N[t1][0], self.N[t1][1] = t2, t0
        self.N[t2][0], self.N[t2][1] = t0, t1
        self._link(na, b, c, t1)
        self._link(nb, c, a, t2)
        for x in (t0, t1, t2):
            self._touch(x)
        self.vt[v] = t0
        for x in (t0, t1, t2):
            self._legalize(x, 2)
        return v

    def split_edge(self, t, k, p) -> int:
        """Insert p on the edge opposite T[t][k] (constraint inherited)."""
        v = len(self.P)
        self.P.append((p[0], p[1]))
        self.vt.append(t)
        T = self.T
        a = T[t][k]
        b, c = T[t][(k + 1) % 3], T[t][(k + 2) % 3]
        u = self.N[t][k]
        con = self.C[t][k]
        nt_c = self.N[t][(k + 2) % 3]       # across a-b (opposite c)
        nt_b = self.N[t][(k + 1) % 3]       # across c-a (opposite b)
        ct_c = self.C[t][(k + 2) % 3]
        ct_b = self.C[t][(k + 1) % 3]
        wt = self.W[t]
        if con:
            self.segv += 1
            key = (min(b, c), max(b, c))
            self.seg.pop(key, None)
            self.seg[(min(b, v), max(b, v))] = True
            self.seg[(min(v, c), max(v, c))] = True
        # t side
        t0 = t
        T[t0] = [a, b, v]
        t1 = self._new([a, v, c], [-1, nt_b, t0], [con, ct_b, False], wt)
        self.N[t0] = [-1, t1, nt_c]
        self.C[t0] = [con, False, ct_c]
        self._link(nt_b, c, a, t1)
        if u >= 0:
            j = self._opp(u, b, c)
            d = T[u][j]
            nu_b = self.N[u][(j + 2) % 3]   # u = (d, c, b): opposite b: d-c
            nu_c = self.N[u][(j + 1) % 3]   # opposite c: b-d
            cu_b = self.C[u][(j + 2) % 3]
            cu_c = self.C[u][(j + 1) % 3]
            wu = self.W[u]
            u0 = u
            T[u0] = [d, c, v]
            u1 = self._new([d, v, b], [t0, nu_c, u0], [con, cu_c, False], wu)
            self.N[u0] = [t1, u1, nu_b]
            self.C[u0] = [con, False, cu_b]
            self._link(nu_c, b, d, u1)
            self.N[t0][0] = u1
            self.N[t1][0] = u0
            news = (t0, t1, u0, u1)
        else:
            news = (t0, t1)
        for x in news:
            self._touch(x)
        self.vt[v] = t0
        self._legalize(t0, 2)
        self._legalize(t1, 1)
        if u >= 0:
            self._legalize(u0, 2)
            self._legalize(u1, 1)
        return v

    def _legalize(self, t, i):
        stack = [(t, i)]
        P, T, N, C = self.P, self.T, self.N, self.C
        while stack:
            t, i = stack.pop()
            if C[t][i]:
                continue
            u = N[t][i]
            if u < 0:
                continue
            p = T[t][i]
            q, r = T[t][(i + 1) % 3], T[t][(i + 2) % 3]
            if p == q or p == r:
                continue
            j = self._opp(u, q, r)
            d = T[u][j]
            if _incircle(P[p], P[q], P[r], P[d]) <= _EPS * 1e-1:
                continue
            # convexity guard (degenerate quads)
            if _orient(P[p], P[q], P[d]) <= 0 or _orient(P[p], P[d], P[r]) <= 0:
                continue
            n_t_q, n_t_r = N[t][(i + 1) % 3], N[t][(i + 2) % 3]
            c_t_q, c_t_r = C[t][(i + 1) % 3], C[t][(i + 2) % 3]
            n_u_r, n_u_q = N[u][(j + 1) % 3], N[u][(j + 2) % 3]
            c_u_r, c_u_q = C[u][(j + 1) % 3], C[u][(j + 2) % 3]
            T[t] = [p, q, d]
            N[t] = [n_u_r, u, n_t_r]
            C[t] = [c_u_r, False, c_t_r]
            T[u] = [p, d, r]
            N[u] = [n_u_q, n_t_q, t]
            C[u] = [c_u_q, c_t_q, False]
            self._link(n_u_r, q, d, t)
            self._link(n_t_q, r, p, u)
            self._touch(t)
            self._touch(u)
            stack.append((t, 0))
            stack.append((u, 0))

    # ---- edges ----
    def find_edge(self, a, b):
        """(t, k) with edge a-b opposite T[t][k], or None."""
        t0 = self.vt[a]
        T, N = self.T, self.N
        if a not in T[t0]:
            for t, tri in enumerate(T):       # stale map (should not happen)
                if a in tri:
                    t0 = t
                    break
        t = t0
        for _direction in (1, 2):
            steps = 0
            while t >= 0 and steps < 1000:
                steps += 1
                tri = T[t]
                i = tri.index(a)
                if tri[(i + 1) % 3] == b:
                    return t, (i + 2) % 3
                if tri[(i + 2) % 3] == b:
                    return t, (i + 1) % 3
                t = N[t][(i + 1) % 3] if _direction == 1 \
                    else N[t][(i + 2) % 3]
                if t == t0:
                    return None
            t = t0
        return None

    def mark(self, a, b) -> bool:
        e = self.find_edge(a, b)
        if e is None:
            return False
        t, k = e
        self.C[t][k] = True
        u = self.N[t][k]
        if u >= 0:
            self.C[u][self._opp(u, a, b)] = True
        self.seg[(min(a, b), max(a, b))] = True
        self.segv += 1
        return True


def _triangulate(pts: np.ndarray, segs, regs, a_max: float, max_pts: int,
                 seg_min: float):
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    cdt = _CDT(lo, hi)
    vid = [cdt.insert((float(x), float(y))) for x, y in pts]
    # constraint recovery by midpoint insertion
    stack = [(vid[a], vid[b], 0) for a, b in segs]
    while stack:
        a, b, depth = stack.pop()
        if a == b:
            continue
        if cdt.mark(a, b):
            continue
        if depth > 40:
            raise MeshError("could not recover a boundary segment")
        pa, pb = cdt.P[a], cdt.P[b]
        m = cdt.insert(((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2),
                       cdt.vt[a])
        stack.append((a, m, depth + 1))
        stack.append((m, b, depth + 1))
    # face classification (flood fill between constrained edges)
    T, N, C, P = cdt.T, cdt.N, cdt.C, cdt.P
    for s in range(len(T)):
        if cdt.W[s] is not None:
            continue
        a, b, c = T[s]
        w = _weight_at((P[a][0] + P[b][0] + P[c][0]) / 3,
                       (P[a][1] + P[b][1] + P[c][1]) / 3, regs)
        cdt.W[s] = w
        stack = [s]
        while stack:
            t = stack.pop()
            for k in range(3):
                u = N[t][k]
                if u >= 0 and not C[t][k] and cdt.W[u] is None:
                    cdt.W[u] = w
                    stack.append(u)
    # Ruppert refinement
    skip = set()
    seg_cache = {"v": -1}

    def seg_arrays():
        if seg_cache["v"] != cdt.segv:
            keys = list(cdt.seg.keys())
            a = np.array([P[i] for i, _ in keys]).reshape(-1, 2)
            b = np.array([P[j] for _, j in keys]).reshape(-1, 2)
            seg_cache.update(keys=keys, mid=(a + b) / 2,
                             r2=np.einsum("ij,ij->i", b - a, b - a) / 4,
                             v=cdt.segv)
        return seg_cache

    def bad(t):
        a, b, c = T[t]
        pa, pb, pc = P[a], P[b], P[c]
        ar = 0.5 * _orient(pa, pb, pc)
        if ar <= 0:
            return False
        if ar > a_max:
            return True
        la = (pb[0] - pc[0]) ** 2 + (pb[1] - pc[1]) ** 2
        lb = (pa[0] - pc[0]) ** 2 + (pa[1] - pc[1]) ** 2
        lc = (pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2
        lmin = min(la, lb, lc)
        if lmin < seg_min * seg_min:
            return False
        R2 = la * lb * lc / (16.0 * ar * ar)
        return R2 > _B_RATIO * _B_RATIO * lmin

    def split_encroached(cands) -> List[int]:
        """Ruppert phase 1: split every subsegment whose diametral circle
        holds the apex of an adjacent triangle (returns new triangles)."""
        born: List[int] = []
        stack = list(cands)
        while stack and len(P) < max_pts:
            t = stack.pop()
            tri = T[t]
            for k in range(3):
                if not C[t][k]:
                    continue
                p = P[tri[k]]
                i, j = tri[(k + 1) % 3], tri[(k + 2) % 3]
                q, r = P[i], P[j]
                if (q[0] - p[0]) * (r[0] - p[0]) + (q[1] - p[1]) \
                        * (r[1] - p[1]) >= 0:
                    continue
                if (q[0] - r[0]) ** 2 + (q[1] - r[1]) ** 2 \
                        <= 4 * seg_min * seg_min:
                    continue
                cdt.new_tris = []
                cdt.split_edge(t, k, ((q[0] + r[0]) / 2, (q[1] + r[1]) / 2))
                news = set(cdt.new_tris)
                born.extend(news)
                stack.extend(news)
                break
        return born

    split_encroached(range(len(T)))
    queue = [(t, cdt.ver[t]) for t in range(len(T)) if cdt.W[t]]
    qi = 0
    while qi < len(queue):
        t, ver = queue[qi]
        qi += 1
        if cdt.ver[t] != ver or (t, ver) in skip or not cdt.W[t]:
            continue
        if len(P) >= max_pts:
            break
        if not bad(t):
            continue
        a, b, c = T[t]
        pa, pb, pc = P[a], P[b], P[c]
        d = 2.0 * _orient(pa, pb, pc)
        ax, ay = pa
        bx, by = pb[0] - ax, pb[1] - ay
        cx, cy = pc[0] - ax, pc[1] - ay
        b2, c2 = bx * bx + by * by, cx * cx + cy * cy
        ux = ax + (cy * b2 - by * c2) / d
        uy = ay + (bx * c2 - cx * b2) / d
        sa = seg_arrays()
        enc = []
        if len(sa["keys"]):
            dd = (sa["mid"][:, 0] - ux) ** 2 + (sa["mid"][:, 1] - uy) ** 2
            hit = np.nonzero(dd < sa["r2"] * (1 - 1e-9))[0]
            enc = [sa["keys"][h] for h in hit
                   if sa["r2"][h] > seg_min * seg_min]
            if len(hit) and not enc:
                skip.add((t, ver))
                continue
        born: List[int] = []
        if enc:
            for (i, j) in enc:
                e = cdt.find_edge(i, j)
                if e is None:
                    continue
                pi_, pj = P[i], P[j]
                cdt.new_tris = []
                cdt.split_edge(e[0], e[1], ((pi_[0] + pj[0]) / 2,
                                            (pi_[1] + pj[1]) / 2))
                born.extend(cdt.new_tris)
            queue.append((t, cdt.ver[t]))
        else:
            # never insert across a constraint / outside the material
            try:
                tl, k = cdt.locate((ux, uy), t)
            except MeshError:
                skip.add((t, ver))
                continue
            if not cdt.W[tl] or (k >= 0 and cdt.C[tl][k]) \
                    or _crosses_constraint(cdt, t, (ux, uy)):
                skip.add((t, ver))
                continue
            cdt.new_tris = []
            cdt.insert((ux, uy), tl)
            born.extend(cdt.new_tris)
        born.extend(split_encroached(set(born)))
        for x in set(born):
            if cdt.W[x]:
                queue.append((x, cdt.ver[x]))
        if qi > 4096 and qi > len(queue) // 2:
            queue = queue[qi:]
            qi = 0
    return cdt


def _crosses_constraint(cdt: _CDT, t: int, p) -> bool:
    """True when the straight walk from triangle t's centroid to p crosses
    a constrained edge."""
    P, T, N, C = cdt.P, cdt.T, cdt.N, cdt.C
    a, b, c = T[t]
    s = ((P[a][0] + P[b][0] + P[c][0]) / 3, (P[a][1] + P[b][1] + P[c][1]) / 3)
    prev = -1
    for _ in range(10000):
        tri = T[t]
        if _orient(P[tri[0]], P[tri[1]], p) >= -_EPS and \
                _orient(P[tri[1]], P[tri[2]], p) >= -_EPS and \
                _orient(P[tri[2]], P[tri[0]], p) >= -_EPS:
            return False
        moved = False
        for k in range(3):
            u = N[t][k]
            if u == prev:
                continue
            q, r = P[tri[(k + 1) % 3]], P[tri[(k + 2) % 3]]
            if _orient(q, r, p) < -_EPS:
                # segment s-p crosses edge q-r ?
                o1, o2 = _orient(s, p, q), _orient(s, p, r)
                if (o1 > 0) != (o2 > 0) or o1 == 0 or o2 == 0:
                    if C[t][k]:
                        return True
                    if u < 0:
                        return True
                    prev, t = t, u
                    moved = True
                    break
        if not moved:
            return False
    return True


def mesh_regions(regions, target: int = DEFAULT_TARGET,
                 max_area: Optional[float] = None) -> dict:
    """Mesh ``regions`` (see module docstring) into Tri6 elements.

    Returns ``{"nodes": (n, 2) array, "elems": (ne, 6) int array (corners
    CCW, then mid-nodes 1-2, 2-3, 3-1), "n": (ne,) weights}``.
    """
    regs = _normalise(regions)
    allp = np.concatenate([r["outer"] for r in regs]
                          + [h for r in regs for h in r["holes"]])
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    centre = (lo + hi) / 2
    scale = float(max(hi[0] - lo[0], hi[1] - lo[1]))
    if not scale > 0:
        raise MeshError("section has no extent")
    nregs = [{"outer": (r["outer"] - centre) / scale,
              "holes": [(h - centre) / scale for h in r["holes"]],
              "n": r["n"]} for r in regs]
    loops = [r["outer"] for r in nregs] + [h for r in nregs
                                           for h in r["holes"]]
    pts, segs = _arrangement(loops, _SNAP * 10)
    area = 0.0
    for r in nregs:
        area += abs(_loop_area(r["outer"])) - sum(abs(_loop_area(h))
                                                  for h in r["holes"])
    if area <= 0:
        raise MeshError("section has no area")
    a_max = (max_area / scale ** 2) if max_area else area / max(int(target),
                                                                10)
    max_pts = max(8 * int(target), 4000)
    cdt = _triangulate(pts, segs, nregs, a_max, max_pts, seg_min=2e-4)
    T, P, W = cdt.T, cdt.P, cdt.W
    keep = [t for t in range(len(T)) if W[t] and W[t] > 0
            and _orient(P[T[t][0]], P[T[t][1]], P[T[t][2]]) > 0]
    if not keep:
        raise MeshError("no material triangles")
    corners = np.array([T[t] for t in keep], dtype=np.int64)
    used, inv = np.unique(corners, return_inverse=True)
    corners = inv.reshape(-1, 3)
    xy = np.array([P[i] for i in used]) * scale + centre
    nv = len(used)
    e = np.concatenate([corners[:, [0, 1]], corners[:, [1, 2]],
                        corners[:, [2, 0]]])
    e.sort(axis=1)
    ue, einv = np.unique(e, axis=0, return_inverse=True)
    einv = einv.reshape(-1)
    ne = len(corners)
    mid = (xy[ue[:, 0]] + xy[ue[:, 1]]) / 2
    nodes = np.vstack([xy, mid])
    elems = np.empty((ne, 6), dtype=np.int64)
    elems[:, :3] = corners
    elems[:, 3] = nv + einv[:ne]
    elems[:, 4] = nv + einv[ne:2 * ne]
    elems[:, 5] = nv + einv[2 * ne:]
    return {"nodes": nodes, "elems": elems,
            "n": np.array([W[t] for t in keep], dtype=float)}


# --------------------------------------------------------------------------- #
# finite elements
# --------------------------------------------------------------------------- #
def _gauss6():
    g1 = 1.0 / 18 * (8 - math.sqrt(10) + math.sqrt(38 - 44 * math.sqrt(0.4)))
    g2 = 1.0 / 18 * (8 - math.sqrt(10) - math.sqrt(38 - 44 * math.sqrt(0.4)))
    w1 = (620 + math.sqrt(213125 - 53320 * math.sqrt(10))) / 3720
    w2 = (620 - math.sqrt(213125 - 53320 * math.sqrt(10))) / 3720
    L = np.array([[1 - 2 * g2, g2, g2], [g2, 1 - 2 * g2, g2],
                  [g2, g2, 1 - 2 * g2], [g1, g1, 1 - 2 * g1],
                  [1 - 2 * g1, g1, g1], [g1, 1 - 2 * g1, g1]])
    w = np.array([w2, w2, w2, w1, w1, w1])
    return L, w


_GL, _GW = _gauss6()


def _shape(L):
    l1, l2, l3 = L
    Nv = np.array([l1 * (2 * l1 - 1), l2 * (2 * l2 - 1), l3 * (2 * l3 - 1),
                   4 * l1 * l2, 4 * l2 * l3, 4 * l3 * l1])
    Cg = np.array([[4 * l1 - 1, 0, 0], [0, 4 * l2 - 1, 0],
                   [0, 0, 4 * l3 - 1], [4 * l2, 4 * l1, 0],
                   [0, 4 * l3, 4 * l2], [4 * l3, 0, 4 * l1]])
    return Nv, Cg


_SHAPES = [_shape(L) for L in _GL]


def _rcm(n: int, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Reverse Cuthill-McKee ordering of the symmetric graph (rows, cols)."""
    m = rows != cols
    r, c = rows[m], cols[m]
    order = np.lexsort((c, r))
    r, c = r[order], c[order]
    keep = np.ones(len(r), dtype=bool)
    keep[1:] = (r[1:] != r[:-1]) | (c[1:] != c[:-1])
    r, c = r[keep], c[keep]
    ptr = np.searchsorted(r, np.arange(n + 1))
    deg = np.diff(ptr)
    adj = [c[ptr[i]:ptr[i + 1]].tolist() for i in range(n)]
    degl = deg.tolist()
    seen = [False] * n
    out: List[int] = []
    for start in np.argsort(deg, kind="stable").tolist():
        if seen[start]:
            continue
        # pseudo-peripheral start: BFS twice
        s = start
        for _ in range(2):
            lev = {s: 0}
            fr = [s]
            last = s
            while fr:
                nx = []
                for v in fr:
                    for w in adj[v]:
                        if w not in lev:
                            lev[w] = lev[v] + 1
                            nx.append(w)
                if nx:
                    last = min(nx, key=lambda q: degl[q])
                fr = nx
            s = last
        seen[s] = True
        q = [s]
        qi = 0
        while qi < len(q):
            v = q[qi]
            qi += 1
            nb = [w for w in adj[v] if not seen[w]]
            nb.sort(key=lambda q_: degl[q_])
            for w in nb:
                seen[w] = True
                q.append(w)
        out.extend(q)
    return np.array(out[::-1], dtype=np.int64)


def _components(n: int, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    parent = np.arange(n)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in zip(rows.tolist(), cols.tolist()):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return np.array([find(i) for i in range(n)])


def _band_solve(n: int, rows, cols, vals, F: np.ndarray,
                pinned: np.ndarray) -> np.ndarray:
    """Solve K X = F (K symmetric positive semi-definite, assembled from
    triplets) with the ``pinned`` dofs held at zero: RCM reordering then a
    block-banded Cholesky factorisation (numpy.linalg.cholesky)."""
    free = np.ones(n, dtype=bool)
    free[pinned] = False
    perm = _rcm(n, rows, cols)
    perm = perm[free[perm]]
    m = len(perm)
    pos = np.full(n, -1, dtype=np.int64)
    pos[perm] = np.arange(m)
    pr, pc = pos[rows], pos[cols]
    ok = (pr >= 0) & (pc >= 0)
    pr, pc, pv = pr[ok], pc[ok], vals[ok]
    bw = int(np.max(np.abs(pr - pc))) if len(pr) else 0
    bs = min(max(bw, 32), m)
    nb = -(-m // bs)
    M = nb * bs
    lower = pr >= pc
    pr, pc, pv = pr[lower], pc[lower], pv[lower]
    bi, bj = pr // bs, pc // bs
    diag = bi == bj
    D = np.bincount((bi[diag] * bs + pr[diag] % bs) * bs + pc[diag] % bs,
                    weights=pv[diag], minlength=nb * bs * bs
                    ).reshape(nb, bs, bs)
    sub = ~diag
    S = np.bincount((bj[sub] * bs + pr[sub] % bs) * bs + pc[sub] % bs,
                    weights=pv[sub], minlength=nb * bs * bs
                    ).reshape(nb, bs, bs)
    # symmetric diagonal blocks (only the lower triangle was summed)
    for k in range(nb):
        Dk = D[k]
        D[k] = Dk + np.tril(Dk, -1).T
    pad = M - m
    if pad:
        idx = np.arange(m - (nb - 1) * bs, bs)
        D[nb - 1][idx, idx] = 1.0
    Fp = np.zeros((M, F.shape[1]))
    Fp[:m] = F[perm]
    Ld, Ls = [], []
    for k in range(nb):
        Akk = D[k]
        if k:
            Akk = Akk - Ls[k - 1] @ Ls[k - 1].T
        try:
            Lk = np.linalg.cholesky(Akk)
        except np.linalg.LinAlgError:
            raise MeshError("singular warping stiffness (degenerate mesh)") \
                from None
        Ld.append(Lk)
        if k < nb - 1:
            Ls.append(np.linalg.solve(Lk, S[k].T).T)
    Y = np.empty_like(Fp)
    for k in range(nb):
        rhs = Fp[k * bs:(k + 1) * bs]
        if k:
            rhs = rhs - Ls[k - 1] @ Y[(k - 1) * bs:k * bs]
        Y[k * bs:(k + 1) * bs] = np.linalg.solve(Ld[k], rhs)
    X = np.empty_like(Fp)
    for k in range(nb - 1, -1, -1):
        rhs = Y[k * bs:(k + 1) * bs]
        if k < nb - 1:
            rhs = rhs - Ls[k].T @ X[(k + 1) * bs:(k + 2) * bs]
        X[k * bs:(k + 1) * bs] = np.linalg.solve(Ld[k].T, rhs)
    out = np.zeros((n, F.shape[1]))
    out[perm] = X[:m]
    return out


def _tri_prep(P3: np.ndarray, w: np.ndarray):
    """Per-triangle sorted vertex heights + weighted areas (for clipping)."""
    ys = np.sort(P3[:, :, 1], axis=1)
    x, y = P3[:, :, 0], P3[:, :, 1]
    ar = 0.5 * np.abs((x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0])
                      - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])) * w
    ya, yb, yc = ys[:, 0], ys[:, 1], ys[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        k1 = ar / ((yb - ya) * (yc - ya))
        k2 = ar / ((yc - yb) * (yc - ya))
    return ya, yb, yc, ar, k1, k2


def _clip_above(pre, c: float) -> Tuple[float, float]:
    """(weighted area, weighted int y dA) of the triangles above y = c."""
    ya, yb, yc, ar, k1, k2 = pre
    ym = (ya + yb + yc) / 3
    full = c <= ya
    A = float(np.sum(ar[full]))
    S = float(np.sum(ar[full] * ym[full]))
    m1 = (c > ya) & (c <= yb) & (yc > ya)
    if np.any(m1):
        alo = k1[m1] * (c - ya[m1]) ** 2
        A += float(np.sum(ar[m1] - alo))
        S += float(np.sum(ar[m1] * ym[m1] - alo * (ya[m1] + 2 * c) / 3))
    m2 = (c > yb) & (c < yc)
    if np.any(m2):
        aup = k2[m2] * (yc[m2] - c) ** 2
        A += float(np.sum(aup))
        S += float(np.sum(aup * (yc[m2] + 2 * c) / 3))
    return A, S


def _plastic(P3: np.ndarray, w: np.ndarray) -> float:
    """Plastic modulus about the horizontal equal-(weighted)-area axis
    (exact clipping of the straight triangles; bisection on the axis)."""
    pre = _tri_prep(P3, w)
    At, St = _clip_above(pre, -np.inf)
    lo, hi = float(pre[0].min()), float(pre[2].max())
    for _ in range(64):
        mid = 0.5 * (lo + hi)
        if _clip_above(pre, mid)[0] > 0.5 * At:
            lo = mid
        else:
            hi = mid
    c = 0.5 * (lo + hi)
    Aa, Sa = _clip_above(pre, c)
    Ab, Sb = At - Aa, St - Sa
    return (Sa - c * Aa) - (Sb - c * Ab)


def _analyse(mesh: dict, nu: float) -> dict:
    X, E, wn = mesh["nodes"], mesh["elems"], mesh["n"]
    nn, ne = len(X), len(E)
    XE = X[E]                                   # (ne, 6, 2)
    x1, y1 = XE[:, 0, 0], XE[:, 0, 1]
    x2, y2 = XE[:, 1, 0], XE[:, 1, 1]
    x3, y3 = XE[:, 2, 0], XE[:, 2, 1]
    a2 = (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)
    if np.any(a2 <= 0):
        raise MeshError("inverted element")
    area = a2 / 2
    dL = np.stack([np.stack([y2 - y3, x3 - x2], 1),
                   np.stack([y3 - y1, x1 - x3], 1),
                   np.stack([y1 - y2, x2 - x1], 1)], 1) / a2[:, None, None]
    # geometric properties (weighted)
    gx, gy, Bs = [], [], []
    for (Nv, Cg) in _SHAPES:
        gx.append(XE[:, :, 0] @ Nv)
        gy.append(XE[:, :, 1] @ Nv)
        Bs.append(np.einsum("ij,ejk->eik", Cg, dL))        # (ne, 6, 2)
    W = [_GW[g] * area * wn for g in range(6)]
    A = sum(float(np.sum(W[g])) for g in range(6))
    A_geo = float(np.sum(area))
    Qx = sum(float(np.sum(W[g] * gy[g])) for g in range(6))
    Qy = sum(float(np.sum(W[g] * gx[g])) for g in range(6))
    cx, cy = Qy / A, Qx / A
    gx = [v - cx for v in gx]
    gy = [v - cy for v in gy]
    Ixx = sum(float(np.sum(W[g] * gy[g] ** 2)) for g in range(6))
    Iyy = sum(float(np.sum(W[g] * gx[g] ** 2)) for g in range(6))
    Ixy = sum(float(np.sum(W[g] * gx[g] * gy[g])) for g in range(6))
    det = Ixx * Iyy - Ixy * Ixy
    # stiffness + load vectors
    Ke = np.zeros((ne, 6, 6))
    fw = np.zeros((ne, 6))
    fpsi = np.zeros((ne, 6))
    fphi = np.zeros((ne, 6))
    for g, (Nv, _Cg) in enumerate(_SHAPES):
        B, w, x, y = Bs[g], W[g], gx[g], gy[g]
        Ke += w[:, None, None] * np.einsum("eik,ejk->eij", B, B)
        fw += w[:, None] * (B[:, :, 0] * y[:, None] - B[:, :, 1] * x[:, None])
        r, q = x * x - y * y, 2 * x * y
        d1, d2 = Ixx * r - Ixy * q, Ixy * r + Ixx * q
        h1, h2 = -Ixy * r + Iyy * q, -Iyy * r - Ixy * q
        fpsi += w[:, None] * (nu / 2 * (B[:, :, 0] * d1[:, None]
                                        + B[:, :, 1] * d2[:, None])
                              + 2 * (1 + nu) * Nv[None, :]
                              * (Ixx * x - Ixy * y)[:, None])
        fphi += w[:, None] * (nu / 2 * (B[:, :, 0] * h1[:, None]
                                        + B[:, :, 1] * h2[:, None])
                              + 2 * (1 + nu) * Nv[None, :]
                              * (Iyy * y - Ixy * x)[:, None])
    rows = np.repeat(E, 6, axis=1).reshape(-1)
    cols = np.tile(E, (1, 6)).reshape(-1)
    vals = Ke.reshape(-1)
    comp = _components(nn, E[:, 0].repeat(5), E[:, 1:].reshape(-1))
    roots = np.unique(comp)
    parts = len(roots)
    pinned = np.array([int(np.nonzero(comp == r)[0][0]) for r in roots])

    def gather(fe):
        out = np.zeros(nn)
        np.add.at(out, E.reshape(-1), fe.reshape(-1))
        return out
    f_w = gather(fw)
    F = [f_w]
    if parts == 1:
        F += [gather(fpsi), gather(fphi)]
    sol = _band_solve(nn, rows, cols, vals, np.stack(F, 1), pinned)
    omega = sol[:, 0]
    J = Ixx + Iyy - float(omega @ f_w)
    out = {"A": A, "A_geometric": A_geo, "cx": cx, "cy": cy, "Ixx": Ixx,
           "Iyy": Iyy, "Ixy": Ixy, "J": J, "parts": parts}
    # warping integrals (omega shifted to zero weighted mean)
    om_e = omega[E]
    qw = iw = ixw = iyw = 0.0
    for g, (Nv, _Cg) in enumerate(_SHAPES):
        o = om_e @ Nv
        qw += float(np.sum(W[g] * o))
        iw += float(np.sum(W[g] * o * o))
        ixw += float(np.sum(W[g] * gx[g] * o))
        iyw += float(np.sum(W[g] * gy[g] * o))
    if parts == 1 and det > 0:
        x_t = (Ixy * ixw - Iyy * iyw) / det
        y_t = (Ixx * ixw - Ixy * iyw) / det
        Cw = iw - qw * qw / A - y_t * ixw + x_t * iyw
        psi, phi = sol[:, 1], sol[:, 2]
        ds = 2 * (1 + nu) * det
        scx = scy = 0.0
        kx = ky = kxy = 0.0
        ps_e, ph_e = psi[E], phi[E]
        for g in range(6):
            x, y, w, B = gx[g], gy[g], W[g], Bs[g]
            rr = x * x + y * y
            scx += float(np.sum(w * (Iyy * x + Ixy * y) * rr))
            scy += float(np.sum(w * (Ixx * y + Ixy * x) * rr))
            r, q = x * x - y * y, 2 * x * y
            d1, d2 = Ixx * r - Ixy * q, Ixy * r + Ixx * q
            h1, h2 = -Ixy * r + Iyy * q, -Iyy * r - Ixy * q
            bpx = np.einsum("ei,ei->e", B[:, :, 0], ps_e) - nu / 2 * d1
            bpy = np.einsum("ei,ei->e", B[:, :, 1], ps_e) - nu / 2 * d2
            bhx = np.einsum("ei,ei->e", B[:, :, 0], ph_e) - nu / 2 * h1
            bhy = np.einsum("ei,ei->e", B[:, :, 1], ph_e) - nu / 2 * h2
            kx += float(np.sum(w * (bpx * bpx + bpy * bpy)))
            ky += float(np.sum(w * (bhx * bhx + bhy * bhy)))
            kxy += float(np.sum(w * (bpx * bhx + bpy * bhy)))
        x_s = (nu / 2 * scx - float(f_w @ phi)) / ds
        y_s = (nu / 2 * scy + float(f_w @ psi)) / ds
        out.update({"Cw": max(Cw, 0.0), "x_s": x_s, "y_s": y_s,
                    "x_t": x_t, "y_t": y_t,
                    "Asx": ds * ds / kx if kx > 0 else None,
                    "Asy": ds * ds / ky if ky > 0 else None,
                    "Asxy": ds * ds / kxy if abs(kxy) > 0 else None})
    else:
        out.update({"Cw": None, "x_s": None, "y_s": None, "x_t": None,
                    "y_t": None, "Asx": None, "Asy": None, "Asxy": None})
    # moduli
    xs, ys = X[:, 0] - cx, X[:, 1] - cy
    out.update({"Sx_top": Ixx / float(ys.max()),
                "Sx_bot": Ixx / float(-ys.min()),
                "Sy_right": Iyy / float(xs.max()),
                "Sy_left": Iyy / float(-xs.min())})
    P3 = XE[:, :3, :] - np.array([cx, cy])
    out["Zx"] = _plastic(P3, wn)
    out["Zy"] = _plastic(P3[:, :, ::-1].copy(), wn)
    avg, R = 0.5 * (Ixx + Iyy), math.hypot(0.5 * (Ixx - Iyy), Ixy)
    th = 0.5 * math.atan2(-2.0 * Ixy, Ixx - Iyy)
    out.update({"I_major": avg + R, "I_minor": avg - R, "theta": th})
    c, s = math.cos(th), math.sin(th)
    rot = np.array([[c, -s], [s, c]])           # (x, y) -> (u, v)
    Puv = P3 @ rot
    out["Z_major"] = _plastic(Puv, wn)
    out["Z_minor"] = _plastic(Puv[:, :, ::-1].copy(), wn)
    return out


# --------------------------------------------------------------------------- #
# public entry point + cache
# --------------------------------------------------------------------------- #
_CACHE: "OrderedDict[str, dict]" = OrderedDict()
_CACHE_MAX = 256
_STATS = {"hits": 0, "misses": 0}


def cache_clear() -> None:
    _CACHE.clear()
    _STATS.update(hits=0, misses=0)


def cache_info() -> dict:
    return {"size": len(_CACHE), **_STATS}


def _key(regs: List[dict], nu: float, target: int, max_area) -> str:
    def rnd(a):
        return [[float("%.12g" % v) for v in p] for p in np.asarray(a)]
    blob = json.dumps({"r": [{"o": rnd(r["outer"]),
                              "h": [rnd(h) for h in r["holes"]],
                              "n": float("%.12g" % r["n"])} for r in regs],
                       "nu": nu, "t": target, "a": max_area},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()


def solve(regions, nu: float = 0.0, target: int = DEFAULT_TARGET,
          max_area: Optional[float] = None, use_cache: bool = True) -> dict:
    """Exact section properties of ``regions`` (module docstring).

    Returns a dict (coordinates in the input frame; moments about the
    weighted centroid; all quantities modulus-weighted = transformed to
    ``n = 1``): ``method ("exact"), A, A_geometric, cx, cy, Ixx (= int
    y^2), Iyy (= int x^2), Ixy, I_major, I_minor, theta (rad, major axis
    from +x), J, Cw, xs, ys (shear centre, elasticity), xs_trefftz,
    ys_trefftz, x0, y0 (shear centre - centroid), Asx, Asy (shear areas
    for shear along x / y), Sx_top, Sx_bot, Sy_left, Sy_right, Zx, Zy,
    Z_major, Z_minor, nu, parts, mesh {elements, nodes, dofs}, time_ms,
    hash, cached, warnings``.  Raises :class:`MeshError` (a ValueError)
    for unusable geometry.
    """
    if isinstance(nu, bool) or not isinstance(nu, (int, float)) \
            or not (-1.0 < nu < 0.5):
        raise ValueError("nu must be a number in (-1, 0.5)")
    target = int(max(50, min(int(target), 20000)))
    regs = _normalise(regions)
    key = _key(regs, float(nu), target, max_area)
    if use_cache and key in _CACHE:
        _CACHE.move_to_end(key)
        _STATS["hits"] += 1
        res = copy.deepcopy(_CACHE[key])
        res["cached"] = True
        return res
    _STATS["misses"] += 1
    t0 = time.perf_counter()
    mesh = mesh_regions(regs, target=target, max_area=max_area)
    r = _analyse(mesh, float(nu))
    cx, cy = r["cx"], r["cy"]
    warnings: List[str] = []
    if r["parts"] > 1:
        warnings.append(f"{r['parts']} disconnected parts: J is the sum of "
                        "the parts; shear centre, shear areas and Cw are not "
                        "defined for parts that do not act together.")
    res = {
        "method": "exact", "A": r["A"], "A_geometric": r["A_geometric"],
        "cx": cx, "cy": cy, "Ixx": r["Ixx"], "Iyy": r["Iyy"],
        "Ixy": r["Ixy"], "I_major": r["I_major"], "I_minor": r["I_minor"],
        "theta": r["theta"], "J": r["J"], "Cw": r["Cw"],
        "xs": None if r["x_s"] is None else cx + r["x_s"],
        "ys": None if r["y_s"] is None else cy + r["y_s"],
        "xs_trefftz": None if r["x_t"] is None else cx + r["x_t"],
        "ys_trefftz": None if r["y_t"] is None else cy + r["y_t"],
        "x0": r["x_s"], "y0": r["y_s"],
        "Asx": r["Asx"], "Asy": r["Asy"],
        "Sx_top": r["Sx_top"], "Sx_bot": r["Sx_bot"],
        "Sy_left": r["Sy_left"], "Sy_right": r["Sy_right"],
        "Zx": r["Zx"], "Zy": r["Zy"], "Z_major": r["Z_major"],
        "Z_minor": r["Z_minor"], "nu": float(nu), "parts": r["parts"],
        "mesh": {"elements": int(len(mesh["elems"])),
                 "nodes": int(len(mesh["nodes"])),
                 "dofs": int(len(mesh["nodes"]))},
        "time_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        "hash": key, "cached": False, "warnings": warnings,
    }
    if use_cache:
        _CACHE[key] = copy.deepcopy(res)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return res
