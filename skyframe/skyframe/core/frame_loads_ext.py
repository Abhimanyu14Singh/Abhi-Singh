"""Distributed frame MOMENTS and local-1 / local-3 distributed forces
(ETABS Assign > Frame Loads > Distributed: Load Type Moments; Direction
Local 1 / Local 3) -- model side.  CONTRACT "Distributed frame moments,
local 1 / 3 frame loads".

``MemberLoad.kind == "moment"`` keeps the legacy CONCENTRATED couple
(``moment_dist == ""``, ``w`` at fraction ``a``).  ``moment_dist`` makes it
a distributed couple per unit length (kN*m/m) over the fraction span
``[a, b]``:

* ``"uniform"``   -- ``w`` over [a, b];
* ``"trapezoid"`` -- linear from ``w`` (at a) to ``w2`` (at b).

``direction`` is one of :data:`loads_ext.MEMBER_MOMENT_DIRECTIONS`
(``local_1/2/3`` == ``local_x/y/z`` about the member local axes, or
``global_x/y/z``); the moment vector follows the right-hand rule about
that axis.  Distributed forces gain ``direction == "local_1"`` (axial,
along i -> j) and ``"local_3"`` (``"local_y"`` stays the local-2
direction).

Serialisation is additive: ``moment_dist`` is written only when set, so a
legacy model's JSON is byte-identical.  The analysis (exact closed-form
fixed-end forces, Euler and Timoshenko, end releases, auto-mesh segments;
statics-exact stations and deflections) lives in
:mod:`skyframe.engine.frame_dist_loads`.
"""

from __future__ import annotations

import math
from typing import List

MOMENT_DISTS = ("", "uniform", "trapezoid")
# directions added to MEMBER_LOAD_DIRECTIONS (distributed / point forces)
LOCAL_FORCE_DIRECTIONS = ("local_1", "local_3")


def _finite(v) -> bool:
    return (not isinstance(v, bool) and isinstance(v, (int, float))
            and math.isfinite(v))


def is_distributed_moment(ml) -> bool:
    return (getattr(ml, "kind", "") == "moment"
            and bool(getattr(ml, "moment_dist", "") or ""))


def validate(ml, pat_name: str) -> None:
    """Checks of the new fields (ValueError).  Called for every member
    load; a legacy load (``moment_dist == ""``) passes untouched."""
    md = getattr(ml, "moment_dist", "") or ""
    if md not in MOMENT_DISTS:
        raise ValueError(f"Pattern {pat_name}: moment_dist must be one of "
                         f"{MOMENT_DISTS[1:]} (or empty), got {md!r}")
    if not md:
        return
    if ml.kind != "moment":
        raise ValueError(f"Pattern {pat_name}: moment_dist applies to "
                         "kind 'moment' loads only")
    for k in ("w", "w2", "a", "b"):
        if not _finite(getattr(ml, k)):
            raise ValueError(f"Pattern {pat_name}: distributed moment {k} "
                             "must be a finite number")
    if not 0.0 <= ml.a <= ml.b <= 1.0:
        raise ValueError(f"Pattern {pat_name}: distributed moment span "
                         f"[{ml.a}, {ml.b}] must satisfy 0 <= a <= b <= 1")
    if getattr(ml, "projected", False):
        raise ValueError(f"Pattern {pat_name}: distributed moments cannot "
                         "be projected")


def to_dict_extra(ml) -> dict:
    md = getattr(ml, "moment_dist", "") or ""
    return {"moment_dist": md} if md else {}


def end_values(ml) -> tuple:
    """(m at a, m at b) of a distributed moment (kN*m/m)."""
    if (getattr(ml, "moment_dist", "") or "") == "trapezoid":
        return float(ml.w), float(ml.w2)
    return float(ml.w), float(ml.w)


def total_moment(ml, length: float) -> float:
    """Resultant couple (kN*m) of a distributed moment on a member of
    ``length`` (m)."""
    m1, m2 = end_values(ml)
    return 0.5 * (m1 + m2) * (ml.b - ml.a) * length


# --------------------------------------------------------------------------- #
# Interactive database editing: "Frame Loads - Moment" (concentrated) and
# "Frame Loads - Distributed Moment" (quantity "line_moment", kN*m/m).
# --------------------------------------------------------------------------- #
def modeltables(G_LOAD: str = "Model Definition > Loads") -> List:
    """``frame_moment_loads`` (concentrated couples, kind 'moment' without
    moment_dist) and ``frame_distributed_moments`` (moment_dist set).
    Untouched rows write back exactly what they read."""
    from . import modeltables as MT
    from .loads_ext import MEMBER_MOMENT_DIRECTIONS

    def target(lst, obj):
        out = dict(obj)
        out["kind"] = "moment"
        if not out.get("moment_dist"):
            out.pop("moment_dist", None)
        if not out.get("projected"):
            out.pop("projected", None)
        return "member_loads", out

    num = "number"
    pat_col = MT.Col("pattern", "Load Pattern", "enum",
                     enum=MT._names("patterns"), required=True)
    frm = MT.Col("member_uid", "Frame", "ref", ref="members", required=True)
    dcol = MT.Col("direction", "Direction", "enum",
                  enum=MEMBER_MOMENT_DIRECTIONS, default="local_z")
    conc = MT.PatternListTable(
        "frame_moment_loads", "Frame Loads - Moment", G_LOAD, [
            pat_col, frm, dcol,
            MT.Col("w", "M", num, "moment", required=True),
            MT.Col("a", "Rel. Dist.", num, default=0.5),
        ], lists=("member_loads",),
        belongs=lambda lst, e: (e.get("kind") == "moment"
                                and not e.get("moment_dist")),
        target=target,
        new_obj=lambda d: {"member_uid": "", "kind": "moment", "w": 0.0,
                           "w2": 0.0, "a": 0.5, "b": 1.0,
                           "direction": "local_z"})
    dist = MT.PatternListTable(
        "frame_distributed_moments", "Frame Loads - Distributed Moment",
        G_LOAD, [
            pat_col, frm,
            MT.Col("moment_dist", "Shape", "enum", enum=MOMENT_DISTS[1:],
                   default="uniform"),
            dcol,
            MT.Col("w", "m (start)", num, "line_moment", required=True),
            MT.Col("w2", "m (end)", num, "line_moment", default=0.0),
            MT.Col("a", "Rel. Dist. a", num, default=0.0),
            MT.Col("b", "Rel. Dist. b", num, default=1.0),
        ], lists=("member_loads",),
        belongs=lambda lst, e: (e.get("kind") == "moment"
                                and bool(e.get("moment_dist"))),
        target=target,
        new_obj=lambda d: {"member_uid": "", "kind": "moment", "w": 0.0,
                           "w2": 0.0, "a": 0.0, "b": 1.0,
                           "direction": "local_z", "moment_dist": "uniform"})
    return [conc, dist]
