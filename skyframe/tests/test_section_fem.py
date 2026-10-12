"""In-house exact section solver (v1.22, skyframe.core.section_fem).

Every reference value is an independent closed form (Saint-Venant series,
polar moments, thin-walled theory, AISC tables) or - only when the
optional package is installed - sectionproperties.
"""

import json
import math
import time

import numpy as np
import pytest

from skyframe.core import builtup as B
from skyframe.core import section_fem as F
from skyframe.core import sections_designer as SD
from skyframe.core import shape_props as SP
from skyframe.core.model import BuildingModel, Material

IN = 0.0254
W14X90 = dict(d=0.3556, bf=0.3683, tf=0.018034, tw=0.011176, r=0.01524)


def _rect(b, t, **kw):
    return dict({"outer": F.rect_loop(-b / 2, b / 2, -t / 2, t / 2)}, **kw)


# --------------------------------------------------------------------------- #
# 1. torsion constant against exact solutions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bt,ref", [(1, 0.1406), (2, 0.2287), (10, 0.3123)])
def test_rectangle_J_matches_saint_venant_series(bt, ref):
    b, t = 0.1 * bt, 0.1
    r = F.solve([_rect(b, t)])
    k = r["J"] / (b * t ** 3)
    assert k == pytest.approx(ref, rel=0.005)                # task table
    assert r["J"] == pytest.approx(SP.rect_J_exact(b, t), rel=5e-4)


def test_circle_and_tube_J_exact():
    R = 0.25
    r = F.solve([{"outer": F.circle_loop(0, 0, R)}])
    assert r["J"] == pytest.approx(math.pi * R ** 4 / 2, rel=0.005)
    assert r["J"] == pytest.approx(math.pi * R ** 4 / 2, rel=1e-4)
    assert r["A"] == pytest.approx(math.pi * R * R, rel=1e-12)  # equal area
    Ri = 0.2
    t = F.solve([{"outer": F.circle_loop(0, 0, R),
                  "holes": [F.circle_loop(0, 0, Ri)]}])
    assert t["J"] == pytest.approx(math.pi * (R ** 4 - Ri ** 4) / 2,
                                   rel=0.005)
    # concentric: shear centre at the centroid; round tube As -> ~A/2
    assert abs(t["x0"]) < 1e-6 and abs(t["y0"]) < 1e-6


def test_thin_I_and_channel_against_thin_walled_theory():
    d, bf, tf, tw = 1.0, 0.5, 0.01, 0.008
    r = F.solve(F.shape_regions("I", dict(d=d, bf=bf, tf=tf, tw=tw)))
    J_thin = (2 * bf * tf ** 3 + (d - tf) * tw ** 3) / 3
    assert r["J"] == pytest.approx(J_thin, rel=0.01)
    Iy = 2 * tf * bf ** 3 / 12 + (d - 2 * tf) * tw ** 3 / 12
    assert r["Cw"] == pytest.approx(Iy * (d - tf) ** 2 / 4, rel=0.005)
    d, bf, tf, tw = 1.0, 0.4, 0.01, 0.01
    c = F.solve(F.shape_regions("channel", dict(d=d, bf=bf, tf=tf, tw=tw)))
    b, h = bf - tw / 2, d - tf                       # mid-line dimensions
    assert c["J"] == pytest.approx((2 * b * tf ** 3 + h * tw ** 3) / 3,
                                   rel=0.01)
    e = 3 * b * b * tf / (6 * b * tf + h * tw)       # from the web mid-line
    x_web = -bf / 2 + tw / 2
    assert x_web - c["xs"] == pytest.approx(e, rel=0.01)
    assert x_web - c["xs_trefftz"] == pytest.approx(e, rel=0.01)
    assert c["ys"] == pytest.approx(0.0, abs=1e-5)


def test_I_section_warping_constant_Iy_h2_over_4():
    d, bf, tf, tw = 0.6, 0.3, 0.012, 0.008
    r = F.solve(F.shape_regions("I", dict(d=d, bf=bf, tf=tf, tw=tw)))
    Iy = 2 * tf * bf ** 3 / 12 + (d - 2 * tf) * tw ** 3 / 12
    assert r["Cw"] == pytest.approx(Iy * (d - tf) ** 2 / 4, rel=0.01)


@pytest.mark.parametrize("bt", [1, 2, 5])
def test_rectangle_shear_area_five_sixths(bt):
    """nu = 0 (the default, = sectionproperties' geometric analysis): the
    energy shear coefficient of a rectangle is exactly 5/6 both ways."""
    r = F.solve([_rect(0.1 * bt, 0.1)])
    assert r["Asx"] == pytest.approx(5 / 6 * r["A"], rel=0.005)
    assert r["Asy"] == pytest.approx(5 / 6 * r["A"], rel=0.005)
    # nu > 0: the Pilkey coefficient of a SQUARE drops below 5/6 (0.8282
    # at nu = 0.3; identical in sectionproperties), a deep section keeps it
    sq = F.solve([_rect(0.1, 0.1)], nu=0.3)
    assert sq["Asy"] / sq["A"] == pytest.approx(0.8282, abs=5e-4)
    deep = F.solve([_rect(0.02, 0.2)], nu=0.3)
    assert deep["Asy"] / deep["A"] == pytest.approx(5 / 6, rel=0.005)


def test_geometric_and_plastic_properties_exact_for_rectangle():
    b, h = 0.3, 0.5
    r = F.solve([{"outer": F.rect_loop(0.1, 0.1 + b, -0.2, -0.2 + h)}])
    assert r["A"] == pytest.approx(b * h, rel=1e-12)
    assert (r["cx"], r["cy"]) == pytest.approx((0.1 + b / 2, -0.2 + h / 2))
    assert r["Ixx"] == pytest.approx(b * h ** 3 / 12, rel=1e-12)
    assert r["Iyy"] == pytest.approx(h * b ** 3 / 12, rel=1e-12)
    assert r["Ixy"] == pytest.approx(0.0, abs=1e-15)
    assert r["Sx_top"] == pytest.approx(b * h * h / 6, rel=1e-12)
    assert r["Zx"] == pytest.approx(b * h * h / 4, rel=1e-9)
    assert r["Zy"] == pytest.approx(h * b * b / 4, rel=1e-9)
    assert r["I_major"] == pytest.approx(b * h ** 3 / 12, rel=1e-12)


# --------------------------------------------------------------------------- #
# 2. W14X90 (with its root fillets) against the AISC tables
# --------------------------------------------------------------------------- #
def test_W14X90_against_AISC_tables():
    """AISC v15: J = 4.06 in^4, Cw = 16000 in^6.  Stated tolerance 2 %
    (the tables use the idealised k1/kdes fillet; the mesh uses the
    database root radius r = kdes - tf)."""
    r = F.solve(F.shape_regions("I", W14X90))
    assert r["J"] / IN ** 4 == pytest.approx(4.06, rel=0.02)
    assert r["Cw"] / IN ** 6 == pytest.approx(16000.0, rel=0.02)
    assert r["A"] / IN ** 2 == pytest.approx(26.5, rel=0.01)
    # without the fillets J is ~7 % low: the fillets matter for J
    r0 = F.solve(F.shape_regions("I", W14X90, fillets=False))
    assert r0["J"] < 0.95 * r["J"]


def test_W14X90_with_cover_plates_J_about_3_5x_the_open_sum():
    mats = {"S": Material("S", E=200e6, nu=0.3)}
    auto = B.template_section("w_cover", {}, material="S")
    ex = B.template_section("w_cover", {}, material="S", torsion="exact")
    pa = B.section_properties(auto, mats)
    pe = B.section_properties(ex, mats)
    assert pa["J_method"] == "open" and pe["J_method"] == "exact"
    assert 3.3 < pe["J"] / pa["J"] < 3.8
    assert pe["Cw"] > 0 and pe["exact"]["mesh"]["elements"] > 100
    # gross properties unchanged by the torsion method
    for k in ("A", "I33", "I22", "Z33", "S33"):
        assert pe[k] == pa[k]
    # FrameSection mirror carries the exact J
    assert B.make_frame_section(ex, mats).J == pytest.approx(pe["J"])


# --------------------------------------------------------------------------- #
# 3. composite, disconnected, touching, input errors
# --------------------------------------------------------------------------- #
def test_modular_ratio_scales_everything():
    r1 = F.solve([_rect(0.2, 0.1)])
    r2 = F.solve([_rect(0.2, 0.1, n=2.5)])
    for k in ("A", "Ixx", "Iyy", "J", "Cw", "Asx", "Asy", "Zx"):
        assert r2[k] == pytest.approx(2.5 * r1[k], rel=1e-9)
    # bimaterial stack: centroid / shear centre move to the stiff part
    st = F.solve([{"outer": F.rect_loop(0, 0.3, 0, 0.5), "n": 1.0},
                  {"outer": F.rect_loop(0, 0.3, 0.5, 0.52), "n": 7.0}])
    A = 0.15 + 7 * 0.006
    assert st["A"] == pytest.approx(A, rel=1e-12)
    assert st["cy"] == pytest.approx((0.15 * 0.25 + 7 * 0.006 * 0.51) / A)
    assert st["xs"] == pytest.approx(0.15, abs=1e-6)


def test_shared_edges_and_overlaps_conform():
    whole = F.solve([_rect(0.4, 0.1)])
    halves = F.solve([{"outer": F.rect_loop(-0.2, 0.0, -0.05, 0.05)},
                      {"outer": F.rect_loop(0.0, 0.2, -0.05, 0.05)}])
    assert halves["J"] == pytest.approx(whole["J"], rel=1e-4)
    assert halves["parts"] == 1
    # a cover plate wider than the flange it sits on (T-junctions)
    t = F.solve([{"outer": F.rect_loop(-0.1, 0.1, 0, 0.02)},
                 {"outer": F.rect_loop(-0.15, 0.15, 0.02, 0.04)}])
    assert t["A_geometric"] == pytest.approx(0.004 + 0.006, rel=1e-12)


def test_disconnected_parts_sum_J_and_leave_shear_undefined():
    a = F.solve([_rect(0.1, 0.3)])
    r = F.solve([{"outer": F.rect_loop(0, 0.1, 0, 0.3)},
                 {"outer": F.rect_loop(0.5, 0.6, 0, 0.3)}])
    assert r["parts"] == 2
    assert r["J"] == pytest.approx(2 * a["J"], rel=1e-4)
    assert r["xs"] is None and r["Asx"] is None and r["Cw"] is None
    assert r["warnings"]


def test_holes_and_unusual_outlines_mesh():
    hole = F.solve([{"outer": F.rect_loop(0, 1, 0, 1),
                     "holes": [F.circle_loop(0.3, 0.6, 0.2)]}])
    assert hole["A"] == pytest.approx(1 - math.pi * 0.04, rel=1e-9)
    acute = F.solve([{"outer": [[0, 0], [1, 0],
                                [1, math.tan(math.radians(5))]]}])
    assert acute["A"] == pytest.approx(0.5 * math.tan(math.radians(5)),
                                       rel=1e-9)
    cw = F.solve([{"outer": F.rect_loop(0, 1, 0, 1)[::-1]}])
    assert cw["J"] == pytest.approx(SP.rect_J_exact(1, 1), rel=5e-4)


@pytest.mark.parametrize("bad", [
    [], [{"outer": [[0, 0], [1, 0]]}], [{"outer": [[0, 0], [1, 0], [2, 0]]}],
    [{"outer": [[0, 0], [1, 0], [1, float("nan")]]}],
    [{"outer": F.rect_loop(0, 1, 0, 1), "n": 0}], "x"])
def test_invalid_input_raises(bad):
    with pytest.raises(ValueError):
        F.solve(bad)
    with pytest.raises(ValueError):
        F.solve([_rect(1, 1)], nu=0.7)


# --------------------------------------------------------------------------- #
# 4. solver internals: banded Cholesky == dense, mesh is Tri6, cache
# --------------------------------------------------------------------------- #
def test_band_solver_matches_dense_solution():
    mesh = F.mesh_regions([_rect(0.3, 0.1)], target=200)
    X, E = mesh["nodes"], mesh["elems"]
    n = len(X)
    assert E.shape[1] == 6
    # Tri6 mid-nodes are the edge midpoints
    assert np.allclose(X[E[:, 3]], (X[E[:, 0]] + X[E[:, 1]]) / 2)
    rng = np.random.default_rng(1)
    rows = np.repeat(E, 6, axis=1).reshape(-1)
    cols = np.tile(E, (1, 6)).reshape(-1)
    Ke = rng.normal(size=(len(E), 6, 6))
    Ke = np.einsum("eij,ekj->eik", Ke, Ke) + 6 * np.eye(6)
    vals = Ke.reshape(-1)
    K = np.zeros((n, n))
    np.add.at(K, (rows, cols), vals)
    f = rng.normal(size=(n, 2))
    x = F._band_solve(n, rows, cols, vals, f, np.array([0]))
    free = np.arange(1, n)
    ref = np.linalg.solve(K[np.ix_(free, free)], f[free])
    assert x[0] == pytest.approx([0, 0])
    assert np.allclose(x[free], ref, rtol=1e-8, atol=1e-10)


def test_cache_by_geometry_hash():
    F.cache_clear()
    regs = F.shape_regions("channel", dict(d=0.3, bf=0.09, tf=0.0135,
                                           tw=0.008))
    a = F.solve(regs)
    b = F.solve(regs)
    assert a["cached"] is False and b["cached"] is True
    assert a["hash"] == b["hash"] and a["J"] == b["J"]
    assert F.cache_info()["hits"] == 1
    b["J"] = -1.0                          # callers get copies
    assert F.solve(regs)["J"] == a["J"]
    c = F.solve(F.shape_regions("channel", dict(d=0.3, bf=0.09, tf=0.014,
                                                tw=0.008)))
    assert c["hash"] != a["hash"] and c["cached"] is False


def test_typical_sections_are_fast():
    F.cache_clear()
    for regs in (F.shape_regions("I", W14X90),
                 F.shape_regions("round_tube", dict(D=0.3, t=0.01)),
                 [_rect(0.3, 0.6)]):
        t0 = time.perf_counter()
        r = F.solve(regs, use_cache=False)
        dt = time.perf_counter() - t0
        assert dt < 1.5, (dt, r["mesh"])       # typically 0.1-0.5 s
        assert 400 <= r["mesh"]["dofs"] <= 12000


# --------------------------------------------------------------------------- #
# 5. integration: built-up / designer / parametric shapes / API
# --------------------------------------------------------------------------- #
def test_builtup_saved_sections_keep_their_method():
    bs = B.BuiltupSection.from_dict({"name": "X", "material": "S",
                                     "components": []})
    assert bs.torsion == "auto"                 # stored default unchanged
    t = B.template_section("plate_girder", {}, material="S")
    assert t.torsion == "auto"
    d = B.template_section("plate_girder", {}, material="S",
                           torsion="exact").to_dict()
    assert d["torsion"] == "exact"
    assert B.BuiltupSection.from_dict(d).to_dict() == d


def test_builtup_composite_exact_uses_modular_ratio():
    mats = {"S": Material("S", E=200e6, nu=0.3),
            "AL": Material("AL", E=200e6 / 3.0, nu=0.33)}
    comps = [{"kind": "plate", "b": 0.2, "t": 0.02, "x": 0.0, "y": 0.0,
              "angle": 0.0},
             {"kind": "plate", "b": 0.2, "t": 0.02, "x": 0.0, "y": 0.02,
              "angle": 0.0, "material": "AL"}]
    bs = B.BuiltupSection.from_dict({"name": "C", "material": "S",
                                     "components": comps,
                                     "torsion": "exact"})
    p = B.section_properties(bs, mats)
    ref = F.solve([{"outer": F.rect_loop(-0.1, 0.1, -0.01, 0.01)},
                   {"outer": F.rect_loop(-0.1, 0.1, 0.01, 0.03),
                    "n": 1 / 3}])
    assert p["J"] == pytest.approx(ref["J"], rel=1e-9)
    assert p["J_method"] == "exact"


def _designer(torsion=None):
    d = {"name": "D1", "material": "C", "base": "concrete",
         "polygons": [{"vertices": [[-0.15, -0.3], [0.15, -0.3],
                                    [0.15, 0.3], [-0.15, 0.3]],
                       "material": "C"}], "rebar": []}
    if torsion:
        d["torsion"] = torsion
    return d


def test_designer_torsion_field_round_trip_and_exact_J():
    mats = {"C": Material("C", E=25e6, nu=0.2)}
    old = SD.DesignerSection.from_dict(_designer())
    assert old.torsion == "approx" and "torsion" not in old.to_dict()
    pa = SD.section_properties(old, mats)
    assert pa["J_method"] == "approx"
    assert pa["J"] == pytest.approx(pa["A"] ** 4 / (40 * pa["Ip"]))
    new = SD.DesignerSection.from_dict(_designer("exact"))
    assert new.to_dict()["torsion"] == "exact"
    pe = SD.section_properties(new, mats)
    assert pe["J_method"] == "exact"
    assert pe["J"] == pytest.approx(SP.rect_J_exact(0.3, 0.6), rel=5e-4)
    assert pe["As2"] == pytest.approx(5 / 6 * 0.18, rel=0.005)
    assert SD.make_frame_section(new, mats).J == pytest.approx(pe["J"])
    bad = SD.DesignerSection.from_dict(_designer("wrong"))
    with pytest.raises(ValueError):
        SD.validate_designer_section(bad, mats)


def test_shape_props_exact_option_and_closed_default():
    closed = SP.shape_properties("I", W14X90)
    assert closed["J_method"] == "closed_form"
    ex = SP.shape_properties("I", W14X90, "exact")
    assert ex["J_method"] == "exact"
    assert ex["J"] / IN ** 4 == pytest.approx(4.06, rel=0.02)
    assert ex["x0"] == 0.0 and ex["y0"] == 0.0
    for k in ("A", "I33", "I22", "S33", "Z33"):
        assert ex[k] == closed[k]
    # channel: exact shear centre close to the thin-walled formula
    ch = dict(d=0.3, bf=0.09, tf=0.0135, tw=0.008)
    assert SP.shape_properties("channel", ch, "exact")["x0"] == \
        pytest.approx(SP.shape_properties("channel", ch)["x0"], rel=0.1)
    with pytest.raises(ValueError):
        SP.shape_properties("I", W14X90, "bogus")
    # a parametric FrameSection opts in with dims.torsion = "exact"
    from skyframe.core.section_ext import fill_from_dims, section_from_dims
    sec = section_from_dims("P1", "S", "I", dict(W14X90, torsion="exact"))
    assert sec.J == pytest.approx(ex["J"])
    assert section_from_dims("P2", "S", "I", W14X90).J == closed["J"]
    kw = fill_from_dims("P3", {"shape": "I",
                               "dims": dict(W14X90, torsion="exact")})
    assert kw["J"] == pytest.approx(ex["J"])


def test_database_import_keeps_tabulated_J():
    from skyframe.core import steel_db
    sec = steel_db.section_from_db("aisc", "W14X90", "S")
    assert sec.J == pytest.approx(4.06 * IN ** 4, rel=1e-6)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api.server import create_app
    app = create_app()
    app.testing = True
    return app.test_client()


def test_api_exact_endpoint(client):
    m = client.get("/api/model").get_json()
    before = json.dumps(m, sort_keys=True)
    r = client.post("/api/sections/exact",
                    json={"kind": "shape", "type": "I",
                          "dims": W14X90}).get_json()
    assert r["method"] == "exact"
    assert r["props"]["J"] / IN ** 4 == pytest.approx(4.06, rel=0.02)
    reg = client.post("/api/sections/exact", json={
        "kind": "regions", "regions": [_rect(0.2, 0.1)]}).get_json()
    assert reg["props"]["J"] == pytest.approx(SP.rect_J_exact(0.2, 0.1),
                                              rel=5e-4)
    mats = {"S": {"E": 200e6, "nu": 0.3}}
    tpl = client.post("/api/sections/builtup/template",
                      json={"id": "w_cover", "name": "BU1"}).get_json()
    assert tpl["section"]["torsion"] == "exact"      # NEW section default
    bu = client.post("/api/sections/exact", json={
        "kind": "builtup", "section": dict(tpl["section"], material="S"),
        "materials": mats}).get_json()
    assert bu["method"] == "exact" and bu["props"]["Cw"] > 0
    ds = client.post("/api/sections/exact", json={
        "kind": "designer", "section": dict(_designer(), material="S",
                                            polygons=[dict(
                                                _designer()["polygons"][0],
                                                material="S")]),
        "materials": mats}).get_json()
    assert ds["props"]["J_method"] == "exact"
    sp = client.post("/api/steel_db/shape_props", json={
        "type": "I", "dims": W14X90, "torsion": "exact"}).get_json()
    assert sp["props"]["J_method"] == "exact"
    sp0 = client.post("/api/steel_db/shape_props", json={
        "type": "I", "dims": W14X90}).get_json()
    assert sp0["props"]["J_method"] == "closed_form"
    for bad in ({"kind": "nope"}, {"kind": "regions", "regions": []},
                {"kind": "shape", "type": "I", "dims": {}}):
        assert client.post("/api/sections/exact", json=bad).status_code \
            == 400
    info = client.get("/api/sections/exact/info").get_json()
    assert info["solver"] == "section_fem"
    assert json.dumps(client.get("/api/model").get_json(),
                      sort_keys=True) == before          # never mutates


def test_default_model_results_unchanged_by_saved_methods():
    """A model whose built-up / designer sections were saved before v1.22
    (no torsion key / "auto") analyses with the closed-form J."""
    mdl = BuildingModel(name="T")
    mdl.add_material(Material("S", E=200e6, nu=0.3))
    bs = B.template_section("w_cover", {}, name="BU", material="S")
    mdl.add_builtup_section(bs)
    d = mdl.to_dict()
    assert d["builtup_sections"]["BU"]["torsion"] == "auto"
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert json.dumps(m2.to_dict(), sort_keys=True) == json.dumps(
        d, sort_keys=True)
    assert m2.sections["BU"].J == pytest.approx(
        B.section_properties(bs, mdl.materials)["J"])


# --------------------------------------------------------------------------- #
# 6. independent cross-check (only when sectionproperties is installed)
# --------------------------------------------------------------------------- #
def _sp_solve(regs, nu=None):
    from shapely import Polygon
    from sectionproperties.analysis.section import Section
    from sectionproperties.pre.geometry import CompoundGeometry, Geometry
    from sectionproperties.pre.pre import Material as SPMat
    geoms = []
    for r in regs:
        poly = Polygon(r["outer"], r.get("holes", []))
        if nu is None:
            geoms.append(Geometry(poly))
        else:
            geoms.append(Geometry(poly, material=SPMat(
                "m%g" % r.get("n", 1.0), elastic_modulus=r.get("n", 1.0),
                poissons_ratio=nu, yield_strength=1.0, density=1.0,
                color="w")))
    g = CompoundGeometry(geoms) if len(geoms) > 1 else geoms[0]
    area = sum(Polygon(r["outer"], r.get("holes", [])).area for r in regs)
    g.create_mesh(mesh_sizes=[area / 3000])
    s = Section(g)
    s.calculate_geometric_properties()
    s.calculate_warping_properties()
    if nu is None:
        J, (asx, asy), cw = s.get_j(), s.get_as(), s.get_gamma()
    else:
        J, (asx, asy), cw = s.get_ej(), s.get_eas(), s.get_egamma()
    xs, ys = s.get_sc()
    return {"J": J, "Asx": asx, "Asy": asy, "Cw": cw, "xs": xs, "ys": ys}


@pytest.mark.parametrize("name", ["angle", "channel", "W_cover",
                                  "composite"])
def test_cross_check_with_sectionproperties(name):
    pytest.importorskip("sectionproperties")
    nu = None
    if name == "angle":
        regs = F.shape_regions("angle", dict(d=0.15, b=0.1, t=0.012))
    elif name == "channel":
        regs = F.shape_regions("channel", dict(d=0.3, bf=0.09, tf=0.0135,
                                               tw=0.008))
    elif name == "W_cover":
        regs = F.shape_regions("I", W14X90, fillets=False) + [
            {"outer": F.rect_loop(-0.2, 0.2, 0.1778, 0.1978)}]
    else:
        regs = [{"outer": F.rect_loop(0, 0.3, 0, 0.5), "n": 1.0},
                {"outer": F.rect_loop(0, 0.3, 0.5, 0.52), "n": 7.0}]
        nu = 0.2
    ours = F.solve(regs, nu=nu or 0.0)
    ref = _sp_solve(regs, nu)
    scale = max(np.ptp(np.concatenate([np.asarray(r["outer"])
                                       for r in regs]), axis=0))
    for k in ("J", "Asx", "Asy", "Cw"):
        assert ours[k] == pytest.approx(ref[k], rel=0.01), k
    for k in ("xs", "ys"):
        assert abs(ours[k] - ref[k]) <= 0.01 * abs(ref[k]) + 1e-4 * scale, k


def test_parametric_exact_section_round_trips_and_self_heals():
    """dims.torsion = "exact" survives save/load byte-identically and a
    stale J in the file is replaced by the exact value on load."""
    mdl = BuildingModel(name="T")
    mdl.add_material(Material("S", E=200e6, nu=0.3))
    from skyframe.core.section_ext import section_from_dims
    mdl.add_section(section_from_dims("PX", "S", "channel",
                                      {"d": 0.3, "bf": 0.09, "tf": 0.0135,
                                       "tw": 0.008, "torsion": "exact"}))
    d = mdl.to_dict()
    assert d["sections"]["PX"]["dims"]["torsion"] == "exact"
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert json.dumps(m2.to_dict(), sort_keys=True) == json.dumps(
        d, sort_keys=True)
    stale = json.loads(json.dumps(d))
    stale["sections"]["PX"]["J"] = 1.0
    m3 = BuildingModel.from_dict(stale)
    assert m3.sections["PX"].J == pytest.approx(d["sections"]["PX"]["J"])
    bad = json.loads(json.dumps(d))
    bad["sections"]["PX"]["dims"]["torsion"] = "wrong"
    with pytest.raises(ValueError):
        BuildingModel.from_dict(bad).validate()
