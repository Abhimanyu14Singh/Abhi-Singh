"""Wave F pick / selection / story-scope geometry — the JS-free mirror of
``skyframe/api/web/js/f_pickgeom.js`` (CONTRACT "Wave F — select and assign
in every view").

Pure functions on plain lists / model dicts; no UI and no analysis.  The
browser module and this one must agree exactly (tests/test_f_select.py runs
the same cases through node when it is available).

Screen-space pick math
    dist_to_seg, point_in_poly, segs_intersect
    rect_hit_point / rect_hit_seg / rect_hit_poly
        WINDOW (``crossing=False``): the object lies entirely inside the
        rectangle; CROSSING (``crossing=True``): any part of it touches the
        rectangle (a vertex inside, an edge crossing a rectangle edge, or the
        rectangle inside a polygon)
    line_hit_seg / line_hit_poly / line_hit_point   (Intersecting Line)
World-space rays
    ray_triangle (Moller-Trumbore, two-sided), ray_polygon (planar polygon
    with optional holes, convex or not)
Pick priority
    resolve_pick: point springs (they sit on their joint), joints, frames /
    links, line springs / tendons, shells last (ETABS point -> line -> area)
Model helpers
    joint_key / model_joints / story_at_z / story_of_ref / scope_stories /
    counterpart / corresponding_refs (This Story / Similar Stories / All
    Stories: same kind, same plan position on the other story, shifted by
    the difference of the story TOP elevations).
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence

from skyframe.core import similar_stories as _sim

TYPE_PRIORITY = {"spring": 0, "joint": 1, "member": 2, "link": 2,
                 "linespring": 3, "tendon": 3, "shell": 5}
GEOM_TOL = 1e-4


# ------------------------------------------------------------ 2D primitives
def dist_to_seg(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = ((px - ax) * dx + (py - ay) * dy) / l2 if l2 else 0.0
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_in_poly(x, y, pts) -> bool:
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i][0], pts[i][1]
        xj, yj = pts[j][0], pts[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _orient(p, q, r) -> int:
    v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    if abs(v) < 1e-12:
        return 0
    return 1 if v > 0 else -1


def _on(p, q, r) -> bool:
    return (min(p[0], r[0]) - 1e-12 <= q[0] <= max(p[0], r[0]) + 1e-12 and
            min(p[1], r[1]) - 1e-12 <= q[1] <= max(p[1], r[1]) + 1e-12)


def segs_intersect(a, b, c, d) -> bool:
    """Segments AB and CD intersect (touching counts)."""
    o1, o2 = _orient(a, b, c), _orient(a, b, d)
    o3, o4 = _orient(c, d, a), _orient(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return ((o1 == 0 and _on(a, c, b)) or (o2 == 0 and _on(a, d, b)) or
            (o3 == 0 and _on(c, a, d)) or (o4 == 0 and _on(c, b, d)))


def norm_rect(r):
    return [min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])]


def rect_hit_point(r, x, y) -> bool:
    return r[0] <= x <= r[2] and r[1] <= y <= r[3]


def _rect_edges(r):
    return [((r[0], r[1]), (r[2], r[1])), ((r[2], r[1]), (r[2], r[3])),
            ((r[2], r[3]), (r[0], r[3])), ((r[0], r[3]), (r[0], r[1]))]


def rect_hit_seg(r, ax, ay, bx, by, crossing: bool) -> bool:
    ia, ib = rect_hit_point(r, ax, ay), rect_hit_point(r, bx, by)
    if not crossing:
        return ia and ib
    if ia or ib:
        return True
    return any(segs_intersect((ax, ay), (bx, by), e0, e1)
               for e0, e1 in _rect_edges(r))


def rect_hit_poly(r, pts, crossing: bool) -> bool:
    if not pts:
        return False
    if not crossing:
        return all(rect_hit_point(r, p[0], p[1]) for p in pts)
    if any(rect_hit_point(r, p[0], p[1]) for p in pts):
        return True
    edges = _rect_edges(r)
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if any(segs_intersect(a, b, e0, e1) for e0, e1 in edges):
            return True
    return point_in_poly(r[0], r[1], pts)


def line_hit_seg(p, q, a, b) -> bool:
    return segs_intersect(p, q, a, b)


def line_hit_poly(p, q, pts) -> bool:
    n = len(pts)
    return any(segs_intersect(p, q, pts[i], pts[(i + 1) % n]) for i in range(n))


def line_hit_point(p, q, pt, tol) -> bool:
    return dist_to_seg(pt[0], pt[1], p[0], p[1], q[0], q[1]) <= tol


# ------------------------------------------------------------ 3D rays
def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def ray_triangle(o, d, a, b, c) -> Optional[float]:
    """Moller-Trumbore (two-sided): ray parameter t > 0 or None."""
    e1, e2 = _sub(b, a), _sub(c, a)
    p = _cross(d, e2)
    det = _dot(e1, p)
    if abs(det) < 1e-14:
        return None
    inv = 1.0 / det
    s = _sub(o, a)
    u = _dot(s, p) * inv
    if u < -1e-12 or u > 1 + 1e-12:
        return None
    q = _cross(s, e1)
    v = _dot(d, q) * inv
    if v < -1e-12 or u + v > 1 + 1e-12:
        return None
    t = _dot(e2, q) * inv
    return t if t > 1e-12 else None


def poly_normal(pts):
    n = [0.0, 0.0, 0.0]
    m = len(pts)
    for i in range(m):
        a, b = pts[i], pts[(i + 1) % m]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return n


def ray_polygon(o, d, pts, holes=()) -> Optional[float]:
    if not pts or len(pts) < 3:
        return None
    n = poly_normal(pts)
    den = _dot(n, d)
    if abs(den) < 1e-14 * math.hypot(*n) * math.hypot(*d):
        return None
    t = _dot(n, _sub(pts[0], o)) / den
    if not t > 1e-12:
        return None
    x = [o[k] + t * d[k] for k in range(3)]
    ax, ay, az = abs(n[0]), abs(n[1]), abs(n[2])
    if az >= ax and az >= ay:
        i, j = 0, 1
    elif ax >= ay:
        i, j = 1, 2
    else:
        i, j = 0, 2
    if not point_in_poly(x[i], x[j], [(p[i], p[j]) for p in pts]):
        return None
    for h in holes or ():
        if h and len(h) >= 3 and point_in_poly(x[i], x[j], [(p[i], p[j]) for p in h]):
            return None
    return t


# ------------------------------------------------------------ priority
def resolve_pick(hits: Sequence[dict]):
    """hits: [{ref: {type, uid}, d (px), z (depth)}] -> winning ref or None."""
    if not hits:
        return None

    def key(h):
        t = h["ref"]["type"]
        return (TYPE_PRIORITY.get(t, 9), 0 if t == "shell" else h["d"], h["z"],
                str(h["ref"]["uid"]))
    return sorted(hits, key=key)[0]["ref"]


# ------------------------------------------------------------ joints
def _js_num(v: float) -> str:
    """JavaScript Number -> string for a value rounded to 1e-6."""
    r = round(v * 1e6) / 1e6
    if r == 0:
        return "0"
    if r == int(r) and abs(r) < 1e21:
        return str(int(r))
    s = repr(r)
    if "e" in s or "E" in s:
        s = format(Decimal(s), "f")
    return s


def r6(v: float) -> float:
    r = round(float(v) * 1e6) / 1e6
    return 0.0 if r == 0 else r


def joint_key(p) -> str:
    return ",".join(_js_num(float(x)) for x in p)


def joint_point(key: str) -> List[float]:
    return [float(x) for x in str(key).split(",")]


def model_joints(m: dict) -> Dict[str, List[float]]:
    out: Dict[str, List[float]] = {}

    def add(p):
        if isinstance(p, (list, tuple)) and len(p) == 3:
            k = joint_key(p)
            if k not in out:
                out[k] = [float(x) for x in p]
    for mm in m.get("members") or []:
        add(mm["pi"]); add(mm["pj"])
    for s in m.get("shells") or []:
        for c in s.get("corners") or []:
            add(c)
    for lk in m.get("links") or []:
        add(lk["pi"]); add(lk["pj"])
    for s in m.get("supports") or []:
        add(s["point"])
    for s in m.get("spring_supports") or []:
        add(s["point"])
    for s in m.get("line_springs") or []:
        add(s["p1"]); add(s["p2"])
    return out


# ------------------------------------------------------------ stories / scope
class _St:
    """Story-like adapter for core.similar_stories on a model dict."""

    def __init__(self, d):
        self.name = d.get("name")
        self.master = bool(d.get("master", False))
        self.similar_to = d.get("similar_to") or ""
        self.elevation = float(d.get("elevation", 0.0))


class _M:
    def __init__(self, m):
        self.stories = [_St(s) for s in m.get("stories") or []]


def story_at_z(m: dict, z: float) -> Optional[str]:
    for s in m.get("stories") or []:
        if abs(float(s["elevation"]) - float(z)) <= GEOM_TOL:
            return s["name"]
    return None


def story_of_ref(m: dict, ref: dict) -> Optional[str]:
    t, uid = ref["type"], ref["uid"]
    if t == "member":
        x = next((o for o in m.get("members") or [] if o["uid"] == uid), None)
        return (x.get("story") or story_at_z(m, max(x["pi"][2], x["pj"][2]))) if x else None
    if t == "shell":
        x = next((o for o in m.get("shells") or [] if o["uid"] == uid), None)
        return (x.get("story") or story_at_z(m, max(c[2] for c in x["corners"]))) if x else None
    if t == "link":
        x = next((o for o in m.get("links") or [] if o["uid"] == uid), None)
        return story_at_z(m, max(x["pi"][2], x["pj"][2])) if x else None
    if t in ("joint", "spring"):
        return story_at_z(m, joint_point(uid)[2])
    return None


def scope_stories(m: dict, story: str, scope: str) -> List[str]:
    return _sim.scope_stories(_M(m), story, scope)


def _same(a, b, tol=GEOM_TOL) -> bool:
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))


def _shift(p, dz):
    return [float(p[0]), float(p[1]), float(p[2]) + dz]


def _same_set(A, B) -> bool:
    if len(A) != len(B):
        return False
    used = [False] * len(B)
    for a in A:
        for k, b in enumerate(B):
            if not used[k] and _same(a, b):
                used[k] = True
                break
        else:
            return False
    return True


def _spring_key(p) -> str:
    """modeledit.springKey: +(+v).toFixed(4) joined by commas."""
    out = []
    for v in p:
        r = float(Decimal(repr(float(v))).quantize(Decimal("0.0001"), rounding="ROUND_HALF_UP"))
        out.append(_js_num(r))
    return ",".join(out)


def counterpart(m: dict, ref: dict, frm: str, to: str) -> Optional[dict]:
    st = {s["name"]: s for s in m.get("stories") or []}
    if frm not in st or to not in st:
        return None
    dz = float(st[to]["elevation"]) - float(st[frm]["elevation"])
    t, uid = ref["type"], ref["uid"]
    if t in ("member", "link"):
        key = "members" if t == "member" else "links"
        x = next((o for o in m.get(key) or [] if o["uid"] == uid), None)
        if x is None:
            return None
        a, b = _shift(x["pi"], dz), _shift(x["pj"], dz)
        for o in m.get(key) or []:
            if o is x or (t == "member" and o.get("kind") != x.get("kind")):
                continue
            if (_same(o["pi"], a) and _same(o["pj"], b)) or (_same(o["pi"], b) and _same(o["pj"], a)):
                return {"type": t, "uid": o["uid"]}
        return None
    if t == "shell":
        x = next((o for o in m.get("shells") or [] if o["uid"] == uid), None)
        if x is None:
            return None
        cs = [_shift(c, dz) for c in x["corners"]]
        for o in m.get("shells") or []:
            if o is not x and o.get("kind") == x.get("kind") and _same_set(o["corners"], cs):
                return {"type": "shell", "uid": o["uid"]}
        return None
    if t == "joint":
        k = joint_key(_shift(joint_point(uid), dz))
        return {"type": "joint", "uid": k} if k in model_joints(m) else None
    if t == "spring":
        p = _shift(joint_point(uid), dz)
        y = next((s for s in m.get("spring_supports") or [] if _same(s["point"], p)), None)
        return {"type": "spring", "uid": _spring_key(y["point"])} if y else None
    return None


def corresponding_refs(m: dict, refs: Iterable[dict], scope: str) -> dict:
    """Counterparts of ``refs`` on the other stories of the scope:
    ``{"pairs": [{src, dst, from, to, dz}], "refs": [dst...], "missing": n}``
    (``refs`` excludes objects already in the input)."""
    out = {"pairs": [], "refs": [], "missing": 0}
    refs = list(refs or [])
    if not scope or scope == "one":
        return out
    have = {f'{r["type"]}:{r["uid"]}' for r in refs}
    seen = set()
    st = {s["name"]: s for s in m.get("stories") or []}
    for r in refs:
        frm = story_of_ref(m, r)
        if not frm:
            continue
        for to in scope_stories(m, frm, scope):
            if to == frm:
                continue
            dst = counterpart(m, r, frm, to)
            if dst is None:
                out["missing"] += 1
                continue
            out["pairs"].append({"src": r, "dst": dst, "from": frm, "to": to,
                                 "dz": float(st[to]["elevation"]) - float(st[frm]["elevation"])})
            k = f'{dst["type"]}:{dst["uid"]}'
            if k not in have and k not in seen:
                seen.add(k)
                out["refs"].append(dst)
    return out
