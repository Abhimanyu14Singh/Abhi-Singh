"""Structural irregularity / regularity checks (Wave B): ASCE 7 Table
12.3-1 / 12.3-2, EN 1998-1 §4.2.3 and IS 1893 (Part 1):2016 Tables 5 / 6.

CONTRACT "Irregularity checks, grouped ASCE 7 combinations, E2K releases /
joint axes, joint-local reactions, RS shell forces in combos, TS 498 / KDS /
CFE wind, NTC long-period distribution", section "Irregularity checks".

Pure post-processing of the model (+ an optional solved results dict for
the response-based items).  No solve, no model field: the engine results
JSON stays byte-identical.  Every ratio goes through the significance gate
of :mod:`skyframe.core.ratio_significance`: a ratio whose denominator is
negligible is ``None`` with a ``na_reason`` instead of a random number.

Geometric quantities per story (bottom -> top)
----------------------------------------------
* **Vertical elements** of a story: columns, braces and walls whose top is
  at the story elevation (members also by their ``story`` label).
* **SFRS plan dimension** ``Dx`` / ``Dy``: the plan extent of those
  elements.
* **Floor**: the slabs at the story elevation (gross polygons minus their
  openings).  Outline / notch quantities come from a raster of
  ``raster`` x ``raster`` cells aligned with the floor bounding box (exact
  for rectangular plans); the net floor area is exact (``net_area``).
* **Story mass**: ``model.compute_story_masses()`` (the mass source), t.
* **Story shear strength** ``Vn`` (kN) per direction: the ``story_strength``
  option ``{story: {"x": kN, "y": kN}}`` when given, else an ESTIMATE from
  the vertical elements (nominal shear strength without reinforcement:
  concrete ``0.17 sqrt(f'c) 0.8 A`` (columns) / ``0.17 sqrt(f'c) t 0.8 L``
  (walls, in their plane), steel ``0.6 Fy Av`` (``Av`` = min(As2, As3), else
  A/2), braces ``Fy A`` horizontal component); ``None`` when a material
  strength (``fc`` / ``fy``) is not set.

Discontinuities
---------------
A column / wall of story ``i > 0`` whose bottom point is not on a column
top or a wall top of story ``i - 1`` (a support level counts as supported)
is *discontinuous*.  ``kind`` = ``in_plane`` (a coplanar wall below / a
column below on the same frame line X or Y), ``out_of_plane`` (a parallel
wall below off its plane) or ``unsupported`` (nothing below);
``offset`` = horizontal distance to the nearest support candidate (m) and
``ratio`` = offset / wall length (walls).

Checks (``status`` = ``irregular`` | ``extreme`` | ``ok`` | ``n/a``)
--------------------------------------------------------------------
Response-based rows (soft story, torsion, stiffness) use the engine's
``irregularity`` / ``story_stiffness`` blocks of the LATERAL static cases
only (:func:`lateral_cases`: every pattern ``quake`` / ``wind``).

ASCE 7-16/22 (Table 12.3-2 vertical, Table 12.3-1 plan):

* V-1a/1b soft story: the engine's ``irregularity`` block (results).
* V-2 weight (mass): ``m_i / m_adj > 1.5`` (a roof lighter than the floor
  below is not considered).
* V-3 vertical geometric: ``D_i / D_adj > 1.3`` (X and Y).
* V-4 in-plane discontinuity: an in-plane offset of a vertical element.
* V-5a/5b weak story: ``Vn_i / Vn_above < 0.80`` (``< 0.65`` extreme).
* P-1a/1b torsional: the engine's flags (results).
* P-2 re-entrant corner: both notch projections ``> 0.15`` of the plan
  dimension; P-3 diaphragm discontinuity: openings ``> 50 %`` of the area
  enclosed by the floor outline; P-4 out-of-plane offset; P-5 nonparallel
  system (a wall / brace not parallel to X or Y within 1 deg).

EN 1998-1 §4.2.3:

* 4.2.3.2(4) compactness: recess area (convex hull - outline) ``<= 5 %``
  of the floor area; (5) slenderness ``Lmax / Lmin <= 4``; (6) torsional
  radius: not evaluated (needs the storey torsional stiffness) -> ``n/a``.
* 4.2.3.3(2) all lateral systems continuous to the top: no
  discontinuity; (3) stiffness and mass "constant or reducing gradually":
  the ratio upper/lower of mass and stiffness must stay within ``[1 -
  ec8_tol, 1 + ec8_tol]`` (``ec8_tol`` default 0.25 is a judgement value,
  EC8 gives no number; the roof mass decrease is not considered); (5)
  setbacks (Fig. 4.1): symmetric each ``<= 0.20 L_i`` (``<= 0.50 L1`` for a
  single setback in the lower 15 % of the height), unsymmetric each ``<=
  0.10 L_i`` and the sum ``<= 0.30 L1``.

IS 1893 (Part 1):2016:

* Table 5 (i) torsional: ``Dmax / Dmin > 1.5`` (from the engine ratio
  ``r = Dmax/Davg``: ``Dmax/Dmin = r/(2 - r)``; ``> 2.0`` reported as
  ``extreme``, the configuration must be revised) or the fundamental
  torsional period longer than a translational one (modal);
  (ii) re-entrant corners ``> 15 %``; (iii) openings ``> 50 %``;
  (iv) out-of-plane offsets; (v) non-parallel systems.
* Table 6 (i) soft storey ``k_i < k_above``; (ii) mass ``W_i > 1.5
  W_below``; (iii) vertical geometric ``D_i > 1.25 D_adj``; (iv) in-plane
  discontinuity ``offset > 0.20`` of the element length (columns: any);
  (v) weak storey ``Vn_i < Vn_above``; (vi) floating / stub columns (a
  discontinuous column); (vii) irregular modes: the first three modes
  below 65 % mass in a principal direction, or the fundamental X and Y
  periods within 10 % of the larger.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import ratio_significance as _rsig

DEFAULTS = {"ec8_tol": 0.25, "raster": 200, "story_strength": None}
TOL = 1e-3                       # m, geometric coincidence
REL_GATE = 1e-9                  # x the largest value: "zero" denominator
NA_ZERO = "not applicable (zero denominator)"
NA_STRENGTH = "not applicable (material strength fc / fy not set)"
NA_NO_FLOOR = "not applicable (no floor slabs at this story)"
NA_RESULTS = "not applicable (needs analysis results)"
CODES = ("ASCE 7", "EN 1998-1", "IS 1893")

ROW_KEYS = ("code", "item", "check", "story", "case", "direction", "value",
            "limit", "status", "note")


# --------------------------------------------------------------------- utils
def _ratio(num: float, den: float, scale: float) -> Optional[float]:
    if den is None or num is None:
        return None
    if abs(den) <= max(REL_GATE * abs(scale), 1e-300):
        return None
    return float(num) / float(den)


def _row(rows, code, item, check, story="", case="", direction="",
         value=None, limit="", status="ok", note=""):
    rows.append({"code": code, "item": item, "check": check,
                 "story": story, "case": case, "direction": direction,
                 "value": value, "limit": limit, "status": status,
                 "note": note})


def _poly_area(pts: Sequence[Sequence[float]]) -> float:
    a = 0.0
    n = len(pts)
    for k in range(n):
        x1, y1 = pts[k][0], pts[k][1]
        x2, y2 = pts[(k + 1) % n][0], pts[(k + 1) % n][1]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def _hull(pts: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    p = sorted(set((round(x, 9), round(y, 9)) for x, y in pts))
    if len(p) <= 2:
        return p

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo: List[Tuple[float, float]] = []
    for q in p:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], q) <= 0:
            lo.pop()
        lo.append(q)
    up: List[Tuple[float, float]] = []
    for q in reversed(p):
        while len(up) >= 2 and cross(up[-2], up[-1], q) <= 0:
            up.pop()
        up.append(q)
    return lo[:-1] + up[:-1]


def _inside(X: np.ndarray, Y: np.ndarray, poly) -> np.ndarray:
    """Even-odd point-in-polygon on grids X, Y."""
    res = np.zeros(X.shape, dtype=bool)
    n = len(poly)
    for k in range(n):
        x1, y1 = poly[k][0], poly[k][1]
        x2, y2 = poly[(k + 1) % n][0], poly[(k + 1) % n][1]
        if y1 == y2:
            continue
        cond = (Y >= min(y1, y2)) & (Y < max(y1, y2))
        xc = x1 + (Y - y1) * (x2 - x1) / (y2 - y1)
        res ^= cond & (X < xc)
    return res


# ------------------------------------------------------------------ geometry
def _span(m) -> Tuple[float, float]:
    return (min(m.pi[2], m.pj[2]), max(m.pi[2], m.pj[2]))


def _story_index(model, z: float) -> Optional[int]:
    for i, s in enumerate(model.stories):
        if abs(s.elevation - z) < TOL:
            return i
    return None


def _vertical_elements(model):
    """{story index: {"cols": [...], "braces": [...], "walls": [...]}}."""
    out = {i: {"cols": [], "braces": [], "walls": []}
           for i in range(len(model.stories))}
    names = {s.name: i for i, s in enumerate(model.stories)}
    for m in model.members:
        if m.kind not in ("column", "brace"):
            continue
        zb, zt = _span(m)
        if zt - zb < TOL:
            continue                                 # a horizontal "brace"
        i = _story_index(model, zt)
        if i is None:
            i = names.get(m.story)
        if i is None:
            continue
        out[i]["cols" if m.kind == "column" else "braces"].append(m)
    for r in model.shells:
        if r.kind != "wall":
            continue
        zt = max(c[2] for c in r.corners)
        i = _story_index(model, zt)
        if i is None:
            i = names.get(r.story)
        if i is None:
            continue
        out[i]["walls"].append(r)
    return out


def _wall_line(r):
    """(p0, p1, z_bottom, z_top, length) of a vertical wall's plan line."""
    xy = [(c[0], c[1]) for c in r.corners]
    best = (0.0, xy[0], xy[0])
    for a in xy:
        for b in xy:
            d = math.hypot(b[0] - a[0], b[1] - a[1])
            if d > best[0]:
                best = (d, a, b)
    zs = [c[2] for c in r.corners]
    return best[1], best[2], min(zs), max(zs), best[0]


def _sfrs_dims(el) -> Optional[Tuple[float, float, float, float]]:
    pts = []
    for m in el["cols"] + el["braces"]:
        pts += [(m.pi[0], m.pi[1]), (m.pj[0], m.pj[1])]
    for r in el["walls"]:
        pts += [(c[0], c[1]) for c in r.corners]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (max(xs) - min(xs), max(ys) - min(ys),
            (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0)


def _floor(model, z: float, n: int) -> Optional[dict]:
    """Floor outline metrics of the slabs at elevation ``z``."""
    slabs = [r for r in model.shells if r.kind == "slab"
             and all(abs(c[2] - z) < TOL for c in r.corners)]
    if not slabs:
        return None
    polys, holes = [], []
    for r in slabs:
        polys.append([(c[0], c[1]) for c in r.corners])
        for op in r.openings:
            if getattr(op, "polygon", None):
                holes.append([(p[0], p[1]) for p in op.polygon])
            else:
                holes.append([r.map_uv(op.u0, op.v0)[:2],
                              r.map_uv(op.u1, op.v0)[:2],
                              r.map_uv(op.u1, op.v1)[:2],
                              r.map_uv(op.u0, op.v1)[:2]])
    allp = [p for pl in polys for p in pl]
    x0, x1 = min(p[0] for p in allp), max(p[0] for p in allp)
    y0, y1 = min(p[1] for p in allp), max(p[1] for p in allp)
    Lx, Ly = x1 - x0, y1 - y0
    if Lx <= TOL or Ly <= TOL:
        return None
    nx = max(4, int(round(n * Lx / max(Lx, Ly))))
    ny = max(4, int(round(n * Ly / max(Lx, Ly))))
    dx, dy = Lx / nx, Ly / ny
    X, Y = np.meshgrid(x0 + (np.arange(nx) + 0.5) * dx,
                       y0 + (np.arange(ny) + 0.5) * dy)
    solid = np.zeros(X.shape, dtype=bool)
    for pl in polys:
        solid |= _inside(X, Y, pl)
    for h in holes:
        solid &= ~_inside(X, Y, h)
    # outline fill: flood the empty cells reachable from the border
    outside = np.zeros(X.shape, dtype=bool)
    stack = [(j, i) for j in range(ny) for i in (0, nx - 1)
             if not solid[j, i]]
    stack += [(j, i) for i in range(nx) for j in (0, ny - 1)
              if not solid[j, i]]
    while stack:
        j, i = stack.pop()
        if outside[j, i] or solid[j, i]:
            continue
        outside[j, i] = True
        for jj, ii in ((j + 1, i), (j - 1, i), (j, i + 1), (j, i - 1)):
            if 0 <= jj < ny and 0 <= ii < nx and not outside[jj, ii] \
                    and not solid[jj, ii]:
                stack.append((jj, ii))
    filled = ~outside

    def max_gap(mask_rows) -> int:
        """Longest run of empty cells (edge or interior) along a row."""
        best = 0
        for row in mask_rows:
            run = 0
            for v in row:
                run = 0 if v else run + 1
                best = max(best, run)
        return best
    notch_x = max_gap(filled) * dx           # along X, over the rows
    notch_y = max_gap(filled.T) * dy         # along Y, over the columns
    a_outline = float(filled.sum()) * dx * dy
    a_net = sum(r.net_area for r in slabs)
    hull = _hull(allp)
    return {"Lx": Lx, "Ly": Ly, "cx": (x0 + x1) / 2.0,
            "cy": (y0 + y1) / 2.0, "area_net": a_net,
            "area_outline": a_outline, "area_hull": _poly_area(hull),
            "notch_x": notch_x, "notch_y": notch_y}


# ------------------------------------------------------------- strength
def _material(model, name):
    return model.materials.get(name)


def _strength(model, el, override) -> Tuple[Optional[float], Optional[float],
                                             str]:
    """(Vx, Vy, source) of one story."""
    if override is not None:
        return (float(override.get("x")) if override.get("x") is not None
                else None,
                float(override.get("y")) if override.get("y") is not None
                else None, "user")
    vx = vy = 0.0
    for m in el["cols"] + el["braces"]:
        sec = model.sections.get(m.section)
        mat = _material(model, sec.material) if sec is not None else None
        if sec is None or mat is None:
            return None, None, "estimated"
        steel = mat.material_type in ("steel", "coldformed", "aluminum")
        if m.kind == "column":
            if steel:
                if mat.fy is None:
                    return None, None, "estimated"
                avs = [a for a in (sec.As2, sec.As3) if a]
                av = min(avs) if avs else 0.5 * sec.A
                v = 0.6 * mat.fy * av
            else:
                if mat.fc is None:
                    return None, None, "estimated"
                v = 0.17 * math.sqrt(mat.fc / 1000.0) * 0.8 * sec.A * 1000.0
            vx += v
            vy += v
        else:                                         # brace
            f = mat.fy if steel else (0.85 * mat.fc if mat.fc else None)
            if f is None:
                return None, None, "estimated"
            d = [m.pj[k] - m.pi[k] for k in range(3)]
            L = math.sqrt(sum(c * c for c in d))
            if L <= 0.0:
                continue
            vx += f * sec.A * abs(d[0]) / L
            vy += f * sec.A * abs(d[1]) / L
    for r in el["walls"]:
        ss = model.shell_sections.get(r.section)
        mat = _material(model, ss.material) if ss is not None else None
        if ss is None or mat is None or mat.fc is None:
            return None, None, "estimated"
        p0, p1, _zb, _zt, L = _wall_line(r)
        if L <= 0.0:
            continue
        v = 0.17 * math.sqrt(mat.fc / 1000.0) * ss.thickness * 0.8 * L \
            * 1000.0
        vx += v * abs(p1[0] - p0[0]) / L
        vy += v * abs(p1[1] - p0[1]) / L
    return vx, vy, "estimated"


# ------------------------------------------------------- discontinuities
def _on_wall_top(pt, z, walls) -> Tuple[bool, Optional[float], str]:
    """(supported, offset to the nearest coplanar/parallel wall, relation)"""
    best = (None, "")
    for w in walls:
        p0, p1, _zb, zt, L = _wall_line(w)
        if abs(zt - z) > TOL or L <= 0.0:
            continue
        ux, uy = (p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L
        rx, ry = pt[0] - p0[0], pt[1] - p0[1]
        t = rx * ux + ry * uy
        dperp = abs(-rx * uy + ry * ux)
        if dperp < TOL and -TOL <= t <= L + TOL:
            return True, 0.0, "on"
        along = max(0.0, -t, t - L)
        rel = "coplanar" if dperp < TOL else "parallel"
        dist = math.hypot(along, dperp)
        if best[0] is None or dist < best[0]:
            best = (dist, rel)
    return False, best[0], best[1]


def discontinuities(model) -> List[dict]:
    """Discontinuous columns / walls (see the module docstring)."""
    els = _vertical_elements(model)
    out: List[dict] = []
    for i in range(1, len(model.stories)):
        below = els[i - 1]
        tops = [(m, max((m.pi, m.pj), key=lambda p: p[2]))
                for m in below["cols"]]
        sup_pts = [tuple(s.point) for s in model.supports]
        for m in els[i]["cols"]:
            bot = min((m.pi, m.pj), key=lambda p: p[2])
            z = bot[2]
            if any(abs(t[0] - bot[0]) < TOL and abs(t[1] - bot[1]) < TOL
                   and abs(t[2] - z) < TOL for _m, t in tops):
                continue
            if any(abs(p[0] - bot[0]) < TOL and abs(p[1] - bot[1]) < TOL
                   and abs(p[2] - z) < TOL for p in sup_pts):
                continue
            ok, _off, _rel = _on_wall_top(bot, z, below["walls"])
            if ok:
                continue
            cand = [t for _m, t in tops if abs(t[2] - z) < TOL]
            if cand:
                near = min(cand, key=lambda t: math.hypot(t[0] - bot[0],
                                                          t[1] - bot[1]))
                off = math.hypot(near[0] - bot[0], near[1] - bot[1])
                line = (abs(near[0] - bot[0]) < TOL
                        or abs(near[1] - bot[1]) < TOL)
                kind = "in_plane" if line else "out_of_plane"
            else:
                off, kind = None, "unsupported"
            out.append({"story": model.stories[i].name, "element": m.uid,
                        "type": "column", "kind": kind, "offset": off,
                        "ratio": None, "point": list(bot)})
        for r in els[i]["walls"]:
            p0, p1, zb, _zt, L = _wall_line(r)
            worst = None
            for pt in (p0, p1):
                ok, off, rel = _on_wall_top(pt, zb, below["walls"])
                if ok:
                    continue
                if any(abs(t[0] - pt[0]) < TOL and abs(t[1] - pt[1]) < TOL
                       and abs(t[2] - zb) < TOL for _m, t in tops):
                    continue
                if any(abs(p[0] - pt[0]) < TOL and abs(p[1] - pt[1]) < TOL
                       and abs(p[2] - zb) < TOL for p in sup_pts):
                    continue
                kind = {"coplanar": "in_plane",
                        "parallel": "out_of_plane"}.get(rel, "unsupported")
                if worst is None or (off or 0.0) > (worst[0] or 0.0):
                    worst = (off, kind)
            if worst is not None:
                out.append({"story": model.stories[i].name,
                            "element": r.uid, "type": "wall",
                            "kind": worst[1], "offset": worst[0],
                            "ratio": (worst[0] / L if worst[0] is not None
                                      and L > 0 else None),
                            "point": None})
    return out


# ------------------------------------------------------------- the report
def story_data(model, options: Optional[dict] = None) -> List[dict]:
    """Per-story geometric / mass / strength quantities (bottom -> top)."""
    opt = dict(DEFAULTS, **(options or {}))
    els = _vertical_elements(model)
    masses = model.compute_story_masses() if model.stories else {}
    over = opt.get("story_strength") or {}
    rows = []
    for i, s in enumerate(model.stories):
        dims = _sfrs_dims(els[i])
        fl = _floor(model, s.elevation, int(opt["raster"]))
        vx, vy, src = _strength(model, els[i], over.get(s.name))
        rows.append({
            "story": s.name, "elevation": s.elevation, "height": s.height,
            "mass": float(masses.get(s.name, 0.0)),
            "Dx": dims[0] if dims else None, "Dy": dims[1] if dims else None,
            "cx": dims[2] if dims else None, "cy": dims[3] if dims else None,
            "n_columns": len(els[i]["cols"]), "n_braces": len(els[i]["braces"]),
            "n_walls": len(els[i]["walls"]),
            "Vn_x": vx, "Vn_y": vy, "strength_source": src,
            "floor": fl})
    return rows


def _nonparallel(model) -> List[str]:
    out = []
    for m in model.members:
        if m.kind != "brace":
            continue
        dx, dy = m.pj[0] - m.pi[0], m.pj[1] - m.pi[1]
        if math.hypot(dx, dy) < TOL:
            continue
        a = math.degrees(math.atan2(dy, dx)) % 90.0
        if min(a, 90.0 - a) > 1.0:
            out.append(m.uid)
    for r in model.shells:
        if r.kind != "wall":
            continue
        p0, p1, _a, _b, L = _wall_line(r)
        if L < TOL:
            continue
        a = math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0])) % 90.0
        if min(a, 90.0 - a) > 1.0:
            out.append(r.uid)
    return out


def compute(model, results: Optional[dict] = None,
            options: Optional[dict] = None) -> dict:
    """Full report ``{"stories", "discontinuities", "checks", "summary",
    "options", "warnings"}`` (see the module docstring)."""
    opt = dict(DEFAULTS, **(options or {}))
    tol = float(opt["ec8_tol"])
    if not (0.0 < tol < 1.0):
        raise ValueError("ec8_tol must be in (0, 1)")
    if int(opt["raster"]) < 10:
        raise ValueError("raster must be >= 10")
    sd = story_data(model, opt)
    disc = discontinuities(model)
    rows: List[dict] = []
    warns: List[str] = []
    n = len(sd)
    names = [r["story"] for r in sd]
    mmax = max([r["mass"] for r in sd] or [0.0])
    A, E8, IS = CODES

    # ---------------- mass (ASCE V-2, IS 6 (ii), EC8 (3) mass)
    for i, r in enumerate(sd):
        m = r["mass"]
        below = sd[i - 1]["mass"] if i > 0 else None
        above = sd[i + 1]["mass"] if i + 1 < n else None
        roof_above = (i + 1 == n - 1)
        cands = []
        if below is not None:
            cands.append(("below", _ratio(m, below, mmax)))
        if above is not None and not (roof_above and above < m):
            cands.append(("above", _ratio(m, above, mmax)))
        vals = [v for _k, v in cands if v is not None]
        if not cands:
            continue
        if not vals:
            _row(rows, A, "Table 12.3-2 Type 2", "Weight (mass)", r["story"],
                 value=None, limit="> 1.5 adjacent", status="n/a",
                 note=NA_ZERO)
        else:
            v = max(vals)
            _row(rows, A, "Table 12.3-2 Type 2", "Weight (mass)", r["story"],
                 value=v, limit="> 1.5 adjacent",
                 status="irregular" if v > 1.5 else "ok")
        if below is not None:
            rb = _ratio(m, below, mmax)
            _row(rows, IS, "Table 6 (ii)", "Mass irregularity", r["story"],
                 value=rb, limit="> 1.5 below",
                 status=("n/a" if rb is None else
                         "irregular" if rb > 1.5 else "ok"),
                 note=NA_ZERO if rb is None else "")
            is_roof = (i == n - 1)
            if rb is not None:
                bad = rb > 1.0 + tol or (rb < 1.0 - tol and not is_roof)
            _row(rows, E8, "4.2.3.3(3)", "Mass gradual (upper/lower)",
                 r["story"], value=rb,
                 limit=f"within [{1 - tol:g}, {1 + tol:g}]",
                 status=("n/a" if rb is None else
                         "irregular" if bad else "ok"),
                 note=NA_ZERO if rb is None else
                 ("roof decrease not considered" if is_roof else ""))

    # ---------------- vertical geometric (ASCE V-3, IS 6 (iii))
    for d in ("x", "y"):
        key = "D" + d
        dmax = max([r[key] or 0.0 for r in sd] or [0.0])
        for i, r in enumerate(sd):
            if r[key] is None:
                continue
            adj = [sd[k][key] for k in (i - 1, i + 1)
                   if 0 <= k < n and sd[k][key] is not None]
            if not adj:
                continue
            vals = [_ratio(r[key], a, dmax) for a in adj]
            vals = [v for v in vals if v is not None]
            v = max(vals) if vals else None
            for code, item, lim in ((A, "Table 12.3-2 Type 3", 1.3),
                                    (IS, "Table 6 (iii)", 1.25)):
                _row(rows, code, item, "Vertical geometric", r["story"],
                     direction=d.upper(), value=v,
                     limit=f"> {lim:g} adjacent",
                     status=("n/a" if v is None else
                             "irregular" if v > lim else "ok"),
                     note=NA_ZERO if v is None else "")

    # ---------------- discontinuities
    by_story: Dict[str, List[dict]] = {}
    for dd in disc:
        by_story.setdefault(dd["story"], []).append(dd)
    for i, r in enumerate(sd):
        if i == 0:
            continue
        lst = by_story.get(r["story"], [])
        inpl = [x for x in lst if x["kind"] == "in_plane"]
        outp = [x for x in lst if x["kind"] == "out_of_plane"]
        cols = [x for x in lst if x["type"] == "column"]
        _row(rows, A, "Table 12.3-2 Type 4", "In-plane discontinuity",
             r["story"], value=len(inpl), limit="any in-plane offset",
             status="irregular" if inpl else "ok",
             note=", ".join(x["element"] for x in inpl))
        _row(rows, A, "Table 12.3-1 Type 4", "Out-of-plane offset",
             r["story"], value=len(outp), limit="any",
             status="irregular" if outp else "ok",
             note=", ".join(x["element"] for x in outp))
        worst = None
        for x in inpl:
            v = 1.0 if x["type"] == "column" else (x["ratio"] or 0.0)
            worst = v if worst is None else max(worst, v)
        _row(rows, IS, "Table 6 (iv)", "In-plane discontinuity",
             r["story"], value=worst, limit="offset > 0.20 L",
             status="irregular" if (worst or 0.0) > 0.2 else "ok",
             note=", ".join(x["element"] for x in inpl))
        _row(rows, IS, "Table 5 (iv)", "Out-of-plane offsets", r["story"],
             value=len(outp), limit="any",
             status="irregular" if outp else "ok",
             note=", ".join(x["element"] for x in outp))
        _row(rows, IS, "Table 6 (vi)", "Floating or stub columns",
             r["story"], value=len(cols), limit="any",
             status="irregular" if cols else "ok",
             note=", ".join(x["element"] for x in cols))
        _row(rows, E8, "4.2.3.3(2)", "Lateral systems continuous",
             r["story"], value=len(lst), limit="no interruption",
             status="irregular" if lst else "ok",
             note=", ".join(x["element"] for x in lst))

    # ---------------- weak story (ASCE V-5, IS 6 (v))
    for d in ("x", "y"):
        key = "Vn_" + d
        vmax = max([r[key] or 0.0 for r in sd] or [0.0])
        for i, r in enumerate(sd[:-1]):
            up = sd[i + 1][key]
            here = r[key]
            if here is None or up is None:
                note = NA_STRENGTH
                v = None
            else:
                v = _ratio(here, up, vmax)
                note = "" if v is not None else NA_ZERO
            note2 = note or f"strength {r['strength_source']}"
            _row(rows, A, "Table 12.3-2 Type 5", "Weak story (strength)",
                 r["story"], direction=d.upper(), value=v,
                 limit="< 0.80 above (< 0.65 extreme)",
                 status=("n/a" if v is None else "extreme" if v < 0.65
                         else "irregular" if v < 0.80 else "ok"),
                 note=note2)
            _row(rows, IS, "Table 6 (v)", "Weak storey", r["story"],
                 direction=d.upper(), value=v, limit="< 1.0 above",
                 status=("n/a" if v is None else
                         "irregular" if v < 1.0 else "ok"), note=note2)

    # ---------------- floor plan checks
    for r in sd:
        fl = r["floor"]
        if fl is None:
            for code, item, chk in (
                    (A, "Table 12.3-1 Type 2", "Re-entrant corner"),
                    (A, "Table 12.3-1 Type 3", "Diaphragm discontinuity"),
                    (IS, "Table 5 (ii)", "Re-entrant corners"),
                    (IS, "Table 5 (iii)", "Floor openings"),
                    (E8, "4.2.3.2(4)", "Compactness")):
                _row(rows, code, item, chk, r["story"], status="n/a",
                     note=NA_NO_FLOOR)
            if r["Dx"] and r["Dy"]:                  # plan = SFRS extents
                lam = max(r["Dx"], r["Dy"]) / min(r["Dx"], r["Dy"])
                _row(rows, E8, "4.2.3.2(5)", "Slenderness Lmax/Lmin",
                     r["story"], value=lam, limit="<= 4",
                     status="irregular" if lam > 4.0 else "ok",
                     note="plan from the vertical-element extents")
            else:
                _row(rows, E8, "4.2.3.2(5)", "Slenderness Lmax/Lmin",
                     r["story"], status="n/a", note=NA_NO_FLOOR)
            continue
        px, py = fl["notch_x"] / fl["Lx"], fl["notch_y"] / fl["Ly"]
        both = min(px, py)
        for code, item in ((A, "Table 12.3-1 Type 2"),
                           (IS, "Table 5 (ii)")):
            _row(rows, code, item, "Re-entrant corner", r["story"],
                 value=both, limit="both projections > 0.15",
                 status="irregular" if px > 0.15 and py > 0.15 else "ok",
                 note=f"px/Lx = {px:.3f}, py/Ly = {py:.3f}")
        op = _ratio(max(fl["area_outline"] - fl["area_net"], 0.0),
                    fl["area_outline"], fl["area_outline"])
        for code, item in ((A, "Table 12.3-1 Type 3"),
                           (IS, "Table 5 (iii)")):
            _row(rows, code, item, "Openings / diaphragm discontinuity",
                 r["story"], value=op, limit="> 0.50 of the floor area",
                 status=("n/a" if op is None else
                         "irregular" if op > 0.5 else "ok"))
        rec = _ratio(max(fl["area_hull"] - fl["area_outline"], 0.0),
                     fl["area_outline"], fl["area_outline"])
        _row(rows, E8, "4.2.3.2(4)", "Compactness (recess / floor area)",
             r["story"], value=rec, limit="<= 0.05",
             status=("n/a" if rec is None else
                     "irregular" if rec > 0.05 else "ok"))
        lam = max(fl["Lx"], fl["Ly"]) / min(fl["Lx"], fl["Ly"])
        _row(rows, E8, "4.2.3.2(5)", "Slenderness Lmax/Lmin", r["story"],
             value=lam, limit="<= 4",
             status="irregular" if lam > 4.0 else "ok")
    _row(rows, E8, "4.2.3.2(6)", "Torsional radius r >= 3.33 e0, r >= ls",
         status="n/a",
         note="not evaluated (needs the storey torsional stiffness)")

    # ---------------- non-parallel systems
    npl = _nonparallel(model)
    for code, item in ((A, "Table 12.3-1 Type 5"), (IS, "Table 5 (v)")):
        _row(rows, code, item, "Nonparallel system", value=len(npl),
             limit="any element not parallel to X / Y",
             status="irregular" if npl else "ok", note=", ".join(npl))

    # ---------------- EC8 setbacks (Fig. 4.1)
    H = max([r["elevation"] for r in sd] or [0.0])
    for d, cd in (("x", "cx"), ("y", "cy")):
        key = "D" + d
        dims = [(r, r["floor"]["L" + d] if r["floor"] else r[key],
                 r["floor"]["c" + d] if r["floor"] else r[cd]) for r in sd]
        dims = [x for x in dims if x[1] is not None and x[1] > TOL]
        if len(dims) < 2:
            continue
        L1 = dims[0][1]
        worst_status, notes = "ok", []
        for (ra, La, ca), (rb, Lb, cb) in zip(dims, dims[1:]):
            if Lb >= La - TOL:
                continue
            step = (La - Lb) / La
            sym = abs(cb - ca) <= max(0.01 * La, TOL)
            low = ra["elevation"] <= 0.15 * H + TOL
            if sym:
                lim = 0.5 if low else 0.2
                bad = (La - Lb) / (L1 if low else La) > lim
            else:
                bad = step > 0.10
            if bad:
                worst_status = "irregular"
            notes.append(f"{rb['story']}: {step:.3f}"
                         f"{' sym' if sym else ' unsym'}")
        tot = (L1 - dims[-1][1]) / L1
        unsym_any = any(abs(b[2] - a[2]) > max(0.01 * a[1], TOL)
                        for a, b in zip(dims, dims[1:]) if b[1] < a[1] - TOL)
        if unsym_any and tot > 0.30:
            worst_status = "irregular"
        _row(rows, E8, "4.2.3.3(5)", "Setbacks", direction=d.upper(),
             value=tot, limit="sym <= 0.20 each (0.50 low); unsym <= 0.10 "
             "each, <= 0.30 total", status=worst_status,
             note="; ".join(notes))

    # ---------------- response-based items
    if results:
        _response_checks(model, results, rows, names, tol)
    else:
        for code, item, chk in (
                (A, "Table 12.3-2 Type 1", "Soft story"),
                (A, "Table 12.3-1 Type 1", "Torsional"),
                (E8, "4.2.3.3(3)", "Stiffness gradual"),
                (IS, "Table 5 (i)", "Torsional"),
                (IS, "Table 6 (i)", "Soft storey"),
                (IS, "Table 6 (vii)", "Irregular modes")):
            _row(rows, code, item, chk, status="n/a", note=NA_RESULTS)

    summary: Dict[str, Dict[str, str]] = {}
    rank = {"n/a": 0, "ok": 1, "irregular": 2, "extreme": 3}
    for r in rows:
        cur = summary.setdefault(r["code"], {}).get(r["item"])
        if cur is None or rank[r["status"]] > rank[cur]:
            summary[r["code"]][r["item"]] = r["status"]
    return {"stories": sd, "discontinuities": disc, "checks": rows,
            "summary": summary,
            "options": {k: opt[k] for k in ("ec8_tol", "raster")},
            "warnings": warns}


def lateral_cases(model) -> set:
    """Static cases whose every pattern is ``quake`` or ``wind``: the
    response-based checks use these (the engine's ``irregularity`` /
    ``story_stiffness`` blocks also diagnose gravity cases with a non-zero
    story shear, which are not meaningful for regularity)."""
    out = set()
    for name, case in model.cases.items():
        kinds = {getattr(model.patterns.get(p), "kind", None)
                 for p in case.patterns}
        if kinds and kinds <= {"quake", "wind"}:
            out.add(name)
    return out


def _response_checks(model, results, rows, names, tol) -> None:
    A, E8, IS = CODES
    lat = lateral_cases(model)
    irr = {c: v for c, v in (results.get("irregularity") or {}).items()
           if c in lat}
    stiff = {c: v for c, v in (results.get("story_stiffness") or {}).items()
             if c in lat}
    for case, by_story in irr.items():
        for s in names:
            e = by_story.get(s)
            if not e:
                continue
            sf = e.get("soft_flag", "")
            sr = e.get("stiff_ratio")
            na = (e.get(_rsig.NA_KEY) or {})
            _row(rows, A, "Table 12.3-2 Type 1", "Soft story", s, case,
                 value=sr, limit="< 0.70 above / < 0.80 avg3 (0.60/0.70)",
                 status=("n/a" if sr is None and "stiff_ratio" in na else
                         "extreme" if sf == "extreme_soft" else
                         "irregular" if sf == "soft" else "ok"),
                 note=na.get("stiff_ratio", ""))
            for d in ("x", "y"):
                tr = e.get(f"tors_ratio_{d}")
                if tr is None:
                    note = na.get(f"tors_ratio_{d}", "")
                    _row(rows, A, "Table 12.3-1 Type 1", "Torsional", s,
                         case, d.upper(), None, ">= 1.2 (1.4 extreme)",
                         "n/a", note)
                    _row(rows, IS, "Table 5 (i)", "Torsional Dmax/Dmin", s,
                         case, d.upper(), None, "> 1.5 (> 2.0 revise)",
                         "n/a", note)
                    continue
                _row(rows, A, "Table 12.3-1 Type 1", "Torsional", s, case,
                     d.upper(), tr, ">= 1.2 (1.4 extreme)",
                     "extreme" if tr >= 1.4 else
                     "irregular" if tr >= 1.2 else "ok")
                if tr >= 2.0 - 1e-12:
                    dm, note = None, "Dmin ~ 0 (one end does not move)"
                else:
                    dm, note = tr / (2.0 - tr), ""
                st = ("extreme" if dm is None or dm > 2.0 else
                      "irregular" if dm > 1.5 else "ok")
                _row(rows, IS, "Table 5 (i)", "Torsional Dmax/Dmin", s,
                     case, d.upper(), dm, "> 1.5 (> 2.0 revise)", st, note)
    for case, by_story in stiff.items():
        for d in ("x", "y"):
            kk = "k" + d
            kv = [(s, (by_story.get(s) or {}).get(kk)) for s in names]
            na_s = {s for s in names if kk in
                    ((by_story.get(s) or {}).get(_rsig.NA_KEY) or {})}
            kmax = max([abs(v or 0.0) for _s, v in kv] or [0.0])
            for i, (s, k) in enumerate(kv[:-1]):
                s2, k2 = kv[i + 1]
                if s in na_s or s2 in na_s or not k or not k2:
                    v, note = None, _rsig.NA_NEGLIGIBLE_DRIFT
                else:
                    v, note = _ratio(k, k2, kmax), ""
                _row(rows, IS, "Table 6 (i)", "Soft storey k/k_above", s,
                     case, d.upper(), v, "< 1.0",
                     "n/a" if v is None else
                     "irregular" if v < 1.0 else "ok", note)
                up = None if v is None else 1.0 / v
                _row(rows, E8, "4.2.3.3(3)",
                     "Stiffness gradual (upper/lower)", s2, case,
                     d.upper(), up, f"within [{1 - tol:g}, {1 + tol:g}]",
                     "n/a" if up is None else
                     "irregular" if (up > 1 + tol or up < 1 - tol)
                     else "ok", note)
    # modal (IS Table 5 (i)(b), Table 6 (vii))
    part = ((results.get("modal") or {}).get("participation") or [])
    if len(part) >= 2:
        def arg(k):
            return max(part, key=lambda p: p.get(k, 0.0))
        mx, my, mr = arg("ux"), arg("uy"), arg("rz")
        tx, ty, tr = mx.get("T"), my.get("T"), mr.get("T")
        first3 = part[:3]
        sx = sum(p.get("ux", 0.0) for p in first3)
        sy = sum(p.get("uy", 0.0) for p in first3)
        bad_a = sx < 0.65 or sy < 0.65
        close = (tx is not None and ty is not None and mx is not my
                 and abs(tx - ty) < 0.10 * max(tx, ty))
        _row(rows, IS, "Table 6 (vii)", "Irregular modes", value=min(sx, sy),
             limit="first 3 modes < 65 % mass; Tx, Ty within 10 %",
             status="irregular" if bad_a or close else "ok",
             note=f"sum UX {sx:.3f}, sum UY {sy:.3f}, Tx {tx:.4g} s, "
                  f"Ty {ty:.4g} s")
        tors = (mr is not mx and mr is not my and tr is not None
                and (tr > tx or tr > ty))
        _row(rows, IS, "Table 5 (i)(b)", "Torsional mode period",
             value=tr, limit="T_torsion > a translational T",
             status="irregular" if tors else "ok",
             note=f"T_rz {tr:.4g} s, Tx {tx:.4g} s, Ty {ty:.4g} s")


# ------------------------------------------------------------------ tables
COLS = [("code", "Code", "text"), ("item", "Item", "text"),
        ("check", "Check", "text"), ("story", "Story", "text"),
        ("case", "Output Case", "text"), ("direction", "Direction", "text"),
        ("value", "Value", "ratio"), ("limit", "Limit", "text"),
        ("status", "Status", "text"), ("note", "Note", "text")]


def _t_irregularity(c) -> List[dict]:
    rep = compute(c.model, c.R)
    out = []
    for r in rep["checks"]:
        if c.case_filter is not None and r["case"] and \
                r["case"] not in c.case_filter:
            continue
        out.append(dict(r))
    return out


def register() -> None:
    from . import tables as T
    T.register_table("irregularity_checks", "Irregularity Checks",
                     f"{T._G} > Structure Output > Other Output Items",
                     [T._col(k, lab, q) for k, lab, q in COLS],
                     _t_irregularity, after="story_stiffness")


__all__ = ["DEFAULTS", "compute", "story_data", "discontinuities",
           "lateral_cases", "register", "CODES", "ROW_KEYS"]
