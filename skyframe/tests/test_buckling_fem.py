"""Buckling with shells and links, releases, sparse eigen and BucklingFEM
(CONTRACT "Buckling with shells and links, sparse eigen and BucklingFEM").

Every expected value is closed form."""

import copy
import json
import math

import numpy as np
import pytest

from skyframe.core.builder import quick_building
from skyframe.core.mesh import mesh_model
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 NodalLoad, PointSupport, ShellSection)
from skyframe.core.member_buckling import (forces_from_case,
                                           member_buckling, midline_props,
                                           plates)
from skyframe.engine import geom_stiffness as gk
from skyframe.engine import lanczos as lz
from skyframe.engine import solver_policy as slv
from skyframe.engine.opensees_engine import OpenSeesEngine

E = 2.0e8


# ------------------------------------------------------------ helpers
def plate_model(n=12, a=1.0, t=0.01, nu=0.3):
    m = BuildingModel(name="plate")
    m.rigid_diaphragms = False
    m.add_material(Material("STL", E=E, nu=nu, unit_weight=0.0))
    m.add_shell_section(ShellSection("PL", "STL", t))
    m.set_stories([a])
    m.add_shell("wall", "shell", "PL", [(0, 0, 0), (a, 0, 0), (a, 0, a),
                                        (0, 0, a)],
                mesh_size=a / n * 1.0001, story="Story1", uid="W1")
    h = a / n
    pat = m.pattern("P", "other")
    for p in mesh_model(m).points:
        x, _y, z = p
        if not (abs(x) < 1e-9 or abs(x - a) < 1e-9 or abs(z) < 1e-9
                or abs(z - a) < 1e-9):
            continue
        r = [0, 1, 0, 0, 0, 0]
        if abs(z) < 1e-9:
            r[2] = 1
            if abs(x) < 1e-9:
                r[0] = 1
        m.supports.append(PointSupport(tuple(p), tuple(r)))
        if abs(z - a) < 1e-9:
            w = h / 2 if (abs(x) < 1e-9 or abs(x - a) < 1e-9) else h
            pat.nodal_loads.append(NodalLoad(tuple(p), fz=-w))
    m.add_buckling_case("B", {"P": 1.0}, num_modes=2, formulation="fem")
    return m


def column_model(n=8, L=4.0, releases=True, form="fem"):
    m = BuildingModel(name="col")
    m.rigid_diaphragms = False
    m.add_material(Material("S", E=E, nu=0.3, unit_weight=0.0))
    m.add_section(FrameSection.rectangular("C", "S", 0.1, 0.2))
    m.set_stories([L])
    for k in range(n):
        rel = (("Mi" if k == 0 else "") + ("Mj" if k == n - 1 else "")
               if releases else "")
        m.add_member("column", "C", (0, 0, L * k / n),
                     (0, 0, L * (k + 1) / n), story="Story1", uid=f"C{k}",
                     releases=rel)
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.supports.append(PointSupport((0, 0, L), (1, 1, 0, 1, 1, 1)))
    m.pattern("P", "other").nodal_loads.append(NodalLoad((0, 0, L), fz=-1.0))
    m.add_buckling_case("B", {"P": 1.0}, num_modes=2, formulation=form)
    return m


def i_model():
    m = BuildingModel(name="mb")
    m.add_material(Material("STL", E=E, nu=0.3, unit_weight=0.0))
    s = FrameSection("I1", "STL", 1, 1, 1, 1)
    s.shape, s.dims = "I", {"d": 0.30, "bf": 0.15, "tf": 0.012, "tw": 0.008}
    m.add_section(s)
    t = FrameSection("T1", "STL", 1, 1, 1, 1)
    t.shape, t.dims = "rect_tube", {"d": 0.2, "b": 0.2, "t": 0.004}
    m.add_section(t)
    m.set_stories([3.0])
    m.add_member("beam", "I1", (0, 0, 3), (4, 0, 3), story="Story1",
                 uid="B1")
    m.add_member("column", "T1", (5, 0, 0), (5, 0, 3), story="Story1",
                 uid="C9")
    return m


# ------------------------------------------------- shells / links / releases
def test_ss_plate_uniaxial_k4():
    """Simply supported square plate, uniform compression: Ncr = 4 pi^2 D
    / b^2 (k = 4), within 2 %."""
    r = OpenSeesEngine(plate_model()).run_buckling("B")
    D = E * 0.01 ** 3 / (12 * (1 - 0.09))
    Ncr = 4 * math.pi ** 2 * D / 1.0
    assert r.factors[0] == pytest.approx(Ncr, rel=0.02)
    # second mode: two half-waves, k = (2 + 1/2)^2 = 6.25
    assert r.factors[1] == pytest.approx(6.25 * math.pi ** 2 * D, rel=0.04)
    d = r.to_dict()
    assert d["formulation"] == "fem"
    assert d["geometric_stiffness"]["shell_elements"] == 144
    assert d["solver"]["converged"] is True


def test_shell_strip_column_euler():
    """Plate strip column (nu = 0, pinned ends): P = pi^2 E b t^3 / 12 L^2."""
    b, t, L, nz = 0.2, 0.01, 2.0, 20
    m = BuildingModel(name="strip")
    m.rigid_diaphragms = False
    m.add_material(Material("S", E=E, nu=0.0, unit_weight=0.0))
    m.add_shell_section(ShellSection("PL", "S", t))
    m.set_stories([L])
    m.add_shell("wall", "shell", "PL", [(0, 0, 0), (b, 0, 0), (b, 0, L),
                                        (0, 0, L)],
                mesh_size=L / nz * 1.0001, story="Story1", uid="W1")
    pts = mesh_model(m).points
    pat = m.pattern("P", "other")
    nx = len({round(p[0], 9) for p in pts}) - 1
    for p in pts:
        x, _y, z = p
        if abs(z) < 1e-9:
            m.supports.append(PointSupport(tuple(p),
                                           (1 if x == 0 else 0, 1, 1, 0, 0,
                                            0)))
        elif abs(z - L) < 1e-9:
            m.supports.append(PointSupport(tuple(p), (0, 1, 0, 0, 0, 0)))
            edge = abs(x) < 1e-9 or abs(x - b) < 1e-9
            pat.nodal_loads.append(NodalLoad(tuple(p),
                                             fz=-(0.5 if edge else 1.0) / nx))
    m.add_buckling_case("B", {"P": 1.0}, num_modes=1, formulation="fem")
    r = OpenSeesEngine(m).run_buckling("B")
    assert r.factors[0] == pytest.approx(math.pi ** 2 * E * b * t ** 3 / 12
                                         / L ** 2, rel=0.02)


def link_column(k):
    L, n = 4.0, 8
    m = BuildingModel(name="lnk")
    m.rigid_diaphragms = False
    m.add_material(Material("S", E=E, nu=0.3, unit_weight=0.0))
    m.add_section(FrameSection.rectangular("C", "S", 0.2, 0.2))
    m.set_stories([L])
    for i in range(n):
        m.add_member("column", "C", (0, 0, L * i / n),
                     (0, 0, L * (i + 1) / n), story="Story1", uid=f"C{i}")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 0, 1)))
    m.supports.append(PointSupport((1.0, 0, L), (1, 1, 1, 1, 1, 1)))
    m.supports.append(PointSupport((0, 0, L), (0, 1, 0, 1, 0, 1)))
    m.add_link((0, 0, L), (1.0, 0, L), stiffness=[k, 0, 0, 0, 0, 0],
               uid="LK1")
    m.pattern("P", "other").nodal_loads.append(NodalLoad((0, 0, L), fz=-1.0))
    m.add_buckling_case("B", {"P": 1.0}, num_modes=1, formulation="fem")
    m.add_buckling_case("BX", {"P": 1.0}, num_modes=1, formulation="fem",
                        include_links=False)
    return m


def test_link_supported_column():
    """Pinned-base column braced at the top by a link spring k: the sway
    mode is a rigid rotation, P = k L exactly (when k L < pi^2 EI/L^2);
    a stiff link gives the pinned-pinned Euler load."""
    r = OpenSeesEngine(link_column(50.0)).run_buckling("B")
    assert r.factors[0] == pytest.approx(50.0 * 4.0, rel=1e-6)
    I = 0.2 ** 4 / 12
    r = OpenSeesEngine(link_column(1e7)).run_buckling("B")
    assert r.factors[0] == pytest.approx(math.pi ** 2 * E * I / 16.0,
                                         rel=2e-3)


def test_released_column_pinned_pinned():
    """Fixed supports, end moment releases on the end members: the column
    is pinned-pinned -> pi^2 EI_min / L^2 (the frame formulation keeps its
    legacy 'releases ignored' answer, byte-identical)."""
    m = column_model()
    r = OpenSeesEngine(m).run_buckling("B")
    Imin = m.sections["C"].I22
    assert r.factors[0] == pytest.approx(math.pi ** 2 * E * Imin / 16.0,
                                         rel=5e-4)
    leg = OpenSeesEngine(column_model(form="frame")).run_buckling("B")
    assert "member end releases ignored (members treated continuous)" \
        in leg.warnings
    assert "formulation" not in leg.to_dict()


def test_fem_base_case_two_load_set_identity():
    """Shared distribution: lambda(base = 1 x P) = lambda(no base) - 1."""
    m = column_model(releases=False)
    m.add_case("PC", {"P": 1.0})
    m.add_buckling_case("BB", {"P": 1.0}, num_modes=1, formulation="fem",
                        base_case="PC")
    eng = OpenSeesEngine(m)
    a = eng.run_buckling("B").factors[0]
    b = eng.run_buckling("BB").factors[0]
    assert b == pytest.approx(a - 1.0, rel=1e-7)


def test_fem_on_buildings_and_exclusions():
    m = quick_building(name="q", bays_x=2, bays_y=1, stories=2)
    m.add_buckling_case("BE", {"DEAD": 1.0}, num_modes=2, formulation="fem")
    r = OpenSeesEngine(m).run().to_dict()
    bk = r["buckling"]["BE"]
    assert bk["factors"][0] > 0 and bk["solver"]["converged"]
    # every mode node is a results node (the viewer maps by tag)
    assert set(bk["modes"]["1"]) <= set(r["nodes"])
    # a soft link between two roof joints: excluding it changes little,
    # and the modes are reported on the full model's node tags
    m.add_link((0, 0, 6.4), (6, 0, 6.4), stiffness=[1.0, 0, 0, 0, 0, 0],
               uid="LK")
    m.add_buckling_case("BX", {"DEAD": 1.0}, num_modes=1, formulation="fem",
                        include_links=False)
    m.add_buckling_case("BL", {"DEAD": 1.0}, num_modes=1, formulation="fem")
    eng = OpenSeesEngine(m)
    rx, rl = eng.run_buckling("BX"), eng.run_buckling("BL")
    assert rx.extra["include_links"] is False
    assert rx.factors[0] == pytest.approx(rl.factors[0], rel=1e-3)
    assert set(rx.modes[1]) <= set(eng._build().node_coords)


# ------------------------------------------------------- model / JSON
def test_case_options_roundtrip_and_validation():
    m = column_model(form="frame")
    assert m.buckling_cases["B"].to_dict() == {
        "name": "B", "gravity": {"P": 1.0}, "num_modes": 2,
        "base_case": None}
    m2 = column_model()
    m2.buckling_cases["B"].include_shells = False
    d = m2.to_dict()
    assert d["buckling_cases"]["B"]["formulation"] == "fem"
    assert d["buckling_cases"]["B"]["include_shells"] is False
    back = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert back.to_dict() == d
    bad = copy.deepcopy(d)
    bad["buckling_cases"]["B"]["formulation"] = "plate"
    with pytest.raises(ValueError, match="formulation"):
        BuildingModel.from_dict(bad).validate()
    bad["buckling_cases"]["B"]["formulation"] = "fem"
    bad["buckling_cases"]["B"]["include_links"] = "no"
    with pytest.raises(ValueError, match="include_links"):
        BuildingModel.from_dict(bad).validate()


# -------------------------------------------------- element matrices
def test_frame_kg_release_condensation_is_string_stiffness():
    """Both ends pinned in both planes: the condensed beam-column Kg is the
    string stiffness N/L on the transverse translations."""
    N, L = -10.0, 2.0
    kg = gk.frame_kg_local(N, L, 3, 3)
    for v, w in ((1, 7), (2, 8)):
        assert kg[v, v] == pytest.approx(N / L)
        assert kg[v, w] == pytest.approx(-N / L)
    for r in (4, 5, 10, 11):
        assert np.allclose(kg[r], 0.0) and np.allclose(kg[:, r], 0.0)
    assert np.allclose(gk.frame_kg_local(N, L), gk._local_geometric(N, L))


def test_shell_kg_rigid_translation_and_uniform_stretch():
    P = np.array([[0, 0, 0], [2, 0, 0], [2, 1, 0], [0, 1, 0]], float)
    tens = gk.constant_tensors(-3.0, 1.0, 0.5, 4)
    k = gk.shell_kg_scalar("ShellMITC4", P, tens)
    assert np.allclose(k, k.T)
    assert np.allclose(k @ np.ones(4), 0.0)          # rigid translation
    # w = x: energy = n11 * area
    w = P[:, 0]
    assert w @ k @ w == pytest.approx(-3.0 * 2.0)
    kt = gk.shell_kg_scalar("ShellDKGT", P[:3], tens[:3])
    w = P[:3, 1]
    assert w @ kt @ w == pytest.approx(1.0 * 1.0)    # n22 * area(=1)


# ------------------------------------------------------ Lanczos kernel
def test_lanczos_repeated_eigenvalues_dense_reference():
    rng = np.random.default_rng(1)
    n = 60
    A = rng.standard_normal((n, n))
    K = A @ A.T + n * np.eye(n)
    w, V = np.linalg.eigh(K)
    Kh = V @ np.diag(np.sqrt(w)) @ V.T
    th = np.concatenate([[5, 5, 4], rng.uniform(-3, 1, n - 3)])
    U = np.linalg.qr(rng.standard_normal((n, n)))[0]
    G = Kh @ U @ np.diag(th) @ U.T @ Kh
    Ki = np.linalg.inv(K)
    r = lz.eigs(lambda f: Ki @ f, lambda x: G @ x, np.ones(n, bool), 3,
                tol=1e-10)
    assert r.thetas == pytest.approx([5, 5, 4], rel=1e-9)
    for t, x in zip(r.thetas, r.vectors):
        assert np.linalg.norm(G @ x - t * K @ x) < 1e-8 * np.linalg.norm(
            K @ x)


# ------------------------------------------------------ sparse modal
def test_lanczos_modal_equals_legacy():
    from skyframe.core.results_compare import compare
    m = quick_building(name="q", bays_x=3, bays_y=2, stories=3)
    m.add_rs_case("RSX", "X", spectrum=[[0, 0.4], [1, 1.0], [4, 0.2]])
    out = {}
    for es in ("legacy", "lanczos"):
        mm = copy.deepcopy(m)
        mm.analysis_options = {"eigen_solver": es}
        out[es] = json.loads(json.dumps(OpenSeesEngine(mm).run().to_dict()))
    ok, bad, worst = compare(out["legacy"], out["lanczos"], rtol=1e-9,
                             ignore_keys=("tors_ratio_x", "tors_ratio_y",
                                          "flag", "elapsed_s"))
    assert ok, bad


def test_lanczos_modal_not_used_below_thresholds():
    from skyframe.engine import sparse_modal as spm
    m = quick_building(name="q", bays_x=1, bays_y=1, stories=2)
    eng = OpenSeesEngine(m)
    asm = eng._build()
    assert spm.applicable(eng, asm, 3) is False       # BandGeneral model
    m.analysis_options = {"eigen_solver": "lanczos"}
    assert spm.applicable(eng, asm, 3) is True


def test_mumps_policy_threshold():
    class A:
        node_coords = {t: (0, 0, 0) for t in range(1, 4001)}
        node_restraints = {}
        story_nodes = {}
        masters = {}
    m = BuildingModel(name="x")
    assert slv.estimate_equations(A) == 24000 >= slv.MUMPS_EQ_THRESHOLD
    assert slv.choose(m, A) == ("Mumps", "")
    A.node_coords = {t: (0, 0, 0) for t in range(1, 1001)}
    assert slv.choose(m, A) == ("UmfPack", "")
    assert "Mumps" in slv.FACTOR_ONCE_SYSTEMS
    assert slv.threads_info()["multithreaded"] is False


# ------------------------------------------------------ sparse stability
def test_sparse_stability_matches_dense():
    from skyframe.core.checks import stability_diagnostics
    m = BuildingModel(name="chk")
    m.materials["C"] = Material("C", E=3.0e7, nu=0.2, unit_weight=24.0)
    m.add_section(FrameSection.rectangular("S", "C", 0.4, 0.4))
    m.set_stories([3.0])
    m.base_fixity = "pinned"
    m.add_member("column", "S", (0, 0, 0), (0, 0, 3), uid="C1")
    m.add_member("column", "S", (5, 0, 0), (5, 0, 3), uid="C2")
    m.add_member("beam", "S", (0, 0, 3), (5, 0, 3), uid="B1",
                 releases="Mi,Mj")
    d = stability_diagnostics(m)
    s = stability_diagnostics(m, method="sparse")
    assert s["method"] == "sparse" and d["n_mechanisms"] == s["n_mechanisms"]
    key = lambda r: [(tuple(x["point"]), x["dof"])
                     for x in r["unstable_dofs"][:4]]
    assert key(s) == key(d)
    q = stability_diagnostics(quick_building(bays_x=2, bays_y=1, stories=2),
                              method="sparse")
    assert q["stable"] is True and q["n_mechanisms"] == 0
    # "dense" keeps the legacy skip above the cap
    assert stability_diagnostics(m, max_dofs=5, method="dense")["skipped"]


# -------------------------------------------------------- BucklingFEM
def test_bucklingfem_ltb_matches_closed_form():
    """Fork-supported I beam under uniform moment: Mcr = (pi/L) sqrt(E Iy
    G J) sqrt(1 + pi^2 E Cw / (G J L^2)) (mid-line properties), 5 %."""
    r = member_buckling(i_model(), "B1", forces={"M3i": 100.0, "M3j": 100.0},
                        num_modes=1)
    Mcr = r["references"]["Mcr_ltb"]
    assert 100.0 * r["factors"][0] == pytest.approx(Mcr, rel=0.05)
    assert r["modes"][0]["class"] == "lateral-torsional"
    assert r["modes"][0]["half_waves"] == 1


def test_bucklingfem_local_plate_buckling():
    """Square tube in compression: the walls buckle as simply supported
    plates, k = 4 (3 %); the I web buckles locally between the SS (k = 4)
    and clamped (k = 6.97) plate values."""
    m = i_model()
    r = member_buckling(m, "C9", forces={"P": -100.0}, num_modes=1,
                        length=0.784, mesh={"n_long": 24, "n_web": 8})
    sig = 100.0 / r["section_props"]["A"]
    pl = r["references"]["plates"][0]
    assert r["factors"][0] * sig == pytest.approx(pl["sigma_cr"], rel=0.03)
    assert r["modes"][0]["class"] == "local"
    r = member_buckling(m, "B1", forces={"P": -100.0}, num_modes=1,
                        length=1.2)
    web = [p for p in r["references"]["plates"] if p["role"] == "web"][0]
    k = 4.0 * r["factors"][0] * 100.0 / r["section_props"]["A"] \
        / web["sigma_cr"]
    assert 4.0 < k < 6.97
    assert r["modes"][0]["class"] == "local"


def test_bucklingfem_geometry_and_forces_from_case():
    g = {"d": 0.3, "bf": 0.15, "tf": 0.012, "tw": 0.008}
    p = midline_props(plates("I", g))
    h0 = 0.3 - 0.012
    assert p["A"] == pytest.approx(2 * 0.15 * 0.012 + h0 * 0.008)
    assert p["I22"] == pytest.approx(2 * 0.012 * 0.15 ** 3 / 12)
    st = {"x": [0, 1, 2, 3, 4], "N": [-5.0] * 5, "M2": [0.0] * 5,
          "V2": [0.0] * 5}
    w, L = 3.0, 4.0
    st["M3"] = [w * x * (L - x) / 2 for x in st["x"]]
    f = forces_from_case({"member_stations": {"B": st}}, "B", L)
    assert f["P"] == -5.0 and f["w2"] == pytest.approx(w)
    with pytest.raises(ValueError, match="plate dimensions"):
        mm = i_model()
        mm.add_section(FrameSection.rectangular("R", "STL", 0.2, 0.3))
        mm.add_member("beam", "R", (0, 1, 3), (4, 1, 3), story="Story1",
                      uid="BR")
        member_buckling(mm, "BR", forces={"P": -1.0})


def test_bucklingfem_api():
    from skyframe.api.server import create_app
    app = create_app()
    c = app.test_client()
    m = i_model()
    m.supports.append(PointSupport((0, 0, 3), (1, 1, 1, 1, 0, 0)))
    m.supports.append(PointSupport((4, 0, 3), (0, 1, 1, 0, 0, 0)))
    m.supports.append(PointSupport((5, 0, 0), (1, 1, 1, 1, 1, 1)))
    from skyframe.core.model import MemberLoad
    m.pattern("P", "other").member_loads.append(
        MemberLoad("B1", kind="udl", w=2.0))
    m.add_case("PC", {"P": 1.0})
    body = {"model": m.to_dict(), "member": "B1",
            "forces": {"M3i": 50.0, "M3j": 50.0}, "num_modes": 1,
            "mesh": {"n_long": 30}}
    r = c.post("/api/member_buckling", json=body)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["factors"] and d["shapes"]["1"] and d["quads"]
    assert c.post("/api/member_buckling", json={"member": "nope"}
                  ).status_code == 400
    f = c.post("/api/member_buckling/forces",
               json={"model": m.to_dict(), "member": "B1", "case": "PC"})
    assert f.status_code == 200 and "M3i" in f.get_json()["forces"]
    assert c.post("/api/check/stability", json={"method": "x"}
                  ).status_code == 400
