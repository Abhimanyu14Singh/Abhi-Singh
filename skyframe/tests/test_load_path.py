"""Load-path explainer (core/load_path.py, api/load_path_api.py) pinned to
hand-derived values.  CONTRACT "Load-path explainer"."""

import math

import numpy as np
import pytest

from skyframe.core import load_path as lp
from skyframe.core.model import (BuildingModel, FrameSection, G_ACCEL,
                                 Material, MemberLoad, NodalLoad, NodalMass)
from skyframe.engine.opensees_engine import OpenSeesEngine

H_STORY, SPAN = 4.0, 6.0
WD, WL, HW = 10.0, 5.0, 20.0     # kN/m dead, kN/m live, kN wind at the top


def portal(cases=True):
    """Pinned-base single-bay portal frame (x-z plane)."""
    m = BuildingModel(name="Portal")
    m.add_material(Material("STEEL", E=200e6, nu=0.3, unit_weight=0.0))
    m.add_section(FrameSection.rectangular("COL", "STEEL", 0.3, 0.3))
    m.add_section(FrameSection.rectangular("BM", "STEEL", 0.3, 0.5))
    m.set_stories([H_STORY])
    m.base_fixity = "pinned"
    m.diaphragm = "none"
    m.add_member("column", "COL", (0, 0, 0), (0, 0, H_STORY), uid="C1")
    m.add_member("column", "COL", (SPAN, 0, 0), (SPAN, 0, H_STORY), uid="C2")
    m.add_member("beam", "BM", (0, 0, H_STORY), (SPAN, 0, H_STORY), uid="B1")
    m.pattern("DEAD", "dead").member_loads.append(MemberLoad("B1", w=WD))
    m.pattern("LIVE", "live").member_loads.append(MemberLoad("B1", w=WL))
    m.pattern("WIND", "wind").nodal_loads.append(
        NodalLoad((0.0, 0.0, H_STORY), fx=HW))
    m.add_case("ULS", {"DEAD": 1.2, "LIVE": 1.6, "WIND": 1.0})
    if cases:
        m.add_case("D", {"DEAD": 1.0})
        m.add_case("L", {"LIVE": 1.0})
        m.add_case("W", {"WIND": 1.0})
        m.add_combo("C1", {"D": 1.2, "L": 1.6})
        m.add_combo("C2", {"C1": 1.0, "W": 1.0})          # nested add
        m.add_combo("ENV", {"D": 1.4, "W": -1.0}, combo_type="envelope")
        m.add_combo("SR", {"D": 1.0, "W": 1.0}, combo_type="srss")
    m.num_modes = 1
    return m


def _tag_at(R, pt):
    for t, c in R["nodes"].items():
        if all(abs(a - b) < 1e-9 for a, b in zip(c, pt)) and t in R["supports"]:
            return t
    raise AssertionError(pt)


@pytest.fixture(scope="module")
def solved():
    lp.clear_cache()
    m = portal()
    R = OpenSeesEngine(m).run().to_dict()
    return m, R, _tag_at(R, (0, 0, 0))


def test_portal_reactions_per_pattern_hand_values(solved):
    m, R, left = solved
    t = {"kind": "reaction", "joint": left, "component": "FZ"}
    out = lp.explain(m, R, t, "ULS")
    assert out["supported"] and out["case_kind"] == "static"
    rows = {r["name"]: r for r in out["rows"]}
    # hand: symmetric frame -> each column carries w L / 2; wind on pinned
    # bases: R_left = -H h / L (moment about the right base)
    assert rows["DEAD"]["unit_value"] == pytest.approx(WD * SPAN / 2, rel=1e-9)
    assert rows["LIVE"]["unit_value"] == pytest.approx(WL * SPAN / 2, rel=1e-9)
    assert rows["WIND"]["unit_value"] == pytest.approx(-HW * H_STORY / SPAN,
                                                       rel=1e-9)
    assert rows["DEAD"]["contribution"] == pytest.approx(1.2 * WD * SPAN / 2)
    hand = 1.2 * WD * SPAN / 2 + 1.6 * WL * SPAN / 2 - HW * H_STORY / SPAN
    assert out["value"] == pytest.approx(hand, rel=1e-9)
    assert out["computed_total"] == pytest.approx(out["value"], rel=1e-12)
    assert out["check"]["ok"]
    # the single-pattern cases D/L/W are stored: no re-solve needed
    assert {r["source"] for r in out["rows"]} == {"stored:D", "stored:L",
                                                  "stored:W"}
    # ranked by |contribution|
    assert [r["rank"] for r in out["rows"]] == [1, 2, 3]
    assert out["rows"][0]["name"] == "DEAD"


def test_portal_resolved_superposition_matches_case():
    lp.clear_cache()
    m = portal(cases=False)
    R = OpenSeesEngine(m).run().to_dict()
    left = _tag_at(R, (0, 0, 0))
    out = lp.explain(m, R, {"kind": "reaction", "joint": left,
                            "component": "FZ"}, "ULS")
    assert {r["source"] for r in out["rows"]} == {"re-solved"}
    assert out["check"]["rel_err"] < 1e-9
    w = next(r for r in out["rows"] if r["name"] == "WIND")
    assert w["unit_value"] == pytest.approx(-HW * H_STORY / SPAN, rel=1e-9)
    # member force at a station: superposition is exact too
    for comp, st in (("M3", 0.5), ("V2", 0.0), ("P", 1.0)):
        o = lp.explain(m, R, {"kind": "member_force", "member": "B1",
                              "component": comp, "station": st}, "ULS")
        assert o["check"]["ok"], (comp, o["check"])
    o = lp.explain(m, R, {"kind": "base", "component": "FX"}, "ULS")
    assert o["value"] == pytest.approx(-HW, rel=1e-9)
    # the model is never mutated (no temporary cases left behind)
    assert set(m.cases) == {"ULS"}


def test_combo_factors_nested_and_patterns(solved):
    m, R, left = solved
    t = {"kind": "reaction", "joint": left, "component": "FZ"}
    o = lp.explain(m, R, t, "C2")
    rows = {r["name"]: r for r in o["rows"]}
    # nested Linear Add flattened into leaf factors
    assert rows["D"]["factor"] == pytest.approx(1.2)
    assert rows["L"]["factor"] == pytest.approx(1.6)
    assert rows["W"]["factor"] == pytest.approx(1.0)
    assert rows["D"]["contribution"] == pytest.approx(1.2 * WD * SPAN / 2)
    assert o["single_valued"] and o["check"]["ok"]
    assert rows["D"]["children"][0]["name"] == "DEAD"
    assert rows["D"]["children"][0]["factor"] == pytest.approx(1.2)


def test_envelope_governing_members(solved):
    m, R, left = solved
    o = lp.explain(m, R, {"kind": "reaction", "joint": left,
                          "component": "FZ"}, "ENV")
    rows = {r["name"]: r for r in o["rows"]}
    d, w = 1.4 * WD * SPAN / 2, -1.0 * (-HW * H_STORY / SPAN)
    assert rows["D"]["governs_max"] and not rows["W"]["governs_max"]
    assert rows["W"]["governs_min"]
    assert o["value"] == pytest.approx(max(d, w), rel=1e-9)
    assert o["value_min"] == pytest.approx(min(d, w), rel=1e-9)
    assert o["check"]["ok"] and o["check_min"]["ok"]
    assert any("Max governed by D" in n for n in o["notes"])


def test_srss_combo_shares(solved):
    m, R, left = solved
    o = lp.explain(m, R, {"kind": "reaction", "joint": left,
                          "component": "FZ"}, "SR")
    d, w = WD * SPAN / 2, HW * H_STORY / SPAN
    assert o["value"] == pytest.approx(math.hypot(d, w), rel=1e-9)
    rows = {r["name"]: r for r in o["rows"]}
    assert rows["D"]["srss_share"] == pytest.approx(d * d / (d * d + w * w))


def test_nonlinear_cases_are_never_resolved():
    m = portal()
    m.cases["ULS"].geometric = "pdelta"
    R = {"cases": {"ULS": {"reactions": {"1": [0, 0, 1, 0, 0, 0]}}}}
    o = lp.explain(m, R, {"kind": "reaction", "joint": "1",
                          "component": "FZ"}, "ULS")
    assert o["supported"] is False and "pdelta" in o["message"]
    assert o["value"] == 1
    m2 = portal()
    m2.members[0].axial_limit = "tension"
    o = lp.explain(m2, R, {"kind": "reaction", "joint": "1",
                           "component": "FZ"}, "ULS")
    assert o["supported"] is False and "tension" in o["message"]


def test_target_validation():
    m = portal()
    with pytest.raises(ValueError):
        lp.explain(m, {}, {"kind": "nope", "component": "FZ"}, "ULS")
    with pytest.raises(ValueError):
        lp.explain(m, {}, {"kind": "reaction", "component": "FZ"}, "ULS")
    with pytest.raises(ValueError):
        lp.explain(m, {}, {"kind": "member_force", "member": "B1",
                           "component": "M3", "station": 2}, "ULS")
    with pytest.raises(ValueError):
        lp.explain(m, {}, {"kind": "base", "component": "FX"}, "missing")
    t = lp.normalize_target({"kind": "member_force", "member": "B1",
                             "component": "m3", "station": "j"})
    assert t == {"kind": "member_force", "component": "M3", "member": "B1",
                 "station": 1.0}


# ----------------------------------------------------------- RS, 2-DOF model
E_COL, B_COL, M1, M2, SA = 30e6, 0.5, 20.0, 10.0, 0.3


def two_dof(method):
    """Fixed-base cantilever column, lumped masses at h and 2h (UX, UY)."""
    m = BuildingModel(name="2DOF")
    m.add_material(Material("C", E=E_COL, nu=0.2, unit_weight=0.0))
    m.add_section(FrameSection.rectangular("COL", "C", B_COL, B_COL))
    m.set_stories([H_STORY, H_STORY])
    m.diaphragm = "none"
    m.add_member("column", "COL", (0, 0, 0), (0, 0, H_STORY), uid="C1")
    m.add_member("column", "COL", (0, 0, H_STORY), (0, 0, 2 * H_STORY),
                 uid="C2")
    m.nodal_masses = [NodalMass((0.0, 0.0, H_STORY), mx=M1, my=M1),
                      NodalMass((0.0, 0.0, 2 * H_STORY), mx=M2, my=M2)]
    m.num_modes = 4
    m.add_rs_case("RS", "X", spectrum=[[0.0, SA], [20.0, SA]],
                  combo_method=method, damping=0.05)
    return m


def hand_modal():
    """Hand modal base shears of the 2-DOF cantilever (flexibility method)."""
    h, EI = H_STORY, E_COL * B_COL ** 4 / 12.0
    F = np.array([[h ** 3 / 3, 5 * h ** 3 / 6], [5 * h ** 3 / 6,
                                                 8 * h ** 3 / 3]]) / EI
    K = np.linalg.inv(F)
    Mm = np.diag([M1, M2])
    w2, phi = np.linalg.eig(np.linalg.solve(Mm, K))
    order = np.argsort(w2)
    w2, phi = w2[order], phi[:, order]
    sa = SA * G_ACCEL
    V = []
    for i in range(2):
        p = phi[:, i]
        gamma = p @ Mm @ np.ones(2) / (p @ Mm @ p)
        V.append(gamma * sa * (Mm @ p).sum())
    return np.sqrt(w2), np.array(V)


def cqc_rho(w, z):
    b = w[0] / w[1]
    r = 8 * z * z * (1 + b) * b ** 1.5 / ((1 - b * b) ** 2
                                           + 4 * z * z * b * (1 + b) ** 2)
    return np.array([[1.0, r], [r, 1.0]])


@pytest.mark.parametrize("method", ["SRSS", "CQC"])
def test_rs_modal_contributions_vs_hand(method):
    lp.clear_cache()
    m = two_dof(method)
    R = OpenSeesEngine(m).run().to_dict()
    o = lp.explain(m, R, {"kind": "base", "component": "FX"}, "RS")
    assert o["supported"] and o["case_kind"] == "response_spectrum"
    w, V = hand_modal()
    # per-mode values: the two X modes match the hand modal base shears
    vals = sorted((abs(r["modal_value"]) for r in o["rows"]), reverse=True)
    assert vals[0] == pytest.approx(max(abs(V)), rel=1e-6)
    assert vals[1] == pytest.approx(min(abs(V)), rel=1e-6)
    assert all(v < 1e-6 * vals[0] for v in vals[2:])        # Y modes
    rho = np.eye(2) if method == "SRSS" else cqc_rho(w, 0.05)
    hand = math.sqrt(V @ rho @ V)
    assert o["value"] == pytest.approx(hand, rel=1e-6)       # stored result
    assert o["computed_total"] == pytest.approx(o["value"], rel=1e-9)
    assert o["check"]["ok"]
    # shares sum to 1; cross terms: zero for SRSS, 2 rho12 V1 V2 for CQC
    assert sum(r["share"] for r in o["rows"]) == pytest.approx(1.0)
    cross = 0.0 if method == "SRSS" else 2 * rho[0, 1] * V[0] * V[1]
    assert o["cross_terms"] == pytest.approx(cross, rel=1e-5, abs=1e-9)


def test_th_governing_step():
    lp.clear_cache()
    m = two_dof("SRSS")
    dt = 0.02
    acc = [math.sin(2 * math.pi * 2.0 * k * dt) * (1 + k / 50) for k in
           range(150)]
    m.add_th_case("TH", "X", accel=acc, dt=dt, damping=0.05)
    R = OpenSeesEngine(m).run().to_dict()
    o = lp.explain(m, R, {"kind": "base", "component": "FX"}, "TH")
    ser = R["th_cases"]["TH"]["base_FX"]
    k = max(range(len(ser)), key=lambda i: abs(ser[i]))
    assert o["governing_step"] == k + 1
    assert o["governing_time"] == pytest.approx(R["th_cases"]["TH"]["t"][k])
    assert o["value"] == pytest.approx(ser[k])
    assert o["rows"][0]["governs"] and len(o["rows"]) == 10
    # a quantity without a recorded series -> message with the peak
    o2 = lp.explain(m, R, {"kind": "reaction", "joint": "1",
                           "component": "FZ"}, "TH")
    assert o2["supported"] is False and "not recorded" in o2["message"]


# ----------------------------------------------------------- API
def test_api_explain(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_HISTORY_DIR", str(tmp_path))
    from skyframe.api import server
    monkeypatch.setitem(server._state, "model", portal(cases=False))
    server._tables_store.clear()
    app = server.create_app()
    app.testing = True
    c = app.test_client()
    body = {"target": {"kind": "base", "component": "FX"}, "case": "ULS"}
    assert c.post("/api/explain", json=body).status_code == 409
    R = c.post("/api/analyze").get_json()
    left = _tag_at(R, (0, 0, 0))
    r = c.post("/api/explain", json={"target": {
        "kind": "reaction", "joint": left, "component": "FZ"},
        "case": "ULS"})
    assert r.status_code == 200
    out = r.get_json()
    hand = 1.2 * WD * SPAN / 2 + 1.6 * WL * SPAN / 2 - HW * H_STORY / SPAN
    assert out["value"] == pytest.approx(hand, rel=1e-9)
    assert out["target"]["label"].startswith("Reaction FZ")
    assert c.post("/api/explain", json={"target": {"kind": "x"},
                                        "case": "ULS"}).status_code == 400
    # explaining never changes the stored results or the model
    R2 = c.post("/api/analyze").get_json()
    assert R2["cases"] == R["cases"]
