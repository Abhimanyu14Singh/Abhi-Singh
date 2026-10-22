"""Pick up ETABS results pushed from the ETABS machine, wherever they landed.

    python collect.py            # fetch, find new results, store them, compare

Looks at every branch of the repository on GitHub for files named
``<case>.etabs.json`` that ETABS wrote (``"program": "ETABS"``), in any folder,
takes the newest copy of each case, and stores it as the reference
``results/etabs-23/<case>.etabs.json`` (plus the run's ``.log.txt`` when it
sits beside it).  Then it prints the three-way comparison for every case
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
        text = json.dumps(d, indent=1)
        if out.exists() and out.read_text() == text:
            continue
        out.write_text(text, encoding="utf-8")
        log = path[: -len(".etabs.json")] + ".log.txt"
        try:
            (DEST / f"{cid}.log.txt").write_text(git("show", f"{ref}:{log}"), encoding="utf-8")
        except subprocess.CalledProcessError:
            pass
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
