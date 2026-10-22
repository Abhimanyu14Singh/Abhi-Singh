"""Model exchange round trips (CONTRACT "Model exchange: E2K / IFC / DXF /
Excel").

* E2K: export -> import reproduces ``to_dict()`` exactly with the SkyFrame
  ``$SF`` extension comments (kN-m), within 1e-12 in kip-in / N-mm, and
  the NATIVE ETABS records alone give the same analysis results (1e-9) for
  a representative model; a hand-written ETABS v20 file (cantilever
  W360X60) reproduces the closed-form tip deflection; database sections
  resolve from FROMFILE; towers round-trip natively.
* IFC: geometry (exact end points / corners / openings / angles) and
  sections (database shape by name, parametric dims, general properties)
  round-trip; legacy reader still reads the file.
* DXF: layer SF-<kind>-<section> -> section mapping, 3DFACE / polyline
  shells with openings, millimetre drawings.
* Excel: byte-identical .xlsx round trip of the model tables; edits apply.
* Nothing changes when unused (pinned model / results hashes).
"""

import base64
import hashlib
import json
import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from exchange_compare import max_rel_diff
from exchange_models import representative_model

from skyframe.core.builder import quick_building
from skyframe.core.model import BuildingModel
from skyframe.io.dxf_layers import export_dxf, import_dxf_layers
from skyframe.io.e2k import import_e2k
from skyframe.io.e2k_read import read_e2k
from skyframe.io.e2k_write import export_e2k
from skyframe.io.exchange_common import (apply_patches, diff_patches,
                                         equal, model_dict)
from skyframe.io.ifc import import_ifc
from skyframe.io.ifc_read import read_ifc
from skyframe.io.ifc_write import export_ifc, step_real
from skyframe.io.xlsx import export_xlsx, import_xlsx, read_workbook


def _sha(d) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()


@pytest.fixture(scope="module")
def rep():
    return representative_model()


@pytest.fixture(scope="module")
def rep_results(rep):
    return OpenSeesEngine(rep).run().to_dict()


# --------------------------------------------------------------- common
def test_patch_diff_apply_identity():
    a = {"x": 1.0, "l": [{"u": 1}, {"u": 2}], "k": {"p": 1, "q": 2}}
    b = {"k": {"q": 3, "p": 1}, "x": 2.0, "l": [{"u": 1}, {"u": 5}],
         "n": None}
    ops = diff_patches(a, b)
    out = apply_patches(a, ops)
    assert equal(out, b) and list(out) == list(b)
    assert diff_patches(b, b) == []


def test_step_real_has_decimal_point():
    assert step_real(3) == "3.0" and step_real(1e-5) == "1.E-05"
    assert float(step_real(0.1)) == 0.1


# ------------------------------------------------------------------ E2K
def test_e2k_extensions_exact_round_trip(rep):
    text, report = export_e2k(rep)
    assert report["exact"] is True and report["skipped"] == []
    m2, warnings = read_e2k(text)
    assert warnings == []
    assert _sha(m2.to_dict()) == _sha(rep.to_dict())
    # only SkyFrame-only fields travel as $SF comments
    assert set(report["extensions"]) <= {"materials", "builtup_sections"}
    assert all(line.startswith("$") or not line.strip() or
               line.startswith("  ") for line in text.splitlines())


def test_e2k_native_records_same_results(rep, rep_results):
    text, report = export_e2k(rep, extensions=False)
    assert "$SF " not in text
    m2, warnings = read_e2k(text)
    assert warnings == []
    worst, where, missing = max_rel_diff(rep_results,
                                         OpenSeesEngine(m2).run().to_dict())
    assert missing == [] and worst <= 1e-9, (worst, where)


@pytest.mark.parametrize("units", [("KIP", "IN"), ("N", "MM"),
                                   ("KGF", "M")])
def test_e2k_other_units(rep, rep_results, units):
    text, report = export_e2k(rep, units=units)
    assert report["exact"] is True
    assert f'UNITS  "{units[0]}"  "{units[1]}"' in text
    m2, _w = read_e2k(text)
    assert equal(model_dict(m2), rep.to_dict(), 1e-12)
    native, _r = export_e2k(rep, units=units, extensions=False)
    m3, _w = read_e2k(native)
    r3 = OpenSeesEngine(m3).run().to_dict()
    # unit conversion perturbs inputs at the last bit: static / combos /
    # RS stay within 1e-9; eigen-vector-based results (modal shapes, the
    # Rayleigh-damped TH) inherit the iterative eigen solver tolerance
    for key in rep_results:
        tol = 1e-6 if key in ("modal", "th_cases") else 1e-9
        worst, where, _m = max_rel_diff({key: rep_results[key]},
                                        {key: r3[key]})
        assert worst <= tol, (key, worst, where)


def test_e2k_legacy_convention_model_exact():
    m = quick_building(stories=2, bays_x=2, bays_y=1)   # "legacy" axes
    text, report = export_e2k(m)
    assert report["exact"] is True
    m2, _w = read_e2k(text)
    assert _sha(m2.to_dict()) == _sha(m.to_dict())
    # ETABS records carry ETABS axes: legacy column angle 0 -> -90
    assert 'ANGLE -90' in text


def test_e2k_dialect_records(rep):
    text, _r = export_e2k(rep, extensions=False)
    for rec in ('PROGRAM  "ETABS"  VERSION "20.0.0"',
                'UNITS  "KN"  "M"  "C"',
                'STORY "Story2"  HEIGHT 3.5',
                'STORY "Base"  ELEV 0',
                'FRAMESECTION  "W14X90"  MATERIAL "A992Fy50"  SHAPE '
                '"Steel I/Wide Flange"',
                'FROMFILE "AISC16.xml"  DBSECTNAME "W14X90"',
                'SHAPE "Steel Channel"', 'SHAPE "General"',
                'SDSECTION "PG1"', 'SHELLPROP  "WALL25"  PROPTYPE  "Wall"',
                'MODELINGTYPE "ShellThick"', 'MODELINGTYPE "Membrane"',
                'AREA "W1~O1"', 'OPENING "Yes"', 'RELEASE "M2I M3I M2J M3J"',
                'ANGLE 30', 'CARDINALPT 8', 'LENOFFI 0.25', 'RIGIDZONE 0.5',
                'LINE  "D1"  BRACE', 'LINKPROP  "LP_K1"  DOF "U1"',
                'POINTSPRING', 'DIAPHRAGM "D1"    TYPE RIGID',
                'LOADPATTERN "DEAD"  TYPE  "Dead"  SELFWEIGHT  1',
                'TYPE "TRAPF"', 'TYPE "POINTF"', 'TYPE "UNIFF"  DIR "X"',
                'AREALOAD  "F1"', 'SEISMIC "EQX"  STORY "Story1"',
                'FUNCTION "SPEC"  FUNCTYPE "SPECTRUM"',
                'TYPE  "Response Spectrum"', 'MODALCOMBO  "CQC"',
                'TYPE  "Linear Direct Integration History"',
                'COMBO "ENV"  TYPE "Envelope"', 'LOADCOMBO "1.2D+1.6L"',
                'MASSSOURCELOAD  "MsSrc1"  "DEAD"  1',
                'GROUP "CORE"  LINE "C000"', 'ACTIVEDOF'):
        assert rec in text, rec


def test_e2k_link_local_axes(rep):
    # link along +Y: local 1 = Y, 2 = Z, 3 = X -> U1=ky U2=kz U3=kx
    text, _r = export_e2k(rep, extensions=False)
    assert 'DOF "U1"  FIXED "No"  KE 20000' in text
    assert 'DOF "U2"  FIXED "No"  KE 500000' in text
    assert 'DOF "U3"  FIXED "No"  KE 10000' in text
    m2, _w = read_e2k(text)
    assert m2.links[0].stiffness == rep.links[0].stiffness


ETABS_CANTILEVER = """$ hand-written ETABS v20 file
$ PROGRAM INFORMATION
  PROGRAM  "ETABS"  VERSION "22.6.0"
$ CONTROLS
  UNITS  "KN"  "M"  "C"
$ STORIES - IN SEQUENCE FROM TOP
  STORY "Level_1"  HEIGHT 1  MASTERSTORY "Yes"
  STORY "Base"  ELEV 0
$ MATERIAL PROPERTIES
  MATERIAL  "Mat_2"    TYPE "Steel"    WEIGHTPERVOLUME 7.85
  MATERIAL  "Mat_2"    SYMTYPE "Isotropic"  E 200000000  U 0.3  A 1E-05
  MATERIAL  "Mat_2"    FY 345000  FU 450000  EFFFY 379500  EFFFU 495000
$ FRAME SECTIONS
  FRAMESECTION  "W360X60"  MATERIAL "Mat_2"  SHAPE "Steel I/Wide Flange"  D 0.352  B 0.203  TF 0.013  TW 0.008
$ POINT COORDINATES
  POINT "1"  0 0
  POINT "2"  4 0
$ LINE CONNECTIVITIES
  LINE  "B1"  BEAM  "1"  "2"  0
$ POINT ASSIGNS
  POINTASSIGN  "1"  "Level_1"  RESTRAINT "UX UY UZ RX RY RZ"
  POINTASSIGN  "2"  "Level_1"
$ LINE ASSIGNS
  LINEASSIGN  "B1"  "Level_1"  SECTION "W360X60"  MINNUMSTA 3 AUTOMESH "YES"
$ LOAD PATTERNS
  LOADPATTERN "Live"  TYPE  "Live"  SELFWEIGHT  0
$ POINT OBJECT LOADS
  POINTLOAD  "2"  "Level_1"  LC "Live"  TYPE "FORCE"  FX 0 FY 0 FZ -10 MX 0 MY 0 MZ 0
$ ANALYSIS OPTIONS
  ACTIVEDOF "UX UY UZ RX RY RZ"
  PDELTA  METHOD "NONE"
$ LOAD CASES
  LOADCASE "Live"  TYPE  "Linear Static"  INITCOND  "PRESET"
  LOADCASE "Live"  LOADPAT  "Live"  SF  1
  END
$ END OF MODEL FILE
"""


def test_etabs_file_cantilever_closed_form():
    m, warnings = read_e2k(ETABS_CANTILEVER)
    assert [s.name for s in m.stories] == ["Level_1"]
    mat = m.materials["Mat_2"]
    assert mat.material_type == "steel" and mat.fy == 345000.0
    assert mat.Ry == 1.1
    sec = m.sections["W360X60"]
    assert sec.shape == "I" and sec.dims == {"d": 0.352, "bf": 0.203,
                                             "tf": 0.013, "tw": 0.008}
    assert len(m.supports) == 1 and m.supports[0].point == (0.0, 0.0, 1.0)
    m.rigid_diaphragms = False
    m.num_modes = 0
    res = OpenSeesEngine(m).run().to_dict()
    disp = res["cases"]["Live"]["node_disp"]
    uz = min(v[2] for v in disp.values())
    P, L, E = 10.0, 4.0, 2e8
    G = E / (2 * 1.3)
    delta = P * L ** 3 / (3 * E * sec.I33) + P * L / (G * sec.As2)
    assert uz == pytest.approx(-delta, rel=1e-9)


def test_e2k_database_section_from_file():
    text = ETABS_CANTILEVER.replace(
        'SHAPE "Steel I/Wide Flange"  D 0.352  B 0.203  TF 0.013  TW 0.008',
        'SHAPE "Steel I/Wide Flange"  D 0.3  B 0.15  TF 0.0107  TW 0.0071'
        '  FROMFILE "Euro.xml"  DBSECTNAME "IPE300"')
    m, w = read_e2k(text)
    sec = m.sections["W360X60"]
    assert sec.db_ref == {"standard": "eurocode", "name": "IPE300"}
    from skyframe.core.steel_db import section_from_db
    ref = section_from_db("eurocode", "IPE300", "Mat_2", "W360X60")
    assert (sec.A, sec.I33, sec.I22, sec.J) == (ref.A, ref.I33, ref.I22,
                                                ref.J)


def test_e2k_unknown_records_warn_never_raise():
    m, w = read_e2k(ETABS_CANTILEVER + "\n  FOO \"x\" 1\n  LINEASSIGN "
                    "\"ZZ\" \"Level_1\" SECTION \"W360X60\"\n")
    assert any("FOO" in x for x in w)
    assert any("unknown line 'ZZ'" in x for x in w)


def test_e2k_towers_native():
    from test_towers import _twin
    m = _twin()
    text, report = export_e2k(m, extensions=False)
    assert 'TOWER  "T2"' in text and 'STORY "B3"  HEIGHT' in text
    m2, _w = read_e2k(text)
    assert [t["name"] for t in m2.towers] == ["T1", "T2"]
    assert [(s.name, s.elevation, s.tower) for s in m2.stories] == \
        [(s.name, s.elevation, s.tower) for s in m.stories]
    assert [(x.uid, x.pi, x.pj, x.story) for x in m2.members] == \
        [(x.uid, x.pi, x.pj, x.story) for x in m.members]
    full, rep2 = export_e2k(m)
    assert rep2["exact"]


def test_legacy_import_e2k_unchanged():
    m, w = import_e2k(ETABS_CANTILEVER)
    assert [x.uid for x in m.members] == ["B1-Level_1"]


# ------------------------------------------------------------------ IFC
def test_ifc_geometry_and_sections_round_trip(rep):
    text, report = export_ifc(rep)
    assert text.startswith("ISO-10303-21;") and "FILE_SCHEMA(('IFC4'));" \
        in text
    for ent in ("IFCISHAPEPROFILEDEF", "IFCUSHAPEPROFILEDEF",
                "IFCRECTANGLEPROFILEDEF", "IFCSTRUCTURALANALYSISMODEL",
                "IFCSTRUCTURALCURVEMEMBER", "IFCSTRUCTURALSURFACEMEMBER",
                "IFCWALL", "IFCSLAB", "IFCCOLUMN", "IFCBEAM", "IFCMEMBER",
                "IFCARBITRARYPROFILEDEFWITHVOIDS", "IFCBUILDINGSTOREY",
                "IFCSTRUCTURALPOINTCONNECTION"):
        assert ent + "(" in text, ent
    m2, warnings = read_ifc(text)
    assert warnings == []
    assert [(s.name, s.elevation) for s in m2.stories] == \
        [(s.name, s.elevation) for s in rep.stories]
    by = {x.uid: x for x in m2.members}
    for x in rep.members:
        y = by[x.uid]
        assert (y.kind, y.section, y.pi, y.pj, y.story) == \
            (x.kind, x.section, x.pi, x.pj, x.story)
        assert y.angle == pytest.approx(x.angle, abs=1e-9)
    w = m2.sections["W14X90"]
    assert w.db_ref == {"standard": "aisc", "name": "W14X90"}
    assert w.to_dict() == rep.sections["W14X90"].to_dict()
    ch = m2.sections["CH1"]
    assert ch.shape == "channel" and ch.dims == rep.sections["CH1"].dims
    for name in ("COL", "BEAM", "GEN1", "PG1"):
        a, b = rep.sections[name], m2.sections[name]
        assert (a.A, a.I33, a.I22, a.J) == (b.A, b.I33, b.I22, b.J), name
    for r in rep.shells:
        s = next(x for x in m2.shells if x.uid == r.uid)
        assert s.corners == r.corners and s.kind == r.kind
        assert [o.to_dict() for o in s.openings] == \
            [o.to_dict() for o in r.openings]
        assert m2.shell_sections[s.section].thickness == \
            rep.shell_sections[r.section].thickness
    assert m2.base_fixity == "fixed" and m2.supports == []
    assert m2.materials["A992Fy50"].E == pytest.approx(
        rep.materials["A992Fy50"].E, rel=1e-12)
    # the legacy geometry-only reader still parses the file
    legacy, lw = import_ifc(text)
    assert legacy.stories


def test_ifc_physical_only_fallback(rep):
    text, _r = export_ifc(rep, include={"analysis": False})
    assert "IFCSTRUCTURALCURVEMEMBER(" not in text
    m2, _w = read_ifc(text)
    by = {x.uid: x for x in m2.members}
    for x in rep.members:
        y = by[x.uid]
        for k in range(3):
            assert y.pi[k] == pytest.approx(x.pi[k], abs=1e-9)
            assert y.pj[k] == pytest.approx(x.pj[k], abs=1e-9)
    w1 = next(s for s in m2.shells if s.uid == "W1")
    assert w1.openings[0].to_dict() == {"u0": 0.4, "v0": 0.0, "u1": 0.6,
                                        "v1": 0.5}


# ------------------------------------------------------------------ DXF
def test_dxf_layers_round_trip(rep):
    text, report = export_dxf(rep)
    assert "SF-COL-W14X90" in report["layers"]
    assert "\n3DFACE\n" in text and "\nPOLYLINE\n" in text
    m2, w = import_dxf_layers(text, sections_from=rep)
    assert w == []
    assert [(x.kind, x.section, x.pi, x.pj, x.story) for x in m2.members] \
        == [(x.kind, x.section, x.pi, x.pj, x.story) for x in rep.members]
    for a, b in zip(rep.shells, m2.shells):
        assert (a.kind, a.behavior, a.section, a.corners) == \
            (b.kind, b.behavior, b.section, b.corners)
        assert [o.to_dict() for o in a.openings] == \
            [o.to_dict() for o in b.openings]
    assert [(s.name, s.elevation) for s in m2.stories] == \
        [(s.name, s.elevation) for s in rep.stories]
    assert len(m2.links) == 1


def test_dxf_sanitised_layer_maps_back_and_mm():
    m = quick_building(stories=1, bays_x=1, bays_y=1)
    m.sections["BEAM 300/600"] = m.sections.pop("BEAM")
    m.sections["BEAM 300/600"].name = "BEAM 300/600"
    for x in m.members:
        if x.section == "BEAM":
            x.section = "BEAM 300/600"
    text, report = export_dxf(m, unit_scale=1000.0)
    assert "SF-BEAM-BEAM_300_600" in report["layers"]
    m2, _w = import_dxf_layers(text, sections_from=m, unit_scale=1000.0)
    assert {x.section for x in m2.members} == {"COL", "BEAM 300/600"}
    assert max(abs(a.pj[k] - b.pj[k]) for a, b in zip(m.members,
                                                      m2.members)
               for k in range(3)) < 1e-12
    # without a source model: placeholders + warnings
    m3, w3 = import_dxf_layers(text, unit_scale=1000.0)
    assert any("placeholder" in x for x in w3)


# ---------------------------------------------------------------- Excel
def test_xlsx_round_trip_identical(rep):
    data, report = export_xlsx(rep)
    book = read_workbook(data)
    assert "frame_objects" in book and "_meta" in book
    model, errors, _s = import_xlsx(rep, data)
    assert errors == []
    assert _sha(model.to_dict()) == _sha(rep.to_dict())


def test_xlsx_edit_applies():
    m = quick_building(stories=1, bays_x=1, bays_y=1)
    data, _r = export_xlsx(m, tables=["frame_sections"])
    book = read_workbook(data)
    rows = book["frame_sections"]
    head = [h.split(" [")[0] for h in rows[0]]
    i_name, i_b = head.index("name"), head.index("b")
    for r in rows[1:]:
        if r[i_name] == "BEAM":
            r[i_b] = "0.4"
    from skyframe.io.xlsx import write_workbook
    edited = write_workbook([(k, v) for k, v in book.items()])
    model, errors, summary = import_xlsx(m, edited)
    assert errors == []
    from skyframe.core.model import FrameSection
    ref = FrameSection.rectangular("BEAM", "CONC", 0.4, 0.6)
    assert model.sections["BEAM"].I33 == ref.I33


# ------------------------------------------------------- byte identity
def test_unused_feature_byte_identical():
    m = quick_building()
    assert _sha(m.to_dict())[:16] == "95a41577bb25e727"
    assert _sha(OpenSeesEngine(m).run().to_dict())[:16] == \
        "506a5d7ddadd0c39"  # v1.28.3: story rotational inertia from where the mass is


# ------------------------------------------------------------------ API
def test_api_export_import_all_formats(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api.server import create_app
    c = create_app().test_client()
    m = representative_model(with_dynamic=False)
    assert c.post("/api/model", json=m.to_dict()).status_code == 200
    for fmt in ("e2k", "ifc", "dxf", "xlsx"):
        r = c.post(f"/api/exchange/export/{fmt}",
                   json={"units": ["KN", "M"]}).get_json()
        assert r["ok"] and r["filename"].endswith("." + fmt), fmt
        body = {"text": r["content"]} if "content" in r else \
            {"content_b64": r["content_b64"]}
        body["dry_run"] = True
        out = c.post(f"/api/exchange/import/{fmt}", json=body).get_json()
        assert out["ok"], (fmt, out)
        assert out["report"]["read"]["frames"] == len(m.members)
    bad = c.post("/api/exchange/import/e2k", json={"text": "",
                                                   "soft_errors": True})
    assert bad.status_code == 200 and bad.get_json()["ok"] is False
    assert c.post("/api/exchange/export/xyz", json={}).status_code == 400
    empty = c.post("/api/exchange/import/ifc", json={"text": "not ifc"})
    assert empty.status_code == 400 and "no frames" in empty.get_json()["error"]
    r = c.post("/api/exchange/export/e2k", json={}).get_json()
    out = c.post("/api/exchange/import/e2k",
                 json={"text": r["content"]}).get_json()
    cur = c.get("/api/model").get_json()
    assert _sha(cur) == _sha(out["model"])
    assert base64  # noqa: B018 (imported for content_b64 bodies)
    assert math
