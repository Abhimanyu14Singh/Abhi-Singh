"""Wave C HTTP endpoints + POST /api/model round trip of every new field
(CONTRACT "Wave C endpoints")."""

import pytest

from skyframe.core.model import BuildingModel


@pytest.fixture()
def client():
    from skyframe.api.server import create_app
    app = create_app()
    app.testing = True
    with app.test_client() as c:
        c.post("/api/model/quick", json={"stories": 3, "bays_x": 2,
                                          "bays_y": 2})
        yield c


def _model(c):
    return c.get("/api/model").get_json()


def test_model_round_trip_new_fields(client):
    d = _model(client)
    col = next(m for m in d["members"] if m["kind"] == "column")
    beam = next(m for m in d["members"] if m["kind"] == "beam")
    col["pier"] = "P1"
    d["pier_labels"] = ["P1", "P2"]
    pat = next(iter(d["patterns"]))
    s1 = d["stories"][0]["name"]
    d["patterns"][pat]["ecc_overrides"] = {s1: {"length": 1.5}}
    d["patterns"][pat]["member_loads"].append(
        {"member_uid": beam["uid"], "kind": "moment", "w": 2.0, "w2": 3.0,
         "a": 0.0, "b": 1.0, "direction": "local_3",
         "moment_dist": "trapezoid"})
    d["patterns"][pat]["member_loads"].append(
        {"member_uid": beam["uid"], "kind": "udl", "w": 1.0, "w2": 0.0,
         "a": 0.0, "b": 1.0, "direction": "local_1"})
    d["fpx_settings"] = {"SDS": 1.0, "Ie": 1.0}
    d["llr_settings"] = {"apply_to": ["columns", "beams"],
                         "beam_params": {"KLL": 2.0}}
    r = client.post("/api/model", json=d)
    assert r.status_code == 200, r.get_json()
    echo = r.get_json()
    r2 = client.post("/api/model", json=echo)
    assert r2.get_json() == echo                     # byte-stable
    assert echo["pier_labels"] == ["P1", "P2"]
    assert echo["patterns"][pat]["ecc_overrides"] == {s1: {"length": 1.5}}
    assert echo["patterns"][pat]["member_loads"][-2]["moment_dist"] == \
        "trapezoid"
    assert next(m for m in echo["members"]
                if m["uid"] == col["uid"])["pier"] == "P1"


def test_model_round_trip_legacy_unchanged(client):
    d = _model(client)
    r = client.post("/api/model", json=d)
    assert r.get_json() == d


def test_fpx_endpoints(client):
    r = client.post("/api/fpx", json={"pattern": "EQX", "SDS": 1.0,
                                      "Ie": 1.0})
    assert r.status_code == 200, r.get_json()
    rep = r.get_json()["reports"][0]
    assert rep["pattern"] == "EQX" and rep["direction"] == "X"
    assert len(rep["stories"]) == 3
    top = rep["stories"][0]
    assert top["Fpx"] == pytest.approx(min(max(top["Fpx_eq"],
                                               top["Fpx_min"]),
                                           top["Fpx_max"]))
    # settings path: every quake pattern
    r = client.post("/api/fpx", json={"SDS": 1.0})
    assert {x["pattern"] for x in r.get_json()["reports"]} == {"EQX",
                                                                "EQY"}
    r = client.post("/api/pattern/fpx", json={"pattern": "EQX",
                                              "SDS": 1.0})
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert len(out["patterns"]) == 3
    assert all(n in out["model"]["patterns"] for n in out["patterns"])
    assert client.post("/api/fpx", json={"pattern": "EQX",
                                         "SDS": -1}).status_code == 400
    assert client.post("/api/pattern/fpx", json={}).status_code == 400


def test_llr_beams_and_ecc_endpoints(client):
    r = client.post("/api/llr/beams", json={})
    assert r.status_code == 200, r.get_json()
    b = r.get_json()["beams"]
    assert b and all(v["n"] == 1 for v in b.values())
    d = _model(client)
    s1 = d["stories"][0]["name"]
    d["patterns"]["EQX"]["accidental_torsion"] = True
    d["patterns"]["EQX"]["ecc_overrides"] = {s1: {"length": 2.0}}
    r = client.post("/api/pattern/ecc-torques",
                    json={"pattern": "EQX", "model": d})
    rows = r.get_json()["rows"]
    f = next(x for x in rows if x["story"] == s1)
    assert f["mz_x"] == pytest.approx(f["fx"] * 2.0)
    assert client.post("/api/pattern/ecc-torques",
                       json={"pattern": "NOPE"}).status_code == 400


def test_run_with_new_loads(client):
    d = _model(client)
    beam = next(m for m in d["members"] if m["kind"] == "beam")
    col = next(m for m in d["members"] if m["kind"] == "column")
    col["pier"] = "PC"
    d["patterns"]["DEAD"]["member_loads"].append(
        {"member_uid": beam["uid"], "kind": "moment", "w": 2.0, "w2": 2.0,
         "a": 0.0, "b": 1.0, "direction": "local_1",
         "moment_dist": "uniform"})
    assert client.post("/api/model", json=d).status_code == 200
    r = client.post("/api/analyze", json={})
    assert r.status_code == 200, r.get_json()
    res = r.get_json()
    assert col["story"] in res["piers"]["DEAD"]["PC"]
    BuildingModel.from_dict(d)
