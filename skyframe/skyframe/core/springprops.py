"""Named point-spring properties and extra link hysteresis types (B9/B11).

ETABS parity:

* **Define > Spring Properties > Point Springs** -> ``model.spring_properties``
  (a ``{name: property}`` dict) referenced by ``SpringSupport.property``,
  with optional local axes in plan (``SpringSupport.angle_deg``) or in the
  property (``local_axes``).
* **Link/Support Properties** hysteresis types -> five extra
  ``LinkMember.link_type`` values (:data:`HYSTERESIS_LINK_TYPES`).

Everything here is pure Python (validation, local-axes algebra, the exact
force law of every spring kind, used for the reported spring reactions).
The OpenSees side lives in :mod:`skyframe.engine.hysteresis`.

Named point-spring property (JSON)::

    {"kind": "linear" | "multilinear" | "compression_only"
             | "tension_only" | "gap",
     "k": [k1, k2, k3, kr1, kr2, kr3],          # LOCAL axes (kN/m, kN*m/rad)
     "curves": {"U1": [[d, F], ...], ...},      # multilinear only
     "nonlinear_dof": "U3",                     # c/t-only and gap (default U3)
     "gap": 0.01,                               # gap only (m, >= 0)
     "local_axes": {"angle_deg": 30.0}          # or a 3x3 row matrix
    }

Local DOF labels are ``U1 U2 U3 R1 R2 R3`` (ETABS); with no local axes
they coincide with global X Y Z.  ``local_axes`` as a matrix gives the
local 1/2/3 axis unit vectors (rows, global components; orthonormal and
right-handed).  ``SpringSupport.angle_deg`` then rotates the property's
axes about global Z (plan angle, counter-clockwise from +X).

Spring laws (``u`` = local deformation of the real node relative to the
fixed ground node; negative U3 = settlement / compression):

* ``linear``: ``F = k*u`` on every DOF with ``k > 0``.
* ``compression_only``: on ``nonlinear_dof`` full ``k`` for ``u < 0`` and
  the residual ``AXIAL_ONLY_RATIO*k`` (1e-6) for ``u >= 0`` (the v0.12 /
  v0.22 Elastic-with-Eneg pattern; the residual keeps a fully released
  support regular).  ``tension_only`` is the mirror.  Other DOFs linear.
* ``gap``: on ``nonlinear_dof`` ``F = k*(u + gap)`` once the spring has
  CLOSED by more than ``gap`` (``u < -gap``), else 0 — OpenSees
  ``ElasticPPGap(k, -huge, -gap)`` in ``Parallel`` with the residual
  ``Elastic(AXIAL_ONLY_RATIO*k)``.  Other DOFs linear.
* ``multilinear``: per-DOF nonlinear ELASTIC curve (``ElasticMultiLinear``,
  loading and unloading on the same curve; beyond the end points the end
  segments are extrapolated — verified OpenSees behaviour).  A curve with
  only positive ``d`` is mirrored (odd-symmetric) through the origin;
  otherwise it must contain the point ``[0, 0]``.  DOFs without a curve
  use ``k`` (optional, default 0).

Analysis participation (documented, ETABS behaviour): nonlinear springs
act nonlinearly in every analysis that solves equilibrium iteratively —
static load cases (routed to Newton exactly like compression-only
line/area springs), pushover, staged, and direct-integration time
history (routed to Newton like the device links).  Eigen-based analyses
(modal, response spectrum, buckling, Ritz, FNA basis) see the INITIAL
tangent stiffness: ``k`` for linear DOFs and the compression side of
c-only, the residual for an open gap / the tension side of c-only, the
first curve segment for multilinear.

Link hysteresis types (all with the isolator layout: link axis vertical or
zero length, the hysteresis on BOTH horizontal shear directions, elastic
vertical ``kv``):

* ``multilinear_kinematic`` {points, kv}: OpenSees ``MultiLinear``
  (odd-symmetric backbone, kinematic rule: unloading at the initial slope
  over twice the first-point force, then translated backbone).  For a
  2-point (bilinear) backbone this is exactly bilinear kinematic hardening.
* ``multilinear_takeda`` {points, points_neg?, kv}: OpenSees
  ``Hysteretic`` with pinchX = pinchY = 1, no damage, beta = 0 — unloading
  parallel to the initial slope; after the force crosses zero the branch
  aims at the peak (max-deformation backbone point) of the opposite side,
  or the opposite first (yield) point if that side never yielded.  This is
  the ETABS Takeda rule; backbone 2 or 3 points per side (a ``Hysteretic``
  limit).
* ``multilinear_pivot`` {points, points_neg?, pinch_x=0.5, pinch_y=0.25,
  unload_beta=0, kv}: APPROXIMATION of the ETABS Dowell pivot model with
  the pinching ``Hysteretic`` material: unloading at ``K0*mu^-unload_beta``
  to zero force (d0), then reloading through the PINCH (pivot) point
  ``(d0 + pinch_x*(d* - d0), pinch_y*Fmax)`` with
  ``d* = dmax - (1 - pinch_y)*Fmax/K0`` (the point at force pinch_y*Fmax on
  the elastic line through the previous peak), then to the peak — verified
  empirically on openseespy 3.7.  ETABS' alpha/beta pivots are NOT
  reproduced literally; pinch_x/pinch_y are the honest parameters.
* ``friction_spring`` {mu, k_init=FP_DEFAULT_KINIT, kv}: OpenSees
  ``flatSliderBearing`` with a Coulomb friction model — rigid-plastic
  Coulomb slider (stick stiffness ``k_init``) whose slip force is
  ``mu * N`` with N the instantaneous compressive axial load from the
  analysis (a flat slider: no restoring stiffness).  This is a Coulomb
  approximation of the ETABS "Friction Spring" family; no preload/spring
  parallel branch.
* ``rubber_isolator_bouc_wen`` {k_init, qd, alpha1=0, alpha2=0, mu=2,
  eta=1, beta=0.5, gamma=0.5, kv}: OpenSees ``elastomericBearingBoucWen``
  — smooth Bouc-Wen hysteresis, characteristic strength ``qd`` (force at
  zero displacement on the fully-yielded loop), post-yield stiffness
  ``alpha1*k_init`` (+ ``alpha2`` * sign(u)*|u|^mu), large-displacement
  force -> ``qd + alpha1*k_init*u`` (verified).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

AXIAL_ONLY_RATIO = 1.0e-6          # same residual as v0.12 / v0.22
GAP_YIELD_HUGE = 1.0e12            # same never-reached yield as gap links
SPRING_KINDS = ("linear", "multilinear", "compression_only",
                "tension_only", "gap")
DOF_LABELS = ("U1", "U2", "U3", "R1", "R2", "R3")
_PROP_KEYS = {"kind", "k", "curves", "nonlinear_dof", "gap", "local_axes"}

# ---------------------------------------------------------------- links
HYSTERESIS_LINK_TYPES = ("multilinear_takeda", "multilinear_pivot",
                         "multilinear_kinematic", "friction_spring",
                         "rubber_isolator_bouc_wen")
# material-based (twoNodeLink / zeroLength shear materials)
MATERIAL_LINK_TYPES = ("multilinear_takeda", "multilinear_pivot",
                       "multilinear_kinematic")
# dedicated bearing elements
ELEMENT_LINK_TYPES = ("friction_spring", "rubber_isolator_bouc_wen")

LINK_PARAM_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "multilinear_kinematic": (("points",), ("kv",)),
    "multilinear_takeda": (("points",), ("points_neg", "kv")),
    "multilinear_pivot": (("points",), ("points_neg", "pinch_x", "pinch_y",
                                        "unload_beta", "kv")),
    "friction_spring": (("mu",), ("k_init", "kv")),
    "rubber_isolator_bouc_wen": (("k_init", "qd"),
                                 ("alpha1", "alpha2", "mu", "eta", "beta",
                                  "gamma", "kv")),
}
PIVOT_DEFAULT_PINCH_X = 0.5
PIVOT_DEFAULT_PINCH_Y = 0.25
BW_DEFAULTS = {"alpha1": 0.0, "alpha2": 0.0, "mu": 2.0, "eta": 1.0,
               "beta": 0.5, "gamma": 0.5}
LIST_PARAMS = ("points", "points_neg")


def _num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


# ============================================================ local axes
def _rz(angle_deg: float) -> List[List[float]]:
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return [[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]]


def property_axes(prop: Optional[dict]) -> List[List[float]]:
    """Rows = local 1/2/3 unit vectors (global components)."""
    la = (prop or {}).get("local_axes")
    if la is None:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    if isinstance(la, dict):
        return _rz(float(la.get("angle_deg", 0.0)))
    return [[float(v) for v in row] for row in la]


def effective_axes(prop: Optional[dict], angle_deg: float) -> List[List[float]]:
    """Property axes rotated by the support's plan angle about global Z."""
    rows = property_axes(prop)
    if not angle_deg:
        return rows
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return [[c * r[0] - s * r[1], s * r[0] + c * r[1], r[2]] for r in rows]


def _validate_axes(where: str, la) -> None:
    if isinstance(la, dict):
        if set(la) - {"angle_deg"}:
            raise ValueError(f"{where}: local_axes dict accepts only "
                             "'angle_deg'")
        if not _num(la.get("angle_deg", 0.0)):
            raise ValueError(f"{where}: local_axes.angle_deg must be a "
                             "finite number")
        return
    if (not isinstance(la, (list, tuple)) or len(la) != 3
            or not all(isinstance(r, (list, tuple)) and len(r) == 3
                       and all(_num(v) for v in r) for r in la)):
        raise ValueError(f"{where}: local_axes must be {{'angle_deg': a}} "
                         "or a 3x3 matrix of finite numbers")
    for i in range(3):
        for j in range(3):
            dot = sum(la[i][k] * la[j][k] for k in range(3))
            if abs(dot - (1.0 if i == j else 0.0)) > 1e-6:
                raise ValueError(f"{where}: local_axes matrix rows must be "
                                 "orthonormal")
    a, b, c = la
    det = (a[0] * (b[1] * c[2] - b[2] * c[1])
           - a[1] * (b[0] * c[2] - b[2] * c[0])
           + a[2] * (b[0] * c[1] - b[1] * c[0]))
    if det <= 0.0:
        raise ValueError(f"{where}: local_axes matrix must be right-handed")


# ============================================================ properties
def normalize_curve(points) -> Tuple[List[float], List[float]]:
    """Full (xs, ys) of a multilinear curve (mirrored when all d > 0)."""
    pts = [(float(p[0]), float(p[1])) for p in points]
    if all(d > 0.0 for d, _ in pts):
        neg = [(-d, -f) for d, f in reversed(pts)]
        pts = neg + [(0.0, 0.0)] + pts
    return [p[0] for p in pts], [p[1] for p in pts]


def _validate_curve(where: str, pts) -> None:
    if (not isinstance(pts, (list, tuple)) or len(pts) < 1
            or not all(isinstance(p, (list, tuple)) and len(p) == 2
                       and all(_num(v) for v in p) for p in pts)):
        raise ValueError(f"{where}: a curve must be a non-empty list of "
                         "[d, F] pairs of finite numbers")
    ds = [float(p[0]) for p in pts]
    if any(b <= a for a, b in zip(ds[:-1], ds[1:])):
        raise ValueError(f"{where}: curve displacements must be strictly "
                         "increasing")
    if all(d > 0.0 for d in ds):
        if float(pts[0][1]) <= 0.0:
            raise ValueError(f"{where}: the first curve force must be > 0 "
                             "(positive initial stiffness)")
        return
    if not any(float(p[0]) == 0.0 and float(p[1]) == 0.0 for p in pts):
        raise ValueError(f"{where}: a curve with d <= 0 points must pass "
                         "through [0, 0]")
    if len(pts) < 2:
        raise ValueError(f"{where}: a curve needs at least 2 points")
    xs, ys = normalize_curve(pts)
    if initial_slope(xs, ys) <= 0.0:
        raise ValueError(f"{where}: the curve must have a positive slope "
                         "at the origin")


def validate_property(name: str, prop) -> None:
    where = f"Spring property {name!r}"
    if not isinstance(name, str) or not name.strip():
        raise ValueError("spring property names must be non-empty strings")
    if not isinstance(prop, dict):
        raise ValueError(f"{where}: must be a dict")
    unknown = set(prop) - _PROP_KEYS
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {sorted(unknown)} "
                         f"(allowed: {sorted(_PROP_KEYS)})")
    kind = prop.get("kind")
    if kind not in SPRING_KINDS:
        raise ValueError(f"{where}: kind must be one of {SPRING_KINDS}, "
                         f"got {kind!r}")
    k = prop.get("k")
    if k is None and kind != "multilinear":
        raise ValueError(f"{where}: 'k' (6 local stiffnesses) is required")
    if k is not None:
        if (not isinstance(k, (list, tuple)) or len(k) != 6
                or not all(_num(v) and v >= 0.0 for v in k)):
            raise ValueError(f"{where}: k must be 6 finite values >= 0 "
                             "[k1, k2, k3, kr1, kr2, kr3]")
    kv = [float(v) for v in (k or [0.0] * 6)]
    if kind != "multilinear" and "curves" in prop:
        raise ValueError(f"{where}: 'curves' is only valid for multilinear")
    if kind not in ("compression_only", "tension_only", "gap"):
        for key in ("nonlinear_dof", "gap"):
            if key in prop:
                raise ValueError(f"{where}: {key!r} is not valid for kind "
                                 f"{kind!r}")
    if kind == "linear":
        if not any(v > 0.0 for v in kv):
            raise ValueError(f"{where}: at least one k entry must be > 0")
    elif kind == "multilinear":
        curves = prop.get("curves")
        if not isinstance(curves, dict) or not curves:
            raise ValueError(f"{where}: multilinear needs 'curves' "
                             "{dof: [[d, F], ...]}")
        for lab, pts in curves.items():
            if lab not in DOF_LABELS:
                raise ValueError(f"{where}: curve DOF must be one of "
                                 f"{DOF_LABELS}, got {lab!r}")
            _validate_curve(f"{where} curve {lab}", pts)
    else:
        lab = prop.get("nonlinear_dof", "U3")
        if lab not in DOF_LABELS:
            raise ValueError(f"{where}: nonlinear_dof must be one of "
                             f"{DOF_LABELS}, got {lab!r}")
        if kv[DOF_LABELS.index(lab)] <= 0.0:
            raise ValueError(f"{where}: k on the nonlinear dof {lab} must "
                             "be > 0")
        if kind == "gap":
            g = prop.get("gap")
            if not (_num(g) and g >= 0.0):
                raise ValueError(f"{where}: gap must be a finite value "
                                 ">= 0 (m)")
        elif "gap" in prop:
            raise ValueError(f"{where}: 'gap' is only valid for kind gap")
    if "local_axes" in prop:
        _validate_axes(where, prop["local_axes"])


def normalize_property(prop: dict) -> dict:
    """JSON-clean copy (floats) of a property dict, keys kept as given."""
    out: dict = {"kind": str(prop.get("kind"))}
    if prop.get("k") is not None:
        out["k"] = [float(v) for v in prop["k"]]
    if "curves" in prop and isinstance(prop["curves"], dict):
        out["curves"] = {str(lab): [[float(p[0]), float(p[1])] for p in pts]
                         for lab, pts in prop["curves"].items()}
    if "nonlinear_dof" in prop:
        out["nonlinear_dof"] = str(prop["nonlinear_dof"])
    if "gap" in prop:
        out["gap"] = float(prop["gap"])
    if "local_axes" in prop:
        la = prop["local_axes"]
        out["local_axes"] = ({"angle_deg": float(la.get("angle_deg", 0.0))}
                             if isinstance(la, dict) else
                             [[float(v) for v in r] for r in la])
    return out


def validate_support(sp, props: Dict[str, dict]) -> None:
    """Model-level checks of a SpringSupport's named property reference."""
    name = getattr(sp, "property", None)
    if name is not None and name not in (props or {}):
        raise ValueError(f"spring support at {tuple(sp.point)}: unknown "
                         f"spring property {name!r}")


def is_named(sp) -> bool:
    """True when the support takes the named / rotated path."""
    return (getattr(sp, "property", None) is not None
            or bool(getattr(sp, "angle_deg", 0.0)))


# ============================================================ force laws
def initial_slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    for i in range(len(xs) - 1):
        if xs[i] <= 0.0 < xs[i + 1] or xs[i] < 0.0 <= xs[i + 1]:
            return (ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i])
    return (ys[1] - ys[0]) / (xs[1] - xs[0]) if len(xs) > 1 else 0.0


def curve_force(xs: Sequence[float], ys: Sequence[float], u: float) -> float:
    """Piecewise-linear F(u), end segments extrapolated (ElasticMultiLinear)."""
    n = len(xs)
    if n == 1:
        return ys[0] / xs[0] * u if xs[0] else 0.0
    i = 0
    while i < n - 2 and u > xs[i + 1]:
        i += 1
    x0, x1, y0, y1 = xs[i], xs[i + 1], ys[i], ys[i + 1]
    return y0 + (y1 - y0) * (u - x0) / (x1 - x0)


@dataclass
class DofLaw:
    """One local DOF of a named spring: ``kind`` in linear | c_only |
    t_only | gap | curve."""
    kind: str
    k: float = 0.0
    gap: float = 0.0
    xs: List[float] = field(default_factory=list)
    ys: List[float] = field(default_factory=list)

    def force(self, u: float) -> float:
        r = AXIAL_ONLY_RATIO
        if self.kind == "linear":
            return self.k * u
        if self.kind == "c_only":
            return self.k * u if u < 0.0 else self.k * r * u
        if self.kind == "t_only":
            return self.k * u if u > 0.0 else self.k * r * u
        if self.kind == "gap":
            f = self.k * (u + self.gap) if u < -self.gap else 0.0
            return f + self.k * r * u
        return curve_force(self.xs, self.ys, u)

    @property
    def nonlinear(self) -> bool:
        return self.kind != "linear"


@dataclass
class SpringSpec:
    """Resolved named spring: local axes + one law per active local DOF."""
    axes: List[List[float]]
    laws: List[Optional[DofLaw]]

    @property
    def nonlinear(self) -> bool:
        return any(lw is not None and lw.nonlinear for lw in self.laws)

    @property
    def identity_axes(self) -> bool:
        return all(abs(self.axes[i][j] - (1.0 if i == j else 0.0)) < 1e-15
                   for i in range(3) for j in range(3))

    def global_dofs(self) -> List[int]:
        """0-based GLOBAL dofs the spring couples to."""
        out = set()
        for i, lw in enumerate(self.laws):
            if lw is None:
                continue
            base = 0 if i < 3 else 3
            row = self.axes[i % 3]
            out |= {base + j for j in range(3) if abs(row[j]) > 1e-12}
        return sorted(out)

    def local_disp(self, u6: Sequence[float]) -> List[float]:
        R = self.axes
        t = [sum(R[i][j] * u6[j] for j in range(3)) for i in range(3)]
        r = [sum(R[i][j] * u6[3 + j] for j in range(3)) for i in range(3)]
        return t + r

    def reaction(self, u6: Sequence[float]) -> List[float]:
        """Global reaction (force ON the structure) = -R^T F_local(u)."""
        ul = self.local_disp(u6)
        fl = [lw.force(ul[i]) if lw is not None else 0.0
              for i, lw in enumerate(self.laws)]
        R = self.axes
        out = [0.0] * 6
        for j in range(3):
            out[j] = -sum(R[i][j] * fl[i] for i in range(3))
            out[3 + j] = -sum(R[i][j] * fl[3 + i] for i in range(3))
        return out


def resolve(sp, props: Dict[str, dict]) -> SpringSpec:
    """SpringSpec of a support on the named / rotated path."""
    name = getattr(sp, "property", None)
    angle = float(getattr(sp, "angle_deg", 0.0) or 0.0)
    if name is None:                      # inline stiffness, rotated axes
        prop = {"kind": "linear", "k": list(sp.stiffness)}
    else:
        prop = props[name]
    axes = effective_axes(prop, angle)
    kind = prop["kind"]
    kv = [float(v) for v in (prop.get("k") or [0.0] * 6)]
    laws: List[Optional[DofLaw]] = [
        DofLaw("linear", k=kv[d]) if kv[d] > 0.0 else None for d in range(6)]
    if kind == "multilinear":
        for lab, pts in prop["curves"].items():
            xs, ys = normalize_curve(pts)
            laws[DOF_LABELS.index(lab)] = DofLaw("curve", xs=xs, ys=ys)
    elif kind in ("compression_only", "tension_only", "gap"):
        d = DOF_LABELS.index(prop.get("nonlinear_dof", "U3"))
        law = {"compression_only": "c_only", "tension_only": "t_only",
               "gap": "gap"}[kind]
        laws[d] = DofLaw(law, k=kv[d], gap=float(prop.get("gap", 0.0)))
    return SpringSpec(axes, laws)


def any_nonlinear(model) -> bool:
    props = getattr(model, "spring_properties", None) or {}
    for sp in getattr(model, "spring_supports", []) or []:
        name = getattr(sp, "property", None)
        if name is not None and name in props \
                and props[name].get("kind") != "linear":
            return True
    return False


# ============================================================ link params
def _validate_backbone(where: str, pts, sign: float, max_pts: Optional[int],
                       key: str) -> None:
    if (not isinstance(pts, (list, tuple)) or len(pts) < 1
            or not all(isinstance(p, (list, tuple)) and len(p) == 2
                       and all(_num(v) for v in p) for p in pts)):
        raise ValueError(f"{where}: {key} must be a list of [d, F] pairs of "
                         "finite numbers")
    if max_pts is not None and not 2 <= len(pts) <= max_pts:
        raise ValueError(f"{where}: {key} needs 2..{max_pts} points "
                         "(Hysteretic backbone limit)")
    prev = 0.0
    for d, f in pts:
        if sign * d <= sign * prev:
            raise ValueError(f"{where}: {key} displacements must move "
                             f"strictly away from 0 ({'+' if sign > 0 else '-'}"
                             " side)")
        if sign * f <= 0.0:
            raise ValueError(f"{where}: {key} forces must be "
                             f"{'> 0' if sign > 0 else '< 0'}")
        prev = d


def validate_link_params(uid: str, ltype: str, prm: dict) -> None:
    """Value checks of a hysteresis link type (keys already checked)."""
    where = f"Link {uid} ({ltype})"
    if ltype in MATERIAL_LINK_TYPES:
        mx = None if ltype == "multilinear_kinematic" else 3
        _validate_backbone(where, prm["points"], 1.0, mx, "points")
        if "points_neg" in prm:
            _validate_backbone(where, prm["points_neg"], -1.0, mx,
                               "points_neg")
            if len(prm["points_neg"]) != len(prm["points"]):
                raise ValueError(f"{where}: points_neg must have as many "
                                 "points as points")
        if ltype == "multilinear_pivot":
            for key, dflt in (("pinch_x", PIVOT_DEFAULT_PINCH_X),
                              ("pinch_y", PIVOT_DEFAULT_PINCH_Y)):
                v = prm.get(key, dflt)
                if not 0.0 < v <= 1.0:
                    raise ValueError(f"{where}: {key} must be in (0, 1]")
            if prm.get("unload_beta", 0.0) < 0.0:
                raise ValueError(f"{where}: unload_beta must be >= 0")
    elif ltype == "friction_spring":
        if prm["mu"] <= 0.0:
            raise ValueError(f"{where}: mu must be > 0")
        if prm.get("k_init", 1.0) <= 0.0:
            raise ValueError(f"{where}: k_init must be > 0")
    elif ltype == "rubber_isolator_bouc_wen":
        if prm["k_init"] <= 0.0:
            raise ValueError(f"{where}: k_init must be > 0")
        if prm["qd"] <= 0.0:
            raise ValueError(f"{where}: qd must be > 0")
        if not 0.0 <= prm.get("alpha1", 0.0) < 1.0:
            raise ValueError(f"{where}: alpha1 must be in [0, 1)")
        if prm.get("alpha2", 0.0) < 0.0:
            raise ValueError(f"{where}: alpha2 must be >= 0")
        for key in ("mu", "eta"):
            if prm.get(key, BW_DEFAULTS[key]) <= 0.0:
                raise ValueError(f"{where}: {key} must be > 0")
        b = prm.get("beta", BW_DEFAULTS["beta"])
        g = prm.get("gamma", BW_DEFAULTS["gamma"])
        if b + g <= 0.0:
            raise ValueError(f"{where}: beta + gamma must be > 0 (Bouc-Wen "
                             "loop shape)")
    if prm.get("kv", 1.0) <= 0.0:
        raise ValueError(f"{where}: kv must be > 0")
