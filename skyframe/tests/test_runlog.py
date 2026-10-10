"""Model info, analysis run log (per-case run times + warnings) and the
report-data aggregate."""
import json

import pytest

from skyframe.core import runlog as rl
from skyframe.core.builder import quick_building


def test_model_info_counts():
    m = quick_building(bays_x=3, bays_y=2, stories=4)
    info = rl.model_info(m)
    c = info["counts"]
    assert c["stories"] == 4
    assert c["frames"] == len(m.members)
    # 4 x 3 column lines x 5 levels of joints
    assert c["joints"] == 4 * 3 * 5
    assert c["load_patterns"] == len(m.patterns)
    assert c["load_cases"] == len(m.cases)
    assert info["members_by_kind"]["column"] == 4 * 3 * 4
    assert info["height"] == pytest.approx(m.stories[-1].elevation)
    assert info["plan"] == [pytest.approx(18.0), pytest.approx(12.0)]
    json.dumps(info)


def test_build_run_log_warnings_and_times():
    m = quick_building()
    res = {"model_name": "x",
           "warning": "time-history cases skipped: too many; "
                      "combo 'C1' skipped: case(s) ['A'] not run",
           "case_status": {"DEAD": "finished", "LIVE": "not_run"},
           "combo_status": {"C1": "skipped"},
           "cases": {"DEAD": {"warnings": ["w1", "w1"]}},
           "pushover": {"PO": {"warnings": ["nc"]}},
           "modal": {"periods": [1.0, 0.5]}}
    log = rl.build_run_log(m, res, {"DEAD": 0.25, "MODAL": 0.5}, 1.5,
                           ["UserWarning: hi", "UserWarning: hi"],
                           started="2026-01-01T00:00:00+00:00")
    assert log["total_s"] == 1.5
    assert log["started"] == "2026-01-01T00:00:00+00:00"
    by = {c["name"]: c for c in log["cases"]}
    assert by["DEAD"]["time_s"] == 0.25 and by["DEAD"]["status"] == \
        "finished"
    assert by["LIVE"]["time_s"] is None and by["LIVE"]["status"] == \
        "not_run"
    assert by["MODAL"]["time_s"] == 0.5
    msgs = [(w["source"], w["message"]) for w in log["warnings"]]
    assert msgs == [("run", "time-history cases skipped: too many"),
                    ("run", "combo 'C1' skipped: case(s) ['A'] not run"),
                    ("DEAD", "w1"), ("PO", "nc"),
                    ("engine", "UserWarning: hi")]
    assert log["combos"] == [{"name": "C1", "status": "skipped"}]
    assert log["counts"]["modes"] == 2


def test_report_data_without_results():
    m = quick_building()
    d = rl.report_data(m, None, None, None)
    assert d["results_available"] is False and d["tables"] == {}
    assert d["model_info"]["counts"]["frames"] == len(m.members)


def test_server_run_log_and_report_data():
    pytest.importorskip("openseespy")
    from skyframe.api import server
    c = server.create_app().test_client()
    assert c.post("/api/model/quick", json={"stories": 2}).status_code == 200
    # no run yet for this model state
    info = c.get("/api/model/info").get_json()
    assert info["counts"]["stories"] == 2
    rd = c.post("/api/report/data", json={}).get_json()
    if not rd["results_available"]:
        assert rd["tables"] == {}
    res = c.post("/api/analyze").get_json()
    log = c.get("/api/analyze/log").get_json()
    assert log["available"] is True and log["current"] is True
    assert log["total_s"] > 0
    names = {x["name"]: x for x in log["cases"]}
    for n in res["cases"]:
        assert names[n]["time_s"] is not None and names[n]["time_s"] >= 0
    assert names["MODAL"]["kind"] == "modal"
    assert names["MODAL"]["time_s"] is not None
    assert sum(x["time_s"] or 0 for x in log["cases"]) <= log["total_s"]
    info = c.get("/api/model/info").get_json()
    assert info["last_analysis"]["current"] is True
    rd = c.post("/api/report/data",
                json={"tables": ["load_case_equilibrium", "modal_periods",
                                 "nope"]}).get_json()
    assert rd["results_available"] is True
    eq = rd["tables"]["load_case_equilibrium"]["rows"]
    assert {r["case"] for r in eq} >= set(res["cases"])
    for r in eq:
        assert r["error_pct"] < 1e-3
    assert len(rd["tables"]["modal_periods"]["rows"]) == \
        len(res["modal"]["periods"])
    assert "error" in rd["tables"]["nope"]
    assert rd["run_log"]["total_s"] == log["total_s"]
    assert c.post("/api/report/data",
                  json={"tables": "x"}).status_code == 400
    # a model change marks the log stale
    c.post("/api/model/quick", json={"stories": 3})
    assert c.get("/api/analyze/log").get_json()["current"] is False


def test_analyze_response_unchanged_by_log():
    """The /api/analyze payload carries no run-log keys (byte-identical
    results); timings live only behind /api/analyze/log."""
    pytest.importorskip("openseespy")
    from skyframe.api import server
    from skyframe.engine.opensees_engine import OpenSeesEngine
    c = server.create_app().test_client()
    c.post("/api/model/quick", json={"stories": 2})
    res = c.post("/api/analyze").get_json()
    direct = OpenSeesEngine(quick_building(stories=2)).run().to_dict()
    assert set(res) == set(json.loads(json.dumps(direct)))
