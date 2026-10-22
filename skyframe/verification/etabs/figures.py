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


def _hull(pts):
    """Convex hull of plan points, counter-clockwise (monotone chain)."""
    pts = sorted(set((round(x, 9), round(y, 9)) for x, y in pts))
    if len(pts) < 3:
        return pts
    cross = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


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

    def along_frames(p):
        """Displacement at a point on a frame line, interpolated between the frame's end joints
        (a membrane deck has no nodes of its own: its edges move with the beams)."""
        for f in case["frames"]:
            a_, b_ = f["i"], f["j"]
            L = math.dist(a_, b_)
            if L <= 0:
                continue
            t = sum((p[k] - a_[k]) * (b_[k] - a_[k]) for k in range(3)) / L**2
            if -1e-9 <= t <= 1 + 1e-9:
                foot = [a_[k] + t * (b_[k] - a_[k]) for k in range(3)]
                if math.dist(foot, p) < 1e-6:
                    da, db = disp(a_), disp(b_)
                    return [da[k] + t * (db[k] - da[k]) for k in range(3)]
        return disp(p)

    fig = plt.figure(figsize=(8, 6), dpi=150, facecolor=PAPER)
    ax = fig.add_subplot(111, projection="3d", facecolor=PAPER)
    # rigid diaphragms: draw each floor plate (plan outline of its joints) so storeys read as floors
    for d in case.get("diaphragms", []):
        hull = _hull([(p[0], p[1]) for p in d["points"]])
        if len(hull) < 3:
            continue
        z = d["points"][0][2]
        plate = [[x, y, z] for x, y in hull]
        ax.add_collection3d(Poly3DCollection([plate], facecolor="#DDE4E6", edgecolor=MODEL, linewidth=0.4, alpha=0.25))
        ax.add_collection3d(Poly3DCollection([[[q[k] + scale * dv[k] for k in range(3)] for q, dv in
                                               ((q, disp(q)) for q in plate)]],
                                             facecolor="#BFD6E8", edgecolor=DEFL, linewidth=0.6, alpha=0.35))
    for a in case.get("areas", []):
        n1, n2 = a.get("mesh", [1, 1])
        c = a["corners"]
        if a.get("toFrame"):
            # membrane deck: its surface spans between the deflected beams (Coons patch of the edges)
            n1 = n2 = 8
            bil0 = lambda u, v: [(1 - u) * (1 - v) * c[0][k] + u * (1 - v) * c[1][k] + u * v * c[2][k]
                                 + (1 - u) * v * c[3][k] for k in range(3)]
            B = lambda u, v: along_frames(bil0(u, v))
            corner = {(0, 0): B(0, 0), (1, 0): B(1, 0), (1, 1): B(1, 1), (0, 1): B(0, 1)}

            def coons(u, v, B=B, corner=corner):
                e = [B(u, 0), B(u, 1), B(0, v), B(1, v)]
                return [(1 - v) * e[0][k] + v * e[1][k] + (1 - u) * e[2][k] + u * e[3][k]
                        - ((1 - u) * (1 - v) * corner[(0, 0)][k] + u * (1 - v) * corner[(1, 0)][k]
                           + u * v * corner[(1, 1)][k] + (1 - u) * v * corner[(0, 1)][k]) for k in range(3)]
            quads, dq = [], []
            for i in range(n1):
                for j in range(n2):
                    uv = [(i / n1, j / n2), ((i + 1) / n1, j / n2), ((i + 1) / n1, (j + 1) / n2), (i / n1, (j + 1) / n2)]
                    q = [bil0(u, v) for u, v in uv]
                    quads.append(q)
                    dq.append([[p[k] + scale * dv[k] for k in range(3)] for p, dv in zip(q, (coons(u, v) for u, v in uv))])
            ax.add_collection3d(Poly3DCollection(quads, facecolor="#DDE4E6", edgecolor="none", alpha=0.25))
            ax.add_collection3d(Poly3DCollection(dq, facecolor="#BFD6E8", edgecolor=DEFL, linewidth=0.2, alpha=0.5))
            continue
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
    sup = [s for s in case["supports"] if any(s["restraint"][:3])]      # real supports, not rotation-only restraints
    sup += [{"at": s["at"]} for s in case.get("springs", [])]
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
