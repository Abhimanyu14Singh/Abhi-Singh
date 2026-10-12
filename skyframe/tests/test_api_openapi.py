"""Versioned REST API (/api/v1) + generated OpenAPI 3 spec -- CONTRACT
"Programmatic API: OAPI facade, /api/v1 and OpenAPI"."""

import json

import pytest

from skyframe.api.openapi import (build_spec, contract_entries,
                                  shape_schema, validate_spec)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("SKYFRAME_PLUGINS_STATE", str(tmp_path / "plg.json"))
    from skyframe.api.server import create_app
    return create_app()


def test_spec_is_valid_openapi(app):
    c = app.test_client()
    spec = c.get("/api/openapi.json").get_json()
    assert spec["openapi"] == "3.0.3"
    assert validate_spec(spec) == []
    # deterministic + JSON-safe
    assert json.dumps(build_spec(app), sort_keys=True) == \
        json.dumps(spec, sort_keys=True)


def test_spec_passes_official_validator(app):
    osv = pytest.importorskip("openapi_spec_validator")
    osv.validate(build_spec(app))


def test_spec_covers_every_api_route(app):
    spec = build_spec(app)
    from skyframe.api.openapi import _oa_path
    for r in app.url_map.iter_rules():
        if not r.rule.startswith("/api/"):
            continue
        p = "/api/v1" + _oa_path(r.rule)[len("/api"):]
        assert p in spec["paths"], r.rule
        for meth in r.methods - {"HEAD", "OPTIONS"}:
            assert meth.lower() in spec["paths"][p], (meth, r.rule)
    ops = [op for it in spec["paths"].values() for op in it.values()]
    assert len(ops) >= 140
    # most endpoints carry their CONTRACT.md description
    assert sum(1 for op in ops if op.get("description")) / len(ops) > 0.7


def test_core_shapes(app):
    spec = build_spec(app)
    p = spec["paths"]
    body = lambda op: op["responses"]["200"]["content"][  # noqa: E731
        "application/json"]["schema"]
    assert body(p["/api/v1/model"]["get"]) == \
        {"$ref": "#/components/schemas/Model"}
    assert body(p["/api/v1/analyze"]["post"]) == \
        {"$ref": "#/components/schemas/AnalysisResults"}
    res = spec["components"]["schemas"]["AnalysisResults"]
    assert {"nodes", "members", "cases", "combos", "modal"} <= \
        set(res["properties"])
    # CONTRACT-derived request shape: {name?, direction?, V, ...}
    req = p["/api/v1/pattern/wind"]["post"]["requestBody"]["content"][
        "application/json"]["schema"]
    assert req["required"] == ["V"]
    assert {"name", "direction", "exposure", "Cp", "importance"} <= \
        set(req["properties"])
    # path parameters
    ex = p["/api/v1/exchange/export/{fmt}"]["post"]
    assert ex["parameters"][0]["name"] == "fmt"
    assert body(ex) == {"$ref": "#/components/schemas/ExportResponse"}
    assert body(p["/api/v1/plugins"]["get"])["properties"]["plugins"]


def test_v1_alias_serves_same_payloads(app):
    c = app.test_client()
    assert c.get("/api/v1/health").get_json() == c.get("/api/health") \
        .get_json()
    assert c.get("/api/v1/model").get_json() == c.get("/api/model") \
        .get_json()
    r = c.post("/api/v1/model/quick", json={"stories": 2})
    assert r.status_code == 200 and len(r.get_json()["stories"]) == 2
    assert c.get("/api/model").get_json()["stories"] == \
        r.get_json()["stories"]
    assert c.get("/api/v1/nope").status_code == 404
    assert c.get("/api/v1/openapi.json").get_json()["info"]["title"] == \
        "SkyFrame REST API"


def test_contract_parsers():
    text = ("| POST   | `/api/x/<fmt>` | `{a, b?: [1], c: {}}` → "
            "`{ok, model}` |\n"
            "* `GET /api/y` `{n?}` ->\n  `{z: 1}` continued\n")
    ent = contract_entries(text)
    assert ent[0][:2] == ("post", "/api/x/<fmt>")
    assert ent[1][0] == "get" and "continued" in ent[1][2]
    s = shape_schema("{a, b?: [1], c: {}, d: \"x\", …}")
    assert s["required"] == ["a", "c", "d"]
    assert s["properties"]["b"]["type"] == "array"
    assert s["properties"]["c"]["type"] == "object"
    assert s["properties"]["d"]["type"] == "string"
    assert shape_schema("no braces here") is None
