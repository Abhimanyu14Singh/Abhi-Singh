"""Wave C interface parity: general grids in E2K / as primary grid, shell
editing with openings, the vertex reshaper on a shell's plane, extension /
parallel / 3D snap geometry, per-quantity units and auto-save recovery.

Hand-derived values:

* shells with openings: a 6 x 4 slab with a 2 x 2 opening has net area
  24 - 4 = 20 m^2; every divide / chop / merge conserves it exactly (to the
  1e-9 m point cleaning); expand by 0.5 keeps the opening: 7 x 5 - 4 = 31;
* reshape ``on_plane``: a point (x, 0.3, z) dragged on the wall y = 0 lands
  on (x, 0, z) (orthogonal projection);
* E2K: GENERAL grid lines written and read back exactly in kN-m and to
  1e-12 in kip-ft; the general system stays the primary one;
* snaps: extension of A(0,0)-B(4,0) at W(5.0, 0.05) -> F(5, 0); parallel
  from P(10, 10) to that segment at W(13, 10.04) -> F(13, 10); the line
  (0,0,0)+t(1,0,0) and the ray from (2, -5, 1) along +y meet at t = 2,
  distance 1;
* units: psi = 4.4482216152605/0.0254^2 /1000 kN/m^2 = 6.894757293168361 kPa,
  ksi = 6894.757293168361 kPa, 1 kip.ft = 1.3558179483314004 kN.m;
* auto-save: the recovered model hashes to the hash recorded at save time.
"""

import json
import os

import pytest

from skyframe.core import shell_holes as SH
from skyframe.core import snapgeom_ext as SX
from skyframe.core import units_custom as UC
from skyframe.core.builder import quick_building
from skyframe.core.edit import apply_edit
from skyframe.core.model import (AreaLoad, BuildingModel, GridSystem, Opening,
                                 ShellSection)
from skyframe.engine.opensees_engine import OpenSeesEngine
from skyframe.io.e2k_read import read_e2k
from skyframe.io.e2k_write import export_e2k


def _sel(**kw):
    out = {"members": [], "shells": [], "links": [], "points": []}
    out.update(kw)
    return out


def _slab(openings=None, corners=None):
    m = quick_building(bays_x=1, bays_y=1, stories=1, bay_width_x=6.0,
                       bay_width_y=4.0)
    m.add_shell_section(ShellSection("SLAB", "CONC", 0.2))
    z = m.stories[0].elevation
    c = corners or [(0, 0, z), (6, 0, z), (6, 4, z), (0, 4, z)]
    m.add_shell("slab", "shell", "SLAB", c, mesh_size=1.0,
                story=m.stories[0].name, uid="S1",
                openings=openings if openings is not None else
                [Opening(2 / 6, 1 / 4, 4 / 6, 3 / 4)])
    m.patterns["DEAD"].area_loads.append(AreaLoad("S1", 2.0))
    return m


def _flat(pts):
    return [float(v) for p in pts for v in p]


def _net(d, uids):
    m = BuildingModel.from_dict(d)
    return sum(r.net_area for r in m.shells if r.uid in uids)


# --------------------------------------------------------------------------- #
# shells with openings
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("params", [
    {"mode": "n", "n1": 3, "n2": 2}, {"mode": "n", "n1": 2, "n2": 2},
    {"mode": "size", "max_size": 1.5}, {"mode": "n", "n1": 5, "n2": 3}])
def test_divide_with_opening_conserves_net_area(params):
    d0 = _slab().to_dict()
    assert _net(d0, ["S1"]) == pytest.approx(20.0, abs=1e-12)
    d, sm = apply_edit(d0, "shell_divide", _sel(shells=["S1"]), params)
    pieces = sm["divided"]["S1"]
    assert len(pieces) > 1 and pieces[0] == "S1"
    assert _net(d, pieces) == pytest.approx(20.0, abs=1e-9)
    # every piece carries the parent's area load
    loads = [a["region_uid"] for a in d["patterns"]["DEAD"]["area_loads"]]
    assert sorted(loads) == sorted(pieces)
    # divide -> merge restores the outline and the opening
    d2, _ = apply_edit(d, "shell_merge", _sel(shells=pieces), {})
    s = next(x for x in d2["shells"] if x["uid"] == "S1")
    assert s["corners"] == d0["shells"][0]["corners"] or len(s["corners"]) == 4
    assert len(s["openings"]) == 1
    assert _net(d2, ["S1"]) == pytest.approx(20.0, abs=1e-9)


def test_divide_merge_round_trip_opening_record():
    d0 = _slab().to_dict()
    d, sm = apply_edit(d0, "shell_divide", _sel(shells=["S1"]),
                       {"mode": "n", "n1": 3, "n2": 2})
    d2, _ = apply_edit(d, "shell_merge", _sel(shells=sm["divided"]["S1"]), {})
    s = next(x for x in d2["shells"] if x["uid"] == "S1")
    assert s["corners"] == d0["shells"][0]["corners"]
    o, o0 = s["openings"][0], d0["shells"][0]["openings"][0]
    for k in ("u0", "v0", "u1", "v1"):            # parametric rectangle back
        assert o[k] == pytest.approx(o0[k], abs=1e-15)


def test_polygon_opening_and_analysis_totals():
    z = 3.2
    tri = [(4.5, 0.5, z), (5.5, 0.5, z), (5.0, 1.5, z)]
    m = _slab(openings=[Opening(2 / 6, 1 / 4, 4 / 6, 3 / 4),
                        Opening(0, 0, 1, 1, polygon=tri)])
    net0 = 24.0 - 4.0 - 0.5
    d0 = m.to_dict()
    assert _net(d0, ["S1"]) == pytest.approx(net0, abs=1e-12)
    d, sm = apply_edit(d0, "shell_chop", _sel(shells=["S1"]),
                       {"mode": "lines", "lines": [[[3, -1], [3, 5]],
                                                   [[-1, 2], [7, 2]]]})
    pieces = sm["chopped"]["S1"]
    assert len(pieces) == 4
    assert _net(d, pieces) == pytest.approx(net0, abs=1e-9)
    # the gravity load on the net area is unchanged (q * A_net)
    r1 = OpenSeesEngine(BuildingModel.from_dict(d0)).run().to_dict()
    r2 = OpenSeesEngine(BuildingModel.from_dict(d)).run().to_dict()
    assert r2["cases"]["DEAD"]["base"]["FZ"] == pytest.approx(
        r1["cases"]["DEAD"]["base"]["FZ"], rel=1e-6)


def test_subtract_and_union_closed_forms():
    cell = [(0, 0), (4, 0), (4, 4), (0, 4)]
    hole = [(1, 1), (3, 1), (3, 3), (1, 3)]
    regs = SH.subtract(cell, [hole])
    assert len(regs) == 1 and len(regs[0][1]) == 1
    assert SH.net_area2(regs) == pytest.approx(12.0)
    # a hole crossing the cell edge -> notch, no opening
    regs = SH.subtract([(0, 0), (2, 0), (2, 4), (0, 4)], [hole])
    assert len(regs) == 1 and regs[0][1] == []
    assert SH.net_area2(regs) == pytest.approx(8.0 - 2.0)
    # hole equal to the cell -> nothing left
    assert SH.subtract(hole, [hole]) == []
    # ring of 8 unit squares -> one region with a 1 x 1 hole
    ring = [[(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
            for i in range(3) for j in range(3) if (i, j) != (1, 1)]
    u = SH.union(ring)
    assert len(u) == 1 and len(u[0][1]) == 1
    assert SH.net_area2(u) == pytest.approx(8.0)


def test_merge_ring_gives_opening():
    m = quick_building(bays_x=1, bays_y=1, stories=1, bay_width_x=6.0,
                       bay_width_y=6.0)
    m.add_shell_section(ShellSection("SLAB", "CONC", 0.2))
    z = m.stories[0].elevation
    uids = []
    for i in range(3):
        for j in range(3):
            if (i, j) == (1, 1):
                continue
            u = f"R{i}{j}"
            m.add_shell("slab", "shell", "SLAB",
                        [(2 * i, 2 * j, z), (2 * i + 2, 2 * j, z),
                         (2 * i + 2, 2 * j + 2, z), (2 * i, 2 * j + 2, z)],
                        uid=u, story=m.stories[0].name)
            uids.append(u)
    d, _ = apply_edit(m.to_dict(), "shell_merge", _sel(shells=uids), {})
    s = next(x for x in d["shells"] if x["uid"] == uids[0])
    assert len(s["openings"]) == 1
    assert _net(d, [uids[0]]) == pytest.approx(32.0, abs=1e-9)


def test_expand_and_reshape_keep_openings():
    d0 = _slab().to_dict()
    d, sm = apply_edit(d0, "shell_expand", _sel(shells=["S1"]),
                       {"distance": 0.5})
    assert _net(d, ["S1"]) == pytest.approx(7 * 5 - 4, abs=1e-9)
    assert sm["expanded"]["S1"]["area_after"] == pytest.approx(35.0)
    with pytest.raises(ValueError, match="openings must stay inside"):
        apply_edit(d0, "shell_expand", _sel(shells=["S1"]),
                   {"distance": -1.2})
    d, _ = apply_edit(d0, "shell_reshape", _sel(shells=["S1"]),
                      {"mode": "insert", "edge": 0})
    s = d["shells"][-1]
    assert len(s["corners"]) == 5
    assert _net(d, ["S1"]) == pytest.approx(20.0, abs=1e-12)
    # the opening keeps its absolute position (polygon record)
    assert s["openings"][0]["polygon"] == [[2.0, 1.0, 3.2], [4.0, 1.0, 3.2],
                                           [4.0, 3.0, 3.2], [2.0, 3.0, 3.2]]


def test_reshape_on_plane_elevation_wall_stays_planar():
    m = quick_building(bays_x=1, bays_y=1, stories=1, bay_width_x=6.0,
                       bay_width_y=4.0)
    m.add_shell_section(ShellSection("W", "CONC", 0.2))
    z = m.stories[0].elevation
    m.add_shell("wall", "shell", "W", [(0, 0, 0), (6, 0, 0), (6, 0, z),
                                       (0, 0, z)], uid="W1",
                story=m.stories[0].name,
                openings=[Opening(0.25, 0.2, 0.5, 0.7)])
    d0 = m.to_dict()
    # drag corner 2 off the plane: on_plane projects it back onto y = 0
    d, _ = apply_edit(d0, "shell_reshape", _sel(shells=["W1"]),
                      {"mode": "move", "index": 2, "to": [7.0, 0.3, 4.0],
                       "on_plane": True})
    s = d["shells"][-1]
    assert s["corners"][2] == [7.0, 0.0, 4.0]
    assert all(abs(c[1]) < 1e-12 for c in s["corners"])
    BuildingModel.from_dict(d)                  # planar, valid
    # without on_plane the same move is rejected as non-planar
    with pytest.raises(ValueError):
        dd, _ = apply_edit(d0, "shell_reshape", _sel(shells=["W1"]),
                           {"mode": "move", "index": 2,
                            "to": [7.0, 0.3, 4.0]})
        BuildingModel.from_dict(dd)
    # an inclined plane: projection is orthogonal
    fr = SH.Frame([(0, 0, 0), (4, 0, 0), (4, 3, 3), (0, 3, 3)])
    p = fr.project([1.0, 0.0, 3.0])
    n = fr.n
    assert abs(sum(p[i] * n[i] for i in range(3))) < 1e-12   # on plane
    assert p == pytest.approx([1.0, 1.5, 1.5], abs=1e-12)


# --------------------------------------------------------------------------- #
# general grids: E2K + primary
# --------------------------------------------------------------------------- #
def _gg_model(primary_general=True):
    m = quick_building(bays_x=2, bays_y=1, stories=2)
    gg = GridSystem(name="GL1", kind="general", origin=(1.5, -2.0),
                    rotation=30.0,
                    lines=[{"label": "L1", "p1": [0.0, 0.0], "p2": [6.0, 3.0]},
                           {"label": "L2", "p1": [0.0, 4.0],
                            "p2": [6.0, -1.0]}])
    m.set_grid_system(gg)
    if primary_general:
        m.grid_systems = [m.grid_systems[1], m.grid_systems[0]]
        m.grid = m.grid_systems[0]
    return m


def test_e2k_general_grid_round_trip_kn_m_exact():
    m = _gg_model()
    d = m.to_dict()
    assert d["grid"]["kind"] == "general"
    text, rep = export_e2k(m, extensions=False)
    assert 'GRIDSYSTEM "GL1"  TYPE "GENERAL"' in text
    assert ('GRID "GL1"  LABEL "L1"  DIR "GENERAL"  X1 0  Y1 0  X2 6  Y2 3'
            in text)
    m2, _w = read_e2k(text)
    d2 = m2.to_dict()
    assert d2["grid_systems"][0] == d["grid_systems"][0]   # primary + exact
    assert d2["grid"]["name"] == "GL1"
    assert d2["grid_systems"][1]["x_lines"] == d["grid_systems"][1]["x_lines"]
    # with extensions the native records already carry the grid: no patch
    _t, rep = export_e2k(m, extensions=True)
    assert "grid_systems" not in json.dumps(rep.get("extensions", {}))


def test_e2k_general_grid_round_trip_kip_ft():
    m = _gg_model()
    text, _ = export_e2k(m, units=("KIP", "FT"), extensions=False)
    m2, _w = read_e2k(text, units=("KIP", "FT"))
    a, b = m.to_dict()["grid_systems"][0], m2.to_dict()["grid_systems"][0]
    assert b["kind"] == "general" and b["name"] == "GL1"
    for la, lb in zip(a["lines"], b["lines"]):
        assert la["label"] == lb["label"]
        for k in ("p1", "p2"):
            assert lb[k] == pytest.approx(la[k], rel=1e-12, abs=1e-12)
    assert b["origin"] == pytest.approx(a["origin"], rel=1e-12)


def test_general_primary_grid_analysis_unchanged():
    r_ortho = OpenSeesEngine(_gg_model(False)).run().to_dict()
    r_gen = OpenSeesEngine(_gg_model(True)).run().to_dict()
    for case in ("DEAD", "EQX"):
        for k in ("FX", "FY", "FZ"):
            assert r_gen["cases"][case]["base"][k] == pytest.approx(
                r_ortho["cases"][case]["base"][k], rel=1e-12, abs=1e-9)


# --------------------------------------------------------------------------- #
# snap geometry
# --------------------------------------------------------------------------- #
def test_snap_extension_parallel_closed_forms():
    segs = [((0.0, 0.0), (4.0, 0.0))]
    r = SX.snap_ext_par((5.0, 0.05), segs, 0.1, {"ext": True})
    assert r["kind"] == "ext" and (r["x"], r["y"]) == (5.0, 0.0)
    assert SX.snap_ext_par((2.0, 0.05), segs, 0.1, {"ext": True}) is None
    assert SX.snap_ext_par((5.0, 0.05), segs, 0.1, {"ext": True},
                           max_ext=0.5) is None
    r = SX.snap_ext_par((13.0, 10.04), segs, 0.1, {"par": True},
                        frm=(10.0, 10.0))
    assert r["kind"] == "par" and r["x"] == pytest.approx(13.0)
    assert r["y"] == pytest.approx(10.0)
    # ext ranks before par
    r = SX.snap_ext_par((5.0, 0.05), segs, 0.1, {"ext": True, "par": True},
                        frm=(0.0, 0.0))
    assert r["kind"] == "ext"


def test_snap_3d_rays():
    r = SX.line_ray([0, 0, 0], [1, 0, 0], [2, -5, 1], [0, 1, 0])
    assert r["t"] == pytest.approx(2.0) and r["s"] == pytest.approx(5.0)
    assert r["dist"] == pytest.approx(1.0)
    x = SX.seg_seg([0, 0, 0], [4, 0, 0], [2, -1, 0], [2, 3, 0])
    assert x["dist"] == pytest.approx(0.0, abs=1e-15)
    assert x["p"] == pytest.approx([2.0, 0.0, 0.0])
    assert SX.seg_seg([0, 0, 0], [1, 0, 0], [3, -1, 0], [3, 1, 0]) is None
    p = SX.plane_hit([0, 0, 10], [0, 0, -1], [0, 0, 3.2], [0, 0, 1])
    assert p == pytest.approx([0, 0, 3.2])
    assert SX.plane_hit([0, 0, 10], [1, 0, 0], [0, 0, 3.2], [0, 0, 1]) is None


# --------------------------------------------------------------------------- #
# per-quantity units
# --------------------------------------------------------------------------- #
def test_unit_catalogue_values():
    cat = UC.unit_catalog()
    si = {k: {r["label"]: r["si"] for r in v} for k, v in cat.items()}
    assert si["stress"]["psi"] == pytest.approx(6.894757293168361, rel=1e-15)
    assert si["stress"]["ksi"] == pytest.approx(6894.757293168361, rel=1e-15)
    assert si["stress"]["MPa"] == 1000.0
    assert si["moment"]["kip·ft"] == pytest.approx(1.3558179483314004,
                                                   rel=1e-15)
    assert si["length"]["mm"] == 1e-3 and si["force"]["kip"] == UC.KIP
    assert si["mass"]["kip·s²/in"] == pytest.approx(UC.KIP / 0.0254)
    assert UC.to_si("temp", "°F", 212.0) == pytest.approx(100.0)
    assert UC.to_si("length", "ft", 10.0) == pytest.approx(3.048)


def test_units_custom_round_trip_and_legacy_identity():
    m = quick_building()
    d0 = m.to_dict()
    assert "display_units_custom" not in d0 and "display_decimals" not in d0
    d = dict(d0, display_units_custom={"length": "mm", "stress": "MPa"},
             display_decimals={"length": 1, "force": 3})
    d1 = BuildingModel.from_dict(d).to_dict()
    assert d1["display_units_custom"] == {"length": "mm", "stress": "MPa"}
    assert d1["display_decimals"] == {"length": 1, "force": 3}
    assert json.dumps(BuildingModel.from_dict(d1).to_dict()) == json.dumps(d1)
    # the SI model is untouched (byte identity without the two keys)
    strip = {k: v for k, v in d1.items()
             if k not in ("display_units_custom", "display_decimals")}
    assert json.dumps(strip) == json.dumps(d0)
    for bad in ({"display_units_custom": {"length": "furlong"}},
                {"display_units_custom": {"bogus": "m"}},
                {"display_decimals": {"length": 11}},
                {"display_decimals": {"length": 1.5}},
                {"display_decimals": {"nope": 2}}):
        with pytest.raises(ValueError):
            BuildingModel.from_dict(dict(d0, **bad))


# --------------------------------------------------------------------------- #
# HTTP: auto-save, primary grid, catalogue
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api.server import create_app
    app = create_app()
    app.testing = True
    return app.test_client()


def test_autosave_recover_same_hash(client):
    from skyframe.api.wc_iface_api import AUTOSAVE_KEEP, model_hash
    m = client.get("/api/model").get_json()
    g = client.get("/api/autosave").get_json()
    assert g["exists"] is False and g["current_hash"] == model_hash(m)
    edited = dict(m, name="Edited")
    r = client.post("/api/autosave", json={"model": edited,
                                           "file_name": "proj"}).get_json()
    h_saved = r["hash"]
    assert h_saved == model_hash(BuildingModel.from_dict(edited).to_dict())
    for k in range(AUTOSAVE_KEEP + 2):              # ring keeps 5 versions
        client.post("/api/autosave", json={"model": dict(m, name=f"v{k}")})
    vs = client.get("/api/autosave").get_json()["versions"]
    assert len(vs) == AUTOSAVE_KEEP and vs[0]["hash"] != h_saved
    client.delete("/api/autosave")
    client.post("/api/autosave", json={"model": edited, "file_name": "proj"})
    rr = client.post("/api/autosave/recover", json={}).get_json()
    assert rr["hash"] == rr["saved_hash"] == h_saved
    assert rr["file_name"] == "proj"
    assert model_hash(client.get("/api/model").get_json()) == h_saved
    assert client.post("/api/autosave", json={"model": dict(m, display_decimals={"length": 99})}
                       ).status_code == 400
    assert client.post("/api/autosave/recover", json={"version": 3}
                       ).status_code == 404
    assert client.delete("/api/autosave").get_json()["removed"] == 1
    assert client.get("/api/autosave").get_json()["exists"] is False


def test_primary_grid_endpoint_and_catalogue(client):
    gs = {"name": "GL1", "kind": "general",
          "lines": [{"label": "L1", "p1": [0, 0], "p2": [5, 5]}]}
    assert client.post("/api/grid", json=gs).status_code == 200
    d = client.post("/api/grid/primary", json={"name": "GL1"}).get_json()
    assert d["grid"]["name"] == "GL1" and d["grid_systems"][0]["kind"] == \
        "general"
    assert client.post("/api/grid/primary", json={"name": "zz"}
                       ).status_code == 404
    cat = client.get("/api/units/catalog").get_json()
    assert cat["kinds"] == json.loads(json.dumps(UC.unit_catalog()))
    assert cat["max_decimals"] == 10


def test_js_mirror_shell_holes_matches_python():
    """js/wc_shellholes.js (through js/wb_shellgeom.js) = core/shell_holes."""
    import shutil
    import subprocess
    import tempfile
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    web = os.path.join(os.path.dirname(__file__), "..", "skyframe", "api",
                       "web", "js")
    tmp = tempfile.mkdtemp()
    shutil.copytree(web, os.path.join(tmp, "js"))
    with open(os.path.join(tmp, "js", "package.json"), "w") as fh:
        fh.write('{"type": "module"}')
    z = 3.2
    m = _slab(openings=[Opening(2 / 6, 1 / 4, 4 / 6, 3 / 4),
                        Opening(0, 0, 1, 1, polygon=[(4.5, 0.5, z),
                                                     (5.5, 0.5, z),
                                                     (5.0, 1.5, z)])])
    d0 = m.to_dict()
    cases = [["shell_divide", _sel(shells=["S1"]), {"mode": "n", "n1": 3,
                                                     "n2": 2}],
             ["shell_chop", _sel(shells=["S1"]),
              {"mode": "lines", "lines": [[[3, -1], [3, 5]]]}],
             ["shell_expand", _sel(shells=["S1"]), {"distance": 0.5}],
             ["shell_reshape", _sel(shells=["S1"]),
              {"mode": "move", "index": 2, "to": [7, 5, 3.3],
               "on_plane": True}]]
    with open(os.path.join(tmp, "m.json"), "w") as fh:
        json.dump(d0, fh)
    with open(os.path.join(tmp, "c.json"), "w") as fh:
        json.dump(cases, fh)
    script = os.path.join(tmp, "run.mjs")
    with open(script, "w") as fh:
        fh.write('import { readFileSync } from "fs";\n'
                 'import { wbApplyEdit } from "./js/wb_shellgeom.js";\n'
                 'const m = JSON.parse(readFileSync(process.argv[2]));\n'
                 'const c = JSON.parse(readFileSync(process.argv[3]));\n'
                 'console.log(JSON.stringify(c.map(([op, s, p]) => '
                 'wbApplyEdit(m, op, s, p).model.shells)));\n')
    js = json.loads(subprocess.check_output(
        [node, script, os.path.join(tmp, "m.json"),
         os.path.join(tmp, "c.json")]))
    for (op, sel, params), shells_js in zip(cases, js):
        d, _ = apply_edit(d0, op, sel, params)
        assert len(d["shells"]) == len(shells_js)
        for a, b in zip(d["shells"], shells_js):
            assert a["uid"] == b["uid"]
            assert _flat(a["corners"]) == pytest.approx(_flat(b["corners"]),
                                                        abs=2e-9)
            assert len(a["openings"]) == len(b["openings"])
            for oa, ob in zip(a["openings"], b["openings"]):
                if "polygon" in oa:
                    assert _flat(oa["polygon"]) == pytest.approx(
                        _flat(ob["polygon"]), abs=2e-9)
                else:
                    for k in ("u0", "v0", "u1", "v1"):
                        assert oa[k] == pytest.approx(ob[k], abs=1e-12)
    shutil.rmtree(tmp, ignore_errors=True)
