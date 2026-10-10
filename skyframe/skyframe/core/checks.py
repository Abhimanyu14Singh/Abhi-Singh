"""ETABS-style *Analyze > Check Model* and stability diagnostics.

Two entry points:

* :func:`check_model` — a pure-geometry / data audit of a
  :class:`~skyframe.core.model.BuildingModel` that REPORTS EVERY issue it
  finds (``BuildingModel.validate`` stops at the first).  Proximity and
  intersection searches use a uniform spatial hash, so the cost is roughly
  linear in the number of objects.
* :func:`stability_diagnostics` — builds the OpenSees domain through the
  engine's normal elastic build path (no case is solved), extracts the
  assembled tangent stiffness ``K`` (dense ``FullGeneral`` system), and
  finds the mechanisms (near-zero eigenvalues of the Jacobi-scaled ``K``),
  WHERE they are (joint + DOF participation, rigid-diaphragm slaves
  expanded from their master), the condition number and the ETABS/SAP
  "maximum diagonal ratio" ``K_ii / D_ii`` of the ``LDL^T`` factorization.

Every issue is a dict ``{"severity": "error"|"warning"|"info", "code",
"message", "objects": [uids/names], "location": [x, y, z] | None}``; the
codes are listed in CONTRACT.md ("Check Model and stability diagnostics").
"""

from __future__ import annotations

import contextlib
import math
import time
from collections import defaultdict
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from skyframe.core import framemesh as _fm
from skyframe.core.model import BuildingModel

Vec3 = Tuple[float, float, float]

DEFAULT_TOLERANCE = 1e-3            # m
PLANAR_TOL = 1e-6                   # m, BuildingModel planarity tolerance
ASPECT_WARN = 4.0
ASPECT_ERROR = 10.0
STABILITY_MAX_DOFS = 3000           # dense-eigen size cap (equations)
STABILITY_MAX_DOFS_LIMIT = 6000     # hard ceiling for the request override
MECHANISM_REL_TOL = 1e-12           # scaled-eigenvalue threshold (12 digits)
DIAG_RATIO_WARN = 1e8               # ETABS-style ill-conditioning warning
COND_WARN = 1e12                    # condition-number warning
MAX_MODES_REPORTED = 10
MAX_OBJECTS_LISTED = 50

DOF_LABELS = ("UX", "UY", "UZ", "RX", "RY", "RZ")

SEVERITIES = ("error", "warning", "info")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _issue(severity: str, code: str, message: str,
           objects: Iterable = (), location=None) -> dict:
    objs = []
    for o in objects:
        if o not in objs:
            objs.append(o)
    return {"severity": severity, "code": code, "message": message,
            "objects": objs[:MAX_OBJECTS_LISTED],
            "location": (None if location is None
                         else [round(float(v), 6) for v in location])}


def _key(p) -> Vec3:
    """Engine node-dedup key (coordinates rounded to 1e-6)."""
    return (round(float(p[0]), 6), round(float(p[1]), 6), round(float(p[2]), 6))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _fmt(p) -> str:
    return "(" + ", ".join(f"{float(v):.4g}" for v in p) + ")"


class _UF:
    """Union-find over hashable items."""

    def __init__(self):
        self.parent: Dict = {}

    def add(self, a):
        self.parent.setdefault(a, a)

    def find(self, a):
        self.parent.setdefault(a, a)
        root = a
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[a] != root:
            self.parent[a], a = root, self.parent[a]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


class _SpatialHash:
    """Uniform grid of cubic cells; boxes are registered in every cell they
    overlap.  Boxes spanning more than ``max_cells`` cells go to an
    ``oversize`` list that every query returns (keeps pathological long
    members from blowing up the table)."""

    def __init__(self, cell: float, max_cells: int = 4096):
        self.cell = max(float(cell), 1e-9)
        self.cells: Dict[Tuple[int, int, int], List[int]] = defaultdict(list)
        self.oversize: List[int] = []
        self.max_cells = max_cells

    def _rng(self, lo, hi):
        c = self.cell
        return [range(int(math.floor(lo[k] / c)),
                      int(math.floor(hi[k] / c)) + 1) for k in range(3)]

    def insert(self, idx: int, lo, hi) -> None:
        rx, ry, rz = self._rng(lo, hi)
        if len(rx) * len(ry) * len(rz) > self.max_cells:
            self.oversize.append(idx)
            return
        for i in rx:
            for j in ry:
                for k in rz:
                    self.cells[(i, j, k)].append(idx)

    def query(self, lo, hi) -> set:
        rx, ry, rz = self._rng(lo, hi)
        out = set(self.oversize)
        if len(rx) * len(ry) * len(rz) > self.max_cells:
            for v in self.cells.values():
                out.update(v)
            return out
        for i in rx:
            for j in ry:
                for k in rz:
                    v = self.cells.get((i, j, k))
                    if v:
                        out.update(v)
        return out

    def pairs(self) -> set:
        """All (a, b) index pairs, a < b, sharing at least one cell."""
        out = set()
        for v in self.cells.values():
            if len(v) < 2:
                continue
            vs = sorted(set(v))
            for x in range(len(vs)):
                for y in range(x + 1, len(vs)):
                    out.add((vs[x], vs[y]))
        if self.oversize:
            every = set()
            for v in self.cells.values():
                every.update(v)
            every.update(self.oversize)
            for o in self.oversize:
                for e in every:
                    if e != o:
                        out.add((min(o, e), max(o, e)))
        return out


def _seg_closest(p1, q1, p2, q2) -> Tuple[float, float, Vec3, Vec3]:
    """Closest points of segments p1q1 / p2q2 (Ericson, RTCD 5.1.9).

    Returns (s, t, c1, c2) with s, t in [0, 1] the fractions along each.
    """
    d1, d2, r = _sub(q1, p1), _sub(q2, p2), _sub(p1, p2)
    a, e, f = _dot(d1, d1), _dot(d2, d2), _dot(d2, r)
    c, b = _dot(d1, r), _dot(d1, d2)
    denom = a * e - b * b

    def clamp(v):
        return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)

    if denom > 1e-14 * a * e:
        s = clamp((b * f - c * e) / denom)
    else:
        s = 0.0
    t = (b * s + f) / e
    if t < 0.0:
        t, s = 0.0, clamp(-c / a)
    elif t > 1.0:
        t, s = 1.0, clamp((b - c) / a)
    return s, t, _add(p1, _scale(d1, s)), _add(p2, _scale(d2, t))


def _point_seg_dist(p, a, b) -> Tuple[float, float]:
    """(distance, fraction t along a->b) of the closest point to p."""
    ab = _sub(b, a)
    L2 = _dot(ab, ab)
    t = 0.0 if L2 <= 0.0 else max(0.0, min(1.0, _dot(_sub(p, a), ab) / L2))
    return _norm(_sub(p, _add(a, _scale(ab, t)))), t


def _newell_normal(pts: Sequence[Vec3]) -> Vec3:
    n = [0.0, 0.0, 0.0]
    m = len(pts)
    for i in range(m):
        a, b = pts[i], pts[(i + 1) % m]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return (n[0], n[1], n[2])


def _plane_basis(normal: Vec3) -> Tuple[Vec3, Vec3, Vec3]:
    nn = _norm(normal)
    n = _scale(normal, 1.0 / nn) if nn > 0 else (0.0, 0.0, 1.0)
    ref = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = _cross(n, ref)
    u = _scale(u, 1.0 / _norm(u))
    v = _cross(n, u)
    return n, u, v


def _seg2_intersect(a, b, c, d, eps=1e-12) -> bool:
    """Proper 2D intersection of segments ab and cd (shared ends excluded)."""
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    return (o1 * o2 < -eps) and (o3 * o4 < -eps)


def _point_in_poly2(p, poly, tol) -> bool:
    """Point inside (or within tol of the boundary of) a 2D polygon."""
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        d, _t = _point_seg_dist((p[0], p[1], 0.0), (a[0], a[1], 0.0),
                                (b[0], b[1], 0.0))
        if d <= tol:
            return True
    inside = False
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if p[0] < x:
                inside = not inside
    return inside


# --------------------------------------------------------------------------- #
# lenient model loading
# --------------------------------------------------------------------------- #
class _UncheckedModel(BuildingModel):
    """BuildingModel whose ``validate`` is a no-op (lenient loading only)."""

    def validate(self) -> None:          # noqa: D401 - intentional no-op
        return None


def load_model_lenient(d: dict) -> BuildingModel:
    """Rebuild a model dict WITHOUT the final cross-reference validation, so
    Check Model can report every defect instead of the first.  Structural
    parse errors (missing keys, wrong types) still raise."""
    mdl = _UncheckedModel.from_dict(d)
    out = BuildingModel.__new__(BuildingModel)
    out.__dict__.update(mdl.__dict__)
    return out


# --------------------------------------------------------------------------- #
# Check Model
# --------------------------------------------------------------------------- #
class _Ctx:
    """Shared geometry tables for the checks."""

    def __init__(self, model: BuildingModel, tol: float):
        self.model = model
        self.tol = tol
        self.issues: List[dict] = []
        # joints: exact engine keys
        self.jkeys: List[Vec3] = []
        self.jidx: Dict[Vec3, int] = {}
        self.owners: Dict[int, List[str]] = defaultdict(list)
        self.frame_ends: Dict[str, Tuple[int, int]] = {}
        self.link_ends: Dict[str, Tuple[int, int]] = {}
        self.shell_corners: Dict[str, List[int]] = {}
        self.shell_joints: Dict[str, set] = defaultdict(set)
        self.flagged: set = set()        # object ids with a specific error

    def add(self, severity, code, message, objects=(), location=None):
        iss = _issue(severity, code, message, objects, location)
        self.issues.append(iss)
        if severity == "error":
            self.flagged.update(iss["objects"])

    def joint(self, p, owner: Optional[str] = None) -> int:
        k = _key(p)
        j = self.jidx.get(k)
        if j is None:
            j = len(self.jkeys)
            self.jkeys.append(k)
            self.jidx[k] = j
        if owner is not None and owner not in self.owners[j]:
            self.owners[j].append(owner)
        return j


def _collect_joints(ctx: _Ctx) -> None:
    m = ctx.model
    for mem in m.members:
        ctx.frame_ends[mem.uid] = (ctx.joint(mem.pi, mem.uid),
                                   ctx.joint(mem.pj, mem.uid))
    for lk in m.links:
        ctx.link_ends[lk.uid] = (ctx.joint(lk.pi, lk.uid),
                                 ctx.joint(lk.pj, lk.uid))
    for r in m.shells:
        ctx.shell_corners[r.uid] = [ctx.joint(c, r.uid) for c in r.corners]
        ctx.shell_joints[r.uid].update(ctx.shell_corners[r.uid])


def _check_duplicate_uids(ctx: _Ctx) -> None:
    for label, objs in (("frame", ctx.model.members),
                        ("shell", ctx.model.shells),
                        ("link", ctx.model.links)):
        seen: Dict[str, int] = defaultdict(int)
        for o in objs:
            seen[o.uid] += 1
        for uid, n in seen.items():
            if n > 1:
                ctx.add("error", "DUPLICATE_UID",
                        f"{n} {label} objects share the uid {uid!r}", [uid])


def _check_coincident_joints(ctx: _Ctx) -> _UF:
    """Distinct joints closer than tol -> JOINT_COINCIDENT.  Returns the
    tolerance-merged union-find over joint indices (for duplicate checks)."""
    tol = ctx.tol
    uf = _UF()
    grid = _SpatialHash(max(tol, 1e-9) * 2.0)
    for j, p in enumerate(ctx.jkeys):
        uf.add(j)
        grid.insert(j, p, p)
    for j, p in enumerate(ctx.jkeys):
        lo = _sub(p, (tol, tol, tol))
        hi = _add(p, (tol, tol, tol))
        for k in grid.query(lo, hi):
            if k > j and math.dist(p, ctx.jkeys[k]) <= tol:
                uf.union(j, k)
    groups: Dict[int, List[int]] = defaultdict(list)
    for j in range(len(ctx.jkeys)):
        groups[uf.find(j)].append(j)
    for js in groups.values():
        if len(js) < 2:
            continue
        js.sort()
        objs = [o for j in js for o in ctx.owners[j]]
        dmax = max(math.dist(ctx.jkeys[a], ctx.jkeys[b])
                   for a in js for b in js)
        ctx.add("warning", "JOINT_COINCIDENT",
                f"{len(js)} joints within {dmax:.3g} m of each other near "
                f"{_fmt(ctx.jkeys[js[0]])} are NOT merged (the analysis "
                f"connects only coincident points); merge them",
                objs, ctx.jkeys[js[0]])
    return uf


def _check_frames(ctx: _Ctx, juf: _UF) -> None:
    m, tol = ctx.model, ctx.tol
    live: List = []
    dup_key: Dict[frozenset, List[str]] = defaultdict(list)
    for mem in m.members:
        L = math.dist(mem.pi, mem.pj)
        if L < tol:
            ctx.add("error", "FRAME_ZERO_LENGTH",
                    f"Frame {mem.uid} has (near) zero length {L:.3g} m "
                    f"(< {tol:g} m)", [mem.uid], mem.pi)
            continue
        i, j = ctx.frame_ends[mem.uid]
        dup_key[frozenset((juf.find(i), juf.find(j)))].append(mem.uid)
        live.append(mem)
    dup_of: Dict[str, frozenset] = {}
    for k, uids in dup_key.items():
        for u in uids:
            dup_of[u] = k
        if len(uids) > 1:
            mem = next(x for x in m.members if x.uid == uids[0])
            mid = _scale(_add(mem.pi, mem.pj), 0.5)
            ctx.add("error", "FRAME_DUPLICATE",
                    f"Frames {', '.join(uids)} connect the same two joints",
                    uids, mid)
    if len(live) < 2:
        return
    lens = sorted(math.dist(x.pi, x.pj) for x in live)
    cell = max(lens[len(lens) // 2], 10.0 * tol)
    grid = _SpatialHash(cell)
    for idx, mem in enumerate(live):
        lo = tuple(min(mem.pi[k], mem.pj[k]) - tol for k in range(3))
        hi = tuple(max(mem.pi[k], mem.pj[k]) + tol for k in range(3))
        grid.insert(idx, lo, hi)
    reported: set = set()
    for a, b in sorted(grid.pairs()):
        A, B = live[a], live[b]
        if dup_of.get(A.uid) == dup_of.get(B.uid):
            continue
        pa, qa = tuple(map(float, A.pi)), tuple(map(float, A.pj))
        pb, qb = tuple(map(float, B.pi)), tuple(map(float, B.pj))
        # quick bbox reject
        if any(min(pa[k], qa[k]) - tol > max(pb[k], qb[k])
               or min(pb[k], qb[k]) - tol > max(pa[k], qa[k])
               for k in range(3)):
            continue
        da, db = _sub(qa, pa), _sub(qb, pb)
        La, Lb = _norm(da), _norm(db)
        sin = _norm(_cross(da, db)) / (La * Lb)
        if sin < 1e-6:
            # parallel: collinear overlap?
            off = _norm(_cross(_sub(pb, pa), da)) / La
            if off > tol:
                continue
            ua = _scale(da, 1.0 / La)
            t0, t1 = sorted((_dot(_sub(pb, pa), ua), _dot(_sub(qb, pa), ua)))
            ov = min(La, t1) - max(0.0, t0)
            if ov > tol:
                key = ("ov", A.uid, B.uid)
                if key not in reported:
                    reported.add(key)
                    loc = _add(pa, _scale(ua, 0.5 * (max(0.0, t0)
                                                     + min(La, t1))))
                    ctx.add("error", "FRAME_OVERLAP",
                            f"Frames {A.uid} and {B.uid} overlap collinearly "
                            f"over {ov:.4g} m", [A.uid, B.uid], loc)
            continue
        s, t, c1, c2 = _seg_closest(pa, qa, pb, qb)
        if math.dist(c1, c2) > tol:
            continue
        end_a = s * La <= tol or (1.0 - s) * La <= tol
        end_b = t * Lb <= tol or (1.0 - t) * Lb <= tol
        loc = _scale(_add(c1, c2), 0.5)
        if end_a and end_b:
            continue                     # shared / coincident joint
        # frame auto mesh (core.framemesh): connected crossings / joints
        # on span are divided by the analysis mesh -> no warning
        auto_ok = math.dist(c1, c2) <= _fm.AUTO_MESH_TOL
        if not end_a and not end_b:
            if auto_ok and _fm.connects_intersection(m, A, B):
                continue
            ctx.add("warning", "FRAME_INTERSECTION",
                    f"Frames {A.uid} and {B.uid} cross at {_fmt(loc)} "
                    f"without a shared joint (they are NOT connected there; "
                    f"divide both frames at the intersection)",
                    [A.uid, B.uid], loc)
        else:
            span, end = (A, B) if not end_a else (B, A)
            if auto_ok and _fm.connects_joints_on_span(m, span):
                continue
            ctx.add("warning", "FRAME_JOINT_ON_SPAN",
                    f"An end of frame {end.uid} lies on the span of frame "
                    f"{span.uid} at {_fmt(loc)} but {span.uid} is not divided "
                    f"there (no connectivity)", [end.uid, span.uid], loc)


def _polygon_geometry(r) -> dict:
    """Plane basis + 2D outline of a polygon (3 or 5+ corner) region."""
    c = [tuple(map(float, p)) for p in r.corners]
    nrm = _newell_normal(c)
    n_c = len(c)
    edges = [math.dist(c[i], c[(i + 1) % n_c]) for i in range(n_c)]
    if _norm(nrm) < 1e-14:
        crs = [_cross(_sub(c[(k + 1) % n_c], c[k]), _sub(c[k - 1], c[k]))
               for k in range(n_c)]
        basis_n = max(crs, key=_norm)
        if _norm(basis_n) < 1e-14:
            basis_n = (0.0, 0.0, 1.0)
    else:
        basis_n = nrm
    n, u, v = _plane_basis(basis_n)
    o = c[0]
    pts2 = [(_dot(_sub(p, o), u), _dot(_sub(p, o), v)) for p in c]
    return {"c": c, "n": n, "u": u, "v": v, "o": o, "pts2": pts2,
            "edges": edges, "area": 0.5 * _norm(nrm), "polygon": True}


def _check_polygon_shell(ctx: "_Ctx", r, geo: Dict[str, dict],
                         dup_key) -> None:
    """Check Model for polygon regions: SHELL_CORNER_COUNT (< 3),
    SHELL_ZERO_AREA, SHELL_WARPED (vertex off the best-fit plane),
    SHELL_SELF_INTERSECTING (any two edges cross/touch); concave polygons
    are VALID (the polygon auto mesh handles them)."""
    from skyframe.core.polymesh import polygon_problems
    probs = polygon_problems(r.corners)
    if probs and probs[0][0] == "SHELL_CORNER_COUNT":
        ctx.add("error", "SHELL_CORNER_COUNT",
                f"Shell {r.uid} has {len(r.corners)} corners (at least 3 "
                "are required)", [r.uid],
                r.corners[0] if r.corners else None)
        return
    g = _polygon_geometry(r)
    fatal = False
    for code, msg, loc in probs:
        ctx.add("error", code, f"Shell {r.uid} {msg}", [r.uid], loc)
        fatal = fatal or code == "SHELL_ZERO_AREA"
    if fatal:
        return
    geo[r.uid] = g
    if any(code == "SHELL_SELF_INTERSECTING" for code, _, _ in probs):
        return
    dup_key[tuple(sorted({ctx._juf.find(j)
                          for j in ctx.shell_corners[r.uid]}))].append(r.uid)


def _shell_geometry(r) -> Optional[dict]:
    c = [tuple(map(float, p)) for p in r.corners]
    if len(c) != 4:
        return None
    nrm = _newell_normal(c)
    basis_n = nrm
    # a bow-tie has a ~zero Newell normal: take the plane from the largest
    # corner cross product instead
    crs = [_cross(_sub(c[(k + 1) % 4], c[k]), _sub(c[k - 1], c[k]))
           for k in range(4)]
    big = max(crs, key=_norm)
    if _norm(nrm) < 0.5 * _norm(big):
        basis_n = big
    n, u, v = _plane_basis(basis_n)
    o = c[0]
    pts2 = [(_dot(_sub(p, o), u), _dot(_sub(p, o), v)) for p in c]
    edges = [math.dist(c[i], c[(i + 1) % 4]) for i in range(4)]
    return {"c": c, "n": n, "u": u, "v": v, "o": o, "pts2": pts2,
            "edges": edges, "area": 0.5 * _norm(nrm)}


def _check_shells(ctx: _Ctx, juf: _UF) -> Dict[str, dict]:
    m, tol = ctx.model, ctx.tol
    geo: Dict[str, dict] = {}
    dup_key: Dict[Tuple, List[str]] = defaultdict(list)
    ctx._juf = juf
    for r in m.shells:
        g = _shell_geometry(r)
        if g is None:
            _check_polygon_shell(ctx, r, geo, dup_key)
            continue
        c, edges = g["c"], g["edges"]
        cen = _scale(_add(_add(c[0], c[1]), _add(c[2], c[3])), 0.25)
        lmax = max(edges)
        p2 = g["pts2"]
        if (_seg2_intersect(p2[0], p2[1], p2[2], p2[3])
                or _seg2_intersect(p2[1], p2[2], p2[3], p2[0])):
            ctx.add("error", "SHELL_SELF_INTERSECTING",
                    f"Shell {r.uid} is self-intersecting (bow-tie corner "
                    "order)", [r.uid], cen)
            geo[r.uid] = g
            continue
        if g["area"] <= max(1e-9, tol * lmax) or min(edges) < tol:
            ctx.add("error", "SHELL_ZERO_AREA",
                    f"Shell {r.uid} is degenerate (area {g['area']:.3g} m^2, "
                    f"shortest edge {min(edges):.3g} m)", [r.uid], cen)
            continue
        geo[r.uid] = g
        dup_key[tuple(sorted({juf.find(j)
                              for j in ctx.shell_corners[r.uid]}))].append(
            r.uid)
        # warp: corner 3 off the plane of corners 0-1-2 (model definition)
        n3 = _cross(_sub(c[1], c[0]), _sub(c[2], c[0]))
        nn = _norm(n3)
        warp = (abs(_dot(_sub(c[3], c[0]), n3)) / nn) if nn > 1e-12 else \
            float("inf")
        if warp > PLANAR_TOL:
            ctx.add("error", "SHELL_WARPED",
                    f"Shell {r.uid} corners are not planar (corner 4 is "
                    f"{warp:.3g} m off the plane of corners 1-3; analysis "
                    f"requires <= {PLANAR_TOL:g} m)", [r.uid], c[3])
        crs = []
        for i in range(4):
            a, b, cc = p2[i - 1], p2[i], p2[(i + 1) % 4]
            crs.append((b[0] - a[0]) * (cc[1] - b[1])
                       - (b[1] - a[1]) * (cc[0] - b[0]))
        scale = lmax * lmax
        if (any(x > 1e-9 * scale for x in crs)
                and any(x < -1e-9 * scale for x in crs)):
            k = min(range(4), key=lambda i: crs[i]
                    * (1 if sum(crs) > 0 else -1))
            ctx.add("error", "SHELL_CONCAVE",
                    f"Shell {r.uid} is concave at corner {k + 1}",
                    [r.uid], c[k])
        # aspect ratio: meshed element (shell) or region (membrane)
        lx = 0.5 * (edges[0] + edges[2])
        ly = 0.5 * (edges[1] + edges[3])
        if r.behavior == "shell" and r.mesh_size > 0:
            from .shellopts import structured_divisions  # mesh options
            nx, ny = structured_divisions(r, lx, ly)
            ex, ey = lx / nx, ly / ny
            ar = max(ex, ey) / max(min(ex, ey), 1e-12)
            what = f"mesh elements ({ex:.3g} x {ey:.3g} m)"
        else:
            ar = max(edges) / max(min(edges), 1e-12)
            what = "region edges"
        if ar > ASPECT_ERROR:
            ctx.add("error", "SHELL_ASPECT_RATIO",
                    f"Shell {r.uid}: aspect ratio {ar:.3g} of its {what} "
                    f"exceeds {ASPECT_ERROR:g}", [r.uid], cen)
        elif ar > ASPECT_WARN:
            ctx.add("warning", "SHELL_ASPECT_RATIO",
                    f"Shell {r.uid}: aspect ratio {ar:.3g} of its {what} "
                    f"exceeds {ASPECT_WARN:g}", [r.uid], cen)
    for uids in dup_key.values():
        if len(uids) > 1:
            g = geo[uids[0]]
            ctx.add("error", "SHELL_DUPLICATE",
                    f"Shells {', '.join(uids)} share the same corners",
                    uids, g["c"][0])
    # joints lying on a shell surface connect to it (meshed shells merge
    # coincident nodes; membranes deliver load to their boundary)
    if geo and ctx.jkeys:
        sizes = [max(g["edges"]) for g in geo.values()]
        grid = _SpatialHash(max(sorted(sizes)[len(sizes) // 2], 10 * tol))
        for j, p in enumerate(ctx.jkeys):
            grid.insert(j, p, p)
        for uid, g in geo.items():
            c = g["c"]
            lo = tuple(min(p[k] for p in c) - tol for k in range(3))
            hi = tuple(max(p[k] for p in c) + tol for k in range(3))
            for j in grid.query(lo, hi):
                if j in ctx.shell_joints[uid]:
                    continue
                p = ctx.jkeys[j]
                d = _sub(p, g["o"])
                if abs(_dot(d, g["n"])) > tol:
                    continue
                if _point_in_poly2((_dot(d, g["u"]), _dot(d, g["v"])),
                                   g["pts2"], tol):
                    ctx.shell_joints[uid].add(j)
    return geo


def _check_references(ctx: _Ctx) -> None:
    m = ctx.model
    for sec in m.sections.values():
        if sec.material not in m.materials:
            ctx.add("error", "SECTION_MATERIAL_MISSING",
                    f"Frame section {sec.name} references missing material "
                    f"{sec.material!r}", [sec.name])
    for sec in m.shell_sections.values():
        if sec.material not in m.materials:
            ctx.add("error", "SECTION_MATERIAL_MISSING",
                    f"Shell section {sec.name} references missing material "
                    f"{sec.material!r}", [sec.name])
    for mem in m.members:
        if mem.section not in m.sections:
            ctx.add("error", "FRAME_SECTION_MISSING",
                    f"Frame {mem.uid} references missing section "
                    f"{mem.section!r}", [mem.uid],
                    _scale(_add(mem.pi, mem.pj), 0.5))
    for r in m.shells:
        if r.behavior == "shell" and r.section not in m.shell_sections:
            ctx.add("error", "SHELL_SECTION_MISSING",
                    f"Shell {r.uid} references missing shell section "
                    f"{r.section!r}", [r.uid],
                    r.corners[0] if r.corners else None)


def _run_validators(ctx: _Ctx) -> None:
    """Per-object model validators, each isolated, reported as
    INVALID_DATA for objects not already flagged by a specific error."""
    m = ctx.model
    checks: List[Tuple[str, Callable[[], None]]] = []
    for mat in m.materials.values():
        checks.append((mat.name, lambda x=mat: m._validate_material(x)))
    for sec in m.sections.values():
        checks.append((sec.name, lambda x=sec: m._validate_section_mods(x)))
    if m.designer_sections:
        from skyframe.core.sections_designer import validate_designer_section
        for ds in m.designer_sections.values():
            checks.append((getattr(ds, "name", "designer"),
                           lambda x=ds: validate_designer_section(
                               x, m.materials)))
    for ss in m.shell_sections.values():
        def _ss(x=ss):
            if not (isinstance(x.mod, (int, float)) and math.isfinite(x.mod)
                    and x.mod > 0.0):
                raise ValueError(f"Shell section {x.name}: mod must be a "
                                 f"finite value > 0 (got {x.mod!r})")
            m._validate_layered(x)
        checks.append((ss.name, _ss))
    for mem in m.members:
        def _mv(x=mem):
            if not x.release_tokens() <= {"Mi", "Mj"}:
                raise ValueError(f"Member {x.uid}: bad releases "
                                 f"{x.releases!r}")
            m._validate_rigid_offsets(x)
            m._validate_foundation(x)
            m._validate_axial_limit(x)
            m._validate_member_hinges(x)
            m._validate_insertion(x)
        checks.append((mem.uid, _mv))
    for r in m.shells:
        checks.append((r.uid, lambda x=r: m._validate_shell(x)))
    for name, coll, fn in (
            ("rs", m.rs_cases, m._validate_rs_case),
            ("th", m.th_cases, m._validate_th_case),
            ("po", m.pushover_cases, m._validate_pushover_case),
            ("staged", m.staged_cases, m._validate_staged_case),
            ("buckling", m.buckling_cases, m._validate_buckling_case),
            ("spec", m.spectrum_functions, m._validate_spectrum_function),
            ("thf", m.th_functions, m._validate_th_function)):
        for k, obj in coll.items():
            checks.append((k, lambda x=obj, f=fn: f(x)))
    for cname, cb in m.rs_combos.items():
        checks.append((cname, lambda n=cname, x=cb: m._validate_rs_combo(n, x)))
    for cut in m.section_cuts:
        checks.append((cut.name, lambda x=cut: m._validate_section_cut(x)))
    for lk in m.links:
        checks.append((lk.uid, lambda x=lk: m._validate_link(x)))
    for k, sp in enumerate(m.spring_supports):
        checks.append((f"spring_support[{k}]",
                       lambda x=sp: m._validate_spring(x)))
    for k, ls in enumerate(m.line_springs):
        checks.append((f"line_spring[{k}]",
                       lambda x=ls: m._validate_line_spring(x)))
    checks.append(("diaphragm", m._validate_diaphragm))
    for g in m.effective_grids():
        checks.append((getattr(g, "name", "grid"),
                       lambda x=g: m._validate_grid(x)))
    for oid, fn in checks:
        if oid in ctx.flagged:
            continue
        try:
            fn()
        except Exception as exc:          # report, never raise
            ctx.add("error", "INVALID_DATA", str(exc), [oid])
    # model-level fields: the full validate() as a final net
    if not any(i["severity"] == "error" for i in ctx.issues):
        try:
            m.validate()
        except Exception as exc:
            ctx.add("error", "INVALID_DATA", str(exc), [])


def _support_joints(ctx: _Ctx, fe_keys: Callable[[], set]) -> set:
    """Joint indices that carry a support (restraint or grounded spring)."""
    m, tol = ctx.model, ctx.tol
    sup: set = set()

    def locate(p) -> Optional[int]:
        return ctx.jidx.get(_key(p))

    explicit = [s for s in m.supports]
    for k, s in enumerate(explicit):
        j = locate(s.point)
        if j is None:
            if _key(s.point) in fe_keys():
                continue                 # on a shell mesh node: fine
            ctx.add("error", "JOINT_UNCONNECTED",
                    f"Support {k} at {_fmt(s.point)} is not on any joint "
                    "(analysis fails: no FE node there)",
                    [f"support[{k}]"], s.point)
            continue
        if any(int(bool(r)) for r in s.restraints):
            sup.add(j)
    if not explicit and ctx.jkeys:
        zmin = min(p[2] for p in ctx.jkeys)
        sup.update(j for j, p in enumerate(ctx.jkeys)
                   if abs(p[2] - zmin) < 1e-6)
    for k, sp in enumerate(m.spring_supports):
        j = locate(sp.point)
        if j is None:
            if _key(sp.point) in fe_keys():
                continue
            ctx.add("warning", "JOINT_UNCONNECTED",
                    f"Spring support {k} at {_fmt(sp.point)} is not on any "
                    "joint (it springs an isolated node and supports nothing)",
                    [f"spring_support[{k}]"], sp.point)
            continue
        if (any(float(x) != 0.0 for x in sp.stiffness)
                or getattr(sp, "property", None) is not None):   # B9
            sup.add(j)
    for ls in m.line_springs:
        if not (ls.kz or ls.kx or ls.ky):
            continue
        for j, p in enumerate(ctx.jkeys):
            d, _t = _point_seg_dist(p, ls.p1, ls.p2)
            if d <= tol:
                sup.add(j)
    for r in m.shells:
        if r.area_spring and r.uid in ctx.shell_corners:
            sup.update(ctx.shell_joints[r.uid])
    for mem in m.members:
        if mem.foundation_k_line > 0.0 and mem.uid in ctx.frame_ends:
            sup.update(ctx.frame_ends[mem.uid])
    return sup


def _check_connectivity(ctx: _Ctx, sup: set) -> None:
    m, tol = ctx.model, ctx.tol
    uf = _UF()
    obj_joints: Dict[str, set] = {}
    for uid, (i, j) in ctx.frame_ends.items():
        obj_joints[uid] = {i, j}
    for uid, (i, j) in ctx.link_ends.items():
        obj_joints[uid] = {i, j}
    for uid, js in ctx.shell_joints.items():
        obj_joints[uid] = set(js)
    for uid, js in obj_joints.items():
        js = sorted(js)
        for j in js:
            uf.union(("j", j), ("o", uid))
    if not obj_joints:
        return
    if not sup:
        ctx.add("error", "NO_SUPPORTS",
                "The structure has no supports (no restrained joint and no "
                "grounded spring): it is unstable", [], None)
        return
    comps: Dict = defaultdict(list)
    for uid in obj_joints:
        comps[uf.find(("o", uid))].append(uid)
    supported_roots = {uf.find(("j", j)) for j in sup
                       if ("j", j) in uf.parent}
    kind = {**{u: "Frame" for u in ctx.frame_ends},
            **{u: "Link" for u in ctx.link_ends},
            **{u: "Shell" for u in ctx.shell_joints}}
    for root, uids in comps.items():
        if root in supported_roots:
            continue
        pts = [ctx.jkeys[j] for u in uids for j in obj_joints[u]]
        low = min(pts, key=lambda p: (p[2], p[0], p[1]))
        if len(uids) == 1:
            u = uids[0]
            code = {"Frame": "FRAME_UNCONNECTED", "Shell": "SHELL_UNCONNECTED",
                    "Link": "LINK_UNCONNECTED"}[kind[u]]
            ctx.add("error", code,
                    f"{kind[u]} {u} is not connected to any other object or "
                    "support (it floats)", [u], low)
        else:
            ctx.add("error", "STRUCTURE_UNSUPPORTED_PART",
                    f"{len(uids)} connected objects (e.g. "
                    f"{', '.join(sorted(uids)[:5])}) have no path to any "
                    "support", sorted(uids), low)
    # vertical load path per story
    joints_supported = {j for j in range(len(ctx.jkeys))
                        if ("j", j) in uf.parent
                        and uf.find(("j", j)) in supported_roots}
    by_z: Dict[float, List[int]] = defaultdict(list)
    for j, p in enumerate(ctx.jkeys):
        if ("j", j) in uf.parent:
            by_z[round(p[2], 6)].append(j)
    zmin = min(p[2] for p in ctx.jkeys)
    verticals: List[Tuple[float, float]] = []
    for mem in m.members:
        if abs(mem.pi[2] - mem.pj[2]) > tol:
            verticals.append((min(mem.pi[2], mem.pj[2]),
                              max(mem.pi[2], mem.pj[2])))
    for lk in m.links:
        if abs(lk.pi[2] - lk.pj[2]) > tol:
            verticals.append((min(lk.pi[2], lk.pj[2]),
                              max(lk.pi[2], lk.pj[2])))
    for r in m.shells:
        zs = [c[2] for c in r.corners]
        if zs and max(zs) - min(zs) > tol:
            verticals.append((min(zs), max(zs)))
    for st in m.stories:
        e = st.elevation
        js = [j for z, jl in by_z.items() if abs(z - e) <= tol for j in jl]
        if not js:
            ctx.add("info", "STORY_EMPTY",
                    f"Story {st.name} (elevation {e:g} m) has no joints",
                    [st.name], None)
            continue
        if e <= zmin + tol:
            continue
        if not any(j in joints_supported for j in js):
            ctx.add("error", "STORY_NO_SUPPORT_PATH",
                    f"Story {st.name} (elevation {e:g} m): no object at this "
                    "level is connected to a support (no vertical load path)",
                    [st.name], ctx.jkeys[js[0]])
        elif not any(lo < e - tol and hi >= e - tol for lo, hi in verticals):
            ctx.add("warning", "STORY_NO_VERTICAL_ELEMENTS",
                    f"Story {st.name} (elevation {e:g} m) has no column, "
                    "brace, wall or link reaching down from it",
                    [st.name], ctx.jkeys[js[0]])


def _check_loads(ctx: _Ctx, fe_keys: Callable[[], set]) -> None:
    m = ctx.model
    member_uids = {x.uid for x in m.members}
    shell_uids = {x.uid for x in m.shells}
    story_names = {s.name for s in m.stories}
    empty = set()
    for pat in m.patterns.values():
        n = (len(pat.member_udls) + len(pat.nodal_loads)
             + len(pat.story_forces) + len(pat.member_loads)
             + len(pat.area_loads) + len(pat.thermal_loads))
        if n == 0 and not pat.self_weight_factor:
            empty.add(pat.name)
            ctx.add("warning", "PATTERN_EMPTY",
                    f"Load pattern {pat.name} has no loads and no "
                    "self-weight", [pat.name])
        for ml in list(pat.member_udls) + list(pat.member_loads) \
                + list(pat.thermal_loads):
            if ml.member_uid not in member_uids:
                ctx.add("error", "LOAD_TARGET_MISSING",
                        f"Pattern {pat.name}: load on missing frame "
                        f"{ml.member_uid!r}", [pat.name, ml.member_uid])
        for al in pat.area_loads:
            if al.region_uid not in shell_uids:
                ctx.add("error", "LOAD_TARGET_MISSING",
                        f"Pattern {pat.name}: area load on missing shell "
                        f"{al.region_uid!r}", [pat.name, al.region_uid])
        for sf in pat.story_forces:
            if sf.story not in story_names:
                ctx.add("error", "LOAD_TARGET_MISSING",
                        f"Pattern {pat.name}: story force on missing story "
                        f"{sf.story!r}", [pat.name, sf.story])
        for k, nl in enumerate(pat.nodal_loads):
            if _key(nl.point) not in ctx.jidx and \
                    _key(nl.point) not in fe_keys():
                ctx.add("error", "JOINT_UNCONNECTED",
                        f"Pattern {pat.name}: nodal load {k} at "
                        f"{_fmt(nl.point)} is not on any joint",
                        [pat.name], nl.point)
    for k, nm in enumerate(m.nodal_masses):
        if _key(nm.point) not in ctx.jidx and _key(nm.point) not in fe_keys():
            ctx.add("warning", "JOINT_UNCONNECTED",
                    f"Nodal mass {k} at {_fmt(nm.point)} is not on any joint",
                    [f"nodal_mass[{k}]"], nm.point)
    for case in m.cases.values():
        active = {p: f for p, f in case.patterns.items() if f}
        if not active:
            ctx.add("warning", "CASE_NO_PATTERNS",
                    f"Load case {case.name} applies no pattern", [case.name])
        refs = list(case.patterns) + list(case.pdelta_gravity or {})
        for p in refs:
            if p not in m.patterns:
                ctx.add("error", "CASE_PATTERN_MISSING",
                        f"Load case {case.name} references missing pattern "
                        f"{p!r}", [case.name, p])
            elif p in empty:
                ctx.add("warning", "CASE_EMPTY_PATTERN",
                        f"Load case {case.name} references pattern {p} which "
                        "has no loads", [case.name, p])
    for combo in m.combos.values():
        if not combo.cases:
            ctx.add("warning", "COMBO_EMPTY",
                    f"Combination {combo.name} has no cases", [combo.name])
        from skyframe.core.combos_ext import MEMBER_KINDS, member_kind
        for c in combo.cases:
            if c in m.cases:
                continue
            kind = member_kind(m, c)
            if kind in MEMBER_KINDS:      # RS / TH / staged / nested combo
                continue
            if kind is not None:
                ctx.add("error", "COMBO_CASE_INVALID",
                        f"Combination {combo.name} references {c!r}, a "
                        f"{kind} case that cannot enter a load combination",
                        [combo.name, c])
            else:
                ctx.add("error", "COMBO_CASE_MISSING",
                        f"Combination {combo.name} references missing case "
                        f"{c!r}", [combo.name, c])


def check_model(model: BuildingModel,
                tolerance: float = DEFAULT_TOLERANCE) -> dict:
    """Run every Check Model test; never raises on model defects.

    Returns ``{"issues": [...], "summary": {...}}`` (see module doc).
    """
    t0 = time.perf_counter()
    tol = float(tolerance)
    if not (math.isfinite(tol) and tol > 0.0):
        raise ValueError("tolerance must be a finite value > 0")
    ctx = _Ctx(model, tol)
    _collect_joints(ctx)

    fe_cache: Dict[str, set] = {}

    def fe_keys() -> set:
        """Engine FE node keys (incl. shell mesh nodes), built lazily."""
        if "k" not in fe_cache:
            keys = set(ctx.jidx)
            try:
                from skyframe.core.mesh import mesh_model
                keys.update(_key(p) for p in mesh_model(model).points)
            except Exception:
                pass
            fe_cache["k"] = keys
        return fe_cache["k"]

    _check_duplicate_uids(ctx)
    _check_references(ctx)
    juf = _check_coincident_joints(ctx)
    _check_frames(ctx, juf)
    _check_shells(ctx, juf)
    sup = _support_joints(ctx, fe_keys)
    _check_connectivity(ctx, sup)
    _check_loads(ctx, fe_keys)
    _run_validators(ctx)

    order = {s: k for k, s in enumerate(SEVERITIES)}
    issues = sorted(ctx.issues, key=lambda i: order[i["severity"]])
    by_code: Dict[str, int] = defaultdict(int)
    for i in issues:
        by_code[i["code"]] += 1
    summary = {
        "errors": sum(1 for i in issues if i["severity"] == "error"),
        "warnings": sum(1 for i in issues if i["severity"] == "warning"),
        "info": sum(1 for i in issues if i["severity"] == "info"),
        "by_code": dict(sorted(by_code.items())),
        "ok": not any(i["severity"] == "error" for i in issues),
        "n_joints": len(ctx.jkeys),
        "n_frames": len(model.members),
        "n_shells": len(model.shells),
        "n_links": len(model.links),
        "n_supported_joints": len(sup),
        "tolerance_m": tol,
        "elapsed_s": round(time.perf_counter() - t0, 4),
    }
    return {"issues": issues, "summary": summary}


# --------------------------------------------------------------------------- #
# Stability diagnostics
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def _single_thread_blas():
    """Run the dense linear algebra with ONE OpenBLAS thread.

    The matrices here are small (<= a few thousand); multi-threaded
    OpenBLAS busy-waits badly when the cores are shared (a 500x500 eigh was
    measured at 23 s vs 0.1 s single-threaded under load).  Best effort:
    a no-op when the library/symbols cannot be found.
    """
    setters = []
    try:
        import ctypes
        paths = set()
        with open("/proc/self/maps") as fh:
            for line in fh:
                if "openblas" in line.lower():
                    paths.add(line.split()[-1])
        for p in paths:
            lib = ctypes.CDLL(p)
            for get_n, set_n in (
                    ("scipy_openblas_get_num_threads64_",
                     "scipy_openblas_set_num_threads64_"),
                    ("openblas_get_num_threads64_",
                     "openblas_set_num_threads64_"),
                    ("openblas_get_num_threads", "openblas_set_num_threads")):
                if hasattr(lib, get_n) and hasattr(lib, set_n):
                    prev = int(getattr(lib, get_n)())
                    getattr(lib, set_n)(1)
                    setters.append((getattr(lib, set_n), prev))
                    break
    except Exception:
        pass
    try:
        yield
    finally:
        for fn, prev in setters:
            try:
                fn(prev)
            except Exception:
                pass


def _node_objects(model: BuildingModel, asm) -> Dict[int, List[str]]:
    out: Dict[int, List[str]] = defaultdict(list)
    mesh = asm.mesh
    if mesh is not None:
        for uid, segs in mesh.segments.items():
            for s in segs:
                for idx in (s.ni, s.nj):
                    if uid not in out[idx + 1]:
                        out[idx + 1].append(uid)
        for q in mesh.quads:
            for idx in q.nodes:
                if q.region not in out[idx + 1]:
                    out[idx + 1].append(q.region)
    pos = {_key(c): t for t, c in asm.struct_coords.items()}
    for lk in model.links:
        for p in (lk.pi, lk.pj):
            t = pos.get(_key(p))
            if t is not None and lk.uid not in out[t]:
                out[t].append(lk.uid)
    return out


def _dof_map(asm, ops) -> Tuple[List[Tuple[int, int, int]],
                                 Dict[int, Tuple[int, List[int]]]]:
    """Map equations to physical (node, dof).

    Returns ``(direct, dia)``: ``direct`` = list of (eq, node tag, dof 0..5)
    for unconstrained DOFs (and the own free DOFs of MP-constrained nodes);
    ``dia`` = constrained node -> (retained node, constrained dofs 0-based).
    """
    direct: List[Tuple[int, int, int]] = []
    constrained: Dict[int, Tuple[int, List[int]]] = {}
    try:
        cnodes = list(ops.getConstrainedNodes() or [])
    except Exception:
        cnodes = []
    for c in cnodes:
        try:
            rn = ops.getRetainedNodes(c)
            rn = rn[0] if isinstance(rn, (list, tuple)) else rn
            cd = [int(d) - 1 for d in ops.getConstrainedDOFs(c)]
        except Exception:
            continue
        constrained[int(c)] = (int(rn), cd)
    for tag in sorted(asm.node_coords):
        try:
            ids = list(ops.nodeDOFs(tag))
        except Exception:
            continue
        if tag in constrained:
            own = [d for d in range(6) if d not in constrained[tag][1]]
            for k, d in enumerate(own):
                if k < len(ids) and ids[k] >= 0:
                    direct.append((ids[k], tag, d))
        else:
            for d, eq in enumerate(ids[:6]):
                if eq >= 0:
                    direct.append((eq, tag, d))
    return direct, constrained


def _physical(vec: np.ndarray, asm, direct, constrained,
              index: Dict[Tuple[int, int], int], nphys: int) -> np.ndarray:
    """Expand an equation-space vector to physical (node, dof) amplitudes."""
    out = np.zeros(nphys)
    for eq, tag, d in direct:
        out[index[(tag, d)]] = vec[eq]
    masters = set(asm.masters.values()) | set(
        (getattr(asm, "dia", {}).get("masters") or {}).values())
    for c, (r, cd) in constrained.items():
        cc = asm.node_coords.get(c)
        rc = asm.node_coords.get(r)
        if cc is None or rc is None:
            continue

        def rv(d):
            k = index.get((r, d))
            return out[k] if k is not None else 0.0
        for d in cd:
            k = index.get((c, d))
            if k is None:
                continue
            if r in masters:
                rz = rv(5)
                if d == 0:
                    out[k] = rv(0) - rz * (cc[1] - rc[1])
                elif d == 1:
                    out[k] = rv(1) + rz * (cc[0] - rc[0])
                else:
                    out[k] = rv(d)
            else:
                out[k] = rv(d)
    return out


def _rank(values: np.ndarray, phys_keys) -> List[int]:
    """Indices by participation (desc); ties (4 significant decimals) put
    translations before rotations, then lower node tags first."""
    return sorted(range(len(values)),
                  key=lambda i: (-round(float(values[i]), 4),
                                 phys_keys[i][1] >= 3, phys_keys[i][0],
                                 phys_keys[i][1]))


def _location_entry(tag, d, value, asm, objs, masters_rev) -> dict:
    p = asm.node_coords.get(tag, (0.0, 0.0, 0.0))
    entry = {"node": int(tag), "point": [round(float(v), 6) for v in p],
             "dof": DOF_LABELS[d], "participation": round(float(value), 4),
             "objects": list(objs.get(tag, []))[:MAX_OBJECTS_LISTED]}
    if tag in masters_rev:
        entry["diaphragm"] = masters_rev[tag]
    return entry


def _analyze_stiffness(result: dict, K: np.ndarray, model, asm, direct,
                       constrained, n_report: int) -> None:
    """Eigen / factorization diagnostics of the assembled K (fills
    ``result`` in place)."""
    n = K.shape[0]
    issues = result["issues"]
    K = 0.5 * (K + K.T)
    diag = np.diag(K).copy()
    objs = _node_objects(model, asm)
    masters_rev = {t: s for s, t in asm.masters.items()}

    # physical dof index (exclude diaphragm masters: their motion is shown
    # on the slaved joints)
    phys_keys: List[Tuple[int, int]] = []
    eq_owner: Dict[int, Tuple[int, int]] = {}
    for eq, tag, d in direct:
        eq_owner.setdefault(eq, (tag, d))
    for tag in sorted(asm.node_coords):
        for d in range(6):
            phys_keys.append((tag, d))
    index = {k: i for i, k in enumerate(phys_keys)}
    report_mask = np.array([tag not in masters_rev for tag, _d in phys_keys])

    # characteristic length -> rotations comparable to translations
    lens = [m.length for m in model.members if m.length > 0]
    lc = float(np.median(lens)) if lens else 1.0
    rot_scale = np.array([1.0 if d < 3 else lc for _t, d in phys_keys])

    # Jacobi scaling; zero-diagonal equations are mechanisms by themselves
    s = np.where(diag > 0.0, 1.0 / np.sqrt(np.where(diag > 0, diag, 1.0)),
                 1.0)
    Ks = K * s[:, None] * s[None, :]
    w_raw = np.linalg.eigvalsh(K)
    ws, vs = np.linalg.eigh(Ks)
    lam_max = float(ws[-1]) if ws.size else 0.0
    null = [k for k in range(len(ws))
            if ws[k] <= MECHANISM_REL_TOL * max(lam_max, 1e-300)]
    result["lowest_eigenvalues"] = [float(x) for x in ws[:6]]
    rmax = float(np.max(np.abs(w_raw))) if w_raw.size else 0.0
    rmin = float(np.min(np.abs(w_raw))) if w_raw.size else 0.0
    nn = [abs(float(ws[k])) for k in range(len(ws)) if k not in set(null)]
    if null:
        result["condition_number"] = None
        result["scaled_condition_number"] = None
    else:
        result["condition_number"] = (rmax / rmin) if rmin > 0 else None
        result["scaled_condition_number"] = (lam_max / min(nn)
                                             if nn and min(nn) > 0 else None)
    if not null and result["condition_number"]:
        result["digits_lost"] = round(math.log10(result["condition_number"]),
                                      2)
    # stable part conditioning (always available)
    if nn:
        result["scaled_condition_number_nonsingular"] = lam_max / min(nn)

    # ---- mechanisms ---------------------------------------------------- #
    if null:
        V = vs[:, null] * s[:, None]            # back to K coordinates
        P = np.column_stack([
            _physical(V[:, k], asm, direct, constrained, index,
                      len(phys_keys)) * rot_scale for k in range(V.shape[1])])
        P[~report_mask, :] = 0.0
        keep = np.linalg.norm(P, axis=0) > 0
        Q = np.linalg.qr(P[:, keep])[0] if keep.any() else P[:, :0]
        part = (Q * Q).sum(axis=1)
        order = _rank(part, phys_keys)
        top = [i for i in order[:max(n_report * 3, 24)] if part[i] > 1e-6]
        pmax = part[top[0]] if top else 0.0
        result["unstable_dofs"] = [
            _location_entry(phys_keys[i][0], phys_keys[i][1], part[i], asm,
                            objs, masters_rev)
            for i in top if part[i] >= 0.05 * pmax][:max(n_report * 3, 24)]
        for k in range(min(Q.shape[1], MAX_MODES_REPORTED)):
            q = Q[:, k] ** 2
            o = _rank(q, phys_keys)
            qmax = q[o[0]] if q.size else 0.0
            dofs = [_location_entry(phys_keys[i][0], phys_keys[i][1], q[i],
                                    asm, objs, masters_rev)
                    for i in o[:n_report] if q[i] >= 0.05 * qmax]
            result["mechanisms"].append({
                "mode": k + 1, "scaled_eigenvalue": float(ws[null[k]]),
                "dofs": dofs})
            d0 = dofs[0] if dofs else None
            where = (f" at joint {_fmt(d0['point'])} DOF {d0['dof']}"
                     + (f" ({', '.join(d0['objects'][:4])})"
                        if d0["objects"] else "")) if d0 else ""
            issues.append(_issue(
                "error", "STABILITY_MECHANISM",
                f"Structure is unstable (mechanism {k + 1} of {len(null)})"
                f"{where}", d0["objects"] if d0 else [],
                d0["point"] if d0 else None))
    result["n_mechanisms"] = len(null)

    # ---- diagonal ratio K_ii / D_ii (LDL^T, Cholesky with a tiny shift) -- #
    dmax = float(diag.max()) if diag.size else 0.0
    L = None
    for shift in (1e-14, 1e-12, 1e-10):
        try:
            L = np.linalg.cholesky(K + shift * max(dmax, 1e-300)
                                   * np.eye(n))
            break
        except np.linalg.LinAlgError:
            continue
    if L is not None:
        D = np.diag(L) ** 2
        ratio = np.where(D > 0, np.abs(diag) / D, np.inf)
        ratio = np.where(diag > 0, ratio, np.inf)
        finite = np.where(np.isfinite(ratio), ratio, 1e300)
        imax = int(np.argmax(finite))
        rv = float(finite[imax])
        result["max_diagonal_ratio"] = rv if rv < 1e300 else None
        result["n_ill_conditioned_equations"] = int(
            np.sum(finite > DIAG_RATIO_WARN))
        own = eq_owner.get(imax)
        if own is not None:
            result["max_diagonal_ratio_at"] = _location_entry(
                own[0], own[1], 1.0, asm, objs, masters_rev)
            result["max_diagonal_ratio_at"].pop("participation", None)
            result["max_diagonal_ratio_at"]["equation"] = imax
        if not null and rv > DIAG_RATIO_WARN:
            at = result["max_diagonal_ratio_at"]
            where = (f" at joint {_fmt(at['point'])} DOF {at['dof']}"
                     if at else "")
            issues.append(_issue(
                "warning", "STABILITY_ILL_CONDITIONED",
                f"Stiffness matrix is ill-conditioned: maximum diagonal "
                f"ratio {rv:.3g}{where} exceeds {DIAG_RATIO_WARN:g} (about "
                f"{math.log10(rv):.0f} digits of accuracy lost)",
                at["objects"] if at else [], at["point"] if at else None))
    cond = result["condition_number"]
    if not null and cond and cond > COND_WARN:
        issues.append(_issue(
            "warning", "STABILITY_ILL_CONDITIONED",
            f"Stiffness condition number {cond:.3g} exceeds {COND_WARN:g}",
            []))
    result["stable"] = not null
    if null:
        top = result["unstable_dofs"][0] if result["unstable_dofs"] else None
        result["message"] = (
            f"Structure is UNSTABLE: {len(null)} mechanism(s)"
            + (f"; largest motion at joint {_fmt(top['point'])} DOF "
               f"{top['dof']}" if top else ""))
    else:
        result["message"] = ("Structure is stable"
                             + ("" if not any(i["code"]
                                              == "STABILITY_ILL_CONDITIONED"
                                              for i in issues)
                                else " but ill-conditioned"))


def stability_diagnostics(model: BuildingModel,
                          max_dofs: int = STABILITY_MAX_DOFS,
                          n_report: int = 8) -> dict:
    """Mechanism / ill-conditioning diagnostics of the assembled stiffness.

    See the module docstring and CONTRACT.md for the result shape.
    """
    from skyframe.engine.opensees_engine import (OpenSeesEngine,
                                                 _quiet_native_output, ops)
    t0 = time.perf_counter()
    result: dict = {"stable": None, "built": False, "skipped": False,
                    "n_equations": 0, "n_mechanisms": 0, "mechanisms": [],
                    "unstable_dofs": [], "condition_number": None,
                    "scaled_condition_number": None,
                    "scaled_condition_number_nonsingular": None,
                    "digits_lost": None,
                    "max_diagonal_ratio": None,
                    "max_diagonal_ratio_at": None,
                    "n_ill_conditioned_equations": 0,
                    "lowest_eigenvalues": [], "issues": [],
                    "thresholds": {"mechanism_rel_eigenvalue":
                                   MECHANISM_REL_TOL,
                                   "diagonal_ratio_warning": DIAG_RATIO_WARN,
                                   "condition_warning": COND_WARN,
                                   "max_dofs": int(max_dofs)},
                    "message": ""}
    issues = result["issues"]
    try:
        model.validate()
    except Exception as exc:
        issues.append(_issue("error", "STABILITY_BUILD_FAILED",
                             f"Model is invalid ({exc}); run Check Model",
                             []))
        result["message"] = issues[-1]["message"]
        return result
    eng = OpenSeesEngine(model)
    try:
        with _quiet_native_output():
            asm = eng._build()
    except Exception as exc:
        ops.wipe()
        issues.append(_issue("error", "STABILITY_BUILD_FAILED",
                             f"Could not build the analysis model: {exc}",
                             []))
        result["message"] = issues[-1]["message"]
        return result
    result["built"] = True
    # cheap equation-count estimate before forming the dense matrix
    est = 0
    for tag in asm.node_coords:
        est += 6 - sum(asm.node_restraints.get(tag, (0,) * 6))
    try:
        est -= 3 * len(ops.getConstrainedNodes() or [])
    except Exception:
        pass
    if est > max_dofs:
        ops.wipe()
        result["skipped"] = True
        result["n_equations"] = int(est)
        msg = (f"Stability eigen-analysis skipped: about {est} equations "
               f"exceed the dense-analysis cap of {max_dofs} (pass a larger "
               f"max_dofs up to {STABILITY_MAX_DOFS_LIMIT}, or check a "
               "smaller sub-model)")
        issues.append(_issue("info", "STABILITY_TOO_LARGE", msg, []))
        result["message"] = msg
        return result
    try:
        with _quiet_native_output():
            ops.wipeAnalysis()
            ops.constraints("Transformation" if asm.use_transformation
                            else "Plain")
            ops.numberer("Plain")
            ops.system("FullGeneral")
            ops.algorithm("Linear")
            ops.integrator("LoadControl", 0.0)
            ops.analysis("Static")
            ops.analyze(1)               # forms K; may "fail" when singular
            raw = np.array(ops.printA("-ret"), dtype=float)
            n = int(round(math.sqrt(raw.size)))
            K = raw.reshape(n, n)
            direct, constrained = _dof_map(asm, ops)
    except Exception as exc:
        ops.wipe()
        issues.append(_issue("error", "STABILITY_BUILD_FAILED",
                             f"Could not assemble the stiffness matrix: {exc}",
                             []))
        result["message"] = issues[-1]["message"]
        return result
    ops.wipe()
    result["n_equations"] = n
    if n == 0:
        result["stable"] = True
        result["message"] = "No free degrees of freedom"
        return result
    with _single_thread_blas():
        _analyze_stiffness(result, K, model, asm, direct, constrained,
                           n_report)
    result["elapsed_s"] = round(time.perf_counter() - t0, 4)
    return result
