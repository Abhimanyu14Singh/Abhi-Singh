"""Wave 15 backend tests (v0.14) — multiple / rotated / radial grid systems.

Grids are DRAFTING AIDS ONLY: members keep absolute global coordinates so the
analysis engine is untouched.  These tests cover the model + snapping helpers.

Rigor bar as in earlier waves: every expected number is hand-derived from the
rotation matrix or a polar identity, independent of any solver.

Coordinate transform (documented, CONTRACT v0.14): a LOCAL grid point (lx, ly)
maps to GLOBAL via a CCW rotation ``rot`` about the origin then a translation:

    gx = ox + lx*cos(rot) - ly*sin(rot)
    gy = oy + lx*sin(rot) + ly*cos(rot)

Units per CONTRACT.md: kN, m, tonne, s.
"""

import math

import pytest

from skyframe.core.builder import quick_building
from skyframe.core.model import BuildingModel, GridSystem


TOL = 1e-9


def _close(a, b, tol=TOL):
    return abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# 1. Orthogonal grid at origin, rotation 0
# --------------------------------------------------------------------------- #
def test_orthogonal_intersections_and_labels():
    g = GridSystem(x_lines=[0.0, 6.0, 12.0], y_lines=[0.0, 4.0])
    inter = g.intersections_global()
    # 3 x-lines * 2 y-lines = 6 crossings
    assert len(inter) == 6
    by_label = {it["label"]: it["point"] for it in inter}
    # labels are "A".."C" across x, "1".."2" up y
    assert set(by_label) == {"A-1", "A-2", "B-1", "B-2", "C-1", "C-2"}
    # each crossing is exactly (x_line, y_line) with no transform
    assert by_label["A-1"] == [0.0, 0.0]
    assert by_label["B-2"] == [6.0, 4.0]
    assert by_label["C-2"] == [12.0, 4.0]
    # lines_global: 3 vertical + 2 horizontal, each a 2-point segment
    lines = g.lines_global()
    assert len(lines) == 5
    vlineB = next(ln for ln in lines if ln["label"] == "B")
    assert vlineB["points"] == [[6.0, 0.0], [6.0, 4.0]]


# --------------------------------------------------------------------------- #
# 2. Rotated grid (origin (10,5), rotation 90 deg)
# --------------------------------------------------------------------------- #
def test_rotated_grid_maps_by_hand():
    g = GridSystem(x_lines=[0.0, 6.0], y_lines=[0.0, 3.0],
                   name="WING", origin=(10.0, 5.0), rotation=90.0)
    # local (0,0) -> origin exactly
    gx, gy = g.to_global(0.0, 0.0)
    assert _close(gx, 10.0) and _close(gy, 5.0)
    # local (6,0) at 90 deg: cos90=0, sin90=1 -> (10 + 0, 5 + 6) = (10, 11)
    gx, gy = g.to_global(6.0, 0.0)
    assert _close(gx, 10.0) and _close(gy, 11.0)
    # local (0,3) -> (10 - 3, 5 + 0) = (7, 5)
    gx, gy = g.to_global(0.0, 3.0)
    assert _close(gx, 7.0) and _close(gy, 5.0)
    # intersection labels come from local line indices, not global position
    by_label = {it["label"]: it["point"] for it in g.intersections_global()}
    # B is x=6, "1" is y=0 -> local (6,0) -> global (10,11)
    px, py = by_label["B-1"]
    assert _close(px, 10.0) and _close(py, 11.0)


def test_rotated_grid_snap():
    g = GridSystem(x_lines=[0.0, 6.0], y_lines=[0.0, 3.0],
                   name="WING", origin=(10.0, 5.0), rotation=90.0)
    # a click a hair off the global (10, 11) point snaps to B-1
    hit = g.snap(10.02, 10.98, tol=0.05)
    assert hit is not None
    assert hit["label"] == "B-1"
    assert _close(hit["point"][0], 10.0) and _close(hit["point"][1], 11.0)
    # outside tolerance -> None
    assert g.snap(10.5, 11.5, tol=0.05) is None


# --------------------------------------------------------------------------- #
# 3. Radial grid
# --------------------------------------------------------------------------- #
def test_radial_intersections_by_hand():
    g = GridSystem(name="RAD", kind="radial",
                   radii=[4.0, 8.0], theta_deg=[0.0, 90.0, 180.0, 270.0])
    inter = g.intersections_global()
    # 2 radii * 4 spokes = 8 crossings
    assert len(inter) == 8
    by_label = {it["label"]: it["point"] for it in inter}
    # radius 8, theta 90 -> (8*cos90, 8*sin90) = (0, 8)
    px, py = by_label["R2-90°"]
    assert _close(px, 0.0) and _close(py, 8.0)
    # radius 4, theta 180 -> (-4, 0)
    px, py = by_label["R1-180°"]
    assert _close(px, -4.0) and _close(py, 0.0)
    # radius 8, theta 0 -> (8, 0)
    px, py = by_label["R2-0°"]
    assert _close(px, 8.0) and _close(py, 0.0)
    # circles drawn as closed polylines + one spoke per angle
    lines = g.lines_global()
    circles = [ln for ln in lines if ln["label"].startswith("R")]
    spokes = [ln for ln in lines if ln["label"].endswith("°")]
    assert len(circles) == 2 and len(spokes) == 4
    # every circle-1 polyline point sits at radius 4 from the origin
    c1 = next(ln for ln in circles if ln["label"] == "R1")
    for px, py in c1["points"]:
        assert _close(math.hypot(px, py), 4.0)


def test_radial_grid_offset_origin():
    # center at (2, 1): radius 8 theta 90 -> (2+0, 1+8) = (2, 9)
    g = GridSystem(name="RAD", kind="radial", origin=(2.0, 1.0),
                   radii=[8.0], theta_deg=[90.0])
    (it,) = g.intersections_global()
    assert it["label"] == "R1-90°"
    assert _close(it["point"][0], 2.0) and _close(it["point"][1], 9.0)


# --------------------------------------------------------------------------- #
# 4. Two grid systems: union + nearest snapping across both
# --------------------------------------------------------------------------- #
def _two_system_model():
    m = BuildingModel(name="TwoGrid")
    ortho = GridSystem(x_lines=[0.0, 6.0], y_lines=[0.0, 6.0], name="G1")
    wing = GridSystem(x_lines=[0.0, 5.0], y_lines=[0.0],
                      name="WING", origin=(6.0, 6.0), rotation=30.0)
    m.grid_systems = [ortho, wing]
    m.grid = ortho
    return m, ortho, wing


def test_two_systems_union_and_cross_snap():
    m, ortho, wing = _two_system_model()
    allx = m.all_intersections_global()
    # 4 from the orthogonal grid + 2 from the 2x1 wing = 6, each tagged
    assert len(allx) == len(ortho.intersections_global()) \
        + len(wing.intersections_global()) == 6
    systems = {it["system"] for it in allx}
    assert systems == {"G1", "WING"}

    # wing local (5,0) at 30 deg about (6,6):
    a = math.radians(30.0)
    wx = 6.0 + 5.0 * math.cos(a)
    wy = 6.0 + 5.0 * math.sin(a)
    hit = m.snap(wx + 0.01, wy - 0.01, tol=0.1)
    assert hit is not None and hit["system"] == "WING"
    assert _close(hit["point"][0], wx) and _close(hit["point"][1], wy)

    # a click near the orthogonal corner (6,0) snaps to G1, not the wing
    hit2 = m.snap(6.05, 0.05, tol=0.2)
    assert hit2 is not None and hit2["system"] == "G1"
    assert hit2["label"] == "B-1"


# --------------------------------------------------------------------------- #
# 5. Backward compatibility
# --------------------------------------------------------------------------- #
def test_legacy_grid_serialises_as_one_system():
    m = BuildingModel(name="Legacy")
    m.grid = GridSystem(x_lines=[0.0, 6.0, 12.0], y_lines=[0.0, 4.0, 8.0])
    d = m.to_dict()
    # to_dict emits BOTH a primary `grid` and a one-entry `grid_systems`
    assert d["grid"]["x_lines"] == [0.0, 6.0, 12.0]
    assert len(d["grid_systems"]) == 1
    assert d["grid_systems"][0]["x_lines"] == d["grid"]["x_lines"]
    assert d["grid_systems"][0]["y_lines"] == d["grid"]["y_lines"]
    # from_dict(to_dict()) round-trips identically
    m2 = BuildingModel.from_dict(d)
    assert m2.to_dict() == d
    # the legacy `grid` attribute still works
    assert m2.grid is not None
    assert m2.grid.x_lines == [0.0, 6.0, 12.0]
    assert m2.grid.x_labels == ["A", "B", "C"]


def test_quick_building_unaffected():
    m = quick_building()
    ext0 = m.plan_extents()
    ctr0 = m.plan_center()
    d = m.to_dict()
    # quick_building's grid still round-trips and reports the same plan extents
    m2 = BuildingModel.from_dict(d)
    assert m2.to_dict() == d
    assert m2.plan_extents() == ext0
    assert m2.plan_center() == ctr0
    # engine-facing member coordinates are untouched by the grid work
    assert [mem.to_dict() for mem in m.members] \
        == [mem.to_dict() for mem in m2.members]


def test_model_with_no_grid():
    m = BuildingModel(name="Bare")
    assert m.effective_grids() == []
    assert m.all_intersections_global() == []
    assert m.snap(0.0, 0.0, 1.0) is None
    d = m.to_dict()
    assert d["grid"] is None
    assert d["grid_systems"] == []


def test_plan_extents_union_of_systems():
    # An orthogonal grid 0..6 in x/y plus a wing pushing x out to 6+5=11.
    m, ortho, wing = _two_system_model()
    lx, ly = m.plan_extents()
    b = m._grid_plan_bbox()
    assert b is not None
    # wing local (5,0) at 30 deg from (6,6) reaches x = 6 + 5cos30 ~= 10.33
    a = math.radians(30.0)
    assert _close(b[1], 6.0 + 5.0 * math.cos(a))   # max x from the wing
    assert lx == pytest.approx(b[1] - b[0])
    assert ly == pytest.approx(b[3] - b[2])


# --------------------------------------------------------------------------- #
# 6. Validation
# --------------------------------------------------------------------------- #
def test_radial_without_radii_invalid():
    m = BuildingModel(name="BadRadial")
    m.grid_systems = [GridSystem(name="R", kind="radial", radii=[],
                                 theta_deg=[0.0])]
    with pytest.raises(ValueError):
        m.validate()


def test_bad_grid_kind_invalid():
    m = BuildingModel(name="BadKind")
    m.grid_systems = [GridSystem(name="G", kind="polar")]
    with pytest.raises(ValueError):
        m.validate()


# --------------------------------------------------------------------------- #
# 7. API
# --------------------------------------------------------------------------- #
@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_api_model_roundtrips_grid_systems(api_client):
    m = BuildingModel(name="Grids")
    m.grid_systems = [
        GridSystem(x_lines=[0.0, 6.0], y_lines=[0.0, 4.0], name="G1"),
        GridSystem(name="RAD", kind="radial", origin=(3.0, 2.0),
                   radii=[4.0, 8.0], theta_deg=[0.0, 90.0]),
    ]
    m.grid = m.grid_systems[0]
    resp = api_client.post("/api/model", json=m.to_dict())
    assert resp.status_code == 200
    d = resp.get_json()
    assert len(d["grid_systems"]) == 2
    assert d["grid_systems"][1]["kind"] == "radial"
    assert d["grid_systems"][1]["radii"] == [4.0, 8.0]
    assert d["grid"]["name"] == "G1"


def test_api_grid_endpoint_add_and_replace(api_client):
    # default model is quick_building (has an orthogonal grid named G1)
    add = api_client.post("/api/grid", json={
        "name": "WING", "kind": "orthogonal",
        "x_lines": [0.0, 5.0], "y_lines": [0.0],
        "origin": [10.0, 5.0], "rotation": 90.0})
    assert add.status_code == 200
    names = [g["name"] for g in add.get_json()["grid_systems"]]
    assert "WING" in names and "G1" in names   # legacy grid migrated + added

    # radial add
    rad = api_client.post("/api/grid", json={
        "name": "RAD", "kind": "radial", "radii": [4.0], "theta_deg": [90.0]})
    assert rad.status_code == 200
    d = rad.get_json()
    wing = next(g for g in d["grid_systems"] if g["name"] == "WING")
    assert wing["rotation"] == 90.0

    # replace WING (same name) rather than append a duplicate
    rep = api_client.post("/api/grid", json={
        "name": "WING", "x_lines": [0.0], "y_lines": [0.0, 9.0]})
    assert rep.status_code == 200
    d = rep.get_json()
    wings = [g for g in d["grid_systems"] if g["name"] == "WING"]
    assert len(wings) == 1
    assert wings[0]["y_lines"] == [0.0, 9.0]


def test_api_grid_radial_without_radii_400(api_client):
    resp = api_client.post("/api/grid",
                           json={"name": "R", "kind": "radial"})
    assert resp.status_code == 400
    assert "radi" in resp.get_json()["error"].lower()
