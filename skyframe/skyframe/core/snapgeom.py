"""Plan-view snap and 3-point-arc geometry: the JS-free Python mirror of
``skyframe/api/web/js/w8_snapgeom.js`` (same formulas, same tolerances).

CONTRACT "Master / similar stories, selection and snapping".  The UI uses
the JS module; this mirror pins the math with closed-form unit tests, and a
node-driven test checks that both give identical numbers.

Snap kinds and priority (lower wins inside the tolerance, then distance):
``joint`` 0 < ``inter`` 1 < ``mid`` 2 < ``perp`` 3.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

SNAP_KINDS = ("joint", "mid", "inter", "perp")
SNAP_PRIORITY = {"joint": 0, "inter": 1, "mid": 2, "perp": 3}
_EPS = 1e-12

P2 = Sequence[float]


def seg_intersect(a: P2, b: P2, c: P2, d: P2) -> Optional[dict]:
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    scale = math.hypot(rx, ry) * math.hypot(sx, sy)
    if not scale > 0 or abs(den) <= _EPS * scale:
        return None
    qx, qy = c[0] - a[0], c[1] - a[1]
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    tol = 1e-9
    if t < -tol or t > 1 + tol or u < -tol or u > 1 + tol:
        return None
    return {"x": a[0] + t * rx, "y": a[1] + t * ry, "t": t, "u": u}


def perp_foot(p: P2, a: P2, b: P2) -> Optional[dict]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if not l2 > 0:
        return None
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    if t < -1e-9 or t > 1 + 1e-9:
        return None
    return {"x": a[0] + t * dx, "y": a[1] + t * dy, "t": t}


def midpoint(a: P2, b: P2) -> dict:
    return {"x": (a[0] + b[0]) / 2, "y": (a[1] + b[1]) / 2}


def dist_to_seg(p: P2, a: P2, b: P2) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2 if l2 else 0.0
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def snap_objects(w: P2, points: List[P2], segs: List[Tuple[P2, P2]],
                 tol: float, modes: Dict[str, bool],
                 frm: Optional[P2] = None) -> Optional[dict]:
    cands = []

    def near(x, y, kind):
        d = math.hypot(x - w[0], y - w[1])
        if d <= tol:
            cands.append({"x": x, "y": y, "kind": kind, "d": d})
    if modes.get("joint"):
        for q in points:
            near(q[0], q[1], "joint")
    if modes.get("mid"):
        for a, b in segs:
            m = midpoint(a, b)
            near(m["x"], m["y"], "mid")
    if modes.get("inter"):
        close = [s for s in segs if dist_to_seg(w, s[0], s[1]) <= tol]
        for i in range(len(close)):
            for j in range(i + 1, len(close)):
                r = seg_intersect(close[i][0], close[i][1], close[j][0],
                                  close[j][1])
                if r:
                    near(r["x"], r["y"], "inter")
    if modes.get("perp") and frm is not None:
        for a, b in segs:
            f = perp_foot(frm, a, b)
            if f and math.hypot(f["x"] - frm[0], f["y"] - frm[1]) > 1e-9:
                near(f["x"], f["y"], "perp")
    if not cands:
        return None
    cands.sort(key=lambda c: (SNAP_PRIORITY[c["kind"]], c["d"]))
    return cands[0]


# ------------------------------------------------------------------ arcs
def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def circle3(p1, p2, p3) -> Optional[dict]:
    a, b = _sub(p1, p3), _sub(p2, p3)
    axb = _cross(a, b)
    n2 = _dot(axb, axb)
    if not n2 > 1e-18 * _dot(a, a) * _dot(b, b):
        return None
    t1, t2 = _cross(b, axb), _cross(axb, a)
    k1, k2 = _dot(a, a) / (2 * n2), _dot(b, b) / (2 * n2)
    c = [p3[i] + k1 * t1[i] + k2 * t2[i] for i in range(3)]
    nn = math.sqrt(n2)
    return {"center": c, "r": math.hypot(*_sub(p1, c)),
            "normal": [v / nn for v in axb]}


def arc_points3(pi, via, pj, n: int = 24) -> Optional[dict]:
    C = circle3(pi, via, pj)
    if C is None:
        return None
    c, nrm, r = C["center"], C["normal"], C["r"]
    e1 = [v / r for v in _sub(pi, c)]
    e2 = _cross(nrm, e1)

    def ang(p):
        v = _sub(p, c)
        t = math.atan2(_dot(v, e2), _dot(v, e1))
        return t + 2 * math.pi if t < 0 else t
    sweep = ang(pj) or 2 * math.pi
    pts = []
    for k in range(n + 1):
        t = sweep * k / n
        pts.append([c[i] + r * (math.cos(t) * e1[i] + math.sin(t) * e2[i])
                    for i in range(3)])
    pts[0], pts[n] = list(pi), list(pj)
    return {"points": pts, "center": c, "r": r, "sweep": sweep,
            "normal": nrm}


def via_from_radius(pi, pj, R: float, side: int = 1) -> Optional[list]:
    dx, dy = pj[0] - pi[0], pj[1] - pi[1]
    c = math.hypot(dx, dy)
    if not c > 0 or not R >= c / 2:
        return None
    s = R - math.sqrt(max(R * R - c * c / 4, 0.0))
    nx, ny = -dy / c, dx / c
    return [(pi[0] + pj[0]) / 2 + side * s * nx,
            (pi[1] + pj[1]) / 2 + side * s * ny, (pi[2] + pj[2]) / 2]
