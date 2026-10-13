"""Model-wide analysis options: solver policy, nonlinear convergence
strategy, model-size warnings.

CONTRACT "Analysis performance, solver policy and convergence strategy".

``BuildingModel.analysis_options`` is a plain dict (empty = every default)::

    {"solver": "auto" | "band" | "umfpack" | "mumps" | "sparse_general"
               | "pardiso",
     "pardiso_threads": "auto" | 1..256,
     "eigen_solver": "auto" | "legacy" | "lanczos",
     "convergence": {
         "mode": "legacy" | "robust",
         "algorithms": ["Newton", "ModifiedNewton", "KrylovNewton",
                        "NewtonLineSearch", "BFGS"],
         "tests": [{"type": "NormDispIncr"}, {"type": "EnergyIncr",
                    "tolerance": 1e-10, "max_iterations": 100}],
         "max_halvings": 4},
     "size_warnings": true,
     "parallel": "auto" | "off" | 1..8,
     "parallel_start": "auto" | "fork" | "spawn"}

* ``solver`` — see :mod:`skyframe.engine.solver_policy` ("auto" keeps the
  legacy BandGeneral path, bit-identical, below the size threshold).
* ``pardiso_threads`` (E2) — MKL threads of the PARDISO solver
  (:mod:`skyframe.engine.pardiso_solver`): "auto" (default) = every
  physical core (``MKL_NUM_THREADS`` in the environment wins), an int sets
  the count.  Only read when PARDISO runs.
* ``eigen_solver`` — "auto" lets large (sparse-policy) models use the exact
  massed-DOF condensation of :mod:`skyframe.engine.modal_condensed` for the
  MODAL case and (v1.24) large models with more massed DOFs than the
  condensation takes the sparse shift-invert Lanczos of
  :mod:`skyframe.engine.sparse_modal`; "legacy" always runs OpenSees'
  eigen solver; "lanczos" forces the Lanczos solver on any model.
* ``convergence`` — see :mod:`skyframe.engine.convergence`.  ``"legacy"``
  (default) reproduces every analysis path's historical retry logic
  exactly; ``"robust"`` (opt-in) runs the full fallback ladder (algorithms
  x tests, then step halving up to ``max_halvings`` levels) and reports
  which steps needed which fallback.  A test entry without ``tolerance`` /
  ``max_iterations`` inherits the calling analysis path's own values.
* ``parallel`` — process-parallel independent solves on large (sparse
  policy) models, see :mod:`skyframe.engine.parallel`: "auto" (default)
  uses up to 8 processes when at least 4 cores are available, "off" (or 1)
  is serial, an int sets the number of processes.  Results are
  byte-identical either way.
* ``parallel_start`` (E1) — how the parallel workers start: "auto"
  (default) forks where the OS can (Linux) and uses the persistent spawn
  worker pool elsewhere (Windows); "spawn" forces the pool (A/B on Linux);
  "fork" asks for fork (spawn where fork does not exist).  See
  :mod:`skyframe.engine.spawn_pool`.
* ``size_warnings`` — warn (results ``warning`` + ``model_size`` block) when
  the model exceeds sizes that will be slow.

``to_dict`` emits the key ONLY when the options differ from the defaults,
so default models serialize byte-identically.
"""

from __future__ import annotations

import copy
import math
from typing import Dict

SOLVERS = ("auto", "band", "umfpack", "mumps", "sparse_general",
           "pardiso")                                  # E2: + pardiso
CONVERGENCE_MODES = ("legacy", "robust")
ALGORITHMS = ("Newton", "ModifiedNewton", "KrylovNewton",
              "NewtonLineSearch", "BFGS")
TESTS = ("NormDispIncr", "EnergyIncr", "NormUnbalance")
MAX_HALVINGS_CAP = 12
KEYS = ("solver", "eigen_solver", "convergence", "size_warnings",
        "parallel", "parallel_start", "pardiso_threads")   # E1 + E2
MAX_PARDISO_THREADS = 256
PARALLEL_MODES = ("auto", "off")
MAX_PARALLEL = 8
PARALLEL_STARTS = ("auto", "fork", "spawn")      # E1
EIGEN_SOLVERS = ("auto", "legacy", "lanczos")   # v1.24 + lanczos
CONV_KEYS = ("mode", "algorithms", "tests", "max_halvings")
TEST_KEYS = ("type", "tolerance", "max_iterations")


def convergence_defaults() -> dict:
    """A fresh copy of the default convergence options (mode "legacy";
    the robust ladder below only runs in mode "robust")."""
    return {"mode": "legacy",
            "algorithms": list(ALGORITHMS),
            "tests": [{"type": "NormDispIncr"},
                      {"type": "EnergyIncr", "tolerance": 1e-10,
                       "max_iterations": 100}],
            "max_halvings": 4}


def defaults() -> dict:
    return {"solver": "auto", "eigen_solver": "auto",
            "convergence": convergence_defaults(), "size_warnings": True,
            "parallel": "auto", "parallel_start": "auto", "pardiso_threads": "auto"}


def normalized(model_or_opts) -> dict:
    """Defaults overlaid with the model's options (no validation)."""
    opts = model_or_opts
    if not isinstance(opts, dict):
        opts = getattr(model_or_opts, "analysis_options", None)
    out = defaults()
    if isinstance(opts, dict):
        for k in ("solver", "eigen_solver", "size_warnings", "parallel",
                  "parallel_start", "pardiso_threads"):
            if k in opts:
                out[k] = opts[k]
        conv = opts.get("convergence")
        if isinstance(conv, dict):
            out["convergence"].update(copy.deepcopy(conv))
    return out


def convergence(model) -> dict:
    """The effective convergence options of a model."""
    return normalized(model)["convergence"]


def is_robust(model) -> bool:
    return convergence(model).get("mode") == "robust"


def validate_analysis_options(model) -> None:
    """Raise ``ValueError`` on malformed ``model.analysis_options``."""
    opts = getattr(model, "analysis_options", None)
    if opts is None:
        return
    if not isinstance(opts, dict):
        raise ValueError("analysis_options must be an object")
    bad = set(opts) - set(KEYS)
    if bad:
        raise ValueError(f"analysis_options: unknown keys {sorted(bad)} "
                         f"(allowed {list(KEYS)})")
    if "solver" in opts and opts["solver"] not in SOLVERS:
        raise ValueError(f"analysis_options.solver must be one of "
                         f"{list(SOLVERS)} (got {opts['solver']!r})")
    if "eigen_solver" in opts and opts["eigen_solver"] not in EIGEN_SOLVERS:
        raise ValueError(f"analysis_options.eigen_solver must be one of "
                         f"{list(EIGEN_SOLVERS)} (got "
                         f"{opts['eigen_solver']!r})")
    if "size_warnings" in opts and not isinstance(opts["size_warnings"],
                                                  bool):
        raise ValueError("analysis_options.size_warnings must be a bool")
    if "parallel" in opts:
        _validate_parallel(opts["parallel"])
    if "parallel_start" in opts and (                       # E1
            not isinstance(opts["parallel_start"], str)
            or opts["parallel_start"] not in PARALLEL_STARTS):
        raise ValueError(f"analysis_options.parallel_start must be one of "
                         f"{list(PARALLEL_STARTS)} (got "
                         f"{opts['parallel_start']!r})")
    if "pardiso_threads" in opts:                       # E2
        _validate_pardiso_threads(opts["pardiso_threads"])
    conv = opts.get("convergence")
    if conv is None:
        return
    if not isinstance(conv, dict):
        raise ValueError("analysis_options.convergence must be an object")
    bad = set(conv) - set(CONV_KEYS)
    if bad:
        raise ValueError(f"analysis_options.convergence: unknown keys "
                         f"{sorted(bad)} (allowed {list(CONV_KEYS)})")
    if "mode" in conv and conv["mode"] not in CONVERGENCE_MODES:
        raise ValueError(f"analysis_options.convergence.mode must be one "
                         f"of {list(CONVERGENCE_MODES)} (got "
                         f"{conv['mode']!r})")
    if "algorithms" in conv:
        algs = conv["algorithms"]
        if (not isinstance(algs, list) or not algs
                or any(a not in ALGORITHMS for a in algs)):
            raise ValueError(f"analysis_options.convergence.algorithms must "
                             f"be a non-empty list of {list(ALGORITHMS)}")
    if "tests" in conv:
        tests = conv["tests"]
        if not isinstance(tests, list) or not tests:
            raise ValueError("analysis_options.convergence.tests must be a "
                             "non-empty list")
        for t in tests:
            _validate_test(t)
    if "max_halvings" in conv:
        mh = conv["max_halvings"]
        if (isinstance(mh, bool) or not isinstance(mh, int)
                or not 0 <= mh <= MAX_HALVINGS_CAP):
            raise ValueError(f"analysis_options.convergence.max_halvings "
                             f"must be an int 0..{MAX_HALVINGS_CAP}")


def _validate_parallel(v) -> None:
    if v in PARALLEL_MODES and isinstance(v, str):
        return
    if (isinstance(v, bool) or not isinstance(v, int)
            or not 1 <= v <= MAX_PARALLEL):
        raise ValueError(f"analysis_options.parallel must be one of "
                         f"{list(PARALLEL_MODES)} or an int "
                         f"1..{MAX_PARALLEL} (got {v!r})")


def _validate_pardiso_threads(v) -> None:
    if v == "auto":
        return
    if (isinstance(v, bool) or not isinstance(v, int)
            or not 1 <= v <= MAX_PARDISO_THREADS):
        raise ValueError(f"analysis_options.pardiso_threads must be "
                         f"\"auto\" or an int 1..{MAX_PARDISO_THREADS} "
                         f"(got {v!r})")


def _validate_test(t) -> None:
    if not isinstance(t, dict):
        raise ValueError("analysis_options.convergence.tests entries must "
                         "be objects")
    bad = set(t) - set(TEST_KEYS)
    if bad:
        raise ValueError(f"convergence test: unknown keys {sorted(bad)}")
    if t.get("type") not in TESTS:
        raise ValueError(f"convergence test type must be one of "
                         f"{list(TESTS)} (got {t.get('type')!r})")
    tol = t.get("tolerance")
    if tol is not None and (isinstance(tol, bool)
                            or not isinstance(tol, (int, float))
                            or not math.isfinite(tol) or tol <= 0.0):
        raise ValueError("convergence test tolerance must be a finite "
                         "value > 0")
    it = t.get("max_iterations")
    if it is not None and (isinstance(it, bool) or not isinstance(it, int)
                           or not 1 <= it <= 10000):
        raise ValueError("convergence test max_iterations must be an int "
                         "1..10000")


def analysis_options_to_dict(model) -> Dict[str, dict]:
    """``{"analysis_options": {...}}`` when set and not the defaults, else
    ``{}`` (default models serialize byte-identically)."""
    opts = getattr(model, "analysis_options", None)
    if not isinstance(opts, dict) or not opts:
        return {}
    if normalized(opts) == defaults():
        return {}
    return {"analysis_options": copy.deepcopy(opts)}


def analysis_options_from_dict(d: dict) -> dict:
    """The stored options of a model dict ({} when absent)."""
    v = d.get("analysis_options")
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError("analysis_options must be an object")
    return copy.deepcopy(v)

