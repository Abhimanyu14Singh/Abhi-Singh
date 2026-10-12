"""Frame end releases / partial fixity and joint local axes (model side).

ETABS Assign > Frame > Releases/Partial Fixity and Assign > Joint > Local
Axes.  CONTRACT section "Frame end releases, partial fixity and joint local
axes".

Frame end releases
------------------
``FrameMember.end_releases`` (None = none, the default; emitted by
``to_dict`` only when set)::

    {"i": {"V2": 0.0, "M3": 1500.0}, "j": {"T": 0.0}}

Keys of each end are the released local DOFs ``P V2 V3 T M2 M3`` (axial,
shear along local 2 / 3, torsion, moment about local 2 / 3).  The value is
the partial-fixity spring stiffness (kN/m for P/V2/V3, kN*m/rad for
T/M2/M3): ``0`` = fully released, ``> 0`` = an elastic spring between the
joint and the member end in that local DOF.  A DOF not listed is
continuous (rigid).

The legacy ``releases`` tokens stay the canonical spelling of the common
pin: ``"Mi"`` == ``{"i": {"M2": 0, "M3": 0}}`` (and ``"Mj"`` for end j).
:func:`effective` merges both, :func:`canonical` writes the canonical form
back (a pure M2+M3 pin with no springs becomes the token), so every legacy
model -- and every model whose releases are only pins -- serialises and
analyses byte-identically to the pre-feature engine.

Stability (ETABS rules; a partial-fixity spring > 0 is NOT a release for
these checks): releasing P at both ends, V2 at both ends, V3 at both ends,
T at both ends, M2 at both ends together with V3 at either end, or M3 at
both ends together with V2 at either end leaves the member a mechanism.
:func:`unstable` lists the violated rules; Check Model reports them as
``RELEASE_UNSTABLE`` warnings and the engine warns and keeps the solve
regular with a tiny residual stiffness (see engine/releases_eng.py).

Joint local axes
----------------
``BuildingModel.joint_local_axes`` (empty = none; emitted only when set)::

    [{"point": [x, y, z], "angles": [a, b, c]}, ...]

Angles in degrees.  The local triad is ``R = Rz(a) @ Ry(b) @ Rx(c)``
(intrinsic rotations: ``a`` about global Z, then ``b`` about the rotated
2 axis, then ``c`` about the rotated 1 axis); local axes 1/2/3 are the
COLUMNS of ``R`` (global components).  With all angles 0 the local axes
are the global X/Y/Z.  Joint local axes re-orient, at that joint:

* explicit restraints (``supports[].restraints`` are read in the joint
  local 1/2/3 axes);
* point springs (``spring_supports`` stiffness / named property axes are
  relative to the joint local axes);
* joint loads entered in the joint local system
  (:func:`joint_load_to_global`; stored loads stay global).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

DOFS: Tuple[str, ...] = ("P", "V2", "V3", "T", "M2", "M3")
ENDS: Tuple[str, ...] = ("i", "j")
TRANSLATIONAL = ("P", "V2", "V3")
ROTATIONAL = ("T", "M2", "M3")
LEGACY_PIN = {"M2": 0.0, "M3": 0.0}
#: local 12-vector index of each DOF at end i (end j: + 6)
DOF_INDEX = {d: k for k, d in enumerate(DOFS)}

_TOKEN = {"i": "Mi", "j": "Mj"}

# ETABS unstable release combinations (each needs FULL releases, k == 0)
_UNSTABLE_RULES = (
    ("P", None, "P released at both ends (axial mechanism)"),
    ("V2", None, "V2 released at both ends (shear mechanism)"),
    ("V3", None, "V3 released at both ends (shear mechanism)"),
    ("T", None, "T released at both ends (torsional mechanism)"),
    ("M2", "V3", "M2 released at both ends with V3 released at an end"),
    ("M3", "V2", "M3 released at both ends with V2 released at an end"),
)


# ------------------------------------------------------------ parsing
def _num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def normalize(er, where: str = "end_releases") -> Optional[dict]:
    """Validated copy of an ``end_releases`` value (None when empty)."""
    if er is None:
        return None
    if not isinstance(er, dict):
        raise ValueError(f"{where} must be an object {{'i': {{...}}, "
                         "'j': {...}}")
    bad = set(er) - set(ENDS)
    if bad:
        raise ValueError(f"{where}: unknown end(s) {sorted(bad)} "
                         "(use 'i' / 'j')")
    out: Dict[str, Dict[str, float]] = {}
    for end in ENDS:
        sub = er.get(end)
        if sub is None:
            continue
        if isinstance(sub, (list, tuple)):          # ["V2", "M3"] shorthand
            sub = {str(k): 0.0 for k in sub}
        if not isinstance(sub, dict):
            raise ValueError(f"{where}.{end} must be an object "
                             "{dof: spring}")
        e: Dict[str, float] = {}
        for k, v in sub.items():
            if k not in DOFS:
                raise ValueError(f"{where}.{end}: unknown DOF {k!r} "
                                 f"(use {', '.join(DOFS)})")
            if v is None:
                v = 0.0
            if not _num(v) or v < 0.0:
                raise ValueError(f"{where}.{end}.{k}: spring stiffness must "
                                 f"be a finite value >= 0 (got {v!r})")
            e[k] = float(v)
        if e:
            out[end] = {d: e[d] for d in DOFS if d in e}
    return out or None


def member_to_dict(m) -> dict:
    er = getattr(m, "end_releases", None)
    if not er:
        return {}
    return {"end_releases": {end: dict(er[end]) for end in ENDS
                             if er.get(end)}}


def member_from_dict(m, md: dict) -> None:
    er = md.get("end_releases")
    if er is not None:
        m.end_releases = normalize(er, f"Member {m.uid}: end_releases")


# ------------------------------------------------------------ semantics
def effective(m) -> Dict[str, Dict[str, float]]:
    """{"i": {dof: k}, "j": {dof: k}}: legacy tokens + end_releases."""
    toks = m.release_tokens()
    er = getattr(m, "end_releases", None) or {}
    out: Dict[str, Dict[str, float]] = {}
    for end in ENDS:
        e: Dict[str, float] = {}
        if _TOKEN[end] in toks:
            e.update(LEGACY_PIN)
        for d, k in (er.get(end) or {}).items():
            e[d] = float(k)
        out[end] = {d: e[d] for d in DOFS if d in e}
    return out


def has_any(m) -> bool:
    return bool(m.release_tokens()) or bool(getattr(m, "end_releases", None))


def has_extended(m) -> bool:
    """True when the member uses releases beyond the legacy Mi/Mj pins."""
    return bool(getattr(m, "end_releases", None))


def canonical(eff: Dict[str, Dict[str, float]]) -> Tuple[str, Optional[dict]]:
    """(releases tokens, end_releases) canonical spelling of ``eff``: an
    end that is exactly the M2+M3 pin without springs is the legacy
    token; anything else lives in end_releases."""
    toks: List[str] = []
    er: Dict[str, Dict[str, float]] = {}
    for end in ENDS:
        e = {d: float(k) for d, k in (eff.get(end) or {}).items()}
        if not e:
            continue
        if e == LEGACY_PIN:
            toks.append(_TOKEN[end])
        else:
            er[end] = {d: e[d] for d in DOFS if d in e}
    return ",".join(toks), (er or None)


def set_effective(m, eff: Dict[str, Dict[str, float]]) -> None:
    m.releases, m.end_releases = canonical(eff)


def is_native_end(e: Dict[str, float]) -> bool:
    """A full M2 and/or M3 release with no other DOF and no spring: the
    exact element-level release (elasticBeamColumn -releasey/-releasez)."""
    return bool(e) and set(e) <= {"M2", "M3"} and all(k == 0.0
                                                      for k in e.values())


def unstable(m) -> List[str]:
    """Violated ETABS stability rules of member ``m`` (empty = stable)."""
    eff = effective(m)
    full = {end: {d for d, k in eff[end].items() if k == 0.0} for end in ENDS}
    out = []
    for both, either, msg in _UNSTABLE_RULES:
        if both in full["i"] and both in full["j"]:
            if either is None or either in full["i"] or either in full["j"]:
                out.append(msg)
    return out


def unstable_dofs(m) -> Dict[str, set]:
    """Fully released DOFs per end taking part in a violated rule."""
    eff = effective(m)
    full = {end: {d for d, k in eff[end].items() if k == 0.0} for end in ENDS}
    out: Dict[str, set] = {"i": set(), "j": set()}
    for both, either, _ in _UNSTABLE_RULES:
        if both in full["i"] and both in full["j"]:
            if either is None:
                out["i"].add(both)
                out["j"].add(both)
            elif either in full["i"] or either in full["j"]:
                for end in ENDS:
                    out[end].add(both)
                    if either in full[end]:
                        out[end].add(either)
    return out


def validate_member(m) -> None:
    """Structural validation (raises ValueError)."""
    er = getattr(m, "end_releases", None)
    if er is None:
        return
    m.end_releases = normalize(er, f"Member {m.uid}: end_releases")


def split_for_piece(m, k: int, n: int):
    """``end_releases`` of piece ``k`` of ``n`` when member ``m`` is split
    into sub-members (curved chords, nonprismatic pieces, Divide Frames):
    end i stays on the first piece, end j on the last."""
    er = getattr(m, "end_releases", None)
    if not er:
        return None
    out = {}
    if k == 0 and er.get("i"):
        out["i"] = dict(er["i"])
    if k == n - 1 and er.get("j"):
        out["j"] = dict(er["j"])
    return out or None


# ------------------------------------------------------------ joint axes
def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]


def _ry(b):
    c, s = math.cos(b), math.sin(b)
    return [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]]


def _rx(c_):
    c, s = math.cos(c_), math.sin(c_)
    return [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]


def _mm(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def axes_rows(angles: Sequence[float]) -> List[List[float]]:
    """Rows = local 1/2/3 unit vectors (global components)."""
    a, b, c = (math.radians(float(v)) for v in angles)
    R = _mm(_mm(_rz(a), _ry(b)), _rx(c))
    rows = [[R[0][j], R[1][j], R[2][j]] for j in range(3)]
    # clean round-off so 90-degree rotations give exact 0 / 1 entries
    return [[0.0 if abs(v) < 1e-15 else v for v in r] for r in rows]


def _pkey(p) -> Tuple[float, float, float]:
    return (round(float(p[0]), 6), round(float(p[1]), 6),
            round(float(p[2]), 6))


def normalize_joint_axes(lst) -> List[dict]:
    if lst is None:
        return []
    if not isinstance(lst, list):
        raise ValueError("joint_local_axes must be a list")
    out, seen = [], set()
    for k, e in enumerate(lst):
        where = f"joint_local_axes[{k}]"
        if not isinstance(e, dict):
            raise ValueError(f"{where} must be an object")
        bad = set(e) - {"point", "angles"}
        if bad:
            raise ValueError(f"{where}: unknown key(s) {sorted(bad)}")
        p = e.get("point")
        if (not isinstance(p, (list, tuple)) or len(p) != 3
                or not all(_num(v) for v in p)):
            raise ValueError(f"{where}.point must be 3 finite numbers")
        ang = e.get("angles", [0.0, 0.0, 0.0])
        if (not isinstance(ang, (list, tuple)) or len(ang) != 3
                or not all(_num(v) for v in ang)):
            raise ValueError(f"{where}.angles must be 3 finite numbers "
                             "(deg)")
        key = _pkey(p)
        if key in seen:
            raise ValueError(f"{where}: duplicate joint {tuple(p)}")
        seen.add(key)
        out.append({"point": [float(v) for v in p],
                    "angles": [float(v) for v in ang]})
    return out


def model_to_dict(model) -> dict:
    jla = getattr(model, "joint_local_axes", None)
    return {"joint_local_axes": [dict(point=list(e["point"]),
                                      angles=list(e["angles"]))
                                 for e in jla]} if jla else {}


def model_from_dict(model, d: dict) -> None:
    """Members' end_releases + model joint_local_axes (absent = none)."""
    for m, md in zip(model.members, d.get("members") or []):
        member_from_dict(m, md)
    if d.get("joint_local_axes") is not None:
        model.joint_local_axes = normalize_joint_axes(d["joint_local_axes"])


def validate_model(model) -> None:
    for m in model.members:
        validate_member(m)
    jla = getattr(model, "joint_local_axes", None)
    if jla:
        model.joint_local_axes = normalize_joint_axes(jla)


def joint_axes_map(model) -> Dict[Tuple[float, float, float],
                                  List[List[float]]]:
    """{point key: rows} for joints whose axes are not the identity."""
    out = {}
    for e in getattr(model, "joint_local_axes", None) or []:
        if any(float(v) != 0.0 for v in e["angles"]):
            out[_pkey(e["point"])] = axes_rows(e["angles"])
    return out


def joint_axes_at(model, point) -> Optional[List[List[float]]]:
    jla = getattr(model, "joint_local_axes", None)
    if not jla:
        return None
    return joint_axes_map(model).get(_pkey(point))


def joint_load_to_global(model, point, local6: Sequence[float]
                         ) -> List[float]:
    """Joint load [F1, F2, F3, M1, M2, M3] given in the joint local axes
    -> global [FX, FY, FZ, MX, MY, MZ] (identity when the joint has no
    local axes)."""
    R = joint_axes_at(model, point)
    f = [float(v) for v in local6]
    if R is None:
        return f
    return ([sum(R[i][j] * f[i] for i in range(3)) for j in range(3)]
            + [sum(R[i][j] * f[3 + i] for i in range(3)) for j in range(3)])


def global_to_joint_local(model, point, g6: Sequence[float]) -> List[float]:
    """Inverse of :func:`joint_load_to_global` (also used for local
    reactions)."""
    R = joint_axes_at(model, point)
    g = [float(v) for v in g6]
    if R is None:
        return g
    return ([sum(R[i][j] * g[j] for j in range(3)) for i in range(3)]
            + [sum(R[i][j] * g[3 + j] for j in range(3)) for i in range(3)])


# ------------------------------------------------------------ Check Model
def check_issues(ctx) -> None:
    """Check Model hook: RELEASE_UNSTABLE warnings (ETABS-style) and
    JOINT_AXES_UNUSED info for local axes at a point without a joint."""
    model = ctx.model
    for m in model.members:
        if not has_any(m):
            continue
        for msg in unstable(m):
            mid = tuple(0.5 * (a + b) for a, b in zip(m.pi, m.pj))
            ctx.add("warning", "RELEASE_UNSTABLE",
                    f"Frame {m.uid}: unstable end releases - {msg}",
                    [m.uid], mid)
    jla = getattr(model, "joint_local_axes", None) or []
    if jla:
        keys = set()
        for m in model.members:
            keys.add(_pkey(m.pi))
            keys.add(_pkey(m.pj))
        for s in getattr(model, "supports", []) or []:
            keys.add(_pkey(s.point))
        for s in getattr(model, "spring_supports", []) or []:
            keys.add(_pkey(s.point))
        for e in jla:
            if _pkey(e["point"]) not in keys:
                ctx.add("info", "JOINT_AXES_UNUSED",
                        f"Joint local axes at {tuple(e['point'])} are not at "
                        "a frame end, support or spring joint",
                        [], tuple(e["point"]))


# ------------------------------------------------------------ DB tables
def tables():
    """Interactive-database tables (core.modeltables hook):
    'Frame Assignments - Releases and Partial Fixity' and
    'Joint Assignments - Local Axes'."""
    from skyframe.core.modeltables import Col, ObjectTable

    num = "number"
    G_ASN = "Model Definition > Assignments"

    def eff_of(o):
        toks = {t.strip() for t in str(o.get("releases", "")).split(",")
                if t.strip()}
        er = o.get("end_releases") or {}
        out = {}
        for end in ENDS:
            e = {}
            if _TOKEN[end] in toks:
                e.update(LEGACY_PIN)
            for d, k in (er.get(end) or {}).items():
                e[d] = float(k or 0.0)
            out[end] = e
        return out

    def write(o, eff):
        toks, er = canonical(eff)
        o["releases"] = toks
        if er:
            o["end_releases"] = er
        else:
            o.pop("end_releases", None)

    cols = [Col("uid", "Frame", "text", editable=False)]
    for end in ENDS:
        for d in DOFS:
            def get(o, dd, end=end, d=d):
                return d in eff_of(o)[end]

            def put(o, v, dd, end=end, d=d):
                eff = eff_of(o)
                if v:
                    eff[end].setdefault(d, 0.0)
                else:
                    eff[end].pop(d, None)
                write(o, eff)
            cols.append(Col(f"{d.lower()}{end}", f"{d}{end.upper()}",
                            "bool", default=False, get=get, put=put))
    for end in ENDS:
        for d in DOFS:
            q = "stiffness" if d in TRANSLATIONAL else "rot_stiffness"

            def getk(o, dd, end=end, d=d):
                return float(eff_of(o)[end].get(d, 0.0))

            def putk(o, v, dd, end=end, d=d):
                v = float(v or 0.0)
                if v < 0.0:
                    raise ValueError("spring stiffness must be >= 0")
                eff = eff_of(o)
                if d not in eff[end]:
                    return          # a spring only acts on a released DOF
                eff[end][d] = v
                write(o, eff)
            cols.append(Col(f"k_{d.lower()}{end}", f"{d}{end.upper()} Spring",
                            num, q, default=0.0, get=getk, put=putk))
    t1 = ObjectTable(
        "frame_releases", "Frame Releases and Partial Fixity", G_ASN,
        cols, container="members", kind="list", keycol="uid",
        can_add=False, can_delete=False)

    def pt_get(i):
        return lambda o, d: float((o.get("point") or [0.0] * 3)[i])

    def pt_put(i):
        def f(o, v, d):
            p = list(o.get("point") or [0.0] * 3)
            p[i] = float(v)
            o["point"] = p
        return f

    def an_get(i):
        return lambda o, d: float((o.get("angles") or [0.0] * 3)[i])

    def an_put(i):
        def f(o, v, d):
            a = list(o.get("angles") or [0.0] * 3)
            a[i] = float(v)
            o["angles"] = a
        return f
    t2 = ObjectTable(
        "joint_local_axes", "Joint Local Axes", G_ASN, [
            Col("x", "X", num, "length", required=True, get=pt_get(0),
                put=pt_put(0)),
            Col("y", "Y", num, "length", required=True, get=pt_get(1),
                put=pt_put(1)),
            Col("z", "Z", num, "length", required=True, get=pt_get(2),
                put=pt_put(2)),
            Col("angle_a", "Angle A (about Z)", num, "angle_deg",
                default=0.0, get=an_get(0), put=an_put(0)),
            Col("angle_b", "Angle B (about 2)", num, "angle_deg",
                default=0.0, get=an_get(1), put=an_put(1)),
            Col("angle_c", "Angle C (about 1)", num, "angle_deg",
                default=0.0, get=an_get(2), put=an_put(2)),
        ], container="joint_local_axes", kind="list",
        new_obj=lambda d: {"point": [0.0, 0.0, 0.0],
                           "angles": [0.0, 0.0, 0.0]})
    return [t1, t2]
