"""Shared nonlinear convergence strategy (fallback ladder + step halving).

CONTRACT "Analysis performance, solver policy and convergence strategy".

Every nonlinear analysis path (nonlinear static cases with axial-only
members / gap links / compression-only springs, P-Delta / corotational
static stages, pushover gravity + push steps, nonlinear static cases,
nonlinear time history with or without the direct-integration options, and
the staged sub-model statics that reuse the static path) advances one step
through :func:`static_step` / :func:`transient_step`.

``analysis_options.convergence.mode``:

* ``"legacy"`` (default) — EXACTLY the historical per-path logic, op for
  op: one ``analyze``; for the paths that had a retry
  (``legacy_retry=True``) a ``NewtonLineSearch`` retry followed by
  ``algorithm Newton``; no test change, no halving.  Results are
  bit-identical to the pre-feature code.
* ``"robust"`` (opt-in) — on failure of the first (unchanged) attempt the
  ladder runs: for each test of ``tests`` (an entry without
  tolerance / max_iterations inherits the calling path's own values) every
  algorithm of ``algorithms`` (default Newton -> ModifiedNewton ->
  KrylovNewton -> NewtonLineSearch -> BFGS) is tried; when the whole ladder
  fails and the caller supplied its integrator (static) / time step
  (transient) the step is split in two halves, each solved recursively with
  the full ladder, up to ``max_halvings`` levels (2^levels substeps).
  OpenSees reverts the domain to the last committed state after a failed
  ``analyze``, so a failed attempt never contaminates the next one.  After
  every step the calling path's own test and base algorithm are restored.

Every step that needed anything beyond the first attempt is appended to
the caller's ``log`` as ``{"step", "algorithm", "test", "halvings"}``
(``algorithm``/``test`` of the attempt that finally converged; for a
halved step, of its last substep) — or ``{"step", "failed": True}``.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple

import openseespy.opensees as ops

from skyframe.core.analysis_options import convergence as _conv_opts

Test = Tuple[str, float, int]


class Strategy:
    """Resolved per-analysis strategy (options + the path's own test)."""

    def __init__(self, model, base_test: Optional[Test] = None,
                 base_algorithm: str = "Newton",
                 legacy_retry: bool = True,
                 log: Optional[list] = None, label: str = ""):
        opts = _conv_opts(model)
        self.robust = opts.get("mode") == "robust"
        self.base_test = base_test
        self.base_algorithm = base_algorithm
        self.legacy_retry = legacy_retry
        self.algorithms = list(opts.get("algorithms") or ["Newton"])
        self.tests = [self._resolve_test(t) for t in opts.get("tests") or []]
        self.max_halvings = int(opts.get("max_halvings", 0))
        self.log = log if log is not None else []
        self.label = label
        self.step_no = 0

    # ---------------------------------------------------------------- utils
    def _resolve_test(self, t: dict) -> Optional[Test]:
        typ = t.get("type", "NormDispIncr")
        bt = self.base_test
        tol = t.get("tolerance")
        it = t.get("max_iterations")
        if bt is not None:
            tol = bt[1] if tol is None else tol
            it = bt[2] if it is None else it
        if tol is None or it is None:
            if bt is None and t.get("tolerance") is None:
                return None              # "the path's own" (unknown)
            tol = 1e-8 if tol is None else tol
            it = 25 if it is None else it
        return (typ, float(tol), int(it))

    # ------------------------------------------------------------ attempts
    def _ladder(self, attempt: Callable[[], int]) -> Tuple[int, str, str]:
        """One step through the algorithm x test ladder (robust mode).
        The first attempt is the path's unchanged setup."""
        ok = attempt()
        if ok == 0:
            return 0, self.base_algorithm, self._test_name(self.base_test)
        tests: List[Optional[Test]] = self.tests or [self.base_test]
        first = True
        for test in tests:
            if test is not None:
                ops.test(test[0], test[1], test[2])
            for alg in self.algorithms:
                if first and alg == self.base_algorithm and (
                        test is None or test == self.base_test):
                    first = False
                    continue             # == the attempt already made
                first = False
                ops.algorithm(alg)
                ok = attempt()
                if ok == 0:
                    self._restore()
                    return 0, alg, self._test_name(test)
        self._restore()
        return ok, "", ""

    def _restore(self) -> None:
        if self.base_test is not None:
            ops.test(*self.base_test)
        ops.algorithm(self.base_algorithm)

    @staticmethod
    def _test_name(test) -> str:
        return test[0] if test is not None else "default"

    def _legacy(self, attempt: Callable[[], int]) -> int:
        ok = attempt()
        if ok != 0 and self.legacy_retry:
            ops.algorithm("NewtonLineSearch")
            ok = attempt()
            ops.algorithm("Newton")
            if ok == 0:
                self.log.append({"step": self.step_no,
                                 "algorithm": "NewtonLineSearch",
                                 "test": self._test_name(self.base_test),
                                 "halvings": 0})
        return ok

    # --------------------------------------------------------------- steps
    def static(self, integrator: Optional[Sequence] = None) -> int:
        """One static step.  ``integrator``: the path's integrator args
        (e.g. ``("LoadControl", 0.1)``) — enables step halving."""
        self.step_no += 1
        if not self.robust:
            return self._legacy(lambda: ops.analyze(1))
        ok, alg, tst, lvl = self._static_rec(integrator, 0)
        self._record(ok, alg, tst, lvl)
        return ok

    def _static_rec(self, integ, level: int):
        ok, alg, tst = self._ladder(lambda: ops.analyze(1))
        if ok == 0:
            return 0, alg, tst, level
        if integ is None or level >= self.max_halvings:
            return ok, "", "", level
        half = list(integ)
        half[-1] = float(integ[-1]) / 2.0
        ops.integrator(*half)
        deepest = level + 1
        try:
            for _ in range(2):
                ok, alg, tst, lv = self._static_rec(half, level + 1)
                deepest = max(deepest, lv)
                if ok != 0:
                    return ok, "", "", deepest
        finally:
            ops.integrator(*integ)
        return 0, alg, tst, deepest

    def transient(self, dt: float) -> int:
        """One transient step of size ``dt`` (halving splits the step)."""
        self.step_no += 1
        if not self.robust:
            return self._legacy(lambda: ops.analyze(1, dt))
        ok, alg, tst, lvl = self._transient_rec(dt, 0)
        self._record(ok, alg, tst, lvl)
        return ok

    def _transient_rec(self, h: float, level: int):
        ok, alg, tst = self._ladder(lambda: ops.analyze(1, h))
        if ok == 0:
            return 0, alg, tst, level
        if level >= self.max_halvings:
            return ok, "", "", level
        deepest = level + 1
        for _ in range(2):
            ok, alg, tst, lv = self._transient_rec(h / 2.0, level + 1)
            deepest = max(deepest, lv)
            if ok != 0:
                return ok, "", "", deepest
        return 0, alg, tst, deepest

    def attempt(self, fn: Callable[[], int]) -> int:
        """Ladder only (no halving) around an arbitrary analyze callable —
        for paths that own their substepping (direct-integration options,
        nonlinear static cases)."""
        self.step_no += 1
        if not self.robust:
            return self._legacy(fn)
        ok, alg, tst = self._ladder(fn)
        self._record(ok, alg, tst, 0)
        return ok

    def _record(self, ok: int, alg: str, tst: str, lvl: int) -> None:
        if ok != 0:
            self.log.append({"step": self.step_no, "failed": True})
            return
        if (alg != self.base_algorithm or lvl > 0
                or tst != self._test_name(self.base_test)):
            self.log.append({"step": self.step_no, "algorithm": alg,
                             "test": tst, "halvings": lvl})

    def report(self) -> dict:
        """JSON-safe summary of this strategy's fallbacks."""
        by_alg: dict = {}
        halved = 0
        failed = 0
        for e in self.log:
            if e.get("failed"):
                failed += 1
                continue
            by_alg[e["algorithm"]] = by_alg.get(e["algorithm"], 0) + 1
            if e["halvings"]:
                halved += 1
        return {"mode": "robust" if self.robust else "legacy",
                "steps": self.step_no,
                "fallback_steps": len(self.log) - failed,
                "failed_steps": failed,
                "halved_steps": halved,
                "by_algorithm": by_alg,
                "log": list(self.log)}


# --------------------------------------------------------------------- API
def static_step(model, analyze=None, *, label: str = "",
                legacy_retry: bool = True,
                base_test: Optional[Test] = None,
                integrator: Optional[Sequence] = None,
                engine=None) -> int:
    """One-shot static step (a fresh :class:`Strategy`; its report is
    filed on ``engine`` under ``label`` when robust).  ``analyze`` is
    ignored (kept for call-site readability: the step is
    ``ops.analyze(1)``)."""
    st = Strategy(model, base_test=base_test, legacy_retry=legacy_retry,
                  label=label)
    ok = st.static(integrator)
    if engine is not None:
        file_report(engine, label, st)
    return ok


def _merge(prev: Optional[dict], rep: dict) -> dict:
    if prev is None:
        return rep
    return {"mode": rep["mode"],
            "steps": prev["steps"] + rep["steps"],
            "fallback_steps": prev["fallback_steps"] + rep["fallback_steps"],
            "failed_steps": prev["failed_steps"] + rep["failed_steps"],
            "halved_steps": prev["halved_steps"] + rep["halved_steps"],
            "by_algorithm": {k: prev["by_algorithm"].get(k, 0)
                             + rep["by_algorithm"].get(k, 0)
                             for k in sorted(set(prev["by_algorithm"])
                                             | set(rep["by_algorithm"]))},
            "log": prev["log"] + rep["log"]}


def file_report(engine, label: str, strategy: "Strategy") -> None:
    """Keep a robust strategy's report on the engine (results
    ``convergence`` block, several strategies of one case merged in
    order); legacy mode reports nothing (byte-identical results)."""
    if not strategy.robust or not label:
        return
    reps = getattr(engine, "_convergence_reports", None)
    if reps is None:
        reps = engine._convergence_reports = {}
    reps[label] = _merge(reps.get(label), strategy.report())


def adopt_child(engine, label: str, child) -> None:
    """Fold the reports of a sub-engine (staged construction stage
    models) into ``engine`` under ``label``."""
    for rep in (getattr(child, "_convergence_reports", None) or {}).values():
        reps = getattr(engine, "_convergence_reports", None)
        if reps is None:
            reps = engine._convergence_reports = {}
        reps[label] = _merge(reps.get(label), rep)


def reports(engine) -> dict:
    """``{case label: report}`` of an engine (robust mode only)."""
    return dict(getattr(engine, "_convergence_reports", None) or {})
