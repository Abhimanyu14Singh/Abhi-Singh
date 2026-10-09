"""Direct-integration time-history options (pure Python, no OpenSees).

ETABS-style controls for a :class:`~skyframe.core.model.TimeHistoryCase`
solved by direct integration (``OpenSeesEngine.run_time_history``):

* ``integration`` — time-integration scheme::

      {"method": "newmark" | "hht" | "wilson" | "central_difference",
       "gamma": 0.5, "beta": 0.25,      # newmark (and hht override)
       "alpha": 0.0,                    # hht, in [-1/3, 0]
       "theta": 1.4}                    # wilson, in [1, 2]

  HHT uses the Hilber-Hughes-Taylor sign convention (``alpha`` <= 0 adds
  numerical damping; 0 == Newmark average acceleration).  When ``gamma`` /
  ``beta`` are omitted for HHT the second-order, unconditionally stable
  values ``gamma = 1/2 - alpha``, ``beta = (1 - alpha)^2 / 4`` are used.
  OpenSees is called with ``alpha_OS = 1 + alpha``.

* ``di_damping`` — viscous damping for direct integration::

      {"type": "rayleigh_by_periods", "T1", "xi1", "T2", "xi2",
       "stiffness": "initial" | "current" | "committed"}
      {"type": "rayleigh_coefficients", "mass_coeff", "stiffness_coeff",
       "stiffness": ...}

  ``None`` keeps the legacy behaviour (``damping_model`` "rayleigh" fit
  at modes 1/3, or "modal").

* ``solver`` — nonlinear solution control::

      {"max_iterations": 25, "tolerance": 1e-8,
       "test": "NormDispIncr" | "NormUnbalance" | "EnergyIncr",
       "max_halvings": 0}

  ``max_halvings`` N > 0: a record step that does not converge is
  re-solved as two half steps, recursively, down to dt / 2**N
  (ETABS-like substepping); the steps that needed it are reported.

* ``energy`` (bool) — record the energy time series (see
  :mod:`skyframe.engine.thoptions`).

Every option defaults to ``None`` / ``False``, which leaves the engine on
its pre-existing code path (bit-identical results).
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

INTEGRATION_METHODS = ("newmark", "hht", "wilson", "central_difference")
DI_DAMPING_TYPES = ("rayleigh_by_periods", "rayleigh_coefficients")
STIFFNESS_BASES = ("initial", "current", "committed")
SOLVER_TESTS = ("NormDispIncr", "NormUnbalance", "EnergyIncr")
MAX_HALVINGS_CAP = 12

SOLVER_DEFAULTS = {"max_iterations": 25, "tolerance": 1.0e-8,
                   "test": "NormDispIncr", "max_halvings": 0}


def _num(d: dict, key: str, default=None) -> float:
    v = d.get(key, default)
    if v is None:
        raise ValueError(f"missing {key!r}")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{key!r} must be a number, got {v!r}")
    v = float(v)
    if not math.isfinite(v):
        raise ValueError(f"{key!r} must be finite")
    return v


# --------------------------------------------------------------------------- #
# integration
# --------------------------------------------------------------------------- #
def resolve_integration(integ: Optional[dict]) -> dict:
    """Normalized integration dict (all parameters filled in).

    Raises ``ValueError`` on an invalid specification.
    """
    if integ is None:
        integ = {}
    if not isinstance(integ, dict):
        raise ValueError("integration must be a dict")
    method = integ.get("method", "newmark")
    if method not in INTEGRATION_METHODS:
        raise ValueError(f"integration method must be one of "
                         f"{INTEGRATION_METHODS}, got {method!r}")
    out: Dict[str, float] = {"method": method}
    if method == "newmark":
        g = _num(integ, "gamma", 0.5)
        b = _num(integ, "beta", 0.25)
        if not 0.5 <= g <= 1.0:
            raise ValueError(f"Newmark gamma must be in [0.5, 1], got {g}")
        if not 0.0 < b <= 0.5:
            raise ValueError(f"Newmark beta must be in (0, 0.5] (use "
                             f"method 'central_difference' for beta = 0), "
                             f"got {b}")
        out.update(gamma=g, beta=b)
    elif method == "hht":
        a = _num(integ, "alpha", 0.0)
        if not -1.0 / 3.0 - 1e-12 <= a <= 0.0:
            raise ValueError(f"HHT alpha must be in [-1/3, 0], got {a}")
        g = _num(integ, "gamma", 0.5 - a)
        b = _num(integ, "beta", (1.0 - a) ** 2 / 4.0)
        if not 0.5 <= g <= 1.5 or not 0.0 < b <= 1.0:
            raise ValueError(f"HHT gamma/beta out of range ({g}, {b})")
        out.update(alpha=a, gamma=g, beta=b)
    elif method == "wilson":
        th = _num(integ, "theta", 1.4)
        if not 1.0 <= th <= 2.0:
            raise ValueError(f"Wilson theta must be in [1, 2] "
                             f"(>= 1.37 for unconditional stability), "
                             f"got {th}")
        out.update(theta=th)
    else:                                   # central difference
        out.update(gamma=0.5, beta=0.0)
    return out


def integrator_args(norm: dict) -> tuple:
    """``ops.integrator(*args)`` arguments for a normalized dict."""
    m = norm["method"]
    if m == "newmark":
        return ("Newmark", norm["gamma"], norm["beta"])
    if m == "hht":
        return ("HHT", 1.0 + norm["alpha"], norm["gamma"], norm["beta"])
    if m == "wilson":
        return ("WilsonTheta", norm["theta"])
    return ("CentralDifference",)


def stability_limit(norm: dict, T_min: float) -> Optional[float]:
    """Critical time step of a CONDITIONALLY stable scheme, else ``None``.

    Newmark family (undamped): ``Omega_crit = 1/sqrt(gamma/2 - beta)``
    when ``2 beta < gamma``, so ``dt_crit = T_min * Omega_crit / (2 pi)``;
    central difference (gamma=1/2, beta=0) gives ``dt_crit = T_min / pi``.
    HHT (with its default gamma/beta) and Wilson theta >= 1.37 are
    unconditionally stable.
    """
    m = norm["method"]
    if m == "wilson":
        return None        # theta >= 1.37 unconditional; no closed form below
    g, b = norm.get("gamma", 0.5), norm.get("beta", 0.25)
    if 2.0 * b >= g - 1e-15:
        return None
    omega_crit = 1.0 / math.sqrt(g / 2.0 - b)
    return T_min * omega_crit / (2.0 * math.pi)


def needs_stability_check(norm: dict) -> bool:
    if norm["method"] in ("wilson",):
        return False
    return 2.0 * norm.get("beta", 0.25) < norm.get("gamma", 0.5) - 1e-15


def check_stability(norm: dict, dt: float, T_min: float) -> Optional[float]:
    """Raise ``ValueError`` when ``dt`` violates the critical step."""
    lim = stability_limit(norm, T_min)
    if lim is not None and not dt < lim:
        what = ("T_min/pi" if norm["method"] == "central_difference"
                else "T_min*Omega_crit/(2 pi)")
        raise ValueError(
            f"{norm['method']} integration is conditionally stable: "
            f"dt = {dt:.6g} s must be < {what} = {lim:.6g} s "
            f"(T_min = {T_min:.6g} s, the shortest period of the model)")
    return lim


# --------------------------------------------------------------------------- #
# damping
# --------------------------------------------------------------------------- #
def rayleigh_from_periods(T1: float, xi1: float, T2: float,
                          xi2: float) -> Tuple[float, float]:
    """(a0, a1) with ``xi(w) = a0/(2w) + a1 w/2`` exact at T1 and T2."""
    w1, w2 = 2.0 * math.pi / T1, 2.0 * math.pi / T2
    den = w2 * w2 - w1 * w1
    a1 = 2.0 * (xi2 * w2 - xi1 * w1) / den
    a0 = 2.0 * w1 * w2 * (xi1 * w2 - xi2 * w1) / den
    return a0, a1


def rayleigh_ratio(a0: float, a1: float, T: float) -> float:
    """Damping ratio of mass/stiffness-proportional damping at period T."""
    w = 2.0 * math.pi / T
    return a0 / (2.0 * w) + a1 * w / 2.0


def rayleigh_ops_args(a0: float, a1: float, basis: str) -> tuple:
    """``ops.rayleigh(alphaM, betaK, betaKinit, betaKcomm)`` arguments."""
    if basis == "current":
        return (a0, a1, 0.0, 0.0)
    if basis == "initial":
        return (a0, 0.0, a1, 0.0)
    if basis == "committed":
        return (a0, 0.0, 0.0, a1)
    raise ValueError(f"stiffness basis must be one of {STIFFNESS_BASES}")


def resolve_di_damping(dd: Optional[dict],
                       default_basis: str = "current") -> Optional[dict]:
    """Normalized damping dict ``{type, a0, a1, stiffness, ...}``."""
    if dd is None:
        return None
    if not isinstance(dd, dict):
        raise ValueError("di_damping must be a dict")
    typ = dd.get("type")
    if typ not in DI_DAMPING_TYPES:
        raise ValueError(f"di_damping type must be one of "
                         f"{DI_DAMPING_TYPES}, got {typ!r}")
    basis = dd.get("stiffness") or default_basis
    if basis not in STIFFNESS_BASES:
        raise ValueError(f"di_damping stiffness must be one of "
                         f"{STIFFNESS_BASES}, got {basis!r}")
    out = {"type": typ, "stiffness": basis}
    if typ == "rayleigh_by_periods":
        T1, T2 = _num(dd, "T1"), _num(dd, "T2")
        xi1, xi2 = _num(dd, "xi1"), _num(dd, "xi2")
        if T1 <= 0.0 or T2 <= 0.0:
            raise ValueError("di_damping periods T1/T2 must be > 0")
        if abs(T1 - T2) <= 1e-9 * max(T1, T2):
            raise ValueError("di_damping periods T1 and T2 must differ")
        for xi in (xi1, xi2):
            if not 0.0 <= xi < 1.0:
                raise ValueError("di_damping ratios xi1/xi2 must be in "
                                 "[0, 1)")
        a0, a1 = rayleigh_from_periods(T1, xi1, T2, xi2)
        if a0 < -1e-15 or a1 < -1e-15:
            raise ValueError(
                f"di_damping: ratios ({xi1} at T1={T1}, {xi2} at T2={T2}) "
                f"give negative Rayleigh coefficients (a0={a0:.4g}, "
                f"a1={a1:.4g}); choose ratios closer together")
        out.update(T1=T1, xi1=xi1, T2=T2, xi2=xi2,
                   a0=max(a0, 0.0), a1=max(a1, 0.0))
    else:
        a0 = _num(dd, "mass_coeff", 0.0)
        a1 = _num(dd, "stiffness_coeff", 0.0)
        if a0 < 0.0 or a1 < 0.0:
            raise ValueError("di_damping coefficients must be >= 0")
        out.update(a0=a0, a1=a1)
    return out


# --------------------------------------------------------------------------- #
# solver
# --------------------------------------------------------------------------- #
def resolve_solver(sv: Optional[dict]) -> dict:
    out = dict(SOLVER_DEFAULTS)
    if sv is None:
        return out
    if not isinstance(sv, dict):
        raise ValueError("solver must be a dict")
    unknown = set(sv) - set(SOLVER_DEFAULTS)
    if unknown:
        raise ValueError(f"solver: unknown keys {sorted(unknown)}")
    if "max_iterations" in sv:
        it = sv["max_iterations"]
        if isinstance(it, bool) or not isinstance(it, int) or it < 1:
            raise ValueError("solver max_iterations must be an int >= 1")
        out["max_iterations"] = int(it)
    if "tolerance" in sv:
        tol = _num(sv, "tolerance")
        if tol <= 0.0:
            raise ValueError("solver tolerance must be > 0")
        out["tolerance"] = tol
    if "test" in sv:
        if sv["test"] not in SOLVER_TESTS:
            raise ValueError(f"solver test must be one of {SOLVER_TESTS}")
        out["test"] = sv["test"]
    if "max_halvings" in sv:
        h = sv["max_halvings"]
        if (isinstance(h, bool) or not isinstance(h, int)
                or not 0 <= h <= MAX_HALVINGS_CAP):
            raise ValueError(f"solver max_halvings must be an int in "
                             f"[0, {MAX_HALVINGS_CAP}]")
        out["max_halvings"] = int(h)
    return out


# --------------------------------------------------------------------------- #
# case-level helpers
# --------------------------------------------------------------------------- #
def has_options(th) -> bool:
    """True when the case uses any direct-integration option."""
    return (getattr(th, "integration", None) is not None
            or getattr(th, "di_damping", None) is not None
            or getattr(th, "solver", None) is not None
            or bool(getattr(th, "energy", False)))


def validate_case(th) -> None:
    """Validate the option fields of a TH case (``ValueError`` on error)."""
    name = getattr(th, "name", "?")
    try:
        resolve_integration(getattr(th, "integration", None))
        resolve_di_damping(getattr(th, "di_damping", None))
        resolve_solver(getattr(th, "solver", None))
    except ValueError as exc:
        raise ValueError(f"TH case {name}: {exc}") from None
    if (getattr(th, "di_damping", None) is not None
            and getattr(th, "damping_model", "rayleigh") == "modal"):
        raise ValueError(f"TH case {name}: di_damping cannot be combined "
                         "with damping_model 'modal'")
    if not isinstance(getattr(th, "energy", False), bool):
        raise ValueError(f"TH case {name}: energy must be a bool")
