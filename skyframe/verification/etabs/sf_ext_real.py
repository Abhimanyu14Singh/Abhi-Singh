"""SkyFrame side of the real-project comparison (case R1, "ext": "real").

``run(case)`` finds every project ETABS exported, imports its text export (``.$et`` / ``.e2k``) with
SkyFrame's e2k reader, runs it, and returns per project (kip-ft, the same shape as the ETABS side,
``etabs_ext_real.py``):

    res["projects"][name] = {
        "size":     {"nodes", "members", "shells", "links", "equations"},
        "import_s", "analysis_s", "warnings": [...], "warningCount",
        "results":  {case or combination: {"kind", "type", "base": {FX..MZ, "min"?},
                                           "maxDispl": {UX, UY, UZ, "*_at"}, "drifts": {story: {X, Y}}}},
        "modal":    {"MODAL": {"periods": [...], "ratios": [{UX, UY, RZ}]}},
        "error":    "..." (only when the project could not be imported or run)}

Where the models come from, first found wins per project name:
  1. the ``e2k`` text embedded in the stored ETABS results ``results/etabs-23/R1.etabs.json``;
  2. ``*.$et`` / ``*.e2k`` files in ``results/etabs-23/R1/``, in ``skyframe/SkyFrameVerification/R1/``
     (the pushed ETABS output folder, when it is checked out here), or in the folder named by the
     environment variable SKYFRAME_REAL_DIR.
No projects found: an empty ``projects`` and a note, never an error.
"""

from __future__ import annotations

import base64
import json
import os
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
FT = 0.3048
KIP = 4.4482216152605
KIPFT = KIP * FT


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def sources(case: dict, etabs: dict = None) -> dict:
    """{project name: (where from, text)} of every model available here."""
    out = {}
    if etabs is None:
        ef = HERE / "results" / "etabs-23" / f"{case['id']}.etabs.json"
        etabs = json.loads(ef.read_text(encoding="utf-8")) if ef.exists() else {}
    for name, p in (etabs.get("projects") or {}).items():
        if p.get("e2k"):
            try:
                out[name] = ("embedded in the ETABS results", _decode(zlib.decompress(base64.b64decode(p["e2k"]))))
            except Exception as exc:              # a damaged entry: try the files below
                out.pop(name, None)
                print(f"{name}: embedded text export unreadable ({exc})")
    dirs = [HERE / "results" / "etabs-23" / case["id"], HERE.parents[1] / "SkyFrameVerification" / case["id"]]
    if os.environ.get("SKYFRAME_REAL_DIR"):
        dirs.append(Path(os.environ["SKYFRAME_REAL_DIR"]))
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in (".$et", ".e2k") and f.stem not in out:
                out[f.stem] = (str(f), _decode(f.read_bytes()))
    return out


def _case_out(c: dict, kind: str, typ: str) -> dict:
    b = c.get("base") or {}
    base = {k: b.get(k, 0.0) / KIP for k in ("FX", "FY", "FZ")}
    base.update({k: b.get(k, 0.0) / KIPFT for k in ("MX", "MY", "MZ")})
    mn = c.get("min")
    if isinstance(mn, dict) and mn.get("base"):
        bm = mn["base"]
        base["min"] = dict({k: bm.get(k, 0.0) / KIP for k in ("FX", "FY", "FZ")},
                           **{k: bm.get(k, 0.0) / KIPFT for k in ("MX", "MY", "MZ")})
    mx = {}
    disp = c.get("node_disp") or {}
    rows = [disp] + ([mn.get("node_disp") or {}] if isinstance(mn, dict) else [])
    for k, comp in enumerate(("UX", "UY", "UZ")):
        best, at = 0.0, ""
        for r in rows:
            for t, u in r.items():
                if abs(u[k]) > best:
                    best, at = abs(u[k]), t
        mx[comp], mx[comp + "_at"] = best / FT, str(at)
    mx["joints"] = len(disp)
    drifts = {}
    for st, v in (c.get("story") or {}).items():
        drifts[st] = {"X": abs(v.get("drift_x", 0.0)), "Y": abs(v.get("drift_y", 0.0))}
    if isinstance(mn, dict):
        for st, v in (mn.get("story") or {}).items():
            cur = drifts.setdefault(st, {"X": 0.0, "Y": 0.0})
            cur["X"] = max(cur["X"], abs(v.get("drift_x", 0.0)))
            cur["Y"] = max(cur["Y"], abs(v.get("drift_y", 0.0)))
    return {"kind": kind, "type": typ, "base": base, "maxDispl": mx, "drifts": drifts}


def one_project(text: str) -> dict:
    """Import, run and export one project (kip-ft)."""
    from skyframe.engine.opensees_engine import OpenSeesEngine
    from skyframe.io.e2k_read import read_e2k

    proj = {}
    t0 = time.perf_counter()
    m, warnings = read_e2k(text)
    proj["import_s"] = time.perf_counter() - t0
    proj["warningCount"] = len(warnings)
    proj["warnings"] = list(warnings[:60])
    t0 = time.perf_counter()
    d = OpenSeesEngine(m).run().to_dict()
    proj["analysis_s"] = time.perf_counter() - t0
    nodes = d.get("nodes") or {}
    sup = set(d.get("supports") or [])
    proj["size"] = {"nodes": len(nodes), "members": len(d.get("members") or []),
                    "shells": len(d.get("shell_quads") or []), "links": len(getattr(m, "links", []) or []),
                    "equations": 6 * len([t for t in nodes if t not in sup])}
    out = {}
    for cname, c in (d.get("cases") or {}).items():
        out[cname] = _case_out(c, "case", "LinearStatic")
    for cname, c in (d.get("nonlinear_static") or {}).items():
        out[cname] = _case_out(c, "case", "NonlinearStatic")
    for cname, c in (d.get("staged") or {}).items():
        if isinstance(c, dict) and "base" in c:
            out[cname] = _case_out(c, "case", "Staged")
    for cname, c in (d.get("rs_cases") or {}).items():
        out[cname] = _case_out(c, "case", "ResponseSpectrum")
    for cname, th in (d.get("th_cases") or {}).items():
        pb = (th.get("peaks") or {}).get("base") or {}
        out[cname] = {"kind": "case", "type": "History", "maxDispl": {}, "drifts": {},
                      "base": {"FX": abs(pb.get("FX", 0.0)) / KIP, "FY": abs(pb.get("FY", 0.0)) / KIP}}
    for cname, c in (d.get("combos") or {}).items():
        out[cname] = _case_out(c, "combo", "Combination")
    proj["results"] = out
    md = d.get("modal") or {}
    proj["modal"] = {}
    if md.get("periods"):
        proj["modal"]["MODAL"] = {"periods": list(md["periods"]),
                                  "ratios": [{"UX": p.get("ux", 0.0), "UY": p.get("uy", 0.0), "RZ": p.get("rz", 0.0)}
                                             for p in md.get("participation", [])]}
    proj["status"] = {**(d.get("case_status") or {}), **(d.get("combo_status") or {})}
    return proj


def run(case: dict, only=None, etabs: dict = None) -> dict:
    """Every project found (or just the names in ``only``), each imported, run and exported."""
    from skyframe import __version__
    res = {"program": "SkyFrame", "version": __version__, "units": "kip-ft", "case": case["id"],
           "cases": {}, "values": {}, "projects": {}}
    found = sources(case, etabs)
    if only:
        found = {k: v for k, v in found.items() if k in only}
    if not found:
        res["note"] = ("no real-project models here: run R1 on the ETABS PC (RealProjects folder), push, "
                       "then python collect.py")
        return res
    for name, (where, text) in found.items():
        try:
            proj = one_project(text)
        except Exception as exc:                  # one project's failure must not stop the others
            proj = {"error": f"{type(exc).__name__}: {exc}"}
        proj["source"] = where
        res["projects"][name] = proj
    return res
