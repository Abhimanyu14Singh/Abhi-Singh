"""User-defined plastic hinge properties + member hinge assignments (B10).

ETABS parity: **Define > Frame Hinge Properties** (user-defined backbone
hinges) -> ``model.hinge_properties``; **Assign > Frame > Hinges** ->
``FrameMember.hinges`` as a LIST of ``{"property", "relative_distance"}``;
**Assign > Frame > Hinge Overwrites** -> ``FrameMember.hinge_overwrites``.
Pure Python here (validation, (de)serialisation, the backbone ->
material-envelope conversion and the per-step state classification); the
OpenSees side is :mod:`skyframe.engine.user_hinges`.  CONTRACT section
"User-defined hinges and hinge overwrites" is the reference.

Hinge property (JSON)::

    {"type": "M3" | "M2" | "P" | "V2" | "PMM_fiber",
     "backbone": [[d, f], ...]                       # symmetric, or
               | {"positive": [[d, f], ...], "negative": [[d, f], ...]},
     "scale": {"yield_value": 1.0,                   # force SF
               "yield_deformation": null | float | "auto"},
     "acceptance": {"IO": x, "LS": x, "CP": x},      # optional
     "hysteresis": "kinematic" | "takeda" | "pivot" | "isotropic"
                   | "concrete",                     # default kinematic
     "drop_strength": "drops" | "holds",             # default drops
     "k_elastic": null | float,                      # optional
     "hysteresis_params": {"pinch_x", "pinch_y", "damage1", "damage2",
                           "beta"}}                  # optional

Backbone (ETABS points A-B-C-D-E): the first point is A = ``[0, 0]``;
then 1..5 more points with non-decreasing deformation ``d`` (PLASTIC
deformation, ETABS convention: rigid up to B; B's deformation is ignored —
every point is shifted so B sits at 0) and force ``f``.  Forces are
``f * yield_value`` (``yield_value`` 1.0 = absolute values); deformations
are ``d * SF_d`` with ``SF_d`` = 1 (``yield_deformation`` null =
absolute), the given float, or ``"auto"`` = the member's yield
deformation ``F_B / k_member``.  Negative-side points may be given with
either sign (magnitudes are used).  Acceptance thresholds are plastic
deformations in the same (scaled) units.

``k_member`` (the member stiffness the hinge works against): M3
``6 E I33 / L``, M2 ``6 E I22 / L``, P ``E A / L``, V2
``12 E I33 / L^3`` (modifier-scaled section properties).
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional, Tuple

HINGE_TYPES = ("M3", "M2", "P", "V2", "PMM_fiber")
HYSTERESIS_TYPES = ("kinematic", "takeda", "pivot", "isotropic", "concrete")
DROP_OPTIONS = ("drops", "holds")
# ETABS hinge states, in order
STATES = ("A-B", "B-IO", "IO-LS", "LS-CP", ">CP", "C-D", "D-E", ">E")
# legacy (v0.19 asce41 vocabulary) state of every ETABS state — the
# ``state`` key of a hinge result keeps the vocabulary existing consumers
# (server hinge_summary, the web viewer) understand.
LEGACY_STATE = {"A-B": "elastic", "B-IO": "IO", "IO-LS": "LS",
                "LS-CP": "CP", ">CP": "collapse", "C-D": "collapse",
                "D-E": "collapse", ">E": "collapse"}
# zeroLength -dir (1-based) of the hinge's active local DOF
HINGE_DIR = {"P": 1, "V2": 2, "M2": 5, "M3": 6}

# default hinge elastic stiffness k_e = UH_RIGID_FACTOR * k_member
# (ETABS hinges are rigid-plastic; the finite k_e is the documented
# idealization — its flexibility is in series with the member).  10 = the
# v0.5 stiff-hinge factor n (HINGE_STIFFNESS_FACTOR).  Larger values make
# the hinge stiffer but break Newton on load REVERSALS (k_e / plastic
# slope > ~1e3 cycles between the loading and unloading branches —
# probed: 100 x fails a cyclic chain that 10 x and 30 x solve); a
# property's ``k_elastic`` overrides it.
UH_RIGID_FACTOR = 10.0
# vertical strength drops (equal deformation, e.g. C -> D, or the drop
# to zero after the last point) are spread over a negative slope
# -UH_DROP_RATIO * k_member (a vertical drop has no tangent)
UH_DROP_RATIO = 0.1
# non-hinge DOFs of the hinge zeroLength: elastic, this factor times the
# member stiffness (the v0.19 HINGE_TIE_FACTOR value)
UH_TIE_FACTOR = 1.0e8
# "drops": residual strength past the last point (fraction of F_B)
UH_RESIDUAL = 1.0e-6
# a parallel Elastic(UH_PARALLEL_RATIO * k_member) keeps the hinge
# tangent > 0 on plateaus (force error <= 1e-9 * k_member * deformation)
UH_PARALLEL_RATIO = 1.0e-9
UH_MAX_POINTS = 6                     # A + up to 5 points (A..E + 1)

HYST_DEFAULTS = {
    "takeda": {"pinch_x": 1.0, "pinch_y": 1.0, "damage1": 0.0,
               "damage2": 0.0, "beta": 0.0},
    "pivot": {"pinch_x": 0.5, "pinch_y": 0.25, "damage1": 0.0,
              "damage2": 0.0, "beta": 0.0},
    "concrete": {"pinch_x": 0.8, "pinch_y": 0.2, "damage1": 0.0,
                 "damage2": 0.0, "beta": 0.0},
}
ISOTROPIC_NOTE = ("isotropic hysteresis approximated by the kinematic "
                  "MultiLinear rule (OpenSees has no multi-linear isotropic "
                  "uniaxial material): monotonic envelope exact, reversal "
                  "elastic range 2*F_y instead of 2*F_max")
OVERWRITE_KEYS = ("auto_subdivide", "relative_length")


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{what} must be a number (got {v!r})")
    v = float(v)
    if not math.isfinite(v):
        raise ValueError(f"{what} must be finite (got {v!r})")
    return v


def _points(raw, what: str) -> List[List[float]]:
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        raise ValueError(f"{what} must be a list of >= 2 [d, f] points "
                         "(A = [0, 0] first)")
    if len(raw) > UH_MAX_POINTS:
        raise ValueError(f"{what}: at most {UH_MAX_POINTS} points "
                         "(A..E + 1)")
    pts = []
    for k, p in enumerate(raw):
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise ValueError(f"{what}[{k}] must be [d, f]")
        pts.append([abs(_num(p[0], f"{what}[{k}][0]")),
                    abs(_num(p[1], f"{what}[{k}][1]"))])
    if pts[0] != [0.0, 0.0]:
        raise ValueError(f"{what}: the first point (A) must be [0, 0]")
    if pts[1][1] <= 0.0:
        raise ValueError(f"{what}: point B must have a force > 0")
    for k in range(1, len(pts)):
        if pts[k][0] < pts[k - 1][0]:
            raise ValueError(f"{what}: deformations must be "
                             "non-decreasing")
    return pts


def normalize_property(name: str, prop) -> dict:
    """Validated canonical copy of one hinge property (raises ValueError)."""
    nm = f"hinge property {name!r}"
    if not isinstance(prop, dict):
        raise ValueError(f"{nm} must be an object")
    allowed = {"type", "backbone", "scale", "acceptance", "hysteresis",
               "drop_strength", "k_elastic", "hysteresis_params"}
    bad = set(prop) - allowed
    if bad:
        raise ValueError(f"{nm}: unknown keys {sorted(bad)}")
    typ = prop.get("type")
    if typ not in HINGE_TYPES:
        raise ValueError(f"{nm}: type must be one of {HINGE_TYPES}, got "
                         f"{typ!r}")
    out: dict = {"type": typ}
    bbk = prop.get("backbone")
    if typ != "PMM_fiber" or bbk is not None:
        if isinstance(bbk, dict):
            if set(bbk) != {"positive", "negative"}:
                raise ValueError(f"{nm}: a two-sided backbone must be "
                                 '{"positive": [...], "negative": [...]}')
            out["backbone"] = {
                "positive": _points(bbk["positive"], f"{nm} backbone+"),
                "negative": _points(bbk["negative"], f"{nm} backbone-")}
        else:
            out["backbone"] = _points(bbk, f"{nm} backbone")
    sc = prop.get("scale") or {}
    if not isinstance(sc, dict) or set(sc) - {"yield_value",
                                              "yield_deformation"}:
        raise ValueError(f"{nm}: scale must be {{yield_value, "
                         "yield_deformation}")
    yv = _num(sc.get("yield_value", 1.0), f"{nm} scale.yield_value")
    if yv <= 0.0:
        raise ValueError(f"{nm}: scale.yield_value must be > 0")
    yd = sc.get("yield_deformation")
    if yd is not None and yd != "auto":
        yd = _num(yd, f"{nm} scale.yield_deformation")
        if yd <= 0.0:
            raise ValueError(f"{nm}: scale.yield_deformation must be > 0, "
                             'null or "auto"')
    out["scale"] = {"yield_value": yv, "yield_deformation": yd}
    acc = prop.get("acceptance")
    if acc is not None:
        if not isinstance(acc, dict) or set(acc) - {"IO", "LS", "CP"}:
            raise ValueError(f"{nm}: acceptance must be {{IO, LS, CP}}")
        acc = {k: _num(v, f"{nm} acceptance.{k}") for k, v in acc.items()}
        if any(v < 0.0 for v in acc.values()):
            raise ValueError(f"{nm}: acceptance values must be >= 0")
        seq = [acc[k] for k in ("IO", "LS", "CP") if k in acc]
        if seq != sorted(seq):
            raise ValueError(f"{nm}: acceptance must satisfy IO <= LS <= CP")
    out["acceptance"] = acc
    hy = prop.get("hysteresis", "kinematic")
    if hy not in HYSTERESIS_TYPES:
        raise ValueError(f"{nm}: hysteresis must be one of "
                         f"{HYSTERESIS_TYPES}, got {hy!r}")
    out["hysteresis"] = hy
    if (hy in ("kinematic", "isotropic")
            and isinstance(out.get("backbone"), dict)
            and out["backbone"]["positive"] != out["backbone"]["negative"]):
        raise ValueError(f"{nm}: {hy} hysteresis needs a symmetric backbone "
                         "(OpenSees MultiLinear is symmetric); use takeda/"
                         "pivot/concrete for an asymmetric one")
    ds = prop.get("drop_strength", "drops")
    if ds not in DROP_OPTIONS:
        raise ValueError(f"{nm}: drop_strength must be one of "
                         f"{DROP_OPTIONS}, got {ds!r}")
    out["drop_strength"] = ds
    ke = prop.get("k_elastic")
    if ke is not None:
        ke = _num(ke, f"{nm} k_elastic")
        if ke <= 0.0:
            raise ValueError(f"{nm}: k_elastic must be > 0")
    out["k_elastic"] = ke
    hp = prop.get("hysteresis_params")
    if hp is not None:
        keys = set(HYST_DEFAULTS["takeda"])
        if not isinstance(hp, dict) or set(hp) - keys:
            raise ValueError(f"{nm}: hysteresis_params keys must be in "
                             f"{sorted(keys)}")
        hp = {k: _num(v, f"{nm} hysteresis_params.{k}")
              for k, v in hp.items()}
    out["hysteresis_params"] = hp
    return out


def coerce_member_hinges(v):
    """``FrameMember.hinges``: the legacy mode string, or a list of
    ``{"property", "relative_distance"}`` (canonical float copies)."""
    if isinstance(v, (list, tuple)):
        out = []
        for h in v:
            if not isinstance(h, dict):
                out.append(h)                 # rejected by validation
                continue
            e = dict(h)
            if "relative_distance" in e and isinstance(
                    e["relative_distance"], (int, float)) \
                    and not isinstance(e["relative_distance"], bool):
                e["relative_distance"] = float(e["relative_distance"])
            out.append(e)
        return out
    return str(v)


def is_user(m) -> bool:
    return isinstance(getattr(m, "hinges", None), list)


def validate_member_hinges(m) -> None:
    """Shape of a list-valued ``FrameMember.hinges`` + hinge_overwrites."""
    seen = set()
    for k, h in enumerate(m.hinges):
        if not isinstance(h, dict) or set(h) != {"property",
                                                 "relative_distance"}:
            raise ValueError(f"Member {m.uid}: hinges[{k}] must be "
                             '{"property": name, "relative_distance": 0..1}')
        if not isinstance(h["property"], str) or not h["property"]:
            raise ValueError(f"Member {m.uid}: hinges[{k}].property must "
                             "be a hinge property name")
        d = _num(h["relative_distance"],
                 f"Member {m.uid}: hinges[{k}].relative_distance")
        if not 0.0 <= d <= 1.0:
            raise ValueError(f"Member {m.uid}: hinges[{k}].relative_distance"
                             f" must be in [0, 1] (got {d!r})")
        if (h["property"], d) in seen:
            raise ValueError(f"Member {m.uid}: duplicate hinge "
                             f"{h['property']!r} at {d!r}")
        seen.add((h["property"], d))
    if getattr(m, "axial_limit", "both") != "both" and m.hinges:
        raise ValueError(f"Member {m.uid}: a plastic hinge cannot be placed "
                         "on an axial-only member")
    normalize_overwrites(getattr(m, "hinge_overwrites", None), m.uid)


def normalize_overwrites(d, uid: str = "") -> Optional[dict]:
    if d is None:
        return None
    if not isinstance(d, dict) or set(d) - set(OVERWRITE_KEYS):
        raise ValueError(f"Member {uid}: hinge_overwrites must be "
                         '{"auto_subdivide": bool, "relative_length": x}')
    out: dict = {}
    if "auto_subdivide" in d:
        if not isinstance(d["auto_subdivide"], bool):
            raise ValueError(f"Member {uid}: hinge_overwrites."
                             "auto_subdivide must be a bool")
        out["auto_subdivide"] = d["auto_subdivide"]
    if "relative_length" in d and d["relative_length"] is not None:
        rl = _num(d["relative_length"],
                  f"Member {uid}: hinge_overwrites.relative_length")
        if not 0.0 < rl <= 0.5:
            raise ValueError(f"Member {uid}: hinge_overwrites."
                             "relative_length must be in (0, 0.5]")
        out["relative_length"] = rl
    return out


def validate_model(model) -> None:
    """Cross references: every property valid, every member hinge names
    an existing property; PMM_fiber hinges only at the member ends and
    never mixed with lumped hinges on one member; interior hinges need
    a member without rigid end offsets / joint offsets."""
    props = getattr(model, "hinge_properties", {}) or {}
    for name, p in props.items():
        normalize_property(name, p)
    for m in model.members:
        if not is_user(m):
            continue
        types = set()
        for h in m.hinges:
            p = props.get(h["property"])
            if p is None:
                raise ValueError(f"Member {m.uid}: unknown hinge property "
                                 f"{h['property']!r}")
            types.add(p["type"])
            d = float(h["relative_distance"])
            if p["type"] == "PMM_fiber" and d not in (0.0, 1.0):
                raise ValueError(f"Member {m.uid}: a PMM_fiber hinge must "
                                 "sit at relative_distance 0 or 1 (the "
                                 "HingeRadau fiber hinges are at the ends)")
            if 0.0 < d < 1.0 and (
                    m.rigid_offset_i + m.rigid_offset_j > 1e-9
                    or getattr(m, "joint_offsets", None)
                    or getattr(m, "cardinal_point", 10) != 10
                    or getattr(m, "end_offsets", "manual") == "auto"):
                raise ValueError(f"Member {m.uid}: interior user hinges are "
                                 "not supported on members with rigid end "
                                 "offsets / insertion offsets")
        if "PMM_fiber" in types and len(types) > 1:
            raise ValueError(f"Member {m.uid}: PMM_fiber hinges cannot be "
                             "mixed with lumped (M3/M2/P/V2) hinges")


# ---------------------------------------------------------- (de)serialise
def member_to_dict(m) -> dict:
    ov = getattr(m, "hinge_overwrites", None)
    return {} if ov is None else {"hinge_overwrites": dict(ov)}


def model_to_dict(model) -> dict:
    hp = getattr(model, "hinge_properties", {}) or {}
    if not hp:
        return {}
    return {"hinge_properties": {k: copy.deepcopy(normalize_property(k, v))
                                 for k, v in hp.items()}}


def model_from_dict(model, d: dict) -> None:
    hp = d.get("hinge_properties") or {}
    if not isinstance(hp, dict):
        raise ValueError("hinge_properties must be an object")
    model.hinge_properties = {str(k): normalize_property(str(k), v)
                              for k, v in hp.items()}
    for m, md in zip(model.members, d.get("members") or []):
        if md.get("hinge_overwrites") is not None:
            m.hinge_overwrites = normalize_overwrites(
                md["hinge_overwrites"], m.uid)


# ---------------------------------------------------------- geometry
def interior_points(model) -> Dict[str, List[Tuple[float, tuple]]]:
    """uid -> [(d, point)] interior (0 < d < 1) lumped user hinges — the
    mesher splits the member there so the zeroLength hinge has a node."""
    props = getattr(model, "hinge_properties", {}) or {}
    out: Dict[str, List[Tuple[float, tuple]]] = {}
    for m in model.members:
        if not is_user(m):
            continue
        for h in m.hinges:
            p = props.get(h.get("property"))
            d = float(h.get("relative_distance", 0.0))
            if p is None or p["type"] == "PMM_fiber" or not 0.0 < d < 1.0:
                continue
            pt = tuple(a + d * (b - a) for a, b in zip(m.pi, m.pj))
            out.setdefault(m.uid, []).append((d, pt))
    for lst in out.values():
        lst.sort(key=lambda e: e[0])
    return out


# ---------------------------------------------------------- envelope
def k_member(typ: str, E: float, A: float, I22: float, I33: float,
             L: float) -> float:
    if typ == "M3":
        return 6.0 * E * I33 / L
    if typ == "M2":
        return 6.0 * E * I22 / L
    if typ == "P":
        return E * A / L
    return 12.0 * E * I33 / L ** 3          # V2


def _sides(prop: dict) -> Tuple[list, list]:
    bb = prop["backbone"]
    if isinstance(bb, dict):
        return bb["positive"], bb["negative"]
    return bb, bb


def build_envelope(prop: dict, km: float) -> dict:
    """Material envelope of a lumped hinge property against a member of
    stiffness ``km``.

    Per side: scaled plastic points (B shifted to 0) -> TOTAL hinge
    deformation ``d = d_p + F / k_e``; any non-increasing step (a
    vertical drop) is spread over the slope ``-UH_DROP_RATIO * km``;
    ``drops`` appends the drop to ``UH_RESIDUAL * F_B`` (same slope) and a
    flat tail, ``holds`` a flat tail at the last force.  ``markers`` are
    the plastic deformations ``d - F/k_e`` of the material points B, C,
    D, E (state classification)."""
    sc = prop["scale"]
    sf_f = sc["yield_value"]
    pos, neg = _sides(prop)
    F_B = pos[1][1] * sf_f
    yd = sc["yield_deformation"]
    sf_d = (1.0 if yd is None else F_B / km if yd == "auto" else yd)
    k_e = prop.get("k_elastic") or UH_RIGID_FACTOR * km
    k_drop = UH_DROP_RATIO * km
    out = {"k_e": k_e, "k_member": km, "sf_d": sf_d, "F_B": F_B}
    for side, raw in (("positive", pos), ("negative", neg)):
        b0 = raw[1][0]
        pts = [(max(0.0, d - b0) * sf_d, f * sf_f) for d, f in raw[1:]]
        Fb = pts[0][1]
        tiny = 1.0e-6 * Fb / k_e
        tot: List[Tuple[float, float]] = []
        for dp, F in pts:
            d = dp + F / k_e
            if tot and d <= tot[-1][0] + tiny:
                d = tot[-1][0] + max(abs(tot[-1][1] - F) / k_drop, tiny)
            tot.append((d, F))
        markers = [d - F / k_e for d, F in tot]
        user_n = len(tot)
        if prop["drop_strength"] == "drops":
            Fr = UH_RESIDUAL * Fb
            dt = tot[-1][0] + max((tot[-1][1] - Fr) / k_drop, tiny)
            tot.append((dt, Fr))
            tot.append((2.0 * dt, Fr))
        else:
            tot.append((2.0 * tot[-1][0], tot[-1][1]))
        out[side] = {"points": [list(p) for p in tot], "markers": markers,
                     "n_user": user_n}
    return out


def classify(d: float, F: float, env: dict, acc: Optional[dict]) -> str:
    """ETABS state of a hinge at total deformation ``d`` / force ``F``.

    Plastic deformation ``dp = |d| - |F| / k_e`` on the side of ``d``;
    ``A-B`` while ``dp`` ~ 0; up to the C marker the acceptance bands
    (IO/LS/CP plastic thresholds, missing = never reached); past C the
    backbone segments C-D, D-E, >E (missing points = never reached)."""
    side = env["positive"] if d >= 0.0 else env["negative"]
    k_e = env["k_e"]
    dp = abs(d) - abs(F) / k_e
    first = side["points"][0][0]
    tol = 1.0e-6 * first
    if dp <= tol:
        return "A-B"
    mk = side["markers"]
    inf = math.inf
    mC = mk[1] if len(mk) > 1 else inf
    mD = mk[2] if len(mk) > 2 else inf
    mE = mk[3] if len(mk) > 3 else inf
    if dp <= mC + tol:
        sf = env["sf_d"]
        a = acc or {}
        for key, st in (("IO", "B-IO"), ("LS", "IO-LS"), ("CP", "LS-CP")):
            lim = a.get(key)
            if lim is None or dp <= lim * sf + tol:
                return st
        return ">CP"
    if dp <= mD + tol:
        return "C-D"
    if dp <= mE + tol:
        return "D-E"
    return ">E"
