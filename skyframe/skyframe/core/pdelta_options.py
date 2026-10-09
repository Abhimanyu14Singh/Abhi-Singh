"""Model-wide P-Delta options (ETABS Define > P-Delta Options).

``BuildingModel.pdelta_options`` is a plain dict::

    {"method": "none" | "non_iterative_mass" | "iterative_loads",
     "load_factors": {pattern: factor},   # iterative_loads gravity combo
     "max_iterations": 2,                 # iterative_loads, int 1..100
     "tolerance": 0.001,                  # iterative_loads, relative, > 0
     "include_in": "all_linear"}

``"none"`` (default) is the exact pre-existing behaviour (per-case
``LoadCase.pdelta`` / ``geometric`` flags only).  The engine side lives in
:mod:`skyframe.engine.pdelta`.  This module only normalizes, validates and
(de)serializes; ``to_dict`` emits the key ONLY when the options differ from
the defaults, so default models serialize byte-identically.
"""

from __future__ import annotations

import math
from typing import Dict

PDELTA_METHODS = ("none", "non_iterative_mass", "iterative_loads")
PDELTA_INCLUDE_IN = ("all_linear",)
PDELTA_MAX_ITER_CAP = 100
PDELTA_KEYS = ("method", "load_factors", "max_iterations", "tolerance",
               "include_in")


def pdelta_defaults() -> dict:
    """A fresh copy of the default options (method "none")."""
    return {"method": "none", "load_factors": {}, "max_iterations": 2,
            "tolerance": 0.001, "include_in": "all_linear"}


def normalized(opts) -> dict:
    """Defaults overlaid with ``opts`` (a missing/None value -> defaults).
    No validation (see :func:`validate_pdelta_options`)."""
    out = pdelta_defaults()
    if isinstance(opts, dict):
        out.update(opts)
    if isinstance(out.get("load_factors"), dict):
        out["load_factors"] = dict(out["load_factors"])
    return out


def effective_method(model) -> str:
    """The active method; anything but a valid non-"none" method -> "none"."""
    opts = getattr(model, "pdelta_options", None)
    if not isinstance(opts, dict):
        return "none"
    m = opts.get("method", "none")
    return m if m in PDELTA_METHODS else "none"


def validate_pdelta_options(model) -> None:
    """Raise ``ValueError`` on malformed ``model.pdelta_options``."""
    opts = getattr(model, "pdelta_options", None)
    if opts is None:
        return
    if not isinstance(opts, dict):
        raise ValueError("pdelta_options must be an object")
    bad = sorted(set(opts) - set(PDELTA_KEYS))
    if bad:
        raise ValueError(f"pdelta_options: unknown keys {bad} "
                         f"(allowed: {list(PDELTA_KEYS)})")
    o = normalized(opts)
    if o["method"] not in PDELTA_METHODS:
        raise ValueError(f"pdelta_options.method must be one of "
                         f"{PDELTA_METHODS}, got {o['method']!r}")
    if o["include_in"] not in PDELTA_INCLUDE_IN:
        raise ValueError(f"pdelta_options.include_in must be one of "
                         f"{PDELTA_INCLUDE_IN}, got {o['include_in']!r}")
    lf = o["load_factors"]
    if not isinstance(lf, dict):
        raise ValueError("pdelta_options.load_factors must be an object "
                         "{pattern: factor}")
    for p, f in lf.items():
        if p not in model.patterns:
            raise ValueError(f"pdelta_options.load_factors: unknown load "
                             f"pattern {p!r}")
        if (isinstance(f, bool) or not isinstance(f, (int, float))
                or not math.isfinite(float(f))):
            raise ValueError(f"pdelta_options.load_factors[{p!r}] must be "
                             f"a finite number (got {f!r})")
    mi = o["max_iterations"]
    if (isinstance(mi, bool) or not isinstance(mi, int)
            or not 1 <= mi <= PDELTA_MAX_ITER_CAP):
        raise ValueError(f"pdelta_options.max_iterations must be an integer "
                         f"in 1..{PDELTA_MAX_ITER_CAP} (got {mi!r})")
    tol = o["tolerance"]
    if (isinstance(tol, bool) or not isinstance(tol, (int, float))
            or not math.isfinite(float(tol)) or float(tol) <= 0.0):
        raise ValueError(f"pdelta_options.tolerance must be a finite number "
                         f"> 0 (got {tol!r})")
    if o["method"] == "iterative_loads":
        if not lf or not any(float(f) != 0.0 for f in lf.values()):
            raise ValueError("pdelta_options: method 'iterative_loads' needs "
                             "a non-empty load_factors gravity combination")


def pdelta_to_dict(model) -> Dict[str, dict]:
    """Model-dict entry; emitted ONLY when the options are not the defaults
    (default models serialize byte-identically)."""
    opts = getattr(model, "pdelta_options", None)
    o = normalized(opts)
    if o == pdelta_defaults():
        return {}
    return {"pdelta_options": {
        "method": o["method"],
        "load_factors": {k: float(v) for k, v in o["load_factors"].items()},
        "max_iterations": o["max_iterations"],
        "tolerance": float(o["tolerance"]),
        "include_in": o["include_in"]}}


def pdelta_from_dict(model, d: dict) -> None:
    """Populate ``model.pdelta_options`` (absent -> defaults).  Validation
    happens in ``BuildingModel.validate``."""
    po = d.get("pdelta_options")
    if po is None:
        model.pdelta_options = pdelta_defaults()
        return
    if not isinstance(po, dict):
        raise ValueError("pdelta_options must be an object")
    o = normalized(po)
    if isinstance(o.get("max_iterations"), float) \
            and float(o["max_iterations"]).is_integer():
        o["max_iterations"] = int(o["max_iterations"])
    model.pdelta_options = o
