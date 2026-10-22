"""Three-way comparison: hand calculation, ETABS, SkyFrame.

    python compare.py F1                     # SkyFrame run here + stored ETABS results (if any)

Reads ``cases/<id>.json`` (or builds it from cases.py), runs SkyFrame,
loads ``results/etabs-23/<id>.etabs.json`` when present, and prints one
row per check.  Status per value, against the tolerance for its kind:
exact (inside it), acceptable (inside twice it), fails.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                     # the skyframe project (import skyframe from it)
ETABS_DIR = HERE / "results" / "etabs-23"

# relative tolerances (the doc "Acceptance tolerances"): vs hand, vs ETABS
TOL = {"joint": (1e-3, 5e-3), "base": (1e-3, 1e-3), "frame": (1e-3, 5e-3), "modal": (1e-3, 5e-3),
       "drift": (1e-3, 5e-3), "pier": (1e-3, 5e-3), "buckling": (1e-3, 5e-3),
       "value": (1e-3, 5e-3)}


def pick(res: dict, chk: dict) -> Optional[float]:
    q = chk["quantity"]
    if q == "modal":
        md = res.get("modal") or {}
        k = chk["mode"] - 1
        if chk["comp"] == "T":
            return md["periods"][k] if k < len(md.get("periods", [])) else None
        return md["ratios"][k][chk["comp"]] if k < len(md.get("ratios", [])) else None
    if q == "buckling":
        f = (res.get("buckling") or {}).get(chk["case"]) or []
        return f[chk["mode"] - 1] if chk["mode"] <= len(f) else None
    if q == "value":                       # a named scalar an extension exports: res["values"][key]
        return (res.get("values") or {}).get(chk["key"])
    c = res["cases"].get(chk["case"])
    if c is None:
        return None
    if q == "pier":
        rows = [r for r in c.get("piers", []) if r["story"] == chk["story"] and r["name"] == chk["name"]
                and r["location"].lower() == chk["location"]]
        return rows[0][chk["comp"]] if rows else None
    if q == "drift":
        rows = [r for r in c.get("drifts", []) if r["story"] == chk["story"] and r["dir"] == chk["comp"]]
        return max((abs(r["drift"]) for r in rows), default=None)
    if q == "base":
        return c["base"][chk["comp"]]
    if q == "joint":
        k = ["UX", "UY", "UZ", "RX", "RY", "RZ"].index(chk["comp"])
        best = min(c["joints"], key=lambda j: math.dist(j["xyz"], chk["at"]))
        if math.dist(best["xyz"], chk["at"]) > 1e-3:
            return None
        return best["u"][k]
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


def status(got: Optional[float], want: float, rel: float, scale: float) -> str:
    if got is None:
        return "missing"
    err = abs(got - want)
    lim = rel * max(abs(want), scale)
    if err <= lim:
        return "exact"
    if err <= 2 * lim:
        return "acceptable"
    return "fails"


def compare(case: dict, sky: dict, etabs: Optional[dict]) -> list:
    rows = []
    for chk in case["checks"]:
        norm = abs if chk["how"] == "abs" else (lambda v: v)
        s = pick(sky, chk)
        in_etabs = chk.get("etabs", True)          # False: ETABS does not report this value
        e = pick(etabs, chk) if (etabs and in_etabs) else None
        s = None if s is None else norm(s)
        e = None if e is None else norm(e)
        h = chk["hand"]
        t_hand, t_etabs = chk.get("tol", TOL[chk["quantity"]])
        # scale: the size a zero (or tiny) answer is judged against, e.g. the load that would be there
        sc = chk.get("scale", abs(h) if h is not None else 0.0)
        vs_hand = (lambda v: "no hand value" if h is None else status(v, h, t_hand, max(abs(h), sc)))
        known = chk.get("knownDifference")             # an ETABS modelling rule that differs, documented
        if known:
            vs_hand_raw = vs_hand
            vs_hand = (lambda v, f=vs_hand_raw: "known difference" if v is e and f(v) == "fails" else f(v))
        rows.append({
            "id": chk["id"], "label": chk["label"], "formula": chk["formula"], "hand": h,
            "skyframe": s, "etabs": e,
            "sky_vs_hand": vs_hand(s),
            "etabs_vs_hand": (vs_hand(e) if in_etabs else "not reported") if etabs else "not run",
            "sky_vs_etabs": ((status(s, e, t_etabs, max(abs(e), sc)) if e is not None else "missing")
                             if in_etabs else "not reported") if etabs else "not run",
        })
        if known and rows[-1]["sky_vs_etabs"] == "fails":
            rows[-1]["sky_vs_etabs"] = "known difference"
    return rows


def main(argv=None) -> int:
    sys.path.insert(0, str(HERE))
    sys.path.insert(1, str(ROOT))
    import cases as C
    import skyframe_side as S

    cid = (argv or sys.argv[1:] or ["F1"])[0]
    case = C.get(cid)
    sky = S.run(case)
    ef = ETABS_DIR / f"{cid}.etabs.json"
    etabs = json.loads(ef.read_text()) if ef.exists() else None
    rows = compare(case, sky, etabs)
    fmt = lambda v: "-" if v is None else f"{v:.6g}"
    print(f"{cid}: {case['title']}   (ETABS results: {'found' if etabs else 'not yet'})")
    print(f"{'check':8} {'hand':>12} {'SkyFrame':>12} {'ETABS':>12}  sky/hand    etabs/hand  sky/etabs")
    for r in rows:
        print(f"{r['id']:8} {fmt(r['hand']):>12} {fmt(r['skyframe']):>12} {fmt(r['etabs']):>12}  "
              f"{r['sky_vs_hand']:11} {r['etabs_vs_hand']:11} {r['sky_vs_etabs']}")
    bad = [r for r in rows if "fails" in (r["sky_vs_hand"], r["etabs_vs_hand"], r["sky_vs_etabs"])
           or r["sky_vs_hand"] == "missing"]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
