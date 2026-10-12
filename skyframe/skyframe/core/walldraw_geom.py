"""Draw Walls (plan) geometry: the JS-free Python mirror of
``skyframe/api/web/js/walldraw_geom.js`` (same formulas, tolerances and
rounding).

CONTRACT "Draw Walls (plan view)".  The browser draws walls with the JS
module; this mirror pins the math with closed-form unit tests and a
node-driven test checks that both give identical numbers.  Nothing here is
used by the engine: the walls the tool creates are ordinary
``ShellRegion(kind="wall")`` objects (four corners, bottom edge first).

Conventions (plan, metres, degrees CCW from +X):

* a drawn line runs a -> b; its LEFT normal is (-dy, dx) / L;
* alignment ``left`` puts the wall body left of the drawn line (centre line
  offset +t/2), ``right`` -t/2, ``centre`` 0; an extra signed offset adds
  (+ = left);
* offset polylines are mitred (neighbouring offset lines intersected;
  parallel / reversed neighbours fall back to the perpendicular offset);
* wall corners: ``[p1 bottom, p2 bottom, p2 top, p1 top]``.
"""

from __future__ import annotations

import math
import re
from typing import Dict, Iterable, List, Optional, Sequence

TOL = 1e-6
P2 = Sequence[float]


def r9(v: float) -> float:
    """Round to 1e-9 the way JS ``Math.round(v * 1e9) / 1e9`` does."""
    r = math.floor(v * 1e9 + 0.5) / 1e9
    return 0.0 if r == 0 else r


def _p(x: float, y: float) -> List[float]:
    return [r9(x), r9(y)]


def _same(a: P2, b: P2, tol: float = TOL) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def align_offset(align: str, thickness: float, extra: float = 0.0) -> float:
    h = float(thickness or 0.0) / 2.0
    base = h if align == "left" else (-h if align == "right" else 0.0)
    return base + float(extra or 0.0)


def clean_polyline(pts: Iterable[P2], closed: bool = False) -> List[List[float]]:
    out: List[List[float]] = []
    for p in pts or []:
        if not out or not _same(out[-1], p):
            out.append([float(p[0]), float(p[1])])
    if closed and len(out) > 1 and _same(out[0], out[-1]):
        out.pop()
    return out


def seg_info(a: P2, b: P2) -> Dict[str, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    ang = math.atan2(dy, dx) * 180.0 / math.pi
    if ang < 0:
        ang += 360.0
    if ang >= 360.0 - 1e-9:
        ang = 0.0
    return {"len": math.hypot(dx, dy), "ang": ang}


def offset_polyline(pts: Iterable[P2], s: float,
                    closed: bool = False) -> List[List[float]]:
    q = clean_polyline(pts, closed)
    n = len(q)
    if n < 2 or abs(s) < 1e-12:
        return [_p(p[0], p[1]) for p in q]
    nseg = n if closed else n - 1
    segs = []
    for i in range(nseg):
        a, b = q[i], q[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        ux, uy = dx / L, dy / L
        segs.append(((a[0] - uy * s, a[1] + ux * s), (ux, uy)))

    def meet(s1, s2, vtx):
        (a1, u1), (a2, u2) = s1, s2
        cr = u1[0] * u2[1] - u1[1] * u2[0]
        if abs(cr) < 1e-9:
            return (vtx[0] - u2[1] * s, vtx[1] + u2[0] * s)
        dx, dy = a2[0] - a1[0], a2[1] - a1[1]
        t = (dx * u2[1] - dy * u2[0]) / cr
        return (a1[0] + u1[0] * t, a1[1] + u1[1] * t)

    out = []
    for k in range(n):
        if not closed and k == 0:
            p = segs[0][0]
        elif not closed and k == n - 1:
            u = segs[n - 2][1]
            p = (q[k][0] - u[1] * s, q[k][1] + u[0] * s)
        else:
            p = meet(segs[(k - 1 + nseg) % nseg], segs[k % nseg], q[k])
        out.append(_p(p[0], p[1]))
    return out


def split_at_lines(a: P2, b: P2, lines, tol: float = TOL) -> List[List[float]]:
    rx, ry = b[0] - a[0], b[1] - a[1]
    L = math.hypot(rx, ry)
    if not L > tol:
        return [_p(a[0], a[1]), _p(b[0], b[1])]
    ts = []
    for c, d in lines or []:
        sx, sy = d[0] - c[0], d[1] - c[1]
        M = math.hypot(sx, sy)
        if not M > tol:
            continue
        den = rx * sy - ry * sx
        if abs(den) <= 1e-9 * L * M:
            continue
        qx, qy = c[0] - a[0], c[1] - a[1]
        t = (qx * sy - qy * sx) / den
        u = (qx * ry - qy * rx) / den
        if t * L <= tol or (1 - t) * L <= tol:
            continue
        if u * M < -tol or (u - 1) * M > tol:
            continue
        ts.append(t)
    ts.sort()
    out = [_p(a[0], a[1])]
    last = 0.0
    for t in ts:
        if (t - last) * L <= tol:
            continue
        out.append(_p(a[0] + rx * t, a[1] + ry * t))
        last = t
    out.append(_p(b[0], b[1]))
    return out


def wall_panels(pts: Iterable[P2], closed: bool = False, offset: float = 0.0,
                lines=None) -> List[List[List[float]]]:
    q = clean_polyline(pts, closed)
    if len(q) < 2 or (closed and len(q) < 3):
        return []
    o = offset_polyline(q, float(offset or 0.0), closed)
    nseg = len(o) if closed else len(o) - 1
    out = []
    for i in range(nseg):
        a, b = o[i], o[(i + 1) % len(o)]
        parts = split_at_lines(a, b, lines) if lines else [a, b]
        for k in range(len(parts) - 1):
            if math.hypot(parts[k + 1][0] - parts[k][0],
                          parts[k + 1][1] - parts[k][1]) > TOL:
                out.append([parts[k], parts[k + 1]])
    return out


def wall_corners(p1: P2, p2: P2, zb: float, zt: float, bot_off: float = 0.0,
                 top_off: float = 0.0) -> Optional[List[List[float]]]:
    z0 = r9(zb + float(bot_off or 0.0))
    z1 = r9(zt + float(top_off or 0.0))
    if not z1 - z0 > TOL:
        return None
    if not math.hypot(p2[0] - p1[0], p2[1] - p1[1]) > TOL:
        return None
    return [[p1[0], p1[1], z0], [p2[0], p2[1], z0],
            [p2[0], p2[1], z1], [p1[0], p1[1], z1]]


def ortho_point(frm: P2, p: P2) -> List[float]:
    dx, dy = p[0] - frm[0], p[1] - frm[1]
    return [p[0], frm[1]] if abs(dx) >= abs(dy) else [frm[0], p[1]]


_NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$", re.I)


def _num(v: str) -> float:
    return float(v) if _NUM.match(v) else math.nan


def parse_typed(text, frm: Optional[P2], toward: Optional[P2] = None,
                f: float = 1.0) -> Optional[List[float]]:
    s = re.sub(r"\s+", "", str("" if text is None else text).strip())
    if not s or frm is None:
        return None
    if s[0] == "@":
        parts = s[1:].split(",")
        if len(parts) != 2:
            return None
        dx, dy = _num(parts[0]), _num(parts[1])
        if not (math.isfinite(dx) and math.isfinite(dy)):
            return None
        x, y = frm[0] + dx * f, frm[1] + dy * f
    elif "<" in s:
        parts = s.split("<")
        if len(parts) != 2:
            return None
        L, A = _num(parts[0]), _num(parts[1])
        if not (math.isfinite(L) and math.isfinite(A)) or L <= 0:
            return None
        a = A * math.pi / 180.0
        x, y = frm[0] + L * f * math.cos(a), frm[1] + L * f * math.sin(a)
    elif "," in s:
        parts = s.split(",")
        if len(parts) != 2:
            return None
        ax, ay = _num(parts[0]), _num(parts[1])
        if not (math.isfinite(ax) and math.isfinite(ay)):
            return None
        x, y = ax * f, ay * f
    else:
        L = _num(s)
        if not math.isfinite(L) or L <= 0:
            return None
        ux, uy = 1.0, 0.0
        if toward is not None:
            dx, dy = toward[0] - frm[0], toward[1] - frm[1]
            d = math.hypot(dx, dy)
            if d > 1e-12:
                ux, uy = dx / d, dy / d
        x, y = frm[0] + L * f * ux, frm[1] + L * f * uy
    if math.hypot(x - frm[0], y - frm[1]) <= TOL:
        return None
    return _p(x, y)


def dist_to_seg(p: P2, a: P2, b: P2) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2 if l2 else 0.0
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def nearest_seg(segs, p: P2, tol: float) -> int:
    best, bd = -1, math.inf
    for i, s in enumerate(segs or []):
        d = dist_to_seg(p, s[0], s[1])
        if d <= tol and (best < 0 or d < bd - 1e-12):
            best, bd = i, d
    return best


def segs_in_box(segs, box):
    xa = min(box[0][0], box[1][0]) - TOL
    xb = max(box[0][0], box[1][0]) + TOL
    ya = min(box[0][1], box[1][1]) - TOL
    yb = max(box[0][1], box[1][1]) + TOL

    def inb(p):
        return xa <= p[0] <= xb and ya <= p[1] <= yb
    return [s for s in segs or [] if inb(s[0]) and inb(s[1])]


def grid_segments(xs, ys, T=None):
    tf = T or (lambda x, y: (x, y))
    X = sorted(float(v) for v in xs or [])
    Y = sorted(float(v) for v in ys or [])
    out = []

    def add(a, b):
        p, q = tf(a[0], a[1]), tf(b[0], b[1])
        out.append([_p(p[0], p[1]), _p(q[0], q[1])])
    for x in X:
        for j in range(len(Y) - 1):
            add((x, Y[j]), (x, Y[j + 1]))
    for y in Y:
        for i in range(len(X) - 1):
            add((X[i], y), (X[i + 1], y))
    return out


def beam_segs(model: dict, story: str):
    out = []
    for m in (model or {}).get("members", []) or []:
        if m.get("kind") != "beam" or m.get("story") != story or m.get("curve"):
            continue
        a, b = m["pi"], m["pj"]
        if math.hypot(b[0] - a[0], b[1] - a[1]) > TOL:
            out.append([_p(a[0], a[1]), _p(b[0], b[1]), m.get("uid")])
    return out


def scope_stories(stories, story: str, scope: str) -> List[str]:
    ss = stories or []
    if scope == "all":
        return [s["name"] for s in ss]
    if scope != "similar":
        return [story]
    s = next((x for x in ss if x["name"] == story), None)
    if s is None:
        return [story]
    master = s["name"] if s.get("master") else (s.get("similar_to") or "")
    if not master:
        return [story]
    return [t["name"] for t in ss
            if t["name"] == master or (t.get("similar_to") or "") == master]


def next_pier(shells) -> str:
    used = {s.get("pier") for s in shells or [] if s.get("pier")}
    k = 1
    while f"P{k}" in used:
        k += 1
    return f"P{k}"


def corner_key(corners) -> str:
    return ";".join(sorted(",".join(f"{r9(v):.6f}" for v in c)
                           for c in corners))


def build_walls(panels, stories, names, bot_off: float = 0.0,
                top_off: float = 0.0, existing=()):
    keys = {s["story"] + "|" + corner_key(s["corners"])
            for s in existing or [] if s.get("kind") == "wall"}
    out = []
    for name in names or []:
        st = next((s for s in stories or [] if s["name"] == name), None)
        if st is None:
            continue
        zb, zt = st["elevation"] - st["height"], st["elevation"]
        for a, b in panels or []:
            c = wall_corners(a, b, zb, zt, bot_off, top_off)
            if c is None:
                continue
            k = name + "|" + corner_key(c)
            if k in keys:
                continue
            keys.add(k)
            out.append({"story": name, "corners": c})
    return out
