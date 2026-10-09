"""Frequency-domain load cases: Steady-State and Power Spectral Density.

ETABS-style frequency-domain analysis cases (analysis only).  The data
lives on :class:`skyframe.core.model.BuildingModel` in three dicts:

* ``frequency_functions`` — name -> :class:`FrequencyFunction` (a named
  ``[[f_hz, value], ...]`` table; ``kind == "steady_state"`` for an
  amplitude-vs-frequency function, ``kind == "psd"`` for a one-sided power
  spectral density in (load)^2/Hz);
* ``steady_state_cases`` — name -> :class:`SteadyStateCase`;
* ``psd_cases`` — name -> :class:`PSDCase`.

All three default to empty; ``to_dict`` emits a key only when its dict is
non-empty, so models that do not use frequency-domain analysis serialise
byte-identically to before.  The solver lives in
:mod:`skyframe.engine.frequency`.

This module deliberately does NOT import :mod:`skyframe.core.model` at
import time (model.py imports it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

FREQ_FUNCTION_KINDS = ("steady_state", "psd")
FREQ_ACCEL_DIRECTIONS = ("UX", "UY", "UZ")
FREQ_ACCEL = "accel"                      # FrequencyLoad.pattern sentinel
FREQ_DAMPING_TYPES = ("modal", "hysteretic")
FREQ_METHODS = ("modal", "direct")
FREQ_MAX_POINTS = 20000                   # cap on the frequency grid size


def _finite(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


# --------------------------------------------------------------------------- #
# data classes
# --------------------------------------------------------------------------- #
@dataclass
class FrequencyFunction:
    """A named frequency function: ``points = [[f_hz, value], ...]``.

    ``kind == "steady_state"``: value = load AMPLITUDE multiplier at f.
    ``kind == "psd"``: value = one-sided power spectral density of the load
    multiplier at f, in (load)^2/Hz (for acceleration loads the "load" is
    the ground acceleration in the units the case ``scale`` converts to
    m/s^2 — e.g. a g^2/Hz function with ``scale = 9.80665``).

    Interpolation is LINEAR in f between points; the function is ZERO
    outside ``[f_first, f_last]``.  Frequencies strictly increasing, >= 0.
    """

    name: str
    kind: str                       # "steady_state" | "psd"
    points: List[List[float]]

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind,
                "points": [[float(f), float(v)] for f, v in self.points]}


@dataclass
class FrequencyLoad:
    """One load of a frequency-domain case.

    * ``pattern`` = a load-pattern name: the pattern's (static) spatial
      load distribution times ``scale * function(f)``;
    * ``pattern == "accel"`` with ``direction`` in UX/UY/UZ: uniform
      support (ground) acceleration ``scale * function(f)`` in m/s^2;
      responses are RELATIVE to the ground.

    ``function`` names a ``model.frequency_functions`` entry of the case's
    kind; for a steady-state load ``""`` means the constant 1.0 (a PSD
    load must name a PSD function).  ``phase_deg`` shifts the load's phase
    (steady state: the load is ``Re{F e^{i(wt + phase)}}``; PSD: the phase
    of the fully-correlated input, see :class:`PSDCase`).
    """

    pattern: str
    scale: float = 1.0
    function: str = ""
    direction: str = ""             # accel only: "UX" | "UY" | "UZ"
    phase_deg: float = 0.0

    @property
    def is_accel(self) -> bool:
        return self.pattern == FREQ_ACCEL

    def to_dict(self) -> dict:
        return {"pattern": self.pattern, "direction": self.direction,
                "scale": float(self.scale), "function": self.function,
                "phase_deg": float(self.phase_deg)}

    @classmethod
    def from_dict(cls, d) -> "FrequencyLoad":
        if isinstance(d, FrequencyLoad):
            return d
        if not isinstance(d, dict):
            raise ValueError("frequency-domain load must be an object")
        return cls(pattern=str(d.get("pattern", "")),
                   scale=float(d.get("scale", 1.0)),
                   function=str(d.get("function", "") or ""),
                   direction=str(d.get("direction", "") or "").upper(),
                   phase_deg=float(d.get("phase_deg", 0.0)))


@dataclass
class _FrequencyCaseBase:
    """Fields shared by :class:`SteadyStateCase` and :class:`PSDCase`.

    Frequency grid (Hz): ``n_freq`` linearly spaced points over
    ``[freq_start_hz, freq_end_hz]`` (``n_freq == 1`` -> just the start;
    ``0`` -> none), UNION the explicit ``frequencies`` list, UNION (when
    ``modal_refine``) clustered points ``f_i * (1 + k*zeta)`` around every
    modal frequency inside the range (k in +-{0.1 .. 13} and 0).  Sorted,
    duplicates removed; at most ``FREQ_MAX_POINTS``.

    ``method``: ``"modal"`` — modal superposition over ``num_modes`` modes
    (0 = ``model.num_modes``); ``"direct"`` — exact solution of the full
    (condensed) system: ALL eigenmodes plus the static residual-flexibility
    correction (mathematically identical to the complex direct solve of
    ``(K* - w^2 M + i w C) u = F`` with the massless DOFs condensed).

    ``damping`` / ``damping_type``: ``"modal"`` = constant viscous modal
    damping ratio (``H_i = 1 / (w_i^2 - w^2 + 2 i zeta w_i w)``);
    ``"hysteretic"`` = complex stiffness ``K (1 + 2 i zeta)`` (loss factor
    ``eta = 2 zeta``; ``H_i = 1 / (w_i^2 (1 + 2 i zeta) - w^2)``).  Must be
    in (0, 1).

    ``output_points``: ``[[x, y, z], ...]`` joints whose per-frequency
    response is reported (steady state: empty -> every joint; PSD: empty
    -> none, only RMS for every joint).
    """

    name: str
    loads: List[FrequencyLoad] = field(default_factory=list)
    freq_start_hz: float = 0.0
    freq_end_hz: float = 10.0
    n_freq: int = 101
    frequencies: List[float] = field(default_factory=list)
    modal_refine: bool = True
    method: str = "modal"
    num_modes: int = 0
    damping: float = 0.05
    damping_type: str = "modal"
    output_points: List[List[float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name,
                "loads": [ld.to_dict() for ld in self.loads],
                "freq_start_hz": float(self.freq_start_hz),
                "freq_end_hz": float(self.freq_end_hz),
                "n_freq": int(self.n_freq),
                "frequencies": [float(f) for f in self.frequencies],
                "modal_refine": bool(self.modal_refine),
                "method": self.method,
                "num_modes": int(self.num_modes),
                "damping": float(self.damping),
                "damping_type": self.damping_type,
                "output_points": [[float(c) for c in p]
                                  for p in self.output_points]}


@dataclass
class SteadyStateCase(_FrequencyCaseBase):
    """Steady-state (harmonic) case: each load ``scale * A(f) e^{i phase}``
    at every grid frequency; output = complex response (amplitude + phase)
    per frequency and the frequency-response peak per DOF."""

    KIND = "steady_state"


@dataclass
class PSDCase(_FrequencyCaseBase):
    """Power-spectral-density (random vibration) case.

    Each load is a stationary random process with one-sided PSD
    ``scale^2 * S(f)`` ((load)^2/Hz).  CORRELATION ASSUMPTION: all loads of
    one case are FULLY CORRELATED (one common underlying process; load j
    contributes ``scale_j sqrt(S_j(f)) e^{i phase_j}``).  The response PSD is
    then ``S_R(f) = |sum_j sum_i R_i p_ij H_i(f) a_j(f)|^2`` — every modal
    cross term (the exact CQC-style double sum) is included — and the RMS
    is ``sqrt(integral S_R df)`` (trapezoid over the grid).  For mutually
    UNCORRELATED inputs define one PSD case per input and SRSS the RMS.
    """

    KIND = "psd"


# --------------------------------------------------------------------------- #
# convenience constructors
# --------------------------------------------------------------------------- #
def _coerce_case_kwargs(kw: dict) -> dict:
    out = dict(kw)
    if "loads" in out:
        out["loads"] = [FrequencyLoad.from_dict(ld) for ld in out["loads"]]
    return out


def add_frequency_function(model, name: str, kind: str,
                           points: Sequence[Sequence[float]]
                           ) -> FrequencyFunction:
    fn = FrequencyFunction(name, kind, [[float(f), float(v)]
                                        for f, v in points])
    _validate_function(fn)
    model.frequency_functions[name] = fn
    return fn


def add_steady_state_case(model, name: str, loads, **kw) -> SteadyStateCase:
    case = SteadyStateCase(name, **_coerce_case_kwargs(dict(kw, loads=loads)))
    _validate_case(model, case, SteadyStateCase.KIND)
    model.steady_state_cases[name] = case
    return case


def add_psd_case(model, name: str, loads, **kw) -> PSDCase:
    case = PSDCase(name, **_coerce_case_kwargs(dict(kw, loads=loads)))
    _validate_case(model, case, PSDCase.KIND)
    model.psd_cases[name] = case
    return case


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _validate_function(fn: FrequencyFunction) -> None:
    if fn.kind not in FREQ_FUNCTION_KINDS:
        raise ValueError(f"Frequency function {fn.name!r}: kind must be one "
                         f"of {FREQ_FUNCTION_KINDS}, got {fn.kind!r}")
    pts = fn.points
    if not isinstance(pts, list) or len(pts) < 2:
        raise ValueError(f"Frequency function {fn.name!r}: needs at least "
                         "two [f_hz, value] points")
    prev = -1.0
    for p in pts:
        if len(p) != 2 or not (_finite(p[0]) and _finite(p[1])):
            raise ValueError(f"Frequency function {fn.name!r}: points must "
                             "be finite [f_hz, value] pairs")
        if p[0] < 0.0 or p[0] <= prev:
            raise ValueError(f"Frequency function {fn.name!r}: frequencies "
                             "must be >= 0 and strictly increasing")
        if fn.kind == "psd" and p[1] < 0.0:
            raise ValueError(f"Frequency function {fn.name!r}: PSD values "
                             "must be >= 0")
        prev = p[0]


def _validate_case(model, case, kind: str) -> None:
    label = "Steady-state" if kind == "steady_state" else "PSD"
    nm = case.name
    for ld in case.loads:
        if not isinstance(ld, FrequencyLoad):
            raise ValueError(f"{label} case {nm!r}: loads must be "
                             "FrequencyLoad entries")
        if ld.is_accel:
            if ld.direction not in FREQ_ACCEL_DIRECTIONS:
                raise ValueError(f"{label} case {nm!r}: accel load direction "
                                 f"must be one of {FREQ_ACCEL_DIRECTIONS}, "
                                 f"got {ld.direction!r}")
        elif ld.pattern not in model.patterns:
            raise ValueError(f"{label} case {nm!r}: unknown load pattern "
                             f"{ld.pattern!r}")
        if not (_finite(ld.scale) and _finite(ld.phase_deg)):
            raise ValueError(f"{label} case {nm!r}: load scale/phase_deg "
                             "must be finite")
        if ld.function:
            fn = model.frequency_functions.get(ld.function)
            if fn is None:
                raise ValueError(f"{label} case {nm!r}: unknown frequency "
                                 f"function {ld.function!r}")
            if fn.kind != kind:
                raise ValueError(f"{label} case {nm!r}: function "
                                 f"{ld.function!r} is a {fn.kind!r} function "
                                 f"(need {kind!r})")
        elif kind == "psd":
            raise ValueError(f"{label} case {nm!r}: every load must name a "
                             "PSD function")
    if not (_finite(case.freq_start_hz) and _finite(case.freq_end_hz)
            and 0.0 <= case.freq_start_hz <= case.freq_end_hz):
        raise ValueError(f"{label} case {nm!r}: need finite "
                         "0 <= freq_start_hz <= freq_end_hz")
    if (isinstance(case.n_freq, bool) or not isinstance(case.n_freq, int)
            or not 0 <= case.n_freq <= FREQ_MAX_POINTS):
        raise ValueError(f"{label} case {nm!r}: n_freq must be an int in "
                         f"[0, {FREQ_MAX_POINTS}]")
    if len(case.frequencies) > FREQ_MAX_POINTS or not all(
            _finite(f) and f >= 0.0 for f in case.frequencies):
        raise ValueError(f"{label} case {nm!r}: frequencies must be finite "
                         "values >= 0")
    if case.n_freq == 0 and not case.frequencies:
        raise ValueError(f"{label} case {nm!r}: empty frequency grid (set "
                         "n_freq > 0 or list frequencies)")
    if case.method not in FREQ_METHODS:
        raise ValueError(f"{label} case {nm!r}: method must be one of "
                         f"{FREQ_METHODS}, got {case.method!r}")
    if case.damping_type not in FREQ_DAMPING_TYPES:
        raise ValueError(f"{label} case {nm!r}: damping_type must be one of "
                         f"{FREQ_DAMPING_TYPES}, got {case.damping_type!r}")
    if not (_finite(case.damping) and 0.0 < case.damping < 1.0):
        raise ValueError(f"{label} case {nm!r}: damping must be in (0, 1) "
                         f"(got {case.damping!r})")
    if (isinstance(case.num_modes, bool) or not isinstance(case.num_modes, int)
            or case.num_modes < 0):
        raise ValueError(f"{label} case {nm!r}: num_modes must be an int "
                         ">= 0")
    for p in case.output_points:
        if len(p) != 3 or not all(_finite(c) for c in p):
            raise ValueError(f"{label} case {nm!r}: output_points must be "
                             "[x, y, z] finite triples")


def validate_frequency(model) -> None:
    """Validate the model's frequency functions and cases (ValueError)."""
    for fn in getattr(model, "frequency_functions", {}).values():
        _validate_function(fn)
    for case in getattr(model, "steady_state_cases", {}).values():
        _validate_case(model, case, "steady_state")
    for case in getattr(model, "psd_cases", {}).values():
        _validate_case(model, case, "psd")


# --------------------------------------------------------------------------- #
# (de)serialisation
# --------------------------------------------------------------------------- #
def frequency_to_dict(model) -> dict:
    """Model-dict entries; a key is emitted ONLY when non-empty."""
    out: dict = {}
    fns = getattr(model, "frequency_functions", {})
    if fns:
        out["frequency_functions"] = {k: v.to_dict() for k, v in fns.items()}
    ss = getattr(model, "steady_state_cases", {})
    if ss:
        out["steady_state_cases"] = {k: v.to_dict() for k, v in ss.items()}
    pc = getattr(model, "psd_cases", {})
    if pc:
        out["psd_cases"] = {k: v.to_dict() for k, v in pc.items()}
    return out


def _case_from_dict(cls, name: str, cd: dict):
    if not isinstance(cd, dict):
        raise ValueError(f"frequency-domain case {name!r} must be an object")
    return cls(
        name=str(cd.get("name", name)),
        loads=[FrequencyLoad.from_dict(ld) for ld in (cd.get("loads") or [])],
        freq_start_hz=float(cd.get("freq_start_hz", 0.0)),
        freq_end_hz=float(cd.get("freq_end_hz", 10.0)),
        n_freq=int(cd.get("n_freq", 101)),
        frequencies=[float(f) for f in (cd.get("frequencies") or [])],
        modal_refine=bool(cd.get("modal_refine", True)),
        method=str(cd.get("method", "modal")),
        num_modes=int(cd.get("num_modes", 0)),
        damping=float(cd.get("damping", 0.05)),
        damping_type=str(cd.get("damping_type", "modal")),
        output_points=[[float(c) for c in p]
                       for p in (cd.get("output_points") or [])])


def frequency_from_dict(model, d: dict) -> None:
    """Populate the model's frequency dicts from a model dict (missing ->
    empty).  Validation happens in ``BuildingModel.validate``."""
    for name, fd in (d.get("frequency_functions") or {}).items():
        model.frequency_functions[name] = FrequencyFunction(
            name=str(fd.get("name", name)), kind=str(fd.get("kind", "")),
            points=[[float(p[0]), float(p[1])] for p in fd["points"]])
    for name, cd in (d.get("steady_state_cases") or {}).items():
        model.steady_state_cases[name] = _case_from_dict(SteadyStateCase,
                                                         name, cd)
    for name, cd in (d.get("psd_cases") or {}).items():
        model.psd_cases[name] = _case_from_dict(PSDCase, name, cd)


def interp_function(fn: Optional[FrequencyFunction], freqs) -> "object":
    """Function values at ``freqs`` (numpy array); None -> constant 1."""
    import numpy as np
    f = np.asarray(freqs, dtype=float)
    if fn is None:
        return np.ones_like(f)
    xs = np.array([p[0] for p in fn.points], dtype=float)
    ys = np.array([p[1] for p in fn.points], dtype=float)
    return np.interp(f, xs, ys, left=0.0, right=0.0)
