"""Frame insertion points, joint offsets and automatic end offsets.

ETABS *Assign > Frame > Insertion Point* and *Assign > Frame > End Length
Offsets > Automatic from Connectivity* (CONTRACT "Insertion point, joint
offsets and automatic end offsets").

Insertion point (``FrameMember.cardinal_point`` + ``joint_offsets``)
--------------------------------------------------------------------
The member's REFERENCE line runs between its joints ``pi``/``pj``.  The
cardinal point says which point of the cross-section lies on that line
(ETABS numbering, local 2 = y "up", local 3 = z; "left" = -3 side, "right"
= +3 side; dims from the section's bounding box: ``h`` along local 2,
``b`` along local 3; the centroid is taken at the bounding-box centre)::

    7 top-left     8 top-center     9 top-right        (y = +h/2)
    4 mid-left     5 mid-center     6 mid-right        (y = 0)
    1 bot-left     2 bot-center     3 bot-right        (y = -h/2)
    10 centroid (default)           11 shear center (= centroid: the
                                       built-in sections are doubly
                                       symmetric — documented approximation)

The analytical (centroidal) element axis therefore sits at
``joint + joint_offset_end - (cy*y + cz*z)`` — a RIGID eccentricity applied
with OpenSees ``geomTransf ... -jntOffset`` (global components).  Optional
``joint_offsets = {"i": [d1,d2,d3], "j": [d1,d2,d3], "system":
"global"|"local"}`` add a further rigid offset of each element end from its
joint (ETABS "frame joint offsets"; local = member x/y/z).  With
``no_transform_stiffness=True`` (ETABS "Do not transform frame stiffness
for offsets from centroid") the cardinal-point part is NOT applied to the
stiffness (the element stays on the reference line; joint offsets still
apply).

Automatic end offsets (``FrameMember.end_offsets == "auto"``)
--------------------------------------------------------------
Each end's offset LENGTH is the largest half-extent, measured along this
member's axis, of the cross-sections of the other (non-parallel) frame
members with an end at the same joint::

    half(n) = 0.5 * (h_n * |x_m . y_n| + b_n * |x_m . z_n|)

(a beam end framing into a column -> half the column dimension in the
beam's direction; a column end under a beam -> half the beam depth; a
secondary beam into a girder -> half the girder width).  User-set
``rigid_i`` / ``rigid_j`` (> 0) win per end.  The effective rigid zone is
``auto_rigid_factor * length`` (ETABS rigid-zone factor, default 0 =
fully flexible) and is fed to the v0.9 rigid end-offset machinery.
"""
from __future__ import annotations

import math
import warnings
from typing import Dict, List, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]

CARDINAL_POINTS = tuple(range(1, 12))
DEFAULT_CARDINAL_POINT = 10
JOINT_OFFSET_SYSTEMS = ("global", "local")
END_OFFSET_MODES = ("manual", "auto")

# (row, col) multipliers: y = row * h/2, z = col * b/2
_CP_GRID = {1: (-1, -1), 2: (-1, 0), 3: (-1, 1),
            4: (0, -1), 5: (0, 0), 6: (0, 1),
            7: (1, -1), 8: (1, 0), 9: (1, 1),
            10: (0, 0), 11: (0, 0)}

_PARALLEL_TOL = 1e-6


def cardinal_point_local(cp: int, b: float, h: float) -> Tuple[float, float]:
    """Local (y, z) of cardinal point ``cp`` relative to the centroid (m)."""
    if cp not in _CP_GRID:
        raise ValueError(f"cardinal_point must be in 1..11 (got {cp!r})")
    r, c = _CP_GRID[cp]
    return 0.5 * h * r, 0.5 * b * c


def _axes(member):
    from skyframe.engine.opensees_engine import _local_axes
    return _local_axes(member)


def _comb(a: float, u: Vec3, b: float, v: Vec3) -> Vec3:
    return (a * u[0] + b * v[0], a * u[1] + b * v[1], a * u[2] + b * v[2])


def has_insertion(member) -> bool:
    """True when the member carries a non-default insertion point/offset."""
    cp = getattr(member, "cardinal_point", DEFAULT_CARDINAL_POINT)
    jo = getattr(member, "joint_offsets", None)
    return cp != DEFAULT_CARDINAL_POINT or bool(jo)


def member_joint_offsets(model, member) -> Optional[Tuple[Vec3, Vec3]]:
    """GLOBAL rigid offsets (e_i, e_j) from the joints to the element ends.

    ``None`` when the member is on its reference line (the default; the
    engine then emits no ``-jntOffset`` at all, so defaults are
    byte-identical).
    """
    if not has_insertion(member):
        return None
    x, y, z, _, _ = _axes(member)
    e_i: Vec3 = (0.0, 0.0, 0.0)
    e_j: Vec3 = (0.0, 0.0, 0.0)
    cp = getattr(member, "cardinal_point", DEFAULT_CARDINAL_POINT)
    if cp != DEFAULT_CARDINAL_POINT and not getattr(
            member, "no_transform_stiffness", False):
        sec = model.sections[member.section]
        if sec.h <= 0.0 and sec.b <= 0.0 and cp not in (5, 10, 11):
            warnings.warn(
                f"insertion point: member {member.uid!r} section "
                f"{sec.name!r} has no b/h dimensions; cardinal point "
                f"{cp} treated as the centroid", UserWarning)
        cy, cz = cardinal_point_local(cp, sec.b, sec.h)
        c = _comb(-cy, y, -cz, z)
        e_i, e_j = c, c
    jo = getattr(member, "joint_offsets", None) or {}
    if jo:
        system = jo.get("system", "global")
        for end in ("i", "j"):
            d = [float(v) for v in (jo.get(end) or (0.0, 0.0, 0.0))]
            if system == "local":
                g = tuple(d[0] * x[k] + d[1] * y[k] + d[2] * z[k]
                          for k in range(3))
            else:
                g = tuple(d)
            if end == "i":
                e_i = tuple(a + b for a, b in zip(e_i, g))
            else:
                e_j = tuple(a + b for a, b in zip(e_j, g))
    if max(abs(v) for v in e_i + e_j) < 1e-12:
        return None
    return e_i, e_j


def offset_at(e: Tuple[Vec3, Vec3], s: float, L: float) -> Vec3:
    """Offset at distance ``s`` along the reference line (linear in s)."""
    t = 0.0 if L <= 0.0 else min(max(s / L, 0.0), 1.0)
    return tuple(a + (b - a) * t for a, b in zip(e[0], e[1]))


def jnt_offset_args(ei: Vec3, ej: Vec3) -> List:
    """``geomTransf`` tail ``['-jntOffset', dXi, dYi, dZi, dXj, dYj, dZj]``
    (empty when both offsets vanish)."""
    if max(abs(v) for v in tuple(ei) + tuple(ej)) < 1e-15:
        return []
    return ["-jntOffset", *[float(v) for v in ei], *[float(v) for v in ej]]


def eccentric_moment(e: Vec3, f: Vec3) -> Vec3:
    """Moment about the JOINT of a force ``f`` acting at the element end
    offset ``e`` from it: ``e x f``."""
    return (e[1] * f[2] - e[2] * f[1],
            e[2] * f[0] - e[0] * f[2],
            e[0] * f[1] - e[1] * f[0])


# --------------------------------------------------------------------------- #
# automatic end offsets
# --------------------------------------------------------------------------- #
def _key(p) -> Vec3:
    return (round(float(p[0]), 6), round(float(p[1]), 6),
            round(float(p[2]), 6))


def auto_end_lengths(model, member) -> Tuple[float, float]:
    """Unfactored automatic end-offset LENGTHS (L_i, L_j) of ``member`` (m).

    See the module docstring for the rule.  Members whose sections carry
    no b/h contribute nothing.
    """
    x_m = _axes(member)[0]
    out = []
    for p in (member.pi, member.pj):
        k = _key(p)
        best = 0.0
        for n in model.members:
            if n is member or n.uid == member.uid:
                continue
            if _key(n.pi) != k and _key(n.pj) != k:
                continue
            x_n, y_n, z_n, _, _ = _axes(n)
            if abs(abs(sum(a * b for a, b in zip(x_m, x_n))) - 1.0) \
                    < _PARALLEL_TOL:
                continue                     # collinear continuation
            sec = model.sections.get(n.section)
            if sec is None:
                continue
            half = 0.5 * (sec.h * abs(sum(a * b for a, b in zip(x_m, y_n)))
                          + sec.b * abs(sum(a * b for a, b in zip(x_m, z_n))))
            best = max(best, half)
        out.append(best)
    return out[0], out[1]


def compute_auto_end_offsets(model) -> Dict[str, Tuple[float, float]]:
    """Effective rigid end offsets ``{uid: (off_i, off_j)}`` for every
    ``end_offsets == "auto"`` member (the model is never mutated).

    Per end: a user-set ``rigid_i``/``rigid_j`` > 0 keeps its manual
    effective value ``rigid_factor * rigid``; otherwise the automatic
    length times ``auto_rigid_factor``.  When the offsets would consume the
    whole member the centerline is kept with a ``UserWarning``.
    """
    out: Dict[str, Tuple[float, float]] = {}
    for m in model.members:
        if getattr(m, "end_offsets", "manual") != "auto":
            continue
        li, lj = auto_end_lengths(model, m)
        f = float(getattr(m, "auto_rigid_factor", 0.0))
        oi = m.rigid_offset_i if m.rigid_i > 0.0 else f * li
        oj = m.rigid_offset_j if m.rigid_j > 0.0 else f * lj
        if oi + oj >= m.length - 1e-9:
            warnings.warn(
                f"auto end offsets: member {m.uid!r} offsets "
                f"({oi + oj:.4g} m) would leave no clear span (length "
                f"{m.length:.4g} m); centerline kept", UserWarning)
            oi, oj = m.rigid_offset_i, m.rigid_offset_j
        out[m.uid] = (oi, oj)
    return out


def end_offset_report(model) -> Dict[str, dict]:
    """Per-member end-offset table (ETABS "Frame End Length Offsets").

    ``{uid: {"mode", "length_i", "length_j", "rigid_factor", "rigid_i",
    "rigid_j", "clear_span"}}`` — ``length_*`` are the (unfactored) end
    offset lengths, ``rigid_*`` the effective rigid zones the engine models
    and ``clear_span = L - length_i - length_j`` (the face-to-face span
    ETABS reports design forces over).
    """
    auto = compute_auto_end_offsets(model)
    rep: Dict[str, dict] = {}
    for m in model.members:
        mode = getattr(m, "end_offsets", "manual")
        if mode == "auto":
            li, lj = auto_end_lengths(model, m)
            li = m.rigid_i if m.rigid_i > 0.0 else li
            lj = m.rigid_j if m.rigid_j > 0.0 else lj
            ri, rj = auto[m.uid]
            fac = float(getattr(m, "auto_rigid_factor", 0.0))
        else:
            li, lj = m.rigid_i, m.rigid_j
            ri, rj = m.rigid_offset_i, m.rigid_offset_j
            fac = m.rigid_factor
        rep[m.uid] = {"mode": mode, "length_i": li, "length_j": lj,
                      "rigid_factor": fac, "rigid_i": ri, "rigid_j": rj,
                      "clear_span": max(m.length - li - lj, 0.0)}
    return rep


# --------------------------------------------------------------------------- #
# validation / (de)serialisation helpers
# --------------------------------------------------------------------------- #
def validate_member(m) -> None:
    """Raise ``ValueError`` on a bad insertion / end-offset assignment."""
    cp = getattr(m, "cardinal_point", DEFAULT_CARDINAL_POINT)
    if isinstance(cp, bool) or not isinstance(cp, int) \
            or cp not in CARDINAL_POINTS:
        raise ValueError(f"Member {m.uid}: cardinal_point must be an "
                         f"integer 1..11 (got {cp!r})")
    jo = getattr(m, "joint_offsets", None)
    if jo is not None:
        if not isinstance(jo, dict):
            raise ValueError(f"Member {m.uid}: joint_offsets must be a dict")
        bad = set(jo) - {"i", "j", "system"}
        if bad:
            raise ValueError(f"Member {m.uid}: joint_offsets has unknown "
                             f"keys {sorted(bad)}")
        if jo.get("system", "global") not in JOINT_OFFSET_SYSTEMS:
            raise ValueError(f"Member {m.uid}: joint_offsets system must be "
                             f"one of {JOINT_OFFSET_SYSTEMS}")
        for end in ("i", "j"):
            v = jo.get(end)
            if v is None:
                continue
            if (not isinstance(v, (list, tuple)) or len(v) != 3
                    or not all(isinstance(c, (int, float))
                               and not isinstance(c, bool)
                               and math.isfinite(c) for c in v)):
                raise ValueError(f"Member {m.uid}: joint_offsets[{end!r}] "
                                 "must be 3 finite numbers")
    if not isinstance(getattr(m, "no_transform_stiffness", False), bool):
        raise ValueError(f"Member {m.uid}: no_transform_stiffness must be "
                         "a bool")
    mode = getattr(m, "end_offsets", "manual")
    if mode not in END_OFFSET_MODES:
        raise ValueError(f"Member {m.uid}: end_offsets must be one of "
                         f"{END_OFFSET_MODES} (got {mode!r})")
    f = getattr(m, "auto_rigid_factor", 0.0)
    if (isinstance(f, bool) or not isinstance(f, (int, float))
            or not math.isfinite(f) or not 0.0 <= f <= 1.0):
        raise ValueError(f"Member {m.uid}: auto_rigid_factor must be in "
                         f"[0, 1] (got {f!r})")
    if (has_insertion(m) and getattr(m, "axial_limit", "both") != "both"):
        raise ValueError(f"Member {m.uid}: axial-only members do not "
                         "support insertion points / joint offsets")


def normalize_joint_offsets(jo) -> Optional[dict]:
    """Canonical ``joint_offsets`` dict (or None) from user/JSON input."""
    if not jo:
        return None
    if not isinstance(jo, dict):
        return jo                        # validate_member reports it
    out = {"system": str(jo.get("system", "global"))}
    for end in ("i", "j"):
        v = jo.get(end)
        if isinstance(v, (list, tuple)):
            try:
                out[end] = [float(c) for c in v]
            except (TypeError, ValueError):
                out[end] = list(v)
        elif v is not None:
            out[end] = v
        else:
            out[end] = [0.0, 0.0, 0.0]
    for k in jo:
        if k not in out:
            out[k] = jo[k]
    return out


def member_fields_to_dict(m) -> dict:
    """The insertion/end-offset fields of a member's JSON form."""
    jo = getattr(m, "joint_offsets", None)
    return {"cardinal_point": getattr(m, "cardinal_point",
                                      DEFAULT_CARDINAL_POINT),
            "joint_offsets": ({k: (list(v) if isinstance(v, (list, tuple))
                                   else v) for k, v in jo.items()}
                              if jo else None),
            "no_transform_stiffness": bool(getattr(
                m, "no_transform_stiffness", False)),
            "end_offsets": getattr(m, "end_offsets", "manual"),
            "auto_rigid_factor": float(getattr(m, "auto_rigid_factor", 0.0))}


def member_fields_from_dict(md: dict) -> dict:
    """Constructor kwargs for the insertion/end-offset fields (defaults
    when absent — older files load unchanged)."""
    cp = md.get("cardinal_point", DEFAULT_CARDINAL_POINT)
    if isinstance(cp, float) and cp.is_integer():
        cp = int(cp)
    return {"cardinal_point": cp,
            "joint_offsets": normalize_joint_offsets(
                md.get("joint_offsets")),
            "no_transform_stiffness": bool(md.get("no_transform_stiffness",
                                                  False)),
            "end_offsets": str(md.get("end_offsets", "manual")),
            "auto_rigid_factor": float(md.get("auto_rigid_factor", 0.0))}


def segment_jnt_offsets(e: Optional[Tuple[Vec3, Vec3]], L: float,
                        s0: float, s1: float) -> Tuple[Vec3, Vec3]:
    """(e at s0, e at s1); zeros when the member has no eccentricity."""
    if e is None:
        z = (0.0, 0.0, 0.0)
        return z, z
    return offset_at(e, s0, L), offset_at(e, s1, L)


__all__: Sequence[str] = (
    "CARDINAL_POINTS", "DEFAULT_CARDINAL_POINT", "END_OFFSET_MODES",
    "JOINT_OFFSET_SYSTEMS", "auto_end_lengths", "cardinal_point_local",
    "compute_auto_end_offsets", "eccentric_moment", "end_offset_report",
    "has_insertion", "jnt_offset_args", "member_joint_offsets",
    "offset_at", "segment_jnt_offsets", "validate_member")
