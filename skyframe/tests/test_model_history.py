"""Model history + structural diff (core/model_diff.py, core/model_history.py,
api/model_history_api.py).  CONTRACT "Model history and visual diff"."""

import copy
import json
import time

import pytest

from skyframe.core import model_diff as md
from skyframe.core import model_history as mh
from skyframe.core.builder import quick_building
from skyframe.core.model import BuildingModel


@pytest.fixture()
def base():
    return quick_building().to_dict()


def _items(d, **kw):
    return [i for i in d["items"]
            if all(i.get(k) == v for k, v in kw.items())]


# --------------------------------------------------------------------- diff
def test_identical_models_are_equal(base):
    d = md.diff(base, copy.deepcopy(base))
    assert d["equal"] and d["n_items"] == 0
    assert d["summary"]["frames"]["unchanged"] == len(base["members"])


def test_field_change_add_remove(base):
    b = copy.deepcopy(base)
    uid0 = b["members"][0]["uid"]
    b["members"][0]["section"] = "BEAM"
    gone = b["members"].pop(3)
    new = copy.deepcopy(b["members"][5])
    new["uid"] = "NEW1"
    new["pi"], new["pj"] = [99.0, 0.0, 0.0], [99.0, 0.0, 3.2]
    b["members"].append(new)
    d = md.diff(base, b)
    s = d["summary"]["frames"]
    assert (s["added"], s["removed"], s["changed"]) == (1, 1, 1)
    ch = _items(d, type="frames", status="changed")[0]
    assert ch["key"] == uid0
    assert ch["fields"] == [{"path": "section", "a": "COL", "b": "BEAM"}]
    rem = _items(d, type="frames", status="removed")[0]
    assert rem["key"] == gone["uid"] and rem["geom"]["pi"] == gone["pi"]
    assert _items(d, type="frames", status="added")[0]["key"] == "NEW1"
    # the new member brings two new joints (one end on the ground)
    assert d["summary"]["joints"]["added"] == 2


def test_rename_matched_by_geometry(base):
    b = copy.deepcopy(base)
    old = b["members"][7]["uid"]
    b["members"][7]["uid"] = "RENAMED"
    # reversed orientation still matches (orientation-free key)
    b["members"][7]["pi"], b["members"][7]["pj"] = \
        b["members"][7]["pj"], b["members"][7]["pi"]
    d = md.diff(base, b)
    it = _items(d, type="frames")
    assert len(it) == 1 and it[0]["status"] == "changed"
    assert it[0]["match"] == "geometry"
    assert it[0]["key_a"] == old and it[0]["key_b"] == "RENAMED"
    assert it[0]["fields"][0] == {"path": "uid", "a": old, "b": "RENAMED"}


def test_material_rename_matched_by_content(base):
    b = copy.deepcopy(base)
    mat = b["materials"].pop("CONC")
    mat["name"] = "C30"
    b["materials"]["C30"] = mat
    d = md.diff(base, b)
    it = _items(d, type="materials")
    assert len(it) == 1 and it[0]["match"] == "content"
    assert it[0]["status"] == "changed"


def test_loads_cases_combos_stories_options(base):
    b = copy.deepcopy(base)
    beam = next(m for m in b["members"] if m["kind"] == "beam")
    pat = b["patterns"]["DEAD"]
    loads_on = [x for x in pat["member_loads"]
                if x.get("member_uid") == beam["uid"]]
    if loads_on:
        loads_on[0]["w"] = loads_on[0]["w"] * 2
    else:
        pat["member_udls"] = [dict(u, w=u["w"] * 2) if u["member_uid"] ==
                              beam["uid"] else u for u in pat["member_udls"]]
    pat["self_weight_factor"] = 1.0
    b["cases"]["LIVE"]["patterns"]["LIVE"] = 1.25
    combo = next(iter(b["combos"]))
    b["combos"][combo]["cases"]["DEAD"] = 1.4
    b["stories"][1]["height"] = 3.6
    b["num_modes"] = 6
    b["supports"].append({"point": [0.0, 0.0, 0.0],
                          "restraints": [1, 1, 1, 0, 0, 0]})
    d = md.diff(base, b)
    keys = {(i["type"], i["key"]) for i in d["items"]}
    assert ("loads", "DEAD") in keys
    assert any(t == "loads" and k.startswith("DEAD|") and k.endswith(
        "frame:" + beam["uid"]) for t, k in keys)
    assert ("cases", "static/LIVE") in keys
    assert ("combos", combo) in keys
    assert ("stories", b["stories"][1]["name"]) in keys
    assert ("options", "num_modes") in keys
    assert ("supports", "restraint@0,0,0") in keys
    sw = _items(d, type="loads", key="DEAD")[0]["fields"]
    assert {"path": "self_weight_factor", "a": 0.0, "b": 1.0} in sw


def test_float_noise_below_tolerance_is_not_a_change(base):
    b = copy.deepcopy(base)
    b["members"][0]["angle"] = 1e-15
    assert md.diff(base, b)["equal"]
    assert not md.diff(base, b, tol=0.0)["equal"]


# --------------------------------------------------------------------- restore
def test_restore_all_reproduces_reference(base):
    b = copy.deepcopy(base)
    b["members"][0]["section"] = "BEAM"
    del b["members"][3]
    b["members"][7]["uid"] = "REN7"
    b["materials"]["CONC2"] = dict(b["materials"]["CONC"], name="CONC2")
    b["patterns"]["DEAD"]["self_weight_factor"] = 1.1
    del b["combos"][next(iter(b["combos"]))]
    b["stories"][0]["height"] += 0.1
    b["num_modes"] = 7
    new, rep = md.apply_restore(b, base)
    assert new == base
    assert md.diff(base, new)["equal"]
    # round trip through the model: byte-identical JSON
    s1 = json.dumps(BuildingModel.from_dict(new).to_dict())
    assert s1 == json.dumps(BuildingModel.from_dict(base).to_dict())


def test_restore_selected_objects_only(base):
    b = copy.deepcopy(base)
    b["members"][0]["section"] = "BEAM"
    gone = b["members"].pop(3)
    b["num_modes"] = 7
    new, rep = md.apply_restore(b, base, [{"type": "frames",
                                           "key": gone["uid"]}])
    assert [m["uid"] for m in new["members"]] == \
        [m["uid"] for m in base["members"]]          # back at its place
    assert new["members"][0]["section"] == "BEAM"     # untouched edit kept
    assert new["num_modes"] == 7
    assert rep["restored"] == [{"type": "frames", "key": gone["uid"],
                                "status": "removed"}]
    new2, rep2 = md.apply_restore(b, base, [{"type": "frames",
                                             "key": "nope"}])
    assert rep2["skipped"][0]["reason"] == "no difference"
    assert new2 == b


def test_restore_load_group(base):
    b = copy.deepcopy(base)
    lst = b["patterns"]["DEAD"]["member_udls"] or \
        b["patterns"]["DEAD"]["member_loads"]
    lst[0]["w"] = 123.0
    d = md.diff(base, b)
    it = _items(d, type="loads")
    assert len(it) == 1
    new, _ = md.apply_restore(b, base, [{"type": "loads",
                                         "key": it[0]["key"]}])
    assert new == base


# --------------------------------------------------------------------- store
def test_checkpoint_bytes_round_trip(tmp_path, base):
    h = mh.History("My Model", root=str(tmp_path))
    out = h.add(base, name="Baseline", note="n1")
    cp = out["checkpoint"]
    assert cp["name"] == "Baseline" and cp["kind"] == "manual"
    meta, m = h.get(cp["id"])
    assert m == base
    assert h.raw(cp["id"]) == mh.canonical_bytes(base)
    # restore round trip of the whole checkpoint is byte-identical
    assert json.dumps(BuildingModel.from_dict(m).to_dict()) == \
        json.dumps(base)
    h.update(cp["id"], name="B2", pinned=True)
    assert h.listing()["checkpoints"][0]["name"] == "B2"
    assert h.delete(cp["id"]) and not h.listing()["checkpoints"]


def test_auto_dedupe_and_pruning(tmp_path, base):
    h = mh.History("p", root=str(tmp_path))
    h.settings(max_auto=3, max_checkpoints=6)
    first = h.add(base, kind="auto", reason="before analysis")
    again = h.add(base, kind="auto", reason="before analysis")
    assert again["skipped"] and again["checkpoint"]["id"] == \
        first["checkpoint"]["id"]
    pinned = h.add(dict(base, name="pin"), name="pinned")["checkpoint"]
    h.update(pinned["id"], pinned=True)
    ids = []
    for i in range(5):
        r = h.add(dict(base, num_modes=20 + i), kind="auto", reason="save")
        ids.append(r["checkpoint"]["id"])
    lst = h.listing()["checkpoints"]
    autos = [c for c in lst if c["kind"] == "auto"]
    assert len(autos) == 3                        # max_auto ring
    assert {c["id"] for c in autos} == set(ids[-3:])
    for i in range(6):
        h.add(dict(base, num_modes=40 + i), name=f"m{i}")
    lst = h.listing()["checkpoints"]
    assert len(lst) <= 6
    assert any(c["id"] == pinned["id"] for c in lst)    # pinned survives
    # files on disk follow the index
    files = {p.name for p in (tmp_path / "p").glob("*.json.gz")}
    assert files == {c["id"] + ".json.gz" for c in lst}
    # byte limit prunes the oldest unpinned
    size = lst[0]["bytes"]
    r = h.settings(max_bytes=64 * 1024)
    assert sum(c["bytes"] for c in h.listing()["checkpoints"]) <= max(
        64 * 1024, size)
    assert r["pruned"] or len(lst) * size <= 64 * 1024


def test_settings_validation(tmp_path):
    h = mh.History("x", root=str(tmp_path))
    with pytest.raises(ValueError):
        h.settings(max_checkpoints=1)
    with pytest.raises(ValueError):
        h.settings(max_auto="many")


def test_sanitize_key():
    k = mh.sanitize_key("../../etc/passwd")
    assert "/" not in k and "\\" not in k and k not in (".", "..")
    assert mh.sanitize_key("..") == "Untitled"
    assert mh.sanitize_key("") == "Untitled"
    assert mh.sanitize_key("Tower A.v2") == "Tower A.v2"


# --------------------------------------------------------------------- perf
def test_large_model_diff_under_one_second():
    from skyframe.core.templates import build_template
    m, _ = build_template("rc_dual_core", {"stories": 50})
    a = m.to_dict()
    b = copy.deepcopy(a)
    for mm in b["members"][::50]:
        mm["section"] = mm["section"]  # untouched
    b["members"][100]["angle"] = 15.0
    del b["shells"][10]
    b["patterns"]["SDL"]["area_loads"][0]["q"] = 2.0
    t0 = time.perf_counter()
    d = md.diff(a, b)
    dt = time.perf_counter() - t0
    assert len(a["members"]) >= 4000 and len(a["shells"]) >= 1000
    assert d["summary"]["frames"]["changed"] == 1
    assert d["summary"]["shells"]["removed"] == 1
    assert dt < 1.0, f"50-story diff took {dt:.3f} s"


# --------------------------------------------------------------------- API
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("SKYFRAME_HISTORY_DIR", str(tmp_path / "hist"))
    from skyframe.api import server
    monkeypatch.setitem(server._state, "model", quick_building())
    server._state.pop("history_key", None)
    app = server.create_app()
    app.testing = True
    return app.test_client()


def test_api_checkpoint_compare_restore(client):
    m0 = client.get("/api/model").get_json()
    r = client.post("/api/history/checkpoint", json={"name": "base"})
    assert r.status_code == 200
    cid = r.get_json()["checkpoint"]["id"]
    lst = client.get("/api/history").get_json()
    assert lst["key"] == "Quick Building" and len(lst["checkpoints"]) == 1
    m1 = copy.deepcopy(m0)
    m1["members"][0]["section"] = "BEAM"
    gone = m1["members"].pop(2)["uid"]
    d = client.post("/api/history/compare",
                    json={"a": cid, "b": m1}).get_json()
    assert d["summary"]["frames"] == {"added": 0, "removed": 1,
                                      "changed": 1,
                                      "unchanged": len(m0["members"]) - 2}
    # partial restore into the SPA's working copy
    r = client.post("/api/history/restore",
                    json={"from": cid, "into": m1,
                          "items": [{"type": "frames", "key": gone}]})
    out = r.get_json()["model"]
    assert [m["uid"] for m in out["members"]] == \
        [m["uid"] for m in m0["members"]]
    assert out["members"][0]["section"] == "BEAM"
    # whole checkpoint: byte-identical to what was saved
    r = client.post("/api/history/restore", json={"from": cid})
    assert json.dumps(r.get_json()["model"]) == json.dumps(m0)
    # an invalid partial restore is refused with a reason
    m2 = copy.deepcopy(m0)
    m2["sections"]["NEWSEC"] = dict(m2["sections"]["BEAM"], name="NEWSEC")
    m2["members"][0]["section"] = "NEWSEC"
    r = client.post("/api/history/restore",
                    json={"from": cid, "into": m2,
                          "items": [{"type": "sections",
                                     "key": "NEWSEC"}]})
    assert r.status_code == 400 and "not valid" in r.get_json()["error"]
    # apply makes it the server model
    r = client.post("/api/history/restore", json={"from": cid,
                                                  "apply": True})
    assert client.get("/api/model").get_json() == m0
    assert client.delete(f"/api/history/checkpoint/{cid}").status_code == 200
    assert client.delete(f"/api/history/checkpoint/{cid}").status_code == 404
    assert client.get("/api/history/checkpoint/zzz").status_code == 404


def test_api_auto_checkpoints_save_and_analysis(client):
    r = client.post("/api/models/tower one")
    assert r.status_code == 200
    lst = client.get("/api/history").get_json()
    assert lst["key"] == "tower one"
    assert [c["reason"] for c in lst["checkpoints"]] == ["save"]
    # analysis of the same model: deduplicated against the save checkpoint
    assert client.post("/api/analyze").status_code == 200
    assert len(client.get("/api/history").get_json()["checkpoints"]) == 1
    m = client.get("/api/model").get_json()
    m["num_modes"] = 5
    client.post("/api/model", json=m)
    assert client.post("/api/analyze").status_code == 200
    reasons = [c["reason"] for c in
               client.get("/api/history").get_json()["checkpoints"]]
    assert reasons == ["before analysis", "save"]
    # explicit key from the SPA
    assert client.post("/api/history/key", json={"key": "other"}
                       ).get_json()["key"] == "other"
    assert client.get("/api/history").get_json()["checkpoints"] == []


def test_auto_checkpoints_do_not_change_results(client):
    """The hooks only read the model: results identical with and without."""
    import os
    r1 = client.post("/api/analyze").get_json()
    os.environ["SKYFRAME_HISTORY_AUTO"] = "off"
    try:
        r2 = client.post("/api/analyze").get_json()
    finally:
        os.environ.pop("SKYFRAME_HISTORY_AUTO")
    for k in ("cases", "combos", "modal"):
        assert json.dumps(r1[k], sort_keys=True) == \
            json.dumps(r2[k], sort_keys=True)


# --------------------------------------------------------------------- e2e
@pytest.mark.skipif(__import__("os").environ.get("SKYFRAME_E2E") != "1",
                    reason="browser check: set SKYFRAME_E2E=1 (needs "
                    "playwright + chromium)")
@pytest.mark.parametrize("mode", ["mock", "live"])
def test_e2e_beyond_tools(tmp_path, mode):
    """tests/e2e/beyond_tools.e2e.mjs: Model History + Explain Result."""
    import os
    import shutil
    import socket
    import subprocess
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    node = shutil.which("node") or "/opt/node22/bin/node"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, SKYFRAME_MODELS_DIR=str(tmp_path / "m"),
               SKYFRAME_HISTORY_DIR=str(tmp_path / "h"),
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    srv = subprocess.Popen(
        [sys.executable, "-c", "from skyframe.api.server import create_app; "
         f"create_app().run(host='127.0.0.1', port={port})"],
        cwd=os.path.join(here, ".."), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        res = subprocess.run(
            [node, os.path.join(here, "e2e", "beyond_tools.e2e.mjs"), mode,
             f"http://127.0.0.1:{port}"],
            capture_output=True, text=True, timeout=900, env=env)
        assert res.returncode == 0, res.stdout + res.stderr
    finally:
        srv.terminate()
        srv.wait(10)
