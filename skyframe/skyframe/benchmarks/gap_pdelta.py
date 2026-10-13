"""Gap case (not in CSI's ETABS analysis set): P-Delta of a cantilevered
column.

Problem (CSI SAP2000 test problem "P-Delta effect for a cantilevered
column"; closed form Timoshenko & Gere, *Theory of Elastic Stability*,
Sec. 1.11): L = 10 m, 0.1 m square, E = 30 GPa, axial load P = 0.70 Pcr
(Pcr = pi^2 EI / 4 L^2) plus a lateral tip load H giving a 0.06 m
first-order tip deflection.  Exact beam-column:

    delta = H / (k P) (tan kL - kL),   M_base = H L + P delta,   k^2 = P/EI.

SkyFrame: static case with ``geometric = "pdelta"`` (OpenSees P-Delta
geometric transformation = chord "string" stiffness), the column meshed
into 10 equal members as CSI recommends (no assumption beyond the mesh).
Also run with ETABS "Define > P-Delta Options" iterative-on-loads
(model-wide P-Delta, gravity P iterated, lateral case linear).
"""

from __future__ import annotations

import math

from ._core import (CaseResult, FIXED, disp, engine, load, new_model,
                    section, sig, support, tag_at, timed)
from ._svg import Sketch

ID = "GAP-PDELTA"
TITLE = "P-Delta cantilever column (Timoshenko & Gere)"
TIER = "gap"

L, B, E = 10.0, 0.1, 30.0e6
N = 10


def exact():
    I = B ** 4 / 12
    EI = E * I
    P = 0.7 * math.pi ** 2 * EI / (4 * L * L)
    H = 0.06 * 3 * EI / L ** 3
    k = math.sqrt(P / EI)
    d = H / (k * P) * (math.tan(k * L) - k * L)
    return P, H, d, H * L + P * d


def _model(method: str):
    P, H, _, _ = exact()
    m = new_model("pdelta", E, nu=0.2, heights=[L])
    m.active_dof = ["UX", "UZ", "RY"]
    section(m, "S", B * B, B ** 4 / 12)
    for k in range(N):
        m.add_member("column", "S", (0, 0, L * k / N), (0, 0, L * (k + 1) / N),
                     story="Story1", uid=f"C{k + 1}")
    support(m, (0, 0, 0), FIXED)
    if method == "case":
        load(m, "PH", (0, 0, L), fx=H, fz=-P)
        m.add_case("PH", {"PH": 1.0}, geometric="pdelta")
        return m, "PH"
    load(m, "G", (0, 0, L), fz=-P)
    load(m, "H", (0, 0, L), fx=H)
    m.add_case("H", {"H": 1.0})
    m.pdelta_options = {"method": "iterative_loads",
                        "load_factors": {"G": 1.0}, "max_iterations": 10,
                        "tolerance": 1e-10, "include_in": "all_linear"}
    return m, "H"


@timed
def run() -> CaseResult:
    res = CaseResult(
        ID, TITLE, TIER,
        source="Timoshenko & Gere, Theory of Elastic Stability, Sec. 1.11 "
               "(exact beam-column); CSI SAP2000 P-Delta cantilever test "
               "problem",
        tests="Geometric nonlinearity: P-Delta case and ETABS-style "
              "model-wide P-Delta (iterative on loads)")
    P, H, d, M = exact()
    res.inputs = [("Column", "L 10 m, 0.1 x 0.1 m, E 30 GPa"),
                  ("Axial", f"P = 0.70 Pcr = {P:.4f} kN"),
                  ("Lateral", f"H = {H:.5f} kN (0.06 m first order)")]
    res.assumptions = [
        f"Column meshed into {N} members (string-stiffness P-Delta needs "
        "a mesh; CSI recommends meshing).",
        "Reference is the exact continuous beam-column; the residual of "
        "a chord-stiffness formulation on 10 segments is ~0.5 %."]
    res.differences = [
        "P-Delta: SkyFrame (like ETABS) uses the chord ('string') "
        "geometric stiffness; on a 10-segment column it is 0.48 % below "
        "the exact beam-column -- the formulation, reproduced to 1e-6 by "
        "an independent numpy assembly (tests/verification)."]
    for method, grp in (("case", "P-Delta load case"),
                        ("options", "ETABS P-Delta options")):
        m, case = _model(method)
        eng = engine(m)
        r = eng.run_static(case)
        res.add("Tip deflection", "column top", "m", sig(d),
                "Timoshenko & Gere 1.11", None,
                abs(disp(eng, r, (0, 0, L), 0)), group=grp)
        if method == "case":
            res.add("Base moment H L + P delta", "column base", "kN-m",
                    sig(M), "Timoshenko & Gere 1.11", None,
                    abs(r.reactions[tag_at(eng, (0, 0, 0))][4]),
                    cls="internal", group=grp)
    sk = Sketch(200, 240)
    sk.line((0, 0), (0, 10)).fixed((0, 0))
    sk.arrow((0, 10), (1, 0), "H").arrow((0, 10.2), (0, -1), "P")
    res.svg = sk.render("P-Delta cantilever")
    return res
