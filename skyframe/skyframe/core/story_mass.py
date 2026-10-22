"""Where a story's derived mass sits in plan, for its rotational inertia.

ETABS keeps the mass of a rigid floor where it arises -- point loads at their
points, line loads along their members, area loads and slab self-weight over
their areas -- and the diaphragm's rotational inertia is the sum of m r^2
about its centre.  ``mass_points`` returns the same sources as
:meth:`BuildingModel.compute_story_masses` as weighted plan points whose
second moment about any point is exact for these shapes:

* point load: one point;
* uniform or linearly varying line load: Simpson's rule on 9 points along the
  loaded part (the weight is linear, r^2 quadratic: the product is cubic,
  which Simpson integrates exactly);
* area load / shell self-weight: each fan triangle of the region carries a
  third of its weight at each edge midpoint (exact for r^2 over a triangle),
  scaled to the net (opening-reduced) area.

Weights are in the model's force unit (kN); the engine divides by g.
"""

from __future__ import annotations

from typing import List, Tuple

Point = Tuple[float, float, float]          # x, y, weight


def _line(p, q, a: float, b: float, w1: float, w2: float, length: float) -> List[Point]:
    """Points for a load w1 (at fraction a) to w2 (at b) along p-q, per unit length."""
    n = 8
    span = (b - a) * length
    if span <= 0.0:
        return []
    out = []
    for k in range(n + 1):
        t = k / n
        c = 1.0 if k in (0, n) else (4.0 if k % 2 else 2.0)
        f = a + (b - a) * t
        w = w1 + (w2 - w1) * t
        out.append((p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f, w * span * c / (3.0 * n)))
    return out


def _area(corners, weight: float) -> List[Point]:
    """``weight`` spread evenly over a planar polygon (plan projection used for the moments)."""
    pts = [(float(c[0]), float(c[1])) for c in corners]
    tris = []
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1), (x2, y2) = pts[0], pts[i], pts[i + 1]
        a = 0.5 * abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0))
        tris.append((a, (x0, y0), (x1, y1), (x2, y2)))
    tot = sum(t[0] for t in tris)
    if tot <= 0.0:                               # vertical or degenerate: centroid only
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        return [(cx, cy, weight)]
    out = []
    for a, p0, p1, p2 in tris:
        share = weight * a / tot / 3.0
        for u, v in ((p0, p1), (p1, p2), (p2, p0)):
            out.append((0.5 * (u[0] + v[0]), 0.5 * (u[1] + v[1]), share))
    return out


def mass_points(model, story, self_mass=None, patterns=None) -> List[Point]:
    """Weighted plan points of the DERIVED mass of ``story`` (explicit story
    masses have no location and are not covered).  Mirrors
    ``compute_story_masses``: same patterns, factors and attribution."""
    from skyframe.core.model import _SelfWeightOnly, area_load_is_default, area_load_weight, _np_area
    from skyframe.core import thermal_ext as _thx
    inc_self = model.mass_option("self_mass") if self_mass is None else bool(self_mass)
    inc_pat = model.mass_option("patterns") if patterns is None else bool(patterns)
    member, shell = model._uid_maps()
    s = story
    out: List[Point] = []
    for pname, fac in model.effective_mass_source().items():
        pat = model.patterns.get(pname)
        if pat is None:
            continue
        if not inc_pat:
            pat = _SelfWeightOnly(pat)
        for udl in pat.member_udls:
            m = member(udl.member_uid)
            if m is not None and m.story == s.name and m.kind != "column":
                out += _line(m.pi, m.pj, 0.0, 1.0, fac * udl.w, fac * udl.w, m.length)
        for ml in pat.member_loads:
            if ml.direction != "gravity":
                continue
            m = member(ml.member_uid)
            if m is None or m.story != s.name or m.kind == "column":
                continue
            if ml.kind == "point":
                f = ml.a
                out.append((m.pi[0] + (m.pj[0] - m.pi[0]) * f, m.pi[1] + (m.pj[1] - m.pi[1]) * f, fac * ml.w))
            else:
                pf = _thx.projected_factor(m, ml)
                w2 = ml.w if ml.kind == "udl" else ml.w2
                out += _line(m.pi, m.pj, ml.a, ml.b, fac * ml.w * pf, fac * w2 * pf, m.length)
        for al in pat.area_loads:
            region = shell(al.region_uid)
            if region is None or not model._region_on_story(region, s):
                continue
            w = (fac * al.q * region.net_area if area_load_is_default(al)
                 else fac * area_load_weight(region, al))
            out += _area(region.corners, w)
        for nl in pat.nodal_loads:
            if abs(nl.point[2] - s.elevation) < 1e-6:
                out.append((nl.point[0], nl.point[1], fac * (-nl.fz)))
        swf = getattr(pat, "self_weight_factor", 0.0)
        if swf and inc_self:
            for m in model.members:
                if m.story != s.name or m.kind == "column":
                    continue
                sec = model.sections.get(m.section)
                mat = model.materials.get(sec.material) if sec else None
                if sec is not None and mat is not None:
                    w = fac * swf * _np_area(model, m, sec) * mat.unit_weight * sec.mod_weight
                    out += _line(m.pi, m.pj, 0.0, 1.0, w, w, m.length)
            for region in model.shells:
                if not model._region_on_story(region, s):
                    continue
                ssec = model.shell_sections.get(region.section)
                mat = model.materials.get(ssec.material) if ssec else None
                if ssec is not None and mat is not None:
                    out += _area(region.corners, fac * swf * ssec.thickness * mat.unit_weight
                                 * region.net_area * ssec.weight)
    return out


def story_points(model, story_name: str) -> List[Point]:
    """``mass_points`` for the story named ``story_name``; with towers, from the tower view that owns
    it (the same views ``compute_story_masses`` uses)."""
    if getattr(model, "towers", None):
        from skyframe.core import towers as _towers
        for _, v in _towers.views(model):
            for st in v.stories:
                if st.name == story_name:
                    return mass_points(v, st)
        return []
    for st in model.stories:
        if st.name == story_name:
            return mass_points(model, st)
    return []


def inertia(points: List[Point], cx: float, cy: float) -> Tuple[float, float]:
    """(total weight, sum w r^2) about (cx, cy)."""
    tot = sum(p[2] for p in points)
    j = sum(p[2] * ((p[0] - cx) ** 2 + (p[1] - cy) ** 2) for p in points)
    return tot, j
