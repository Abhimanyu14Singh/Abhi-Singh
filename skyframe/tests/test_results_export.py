"""Element / wall / nonlinear Show-Tables tables, spandrel labels + forces,
results export to .xlsx / .sqlite and the Word (.docx) report writer.

Closed forms / hand checks:

* Element Forces - Frames: station 0 == -(end-i local forces), station L ==
  +(end-j local forces) (the engine's statics stations, exact); a cantilever
  tip load P gives M3(0) = -P L, V2 = -P along the member;
* coupled wall: the spandrel is a free body with no load, so the two end
  cuts balance exactly (V_L = V_R, P_L = P_R, M_R - M_L = -V L) and the
  PIER elements at the interface nodes carry the equal and opposite force
  (an independent cut through other elements: sum F = 0);
* frame spandrel (coupling beam): P / V2 / M3 = the member's own stations;
* elastic link: two springs in parallel with a cantilever, F = k u;
* hinge-state table: one row per hinge per step, the pushover curve's
  state counts add up to the number of hinges at every step;
* xlsx / sqlite open with zipfile / sqlite3 and hold exactly the table rows;
* .docx: valid zip + well-formed document.xml with the headings, tables and
  images.
"""

import base64
import io
import json
import sqlite3
import struct
import warnings
import zipfile
import zlib
from xml.etree import ElementTree as ET

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core import modeltables as MT                      # noqa: E402
from skyframe.core import spandrels as SPD                       # noqa: E402
from skyframe.core import tables                                 # noqa: E402
from skyframe.core.builder import quick_building                 # noqa: E402
from skyframe.core.model import (BuildingModel, FrameSection,    # noqa: E402
                                 LoadCase, LoadPattern, Material, NodalLoad,
                                 PointSupport, ShellRegion, ShellSection)
from skyframe.io.docx import build_docx                          # noqa: E402
from skyframe.io.results_export import (export_results_sqlite,   # noqa: E402
                                        export_results_xlsx)
from skyframe.io.xlsx import read_workbook                       # noqa: E402

E = 25e6
FIX = (1, 1, 1, 1, 1, 1)


def _solve(m):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        eng = OpenSeesEngine(m)
        res = eng.run()
    return eng, res, res.to_dict(), tables.engine_context(eng)


def _rows(key, d, m, ctx=None, **kw):
    return tables.compute_table(key, d, m, ctx, **kw)["rows"]


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
def _coupled_wall(h=0.5, frame_beam=False, lateral=100.0):
    """Two 2 m piers joined by a 2 m x 1 m shell spandrel (or a frame
    coupling beam at the top), one 3 m story, lateral load at the top."""
    m = BuildingModel(name="cw")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E, 0.2, 0.0))
    m.add_shell_section(ShellSection("W", "C", 0.3))
    m.set_stories([3.0])
    for uid, x0 in (("P1", 0.0), ("P2", 4.0)):
        m.shells.append(ShellRegion(uid, "wall", "shell", "W",
                                    [(x0, 0, 0), (x0 + 2, 0, 0),
                                     (x0 + 2, 0, 3), (x0, 0, 3)],
                                    mesh_size=h, story="Story1", pier=uid))
    if frame_beam:
        m.add_section(FrameSection.rectangular("CB", "C", 0.3, 0.8))
        m.add_member("beam", "CB", (2, 0, 3), (4, 0, 3), story="Story1",
                     uid="CB1")
        m.members[-1].spandrel = "SP1"
    else:
        m.shells.append(ShellRegion("S1", "wall", "shell", "W",
                                    [(2, 0, 2), (4, 0, 2), (4, 0, 3),
                                     (2, 0, 3)], mesh_size=h,
                                    story="Story1", spandrel="SP1"))
    m.patterns["LAT"] = LoadPattern("LAT", "other")
    m.patterns["LAT"].nodal_loads.append(NodalLoad((0, 0, 3), lateral, 0, 0))
    m.cases["LAT"] = LoadCase("LAT", {"LAT": 1.0})
    m.validate()
    return m


def _cantilever_beam(L=4.0, P=10.0):
    m = BuildingModel(name="cb")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E, 0.2, 0.0))
    m.add_section(FrameSection.rectangular("B", "C", 0.3, 0.5))
    m.set_stories([3.0])
    m.add_member("beam", "B", (0, 0, 3), (L, 0, 3), story="Story1", uid="B1")
    m.supports.append(PointSupport((0, 0, 3), FIX))
    m.patterns["P"] = LoadPattern("P", "other")
    m.patterns["P"].nodal_loads.append(NodalLoad((L, 0, 3), 0, 0, -P))
    m.cases["P"] = LoadCase("P", {"P": 1.0})
    m.validate()
    return m


# --------------------------------------------------------------------------- #
# 1. Element Forces - Frames
# --------------------------------------------------------------------------- #
def test_frame_station_forces_match_member_end_forces():
    m = quick_building(stories=2)
    _e, _r, d, ctx = _solve(m)
    rows = _rows("element_forces_frames", d, m, ctx)
    n_st = sum(len(st["x"]) for cd in list(d["cases"].values())
               + [c for c in d["combos"].values() if "min" not in c]
               for st in cd["member_stations"].values())
    assert len(rows) == n_st
    keys = ("P", "V2", "V3", "T", "M2", "M3")
    for case in ("DEAD", "EQX"):
        mf = d["cases"][case]["member_forces"]
        for uid, f in mf.items():
            rs = [r for r in rows if r["frame"] == uid and r["case"] == case]
            assert rs[0]["station"] == 0.0
            for i, k in enumerate(keys):
                assert rs[0][k] == pytest.approx(-f[i], abs=1e-9)
                assert rs[-1][k] == pytest.approx(f[6 + i], abs=1e-9)
            st = d["cases"][case]["member_stations"][uid]
            assert [r["M3"] for r in rs] == st["M3"]
            assert [r["P"] for r in rs] == st["N"]


def test_frame_station_forces_cantilever_closed_form():
    L, P = 4.0, 10.0
    m = _cantilever_beam(L, P)
    _e, _r, d, ctx = _solve(m)
    rows = _rows("element_forces_frames", d, m, ctx, cases=["P"])
    assert rows and all(r["case_type"] == "LinStatic" for r in rows)
    for r in rows:
        assert r["M3"] == pytest.approx(-P * (L - r["station"]), abs=1e-8)
        assert abs(r["V2"]) == pytest.approx(P, rel=1e-9)
        assert r["story"] == "Story1" and r["frame"] == "B1"


# --------------------------------------------------------------------------- #
# 2. spandrels
# --------------------------------------------------------------------------- #
def test_spandrel_shell_free_body_balances_and_pier_cut_agrees():
    m = _coupled_wall()
    eng, res, d, ctx = _solve(m)
    e = d["spandrels"]["LAT"]["SP1"]["Story1"]
    L, R = e["Left"], e["Right"]
    assert e["length"] == pytest.approx(2.0) and e["n_shells"] == 1
    V = L["V2"]
    assert abs(V) > 1.0                               # coupling is active
    assert R["V2"] == pytest.approx(V, rel=1e-9)
    assert R["P"] == pytest.approx(L["P"], rel=1e-9, abs=1e-9)
    assert R["M3"] - L["M3"] == pytest.approx(-V * 2.0, rel=1e-9)
    for side in (L, R):                               # in-plane only
        assert abs(side["V3"]) < 1e-6 and abs(side["T"]) < 1e-6
    # hand cut through the PIER elements at the interface nodes: no load
    # acts there, so the piers receive exactly the opposite force
    import numpy as np
    asm = eng._asm
    sn = res.cases["LAT"].shell_nodal
    for x_cut, side, sgn in ((2.0, L, -1.0), (4.0, R, 1.0)):
        F = np.zeros(3)
        Fs = np.zeros(3)
        for qi, q in enumerate(asm.shell_quads):
            for k, t in enumerate(q["nodes"]):
                c = asm.node_coords[t]
                if abs(c[0] - x_cut) < 1e-9 and 2.0 - 1e-9 <= c[2] <= 3 + 1e-9:
                    f = np.asarray(sn[qi][6 * k: 6 * k + 3])
                    (Fs if q["region"] == "S1" else F).__iadd__(f)
        # sum over every element meeting the interface nodes = applied = 0
        # except the loaded top-left corner (not on an interface)
        assert np.allclose(F + Fs, 0.0, atol=1e-6)
        # spandrel internal force at the cut, from the pier side
        assert sgn * Fs[2] == pytest.approx(side["V2"], rel=1e-9)
        assert sgn * Fs[0] == pytest.approx(side["P"], rel=1e-9, abs=1e-9)
    # equilibrium of the whole spandrel: both end resultants cancel
    assert -L["V2"] + R["V2"] == pytest.approx(0.0, abs=1e-8)


def test_spandrel_frame_equals_member_stations():
    m = _coupled_wall(frame_beam=True)
    _e, _r, d, ctx = _solve(m)
    e = d["spandrels"]["LAT"]["SP1"]["Story1"]
    st = d["cases"]["LAT"]["member_stations"]["CB1"]
    assert e["n_frames"] == 1 and e["n_shells"] == 0
    for side, i in (("Left", 0), ("Right", -1)):
        assert e[side]["P"] == pytest.approx(st["N"][i], abs=1e-9)
        assert e[side]["V2"] == pytest.approx(st["V2"][i], abs=1e-9)
        assert e[side]["M3"] == pytest.approx(st["M3"][i], abs=1e-9)
    # the table carries the same numbers, ETABS columns
    rows = _rows("spandrel_forces", d, m, ctx)
    assert [r["location"] for r in rows] == ["Left", "Right"]
    assert rows[0]["V2"] == e["Left"]["V2"]
    assert rows[1]["M3"] == e["Right"]["M3"]
    assert rows[0]["spandrel"] == "SP1" and rows[0]["story"] == "Story1"


def test_spandrel_mixed_shell_and_frame_one_free_body():
    """A wall panel AND a beam along its top edge (frame split at the shell
    nodes) with the same label: only their union is unloaded, so the
    combined end cuts balance exactly (separately they do not)."""
    m = _coupled_wall()
    m.add_section(FrameSection.rectangular("CB", "C", 0.3, 0.8))
    m.add_member("beam", "CB", (2, 0, 3), (4, 0, 3), story="Story1",
                 uid="CB1")
    m.validate()
    sep = _solve(m)[2]["spandrels"]["LAT"]["SP1"]["Story1"]
    m.members[-1].spandrel = "SP1"
    e = _solve(m)[2]["spandrels"]["LAT"]["SP1"]["Story1"]
    assert e["n_shells"] == 1 and e["n_frames"] == 1
    assert abs(sep["Left"]["V2"] - sep["Right"]["V2"]) > 0.1   # beam loads it
    assert e["Right"]["V2"] == pytest.approx(e["Left"]["V2"], rel=1e-9)
    assert e["Right"]["P"] == pytest.approx(e["Left"]["P"], rel=1e-9)
    assert e["Right"]["M3"] - e["Left"]["M3"] == pytest.approx(
        -e["Left"]["V2"] * 2.0, rel=1e-9)


def test_spandrel_reversed_frame_and_canonical_left_right():
    """A coupling beam drawn right-to-left reports the same Left / Right
    forces (Left = smaller X)."""
    a = _coupled_wall(frame_beam=True)
    b = _coupled_wall(frame_beam=True)
    mb = b.members[-1]
    mb.pi, mb.pj = mb.pj, mb.pi
    ea = _solve(a)[2]["spandrels"]["LAT"]["SP1"]["Story1"]
    eb = _solve(b)[2]["spandrels"]["LAT"]["SP1"]["Story1"]
    for side in ("Left", "Right"):
        for k in ("P", "V2", "M3"):
            assert eb[side][k] == pytest.approx(ea[side][k], rel=1e-7,
                                                abs=1e-7)


def test_spandrel_legacy_byte_identical_and_round_trip():
    m = quick_building(stories=2)
    d0 = m.to_dict()
    assert "spandrel_labels" not in d0
    assert all("spandrel" not in x for x in d0["members"])
    assert "spandrels" not in _solve(m)[2]
    # labels round-trip
    w = _coupled_wall()
    w.spandrel_labels = ["SP1", "SP2"]
    dd = w.to_dict()
    assert dd["spandrel_labels"] == ["SP1", "SP2"]
    assert [s.get("spandrel") for s in dd["shells"]] == [None, None, "SP1"]
    back = BuildingModel.from_dict(json.loads(json.dumps(dd)))
    d1 = back.to_dict()                       # (loads normalise 0 -> 0.0)
    assert d1["spandrel_labels"] == ["SP1", "SP2"]
    assert d1["shells"][2]["spandrel"] == "SP1"
    d2 = BuildingModel.from_dict(json.loads(json.dumps(d1))).to_dict()
    assert json.dumps(d2, sort_keys=True) == json.dumps(d1, sort_keys=True)
    assert SPD.all_labels(back) == ["SP1", "SP2"]
    assert SPD.summary(back)["SP1"]["shells"] == ["S1"]


def test_spandrel_validation():
    w = _coupled_wall()
    w.spandrel_labels = ["A", "A"]
    with pytest.raises(ValueError, match="twice"):
        w.validate()
    w.spandrel_labels = ["bad,label"]
    with pytest.raises(ValueError):
        w.validate()
    w.spandrel_labels = []
    slab = ShellRegion("SL", "slab", "shell", "W",
                       [(0, 0, 3), (2, 0, 3), (2, 2, 3), (0, 2, 3)],
                       spandrel="X")
    w.shells.append(slab)
    with pytest.raises(ValueError, match="walls only"):
        w.validate()


def test_spandrel_db_edit_table():
    w = _coupled_wall(frame_beam=True)
    d = w.to_dict()
    t = MT.get_table(d, "spandrel_assignments")
    rows = t["rows"]
    assert {(r["object_type"], r["uid"]) for r in rows} == {
        ("Frame", "CB1"), ("Shell", "P1"), ("Shell", "P2")}
    for r in rows:
        if r["uid"] == "P2":
            r["spandrel"] = "SPX"
        if r["uid"] == "CB1":
            r["spandrel"] = ""
    model, errs, summ = MT.apply_tables(d, {"spandrel_assignments": rows})
    assert errs == [] and summ["spandrel_assignments"]["modified"] == 2
    assert model.shells[1].spandrel == "SPX"
    assert model.members[0].spandrel == ""
    # untouched apply is byte-identical
    model2, errs2, _ = MT.apply_tables(d, {"spandrel_assignments":
                                           MT.get_table(d, "spandrel_"
                                                        "assignments")["rows"]})
    assert errs2 == [] and model2.to_dict() == BuildingModel.from_dict(
        d).to_dict()


# --------------------------------------------------------------------------- #
# 3. piers / section cuts / links / tendons
# --------------------------------------------------------------------------- #
def test_pier_and_section_cut_tables_follow_results():
    m = _coupled_wall()
    m.add_section_cut("CUT", "z", 1.0)
    _e, _r, d, ctx = _solve(m)
    rows = _rows("pier_forces", d, m, ctx)
    assert {r["pier"] for r in rows} == {"P1", "P2"}
    for r in rows:
        f = d["piers"][r["case"]][r["pier"]][r["story"]]
        assert r["P"] == -f["P"] and r["V2"] == f["V"] and r["M3"] == f["M"]
        assert r["location"] == "Bottom"
    # base shear shared by the two piers (sum V = 100 kN)
    assert sum(r["V2"] for r in rows) == pytest.approx(100.0, rel=1e-9)
    cuts = _rows("section_cut_forces", d, m, ctx)
    assert len(cuts) == 1
    v = d["section_cuts"]["LAT"]["CUT"]
    assert cuts[0]["F1"] == v["FX"] and cuts[0]["n_shells"] == v["n_shells"]


def test_link_forces_elastic_parallel_springs():
    """Cantilever column (k_c = 3EI/L^3) + horizontal link k to a fixed
    point at the tip: link force = H k / (k + k_c)."""
    L, H, k = 3.0, 50.0, 4000.0
    m = BuildingModel(name="lk")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E, 0.2, 0.0))
    m.add_section(FrameSection.rectangular("S", "C", 0.4, 0.4))
    m.set_stories([L])
    m.add_member("column", "S", (0, 0, 0), (0, 0, L), story="Story1",
                 uid="C1")
    m.supports.append(PointSupport((0, 0, 0), FIX))
    m.add_link((0, 0, L), (2, 0, L), [k, 0, 0, 0, 0, 0], uid="K1")
    m.supports.append(PointSupport((2, 0, L), FIX))
    m.patterns["H"] = LoadPattern("H", "other")
    m.patterns["H"].nodal_loads.append(NodalLoad((0, 0, L), H, 0, 0))
    m.cases["H"] = LoadCase("H", {"H": 1.0})
    m.validate()
    _e, _r, d, ctx = _solve(m)
    I = 0.4 ** 4 / 12.0
    kc = 3 * E * I / L ** 3
    rows = _rows("element_forces_links", d, m, ctx)
    assert [r["location"] for r in rows] == ["I-End", "J-End"]
    # link pulls the tip back: F = k (u_j - u_i) = -k u_tip
    assert rows[0]["P"] == pytest.approx(-H * k / (k + kc), rel=1e-6)
    assert rows[0]["V2"] == 0.0 and rows[0]["story"] == "Story1"
    # same without the engine context (coordinate fallback)
    rows2 = _rows("element_forces_links", d, m, None)
    assert rows2[0]["P"] == pytest.approx(rows[0]["P"], rel=1e-12)


def test_tendon_force_table():
    from test_tendons import _ss
    m = _ss()
    _e, _r, d, ctx = _solve(m)
    rows = _rows("tendon_forces", d, m, ctx)
    st = d["tendons"]["T1"]["stations"]
    assert [r["P"] for r in rows] == st["P"]
    assert rows[0]["loss"] == pytest.approx(0.0, abs=1e-9)
    assert rows[0]["x"] == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# 4. hinge states / pushover curve / TH steps
# --------------------------------------------------------------------------- #
def _push():
    from test_wave20 import _cantilever
    return _cantilever(drift=0.06, steps=60)


def test_hinge_state_counts_and_pushover_curve():
    m = _push()
    _e, _r, d, ctx = _solve(m)
    po = d["pushover"]["PUSH"]
    hinges = po["hinges"]
    assert hinges
    rows = _rows("frame_hinge_states", d, m, ctx)
    assert len(rows) == sum(len(h["rot"]) for h in hinges)
    from skyframe.design.hinges import hinge_state
    from test_wave20 import E_STEEL
    k33 = (engine_mod.HINGE_STIFFNESS_FACTOR * 6.0 * E_STEEL
           * m.sections["W18x50"].I33 / 3.0)
    # legacy state <-> ETABS status, and plastic deformation |rot| - thy
    for r in rows:
        h = next(x for x in hinges if f"{x['uid']}-{x['end']}" == r["hinge"])
        lg = h["state"][r["step"] - 1]
        assert r["status"] == {"elastic": "<=IO", "IO": "<=IO",
                               "LS": "IO to LS", "CP": "LS to CP",
                               "collapse": ">CP"}[lg]
        assert (r["state"] == "A to B") == (lg == "elastic")
        rot = h["rot"][r["step"] - 1]
        assert r["plastic"] == pytest.approx(
            max(0.0, abs(rot) - h["My"] / k33) if lg != "elastic" else 0.0,
            rel=1e-6, abs=1e-12)
        assert hinge_state(rot, h, k33) == lg
    curve = _rows("pushover_curve", d, m, ctx)
    assert len(curve) == len(po["roof_disp"])
    for r in curve:
        assert r["total"] == len(hinges)
        assert r["AtoIO"] + r["IOtoLS"] + r["LStoCP"] + r["beyondCP"] == \
            len(hinges)
        k = r["step"] - 1
        assert r["AtoB"] == sum(1 for h in hinges if h["state"][k] ==
                                "elastic")
        assert r["base_force"] == po["base_shear"][k]
    assert any(r["AtoB"] < len(hinges) for r in curve)   # hinges yield
    # case filter naming only static cases does not hide pushover rows
    assert len(_rows("pushover_curve", d, m, ctx, cases=["X"])) == len(curve)


def test_time_history_step_tables():
    m = quick_building(stories=2)
    acc = [0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5, 0.0, 0.0]
    m.add_th_case("THX", "X", acc, 0.02, damping=0.05)
    _e, _r, d, ctx = _solve(m)
    th = d["th_cases"]["THX"]
    base = _rows("th_base_reactions_steps", d, m, ctx)
    assert len(base) == len(th["t"])
    assert [r["FX"] for r in base] == th["base_FX"]
    story = _rows("th_story_displacements_steps", d, m, ctx)
    assert len(story) == len(th["t"]) * 2
    r = next(x for x in story if x["step"] == 3 and x["story"] == "Story2")
    assert r["UX"] == th["story_ux"]["Story2"][2]


def test_nonlinear_static_steps_table():
    """Linear column under a 4-step nonlinear static case: the base FX of
    step k is -lambda_k H (equilibrium with the applied load)."""
    from test_nonlinear_static import _column
    H = 7.0
    m = _column(H=H)
    m.add_nonlinear_static_case("NL", {"H": 1.0}, steps=4)
    _e, _r, d, ctx = _solve(m)
    hist = d["nonlinear_static"]["NL"]["nonlinear"]["history"]
    rows = _rows("nonlinear_static_steps", d, m, ctx)
    assert [r["step"] for r in rows] == hist["step"]
    for r in rows:
        assert r["FX"] == pytest.approx(-r["load_factor"] * H, rel=1e-9,
                                        abs=1e-9)
    assert rows[-1]["load_factor"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 5. export xlsx / sqlite
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def solved():
    m = _coupled_wall(frame_beam=True)
    m.add_section_cut("CUT", "z", 1.0)
    _e, _r, d, ctx = _solve(m)
    return m, d, ctx


def test_xlsx_export_one_sheet_per_table(solved):
    m, d, ctx = solved
    keys = ["element_forces_frames", "spandrel_forces", "pier_forces",
            "story_drifts"]
    data, rep = export_results_xlsx(m, d, ctx, keys=keys)
    assert zipfile.ZipFile(io.BytesIO(data)).testzip() is None
    book = read_workbook(data)
    titles = {k: tables.TABLES[k][0] for k in keys}
    assert list(book) == ["_index"] + [titles[k] for k in
                                       [t["key"] for t in tables.list_tables()
                                        if t["key"] in keys]] + ["_meta"]
    for k in keys:
        t = tables.compute_table(k, d, m, ctx)
        sheet = book[titles[k]]
        assert len(sheet) == 1 + len(t["rows"]) == 1 + rep["tables"][k]
        assert sheet[0][0] == t["columns"][0]["label"]
    sp = book["Spandrel Forces"]
    head = sp[0]
    assert "V2 [kN]" in head and "M3 [kN*m]" in head
    v = float(sp[1][head.index("V2 [kN]")])
    assert v == tables.compute_table("spandrel_forces", d, m,
                                     ctx)["rows"][0]["V2"]
    # kip-ft conversion of the same cell
    data2, _ = export_results_xlsx(m, d, ctx, keys=["spandrel_forces"],
                                   units="kip-ft")
    sp2 = read_workbook(data2)["Spandrel Forces"]
    h2 = sp2[0]
    assert "V2 [kip]" in h2 and "M3 [kip*ft]" in h2
    assert float(sp2[1][h2.index("V2 [kip]")]) == pytest.approx(
        v / 4.4482216152605, rel=1e-12)
    with pytest.raises(ValueError):
        export_results_xlsx(m, d, ctx, keys=keys, units="furlong")
    with pytest.raises(KeyError):
        export_results_xlsx(m, d, ctx, keys=["nope"])


def test_sqlite_export_tables_rows_units(solved, tmp_path):
    m, d, ctx = solved
    data, rep = export_results_sqlite(m, d, ctx)
    p = tmp_path / "r.sqlite"
    p.write_bytes(data)
    con = sqlite3.connect(str(p))
    idx = dict(con.execute('SELECT TableKey, NumRows FROM "Table Index"'))
    assert set(idx) == set(tables.TABLES)
    for key, n in idx.items():
        name = con.execute('SELECT TableName FROM "Table Index" WHERE '
                           'TableKey = ?', (key,)).fetchone()[0]
        cnt = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        assert cnt == n == rep["tables"][key]
    cols = [r[1] for r in con.execute(
        'PRAGMA table_info("Element Forces - Frames")')]
    assert cols[:5] == ["Story", "Frame", "OutputCase", "CaseType",
                        "Station"]
    m3 = con.execute('SELECT M3 FROM "Element Forces - Frames" WHERE '
                     'Frame = ? AND Station = 0 AND OutputCase = ?',
                     ("CB1", "LAT")).fetchone()[0]
    assert m3 == d["cases"]["LAT"]["member_stations"]["CB1"]["M3"][0]
    unit = con.execute('SELECT Unit FROM "Field Units" WHERE TableName = '
                       '"Element Forces - Frames" AND Field = "M3"'
                       ).fetchone()[0]
    assert unit == "kN*m"
    assert dict(con.execute('SELECT * FROM "Program Control"'))["Units"] == \
        "kN-m"
    con.close()


# --------------------------------------------------------------------------- #
# 6. docx
# --------------------------------------------------------------------------- #
def _png(w=4, h=3):
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(t, b):
        return (struct.pack(">I", len(b)) + t + b
                + struct.pack(">I", zlib.crc32(t + b) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_docx_structure_and_content():
    png = base64.b64encode(_png(1200, 600)).decode()
    data = build_docx({
        "title": "CW — Analysis Report", "subtitle": "test",
        "meta": [["Units", "kN-m"]],
        "blocks": [
            {"type": "heading", "text": "1. Spandrel Forces", "level": 1},
            {"type": "paragraph", "text": "a & b < c", "style": "note"},
            {"type": "table", "columns": ["Story", "V2 [kN]"],
             "rows": [["Story1", 24.4054], ["Story2", -1.5]]},
            {"type": "image", "png_b64": png, "caption": "LAT deformed"},
            {"type": "page_break"},
            {"type": "image", "data_url": "data:image/png;base64," + png},
        ]})
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None
    names = set(z.namelist())
    assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml",
            "word/styles.xml", "word/_rels/document.xml.rels",
            "word/media/image1.png", "word/media/image2.png",
            "docProps/core.xml"} <= names
    for n in names:
        if n.endswith(".xml") or n.endswith(".rels"):
            ET.fromstring(z.read(n))                  # well-formed XML
    doc = z.read("word/document.xml").decode()
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ET.fromstring(doc)
    texts = "".join(t.text or "" for t in root.iter(W + "t"))
    assert "1. Spandrel Forces" in texts and "a & b < c" in texts
    assert "24.4054" in texts and "Story2" in texts
    assert len(list(root.iter(W + "tbl"))) == 2       # meta + data table
    # the image is scaled to the text width
    ext = [e for e in root.iter(
        "{http://schemas.openxmlformats.org/drawingml/2006/"
        "wordprocessingDrawing}extent")]
    cx = int(ext[0].get("cx"))
    assert cx == (11906 - 2 * 1020) * 635
    assert int(ext[0].get("cy")) == cx // 2
    # width_px sets the display size (aspect kept)
    small = ET.fromstring(zipfile.ZipFile(io.BytesIO(build_docx({"blocks": [
        {"type": "image", "png_b64": png, "width_px": 300}]}))).read(
        "word/document.xml"))
    e2 = next(small.iter("{http://schemas.openxmlformats.org/drawingml/"
                         "2006/wordprocessingDrawing}extent"))
    assert int(e2.get("cx")) == 300 * 9525 and int(e2.get("cy")) == 150 * 9525
    rels = z.read("word/_rels/document.xml.rels").decode()
    assert "media/image1.png" in rels and "rIdImg2" in rels
    assert z.read("word/media/image1.png")[:8] == b"\x89PNG\r\n\x1a\n"


def test_docx_validation_errors():
    with pytest.raises(ValueError, match="PNG"):
        build_docx({"blocks": [{"type": "image",
                                "png_b64": base64.b64encode(b"GIF89a....")
                                .decode()}]})
    with pytest.raises(ValueError, match="unknown type"):
        build_docx({"blocks": [{"type": "video"}]})
    with pytest.raises(ValueError, match="columns"):
        build_docx({"blocks": [{"type": "table", "rows": []}]})
    with pytest.raises(ValueError, match="paper"):
        build_docx({"paper": "A3", "blocks": []})


# --------------------------------------------------------------------------- #
# 7. HTTP API
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client():
    from skyframe.api import server
    app = server.create_app()
    app.config["TESTING"] = True
    saved = server._state["model"]
    yield app.test_client(), server
    server._state["model"] = saved
    server._tables_store.clear()


def test_api_export_and_docx(client):
    c, server = client
    m = _coupled_wall(frame_beam=True)
    r = c.post("/api/model", json=m.to_dict())
    assert r.status_code == 200
    assert r.get_json()["members"][0]["spandrel"] == "SP1"
    assert c.post("/api/analyze").status_code == 200
    r = c.post("/api/tables/spandrel_forces", json={})
    assert r.status_code == 200 and len(r.get_json()["rows"]) == 2
    r = c.post("/api/tables/export/xlsx",
               json={"tables": ["spandrel_forces", "element_forces_frames"],
                     "units": "kN-m"})
    j = r.get_json()
    assert r.status_code == 200 and j["filename"].endswith(".xlsx")
    book = read_workbook(base64.b64decode(j["content_b64"]))
    assert len(book["Spandrel Forces"]) == 3
    r = c.post("/api/tables/export/sqlite", json={})
    j = r.get_json()
    assert r.status_code == 200 and j["mime"] == "application/vnd.sqlite3"
    raw = base64.b64decode(j["content_b64"])
    assert raw[:16] == b"SQLite format 3\x00"
    assert c.post("/api/tables/export/xls", json={}).status_code == 400
    assert c.post("/api/tables/export/xlsx",
                  json={"tables": ["nope"]}).status_code == 404
    assert c.post("/api/tables/export/xlsx",
                  json={"tables": []}).status_code == 400
    assert c.post("/api/tables/export/xlsx",
                  json={"units": "bad"}).status_code == 400
    r = c.post("/api/report/docx", json={
        "title": "T", "blocks": [{"type": "heading", "text": "H"}]})
    j = r.get_json()
    assert r.status_code == 200 and j["filename"].endswith(".docx")
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(j["content_b64"])))
    assert "word/document.xml" in z.namelist()
    assert c.post("/api/report/docx",
                  json={"blocks": [{"type": "x"}]}).status_code == 400
    # catalogue lists the new tables
    keys = [t["key"] for t in c.get("/api/tables/list").get_json()["tables"]]
    assert {"element_forces_frames", "spandrel_forces", "pier_forces",
            "section_cut_forces", "frame_hinge_states", "tendon_forces",
            "element_forces_links", "pushover_curve"} <= set(keys)
    # the report data endpoint accepts the new tables
    r = c.post("/api/report/data", json={"tables": ["spandrel_forces"]})
    assert len(r.get_json()["tables"]["spandrel_forces"]["rows"]) == 2
