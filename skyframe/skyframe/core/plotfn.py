"""Plot functions (ETABS Display > Show Plot Functions) — model side + math.

Two pieces, both pure numpy (no OpenSees):

1. ``TimeHistoryCase.output_requests`` — the optional per-case request of
   full response time series (recorded by :mod:`skyframe.engine.plotfn`)::

       {"joints": [[x, y, z], ...],   # joint disp/vel/acc rel + absolute
        "links":  [uid, ...],         # link basic force / deformation
        "frames": [uid, ...],         # frame end forces (12 local)
        "hinges": true}               # every hinge spring rot / moment

   Absent (``None``) = nothing recorded, results byte-identical to the
   pre-feature engine.  :func:`normalize` canonicalises a request,
   :func:`validate` checks it against the model.

2. Response spectra of a recorded acceleration series (floor response
   spectra) by the EXACT piecewise-linear-excitation recurrence of
   Nigam & Jennings (1969) — :func:`response_spectrum`.

Nigam-Jennings recurrence
-------------------------

For the unit-mass SDOF ``u'' + 2 z w u' + w^2 u = -a(t)`` with ``a``
linear over each step ``[t_i, t_i + h]`` the state advances exactly by

    u_i+1 = A u_i + B v_i + C p_i + D p_i+1
    v_i+1 = A' u_i + B' v_i + C' p_i + D' p_i+1,     p = -a

(Chopra, *Dynamics of Structures*, Table 5.2.1, k = w^2).  The recurrence
is exact for the piecewise-linear interpolation of the samples (the same
interpolation an OpenSees ``Path`` series uses), so a step / ramp input
reproduces its closed-form response to round-off.  Spectral values are
the peaks over the sample instants:

* ``Sd`` = max |u|          (relative displacement, m)
* ``Sv`` = max |v|          (relative velocity, m/s)
* ``Sa`` = max |u'' + a| = max |2 z w v + w^2 u|  (absolute accel, m/s^2)
* ``PSv = w Sd``, ``PSa = w^2 Sd`` (pseudo values)

``T = 0`` is the rigid oscillator: ``Sa = PSa = max |a|``, ``Sd = Sv =
PSv = 0``.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np

REQUEST_KEYS = ("joints", "links", "frames", "hinges")

#: default period grid (s): T = 0 (PGA) + 99 log-spaced points 0.02 .. 5 s
DEFAULT_PERIODS: List[float] = [0.0] + [
    float(v) for v in np.logspace(math.log10(0.02), math.log10(5.0), 99)]


# --------------------------------------------------------------------------- #
# output_requests (model side)
# --------------------------------------------------------------------------- #
def normalize(req) -> Optional[dict]:
    """Canonical output_requests dict (``None`` stays ``None``).

    Only the keys present are kept: ``joints`` -> list of 3-float lists,
    ``links``/``frames`` -> list of str, ``hinges`` -> bool.  Raises
    ValueError on a malformed shape.
    """
    if req is None:
        return None
    if not isinstance(req, dict):
        raise ValueError("output_requests must be an object")
    bad = [k for k in req if k not in REQUEST_KEYS]
    if bad:
        raise ValueError(f"output_requests: unknown key(s) {sorted(bad)}; "
                         f"allowed {list(REQUEST_KEYS)}")
    out: dict = {}
    if "joints" in req:
        pts = req["joints"]
        if not isinstance(pts, (list, tuple)):
            raise ValueError("output_requests.joints must be a list of "
                             "[x, y, z] points")
        jl = []
        for p in pts:
            if (not isinstance(p, (list, tuple)) or len(p) != 3
                    or any(isinstance(c, bool) for c in p)):
                raise ValueError("output_requests.joints entries must be "
                                 "[x, y, z]")
            try:
                xyz = [float(c) for c in p]
            except (TypeError, ValueError):
                raise ValueError("output_requests.joints entries must be "
                                 "numeric [x, y, z]") from None
            if not all(math.isfinite(c) for c in xyz):
                raise ValueError("output_requests.joints coordinates must "
                                 "be finite")
            jl.append(xyz)
        out["joints"] = jl
    for key in ("links", "frames"):
        if key in req:
            v = req[key]
            if not isinstance(v, (list, tuple)) or not all(
                    isinstance(u, str) and u for u in v):
                raise ValueError(f"output_requests.{key} must be a list of "
                                 "uid strings")
            out[key] = [str(u) for u in v]
    if "hinges" in req:
        if not isinstance(req["hinges"], bool):
            raise ValueError("output_requests.hinges must be true/false")
        out["hinges"] = bool(req["hinges"])
    return out


def validate(model, th) -> None:
    """Check a TH case's output_requests against the model (no-op if
    absent).  Re-normalises in place so an API-built dict is canonical."""
    req = getattr(th, "output_requests", None)
    if req is None:
        return
    try:
        th.output_requests = normalize(req)
    except ValueError as exc:
        raise ValueError(f"TH case {th.name}: {exc}") from None
    req = th.output_requests
    link_uids = {lk.uid for lk in model.links}
    for u in req.get("links", []):
        if u not in link_uids:
            raise ValueError(f"TH case {th.name}: output_requests.links "
                             f"references unknown link {u!r}")
    mem_uids = {m.uid for m in model.members}
    for u in req.get("frames", []):
        if u not in mem_uids:
            raise ValueError(f"TH case {th.name}: output_requests.frames "
                             f"references unknown member {u!r}")


# --------------------------------------------------------------------------- #
# ground motion kinematics (exact for piecewise-linear acceleration)
# --------------------------------------------------------------------------- #
def integrate_linear_accel(acc: Sequence[float], dt: float):
    """Velocity / displacement of a piecewise-linear acceleration history
    sampled at ``dt`` (zero initial velocity and displacement), exact:

        v_i+1 = v_i + h (a_i + a_i+1) / 2
        u_i+1 = u_i + h v_i + h^2 (2 a_i + a_i+1) / 6
    """
    a = np.asarray(acc, dtype=float)
    n = len(a)
    v = np.zeros(n)
    u = np.zeros(n)
    for i in range(n - 1):
        v[i + 1] = v[i] + 0.5 * dt * (a[i] + a[i + 1])
        u[i + 1] = u[i] + dt * v[i] + dt * dt * (2.0 * a[i] + a[i + 1]) / 6.0
    return v, u


# --------------------------------------------------------------------------- #
# Nigam-Jennings response spectrum
# --------------------------------------------------------------------------- #
def nj_coefficients(w: np.ndarray, z: float, h: float):
    """Exact piecewise-linear recurrence coefficients (unit mass,
    k = w^2) for an array of circular frequencies ``w`` > 0, 0 <= z < 1."""
    w = np.asarray(w, dtype=float)
    k = w * w
    sq = math.sqrt(1.0 - z * z)
    wd = w * sq
    e = np.exp(-z * w * h)
    s = np.sin(wd * h)
    c = np.cos(wd * h)
    zs = z / sq
    A = e * (zs * s + c)
    B = e * (s / wd)
    C = (2.0 * z / (w * h)
         + e * (((1.0 - 2.0 * z * z) / (wd * h) - zs) * s
                - (1.0 + 2.0 * z / (w * h)) * c)) / k
    D = (1.0 - 2.0 * z / (w * h)
         + e * ((2.0 * z * z - 1.0) / (wd * h) * s
                + 2.0 * z / (w * h) * c)) / k
    Ap = -e * (w / sq * s)
    Bp = e * (c - zs * s)
    Cp = (-1.0 / h + e * ((w / sq + z / (h * sq)) * s + c / h)) / k
    Dp = (1.0 - e * (zs * s + c)) / (k * h)
    return A, B, C, D, Ap, Bp, Cp, Dp


def sdof_history(acc: Sequence[float], dt: float, T: float, z: float):
    """Exact relative displacement / velocity histories (at the samples)
    of one SDOF (period T > 0, ratio z) under base acceleration ``acc``
    (zero initial conditions)."""
    w = np.array([2.0 * math.pi / T])
    co = nj_coefficients(w, z, dt)
    a = np.asarray(acc, dtype=float)
    n = len(a)
    u = np.zeros(n)
    v = np.zeros(n)
    A, B, C, D, Ap, Bp, Cp, Dp = (float(x[0]) for x in co)
    for i in range(n - 1):
        p0, p1 = -a[i], -a[i + 1]
        u[i + 1] = A * u[i] + B * v[i] + C * p0 + D * p1
        v[i + 1] = Ap * u[i] + Bp * v[i] + Cp * p0 + Dp * p1
    return u, v


def response_spectrum(acc: Sequence[float], dt: float,
                      periods: Optional[Sequence[float]] = None,
                      damping: Sequence[float] = (0.05,)) -> dict:
    """Response spectra of the acceleration series ``acc`` (m/s^2, sample
    k at t = k*dt, zero initial conditions) for each damping ratio.

    Returns ``{"periods": [...], "spectra": [{"damping", "Sd", "Sv",
    "Sa", "PSv", "PSa"}, ...], "pga": max|a|}`` (SI: m, m/s, m/s^2).
    """
    if not (isinstance(dt, (int, float)) and math.isfinite(dt) and dt > 0):
        raise ValueError("dt must be > 0")
    a = np.asarray(acc, dtype=float)
    if a.ndim != 1 or len(a) < 2:
        raise ValueError("acceleration series needs >= 2 samples")
    if not np.all(np.isfinite(a)):
        raise ValueError("acceleration series must be finite")
    Ts = list(DEFAULT_PERIODS if periods is None else periods)
    if not Ts:
        raise ValueError("periods must not be empty")
    for T in Ts:
        if not (isinstance(T, (int, float)) and not isinstance(T, bool)
                and math.isfinite(T) and T >= 0.0):
            raise ValueError("periods must be finite values >= 0")
    zs = list(damping)
    if not zs:
        raise ValueError("damping must not be empty")
    for z in zs:
        if not (isinstance(z, (int, float)) and not isinstance(z, bool)
                and math.isfinite(z) and 0.0 <= z < 1.0):
            raise ValueError("damping ratios must be in [0, 1)")
    Tarr = np.asarray(Ts, dtype=float)
    pos = Tarr > 0.0
    w = np.where(pos, 2.0 * math.pi / np.where(pos, Tarr, 1.0), 0.0)
    wp = w[pos]
    pga = float(np.max(np.abs(a)))
    n = len(a)
    spectra = []
    for z in zs:
        Sd = np.zeros(len(Tarr))
        Sv = np.zeros(len(Tarr))
        Sa = np.full(len(Tarr), pga)
        if wp.size:
            A, B, C, D, Ap, Bp, Cp, Dp = nj_coefficients(wp, float(z), dt)
            u = np.zeros(wp.size)
            v = np.zeros(wp.size)
            mu = np.zeros(wp.size)
            mv = np.zeros(wp.size)
            ma = np.zeros(wp.size)
            c2 = 2.0 * z * wp
            k2 = wp * wp
            for i in range(n - 1):
                p0, p1 = -a[i], -a[i + 1]
                u, v = (A * u + B * v + C * p0 + D * p1,
                        Ap * u + Bp * v + Cp * p0 + Dp * p1)
                np.maximum(mu, np.abs(u), out=mu)
                np.maximum(mv, np.abs(v), out=mv)
                np.maximum(ma, np.abs(c2 * v + k2 * u), out=ma)
            Sd[pos], Sv[pos], Sa[pos] = mu, mv, ma
        spectra.append({
            "damping": float(z),
            "Sd": [float(x) for x in Sd],
            "Sv": [float(x) for x in Sv],
            "Sa": [float(x) for x in Sa],
            "PSv": [float(x) for x in w * Sd],
            "PSa": [float(x) if p else pga
                    for x, p in zip(w * w * Sd, pos)],
        })
    return {"periods": [float(T) for T in Tarr], "spectra": spectra,
            "pga": pga,
            "method": "Nigam-Jennings exact piecewise-linear recurrence"}


def joint_abs_accel(pf: dict, point: Sequence[float],
                    direction: str) -> List[float]:
    """The recorded absolute acceleration series (incl. t = 0) of the
    requested joint nearest-equal to ``point`` in ``direction`` (X|Y|Z)
    from a TH result's ``plot_functions`` dict; ``"ground"`` as point
    returns the ground acceleration.  Raises KeyError when not recorded."""
    key = {"X": "UX", "Y": "UY", "Z": "UZ"}[direction]
    if isinstance(point, str):
        if point != "ground":
            raise KeyError(point)
        return list(pf["ground"]["acc"][key])
    for j in pf.get("joints", []):
        if all(abs(float(a) - float(b)) < 1e-6
               for a, b in zip(j["point"], point)):
            return list(j["acc_abs"][key])
    raise KeyError(tuple(point))


def spectrum_dict_from_results(pf: dict, point, direction: str,
                               damping: Sequence[float],
                               periods=None) -> Dict[str, object]:
    """Floor response spectrum from a recorded plot_functions dict."""
    t = pf["t"]
    dt = float(t[1] - t[0]) if len(t) > 1 else 0.0
    acc = joint_abs_accel(pf, point, direction)
    out = response_spectrum(acc, dt, periods, damping)
    out["direction"] = direction
    out["dt"] = dt
    return out


# --------------------------------------------------------------------------- #
# POST /api/plotfn/spectrum
# --------------------------------------------------------------------------- #
def _damping_list(v) -> List[float]:
    if v is None:
        return [0.05]
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return [float(v)]
    if isinstance(v, list) and v and all(
            isinstance(z, (int, float)) and not isinstance(z, bool)
            for z in v):
        return [float(z) for z in v]
    raise ValueError("'damping' must be a ratio or a non-empty list of "
                     "ratios")


def spectrum_request(model, body, stored: Optional[dict] = None) -> dict:
    """Floor (or ground / raw-series) response spectrum for the API.

    Body ``{case, point: [x, y, z] | "ground", damping: z | [z...],
    direction?: "X"|"Y"|"Z" (default: the case direction), periods?}``;
    or ``{accel: [...], dt, damping?, periods?}`` for a raw series.
    The joint's absolute acceleration comes from the stored analysis
    results when that joint was recorded (``source: "recorded"``), else
    the case is re-run on a copy of the model with the joint added to its
    output_requests (``source: "rerun"``) — the model is never mutated.
    """
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")
    damping = _damping_list(body.get("damping"))
    periods = body.get("periods")
    if periods is not None and not isinstance(periods, list):
        raise ValueError("'periods' must be a list of periods (s)")
    if "accel" in body:
        acc = body.get("accel")
        if not isinstance(acc, list):
            raise ValueError("'accel' must be a list (m/s^2)")
        dt = body.get("dt")
        if isinstance(dt, bool) or not isinstance(dt, (int, float)):
            raise ValueError("'dt' must be a number > 0")
        out = response_spectrum([float(a) for a in acc], float(dt),
                                periods, damping)
        out.update(source="series", dt=float(dt))
        return out
    case = body.get("case")
    if not isinstance(case, str) or case not in model.th_cases:
        raise ValueError("'case' must name a time-history case")
    th = model.th_cases[case]
    point = body.get("point", "ground")
    if not isinstance(point, str):
        point = normalize({"joints": [point]})["joints"][0]
    elif point != "ground":
        raise ValueError("'point' must be [x, y, z] or \"ground\"")
    direction = body.get("direction") or th.direction
    if direction not in ("X", "Y", "Z"):
        raise ValueError("'direction' must be X|Y|Z")
    pf = None
    source = "recorded"
    if stored:
        pf = ((stored.get("th_cases") or {}).get(case) or {}).get(
            "plot_functions")
    if pf is not None:
        try:
            joint_abs_accel(pf, point, direction)
        except KeyError:
            pf = None
    if pf is None:
        from skyframe.engine.opensees_engine import OpenSeesEngine
        cp = type(model).from_dict(model.to_dict())
        th2 = cp.th_cases[case]
        req = dict(th2.output_requests or {})
        if not isinstance(point, str):
            req["joints"] = list(req.get("joints", [])) + [list(point)]
        th2.output_requests = normalize(req)
        pf = OpenSeesEngine(cp).run_time_history(case).extra[
            "plot_functions"]
        source = "rerun"
    out = spectrum_dict_from_results(pf, point, direction, damping, periods)
    out.update(case=case, point=point, source=source)
    return out
