"""Real projects (case R1): SkyFrame against ETABS, project by project.  No hand calculation.

    python compare_real.py                       # every project in results/etabs-23/R1.etabs.json
    python compare_real.py Tower_A               # one project (its EDB name without .EDB)
    python compare_real.py --stories             # also every story's drift
    python compare_real.py --save sky.json       # keep SkyFrame's results ...
    python compare_real.py --sky sky.json        # ... and compare them again without rerunning

ETABS results come from ``results/etabs-23/R1.etabs.json`` (``--etabs`` for another file; ``python
collect.py`` stores it there after the ETABS PC has pushed R1).  SkyFrame imports each project's text
export (embedded in that file, see ``sf_ext_real.py``), runs it here and exports the same quantities.

Per project it prints, with the relative difference (SkyFrame - ETABS) / |ETABS|:
  base reactions FX FY FZ per load case and combination (kip),
  modal periods (s) and participating mass ratios UX UY,
  the largest story drift ratio per case (and per story with --stories),
  the largest joint displacement |UX| |UY| |UZ| per case (ft).
Values smaller than 1e-6 of the largest of their kind in the project are shown, not compared.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parents[1]))
ETABS_FILE = HERE / "results" / "etabs-23" / "R1.etabs.json"


def rel(sky, etabs, scale: float) -> str:
    if sky is None or etabs is None:
        return "-"
    if abs(etabs) <= 1e-6 * max(scale, 1e-30):
        return "~0" if abs(sky) <= 1e-6 * max(scale, 1e-30) else "-"
    return f"{100 * (sky - etabs) / abs(etabs):+.2f}%"


def fmt(v) -> str:
    return "-" if v is None else f"{v:.5g}"


def r3(v) -> str:
    return "-" if v is None else f"{v:.3f}"


def _max_drift(drifts: dict, comp: str):
    best = None
    for st, v in (drifts or {}).items():
        if comp in v and (best is None or v[comp] > best[1]):
            best = (st, v[comp])
    return best


def report(name: str, e: dict, s: dict, stories: bool = False) -> list:
    """Lines comparing one project."""
    L = [f"\n== {name}"]
    if e.get("error"):
        return L + [f"   ETABS stopped on it: {e['error']}"]
    if s is None:
        return L + ["   no SkyFrame result (no text export for it here)"]
    if s.get("error"):
        return L + [f"   SkyFrame could not import or run it: {s['error']}"]
    es, ss = e.get("size") or {}, s.get("size") or {}
    L.append(f"   ETABS: {es.get('points')} points, {es.get('frames')} frames, {es.get('areas')} areas "
             f"({es.get('area_elements')} area elements), {es.get('links')} links; analysis "
             f"{fmt(e.get('analysis_s'))} s (cases run: {e.get('runCases', '')})")
    L.append(f"   SkyFrame: {ss.get('nodes')} nodes, {ss.get('members')} members, {ss.get('shells')} shells, "
             f"~{ss.get('equations')} equations; import {fmt(s.get('import_s'))} s, analysis {fmt(s.get('analysis_s'))} s")
    if s.get("warningCount"):
        w = s.get("warnings", [])
        w = [x for x in w if "not supported; ignored" not in x] + [x for x in w if "not supported; ignored" in x]
        L.append(f"   importer: {s['warningCount']} warnings, e.g. " + "; ".join(w[:3]))
    if e.get("errors"):
        L.append(f"   ETABS: {len(e['errors'])} quantities not read, e.g. {e['errors'][0]}")
    er, sr = e.get("results") or {}, s.get("results") or {}
    names = [n for n in er if n in sr]
    missing = [n for n in er if n not in sr]
    if missing:
        L.append(f"   not in SkyFrame ({len(missing)}): {', '.join(missing[:15])}{' ...' if len(missing) > 15 else ''}")

    def scale(key, comps):
        return max([abs(r.get(key, {}).get(c, 0.0) or 0.0) for r in er.values() for c in comps] + [0.0])

    sb = scale("base", ("FX", "FY", "FZ"))
    L.append("\n   Base reactions (kip)")
    L.append(f"   {'case / combination':28} {'':3} {'ETABS':>12} {'SkyFrame':>12} {'diff':>9}")
    for n in names:
        for c in ("FX", "FY", "FZ"):
            ev, sv = er[n]["base"].get(c), sr[n]["base"].get(c)
            if ev is None and sv is None:
                continue
            L.append(f"   {n[:28]:28} {c:3} {fmt(ev):>12} {fmt(sv):>12} {rel(sv, ev, sb):>9}")

    em, sm_ = e.get("modal") or {}, s.get("modal") or {}
    if em:
        ename = next(iter(em))                     # ETABS's first modal case that ran (usually "Modal")
        emd, smd = em[ename], next(iter(sm_.values()), {})
        ep, sp = emd.get("periods", []), smd.get("periods", [])
        L.append(f"\n   Modal ({ename}): period (s), mass ratios UX UY")
        L.append(f"   {'mode':>4} {'T ETABS':>9} {'T SkyFrame':>10} {'diff':>9}   {'UX E':>6} {'UX S':>6}  {'UY E':>6} {'UY S':>6}")
        for k in range(min(max(len(ep), len(sp)), 12)):
            et, st = (ep[k] if k < len(ep) else None), (sp[k] if k < len(sp) else None)
            erat = emd.get("ratios", [])[k] if k < len(emd.get("ratios", [])) else {}
            srat = smd.get("ratios", [])[k] if k < len(smd.get("ratios", [])) else {}
            L.append(f"   {k + 1:4} {fmt(et):>9} {fmt(st):>10} {rel(st, et, max(ep or [1.0])):>9}   "
                     f"{r3(erat.get('UX')):>6} {r3(srat.get('UX')):>6}  {r3(erat.get('UY')):>6} {r3(srat.get('UY')):>6}")
        if not sp:
            L.append("   SkyFrame found no modes (no mass on free joints after import?)")

    sd = max([v for r in er.values() for d in (r.get("drifts") or {}).values() for v in d.values()] + [0.0])
    rows = []
    for n in names:
        for c in ("X", "Y"):
            eb, sb_ = _max_drift(er[n].get("drifts"), c), _max_drift(sr[n].get("drifts"), c)
            if eb and eb[1] > 1e-6 * max(sd, 1e-30):
                rows.append(f"   {n[:28]:28} {c:3} {fmt(eb[1]):>11} {eb[0][:10]:>10} {fmt(sb_ and sb_[1]):>11} "
                            f"{(sb_[0][:10] if sb_ else '-'):>10} {rel(sb_ and sb_[1], eb[1], sd):>9}")
    if rows:
        L.append("\n   Largest story drift ratio per case")
        L.append(f"   {'case / combination':28} {'dir':3} {'ETABS':>11} {'story':>10} {'SkyFrame':>11} {'story':>10} {'diff':>9}")
        L += rows
    if stories:
        for n in names:
            ed, sdr = er[n].get("drifts") or {}, sr[n].get("drifts") or {}
            if not any(v > 1e-6 * max(sd, 1e-30) for d in ed.values() for v in d.values()):
                continue
            L.append(f"\n   Story drifts, {n}")
            L.append(f"   {'story':16} {'X ETABS':>10} {'X SkyFrame':>10} {'diff':>9}  {'Y ETABS':>10} {'Y SkyFrame':>10} {'diff':>9}")
            for st, v in ed.items():
                w = sdr.get(st, {})
                L.append(f"   {st[:16]:16} {fmt(v.get('X')):>10} {fmt(w.get('X')):>10} {rel(w.get('X'), v.get('X'), sd):>9}  "
                         f"{fmt(v.get('Y')):>10} {fmt(w.get('Y')):>10} {rel(w.get('Y'), v.get('Y'), sd):>9}")

    su = scale("maxDispl", ("UX", "UY", "UZ"))
    L.append("\n   Largest joint displacement (ft)")
    L.append(f"   {'case / combination':28} {'':3} {'ETABS':>12} {'SkyFrame':>12} {'diff':>9}")
    for n in names:
        for c in ("UX", "UY", "UZ"):
            ev, sv = (er[n].get("maxDispl") or {}).get(c), (sr[n].get("maxDispl") or {}).get(c)
            if ev is None or ev <= 1e-6 * max(su, 1e-30):
                continue
            L.append(f"   {n[:28]:28} {c:3} {fmt(ev):>12} {fmt(sv):>12} {rel(sv, ev, su):>9}")
    return L


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("projects", nargs="*", help="project names (EDB names without .EDB); default all")
    ap.add_argument("--etabs", default=str(ETABS_FILE), help="the ETABS results of R1")
    ap.add_argument("--sky", help="SkyFrame results saved earlier with --save (no rerun)")
    ap.add_argument("--save", help="write SkyFrame's results to this JSON file")
    ap.add_argument("--stories", action="store_true", help="print every story's drift too")
    a = ap.parse_args(argv)

    import cases as C
    case = C.get("R1")
    ef = Path(a.etabs)
    if not ef.exists():
        print(f"No ETABS results for R1 at {ef}: run R1 on the ETABS PC (models in its RealProjects folder), "
              "push, then python collect.py")
        return 0
    etabs = json.loads(ef.read_text(encoding="utf-8"))
    projects = etabs.get("projects") or {}
    if not projects:
        print(f"R1: ETABS found no projects ({etabs.get('note', 'empty RealProjects folder')})")
        return 0
    only = set(a.projects) or None
    if a.sky:
        sky = json.loads(Path(a.sky).read_text(encoding="utf-8"))
    else:
        import sf_ext_real
        print("Running SkyFrame on " + ", ".join(sorted(only or projects)) + " ...", flush=True)
        sky = sf_ext_real.run(case, only=only, etabs=etabs)
    if a.save:
        Path(a.save).write_text(json.dumps(sky, indent=1), encoding="utf-8")
        print(f"SkyFrame results saved to {a.save}")
    print(f"R1: real projects, ETABS {etabs.get('version', '?')} against SkyFrame {sky.get('version', '?')}"
          " (diff = (SkyFrame - ETABS) / |ETABS|)")
    for name, e in projects.items():
        if only and name not in only:
            continue
        for line in report(name, e, (sky.get("projects") or {}).get(name), a.stories):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
