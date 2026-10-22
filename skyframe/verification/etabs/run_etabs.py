r"""Build one verification model in ETABS 23 from its definition, run it, and
export the results.  Runs on the ETABS machine with Python + comtypes only.

    python run_etabs.py F1                    # one case; ETABS stays open to look
    python run_etabs.py F1 F2 F3              # several, one after another; ETABS closes at the end
    python run_etabs.py --all --push C:\SkyFrameVerification\repo
                                              # every new case, then push the results to GitHub
    python run_etabs.py --update              # replace this script with the latest version

Writes everything under C:\SkyFrameVerification (change with --out), one
folder per case:
    C:\SkyFrameVerification\F1\F1.EDB          the ETABS model, open it to look
    C:\SkyFrameVerification\F1\F1.etabs.json   the results
    C:\SkyFrameVerification\F1\F1.log.txt      what the run printed
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
OUT_ROOT = r"C:\SkyFrameVerification"

KIP_FT_F = 4                 # eUnits.kip_ft_F
MAT_NODESIGN = 3             # eMatType.NoDesign
PATTERN_OTHER = 8            # eLoadPatternType.Other
OBJECT_ELM, GROUP_ELM = 0, 2 # eItemTypeElm


class Stop(Exception):
    pass


_LOG: list = []               # what the current case printed, for its log file


def say(msg: str) -> None:
    print(msg, flush=True)
    _LOG.append(msg)


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
        ok(sm.PropMaterial.SetMPIsotropic(mt["name"], mt["E"], mt["nu"], float(mt.get("alpha", 0.0))),
           f"material {mt['name']} E, nu, alpha")
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
        if s.get("modifiers"):
            md = s["modifiers"]   # ETABS order: A, As2, As3, J, I22, I33, mass, weight
            ok(sm.PropFrame.SetModifiers(s["name"], [md.get("A", 1.0), 1.0, 1.0, md.get("J", 1.0),
                                                     md.get("I22", 1.0), md.get("I33", 1.0), 1.0, 1.0]),
               f"section {s['name']} modifiers")
            back = sm.PropFrame.GetModifiers(s["name"], [])
            ok(back, f"read back modifiers {s['name']}")
            say(f"  section {s['name']} modifiers (read back): {list(back[0])}")
        say(f"  section {s['name']}: A {area}, I22 {i22}, I33 {i33}, J {tors}, As2 {as2}, As3 {as3} (read back)")

    names = {}
    for f in case["frames"]:
        (xi, yi, zi), (xj, yj, zj) = f["i"], f["j"]
        r = sm.FrameObj.AddByCoord(xi, yi, zi, xj, yj, zj, "", f["section"], f["id"], "Global")
        ok(r, f"frame {f['id']}")
        names[f["id"]] = r[0]
        # ETABS adds end length offsets from connectivity by default: with rigid-zone factor 0 the
        # stiffness is unchanged, but forces are reported at the joint FACES (a column below a
        # 1.5 ft beam stops at 10.5 ft). Offsets off, so stations run joint to joint as in SkyFrame.
        rz = f.get("rigid") or {}
        ok(sm.FrameObj.SetEndLengthOffset(r[0], False, float(rz.get("i", 0.0)), float(rz.get("j", 0.0)),
                                          float(rz.get("factor", 0.0))), f"frame {f['id']} end offsets")
        if f.get("releases"):
            dofs = ("P", "V2", "V3", "T", "M2", "M3")
            ri, rj = f["releases"].get("i", {}), f["releases"].get("j", {})
            ok(sm.FrameObj.SetReleases(r[0], [d in ri for d in dofs], [d in rj for d in dofs],
                                       [float(ri.get(d, 0.0)) for d in dofs], [float(rj.get(d, 0.0)) for d in dofs]),
               f"frame {f['id']} releases")
        if f.get("angle"):
            ok(sm.FrameObj.SetLocalAxes(r[0], f["angle"]), f"frame {f['id']} angle")
        if f.get("cardinal", 10) != 10:
            # stiffness transformed for the offset from the centroid (the definition's assumption)
            ok(sm.FrameObj.SetInsertionPoint(r[0], int(f["cardinal"]), False, True, [0.0] * 3, [0.0] * 3, "Local"),
               f"frame {f['id']} insertion point")
            back = sm.FrameObj.GetInsertionPoint(r[0], 0, False, False, [], [], "")
            ok(back, f"read back insertion point {f['id']}")
            if back[0] != int(f["cardinal"]) or not back[2]:
                raise Stop(f"frame {f['id']}: insertion point reads back {back[0]}, transform {back[2]}")
            say(f"  frame {f['id']}: cardinal point {back[0]}, stiffness transformed {back[2]} (read back)")
        if case.get("stationSpacing"):
            ok(sm.FrameObj.SetOutputStations(r[0], 1, float(case["stationSpacing"]), 2, False, False),
               f"frame {f['id']} output stations")

    def point(at):
        r = sm.PointObj.AddCartesian(at[0], at[1], at[2], "", "", "Global", False, 0)
        ok(r, f"point at {at}")
        return r[0]

    areas = build_areas(sm, case)

    for s in case["supports"]:
        ok(sm.PointObj.SetRestraint(point(s["at"]), [bool(v) for v in s["restraint"]]), f"support at {s['at']}")
    for p in case["patterns"]:
        ok(sm.LoadPatterns.Add(p["name"], PATTERN_OTHER, float(p.get("selfWeight", 0.0)), True),
           f"load pattern {p['name']}")
    for ld in case.get("pointLoads", []):
        ok(sm.PointObj.SetLoadForce(point(ld["at"]), ld["pattern"], [float(v) for v in ld["f"]],
                                    False, "Global", 0), f"load {ld['pattern']} at {ld['at']}")
    for ld in case.get("distLoads", []):
        # MyType 1 = force per length; Dir 10 = gravity (positive acts down); whole length, relative distances
        ok(sm.FrameObj.SetLoadDistributed(names[ld["frame"]], ld["pattern"], 1, 10, 0.0, 1.0,
                                          float(ld["w"]), float(ld["w"]), "Global", True, True, 0),
           f"uniform load {ld['pattern']} on {ld['frame']}")
    for ld in case.get("frameLoads", []):
        if ld["type"] == "point":
            # MyType 1 = force; Dir 10 = gravity; relative distance; add to what is there
            ok(sm.FrameObj.SetLoadPoint(names[ld["frame"]], ld["pattern"], 1, 10, float(ld["a"]), float(ld["P"]),
                                        "Global", True, False, 0), f"point load {ld['pattern']} on {ld['frame']}")
        else:
            ok(sm.FrameObj.SetLoadDistributed(names[ld["frame"]], ld["pattern"], 1, 10, float(ld["a"]),
                                              float(ld["b"]), float(ld["w1"]), float(ld["w2"]), "Global", True,
                                              False, 0), f"distributed load {ld['pattern']} on {ld['frame']}")
    for ld in case.get("tempLoads", []):
        # MyType 1 = uniform temperature change (deg F in kip-ft-F units)
        ok(sm.FrameObj.SetLoadTemperature(names[ld["frame"]], ld["pattern"], 1, float(ld["dT"]), "", False, 0),
           f"temperature {ld['pattern']} on {ld['frame']}")
    for ld in case.get("areaLoads", []):
        a = next(x for x in case["areas"] if x["id"] == ld["area"])
        for an in areas[ld["area"]]:
            if a.get("toFrame"):
                # Dir 10 = gravity; DistType 1 = one-way (along local 1), 2 = two-way
                ok(sm.AreaObj.SetLoadUniformToFrame(an, ld["pattern"], float(ld["q"]), 10,
                                                    1 if a["toFrame"] == "one" else 2, True, "Global", 0),
                   f"area load to frames {ld['pattern']} on {ld['area']}")
            else:
                ok(sm.AreaObj.SetLoadUniform(an, ld["pattern"], float(ld["q"]), 10, True, "Global", 0),
                   f"area load {ld['pattern']} on {ld['area']}")
    return names


SHELL_TYPE = {"thin": 1, "thick": 2, "membrane": 3}     # eShellType


def _bilinear(c, u, v):
    return [(1 - u) * (1 - v) * c[0][k] + u * (1 - v) * c[1][k] + u * v * c[2][k] + (1 - u) * v * c[3][k]
            for k in range(3)]


def build_areas(sm, case: dict) -> dict:
    """Area sections and objects.  A meshed area is drawn as n1 x n2 separate area objects, so ETABS
    and SkyFrame use the same elements; returns {area id: [ETABS area names]}."""
    for a in case.get("areaSections", []):
        st = SHELL_TYPE[a["shell"]]
        if a["kind"] == "wall":
            ok(sm.PropArea.SetWall(a["name"], 1, st, a["material"], float(a["thickness"])), f"wall {a['name']}")
            back = sm.PropArea.GetWall(a["name"], 0, 0, "", 0.0, 0, "", "")
            got_t, got_st = back[3], back[1]
        else:
            ok(sm.PropArea.SetSlab(a["name"], 0, st, a["material"], float(a["thickness"])), f"slab {a['name']}")
            back = sm.PropArea.GetSlab(a["name"], 0, 0, "", 0.0, 0, "", "")
            got_t, got_st = back[3], back[1]
        ok(back, f"read back area section {a['name']}")
        if abs(got_t - a["thickness"]) > 1e-9 or got_st != st:
            raise Stop(f"area section {a['name']}: reads back thickness {got_t}, shell type {got_st}")
        if a.get("modifiers"):
            md = a["modifiers"]
            keys = ("f11", "f22", "f12", "m11", "m22", "m12", "v13", "v23", "mass", "weight")
            ok(sm.PropArea.SetModifiers(a["name"], [float(md.get(k, 1.0)) for k in keys]),
               f"area section {a['name']} modifiers")
            got = sm.PropArea.GetModifiers(a["name"], [])
            ok(got, f"read back modifiers {a['name']}")
            say(f"  area section {a['name']} modifiers (read back): {list(got[0])}")
        say(f"  area section {a['name']}: {a['kind']}, thickness {got_t}, shell type {got_st} (read back)")
    out = {}
    for a in case.get("areas", []):
        n1, n2 = a.get("mesh", [1, 1])
        out[a["id"]] = []
        for i in range(n1):
            for j in range(n2):
                pts = [_bilinear(a["corners"], u, v) for u, v in
                       ((i / n1, j / n2), ((i + 1) / n1, j / n2), ((i + 1) / n1, (j + 1) / n2), (i / n1, (j + 1) / n2))]
                label = a["id"] if n1 * n2 == 1 else f"{a['id']}_{i + 1}_{j + 1}"
                r = sm.AreaObj.AddByCoord(4, [p[0] for p in pts], [p[1] for p in pts], [p[2] for p in pts],
                                          "", a["section"], label, "Global")
                ok(r, f"area {label}")
                out[a["id"]].append(r[3])
    if out:
        say(f"  {sum(len(v) for v in out.values())} area objects")
    return out


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
    st = chk["station"]
    if st == "i":
        row = min(rows, key=lambda r: r["station"])
    elif st == "j":
        row = max(rows, key=lambda r: r["station"])
    else:                                   # a distance from end i, ft (e.g. a support face)
        row = min(rows, key=lambda r: abs(r["station"] - st))
        if abs(row["station"] - st) > 1e-3:
            return None
    return row[chk["comp"]]


def run_case(sm, case: dict, out_root: Path, version: str) -> dict:
    """Build, save, analyse and read one case; returns its summary."""
    work = out_root / case["id"]
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{case['id']}.case.json").write_text(json.dumps(case, indent=1), encoding="utf-8")
    say(f"== {case['id']}: {case['title']}")
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
    (work / f"{case['id']}.etabs.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    say(f"{'check':8} {'hand':>12} {'ETABS':>12}  difference")
    worst = 0.0
    for chk in case["checks"]:
        v = pick(res, chk)
        if v is not None and chk["how"] == "abs":
            v = abs(v)
        if chk["hand"] is None:
            say(f"{chk['id']:8} {'-':>12} {('-' if v is None else f'{v:12.6g}'):>12}  compared with SkyFrame only")
            continue
        d = None if v is None else abs(v - chk["hand"]) / max(abs(chk["hand"]), chk.get("scale", 1e-12))
        worst = max(worst, d or 0.0)
        say(f"{chk['id']:8} {chk['hand']:12.6g} {('-' if v is None else f'{v:12.6g}'):>12}  "
            f"{'missing' if d is None else f'{100 * d:.3f}%'}")
    say(f"Saved {edb}; largest difference from the hand calculation {100 * worst:.3f}%")
    return {"id": case["id"], "ok": True, "worst": worst, "folder": str(work)}


def push(repo: Path, summaries: list, out_root: Path) -> None:
    """Copy the results into the GitHub-linked clone and push them (branch etabs-results)."""
    import shutil
    import subprocess

    dest = repo / "skyframe" / "verification" / "etabs" / "results" / "etabs-23"
    dest.mkdir(parents=True, exist_ok=True)
    for s in summaries:
        work = out_root / s["id"]
        for f in (f"{s['id']}.etabs.json", f"{s['id']}.case.json", f"{s['id']}.log.txt"):
            if (work / f).exists():
                shutil.copy2(work / f, dest / f)
    git = lambda *args: subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    git("add", "skyframe/verification/etabs/results")
    msg = "ETABS 23 results: " + ", ".join(f"{s['id']} {'ok' if s['ok'] else 'stopped'}" for s in summaries)
    c = git("commit", "-m", msg)
    if c.returncode and "nothing to commit" not in (c.stdout + c.stderr):
        raise Stop(f"git commit failed: {c.stderr.strip() or c.stdout.strip()}")
    p = git("push")
    if p.returncode:
        raise Stop(f"git push failed: {p.stderr.strip()}")
    say(f"Pushed results for {', '.join(s['id'] for s in summaries)} to GitHub")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*", help="case ids, e.g. F1 F2; or --all")
    ap.add_argument("--all", action="store_true", help="every case published so far that has no results here yet")
    ap.add_argument("--force", action="store_true", help="with --all: rerun cases that already have results")
    ap.add_argument("--etabs", default=ETABS_EXE, help="full path to ETABS.exe")
    ap.add_argument("--close", action="store_true", help="close ETABS when done (always done after several cases)")
    ap.add_argument("--local", help="use this case JSON file instead of downloading it")
    ap.add_argument("--out", default=OUT_ROOT, help="folder for the ETABS models and results")
    ap.add_argument("--push", metavar="CLONE", help="git clone (branch etabs-results) to copy results into and push")
    ap.add_argument("--update", action="store_true", help="download the latest version of this script")
    a = ap.parse_args(argv)

    if a.update:
        Path(__file__).write_bytes(fetch("run_etabs.py"))
        say("run_etabs.py updated")
        return 0
    out_root = Path(a.out)
    if a.local:
        todo = [json.loads(Path(a.local).read_text())]
    else:
        ids = list(a.cases)
        if a.all:
            index = json.loads(fetch("cases/index.json"))
            ids += [c for c in index if a.force or not (out_root / c / f"{c}.etabs.json").exists()]
        if not ids:
            say("Nothing to run: every published case already has results here (use --force to rerun).")
            return 0
        todo = [json.loads(fetch(f"cases/{c}.json")) for c in dict.fromkeys(ids)]
    say(f"Cases: {', '.join(c['id'] for c in todo)}; saving under {out_root}")

    import comtypes.client
    helper = comtypes.client.CreateObject("ETABSv1.Helper")
    import comtypes.gen.ETABSv1 as E
    helper = helper.QueryInterface(E.cHelper)
    etabs = helper.CreateObject(a.etabs)
    ok(etabs.ApplicationStart(), "start ETABS")
    sm = etabs.SapModel
    version = sm.GetVersion()[0]
    say(f"ETABS {version} started")
    summaries = []
    try:
        for case in todo:
            _LOG.clear()
            try:
                summaries.append(run_case(sm, case, out_root, version))
            except Stop as exc:
                say(f"STOPPED on {case['id']}: {exc}")
                summaries.append({"id": case["id"], "ok": False, "error": str(exc)})
            finally:
                (out_root / case["id"]).mkdir(parents=True, exist_ok=True)
                (out_root / case["id"] / f"{case['id']}.log.txt").write_text("\n".join(_LOG) + "\n", encoding="utf-8")
        if a.push:
            push(Path(a.push), summaries, out_root)
    except Stop as exc:
        say(f"STOPPED: {exc}")
    finally:
        say("")
        for s in summaries:
            say(f"{s['id']}: " + (f"done, largest difference from hand {100 * s['worst']:.3f}%" if s["ok"]
                                  else f"stopped: {s['error']}"))
        if a.close or len(todo) > 1:
            etabs.ApplicationExit(False)
            say("ETABS closed")
        else:
            say("ETABS is left open so you can look at the model; close it when done.")
    return 0 if all(s["ok"] for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
