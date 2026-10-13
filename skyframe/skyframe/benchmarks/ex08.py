"""CSI ETABS Verification, Analysis Example 8 -- Two-story 3D frame,
dynamic response spectrum analysis with a constant 0.4 g spectrum (Rev. 0).

Inputs (published, kip-ft-s): 3 x 3 column grid X = 0, 35, 70 ft, Y = 0,
25, 50 ft; 2 stories of 13 ft; fixed bases; 12 beams per level.  Columns A
= 4 ft^2, I = 1.25 / 1.25 ft^4, E = 350 000 ksf; beams A = 5 ft^2, I
minor 1.67, major 2.61 ft^4, E = 500 000 ksf.  Story mass 6.212
kip-s^2/ft at the centre of mass (38, 27) ft of each story, NO mass
moment of inertia.  Spectrum constant 0.4 g.  References: Wilson &
Habibullah (1992) SAP90 (Ref. 1) and Peterson (1981) (Ref. 2).

[assumed] g = 32.2 ft/s^2 for the 0.4 g spectrum (kip-ft-s model).
[assumed] Torsion constant J and Poisson's ratio are not printed: J =
I22 + I33 (polar sum), nu = 0.3.  J only moves the 5th digit of T1
(J ~ 0 -> 0.22708 = ETABS, J >= 1 ft^4 -> 0.22706 = references).
[assumed] Excitation in X (the X displacement is reported); damping 5 %,
CQC (a flat spectrum gives 0.0201 with CQC or SRSS).
Rigid diaphragm per story; the CM joint is created with a massless stub
(see ``_core.cm_stub``) and carries the 6.212 mass in X and Y with zero
rotational inertia, exactly as the references model it.
Shear deformation off (no shear areas given).
"""

from __future__ import annotations

from skyframe.core.model import G_ACCEL, Material

from ._core import (CaseResult, FIXED, cm_stub, disp, engine, mass,
                    new_model, section, support, timed)
from ._svg import Sketch

ID = "EX08"
TITLE = "Two-story 3D frame, eccentric mass, flat 0.4 g spectrum"
TIER = "1"

G_FT = 32.2
CM = (38.0, 27.0)
REF = [("Period, mode 1", "0.22708", "0.22706"),
       ("Period, mode 2", "0.21565", "0.21563"),
       ("Period, mode 3", "0.07335", "0.07335"),
       ("Period, mode 4", "0.07201", "0.07201")]
REF_DX = ("X-displacement, CM 2nd story", "0.0201", "0.0201")


def build():
    m = new_model("CSI Ex08", 350000.0, nu=0.3, heights=[13.0, 13.0],
                  diaphragm=True)
    m.add_material(Material("BEAM", E=500000.0, nu=0.3, unit_weight=0.0))
    section(m, "COL", 4.0, 1.25, 1.25, 2.5)
    section(m, "BM", 5.0, 2.61, 1.67, 2.61 + 1.67, mat="BEAM")
    xs, ys = (0.0, 35.0, 70.0), (0.0, 25.0, 50.0)
    for k, (z0, z1) in enumerate(((0.0, 13.0), (13.0, 26.0))):
        st = f"Story{k + 1}"
        for j, y in enumerate(ys):
            for i, x in enumerate(xs):
                m.add_member("column", "COL", (x, y, z0), (x, y, z1),
                             story=st, uid=f"C{3 * j + i + 1}_{k + 1}")
        for y in ys:
            for i in range(2):
                m.add_member("beam", "BM", (xs[i], y, z1),
                             (xs[i + 1], y, z1), story=st)
        for x in xs:
            for j in range(2):
                m.add_member("beam", "BM", (x, ys[j], z1),
                             (x, ys[j + 1], z1), story=st)
        cm_stub(m, "BM", (35.0, 25.0, z1), (CM[0], CM[1], z1),
                uid=f"CMSTUB{k + 1}")
        mass(m, (CM[0], CM[1], z1), mx=6.212, my=6.212)
    for y in ys:
        for x in xs:
            support(m, (x, y, 0.0), FIXED)
    m.num_modes = 4
    m.add_rs_case("RSX", "X", [[0.0, 0.4], [10.0, 0.4]], num_modes=4,
                  combo_method="CQC", damping=0.05, scale=G_FT / G_ACCEL)
    return m


@timed
def run() -> CaseResult:
    res = CaseResult(
        ID, TITLE, TIER,
        source="Wilson & Habibullah (1992) SAP90 [Ref. 1]; Peterson (1981) "
               "[Ref. 2]; CSI ETABS Verification Example 8, Table 8-1",
        tests="3D frame, rigid diaphragm with eccentric CM and no rotational "
              "mass, modal + flat-spectrum RS")
    res.inputs = [("Units", "kip, ft, s"),
                  ("Columns", "A 4 ft2, I 1.25/1.25 ft4, E 350,000 ksf"),
                  ("Beams", "A 5 ft2, I 1.67/2.61 ft4, E 500,000 ksf"),
                  ("Plan", "2 x 35 ft by 2 x 25 ft, 2 stories x 13 ft"),
                  ("Mass", "6.212 kip-s2/ft per story at CM (38, 27) ft, "
                           "no MMI"),
                  ("Spectrum", "constant 0.4 g")]
    res.assumptions = [
        "[assumed] g = 32.2 ft/s2; X excitation; 5 % damping, CQC.",
        "[assumed] J = I22 + I33 and nu = 0.3 (not printed; J only moves "
        "the 5th digit of T1).",
        "CM joint created by a massless in-plane stub so the story mass "
        "sits at (38, 27) with zero rotational inertia.",
    ]
    m = build()
    eng = engine(m)
    md = eng.run_modal(4)
    for k, (q, et, ref) in enumerate(REF):
        res.add(q, "", "s", ref, "Wilson & Habibullah 1992", et,
                float(md.periods[k]))
    rs = eng.run_response_spectrum("RSX")
    ux = disp(eng, rs, (CM[0], CM[1], 26.0), 0)
    res.add(REF_DX[0], "CM (38, 27, 26) ft", "ft", REF_DX[2],
            "Wilson & Habibullah 1992", REF_DX[1], abs(ux))
    sk = Sketch()
    for x in (0, 35, 70):
        sk.line((x, 0), (x, 50), "b")
    for y in (0, 25, 50):
        sk.line((0, y), (70, y), "b")
    for x in (0, 35, 70):
        for y in (0, 25, 50):
            sk.dot((x, y), "col")
    sk.dot(CM, "cm").text((CM[0] + 2, CM[1] + 2), "CM (38, 27)", "lbl",
                          "start")
    res.svg = sk.render("Example 8 plan")
    return res
