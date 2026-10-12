import re
"""v1.21 built-in material libraries (skyframe.core.material_db).

Pinned by the code formulas themselves (re-derived here, not read back):

* EN 1992-1-1 Table 3.1  Ecm = 22 (fcm/10)^0.3 GPa, fcm = fck + 8 (formula
  value used; the rounded table value kept as E_table) -- C30/37 = 32.84 GPa
  vs 33 GPa tabulated;
* ACI 318-19 19.2.2.1(b) Ec = 57,000 sqrt(f'c) psi / 4700 sqrt(f'c) MPa;
* IS 456:2000 6.2.3.1    Ec = 5000 sqrt(fck);
* AS 3600-2018 Table 3.1.2 (used) + the 3.1.2 formula (kept as E_formula);
* BS 8110-2 eq. 17       Ec = 20 + 0.2 fcu GPa;
* steel E / G per AISC 360, EN 1993-1-1 3.2.6, IS 800 2.2.4.1, AS 4100 2.2.4.

Plus the record -> Material mapping, model round trip, an analysis with an
imported C30/37 against the hand cantilever PL^3/3EI, the API, the mock data
copy (js/matlib_data.js) and byte identity when the library is unused.
"""

import hashlib
import json
import math
from pathlib import Path

import pytest

from skyframe.core import material_db as mdb
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 NodalLoad, NodalMass, PointSupport,
                                 material_from_dict)

G = 9.80665
MPA = 1e6
KSI = 6894757.293168361
PSI = KSI / 1000.0
WEB_JS = (Path(mdb.__file__).resolve().parent.parent / "api" / "web" / "js"
          / "matlib_data.js")


def rec(std, name):
    return mdb.material(std, name)


def all_records():
    for s in mdb.STANDARD_IDS:
        for r in mdb.materials(s):
            yield s, r


# --------------------------------------------------------------------------- #
# 1. data files: shape, units, citations, internal consistency
# --------------------------------------------------------------------------- #
def test_standards_listing():
    st = mdb.standards()
    assert [s["id"] for s in st] == ["astm_aci", "eurocode", "is", "bs", "as_nzs",
                                     "gb", "csa", "jis", "ks", "gost"]   # Wave C
    for s in st:
        assert s["count"] == sum(s["kinds"].values()) > 10
        assert set(s["kinds"]) <= set(mdb.KINDS)
    # Wave C regional libraries: steel, concrete and rebar
    for s in st[5:]:
        assert {"steel", "concrete", "rebar"} <= set(s["kinds"]), s["id"]
    # every v1.21 standard has steel, concrete, rebar, tendon and cold-formed
    for s in st[:5]:
        assert {"steel", "concrete", "rebar", "tendon", "cold_formed"} <= set(s["kinds"]), s["id"]
    assert "aluminum" in st[0]["kinds"] and "aluminum" in st[1]["kinds"]


def test_every_record_is_complete_and_consistent():
    keys = {"name", "kind", "group", "E", "nu", "G", "unit_weight",
            "mass_density", "alpha", "fy", "fu", "fc", "source", "formula",
            "E_basis"}
    for s in mdb.STANDARD_IDS:
        names = [r["name"] for r in mdb.materials(s)]
        assert len(names) == len(set(names)), s
    for s, r in all_records():
        assert keys <= set(r), (s, r["name"])
        assert r["kind"] in mdb.KINDS
        assert r["source"].strip() and r["formula"].strip()
        assert 0 <= r["nu"] < 0.5
        # G stored = E / 2(1 + nu) = what Material.G derives
        assert r["G"] == pytest.approx(r["E"] / (2 * (1 + r["nu"])), rel=1e-15)
        # weight and mass are the same density (W = rho g)
        assert r["unit_weight"] == pytest.approx(r["mass_density"] * G, rel=1e-12)
        assert 1e-6 < r["alpha"] < 3e-5
        if r["kind"] == "concrete":
            assert r["fc"] and r["fy"] is None and r["fu"] is None
            assert 15e9 < r["E"] < 50e9
            assert 2300 < r["mass_density"] < 2600
        else:
            assert r["fc"] is None and r["fy"] and r["fu"] and r["fu"] >= r["fy"]
        if r["kind"] in ("steel", "rebar", "tendon", "cold_formed"):
            assert 190e9 <= r["E"] <= 211e9
            assert 7840 <= r["mass_density"] <= 7851


# --------------------------------------------------------------------------- #
# 2. formula pins per standard
# --------------------------------------------------------------------------- #
EC2 = [(12, 15, 27), (16, 20, 29), (20, 25, 30), (25, 30, 31), (30, 37, 33),
       (35, 45, 34), (40, 50, 35), (45, 55, 36), (50, 60, 37), (55, 67, 38),
       (60, 75, 39), (70, 85, 41), (80, 95, 42), (90, 105, 44)]


def test_eurocode_ecm_formula_and_table():
    r = rec("eurocode", "C30/37")
    # Ecm = 22 (38/10)^0.3 = 32.8366 GPa, vs 33 GPa rounded in Table 3.1
    assert r["E"] == pytest.approx(32.8366e9, rel=2e-6)
    assert r["E_table"] == 33e9 and r["E_basis"] == "formula"
    assert r["fcm"] == 38 * MPA and r["fc"] == 30 * MPA
    for fck, cube, etab in EC2:
        r = rec("eurocode", f"C{fck}/{cube}")
        ecm = 22.0 * ((fck + 8) / 10.0) ** 0.3 * 1e9
        assert r["E"] == ecm == r["E_formula"]
        assert r["E_table"] == etab * 1e9
        assert round(r["E"] / 1e9) == etab          # table = rounded formula
        assert r["unit_weight"] == 25000.0 and r["nu"] == 0.2 and r["alpha"] == 1e-5


def test_aci_ec_formulas():
    r = rec("astm_aci", "4000psi")
    assert r["E"] == 57000 * math.sqrt(4000) * PSI
    # the built-in ETABS 4000Psi (24 855 600 kPa) is the same formula
    assert r["E"] / 1000 == pytest.approx(24_855_600.0, rel=1e-6)
    for f in (3000, 5000, 6000, 7000, 8000, 9000, 10000):
        assert rec("astm_aci", f"{f}psi")["E"] == 57000 * math.sqrt(f) * PSI
    r = rec("astm_aci", "28MPa")
    assert r["E"] == 4700 * math.sqrt(28) * MPA            # 24 870.06 MPa
    assert r["E"] / MPA == pytest.approx(24870.06, abs=0.01)
    for f in (20, 25, 30, 35, 40, 45, 50, 55, 60, 70):
        assert rec("astm_aci", f"{f}MPa")["E"] == 4700 * math.sqrt(f) * MPA
    # wc = 150 pcf = 23.563 kN/m^3 (stated value)
    assert r["unit_weight"] == pytest.approx(23563.0, abs=1.0)


def test_is456_formula():
    assert rec("is", "M25")["E"] == 25000 * MPA            # 5000 sqrt(25)
    for fck in (15, 20, 30, 35, 40, 45, 50, 55, 60):
        assert rec("is", f"M{fck}")["E"] == 5000 * math.sqrt(fck) * MPA


def test_as3600_table_and_formula():
    r = rec("as_nzs", "N32")
    assert r["E"] == 30100 * MPA and r["E_basis"] == "table"
    # formula (fcmi = 35 <= 40): 2400^1.5 x 0.043 sqrt(35) = 29 910 MPa
    assert r["E_formula"] == 2400 ** 1.5 * 0.043 * math.sqrt(35) * MPA
    assert r["E_formula"] / MPA == pytest.approx(29910, abs=1)
    r = rec("as_nzs", "N50")                                # fcmi = 53 > 40
    assert r["E_formula"] == 2400 ** 1.5 * (0.024 * math.sqrt(53) + 0.12) * MPA
    for name in ("N20", "N25", "N32", "N40", "N50", "N65", "N80", "N100"):
        r = rec("as_nzs", name)
        assert abs(r["E_formula"] / r["E_table"] - 1) < 0.012   # formula ~ table


def test_bs8110_formula():
    r = rec("bs", "C30-BS8110")
    assert r["E"] == 26e9 and r["fcu"] == 30 * MPA and r["fc"] == 25 * MPA
    for fcu in (25, 35, 40, 45, 50, 60):
        assert rec("bs", f"C{fcu}-BS8110")["E"] == (20 + 0.2 * fcu) * 1e9


def test_steel_moduli_per_code():
    assert rec("eurocode", "S355")["E"] == 210e9
    assert rec("eurocode", "S355")["G"] / MPA == pytest.approx(80769.23, abs=0.01)
    assert (rec("eurocode", "S355")["fy"], rec("eurocode", "S355")["fu"]) == (355e6, 510e6)
    a992 = rec("astm_aci", "A992")
    assert a992["E"] == 29000 * KSI and a992["fy"] == 50 * KSI and a992["fu"] == 65 * KSI
    assert a992["G_code"] == 11200 * KSI and a992["Ry"] == 1.1
    assert rec("astm_aci", "A36")["Ry"] == 1.5
    e250 = rec("is", "E250")
    assert e250["E"] == 2e11 and e250["G"] / MPA == pytest.approx(0.769e5, rel=1e-3)
    c350 = rec("as_nzs", "C350L0")
    assert c350["E"] == 2e11 and c350["G"] == 80e9        # AS 4100: nu = 0.25
    assert rec("bs", "S275-BS5950")["E"] == 205e9
    assert rec("bs", "S355-t16")["fu"] == 470e6          # UK NA: product std Rm
    assert rec("eurocode", "B500C")["fu"] == pytest.approx(575e6)
    assert rec("as_nzs", "D500L")["fu"] == pytest.approx(515e6)
    assert rec("is", "Fe500")["fu"] == 545e6
    t = rec("astm_aci", "A416Gr270")
    assert t["fu"] == 270 * KSI and t["fy"] == pytest.approx(243 * KSI)
    assert rec("eurocode", "Y1860S7")["E"] == 195e9


# --------------------------------------------------------------------------- #
# 3. record -> Material
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("std,name", [("eurocode", "S355"), ("eurocode", "C30/37"),
                                      ("is", "M25"), ("astm_aci", "A992"),
                                      ("as_nzs", "N32"), ("astm_aci", "A615Gr60"),
                                      ("bs", "Y1860S7"), ("eurocode", "EN-AW-6061-T6"),
                                      ("astm_aci", "A653SS-Gr50-1")])
def test_import_gives_exact_material(std, name):
    r = rec(std, name)
    m = mdb.material_from_db(std, name)
    assert isinstance(m, Material) and m.name == name
    assert m.E == r["E"] / 1000 and m.nu == r["nu"]
    assert m.unit_weight == r["unit_weight"] / 1000
    assert m.mass_density == r["mass_density"] / 1000
    assert m.alpha == r["alpha"]
    assert m.G == pytest.approx(r["G"] / 1000, rel=1e-15)
    assert m.mass_per_volume == m.mass_density
    for k in ("fc", "fy", "fu"):
        assert getattr(m, k) == (None if r[k] is None else r[k] / 1000)
    assert m.material_type == mdb.KIND_TO_TYPE[r["kind"]]
    assert m.symmetry == ("uniaxial" if r["kind"] in ("rebar", "tendon") else "isotropic")
    assert m.Ry == r.get("Ry", 1.1) and m.stress_strain is None
    assert name in m.notes and r["source"] in m.notes
    BuildingModel._validate_material(m)


def test_every_record_imports_and_validates():
    for s, r in all_records():
        BuildingModel._validate_material(mdb.material_from_db(s, r["name"]))


def test_round_trip_through_model_json():
    mdl = BuildingModel(name="rt")
    res = mdb.import_into(mdl, "eurocode", ["S355", "C30/37"])
    res += mdb.import_into(mdl, "is", ["M25"])
    res += mdb.import_into(mdl, "astm_aci", ["A992"])
    res += mdb.import_into(mdl, "as_nzs", ["N32"])
    assert [x["as"] for x in res] == ["S355", "C30/37", "M25", "A992", "N32"]
    d1 = mdl.to_dict()
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d1)))
    assert json.dumps(m2.to_dict(), sort_keys=True) == json.dumps(d1, sort_keys=True)
    for k, m in mdl.materials.items():
        assert material_from_dict(m.to_dict()) == m


def test_listing_material_equals_import():
    for r in mdb.listing("eurocode", "concrete"):
        assert r["material"] == mdb.material_from_db("eurocode", r["name"]).to_dict()
    assert all(r["kind"] == "rebar" for r in mdb.listing("is", "rebar"))
    assert [r["name"] for r in mdb.materials("eurocode", query="s355")] == \
        ["S355", "S355H-EN10210", "S355H-EN10219"]
    with pytest.raises(ValueError):
        mdb.materials("eurocode", kind="wood")
    with pytest.raises(KeyError):
        mdb.materials("nope")
    with pytest.raises(KeyError):
        mdb.material("is", "M99")


def test_name_clash_modes():
    mdl = BuildingModel(name="clash")
    mdb.import_into(mdl, "eurocode", ["S355"])
    r = mdb.import_into(mdl, "eurocode", ["S355", "S355"])
    assert [x["as"] for x in r] == ["S355-2", "S355-3"]
    mdl.materials["S355"].E = 1.0
    r = mdb.import_into(mdl, "eurocode", ["S355"], on_clash="replace")
    assert r == [{"name": "S355", "as": "S355", "replaced": True}]
    assert mdl.materials["S355"].E == 210e6
    with pytest.raises(ValueError, match="already exists"):
        mdb.import_into(mdl, "eurocode", ["S275", "S355"], on_clash="error")
    assert "S275" not in mdl.materials                       # atomic
    r = mdb.import_into(mdl, "eurocode", ["S355"], rename={"S355": "MySteel"})
    assert r[0]["as"] == "MySteel" and mdl.materials["MySteel"].fy == 355e3
    with pytest.raises(ValueError):
        mdb.import_into(mdl, "eurocode", ["S355"], on_clash="merge")


# --------------------------------------------------------------------------- #
# 4. analysis with an imported C30/37 -- hand cantilever with Ecm
# --------------------------------------------------------------------------- #
def test_cantilever_with_imported_c30_37_matches_hand():
    ose = pytest.importorskip("skyframe.engine.opensees_engine")
    L, P, b = 3.0, 10.0, 0.4
    I = b ** 4 / 12.0
    Ecm = 22.0 * (38 / 10.0) ** 0.3 * 1e6            # kPa (EN 1992-1-1 Table 3.1)
    mdl = BuildingModel(name="c3037")
    mdl.rigid_diaphragms = False
    mdb.import_into(mdl, "eurocode", ["C30/37"])
    mdl.set_stories([L])
    mdl.add_section(FrameSection.rectangular("COL", "C30/37", b, b))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("P", "other").nodal_loads.append(NodalLoad((0, 0, L), fx=P))
    mdl.add_case("P", {"P": 1.0})
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=1.0, my=1.0))
    mdl.num_modes = 1
    d = ose.OpenSeesEngine(mdl).run().to_dict()
    tip = next(t for t, xyz in d["nodes"].items()
               if all(abs(a - c) < 1e-9 for a, c in zip(xyz, (0, 0, L))))
    ux = d["cases"]["P"]["node_disp"][tip][0]
    hand = P * L ** 3 / (3.0 * Ecm * I)               # 1.2849e-4 m
    assert ux == pytest.approx(hand, rel=1e-6)
    # ... and NOT the rounded table value (33 GPa): 0.5 % stiffer
    assert abs(ux / (P * L ** 3 / (3.0 * 33e6 * I)) - 1) > 4e-3


# --------------------------------------------------------------------------- #
# 5. byte identity when the library is unused
# --------------------------------------------------------------------------- #
def _hash_model_and_results():
    ose = pytest.importorskip("skyframe.engine.opensees_engine")
    from skyframe.core.builder import quick_building
    m = quick_building(bays_x=1, bays_y=1, stories=2)
    md = json.dumps(m.to_dict(), sort_keys=True)
    rd = json.dumps(ose.OpenSeesEngine(m).run().to_dict(), sort_keys=True,
                    default=str)
    return hashlib.sha256(md.encode()).hexdigest(), hashlib.sha256(rd.encode()).hexdigest()


def test_material_dict_shape_unchanged():
    # no new Material fields: the serialized key set is exactly the v1.13 one
    assert list(Material("X", 1.0).to_dict()) == [
        "name", "E", "nu", "unit_weight", "material_type", "symmetry",
        "mass_density", "alpha", "fc", "fy", "fu", "Ry", "damping",
        "lightweight", "lam", "color", "notes", "stress_strain", "G",
        "mass_per_volume", "Fye", "Fue"]


def test_library_use_leaves_other_models_byte_identical():
    before = _hash_model_and_results()
    mdb._load.cache_clear()
    mdb.listing("eurocode")
    mdb.import_into(BuildingModel(name="other"), "is", ["M25"])
    assert _hash_model_and_results() == before


# --------------------------------------------------------------------------- #
# 6. API
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    from skyframe.api.server import create_app
    app = create_app()
    app.testing = True
    return app.test_client()


def test_api_standards_and_materials(client):
    st = client.get("/api/material_db/standards").get_json()
    assert [s["id"] for s in st] == list(mdb.STANDARD_IDS)
    r = client.get("/api/material_db/materials?standard=eurocode&kind=concrete").get_json()
    assert r["standard"] == "eurocode" and len(r["materials"]) == 14
    c = next(x for x in r["materials"] if x["name"] == "C30/37")
    assert c["material"]["E"] == c["E"] / 1000 and c["material"]["material_type"] == "concrete"
    r = client.get("/api/material_db/materials?standard=is&q=fe50").get_json()
    assert [x["name"] for x in r["materials"]] == ["Fe500", "Fe500D"]
    assert client.get("/api/material_db/materials?standard=xx").status_code == 404
    assert client.get("/api/material_db/materials?standard=is&kind=wood").status_code == 400
    one = client.get("/api/material_db/material?standard=as_nzs&name=N32").get_json()
    assert one["record"]["E"] == 30100e6 and one["material"]["E"] == 30100e3
    assert client.get("/api/material_db/material?standard=as_nzs&name=N33").status_code == 404


def test_api_import(client):
    r = client.post("/api/material_db/import",
                    json={"standard": "eurocode", "names": ["S355", "C30/37"]})
    assert r.status_code == 200
    body = r.get_json()
    assert [x["as"] for x in body["imported"]] == ["S355", "C30/37"]
    assert body["model"]["materials"]["C30/37"]["E"] == rec("eurocode", "C30/37")["E"] / 1000
    r = client.post("/api/material_db/import", json={"standard": "eurocode", "names": ["S355"]})
    assert r.get_json()["imported"][0]["as"] == "S355-2"
    r = client.post("/api/material_db/import",
                    json={"standard": "eurocode", "names": ["S355"], "on_clash": "error"})
    assert r.status_code == 409
    assert client.post("/api/material_db/import", json={"standard": "eurocode", "names": ["Nope"]}).status_code == 404
    assert client.post("/api/material_db/import", json={"standard": "eurocode", "names": []}).status_code == 400
    assert client.post("/api/material_db/import", data="x").status_code == 400
    m = client.get("/api/model").get_json()
    assert "S355-2" in m["materials"]


# --------------------------------------------------------------------------- #
# 7. packaging + the ?mock=1 data copy
# --------------------------------------------------------------------------- #
def test_package_data_declared():
    root = Path(mdb.__file__).resolve().parents[2]
    assert re.search(r'"skyframe" = \[[^\]]*"data/(\*|materials)/\*\.json"', (root / "pyproject.toml").read_text())
    for s in mdb.STANDARD_IDS:
        assert (mdb.DATA_DIR / f"{s}.json").is_file()


def test_mock_data_copy_matches_json():
    """js/matlib_data.js (the ?mock=1 copy) must equal the JSON files."""
    txt = WEB_JS.read_text(encoding="utf-8")
    start = txt.index("{")
    end = txt.rindex("}") + 1
    data = json.loads(txt[start:end])
    assert list(data) == list(mdb.STANDARD_IDS)
    for s in mdb.STANDARD_IDS:
        assert data[s] == mdb._load(s), s
