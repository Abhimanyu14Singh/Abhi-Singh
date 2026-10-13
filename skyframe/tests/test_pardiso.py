"""MKL PARDISO linear solver path (Wave E2).

CONTRACT "PARDISO linear solver (MKL)"; docs/PERFORMANCE.md "PARDISO".
Closed-form pins (cantilever, fixed-base portal, uniaxial shell plate) and
equality with the OpenSees path (static, modal condensed / eigsh, RS, CR,
Ritz, FEM buckling) to 1e-9 relative (core/results_compare)."""

import json
import math

import numpy as np
import pytest

from skyframe.core import analysis_options as aopt
from skyframe.core.builder import quick_building
from skyframe.core.mesh import mesh_model
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 NodalLoad, PointSupport, ShellSection)
from skyframe.core.results_compare import compare
from skyframe.engine import pardiso_solver as pds
from skyframe.engine import parallel as par
from skyframe.engine import solver_policy as slv
from skyframe.engine.opensees_engine import OpenSeesEngine

pytestmark = pytest.mark.skipif(not pds.available(),
                                reason="pypardiso / MKL not installed")

E = 2.0e8
IGN = ("tors_ratio_x", "tors_ratio_y", "flag", "warning", "model_size",
       "timing")


def _pardiso(m):
    m.analysis_options = dict(m.analysis_options or {}, solver="pardiso")
    return m


def _floor(d, eps=1e-15):
    """zero numbers of pure round-off size (both solvers' noise)."""
    if isinstance(d, dict):
        return {k: _floor(v, eps) for k, v in d.items()}
    if isinstance(d, list):
        return [_floor(v, eps) for v in d]
    if isinstance(d, float) and abs(d) < eps:
        return 0.0
    return d


def _run(m):
    eng = OpenSeesEngine(m)
    d = json.loads(json.dumps(eng.run().to_dict(), default=str))
    return eng, d


def _same(make, rtol=1e-9, opts=None):
    ref = make()
    new = _pardiso(make())
    if opts:
        ref.analysis_options = dict(ref.analysis_options or {}, **opts)
        new.analysis_options = dict(new.analysis_options, **opts)
    _, a = _run(ref)
    eng, b = _run(new)
    sol = b.pop("extra")["solver"]
    assert sol["used"] and sol["reason"] == "", sol
    ok, bad, worst = compare(_floor(a), _floor(b), rtol=rtol,
                             ignore_keys=IGN)
    assert ok, bad[:5]
    return eng, sol, worst


# ------------------------------------------------------------ closed form
def _cantilever(L=3.0, n=4):
    m = BuildingModel(name="cant")
    m.rigid_diaphragms = False
    m.add_material(Material("S", E=E, nu=0.3, unit_weight=0.0))
    m.add_section(FrameSection.rectangular("C", "S", 0.2, 0.4))
    m.set_stories([L])
    for k in range(n):
        m.add_member("column", "C", (0, 0, L * k / n),
                     (0, 0, L * (k + 1) / n), story="Story1", uid=f"C{k}")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.pattern("P", "other").nodal_loads.append(
        NodalLoad((0, 0, L), fx=10.0, fy=-4.0, fz=-50.0))
    m.add_case("P", {"P": 1.0})
    return m


def test_cantilever_tip_closed_form():
    L, b, h = 3.0, 0.2, 0.4
    m = _pardiso(_cantilever(L))
    eng = OpenSeesEngine(m)
    r = eng.run_static("P")
    assert pds.system(eng._asm) is not None
    tip = max(r.node_disp, key=lambda t: eng._asm.node_coords[t][2])
    u = r.node_disp[tip]
    Iy_bend_x = h * b ** 3 / 12.0           # load along x bends about ...
    Ix = b * h ** 3 / 12.0
    # the rectangular section 0.2 x 0.4: depth 0.4 along the local axis
    # that bends under one of the two lateral loads; pin both directions
    I_small, I_big = min(Iy_bend_x, Ix), max(Iy_bend_x, Ix)
    ux = 10.0 * L ** 3 / (3 * E * np.array([I_small, I_big]))
    uy = 4.0 * L ** 3 / (3 * E * np.array([I_small, I_big]))
    assert (abs(u[0]) == pytest.approx(ux[0], rel=1e-9)
            or abs(u[0]) == pytest.approx(ux[1], rel=1e-9))
    assert (abs(u[1]) == pytest.approx(uy[0], rel=1e-9)
            or abs(u[1]) == pytest.approx(uy[1], rel=1e-9))
    assert u[2] == pytest.approx(-50.0 * L / (E * b * h), rel=1e-9)
    # reactions balance the load exactly
    base = min(r.reactions, key=lambda t: eng._asm.node_coords[t][2])
    assert r.reactions[base][0] == pytest.approx(-10.0, rel=1e-9)
    assert r.reactions[base][4] == pytest.approx(-10.0 * L, rel=1e-9)


def test_portal_lateral_stiffness_closed_form():
    """Fixed-base portal, no diaphragm: k = 24 E Ic/h^3 (1+6 rho)/(4+6 rho),
    rho = (Ib/L)/(Ic/h); axial deformation suppressed with a huge area."""
    h, L = 3.0, 6.0
    m = BuildingModel(name="portal")
    m.rigid_diaphragms = False
    m.diaphragm = "none"
    m.add_material(Material("S", E=E, nu=0.3, unit_weight=0.0))
    Ic, Ib = 2.0e-4, 5.0e-4
    m.add_section(FrameSection("COL", "S", 1.0e3, Ic, Ic, 1.0e-2))
    m.add_section(FrameSection("BM", "S", 1.0e3, Ib, Ib, 1.0e-2))
    m.set_stories([h])
    m.add_member("column", "COL", (0, 0, 0), (0, 0, h), story="Story1",
                 uid="C1")
    m.add_member("column", "COL", (L, 0, 0), (L, 0, h), story="Story1",
                 uid="C2")
    m.add_member("beam", "BM", (0, 0, h), (L, 0, h), story="Story1",
                 uid="B1")
    for x in (0.0, L):
        m.supports.append(PointSupport((x, 0, 0), (1, 1, 1, 1, 1, 1)))
    P = 12.0
    m.pattern("H", "other").nodal_loads.append(NodalLoad((0, 0, h), fx=P))
    m.add_case("H", {"H": 1.0})
    eng = OpenSeesEngine(_pardiso(m))
    r = eng.run_static("H")
    assert pds.system(eng._asm) is not None
    rho = (Ib / L) / (Ic / h)
    k = 24 * E * Ic / h ** 3 * (1 + 6 * rho) / (4 + 6 * rho)
    top = [t for t, c in eng._asm.node_coords.items() if c[2] == h]
    ux = np.mean([r.node_disp[t][0] for t in top])
    assert ux == pytest.approx(P / k, rel=1e-6)   # (axial area 1e3 m^2)


def test_shell_plate_uniaxial_closed_form():
    """Square shell wall, uniform top edge load q, uniaxial support set:
    constant stress -> u_top = q a / (E t) exactly (any mesh)."""
    a, t, n = 1.0, 0.01, 6
    m = BuildingModel(name="plate")
    m.rigid_diaphragms = False
    m.add_material(Material("STL", E=E, nu=0.3, unit_weight=0.0))
    m.add_shell_section(ShellSection("PL", "STL", t))
    m.set_stories([a])
    m.add_shell("wall", "shell", "PL", [(0, 0, 0), (a, 0, 0), (a, 0, a),
                                        (0, 0, a)],
                mesh_size=a / n * 1.0001, story="Story1", uid="W1")
    h = a / n
    pat = m.pattern("P", "other")
    for p in mesh_model(m).points:
        x, _y, z = p
        r = [0, 1, 0, 0, 0, 0]
        if abs(z) < 1e-9:
            r[2] = 1
            if abs(x) < 1e-9:
                r[0] = 1
        m.supports.append(PointSupport(tuple(p), tuple(r)))
        if abs(z - a) < 1e-9:
            w = h / 2 if (abs(x) < 1e-9 or abs(x - a) < 1e-9) else h
            pat.nodal_loads.append(NodalLoad(tuple(p), fz=-w))
    m.add_case("P", {"P": 1.0})
    eng = OpenSeesEngine(_pardiso(m))
    r = eng.run_static("P")
    assert pds.system(eng._asm) is not None
    tops = [tg for tg, c in eng._asm.node_coords.items()
            if abs(c[2] - a) < 1e-9]
    for tg in tops:
        assert r.node_disp[tg][2] == pytest.approx(-a / (E * t), rel=1e-9)


# --------------------------------------------- equality with OpenSees path
def _qb_full():
    m = quick_building(name="q", bays_x=2, bays_y=2, stories=3)
    sp = [[0.0, 0.4], [0.5, 1.0], [2.0, 0.3]]
    m.add_rs_case("RSX", "X", spectrum=sp)
    m.add_rs_case("RSY", "Y", spectrum=sp)
    return m


def test_building_equals_opensees_static_modal_rs_cr():
    eng, sol, worst = _same(_qb_full)
    assert sol["domain_solves"] >= 4 and sol["rhs"] > sol["solves"] - 1
    assert worst < 1e-9


def test_meshed_condensed_and_ritz_equal_opensees():
    def make():
        m = quick_building(name="qm", bays_x=1, bays_y=1, stories=2)
        m.add_shell_section(ShellSection("SLAB", "CONC", 0.2))
        for st in m.stories:
            z = st.elevation
            m.add_shell("slab", "shell", "SLAB",
                        [(0, 0, z), (6, 0, z), (6, 6, z), (0, 6, z)],
                        mesh_size=2.0, story=st.name)
        m.add_rs_case("RSX", "X", spectrum=[[0.0, 0.4], [2.0, 0.3]])
        m.modal_cases = {"RZ": {"type": "ritz", "num_modes": 3}}
        return m
    eng, sol, worst = _same(make)
    assert worst < 1e-9


def test_eigsh_path_equals_lanczos():
    """No-diaphragm meshed model with > MAX_MASSED massed dofs: scipy
    eigsh on the PARDISO factor == the OpenSees-MUMPS Lanczos path."""
    from skyframe.engine import modal_condensed as mcd

    def make():
        m = quick_building(name="nd", bays_x=3, bays_y=3, stories=3)
        m.rigid_diaphragms = False
        m.diaphragm = "none"
        m.add_shell_section(ShellSection("SLAB", "CONC", 0.2))
        for st in m.stories:
            z = st.elevation
            for i in range(3):
                for j in range(3):
                    m.add_shell("slab", "shell", "SLAB",
                                [(6 * i, 6 * j, z), (6 * i + 6, 6 * j, z),
                                 (6 * i + 6, 6 * j + 6, z),
                                 (6 * i, 6 * j + 6, z)],
                                mesh_size=1.5, story=st.name)
        m.num_modes = 6
        m.analysis_options = {"solver": "umfpack", "eigen_solver": "lanczos"}
        return m
    probe = OpenSeesEngine(make())
    assert len(mcd.massed_dofs(probe._build())) > mcd.MAX_MASSED
    eng, sol, worst = _same(make)
    assert eng._spm_info["solver"] == "eigsh-pardiso"
    assert sol["timing"].get("eigsh", 0.0) > 0.0


def test_fem_buckling_equals_opensees():
    def make():
        m = quick_building(name="b", bays_x=2, bays_y=1, stories=2)
        m.add_buckling_case("BE", {"DEAD": 1.0}, num_modes=2,
                            formulation="fem")
        return m
    ref = OpenSeesEngine(make()).run_buckling("BE")
    eng = OpenSeesEngine(_pardiso(make()))
    new = eng.run_buckling("BE")
    assert pds.system(eng._asm) is not None
    assert new.factors == pytest.approx(ref.factors, rel=1e-9)


# ------------------------------------------------------------- fallbacks
def test_fallback_nonlinear_springs_reported():
    m = _cantilever()
    eng = OpenSeesEngine(_pardiso(m))
    eng._compression_only_springs_present = lambda: True     # as if present
    d = eng.run().to_dict()
    sol = d["extra"]["solver"]
    assert not sol["used"] and "nonlinear" in sol["reason"]
    assert sol["engine"] == "opensees"


def test_fallback_unsupported_material_and_runtime_guard(monkeypatch):
    # an element type outside the allow-list is reported, never solved
    with pytest.raises(pds.Unsupported):
        pds._element_template(("forceBeamColumn", 1, 1, 2, 1, 1), {})
    with pytest.raises(pds.Unsupported):
        pds._linear_defs([("uniaxialMaterial", ("Steel01", 1, 1, 1, 0.1),
                           {})])
    # the runtime residual guard: a wrong stiffness falls back to OpenSees
    m = _cantilever()
    eng = OpenSeesEngine(_pardiso(m))
    asm = eng._elastic_domain()
    S = pds.system(asm)
    S.Kr = S.Kr * 1.5
    r = eng.run_static("P")
    assert pds.system(asm) is None
    assert "residual" in eng._pds_state["reason"]
    ref = OpenSeesEngine(_cantilever()).run_static("P")
    for t, v in ref.node_disp.items():
        assert r.node_disp[t] == pytest.approx(v, rel=1e-12, abs=1e-18)


def test_unavailable_auto_is_todays_path(monkeypatch):
    m = quick_building(stories=2, bays_x=1, bays_y=1)
    monkeypatch.setitem(pds._LIB, "tried", True)
    monkeypatch.setitem(pds._LIB, "mod", None)
    monkeypatch.setitem(pds._LIB, "err", "not installed")
    monkeypatch.setattr(pds, "PARDISO_EQ_THRESHOLD", 1)
    d = OpenSeesEngine(m).run().to_dict()
    assert "extra" not in d
    m2 = _pardiso(quick_building(stories=2, bays_x=1, bays_y=1))
    d2 = OpenSeesEngine(m2).run().to_dict()
    assert d2["extra"]["solver"]["reason"].startswith("pypardiso unavail")
    assert d2["extra"]["solver"]["used"] is False


# ---------------------------------------------------- policy / options
def test_auto_threshold_and_default_byte_identity(monkeypatch):
    small = quick_building(stories=2, bays_x=1, bays_y=1)
    d = OpenSeesEngine(small).run().to_dict()
    assert "extra" not in d                      # below threshold: legacy
    monkeypatch.setattr(pds, "PARDISO_EQ_THRESHOLD", 10)
    eng = OpenSeesEngine(quick_building(stories=2, bays_x=1, bays_y=1))
    d2 = eng.run().to_dict()
    assert d2["extra"]["solver"]["used"] is True
    assert d2["extra"]["solver"]["requested"] == "auto"


def test_parallel_fork_lanes_with_pardiso_equal_serial():
    """Fork-parallel families keep running with PARDISO (the forked
    workers inherit the factor); results equal the serial run."""
    def make(parallel):
        m = _pardiso(quick_building(stories=3, bays_x=2, bays_y=2))
        m.analysis_options["parallel"] = parallel
        return m
    eng = OpenSeesEngine(make(2))
    asm = eng._elastic_domain()
    assert slv.pardiso_active(asm) and slv.factor_once_ok(asm)
    if not par.fork_supported():
        pytest.skip("no fork on this platform")
    assert par.lanes(eng.model, asm) == 2
    _, a = _run(make("off"))
    e2, b = _run(make(2))
    assert b["extra"]["solver"]["used"]
    a.pop("extra"); b.pop("extra")
    ok, bad, _ = compare(_floor(a), _floor(b), rtol=1e-12, ignore_keys=IGN)
    assert ok, bad[:3]


def test_options_validation_and_serialization():
    m = quick_building(stories=1, bays_x=1, bays_y=1)
    m.analysis_options = {"solver": "pardiso", "pardiso_threads": 4}
    aopt.validate_analysis_options(m)
    for bad in (0, 300, "x", True, 2.5):
        m.analysis_options = {"pardiso_threads": bad}
        with pytest.raises(ValueError):
            aopt.validate_analysis_options(m)
    m.analysis_options = {"pardiso_threads": "auto"}
    assert aopt.analysis_options_to_dict(m) == {}     # default: not stored
    m.analysis_options = {"solver": "pardiso", "pardiso_threads": 2}
    d = json.loads(json.dumps(m.to_dict()))
    m2 = BuildingModel.from_dict(d)
    assert m2.analysis_options == {"solver": "pardiso", "pardiso_threads": 2}
    assert pds.resolve_threads(m2) == 2


def test_transformation_matrices_closed_form():
    """Rigid diaphragm / rigidLink beam C matrices (OpenSees formulas)."""
    coords = {1: (0.0, 0.0, 3.0), 2: (2.0, 1.0, 3.0), 3: (1.0, -2.0, 5.0)}
    tags = [1, 2, 3]
    idx = {t: i for i, t in enumerate(tags)}
    T, red, _ = pds.PardisoSystem._transformation(
        tags, idx, coords, {}, [("rigidDiaphragm", (3, 1, 2)),
                                ("rigidLink", ("beam", 1, 3))])
    T = T.toarray()
    xr = np.zeros(T.shape[1])
    rp = {int(p): i for i, p in enumerate(red)}
    xr[rp[5]] = 0.01                        # master rz
    xr[rp[3]] = 0.02                        # master rx
    u = T @ xr
    # diaphragm slave: ux = -dy rz, uy = dx rz
    assert u[6 + 0] == pytest.approx(-1.0 * 0.01)
    assert u[6 + 1] == pytest.approx(2.0 * 0.01)
    # rigid link: u_c = theta x d, d = (1, -2, 2)
    th = np.array([0.02, 0.0, 0.01])
    assert u[12:15] == pytest.approx(np.cross(th, [1.0, -2.0, 2.0]))


def test_pardiso_factor_block_solve_and_threads():
    import scipy.sparse as sp
    rng = np.random.default_rng(1)
    A = rng.standard_normal((30, 30))
    A = sp.csr_matrix(A @ A.T + 30 * np.eye(30))
    f = pds.PardisoFactor(A, 1)
    B = rng.standard_normal((30, 7))
    X = f.solve(B)
    assert np.abs(A @ X - B).max() < 1e-12
    assert f.mtype == 2 and f.rhs == 7
    assert pds.physical_cores() >= 1


def test_dbedit_table_and_run_log():
    from skyframe.core import modeltables as MT
    from skyframe.core import runlog
    m = quick_building(stories=1, bays_x=1, bays_y=1)
    d = m.to_dict()
    rows = json.loads(json.dumps(MT.get_table(d, "analysis_options")["rows"]))
    assert rows[0]["pardiso_threads"] == "auto"
    rows[0].update(solver="pardiso", pardiso_threads="3")
    m2, errs, _ = MT.apply_tables(d, {"analysis_options": rows})
    assert errs == []
    assert m2.analysis_options == {"solver": "pardiso", "pardiso_threads": 3}
    rows[0].update(solver="auto", pardiso_threads="auto")
    m3, errs, _ = MT.apply_tables(d, {"analysis_options": rows})
    assert errs == [] and "analysis_options" not in m3.to_dict()
    res = OpenSeesEngine(m2).run().to_dict()
    log = runlog.build_run_log(m2, res, {}, 1.0)
    assert log["solver"]["engine"] == "pardiso"
    assert log["solver"]["threads"] == 3
    log0 = runlog.build_run_log(m, OpenSeesEngine(m).run().to_dict(), {}, 1.0)
    assert "solver" not in log0


def test_selftest_and_threads_info():
    rep = pds.selftest()
    assert rep["ok"] and rep["used"], rep
    info = slv.threads_info()
    assert info["pardiso"] is True
