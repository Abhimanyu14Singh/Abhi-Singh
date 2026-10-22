r"""ETABS side of the real-project comparison (case R1, "ext": "real").

The runner (run_etabs.py) downloads this file next to itself and calls ``run_case(sm, case, R, work)``
instead of building a model.  For every ``*.EDB`` in ``<out>\RealProjects`` (``work.parent``):

1. the EDB is copied to ``<out>\R1\<name>\`` and the copy is opened (the original is never changed or
   analysed, and its own folder gets no analysis files);
2. the copy is saved, which makes ETABS write its text export ``<name>.$et`` beside it; that file is
   copied to ``<out>\R1\<name>.$et`` (pushed with the results) and also embedded, zlib-compressed and
   base64, in the results (``projects[name]["e2k"]``), so SkyFrame has the model even where only
   R1.etabs.json is collected (an ``<name>.e2k`` / ``.$et`` next to the EDB is the fallback);
3. the analysis cases the model has set to run are run (all of them when none is set), with ETABS's
   advanced solver on all cores, and timed;
4. per project, in kip-ft: base reactions per load case and combination, modal periods and
   participating mass ratios, story drifts (largest per story and direction) and the largest joint
   displacements per case and combination;
5. the analysed copy and its analysis files are deleted (they can be hundreds of MB, too big to push);
   ``keepAnalysis: true`` in the case keeps them.

An empty or missing folder is reported and the case finishes cleanly.  A project that fails stops only
itself: its entry carries the error and the next project runs.
"""

from __future__ import annotations

import base64
import shutil
import time
import zlib
from pathlib import Path

# eLoadCaseType
CASE_TYPES = {1: "LinearStatic", 2: "NonlinearStatic", 3: "Modal", 4: "ResponseSpectrum", 5: "LinearHistory",
              6: "NonlinearHistory", 7: "LinearDynamic", 8: "NonlinearDynamic", 9: "MovingLoad", 10: "Buckling",
              11: "SteadyState", 12: "PowerSpectralDensity", 13: "LinearStaticMultiStep", 14: "HyperStatic"}
NO_FORCE_OUTPUT = {"Modal", "Buckling", "SteadyState", "PowerSpectralDensity", "HyperStatic"}
FINISHED = 4                                    # eCaseStatus: 1 not run, 2 could not start, 3 not finished
EMBED_LIMIT = 60 * 2**20                        # bytes of text export embedded in the JSON at most


def _items(ret, k):
    """Item k of a comtypes result as a plain list (None-safe)."""
    v = ret[k]
    return list(v) if v is not None else []


def _code(ret):
    return ret[-1] if isinstance(ret, (list, tuple)) else ret


def _case_type(sm, name: str) -> str:
    """The load case's type name, or "" when this ETABS cannot say (GetTypeOAPI / _1 differ by version)."""
    lc = sm.LoadCases
    for meth, args in (("GetTypeOAPI_1", (name, 0, 0, 0, 0, 0)), ("GetTypeOAPI", (name, 0, 0)),
                       ("GetType", (name, 0, 0))):
        if not hasattr(lc, meth):
            continue
        try:
            r = getattr(lc, meth)(*args)
            if _code(r) == 0:
                return CASE_TYPES.get(int(r[0]), str(r[0]))
        except Exception:
            continue
    return ""


def _names(R, call, what: str) -> list:
    r = call()
    R.ok(r, what)
    return [str(x) for x in _items(r, 1)][: int(r[0])]


def _select(sm, R, name: str, kind: str) -> None:
    R.ok(sm.Results.Setup.DeselectAllCasesAndCombosForOutput(), "deselect output")
    if kind == "combo":
        R.ok(sm.Results.Setup.SetComboSelectedForOutput(name), f"select combination {name}")
    else:
        R.ok(sm.Results.Setup.SetCaseSelectedForOutput(name), f"select case {name}")


def _base(sm, R, name: str) -> dict:
    """Base reactions about the global origin: {"FX".."MZ"}, plus "min" for an envelope's minima."""
    br = sm.Results.BaseReact(0, [], [], [], [], [], [], [], [], [], 0, 0, 0)
    R.ok(br, f"base reactions {name}")
    out = {}
    for i in range(int(br[0])):
        row = dict(zip(("FX", "FY", "FZ", "MX", "MY", "MZ"), (float(br[c][i]) for c in range(4, 10))))
        step = str(br[2][i]).lower()
        if step == "min":
            out["min"] = row
        else:                                   # single row, "Max", or the last step of a multi-step case
            out.update(row)
    return out


def _joint_max(sm, R, name: str) -> dict:
    """Largest |UX|, |UY|, |UZ| over every joint (mesh points included), with the joint it occurs at."""
    jd = sm.Results.JointDispl("All", R.GROUP_ELM, 0, [], [], [], [], [], [], [], [], [], [], [])
    R.ok(jd, f"joint displacements {name}")
    n, elm = int(jd[0]), jd[2]
    out = {}
    for k, comp in enumerate(("UX", "UY", "UZ")):
        col = jd[6 + k]
        best = max(range(n), key=lambda i: abs(col[i]), default=None)
        out[comp] = 0.0 if best is None else abs(float(col[best]))
        out[comp + "_at"] = "" if best is None else str(elm[best])
    out["joints"] = len(set(elm[:n])) if n else 0
    return out


def _drifts(sm, R, name: str) -> dict:
    """Largest story drift ratio per story and direction: {story: {"X": d, "Y": d}}."""
    sd = sm.Results.StoryDrifts(0, [], [], [], [], [], [], [], [], [], [])
    R.ok(sd, f"story drifts {name}")
    out = {}
    for k in range(int(sd[0])):
        st, dr = str(sd[1][k]), str(sd[5][k]).upper()
        if dr not in ("X", "Y"):
            continue
        cur = out.setdefault(st, {})
        cur[dr] = max(cur.get(dr, 0.0), abs(float(sd[6][k])))
    return out


def _modal(sm, R, name: str) -> dict:
    mp = sm.Results.ModalPeriod(0, [], [], [], [], [], [], [])
    R.ok(mp, f"modal periods {name}")
    mr = sm.Results.ModalParticipatingMassRatios(0, [], [], [], [], [], [], [], [], [], [], [], [], [], [], [], [])
    R.ok(mr, f"modal mass ratios {name}")
    return {"periods": [float(t) for t in list(mp[4])[: int(mp[0])]],
            "ratios": [{"UX": float(mr[5][k]), "UY": float(mr[6][k]), "UZ": float(mr[7][k]),
                        "RZ": float(mr[13][k]), "SumUX": float(mr[8][k]), "SumUY": float(mr[9][k])}
                       for k in range(int(mr[0]))]}


def _try(errors: list, label: str, call, default=None):
    try:
        return call()
    except Exception as exc:                   # one quantity that fails must not lose the project
        errors.append(f"{label}: {exc}")
        return default


def _text_export(pdir: Path, name: str, original: Path):
    """The model's text export: <name>.$et that ETABS wrote on save, else an .e2k / .$et the user put
    next to the EDB."""
    cands = [pdir / f"{name}.$et", pdir / f"{name}.e2k"]
    cands += sorted(p for p in pdir.iterdir() if p.suffix.lower() in (".$et", ".e2k"))
    cands += [original.with_suffix(s) for s in (".$et", ".e2k", ".E2K", ".$ET")]
    return next((p for p in cands if p.exists() and p.stat().st_size > 0), None)


def one_project(sm, R, edb: Path, work: Path, proj: dict) -> None:
    """Open, run and read one project, filling ``proj`` as it goes (what was read survives a stop)."""
    name = edb.stem
    pdir = work / name
    shutil.rmtree(pdir, ignore_errors=True)
    pdir.mkdir(parents=True, exist_ok=True)
    copy = pdir / edb.name
    shutil.copy2(edb, copy)
    R.say(f"-- {name}: opening a copy of {edb}")
    R.ok(sm.File.OpenFile(str(copy)), f"open {copy}")
    R.ok(sm.SetPresentUnits(R.KIP_FT_F), "units kip-ft")
    errors: list = []
    proj["errors"] = errors
    proj["size"] = {k: _try(errors, f"count {k}", lambda o=o: int(getattr(sm, o).Count()))
                    for k, o in (("points", "PointObj"), ("frames", "FrameObj"), ("areas", "AreaObj"),
                                 ("links", "LinkObj"), ("area_elements", "AreaElm"))}
    try:
        cases = _names(R, lambda: sm.LoadCases.GetNameList(0, []), "list load cases")
    except Exception:                           # the optional case-type filter made explicit (0 = all)
        cases = _names(R, lambda: sm.LoadCases.GetNameList(0, [], 0), "list load cases")
    combos = _try(errors, "list combinations", lambda: _names(R, lambda: sm.RespCombo.GetNameList(0, []),
                                                              "list combinations"), [])
    types = {c: _case_type(sm, c) for c in cases}
    for c, t in types.items():                  # type unknown to this ETABS's API: ETABS's default name
        if not t and c.lower().startswith("modal"):
            types[c] = "Modal"
    flags = _try(errors, "run flags", lambda: sm.Analyze.GetRunCaseFlag(0, [], []))
    chosen = []
    if flags is not None and _code(flags) == 0:
        chosen = [str(n) for n, f in zip(_items(flags, 1), _items(flags, 2)) if f]
    if not chosen:
        R.ok(sm.Analyze.SetRunCaseFlag("", True, True), "switch every case on")
        chosen = list(cases)
        proj["runCases"] = "all (none was set to run)"
    else:
        proj["runCases"] = "as set in the model"
    R.say(f"   {len(cases)} load cases ({len(chosen)} to run), {len(combos)} combinations; size {proj['size']}")
    R.ok(sm.File.Save(str(copy)), f"save {copy}")      # writes <name>.$et; the analysis needs a saved model
    # the text export is kept before the analysis, so SkyFrame still gets the model if ETABS stops on it
    et = _text_export(pdir, name, edb)
    if et is None:
        errors.append("no text export (.$et) found after saving: SkyFrame cannot run this project")
        R.say(f"   {errors[-1]}")
    else:
        raw = et.read_bytes()
        shutil.copy2(et, work / f"{name}.$et")
        proj["e2kFile"] = f"{name}.$et"
        proj["e2kBytes"] = len(raw)
        if len(raw) <= EMBED_LIMIT:
            proj["e2k"] = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
        R.say(f"   text export {et.name} ({len(raw) / 2**20:.1f} MB) copied to {work / proj['e2kFile']}")
    proj["solver"] = _try(errors, "solver", lambda: R.fastest_solver(sm))
    t0 = time.perf_counter()
    R.ok(sm.Analyze.RunAnalysis(), f"run {name}")
    proj["analysis_s"] = time.perf_counter() - t0
    R.say(f"   ETABS analysis: {proj['analysis_s']:.1f} s")
    R.ok(sm.SetPresentUnits(R.KIP_FT_F), "units kip-ft")

    status = {}
    st = _try(errors, "case status", lambda: sm.Analyze.GetCaseStatus(0, [], []))
    if st is not None and _code(st) == 0:
        status = {str(n): int(s) for n, s in zip(_items(st, 1), _items(st, 2))}
    # without a status from ETABS, the cases it was asked to run are taken as done
    done = [c for c in cases if status.get(c) == FINISHED] if status else [c for c in cases if c in chosen]
    proj["cases"] = {c: {"type": types[c], "ran": c in done} for c in cases}
    not_done = [c for c in chosen if c not in done]
    if not_done:
        R.say(f"   not finished: {', '.join(not_done[:20])}")

    R.ok(sm.Results.Setup.SetOptionNLStatic(3), "nonlinear static results: last step")
    for opt, val in (("SetOptionModalHist", 1), ("SetOptionDirectHist", 1), ("SetOptionMultiStepStatic", 3),
                     ("SetOptionMultiValuedCombo", 1)):
        if hasattr(sm.Results.Setup, opt):     # time histories: envelopes; staged / multi-step: last step
            _try(errors, opt, lambda o=opt, v=val: getattr(sm.Results.Setup, o)(v))
    out = {}
    for cname, kind in [(c, "case") for c in done] + [(c, "combo") for c in combos]:
        if kind == "case" and types.get(cname) in NO_FORCE_OUTPUT:
            continue
        sel = _try(errors, f"select {cname}", lambda: _select(sm, R, cname, kind), False)
        if sel is False:
            continue
        out[cname] = {"kind": kind, "type": types.get(cname, "") if kind == "case" else "Combination",
                      "base": _try(errors, f"base {cname}", lambda: _base(sm, R, cname), {}),
                      "maxDispl": _try(errors, f"joints {cname}", lambda: _joint_max(sm, R, cname), {}),
                      "drifts": _try(errors, f"drifts {cname}", lambda: _drifts(sm, R, cname), {})}
    proj["results"] = out
    proj["modal"] = {}
    for cname in done:
        if types.get(cname) == "Modal":
            _try(errors, f"select {cname}", lambda: _select(sm, R, cname, "case"))
            md = _try(errors, f"modal {cname}", lambda: _modal(sm, R, cname))
            if md:
                proj["modal"][cname] = md
                R.say(f"   {cname}: T1..3 = {', '.join(f'{t:.3f}' for t in md['periods'][:3])} s")
    R.say(f"   exported {len(out)} cases and combinations, {len(proj['modal'])} modal case(s)"
          + (f"; {len(errors)} quantities not read (see errors)" if errors else ""))


def run_case(sm, case: dict, R, work: Path) -> dict:
    folder = work.parent / case.get("folder", "RealProjects")
    res = {"cases": {}, "values": {}, "projects": {}, "folder": str(folder)}
    if not folder.is_dir():
        folder.mkdir(parents=True, exist_ok=True)
    edbs = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".edb")
    if not edbs:
        res["note"] = f"no ETABS models (.EDB) in {folder}"
        R.say(f"R1: {res['note']}: put the project models there and run R1 again "
              f"(python run_etabs.py R1 --out \"{work.parent}\")")
        return res
    R.say(f"R1: {len(edbs)} project(s) in {folder}: {', '.join(p.name for p in edbs)}")
    for edb in edbs:
        proj = res["projects"][edb.stem] = {"edb": edb.name}
        try:
            one_project(sm, R, edb, work, proj)
        except Exception as exc:                # one project's failure must not stop the others
            R.say(f"   {edb.stem}: STOPPED: {type(exc).__name__}: {exc}")
            proj["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            # release the copy, then delete it with its analysis files (never the original)
            try:
                sm.InitializeNewModel(R.KIP_FT_F)
            except Exception:
                pass
            if not case.get("keepAnalysis"):
                shutil.rmtree(work / edb.stem, ignore_errors=True)
    ok_n = sum(1 for p in res["projects"].values() if "error" not in p)
    R.say(f"R1: {ok_n} of {len(edbs)} project(s) exported")
    return res
