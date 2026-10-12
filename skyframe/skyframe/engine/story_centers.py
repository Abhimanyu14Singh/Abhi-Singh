"""Story centre of mass (CM) for the "Centers Of Mass And Rigidity" output.

Definitions (CONTRACT "Centers of mass and rigidity (audit D3)"):

* **CM** of a rigid-diaphragm story = the mass-weighted centroid of the
  LATERAL (UX) mass the analysis really carries on that story: the mass
  lumped at the diaphragm master plus every mass sitting on the story's
  slave nodes (explicit joint masses, element self-mass, additional
  frame/shell mass, tributary story mass when ``lump_at_stories`` is
  off).  The part of the master mass that is the derived (gravity-load)
  story mass is placed at the centroid of the loads that produced it
  (:func:`derived_centroids`), additional mass lumped onto a master at the
  centroid of that item, and any other master mass at the master itself.
* **CR** (unchanged, engine ``_compute_story_props``): unit Fx, Fy and Mz
  applied in turn at the story's master only (no load on any other
  story; the floors above / below are free, as ETABS), the CR being the
  point where a lateral force produces no rotation of THAT story.

Pure post-processing of the assembled model: no solve here.
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

from skyframe.core import loads_ext as _lx
from skyframe.core import nonprismatic as _npx
from skyframe.core.thermal_ext import projected_factor as _proj_f

XY = Tuple[float, float]


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #
def _poly_centroid3(pts) -> Tuple[float, Tuple[float, float, float]]:
    """(area, centroid) of a planar 3D polygon (any orientation, simple)."""
    p = [tuple(map(float, q)) for q in pts]
    n = len(p)
    nx = ny = nz = 0.0                                   # Newell normal
    for i in range(n):
        a, b = p[i], p[(i + 1) % n]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    nn = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nn <= 0.0:
        c = tuple(sum(q[k] for q in p) / n for k in range(3))
        return 0.0, c
    u = (nx / nn, ny / nn, nz / nn)
    o = p[0]
    at = 0.0
    sx = sy = sz = 0.0
    for i in range(1, n - 1):
        a, b = p[i], p[i + 1]
        e1 = (a[0] - o[0], a[1] - o[1], a[2] - o[2])
        e2 = (b[0] - o[0], b[1] - o[1], b[2] - o[2])
        cr = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2],
              e1[0] * e2[1] - e1[1] * e2[0])
        ar = 0.5 * (cr[0] * u[0] + cr[1] * u[1] + cr[2] * u[2])  # signed
        at += ar
        sx += ar * (o[0] + a[0] + b[0]) / 3.0
        sy += ar * (o[1] + a[1] + b[1]) / 3.0
        sz += ar * (o[2] + a[2] + b[2]) / 3.0
    if abs(at) <= 1e-300:
        c = tuple(sum(q[k] for q in p) / n for k in range(3))
        return 0.0, c
    return abs(at), (sx / at, sy / at, sz / at)


def region_centroid(region) -> XY:
    """Plan centroid of a shell region's NET area (openings removed).

    A 4-corner parallelogram without openings returns the bilinear centre
    ``map_uv(0.5, 0.5)`` (identical to the true centroid; kept for
    bit-identical legacy output).  Every other region (trapezoid, polygon
    / L-shape, openings) uses the exact area centroid — the pre-D3 code used
    the parametric centre for all of them (bounding-box centre for
    polygons), which is wrong whenever the shape is not centrally
    symmetric."""
    cs = [tuple(map(float, c)) for c in region.corners]
    ops_ = getattr(region, "openings", None) or []
    if len(cs) == 4 and not ops_ and all(
            abs(cs[0][k] + cs[2][k] - cs[1][k] - cs[3][k]) <= 1e-9
            for k in range(3)):
        c = region.map_uv(0.5, 0.5)
        return c[0], c[1]
    a0, c0 = _poly_centroid3(cs)
    if a0 <= 0.0:
        c = region.map_uv(0.5, 0.5)
        return c[0], c[1]
    ax, ay, at = a0 * c0[0], a0 * c0[1], a0
    if ops_:
        from skyframe.core.polymesh import opening_polygons3
        for h in opening_polygons3(region):
            ah, ch = _poly_centroid3(h)
            ax -= ah * ch[0]
            ay -= ah * ch[1]
            at -= ah
    if at <= 1e-12:
        return c0[0], c0[1]
    return ax / at, ay / at


def _mid(m) -> XY:
    return (0.5 * (m.pi[0] + m.pj[0]), 0.5 * (m.pi[1] + m.pj[1]))


def _at(m, f: float) -> XY:
    """Plan point at length fraction ``f`` (exact midpoint for 0.5)."""
    if f == 0.5:
        return _mid(m)
    return (m.pi[0] + f * (m.pj[0] - m.pi[0]),
            m.pi[1] + f * (m.pj[1] - m.pi[1]))


def _load_fraction(ml) -> float:
    """Length fraction of the resultant of a gravity MemberLoad."""
    a, b = float(ml.a), float(ml.b)
    if ml.kind == "point":
        return a
    if ml.kind == "udl":
        return 0.5 * (a + b)
    w1, w2 = float(ml.w), float(ml.w2)
    if w1 + w2 == 0.0 or w1 == w2:
        return 0.5 * (a + b)
    return a + (b - a) * (w1 + 2.0 * w2) / (3.0 * (w1 + w2))


# --------------------------------------------------------------------------- #
# derived (gravity-load) story mass centroid
# --------------------------------------------------------------------------- #
def derived_centroids(eng) -> Dict[str, XY]:
    """Centroid of each story's DERIVED mass (the weight / g of the mass
    source patterns, the same contributions and options as
    ``BuildingModel.compute_story_masses``).  Stories with an explicit
    ``story_masses`` entry (no spatial information) get the plan centre.

    Each contribution sits at its true centroid: member point / partial /
    trapezoidal loads at their resultant, area loads and slab self-weight at
    the region's net-area centroid (:func:`region_centroid`)."""
    model = eng.model
    source = model.effective_mass_source()
    inc_self = model.mass_option("self_mass")
    inc_pat = model.mass_option("patterns")
    out: Dict[str, XY] = {}
    for s in model.stories:
        if s.name in model.story_masses:
            out[s.name] = model.plan_center()   # explicit: no spatial info
            continue
        wsum = wx = wy = 0.0

        def add(w: float, xy: XY) -> None:
            nonlocal wsum, wx, wy
            wsum += w
            wx += w * xy[0]
            wy += w * xy[1]

        for pname, fac in source.items():
            pat = model.patterns.get(pname)
            if pat is None:
                continue
            if inc_pat:
                for udl in pat.member_udls:
                    m = eng._mlookup(udl.member_uid)
                    if m is not None and m.story == s.name and \
                            m.kind != "column":
                        add(fac * udl.w * m.length, _mid(m))
                for ml in pat.member_loads:
                    if ml.direction != "gravity":
                        continue
                    m = eng._mlookup(ml.member_uid)
                    if m is None or m.story != s.name or m.kind == "column":
                        continue
                    if ml.kind == "point":
                        w = fac * ml.w
                    elif ml.kind == "udl":
                        w = fac * ml.w * (ml.b - ml.a) * m.length
                    else:
                        w = fac * 0.5 * (ml.w + ml.w2) * (ml.b - ml.a) \
                            * m.length
                    add(w * _proj_f(m, ml), _at(m, _load_fraction(ml)))
                for al in pat.area_loads:
                    region = eng._slookup(al.region_uid)
                    if region is None or \
                            not model._region_on_story(region, s):
                        continue
                    add(fac * al.q * region.net_area
                        if _lx.area_load_is_default(al) else
                        fac * _lx.area_load_weight(region, al),
                        region_centroid(region))
                for nl in pat.nodal_loads:
                    if abs(nl.point[2] - s.elevation) < 1e-6:
                        add(fac * (-nl.fz), (nl.point[0], nl.point[1]))
            swf = getattr(pat, "self_weight_factor", 0.0)
            if swf and inc_self:
                for m in model.members:
                    if m.story != s.name or m.kind == "column":
                        continue
                    sec = model.sections.get(m.section)
                    mat = (model.materials.get(sec.material)
                           if sec else None)
                    if sec is None or mat is None:
                        continue
                    if _npx.is_varying(model, sec):
                        k = fac * swf * mat.unit_weight * m.length \
                            * sec.mod_weight
                        for a_, b_, A0, A1 in _npx.area_pieces(model, m):
                            if A0 + A1 <= 0.0:
                                continue
                            f = a_ + (b_ - a_) * (A0 + 2.0 * A1) / (
                                3.0 * (A0 + A1))
                            add(k * 0.5 * (A0 + A1) * (b_ - a_), _at(m, f))
                        continue
                    add(fac * swf * sec.A * mat.unit_weight
                        * m.length * sec.mod_weight, _mid(m))
                for region in model.shells:
                    if not model._region_on_story(region, s):
                        continue
                    ssec = model.shell_sections.get(region.section)
                    mat = (model.materials.get(ssec.material)
                           if ssec else None)
                    if ssec is not None and mat is not None:
                        add(fac * swf * ssec.thickness * mat.unit_weight
                            * region.net_area * ssec.weight,
                            region_centroid(region))
        if wsum > 1e-12:
            out[s.name] = (wx / wsum, wy / wsum)
        else:
            out[s.name] = model.plan_center()
    return out


# --------------------------------------------------------------------------- #
# story CM from the analysis mass
# --------------------------------------------------------------------------- #
def _lat(mass_map, t: int) -> float:
    v = mass_map.get((t, 1), 0.0)
    return v if v > 0.0 else mass_map.get((t, 2), 0.0)


def _addl_at_masters(eng, asm) -> Dict[int, list]:
    """{master tag: [(mass, x, y), ...]} additional frame / shell mass that
    :func:`skyframe.engine.diaphragms.add_extra_mass` lumped onto a master
    (the item centroid, or the slave node it came from)."""
    from skyframe.core import diaphragms as _cd
    from skyframe.engine import diaphragms as _dgm
    model = eng.model
    out: Dict[int, list] = {}
    if not _cd.has_additional_mass(model):
        return out
    opts = model.effective_mass_options()
    if not opts["include_lateral"] or not opts["lump_at_stories"]:
        return out
    sm = asm.dia.get("slave_master") or {}
    for it in _dgm._addl_items(eng, asm):
        nodes = it["nodes"]
        masters = {sm.get(t) for t in nodes}
        if len(masters) == 1 and None not in masters:
            mt = next(iter(masters))
            tot = sum(nodes.values())
            if tot > 0.0:
                cx = sum(v * asm.node_coords[t][0]
                         for t, v in nodes.items()) / tot
                cy = sum(v * asm.node_coords[t][1]
                         for t, v in nodes.items()) / tot
            else:
                cx, cy = asm.node_coords[mt][0], asm.node_coords[mt][1]
            out.setdefault(mt, []).append((float(it["m"]), cx, cy))
            continue
        for t, v in nodes.items():
            mt = sm.get(t)
            if mt is not None:
                out.setdefault(mt, []).append(
                    (float(v), asm.node_coords[t][0], asm.node_coords[t][1]))
    return out


def story_cm(eng, asm, derived: Dict[str, XY]) -> Dict[str, XY]:
    """Per rigid-diaphragm story: CM of the analysis lateral mass (see the
    module docstring).  ``derived`` = the derived-mass centroids
    (``eng._story_cm()``, tower aware).  A story whose only lateral mass is
    the derived master lump returns ``derived`` unchanged (bit-identical
    legacy output); a story with no lateral mass at all falls back to it."""
    mm = asm.mass_map
    named = asm.dia.get("named") or set()
    addl = _addl_at_masters(eng, asm)
    out: Dict[str, XY] = {}
    for s in eng.model.stories:
        mt = asm.masters.get(s.name)
        if mt is None:
            continue
        dflt = derived.get(s.name, (asm.node_coords[mt][0],
                                    asm.node_coords[mt][1]))
        parts = []                                    # (m, x, y)
        for t in asm.story_nodes.get(s.name, ()):
            if t == mt:
                continue
            v = _lat(mm, t)
            if v > 0.0:
                parts.append((v, asm.node_coords[t][0],
                              asm.node_coords[t][1]))
        m_master = _lat(mm, mt)
        mx, my = asm.node_coords[mt][0], asm.node_coords[mt][1]
        if s.name in named:
            # named-diaphragm master: built AT the centroid of its own mass
            if m_master > 0.0:
                parts.append((m_master, mx, my))
            out[s.name] = _centroid(parts, dflt)
            continue
        extra = addl.get(mt, [])
        if not parts and not extra:
            out[s.name] = dflt                        # legacy: unchanged
            continue
        m_add = sum(p[0] for p in extra)
        m_der = m_master - m_add
        parts.extend(extra)
        if m_der > 1e-12 * max(m_master, 1.0):
            parts.append((m_der, dflt[0], dflt[1]))
        out[s.name] = _centroid(parts, dflt)
    return out


def story_lateral_mass(asm, story: str, master: Optional[int]) -> float:
    """Total lateral (UX) mass of a story: master + its story nodes."""
    mm = asm.mass_map
    tot = _lat(mm, master) if master is not None else 0.0
    for t in asm.story_nodes.get(story, ()):
        if t != master:
            tot += _lat(mm, t)
    return tot


def _centroid(parts, dflt: XY) -> XY:
    ms = sum(p[0] for p in parts)
    if ms <= 0.0:
        return dflt
    return (sum(p[0] * p[1] for p in parts) / ms,
            sum(p[0] * p[2] for p in parts) / ms)
