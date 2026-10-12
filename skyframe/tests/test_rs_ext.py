"""Response-spectrum completeness (ETABS parity): accidental eccentricity,
U3 / arbitrary-angle excitation, several directions in one case with SRSS /
CQC3 / Absolute directional combination, and RS shell forces.

Every engine value is pinned to a hand-derived closed form:

* a 2-DOF spring chain (X, Y and Z springs + lumped masses): eigenpairs from
  the quadratic characteristic equation, CQC by hand;
* a torsional 3-DOF one-story rigid diaphragm on four corner springs:
  K / M written out by hand (ux, uy, theta about the diaphragm master),
  eigenpairs of the 3x3 problem, RS by hand for any excitation angle;
* eccentricity torques = ecc x RS story force x perpendicular dimension and
  the static torsion response = K^-1 [0, 0, T];
* CQC3 against the brute-force maximum over the excitation angle;
* RS shell forces of a single-mode shell model = |static shell forces|.
"""

import json
import math
import warnings

import numpy as np
import pytest

from skyframe.core import rs_ext as crx
from skyframe.core.builder import quick_building
from skyframe.core.model import (
    G_ACCEL,
    BuildingModel,
    FrameSection,
    Material,
    NodalLoad,
    NodalMass,
    PointSupport,
    ResponseSpectrumCase,
    ShellSection,
)
from skyframe.engine import rs_ext as erx
from skyframe.engine.opensees_engine import OpenSeesEngine

SA = 0.5                                     # flat spectrum (g)
FLAT = [[0.0, SA], [10.0, SA]]
SLOPE = [[0.0, 0.3], [0.2, 1.0], [0.5, 1.0], [2.0, 0.25]]


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _run(m):
    m.validate()
    return OpenSeesEngine(m).run().to_dict()


def _node(d, pt, tol=1e-6):
    for tag, xyz in d["nodes"].items():
        if all(abs(a - b) < tol for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(f"no node at {pt}")


def _cqc(vals, omegas, z=0.05):
    from skyframe.engine.modalcombo import cqc_rho
    v = np.asarray(vals, float)
    return math.sqrt(max(v @ cqc_rho(omegas, z) @ v, 0.0))


# --------------------------------------------------------------------------- #
# 2-DOF spring chain (X / Y / Z uncoupled)
# --------------------------------------------------------------------------- #
K1, K2, M1, M2 = 4000.0, 2500.0, 6.0, 4.0     # kN/m, t
KY = 1.7                                      # Y springs = 1.7 x X springs
KZ1, KZ2 = 9.0e4, 6.0e4


def _chain(mz=True, solver=None, modes=6):
    m = BuildingModel(name="chain")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=3e7, nu=0.2))
    m.set_stories([3.0, 3.0])
    m.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    m.add_link((0, 0, 0), (0, 0, 3), [K1, KY * K1, KZ1, 0, 0, 0], uid="L1")
    m.add_link((0, 0, 3), (0, 0, 6), [K2, KY * K2, KZ2, 0, 0, 0], uid="L2")
    m.nodal_masses.append(NodalMass((0, 0, 3), mx=M1, my=M1,
                                    mz=M1 if mz else 0.0))
    m.nodal_masses.append(NodalMass((0, 0, 6), mx=M2, my=M2,
                                    mz=M2 if mz else 0.0))
    m.num_modes = modes
    if solver:
        m.analysis_options = {"solver": solver}
    return m


def _two_dof(k1, k2, m1=M1, m2=M2):
    """Closed-form eigenpairs of the 2-DOF chain (mass-normalised)."""
    a = m1 * m2
    b = -(m1 * k2 + m2 * (k1 + k2))
    c = k1 * k2
    out = []
    for w2 in sorted([(-b - math.sqrt(b * b - 4 * a * c)) / (2 * a),
                      (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)]):
        phi = np.array([1.0, (k1 + k2 - w2 * m1) / k2])
        phi = phi / math.sqrt(m1 * phi[0] ** 2 + m2 * phi[1] ** 2)
        gam = m1 * phi[0] + m2 * phi[1]
        out.append((math.sqrt(w2), phi, gam))
    return out


def _chain_hand(k1, k2, s=1.0):
    """CQC of (u1, u2, base shear) for a flat spectrum SA*s."""
    modes = _two_dof(k1, k2)
    om = [w for w, _, _ in modes]
    u1, u2, V = [], [], []
    for w, phi, gam in modes:
        u = gam * SA * s * G_ACCEL / w ** 2 * phi
        u1.append(u[0])
        u2.append(u[1])
        V.append(gam * SA * s * G_ACCEL * (M1 * phi[0] + M2 * phi[1]))
    return _cqc(u1, om), _cqc(u2, om), _cqc(V, om)


def test_two_dof_x_closed_form():
    m = _chain()
    m.add_rs_case("RSX", "X", FLAT)
    d = _run(m)
    n1, n2 = _node(d, (0, 0, 3)), _node(d, (0, 0, 6))
    u1, u2, V = _chain_hand(K1, K2)
    r = d["rs_cases"]["RSX"]
    assert r["node_disp"][n1][0] == pytest.approx(u1, rel=1e-8)
    assert r["node_disp"][n2][0] == pytest.approx(u2, rel=1e-8)
    assert r["base"]["FX"] == pytest.approx(V, rel=1e-8)
    assert r["base"]["FZ"] == pytest.approx(0.0, abs=1e-9)
    assert "rs" not in r                      # legacy case: legacy shape


@pytest.mark.parametrize("solver", [None, "umfpack"])
def test_two_dof_vertical_u3_closed_form(solver):
    m = _chain(solver=solver)
    m.add_rs_case("RSZ", "Z", FLAT)
    d = _run(m)
    n1, n2 = _node(d, (0, 0, 3)), _node(d, (0, 0, 6))
    u1, u2, V = _chain_hand(KZ1, KZ2)
    r = d["rs_cases"]["RSZ"]
    assert r["node_disp"][n1][2] == pytest.approx(u1, rel=1e-7)
    assert r["node_disp"][n2][2] == pytest.approx(u2, rel=1e-7)
    assert r["base"]["FZ"] == pytest.approx(V, rel=1e-7)
    assert r["base"]["FX"] == pytest.approx(0.0, abs=1e-6)
    assert r["rs"]["loads"][0]["vector"] == [0.0, 0.0, 1.0]
    # UZ participation reported (an RS case excites U3); all 6 modes
    part = d["modal"]["participation"]
    assert all("uz" in p and "gamma_z" in p for p in part)
    assert sum(p["uz"] for p in part) == pytest.approx(1.0, rel=1e-9)
    hand = sorted(abs(g) for _, _, g in _two_dof(KZ1, KZ2))
    eng = sorted(abs(p["gamma_z"]) for p in part if p["uz"] > 1e-9)
    assert eng == pytest.approx(hand, rel=1e-7)


def test_vertical_without_uz_mass_is_an_error():
    m = _chain(mz=False)
    m.add_rs_case("RSZ", "Z", FLAT)
    m.validate()
    with pytest.raises(ValueError, match="vertical mass"):
        OpenSeesEngine(m).run_response_spectrum("RSZ")


def test_include_vertical_reports_uz_participation_and_mass():
    m = quick_building(stories=2)
    base = _run(quick_building(stories=2))
    assert all("uz" not in p for p in base["modal"]["participation"])
    m.mass_options = {"include_vertical": True}
    m.num_modes = 40
    d = _run(m)
    part = d["modal"]["participation"]
    assert all("uz" in p for p in part)
    assert max(p["uz"] for p in part) > 0.05     # vertical modes captured


# --------------------------------------------------------------------------- #
# torsional 3-DOF one-story diaphragm
# --------------------------------------------------------------------------- #
A, B, H, MS = 8.0, 6.0, 3.0, 40.0
CORNERS = [(0.0, 0.0), (A, 0.0), (A, B), (0.0, B)]
KXS = [2000.0, 2000.0, 6000.0, 6000.0]
KYS = [3000.0, 5000.0, 5000.0, 3000.0]


def _tors(kx=KXS, ky=KYS):
    m = BuildingModel(name="t3")
    m.add_material(Material("C", E=3e7, nu=0.2, unit_weight=0.0))
    m.add_section(FrameSection.rectangular("B", "C", 0.3, 0.5))
    m.set_stories([H])
    s = m.stories[0].name
    for i in range(4):
        p, q = CORNERS[i], CORNERS[(i + 1) % 4]
        m.add_member("beam", "B", (*p, H), (*q, H), story=s, uid=f"B{i}")
        m.supports.append(PointSupport((*p, 0.0), (1,) * 6))
        m.add_link((*p, 0.0), (*p, H), [kx[i], ky[i], 1e6, 0, 0, 0],
                   uid=f"L{i}")
    m.story_masses = {s: MS}
    m.num_modes = 3
    return m


def _tors_km(kx=KXS, ky=KYS):
    xc, yc = A / 2, B / 2
    K = np.zeros((3, 3))
    for (x, y), a, b in zip(CORNERS, kx, ky):
        dx, dy = x - xc, y - yc
        # point displacement: ux - th*dy, uy + th*dx
        tx = np.array([1.0, 0.0, -dy])
        ty = np.array([0.0, 1.0, dx])
        K += a * np.outer(tx, tx) + b * np.outer(ty, ty)
    Mm = np.diag([MS, MS, MS * (A * A + B * B) / 12.0])
    return K, Mm


def _tors_modes(kx=KXS, ky=KYS):
    K, Mm = _tors_km(kx, ky)
    s = np.sqrt(np.diag(Mm))
    w2, Y = np.linalg.eigh(K / s[:, None] / s[None, :])
    phis = Y / s[:, None]                       # phi^T M phi = 1
    return np.sqrt(w2), phis, K, Mm


def _tors_hand(e, spec=FLAT, kx=KXS, ky=KYS, scale=1.0):
    """CQC of master (ux, uy, th) and base (FX, FY, MZ about the master)."""
    from skyframe.engine.opensees_engine import _interp_spectrum
    om, phis, K, Mm = _tors_modes(kx, ky)
    rows = []
    for i in range(3):
        phi = phis[:, i]
        gam = phi @ Mm @ np.array([e[0], e[1], 0.0])
        T = 2 * math.pi / om[i]
        sa = _interp_spectrum(spec, T) * scale * G_ACCEL
        f = gam * sa * (Mm @ phi)               # modal static force
        u = np.linalg.solve(K, f)
        rows.append(np.concatenate([u, f]))
    rows = np.array(rows)
    return np.array([_cqc(rows[:, j], om) for j in range(6)]), om


def _tors_engine(d, case):
    r = d["rs_cases"][case]
    mt = _node(d, (A / 2, B / 2, H))
    u = r["node_disp"][mt]
    b = r["base"]
    # base MZ is about the origin: subtract the moment arm of the forces
    # (only valid for the hand comparison of single quantities below)
    return np.array([u[0], u[1], u[5], b["FX"], b["FY"]]), b["MZ"]


@pytest.mark.parametrize("angle", [0.0, 30.0, 117.5])
def test_torsional_3dof_angle_closed_form(angle):
    m = _tors()
    m.add_rs_case("R", "X", SLOPE, angle=angle)
    d = _run(m)
    a = math.radians(angle)
    hand, _ = _tors_hand((math.cos(a), math.sin(a)), SLOPE)
    eng, _ = _tors_engine(d, "R")
    assert eng == pytest.approx(hand[:5], rel=1e-7, abs=1e-12)
    # U2 at angle a == U1 at angle a + 90
    m2 = _tors()
    m2.add_rs_case("R", "Y", SLOPE, angle=angle - 90.0)
    eng2, _ = _tors_engine(_run(m2), "R")
    assert eng2 == pytest.approx(eng, rel=1e-9, abs=1e-12)


def test_angle_45_on_symmetric_model_gives_sqrt2_over_2():
    kx, ky = [3000.0] * 4, [5000.0] * 4        # doubly symmetric
    m = _tors(kx, ky)
    m.add_rs_case("RX", "X", SLOPE)
    m.add_rs_case("RY", "Y", SLOPE)
    m.add_rs_case("R45", "X", SLOPE, angle=45.0)
    d = _run(m)
    r = d["rs_cases"]
    h = math.sqrt(2.0) / 2.0
    assert r["R45"]["base"]["FX"] == pytest.approx(h * r["RX"]["base"]["FX"],
                                                   rel=1e-9)
    assert r["R45"]["base"]["FY"] == pytest.approx(h * r["RY"]["base"]["FY"],
                                                   rel=1e-9)
    mt = _node(d, (A / 2, B / 2, H))
    assert r["R45"]["node_disp"][mt][0] == pytest.approx(
        h * r["RX"]["node_disp"][mt][0], rel=1e-9)


def test_angle_zero_extended_path_equals_legacy():
    m = _tors()
    m.add_rs_case("LEG", "X", SLOPE)
    m.add_rs_case("EXT", "X", SLOPE, loads=[{"dir": "X"}])
    d = _run(m)
    a, b = d["rs_cases"]["LEG"], d["rs_cases"]["EXT"]
    for k in ("FX", "FY", "MZ"):
        assert b["base"][k] == pytest.approx(a["base"][k], rel=1e-10)


# --------------------------------------------------------------------------- #
# directional combination: SRSS / ABS / CQC3
# --------------------------------------------------------------------------- #
def _flat_vals(r):
    return [r["base"][k] for k in ("FX", "FY", "MZ")] + [
        v for t in sorted(r["node_disp"]) for v in r["node_disp"][t]]


def test_multi_direction_srss_and_abs_match_single_cases():
    m = _tors()
    m.add_rs_case("RX", "X", SLOPE)
    m.add_rs_case("RY", "Y", SLOPE, scale=0.6)
    loads = [{"dir": "X", "scale": 1.0}, {"dir": "Y", "scale": 0.6}]
    m.add_rs_case("S", "X", SLOPE, loads=loads)
    m.add_rs_case("A1", "X", SLOPE, loads=loads, dir_combo="ABS")
    m.add_rs_case("A3", "X", SLOPE, loads=loads, dir_combo="ABS",
                  dir_sf=0.3)
    m.add_rs_combo("C1030", "RX", "RY", "100_30")
    m.add_rs_combo("CABS", "RX", "RY", "ABS")
    d = _run(m)
    r = d["rs_cases"]
    x, y = np.array(_flat_vals(r["RX"])), np.array(_flat_vals(r["RY"]))
    assert np.array(_flat_vals(r["S"])) == pytest.approx(
        np.sqrt(x * x + y * y), rel=1e-9, abs=1e-12)
    assert np.array(_flat_vals(r["A1"])) == pytest.approx(x + y, rel=1e-9,
                                                          abs=1e-12)
    assert np.array(_flat_vals(r["CABS"])) == pytest.approx(x + y, rel=1e-12,
                                                            abs=1e-12)
    # ABS with sf = 0.3 is the 100/30 rule
    assert np.array(_flat_vals(r["A3"])) == pytest.approx(
        np.array(_flat_vals(r["C1030"])), rel=1e-9, abs=1e-12)


def test_cqc3_isotropic_equals_srss_of_directions():
    m = _tors()
    m.add_rs_case("RX", "X", SLOPE)
    m.add_rs_case("RY", "Y", SLOPE)
    m.add_rs_combo("SR", "RX", "RY", "SRSS")
    m.add_rs_combo("C3", "RX", "RY", "CQC3")
    both = [{"dir": "X"}, {"dir": "Y"}]
    m.add_rs_case("IN", "X", SLOPE, loads=both, dir_combo="CQC3")
    m.add_rs_case("INS", "X", SLOPE, loads=both)
    d = _run(m)
    r = d["rs_cases"]
    sr = np.array(_flat_vals(r["SR"]))
    assert np.array(_flat_vals(r["C3"])) == pytest.approx(sr, rel=1e-9,
                                                          abs=1e-12)
    assert np.array(_flat_vals(r["IN"])) == pytest.approx(sr, rel=1e-9,
                                                          abs=1e-12)
    assert np.array(_flat_vals(r["INS"])) == pytest.approx(sr, rel=1e-9,
                                                           abs=1e-12)


def test_cqc3_is_the_maximum_over_the_excitation_angle():
    g = 0.55                                   # minor / major spectrum ratio
    m = _tors()
    loads = [{"dir": "X", "scale": 1.0}, {"dir": "Y", "scale": g}]
    m.add_rs_case("C3", "X", SLOPE, loads=loads, dir_combo="CQC3")
    m.add_rs_case("C3b", "X", SLOPE, loads=loads, dir_combo="CQC3",
                  angle=37.0)
    for k in range(0, 180):
        # independent components: R(theta)^2 = R_U1^2 + R_U2^2 (SRSS case)
        m.add_rs_case(f"T{k}", "X", SLOPE, loads=loads, angle=float(k))
    d = _run(m)
    r = d["rs_cases"]
    c3 = np.array(_flat_vals(r["C3"]))
    sweep = np.array([_flat_vals(r[f"T{k}"]) for k in range(180)])
    best = sweep.max(axis=0)
    assert np.all(best <= c3 * (1 + 1e-9) + 1e-12)
    assert best == pytest.approx(c3, rel=2e-4, abs=1e-12)
    # CQC3 does not depend on the reference angle
    assert np.array(_flat_vals(r["C3b"])) == pytest.approx(c3, rel=1e-9,
                                                           abs=1e-12)


def test_cqc3_pure_numerics_menun_formula():
    rng = np.random.default_rng(3)
    n, nq = 4, 7
    Q = rng.normal(size=(n, nq))
    om = [3.0, 4.1, 7.0, 7.4]
    from skyframe.engine.modalcombo import cqc_rho
    rho = cqc_rho(om, 0.05)
    s1 = rng.uniform(1, 3, n)
    gam = 0.6
    g1, g2 = rng.normal(size=n), rng.normal(size=n)
    got = erx.cqc3(rho, Q, s1, gam * s1, g1, g2)
    # Menun & Der Kiureghian with S2 = gam S1 written with the X/Y sums
    a1, a2 = (s1 * g1)[:, None] * Q, (s1 * g2)[:, None] * Q
    Sxx = (a1 * (rho @ a1)).sum(0)
    Syy = (a2 * (rho @ a2)).sum(0)
    Sxy = (a1 * (rho @ a2)).sum(0)
    hand = np.sqrt(0.5 * (1 + gam ** 2) * (Sxx + Syy) + (1 - gam ** 2)
                   * np.sqrt(0.25 * (Sxx - Syy) ** 2 + Sxy ** 2))
    assert got == pytest.approx(hand, rel=1e-12)


def test_three_directions_with_u3_srss():
    m = _chain()
    m.add_rs_case("RX", "X", FLAT)
    m.add_rs_case("RY", "Y", FLAT)
    m.add_rs_case("RZ", "Z", FLAT)
    m.add_rs_case("ALL", "X", FLAT, loads=[{"dir": "X"}, {"dir": "Y"},
                                           {"dir": "Z"}])
    m.add_rs_case("ALL3", "X", FLAT, loads=[{"dir": "X"}, {"dir": "Y"},
                                            {"dir": "Z"}], dir_combo="CQC3")
    d = _run(m)
    r = d["rs_cases"]
    for k in ("FX", "FY", "FZ"):
        hand = math.sqrt(sum(r[c]["base"][k] ** 2
                             for c in ("RX", "RY", "RZ")))
        assert r["ALL"]["base"][k] == pytest.approx(hand, rel=1e-9,
                                                    abs=1e-9)
        assert r["ALL3"]["base"][k] == pytest.approx(hand, rel=1e-9,
                                                     abs=1e-9)


# --------------------------------------------------------------------------- #
# accidental eccentricity
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("direction,dim", [("X", B), ("Y", A)])
def test_eccentricity_one_story_torque_and_response(direction, dim):
    ecc = 0.05
    m = _tors()
    m.add_rs_case("R", direction, SLOPE)
    m.add_rs_case("RE", direction, SLOPE, ecc=ecc)
    d = _run(m)
    r, re_ = d["rs_cases"]["R"], d["rs_cases"]["RE"]
    s = m.stories[0].name
    F = r["base"]["FX" if direction == "X" else "FY"]   # one story
    info = re_["rs"]["ecc"]
    assert info["forces"][s][direction] == pytest.approx(F, rel=1e-9)
    T = ecc * F * dim
    assert info["torques"][s] == pytest.approx(T, rel=1e-9)
    # static torsion response by hand: u = K^-1 [0, 0, T] (master dofs)
    K, _ = _tors_km()
    ut = np.linalg.solve(K, [0.0, 0.0, T])
    mt = _node(d, (A / 2, B / 2, H))
    for j, dof in enumerate((0, 1, 5)):
        assert re_["node_disp"][mt][dof] == pytest.approx(
            r["node_disp"][mt][dof] + abs(ut[j]), rel=1e-7)
    # base torque about the master grows by T exactly (forces sum to zero)
    assert re_["base"]["MZ"] == pytest.approx(r["base"]["MZ"] + T, rel=1e-9)
    assert re_["base"]["FX"] == pytest.approx(r["base"]["FX"] + abs(
        K[0] @ ut), rel=1e-9, abs=1e-9)


def test_eccentricity_story_forces_two_stories_single_mode():
    m = quick_building(stories=2)
    m.num_modes = 1
    m.add_rs_case("R", "X", SLOPE, num_modes=1)
    m.add_rs_case("RE", "X", SLOPE, num_modes=1, ecc=0.05,
                  ecc_overrides={m.stories[1].name: 0.10})
    d = _run(m)
    st = d["rs_cases"]["R"]["story"]
    s1, s2 = m.stories[0].name, m.stories[1].name
    lx, ly = m.plan_extents()
    tq = d["rs_cases"]["RE"]["rs"]["ecc"]["torques"]
    F2 = st[s2]["shear_x"]
    F1 = st[s1]["shear_x"] - st[s2]["shear_x"]
    assert tq[s1] == pytest.approx(0.05 * F1 * ly, rel=1e-8)
    assert tq[s2] == pytest.approx(0.10 * F2 * ly, rel=1e-8)


def test_eccentricity_multi_direction_torques_srss_and_abs():
    m = _tors()
    m.add_rs_case("RX", "X", SLOPE, ecc=0.05)
    m.add_rs_case("RY", "Y", SLOPE, ecc=0.05)
    both = [{"dir": "X"}, {"dir": "Y"}]
    m.add_rs_case("S", "X", SLOPE, loads=both, ecc=0.05)
    m.add_rs_case("AB", "X", SLOPE, loads=both, ecc=0.05, dir_combo="ABS")
    d = _run(m)
    s = m.stories[0].name
    tx = d["rs_cases"]["RX"]["rs"]["ecc"]["torques"][s]
    ty = d["rs_cases"]["RY"]["rs"]["ecc"]["torques"][s]
    assert d["rs_cases"]["S"]["rs"]["ecc"]["torques"][s] == pytest.approx(
        math.hypot(tx, ty), rel=1e-9)
    assert d["rs_cases"]["AB"]["rs"]["ecc"]["torques"][s] == pytest.approx(
        tx + ty, rel=1e-9)


def test_eccentricity_named_diaphragms_use_their_own_extents():
    from tests.test_diaphragms_mass import _two
    m = _two()
    m.add_rs_case("R", "X", [[0.0, 1.0], [5.0, 1.0]], ecc=0.05,
                  ecc_overrides={"S1/DB": 0.1})
    d = _run(m)
    info = d["rs_cases"]["R"]["rs"]["ecc"]
    assert set(info["torques"]) == {"S1/DA", "S1/DB"}
    for lbl, ratio in (("S1/DA", 0.05), ("S1/DB", 0.1)):
        # each tower is A x A in plan (tests/test_diaphragms_mass.py)
        from tests.test_diaphragms_mass import A as SIDE
        F = info["forces"][lbl]["X"]
        assert F > 0.0
        assert info["torques"][lbl] == pytest.approx(ratio * F * SIDE,
                                                     rel=1e-9)
    with pytest.raises(ValueError, match="unknown story"):
        m.add_rs_case("R2", "X", [[0.0, 1.0]], ecc=0.05,
                      ecc_overrides={"S1/NOPE": 0.1})


def test_eccentricity_towers_use_per_tower_dimensions():
    from tests.test_towers import _twin
    m = _twin()
    m.add_rs_case("R", "Y", SLOPE, ecc=0.05)
    d = _run(m)
    info = d["rs_cases"]["R"]["rs"]["ecc"]
    lx_all, _ = m.plan_extents()
    assert lx_all > 40.0
    for s in m.stories:
        F = info["forces"][s.name]["Y"]
        # each tower is 6 m wide in X (tests/test_towers.py _tower bx)
        assert info["torques"][s.name] == pytest.approx(0.05 * F * 6.0,
                                                        rel=1e-9)


def test_eccentricity_ax_amplification():
    m = _tors(kx=[500.0, 500.0, 9000.0, 9000.0])
    m.add_rs_case("R", "X", SLOPE, ecc=0.05)
    m.add_rs_case("RA", "X", SLOPE, ecc=0.05, ecc_ax=True)
    d = _run(m)
    s = m.stories[0].name
    a, b = d["rs_cases"]["R"]["rs"]["ecc"], d["rs_cases"]["RA"]["rs"]["ecc"]
    ax = b["ax"][s]
    assert 1.0 <= ax <= 3.0
    assert b["torques"][s] == pytest.approx(ax * a["torques"][s], rel=1e-9)
    # hand Ax from the un-amplified combined corner displacements along X
    r = d["rs_cases"]["R"]
    lo, hi = _node(d, (0.0, 0.0, H)), _node(d, (0.0, B, H))
    d1, d2 = r["node_disp"][lo][0], r["node_disp"][hi][0]
    hand = min(max((max(d1, d2) / (0.6 * (d1 + d2))) ** 2, 1.0), 3.0)
    assert ax == pytest.approx(hand, rel=1e-9)
    # symmetric model: Ax = 1 and the result is unchanged
    m2 = _tors([3000.0] * 4, [5000.0] * 4)
    m2.add_rs_case("R", "X", SLOPE, ecc=0.05)
    m2.add_rs_case("RA", "X", SLOPE, ecc=0.05, ecc_ax=True)
    d2_ = _run(m2)
    assert d2_["rs_cases"]["RA"]["base"] == d2_["rs_cases"]["R"]["base"]


def test_eccentricity_on_rs_combos_cqc3():
    m = _tors()
    m.add_rs_case("RX", "X", SLOPE, ecc=0.05)
    m.add_rs_case("RY", "Y", SLOPE, ecc=0.05)
    m.add_rs_combo("C3", "RX", "RY", "CQC3")
    m.add_rs_case("IN", "X", SLOPE, loads=[{"dir": "X"}, {"dir": "Y"}],
                  dir_combo="CQC3", ecc=0.05)
    d = _run(m)
    a, b = d["rs_cases"]["C3"], d["rs_cases"]["IN"]
    assert np.array(_flat_vals(a)) == pytest.approx(
        np.array(_flat_vals(b)), rel=1e-9, abs=1e-12)


# --------------------------------------------------------------------------- #
# RS shell forces
# --------------------------------------------------------------------------- #
MW = 12.0


def _wall(solver=None):
    m = BuildingModel(name="wall")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=3e7, nu=0.2, unit_weight=0.0))
    m.add_shell_section(ShellSection("W", "C", 0.25))
    m.set_stories([3.0])
    m.add_shell("wall", "shell", "W",
                [(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3)], mesh_size=1.0,
                story=m.stories[0].name)
    for x in (0.0, 1.0, 2.0, 3.0, 4.0):
        m.supports.append(PointSupport((x, 0.0, 0.0), (1,) * 6))
    m.nodal_masses.append(NodalMass((4.0, 0.0, 3.0), mx=MW))
    m.num_modes = 1
    sa_g = SA * G_ACCEL
    m.pattern("P", "other").nodal_loads.append(
        NodalLoad((4.0, 0.0, 3.0), fx=MW * sa_g))
    m.add_case("P", {"P": 1.0})
    if solver:
        m.analysis_options = {"solver": solver}
    return m


@pytest.mark.parametrize("solver", [None, "umfpack"])
def test_rs_shell_forces_single_mode_equal_scaled_static(solver):
    m = _wall(solver)
    m.add_rs_case("R", "X", FLAT)
    m.add_rs_case("R30", "X", FLAT, angle=60.0)        # cos 60 = 1/2
    m.add_rs_case("RE", "X", FLAT, scale=2.0, loads=[{"dir": "X"}])
    d = _run(m)
    st = d["cases"]["P"]["shell_forces"]
    rs = d["rs_cases"]["R"]["shell_forces"]
    assert set(rs) == set(st) and len(st) == 12
    for q, v in st.items():
        assert len(rs[q]) == 8
        assert rs[q] == pytest.approx([abs(x) for x in v], rel=1e-7,
                                      abs=1e-9)
        assert d["rs_cases"]["R30"]["shell_forces"][q] == pytest.approx(
            [0.5 * abs(x) for x in v], rel=1e-7, abs=1e-9)
        assert d["rs_cases"]["RE"]["shell_forces"][q] == pytest.approx(
            [2.0 * abs(x) for x in v], rel=1e-7, abs=1e-9)


def test_rs_shell_forces_on_directional_combos_and_ecc():
    m = _wall()
    m.nodal_masses[0].my = MW
    m.num_modes = 2
    m.add_rs_case("RX", "X", FLAT)
    m.add_rs_case("RY", "Y", FLAT)
    m.add_rs_combo("D", "RX", "RY", "SRSS")
    m.add_rs_combo("C3", "RX", "RY", "CQC3")
    d = _run(m)
    rx = d["rs_cases"]["RX"]["shell_forces"]
    ry = d["rs_cases"]["RY"]["shell_forces"]
    for name in ("D", "C3"):
        sf = d["rs_cases"][name]["shell_forces"]
        for q in rx:
            assert sf[q] == pytest.approx(
                [math.hypot(a, b) for a, b in zip(rx[q], ry[q])], rel=1e-8,
                abs=1e-9)


def test_rs_shell_forces_shape_matches_static_on_meshed_building():
    from tests.test_meshed_perf import _meshed
    m = _meshed()
    m.add_rs_case("RE", "X", SLOPE, ecc=0.05)
    d = _run(m)
    st = d["cases"]["SDL"]["shell_forces"]
    for name in ("RSX", "RSY", "RE"):
        sf = d["rs_cases"][name]["shell_forces"]
        assert set(sf) == set(st)
        assert all(len(v) == 8 and min(v) >= 0.0 for v in sf.values())
    a, b = d["rs_cases"]["RSX"]["shell_forces"], \
        d["rs_cases"]["RE"]["shell_forces"]
    assert all(b[q][k] >= a[q][k] - 1e-12 for q in a for k in range(8))


# --------------------------------------------------------------------------- #
# model fields, JSON, validation, legacy
# --------------------------------------------------------------------------- #
def test_legacy_rs_case_json_unchanged_and_new_fields_round_trip():
    rs = ResponseSpectrumCase("R", "X", [[0.0, 1.0]])
    assert set(rs.to_dict()) == {"name", "direction", "spectrum",
                                 "num_modes", "combo_method", "damping",
                                 "scale", "function"}
    assert not crx.is_extended(rs) and not crx.has_ecc(rs)
    m = _tors()
    m.add_rs_case("N", "Y", SLOPE, angle=15.0, ecc=0.05, ecc_ax=True,
                  loads=[{"dir": "X", "scale": 1.0},
                         {"dir": "Y", "scale": 0.3},
                         {"dir": "Z", "scale": 0.7, "function": ""}],
                  dir_combo="ABS", dir_sf=0.3,
                  ecc_overrides={m.stories[0].name: 0.1})
    d = m.to_dict()
    rd = d["rs_cases"]["N"]
    assert rd["angle"] == 15.0 and rd["dir_combo"] == "ABS"
    assert rd["dir_sf"] == 0.3 and rd["ecc"] == 0.05 and rd["ecc_ax"]
    assert len(rd["loads"]) == 3
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert json.dumps(m2.to_dict(), sort_keys=True) == json.dumps(
        d, sort_keys=True)


@pytest.mark.parametrize("kw,msg", [
    ({"direction": "W"}, "direction"),
    ({"angle": float("nan")}, "angle"),
    ({"loads": [{"dir": "Q"}]}, "dir must be"),
    ({"loads": [{"dir": "X"}, {"dir": "X"}]}, "twice"),
    ({"loads": [{"dir": "X", "function": "NOPE"}]}, "spectrum_function"),
    ({"dir_combo": "FOO"}, "dir_combo"),
    ({"dir_sf": 1.5}, "dir_sf"),
    ({"dir_combo": "CQC3"}, "CQC3 needs both"),
    ({"dir_combo": "CQC3", "loads": [{"dir": "X"}, {"dir": "Y"}],
      "combo_method": "ABS"}, "CQC3 needs a CQC"),
    ({"ecc": -0.1}, "ecc"),
    ({"ecc": 0.7}, "ecc"),
    ({"ecc_overrides": {"NOPE": 0.05}}, "unknown story"),
    ({"direction": "Z", "ecc": 0.05}, "horizontal"),
])
def test_validation(kw, msg):
    m = _tors()
    args = {"direction": "X"}
    args.update(kw)
    direction = args.pop("direction")
    with pytest.raises(ValueError, match=msg):
        m.add_rs_case("BAD", direction, SLOPE, **args)


def test_rs_combo_cqc3_validation():
    m = _tors()
    m.add_rs_case("RX", "X", SLOPE)
    m.add_rs_case("RX2", "X", SLOPE, angle=10.0)
    m.add_rs_case("RYA", "Y", SLOPE, combo_method="ABS")
    with pytest.raises(ValueError, match="perpendicular"):
        m.add_rs_combo("C", "RX", "RX2", "CQC3")
    with pytest.raises(ValueError, match="CQC, SRSS or DSC"):
        m.add_rs_combo("C", "RX", "RYA", "CQC3")
    m.add_rs_case("RY10", "Y", SLOPE, angle=10.0)
    m.add_rs_combo("C", "RX2", "RY10", "CQC3")          # rotated pair ok


def test_e2k_exports_u3_and_reads_it_back():
    from skyframe.io.e2k_read import read_e2k
    from skyframe.io.e2k_write import export_e2k
    m = quick_building(stories=2)
    m.add_rs_case("RSZ", "Z", SLOPE)
    text, _ = export_e2k(m, extensions=False)
    assert 'ACCEL  "U3"' in text
    m2, _ = read_e2k(text)
    assert m2.rs_cases["RSZ"].direction == "Z"


def test_table_columns_round_trip():
    from skyframe.core import modeltables as mt
    m = _tors()
    m.add_rs_case("R", "X", SLOPE)
    d = m.to_dict()
    t = mt.get_table(d, "rs_cases")
    keys = {c["key"] for c in t["columns"]}
    assert {"angle", "dir_combo", "dir_sf", "ecc", "ecc_ax"} <= keys
    m2, err, _ = mt.apply_tables(d, {"rs_cases": t["rows"]})
    assert not err
    assert json.dumps(m2.to_dict(), sort_keys=True) == json.dumps(
        d, sort_keys=True)                     # defaults -> byte-identical
    rows = [dict(t["rows"][0], angle=30.0, ecc=0.05, ecc_ax=True)]
    m3, err, _ = mt.apply_tables(d, {"rs_cases": rows})
    assert not err
    r = m3.rs_cases["R"]
    assert (r.angle, r.ecc, r.ecc_ax) == (30.0, 0.05, True)


def test_flat_round_trip_and_combiner_matches_modalcombo():
    from skyframe.engine.modalcombo import ModalCombiner
    rng = np.random.default_rng(7)
    V = rng.normal(size=(4, 6))
    om = [2.0, 2.1, 9.0, 40.0]
    for method in ("CQC", "SRSS", "ABS", "GMC", "NRC10", "DSC"):
        for rigid in (False, True):
            comb = ModalCombiner(method, om, rigid_response=rigid,
                                 n_rigid_extra=1)
            extra = rng.normal(size=(1, 6))
            got = erx.combine_cols(comb, V, extra)
            want = [comb(list(V[:, j]) + [extra[0, j]]) for j in range(6)]
            assert got == pytest.approx(want, rel=1e-12)
