"""v1.13 — ETABS modal-combination parity + modal load participation.

Hand-computed closed-form checks of every combination rule (CQC, SRSS,
ABS, GMC, NRC10, DSC, rigid split, missing mass), engine-level exactness on
the classic 2-mass cantilever (per-mode responses rebuilt from the engine's
own modal output, exactly like test_phase4), the missing-mass static
correction, directional composition, Wilson load participation ratios, and
byte-identical legacy defaults.
"""

import json
import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.model import (  # noqa: E402
    G_ACCEL,
    BuildingModel,
    FrameSection,
    Material,
    MemberLoad,
    NodalLoad,
    NodalMass,
    PointSupport,
    ResponseSpectrumCase,
)
from skyframe.engine import modalcombo as mc  # noqa: E402

E_CONC = 25_000_000.0  # kPa


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _new_model(name, story_heights):
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories(list(story_heights))
    return mdl


def _two_mass_column(L=6.0, size=0.3, m1=8.0, m2=5.0):
    mdl = _new_model("2DOF", [L / 2, L / 2])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L / 2),
                   story="Story1", uid="C1")
    mdl.add_member("column", "COL", (0, 0, L / 2), (0, 0, L),
                   story="Story2", uid="C2")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L / 2), mx=m1))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m2))
    mdl.pattern("PX", "other").nodal_loads.append(NodalLoad((0, 0, L), fx=1.0))
    mdl.add_case("PX", {"PX": 1.0})
    mdl.num_modes = 2
    return mdl


def _find_node(results, pt, tol=1e-6):
    for tag, xyz in results["nodes"].items():
        if all(abs(a - b) < tol for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(f"no FE node at {pt}")


def _modal_parts(d, L, m1, m2, Sa):
    """Per-mode signed (u_mid, u_tip, V) from the engine's modal output."""
    mid = _find_node(d, (0, 0, L / 2))
    tip = _find_node(d, (0, 0, L))
    sag = Sa * G_ACCEL
    out = []
    for i, T in enumerate(d["modal"]["periods"]):
        om = 2.0 * math.pi / T
        gam = d["modal"]["participation"][i]["gamma_x"]
        sh = d["modal"]["shapes"][str(i + 1)]
        phi = np.array([sh[mid][0], sh[tip][0]])
        u = gam * sag / om ** 2 * phi
        V = gam * sag * (m1 * phi[0] + m2 * phi[1])
        out.append((u[0], u[1], V, om))
    return mid, tip, out


# --------------------------------------------------------------------------- #
# 1. pure numerics: hand-computed closed forms
# --------------------------------------------------------------------------- #
def test_cqc_rho_der_kiureghian_hand_value():
    z, w1, w2 = 0.05, 10.0, 12.0
    b = w1 / w2
    hand = (8 * z * z * (1 + b) * b ** 1.5
            / ((1 - b * b) ** 2 + 4 * z * z * b * (1 + b) ** 2))
    rho = mc.cqc_rho([w1, w2], z)
    assert rho[0, 1] == pytest.approx(hand, rel=1e-14)
    assert rho[0, 0] == rho[1, 1] == 1.0
    # explicit numeric value for b = 5/6, z = 0.05:
    # num = 0.02*1.8333*0.76073 = 0.027893, den = 0.093364 + 0.028009
    assert rho[0, 1] == pytest.approx(0.229814, rel=1e-5)
    # identical to the legacy engine matrix (byte-identical default path)
    assert np.array_equal(rho, engine_mod._cqc_matrix([w1, w2], z))


def test_cqc_combination_two_modes():
    r = [3.0, -2.0]
    w = [10.0, 12.0]
    rho = mc.cqc_rho(w, 0.05)[0, 1]
    hand = math.sqrt(9 + 4 + 2 * rho * 3 * (-2))
    assert mc.combine_values(r, "CQC", w) == pytest.approx(hand, rel=1e-14)


def test_srss_combination():
    assert mc.combine_values([3.0, -4.0], "SRSS", [1, 9]) == \
        pytest.approx(5.0, rel=1e-15)


def test_abs_combination_sum_of_magnitudes():
    assert mc.combine_values([3.0, -4.0, 0.5], "ABS", [1, 2, 3]) == \
        pytest.approx(7.5, rel=1e-15)


def test_nrc10_grouping_closely_spaced():
    # f 1.00 & 1.05 within 10% -> grouped; 2.0 is not close to either
    w = [2 * math.pi * f for f in (1.0, 1.05, 2.0)]
    c = mc.nrc10_close(w)
    assert c[0, 1] == 1.0 and c[0, 2] == 0.0 and c[1, 2] == 0.0
    r = [3.0, -2.0, 1.0]
    hand = math.sqrt(9 + 4 + 1 + 2 * abs(3.0 * -2.0))
    assert mc.combine_values(r, "NRC10", w) == pytest.approx(hand, rel=1e-14)


def test_nrc10_boundary_and_no_groups_equals_srss():
    w = [2 * math.pi * f for f in (1.0, 1.10)]        # exactly 10% -> close
    assert mc.nrc10_close(w)[0, 1] == 1.0
    w2 = [2 * math.pi * f for f in (1.0, 1.2, 2.0)]   # nothing close
    r = [3.0, -2.0, 1.0]
    assert mc.combine_values(r, "NRC10", w2) == pytest.approx(
        math.sqrt(14.0), rel=1e-15)


def test_dsc_rosenblueth_hand_value():
    z, td = 0.05, 10.0
    w1, w2 = 10.0, 11.0
    wd1, wd2 = w1 * math.sqrt(1 - z * z), w2 * math.sqrt(1 - z * z)
    z1, z2 = z + 2 / (td * w1), z + 2 / (td * w2)
    eps = 1.0 / (1.0 + ((wd1 - wd2) / (z1 * w1 + z2 * w2)) ** 2)
    r = [2.0, 1.5]
    hand = math.sqrt(4 + 2.25 + 2 * eps * 2.0 * 1.5)
    assert mc.dsc_eps([w1, w2], z, td)[0, 1] == pytest.approx(eps, rel=1e-14)
    assert mc.combine_values(r, "DSC", [w1, w2], zeta=z, td=td) == \
        pytest.approx(hand, rel=1e-14)
    assert 0.0 < eps < 1.0


def test_gmc_alpha_clamped_log_rule():
    a = mc.rigid_alphas([0.5, 1.0, 5.0, 33.0, 60.0], 1.0, 33.0)
    assert a[0] == 0.0 and a[1] == 0.0
    assert a[2] == pytest.approx(math.log(5.0) / math.log(33.0), rel=1e-15)
    assert a[3] == 1.0 and a[4] == 1.0


def test_gmc_three_mode_hand_combination():
    f = [0.5, 5.0, 50.0]                       # periodic, mixed, rigid
    w = [2 * math.pi * x for x in f]
    r = [4.0, -3.0, 2.0]
    al = [0.0, math.log(5.0) / math.log(33.0), 1.0]
    rr = sum(a * x for a, x in zip(al, r))
    p = np.array([math.sqrt(1 - a * a) * x for a, x in zip(al, r)])
    rp = math.sqrt(p @ mc.cqc_rho(w, 0.05) @ p)
    hand = math.sqrt(rr * rr + rp * rp)
    assert mc.combine_values(r, "GMC", w, f1=1.0, f2=33.0) == \
        pytest.approx(hand, rel=1e-14)


def test_gmc_all_rigid_is_algebraic_sum():
    w = [2 * math.pi * x for x in (40.0, 50.0, 70.0)]
    r = [4.0, -3.0, 2.0]
    assert mc.combine_values(r, "GMC", w, f1=1.0, f2=33.0) == \
        pytest.approx(3.0, rel=1e-14)        # |4 - 3 + 2|, not ABS 9


def test_rigid_response_split_on_srss():
    f = [0.5, 10.0]
    w = [2 * math.pi * x for x in f]
    r = [3.0, 2.0]
    a2 = math.log(10.0) / math.log(33.0)
    rr = a2 * 2.0
    rp = math.sqrt(3.0 ** 2 + ((1 - a2 * a2) ** 0.5 * 2.0) ** 2)
    comb = mc.ModalCombiner("SRSS", w, rigid_response=True)
    assert comb.split(r) == pytest.approx((rr, rp), rel=1e-14)
    assert comb(r) == pytest.approx(math.hypot(rr, rp), rel=1e-14)
    # without the rigid split it is plain SRSS
    assert mc.combine_values(r, "SRSS", w) == pytest.approx(
        math.sqrt(13.0), rel=1e-15)


def test_missing_mass_extra_rigid_mode():
    w = [2 * math.pi * 2.0]
    comb = mc.ModalCombiner("CQC", w, n_rigid_extra=1)
    # one periodic mode r1 = 3, missing-mass response 4 -> sqrt(9 + 16)
    assert comb([3.0, 4.0]) == pytest.approx(5.0, rel=1e-15)
    comb_r = mc.ModalCombiner("GMC", [2 * math.pi * 50.0], n_rigid_extra=1)
    assert comb_r([3.0, 4.0]) == pytest.approx(7.0, rel=1e-15)


def test_invalid_method_and_parameters_rejected():
    with pytest.raises(ValueError):
        mc.ModalCombiner("FOO", [1.0])
    mdl = _two_mass_column()
    spec = [[0.0, 0.4], [10.0, 0.4]]
    with pytest.raises(ValueError):
        mdl.add_rs_case("BAD", "X", spec, combo_method="FOO")
    with pytest.raises(ValueError):
        mdl.add_rs_case("BAD", "X", spec, combo_method="GMC",
                        gmc_f1=10.0, gmc_f2=5.0)
    with pytest.raises(ValueError):
        mdl.add_rs_case("BAD", "X", spec, combo_method="DSC", dsc_td=0.0)


def test_rs_case_serialisation_roundtrip():
    rs = ResponseSpectrumCase("R", "X", [[0.0, 0.4]])
    d = rs.to_dict()
    assert set(d) == {"name", "direction", "spectrum", "num_modes",
                      "combo_method", "damping", "scale", "function"}
    mdl = _two_mass_column()
    mdl.add_rs_case("G", "X", [[0.0, 0.4]], combo_method="GMC",
                    gmc_f1=2.0, gmc_f2=20.0, rigid_response=True,
                    include_missing_mass=True)
    mdl.add_rs_case("D", "Y", [[0.0, 0.4]], combo_method="DSC", dsc_td=7.5)
    back = BuildingModel.from_dict(json.loads(json.dumps(mdl.to_dict())))
    g, dd = back.rs_cases["G"], back.rs_cases["D"]
    assert (g.combo_method, g.gmc_f1, g.gmc_f2, g.rigid_response,
            g.include_missing_mass) == ("GMC", 2.0, 20.0, True, True)
    assert (dd.combo_method, dd.dsc_td) == ("DSC", 7.5)


# --------------------------------------------------------------------------- #
# 2. engine exactness on the 2-mass cantilever
# --------------------------------------------------------------------------- #
L_, M1, M2, SA = 6.0, 8.0, 5.0, 0.4
SPEC = [[0.0, SA], [10.0, SA]]


@pytest.fixture(scope="module")
def two_mass_results():
    mdl = _two_mass_column(L=L_, m1=M1, m2=M2)
    mdl.add_rs_case("CQC", "X", SPEC)
    mdl.add_rs_case("ABS", "X", SPEC, combo_method="ABS")
    mdl.add_rs_case("NRC", "X", SPEC, combo_method="NRC10")
    mdl.add_rs_case("DSC", "X", SPEC, combo_method="DSC", dsc_td=15.0)
    mdl.add_rs_case("GMC", "X", SPEC, combo_method="GMC",
                    gmc_f1=1.0, gmc_f2=20.0)
    mdl.add_rs_case("SRSSR", "X", SPEC, combo_method="SRSS",
                    rigid_response=True, gmc_f1=1.0, gmc_f2=20.0)
    mdl.add_rs_case("RIGID", "X", SPEC, combo_method="GMC",
                    gmc_f1=0.01, gmc_f2=0.02)
    mdl.add_rs_case("MM1", "X", SPEC, combo_method="GMC", num_modes=1,
                    gmc_f1=0.01, gmc_f2=0.02, include_missing_mass=True)
    mdl.add_rs_case("M1", "X", SPEC, combo_method="GMC", num_modes=1,
                    gmc_f1=0.01, gmc_f2=0.02)
    mdl.add_rs_case("MMC", "X", SPEC, combo_method="CQC", num_modes=1,
                    include_missing_mass=True)
    mdl.add_rs_case("MMALL", "X", SPEC, combo_method="GMC",
                    gmc_f1=0.01, gmc_f2=0.02, include_missing_mass=True)
    mdl.add_rs_case("GY", "Y", SPEC, combo_method="GMC")
    mdl.rs_combos["DIR"] = {"name": "DIR", "name_x": "GMC", "name_y": "GY",
                            "method": "100_30"}
    eng = OpenSeesEngine(mdl)
    d = eng.run().to_dict()
    return mdl, eng, d


def test_engine_methods_match_hand_combination(two_mass_results):
    _, _, d = two_mass_results
    mid, tip, parts = _modal_parts(d, L_, M1, M2, SA)
    w = [p[3] for p in parts]
    for case, kw in (("ABS", dict(method="ABS")),
                     ("NRC", dict(method="NRC10")),
                     ("DSC", dict(method="DSC", td=15.0)),
                     ("GMC", dict(method="GMC", f2=20.0)),
                     ("SRSSR", dict(method="SRSS", f2=20.0,
                                    rigid_response=True))):
        method = kw.pop("method")
        rs = d["rs_cases"][case]
        for j, node in ((0, mid), (1, tip)):
            hand = mc.combine_values([p[j] for p in parts], method, w, **kw)
            assert rs["node_disp"][node][0] == pytest.approx(hand, rel=1e-9)
        hand_v = mc.combine_values([p[2] for p in parts], method, w, **kw)
        assert rs["base"]["FX"] == pytest.approx(hand_v, rel=1e-9)
    # ABS explicitly: sum of |modal base shears|
    assert d["rs_cases"]["ABS"]["base"]["FX"] == pytest.approx(
        sum(abs(p[2]) for p in parts), rel=1e-9)
    # well separated modes: NRC10 groups nothing -> SRSS
    assert d["rs_cases"]["NRC"]["base"]["FX"] == pytest.approx(
        math.sqrt(sum(p[2] ** 2 for p in parts)), rel=1e-9)


def test_engine_all_rigid_gmc_equals_static_full_mass(two_mass_results):
    """All modes rigid (f > f2): algebraic modal sum == static M*iota*Sa*g."""
    _, _, d = two_mass_results
    assert d["rs_cases"]["RIGID"]["base"]["FX"] == pytest.approx(
        (M1 + M2) * SA * G_ACCEL, rel=1e-9)


def test_missing_mass_restores_full_mass_base_shear(two_mass_results):
    """1 mode + missing mass, rigid excitation: V == (m1+m2) Sa g exactly."""
    _, _, d = two_mass_results
    v_total = (M1 + M2) * SA * G_ACCEL
    v_m1 = d["rs_cases"]["M1"]["base"]["FX"]
    assert v_m1 < 0.95 * v_total              # truncated modes miss mass
    assert d["rs_cases"]["MM1"]["base"]["FX"] == pytest.approx(v_total,
                                                               rel=1e-9)
    # displacements too: rigid excitation static solution u = K^-1 M 1 Sa g
    EI = E_CONC * 0.3 ** 4 / 12.0
    a, b = L_ / 2, L_
    f = lambda x, y: x * x * (3 * y - x) / (6 * EI)  # noqa: E731
    F = np.array([[f(a, a), f(a, b)], [f(a, b), f(b, b)]])
    u_static = F @ (np.array([M1, M2]) * SA * G_ACCEL)
    tip = _find_node(d, (0, 0, L_))
    assert d["rs_cases"]["MM1"]["node_disp"][tip][0] == pytest.approx(
        u_static[1], rel=1e-6)
    # with ALL modes the residual mass vanishes: identical to RIGID
    assert d["rs_cases"]["MMALL"]["base"]["FX"] == pytest.approx(
        d["rs_cases"]["RIGID"]["base"]["FX"], rel=1e-9)


def test_missing_mass_with_periodic_cqc_is_srss_of_parts(two_mass_results):
    """CQC (no rigid split) + missing mass: sqrt(V1^2 + Vmm^2)."""
    _, _, d = two_mass_results
    _, _, parts = _modal_parts(d, L_, M1, M2, SA)
    v1 = parts[0][2]
    v_mm = (M1 + M2) * SA * G_ACCEL - v1      # residual mass is rigid-static
    assert d["rs_cases"]["MMC"]["base"]["FX"] == pytest.approx(
        math.hypot(v1, v_mm), rel=1e-9)


def test_directional_combination_composes_with_new_methods(two_mass_results):
    _, eng, d = two_mass_results
    gx = d["rs_cases"]["GMC"]
    gy = d["rs_cases"]["GY"]
    dr = d["rs_cases"]["DIR"]
    tip = _find_node(d, (0, 0, L_))
    for q in ("FX", "FY"):
        a, b = abs(gx["base"][q]), abs(gy["base"][q])
        assert dr["base"][q] == pytest.approx(max(a + 0.3 * b, 0.3 * a + b),
                                              rel=1e-12, abs=1e-12)
    a, b = gx["node_disp"][tip][0], gy["node_disp"][tip][0]
    assert dr["node_disp"][tip][0] == pytest.approx(
        max(a + 0.3 * b, 0.3 * a + b), rel=1e-12)
    srss = eng.run_rs_directional("GMC", "GY", method="SRSS")
    assert srss.base["FX"] == pytest.approx(
        math.hypot(gx["base"]["FX"], gy["base"]["FX"]), rel=1e-12)


def test_default_cqc_byte_identical_to_legacy_path(two_mass_results):
    """Default case == the untouched legacy CQC combination, byte for byte."""
    mdl, eng, d = two_mass_results
    rs = mdl.rs_cases["CQC"]
    assert mc.is_legacy(rs)
    modal = eng.run_modal(None)
    mass_map = dict(eng._asm.mass_map)
    per_mode, omegas = [], []
    for i, T in enumerate(modal.periods, start=1):
        g = modal.participation[i - 1]["gamma_x"]
        sa = engine_mod._interp_spectrum(SPEC, T) * rs.scale * G_ACCEL
        loads = {(t, dof): g * sa * m * modal.shapes[i][t][dof - 1]
                 for (t, dof), m in mass_map.items()}
        omegas.append(2.0 * math.pi / T)
        per_mode.append(eng._modal_static(loads))
    legacy = OpenSeesEngine._combine_rsa(
        "CQC", per_mode, engine_mod._cqc_matrix(omegas, rs.damping))
    assert json.dumps(legacy.to_dict(), sort_keys=True) == json.dumps(
        d["rs_cases"]["CQC"], sort_keys=True)
    # and the default model JSON carries no new keys
    assert "gmc_f1" not in json.dumps(mdl.rs_cases["CQC"].to_dict())


# --------------------------------------------------------------------------- #
# 3. load participation ratios
# --------------------------------------------------------------------------- #
def test_load_participation_shape_and_existing_keys(two_mass_results):
    _, _, d = two_mass_results
    m = d["modal"]
    assert {"periods", "frequencies", "participation", "shapes",
            "load_participation"} <= set(m)
    lp = m["load_participation"]
    assert set(lp) == {"acceleration", "patterns"}
    assert set(lp["acceleration"]) == {"UX", "UY", "UZ"}
    assert set(lp["patterns"]) == {"PX"}
    for v in list(lp["acceleration"].values()) + list(lp["patterns"].values()):
        assert set(v) == {"static", "dynamic"}


def test_load_participation_all_modes_100_percent(two_mass_results):
    _, _, d = two_mass_results
    lp = d["modal"]["load_participation"]
    assert lp["acceleration"]["UX"]["static"] == pytest.approx(100.0, rel=1e-7)
    assert lp["acceleration"]["UX"]["dynamic"] == pytest.approx(100.0,
                                                                rel=1e-9)
    # tip point load on a massed DOF: fully captured by the two modes
    assert lp["patterns"]["PX"]["static"] == pytest.approx(100.0, rel=1e-7)
    assert lp["patterns"]["PX"]["dynamic"] == pytest.approx(100.0, rel=1e-9)
    # no Y / Z mass -> zero
    assert lp["acceleration"]["UY"] == {"static": 0.0, "dynamic": 0.0}
    assert lp["acceleration"]["UZ"] == {"static": 0.0, "dynamic": 0.0}


def test_load_participation_truncated_matches_closed_form():
    """1 of 2 modes: dynamic == mode-1 mass ratio; static == Wilson hand."""
    L, size = 6.0, 0.3
    mdl = _two_mass_column(L=L, size=size, m1=M1, m2=M2)
    mdl.num_modes = 1
    d = OpenSeesEngine(mdl).run().to_dict()
    lp = d["modal"]["load_participation"]["acceleration"]["UX"]
    assert lp["dynamic"] == pytest.approx(
        100.0 * d["modal"]["participation"][0]["ux"], rel=1e-9)
    # closed form (flexibility) Wilson static ratio of mode 1
    EI = E_CONC * size ** 4 / 12.0
    a, b = L / 2, L
    f = lambda x, y: x * x * (3 * y - x) / (6 * EI)  # noqa: E731
    F = np.array([[f(a, a), f(a, b)], [f(a, b), f(b, b)]])
    K = np.linalg.inv(F)
    M = np.diag([M1, M2])
    lam, vec = np.linalg.eig(np.linalg.solve(M, K))
    k = int(np.argmin(lam))
    phi = vec[:, k]
    r = M @ np.ones(2)
    hand = (phi @ r) ** 2 / (lam[k] * (phi @ M @ phi)) / (r @ F @ r)
    assert lp["static"] == pytest.approx(100.0 * hand, rel=1e-6)
    assert 0.0 < lp["static"] < 100.0
    assert lp["static"] > lp["dynamic"]     # Wilson: static converges faster


def test_load_participation_member_udl_closed_form():
    """Cantilever + lateral UDL, tip mass: static = (3/64)/(7/144) = 27/28.

    One mode, massed tip ux only.  phi^T r = k u_tip (u_tip = wL^4/8EI,
    k = 3EI/L^3, omega^2 = k/m) so the numerator is k u_tip^2 =
    3 w^2 L^5 / (64 EI).  The consistent tip load of one exact cubic
    element is (wL/2, -wL^2/12) with tip rotation wL^3/6EI, so
    r^T K^-1 r = w^2 L^5/EI (1/16 - 1/72) = 7 w^2 L^5 / (144 EI)
    -> ratio 432/448 = 96.43%.  The 3.57% missing is the load on the
    MASSLESS tip rotation.  Dynamic: one massed DOF -> 100%.
    """
    L, size, m, w = 3.0, 0.3, 10.0, 2.5
    mdl = _new_model("UDL", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m))
    mdl.pattern("W", "other").member_loads.append(
        MemberLoad("C1", "udl", w, direction="global_x"))
    mdl.add_case("W", {"W": 1.0})
    mdl.num_modes = 1
    d = OpenSeesEngine(mdl).run().to_dict()
    lp = d["modal"]["load_participation"]["patterns"]["W"]
    assert lp["static"] == pytest.approx(100.0 * 27.0 / 28.0, rel=1e-6)
    assert lp["dynamic"] == pytest.approx(100.0, rel=1e-9)


def test_load_participation_rigid_diaphragm_building():
    """Quick building (rigid diaphragms, story masses): all modes -> 100%."""
    from skyframe.core.builder import quick_building
    mdl = quick_building()
    eng = OpenSeesEngine(mdl)
    n_massed = None
    modal = eng.run_modal()
    asm = eng._asm
    n_massed = asm.free_massed_dofs()
    if len(modal.periods) < n_massed:
        modal = eng.run_modal(n_massed)
    lp = mc.load_participation(eng, modal)
    for key in ("UX", "UY"):
        assert lp["acceleration"][key]["dynamic"] == pytest.approx(
            100.0, rel=1e-6)
        assert lp["acceleration"][key]["static"] == pytest.approx(
            100.0, rel=1e-6)
        cum = sum(p["ux" if key == "UX" else "uy"]
                  for p in modal.participation)
        assert lp["acceleration"][key]["dynamic"] == pytest.approx(
            100.0 * cum, rel=1e-9)
    for name, v in lp["patterns"].items():
        assert -1e-9 <= v["static"] <= 100.0 + 1e-6, name
        assert -1e-9 <= v["dynamic"] <= 100.0 + 1e-6, name


def test_load_participation_diaphragm_patterns_condense_to_master():
    """Story force (master) and an in-plane load on a SLAVE node: both live
    entirely on the condensed massed DOFs -> 100% static and dynamic with
    all modes; EQX (existing story-force pattern) likewise.  DEAD (gravity
    member loads, massless DOFs) has ~0% dynamic participation."""
    from skyframe.core.builder import quick_building
    from skyframe.core.model import StoryForce
    mdl = quick_building()
    top = mdl.stories[-1]
    mdl.pattern("SF", "other").story_forces.append(
        StoryForce(top.name, fx=10.0, fy=3.0))
    corner = next(m.pj for m in mdl.members
                  if m.kind == "column" and abs(m.pj[2] - top.elevation) < 1e-9)
    mdl.pattern("SLV", "other").nodal_loads.append(
        NodalLoad(corner, fx=4.0, fy=-2.0))
    eng = OpenSeesEngine(mdl)
    eng.run_modal()
    n_massed = eng._asm.free_massed_dofs()
    modal = eng.run_modal(n_massed)
    assert len(modal.periods) == n_massed
    lp = mc.load_participation(eng, modal)["patterns"]
    for name in ("SF", "SLV", "EQX"):
        assert lp[name]["static"] == pytest.approx(100.0, rel=1e-6), name
        assert lp[name]["dynamic"] == pytest.approx(100.0, rel=1e-6), name
    assert lp["DEAD"]["dynamic"] == pytest.approx(0.0, abs=1e-6)
