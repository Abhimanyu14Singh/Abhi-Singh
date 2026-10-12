"""Plugin framework (plugin.json manifests, JS menu items + Python hooks)
-- CONTRACT "Plugins".  The bundled example plugin is loaded from
``plugins/example-story-summary``."""

import json
import os
import shutil

import pytest

from skyframe.core import tables as _tables
from skyframe.core.plugins import PluginManager, validate_manifest

ROOT = os.path.dirname(os.path.dirname(__file__))
EXAMPLE = os.path.join(ROOT, "plugins", "example-story-summary")
PID = "example-story-summary"
TKEY = f"plugin.{PID}.story_summary"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    pdir = tmp_path / "plugins"
    pdir.mkdir()
    shutil.copytree(EXAMPLE, pdir / PID)
    monkeypatch.setenv("SKYFRAME_PLUGINS_DIR", str(pdir))
    monkeypatch.setenv("SKYFRAME_PLUGINS_STATE", str(tmp_path / "st.json"))
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    yield app, pdir, tmp_path
    app.extensions["skyframe_plugins"].scan()      # drop plugin tables


def _quick(c, **kw):
    r = c.post("/api/model/quick", json={"stories": 3, "bays_x": 2,
                                         "bays_y": 1, **kw})
    assert r.status_code == 200
    return r.get_json()


def test_disabled_by_default_is_a_no_op(env):
    app, _pdir, _ = env
    c = app.test_client()
    lst = c.get("/api/plugins").get_json()
    p = next(x for x in lst["plugins"] if x["id"] == PID)
    assert p["enabled"] is False and p["invalid"] is False
    assert p["tables"] == [TKEY] and p["js"] == ["plugin.js"]
    _quick(c)
    res = c.post("/api/analyze").get_json()
    assert "plugins" not in res
    keys = [t["key"] for t in c.get("/api/tables/list").get_json()["tables"]]
    assert TKEY not in keys
    assert c.post(f"/api/plugins/{PID}/call/summary").status_code == 404


def test_enable_menu_table_hooks_endpoint(env):
    app, _pdir, tmp = env
    c = app.test_client()
    before = c.get("/api/tables/list").get_json()["tables"]
    r = c.post(f"/api/plugins/{PID}/enable", json={"enabled": True})
    assert r.status_code == 200 and r.get_json()["loaded"] is True
    with open(tmp / "st.json") as fh:
        assert json.load(fh)["enabled"][PID] is True
    # the JS module (menu item) is served from inside the plugin folder
    js = c.get(f"/api/plugins/{PID}/files/plugin.js")
    assert js.status_code == 200 and js.mimetype == "text/javascript"
    assert b"tool-plg-story-summary" in js.data
    for bad in ("../../CONTRACT.md", "plugin.py", "plugin.json/../plugin.py"):
        assert c.get(f"/api/plugins/{PID}/files/{bad}").status_code == 404
    # the table joins the Show Tables catalogue (group "Plugins")
    after = c.get("/api/tables/list").get_json()["tables"]
    assert len(after) == len(before) + 1
    ent = next(t for t in after if t["key"] == TKEY)
    assert ent["group"] == "Plugins" and ent["columns"][1]["quantity"] == \
        "length"
    _quick(c)
    res = c.post("/api/analyze").get_json()
    drifts = {s: max(max(abs(cs["story"][s]["drift_x"]),
                         abs(cs["story"][s]["drift_y"]))
                     for cs in res["cases"].values()) for s in
              res["story_order"]}
    gov = max(drifts, key=drifts.get)
    assert res["plugins"][PID] == {"stories": 3, "max_drift": drifts[gov],
                                   "governing": gov}
    tab = c.post(f"/api/tables/{TKEY}", json={"results": res}).get_json()
    # closed form: (2+1)(1+1) = 6 columns, 2*2 + 1*3 = 7 beams per story
    assert [r["columns"] for r in tab["rows"]] == [6, 6, 6]
    assert [r["beams"] for r in tab["rows"]] == [7, 7, 7]
    assert [r["max_drift"] for r in tab["rows"]] == \
        [drifts[s] for s in res["story_order"]]
    summ = c.post(f"/api/plugins/{PID}/call/summary", json={}).get_json()
    assert [s["story"] for s in summ["stories"]] == res["story_order"]
    # disable: table removed, analysis back to the plain payload
    c.post(f"/api/plugins/{PID}/enable", json={"enabled": False})
    assert TKEY not in [t["key"] for t in
                        c.get("/api/tables/list").get_json()["tables"]]
    assert "plugins" not in c.post("/api/analyze").get_json()


def test_state_persists_across_apps(env):
    app, _pdir, _ = env
    app.test_client().post(f"/api/plugins/{PID}/enable",
                           json={"enabled": True})
    from skyframe.api.server import create_app
    app2 = create_app()
    p = next(x for x in app2.test_client().get("/api/plugins").get_json()
             ["plugins"] if x["id"] == PID)
    assert p["enabled"] and p["loaded"]
    app2.extensions["skyframe_plugins"].set_enabled(PID, False)


def test_failures_are_contained(env, tmp_path):
    app, pdir, _ = env
    bad = pdir / "bad-hook"
    bad.mkdir()
    (bad / "plugin.json").write_text(json.dumps({
        "id": "bad-hook", "name": "Bad", "python": "p.py",
        "hooks": ["post_analysis", "pre_analysis"],
        "endpoints": ["boom"]}))
    (bad / "p.py").write_text(
        "def pre_analysis(model, ctx):\n    return ['careful']\n"
        "def post_analysis(model, results, ctx):\n    raise RuntimeError('x')\n"
        "def _boom(model, body, ctx):\n    raise ValueError('nope')\n"
        "endpoints = {'boom': _boom}\n")
    broken = pdir / "broken-import"
    broken.mkdir()
    (broken / "plugin.json").write_text(json.dumps({
        "id": "broken-import", "name": "Broken", "python": "p.py",
        "hooks": ["pre_analysis"]}))
    (broken / "p.py").write_text("raise ImportError('no deps')\n")
    invalid = pdir / "Invalid Folder"
    invalid.mkdir()
    (invalid / "plugin.json").write_text(json.dumps({
        "id": "BAD ID", "name": "", "js": ["../x.js"],
        "hooks": ["on_save"]}))
    c = app.test_client()
    c.post("/api/plugins/rescan")
    assert c.post("/api/plugins/bad-hook/enable").status_code == 200
    r = c.post("/api/plugins/broken-import/enable").get_json()
    assert r["enabled"] is False and "import failed" in r["error"]
    assert c.post("/api/plugins/BAD ID/enable").status_code in (400, 404)
    _quick(c)
    res = c.post("/api/analyze")
    assert res.status_code == 200                      # analysis still runs
    assert res.get_json()["plugins"]["bad-hook"] == {"error": "x"}
    log = c.get("/api/analyze/log").get_json()
    assert "careful" in json.dumps(log)                 # pre-hook warning
    assert c.post("/api/plugins/bad-hook/call/boom").status_code == 400
    lst = {p["id"]: p for p in c.get("/api/plugins").get_json()["plugins"]}
    assert "endpoint boom failed" in lst["bad-hook"]["error"]
    assert lst["BAD ID"]["invalid"]


def test_manifest_validation(tmp_path):
    (tmp_path / "a.js").write_text("export function activate(){}")
    ok = {"id": "ok", "name": "OK", "js": ["a.js"]}
    assert validate_manifest(ok, str(tmp_path)) == []
    errs = validate_manifest({"id": "x y", "name": "", "js": ["../a.js"],
                              "skyframe": ">=99.0", "permissions": ["root"],
                              "tables": [{"key": "t", "columns": [
                                  {"key": "a", "quantity": "parsec"}]}]},
                             str(tmp_path))
    text = " ".join(errs)
    for frag in ("id must match", "name is required", "inside the plugin",
                 "needs SkyFrame", "unknown permission", "need a python",
                 "unknown quantity"):
        assert frag in text, frag


def test_manager_without_folders(tmp_path):
    mgr = PluginManager(dirs=[str(tmp_path / "none")],
                        state_path=str(tmp_path / "s.json"))
    assert mgr.list() == []
    assert not any(k.startswith("plugin.") for k in _tables.TABLES)
