"""Wave C: native ETABS e2k records for pushover / nonlinear static /
staged / steady-state / PSD cases, hinge properties + assignments,
nonlinear link properties and temperature loads (CONTRACT "E2K native
nonlinear records (Wave C)").

* The NATIVE records alone (no ``$SF`` lines) rebuild the model exactly in
  kN-m and within 1e-12 in kip-in / N-mm / kgf-m, and give identical
  analysis results (1e-9) for a model with every feature.
* Hand-written ETABS-style files using the record spellings confirmed
  from real ETABS v16-v22 exports import to the closed-form values:
  restrained-beam temperature force N = E A alpha dT and gradient moment
  M = E I alpha g, exponential damper C unit conversion
  C_SI = C_file * F / L^alpha, hinge scale factors, the monitored
  displacement of a displacement-controlled pushover (= DISPLMAG).
* Models without the features export byte-identically to before.
"""

import warnings

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from e2k_nonlinear_models import nonlinear_model
from exchange_compare import max_rel_diff
from exchange_models import representative_model

from skyframe.io.e2k_read import read_e2k
from skyframe.io.e2k_write import export_e2k
from skyframe.io.exchange_common import diff_patches, equal, model_dict

FEATURE_KEYS = ("pushover_cases", "staged_cases", "nonlinear_static_cases",
                "steady_state_cases", "psd_cases", "frequency_functions",
                "hinge_properties", "links", "patterns", "members")


@pytest.fixture(scope="module")
def nlm():
    return nonlinear_model()


@pytest.fixture(scope="module")
def nlm_results(nlm):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return OpenSeesEngine(nlm).run().to_dict()


# ------------------------------------------------------------ round trip
def test_native_records_rebuild_model_exactly(nlm):
    text, report = export_e2k(nlm, extensions=False)
    assert "$SF " not in text
    assert report["native_exact"] is True and report["skipped"] == []
    m2, warns = read_e2k(text)
    assert warns == []
    assert equal(model_dict(m2), nlm.to_dict())
    # with extensions on, nothing of the feature set travels as $SF
    text2, rep2 = export_e2k(nlm)
    assert rep2["exact"] is True and rep2["extensions"] == {}
    assert "$SF " not in text2


@pytest.mark.parametrize("units", [("KIP", "IN"), ("N", "MM"),
                                   ("KGF", "M"), ("LB", "FT")])
def test_native_records_other_units(nlm, units):
    text, report = export_e2k(nlm, units=units, extensions=False)
    m2, warns = read_e2k(text)
    assert warns == []
    ops = diff_patches(model_dict(m2), nlm.to_dict(), 1e-12)
    assert ops == [], ops[:3]


def test_native_round_trip_same_results(nlm, nlm_results):
    st = nlm_results["case_status"]
    assert all(v == "finished" for v in st.values()), st
    for key in ("pushover", "staged", "nonlinear_static", "steady_state",
                "psd"):
        assert nlm_results[key], key
    text, _r = export_e2k(nlm, extensions=False)
    m2, _w = read_e2k(text)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r2 = OpenSeesEngine(m2).run().to_dict()
    worst, where, missing = max_rel_diff(nlm_results, r2)
    assert missing == [] and worst <= 1e-9, (worst, where)


def test_dialect_records(nlm):
    text, _r = export_e2k(nlm, extensions=False)
    for rec in (
            'LOADCASE "NX"  TYPE  "Nonlinear Static"  INITCOND  "NG"',
            'LOADCASE "NX"  LOADCONTROL  "Displacement"  DISPLTYPE  '
            '"Monitored"  MONITOREDDISPL  "Joint"  DISPLMAG  0.02  DOF  '
            '"U1"  JOINT  "1"  "Story2"',
            'LOADCASE "NX"  RESULTSSAVED  "Multiple"  MINSAVED  10  '
            'MAXSAVED  10',
            'LOADCASE "PX~GRAV"  LOADPAT  "LIVE"  SF  0.25',
            'LOADCASE "PX"  TYPE  "Nonlinear Static"  INITCOND  "PX~GRAV"',
            'LOADCASE "PY"  NLGEOMTYPE  "PDelta"',
            'LOADCASE "SC2"  STAGE  "S1"  OPERATION  "Add Structure"  '
            'OBJTYPE  "Group"  OBJNAME  "G1"  AGE  7',
            'TYPE  "Nonlinear Staged Construction"',
            'LOADCASE "SS1"  TYPE  "Steady State"',
            'TYPE  "Power Spectral Density"',
            'FUNCTION "SSF"  FUNCTYPE "STEADYSTATE"',
            'HINGE "H1"  BEHAVIOR "Deformation Controlled"  DOF "M3"  TYPE '
            '"Moment-Rotation"  HYSTYPE "Takeda"',
            'HINGE "H1"  "-C"  -0.015  -110',
            'HINGE "H1"  IO 0.005  LS 0.01  CP 0.015',
            'HINGEASSIGN "BX10"  "Story2"  HINGEPROP "H1"  RDISTANCE 0',
            'TABLEITEM "Concrete Beams"  DOF "M3"  RDISTANCE 1',
            'HINGEOVERWRITE "BX05"  "Story1"  AUTOSUBDIVIDE "Yes"  '
            'AUTOSUBDIVIDERELLENGTH 0.05',
            'LINKPROP  "LP_DMP"  TYPE "DAMPER - EXPONENTIAL"',
            'LINKPROP  "LP_DMP"  DOF "U1"  NONLIN "Yes"  K 200000  C 50  '
            'EXP 0.5',
            'LINKPROP  "LP_ISO"  TYPE "RUBBER ISOLATOR"',
            'LINKPROP  "LP_TK"  DOF "U2"  DISPL -0.03  FORCE -70',
            'LINELOAD  "BX10"  "Story2"  TYPE "TEMPERATURE"  LC "TEMP"  T 25',
            'TYPE "TEMPERATUREGRADIENT"  DIR "3"  LC "TEMP"  TGRAD -6',
            'AREALOAD  "F1"  "Story1"  TYPE "TEMPERATURE"  LC "TEMP"  T 12',
            'TYPE "TEMPERATURE"  T 8'):
        assert rec in text, rec
    for kind in ("pushover", "staged", "nonlinear static", "steady",
                 "PSD", "device", "temperature"):
        assert f"{kind}" not in " ".join(_r["skipped"])


def test_legacy_model_export_unchanged():
    """A model without the features writes none of the new records."""
    rep = representative_model()
    text, report = export_e2k(rep)
    for kw in ("HINGE", "TEMPERATURE", "Nonlinear Staged", "Steady State",
               "LOADDIST", 'NONLIN "Yes"'):
        assert kw not in text
    assert report["exact"] is True


# ------------------------------------------------- hand-written ETABS files
_HEAD = """$ hand-written ETABS-style file
$ PROGRAM INFORMATION
  PROGRAM  "ETABS"  VERSION "22.6.0"
$ CONTROLS
  UNITS  "N"  "MM"  "C"
$ STORIES - IN SEQUENCE FROM TOP
  STORY "Story1"  HEIGHT 3000  MASTERSTORY "Yes"
  STORY "Base"  ELEV 0
$ MATERIAL PROPERTIES
  MATERIAL  "S"    TYPE "Steel"    WEIGHTPERVOLUME 0
  MATERIAL  "S"    SYMTYPE "Isotropic"  E 200000  U 0.3  A 1.2E-05
$ FRAME SECTIONS
  FRAMESECTION  "G"  MATERIAL "S"  SHAPE "General"  D 400  B 200  AREA 10000  AS2 0  AS3 0  I33 200000000  I22 50000000  I23 0  J 100000000
"""

THERMAL = _HEAD + """$ POINT COORDINATES
  POINT "1"  0 0
  POINT "2"  4000 0
$ LINE CONNECTIVITIES
  LINE  "B1"  BEAM  "1"  "2"  0
$ POINT ASSIGNS
  POINTASSIGN  "1"  "Story1"  RESTRAINT "UX UY UZ RX RY RZ"
  POINTASSIGN  "2"  "Story1"  RESTRAINT "UX UY UZ RX RY RZ"
$ LINE ASSIGNS
  LINEASSIGN  "B1"  "Story1"  SECTION "G"
$ LOAD PATTERNS
  LOADPATTERN "TEMP"  TYPE  "Temperature"  SELFWEIGHT  0
$ FRAME OBJECT LOADS
  LINELOAD  "B1"  "Story1"  TYPE "TEMPERATURE"  LC "TEMP"  T 20
  LINELOAD  "B1"  "Story1"  TYPE "TEMPERATUREGRADIENT"  DIR "2"  LC "TEMP"  TGRAD 0.05
$ LOAD CASES
  LOADCASE "TEMP"  TYPE  "Linear Static"  INITCOND  "PRESET"
  LOADCASE "TEMP"  LOADPAT  "TEMP"  SF  1
  END
$ END OF MODEL FILE
"""


def test_etabs_temperature_closed_form():
    """Fixed-fixed beam, dT = 20 C and grad2 = 0.05 C/mm = 50 C/m:
    N = E A alpha dT = 2e8 * 0.01 * 1.2e-5 * 20 = 480 kN (compression),
    M3 = E I33 alpha g = 2e8 * 2e-4 * 1.2e-5 * 50 = 24 kN m."""
    m, warns = read_e2k(THERMAL)
    assert warns == []
    tl = m.patterns["TEMP"].thermal_loads
    assert len(tl) == 1 and tl[0].dT == 20.0
    assert tl[0].grad2 == pytest.approx(50.0, rel=1e-12)
    m.rigid_diaphragms = False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = OpenSeesEngine(m).run_static("TEMP")
    f = res.member_forces["B1"]
    assert abs(f[0]) == pytest.approx(2e8 * 0.01 * 1.2e-5 * 20, rel=1e-9)
    assert abs(f[5]) == pytest.approx(2e8 * 2e-4 * 1.2e-5 * 50, rel=1e-9)


PORTAL = _HEAD + """$ LINK PROPERTIES
  LINKPROP  "VDX"  TYPE "DAMPER - EXPONENTIAL"
  LINKPROP  "VDX"  DOF "U1"  NONLIN "Yes"  K 100000 C 480.3518 EXP 0.6
$ FRAME HINGE PROPERTIES
  HINGE "B1H9"  BEHAVIOR "Deformation Controlled"  DOF "M3"  TYPE "Moment-Rotation"  HYSTYPE "Degrading"  DEGRADINGSYM "Yes"
  HINGE "B1H9"  "-E"  -0.05 -0.2
  HINGE "B1H9"  "-D"  -0.025 -1.1
  HINGE "B1H9"  "-C"  -0.025 -1.1
  HINGE "B1H9"  "-B"  0 -1
  HINGE "B1H9"  "B"  0 1
  HINGE "B1H9"  "C"  0.025 1.1
  HINGE "B1H9"  "D"  0.025 1.1
  HINGE "B1H9"  "E"  0.05 0.2
  HINGE "B1H9"  MOMENTSFP 6.677216E+07 ROTATIONSFP 1 MOMENTSFN 6.677216E+07 ROTATIONSFN 1
  HINGE "B1H9"  IO 0.01 LS 0.025 CP 0.05 -IO -0.01 -LS -0.025 -CP -0.05
$ POINT COORDINATES
  POINT "1"  0 0
  POINT "2"  6000 0
  POINT "3"  9000 0
$ LINE CONNECTIVITIES
  LINE  "C1"  COLUMN  "1"  "1"  1
  LINE  "C2"  COLUMN  "2"  "2"  1
  LINE  "B1"  BEAM  "1"  "2"  0
$ LINK CONNECTIVITIES
  LINK  "K1"  POINT1 "2"  "Story1"  POINT2 "3"  "Story1"
$ POINT ASSIGNS
  POINTASSIGN  "1"  "Base"  RESTRAINT "UX UY UZ RX RY RZ"
  POINTASSIGN  "2"  "Base"  RESTRAINT "UX UY UZ RX RY RZ"
$ LINE ASSIGNS
  LINEASSIGN  "C1"  "Story1"  SECTION "G"
  LINEASSIGN  "C2"  "Story1"  SECTION "G"
  LINEASSIGN  "B1"  "Story1"  SECTION "G"
$ LINK ASSIGNS
  LINKASSIGN  "K1"  PROPERTY "VDX"
$ FRAME HINGE ASSIGNMENTS
  HINGEASSIGN "B1"  "Story1"  HINGEPROP "B1H9"  RDISTANCE 0
  HINGEASSIGN "B1"  "Story1"  HINGEPROP "B1H9"  RDISTANCE 1
  HINGEASSIGN "C1"  "Story1"  AUTOHINGETYPE "ASCE41-13"  TABLEITEM "Concrete Columns"  DOF "PM2M3"  FAILCONDITION "ii"  CASECOMBO "DL"  RDISTANCE 0
$ FRAME HINGE OVERWRITES
  HINGEOVERWRITE "B1"  "Story1"  AUTOSUBDIVIDERELLENGTH 0.02
$ LOAD PATTERNS
  LOADPATTERN "DL"  TYPE  "Dead"  SELFWEIGHT  0
  LOADPATTERN "LL"  TYPE  "Live"  SELFWEIGHT  0
  LOADPATTERN "EQX"  TYPE  "Seismic"  SELFWEIGHT  0
$ POINT OBJECT LOADS
  POINTLOAD  "1"  "Story1"  LC "DL"  TYPE "FORCE"  FX 0 FY 0 FZ -50000 MX 0 MY 0 MZ 0
  POINTLOAD  "2"  "Story1"  LC "LL"  TYPE "FORCE"  FX 0 FY 0 FZ -20000 MX 0 MY 0 MZ 0
  POINTLOAD  "1"  "Story1"  LC "EQX"  TYPE "FORCE"  FX 10000 FY 0 FZ 0 MX 0 MY 0 MZ 0
$ LOAD CASES
  LOADCASE "Modal"  TYPE  "Modal - Eigen"  INITCOND  "PRESET"
  LOADCASE "Modal"  MAXMODES  2 MINMODES  1 EIGENSHIFTFREQ  0 EIGENCUTOFF  0 EIGENTOL  1E-09
  LOADCASE "Gravity"  TYPE  "Nonlinear Static"  INITCOND  "NONE"  MODALCASE  "Modal"  MASSSOURCE  "Previous"
  LOADCASE "Gravity"  LOADPAT  "DL"  SF  1
  LOADCASE "Gravity"  LOADPAT  "LL"  SF  0.5
  LOADCASE "Gravity"  LOADCONTROL  "Full"  DISPLTYPE  "Monitored"  MONITOREDDISPL  "Joint"  DISPLMAG  0 DOF  "U1"  JOINT  "1"  "Story1"
  LOADCASE "Gravity"  RESULTSSAVED  "Final"
  LOADCASE "Gravity"  SOLUTIONSCHEME  "Iterative Events"  MAXTOTALSTEPS  200 MAXNULLSTEPS  50 MAXEVENTSPERSTEP  24
  LOADCASE "Push X"  TYPE  "Nonlinear Static"  INITCOND  "Gravity"  MODALCASE  "Modal"  MASSSOURCE  "Previous"
  LOADCASE "Push X"  LOADPAT  "EQX"  SF  1
  LOADCASE "Push X"  NLGEOMTYPE  "PDelta"
  LOADCASE "Push X"  LOADCONTROL  "Displacement"  DISPLTYPE  "Monitored"  MONITOREDDISPL  "Joint"  DISPLMAG  30 DOF  "U1"  JOINT  "1"  "Story1"
  LOADCASE "Push X"  RESULTSSAVED  "Multiple"  MINSAVED  12 MAXSAVED  200
  LOADCASE "Push M"  TYPE  "Nonlinear Static"  INITCOND  "Gravity"  MODALCASE  "Modal"  MASSSOURCE  "Previous"
  LOADCASE "Push M"  MODE  1 SF  1
  LOADCASE "Push M"  NLGEOMTYPE  "LargeDispl"
  LOADCASE "Push M"  LOADCONTROL  "Displacement"  DISPLTYPE  "Monitored"  MONITOREDDISPL  "Joint"  DISPLMAG  25 DOF  "U1"  JOINT  "1"  "Story1"
  LOADCASE "Push M"  RESULTSSAVED  "Multiple"  MINSAVED  10 MAXSAVED  300
  END
$ END OF MODEL FILE
"""


def test_etabs_nonlinear_records_parse():
    m, warns = read_e2k(PORTAL)
    # ASCE 41-13 tables are mapped to the ASCE 41-17 generator (reported)
    assert any("ASCE41-13" in x for x in warns)
    # exponential damper: K 1e5 N/mm = 1e5 kN/m, C in N (s/mm)^0.6
    lk = m.links[0]
    assert lk.link_type == "damper"
    assert lk.params["k"] == pytest.approx(1e5, rel=1e-12)
    assert lk.params["alpha"] == 0.6
    assert lk.params["cd"] == pytest.approx(
        480.3518 * 1e-3 / 1e-3 ** 0.6, rel=1e-12)
    # user hinge: symmetric backbone, M_y = 6.677216e7 N mm = 66.77 kN m
    h = m.hinge_properties["B1H9"]
    # ETABS "Degrading" hysteresis -> SkyFrame takeda (degrading stiffness)
    assert h["type"] == "M3" and h["hysteresis"] == "takeda"
    assert h["backbone"] == [[0.0, 0.0], [0.0, 1.0], [0.025, 1.1],
                             [0.025, 1.1], [0.05, 0.2]]
    assert h["scale"]["yield_value"] == pytest.approx(66.77216, rel=1e-12)
    assert h["scale"]["yield_deformation"] == 1.0
    assert h["acceptance"] == {"IO": 0.01, "LS": 0.025, "CP": 0.05}
    mem = {mm.uid: mm for mm in m.members}
    assert mem["B1"].hinges == [
        {"property": "B1H9", "relative_distance": 0.0},
        {"property": "B1H9", "relative_distance": 1.0}]
    assert mem["B1"].hinge_overwrites == {"relative_length": 0.02}
    auto = mem["C1"].hinges[0]["property"]
    assert m.hinge_properties[auto]["auto"]["component"] == "rc_column"
    assert m.hinge_properties[auto]["auto"]["dof"] == "PMM"
    # nonlinear static chain: Gravity (load control) -> Push X
    g = m.nonlinear_static_cases["Gravity"]
    assert g.load_application == "load_control"
    assert g.loads == [{"pattern": "DL", "scale": 1.0},
                       {"pattern": "LL", "scale": 0.5}]
    px = m.nonlinear_static_cases["Push X"]
    assert px.start_from == "Gravity"
    assert px.load_application == "displacement_control"
    assert px.target_disp == pytest.approx(0.030, rel=1e-12)
    assert px.control_point == [0.0, 0.0, 3.0] and px.control_dof == "UX"
    assert px.steps == 12 and px.geometric == "p_delta"
    # modal pushover -> PushoverCase with the gravity of its initial case
    pm = m.pushover_cases["Push M"]
    assert pm.load_distribution == "mode" and pm.mode_number == 1
    assert pm.gravity == {"DL": 1.0, "LL": 0.5}
    assert pm.target_disp == pytest.approx(0.025, rel=1e-12)
    assert pm.geometric == "corotational" and pm.steps == 10
    assert pm.direction == "X"


def test_etabs_displacement_pushover_reaches_displmag():
    """Displacement control drives the monitored joint to DISPLMAG
    (30 mm) in UX at the end of the case, measured from the Gravity
    state (the case chain is solved in one domain)."""
    m, _w = read_e2k(PORTAL)
    for mm in m.members:
        mm.hinges = "none"
    m.hinge_properties = {}
    m.links = []
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.pushover_cases = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = OpenSeesEngine(m).run().to_dict()
    assert res["case_status"]["Push X"] == "finished"
    nls = res["nonlinear_static"]["Push X"]
    assert nls["nonlinear"]["history"]["disp"][-1] == pytest.approx(
        0.030, rel=1e-9)


def test_unknown_link_and_hinge_records_warn_never_raise():
    text = PORTAL.replace('DOF "M3"  TYPE "Moment-Rotation"',
                          'DOF "XYZ"  TYPE "Moment-Rotation"')
    text = text.replace('TABLEITEM "Concrete Columns"',
                        'TABLEITEM "Masonry Walls"')
    m, warns = read_e2k(text)
    assert any("B1H9" in x for x in warns)
    assert any("Masonry Walls" in x for x in warns)


# ------------------------------------------------------------------ API
def test_api_e2k_native_round_trip(tmp_path, monkeypatch, nlm):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api.server import create_app
    c = create_app().test_client()
    assert c.post("/api/model", json=nlm.to_dict()).status_code == 200
    r = c.post("/api/exchange/export/e2k",
               json={"units": ["KIP", "IN"], "extensions": False}).get_json()
    assert r["ok"] and "$SF " not in r["content"]
    assert r["report"]["written"]["hinge_properties"] == 3
    out = c.post("/api/exchange/import/e2k",
                 json={"text": r["content"], "dry_run": True}).get_json()
    assert out["ok"] and out["warnings"] == []
    # (the JSON transport sorts dict keys: compare content, not order)
    ops = diff_patches(model_dict(out["model"]), nlm.to_dict(), 1e-12)
    assert [op for op in ops if op["op"] != "order"] == []


# ------------------------------------------------------------------ DWG
def test_dwg_signature_detected():
    from skyframe.io.dwg import DWG_HELP, is_dwg
    for sig in ("AC1015", "AC1018", "AC1024", "AC1027", "AC1032"):
        assert is_dwg(sig + "\x00\x00\x00") and is_dwg(sig.encode() + b"\0")
    assert not is_dwg("  0\nSECTION\n  2\nHEADER\n")
    assert not is_dwg("999\nSF\n") and not is_dwg("") and not is_dwg(None)
    assert "Save the drawing as DXF" in DWG_HELP


def test_dxf_import_rejects_dwg_with_help(tmp_path, monkeypatch):
    from skyframe.io.dxf_layers import import_dxf_layers
    with pytest.raises(ValueError, match="binary DWG drawing"):
        import_dxf_layers("AC1032\x00\x00\x00\x00\x00\x00\x01")
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api.server import create_app
    c = create_app().test_client()
    r = c.post("/api/exchange/import/dxf", json={"text": "AC1027\x00junk"})
    assert r.status_code == 400
    assert "Save the drawing as DXF" in r.get_json()["error"]


# ------------------------------------------------- Tekla / Revit coverage
def test_tekla_and_revit_routes_cover_steel_frame():
    """A native Tekla link / Revit add-in cannot be built here (Windows
    APIs); the file routes Tekla Structures and Revit read and write cover
    the analysis model: SDNF (both ways) and IFC4 (both ways, also the
    Revit-friendly flavour) carry every member end point and section."""
    from test_interop_steel import _steel_frame
    from skyframe.io.ifc_read import read_ifc
    from skyframe.io.ifc_write import export_ifc
    from skyframe.io.sdnf import export_sdnf, read_sdnf
    m = _steel_frame()
    ref = sorted((x.uid, x.pi, x.pj, x.section) for x in m.members)
    sd, _r = export_sdnf(m)
    m2, _w = read_sdnf(sd)
    assert sorted((x.uid, x.pi, x.pj, x.section) for x in m2.members) == ref
    for revit in (False, True):
        txt, _r = export_ifc(m, revit=revit)
        m3, _w = read_ifc(txt)
        got = {x.uid: x for x in m3.members}
        for uid, pi, pj, sec in ref:
            y = got[uid]
            assert y.section == sec
            assert max(abs(a - b) for a, b in zip(tuple(y.pi) + tuple(y.pj),
                                                   pi + pj)) < 1e-9
