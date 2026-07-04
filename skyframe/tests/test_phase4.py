"""Phase 4 backend tests: RSA, P-Delta, model save/open API, section library.

Same rigor as test_analytical_benchmarks.py: every expected number is derived
in the test from first principles (SDOF/2-DOF modal mechanics, independent
numpy stiffness/flexibility assemblies, exact unit conversions) — nothing is
fabricated.

Response-spectrum analysis (RSA):
  * SDOF cantilever, flat spectrum: u = Sa*g/omega^2 = Sa*g*m/k exactly
    (Gamma*phi = 1 at the single mass DOF), base shear = m*Sa*g exactly.
  * 2-mass cantilever: engine SRSS/CQC vs numpy combination of per-mode
    responses computed from the engine's OWN modal output (internal
    consistency, 1e-9) AND vs a fully independent closed-form flexibility
    eigen solution (1e-6; Euler elements are flexibility-exact here).
  * CQC ~ SRSS for well-separated modes; CQC >= each single-mode response.
  * Spectrum interpolation is linear between points, clamped at the ends.

P-Delta:
  * OpenSees' 'PDelta' geomTransf is the linearized "lean-column" geometric
    stiffness: it adds -P/L on the transverse sway translations (no
    rotational geometric terms).  The engine is verified against an
    independent numpy assembly of exactly that formulation, same
    discretization, at P/Pcr = 0.25 (amplification ~1.31 vs the continuous
    P-Delta closed form 1/(1-P/Pcr) = 1.333 — the difference is the
    lean-column-vs-consistent-geometric-stiffness discretization, which is
    the documented OpenSees behavior).
  * pdelta=True drifts exceed pdelta=False; a pure-gravity P-Delta case
    matches the linear one to <0.1% (tiny axial-shortening effect only).

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import json
import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.builder import quick_building
from skyframe.core.model import (
    G_ACCEL,
    BuildingModel,
    FrameSection,
    Material,
    NodalLoad,
    NodalMass,
    PointSupport,
)

E_CONC = 25_000_000.0  # kPa


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(model) -> dict:
    return OpenSeesEngine(model).run().to_dict()


def _find_node(results: dict, pt, tol=1e-6):
    for tag, xyz in results["nodes"].items():
        if all(abs(a - b) < tol for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(f"no FE node found at {pt}")


def _new_model(name: str, story_heights) -> BuildingModel:
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories(list(story_heights))
    return mdl


def _sdof_column(m=10.0, L=3.0, size=0.3) -> BuildingModel:
    """Cantilever column, tip mass on ux only: exact SDOF, k = 3EI/L^3."""
    mdl = _new_model("SDOF", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m))
    mdl.pattern("PX", "other").nodal_loads.append(NodalLoad((0, 0, L), fx=1.0))
    mdl.add_case("PX", {"PX": 1.0})
    mdl.num_modes = 1
    return mdl


def _two_mass_column(L=6.0, size=0.3, m1=8.0, m2=5.0) -> BuildingModel:
    """Cantilever column of height L, masses m1 at L/2 and m2 at L (ux)."""
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


def _cantilever_2dof_modal(L, EI, m1, m2):
    """Independent closed-form 2-DOF modal solution of the 2-mass cantilever.

    Flexibility (unit load at height b, deflection at height a <= b):
    f(a, b) = a^2 (3b - a) / (6 EI)  — standard cantilever influence
    coefficients; K = F^-1; generalized eig via the symmetric M^-1/2
    transformation.  Returns (omegas asc, Gamma, phi columns).
    """
    a, b = L / 2.0, L
    f = lambda x, y: x * x * (3.0 * y - x) / (6.0 * EI)  # noqa: E731
    F = np.array([[f(a, a), f(a, b)], [f(a, b), f(b, b)]])
    K = np.linalg.inv(F)
    M = np.diag([m1, m2])
    Mih = np.diag([1.0 / math.sqrt(m1), 1.0 / math.sqrt(m2)])
    lam, psi = np.linalg.eigh(Mih @ K @ Mih)
    omegas = np.sqrt(lam)
    phi = Mih @ psi                       # columns = mode shapes (mass dofs)
    ones = np.ones(2)
    gammas = np.array([(phi[:, i] @ M @ ones) / (phi[:, i] @ M @ phi[:, i])
                       for i in range(2)])
    return omegas, gammas, phi


def _cqc_rho(omegas, zeta):
    """Standard CQC correlation matrix (constant damping)."""
    n = len(omegas)
    rho = np.eye(n)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            bta = omegas[i] / omegas[j]
            rho[i, j] = (8.0 * zeta ** 2 * (1.0 + bta) * bta ** 1.5
                         / ((1.0 - bta ** 2) ** 2
                            + 4.0 * zeta ** 2 * bta * (1.0 + bta) ** 2))
    return rho


# --------------------------------------------------------------------------- #
# RSA: SDOF exactness
# --------------------------------------------------------------------------- #
def test_rsa_sdof_exact():
    """Flat spectrum Sa = 0.4 g on the exact SDOF:

    Gamma*phi = 1 at the single mass DOF, so the modal displacement is
    u = Sa*g/omega^2 = Sa*g*m/k with k = 3EI/L^3 (elasticBeamColumn is
    exact for a tip load) and the base shear is m*Sa*g.  1e-9 relative.
    """
    m, L, size, Sa = 10.0, 3.0, 0.3, 0.4
    I = size ** 4 / 12.0
    k = 3.0 * E_CONC * I / L ** 3
    mdl = _sdof_column(m=m, L=L, size=size)
    mdl.add_rs_case("RSX", "X", [[0.0, Sa], [10.0, Sa]], combo_method="SRSS")
    mdl.add_rs_case("RSXC", "X", [[0.0, Sa], [10.0, Sa]], combo_method="CQC")
    mdl.add_rs_case("RSY", "Y", [[0.0, Sa], [10.0, Sa]], combo_method="SRSS")

    d = _run(mdl)
    assert set(d["rs_cases"]) == {"RSX", "RSXC", "RSY"}
    rs = d["rs_cases"]["RSX"]
    tip = _find_node(d, (0, 0, L))

    u_exact = Sa * G_ACCEL * m / k
    v_exact = m * Sa * G_ACCEL
    assert rs["node_disp"][tip][0] == pytest.approx(u_exact, rel=1e-9)
    assert rs["base"]["FX"] == pytest.approx(v_exact, rel=1e-9)
    assert abs(rs["base"]["MY"]) == pytest.approx(v_exact * L, rel=1e-9)
    assert rs["story"]["Story1"]["ux"] == pytest.approx(u_exact, rel=1e-9)
    assert rs["story"]["Story1"]["shear_x"] == pytest.approx(v_exact, rel=1e-9)
    assert rs["story"]["Story1"]["drift_x"] == pytest.approx(u_exact / L,
                                                             rel=1e-9)
    # column shear at every station equals the base shear (single tip force)
    st = rs["member_stations"]["C1"]
    for v in st["V2"] + st["V3"]:
        assert math.hypot(v, 0.0) <= v_exact * (1 + 1e-9)
    assert max(abs(v) for v in st["V2"] + st["V3"]) == pytest.approx(
        v_exact, rel=1e-9)

    # a single mode: CQC == SRSS identically
    rsc = d["rs_cases"]["RSXC"]
    assert rsc["node_disp"][tip][0] == pytest.approx(
        rs["node_disp"][tip][0], rel=1e-12)

    # RSA results are positive envelopes
    assert rs["base"]["FX"] > 0.0 and rs["node_disp"][tip][0] > 0.0

    # no mass in Y: the Y-direction RS case has zero response
    assert d["rs_cases"]["RSY"]["base"]["FY"] == pytest.approx(0.0, abs=1e-12)
    assert d["rs_cases"]["RSY"]["node_disp"][tip][0] == pytest.approx(
        0.0, abs=1e-15)

    # modal participation factors are exposed and consistent: for the SDOF,
    # Gamma * phi_tip = 1 exactly
    p1 = d["modal"]["participation"][0]
    phi_tip = d["modal"]["shapes"]["1"][tip][0]
    assert p1["gamma_x"] * phi_tip == pytest.approx(1.0, rel=1e-9)
    assert p1["gamma_y"] == pytest.approx(0.0, abs=1e-12)


# --------------------------------------------------------------------------- #
# RSA: two-mode SRSS/CQC vs numpy, and closed-form cross-check
# --------------------------------------------------------------------------- #
def test_rsa_two_mode_combination():
    """2-mass cantilever, flat 0.4 g spectrum.

    (1) Internal consistency at 1e-9: per-mode responses computed in numpy
        from the ENGINE's own modal output (gamma_x, shapes, periods) via
        u_i = Gamma_i Sa g / omega_i^2 * phi_i and base shear
        V_i = Gamma_i * L_i * Sa g, combined SRSS/CQC, must equal the
        engine's combined RSA results (the engine does exact modal statics).
    (2) Independent cross-check at 1e-6: the same quantities from a
        closed-form flexibility eigen solution (no engine data at all).
    (3) CQC ~ SRSS (<2%) for these well-separated modes; CQC >= the
        single-mode response (num_modes=1 case).
    """
    L, size, m1, m2, Sa = 6.0, 0.3, 8.0, 5.0, 0.4
    EI = E_CONC * size ** 4 / 12.0
    zeta = 0.05
    mdl = _two_mass_column(L=L, size=size, m1=m1, m2=m2)
    spec = [[0.0, Sa], [10.0, Sa]]
    mdl.add_rs_case("SRSS", "X", spec, combo_method="SRSS")
    mdl.add_rs_case("CQC", "X", spec, combo_method="CQC", damping=zeta)
    mdl.add_rs_case("M1", "X", spec, combo_method="SRSS", num_modes=1)

    d = _run(mdl)
    mid = _find_node(d, (0, 0, L / 2))
    tip = _find_node(d, (0, 0, L))
    part = d["modal"]["participation"]
    periods = d["modal"]["periods"]
    assert len(periods) == 2

    # ---- (1) per-mode responses from the engine's own modal output -------
    sag = Sa * G_ACCEL
    u_modes = np.zeros((2, 2))       # [mode, node(mid,tip)]
    v_modes = np.zeros(2)            # base shear per mode
    mb_modes = np.zeros(2)           # base overturning per mode
    for i in (0, 1):
        om = 2.0 * math.pi / periods[i]
        gam = part[i]["gamma_x"]
        sh = d["modal"]["shapes"][str(i + 1)]
        phi = np.array([sh[mid][0], sh[tip][0]])
        u_modes[i] = gam * sag / om ** 2 * phi
        Lfac = m1 * phi[0] + m2 * phi[1]
        v_modes[i] = gam * sag * Lfac
        mb_modes[i] = gam * sag * (m1 * phi[0] * L / 2 + m2 * phi[1] * L)

    omegas = np.array([2.0 * math.pi / t for t in periods])
    rho = _cqc_rho(omegas, zeta)

    srss = d["rs_cases"]["SRSS"]
    cqc = d["rs_cases"]["CQC"]
    for j, node in enumerate((mid, tip)):
        u_srss = math.sqrt(u_modes[0, j] ** 2 + u_modes[1, j] ** 2)
        u_cqc = math.sqrt(u_modes[:, j] @ rho @ u_modes[:, j])
        assert srss["node_disp"][node][0] == pytest.approx(u_srss, rel=1e-9)
        assert cqc["node_disp"][node][0] == pytest.approx(u_cqc, rel=1e-9)
    v_srss = math.sqrt(v_modes @ v_modes)
    assert srss["base"]["FX"] == pytest.approx(v_srss, rel=1e-9)
    assert cqc["base"]["FX"] == pytest.approx(
        math.sqrt(v_modes @ rho @ v_modes), rel=1e-9)
    assert abs(srss["base"]["MY"]) == pytest.approx(
        math.sqrt(mb_modes @ mb_modes), rel=1e-9)

    # ---- (2) fully independent closed-form cross-check -------------------
    om_cf, gam_cf, phi_cf = _cantilever_2dof_modal(L, EI, m1, m2)
    # engine periods match the closed form (Euler flexibility is exact)
    for i in (0, 1):
        assert periods[i] == pytest.approx(2.0 * math.pi / om_cf[i], rel=1e-6)
    u_cf = np.array([gam_cf[i] * sag / om_cf[i] ** 2 * phi_cf[:, i]
                     for i in (0, 1)])              # [mode, node]
    v_cf = np.array([gam_cf[i] * sag
                     * (m1 * phi_cf[0, i] + m2 * phi_cf[1, i])
                     for i in (0, 1)])
    for j, node in enumerate((mid, tip)):
        assert srss["node_disp"][node][0] == pytest.approx(
            math.sqrt(u_cf[0, j] ** 2 + u_cf[1, j] ** 2), rel=1e-6)
    assert srss["base"]["FX"] == pytest.approx(
        math.sqrt(v_cf @ v_cf), rel=1e-6)

    # ---- (3) CQC vs SRSS, and CQC >= single-mode envelope ----------------
    assert om_cf[1] / om_cf[0] > 5.0            # well-separated by design
    assert abs(cqc["base"]["FX"] - srss["base"]["FX"]) \
        <= 0.02 * srss["base"]["FX"]
    assert abs(cqc["node_disp"][tip][0] - srss["node_disp"][tip][0]) \
        <= 0.02 * srss["node_disp"][tip][0]

    m1only = d["rs_cases"]["M1"]
    # num_modes=1 == |mode-1 response| exactly...
    assert m1only["node_disp"][tip][0] == pytest.approx(
        abs(u_modes[0, 1]), rel=1e-9)
    assert m1only["base"]["FX"] == pytest.approx(abs(v_modes[0]), rel=1e-9)
    # ...and the full CQC envelope can never fall below it (rho is PSD with
    # unit diagonal, so the combined value >= each |r_i|)
    assert cqc["node_disp"][tip][0] >= m1only["node_disp"][tip][0] * (1 - 1e-12)
    assert cqc["base"]["FX"] >= m1only["base"]["FX"] * (1 - 1e-12)


# --------------------------------------------------------------------------- #
# RSA: spectrum interpolation, clamping, scale
# --------------------------------------------------------------------------- #
def test_rsa_spectrum_interpolation_and_scale():
    """Two-point spectrum, T1 between the points: Sa is the LINEAR
    interpolation; verified through base shear V = m*Sa(T1)*g (Gamma*phi=1),
    1e-9.  Outside the tabulated range Sa clamps to the end value; ``scale``
    multiplies Sa.
    """
    m, L, size = 10.0, 3.0, 0.3
    I = size ** 4 / 12.0
    k = 3.0 * E_CONC * I / L ** 3
    T1 = 2.0 * math.pi * math.sqrt(m / k)

    tA, sA = T1 - 0.1, 0.5
    tB, sB = T1 + 0.3, 0.2
    sa_interp = sA + (sB - sA) * (T1 - tA) / (tB - tA)

    mdl = _sdof_column(m=m, L=L, size=size)
    mdl.add_rs_case("INT", "X", [[tA, sA], [tB, sB]], combo_method="SRSS")
    # entire spectrum below T1 -> clamp to the last (long-period) value
    mdl.add_rs_case("CLAMP", "X", [[0.05, 0.8], [0.10, 0.6]],
                    combo_method="SRSS")
    mdl.add_rs_case("SCALED", "X", [[0.0, 0.4], [10.0, 0.4]],
                    combo_method="SRSS", scale=2.0)

    d = _run(mdl)
    assert d["rs_cases"]["INT"]["base"]["FX"] == pytest.approx(
        m * sa_interp * G_ACCEL, rel=1e-9)
    assert d["rs_cases"]["CLAMP"]["base"]["FX"] == pytest.approx(
        m * 0.6 * G_ACCEL, rel=1e-9)
    assert d["rs_cases"]["SCALED"]["base"]["FX"] == pytest.approx(
        m * 0.8 * G_ACCEL, rel=1e-9)


# --------------------------------------------------------------------------- #
# P-Delta
# --------------------------------------------------------------------------- #
def _pdelta_column_model(L=6.0, size=0.3, P=None, H=5.0):
    """Two-element cantilever with tip axial P (gravity) and lateral H."""
    I = size ** 4 / 12.0
    Pcr = math.pi ** 2 * E_CONC * I / (4.0 * L ** 2)
    P = P if P is not None else 0.25 * Pcr
    mdl = _new_model("PD", [L / 2, L / 2])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L / 2),
                   story="Story1", uid="C1")
    mdl.add_member("column", "COL", (0, 0, L / 2), (0, 0, L),
                   story="Story2", uid="C2")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=1.0))
    mdl.pattern("G", "dead").nodal_loads.append(NodalLoad((0, 0, L), fz=-P))
    mdl.pattern("H", "other").nodal_loads.append(NodalLoad((0, 0, L), fx=H))
    mdl.add_case("G", {"G": 1.0})
    mdl.add_case("HLIN", {"H": 1.0})
    mdl.add_case("HPD", {"H": 1.0}, pdelta=True, pdelta_gravity={"G": 1.0})
    mdl.add_case("GPD", {"G": 1.0}, pdelta=True)   # gravity state = own loads
    mdl.num_modes = 1
    return mdl, P, Pcr


def test_pdelta_cantilever_vs_numpy_geometric_stiffness():
    """Engine P-Delta vs an independent numpy assembly of the SAME
    linearized formulation: 2D Euler bending stiffness minus the
    "lean-column" geometric stiffness P/Le on the transverse translations —
    which is exactly what OpenSees' 'PDelta' geomTransf adds.  Same
    2-element discretization, P/Pcr = 0.25, tolerance 1% (observed agreement
    is ~1e-15; the loose bound guards against solver iteration noise).

    Also: the amplification sits near (but, being a discretized P-Delta-only
    model, not exactly at) the continuous closed form 1/(1 - P/Pcr).
    """
    L, size, H = 6.0, 0.3, 5.0
    I = size ** 4 / 12.0
    mdl, P, Pcr = _pdelta_column_model(L=L, size=size, H=H)

    eng = OpenSeesEngine(mdl)
    u_pd = eng.run_static("HPD")
    u_lin = eng.run_static("HLIN")
    d_nodes = eng._asm.node_coords
    tip = next(t for t, c in d_nodes.items() if abs(c[2] - L) < 1e-9)

    # ---- independent numpy: (u, theta) per node, base fixed ---------------
    Le = L / 2.0
    EI = E_CONC * I

    def k_beam(EI, Le):
        return EI / Le ** 3 * np.array(
            [[12, 6 * Le, -12, 6 * Le],
             [6 * Le, 4 * Le * Le, -6 * Le, 2 * Le * Le],
             [-12, -6 * Le, 12, -6 * Le],
             [6 * Le, 2 * Le * Le, -6 * Le, 4 * Le * Le]])

    def kg_lean(P, Le):
        g = np.zeros((4, 4))
        g[0, 0] = g[2, 2] = 1.0
        g[0, 2] = g[2, 0] = -1.0
        return P / Le * g

    K = np.zeros((6, 6))
    for a, b in ((0, 1), (1, 2)):
        dofs = [2 * a, 2 * a + 1, 2 * b, 2 * b + 1]
        K[np.ix_(dofs, dofs)] += k_beam(EI, Le) - kg_lean(P, Le)
    free = [2, 3, 4, 5]
    F = np.zeros(4)
    F[2] = H                                  # lateral load at the tip
    u_np = np.linalg.solve(K[np.ix_(free, free)], F)[2]

    assert u_pd.node_disp[tip][0] == pytest.approx(u_np, rel=1e-2)
    assert u_pd.node_disp[tip][0] == pytest.approx(u_np, rel=1e-6)  # actual

    # linear case is the exact HL^3/3EI
    assert u_lin.node_disp[tip][0] == pytest.approx(
        H * L ** 3 / (3.0 * EI), rel=1e-9)

    # amplification: larger than 1, in the neighborhood of 1/(1 - P/Pcr)
    amp = u_pd.node_disp[tip][0] / u_lin.node_disp[tip][0]
    amp_exact = 1.0 / (1.0 - P / Pcr)
    assert amp > 1.05
    assert amp == pytest.approx(amp_exact, rel=0.05)

    # two-stage P-Delta reports the case's INCREMENT: no gravity axial in
    # the lateral case's reactions (base FZ ~ 0), base shear equilibrates H
    assert abs(u_pd.base["FZ"]) < 1e-6 * P
    assert u_pd.base["FX"] == pytest.approx(-H, rel=1e-6)


def test_pdelta_direction_and_gravity_only():
    """pdelta=True lateral drift > pdelta=False; pure-gravity P-Delta case
    matches the linear gravity case to <0.1% on the vertical displacement
    (only a tiny axial-shortening geometric effect remains)."""
    L = 6.0
    mdl, P, Pcr = _pdelta_column_model(L=L)
    eng = OpenSeesEngine(mdl)
    tip = None
    r_lin = eng.run_static("HLIN")
    r_pd = eng.run_static("HPD")
    tip = next(t for t, c in eng._asm.node_coords.items()
               if abs(c[2] - L) < 1e-9)
    assert r_pd.node_disp[tip][0] > 1.2 * r_lin.node_disp[tip][0]

    r_g = eng.run_static("G")
    r_gpd = eng.run_static("GPD")
    uz_lin = r_g.node_disp[tip][2]
    uz_pd = r_gpd.node_disp[tip][2]
    assert uz_lin < 0.0
    assert uz_pd == pytest.approx(uz_lin, rel=1e-3)


def test_pdelta_combo_superposition_warning():
    """A combo that superposes a P-Delta case carries a 'warning' field;
    an all-linear combo does not."""
    mdl, P, Pcr = _pdelta_column_model()
    mdl.add_combo("G + HPD", {"G": 1.0, "HPD": 1.0})
    mdl.add_combo("G + HLIN", {"G": 1.0, "HLIN": 1.0})
    d = _run(mdl)
    assert "warning" in d["combos"]["G + HPD"]
    assert "P-Delta" in d["combos"]["G + HPD"]["warning"]
    assert "warning" not in d["combos"]["G + HLIN"]


def test_rs_case_cannot_enter_combo():
    mdl = _sdof_column()
    mdl.add_rs_case("RSX", "X", [[0.0, 0.4], [10.0, 0.4]])
    with pytest.raises(ValueError, match="cannot enter a load combo"):
        mdl.add_combo("BAD", {"RSX": 1.0})


# --------------------------------------------------------------------------- #
# Model save/open API
# --------------------------------------------------------------------------- #
@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def _full_v03_model() -> BuildingModel:
    """Small building exercising every v0.3 model field (rs case, pdelta)."""
    mdl = quick_building(name="RoundTrip", bays_x=1, bays_y=1, stories=2)
    mdl.num_modes = 4
    mdl.add_rs_case("RSX", "X", [[0.0, 0.4], [0.5, 0.4], [3.0, 0.1]],
                    num_modes=4, combo_method="CQC", damping=0.05, scale=1.0)
    mdl.cases["EQX"].pdelta = True
    mdl.cases["EQX"].pdelta_gravity = {"DEAD": 1.0, "LIVE": 0.25}
    return mdl


def test_models_save_list_open_delete_roundtrip(api_client):
    mdl = _full_v03_model()
    saved = api_client.post("/api/model", json=mdl.to_dict())
    assert saved.status_code == 200, saved.get_json()
    dict_a = saved.get_json()

    # save under a name with every allowed character class
    r = api_client.post("/api/models/My Model_1-a")
    assert r.status_code == 200
    entry = r.get_json()
    assert entry["name"] == "My Model_1-a"
    assert entry["stories"] == 2
    assert entry["members"] == len(mdl.members)
    assert entry["mtime"] > 0

    # listing shows it
    lst = api_client.get("/api/models").get_json()
    assert [e["name"] for e in lst] == ["My Model_1-a"]
    assert lst[0]["stories"] == 2

    # clobber the in-memory model, then open the saved one back
    api_client.post("/api/model/quick", json={"stories": 3})
    assert len(api_client.get("/api/model").get_json()["stories"]) == 3
    opened = api_client.post("/api/models/My Model_1-a/open")
    assert opened.status_code == 200
    dict_b = opened.get_json()
    assert dict_b == dict_a          # exact round trip through disk

    # analysis results preserved: run both models, compare to 1e-12
    res_a = OpenSeesEngine(BuildingModel.from_dict(dict_a)).run().to_dict()
    res_b = OpenSeesEngine(BuildingModel.from_dict(dict_b)).run().to_dict()
    disp_a = res_a["cases"]["DEAD"]["node_disp"]
    disp_b = res_b["cases"]["DEAD"]["node_disp"]
    assert set(disp_a) == set(disp_b)
    for tag in disp_a:
        for i in range(6):
            assert abs(disp_a[tag][i] - disp_b[tag][i]) \
                <= 1e-12 + 1e-12 * abs(disp_a[tag][i])
    for key in ("FX", "FY", "FZ", "MX", "MY", "MZ"):
        assert abs(res_a["rs_cases"]["RSX"]["base"][key]
                   - res_b["rs_cases"]["RSX"]["base"][key]) \
            <= 1e-12 + 1e-12 * abs(res_a["rs_cases"]["RSX"]["base"][key])
    # the P-Delta EQX case survived the round trip and superposition combos
    # flag the approximation
    assert "warning" in res_b["combos"]["1.2D + 1.0L + 1.0EX"]

    # delete
    assert api_client.delete("/api/models/My Model_1-a").status_code == 200
    assert api_client.get("/api/models").get_json() == []
    assert api_client.delete("/api/models/My Model_1-a").status_code == 404


def test_models_api_bad_names_and_missing(api_client):
    for bad in ("bad*name", "a.b", "x" * 61, "semi;colon", "sp&ce"):
        assert api_client.post(f"/api/models/{bad}").status_code == 400, bad
        assert api_client.post(f"/api/models/{bad}/open").status_code == 400
        assert api_client.delete(f"/api/models/{bad}").status_code == 400
    assert api_client.post("/api/models/NotThere/open").status_code == 404
    assert api_client.delete("/api/models/NotThere").status_code == 404


def test_models_dir_env_override(api_client, tmp_path):
    """Files land in SKYFRAME_MODELS_DIR as <name>.skyframe.json."""
    api_client.post("/api/model/quick", json={"stories": 1, "bays_x": 1,
                                              "bays_y": 1})
    assert api_client.post("/api/models/EnvTest").status_code == 200
    path = tmp_path / "models" / "EnvTest.skyframe.json"
    assert path.is_file()
    d = json.loads(path.read_text())
    assert len(d["stories"]) == 1


# --------------------------------------------------------------------------- #
# Steel section library
# --------------------------------------------------------------------------- #
def test_section_library_w12x26_properties():
    """W12x26 SI properties vs independently stated AISC metric values
    (W310x38.7 soft-metric listing: A = 4940 mm^2, Ix = 84.9e6 mm^4,
    Iy = 7.20e6 mm^4, J = 125e3 mm^4), 0.5% tolerance."""
    sec = FrameSection.from_library("W12x26", "STEEL")
    assert sec.A == pytest.approx(4940e-6, rel=5e-3)
    assert sec.I33 == pytest.approx(84.9e-6, rel=5e-3)
    assert sec.I22 == pytest.approx(7.20e-6, rel=5e-3)
    assert sec.J == pytest.approx(125e3 * 1e-12, rel=5e-3)  # 125e3 mm^4
    # drawing dims: d = 12.2 in, bf = 6.49 in
    assert sec.h == pytest.approx(12.2 * 0.0254, rel=1e-9)
    assert sec.b == pytest.approx(6.49 * 0.0254, rel=1e-9)

    # the section is engine-usable inside a model
    mdl = _new_model("Steel", [3.0])
    mdl.add_material(Material("STEEL", E=200_000_000.0, nu=0.3,
                              unit_weight=77.0))
    mdl.add_section(sec)
    mdl.add_member("column", "W12x26", (0, 0, 0), (0, 0, 3.0),
                   story="Story1", uid="C1")
    mdl.validate()

    with pytest.raises(KeyError):
        FrameSection.from_library("W99x999", "STEEL")


def test_section_library_endpoint(api_client):
    r = api_client.get("/api/sections/library")
    assert r.status_code == 200
    lib = r.get_json()
    assert len(lib) >= 20
    names = [e["name"] for e in lib]
    assert "W8x31" in names and "W36x150" in names and "W12x26" in names
    for e in lib:
        for key in ("name", "A", "I33", "I22", "J", "b", "h"):
            assert key in e
        assert e["A"] > 0 and e["I33"] > e["I22"] > 0 and e["J"] > 0
    # exact conversion spot check: W36x150 Ix = 9040 in^4
    w36 = next(e for e in lib if e["name"] == "W36x150")
    assert w36["I33"] == pytest.approx(9040 * 0.0254 ** 4, rel=1e-12)
