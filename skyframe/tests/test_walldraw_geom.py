"""D1 Draw Walls (plan) geometry (skyframe/core/walldraw_geom.py, the
JS-free mirror of js/walldraw_geom.js).

Closed forms: alignment offsets (+-t/2), mitred offset polylines (an L and a
closed rectangle offset by s give the hand-computed inner/outer corners),
splits where grid lines cross a drawn line, Quick Draw grid panels, typed
length / polar / relative / absolute points, story replication over
master / similar stories and duplicate suppression.  When node is
available the SAME cases are evaluated by the browser module and must
agree to 1e-12.
"""

import json
import math
import os
import shutil
import subprocess

import pytest

from skyframe.core import walldraw_geom as G

WEB_JS = os.path.join(os.path.dirname(__file__), "..", "skyframe", "api",
                      "web", "js", "walldraw_geom.js")

STORIES = [
    {"name": "Story1", "elevation": 3.5, "height": 3.5},
    {"name": "Story2", "elevation": 6.7, "height": 3.2, "master": True},
    {"name": "Story3", "elevation": 9.9, "height": 3.2,
     "similar_to": "Story2"},
    {"name": "Story4", "elevation": 13.1, "height": 3.2,
     "similar_to": "Story2"},
    {"name": "Roof", "elevation": 16.3, "height": 3.2},
]


def test_alignment_offset_is_half_thickness():
    assert G.align_offset("centre", 0.3) == 0.0
    assert G.align_offset("left", 0.3) == pytest.approx(0.15)
    assert G.align_offset("right", 0.3) == pytest.approx(-0.15)
    assert G.align_offset("right", 0.3, 0.05) == pytest.approx(-0.10)
    assert G.align_offset("bogus", 0.3, -0.2) == pytest.approx(-0.2)


def test_offset_single_segment_left_normal():
    # a -> b along +X: the left normal is +Y
    assert G.offset_polyline([(0, 0), (6, 0)], 0.15) == [[0, 0.15], [6, 0.15]]
    # along +Y: the left normal is -X
    assert G.offset_polyline([(0, 0), (0, 4)], 0.1) == [[-0.1, 0], [-0.1, 4]]
    # zero offset is the identity (exact)
    assert G.offset_polyline([(0, 0), (6, 0), (6, 6)], 0) == \
        [[0, 0], [6, 0], [6, 6]]


def test_offset_l_shape_is_mitred():
    # (0,0)->(6,0)->(6,6) offset left by 0.1: the corner moves to
    # (6-0.1, 0+0.1) — intersection of y=0.1 and x=5.9
    o = G.offset_polyline([(0, 0), (6, 0), (6, 6)], 0.1)
    assert o == [[0, 0.1], [5.9, 0.1], [5.9, 6]]
    # right side (outside of the turn): (6.1, -0.1)
    o = G.offset_polyline([(0, 0), (6, 0), (6, 6)], -0.1)
    assert o == [[0, -0.1], [6.1, -0.1], [6.1, 6]]


def test_offset_45_degree_miter_closed_form():
    # (0,0)->(4,0)->(8,4): miter at the 45 deg bend for s=1 is at
    # x = 4 - tan(22.5 deg), y = 1
    o = G.offset_polyline([(0, 0), (4, 0), (8, 4)], 1.0)
    assert o[1][0] == pytest.approx(4 - math.tan(math.radians(22.5)), abs=1e-9)
    assert o[1][1] == pytest.approx(1.0, abs=1e-9)
    # the last point is offset along the 2nd segment's left normal
    assert o[2] == pytest.approx([8 - 1 / math.sqrt(2), 4 + 1 / math.sqrt(2)],
                                 abs=1e-9)


def test_closed_rectangle_offset_inside():
    # CCW rectangle, left = inside: each corner moves diagonally inwards
    rect = [(0, 0), (6, 0), (6, 4), (0, 4)]
    o = G.offset_polyline(rect, 0.2, closed=True)
    assert o == [[0.2, 0.2], [5.8, 0.2], [5.8, 3.8], [0.2, 3.8]]
    panels = G.wall_panels(rect + [(0, 0)], closed=True, offset=0.2)
    assert len(panels) == 4
    assert panels[3] == [[0.2, 3.8], [0.2, 0.2]]


def test_reversed_segment_falls_back_to_perpendicular():
    o = G.offset_polyline([(0, 0), (6, 0), (2, 0)], 0.5)
    assert o == [[0, 0.5], [6, -0.5], [2, -0.5]]


def test_split_at_grid_lines():
    # drawn along y=0 from x=0 to x=18, grid lines x=6, 12 cross it; the
    # collinear line y=0 and the end line x=0 never split
    lines = [[(6, -1), (6, 13)], [(12, -1), (12, 13)], [(0, 0), (18, 0)],
             [(0, -1), (0, 13)], [(30, -1), (30, 13)]]
    assert G.split_at_lines((0, 0), (18, 0), lines) == \
        [[0, 0], [6, 0], [12, 0], [18, 0]]
    # a finite line that stops short of the wall does not split it
    assert G.split_at_lines((0, 0), (18, 0), [[(9, 1), (9, 5)]]) == \
        [[0, 0], [18, 0]]
    # diagonal: y = x/3 crosses x=6 at y=2
    assert G.split_at_lines((0, 0), (12, 4), [[(6, -5), (6, 5)]]) == \
        [[0, 0], [6, 2], [12, 4]]


def test_polyline_panels_with_split():
    lines = [[(x, -1), (x, 13)] for x in (0, 6, 12, 18)] + \
            [[(-1, y), (19, y)] for y in (0, 6, 12)]
    panels = G.wall_panels([(0, 0), (12, 0), (12, 12)], lines=lines)
    assert panels == [[[0, 0], [6, 0]], [[6, 0], [12, 0]],
                      [[12, 0], [12, 6]], [[12, 6], [12, 12]]]
    assert len(G.wall_panels([(0, 0), (12, 0), (12, 12)])) == 2


def test_wall_corners_with_offsets():
    c = G.wall_corners([0, 0], [6, 0], 3.5, 6.7)
    assert c == [[0, 0, 3.5], [6, 0, 3.5], [6, 0, 6.7], [0, 0, 6.7]]
    c = G.wall_corners([0, 0], [6, 0], 3.5, 6.7, 0.25, -0.6)
    assert c[0][2] == 3.75 and c[2][2] == pytest.approx(6.1)
    assert G.wall_corners([0, 0], [6, 0], 3.5, 6.7, 2.0, -1.3) is None
    assert G.wall_corners([1, 1], [1, 1], 0, 3) is None


def test_ortho_and_typed_points():
    assert G.ortho_point([0, 0], [5, 1.2]) == [5, 0]
    assert G.ortho_point([0, 0], [1.2, -5]) == [0, -5]
    # length along the cursor direction
    assert G.parse_typed("5", [1, 1], [1, 9]) == [1, 6]
    # polar
    p = G.parse_typed("6<90", [0, 0], None)
    assert p == [0, 6]
    p = G.parse_typed("2<30", [0, 0], None)
    assert p == pytest.approx([math.sqrt(3), 1.0], abs=1e-9)
    # relative and absolute
    assert G.parse_typed("@3,4", [1, 1]) == [4, 5]
    assert G.parse_typed("12, 6", [1, 1]) == [12, 6]
    # display units: feet -> metres
    assert G.parse_typed("10", [0, 0], [1, 0], 0.3048) == [3.048, 0]
    for bad in ("", "abc", "-3", "0", "@1", "1<", "1,2,3", "@0,0"):
        assert G.parse_typed(bad, [0, 0], [1, 0]) is None, bad


def test_quick_draw_grid_segments_and_picking():
    segs = G.grid_segments([0, 6, 12], [0, 8])
    # 3 x-lines x 1 bay + 2 y-lines x 2 bays
    assert len(segs) == 7
    assert segs[0] == [[0, 0], [0, 8]]
    assert [[6, 8], [12, 8]] in segs
    # click near the middle of grid line y=8 between x=6 and 12
    i = G.nearest_seg(segs, (9.1, 7.8), 0.3)
    assert segs[i] == [[6, 8], [12, 8]]
    assert G.nearest_seg(segs, (9, 4), 0.3) == -1
    # box over the left bay picks its four edges only
    box = G.segs_in_box(segs, [(-1, -1), (7, 9)])
    assert sorted(box) == sorted([
        [[0, 0], [0, 8]], [[6, 0], [6, 8]], [[0, 0], [6, 0]],
        [[0, 8], [6, 8]]])
    # rotated grid: T applied to every end
    rot = G.grid_segments([0, 6], [0], lambda x, y: (-y, x))
    assert rot == [[[0, 0], [0, 6]]]


def test_beam_segments_of_story():
    model = {"members": [
        {"uid": "B1", "kind": "beam", "story": "S1", "pi": [0, 0, 3],
         "pj": [6, 0, 3]},
        {"uid": "C1", "kind": "column", "story": "S1", "pi": [0, 0, 0],
         "pj": [0, 0, 3]},
        {"uid": "B2", "kind": "beam", "story": "S2", "pi": [0, 0, 6],
         "pj": [6, 0, 6]}]}
    assert G.beam_segs(model, "S1") == [[[0, 0], [6, 0], "B1"]]


def test_story_replication_scopes():
    assert G.scope_stories(STORIES, "Story3", "one") == ["Story3"]
    assert G.scope_stories(STORIES, "Story3", "similar") == \
        ["Story2", "Story3", "Story4"]
    assert G.scope_stories(STORIES, "Story1", "similar") == ["Story1"]
    assert G.scope_stories(STORIES, "Story1", "all") == \
        [s["name"] for s in STORIES]


def test_build_walls_replicates_and_dedupes():
    panels = G.wall_panels([(0, 0), (6, 0), (6, 6), (0, 6)])
    names = G.scope_stories(STORIES, "Story3", "similar")
    walls = G.build_walls(panels, STORIES, names, 0.0, -0.5)
    assert len(walls) == 9
    w = [x for x in walls if x["story"] == "Story2"][0]
    assert w["corners"] == [[0, 0, 3.5], [6, 0, 3.5], [6, 0, 6.2],
                            [0, 0, 6.2]]
    # an existing wall drawn the other way round is a duplicate
    existing = [{"kind": "wall", "story": "Story2", "corners": [
        [6, 0, 3.5], [0, 0, 3.5], [0, 0, 6.2], [6, 0, 6.2]]}]
    assert len(G.build_walls(panels, STORIES, names, 0.0, -0.5,
                             existing)) == 8
    # drawing the same panel twice in one call creates it once
    assert len(G.build_walls(panels + panels[:1], STORIES, ["Story1"])) == 3


def test_next_pier():
    assert G.next_pier([]) == "P1"
    assert G.next_pier([{"pier": "P1"}, {"pier": "P3"}, {"pier": ""}]) == "P2"


def test_walls_are_engine_shells():
    """Walls built by the tool are ordinary ShellRegion walls: they load,
    validate, carry the pier label and stiffen the frame (T1 in the wall
    direction drops) — no parallel data type."""
    from skyframe.core.builder import quick_building
    from skyframe.core.model import BuildingModel
    from skyframe.engine.opensees_engine import OpenSeesEngine

    base = quick_building(stories=2, bays_x=1, bays_y=1)
    d = base.to_dict()
    d.setdefault("shell_sections", {})["WALL200"] = {
        "name": "WALL200", "material": "CONC", "thickness": 0.2}
    names = G.scope_stories(d["stories"], "Story1", "all")
    # drawn along grid line 1 (y = 0) and 2 (y = 6), centred
    panels = G.wall_panels([(0, 0), (6, 0)]) + G.wall_panels([(6, 6), (0, 6)])
    walls = G.build_walls(panels, d["stories"], names)
    assert len(walls) == 4
    pier = G.next_pier(d.get("shells", []))
    for i, w in enumerate(walls):
        d["shells"].append({"uid": f"W{i + 1}", "kind": "wall",
                            "behavior": "shell", "section": "WALL200",
                            "corners": w["corners"], "story": w["story"],
                            "mesh_size": 1.5, "openings": [], "pier": pier})
    m2 = BuildingModel.from_dict(d)
    m2.validate()
    ws = [s for s in m2.shells if s.kind == "wall"]
    assert len(ws) == 4 and all(s.pier == "P1" for s in ws)
    t_frame = OpenSeesEngine(base).run_modal(3)
    t_wall = OpenSeesEngine(m2).run_modal(3)
    # the walls lie in X: the X-dominant period must fall a lot
    def tx(res):
        return max(zip(res.participation, res.periods),
                   key=lambda pp: pp[0]["ux"])[1]
    assert tx(t_wall) < 0.5 * tx(t_frame)


NODE = shutil.which("node") or ("/opt/node22/bin/node"
                                if os.path.exists("/opt/node22/bin/node")
                                else None)


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_js_module_matches_python_mirror(tmp_path):
    lines = [[[x, -1], [x, 13]] for x in (0, 6, 12, 18)] + \
            [[[-1, y], [19, y]] for y in (0, 6, 12)]
    cases = {
        "offset": [[[[0, 0], [6, 0], [6, 6]], 0.1, False],
                   [[[0, 0], [4, 0], [8, 4]], 1.0, False],
                   [[[0, 0], [6, 0], [6, 4], [0, 4]], 0.2, True],
                   [[[0, 0], [6, 0], [2, 0]], 0.5, False],
                   [[[1, 1], [7.3, 2.9], [3.1, 8.4], [-2, 5]], -0.17, True]],
        "split": [[[0, 0], [18, 0]], [[0, 0], [12, 4]], [[3, -0.5], [3, 12.5]]],
        "typed": [["5", [1, 1], [1, 9], 1], ["2<30", [0, 0], None, 1],
                  ["@3,4", [1, 1], None, 1], ["12,6", [0, 0], None, 1],
                  ["10", [0, 0], [3, 4], 0.3048], ["abc", [0, 0], None, 1]],
        "info": [[[0, 0], [3, 4]], [[0, 0], [0, -2]], [[1, 1], [0, 1]]],
    }
    script = tmp_path / "run.mjs"
    url = "file://" + os.path.abspath(WEB_JS)
    script.write_text(
        f"import G from {json.dumps(url)};\n"
        f"const C = {json.dumps(cases)};\n"
        f"const LINES = {json.dumps(lines)};\n"
        f"const ST = {json.dumps(STORIES)};\n"
        "const segs = G.gridSegments([0, 6, 12], [0, 8]);\n"
        "const panels = G.wallPanels([[0,0],[12,0],[12,12]], {lines: LINES, offset: 0.1});\n"
        "const out = {\n"
        " offset: C.offset.map(([p, s, c]) => G.offsetPolyline(p, s, c)),\n"
        " split: C.split.map(([a, b]) => G.splitAtLines(a, b, LINES)),\n"
        " typed: C.typed.map(([t, f, w, k]) => G.parseTyped(t, f, w, k)),\n"
        " info: C.info.map(([a, b]) => G.segInfo(a, b)),\n"
        " segs, near: G.nearestSeg(segs, [9.1, 7.8], 0.3),\n"
        " box: G.segsInBox(segs, [[-1, -1], [7, 9]]),\n"
        " panels, scope: G.scopeStories(ST, 'Story4', 'similar'),\n"
        " walls: G.buildWalls(panels, ST, ['Story2', 'Story3'], 0.1, -0.4),\n"
        " pier: G.nextPier([{pier: 'P1'}, {pier: 'P2'}]),\n"
        "};\n"
        "console.log(JSON.stringify(out));\n")
    res = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                         timeout=60)
    assert res.returncode == 0, res.stderr
    js = json.loads(res.stdout)
    segs = G.grid_segments([0, 6, 12], [0, 8])
    panels = G.wall_panels([[0, 0], [12, 0], [12, 12]], lines=lines,
                           offset=0.1)
    py = {
        "offset": [G.offset_polyline(p, s, c) for p, s, c in cases["offset"]],
        "split": [G.split_at_lines(a, b, lines) for a, b in cases["split"]],
        "typed": [G.parse_typed(t, f, w, k) for t, f, w, k in cases["typed"]],
        "info": [G.seg_info(a, b) for a, b in cases["info"]],
        "segs": segs, "near": G.nearest_seg(segs, [9.1, 7.8], 0.3),
        "box": G.segs_in_box(segs, [[-1, -1], [7, 9]]),
        "panels": panels,
        "scope": G.scope_stories(STORIES, "Story4", "similar"),
        "walls": G.build_walls(panels, STORIES, ["Story2", "Story3"], 0.1,
                               -0.4),
        "pier": G.next_pier([{"pier": "P1"}, {"pier": "P2"}]),
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
        assert close(json.loads(json.dumps(py[k])), js[k]), (k, py[k], js[k])
