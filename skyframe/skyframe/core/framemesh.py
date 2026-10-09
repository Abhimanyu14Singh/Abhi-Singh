"""Frame auto-mesh options and output stations (ETABS Assign > Frame >
Frame Auto Mesh Options / Output Stations).

Auto mesh
---------
A frame member (the DRAWN object) may be divided into several ANALYSIS
segments so that it is connected to the objects it meets on its span.
Options, per member ``FrameMember.auto_mesh`` (a dict, ``None`` = use the
model-wide ``BuildingModel.frame_auto_mesh``; ``None`` there = everything
off = the exact pre-existing behaviour)::

    {"at_intermediate_joints": bool,   # joints lying on the span
     "at_intersections": bool,         # frames crossing the span in 3D
     "max_length": float | None,       # max analysis segment length (m)
     "min_segments": int | None}       # min number of equal segments

A member-level dict REPLACES the model default as a whole (missing keys =
off).

* Intermediate joints: every member end, link end, point support / spring
  support and shell corner lying within ``AUTO_MESH_TOL`` (1e-3 m, the
  Check Model default tolerance) of the member's axis, strictly inside the
  span, becomes a segment node (the joint point itself, so the joint's own
  object is connected there).
* Intersections: two members whose axes come within ``AUTO_MESH_TOL`` of
  each other with the closest points strictly inside BOTH spans (a true
  crossing, not a T) are divided at the midpoint of the two closest points
  -- BOTH members are divided as soon as EITHER has ``at_intersections``
  on, so the crossing is always connected.  A T-junction (one member's END
  on the other's span) is an intermediate joint of the spanned member.
* ``min_segments`` adds the ``min_segments - 1`` equal-division points of
  the whole member; ``max_length`` then subdivides every piece between
  consecutive (joint / intersection / equal) cuts into ``ceil(len /
  max_length)`` equal parts.

Axial-only (tension/compression) members are 2-node Truss elements and are
NEVER auto-meshed (neither as the meshed member nor as an intersection
partner; a ``UserWarning`` is emitted).

All auto-mesh points are computed from the model geometry BEFORE the shell
mesher runs and enter the global point pool first, so polygon shells
(:mod:`skyframe.core.polymesh` conformity pass) insert them into their
element edges; on 4-corner structured shells a point that is not already a
mesh node is a hanging node (tied only with ``edge_constraints``).
Shell-node and Winkler cuts are merged as before
(:func:`skyframe.core.mesh._split_member`).

Output stations
---------------
``FrameMember.output_stations`` -- ``None`` (default: the fixed 11
equally spaced stations, bit-identical) or ``{"max_spacing": s}`` (equal
spacing <= s) or ``{"min_number": n}`` (n >= 2 equally spaced stations).
When set, the station list also contains every analysis segment end and
every concentrated load point (``MemberLoad`` kinds "point" / "moment"
of ANY pattern on that member), so the list is the same for every case and
combinations superpose station by station.
"""

from __future__ import annotations

import math
import warnings
from typing import Dict, List, Optional, Tuple

Vec3 = Tuple[float, float, float]

AUTO_MESH_TOL = 1e-3            # m (= checks.DEFAULT_TOLERANCE)
DEFAULT_N_STATIONS = 11
AUTO_MESH_KEYS = ("at_intermediate_joints", "at_intersections",
                  "max_length", "min_segments")
_END_TOL = 1e-6                 # m, cut too close to a member end: dropped
_EPS_STATION = 1e-9


# --------------------------------------------------------------------------- #
# normalisation / validation / JSON
# --------------------------------------------------------------------------- #
def normalize_auto_mesh(d) -> Optional[dict]:
    """Canonical auto-mesh dict (all four keys) or ``None``.  Raises
    ``ValueError`` on bad input."""
    if d is None:
        return None
    if not isinstance(d, dict):
        raise ValueError("auto_mesh must be an object or null")
    bad = set(d) - set(AUTO_MESH_KEYS)
    if bad:
        raise ValueError(f"auto_mesh: unknown key(s) {sorted(bad)}")
    out = {"at_intermediate_joints": bool(d.get("at_intermediate_joints",
                                                False)),
           "at_intersections": bool(d.get("at_intersections", False)),
           "max_length": None, "min_segments": None}
    ml = d.get("max_length")
    if ml is not None:
        if isinstance(ml, bool):
            raise ValueError("auto_mesh.max_length must be a number")
        ml = float(ml)
        if not (math.isfinite(ml) and ml > 0.0):
            raise ValueError("auto_mesh.max_length must be a finite value > 0")
        out["max_length"] = ml
    ns = d.get("min_segments")
    if ns is not None:
        if isinstance(ns, bool) or float(ns) != int(ns):
            raise ValueError("auto_mesh.min_segments must be an integer")
        ns = int(ns)
        if ns < 1:
            raise ValueError("auto_mesh.min_segments must be >= 1")
        out["min_segments"] = ns
    return out


def normalize_output_stations(d) -> Optional[dict]:
    """``{"max_spacing": s}`` | ``{"min_number": n}`` | ``None``."""
    if d is None:
        return None
    if not isinstance(d, dict) or len(d) != 1:
        raise ValueError('output_stations must be {"max_spacing": s} or '
                         '{"min_number": n}')
    (k, v), = d.items()
    if isinstance(v, bool):
        raise ValueError(f"output_stations.{k} must be a number")
    if k == "max_spacing":
        v = float(v)
        if not (math.isfinite(v) and v > 0.0):
            raise ValueError("output_stations.max_spacing must be a finite "
                             "value > 0")
        return {"max_spacing": v}
    if k == "min_number":
        if float(v) != int(v):
            raise ValueError("output_stations.min_number must be an integer")
        v = int(v)
        if v < 2:
            raise ValueError("output_stations.min_number must be >= 2")
        return {"min_number": v}
    raise ValueError(f"output_stations: unknown key {k!r}")


def member_to_dict(m) -> dict:
    """Member JSON fields -- emitted only when set (old files unchanged)."""
    out = {}
    am = getattr(m, "auto_mesh", None)
    if am is not None:
        out["auto_mesh"] = dict(am)
    os_ = getattr(m, "output_stations", None)
    if os_ is not None:
        out["output_stations"] = dict(os_)
    return out


def member_from_dict(m, md: dict) -> None:
    m.auto_mesh = normalize_auto_mesh(md.get("auto_mesh"))
    m.output_stations = normalize_output_stations(md.get("output_stations"))


def model_to_dict(model) -> dict:
    fam = getattr(model, "frame_auto_mesh", None)
    return {} if fam is None else {"frame_auto_mesh": dict(fam)}


def model_from_dict(model, d: dict) -> None:
    model.frame_auto_mesh = normalize_auto_mesh(d.get("frame_auto_mesh"))


def validate_model(model) -> None:
    """Re-normalise (raises ``ValueError`` on bad values) in place."""
    model.frame_auto_mesh = normalize_auto_mesh(
        getattr(model, "frame_auto_mesh", None))
    for m in model.members:
        try:
            m.auto_mesh = normalize_auto_mesh(getattr(m, "auto_mesh", None))
            m.output_stations = normalize_output_stations(
                getattr(m, "output_stations", None))
        except ValueError as exc:
            raise ValueError(f"Member {m.uid}: {exc}") from None


# --------------------------------------------------------------------------- #
# effective options
# --------------------------------------------------------------------------- #
def effective_auto_mesh(model, m) -> Optional[dict]:
    """The member's effective options (member dict replaces the model
    default), or ``None`` when nothing is on."""
    am = getattr(m, "auto_mesh", None)
    if am is None:
        am = getattr(model, "frame_auto_mesh", None)
    if am is None:
        return None
    am = normalize_auto_mesh(am)
    if not (am["at_intermediate_joints"] or am["at_intersections"]
            or am["max_length"] is not None
            or (am["min_segments"] or 1) > 1):
        return None
    return am


def any_auto_mesh(model) -> bool:
    if getattr(model, "frame_auto_mesh", None) is not None:
        return any(effective_auto_mesh(model, m) is not None
                   for m in model.members)
    return any(getattr(m, "auto_mesh", None) is not None
               and effective_auto_mesh(model, m) is not None
               for m in model.members)


def _meshable(m) -> bool:
    return getattr(m, "axial_limit", "both") == "both"


def connects_intersection(model, a, b) -> bool:
    """True when a crossing of members ``a`` and ``b`` is connected by the
    auto mesh (either has ``at_intersections``; both meshable)."""
    if not (_meshable(a) and _meshable(b)):
        return False
    for m in (a, b):
        am = effective_auto_mesh(model, m)
        if am is not None and am["at_intersections"]:
            return True
    return False


def connects_joints_on_span(model, m) -> bool:
    am = effective_auto_mesh(model, m)
    return bool(am is not None and am["at_intermediate_joints"]
                and _meshable(m))


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _seg_closest(p1, q1, p2, q2):
    """Closest points of segments p1q1 / p2q2 (same algorithm as Check
    Model).  Returns (s, t, c1, c2), s/t fractions in [0, 1]."""
    d1, d2, r = _sub(q1, p1), _sub(q2, p2), _sub(p1, p2)
    a, e, f = _dot(d1, d1), _dot(d2, d2), _dot(d2, r)
    c, b = _dot(d1, r), _dot(d1, d2)
    denom = a * e - b * b

    def clamp(v):
        return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)

    s = clamp((b * f - c * e) / denom) if denom > 1e-14 * a * e else 0.0
    t = (b * s + f) / e
    if t < 0.0:
        t, s = 0.0, clamp(-c / a)
    elif t > 1.0:
        t, s = 1.0, clamp((b - c) / a)
    c1 = tuple(p1[k] + s * d1[k] for k in range(3))
    c2 = tuple(p2[k] + t * d2[k] for k in range(3))
    return s, t, c1, c2


def _joint_points(model) -> List[Vec3]:
    pts: List[Vec3] = []
    for m in model.members:
        pts.append(tuple(map(float, m.pi)))
        pts.append(tuple(map(float, m.pj)))
    for lk in getattr(model, "links", []):
        pts.append(tuple(map(float, lk.pi)))
        pts.append(tuple(map(float, lk.pj)))
    for s in getattr(model, "supports", []):
        pts.append(tuple(map(float, s.point)))
    for s in getattr(model, "spring_supports", []):
        pts.append(tuple(map(float, s.point)))
    for r in getattr(model, "shells", []):
        for c in r.corners:
            pts.append(tuple(map(float, c)))
    return list(dict.fromkeys(pts))


def auto_mesh_points(model) -> Dict[str, List[Tuple[float, Vec3]]]:
    """Per member uid: the sorted auto-mesh cut list ``[(t, point), ...]``
    (``t`` = distance from end i, interior only).  Empty dict when no
    member has auto mesh on (the default)."""
    eff = {m.uid: effective_auto_mesh(model, m) for m in model.members}
    if all(v is None for v in eff.values()):
        return {}
    tol = AUTO_MESH_TOL
    cuts: Dict[str, List[Tuple[float, Vec3]]] = {}
    warned = set()

    def add(m, t: float, p: Vec3) -> None:
        if not _meshable(m):
            if m.uid not in warned:
                warned.add(m.uid)
                warnings.warn(f"frame auto mesh: axial-only member {m.uid!r} "
                              "is never divided (Truss element)", UserWarning)
            return
        L = m.length
        if t <= _END_TOL or t >= L - _END_TOL:
            return
        cuts.setdefault(m.uid, []).append((t, p))

    members = list(model.members)
    # --- intermediate joints -------------------------------------------- #
    joints = None
    for m in members:
        am = eff[m.uid]
        if am is None or not am["at_intermediate_joints"]:
            continue
        if joints is None:
            joints = _joint_points(model)
        pi, pj = tuple(map(float, m.pi)), tuple(map(float, m.pj))
        L = m.length
        u = tuple(d / L for d in _sub(pj, pi))
        lo = [min(pi[k], pj[k]) - tol for k in range(3)]
        hi = [max(pi[k], pj[k]) + tol for k in range(3)]
        for p in joints:
            if not all(lo[k] <= p[k] <= hi[k] for k in range(3)):
                continue
            t = _dot(_sub(p, pi), u)
            if t <= tol or t >= L - tol:
                continue
            foot = tuple(pi[k] + t * u[k] for k in range(3))
            if math.dist(p, foot) <= tol:
                add(m, t, p)
    # --- intersections --------------------------------------------------- #
    inter = [m for m in members
             if eff[m.uid] is not None and eff[m.uid]["at_intersections"]]
    if inter:
        bbox = {m.uid: ([min(m.pi[k], m.pj[k]) - tol for k in range(3)],
                        [max(m.pi[k], m.pj[k]) + tol for k in range(3)])
                for m in members}
        done = set()
        for a in inter:
            la, ha = bbox[a.uid]
            for b in members:
                if b is a:
                    continue
                key = (a.uid, b.uid) if a.uid < b.uid else (b.uid, a.uid)
                if key in done:
                    continue
                lb, hb = bbox[b.uid]
                if any(la[k] > hb[k] or lb[k] > ha[k] for k in range(3)):
                    continue
                done.add(key)
                pa, qa = tuple(map(float, a.pi)), tuple(map(float, a.pj))
                pb, qb = tuple(map(float, b.pi)), tuple(map(float, b.pj))
                La, Lb = a.length, b.length
                da, db = _sub(qa, pa), _sub(qb, pb)
                cr = (da[1] * db[2] - da[2] * db[1],
                      da[2] * db[0] - da[0] * db[2],
                      da[0] * db[1] - da[1] * db[0])
                if math.sqrt(_dot(cr, cr)) / (La * Lb) < 1e-6:
                    continue                      # parallel: no crossing
                s, t, c1, c2 = _seg_closest(pa, qa, pb, qb)
                if math.dist(c1, c2) > tol:
                    continue
                if (s * La <= tol or (1.0 - s) * La <= tol
                        or t * Lb <= tol or (1.0 - t) * Lb <= tol):
                    continue                      # T / shared joint
                if not (_meshable(a) and _meshable(b)):
                    for m in (a, b):
                        if not _meshable(m) and m.uid not in warned:
                            warned.add(m.uid)
                            warnings.warn(
                                f"frame auto mesh: axial-only member "
                                f"{m.uid!r} is never divided (Truss "
                                "element); crossing left unconnected",
                                UserWarning)
                    continue
                P = tuple(0.5 * (c1[k] + c2[k]) for k in range(3))
                add(a, s * La, P)
                add(b, t * Lb, P)
    # --- equal divisions / max length ------------------------------------ #
    for m in members:
        am = eff[m.uid]
        if am is None or not _meshable(m):
            continue
        ns = am["min_segments"] or 1
        ml = am["max_length"]
        if ns <= 1 and ml is None:
            continue
        L = m.length
        pi, pj = tuple(map(float, m.pi)), tuple(map(float, m.pj))

        def at(t: float) -> Vec3:
            return tuple(pi[k] + (pj[k] - pi[k]) * t / L for k in range(3))

        hard = sorted(cuts.get(m.uid, []), key=lambda c: c[0])
        for i in range(1, ns):
            hard.append((i * L / ns, at(i * L / ns)))
        hard.sort(key=lambda c: c[0])
        merged: List[Tuple[float, Vec3]] = []
        for t, p in hard:
            if merged and t - merged[-1][0] <= _END_TOL:
                continue
            merged.append((t, p))
        if ml is not None:
            ts = [0.0] + [t for t, _ in merged] + [L]
            for t0, t1 in zip(ts[:-1], ts[1:]):
                n = int(math.ceil((t1 - t0) / ml - 1e-9))
                for i in range(1, n):
                    t = t0 + i * (t1 - t0) / n
                    merged.append((t, at(t)))
        if merged:
            cuts[m.uid] = merged
    out: Dict[str, List[Tuple[float, Vec3]]] = {}
    for uid, lst in cuts.items():
        lst = sorted(lst, key=lambda c: c[0])
        clean: List[Tuple[float, Vec3]] = []
        for t, p in lst:
            if clean and t - clean[-1][0] <= _END_TOL:
                continue
            clean.append((t, p))
        if clean:
            out[uid] = clean
    return out


# --------------------------------------------------------------------------- #
# output stations
# --------------------------------------------------------------------------- #
def _default_xs(L: float) -> List[float]:
    n = DEFAULT_N_STATIONS
    return [k * L / (n - 1) for k in range(n)]


def concentrated_load_xs(model, m) -> List[float]:
    """Distances of every concentrated force / moment on the member (any
    pattern)."""
    L = m.length
    xs = []
    for pat in model.patterns.values():
        for ml in getattr(pat, "member_loads", []):
            if ml.member_uid == m.uid and ml.kind in ("point", "moment"):
                xs.append(min(max(float(ml.a), 0.0), 1.0) * L)
    return xs


def station_xs(model, m, segs=None) -> List[float]:
    """Output station distances (m from end i) of one member.

    ``output_stations is None`` -> the fixed 11 equally spaced stations
    (exact pre-existing list).  Otherwise the equal stations of the option
    plus every segment end and concentrated-load point, sorted, duplicates
    (within 1e-9 x L) removed."""
    L = m.length
    os_ = getattr(m, "output_stations", None)
    if os_ is None:
        return _default_xs(L)
    if "max_spacing" in os_:
        n = max(1, int(math.ceil(L / float(os_["max_spacing"]) - 1e-9)))
    else:
        n = max(1, int(os_["min_number"]) - 1)
    xs = [k * L / n for k in range(n + 1)]
    for s in segs or ():
        xs.append(s.x0)
        xs.append(s.x0 + s.length)
    xs.extend(concentrated_load_xs(model, m))
    xs = sorted(min(max(x, 0.0), L) for x in xs)
    tol = _EPS_STATION * max(1.0, L)
    out: List[float] = []
    for x in xs:
        if out and x - out[-1] <= tol:
            continue
        out.append(x)
    out[0] = 0.0
    if L - out[-1] <= tol:
        out[-1] = L
    return out


def integrate_stations(xs: List[float], f: List[float]) -> float:
    """Quadrature of station values for arbitrary (sorted) stations:
    composite Simpson on consecutive interval PAIRS (the non-uniform
    3-point rule, exact for quadratics over each pair); a trailing odd
    interval integrates the quadratic through the last three stations."""
    n = len(xs) - 1
    total = 0.0
    i = 0
    while i + 2 <= n:
        h0, h1 = xs[i + 1] - xs[i], xs[i + 2] - xs[i + 1]
        if h0 <= 0.0 or h1 <= 0.0:
            total += 0.5 * (f[i] + f[i + 1]) * h0 \
                + 0.5 * (f[i + 1] + f[i + 2]) * h1
        else:
            H = h0 + h1
            total += H / 6.0 * ((2.0 - h1 / h0) * f[i]
                                + H * H / (h0 * h1) * f[i + 1]
                                + (2.0 - h0 / h1) * f[i + 2])
        i += 2
    if i < n:
        if n >= 2 and xs[i] > xs[i - 1] and xs[i + 1] > xs[i]:
            # last interval: integral of the quadratic through the last
            # three stations (exact for quadratics, like the pairs)
            x0, x1, x2 = xs[i - 1], xs[i], xs[i + 1]
            f0, f1, f2 = f[i - 1], f[i], f[i + 1]
            h0, h1 = x1 - x0, x2 - x1
            # q(s) = f1 + b s + c s^2, s = x - x1
            b = ((f2 - f1) * h0 * h0 + (f1 - f0) * h1 * h1) \
                / (h0 * h1 * (h0 + h1))
            c = ((f2 - f1) * h0 - (f1 - f0) * h1) / (h0 * h1 * (h0 + h1))
            total += f1 * h1 + b * h1 * h1 / 2.0 + c * h1 ** 3 / 3.0
        else:
            total += 0.5 * (f[i] + f[i + 1]) * (xs[i + 1] - xs[i])
    return total


def value_at(xs: List[float], vals: List[float], xq: float) -> float:
    """Station value at ``xq``: the station itself when one lies within
    1e-9 x L, else linear interpolation."""
    L = xs[-1] if xs else 0.0
    tol = _EPS_STATION * max(1.0, abs(L))
    for x, v in zip(xs, vals):
        if abs(x - xq) <= tol:
            return v
    if xq <= xs[0]:
        return vals[0]
    for i in range(len(xs) - 1):
        if xs[i] <= xq <= xs[i + 1]:
            dx = xs[i + 1] - xs[i]
            return vals[i] + (xq - xs[i]) / dx * (vals[i + 1] - vals[i])
    return vals[-1]
