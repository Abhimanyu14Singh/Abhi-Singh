"""Multi-component ground motion + load-pattern time histories (pure logic).

A :class:`~skyframe.core.model.TimeHistoryCase` may carry
``components`` (default ``None`` = the legacy single record in
``direction``, untouched).  Each entry is ONE of:

* ground component ``{"direction": "UX"|"UY"|"UZ", "function": name,
  "scale": 1.0, "angle_deg": 0.0, "time_shift": 0.0}`` — a uniform
  support acceleration.  ``function`` names a ``model.th_functions``
  record (omitted/empty -> the case's own record: inline ``accel``/``dt``
  or the case ``function``).  ``angle_deg`` rotates a HORIZONTAL
  component counter-clockwise in plan: UX at theta acts along
  ``(cos theta, sin theta)``, UY at theta along ``(-sin theta,
  cos theta)``; it must be 0 for UZ.
* load-pattern component ``{"pattern": name, "function": name,
  "scale": 1.0, "time_shift": 0.0}`` — the pattern's loads times
  ``scale * f(t)`` (``function`` required: the time function, any
  units-free ``th_functions`` record).

``time_shift`` (s, >= 0) delays the component: its value is 0 before
``t = time_shift``.  The case ``scale`` multiplies every component.

Common time grid: ``dt`` = the smallest component record dt; every
component is resampled onto ``t_k = k*dt`` with the OpenSees ``Path``
rule (linear between samples, 0 outside the record) — a component whose
record already lies on the grid is copied sample-for-sample.  The run has
``n = max_c ceil((time_shift_c + (n_c - 1) dt_c)/dt) + 1`` steps (the
legacy ``len(accel)`` for a single unshifted component).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

GROUND_DIRECTIONS = ("UX", "UY", "UZ")
GROUND_KEYS = {"direction", "function", "scale", "angle_deg", "time_shift"}
PATTERN_KEYS = {"pattern", "function", "scale", "time_shift"}
_SNAP = 1e-12


def path_value(values, dt: float, t: float) -> float:
    """OpenSees ``Path -dt`` value at time t (linear, 0 outside)."""
    n = len(values)
    if t < -1e-12 or n == 0:
        return 0.0
    x = t / dt
    k = int(math.floor(x + 1e-9))
    if k >= n - 1:
        return float(values[n - 1]) if abs(x - (n - 1)) <= 1e-9 else 0.0
    r = x - k
    if r < 1e-9:
        return float(values[k])
    return float(values[k]) + r * (float(values[k + 1]) - float(values[k]))


def is_pattern(comp: dict) -> bool:
    return "pattern" in comp


def direction_cosines(direction: str, angle_deg: float
                      ) -> Tuple[float, float, float]:
    """Global (x, y, z) unit vector of a ground component."""
    if direction == "UZ":
        return (0.0, 0.0, 1.0)
    if angle_deg == 0.0:
        return (1.0, 0.0, 0.0) if direction == "UX" else (0.0, 1.0, 0.0)
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    c = 0.0 if abs(c) < _SNAP else c
    s = 0.0 if abs(s) < _SNAP else s
    return (c, s, 0.0) if direction == "UX" else (-s, c, 0.0)


def _num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(float(v)))


def normalize(components) -> Optional[List[dict]]:
    """Plain-dict copy (floats coerced) for storage/round-trip."""
    if components is None:
        return None
    out = []
    for c in components:
        if not isinstance(c, dict):
            out.append(c)          # rejected by validate()
            continue
        d = dict(c)
        for k in ("scale", "angle_deg", "time_shift"):
            if k in d and _num(d[k]):
                d[k] = float(d[k])
        out.append(d)
    return out


def validate_case(model, th) -> None:
    """Raise ValueError for an invalid ``th.components`` list."""
    comps = th.components
    name = th.name
    if comps is None:
        return
    if not isinstance(comps, list) or not comps:
        raise ValueError(f"TH case {name}: components must be a non-empty "
                         "list (or omitted)")
    for i, c in enumerate(comps):
        tag = f"TH case {name}: components[{i}]"
        if not isinstance(c, dict):
            raise ValueError(f"{tag} must be an object")
        pat = is_pattern(c)
        if pat and "direction" in c:
            raise ValueError(f"{tag}: give either direction (ground) or "
                             "pattern (load), not both")
        allowed = PATTERN_KEYS if pat else GROUND_KEYS
        bad = set(c) - allowed
        if bad:
            raise ValueError(f"{tag}: unknown keys {sorted(bad)} (allowed "
                             f"{sorted(allowed)})")
        for k in ("scale", "time_shift") + (() if pat else ("angle_deg",)):
            if k in c and not _num(c[k]):
                raise ValueError(f"{tag}: {k} must be a finite number")
        if float(c.get("time_shift", 0.0)) < 0.0:
            raise ValueError(f"{tag}: time_shift must be >= 0")
        fn = c.get("function", "") or ""
        if not isinstance(fn, str):
            raise ValueError(f"{tag}: function must be a name")
        if fn and fn not in model.th_functions:
            raise ValueError(f"{tag}: function {fn!r} is not a defined "
                             "th_function")
        if pat:
            p = c.get("pattern")
            if not isinstance(p, str) or p not in model.patterns:
                raise ValueError(f"{tag}: pattern {p!r} is not a defined "
                                 "load pattern")
            if not fn:
                raise ValueError(f"{tag}: a load-pattern component needs "
                                 "a time function (function)")
        else:
            d = c.get("direction")
            if d not in GROUND_DIRECTIONS:
                raise ValueError(f"{tag}: direction must be UX|UY|UZ, got "
                                 f"{d!r}")
            if d == "UZ" and float(c.get("angle_deg", 0.0)) != 0.0:
                raise ValueError(f"{tag}: angle_deg applies to horizontal "
                                 "components only (UZ must be 0)")
            if not fn and not (th.function or th.accel):
                raise ValueError(f"{tag}: no function given and the case "
                                 "has no record of its own")
            if not fn and not th.function and not (
                    math.isfinite(th.dt) and th.dt > 0.0):
                raise ValueError(f"{tag}: the case record dt must be > 0")


def _record(model, th, comp) -> Tuple[List[float], float]:
    fn = comp.get("function", "") or ""
    if fn:
        f = model.th_functions[fn]
        return list(f.values), float(f.dt)
    if th.function:
        f = model.th_functions[th.function]
        return list(f.values), float(f.dt)
    return list(th.accel), float(th.dt)


class Plan:
    """Resolved components on the common grid (see module docstring).

    ``ground``: ``[(dof, values, factor)]`` per component and nonzero
    global direction (dof 1/2/3, values = the resampled raw record,
    factor = case scale * component scale * direction cosine);
    ``patterns``: ``[(pattern, values, factor)]``; ``resolved``: the
    per-component report written to the results.
    """

    def __init__(self, dt: float, n: int) -> None:
        self.dt = dt
        self.n = n
        self.ground: List[Tuple[int, List[float], float]] = []
        self.patterns: List[Tuple[str, List[float], float]] = []
        self.resolved: List[dict] = []

    def ground_series(self, dof: int) -> List[float]:
        """Combined scaled ground acceleration samples in global ``dof``."""
        out = [0.0] * self.n
        for d, vals, fac in self.ground:
            if d == dof:
                for k in range(self.n):
                    out[k] += fac * vals[k]
        return out

    def pattern_factors(self) -> List[List[float]]:
        """Per pattern component: scaled f(t_k) samples."""
        return [[fac * v for v in vals] for _p, vals, fac in self.patterns]

    def dofs(self) -> List[int]:
        return sorted({d for d, _v, _f in self.ground})


def resolve(model, th) -> Optional[Plan]:
    """The case's :class:`Plan`, or ``None`` for a legacy case."""
    comps = getattr(th, "components", None)
    if comps is None:
        return None
    recs = [_record(model, th, c) for c in comps]
    dt = min(r[1] for r in recs)
    n = 1
    for c, (vals, dtc) in zip(comps, recs):
        end = float(c.get("time_shift", 0.0)) + (len(vals) - 1) * dtc
        n = max(n, int(math.ceil(end / dt - 1e-9)) + 1)
    plan = Plan(dt, n)
    case_scale = float(th.scale)
    for c, (vals, dtc) in zip(comps, recs):
        shift = float(c.get("time_shift", 0.0))
        if shift == 0.0 and dtc == dt:
            grid = [float(v) for v in vals[:n]] + [0.0] * (n - len(vals))
        else:
            grid = [path_value(vals, dtc, k * dt - shift) for k in range(n)]
        sc = case_scale * float(c.get("scale", 1.0))
        if is_pattern(c):
            plan.patterns.append((c["pattern"], grid, sc))
            plan.resolved.append({
                "pattern": c["pattern"], "function": c["function"],
                "effective_scale": sc, "time_shift": shift})
            continue
        ang = float(c.get("angle_deg", 0.0))
        cos3 = direction_cosines(c["direction"], ang)
        for d0, cv in enumerate(cos3):
            if cv != 0.0:
                plan.ground.append((d0 + 1, grid, sc * cv))
        plan.resolved.append({
            "direction": c["direction"],
            "function": c.get("function", "") or "",
            "effective_scale": sc, "angle_deg": ang,
            "time_shift": shift,
            "cosines": list(cos3)})
    return plan
