"""ANALYSIS WAVE 2 (v0.25) validation — analysis parity II.

Every assertion is pinned by a hand derivation, a published benchmark, or
an exact cross-check against an independent solution path:

1. COROTATIONAL LARGE DISPLACEMENT — Mattiasson's (1981) elastica
   cantilever under a tip point load (PL^2/EI = 1: w/L = 0.30172,
   u/L = 0.05643) with 10 corotational elements to < 1%; the deep
   large-rotation regime PL^2/EI = 3 against an IN-TEST RK4 elastica ODE
   solver (itself validated against the Mattiasson row) to < 1%;
   corotational == linear for tiny loads to 1e-9 relative; the P-Delta
   path bit-identical to the legacy pdelta bool; precedence/serialization
   of LoadCase.geometric; a corotational pushover with the exact
   hand-series elastic slope.
2. BUCKLING FROM A STRESSED STATE — Euler column with base P0 =
   0.5*lambda0: remaining multiplier = 0.5*lambda0 EXACTLY (Kg linear in
   N -> lambda_base + lambda = lambda_no_base to machine precision);
   base at/beyond buckling -> honest warning, no factors; staged base
   equivalence through the engine force path.
3. FLOOR CRACKING — below-cracking run BIT-IDENTICAL to elastic; a
   uniformly-cracked simply supported strip lands EXACTLY at deflection
   1/cracked_ratio x elastic; Mcr = fr*t^2/6 hand value; monotone
   terminating iteration; API-shape dict.
4. TIME-DEPENDENT STAGED (AAEM) — a held load gives delta_total/
   delta_elastic = 1 + chi*phi exactly (the AAEM closed form; both t=inf
   and a finite t_eval on the ACI 209 curve); an unloaded column's
   shrinkage shortening = eps_sh(t)*L exactly via thermal equivalence;
   the two-story creep+shrinkage delta matches the longhand sum;
   time_dependent=None stays bit-identical to the elastic staged run.
"""

import math

import pytest

from skyframe.core.buckling import buckling_analysis
from skyframe.core.cracked import cracked_analysis
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 Material, NodalLoad, PointSupport,
                                 ShellSection)
from skyframe.engine.opensees_engine import (OpenSeesEngine, TD_CHI,
                                             aci209_creep,
                                             aci209_modulus_growth,
                                             aci209_shrinkage)

ops = pytest.importorskip("openseespy.opensees")


# --------------------------------------------------------------------------- #
# shared model helpers
# --------------------------------------------------------------------------- #
E_STEEL = 2.0e8         # kPa
E_CONC = 30.0e6         # kPa


def _horizontal_cantilever(n=10, L=2.0, size=0.1):
    """Horizontal cantilever of ``n`` chained beam members (10-element
    corotational discretization), fixed at the left end."""
    mdl = BuildingModel(name="elastica")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("S", E=E_STEEL, nu=0.3))
    sec = FrameSection.rectangular("B", "S", size, size)
    mdl.add_section(sec)
    mdl.set_stories([1.0])
    for i in range(n):
        mdl.add_member("beam", "B", (i * L / n, 0, 1.0),
                       ((i + 1) * L / n, 0, 1.0),
                       story="Story1", uid=f"B{i + 1}")
    mdl.supports.append(PointSupport((0, 0, 1.0), (1, 1, 1, 1, 1, 1)))
    return mdl, sec.I33, L


def _euler_column(L=3.0, size=0.2):
    """Single fixed-base column (buckling: Pcr = pi^2 EI/(2L)^2)."""
    mdl = BuildingModel(name="euler")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("S", E=E_STEEL, nu=0.3))
    sec = FrameSection.rectangular("C", "S", size, size)
    mdl.add_section(sec)
    mdl.set_stories([L])
    mdl.add_member("column", "C", (0, 0, 0), (0, 0, L),
                   story="Story1", uid="C1")
    return mdl, sec.I33, L


def _strip_model(q, mesh=0.5, t=0.2):
    """Simply supported one-way slab strip 6 x 1 m under area load q."""
    mdl = BuildingModel(name="strip")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=25.0e6, nu=0.2))
    mdl.add_shell_section(ShellSection("SLAB", "CONC", t))
    mdl.set_stories([3.0])
    mdl.add_shell("slab", "shell", "SLAB",
                  [(0, 0, 3.0), (6, 0, 3.0), (6, 1, 3.0), (0, 1, 3.0)],
                  mesh_size=mesh, story="Story1", uid="S1")
    for y in (0.0, 0.5, 1.0):
        mdl.supports.append(PointSupport((0, y, 3.0), (1, 1, 1, 0, 0, 0)))
        mdl.supports.append(PointSupport((6, y, 3.0), (1, 1, 1, 0, 0, 0)))
    pat = mdl.pattern("Q")
    pat.area_loads.append(AreaLoad("S1", q))
    mdl.add_case("Q", {"Q": 1.0})
    mdl.validate()
    return mdl


def _stacked_column(heights, P_per_story, td=None, uid_sec="COL"):
    """Multi-story single column, nodal load P at each story top."""
    mdl = BuildingModel(name="stack")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("C30", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular(uid_sec, "C30", 0.4, 0.4))
    mdl.set_stories(list(heights))
    pat = mdl.pattern("DEAD")
    z = 0.0
    for i, h in enumerate(heights):
        mdl.add_member("column", uid_sec, (0, 0, z), (0, 0, z + h),
                       story=f"Story{i + 1}", uid=f"C{i + 1}")
        z += h
        if P_per_story:
            pat.nodal_loads.append(NodalLoad((0, 0, z), fz=-P_per_story))
    mdl.add_staged_case("STG", pattern="DEAD", time_dependent=td)
    mdl.validate()
    return mdl


def _tip_tag(eng, x, z):
    return next(t for t, c in eng._asm.node_coords.items()
                if abs(c[0] - x) < 1e-9 and abs(c[1]) < 1e-9
                and abs(c[2] - z) < 1e-9)


# =========================================================================== #
# 1. corotational large displacement
# =========================================================================== #
def test_corotational_mattiasson_tip_load():
    """Mattiasson (1981) elastica cantilever, PL^2/EI = 1: w/L = 0.30172,
    u/L = 0.05643.  10 corotational elements land within 1%."""
    mdl, I, L = _horizontal_cantilever()
    P = E_STEEL * I / L ** 2
    mdl.pattern("P").nodal_loads.append(NodalLoad((L, 0, 1.0), fz=-P))
    mdl.add_case("CORO", {"P": 1.0}, geometric="corotational")
    mdl.validate()
    eng = OpenSeesEngine(mdl)
    r = eng.run_static("CORO")
    tip = _tip_tag(eng, L, 1.0)
    w_over_L = -r.node_disp[tip][2] / L
    u_over_L = -r.node_disp[tip][0] / L
    assert w_over_L == pytest.approx(0.30172, rel=0.01)
    assert u_over_L == pytest.approx(0.05643, rel=0.01)
    # sanity: the linear solution is far away (PL^3/3EI = L/3)
    assert abs(w_over_L - 1.0 / 3.0) > 0.02


def _elastica_tip_load_reference(alpha, n_steps=20000):
    """Independent elastica reference: cantilever, tip point load with
    PL^2/EI = alpha.  Exact ODE d(theta)/ds = M(s)/EI with M = P*(x_tip -
    x(s)) solved by RK4 + shooting on the unknown tip abscissa (pure
    numpy, no scipy).  Returns (w/L, u/L)."""
    def integrate(x_tip):
        # nondimensional: L = 1, EI = 1, P = alpha
        h = 1.0 / n_steps
        theta = x = w = 0.0

        def dtheta(xv):
            return alpha * (x_tip - xv)
        for _ in range(n_steps):
            k1t = dtheta(x)
            k1x, k1w = math.cos(theta), math.sin(theta)
            t2 = theta + 0.5 * h * k1t
            k2t = dtheta(x + 0.5 * h * k1x)
            k2x, k2w = math.cos(t2), math.sin(t2)
            t3 = theta + 0.5 * h * k2t
            k3t = dtheta(x + 0.5 * h * k2x)
            k3x, k3w = math.cos(t3), math.sin(t3)
            t4 = theta + h * k3t
            k4t = dtheta(x + h * k3x)
            k4x, k4w = math.cos(t4), math.sin(t4)
            theta += h / 6.0 * (k1t + 2 * k2t + 2 * k3t + k4t)
            x += h / 6.0 * (k1x + 2 * k2x + 2 * k3x + k4x)
            w += h / 6.0 * (k1w + 2 * k2w + 2 * k3w + k4w)
        return x, w
    lo, hi = 0.0, 1.0
    for _ in range(60):                     # bisection on x_tip
        mid = 0.5 * (lo + hi)
        x_end, _ = integrate(mid)
        if x_end > mid:
            lo = mid
        else:
            hi = mid
    x_end, w_end = integrate(0.5 * (lo + hi))
    return w_end, 1.0 - x_end


def test_elastica_reference_reproduces_mattiasson():
    """The in-test RK4 elastica solver itself reproduces Mattiasson's
    published PL^2/EI = 1 row (independent-reference sanity)."""
    w, u = _elastica_tip_load_reference(1.0)
    assert w == pytest.approx(0.30172, abs=2e-5)
    assert u == pytest.approx(0.05643, abs=2e-5)


def test_corotational_deep_elastica_vs_independent_ode():
    """Deep large-rotation regime PL^2/EI = 3 (tip deflection ~ 0.6 L):
    the 10-element corotational engine solution lands within 1% of the
    independent RK4 elastica reference — a cross-check at a load level
    where the linear answer (w/L = 1.0) is wrong by ~65%."""
    w_ref, u_ref = _elastica_tip_load_reference(3.0)
    mdl, I, L = _horizontal_cantilever()
    P = 3.0 * E_STEEL * I / L ** 2
    mdl.pattern("P").nodal_loads.append(NodalLoad((L, 0, 1.0), fz=-P))
    mdl.add_case("CORO", {"P": 1.0}, geometric="corotational")
    mdl.validate()
    eng = OpenSeesEngine(mdl)
    r = eng.run_static("CORO")
    tip = _tip_tag(eng, L, 1.0)
    assert -r.node_disp[tip][2] / L == pytest.approx(w_ref, rel=0.01)
    assert -r.node_disp[tip][0] / L == pytest.approx(u_ref, rel=0.01)


def test_corotational_equals_linear_for_tiny_load():
    """For an infinitesimal load the corotational case equals the linear
    case to 1e-9 relative (geometry effects vanish)."""
    mdl, I, L = _horizontal_cantilever()
    P = 1.0e-6 * E_STEEL * I / L ** 2
    mdl.pattern("P").nodal_loads.append(NodalLoad((L, 0, 1.0), fz=-P))
    mdl.add_case("CORO", {"P": 1.0}, geometric="corotational")
    mdl.add_case("LIN", {"P": 1.0})
    mdl.validate()
    eng = OpenSeesEngine(mdl)
    rc = eng.run_static("CORO")
    rl = OpenSeesEngine(mdl).run_static("LIN")
    tip = _tip_tag(eng, L, 1.0)
    wc, wl = rc.node_disp[tip][2], rl.node_disp[tip][2]
    assert wl != 0.0
    assert abs(wc - wl) / abs(wl) < 1e-9


def test_pdelta_path_bit_identical_to_legacy_bool():
    """geometric="pdelta" is the SAME solve as the legacy pdelta=True
    (bit-identical results — same OpenSees calls)."""
    mdl, I, L = _euler_column()
    Pcr = math.pi ** 2 * E_STEEL * I / (2 * L) ** 2
    g = mdl.pattern("G")
    g.nodal_loads.append(NodalLoad((0, 0, L), fz=-0.3 * Pcr))
    h = mdl.pattern("H")
    h.nodal_loads.append(NodalLoad((0, 0, L), fx=10.0))
    mdl.add_case("OLD", {"H": 1.0}, pdelta=True, pdelta_gravity={"G": 1.0})
    mdl.add_case("NEW", {"H": 1.0}, geometric="pdelta",
                 pdelta_gravity={"G": 1.0})
    mdl.validate()
    r_old = OpenSeesEngine(mdl).run_static("OLD")
    r_new = OpenSeesEngine(mdl).run_static("NEW")
    for t in r_old.node_disp:
        assert r_old.node_disp[t] == r_new.node_disp[t]
    for u in r_old.member_forces:
        assert r_old.member_forces[u] == r_new.member_forces[u]


def test_corotational_pdelta_ordering_under_gravity():
    """Under the same gravity + lateral load the drifts order physically:
    linear < pdelta ~ corotational, with corotational within a few % of
    P-Delta at moderate load (both capture the leading P-Delta effect)."""
    mdl, I, L = _euler_column()
    Pcr = math.pi ** 2 * E_STEEL * I / (2 * L) ** 2
    mdl.pattern("G").nodal_loads.append(
        NodalLoad((0, 0, L), fz=-0.3 * Pcr))
    mdl.pattern("H").nodal_loads.append(NodalLoad((0, 0, L), fx=5.0))
    mdl.add_case("LIN", {"H": 1.0})
    mdl.add_case("PD", {"H": 1.0}, geometric="pdelta",
                 pdelta_gravity={"G": 1.0})
    mdl.add_case("CR", {"H": 1.0}, geometric="corotational",
                 pdelta_gravity={"G": 1.0})
    mdl.validate()
    ux = {}
    for cname in ("LIN", "PD", "CR"):
        eng = OpenSeesEngine(mdl)
        r = eng.run_static(cname)
        tip = _tip_tag(eng, 0.0, L)
        ux[cname] = r.node_disp[tip][0]
    assert ux["PD"] > 1.2 * ux["LIN"]       # ~1/(1 - 0.3) amplification
    assert ux["CR"] > 1.2 * ux["LIN"]
    assert ux["CR"] == pytest.approx(ux["PD"], rel=0.05)


def test_geometric_field_precedence_and_roundtrip():
    """Documented precedence: non-"linear" geometric WINS; else the pdelta
    bool maps True -> "pdelta".  Both fields serialize and round-trip."""
    from skyframe.core.model import LoadCase
    assert LoadCase("A", {}).effective_geometric == "linear"
    assert LoadCase("A", {}, pdelta=True).effective_geometric == "pdelta"
    assert LoadCase("A", {}, pdelta=True,
                    geometric="corotational").effective_geometric \
        == "corotational"
    assert LoadCase("A", {}, pdelta=False,
                    geometric="pdelta").effective_geometric == "pdelta"

    mdl, _, L = _euler_column()
    mdl.pattern("P").nodal_loads.append(NodalLoad((0, 0, L), fx=1.0))
    mdl.add_case("C", {"P": 1.0}, geometric="corotational")
    mdl.add_pushover_case("PO", "X", geometric="corotational",
                          default_My=50.0, steps=5)
    d = mdl.to_dict()
    assert d["cases"]["C"]["geometric"] == "corotational"
    assert d["cases"]["C"]["pdelta"] is False       # legacy field kept
    assert d["pushover_cases"]["PO"]["geometric"] == "corotational"
    m2 = BuildingModel.from_dict(d)
    assert m2.cases["C"].effective_geometric == "corotational"
    assert m2.pushover_cases["PO"].geometric == "corotational"
    # pre-v0.25 file (no geometric key): pdelta bool decides
    del d["cases"]["C"]["geometric"]
    d["cases"]["C"]["pdelta"] = True
    m3 = BuildingModel.from_dict(d)
    assert m3.cases["C"].effective_geometric == "pdelta"
    with pytest.raises(ValueError, match="geometric"):
        mdl.add_case("BAD", {"P": 1.0}, geometric="exact")
    with pytest.raises(ValueError, match="geometric"):
        mdl.add_pushover_case("BADPO", "X", geometric="big")


def test_corotational_pushover_tracks_static():
    """A corotational pushover's capacity curve slope equals the elastic
    corotational static stiffness at small drift (no hinges yield), and
    the case runs through the standard pushover pipeline."""
    mdl, I, L = _euler_column()
    mdl.add_pushover_case("PO", "X", target_drift=0.001, steps=4,
                          default_My=1.0e9, geometric="corotational")
    mdl.validate()
    po = OpenSeesEngine(mdl).run_pushover("PO")
    assert len(po.roof_disp) == 4 and not po.warnings
    # exact elastic slope of a tip-loaded cantilever with the base hinge
    # spring k_theta = 10*6EI/L in series:
    #   delta = FL^3/3EI + (FL)L/k_theta -> k = EI/L^3 / (1/3 + 1/60)
    k = po.base_shear[-1] / po.roof_disp[-1]
    k_hand = E_STEEL * I / L ** 3 / (1.0 / 3.0 + 1.0 / 60.0)
    assert k == pytest.approx(k_hand, rel=0.005)


# =========================================================================== #
# 2. buckling from a stressed / staged state
# =========================================================================== #
def test_buckling_base_case_euler_pin():
    """Euler column with base P0 = 0.5*lambda0 under a unit reference
    load: the remaining multiplier is 0.5*lambda0 EXACTLY (Kg linear in
    N — the two-load-set identity lambda_base + lambda = lambda_no_base),
    and lambda0 itself sits at the analytic Pcr = pi^2 EI/(2L)^2."""
    mdl, I, L = _euler_column()
    Pcr = math.pi ** 2 * E_STEEL * I / (2 * L) ** 2
    mdl.pattern("UNIT").nodal_loads.append(NodalLoad((0, 0, L), fz=-1.0))
    mdl.add_buckling_case("BK0", {"UNIT": 1.0}, num_modes=2)
    mdl.validate()
    eng = OpenSeesEngine(mdl)
    lam0 = eng.run_buckling("BK0").factors[0]
    assert lam0 == pytest.approx(Pcr, rel=0.01)     # 1-element consistent Kg

    mdl.pattern("P0").nodal_loads.append(
        NodalLoad((0, 0, L), fz=-0.5 * lam0))
    mdl.add_case("BASE", {"P0": 1.0})
    mdl.add_buckling_case("BK", {"UNIT": 1.0}, num_modes=2,
                          base_case="BASE")
    mdl.validate()
    res = OpenSeesEngine(mdl).run_buckling("BK")
    assert res.base_case == "BASE"
    assert res.factors[0] == pytest.approx(0.5 * lam0, rel=1e-9)
    assert res.to_dict()["base_case"] == "BASE"


def test_buckling_base_beyond_capacity_warns():
    """A base state past the buckling load makes K + Kg(N_base)
    indefinite: no factors, an explicit warning names the cause."""
    mdl, I, L = _euler_column()
    mdl.pattern("UNIT").nodal_loads.append(NodalLoad((0, 0, L), fz=-1.0))
    mdl.add_buckling_case("BK0", {"UNIT": 1.0})
    mdl.validate()
    lam0 = OpenSeesEngine(mdl).run_buckling("BK0").factors[0]
    mdl.pattern("BIG").nodal_loads.append(
        NodalLoad((0, 0, L), fz=-1.2 * lam0))
    mdl.add_case("BASE", {"BIG": 1.0})
    mdl.add_buckling_case("BK", {"UNIT": 1.0}, base_case="BASE")
    res = OpenSeesEngine(mdl).run_buckling("BK")
    assert res.factors == []
    assert any("at or beyond the buckling" in w for w in res.warnings)


def test_buckling_base_none_unchanged_and_module_level():
    """base_N=None keeps the classic problem; the module-level base_N
    hook reproduces the engine result (independent path cross-check)."""
    mdl, I, L = _euler_column()
    Pcr = math.pi ** 2 * E_STEEL * I / (2 * L) ** 2
    mdl.pattern("UNIT").nodal_loads.append(NodalLoad((0, 0, L), fz=-1.0))
    mdl.pattern("P0").nodal_loads.append(
        NodalLoad((0, 0, L), fz=-0.25 * Pcr))
    mdl.add_case("BASE", {"P0": 1.0})
    mdl.add_buckling_case("BK", {"UNIT": 1.0}, base_case="BASE")
    mdl.validate()
    res_engine = OpenSeesEngine(mdl).run_buckling("BK")
    # independent: hand-fed compression (tension-negative) into the core
    res_core = buckling_analysis(mdl, {"UNIT": 1.0},
                                 base_N={"C1": -0.25 * Pcr},
                                 base_label="hand")
    assert res_engine.factors[0] == pytest.approx(res_core.factors[0],
                                                  rel=1e-12)
    res_none = buckling_analysis(mdl, {"UNIT": 1.0})
    assert res_none.factors[0] == pytest.approx(
        res_engine.factors[0] + 0.25 * Pcr, rel=1e-9)
    assert res_none.base_case is None


def test_buckling_base_case_validation():
    """base_case must name an existing STATIC case (model validation)."""
    mdl, I, L = _euler_column()
    mdl.pattern("UNIT").nodal_loads.append(NodalLoad((0, 0, L), fz=-1.0))
    with pytest.raises(ValueError, match="base_case"):
        mdl.add_buckling_case("BK", {"UNIT": 1.0}, base_case="NOPE")
    # round-trip
    mdl.add_case("BASE", {"UNIT": 1.0})
    mdl.add_buckling_case("BK", {"UNIT": 1.0}, base_case="BASE")
    m2 = BuildingModel.from_dict(mdl.to_dict())
    assert m2.buckling_cases["BK"].base_case == "BASE"


def test_buckling_base_case_from_staged_force_path():
    """The engine base path (run_static -> station axials) feeds the SAME
    N the nodal loads imply: on a 2-story column with per-story loads the
    two base formulations (engine case vs hand base_N) agree exactly.
    The existing staged tests pin staged == one-shot member forces, so a
    staged final state feeds identical N through this path."""
    mdl = BuildingModel(name="stack2")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("S", E=E_STEEL, nu=0.3))
    mdl.add_section(FrameSection.rectangular("C", "S", 0.2, 0.2))
    mdl.set_stories([3.0, 3.0])
    mdl.add_member("column", "C", (0, 0, 0), (0, 0, 3.0),
                   story="Story1", uid="C1")
    mdl.add_member("column", "C", (0, 0, 3.0), (0, 0, 6.0),
                   story="Story2", uid="C2")
    g = mdl.pattern("G")
    g.nodal_loads.append(NodalLoad((0, 0, 3.0), fz=-100.0))
    g.nodal_loads.append(NodalLoad((0, 0, 6.0), fz=-50.0))
    mdl.pattern("UNIT").nodal_loads.append(NodalLoad((0, 0, 6.0), fz=-1.0))
    mdl.add_case("BASE", {"G": 1.0})
    mdl.add_buckling_case("BK", {"UNIT": 1.0}, base_case="BASE")
    mdl.validate()
    res_engine = OpenSeesEngine(mdl).run_buckling("BK")
    # hand axial state: C1 carries 150 kN compression, C2 carries 50 kN
    res_hand = buckling_analysis(mdl, {"UNIT": 1.0},
                                 base_N={"C1": -150.0, "C2": -50.0})
    assert res_engine.factors[0] == pytest.approx(res_hand.factors[0],
                                                  rel=1e-12)


# =========================================================================== #
# 3. floor-cracking iterative stiffness
# =========================================================================== #
def test_cracked_below_cracking_bit_identical():
    """A slab loaded below cracking: one iteration, no cracked quads, and
    the returned CaseResults is BIT-IDENTICAL to the plain elastic run."""
    mdl = _strip_model(q=1.0)
    res = OpenSeesEngine(mdl).run_cracked("Q")
    elastic = OpenSeesEngine(mdl).run_static("Q")
    assert res.iterations == 1 and res.converged
    assert res.cracked_quads == []
    assert all(not e["cracked"] for e in res.cracking.values())
    for t, v in elastic.node_disp.items():
        assert res.case.node_disp[t] == v          # bitwise
    for qi, sf in elastic.shell_forces.items():
        assert res.case.shell_forces[qi] == sf


def test_cracked_fully_cracked_closed_form():
    """Far above cracking every quad cracks and the deflection converges
    to EXACTLY 1/cracked_ratio x elastic (uniform stiffness scale on a
    statically determinate strip — closed form)."""
    mdl = _strip_model(q=100.0)
    res = OpenSeesEngine(mdl).run_cracked("Q", max_iter=20)
    elastic = OpenSeesEngine(mdl).run_static("Q")
    n_quads = len(res.cracking)
    assert n_quads > 0 and len(res.cracked_quads) == n_quads
    assert res.converged
    w_cr = max(abs(v[2]) for v in res.case.node_disp.values())
    w_el = max(abs(v[2]) for v in elastic.node_disp.values())
    assert w_cr == pytest.approx(w_el / 0.35, rel=1e-9)
    # custom ratio honored
    res2 = OpenSeesEngine(mdl).run_cracked("Q", cracked_ratio=0.5,
                                           max_iter=20)
    w2 = max(abs(v[2]) for v in res2.case.node_disp.values())
    assert w2 == pytest.approx(w_el / 0.5, rel=1e-9)


def test_cracked_mcr_hand_value_and_map():
    """Mcr = fr*t^2/6 with fr = 0.62*sqrt(fc') MPa and fc' from the ACI
    E-inversion — hand value; the map's Ma matches the elastic |M| field
    of the below-cracking run."""
    mdl = _strip_model(q=1.0)
    res = OpenSeesEngine(mdl).run_cracked("Q")
    E = 25.0e6
    fc_mpa = (E / 1000.0 / 4700.0) ** 2
    fr_kpa = 0.62 * math.sqrt(fc_mpa) * 1000.0
    mcr_hand = fr_kpa * 0.2 ** 2 / 6.0
    elastic = OpenSeesEngine(mdl).run_static("Q")
    for qi, entry in res.cracking.items():
        assert entry["Mcr"] == pytest.approx(mcr_hand, rel=1e-12)
        sf = elastic.shell_forces[qi]
        assert entry["Ma"] == pytest.approx(max(abs(sf[3]), abs(sf[4])),
                                            rel=1e-12)
        assert entry["region"] == "S1"


def test_cracked_iteration_monotone_and_terminates():
    """A load near the cracking threshold cracks only the high-moment
    midspan band; the cracked set is monotone (supersets per iteration)
    and the run terminates converged within max_iter."""
    # Mmax = qL^2/8 = 4.5q; Mcr ~ 22.06 -> q = 6: midspan cracks (M=27),
    # supports don't
    mdl = _strip_model(q=6.0)
    res = OpenSeesEngine(mdl).run_cracked("Q", max_iter=15)
    n_cracked = len(res.cracked_quads)
    assert res.converged
    assert 0 < n_cracked < len(res.cracking)
    # every cracked quad exceeded Mcr in the final state's terms
    for qi in res.cracked_quads:
        assert res.cracking[qi]["Ma"] >= 0.0
    # dict shape (the API payload core)
    d = res.to_dict()
    assert set(["cracking", "iterations", "converged", "cracked_ratio",
                "fr_factor"]).issubset(d)
    assert d["cracking"][str(res.cracked_quads[0])]["cracked"] is True


def test_cracked_parameter_validation_and_no_slabs():
    """Bad parameters raise; a slab-free model returns the elastic result
    with an explicit warning."""
    mdl = _strip_model(q=1.0)
    with pytest.raises(ValueError, match="cracked_ratio"):
        cracked_analysis(mdl, "Q", cracked_ratio=0.0)
    with pytest.raises(ValueError, match="cracked_ratio"):
        cracked_analysis(mdl, "Q", cracked_ratio=1.5)
    with pytest.raises(ValueError, match="max_iter"):
        cracked_analysis(mdl, "Q", max_iter=0)
    with pytest.raises(ValueError, match="Unknown load case"):
        cracked_analysis(mdl, "NOPE")
    mdl2, _, L = _euler_column()
    mdl2.pattern("P").nodal_loads.append(NodalLoad((0, 0, L), fx=1.0))
    mdl2.add_case("P", {"P": 1.0})
    mdl2.validate()
    res = cracked_analysis(mdl2, "P")
    assert res.cracking == {} and res.converged
    assert any("no slab shell quads" in w for w in res.warnings)


def test_cracked_api_endpoint():
    """POST /api/analyze/cracked returns the case dict + cracking payload;
    400 on unknown case / bad ratio."""
    from skyframe.api.server import create_app
    app = create_app()
    client = app.test_client()
    mdl = _strip_model(q=100.0)
    rv = client.post("/api/model", json=mdl.to_dict())
    assert rv.status_code == 200
    rv = client.post("/api/analyze/cracked", json={"case": "Q"})
    assert rv.status_code == 200
    d = rv.get_json()
    assert d["method"] == "cracked" and d["case"] == "Q"
    assert d["converged"] and d["cracking"]
    assert all(e["cracked"] for e in d["cracking"].values())
    assert rv.status_code == 200
    assert client.post("/api/analyze/cracked",
                       json={"case": "NOPE"}).status_code == 400
    assert client.post("/api/analyze/cracked",
                       json={"case": "Q",
                             "cracked_ratio": 2.0}).status_code == 400
    assert client.post("/api/analyze/cracked", json={}).status_code == 400


# =========================================================================== #
# 4. time-dependent staged construction (AAEM)
# =========================================================================== #
def test_aci209_curves_hand_values():
    """The module-level ACI 209 curves at hand-computed points."""
    assert aci209_creep(2.0, math.inf) == 2.0
    assert aci209_creep(2.0, 0.0) == 0.0
    assert aci209_creep(2.0, 10.0) == pytest.approx(
        2.0 * (10.0 / 20.0) ** 0.6, rel=1e-12)
    assert aci209_shrinkage(300e-6, math.inf) == 300e-6
    assert aci209_shrinkage(300e-6, 35.0) == pytest.approx(150e-6,
                                                           rel=1e-12)
    assert aci209_modulus_growth(math.inf) == pytest.approx(
        math.sqrt(1.0 / 0.85), rel=1e-12)
    assert aci209_modulus_growth(28.0) == pytest.approx(
        math.sqrt(28.0 / (4.0 + 0.85 * 28.0)), rel=1e-12)
    assert TD_CHI == 0.8


def test_aaem_closed_form_long_term():
    """Single loaded column at t=inf: delta_total/delta_elastic =
    1 + chi*phi_inf EXACTLY (the AAEM closed form)."""
    td = {"days_per_story": 7.0, "creep_coeff": 2.0, "shrinkage": 0.0}
    mdl = _stacked_column([3.0], 100.0, td=td)
    r = OpenSeesEngine(mdl).run_staged("STG")
    sh = r.shortening["Story1"]
    assert sh["uz_elastic"] < 0.0
    assert sh["uz_time_dependent"] / sh["uz_elastic"] == pytest.approx(
        1.0 + 0.8 * 2.0, rel=1e-12)
    # hand elastic check: P*L/EA
    A = 0.4 * 0.4
    assert sh["uz_elastic"] == pytest.approx(-100.0 * 3.0 / (E_CONC * A),
                                             rel=1e-9)


def test_aaem_finite_t_eval_on_creep_curve():
    """Finite t_eval: the ratio follows 1 + chi*phi(t_eval - T_load) with
    T_load = days_per_story (stage-0 loads arrive one cycle after cast)."""
    td = {"days_per_story": 7.0, "creep_coeff": 2.0, "shrinkage": 0.0,
          "t_eval": 100.0}
    mdl = _stacked_column([3.0], 100.0, td=td)
    r = OpenSeesEngine(mdl).run_staged("STG")
    sh = r.shortening["Story1"]
    dur = 100.0 - 7.0
    phi_t = 2.0 * (dur / (10.0 + dur)) ** 0.6
    assert sh["uz_time_dependent"] / sh["uz_elastic"] == pytest.approx(
        1.0 + 0.8 * phi_t, rel=1e-12)


def test_shrinkage_thermal_equivalence_exact():
    """An UNLOADED column's shrinkage shortening equals eps_sh(t)*L
    exactly (statically determinate -> strain independent of E; the
    thermal-load machinery delivers alpha*dT*L = -eps_sh*L exactly)."""
    td = {"days_per_story": 7.0, "creep_coeff": 2.0, "shrinkage": 300e-6}
    mdl = _stacked_column([3.0], 0.0, td=td)
    r = OpenSeesEngine(mdl).run_staged("STG")
    sh = r.shortening["Story1"]
    assert sh["uz_elastic"] == 0.0
    assert sh["uz_time_dependent"] == pytest.approx(-300e-6 * 3.0,
                                                    rel=1e-9)
    # finite time: the ACI t/(35+t) curve at the story's concrete age
    td2 = dict(td, t_eval=35.0)      # story cast at t=0 -> age 35 d
    mdl2 = _stacked_column([3.0], 0.0, td=td2)
    r2 = OpenSeesEngine(mdl2).run_staged("STG")
    assert r2.shortening["Story1"]["uz_time_dependent"] == pytest.approx(
        -300e-6 * 0.5 * 3.0, rel=1e-9)


def test_two_story_creep_hand_sum():
    """Two-story column, long-term creep, no shrinkage, no aging: every
    stage's increment scales by the SAME 1 + chi*phi_inf (duration inf),
    so the total top shortening is (1 + chi*phi_inf) x elastic — the
    longhand stage-by-stage sum."""
    td = {"days_per_story": 10.0, "creep_coeff": 2.5, "shrinkage": 0.0}
    mdl = _stacked_column([3.0, 3.0], 50.0, td=td)
    r = OpenSeesEngine(mdl).run_staged("STG")
    f = 1.0 + 0.8 * 2.5
    A = 0.16
    # hand elastic staged accumulation (rebuild-and-accumulate: a node
    # only collects increments from stages in which it EXISTS):
    #   story1 top: stage0 gives 50*3/EA, stage1 adds 50*3/EA (C1 carries
    #     the z=6 load) -> 100*3/EA total;
    #   story2 top: exists from stage1 only -> C1 + C2 shares of the z=6
    #     load = 50*3/EA + 50*3/EA (story2 is cast to design elevation
    #     AFTER the stage-0 settlement — the staged-construction point).
    d1_el = -100.0 * 3.0 / (E_CONC * A)
    d2_el = -(50.0 * 3.0 + 50.0 * 3.0) / (E_CONC * A)
    sh = r.shortening
    assert sh["Story1"]["uz_elastic"] == pytest.approx(d1_el, rel=1e-9)
    assert sh["Story2"]["uz_elastic"] == pytest.approx(d2_el, rel=1e-9)
    assert sh["Story1"]["uz_time_dependent"] == pytest.approx(f * d1_el,
                                                              rel=1e-12)
    assert sh["Story2"]["uz_time_dependent"] == pytest.approx(f * d2_el,
                                                              rel=1e-12)
    assert sh["Story2"]["delta"] == pytest.approx((f - 1.0) * d2_el,
                                                  rel=1e-9)
    # serialized shape
    d = r.to_dict()
    assert d["shortening"]["Story2"]["delta"] == pytest.approx(
        (f - 1.0) * d2_el, rel=1e-9)


def test_aging_reduces_creep_deflection_of_older_stories():
    """aging=True scales each stage's loading-age modulus UP the ACI 209
    growth curve: story 1's concrete is 2 cycles old when stage 2 loads
    arrive, so its stage-2 increment is SMALLER than with aging=False
    (E(t)>E28 requires t>27.8 d; use a long cycle), and the hand factor
    reproduces the engine number exactly."""
    base = {"days_per_story": 60.0, "creep_coeff": 0.0, "shrinkage": 0.0}
    mdl_off = _stacked_column([3.0, 3.0], 50.0, td=dict(base))
    mdl_on = _stacked_column([3.0, 3.0], 50.0, td=dict(base, aging=True))
    r_off = OpenSeesEngine(mdl_off).run_staged("STG")
    r_on = OpenSeesEngine(mdl_on).run_staged("STG")
    uz_off = r_off.shortening["Story2"]["uz_time_dependent"]
    uz_on = r_on.shortening["Story2"]["uz_time_dependent"]
    assert abs(uz_on) < abs(uz_off)        # older/stiffer concrete
    # exact hand check: phi = 0 -> each stage k scales by
    # 1/E_growth((k+1-j)*d) per story-j member.  Story2's top exists
    # from stage 1 only (see the accumulation note in the previous test):
    #   uz(story2 top) = stage-1 C1 share (story1 concrete, age 120 d)
    #                  + stage-1 C2 share (story2 concrete, age 60 d)
    EA = E_CONC * 0.16
    g = aci209_modulus_growth
    d2_c1 = -50.0 * 3.0 / (EA * g(120.0))                 # stage 1, C1
    d2_c2 = -50.0 * 3.0 / (EA * g(60.0))                  # stage 1, C2
    assert uz_on == pytest.approx(d2_c1 + d2_c2, rel=1e-12)


def test_time_dependent_none_bit_identical_elastic():
    """time_dependent=None keeps the elastic staged path bit-identical
    (same accumulation loop, no material adjustment)."""
    mdl_a = _stacked_column([3.0, 3.0], 50.0, td=None)
    td = {"days_per_story": 7.0, "creep_coeff": 0.0, "shrinkage": 0.0}
    mdl_b = _stacked_column([3.0, 3.0], 50.0, td=td)
    r_a = OpenSeesEngine(mdl_a).run_staged("STG")
    r_b = OpenSeesEngine(mdl_b).run_staged("STG")
    assert r_a.shortening is None
    # phi = 0, eps = 0, aging off -> the td pass IS elastic
    for t, v in r_a.case.node_disp.items():
        assert r_b.case.node_disp[t] == pytest.approx(v, abs=1e-15)
    sh = r_b.shortening["Story2"]
    assert sh["delta"] == pytest.approx(0.0, abs=1e-15)


def test_time_dependent_validation_and_roundtrip():
    """time_dependent validation (required key, unknown keys, bad values,
    unknown materials) and to_dict/from_dict round-trip."""
    mdl = _stacked_column([3.0], 10.0,
                          td={"days_per_story": 7.0, "aging": True,
                              "materials": ["C30"]})
    d = mdl.to_dict()
    m2 = BuildingModel.from_dict(d)
    td2 = m2.staged_cases["STG"].time_dependent
    assert td2["days_per_story"] == 7.0 and td2["aging"] is True
    assert td2["materials"] == ["C30"]
    with pytest.raises(ValueError, match="days_per_story"):
        _stacked_column([3.0], 10.0, td={"creep_coeff": 2.0})
    with pytest.raises(ValueError, match="unknown time_dependent key"):
        _stacked_column([3.0], 10.0, td={"days_per_story": 7.0,
                                         "phi": 2.0})
    with pytest.raises(ValueError, match="aging"):
        _stacked_column([3.0], 10.0, td={"days_per_story": 7.0,
                                         "aging": 1})
    with pytest.raises(ValueError, match="unknown material"):
        _stacked_column([3.0], 10.0, td={"days_per_story": 7.0,
                                         "materials": ["STEEL"]})
    with pytest.raises(ValueError, match="days_per_story"):
        _stacked_column([3.0], 10.0, td={"days_per_story": -1.0})


def test_materials_filter_excludes_steel():
    """A steel story (material excluded via the materials list) takes NO
    creep: only the concrete story's increment scales."""
    mdl = BuildingModel(name="mix")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("C30", E=E_CONC, nu=0.2))
    mdl.add_material(Material("S355", E=E_STEEL, nu=0.3))
    mdl.add_section(FrameSection.rectangular("COL_C", "C30", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("COL_S", "S355", 0.4, 0.4))
    mdl.set_stories([3.0, 3.0])
    mdl.add_member("column", "COL_C", (0, 0, 0), (0, 0, 3.0),
                   story="Story1", uid="C1")
    mdl.add_member("column", "COL_S", (0, 0, 3.0), (0, 0, 6.0),
                   story="Story2", uid="C2")
    pat = mdl.pattern("DEAD")
    pat.nodal_loads.append(NodalLoad((0, 0, 6.0), fz=-100.0))
    mdl.add_staged_case("STG", pattern="DEAD",
                        time_dependent={"days_per_story": 7.0,
                                        "creep_coeff": 2.0,
                                        "shrinkage": 0.0,
                                        "materials": ["C30"]})
    mdl.validate()
    r = OpenSeesEngine(mdl).run_staged("STG")
    f = 1.0 + 0.8 * 2.0
    el1 = r.shortening["Story1"]["uz_elastic"]
    td1 = r.shortening["Story1"]["uz_time_dependent"]
    assert td1 == pytest.approx(f * el1, rel=1e-12)   # concrete creeps
    # steel story's OWN shortening share is unchanged: top total =
    # f*concrete share + 1.0*steel share
    el2 = r.shortening["Story2"]["uz_elastic"]
    td2 = r.shortening["Story2"]["uz_time_dependent"]
    steel_share = el2 - el1
    assert td2 == pytest.approx(f * el1 + steel_share, rel=1e-9)


def test_staged_include_live_stays_elastic_and_oneshot_unchanged():
    """The include_live increment is applied at E28 (no creep): with
    phi_inf > 0 the live share of the top displacement equals the elastic
    live share exactly; the one-shot comparison stays elastic."""
    mdl = _stacked_column([3.0], 100.0,
                          td={"days_per_story": 7.0, "creep_coeff": 2.0,
                              "shrinkage": 0.0})
    live = mdl.pattern("LIVE")
    live.nodal_loads.append(NodalLoad((0, 0, 3.0), fz=-40.0))
    mdl.staged_cases["STG"].include_live = {"LIVE": 1.0}
    mdl.validate()
    r = OpenSeesEngine(mdl).run_staged("STG")
    A = 0.16
    d_dead_el = -100.0 * 3.0 / (E_CONC * A)
    d_live_el = -40.0 * 3.0 / (E_CONC * A)
    f = 1.0 + 0.8 * 2.0
    sh = r.shortening["Story1"]
    assert sh["uz_time_dependent"] == pytest.approx(
        f * d_dead_el + d_live_el, rel=1e-9)
    # one-shot comparison stays elastic: full loads react at the base
    assert r.oneshot.base["FZ"] == pytest.approx(140.0, rel=1e-9)
