import re
"""v1.21 steel section database (AISC Shapes Database v16.0), loader API,
HTTP endpoints, FrameSection dims / db_ref / principal round trip, the
byte-identical legacy W-shape library and the principal-axis analysis of
unsymmetric sections.

Published values in SPOT are the AISC Shapes Database v16.0 (US units) as
tabulated; every expected SI number is ``value * 0.0254**n``.
"""

import json
import math
import shutil

import pytest

from skyframe.core import steel_db as sdb
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 NodalLoad, PointSupport)
from skyframe.core.section_ext import section_from_dims
from skyframe.core.shape_props import SHAPE_TYPES, shape_properties

IN = 0.0254
P_IN = {"A": 2, "Ix": 4, "Iy": 4, "J": 4, "Cw": 6, "Zx": 3, "Sx": 3,
        "Iw": 4, "Iz": 4, "eo": 1}
SI_KEY = {"A": "A", "Ix": "I33", "Iy": "I22", "J": "J", "Cw": "Cw",
          "Zx": "Z33", "Sx": "S33", "Iw": "I_major", "Iz": "I_minor",
          "eo": "eo"}

# --------------------------------------------------------------------------- #
# 23 shapes across all 15 families: published AISC v16.0 values (US units)
# --------------------------------------------------------------------------- #
SPOT = {
    "W14X90": {"A": 26.5, "Ix": 999, "Iy": 362, "J": 4.06, "Zx": 157,
               "Sx": 143, "Cw": 16000},
    "W44X408": {"A": 120, "Ix": 38700, "Iy": 1520, "J": 134, "Cw": 691000},
    "W12X26": {"A": 7.65, "Ix": 204, "Iy": 17.3, "J": 0.300},
    "M12.5X12.4": {"A": 3.63, "Ix": 89.3, "Iy": 2.01, "J": 0.0493},
    "S24X121": {"A": 35.5, "Ix": 3160, "Iy": 83, "J": 12.8},
    "HP18X204": {"A": 60.2, "Ix": 3480, "Iy": 1120, "J": 29.5},
    "C15X50": {"A": 14.7, "Ix": 404, "Iy": 11.0, "J": 2.65, "eo": 0.583},
    "MC18X58": {"A": 17.1, "Ix": 675, "Iy": 17.6, "J": 2.81},
    "L8X6X1": {"A": 13.1, "Ix": 80.9, "Iy": 38.8, "J": 4.34, "Iw": 98.4,
               "Iz": 21.3},
    "L4X3X1/4": {"A": 1.69, "Ix": 2.75, "Iy": 1.33, "J": 0.0386,
                 "Iw": 3.39, "Iz": 0.692},
    "L12X12X1-3/8": {"A": 31.1, "Ix": 413, "Iy": 413, "J": 19.9,
                     "Iw": 661, "Iz": 165},
    "2L8X6X1LLBB": {"A": 26.2, "Ix": 162, "Iy": 150},
    "2L8X6X1SLBB": {"A": 26.2, "Ix": 77.6, "Iy": 345},
    "2L12X12X1-3/8": {"A": 62.2, "Ix": 826, "Iy": 1590},
    "WT22X204": {"A": 59.9, "Ix": 2670, "Iy": 758, "J": 66.9},
    "WT7X45": {"A": 13.2, "Ix": 36.5, "Iy": 181, "J": 2.03},
    "MT6.25X6.2": {"A": 1.82, "Ix": 7.29, "Iy": 1.0, "J": 0.0246},
    "ST12X60.5": {"A": 17.8, "Ix": 259, "Iy": 41.5, "J": 6.38},
    "HSS34X10X1": {"A": 76.2, "Ix": 9600, "Iy": 1340, "J": 4040},
    "HSS20X12X5/8": {"A": 35.0, "Ix": 1880, "Iy": 851, "J": 1890},
    "HSS16.000X0.625": {"A": 28.1, "Ix": 838, "Iy": 838, "J": 1680},
    "Pipe26STD": {"A": 28.2, "Ix": 2320, "Iy": 2320, "J": 4640},
    "Pipe8STD": {"A": 7.85, "Ix": 68.1, "Iy": 68.1, "J": 136},
}

FAMILY_COUNTS = {"W": 289, "M": 16, "S": 28, "HP": 22, "C": 32, "MC": 40,
                 "L": 137, "2L": 639, "WT": 289, "MT": 14, "ST": 28,
                 "HSS Rect": 399, "HSS Square": 126, "HSS Round": 189,
                 "Pipe": 51}


@pytest.fixture(autouse=True)
def _fresh_cache():
    sdb.clear_cache()
    yield
    sdb.clear_cache()


# --------------------------------------------------------------------------- #
# the AISC file
# --------------------------------------------------------------------------- #
def test_aisc_is_complete_v16():
    st = {s["id"]: s for s in sdb.standards()}
    assert st["aisc"]["standard"] == "AISC"
    assert st["aisc"]["edition"] == "AISC Shapes Database v16.0"
    assert st["aisc"]["count"] == 2299
    assert sdb.families("aisc") == list(FAMILY_COUNTS)
    for fam, n in FAMILY_COUNTS.items():
        assert len(sdb.shapes("aisc", fam)) == n, fam
    names = [s["name"] for s in sdb.shapes("aisc")]
    assert len(set(names)) == len(names)


def test_every_record_is_well_formed():
    need = ("A", "I33", "I22", "J", "S33", "S22", "Z33", "Z22", "As2", "As3",
            "r33", "r22", "x0", "y0", "mass")
    for r in sdb.shapes("aisc"):
        assert r["type"] in SHAPE_TYPES
        p = r["props"]
        for k in need:
            assert isinstance(p[k], float) and math.isfinite(p[k]), \
                (r["name"], k)
        for k in ("A", "I33", "I22", "J", "As2", "As3", "mass"):
            assert p[k] > 0, (r["name"], k)
        assert set(r["computed"]) >= {"As2", "As3"}
        # r = sqrt(I/A) holds to the 3-significant-figure rounding
        assert p["r33"] == pytest.approx(math.sqrt(p["I33"] / p["A"]),
                                         rel=0.02), r["name"]
        if r["type"] == "angle":
            assert p["I_major"] >= p["I_minor"] > 0
            assert 0 < p["alpha"] <= math.pi / 4 + 1e-12


@pytest.mark.parametrize("name", list(SPOT))
def test_spot_check_published_values(name):
    rec = sdb.shape("aisc", name)
    for key, val in SPOT[name].items():
        exp = val * IN ** P_IN[key]
        assert rec["props"][SI_KEY[key]] == pytest.approx(exp, rel=1e-12), \
            (name, key)
        assert rec["aisc"][key] == val          # verbatim source block


def test_unit_conversion_and_mass():
    w = sdb.shape("aisc", "W14X90")
    assert w["props"]["A"] == 26.5 * 0.0254 ** 2
    assert w["props"]["mass"] == pytest.approx(90 * 0.45359237 / 0.3048,
                                               rel=1e-14)     # 133.93 kg/m
    assert w["dims"] == pytest.approx({"d": 14 * IN, "bf": 14.5 * IN,
                                       "tf": 0.71 * IN, "tw": 0.44 * IN,
                                       "r": (1.31 - 0.71) * IN})


def test_column_mapping_formulas():
    w = sdb.shape("aisc", "W14X90")
    d, bf, tf, tw = (w["dims"][k] for k in ("d", "bf", "tf", "tw"))
    assert w["props"]["As2"] == pytest.approx(d * tw, rel=1e-15)
    assert w["props"]["As3"] == pytest.approx(5 / 3 * bf * tf, rel=1e-15)
    h = sdb.shape("aisc", "HSS20X12X5/8")
    assert h["type"] == "rect_tube" and h["family"] == "HSS Rect"
    assert h["dims"]["t"] == 0.581 * IN                   # tdes, not tnom
    assert h["dims"]["r_out"] == pytest.approx(2 * 0.581 * IN)
    assert h["props"]["As2"] == pytest.approx(2 * 20 * IN * 0.581 * IN)
    assert h["props"]["As3"] == pytest.approx(2 * 12 * IN * 0.581 * IN)
    p = sdb.shape("aisc", "Pipe8STD")
    assert p["props"]["As2"] == p["props"]["A"] / 2
    # angle: long leg vertical (d), principal data, shear centre at the heel
    a = sdb.shape("aisc", "L8X6X1")
    assert (a["dims"]["d"], a["dims"]["b"]) == (8 * IN, 6 * IN)
    assert a["props"]["alpha"] == math.atan(0.542)
    assert a["props"]["x0"] == pytest.approx((0.5 - 1.65) * IN)
    assert a["props"]["y0"] == pytest.approx((0.5 - 2.65) * IN)
    # channel shear centre: -(x + eo) from the centroid (web on the left)
    c = sdb.shape("aisc", "C15X50")
    assert c["props"]["x0"] == pytest.approx(-(0.799 + 0.583) * IN)
    # 2L: J = 2 x single-angle J (two separate angles), spacing decoded
    two = sdb.shape("aisc", "2L8X6X1X3/4LLBB")
    assert two["dims"]["s"] == pytest.approx(0.75 * IN)
    assert two["dims"]["orientation"] == "LLBB"
    assert two["props"]["J"] == pytest.approx(2 * 4.34 * IN ** 4)
    assert "J" in two["computed"]
    eq = sdb.shape("aisc", "2L12X12X1-3/8X1-1/2")
    assert eq["dims"]["s"] == pytest.approx(1.5 * IN)
    assert eq["dims"]["orientation"] == "EQUAL"


def test_lookup_search_and_aliases():
    assert sdb.shape("aisc", "w14x90")["name"] == "W14X90"
    assert sdb.shape("aisc", "HSS20X12X.625")["name"] == "HSS20X12X5/8"
    assert sdb.standard_id("AISC") == "aisc"
    q = [s["name"] for s in sdb.shapes("aisc", "W", "w14")]
    assert q and all(n.startswith("W14X") for n in q)
    assert [s["name"] for s in sdb.shapes("aisc", query="hss 20x12 5/8")] \
        == ["HSS20X12X5/8"]
    with pytest.raises(KeyError):
        sdb.shape("aisc", "W99X999")
    with pytest.raises(KeyError):
        sdb.families("nope")


def test_loader_discovers_any_database_file(tmp_path, monkeypatch):
    shutil.copy(sdb.DATA_DIR / "aisc.json", tmp_path / "aisc.json")
    rec = sdb.shape("aisc", "W14X90")
    other = {"standard": "Eurocode", "edition": "test", "source": "unit test",
             "units": "SI (m, m^2, m^4, m^6, kg/m)",
             "shapes": [{"name": "IPE300", "family": "IPE", "type": "I",
                         "dims": {"d": 0.3, "bf": 0.15, "tf": 0.0107,
                                  "tw": 0.0071},
                         "props": {k: v for k, v in rec["props"].items()}}]}
    (tmp_path / "eurocode.json").write_text(json.dumps(other))
    monkeypatch.setattr(sdb, "DATA_DIR", tmp_path)
    sdb.clear_cache()
    assert [s["id"] for s in sdb.standards()] == ["aisc", "eurocode"]
    assert sdb.families("Eurocode") == ["IPE"]
    s = sdb.section_from_db("eurocode", "IPE300", "S")
    assert s.db_ref == {"standard": "eurocode", "name": "IPE300"}
    assert (s.b, s.h) == pytest.approx((0.15, 0.3))


# --------------------------------------------------------------------------- #
# FrameSection from the database
# --------------------------------------------------------------------------- #
def test_section_from_db_fields():
    s = sdb.section_from_db("aisc", "W14X90", "STL")
    r = sdb.shape("aisc", "W14X90")["props"]
    assert (s.A, s.I33, s.I22, s.J) == (r["A"], r["I33"], r["I22"], r["J"])
    assert (s.As2, s.As3) == (r["As2"], r["As3"])
    assert (s.b, s.h) == pytest.approx((14.5 * IN, 14 * IN))
    assert s.shape == "I" and s.dims["d"] == 14 * IN
    assert s.db_ref == {"standard": "aisc", "name": "W14X90"}
    assert s.principal is None and s.use_principal is False
    a = sdb.section_from_db("aisc", "L4X3X1/4", "STL", section_name="ANG")
    assert a.name == "ANG" and a.shape == "angle"
    assert a.principal == pytest.approx({"I_major": 3.39 * IN ** 4,
                                         "I_minor": 0.692 * IN ** 4,
                                         "alpha": math.atan(0.558)})
    two = sdb.section_from_db("aisc", "2L8X6X1X3/4LLBB", "STL")
    assert two.b == pytest.approx((2 * 6 + 0.75) * IN)          # outline


def test_import_sections_into_model():
    m = BuildingModel()
    m.add_material(Material("STL", 2e8, 0.3, 77.0, material_type="steel"))
    added = sdb.import_sections(m, "aisc", ["W14X90", "HSS20X12X5/8",
                                            "L4X3X1/4"], "STL")
    assert added == ["W14X90", "HSS20X12X5/8", "L4X3X1/4"]
    assert sdb.import_sections(m, "aisc", ["W14X90"], "STL") == []
    with pytest.raises(ValueError):
        sdb.import_sections(m, "aisc", ["W14X90"], "NOPE")
    with pytest.raises(KeyError):
        sdb.import_sections(m, "aisc", ["W14X90", "BOGUS"], "STL")
    m.validate()


# --------------------------------------------------------------------------- #
# legacy library: byte-identical (pre-v1.21 table, Manual 15th ed.)
# --------------------------------------------------------------------------- #
_OLD = {   # (A, Ix, Iy, J, d, bf) - the pre-v1.21 hard-coded table
    "W8x31": (9.13, 110.0, 37.1, 0.536, 8.00, 8.00),
    "W8x40": (11.7, 146.0, 49.1, 1.12, 8.25, 8.07),
    "W10x33": (9.71, 171.0, 36.6, 0.583, 9.73, 7.96),
    "W10x49": (14.4, 272.0, 93.4, 1.39, 9.98, 10.00),
    "W12x26": (7.65, 204.0, 17.3, 0.300, 12.20, 6.49),
    "W12x40": (11.7, 307.0, 44.1, 0.906, 11.90, 8.01),
    "W12x65": (19.1, 533.0, 174.0, 2.18, 12.10, 12.00),
    "W14x30": (8.85, 291.0, 19.6, 0.380, 13.80, 6.73),
    "W14x48": (14.1, 484.0, 51.4, 1.45, 13.80, 8.03),
    "W14x90": (26.5, 999.0, 362.0, 4.06, 14.00, 14.50),
    "W16x36": (10.6, 448.0, 24.5, 0.545, 15.90, 6.99),
    "W16x57": (16.8, 758.0, 43.1, 2.22, 16.40, 7.12),
    "W18x50": (14.7, 800.0, 40.1, 1.24, 18.00, 7.50),
    "W18x76": (22.3, 1330.0, 152.0, 2.83, 18.20, 11.00),
    "W21x62": (18.3, 1330.0, 57.5, 1.83, 21.00, 8.24),
    "W21x93": (27.3, 2070.0, 92.9, 6.03, 21.60, 8.42),
    "W24x76": (22.4, 2100.0, 82.5, 2.68, 23.90, 8.99),
    "W24x104": (30.7, 3100.0, 259.0, 4.72, 24.10, 12.80),
    "W27x94": (27.6, 3270.0, 124.0, 4.03, 26.90, 10.00),
    "W30x116": (34.2, 4930.0, 164.0, 6.43, 30.00, 10.50),
    "W33x130": (38.3, 6710.0, 218.0, 7.37, 33.10, 11.50),
    "W36x150": (44.3, 9040.0, 270.0, 10.10, 35.90, 12.00),
}


def test_legacy_library_byte_identical():
    from skyframe.core import sections_library as sl
    assert sl.library_names() == list(_OLD)
    for n, (A, Ix, Iy, J, d, bf) in _OLD.items():
        exp = {"name": n, "A": A * IN ** 2, "I33": Ix * IN ** 4,
               "I22": Iy * IN ** 4, "J": J * IN ** 4, "h": d * IN,
               "b": bf * IN}
        assert json.dumps(sl.library_properties(n)) == json.dumps(exp), n
        assert sl._W_SHAPES_IMPERIAL[n] == (A, Ix, Iy, J, d, bf)
        s = FrameSection.from_library(n, "S")
        assert s.to_dict() == FrameSection(n, "S", exp["A"], exp["I33"],
                                           exp["I22"], exp["J"], b=exp["b"],
                                           h=exp["h"]).to_dict()
    # every other value is the AISC v16.0 database value; d is pinned once
    assert sdb.shape("aisc", "W10X49")["aisc"]["d"] == 10.0
    with pytest.raises(KeyError):
        sl.library_properties("W14X90")       # library labels keep "x"


# --------------------------------------------------------------------------- #
# serialisation: only-when-set, exact round trip, shape + dims only
# --------------------------------------------------------------------------- #
def _demo_model():
    m = BuildingModel(name="db")
    m.add_material(Material("STL", 2e8, 0.3, 77.0, material_type="steel"))
    m.add_material(Material("C30", 2.5e7, 0.2, 24.0))
    for n in ("W14X90", "HSS20X12X5/8", "L4X3X1/4"):
        m.add_section(sdb.section_from_db("aisc", n, "STL"))
    m.sections["L4X3X1/4"].use_principal = True
    m.add_section(section_from_dims("CH", "STL", "channel",
                                    {"d": 0.3, "bf": 0.1, "tf": 0.012,
                                     "tw": 0.008}))
    m.add_section(section_from_dims("TB", "C30", "tee_concrete",
                                    {"bf": 1.2, "hf": 0.15, "bw": 0.3,
                                     "h": 0.6}))
    return m


def test_new_fields_only_when_set():
    d = FrameSection.rectangular("R", "C", 0.3, 0.6).to_dict()
    for k in ("dims", "db_ref", "principal", "use_principal"):
        assert k not in d
    m = BuildingModel.from_dict({"name": "x", "materials": {"C": {
        "name": "C", "E": 3e7}}, "sections": {"R": d}})
    assert m.sections["R"].to_dict() == d


def test_db_and_parametric_sections_round_trip():
    m = _demo_model()
    d1 = m.to_dict()
    s = d1["sections"]
    assert s["W14X90"]["db_ref"] == {"standard": "aisc", "name": "W14X90"}
    assert s["L4X3X1/4"]["use_principal"] is True
    assert "principal" in s["L4X3X1/4"] and "principal" not in s["W14X90"]
    assert s["CH"]["shape"] == "channel" and "db_ref" not in s["CH"]
    txt = json.dumps(d1, sort_keys=True)
    m2 = BuildingModel.from_dict(json.loads(txt))
    assert json.dumps(m2.to_dict(), sort_keys=True) == txt
    m2.validate()


def test_shape_and_dims_only_section_is_computed():
    dims = {"d": 0.3, "bf": 0.1, "tf": 0.012, "tw": 0.008}
    m = BuildingModel.from_dict({"name": "x", "materials": {"S": {
        "name": "S", "E": 2e8}}, "sections": {"CH": {
            "name": "CH", "material": "S", "shape": "channel",
            "dims": dims}}})
    s, p = m.sections["CH"], shape_properties("channel", dims)
    assert (s.A, s.I33, s.I22, s.J, s.b, s.h) == (p["A"], p["I33"], p["I22"],
                                                  p["J"], p["b"], p["h"])
    assert s.dims == dims and s.As2 is None    # shear areas stay explicit


def test_extension_validation():
    m = BuildingModel()
    m.add_material(Material("S", 2e8, 0.3, 77.0))
    bad = FrameSection("B", "S", 1.0, 1.0, 1.0, 1.0)
    bad.use_principal = True
    with pytest.raises(ValueError, match="use_principal"):
        m.add_section(bad)
    bad2 = FrameSection("B2", "S", 1.0, 1.0, 1.0, 1.0)
    bad2.shape, bad2.dims = "channel", {"d": 0.1, "bf": 0.1, "tf": 0.06,
                                        "tw": 0.01}
    with pytest.raises(ValueError, match="thicker"):
        m.add_section(bad2)
    bad3 = FrameSection("B3", "S", 1.0, 1.0, 1.0, 1.0)
    bad3.db_ref = {"standard": "aisc"}
    with pytest.raises(ValueError, match="db_ref"):
        m.add_section(bad3)


# --------------------------------------------------------------------------- #
# HTTP API
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api import server
    app = server.create_app()
    app.testing = True
    old = server._state["model"]
    server._state["model"] = BuildingModel(name="api")
    server._state["model"].add_material(Material("STL", 2e8, 0.3, 77.0,
                                                 material_type="steel"))
    yield app.test_client()
    server._state["model"] = old


def test_api_standards_and_shapes(client):
    st = client.get("/api/steel_db/standards").get_json()
    aisc = [s for s in st if s["id"] == "aisc"][0]
    assert aisc["count"] == 2299 and aisc["families"][0] == "W"
    r = client.get("/api/steel_db/shapes?standard=aisc&family=W&q=W14"
                   ).get_json()
    assert r["standard"] == "aisc" and r["count"] == r["total"] > 0
    assert all(s["family"] == "W" and "aisc" not in s for s in r["shapes"])
    r2 = client.get("/api/steel_db/shapes?standard=AISC&limit=5&raw=1"
                    ).get_json()
    assert r2["count"] == 5 and r2["total"] == 2299
    assert "aisc" in r2["shapes"][0]
    assert client.get("/api/steel_db/shapes?standard=zz").status_code == 404
    assert client.get("/api/steel_db/shapes?limit=x").status_code == 400


def test_api_import_dry_run_and_apply(client):
    from skyframe.api import server
    body = {"standard": "aisc", "names": ["W14X90", "HSS20X12X5/8",
                                          "L4X3X1/4"], "material": "STL"}
    dry = client.post("/api/steel_db/import", json={**body, "apply": False}
                      ).get_json()
    assert dry["added"] == body["names"] and "model" not in dry
    assert not server._state["model"].sections
    assert dry["sections"]["W14X90"]["db_ref"]["name"] == "W14X90"
    r = client.post("/api/steel_db/import", json=body).get_json()
    assert r["added"] == body["names"] and r["skipped"] == []
    assert set(r["model"]["sections"]) == set(body["names"])
    assert r["model"]["sections"]["W14X90"] == dry["sections"]["W14X90"]
    again = client.post("/api/steel_db/import", json=body).get_json()
    assert again["skipped"] == body["names"]
    assert client.post("/api/steel_db/import", json={
        **body, "material": "X"}).status_code == 400
    assert client.post("/api/steel_db/import", json={
        **body, "names": ["NOPE"]}).status_code == 404
    assert client.post("/api/steel_db/import", json={
        "material": "STL"}).status_code == 400


def test_api_shape_props(client):
    r = client.post("/api/steel_db/shape_props", json={
        "type": "rect_solid", "dims": {"d": 0.6, "b": 0.3}}).get_json()
    assert r["props"]["I33"] == pytest.approx(0.3 * 0.6 ** 3 / 12)
    assert r["shear_area_formula"] == "As2 = As3 = 5/6*A"
    assert client.post("/api/steel_db/shape_props", json={
        "type": "tee", "dims": {"d": 0.1}}).status_code == 400


def test_api_model_save_reload_round_trip(client):
    d = _demo_model().to_dict()
    r = client.post("/api/model", json=d)
    assert r.status_code == 200
    echoed = client.get("/api/model").get_json()
    assert echoed["sections"] == d["sections"]
    assert client.post("/api/models/dbtest").status_code == 200
    client.post("/api/model", json={"name": "other"})
    assert client.post("/api/models/dbtest/open").status_code == 200
    again = client.get("/api/model").get_json()
    assert json.dumps(again["sections"], sort_keys=True) == \
        json.dumps(d["sections"], sort_keys=True)
    # a second POST of the echo is byte-identical
    client.post("/api/model", json=again)
    assert client.get("/api/model").get_json() == again


# --------------------------------------------------------------------------- #
# engine: the properties are used correctly
# --------------------------------------------------------------------------- #
E_S, NU_S = 2.0e8, 0.3
G_S = E_S / (2 * (1 + NU_S))


def _cantilever(sec, L=3.0, P=10.0, along="z"):
    from skyframe.engine.opensees_engine import OpenSeesEngine
    m = BuildingModel(name="c")
    m.rigid_diaphragms = False
    m.add_material(Material("STL", E_S, NU_S, 77.0, material_type="steel"))
    m.set_stories([3.0])
    sec.material = "STL"
    m.add_section(sec)
    m.add_member("beam", sec.name, (0, 0, 0), (L, 0, 0), uid="B1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.pattern("P").nodal_loads.append(
        NodalLoad((L, 0, 0), fz=-P) if along == "z" else
        NodalLoad((L, 0, 0), fy=-P))
    m.add_case("P", {"P": 1.0})
    eng = OpenSeesEngine(m)
    res = eng.run_static("P")
    return eng, res, res.node_disp[eng._find_node(eng._asm, (L, 0, 0))]


def test_engine_uses_db_shear_area():
    """Imported W14X90 cantilever: Timoshenko with As2 = d tw."""
    L, P = 3.0, 10.0
    sec = sdb.section_from_db("aisc", "W14X90", "STL")
    _, _, u = _cantilever(sec, L, P)
    exp = P * L ** 3 / (3 * E_S * sec.I33) + P * L / (G_S * sec.As2)
    assert u[2] == pytest.approx(-exp, rel=1e-9)


def _angle(use_principal):
    sec = section_from_dims("ANG", "STL", "angle",
                            {"d": 0.2, "b": 0.15, "t": 0.02})
    sec.As2 = sec.As3 = None                 # pure bending reference
    sec.use_principal = use_principal
    return sec


@pytest.mark.parametrize("along", ["z", "y"])
def test_angle_leg_axes_ignore_product_of_inertia(along):
    """Default (ETABS geometric axes): I33 / I22 about the legs, I23
    ignored -> no deflection perpendicular to the load."""
    L, P = 3.0, 10.0
    sec = _angle(False)
    eng, res, u = _cantilever(sec, L, P, along)
    if along == "z":
        assert u[2] == pytest.approx(-P * L ** 3 / (3 * E_S * sec.I33),
                                     rel=1e-9)
        assert abs(u[1]) < 1e-12
    else:
        assert u[1] == pytest.approx(-P * L ** 3 / (3 * E_S * sec.I22),
                                     rel=1e-9)
        assert abs(u[2]) < 1e-12
    assert eng.model is eng.source_model           # no principal copy


@pytest.mark.parametrize("along", ["z", "y"])
def test_angle_principal_axes_exact_unsymmetric_bending(along):
    """use_principal: the engine rotates the member by -alpha and uses
    I_major / I_minor; the tip deflection equals the full unsymmetric
    bending solution u = L^3/(3E) K^-1 F, K = [[I33, I23], [I23, I22]]
    (local 2 = +Z, local 3 = -Y for a beam along +X)."""
    L, P = 3.0, 10.0
    sec = _angle(True)
    p = shape_properties("angle", sec.dims)
    I33, I22, I23 = p["I33"], p["I22"], p["I23"]
    det = I33 * I22 - I23 * I23
    Kinv = [[I22 / det, -I23 / det], [-I23 / det, I33 / det]]
    F = (-P, 0.0) if along == "z" else (0.0, P)      # (F2, F3); fy=-P -> F3=+P
    u2 = L ** 3 / (3 * E_S) * (Kinv[0][0] * F[0] + Kinv[0][1] * F[1])
    u3 = L ** 3 / (3 * E_S) * (Kinv[1][0] * F[0] + Kinv[1][1] * F[1])
    eng, res, u = _cantilever(sec, L, P, along)
    assert u[2] == pytest.approx(u2, rel=1e-8)
    assert u[1] == pytest.approx(-u3, rel=1e-8)
    assert abs(u[1]) > 0.05 * abs(u[2]) or along == "y"   # real coupling
    # the analysed copy carries principal I, the member is stamped
    asec = eng.model.sections["ANG"]
    assert (asec.I33, asec.I22) == (p["I_major"], p["I_minor"])
    assert eng.source_model.sections["ANG"].I33 == I33   # model untouched
    assert eng.model.members[0].section_axis_offset == pytest.approx(
        -math.degrees(p["alpha"]))
    assert "section_axis_offset" not in json.dumps(
        eng.source_model.to_dict())


def test_database_ships_as_package_data():
    """pyproject package-data covers every data/steel/*.json (installer)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    txt = (root / "pyproject.toml").read_text()
    assert re.search(r'"skyframe" = \[[^\]]*"data/(\*|steel)/\*\.json"', txt)
    assert sdb.DATA_DIR == root / "skyframe" / "data" / "steel"
    assert (sdb.DATA_DIR / "aisc.json").is_file()


def test_principal_flag_survives_reload_and_stamps_members():
    m = _demo_model()
    m.add_member("beam", "L4X3X1/4", (0, 0, 0), (3, 0, 0), uid="A1")
    m2 = BuildingModel.from_dict(json.loads(json.dumps(m.to_dict())))
    a = m2.sections["L4X3X1/4"].principal["alpha"]
    assert m2.members[0].section_axis_offset == pytest.approx(
        -math.degrees(a))
    from skyframe.core.frame_axes import member_local_axes
    x, y, z, _, _ = member_local_axes(m2.members[0])
    assert y[2] == pytest.approx(math.cos(a))        # local 2 tilted
