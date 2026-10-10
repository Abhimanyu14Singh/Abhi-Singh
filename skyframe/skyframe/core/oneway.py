"""One-way load distribution of membrane slabs (ETABS one-way deck/slab).

CONTRACT "Shell element types, one-way slabs and shell benchmarks".

A ``behavior == "membrane"`` slab with ``distribution == "one_way"`` is cut
into strips parallel to the SPAN direction ``d`` (region local axis 1 =
unit(c1 - c0) when ``one_way_dir == 1``; local axis 2 = n x axis 1 when
``one_way_dir == 2``; n = the corner-ordering normal).  The two SUPPORT
edges are the opposite edge pair (0, 2) or (1, 3) that ``d`` crosses most
squarely (larger summed |t_k . p|, p = n x d; ties -> (1, 3) for local 1,
(0, 2) for local 2).  Every strip of chord length l (along d) and width
d(eta) (eta = coordinate along p):

* both ends on support edges -> half of ``q l d(eta)`` to each end;
* only one end on a support edge -> all of it to that end;

so on support edge k (parameter s, |t_k . p| ds = d(eta)) the line load is
``w(s) = factor * l(s) * |t_k . p|`` kN/m per kPa.  l(s) is piecewise
linear between the projections of the corners, so each profile is an exact
piecewise-linear trapezoid chain.  Non-support edges get NOTHING.  For a
parallelogram (rectangle) whose span crosses the support pair, every strip
hits both supports and the distribution is exact (rectangle Lx x Ly, span
X: each Y-edge beam carries the uniform q Lx / 2).  Any strip area touching
no support edge (possible only for non-parallelogram quads) is restored by
a uniform rescale (warning) so the region total is conserved exactly.

Beams are found exactly like the two-way rule (members collinear with the
edge); a support edge or edge part without beams sends its load to that
edge's two corner nodes (warning), which must be FE nodes.  Openings reduce
the total uniformly by the opening-area ratio (same documented
approximation and warning as the two-way rule).
"""

from __future__ import annotations

import math
import warnings
from typing import Dict, List, Tuple

Vec3 = Tuple[float, float, float]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n)


def region_axes(region) -> Tuple[Vec3, Vec3, Vec3]:
    """(local 1, local 2, normal) of a 4-corner region."""
    c = [tuple(map(float, p)) for p in region.corners]
    e1 = _unit(_sub(c[1], c[0]))
    nrm = _unit(_cross(_sub(c[2], c[0]), _sub(c[3], c[1])))
    e2 = _cross(nrm, e1)
    return e1, e2, nrm


def span_and_supports(region) -> Tuple[Vec3, Vec3, Tuple[int, int]]:
    """(span direction d, transverse p = n x d, support edge pair)."""
    c = [tuple(map(float, p)) for p in region.corners]
    e1, e2, nrm = region_axes(region)
    d = e1 if int(getattr(region, "one_way_dir", 1)) == 1 else e2
    p = _cross(nrm, d)
    cross = []
    for k in range(4):
        t = _unit(_sub(c[(k + 1) % 4], c[k]))
        cross.append(abs(_dot(t, p)))
    s02, s13 = cross[0] + cross[2], cross[1] + cross[3]
    if abs(s02 - s13) <= 1e-12 * max(1.0, s02 + s13):
        pair = (1, 3) if d is e1 else (0, 2)
    else:
        pair = (0, 2) if s02 > s13 else (1, 3)
    return d, p, pair


def _chord(c, d, p, eta) -> List[Tuple[float, int]]:
    """Intersections (station along d, edge index) of the strip line
    x . p = eta with the polygon edges (convex: 2 points, sorted)."""
    hits: List[Tuple[float, int]] = []
    for k in range(4):
        a, b = c[k], c[(k + 1) % 4]
        ea, eb = _dot(a, p), _dot(b, p)
        if abs(eb - ea) < 1e-14:
            continue
        lam = (eta - ea) / (eb - ea)
        if -1e-12 <= lam <= 1.0 + 1e-12:
            x = tuple(a[i] + lam * (b[i] - a[i]) for i in range(3))
            hits.append((_dot(x, d), k))
    hits.sort()
    # drop duplicate hits at a shared vertex
    out: List[Tuple[float, int]] = []
    for h in hits:
        if not out or abs(h[0] - out[-1][0]) > 1e-10:
            out.append(h)
    return out


def edge_profiles(region) -> Tuple[List[List[Tuple[float, float]]],
                                   Tuple[int, int]]:
    """Per-edge (s, w) breakpoint profiles per unit q (non-support edges
    get [(0, 0), (L, 0)]) BEFORE conservation rescale; plus the pair."""
    c = [tuple(map(float, p)) for p in region.corners]
    d, p, pair = span_and_supports(region)
    profiles: List[List[Tuple[float, float]]] = []
    for k in range(4):
        a, b = c[k], c[(k + 1) % 4]
        L = math.sqrt(_dot(_sub(b, a), _sub(b, a)))
        if k not in pair:
            profiles.append([(0.0, 0.0), (L, 0.0)])
            continue
        t = _unit(_sub(b, a))
        tp = _dot(t, p)
        ea = _dot(a, p)
        # breakpoints: edge ends + projections of the other corners
        ss = {0.0, L}
        if abs(tp) > 1e-14:
            for v in c:
                s = (_dot(v, p) - ea) / tp
                if 1e-9 < s < L - 1e-9:
                    ss.add(s)
        ss = sorted(ss)
        bps: List[Tuple[float, float]] = []
        if abs(tp) <= 1e-14:
            profiles.append([(0.0, 0.0), (L, 0.0)])
            continue
        for s0, s1 in zip(ss[:-1], ss[1:]):
            sm = 0.5 * (s0 + s1)
            hits = _chord(c, d, p, ea + sm * tp)
            if len(hits) < 2:
                vals = (0.0, 0.0)
            else:
                (x_lo, k_lo), (x_hi, k_hi) = hits[0], hits[-1]
                other = k_hi if k_lo == k else k_lo
                factor = 0.5 if other in pair else 1.0
                vals = []
                for s in (s0, s1):
                    hh = _chord(c, d, p, ea + s * tp)
                    ell = (hh[-1][0] - hh[0][0]) if len(hh) >= 2 else 0.0
                    vals.append(factor * ell * abs(tp))
            if bps and abs(bps[-1][0] - s0) < 1e-12:
                if abs(bps[-1][1] - vals[0]) > 1e-12:
                    bps.append((s0, vals[0]))      # step (vertex change)
            else:
                bps.append((s0, vals[0]))
            bps.append((s1, vals[1]))
        profiles.append(bps)
    return profiles, pair


def _integral(bps):
    return sum(0.5 * (w0 + w1) * (s1 - s0)
               for (s0, w0), (s1, w1) in zip(bps[:-1], bps[1:]))


def distribution(model, region, pool):
    """(loads, nodal) per unit q, same records as the two-way rule."""
    from .mesh import TributaryMemberLoad, _key, _members_on_edge, _TOL
    c = [tuple(map(float, p)) for p in region.corners]
    area = region.area
    if region.openings:
        warnings.warn(
            f"Membrane slab {region.uid!r}: openings reduce the one-way "
            "distributed load uniformly by the opening area ratio "
            f"{region.opening_area / area:.4f} (approximation)")
        area = region.net_area
    profiles, pair = edge_profiles(region)
    tot = sum(_integral(pr) for pr in profiles)
    if tot <= 1e-12:
        raise ValueError(f"Membrane slab {region.uid!r}: one-way span "
                         "direction crosses no support edge")
    f = area / tot
    if abs(tot - region.area) > 1e-9 * max(1.0, region.area):
        warnings.warn(f"Membrane slab {region.uid!r}: one-way strips not "
                      "reaching a support edge; load rescaled by "
                      f"{area / tot:.6g} to conserve the total")
    profiles = [[(s, w * f) for s, w in pr] for pr in profiles]

    edges = [(c[k], c[(k + 1) % 4]) for k in range(4)]
    loads: List = []
    corner: Dict[Vec3, float] = {}
    applied = 0.0
    for k in pair:
        pa, pb = edges[k]
        L = math.sqrt(_dot(_sub(pb, pa), _sub(pb, pa)))
        prof = profiles[k]
        bp_s = [s for s, _ in prof]
        covered: List[Tuple[float, float]] = []
        for member, s_pi, s_pj in _members_on_edge(model, pa, pb):
            lo = max(0.0, min(s_pi, s_pj))
            hi = min(L, max(s_pi, s_pj))
            pieces, cur = [], lo
            for c0, c1 in sorted(covered):
                if c1 <= cur:
                    continue
                if c0 > hi:
                    break
                if c0 > cur:
                    pieces.append((cur, min(c0, hi)))
                cur = max(cur, c1)
            if cur < hi:
                pieces.append((cur, hi))
            for p0, p1 in pieces:
                if p1 - p0 <= _TOL:
                    continue
                covered.append((p0, p1))
                pts = sorted({p0, p1} | {s for s in bp_s if p0 < s < p1})
                for s0, s1 in zip(pts[:-1], pts[1:]):
                    # step profiles: evaluate just inside the sub-piece
                    w0 = _value_right(prof, s0)
                    w1 = _value_left(prof, s1)
                    if abs(w0) < 1e-12 and abs(w1) < 1e-12:
                        continue
                    fr0 = (s0 - s_pi) / (s_pj - s_pi)
                    fr1 = (s1 - s_pi) / (s_pj - s_pi)
                    if fr0 > fr1:
                        fr0, fr1, w0, w1 = fr1, fr0, w1, w0
                    loads.append(TributaryMemberLoad(
                        member.uid, w0, w1, min(max(fr0, 0.0), 1.0),
                        min(max(fr1, 0.0), 1.0)))
                    applied += 0.5 * (w0 + w1) * (s1 - s0)
        residual = _integral(prof) - sum(_integral_piece(prof, a0, b0)
                                         for a0, b0 in covered)
        if abs(residual) > 1e-9 * max(1.0, area):
            warnings.warn(f"Membrane slab {region.uid!r}: one-way support "
                          f"edge {k} not fully covered by beams; "
                          f"{residual:.6g} kN/kPa moved to its corner nodes")
            for pt in (pa, pb):
                corner[_key(pt)] = corner.get(_key(pt), 0.0) + residual / 2
    nodal: Dict[int, float] = {}
    for ck, w in corner.items():
        idx = pool.find(ck)
        if idx is None:
            raise ValueError(
                f"Membrane slab {region.uid!r}: needs a nodal load at corner "
                f"{ck} but no frame node exists there")
        nodal[idx] = nodal.get(idx, 0.0) + w
        applied += w
    if abs(applied - area) > 1e-8 * max(1.0, area):
        raise AssertionError(f"Membrane slab {region.uid!r}: one-way "
                             f"distribution lost load ({applied!r} vs "
                             f"{area!r})")
    return loads, nodal


def _value_right(bps, s):
    """Profile value just to the right of s (steps take the right value)."""
    for (s0, w0), (s1, w1) in zip(bps[:-1], bps[1:]):
        if s0 - 1e-12 <= s < s1 - 1e-12:
            return w0 + (w1 - w0) * (s - s0) / (s1 - s0)
    return bps[-1][1]


def _value_left(bps, s):
    """Profile value just to the left of s."""
    for (s0, w0), (s1, w1) in zip(bps[:-1], bps[1:]):
        if s0 + 1e-12 < s <= s1 + 1e-12:
            return w0 + (w1 - w0) * (s - s0) / (s1 - s0)
    return bps[0][1]


def _integral_piece(bps, lo, hi):
    tot = 0.0
    for (s0, w0), (s1, w1) in zip(bps[:-1], bps[1:]):
        a, b = max(s0, lo), min(s1, hi)
        if b - a <= 0.0 or s1 - s0 <= 0.0:
            continue
        wa = w0 + (w1 - w0) * (a - s0) / (s1 - s0)
        wb = w0 + (w1 - w0) * (b - s0) / (s1 - s0)
        tot += 0.5 * (wa + wb) * (b - a)
    return tot
