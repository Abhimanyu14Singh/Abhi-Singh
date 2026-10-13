"""CSI ETABS Verification, Analysis Example 4 -- Single-story 3D frame,
dynamic response spectrum analysis (Rev. 0).  Only the modal results are
compared (Table 4-1), so the El Centro spectrum is not needed.

Inputs (published, kip-in-s): one story 15 ft (180 in), plan 30 ft x 30 ft;
columns C1, C2 24" x 24", C3, C4 18" x 18"; beams infinitely rigid, 36"
deep, rigid offset at the column tops = 36 in; E = 3000 ksi; story weight
150 psf; shear and axial deformation of the columns neglected; rigid
diaphragm; 3 modes.  Reference: 3x3 stiffness (Przemieniecki 1968) and
mass matrices, eigen solution (Paz 1985).

[derived] Column coordinates from Figure 4-1 (origin at the plan centre):
C1 (-180, -180), C2 (180, -180), C3 (-180, 180), C4 (180, 180) in; I =
b^4/12 (27 648 / 8 748 in^4).
[derived] Story mass m = 150 psf x 900 ft^2 / g = 135 k / 386.4 in/s^2 =
0.349379 k-s^2/in (g = 386.4 reproduces the published Y mode-shape
ordinate 1/sqrt(m) = 1.6918; 386.09 would give 1.6911) at the plan centre
with I_z = m (a^2 + b^2)/12 (SkyFrame lumps an explicit story mass at the
diaphragm master with exactly this inertia).
[assumed] "Axial deformation neglected" = A x 1e6; rigid beams = A, I x
1e6; the hand stiffness matrix has no column torsion, so the column
torsion constant is set to ~0 (J = 1e-6 I).
Mode shapes are mass-normalised (SkyFrame's normalisation); the sign of a
mode is arbitrary and is aligned with the reference's dominant component.
"""

from __future__ import annotations

import math

from ._core import (CaseResult, FIXED, engine, new_model, section, support,
                    timed)
from ._svg import Sketch

ID = "EX04"
TITLE = "One-story 3D frame, torsionally coupled modes"
TIER = "1"

BIG = 1.0e6
G_IN = 386.4

REF = [   # mode, quantity, ETABS, theoretical
    (1, "Period", "0.1389", "0.1389"),
    (1, "X-translation", "-1.6244", "-1.6244"),
    (1, "Y-translation", "0.0000", "0.000"),
    (1, "Z-rotation", "0.0032", "0.0032"),
    (2, "Period", "0.1254", "0.1254"),
    (2, "X-translation", "0.000", "0.000"),
    (2, "Y-translation", "1.6918", "1.6918"),
    (2, "Z-rotation", "0.000", "0.000"),
    (3, "Period", "0.0702", "0.070"),
    (3, "X-translation", "0.4728", "0.4728"),
    (3, "Y-translation", "0.000", "0.000"),
    (3, "Z-rotation", "0.0111", "0.0111"),
]
_DOF = {"X-translation": 0, "Y-translation": 1, "Z-rotation": 5}


def build():
    H = 180.0
    m = new_model("CSI Ex04", 3000.0, nu=0.2, heights=[H], diaphragm=True)
    m.num_modes = 3
    for name, b in (("C24", 24.0), ("C18", 18.0)):
        I = b ** 4 / 12.0
        section(m, name, b * b * BIG, I, I, 1e-6 * I)
    section(m, "RB", 1e9, 1e9, 1e9, 1e9)
    cols = {"C1": (-180.0, -180.0, "C24"), "C2": (180.0, -180.0, "C24"),
            "C3": (-180.0, 180.0, "C18"), "C4": (180.0, 180.0, "C18")}
    for uid, (x, y, s) in cols.items():
        m.add_member("column", s, (x, y, 0), (x, y, H), story="Story1",
                     uid=uid, rigid_j=36.0)
        support(m, (x, y, 0), FIXED)
    for uid, a, b in (("B1", "C1", "C2"), ("B2", "C3", "C4"),
                      ("B3", "C1", "C3"), ("B4", "C2", "C4")):
        pa, pb = cols[a], cols[b]
        m.add_member("beam", "RB", (pa[0], pa[1], H), (pb[0], pb[1], H),
                     story="Story1", uid=uid)
    m.story_masses["Story1"] = 150.0 * 900.0 / 1000.0 / G_IN
    return m


@timed
def run() -> CaseResult:
    res = CaseResult(
        ID, TITLE, TIER,
        source="3-DOF stiffness (Przemieniecki 1968) + eigen solution "
               "(Paz 1985); CSI ETABS Verification Example 4, Table 4-1",
        tests="3D frame, rigid diaphragm, rigid end offsets, torsionally "
              "coupled modal analysis, story mass from area weight")
    res.inputs = [("Units", "kip, in, s"), ("E", "3000 ksi"),
                  ("Columns C1, C2", "24 x 24 in"),
                  ("Columns C3, C4", "18 x 18 in"),
                  ("Beams", "infinitely rigid, 36 in deep"),
                  ("Story", "15 ft, plan 30 x 30 ft, weight 150 psf")]
    res.assumptions = [
        "[derived] Column layout read from Figure 4-1 (24 in columns on the "
        "-Y edge).",
        "[derived] m = 135 k / 386.4 in/s2 = 0.34938 k-s2/in at the plan "
        "centre, Iz = m(a2+b2)/12.",
        "[assumed] Axial deformation neglected (A x 1e6); beams rigid; "
        "column torsion neglected as in the 3x3 hand model.",
        "Rigid offset 36 in at the column tops (flexible length 144 in).",
        "Mode-shape sign aligned with the reference (arbitrary in any "
        "eigen solver); shapes are mass-normalised in both.",
    ]
    m = build()
    eng = engine(m)
    md = eng.run_modal(3)
    master = eng._asm.masters["Story1"]
    for mode, qty, et, th in REF:
        if qty == "Period":
            v = md.periods[mode - 1]
            res.add(f"Mode {mode} period", "", "s", th, "Paz 1985 eigen",
                    et, float(v), group=f"Mode {mode}")
            continue
        sh = md.shapes[mode][master]
        dom = max((0, 1, 5), key=lambda d: abs(sh[d]) * (1.0 if d < 5
                                                            else 180.0))
        ref_dom = {1: -1.6244, 2: 1.6918, 3: 0.4728}[mode]
        sign = 1.0 if sh[dom] * ref_dom > 0 else -1.0
        res.add(f"Mode {mode} {qty.lower()}", "diaphragm (plan centre)",
                "rad" if qty == "Z-rotation" else "-", th, "Paz 1985 eigen",
                et, float(sign * sh[_DOF[qty]]), group=f"Mode {mode}")
    sk = Sketch()
    pts = {"C1": (-180, -180), "C2": (180, -180), "C3": (-180, 180),
           "C4": (180, 180)}
    for a, b in (("C1", "C2"), ("C3", "C4"), ("C1", "C3"), ("C2", "C4")):
        sk.line(pts[a], pts[b], "b")
    for k, p in pts.items():
        sk.dot(p, "col")
        sk.text((p[0], p[1] + (14 if p[1] > 0 else -24)),
                k + (" 24x24" if k in ("C1", "C2") else " 18x18"))
    sk.dot((0, 0), "cm").text((0, 10), "CM")
    res.svg = sk.render("Example 4 plan")
    return res
