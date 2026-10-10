"""Curved frame members (ETABS Draw > Draw Curved Beams) -- geometry,
validation, and the chord expansion used at analysis time.

A drawn member becomes CURVED when ``FrameMember.curve`` is set::

    {"type": "arc", "via": [x, y, z]}                        # 3-point arc
    {"type": "arc", "center": [x, y, z],
     "plane_normal": [nx, ny, nz]}                           # center form
    # optional for both:
    "segments": int >= 1 | None   # chords; None = auto (one per 10 deg)
    "local2": "in_plane" | "normal"   # local axis 2 (default "in_plane")

* 3-point arc: the circle through ``pi``, ``via``, ``pj``; the arc runs
  from ``pi`` THROUGH ``via`` to ``pj`` (plane normal = (via-pi) x (pj-pi)).
* Center form: ``|pi - center| == |pj - center|`` (1e-6 relative) and both
  ends in the plane through ``center`` normal to ``plane_normal``; the arc
  runs counter-clockwise about ``plane_normal`` from ``pi`` to ``pj``
  (sweep in (0, 360) deg).
* ``segments`` None -> ``ceil(sweep / 10 deg)`` equal-angle chords.

Analysis (``expand_members``): the drawn member is replaced by its chords
``<uid>~0 .. <uid>~(n-1)`` (straight elastic members joined at the arc
points).  Chord local axes are consistent along the arc: local 1 along the
chord; ``local2 == "in_plane"`` puts local 2 in the plane of curvature
(radial, sign chosen ONCE per member so that it agrees with the default
straight-member axis -- an arch in a vertical plane keeps local 2 "up",
the exact straight-beam convention; a plan-curved member whose in-plane
direction is horizontal gets local 2 pointing to the center); ``"normal"``
puts local 2 along the plane normal.  The member's own ``angle`` rotates
on top.  ``Mi`` / ``Mj`` releases and ``rigid_i`` / ``rigid_j`` go to the
first / last chord; foundation, additional mass, insertion cardinal point,
auto-mesh / output-station options are copied to every chord.

Member loads on a curved member are expressed along the ARC: ``a``/``b``
are fractions of the ARC length and ``w`` is per unit ARC length (or per
projected length when ``projected``).  Each chord receives the part of the
load lying on its arc interval (fractions mapped linearly), scaled by
``arc_len / chord_len`` so the total load is conserved exactly (projected
loads: unscaled -- the chord polyline has the arc's projection).  Point
loads / moments go to the chord holding their arc fraction, at the same
fraction of the chord.  Thermal loads are copied to every chord; self
weight and mass act on the chord polyline (relative shortfall
``1 - sin(x)/x`` with ``x`` = half the chord angle: 0.13 % at 10 deg).

Results (engine side :mod:`skyframe.engine.curved`): every chord keeps its
own ``member_stations`` entry; the drawn member is stitched in
``results["curved_frames"][uid]`` with stations along the arc coordinate
``s`` (0 .. arc length).
"""

from __future__ import annotations

import copy
import dataclasses
import math
from typing import Dict, List, Optional, Tuple

Vec3 = Tuple[float, float, float]

CURVE_TYPES = ("arc",)
LOCAL2_OPTIONS = ("in_plane", "normal")
AUTO_SEG_DEG = 10.0             # auto chord angle (deg)
MAX_SEGMENTS = 720
CHORD_SEP = "~"
_GEOM_TOL = 1e-6
_VERT_TOL = 1e-6                # = engine _TOL (vertical member test)


# --------------------------------------------------------------------------- #
# vectors
# --------------------------------------------------------------------------- #
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    if n < 1e-14:
        raise ValueError("degenerate vector")
    return (a[0] / n, a[1] / n, a[2] / n)


def _vec3(v, what: str) -> Vec3:
    if (not isinstance(v, (list, tuple)) or len(v) != 3
            or not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                       and math.isfinite(x) for x in v)):
        raise ValueError(f"{what} must be three finite numbers")
    return (float(v[0]), float(v[1]), float(v[2]))


# --------------------------------------------------------------------------- #
# normalisation / geometry
# --------------------------------------------------------------------------- #
def normalize_curve(c) -> Optional[dict]:
    """Canonical curve dict or ``None`` (no geometry check)."""
    if c is None:
        return None
    if not isinstance(c, dict):
        raise ValueError("curve must be an object or null")
    allowed = {"type", "via", "center", "plane_normal", "segments", "local2"}
    bad = set(c) - allowed
    if bad:
        raise ValueError(f"curve: unknown key(s) {sorted(bad)}")
    typ = c.get("type", "arc")
    if typ not in CURVE_TYPES:
        raise ValueError(f"curve.type must be one of {CURVE_TYPES}")
    has_via = c.get("via") is not None
    has_c = c.get("center") is not None
    if has_via == has_c:
        raise ValueError('curve: give exactly one of "via" or "center"')
    out: dict = {"type": "arc"}
    if has_via:
        if c.get("plane_normal") is not None:
            raise ValueError('curve: "plane_normal" is only used with '
                             '"center"')
        out["via"] = list(_vec3(c["via"], "curve.via"))
    else:
        out["center"] = list(_vec3(c["center"], "curve.center"))
        if c.get("plane_normal") is None:
            raise ValueError('curve: "center" needs "plane_normal"')
        n = _vec3(c["plane_normal"], "curve.plane_normal")
        if _norm(n) < 1e-12:
            raise ValueError("curve.plane_normal must be non-zero")
        out["plane_normal"] = list(n)
    seg = c.get("segments")
    if seg is not None:
        if isinstance(seg, bool) or float(seg) != int(seg):
            raise ValueError("curve.segments must be an integer")
        seg = int(seg)
        if not 1 <= seg <= MAX_SEGMENTS:
            raise ValueError(f"curve.segments must be in [1, {MAX_SEGMENTS}]")
    out["segments"] = seg
    l2 = c.get("local2", "in_plane")
    if l2 not in LOCAL2_OPTIONS:
        raise ValueError(f"curve.local2 must be one of {LOCAL2_OPTIONS}")
    out["local2"] = l2
    return out


def arc_geometry(pi, pj, curve: dict) -> dict:
    """``{center, radius, normal, e1, e2, sweep}`` (sweep in rad, the arc
    being ``center + R (cos t e1 + sin t e2)`` for t in [0, sweep])."""
    A = tuple(map(float, pi))
    C = tuple(map(float, pj))
    if "via" in curve:
        B = tuple(map(float, curve["via"]))
        a, b = _sub(A, C), _sub(B, C)
        axb = _cross(a, b)
        den = 2.0 * _dot(axb, axb)
        scale = max(_norm(a), _norm(b), 1.0)
        if _norm(axb) <= 1e-9 * scale * scale:
            raise ValueError("curve: pi, via and pj are collinear "
                             "(or coincident)")
        num = _cross(_sub(_mul(b, _dot(a, a)), _mul(a, _dot(b, b))), axb)
        cen = _add(C, _mul(num, 1.0 / den))
        n = _unit(_cross(_sub(B, A), _sub(C, A)))
    else:
        cen = tuple(map(float, curve["center"]))
        n = _unit(tuple(map(float, curve["plane_normal"])))
    R = _norm(_sub(A, cen))
    Rj = _norm(_sub(C, cen))
    if R < 1e-9:
        raise ValueError("curve: end i coincides with the arc center")
    if abs(R - Rj) > _GEOM_TOL * max(1.0, R):
        raise ValueError(f"curve: ends are not equidistant from the center "
                         f"(|pi-c| = {R:.6g}, |pj-c| = {Rj:.6g})")
    for p, nm in ((A, "pi"), (C, "pj")):
        if abs(_dot(_sub(p, cen), n)) > _GEOM_TOL * max(1.0, R):
            raise ValueError(f"curve: {nm} is not in the arc plane")
    e1 = _unit(_sub(A, cen))
    e1 = _unit(_sub(e1, _mul(n, _dot(e1, n))))
    e2 = _cross(n, e1)
    v = _sub(C, cen)
    t = math.atan2(_dot(v, e2), _dot(v, e1))
    if t <= 1e-12:
        t += 2.0 * math.pi
    if t >= 2.0 * math.pi - 1e-9:
        raise ValueError("curve: zero-length arc")
    if "via" in curve:
        vb = _sub(tuple(map(float, curve["via"])), cen)
        tb = math.atan2(_dot(vb, e2), _dot(vb, e1))
        if tb <= 0.0:
            tb += 2.0 * math.pi
        if not 0.0 < tb < t:                         # pragma: no cover
            raise ValueError("curve: via point is not between the ends")
    return {"center": cen, "radius": R, "normal": n, "e1": e1, "e2": e2,
            "sweep": t}


def n_segments(curve: dict, sweep: float) -> int:
    seg = curve.get("segments")
    if seg:
        return int(seg)
    return max(1, int(math.ceil(math.degrees(sweep) / AUTO_SEG_DEG - 1e-9)))


def arc_points(m, geom: Optional[dict] = None,
               n: Optional[int] = None) -> List[Vec3]:
    """The n+1 arc points (exact pi / pj at the ends)."""
    curve = m.curve
    g = geom or arc_geometry(m.pi, m.pj, curve)
    n = n or n_segments(curve, g["sweep"])
    c, R, e1, e2 = g["center"], g["radius"], g["e1"], g["e2"]
    pts = []
    for k in range(n + 1):
        t = g["sweep"] * k / n
        pts.append(tuple(c[i] + R * (math.cos(t) * e1[i] + math.sin(t) * e2[i])
                         for i in range(3)))
    pts[0] = tuple(map(float, m.pi))
    pts[-1] = tuple(map(float, m.pj))
    return pts


def arc_length(m) -> float:
    """Arc length of a curved member (chord length when straight)."""
    if getattr(m, "curve", None) is None:
        return m.length
    g = arc_geometry(m.pi, m.pj, m.curve)
    return g["radius"] * g["sweep"]


def _default_axes(pi, pj):
    """Engine default (angle = 0) local axes x, y, z (see
    ``opensees_engine._local_axes``)."""
    d = _sub(pj, pi)
    x = _unit(d)
    if math.hypot(d[0], d[1]) < _VERT_TOL:
        vecxz = (1.0, 0.0, 0.0)
    else:
        vecxz = _unit((x[1], -x[0], 0.0))
    y = _unit(_cross(vecxz, x))
    z = _cross(x, y)
    return x, y, z


def chord_angles(m, pts: List[Vec3], normal: Vec3) -> List[float]:
    """Per-chord ``angle`` (deg) putting local 2 on the curve convention."""
    mode = (m.curve or {}).get("local2", "in_plane")
    axes = [_default_axes(a, b) for a, b in zip(pts[:-1], pts[1:])]
    if mode == "normal":
        targets = [normal for _ in axes]
    else:
        targets = [_unit(_cross(normal, x)) for x, _, _ in axes]
    s = sum(_dot(t, y) for t, (_, y, _) in zip(targets, axes))
    sign = -1.0 if s < -1e-9 * len(axes) else 1.0
    out = []
    for t, (_, y, z) in zip(targets, axes):
        t = _mul(t, sign)
        a = math.degrees(math.atan2(_dot(t, z), _dot(t, y)))
        if abs(a) < 1e-9:
            a = 0.0
        out.append(a + float(getattr(m, "angle", 0.0) or 0.0))
    return out


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate_model(model) -> None:
    """Normalise + check every curved member (ValueError)."""
    curved = set()
    for m in model.members:
        c = getattr(m, "curve", None)
        if c is None:
            continue
        tag = f"Member {m.uid}"
        try:
            m.curve = normalize_curve(c)
            arc_geometry(m.pi, m.pj, m.curve)
        except ValueError as exc:
            raise ValueError(f"{tag}: {exc}") from None
        if CHORD_SEP in m.uid:
            raise ValueError(f"{tag}: a curved member uid may not contain "
                             f"{CHORD_SEP!r}")
        if m.axial_limit != "both":
            raise ValueError(f"{tag}: a curved member must have "
                             "axial_limit 'both'")
        if not (isinstance(m.hinges, str) and m.hinges == "none"):
            raise ValueError(f"{tag}: hinges are not supported on curved "
                             "members")
        if getattr(m, "hinge_overwrites", None):
            raise ValueError(f"{tag}: hinge overwrites are not supported on "
                             "curved members")
        if getattr(m, "joint_offsets", None):
            raise ValueError(f"{tag}: joint_offsets are not supported on "
                             "curved members")
        if getattr(m, "end_offsets", "manual") != "manual":
            raise ValueError(f"{tag}: automatic end offsets are not "
                             "supported on curved members")
        sec = model.sections.get(m.section)
        if getattr(sec, "kind", "prismatic") != "prismatic":
            raise ValueError(f"{tag}: a curved member needs a prismatic "
                             "section")
        curved.add(m.uid)
    if not curved:
        return
    names = set()
    for m in model.members:
        if m.uid in curved:
            n = n_segments(m.curve, arc_geometry(m.pi, m.pj, m.curve)["sweep"])
            names.update(f"{m.uid}{CHORD_SEP}{k}" for k in range(n))
    for m in model.members:
        if m.uid in names:
            raise ValueError(f"Member {m.uid}: uid collides with a "
                             "curved-member chord name")
    for td in getattr(model, "tendons", None) or []:
        host = td.get("host") if isinstance(td, dict) else None
        hosts = [host] if isinstance(host, str) else list(host or [])
        if any(h in curved for h in hosts):
            raise ValueError(f"Tendon {td.get('uid')!r}: curved members "
                             "cannot host tendons")


def member_to_dict(m) -> dict:
    c = getattr(m, "curve", None)
    return {} if c is None else {"curve": copy.deepcopy(c)}


# --------------------------------------------------------------------------- #
# analysis expansion
# --------------------------------------------------------------------------- #
def _chord_loads(ml, n: int, ratio: float, chords: List[str]):
    """MemberLoad pieces of one arc load on the n chords."""
    from .model import MemberLoad
    out = []
    kind = ml.kind
    if kind in ("point", "moment"):
        a = min(max(float(ml.a), 0.0), 1.0)
        k = min(n - 1, int(math.floor(a * n)))
        t = min(max(a * n - k, 0.0), 1.0)
        out.append(dataclasses.replace(ml, member_uid=chords[k], a=t))
        return out
    a, b = float(ml.a), float(ml.b)
    w1 = float(ml.w)
    w2 = float(ml.w) if kind == "udl" else float(ml.w2)
    f = 1.0 if getattr(ml, "projected", False) else ratio

    def w_at(s):
        if b - a <= 0.0:
            return w1
        return w1 + (w2 - w1) * (s - a) / (b - a)
    for k in range(n):
        s0, s1 = k / n, (k + 1) / n
        lo, hi = max(a, s0), min(b, s1)
        if hi - lo <= 1e-12:
            continue
        t0 = min(max((lo - s0) * n, 0.0), 1.0)
        t1 = min(max((hi - s0) * n, 0.0), 1.0)
        if kind == "udl":
            out.append(MemberLoad(chords[k], "udl", w1 * f, 0.0, t0, t1,
                                  ml.direction,
                                  projected=getattr(ml, "projected", False)))
        else:
            out.append(MemberLoad(chords[k], "trapezoid", w_at(lo) * f,
                                  w_at(hi) * f, t0, t1, ml.direction,
                                  projected=getattr(ml, "projected", False)))
    return out


def expand_members(model) -> Dict[str, dict]:
    """IN PLACE: replace every curved member of ``model`` (an analysis
    copy) by its chords; rewrite member loads / UDLs / thermal loads and
    group lists.  Returns ``{uid: info}`` for the results stitching."""
    from .model import MemberLoad
    info: Dict[str, dict] = {}
    new_members = []
    for m in model.members:
        if getattr(m, "curve", None) is None:
            new_members.append(m)
            continue
        g = arc_geometry(m.pi, m.pj, m.curve)
        n = n_segments(m.curve, g["sweep"])
        pts = arc_points(m, g, n)
        angs = chord_angles(m, pts, g["normal"])
        toks = m.release_tokens()
        chords = [f"{m.uid}{CHORD_SEP}{k}" for k in range(n)]
        S = g["radius"] * g["sweep"]
        clen = [math.dist(a, b) for a, b in zip(pts[:-1], pts[1:])]
        for k in range(n):
            rel = []
            if k == 0 and "Mi" in toks:
                rel.append("Mi")
            if k == n - 1 and "Mj" in toks:
                rel.append("Mj")
            ch = dataclasses.replace(
                m, uid=chords[k], pi=pts[k], pj=pts[k + 1], angle=angs[k],
                releases=",".join(rel),
                rigid_i=m.rigid_i if k == 0 else 0.0,
                rigid_j=m.rigid_j if k == n - 1 else 0.0,
                auto_mesh=copy.deepcopy(m.auto_mesh),
                output_stations=copy.deepcopy(m.output_stations))
            ch.curve = None
            new_members.append(ch)
        info[m.uid] = {
            "chords": chords, "points": [list(p) for p in pts],
            "s_nodes": [S * k / n for k in range(n + 1)],
            "chord_lengths": clen, "arc_length": S,
            "radius": g["radius"], "sweep_deg": math.degrees(g["sweep"]),
            "center": list(g["center"]), "plane_normal": list(g["normal"]),
            "segments": n, "local2": m.curve.get("local2", "in_plane"),
            "story": m.story, "section": m.section, "kind": m.kind}
    if not info:
        return info
    model.members = new_members
    for pat in model.patterns.values():
        loads = []
        for ml in pat.member_loads:
            ci = info.get(ml.member_uid)
            if ci is None:
                loads.append(ml)
                continue
            n = ci["segments"]
            ratio = (ci["arc_length"] / n) / ci["chord_lengths"][0]
            loads.extend(_chord_loads(ml, n, ratio, ci["chords"]))
        udls = []
        for u in pat.member_udls:
            ci = info.get(u.member_uid)
            if ci is None:
                udls.append(u)
                continue
            n = ci["segments"]
            ratio = (ci["arc_length"] / n) / ci["chord_lengths"][0]
            loads.extend(_chord_loads(
                MemberLoad(u.member_uid, "udl", float(u.w), 0.0, 0.0, 1.0,
                           "gravity"), n, ratio, ci["chords"]))
        pat.member_loads = loads
        pat.member_udls = udls
        th = []
        for tl in pat.thermal_loads:
            ci = info.get(tl.member_uid)
            if ci is None:
                th.append(tl)
                continue
            th.extend(dataclasses.replace(tl, member_uid=c)
                      for c in ci["chords"])
        pat.thermal_loads = th
    for g in (getattr(model, "groups", None) or {}).values():
        mem = g.get("members")
        if mem and any(u in info for u in mem):
            out = []
            for u in mem:
                out.extend(info[u]["chords"] if u in info else [u])
            g["members"] = out
    return info
