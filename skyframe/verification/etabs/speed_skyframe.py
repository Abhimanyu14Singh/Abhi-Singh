"""Time SkyFrame on a benchmark definition, the linear and nonlinear parts separately, on this machine.

    python speed_skyframe.py --case SP1.case.json --out SP1.skyframe-speed.json

Run by run_etabs.py on the ETABS machine right after ETABS has timed the same model, so both programs
are timed on the same computer.  Each part is built and solved from scratch (model assembly
included, as in ETABS's analysis run) and timed twice; the faster run is reported.
"""

from __future__ import annotations

# OpenSeesPy first: on Windows its DLLs fail to load once numpy / scipy / PARDISO have loaded their own
# MKL ("Failed to import openseespy on Windows")
import openseespy.opensees  # noqa: F401,E402  isort: skip

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parents[1]))


class SolveClock:
    """Seconds spent inside the equation solvers, counted once for nested calls: OpenSees analyze and
    eigen, PARDISO's phases, SciPy's sparse eigen and linear solvers.  Everything else in a run is
    model assembly in Python and writing out results."""

    def __init__(self):
        self.t, self.depth = 0.0, 0

    def wrap(self, owner, name):
        f = getattr(owner, name, None)
        if f is None or getattr(f, "_solve_clock", False):
            return

        def timed(*a, **k):
            self.depth += 1
            t0 = time.perf_counter()
            try:
                return f(*a, **k)
            finally:
                self.depth -= 1
                if self.depth == 0:
                    self.t += time.perf_counter() - t0
        timed._solve_clock = True
        setattr(owner, name, timed)

    def install(self):
        import openseespy.opensees as ops
        for n in ("analyze", "eigen"):
            self.wrap(ops, n)
        import scipy.sparse.linalg as sla
        for n in ("eigsh", "spsolve", "splu", "factorized", "lobpcg"):
            self.wrap(sla, n)
        try:
            from skyframe.engine import pardiso_solver as pdx
            self.wrap(pdx.PardisoFactor, "_call")
        except Exception:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeat", type=int, default=3)
    a = ap.parse_args(argv)
    case = json.loads(Path(a.case).read_text(encoding="utf-8"))
    clock = SolveClock()
    clock.install()                             # before SkyFrame imports bind the solver functions
    import skyframe
    clock.install()                             # PARDISO's class exists only once SkyFrame is imported
    import skyframe_side as S
    from skyframe.engine.opensees_engine import OpenSeesEngine
    try:
        import pypardiso  # noqa: F401
        solver = "PARDISO (pypardiso) where SkyFrame uses it, OpenSees otherwise"
    except ImportError:
        solver = "OpenSees default (pypardiso not installed)"
    out = {"program": "SkyFrame", "version": skyframe.__version__, "case": case["id"],
           "python": sys.version.split()[0], "cores": os.cpu_count(),
           "machine": platform.processor() or platform.machine(), "solver": solver}
    for part in ("linear", "nonlinear"):
        names = case["benchmark"].get(part) or []
        if not names:
            continue                            # linear-only benchmarks (SP4, SP5)
        runs, full, solve = [], [], []
        for _ in range(max(1, a.repeat)):
            m = S.build(case, only=set(names))
            clock.t = 0.0
            t0 = time.perf_counter()
            r = OpenSeesEngine(m).run()
            t1 = time.perf_counter()
            solve.append(clock.t)
            r.to_dict()                         # every result written out, as ETABS saves its results
            t2 = time.perf_counter()
            runs.append(t1 - t0)
            full.append(t2 - t0)
        k = min(range(len(full)), key=full.__getitem__)       # the best full run, and its solve share
        out[f"{part}_solve_s"] = solve[k]
        out[f"{part}_solve_runs_s"] = solve
        out[f"{part}_s"] = min(runs)
        out[f"{part}_runs_s"] = runs
        out[f"{part}_with_results_s"] = min(full)
        out[f"{part}_with_results_runs_s"] = full
        print(f"SkyFrame {part} run: best {min(runs):.2f} s of {', '.join(f'{t:.2f}' for t in runs)}; "
              f"with every result written out {min(full):.2f} s, of which inside the solvers {solve[k]:.2f} s "
              f"({', '.join(names)})", flush=True)
    # the same model solved once more, untimed, for its answers: a fast run must also be a right one
    import compare as CMP
    res = S.run(case)
    out["values"] = {chk["id"]: CMP.pick(res, chk) for chk in case["checks"]}
    print("SkyFrame answers on this PC: " + ", ".join(f"{k} {v:.6g}" for k, v in out["values"].items()
                                                     if v is not None), flush=True)
    Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
