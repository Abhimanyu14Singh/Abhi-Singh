"""Extended ETABS-style joint / shell / frame loads (Assign > Joint Loads,
Assign > Shell Loads, frame concentrated moments).

Self-contained helpers shared by :mod:`skyframe.core.model` (validation,
serialization, mass-source weights) and the OpenSees engine (load
application).  This module deliberately imports nothing from the model so
the model can import it at module level without a cycle; every function is
duck-typed on the model dataclasses.

Features
--------
* **Joint moments** — ``NodalLoad.mx/my/mz`` (kN*m, global axes).
* **Ground displacement** (support settlement) — :class:`GroundDisplacement`
  entries on ``LoadPattern.ground_displacements``: imposed displacements /
  rotations on the RESTRAINED dofs of a support (``ops.sp``) or at the
  GROUND end of a grounded point spring.
* **Shell load directions** — ``AreaLoad.direction`` in
  :data:`AREA_LOAD_DIRECTIONS`, ``AreaLoad.projected`` and the linear
  ``AreaLoad.joint_pattern`` (``p = a*x + b*y + c*z + d``) distributed to the
  mesh nodes by CONSISTENT (bilinear shape-function) integration.
* **Frame concentrated moments** — ``MemberLoad(kind="moment")``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]

AREA_LOAD_DIRECTIONS = ("gravity", "global_x", "global_y", "global_z",
                        "local_1", "local_2", "local_3")
# frame concentrated moment axes (about the member local / global axes);
# local_1/2/3 are accepted ETABS aliases of local_x/y/z.
MEMBER_MOMENT_DIRECTIONS = ("local_x", "local_y", "local_z",
                            "local_1", "local_2", "local_3",
                            "global_x", "global_y", "global_z")
_LOCAL_ALIAS = {"local_1": "local_x", "local_2": "local_y",
                "local_3": "local_z"}
GD_DOFS = ("ux", "uy", "uz", "rx", "ry", "rz")

# 3-point Gauss-Legendre on [-1, 1]
_G3 = ((-math.sqrt(0.6), 5.0 / 9.0), (0.0, 8.0 / 9.0),
       (math.sqrt(0.6), 5.0 / 9.0))


# --------------------------------------------------------------------------- #
# ground displacement
# --------------------------------------------------------------------------- #
@dataclass
class GroundDisplacement:
    """Imposed ground displacement at a support / spring point (m, rad,
    global axes).  Applied only on the support's RESTRAINED dofs (or the
    spring-covered dofs, at the spring's ground end); a non-zero value on a
    free dof is ignored with a warning (ETABS behavior)."""

    point: Tuple[float, float, float]
    ux: float = 0.0
    uy: float = 0.0
    uz: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    rz: float = 0.0

    def values(self) -> List[float]:
        return [self.ux, self.uy, self.uz, self.rx, self.ry, self.rz]

    def to_dict(self) -> dict:
        return {"point": [float(v) for v in self.point],
                "ux": self.ux, "uy": self.uy, "uz": self.uz,
                "rx": self.rx, "ry": self.ry, "rz": self.rz}

    @staticmethod
    def from_dict(d: dict) -> "GroundDisplacement":
        return GroundDisplacement(
            tuple(float(v) for v in d["point"]),
            **{k: float(d.get(k, 0.0)) for k in GD_DOFS})


def model_has_ground_displacements(model) -> bool:
    return any(getattr(p, "ground_displacements", None)
               for p in model.patterns.values())


def _same(a, b, tol: float = 1e-6) -> bool:
    return all(abs(float(x) - float(y)) < tol for x, y in zip(a, b))


def validate_ground_displacement(model, pat_name: str,
                                 gd: GroundDisplacement) -> None:
    """The point must be an explicit support, a point spring, or (with no
    explicit supports) lie at the automatic base level."""
    pt = getattr(gd, "point", None)
    if pt is None or len(pt) != 3:
        raise ValueError(f"Pattern {pat_name}: ground displacement needs a "
                         "3-coordinate point")
    for v in list(pt) + gd.values():
        if not (isinstance(v, (int, float)) and math.isfinite(v)):
            raise ValueError(f"Pattern {pat_name}: ground displacement "
                             f"values must be finite (got {v!r})")
    ok = any(_same(s.point, pt) for s in model.supports)
    ok = ok or any(_same(s.point, pt) for s in model.spring_supports)
    if not ok and not model.supports:
        zs = [p[2] for m in model.members for p in (m.pi, m.pj)]
        zs += [c[2] for r in model.shells for c in r.corners]
        ok = bool(zs) and abs(float(pt[2]) - min(zs)) < 1e-6
    if not ok:
        raise ValueError(
            f"Pattern {pat_name}: ground displacement at {tuple(pt)} is not "
            "at a support or spring point")


# --------------------------------------------------------------------------- #
# joint loads (moments) — serialization helper
# --------------------------------------------------------------------------- #
def nodal_load_to_dict(nl) -> dict:
    """``fx/fy/fz`` always; ``mx/my/mz`` only when non-zero (pre-feature
    files serialize byte-identically)."""
    d = {"point": nl.point, "fx": nl.fx, "fy": nl.fy, "fz": nl.fz}
    for k in ("mx", "my", "mz"):
        v = getattr(nl, k, 0.0)
        if v:
            d[k] = v
    return d


# --------------------------------------------------------------------------- #
# shell loads
# --------------------------------------------------------------------------- #
def area_load_is_default(al) -> bool:
    """True for the pre-feature load: uniform, gravity, not projected."""
    return (getattr(al, "direction", "gravity") == "gravity"
            and not getattr(al, "projected", False)
            and getattr(al, "joint_pattern", None) is None)


def area_load_to_dict(al) -> dict:
    d = {"region_uid": al.region_uid, "q": al.q}
    if getattr(al, "direction", "gravity") != "gravity":
        d["direction"] = al.direction
    if getattr(al, "projected", False):
        d["projected"] = True
    jp = getattr(al, "joint_pattern", None)
    if jp is not None:
        d["joint_pattern"] = dict(jp)
    return d


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
    if n < 1e-14:
        raise ValueError("degenerate vector")
    return (a[0] / n, a[1] / n, a[2] / n)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def shell_local_axes(region) -> Tuple[Vec3, Vec3, Vec3]:
    """(e1, e2, e3) of a shell region for directional loads.

    * ``e3`` — the corner-ordering normal ``(c1 - c0) x (c3 - c0)``
      (same as the v0.23 wind normal);
    * ``e1`` — horizontal: ``Z x e3`` normalized; for a horizontal region
      (|e3_z| ~ 1) ``e1`` = global +X;
    * ``e2 = e3 x e1`` (points "up" the slope / wall).
    """
    c = [tuple(map(float, p)) for p in region.corners]
    e3 = _unit(_cross(_sub(c[1], c[0]), _sub(c[3], c[0])))
    if abs(e3[2]) > 1.0 - 1e-9:
        e1 = (1.0, 0.0, 0.0)
    else:
        e1 = _unit(_cross((0.0, 0.0, 1.0), e3))
    e2 = _cross(e3, e1)
    return e1, e2, e3


def area_load_dir_vector(region, direction: str) -> Vec3:
    """Unit force direction of a positive load value."""
    if direction == "gravity":
        return (0.0, 0.0, -1.0)
    if direction == "global_x":
        return (1.0, 0.0, 0.0)
    if direction == "global_y":
        return (0.0, 1.0, 0.0)
    if direction == "global_z":
        return (0.0, 0.0, 1.0)
    e1, e2, e3 = shell_local_axes(region)
    return {"local_1": e1, "local_2": e2, "local_3": e3}[direction]


def joint_pattern_value(jp: Optional[dict], x: float, y: float,
                        z: float) -> float:
    """Pattern multiplier at a point (1.0 when no pattern)."""
    if jp is None:
        return 1.0
    p = (float(jp.get("a", 0.0)) * x + float(jp.get("b", 0.0)) * y
         + float(jp.get("c", 0.0)) * z + float(jp.get("d", 0.0)))
    if jp.get("zero_negative") and p < 0.0:
        return 0.0
    if jp.get("zero_positive") and p > 0.0:
        return 0.0
    return p


def validate_area_load(al, region, pat_name: str) -> None:
    direction = getattr(al, "direction", "gravity")
    if direction not in AREA_LOAD_DIRECTIONS:
        raise ValueError(f"Pattern {pat_name}: area load direction must be "
                         f"one of {AREA_LOAD_DIRECTIONS}, got {direction!r}")
    if not isinstance(getattr(al, "projected", False), bool):
        raise ValueError(f"Pattern {pat_name}: area load projected must be "
                         "a bool")
    if getattr(al, "projected", False) and direction not in (
            "gravity", "global_x", "global_y", "global_z"):
        raise ValueError(f"Pattern {pat_name}: projected area loads need a "
                         "gravity/global direction")
    jp = getattr(al, "joint_pattern", None)
    if jp is not None:
        if not isinstance(jp, dict) or jp.get("type", "linear") != "linear":
            raise ValueError(f"Pattern {pat_name}: joint_pattern must be "
                             "{type: 'linear', a, b, c, d}")
        for k in ("a", "b", "c", "d"):
            v = jp.get(k, 0.0)
            if (isinstance(v, bool) or not isinstance(v, (int, float))
                    or not math.isfinite(v)):
                raise ValueError(f"Pattern {pat_name}: joint_pattern {k} "
                                 f"must be finite (got {v!r})")
        if jp.get("zero_negative") and jp.get("zero_positive"):
            raise ValueError(f"Pattern {pat_name}: joint_pattern cannot zero "
                             "both signs")
    if region is not None and region.behavior == "membrane" and (
            direction != "gravity" or jp is not None):
        raise ValueError(
            f"Pattern {pat_name}: membrane region {region.uid!r} only takes "
            "uniform gravity area loads (direction/joint_pattern need shell "
            "behavior)")


def _quad_integrate(X: Sequence[Vec3], fn) -> List[Tuple[float, ...]]:
    """3x3 Gauss over a bilinear quad: returns per-node sums of
    ``N_i * fn(point, unit_normal) * dA`` (fn returns a 3-vector)."""
    out = [[0.0, 0.0, 0.0] for _ in range(4)]
    sgn = ((-1, -1), (1, -1), (1, 1), (-1, 1))
    for gx, wx in _G3:
        for gy, wy in _G3:
            N = [0.25 * (1 + sx * gx) * (1 + sy * gy) for sx, sy in sgn]
            dNx = [0.25 * sx * (1 + sy * gy) for sx, sy in sgn]
            dNy = [0.25 * sy * (1 + sx * gx) for sx, sy in sgn]
            P = tuple(sum(N[i] * X[i][k] for i in range(4)) for k in range(3))
            a = tuple(sum(dNx[i] * X[i][k] for i in range(4))
                      for k in range(3))
            b = tuple(sum(dNy[i] * X[i][k] for i in range(4))
                      for k in range(3))
            cr = _cross(a, b)
            J = math.sqrt(_dot(cr, cr))
            if J < 1e-16:
                continue
            nrm = (cr[0] / J, cr[1] / J, cr[2] / J)
            f = fn(P, nrm)
            w = wx * wy * J
            for i in range(4):
                for k in range(3):
                    out[i][k] += N[i] * f[k] * w
    return [tuple(o) for o in out]


def _intensity_fn(region, al, q: float):
    direction = getattr(al, "direction", "gravity")
    dvec = area_load_dir_vector(region, direction)
    proj = bool(getattr(al, "projected", False))
    jp = getattr(al, "joint_pattern", None)

    def fn(P, nrm):
        s = q * joint_pattern_value(jp, P[0], P[1], P[2])
        if proj:
            s *= abs(_dot(nrm, dvec))
        return (s * dvec[0], s * dvec[1], s * dvec[2])
    return fn


def area_load_nodal_forces(region, al, q: float,
                           quads: Iterable, points: Sequence[Vec3]
                           ) -> Dict[int, List[float]]:
    """Consistent nodal forces (global kN) of a (case-scaled) area load on a
    SHELL-behavior region: ``F_i = int N_i * q * p(x) * [|n.d|] * d dA``
    over every kept mesh quad (3x3 Gauss per element; exact for a linear
    pattern on parallelogram elements)."""
    fn = _intensity_fn(region, al, q)
    out: Dict[int, List[float]] = {}
    for quad in quads:
        if quad.region != region.uid:
            continue
        X = [points[n] for n in quad.nodes]
        for n, f in zip(quad.nodes, _quad_integrate(X, fn)):
            acc = out.setdefault(n, [0.0, 0.0, 0.0])
            acc[0] += f[0]
            acc[1] += f[1]
            acc[2] += f[2]
    return out


def area_load_weight(region, al) -> float:
    """Downward resultant (kN) of an area load WITHOUT a mesh (mass source /
    story gravity summaries).  The pre-feature load returns exactly
    ``q * net_area``; otherwise the -Z component is integrated over the
    region patch (8x8 sub-cells, 3x3 Gauss) and scaled by
    ``net_area / area`` for openings."""
    if area_load_is_default(al):
        return al.q * region.net_area
    return -area_load_resultant(region, al)[2]


def area_load_resultant(region, al) -> List[float]:
    """Global force resultant [FX, FY, FZ] (kN) of an area load WITHOUT a
    mesh: the patch integral used by :func:`area_load_weight`, all three
    components (8x8 sub-cells, 3x3 Gauss; scaled by ``net_area / area``
    for openings).  Exact for a linear joint pattern on a parallelogram
    region without openings."""
    c = [tuple(map(float, p)) for p in region.corners]
    fn = _intensity_fn(region, al, al.q)

    def bil(u, v):
        return tuple((1 - u) * (1 - v) * c[0][k] + u * (1 - v) * c[1][k]
                     + u * v * c[2][k] + (1 - u) * v * c[3][k]
                     for k in range(3))
    n = 8
    total = [0.0, 0.0, 0.0]
    for i in range(n):
        for j in range(n):
            X = [bil(i / n, j / n), bil((i + 1) / n, j / n),
                 bil((i + 1) / n, (j + 1) / n), bil(i / n, (j + 1) / n)]
            for f in _quad_integrate(X, fn):
                for k in range(3):
                    total[k] += f[k]
    area = region.area
    scale = region.net_area / area if area > 0.0 else 1.0
    return [t * scale for t in total]


def membrane_scale(region, al) -> float:
    """Multiplier on q for a (gravity-only) membrane region: |n_z| when
    projected (load per plan area), else 1."""
    if not getattr(al, "projected", False):
        return 1.0
    return abs(shell_local_axes(region)[2][2])


# --------------------------------------------------------------------------- #
# frame concentrated moments
# --------------------------------------------------------------------------- #
def validate_member_moment(ml, pat_name: str) -> None:
    if ml.direction not in MEMBER_MOMENT_DIRECTIONS:
        raise ValueError(f"Pattern {pat_name}: moment load direction must "
                         f"be one of {MEMBER_MOMENT_DIRECTIONS}, got "
                         f"{ml.direction!r}")
    if not 0.0 <= ml.a <= 1.0:
        raise ValueError(f"Pattern {pat_name}: moment load position "
                         f"a={ml.a} outside [0, 1]")


def member_moment_vectors(direction: str, M: float, xax: Vec3, yax: Vec3,
                          zax: Vec3) -> Tuple[Vec3, Vec3]:
    """(local (mx, my, mz), global (Mx, My, Mz)) of a moment of magnitude M
    about the given axis."""
    d = _LOCAL_ALIAS.get(direction, direction)
    if d.startswith("local_"):
        axis = {"local_x": xax, "local_y": yax, "local_z": zax}[d]
    else:
        axis = {"global_x": (1.0, 0.0, 0.0), "global_y": (0.0, 1.0, 0.0),
                "global_z": (0.0, 0.0, 1.0)}[d]
    loc = (M * _dot(axis, xax), M * _dot(axis, yax), M * _dot(axis, zax))
    glo = (M * axis[0], M * axis[1], M * axis[2])
    return loc, glo
