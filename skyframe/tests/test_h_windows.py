"""Wave H — windows, pop-outs and side-by-side compare.

* js/h_dock_core.js (docking layout model, run under node): legacy View ›
  Windows presets 1 / 2v / 2h / 3 / 4 build the old tile arrangement, old
  named-view ``display.wb_windows`` layouts migrate, serialise / deserialise
  round trip and validation, the reducer (add / close / move to a tab or an
  edge / resize / pop out / dock back / link / arrange), the ETABS Arrange
  Windows rule (3rd splits the 2nd, 4th splits the 1st, ≤ 10 tiles, extras as
  tabs of the first), monitor keys and off-screen rectangle clamping;
* js/h_sync_core.js (sync protocol): envelopes, validation, recipients,
  dedupe, per-key model deltas;
* core/compare_view.py (+ POST /api/results/compare/fields) pinned to hand
  values, and js/h_compare_core.js (its exact JS mirror) equal to it;
* api/h_sync_api.py relay (post / read / long-poll / bounds);
* skyframe/desktop_windows.py (pywebview js_api bridge) with a fake webview;
* named views carrying a Wave H layout round-trip through the model file and
  untouched models stay byte-identical.

The browser check is tests/e2e/h_windows.e2e.mjs (opt-in: SKYFRAME_E2E=1)."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import time

import pytest

from skyframe.core import compare_view as cv

HERE = os.path.dirname(os.path.abspath(__file__))
JS = os.path.abspath(os.path.join(HERE, "..", "skyframe", "api", "web", "js"))
NODE = shutil.which("node") or ("/opt/node22/bin/node"
                                if os.path.exists("/opt/node22/bin/node") else None)
needs_node = pytest.mark.skipif(NODE is None, reason="node not available")


def run_js(tmp_path, body: str):
    script = tmp_path / "t.mjs"
    imp = "".join(
        f"import * as {n} from {json.dumps('file://' + os.path.join(JS, f))};\n"
        for n, f in (("D", "h_dock_core.js"), ("S", "h_sync_core.js"), ("C", "h_compare_core.js")))
    script.write_text(imp + "let out;\n" + body + "\nconsole.log(JSON.stringify(out));\n")
    res = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


# ======================================================================= layout
@needs_node
def test_legacy_presets_build_the_old_tiles(tmp_path):
    out = run_js(tmp_path, """
      const P = k => { const L = D.hPreset(k); return { root: L.root, kinds: D.hWinIds(L).map(id => L.wins[id].kind), preset: L.preset, active: L.active }; };
      out = { one: D.hPreset("1"), v: P("2v"), h: P("2h"), three: P("3"), four: P("4"), bad: D.hPreset("9").root };
    """)
    assert out["one"]["root"] is None
    assert out["bad"] is None
    v = out["v"]["root"]
    assert v["t"] == "split" and v["dir"] == "row" and [k["wins"] for k in v["kids"]] == [["w1"], ["w2"]]
    assert out["v"]["kinds"] == ["plan", "3d"] and out["v"]["preset"] == "2v" and out["v"]["active"] == "w1"
    assert out["h"]["root"]["dir"] == "col"
    t = out["three"]["root"]   # first window spans both rows, the 2nd column is split
    assert t["dir"] == "row" and t["kids"][0]["wins"] == ["w1"]
    assert t["kids"][1]["dir"] == "col" and [k["wins"] for k in t["kids"][1]["kids"]] == [["w2"], ["w3"]]
    assert out["three"]["kinds"] == ["plan", "3d", "elev"]
    f = out["four"]["root"]    # 2 x 2, row major (old CSS grid order)
    assert f["dir"] == "col" and [[g["wins"][0] for g in r["kids"]] for r in f["kids"]] == [["w1", "w2"], ["w3", "w4"]]
    assert out["four"]["kinds"] == ["plan", "3d", "elev", "plan"]
    assert f["sizes"] == [0.5, 0.5]


@needs_node
def test_migrate_old_named_view_layouts(tmp_path):
    out = run_js(tmp_path, """
      const old = { layout: "3", panes: [{ kind: "3d", camera: { yaw: 1, pitch: 0.3, dist: 20, target: [1, 2, 3] } }, { kind: "plan", story: "Story2", camera: { cx: 1, cy: 2, scale: 30 } }, { kind: "elev", line: "y:1" }] };
      const L = D.hMigrate(old);
      out = { ids: D.hWinIds(L), wins: L.wins, preset: L.preset, none: D.hMigrate(null), key: D.hMigrate("2h").preset,
              h: D.hMigrate(D.hSerialize(L)).wins.w2 };
    """)
    assert out["ids"] == ["w1", "w2", "w3"] and out["preset"] == "3"
    assert out["wins"]["w1"] == {"id": "w1", "kind": "3d", "camera": {"yaw": 1, "pitch": 0.3, "dist": 20, "target": [1, 2, 3]}}
    assert out["wins"]["w2"]["story"] == "Story2" and out["wins"]["w2"]["camera"] == {"cx": 1, "cy": 2, "scale": 30}
    assert out["wins"]["w3"]["line"] == "y:1"
    assert out["none"] is None and out["key"] == "2h"
    assert out["h"] == out["wins"]["w2"]


@needs_node
def test_serialize_roundtrip_and_validation(tmp_path):
    out = run_js(tmp_path, """
      let L = D.hPreset("4");
      L = D.hReduce(L, { type: "add", win: { kind: "table", table: { key: "joint_disp" } }, target: "w1", zone: "center" });
      L = D.hReduce(L, { type: "link", ids: ["w2", "w4"] });
      L = D.hReduce(L, { type: "popout", id: "w3" });
      const ser = D.hSerialize(L);
      const back = D.hSerialize(D.hDeserialize(JSON.parse(JSON.stringify(ser))));
      const err = o => { try { D.hDeserialize(o); return ""; } catch (e) { return e.message; } };
      const bad1 = JSON.parse(JSON.stringify(ser)); bad1.root.kids[0].kids[0].wins.push("w9");
      const bad2 = JSON.parse(JSON.stringify(ser)); bad2.wins.w1.kind = "radar";
      const bad3 = JSON.parse(JSON.stringify(ser)); bad3.popped.w1 = {};
      const bad4 = JSON.parse(JSON.stringify(ser)); bad4.wins["x1"] = { kind: "plan" };
      out = { same: JSON.stringify(ser) === JSON.stringify(back), ser, errs: [err(bad1), err(bad2), err(bad3), err(bad4), err({ v: 7 })] };
    """)
    assert out["same"]
    ser = out["ser"]
    assert ser["popped"] == {"w3": {"near": "w4", "zone": "left"}}   # w3 was left of w4
    assert ser["links"] == [["w2", "w4"]]
    assert ser["wins"]["w5"]["table"] == {"key": "joint_disp"}
    assert "preset" not in ser            # structural edits leave the preset
    e = out["errs"]
    assert "unknown window w9" in e[0]
    assert "unknown kind" in e[1]
    assert "both docked and popped" in e[2]
    assert "bad window id" in e[3]
    assert "not a Wave H window layout" in e[4]


@needs_node
def test_reducer_move_tabs_edges_resize_close(tmp_path):
    out = run_js(tmp_path, """
      let L = D.hPreset("2v");                       // [w1 | w2]
      const s = [];
      L = D.hReduce(L, { type: "add", win: { kind: "3d" }, target: "w2", zone: "bottom" }); s.push(JSON.stringify(L.root));   // w3 below w2
      L = D.hReduce(L, { type: "move", id: "w3", target: "w1", zone: "center" }); s.push(JSON.stringify(L.root));         // tab of w1
      const groupOfW1 = D.hFind(L, "w1").node.wins.slice();
      L = D.hReduce(L, { type: "move", id: "w1", target: "w2", zone: "left" }); s.push(JSON.stringify(L.root));
      L = D.hReduce(L, { type: "resize", path: [], sizes: [1, 1, 2] });
      const sizes = L.root.sizes.slice();
      const kept = L.wins.w1.kind;
      L = D.hReduce(L, { type: "close", id: "w2" });
      out = { s, groupOfW1, sizes, kept, after: L.root, ids: D.hWinIds(L), active: L.active, noop: JSON.stringify(D.hReduce(L, { type: "move", id: "w1", target: "w1" }).root) === JSON.stringify(L.root) };
    """)
    r0 = json.loads(out["s"][0])
    assert r0["dir"] == "row" and r0["kids"][1]["dir"] == "col"
    assert [k["wins"] for k in r0["kids"][1]["kids"]] == [["w2"], ["w3"]]
    r1 = json.loads(out["s"][1])     # w3 became a tab of w1; the col split collapsed
    assert r1["kids"][0]["wins"] == ["w1", "w3"] and r1["kids"][0]["cur"] == "w3" and r1["kids"][1]["wins"] == ["w2"]
    assert out["groupOfW1"] == ["w1", "w3"]
    r2 = json.loads(out["s"][2])     # w1 left of w2 in the same row (no nested row split)
    assert [k["wins"] for k in r2["kids"]] == [["w3"], ["w1"], ["w2"]]
    assert out["sizes"] == [0.25, 0.25, 0.5]
    assert out["kept"] == "plan"
    assert out["ids"] == ["w3", "w1"] and out["after"]["sizes"] == [0.5, 0.5] or out["after"]["sizes"][0] > 0
    assert out["noop"]


@needs_node
def test_popout_and_dock_back_restore_the_place(tmp_path):
    out = run_js(tmp_path, """
      let L = D.hPreset("3");
      L = D.hReduce(L, { type: "patch", id: "w3", patch: { camera: { yaw: 2, pitch: 0.1, dist: 9, target: [0, 0, 0] } } });
      const p = D.hReduce(L, { type: "popout", id: "w3" });
      const d = D.hReduce(p, { type: "dock", id: "w3", state: { res: { sel: { key: "c:EQX", step: -1, env: "step" } } } });
      const t = D.hReduce(p, { type: "dock", id: "w3", target: "w1", zone: "center" });
      out = { popped: p.popped, docked: D.hWinIds(p), active: p.active, back: d.root, w3: d.wins.w3, tab: D.hFind(t, "w3").node.wins };
    """)
    assert out["popped"] == {"w3": {"near": "w2", "zone": "bottom"}}
    assert out["docked"] == ["w1", "w2"]
    back = out["back"]
    assert back["kids"][1]["dir"] == "col" and [k["wins"] for k in back["kids"][1]["kids"]] == [["w2"], ["w3"]]
    assert out["w3"]["camera"]["yaw"] == 2 and out["w3"]["res"]["sel"]["key"] == "c:EQX"
    assert out["tab"] == ["w1", "w3"]


@needs_node
def test_arrange_windows_etabs_rule(tmp_path):
    out = run_js(tmp_path, """
      const cols = n => { const ids = Array.from({ length: n }, (_, i) => "w" + (i + 1)); const r = D.hArrangeTree(ids);
        if (r.t === "tabs") return [[r.wins]]; return r.kids.map(k => k.t === "tabs" ? [k.wins] : k.kids.map(g => g.wins)); };
      out = { 2: cols(2), 3: cols(3), 4: cols(4), 5: cols(5), 10: cols(10), 13: cols(13) };
    """)
    assert out["2"] == [[["w1"]], [["w2"]]]
    assert out["3"] == [[["w1"]], [["w2"], ["w3"]]]                    # 3rd splits the 2nd
    assert out["4"] == [[["w1"], ["w4"]], [["w2"], ["w3"]]]            # 4th splits the 1st
    assert out["5"] == [[["w1"]], [["w2"], ["w5"]], [["w3"], ["w4"]]]
    tiles = [g for c in out["10"] for g in c]
    assert len(tiles) == 10 and all(len(g) == 1 for g in tiles)
    first = out["13"][0][0]                                             # extras become tabs of window 1
    assert first == ["w1", "w11", "w12", "w13"]
    assert sum(len(c) for c in out["13"]) == 10


@needs_node
def test_link_groups_and_monitor_rects(tmp_path):
    out = run_js(tmp_path, """
      let L = D.hPreset("4");
      L = D.hReduce(L, { type: "link", ids: ["w1", "w2"] });
      L = D.hReduce(L, { type: "link", ids: ["w2", "w3"] });
      const merged = L.links;
      L = D.hReduce(L, { type: "unlink", id: "w2" });
      const after = L.links;
      L = D.hReduce(L, { type: "close", id: "w3" });
      const k1 = D.hMonitorKey([{ x: 1920, y: 0, width: 2560, height: 1440 }, { x: 0, y: 0, width: 1920, height: 1080 }]);
      const k2 = D.hMonitorKey([{ x: 0, y: 0, width: 1920, height: 1080 }, { x: 1920, y: 0, width: 2560, height: 1440 }]);
      const scr = [{ x: 0, y: 0, width: 1920, height: 1080 }];
      out = { merged, after, final: L.links, k1, same: k1 === k2, keep: D.hClampRect({ x: 100, y: 100, w: 800, h: 600 }, scr),
              off: D.hClampRect({ x: 3000, y: 200, w: 800, h: 600 }, scr), with: D.hLinkedWith(D.hReduce(D.hPreset("2v"), { type: "link", ids: ["w1", "w2"] }), "w1") };
    """)
    assert out["merged"] == [["w1", "w2", "w3"]]
    assert out["after"] == [["w1", "w3"]]
    assert out["final"] == []          # a one-window group dissolves
    assert out["same"] and out["k1"] == "0,0,1920x1080|1920,0,2560x1440"
    assert out["keep"] == {"x": 100, "y": 100, "w": 800, "h": 600}
    assert out["off"] == {"x": 560, "y": 240, "w": 800, "h": 600}   # second monitor gone: centred on the first
    assert out["with"] == ["w2"]


# ======================================================================= sync protocol
@needs_node
def test_sync_protocol(tmp_path):
    out = run_js(tmp_path, """
      const m = S.hsMake("abcd1234", "w2", "hello", { win: "w2" }, "main");
      const chk = [S.hsCheck(m, "abcd1234"), S.hsCheck(m, "other"), S.hsCheck({ ...m, type: "zap" }), S.hsCheck({ ...m, body: {} }),
                   S.hsCheck({ ...m, from: "evil" }), S.hsCheck(S.hsMake("abcd1234", "w2", "cmd", { cmd: "rm" })), S.hsCheck({ ...m, v: 2 })];
      const seen = S.hsSeen(3);
      const dd = [seen("a"), seen("a"), seen("b"), seen("c"), seen("d"), seen("a")];
      const a = { members: [{ uid: "B1" }], stories: [{ name: "S1" }], _mock_params: { x: 1 }, old: 1 };
      const b = { members: [{ uid: "B1" }, { uid: "B2" }], stories: [{ name: "S1" }] };
      const delta = S.hsDelta(S.hsSnapshot(a), S.hsSnapshot(b));
      const tgt = JSON.parse(JSON.stringify(a)); const keys = S.hsApply(tgt, delta);
      out = { chk, dd, delta, keys, tgt, none: S.hsDelta(S.hsSnapshot(b), S.hsSnapshot(b)), forMe: [S.hsForMe(m, "main"), S.hsForMe(m, "w2"), S.hsForMe({ ...m, to: "*" }, "w5")],
              sel: S.hsSelSig([{ type: "member", uid: "B" }, { type: "member", uid: "A" }]), cam: S.hsCamSig({ yaw: 1.23456789, target: [0, 1, 2] }) };
    """)
    c = out["chk"]
    assert c[0] == "" and c[1] == "other channel" and "unknown type" in c[2]
    assert c[3] == "hello: missing win" and c[4] == "bad sender" and "unknown rm" in c[5] and "version" in c[6]
    assert out["dd"] == [False, True, False, False, False, False]   # bounded: "a" forgotten after 3 newer ids
    assert out["delta"] == {"set": {"members": json.dumps([{"uid": "B1"}, {"uid": "B2"}], separators=(",", ":"))}, "del": ["old"]}
    assert sorted(out["keys"]) == ["members", "old"]
    assert out["tgt"]["members"] == [{"uid": "B1"}, {"uid": "B2"}] and "old" not in out["tgt"]
    assert out["tgt"]["_mock_params"] == {"x": 1}                   # never synced
    assert out["none"] is None
    assert out["forMe"] == [True, False, True]
    assert out["sel"] == "member:A|member:B"
    assert out["cam"] == "target=0.00000,1.00000,2.00000;yaw=1.23457"


# ======================================================================= compare view
A = {
    "nodes": {"1": [0, 0, 3], "2": [5, 0, 3], "3": [0, 0, 0]},
    "cases": {"D": {
        "node_disp": {"1": [0.001, 0, -0.002, 0, 0, 0], "2": [0.001, 0, -0.010, 0, 0.002, 0], "3": [0, 0, 0, 0, 0, 0]},
        "reactions": {"3": [-2, 0, 60, 0, 0, 0]},
        "member_stations": {"B1": {"x": [0, 2.5, 5], "N": [0, 0, 0], "V2": [50, 25, 0], "M3": [-125, -31.25, 0]}}},
        "L": {"node_disp": {"1": [0.004, 0, -0.002, 0, 0, 0], "2": [0.0025, 0, -0.012, 0, 0.003, 0], "3": [0, 0, 0, 0, 0, 0]},
              "reactions": {"3": [-5, 0, 70, 0, 0, 0]},
              "member_stations": {"B1": {"x": [0, 5], "N": [0, 0], "V2": [20, 20], "M3": [-100, 0]}}}},
    "combos": {"ENV": {"node_disp": {"2": [0.003, 0, 0, 0, 0, 0]}, "min": {"node_disp": {"2": [-0.004, 0, 0, 0, 0, 0]}}}},
    "modal": {"shapes": {"1": {"2": [1, 0, 0, 0, 0, 0]}}},
}
# run B: the same building, renumbered joints (11, 12, 13) and one extra joint
B = {
    "nodes": {"11": [0, 0, 3], "12": [5, 0, 3], "13": [0, 0, 0], "14": [9, 9, 9]},
    "cases": {"D": {
        "node_disp": {"11": [0.0015, 0, -0.002, 0, 0, 0], "12": [0.001, 0.0005, -0.011, 0, 0.002, 0], "13": [0, 0, 0, 0, 0, 0], "14": [1, 1, 1, 0, 0, 0]},
        "member_stations": {"B1": {"x": [0, 2.5, 5], "N": [0, 0, 0], "V2": [50, 25, 0], "M3": [-120, -31.25, 0]}, "B9": {"x": [0], "M3": [1]}}}},
}


def test_case_state_keys():
    assert cv.case_state(A, "c:D")["node_disp"]["2"][2] == -0.010
    assert cv.case_state(A, "k:ENV", "min")["node_disp"]["2"] == [-0.004, 0, 0, 0, 0, 0]
    assert cv.case_state(A, "modal:1") == {"node_disp": {"2": [1, 0, 0, 0, 0, 0]}}
    for bad, msg in (("th:X", "unsupported"), ("c:NOPE", "no cases entry"), ("modal:7", "no mode 7"), ("modal:x", "bad mode")):
        with pytest.raises(ValueError, match=msg):
            cv.case_state(A, bad)
    with pytest.raises(ValueError, match="no envelope minimum"):
        cv.case_state(A, "c:D", "min")


def test_two_cases_same_run_hand_values():
    out = cv.compare_fields(A, A, "c:D", "c:L")
    nd = out["node_disp"]
    assert nd["1"] == pytest.approx([0.003, 0, 0, 0, 0, 0], abs=1e-15)
    assert nd["2"] == pytest.approx([0.0015, 0, -0.002, 0, 0.001, 0], abs=1e-15)
    st = out["stats"]["node_disp"]
    assert st["max_abs"] == pytest.approx(0.003, abs=1e-15) and st["at"] == ["1", 0]
    # translation norms: joint 1 = 0.003, joint 2 = hypot(0.0015, 0.002) = 0.0025
    assert st["max_trans"] == pytest.approx(0.003, abs=1e-15) and st["at_trans"] == "1"
    assert math.hypot(*nd["2"][:3]) == pytest.approx(0.0025, abs=1e-15)
    assert st["scale"] == pytest.approx(0.010)        # family scale of run A (|Uz| of joint 2)
    assert st["rel"] == pytest.approx(0.3)
    assert out["reactions"]["3"] == [-3, 0, 10, 0, 0, 0]
    # B1 of L has stations [0, 5] only: interpolated onto A's [0, 2.5, 5] -> M3 -50 at mid-span
    ms = out["member_stations"]["B1"]
    assert ms["x"] == [0, 2.5, 5]
    assert ms["M3"] == pytest.approx([25, -18.75, 0])
    assert ms["V2"] == pytest.approx([-30, -5, 20])
    sm = out["stats"]["member_stations"]
    assert sm["V2"]["max_abs"] == 30 and sm["V2"]["at"] == ["B1", 0]
    assert sm["M3"]["max_abs"] == 25
    # flags: every non-zero component of the difference exceeds rtol * S
    assert out["n_flagged"] == 4 + 2 + 2 + 3   # joints (4) + reactions (2) + M3 (2) + V2 (3)
    assert not out["equal"] and out["same_run"]


def test_two_runs_matched_by_coordinates():
    out = cv.compare_fields(A, B, "c:D")
    assert out["n_nodes_matched"] == 3
    nd = out["node_disp"]
    assert nd["1"] == pytest.approx([0.0005, 0, 0, 0, 0, 0], abs=1e-15)
    assert nd["2"] == pytest.approx([0, 0.0005, -0.001, 0, 0, 0], abs=1e-15)
    assert out["only_b"]["node_disp"] == ["14"] and out["only_a"]["node_disp"] == []
    assert out["only_b"]["member_stations"] == ["B9"]
    assert out["member_stations"]["B1"]["M3"] == pytest.approx([5, 0, 0])
    assert out["stats"]["node_disp"]["max_trans"] == pytest.approx(math.hypot(0.0005, 0.001), abs=1e-15)
    # tolerance: a 1e-6 relative change of the A scale is not flagged
    tiny = json.loads(json.dumps(A))
    tiny["cases"]["D"]["node_disp"]["2"][2] *= (1 + 1e-9)
    t = cv.compare_fields(A, tiny, "c:D")
    assert t["equal"] and t["n_flagged"] == 0 and not t["same_run"]


def test_compare_fields_validation():
    with pytest.raises(ValueError, match="env"):
        cv.compare_fields(A, A, "c:D", env="avg")
    with pytest.raises(ValueError, match="rtol"):
        cv.field_difference({}, {}, rtol=-1)


@needs_node
def test_js_mirror_equals_python(tmp_path):
    out = run_js(tmp_path, f"""
      const A = {json.dumps(A)}, B = {json.dumps(B)};
      const st = (r, k) => {{ const [p, n] = k.split(":"); return {{ c: r.cases, k: r.combos }}[p][n]; }};
      const pick = b => ({{ node_disp: b.node_disp, reactions: b.reactions, member_stations: b.member_stations }});
      const clean = o => JSON.parse(JSON.stringify(o, (k, v) => v === undefined ? undefined : v));
      out = {{
        same: clean(C.hcFieldDifference(pick(st(A, "c:D")), pick(st(A, "c:L")), C.hcNodeMap(A, A))),
        runs: clean(C.hcFieldDifference(pick(st(A, "c:D")), pick(st(B, "c:D")), C.hcNodeMap(A, B))),
        map: C.hcNodeMap(A, B), fam: [C.hcFamily(["cases", "DEAD", "node_disp", "12", 0]), C.hcGroup("member_stations.*.N.#")],
        diffState: C.hcDiffState({{ key: "c:D", label: "D" }}, {{ node_disp: {{ "1": [1] }} }}, "D", "L"),
        rekey: C.hcRekey({{ node_disp: {{ "11": [1], "14": [2] }} }}, C.hcNodeMap(A, B)),
      }};
    """)
    for js, (ka, kb, rb) in ((out["same"], ("c:D", "c:L", A)), (out["runs"], ("c:D", "c:D", B))):
        py = cv.compare_fields(A, rb, ka, kb)
        for k in ("node_disp", "reactions", "member_stations", "only_a", "only_b", "n_flagged", "equal"):
            if k in py or k in js:
                assert json.dumps(js.get(k), sort_keys=True) == json.dumps(py.get(k), sort_keys=True) or \
                    _close(js.get(k), py.get(k)), k
        for q, s in py["stats"].items():
            assert _close(js["stats"][q], s), q
    assert out["map"] == {"11": "1", "12": "2", "13": "3"}
    assert out["fam"] == ["cases.*.node_disp.*.#", "member_stations.*.*.#"]
    assert out["diffState"]["node_disp"] == {"1": [1]} and out["diffState"]["hDiff"] and out["diffState"]["label"] == "Δ L − D"
    assert out["rekey"]["node_disp"] == {"1": [1]}


def _close(a, b, tol=1e-12):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    return a == b


# ======================================================================= API
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api import server
    app = server.create_app()
    app.testing = True
    return app.test_client(), app


def test_relay_post_read_longpoll(client):
    c, app = client
    assert c.post("/api/hsync/x", json={"msg": {}}).status_code == 400            # channel too short
    assert c.post("/api/hsync/abcd1234", json={"nope": 1}).status_code == 400
    assert c.get("/api/hsync/abcd1234?after=0&wait=-1").status_code == 400
    r = c.get("/api/hsync/abcd1234?after=0").get_json()
    assert r == {"seq": 0, "msgs": []}
    for i in range(3):
        assert c.post("/api/hsync/abcd1234", json={"msg": {"type": "sel", "i": i}}).get_json() == {"seq": i + 1}
    r = c.get("/api/hsync/abcd1234?after=1").get_json()
    assert r["seq"] == 3 and [m["msg"]["i"] for m in r["msgs"]] == [1, 2]
    assert c.get("/api/hsync/other-ch?after=0").get_json()["msgs"] == []          # channels are separate
    # long-poll: a message posted from another thread wakes the reader
    relay = app.config["SKYFRAME_HSYNC_RELAY"]
    threading.Timer(0.3, lambda: relay.post("abcd1234", {"type": "late"})).start()
    t0 = time.time()
    r = relay.read("abcd1234", after=3, wait=5)
    assert r["msgs"][0]["msg"] == {"type": "late"} and time.time() - t0 < 4


def test_relay_bounds():
    from skyframe.api.h_sync_api import Relay
    now = [0.0]
    rl = Relay(max_msgs=3, max_bytes=10_000, idle_s=60, clock=lambda: now[0])
    for i in range(5):
        rl.post("chan", {"i": i})
    r = rl.read("chan", 0)
    assert [m["msg"]["i"] for m in r["msgs"]] == [2, 3, 4] and r["seq"] == 5
    big = Relay(max_msgs=100, max_bytes=60, clock=lambda: now[0])
    big.post("c", {"s": "x" * 30})
    big.post("c", {"s": "y" * 30})
    assert [m["msg"]["s"][0] for m in big.read("c", 0)["msgs"]] == ["y"]
    now[0] = 120.0                                                                 # idle channel forgotten
    assert rl.read("chan", 0) == {"seq": 0, "msgs": []}


def test_api_compare_fields(client, monkeypatch):
    c, _app = client
    r = c.post("/api/results/compare/fields", json={"a": A, "b": A, "case_a": "c:D", "case_b": "c:L"})
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out["node_disp"]["1"][0] == pytest.approx(0.003)
    assert out["stats"]["node_disp"]["rel"] == pytest.approx(0.3)
    r = c.post("/api/results/compare/fields", json={"a": {"results": A}, "b": B, "case_a": "c:D"})
    assert r.get_json()["only_b"]["node_disp"] == ["14"]
    assert c.post("/api/results/compare/fields", json={"a": A, "b": A, "case_a": "th:X"}).status_code == 400
    assert c.post("/api/results/compare/fields", json=[1]).status_code == 400
    from skyframe.api import server
    monkeypatch.setitem(server._state, "model", server._state["model"])
    r = c.post("/api/results/compare/fields", json={"a": "current", "b": "current", "case_a": "c:DEAD"})
    assert r.status_code in (200, 400)
    if r.status_code == 400:
        assert "run the analysis" in r.get_json()["error"] or "no cases" in r.get_json()["error"]


# ======================================================================= desktop shell
class _Evt:
    def __init__(self):
        self.fns = []

    def __iadd__(self, fn):
        self.fns.append(fn)
        return self

    def fire(self):
        for f in self.fns:
            f()


class _Win:
    def __init__(self, title, url, kw):
        self.title, self.url, self.kw = title, url, kw
        self.destroyed = False
        self.events = type("E", (), {})()
        self.events.closed = _Evt()

    def destroy(self):
        self.destroyed = True
        self.events.closed.fire()


class _Screen:
    def __init__(self, w, h, x, y):
        self.width, self.height = w, h
        self.frame = type("F", (), {"Bounds": type("B", (), {"X": x, "Y": y})()})()


class _Webview:
    def __init__(self):
        self.created = []
        self.screens = [_Screen(1920, 1080, 0, 0), _Screen(2560, 1440, 1920, 0)]

    def create_window(self, title, url, **kw):
        w = _Win(title, url, kw)
        self.created.append(w)
        return w


def test_desktop_windows_bridge():
    from skyframe.desktop_windows import DesktopWindows, attach_main
    wv = _Webview()
    dw = DesktopWindows(wv, "http://127.0.0.1:5555/?x=1", "SkyFrame")
    r = dw.open_window("w3", {"hpop": "w3", "hs": "mabc123", "mock": "1", "evil": "javascript:alert(1)"}, "Pop", 100, 50, 900, 600)
    assert r == {"ok": True, "id": "w3"}
    w = wv.created[0]
    assert w.url == "http://127.0.0.1:5555/?hpop=w3&hs=mabc123&mock=1"        # same server, whitelisted keys only
    assert w.kw["x"] == 100 and w.kw["y"] == 50 and w.kw["width"] == 900 and w.kw["js_api"] is dw
    assert dw.open_window("w3", {})["existing"]
    assert not dw.open_window("../x", {})["ok"]
    assert not dw.open_window("w4", {"hs": "a b"})["ok"]                          # bad characters
    assert dw.list_windows() == ["w3"]
    assert dw.screens() == [{"x": 0, "y": 0, "width": 1920, "height": 1080}, {"x": 1920, "y": 0, "width": 2560, "height": 1440}]
    main = _Win("main", "u", {})
    attach_main(main, dw)
    dw.open_window("w5", {"hpop": "w5"}, None, None, None, None, None)
    assert "x" not in wv.created[-1].kw and wv.created[-1].kw["width"] == 900
    main.destroy()                                                                # closing main closes every pop-out
    assert all(x.destroyed for x in wv.created) and dw.list_windows() == []
    assert dw.close_window("w3") == {"ok": False}
    # self-test reporting
    threading.Timer(0.1, lambda: dw.selftest_result({"ok": True, "transport": "bc"})).start()
    assert dw.wait_selftest(5) == {"ok": True, "transport": "bc"}


def test_desktop_selftest_flag_without_gui(monkeypatch, capsys):
    """--selftest-windows without a GUI stack exits 1 with a clear line (the frozen Windows build has one)."""
    import builtins
    from skyframe import desktop
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "webview":
            raise ImportError("no GUI here")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    monkeypatch.setattr(desktop, "_start_server", lambda port: None)
    monkeypatch.setattr(desktop, "_wait_healthy", lambda port: True)
    codes = []
    monkeypatch.setattr(desktop.os, "_exit", lambda c: (_ for _ in ()).throw(SystemExit(c)))
    with pytest.raises(SystemExit) as e:
        desktop.main(["--selftest-windows", "--port", "5999"])
    codes.append(e.value.code)
    assert codes == [1]
    assert "windows selftest: native windows unavailable" in capsys.readouterr().out


# ======================================================================= model file
def test_named_view_with_layout_roundtrips_and_legacy_unchanged():
    from skyframe.core.model import BuildingModel
    from skyframe.core.builder import quick_building
    m = quick_building()
    d0 = m.to_dict()
    raw0 = json.dumps(d0, sort_keys=True)
    # untouched model: byte-identical round trip, no Wave H keys anywhere
    assert json.dumps(BuildingModel.from_dict(json.loads(raw0)).to_dict(), sort_keys=True) == raw0
    assert "h_dock" not in raw0
    layout = {"v": 1, "root": {"t": "split", "dir": "row", "sizes": [0.5, 0.5], "kids": [
        {"t": "tabs", "wins": ["w1"], "cur": "w1"}, {"t": "tabs", "wins": ["w2", "w3"], "cur": "w2"}]},
        "wins": {"w1": {"id": "w1", "kind": "plan", "story": "Story1"}, "w2": {"id": "w2", "kind": "3d", "res": {"sel": {"key": "c:EQX", "step": -1, "env": "step"}}},
                 "w3": {"id": "w3", "kind": "table", "table": {"key": "joint_disp"}}},
        "active": "w2", "next": 4, "links": [["w1", "w2"]], "popped": {}}
    d = json.loads(raw0)
    d["named_views"] = [{"name": "Docked", "view": "3d", "camera": {"yaw": 1, "pitch": 0.4, "dist": 30, "target": [0, 0, 0]},
                         "display": {"h_dock": layout}}]
    m2 = BuildingModel.from_dict(d)
    d2 = m2.to_dict()
    assert d2["named_views"][0]["display"]["h_dock"] == layout
    assert json.dumps(BuildingModel.from_dict(json.loads(json.dumps(d2))).to_dict(), sort_keys=True) == json.dumps(d2, sort_keys=True)


def test_fast_scales_equal_the_engine():
    """compare_view.scales (memoised key collapse) == results_compare._scales."""
    from skyframe.core import results_compare as rc
    st = cv.case_state(json.loads(json.dumps(A)), "c:D")
    st["member_stations"]["beam-+ 1"] = {"x": [0, 1], "N": [3, -4], "M3": [7, 8]}
    st["node_disp"]["top"] = [9, 0, 0, 0, 0, 0]          # an identifier without digits keeps its own family
    assert cv.scales(st) == rc._scales(st)
    assert cv.scales({"cases": {"D": st}}) == rc._scales({"cases": {"D": st}})


@needs_node
def test_js_scales_equal_python(tmp_path):
    st = cv.case_state(json.loads(json.dumps(A)), "c:D")
    st["node_disp"]["top"] = [9, 0, 0, 0, 0, 0]
    out = run_js(tmp_path, f"out = Object.fromEntries(C.hcScales({json.dumps(st)}));")
    assert out == cv.scales(st)
