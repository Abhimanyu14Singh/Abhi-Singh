"""Draw each verification model as SkyFrame solved it: the model in grey, its deflected shape in
blue, supports as triangles.  One PNG per case, for the slides beside the ETABS screenshot.

    python figures.py            # every case -> figures/<id>.skyframe.png
    python figures.py W2 B1      # some cases
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parents[1]))
OUT = HERE / "figures"

INK, MODEL, DEFL, SUP, PAPER = "#16232B", "#9AA8AD", "#2F6F9F", "#E0904A", "#FFFFFF"


def _first_case(case: dict, res: dict) -> str:
    order = [c["case"] for c in case["checks"] if c.get("case") in res["cases"]]
    for name in order:
        if res["cases"][name]["joints"]:
            return name
    return next(iter(res["cases"]), None)


def draw(cid: str) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    import cases as C
    import skyframe_side as S

    case = C.get(cid)
    res = S.run(case)
    lc = _first_case(case, res)
    joints = res["cases"][lc]["joints"] if lc else []

    def disp(p):
        best = min(joints, key=lambda j: math.dist(j["xyz"], p)) if joints else None
        return best["u"][:3] if best and math.dist(best["xyz"], p) < 1e-3 else [0.0, 0.0, 0.0]

    pts = [f["i"] for f in case["frames"]] + [f["j"] for f in case["frames"]]
    for a in case.get("areas", []):
        pts += a["corners"]
    span = max(max(p[k] for p in pts) - min(p[k] for p in pts) for k in range(3)) or 1.0
    umax = max((math.sqrt(sum(v * v for v in j["u"][:3])) for j in joints), default=0.0)
    scale = 0.08 * span / umax if umax > 0 else 0.0

    fig = plt.figure(figsize=(8, 6), dpi=150, facecolor=PAPER)
    ax = fig.add_subplot(111, projection="3d", facecolor=PAPER)
    for a in case.get("areas", []):
        n1, n2 = a.get("mesh", [1, 1])
        c = a["corners"]
        bil = lambda u, v: [(1 - u) * (1 - v) * c[0][k] + u * (1 - v) * c[1][k] + u * v * c[2][k]
                            + (1 - u) * v * c[3][k] for k in range(3)]
        quads, dq = [], []
        for i in range(n1):
            for j in range(n2):
                q = [bil(i / n1, j / n2), bil((i + 1) / n1, j / n2), bil((i + 1) / n1, (j + 1) / n2),
                     bil(i / n1, (j + 1) / n2)]
                quads.append(q)
                dq.append([[p[k] + scale * d[k] for k in range(3)] for p, d in ((p, disp(p)) for p in q)])
        ax.add_collection3d(Poly3DCollection(quads, facecolor="#DDE4E6", edgecolor=MODEL, linewidth=0.2, alpha=0.35))
        ax.add_collection3d(Poly3DCollection(dq, facecolor="#BFD6E8", edgecolor=DEFL, linewidth=0.3, alpha=0.55))
    for f in case["frames"]:
        xs, ys, zs = zip(f["i"], f["j"])
        ax.plot(xs, ys, zs, color=MODEL, linewidth=1.0)
        di, dj = disp(f["i"]), disp(f["j"])
        p, q = [f["i"][k] + scale * di[k] for k in range(3)], [f["j"][k] + scale * dj[k] for k in range(3)]
        ax.plot([p[0], q[0]], [p[1], q[1]], [p[2], q[2]], color=DEFL, linewidth=1.8)
    sup = case["supports"] + [{"at": s["at"]} for s in case.get("springs", [])]
    if sup and len(sup) < 400:
        ax.scatter([s["at"][0] for s in sup], [s["at"][1] for s in sup], [s["at"][2] for s in sup],
                   marker="^", color=SUP, s=18, depthshade=False)
    lo = [min(p[k] for p in pts) for k in range(3)]
    hi = [max(p[k] for p in pts) for k in range(3)]
    mid = [(a + b) / 2 for a, b in zip(lo, hi)]
    half = 0.42 * span
    ax.set_xlim(mid[0] - half, mid[0] + half)
    ax.set_ylim(mid[1] - half, mid[1] + half)
    ax.set_zlim(max(lo[2] - 0.1 * span, mid[2] - half), mid[2] + half)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=22, azim=-58)
    ax.set_axis_off()
    fig.text(0.04, 0.95, f"SkyFrame · {cid}", color=INK, fontsize=15, fontweight="bold", va="top")
    fig.text(0.04, 0.90, f"{case['title']} · case {lc}, deflection x{scale:.3g}" if scale else case["title"],
             color="#4A5A62", fontsize=10, va="top")
    OUT.mkdir(exist_ok=True)
    path = OUT / f"{cid}.skyframe.png"
    fig.subplots_adjust(0, 0, 1, 1)
    fig.savefig(path, facecolor=PAPER)
    plt.close(fig)
    return path


def main(argv=None) -> int:
    import cases as C
    ids = (argv if argv is not None else sys.argv[1:]) or list(C.CASES)
    for cid in ids:
        print(draw(cid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
