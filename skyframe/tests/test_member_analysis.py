"""Deep validation of v0.2 frame-member analysis (releases, MemberLoad,
member_stations) against closed-form beam mechanics.

Every expected number is derived in-test from first principles; every one of
those closed forms was additionally cross-checked against an independent
800-element Euler-beam FE (numpy, consistent load vectors) before being
pinned here (scratch script derive_beams.py; agreement 1e-7..1e-9).

Tolerance policy (per-test comments give the specific justification):
  * reactions / fixed-end forces / station values derived by exact statics
    from end forces  ->  tight (1e-9 .. 1e-6 relative)
  * quantities that depend on the engine's load-transfer bookkeeping for
    partial/varying loads  ->  1e-4 of the diagram maximum (spec tolerance)

Sign conventions: the CONTRACT does not pin the sign convention of the
station diagrams, so each diagram is compared through ONE global +/-1 fitted
at the largest-magnitude analytic station (see _v02_utils.sign_fit); after
that single fit every station must match pointwise, so shape, magnitude and
*relative* signs (e.g. hogging ends vs sagging midspan) are all enforced.

Models: hand-built BuildingModel, explicit PointSupports,
rigid_diaphragms=False.  Units kN / m / tonne / s per CONTRACT.md.
"""

import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")

from _v02_utils import (          # noqa: E402
    E_CONC, model_mod, FrameSection, NodalLoad, PointSupport,
    has_member_load, has_releases,
    new_model, finish, run, find_node,
    add_gravity_udl, add_member_load,
    stations, check_x_grid, sign_fit, assert_station_match,
)

needs_member_load = pytest.mark.skipif(
    not has_member_load(), reason="v0.2 MemberLoad API not present yet")
needs_releases = pytest.mark.skipif(
    not has_releases(), reason="FrameMember.releases not present yet")

FIX = (1, 1, 1, 1, 1, 1)
PIN = (1, 1, 1, 1, 0, 0)     # translations + torsion about the member axis
ROLLER = (0, 1, 1, 0, 0, 0)  # uy+uz: vertical roller, laterally guided


def _beam_model(name, L, b=0.35, h=0.35):
    """One horizontal beam along +X at z=0 (square by default so results are
    independent of the engine's local-axis orientation)."""
    mdl = new_model(name, [1.0])
    mdl.add_section(FrameSection.rectangular("BM", "CONC", b, h))
    mdl.add_member("beam", "BM", (0, 0, 0), (L, 0, 0), story="Story1", uid="B1")
    return mdl


# --------------------------------------------------------------------------- #
# 1. fixed-fixed beam, full UDL
# --------------------------------------------------------------------------- #
def test_fixed_fixed_beam_full_udl():
    """Fixed-fixed beam, UDL w:
        R = wL/2 (each end),      M_end = wL^2/12 (hogging),
        M_mid = wL^2/24 (sagging),  M(x) = wLx/2 - wx^2/2 - wL^2/12,
        V(x) = wL/2 - wx.
    All are exact statics for a prismatic member (engine stations are
    'computed by statics from end forces + member loads', and the FE end
    forces for a full-span UDL with consistent load vectors are exact), so
    1e-6 relative is generous."""
    L, w = 10.0, 15.0
    mdl = _beam_model("FF-UDL", L)
    mdl.supports.append(PointSupport((0, 0, 0), FIX))
    mdl.supports.append(PointSupport((L, 0, 0), FIX))
    add_gravity_udl(mdl, "D", "B1", w)
    mdl.add_case("D", {"D": 1.0})
    finish(mdl, (L, 0, 0))

    d = run(mdl)
    case = d["cases"]["D"]

    # reactions: wL/2 up at each clamp (upward-positive per contract sample)
    na, nb = find_node(d, (0, 0, 0)), find_node(d, (L, 0, 0))
    assert case["reactions"][na][2] == pytest.approx(w * L / 2, rel=1e-6)
    assert case["reactions"][nb][2] == pytest.approx(w * L / 2, rel=1e-6)
    assert case["base"]["FZ"] == pytest.approx(w * L, rel=1e-9)

    st = stations(d, "D", "B1")
    # bending: closed form, all 11 stations, sagging-positive reference
    assert_station_match(
        st, "M3", lambda x: w * L * x / 2 - w * x**2 / 2 - w * L**2 / 12,
        L, rel=1e-6, label="FF UDL")
    M3 = np.asarray(st["M3"], float)
    assert abs(M3[0]) == pytest.approx(w * L**2 / 12, rel=1e-6)    # station 0
    assert abs(M3[10]) == pytest.approx(w * L**2 / 12, rel=1e-6)   # station 10
    assert abs(M3[5]) == pytest.approx(w * L**2 / 24, rel=1e-6)    # midspan
    # hogging at ends, sagging at midspan: opposite signs whatever the
    # engine's global convention is
    assert M3[0] * M3[5] < 0 and M3[10] * M3[5] < 0

    # shear: V(x) = wL/2 - wx, end shears wL/2
    assert_station_match(st, "V2", lambda x: w * L / 2 - w * x,
                         L, rel=1e-6, label="FF UDL")
    V2 = np.asarray(st["V2"], float)
    assert abs(V2[0]) == pytest.approx(w * L / 2, rel=1e-6)
    assert abs(V2[10]) == pytest.approx(w * L / 2, rel=1e-6)


# --------------------------------------------------------------------------- #
# 2. fixed-fixed beam, central point load
# --------------------------------------------------------------------------- #
@needs_member_load
def test_fixed_fixed_beam_central_point_load():
    """Fixed-fixed beam, P at midspan:
        FEM = PL/8 (hogging both ends),  M_mid = +PL/8 (sagging),
        V = +P/2 on [0, L/2), -P/2 on (L/2, L]  ->  V jumps by P at the load.
    Exact statics -> 1e-6.  Shear is checked one station either side of the
    load (x = 0.4L and 0.6L) because the station AT the load sits on the
    discontinuity."""
    L, P = 10.0, 40.0
    mdl = _beam_model("FF-P", L)
    mdl.supports.append(PointSupport((0, 0, 0), FIX))
    mdl.supports.append(PointSupport((L, 0, 0), FIX))
    add_member_load(mdl, "D", member_uid="B1", kind="point", w=P, a=0.5)
    mdl.add_case("D", {"D": 1.0})
    finish(mdl, (L, 0, 0))

    d = run(mdl)
    case = d["cases"]["D"]
    na, nb = find_node(d, (0, 0, 0)), find_node(d, (L, 0, 0))
    assert case["reactions"][na][2] == pytest.approx(P / 2, rel=1e-6)
    assert case["reactions"][nb][2] == pytest.approx(P / 2, rel=1e-6)
    assert case["base"]["FZ"] == pytest.approx(P, rel=1e-9)

    st = stations(d, "D", "B1")
    # M(x) = -PL/8 + (P/2) x            for x <= L/2, symmetric after
    assert_station_match(
        st, "M3",
        lambda x: -P * L / 8 + P / 2 * min(x, L - x),
        L, rel=1e-6, label="FF central P")
    M3 = np.asarray(st["M3"], float)
    assert abs(M3[0]) == pytest.approx(P * L / 8, rel=1e-6)   # FEM = PL/8
    assert abs(M3[10]) == pytest.approx(P * L / 8, rel=1e-6)
    assert abs(M3[5]) == pytest.approx(P * L / 8, rel=1e-6)   # midspan PL/8
    assert M3[0] * M3[5] < 0                                  # hog vs sag

    V2 = np.asarray(st["V2"], float)
    assert abs(V2[4]) == pytest.approx(P / 2, rel=1e-6)       # x = 0.4L
    assert abs(V2[6]) == pytest.approx(P / 2, rel=1e-6)       # x = 0.6L
    # the jump across the load equals P (V changes sign there)
    assert abs(V2[4] - V2[6]) == pytest.approx(P, rel=1e-6)
    assert V2[4] * V2[6] < 0


# --------------------------------------------------------------------------- #
# 3. propped cantilever (fixed-pinned via "Mj" release), full UDL
# --------------------------------------------------------------------------- #
@needs_releases
def test_propped_cantilever_release_mj_udl():
    """Fixed at i, moment-released at j ("Mj"), propped (uz) at j, UDL w:
        R_fixed = 5wL/8,  R_prop = 3wL/8,  M_fixed = wL^2/8 (hogging),
        M(x) = (5wL/8) x - wL^2/8 - w x^2/2,   M(released end) = 0,
        M_max = 9 w L^2 / 128  at  x = 5L/8.
    (Force method: redundant R_prop from delta_prop = wL^4/8EI = R L^3/3EI;
    verified against the independent FE to 1e-7.)  Exact statics -> 1e-6;
    the released-end moment must vanish to 1e-8 * wL^2 (spec)."""
    L, w = 10.0, 12.0
    mdl = _beam_model("propped", L)
    mdl.members[0].releases = "Mj"
    mdl.supports.append(PointSupport((0, 0, 0), FIX))
    # prop: uz plus the three rotation DOFs.  The rotations at this node are
    # DANGLING -- the "Mj" release disconnects the member's rotational
    # stiffness from the node and no other element is attached -- so
    # restraining them only removes zero-stiffness DOFs; the corresponding
    # reaction moments must be (and are asserted) ~zero: still a true prop.
    mdl.supports.append(PointSupport((L, 0, 0), (0, 0, 1, 1, 1, 1)))
    add_gravity_udl(mdl, "D", "B1", w)
    mdl.add_case("D", {"D": 1.0})
    finish(mdl, (L, 0, 0))

    d = run(mdl)
    case = d["cases"]["D"]
    ni, nj = find_node(d, (0, 0, 0)), find_node(d, (L, 0, 0))
    assert case["reactions"][ni][2] == pytest.approx(5 * w * L / 8, rel=1e-6)
    assert case["reactions"][nj][2] == pytest.approx(3 * w * L / 8, rel=1e-6)
    # clamp reaction moment magnitude = wL^2/8 (about global Y for a beam
    # along X); compare the moment-resultant so column-axis conventions
    # cannot bite: only MY should be non-trivial.
    Mres = math.hypot(case["reactions"][ni][3], case["reactions"][ni][4])
    assert Mres == pytest.approx(w * L**2 / 8, rel=1e-6)
    # the restrained-but-dangling rotations at the prop attract no moment
    assert max(abs(v) for v in case["reactions"][nj][3:]) < 1e-8 * w * L**2

    st = stations(d, "D", "B1")
    R1 = 5 * w * L / 8
    assert_station_match(
        st, "M3", lambda x: R1 * x - w * L**2 / 8 - w * x**2 / 2,
        L, rel=1e-6, label="propped cantilever")
    M3 = np.asarray(st["M3"], float)
    assert abs(M3[0]) == pytest.approx(w * L**2 / 8, rel=1e-6)
    assert abs(M3[10]) < 1e-8 * w * L**2          # released end: M == 0
    # max sagging moment: M(x) is exactly quadratic, so a parabola through
    # the three stations around x = 5L/8 (0.5L, 0.6L, 0.7L) reproduces the
    # analytic maximum exactly.  Vertex of the fit must be 9wL^2/128 @ 5L/8.
    xs = check_x_grid(st, L)
    s = sign_fit(M3, [R1 * x - w * L**2 / 8 - w * x**2 / 2 for x in xs])
    c2, c1, c0 = np.polyfit(xs[5:8], s * M3[5:8], 2)
    x_peak = -c1 / (2 * c2)
    m_peak = c0 + c1 * x_peak + c2 * x_peak**2
    assert x_peak == pytest.approx(5 * L / 8, rel=1e-6)
    assert m_peak == pytest.approx(9 * w * L**2 / 128, rel=1e-6)

    assert_station_match(st, "V2", lambda x: R1 - w * x, L, rel=1e-6,
                         label="propped cantilever")


# --------------------------------------------------------------------------- #
# 4. both-ends-released beam == simply supported (physical equivalence)
# --------------------------------------------------------------------------- #
@needs_releases
def test_both_end_releases_equal_simply_supported():
    """Beam clamped at both supports but with BOTH end moments released must
    be mechanically identical to the same beam on physical pins:
        M_mid = wL^2/8,  end M = 0,  delta_mid = 5wL^4/384EI.
    Built as 2 members (midspan node exists) each way; node displacements of
    the released model must match the pinned model to 1e-6 relative (same
    stiffness matrix after static condensation -> agreement is exact up to
    solver round-off; 1e-6 leaves room for the release implementation)."""
    L, w = 8.0, 12.0
    b, h = 0.3, 0.6
    I = b * h**3 / 12.0
    delta = 5 * w * L**4 / (384 * E_CONC * I)

    def two_member_beam(name):
        mdl = new_model(name, [1.0])
        mdl.add_section(FrameSection.rectangular("BM", "CONC", b, h))
        mdl.add_member("beam", "BM", (0, 0, 0), (L / 2, 0, 0), story="Story1", uid="B1")
        mdl.add_member("beam", "BM", (L / 2, 0, 0), (L, 0, 0), story="Story1", uid="B2")
        add_gravity_udl(mdl, "D", "B1", w)
        add_gravity_udl(mdl, "D", "B2", w)
        mdl.add_case("D", {"D": 1.0})
        finish(mdl, (L / 2, 0, 0))
        return mdl

    rel_mdl = two_member_beam("released")
    rel_mdl.members[0].releases = "Mi"      # outer end of B1
    rel_mdl.members[1].releases = "Mj"      # outer end of B2
    rel_mdl.supports.append(PointSupport((0, 0, 0), FIX))
    rel_mdl.supports.append(PointSupport((L, 0, 0), FIX))

    pin_mdl = two_member_beam("pinned")
    pin_mdl.supports.append(PointSupport((0, 0, 0), PIN))
    pin_mdl.supports.append(PointSupport((L, 0, 0), ROLLER))

    dr, dp = run(rel_mdl), run(pin_mdl)
    cr, cp = dr["cases"]["D"], dp["cases"]["D"]

    # released model, SS answers
    nm = find_node(dr, (L / 2, 0, 0))
    assert cr["node_disp"][nm][2] == pytest.approx(-delta, rel=1e-6)
    na = find_node(dr, (0, 0, 0))
    assert cr["reactions"][na][2] == pytest.approx(w * L / 2, rel=1e-6)

    st1 = stations(dr, "D", "B1")
    # span coordinate of B1 is x in [0, L/2]; SS beam: M(x) = wLx/2 - wx^2/2
    assert_station_match(st1, "M3", lambda x: w * L * x / 2 - w * x**2 / 2,
                         L / 2, rel=1e-6, label="released B1")
    M3 = np.asarray(st1["M3"], float)
    assert abs(M3[0]) < 1e-8 * w * L**2            # released end: M == 0
    assert abs(M3[10]) == pytest.approx(w * L**2 / 8, rel=1e-6)   # midspan
    st2 = stations(dr, "D", "B2")
    assert abs(np.asarray(st2["M3"], float)[10]) < 1e-8 * w * L**2

    # physical equivalence: every node displacement matches the pinned model
    for pt in [(0, 0, 0), (L / 2, 0, 0), (L, 0, 0)]:
        tr, tp = find_node(dr, pt), find_node(dp, pt)
        ur, up = cr["node_disp"][tr], cp["node_disp"][tp]
        for i in range(6):
            if pt == (0, 0, 0) and i in (4, 5):
                # the released model's clamp holds the NODE rotation at the
                # support (the member end rotates freely behind the release);
                # the pinned model's node rotates.  Skip the two bending
                # rotations at supports -- they are the one legitimate
                # difference between the two idealisations.
                continue
            if pt == (L, 0, 0) and i in (4, 5):
                continue
            assert ur[i] == pytest.approx(up[i], rel=1e-6, abs=1e-12), (
                f"node {pt} dof {i}: released={ur[i]!r} pinned={up[i]!r}")


# --------------------------------------------------------------------------- #
# 5. partial UDL on a simply supported beam
# --------------------------------------------------------------------------- #
@needs_member_load
def test_partial_udl_simply_supported():
    """SS beam, UDL w over [a, b] only.  With W = w(b-a), c = (a+b)/2:
        R1 = W (L - c) / L,   R2 = W c / L        (moments about supports)
        M(x) = R1 x                      for x <= a
             = R1 x - w (x-a)^2 / 2      for a <= x <= b
             = R2 (L - x)                for x >= b
    Cross-checked against the independent fine-mesh FE to ~1e-7 before
    pinning.  Reactions are exact statics -> 1e-6; stations go through the
    engine's partial-load fixed-end-force bookkeeping -> 1e-4 of max M."""
    L, w, a, b = 10.0, 14.0, 2.5, 6.875
    W = w * (b - a)
    c = (a + b) / 2
    R1, R2 = W * (L - c) / L, W * c / L

    mdl = _beam_model("partial", L)
    mdl.supports.append(PointSupport((0, 0, 0), PIN))
    mdl.supports.append(PointSupport((L, 0, 0), ROLLER))
    add_member_load(mdl, "D", member_uid="B1", kind="udl", w=w, a=a / L, b=b / L)
    mdl.add_case("D", {"D": 1.0})
    finish(mdl, (L, 0, 0))

    d = run(mdl)
    case = d["cases"]["D"]
    n1, n2 = find_node(d, (0, 0, 0)), find_node(d, (L, 0, 0))
    assert case["reactions"][n1][2] == pytest.approx(R1, rel=1e-6)
    assert case["reactions"][n2][2] == pytest.approx(R2, rel=1e-6)
    assert case["base"]["FZ"] == pytest.approx(W, rel=1e-9)   # load conserved

    def M(x):
        if x <= a:
            return R1 * x
        if x <= b:
            return R1 * x - w * (x - a) ** 2 / 2
        return R2 * (L - x)

    def V(x):
        return R1 - w * min(max(x - a, 0.0), b - a)

    st = stations(d, "D", "B1")
    assert_station_match(st, "M3", M, L, rel=1e-4, label="partial UDL")
    assert_station_match(st, "V2", V, L, rel=1e-4, label="partial UDL")


# --------------------------------------------------------------------------- #
# 6. triangular load (0 -> w2) on a simply supported beam
# --------------------------------------------------------------------------- #
@needs_member_load
def test_triangular_load_simply_supported():
    """SS beam, w(x) = w2 x / L (trapezoid load with w1 = 0):
        total = w2 L / 2 at centroid 2L/3  ->  R1 = w2 L/6, R2 = w2 L/3
        M(x) = w2 L x / 6 - w2 x^3 / (6 L)
        M_max = w2 L^2 / (9 sqrt 3)  at  x = L / sqrt 3.
    Reactions are pure statics -> 1e-9 (spec).  Station moments are compared
    against the analytic M evaluated AT the station abscissae (1e-4 of max),
    which avoids any station-grid coarseness argument; the analytic maximum
    itself is verified against a dense evaluation of the same function."""
    L, w2 = 9.0, 15.0
    mdl = _beam_model("triangular", L)
    mdl.supports.append(PointSupport((0, 0, 0), PIN))
    mdl.supports.append(PointSupport((L, 0, 0), ROLLER))
    add_member_load(mdl, "D", member_uid="B1", kind="trapezoid",
                    w=0.0, w2=w2, a=0.0, b=1.0)
    mdl.add_case("D", {"D": 1.0})
    finish(mdl, (L, 0, 0))

    d = run(mdl)
    case = d["cases"]["D"]
    n1, n2 = find_node(d, (0, 0, 0)), find_node(d, (L, 0, 0))
    assert case["reactions"][n1][2] == pytest.approx(w2 * L / 6, rel=1e-9)
    assert case["reactions"][n2][2] == pytest.approx(w2 * L / 3, rel=1e-9)

    def M(x):
        return w2 * L * x / 6 - w2 * x**3 / (6 * L)

    # self-check: dense max of the analytic curve == closed-form Mmax at L/sqrt3
    xd = np.linspace(0, L, 20001)
    m_dense = max(M(x) for x in xd)
    assert m_dense == pytest.approx(w2 * L**2 / (9 * math.sqrt(3)), rel=1e-7)

    st = stations(d, "D", "B1")
    assert_station_match(st, "M3", M, L, rel=1e-4, label="triangular")
    # shear V(x) = R1 - w2 x^2 / (2L)
    assert_station_match(st, "V2", lambda x: w2 * L / 6 - w2 * x**2 / (2 * L),
                         L, rel=1e-4, label="triangular")


# --------------------------------------------------------------------------- #
# 7. point load at x = 0.3 L on a fixed-fixed beam
# --------------------------------------------------------------------------- #
@needs_member_load
def test_point_load_at_030L_fixed_fixed():
    """Fixed-fixed beam, P at a = 0.3L (b = 0.7L):
        FEM_i = P a b^2 / L^2   (hogging at the near end,  larger)
        FEM_j = P a^2 b / L^2   (hogging at the far end,   smaller)
        R1 = P b^2 (3a + b) / L^3,   R2 = P a^2 (a + 3b) / L^3
        M(x) = -FEM_i + R1 x [- P (x - a) beyond the load]
    Verified vs the independent FE to 1e-7.  Exact statics -> 1e-6."""
    L, P = 10.0, 55.0
    a = 0.3 * L
    b = L - a
    Mi = P * a * b**2 / L**2
    Mj = P * a**2 * b / L**2
    R1 = P * b**2 * (3 * a + b) / L**3
    R2 = P * a**2 * (a + 3 * b) / L**3

    mdl = _beam_model("FF-P03", L)
    mdl.supports.append(PointSupport((0, 0, 0), FIX))
    mdl.supports.append(PointSupport((L, 0, 0), FIX))
    add_member_load(mdl, "D", member_uid="B1", kind="point", w=P, a=0.3)
    mdl.add_case("D", {"D": 1.0})
    finish(mdl, (L, 0, 0))

    d = run(mdl)
    case = d["cases"]["D"]
    n1, n2 = find_node(d, (0, 0, 0)), find_node(d, (L, 0, 0))
    assert case["reactions"][n1][2] == pytest.approx(R1, rel=1e-6)
    assert case["reactions"][n2][2] == pytest.approx(R2, rel=1e-6)

    def M(x):
        m = -Mi + R1 * x
        return m if x <= a else m - P * (x - a)

    st = stations(d, "D", "B1")
    assert_station_match(st, "M3", M, L, rel=1e-6, label="FF P@0.3L")
    M3 = np.asarray(st["M3"], float)
    # end stations: FEM values with the asymmetry the formula demands,
    # both hogging (same sign as each other, opposite to under the load)
    assert abs(M3[0]) == pytest.approx(Mi, rel=1e-6)
    assert abs(M3[10]) == pytest.approx(Mj, rel=1e-6)
    assert M3[0] * M3[10] > 0
    assert M3[0] * M3[3] < 0            # x=0.3L (under load): sagging

    assert_station_match(
        st, "V2", lambda x: R1 if x < a else R1 - P, L, rel=1e-6,
        label="FF P@0.3L")


# --------------------------------------------------------------------------- #
# 8. two-span continuous beam, UDL both spans
# --------------------------------------------------------------------------- #
def test_two_span_continuous_beam_udl():
    """Two equal spans L on three supports, UDL w on both spans.
    Three-moment (Clapeyron) equation, spans equal, ends simply supported:
        M_A + 4 M_B + M_C = -6 (wL^3/24 + wL^3/24)/L,  M_A = M_C = 0
        =>  M_B = - w L^2 / 8               (hogging over middle support)
    Reactions from span statics:
        R_A = R_C = wL/2 + M_B/L = 3wL/8,   R_B = 2 (wL/2 - M_B/L) = 5wL/4
        Span 1: M(x) = (3wL/8) x - w x^2 / 2       (x from A)
    Verified vs an independent 1600-element FE to ~1e-7.  Center support is
    vertical-only per spec; two members share the middle node.  Exact
    statics -> 1e-6; the two members' station diagrams must agree AT the
    shared node in the raw engine values (continuity, no sign fit)."""
    L, w = 6.0, 18.0
    mdl = new_model("two-span", [1.0])
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.35, 0.35))
    mdl.add_member("beam", "BM", (0, 0, 0), (L, 0, 0), story="Story1", uid="S1")
    mdl.add_member("beam", "BM", (L, 0, 0), (2 * L, 0, 0), story="Story1", uid="S2")
    mdl.supports.append(PointSupport((0, 0, 0), PIN))
    mdl.supports.append(PointSupport((L, 0, 0), (0, 0, 1, 0, 0, 0)))   # vertical only
    mdl.supports.append(PointSupport((2 * L, 0, 0), ROLLER))
    add_gravity_udl(mdl, "D", "S1", w)
    add_gravity_udl(mdl, "D", "S2", w)
    mdl.add_case("D", {"D": 1.0})
    finish(mdl, (2 * L, 0, 0))

    d = run(mdl)
    case = d["cases"]["D"]
    nA = find_node(d, (0, 0, 0))
    nB = find_node(d, (L, 0, 0))
    nC = find_node(d, (2 * L, 0, 0))
    assert case["reactions"][nA][2] == pytest.approx(3 * w * L / 8, rel=1e-6)
    assert case["reactions"][nB][2] == pytest.approx(5 * w * L / 4, rel=1e-6)
    assert case["reactions"][nC][2] == pytest.approx(3 * w * L / 8, rel=1e-6)
    assert case["base"]["FZ"] == pytest.approx(2 * w * L, rel=1e-9)

    st1 = stations(d, "D", "S1")
    st2 = stations(d, "D", "S2")
    # span 1: M(x) = 3wL/8 x - w x^2/2 ;  span 2 mirrored
    assert_station_match(st1, "M3", lambda x: 3 * w * L / 8 * x - w * x**2 / 2,
                         L, rel=1e-6, label="two-span S1")
    assert_station_match(
        st2, "M3", lambda x: 3 * w * L / 8 * (L - x) - w * (L - x)**2 / 2,
        L, rel=1e-6, label="two-span S2")

    M1 = np.asarray(st1["M3"], float)
    M2 = np.asarray(st2["M3"], float)
    # support moment over B from either side = wL^2/8, and the raw engine
    # values must be continuous across the shared node (same convention,
    # same running direction +X for both members)
    assert abs(M1[10]) == pytest.approx(w * L**2 / 8, rel=1e-6)
    assert abs(M2[0]) == pytest.approx(w * L**2 / 8, rel=1e-6)
    assert M1[10] == pytest.approx(M2[0], rel=1e-6)

    # shear jumps by R_B across the middle support
    V1 = np.asarray(st1["V2"], float)
    V2_ = np.asarray(st2["V2"], float)
    assert abs(V1[10]) == pytest.approx(5 * w * L / 8, rel=1e-6)  # wL/2 - M_B/L
    assert abs(V1[10] - V2_[0]) == pytest.approx(5 * w * L / 4, rel=1e-6)


# --------------------------------------------------------------------------- #
# 9. 3D right-angle balcony: bending + torsion superposition
# --------------------------------------------------------------------------- #
def test_balcony_bending_plus_torsion():
    """Right-angle balcony, both members horizontal, square section:
      m1: (0,0,0)->(L1,0,0) clamped at origin; m2: (L1,0,0)->(L1,L2,0);
      tip load P down at (L1, L2, 0).
        tip dz = P L1^3/3EI  (m1 bending)
               + P L2^3/3EI  (m2 bending)
               + (P L2) L1 / (G J) * L2   (m1 twist -> rigid-arm dz)
        torque in m1 = P L2 (constant along m1);  torque in m2 = 0.
    The superposition was verified against an independent 12-DOF 3D frame FE
    (scratch derive_balcony.py, agreement 6e-15).  Euler beam + GJ torsion
    is exactly what elasticBeamColumn solves -> 1e-6."""
    L1, L2, P, size = 4.0, 2.5, 10.0, 0.35
    mdl = new_model("balcony", [1.0])
    sec = FrameSection.rectangular("BAL", "CONC", size, size)
    mdl.add_section(sec)
    I = sec.I33                                # == I22 (square)
    J = sec.J                                  # Roark, same formula engine uses
    G = mdl.materials["CONC"].G                # E / (2 (1 + nu))
    delta = (P * L1**3 / (3 * E_CONC * I)
             + P * L2**3 / (3 * E_CONC * I)
             + P * L2**2 * L1 / (G * J))

    mdl.add_member("beam", "BAL", (0, 0, 0), (L1, 0, 0), story="Story1", uid="M1")
    mdl.add_member("beam", "BAL", (L1, 0, 0), (L1, L2, 0), story="Story1", uid="M2")
    mdl.supports.append(PointSupport((0, 0, 0), FIX))
    mdl.pattern("P", "other").nodal_loads.append(NodalLoad((L1, L2, 0), fz=-P))
    mdl.add_case("P", {"P": 1.0})
    finish(mdl, (L1, L2, 0))

    d = run(mdl)
    case = d["cases"]["P"]
    tip = find_node(d, (L1, L2, 0))
    assert case["node_disp"][tip][2] == pytest.approx(-delta, rel=1e-6)

    # global equilibrium at the single clamp: FZ = P, |MX| = P*L2, |MY| = P*L1
    base = find_node(d, (0, 0, 0))
    R = case["reactions"][base]
    assert R[2] == pytest.approx(P, rel=1e-9)
    assert abs(R[3]) == pytest.approx(P * L2, rel=1e-9)
    assert abs(R[4]) == pytest.approx(P * L1, rel=1e-9)

    # torsion diagrams: constant P*L2 along m1, ~0 along m2
    st1 = stations(d, "P", "M1")
    T1 = np.asarray(st1["T"], float)
    check_x_grid(st1, L1)
    assert np.max(np.abs(np.abs(T1) - P * L2)) < 1e-6 * P * L2, (
        f"m1 torque should be P*L2={P*L2} at every station, got {T1}")
    assert np.max(np.abs(T1 - T1[0])) < 1e-9 * P * L2       # exactly constant

    st2 = stations(d, "P", "M2")
    T2 = np.asarray(st2["T"], float)
    assert np.max(np.abs(T2)) < 1e-9 * P * L2

    # m2 bending: cantilever off the joint, M(x') = -P (L2 - x')
    assert_station_match(st2, "M3", lambda x: -P * (L2 - x), L2, rel=1e-6,
                         label="balcony m2")


# --------------------------------------------------------------------------- #
# 10. load direction handling: global_x UDL on a vertical cantilever
# --------------------------------------------------------------------------- #
@needs_member_load
def test_global_x_udl_on_vertical_cantilever():
    """Vertical cantilever column, uniform lateral load w (global X), full
    span:  tip dx = wL^4/8EI,  base shear = wL,  base moment = wL^2/2.
    Square section so the answer cannot depend on the engine's column
    local-axis orientation; bending moment is checked through the resultant
    sqrt(M2^2+M3^2) for the same reason.  Exact for Euler FE -> 1e-6."""
    L, w, size = 6.0, 9.0, 0.4
    I = size**4 / 12
    delta = w * L**4 / (8 * E_CONC * I)

    mdl = new_model("cant-gx", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), FIX))
    add_member_load(mdl, "W", member_uid="C1", kind="udl", w=w,
                    direction="global_x")
    mdl.add_case("W", {"W": 1.0})
    finish(mdl, (0, 0, L))

    d = run(mdl)
    case = d["cases"]["W"]
    tip = find_node(d, (0, 0, L))
    base = find_node(d, (0, 0, 0))

    ux = case["node_disp"][tip][0]
    assert abs(ux) == pytest.approx(delta, rel=1e-6)
    # base shear opposes the resultant wL; deflection is along the load
    fx = case["reactions"][base][0]
    assert abs(fx) == pytest.approx(w * L, rel=1e-9)
    assert ux * fx < 0.0            # reaction opposite the motion/load
    assert abs(case["reactions"][base][4]) == pytest.approx(w * L**2 / 2, rel=1e-9)

    # station diagrams: |M|(z) = w (L-z)^2 / 2 as a resultant of M2/M3,
    # base value wL^2/2, tip 0; shear resultant likewise w (L-z).
    st = stations(d, "W", "C1")
    xs = check_x_grid(st, L)
    Mres = np.hypot(np.asarray(st["M2"], float), np.asarray(st["M3"], float))
    Mref = w * (L - xs) ** 2 / 2
    np.testing.assert_allclose(Mres, Mref, rtol=0, atol=1e-6 * w * L**2 / 2)
    Vres = np.hypot(np.asarray(st["V2"], float), np.asarray(st["V3"], float))
    np.testing.assert_allclose(Vres, w * (L - xs), rtol=0, atol=1e-6 * w * L)


# --------------------------------------------------------------------------- #
# 11. superposition of point + partial + trapezoid loads
# --------------------------------------------------------------------------- #
@needs_member_load
def test_member_load_superposition():
    """Linear analysis: (point + partial UDL + trapezoid) on one SS beam must
    equal the sum of the three individual runs -- displacements, reactions
    and every station array -- to 1e-9 relative.  This is a pure bookkeeping
    identity (same stiffness matrix, load vectors add), so any disagreement
    is a fixed-end-force accounting bug, not discretisation."""
    L = 8.0
    mdl = _beam_model("superpose", L)
    mdl.supports.append(PointSupport((0, 0, 0), PIN))
    mdl.supports.append(PointSupport((L, 0, 0), ROLLER))
    add_member_load(mdl, "P1", member_uid="B1", kind="point", w=25.0, a=0.35)
    add_member_load(mdl, "P2", member_uid="B1", kind="udl", w=10.0, a=0.2, b=0.7)
    add_member_load(mdl, "P3", member_uid="B1", kind="trapezoid",
                    w=5.0, w2=12.0, a=0.1, b=0.9)
    for c in ("P1", "P2", "P3"):
        mdl.add_case(c, {c: 1.0})
    mdl.add_case("ALL", {"P1": 1.0, "P2": 1.0, "P3": 1.0})
    finish(mdl, (L, 0, 0))

    d = run(mdl)
    parts = [d["cases"][c] for c in ("P1", "P2", "P3")]
    tot = d["cases"]["ALL"]

    def close(got, want, scale):
        assert abs(got - want) <= 1e-9 * scale + 1e-12, (
            f"superposition broke: got {got!r} want {want!r}")

    for tag in tot["node_disp"]:
        for i in range(6):
            want = sum(p["node_disp"][tag][i] for p in parts)
            scale = max(abs(want), *(abs(p["node_disp"][tag][i]) for p in parts))
            close(tot["node_disp"][tag][i], want, max(scale, 1e-6))
    for tag in tot["reactions"]:
        for i in range(6):
            want = sum(p["reactions"][tag][i] for p in parts)
            close(tot["reactions"][tag][i], want, max(abs(want), 1.0))

    st_tot = stations(d, "ALL", "B1")
    st_parts = [stations(d, c, "B1") for c in ("P1", "P2", "P3")]
    np.testing.assert_allclose(st_tot["x"], st_parts[0]["x"], rtol=0, atol=1e-9)
    for key in ("N", "V2", "V3", "T", "M2", "M3"):
        want = np.sum([np.asarray(p[key], float) for p in st_parts], axis=0)
        got = np.asarray(st_tot[key], float)
        scale = max(float(np.max(np.abs(want))), 1e-6)
        np.testing.assert_allclose(
            got, want, rtol=0, atol=1e-9 * scale + 1e-12,
            err_msg=f"superposition broke for station array {key}")
