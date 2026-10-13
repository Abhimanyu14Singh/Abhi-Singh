"""Wave F — select and assign in every view (CONTRACT "Wave F — select and
assign in every view").

Pinned:
  * pick math by hand: point / segment distances, even-odd point in polygon,
    WINDOW vs CROSSING for joints (points), frames (segments) and shells
    (polygons, incl. a box inside a slab), the Intersecting Line, Moller-
    Trumbore ray / triangle and ray / polygon (non-convex, with a hole)
    against closed forms, and the ETABS pick priority;
  * joint keys = coordinates rounded to 1e-6 in JavaScript number format;
  * story scope: counterparts on Similar / All stories (same kind, same plan
    position, shifted by the story top-elevation difference) incl. joints;
  * model-level assign via POST /api/assign (frame / shell sections, joint
    restraints with the automatic base supports written out, link
    properties) with This Story / Similar Stories / All Stories, dry_run,
    errors, and byte-identity when nothing changes;
  * when node is available the SAME cases run through the browser module
    js/f_pickgeom.js and must agree exactly, including the client-side
    replication of an assignment (section + a load entry + a restraint)
    onto the similar stories.
"""

import copy
import json
import math
import os
import shutil
import subprocess

import pytest

from skyframe.core import pickgeom as PG
from skyframe.core.assign_scope import assign
from skyframe.core.builder import quick_building

WEB_JS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "skyframe", "api",
                                       "web", "js", "f_pickgeom.js"))
NODE = shutil.which("node") or ("/opt/node22/bin/node" if os.path.exists("/opt/node22/bin/node") else None)


# ------------------------------------------------------------------ fixtures
def scoped_model() -> dict:
    """4 stories (top 3.2 / 6.4 / 9.6 / 12.8 m): Story1 master, Story2 and
    Story3 similar to it, Story4 independent; 2 x 1 bays of 6 m."""
    m = quick_building(bays_x=2, bays_y=1, stories=4)
    m.stories[0].master = True
    m.stories[1].similar_to = "Story1"
    m.stories[2].similar_to = "Story1"
    d = m.to_dict()
    d["shell_sections"] = {"S200": {"name": "S200", "material": d["sections"]["COL"]["material"], "thickness": 0.2},
                           "S150": {"name": "S150", "material": d["sections"]["COL"]["material"], "thickness": 0.15}}
    d["shells"] = []
    for st in d["stories"]:
        z = st["elevation"]
        d["shells"].append({"uid": "F" + st["name"][-1], "kind": "slab", "behavior": "shell", "section": "S200",
                            "corners": [[0, 0, z], [6, 0, z], [6, 6, z], [0, 6, z]], "story": st["name"]})
    d["links"] = [{"uid": f"L{i}", "pi": [12, 6, z - 3.2], "pj": [12, 6, z], "stiffness": [1e5] * 3 + [0] * 3,
                   "link_type": "elastic", "params": {}} for i, z in enumerate([3.2, 6.4, 9.6, 12.8], 1)]
    return d


def beam_on(d, story, x0, y0, x1, y1):
    z = next(s["elevation"] for s in d["stories"] if s["name"] == story)
    for mm in d["members"]:
        if mm["kind"] == "beam" and PG._same(mm["pi"], [x0, y0, z]) and PG._same(mm["pj"], [x1, y1, z]):
            return mm["uid"]
    raise AssertionError("no such beam")


# ------------------------------------------------------------------ pick math
def test_distances_and_point_in_polygon():
    assert PG.dist_to_seg(1, 3, 0, 0, 4, 0) == 3.0                    # foot inside
    assert PG.dist_to_seg(7, 4, 0, 0, 4, 0) == 5.0                    # 3-4-5 beyond the end
    assert PG.dist_to_seg(2, 2, 0, 0, 0, 0) == math.hypot(2, 2)       # degenerate
    L = [(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)]              # L-shape
    assert PG.point_in_poly(0.5, 3, L) and PG.point_in_poly(3, 0.5, L)
    assert not PG.point_in_poly(3, 3, L)                               # the notch


def test_window_vs_crossing_joints_frames_shells():
    r = PG.norm_rect([10, 10, 0, 0])
    assert r == [0, 0, 10, 10]
    # joints (points): inside for both modes
    assert PG.rect_hit_point(r, 5, 5) and not PG.rect_hit_point(r, 11, 5)
    # frame fully inside / crossing an edge / passing through / outside
    assert PG.rect_hit_seg(r, 1, 1, 9, 9, False) and PG.rect_hit_seg(r, 1, 1, 9, 9, True)
    assert not PG.rect_hit_seg(r, 5, 5, 15, 5, False) and PG.rect_hit_seg(r, 5, 5, 15, 5, True)
    assert not PG.rect_hit_seg(r, -5, 5, 15, 5, False) and PG.rect_hit_seg(r, -5, 5, 15, 5, True)
    assert not PG.rect_hit_seg(r, 11, 0, 11, 10, True)
    # shells: inside / overlapping / containing the box / disjoint
    sq = lambda a, b, s: [(a, b), (a + s, b), (a + s, b + s), (a, b + s)]   # noqa: E731
    assert PG.rect_hit_poly(r, sq(2, 2, 3), False) and PG.rect_hit_poly(r, sq(2, 2, 3), True)
    assert not PG.rect_hit_poly(r, sq(8, 8, 4), False) and PG.rect_hit_poly(r, sq(8, 8, 4), True)
    assert not PG.rect_hit_poly(r, sq(-5, -5, 30), False) and PG.rect_hit_poly(r, sq(-5, -5, 30), True)
    assert not PG.rect_hit_poly(r, sq(20, 20, 2), True)


def test_intersecting_line():
    assert PG.line_hit_seg((0, 0), (10, 10), (0, 10), (10, 0))
    assert not PG.line_hit_seg((0, 0), (4, 4), (6, 0), (10, -4))
    assert PG.line_hit_poly((-1, 2), (11, 2), [(0, 0), (10, 0), (10, 5), (0, 5)])
    assert not PG.line_hit_poly((1, 1), (2, 2), [(0, 0), (10, 0), (10, 5), (0, 5)])   # inside, crossing no edge
    assert PG.line_hit_point((0, 0), (10, 0), (5, 3), 3.0) and not PG.line_hit_point((0, 0), (10, 0), (5, 3.1), 3.0)


def test_ray_triangle_and_polygon_closed_forms():
    # ray down from (0.25, 0.25, 5) onto the unit right triangle in z = 1: t = 4
    t = PG.ray_triangle([0.25, 0.25, 5], [0, 0, -1], [0, 0, 1], [1, 0, 1], [0, 1, 1])
    assert t == pytest.approx(4.0, abs=1e-15)
    assert PG.ray_triangle([0.75, 0.75, 5], [0, 0, -1], [0, 0, 1], [1, 0, 1], [0, 1, 1]) is None   # beyond the hypotenuse
    assert PG.ray_triangle([0.25, 0.25, 0], [0, 0, -1], [0, 0, 1], [1, 0, 1], [0, 1, 1]) is None   # behind the ray
    # oblique ray: d = (1, 0, -1)/sqrt2 from (-1, 0.2, 2) hits z = 1 at x = 0 after t = sqrt2
    s2 = math.sqrt(2)
    t = PG.ray_triangle([-1, 0.2, 2], [1 / s2, 0, -1 / s2], [0, 0, 1], [1, 0, 1], [0, 1, 1])
    assert t == pytest.approx(s2, abs=1e-14)
    # vertical wall x = 3, L-shaped (non-convex) with a hole; ray along +x from x = 0
    L = [[3, 0, 0], [3, 4, 0], [3, 4, 1], [3, 1, 1], [3, 1, 4], [3, 0, 4]]
    hole = [[3, 0.2, 2], [3, 0.8, 2], [3, 0.8, 3], [3, 0.2, 3]]
    assert PG.ray_polygon([0, 0.5, 0.5], [1, 0, 0], L) == pytest.approx(3.0)
    assert PG.ray_polygon([0, 3, 3], [1, 0, 0], L) is None                 # in the notch
    assert PG.ray_polygon([0, 0.5, 2.5], [1, 0, 0], L, [hole]) is None     # through the opening
    assert PG.ray_polygon([0, 0.5, 2.5], [1, 0, 0], L) == pytest.approx(3.0)
    assert PG.ray_polygon([0, 0.5, 0.5], [0, 1, 0], L) is None             # parallel


def test_pick_priority():
    hits = [{"ref": {"type": "shell", "uid": "F1"}, "d": 0, "z": 10},
            {"ref": {"type": "member", "uid": "B2"}, "d": 3, "z": 12},
            {"ref": {"type": "member", "uid": "B1"}, "d": 2, "z": 14},
            {"ref": {"type": "joint", "uid": "0,0,3.2"}, "d": 5, "z": 13}]
    assert PG.resolve_pick(hits) == {"type": "joint", "uid": "0,0,3.2"}
    assert PG.resolve_pick(hits[:3]) == {"type": "member", "uid": "B1"}      # nearest line
    assert PG.resolve_pick(hits[:1]) == {"type": "shell", "uid": "F1"}
    assert PG.resolve_pick([]) is None


def test_joint_keys_js_format():
    assert PG.joint_key([0.0, 0, 3.2]) == "0,0,3.2"
    assert PG.joint_key([1e-6, -0.0, 9.600000000000001]) == "0.000001,0,9.6"
    assert PG.joint_key([12.0, -6.5, 1234567.25]) == "12,-6.5,1234567.25"
    assert PG.joint_point("0.000001,0,9.6") == [1e-6, 0.0, 9.6]


# ------------------------------------------------------------------ story scope
def test_corresponding_refs_similar_and_all():
    d = scoped_model()
    b1 = beam_on(d, "Story1", 0, 0, 6, 0)
    refs = [{"type": "member", "uid": b1}, {"type": "shell", "uid": "F1"},
            {"type": "joint", "uid": PG.joint_key([6, 0, 3.2])}, {"type": "link", "uid": "L1"}]
    sim = PG.corresponding_refs(d, refs, "similar")
    to = sorted({p["to"] for p in sim["pairs"]})
    assert to == ["Story2", "Story3"]                                   # Story4 is independent
    assert {r["uid"] for r in sim["refs"] if r["type"] == "shell"} == {"F2", "F3"}
    assert {r["uid"] for r in sim["refs"] if r["type"] == "link"} == {"L2", "L3"}
    assert {r["uid"] for r in sim["refs"] if r["type"] == "joint"} == {"6,0,6.4", "6,0,9.6"}
    for p in sim["pairs"]:
        assert p["dz"] == pytest.approx({"Story2": 3.2, "Story3": 6.4}[p["to"]])
    al = PG.corresponding_refs(d, refs, "all")
    assert sorted({p["to"] for p in al["pairs"]}) == ["Story2", "Story3", "Story4"]
    assert len(al["refs"]) == 12 and al["missing"] == 0
    assert PG.corresponding_refs(d, refs, "one")["refs"] == []
    # from a non-master story of the group: the master and the other similar story
    b2 = beam_on(d, "Story2", 0, 0, 6, 0)
    r2 = PG.corresponding_refs(d, [{"type": "member", "uid": b2}], "similar")
    assert sorted(p["to"] for p in r2["pairs"]) == ["Story1", "Story3"]
    # an object without a counterpart is counted as missing
    d["shells"] = [s for s in d["shells"] if s["uid"] != "F3"]
    assert PG.corresponding_refs(d, [{"type": "shell", "uid": "F1"}], "similar")["missing"] == 1


# ------------------------------------------------------------------ model-level assign
def test_assign_frame_section_scope():
    d = scoped_model()
    b1 = beam_on(d, "Story1", 0, 0, 6, 0)
    out, s = assign(d, {"members": [b1]}, {"frame_section": "COL"}, "similar")
    sec = {mm["uid"]: mm["section"] for mm in out["members"]}
    assert sec[b1] == "COL"
    assert sec[beam_on(d, "Story2", 0, 0, 6, 0)] == "COL" and sec[beam_on(d, "Story3", 0, 0, 6, 0)] == "COL"
    assert sec[beam_on(d, "Story4", 0, 0, 6, 0)] == "BEAM"
    assert s["written"]["members"] == 3 and s["counterparts"] == 2 and s["stories"] == ["Story1", "Story2", "Story3"]
    # nothing else changed
    changed = [k for k in d if json.dumps(d[k]) != json.dumps(out[k])]
    assert changed == ["members"]
    assert d["members"][0]["section"] == "COL" and json.dumps(d) == json.dumps(scoped_model())   # input untouched
    out1, s1 = assign(d, {"members": [b1]}, {"frame_section": "COL"}, "one")
    assert s1["written"]["members"] == 1 and s1["counterparts"] == 0


def test_assign_shell_section_link_and_releases_all():
    d = scoped_model()
    out, s = assign(d, {"shells": ["F2"]}, {"shell_section": "S150"}, "all")
    assert [x["section"] for x in out["shells"]] == ["S150"] * 4 and s["written"]["shells"] == 4
    out, s = assign(d, {"links": ["L1"]}, {"link_property": {"link_type": "damper", "params": {"cd": 250.0, "alpha": 0.5}}}, "similar")
    lt = {x["uid"]: x["link_type"] for x in out["links"]}
    assert lt == {"L1": "damper", "L2": "damper", "L3": "damper", "L4": "elastic"}
    b = beam_on(d, "Story4", 0, 0, 6, 0)
    out, s = assign(d, {"members": [b]}, {"frame_releases": "Mi, Mj"}, "similar")   # independent story: itself only
    assert s["written"]["members"] == 1 and [x for x in out["members"] if x["uid"] == b][0]["releases"] == "Mi,Mj"


def test_assign_restraints_keeps_base_supports():
    d = scoped_model()
    assert not d.get("supports")
    out, s = assign(d, {"points": [[6, 0, 3.2]]}, {"restraints": [1, 1, 0, 0, 0, 0]}, "similar")
    sup = {PG.joint_key(x["point"]): x["restraints"] for x in out["supports"]}
    base = {k: v for k, v in sup.items() if k.endswith(",0")}
    assert len(base) == 6 and all(v == [1] * 6 for v in base.values())        # automatic base written out
    assert sup["6,0,3.2"] == sup["6,0,6.4"] == sup["6,0,9.6"] == [1, 1, 0, 0, 0, 0]
    assert "6,0,12.8" not in sup and s["written"]["points"] == 3
    # clearing removes the explicit supports again (the base ones stay)
    out2, _ = assign(out, {"points": [[6, 0, 3.2]]}, {"restraints": [0] * 6}, "similar")
    assert {PG.joint_key(x["point"]) for x in out2["supports"]} == set(base)


def test_assign_errors():
    d = scoped_model()
    b1 = beam_on(d, "Story1", 0, 0, 6, 0)
    for sel, spec, scope, msg in [
        ({"members": [b1]}, {"frame_section": "NOPE"}, "one", "unknown frame section"),
        ({"members": ["X9"]}, {"frame_section": "COL"}, "one", "unknown uid"),
        ({"members": [b1]}, {"frame_section": "COL", "shell_section": "S150"}, "one", "exactly one"),
        ({"members": [b1]}, {"frame_section": "COL"}, "some", "scope must be"),
        ({"points": [[1, 1, 1]]}, {"restraints": [1] * 6}, "one", "no joint at"),
        ({"members": [b1]}, {"restraints": [1] * 6}, "one", "no joints"),
        ({"shells": ["F1"]}, {"frame_section": "COL"}, "one", "no frame objects"),
        ({}, {"frame_section": "COL"}, "one", "empty"),
    ]:
        with pytest.raises(ValueError, match=msg):
            assign(d, sel, spec, scope)


def test_assign_api_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api.server import create_app
    app = create_app()
    c = app.test_client()
    d = scoped_model()
    assert c.post("/api/model", json=d).status_code == 200
    before = c.get("/api/model").get_json()
    b1 = beam_on(before, "Story1", 0, 0, 6, 0)
    r = c.post("/api/assign", json={"selection": {"members": [b1]}, "assign": {"frame_section": "COL"}, "scope": "similar", "dry_run": True})
    assert r.status_code == 200 and r.get_json()["summary"]["written"]["members"] == 3
    assert c.get("/api/model").get_json() == before                  # dry run: unchanged
    r = c.post("/api/assign", json={"selection": {"members": [b1]}, "assign": {"frame_section": "BEAM"}, "scope": "similar"})
    assert r.status_code == 200
    assert json.dumps(c.get("/api/model").get_json(), sort_keys=True) == json.dumps(before, sort_keys=True)   # same value → byte-identical
    r = c.post("/api/assign", json={"selection": {"points": [[0, 0, 3.2]]}, "assign": {"restraints": [1, 1, 1, 0, 0, 0]}, "scope": "all"})
    assert r.status_code == 200
    sup = c.get("/api/model").get_json()["supports"]
    assert sum(1 for s in sup if s["point"][:2] == [0, 0] and s["point"][2] > 0) == 4
    r = c.post("/api/assign", json={"selection": {"members": [b1]}, "assign": {"frame_section": "NOPE"}})
    assert r.status_code == 400 and "unknown frame section" in r.get_json()["error"]
    assert c.post("/api/assign", data="x").status_code == 400


# ------------------------------------------------------------------ JS mirror (node)
def run_node(script: str):
    js = "import * as G from " + json.dumps("file://" + WEB_JS) + ";\n" + script
    p = subprocess.run([NODE, "--input-type=module", "-e", js], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_js_mirror_pick_math_agrees():
    cases = {
        "dist": [[1, 3, 0, 0, 4, 0], [7, 4, 0, 0, 4, 0], [2, 2, 0, 0, 0, 0], [0.3, 0.7, -1.1, 2.2, 3.3, -4.4]],
        "pip": [[0.5, 3], [3, 0.5], [3, 3], [1, 1]],
        "seg": [[1, 1, 9, 9], [5, 5, 15, 5], [-5, 5, 15, 5], [11, 0, 11, 10], [0, -1, 10, 11]],
        "poly": [[2, 2, 3], [8, 8, 4], [-5, -5, 30], [20, 20, 2], [-1, 4, 2]],
        "tri": [[[0.25, 0.25, 5], [0, 0, -1]], [[0.75, 0.75, 5], [0, 0, -1]], [[-1, 0.2, 2], [0.7071067811865475, 0, -0.7071067811865475]]],
        "rp": [[[0, 0.5, 0.5], [1, 0, 0], 0], [[0, 3, 3], [1, 0, 0], 0], [[0, 0.5, 2.5], [1, 0, 0], 1], [[0, 0.5, 2.5], [1, 0, 0], 0]],
        "keys": [[0.0, 0, 3.2], [1e-6, -0.0, 9.600000000000001], [12.0, -6.5, 1234567.25]],
    }
    L = [(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)]
    W = [[3, 0, 0], [3, 4, 0], [3, 4, 1], [3, 1, 1], [3, 1, 4], [3, 0, 4]]
    H = [[3, 0.2, 2], [3, 0.8, 2], [3, 0.8, 3], [3, 0.2, 3]]
    sq = lambda a, b, s: [[a, b], [a + s, b], [a + s, b + s], [a, b + s]]   # noqa: E731
    r = [0, 0, 10, 10]
    tri = [[0, 0, 1], [1, 0, 1], [0, 1, 1]]
    py = {
        "dist": [PG.dist_to_seg(*c) for c in cases["dist"]],
        "pip": [PG.point_in_poly(x, y, L) for x, y in cases["pip"]],
        "seg": [[PG.rect_hit_seg(r, *c, False), PG.rect_hit_seg(r, *c, True)] for c in cases["seg"]],
        "poly": [[PG.rect_hit_poly(r, sq(*c), False), PG.rect_hit_poly(r, sq(*c), True)] for c in cases["poly"]],
        "tri": [PG.ray_triangle(o, d, *tri) for o, d in cases["tri"]],
        "rp": [PG.ray_polygon(o, d, W, [H] if h else []) for o, d, h in cases["rp"]],
        "keys": [PG.joint_key(p) for p in cases["keys"]],
    }
    js = run_node(f"""
const C = {json.dumps(cases)}, L = {json.dumps(L)}, W = {json.dumps(W)}, H = {json.dumps(H)}, r = [0, 0, 10, 10], tri = {json.dumps(tri)};
const sq = (a, b, s) => [[a, b], [a + s, b], [a + s, b + s], [a, b + s]];
console.log(JSON.stringify({{
  dist: C.dist.map(c => G.distToSeg(...c)),
  pip: C.pip.map(([x, y]) => G.pointInPoly(x, y, L)),
  seg: C.seg.map(c => [G.rectHitSeg(r, ...c, false), G.rectHitSeg(r, ...c, true)]),
  poly: C.poly.map(c => [G.rectHitPoly(r, sq(...c), false), G.rectHitPoly(r, sq(...c), true)]),
  tri: C.tri.map(([o, d]) => G.rayTriangle(o, d, ...tri)),
  rp: C.rp.map(([o, d, h]) => G.rayPolygon(o, d, W, h ? [H] : [])),
  keys: C.keys.map(p => G.jointKey(p)),
}}));""")
    assert js == py


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_js_mirror_scope_and_replication_agree():
    d = scoped_model()
    b1 = beam_on(d, "Story1", 0, 0, 6, 0)
    refs = [{"type": "member", "uid": b1}, {"type": "shell", "uid": "F1"},
            {"type": "joint", "uid": PG.joint_key([6, 0, 3.2])}, {"type": "link", "uid": "L1"}]
    for scope in ("similar", "all"):
        py = PG.corresponding_refs(d, refs, scope)
        js = run_node(f"console.log(JSON.stringify(G.correspondingRefs({json.dumps(d)}, {json.dumps(refs)}, {json.dumps(scope)})));")
        assert js["refs"] == py["refs"] and js["missing"] == py["missing"]
        assert [(p["src"], p["dst"], p["from"], p["to"]) for p in js["pairs"]] == \
               [(p["src"], p["dst"], p["from"], p["to"]) for p in py["pairs"]]
    # client-side replication (what an Assign dialog with "Similar Stories" does):
    # section + a UDL on the beam, a restraint at a joint, the slab section
    m = copy.deepcopy(d)
    m["patterns"] = {"DEAD": {"name": "DEAD", "member_udls": []}}
    js = run_node(f"""
const m = {json.dumps(m)}, refs = {json.dumps(refs)};
const before = G.assignSnapshot(m);
const b = m.members.find(x => x.uid === {json.dumps(b1)});
b.section = "COL"; b.releases = "Mi";
m.patterns.DEAD.member_udls.push({{ member_uid: b.uid, w: 12.5 }});
m.supports = [{{ point: [6, 0, 3.2], restraints: [1, 1, 1, 0, 0, 0] }}];
m.shells.find(s => s.uid === "F1").section = "S150";
const pairs = G.correspondingRefs(m, refs, "similar").pairs;
const n = G.replicateChanges(m, before, pairs);
const again = G.replicateChanges(m, G.assignSnapshot(m), pairs);
console.log(JSON.stringify({{ n, again, m }}));""")
    out = js["m"]
    assert js["again"] == 0                                             # idempotent
    py, _ = assign(d, {"members": [b1]}, {"frame_section": "COL"}, "similar")
    sec_js = {x["uid"]: x["section"] for x in out["members"]}
    assert sec_js == {x["uid"]: x["section"] for x in py["members"]}     # same objects as the API
    for st in ("Story2", "Story3"):
        u = beam_on(d, st, 0, 0, 6, 0)
        assert [x for x in out["members"] if x["uid"] == u][0]["releases"] == "Mi"
        assert {"member_uid": u, "w": 12.5} in out["patterns"]["DEAD"]["member_udls"]
    assert [x for x in out["members"] if x["uid"] == beam_on(d, "Story4", 0, 0, 6, 0)][0]["section"] == "BEAM"
    assert {PG.joint_key(s["point"]) for s in out["supports"]} == {"6,0,3.2", "6,0,6.4", "6,0,9.6"}
    assert [s["section"] for s in out["shells"]] == ["S150", "S150", "S150", "S200"]
    assert js["n"] == 2 * 3          # beam, slab, joint on Story2 and Story3 (the link did not change)
