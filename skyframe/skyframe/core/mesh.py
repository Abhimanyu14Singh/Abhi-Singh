"""Meshing layer: shell-region quad meshing + frame/shell compatibility.

``mesh_model(model) -> MeshedModel`` performs, solver-agnostically:

* structured quad meshing of every ``behavior == "shell"`` ShellRegion
  (``nx, ny = max(1, round(edge_len / mesh_size))``, bilinear interior points
  so general planar quads mesh cleanly);
* global node dedup (1e-6) across shell meshes and frame member endpoints;
* frame-shell compatibility: any frame member whose axis passes through shell
  mesh nodes (collinear with a region edge chain, fully or partially
  overlapping) is SPLIT into segments whose intermediate nodes coincide with
  the mesh nodes.  The parent uid -> ordered segment list is tracked so the
  engine can re-aggregate per-ORIGINAL-member results;
* per-node tributary areas of shell regions (consistent nodal loads for area
  loads: each element contributes a quarter of its exact shoelace area to
  each of its 4 nodes);
* membrane slabs (``behavior == "membrane"``): NOT meshed — each unit of
  area load converts to two-way (45 degree) tributary trapezoid/triangle
  member loads on the slab's edge beams, with the load total conserved
  exactly.  Missing-edge policy (documented): an edge without beams sends its
  share to the two ADJACENT edges' beams, split proportionally to those
  edges' own tributary totals; if neither adjacent edge has beams, that
  edge's share goes to its two corner nodes as nodal loads; if NO edge has
  beams the whole load goes to the four corner nodes (corner columns)
  equally.  Partially covered edges send the uncovered remainder to the
  edge's corner nodes (with a warning).

Units: kN, m, kPa (area load q).  All tributary records are stored per unit
q = 1 kPa; the engine scales them by ``q * case_factor``.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .model import BuildingModel, FrameMember, ShellRegion, _polygon_area3d

_TOL = 1e-6

# v0.11 Winkler elastic-foundation discretization controls.  A foundation
# member (foundation_ks > 0 and foundation_width > 0) is split into N equal
# segments so the lumped nodal soil springs resolve the beam-on-elastic-
# foundation response: segment length <= min(L / FOUNDATION_MIN_SEGMENTS,
# lc / FOUNDATION_SEGS_PER_LC), where the Hetenyi characteristic length
# lc = (4 E I33 / k_line)^0.25.  N is floored at FOUNDATION_MIN_SEGMENTS and
# capped at FOUNDATION_MAX_SEGMENTS.  (Exposed as module globals so tests can
# force a fixed N and assert monotone mesh convergence.)
FOUNDATION_MIN_SEGMENTS = 8
FOUNDATION_MAX_SEGMENTS = 40
FOUNDATION_SEGS_PER_LC = 4.0


def foundation_segment_count(model: BuildingModel, member: FrameMember) -> int:
    """Number of equal FE segments for a Winkler-foundation member (1 if off).

    Hetenyi beam on elastic foundation: characteristic length
    ``lc = (4 E I33 / k_line)^0.25`` (``k_line = ks * width``).  The segment
    length is capped at ``min(L / FOUNDATION_MIN_SEGMENTS, lc /
    FOUNDATION_SEGS_PER_LC)`` and N clamped to
    [FOUNDATION_MIN_SEGMENTS, FOUNDATION_MAX_SEGMENTS].
    """
    k_line = getattr(member, "foundation_k_line", 0.0)
    if k_line <= 0.0:
        return 1
    sec = model.sections.get(member.section)
    if sec is None:
        return 1
    mat = model.materials.get(sec.material)
    if mat is None:
        return 1
    EI = mat.E * sec.I33 * getattr(sec, "mod_I33", 1.0)
    L = member.length
    if EI <= 0.0 or L <= 0.0:
        return 1
    lc = (4.0 * EI / k_line) ** 0.25
    seg_max = min(L / FOUNDATION_MIN_SEGMENTS, lc / FOUNDATION_SEGS_PER_LC)
    n = math.ceil(L / seg_max) if seg_max > 0.0 else FOUNDATION_MIN_SEGMENTS
    return max(FOUNDATION_MIN_SEGMENTS, min(FOUNDATION_MAX_SEGMENTS, n))

Vec3 = Tuple[float, float, float]


# --------------------------------------------------------------------------- #
# small vector helpers
# --------------------------------------------------------------------------- #
def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def _key(p) -> Vec3:
    """Dedup key: coordinates rounded to 1e-6 (matches the engine)."""
    return (round(float(p[0]), 6), round(float(p[1]), 6),
            round(float(p[2]), 6))


class _PointPool:
    """Global deduplicated 3D point pool (1e-6 merge tolerance)."""

    def __init__(self) -> None:
        self.points: List[Vec3] = []
        self._index: Dict[Vec3, int] = {}

    def add(self, p) -> int:
        k = _key(p)
        idx = self._index.get(k)
        if idx is None:
            idx = len(self.points)
            self._index[k] = idx
            self.points.append(k)
        return idx

    def find(self, p) -> Optional[int]:
        return self._index.get(_key(p))


# --------------------------------------------------------------------------- #
# mesh data structures
# --------------------------------------------------------------------------- #
@dataclass
class Segment:
    """One FE piece of a (possibly split) frame member."""

    parent: str          # parent member uid
    index: int           # 0-based order along the parent (i -> j)
    ni: int              # point index of segment end i
    nj: int              # point index of segment end j
    x0: float            # distance of end i from the parent's end i (m)
    length: float        # segment length (m)


@dataclass
class ShellQuad:
    """One meshed ShellMITC4 quad (counter-clockwise node order)."""

    region: str
    nodes: Tuple[int, int, int, int]   # point indices


@dataclass
class TributaryMemberLoad:
    """Gravity trapezoid load on a member, per unit area load q = 1 kPa.

    ``w1``/``w2`` in kN/m per kPa at member-length fractions ``a``/``b``.
    """

    member_uid: str
    w1: float
    w2: float
    a: float
    b: float


@dataclass
class MeshedModel:
    """FE-ready geometry derived from a BuildingModel."""

    model: BuildingModel
    points: List[Vec3]                                   # deduped, rounded
    segments: Dict[str, List[Segment]]                   # parent uid -> ordered
    quads: List[ShellQuad]                               # all shell elements
    region_trib: Dict[str, Dict[int, float]] = field(default_factory=dict)
    #   shell-behavior regions: point idx -> tributary area (m^2)
    membrane_loads: Dict[str, List[TributaryMemberLoad]] = field(
        default_factory=dict)                            # per unit q
    membrane_nodal: Dict[str, Dict[int, float]] = field(default_factory=dict)
    #   membrane regions: point idx -> downward kN per unit q


# --------------------------------------------------------------------------- #
# shell-region structured meshing
# --------------------------------------------------------------------------- #
def _omitted_cells(region: ShellRegion, nx: int, ny: int) -> set:
    """Cells (i, j) omitted by openings, snapped to the structured mesh.

    Each opening bound snaps to the NEAREST mesh line (u -> round(u*nx),
    v -> round(v*ny)).  An opening whose snapped span collapses to zero
    cells in either direction (smaller than one element) is skipped with a
    warning.
    """
    omitted: set = set()
    for k, op in enumerate(getattr(region, "openings", []) or []):
        i0 = min(max(int(round(op.u0 * nx)), 0), nx)
        i1 = min(max(int(round(op.u1 * nx)), 0), nx)
        j0 = min(max(int(round(op.v0 * ny)), 0), ny)
        j1 = min(max(int(round(op.v1 * ny)), 0), ny)
        if i1 <= i0 or j1 <= j0:
            warnings.warn(
                f"Shell {region.uid!r}: opening {k} is smaller than one "
                "mesh element after snapping to the mesh lines; skipped")
            continue
        omitted.update((i, j) for i in range(i0, i1) for j in range(j0, j1))
    return omitted


def _mesh_region(region: ShellRegion, pool: _PointPool,
                 quads: List[ShellQuad]) -> Dict[int, float]:
    """Structured quad mesh of one planar region; returns nodal tributary
    areas (quarter of each element's exact shoelace area per node).

    v0.5 openings: cells fully inside a (mesh-line-snapped) opening are
    omitted, and grid points used by NO kept cell are never added to the
    point pool (no orphan nodes).  The tributary map therefore sums to the
    exact meshed NET area, so area loads conserve q x net area.
    """
    c = [tuple(map(float, p)) for p in region.corners]
    lx = 0.5 * (_norm(_sub(c[1], c[0])) + _norm(_sub(c[2], c[3])))
    ly = 0.5 * (_norm(_sub(c[3], c[0])) + _norm(_sub(c[2], c[1])))
    nx = max(1, round(lx / region.mesh_size))
    ny = max(1, round(ly / region.mesh_size))
    omitted = _omitted_cells(region, nx, ny)

    def bilinear(u: float, v: float) -> Vec3:
        return tuple(
            (1 - u) * (1 - v) * c[0][k] + u * (1 - v) * c[1][k]
            + u * v * c[2][k] + (1 - u) * v * c[3][k]
            for k in range(3))

    pts = [[bilinear(i / nx, j / ny) for j in range(ny + 1)]
           for i in range(nx + 1)]
    used = [[not omitted for j in range(ny + 1)] for i in range(nx + 1)]
    if omitted:
        used = [[False] * (ny + 1) for _ in range(nx + 1)]
        for i in range(nx):
            for j in range(ny):
                if (i, j) in omitted:
                    continue
                for di in (0, 1):
                    for dj in (0, 1):
                        used[i + di][j + dj] = True
    # same insertion order as the no-openings mesh (numbering stability)
    idx = [[pool.add(pts[i][j]) if used[i][j] else None
            for j in range(ny + 1)] for i in range(nx + 1)]

    trib: Dict[int, float] = {}
    for i in range(nx):
        for j in range(ny):
            if (i, j) in omitted:
                continue
            corners = (pts[i][j], pts[i + 1][j], pts[i + 1][j + 1],
                       pts[i][j + 1])
            nodes = (idx[i][j], idx[i + 1][j], idx[i + 1][j + 1],
                     idx[i][j + 1])
            quads.append(ShellQuad(region.uid, nodes))
            quarter = _polygon_area3d(list(corners)) / 4.0
            for n in nodes:
                trib[n] = trib.get(n, 0.0) + quarter
    return trib


# --------------------------------------------------------------------------- #
# frame member splitting at shell mesh nodes
# --------------------------------------------------------------------------- #
def _split_member(member: FrameMember, pool: _PointPool,
                  shell_pts: List[int],
                  model: Optional[BuildingModel] = None) -> List[Segment]:
    """Split a member at shell mesh nodes and (v0.11) Winkler-foundation nodes.

    Shell-edge compatibility cuts (a shell mesh node on the axis) and
    foundation discretization cuts (``foundation_segment_count`` equal
    subdivisions) are merged into one ordered station list; interior
    foundation cut points are inserted into the global pool.
    """
    pi, pj = tuple(map(float, member.pi)), tuple(map(float, member.pj))
    length = member.length
    u = tuple(d / length for d in _sub(pj, pi))
    # (t, point-index-or-None) cuts: shell nodes carry an index, foundation
    # cuts get a pool point created lazily below.
    cuts: List[Tuple[float, Optional[int]]] = []
    for p_idx in shell_pts:
        p = pool.points[p_idx]
        t = _dot(_sub(p, pi), u)
        if t <= _TOL or t >= length - _TOL:
            continue
        foot = (pi[0] + t * u[0], pi[1] + t * u[1], pi[2] + t * u[2])
        if _norm(_sub(p, foot)) < _TOL:
            cuts.append((t, p_idx))
    if model is not None:
        nseg = foundation_segment_count(model, member)
        for i in range(1, nseg):
            cuts.append((i * length / nseg, None))
    cuts.sort(key=lambda c: c[0])
    # drop cuts closer than tolerance to each other; create pool points for
    # foundation cuts that survive.
    filtered: List[Tuple[float, int]] = []
    for t, p_idx in cuts:
        if filtered and t - filtered[-1][0] <= _TOL:
            continue
        if p_idx is None:
            foot = (pi[0] + t * u[0], pi[1] + t * u[1], pi[2] + t * u[2])
            p_idx = pool.add(foot)
        filtered.append((t, p_idx))

    ni = pool.add(pi)
    nj = pool.add(pj)
    stations = [(0.0, ni)] + filtered + [(length, nj)]
    return [Segment(member.uid, k, stations[k][1], stations[k + 1][1],
                    x0=stations[k][0],
                    length=stations[k + 1][0] - stations[k][0])
            for k in range(len(stations) - 1)]


# --------------------------------------------------------------------------- #
# membrane slab two-way tributary distribution
# --------------------------------------------------------------------------- #
def _members_on_edge(model: BuildingModel, pa: Vec3, pb: Vec3
                     ) -> List[Tuple[FrameMember, float, float]]:
    """Members collinear with the edge line whose span overlaps [0, L].

    Returns (member, s_of_pi, s_of_pj) with s measured along pa -> pb.
    """
    length = _norm(_sub(pb, pa))
    u = tuple(d / length for d in _sub(pb, pa))
    found: List[Tuple[FrameMember, float, float]] = []
    for m in model.members:
        ss = []
        on_line = True
        for p in (tuple(map(float, m.pi)), tuple(map(float, m.pj))):
            t = _dot(_sub(p, pa), u)
            foot = (pa[0] + t * u[0], pa[1] + t * u[1], pa[2] + t * u[2])
            if _norm(_sub(p, foot)) > _TOL:
                on_line = False
                break
            ss.append(t)
        if not on_line or abs(ss[1] - ss[0]) < _TOL:
            continue
        lo, hi = min(ss), max(ss)
        if hi < _TOL or lo > length - _TOL:   # no overlap with the edge
            continue
        found.append((m, ss[0], ss[1]))
    return found


def _profile_value(bps: List[Tuple[float, float]], s: float) -> float:
    """Piecewise-linear interpolation of profile breakpoints at s."""
    for (s0, w0), (s1, w1) in zip(bps[:-1], bps[1:]):
        if s0 - 1e-12 <= s <= s1 + 1e-12:
            if s1 - s0 < 1e-12:
                return w1
            return w0 + (w1 - w0) * (s - s0) / (s1 - s0)
    return 0.0


def _profile_integral(bps: List[Tuple[float, float]]) -> float:
    return sum(0.5 * (w0 + w1) * (s1 - s0)
               for (s0, w0), (s1, w1) in zip(bps[:-1], bps[1:]))


def _membrane_distribution(model: BuildingModel, region: ShellRegion,
                           pool: _PointPool
                           ) -> Tuple[List[TributaryMemberLoad],
                                      Dict[int, float]]:
    """Two-way (45 degree) tributary distribution of a membrane slab's area
    load onto its edge beams, per unit q = 1 kPa.  Total conserved exactly.
    """
    c = [tuple(map(float, p)) for p in region.corners]
    area = region.area                        # kN per unit q
    if region.openings:
        # v0.5 documented approximation: the load TOTAL drops by the exact
        # opening area ratio, applied uniformly — the two-way tributary
        # SHAPE is unchanged (openings do not re-route membrane loads).
        ratio = region.opening_area / area
        warnings.warn(
            f"Membrane slab {region.uid!r}: openings reduce the "
            f"distributed load uniformly by the opening area ratio "
            f"{ratio:.4f}; the two-way tributary shape is unchanged "
            "(approximation)")
        area = region.net_area
    edges = [(c[k], c[(k + 1) % 4]) for k in range(4)]
    lens = [_norm(_sub(b, a)) for a, b in edges]
    lx = 0.5 * (lens[0] + lens[2])
    ly = 0.5 * (lens[1] + lens[3])
    depth = min(lx, ly) / 2.0                 # 45-degree tributary depth

    edge_members = [_members_on_edge(model, a, b) for a, b in edges]
    has_beam = [bool(em) for em in edge_members]

    def base_profile(length: float) -> List[Tuple[float, float]]:
        """w(s) = min(s, L - s, depth), kN/m per kPa."""
        if length <= 2.0 * depth + _TOL:
            return [(0.0, 0.0), (length / 2.0, min(length / 2.0, depth)),
                    (length, 0.0)]
        return [(0.0, 0.0), (depth, depth), (length - depth, depth),
                (length, 0.0)]

    profiles = [base_profile(lens[k]) for k in range(4)]
    base_tot = [_profile_integral(p) for p in profiles]
    corner_nodal: Dict[Vec3, float] = {}

    def add_corner(point: Vec3, w: float) -> None:
        corner_nodal[_key(point)] = corner_nodal.get(_key(point), 0.0) + w

    if not any(has_beam):
        warnings.warn(f"Membrane slab {region.uid!r}: no edge beams at all; "
                      "area load lumped to the 4 corner nodes")
        for p in c:
            add_corner(p, area / 4.0)
        profiles = [[(0.0, 0.0), (lens[k], 0.0)] for k in range(4)]
    else:
        extra = [0.0] * 4                     # uniform add-on per edge (kN/m)
        for k in range(4):
            if has_beam[k]:
                continue
            adj = [j for j in ((k - 1) % 4, (k + 1) % 4) if has_beam[j]]
            share = base_tot[k]
            if adj:
                warnings.warn(
                    f"Membrane slab {region.uid!r}: edge {k} has no beam; "
                    "its share moves to the adjacent edges' beams")
                weights = [base_tot[j] for j in adj]
                wsum = sum(weights) or float(len(adj))
                for j, wj in zip(adj, weights):
                    frac = (wj / wsum) if sum(weights) else 1.0 / len(adj)
                    extra[j] += share * frac / lens[j]
            else:
                warnings.warn(
                    f"Membrane slab {region.uid!r}: edge {k} and its "
                    "neighbours have no beams; share moved to its corners")
                add_corner(edges[k][0], share / 2.0)
                add_corner(edges[k][1], share / 2.0)
        for k in range(4):
            if has_beam[k]:
                profiles[k] = [(s, w + extra[k]) for s, w in profiles[k]]
            else:
                profiles[k] = [(0.0, 0.0), (lens[k], 0.0)]

    # exact conservation: scale beam-borne profiles so that
    # (beam loads) + (corner loads) == q * area
    nodal_tot = sum(corner_nodal.values())
    beam_target = area - nodal_tot
    beam_tot = sum(_profile_integral(p) for p in profiles)
    if beam_tot > 1e-12:
        f = beam_target / beam_tot
        profiles = [[(s, w * f) for s, w in p] for p in profiles]
    elif abs(beam_target) > 1e-9 * max(1.0, area):
        raise ValueError(f"Membrane slab {region.uid!r}: cannot conserve "
                         "area load (no beams and no corner nodes)")

    # ---- emit member loads (clipped to uncovered intervals per edge) ------
    loads: List[TributaryMemberLoad] = []
    applied = 0.0
    for k in range(4):
        if not has_beam[k]:
            continue
        prof = profiles[k]
        bp_s = [s for s, _ in prof]
        covered: List[Tuple[float, float]] = []   # disjoint, sorted

        def uncovered(lo: float, hi: float) -> List[Tuple[float, float]]:
            out, cur = [], lo
            for c0, c1 in sorted(covered):
                if c1 <= cur:
                    continue
                if c0 > hi:
                    break
                if c0 > cur:
                    out.append((cur, min(c0, hi)))
                cur = max(cur, c1)
                if cur >= hi:
                    break
            if cur < hi:
                out.append((cur, hi))
            return [(a0, b0) for a0, b0 in out if b0 - a0 > _TOL]

        for member, s_pi, s_pj in edge_members[k]:
            lo = max(0.0, min(s_pi, s_pj))
            hi = min(lens[k], max(s_pi, s_pj))
            for piece_lo, piece_hi in uncovered(lo, hi):
                covered.append((piece_lo, piece_hi))
                # split at profile breakpoints inside the piece
                pts = sorted({piece_lo, piece_hi}
                             | {s for s in bp_s if piece_lo < s < piece_hi})
                for s0, s1 in zip(pts[:-1], pts[1:]):
                    w0 = _profile_value(prof, s0)
                    w1 = _profile_value(prof, s1)
                    if abs(w0) < 1e-12 and abs(w1) < 1e-12:
                        continue
                    # map edge coords to member-length fractions
                    fr0 = (s0 - s_pi) / (s_pj - s_pi)
                    fr1 = (s1 - s_pi) / (s_pj - s_pi)
                    if fr0 > fr1:
                        fr0, fr1, w0, w1 = fr1, fr0, w1, w0
                    loads.append(TributaryMemberLoad(
                        member.uid, w0, w1,
                        min(max(fr0, 0.0), 1.0), min(max(fr1, 0.0), 1.0)))
                    applied += 0.5 * (w0 + w1) * (s1 - s0)
        # uncovered remainder of this edge -> its corner nodes
        residual = _profile_integral(prof) - sum(
            _integral_between(prof, c0, c1) for c0, c1 in covered)
        if abs(residual) > 1e-9 * max(1.0, area):
            warnings.warn(f"Membrane slab {region.uid!r}: edge {k} beams do "
                          f"not cover the full edge; {residual:.6g} kN/kPa "
                          "moved to the edge corner nodes")
            add_corner(edges[k][0], residual / 2.0)
            add_corner(edges[k][1], residual / 2.0)

    # ---- corner nodal loads must land on existing FE points ---------------
    nodal: Dict[int, float] = {}
    for ck, w in corner_nodal.items():
        if abs(w) < 1e-12:
            continue
        idx = pool.find(ck)
        if idx is None:
            raise ValueError(
                f"Membrane slab {region.uid!r}: needs a nodal load at corner "
                f"{ck} but no frame node exists there")
        nodal[idx] = nodal.get(idx, 0.0) + w
        applied += w

    if abs(applied - area) > 1e-8 * max(1.0, area):
        raise AssertionError(
            f"Membrane slab {region.uid!r}: tributary distribution lost load "
            f"(applied {applied!r} kN/kPa vs area {area!r} m^2)")
    return loads, nodal


def _integral_between(bps: List[Tuple[float, float]], lo: float,
                      hi: float) -> float:
    """Integral of a piecewise-linear profile over [lo, hi]."""
    pts = sorted({lo, hi} | {s for s, _ in bps if lo < s < hi})
    return sum(0.5 * (_profile_value(bps, s0) + _profile_value(bps, s1))
               * (s1 - s0) for s0, s1 in zip(pts[:-1], pts[1:]))


# --------------------------------------------------------------------------- #
# v0.22 auto edge constraints (the ETABS "zipper")
# --------------------------------------------------------------------------- #
def edge_tie_chains(mesh: "MeshedModel"
                    ) -> List[Tuple[str, int, int, List[int]]]:
    """Hanging-node chains for the v0.22 auto edge constraints.

    A HANGING NODE is a mesh point lying strictly INSIDE a shell element
    edge (within the 1e-6 pool tolerance) without being one of that edge's
    end nodes — the classic T-junction of two shell regions meshed at
    different sizes, or a frame member end landing mid-edge.  Candidates
    are ALL pool points (shell mesh nodes AND frame nodes); edges are all
    quad edges, deduplicated by their (unordered) end-node pair, each
    keeping the region uid of the first quad that contributed it (the tie
    stiffness is sized from that region's shell section).

    Returns ``[(region_uid, end_a, end_b, [hanging point indices sorted
    by position along the edge]), ...]`` — only edges that actually have
    hanging nodes, in deterministic (quad, edge) discovery order.
    Structured meshing guarantees a region's own nodes never fall strictly
    inside its own edges, so every entry is a genuine interface.
    """
    edges: Dict[Tuple[int, int], Tuple[str, int, int]] = {}
    order: List[Tuple[int, int]] = []
    for quad in mesh.quads:
        n = quad.nodes
        for k in range(4):
            a, b = n[k], n[(k + 1) % 4]
            key = (a, b) if a < b else (b, a)
            if key not in edges:
                edges[key] = (quad.region, a, b)
                order.append(key)

    pts = mesh.points
    chains: List[Tuple[str, int, int, List[int]]] = []
    for key in order:
        region_uid, a, b = edges[key]
        pa, pb = pts[a], pts[b]
        d = _sub(pb, pa)
        length = _norm(d)
        u = tuple(c / length for c in d)
        lo = (min(pa[0], pb[0]) - _TOL, min(pa[1], pb[1]) - _TOL,
              min(pa[2], pb[2]) - _TOL)
        hi = (max(pa[0], pb[0]) + _TOL, max(pa[1], pb[1]) + _TOL,
              max(pa[2], pb[2]) + _TOL)
        hanging: List[Tuple[float, int]] = []
        for idx, p in enumerate(pts):
            if idx == a or idx == b:
                continue
            if not (lo[0] <= p[0] <= hi[0] and lo[1] <= p[1] <= hi[1]
                    and lo[2] <= p[2] <= hi[2]):
                continue                    # cheap bounding-box reject
            t = _dot(_sub(p, pa), u)
            if t <= _TOL or t >= length - _TOL:
                continue
            foot = (pa[0] + t * u[0], pa[1] + t * u[1], pa[2] + t * u[2])
            if _norm(_sub(p, foot)) < _TOL:
                hanging.append((t, idx))
        if hanging:
            hanging.sort()
            chains.append((region_uid, a, b, [idx for _, idx in hanging]))
    return chains


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def mesh_model(model: BuildingModel) -> MeshedModel:
    """Mesh all shell regions and reconcile frame members with the mesh."""
    pool = _PointPool()
    # member endpoints FIRST, in model order (keeps v0.1 node numbering when
    # the model has no shells)
    for m in model.members:
        pool.add(m.pi)
        pool.add(m.pj)
    # v0.5 links: endpoints join the pool (merged with coincident nodes)
    for lk in getattr(model, "links", []):
        pool.add(lk.pi)
        pool.add(lk.pj)

    quads: List[ShellQuad] = []
    region_trib: Dict[str, Dict[int, float]] = {}
    for region in model.shells:
        if region.behavior == "shell":
            region_trib[region.uid] = _mesh_region(region, pool, quads)

    shell_pts = sorted({n for q in quads for n in q.nodes})
    segments = {m.uid: _split_member(m, pool, shell_pts, model)
                for m in model.members}

    membrane_loads: Dict[str, List[TributaryMemberLoad]] = {}
    membrane_nodal: Dict[str, Dict[int, float]] = {}
    for region in model.shells:
        if region.behavior == "membrane":
            loads, nodal = _membrane_distribution(model, region, pool)
            membrane_loads[region.uid] = loads
            membrane_nodal[region.uid] = nodal

    return MeshedModel(model=model, points=list(pool.points),
                       segments=segments, quads=quads,
                       region_trib=region_trib,
                       membrane_loads=membrane_loads,
                       membrane_nodal=membrane_nodal)
