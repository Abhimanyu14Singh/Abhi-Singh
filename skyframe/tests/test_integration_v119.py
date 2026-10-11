"""Cross-feature checks for the v1.18/v1.19 merges: multiple towers x
solver policy / condensed modal, model repair x tower joint tags, mesh
study / accuracy advisor on tower models."""
import json
import math

import pytest

from skyframe.core.accuracy_advisor import accuracy_advice
from skyframe.core.mesh_study import mesh_study
from skyframe.core.model import BuildingModel
from skyframe.core.repair import repair
from skyframe.engine.opensees_engine import OpenSeesEngine

from tests.test_towers import _twin


def _close(a, b, rel=1e-8, abs_=1e-10):
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


@pytest.fixture(scope="module")
def twin_default():
    return OpenSeesEngine(_twin()).run()


@pytest.mark.parametrize("solver", ["umfpack", "band"])
def test_towers_independent_of_solver_policy(twin_default, solver):
    m = _twin()
    m.analysis_options = {"solver": solver}
    m.validate()
    r = OpenSeesEngine(m).run()
    for a, b in zip(r.modal.periods, twin_default.modal.periods):
        assert _close(a, b, 1e-8)
    for case in ("DEAD", "EQX"):
        for nm, row in twin_default.cases[case].story.items():
            for k, v in row.items():
                if isinstance(v, (int, float)):
                    assert _close(r.cases[case].story[nm][k], v, 1e-7,
                                  1e-9), (solver, case, nm, k)
    assert r.to_dict()["story_tower"] == twin_default.to_dict()["story_tower"]


def test_repair_merge_remaps_tower_joint_tags():
    m = _twin()
    # tag a tower-B roof joint, plus a near-duplicate of it 0.4 mm away
    p = [40.0, 0.0, m.stories[-1].elevation]
    m.joint_towers = [{"point": p, "tower": "T2"},
                      {"point": [p[0] + 4e-4, p[1], p[2]], "tower": "T2"}]
    d = m.to_dict()
    new, rep = repair(d, ["merge_joints"], tolerance=1e-3)
    jt = new.get("joint_towers") or []
    assert len(jt) == 1 and jt[0]["tower"] == "T2"
    # the model still loads, validates and analyses identically
    m2 = BuildingModel.from_dict(json.loads(json.dumps(new)))
    r = OpenSeesEngine(m2).run()
    assert r.cases["EQX"].story["B1"]["shear_x"] == pytest.approx(60.0,
                                                                  rel=1e-9)


def test_repair_clean_tower_model_unchanged():
    d = _twin().to_dict()
    new, _ = repair(d)
    assert json.dumps(new, sort_keys=True) == json.dumps(d, sort_keys=True)


def test_mesh_study_and_advisor_on_tower_model():
    m = _twin()
    before = json.dumps(m.to_dict(), sort_keys=True)
    res = mesh_study(m, levels=[1, 2], cases=["EQX"])
    assert res["rows"]
    # frame-only elastic model: refinement changes nothing
    for row in res["rows"]:
        v = [x for x in row["values"] if x is not None]
        if v and abs(v[0]) > 1e-12:
            assert _close(v[-1], v[0], 1e-6), row["key"]
    adv = accuracy_advice(m)
    assert "advice" in adv
    assert json.dumps(m.to_dict(), sort_keys=True) == before
