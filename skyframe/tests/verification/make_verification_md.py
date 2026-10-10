"""Regenerate ``docs/VERIFICATION.md`` from the verification suite.

Usage (from the ``skyframe/`` package root)::

    python3 tests/verification/make_verification_md.py

Runs ``pytest tests/verification`` with ``SKYFRAME_VERIF_JSON`` set, so
every ``_vhelp.check`` comparison is dumped to JSON, then renders the
summary table (problem, source, quantity, reference, SkyFrame, % error,
status).  The table is therefore always the output of the tests as they
ran -- never hand-edited numbers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                      # .../skyframe (package root)
OUT = ROOT / "docs" / "VERIFICATION.md"

FAMILY_FILES = OrderedDict([
    ("Frames", "test_verif_frames.py"),
    ("Shells", "test_verif_shells.py"),
    ("Dynamics", "test_verif_dynamics.py"),
    ("Nonlinear", "test_verif_nonlinear.py"),
])

BUGS = """\
## Engine defects found and fixed by this suite

| # | Defect (root cause) | Problem that exposed it | Before | After |
|---|---|---|---|---|
| 1 | Elastic-perfectly-plastic hinges (`hardening = 0`) used Steel01 with post-yield ratio `b = 0`: a node whose only rotational stiffness comes from two yielded hinge springs (column top + beam end, or the two beam halves at a mid-span load) -- and the full collapse mechanism -- made the tangent `K` singular, so `DisplacementControl` failed and the pushover stopped **before** the plastic collapse load. Fix: in displacement-controlled pushovers floor `b` at `HINGE_MIN_STEEL01_B = 1e-9` (`engine/opensees_engine.py`; a load-controlled step past collapse must still fail, a TH is regularised by its mass); every `b >= 1e-9` (all practical `h`, incl. the 0.02 default) is bit-identical. | EPP portal pushover, sway mechanism `4Mp/h = 200 kN` | stopped at step 44/150, `V = 198.387 kN` (-0.81 %), warning | `200.0000115 kN` (+5.7e-8), full 150-step curve |
| 1b | (same root cause) | EPP portal, combined mechanism `3Mp/h = 150 kN` | stopped at step 16/150, `V = 110.129 kN` (-26.6 %) | `150.0000274 kN` (+1.8e-7) |
| 2 | Model-wide P-Delta `non_iterative_mass` (ETABS "based on mass") without a diaphragm shares the story weight `P` over the story's vertical members by `EA/L` **as if every member were a parallel column**: a column modelled as several stacked members (split at mid-height, at brace points, or meshed by hand) gave each piece only its `EA/L` share (~`P/n`), so the P-Delta effect almost vanished. Fix (`engine/pdelta.py::_series_column_lines`): members connected end-to-end inside the story form ONE series column line (`1/sum(L/EA)`), every piece carries the line's full axial force; single-member lines keep their exact weights (bit-identical). Also fixes the named-diaphragm path that reuses `_column_strings`. | CSI P-Delta cantilevered column, 10 stacked members, `P = 0.7 Pcr` | tip `0.06445 m` (amplification 1.07; reference 0.19710 m, -67.3 %) | `0.1970983 m` (= independent string-stiffness numpy to 1e-9; -0.48 % vs exact beam-column) |

## Deviations investigated and kept (documented, not defects)

* **P-Delta = chord ("string") geometric stiffness.**  The CSI cantilevered-
  column problem converges to the exact beam-column value only with
  meshing (2/4/10 segments: -10.3 % / -2.9 % / -0.48 %).  An independent
  numpy assembly of the same formulation reproduces SkyFrame to 1e-6, so the
  residual is the formulation (no P-small-delta member term), not a bug.
* **Shells are flat MITC4 facets.**  Scordelis-Lo, pinched cylinder /
  hemisphere and Morley skew plate converge monotonically from below at the
  published MITC4 rates (h^1.5 for the roof, slower for the singular
  problems); the finest-mesh errors (-0.6 % ... -1.2 %) are discretisation,
  shown by the mesh sequences in the notes.  Walls with openings converge
  at ~h^1.1 (re-entrant-corner singularity) -- the Richardson-extrapolated
  SkyFrame continuum value matches the independent Q8 solution to 0.3 %.
* **Undamped hinge springs in nonlinear TH.**  The zeroLength hinge
  springs carry no Rayleigh term (OpenSees default; the Zareian & Medina
  2010 practice), so a hinged model's stiffness-proportional damping acts on
  the elastic members only.  The suite pins this law EXACTLY against an
  independent 2-DOF integrator; versus a classical-Rayleigh SDOF it changes
  the end-of-record displacement by +0.78 %.  Damping the springs with the
  committed tangent (`-doRayleigh 1`) was tried and reproduces classical
  Rayleigh exactly, but breaks the exact energy balance of the TH energy
  tracker at yield events (`tests/test_th_options.py`), so the documented
  behaviour was kept.
"""

POLICY = """\
## Tolerance policy

* Quantities that the element formulation reproduces EXACTLY (Euler-Bernoulli
  frames with exact fixed-end forces, condensed shear-building modes, hand
  statics, link laws) are asserted at 1e-6..1e-8 relative -- any larger
  deviation is a bug, not a modelling approximation.
* Discretisation-dependent results (shells, string-stiffness P-Delta,
  corotational large displacement, consistent-Kg buckling, Newmark time
  integration) are asserted as a CONVERGENCE STUDY: monotone convergence
  plus a finest-mesh tolerance that follows from the measured / theoretical
  convergence rate (e.g. Newmark period error (pi^2/12)(dt/T)^2).  The
  mesh sequence is in the "note" column.
* No tolerance was widened to hide an error: every deviation larger than the
  element formulation justifies was traced to a root cause and fixed (see
  above) before the reference was accepted.
"""


def run_suite() -> list:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "verif.json"
        env = dict(os.environ, SKYFRAME_VERIF_JSON=str(out),
                   OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
                   MKL_NUM_THREADS="1")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(HERE), "-q",
             "-p", "no:cacheprovider"], cwd=str(ROOT), env=env,
            capture_output=True, text=True)
        tail = proc.stdout.strip().splitlines()[-1:] or [""]
        print(tail[0])
        if not out.exists():
            print(proc.stdout[-4000:], proc.stderr[-4000:])
            raise SystemExit("verification suite produced no records")
        return json.loads(out.read_text()), tail[0]


def fmt(v: float) -> str:
    if v == 0.0:
        return "0"
    a = abs(v)
    if 1e-3 <= a < 1e5:
        return f"{v:.6g}"
    return f"{v:.5e}"


def render(records: list, summary: str) -> str:
    lines = ["# SkyFrame independent verification",
             "",
             "Generated by `tests/verification/make_verification_md.py` from "
             "the assertions of `tests/verification/` (do not edit by hand).",
             "Problems are modelled on the CSI *SAP2000 / ETABS Software "
             "Verification* manuals and the classic NAFEMS / MacNeal-Harder / "
             "textbook benchmarks; each test docstring cites its source and "
             "derives the reference.",
             "",
             f"Last run: `{summary}`",
             ""]
    problems = OrderedDict()
    for r in records:
        problems.setdefault(r["problem"], r["family"])
    n_fail = sum(r["status"] != "PASS" for r in records)
    lines.append(f"**{len(problems)} problems, {len(records)} compared "
                 f"quantities, {n_fail} failing.**")
    lines.append("")
    lines.append(BUGS)
    lines.append(POLICY)
    for fam in FAMILY_FILES:
        rows = [r for r in records if r["family"] == fam]
        if not rows:
            continue
        lines.append(f"## {fam} (`tests/verification/{FAMILY_FILES[fam]}`)")
        lines.append("")
        lines.append("| Problem | Source | Quantity | Reference | SkyFrame "
                     "| % error | Tol % | Status | Note |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            if r["reference"] == 0.0:
                err = f"abs {fmt(r['value'])}"
                tol = f"abs <= {fmt(r.get('abs_floor', 0.0))}"
            else:
                err = f"{100 * r['err']:+.4f}"
                tol = f"{100 * r['tol']:.3g}"
            cells = [r["problem"], r["source"], r["quantity"],
                     fmt(r["reference"]), fmt(r["value"]), err, tol,
                     r["status"], r.get("note", "")]
            lines.append("| " + " | ".join(str(c).replace("|", "/")
                                           for c in cells) + " |")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    records, summary = run_suite()
    OUT.write_text(render(records, summary))
    print(f"wrote {OUT} ({len(records)} rows)")


if __name__ == "__main__":
    main()
