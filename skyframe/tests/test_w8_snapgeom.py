"""W8 snapping + 3-point-arc geometry (skyframe/core/snapgeom.py, the
JS-free mirror of js/w8_snapgeom.js).

Closed forms: segment intersections by hand, perpendicular feet, the
circumcircle of three points (cross-checked against the engine's own arc
geometry, core/curved.arc_geometry), the radius -> sagitta construction,
and snap ranking.  When node is available, the SAME cases are evaluated by
the browser module and must agree to 1e-12.
"""

import json
import math
import os
import shutil
import subprocess

import pytest

from skyframe.core import snapgeom as SG
from skyframe.core import curved as CV

WEB_JS = os.path.join(os.path.dirname(__file__), "..", "skyframe", "api",
                      "web", "js", "w8_snapgeom.js")


def test_segment_intersection_closed_forms():
    r = SG.seg_intersect((0, 0), (4, 4), (0, 4), (4, 0))
    assert (r["x"], r["y"], r["t"], r["u"]) == (2.0, 2.0, 0.5, 0.5)
    # general: y = x/3 and the line x = 2.5 between y = -1 and 3
    r = SG.seg_intersect((0, 0), (6, 2), (2.5, -1), (2.5, 3))
    assert r["x"] == pytest.approx(2.5, abs=1e-15)
    assert r["y"] == pytest.approx(2.5 / 3, abs=1e-15)
    # touching at an end point counts; parallel, collinear, disjoint do not
    assert SG.seg_intersect((0, 0), (2, 0), (2, 0), (2, 5))["x"] == 2.0
    assert SG.seg_intersect((0, 0), (2, 0), (0, 1), (2, 1)) is None
    assert SG.seg_intersect((0, 0), (2, 0), (1, 0), (3, 0)) is None
    assert SG.seg_intersect((0, 0), (1, 1), (3, 0), (2, 1)) is None


def test_perpendicular_foot_and_midpoint():
    f = SG.perp_foot((1, 3), (0, 0), (4, 0))
    assert (f["x"], f["y"], f["t"]) == (1.0, 0.0, 0.25)
    # rotated segment y = x: foot of (0, 2) is (1, 1)
    f = SG.perp_foot((0, 2), (0, 0), (5, 5))
    assert f["x"] == pytest.approx(1.0, abs=1e-15)
    assert f["y"] == pytest.approx(1.0, abs=1e-15)
    assert SG.perp_foot((9, 3), (0, 0), (4, 0)) is None     # beyond the end
    assert SG.midpoint((0, 0), (3, 7)) == {"x": 1.5, "y": 3.5}


def test_snap_ranking_and_modes():
    # an L of two beams + a crossing beam
    segs = [((0, 0), (6, 0)), ((6, 0), (6, 6)), ((3, -2), (3, 2))]
    pts = [p for s in segs for p in s]
    allm = {"joint": True, "mid": True, "inter": True, "perp": True}
    # near the corner: the joint wins
    s = SG.snap_objects((5.9, 0.1), pts, segs, 0.5, allm)
    assert (s["kind"], s["x"], s["y"]) == ("joint", 6, 0)
    # near (3, 0): the intersection beats the midpoint of (0,0)-(6,0)
    s = SG.snap_objects((3.05, 0.02), pts, segs, 0.3, allm)
    assert (s["kind"], s["x"], s["y"]) == ("inter", 3.0, 0.0)
    s = SG.snap_objects((3.05, 0.02), pts, segs, 0.3,
                        {"mid": True, "joint": True})
    assert (s["kind"], s["x"], s["y"]) == ("mid", 3.0, 0.0)
    # perpendicular from a pending point (1, 4) onto the vertical (6, y)
    s = SG.snap_objects((6.1, 4.1), pts, segs, 0.4, {"perp": True},
                        frm=(1, 4))
    assert (s["kind"], s["x"], s["y"]) == ("perp", 6.0, 4.0)
    assert SG.snap_objects((4.5, 3), pts, segs, 0.3, allm) is None


def test_circumcircle_matches_engine_arc_geometry():
    pi, via, pj = (2.0, 1.0, 3.2), (0.5, 4.0, 3.2), (-3.0, 2.0, 3.2)
    C = SG.circle3(pi, via, pj)
    g = CV.arc_geometry(pi, pj, {"type": "arc", "via": list(via),
                                 "segments": None, "local2": "in_plane"})
    assert C["center"] == pytest.approx(list(g["center"]), abs=1e-12)
    assert C["r"] == pytest.approx(g["radius"], rel=1e-14)
    a = SG.arc_points3(pi, via, pj, 36)
    assert a["sweep"] == pytest.approx(g["sweep"], rel=1e-13)
    for p in a["points"]:
        assert math.dist(p, C["center"]) == pytest.approx(C["r"], rel=1e-13)
    # semicircle R = 10: sweep pi, mid point = via
    a = SG.arc_points3((10, 0, 0), (0, 10, 0), (-10, 0, 0), 2)
    assert a["sweep"] == pytest.approx(math.pi, rel=1e-15)
    assert a["points"][1] == pytest.approx([0, 10, 0], abs=1e-12)
    # vertical-plane arch
    a = SG.arc_points3((-5, 0, 0), (0, 0, 5), (5, 0, 0), 4)
    assert a["r"] == pytest.approx(5.0) and a["center"] == pytest.approx(
        [0, 0, 0], abs=1e-12)
    assert SG.circle3((0, 0, 0), (1, 1, 0), (2, 2, 0)) is None


def test_via_from_radius_sagitta():
    # chord 6, R = 5 -> sagitta 5 - 4 = 1, left of +X is +Y
    v = SG.via_from_radius((0, 0, 3), (6, 0, 3), 5.0, 1)
    assert v == pytest.approx([3.0, 1.0, 3.0], abs=1e-15)
    v = SG.via_from_radius((0, 0, 3), (6, 0, 3), 5.0, -1)
    assert v == pytest.approx([3.0, -1.0, 3.0], abs=1e-15)
    assert SG.circle3((0, 0, 3), v, (6, 0, 3))["r"] == pytest.approx(5.0)
    # R = c/2 gives the semicircle; R < c/2 is impossible
    assert SG.via_from_radius((0, 0, 0), (6, 0, 0), 3.0, 1) == \
        pytest.approx([3, 3, 0])
    assert SG.via_from_radius((0, 0, 0), (6, 0, 0), 2.9, 1) is None


NODE = shutil.which("node") or ("/opt/node22/bin/node"
                                if os.path.exists("/opt/node22/bin/node")
                                else None)


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_js_module_matches_python_mirror(tmp_path):
    cases = {
        "inter": [[[0, 0], [6, 2], [2.5, -1], [2.5, 3]],
                  [[0, 0], [4, 4], [0, 4], [4, 0]],
                  [[0, 0], [2, 0], [0, 1], [2, 1]]],
        "perp": [[[0, 2], [0, 0], [5, 5]], [[1, 3], [0, 0], [4, 0]],
                 [[9, 3], [0, 0], [4, 0]]],
        "circle": [[[2, 1, 3.2], [0.5, 4, 3.2], [-3, 2, 3.2]],
                   [[-5, 0, 0], [0, 0, 5], [5, 0, 0]]],
        "via": [[[0, 0, 3], [6, 0, 3], 5, 1], [[1, 2, 0], [4, 6, 0], 4, -1]],
        "snap": [[[3.05, 0.02], 0.3], [[5.9, 0.1], 0.5], [[6.1, 4.1], 0.4]],
    }
    script = tmp_path / "run.mjs"
    url = "file://" + os.path.abspath(WEB_JS)
    script.write_text(
        f"import G from {json.dumps(url)};\n"
        f"const C = {json.dumps(cases)};\n"
        "const segs = [[[0,0],[6,0]],[[6,0],[6,6]],[[3,-2],[3,2]]];\n"
        "const T = {points: segs.flat(), segs};\n"
        "const all = {joint:true, mid:true, inter:true, perp:true};\n"
        "const out = {\n"
        " inter: C.inter.map(a => G.segIntersect(...a)),\n"
        " perp: C.perp.map(a => G.perpFoot(...a)),\n"
        " circle: C.circle.map(a => G.circle3(...a)),\n"
        " arc: C.circle.map(a => G.arcPoints3(a[0], a[1], a[2], 8)),\n"
        " via: C.via.map(a => G.viaFromRadius(...a)),\n"
        " snap: C.snap.map(([w, t]) => G.snapObjects({x: w[0], y: w[1]},"
        " T, t, all, [1, 4])),\n"
        "};\n"
        "console.log(JSON.stringify(out));\n")
    res = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                         timeout=60)
    assert res.returncode == 0, res.stderr
    js = json.loads(res.stdout)
    segs = [((0, 0), (6, 0)), ((6, 0), (6, 6)), ((3, -2), (3, 2))]
    pts = [p for s in segs for p in s]
    allm = {"joint": True, "mid": True, "inter": True, "perp": True}
    py = {
        "inter": [SG.seg_intersect(*a) for a in cases["inter"]],
        "perp": [SG.perp_foot(*a) for a in cases["perp"]],
        "circle": [SG.circle3(*a) for a in cases["circle"]],
        "arc": [SG.arc_points3(a[0], a[1], a[2], 8) for a in cases["circle"]],
        "via": [SG.via_from_radius(*a) for a in cases["via"]],
        "snap": [SG.snap_objects(w, pts, segs, t, allm, frm=(1, 4))
                 for w, t in cases["snap"]],
    }

    def close(a, b):
        if isinstance(a, dict):
            return set(a) == set(b) and all(close(a[k], b[k]) for k in a)
        if isinstance(a, list):
            return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return abs(a - b) <= 1e-12 * max(1.0, abs(a))
        return a == b
    for k in py:
        assert close(json.loads(json.dumps(py[k])), js[k]), k
