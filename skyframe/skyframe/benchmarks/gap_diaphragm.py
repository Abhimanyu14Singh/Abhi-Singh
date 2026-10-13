"""Gap case (not in CSI's ETABS analysis set): rigid-diaphragm centre of
mass (CM) and centre of rigidity (CR) -- the D3 audit hand calculations
(``tests/test_cm_cr_audit.py``, CONTRACT "Centers of mass and rigidity").

One story, h = 3 m, E = 25 GPa, nu = 0.2, rigid diaphragm; square columns
(x, y, side): (0, 0, 0.7), (0, 6, 0.7), (0, 3, 0.7), (9, 0, 0.4), (9, 6,
0.4), (4, 7, 0.5) m with their tops guided (rotations restrained), so each
column's lateral stiffness is exactly k = 12 E I / h^3, I = s^4 / 12.

* CR (ETABS unit-load definition; for one story with guided columns it is
  the stiffness centroid): x_CR = sum(k x) / sum(k), y_CR = sum(k y) /
  sum(k).
* CM: an explicit story mass of 10 t (lumped by SkyFrame at the plan
  centre of the column bounding box, (4.5, 3.5)) plus a 20 t joint mass
  at the column top (9, 6): x_CM = (10 x 4.5 + 20 x 9) / 30, y_CM = (10 x
  3.5 + 20 x 6) / 30.
"""

from __future__ import annotations

from skyframe.core.model import FrameSection

from ._core import (CaseResult, FIXED, engine, mass, new_model, sig,
                    support, timed)
from ._svg import Sketch

ID = "GAP-DIAPHRAGM"
TITLE = "Rigid-diaphragm centre of mass and rigidity"
TIER = "gap"

E, NU, H = 25.0e6, 0.2, 3.0
COLS = [(0, 0, 0.7), (0, 6, 0.7), (0, 3, 0.7), (9, 0, 0.4), (9, 6, 0.4),
        (4, 7, 0.5)]


def build():
    m = new_model("cmcr", E, nu=NU, heights=[H], diaphragm=True)
    for i, (x, y, s) in enumerate(COLS):
        nm = f"S{s}"
        if nm not in m.sections:
            m.add_section(FrameSection.rectangular(nm, "MAT", s, s))
        m.add_member("column", nm, (x, y, 0), (x, y, H), story="Story1",
                     uid=f"C{i + 1}")
        support(m, (x, y, 0), FIXED)
        support(m, (x, y, H), (0, 0, 0, 1, 1, 0))
    m.story_masses["Story1"] = 10.0
    mass(m, (9.0, 6.0, H), mx=20.0, my=20.0)
    return m


@timed
def run() -> CaseResult:
    res = CaseResult(
        ID, TITLE, TIER,
        source="Hand calculation (stiffness and mass centroids); D3 audit, "
               "tests/test_cm_cr_audit.py",
        tests="Story centre of mass / centre of rigidity of a rigid "
              "diaphragm (ETABS Centers of Mass and Rigidity table)")
    res.inputs = [("Columns", "6 square columns 0.4-0.7 m, guided tops"),
                  ("Story", "h = 3 m, E 25 GPa, rigid diaphragm"),
                  ("Mass", "10 t story mass + 20 t joint mass at (9, 6)")]
    res.assumptions = ["Guided column tops -> k = 12EI/h^3 exactly; the "
                       "story mass is lumped at the plan centre (4.5, 3.5)."]
    k = [12 * E * s ** 4 / 12.0 / H ** 3 for _, _, s in COLS]
    xcr = sum(ki * c[0] for ki, c in zip(k, COLS)) / sum(k)
    ycr = sum(ki * c[1] for ki, c in zip(k, COLS)) / sum(k)
    xcm = (10 * 4.5 + 20 * 9) / 30.0
    ycm = (10 * 3.5 + 20 * 6) / 30.0
    d = engine(build()).run().to_dict()
    sp = d["story_props"]["Story1"]
    for q, ref, v in (("x_CM", xcm, sp["cm_x"]), ("y_CM", ycm, sp["cm_y"]),
                      ("x_CR", xcr, sp["cr_x"]), ("y_CR", ycr, sp["cr_y"])):
        res.add(q, "Story1", "m", sig(ref), "hand calculation", None,
                float(v))
    sk = Sketch(240, 200)
    for x, y, s in COLS:
        sk.dot((x, y), "col")
    sk.dot((xcm, ycm), "cm").text((xcm, ycm + 0.5), "CM")
    sk.dot((xcr, ycr), "cr").text((xcr, ycr + 0.5), "CR")
    res.svg = sk.render("CM / CR plan")
    return res
