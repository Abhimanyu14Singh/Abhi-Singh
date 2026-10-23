"""Pick up ETABS results pushed from the ETABS machine, wherever they landed.

    python collect.py            # fetch, find new results, store them, compare

Looks at every branch of the repository on GitHub for files named
``<case>.etabs.json`` that ETABS wrote (``"program": "ETABS"``), in any folder,
takes the newest copy of each case, and stores it as the reference
``results/etabs-23/<case>.etabs.json`` (plus the run's ``.log.txt`` when it
sits beside it).  Results from a run of an older version of a model (its
``.case.json`` differs from the current definition) are skipped, and removed
from the store.  Then it prints the three-way comparison for every case
that changed.  Exit 0 = nothing new or all compared; 2 = new results with a
failing value.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DEST = HERE / "results" / "etabs-23"


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def found() -> dict:
    """case id -> (commit time, ref, path) of the newest ETABS result for it."""
    git("fetch", "--quiet", "origin")
    best: dict = {}
    refs = [r for r in git("for-each-ref", "--format=%(refname)", "refs/remotes/origin").split() if not r.endswith("/HEAD")]
    for ref in refs:
        for path in git("ls-tree", "-r", "--name-only", ref).splitlines():
            if not path.endswith(".etabs.json") or "/results/etabs-23/" in f"/{path}":
                continue
            try:
                d = json.loads(git("show", f"{ref}:{path}"))
            except (json.JSONDecodeError, subprocess.CalledProcessError):
                continue
            if d.get("program") != "ETABS" or not d.get("case"):
                continue
            when = int(git("log", "-1", "--format=%ct", ref, "--", path).strip() or 0)
            if d["case"] not in best or when > best[d["case"]][0]:
                best[d["case"]] = (when, ref, path, d)
    return best


_TEXT_ONLY = ("title", "purpose", "settings", "checks", "notes")
# results made by run_etabs.py before this runner version are wrong for the case (see run_etabs.RERUN_BEFORE)
RERUN_BEFORE = {"L1": 2, "L1S": 2, "L2": 2, "H3": 3, "K4": 4, "N4": 5, "H2": 5, "C1": 7, "C2": 7, "C3": 7}


def model_part(case: dict) -> dict:
    """What ETABS built: the definition without its descriptions and checks."""
    return {k: v for k, v in json.loads(json.dumps(case)).items() if k not in _TEXT_ONLY}


def stale(ref: str, path: str, case: dict) -> bool:
    """True when the ETABS run used a different model from the current definition (its .case.json)."""
    try:
        ran = json.loads(git("show", f"{ref}:{path[: -len('.etabs.json')] + '.case.json'}"))
    except (json.JSONDecodeError, subprocess.CalledProcessError):
        return False                             # no record of the model: take the results
    return model_part(ran) != model_part(case)


def main() -> int:
    sys.path.insert(0, str(HERE))
    sys.path.insert(1, str(HERE.parents[1]))
    import cases as C
    import compare as CMP
    import skyframe_side as S

    DEST.mkdir(parents=True, exist_ok=True)
    new = []
    for cid, (when, ref, path, d) in sorted(found().items()):
        out = DEST / f"{cid}.etabs.json"
        if cid in C.CASES and (stale(ref, path, C.get(cid))
                               or d.get("runner", 1) < RERUN_BEFORE.get(cid, 0)):
            if out.exists():                     # stored from the old model: drop it until the rerun
                out.unlink()
                (DEST / f"{cid}.log.txt").unlink(missing_ok=True)
                print(f"{cid}: stored ETABS results removed, the model (or the runner's build of it) has changed "
                      "since that run")
            continue
        text = json.dumps(d, indent=1)
        stem = path[: -len(".etabs.json")]
        extras = 0
        log = subprocess.run(["git", "-C", str(REPO), "show", f"{ref}:{stem}.log.txt"], capture_output=True)
        clean_shot = b"(ETABS 3-D view only)" in log.stdout   # older runs grabbed whole screens: never kept
        for ext in (".log.txt", ".etabs.png", ".skyframe-speed.json", ".skyframe-speed.log.txt"):
            if ext == ".etabs.png" and not clean_shot:
                continue
            r = subprocess.run(["git", "-C", str(REPO), "show", f"{ref}:{stem}{ext}"], capture_output=True)
            if r.returncode == 0:                # the screenshot, SkyFrame's timing on the same PC, logs
                dest = DEST / f"{cid}{ext}"
                if not dest.exists() or dest.read_bytes() != r.stdout:
                    dest.write_bytes(r.stdout)
                    extras += ext != ".log.txt"
        if out.exists():
            have = json.loads(out.read_text())
            # the same run, already stored (possibly with the reversed-frame list added afterwards)
            if {k: v for k, v in have.items() if k != "reversed"} == {k: v for k, v in d.items() if k != "reversed"}:
                if extras:
                    print(f"new screenshot or timing for {cid}")
                continue
        out.write_text(text, encoding="utf-8")

        new.append(cid)
        print(f"new ETABS results for {cid}: {ref.split('/', 3)[-1]}:{path}")
    if not new:
        print("no new ETABS results on GitHub")
        return 0
    failing = False
    for cid in new:
        if cid not in C.CASES:
            print(f"{cid}: results stored, but there is no definition for it here")
            continue
        case = C.get(cid)
        if not case["checks"]:                   # R1 (real projects): nothing to check here, and a slow run
            print(f"\n{cid}: {case['title']}: results stored"
                  + ("; compare them with python compare_real.py" if case.get("ext") == "real" else ""))
            continue
        rows = CMP.compare(case, S.run(case), json.loads((DEST / f"{cid}.etabs.json").read_text()))
        print(f"\n{cid}: {case['title']}")
        for r in rows:
            fmt = lambda v: "-" if v is None else f"{v:.6g}"
            print(f"  {r['id']:8} hand {fmt(r['hand']):>12}  SkyFrame {fmt(r['skyframe']):>12}  ETABS {fmt(r['etabs']):>12}"
                  f"  sky/etabs {r['sky_vs_etabs']}  etabs/hand {r['etabs_vs_hand']}")
            failing |= r["sky_vs_etabs"] == "fails"
    return 2 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
