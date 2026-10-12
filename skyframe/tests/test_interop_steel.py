"""Steel interop: SDNF v3 reader + writer, CIS/2 (LPM6) reader, IDEA
StatiCa IOM XML export -- CONTRACT "SAP2000 / SAFE / steel interop"."""

import io
import xml.etree.ElementTree as ET
import zipfile

import pytest

from skyframe.core import frame_axes as _fa
from skyframe.core.builder import quick_building
from skyframe.core.model import BuildingModel, Material
from skyframe.core.steel_db import section_from_db
from skyframe.engine.opensees_engine import OpenSeesEngine
from skyframe.io.cis2 import read_cis2
from skyframe.io.idea_iom import export_idea, export_idea_zip
from skyframe.io.sdnf import export_sdnf, read_sdnf


def _steel_frame():
    m = _fa.as_new_model(BuildingModel(name="Steel bay"))
    m.add_material(Material("A992", 2.0e8, 0.3, 77.0, material_type="steel",
                            fy=345e3, fu=450e3))
    m.add_section(section_from_db("aisc", "W14X90", "A992", "W14X90"))
    m.add_section(section_from_db("aisc", "W18X35", "A992", "W18X35"))
    m.set_stories([4.0, 3.5])
    for z0, z1, st in ((0.0, 4.0, "Story1"), (4.0, 7.5, "Story2")):
        for x in (0.0, 8.0):
            m.add_member("column", "W14X90", (x, 0, z0), (x, 0, z1),
                         story=st, uid=f"C{int(x)}-{st}")
        m.add_member("beam", "W18X35", (0, 0, z1), (8, 0, z1), story=st,
                     uid=f"B-{st}", releases="Mi,Mj" if st == "Story2"
                     else "")
    m.add_member("brace", "W18X35", (0, 0, 0), (8, 0, 4.0), story="Story1",
                 uid="X1", angle=90.0)
    m.members[-1].cardinal_point = 8
    m.validate()
    return m


# --------------------------------------------------------------- SDNF
def test_sdnf_roundtrip():
    m = _steel_frame()
    text, rep = export_sdnf(m)
    assert text.startswith("Packet 00") and "Packet 10" in text
    assert rep["written"]["members"] == 7
    m2, warns = read_sdnf(text)
    assert warns == []
    by = {x.uid: x for x in m2.members}
    for x in m.members:
        y = by[x.uid]
        assert (y.pi, y.pj, y.kind, y.section, y.angle, y.releases,
                y.cardinal_point) == (x.pi, x.pj, x.kind, x.section,
                                      x.angle, x.releases,
                                      x.cardinal_point), x.uid
    # database sections come back as the AISC records
    assert m2.sections["W14X90"].db_ref == {"standard": "aisc",
                                            "name": "W14X90"}
    assert m2.sections["W14X90"].I33 == m.sections["W14X90"].I33
    assert [s.elevation for s in m2.stories] == [4.0, 7.5]
    assert export_sdnf(m2)[0] == text


def test_sdnf_units_and_foreign_packets():
    m = _steel_frame()
    text, _ = export_sdnf(m, units="mm")
    assert '"Millimeters"' in text and "8000 0 4000" in text
    text = "# comment\n" + text + "Packet 20\n0\n"
    m2, warns = read_sdnf(text)
    assert any("Packet 20" in w for w in warns)
    assert {x.pj for x in m2.members} == {x.pj for x in m.members}


def test_sdnf_unknown_section_placeholder_and_current_model():
    m = _fa.as_new_model(quick_building(stories=1, bays_x=1, bays_y=1))
    text, _ = export_sdnf(m)
    m2, warns = read_sdnf(text)
    assert any("placeholder" in w for w in warns)
    m3, warns3 = read_sdnf(text, sections_from=m)
    assert warns3 == []
    assert m3.sections["COL"].A == m.sections["COL"].A


# --------------------------------------------------------------- CIS/2
_CIS2 = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('CIS/2 LPM6 analysis model'),'2;1');
FILE_NAME('frame.stp','2026-01-01T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('STRUCTURAL_FRAME_SCHEMA'));
ENDSEC;
DATA;
#1=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.));
#10=CARTESIAN_POINT('',(0.,0.,0.));
#11=CARTESIAN_POINT('',(0.,0.,4000.));
#12=CARTESIAN_POINT('',(6000.,0.,4000.));
#13=CARTESIAN_POINT('',(6000.,0.,0.));
#20=BOUNDARY_CONDITION_LOGICAL('fixed',$,.T.,.T.,.T.,.T.,.T.,.T.);
#21=NODE(1,'N1',$,#10,#20,#5);
#22=NODE(2,'N2',$,#11,$,#5);
#23=NODE(3,'N3',$,#12,$,#5);
#24=NODE(4,'N4',$,#13,#20,#5);
#30=MATERIAL(1,'S355',$);
#31=SECTION_PROFILE_I_TYPE(1,'W14X90',$,$,10,.F.,$,$,$,$);
#32=SECTION_PROFILE_I_TYPE(2,'GIRDER-600',$,$,10,.F.,#40,#41,#42,#43);
#40=POSITIVE_LENGTH_MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(600.),#1);
#41=POSITIVE_LENGTH_MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(250.),#1);
#42=POSITIVE_LENGTH_MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(10.),#1);
#43=POSITIVE_LENGTH_MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(20.),#1);
#50=ELEMENT_CURVE_SIMPLE(1,'C1',$,#5,1,#31,#30);
#51=ELEMENT_CURVE_SIMPLE(2,'B1',$,#5,1,#32,#30);
#52=ELEMENT_CURVE_SIMPLE(3,'C2',$,#5,1,#31,#30);
#60=ELEMENT_NODE_CONNECTIVITY(1,$,$,1,#21,#50);
#61=ELEMENT_NODE_CONNECTIVITY(2,$,$,2,#22,#50);
#62=ELEMENT_NODE_CONNECTIVITY(3,$,$,1,#22,#51);
#63=ELEMENT_NODE_CONNECTIVITY(4,$,$,2,#23,#51);
#64=ELEMENT_NODE_CONNECTIVITY(5,$,$,2,#23,#52);
#65=ELEMENT_NODE_CONNECTIVITY(6,$,$,1,#24,#52);
ENDSEC;
END-ISO-10303-21;
"""

_CIS2_DESIGN = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('CIS/2 LPM6 design model'),'2;1');
FILE_NAME('parts.stp','',(''),(''),'','','');
FILE_SCHEMA(('STRUCTURAL_FRAME_SCHEMA'));
ENDSEC;
DATA;
#100=CARTESIAN_POINT('',(2.,3.,0.));
#101=DIRECTION('',(0.,0.,1.));
#102=DIRECTION('',(1.,0.,0.));
#103=AXIS2_PLACEMENT_3D('',#100,#101,#102);
#104=PART_PRISMATIC_SIMPLE(1,'COL',$,$,.STEEL.,$,#31,#105);
#105=POSITIVE_LENGTH_MEASURE_WITH_UNIT(POSITIVE_LENGTH_MEASURE(3.5),$);
#106=LOCATED_PART(1,'P1',$,#103,#104,$);
#31=SECTION_PROFILE_CIRCLE_HOLLOW(1,'PIPE-273',$,$,10,.F.,0.273,0.01);
ENDSEC;
END-ISO-10303-21;
"""


def test_cis2_analysis_model():
    m, warns = read_cis2(_CIS2)
    # only the complex unit instance #1 may be reported (read by pattern)
    assert all("#1" in w for w in warns), warns
    by ={x.uid: x for x in m.members}
    assert by["C1"].pi == (0.0, 0.0, 0.0) and by["C1"].pj == (0.0, 0.0, 4.0)
    assert by["B1"].kind == "beam" and by["B1"].pj == (6.0, 0.0, 4.0)
    assert by["C2"].pi == (6.0, 0.0, 0.0) and by["C2"].kind == "column"
    assert m.sections["W14X90"].db_ref["name"] == "W14X90"
    g = m.sections["GIRDER-600"]                 # parametric I from dims
    assert g.A == pytest.approx(2 * 0.25 * 0.02 + (0.6 - 0.04) * 0.01,
                                rel=1e-12)
    assert len(m.supports) == 2 and m.supports[0].restraints == (1,) * 6
    assert m.materials["S355"].material_type == "steel"
    res = OpenSeesEngine(m).run()                  # analysable as read
    assert {(0.0, 0.0, 4.0), (6.0, 0.0, 4.0)} <= set(res.nodes.values())


def test_cis2_design_model_units_override():
    m, warns = read_cis2(_CIS2_DESIGN)
    x = m.members[0]
    assert x.pi == (2.0, 3.0, 0.0) and x.pj == (2.0, 3.0, 3.5)
    s = m.sections["PIPE-273"]
    assert s.shape == "round_tube" and s.dims["D"] == 0.273
    m_mm, _ = read_cis2(_CIS2_DESIGN.replace("3.5)", "3500.)")
                        .replace("0.273,0.01", "273.,10."), units="mm")
    assert m_mm.members[0].pj == pytest.approx((2e-3, 3e-3, 3.5))


def test_cis2_empty_file_warns():
    m, warns = read_cis2("ISO-10303-21;\nDATA;\nENDSEC;\n")
    assert not m.members and any("no CIS/2 members" in w for w in warns)


# --------------------------------------------------------------- IDEA IOM
def _loaded():
    from skyframe.core.model import MemberLoad
    m = _steel_frame()
    p = m.pattern("DEAD", "dead")
    p.member_loads.append(MemberLoad("B-Story1", w=20.0))
    p.member_loads.append(MemberLoad("B-Story2", w=10.0))
    m.add_case("DEAD", {"DEAD": 1.0})
    m.add_combo("1.35D", {"DEAD": 1.35})
    return m


def test_idea_iom_counts_and_effects():
    m = _loaded()
    res = OpenSeesEngine(m).run().to_dict()
    mx, rx, rep = export_idea(m, res)
    root = ET.fromstring(mx)                       # well-formed
    rr = ET.fromstring(rx)
    assert root.tag == "OpenModel" and rr.tag == "OpenModelResult"
    cnt = lambda tag: len(root.find(tag)) if root.find(tag) is not None \
        else 0                                     # noqa: E731
    assert cnt("Point3D") == 6                     # joints
    assert cnt("LineSegment3D") == cnt("Element1D") == cnt("Member1D") == 7
    assert cnt("CrossSection") == 2 and cnt("MatSteelEc3") == 1
    assert cnt("LoadCase") == 1 and cnt("CombiInput") == 1
    # every joint shared by 2+ members is a connection point
    assert cnt("ConnectionPoint") == rep["written"]["connection_points"] == 5
    names = [e.findtext("Name") for e in root.find("Member1D")]
    assert names == [x.uid for x in m.members]
    cs = root.find("CrossSection")[0]
    assert cs.findtext("CrossSectionType") == "RolledI"
    assert cs.find("Parameters")[0].findtext("Value") == "W14X90"
    # load effects: the internal forces at x = 0 of the first-story
    # columns + brace, rotated with the LCS written in the file, balance
    # the whole DEAD load 20 * 8 + 10 * 8 = 240 kN = 240000 N
    lcs = {}
    for k, seg in enumerate(root.find("LineSegment3D"), start=1):
        cs = seg.find("LocalCoordinateSystem")
        lcs[k] = [[float(cs.find(v).findtext(a)) for a in "XYZ"]
                  for v in ("VecX", "VecY", "VecZ")]
    total = 0.0
    for rm in rr.iter("ResultOnMember"):
        mid = int(rm.find("Member").findtext("Id"))
        uid = m.members[mid - 1].uid
        sec0 = rm.find("Results")[0]
        assert sec0.findtext("Position") == "0"
        f = sec0.find("Results")[0]
        st = res["cases"]["DEAD"]["member_stations"][uid]
        assert float(f.findtext("N")) == pytest.approx(st["N"][0] * 1e3)
        assert float(f.findtext("Qz")) == pytest.approx(st["V2"][0] * 1e3)
        assert float(f.findtext("My")) == pytest.approx(-st["M3"][0] * 1e3)
        if uid in ("C0-Story1", "C8-Story1", "X1"):
            vx, vy, vz = lcs[mid]
            total += sum(float(f.findtext(q)) * v[2] for q, v in
                         (("N", vx), ("Qy", vy), ("Qz", vz)))
    assert total == pytest.approx(-240000.0, rel=1e-9)
    assert rep["written"]["internal_force_results"] == 7 * 11


def test_idea_zip():
    m = _steel_frame()
    data, rep = export_idea_zip(m)
    z = zipfile.ZipFile(io.BytesIO(data))
    assert sorted(z.namelist()) == ["Steel bay.results.xml", "Steel bay.xml"]
    ET.fromstring(z.read("Steel bay.xml"))
    assert any("no analysis results" in s for s in rep["skipped"])


def test_http_steel_formats(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("SKYFRAME_PLUGINS_STATE", str(tmp_path / "p.json"))
    from skyframe.api.server import create_app
    c = create_app().test_client()
    c.post("/api/model", json=_loaded().to_dict())
    sd = c.post("/api/interop/export/sdnf", json={"units": "mm"}).get_json()
    assert sd["ok"] and sd["filename"].endswith(".sdnf")
    imp = c.post("/api/interop/import/sdnf", json={"text": sd["content"],
                                                   "dry_run": True})
    assert imp.status_code == 200
    cis = c.post("/api/interop/import/cis2", json={"text": _CIS2,
                                                   "dry_run": True})
    assert cis.status_code == 200 and len(cis.get_json()["model"]
                                          ["members"]) == 3
    idea = c.post("/api/interop/export/idea", json={}).get_json()
    assert idea["ok"] and idea["filename"].endswith(".zip")
    assert idea["report"]["written"]["internal_force_results"] > 0
    rv = c.post("/api/interop/export/ifc_revit", json={}).get_json()
    assert rv["ok"] and "IFCCOLUMNTYPE" in rv["content"]
