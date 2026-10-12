"""A3: shell force contours (RS / envelopes, principal, nodal averaging,
global axes) and shell stresses (S11..SVM top/mid/bottom, layered shells).

CONTRACT "Shell force contours and shell stresses".  Every number is pinned
to a hand value:

* cantilever plate under a tip line load: M11 = P (L - x)/b, S11 = +-6M/t^2
  (top in tension for the hogging moment), nodal averages = M(x_node);
* membrane wall in uniaxial tension: F11 = P/h, S11 = P/(h t) at every face;
* principal / von Mises / angle of a known state;
* averaging of a uniform field is exact on a mixed quad / triangle mesh
  whose elements all have different local axes (and in global axes);
* envelope / RS (stubbed per-mode shell forces combined with the engine's
  CQC helpers until RS shell_forces land) / add / abs / srss / range max-min.
"""

import json
import math

import numpy as np
import pytest

from _v02_utils import (          # noqa: E402
    model_mod, NodalLoad, PointSupport, new_model, finish, run,
)
import openseespy.opensees as ops

from skyframe.core import shell_results as sr
from skyframe.core import tables
from skyframe.core.model import Material, ShellSection

P, L, B, T = 10.0, 4.0, 1.0, 0.2


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
def _plate(layered=None):
    """Cantilever slab 4 x 1 x 0.2 at z = 3, fixed along x = 0, tip line
    load P (downward) at x = L; nu = 0 (no anticlastic coupling)."""
    mdl = new_model("plate", [3.0], nu=0.0)
    mdl.add_material(Material("STL", E=2.0e8, nu=0.3))
    mdl.add_shell_section(ShellSection("SL", "CONC", T, layered=layered))
    mdl.add_shell("slab", "shell", "SL",
                  [(0, 0, 3), (L, 0, 3), (L, B, 3), (0, B, 3)],
                  mesh_size=0.5, story="Story1")
    for y in (0.0, 0.5, 1.0):
        mdl.supports.append(PointSupport((0, y, 3), (1, 1, 1, 1, 1, 1)))
    for y, f in ((0.0, 0.25), (0.5, 0.5), (1.0, 0.25)):
        mdl.pattern("TIP", "dead").nodal_loads.append(
            NodalLoad((L, y, 3), fz=-P * f))
    mdl.add_case("TIP", {"TIP": 1.0})
    finish(mdl, (L, B, 3))
    return mdl


def _wall(along_y=False):
    """2 x 1 x 0.2 wall in uniaxial tension 10 kN over its 1 m height."""
    mdl = new_model("wall", [3.0], nu=0.0)
    mdl.add_shell_section(ShellSection("SH", "CONC", T))
    P2 = (lambda x, z: (0, x, z)) if along_y else (lambda x, z: (x, 0, z))
    mdl.add_shell("wall", "shell", "SH",
                  [P2(0, 0), P2(2, 0), P2(2, 1), P2(0, 1)],
                  mesh_size=0.5, story="Story1")
    for z in (0.0, 0.5, 1.0):
        fx = (1, 1, 1, 1, 1, 1) if z == 0 else \
            ((1, 1, 0, 1, 1, 1))
        mdl.supports.append(PointSupport(P2(0, z), fx))
    for z, f in ((0.0, 2.5), (0.5, 5.0), (1.0, 2.5)):
        kw = {"fy": f} if along_y else {"fx": f}
        mdl.pattern("P", "dead").nodal_loads.append(NodalLoad(P2(2, z), **kw))
    mdl.add_case("P", {"P": 1.0})
    finish(mdl, P2(2, 1))
    return mdl


@pytest.fixture(scope="module")
def plate():
    m = _plate()
    return m, run(m)


@pytest.fixture(scope="module")
def wall():
    m = _wall()
    return m, run(m)


def _centroids(d):
    nodes = d["nodes"]
    return [np.mean([nodes[str(t)] for t in q["nodes"]], axis=0)
            for q in d["shell_quads"]]


# --------------------------------------------------------------------------- #
# 1. cantilever plate: sigma = 6 M / t^2 by hand
# --------------------------------------------------------------------------- #
def test_cantilever_plate_moment_and_face_stresses(plate):
    m, d = plate
    cen = _centroids(d)
    M11 = sr.contour(m, d, "TIP", "M11")["values"]
    top = sr.contour(m, d, "TIP", "S11", location="top")["values"]
    bot = sr.contour(m, d, "TIP", "S11", location="bot")["values"]
    mid = sr.contour(m, d, "TIP", "S11", location="mid")["values"]
    for c, mm, st, sb, sm in zip(cen, M11, top, bot, mid):
        Mh = P * (L - c[0]) / B                     # hogging, kN*m/m
        assert mm == pytest.approx(Mh, rel=1e-6)
        assert st == pytest.approx(6 * Mh / T ** 2, rel=1e-6)    # tension
        assert sb == pytest.approx(-6 * Mh / T ** 2, rel=1e-6)
        assert abs(sm) < 1e-6 * 6 * P * L / T ** 2
    # explicit number: element at x = 0.25: M = 37.5, S11top = 5625 kPa
    i0 = int(np.argmin([c[0] + c[1] for c in cen]))
    assert top[i0] == pytest.approx(6 * 37.5 / 0.04, rel=1e-6) == 5625.0
    # principal top stress = S11 (uniaxial), von Mises = |S11|
    smax = sr.contour(m, d, "TIP", "SMax")["values"]
    svm = sr.contour(m, d, "TIP", "SVM", location="bot")["values"]
    assert smax[i0] == pytest.approx(5625.0, rel=1e-6)
    assert svm[i0] == pytest.approx(5625.0, rel=1e-6)


def test_cantilever_nodal_averaging_gives_nodal_moment(plate):
    """Linear M field: the average of the two element centroid values about
    an interior node equals M(x_node) exactly."""
    m, d = plate
    c = sr.contour(m, d, "TIP", "M11", averaging="objects")
    nodes = d["nodes"]
    checked = 0
    for q, cs in zip(d["shell_quads"], c["corners"]):
        for t, v in zip(q["nodes"], cs):
            x = nodes[str(t)][0]
            if 1e-9 < x < L - 1e-9:
                assert v == pytest.approx(P * (L - x) / B, rel=1e-6)
                checked += 1
    assert checked > 20
    # the fixed-edge nodes belong to one element each: its centroid value
    assert c["averaging"] == "objects" and c["range"][1] == pytest.approx(
        P * (L - 0.25), rel=1e-6)


def test_plate_global_axes_equal_local_for_ccw_slab(plate):
    m, d = plate
    for comp in ("M11", "M22", "M12", "V13", "F11"):
        a = sr.contour(m, d, "TIP", comp)["values"]
        b = sr.contour(m, d, "TIP", comp, axes="global")["values"]
        assert a == pytest.approx(b, abs=1e-9)


# --------------------------------------------------------------------------- #
# 2. membrane under uniaxial tension
# --------------------------------------------------------------------------- #
def test_membrane_uniaxial_tension(wall):
    m, d = wall
    for av in sr.AVERAGING:
        for comp, want in (("F11", 10.0), ("F22", 0.0), ("F12", 0.0),
                           ("FMax", 10.0), ("FMin", 0.0), ("FVM", 10.0),
                           ("S11", 50.0), ("SVM", 50.0), ("M11", 0.0)):
            for loc in sr.FACES:
                c = sr.contour(m, d, "P", comp, location=loc, averaging=av)
                vals = c["values"] + [v for cs in (c["corners"] or [])
                                      for v in cs]
                assert vals == pytest.approx([want] * len(vals), abs=1e-9)


def test_wall_along_y_global_axes():
    """A wall running along Y: local 1 = +Y (corner order), global 1 = the
    horizontal in-plane direction (+Y), 2 = up; F11 = 10 either way and the
    uniaxial state lands on F11 in global axes."""
    m = _wall(along_y=True)
    d = run(m)
    g = sr.global_frame(np.array([1.0, 0, 0]))
    assert g == pytest.approx(np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]]))
    for axes in sr.AXES:
        f11 = sr.contour(m, d, "P", "F11", axes=axes)["values"]
        f22 = sr.contour(m, d, "P", "F22", axes=axes)["values"]
        assert f11 == pytest.approx([10.0] * len(f11), abs=1e-9)
        assert f22 == pytest.approx([0.0] * len(f22), abs=1e-9)


def test_global_frame_rules():
    up = sr.global_frame(np.array([0, 0, -1.0]))       # slab, normal down
    assert up == pytest.approx(np.eye(3))              # X, Y, +Z
    wx = sr.global_frame(np.array([0, 1.0, 0]))        # wall along X
    assert wx[0] == pytest.approx([1, 0, 0]) and wx[1] == pytest.approx(
        [0, 0, 1])
    assert wx[2] == pytest.approx([0, -1, 0])          # 3 = 1 x 2


# --------------------------------------------------------------------------- #
# 3. principal values of a known state
# --------------------------------------------------------------------------- #
def _inject(d, case, per_elem):
    d = json.loads(json.dumps(d))
    d["cases"][case]["shell_forces"] = {str(i): list(map(float, v))
                                        for i, v in enumerate(per_elem)}
    return d


def test_principal_values_of_known_state(wall):
    m, d0 = wall
    n = len(d0["shell_quads"])
    st = [3.0, -1.0, 2.0, 30.0, -10.0, 20.0, 3.0, 4.0]
    d = _inject(d0, "P", [st] * n)
    g = lambda c, **k: sr.contour(m, d, "P", c, **k)["values"][0]
    r = math.sqrt(8.0)
    assert g("FMax") == pytest.approx(1 + r)
    assert g("FMin") == pytest.approx(1 - r)
    assert g("FVM") == pytest.approx(math.sqrt(9 + 3 + 1 + 12))   # = 5
    assert g("MMax") == pytest.approx(10 + 10 * r)
    assert g("VMax") == pytest.approx(5.0)
    rows = sr.element_rows(m, d, "P", ("FAngle", "MAngle"))[1]
    assert rows[0]["FAngle"] == pytest.approx(22.5)    # atan2(4, 4)/2
    # top-face stress state: N/t + 6M/t^2 = (15+4500, -5-1500, 10+3000)
    a, b, c = 15 + 4500.0, -5 - 1500.0, 10 + 3000.0
    assert g("S11") == pytest.approx(a)
    assert g("SMax") == pytest.approx(0.5 * (a + b) + math.hypot(
        0.5 * (a - b), c))
    assert g("SVM") == pytest.approx(math.sqrt(a * a - a * b + b * b
                                               + 3 * c * c))
    assert g("S13") == pytest.approx(15.0) and g("S23") == pytest.approx(20.0)
    # rotating the principal state by the angle diagonalises it
    th = math.radians(22.5)
    R = np.array([[math.cos(th), math.sin(th)], [-math.sin(th), math.cos(th)]])
    S = R @ np.array([[3.0, 2.0], [2.0, -1.0]]) @ R.T
    assert S[0, 0] == pytest.approx(1 + r) and S[0, 1] == pytest.approx(0,
                                                                         abs=1e-12)


# --------------------------------------------------------------------------- #
# 4. averaging of a uniform field is exact (mixed frames)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def poly():
    mdl = new_model("poly", [3.0])
    mdl.add_shell_section(ShellSection("SH", "CONC", 0.2))
    corners = [(0, 0, 3), (4, 0, 3), (4, 3, 3), (2, 4.5, 3), (0, 3, 3)]
    mdl.add_shell("slab", "shell", "SH", corners, mesh_size=1.0,
                  story="Story1")
    mdl.add_shell("slab", "shell", "SH",
                  [(4, 0, 3), (6, 0, 3), (6, 3, 3), (4, 3, 3)],
                  mesh_size=1.0, story="Story1")
    for (x, y, z) in corners + [(6, 0, 3), (6, 3, 3)]:
        mdl.supports.append(PointSupport((x, y, z), (1, 1, 1, 0, 0, 0)))
    mdl.pattern("Q", "dead").area_loads.append(
        model_mod.AreaLoad(region_uid="S1", q=2.0))
    mdl.add_case("Q", {"Q": 1.0})
    finish(mdl, (4, 0, 3))
    return mdl, run(mdl)


def _uniform_local(geo, N, M, V):
    """Element-local resultants of a uniform GLOBAL (X, Y) field."""
    out = []
    for E in geo.E:
        e = E[:2, :2]                       # in-plane components (slab)
        s = np.sign(E[2, 2])
        n = e @ N @ e.T
        mm = s * (e @ M @ e.T)
        v = s * (e @ V)
        out.append([n[0, 0], n[1, 1], n[0, 1], mm[0, 0], mm[1, 1], mm[0, 1],
                    v[0], v[1]])
    return out


def _scramble(d0):
    """Cyclically shift / reverse element node lists (synthetic): every
    element gets its own local axes and half the normals flip."""
    d = json.loads(json.dumps(d0))
    for i, q in enumerate(d["shell_quads"]):
        nd = q["nodes"]
        k = i % len(nd)
        nd = nd[k:] + nd[:k]
        q["nodes"] = nd[::-1] if i % 2 else nd
    return d


def test_uniform_field_averaging_exact(poly):
    m, d0 = poly
    d0 = _scramble(d0)
    geo = sr.Geometry(m, d0)
    assert any(len(nd) == 3 for nd in geo.nodes)          # triangles present
    e1s = {tuple(np.round(E[0], 6)) for E in geo.E}
    assert len(e1s) > 3                                   # many local frames
    assert {np.sign(E[2, 2]) for E in geo.E} == {-1.0, 1.0}
    N = np.array([[5.0, 2.0], [2.0, -3.0]])
    M = np.array([[1.5, -0.5], [-0.5, 0.75]])
    V = np.array([0.4, -0.7])
    d = _inject(d0, "Q", _uniform_local(geo, N, M, V))
    want = {"F11": 5.0, "F22": -3.0, "F12": 2.0, "M11": 1.5, "M12": -0.5,
            "V23": -0.7, "FMax": 1 + math.hypot(4, 2),
            "S11": 5 / 0.2 + 6 * 1.5 / 0.04}
    for av in sr.AVERAGING:
        for comp, w in want.items():
            c = sr.contour(m, d, "Q", comp, axes="global", averaging=av)
            vals = c["values"] + [v for cs in (c["corners"] or []) for v in cs]
            assert vals == pytest.approx([w] * len(vals), rel=1e-9, abs=1e-9)
        # local axes: averaged corner == own element value (uniform field)
        c = sr.contour(m, d, "Q", "F11", averaging=av)
        if c["corners"]:
            for v, cs in zip(c["values"], c["corners"]):
                assert cs == pytest.approx([v] * len(cs), abs=1e-9)
    # FMax is frame invariant: local == global
    a = sr.contour(m, d, "Q", "FMax")["values"]
    assert a == pytest.approx([1 + math.hypot(4, 2)] * len(a))


def test_objects_vs_all_averaging_at_region_seam(poly):
    """Different uniform values in the two regions: "objects" keeps each
    region's value at the shared edge, "all" averages across it."""
    m, d0 = poly
    geo = sr.Geometry(m, d0)
    vals = []
    for i in range(geo.n):
        v = 10.0 if geo.region[i] == "S1" else 20.0
        vals.append(_uniform_local(geo, np.diag([v, v]), np.zeros((2, 2)),
                                   np.zeros(2))[i])
    d = _inject(d0, "Q", vals)
    co = sr.contour(m, d, "Q", "F11", averaging="objects")["corners"]
    ca = sr.contour(m, d, "Q", "F11", averaging="all")["corners"]
    nodes = d["nodes"]
    seam = 0
    for i, q in enumerate(d["shell_quads"]):
        own = 10.0 if geo.region[i] == "S1" else 20.0
        for k, t in enumerate(q["nodes"]):
            assert co[i][k] == pytest.approx(own)
            if abs(nodes[str(t)][0] - 4.0) < 1e-9 and 0 < nodes[str(t)][1] < 3:
                assert 10.0 < ca[i][k] < 20.0                 # mixed
                seam += 1
    assert seam > 0


# --------------------------------------------------------------------------- #
# 5. element axes == OpenSees 'stresses' axes (probe, every family)
# --------------------------------------------------------------------------- #
def _probe(eltype, pts):
    from skyframe.engine import shell_elements as shel
    from types import SimpleNamespace
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    for i, p in enumerate(pts):
        ops.node(i + 1, *p)
    ops.section("ElasticMembranePlateSection", 1, 2e7, 0.0, 0.2)
    if eltype == "ASDShellQ4":                  # the engine adds -local
        ssec = SimpleNamespace(shell_type="shell_thick")
        shel.add_element(ssec, 1, list(range(1, 5)), 1)
    else:
        ops.element(eltype, 1, *range(1, len(pts) + 1), 1)
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    for i, p in enumerate(pts):
        u = [1e-4 * p[0], 0, 1e-4 * p[0], 0, 0, 0]  # exx and gamma_xz
        for k in range(6):
            ops.sp(i + 1, k + 1, float(u[k]))
    ops.system("FullGeneral"); ops.numberer("Plain")
    ops.constraints("Penalty", 1e14, 1e14)
    ops.integrator("LoadControl", 1.0); ops.algorithm("Linear")
    ops.analysis("Static")
    assert ops.analyze(1) == 0
    ops.eleResponse(1, "forces")
    s = np.asarray(ops.eleResponse(1, "stresses")).reshape(-1, 8).mean(0)
    ops.wipe()
    return s


@pytest.mark.parametrize("eltype", ["ShellMITC4", "ShellDKGQ", "ASDShellQ4",
                                    "ShellDKGT", "ASDShellT3"])
def test_element_frame_matches_opensees(eltype):
    quad = [(0, 0, 0), (2, 0.3, 0), (2.2, 1.9, 0), (-0.1, 1.5, 0)]
    pts = quad if eltype.endswith("Q4") or eltype in ("ShellMITC4",
                                                      "ShellDKGQ") \
        else [(0, 0, 0), (2, 0.3, 0), (0.4, 1.9, 0)]
    s = _probe(eltype, pts)
    E = sr.element_frame(pts, eltype)
    Ng = np.diag([2e7 * 0.2 * 1e-4, 0.0, 0.0])          # exx only, nu = 0
    pred = [E[0] @ Ng @ E[0], E[1] @ Ng @ E[1], E[0] @ Ng @ E[1]]
    assert s[:3] == pytest.approx(pred, abs=1e-6 * 400)
    # transverse shear (Mindlin elements) acts along global X:
    # (V13, V23) ∝ (e1.X, e2.X) -- probed on an undistorted (rotated
    # rectangle / triangle) element where the constant shear is exact
    c, s30 = math.cos(math.radians(30)), math.sin(math.radians(30))
    rot = lambda x, y: (c * x - s30 * y, s30 * x + c * y, 0.0)
    pts2 = ([rot(0, 0), rot(2, 0), rot(2, 1), rot(0, 1)] if len(pts) == 4
            else [rot(0, 0), rot(2, 0), rot(0, 1)])
    v = _probe(eltype, pts2)[6:8]
    E = sr.element_frame(pts2, eltype)
    if np.linalg.norm(v) > 1e-9:
        dirn = np.array([E[0][0], E[1][0]])
        assert v / np.linalg.norm(v) == pytest.approx(dirn, abs=2e-3)


# --------------------------------------------------------------------------- #
# 6. envelopes, RS (stub) and max/min combos
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def rs_plate():
    """The cantilever plate plus an RS case, envelope / add / abs / srss /
    range combos.  RS shell forces: stubbed per-mode values CQC-combined
    with the engine's modal-combination helper (until the engine emits
    rs_cases[...]["shell_forces"] itself)."""
    from skyframe.engine.modalcombo import cqc_rho
    from skyframe.engine.opensees_engine import _combine_rows
    m = _plate()
    m.nodal_masses[0].mz = 1.0
    m.num_modes = 2
    m.pattern("UP", "other").nodal_loads.append(
        NodalLoad((L, 0.5, 3), fz=+P))
    m.add_case("UP", {"UP": 1.0})
    m.add_rs_case("RSZ", "X", [[0.0, 1.0], [10.0, 1.0]], num_modes=2)
    m.add_combo("ENV", {"TIP": 1.0, "UP": 1.0}, combo_type="envelope")
    m.add_combo("1.2D+E", {"TIP": 1.2, "RSZ": 1.0})
    m.add_combo("ABS", {"TIP": 1.0, "RSZ": 1.0}, combo_type="abs")
    m.add_combo("SRSS", {"TIP": 1.0, "RSZ": 1.0}, combo_type="srss")
    m.add_combo("RNG", {"TIP": 1.0, "UP": 1.0}, combo_type="range")
    m.add_combo("ENV2", {"ENV": 1.0, "1.2D+E": 1.0}, combo_type="envelope")
    d = run(m)
    n = len(d["shell_quads"])
    rng = np.random.default_rng(7)
    per_mode = [rng.normal(size=(n, 8)) for _ in range(2)]
    rho = cqc_rho([10.0, 11.0], 0.05)
    flat = _combine_rows([pm.ravel().tolist() for pm in per_mode], rho)
    r = np.asarray(flat).reshape(n, 8)
    return m, d, r


def test_rs_case_without_shell_forces_is_not_a_source(rs_plate):
    m, d, _r = rs_plate
    names = {s["name"] for s in sr.list_sources(m, d)}
    if "shell_forces" not in d["rs_cases"]["RSZ"]:       # before RS shells
        assert "RSZ" not in names and "1.2D+E" not in names
        with pytest.raises(sr.Unavailable, match="RSZ"):
            sr.contour(m, d, "1.2D+E", "M11")
    assert {"TIP", "UP", "ENV", "RNG"} <= names


def _with_rs(d, r):
    d = json.loads(json.dumps(d))
    d["rs_cases"]["RSZ"]["shell_forces"] = {
        str(i): list(map(float, v)) for i, v in enumerate(r)}
    return d


def test_envelope_picks_max_and_min(rs_plate):
    m, d, _r = rs_plate
    a = np.array(sr.contour(m, d, "TIP", "M11")["values"])
    b = np.array(sr.contour(m, d, "UP", "M11")["values"])
    hi = sr.contour(m, d, "ENV", "M11", bound="max")
    lo = sr.contour(m, d, "ENV", "M11", bound="min")
    assert hi["bounded"] and hi["kind"] == "combo"
    assert hi["values"] == pytest.approx(np.maximum(a, b))
    assert lo["values"] == pytest.approx(np.minimum(a, b))
    # tip (hogging) is the max everywhere; the uplift (sagging) the min
    assert hi["values"] == pytest.approx(a) and lo["values"] == pytest.approx(b)
    # principal enveloped per member (not from the component box)
    fa = np.array(sr.contour(m, d, "TIP", "SMax")["values"])
    fb = np.array(sr.contour(m, d, "UP", "SMax")["values"])
    assert sr.contour(m, d, "ENV", "SMax")["values"] == pytest.approx(
        np.maximum(fa, fb))
    assert sr.contour(m, d, "ENV", "SMax", bound="min")["values"] == \
        pytest.approx(np.minimum(fa, fb))
    # range: sum of positive parts / negative parts
    rh = sr.contour(m, d, "RNG", "M11")["values"]
    rl = sr.contour(m, d, "RNG", "M11", bound="min")["values"]
    assert rh == pytest.approx(np.maximum(a, 0) + np.maximum(b, 0))
    assert rl == pytest.approx(np.minimum(a, 0) + np.minimum(b, 0))


def test_rs_stub_contours_and_combos(rs_plate):
    m, d0, r = rs_plate
    d = _with_rs(d0, r)
    names = {s["name"]: s for s in sr.list_sources(m, d)}
    assert names["RSZ"]["bounded"] and names["RSZ"]["kind"] == \
        "response_spectrum"
    k = sr.BASE.index("M11")
    rs_hi = sr.contour(m, d, "RSZ", "M11")["values"]
    rs_lo = sr.contour(m, d, "RSZ", "M11", bound="min")["values"]
    assert rs_hi == pytest.approx(np.abs(r[:, k]))
    assert rs_lo == pytest.approx(-np.abs(r[:, k]))
    D = np.array(sr.contour(m, d, "TIP", "M11")["values"])
    hi = sr.contour(m, d, "1.2D+E", "M11")["values"]
    lo = sr.contour(m, d, "1.2D+E", "M11", bound="min")["values"]
    assert hi == pytest.approx(1.2 * D + np.abs(r[:, k]))
    assert lo == pytest.approx(1.2 * D - np.abs(r[:, k]))
    ab = sr.contour(m, d, "ABS", "M11")["values"]
    ss = sr.contour(m, d, "SRSS", "M11", bound="min")["values"]
    assert ab == pytest.approx(np.abs(D) + np.abs(r[:, k]))
    assert ss == pytest.approx(-np.hypot(D, r[:, k]))
    # RS stresses: bounded independently, top = |N|/t + 6|M|/t^2
    st = sr.contour(m, d, "RSZ", "S11")
    want = np.abs(r[:, 0]) / T + 6 * np.abs(r[:, 3]) / T ** 2
    assert st["values"] == pytest.approx(want)
    assert st["approximations"]
    # principal of an RS box: governing bound exact = max over the corners
    fm = sr.contour(m, d, "RSZ", "FMax")["values"]
    for i in range(3):
        a, b, c = np.abs(r[i, :3])
        best = max(0.5 * (sa * a + sb * b) + math.hypot(
            0.5 * (sa * a - sb * b), c) for sa in (-1, 1) for sb in (-1, 1))
        assert fm[i] == pytest.approx(best)
    # VMax exact both ways: min = 0 (box straddles 0), max = |r| hypot
    vm = sr.contour(m, d, "RSZ", "VMax", bound="min")["values"]
    assert vm == pytest.approx([0.0] * len(vm))
    # nested envelope = envelope over the union of its members' states
    for bd, fn in (("max", np.maximum), ("min", np.minimum)):
        e2 = sr.contour(m, d, "ENV2", "M11", bound=bd)["values"]
        env = sr.contour(m, d, "ENV", "M11", bound=bd)["values"]
        cmb = sr.contour(m, d, "1.2D+E", "M11", bound=bd)["values"]
        assert e2 == pytest.approx(fn(env, cmb))
    # RS in global axes / averaged: still a valid +- pair
    g = sr.contour(m, d, "RSZ", "M11", axes="global", averaging="all")
    gl = sr.contour(m, d, "RSZ", "M11", axes="global", averaging="all",
                    bound="min")
    for cs, cl in zip(g["corners"], gl["corners"]):
        assert cs == pytest.approx([-v for v in cl])


# --------------------------------------------------------------------------- #
# 7. layered shells
# --------------------------------------------------------------------------- #
def test_layered_same_material_layers_match_face_formula():
    lay = {"layers": [{"t": 0.05, "material": "CONC", "kind": "concrete"},
                      {"t": 0.1, "material": "CONC", "kind": "concrete"},
                      {"t": 0.05, "material": "CONC", "kind": "concrete"}]}
    m = _plate(layered=lay)
    d = run(m)
    cen = _centroids(d)
    s0 = sr.contour(m, d, "TIP", "S11", location="layer:0")
    s2 = sr.contour(m, d, "TIP", "S11", location="layer:2")["values"]
    assert s0["approximations"]
    for c, a, b in zip(cen, s0["values"], s2):
        Mh = P * (L - c[0])
        # layer 0 (bottom) centre z = -0.075, layer 2 (top) z = +0.075
        assert a == pytest.approx(12 * Mh * (-0.075) / T ** 3, rel=1e-6)
        assert b == pytest.approx(12 * Mh * 0.075 / T ** 3, rel=1e-6)


def test_layered_steel_layer_uniaxial_bar_stress():
    """Steel layer at the top (angle 0 / 90): sigma = E_s * eps(z) along
    the bar; a 90-degree bar carries nothing from a pure M11 field."""
    for ang in (0, 90):
        m = _plate(layered={"layers": [
            {"t": 0.19, "material": "CONC", "kind": "concrete"},
            {"t": 0.01, "material": "STL", "kind": "steel", "angle": ang}]})
        d = run(m)
        cen = _centroids(d)
        v = sr.contour(m, d, "TIP", "S11", location="layer:1")["values"]
        vm = sr.contour(m, d, "TIP", "SVM", location="layer:1")["values"]
        Ec = 25_000_000.0
        for c, s, w in zip(cen, v, vm):
            Mh = P * (L - c[0])
            kap = 12 * Mh / (Ec * T ** 3)               # nu = 0
            want = 2.0e8 * kap * 0.095 if ang == 0 else 0.0
            assert s == pytest.approx(want, rel=1e-6, abs=1e-6)
            assert w == pytest.approx(abs(want), rel=1e-6, abs=1e-6)


# --------------------------------------------------------------------------- #
# 8. tables registry + rows
# --------------------------------------------------------------------------- #
def test_tables_registered_and_rows(rs_plate):
    m, d0, r = rs_plate
    d = _with_rs(d0, r)
    cat = {t["key"]: t for t in tables.list_tables()}
    assert {"shell_forces", "shell_stresses"} <= set(cat)
    for k in ("line_force", "line_moment", "stress", "angle_deg"):
        assert k in tables.QUANTITIES
    out = tables.compute_table("shell_forces", d, m)
    assert out["columns"] == cat["shell_forces"]["columns"]
    want = {c["key"] for c in out["columns"]}
    n = len(d["shell_quads"])
    tip = [x for x in out["rows"] if x["case"] == "TIP"]
    assert len(tip) == n and all(set(x) == want for x in out["rows"])
    cm = sr.contour(m, d, "TIP", "M11")["values"]
    assert [x["M11"] for x in sorted(tip, key=lambda x: x["element"])] == \
        pytest.approx(cm)
    assert tip[0]["case_type"] == "LinStatic" and tip[0]["step_type"] == ""
    rs = [x for x in out["rows"] if x["case"] == "RSZ"]
    assert {x["step_type"] for x in rs} == {"Max", "Min"}
    assert {x["case_type"] for x in rs} == {"LinRespSpec"}
    st = tables.compute_table("shell_stresses", d, m, cases=["TIP"])
    assert {x["location"] for x in st["rows"]} == {"Top", "Bottom"}
    assert len(st["rows"]) == 2 * n
    e0 = [x for x in st["rows"] if x["element"] == 0]
    assert e0[0]["S11"] == pytest.approx(-e0[1]["S11"])
    json.dumps(st)


def test_layered_table_rows():
    lay = {"layers": [{"t": 0.1, "material": "CONC", "kind": "concrete"},
                      {"t": 0.1, "material": "CONC", "kind": "concrete"}]}
    m = _plate(layered=lay)
    d = run(m)
    st = tables.compute_table("shell_stresses", d, m)
    locs = {x["location"] for x in st["rows"]}
    assert "Layer 1 (CONC, concrete)" in locs and "Layer 2 (CONC, concrete)" \
        in locs
    assert any("layered" in w for w in st["warnings"])


def test_results_untouched(rs_plate):
    m, d, _ = rs_plate
    before = json.dumps(d, sort_keys=True)
    for c in sr.CONTOUR_COMPONENTS:
        sr.contour(m, d, "ENV", c, averaging="all", axes="global")
    for k in ("shell_forces", "shell_stresses"):
        tables.compute_table(k, d, m)
    assert json.dumps(d, sort_keys=True) == before


def test_bad_inputs(plate):
    m, d = plate
    for kw in ({"component": "X11"}, {"location": "side"},
               {"axes": "polar"}, {"averaging": "some"}, {"bound": "avg"}):
        args = {"component": "M11", **kw}
        comp = args.pop("component")
        with pytest.raises(ValueError):
            sr.contour(m, d, "TIP", comp, **args)
    with pytest.raises(sr.Unavailable):
        sr.contour(m, d, "NOPE", "M11")
    with pytest.raises(sr.Unavailable):
        sr.contour(m, {"shell_quads": []}, "TIP", "M11")


# --------------------------------------------------------------------------- #
# 9. HTTP API
# --------------------------------------------------------------------------- #
def test_api_contour_and_sources(tmp_path, monkeypatch, plate):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api import server
    app = server.create_app()
    app.config["TESTING"] = True
    m, d = plate
    with app.test_client() as cl:
        assert cl.post("/api/model", json=m.to_dict()).status_code == 200
        opts = cl.get("/api/shell_results/options").get_json()
        assert "SVM" in [c["key"] for c in opts["components"]]
        src = cl.post("/api/shell_results/sources").get_json()  # solves once
        assert [s["name"] for s in src["sources"]] == ["TIP"]
        assert src["layers"] == 0 and src["n_elements"] == len(
            d["shell_quads"])
        out = cl.post("/api/shell_results/contour",
                      json={"case": "TIP", "component": "S11",
                            "location": "top", "averaging": "objects"}
                      ).get_json()
        assert out["quantity"] == "stress" and out["corners"]
        assert out["range"][1] == pytest.approx(6 * P * (L - 0.25) / T ** 2,
                                                rel=1e-6)
        out2 = cl.post("/api/shell_results/contour",
                       json={"case": "TIP", "component": "M11",
                             "results": d}).get_json()
        assert out2["values"] == pytest.approx(
            sr.contour(m, d, "TIP", "M11")["values"])
        bad = cl.post("/api/shell_results/contour",
                      json={"case": "TIP", "component": "Q"})
        assert bad.status_code == 400 and "component" in bad.get_json()["error"]
        assert cl.post("/api/shell_results/contour",
                       json={}).status_code == 400
        tl = cl.get("/api/tables/list").get_json()["tables"]
        assert "shell_stresses" in [t["key"] for t in tl]
        t = cl.post("/api/tables/shell_forces", json={}).get_json()
        assert len(t["rows"]) == len(d["shell_quads"])
