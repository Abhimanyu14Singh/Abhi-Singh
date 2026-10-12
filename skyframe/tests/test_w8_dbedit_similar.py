"""W8: master / similar stories + Interactive Database Editing coverage for
the engine features that had no table (CONTRACT "Master / similar stories,
selection and snapping").

Pinned behaviour:
  * Story.master / similar_to: absent keys by default (byte-identical
    legacy JSON), exact round trip, validation, similar groups and the
    One / Similar / All scope; drawing on one story of a 5-story model with
    every story similar to the master creates 5 members with the right
    story tags and elevations;
  * every new / extended model table round-trips byte-identically (one by
    one, all at once, CSV and zip) on a model that uses every feature;
  * edits write exactly the model keys and remove them again at the
    default; renames cascade; deletes of used definitions are refused;
  * edits made through the tables drive the engine to closed forms: a
    one-way membrane slab (w = q L / 2, M = w L^2 / 8), a 3-point arc
    (arc length R * sweep) and a uniform load set (base FZ = q A).
"""

import copy
import json
import math

import numpy as np
import pytest

from _v02_utils import (          # noqa: E402
    model_mod, FrameSection, PointSupport, new_model, finish, run,
    stations)
from w8_fixtures import w8_model
from skyframe.core import modeltables as MT
from skyframe.core import modeltables_ext as MX
from skyframe.core import similar_stories as SS
from skyframe.core.builder import quick_building
from skyframe.core.model import BuildingModel

J = lambda d: json.dumps(d)                                   # noqa: E731

NEW_TABLES = ["spring_properties", "hinge_properties", "hinge_backbones",
              "rs_functions", "rs_function_points", "th_functions",
              "th_function_values", "frequency_functions",
              "frequency_function_points", "story_similarity",
              "link_properties", "frame_curves", "shell_load_sets",
              "tendons", "nonlinear_static_cases", "nonlinear_static_loads",
              "steady_state_cases", "steady_state_loads", "psd_cases",
              "psd_loads"]
EXTENDED = ["shell_sections", "frame_objects", "shell_objects"]


@pytest.fixture(scope="module")
def base():
    return w8_model().to_dict()


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


# --------------------------------------------------------------------------- #
# 1. master / similar stories
# --------------------------------------------------------------------------- #
def five_story(similar=True):
    m = quick_building(bays_x=1, bays_y=1, stories=5)
    if similar:
        m.stories[0].master = True
        for s in m.stories[1:]:
            s.similar_to = m.stories[0].name
    m.validate()
    return m


def test_story_fields_default_absent_and_round_trip():
    q = quick_building(stories=2)
    for sd in q.to_dict()["stories"]:
        assert "master" not in sd and "similar_to" not in sd
    m = five_story()
    d = m.to_dict()
    assert d["stories"][0]["master"] is True
    assert "similar_to" not in d["stories"][0]
    assert all(s["similar_to"] == "Story1" for s in d["stories"][1:])
    assert all("master" not in s for s in d["stories"][1:])
    assert J(BuildingModel.from_dict(d).to_dict()) == J(d)


@pytest.mark.parametrize("patch,msg", [
    (lambda s: s[1].__setattr__("similar_to", "Nope"), "unknown story"),
    (lambda s: s[2].__setattr__("similar_to", "Story2"), "not a master"),
    (lambda s: s[0].__setattr__("similar_to", "Story2"), "master story"),
    (lambda s: s[3].__setattr__("similar_to", "Story4"), "names itself"),
])
def test_story_similarity_validation(patch, msg):
    m = quick_building(bays_x=1, bays_y=1, stories=4)
    m.stories[0].master = True
    m.stories[1].similar_to = "Story1"
    m.validate()
    patch(m.stories)
    with pytest.raises(ValueError, match=msg):
        m.validate()
    d = quick_building(stories=1).to_dict()
    d["stories"][0]["master"] = "yes"
    with pytest.raises(ValueError, match="true or false"):
        BuildingModel.from_dict(d)


def test_similar_groups_and_scope():
    m = quick_building(bays_x=1, bays_y=1, stories=6)
    s = [st.name for st in m.stories]
    m.stories[0].master = True
    m.stories[3].master = True
    for k in (1, 2):
        m.stories[k].similar_to = s[0]
    m.stories[4].similar_to = s[3]          # Story6 independent
    m.validate()
    assert SS.similar_group(m, s[1]) == s[:3]
    assert SS.similar_group(m, s[0]) == s[:3]
    assert SS.similar_group(m, s[4]) == [s[3], s[4]]
    assert SS.similar_group(m, s[5]) == [s[5]]
    assert SS.scope_stories(m, s[2], "one") == [s[2]]
    assert SS.scope_stories(m, s[2], "all") == s
    assert SS.groups(m) == {s[0]: s[:3], s[3]: s[3:5]}
    with pytest.raises(ValueError):
        SS.scope_stories(m, s[0], "some")


def test_similar_scope_draw_on_five_stories_creates_five_members():
    m = five_story()
    st3 = m.stories[2]
    sec = m.members[-1].section
    b = m.add_member("beam", sec, (0, 0, st3.elevation), (3, 2, st3.elevation),
                     story=st3.name, uid="NEWB")
    new = SS.replicate_members(m, [b.uid], "similar")
    assert len(new) == 4
    got = sorted((mm.story, mm.pi[2], mm.pj[2]) for mm in m.members
                 if (mm.pi[0], mm.pi[1], mm.pj[0], mm.pj[1]) == (0, 0, 3, 2))
    assert got == sorted((s.name, s.elevation, s.elevation)
                         for s in m.stories)
    assert len(got) == 5
    # idempotent: replicating again adds nothing
    assert SS.replicate_members(m, [b.uid], "similar") == []
    # One-story scope never copies; an independent story copies to itself only
    m2 = five_story(similar=False)
    b2 = m2.add_member("beam", sec, (0, 0, m2.stories[0].elevation),
                       (3, 2, m2.stories[0].elevation),
                       story=m2.stories[0].name, uid="NB")
    assert SS.replicate_members(m2, [b2.uid], "similar") == []
    assert len(SS.replicate_members(m2, [b2.uid], "all")) == 4
    m2.validate()


def test_story_similarity_table_and_cascades():
    m = five_story(similar=False)
    d = m.to_dict()
    rows = rows_of(d, "story_similarity")
    assert [r["master"] for r in rows] == [False] * 5
    rows[0]["master"] = True
    for r in rows[1:4]:
        r["similar_to"] = "Story1"
    mm, errs, _ = apply(d, story_similarity=rows)
    assert errs == []
    assert SS.similar_group(mm, "Story3") == ["Story1", "Story2", "Story3",
                                              "Story4"]
    dd = mm.to_dict()
    # rename the master in the Story table: similar_to follows
    srows = rows_of(dd, "stories")
    srows[0]["name"] = "L1"
    m2, errs, _ = apply(dd, stories=srows)
    assert errs == []
    assert [s.similar_to for s in m2.stories] == ["", "L1", "L1", "L1", ""]
    # delete the master story: references are cleared, not left dangling
    m3, errs, _ = apply(dd, stories=srows[1:])
    assert errs == [] and all(s.similar_to == "" for s in m3.stories)
    # back to defaults: the keys disappear again (byte-identical)
    rows = rows_of(dd, "story_similarity")
    for r in rows:
        r["master"], r["similar_to"] = False, ""
    m4, errs, _ = apply(dd, story_similarity=rows)
    assert errs == [] and J(m4.to_dict()) == J(d)
    # bad similar_to -> error on the cell; nothing applied
    rows[2]["similar_to"] = "Story2"
    m5, errs, _ = apply(d, story_similarity=rows)
    assert m5 is None and "not a master" in errs[0]["message"]


# --------------------------------------------------------------------------- #
# 2. round trips of every new / extended table
# --------------------------------------------------------------------------- #
def test_catalogue_has_the_new_tables(base):
    keys = MT.table_keys()
    assert set(NEW_TABLES) <= set(keys)
    assert len(keys) == len(set(keys))
    for k in NEW_TABLES:
        t = MT.get_table(base, k)
        assert t["editable"] and t["group"].startswith("Model Definition")
        assert t["rows"], k                       # the fixture uses each one
    cols = {c["key"] for c in MT.get_table(base, "shell_sections")["columns"]}
    assert "shell_type" in cols
    cols = {c["key"] for c in MT.get_table(base, "frame_objects")["columns"]}
    assert "curve" in cols
    cols = {c["key"] for c in MT.get_table(base, "shell_objects")["columns"]}
    assert {"mesh_n1", "mesh_n2", "mesh_max", "distribution", "one_way_dir",
            "load_set"} <= cols


@pytest.mark.parametrize("key", NEW_TABLES + EXTENDED)
def test_round_trip_each_new_table(d, key):
    m, errs, _ = apply(d, **{key: rows_of(d, key)})
    assert errs == []
    assert J(m.to_dict()) == J(d)
    text = MT.table_to_csv(d, key)
    m, errs, _ = apply(d, **{key: MT.csv_to_rows(d, key, text)})
    assert errs == [] and J(m.to_dict()) == J(d)
    assert MT.table_to_csv(m.to_dict(), key) == text


def test_round_trip_all_tables_and_zip(d):
    editable = [t.key for t in MT.catalogue() if t.editable]
    m, errs, _ = apply(d, **{k: rows_of(d, k) for k in editable})
    assert errs == [] and J(m.to_dict()) == J(d)
    m, errs, _ = MT.import_zip(d, MT.export_zip(d))
    assert errs == [] and J(m.to_dict()) == J(d)
    for mdl in (quick_building(), BuildingModel()):
        dd = mdl.to_dict()
        m, errs, _ = MT.apply_tables(dd, {k: rows_of(dd, k)
                                          for k in editable})
        assert errs == [] and J(m.to_dict()) == J(dd)


# --------------------------------------------------------------------------- #
# 3. edits
# --------------------------------------------------------------------------- #
def test_shell_type_column_set_and_reset(d):
    rows = rows_of(d, "shell_sections")
    deck = next(r for r in rows if r["name"] == "DECK")
    assert deck["shell_type"] == "shell"
    deck["shell_type"] = "plate_thin"
    m, errs, _ = apply(d, shell_sections=rows)
    assert errs == [] and m.shell_sections["DECK"].shell_type == "plate_thin"
    dd = m.to_dict()
    assert dd["shell_sections"]["DECK"]["shell_type"] == "plate_thin"
    rows = rows_of(dd, "shell_sections")
    next(r for r in rows if r["name"] == "DECK")["shell_type"] = "shell"
    m, errs, _ = apply(dd, shell_sections=rows)
    assert errs == [] and J(m.to_dict()) == J(d)
    rows[0]["shell_type"] = "thin"
    m, errs, _ = apply(dd, shell_sections=rows)
    assert m is None and errs[0]["col"] == "shell_type"


def test_shell_object_mesh_distribution_and_load_set_columns(d):
    rows = rows_of(d, "shell_objects")
    w1 = next(r for r in rows if r["uid"] == "W1")
    assert (w1["mesh_n1"], w1["mesh_n2"], w1["mesh_max"]) == (4, 3, None)
    s2 = next(r for r in rows if r["uid"] == "S2")
    assert (s2["mesh_max"], s2["load_set"]) == (1.5, "OFFICE")
    s1 = next(r for r in rows if r["uid"] == "S1")
    assert (s1["distribution"], s1["one_way_dir"]) == ("one_way", 2)
    w1["mesh_n1"], w1["mesh_n2"] = 6, 5
    s2["mesh_max"], s2["mesh_n1"], s2["mesh_n2"] = None, 2, 2
    s2["load_set"] = ""
    s1["one_way_dir"] = 1
    m, errs, _ = apply(d, shell_objects=rows)
    assert errs == []
    by = {r.uid: r for r in m.shells}
    assert by["W1"].mesh_divisions == {"n1": 6, "n2": 5}
    assert by["S2"].mesh_divisions == {"n1": 2, "n2": 2}
    assert by["S2"].load_set == "" and by["S1"].one_way_dir == 1
    out = {r["uid"]: r for r in m.to_dict()["shells"]}
    assert "load_set" not in out["S2"] and "one_way_dir" not in out["S1"]
    # clearing every mesh cell removes the key; an incomplete n1 is refused
    rows = rows_of(m.to_dict(), "shell_objects")
    w = next(r for r in rows if r["uid"] == "W1")
    w["mesh_n1"] = w["mesh_n2"] = None
    m2, errs, _ = apply(m.to_dict(), shell_objects=rows)
    assert errs == [] and "mesh_divisions" not in \
        {r["uid"]: r for r in m2.to_dict()["shells"]}["W1"]
    w["mesh_n1"] = 3
    m3, errs, _ = apply(m.to_dict(), shell_objects=rows)
    assert m3 is None and "mesh_divisions" in errs[0]["message"]


def test_curve_text_column_and_curve_table(d):
    assert MX.curve_text({"type": "arc", "via": [-1.5, 3.0, 3.2],
                          "segments": 8, "local2": "in_plane"}) == \
        "via -1.5,3,3.2 segments 8"
    c = MX.parse_curve_text("center 0,0,0 normal 0,0,1 segments auto "
                            "local2 normal")
    assert c == {"type": "arc", "center": [0.0, 0.0, 0.0],
                 "plane_normal": [0.0, 0.0, 1.0], "segments": None,
                 "local2": "normal"}
    for bad in ("via 1,2", "via 1,2,3 center 0,0,0", "radius 3",
                "via 0,0,0 segments x"):
        with pytest.raises(ValueError):
            MX.parse_curve_text(bad)
    rows = rows_of(d, "frame_objects")
    arc = next(r for r in rows if r["uid"] == "ARC")
    assert arc["curve"] == "via -1.5,3,3.2 segments 8"
    straight = next(r for r in rows if r["uid"] != "ARC"
                    and r["kind"] == "beam")
    assert straight["curve"] == ""
    arc["curve"] = ""                              # make it straight
    m, errs, _ = apply(d, frame_objects=rows)
    assert errs == [] and m.members[[x.uid for x in m.members]
                                    .index("ARC")].curve is None
    arc["curve"] = "via 1,2"                         # bad text
    m, errs, _ = apply(d, frame_objects=rows)
    assert m is None and errs[0]["col"] == "curve"
    # the curve table: edit segments / add a curve / drop one
    crow = rows_of(d, "frame_curves")
    assert crow[0]["member"] == "ARC" and crow[0]["segments"] == 8
    crow[0]["segments"] = None
    m, errs, _ = apply(d, frame_curves=crow)
    assert errs == []
    assert {mm.uid: mm.curve for mm in m.members}["ARC"]["segments"] is None
    m, errs, _ = apply(d, frame_curves=[])
    assert errs == [] and all(mm.curve is None for mm in m.members)
    m, errs, _ = apply(d, frame_curves=[{"member": "ARC", "form": "center",
                                         "px": 0, "py": 3, "pz": 3.2}])
    assert m is None and errs[0]["col"] == "nx"


def test_load_set_table_edit_rename_pattern_and_refs(d):
    rows = rows_of(d, "shell_load_sets")
    rows[1]["direction"] = "gravity"
    rows.append({"set": "ROOF", "pattern": "LIVE", "q": 1.0})
    m, errs, _ = apply(d, shell_load_sets=rows)
    assert errs == []
    assert m.shell_load_sets == {"OFFICE": {"DEAD": {"q": 1.5},
                                            "LIVE": {"q": 2.4}},
                                 "ROOF": {"LIVE": {"q": 1.0}}}
    # renaming a pattern cascades into the sets, NLS loads, tendons
    prow = rows_of(d, "load_patterns")
    next(r for r in prow if r["name"] == "DEAD")["name"] = "DL"
    m, errs, _ = apply(d, load_patterns=prow)
    assert errs == []
    assert "DL" in m.shell_load_sets["OFFICE"]
    assert m.nonlinear_static_cases["NLS1"].loads[0]["pattern"] == "DL"
    assert m.tendons[0]["pattern"] == "DL"
    # deleting a pattern still used by a set is refused
    prow = rows_of(d, "load_patterns") + [{"name": "SDL", "kind": "dead"}]
    m, errs, _ = apply(d, load_patterns=prow, shell_load_sets=rows_of(
        d, "shell_load_sets") + [{"set": "ROOF", "pattern": "SDL", "q": 1}])
    assert errs == []
    dd = m.to_dict()
    prow = [r for r in rows_of(dd, "load_patterns") if r["name"] != "SDL"]
    m, errs, _ = apply(dd, load_patterns=prow)
    assert m is None and "shell load set" in errs[0]["message"]


def test_spring_hinge_property_tables(d):
    rows = rows_of(d, "spring_properties")
    rows[0]["name"] = "SOIL"
    rows[0]["k3"] = 60000.0
    rows[0]["angle_deg"] = 30.0
    m, errs, _ = apply(d, spring_properties=rows)
    assert errs == []
    assert m.spring_supports[-1].property == "SOIL"
    sp = m.to_dict()["spring_properties"]["SOIL"]
    assert sp["k"][2] == 60000.0 and sp["local_axes"] == {"angle_deg": 30.0}
    m, errs, _ = apply(d, spring_properties=[])
    assert m is None and "spring property" in errs[0]["message"]
    # asymmetric backbone through the point table
    bb = rows_of(d, "hinge_backbones")
    pos = [dict(r, side="positive") for r in bb]
    neg = [{"hinge": "HM3", "side": "negative", "d": r["d"],
            "f": r["f"] * 0.5} for r in bb]
    hp = rows_of(d, "hinge_properties")
    hp[0]["hysteresis"] = "takeda"          # kinematic needs symmetric
    m, errs, _ = apply(d, hinge_properties=hp, hinge_backbones=pos + neg)
    assert errs == []
    b = m.hinge_properties["HM3"]["backbone"]
    assert isinstance(b, dict) and b["negative"][1] == [0.0, 60.0]
    m, errs, _ = apply(d, hinge_backbones=bb + neg)
    assert m is None and "symmetric" in errs[0]["message"]
    hrow = rows_of(d, "hinge_properties")
    assert hrow[0]["IO"] == 0.005 and hrow[0]["yield_deformation"] == ""
    hrow[0]["yield_deformation"] = "auto"
    hrow[0]["CP"] = None
    m, errs, _ = apply(d, hinge_properties=hrow)
    assert errs == []
    h = m.hinge_properties["HM3"]
    assert h["scale"]["yield_deformation"] == "auto"
    assert "CP" not in (h.get("acceptance") or {})


def test_function_and_case_tables_add_in_one_apply(d):
    fn = rows_of(d, "rs_functions") + [{"name": "SP2", "damping": 0.05}]
    pts = rows_of(d, "rs_function_points") + [
        {"function": "SP2", "period": 0.0, "value": 0.4},
        {"function": "SP2", "period": 2.0, "value": 0.2}]
    nls = rows_of(d, "nonlinear_static_cases") + [
        {"name": "NLS2", "steps": 3, "start_from": "NLS1"}]
    nll = rows_of(d, "nonlinear_static_loads") + [
        {"case": "NLS2", "pattern": "LIVE", "scale": 1.0}]
    ssl = rows_of(d, "steady_state_loads")
    ssl[0]["phase_deg"] = 45.0
    m, errs, _ = apply(d, rs_functions=fn, rs_function_points=pts,
                       nonlinear_static_cases=nls,
                       nonlinear_static_loads=nll, steady_state_loads=ssl)
    assert errs == []
    assert m.spectrum_functions["SP2"].points == [[0.0, 0.4], [2.0, 0.2]]
    c = m.nonlinear_static_cases["NLS2"]
    assert c.start_from == "NLS1" and c.steps == 3
    assert [(ld["pattern"], ld["scale"]) for ld in c.loads] == [("LIVE", 1.0)]
    assert m.steady_state_cases["SS1"].loads[0].phase_deg == 45.0
    # TH values: the time column is derived from dt
    tv = rows_of(d, "th_function_values")
    assert [r["t"] for r in tv] == pytest.approx([0, 0.01, 0.02, 0.03, 0.04])
    tv[2]["value"] = -0.25
    m, errs, _ = apply(d, th_function_values=tv)
    assert errs == [] and m.th_functions["REC"].values[2] == -0.25
    # unknown parent and a function rename cascade
    m, errs, _ = apply(d, rs_function_points=[{"function": "NOPE",
                                               "period": 0, "value": 1}])
    assert m is None and errs[0]["col"] == "function"
    fr = rows_of(d, "frequency_functions")
    fr[0]["name"] = "FSS2"
    m, errs, _ = apply(d, frequency_functions=fr)
    assert errs == [] and m.steady_state_cases["SS1"].loads[0].function == \
        "FSS2"


def test_link_params_and_tendons(d):
    lp = rows_of(d, "link_properties")
    assert {(r["param"], r["value"]) for r in lp} == {("cd", 250.0),
                                                       ("alpha", 0.5)}
    next(r for r in lp if r["param"] == "alpha")["value"] = 0.35
    m, errs, _ = apply(d, link_properties=lp)
    assert errs == []
    assert m.links[-1].params == {"cd": 250.0, "alpha": 0.35}
    lp.append(dict(lp[0]))
    m, errs, _ = apply(d, link_properties=lp)
    assert m is None and "duplicate" in errs[0]["message"]
    tr = rows_of(d, "tendons")
    assert tr[0]["sags"] == "0.2" and tr[0]["host"] == "BY3-C1"
    tr[0]["sags"] = "0.25"
    tr[0]["friction_mu"] = 0.2
    m, errs, _ = apply(d, tendons=tr)
    assert errs == []
    t = m.tendons[0]
    assert t["sags"] == [0.25] and t["losses"]["friction_mu"] == 0.2
    # renaming the host frame follows into the tendon
    fr = rows_of(d, "frame_objects")
    next(r for r in fr if r["uid"] == "BY3-C1")["uid"] = "HOSTB"
    m, errs, _ = apply(d, frame_objects=fr)
    assert errs == [] and m.tendons[0]["host"] == ["HOSTB"]


# --------------------------------------------------------------------------- #
# 4. closed forms through table edits
# --------------------------------------------------------------------------- #
def _four_beam_membrane_slab(q):
    mdl = new_model("w8-oneway", [3.0])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.3, 0.5))
    cpts = [(0, 0), (8, 0), (8, 4), (0, 4)]
    for i, (x, y) in enumerate(cpts):
        mdl.add_member("column", "COL", (x, y, 0), (x, y, 3.0),
                       story="Story1", uid=f"C{i+1}")
        mdl.supports.append(PointSupport((x, y, 0), (1, 1, 1, 1, 1, 1)))
    names = ["XB1", "YB1", "XB2", "YB2"]
    for i in range(4):
        (x1, y1), (x2, y2) = cpts[i], cpts[(i + 1) % 4]
        mm = mdl.add_member("beam", "BM", (x1, y1, 3.0), (x2, y2, 3.0),
                            story="Story1", uid=names[i])
        mm.releases = "Mi,Mj"
    mdl.add_shell("slab", "membrane", "", [(0, 0, 3.0), (8, 0, 3.0),
                                           (8, 4, 3.0), (0, 4, 3.0)],
                  story="Story1", uid="S1")
    mdl.pattern("Q", "dead").area_loads.append(
        model_mod.AreaLoad(region_uid="S1", q=q))
    mdl.add_case("Q", {"Q": 1.0})
    finish(mdl, (0, 0, 3.0))
    return mdl


def test_one_way_set_through_shell_table_closed_form():
    """8 x 4 m membrane slab, q = 5 kPa, simply supported edge beams.
    The table sets one-way along local 1 (X): YB1 / YB2 (L = 4) carry
    w = q 8 / 2 = 20 kN/m -> M_mid = w L^2 / 8 = 40 kN m; XB beams carry
    nothing; base FZ = q A = 160 kN."""
    q = 5.0
    d0 = _four_beam_membrane_slab(q).to_dict()
    rows = rows_of(d0, "shell_objects")
    rows[0]["distribution"], rows[0]["one_way_dir"] = "one_way", 1
    m, errs, _ = MT.apply_tables(d0, {"shell_objects": rows})
    assert errs == []
    res = run(m)
    case = res["cases"]["Q"]
    assert case["base"]["FZ"] == pytest.approx(q * 32.0, rel=1e-9)
    w, L = q * 8 / 2, 4.0
    for uid in ("YB1", "YB2"):
        s = stations(res, "Q", uid)
        assert abs(np.asarray(s["M3"], float)[5]) == pytest.approx(
            w * L * L / 8, rel=1e-6)
    for uid in ("XB1", "XB2"):
        assert np.max(np.abs(stations(res, "Q", uid)["M3"])) < 1e-6


def test_curve_set_through_table_gives_exact_arc_and_load_set_total():
    """A beam made a semicircle through the curve column: the analysed arc
    has radius R = 3 and length pi R; a load set with q on a 6 x 6 slab
    gives base FZ = q A exactly (the expanded area load)."""
    m0 = quick_building(bays_x=1, bays_y=1, stories=1)
    z = m0.stories[0].elevation
    from skyframe.core.model import ShellSection
    m0.add_shell_section(ShellSection("SL", "CONC", 0.2))
    m0.add_shell("slab", "shell", "SL", [(0, 0, z), (6, 0, z), (6, 6, z),
                                          (0, 6, z)], mesh_size=3.0,
                 story=m0.stories[0].name, uid="S1")
    m0.pattern("OCCP", "live")
    m0.add_case("OCCP", {"OCCP": 1.0})
    d0 = m0.to_dict()
    beam = next(r for r in rows_of(d0, "frame_objects")
                if r["kind"] == "beam" and r["iy"] == r["jy"] == 0)
    rows = rows_of(d0, "frame_objects")
    rr = next(r for r in rows if r["uid"] == beam["uid"])
    rr["curve"] = f"via 3,-3,{MX.fmt_num(z)} segments 36"
    ls = [{"set": "OCC", "pattern": "OCCP", "q": 2.0}]
    so = rows_of(d0, "shell_objects")
    so[0]["load_set"] = "OCC"
    m, errs, _ = MT.apply_tables(d0, {"frame_objects": rows,
                                      "shell_load_sets": ls,
                                      "shell_objects": so})
    assert errs == [], errs
    from skyframe.engine.opensees_engine import OpenSeesEngine
    res = OpenSeesEngine(m).run().to_dict()
    cf = res["curved_frames"][beam["uid"]]
    assert cf["radius"] == pytest.approx(3.0, rel=1e-12)
    assert cf["arc_length"] == pytest.approx(math.pi * 3.0, rel=1e-12)
    assert cf["segments"] == 36
    live = res["cases"]["OCCP"]["base"]["FZ"]
    assert live == pytest.approx(2.0 * 36.0, rel=1e-9)
