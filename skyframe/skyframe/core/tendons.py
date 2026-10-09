"""Post-tensioning tendons modelled as EQUIVALENT LOADS + the hyperstatic
(secondary) case — ETABS "model tendons as loads" (analysis only).

Tendon dict (``BuildingModel.tendons``, normalised by
:func:`normalize_tendon`)::

    {"uid": "T1",
     "points": [[x, y, z], ...],          # >= 2 profile points (m)
     "sags": [s_1, ...],                   # one per segment (m, default 0)
     "drape_dir": [0, 0, -1],              # sag direction (default down)
     "material": "A416Gr270",              # model material or default lib
     "area": 0.00099,                      # tendon area (m^2)
     "jacking_stress": 1.395e6,            # kPa
     "jacking_end": "start"|"end"|"both",
     "losses": {"friction_mu": 0.0, "wobble_k": 0.0,      # -, 1/m
                "anchor_set": 0.0,                         # m (draw-in)
                "long_term_fraction": 0.0},                # 0..1
     "host": ["B1", ...],                  # frame uids OR shell-region uids
     "pattern": "PT",                      # load pattern receiving the loads
     "n_sub": 16}                          # sub-pieces per segment

Input conveniences (all normalised into ``points`` + ``sags``):
``"profile": {"start", "end", "sag"}`` (one parabola) or a list of such
contiguous dicts (``"segments"`` is an alias); ``"host"`` may be a single
uid string.

Geometry.  Segment k runs from ``A = points[k]`` to ``B = points[k+1]``
(chord length ``Lc = |B - A|``) and is the parabola
``p(u) = A + u (B - A) + 4 s u (1 - u) d``, u in [0, 1], where ``s`` is the
segment sag and ``d`` the unit component of ``drape_dir`` perpendicular to
the chord (``s = 0`` -> straight segment; a polyline is a list of straight
segments).

Force convention (small-slope theory, the ETABS/textbook load-balancing
basis).  The tendon force vector is ``T(u) = P * p'(u) / Lc``: its component
along the chord equals the effective force ``P``.  Hence a parabolic
segment carrying constant ``P`` exerts the classic uniform curvature load
``w = 8 P s / Lc^2`` (per unit chord length) on the concrete, opposite to
the sag; straight segments carry the exact tendon force.

Losses (from the jacking end, along the tendon length ``x``):
  * friction + wobble ``P_f(x) = P0 exp(-(mu alpha(x) + k x))`` with
    ``alpha`` the cumulative angle change of the tangent (exact for the
    parabola + kinks between segments);
  * anchor set ``delta``: ``P = min(P_f, 2 P* - P_f)`` with ``P*`` solved
    from ``2 int max(P_f - P*, 0) dx = delta Ep Ap`` (the classic mirrored
    friction diagram; the set length is where ``P_f = P*``, or the whole
    tendon when ``P* < P_f(end)``);
  * ``jacking_end == "both"``: the larger of the two one-end profiles;
  * long-term: ``P_eff = (1 - long_term_fraction) * P``.

Equivalent loads.  The tendon is cut into sub-pieces (``n_sub`` per segment,
also split where the host frame member changes).  Each sub-piece carries the
constant force P of its mid-point; the concrete receives
  * point forces ``T_right - T_left`` at every sub-piece end (anchors:
    ``+T`` at the start, ``-T`` at the end; kinks; friction steps),
  * the uniform curvature load ``-(8 P s / Lc^2) d`` per unit chord length
    on each parabolic sub-piece.
This set is EXACTLY self-equilibrated (zero net force and moment).  Frame
hosts receive point / partial-UDL member loads (global components) at the
projections of the tendon points on the member axis plus the eccentricity
couples ``(p - c) x F``; shell hosts receive nodal loads at the nearest
host-region mesh node (with the transfer couple).

Hyperstatic case (``BuildingModel.hyperstatic_cases``,
``{name: {"case": <static case>}}``): secondary effects = total PT effects
of the referenced static case - primary effects.  Primary internal forces
of a host member at a station are those of the concrete section under the
tendon force alone: ``F1 = -T`` acting at the tendon point ``p``, i.e.
``N1 = -T.x``, ``V2 = -T.y``, ``V3 = -T.z``, moments
``(p - c) x (-T)`` in the member local axes (the engine's station sign
convention: force of the j-side part on the i-side part).  Primary
reactions are zero (the load set is self-equilibrated) so the secondary
reactions are the PT case reactions.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]

JACKING_ENDS = ("start", "end", "both")
LOSS_KEYS = ("friction_mu", "wobble_k", "anchor_set", "long_term_fraction")
DEFAULT_N_SUB = 16
_EPS = 1e-12


# --------------------------------------------------------------------------- #
# small vector helpers
# --------------------------------------------------------------------------- #
def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _angle(a, b) -> float:
    na, nb = _norm(a), _norm(b)
    if na < _EPS or nb < _EPS:
        return 0.0
    return math.atan2(_norm(_cross(a, b)), _dot(a, b))


# --------------------------------------------------------------------------- #
# normalisation / validation
# --------------------------------------------------------------------------- #
def _vec(v, what: str) -> Vec3:
    if (not isinstance(v, (list, tuple)) or len(v) != 3
            or not all(isinstance(c, (int, float)) and not isinstance(c, bool)
                       and math.isfinite(c) for c in v)):
        raise ValueError(f"{what} must be three finite numbers")
    return (float(v[0]), float(v[1]), float(v[2]))


def _num(v, what: str, lo: Optional[float] = None, lo_open=False,
         hi: Optional[float] = None) -> float:
    if not (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v)):
        raise ValueError(f"{what} must be a finite number (got {v!r})")
    v = float(v)
    if lo is not None and (v < lo or (lo_open and v <= lo)):
        raise ValueError(f"{what} must be {'>' if lo_open else '>='} {lo} "
                         f"(got {v!r})")
    if hi is not None and v > hi:
        raise ValueError(f"{what} must be <= {hi} (got {v!r})")
    return v


def normalize_tendon(d: dict) -> dict:
    """Canonical tendon dict (see module docstring); raises ValueError."""
    if not isinstance(d, dict):
        raise ValueError("tendon must be an object")
    uid = d.get("uid")
    if not isinstance(uid, str) or not uid:
        raise ValueError("tendon: uid must be a non-empty string")
    tag = f"Tendon {uid!r}"
    prof = d.get("profile", d.get("segments"))
    if prof is not None and d.get("points") is not None:
        raise ValueError(f"{tag}: give either points or profile, not both")
    if prof is not None:
        if isinstance(prof, dict):
            prof = [prof]
        if not isinstance(prof, list) or not prof:
            raise ValueError(f"{tag}: profile must be an object or a "
                             "non-empty list of {start, end, sag}")
        pts: List[Vec3] = []
        sags: List[float] = []
        for k, sg in enumerate(prof):
            if not isinstance(sg, dict):
                raise ValueError(f"{tag}: profile[{k}] must be an object")
            a = _vec(sg.get("start"), f"{tag}: profile[{k}].start")
            b = _vec(sg.get("end"), f"{tag}: profile[{k}].end")
            if pts and _norm(_sub(pts[-1], a)) > 1e-9:
                raise ValueError(f"{tag}: profile[{k}].start must equal the "
                                 "previous segment end (contiguous profile)")
            if not pts:
                pts.append(a)
            pts.append(b)
            sags.append(_num(sg.get("sag", sg.get("drape", 0.0)),
                             f"{tag}: profile[{k}].sag"))
    else:
        raw = d.get("points")
        if not isinstance(raw, list) or len(raw) < 2:
            raise ValueError(f"{tag}: points must list >= 2 [x, y, z] points")
        pts = [_vec(p, f"{tag}: points[{k}]") for k, p in enumerate(raw)]
        sg = d.get("sags")
        if sg is None:
            sags = [0.0] * (len(pts) - 1)
        else:
            if not isinstance(sg, list) or len(sg) != len(pts) - 1:
                raise ValueError(f"{tag}: sags must list one value per "
                                 f"segment ({len(pts) - 1})")
            sags = [_num(s, f"{tag}: sags[{k}]") for k, s in enumerate(sg)]
    for k in range(len(pts) - 1):
        if _norm(_sub(pts[k + 1], pts[k])) < 1e-9:
            raise ValueError(f"{tag}: segment {k} has zero length")
    dd = _vec(d.get("drape_dir", [0.0, 0.0, -1.0]), f"{tag}: drape_dir")
    if _norm(dd) < _EPS:
        raise ValueError(f"{tag}: drape_dir must be non-zero")
    for k in range(len(pts) - 1):
        if sags[k] != 0.0:
            c = _sub(pts[k + 1], pts[k])
            c = _mul(c, 1.0 / _norm(c))
            perp = _sub(dd, _mul(c, _dot(dd, c)))
            if _norm(perp) < 1e-6 * _norm(dd):
                raise ValueError(f"{tag}: drape_dir is parallel to the chord "
                                 f"of curved segment {k}")
    mat = d.get("material", "A416Gr270")
    if not isinstance(mat, str) or not mat:
        raise ValueError(f"{tag}: material must be a material name")
    area = _num(d.get("area"), f"{tag}: area", 0.0, lo_open=True)
    fj = _num(d.get("jacking_stress"), f"{tag}: jacking_stress", 0.0,
              lo_open=True)
    je = d.get("jacking_end", "start")
    if je not in JACKING_ENDS:
        raise ValueError(f"{tag}: jacking_end must be one of {JACKING_ENDS}")
    lo = d.get("losses") or {}
    if not isinstance(lo, dict):
        raise ValueError(f"{tag}: losses must be an object")
    bad = set(lo) - set(LOSS_KEYS)
    if bad:
        raise ValueError(f"{tag}: unknown losses keys {sorted(bad)} "
                         f"(allowed: {list(LOSS_KEYS)})")
    losses = {k: _num(lo.get(k, 0.0), f"{tag}: losses.{k}", 0.0)
              for k in LOSS_KEYS}
    if losses["long_term_fraction"] >= 1.0:
        raise ValueError(f"{tag}: losses.long_term_fraction must be < 1")
    host = d.get("host")
    if isinstance(host, str):
        host = [host]
    if (not isinstance(host, list) or not host
            or not all(isinstance(h, str) and h for h in host)):
        raise ValueError(f"{tag}: host must be a member/shell uid or a "
                         "non-empty list of uids")
    if len(set(host)) != len(host):
        raise ValueError(f"{tag}: duplicate host uids")
    pat = d.get("pattern")
    if not isinstance(pat, str) or not pat:
        raise ValueError(f"{tag}: pattern must be a load pattern name")
    n_sub = d.get("n_sub", DEFAULT_N_SUB)
    if (not isinstance(n_sub, int) or isinstance(n_sub, bool)
            or not 1 <= n_sub <= 200):
        raise ValueError(f"{tag}: n_sub must be an integer in 1..200")
    return {"uid": uid, "points": [list(p) for p in pts],
            "sags": list(sags), "drape_dir": list(dd), "material": mat,
            "area": area, "jacking_stress": fj, "jacking_end": je,
            "losses": losses, "host": list(host), "pattern": pat,
            "n_sub": n_sub}


def tendon_material(model, td: dict):
    """The tendon Material: the model's when defined, else the ETABS
    default library's (``A416Gr270`` ...)."""
    if td["material"] in model.materials:
        return model.materials[td["material"]]
    from skyframe.core.model import library_material
    return library_material(td["material"])


def host_kind(model, td: dict) -> str:
    """"frame" | "shell" (validated)."""
    mem = {m.uid for m in model.members}
    shl = {r.uid for r in model.shells}
    hs = td["host"]
    if all(h in mem for h in hs):
        return "frame"
    if all(h in shl for h in hs):
        return "shell"
    raise ValueError(f"Tendon {td['uid']!r}: host must be all frame-member "
                     f"uids or all shell-region uids (got {hs})")


def validate_tendons(model) -> None:
    """Cross-reference validation of ``model.tendons`` and
    ``model.hyperstatic_cases`` (no-op when both are empty)."""
    tds = getattr(model, "tendons", None) or []
    hcs = getattr(model, "hyperstatic_cases", None) or {}
    if not tds and not hcs:
        return
    if not isinstance(tds, list):
        raise ValueError("tendons must be a list")
    seen = set()
    for k, raw in enumerate(tds):
        td = normalize_tendon(raw)
        tds[k] = td
        if td["uid"] in seen:
            raise ValueError(f"Duplicate tendon uid {td['uid']!r}")
        seen.add(td["uid"])
        if td["pattern"] not in model.patterns:
            raise ValueError(f"Tendon {td['uid']!r}: unknown load pattern "
                             f"{td['pattern']!r}")
        try:
            mat = tendon_material(model, td)
        except KeyError:
            raise ValueError(f"Tendon {td['uid']!r}: unknown material "
                             f"{td['material']!r}") from None
        if td["losses"]["anchor_set"] > 0.0 and not mat.E > 0.0:
            raise ValueError(f"Tendon {td['uid']!r}: material E must be > 0")
        kind = host_kind(model, td)
        if kind == "frame":
            for m in model.members:
                if (m.uid in td["host"]
                        and getattr(m, "axial_limit", "both") != "both"):
                    raise ValueError(f"Tendon {td['uid']!r}: host member "
                                     f"{m.uid!r} is axial-only")
            sub_pieces(model, td)          # raises when off the host
    if not isinstance(hcs, dict):
        raise ValueError("hyperstatic_cases must be an object")
    tendon_pats = {t["pattern"] for t in tds}
    kinds = model.case_kinds()
    for name, hc in hcs.items():
        if not isinstance(hc, dict) or not isinstance(hc.get("case"), str):
            raise ValueError(f"Hyperstatic case {name!r}: needs "
                             "{\"case\": <static case name>}")
        if set(hc) - {"case"}:
            raise ValueError(f"Hyperstatic case {name!r}: unknown keys "
                             f"{sorted(set(hc) - {'case'})}")
        if kinds.get(name) != "hyperstatic":
            raise ValueError(f"Hyperstatic case {name!r}: name already used "
                             "by another case")
        ref = hc["case"]
        if ref not in model.cases:
            raise ValueError(f"Hyperstatic case {name!r}: unknown static "
                             f"case {ref!r}")
        if not any(p in tendon_pats for p in model.cases[ref].patterns):
            raise ValueError(f"Hyperstatic case {name!r}: static case "
                             f"{ref!r} applies no tendon load pattern")


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
class _Seg:
    """One parabolic (or straight) tendon segment."""

    def __init__(self, A: Vec3, B: Vec3, s: float, drape: Vec3):
        self.A, self.B, self.s = A, B, s
        self.ch = _sub(B, A)
        self.Lc = _norm(self.ch)
        c = _mul(self.ch, 1.0 / self.Lc)
        if s != 0.0:
            perp = _sub(drape, _mul(c, _dot(drape, c)))
            self.d = _mul(perp, 1.0 / _norm(perp))
        else:
            self.d = (0.0, 0.0, 0.0)

    def p(self, u: float) -> Vec3:
        return _add(_add(self.A, _mul(self.ch, u)),
                    _mul(self.d, 4.0 * self.s * u * (1.0 - u)))

    def dp(self, u: float) -> Vec3:
        return _add(self.ch, _mul(self.d, 4.0 * self.s * (1.0 - 2.0 * u)))

    def T(self, u: float, P: float) -> Vec3:
        """Tendon force vector (chord component == P)."""
        return _mul(self.dp(u), P / self.Lc)

    def arc(self, u1: float, u2: float, n: int = 32) -> float:
        """Arc length between u1 and u2 (Simpson, n even)."""
        if self.s == 0.0:
            return self.Lc * (u2 - u1)
        h = (u2 - u1) / n
        tot = _norm(self.dp(u1)) + _norm(self.dp(u2))
        for i in range(1, n):
            tot += (4 if i % 2 else 2) * _norm(self.dp(u1 + i * h))
        return tot * h / 3.0

    def mean_p(self, u1: float, u2: float) -> Vec3:
        """Mean of p(u) over [u1, u2] (exact)."""
        um = 0.5 * (u1 + u2)
        q = um - (u1 * u1 + u1 * u2 + u2 * u2) / 3.0      # mean of u(1-u)
        return _add(_add(self.A, _mul(self.ch, um)),
                    _mul(self.d, 4.0 * self.s * q))


def segments(td: dict) -> List[_Seg]:
    pts = [tuple(p) for p in td["points"]]
    dd = tuple(td["drape_dir"])
    dn = _norm(dd)
    dd = _mul(dd, 1.0 / dn)
    return [_Seg(pts[k], pts[k + 1], td["sags"][k], dd)
            for k in range(len(pts) - 1)]


def _member_axis(m) -> Tuple[Vec3, Vec3, float]:
    pi = tuple(m.pi)
    d = _sub(tuple(m.pj), pi)
    L = _norm(d)
    return pi, _mul(d, 1.0 / L), L


def _project(axis, p: Vec3) -> Tuple[float, float]:
    """(x along the member axis, perpendicular distance)."""
    pi, mh, L = axis
    r = _sub(p, pi)
    x = _dot(r, mh)
    perp = _sub(r, _mul(mh, x))
    return x, _norm(perp)


def _assign(axes: Dict[str, tuple], p: Vec3, tol: float) -> Optional[str]:
    """Host member whose axis span contains the projection of ``p`` (the
    nearest one when several do); None when none does."""
    best, bd = None, math.inf
    for uid, ax in axes.items():
        x, dist = _project(ax, p)
        if -tol <= x <= ax[2] + tol and dist < bd:
            best, bd = uid, dist
    return best


def sub_pieces(model, td: dict) -> List[dict]:
    """The tendon cut into sub-pieces ``{"seg", "u1", "u2", "host"}``
    (host = frame uid, or None for shell hosts), in tendon order."""
    segs = segments(td)
    n = td["n_sub"]
    frame = host_kind(model, td) == "frame"
    axes = ({m.uid: _member_axis(m) for m in model.members
             if m.uid in td["host"]} if frame else {})
    tol = 1e-6
    out: List[dict] = []
    for k, sg in enumerate(segs):
        us = [i / n for i in range(n + 1)]
        if frame:
            # refine: split where the host member changes along the segment
            fine = 8 * n
            hosts = []
            for i in range(fine + 1):
                u = i / fine
                h = _assign(axes, sg.p(u), tol)
                if h is None:
                    raise ValueError(
                        f"Tendon {td['uid']!r}: point {sg.p(u)} of segment "
                        f"{k} does not project onto any host member "
                        f"{td['host']}")
                hosts.append(h)
            extra = []
            for i in range(fine):
                if hosts[i] != hosts[i + 1]:
                    lo, hi = i / fine, (i + 1) / fine
                    h_lo = hosts[i]
                    # the boundary: where the (sharp, zero-tolerance)
                    # assignment leaves h_lo
                    for _ in range(60):
                        mid = 0.5 * (lo + hi)
                        if _assign(axes, sg.p(mid), 0.0) == h_lo:
                            lo = mid
                        else:
                            hi = mid
                    extra.append(0.5 * (lo + hi))
            us = sorted(set(us) | set(extra))
            # drop near-duplicates
            clean = [us[0]]
            for u in us[1:]:
                if u - clean[-1] > 1e-9:
                    clean.append(u)
                else:
                    clean[-1] = u if u in (0.0, 1.0) else clean[-1]
            us = clean
        for i in range(len(us) - 1):
            u1, u2 = us[i], us[i + 1]
            host = (_assign(axes, sg.p(0.5 * (u1 + u2)), tol)
                    if frame else None)
            out.append({"seg": k, "u1": u1, "u2": u2, "host": host})
    return out


# --------------------------------------------------------------------------- #
# force profile (losses)
# --------------------------------------------------------------------------- #
def _arc_alpha(segs: List[_Seg], pieces: List[dict]):
    """Cumulative arc length / angle change at every sub-piece boundary
    (len(pieces) + 1 values) and at every sub-piece mid-point."""
    s_b = [0.0]
    a_b = [0.0]
    s_m: List[float] = []
    a_m: List[float] = []
    prev_t = None
    for pc in pieces:
        sg = segs[pc["seg"]]
        t1 = sg.dp(pc["u1"])
        a0 = a_b[-1] + (_angle(prev_t, t1) if prev_t is not None else 0.0)
        um = 0.5 * (pc["u1"] + pc["u2"])
        s_m.append(s_b[-1] + sg.arc(pc["u1"], um))
        a_m.append(a0 + _angle(t1, sg.dp(um)))
        s_b.append(s_b[-1] + sg.arc(pc["u1"], pc["u2"]))
        t2 = sg.dp(pc["u2"])
        a_b.append(a0 + _angle(t1, t2))
        prev_t = t2
    return s_b, a_b, s_m, a_m


def _one_end(P0: float, mu: float, k: float, s: List[float],
             a: List[float]) -> List[float]:
    return [P0 * math.exp(-(mu * ai + k * si)) for si, ai in zip(s, a)]


def _anchor_set(xs: List[float], pf: List[float], target: float
                ) -> Tuple[float, float]:
    """Solve ``2 int max(Pf - P*, 0) dx = target`` on the piecewise-linear
    friction curve (xs ascending from the jacking end).  Returns
    (P*, set length)."""
    def area(ps: float) -> float:
        tot = 0.0
        for i in range(len(xs) - 1):
            f1, f2 = pf[i] - ps, pf[i + 1] - ps
            h = xs[i + 1] - xs[i]
            if f1 >= 0.0 and f2 >= 0.0:
                tot += 0.5 * (f1 + f2) * h
            elif f1 > 0.0 or f2 > 0.0:
                pos, neg = (f1, f2) if f1 > 0.0 else (f2, f1)
                tot += 0.5 * pos * h * pos / (pos - neg)
        return 2.0 * tot

    hi = max(pf)
    lo = min(pf)
    span = xs[-1] - xs[0]
    if area(lo) < target:
        # the whole tendon is affected: shift below the end value
        lo = lo - target / (2.0 * span) - abs(hi)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if area(mid) > target:
            lo = mid
        else:
            hi = mid
    ps = 0.5 * (lo + hi)
    lset = span
    for i in range(len(xs) - 1):
        if pf[i] >= ps >= pf[i + 1] and pf[i] != pf[i + 1]:
            lset = xs[i] + (pf[i] - ps) / (pf[i] - pf[i + 1]) * (
                xs[i + 1] - xs[i]) - xs[0]
            break
    return ps, lset


def force_profile(model, td: dict, pieces: Optional[List[dict]] = None
                  ) -> dict:
    """Tendon force along its length.  Returns ``{"P0", "length",
    "s_mid", "alpha_mid", "P_mid" (effective, per sub-piece),
    "s_bound", "alpha_bound", "P_bound" (effective, at sub-piece
    boundaries), "anchor_set_length": {"start", "end"}}``."""
    segs = segments(td)
    if pieces is None:
        pieces = sub_pieces(model, td)
    s_b, a_b, s_m, a_m = _arc_alpha(segs, pieces)
    lo = td["losses"]
    mu, k = lo["friction_mu"], lo["wobble_k"]
    P0 = td["jacking_stress"] * td["area"]
    total_L = s_b[-1]
    total_a = a_b[-1]
    dset = lo["anchor_set"]
    EA = tendon_material(model, td).E * td["area"]

    # dense evaluation grid = boundaries + mid-points (ascending)
    xs = sorted(set(s_b) | set(s_m))
    amap = dict(zip(s_b, a_b))
    amap.update(zip(s_m, a_m))
    alphas = [amap[x] for x in xs]

    def from_end(start: bool) -> Tuple[List[float], float]:
        """Force at every ``xs`` point (ascending order) when jacked from
        the start (or the end)."""
        if start:
            xx, aa = xs, alphas
        else:
            xx = [total_L - x for x in reversed(xs)]
            aa = [total_a - a for a in reversed(alphas)]
        pf = _one_end(P0, mu, k, xx, aa)
        lset = 0.0
        if dset > 0.0:
            ps, lset = _anchor_set(xx, pf, dset * EA)
            pf = [min(p, 2.0 * ps - p) for p in pf]
        return (pf if start else pf[::-1]), lset

    je = td["jacking_end"]
    sets = {"start": 0.0, "end": 0.0}
    if je == "start":
        pv, sets["start"] = from_end(True)
    elif je == "end":
        pv, sets["end"] = from_end(False)
    else:
        v1, sets["start"] = from_end(True)
        v2, sets["end"] = from_end(False)
        pv = [max(a, b) for a, b in zip(v1, v2)]
    ltf = 1.0 - lo["long_term_fraction"]
    vals = dict(zip(xs, pv))
    P_mid = [max(vals[x], 0.0) * ltf for x in s_m]
    P_bnd = [max(vals[x], 0.0) * ltf for x in s_b]
    return {"P0": P0, "length": total_L, "s_mid": s_m, "alpha_mid": a_m,
            "P_mid": P_mid, "s_bound": s_b, "alpha_bound": a_b,
            "P_bound": P_bnd, "anchor_set_length": sets}


# --------------------------------------------------------------------------- #
# equivalent load set
# --------------------------------------------------------------------------- #
def equivalent_loads(model, td: dict) -> dict:
    """Self-equilibrated equivalent load set of one tendon (unit pattern
    scale): ``{"points": [{"point", "force", "host"}],
    "lines": [{"seg", "u1", "u2", "point" (mean), "force" (total),
    "host"}], "pieces", "profile"}``."""
    segs = segments(td)
    pieces = sub_pieces(model, td)
    prof = force_profile(model, td, pieces)
    P = prof["P_mid"]
    pts: List[dict] = []
    lines: List[dict] = []
    n = len(pieces)
    for i, pc in enumerate(pieces):
        sg = segs[pc["seg"]]
        T1 = sg.T(pc["u1"], P[i])
        if i == 0:
            F = T1
        else:
            pp = pieces[i - 1]
            F = _sub(T1, segs[pp["seg"]].T(pp["u2"], P[i - 1]))
        pts.append({"point": sg.p(pc["u1"]), "force": F,
                    "host": pc["host"], "piece": i, "end": "start"})
        if sg.s != 0.0:
            W = _mul(sg.d, -8.0 * P[i] * sg.s / sg.Lc
                     * (pc["u2"] - pc["u1"]))
            lines.append({"seg": pc["seg"], "u1": pc["u1"], "u2": pc["u2"],
                          "point": sg.mean_p(pc["u1"], pc["u2"]),
                          "force": W, "host": pc["host"], "piece": i})
    last = pieces[-1]
    sg = segs[last["seg"]]
    pts.append({"point": sg.p(last["u2"]),
                "force": _mul(sg.T(last["u2"], P[n - 1]), -1.0),
                "host": last["host"], "piece": n - 1, "end": "end"})
    return {"points": pts, "lines": lines, "pieces": pieces,
            "profile": prof}


def resultant(eq: dict, about: Vec3 = (0.0, 0.0, 0.0)) -> Tuple[Vec3, Vec3]:
    """(net force, net moment about ``about``) of an equivalent load set."""
    F = (0.0, 0.0, 0.0)
    M = (0.0, 0.0, 0.0)
    for item in eq["points"] + eq["lines"]:
        F = _add(F, item["force"])
        M = _add(M, _cross(_sub(item["point"], about), item["force"]))
    return F, M


def frame_member_loads(model, td: dict, eq: Optional[dict] = None
                       ) -> List[Tuple[str, str, float, float, float, str]]:
    """The equivalent loads as member loads on the frame hosts:
    ``[(member_uid, kind, w, a, b, direction)]`` with ``kind`` in
    {"point", "udl", "moment"} and global directions (unit pattern
    scale)."""
    if eq is None:
        eq = equivalent_loads(model, td)
    axes = {m.uid: _member_axis(m) for m in model.members
            if m.uid in td["host"]}
    out = []
    dirs = ("global_x", "global_y", "global_z")
    tiny = 1e-13 * max(eq["profile"]["P0"], 1.0)

    def frac(uid, p):
        x, _d = _project(axes[uid], p)
        L = axes[uid][2]
        return min(max(x / L, 0.0), 1.0)

    def emit_moment(uid, a, M):
        for c, dn in zip(M, dirs):
            if abs(c) > tiny:
                out.append((uid, "moment", c, a, a, dn))

    for it in eq["points"]:
        uid = it["host"]
        a = frac(uid, it["point"])
        pi, mh, L = axes[uid]
        c = _add(pi, _mul(mh, a * L))
        for comp, dn in zip(it["force"], dirs):
            if abs(comp) > tiny:
                out.append((uid, "point", comp, a, a, dn))
        emit_moment(uid, a, _cross(_sub(it["point"], c), it["force"]))
    segs = segments(td)
    for it in eq["lines"]:
        uid = it["host"]
        sg = segs[it["seg"]]
        a1 = frac(uid, sg.p(it["u1"]))
        a2 = frac(uid, sg.p(it["u2"]))
        if a2 < a1:
            a1, a2 = a2, a1
        pi, mh, L = axes[uid]
        span = (a2 - a1) * L
        am = 0.5 * (a1 + a2)
        if span < 1e-12:
            # degenerate (projects to a point): concentrated force
            for comp, dn in zip(it["force"], dirs):
                if abs(comp) > tiny:
                    out.append((uid, "point", comp, am, am, dn))
        else:
            for comp, dn in zip(it["force"], dirs):
                if abs(comp) > tiny:
                    out.append((uid, "udl", comp / span, a1, a2, dn))
        c = _add(pi, _mul(mh, am * L))
        emit_moment(uid, am, _cross(_sub(it["point"], c), it["force"]))
    return out


# --------------------------------------------------------------------------- #
# primary (P*e) section forces on a frame host
# --------------------------------------------------------------------------- #
def primary_at(model, td: dict, eq: dict, member, x: float,
               axes_local: Tuple[Vec3, Vec3, Vec3], side: str
               ) -> Optional[Tuple[float, ...]]:
    """Primary section forces (N, V2, V3, T, M2, M3) of ``member`` at
    distance ``x`` from end i due to this tendon (unit scale), or None when
    the tendon does not pass the station.  ``side`` ("right" | "left")
    picks the sub-piece when the station sits exactly on a sub-piece
    boundary (the engine's station convention)."""
    if member.uid not in td["host"]:
        return None
    ax = _member_axis(member)
    segs = segments(td)
    P = eq["profile"]["P_mid"]
    cands = []
    for i, pc in enumerate(eq["pieces"]):
        if pc["host"] != member.uid:
            continue
        sg = segs[pc["seg"]]
        x1 = _project(ax, sg.p(pc["u1"]))[0]
        x2 = _project(ax, sg.p(pc["u2"]))[0]
        lo, hi = min(x1, x2), max(x1, x2)
        if lo - 1e-9 <= x <= hi + 1e-9:
            cands.append((i, lo, hi, x1 <= x2))
    if not cands:
        return None
    if len(cands) > 1:
        # boundary station: right side = the piece extending to larger x
        if side == "right":
            cands.sort(key=lambda c: -c[2])
        else:
            cands.sort(key=lambda c: c[1])
    i, lo, hi, inc = cands[0]
    pc = eq["pieces"][i]
    sg = segs[pc["seg"]]
    ua, ub = pc["u1"], pc["u2"]
    for _ in range(80):                       # projection is monotonic
        um = 0.5 * (ua + ub)
        xm = _project(ax, sg.p(um))[0]
        if (xm < x) == inc:
            ua = um
        else:
            ub = um
    u = 0.5 * (ua + ub)
    p = sg.p(u)
    T = sg.T(u, P[i])
    c = _add(ax[0], _mul(ax[1], x))
    F = _mul(T, -1.0)
    M = _cross(_sub(p, c), F)
    xl, yl, zl = axes_local
    return (_dot(F, xl), _dot(F, yl), _dot(F, zl),
            _dot(M, xl), _dot(M, yl), _dot(M, zl))


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def tendon_report(model, td: dict, eq: Optional[dict] = None) -> dict:
    """JSON-ready per-tendon summary (results ``"tendons"``)."""
    if eq is None:
        eq = equivalent_loads(model, td)
    prof = eq["profile"]
    segs = segments(td)
    F, M = resultant(eq)
    pts = [list(segs[pc["seg"]].p(pc["u1"])) for pc in eq["pieces"]]
    last = eq["pieces"][-1]
    pts.append(list(segs[last["seg"]].p(last["u2"])))
    uplift = []
    for k, sg in enumerate(segs):
        pk = [P for P, pc in zip(prof["P_mid"], eq["pieces"])
              if pc["seg"] == k]
        Pavg = sum(pk) / len(pk)
        uplift.append(8.0 * Pavg * sg.s / sg.Lc ** 2)
    return {
        "pattern": td["pattern"],
        "P0": prof["P0"],
        "length": prof["length"],
        "anchor_set_length": dict(prof["anchor_set_length"]),
        "stations": {"s": list(prof["s_bound"]),
                     "alpha": list(prof["alpha_bound"]),
                     "P": list(prof["P_bound"]),
                     "points": pts},
        "segment_uplift": uplift,
        "equivalent_loads": {
            "points": [{"point": list(it["point"]),
                        "force": list(it["force"])} for it in eq["points"]],
            "lines": [{"segment": it["seg"], "u1": it["u1"], "u2": it["u2"],
                       "force": list(it["force"])} for it in eq["lines"]],
            "net_force": list(F), "net_moment": list(M)},
    }
