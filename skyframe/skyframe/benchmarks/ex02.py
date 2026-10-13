"""CSI ETABS Verification, Analysis Example 2 -- Three-story plane frame,
dynamic response spectrum analysis (Rev. 0).  TIER 2: the periods and
mode shapes (Table 2-1, 12 values) are rebuilt; the four response-spectrum
values of Table 2-2 are BLOCKED because the El Centro spectrum ordinates
(file ELCN-RS1, shipped with ETABS) are not printed in the PDF.

Inputs (published, kip-in-s): two column lines 36 ft apart, 3 stories of 12
ft, fixed bases; columns W14X90, E = 29 500 ksi, infinite axial area, zero
shear area (shear deformation neglected); beams infinitely rigid, 24 in
deep, rigid offset 24 in at the column tops; story mass 0.4 kip-s^2/in.
Reference: 3-spring / 3-mass closed-form eigen solution (Przemieniecki
1968; Paz 1985).

[assumed] W14X90 strong-axis I = 999 in^4 (AISC Manual; not printed in the
PDF -- confirmed by the published period 0.4414 s).
[assumed] Axial deformation neglected = A x 1e6; rigid beams = A, I x 1e6.
Rigid diaphragm per floor carrying the story mass; mass-normalised mode
shapes, sign aligned with the reference.
"""

from __future__ import annotations

from ._core import (CaseResult, FIXED, engine, new_model, section, support,
                    timed)
from ._svg import elevation

ID = "EX02"
TITLE = "Three-story plane frame, rigid offsets, modes"
TIER = "2"

REF_T = ["0.4414", "0.1575", "0.1090"]
REF_PHI = {1: ["1.165", "0.934", "0.519"],          # roof, 2nd, 1st
           2: ["0.934", "-0.519", "-1.165"],
           3: ["0.519", "-1.165", "0.934"]}
BLOCKED = [
    "Table 2-2 (roof/2nd/1st RS displacements 2.139/1.716/0.955 in, C1 "
    "base moment 11,730 kip-in): needs the El Centro 1940 N-S 5 % "
    "spectrum ordinates, file ELCN-RS1 (ETABS install); not tabulated in "
    "the PDF."]


def build():
    m = new_model("CSI Ex02", 29500.0, heights=[144.0] * 3,
                  names=["1st", "2nd", "Roof"], diaphragm=True)
    m.active_dof = ["UX", "UZ", "RY"]
    section(m, "W14X90", 26.5 * 1e6, 999.0, 362.0, 4.06)
    section(m, "RB", 1e9, 1e9, 1e9, 1e9)
    for k in range(3):
        z0, z1 = 144.0 * k, 144.0 * (k + 1)
        st = m.stories[k].name
        for i, x in enumerate((0.0, 432.0)):
            m.add_member("column", "W14X90", (x, 0, z0), (x, 0, z1),
                         story=st, uid=f"C{i + 1}_{k + 1}", rigid_j=24.0,
                         angle=90.0)   # strong axis in the X-Z plane
        m.add_member("beam", "RB", (0, 0, z1), (432.0, 0, z1), story=st,
                     uid=f"B1_{k + 1}")
        m.story_masses[st] = 0.4
    for x in (0.0, 432.0):
        support(m, (x, 0, 0), FIXED)
    m.num_modes = 3
    return m


@timed
def run() -> CaseResult:
    res = CaseResult(
        ID, TITLE, TIER, status="partial",
        source="Closed-form 3-DOF eigen solution (Przemieniecki 1968; Paz "
               "1985); CSI ETABS Verification Example 2, Table 2-1",
        tests="2D frame, rigid end offsets, modal analysis (RS blocked)")
    res.inputs = [("Units", "kip, in, s"), ("Columns", "W14X90, E 29,500 "
                                                       "ksi"),
                  ("Beams", "rigid, 24 in deep (offset 24 in)"),
                  ("Geometry", "1 bay 36 ft, 3 stories x 12 ft"),
                  ("Mass", "0.4 kip-s2/in per story")]
    res.assumptions = [
        "[assumed] W14X90 I = 999 in4 (AISC; not printed).",
        "[assumed] Axial deformation neglected: A x 1e6; beams rigid.",
        "Shear deformation off (zero shear area in ETABS).",
        "Mode shapes mass-normalised, sign aligned with the reference.",
    ]
    res.blocked = list(BLOCKED)
    m = build()
    eng = engine(m)
    md = eng.run_modal(3)
    masters = [eng._asm.masters[s] for s in ("Roof", "2nd", "1st")]
    for k in range(3):
        res.add(f"Mode {k + 1} period", "", "s", REF_T[k],
                "Paz 1985 closed form", REF_T[k], float(md.periods[k]),
                group=f"Mode {k + 1}")
        sh = [md.shapes[k + 1][t][0] for t in masters]
        ref = [float(v) for v in REF_PHI[k + 1]]
        sign = 1.0 if sum(a * b for a, b in zip(sh, ref)) > 0 else -1.0
        for lvl, v, r in zip(("Roof", "2nd", "1st"), sh, REF_PHI[k + 1]):
            res.add(f"Mode {k + 1} shape", lvl, "-", r,
                    "Paz 1985 closed form", r, sign * float(v),
                    group=f"Mode {k + 1}")
    sk = elevation([0, 432], [144, 288, 432])
    res.svg = sk.render("Example 2 elevation")
    return res
