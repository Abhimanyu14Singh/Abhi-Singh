"""CSI ETABS Verification, Analysis Example 6 -- Nine-story, ten-bay plane
frame, eigenvalue analysis (Rev. 2).

Inputs (published, kip-ft-s): 11 column lines at 20 ft, 9 stories at 10 ft,
fixed bases; E = 432 000 ksf; every member A = 3 ft^2, I = 1 ft^4; mass 3
kip-s^2/ft per ft of member length.  Reference: Bathe & Wilson (1972) and
Wilson & Habibullah (1992), who lump horizontal AND vertical mass at every
joint; ETABS lumps ONE horizontal mass per floor (rigid floor).

Two SkyFrame models are run:

* **ETABS mass model** (primary rows, as CSI compares): rigid diaphragm per
  floor, one horizontal story mass = tributary member length x 3:
  [derived] levels 1-8: (200 + 11 x 10) x 3 = 930, roof: (200 + 11 x 5)
  x 3 = 765 kip-s^2/ft.  Compared with Bathe & Wilson (independent) and
  ETABS; CSI rates the ~0.1 % gap "acceptable" because the mass models
  differ.
* **Reference mass model** (supplementary rows): no diaphragm, half of
  each member's mass lumped at each end joint in BOTH X and Z (the
  references' model) -> should reproduce Bathe & Wilson to their 6
  printed digits.

[assumed] Shear deformation not mentioned -> off (SkyFrame default).
"""

from __future__ import annotations

import math

from ._core import (CaseResult, FIXED, engine, mass, new_model, section,
                    support, timed)
from ._svg import Sketch

ID = "EX06"
TITLE = "Nine-story ten-bay plane frame eigenvalues"
TIER = "1"

REF = [("1", "0.58964", "0.58954", "0.58954"),
       ("2", "5.53195", "5.52696", "5.52695"),
       ("3", "16.5962", "16.5879", "16.5878")]   # mode, ETABS, W&H, B&W


def build(story_mass: bool):
    m = new_model("CSI Ex06", 432000.0, heights=[10.0] * 9,
                  diaphragm=story_mass)
    m.active_dof = ["UX", "UZ", "RY"]
    section(m, "S", 3.0, 1.0)
    jm = {}

    def add(p, v):
        jm[p] = jm.get(p, 0.0) + v
    for j in range(9):
        for i in range(11):
            p, q = (20.0 * i, 0.0, 10.0 * j), (20.0 * i, 0.0, 10.0 * (j + 1))
            m.add_member("column", "S", p, q, story=f"Story{j + 1}",
                         uid=f"C{i + 1}_{j + 1}")
            add(p, 15.0)
            add(q, 15.0)
    for j in range(1, 10):
        for i in range(10):
            p, q = (20.0 * i, 0.0, 10.0 * j), (20.0 * (i + 1), 0.0, 10.0 * j)
            m.add_member("beam", "S", p, q, story=f"Story{j}",
                         uid=f"B{i + 1}_{j}")
            add(p, 30.0)
            add(q, 30.0)
    for i in range(11):
        support(m, (20.0 * i, 0.0, 0.0), FIXED)
    if story_mass:
        for j in range(1, 10):
            m.story_masses[f"Story{j}"] = 930.0 if j < 9 else 765.0
    else:
        for p, v in jm.items():
            if p[2] > 0:
                mass(m, p, mx=v, mz=v)
    m.num_modes = 3
    return m


def _eig(story_mass: bool):
    md = engine(build(story_mass)).run_modal(3)
    return [(2 * math.pi / T) ** 2 for T in md.periods]


@timed
def run() -> CaseResult:
    res = CaseResult(
        ID, TITLE, TIER,
        source="Bathe & Wilson (1972); Wilson & Habibullah (1992) SAP90; "
               "CSI ETABS Verification Example 6, Table 6-1",
        tests="Large 2D frame eigenvalue solution, tributary story mass, "
              "rigid floor")
    res.inputs = [("Units", "kip, ft, s"), ("E", "432,000 ksf"),
                  ("Members", "A = 3 ft2, I = 1 ft4 (all)"),
                  ("Geometry", "10 bays @ 20 ft, 9 stories @ 10 ft"),
                  ("Mass", "3 kip-s2/ft per ft of member length")]
    res.assumptions = [
        "[derived] Story mass 930 kip-s2/ft (levels 1-8), 765 (roof) by "
        "tributary length, as ETABS converts it.",
        "Primary rows: ETABS mass model (one horizontal mass per floor, "
        "rigid floor).",
        "Supplementary rows: the references' joint-lumped H + V mass "
        "without diaphragm.",
        "[assumed] Shear deformation off (not mentioned).",
    ]
    res.differences = [
        "Example 6: the CSI comparison is between DIFFERENT mass models "
        "(story-lumped horizontal vs joint-lumped H+V); the +0.02 to +0.09 "
        "% gap of both ETABS and SkyFrame's ETABS-style model is that "
        "modelling difference, not solver error -- SkyFrame's reference-"
        "style model matches Bathe & Wilson to the printed digits."]
    lam_s = _eig(True)
    lam_j = _eig(False)
    for k, (mode, et, wh, bw) in enumerate(REF):
        res.add(f"Eigenvalue mode {mode}", "ETABS mass model", "(rad/s)2",
                bw, "Bathe & Wilson 1972", et, lam_s[k],
                group="ETABS mass model")
    for k, (mode, et, wh, bw) in enumerate(REF):
        res.add(f"Eigenvalue mode {mode}", "joint H+V mass model",
                "(rad/s)2", bw, "Bathe & Wilson 1972", None, lam_j[k],
                group="Reference mass model",
                note="supplementary: references' mass model")
    sk = Sketch(420, 220)
    for i in range(11):
        sk.line((20 * i, 0), (20 * i, 90))
        sk.fixed((20 * i, 0))
    for j in range(1, 10):
        sk.line((0, 10 * j), (200, 10 * j), "b")
    res.svg = sk.render("Example 6 elevation")
    return res
