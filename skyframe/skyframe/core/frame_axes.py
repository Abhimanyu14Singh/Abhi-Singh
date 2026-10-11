"""Frame local axes: the ONE place that computes a frame member's local
1/2/3 triad (CONTRACT "Frame local axes: ETABS convention").

Two conventions, chosen per model by ``BuildingModel.frame_axis_convention``:

``"legacy"`` (dataclass default; every file saved without the key)
    The pre-existing SkyFrame rule, kept bit-for-bit.  Local 1 runs i -> j.
    Non-vertical members use ``vecxz = (dy, -dx, 0)`` (local 2 in the
    vertical plane through the member, pointing up; local 3 = 1 x 2).
    Vertical members use ``vecxz = (1, 0, 0)``, i.e. at angle 0 a column
    drawn bottom-up has local 2 = -Y and local 3 = +X (drawn top-down:
    local 2 = +Y, local 3 = +X).

``"etabs"`` (every new model made in the UI / API new-model paths)
    CSI ETABS / SAP2000 default: identical to legacy for non-vertical
    members; a VERTICAL member has local 2 = +X at angle 0 whichever way it
    was drawn and local 3 = 1 x 2, so a column drawn bottom-up has
    local 3 = +Y (``vecxz = (0, 1, 0)``) and one drawn top-down has
    local 1 = -Z, local 2 = +X, local 3 = -Y (``vecxz = (0, -1, 0)``).
    The section depth ``h`` (along local 2) therefore runs along global X.

In both conventions ``angle`` (degrees) rotates local 2/3 about local 1 by
the right-hand rule (ETABS "local axis 2 angle").  Exact multiples of 90 deg
use exact cos/sin under ``"etabs"`` so a converted model reproduces the
legacy axes bit-for-bit (the legacy branch keeps its historical floating
point evaluation untouched).

Member stamping: the convention is a MODEL field, but most axis consumers
only see a member, so every :class:`FrameMember` carries a non-serialised
``axis_convention`` stamp (``"legacy"`` by default).  ``from_dict``,
``add_member``, the engine entry and :func:`convert_model_to_etabs` stamp
the members (:func:`sync_members`).
"""
from __future__ import annotations

import math
from typing import Iterable, Tuple

Vec3 = Tuple[float, float, float]

LEGACY = "legacy"
ETABS = "etabs"
FRAME_AXIS_CONVENTIONS = (ETABS, LEGACY)

_TOL = 1e-6            # same vertical test as the OpenSees engine


def _unit(v: Vec3) -> Vec3:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def normalize_convention(value) -> str:
    """Validated convention string (ValueError otherwise)."""
    if value is None:
        return LEGACY
    if value not in FRAME_AXIS_CONVENTIONS:
        raise ValueError("frame_axis_convention must be one of "
                         f"{list(FRAME_AXIS_CONVENTIONS)} (got {value!r})")
    return value


def _exact_cos_sin(ang: float) -> Tuple[float, float]:
    q = ang / 90.0
    if q == int(q):
        k = int(q) % 4
        return ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))[k]
    a = math.radians(ang)
    return math.cos(a), math.sin(a)


def frame_local_axes(pi, pj, angle: float = 0.0,
                     convention: str = LEGACY
                     ) -> Tuple[Vec3, Vec3, Vec3, Vec3, bool]:
    """``(x, y, z, vecxz, vertical)`` of a straight frame from ``pi`` to
    ``pj``: local 1 = x, 2 = y, 3 = z; ``vecxz`` is the OpenSees
    geomTransf vector (the rotated local z); ``vertical`` flags a member
    whose plan projection is shorter than 1e-6 m."""
    d = (pj[0] - pi[0], pj[1] - pi[1], pj[2] - pi[2])
    x = _unit(d)
    vertical = math.hypot(d[0], d[1]) < _TOL
    ang = angle or 0.0
    if convention == ETABS and vertical:
        # ETABS: local 2 = +X for a vertical member (either drawing sense)
        y: Vec3 = (1.0, 0.0, 0.0)
        z = _cross(x, y)
        if ang != 0.0:
            ca, sa = _exact_cos_sin(ang)
            y, z = ((ca * y[0] + sa * z[0], ca * y[1] + sa * z[1],
                     ca * y[2] + sa * z[2]),
                    (ca * z[0] - sa * y[0], ca * z[1] - sa * y[1],
                     ca * z[2] - sa * y[2]))
        return x, y, z, z, vertical
    # ---- legacy rule (verbatim; also every non-vertical etabs member) ----
    if vertical:
        vecxz: Vec3 = (1.0, 0.0, 0.0)
    else:
        vecxz = _unit((x[1], -x[0], 0.0))
    y = _unit(_cross(vecxz, x))
    z = _cross(x, y)
    if abs(ang) > 1e-12:
        a = math.radians(ang)
        ca, sa = math.cos(a), math.sin(a)
        y2 = (ca * y[0] + sa * z[0], ca * y[1] + sa * z[1],
              ca * y[2] + sa * z[2])
        z2 = (ca * z[0] - sa * y[0], ca * z[1] - sa * y[1],
              ca * z[2] - sa * y[2])
        y, z = _unit(y2), _unit(z2)
        vecxz = z
    return x, y, z, vecxz, vertical


def member_convention(member) -> str:
    return getattr(member, "axis_convention", LEGACY) or LEGACY


def member_local_axes(member) -> Tuple[Vec3, Vec3, Vec3, Vec3, bool]:
    """:func:`frame_local_axes` of a :class:`FrameMember` (its stamp)."""
    return frame_local_axes(member.pi, member.pj,
                            getattr(member, "angle", 0.0) or 0.0,
                            member_convention(member))


def model_convention(model) -> str:
    return getattr(model, "frame_axis_convention", LEGACY) or LEGACY


def stamp(members: Iterable, convention: str) -> None:
    for m in members:
        m.axis_convention = convention


def sync_members(model) -> None:
    """Stamp every member with the model's convention (from_dict / API)."""
    stamp(model.members, model_convention(model))


def sync_members_if_etabs(model) -> None:
    """Engine entry: an ``"etabs"`` model stamps its members; a legacy one
    leaves existing stamps alone (sub-models built by the staged / derived
    paths keep the stamps of the members they borrow)."""
    if model_convention(model) == ETABS:
        stamp(model.members, ETABS)


def _wrap180(a: float) -> float:
    return ((a + 180.0) % 360.0 + 360.0) % 360.0 - 180.0


def converted_angle(angle: float) -> float:
    """ETABS angle reproducing the legacy local axes of a VERTICAL member
    at legacy ``angle`` (either drawing sense): ``angle - 90`` wrapped to
    [-180, 180)."""
    return _wrap180(float(angle or 0.0) - 90.0)


def convert_model_to_etabs(model) -> dict:
    """Switch a legacy model to the ETABS convention IN PLACE keeping every
    member's physical orientation (identical local axes => identical
    analysis results).  Only straight vertical members change ``angle``;
    curved members are convention-independent (their chord angles are
    measured from the chord default axes) and are left untouched.

    Returns ``{"converted": bool, "from", "to", "members": [{uid, old_angle,
    new_angle}]}``; an already-``"etabs"`` model is a no-op."""
    before = model_convention(model)
    rep = {"converted": False, "from": before, "to": ETABS, "members": []}
    if before == ETABS:
        sync_members(model)
        return rep
    for m in model.members:
        if getattr(m, "curve", None) is not None:
            continue
        d = (m.pj[0] - m.pi[0], m.pj[1] - m.pi[1])
        if math.hypot(d[0], d[1]) >= _TOL:
            continue
        old = float(m.angle or 0.0)
        new = converted_angle(old)
        m.angle = new
        rep["members"].append({"uid": m.uid, "old_angle": old,
                               "new_angle": new})
    model.frame_axis_convention = ETABS
    sync_members(model)
    rep["converted"] = True
    return rep


def as_new_model(model):
    """Mark a freshly created model (UI / API new-model paths, importers)
    with the ETABS convention and stamp its members; returns the model."""
    model.frame_axis_convention = ETABS
    sync_members(model)
    return model


def convert_dict_to_etabs(d: dict) -> Tuple[dict, dict]:
    """Model-dict form of :func:`convert_model_to_etabs` (API echo)."""
    from skyframe.core.model import BuildingModel
    mdl = BuildingModel.from_dict(d)
    rep = convert_model_to_etabs(mdl)
    return mdl.to_dict(), rep
