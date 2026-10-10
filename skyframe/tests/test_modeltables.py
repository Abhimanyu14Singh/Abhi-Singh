"""Interactive Database Editing — the model as editable tables
(skyframe.core.modeltables + /api/modeltables/*).

Pinned behaviour:
  * model -> rows -> apply is the identity (byte-identical model JSON) for
    every table, one at a time and all at once, also through CSV and the
    whole-model zip;
  * edits change exactly the targeted fields (rectangle b/h recompute is
    FrameSection.rectangular; story elevations are cumulative heights);
  * bad rows are rejected with precise {table,row,col} errors and NOTHING is
    applied (the base dict is never mutated);
  * renames cascade, object deletion removes dependent loads / groups;
  * a section edit through the table drives the engine to the closed-form
    cantilever deflection P L^3 / (3 E I).
"""

import copy
import io
import json
import zipfile

import pytest

from skyframe.core import modeltables as MT
from skyframe.core.builder import quick_building
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 Material, MemberLoad, NodalLoad, NodalMass,
                                 PointSupport, ShellSection)

J = lambda d: json.dumps(d)                                   # noqa: E731
JS = lambda d: json.dumps(d, sort_keys=True)                  # noqa: E731


def rich_model() -> BuildingModel:
    """quick_building + one of every tabulated feature."""
    m = quick_building(bays_x=2, bays_y=1, stories=2)
    m.add_shell_section(ShellSection("SLAB20", "CONC", 0.2))
    z = m.stories[0].elevation
    m.add_shell("slab", "shell", "SLAB20",
                [(0, 0, z), (6, 0, z), (6, 6, z), (0, 6, z)],
                mesh_size=3.0, story=m.stories[0].name, uid="S1")
    m.add_link((12, 0, 0), (12, 0, -0.5), [1e5, 1e5, 1e5, 0, 0, 0], uid="L1")
    m.add_spring_support((12, 6, 0), [1e4, 1e4, 1e4, 0, 0, 0])
    m.add_line_spring((0, 0, 0), (6, 0, 0), kz=5e3)
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.patterns["LIVE"].nodal_loads.append(
        NodalLoad((6, 6, z), fz=-10.0, mx=2.0))
    m.patterns["DEAD"].member_loads.append(
        MemberLoad(m.members[-1].uid, kind="point", w=12.0, a=0.3))
    m.patterns["DEAD"].member_loads.append(
        MemberLoad(m.members[-2].uid, kind="trapezoid", w=1.0, w2=3.0, a=0.1,
                   b=0.9, direction="global_z"))
    m.patterns["DEAD"].area_loads.append(AreaLoad("S1", 2.5))
    m.add_spectrum_function("SPEC", [[0, 0.5], [1, 1.0], [2, 0.5]])
    m.add_rs_case("RSX", "X", function="SPEC")
    m.add_th_function("REC", [0, 0.1, -0.2, 0.05, 0], 0.01)
    m.add_th_case("THX", "X", function="REC", dt=0.01)
    m.add_pushover_case("PUSHX", "X", gravity={"DEAD": 1.0})
    m.add_buckling_case("BUCK", {"DEAD": 1.0})
    m.add_staged_case("STG", "DEAD")
    m.add_combo("ENV", {"DEAD": 1.0, "RSX": 1.0}, combo_type="envelope")
    m.add_group("G1", members=[m.members[0].uid], shells=["S1"],
                links=["L1"], points=[[0, 0, 0]], color="#ff0000")
    m.diaphragms = {"D1": {"type": "rigid"}}
    m.joint_diaphragms = [{"point": [12.0, 6.0, z], "diaphragm": "D1"}]
    m.story_diaphragm = {m.stories[1].name: "none"}
    m.validate()
    return BuildingModel.from_dict(m.to_dict())


@pytest.fixture(scope="module")
def base():
    return rich_model().to_dict()


@pytest.fixture
def d(base):
    return copy.deepcopy(base)


def rows_of(d, key):
    return json.loads(json.dumps(MT.get_table(d, key)["rows"]))


def apply(d, **tables):
    before = J(d)
    m, errs, summary = MT.apply_tables(d, tables)
    assert J(d) == before, "apply must never mutate the base dict"
    return m, errs, summary


EDITABLE = [t.key for t in MT.catalogue() if t.editable]
REQUIRED = {"materials", "frame_sections", "shell_sections", "stories",
            "grid_lines", "joints", "frame_objects", "shell_objects", "links",
            "supports", "point_springs", "line_springs", "load_patterns",
            "joint_loads", "frame_distributed_loads", "frame_point_loads",
            "area_loads", "story_forces", "load_cases", "static_cases", "static_case_loads",
            "rs_cases", "th_cases", "pushover_cases", "buckling_cases",
            "staged_cases", "load_combinations", "combo_members",
            "mass_source", "mass_source_options", "groups",
            "group_assignments", "diaphragm_definitions", "story_diaphragms",
            "joint_diaphragms"}


# --------------------------------------------------------------------------- #
# catalogue + metadata
# --------------------------------------------------------------------------- #
def test_catalogue_covers_every_model_definition_table(base):
    keys = MT.table_keys()
    assert REQUIRED <= set(keys)
    assert len(keys) == len(set(keys))
    lst = MT.list_tables(base)
    assert [t["key"] for t in lst] == keys
    assert all(t["group"].startswith("Model Definition") for t in lst)
    assert {t["key"] for t in lst if not t["editable"]} == {"joints",
                                                            "load_cases"}


def test_column_metadata_and_units(base):
    for t in MT.catalogue():
        cols = MT.get_table(base, t.key)["columns"]
        assert cols, t.key
        for c in cols:
            assert set(c) >= {"key", "label", "quantity", "type", "editable",
                              "unit"}
            assert c["quantity"] in MT.QUANTITIES
            assert c["unit"] == MT.QUANTITIES[c["quantity"]]
            assert c["type"] in MT.COL_TYPES
            if c["type"] == "enum":
                assert isinstance(c["enum"], list)
    q = {c["key"]: c for c in MT.get_table(base, "frame_sections")["columns"]}
    assert (q["A"]["quantity"], q["A"]["unit"]) == ("area", "m^2")
    assert (q["I33"]["quantity"], q["I33"]["unit"]) == ("inertia", "m^4")
    assert q["b"]["quantity"] == "dim"
    assert q["material"]["enum"] == ["CONC"]
    m = {c["key"]: c for c in MT.get_table(base, "materials")["columns"]}
    assert (m["E"]["quantity"], m["E"]["unit"]) == ("modulus", "kPa")
    assert m["G"]["editable"] is False
    f = {c["key"]: c for c in MT.get_table(base, "frame_objects")["columns"]}
    assert f["ix"]["quantity"] == "length" and f["angle"]["unit"] == "deg"
    assert f["section"]["enum"] == ["COL", "BEAM"]
    jl = {c["key"]: c for c in MT.get_table(base, "joint_loads")["columns"]}
    assert jl["fx"]["unit"] == "kN" and jl["mx"]["unit"] == "kN*m"
    dl = {c["key"]: c for c in
          MT.get_table(base, "frame_distributed_loads")["columns"]}
    assert dl["w"]["quantity"] == "line_force"
    al = {c["key"]: c for c in MT.get_table(base, "area_loads")["columns"]}
    assert al["q"]["unit"] == "kPa"


# --------------------------------------------------------------------------- #
# round trips
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", EDITABLE)
def test_round_trip_each_table_is_byte_identical(d, key):
    m, errs, _ = apply(d, **{key: rows_of(d, key)})
    assert errs == []
    assert J(m.to_dict()) == J(d)


def test_round_trip_all_tables_at_once(d):
    m, errs, summary = apply(d, **{k: rows_of(d, k) for k in EDITABLE})
    assert errs == []
    assert J(m.to_dict()) == J(d)
    assert set(summary) == set(EDITABLE)


def test_round_trip_quick_building_and_empty_model():
    for mdl in (quick_building(), BuildingModel()):
        dd = mdl.to_dict()
        m, errs, _ = MT.apply_tables(dd, {k: rows_of(dd, k) for k in EDITABLE})
        assert errs == [] and J(m.to_dict()) == J(dd)


@pytest.mark.parametrize("key", EDITABLE)
def test_csv_export_import_identity(d, key):
    text = MT.table_to_csv(d, key)
    rows = MT.csv_to_rows(d, key, text)
    assert len(rows) == len(MT.get_table(d, key)["rows"])
    m, errs, _ = apply(d, **{key: rows})
    assert errs == []
    assert J(m.to_dict()) == J(d)
    assert MT.table_to_csv(m.to_dict(), key) == text


def test_csv_with_labels_units_and_no_ids_edits_by_key(d):
    # Excel-style sheet: label headers with [unit] suffixes, no _id column
    text = ("Name,Material,b (width) [m],h (depth) [m]\n"
            "BEAM,CONC,0.3,0.7\n"
            "COL,CONC,0.5,0.5\n")
    rows = MT.csv_to_rows(d, "frame_sections", text)
    assert rows[0] == {"name": "BEAM", "material": "CONC", "b": 0.3, "h": 0.7}
    m, errs, _ = apply(d, frame_sections=rows)
    assert errs == []
    r = FrameSection.rectangular("BEAM", "CONC", 0.3, 0.7)
    s = m.sections["BEAM"]
    assert (s.A, s.I33, s.I22, s.J) == (r.A, r.I33, r.I22, r.J)
    assert m.sections["COL"].to_dict() == d["sections"]["COL"] | {}


def test_zip_export_import_identity_and_foreign_model(d):
    data = MT.export_zip(d)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
    assert {"_index.csv", "_meta.json"} <= names
    assert {f"{k}.csv" for k in MT.table_keys()} <= names
    m, errs, _ = MT.import_zip(d, data)
    assert errs == [] and J(m.to_dict()) == J(d)
    # into an EMPTY model: ids dropped, everything rebuilt from the sheets
    qb = quick_building().to_dict()
    m2, errs2, _ = MT.import_zip(BuildingModel().to_dict(), MT.export_zip(qb))
    assert errs2 == []
    got = m2.to_dict()
    # model-level scalars (name, modes, legacy mass alias) are not tables
    for k in ("name", "num_modes", "mass_from_patterns"):
        got.pop(k), qb.pop(k)
    assert JS(got) == JS(qb)
    # function libraries are not tables: RS cases naming one need them
    m3, errs3, _ = MT.import_zip(BuildingModel().to_dict(), data)
    assert m3 is None and errs3[0]["col"] == "function"
    assert MT.import_zip(d, b"not a zip")[1][0]["message"] == \
        "not a zip archive"


# --------------------------------------------------------------------------- #
# edits
# --------------------------------------------------------------------------- #
def test_edit_section_dimension_recomputes_rectangle(d):
    rows = rows_of(d, "frame_sections")
    beam = next(r for r in rows if r["name"] == "BEAM")
    beam["h"] = 0.75
    m, errs, summary = apply(d, frame_sections=rows)
    assert errs == []
    r = FrameSection.rectangular("BEAM", "CONC", 0.3, 0.75)
    s = m.sections["BEAM"]
    assert s.h == 0.75 and s.A == r.A == pytest.approx(0.225, rel=1e-15)
    assert (s.I33, s.I22, s.J) == (r.I33, r.I22, r.J)
    assert s.I33 == 0.3 * 0.75 ** 3 / 12.0
    assert summary["frame_sections"]["modified"] == 1
    assert m.sections["COL"].to_dict() == d["sections"]["COL"]


def test_explicit_property_edit_wins_over_dims(d):
    rows = rows_of(d, "frame_sections")
    beam = next(r for r in rows if r["name"] == "BEAM")
    beam["I33"] = 1.0e-2
    m, errs, _ = apply(d, frame_sections=rows)
    assert errs == []
    assert m.sections["BEAM"].I33 == 1.0e-2
    assert m.sections["BEAM"].A == d["sections"]["BEAM"]["A"]


def test_add_section_by_dimensions_and_reject_incomplete(d):
    rows = rows_of(d, "frame_sections") + [
        {"name": "B2", "material": "CONC", "b": 0.4, "h": 0.8}]
    m, errs, _ = apply(d, frame_sections=rows)
    assert errs == []
    assert m.sections["B2"].A == 0.4 * 0.8
    bad = rows_of(d, "frame_sections") + [
        {"name": "B3", "material": "CONC", "A": 0.1}]
    m, errs, _ = apply(d, frame_sections=bad)
    assert m is None and errs[0]["row"] == 2 and errs[0]["col"] == "A"


def test_add_load_case_and_combo_in_one_apply(d):
    cases = rows_of(d, "static_cases") + [{"name": "WIND1"}]
    loads = rows_of(d, "static_case_loads") + [
        {"case": "WIND1", "pattern": "EQX", "factor": 0.5},
        {"case": "WIND1", "pattern": "LIVE", "factor": 0.25}]
    combos = rows_of(d, "load_combinations") + [
        {"name": "U9", "combo_type": "add"}]
    members = rows_of(d, "combo_members") + [
        {"combo": "U9", "case": "DEAD", "factor": 1.2},
        {"combo": "U9", "case": "WIND1", "factor": 1.0}]
    m, errs, _ = apply(d, combo_members=members, static_cases=cases,
                       load_combinations=combos, static_case_loads=loads)
    assert errs == []
    assert m.cases["WIND1"].patterns == {"EQX": 0.5, "LIVE": 0.25}
    assert m.cases["WIND1"].geometric == "linear"
    assert m.combos["U9"].cases == {"DEAD": 1.2, "WIND1": 1.0}
    assert list(m.combos)[-1] == "U9"


def test_paste_ten_member_sections(d):
    rows = rows_of(d, "frame_objects")
    beams = [r for r in rows if r["kind"] == "beam"][:10]
    assert len(beams) == 10
    for r in beams:
        r["section"] = "COL"
    m, errs, summary = apply(d, frame_objects=rows)
    assert errs == []
    changed = {r["uid"] for r in beams}
    for mm, od in zip(m.members, d["members"]):
        assert mm.section == ("COL" if od["uid"] in changed else od["section"])
    assert summary["frame_objects"] == {"added": 0, "modified": 10,
                                        "deleted": 0}


def test_delete_frame_object_cascades_loads_and_groups(d):
    rows = rows_of(d, "frame_objects")
    victim = rows[0]["uid"]
    assert victim in d["groups"]["G1"]["members"]
    m, errs, summary = apply(d, frame_objects=rows[1:])
    assert errs == []
    assert victim not in {x.uid for x in m.members}
    assert victim not in m.groups["G1"]["members"]
    for p in m.patterns.values():
        assert all(u.member_uid != victim for u in p.member_udls)
        assert all(u.member_uid != victim for u in p.all_member_loads())
    assert summary["frame_objects"]["deleted"] == 1


def test_rename_cascades(d):
    mats = rows_of(d, "materials")
    mats[0]["name"] = "C30"
    secs = rows_of(d, "frame_sections")
    for r in secs:
        if r["name"] == "BEAM":
            r["name"] = "B300x600"
    pats = rows_of(d, "load_patterns")
    for r in pats:
        if r["name"] == "DEAD":
            r["name"] = "D"
    m, errs, _ = apply(d, materials=mats, frame_sections=secs,
                       load_patterns=pats)
    assert errs == []
    assert set(m.materials) == {"C30"}
    assert all(s.material == "C30" for s in m.sections.values())
    assert m.shell_sections["SLAB20"].material == "C30"
    assert {mm.section for mm in m.members} == {"COL", "B300x600"}
    assert m.cases["DEAD"].patterns == {"D": 1.0}
    assert m.mass_source == {"D": 1.0}
    assert m.staged_cases["STG"].pattern == "D"
    assert m.pushover_cases["PUSHX"].gravity == {"D": 1.0}


def test_story_height_edit_recomputes_elevations():
    d = quick_building(stories=2).to_dict()
    rows = rows_of(d, "stories")
    rows[0]["height"] = 4.0
    m, errs, _ = apply(d, stories=rows)
    assert errs == []
    h1 = d["stories"][1]["height"]
    assert [s.elevation for s in m.stories] == [4.0, 4.0 + h1]


def test_grid_lines_add_line_and_new_system(d):
    rows = rows_of(d, "grid_lines")
    rows.append({"system": "G1", "axis": "x", "ordinate": 15.0})
    rows.append({"system": "AUX", "axis": "y", "ordinate": 2.0})
    m, errs, _ = apply(d, grid_lines=rows)
    assert errs == []
    g = {x.name: x for x in m.effective_grids()}
    assert g["G1"].x_lines[-1] == 15.0
    assert g["AUX"].y_lines == [2.0] and g["AUX"].x_lines == []
    bad = rows_of(d, "grid_lines") + [{"system": "G1", "axis": "radius",
                                       "ordinate": 3.0}]
    m, errs, _ = apply(d, grid_lines=bad)
    assert m is None and errs[0]["col"] == "axis"


def test_distributed_loads_legacy_udl_slots(d):
    rows = rows_of(d, "frame_distributed_loads")
    dead = [r for r in rows if r["pattern"] == "DEAD"]
    dead[0]["w"] = 30.0                    # stays a legacy member_udl
    dead[1]["direction"] = "global_x"      # no longer representable -> moves
    m, errs, _ = apply(d, frame_distributed_loads=rows)
    assert errs == []
    p = m.patterns["DEAD"]
    assert p.member_udls[0].w == 30.0
    assert len(p.member_udls) == len(d["patterns"]["DEAD"]["member_udls"]) - 1
    moved = p.member_loads[-1]
    assert (moved.member_uid, moved.direction, moved.kind) == \
        (dead[1]["member_uid"], "global_x", "udl")
    # the point load (another table) kept its place
    assert p.member_loads[0].kind == "point"


def test_add_joint_load_point_load_and_area_load(d):
    z = d["stories"][0]["elevation"]
    jl = rows_of(d, "joint_loads") + [
        {"pattern": "DEAD", "x": 0, "y": 0, "z": z, "fz": "-7.5"}]
    pl = rows_of(d, "frame_point_loads") + [
        {"pattern": "LIVE", "member_uid": d["members"][-1]["uid"], "w": 4,
         "a": 0.5}]
    al = rows_of(d, "area_loads")
    al[0]["q"] = 3.0
    m, errs, _ = apply(d, joint_loads=jl, frame_point_loads=pl, area_loads=al)
    assert errs == []
    nl = m.patterns["DEAD"].nodal_loads[-1]
    assert (nl.point, nl.fz, nl.fx) == ((0.0, 0.0, z), -7.5, 0.0)
    ml = m.patterns["LIVE"].member_loads[-1]
    assert (ml.kind, ml.w, ml.a, ml.direction) == ("point", 4.0, 0.5,
                                                   "gravity")
    assert m.patterns["DEAD"].area_loads[0].q == 3.0


def test_supports_springs_links_and_mass_source(d):
    sp = rows_of(d, "supports")
    sp[0]["rx"] = False
    sp.append({"x": 12, "y": 0, "z": 0, "ux": True, "uy": "yes", "uz": 1})
    ps = rows_of(d, "point_springs")
    ps[0]["kz"] = 2.0e4
    lk = rows_of(d, "links")
    lk[0]["kx"] = 5.0e4
    ms = [{"pattern": "DEAD", "factor": 1.0}, {"pattern": "LIVE",
                                               "factor": 0.25}]
    mo = rows_of(d, "mass_source_options")
    mo[0]["include_vertical"] = True
    m, errs, _ = apply(d, supports=sp, point_springs=ps, links=lk,
                       mass_source=ms, mass_source_options=mo)
    assert errs == []
    assert m.supports[0].restraints == (1, 1, 1, 0, 1, 1)
    assert m.supports[1].restraints == (1, 1, 1, 0, 0, 0)
    assert m.spring_supports[0].stiffness[2] == 2.0e4
    assert m.links[0].stiffness[0] == 5.0e4
    assert m.mass_source == {"DEAD": 1.0, "LIVE": 0.25}
    assert m.mass_options["include_vertical"] is True


def test_auto_uid_new_frame_and_release_columns(d):
    z = d["stories"][0]["elevation"]
    rows = rows_of(d, "frame_objects") + [
        {"uid": "", "kind": "beam", "section": "BEAM", "ix": 0, "iy": 0,
         "iz": z, "jx": 6, "jy": 6, "jz": z, "release_i": True,
         "release_j": True}]
    m, errs, summary = apply(d, frame_objects=rows)
    assert errs == []
    new = m.members[-1]
    assert new.uid == "F1" and new.releases == "Mi,Mj"
    assert new.length == pytest.approx(6 * 2 ** 0.5)
    assert summary["frame_objects"]["added"] == 1


def test_groups_and_diaphragm_tables(d):
    ga = rows_of(d, "group_assignments") + [
        {"group": "G1", "type": "frame", "object": d["members"][3]["uid"]}]
    dd = rows_of(d, "diaphragm_definitions")
    dd[0]["name"] = "DX"
    m, errs, _ = apply(d, group_assignments=ga, diaphragm_definitions=dd)
    assert errs == []
    assert m.groups["G1"]["members"][-1] == d["members"][3]["uid"]
    assert m.groups["G1"]["points"] == [[0.0, 0.0, 0.0]]
    assert set(m.diaphragms) == {"DX"}
    assert m.joint_diaphragms[0]["diaphragm"] == "DX"
    sd = rows_of(d, "story_diaphragms")
    sd[1]["diaphragm"] = "default"
    sd[0]["diaphragm"] = "none"
    m, errs, _ = apply(d, story_diaphragms=sd)
    assert errs == [] and m.story_diaphragm == {d["stories"][0]["name"]:
                                               "none"}


# --------------------------------------------------------------------------- #
# errors: precise, atomic
# --------------------------------------------------------------------------- #
def test_type_errors_are_per_cell_and_nothing_applies(d):
    rows = rows_of(d, "frame_objects")
    rows[2]["angle"] = "abc"
    rows[5]["kind"] = "girder"
    rows[7]["section"] = "NOPE"
    rows[9]["ix"] = None
    secs = rows_of(d, "frame_sections")
    secs[0]["mod_A"] = 2.0                 # valid edit in another table
    m, errs, _ = apply(d, frame_objects=rows, frame_sections=secs)
    assert m is None
    got = {(e["table"], e["row"], e["col"]) for e in errs}
    assert got == {("frame_objects", 2, "angle"),
                   ("frame_objects", 5, "kind"),
                   ("frame_objects", 7, "section"),
                   ("frame_objects", 9, "ix")}
    msg = {e["col"]: e["message"] for e in errs}
    assert "'abc' is not a number" in msg["angle"]
    assert "'NOPE'" in msg["section"]


def test_duplicate_key_and_required_columns(d):
    rows = rows_of(d, "frame_objects")
    rows[1]["uid"] = rows[0]["uid"]
    m, errs, _ = apply(d, frame_objects=rows)
    assert m is None and (errs[0]["row"], errs[0]["col"]) == (1, "uid")
    rows = rows_of(d, "frame_objects") + [{"uid": "NEW", "kind": "beam",
                                           "section": "BEAM"}]
    m, errs, _ = apply(d, frame_objects=rows)
    n = len(rows) - 1
    assert m is None
    assert {(e["row"], e["col"]) for e in errs} == {
        (n, c) for c in ("ix", "iy", "iz", "jx", "jy", "jz")}


def test_model_validation_error_is_attributed_to_the_row(d):
    rows = rows_of(d, "frame_objects")
    r = rows[4]
    r["jx"], r["jy"], r["jz"] = r["ix"], r["iy"], r["iz"]   # zero length
    m, errs, _ = apply(d, frame_objects=rows)
    assert m is None and len(errs) == 1
    e = errs[0]
    assert (e["table"], e["row"], e["col"]) == ("frame_objects", 4, "uid")
    assert "zero length" in e["message"]


def test_bad_load_reference_and_deleting_used_definitions(d):
    rows = rows_of(d, "frame_point_loads")
    rows[0]["member_uid"] = "GHOST"
    m, errs, _ = apply(d, frame_point_loads=rows)
    assert m is None and (errs[0]["row"], errs[0]["col"]) == (0, "member_uid")
    assert "unknown frame 'GHOST'" in errs[0]["message"]
    m, errs, _ = apply(d, frame_sections=[r for r in rows_of(
        d, "frame_sections") if r["name"] != "BEAM"])
    assert m is None and "is used by frame" in errs[0]["message"]
    m, errs, _ = apply(d, load_patterns=[r for r in rows_of(
        d, "load_patterns") if r["name"] != "LIVE"])
    assert m is None and "LIVE" in errs[0]["message"]


def test_read_only_tables_and_unknown_table(d):
    m, errs, _ = apply(d, joints=rows_of(d, "joints"))
    assert m is None and "read only" in errs[0]["message"]
    m, errs, _ = apply(d, nope=[])
    assert m is None and errs[0]["message"] == "unknown table 'nope'"
    joints = MT.get_table(d, "joints")["rows"]
    pts = {tuple(round(v, 6) for v in p) for mm in d["members"]
           for p in (mm["pi"], mm["pj"])}
    assert len(joints) >= len(pts)
    assert all(not c["editable"] for c in MT.get_table(d, "joints")["columns"])


def test_parse_cell_coercions():
    c = MT.Col("v", "V", "number", "force")
    assert MT.parse_cell(c, " 2.5 ", {}) == 2.5
    with pytest.raises(MT.RowError):
        MT.parse_cell(c, True, {})
    with pytest.raises(MT.RowError):
        MT.parse_cell(c, float("nan"), {})
    b = MT.Col("b", "B", "bool")
    assert [MT.parse_cell(b, v, {}) for v in ("Yes", "0", 1, False, "")] == \
        [True, False, True, False, False]
    p = MT.Col("p", "P", "points", "length")
    assert MT.parse_cell(p, "0,0,1; 2 3 4", {}) == [[0.0, 0.0, 1.0],
                                                     [2.0, 3.0, 4.0]]
    with pytest.raises(MT.RowError):
        MT.parse_cell(p, "1,2", {})
    i = MT.Col("n", "N", "int")
    with pytest.raises(MT.RowError):
        MT.parse_cell(i, 2.5, {})


# --------------------------------------------------------------------------- #
# engine: a table edit reaches the analysis (closed form)
# --------------------------------------------------------------------------- #
def test_section_edit_drives_cantilever_closed_form():
    eng = pytest.importorskip("skyframe.engine.opensees_engine")
    E, L, P = 25_000_000.0, 3.0, 10.0
    mdl = BuildingModel(name="Cant")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E, nu=0.2))
    mdl.set_stories([L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("P", "other").nodal_loads.append(NodalLoad((0, 0, L), fx=P))
    mdl.add_case("P", {"P": 1.0})
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=1.0, my=1.0))
    mdl.num_modes = 1
    dd = mdl.to_dict()
    rows = rows_of(dd, "frame_sections")
    rows[0]["b"] = rows[0]["h"] = 0.5               # 0.4 -> 0.5 square
    m, errs, _ = MT.apply_tables(dd, {"frame_sections": rows})
    assert errs == []
    res = eng.OpenSeesEngine(m).run().to_dict()
    tip = next(t for t, xyz in res["nodes"].items()
               if all(abs(a - b) < 1e-9 for a, b in zip(xyz, (0, 0, L))))
    I = 0.5 ** 4 / 12.0
    assert res["cases"]["P"]["node_disp"][tip][0] == pytest.approx(
        P * L ** 3 / (3 * E * I), rel=1e-3)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    from skyframe.api.server import create_app
    return create_app().test_client()


def test_http_list_get_apply_and_errors(client, base):
    r = client.post("/api/modeltables/list", json={"model": base})
    assert r.status_code == 200
    assert {t["key"] for t in r.get_json()["tables"]} >= REQUIRED
    assert r.get_json()["quantities"]["modulus"] == "kPa"
    t = client.post("/api/modeltables/frame_sections",
                    json={"model": base}).get_json()
    assert [c["key"] for c in t["columns"]][:2] == ["name", "material"]
    rows = t["rows"]
    bi = next(i for i, r in enumerate(rows) if r["name"] == "BEAM")
    rows[bi]["h"] = 0.7
    r = client.post("/api/modeltables/frame_sections/apply",
                    json={"model": base, "rows": rows})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["model"]["sections"]["BEAM"]["h"] == 0.7
    # the server state was replaced
    assert client.get("/api/model").get_json()["sections"]["BEAM"]["h"] == 0.7
    rows[bi]["h"] = "tall"
    r = client.post("/api/modeltables/frame_sections/apply",
                    json={"rows": rows})
    assert r.status_code == 400
    e = r.get_json()["errors"][0]
    assert (e["table"], e["row"], e["col"]) == ("frame_sections", bi, "h")
    assert client.get("/api/model").get_json()["sections"]["BEAM"]["h"] == 0.7
    r = client.post("/api/modeltables/frame_sections/apply",
                    json={"rows": rows, "soft_errors": True})
    assert r.status_code == 200 and r.get_json()["ok"] is False
    assert r.get_json()["errors"][0]["col"] == "h"
    assert client.post("/api/modeltables/nope").status_code == 404


def test_http_apply_many_dry_run_csv_and_zip(client, base):
    cases = client.post("/api/modeltables/static_cases",
                        json={"model": base}).get_json()["rows"]
    loads = client.post("/api/modeltables/static_case_loads",
                        json={"model": base}).get_json()["rows"]
    r = client.post("/api/modeltables/apply", json={
        "model": base, "dry_run": True,
        "tables": {"static_cases": cases + [{"name": "X1"}],
                   "static_case_loads": loads + [
                       {"case": "X1", "pattern": "DEAD", "factor": 2}]}})
    assert r.status_code == 200
    assert r.get_json()["model"]["cases"]["X1"]["patterns"] == {"DEAD": 2.0}
    assert "X1" not in client.get("/api/model").get_json()["cases"]
    c = client.post("/api/modeltables/materials/csv",
                    json={"model": base}).get_json()
    assert c["filename"] == "materials.csv" and c["csv"].startswith("_id,")
    r = client.post("/api/modeltables/materials/csv_import",
                    json={"model": base, "csv": c["csv"]})
    assert r.get_json()["rows"][0]["name"] == "CONC"
    r = client.post("/api/modeltables/materials/csv_import",
                    json={"model": base, "csv": c["csv"], "apply": True})
    assert r.status_code == 200 and JS(r.get_json()["model"]) == JS(base)
    z = client.post("/api/modeltables/export", json={"model": base})
    assert z.mimetype == "application/zip"
    client.post("/api/model", json=base)
    r = client.post("/api/modeltables/import", data=z.data,
                    content_type="application/zip")
    assert r.status_code == 200 and JS(r.get_json()["model"]) == JS(base)
