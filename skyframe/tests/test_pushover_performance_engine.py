"""Pushover performance methods on the engine: ADRS conversion of a 2-DOF
shear building by hand, the run() hook, the endpoint and byte-identity.

Benchmark (as tests/test_pushover_distribution.py): quick_building 1x1
bay, h1 = 4 m, h2 = 3 m, rigid beams, axially rigid columns, so each
story is a shear spring k_i = 4 * 12 E I22 / h_i^3; masses m1 = 20 t,
m2 = 10 t on the diaphragm masters.  2-DOF eigenproblem by hand:

    m1 m2 w^4 - (m1 k2 + m2 (k1 + k2)) w^2 + k1 k2 = 0
    phi = [1, (k1 + k2 - w1^2 m1) / k2]
    Gamma = sum m phi / sum m phi^2 ; alpha1 = (sum m phi)^2 /
            (sum m phi^2 * sum m)

ADRS: Sa = V / (alpha1 W), Sd = d_roof / (Gamma phi_roof).  Under the
first-mode force pattern f = m phi the elastic response is u = c phi, so
the capacity spectrum is the straight line Sa g / Sd = w1^2 EXACTLY.
"""

from __future__ import annotations

import json
import math

import pytest

pytest.importorskip("skyframe.engine.opensees_engine")

from skyframe.core.builder import quick_building                # noqa: E402
from skyframe.core.model import BuildingModel                   # noqa: E402
from skyframe.core import pushover_methods as pm                # noqa: E402
from skyframe.engine.opensees_engine import OpenSeesEngine      # noqa: E402
from skyframe.engine import pushover_performance as ppf         # noqa: E402

E = 25_000_000.0
H1, H2 = 4.0, 3.0
M1, M2 = 20.0, 10.0


def _model() -> BuildingModel:
    m = quick_building(name="PO2", bays_x=1, bays_y=1, stories=2,
                       story_height=H2, first_story_height=H1, E=E,
                       column_size=0.5)
    m.sections["BEAM"].I33 *= 1e5
    m.sections["BEAM"].I22 *= 1e5
    m.sections["BEAM"].A *= 1e3
    m.sections["COL"].A *= 1e4
    m.sections["COL"].I22 *= 2.0
    m.story_masses = {"Story1": M1, "Story2": M2}
    return m


def _hand():
    I = _model().sections["COL"].I22
    k1, k2 = (4 * 12.0 * E * I / h ** 3 for h in (H1, H2))
    a, b, c = M1 * M2, -(M1 * k2 + M2 * (k1 + k2)), k1 * k2
    lam = (-b - math.sqrt(b * b - 4 * a * c)) / (2 * a)       # w1^2
    phi = [1.0, (k1 + k2 - lam * M1) / k2]
    L = M1 * phi[0] + M2 * phi[1]
    Mn = M1 * phi[0] ** 2 + M2 * phi[1] ** 2
    gamma = L / Mn
    return {"w2": lam, "T": 2 * math.pi / math.sqrt(lam), "gamma": gamma,
            "pf_phi": gamma * phi[1], "alpha": L * L / (Mn * (M1 + M2)),
            "m_star": L / phi[1]}


def test_modal_props_match_2dof_hand():
    m = _model()
    m.add_pushover_case("PO", "X", target_drift=0.001, steps=4,
                        load_distribution="mode")
    eng = OpenSeesEngine(m)
    mp = ppf.modal_props(eng, m.pushover_cases["PO"])
    h = _hand()
    assert mp["T"] == pytest.approx(h["T"], rel=1e-3)
    assert abs(mp["pf_phi"]) == pytest.approx(h["pf_phi"], rel=1e-3)
    assert mp["alpha"] == pytest.approx(h["alpha"], rel=1e-3)
    assert mp["mass"] == pytest.approx(M1 + M2, rel=1e-9)
    assert mp["W"] == pytest.approx((M1 + M2) * 9.81, rel=1e-9)
    # N2 m* = alpha M / Gamma = sum m Phi (Phi = 1 at the roof)
    assert mp["alpha"] * mp["mass"] / abs(mp["pf_phi"]) == pytest.approx(
        h["m_star"], rel=1e-3)


def test_adrs_of_first_mode_push_is_the_w1_squared_line():
    m = _model()
    m.add_pushover_case("PO", "X", target_drift=0.001, steps=4,
                        load_distribution="mode")
    eng = OpenSeesEngine(m)
    po = eng.run_pushover("PO")
    mp = ppf.modal_props(eng, m.pushover_cases["PO"])
    sd, sa = pm.adrs(po.roof_disp, po.base_shear, W=mp["W"],
                     alpha=mp["alpha"], pf_phi=abs(mp["pf_phi"]))
    w2 = (2 * math.pi / mp["T"]) ** 2
    for d, a in zip(sd, sa):
        assert a * 9.81 / d == pytest.approx(w2, rel=1e-6)
    assert w2 == pytest.approx(_hand()["w2"], rel=2e-3)
    # by hand: Sa = V / (alpha W), Sd = d / (Gamma phi)
    assert sa[-1] == pytest.approx(po.base_shear[-1] / (mp["alpha"] * mp["W"]))
    assert sd[-1] == pytest.approx(po.roof_disp[-1] / abs(mp["pf_phi"]))


def _nl_model(perf=None):
    m = _model()
    m.story_masses = {"Story1": 100 * M1, "Story2": 100 * M2}   # T ~ 0.8 s
    kw = {}
    if perf is not None:
        kw["performance"] = perf
    m.add_pushover_case("PO", "X", target_drift=0.03, steps=60,
                        hinges="all_ends", default_My=2500.0,
                        hardening=0.02, load_distribution="mode", **kw)
    return m


PERF = {"spectrum": {"code": "asce7", "SDS": 0.8, "SD1": 0.4}}


def test_run_attaches_performance_only_when_configured():
    r0 = OpenSeesEngine(_nl_model()).run().to_dict()
    assert "performance" not in r0["pushover"]["PO"]
    r1 = OpenSeesEngine(_nl_model(PERF)).run().to_dict()
    p = r1["pushover"]["PO"]["performance"]
    # the curve itself is unchanged by the option
    assert r1["pushover"]["PO"]["roof_disp"] == r0["pushover"]["PO"]["roof_disp"]
    assert [r["method"] for r in p["summary"]] == list(pm.METHODS)
    for r in p["summary"]:
        assert r["roof_disp"] is not None and r["roof_disp"] > 0.0, r
        assert r["step"] is not None
    # rows reuse the ADRS conversion
    mp = p["modal"]
    for r in p["summary"]:
        if r["base_shear"] is not None:
            assert r["Sa"] == pytest.approx(r["base_shear"]
                                            / (mp["alpha"] * mp["W"]))
            assert r["Sd"] == pytest.approx(r["roof_disp"] / mp["pf_phi"])
    # FEMA 440 fixed point: the Teff radial point equals the MADRS point
    f = p["methods"]["fema440"]
    assert f["converged"]
    assert f["d_max_radial"] == pytest.approx(
        f["performance_point"]["Sd"], rel=1e-4)
    json.dumps(p)


def test_model_round_trip_and_validation():
    m = _nl_model({"methods": ["n2"], "spectrum": {"code": "ec8", "ag": 0.25,
                                                   "ground_type": "B"}})
    d = m.to_dict()
    assert d["pushover_cases"]["PO"]["performance"]["methods"] == ["n2"]
    s = json.dumps(d, sort_keys=True)
    m2 = BuildingModel.from_dict(json.loads(s))
    assert json.dumps(m2.to_dict(), sort_keys=True) == s
    # default: no key at all
    assert "performance" not in _nl_model().to_dict()["pushover_cases"]["PO"]
    with pytest.raises(ValueError, match="performance"):
        _nl_model({"behavior": "Z"})


def test_endpoint():
    from skyframe.api.server import create_app
    app = create_app()
    c = app.test_client()
    m = _nl_model()
    r = c.post("/api/model", json=m.to_dict())
    assert r.status_code == 200, r.get_json()
    r = c.post("/api/results/pushover-performance",
               json={"case": "PO", "performance": {
                   "methods": ["fema440", "n2"], "behavior": "B",
                   "spectrum": {"code": "ec8", "ag": 0.3}}})
    out = r.get_json()
    assert r.status_code == 200, out
    assert [x["method"] for x in out["summary"]] == ["fema440", "n2"]
    assert out["spectrum"]["TC"] == 0.6 and out["case"] == "PO"
    assert len(out["curve"]["disp"]) == len(out["capacity"]["Sd"])
    assert c.post("/api/results/pushover-performance",
                  json={"case": "nope"}).status_code == 400
    assert c.post("/api/results/pushover-performance",
                  json={"case": "PO", "performance": {"methods": []}}
                  ).status_code == 400
