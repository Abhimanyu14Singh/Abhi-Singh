"""Wave G results model core (js/g_core.js, pure JS) — run under node and
pinned against hand-derived values: case catalogue, step / envelope
extraction (incl. the ETABS envelope deformed shape and positive-only RS),
controlling stations, values at a station, projection of frame diagrams
into plan / elevation planes (in-plane components), select-by-result
filters, result probes, equivalent loads and member deflection.

The browser check lives in tests/e2e/g_results.e2e.mjs (opt-in:
SKYFRAME_E2E=1)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.abspath(os.path.join(HERE, "..", "skyframe", "api", "web",
                                    "js", "g_core.js"))
NODE = shutil.which("node") or ("/opt/node22/bin/node"
                                if os.path.exists("/opt/node22/bin/node")
                                else None)
pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def run_js(tmp_path, body: str):
    script = tmp_path / "t.mjs"
    script.write_text(
        f"import * as G from {json.dumps('file://' + CORE)};\n"
        "let out;\n" + body + "\nconsole.log(JSON.stringify(out));\n")
    res = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                         timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


# A 2-node cantilever beam along X (5 m) and a column, results by hand.
RESULTS = {
    "nodes": {"1": [0, 0, 3], "2": [5, 0, 3], "3": [0, 0, 0]},
    "members": [{"uid": "B1", "ni": "1", "nj": "2"}, {"uid": "C1", "ni": "3", "nj": "1"}],
    "supports": ["3"],
    "story_elev": {"S1": 3},
    "cases": {"D": {
        "node_disp": {"1": [0.001, 0, -0.002, 0, 0, 0], "2": [0.001, 0, -0.010, 0, 0.002, 0], "3": [0, 0, 0, 0, 0, 0]},
        "reactions": {"3": [-2, 0, 60, 0, 0, 0]},
        "member_stations": {
            "B1": {"x": [0, 2.5, 5], "N": [0, 0, 0], "V2": [50, 25, 0], "V3": [0, 0, 0], "T": [0, 0, 0], "M2": [0, 0, 0], "M3": [-125, -31.25, 0]},
            "C1": {"x": [0, 1.5, 3], "N": [-60, -60, -60], "V2": [2, 2, 2], "V3": [0, 0, 0], "T": [0, 0, 0], "M2": [0, 0, 0], "M3": [6, 3, 0]}},
    }},
    "combos": {"ENV": {
        "node_disp": {"1": [0.003, 0, 0, 0, 0, 0], "2": [0.004, 0, 0.001, 0, 0, 0], "3": [0, 0, 0, 0, 0, 0]},
        "member_stations": {"B1": {"x": [0, 2.5, 5], "N": [0, 0, 0], "V2": [60, 30, 0], "V3": [0, 0, 0], "T": [0, 0, 0], "M2": [0, 0, 0], "M3": [10, 2, 0]}},
        "min": {"node_disp": {"1": [-0.005, 0, -0.002, 0, 0, 0], "2": [-0.001, 0, -0.012, 0, 0, 0], "3": [0, 0, 0, 0, 0, 0]},
                "member_stations": {"B1": {"x": [0, 2.5, 5], "N": [0, 0, 0], "V2": [10, 5, 0], "V3": [0, 0, 0], "T": [0, 0, 0], "M2": [0, 0, 0], "M3": [-150, -40, 0]}}},
    }},
    "rs_cases": {"RSX": {"node_disp": {"1": [0.02, 0.001, 0, 0, 0, 0], "2": [0.02, 0, 0, 0, 0, 0], "3": [0, 0, 0, 0, 0, 0]},
                         "member_stations": {}}},
    "th_cases": {"TH": {"t": [0.1, 0.2]}},
    "modal": {"periods": [0.5, 0.2], "shapes": {"1": {"1": [1, 0, 0, 0, 0, 0]}, "2": {"1": [0, 1, 0, 0, 0, 0]}}},
}
MODEL = {
    "members": [{"uid": "B1", "kind": "beam", "pi": [0, 0, 3], "pj": [5, 0, 3], "story": "S1"},
                {"uid": "C1", "kind": "column", "pi": [0, 0, 0], "pj": [0, 0, 3], "story": "S1"}],
    "stories": [{"name": "S1", "elevation": 3, "height": 3}],
    "groups": {"G": {"members": ["C1"]}},
    "shells": [], "links": [],
}
STEPS = {"case": "TH", "kind": "th", "n_steps": 2, "steps": [1, 2],
         "axis": {"name": "t", "values": [0.1, 0.2]},
         "node_disp": {"1": {"1": [0.01, 0, 0, 0, 0, 0], "2": [0.01, 0, 0, 0, 0, 0]},
                       "2": {"1": [-0.02, 0, 0, 0, 0, 0], "2": [-0.03, 0, 0, 0, 0, 0]}},
         "reactions": {"1": {}, "2": {}},
         "member_stations": {"1": {"B1": {"x": [0, 5], "N": [0, 0], "V2": [1, 1], "V3": [0, 0], "T": [0, 0], "M2": [0, 0], "M3": [5, 0]}},
                             "2": {"B1": {"x": [0, 5], "N": [0, 0], "V2": [-3, -3], "V3": [0, 0], "T": [0, 0], "M2": [0, 0], "M3": [-15, 0]}}},
         "envelope": {"max": {"node_disp": {"1": [0.01, 0, 0, 0, 0, 0], "2": [0.01, 0, 0, 0, 0, 0]}, "reactions": {},
                              "member_stations": {"B1": {"x": [0, 5], "N": [0, 0], "V2": [1, 1], "V3": [0, 0], "T": [0, 0], "M2": [0, 0], "M3": [5, 0]}}},
                      "min": {"node_disp": {"1": [-0.02, 0, 0, 0, 0, 0], "2": [-0.03, 0, 0, 0, 0, 0]}, "reactions": {},
                              "member_stations": {"B1": {"x": [0, 5], "N": [0, 0], "V2": [-3, -3], "V3": [0, 0], "T": [0, 0], "M2": [0, 0], "M3": [-15, 0]}}},
                      "stride": 1}}
SETUP = f"const R = {json.dumps(RESULTS)}; const M = {json.dumps(MODEL)}; const SD = {json.dumps(STEPS)};\n"


def test_catalog_keys_kinds_and_signs(tmp_path):
    out = run_js(tmp_path, SETUP + "out = G.caseCatalog(R).map(e => [e.key, e.kind, e.multi, e.signed, e.minmax]);")
    assert out == [["c:D", "static", False, True, False], ["k:ENV", "minmax", False, True, True],
                   ["rs:RSX", "rs", False, False, False], ["th:TH", "th", True, True, False],
                   ["modal", "modal", True, True, False]]


def test_envelope_extraction_and_etabs_envelope_shape(tmp_path):
    out = run_js(tmp_path, SETUP + """
      const mx = G.extractState(R, {key: "k:ENV", env: "max"});
      const mn = G.extractState(R, {key: "k:ENV", env: "min"});
      const both = G.extractState(R, {key: "k:ENV", env: "maxmin"});
      out = {mx: mx.node_disp["2"], mn: mn.node_disp["2"], both: both.node_disp, pair: !!both.member_stations_min, env: both.env};""")
    assert out["mx"] == [0.004, 0, 0.001, 0, 0, 0]
    assert out["mn"] == [-0.001, 0, -0.012, 0, 0, 0]
    # per DOF the larger magnitude, sign kept: node 1 ux max 0.003 / min -0.005 -> -0.005
    assert out["both"]["1"][0] == -0.005 and out["both"]["2"][0] == 0.004
    assert out["both"]["2"][2] == -0.012
    assert out["pair"] and out["env"] == "maxmin"


def test_rs_is_positive_envelope_never_signed(tmp_path):
    out = run_js(tmp_path, SETUP + """
      const s = G.extractState(R, {key: "rs:RSX", env: "min"});
      out = {d: s.node_disp["1"], signed: s.signed, env: s.env, env_ok: s.envAvailable, note: s.note};""")
    assert out["d"] == [0.02, 0.001, 0, 0, 0, 0]          # unchanged, no sign invented
    assert out["signed"] is False and out["env"] == "step" and out["env_ok"] is False
    assert "positive" in out["note"]


def test_step_extraction_and_step_envelope(tmp_path):
    out = run_js(tmp_path, SETUP + """
      const s1 = G.extractState(R, {key: "th:TH", step: 0, env: "step"}, SD);
      const s2 = G.extractState(R, {key: "th:TH", step: 1, env: "step"}, SD, {fmt: (k, v) => v.toFixed(2) + " s"});
      const last = G.extractState(R, {key: "th:TH", step: -1, env: "step"}, SD);
      const env = G.extractState(R, {key: "th:TH", step: 0, env: "maxmin"}, SD);
      const ax = G.stepAxis(R, G.catalogEntry(R, "th:TH"), SD, {fmt: (k, v) => v.toFixed(2) + " s"});
      out = {u1: s1.node_disp["2"][0], u2: s2.node_disp["2"][0], last: last.stepIndex, lab: s2.stepLabel,
             envU: env.node_disp["2"][0], envMin: env.member_stations_min.B1.M3[0], labels: ax.labels, src: ax.source};""")
    assert out["u1"] == 0.01 and out["u2"] == -0.03 and out["last"] == 1
    assert out["lab"] == "t = 0.20 s" and out["labels"] == ["t = 0.10 s", "t = 0.20 s"]
    assert out["envU"] == -0.03 and out["envMin"] == -15 and out["src"] == "steps"


def test_envelope_maps_and_station_envelope(tmp_path):
    out = run_js(tmp_path, """
      const e = G.envelopeMaps([{a: [1, -2]}, {a: [-3, 5]}, {a: [2, 0]}]);
      const s = G.stationsEnvelope([{m: {x: [0, 1], N: [1, 2], V2: [0, 0], V3: [0, 0], T: [0, 0], M2: [0, 0], M3: [4, -1]}},
                                    {m: {x: [0, 1], N: [-1, 3], V2: [0, 0], V3: [0, 0], T: [0, 0], M2: [0, 0], M3: [-6, 2]}}]);
      out = {max: e.max.a, min: e.min.a, smax: s.max.m.M3, smin: s.min.m.M3, nmax: s.max.m.N};""")
    assert out == {"max": [2, 5], "min": [-3, -2], "smax": [4, 2], "smin": [-6, -1], "nmax": [1, 3]}


def test_controlling_stations_and_values(tmp_path):
    out = run_js(tmp_path, """
      const xs = [0, 1, 2, 3, 4];
      out = {
        sag: G.controllingStations(xs, [0, 30, 40, 30, 0]),          // interior max
        hog: G.controllingStations(xs, [-50, 10, 25, 10, -50]),      // ends + interior max
        mono: G.controllingStations(xs, [10, 20, 30, 40, 50]),      // extremes at the ends only
        both: G.controllingStations(xs, [0, 12, -8, 3, 0]),          // interior max and min
        at: [G.valueAt(xs, [0, 30, 40, 30, 0], 2), G.valueAt(xs, [0, 30, 40, 30, 0], 2.5), G.valueAt(xs, [0, 30, 40, 30, 0], -1)],
        mx: G.maxOf(xs, [-50, 10, 25, 10, -60]),
        q: [G.memberQuantity({M3: [-50, 10, 25]}, "M3"), G.memberQuantity({M3: [-50, 10, 25]}, "M3", "max"), G.memberQuantity({M3: [-50, 10, 25]}, "M3", "min", {M3: [-70]})],
      };""")
    assert out["sag"] == [0, 2, 4]
    assert out["hog"] == [0, 2, 4]
    assert out["mono"] == [0, 4]
    assert out["both"] == [0, 1, 2, 4]
    assert out["at"] == [40, 35, 0]
    assert out["mx"] == {"i": 4, "x": 4, "v": -60}
    assert out["q"] == [50, 25, -70]


def test_projection_into_plan_and_elevation(tmp_path):
    out = run_js(tmp_path, """
      const beam = {pi: [0, 0, 3], pj: [5, 0, 3]};
      const ax = {x: [1, 0, 0], y: [0, 0, 1], z: [0, -1, 0]};    // horizontal beam, local 2 up
      const plan = G.viewBasis({kind: "plan", z: 3}), elevY = G.viewBasis({kind: "elev", axis: "y", coord: 0});
      const elevX = G.viewBasis({kind: "elev", axis: "x", coord: 0});
      // M3 (bending about local 3 = vertical plane) in the elevation y = 0 is in-plane:
      const e1 = G.projectDiagram(beam, ax, [0, 5], [10, -10], "M3", 0.1, elevY);
      // in plan the M3 diagram (local 2 = vertical) is rotated into the plane: perpendicular to the beam
      const p1 = G.projectDiagram(beam, ax, [0, 5], [10, -10], "M3", 0.1, plan);
      // 3D: along local 2 (up), moments on the tension side
      const d3 = G.projectDiagram(beam, ax, [0, 5], [10, -10], "M3", 0.1, null);
      out = {
        e1: e1.tip, p1: p1.tip, d3: d3.tip,
        inPlaneElev: [G.resolveComp("MIN", ax, elevY), G.resolveComp("VIN", ax, elevY)],
        inPlanePlan: [G.resolveComp("MIN", ax, plan), G.resolveComp("VIN", ax, plan)],
        onPlane: [G.memberInView(plan, beam.pi, beam.pj), G.memberInView(elevX, beam.pi, beam.pj), G.memberInView(elevY, beam.pi, beam.pj)],
        uv: G.toPlane(elevX, [2, 7, 3]),
      };""")
    assert out["e1"] == [[0, 0, 2], [5, 0, 4]]                # -M x k along +z (tension side)
    # n x e1 = z x x = +y; flipped to agree with local 2 projection (0) -> +y kept
    assert out["p1"] == [[0, -1, 3], [5, 1, 3]]
    assert out["d3"] == [[0, 0, 2], [5, 0, 4]]
    assert out["inPlaneElev"] == ["M3", "V2"]                 # bending in the x-z plane
    assert out["inPlanePlan"] == ["M2", "V3"]                 # bending in the plan plane
    assert out["onPlane"] == [True, False, True]
    assert out["uv"] == [7, 3]


def test_deformed_polyline_cubic_and_chord(tmp_path):
    out = run_js(tmp_path, """
      const c = G.deformedPolyline([0,0,0], [4,0,0], [0,0,0,0,0,0], [0,0,0,0,0,0.01], 1, true, 4);
      const s = G.deformedPolyline([0,0,0], [4,0,0], [0,0,0,0,0,0], [0,0.04,0,0,0,0], 1, false, 4);
      out = {c: c.map(p => +p[1].toFixed(6)), s: s.map(p => +p[1].toFixed(6))};""")
    # end rotation 0.01 about z, ends fixed: v = h4 L theta, h4 = -t^2 + t^3
    assert out["c"] == [0, round(4 * 0.01 * (-0.0625 + 0.015625), 6), round(4 * 0.01 * (-0.25 + 0.125), 6),
                        round(4 * 0.01 * (-0.5625 + 0.421875), 6), 0]
    assert out["s"] == [0, 0.01, 0.02, 0.03, 0.04]


def test_select_by_result_filters_hand_values(tmp_path):
    out = run_js(tmp_path, SETUP + """
      const st = G.extractState(R, {key: "c:D"});
      const ctx = {model: M, results: R, state: st};
      const a = G.selectByResult(ctx, {objType: "frame", quantity: "M3", mode: "absmax", op: ">=", threshold: 100});
      const b = G.selectByResult(ctx, {objType: "frame", quantity: "N", mode: "min", op: "<", threshold: -50});
      const c = G.selectByResult(ctx, {objType: "joint", quantity: "UZ", mode: "absmax", op: ">", threshold: 0.005});
      const d = G.selectByResult(ctx, {objType: "joint", quantity: "DRIFT_X", mode: "absmax", op: ">", threshold: 1e-4});
      const d2 = G.selectByResult(ctx, {objType: "joint", quantity: "DRIFT_X", mode: "absmax", op: ">", threshold: 1/400});
      const e = G.selectByResult(ctx, {objType: "frame", quantity: "V2", mode: "absmax", op: ">", threshold: 1, filter: {group: "G"}});
      const env = G.extractState(R, {key: "k:ENV", env: "maxmin"});
      const f = G.selectByResult({model: M, results: R, state: env}, {objType: "frame", quantity: "M3", mode: "absmax", op: ">=", threshold: 140});
      const g = G.selectByResult(ctx, {objType: "story", quantity: "DRIFT_X", mode: "absmax", op: ">", threshold: 1e-4});
      out = {a: a.refs, b: b.refs, c: c.rows.map(r => [r.type, r.uid, r.value]), d: d.rows.map(r => [r.uid, +r.value.toFixed(6)]), d2: d2.refs.length,
             e: e.refs, f: f.rows.map(r => [r.uid, r.value]), g: g.refs};""")
    assert out["a"] == [{"type": "member", "uid": "B1"}]                    # |M3| 125 >= 100; C1 6
    assert out["b"] == [{"type": "member", "uid": "C1"}]                    # N -60 < -50
    assert out["c"] == [["joint", "5,0,3", 0.01]]                           # |uz| 0.010 at the tip
    assert out["d"] == [["0,0,3", 0.000333]] and out["d2"] == 0     # 0.001 / 3 m drift ratio
    assert out["e"] == [{"type": "member", "uid": "C1"}]                    # group G only
    assert out["f"] == [["B1", 150]]                                        # min twin -150 counts
    assert out["g"] == [{"type": "member", "uid": "B1"}, {"type": "member", "uid": "C1"}]


def test_joint_drift_and_story_drift_hand_values(tmp_path):
    out = run_js(tmp_path, SETUP + """
      const st = G.extractState(R, {key: "c:D"});
      out = {jd: G.jointDrift(R, st.node_disp, "1", 0), sd: G.storyDrifts(R, st.node_disp)};""")
    assert out["jd"] == pytest.approx(0.001 / 3)
    assert out["sd"]["S1"]["drift_x"] == pytest.approx(0.001 / 3)


def test_probes_update_with_case_and_step(tmp_path):
    out = run_js(tmp_path, SETUP + """
      const probes = [
        {type: "joint_disp", point: [5, 0, 3], comp: "UZ"},
        {type: "member_end", uid: "B1", end: "i", comp: "M3"},
        {type: "member_at", uid: "B1", comp: "M3", x: 1.25},
        {type: "member_max", uid: "B1", comp: "M3"},
        {type: "reaction", point: [0, 0, 0], comp: "FZ"},
        {type: "joint_drift", point: [0, 0, 3], dir: "X"},
      ];
      const val = st => probes.map(p => G.probeValue({model: M, results: R, state: st}, p).value);
      out = {
        D: val(G.extractState(R, {key: "c:D"})),
        ENV: val(G.extractState(R, {key: "k:ENV", env: "maxmin"})),
        TH1: val(G.extractState(R, {key: "th:TH", step: 0}, SD)),
        TH2: val(G.extractState(R, {key: "th:TH", step: 1}, SD)),
      };""")
    assert out["D"][:5] == [-0.010, -125, -78.125, 125, 60]
    assert out["D"][5] == pytest.approx(0.001 / 3)
    assert out["ENV"][1] == 10 and out["ENV"][3] == 150          # max end-I, |max| incl. min twin
    assert out["TH1"][2] == pytest.approx(3.75) and out["TH2"][2] == pytest.approx(-11.25)
    assert out["TH2"][0] == 0


def test_equivalent_loads_and_member_deflection(tmp_path):
    out = run_js(tmp_path, """
      const eq = G.equivalentLoads([0, 2.5, 5, 5, 7.5], [50, 25, 0, -40, -40]);
      // cantilever tip rotation / translation (local 2 = z)
      const ax = {x: [1, 0, 0], y: [0, 0, 1], z: [0, -1, 0]};
      const d = G.memberDeflection([0, 0, 0], [4, 0, 0], [0, 0, 0, 0, 0, 0], [0, 0, -0.008, 0, 0.003, 0], ax, 5);
      out = {eq, d2: d.d2.map(v => +v.toFixed(7)), rel: G.relativeDeflection([0, 1, 2], [0, 1, 4]).map(v => +v.toFixed(9))};""")
    assert out["eq"] == [{"kind": "dist", "x0": 0, "x1": 2.5, "w": 10}, {"kind": "dist", "x0": 2.5, "x1": 5, "w": 10},
                         {"kind": "point", "x": 5, "P": 40}, {"kind": "dist", "x0": 5, "x1": 7.5, "w": -0}]
    # v_j = d.y = -0.008; slope = theta . z = 0.003 * -1 * ... (rot about -y) -> theta_z_local = dot([0,0.003,0],[0,-1,0]) = -0.003
    assert out["d2"][0] == 0 and out["d2"][-1] == pytest.approx(-0.008)
    assert out["rel"] == [0, -1, 0]


def test_contour_range_and_colour_clamp(tmp_path):
    out = run_js(tmp_path, """
      out = {sym: G.rangeOf([-2, 5, 1]), pos: G.rangeOf([1, 4]), user: G.rangeOf([1, 4], [0, 10]),
             t: [G.tOf(-5, [0, 10]), G.tOf(15, [0, 10]), G.tOf(5, [0, 10])],
             c: [G.valueColor(-99, [0, 1], "diverging"), G.valueColor(0, [0, 1], "diverging")]};""")
    assert out["sym"] == [-5, 5] and out["pos"] == [1, 4] and out["user"] == [0, 10]
    assert out["t"] == [0, 1, 0.5]
    assert out["c"][0] == out["c"][1]                               # below min clamps to the min colour


def test_object_filter_towers_and_groups(tmp_path):
    out = run_js(tmp_path, """
      const m = {towers: [{name: "T1"}, {name: "T2"}], stories: [{name: "A"}, {name: "B", tower: "T2"}],
        members: [{uid: "m1", story: "A"}, {uid: "m2", story: "B"}, {uid: "m3", story: "A", tower: "T2"}], groups: {G: {members: ["m1"]}}};
      const f1 = G.objectFilter(m, {towers: ["T2"]}), f2 = G.objectFilter(m, {towers: [], group: "G"}), f3 = G.objectFilter(m, {towers: ["T1", "T2"]});
      out = {t2: m.members.map(f1.member), g: m.members.map(f2.member), all: m.members.map(f3.member), act: [f1.active, f2.active]};""")
    assert out == {"t2": [False, True, True], "g": [True, False, False], "all": [True, True, True], "act": [True, True]}


def test_shell_stresses_top_bottom(tmp_path):
    out = run_js(tmp_path, """
      const f = [100, 0, 0, 2, 0, 0, 4, 0];   // F11 = 100 kN/m, M11 = 2 kN m/m, V13 = 4 kN/m, t = 0.2 m
      out = {top: G.shellStresses(f, 0.2, "top"), bot: G.shellStresses(f, 0.2, "bot"), comp: G.shellComponent(f, 0.2, "M11")};""")
    assert out["top"]["S11"] == pytest.approx(100 / 0.2 + 6 * 2 / 0.04)
    assert out["bot"]["S11"] == pytest.approx(100 / 0.2 - 6 * 2 / 0.04)
    assert out["top"]["S13"] == pytest.approx(20)
    assert out["top"]["SVM"] == pytest.approx(abs(out["top"]["S11"]))
    assert out["comp"] == 2
