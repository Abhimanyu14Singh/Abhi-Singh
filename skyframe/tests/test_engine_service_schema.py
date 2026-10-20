"""The engine's published formats are valid JSON Schemas, and what the engine
takes and returns conforms to them."""

import json
from pathlib import Path

import jsonschema
import pytest

from skyframe.engine_service.supa import CASES, solve_fem

SCHEMA = Path(__file__).resolve().parents[1] / "skyframe" / "engine_service" / "schema"


def load(name):
    return json.loads((SCHEMA / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["supa_fem_model.schema.json", "supa_fem_result.schema.json",
                                  "analysis_model.schema.json"])
def test_schema_is_valid(name):
    jsonschema.Draft202012Validator.check_schema(load(name))


def test_example_analysis_model_conforms():
    ex = json.loads((SCHEMA / "examples" / "two_story_core.analysis_model.json").read_text())
    jsonschema.validate(ex, load("analysis_model.schema.json"))


def test_supa_model_and_engine_result_conform():
    E = 29000 * 144
    m = dict(xyz=[0, 0, 0, 0, 0, 12, 10, 0, 0, 10, 0, 12], fixed=[True, False, True, False],
             els=[dict(i=0, j=1, E=E, G=E / 2.6, A=0.1, Iy=0.01, Iz=0.01, J=0.001, yh=[1, 0, 0], ri=False, rj=False,
                       src="c1", kind="frame", q={}),
                  dict(i=2, j=3, E=E, G=E / 2.6, A=0.1, Iy=0.01, Iz=0.01, J=0.001, yh=[1, 0, 0], ri=False, rj=False,
                       src="c2", kind="frame", q={}),
                  dict(i=1, j=3, E=E, G=E / 2.6, A=0.1, Iy=0.01, Iz=0.01, J=0.001, yh=[0, 1, 0], ri=True, rj=True,
                       src="b", kind="frame", q={"D": [0, 0, -1.0], "L": [0, 0, -0.5]})],
             dia=[dict(id="roof", x=5, y=0, z=12, nodes=[1, 3])],
             nodeLoads={c: [0.0] * 12 for c in CASES}, diaLoads={c: [0.0] * 3 for c in CASES}, notes=[])
    m["diaLoads"]["Ex"][0] = 10
    jsonschema.validate(m, load("supa_fem_model.schema.json"))
    r = solve_fem(m)
    jsonschema.validate(json.loads(json.dumps(r)), load("supa_fem_result.schema.json"))
    assert set(r["pieces"]) == {"c1", "c2", "b"}
