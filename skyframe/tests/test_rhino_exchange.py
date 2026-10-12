"""Rhino / Grasshopper exchange (``skyframe.exchange/1``): validation,
units, role resolution, conversion, stories, merge / replace, repair
connectivity, results keyed by source id, API (GH-style JSON payloads, as
the C# plugin sends them) and byte identity when the exchange is unused.
"""

import copy
import json
import math

import pytest

from skyframe.core import rhino_exchange as X
from skyframe.core.model import (AreaLoad, BuildingModel, LoadCase,
                                 LoadPattern, MemberLoad, NodalLoad,
                                 FrameSection, Material)

H = 3.0
L = 6.0


# --------------------------------------------------------------------------- #
# payload helpers (Grasshopper style: kind / role / props explicit)
# --------------------------------------------------------------------------- #
def ex(objs, session="GH-def-1", units="m", tol=0.001, app="grasshopper"):
    return {"schema": "skyframe.exchange/1",
            "source": {"app": app, "doc": "test.gh", "units": units,
                       "tolerance": tol},
            "session": session,
            "layers": [{"path": "SF::Frames", "color": "#FF0000"}],
            "objects": objs}


def crv(oid, a, b, role=None, layer="SF::Frames", props=None, attrs=None):
    o = {"id": oid, "kind": "curve", "layer": layer,
         "geom": {"type": "line", "p": [list(a), list(b)]}}
    if role:
        o["role"] = role
    if props:
        o["props"] = props
    if attrs:
        o["attrs"] = attrs
    return o


def pnt(oid, p, role=None, props=None, layer="SF::Supports"):
    o = {"id": oid, "kind": "point", "layer": layer,
         "geom": {"type": "point", "p": list(p)}}
    if role:
        o["role"] = role
    if props:
        o["props"] = props
    return o


def poly(oid, outer, holes=(), role=None, props=None, layer="SF::Slabs"):
    o = {"id": oid, "kind": "surface", "layer": layer,
         "geom": {"type": "polygon", "outer": [list(p) for p in outer],
                  "holes": [[list(p) for p in h] for h in holes]}}
    if role:
        o["role"] = role
    if props:
        o["props"] = props
    return o


def portal(z=H, dz_beam=0.0, sections=True, slab=False, s=1.0,
           slab_offset=0.0):
    """4 columns + 4 edge beams + 4 base supports (+ slab with opening),
    coordinates scaled by ``s`` (units)."""
    pts = [(0, 0), (L, 0), (L, L), (0, L)]
    objs = []
    for k, (x, y) in enumerate(pts):
        objs.append(crv(f"col{k}", (x * s, y * s, 0),
                        (x * s, y * s, (z + dz_beam) * s), role="column",
                        props={"section": "W14X90"} if sections else None))
        objs.append(pnt(f"sup{k}", (x * s, y * s, 0), role="support",
                        props={"restraint": [1, 1, 1, 1, 1, 1]}))
    for k in range(4):
        a, b = pts[k], pts[(k + 1) % 4]
        zz = (z + dz_beam) * s
        objs.append(crv(f"beam{k}", (a[0] * s, a[1] * s, zz),
                        (b[0] * s, b[1] * s, zz), role="beam",
                        props={"section": "W18X50"} if sections else None))
    if slab:
        zz = (z + dz_beam + slab_offset) * s
        objs.append(poly("slab", [(0, 0, zz), (L * s, 0, zz),
                                  (L * s, L * s, zz), (0, L * s, zz)],
                         holes=[[(2 * s, 2 * s, zz), (3 * s, 2 * s, zz),
                                 (3 * s, 3 * s, zz), (2 * s, 3 * s, zz)]],
                         role="slab", props={"thickness": 0.2 * s}))
    return objs


def dead_model():
    m = BuildingModel(name="xch")
    p = LoadPattern("DEAD", "dead")
    p.self_weight_factor = 1.0
    m.patterns["DEAD"] = p
    m.cases["DEAD"] = LoadCase("DEAD", {"DEAD": 1.0})
    return m


def run(model):
    from skyframe.engine.opensees_engine import OpenSeesEngine
    return OpenSeesEngine(model).run().to_dict()


def total_weight(model):
    """Closed form: sum A*L*gamma (frames) + t*net_area*gamma (shells)."""
    W = 0.0
    for m in model.members:
        sec = model.sections[m.section]
        W += sec.A * m.length * model.materials[sec.material].unit_weight
    for r in model.shells:
        ss = model.shell_sections[r.section]
        W += ss.thickness * r.net_area * \
            model.materials[ss.material].unit_weight
    return W


def sum_fz(res, case="DEAD"):
    return sum(v[2] for v in res["cases"][case]["reactions"].values())


# --------------------------------------------------------------------------- #
# validation / units
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mut,msg", [
    (lambda e: e.update(schema="x"), "schema"),
    (lambda e: e["source"].update(units="km"), "units"),
    (lambda e: e.update(session=""), "session"),
    (lambda e: e["objects"].append(dict(e["objects"][0])), "not unique"),
    (lambda e: e["objects"][0].update(kind="solid"), "kind"),
    (lambda e: e["objects"][0].update(role="pile"), "role"),
    (lambda e: e["objects"][0]["geom"].update(p=[[0, 0, 0]]), "geom.p"),
    (lambda e: e["objects"][0]["geom"].update(type="spline"), "type"),
    (lambda e: e["objects"][0].update(props={"release_i": [1, 0]}),
     "release_i"),
])
def test_validation_errors(mut, msg):
    e = ex([crv("a", (0, 0, 0), (0, 0, 3))])
    mut(e)
    with pytest.raises(ValueError, match=msg):
        X.validate_exchange(e)


@pytest.mark.parametrize("units,s", [("mm", 1000.0), ("cm", 100.0),
                                     ("ft", 1 / 0.3048), ("in", 1 / 0.0254),
                                     ("m", 1.0)])
def test_units_converted_to_m(units, s):
    e = ex(portal(s=s, slab=True), units=units, tol=0.001 * s)
    new, rep = X.apply_exchange(dead_model(), e)
    col = next(m for m in new.members
               if m.source_ref["id"] == "col2")
    assert col.pi == pytest.approx((6.0, 6.0, 0.0), abs=1e-9)
    assert col.pj == pytest.approx((6.0, 6.0, 3.0), abs=1e-9)
    # thickness prop is a length in source units too
    sl = next(r for r in new.shells if r.source_ref["id"] == "slab")
    assert new.shell_sections[sl.section].thickness == pytest.approx(0.2)
    assert sl.net_area == pytest.approx(35.0)
    assert rep["source"]["units"] == units


# --------------------------------------------------------------------------- #
# role resolution (spec §3)
# --------------------------------------------------------------------------- #
def test_role_resolution_priority():
    prof = {"rules": [{"layer": "Beams*", "role": "brace",
                       "section": "W12X26"},
                      {"layer": "*::Cols", "role": "column"}]}
    objs = [
        # 1. explicit role + props beat UserText and the rule
        crv("o1", (0, 0, 3), (6, 0, 3), role="column", layer="Beams",
            props={"section": "W10X33"},
            attrs={"sf_role": "beam", "sf_section": "W18X50"}),
        # 2. UserText beats the rule
        crv("o2", (0, 6, 3), (6, 6, 3), layer="Beams",
            attrs={"sf_role": "beam", "sf_section": "W18X50"}),
        # 3. the rule (glob on the layer path)
        crv("o3", (0, 0, 0), (6, 6, 3), layer="Beams::Level1"),
        # 4. defaults by geometry
        crv("o4", (0, 0, 0), (0, 0, 3), layer="Other"),
        crv("o5", (6, 0, 3), (6, 6, 3), layer="Other"),
        crv("o6", (6, 6, 0), (0, 6, 3), layer="Other"),
        crv("o7", (6, 6, 0), (6, 6, 3), layer="A::Cols"),
    ]
    rows = {o["id"]: o for o in
            X.preview_exchange(dead_model(), ex(objs), prof)["objects"]}
    assert (rows["o1"]["role"], rows["o1"]["section"]) == ("column",
                                                           "W10X33")
    assert (rows["o2"]["role"], rows["o2"]["section"]) == ("beam", "W18X50")
    assert (rows["o3"]["role"], rows["o3"]["section"]) == ("brace", "W12X26")
    assert rows["o3"]["rule"] == "Beams*"
    assert [rows[k]["role"] for k in ("o4", "o5", "o6", "o7")] == \
        ["column", "beam", "brace", "column"]


def test_default_roles_surfaces_points():
    objs = [
        poly("slab", [(0, 0, 3), (6, 0, 3), (6, 6, 3), (0, 6, 3)]),
        poly("wall", [(0, 0, 0), (6, 0, 0), (6, 0, 3), (0, 0, 3)]),
        poly("ramp", [(0, 0, 0), (6, 0, 0), (6, 6, 2), (0, 6, 2)]),
        pnt("p_sup", (0, 0, 0), layer="Supports"),
        pnt("p_jnt", (6, 6, 3), layer="Misc"),
        pnt("p_rst", (6, 0, 0), layer="Misc",
            props={"restraint": [1, 1, 1, 0, 0, 0]}),
    ]
    rows = {o["id"]: o for o in X.preview_exchange(
        dead_model(), ex(objs))["objects"]}
    assert rows["slab"]["role"] == "slab"
    assert rows["wall"]["role"] == "wall"
    assert rows["ramp"]["role"] == "shell"
    assert rows["p_sup"]["role"] == "support"
    assert rows["p_jnt"]["role"] == "joint"
    assert rows["p_rst"]["role"] == "support"
    new, _ = X.apply_exchange(dead_model(), ex(objs))
    kinds = {r.source_ref["id"]: r.kind for r in new.shells}
    # an inclined "shell" is a slab when |n_z| >= cos 45 (here 18.4 deg)
    assert kinds == {"slab": "slab", "wall": "wall", "ramp": "slab"}
    sup = {s.source_ref["id"]: s.restraints for s in new.supports}
    assert sup == {"p_sup": (1, 1, 1, 1, 1, 1), "p_rst": (1, 1, 1, 0, 0, 0)}
    assert new.exchange_sessions["GH-def-1"]["joints"] == \
        {"p_jnt#0": [6.0, 6.0, 3.0]}


def test_usertext_parsing_and_ignore_role():
    objs = [crv("a", (0, 0, 0), (0, 0, 3), attrs={
        "SF_Angle": "90", "sf_release_j": "000011", "sf_story": "Nope"}),
        crv("b", (0, 0, 3), (6, 0, 3), attrs={"sf_role": "ignore"}),
        crv("c", (6, 0, 3), (6, 0, 0), attrs={"sf_angle": "abc"})]
    new, rep = X.apply_exchange(dead_model(), ex(objs))
    a = next(m for m in new.members if m.source_ref["id"] == "a")
    assert a.angle == 90.0 and a.releases == "Mj"
    assert not any(m.source_ref["id"] == "b" for m in new.members)
    assert any("sf_angle='abc' ignored" in w for w in rep["warnings"])
    assert any("story 'Nope' does not exist" in w for w in rep["warnings"])


# --------------------------------------------------------------------------- #
# conversion
# --------------------------------------------------------------------------- #
def test_polyline_arc_nurbs_conversion():
    R = 5.0
    arc_pts = [(R * math.cos(t), R * math.sin(t), 3.0)
               for t in (0.0, math.pi / 4, math.pi / 2)]
    samples_arc = [[R * math.cos(t), R * math.sin(t), 6.0]
                   for t in [k * math.pi / 2 / 40 for k in range(41)]]
    samples_free = [[x, 0.0, 3.0 + 0.5 * math.sin(math.pi * x / 8)]
                    for x in [8 * k / 200 for k in range(201)]]
    objs = [
        {"id": "pl", "kind": "curve", "layer": "F",
         "geom": {"type": "polyline", "p": [[0, 10, 0], [0, 10, 3],
                                            [6, 10, 3]]}},
        {"id": "arc", "kind": "curve", "layer": "F",
         "geom": {"type": "arc", "p": [list(p) for p in arc_pts]}},
        {"id": "nb_arc", "kind": "curve", "layer": "F",
         "geom": {"type": "nurbs", "degree": 2, "samples": samples_arc}},
        {"id": "nb_free", "kind": "curve", "layer": "F",
         "geom": {"type": "nurbs", "degree": 3, "samples": samples_free}},
        {"id": "nb_mesh", "kind": "curve", "layer": "F",
         "props": {"mesh_size": 1.0},
         "geom": {"type": "nurbs", "degree": 3,
                  "samples": [[x, 20.0, 3.0 + 0.5 * math.sin(
                      math.pi * x / 8)] for x in [8 * k / 200
                                                   for k in range(201)]]}},
    ]
    new, rep = X.apply_exchange(dead_model(), ex(objs))
    by = {}
    for m in new.members:
        by.setdefault(m.source_ref["id"], []).append(m)
    # polyline: one member per segment, part = segment index, per-segment
    # role (column then beam)
    assert [(m.kind, m.source_ref["part"]) for m in by["pl"]] == \
        [("column", 0), ("beam", 1)]
    # arc -> one curved member through the mid point
    (a,) = by["arc"]
    assert a.curve["via"] == pytest.approx(list(arc_pts[1]))
    from skyframe.core.curved import arc_length
    assert arc_length(a) == pytest.approx(R * math.pi / 2)
    # a NURBS that is an exact arc is recognised as one curved member
    (b,) = by["nb_arc"]
    assert b.curve is not None and arc_length(b) == pytest.approx(
        R * math.pi / 2, rel=1e-6)
    # free-form NURBS: a chain of straight members (chord sag <= L/200)
    free = by["nb_free"]
    assert 3 <= len(free) <= 40
    assert free[0].pi == pytest.approx((0, 0, 3)) and \
        free[-1].pj == pytest.approx((8, 0, 3))
    for m0, m1 in zip(free[:-1], free[1:]):
        assert m0.pj == m1.pi
    assert all(m.kind == "beam" for m in free)       # chord is horizontal
    # mesh_size 1.0 -> equal pieces of length <= 1 m along the curve
    pieces = by["nb_mesh"]
    S = sum(math.dist(p, q) for p, q in zip(
        objs[4]["geom"]["samples"][:-1], objs[4]["geom"]["samples"][1:]))
    assert len(pieces) == math.ceil(S / 1.0)


def test_mesh_faces_welded_and_warped_quads_split():
    v = [[0, 0, 3], [3, 0, 3], [6, 0, 3], [0, 3, 3], [3, 3, 3.6], [6, 3, 3],
         [0, 6, 3], [3, 6, 3], [6, 6, 3],
         [3.0000001, 0, 3]]                       # near-duplicate of v1
    f = [[0, 9, 4, 3], [1, 2, 5, 4], [3, 4, 7, 6], [4, 5, 8, 7]]
    flat = {"id": "flat", "kind": "mesh", "layer": "Roof",
            "geom": {"type": "mesh",
                     "v": [[x + 10, y, 0] for x, y, _ in v[:9]],
                     "f": [[0, 1, 4, 3], [1, 2, 5, 4]]}}
    objs = [{"id": "roof", "kind": "mesh", "layer": "Roof",
             "geom": {"type": "mesh", "v": v, "f": f}}, flat]
    new, rep = X.apply_exchange(dead_model(), ex(objs))
    roof = [r for r in new.shells if r.source_ref["id"] == "roof"]
    # 4 warped quads -> 8 triangles; the near-duplicate vertex is welded
    assert len(roof) == 8 and all(len(r.corners) == 3 for r in roof)
    keys = {tuple(c) for r in roof for c in r.corners}
    assert len(keys) == 9
    flats = [r for r in new.shells if r.source_ref["id"] == "flat"]
    assert len(flats) == 2 and all(len(r.corners) == 4 for r in flats)
    assert [r.source_ref["part"] for r in flats] == [0, 1]


def test_nonplanar_polygon_skipped_with_warning():
    objs = [poly("bad", [(0, 0, 0), (6, 0, 0), (6, 6, 1), (0, 6, 0)])]
    new, rep = X.apply_exchange(dead_model(), ex(objs))
    assert not new.shells
    assert any("not planar" in w for w in rep["warnings"])


def test_links_and_releases():
    objs = [crv("lk", (0, 0, 0), (0, 0, 0.5), role="link",
                props={"stiffness": [1, 2, 3, 4, 5, 6]}),
            crv("b", (0, 0, 3), (6, 0, 3), role="beam",
                props={"release_i": [0, 0, 0, 0, 1, 1],
                       "release_j": [0, 0, 0, 1, 0, 1]})]
    new, rep = X.apply_exchange(dead_model(), ex(objs))
    (lk,) = new.links
    assert lk.stiffness == [1, 2, 3, 4, 5, 6] and lk.source_ref["id"] == "lk"
    (b,) = new.members
    # i end: M2+M3 pin -> legacy token; j end: T + M3 -> per-DOF releases
    assert b.releases == "Mi"
    assert b.end_releases == {"j": {"T": 0.0, "M3": 0.0}}
    assert not any("release approximated" in w for w in rep["warnings"])


def test_sections_from_steel_db_and_material_override():
    objs = [crv("a", (0, 0, 0), (0, 0, 3), role="column",
                props={"section": "W10X33", "material": "A992Fy50"}),
            crv("b", (0, 0, 3), (6, 0, 3), role="beam",
                props={"section": "NOPE"})]
    m = dead_model()
    m.materials["S275"] = Material("S275", 2.0e8, 0.3, 77.0)
    m.materials["S275"].material_type = "steel"
    new, rep = X.apply_exchange(m, ex(objs))
    assert "W10X33" in rep["sections_added"]
    a = next(x for x in new.members if x.source_ref["id"] == "a")
    assert new.sections[a.section].material == "A992Fy50"
    assert any("unknown section 'NOPE'" in w for w in rep["warnings"])


# --------------------------------------------------------------------------- #
# stories
# --------------------------------------------------------------------------- #
def test_story_clustering_new_model():
    objs = [crv("b1", (0, 0, 3.0), (6, 0, 3.0)),
            crv("b2", (0, 6, 3.03), (6, 6, 3.03)),      # same level (tol)
            crv("b3", (0, 0, 7.0), (6, 0, 7.0)),
            crv("c1", (0, 0, 0), (0, 0, 3.0)),
            crv("c2", (0, 0, 3.0), (0, 0, 7.0)),
            crv("br", (6, 0, 3.0), (0, 0, 7.0))]
    new, rep = X.apply_exchange(dead_model(), ex(objs))
    assert rep["stories_created"] == ["Story1", "Story2"]
    st = [(s.name, s.elevation, s.height) for s in new.stories]
    assert st == [("Story1", pytest.approx(3.015), pytest.approx(3.015)),
                  ("Story2", 7.0, pytest.approx(3.985))]
    story = {m.source_ref["id"]: m.story for m in new.members}
    assert story == {"b1": "Story1", "b2": "Story1", "b3": "Story2",
                     "c1": "Story1", "c2": "Story2", "br": "Story2"}


def test_story_clustering_keeps_existing_and_explicit_rule():
    m = dead_model()
    m.set_stories([3.0, 3.0])                       # Story1 3.0, Story2 6.0
    objs = [crv("b1", (0, 0, 3.0), (6, 0, 3.0)),
            crv("b9", (0, 0, 9.5), (6, 0, 9.5)),
            crv("c9", (0, 0, 6.0), (0, 0, 9.5)),
            crv("x", (0, 0, 4.0), (6, 0, 4.0),
                props={"story": "Story1"})]         # explicit: no level
    new, rep = X.apply_exchange(m, ex(objs))
    assert rep["stories_created"] == ["Story3"]
    assert [(s.name, s.elevation, s.height) for s in new.stories] == [
        ("Story1", 3.0, 3.0), ("Story2", 6.0, 3.0), ("Story3", 9.5, 3.5)]
    story = {x.source_ref["id"]: x.story for x in new.members}
    assert story == {"b1": "Story1", "b9": "Story3", "c9": "Story3",
                     "x": "Story1"}


# --------------------------------------------------------------------------- #
# apply -> analysis; repair connectivity
# --------------------------------------------------------------------------- #
def test_gh_payload_apply_and_analysis_equilibrium():
    new, rep = X.apply_exchange(dead_model(), ex(portal(slab=True)))
    assert rep["summary"]["create"] == {"members": 8, "shells": 1,
                                        "joints": 4, "links": 0}
    res = run(new)
    assert res["case_status"]["DEAD"] == "finished"
    # closed form: reactions carry the whole self weight
    assert sum_fz(res) == pytest.approx(total_weight(new), rel=1e-6)


def test_frames_connect_to_shells_after_repair():
    # slab corners 0.4 mm above the column tops (tolerance 1 mm) and a
    # beam ending mid-edge of another beam's span: the repair merges the
    # joints, snaps the slab and splits the frame -- the slab, which has no
    # support of its own, then carries its weight down the frame
    objs = portal(slab=True, slab_offset=0.0004)
    objs.append(crv("mid", (3, 0, 3), (3, 6, 3), role="beam",
                    props={"section": "W18X50"}))
    new, rep = X.apply_exchange(dead_model(), ex(objs))
    rr = rep["repair"]
    assert rr["counts"]["merge_joints"] + rr["counts"][
        "snap_shell_corners"] >= 1
    assert rr["counts"]["split_at_joints"] >= 1
    (slab,) = new.shells
    assert all(abs(c[2] - 3.0) < 1e-12 for c in slab.corners)
    beam0 = [m for m in new.members if m.source_ref["id"] == "beam0"]
    beam2 = [m for m in new.members if m.source_ref["id"] == "beam2"]
    assert len(beam0) == 2 and len(beam2) == 2       # split at x = 3
    assert beam0[0].pj == beam0[1].pi == (3.0, 0.0, 3.0)
    res = run(new)
    assert res["case_status"]["DEAD"] == "finished"
    assert sum_fz(res) == pytest.approx(total_weight(new), rel=1e-6)
    from skyframe.core.checks import check_model
    codes = check_model(new)["summary"]["by_code"]
    assert "SHELL_UNCONNECTED" not in codes and \
        "FRAME_UNCONNECTED" not in codes


def test_pure_joint_splits_frame_and_results_segments():
    objs = portal() + [pnt("j", (3, 0, 3), role="joint")]
    m = dead_model()
    new, rep = X.apply_exchange(m, ex(objs))
    pieces = [x for x in new.members if x.source_ref["id"] == "beam0"]
    assert len(pieces) == 2
    assert rep["joint_splits"][0]["joint"] == [3.0, 0.0, 3.0]
    new.patterns["DEAD"].nodal_loads.append(NodalLoad((3, 0, 3), fz=-10.0))
    res = run(new)
    out = X.results_by_source(new, res, "GH-def-1", "DEAD", "member_forces")
    segs = out["items"]["beam0"]["segments"]
    assert [s["uid"] for s in segs] == [p.uid for p in pieces]
    assert segs[0]["pj"] == segs[1]["pi"] == [3.0, 0.0, 3.0]
    disp = X.results_by_source(new, res, None, "DEAD", "displacements")
    dj = disp["items"]["j"]["disp"]
    assert dj[2] < 0
    assert disp["items"]["beam0"]["segments"][0]["j"] == dj
    rea = X.results_by_source(new, res, None, "DEAD", "reactions")
    assert sum(rea["items"][f"sup{k}"]["reaction"][2] for k in range(4)) == \
        pytest.approx(sum_fz(res))


# --------------------------------------------------------------------------- #
# merge / replace (spec §5)
# --------------------------------------------------------------------------- #
def test_merge_keeps_assigned_section_and_load_when_geometry_moves():
    m = dead_model()
    new, _ = X.apply_exchange(m, ex(portal()))
    b0 = next(x for x in new.members if x.source_ref["id"] == "beam0")
    # SkyFrame-side assignments
    new.sections["BIG"] = FrameSection.rectangular("BIG", "A992Fy50",
                                                   0.3, 0.6)
    b0.section = "BIG"
    b0.releases = "Mi"
    new.patterns["DEAD"].member_loads.append(
        MemberLoad(b0.uid, "udl", 7.5, 0.0, 0.0, 1.0, "gravity"))
    new.groups = {"G": {"members": [b0.uid], "shells": [], "links": [],
                        "points": [], "color": "#ff0000"}}
    new = BuildingModel.from_dict(new.to_dict())
    # Rhino side: the whole roof moves up 0.5 m (layer rule gives another
    # section -- a rule is NOT an explicit source assignment)
    moved = portal(dz_beam=0.5, sections=False)
    prof = {"rules": [{"layer": "*", "section": "W12X26"}]}
    new2, rep = X.apply_exchange(new, ex(moved), prof)
    b0b = next(x for x in new2.members if x.source_ref["id"] == "beam0")
    assert b0b.uid == b0.uid
    assert b0b.pi == (0.0, 0.0, 3.5) and b0b.pj == (6.0, 0.0, 3.5)
    assert b0b.section == "BIG" and b0b.releases == "Mi"
    assert [ld.member_uid for ld in new2.patterns["DEAD"].member_loads] \
        == [b0.uid]
    assert new2.groups["G"]["members"] == [b0.uid]
    assert rep["summary"]["update"]["members"] == 8
    assert rep["summary"]["create"]["members"] == 0
    # columns follow (longer), supports unchanged
    c = next(x for x in new2.members if x.source_ref["id"] == "col0")
    assert c.pj == (0.0, 0.0, 3.5) and c.section == "W14X90"
    assert rep["summary"]["unchanged"]["joints"] == 4
    res = run(new2)
    assert res["case_status"]["DEAD"] == "finished"


def test_merge_explicit_source_section_wins_and_moves_point_loads():
    m = dead_model()
    new, _ = X.apply_exchange(m, ex(portal()))
    b1 = next(x for x in new.members if x.source_ref["id"] == "beam1")
    b1.section = "W14X90"
    new.patterns["DEAD"].nodal_loads.append(NodalLoad((6, 0, 3), fz=-5.0))
    objs = portal(dz_beam=0.25)
    o = objs[[o["id"] for o in objs].index("beam1")]
    del o["props"]                  # (props would outrank the UserText)
    o["attrs"] = {"sf_section": "W12X26"}
    new2, rep = X.apply_exchange(new, ex(objs))
    b1b = next(x for x in new2.members if x.source_ref["id"] == "beam1")
    assert b1b.section == "W12X26"                   # UserText = explicit
    # the nodal load at the moved joint follows it
    assert new2.patterns["DEAD"].nodal_loads[0].point == (6.0, 0.0, 3.25)


def test_merge_split_pieces_follow_affine_map():
    # a beam split by a native column landing mid-span keeps its pieces
    m = dead_model()
    new, _ = X.apply_exchange(m, ex(portal()))
    new.add_member("column", "W14X90", (3, 0, 0), (3, 0, 3), uid="NAT1")
    from skyframe.core.repair import repair
    new, _ = repair(new, ["split_at_joints"], 0.001, check=False)
    pieces = [x for x in new.members if x.source_ref and
              x.source_ref["id"] == "beam0"]
    assert len(pieces) == 2
    new.patterns["DEAD"].member_loads.append(
        MemberLoad(pieces[1].uid, "udl", 2.0, 0.0, 0.0, 1.0, "gravity"))
    # source beam0 is lifted 0.5 m and stretched to x in [0, 8]
    objs = portal()
    i = [o["id"] for o in objs].index("beam0")
    objs[i]["geom"]["p"] = [[0, 0, 3.5], [8, 0, 3.5]]
    new2, rep = X.apply_exchange(new, ex(objs))
    p2 = [x for x in new2.members if x.source_ref and
          x.source_ref["id"] == "beam0"]
    assert [x.uid for x in p2] == [x.uid for x in pieces]
    assert p2[0].pi == (0.0, 0.0, 3.5) and p2[1].pj == (8.0, 0.0, 3.5)
    assert p2[0].pj == p2[1].pi == (4.0, 0.0, 3.5)   # t = 0.5 kept
    assert new2.patterns["DEAD"].member_loads[0].member_uid == pieces[1].uid
    # the native column is never moved nor deleted
    nat = next(x for x in new2.members if x.uid == "NAT1")
    assert nat.pj == (3.0, 0.0, 3.0)


def test_delete_only_same_session_objects():
    m = dead_model()
    new, _ = X.apply_exchange(m, ex(portal(), session="A"))
    other = [crv("x0", (20, 0, 0), (20, 0, 3), role="column",
                 props={"section": "W14X90"}),
             crv("x1", (20, 0, 3), (26, 0, 3), role="beam",
                 props={"section": "W14X90"})]
    new, _ = X.apply_exchange(new, ex(other, session="B"))
    new.add_member("beam", "W14X90", (30, 0, 3), (36, 0, 3), uid="NATIVE")
    n_before = len(new.members)
    # session A, second payload: beam3 and sup3 are gone
    objs = [o for o in portal() if o["id"] not in ("beam3", "sup3")]
    new2, rep = X.apply_exchange(new, ex(objs, session="A"))
    assert rep["summary"]["delete"] == {"members": 1, "shells": 0,
                                        "joints": 1, "links": 0}
    assert len(rep["deleted"]["members"]) == 1
    assert rep["deleted"]["supports"] == ["sup3"]
    assert len(new2.members) == n_before - 1
    ids = {(x.source_ref or {}).get("id") for x in new2.members}
    assert "beam3" not in ids and {"x0", "x1"} <= ids
    assert any(x.uid == "NATIVE" for x in new2.members)
    assert len(new2.supports) == 3
    dels = [o for o in rep["objects"] if o["status"] == "delete"]
    assert {o["id"] for o in dels} == {"beam3", "sup3"}
    # session B, empty payload: only B's objects go
    new3, rep3 = X.apply_exchange(new2, ex([], session="B"))
    assert rep3["summary"]["delete"]["members"] == 2
    assert sum(1 for x in new3.members if x.source_ref and
               x.source_ref["session"] == "A") == 7
    assert any(x.uid == "NATIVE" for x in new3.members)


def test_duplicate_of_other_session_dropped_with_warning():
    new, _ = X.apply_exchange(dead_model(), ex(portal(), session="A"))
    new2, rep = X.apply_exchange(new, ex(portal(), session="B"))
    # the repair keeps the first copy; the report says why B got nothing
    assert len(new2.members) == 8
    assert rep["created"]["members"] == []
    assert any("duplicated objects already in the model" in w
               for w in rep["warnings"])


def test_replace_mode_recreates_session():
    m = dead_model()
    new, _ = X.apply_exchange(m, ex(portal()))
    b0 = next(x for x in new.members if x.source_ref["id"] == "beam0")
    b0.section = "W14X90"
    new2, rep = X.apply_exchange(new, ex(portal()), mode="replace")
    assert rep["summary"]["delete"]["members"] == 8
    assert rep["summary"]["create"]["members"] == 8
    b0b = next(x for x in new2.members if x.source_ref["id"] == "beam0")
    assert b0b.section == "W18X50"                  # source again
    assert len(new2.members) == 8 and len(new2.supports) == 4


def test_reapply_is_unchanged_and_idempotent():
    m = dead_model()
    e = ex(portal(slab=True))
    new, _ = X.apply_exchange(m, e, now="T")
    new2, rep = X.apply_exchange(new, e, now="T")
    assert rep["summary"]["unchanged"] == {"members": 8, "shells": 1,
                                           "joints": 4, "links": 0}
    assert all(o["status"] == "unchanged" for o in rep["objects"])
    assert json.dumps(new2.to_dict(), sort_keys=True) == \
        json.dumps(new.to_dict(), sort_keys=True)


def test_preview_does_not_touch_model():
    m = dead_model()
    before = json.dumps(m.to_dict(), sort_keys=True)
    pv = X.preview_exchange(m, ex(portal(slab=True)))
    assert json.dumps(m.to_dict(), sort_keys=True) == before
    assert pv["valid"] is True
    assert pv["summary"]["create"]["members"] == 8
    assert {o["status"] for o in pv["objects"]} == {"create"}


def test_remove_session_and_sessions_listing():
    new, _ = X.apply_exchange(dead_model(), ex(portal(slab=True),
                                               session="A"))
    new, _ = X.apply_exchange(new, ex([pnt("j", (1, 1, 1), role="joint")],
                                      session="B"))
    ses = {s["session"]: s for s in X.sessions(new)}
    assert ses["A"]["objects"] == 13 and ses["A"]["members"] == 8
    assert ses["A"]["supports"] == 4 and ses["A"]["shells"] == 1
    assert ses["B"]["objects"] == 1 and ses["B"]["joints"] == 1
    new2, rep = X.remove_session(new, "A")
    assert not new2.members and not new2.shells and not new2.supports
    assert list(new2.exchange_sessions) == ["B"]
    new3, _ = X.remove_session(new2, "B")
    assert "exchange_sessions" not in new3.to_dict()
    with pytest.raises(ValueError):
        X.remove_session(new3, "B")


def test_mapping_profiles_round_trip_and_named_use():
    m = dead_model()
    X.save_mapping(m, "office", {"rules": [
        {"layer": "SF::*", "role": "beam", "section": "W12X26",
         "restraint": "111000", "story_rule": "auto"}]})
    d = m.to_dict()
    assert d["exchange_mappings"]["office"]["rules"][0]["restraint"] == \
        "111000"
    m2 = BuildingModel.from_dict(d)
    assert m2.to_dict() == d
    new, rep = X.apply_exchange(m2, ex([crv("a", (0, 0, 0), (0, 0, 3))]),
                                "office")
    assert new.members[0].kind == "beam" and \
        new.members[0].section == "W12X26"
    assert new.exchange_sessions["GH-def-1"]["mapping"] == "office"
    with pytest.raises(ValueError, match="unknown mapping"):
        X.apply_exchange(m2, ex([]), "nope")
    with pytest.raises(ValueError, match="unknown key"):
        X.save_mapping(m2, "bad", {"rules": [{"layer": "*", "colour": 1}]})


# --------------------------------------------------------------------------- #
# byte identity / round trip
# --------------------------------------------------------------------------- #
def test_byte_identity_without_exchange():
    from skyframe.core.builder import quick_building
    m = quick_building()
    d = m.to_dict()
    s = json.dumps(d)
    assert "source_ref" not in s and "exchange_" not in s
    assert json.dumps(BuildingModel.from_dict(d).to_dict()) == s


def test_source_ref_round_trip_and_no_effect_on_results():
    new, _ = X.apply_exchange(dead_model(), ex(portal(slab=True)), now="T")
    d = new.to_dict()
    assert d["members"][0]["source_ref"] == {"session": "GH-def-1",
                                             "id": "col0", "part": 0}
    assert "source_ref" in d["supports"][0] and \
        "source_ref" in d["shells"][0]
    assert BuildingModel.from_dict(d).to_dict() == d
    # strip every exchange field: identical analysis results
    bare = copy.deepcopy(d)
    for k in ("members", "shells", "supports", "links"):
        for o in bare[k]:
            o.pop("source_ref", None)
    bare.pop("exchange_sessions")
    r1 = run(BuildingModel.from_dict(d))
    r2 = run(BuildingModel.from_dict(bare))
    assert json.dumps(r1, sort_keys=True, default=str) == \
        json.dumps(r2, sort_keys=True, default=str)


def test_bad_source_ref_rejected():
    new, _ = X.apply_exchange(dead_model(), ex(portal()))
    d = new.to_dict()
    d["members"][0]["source_ref"] = {"session": "", "id": "x"}
    with pytest.raises(ValueError, match="source_ref.session"):
        BuildingModel.from_dict(d)


# --------------------------------------------------------------------------- #
# HTTP API (GH-style JSON)
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    import skyframe.api.server as srv
    app = srv.create_app()
    old = srv._state["model"]
    srv._state["model"] = dead_model()
    srv._tables_store.clear()
    with app.test_client() as c:
        yield c, srv
    srv._state["model"] = old
    srv._state.pop("rhino_link", None)
    srv._tables_store.clear()


def test_api_status_preview_apply_results(client):
    c, srv = client
    srv._state["model"].mass_source = {"DEAD": 1.0}      # modal periods
    r = c.get("/api/exchange/status")
    j = r.get_json()
    assert j["app"] == "SkyFrame" and j["model"] == "xch"
    assert j["sessions"] == [] and j["analysis"] == {"has_results": False}
    stored = json.dumps(srv._state["model"].to_dict(), sort_keys=True)
    e = ex(portal(slab=True))
    r = c.post("/api/exchange/preview", json={"exchange": e})
    assert r.status_code == 200
    pv = r.get_json()
    assert set(pv) >= {"summary", "warnings", "objects"}
    assert pv["summary"]["create"] == {"members": 8, "shells": 1,
                                       "joints": 4, "links": 0}
    assert {"id", "role", "section", "story", "status"} <= set(
        pv["objects"][0])
    assert json.dumps(srv._state["model"].to_dict(),
                      sort_keys=True) == stored
    r = c.get("/api/exchange/results?what=displacements")
    assert r.status_code == 409
    r = c.post("/api/exchange/apply", json={"exchange": e, "run": True})
    assert r.status_code == 200
    j = r.get_json()
    assert set(j) >= {"report", "model_summary"}
    assert j["model_summary"]["members"] == 8
    assert j["analysis"]["ok"] and j["analysis"]["case_status"] == {
        "DEAD": "finished", "MODAL": "finished"}
    assert len(srv._state["model"].members) == 8
    st = c.get("/api/exchange/status").get_json()
    assert st["analysis"]["has_results"] is True
    assert st["sessions"][0]["session"] == "GH-def-1"
    assert st["sessions"][0]["objects"] == 13
    r = c.get("/api/exchange/results?session=GH-def-1&case=DEAD"
              "&what=member_forces")
    assert r.status_code == 200
    j = r.get_json()
    assert j["units"] == "SI" and j["case"] == "DEAD"
    assert set(j["items"]) == {f"col{k}" for k in range(4)} | \
        {f"beam{k}" for k in range(4)}
    assert len(j["items"]["col0"]["segments"][0]["forces"]) == 12
    j = c.get("/api/exchange/results?what=reactions").get_json()
    assert sum(j["items"][f"sup{k}"]["reaction"][2] for k in range(4)) == \
        pytest.approx(total_weight(srv._state["model"]), rel=1e-6)
    j = c.get("/api/exchange/results?what=displacements").get_json()
    assert j["items"]["slab"]["type"] == "shell"
    assert len(j["items"]["slab"]["vertices"]) == 8
    j = c.get("/api/exchange/results?what=periods").get_json()
    assert len(j["model"]["periods"]) >= 1
    assert c.get("/api/exchange/results?what=story").status_code == 200
    assert c.get("/api/exchange/results?what=bogus").status_code == 400
    assert c.get("/api/exchange/results?case=NOPE").status_code == 404


def test_api_errors_and_model_in_body(client):
    c, srv = client
    assert c.post("/api/exchange/apply", json={}).status_code == 400
    r = c.post("/api/exchange/apply", json={"exchange": {"schema": "x"}})
    assert r.status_code == 400 and "schema" in r.get_json()["error"]
    body = {"exchange": ex(portal()), "model": dead_model().to_dict(),
            "mode": "replace"}
    r = c.post("/api/exchange/apply", json=body)
    assert r.status_code == 200
    assert len(r.get_json()["model"]["members"]) == 8
    r = c.post("/api/exchange/apply", json={"exchange": ex(portal()),
                                            "mode": "sideways"})
    assert r.status_code == 400


def test_api_cors_localhost_only(client):
    c, _srv = client
    for origin in ("http://127.0.0.1:8691", "http://localhost:3000",
                   "http://localhost"):
        r = c.get("/api/exchange/status", headers={"Origin": origin})
        assert r.headers.get("Access-Control-Allow-Origin") == origin
    r = c.options("/api/exchange/apply",
                  headers={"Origin": "http://localhost:5000",
                           "Access-Control-Request-Method": "POST"})
    assert r.headers.get("Access-Control-Allow-Origin") == \
        "http://localhost:5000"
    assert "POST" in r.headers.get("Access-Control-Allow-Methods")
    for origin in ("http://evil.com", "http://localhost.evil.com",
                   "http://127.0.0.1.nip.io"):
        r = c.get("/api/exchange/status", headers={"Origin": origin})
        assert "Access-Control-Allow-Origin" not in r.headers


def test_api_mappings_and_sessions(client):
    c, srv = client
    prof = {"rules": [{"layer": "SF::*", "role": "beam"}]}
    r = c.post("/api/exchange/mappings", json={"name": "p1",
                                               "profile": prof})
    assert r.status_code == 200
    assert r.get_json()["mappings"]["p1"]["rules"][0]["role"] == "beam"
    assert c.get("/api/exchange/mappings").get_json()["mappings"] == \
        {"p1": prof}
    r = c.post("/api/exchange/apply", json={
        "exchange": ex([crv("a", (0, 0, 0), (0, 0, 3))]), "mapping": "p1"})
    assert srv._state["model"].members[0].kind == "beam"
    r = c.post("/api/exchange/sessions/remove",
               json={"session": "GH-def-1"})
    assert r.status_code == 200
    assert not srv._state["model"].members
    r = c.post("/api/exchange/mappings", json={"name": "p1",
                                               "profile": None})
    assert r.get_json()["mappings"] == {}
    assert c.post("/api/exchange/sessions/remove",
                  json={"session": "zz"}).status_code == 400
    info = c.get("/api/rhino/info").get_json()
    assert set(info) == {"available", "version", "hint"}


# --------------------------------------------------------------------------- #
# the Grasshopper plugin's own sample payloads (rhino/samples/*.json of the
# C# plugin, copied verbatim to tests/fixtures/exchange)
# --------------------------------------------------------------------------- #
def _sample(name):
    import os
    p = os.path.join(os.path.dirname(__file__), "fixtures", "exchange",
                     f"{name}.json")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def test_plugin_samples_preview_apply_run(client):
    c, srv = client
    srv._state["model"].mass_source = {"DEAD": 1.0}
    for name in ("frames", "shells"):
        e = _sample(name)
        r = c.post("/api/exchange/preview", json={"exchange": e})
        assert r.status_code == 200, r.get_json()
        pv = r.get_json()
        assert pv["valid"] is True
        assert set(pv["summary"]) >= {"create", "update", "delete"}
        assert {o["id"] for o in pv["objects"]} <= {o["id"] for o in
                                                   e["objects"]}
        r = c.post("/api/exchange/apply", json={"exchange": e,
                                                "run": True})
        assert r.status_code == 200, r.get_json()
        j = r.get_json()
        assert j["analysis"]["ok"] is True, j["analysis"]
        assert j["analysis"]["case_status"]["DEAD"] == "finished"
        assert set(j["report"]["summary"]["create"]) >= {"members", "shells",
                                                         "joints"}
        assert "warnings" in j["report"]
        for what in X.RESULT_WHATS:
            r = c.get("/api/exchange/results", query_string={
                "session": e["session"], "case": "DEAD", "what": what})
            assert r.status_code == 200, (what, r.get_json())
            assert {"case", "units", "items"} <= set(r.get_json())
    m = srv._state["model"]
    fr = _sample("frames")
    col = next(x for x in m.members if x.source_ref["id"] == fr[
        "objects"][0]["id"])
    assert (col.kind, col.section, col.angle, col.releases) == \
        ("column", "W14X90", 90.0, "Mj")
    assert m.sections[col.section].material == "A992Fy50"   # "A992"
    sh = _sample("shells")
    slab = next(s for s in m.shells if s.source_ref["id"] ==
                sh["objects"][0]["id"])
    assert m.shell_sections[slab.section].thickness == pytest.approx(0.2)
    assert slab.mesh_size == pytest.approx(1.0)          # 1000 mm
    assert slab.net_area == pytest.approx(35.0)
    wall = [s for s in m.shells if s.source_ref["id"] == sh["objects"][2][
        "id"]]
    assert {s.kind for s in wall} == {"wall"} and len(wall) == 3
    assert m.shell_sections["WALL250"].thickness == pytest.approx(0.25)
    # (the shells sample's beam duplicates a segment of the frames sample's
    # polyline: the repair keeps the first -- check it on its own model)
    alone, rep = X.apply_exchange(dead_model(), sh)
    beam = next(x for x in alone.members if x.source_ref["id"] == sh[
        "objects"][3]["id"])
    assert beam.section == "W18X35" and beam.releases == "Mj"
    assert {s["session"] for s in X.sessions(m)} == {fr["session"],
                                                     sh["session"]}
    j = c.get("/api/exchange/results", query_string={
        "what": "periods"}).get_json()
    assert isinstance(j["periods"], list) and j["periods"]
    j = c.get("/api/exchange/results", query_string={
        "what": "drifts", "case": "DEAD"}).get_json()
    assert set(j["items"]) == {s.name for s in m.stories}
