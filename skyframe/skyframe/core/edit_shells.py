"""ETABS Edit > Edit Shells, Draw Ramp, Draw Wall Stacks and Auto Relabel.

CONTRACT "Model templates, general grids, shell editing and drawing aids".

Registered into :mod:`skyframe.core.edit` (``POST /api/edit/<op>``), so
every op is a PURE function of the model JSON with the exact
``(model, summary)`` / undo semantics of the existing Edit utilities.  The
SPA mirrors the algorithms in ``js/wb_shellgeom.js`` for ``?mock=1``.

Operations (``op`` -> ``params``):

* ``shell_divide``  - selected shells; ``mode`` "n" ``{n1, n2}`` or
  "size" ``{max_size}``.  4-corner shells are subdivided in their bilinear
  parameter space (n1 along c0->c1, n2 along c0->c3); every sub-quad of a
  planar quad is planar and the pieces tile the parent exactly.  Convex
  polygon shells (3 or 5+ corners) are chopped by the planes normal to the
  local 1 / 2 axes at equal spacing.
* ``shell_merge``   - >= 2 coplanar selected shells of the same kind,
  behavior and section that form ONE region without holes: edges shared by
  two shells cancel (T-junction vertices are inserted first), the remaining
  boundary loop becomes the merged shell (collinear vertices dropped).  The
  first selected shell keeps its uid and assignments; area loads must be
  identical on every merged shell (else 400).
* ``shell_chop``    - convex selected shells cut by planes: ``mode``
  "grid" (every grid line of every grid system, as a vertical plane),
  "lines" ``{lines: [[[x, y], [x, y]], ...]}`` (vertical planes through plan
  lines), "frames" (vertical planes through the plan traces of the
  selected non-vertical frames), "levels" ``{z?: [..]}`` (horizontal
  planes; default every story level).  Pieces conserve the area exactly.
* ``shell_expand``  - one selected shell; ``{distance, edge?}``: the edge
  ``edge`` (corner k -> k+1; omitted = every edge) moves along its in-plane
  outward normal by ``distance`` (negative shrinks); the neighbouring edge
  lines are kept (their intersections give the new corners).
* ``shell_reshape`` - one selected shell; ``mode`` "corners"
  ``{corners}`` | "move" ``{index, to}`` | "insert" ``{edge, point?}``
  (default: edge midpoint) | "delete" ``{index}``.
* ``ramp``          - ``{p1: [x, y], p2: [x, y], width, story_from,
  story_to, section?, behavior? = "shell", mesh_size? = 1}``: a sloped slab
  from the plan line p1 (at story_from's level) to p2 (at story_to's level)
  of the given width, corners counter-clockwise from +Z, ``story`` =
  story_to.  The engine slaves only nodes lying ON a rigid-diaphragm level,
  so the ramp's two level edges join their diaphragms and its sloped
  interior stays free (the ETABS behaviour).
* ``wall_stack``    - ``{shape: "straight"|"L"|"C"|"I"|"T"|"box", origin:
  [x, y], rotation (deg), B, D, stories: [names], section?, pier?,
  mesh_size?}``: centre-line wall legs (flanges split at web junctions so
  the meshes connect) on every listed story, all with the pier label
  ``pier`` (default the next free ``P<n>``).
* ``relabel``       - Auto Relabel: ``{kinds?: [...]}``.  Objects get
  ETABS story labels: a label number per PLAN position (columns: plan
  point; other frames / walls / links: plan midpoint; slabs: plan
  centroid), numbered by (y, x) per prefix C (column), B (beam), D (brace),
  W (wall), F (floor), L (link), so a column stack shares its label on
  every story.  uid = ``<label>-<story>`` (``<label>`` alone when the
  object has no story).  Every uid reference follows (loads, groups,
  tendon hosts, pushover / TH ``My``).
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional, Sequence, Tuple

from . import edit as _ed
from . import shell_holes as _shh  # Wave C: shells with openings

_TOL = 1e-9
SHELL_OPS = ("shell_divide", "shell_merge", "shell_chop", "shell_expand",
             "shell_reshape", "ramp", "wall_stack", "relabel")
WALL_SHAPES = ("straight", "L", "C", "I", "T", "box")
MAX_PIECES = 400


# --------------------------------------------------------------------------- #
# vector / polygon helpers (3D, planar polygons)
# --------------------------------------------------------------------------- #
_sub, _add, _mul, _dot, _cross, _norm = (_ed._sub, _ed._add, _ed._mul,
                                         _ed._dot, _ed._cross, _ed._norm)


def _unit(v):
    n = _norm(v)
    if n < 1e-15:
        raise ValueError("degenerate direction")
    return [v[0] / n, v[1] / n, v[2] / n]


def newell(pts) -> List[float]:
    """Newell normal (length = 2 x area)."""
    n = [0.0, 0.0, 0.0]
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return n


def area(pts) -> float:
    return 0.5 * _norm(newell(pts))


def is_convex(pts) -> bool:
    n = newell(pts)
    sgn = 0
    for i in range(len(pts)):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
        z = _dot(_cross(_sub(b, a), _sub(c, b)), n)
        if abs(z) < 1e-12:
            continue
        s = 1 if z > 0 else -1
        if sgn and s != sgn:
            return False
        sgn = s
    return True


def drop_collinear(pts, tol=1e-9) -> List[list]:
    out = list(pts)
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
            ab, bc = _sub(b, a), _sub(c, b)
            if _norm(ab) < tol or _norm(_cross(ab, bc)) <= tol * max(
                    1.0, _norm(ab) * _norm(bc)) and _dot(ab, bc) > 0:
                out.pop(i)
                changed = True
                break
    return out


def clip_plane(pts, P0, N) -> Tuple[List[list], List[list]]:
    """Split a CONVEX polygon by the plane through P0 with normal N:
    ``(front, back)`` (front: (p - P0).N >= 0); orientation kept."""
    s = [_dot(_sub(p, P0), N) for p in pts]
    front, back = [], []
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        sa, sb = s[i], s[(i + 1) % n]
        if sa >= -_TOL:
            front.append(a)
        if sa <= _TOL:
            back.append(a)
        if (sa > _TOL and sb < -_TOL) or (sa < -_TOL and sb > _TOL):
            t = sa / (sa - sb)
            p = _ed._clean([a[k] + t * (b[k] - a[k]) for k in range(3)])
            front.append(p)
            back.append(p)
    return _dedupe(front), _dedupe(back)


def _dedupe(pts):
    out = []
    for p in pts:
        if not out or _ed._key(out[-1]) != _ed._key(p):
            out.append(_ed._clean(p))
    while len(out) > 1 and _ed._key(out[0]) == _ed._key(out[-1]):
        out.pop()
    return out


def _orient_like(pts, e1):
    """Rotate a polygon's vertex list so its first edge best follows e1."""
    best, bi = -2.0, 0
    for i in range(len(pts)):
        d = _sub(pts[(i + 1) % len(pts)], pts[i])
        if _norm(d) < 1e-12:
            continue
        c = _dot(_unit(d), e1)
        if c > best + 1e-9:
            best, bi = c, i
    return pts[bi:] + pts[:bi]


def line_line(P, D1, Q, D2):
    """Intersection point of two coplanar 3D lines (ValueError if parallel)."""
    c = _cross(D1, D2)
    cc = _dot(c, c)
    if cc < 1e-20 * max(1.0, _dot(D1, D1) * _dot(D2, D2)):
        raise ValueError("adjacent edges are parallel to the moved edge")
    s = _dot(_cross(_sub(Q, P), D2), c) / cc
    return _ed._clean(_add(P, _mul(D1, s)))


# --------------------------------------------------------------------------- #
# derived shells / reference bookkeeping
# --------------------------------------------------------------------------- #
def _shell_loads(d, uid):
    out = []
    for pn, pat in (d.get("patterns") or {}).items():
        for k in ("area_loads", "shell_thermal_loads"):
            for ld in pat.get(k) or []:
                if ld.get("region_uid") == uid:
                    out.append((pn, k, ld))
    return out


def _copy_loads(d, src, dst):
    for pn, k, ld in _shell_loads(d, src):
        nl = copy.deepcopy(ld)
        nl["region_uid"] = dst
        d["patterns"][pn][k].append(nl)


def _drop_loads(d, uid):
    for pat in (d.get("patterns") or {}).values():
        for k in ("area_loads", "shell_thermal_loads"):
            if pat.get(k):
                pat[k] = [x for x in pat[k] if x.get("region_uid") != uid]


def _no_openings(s):
    if s.get("openings"):
        raise ValueError(f"shell {s['uid']}: shells with openings cannot be "
                         "edited this way (remove the openings first)")


def _replace_pieces(ctx, s, pieces: List[list]) -> List[str]:
    """Replace shell ``s`` by polygons ``pieces`` (first keeps the uid)."""
    d = ctx.d
    if len(pieces) > MAX_PIECES:
        raise ValueError(f"too many pieces (> {MAX_PIECES})")
    e1 = _unit(_sub(s["corners"][1], s["corners"][0]))
    uids = []
    idx = d["shells"].index(s)
    new_objs = []
    for k, pc in enumerate(pieces):
        pc = _orient_like([_ed._clean(p) for p in pc], e1)
        if k == 0:
            s["corners"] = pc
            ctx.restory(s, pc)
            uids.append(s["uid"])
            ctx.mark_modified("shells", s["uid"])
            continue
        ns = copy.deepcopy(s)
        ns["uid"] = ctx.new_uid(s["uid"], "S")
        ns["corners"] = pc
        ctx.restory(ns, pc)
        new_objs.append(ns)
        uids.append(ns["uid"])
        ctx.created["shells"].append(ns["uid"])
        _copy_loads(d, s["uid"], ns["uid"])
    d["shells"][idx + 1:idx + 1] = new_objs
    if len(uids) > 1:
        _ed._replace_uid_refs(d, "shells", s["uid"], uids)
    return uids


def _sel_shells(ctx, sel, what, need=1):
    if not sel["shells"]:
        raise ValueError(f"{what}: select at least {need} shell"
                         f"{'s' if need > 1 else ''}")
    if len(sel["shells"]) < need:
        raise ValueError(f"{what}: select at least {need} shells")
    return [ctx.shell(u) for u in sel["shells"]]


# --------------------------------------------------------------------------- #
# divide
# --------------------------------------------------------------------------- #
def bilinear(c, u, v):
    return [(1 - u) * (1 - v) * c[0][k] + u * (1 - v) * c[1][k]
            + u * v * c[2][k] + (1 - u) * v * c[3][k] for k in range(3)]


def divide_quad(c, n1, n2) -> List[list]:
    out = []
    for j in range(n2):
        for i in range(n1):
            u0, u1, v0, v1 = i / n1, (i + 1) / n1, j / n2, (j + 1) / n2
            out.append([_ed._clean(bilinear(c, u0, v0)),
                        _ed._clean(bilinear(c, u1, v0)),
                        _ed._clean(bilinear(c, u1, v1)),
                        _ed._clean(bilinear(c, u0, v1))])
    return out


def _local_axes(c):
    n = _unit(newell(c))
    e1 = _unit(_sub(c[1], c[0]))
    return e1, _cross(n, e1), n


def divide_polygon(c, n1, n2) -> List[list]:
    """Convex polygon chopped by n1-1 / n2-1 equally spaced planes normal
    to the local 1 / 2 axes."""
    e1, e2, _n = _local_axes(c)
    pieces = [c]
    for axis, n in ((e1, n1), (e2, n2)):
        proj = [_dot(p, axis) for p in c]
        lo, hi = min(proj), max(proj)
        for k in range(1, n):
            off = lo + (hi - lo) * k / n
            nxt = []
            for pc in pieces:
                f, b = clip_plane(pc, _mul(axis, off), axis)
                for q in (b, f):
                    if len(q) >= 3 and area(q) > 1e-12:
                        nxt.append(q)
            pieces = nxt
    return pieces


def op_shell_divide(ctx, sel, params):
    shells = _sel_shells(ctx, sel, "shell_divide")
    mode = params.get("mode", "n")
    divided = {}
    for s in shells:
        c = [list(map(float, p)) for p in s["corners"]]
        if mode == "n":
            n1 = _ed._num(params, "n1", 2, integer=True, lo=1, hi=100)
            n2 = _ed._num(params, "n2", 2, integer=True, lo=1, hi=100)
        elif mode == "size":
            h = _ed._num(params, "max_size", positive=True)
            e1, e2, _ = _local_axes(c)
            l1 = max(_dot(p, e1) for p in c) - min(_dot(p, e1) for p in c)
            l2 = max(_dot(p, e2) for p in c) - min(_dot(p, e2) for p in c)
            if len(c) == 4:
                l1 = max(_norm(_sub(c[1], c[0])), _norm(_sub(c[2], c[3])))
                l2 = max(_norm(_sub(c[3], c[0])), _norm(_sub(c[2], c[1])))
            n1 = max(1, math.ceil(l1 / h - 1e-9))
            n2 = max(1, math.ceil(l2 / h - 1e-9))
        else:
            raise ValueError("shell_divide: mode must be 'n' or 'size'")
        if n1 * n2 <= 1:
            ctx.warnings.append(f"shell {s['uid']}: nothing to divide")
            continue
        if len(c) == 4:
            pieces = divide_quad(c, n1, n2)
        else:
            if not is_convex(c):
                raise ValueError(f"shell {s['uid']}: only 4-corner or convex "
                                 "polygon shells can be divided")
            pieces = divide_polygon(c, n1, n2)
        divided[s["uid"]] = (_shh.replace_with_openings(ctx, s, pieces)   # Wave C
                             if s.get("openings") else
                             _replace_pieces(ctx, s, pieces))
    ns = _ed._new_sel()
    ns["shells"] = [u for v in divided.values() for u in v]
    return {"divided": divided, "new_selection": ns}


# --------------------------------------------------------------------------- #
# merge
# --------------------------------------------------------------------------- #
def merge_polygons(polys: List[List[list]]) -> List[list]:
    """Union of edge-adjacent coplanar polygons (same orientation) as one
    boundary loop (ValueError when the union has holes / is disconnected)."""
    K = _ed._key
    pts = {}
    for pg in polys:
        for p in pg:
            pts[K(p)] = list(p)
    edges = []
    for pg in polys:
        for i in range(len(pg)):
            a, b = pg[i], pg[(i + 1) % len(pg)]
            # insert any vertex lying strictly inside the edge (T-junction)
            ab = _sub(b, a)
            L2 = _dot(ab, ab)
            inner = []
            for q in pts.values():
                t = _dot(_sub(q, a), ab) / L2
                if 1e-9 < t < 1 - 1e-9:
                    foot = _add(a, _mul(ab, t))
                    if _norm(_sub(q, foot)) < 1e-6:
                        inner.append((t, q))
            chain = [a] + [q for _, q in sorted(inner)] + [b]
            for k in range(len(chain) - 1):
                edges.append((K(chain[k]), K(chain[k + 1])))
    cnt: Dict[tuple, int] = {}
    for e in edges:
        cnt[e] = cnt.get(e, 0) + 1
    if any(v > 1 for v in cnt.values()):
        raise ValueError("shells overlap (an edge is used twice in the same "
                         "direction)")
    bnd = [e for e in edges if (e[1], e[0]) not in cnt]
    nxt: Dict[tuple, tuple] = {}
    for a, b in bnd:
        if a in nxt:
            raise ValueError("the merged region would touch itself at a "
                             "point")
        nxt[a] = b
    start = bnd[0][0]
    loop, cur = [start], nxt[start]
    while cur != start:
        loop.append(cur)
        if cur not in nxt or len(loop) > len(bnd):
            raise ValueError("could not trace the merged boundary")
        cur = nxt[cur]
    if len(loop) != len(bnd):
        raise ValueError("the shells do not form one region without holes "
                         "(they must share edges)")
    return drop_collinear([pts[k] for k in loop])


def _loads_signature(d, uid):
    sig = []
    for pn, k, ld in _shell_loads(d, uid):
        x = dict(ld)
        x.pop("region_uid", None)
        sig.append((pn, k, repr(sorted(x.items()))))
    return sorted(sig)


def op_shell_merge(ctx, sel, params):
    shells = _sel_shells(ctx, sel, "shell_merge", need=2)
    s0 = shells[0]
    n0 = _unit(newell(s0["corners"]))
    polys = []
    for s in shells:
        for key in ("kind", "behavior", "section"):
            if s.get(key) != s0.get(key):
                raise ValueError(f"shell_merge: {s['uid']} has a different "
                                 f"{key} than {s0['uid']}")
        c = [list(map(float, p)) for p in s["corners"]]
        n = _unit(newell(c))
        if abs(abs(_dot(n, n0)) - 1.0) > 1e-9 or any(
                abs(_dot(_sub(p, s0["corners"][0]), n0)) > 1e-6 for p in c):
            raise ValueError(f"shell_merge: {s['uid']} is not coplanar with "
                             f"{s0['uid']}")
        if _dot(n, n0) < 0:
            c = c[::-1]
        polys.append(c)
        if _loads_signature(ctx.d, s["uid"]) != _loads_signature(
                ctx.d, s0["uid"]):
            raise ValueError(f"shell_merge: {s['uid']} carries different "
                             f"area loads than {s0['uid']} — make them equal "
                             "first")
    holes = None
    if any(s.get("openings") for s in shells):     # Wave C: keep openings
        merged, holes = _shh.merge_with_openings(shells, polys)
    else:
        try:
            merged = merge_polygons(polys)
        except ValueError as exc:                  # Wave C: a ring of shells
            if "without holes" not in str(exc):    # -> one shell + opening
                raise
            merged, holes = _shh.merge_with_openings(shells, polys)
            if not holes:
                raise
        a_sum = sum(area(p) for p in polys)
        if holes is None and abs(area(merged) - a_sum) > 1e-9 * max(
                1.0, a_sum):
            raise ValueError("shell_merge: shells overlap")
    c0k = _ed._key(s0["corners"][0])
    ks = [_ed._key(p) for p in merged]
    if c0k in ks:
        i = ks.index(c0k)
        merged = merged[i:] + merged[:i]
    merged = [_ed._clean(p) for p in merged]
    s0["corners"] = merged
    if holes is not None:
        s0["openings"] = _shh.openings_for(merged, holes)   # Wave C
    ctx.mark_modified("shells", s0["uid"])
    gone = [s["uid"] for s in shells[1:]]
    for u in gone:
        _ed._replace_uid_refs(ctx.d, "shells", u, [s0["uid"]])
    ctx.d["shells"] = [s for s in ctx.d["shells"] if s["uid"] not in gone]
    for u in gone:
        _drop_loads(ctx.d, u)
        ctx.deleted["shells"].append(u)
    ns = _ed._new_sel()
    ns["shells"] = [s0["uid"]]
    return {"merged": {s0["uid"]: [s["uid"] for s in shells]},
            "new_selection": ns}


# --------------------------------------------------------------------------- #
# chop
# --------------------------------------------------------------------------- #
def _grid_planes(d):
    from .model import GridSystem
    planes = []
    grids = d.get("grid_systems") or ([d["grid"]] if d.get("grid") else [])
    for gd in grids:
        g = GridSystem.from_dict(gd)
        for ln in g.lines_global():
            pts = ln["points"]
            if len(pts) != 2:
                continue                       # circles are not planes
            planes.append(_vplane(pts[0], pts[1]))
    return [p for p in planes if p]


def _vplane(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return None
    return ([float(a[0]), float(a[1]), 0.0], [-dy / L, dx / L, 0.0])


def op_shell_chop(ctx, sel, params):
    shells = _sel_shells(ctx, sel, "shell_chop")
    mode = params.get("mode", "grid")
    if mode == "grid":
        planes = _grid_planes(ctx.d)
    elif mode == "lines":
        lines = params.get("lines")
        if not isinstance(lines, list) or not lines:
            raise ValueError("shell_chop: 'lines' must be a non-empty list of "
                             "[[x, y], [x, y]]")
        planes = []
        for ln in lines:
            if not isinstance(ln, list) or len(ln) != 2:
                raise ValueError("shell_chop: each line is [[x, y], [x, y]]")
            a = [float(v) for v in ln[0]][:2]
            b = [float(v) for v in ln[1]][:2]
            planes.append(_vplane(a, b))
        planes = [p for p in planes if p]
    elif mode == "frames":
        planes = []
        for u in sel["members"]:
            m = ctx.member(u)
            planes.append(_vplane(m["pi"], m["pj"]))
        planes = [p for p in planes if p]
        if not planes:
            raise ValueError("shell_chop: select non-vertical frames to "
                             "chop at")
    elif mode == "levels":
        zs = params.get("z")
        if zs is None:
            zs = [float(s["elevation"]) for s in ctx.d.get("stories") or []]
            zs += [float(s["elevation"]) - float(s["height"])
                   for s in ctx.d.get("stories") or []]
        if not isinstance(zs, list):
            raise ValueError("shell_chop: 'z' must be a list of elevations")
        planes = [([0.0, 0.0, float(z)], [0.0, 0.0, 1.0]) for z in
                  sorted(set(round(float(z), 9) for z in zs))]
    else:
        raise ValueError("shell_chop: mode must be grid|lines|frames|levels")
    chopped = {}
    for s in shells:
        c = [list(map(float, p)) for p in s["corners"]]
        if not is_convex(c):
            ctx.warnings.append(f"shell {s['uid']}: concave shells are not "
                                "chopped")
            continue
        pieces = [c]
        for P0, N in planes:
            nxt = []
            for pc in pieces:
                f, b = clip_plane(pc, P0, N)
                if len(f) >= 3 and len(b) >= 3 and area(f) > 1e-9 \
                        and area(b) > 1e-9:
                    nxt.extend([drop_collinear(b), drop_collinear(f)])
                else:
                    nxt.append(pc)
            pieces = nxt
        if len(pieces) > 1:
            chopped[s["uid"]] = (_shh.replace_with_openings(ctx, s, pieces)
                                 if s.get("openings") else   # Wave C
                                 _replace_pieces(ctx, s, pieces))
    if not chopped:
        ctx.warnings.append("shell_chop: no shell was crossed by a cutting "
                            "plane")
    ns = _ed._new_sel()
    ns["shells"] = [u for v in chopped.values() for u in v]
    return {"chopped": chopped, "new_selection": ns}


# --------------------------------------------------------------------------- #
# expand / shrink
# --------------------------------------------------------------------------- #
def offset_edges(c, dist, edges: Sequence[int]) -> List[list]:
    n = _unit(newell(c))
    N = len(c)
    lines = []
    for i in range(N):
        a, b = c[i], c[(i + 1) % N]
        D = _sub(b, a)
        P = a
        if i in edges:
            o = _unit(_cross(D, n))            # in-plane outward normal
            P = _add(a, _mul(o, dist))
        lines.append((P, D))
    out = []
    for i in range(N):
        P1, D1 = lines[i - 1]
        P2, D2 = lines[i]
        if (i - 1) % N not in edges and i not in edges:
            out.append(list(c[i]))
        else:
            out.append(line_line(P1, D1, P2, D2))
    return out


def op_shell_expand(ctx, sel, params):
    shells = _sel_shells(ctx, sel, "shell_expand")
    dist = _ed._num(params, "distance")
    out = {}
    for s in shells:
        holes = _shh.holes3(s)                     # Wave C: openings stay put
        c = [list(map(float, p)) for p in s["corners"]]
        if params.get("edge") is None:
            edges = list(range(len(c)))
        else:
            k = _ed._num(params, "edge", integer=True, lo=0,
                         hi=len(c) - 1)
            edges = [k]
        nc = offset_edges(c, dist, edges)
        a = area(nc)
        if a < 1e-9 or _dot(newell(nc), newell(c)) <= 0:
            raise ValueError(f"shell_expand: {s['uid']} would collapse")
        s["corners"] = [_ed._clean(p) for p in nc]
        if holes:                                  # Wave C
            s["openings"] = _shh.openings_for(s["corners"], holes)
            _shh.check_inside(s, "shell_expand")
        ctx.mark_modified("shells", s["uid"])
        out[s["uid"]] = {"area_before": area(c), "area_after": a}
    ns = _ed._new_sel()
    ns["shells"] = list(out)
    return {"expanded": out, "new_selection": ns}


# --------------------------------------------------------------------------- #
# vertex reshaper
# --------------------------------------------------------------------------- #
def op_shell_reshape(ctx, sel, params):
    shells = _sel_shells(ctx, sel, "shell_reshape")
    if len(shells) != 1:
        raise ValueError("shell_reshape: select exactly one shell")
    s = shells[0]
    c = [list(map(float, p)) for p in s["corners"]]
    holes = _shh.holes3(s)                         # Wave C: openings stay put
    mode = params.get("mode", "corners")
    if mode == "corners":
        cs = params.get("corners")
        if not isinstance(cs, list) or len(cs) < 3:
            raise ValueError("shell_reshape: 'corners' needs >= 3 points")
        c = [_ed._f3(p) for p in cs]
    elif mode == "move":
        i = _ed._num(params, "index", integer=True, lo=0, hi=len(c) - 1)
        c[i] = _ed._f3(params.get("to"))
    elif mode == "insert":
        i = _ed._num(params, "edge", integer=True, lo=0, hi=len(c) - 1)
        a, b = c[i], c[(i + 1) % len(c)]
        p = params.get("point")
        p = _ed._f3(p) if p is not None else [(a[k] + b[k]) / 2.0
                                               for k in range(3)]
        c.insert(i + 1, p)
    elif mode == "delete":
        if len(c) <= 3:
            raise ValueError("shell_reshape: a shell keeps at least 3 "
                             "corners")
        i = _ed._num(params, "index", integer=True, lo=0, hi=len(c) - 1)
        c.pop(i)
    else:
        raise ValueError("shell_reshape: mode must be corners|move|insert|"
                         "delete")
    if params.get("on_plane"):                     # Wave C: elevation / 3D
        c = _shh.project_on_plane(s, c)
    if len({_ed._key(p) for p in c}) != len(c):
        raise ValueError("shell_reshape: duplicate corners")
    s["corners"] = [_ed._clean(p) for p in c]
    if holes:                                      # Wave C
        s["openings"] = _shh.openings_for(s["corners"], holes)
        _shh.check_inside(s, "shell_reshape")
    st = ctx.story_for([p[2] for p in c])
    if st is not None:
        s["story"] = st
    ctx.mark_modified("shells", s["uid"])
    ns = _ed._new_sel()
    ns["shells"] = [s["uid"]]
    return {"area": area(c), "new_selection": ns}


# --------------------------------------------------------------------------- #
# ramp
# --------------------------------------------------------------------------- #
def _story(d, name):
    for s in d.get("stories") or []:
        if s["name"] == name:
            return s
    raise ValueError(f"unknown story {name!r}")


def _default_shell_section(d):
    names = list((d.get("shell_sections") or {}))
    for n in names:
        if "SLAB" in n.upper():
            return n
    if not names:
        raise ValueError("define a slab section first")
    return names[0]


def ramp_corners(p1, p2, width, z1, z2) -> List[list]:
    """Sloped quad from plan line p1 (z1) to p2 (z2), counter-clockwise
    seen from +Z (the plan normal points up)."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L = math.hypot(dx, dy)
    if L < 1e-9:
        raise ValueError("ramp: p1 and p2 coincide")
    nx, ny = -dy / L * width / 2.0, dx / L * width / 2.0
    a = [p1[0] - nx, p1[1] - ny, z1]
    b = [p2[0] - nx, p2[1] - ny, z2]
    c = [p2[0] + nx, p2[1] + ny, z2]
    e = [p1[0] + nx, p1[1] + ny, z1]
    return [_ed._clean(p) for p in (a, b, c, e)]


def op_ramp(ctx, sel, params):
    d = ctx.d
    p1 = params.get("p1")
    p2 = params.get("p2")
    for nm, p in (("p1", p1), ("p2", p2)):
        if not isinstance(p, list) or len(p) < 2:
            raise ValueError(f"ramp: '{nm}' must be [x, y]")
    width = _ed._num(params, "width", positive=True)
    s_from = _story(d, params.get("story_from"))
    s_to = _story(d, params.get("story_to"))
    z1, z2 = float(s_from["elevation"]), float(s_to["elevation"])
    if abs(z2 - z1) < 1e-9:
        raise ValueError("ramp: story_from and story_to must differ")
    section = params.get("section") or _default_shell_section(d)
    if section not in (d.get("shell_sections") or {}):
        raise ValueError(f"ramp: unknown shell section {section!r}")
    behavior = params.get("behavior", "shell")
    if behavior not in ("shell", "membrane"):
        raise ValueError("ramp: behavior must be shell|membrane")
    corners = ramp_corners([float(p1[0]), float(p1[1])],
                           [float(p2[0]), float(p2[1])], width, z1, z2)
    uid = ctx.new_uid("RAMP")
    sh = {"uid": uid, "kind": "slab", "behavior": behavior,
          "section": section, "corners": corners,
          "mesh_size": float(params.get("mesh_size", 1.0)),
          "story": s_to["name"] if z2 > z1 else s_from["name"],
          "openings": [], "pier": "", "area_spring": None, "wind_cp": None}
    d["shells"].append(sh)
    ctx.created["shells"].append(uid)
    ns = _ed._new_sel()
    ns["shells"] = [uid]
    L = math.dist(corners[0], corners[1])
    return {"slope": abs(z2 - z1) / math.hypot(corners[1][0] - corners[0][0],
                                                corners[1][1] - corners[0][1]),
            "length": L, "area": area(corners), "new_selection": ns}


# --------------------------------------------------------------------------- #
# wall stacks
# --------------------------------------------------------------------------- #
def wall_stack_legs(shape: str, B: float, D: float) -> List[Tuple]:
    """Centre-line legs [(a, b)] in the stack's LOCAL plan frame (x along B,
    y along D); flanges are split at web junctions."""
    h = B / 2.0
    if shape == "straight":
        return [((0.0, 0.0), (B, 0.0))]
    if shape == "L":
        return [((0.0, D), (0.0, 0.0)), ((0.0, 0.0), (B, 0.0))]
    if shape == "C":
        return [((B, D), (0.0, D)), ((0.0, D), (0.0, 0.0)),
                ((0.0, 0.0), (B, 0.0))]
    if shape == "I":
        return [((-h, 0.0), (0.0, 0.0)), ((0.0, 0.0), (h, 0.0)),
                ((0.0, 0.0), (0.0, D)),
                ((-h, D), (0.0, D)), ((0.0, D), (h, D))]
    if shape == "T":
        return [((0.0, 0.0), (0.0, D)), ((-h, D), (0.0, D)),
                ((0.0, D), (h, D))]
    if shape == "box":
        return [((0.0, 0.0), (B, 0.0)), ((B, 0.0), (B, D)),
                ((B, D), (0.0, D)), ((0.0, D), (0.0, 0.0))]
    raise ValueError(f"wall_stack: shape must be one of {WALL_SHAPES}")


def _next_pier(d):
    used = {s.get("pier") for s in d.get("shells") or []}
    k = 1
    while f"P{k}" in used:
        k += 1
    return f"P{k}"


def op_wall_stack(ctx, sel, params):
    d = ctx.d
    shape = params.get("shape", "C")
    if shape not in WALL_SHAPES:
        raise ValueError(f"wall_stack: shape must be one of {WALL_SHAPES}")
    B = _ed._num(params, "B", positive=True)
    if shape == "straight":
        D = _ed._num(params, "D", 0.0, lo=0.0)
    else:
        D = _ed._num(params, "D", positive=True)
    org = params.get("origin", [0.0, 0.0])
    if not isinstance(org, list) or len(org) < 2:
        raise ValueError("wall_stack: 'origin' must be [x, y]")
    ox, oy = float(org[0]), float(org[1])
    rot = math.radians(_ed._num(params, "rotation", 0.0))
    ca, sa = math.cos(rot), math.sin(rot)
    names = params.get("stories")
    if not isinstance(names, list) or not names:
        raise ValueError("wall_stack: 'stories' must list story names")
    stories = [_story(d, n) for n in names]
    section = params.get("section")
    if section is None:
        secs = list(d.get("shell_sections") or {})
        section = next((n for n in secs if "WALL" in n.upper()),
                       secs[0] if secs else None)
    if section not in (d.get("shell_sections") or {}):
        raise ValueError(f"wall_stack: unknown wall section {section!r}")
    pier = params.get("pier") or _next_pier(d)
    if not isinstance(pier, str) or not pier.strip():
        raise ValueError("wall_stack: 'pier' must be a label")
    mesh = float(params.get("mesh_size", 1.0))

    def G(p):
        return [ox + p[0] * ca - p[1] * sa, oy + p[0] * sa + p[1] * ca]

    made = []
    for st in stories:
        zt = float(st["elevation"])
        zb = zt - float(st["height"])
        for a, b in wall_stack_legs(shape, B, D):
            ga, gb = G(a), G(b)
            corners = [_ed._clean([ga[0], ga[1], zb]),
                       _ed._clean([gb[0], gb[1], zb]),
                       _ed._clean([gb[0], gb[1], zt]),
                       _ed._clean([ga[0], ga[1], zt])]
            uid = ctx.new_uid("W")
            d["shells"].append({"uid": uid, "kind": "wall",
                                "behavior": "shell", "section": section,
                                "corners": corners, "mesh_size": mesh,
                                "story": st["name"], "openings": [],
                                "pier": pier.strip(), "area_spring": None,
                                "wind_cp": None})
            ctx.created["shells"].append(uid)
            made.append(uid)
    ns = _ed._new_sel()
    ns["shells"] = made
    return {"pier": pier.strip(), "walls": len(made), "new_selection": ns}


# --------------------------------------------------------------------------- #
# auto relabel
# --------------------------------------------------------------------------- #
RELABEL_KINDS = ("column", "beam", "brace", "wall", "slab", "link")
_PREFIX = {"column": "C", "beam": "B", "brace": "D", "wall": "W",
           "slab": "F", "link": "L"}


def _plan_key(pts, kind):
    if kind == "column":
        p = pts[0]
        return (round(p[1], 4), round(p[0], 4))
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    return (round(cy, 4), round(cx, 4))


def relabel_map(d: dict, kinds=RELABEL_KINDS) -> Dict[Tuple[str, str], str]:
    """``{(collection, old uid): new uid}`` (unchanged uids omitted)."""
    groups: Dict[str, List[tuple]] = {k: [] for k in RELABEL_KINDS}
    story_z = [(float(s["elevation"]) - float(s["height"]),
                float(s["elevation"]), s["name"]) for s in
               d.get("stories") or []]

    def story_of_z(z):
        for zb, zt, n in story_z:
            if zb + 1e-6 < z <= zt + 1e-6:
                return n
        return ""
    for m in d.get("members") or []:
        k = m.get("kind")
        if k in kinds and k in groups:
            groups[k].append(("members", m["uid"], [m["pi"], m["pj"]],
                              m.get("story") or ""))
    for s in d.get("shells") or []:
        k = s.get("kind")
        if k in kinds and k in groups:
            groups[k].append(("shells", s["uid"], s["corners"],
                              s.get("story") or ""))
    if "link" in kinds:
        for lk in d.get("links") or []:
            groups["link"].append(("links", lk["uid"], [lk["pi"], lk["pj"]],
                                   story_of_z(max(lk["pi"][2],
                                                  lk["pj"][2]))))
    out = {}
    taken = set()
    for kind in RELABEL_KINDS:
        objs = groups[kind]
        keys = sorted({_plan_key(o[2], kind) for o in objs})
        num = {k: i + 1 for i, k in enumerate(keys)}
        for coll, uid, pts, story in objs:
            label = f"{_PREFIX[kind]}{num[_plan_key(pts, kind)]}"
            new = f"{label}-{story}" if story else label
            base, n = new, 2
            while new in taken:
                new = f"{base}.{n}"
                n += 1
            taken.add(new)
            out[(coll, uid)] = new
    return {k: v for k, v in out.items() if k[1] != v}


def _rename_refs(d, kind, mapping: Dict[str, str]):
    if not mapping:
        return
    f = mapping.get
    key = "member_uid" if kind == "members" else "region_uid"
    for pat in (d.get("patterns") or {}).values():
        lists = (("member_loads", "member_udls", "thermal_loads")
                 if kind == "members" else
                 ("area_loads", "shell_thermal_loads") if kind == "shells"
                 else ())
        for k in lists:
            for ld in pat.get(k) or []:
                ld[key] = f(ld[key], ld[key])
    for g in (d.get("groups") or {}).values():
        if g.get(kind):
            g[kind] = [f(u, u) for u in g[kind]]
    if kind in ("members", "shells"):
        for td in d.get("tendons") or []:
            h = td.get("host")
            if isinstance(h, str):
                td["host"] = f(h, h)
            elif isinstance(h, list):
                td["host"] = [f(u, u) for u in h]
    if kind == "members":
        for coll in ("pushover_cases", "th_cases"):
            for c in (d.get(coll) or {}).values():
                my = c.get("My")
                if isinstance(my, dict) and my:
                    c["My"] = {f(u, u): v for u, v in my.items()}


def op_relabel(ctx, sel, params):
    kinds = params.get("kinds") or list(RELABEL_KINDS)
    if not isinstance(kinds, list) or any(k not in RELABEL_KINDS
                                          for k in kinds):
        raise ValueError(f"relabel: kinds must be a subset of "
                         f"{RELABEL_KINDS}")
    d = ctx.d
    mp = relabel_map(d, kinds)
    # the final uid set must stay unique against objects NOT relabelled
    final = {}
    for coll in ("members", "shells", "links"):
        for o in d.get(coll) or []:
            u = mp.get((coll, o["uid"]), o["uid"])
            if u in final:
                raise ValueError(f"relabel: {u!r} would not be unique; "
                                 "include every kind")
            final[u] = coll
    by_kind = {"members": {}, "shells": {}, "links": {}}
    for (coll, old), new in mp.items():
        by_kind[coll][old] = new
    for coll, mapping in by_kind.items():
        for o in d.get(coll) or []:
            if o["uid"] in mapping:
                o["uid"] = mapping[o["uid"]]
        _rename_refs(d, coll, mapping)
    ns = _ed._new_sel()
    return {"renamed": {f"{c}:{o}": n for (c, o), n in mp.items()},
            "count": len(mp), "new_selection": ns}


OPS = {"shell_divide": op_shell_divide, "shell_merge": op_shell_merge,
       "shell_chop": op_shell_chop, "shell_expand": op_shell_expand,
       "shell_reshape": op_shell_reshape, "ramp": op_ramp,
       "wall_stack": op_wall_stack, "relabel": op_relabel}
