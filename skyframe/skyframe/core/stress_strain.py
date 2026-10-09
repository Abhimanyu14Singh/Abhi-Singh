"""Material stress-strain laws for analysis nonlinearity (v1.13).

``Material.stress_strain`` (optional dict, ``None`` = the engine's legacy
hard-coded laws, bit-identical) selects a uniaxial backbone + hysteresis
rule that the OpenSees engine uses wherever it builds a NONLINEAR material
law from a :class:`~skyframe.core.model.Material` (fiber PMM hinge sections
and layered-shell layers — see CONTRACT.md "v1.13 additions").

Units: strain (-), stress kPa.  Sign convention: compression NEGATIVE.

This module is pure Python (no OpenSees import at module level): every law
exposes

* :meth:`UniaxialLaw.exact` — the closed-form theoretical backbone;
* :meth:`UniaxialLaw.stress` — the ENGINE-EFFECTIVE monotonic backbone,
  i.e. exactly what the OpenSees material built by
  :meth:`UniaxialLaw.ops_material` follows under monotonic loading (the
  exact formula for native laws — Steel01 / Concrete01 / Concrete02 /
  Concrete04 — or the piecewise-linear interpolation of the very points
  handed to MultiLinear / HystereticSM / ElasticMultiLinear);
* :meth:`UniaxialLaw.sample` — a sampled monotonic curve (compression and
  tension) for ``POST /api/materials/curve``.

Model -> OpenSees mapping (``hysteresis`` x ``model``):

=========  ===========================  ==================================
model      kinematic                    takeda / pivot / elastic
=========  ===========================  ==================================
concrete
 simple    Concrete01 (Concrete02 if    HystereticSM (6-pt envelope) /
           ft > 0)                      ElasticMultiLinear (dense)
 park      Concrete01 / Concrete02      same
 mander    Concrete04                   same
 user      HystereticSM (pinch 1, no    same
           degradation)
steel
 simple    Steel01                      same
 park      MultiLinear (dense,          same
           symmetric)
 user      HystereticSM                 same
=========  ===========================  ==================================
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

STRESS_STRAIN_MODELS = ("default", "simple", "mander", "park", "user")
HYSTERESIS_RULES = ("kinematic", "takeda", "pivot", "elastic")

CONCRETE_TYPES = ("concrete", "masonry")
STEEL_TYPES = ("steel", "rebar", "tendon", "coldformed", "aluminum")

# allowed models per material family ("other" accepts default + user)
MODELS_FOR_KIND = {
    "concrete": ("default", "simple", "mander", "park", "user"),
    "steel": ("default", "simple", "park", "user"),
    "other": ("default", "user"),
}

CONCRETE_PARAMS = ("eps_c0", "eps_cu", "ft", "eps_tu")
STEEL_PARAMS = ("eps_sh", "eps_su", "b")

# defaults (contract v1.13)
DEFAULT_EPS_C0 = 0.002
DEFAULT_EPS_CU = 0.0035
DEFAULT_FT = 0.0
DEFAULT_EPS_TU_FACTOR = 10.0     # eps_tu = 10 * ft / E0 when ft > 0
DEFAULT_EPS_SH = 0.01
DEFAULT_EPS_SU = 0.09
DEFAULT_B = 0.01                 # == engine FIBER_STEEL_HARDENING
FU_FALLBACK_RATIO = 1.25         # park steel fu when Material.fu is None
SIMPLE_RESIDUAL = 0.85           # Hognestad: 0.85 f'c at eps_cu
PARK_RESIDUAL = 0.2              # Kent-Park residual 0.2 f'c
CONCRETE02_LAMBDA = 0.1          # Concrete02 unloading-slope ratio
CONCRETE04_BETA = 0.1            # Concrete04 tension decay factor

# legacy (stress_strain = null) fiber laws — mirrors the engine constants
LEGACY_FY_STEEL = 345_000.0      # kPa (structural steel fallback)
LEGACY_FY_REBAR = 420_000.0      # kPa (rebar / tendon fallback)
LEGACY_RESIDUAL = 0.2
LEGACY_EPS_U = 0.006

# hysteresis rule constants (HystereticSM): (pinchX, pinchY, d1, d2, beta)
HYSTERESIS_PARAMS = {
    "kinematic": (1.0, 1.0, 0.0, 0.0, 0.0),
    "takeda": (1.0, 1.0, 0.0, 0.0, 0.4),
    "pivot": (0.5, 0.3, 0.0, 0.0, 0.0),
}
HSM_MAX_POINTS = 7               # HystereticSM accepts <= 7 points per side
CAP_FACTOR = 10.0                # flat cap point placed at 10x the last strain
EML_POINTS_PER_SIDE = 24         # ElasticMultiLinear dense sampling
PARK_ML_POINTS = 16              # MultiLinear hardening stations (park steel)
TINY = 1e-6                      # zero-stress floor ratio for Hysteretic
USER_MAX_POINTS = 200


def kind_of(material_type: str) -> str:
    """Material family: "concrete" | "steel" | "other"."""
    if material_type in CONCRETE_TYPES:
        return "concrete"
    if material_type in STEEL_TYPES:
        return "steel"
    return "other"


def _fc_from_E(E: float) -> float:
    from skyframe.design.wall import fc_from_E
    return fc_from_E(E)


def default_strength(mat) -> float:
    """f'c (concrete) or fy (steel) the laws use when no override is given:
    the material value, else the engine's legacy fallback."""
    k = kind_of(getattr(mat, "material_type", "concrete"))
    if k == "steel":
        fy = getattr(mat, "fy", None)
        if fy:
            return float(fy)
        return (LEGACY_FY_STEEL
                if mat.material_type in ("steel", "coldformed", "aluminum")
                else LEGACY_FY_REBAR)
    fc = getattr(mat, "fc", None)
    return float(fc) if fc else _fc_from_E(mat.E)


# --------------------------------------------------------------------------- #
# validation / normalisation
# --------------------------------------------------------------------------- #
def normalize_stress_strain(d) -> Optional[dict]:
    """Canonical JSON form of a stress_strain dict (None stays None).

    Raises ValueError/TypeError on a malformed shape (value checks that need
    the material live in :func:`validate_stress_strain`)."""
    if d is None:
        return None
    if not isinstance(d, dict):
        raise ValueError("stress_strain must be an object or null")
    unknown = set(d) - {"model", "hysteresis", "params", "points"}
    if unknown:
        raise ValueError(f"stress_strain: unknown keys {sorted(unknown)}")
    out = {"model": str(d.get("model", "default")),
           "hysteresis": str(d.get("hysteresis", "kinematic")),
           "params": {}}
    params = d.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("stress_strain.params must be an object")
    for k, v in params.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"stress_strain.params[{k!r}] must be a number")
        out["params"][str(k)] = float(v)
    pts = d.get("points")
    if pts is not None:
        if not isinstance(pts, (list, tuple)):
            raise ValueError("stress_strain.points must be a list")
        norm = []
        for p in pts:
            if (not isinstance(p, (list, tuple)) or len(p) != 2
                    or any(isinstance(v, bool)
                           or not isinstance(v, (int, float)) for v in p)):
                raise ValueError("stress_strain.points entries must be "
                                 "[strain, stress] number pairs")
            norm.append([float(p[0]), float(p[1])])
        out["points"] = norm
    return out


def validate_stress_strain(mat) -> None:
    """Raise ValueError when ``mat.stress_strain`` is inconsistent."""
    ss = getattr(mat, "stress_strain", None)
    if ss is None:
        return
    name = mat.name
    ss = normalize_stress_strain(ss)
    model, hyst = ss["model"], ss["hysteresis"]
    if model not in STRESS_STRAIN_MODELS:
        raise ValueError(f"Material {name!r}: stress_strain.model must be one "
                         f"of {STRESS_STRAIN_MODELS}, got {model!r}")
    if hyst not in HYSTERESIS_RULES:
        raise ValueError(f"Material {name!r}: stress_strain.hysteresis must "
                         f"be one of {HYSTERESIS_RULES}, got {hyst!r}")
    kind = kind_of(mat.material_type)
    if model not in MODELS_FOR_KIND[kind]:
        raise ValueError(f"Material {name!r}: stress_strain.model {model!r} "
                         f"is not applicable to material_type "
                         f"{mat.material_type!r} (allowed: "
                         f"{MODELS_FOR_KIND[kind]})")
    if model == "user":
        if ss["params"]:
            raise ValueError(f"Material {name!r}: a 'user' stress_strain "
                             "takes no params (the points define the law)")
        _validate_points(name, ss.get("points"))
    else:
        if ss.get("points") is not None:
            raise ValueError(f"Material {name!r}: stress_strain.points is "
                             "only valid for model 'user'")
        allowed = (() if model == "default" else
                   CONCRETE_PARAMS if kind == "concrete" else STEEL_PARAMS)
        bad = set(ss["params"]) - set(allowed)
        if bad:
            raise ValueError(f"Material {name!r}: stress_strain.params "
                             f"{sorted(bad)} not valid for model {model!r} "
                             f"(allowed: {list(allowed)})")
        for k, v in ss["params"].items():
            if not math.isfinite(v):
                raise ValueError(f"Material {name!r}: stress_strain.params"
                                 f"[{k!r}] must be finite")
        if model != "default":
            # building the law runs every value check
            material_law(mat, _validated=True)


def _validate_points(name: str, pts) -> None:
    if not pts or len(pts) < 2:
        raise ValueError(f"Material {name!r}: a 'user' stress_strain needs "
                         "points [[strain, stress], ...] including [0, 0]")
    if len(pts) > USER_MAX_POINTS:
        raise ValueError(f"Material {name!r}: at most {USER_MAX_POINTS} "
                         "user points")
    for e, s in pts:
        if not (math.isfinite(e) and math.isfinite(s)):
            raise ValueError(f"Material {name!r}: user points must be finite")
        if (e < 0.0 and s > 0.0) or (e > 0.0 and s < 0.0):
            raise ValueError(f"Material {name!r}: user point ({e}, {s}) has "
                             "a stress of the wrong sign (compression "
                             "negative)")
    es = [p[0] for p in pts]
    if any(b <= a for a, b in zip(es, es[1:])):
        raise ValueError(f"Material {name!r}: user points must have strictly "
                         "increasing strain")
    if not any(e == 0.0 and s == 0.0 for e, s in pts):
        raise ValueError(f"Material {name!r}: user points must include the "
                         "origin [0, 0]")
    if len(pts) < 3 and all(s == 0.0 for _e, s in pts):
        raise ValueError(f"Material {name!r}: user points carry no stress")
    nz = [abs(s) for _e, s in pts if s != 0.0]
    if not nz:
        raise ValueError(f"Material {name!r}: user points carry no stress")


# --------------------------------------------------------------------------- #
# piecewise helpers
# --------------------------------------------------------------------------- #
def _interp(pts: Sequence[Tuple[float, float]], e: float) -> float:
    """Linear interpolation through sorted (strain, stress) points; FLAT
    (last stress held) outside the point range."""
    if e <= pts[0][0]:
        return pts[0][1]
    if e >= pts[-1][0]:
        return pts[-1][1]
    lo, hi = 0, len(pts) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if pts[mid][0] <= e:
            lo = mid
        else:
            hi = mid
    (e0, s0), (e1, s1) = pts[lo], pts[hi]
    return s0 + (s1 - s0) * (e - e0) / (e1 - e0)


def _simplify(side: List[Tuple[float, float]], n: int
              ) -> List[Tuple[float, float]]:
    """Visvalingam-Whyatt reduction of one envelope side (origin excluded,
    sorted by |strain|) to at most ``n`` points; the last point is kept."""
    pts = [(0.0, 0.0)] + list(side)
    while len(pts) - 1 > n:
        best, bi = None, None
        for i in range(1, len(pts) - 1):
            (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
            area = abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0))
            if best is None or area < best:
                best, bi = area, i
        pts.pop(bi)
    return pts[1:]


# --------------------------------------------------------------------------- #
# the law
# --------------------------------------------------------------------------- #
class UniaxialLaw:
    """One resolved uniaxial material law (see module docstring)."""

    def __init__(self, kind: str, model: str, hysteresis: str,
                 params: Dict[str, float], notes: List[str]):
        self.kind = kind
        self.model = model
        self.hysteresis = hysteresis
        self.params = params
        self.notes = notes
        self.native = False          # True -> stress() == exact()
        self.ops_type = ""
        self.ops_args: list = []
        self.points: Optional[List[Tuple[float, float]]] = None
        self.user_points: Optional[List[Tuple[float, float]]] = None
        self.range = (-0.01, 0.01)   # sampling strain range
        self.key_strains: List[float] = []

    # ---------------------------------------------------------- backbones
    def exact(self, e: float) -> float:
        """Closed-form theoretical monotonic backbone (kPa)."""
        p = self.params
        if self.model == "user":
            return _interp(self.user_points, e)
        if self.kind == "steel":
            if self.model in ("simple", "default"):
                E, fy, b = p["E"], p["fy"], p["b"]
                ey = fy / E
                a = abs(e)
                s = E * a if a <= ey else fy + b * E * (a - ey)
                return math.copysign(s, e) if e != 0.0 else 0.0
            # park
            return math.copysign(park_steel_stress(abs(e), p), e) \
                if e != 0.0 else 0.0
        # concrete
        if e >= 0.0:
            return self._concrete_tension(e)
        x = -e
        fc, e0 = p["fc"], p["eps_c0"]
        if self.model == "mander":
            if x > p["eps_cu"]:
                return 0.0
            r = p["r"]
            xr = x / e0
            return -fc * xr * r / (r - 1.0 + xr ** r)
        # simple / park / default: Hognestad / Kent-Park parabola, then the
        # linear branch to (eps_u, residual), then the residual held
        if x <= e0:
            xr = x / e0
            return -fc * (2.0 * xr - xr * xr)
        eu, fr = p["eps_u"], p["f_res"]
        if x >= eu:
            return -fr
        return -(fc + (fr - fc) * (x - e0) / (eu - e0))

    def _concrete_tension(self, e: float) -> float:
        p = self.params
        ft = p.get("ft", 0.0)
        if ft <= 0.0 or e <= 0.0:
            return 0.0
        E0, etu = p["E0"], p["eps_tu"]
        et = ft / E0
        if e <= et:
            return E0 * e
        if e > etu:
            return 0.0
        if self.model == "mander":                  # Concrete04 decay
            return ft * CONCRETE04_BETA ** ((e - et) / (etu - et))
        return ft * (etu - e) / (etu - et)          # Concrete02 linear

    def stress(self, e: float) -> float:
        """Engine-effective monotonic backbone (what OpenSees follows)."""
        if self.native:
            return self.exact(e)
        return _interp(self.points, e)

    # ------------------------------------------------------------- sample
    def sample(self, n: int = 80) -> Tuple[List[float], List[float]]:
        """~n strains over the compression + tension range (sorted), with
        the law's key strains (peaks, corners) included exactly."""
        lo, hi = self.range
        half = max(n // 2, 2)
        es = set([0.0])
        for i in range(1, half + 1):
            es.add(lo * i / half)
            es.add(hi * i / half)
        for k in self.key_strains:
            if lo <= k <= hi:
                es.add(k)
        strains = sorted(es)
        return strains, [self.stress(e) for e in strains]

    # ------------------------------------------------------------ OpenSees
    def ops_material(self, ops, tag: int) -> None:
        """Create the OpenSees uniaxial material for this law."""
        ops.uniaxialMaterial(self.ops_type, tag, *self.ops_args)

    def side_points(self, sign: int) -> List[Tuple[float, float]]:
        """(|strain|, |stress|) envelope stations of one side (sign = -1
        compression, +1 tension) for point-based consumers (the layered
        shell ASDConcrete3D laws), from the engine-effective backbone."""
        out = []
        stations = sorted({abs(k) for k in self.key_strains
                           if k * sign > 0.0})
        lo, hi = self.range
        end = abs(lo if sign < 0 else hi)
        for i in range(1, 9):
            stations.append(end * i / 8.0)
        for a in sorted(set(stations)):
            out.append((a, abs(self.stress(sign * a))))
        return out


def park_steel_stress(x: float, p: Dict[str, float]) -> float:
    """Park (1975) strain-hardening steel, |strain| x -> |stress| (kPa):
    elastic to eps_y, plateau fy to eps_sh, then

        fs = fy [ (m u + 2)/(60 u + 2) + u (60 - m)/(2 (30 r + 1)^2) ]

    u = x - eps_sh, r = eps_su - eps_sh,
    m = ((fu/fy)(30 r + 1)^2 - 60 r - 1)/(15 r^2); fu held beyond eps_su.
    """
    E, fy, fu = p["E"], p["fy"], p["fu"]
    esh, esu = p["eps_sh"], p["eps_su"]
    ey = fy / E
    if x <= ey:
        return E * x
    if x <= esh:
        return fy
    if x >= esu:
        return fu
    r = esu - esh
    m = ((fu / fy) * (30.0 * r + 1.0) ** 2 - 60.0 * r - 1.0) / (15.0 * r * r)
    u = x - esh
    return fy * ((m * u + 2.0) / (60.0 * u + 2.0)
                 + u * (60.0 - m) / (2.0 * (30.0 * r + 1.0) ** 2))


# --------------------------------------------------------------------------- #
# law factory
# --------------------------------------------------------------------------- #
def legacy_law(mat, strength: Optional[float] = None,
               E: Optional[float] = None) -> UniaxialLaw:
    """The engine's legacy (stress_strain = null) FIBER law of ``mat`` as a
    :class:`UniaxialLaw`: Concrete01(f'c, 2f'c/E, 0.2f'c, 0.006) for the
    concrete family (and "other"), Steel01(fy, E, 0.01) for steels."""
    kind = kind_of(mat.material_type)
    Em = float(E if E is not None else mat.E)
    st = float(strength if strength is not None else default_strength(mat))
    notes = ["legacy law (stress_strain null / model 'default'): the engine "
             "keeps its pre-v1.13 hard-coded material"]
    if kind == "steel":
        law = UniaxialLaw("steel", "default", "kinematic",
                          {"E": Em, "fy": st, "b": DEFAULT_B}, notes)
        law.native = True
        law.ops_type, law.ops_args = "Steel01", [st, Em, DEFAULT_B]
        ey = st / Em
        law.range = (-max(DEFAULT_EPS_SU, 10 * ey), max(DEFAULT_EPS_SU,
                                                        10 * ey))
        law.key_strains = [-ey, ey]
        return law
    e0 = 2.0 * st / Em
    law = UniaxialLaw("concrete", "default", "kinematic",
                      {"E": Em, "E0": Em, "fc": st, "eps_c0": e0,
                       "eps_u": LEGACY_EPS_U, "f_res": LEGACY_RESIDUAL * st,
                       "ft": 0.0}, notes)
    law.native = True
    law.ops_type = "Concrete01"
    law.ops_args = [-st, -e0, -LEGACY_RESIDUAL * st, -LEGACY_EPS_U]
    law.range = (-1.25 * max(LEGACY_EPS_U, e0), 0.25 * max(LEGACY_EPS_U, e0))
    law.key_strains = [-e0, -LEGACY_EPS_U]
    if kind == "other":
        law.notes.append("material_type 'other' samples the concrete-family "
                         "legacy law")
    return law


def material_law(mat, strength: Optional[float] = None,
                 E: Optional[float] = None,
                 _validated: bool = False) -> Optional[UniaxialLaw]:
    """Resolve ``mat.stress_strain`` into a :class:`UniaxialLaw`.

    Returns ``None`` when ``stress_strain`` is null or its model is
    ``"default"`` (the consumer keeps its legacy law — bit-identical).
    ``strength`` overrides f'c / fy (e.g. the expected Fye of a W-shape
    fiber hinge), ``E`` the modulus (e.g. a layered shell's E*mod).
    Raises ValueError on inconsistent parameters.
    """
    ss = getattr(mat, "stress_strain", None)
    if ss is None:
        return None
    ss = normalize_stress_strain(ss)
    model, hyst = ss["model"], ss["hysteresis"]
    if model == "default":
        return None
    name = mat.name
    kind = kind_of(mat.material_type)
    Em = float(E if E is not None else mat.E)
    pr = ss["params"]
    notes: List[str] = []

    def bad(msg: str):
        raise ValueError(f"Material {name!r}: stress_strain {msg}")

    if model == "user":
        pts = [(float(a), float(b)) for a, b in ss["points"]]
        if not _validated:
            _validate_points(name, [list(p) for p in pts])
        law = UniaxialLaw(kind if kind != "other" else "steel", "user", hyst,
                          {"E": Em}, notes)
        law.user_points = pts
        lo, hi = pts[0][0], pts[-1][0]
        span = max(abs(lo), abs(hi))
        law.range = (lo * 1.1 if lo < 0 else -0.05 * span,
                     hi * 1.1 if hi > 0 else 0.05 * span)
        law.key_strains = [p[0] for p in pts]
        notes.append("user backbone: linear between points, last stress held "
                     "beyond the end points")
        _finish_point_law(law, hyst)
        return law

    st = float(strength if strength is not None else default_strength(mat))
    if kind == "steel":
        b = pr.get("b", DEFAULT_B)
        esh = pr.get("eps_sh", DEFAULT_EPS_SH)
        esu = pr.get("eps_su", DEFAULT_EPS_SU)
        ey = st / Em
        if not 0.0 <= b < 1.0:
            bad(f"b must be in [0, 1) (got {b})")
        p = {"E": Em, "fy": st, "b": b, "eps_sh": esh, "eps_su": esu}
        if model == "simple":
            law = UniaxialLaw("steel", "simple", hyst, p, notes)
            law.range = (-1.1 * max(esu, 10 * ey), 1.1 * max(esu, 10 * ey))
            law.key_strains = [-ey, ey]
            if hyst == "kinematic":
                law.native = True
                law.ops_type, law.ops_args = "Steel01", [st, Em, b]
                return law
            _finish_point_law(law, hyst)
            return law
        # park
        if not esh > ey:
            bad(f"eps_sh ({esh}) must exceed the yield strain fy/E ({ey:.6g})")
        if not esu > esh:
            bad(f"eps_su ({esu}) must exceed eps_sh ({esh})")
        fu_m = getattr(mat, "fu", None)
        if strength is not None and fu_m:
            fu = float(fu_m) * st / default_strength(mat)
        else:
            fu = float(fu_m) if fu_m else FU_FALLBACK_RATIO * st
        if fu_m is None:
            notes.append(f"park: Material.fu unset -> fu = "
                         f"{FU_FALLBACK_RATIO} fy")
        if not fu > st:
            bad(f"park needs fu > fy (fu={fu}, fy={st})")
        p["fu"] = fu
        law = UniaxialLaw("steel", "park", hyst, p, notes)
        law.range = (-1.1 * esu, 1.1 * esu)
        law.key_strains = [-esu, -esh, -ey, ey, esh, esu]
        if hyst == "kinematic":
            # symmetric MultiLinear through dense stations of the exact
            # Park curve + a flat cap (MultiLinear extrapolates its last
            # slope otherwise)
            stations = [ey, esh] + [esh + (esu - esh) * (i / PARK_ML_POINTS)
                                    ** 1.5 for i in range(1, PARK_ML_POINTS
                                                          + 1)]
            pos = [(x, park_steel_stress(x, p)) for x in stations]
            pos.append((CAP_FACTOR * esu, fu))
            law.points = ([(-x, -s) for x, s in reversed(pos)] + [(0.0, 0.0)]
                          + pos)
            law.ops_type = "MultiLinear"
            law.ops_args = [v for x, s in pos for v in (x, s)]
            notes.append(f"park kinematic: MultiLinear through "
                         f"{len(pos) - 1} stations of the Park curve "
                         "(exact at the stations, linear between)")
            return law
        _finish_point_law(law, hyst)
        return law

    # ---------------- concrete family ----------------
    fc = st
    e0 = pr.get("eps_c0", DEFAULT_EPS_C0)
    ecu = pr.get("eps_cu", DEFAULT_EPS_CU)
    ft = pr.get("ft", DEFAULT_FT)
    if not e0 > 0.0:
        bad(f"eps_c0 must be > 0 (got {e0})")
    if not ecu > e0:
        bad(f"eps_cu ({ecu}) must exceed eps_c0 ({e0})")
    if not ft >= 0.0:
        bad(f"ft must be >= 0 (got {ft})")
    if ft >= fc:
        bad(f"ft ({ft}) must be below f'c ({fc})")
    p = {"E": Em, "fc": fc, "eps_c0": e0, "eps_cu": ecu, "ft": ft}
    if model == "mander":
        Esec = fc / e0
        if not Em > Esec:
            bad(f"mander needs E ({Em}) > f'c/eps_c0 ({Esec:.6g})")
        p["r"] = Em / (Em - Esec)
        p["E0"] = Em
        tail = ecu
    elif model == "simple":
        p["E0"] = 2.0 * fc / e0
        p["eps_u"], p["f_res"] = ecu, SIMPLE_RESIDUAL * fc
        tail = ecu
    else:  # park (Kent-Park, unconfined)
        fc_mpa = fc / 1000.0
        e50u = (3.0 + 0.29 * fc_mpa) / (145.0 * fc_mpa - 1000.0)
        if not (145.0 * fc_mpa > 1000.0 and e50u > e0):
            bad(f"Kent-Park eps_50u ({e50u:.6g}) must exceed eps_c0 ({e0}); "
                "f'c too low/high for the unconfined formula")
        Z = 0.5 / (e50u - e0)
        p["E0"] = 2.0 * fc / e0
        p["eps_50u"], p["Z"] = e50u, Z
        p["eps_u"] = e0 + (1.0 - PARK_RESIDUAL) / Z
        p["f_res"] = PARK_RESIDUAL * fc
        tail = p["eps_u"]
        if "eps_cu" in pr:
            notes.append("park: eps_cu is not used (Kent-Park descends to "
                         "0.2 f'c at eps_20 = eps_c0 + 0.8/Z)")
    if ft > 0.0:
        etu = pr.get("eps_tu", DEFAULT_EPS_TU_FACTOR * ft / p["E0"])
        if not etu > ft / p["E0"]:
            bad(f"eps_tu ({etu}) must exceed ft/E0 ({ft / p['E0']:.6g})")
        p["eps_tu"] = etu
    elif "eps_tu" in pr:
        bad("eps_tu requires ft > 0")
    law = UniaxialLaw("concrete", model, hyst, p, notes)
    t_end = p.get("eps_tu", 0.0)
    law.range = (-1.3 * tail, 1.2 * t_end if t_end > 0 else 0.1 * e0)
    law.key_strains = [-e0, -tail] + ([ft / p["E0"], t_end] if ft > 0
                                      else [])
    if hyst == "kinematic":
        law.native = True
        if model == "mander":
            law.ops_type = "Concrete04"
            law.ops_args = [-fc, -e0, -ecu, Em]
            if ft > 0.0:
                law.ops_args += [ft, p["eps_tu"], CONCRETE04_BETA]
        elif ft > 0.0:
            law.ops_type = "Concrete02"
            law.ops_args = [-fc, -e0, -p["f_res"], -p["eps_u"],
                            CONCRETE02_LAMBDA, ft,
                            ft / (p["eps_tu"] - ft / p["E0"])]
        else:
            law.ops_type = "Concrete01"
            law.ops_args = [-fc, -e0, -p["f_res"], -p["eps_u"]]
        return law
    _finish_point_law(law, hyst)
    return law


def _exact_side(law: UniaxialLaw, sign: int, n: int
                ) -> List[Tuple[float, float]]:
    """Dense |strain| stations of one side of the exact backbone."""
    lo, hi = law.range
    end = abs(lo if sign < 0 else hi)
    st = {abs(k) for k in law.key_strains if k * sign > 0.0}
    for i in range(1, n + 1):
        st.add(end * i / n)
    return [(a, abs(law.exact(sign * a))) for a in sorted(st)]


def _envelope_stations(law: UniaxialLaw, sign: int
                       ) -> List[Tuple[float, float]]:
    """<= HSM_MAX_POINTS - 1 (|strain|, |stress|) stations of one side for
    HystereticSM (the flat cap is appended by the caller)."""
    p = law.params
    if law.model == "user":
        side = [(abs(e), abs(s)) for e, s in law.user_points
                if e * sign > 0.0]
        side.sort()
        return _simplify(side, HSM_MAX_POINTS - 1)
    if law.kind == "steel":
        ey = p["fy"] / p["E"]
        if law.model == "simple":
            end = abs(law.range[1])
            xs = [ey, end / 1.1]
        else:
            esh, esu = p["eps_sh"], p["eps_su"]
            r = esu - esh
            xs = [ey, esh, esh + 0.2 * r, esh + 0.5 * r, esu]
        return [(x, abs(law.exact(sign * x))) for x in xs]
    # concrete
    if sign < 0:
        e0 = p["eps_c0"]
        if law.model == "mander":
            ecu = p["eps_cu"]
            xs = [e0 / 3.0, 2.0 * e0 / 3.0, e0, 0.5 * (e0 + ecu), ecu]
            pts = [(x, abs(law.exact(-x))) for x in xs]
            pts.append((ecu * 1.001, 0.0))           # crushing drop
            return pts
        xs = [e0 / 3.0, 2.0 * e0 / 3.0, e0, p["eps_u"]]
        return [(x, abs(law.exact(-x))) for x in xs]
    ft = p.get("ft", 0.0)
    if ft > 0.0:
        et, etu = ft / p["E0"], p["eps_tu"]
        if law.model == "mander":
            xs = [et, et + 0.25 * (etu - et), et + 0.5 * (etu - et), etu]
            pts = [(x, abs(law.exact(x))) for x in xs]
            pts.append((etu * 1.001, 0.0))
            return pts
        return [(et, ft), (etu, 0.0)]
    return []


def _finish_point_law(law: UniaxialLaw, hyst: str) -> None:
    """Fill ``law.points`` / ``ops_type`` / ``ops_args`` for the point-based
    OpenSees materials (HystereticSM or ElasticMultiLinear)."""
    if hyst == "elastic":
        sides = {}
        for sign in (-1, 1):
            if law.model == "user":
                side = sorted((abs(e), abs(s)) for e, s in law.user_points
                              if e * sign > 0.0)
            else:
                side = _exact_side(law, sign, EML_POINTS_PER_SIDE)
            if side:
                side = side + [(CAP_FACTOR * side[-1][0], side[-1][1])]
            else:                      # e.g. concrete without tension
                end = abs(law.range[1 if sign > 0 else 0]) or 1e-3
                side = [(CAP_FACTOR * end, 0.0)]
            sides[sign] = side
        pts = ([(-a, -s) for a, s in reversed(sides[-1])] + [(0.0, 0.0)]
               + [(a, s) for a, s in sides[1]])
        law.points = pts
        law.ops_type = "ElasticMultiLinear"
        law.ops_args = [0.0, "-strain", *[e for e, _ in pts],
                        "-stress", *[s for _, s in pts]]
        law.notes.append("elastic: nonlinear-elastic ElasticMultiLinear "
                         "(loads and unloads along the backbone, no energy "
                         "dissipation)")
        return
    peak = max(abs(law.exact(law.range[0])), abs(law.exact(law.range[1])),
               max((abs(law.exact(k)) for k in law.key_strains), default=0.0),
               1e-9)
    floor = TINY * peak
    sides = {}
    for sign in (-1, 1):
        st = _envelope_stations(law, sign)
        if not st:
            # no stress on this side (e.g. concrete without tension):
            # a negligible elastic-perfectly-plastic stub
            E0 = law.params.get("E0", law.params.get("E", 1.0))
            st = [(floor / E0, floor)]
        st = [(a, max(s, floor)) for a, s in st]
        if len(st) > HSM_MAX_POINTS - 1:
            st = _simplify(st, HSM_MAX_POINTS - 1)
        # HystereticSM needs the first TWO segments of each side rising:
        # split the first segment at its midpoint when the second one
        # does not rise (plateau / softening / single station)
        if len(st) < 2 or st[1][1] <= st[0][1]:
            if len(st) > HSM_MAX_POINTS - 2:
                st = _simplify(st, HSM_MAX_POINTS - 2)
            st.insert(0, (0.5 * st[0][0], 0.5 * st[0][1]))
        st.append((CAP_FACTOR * st[-1][0], st[-1][1]))
        sides[sign] = st
    law.points = ([(-a, -s) for a, s in reversed(sides[-1])] + [(0.0, 0.0)]
                  + [(a, s) for a, s in sides[1]])
    pinch_x, pinch_y, d1, d2, beta = HYSTERESIS_PARAMS[hyst]
    pos = [v for a, s in sides[1] for v in (s, a)]
    neg = [v for a, s in sides[-1] for v in (-s, -a)]
    law.ops_type = "HystereticSM"
    law.ops_args = ["-posEnv", *pos, "-negEnv", *neg,
                    "-pinch", pinch_x, pinch_y, "-damage", d1, d2,
                    "-beta", beta]
    rule = {"kinematic": "no pinching / no degradation (peak-oriented "
                         "Hysteretic reloading — an approximation of "
                         "kinematic hardening)",
            "takeda": "Takeda-like: unloading stiffness k*mu^-0.4 "
                      "(beta = 0.4), no pinching",
            "pivot": "pivot-like pinching (pinchX = 0.5, pinchY = 0.3); "
                     "an approximation of the Dowell pivot rule"}[hyst]
    law.notes.append(f"{hyst}: HystereticSM with a <= {HSM_MAX_POINTS}-point "
                     f"envelope per side (exact at the stations, linear "
                     f"between, flat beyond); {rule}")


def curve_payload(mat, n: int = 80) -> dict:
    """``POST /api/materials/curve`` response for one material."""
    law = material_law(mat)
    if law is None:
        law = legacy_law(mat)
    strains, stresses = law.sample(n)
    ss = getattr(mat, "stress_strain", None)
    return {"strain": strains, "stress": stresses,
            "model": ss.get("model", "default") if ss else "default",
            "hysteresis": law.hysteresis,
            "opensees": law.ops_type,
            "notes": list(law.notes)}
