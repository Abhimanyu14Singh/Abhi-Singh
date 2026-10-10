"""Verification family 1 -- FRAMES (static, geometric nonlinearity, buckling).

Modelled on the CSI *SAP2000 / ETABS Software Verification* frame examples
(continuous beams, portal frames, end releases, end offsets, temperature
loading, displacement loading, P-Delta, buckling, large displacement) with
the reference taken from the PUBLISHED closed form each CSI example itself
uses as its "independent" result.  Tolerances follow the element
formulation:

* Euler-Bernoulli ``elasticBeamColumn`` with exact consistent fixed-end
  forces is EXACT at the nodes for prismatic members -> 1e-6 relative;
* string-stiffness P-Delta / corotational / consistent-Kg buckling are
  discretisation-dependent -> a mesh-convergence assertion plus the
  tolerance the converged mesh justifies.
"""

import math

import numpy as np
import pytest

from skyframe.core.buckling import buckling_analysis
from skyframe.core.loads_ext import GroundDisplacement
from skyframe.core.model import MemberLoad

from _vhelp import (FIXED, PINNED, chain, check, disp, engine, load,
                    new_model, planar_xz, run_case, section, support, tag_at)

FAM = "Frames"
EXACT = 1e-6        # nodal-exact Euler-Bernoulli quantities


# --------------------------------------------------------------------------- #
# F1  three-span continuous beam, UDL
# --------------------------------------------------------------------------- #
def test_f01_three_span_continuous_beam_udl():
    """Three equal spans L, uniform load w (SAP2000 Verification Example
    1-001 family "frame - beam on supports"; reference: AISC Steel
    Construction Manual, Beam Diagrams & Formulas, case 36 "continuous beam
    - three equal spans - all spans loaded": R_end = 0.4wL, R_int = 1.1wL,
    M_B = -0.100wL^2; centre-span midpoint deflection by superposition
    5wL^4/384EI - M_B L^2/8EI = 0.000521 wL^4/EI)."""
    L, w, E, I = 6.0, 10.0, 2.0e8, 2.0e-4
    m = new_model("F1", E)
    section(m, "S", 1e-2, I)
    planar_xz(m)
    xs = [0, L / 2, L, 1.5 * L, 2 * L, 2.5 * L, 3 * L]
    for k in range(6):
        m.add_member("beam", "S", (xs[k], 0, 0), (xs[k + 1], 0, 0),
                     story="Story1", uid=f"B{k + 1}")
        m.pattern("W").member_loads.append(MemberLoad(f"B{k + 1}", w=w))
    support(m, (0, 0, 0), PINNED)
    for x in (L, 2 * L, 3 * L):
        support(m, (x, 0, 0), (0, 1, 1, 0, 0, 0))
    m.add_case("W", {"W": 1.0})
    eng, r = run_case(m, "W")
    P = "Three-span continuous beam, UDL"
    S = "AISC Manual Beam Diagrams case 36; SAP2000 Ex. 1-001 family"
    check(P, S, "end reaction R_A [0.4wL]", 0.4 * w * L,
          r.reactions[tag_at(eng, (0, 0, 0))][2], EXACT, family=FAM)
    check(P, S, "interior reaction R_B [1.1wL]", 1.1 * w * L,
          r.reactions[tag_at(eng, (L, 0, 0))][2], EXACT, family=FAM)
    mB = r.member_forces["B2"][11]          # Mz at end j of B2 (= support B)
    check(P, S, "support moment |M_B| [0.1wL^2]", 0.1 * w * L * L,
          abs(mB), EXACT, family=FAM)
    wmid = (5.0 / 384.0 - 1.0 / 80.0) * w * L ** 4 / (E * I)
    check(P, S, "centre-span deflection [0.000521wL^4/EI]", -wmid,
          disp(eng, r, (1.5 * L, 0, 0), 2), EXACT, family=FAM)


# --------------------------------------------------------------------------- #
# F2  fixed-base portal frame, lateral sway
# --------------------------------------------------------------------------- #
def test_f02_portal_frame_sway():
    """Fixed-base single-bay portal under a lateral top load H (ETABS
    Verification "plane frame" class; reference: slope-deflection solution,
    e.g. Hibbeler *Structural Analysis* Ch. 11 / Kassimali Ch. 16 --
    antisymmetric sway, inextensible members):
    theta = 3psi/(2+3r), Delta = H h^3 / (4 E Ic (6 - 9/(2+3r))),
    M_base = 2 E Ic/h (theta - 3 psi), r = (Ib/L)/(Ic/h)."""
    h, L, E, Ic, Ib, H = 4.0, 6.0, 2.0e8, 3.0e-4, 5.0e-4, 50.0
    m = new_model("F2", E)
    big_A = 1.0e3                   # axially rigid (slope-deflection basis)
    section(m, "C", big_A, Ic)
    section(m, "B", big_A, Ib)
    planar_xz(m)
    m.add_member("column", "C", (0, 0, 0), (0, 0, h), story="Story1", uid="C1")
    m.add_member("column", "C", (L, 0, 0), (L, 0, h), story="Story1", uid="C2")
    m.add_member("beam", "B", (0, 0, h), (L, 0, h), story="Story1", uid="B1")
    support(m, (0, 0, 0))
    support(m, (L, 0, 0))
    load(m, "H", (0, 0, h), fx=H)
    m.add_case("H", {"H": 1.0})
    eng, r = run_case(m, "H")
    r_ = (Ib / L) / (Ic / h)
    delta = H * h ** 3 / (4 * E * Ic * (6 - 9 / (2 + 3 * r_)))
    psi = delta / h
    theta = 3 * psi / (2 + 3 * r_)
    M_base = abs(2 * E * Ic / h * (theta - 3 * psi))
    P = "Fixed-base portal frame, lateral sway"
    S = "Slope-deflection (Hibbeler Ch.11); ETABS plane-frame class"
    tol = 1e-5    # finite (very large) EA vs the inextensible hand solution
    check(P, S, "sway Delta", delta, disp(eng, r, (0, 0, h), 0), tol,
          family=FAM)
    check(P, S, "joint rotation theta", theta,
          abs(disp(eng, r, (0, 0, h), 4)), tol, family=FAM)
    check(P, S, "base moment", M_base,
          abs(r.reactions[tag_at(eng, (0, 0, 0))][4]), tol, family=FAM)
    check(P, S, "base shear sum = H", -H, r.base["FX"], EXACT, family=FAM)


# --------------------------------------------------------------------------- #
# F3  3D space frame (bent cantilever: bending + torsion)
# --------------------------------------------------------------------------- #
def test_f03_space_frame_bent_cantilever():
    """Horizontal L-shaped (bent) cantilever loaded out of plane: leg 1
    along X (length a, fixed at the origin), leg 2 along Y (length b),
    vertical tip load P.  Classic 3D-frame verification (SAP2000 3D frame
    examples; Roark's *Formulas for Stress and Strain* 8th ed. Table 10.1 /
    Gere & Timoshenko *Mechanics of Materials* bent-bar example):
    delta_tip = P b^3/3EI + P a^3/3EI + (P b) a b / GJ, and the fixed-end
    torque T = P b, bending moment M = P a."""
    a, b, P, E, nu = 3.0, 2.0, 10.0, 2.0e8, 0.3
    I, J = 8.0e-6, 1.6e-5
    m = new_model("F3", E, nu=nu)
    section(m, "S", 4e-3, I, I, J)
    m.add_member("beam", "S", (0, 0, 0), (a, 0, 0), story="Story1", uid="L1")
    m.add_member("beam", "S", (a, 0, 0), (a, b, 0), story="Story1", uid="L2")
    support(m, (0, 0, 0))
    load(m, "P", (a, b, 0), fz=-P)
    m.add_case("P", {"P": 1.0})
    eng, r = run_case(m, "P")
    G = E / (2 * (1 + nu))
    delta = P * b ** 3 / (3 * E * I) + P * a ** 3 / (3 * E * I) \
        + P * b * a * b / (G * J)
    P_ = "3D space frame: bent cantilever (bending + torsion)"
    S = "Roark Table 10.1 / Gere-Timoshenko bent bar; SAP2000 3D frame class"
    check(P_, S, "tip deflection", -delta, disp(eng, r, (a, b, 0), 2),
          EXACT, family=FAM)
    rx = r.reactions[tag_at(eng, (0, 0, 0))]
    check(P_, S, "support torque |Mx| = Pb", P * b, abs(rx[3]), EXACT,
          family=FAM)
    check(P_, S, "support moment |My| = Pa", P * a, abs(rx[4]), EXACT,
          family=FAM)


# --------------------------------------------------------------------------- #
# F4  end releases
# --------------------------------------------------------------------------- #
def test_f04_end_releases():
    """End releases (SAP2000 Verification Example 1-007 "End Releases"):
    (a) fixed-fixed supports with a moment release at end j behave as a
    propped cantilever: central load P gives R_j = 5P/16, M_i = 3PL/16
    (AISC Beam Diagrams case 13); (b) a fixed-fixed beam with an internal
    hinge at mid-span (release Mj on the left half) is two cantilevers each
    carrying P/2: delta = (P/2)(L/2)^3/3EI = PL^3/48EI."""
    L, P, E, I = 8.0, 40.0, 2.0e8, 1.0e-4
    m = new_model("F4a", E)
    section(m, "S", 1e-2, I)
    planar_xz(m)
    m.add_member("beam", "S", (0, 0, 0), (L / 2, 0, 0), story="Story1",
                 uid="B1")
    m.add_member("beam", "S", (L / 2, 0, 0), (L, 0, 0), story="Story1",
                 uid="B2", releases="Mj")
    support(m, (0, 0, 0))
    support(m, (L, 0, 0))
    load(m, "P", (L / 2, 0, 0), fz=-P)
    m.add_case("P", {"P": 1.0})
    eng, r = run_case(m, "P")
    Pn = "End releases: propped beam via Mj release"
    S = "SAP2000 Verification Ex. 1-007; AISC Beam Diagrams case 13"
    check(Pn, S, "reaction at released end 5P/16", 5 * P / 16,
          r.reactions[tag_at(eng, (L, 0, 0))][2], EXACT, family=FAM)
    check(Pn, S, "fixed-end moment 3PL/16", 3 * P * L / 16,
          abs(r.reactions[tag_at(eng, (0, 0, 0))][4]), EXACT, family=FAM)
    check(Pn, S, "moment at released end", 0.0,
          r.reactions[tag_at(eng, (L, 0, 0))][4], EXACT, family=FAM,
          abs_floor=1e-6 * P * L)

    m = new_model("F4b", E)
    section(m, "S", 1e-2, I)
    planar_xz(m)
    m.add_member("beam", "S", (0, 0, 0), (L / 2, 0, 0), story="Story1",
                 uid="B1", releases="Mj")
    m.add_member("beam", "S", (L / 2, 0, 0), (L, 0, 0), story="Story1",
                 uid="B2")
    support(m, (0, 0, 0))
    support(m, (L, 0, 0))
    load(m, "P", (L / 2, 0, 0), fz=-P)
    m.add_case("P", {"P": 1.0})
    eng, r = run_case(m, "P")
    check("End releases: internal hinge (Gerber) beam", S.split(";")[0],
          "hinge deflection PL^3/48EI", -P * L ** 3 / (48 * E * I),
          disp(eng, r, (L / 2, 0, 0), 2), EXACT, family=FAM)


# --------------------------------------------------------------------------- #
# F5  rigid end offsets
# --------------------------------------------------------------------------- #
def test_f05_end_offsets():
    """Rigid end offsets (SAP2000 Verification Example 1-006 "End Offsets"
    / ETABS rigid-zone factor): fixed-fixed beam, rigid zones a at both
    ends, central load P.  The clear span l = L - 2a is a fixed-fixed beam:
    delta = P l^3/192EI; support moment = P l/8 + (P/2) a (rigid-arm shear
    transfer)."""
    L, a, P, E, I = 8.0, 0.5, 40.0, 2.0e8, 1.0e-4
    m = new_model("F5", E)
    section(m, "S", 1e-2, I)
    planar_xz(m)
    m.add_member("beam", "S", (0, 0, 0), (L / 2, 0, 0), story="Story1",
                 uid="B1", rigid_i=a)
    m.add_member("beam", "S", (L / 2, 0, 0), (L, 0, 0), story="Story1",
                 uid="B2", rigid_j=a)
    support(m, (0, 0, 0))
    support(m, (L, 0, 0))
    load(m, "P", (L / 2, 0, 0), fz=-P)
    m.add_case("P", {"P": 1.0})
    eng, r = run_case(m, "P")
    l = L - 2 * a
    Pn = "Rigid end offsets: fixed-fixed beam"
    S = "SAP2000 Verification Ex. 1-006 (end offsets); fixed-fixed beam"
    tol = 1e-5     # rigid arm modelled as EI x 1e6 (CONTRACT v0.9)
    check(Pn, S, "mid-span deflection P l^3/192EI",
          -P * l ** 3 / (192 * E * I), disp(eng, r, (L / 2, 0, 0), 2),
          tol, family=FAM)
    check(Pn, S, "support moment P l/8 + P a/2", P * l / 8 + P * a / 2,
          abs(r.reactions[tag_at(eng, (0, 0, 0))][4]), tol, family=FAM)


# --------------------------------------------------------------------------- #
# F6  temperature loading
# --------------------------------------------------------------------------- #
def test_f06_temperature_loading():
    """Temperature loading (SAP2000 Verification Example 1-002
    "Temperature Loading": uniform temperature change and temperature
    gradient on cantilever / propped beams vs hand calculation):
    (a) fixed-fixed bar, uniform dT: N = -E A alpha dT;
    (b) cantilever, gradient g (dT/h through the depth): tip deflection
        alpha g L^2/2, tip rotation alpha g L, no internal force;
    (c) propped cantilever, gradient g: prop reaction 3 E I alpha g/(2L)."""
    L, E, A, I, alpha = 5.0, 2.0e8, 4e-3, 2e-5, 1.2e-5
    dT, g = 30.0, 50.0
    Pn = "Temperature loading"
    S = "SAP2000 Verification Ex. 1-002 (temperature loading), hand calc"
    # (a)
    m = new_model("F6a", E)
    m.thermal_alpha = alpha
    section(m, "S", A, I)
    planar_xz(m)
    m.add_member("beam", "S", (0, 0, 0), (L, 0, 0), story="Story1", uid="B")
    support(m, (0, 0, 0))
    support(m, (L, 0, 0))
    m.pattern("T")
    m.add_thermal_load("T", "B", dT)
    m.add_case("T", {"T": 1.0})
    eng, r = run_case(m, "T")
    check(Pn, S, "restrained axial force E A alpha dT", E * A * alpha * dT,
          abs(r.reactions[tag_at(eng, (0, 0, 0))][0]), EXACT, family=FAM)
    # (b)/(c)
    for propped in (False, True):
        m = new_model("F6b", E)
        m.thermal_alpha = alpha
        section(m, "S", A, I)
        planar_xz(m)
        chain(m, "S", (0, 0, 0), (L, 0, 0), 2, "B")
        support(m, (0, 0, 0))
        if propped:
            support(m, (L, 0, 0), (0, 1, 1, 0, 0, 0))
        m.pattern("T")
        for uid in ("B1", "B2"):
            m.add_thermal_load("T", uid, 0.0, grad2=g)
        m.add_case("T", {"T": 1.0})
        eng, r = run_case(m, "T")
        if not propped:
            check(Pn, S, "cantilever gradient: tip deflection alpha g L^2/2",
                  alpha * g * L ** 2 / 2,
                  abs(disp(eng, r, (L, 0, 0), 2)), EXACT, family=FAM)
            check(Pn, S, "cantilever gradient: tip rotation alpha g L",
                  alpha * g * L, abs(disp(eng, r, (L, 0, 0), 4)), EXACT,
                  family=FAM)
        else:
            check(Pn, S, "propped gradient: prop force 3EI alpha g/2L",
                  3 * E * I * alpha * g / (2 * L),
                  abs(r.reactions[tag_at(eng, (L, 0, 0))][2]), EXACT,
                  family=FAM)


# --------------------------------------------------------------------------- #
# F7  support settlement
# --------------------------------------------------------------------------- #
def test_f07_support_settlement():
    """Displacement loading (SAP2000 Verification Example 1-005
    "Displacement Loading" -- support settlement): two equal spans L, the
    middle support settles Delta.  Flexibility method on the simple span 2L:
    R_B = 6 E I Delta / L^3 (pulling down), R_A = R_C = 3 E I Delta / L^3,
    M_B = 3 E I Delta / L^2."""
    L, E, I, D = 6.0, 2.0e8, 1.0e-4, 0.01
    m = new_model("F7", E)
    section(m, "S", 1e-2, I)
    planar_xz(m)
    m.add_member("beam", "S", (0, 0, 0), (L, 0, 0), story="Story1", uid="B1")
    m.add_member("beam", "S", (L, 0, 0), (2 * L, 0, 0), story="Story1",
                 uid="B2")
    support(m, (0, 0, 0), PINNED)
    support(m, (L, 0, 0), (0, 1, 1, 0, 0, 0))
    support(m, (2 * L, 0, 0), (0, 1, 1, 0, 0, 0))
    m.pattern("S").ground_displacements.append(
        GroundDisplacement((L, 0, 0), uz=-D))
    m.add_case("S", {"S": 1.0})
    eng, r = run_case(m, "S")
    Pn = "Support settlement, two-span beam"
    S = "SAP2000 Verification Ex. 1-005 (displacement loading), hand calc"
    check(Pn, S, "settled-support reaction -6EI Delta/L^3",
          -6 * E * I * D / L ** 3, r.reactions[tag_at(eng, (L, 0, 0))][2],
          EXACT, family=FAM)
    check(Pn, S, "end reaction 3EI Delta/L^3", 3 * E * I * D / L ** 3,
          r.reactions[tag_at(eng, (0, 0, 0))][2], EXACT, family=FAM)
    check(Pn, S, "M_B = 3EI Delta/L^2", 3 * E * I * D / L ** 2,
          abs(r.member_forces["B1"][11]), EXACT, family=FAM)
    check(Pn, S, "imposed displacement reproduced", -D,
          disp(eng, r, (L, 0, 0), 2), EXACT, family=FAM)


# --------------------------------------------------------------------------- #
# F8  P-Delta: CSI cantilevered-column test problem
# --------------------------------------------------------------------------- #
def _beam_column_exact(H, P, EI, L):
    """Exact beam-column (Timoshenko & Gere, *Theory of Elastic Stability*
    Sec. 1.11): cantilever, axial compression P, tip lateral load H."""
    k = math.sqrt(P / EI)
    d = H / (k * P) * (math.tan(k * L) - k * L)
    return d, H * L + P * d


@pytest.mark.parametrize("geometric", ["pdelta", "corotational"])
def test_f08_pdelta_cantilever_column(geometric):
    """CSI test problem "P-Delta effect for a cantilevered column": L = 10 m,
    0.1 m square, E = 30 GPa, axial load P = 0.70 Pcr (Pcr = pi^2 EI/4L^2)
    plus a lateral tip load giving a 0.06 m linear deflection.  Reference:
    the exact beam-column solution (Timoshenko & Gere Sec. 1.11)
    delta = H/(kP)(tan kL - kL), M_base = H L + P delta.  SkyFrame's P-Delta
    is the OpenSees chord ("string") geometric stiffness, so the column is
    meshed (as CSI recommends); 10 segments converge to < 0.6%."""
    L, b, E = 10.0, 0.1, 30.0e6
    I = b ** 4 / 12
    EI = E * I
    Pcr = math.pi ** 2 * EI / (4 * L * L)
    P = 0.7 * Pcr
    H = 0.06 * 3 * EI / L ** 3                 # linear tip deflection 0.06
    d_ref, M_ref = _beam_column_exact(H, P, EI, L)
    res = {}
    for n in (2, 4, 10):
        m = new_model("F8", E, nu=0.2)
        section(m, "S", b * b, I)
        planar_xz(m)
        chain(m, "S", (0, 0, 0), (0, 0, L), n, "C", kind="column")
        support(m, (0, 0, 0))
        load(m, "PH", (0, 0, L), fx=H, fz=-P)
        m.add_case("PH", {"PH": 1.0}, geometric=geometric)
        eng, r = run_case(m, "PH")
        res[n] = (disp(eng, r, (0, 0, L), 0),
                  abs(r.reactions[tag_at(eng, (0, 0, 0))][4]))
    # monotone mesh convergence toward the exact value
    e = {n: abs(res[n][0] - d_ref) for n in res}
    assert e[10] < e[4] < e[2]
    Pn = f"P-Delta cantilever column ({geometric}, 10 segments)"
    S = ("CSI test problem 'P-Delta effect for a cantilevered column'; "
         "exact: Timoshenko & Gere Sec. 1.11")
    tol = 0.006
    check(Pn, S, "tip deflection", d_ref, res[10][0], tol, family=FAM,
          note=f"2/4/10 seg: {res[2][0]:.4f}/{res[4][0]:.4f}/"
               f"{res[10][0]:.4f} m")
    check(Pn, S, "base moment H L + P delta", M_ref, res[10][1], tol,
          family=FAM)
    if geometric == "pdelta":
        # the residual 0.5% is the formulation (chord "P-Delta" stiffness
        # without the P-small-delta member term), NOT a defect: an
        # independent numpy assembly of K - (P/l)[1 -1; -1 1] on the same
        # 10 segments reproduces SkyFrame to round-off
        check("P-Delta column vs same-formulation numpy assembly",
              "Wilson, Static & Dynamic Analysis of Structures Ch. 11 "
              "(string stiffness)", "tip deflection (10 seg)",
              _string_stiffness_tip(H, P, EI, L, 10), res[10][0], 1e-6,
              family=FAM)


def _string_stiffness_tip(H, P, EI, L, n):
    """Independent numpy solution of the linearised P-Delta cantilever:
    Euler beam stiffness minus the chord geometric stiffness P/l on the
    sway translations of each of n segments (Wilson Ch. 11)."""
    l = L / n
    K = np.zeros((2 * n + 2, 2 * n + 2))
    ke = EI / l ** 3 * np.array([[12, 6 * l, -12, 6 * l],
                                 [6 * l, 4 * l * l, -6 * l, 2 * l * l],
                                 [-12, -6 * l, 12, -6 * l],
                                 [6 * l, 2 * l * l, -6 * l, 4 * l * l]])
    kg = P / l * np.array([[1, 0, -1, 0], [0, 0, 0, 0],
                           [-1, 0, 1, 0], [0, 0, 0, 0]])
    for e in range(n):
        idx = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        K[np.ix_(idx, idx)] += ke - kg
    f = np.zeros(2 * n + 2)
    f[-2] = H
    return np.linalg.solve(K[2:, 2:], f[2:])[-2]


# --------------------------------------------------------------------------- #
# F9  linear buckling: Euler column + portal frame sway buckling
# --------------------------------------------------------------------------- #
def test_f09_euler_buckling_pinned_column():
    """Euler buckling of a pin-ended column (SAP2000 Verification "Buckling"
    examples; Timoshenko & Gere Sec. 2.1): P_cr = pi^2 E I / L^2.  Consistent
    geometric stiffness converges from above as h^4 -- 8 segments < 0.1%."""
    L, E, I = 6.0, 2.0e8, 1.0e-5
    vals = {}
    for n in (2, 4, 8):
        m = new_model("F9", E)
        section(m, "S", 1e-2, I, I, 2 * I)
        chain(m, "S", (0, 0, 0), (0, 0, L), n, "C", kind="column")
        support(m, (0, 0, 0), (1, 1, 1, 0, 0, 1))
        support(m, (0, 0, L), (1, 1, 0, 0, 0, 0))
        load(m, "G", (0, 0, L), fz=-1.0)
        m.validate()
        vals[n] = buckling_analysis(m, {"G": 1.0}, num_modes=2).factors[0]
    Pcr = math.pi ** 2 * E * I / L ** 2
    assert vals[2] > vals[4] > vals[8] > Pcr          # from above
    check("Euler buckling, pinned-pinned column (8 seg)",
          "Timoshenko & Gere Sec. 2.1; SAP2000 buckling examples",
          "P_cr = pi^2 EI/L^2", Pcr, vals[8], 1e-3, family=FAM,
          note=f"2/4/8 seg errors: " + "/".join(
              f"{100 * (vals[n] / Pcr - 1):.3f}%" for n in (2, 4, 8)))


def _portal_sway_buckling_phi(r):
    """Exact sway-buckling parameter phi = h sqrt(P/EIc) of a fixed-base
    portal (two columns, beam without axial load) from the stability
    functions s, c (Livesley & Chandler / Timoshenko & Gere Sec. 2.8):
    det [[s + 6r, -s(1+c)], [-s(1+c), 2s(1+c) - phi^2]] = 0,
    r = (Ib/L)/(Ic/h)."""
    def det(phi):
        sn, cs = math.sin(phi), math.cos(phi)
        s = phi * (sn - phi * cs) / (2 - 2 * cs - phi * sn)
        c = (phi - sn) / (sn - phi * cs)
        return (s + 6 * r) * (2 * s * (1 + c) - phi ** 2) - (s * (1 + c)) ** 2
    lo, hi = 0.5, 3.1
    assert det(lo) * det(hi) < 0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if det(lo) * det(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def test_f10_portal_frame_sway_buckling():
    """Elastic buckling of a fixed-base portal frame (SAP2000 Verification
    Example 1-019 "Buckling of a Rigid Frame"; exact reference from the
    stability-function determinant, Timoshenko & Gere Sec. 2.8): equal
    Ic = Ib, h = L, vertical loads P on both column tops.  Exact
    phi = 2.7165 -> P_cr = 7.379 EIc/h^2."""
    h = L = 4.0
    E, I = 2.0e8, 2.0e-5
    phi = _portal_sway_buckling_phi((I / L) / (I / h))
    Pcr = phi ** 2 * E * I / h ** 2
    m = new_model("F10", E)
    section(m, "S", 1.0, I, I, 2 * I)       # axially stiff (exact basis)
    chain(m, "S", (0, 0, 0), (0, 0, h), 4, "CA", kind="column")
    chain(m, "S", (L, 0, 0), (L, 0, h), 4, "CB", kind="column")
    chain(m, "S", (0, 0, h), (L, 0, h), 4, "BM")
    support(m, (0, 0, 0))
    support(m, (L, 0, 0))
    for x in (0.0, L):
        load(m, "G", (x, 0, h), fz=-1.0)
        # brace out of plane so the in-plane sway mode is the one measured
        support(m, (x, 0, h), (0, 1, 0, 0, 0, 0))
    m.validate()
    lam = buckling_analysis(m, {"G": 1.0}, num_modes=3).factors[0]
    check("Portal frame sway buckling (fixed base, Ib=Ic, h=L)",
          "SAP2000 Verification Ex. 1-019; Timoshenko & Gere Sec. 2.8",
          "P_cr per column (7.379 EI/h^2)", Pcr, lam, 1e-3, family=FAM,
          note=f"phi = {phi:.4f}")


# --------------------------------------------------------------------------- #
# F8b  model-wide (ETABS) P-Delta options on the same column
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("method", ["iterative_loads", "non_iterative_mass"])
@pytest.mark.parametrize("n", [1, 10])
def test_f08b_etabs_pdelta_options_column(method, n):
    """ETABS "Define > P-Delta Options" on the CSI cantilevered column
    (ETABS Verification P-Delta example class): the gravity P comes either
    from an iterated load combination ("iterative_loads") or from the mass
    above the story ("non_iterative_mass", P = g * m).  Both are the chord
    string stiffness, so the LATERAL linear case must equal the independent
    numpy string-stiffness solution on the same n segments (1e-6) -- and
    with 10 segments it is within 0.5% of the exact beam-column value.

    REGRESSION (fixed in this suite): "non_iterative_mass" shared the story
    P over every vertical member of the story by EA/L as if they were
    PARALLEL columns, so a column split into 10 stacked pieces gave each
    piece ~P/10 (tip 0.0645 m instead of 0.1971 m; amplification 1.07
    instead of 3.29).  Stacked pieces are now one series column line."""
    from skyframe.core.model import NodalMass
    L, b, E = 10.0, 0.1, 30.0e6
    I = b ** 4 / 12
    EI = E * I
    P = 0.7 * math.pi ** 2 * EI / (4 * L * L)
    H = 0.06 * 3 * EI / L ** 3
    m = new_model("F8b", E, nu=0.2)
    m.set_stories([L])
    section(m, "S", b * b, I)
    planar_xz(m)
    chain(m, "S", (0, 0, 0), (0, 0, L), n, "C", kind="column")
    support(m, (0, 0, 0))
    load(m, "G", (0, 0, L), fz=-P)
    load(m, "H", (0, 0, L), fx=H)
    m.add_case("H", {"H": 1.0})
    if method == "iterative_loads":
        m.pdelta_options = {"method": method, "load_factors": {"G": 1.0},
                            "max_iterations": 5, "tolerance": 1e-9,
                            "include_in": "all_linear"}
    else:
        m.pdelta_options = {"method": method, "load_factors": {},
                            "max_iterations": 2, "tolerance": 1e-3,
                            "include_in": "all_linear"}
        g = 9.80665
        m.nodal_masses.append(NodalMass((0, 0, L), mx=P / g, my=P / g))
    eng, r = run_case(m, "H")
    u = disp(eng, r, (0, 0, L), 0)
    Pn = f"ETABS P-Delta option '{method}', column in {n} piece(s)"
    S = ("ETABS P-Delta options; Wilson Ch. 11 string stiffness "
         "(independent numpy)")
    check(Pn, S, "lateral tip deflection vs string-stiffness numpy",
          _string_stiffness_tip(H, P, EI, L, n), u, 1e-6, family=FAM)
    if n == 10:
        check(Pn, "Timoshenko & Gere Sec. 1.11 (exact beam-column)",
              "lateral tip deflection vs exact", _beam_column_exact(
                  H, P, EI, L)[0], u, 6e-3, family=FAM)


# --------------------------------------------------------------------------- #
# F12  span loads on a fixed-fixed beam (fixed-end actions + stations)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", ["partial_udl", "triangle", "point"])
def test_f12_fixed_beam_span_loads(kind):
    """Fixed-fixed beam under span loads (SAP2000 Verification "frame -
    span loads" class; fixed-end moments from AISC Manual Beam Diagrams /
    Roark Table 8.1):
    partial UDL on [0, L/2]: M_A = 11wL^2/192, M_B = 5wL^2/192;
    triangle 0 -> w: M_A = wL^2/30, M_B = wL^2/20;
    point P at a = L/3: M_A = Pab^2/L^2, M_B = Pa^2b/L^2 and the station
    deflection at x = 0.3L < a, P b^2 x^2 (3aL - 3ax - bx) / (6 E I L^3)."""
    E, I, L, w, P = 2.0e8, 1e-4, 6.0, 10.0, 30.0
    m = new_model("F12", E)
    section(m, "S", 1e-2, I)
    planar_xz(m)
    m.add_member("beam", "S", (0, 0, 0), (L, 0, 0), story="Story1", uid="B1")
    support(m, (0, 0, 0))
    support(m, (L, 0, 0))
    a, b = L / 3, 2 * L / 3
    ld = {"partial_udl": MemberLoad("B1", kind="udl", w=w, a=0.0, b=0.5),
          "triangle": MemberLoad("B1", kind="trapezoid", w=0.0, w2=w),
          "point": MemberLoad("B1", kind="point", w=P, a=1 / 3)}[kind]
    ref = {"partial_udl": (11 * w * L * L / 192, 5 * w * L * L / 192),
           "triangle": (w * L * L / 30, w * L * L / 20),
           "point": (P * a * b * b / L ** 2, P * a * a * b / L ** 2)}[kind]
    m.pattern("W").member_loads.append(ld)
    m.add_case("W", {"W": 1.0})
    eng, r = run_case(m, "W")
    Pn = f"Fixed-fixed beam, span load: {kind}"
    S = "AISC Beam Diagrams / Roark Table 8.1; SAP2000 span-load class"
    check(Pn, S, "fixed-end moment M_A", ref[0],
          abs(r.reactions[tag_at(eng, (0, 0, 0))][4]), EXACT, family=FAM)
    check(Pn, S, "fixed-end moment M_B", ref[1],
          abs(r.reactions[tag_at(eng, (L, 0, 0))][4]), EXACT, family=FAM)
    if kind == "point":
        st = r.member_deflections["B1"]
        x = st["x"][3]                       # 11 stations: x = 0.3 L
        assert abs(x - 0.3 * L) < 1e-12
        ref_d = (P * b * b * x * x * (3 * a * L - 3 * a * x - b * x)
                 / (6 * E * I * L ** 3))
        check(Pn, S, "station deflection at x = 0.3L", ref_d,
              abs(st["dy"][3]), EXACT, family=FAM)


# --------------------------------------------------------------------------- #
# F11  large displacement: Mattiasson elastica
# --------------------------------------------------------------------------- #
MATTIASSON = {     # PL^2/EI : (w/L, u/L)   Mattiasson (1981) Table 1
    1.0: (0.30172, 0.05643),
    2.0: (0.49346, 0.16064),
    5.0: (0.71379, 0.38763),
    10.0: (0.81061, 0.55500),
}


@pytest.mark.parametrize("alpha", sorted(MATTIASSON))
def test_f11_mattiasson_elastica(alpha):
    """Large-displacement cantilever under a tip load (Mattiasson, K. (1981)
    "Numerical results from large deflection beam and frame problems
    analysed by means of elliptic integrals", IJNME 17:145-153, Table 1;
    also SAP2000 Verification "large displacement" frame examples).  20
    corotational elements; tolerance 1% (corotational Euler beam converges
    as h^2; 20 elements measured well inside)."""
    L, E, b = 2.0, 2.0e8, 0.05
    I = b ** 4 / 12
    n = 20
    m = new_model("F11", E)
    section(m, "S", b * b, I)
    planar_xz(m)
    chain(m, "S", (0, 0, 0), (L, 0, 0), n, "B")
    support(m, (0, 0, 0))
    P = alpha * E * I / L ** 2
    load(m, "P", (L, 0, 0), fz=-P)
    m.add_case("P", {"P": 1.0}, geometric="corotational")
    eng, r = run_case(m, "P")
    w_ref, u_ref = MATTIASSON[alpha]
    Pn = f"Mattiasson elastica, PL^2/EI = {alpha:g}"
    S = "Mattiasson (1981) IJNME 17 Table 1"
    check(Pn, S, "tip vertical w/L", w_ref,
          -disp(eng, r, (L, 0, 0), 2) / L, 0.01, family=FAM)
    check(Pn, S, "tip horizontal u/L", u_ref,
          -disp(eng, r, (L, 0, 0), 0) / L, 0.01, family=FAM)
