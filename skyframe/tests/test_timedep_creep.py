"""Time-dependent material laws, step-by-step staged creep and bonded
tendon elements (CONTRACT "Time-dependent materials, incremental creep and
tendon elements").

Every expected value is written out from the code equations / hand
mechanics in the test itself:

* fib MC2010 (5.1-63 .. 5.1-81), ACI 209R-92 (2-1, 2-8 .. 2-23), GL2000
  creep / shrinkage / ageing at given t, t0, h0, RH;
* EN 1992-1-1 3.3.2 and Magura relaxation;
* column under constant load: eps(t) = sigma J(t, t0)
  (= sigma/E(t0) (1 + phi) for ACI 209 / user curves), any step count;
* two-stage column with ageing, loading-age dependent compliance and
  shrinkage — superposition of the age-adjusted compliances (determinate);
* composite (concrete + steel) column — age-adjusted effective modulus
  closed form, chi = 0.8 (tolerance: the AAEM chi is itself approximate);
* straight eccentric bonded tendon in a simply supported beam: the
  section equations give P_eff and the camber P_eff e L^2/(8 EI); the
  equivalent-load model with the same force gives the same camber;
* losses: friction exp(-(mu alpha + k x)), anchor set sqrt(delta Ep Ap /
  p), elastic shortening (Ep/Ec) f_cgp, relaxation dP_r/(1 + Ep Ap f).
"""

import copy
import json
import math

import numpy as np
import pytest

pytest.importorskip("openseespy")

from skyframe.core import timedep as TD  # noqa: E402
from skyframe.core import tendon_elements as TE  # noqa: E402
from skyframe.core import tendons as T  # noqa: E402
from skyframe.core.model import (BuildingModel, FrameSection,  # noqa: E402
                                 LoadPattern, Material, NodalLoad,
                                 PointSupport)
from skyframe.engine.opensees_engine import OpenSeesEngine  # noqa: E402

E_C = 30e6
FC = 30_000.0


# --------------------------------------------------------------------------- #
# 1. material laws pinned to the code equations
# --------------------------------------------------------------------------- #
def test_mc2010_creep_hand():
    p = TD.normalize_props({"law": "cebfip2010", "rh": 60.0, "h0": 0.2,
                            "cement": "N", "fcm": 38_000.0})
    t0, t = 28.0, 28.0 + 1000.0
    fcm, h, rh = 38.0, 200.0, 60.0
    t0a = t0 * (9.0 / (2.0 + t0 ** 1.2) + 1.0) ** 0.0          # alpha = 0
    b_bc = 1.8 / fcm ** 0.7 * math.log((30.0 / t0a + 0.035) ** 2
                                       * (t - t0) + 1.0)
    a = math.sqrt(35.0 / fcm)
    bh = min(1.5 * h + 250.0 * a, 1500.0 * a)
    gam = 1.0 / (2.3 + 3.5 / math.sqrt(t0a))
    b_dc = (412.0 / fcm ** 1.4 * (1 - rh / 100) / (0.1 * h) ** (1 / 3)
            / (0.1 + t0a ** 0.2) * ((t - t0) / (bh + t - t0)) ** gam)
    assert TD.creep_coefficient(p, t, t0) == pytest.approx(b_bc + b_dc,
                                                           rel=1e-12)
    # cement class R: adjusted loading age (alpha = 1) at t0 = 7
    pr = TD.normalize_props({"law": "cebfip2010", "cement": "R"})
    assert TD.mc2010_t0_adj(pr, 7.0) == pytest.approx(
        7.0 * (9.0 / (2.0 + 7.0 ** 1.2) + 1.0), rel=1e-12)
    # compliance J = 1/Eci(t0) + phi/Eci with beta_E = sqrt(beta_cc)
    t0 = 7.0
    bcc = math.exp(0.5 * (1.0 - math.sqrt(28.0 / t0)))
    J = TD.compliance(p, E_C, 100.0, t0)
    assert J == pytest.approx(1.0 / (E_C * math.sqrt(bcc))
                              + TD.creep_coefficient(p, 100.0, t0) / E_C,
                              rel=1e-12)


def test_mc2010_shrinkage_hand():
    p = TD.normalize_props({"law": "cebfip2010", "rh": 60.0, "h0": 0.2,
                            "cement": "N", "fcm": 38_000.0,
                            "cure_days": 7.0})
    t, ts, fcm, h = 365.0, 7.0, 38.0, 200.0
    e_as = 700.0 * (3.8 / 9.8) ** 2.5 * 1e-6 * (1 - math.exp(-0.2 * math.sqrt(t)))
    e_ds0 = (220.0 + 110.0 * 4.0) * math.exp(-0.012 * fcm) * 1e-6
    b_rh = -1.55 * (1 - 0.6 ** 3)
    b_ds = math.sqrt((t - ts) / (0.035 * h * h + t - ts))
    assert TD.shrinkage_strain(p, t) == pytest.approx(
        e_as - e_ds0 * b_rh * b_ds, rel=1e-12)
    # very humid: beta_RH = +0.25 (swelling reduces the contraction)
    pw = TD.normalize_props({"law": "cebfip2010", "rh": 100.0, "h0": 0.2,
                             "fcm": 38_000.0})
    assert TD.shrinkage_strain(pw, t) == pytest.approx(
        e_as - e_ds0 * 0.25 * b_ds, rel=1e-12)


def test_aci209_hand():
    p = TD.normalize_props({"law": "aci209", "rh": 70.0, "h0": 0.15,
                            "cure_days": 7.0})
    vs = 75.0                                   # mm (h0/2)
    g = (1.25 * 28 ** -0.118) * (1.27 - 0.67 * 0.7) * (
        2 / 3 * (1 + 1.13 * math.exp(-0.0213 * vs)))
    dt = 500.0
    phi = dt ** 0.6 / (10 + dt ** 0.6) * 2.35 * g
    assert TD.creep_coefficient(p, 28 + dt, 28) == pytest.approx(phi,
                                                                 rel=1e-12)
    e_u = 780e-6 * (1.40 - 1.02 * 0.7) * (1.202 - 0.2337 * math.log10(7)) \
        * 1.2 * math.exp(-0.00472 * vs)
    assert TD.shrinkage_strain(p, 7 + 100) == pytest.approx(
        100 / 135 * e_u, rel=1e-12)
    # strength / modulus ageing, moist type I: t/(4 + 0.85 t)
    assert TD.strength_ratio(p, 7.0) == pytest.approx(7 / (4 + 5.95))
    assert TD.modulus_ratio(p, 7.0) == pytest.approx(math.sqrt(7 / 9.95))
    # J = (1 + phi)/E(t0)
    assert TD.compliance(p, E_C, 28 + dt, 28) == pytest.approx(
        (1 + phi) / (E_C * math.sqrt(28 / (4 + 0.85 * 28))), rel=1e-12)
    # overrides
    po = TD.normalize_props({"law": "aci209", "phi_u": 2.0,
                             "eps_shu": 500e-6})
    assert TD.creep_coefficient(po, 10 ** 12, 28) == pytest.approx(2.0,
                                                                   rel=1e-6)
    assert TD.shrinkage_strain(po, 7 + 35) == pytest.approx(250e-6)


def test_gl2000_hand():
    p = TD.normalize_props({"law": "gl2000", "rh": 70.0, "h0": 0.15,
                            "cement": "I", "cure_days": 7.0,
                            "fcm": 40_000.0})
    vs, h = 75.0, 0.7
    t0, dt = 28.0, 365.0
    Phi = math.sqrt(1 - math.sqrt((t0 - 7) / (t0 - 7 + 0.12 * vs * vs)))
    phi = Phi * (2 * dt ** 0.3 / (dt ** 0.3 + 14)
                 + math.sqrt(7 / t0) * math.sqrt(dt / (dt + 7))
                 + 2.5 * (1 - 1.086 * h * h)
                 * math.sqrt(dt / (dt + 0.12 * vs * vs)))
    assert TD.creep_coefficient(p, t0 + dt, t0) == pytest.approx(phi,
                                                                 rel=1e-12)
    eps = (900 * math.sqrt(30 / 40) * 1e-6 * (1 - 1.18 * h ** 4)
           * math.sqrt(dt / (dt + 0.12 * vs * vs)))
    assert TD.shrinkage_strain(p, 7 + dt) == pytest.approx(eps, rel=1e-12)
    # J = 1/E(t0) + phi28/E28; E(t) from 3500 + 4300 sqrt(fcm(t))
    f7 = 40 * math.exp(0.335 * (1 - math.sqrt(4)))
    r7 = (3500 + 4300 * math.sqrt(f7)) / (3500 + 4300 * math.sqrt(40))
    assert TD.modulus_ratio(p, 7.0) == pytest.approx(r7, rel=1e-12)
    # default fcm = 1.1 fc + 5 MPa
    pd = TD.normalize_props({"law": "gl2000"})
    assert TD.fcm_mpa(pd, 30_000.0) == pytest.approx(38.0)


def test_user_curves_and_relaxation_hand():
    p = TD.normalize_props({"law": "user",
                            "creep_curve": [[0, 0], [100, 1.0], [1000, 2.0]],
                            "shrinkage_curve": [[0, 0], [100, 2e-4]],
                            "modulus_curve": [[1, 0.5], [28, 1.0]],
                            "cure_days": 3.0})
    assert TD.creep_coefficient(p, 28 + 550, 28) == pytest.approx(1.5)
    assert TD.shrinkage_strain(p, 3 + 50) == pytest.approx(1e-4)
    assert TD.shrinkage_strain(p, 3 + 500) == pytest.approx(2e-4)
    assert TD.modulus_ratio(p, 14.5) == pytest.approx(0.75)
    assert TD.compliance(p, E_C, 14.5 + 100, 14.5) == pytest.approx(
        (1 + 1.0) / (0.75 * E_C))
    # EN 1992-1-1 class 2 at 1000 h, mu = 0.7: 0.66 rho e^(9.1 mu) 1e-5
    r = TD.normalize_relaxation({"law": "ec2", "class": 2, "rho1000": 2.5})
    assert TD.relaxation_ratio(r, 0.7 * 1860e3, 1000 / 24, fpk=1860e3) == \
        pytest.approx(0.66 * 2.5 * math.exp(9.1 * 0.7) * 1e-5, rel=1e-12)
    th = 500000.0                                   # hours
    assert TD.relaxation_ratio(r, 0.7 * 1860e3, th / 24, fpk=1860e3) == \
        pytest.approx(0.66 * 2.5 * math.exp(6.37) * (th / 1000) ** 0.225
                      * 1e-5, rel=1e-12)
    ra = TD.normalize_relaxation({"law": "aci", "K": 45})
    assert TD.relaxation_ratio(ra, 0.75 * 1690e3, 1000.0, fpy=1690e3) == \
        pytest.approx(math.log10(24000) / 45 * 0.2, rel=1e-12)


def test_props_validation_and_roundtrip():
    with pytest.raises(ValueError):
        TD.normalize_props({"law": "bogus"})
    with pytest.raises(ValueError):
        TD.normalize_props({"law": "cebfip2010", "cement": "III"})
    with pytest.raises(ValueError):
        TD.normalize_props({"law": "user"})          # needs curves
    with pytest.raises(ValueError):
        TD.normalize_props({"law": "aci209", "rh": 20})
    with pytest.raises(ValueError):
        TD.normalize_props({"law": "aci209", "foo": 1})
    m = _column_model({"law": "aci209"})
    d = m.to_dict()
    assert d["material_time_dependent"]["C"]["law"] == "aci209"
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d)))
    d2 = m2.to_dict()
    assert d2["material_time_dependent"] == d["material_time_dependent"]
    assert d2["staged_cases"] == d["staged_cases"]
    m3 = BuildingModel.from_dict(json.loads(json.dumps(d2)))
    assert json.dumps(m3.to_dict(), sort_keys=True) == json.dumps(
        d2, sort_keys=True)
    bad = copy.deepcopy(d)
    bad["material_time_dependent"]["NOPE"] = {"law": "aci209"}
    with pytest.raises(ValueError):
        BuildingModel.from_dict(bad)
    # legacy model: no key emitted
    plain = BuildingModel(name="x")
    assert "material_time_dependent" not in plain.to_dict()


# --------------------------------------------------------------------------- #
# 2. step-by-step staged creep: columns
# --------------------------------------------------------------------------- #
H = 3.0
A_COL = 0.16


def _column_model(props, steps=4, lateral=False, P=1000.0, dur=1000.0,
                  t_eval=None, out_times=None):
    m = BuildingModel(name="col")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=E_C, nu=0.2, fc=FC))
    m.set_stories([H])
    m.add_section(FrameSection.rectangular("S", "C", 0.4, 0.4))
    m.add_member("column", "S", (0, 0, 0), (0, 0, H), story="Story1",
                 uid="C1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    pat = LoadPattern("DEAD")
    pat.nodal_loads.append(NodalLoad((0, 0, H), 10.0 if lateral else 0.0,
                                     5.0 if lateral else 0.0, -P))
    m.patterns["DEAD"] = pat
    m.add_case("DEAD", {"DEAD": 1.0})
    m.add_group("G", members=["C1"], points=[[0, 0, H]])
    m.material_time_dependent = {"C": props}
    td = {"method": "step", "steps_per_stage": steps}
    if t_eval is not None:
        td["t_eval"] = t_eval
    if out_times:
        td["output_times"] = out_times
    m.add_staged_case("ST", stages=[
        {"name": "S1", "duration_days": dur, "operations": [
            {"op": "add", "group": "G", "age_days": 28},
            {"op": "load", "group": "G", "pattern": "DEAD"}]}],
        time_dependent=td)
    m.validate()
    return m


def _top(r, z=H):
    eng_nodes = r.case.node_disp
    return [v for v in eng_nodes.values()]


def _uz_top(res):
    return min(v[2] for v in res.case.node_disp.values())


@pytest.mark.parametrize("steps", [1, 3, 8])
def test_column_constant_load_user_exact(steps):
    """sigma/E(t0) (1 + phi) for any number of steps."""
    props = {"law": "user", "creep_curve": [[0, 0], [1000, 2.0]],
             "shrinkage": False, "aging": False}
    r = OpenSeesEngine(_column_model(props, steps=steps)).run_staged("ST")
    el = -1000.0 * H / (A_COL * E_C)
    assert _uz_top(r) == pytest.approx(el * 3.0, rel=1e-12)
    th = r.to_dict()["time_history"]
    assert th["times"][0] == 0.0 and th["times"][-1] == 1000.0
    # every intermediate time follows the curve: phi linear in t
    for t, uz in zip(th["times"], th["story_uz"]["Story1"]):
        assert uz == pytest.approx(el * (1 + 2.0 * t / 1000.0), rel=1e-12)
    sh = r.shortening["Story1"]
    assert sh["uz_elastic"] == pytest.approx(el, rel=1e-12)
    assert sh["delta"] == pytest.approx(2 * el, rel=1e-12)


@pytest.mark.parametrize("law,extra", [
    ("aci209", {"rh": 60.0, "h0": 0.2}),
    ("cebfip2010", {"rh": 50.0, "h0": 0.2, "cement": "R"}),
    ("gl2000", {"rh": 70.0, "h0": 0.3, "cement": "III"}),
])
def test_column_constant_load_code_laws(law, extra):
    props = dict(law=law, shrinkage=False, **extra)
    r = OpenSeesEngine(_column_model(props, steps=5)).run_staged("ST")
    p = TD.normalize_props(props)
    J = TD.compliance(p, E_C, 28 + 1000.0, 28.0, FC)
    assert _uz_top(r) == pytest.approx(-1000.0 / A_COL * J * H, rel=1e-11)
    if law == "aci209":            # J = (1 + phi)/E(t0) explicitly
        phi = TD.creep_coefficient(p, 1028.0, 28.0)
        e0 = E_C * math.sqrt(28 / (4 + 0.85 * 28))
        assert _uz_top(r) == pytest.approx(-1000 / A_COL / e0 * (1 + phi)
                                           * H, rel=1e-11)


def test_column_shrinkage_only_free():
    """Unloaded column: shortening = eps_sh(t) - eps_sh(t_add) exactly."""
    props = {"law": "aci209", "rh": 60.0, "creep": True}
    r = OpenSeesEngine(_column_model(props, P=0.0, steps=3)
                       ).run_staged("ST")
    p = TD.normalize_props(props)
    d = (TD.shrinkage_strain(p, 1028.0) - TD.shrinkage_strain(p, 28.0)) * H
    assert _uz_top(r) == pytest.approx(-d, rel=1e-10)


def test_cantilever_curvature_creep_both_axes():
    """Determinate cantilever, lateral tip loads in X and Y: the creep
    curvatures (both local axes) give (1 + phi) x elastic deflections."""
    props = {"law": "user", "creep_curve": [[0, 0], [1000, 1.5]],
             "shrinkage": False, "aging": False}
    r = OpenSeesEngine(_column_model(props, steps=3, lateral=True)
                       ).run_staged("ST")
    tip = [v for v in r.case.node_disp.values() if abs(v[2]) > 0][0]
    P = 10.0
    I_y = 0.4 ** 4 / 12
    dx = P * H ** 3 / (3 * E_C * I_y)
    assert tip[0] == pytest.approx(2.5 * dx, rel=1e-8)
    assert tip[1] == pytest.approx(2.5 * dx / 2, rel=1e-8)


def test_output_times_snapshots():
    props = {"law": "user", "creep_curve": [[0, 0], [1000, 2.0]],
             "shrinkage": False, "aging": False}
    r = OpenSeesEngine(_column_model(props, steps=2, out_times=[250.0])
                       ).run_staged("ST")
    th = r.to_dict()["time_history"]
    assert 250.0 in th["times"]
    (snap,) = th["outputs"]
    assert snap["t"] == 250.0
    uz = min(v[2] for v in snap["node_disp"].values())
    assert uz == pytest.approx(-1000 * H / (A_COL * E_C) * 1.5, rel=1e-12)


def test_long_term_after_last_stage():
    props = {"law": "user", "creep_curve": [[0, 0], [1000, 2.0]],
             "shrinkage": False, "aging": False}
    r = OpenSeesEngine(_column_model(props, steps=2, dur=100.0,
                                     t_eval=1000.0)).run_staged("ST")
    assert _uz_top(r) == pytest.approx(-1000 * H / (A_COL * E_C) * 3.0,
                                       rel=1e-12)
    assert r.to_dict()["time_history"]["stage"][-1] == "(long term)"


def _two_stage(props, steps):
    m = BuildingModel(name="col2")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=E_C, nu=0.2, fc=FC))
    m.set_stories([H, 2 * H])
    m.add_section(FrameSection.rectangular("S", "C", 0.4, 0.4))
    m.add_member("column", "S", (0, 0, 0), (0, 0, H), story="Story1",
                 uid="C1")
    m.add_member("column", "S", (0, 0, H), (0, 0, 2 * H), story="Story2",
                 uid="C2")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    pat = LoadPattern("DEAD")
    pat.nodal_loads.append(NodalLoad((0, 0, H), 0, 0, -800.0))
    pat.nodal_loads.append(NodalLoad((0, 0, 2 * H), 0, 0, -500.0))
    m.patterns["DEAD"] = pat
    m.add_case("DEAD", {"DEAD": 1.0})
    m.add_group("G1", members=["C1"], points=[[0, 0, H]])
    m.add_group("G2", members=["C2"], points=[[0, 0, 2 * H]])
    m.material_time_dependent = {"C": props}
    m.add_staged_case("ST", stages=[
        {"name": "S1", "duration_days": 30.0, "operations": [
            {"op": "add", "group": "G1", "age_days": 14},
            {"op": "load", "group": "G1", "pattern": "DEAD"}]},
        {"name": "S2", "duration_days": 970.0, "operations": [
            {"op": "add", "group": "G2", "age_days": 7},
            {"op": "load", "group": "G2", "pattern": "DEAD"}]}],
        time_dependent={"method": "step", "steps_per_stage": steps,
                        "t_eval": 3000.0})
    m.validate()
    return m


@pytest.mark.parametrize("steps", [2, 6])
def test_two_stage_column_shortening_hand(steps):
    """Story 1 carries 800 kN from t = 0 (age 14 d) and +500 kN from
    t = 30 d (age 44 d); story 2 (cast at 23 d, added at 30 d at age 7)
    carries 500 kN.  Rebuild-and-accumulate: story 2 is placed at design
    level, so its top moves only by story-1 shortening after 30 d.  The
    hand solution superposes the loading-age compliances (MC2010 with
    ageing) + shrinkage since addition — exact for this determinate
    stack."""
    props = {"law": "cebfip2010", "rh": 60.0, "h0": 0.2, "cement": "N"}
    p = TD.normalize_props(props)
    r = OpenSeesEngine(_two_stage(props, steps)).run_staged("ST")
    t, tc1, tc2 = 3000.0, -14.0, 23.0

    def J(tt, tau, tc):
        return TD.compliance(p, E_C, tt - tc, tau - tc, FC)

    def sh(tt, tc):
        return TD.shrinkage_strain(p, tt - tc, FC)
    d1 = H * (800 / A_COL * J(t, 0, tc1) + 500 / A_COL * J(t, 30, tc1)
              + sh(t, tc1) - sh(0, tc1))
    d1_30 = H * (800 / A_COL * J(30, 0, tc1) + sh(30, tc1) - sh(0, tc1))
    d2 = H * (500 / A_COL * J(t, 30, tc2) + sh(t, tc2) - sh(30, tc2))
    s = r.shortening
    assert s["Story1"]["uz_time_dependent"] == pytest.approx(-d1, rel=1e-10)
    assert s["Story2"]["uz_time_dependent"] == pytest.approx(
        -(d1 - d1_30 + d2), rel=1e-10)
    # the instantaneous (elastic) share uses the loading-age moduli
    e1 = H * (800 / A_COL * J(0, 0, tc1) + 500 / A_COL * J(30, 30, tc1))
    assert s["Story1"]["uz_elastic"] == pytest.approx(-e1, rel=1e-10)


def _composite(props, steps):
    m = BuildingModel(name="comp")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=E_C, nu=0.2, fc=FC))
    m.add_material(Material("STL", E=200e6, nu=0.3, material_type="steel"))
    m.set_stories([H])
    m.add_section(FrameSection.rectangular("S", "C", 0.4, 0.4))
    m.add_section(FrameSection("R", "STL", 0.004, 1e-6, 1e-6, 2e-6))
    m.add_member("column", "S", (0, 0, 0), (0, 0, H), story="Story1",
                 uid="C1")
    m.add_member("column", "R", (0, 0, 0), (0, 0, H), story="Story1",
                 uid="R1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    pat = LoadPattern("DEAD")
    pat.nodal_loads.append(NodalLoad((0, 0, H), 0, 0, -2000.0))
    m.patterns["DEAD"] = pat
    m.add_case("DEAD", {"DEAD": 1.0})
    m.add_group("G", members=["C1", "R1"], points=[[0, 0, H]])
    m.material_time_dependent = {"C": props}
    m.add_staged_case("ST", stages=[
        {"name": "S1", "duration_days": 10000.0, "operations": [
            {"op": "add", "group": "G", "age_days": 28},
            {"op": "load", "group": "G", "pattern": "DEAD"}]}],
        time_dependent={"method": "step", "steps_per_stage": steps})
    m.validate()
    return m


def _aaem_composite(p, chi):
    """Age-adjusted effective modulus closed form for a concrete + steel
    column under constant N = 2000 kN applied at t0 = 28 d (phi referred
    to E(t0): J(t, t0) = (1 + phi_e)/E(t0)):
    d_eps = sigma_c0 phi_e / (E0 (1 + n rho (1 + chi phi_e)))."""
    Ac, As, Es = A_COL, 0.004, 200e6
    E0 = E_C * TD.modulus_ratio(p, 28.0, FC)
    phe = TD.compliance(p, E_C, 28 + 10000.0, 28.0, FC) * E0 - 1
    n, rho = Es / E0, As / Ac
    sc0 = 2000.0 / (Ac * (1 + n * rho))
    return sc0 / E0 + sc0 * phe / (E0 * (1 + n * rho * (1 + chi * phe)))


def test_composite_column_vs_aaem_aci209():
    """Creep sheds load from the concrete to the steel.  With the ageing
    ACI 209 law the converged step solution equals the AAEM closed form
    with the Trost-Bazant chi = 0.8 (0.1 %); the step count converges."""
    props = {"law": "aci209", "rh": 60.0, "shrinkage": False}
    p = TD.normalize_props(props)
    got = {n: -_uz_top(OpenSeesEngine(_composite(props, n)
                                      ).run_staged("ST")) / H
           for n in (12, 24)}
    assert got[24] == pytest.approx(_aaem_composite(p, 0.8), rel=1e-3)
    assert got[24] == pytest.approx(got[12], rel=2e-4)
    # compatibility: the steel force is Es As eps
    r = OpenSeesEngine(_composite(props, 12)).run_staged("ST")
    Ns = r.case.member_forces["R1"][6]
    assert Ns == pytest.approx(-200e6 * 0.004 * got[12], rel=1e-9)


def test_composite_column_nonaging_limit():
    """Non-ageing creep (phi a function of t - t0 only) that has
    saturated: the exact relaxation limit is the AAEM with chi = 1."""
    props = {"law": "user", "creep_curve": [[0, 0], [10, 1.0], [100, 1.8],
                                            [1000, 2.0], [10000, 2.0]],
             "shrinkage": False, "aging": False}
    p = TD.normalize_props(props)
    got = -_uz_top(OpenSeesEngine(_composite(props, 20)).run_staged("ST")) / H
    assert got == pytest.approx(_aaem_composite(p, 1.0), rel=3e-3)
    assert got < _aaem_composite(p, 0.8)


def test_step_validation():
    m = _column_model({"law": "aci209"})
    with pytest.raises(ValueError, match="stage list"):
        m.add_staged_case("PS", stages="per_story",
                          time_dependent={"days_per_story": 7,
                                          "method": "step"})
    with pytest.raises(ValueError, match="steps_per_stage"):
        m.add_staged_case("X", stages=m.staged_cases["ST"].stages,
                          time_dependent={"method": "step",
                                          "steps_per_stage": 0})
    with pytest.raises(ValueError, match="need method"):
        m.add_staged_case("X", stages=m.staged_cases["ST"].stages,
                          time_dependent={"steps_per_stage": 3})
    with pytest.raises(ValueError, match="method"):
        m.add_staged_case("X", stages=m.staged_cases["ST"].stages,
                          time_dependent={"method": "exp"})


# --------------------------------------------------------------------------- #
# 3. bonded tendon elements
# --------------------------------------------------------------------------- #
L_B, ECC = 10.0, 0.2
B_W, B_H = 0.4, 0.6
EP = 196_501_000.0
AP = 0.001


def _beam(model_as=None, losses=None, n_sub=8, pt=True):
    m = BuildingModel(name="pt")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=E_C, nu=0.2, fc=FC))
    m.set_stories([1.0])
    m.add_section(FrameSection.rectangular("B", "C", B_W, B_H))
    m.add_member("beam", "B", (0, 0, 0), (L_B, 0, 0), story="Story1",
                 uid="B1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 0, 0)))
    m.supports.append(PointSupport((L_B, 0, 0), (0, 1, 1, 0, 0, 0)))
    m.patterns["PT"] = LoadPattern("PT")
    m.add_case("PT", {"PT": 1.0})
    td = {"uid": "T1", "area": AP, "jacking_stress": 1.0e6,
          "pattern": "PT", "host": "B1",
          "points": [[0, 0, -ECC], [L_B, 0, -ECC]], "n_sub": n_sub}
    if model_as:
        td["model_as"] = model_as
    if losses:
        td["losses"] = losses
    if pt:
        m.tendons = [td]
    m.validate()
    return m


def _mid_uz(r, x=L_B / 2):
    for t, c in r["nodes"].items():
        if abs(c[0] - x) < 1e-9 and abs(c[2]) < 1e-9:
            return r["cases"]["PT"]["node_disp"][t][2]
    raise KeyError(x)


def _hand_bonded(P0):
    """Section equations of the bonded composite (concrete + tendon at
    y = -e): (EA + k) e0 - k e kap = -P0 ; -k e e0 + (EI + k e^2 + EpIb)
    kap = P0 e, k = Ep Ap.  Returns (P_eff, kappa)."""
    A = B_W * B_H
    I = B_W * B_H ** 3 / 12
    k = EP * AP
    Ib = AP * AP / (4 * math.pi)
    K = np.array([[E_C * A + k, -k * ECC],
                  [-k * ECC, E_C * I + k * ECC ** 2 + EP * Ib]])
    e0, kap = np.linalg.solve(K, [-P0, P0 * ECC])
    P_eff = P0 + k * (e0 - ECC * kap)
    return P_eff, kap


def test_tendon_element_camber_and_es_loss():
    r = OpenSeesEngine(_beam("elements")).run().to_dict()
    P0 = 1.0e6 * AP
    P_eff, kap = _hand_bonded(P0)
    el = r["tendons"]["T1"]["element"]
    assert el["n"] == 8
    for P in el["cases"]["PT"]["P"]:
        assert P == pytest.approx(P_eff, rel=1e-9)
    I = B_W * B_H ** 3 / 12
    Ib = AP * AP / (4 * math.pi)
    camber = P_eff * ECC * L_B ** 2 / (8 * (E_C * I + EP * Ib))
    assert _mid_uz(r) == pytest.approx(camber, rel=1e-9)
    assert kap * L_B ** 2 / 8 == pytest.approx(camber, rel=1e-9)
    # ES loss = (Ep/Ec) f_cgp Ap with f_cgp = P_eff/A + P_eff e^2/I
    fcgp = P_eff / (B_W * B_H) + P_eff * ECC ** 2 / I
    loss = el["cases"]["PT"]["loss"][0]
    assert loss == pytest.approx(P0 - P_eff, rel=1e-9)
    # textbook (Ep/Ec) f_cgp Ap (strand bending neglected: 1e-4)
    assert loss == pytest.approx(EP / E_C * fcgp * AP, rel=1e-4)
    # P e L^2 / 8EI with the jacking force is the (upper) loads-only value
    assert camber < P0 * ECC * L_B ** 2 / (8 * E_C * I)


def test_tendon_element_vs_equivalent_load_method():
    """Loads-only model with the element's effective force (long-term
    fraction = 1 - P_eff/P0) gives the same camber (strand bending
    neglected: 1e-4)."""
    P0 = 1.0e6 * AP
    P_eff, _ = _hand_bonded(P0)
    r_el = OpenSeesEngine(_beam("elements")).run().to_dict()
    m = _beam(None, losses={"long_term_fraction": 1 - P_eff / P0})
    # split the host at mid-span so the loads model reports the node
    m.frame_auto_mesh = {"max_length": 2.5}
    r_ld = OpenSeesEngine(m).run().to_dict()
    I = B_W * B_H ** 3 / 12
    assert _mid_uz(r_ld) == pytest.approx(P_eff * ECC * L_B ** 2
                                          / (8 * E_C * I), rel=1e-9)
    assert _mid_uz(r_el) == pytest.approx(_mid_uz(r_ld), rel=1e-4)


def test_tendon_element_other_case_force_change():
    """A gravity case without the PT pattern still loads the bonded
    tendon: P = k * elongation (no prestress term)."""
    m = _beam("elements")
    m.patterns["LIVE"] = LoadPattern("LIVE")
    m.patterns["LIVE"].nodal_loads.append(NodalLoad((L_B / 2, 0, 0), 0, 0,
                                                    -100.0))
    m.add_case("LIVE", {"LIVE": 1.0})
    m.validate()
    r = OpenSeesEngine(m).run().to_dict()
    P = r["tendons"]["T1"]["element"]["cases"]["LIVE"]["P"]
    assert all(v > 0 for v in P)            # sagging: bottom tendon pulled
    assert r["tendons"]["T1"]["element"]["cases"]["LIVE"]["P_initial"] == \
        [0.0] * 8


def test_tendon_element_parabolic_matches_loads_model_roughly():
    """Parabolic drape: element chords vs exact parabola loads — mid-span
    uplift within the discretisation + ES difference (3 %)."""
    def model(ma):
        m = _beam(ma)
        m.tendons[0] = dict(m.tendons[0], points=[[0, 0, 0], [L_B, 0, 0]],
                            sags=[0.25], n_sub=16)
        if ma is None:
            m.frame_auto_mesh = {"max_length": 2.5}
        m.validate()
        return m
    ra = OpenSeesEngine(model("elements")).run().to_dict()
    a = _mid_uz(ra)
    Pm = ra["tendons"]["T1"]["element"]["cases"]["PT"]["P_mean"]
    mb = model(None)
    mb.tendons[0]["losses"] = {"long_term_fraction": 1 - Pm / 1000.0}
    mb.validate()
    b = _mid_uz(OpenSeesEngine(mb).run().to_dict())
    assert a > 0 and b > 0
    # residual: ES varies with e(x)^2 along the drape (element model) and
    # chords vs the exact parabola
    assert a == pytest.approx(b, rel=0.025)


def test_tendon_roundtrip_and_legacy_identity():
    m = _beam(None)
    d = m.to_dict()
    assert "model_as" not in d["tendons"][0]
    assert "es_factor" not in d["tendons"][0]["losses"]
    me = _beam("elements", losses={"es_factor": 0.5})
    de = me.to_dict()
    assert de["tendons"][0]["model_as"] == "elements"
    assert de["tendons"][0]["losses"]["es_factor"] == 0.5
    m2 = BuildingModel.from_dict(json.loads(json.dumps(de)))
    assert m2.to_dict()["tendons"] == de["tendons"]
    with pytest.raises(ValueError):
        _beam("struts")


# --------------------------------------------------------------------------- #
# 4. losses vs hand values
# --------------------------------------------------------------------------- #
def test_friction_and_anchor_set_hand():
    k, dset = 0.02, 0.003
    m = _beam(None, losses={"wobble_k": k, "anchor_set": dset}, n_sub=40)
    td = T.normalize_tendon(m.tendons[0])
    prof = T.force_profile(m, td)
    P0 = 1000.0
    # anchor set on P0 exp(-k x): 2 int_0^l (P0 e^-kx - P0 e^-kl) dx
    # = 2 P0 [(1 - e^-kl)/k - l e^-kl] = delta Ep Ap  (bisection)
    lo, hi = 0.0, L_B
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f = 2 * P0 * ((1 - math.exp(-k * mid)) / k
                      - mid * math.exp(-k * mid))
        lo, hi = (mid, hi) if f < dset * EP * AP else (lo, mid)
    lset = 0.5 * (lo + hi)
    # (the engine integrates the friction curve piecewise linearly)
    assert prof["anchor_set_length"]["start"] == pytest.approx(lset,
                                                               rel=1e-5)
    Pstar = P0 * math.exp(-k * lset)
    for s, P in zip(prof["s_bound"], prof["P_bound"]):
        if s > lset + 1e-6:                    # beyond the set: friction
            assert P == pytest.approx(P0 * math.exp(-k * s), rel=1e-12)
        else:                                  # mirrored diagram
            assert P == pytest.approx(2 * Pstar - P0 * math.exp(-k * s),
                                      rel=1e-5)
    # the linear-slope hand formula sqrt(delta Ep Ap / p) is close
    hand = TE.hand_losses(P0, 0.0, k, 0.0, lset, EP, AP, set_mm=3.0)
    assert hand["set_length"] == pytest.approx(lset, rel=0.05)
    # curvature friction: P(x) = P0 exp(-(mu alpha + k x)) on a parabola
    mp = _beam(None, losses={"friction_mu": 0.2}, n_sub=16)
    mp.tendons[0] = dict(mp.tendons[0], points=[[0, 0, 0], [L_B, 0, 0]],
                         sags=[0.25])
    mp.validate()
    tdp = T.normalize_tendon(mp.tendons[0])
    pr = T.force_profile(mp, tdp)
    alpha_end = 2 * math.atan(4 * 0.25 / L_B)
    assert pr["alpha_bound"][-1] == pytest.approx(alpha_end, rel=1e-9)
    assert pr["P_bound"][-1] == pytest.approx(
        P0 * math.exp(-0.2 * alpha_end), rel=1e-9)


def test_es_factor_loads_model_hand():
    m = _beam(None, losses={"es_factor": 0.5})
    td = T.normalize_tendon(m.tendons[0])
    prof = T.force_profile(m, td)
    P0 = 1000.0
    I = B_W * B_H ** 3 / 12
    fcgp = P0 / (B_W * B_H) + P0 * ECC ** 2 / I
    dP = 0.5 * EP / E_C * fcgp * AP
    for P in prof["P_mid"]:
        assert P == pytest.approx(P0 - dP, rel=1e-12)
    hl = TE.hand_losses(P0, 0, 0, 0, 1, EP, AP, Ec=E_C, fcgp=fcgp, kes=0.5)
    assert hl["es_stress_loss"] * AP == pytest.approx(dP, rel=1e-12)


def _staged_pt(relax=None, creep=False, steps=4):
    m = _beam("elements", pt=True)
    m.add_material(Material("A416Gr270", E=EP, nu=0.3,
                            material_type="tendon", fy=1_689_900.0,
                            fu=1_861_580.0))
    m.tendons[0]["material"] = "A416Gr270"
    m.add_group("BEAM", members=["B1"])
    props = ({"law": "user", "creep_curve": [[0, 0], [1000, 2.0]],
              "shrinkage": False, "aging": False} if creep else
             {"law": "user", "creep": False, "shrinkage": False,
              "aging": False})
    m.material_time_dependent = {"C": props}
    if relax:
        m.material_time_dependent["A416Gr270"] = {
            "law": "user", "creep": False, "shrinkage": False,
            "relaxation": relax}
    m.add_staged_case("ST", stages=[
        {"name": "S1", "duration_days": 1000.0, "operations": [
            {"op": "add", "group": "BEAM", "age_days": 28},
            {"op": "load", "group": "BEAM", "pattern": "PT"}]}],
        time_dependent={"method": "step", "steps_per_stage": steps})
    m.validate()
    return m


def test_staged_tendon_transfer_matches_static():
    r = OpenSeesEngine(_staged_pt()).run_staged("ST")
    P_eff, _ = _hand_bonded(1000.0)
    th = r.to_dict()["time_history"]
    for P in th["tendons"]["T1"]["P"]:
        assert P == pytest.approx(P_eff, rel=1e-9)


def test_staged_tendon_relaxation_loss_hand():
    """EC2 class 2 relaxation on a bonded straight tendon: the force
    change is -dPr / (1 + Ep Ap f), f = 1/EA + e^2/EI (uniform
    member, concrete creep off)."""
    relax = {"law": "ec2", "class": 2, "rho1000": 2.5}
    r = OpenSeesEngine(_staged_pt(relax=relax)).run_staged("ST")
    P_eff, _ = _hand_bonded(1000.0)
    sig = P_eff / AP
    rr = TD.relaxation_ratio(TD.normalize_relaxation(relax), sig, 1000.0,
                             fpk=1_861_580.0)
    A = B_W * B_H
    I = B_W * B_H ** 3 / 12
    Ib = AP * AP / (4 * math.pi)
    f = 1 / (E_C * A) + ECC ** 2 / (E_C * I + EP * Ib)
    dP = -AP * sig * rr / (1 + EP * AP * f)
    P_end = r.to_dict()["time_history"]["tendons"]["T1"]["P"]
    for P in P_end:
        assert P == pytest.approx(P_eff + dP, rel=1e-6)
    assert dP < 0


def test_staged_tendon_creep_loss_and_camber_growth():
    """Concrete creep (phi = 2 at 1000 d) shortens the concrete at the
    tendon level: the bonded tendon loses force and the camber grows by
    less than (1 + phi) (the loss relaxes it)."""
    r = OpenSeesEngine(_staged_pt(creep=True, steps=8)).run_staged("ST")
    th = r.to_dict()["time_history"]
    P = th["tendon_force"]["T1"]
    assert P[-1] < P[0]
    P_eff, kap0 = _hand_bonded(1000.0)
    assert P[0] == pytest.approx(P_eff, rel=1e-9)
    # AAEM estimate of the creep loss: dP = n_aaem... bounded by the
    # free-creep loss Ep Ap phi eps_cp
    A = B_W * B_H
    I = B_W * B_H ** 3 / 12
    eps_cp = P_eff / (E_C * A) + P_eff * ECC ** 2 / (E_C * I)
    free = EP * AP * 2.0 * eps_cp
    assert 0.6 * free < P[0] - P[-1] < free


def test_tendon_element_on_shell_host():
    """One-way slab strip with a parabolic element tendon (nearest-node
    links): self-equilibrated (zero reactions) and the camber matches the
    beam value 5 P_eff e L^2/(48 EI) within the mesh / link tolerance."""
    from skyframe.core.model import ShellSection
    L, b, t, e = 10.0, 1.0, 0.25, 0.08
    m = BuildingModel(name="slab")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=E_C, nu=0.0))
    m.set_stories([1.0])
    m.add_shell_section(ShellSection("S", "C", t))
    m.add_shell("slab", "shell", "S", [(0, 0, 0), (L, 0, 0), (L, b, 0),
                                       (0, b, 0)], mesh_size=0.5,
                story="Story1", uid="S1")
    for y in (0.0, 0.5, 1.0):
        m.supports.append(PointSupport((0, y, 0), (1, 1 if y == 0 else 0,
                                                   1, 0, 0, 0)))
        m.supports.append(PointSupport((L, y, 0), (0, 0, 1, 0, 0, 0)))
    m.patterns["PT"] = LoadPattern("PT")
    m.add_case("PT", {"PT": 1.0})
    m.tendons.append({"uid": "T1", "area": AP, "jacking_stress": 1.0e6,
                      "pattern": "PT", "model_as": "elements", "host": "S1",
                      "profile": {"start": [0, 0.5, 0], "end": [L, 0.5, 0],
                                  "sag": e}, "n_sub": 20})
    m.validate()
    r = OpenSeesEngine(m).run().to_dict()
    tot = [0.0] * 3
    for v in r["cases"]["PT"]["reactions"].values():
        tot = [a + c for a, c in zip(tot, v[:3])]
    assert max(abs(c) for c in tot) < 1e-6
    el = r["tendons"]["T1"]["element"]
    Pm = el["cases"]["PT"]["P_mean"]
    assert 0.9 * 1000.0 < Pm < 1000.0               # ES loss (~4 %)
    mid = [t_ for t_, c in r["nodes"].items()
           if abs(c[0] - L / 2) < 1e-9 and abs(c[1] - 0.5) < 1e-9][0]
    uz = r["cases"]["PT"]["node_disp"][mid][2]
    EI = E_C * b * t ** 3 / 12
    assert uz == pytest.approx(5 * Pm * e * L ** 2 / (48 * EI), rel=0.05)


# --------------------------------------------------------------------------- #
# 5. database editing tables + HTTP API
# --------------------------------------------------------------------------- #
def test_dbedit_material_time_dependent_table():
    from skyframe.core import modeltables as MT
    m = _column_model({"law": "aci209", "rh": 60.0})
    d = m.to_dict()
    t = MT.get_table(d, "material_time_dependent")
    (row,) = t["rows"]
    assert row["name"] == "C" and row["law"] == "aci209" and row["rh"] == 60
    # untouched apply = identity
    model, errs, _ = MT.apply_tables(d, {"material_time_dependent":
                                         t["rows"]})
    assert not errs
    assert model.to_dict() == BuildingModel.from_dict(d).to_dict()
    # edit RH and the law
    row2 = dict(row, rh=55.0, law="gl2000", cement="III")
    model, errs, _ = MT.apply_tables(d, {"material_time_dependent": [row2]})
    assert not errs, errs
    p = model.material_time_dependent["C"]
    assert p["rh"] == 55.0 and p["law"] == "gl2000" and p["cement"] == "III"
    # delete the row -> key dropped from to_dict
    model, errs, _ = MT.apply_tables(d, {"material_time_dependent": []})
    assert not errs and "material_time_dependent" not in model.to_dict()
    # legacy model: empty table, untouched apply leaves to_dict identical
    plain = _beam(None).to_dict()
    assert MT.get_table(plain, "material_time_dependent")["rows"] == []
    model, errs, _ = MT.apply_tables(plain, {"material_time_dependent": []})
    assert not errs and model.to_dict() == BuildingModel.from_dict(
        plain).to_dict()


def test_dbedit_tendon_element_columns():
    from skyframe.core import modeltables as MT
    d = _beam(None).to_dict()
    t = MT.get_table(d, "tendons")
    (row,) = t["rows"]
    assert row["model_as"] == "loads" and row["es_factor"] == 0.0
    model, errs, _ = MT.apply_tables(d, {"tendons": t["rows"]})
    assert not errs and model.to_dict() == BuildingModel.from_dict(
        d).to_dict()
    model, errs, _ = MT.apply_tables(d, {"tendons": [
        dict(row, model_as="elements", es_factor=0.5)]})
    assert not errs, errs
    assert model.tendons[0]["model_as"] == "elements"
    assert model.tendons[0]["losses"]["es_factor"] == 0.5


def test_api_curves_losses_relaxation():
    from skyframe.api.server import create_app
    app = create_app()
    c = app.test_client()
    r = c.post("/api/timedep/curves", json={"props": {"law": "aci209"},
                                            "E": E_C, "times": [100.0]})
    assert r.status_code == 200
    p = TD.normalize_props({"law": "aci209"})
    assert r.get_json()["creep"][0] == pytest.approx(
        TD.creep_coefficient(p, 128.0, 28.0))
    assert c.post("/api/timedep/curves",
                  json={"props": {"law": "x"}}).status_code == 400
    m = _beam("elements", losses={"es_factor": 0.0})
    r = c.post("/api/timedep/tendon_losses",
               json={"uid": "T1", "model": m.to_dict()})
    j = r.get_json()
    assert r.status_code == 200 and j["model_as"] == "elements"
    assert len(j["elements"]) == 8 and j["P0"] == pytest.approx(1000.0)
    r = c.post("/api/timedep/relaxation", json={
        "relaxation": {"law": "aci", "K": 45}, "sigma_pi": 0.75 * 1690e3,
        "fpy": 1690e3, "times": [1000.0]})
    assert r.get_json()["ratio"][0] == pytest.approx(
        math.log10(24000) / 45 * 0.2)
