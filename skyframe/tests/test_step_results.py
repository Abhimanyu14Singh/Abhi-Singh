"""Wave G: step / envelope states of multi-step cases for the graphics
(``skyframe.engine.step_results``, ``POST /api/results/steps``).

Pinned against closed-form / hand-derived values:

* staged column (P = 100 / 200 / 300 kN, EA = 4.8e6 kN, h = 3 m): stage-1
  top-of-story-1 settlement = -P1 h / EA; column axials after stage j =
  the loads applied so far (|N_C1| = 100, 300, 600 kN); the last stage
  equals the final staged state exactly (per-story AND user stages);
* elastic nonlinear static load control: step i = (i / n) x the final
  state (linearity, 1e-9) and the last step = the case's final state;
* elastic pushover (no hinges): the monitored roof DOF at step k equals
  the capacity-curve roof displacement (exact) and states scale linearly;
* linear time history: the recorded roof displacement equals the story
  history at that step (exact); the base shear from the recorded column
  station shears equals the base reaction history (statics, 1e-7); the
  base moment of a column = V L (no span load); the envelope max / min
  of the roof displacement equal the max / min of the history;
* the recorder only READS the domain: run() results with a request set
  are byte-identical to a plain run; the endpoint never changes the
  model and caches its answer; bad bodies are 400.
"""

from __future__ import annotations

import json
import math

import pytest

from skyframe.core.builder import quick_building
from skyframe.core.model import BuildingModel, FrameSection, Material, NodalLoad
from skyframe.engine import step_results as SR
from skyframe.engine.opensees_engine import OpenSeesEngine

E = 30.0e6
H = 3.0
P = (100.0, 200.0, 300.0)
EA = E * 0.16


def _column(user: bool):
    mdl = BuildingModel(name="col")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("C30", E=E, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "C30", 0.4, 0.4))
    mdl.set_stories([H, H, H])
    pat = mdl.pattern("DEAD")
    for i in range(3):
        mdl.add_member("column", "COL", (0, 0, i * H), (0, 0, (i + 1) * H),
                       story=f"Story{i + 1}", uid=f"C{i + 1}")
        pat.nodal_loads.append(NodalLoad((0, 0, (i + 1) * H), fz=-P[i]))
        mdl.add_group(f"G{i + 1}", members=[f"C{i + 1}"],
                      points=[[0, 0, (i + 1) * H]])
    if user:
        mdl.add_staged_case("U", stages=[
            {"name": f"S{i + 1}", "operations": [
                {"op": "add", "group": f"G{i + 1}"},
                {"op": "load", "group": f"G{i + 1}", "pattern": "DEAD"}]}
            for i in range(3)])
    else:
        mdl.add_staged_case("PS", pattern="DEAD")
    mdl.validate()
    return mdl


def _tag_at(results, z):
    return next(t for t, c in results["nodes"].items()
                if abs(c[2] - z) < 1e-9 and abs(c[0]) < 1e-9
                and abs(c[1]) < 1e-9)


@pytest.mark.parametrize("user", [False, True])
def test_staged_stage_states_closed_form(user):
    mdl = _column(user)
    name = "U" if user else "PS"
    pay = SR.run_steps(mdl, {"case": name, "frames": 40, "members": True,
                             "envelope": False})
    assert pay["kind"] == "staged" and pay["steps"] == [1, 2, 3]
    assert pay["labels"] == (["S1", "S2", "S3"] if user
                             else ["Story1", "Story2", "Story3"])
    res = OpenSeesEngine(mdl).run().to_dict()
    t1 = _tag_at(res, H)
    # stage 1: only story 1 built and loaded -> uz = -P1 h / EA
    uz1 = pay["node_disp"]["1"][t1][2]
    assert uz1 == pytest.approx(-P[0] * H / EA, rel=1e-9)
    # column axials = loads applied so far (statics)
    for j, expect in ((1, 100.0), (2, 300.0), (3, 600.0)):
        n = pay["member_stations"][str(j)]["C1"]["N"]
        assert abs(n[0]) == pytest.approx(expect, rel=1e-9)
        assert abs(n[-1]) == pytest.approx(expect, rel=1e-9)
    # last stage == the final staged state (no include_live)
    fin = res["staged"][name]
    last = pay["node_disp"]["3"]
    for t, v in fin["node_disp"].items():
        assert last[t] == pytest.approx(v, abs=1e-15)
    for uid, st in fin["member_stations"].items():
        got = pay["member_stations"]["3"][uid]
        for k in ("N", "V2", "V3", "T", "M2", "M3"):
            assert got[k] == pytest.approx(st[k], abs=1e-12)


def _frame():
    # asymmetric plan (no repeated periods) -> deterministic eigen solves
    return quick_building(bays_x=2, bays_y=1, stories=2)


def test_nonlinear_static_steps_scale_linearly():
    m = _frame()
    m.add_nonlinear_static_case("NLG", {"DEAD": 1.0, "EQX": 1.0}, steps=5)
    m.validate()
    pay = SR.run_steps(m, {"case": "NLG", "frames": 400, "members": True,
                           "envelope": False})
    assert pay["kind"] == "nonlinear_static"
    assert pay["steps"] == [0, 1, 2, 3, 4, 5]
    assert pay["axis"]["values"] == pytest.approx([0, .2, .4, .6, .8, 1.0])
    fin = OpenSeesEngine(m).run().to_dict()["nonlinear_static"]["NLG"]
    nd5 = pay["node_disp"]["5"]
    for t, v in fin["node_disp"].items():
        assert nd5[t] == pytest.approx(v, rel=1e-9, abs=1e-14)
    for i in (1, 2, 3):
        for t, v in nd5.items():
            assert pay["node_disp"][str(i)][t] == pytest.approx(
                [x * i / 5 for x in v], rel=1e-7, abs=1e-12)
        for uid, st in pay["member_stations"]["5"].items():
            got = pay["member_stations"][str(i)][uid]
            assert got["M3"] == pytest.approx([x * i / 5 for x in st["M3"]],
                                              rel=1e-7, abs=1e-8)
    for uid, st in fin["member_stations"].items():
        assert pay["member_stations"]["5"][uid]["M3"] == pytest.approx(
            st["M3"], rel=1e-9, abs=1e-9)


def test_pushover_steps_follow_the_capacity_curve():
    m = _frame()
    m.add_pushover_case("PUSH", "X", steps=12, target_drift=0.01)
    m.validate()
    pay = SR.run_steps(m, {"case": "PUSH", "frames": 4, "members": True,
                           "envelope": True})
    assert pay["kind"] == "pushover" and pay["steps"] == [3, 6, 9, 12]
    po = OpenSeesEngine(m).run_pushover("PUSH")
    for s, rd in zip(pay["steps"], pay["axis"]["values"]):
        assert rd == po.roof_disp[s - 1]
    mon = po.control["node"]
    for s in pay["steps"]:
        assert pay["node_disp"][str(s)][str(mon)][0] == pytest.approx(
            po.roof_disp[s - 1], rel=1e-12)
    # elastic: the states scale with the step (linear)
    a, b = pay["node_disp"]["3"], pay["node_disp"]["12"]
    for t in a:
        assert a[t][0] == pytest.approx(b[t][0] * 3 / 12, rel=1e-7, abs=1e-12)
    env = pay["envelope"]
    assert env["stride"] == 1 and env["samples"] == 12
    assert env["max"]["node_disp"][str(mon)][0] == pytest.approx(
        max(po.roof_disp))


def test_time_history_steps_statics_and_envelope():
    m = _frame()
    dt = 0.01
    acc = [0.0] + [3.0 * math.sin(2 * math.pi * 1.7 * k * dt)
                   for k in range(1, 160)]
    m.add_th_case("THX", "X", accel=acc, dt=dt)
    m.validate()
    pay = SR.run_steps(m, {"case": "THX", "frames": 8, "members": True,
                           "envelope": True})
    assert pay["kind"] == "th" and len(pay["steps"]) == 8
    assert pay["steps"][-1] == 160
    eng = OpenSeesEngine(m)
    th = eng.run_time_history("THX")
    roof = m.stories[-1].name
    asm = eng._asm
    master = asm.masters[roof]
    for s, t in zip(pay["steps"], pay["axis"]["values"]):
        assert t == pytest.approx(s * dt)
        assert pay["node_disp"][str(s)][str(master)][0] == pytest.approx(
            th.story_ux[roof][s - 1], rel=1e-12, abs=1e-15)
        # base shear from the column station shears == base reaction
        cols = [mm for mm in m.members if mm.kind == "column"
                and min(mm.pi[2], mm.pj[2]) < 1e-9]
        v2 = sum(pay["member_stations"][str(s)][c.uid]["V2"][0] for c in cols)
        v3 = sum(pay["member_stations"][str(s)][c.uid]["V3"][0] for c in cols)
        assert math.hypot(v2, v3) == pytest.approx(abs(th.base_FX[s - 1]),
                                                   rel=1e-7)
        # cantilever-type statics per column: M at the base = V * L at the
        # top minus M at the top (no span load): M(0) - M(L) = V L
        c = cols[0]
        st = pay["member_stations"][str(s)][c.uid]
        L = st["x"][-1]
        for vk, mk in (("V2", "M3"), ("V3", "M2")):
            assert abs(st[mk][0] - st[mk][-1]) == pytest.approx(
                abs(st[vk][0]) * L, rel=1e-7, abs=1e-9)
    env = pay["envelope"]
    assert env["max"]["node_disp"][str(master)][0] == pytest.approx(
        max(th.story_ux[roof]), rel=1e-12)
    assert env["min"]["node_disp"][str(master)][0] == pytest.approx(
        min(th.story_ux[roof]), rel=1e-12)
    # recorded steps lie inside the envelope
    for s in pay["steps"]:
        for uid, stv in pay["member_stations"][str(s)].items():
            hi = env["max"]["member_stations"][uid]["M3"]
            lo = env["min"]["member_stations"][uid]["M3"]
            for v, a, b in zip(stv["M3"], lo, hi):
                assert a - 1e-9 <= v <= b + 1e-9


def test_recorder_reads_only_results_identical():
    m = _frame()
    acc = [0.0] + [2.0 * math.sin(0.3 * k) for k in range(1, 60)]
    m.add_th_case("THX", "X", accel=acc, dt=0.01)
    m.add_nonlinear_static_case("NLG", {"DEAD": 1.0}, steps=3)
    m.add_staged_case("STG", "DEAD", include_live={"LIVE": 1.0})
    m.validate()
    plain = json.dumps(OpenSeesEngine(m).run().to_dict(), sort_keys=True)
    for case in ("THX", "NLG", "STG"):
        eng = OpenSeesEngine(m)
        eng._gres_req = {"case": case, "frames": 5, "members": True,
                         "envelope": True}
        got = json.dumps(eng.run().to_dict(), sort_keys=True)
        assert got == plain
        assert case in eng._gres_out
    # staged with include_live: the final state is the last step
    pay = SR.run_steps(m, {"case": "STG", "frames": 40, "members": True,
                           "envelope": False})
    assert pay["labels"][-1] == "Final"
    fin = json.loads(plain)["staged"]["STG"]
    for t, v in fin["node_disp"].items():
        assert pay["node_disp"][str(pay["steps"][-1])][t] == pytest.approx(
            v, abs=1e-15)


def test_pick_steps_rule():
    assert SR.pick_steps(10, 4) == [2, 5, 8, 10]
    assert SR.pick_steps(3, 40) == [1, 2, 3]
    assert SR.pick_steps(0, 5) == []
    assert SR.pick_steps(7, 1) == [7]


def test_endpoint_validation_cache_and_model_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api.server import create_app
    app = create_app()
    c = app.test_client()
    m = _frame()
    m.add_nonlinear_static_case("NLG", {"DEAD": 1.0}, steps=2)
    m.validate()
    assert c.post("/api/model", json=m.to_dict()).status_code == 200
    before = c.get("/api/model").get_json()
    assert c.post("/api/results/steps", json={}).status_code == 400
    assert c.post("/api/results/steps", json={"case": "NOPE"}).status_code == 400
    assert c.post("/api/results/steps", json={"case": "NLG", "frames": 0}
                  ).status_code == 400
    assert c.post("/api/results/steps", json={"case": "DEAD"}).status_code == 400
    r1 = c.post("/api/results/steps", json={"case": "NLG", "frames": 2})
    assert r1.status_code == 200
    j1 = r1.get_json()
    assert j1["cached"] is False and j1["steps"] == [0, 1, 2]
    j2 = c.post("/api/results/steps", json={"case": "NLG", "frames": 2}
                ).get_json()
    assert j2["cached"] is True and j2["node_disp"] == j1["node_disp"]
    # lazy member stations: the index omits them, slices come from the cached run
    lz = c.post("/api/results/steps", json={"case": "NLG", "frames": 2,
                                            "members": "lazy"}).get_json()
    assert lz["cached"] is True and lz["members_lazy"] is True
    assert "member_stations" not in lz and set(lz["member_peaks"]) == {"0", "1", "2"}
    sl = c.post("/api/results/steps", json={"case": "NLG", "frames": 2,
                                            "members": "lazy",
                                            "member_steps": [2, 9]}).get_json()
    assert list(sl["member_stations"]) == ["2"]
    assert sl["member_stations"]["2"] == j1["member_stations"]["2"]
    peak = max(abs(v) for st in j1["member_stations"]["2"].values()
               for v in st["M3"])
    assert lz["member_peaks"]["2"]["M3"] == pytest.approx(peak, rel=1e-11)
    assert c.post("/api/results/steps", json={"case": "NLG", "members": "x"}
                  ).status_code == 400
    assert c.post("/api/results/steps", json={"case": "NLG",
                                              "member_steps": "1"}
                  ).status_code == 400
    assert c.get("/api/model").get_json() == before
