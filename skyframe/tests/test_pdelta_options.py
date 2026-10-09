"""Model-wide P-Delta options (ETABS Define > P-Delta Options).

Every expected number is derived here from first principles:

* Cantilever, Euler element, tip axial P (compression) + tip lateral H.
  The engine's geometric stiffness is the linearized "string" term
  ``-P/L`` on the relative transverse translation of each frame element
  (exactly OpenSees' PDelta geomTransf), so a one-element cantilever has
  lateral stiffness ``k = 3EI/L^3 - P/L = k0 (1 - P/Pcr_s)`` with the
  string critical load ``Pcr_s = 3EI/L^2`` and a small lateral load is
  amplified by ``1/(1 - P/Pcr_s)``.  With the column split into n
  elements the string model converges to the EXACT stability-function
  result ``u/u0 = 3 (tan(kL) - kL)/(kL)^3`` (k = sqrt(P/EI)), which is
  itself ~``1/(1 - P/Pe)`` with Euler's ``Pe = pi^2 EI/(4 L^2)``.
* SDOF modal: tip mass m on the column, ``T = T0/sqrt(1 - P/Pcr_s)`` with
  ``P = m g`` (non_iterative_mass: the hand story spring -P/h).
* Two-story rigid-diaphragm frame: the story-spring matrix
  ``Kg = [[P1/h1 + P2/h2, -P2/h2], [-P2/h2, P2/h2]]`` subtracted from the
  condensed lateral stiffness (inverse of the engine's own flexibility
  under unit story forces) gives the hand periods.
* RS: SDOF base shear ``m * Sa(T_pd) * g`` at the LENGTHENED period.

Units: kN, m, tonne, s; E in kPa.
"""

import json
import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.frequency_cases import add_steady_state_case
from skyframe.core.model import (G_ACCEL, BuildingModel, FrameSection,
                                 Material, NodalLoad, NodalMass,
                                 PointSupport, StoryForce)
from skyframe.core.pdelta_options import pdelta_defaults

E_CONC = 25_000_000.0      # kPa
SIZE = 0.3
I_COL = SIZE ** 4 / 12.0
EI = E_CONC * I_COL


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _column(L=3.0, n=1, P=0.0, m=10.0, H=1.0, method="none",
            load_factors=None, extra_case=False):
    """Cantilever of n equal elements (one per story), tip mass m (ux,uy),
    pattern G = tip axial -P, pattern H = tip lateral +H (X)."""
    mdl = BuildingModel(name="col")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories([L / n] * n)
    mdl.add_section(FrameSection.rectangular("COL", "CONC", SIZE, SIZE))
    for i in range(n):
        mdl.add_member("column", "COL", (0, 0, i * L / n),
                       (0, 0, (i + 1) * L / n), story=f"Story{i + 1}",
                       uid=f"C{i + 1}")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    if m:
        mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m, my=m))
    mdl.pattern("G", "dead").nodal_loads.append(NodalLoad((0, 0, L),
                                                          fz=-P))
    mdl.pattern("H", "other").nodal_loads.append(NodalLoad((0, 0, L),
                                                           fx=H))
    mdl.add_case("G", {"G": 1.0})
    mdl.add_case("H", {"H": 1.0})
    if extra_case:
        mdl.add_case("HPD", {"H": 1.0}, pdelta=True,
                     pdelta_gravity={"G": 1.0})
    mdl.num_modes = 1
    if method != "none":
        mdl.pdelta_options = {"method": method,
                              "load_factors": (load_factors
                                               if load_factors is not None
                                               else {"G": 1.0})}
    mdl.validate()
    return mdl


def _tip(eng, L):
    return next(t for t, c in eng._asm.node_coords.items()
                if abs(c[2] - L) < 1e-9 and abs(c[0]) < 1e-9
                and abs(c[1]) < 1e-9)


def _frame(heights=(3.0,), masses=(20.0,), method="none", bay=6.0,
           gravity_per_col=None, bay_y=None):
    """Square 1x1-bay rigid-diaphragm frame (4 columns per story, base
    fixed), explicit story masses; pattern G = per-column-top axial loads,
    pattern EX = unit story forces in X."""
    mdl = BuildingModel(name="frame")
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories(list(heights))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.3, 0.6))
    by = bay if bay_y is None else bay_y
    corners = [(0, 0), (bay, 0), (bay, by), (0, by)]
    z0 = 0.0
    for k, s in enumerate(mdl.stories):
        z1 = s.elevation
        for j, (x, y) in enumerate(corners):
            mdl.add_member("column", "COL", (x, y, z0), (x, y, z1),
                           story=s.name, uid=f"C{k}{j}")
        for j in range(4):
            (xa, ya), (xb, yb) = corners[j], corners[(j + 1) % 4]
            mdl.add_member("beam", "BM", (xa, ya, z1), (xb, yb, z1),
                           story=s.name, uid=f"B{k}{j}")
        z0 = z1
    mdl.story_masses = {s.name: float(mm)
                        for s, mm in zip(mdl.stories, masses)}
    g = mdl.pattern("G", "dead")
    for s, mm in zip(mdl.stories, masses):
        per = (gravity_per_col if gravity_per_col is not None
               else mm * G_ACCEL / 4.0)
        for (x, y) in corners:
            g.nodal_loads.append(NodalLoad((x, y, s.elevation), fz=-per))
    ex = mdl.pattern("EX", "other")
    for s in mdl.stories:
        ex.story_forces.append(StoryForce(s.name, fx=1.0))
    mdl.add_case("G", {"G": 1.0})
    mdl.add_case("EX", {"EX": 1.0})
    mdl.num_modes = 3 * len(heights)
    if method != "none":
        mdl.pdelta_options = {"method": method, "load_factors": {"G": 1.0}}
    mdl.validate()
    return mdl


def _results_json(mdl) -> str:
    return json.dumps(OpenSeesEngine(mdl).run().to_dict(), sort_keys=True)


# --------------------------------------------------------------------------- #
# defaults / byte-identical "none"
# --------------------------------------------------------------------------- #
def test_defaults_and_no_key_in_to_dict():
    mdl = BuildingModel()
    assert mdl.pdelta_options == pdelta_defaults()
    assert pdelta_defaults() == {"method": "none", "load_factors": {},
                                 "max_iterations": 2, "tolerance": 0.001,
                                 "include_in": "all_linear"}
    assert "pdelta_options" not in mdl.to_dict()
    back = BuildingModel.from_dict(mdl.to_dict())
    assert back.pdelta_options == pdelta_defaults()


def test_none_is_byte_identical_full_run():
    """A default model and the same model with the defaults set explicitly
    (and round-tripped) produce byte-identical results, with every linear
    analysis kind present (static, combo, modal, RS, TH, P-Delta case)."""
    def build():
        mdl = _frame(heights=(3.0, 3.0), masses=(20.0, 15.0))
        mdl.add_case("EXPD", {"EX": 1.0}, pdelta=True,
                     pdelta_gravity={"G": 1.0})
        mdl.add_combo("C1", {"G": 1.2, "EX": 1.0})
        mdl.add_rs_case("RSX", "X", [[0.0, 0.5], [1.0, 0.5], [3.0, 0.1]])
        mdl.add_th_case("THX", "X", accel=[0.0, 0.1, -0.2, 0.15, 0.0] * 4,
                        dt=0.02)
        return mdl
    a = build()
    b = build()
    b.pdelta_options = dict(pdelta_defaults())
    b = BuildingModel.from_dict(b.to_dict())
    ja, jb = _results_json(a), _results_json(b)
    assert ja == jb
    assert '"pdelta"' not in ja


def test_round_trip_non_default():
    mdl = _column(P=100.0, method="iterative_loads")
    mdl.pdelta_options.update(max_iterations=5, tolerance=1e-4)
    d = mdl.to_dict()
    assert d["pdelta_options"] == {"method": "iterative_loads",
                                   "load_factors": {"G": 1.0},
                                   "max_iterations": 5, "tolerance": 1e-4,
                                   "include_in": "all_linear"}
    back = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert back.pdelta_options == d["pdelta_options"]
    assert back.to_dict() == d


@pytest.mark.parametrize("opts, msg", [
    ({"method": "bogus"}, "method must be one of"),
    ({"method": "iterative_loads"}, "non-empty load_factors"),
    ({"method": "iterative_loads", "load_factors": {"NOPE": 1.0}},
     "unknown load pattern"),
    ({"method": "iterative_loads", "load_factors": {"G": float("nan")}},
     "finite number"),
    ({"method": "non_iterative_mass", "max_iterations": 0},
     "max_iterations"),
    ({"method": "non_iterative_mass", "max_iterations": True},
     "max_iterations"),
    ({"method": "non_iterative_mass", "tolerance": 0.0}, "tolerance"),
    ({"method": "non_iterative_mass", "include_in": "modal"}, "include_in"),
    ({"method": "none", "extra": 1}, "unknown keys"),
])
def test_validation_errors(opts, msg):
    mdl = _column(P=10.0)
    mdl.pdelta_options = opts
    with pytest.raises(ValueError, match=msg):
        mdl.validate()
    d = _column(P=10.0).to_dict()
    d["pdelta_options"] = opts
    with pytest.raises(ValueError, match=msg):
        BuildingModel.from_dict(d)


def test_from_dict_rejects_non_object():
    d = _column(P=10.0).to_dict()
    d["pdelta_options"] = "iterative_loads"
    with pytest.raises(ValueError, match="must be an object"):
        BuildingModel.from_dict(d)


# --------------------------------------------------------------------------- #
# cantilever statics
# --------------------------------------------------------------------------- #
def test_iterative_lateral_stiffness_k_minus_P_over_L():
    L = 3.0
    k0 = 3.0 * EI / L ** 3
    Pcr_s = 3.0 * EI / L ** 2
    P = 0.4 * Pcr_s
    eng = OpenSeesEngine(_column(L=L, P=P, method="iterative_loads",
                                 extra_case=True))
    u = eng.run_static("H").node_disp[_tip(eng, L)][0]
    assert 1.0 / u == pytest.approx(k0 - P / L, rel=1e-9)
    assert 1.0 / u == pytest.approx(k0 * (1.0 - P / Pcr_s), rel=1e-9)
    # identical to the per-case P-Delta two-stage (PDelta geomTransf) flow
    u_case = eng.run_static("HPD").node_disp[_tip(eng, L)][0]
    assert u == pytest.approx(u_case, rel=1e-9)


def test_amplification_single_element_and_converged_to_exact():
    """Single element: amplification exactly 1/(1 - P/Pcr_s).  Eight
    elements: within 1% of the exact stability-function amplification
    (and of Euler's 1/(1 - P/Pe)), at P = 0.3 Pe."""
    L = 6.0
    Pe = math.pi ** 2 * EI / (4.0 * L ** 2)
    P = 0.3 * Pe
    amp = {}
    for n in (1, 8):
        lin = OpenSeesEngine(_column(L=L, n=n, P=P))
        pd = OpenSeesEngine(_column(L=L, n=n, P=P,
                                    method="iterative_loads"))
        amp[n] = (pd.run_static("H").node_disp[_tip(pd, L)][0]
                  / lin.run_static("H").node_disp[_tip(lin, L)][0])
    assert amp[1] == pytest.approx(1.0 / (1.0 - P * L ** 2 / (3.0 * EI)),
                                   rel=1e-9)
    u = math.sqrt(P / EI) * L
    exact = 3.0 * (math.tan(u) - u) / u ** 3
    assert amp[8] == pytest.approx(exact, rel=0.01)
    assert amp[8] == pytest.approx(1.0 / (1.0 - P / Pe), rel=0.01)
    assert amp[1] < amp[8] < exact * 1.001       # converges from below


def test_static_base_shear_equals_applied_load():
    """The geometric stiffness is internal: base reactions still balance
    the applied lateral load (string/story-spring forces land in the
    support reactions)."""
    for method in ("iterative_loads", "non_iterative_mass"):
        mdl = _frame(heights=(3.0, 3.0), masses=(20.0, 15.0), method=method)
        res = OpenSeesEngine(mdl).run_static("EX")
        assert res.base["FX"] == pytest.approx(-2.0, rel=1e-9)


# --------------------------------------------------------------------------- #
# modal
# --------------------------------------------------------------------------- #
def test_modal_period_mass_method_vs_hand_spring():
    L, m = 3.0, 10.0
    k0 = 3.0 * EI / L ** 3
    T0 = 2.0 * math.pi * math.sqrt(m / k0)
    P = m * G_ACCEL
    Pcr_s = 3.0 * EI / L ** 2
    eng0 = OpenSeesEngine(_column(L=L, m=m))
    assert eng0.run_modal().periods[0] == pytest.approx(T0, rel=1e-9)
    eng = OpenSeesEngine(_column(L=L, m=m, method="non_iterative_mass"))
    T = eng.run_modal().periods[0]
    assert T == pytest.approx(2 * math.pi * math.sqrt(m / (k0 - P / L)),
                              rel=1e-9)
    assert T == pytest.approx(T0 / math.sqrt(1.0 - P / Pcr_s), rel=1e-9)
    assert T > T0


def test_modal_period_heavy_axial_iterative():
    """iterative_loads with P well above the weight: T0/sqrt(1-P/Pcr_s)."""
    L, m = 3.0, 10.0
    Pcr_s = 3.0 * EI / L ** 2
    P = 0.6 * Pcr_s
    k0 = 3.0 * EI / L ** 3
    T0 = 2.0 * math.pi * math.sqrt(m / k0)
    eng = OpenSeesEngine(_column(L=L, m=m, P=P, method="iterative_loads"))
    assert eng.run_modal().periods[0] == pytest.approx(
        T0 / math.sqrt(1.0 - P / Pcr_s), rel=1e-9)


def test_iterative_equals_mass_when_gravity_is_weight_column():
    L, m = 3.0, 10.0
    a = OpenSeesEngine(_column(L=L, m=m, P=m * G_ACCEL,
                               method="iterative_loads"))
    b = OpenSeesEngine(_column(L=L, m=m, P=m * G_ACCEL,
                               method="non_iterative_mass"))
    assert a.run_modal().periods[0] == pytest.approx(
        b.run_modal().periods[0], rel=1e-9)
    ua = a.run_static("H").node_disp[_tip(a, L)][0]
    ub = b.run_static("H").node_disp[_tip(b, L)][0]
    assert ua == pytest.approx(ub, rel=1e-9)


def test_iterative_equals_mass_diaphragm_frame_translational_modes():
    """4-column rigid-diaphragm frame, column-top gravity = story mass*g:
    the per-column strings (iterative) sum to the master story spring
    (mass) for the translational modes (torsional P-Delta is carried only
    by the member strings, so the RZ mode is not compared)."""
    def trans_periods(method):
        res = OpenSeesEngine(_frame(heights=(3.0, 3.0), masses=(20.0, 15.0),
                                    method=method)).run_modal()
        # X/Y modes are degenerate (square plan): select by "not torsion"
        return sorted(p["T"] for p in res.participation
                      if p["ux"] + p["uy"] > p["rz"])
    ti = trans_periods("iterative_loads")
    tm = trans_periods("non_iterative_mass")
    t0 = trans_periods("none")
    assert len(ti) == len(tm) == 4
    assert ti == pytest.approx(tm, rel=1e-6)
    assert all(a > b for a, b in zip(tm, t0))


def test_story_spring_two_story_vs_condensed_hand_model():
    """non_iterative_mass on a 2-story diaphragm frame vs the hand
    shear-building model: K (from the engine's own unit-story-force
    flexibility, method none) minus the story-spring Kg."""
    h, m1, m2 = 3.0, 20.0, 15.0
    base = _frame(heights=(h, h), masses=(m1, m2), bay_y=4.0)
    for s in base.stories:
        p = base.pattern(f"U{s.name}", "other")
        p.story_forces.append(StoryForce(s.name, fx=1.0))
        base.add_case(f"U{s.name}", {f"U{s.name}": 1.0})
    eng = OpenSeesEngine(base)
    names = [s.name for s in base.stories]
    F = np.zeros((2, 2))
    for j, sj in enumerate(names):
        cr = eng.run_static(f"U{sj}")
        for i, si in enumerate(names):
            F[i, j] = cr.node_disp[eng._asm.masters[si]][0]
    K = np.linalg.inv(0.5 * (F + F.T))
    P1, P2 = G_ACCEL * (m1 + m2), G_ACCEL * m2
    Kg = np.array([[P1 / h + P2 / h, -P2 / h], [-P2 / h, P2 / h]])
    M = np.diag([m1, m2])
    lam = np.sort(np.linalg.eigvals(np.linalg.solve(M, K - Kg)).real)
    T_hand = sorted(2 * math.pi / np.sqrt(lam))[::-1]

    mdl = _frame(heights=(h, h), masses=(m1, m2), method="non_iterative_mass",
                 bay_y=4.0)
    res = OpenSeesEngine(mdl).run()
    Tx = sorted((p["T"] for p in res.modal.participation
                 if p["ux"] > max(p["uy"], p["rz"])),
                reverse=True)
    assert Tx == pytest.approx(T_hand, rel=1e-6)
    assert res.pdelta["story_P"] == pytest.approx(
        {"Story1": P1, "Story2": P2}, rel=1e-12)


# --------------------------------------------------------------------------- #
# RS / TH / frequency domain
# --------------------------------------------------------------------------- #
def test_rs_base_shear_uses_lengthened_period():
    L, m = 3.0, 10.0
    P = 0.5 * 3.0 * EI / L ** 2
    spec = [[0.0, 1.0], [2.0, 0.2]]

    def sa(T):
        return float(np.interp(T, [0.0, 2.0], [1.0, 0.2]))
    out = {}
    for method in ("none", "iterative_loads"):
        mdl = _column(L=L, m=m, P=P, method=method)
        mdl.nodal_masses[0].my = 0.0
        mdl.add_rs_case("RSX", "X", spec, num_modes=1)
        res = OpenSeesEngine(mdl).run()
        T = res.modal.periods[0]
        out[method] = (T, res.rs_cases["RSX"].base["FX"])
        assert abs(out[method][1]) == pytest.approx(m * sa(T) * G_ACCEL,
                                                    rel=1e-9)
    T0, Tpd = out["none"][0], out["iterative_loads"][0]
    assert Tpd == pytest.approx(T0 / math.sqrt(0.5), rel=1e-9)
    assert abs(out["iterative_loads"][1]) < abs(out["none"][1])


def test_linear_th_equals_reduced_stiffness_column():
    """Linear TH on K + Kg equals a plain column whose E is reduced so
    3EI'/L^3 = 3EI/L^3 - P/L (same SDOF k and m, same Rayleigh fit)."""
    L, m = 3.0, 10.0
    P = 0.3 * 3.0 * EI / L ** 2
    rec = [0.0, 0.2, 0.5, 0.1, -0.4, -0.3, 0.2, 0.0] * 5

    def th(mdl):
        mdl.nodal_masses[0].my = 0.0
        mdl.add_th_case("THX", "X", accel=rec, dt=0.01, damping=0.05)
        return OpenSeesEngine(mdl).run_time_history("THX")
    a = th(_column(L=L, m=m, P=P, method="iterative_loads"))
    red = _column(L=L, m=m, P=P)
    red.materials["CONC"].E = E_CONC * (1.0 - P * L ** 2 / (3.0 * EI))
    b = th(red)
    ua, ub = np.array(a.story_ux["Story1"]), np.array(b.story_ux["Story1"])
    assert np.max(np.abs(ua)) > 0
    assert np.allclose(ua, ub, rtol=1e-6, atol=1e-12 * np.max(np.abs(ub)))


def test_steady_state_static_limit():
    L, m = 3.0, 10.0
    P = 0.25 * 3.0 * EI / L ** 2
    k = 3.0 * EI / L ** 3 - P / L
    mdl = _column(L=L, m=m, P=P, method="iterative_loads")
    mdl.nodal_masses[0].my = 0.0          # single (X) mode, no degeneracy
    add_steady_state_case(mdl, "SS", [{"pattern": "H"}], freq_start_hz=0.0,
                          freq_end_hz=1.0, n_freq=3, damping=0.05)
    eng = OpenSeesEngine(mdl)
    r = eng.run_steady_state("SS")
    tip = _tip(eng, L)
    assert r["frequencies_hz"][0] == 0.0
    assert r["node_disp"][str(tip)]["amp"][0][0] == pytest.approx(1.0 / k,
                                                             rel=1e-6)


# --------------------------------------------------------------------------- #
# precedence, iteration, summary
# --------------------------------------------------------------------------- #
def test_per_case_pdelta_flag_keeps_its_own_flow():
    """A case with its own pdelta flag runs the unchanged nonlinear
    PDelta-transform flow (no double counting) — identical to method
    none — and is listed in the results summary."""
    L, P = 3.0, 200.0
    a = OpenSeesEngine(_column(L=L, P=P, extra_case=True)).run()
    b = OpenSeesEngine(_column(L=L, P=P, extra_case=True,
                               method="iterative_loads")).run()
    assert (json.dumps(a.cases["HPD"].to_dict(), sort_keys=True)
            == json.dumps(b.cases["HPD"].to_dict(), sort_keys=True))
    assert b.pdelta["cases_own_geometric"] == ["HPD"]
    assert a.to_dict().get("pdelta") is None


def _portal(max_iter, tol):
    """Unsymmetric portal (different column sizes + eccentric gravity):
    gravity sways, so P-Delta changes the column axials -> iteration."""
    mdl = BuildingModel(name="portal")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories([4.0])
    mdl.add_section(FrameSection.rectangular("C1", "CONC", 0.25, 0.25))
    mdl.add_section(FrameSection.rectangular("C2", "CONC", 0.45, 0.45))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.3, 0.5))
    mdl.add_member("column", "C1", (0, 0, 0), (0, 0, 4), story="Story1",
                   uid="CA")
    mdl.add_member("column", "C2", (6, 0, 0), (6, 0, 4), story="Story1",
                   uid="CB")
    mdl.add_member("beam", "BM", (0, 0, 4), (6, 0, 4), story="Story1",
                   uid="B")
    g = mdl.pattern("G", "dead")
    g.nodal_loads.append(NodalLoad((0, 0, 4), fx=40.0, fz=-1500.0))
    g.nodal_loads.append(NodalLoad((6, 0, 4), fz=-300.0))
    mdl.add_case("G", {"G": 1.0})
    mdl.pdelta_options = {"method": "iterative_loads",
                          "load_factors": {"G": 1.0},
                          "max_iterations": max_iter, "tolerance": tol}
    mdl.validate()
    return mdl


def test_iteration_history_converges():
    eng = OpenSeesEngine(_portal(20, 1e-10))
    eng.run_static("G")
    info = engine_mod._pdelta.info(eng)
    hist = info["relative_change"]
    assert info["converged"] is True
    assert 3 <= info["iterations"] <= 20
    assert hist[-1] <= 1e-10
    assert all(b < a for a, b in zip(hist, hist[1:]))     # monotone


def test_max_iterations_cap_and_single_pass():
    eng = OpenSeesEngine(_portal(2, 1e-14))
    eng.run_static("G")
    info = engine_mod._pdelta.info(eng)
    assert info["iterations"] == 2 and info["converged"] is False
    assert len(info["relative_change"]) == 1
    eng1 = OpenSeesEngine(_portal(1, 1e-3))
    eng1.run_static("G")
    info1 = engine_mod._pdelta.info(eng1)
    assert info1["iterations"] == 1 and info1["relative_change"] == []


def test_results_summary_keys():
    res = OpenSeesEngine(_column(P=50.0, method="non_iterative_mass")
                         ).run().to_dict()
    pd = res["pdelta"]
    assert pd["method"] == "non_iterative_mass"
    assert pd["n_springs"] == 1
    assert pd["story_P"]["Story1"] == pytest.approx(10.0 * G_ACCEL)
    json.dumps(pd)
    res2 = OpenSeesEngine(_column(P=50.0, method="iterative_loads")
                          ).run().to_dict()["pdelta"]
    assert res2["iterations"] == 2 and res2["converged"] is True
    assert res2["load_factors"] == {"G": 1.0}


def test_combo_superposes_kg_cases_linearly():
    mdl = _frame(heights=(3.0, 3.0), masses=(20.0, 15.0),
                 method="non_iterative_mass")
    mdl.add_combo("C", {"G": 1.2, "EX": 2.0})
    res = OpenSeesEngine(mdl).run()
    c, g, e = res.combos["C"], res.cases["G"], res.cases["EX"]
    assert "warning" not in c.to_dict()
    for t, d in c.node_disp.items():
        assert d[0] == pytest.approx(1.2 * g.node_disp[t][0]
                                     + 2.0 * e.node_disp[t][0],
                                     rel=1e-9, abs=1e-15)


def test_buckling_case_unaffected():
    """Linear buckling is its own K + lambda*Kg eigenproblem (numpy) and is
    documented as unaffected by the model-wide option."""
    def run(method):
        mdl = _column(P=100.0, method=method)
        mdl.add_buckling_case("BK", {"G": 1.0}, num_modes=1)
        return OpenSeesEngine(mdl).run_buckling("BK").to_dict()
    assert (json.dumps(run("none"), sort_keys=True)
            == json.dumps(run("iterative_loads"), sort_keys=True))


def test_api_model_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    mdl = _column(P=50.0, method="iterative_loads")
    with app.test_client() as client:
        r = client.post("/api/model", json=mdl.to_dict())
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["pdelta_options"]["method"] == "iterative_loads"
        bad = mdl.to_dict()
        bad["pdelta_options"] = {"method": "nope"}
        assert client.post("/api/model", json=bad).status_code == 400


# --------------------------------------------------------------------------- #
# whole building
# --------------------------------------------------------------------------- #
def _quick(method):
    from skyframe.core.builder import quick_building
    mdl = quick_building(name="Q", bays_x=2, bays_y=1, stories=3)
    mdl.num_modes = 6
    mdl.add_rs_case("RSX", "X", [[0.0, 0.5], [0.5, 0.5], [3.0, 0.1]],
                    num_modes=6)
    mdl.add_pushover_case("PO", "X", gravity={"DEAD": 1.0},
                          target_drift=0.005, steps=10)
    if method != "none":
        mdl.pdelta_options = {"method": method,
                              "load_factors": {"DEAD": 1.0, "LIVE": 0.25}}
    mdl.validate()
    return mdl


def test_quick_building_all_methods_run_and_lengthen_periods():
    res = {m: OpenSeesEngine(_quick(m)).run()
           for m in ("none", "non_iterative_mass", "iterative_loads")}
    for m, r in res.items():
        assert set(r.case_status.values()) == {"finished"}, \
            (m, r.case_status)
    T0 = res["none"].modal.periods
    top = res["none"].story_order[-1]
    for m in ("non_iterative_mass", "iterative_loads"):
        T = res[m].modal.periods
        assert T[0] > T0[0] * 1.001
        assert all(a >= b * (1 - 1e-9) for a, b in zip(sorted(T),
                                                         sorted(T0)))
        # P-Delta amplifies the EQX roof displacement
        assert (abs(res[m].cases["EQX"].story[top]["ux"])
                > abs(res["none"].cases["EQX"].story[top]["ux"]))
    # pushover (hinged nonlinear build) is not affected by the option
    assert (json.dumps(res["iterative_loads"].pushover["PO"].to_dict(),
                       sort_keys=True)
            == json.dumps(res["none"].pushover["PO"].to_dict(),
                          sort_keys=True))
