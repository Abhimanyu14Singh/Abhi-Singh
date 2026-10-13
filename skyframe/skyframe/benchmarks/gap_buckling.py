"""Gap case (not in CSI's ETABS analysis set): linear buckling.

1. Euler column, pinned-pinned (Timoshenko & Gere, *Theory of Elastic
   Stability*, Sec. 2.1): L = 6 m, E = 200 GPa, I = 1e-5 m^4, unit axial
   load; P_cr = pi^2 E I / L^2.  Column meshed into 8 members (consistent
   geometric stiffness converges from above as h^4).
2. Fixed-base portal frame, sway buckling (Timoshenko & Gere Sec. 2.8;
   SAP2000 Verification "Buckling of a rigid frame"): h = L = 4 m, Ic = Ib
   = 2e-5 m^4, E = 200 GPa, equal vertical loads on both column tops.
   Exact parameter phi = h sqrt(P/EIc) from the stability-function
   determinant det[[s + 6r, -s(1+c)], [-s(1+c), 2s(1+c) - phi^2]] = 0
   (r = (Ib/L)/(Ic/h)) -> phi = 2.7165, P_cr = 7.379 EIc/h^2.  Members
   meshed into 4 pieces; the joints are braced out of plane so the
   in-plane sway mode is measured.  [assumed] Columns axially stiff (A =
   1 m^2), as in the closed form.
"""

from __future__ import annotations

import math

from skyframe.core.buckling import buckling_analysis

from ._core import (CaseResult, FIXED, load, new_model, section, sig,
                    support, timed)
from ._svg import Sketch

ID = "GAP-BUCKLING"
TITLE = "Linear buckling: Euler column and rigid portal frame"
TIER = "gap"


def _chain(m, sec, p0, p1, n, prefix, kind):
    for k in range(n):
        a = [p0[i] + (p1[i] - p0[i]) * k / n for i in range(3)]
        b = [p0[i] + (p1[i] - p0[i]) * (k + 1) / n for i in range(3)]
        m.add_member(kind, sec, tuple(a), tuple(b), story="Story1",
                     uid=f"{prefix}{k + 1}")


def portal_phi(r: float) -> float:
    def det(phi):
        sn, cs = math.sin(phi), math.cos(phi)
        s = phi * (sn - phi * cs) / (2 - 2 * cs - phi * sn)
        c = (phi - sn) / (sn - phi * cs)
        return (s + 6 * r) * (2 * s * (1 + c) - phi ** 2) - (s * (1 + c)) ** 2
    lo, hi = 0.5, 3.1
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if det(lo) * det(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def euler():
    L, E, I = 6.0, 2.0e8, 1.0e-5
    m = new_model("euler", E, heights=[L])
    section(m, "S", 1e-2, I, I, 2 * I)
    _chain(m, "S", (0, 0, 0), (0, 0, L), 8, "C", "column")
    support(m, (0, 0, 0), (1, 1, 1, 0, 0, 1))
    support(m, (0, 0, L), (1, 1, 0, 0, 0, 0))
    load(m, "G", (0, 0, L), fz=-1.0)
    m.validate()
    lam = buckling_analysis(m, {"G": 1.0}, num_modes=2).factors[0]
    return math.pi ** 2 * E * I / L ** 2, lam


def portal():
    h = L = 4.0
    E, I = 2.0e8, 2.0e-5
    phi = portal_phi((I / L) / (I / h))
    m = new_model("portal", E, heights=[h])
    section(m, "S", 1.0, I, I, 2 * I)
    _chain(m, "S", (0, 0, 0), (0, 0, h), 4, "CA", "column")
    _chain(m, "S", (L, 0, 0), (L, 0, h), 4, "CB", "column")
    _chain(m, "S", (0, 0, h), (L, 0, h), 4, "BM", "beam")
    support(m, (0, 0, 0), FIXED)
    support(m, (L, 0, 0), FIXED)
    for x in (0.0, L):
        load(m, "G", (x, 0, h), fz=-1.0)
        support(m, (x, 0, h), (0, 1, 0, 0, 0, 0))
    m.validate()
    lam = buckling_analysis(m, {"G": 1.0}, num_modes=3).factors[0]
    return phi ** 2 * E * I / h ** 2, lam, phi


@timed
def run() -> CaseResult:
    res = CaseResult(
        ID, TITLE, TIER,
        source="Timoshenko & Gere, Theory of Elastic Stability, Sec. 2.1 "
               "(Euler) and Sec. 2.8 (rigid frame, stability functions)",
        tests="Linear (eigen) buckling with consistent geometric stiffness")
    res.inputs = [("Euler column", "L 6 m, E 200 GPa, I 1e-5 m4, pinned"),
                  ("Portal", "h = L = 4 m, Ic = Ib = 2e-5 m4, fixed base")]
    res.assumptions = [
        "Members meshed (8 per column; 4 per portal member); the "
        "consistent geometric stiffness converges from above.",
        "[assumed] Portal columns axially stiff, joints braced out of "
        "plane (as in the closed form)."]
    pe, le = euler()
    res.add("Critical load P_cr = pi^2 EI/L^2", "pinned column, 8 seg.",
            "kN", sig(pe), "Timoshenko & Gere 2.1", None, le,
            group="Euler column")
    pp, lp, phi = portal()
    res.add("Critical load per column", f"phi = {phi:.4f}", "kN", sig(pp),
            "Timoshenko & Gere 2.8", None, lp, group="Portal frame")
    sk = Sketch(220, 200)
    sk.line((0, 0), (0, 4)).line((4, 0), (4, 4)).line((0, 4), (4, 4), "b")
    sk.fixed((0, 0)).fixed((4, 0))
    sk.arrow((0, 4), (0, -1), "P").arrow((4, 4), (0, -1), "P")
    res.svg = sk.render("Portal frame buckling")
    return res
