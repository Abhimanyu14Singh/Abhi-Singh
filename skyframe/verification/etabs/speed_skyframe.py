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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeat", type=int, default=2)
    a = ap.parse_args(argv)
    case = json.loads(Path(a.case).read_text(encoding="utf-8"))
    import skyframe
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
        names = case["benchmark"][part]
        best = None
        for _ in range(max(1, a.repeat)):
            m = S.build(case, only=set(names))
            t0 = time.perf_counter()
            OpenSeesEngine(m).run()
            dt = time.perf_counter() - t0
            best = dt if best is None else min(best, dt)
        out[f"{part}_s"] = best
        print(f"SkyFrame {part} run: {best:.2f} s ({', '.join(names)})", flush=True)
    Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
