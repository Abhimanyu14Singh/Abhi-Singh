"""View > Set Display Options (D2): settings schema, sparse (de)serialisation,
3D (viewext "vx") mapping and colour-by rules of js/dispopts_core.js.

The module is pure JS (no DOM); these tests run it under node and pin the
behaviour against hand-derived expectations. The browser check lives in
tests/e2e/display_options.e2e.mjs (opt-in: SKYFRAME_E2E=1)."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_JS = os.path.join(HERE, "..", "skyframe", "api", "web", "js")
CORE = os.path.abspath(os.path.join(WEB_JS, "dispopts_core.js"))
NODE = shutil.which("node") or ("/opt/node22/bin/node"
                                if os.path.exists("/opt/node22/bin/node")
                                else None)
pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def run_js(tmp_path, body: str):
    """Run `body` (JS; must assign `out`) with the core module as C."""
    script = tmp_path / "t.mjs"
    script.write_text(
        f"import * as C from {json.dumps('file://' + CORE)};\n"
        "let out;\n" + body + "\nconsole.log(JSON.stringify(out));\n")
    res = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                         timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


MODEL = {
    "stories": [{"name": "S2", "elevation": 6.4}, {"name": "S1", "elevation": 3.2}],
    "sections": {
        "COL": {"b": 0.5, "h": 0.5, "material": "C30"},
        "W14": {"shape": "w", "tf": 0.02, "tw": 0.01, "b": 0.3, "h": 0.35, "material": "S355"},
        "P1": {"shape": "pipe", "b": 0.3, "h": 0.3, "material": "S355"},
        "NP": {"segments": [{"start_section": "COL"}]},
        "SD": {"shape": "designer"},
    },
    "shell_sections": {"SLAB": {"material": "C25"}},
    "groups": {"ZED": {"members": ["B1"]}, "CORE": {"members": ["C1", "B1"], "color": "#123456"}},
    "members": [
        {"uid": "C1", "kind": "column", "section": "COL", "story": "S1"},
        {"uid": "B1", "kind": "beam", "section": "W14", "story": "S2"},
    ],
    "shells": [{"uid": "F1", "kind": "slab", "section": "SLAB", "story": "S1", "behavior": "membrane"}],
}


def test_defaults_roundtrip_is_empty_and_sparse(tmp_path):
    out = run_js(tmp_path, """
      const d = C.dpDefaults();
      const s = C.dpSerialize(d);
      const t = C.dpMerge(d, {labels: {frameName: true}, other: {lineScale: 2}});
      out = {s, isDef: C.dpIsDefault(d), diff: C.dpDiff(t), ser: JSON.parse(C.dpSerialize(t)),
             back: C.dpEqual(C.dpDeserialize(C.dpSerialize(t)), t)};
    """)
    assert out["s"] == '{"v":1,"s":{}}'           # a default window stores nothing
    assert out["isDef"] is True
    assert out["diff"] == {"labels": {"frameName": True}, "other": {"lineScale": 2}}
    assert out["ser"] == {"v": 1, "s": out["diff"]}
    assert out["back"] is True


def test_normalize_clamps_and_rejects(tmp_path):
    out = run_js(tmp_path, """
      out = C.dpNormalize({objects: {beam: 0, bogus: true}, view: {colorBy: "design", shellTransp: 5},
        other: {jointSize: 99, lineScale: 0.1, background: "pink"},
        filter: {cut: {axis: "q", coord: "3.5", keep: "above"}, group: 7}});
    """)
    assert out["objects"]["beam"] is False and "bogus" not in out["objects"]
    assert out["view"]["colorBy"] == "type"        # unknown mode -> default
    assert out["view"]["shellTransp"] == 0.9       # clamped 0..0.9
    assert out["other"]["jointSize"] == 12 and out["other"]["lineScale"] == 0.5
    assert out["other"]["background"] == "theme"
    assert out["filter"]["cut"] == {"on": False, "axis": "x", "coord": 3.5, "keep": "above"}
    assert out["filter"]["group"] == ""


def test_deserialize_is_tolerant(tmp_path):
    out = run_js(tmp_path, """
      const D = C.dpDefaults();
      out = [C.dpDeserialize("{not json"), C.dpDeserialize('{"v":99,"s":{"labels":{"frameName":true}}}'),
             C.dpDeserialize(null), C.dpDeserialize({labels: {frameName: true}})]
             .map(x => C.dpEqual(x, D));
    """)
    assert out == [True, True, True, False]        # bare legacy object is accepted


def test_store_roundtrip_with_default(tmp_path):
    out = run_js(tmp_path, """
      const def = C.dpMerge(C.dpDefaults(), {view: {colorBy: "story"}});
      const plan = C.dpMerge(def, {labels: {frameName: true}});
      const elev = C.dpClone(def);
      const txt = C.dpSerializeStore({windows: {plan, elev, bogus: plan}, default: def});
      const st = C.dpDeserializeStore(txt);
      out = {raw: JSON.parse(txt), plan: C.dpEqual(st.windows.plan, plan), noElev: !("elev" in st.windows),
             def: C.dpEqual(st.default, def), junk: C.dpDeserializeStore("[]")};
    """)
    # windows are stored relative to the saved default: elev == default -> nothing
    assert out["raw"] == {"v": 1, "windows": {"plan": {"labels": {"frameName": True}}},
                          "default": {"view": {"colorBy": "story"}}}
    assert out["plan"] and out["noElev"] and out["def"]
    assert out["junk"] == {"windows": {}, "default": None}


def test_vx_mapping_is_lossless(tmp_path):
    out = run_js(tmp_path, """
      const s = C.dpMerge(C.dpDefaults(), {
        objects: {tendon: false, beam: false, joint: true},
        labels: {frameName: true, shellName: false, frameAxes: false, shellAxes: true, frameReleases: true, linkName: true},
        view: {extrude: true, fill: false, shrink: true, colorBy: "group", shellTransp: 0.3},
        other: {grid: false, bubbles: false, background: "white", jointSize: 7, lineScale: 1.5},
        filter: {group: "CORE", storyFrom: "S1", cut: {on: true, axis: "z", coord: 2, keep: "below"}}});
      const vx = C.dpToVx(s);
      out = {vx, back: C.dpEqual(C.dpFromVx(vx), s), def: C.dpToVx(C.dpDefaults()).dp,
             legacy: C.dpFromVx({show: {label: true, axes: true}})};
    """)
    vx = out["vx"]
    assert out["back"] is True
    assert vx["show"]["beam"] is False and vx["show"]["label"] is True and vx["show"]["axes"] is False
    assert vx["show"]["grid"] is False and vx["extrude"] is True and vx["colorBy"] == "group"
    assert vx["group"] == "CORE" and vx["cut"]["axis"] == "z"
    assert vx["dp"] == {"tendon": False, "shellName": False, "shellAxes": True,
                        "frameReleases": True, "linkName": True, "fill": False,
                        "shrink": True, "bubbles": False, "background": "white",
                        "jointSize": 7, "lineScale": 1.5}
    assert out["def"] is None                      # defaults add nothing to vx
    # a legacy vx state (one label flag) shows frames AND shells labelled
    lg = out["legacy"]["labels"]
    assert lg["frameName"] and lg["shellName"] and lg["frameAxes"] and lg["shellAxes"]


def test_colour_by(tmp_path):
    out = run_js(tmp_path, """
      const M = %s;
      const c1 = {type: "member", kind: "column", uid: "C1", section: "COL", story: "S1"};
      const b1 = {type: "member", kind: "beam", uid: "B1", section: "W14", story: "S2"};
      const f1 = {type: "shell", kind: "slab", uid: "F1", section: "SLAB", story: "S1", behavior: "membrane"};
      out = {
        type: C.dpColorOf(M, "type", c1),
        sec: [C.dpColorOf(M, "section", c1), C.dpColorOf(M, "section", b1), C.dpColorOf(M, "section", f1)],
        story: [C.dpColorOf(M, "story", c1), C.dpColorOf(M, "story", b1)],
        grp: [C.dpColorKey(M, "group", b1), C.dpColorOf(M, "group", b1), C.dpColorOf(M, "group", f1)],
        mat: [C.dpColorKey(M, "material", c1), C.dpColorKey(M, "material", f1)],
        prop: ["COL", "W14", "P1", "NP", "SD", "nope"].map(n => C.dpPropertyType(M, n)),
        sprop: C.dpColorKey(M, "property", f1),
        pal: C.DP_PALETTE, none: C.DP_NONE_COLOR,
        legend: C.dpLegend(M, "story").map(r => r[0]),
      };
    """ % json.dumps(MODEL))
    pal = out["pal"]
    assert out["type"] == "#5f8fc9"
    # section index: frames COL W14 P1 NP SD, then shells SLAB -> 0, 1, 5
    assert out["sec"] == [pal[0], pal[1], pal[5]]
    # story order is by elevation (S1 below S2), not by list order
    assert out["story"] == [pal[0], pal[1]]
    # first group by name (CORE < ZED) and its own colour; not in a group -> grey
    assert out["grp"] == ["grp:CORE", "#123456", out["none"]]
    assert out["mat"] == ["mat:C30", "mat:C25"]
    assert out["prop"] == ["Rectangular", "I / Wide Flange", "Pipe", "Nonprismatic",
                           "Section Designer", "General"]
    assert out["sprop"] == "prop:Slab (membrane)"
    assert out["legend"] == ["S1", "S2"]


def test_release_insertion_shrink_helpers(tmp_path):
    out = run_js(tmp_path, """
      out = {
        rel: C.dpReleases({releases: "Mi", end_releases: {j: {T: 0, M2: 0}, i: {M3: 0}}}),
        ins: [C.dpInsertionLabel({cardinal_point: 10}), C.dpInsertionLabel({cardinal_point: 8}),
              C.dpInsertionLabel({joint_offsets: {i: [0, 0, 0.1]}}),
              C.dpInsertionLabel({cardinal_point: 2, joint_offsets: {j: [0.2, 0, 0]}})],
        shr: C.dpShrinkPts([[0, 0], [10, 0]], 0.8),
        shr3: C.dpShrinkPts([[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0]], 0.5),
      };
    """)
    assert out["rel"] == {"i": ["M3"], "j": ["T", "M2"]}   # Mi token + per-DOF, no duplicate
    assert out["ins"] == ["", "CP8", "offset", "CP2 + offset"]
    assert out["shr"] == [[1, 0], [9, 0]]
    assert out["shr3"] == [[0.5, 0.5, 0], [1.5, 0.5, 0], [1.5, 1.5, 0], [0.5, 1.5, 0]]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.mark.skipif(os.environ.get("SKYFRAME_E2E") != "1",
                    reason="browser check: set SKYFRAME_E2E=1 (needs playwright + chromium)")
def test_e2e_display_options_mock(tmp_path):
    port = _free_port()
    env = dict(os.environ, SKYFRAME_MODELS_DIR=str(tmp_path), OMP_NUM_THREADS="1",
               OPENBLAS_NUM_THREADS="1")
    srv = subprocess.Popen(
        [sys.executable, "-c",
         "from skyframe.api.server import create_app; "
         f"create_app().run(host='127.0.0.1', port={port})"],
        cwd=os.path.join(HERE, ".."), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        res = subprocess.run(
            [NODE, os.path.join(HERE, "e2e", "display_options.e2e.mjs"),
             f"http://127.0.0.1:{port}", "1"],
            capture_output=True, text=True, timeout=300, env=env)
        assert res.returncode == 0, res.stdout + res.stderr
    finally:
        srv.terminate()
        srv.wait(10)
