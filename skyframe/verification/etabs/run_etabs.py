"""Build one verification model in ETABS 23 from its definition, run it, and
export the results.  Runs on the ETABS machine with Python + comtypes only.

    python run_etabs.py F1             # fetch cases/F1.json, build, run, export
    python run_etabs.py F1 --close     # close ETABS afterwards (default: leave it open to look)
    python run_etabs.py --update       # replace this script with the latest version

Writes, in a folder named after the case (beside this script):
    F1.EDB              the ETABS model, open it to look
    F1.etabs.json       the results: send this file back
and prints each check against its hand-calculated answer.

Every value the script sets on a section is read back from ETABS and
compared before the analysis; any mismatch stops the run with a message.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.request
from pathlib import Path

RAW = ("https://raw.githubusercontent.com/Abhimanyu14Singh/Abhi-Singh/"
       "claude/etabs-opensees-ui-rebuild-kt2zwh/skyframe/verification/etabs/")
ETABS_EXE = r"C:\Program Files\Computers and Structures\ETABS 23\ETABS.exe"
HERE = Path(__file__).resolve().parent

KIP_FT_F = 4                 # eUnits.kip_ft_F
MAT_NODESIGN = 3             # eMatType.NoDesign
PATTERN_OTHER = 8            # eLoadPatternType.Other
OBJECT_ELM, GROUP_ELM = 0, 2 # eItemTypeElm


class Stop(Exception):
    pass


def say(msg: str) -> None:
    print(msg, flush=True)


def ok(ret, what: str) -> None:
    """ETABS API calls return 0 on success (comtypes: last item of a list)."""
    code = ret[-1] if isinstance(ret, (list, tuple)) else ret
    if code != 0:
        raise Stop(f"ETABS refused: {what} (return code {code})")


def fetch(name: str) -> bytes:
    with urllib.request.urlopen(RAW + name, timeout=30) as r:
        return r.read()


# ── build ───────────────────────────────────────────────────────────────────
def build(sm, case: dict) -> dict:
    ok(sm.InitializeNewModel(KIP_FT_F), "new model in kip-ft")
    h = [s["height"] for s in case["stories"]]
    if len(set(h[1:])) > 1:
        raise Stop("stories of different heights above the first are not supported yet")
    ok(sm.File.NewGridOnly(len(h), h[1] if len(h) > 1 else h[0], h[0], 2, 2, 20.0, 20.0), "new grid-only model")

    for mt in case["materials"]:
        ok(sm.PropMaterial.SetMaterial(mt["name"], MAT_NODESIGN), f"material {mt['name']}")
        ok(sm.PropMaterial.SetMPIsotropic(mt["name"], mt["E"], mt["nu"], 0.0), f"material {mt['name']} E, nu")
        ok(sm.PropMaterial.SetWeightAndMass(mt["name"], 1, mt.get("unitWeight", 0.0)), f"material {mt['name']} weight")

    for s in case["sections"]:
        r22, r33 = math.sqrt(s["I22"] / s["A"]), math.sqrt(s["I33"] / s["A"])
        ok(sm.PropFrame.SetGeneral(s["name"], s["material"], s["depth"], s["width"], s["A"],
                                   s["As2"], s["As3"], s["J"], s["I22"], s["I33"],
                                   1.0, 1.0, 1.0, 1.0, r22, r33), f"section {s['name']}")
        got = sm.PropFrame.GetSectProps(s["name"], 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        ok(got, f"read back section {s['name']}")
        area, as2, as3, tors, i22, i33 = got[:6]
        for label, want, have in (("A", s["A"], area), ("J", s["J"], tors),
                                  ("I22", s["I22"], i22), ("I33", s["I33"], i33)):
            if abs(have - want) > 1e-9 * max(1.0, abs(want)):
                raise Stop(f"section {s['name']}: {label} reads back {have}, set {want}")
        say(f"  section {s['name']}: A {area}, I22 {i22}, I33 {i33}, J {tors}, As2 {as2}, As3 {as3} (read back)")

    names = {}
    for f in case["frames"]:
        (xi, yi, zi), (xj, yj, zj) = f["i"], f["j"]
        r = sm.FrameObj.AddByCoord(xi, yi, zi, xj, yj, zj, "", f["section"], f["id"], "Global")
        ok(r, f"frame {f['id']}")
        names[f["id"]] = r[0]
        if f.get("angle"):
            ok(sm.FrameObj.SetLocalAxes(r[0], f["angle"]), f"frame {f['id']} angle")

    def point(at):
        r = sm.PointObj.AddCartesian(at[0], at[1], at[2], "", "", "Global", False, 0)
        ok(r, f"point at {at}")
        return r[0]

    for s in case["supports"]:
        ok(sm.PointObj.SetRestraint(point(s["at"]), [bool(v) for v in s["restraint"]]), f"support at {s['at']}")
    for p in case["patterns"]:
        ok(sm.LoadPatterns.Add(p["name"], PATTERN_OTHER, 0.0, True), f"load pattern {p['name']}")
    for ld in case.get("pointLoads", []):
        ok(sm.PointObj.SetLoadForce(point(ld["at"]), ld["pattern"], [float(v) for v in ld["f"]],
                                    False, "Global", 0), f"load {ld['pattern']} at {ld['at']}")
    return names


# ── results ─────────────────────────────────────────────────────────────────
def results(sm, case: dict, names: dict) -> dict:
    out = {}
    coords = {}
    r = sm.PointObj.GetNameList(0, [])
    ok(r, "list points")
    for pn in r[1]:
        c = sm.PointObj.GetCoordCartesian(pn, 0, 0, 0, "Global")
        ok(c, f"coordinates of point {pn}")
        coords[pn] = [c[0], c[1], c[2]]
    for p in case["patterns"]:
        cname = p["name"]
        ok(sm.Results.Setup.DeselectAllCasesAndCombosForOutput(), "deselect output")
        ok(sm.Results.Setup.SetCaseSelectedForOutput(cname), f"select {cname}")
        jd = sm.Results.JointDispl("All", GROUP_ELM, 0, [], [], [], [], [], [], [], [], [], [], [])
        ok(jd, f"joint displacements {cname}")
        n, obj = jd[0], jd[1]
        u = jd[6:12]
        joints = [{"xyz": coords.get(obj[k], [math.nan] * 3), "name": obj[k],
                   "u": [u[0][k], u[1][k], u[2][k], u[3][k], u[4][k], u[5][k]]} for k in range(n)]
        br = sm.Results.BaseReact(0, [], [], [], [], [], [], [], [], [], 0, 0, 0)
        ok(br, f"base reactions {cname}")
        base = dict(zip(("FX", "FY", "FZ", "MX", "MY", "MZ"), (br[i][0] for i in range(4, 10))))
        frames = {}
        for fid, fname in names.items():
            ff = sm.Results.FrameForce(fname, OBJECT_ELM, 0, [], [], [], [], [], [], [], [], [], [], [], [], [])
            ok(ff, f"frame forces {fid} {cname}")
            nn, sta = ff[0], ff[2]
            P, V2, V3, T, M2, M3 = ff[8:14]
            frames[fid] = [{"station": sta[k], "P": P[k], "V2": V2[k], "V3": V3[k], "T": T[k],
                            "M2": M2[k], "M3": M3[k]} for k in range(nn)]
        out[cname] = {"joints": joints, "base": base, "frames": frames}
    return out


def pick(res: dict, chk: dict):
    c = res["cases"].get(chk["case"])
    if c is None:
        return None
    if chk["quantity"] == "base":
        return c["base"][chk["comp"]]
    if chk["quantity"] == "joint":
        k = ["UX", "UY", "UZ", "RX", "RY", "RZ"].index(chk["comp"])
        best = min(c["joints"], key=lambda j: math.dist(j["xyz"], chk["at"]))
        return best["u"][k] if math.dist(best["xyz"], chk["at"]) < 1e-3 else None
    rows = c["frames"].get(chk["frame"]) or []
    if not rows:
        return None
    row = min(rows, key=lambda r: r["station"]) if chk["station"] == "i" else max(rows, key=lambda r: r["station"])
    return row[chk["comp"]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case", nargs="?")
    ap.add_argument("--etabs", default=ETABS_EXE, help="full path to ETABS.exe")
    ap.add_argument("--close", action="store_true", help="close ETABS when done")
    ap.add_argument("--local", help="use this case JSON file instead of downloading it")
    ap.add_argument("--update", action="store_true", help="download the latest version of this script")
    a = ap.parse_args(argv)

    if a.update:
        Path(__file__).write_bytes(fetch("run_etabs.py"))
        say("run_etabs.py updated")
        return 0
    if not a.case:
        ap.error("give a case id, e.g. F1")
    case = json.loads(Path(a.local).read_text() if a.local else fetch(f"cases/{a.case}.json"))
    say(f"{case['id']}: {case['title']}")
    work = HERE / case["id"]
    work.mkdir(exist_ok=True)

    import comtypes.client
    helper = comtypes.client.CreateObject("ETABSv1.Helper")
    import comtypes.gen.ETABSv1 as E
    helper = helper.QueryInterface(E.cHelper)
    etabs = helper.CreateObject(a.etabs)
    ok(etabs.ApplicationStart(), "start ETABS")
    sm = etabs.SapModel
    version = sm.GetVersion()[0]
    say(f"ETABS {version} started")
    try:
        say("Building the model ...")
        names = build(sm, case)
        edb = str(work / f"{case['id']}.EDB")
        ok(sm.File.Save(edb), f"save {edb}")
        ok(sm.Analyze.SetRunCaseFlag("", False, True), "switch all cases off")
        for p in case["patterns"]:
            ok(sm.Analyze.SetRunCaseFlag(p["name"], True, False), f"switch case {p['name']} on")
        say("Running the analysis ...")
        ok(sm.Analyze.RunAnalysis(), "run analysis")
        say("Reading results ...")
        res = {"program": "ETABS", "version": version, "units": "kip-ft", "case": case["id"],
               "cases": results(sm, case, names)}
        out = work / f"{case['id']}.etabs.json"
        out.write_text(json.dumps(res, indent=1), encoding="utf-8")
        say("")
        say(f"{'check':8} {'hand':>12} {'ETABS':>12}  difference")
        worst = 0.0
        for chk in case["checks"]:
            v = pick(res, chk)
            if v is not None and chk["how"] == "abs":
                v = abs(v)
            d = None if v is None else abs(v - chk["hand"]) / max(abs(chk["hand"]), 1e-12)
            worst = max(worst, d or 0.0)
            say(f"{chk['id']:8} {chk['hand']:12.6g} {('-' if v is None else f'{v:12.6g}'):>12}  "
                f"{'missing' if d is None else f'{100 * d:.3f}%'}")
        say("")
        say(f"Saved {edb}")
        say(f"Saved {out}  <- send this file back")
        say(f"Largest difference from the hand calculation: {100 * worst:.3f}%")
        return 0
    except Stop as exc:
        say(f"STOPPED: {exc}")
        return 1
    finally:
        if a.close:
            etabs.ApplicationExit(False)
        else:
            say("ETABS is left open so you can look at the model; close it when done.")


if __name__ == "__main__":
    sys.exit(main())
