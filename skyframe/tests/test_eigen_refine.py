"""Eigen verification / Rayleigh-Ritz cleanup / canonical degenerate basis
(engine/eigen_refine.py, CONTRACT "Eigen refinement and tower control
height") and the tower control height of pushovers.

Pins, for every eigen path (band default -> -fullGenLapack / Arpack,
umfpack -> condensed, eigen_solver "legacy"):

* eigen residual ||K phi - lam M phi|| / ||K phi|| and mass-orthogonality
  error, measured with the ASSEMBLED K and M (GimmeMCK + FullGeneral), are
  at round-off (<= 1e-10);
* a response-spectrum X case on doubly symmetric models has no Y response
  (< 1e-8 relative) with CQC and SRSS — including a degenerate cluster CUT
  by the requested mode count;
* CQC is invariant under any rotation of a degenerate pair (SRSS is not —
  which is why the basis is made canonical);
* non-degenerate models are byte-identical (the OpenSees eigenpairs are
  returned as the same objects); periods agree across solvers to 1e-10;
  participation sums are basis-invariant (full set = 1, X = Y by symmetry).
"""
import math
import warnings

import numpy as np
import openseespy.opensees as ops
import pytest

from skyframe.core.builder import quick_building
from skyframe.engine import eigen_refine as er
from skyframe.engine.opensees_engine import OpenSeesEngine, _cqc_matrix

from test_towers import _base, _cases, _podium, _tower, _twin

warnings.simplefilter("ignore")

SPEC = [[0.0, 0.4], [0.5, 1.0], [1.0, 0.6], [3.0, 0.2]]
SOLVERS = {"band": {}, "umfpack": {"solver": "umfpack"},
           "legacy": {"solver": "umfpack", "eigen_solver": "legacy"}}


def _square():
    return quick_building(bays_x=2, bays_y=2)


def _twin_square():
    m = _base("twsq", modes=12)
    _tower(m, "A", 0.0, bx=6.0, by=6.0)
    _tower(m, "B", 40.0, tower="T2", bx=6.0, by=6.0)
    m.towers = [{"name": "T1", "grid_system": "GA", "base_elevation": 0.0},
                {"name": "T2", "grid_system": "GB", "base_elevation": 0.0}]
    m.validate()
    return _cases(m)


def _model(mk, n, solver):
    m = mk()
    m.num_modes = n
    m.analysis_options = dict(SOLVERS[solver])
    m.add_rs_case("RSX", "X", SPEC)
    m.add_rs_case("RSXS", "X", SPEC, combo_method="SRSS")
    return m


# ------------------------------------------------ assembled K / M check
def _slaves(asm):
    out = set()
    for s in asm.masters:
        out.update(asm.story_nodes.get(s, ()))
    return out


def _eq_vector(asm, shape, neq, slaves):
    """Node shape -> reduced (constrained) equation vector.  A rigid-
    diaphragm slave lists its own free dofs (uz, rx, ry) first, then the
    master's equations (Transformation handler)."""
    x = np.zeros(neq)
    for t in asm.node_coords:
        ids = ops.nodeDOFs(t)
        if t in slaves and asm.use_transformation:
            pairs = zip((2, 3, 4), ids[:3])
        else:
            pairs = enumerate(ids)
        for d, q in pairs:
            if q >= 0:
                x[q] = shape[t][d]
    return x


def _assembled_check(eng, modal):
    """(max residual, max orthogonality error) with the assembled K, M."""
    asm = eng._build()
    ops.wipeAnalysis()
    ops.constraints("Transformation" if asm.use_transformation else "Plain")
    ops.numberer("Plain")
    ops.system("FullGeneral")
    ops.algorithm("Linear")
    mats = []
    for coef in ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)):
        ops.integrator("GimmeMCK", *coef)
        ops.analysis("Transient")
        ops.analyze(1, 0.0)           # forms the matrix (M-only solve fails)
        a = np.array(ops.printA("-ret"))
        neq = int(round(math.sqrt(len(a))))
        mats.append(a.reshape(neq, neq))
    K, M = mats
    sl = _slaves(asm)
    n = len(modal.periods)
    X = np.column_stack([_eq_vector(asm, modal.shapes[i], K.shape[0], sl)
                         for i in range(1, n + 1)])
    lam = [(2.0 * math.pi / T) ** 2 for T in modal.periods]
    res = max(np.linalg.norm(K @ X[:, i] - lam[i] * (M @ X[:, i]))
              / np.linalg.norm(K @ X[:, i]) for i in range(n))
    Mr = X.T @ M @ X
    d = np.sqrt(np.diag(Mr))
    orth = float(np.max(np.abs(Mr / np.outer(d, d) - np.eye(n))))
    return res, orth


CASES = [("square", _square, 12), ("square", _square, 8),
         ("twin", _twin, 12), ("twin", _twin, 8),
         ("twin_square", _twin_square, 12), ("twin_square", _twin_square, 8)]


@pytest.mark.parametrize("solver", list(SOLVERS))
@pytest.mark.parametrize("label,mk,n", CASES)
def test_residual_orthogonality_and_no_leakage(label, mk, n, solver):
    eng = OpenSeesEngine(_model(mk, n, solver))
    modal = eng.run_modal()
    assert len(modal.periods) == n
    for case in ("RSX", "RSXS"):
        r = eng.run_response_spectrum(case)
        ux = max(abs(v[0]) for v in r.node_disp.values())
        uy = max(abs(v[1]) for v in r.node_disp.values())
        assert uy / ux < 1e-8, (case, uy / ux)
        assert r.base["FY"] / r.base["FX"] < 1e-8
        for s in r.story.values():
            assert s["shear_y"] <= 1e-8 * r.base["FX"]
    res, orth = _assembled_check(eng, modal)
    assert res < 1e-10 and orth < 1e-10, (res, orth)


@pytest.mark.parametrize("mk,n", [(_square, 12), (_twin_square, 12),
                                  (_twin_square, 8)])
def test_periods_and_rs_agree_across_solvers(mk, n):
    out = {}
    for solver in SOLVERS:
        e = OpenSeesEngine(_model(mk, n, solver))
        out[solver] = (e.run_modal(), e.run_response_spectrum("RSX"),
                       e.run_response_spectrum("RSXS"))
    ref = out["umfpack"]
    for solver, (md, cqc, srss) in out.items():
        for a, b in zip(md.periods, ref[0].periods):
            assert a == pytest.approx(b, rel=1e-10)
        for got, want in ((cqc, ref[1]), (srss, ref[2])):
            assert got.base["FX"] == pytest.approx(want.base["FX"], rel=1e-8)
            for s, v in want.story.items():
                assert got.story[s]["ux"] == pytest.approx(v["ux"], rel=1e-8)
        # canonical basis: identical per-mode participation on every path
        for p, q in zip(md.participation, ref[0].participation):
            for key in ("ux", "uy", "rz"):
                assert p[key] == pytest.approx(q[key], abs=1e-9)


def test_canonical_pair_is_pure_x_then_pure_y():
    md = OpenSeesEngine(_model(_square, 12, "band")).run_modal()
    p1, p2 = md.participation[0], md.participation[1]
    assert md.periods[0] == pytest.approx(md.periods[1], rel=1e-12)
    assert p1["uy"] < 1e-12 and p2["ux"] < 1e-12
    assert p1["ux"] > 0.5 and p2["uy"] > 0.5


def test_participation_sums_basis_invariant():
    md = OpenSeesEngine(_model(_square, 12, "band")).run_modal()
    for key in ("ux", "uy", "rz"):         # all 12 modes = all massed dofs
        assert sum(p[key] for p in md.participation) == pytest.approx(
            1.0, abs=1e-12)
    md = OpenSeesEngine(_model(_twin_square, 12, "band")).run_modal()
    sx = sum(p["ux"] for p in md.participation)
    sy = sum(p["uy"] for p in md.participation)
    assert sx == pytest.approx(sy, rel=1e-12)          # X / Y symmetry


# ------------------------------------------- CQC rotation invariance
def _pair_responses(theta):
    """Per-mode X-excitation responses of the first (degenerate) pair of
    the square building, the pair basis rotated by ``theta``."""
    eng = OpenSeesEngine(_model(_square, 12, "band"))
    md = eng.run_modal()
    asm = eng._asm
    keys = sorted(asm.mass_map)
    m = np.array([asm.mass_map[k] for k in keys])
    Phi = np.array([[md.shapes[i][t][d - 1] for (t, d) in keys]
                    for i in (1, 2)]).T
    c, s = math.cos(theta), math.sin(theta)
    Phi = Phi @ np.array([[c, -s], [s, c]])
    rx = np.array([1.0 if d == 1 else 0.0 for (_t, d) in keys])
    resp = []
    for j in range(2):
        phi = Phi[:, j]
        gamma = (phi @ (m * rx)) / (phi @ (m * phi))
        resp.append(gamma * phi)                     # u_j = Gamma_j phi_j
    omega = 2.0 * math.pi / md.periods[0]
    return np.array(resp), [omega, omega * (1.0 + 1e-14)]


@pytest.mark.parametrize("theta", [0.0, 0.3, 1.1, 2.5])
def test_cqc_rotation_invariant_within_degenerate_pair(theta):
    r0, om = _pair_responses(0.0)
    rt, _ = _pair_responses(theta)
    rho = _cqc_matrix(om, 0.05)
    assert rho[0, 1] == pytest.approx(1.0, abs=1e-12)

    def cqc(r):
        return np.sqrt(np.maximum(np.einsum("iq,ij,jq->q", r, rho, r), 0.0))

    def srss(r):
        return np.sqrt(np.sum(r * r, axis=0))

    scale = np.max(np.abs(cqc(r0)))
    assert np.max(np.abs(cqc(rt) - cqc(r0))) <= 1e-10 * scale
    if theta not in (0.0,):
        # SRSS treats the pair as uncorrelated: basis-dependent (why the
        # engine uses the canonical basis)
        assert np.max(np.abs(srss(rt) - srss(r0))) > 1e-3 * scale


# ------------------------------------------------ byte identity / units
def test_non_degenerate_models_untouched(monkeypatch):
    seen = []
    real = er.verify

    def spy(eng, asm, lambdas, shapes):
        out = real(eng, asm, lambdas, shapes)
        seen.append(out[0] is lambdas and out[1] is shapes)
        return out
    monkeypatch.setattr(er, "verify", spy)
    # 4 stories: n == n_massed -> -fullGenLapack; 10 stories forced onto
    # the band path: n < n_massed -> Arpack
    for mk, n, opts in ((quick_building, 12, {}),
                        (lambda: quick_building(stories=10), 9,
                         {"solver": "band"})):
        m = mk()
        m.num_modes = n
        m.analysis_options = opts
        e = OpenSeesEngine(m)
        e.run_modal()
        assert e._eigen_check["refined"] is False
        assert e._eigen_check["residual"] < 1e-10
        assert e._eigen_check["orthogonality"] < 1e-10
    assert seen == [True, True]


def test_lapack_degenerate_pair_needed_the_cleanup():
    """The root cause, pinned: -fullGenLapack returns a non-M-orthogonal
    basis of the square building's degenerate pair (fixed by refine)."""
    eng = OpenSeesEngine(_model(_square, 12, "band"))
    eng.run_modal()
    assert eng._eigen_check["refined"] is True
    asm = eng._build()
    eng._setup_analysis(asm)
    vals = ops.eigen("-fullGenLapack", 2)
    dofs = sorted(asm.mass_map)
    m = np.array([asm.mass_map[k] for k in dofs])
    Phi = np.array([[ops.nodeEigenvector(t, i)[d - 1] for (t, d) in dofs]
                    for i in (1, 2)]).T
    assert vals[0] == pytest.approx(vals[1], rel=1e-12)
    assert er.orthogonality_error(Phi, m) > 1e-6


def test_clusters_and_cluster_end():
    assert er.clusters([1.0, 2.0, 3.0]) == []
    assert er.clusters([1.0, 1.0 + 1e-14, 2.0, 3.0, 3.0, 3.0]) == [
        [0, 1], [3, 4, 5]]
    assert er.clusters([1.0, 1.0 + 1e-6]) == []
    lams = [1.0, 2.0, 2.0, 2.0, 5.0]
    assert er.cluster_end(lams, 1) == 1
    assert er.cluster_end(lams, 2) == 4
    assert er.cluster_end(lams, 4) == 4
    assert er.cluster_end(lams, 5) == 5


def test_align_basis_hand_example():
    """Two equal-mass dofs (x, y) with a 45-degree basis -> pure x, pure y."""
    dofs = [(1, 1), (1, 2)]
    m = np.array([2.0, 2.0])
    c = 1.0 / math.sqrt(2.0)
    Phi = np.array([[c, -c], [c, c]]) / math.sqrt(2.0)   # M-orthonormal
    T = er.align_basis(Phi, m, dofs)
    B = Phi @ T
    assert B[:, 0] == pytest.approx([1.0 / math.sqrt(2.0), 0.0], abs=1e-15)
    assert B[:, 1] == pytest.approx([0.0, 1.0 / math.sqrt(2.0)], abs=1e-15)


# --------------------------------------------- pushover control height
def test_pushover_control_height_is_tower_height():
    m = _podium()
    m.add_pushover_case("PT1", "X", steps=2, target_drift=0.001,
                        control_tower="T1")
    m.add_pushover_case("PT2", "X", steps=2, target_drift=0.001,
                        control_tower="T2")
    m.add_pushover_case("PP", "X", steps=2, target_drift=0.001,
                        control_tower="P")
    m.add_pushover_case("DEF", "X", steps=2, target_drift=0.001)
    eng = OpenSeesEngine(m)
    # A3 / B2 at 15 / 12 m on towers based at 6 m; podium P2 at 6 m
    for name, h in (("PT1", 9.0), ("PT2", 6.0), ("PP", 6.0), ("DEF", 6.0)):
        po = eng.run_pushover(name)
        assert po.control["height"] == pytest.approx(h, rel=1e-12)
        assert po.roof_disp[-1] == pytest.approx(0.001 * h, rel=1e-9)
        assert po.roof_drift[-1] == pytest.approx(0.001, rel=1e-9)


def test_pushover_height_unchanged_without_raised_towers():
    m = quick_building(stories=3)
    m.add_pushover_case("PO", "X", steps=2, target_drift=0.002)
    po = OpenSeesEngine(m).run_pushover("PO")
    assert po.control["height"] == m.stories[-1].elevation
    m = _twin()                                    # both towers at base 0
    m.add_pushover_case("PO", "X", steps=2, target_drift=0.002,
                        control_tower="T1")
    m.add_pushover_case("PD", "X", steps=2, target_drift=0.002)
    eng = OpenSeesEngine(m)
    assert eng.run_pushover("PO").control["height"] == 9.0
    assert eng.run_pushover("PD").control["height"] == 9.0
