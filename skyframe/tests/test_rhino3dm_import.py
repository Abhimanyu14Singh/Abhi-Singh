"""Rhino .3dm import (rhino3dm) + the linked-file workflow.

The .3dm files are built here with rhino3dm (tests/_rhino_build.py):
layers, a frame of lines, an arc beam, a NURBS beam, a planar slab with an
opening, a mesh roof, points as supports, UserText overrides.
"""

import json
import os
import sys
import time

import pytest

rhino3dm = pytest.importorskip("rhino3dm")

sys.path.insert(0, os.path.dirname(__file__))
from _rhino_build import build_frame_3dm  # noqa: E402

from skyframe.core import rhino_exchange as X  # noqa: E402
from skyframe.core.model import (BuildingModel, LoadCase,  # noqa: E402
                                 LoadPattern, MemberLoad)
from skyframe.io import rhino3dm_io as R  # noqa: E402


def dead_model():
    m = BuildingModel(name="rh")
    p = LoadPattern("DEAD", "dead")
    p.self_weight_factor = 1.0
    m.patterns["DEAD"] = p
    m.cases["DEAD"] = LoadCase("DEAD", {"DEAD": 1.0})
    return m


def run(model):
    from skyframe.engine.opensees_engine import OpenSeesEngine
    return OpenSeesEngine(model).run().to_dict()


@pytest.fixture(scope="module")
def f3dm(tmp_path_factory):
    p = tmp_path_factory.mktemp("rh") / "frame.3dm"
    ids = build_frame_3dm(str(p))
    return str(p), ids


def by_id(payload):
    return {o["id"]: o for o in payload["objects"]}


def test_read_layers_units_usertext(f3dm):
    path, ids = f3dm
    pl = R.read_3dm(path)
    assert pl["schema"] == "skyframe.exchange/1"
    assert pl["source"] == {"app": "rhino3dm", "doc": "frame.3dm",
                            "units": "mm", "tolerance": 0.01}
    assert pl["session"] == R.session_for(path)
    assert pl["warnings"] == []
    lay = {ly["path"]: ly["color"] for ly in pl["layers"]}
    assert lay["Structure::Columns"] == "#DC1E1E"
    assert lay["Structure::Beams"] == "#1E3CDC"
    rows = {r["path"]: r for r in R.layer_table(pl)}
    assert rows["Structure::Columns"]["count"] == 8
    assert rows["Supports"]["kinds"] == {"point": 4}
    assert rows["Structure::Roof"]["kinds"] == {"mesh": 1, "surface": 1}
    o = by_id(pl)
    assert o[ids["B0"]]["attrs"] == {"sf_section": "W18X50"}
    assert o[ids["P0"]]["attrs"] == {"sf_role": "brace"}
    assert o[ids["C0"]]["geom"] == {"type": "line", "p": [[0.0, 0.0, 0.0],
                                                          [0.0, 0.0,
                                                           3000.0]]}
    arc = o[ids["ARC"]]["geom"]
    assert arc["type"] == "arc"
    assert arc["p"][1] == pytest.approx([-1000.0, 3000.0, 3000.0])
    nb = o[ids["NURBS"]]["geom"]
    assert nb["type"] == "nurbs" and nb["degree"] == 3
    assert len(nb["cv"]) == 4 and len(nb["samples"]) > 10
    assert nb["samples"][0] == pytest.approx([0, 0, 3000])
    slab = o[ids["SLAB"]]["geom"]
    assert slab["type"] == "polygon" and len(slab["outer"]) == 4
    assert len(slab["holes"]) == 1 and len(slab["holes"][0]) == 4
    roof = o[ids["ROOF"]]
    assert roof["kind"] == "mesh" and len(roof["geom"]["f"]) == 9
    vault = o[ids["VAULT"]]
    assert vault["kind"] == "surface" and \
        vault["geom"]["type"] == "nurbs_surface"
    assert len(vault["geom"]["mesh"]["f"]) == 16
    assert o[ids["S0"]]["geom"] == {"type": "point", "p": [0.0, 0.0, 0.0]}


def test_nurbs_samples_within_tolerance(f3dm):
    path, ids = f3dm
    pl = R.read_3dm(path)
    s = by_id(pl)[ids["NURBS"]]["geom"]["samples"]
    # check the chord deviation against the true curve, densely sampled
    model = rhino3dm.File3dm.Read(path)
    crv = next(ob.Geometry for ob in model.Objects
               if str(ob.Attributes.Id) == ids["NURBS"])
    d0, d1 = crv.Domain.T0, crv.Domain.T1
    pts = [crv.PointAt(d0 + (d1 - d0) * k / 2000) for k in range(2001)]
    worst = 0.0
    for p in pts:
        q = (p.X, p.Y, p.Z)
        best = min(R._seg_dev(a, b, q) for a, b in zip(s[:-1], s[1:]))
        worst = max(worst, best)
    assert worst <= 0.01 + 1e-9


def test_import_preview_apply_analysis(f3dm):
    path, ids = f3dm
    pl = R.read_3dm(path)
    m = dead_model()
    pv = X.preview_exchange(m, pl)
    assert pv["valid"] and pv["stories_created"] == ["Story1"]
    rows = {r["id"]: r for r in pv["objects"]}
    assert rows[ids["C0"]]["role"] == "column"
    assert rows[ids["B1"]]["role"] == "beam"
    assert rows[ids["S0"]]["role"] == "support"
    assert rows[ids["SLAB"]]["role"] == "slab"
    assert rows[ids["ROOF"]]["role"] == "shell"
    assert rows[ids["P0"]]["role"] == "brace"            # UserText
    assert rows[ids["B0"]]["section"] == "W18X50"        # UserText
    new, rep = X.apply_exchange(m, pl)
    # mm -> m
    c2 = next(x for x in new.members if x.source_ref["id"] == ids["C2"])
    assert c2.pi == (6.0, 6.0, 0.0) and c2.pj == (6.0, 6.0, 3.0)
    arc = next(x for x in new.members if x.source_ref["id"] == ids["ARC"])
    assert arc.curve["via"] == pytest.approx([-1.0, 3.0, 3.0])
    nurbs = [x for x in new.members if x.source_ref["id"] == ids["NURBS"]]
    assert 2 <= len(nurbs) <= 12 and all(x.kind == "beam" for x in nurbs)
    (slab,) = [s for s in new.shells if s.source_ref["id"] == ids["SLAB"]]
    assert slab.net_area == pytest.approx(35.0)
    assert len(slab.openings) == 1
    roof = [s for s in new.shells if s.source_ref["id"] == ids["ROOF"]]
    assert len(roof) == 13          # 8 warped quads -> 16 tri? see below
    assert len(new.supports) == 4
    assert [s.name for s in new.stories] == ["Story1"]
    res = run(new)
    assert res["case_status"]["DEAD"] == "finished"
    fz = sum(v[2] for v in res["cases"]["DEAD"]["reactions"].values())
    assert fz > 0


def test_reimport_same_file_is_all_unchanged(f3dm):
    # incl. the arc beam (stored curves carry normalised defaults) and the
    # NURBS chain / mesh faces / slab with its opening
    path, _ids = f3dm
    pl = R.read_3dm(path)
    new, _ = X.apply_exchange(dead_model(), pl, now="T")
    new2, rep = X.apply_exchange(new, pl, now="T")
    s = rep["summary"]
    assert all(v == 0 for k in ("create", "update", "delete")
               for v in s[k].values()), s
    assert {o["status"] for o in rep["objects"]} == {"unchanged"}
    assert json.dumps(new2.to_dict(), sort_keys=True) == \
        json.dumps(new.to_dict(), sort_keys=True)


def test_vault_without_render_mesh_warns(tmp_path):
    p = tmp_path / "nomesh.3dm"
    ids = build_frame_3dm(str(p), render_mesh=False)
    pl = R.read_3dm(str(p))
    assert ids["VAULT"] not in by_id(pl)
    assert any("render mesh" in w and "Grasshopper" in w
               for w in pl["warnings"])


def test_units_from_file_settings(tmp_path):
    p = tmp_path / "metres.3dm"
    build_frame_3dm(str(p), units="Meters", omit=["VAULT"])
    pl = R.read_3dm(str(p))
    assert pl["source"]["units"] == "m"
    p2 = tmp_path / "km.3dm"
    build_frame_3dm(str(p2), units="Kilometers", omit=["VAULT"])
    pl2 = R.read_3dm(str(p2))
    assert pl2["source"]["units"] == "m"           # converted (x 1000)
    c = next(o for o in pl2["objects"] if o["layer"] == "Structure::Columns")
    assert c["geom"]["p"][1][2] == pytest.approx(3000.0 * 1000.0)


def test_second_version_merge_and_delete(tmp_path):
    p = tmp_path / "v.3dm"
    ids = build_frame_3dm(str(p), omit=["VAULT"])
    m = dead_model()
    new, _ = X.apply_exchange(m, R.read_3dm(str(p)))
    b1 = next(x for x in new.members if x.source_ref["id"] == ids["B1"])
    b1.section = "W12X26" if "W12X26" in new.sections else b1.section
    new.patterns["DEAD"].member_loads.append(
        MemberLoad(b1.uid, "udl", 4.0, 0.0, 0.0, 1.0, "gravity"))
    sec_before = b1.section
    # v2: same GUIDs are lost when a file is rebuilt, so emulate the edit
    # on the payload of v1: B2 deleted in Rhino, everything lifted 20 mm (same level)
    pl = R.read_3dm(str(p))
    pl["objects"] = [o for o in pl["objects"] if o["id"] != ids["B2"]]
    for o in pl["objects"]:
        g = o["geom"]
        if o["layer"] == "Supports":
            continue
        for key in ("p", "outer", "v", "samples"):
            if key in g and g["type"] != "point":
                for q in g[key]:
                    if q[2] > 1.0:
                        q[2] += 20.0
        for h in g.get("holes", []):
            for q in h:
                q[2] += 20.0
    new2, rep = X.apply_exchange(new, pl)
    assert rep["summary"]["delete"]["members"] == 1
    assert ids["B2"] not in {(x.source_ref or {}).get("id")
                             for x in new2.members}
    b1b = next(x for x in new2.members if x.source_ref["id"] == ids["B1"])
    assert b1b.uid == b1.uid and b1b.section == sec_before
    assert b1b.pi[2] == pytest.approx(3.02)
    assert new2.patterns["DEAD"].member_loads[0].member_uid == b1.uid
    assert [s.name for s in new2.stories] == ["Story1"]
    res = run(new2)
    assert res["case_status"]["DEAD"] == "finished"


def test_rhino_unavailable_message(monkeypatch):
    def boom():
        raise R.RhinoUnavailable(R.INSTALL_HINT)
    monkeypatch.setattr(R, "_r3", boom)
    with pytest.raises(R.RhinoUnavailable, match=r"skyframe\[rhino\]"):
        R.read_3dm(b"xx")
    assert R.available() is False


# --------------------------------------------------------------------------- #
# API: import + linked file
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    import skyframe.api.server as srv
    app = srv.create_app()
    old = srv._state["model"]
    srv._state["model"] = dead_model()
    srv._tables_store.clear()
    with app.test_client() as c:
        yield c, srv
    srv._state["model"] = old
    srv._state.pop("rhino_link", None)
    srv._tables_store.clear()


def test_api_import_multipart_and_path(client, f3dm):
    c, srv = client
    path, ids = f3dm
    with open(path, "rb") as fh:
        r = c.post("/api/rhino/import",
                   data={"file": (fh, "frame.3dm")},
                   content_type="multipart/form-data")
    assert r.status_code == 200
    j = r.get_json()
    assert j["exchange"]["source"]["doc"] == "frame.3dm"
    assert any(ly["path"] == "Supports" and ly["count"] == 4
               for ly in j["layers"])
    r2 = c.post("/api/rhino/import", json={"path": path})
    j2 = r2.get_json()
    assert j2["exchange"]["session"] == R.session_for(path)
    assert j2["mtime"] == pytest.approx(os.path.getmtime(path))
    r = c.post("/api/exchange/apply", json={"exchange": j2["exchange"],
                                            "run": True})
    assert r.status_code == 200 and r.get_json()["analysis"]["ok"]
    res = c.get("/api/exchange/results?what=reactions").get_json()
    assert {ids[f"S{k}"] for k in range(4)} <= set(res["items"])
    assert c.post("/api/rhino/import", json={}).status_code == 400
    assert c.post("/api/rhino/import",
                  json={"path": "/nope/x.3dm"}).status_code == 400


def test_api_link_changed_update(client, tmp_path):
    c, srv = client
    p = tmp_path / "linked.3dm"
    build_frame_3dm(str(p), omit=["VAULT"])
    assert c.get("/api/rhino/link").get_json()["linked"] is False
    r = c.post("/api/rhino/link", json={"path": str(p)})
    assert r.status_code == 200
    j = r.get_json()
    assert j["linked"] and j["changed"] is False
    # first import + apply through the link
    imp = c.post("/api/rhino/import", json={"path": str(p)}).get_json()
    c.post("/api/exchange/apply", json={"exchange": imp["exchange"]})
    n0 = len(srv._state["model"].members)
    # the file is modified in Rhino: one beam gone
    build_frame_3dm(str(p), omit=["VAULT", "B2"], dz=100.0)
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    j = c.get("/api/rhino/link").get_json()
    assert j["changed"] is True
    # Update = import the linked path + apply (merge); the link baseline
    # follows and the indicator goes back to "linked"
    imp = c.post("/api/rhino/import", json={"path": str(p)}).get_json()
    r = c.post("/api/exchange/apply", json={"exchange": imp["exchange"]})
    assert r.status_code == 200
    assert c.get("/api/rhino/link").get_json()["changed"] is False
    # (a rebuilt file has new GUIDs: the old session objects are replaced)
    rep = r.get_json()["report"]
    assert rep["summary"]["delete"]["members"] >= 1
    assert len(srv._state["model"].members) == n0 - 1
    r = c.delete("/api/rhino/link")
    assert r.get_json()["linked"] is False
    assert c.post("/api/rhino/link",
                  json={"path": str(tmp_path / "x.3dm")}).status_code == 404


def test_api_link_auto_apply_polls(client, tmp_path, monkeypatch):
    c, srv = client
    import skyframe.api.rhino_api as xa
    monkeypatch.setattr(xa, "LINK_POLL_S", 0.1)
    p = tmp_path / "auto.3dm"
    build_frame_3dm(str(p), omit=["VAULT"])
    r = c.post("/api/rhino/link", json={"path": str(p),
                                        "auto_apply": True})
    rev0 = r.get_json()["revision"]
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    t0 = time.time()
    while time.time() - t0 < 20:
        j = c.get("/api/rhino/link").get_json()
        if j["revision"] > rev0 and not j["changed"]:
            break
        time.sleep(0.1)
    assert j["revision"] > rev0 and j["changed"] is False
    assert j["last_error"] is None
    assert len(srv._state["model"].members) > 0
    c.delete("/api/rhino/link")
