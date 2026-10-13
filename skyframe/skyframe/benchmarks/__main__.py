"""``python -m skyframe.benchmarks [--case ID ...] [--report out_dir]``

Runs the benchmark pack (all cases, or the given ids), prints a one-line
summary per case and, with ``--report``, writes ``scorecard.json`` and
``benchmark_report.html`` into ``out_dir``.  Exit status 1 when any value
is rated unacceptable or a case errors.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

from . import case_ids, run_all
from .report import totals, write_html, write_json


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m skyframe.benchmarks",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--case", action="append", choices=case_ids(),
                    help="case id (repeatable); default: all")
    ap.add_argument("--report", metavar="OUT_DIR",
                    help="write scorecard.json + benchmark_report.html")
    a = ap.parse_args(argv)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = run_all(a.case)
    for r in res:
        c = r.counts()
        print(f"{r.id:14s} {r.status:8s} exact {c['exact']:3d}  acceptable "
              f"{c['acceptable']:3d}  unacceptable {c['unacceptable']:3d}  "
              f"{r.seconds:6.1f} s  {r.title}")
    t = totals(res)
    print(f"TOTAL {t['values']} values: {t['exact']} exact, "
          f"{t['acceptable']} acceptable, {t['unacceptable']} unacceptable; "
          f"{t['cases_blocked']} blocked, {t['cases_error']} error")
    if a.report:
        out = Path(a.report)
        out.mkdir(parents=True, exist_ok=True)
        write_json(res, out / "scorecard.json")
        write_html(res, out / "benchmark_report.html")
        print(f"report: {out / 'benchmark_report.html'}")
    return 1 if (t["unacceptable"] or t["cases_error"]) else 0


if __name__ == "__main__":
    sys.exit(main())
