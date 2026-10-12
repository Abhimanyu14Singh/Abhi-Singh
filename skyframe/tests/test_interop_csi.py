"""SAP2000 .s2k and SAFE .f2k (CSI database-table text) -- CONTRACT
"SAP2000 / SAFE / steel interop"."""

import math

import pytest

from skyframe.core import frame_axes as _fa
from skyframe.core.builder import quick_building
from skyframe.core.model import (AreaLoad, MemberLoad, NodalLoad, NodalMass,
                                 PointSupport, ShellSection, SpringSupport)
from skyframe.engine.opensees_engine import OpenSeesEngine
from skyframe.io.csi_tables import read_tables, write_tables
from skyframe.io.f2k import column_reactions, export_f2k, read_f2k
from skyframe.io.s2k import export_s2k, read_s2k


def _quick(**kw):
    return _fa.as_new_model(quick_building(**kw))


def _rich():
    from skyframe.core.section_ext import section_from_dims
    from skyframe.core.steel_db import section_from_db
    m = _quick(stories=2, bays_x=2, bays_y=1)
    m.add_material(__import__("skyframe.core.model", fromlist=["Material"])
                   .Material("A992", 2.0e8, 0.3, 77.0, material_type="steel",
                             fy=345e3, fu=450e3))
    m.add_section(section_from_db("aisc", "W14X90", "A992", "W14X90"))
    m.add_section(section_from_dims("I400", "A992", "I", {
        "d": 0.4, "bf": 0.2, "tf": 0.015, "tw": 0.01}))
    m.add_section(section_from_dims("CH", "A992", "channel", {
        "d": 0.3, "bf": 0.09, "tf": 0.012, "tw": 0.008}))
    m.sections["BEAM"].mod_I33 = 0.35
    m.members[0].section = "W14X90"
    m.members[1].section = "I400"
    m.members[1].angle = 30.0
    b = next(x for x in m.members if x.kind == "beam")
    b.releases = "Mj"
    b2 = [x for x in m.members if x.kind == "beam"][1]
    b2.end_releases = {"i": {"M3": 2500.0}}
    b2.rigid_i, b2.rigid_j = 0.25, 0.25
    m.add_shell_section(ShellSection("SLAB", "CONC", 0.2))
    m.add_shell_section(ShellSection("DECK", "CONC", 0.15))
    m.add_shell("slab", "shell", "SLAB", [(0, 0, 3.2), (6, 0, 3.2),
                                          (6, 6, 3.2), (0, 6, 3.2)],
                mesh_size=1.5, story="Story1", uid="F1")
    m.add_shell("slab", "membrane", "DECK", [(6, 0, 6.4), (12, 0, 6.4),
                                             (12, 6, 6.4), (6, 6, 6.4)],
                story="Story2", uid="F2")
    m.patterns["DEAD"].area_loads.append(AreaLoad("F1", 2.5))
    m.patterns["LIVE"].area_loads.append(AreaLoad("F2", 3.0))
    m.patterns["LIVE"].member_loads += [
        MemberLoad(b.uid, kind="trapezoid", w=4.0, w2=8.0, a=0.1, b=0.9),
        MemberLoad(b.uid, kind="point", w=12.0, a=0.4,
                   direction="global_x"),
        MemberLoad(b2.uid, kind="moment", w=5.0, a=0.5,
                   direction="local_z")]
    m.patterns["LIVE"].nodal_loads.append(NodalLoad((12, 6, 6.4), 1, 2, -3,
                                                    0.1, 0.2, 0.3))
    m.nodal_masses.append(NodalMass((12, 6, 6.4), 0.5, 0.5, 0.0))
    m.add_combo("ENV", {"DEAD": 1.0, "LIVE": 1.0}, combo_type="envelope")
    m.add_group("G1", members=[b.uid], shells=["F1"],
                points=[[0.0, 0.0, 0.0]])
    m.validate()
    return m


def _static(m):
    r = OpenSeesEngine(m).run().to_dict()
    return {c: r["cases"][c] for c in m.cases}, r


def test_table_text_roundtrip():
    rows = [{"Joint": "1", "X": 0.1 + 0.2, "Name": "two words",
             "Flag": True, "Empty": ""}]
    txt = write_tables([("JOINT COORDINATES", rows * 30)], "hdr")
    back = read_tables(txt)["JOINT COORDINATES"]
    assert len(back) == 30
    assert back[0] == {"Joint": "1", "X": repr(0.1 + 0.2),
                       "Name": "two words", "Flag": "Yes", "Empty": ""}


def test_s2k_quick_building_results_identical():
    m = _quick(stories=3, bays_x=2, bays_y=2)
    text, rep = export_s2k(m)
    assert rep["skipped"] == []
    assert 'TABLE:  "JOINT COORDINATES"' in text
    assert 'TABLE:  "CONNECTIVITY - FRAME"' in text
    m2, warns = read_s2k(text)
    assert warns == []
    a, ra = _static(m)
    b, rb = _static(m2)
    for c in a:                                  # identical static results
        assert a[c]["base"] == b[c]["base"]
        assert a[c]["member_forces"] == b[c]["member_forces"]
    assert ra["modal"]["periods"] == rb["modal"]["periods"]
    d1, d2 = m.to_dict(), m2.to_dict()
    for k in d1:
        if k != "patterns":                      # legacy member_udls ->
            assert d1[k] == d2[k], k             # member_loads (same load)
    assert export_s2k(m2)[0] == text             # stable


def test_s2k_rich_model_roundtrip():
    m = _rich()
    text, rep = export_s2k(m)
    m2, warns = read_s2k(text)
    assert warns == []
    assert sorted(m2.sections) == sorted(m.sections)
    for n, s in m.sections.items():
        s2 = m2.sections[n]
        assert (s2.A, s2.I33, s2.I22, s2.J, s2.As2, s2.As3, s2.mod_I33) == \
            (s.A, s.I33, s.I22, s.J, s.As2, s.As3, s.mod_I33), n
    assert m2.sections["W14X90"].db_ref == {"standard": "aisc",
                                            "name": "W14X90"}
    assert m2.sections["I400"].shape == "I"
    by = {x.uid: x for x in m2.members}
    for x in m.members:
        y = by[x.uid]
        assert (y.pi, y.pj, y.section, y.angle, y.releases,
                y.end_releases, y.rigid_i, y.rigid_j) == \
            (x.pi, x.pj, x.section, x.angle, x.releases, x.end_releases,
             x.rigid_i, x.rigid_j), x.uid
    assert [(r.uid, r.behavior, r.section, r.mesh_size) for r in
            m2.shells] == [(r.uid, r.behavior, r.section, r.mesh_size)
                           for r in m.shells]
    assert m2.combos["ENV"].combo_type == "envelope"
    assert m2.groups["G1"]["members"] == m.groups["G1"]["members"]
    assert m2.nodal_masses[0].mx == 0.5
    a, _ = _static(m)
    b, _ = _static(m2)
    for c in a:                                  # identical static results
        assert b[c]["base"] == a[c]["base"], c
        assert b[c]["member_forces"] == a[c]["member_forces"], c


def test_s2k_units_kip_in():
    m = _rich()
    text, _ = export_s2k(m, units=("KIP", "IN"))
    assert 'CurrUnits="Kip, in, F"' in text
    m2, warns = read_s2k(text)
    assert warns == []
    x, y = m.members[5], next(v for v in m2.members if v.uid ==
                              m.members[5].uid)
    for p, q in zip(x.pj, y.pj):
        assert q == pytest.approx(p, rel=1e-12, abs=1e-12)
    assert m2.sections["BEAM"].I33 == pytest.approx(m.sections["BEAM"].I33,
                                                    rel=1e-12)
    a, _ = _static(m)
    b, _ = _static(m2)
    # last-bit unit-conversion changes, amplified by the rigid-offset and
    # partial-fixity penalty stiffnesses: 1e-7 (observed 3.6e-9)
    assert b["DEAD"]["base"]["FZ"] == pytest.approx(a["DEAD"]["base"]["FZ"],
                                                    rel=1e-7)


def test_s2k_skips_are_reported():
    m = _rich()
    m.shells[0].openings.append(
        __import__("skyframe.core.model", fromlist=["Opening"])
        .Opening(0.2, 0.2, 0.4, 0.4))
    m.patterns["DEAD"].accidental_torsion = True
    _t, rep = export_s2k(m)
    txt = " ".join(rep["skipped"])
    assert "opening" in txt and "accidental torsion" in txt


def test_f2k_column_reactions_closed_form():
    m = _quick(stories=3, bays_x=2, bays_y=1)
    res = OpenSeesEngine(m).run().to_dict()
    text, rep = export_f2k(m, "Story1", results=res)
    assert rep["written"]["columns"] == 6 and rep["written"]["beams"] == 7
    # DEAD from above = 25 kN/m on every beam of Story2 and Story3
    L = sum(b.length for b in m.members
            if b.kind == "beam" and b.story in ("Story2", "Story3"))
    assert rep["reaction_totals"]["DEAD"] == pytest.approx(25.0 * L,
                                                           rel=1e-12)
    # EQX: the column shears above Story1 sum to the story forces above
    fx_above = sum(sf.fx for sf in m.patterns["EQX"].story_forces
                   if sf.story in ("Story2", "Story3"))
    reac = column_reactions(m, res, "Story1")["EQX"]
    assert sum(v[0] for v in reac.values()) == pytest.approx(fx_above,
                                                             rel=1e-9)
    t = read_tables(text)
    assert t["PROGRAM CONTROL"][0]["ProgramName"] == "SAFE"
    above = [r for r in t["LOAD ASSIGNMENTS - POINT LOADS"]
             if r["LoadPat"] == "DEAD"]
    assert len(above) == 6
    assert sum(float(r["Fgrav"]) for r in above) == pytest.approx(25 * L)


def test_f2k_roundtrip():
    m = _quick(stories=2, bays_x=1, bays_y=1)
    m.add_shell_section(ShellSection("SLAB", "CONC", 0.2))
    m.add_shell("slab", "shell", "SLAB", [(0, 0, 3.2), (6, 0, 3.2),
                                          (6, 6, 3.2), (0, 6, 3.2)],
                story="Story1", uid="F1")
    m.patterns["LIVE"].area_loads.append(AreaLoad("F1", 2.0))
    res = OpenSeesEngine(m).run().to_dict()
    text, rep = export_f2k(m, "Story1", results=res)
    m2, warns = read_f2k(text)
    assert warns == []
    assert len(m2.shells) == 1 and m2.shells[0].corners == \
        [(0.0, 0.0, 3.2), (6.0, 0.0, 3.2), (6.0, 6.0, 3.2), (0.0, 6.0, 3.2)]
    cols = [x for x in m2.members if x.kind == "column"]
    assert sorted(c.pi[:2] for c in cols) == sorted(
        c.pi[:2] for c in m.members if c.kind == "column"
        and c.story == "Story1")
    assert all(c.pi[2] == 0.0 and c.pj[2] == pytest.approx(3.2) for c in
               cols)
    assert len(m2.supports) == 4
    # loads: floor slab load + floor beam UDLs + loads from above
    a = sum(al.q * 36.0 for al in m2.patterns["LIVE"].area_loads)
    assert a == pytest.approx(72.0)
    above = sum(-nl.fz for nl in m2.patterns["DEAD"].nodal_loads)
    assert above == pytest.approx(rep["reaction_totals"]["DEAD"])
    assert export_f2k(m2, "Floor", loads_from_above=False)[1][
        "written"]["slabs"] == 1


def test_http_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("SKYFRAME_PLUGINS_STATE", str(tmp_path / "p.json"))
    from skyframe.api.server import create_app
    c = create_app().test_client()
    fm = c.get("/api/interop/formats").get_json()["formats"]
    assert {f["fmt"] for f in fm} == {"s2k", "f2k", "sdnf", "cis2", "idea",
                                      "ifc_revit"}
    before = c.get("/api/model").get_json()
    r = c.post("/api/interop/export/s2k", json={}).get_json()
    assert r["ok"] and r["filename"].endswith(".s2k")
    assert c.get("/api/model").get_json() == before   # export: no change
    imp = c.post("/api/interop/import/s2k", json={"text": r["content"]})
    assert imp.status_code == 200
    f2k = c.post("/api/interop/export/f2k", json={"story": "Story1"})
    assert f2k.get_json()["report"]["written"]["column_reactions"] > 0
    assert c.post("/api/interop/import/s2k", json={"text": " "}) \
        .status_code == 400
    assert c.post("/api/interop/export/xyz", json={}).status_code == 400
    soft = c.post("/api/interop/export/f2k", json={
        "story": "nope", "soft_errors": True}).get_json()
    assert soft["ok"] is False
