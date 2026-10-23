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
import time
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


_RUN_LOG: list = []           # everything this run printed, saved to <out>/_last_run.txt at the end


def say(msg: str) -> None:
    print(msg, flush=True)
    _LOG.append(msg)
    _RUN_LOG.append(msg)


def ok(ret, what: str) -> None:
    """ETABS API calls return 0 on success (comtypes: last item of a list)."""
    code = ret[-1] if isinstance(ret, (list, tuple)) else ret
    if code != 0:
        raise Stop(f"ETABS refused: {what} (return code {code})")


HEAD_API = ("https://api.github.com/repos/Abhimanyu14Singh/Abhi-Singh/commits/"
            "claude/etabs-opensees-ui-rebuild-kt2zwh")
RAW_AT = "https://raw.githubusercontent.com/Abhimanyu14Singh/Abhi-Singh/{}/skyframe/verification/etabs/"
_SHA: list = []


def _head() -> str:
    """The branch's latest commit.  Files are fetched at that commit: the branch URL is served from a
    cache for several minutes after a push, and a run right after one would get yesterday's files."""
    if not _SHA:
        try:
            req = urllib.request.Request(HEAD_API, headers={"Accept": "application/vnd.github.sha"})
            with urllib.request.urlopen(req, timeout=30) as r:
                sha = r.read().decode().strip()
            _SHA.append(sha if len(sha) == 40 else "")
        except Exception:
            _SHA.append("")
    return _SHA[0]


def fetch(name: str) -> bytes:
    sha = _head()
    with urllib.request.urlopen((RAW_AT.format(sha) if sha else RAW) + name, timeout=30) as r:
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

    names, flipped = {}, set()
    for f in case["frames"]:
        (xi, yi, zi), (xj, yj, zj) = f["i"], f["j"]
        r = sm.FrameObj.AddByCoord(xi, yi, zi, xj, yj, zj, "", f["section"], f["id"], "Global")
        ok(r, f"frame {f['id']}")
        names[f["id"]] = r[0]
        # ETABS draws horizontal members towards +X / +Y: everything given per end (releases, rigid
        # zones, partial-length loads) goes on the end ETABS calls i or j, not the definition's
        if drawn_reversed(sm, r[0], f):
            flipped.add(f["id"])
        swap = f["id"] in flipped
        # ETABS adds end length offsets from connectivity by default: with rigid-zone factor 0 the
        # stiffness is unchanged, but forces are reported at the joint FACES (a column below a
        # 1.5 ft beam stops at 10.5 ft). Offsets off, so stations run joint to joint as in SkyFrame.
        rz = f.get("rigid") or {}
        oi, oj = float(rz.get("i", 0.0)), float(rz.get("j", 0.0))
        if swap:
            oi, oj = oj, oi
        ok(sm.FrameObj.SetEndLengthOffset(r[0], False, oi, oj, float(rz.get("factor", 0.0))),
           f"frame {f['id']} end offsets")
        if f.get("releases"):
            dofs = ("P", "V2", "V3", "T", "M2", "M3")
            ri, rj = f["releases"].get("i", {}), f["releases"].get("j", {})
            if swap:
                ri, rj = rj, ri
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
        if f.get("tcLimit") == "tension":
            # no compression (limit 0); limits act in nonlinear cases only
            ok(sm.FrameObj.SetTCLimits(r[0], True, 0.0, False, 0.0, 0), f"frame {f['id']} tension-only")
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
        sw = float(p.get("selfWeight", 0.0))
        if sm.LoadPatterns.Add(p["name"], PATTERN_OTHER, sw, True) != 0:
            # a new model already has Dead and Live (names ignore case): use it, with our self-weight
            ok(sm.LoadPatterns.SetSelfWTMultiplier(p["name"], sw), f"load pattern {p['name']}")
            say(f"  load pattern {p['name']} already defined, used with self-weight multiplier {sw}")
    for ld in case.get("pointLoads", []):
        ok(sm.PointObj.SetLoadForce(point(ld["at"]), ld["pattern"], [float(v) for v in ld["f"]],
                                    False, "Global", 0), f"load {ld['pattern']} at {ld['at']}")
    for ld in case.get("distLoads", []):
        # MyType 1 = force per length; Dir 10 = gravity (positive acts down); whole length, relative distances
        ok(sm.FrameObj.SetLoadDistributed(names[ld["frame"]], ld["pattern"], 1, 10, 0.0, 1.0,
                                          float(ld["w"]), float(ld["w"]), "Global", True, True, 0),
           f"uniform load {ld['pattern']} on {ld['frame']}")
    for ld in case.get("frameLoads", []):
        swap = ld["frame"] in flipped                    # distances measured from ETABS's i end
        if ld["type"] == "point":
            a = 1.0 - float(ld["a"]) if swap else float(ld["a"])
            # MyType 1 = force; Dir 10 = gravity; relative distance; add to what is there
            ok(sm.FrameObj.SetLoadPoint(names[ld["frame"]], ld["pattern"], 1, 10, a, float(ld["P"]),
                                        "Global", True, False, 0), f"point load {ld['pattern']} on {ld['frame']}")
        else:
            a, b, w1, w2 = float(ld["a"]), float(ld["b"]), float(ld["w1"]), float(ld["w2"])
            if swap:
                a, b, w1, w2 = 1.0 - b, 1.0 - a, w2, w1
            ok(sm.FrameObj.SetLoadDistributed(names[ld["frame"]], ld["pattern"], 1, 10, a, b, w1, w2, "Global",
                                              True, False, 0), f"distributed load {ld['pattern']} on {ld['frame']}")
    for ld in case.get("tempLoads", []):
        # MyType 1 = uniform temperature change (deg F in kip-ft-F units)
        ok(sm.FrameObj.SetLoadTemperature(names[ld["frame"]], ld["pattern"], 1, float(ld["dT"]), "", False, 0),
           f"temperature {ld['pattern']} on {ld['frame']}")
    for ld in case.get("areaLoads", []):
        a = next(x for x in case["areas"] if x["id"] == ld["area"])
        for an in areas[ld["area"]]:
            # Dir 10 = gravity.  A membrane slab passes this load to its edge beams: two-way for a plain
            # slab, one-way along its ribs for a ribbed one (SetLoadUniformToFrame is refused, -100)
            ok(sm.AreaObj.SetLoadUniform(an, ld["pattern"], float(ld["q"]), 10, True, "Global", 0),
               f"area load {ld['pattern']} on {ld['area']}")
    case["_etabs_names"] = names
    try:
        build_analysis(sm, case, point)
        ext = load_ext(case)
        if ext is not None and hasattr(ext, "build"):
            ext.build(sm, case, sys.modules[__name__], point, names)   # features the stock keys do not cover
    finally:
        case.pop("_etabs_names", None)
    clear_unasked_restraints(sm, case)
    if case.get("diagnose"):
        diagnose(sm, case, names)
    return names


def diagnose(sm, case: dict, names: dict) -> None:
    """Log what ETABS holds for a few frames of each kind: local axes, end offsets, insertion point,
    releases, modifiers, section, and the material's E."""
    seen = {}
    for f in case["frames"]:
        kind = ("column" if f["i"][:2] == f["j"][:2] else "beam", f.get("angle", 0.0), f["section"])
        seen.setdefault(kind, f)
    for (kind, ang, secn), f in seen.items():
        nm = names[f["id"]]
        out = [f"{f['id']} ({kind}, angle {ang}, {secn})"]
        for label, call in (("local axes", lambda: sm.FrameObj.GetLocalAxes(nm, 0.0, False)),
                            ("end offsets", lambda: sm.FrameObj.GetEndLengthOffset(nm, False, 0.0, 0.0, 0.0)),
                            ("insertion", lambda: sm.FrameObj.GetInsertionPoint(nm, 0, False, False, [], [], "")),
                            ("releases", lambda: sm.FrameObj.GetReleases(nm, [], [], [], [])),
                            ("modifiers", lambda: sm.FrameObj.GetModifiers(nm, [])),
                            ("section", lambda: sm.FrameObj.GetSection(nm, "", "")),
                            ("points", lambda: sm.FrameObj.GetPoints(nm, "", ""))):
            try:
                r = call()
                out.append(f"{label} {[list(x) if isinstance(x, (list, tuple)) else x for x in r]}")
            except Exception as exc:
                out.append(f"{label} ? {exc}")
        say("  diagnose " + " | ".join(out))
    for mt in case["materials"]:
        try:
            say(f"  diagnose material {mt['name']}: {list(sm.PropMaterial.GetMPIsotropic(mt['name'], 0.0, 0.0, 0.0))}")
        except Exception as exc:
            say(f"  diagnose material {mt['name']}: ? {exc}")
    try:
        say(f"  diagnose shear deformation / options: {list(sm.Analyze.GetActiveDOF([]))}")
    except Exception:
        pass


def load_ext(case: dict):
    """The case's ETABS extension (etabs_ext_<group>.py): the copy next to this script when there is
    one (--local work), else the latest from GitHub, saved next to this script and imported."""
    if not case.get("ext"):
        return None
    import importlib
    name = f"etabs_ext_{case['ext']}"
    if name in sys.modules:
        return sys.modules[name]
    path = HERE / f"{name}.py"
    if not _LOCAL_EXT:
        path.write_bytes(fetch(f"{name}.py"))
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    return importlib.import_module(name)


_LOCAL_EXT: list = []          # non-empty: use the extension files already next to this script


def drawn_reversed(sm, fname: str, f: dict) -> bool:
    """True when ETABS put the frame's end i at the definition's j point."""
    pts = sm.FrameObj.GetPoints(fname, "", "")
    ok(pts, f"end points of {f['id']}")
    c = sm.PointObj.GetCoordCartesian(pts[0], 0, 0, 0, "Global")
    ok(c, f"coordinates of {f['id']} end i")
    return math.dist(c[:3], f["i"]) > 1e-6


def clear_unasked_restraints(sm, case: dict) -> None:
    """ETABS restrains joints at the Base level by itself; free every joint the definition does not
    support, so springs and base-level slabs work as defined."""
    wanted = [s["at"] for s in case["supports"]]
    r = sm.PointObj.GetNameList(0, [])
    ok(r, "list points")
    freed = 0
    for pn in r[1]:
        c = sm.PointObj.GetCoordCartesian(pn, 0, 0, 0, "Global")
        ok(c, f"coordinates of point {pn}")
        if any(math.dist(c[:3], w) < 1e-6 for w in wanted):
            continue
        got = sm.PointObj.GetRestraint(pn, [])
        ok(got, f"restraint of point {pn}")
        if any(got[0]):
            ok(sm.PointObj.SetRestraint(pn, [False] * 6), f"free point {pn}")
            freed += 1
    if freed:
        say(f"  freed {freed} joints ETABS had restrained without being asked")


def _methods(obj) -> str:
    try:
        return ", ".join(n for n in dir(obj) if not n.startswith("_"))[:1500]
    except Exception as exc:
        return f"(could not list: {exc})"


def _edit_table(sm, key: str, rows: list) -> str:
    """Add records to an ETABS database table: ``rows`` are {field (lower case): value}; fields the
    table has but a row does not name are left blank.  Returns what ETABS reported."""
    dt = sm.DatabaseTables
    got = dt.GetTableForEditingArray(key, "", 0, [], 0, [])
    ok(got, f"open table {key}")
    version, fields, n, data = got[0], list(got[1]), got[2], list(got[3])
    for row in rows:
        for f in fields:
            fl = f.lower()
            v = next((val for k, val in row.items() if fl == k or fl.startswith(k)), "")
            data.append(str(v))
    n += len(rows)
    ok(dt.SetTableForEditingArray(key, version, fields, n, data), f"edit table {key}")
    r = dt.ApplyEditedTables(True, 0, 0, 0, 0, "")
    ok(r, f"apply table {key}")
    if r[0] or r[1]:                                        # fatal errors, errors (then warnings, info, log)
        raise Stop(f"table {key}: {r[0]} fatal errors, {r[1]} errors: {str(r[4])[:400]}")
    return f"database table ({', '.join(fields)})"


def set_spectrum(sm, fn: dict) -> str:
    """A user response-spectrum function: the API call where ETABS has it, else its database table."""
    T, Sa = [float(p[0]) for p in fn["points"]], [float(p[1]) for p in fn["points"]]
    tried = []
    try:
        ok(sm.Func.FuncRS.SetUser(fn["name"], len(T), T, Sa, float(fn["damping"])), "spectrum")
        return "Func.FuncRS.SetUser"
    except Exception as exc:
        tried.append(f"FuncRS.SetUser: {exc}")
    try:
        return _edit_table(sm, "Functions - Response Spectrum - User Defined",
                           [{"name": fn["name"], "period": t, "value": a, "damp": float(fn["damping"])}
                            for t, a in zip(T, Sa)])
    except Exception as exc:
        tried.append(f"table: {exc}")
    raise Stop("response-spectrum function refused: " + "; ".join(tried)
               + f" | Func.FuncRS offers: {_methods(sm.Func.FuncRS)}")


def table_keys(sm) -> list:
    """Every database table ETABS knows (needs a model open).  GetAvailableTables lists only tables
    that already hold rows (a fresh model: linear static and eigen cases), so the full list from
    GetAllTables comes first: a table for a case type not yet in the model is found and filled."""
    dt = sm.DatabaseTables
    keys = []
    for call in (lambda: dt.GetAllTables(0, [], [], [], []), lambda: dt.GetAvailableTables(0, [], [], [])):
        try:
            t = call()
            for k in (t[1] if isinstance(t, (list, tuple)) and len(t) > 1 else []):
                if str(k) not in keys:
                    keys.append(str(k))
        except Exception:
            pass
    return keys


def find_table(sm, *words) -> str:
    """The table key containing every word (case-insensitive), else ''."""
    for k in table_keys(sm):
        if all(w.lower() in k.lower() for w in words):
            return k
    return ""


def edit_table_row(sm, key: str, name: str, values: dict) -> str:
    """Change fields of the row whose first field is ``name``: ``values`` maps candidate field names
    (lower case, any of several separated by |) to the new value.  Returns what was set."""
    dt = sm.DatabaseTables
    got = dt.GetTableForEditingArray(key, "", 0, [], 0, [])
    ok(got, f"open table {key}")
    version, fields, n, data = got[0], list(got[1]), got[2], list(got[3])
    nf = len(fields)
    low = [f.lower().replace(" ", "") for f in fields]
    rows = [k for k in range(n) if str(data[k * nf]).lower() == name.lower()]
    if not rows:
        raise Stop(f"table {key}: no row {name!r} (fields {', '.join(fields)})")
    done = []
    for cands, v in values.items():
        col = next((i for c in cands.split("|") for i, f in enumerate(low) if f == c), None)
        if col is None:
            col = next((i for c in cands.split("|") for i, f in enumerate(low) if c in f), None)
        if col is None:
            raise Stop(f"table {key}: no field like {cands!r} (fields {', '.join(fields)})")
        for k in rows:
            data[k * nf + col] = str(v)
        done.append(f"{fields[col]} = {v}")
    ok(dt.SetTableForEditingArray(key, version, fields, n, data), f"edit table {key}")
    r = dt.ApplyEditedTables(True, 0, 0, 0, 0, "")
    ok(r, f"apply table {key}")
    if r[0] or r[1]:                                        # fatal errors, errors
        raise Stop(f"table {key}: {r[0]} fatal errors, {r[1]} errors: {str(r[4])[:400]}")
    return f"table {key}: " + ", ".join(done)


def set_rs_comb_damping(sm, nm: str, rs: dict) -> str:
    """Modal combination and constant damping of a spectrum case.  ETABS 23's API only reads them
    (GetModalComb_1, GetDampConstant): where it cannot set them, the case's defaults are read back
    and must be what the definition asks for."""
    rsp = sm.LoadCases.ResponseSpectrum
    comb = {"CQC": 1, "SRSS": 2}[rs["combine"]]
    damp = float(rs["damping"])
    out = []
    if hasattr(rsp, "SetModalComb_1"):
        ok(rsp.SetModalComb_1(nm, comb, 1.0, 0.0, 1, 0.0), f"{nm} modal combination")
        out.append(f"{rs['combine']} set")
    else:
        got = rsp.GetModalComb_1(nm, 0, 0.0, 0.0, 0, 0.0)
        ok(got, f"read back {nm} modal combination")
        if int(got[0]) != comb:
            raise Stop(f"{nm}: ETABS's API cannot set the modal combination and the case has type {got[0]}, "
                       f"the definition asks for {rs['combine']} ({comb})")
        out.append(f"{rs['combine']} (ETABS default, read back)")
    if hasattr(rsp, "SetDampConstant"):
        ok(rsp.SetDampConstant(nm, damp), f"{nm} damping")
        out.append(f"damping {damp} set")
    else:
        got = rsp.GetDampConstant(nm, 0.0)
        ok(got, f"read back {nm} damping")
        if abs(float(got[0]) - damp) > 1e-9:
            raise Stop(f"{nm}: ETABS's API cannot set the damping and the case has {got[0]}, "
                       f"the definition asks for {damp}")
        out.append(f"damping {got[0]} (ETABS default, read back)")
    return ", ".join(out)


def set_buckling(sm, bc: dict) -> str:
    """A linear buckling case: the API where ETABS has it, else its database table."""
    nm, pats = bc["name"], list(bc["loads"].items())
    tried = []
    try:
        lb = sm.LoadCases.Buckling
        ok(lb.SetCase(nm), "buckling case")
        ok(lb.SetLoads(nm, len(pats), ["Load"] * len(pats), [p for p, _ in pats], [float(f) for _, f in pats]),
           "buckling loads")
        ok(lb.SetParameters(nm, int(bc["modes"]), 1e-9), "buckling modes")
        return "LoadCases.Buckling"
    except Exception as exc:
        tried.append(f"LoadCases.Buckling: {exc}")
    try:
        return _edit_table(sm, "Load Case Definitions - Buckling",
                           [{"name": nm, "nummodes": int(bc["modes"]), "loadtype": "Load Pattern", "loadname": p,
                             "loadsf": float(f), "mass": "", "initial": "Zero"} for p, f in pats])
    except Exception as exc:
        tried.append(f"table: {exc}")
    raise Stop("buckling case refused: " + "; ".join(tried)
               + f" | LoadCases offers: {_methods(sm.LoadCases)} | Buckling offers: {_methods(sm.LoadCases.Buckling)}")


def _try(label: str, tried: list, call):
    """Run ``call``; True when it worked, else note why in ``tried``."""
    try:
        r = call()
        code = r[-1] if isinstance(r, (list, tuple)) else r
        if code == 0:
            return True
        tried.append(f"{label} returned {code}")
    except Exception as exc:
        tried.append(f"{label}: {exc}")
    return False


def set_gap_link(sm, lk: dict) -> str:
    """A two-joint link, axial U1 along i -> j only: a gap (compression only, stiffness k, opening) or a
    Plastic (Wen) element (stiffness k, yield force, post-yield ratio, exponent)."""
    k = float(lk["k"])
    dof = [True, False, False, False, False, False]
    fixed = [False] * 6
    nonlin = [True, False, False, False, False, False]
    ke, ce = [k, 0, 0, 0, 0, 0], [0.0] * 6
    kk = [k, 0, 0, 0, 0, 0]
    tried = []
    if lk["type"] == "wen":
        name = f"WEN_{lk['id']}"
        fy, ratio, ex = [float(lk["yield"])] + [0.0] * 5, [float(lk["ratio"])] + [0.0] * 5, [float(lk["exp"])] + [2.0] * 5
        if not (_try("PropLink.SetPlasticWen", tried, lambda: sm.PropLink.SetPlasticWen(
                    name, dof, fixed, nonlin, ke, ce, kk, fy, ratio, ex, 0.0, 0.0, "", ""))
                or _try("PropLink.SetPlasticWen (short)", tried, lambda: sm.PropLink.SetPlasticWen(
                    name, dof, fixed, nonlin, ke, ce, kk, fy, ratio, ex, 0.0, 0.0))):
            raise Stop("Plastic (Wen) link property refused: " + "; ".join(tried)
                       + f" | PropLink offers: {_methods(sm.PropLink)}")
        what = f"Plastic (Wen) k {k}, yield {lk['yield']}, ratio {lk['ratio']}, exponent {lk['exp']}"
    else:
        name, gap = f"GAP_{lk['id']}", float(lk["open"])
        dis = [gap, 0, 0, 0, 0, 0]
        if not (_try("PropLink.SetGap", tried, lambda: sm.PropLink.SetGap(name, dof, fixed, nonlin, ke, ce, kk, dis,
                                                                              0.0, 0.0, "", ""))
                or _try("PropLink.SetGap (short)", tried, lambda: sm.PropLink.SetGap(name, dof, fixed, nonlin, ke,
                                                                                      ce, kk, dis, 0.0, 0.0))):
            raise Stop("gap link property refused: " + "; ".join(tried)
                       + f" | PropLink offers: {_methods(sm.PropLink)}")
        what = f"gap k {k}, opening {gap}"
    (xi, yi, zi), (xj, yj, zj) = lk["i"], lk["j"]
    r = None
    for label, call in (("LinkObj.AddByCoord", lambda: sm.LinkObj.AddByCoord(xi, yi, zi, xj, yj, zj, "", False,
                                                                            name, lk["id"], "Global")),
                        ("LinkObj.AddByCoord (short)", lambda: sm.LinkObj.AddByCoord(xi, yi, zi, xj, yj, zj, "",
                                                                                    False, name))):
        try:
            r = call()
            if (r[-1] if isinstance(r, (list, tuple)) else r) == 0:
                break
            tried.append(f"{label} returned {r[-1]}")
            r = None
        except Exception as exc:
            tried.append(f"{label}: {exc}")
    if r is None:
        raise Stop("gap link refused: " + "; ".join(tried) + f" | LinkObj offers: {_methods(sm.LinkObj)}")
    return f"{what}, link {r[0]}"


def set_th_function(sm, fn: dict) -> str:
    """A user time-history function (time, value): the API where ETABS has it, else its database table."""
    T, V = [float(p[0]) for p in fn["points"]], [float(p[1]) for p in fn["points"]]
    tried = []
    if _try("Func.FuncTH.SetUser", tried, lambda: sm.Func.FuncTH.SetUser(fn["name"], len(T), T, V)):
        return "Func.FuncTH.SetUser"
    try:
        return _edit_table(sm, "Functions - Time History - User Defined",
                           [{"name": fn["name"], "time": t, "value": v} for t, v in zip(T, V)])
    except Exception as exc:
        tried.append(f"table: {exc}")
    raise Stop("time-history function refused: " + "; ".join(tried)
               + f" | Func.FuncTH offers: {_methods(getattr(sm.Func, 'FuncTH', None))}")


def set_th_case(sm, case: dict, th: dict) -> str:
    """A linear modal time-history case: ground acceleration along X or Y, constant modal damping."""
    nm, mh = th["name"], sm.LoadCases.ModHistLinear
    steps = [("SetCase", lambda: mh.SetCase(nm)),
             ("SetLoads", lambda: mh.SetLoads(nm, 1, ["Accel"], ["U1" if th["dir"] == "X" else "U2"],
                                              [th["function"]], [float(th["scale"])], [1.0], [0.0], ["Global"],
                                              [0.0]))]
    for label, call in steps:
        tried = []
        if not _try(f"ModHistLinear.{label}", tried, call):
            raise Stop("modal time-history case refused: " + "; ".join(tried)
                       + f" | ModHistLinear offers: {_methods(mh)}")
    want = {"modalcase": case["modal"]["case"], "outsteps|numsteps|nsteps|numberofsteps": int(th["steps"]),
            "stepsize|outstepsize|dt": float(th["dt"]), "constdamp|dampconst|constantdamping": float(th["damping"])}
    if all(hasattr(mh, m) for m in ("SetModalCase", "SetTimeStep", "SetDampConstant")):
        ok(mh.SetModalCase(nm, case["modal"]["case"]), f"{nm} modal case")
        ok(mh.SetTimeStep(nm, int(th["steps"]), float(th["dt"])), f"{nm} time steps")
        ok(mh.SetDampConstant(nm, float(th["damping"])), f"{nm} damping")
        how = "API"
    else:
        # ETABS 23's API sets only the case and its loads: the rest through the load-case table
        key = find_table(sm, "load case", "modal", "history") or find_table(sm, "load case", "time history")
        if not key:
            raise Stop("modal time-history case: the API cannot set its steps or damping and no load-case "
                       f"table was found; tables offered: {', '.join(table_keys(sm))[:1500]}")
        damp = want.pop("constdamp|dampconst|constantdamping")
        how = edit_table_row(sm, key, nm, want)
        # modal damping has its own table ("Load Case Definitions - Damping - Modal")
        dkey = (find_table(sm, "load case", "damping", "modal") or find_table(sm, "load case", "modal", "history", "damp")
                or find_table(sm, "modal history", "damp"))
        if dkey:
            try:
                how += "; " + edit_table_row(sm, dkey, nm, {"constdamp|dampconst|constantdamping|constant|damping|ratio": damp})
            except Stop as exc:
                if abs(damp - 0.05) >= 1e-12:
                    raise
                how += f"; damping 0.05 (the ETABS default; {exc})"
        elif abs(damp - 0.05) < 1e-12:
            how += "; damping 0.05 (the ETABS default; no damping table)"
        else:
            raise Stop(f"modal time-history damping {damp}: no damping table; tables with 'damp': "
                       f"{[k for k in table_keys(sm) if 'amp' in k]}")
    return f"modal linear, {th['steps']} steps of {th['dt']} s, damping {th['damping']} ({how})"


def set_pushover(sm, case: dict, po: dict, point) -> str:
    """Rigid-plastic M3 hinges at both ends of the listed frames, then a displacement-controlled push."""
    names = case.get("_etabs_names", {})
    tried = []
    hinge_api = [n for n in dir(sm.FrameObj) if "inge" in n] if hasattr(sm, "FrameObj") else []
    done = False
    for fid, mp in (po.get("hinges") or {}).items():
        fname = names.get(fid, fid)
        for rel in (0.0, 1.0):
            ok_one = False
            if hasattr(sm.FrameObj, "SetHingeAssigns"):
                ok_one = _try("FrameObj.SetHingeAssigns", tried, lambda: sm.FrameObj.SetHingeAssigns(
                    fname, 1, [f"EPP_M3_{int(mp)}"], [rel], 0))
            if not ok_one:
                try:
                    _edit_table(sm, "Frame Assignments - Hinges",
                                [{"uniquename": fname, "label": fid, "hinge": f"Auto M3", "reldist": rel,
                                  "autohingetype": "From Tables In ASCE 41-17", "dof": "M3"}])
                    ok_one = True
                except Exception as exc:
                    tried.append(f"table Frame Assignments - Hinges: {exc}")
            if not ok_one:
                hinge_tables = [k for k in table_keys(sm) if "inge" in k]
                raise Stop("frame hinges: no way found to assign them through the API ("
                           + "; ".join(tried[-3:]) + f") | FrameObj hinge methods: {hinge_api} | hinge tables: "
                           f"{hinge_tables}; see _api_probe.txt for their fields")
            done = True
    nm, sn = po["name"], sm.LoadCases.StaticNonlinear
    pn = point(po["control"])
    for label, call in (("SetCase", lambda: sn.SetCase(nm)),
                        ("SetLoads", lambda: sn.SetLoads(nm, 1, ["Load"], [po["pattern"]], [1.0])),
                        ("SetLoadApplication", lambda: sn.SetLoadApplication(nm, 2, 2, float(po["target"]), 1,
                                                                            1 if po["dir"] == "X" else 2, pn, ""))):
        t2 = []
        if not _try(f"StaticNonlinear.{label}", t2, call):
            raise Stop("pushover case refused: " + "; ".join(t2) + f" | StaticNonlinear offers: {_methods(sn)}")
    where = f"hinges at both ends of {', '.join(po['hinges'])}" if done else "no frame hinges (links only)"
    return f"{where}; push to {po['target']} ft at {pn}"


def set_staged(sm, case: dict, sc: dict, point) -> str:
    """Staged construction, one stage per story: add the story's columns and top joint, load what is new."""
    names = case.get("_etabs_names", {})
    tops = []
    z = 0.0
    for st in case["stories"]:
        z += st["height"]
        tops.append(z)
    groups = []
    for k, ztop in enumerate(tops):
        g = f"STAGE{k + 1}"
        t = []
        if not _try("GroupDef.SetGroup", t, lambda: sm.GroupDef.SetGroup(g)):
            raise Stop("group refused: " + "; ".join(t))
        zbot = 0.0 if k == 0 else tops[k - 1]
        for f in case["frames"]:
            if abs(max(f["i"][2], f["j"][2]) - ztop) < 1e-6 and min(f["i"][2], f["j"][2]) >= zbot - 1e-6:
                ok(sm.FrameObj.SetGroupAssign(names[f["id"]], g, False, 0), f"group {g}: {f['id']}")
                for p in (f["i"], f["j"]):
                    if abs(p[2] - ztop) < 1e-6 or (k == 0 and abs(p[2]) < 1e-6):
                        ok(sm.PointObj.SetGroupAssign(point(p), g, False, 0), f"group {g}: point {p}")
        groups.append(g)
    nm, ss = sc["name"], sm.LoadCases.StaticNonlinearStaged
    n = len(groups)
    t = []
    if not _try("StaticNonlinearStaged.SetCase", t, lambda: ss.SetCase(nm)):
        raise Stop("staged case refused: " + "; ".join(t) + f" | offers: {_methods(ss)}")
    if not (_try("SetStageDefinitions_2", t, lambda: ss.SetStageDefinitions_2(nm, n, [0.0] * n, [True] * n,
                                                                              [""] * n, [""] * n))
            or _try("SetStageDefinitions_1", t, lambda: ss.SetStageDefinitions_1(nm, n, [0.0] * n, [True] * n,
                                                                                [""] * n, [""] * n))
            or _try("SetStageDefinitions", t, lambda: ss.SetStageDefinitions(nm, n, [0.0] * n, [True] * n,
                                                                            [""] * n))):
        raise Stop("staged definitions refused: " + "; ".join(t) + f" | offers: {_methods(ss)}")
    for k, g in enumerate(groups, start=1):
        # operation 1 = add structure, 3 = load objects if new (pattern, factor)
        args = (nm, k, 2, [1, 3], ["Group", "Group"], [g, g], [0.0, 0.0], ["", "Load"], ["", sc["pattern"]],
                [0.0, 1.0])
        if not (_try("SetStageData_2", t, lambda: ss.SetStageData_2(*args))
                or _try("SetStageData_1", t, lambda: ss.SetStageData_1(*args))
                or _try("SetStageData", t, lambda: ss.SetStageData(*args))):
            raise Stop(f"stage {k} refused: " + "; ".join(t[-3:]) + f" | offers: {_methods(ss)}")
    return f"{n} stages: add each story's group, then load what is new"


def api_probe(sm, out_root: Path) -> None:
    """Write what this ETABS's API offers (objects, methods, database tables and fields) to _api_probe.txt.
    Never stops the run."""
    lines = []

    def put(label, obj):
        try:
            lines.append(f"[{label}] " + ", ".join(n for n in dir(obj) if not n.startswith("_")))
        except Exception as exc:
            lines.append(f"[{label}] not available: {exc}")

    def get(path):
        obj = sm
        for part in path.split("."):
            obj = getattr(obj, part)
        return obj

    for path in ("Func", "Func.FuncRS", "Func.FuncTH", "LoadCases", "LoadCases.ModalEigen",
                 "LoadCases.ResponseSpectrum", "LoadCases.StaticNonlinear", "LoadCases.StaticNonlinearStaged",
                 "LoadCases.DirHistLinear", "LoadCases.DirHistNonlinear", "LoadCases.ModHistLinear",
                 "LoadCases.Buckling", "PropLink", "LinkObj", "SourceMass", "PropHinge", "GroupDef"):
        try:
            put(path, get(path))
        except Exception as exc:
            lines.append(f"[{path}] not available: {exc}")
    try:
        lines.append("[FrameObj hinge methods] " + ", ".join(n for n in dir(sm.FrameObj) if "inge" in n))
    except Exception as exc:
        lines.append(f"[FrameObj] {exc}")
    try:
        # tables exist only with a model open
        ok(sm.InitializeNewModel(KIP_FT_F), "new model in kip-ft")
        ok(sm.File.NewGridOnly(1, 12.0, 12.0, 2, 2, 20.0, 20.0), "new grid-only model")
        sm.LoadCases.ModHistLinear.SetCase("PROBE_TH")
        dt = sm.DatabaseTables
        keys = table_keys(sm)
        lines.append(f"[tables] {len(keys)} available")
        for k in keys:
            lines.append(f"  {k}")
        for k in keys:
            if any(w.lower() in k.lower() for w in ("Response Spectrum", "History", "Buckling", "Hinge", "Staged")):
                try:
                    f = dt.GetAllFieldsInTable(k, 0, 0, [], [], [], [], [])
                    lines.append(f"[fields] {k}: " + ", ".join(str(x) for x in f[2]))
                except Exception as exc:
                    lines.append(f"[fields] {k}: {exc}")
    except Exception as exc:
        lines.append(f"[tables] not available: {exc}")
    try:
        out_root.mkdir(parents=True, exist_ok=True)
        (out_root / "_api_probe.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        say(f"API probe written: {out_root / '_api_probe.txt'}")
    except Exception as exc:
        say(f"API probe not written: {exc}")


def set_mass_source(sm, elements: bool, masses: bool, pats: list) -> str:
    """The mass source, through whichever API this ETABS accepts (the newer named source first)."""
    names = [p for p, _ in pats] or [""]                 # never an empty array: COM may refuse it
    sfs = [float(f) for _, f in pats] or [0.0]
    tried = []
    attempts = (
        ("SourceMass.SetMassSource", lambda: sm.SourceMass.SetMassSource(
            "VERIF", elements, masses, bool(pats), True, len(pats), names, sfs)),
        ("PropMaterial.SetMassSource_1", lambda: sm.PropMaterial.SetMassSource_1(
            elements, masses, bool(pats), len(pats), names, sfs)),
        ("PropMaterial.SetMassSource", lambda: sm.PropMaterial.SetMassSource(
            (3 if pats else 1) if (elements or masses) else 2, len(pats), names, sfs)),
    )
    for label, call in attempts:
        try:
            r = call()
            code = r[-1] if isinstance(r, (list, tuple)) else r
            if code == 0:
                return label
            tried.append(f"{label} returned {code}")
        except Exception as exc:
            tried.append(f"{label}: {exc}")
    raise Stop("mass source refused: " + "; ".join(tried))


def build_analysis(sm, case: dict, point) -> None:
    """Diaphragms, springs, masses, mass source, modal / response-spectrum / P-Delta cases, combinations."""
    for d in case.get("diaphragms", []):
        if sm.Diaphragm.SetDiaphragm(d["name"], False) != 0:
            # a new model already has a rigid diaphragm D1: use it if it is rigid
            got = sm.Diaphragm.GetDiaphragm(d["name"], False)
            ok(got, f"rigid diaphragm {d['name']}")
            if got[0]:
                raise Stop(f"diaphragm {d['name']} exists and is semi-rigid")
            say(f"  diaphragm {d['name']} already defined (rigid), used as is")
        for p in d["points"]:
            pn = point(p)
            ok(sm.PointObj.SetDiaphragm(pn, 3, d["name"]), f"diaphragm {d['name']} at {p}")   # 3 = defined
            back = sm.PointObj.GetDiaphragm(pn, 0, "")
            ok(back, f"read back diaphragm at {p}")
            if back[1] != d["name"]:
                raise Stop(f"point {p}: diaphragm reads back {back[1]!r}, set {d['name']!r}")
        say(f"  diaphragm {d['name']}: {len(d['points'])} joints (read back)")
    for sp in case.get("springs", []):
        pn = point(sp["at"])
        ok(sm.PointObj.SetSpring(pn, [float(v) for v in sp["k"]], 0, False, True), f"spring at {sp['at']}")
        back = sm.PointObj.GetSpring(pn, [])
        ok(back, f"read back spring at {sp['at']}")
        if any(abs(a - b) > 1e-6 * max(1.0, abs(b)) for a, b in zip(back[0], sp["k"])):
            raise Stop(f"spring at {sp['at']}: reads back {list(back[0])}, set {sp['k']}")
        say(f"  spring at {sp['at']}: {list(back[0])} (read back)")
    for ms in case.get("masses", []):
        ok(sm.PointObj.SetMass(point(ms["at"]), [float(v) for v in ms["m"]], 0, False, True),
           f"mass at {ms['at']}")
    if case.get("massSource"):
        src = case["massSource"]
        pats = list((src.get("patterns") or {}).items())
        how = set_mass_source(sm, bool(src.get("elements")), bool(src.get("masses")), pats)
        say(f"  mass source ({how}): elements {bool(src.get('elements'))}, joint masses {bool(src.get('masses'))}, "
            f"patterns {dict(pats)}")
    if case.get("modal"):
        mc = case["modal"]
        ok(sm.LoadCases.ModalEigen.SetCase(mc["case"]), f"modal case {mc['case']}")
        ok(sm.LoadCases.ModalEigen.SetNumberModes(mc["case"], int(mc["modes"]), 1), f"{mc['case']} modes")
    for fn in case.get("spectra", []):
        say(f"  spectrum {fn['name']}: {set_spectrum(sm, fn)}")
    for rs in case.get("rsCases", []):
        nm = rs["name"]
        ok(sm.LoadCases.ResponseSpectrum.SetCase(nm), f"response spectrum case {nm}")
        ok(sm.LoadCases.ResponseSpectrum.SetModalCase(nm, case["modal"]["case"]), f"{nm} modal case")
        ok(sm.LoadCases.ResponseSpectrum.SetLoads(nm, 1, ["U1" if rs["dir"] == "X" else "U2"], [rs["function"]],
                                                  [float(rs["scale"])], ["Global"], [0.0]), f"{nm} loads")
        say(f"  spectrum case {nm}: {set_rs_comb_damping(sm, nm, rs)}")
        ok(sm.LoadCases.ResponseSpectrum.SetEccentricity(nm, 0.0), f"{nm} eccentricity")
    for pc in case.get("pdeltaCases", []):
        nm = pc["name"]
        ok(sm.LoadCases.StaticNonlinear.SetCase(nm), f"nonlinear case {nm}")
        ok(sm.LoadCases.StaticNonlinear.SetGeometricNonlinearity(nm, 1), f"{nm} P-Delta")
        pats = list(pc["loads"].items())
        ok(sm.LoadCases.StaticNonlinear.SetLoads(nm, len(pats), ["Load"] * len(pats), [p for p, _ in pats],
                                                 [float(f) for _, f in pats]), f"{nm} loads")
    geo = {"none": 0, "pdelta": 1, "large": 2}            # eGeometricNonlinearity
    for nc in case.get("nlCases", []):
        nm = nc["name"]
        ok(sm.LoadCases.StaticNonlinear.SetCase(nm), f"nonlinear case {nm}")
        ok(sm.LoadCases.StaticNonlinear.SetGeometricNonlinearity(nm, geo[nc["geometry"]]), f"{nm} geometry")
        pats = list(nc["loads"].items())
        ok(sm.LoadCases.StaticNonlinear.SetLoads(nm, len(pats), ["Load"] * len(pats), [p for p, _ in pats],
                                                 [float(f) for _, f in pats]), f"{nm} loads")
    for bc in case.get("bucklingCases", []):
        say(f"  buckling case {bc['name']}: {set_buckling(sm, bc)}")
    for lk in case.get("links", []):
        say(f"  link {lk['id']}: {set_gap_link(sm, lk)}")
    for fn in case.get("thFunctions", []):
        say(f"  time-history function {fn['name']}: {set_th_function(sm, fn)}")
    for th in case.get("thCases", []):
        say(f"  time-history case {th['name']}: {set_th_case(sm, case, th)}")
    for po in case.get("pushover", []):
        say(f"  pushover {po['name']}: {set_pushover(sm, case, po, point)}")
    for sc in case.get("stagedCases", []):
        say(f"  staged case {sc['name']}: {set_staged(sm, case, sc, point)}")
    # each time history also as an envelope combination: its Max/Min rows give the peaks whatever the
    # time-history output option does (N4 and H2 came back as their last step)
    for nm in [t["name"] for t in case.get("thCases", [])] + list(case.get("extCases", [])):
        try:
            if sm.RespCombo.Add(f"{nm}_ENV", 1) == 0:
                sm.RespCombo.SetCaseList(f"{nm}_ENV", 0, nm, 1.0)
        except Exception as exc:
            say(f"  envelope combination for {nm} not made: {exc}")
    for cb in case.get("combos", []):
        ok(sm.RespCombo.Add(cb["name"], 1 if cb["type"] == "envelope" else 0), f"combination {cb['name']}")
        for cn, f in cb["cases"].items():
            ok(sm.RespCombo.SetCaseList(cb["name"], 0, cn, float(f)), f"combination {cb['name']}: {cn}")


SHELL_TYPE = {"thin": 1, "thick": 2, "membrane": 3}     # eShellType


def _bilinear(c, u, v):
    return [(1 - u) * (1 - v) * c[0][k] + u * (1 - v) * c[1][k] + u * v * c[2][k] + (1 - u) * v * c[3][k]
            for k in range(3)]


def build_areas(sm, case: dict) -> dict:
    """Area sections and objects.  A meshed area is drawn as n1 x n2 separate area objects, so ETABS
    and SkyFrame use the same elements; returns {area id: [ETABS area names]}."""
    one_way = {a["section"] for a in case.get("areas", []) if a.get("toFrame") == "one"}
    for a in case.get("areaSections", []):
        st = SHELL_TYPE[a["shell"]]
        if a["kind"] == "wall":
            ok(sm.PropArea.SetWall(a["name"], 1, st, a["material"], float(a["thickness"])), f"wall {a['name']}")
            back = sm.PropArea.GetWall(a["name"], 0, 0, "", 0.0, 0, "", "")
            got_t, got_st = back[3], back[1]
        elif a["name"] in one_way:
            # one-way load distribution: a ribbed membrane slab, ribs along local 1 (the span)
            t = float(a["thickness"])
            ok(sm.PropArea.SetSlab(a["name"], 3, st, a["material"], t), f"ribbed slab {a['name']}")
            ok(sm.PropArea.SetSlabRibbed(a["name"], t, t / 2, 0.5, 0.5, 2.0, 1), f"ribs of {a['name']}")
            back = sm.PropArea.GetSlab(a["name"], 0, 0, "", 0.0, 0, "", "")
            got_t, got_st = t, back[1]
            if back[0] != 3:
                raise Stop(f"slab {a['name']}: reads back slab type {back[0]}, set ribbed (3)")
            say(f"  area section {a['name']}: ribbed membrane slab, ribs along local 1 (one-way)")
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
    labels = sorted({a["pier"] for a in case.get("areas", []) if a.get("pier")})
    for p in labels:
        if sm.PierLabel.SetPier(p) != 0:
            have = sm.PierLabel.GetNameList(0, [])          # a new model already has pier P1
            ok(have, "list pier labels")
            if p not in list(have[1]):
                raise Stop(f"ETABS refused: pier label {p}")
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
                if a.get("pier"):
                    ok(sm.AreaObj.SetPier(r[3], a["pier"]), f"area {label} pier {a['pier']}")
                if case.get("edgeConstraints"):
                    ok(sm.AreaObj.SetEdgeConstraint(r[3], True), f"area {label} edge constraint")
    if out:
        say(f"  {sum(len(v) for v in out.values())} area objects" + (f"; pier labels {labels}" if labels else "")
            + ("; edge constraints on" if case.get("edgeConstraints") else ""))
    return out


# ── results ─────────────────────────────────────────────────────────────────
def output_cases(case: dict) -> list:
    """(name, kind) of every case and combination whose results are exported."""
    out = [(p["name"], "case") for p in case["patterns"]]
    out += [(c["name"], "case") for c in case.get("pdeltaCases", [])]
    out += [(c["name"], "case") for c in case.get("nlCases", [])]
    out += [(c["name"], "case") for c in case.get("rsCases", [])]
    out += [(c["name"], "case") for c in case.get("thCases", [])]
    out += [(c["name"], "case") for c in case.get("pushover", [])]
    out += [(c["name"], "case") for c in case.get("stagedCases", [])]
    out += [(c, "case") for c in case.get("extCases", [])]      # cases an extension defines
    out += [(c["name"], "combo") for c in case.get("combos", [])]
    return out


def _split(rows: list, step_of) -> dict:
    """Rows of one case -> {"": rows} or, for an envelope, {"": the maxima, ":min": the minima}."""
    out = {"": [], ":min": []}
    for r in rows:
        out[":min" if str(step_of(r)).lower() == "min" else ""].append(r)
    return out


def _peaks(rows: list) -> list:
    """Time-history rows (every step of every joint) -> one row per joint holding, per component, the
    value of largest magnitude, step "Max"."""
    best = {}
    for r in rows:
        b = best.setdefault(r["name"], {"xyz": r["xyz"], "name": r["name"], "step": "Max", "u": [0.0] * 6})
        b["u"] = [v if abs(v) > abs(w) else w for v, w in zip(r["u"], b["u"])]
    return list(best.values())


def results(sm, case: dict, names: dict) -> dict:
    out = {}
    th_names = {t["name"] for t in case.get("thCases", [])}
    coords = {}
    r = sm.PointObj.GetNameList(0, [])
    ok(r, "list points")
    for pn in r[1]:
        c = sm.PointObj.GetCoordCartesian(pn, 0, 0, 0, "Global")
        ok(c, f"coordinates of point {pn}")
        coords[pn] = [c[0], c[1], c[2]]
    ok(sm.Results.Setup.SetOptionNLStatic(3), "nonlinear static results: last step")
    # time histories step by step (2): the peaks are taken here, since asking ETABS for envelopes (1)
    # returned only the last step in N4; staged construction: last stage (3)
    for opt, val in (("SetOptionModalHist", 2), ("SetOptionDirectHist", 2), ("SetOptionMultiStepStatic", 3)):
        try:
            getattr(sm.Results.Setup, opt)(val)
        except Exception:
            pass
    for cname, kind in output_cases(case):
        ok(sm.Results.Setup.DeselectAllCasesAndCombosForOutput(), "deselect output")
        if kind == "combo":
            ok(sm.Results.Setup.SetComboSelectedForOutput(cname), f"select combination {cname}")
        else:
            ok(sm.Results.Setup.SetCaseSelectedForOutput(cname), f"select {cname}")
        parts = {"": {"joints": [], "base": {}, "frames": {}, "drifts": [], "piers": [], "spandrels": []}}
        parts[":min"] = {"joints": [], "base": {}, "frames": {}, "drifts": [], "piers": [], "spandrels": []}

        hist = cname in th_names or cname in case.get("extCases", [])
        if hist:
            # time histories step by step, set right before the read (set once at the top, N4 and H2
            # still came back as their last step only)
            codes = []
            for opt in ("SetOptionModalHist", "SetOptionDirectHist"):
                try:
                    codes.append(f"{opt}(2) -> {getattr(sm.Results.Setup, opt)(2)}")
                except Exception as exc:
                    codes.append(f"{opt}(2) failed: {exc}")
        jd = sm.Results.JointDispl("All", GROUP_ELM, 0, [], [], [], [], [], [], [], [], [], [], [])
        ok(jd, f"joint displacements {cname}")
        n, obj, step = jd[0], jd[1], jd[4]
        u = jd[6:12]
        rows = [{"xyz": coords[obj[k]], "name": obj[k], "step": step[k],
                 "u": [u[0][k], u[1][k], u[2][k], u[3][k], u[4][k], u[5][k]]}
                for k in range(n) if obj[k] in coords]          # diaphragm centre points have no object
        if hist:
            say(f"  {cname}: {'; '.join(codes)}; {len(rows)} joint rows, steps "
                f"{sorted(set(str(r['step']) for r in rows))[:4]}")
        if hist:
            try:                                           # the envelope combination's Max and Min rows
                ok(sm.Results.Setup.DeselectAllCasesAndCombosForOutput(), "deselect output")
                ok(sm.Results.Setup.SetComboSelectedForOutput(f"{cname}_ENV"), f"select {cname}_ENV")
                je = sm.Results.JointDispl("All", GROUP_ELM, 0, [], [], [], [], [], [], [], [], [], [], [])
                ok(je, f"joint displacements {cname}_ENV")
                ue = je[6:12]
                env = [{"xyz": coords[je[1][k]], "name": je[1][k], "step": je[4][k],
                        "u": [ue[0][k], ue[1][k], ue[2][k], ue[3][k], ue[4][k], ue[5][k]]}
                       for k in range(je[0]) if je[1][k] in coords]
                say(f"  {cname}_ENV: {len(env)} rows, steps {sorted(set(str(r['step']) for r in env))[:4]}")
                rows = rows + env
            except Exception as exc:
                say(f"  {cname}_ENV not read: {exc}")
            finally:
                ok(sm.Results.Setup.DeselectAllCasesAndCombosForOutput(), "deselect output")
                ok(sm.Results.Setup.SetCaseSelectedForOutput(cname), f"select {cname}")
        if len({r["name"] for r in rows}) < len(rows):     # several steps per joint: keep each joint's peak
            rows = _peaks(rows)
        for key, rr in _split(rows, lambda x: x["step"]).items():
            parts[key]["joints"] = rr

        br = sm.Results.BaseReact(0, [], [], [], [], [], [], [], [], [], 0, 0, 0)
        ok(br, f"base reactions {cname}")
        for i in range(br[0]):
            key = ":min" if str(br[2][i]).lower() == "min" else ""
            parts[key]["base"] = dict(zip(("FX", "FY", "FZ", "MX", "MY", "MZ"), (br[c][i] for c in range(4, 10))))

        wanted = names
        if len(names) > 200 and not case.get("exportAllFrames"):   # big models: only the frames checked
            used = {c.get("frame") for c in case["checks"]}
            wanted = {f: n for f, n in names.items() if f in used}
        for fid, fname in wanted.items():
            ff = sm.Results.FrameForce(fname, OBJECT_ELM, 0, [], [], [], [], [], [], [], [], [], [], [], [], [])
            ok(ff, f"frame forces {fid} {cname}")
            P, V2, V3, T, M2, M3 = ff[8:14]
            rows = [{"station": ff[2][k], "step": ff[6][k], "P": P[k], "V2": V2[k], "V3": V3[k], "T": T[k],
                     "M2": M2[k], "M3": M3[k]} for k in range(ff[0])]
            for key, rr in _split(rows, lambda x: x["step"]).items():
                parts[key]["frames"][fid] = rr

        if any(c["quantity"] == "drift" for c in case["checks"]):
            sd = sm.Results.StoryDrifts(0, [], [], [], [], [], [], [], [], [], [])
            ok(sd, f"story drifts {cname}")
            rows = [{"story": sd[1][k], "dir": sd[5][k], "drift": sd[6][k], "step": sd[3][k]} for k in range(sd[0])]
            for key, rr in _split(rows, lambda x: x["step"]).items():
                parts[key]["drifts"] = rr

        for what, fn in (("piers", "PierForce"), ("spandrels", "SpandrelForce")):
            if not any(c["quantity"] == what[:-1] for c in case["checks"]):
                continue
            pf = getattr(sm.Results, fn)(0, [], [], [], [], [], [], [], [], [], [])
            ok(pf, f"{what} {cname}")
            parts[""][what] = [{"story": pf[1][k], "name": pf[2][k], "location": pf[4][k],
                                **dict(zip(("P", "V2", "V3", "T", "M2", "M3"), (pf[c][k] for c in range(5, 11))))}
                               for k in range(pf[0])]

        out[cname] = parts[""]
        if parts[":min"]["joints"] or parts[":min"]["base"]:
            out[cname + ":min"] = parts[":min"]
    return out


def reversed_frames(sm, case: dict, names: dict) -> list:
    """Frames ETABS drew from the definition's j end (it orients horizontal members towards +X / +Y):
    their stations run from that end, and the comparison reads them mirrored."""
    out = []
    by_id = {f["id"]: f for f in case["frames"]}
    for fid, fname in names.items():
        try:
            pts = sm.FrameObj.GetPoints(fname, "", "")
            ok(pts, f"end points of {fid}")
            c = sm.PointObj.GetCoordCartesian(pts[0], 0, 0, 0, "Global")
            ok(c, f"coordinates of {fid} end i")
            if math.dist(c[:3], by_id[fid]["i"]) > 1e-6:
                out.append(fid)
        except Stop:
            raise
        except Exception:
            pass
    if out:
        say(f"  ETABS reversed {len(out)} frames: {', '.join(out[:12])}{' ...' if len(out) > 12 else ''}")
    return out


def modal_results(sm, case: dict) -> dict:
    mc = case["modal"]["case"]
    ok(sm.Results.Setup.DeselectAllCasesAndCombosForOutput(), "deselect output")
    ok(sm.Results.Setup.SetCaseSelectedForOutput(mc), f"select {mc}")
    mp = sm.Results.ModalPeriod(0, [], [], [], [], [], [], [])
    ok(mp, "modal periods")
    mr = sm.Results.ModalParticipatingMassRatios(0, [], [], [], [], [], [], [], [], [], [], [], [], [], [], [], [])
    ok(mr, "modal mass ratios")
    # ETABS lists the modes of every modal case whatever is selected: keep this case's rows
    pk = [k for k in range(mp[0]) if str(mp[1][k]).lower() == mc.lower()] or list(range(mp[0]))
    rk = [k for k in range(mr[0]) if str(mr[1][k]).lower() == mc.lower()] or list(range(mr[0]))
    return {"periods": [mp[4][k] for k in pk],
            "ratios": [{"UX": mr[5][k], "UY": mr[6][k], "UZ": mr[7][k], "RZ": mr[13][k]} for k in rk]}


def pick(res: dict, chk: dict):
    if chk["quantity"] == "value":                       # a named scalar an extension exported
        return (res.get("values") or {}).get(chk["key"])
    if chk["quantity"] == "buckling":
        f = (res.get("buckling") or {}).get(chk["case"]) or []
        return f[chk["mode"] - 1] if chk["mode"] <= len(f) else None
    if chk["quantity"] == "modal":
        md = res.get("modal") or {}
        k = chk["mode"] - 1
        rows = md.get("periods" if chk["comp"] == "T" else "ratios", [])
        if k >= len(rows):
            return None
        return rows[k] if chk["comp"] == "T" else rows[k][chk["comp"]]
    c = res["cases"].get(chk["case"])
    if c is None:
        return None
    if chk["quantity"] == "drift":
        rows = [r for r in c.get("drifts", []) if r["story"] == chk["story"] and r["dir"] == chk["comp"]]
        return max((abs(r["drift"]) for r in rows), default=None)
    if chk["quantity"] in ("pier", "spandrel"):
        rows = [r for r in c.get(chk["quantity"] + "s", []) if r["story"] == chk["story"]
                and r["name"] == chk["name"] and r["location"].lower() == chk["location"]]
        return rows[0][chk["comp"]] if rows else None
    if chk["quantity"] == "base":
        return c["base"].get(chk["comp"])
    if chk["quantity"] == "joint":
        k = ["UX", "UY", "UZ", "RX", "RY", "RZ"].index(chk["comp"])
        if not c["joints"]:
            return None
        best = min(c["joints"], key=lambda j: math.dist(j["xyz"], chk["at"]))
        return best["u"][k] if math.dist(best["xyz"], chk["at"]) < 1e-3 else None
    rows = c["frames"].get(chk["frame"]) or []
    if not rows:
        return None
    st = chk["station"]
    if chk["frame"] in res.get("reversed", []):        # ETABS drew it from the other end
        top = max(r["station"] for r in rows)
        st = {"i": "j", "j": "i"}.get(st, top - st if not isinstance(st, str) else st)
    if st == "i":
        row = min(rows, key=lambda r: r["station"])
    elif st == "j":
        row = max(rows, key=lambda r: r["station"])
    else:                                   # a distance from end i, ft (e.g. a support face)
        row = min(rows, key=lambda r: abs(r["station"] - st))
        if abs(row["station"] - st) > 1e-3:
            return None
    return row[chk["comp"]]


def fastest_solver(sm) -> dict:
    """ETABS's fastest setting: the advanced solver, every core, analysis in a separate process."""
    cores = os.cpu_count() or 1
    try:
        ok(sm.Analyze.SetSolverOption_3(1, 2, 0, 0, cores, ""), "advanced solver, all cores")
        got = sm.Analyze.GetSolverOption_3(0, 0, 0, 0, 0, "")
        ok(got, "read back solver options")
        info = {"solver": {0: "standard", 1: "advanced", 2: "multithreaded"}.get(got[0], got[0]),
                "process": got[1], "threads": got[4], "cores": cores}
    except Exception as exc:
        info = {"solver": f"not set: {exc}", "cores": cores}
    say(f"  ETABS solver: {info}")
    return info


def benchmark(sm, case: dict) -> dict:
    """Time the linear and the nonlinear part separately (analysis only: building is excluded)."""
    import platform
    out = {"etabs": fastest_solver(sm), "machine": platform.processor() or platform.machine()}
    repeat = int(case["benchmark"].get("repeat", BENCH_REPEAT))
    out["repeat"] = repeat
    for part in ("linear", "nonlinear"):
        runs, logs = [], []
        for _ in range(repeat):                 # best of several, as SkyFrame is timed: runs vary by a few %
            ok(sm.Analyze.SetRunCaseFlag("", False, True), "switch all cases off")
            for cname in case["benchmark"][part]:
                ok(sm.Analyze.SetRunCaseFlag(cname, True, False), f"switch case {cname} on")
            t0 = time.perf_counter()
            code = sm.Analyze.RunAnalysis()
            if code != 0:
                say(f"  ETABS refused the {part} run (code {code}); its analysis log ends:\n" + analysis_log_tail(sm))
                try:
                    sm.SetModelIsLocked(False)
                    sm.File.Save(sm.GetModelFilename(True))
                except Exception:
                    pass
                ok(sm.Analyze.SetRunCaseFlag("", False, True), "switch all cases off")
                for cname in case["benchmark"][part]:
                    ok(sm.Analyze.SetRunCaseFlag(cname, True, False), f"switch case {cname} on")
                t0 = time.perf_counter()
                ok(sm.Analyze.RunAnalysis(), f"run the {part} part (retry)")
            runs.append(time.perf_counter() - t0)
            logs.append(analysis_log_text(sm))   # ETABS's own phase-by-phase log of this run
            try:
                ok(sm.SetModelIsLocked(False), "unlock the model")
            except Exception:
                pass
        out[f"{part}_s"] = min(runs)
        out[f"{part}_runs_s"] = runs
        out[f"{part}_logs"] = logs              # split into phases (solve vs the rest) from these
        say(f"  ETABS {part} run: best {min(runs):.2f} s of {', '.join(f'{t:.2f}' for t in runs)} "
            f"({', '.join(case['benchmark'][part])})")
    return out


BRANCH = "claude/etabs-opensees-ui-rebuild-kt2zwh"
BENCH_REPEAT = 3               # timed runs per part, both programs; the best is reported, every run kept


PY312_NUGET = "https://www.nuget.org/api/v2/package/python/3.12.10"


def private_python312():
    """A Python 3.12 of its own under %LOCALAPPDATA%\\SkyFramePython312 (NuGet zip, pip bootstrapped)."""
    import subprocess
    import zipfile
    home = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SkyFramePython312"
    exe = home / "tools" / "python.exe"
    if not exe.exists():
        say("SkyFrame speed run: unpacking a private Python 3.12 (NuGet) ...")
        try:
            home.mkdir(parents=True, exist_ok=True)
            pkg = home / "python312.nupkg"
            with urllib.request.urlopen(PY312_NUGET, timeout=300) as r:
                pkg.write_bytes(r.read())
            zipfile.ZipFile(pkg).extractall(home)
        except Exception as exc:
            say(f"  could not get Python 3.12: {exc}")
            return None
    if subprocess.run([str(exe), "-m", "pip", "--version"], capture_output=True).returncode != 0:
        subprocess.run([str(exe), "-m", "ensurepip", "--upgrade"], capture_output=True)
    return [str(exe)] if exe.exists() else None


def skyframe_speed(out_root: Path, case: dict) -> None:
    """Time SkyFrame on this machine on the same benchmark model: the latest SkyFrame code is taken
    from the GitHub clone (git archive, nothing in the clone changes) and run with Python 3.11/3.12."""
    import shutil
    import subprocess
    import tempfile
    import zipfile
    repo = out_root.parents[1]
    if not (repo / ".git").exists():
        say(f"SkyFrame speed run skipped: {repo} is not the GitHub clone")
        return
    src = Path(tempfile.gettempdir()) / "skyframe_speed_src"
    shutil.rmtree(src, ignore_errors=True)
    src.mkdir(parents=True)
    say("SkyFrame speed run: fetching the latest SkyFrame ...")
    subprocess.run(["git", "-C", str(repo), "fetch", "--quiet", "origin", BRANCH], check=False)
    z = src / "src.zip"
    r = subprocess.run(["git", "-C", str(repo), "archive", "--format=zip", "-o", str(z), f"origin/{BRANCH}",
                        "skyframe/skyframe", "skyframe/verification/etabs"], capture_output=True, text=True)
    if r.returncode:
        say(f"SkyFrame speed run skipped: git archive failed: {r.stderr.strip()}")
        return
    zipfile.ZipFile(z).extractall(src)
    py = None
    # OpenSeesPy's Windows module (openseespywin, every current wheel) links python312.dll: it loads
    # under Python 3.12 only.  Without an installed 3.12, a private copy is unpacked from the NuGet
    # "python" package into the user's profile (no installer, no admin, other Pythons untouched).
    for cand in (["py", "-3.12"], "private 3.12"):
        if cand == "private 3.12":
            cand = private_python312()
            if cand is None:
                continue
        try:
            if subprocess.run(cand + ["-c", "import sys"], capture_output=True).returncode != 0:
                continue
        except OSError:
            continue
        say(f"SkyFrame speed run: installing OpenSeesPy and PARDISO for {' '.join(cand)} ...")
        subprocess.run(cand + ["-m", "pip", "install", "--user", "--quiet", "--upgrade", "openseespy",
                               "openseespywin", "numpy", "scipy", "pypardiso", "flask"], check=False)
        t = subprocess.run(cand + ["-c", "import openseespy.opensees as o; o.wipe(); print('ok')"],
                           capture_output=True, text=True)
        if t.returncode == 0:
            py = cand
            break
        say(f"  OpenSeesPy does not import under {' '.join(cand)}:")
        for line in (t.stdout + t.stderr).splitlines()[-8:]:
            say("    " + line)
    if py is None:
        say("SkyFrame speed run skipped: OpenSeesPy imports under none of the Pythons found")
        return
    work = out_root / case["id"]
    r = subprocess.run(py + [str(src / "skyframe" / "verification" / "etabs" / "speed_skyframe.py"),
                             "--case", str(work / f"{case['id']}.case.json"),
                             "--out", str(work / f"{case['id']}.skyframe-speed.json"), "--repeat", str(BENCH_REPEAT)],
                       capture_output=True, text=True)
    for line in (r.stdout + r.stderr).splitlines()[-30:]:
        say("  " + line)
    if r.returncode:
        # openseespy hides why its Windows module did not load: import that module directly
        d = subprocess.run(py + ["-c", "import numpy, scipy\ntry:\n import pypardiso\nexcept Exception: pass\n"
                                       "import openseespywin.opensees"], capture_output=True, text=True)
        say("  OpenSeesPy after numpy/scipy/PARDISO: " + ("imports" if d.returncode == 0 else "fails:"))
        for line in (d.stdout + d.stderr).splitlines()[-8:]:
            say("    " + line)


def _pillow():
    """PIL.ImageGrab, installing Pillow for this Python on first use; None when it cannot."""
    try:
        from PIL import ImageGrab
        return ImageGrab
    except ImportError:
        import subprocess
        say("Installing Pillow (for the ETABS screenshots) ...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet", "pillow"], check=False)
        try:
            import site
            import importlib
            sys.path.append(site.getusersitepackages())
            importlib.invalidate_caches()
            from PIL import ImageGrab
            return ImageGrab
        except Exception:
            return None


def _etabs_windows():
    """Top-level windows that belong to an ETABS.exe process (never a browser tab named ETABS)."""
    import ctypes
    from ctypes import wintypes as W
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, W.HWND, W.LPARAM)

    def each(h, _):
        if not user32.IsWindowVisible(h):
            return True
        pid = W.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        hp = kernel32.OpenProcess(0x1000, False, pid.value)          # query limited information
        if hp:
            buf, n = ctypes.create_unicode_buffer(520), W.DWORD(520)
            if kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(n)) and \
                    buf.value.lower().endswith("etabs.exe"):
                found.append(h)
            kernel32.CloseHandle(hp)
        return True

    user32.EnumWindows(proc(each), 0)
    return found


def _child_titled(parent, prefix: str):
    """The first child window whose title starts with ``prefix`` (ETABS's 3-D view pane)."""
    import ctypes
    from ctypes import wintypes as W
    user32 = ctypes.windll.user32
    hit = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, W.HWND, W.LPARAM)

    def each(h, _):
        n = user32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, buf, n + 1)
        if buf.value.startswith(prefix) and user32.IsWindowVisible(h):
            hit.append(h)
            return False
        return True

    user32.EnumChildWindows(parent, proc(each), 0)
    return hit[0] if hit else None


def _print_window(h):
    """The window's own pixels (PrintWindow, full content): nothing on top of it can appear."""
    import ctypes
    from ctypes import wintypes as W
    from PIL import Image
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    r = W.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    w, ht = r.right - r.left, r.bottom - r.top
    if w <= 0 or ht <= 0:
        return None
    hdc = user32.GetWindowDC(h)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, ht)
    gdi32.SelectObject(mem, bmp)
    ok_ = user32.PrintWindow(h, mem, 2)                                # PW_RENDERFULLCONTENT

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD),
                    ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                    ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD),
                    ("biClrImportant", W.DWORD)]
    bi = BIH(ctypes.sizeof(BIH), w, -ht, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * ht * 4)
    gdi32.GetDIBits(mem, bmp, 0, ht, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(h, hdc)
    if not ok_:
        return None
    return Image.frombuffer("RGB", (w, ht), buf, "raw", "BGRX", 0, 1)


def screenshot(sm, cid: str, path: Path) -> None:
    """Save ETABS's 3-D view after the run (its deflected shape).  Only that pane of the ETABS window
    is captured, from the window's own pixels, so other windows and pop-ups cannot appear."""
    try:
        if _pillow() is None:
            say("  no screenshot: Pillow could not be installed")
            return
        from PIL import ImageStat
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        try:
            sm.View.RefreshView(0, False)
        except Exception:
            pass
        mains = _etabs_windows()
        if not mains:
            say("  no screenshot: no ETABS window found")
            return
        pane = None
        for m in mains:
            pane = _child_titled(m, "3-D View")
            if pane:
                break
        if pane is None:
            say("  no screenshot: ETABS has no 3-D view open")
            return
        img = _print_window(pane)
        if img is None or max(ImageStat.Stat(img).stddev) < 4:
            say("  no screenshot: the 3-D view could not be captured")
            return
        img.save(str(path))
        say(f"  screenshot {path.name} (ETABS 3-D view only)")
    except Exception as exc:                     # pictures are a bonus: never stop the run for one
        say(f"  no screenshot: {exc}")


def run_case(sm, case: dict, out_root: Path, version: str) -> dict:
    """Build, save, analyse and read one case; returns its summary."""
    work = out_root / case["id"]
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{case['id']}.case.json").write_text(json.dumps(case, indent=1), encoding="utf-8")
    say(f"== {case['id']}: {case['title']}")
    ext = load_ext(case)
    if ext is not None and hasattr(ext, "run_case"):
        # the extension builds (or opens), runs and reads the case itself, e.g. a real project
        res = ext.run_case(sm, case, sys.modules[__name__], work)
        res.update({"program": "ETABS", "version": version, "units": "kip-ft", "case": case["id"], "runner": RUNNER})
        (work / f"{case['id']}.etabs.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        say(f"Saved the results of {case['id']}")
        return {"id": case["id"], "ok": True, "worst": 0.0, "folder": str(work)}
    say("Building the model ...")
    names = build(sm, case)
    edb = str(work / f"{case['id']}.EDB")
    ok(sm.File.Save(edb), f"save {edb}")
    ok(sm.Analyze.SetRunCaseFlag("", False, True), "switch all cases off")
    run = [n for n, kind in output_cases(case) if kind == "case"]
    if case.get("modal"):
        run.append(case["modal"]["case"])
    run += [b["name"] for b in case.get("bucklingCases", [])]
    if ext is not None and hasattr(ext, "analysis_cases"):
        run += list(ext.analysis_cases(case))
    timing = benchmark(sm, case) if case.get("benchmark") else None
    ok(sm.Analyze.SetRunCaseFlag("", False, True), "switch all cases off")
    for cname in run:
        ok(sm.Analyze.SetRunCaseFlag(cname, True, False), f"switch case {cname} on")
    say("Running the analysis ...")
    ok(sm.Analyze.RunAnalysis(), "run analysis")
    screenshot(sm, case["id"], work / f"{case['id']}.etabs.png")
    say("Reading results ...")
    res = {"program": "ETABS", "version": version, "units": "kip-ft", "case": case["id"], "runner": RUNNER,
           "cases": results(sm, case, names), "reversed": reversed_frames(sm, case, names)}
    if case.get("modal"):
        res["modal"] = modal_results(sm, case)
    if case.get("bucklingCases"):
        res["buckling"] = {}
        for bc in case["bucklingCases"]:
            ok(sm.Results.Setup.DeselectAllCasesAndCombosForOutput(), "deselect output")
            ok(sm.Results.Setup.SetCaseSelectedForOutput(bc["name"]), f"select {bc['name']}")
            bf = sm.Results.BucklingFactor(0, [], [], [], [])
            ok(bf, f"buckling factors {bc['name']}")
            res["buckling"][bc["name"]] = list(bf[4][:bf[0]])
    if timing:
        res["timing"] = timing
    res["values"] = {}
    if ext is not None and hasattr(ext, "results"):
        ext.results(sm, case, sys.modules[__name__], names, res)   # extra outputs: res["values"][key]
    if case.get("areas"):
        try:
            n_elm, n_obj = sm.AreaElm.Count(), sm.AreaObj.Count()
            say(f"  ETABS analysed {n_elm} area elements for {n_obj} area objects"
                + ("" if n_elm == n_obj else "  (it meshed the objects further: meshes differ from SkyFrame)"))
        except Exception as exc:                         # informative only
            say(f"  could not count area elements: {exc}")
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


_TEXT_ONLY = ("title", "purpose", "settings", "checks", "notes")
RUNNER = 7
# cases whose ETABS model this script built wrongly before the given runner version, so they rerun:
# 2 = releases on members ETABS draws from the definition's j end went on the wrong end
# 3 = H3's Ritz results were read across every modal case at once; K4's steel curve call had a wrong argument
# 4 = N4's time history was read at its last step instead of its peak; K4's layered wall was unstable in ETABS
# 5 = N4 and H2 time histories still read as their last step; C1-C3 time dependence and hinges not yet right
# 6 = SP1-SP3 timed once in ETABS: now best of three in both programs, every run kept
# 7 = SP1-SP3 with ETABS's analysis log of each timed run and SkyFrame's time inside its solvers
RERUN_BEFORE = {"L1": 2, "L1S": 2, "L2": 2, "H3": 3, "K4": 4, "N4": 5, "H2": 5, "C1": 5, "C2": 5, "C3": 5, "SP1": 7, "SP2": 7, "SP3": 7}


def needs_run(out_root: Path, cid: str) -> bool:
    """True when a case has no ETABS results here, or its model changed since they were made
    (descriptions and checks may change without a rerun)."""
    work = out_root / cid
    if not (work / f"{cid}.etabs.json").exists() or not (work / f"{cid}.case.json").exists():
        return True
    try:
        ran = json.loads((work / f"{cid}.case.json").read_text(encoding="utf-8"))
        now = json.loads(fetch(f"cases/{cid}.json"))
    except Exception:
        return True
    if cid in RERUN_BEFORE:
        try:
            had = json.loads((work / f"{cid}.etabs.json").read_text(encoding="utf-8")).get("runner", 1)
        except Exception:
            had = 1
        if had < RERUN_BEFORE[cid]:
            return True
    model = lambda d: {k: v for k, v in d.items() if k not in _TEXT_ONLY}
    return model(ran) != model(now)


def etabs_gone(exc: Exception) -> bool:
    """True when the error means the ETABS process is gone or no longer answers COM calls."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(w in text for w in ("invalid pointer", "rpc server", "not set to an instance", "disconnected",
                                   "-2147467261", "-2147023174", "-2147417848"))


def analysis_log_text(sm, limit: int = 200_000) -> str:
    """ETABS's analysis log (<model>.LOG) as it stands after a run: it stamps each phase with the
    clock time, which splits the run into equation solution and the rest."""
    try:
        log = Path(str(sm.GetModelFilename(True))).with_suffix(".LOG")
        return log.read_text(encoding="utf-8", errors="replace")[-limit:] if log.exists() else f"(no {log})"
    except Exception as exc:
        return f"(analysis log not read: {exc})"


def analysis_log_tail(sm, lines: int = 25) -> str:
    """The end of ETABS's own analysis log (<model>.LOG next to the model), for a refused analysis."""
    try:
        name = sm.GetModelFilename(True)
        log = Path(str(name)).with_suffix(".LOG")
        if log.exists():
            return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
        return f"(no analysis log at {log})"
    except Exception as exc:
        return f"(analysis log not read: {exc})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*", help="case ids, e.g. F1 F2; or --all")
    ap.add_argument("--all", action="store_true",
                    help="every published case that has no results here yet or whose model has changed")
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
    speed_only: list = []
    if a.local:
        todo = [json.loads(Path(a.local).read_text())]
        _LOCAL_EXT.append(True)
    else:
        ids = list(a.cases)
        if a.all:
            index = json.loads(fetch("cases/index.json"))
            ids += [c for c in index if a.force or needs_run(out_root, c)]
            # benchmarks whose ETABS run stands but whose SkyFrame timing (with its answers) is missing:
            # time SkyFrame only, from the definition stored with the ETABS run
            for c in index:
                work = out_root / c
                sj = work / f"{c}.skyframe-speed.json"
                if c in ids or not (work / f"{c}.etabs.json").exists() or not (work / f"{c}.case.json").exists():
                    continue
                stored = json.loads((work / f"{c}.case.json").read_text(encoding="utf-8"))
                if stored.get("benchmark") and (not sj.exists() or "values" not in sj.read_text(encoding="utf-8")):
                    speed_only.append(stored)
        if not ids and not speed_only:
            say("Nothing to run: every published case already has results here (use --force to rerun).")
            return 0
        todo = [json.loads(fetch(f"cases/{c}.json")) for c in dict.fromkeys(ids)]
        # the large speed benchmarks last: if one takes ETABS down, every other model has already run
        todo.sort(key=lambda c: bool(c.get("benchmark")))
    say(f"Cases: {', '.join(c['id'] for c in todo) or 'none'}; SkyFrame timing only: {', '.join(c['id'] for c in speed_only) or 'none'}; saving under {out_root}")

    import comtypes.client

    def start_etabs():
        helper = comtypes.client.CreateObject("ETABSv1.Helper")
        import comtypes.gen.ETABSv1 as E
        helper = helper.QueryInterface(E.cHelper)
        app = helper.CreateObject(a.etabs)
        ok(app.ApplicationStart(), "start ETABS")
        return app, app.SapModel

    etabs, sm = start_etabs()
    version = sm.GetVersion()[0]
    say(f"ETABS {version} started")
    try:
        api_probe(sm, out_root)
    except Exception as exc:
        say(f"API probe failed: {exc}")
    summaries = []
    try:
        for case in todo:
            _LOG.clear()
            try:
                try:
                    summaries.append(run_case(sm, case, out_root, version))
                except Exception as exc:
                    if not etabs_gone(exc):
                        raise
                    # ETABS itself died (SP2's refused analysis took it down, and every later case failed
                    # with "Invalid pointer"): start a fresh ETABS and give this case one more go
                    say(f"ETABS stopped responding ({exc}); restarting it and retrying {case['id']}")
                    try:
                        etabs.ApplicationExit(False)
                    except Exception:
                        pass
                    etabs, sm = start_etabs()
                    summaries.append(run_case(sm, case, out_root, version))
            except Stop as exc:
                say(f"STOPPED on {case['id']}: {exc}")
                summaries.append({"id": case["id"], "ok": False, "error": str(exc)})
            except Exception as exc:                 # one model's surprise must not end the whole run
                import traceback
                say(f"STOPPED on {case['id']}: {type(exc).__name__}: {exc}")
                say(traceback.format_exc())
                summaries.append({"id": case["id"], "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            finally:
                (out_root / case["id"]).mkdir(parents=True, exist_ok=True)
                (out_root / case["id"] / f"{case['id']}.log.txt").write_text("\n".join(_LOG) + "\n", encoding="utf-8")
        for case in todo + speed_only:
            if case.get("benchmark") and (case in speed_only or any(x["id"] == case["id"] and x["ok"] for x in summaries)):
                _LOG.clear()
                try:
                    skyframe_speed(out_root, case)
                except Exception as exc:
                    say(f"SkyFrame speed run failed: {exc}")
                (out_root / case["id"] / f"{case['id']}.skyframe-speed.log.txt").write_text(
                    "\n".join(_LOG) + "\n", encoding="utf-8")
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
        try:                        # the whole console, pushed with the results: a closed window hides nothing
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            (out_root / "_last_run.txt").write_text(f"run finished {stamp}; runner {RUNNER}, commit {_head() or '?'}\n"
                                                    + "\n".join(_RUN_LOG) + "\n", encoding="utf-8")
        except Exception:
            pass
    return 0 if all(s["ok"] for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
