"""Multiple named diaphragms per story + ETABS additional mass (engine side).

Model side / data: :mod:`skyframe.core.diaphragms`.  Hooks are called from
:meth:`OpenSeesEngine._build`, ``_assign_mass``, ``_apply_pattern`` and
``run``; every hook is a no-op for a model without named-diaphragm
assignments / additional mass, so legacy results stay byte-identical.

NAMED STORIES.  A story is *named* when at least one joint
(``model.joint_diaphragms``) or shell (``ShellRegion.diaphragm``) of that
story is assigned to a named diaphragm.  On a named story the legacy
"whole plane = one diaphragm" rule is replaced by the assignments (the
story-level ``diaphragm`` / ``story_diaphragm`` mode is ignored there):

* a shell assignment captures every structural node of the story plane that
  lies inside or on the slab polygon (frame + mesh nodes); a joint
  assignment captures that node and wins over a shell assignment;
* unassigned nodes of a named story are FREE (ETABS: a joint without a
  diaphragm is not constrained);
* each ``rigid`` diaphragm with >= 2 nodes gets its own master node at the
  diaphragm's centre of mass (weighted by the derived tributary story mass +
  lateral additional mass of its nodes; plan bounding-box centre when it
  carries no such mass) and an OpenSees ``rigidDiaphragm`` constraint;
* ``semi_rigid`` diaphragms add no constraint (their meshed slab carries the
  in-plane stiffness — the v0.22 semi-rigid treatment) but still group the
  nodes for mass / story-force distribution and reporting.

A named story with exactly ONE rigid master is registered in
``asm.masters[story]`` too, so every story-keyed consumer (story results,
TH/pushover control, P-Delta, tables) treats it as before.  Stories with
several diaphragms keep ``asm.masters`` free of them (story-level
consumers then use the node-average fallback) and are reported per
diaphragm in ``results["diaphragms"]``.

STORY MASS on a named rigid diaphragm (lump_at_stories, include_lateral):
``m_d`` = the tributary story mass of its nodes, ``J_d = m_d (a_d^2 +
b_d^2)/12 + m_d |c_d - master|^2`` (``a_d, b_d`` = the diaphragm's plan
extents, ``c_d`` its tributary centroid) — the legacy story rule applied per
diaphragm.  Explicit story masses are split by diaphragm plan (bounding
box) area.  Free / semi-rigid nodes keep their tributary mass.

ADDITIONAL MASS.  Frame ``additional_mass`` (t/m) -> ``m L / 2`` at each
member end ("lumped") or ``m L_seg / 2`` at the ends of every FE segment
("distributed"); shell ``additional_mass`` (t/m^2) -> ``q * net_area`` over
the mesh nodes by tributary area (membrane / unmeshed: equal split over the
corner nodes).  ``include_lateral`` -> UX/UY, ``include_vertical`` -> UZ.
With ``lump_at_stories`` the LATERAL part of an item whose nodes are all
slaves of one rigid master moves onto that master together with its exact
mass moment of inertia about the master (segment: ``m (d^2 + L_h^2/12)``,
plate: ``q * polar second moment of the slab polygon`` minus openings — a
rectangle about its centroid gives ``m (a^2 + b^2)/12``); other slave lumps
move as point masses (``m r^2``).  Joint rotational inertia
``NodalMass.mrx/mry/mrz`` goes on the node's RX/RY/RZ (a rigid slave's
``mrz`` onto its master's RZ — identical kinematics).
"""

from __future__ import annotations

import math
import warnings
from typing import Dict, List, Optional, Tuple

import openseespy.opensees as ops

from skyframe.core import diaphragms as _cd

_TOL = 1e-6


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #
def _in_poly(x: float, y: float, poly: List[Tuple[float, float]]) -> bool:
    """Point in polygon, boundary inclusive (tolerance _TOL*1e3)."""
    n = len(poly)
    tol = 1e-4
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        if L2 > 0:
            t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
            if math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)) < tol:
                return True
    inside = False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def polygon_polar_moment(poly: List[Tuple[float, float]], x0: float,
                         y0: float) -> Tuple[float, float]:
    """(area, polar second moment about (x0, y0)) of a simple polygon."""
    a = ixx = iyy = 0.0
    n = len(poly)
    for i in range(n):
        xa, ya = poly[i][0] - x0, poly[i][1] - y0
        xb, yb = poly[(i + 1) % n][0] - x0, poly[(i + 1) % n][1] - y0
        cr = xa * yb - xb * ya
        a += cr
        ixx += cr * (ya * ya + ya * yb + yb * yb)
        iyy += cr * (xa * xa + xa * xb + xb * xb)
    a *= 0.5
    ip = (ixx + iyy) / 12.0
    if a < 0:
        a, ip = -a, -ip
    return a, ip


def _bbox(pts) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


# --------------------------------------------------------------------------- #
# partition
# --------------------------------------------------------------------------- #
def named_stories(model) -> List[str]:
    if not _cd.has_named(model):
        return []
    out = []
    for s in model.stories:
        hit = any(abs(float(e["point"][2]) - s.elevation) < _TOL
                  for e in model.joint_diaphragms)
        if not hit:
            hit = any(getattr(r, "diaphragm", "")
                      and abs(max(c[2] for c in r.corners) - s.elevation)
                      < _TOL for r in model.shells)
        if hit:
            out.append(s.name)
    return out


def partition(eng, coords: Dict[int, tuple], story) -> Dict[str, List[int]]:
    """{diaphragm name: sorted node tags} of a named story; ``coords`` the
    candidate structural nodes (tag -> xyz)."""
    model = eng.model
    plane = {t: c for t, c in coords.items()
             if abs(c[2] - story.elevation) < _TOL}
    owner: Dict[int, str] = {}
    for r in model.shells:
        name = getattr(r, "diaphragm", "")
        if not name or abs(max(c[2] for c in r.corners)
                           - story.elevation) >= _TOL:
            continue
        poly = [(float(c[0]), float(c[1])) for c in r.corners]
        for t, c in plane.items():
            if _in_poly(c[0], c[1], poly):
                prev = owner.get(t)
                if prev is not None and prev != name:
                    warnings.warn(
                        f"node {t} lies on slabs of diaphragms {prev!r} and "
                        f"{name!r}; kept in {prev!r}", UserWarning)
                    continue
                owner[t] = name
    for e in model.joint_diaphragms:
        p = e["point"]
        if abs(float(p[2]) - story.elevation) >= _TOL:
            continue
        for t, c in plane.items():
            if all(abs(c[k] - float(p[k])) < _TOL for k in range(3)):
                owner[t] = e["diaphragm"]
    out: Dict[str, List[int]] = {}
    for t in sorted(owner):
        out.setdefault(owner[t], []).append(t)
    return out


def _dtype(model, name: str) -> str:
    return (model.diaphragms.get(name) or {}).get("type", "rigid")


def prospective(eng, asm, slaves: set) -> set:
    """Replace the legacy prospective rigid slaves of named stories."""
    model = eng.model
    names = set(named_stories(model))
    if not names:
        return slaves
    out = set(slaves)
    for s in model.stories:
        if s.name not in names:
            continue
        out -= {t for t, c in asm.struct_coords.items()
                if abs(c[2] - s.elevation) < _TOL}
        for d, tags in partition(eng, asm.struct_coords, s).items():
            if _dtype(model, d) == "rigid" and len(tags) >= 2:
                out.update(tags)
    return out


# --------------------------------------------------------------------------- #
# additional mass items
# --------------------------------------------------------------------------- #
def _addl_items(eng, asm) -> List[dict]:
    """Raw additional-mass items: {"nodes": {tag: m}, "m", "kind",
    "geom"} (cached on the assembly)."""
    if "addl" in asm.dia:
        return asm.dia["addl"]
    model = eng.model
    mesh = asm.mesh
    items: List[dict] = []

    def node_at(p) -> Optional[int]:
        try:
            return eng._find_node(asm, p)
        except ValueError:
            return None

    for m in model.members:
        q = float(getattr(m, "additional_mass", 0.0) or 0.0)
        if q <= 0.0:
            continue
        segs = mesh.segments.get(m.uid) or []
        if getattr(m, "additional_mass_mode", "lumped") == "distributed" \
                and segs:
            for seg in segs:
                ms = q * seg.length
                a, b = seg.ni + 1, seg.nj + 1
                pa, pb = asm.node_coords[a], asm.node_coords[b]
                nodes: Dict[int, float] = {}
                nodes[a] = nodes.get(a, 0.0) + 0.5 * ms
                nodes[b] = nodes.get(b, 0.0) + 0.5 * ms
                items.append({"kind": "line", "m": ms, "nodes": nodes,
                              "geom": (pa, pb)})
            continue
        ms = q * m.length
        a = node_at(m.pi)
        b = node_at(m.pj)
        if a is None and segs:
            a = segs[0].ni + 1
        if b is None and segs:
            b = segs[-1].nj + 1
        tags = [t for t in (a, b) if t is not None]
        if not tags:
            warnings.warn(f"additional mass of member {m.uid!r}: no FE end "
                          "node; ignored", UserWarning)
            continue
        nodes = {}
        for t in tags:
            nodes[t] = nodes.get(t, 0.0) + ms / len(tags)
        items.append({"kind": "line", "m": ms, "nodes": nodes,
                      "geom": (tuple(m.pi), tuple(m.pj))})
    for r in model.shells:
        q = float(getattr(r, "additional_mass", 0.0) or 0.0)
        if q <= 0.0:
            continue
        ms = q * r.net_area
        raw: Dict[int, float] = {}
        if r.behavior == "shell":
            for pidx, ta in mesh.region_trib.get(r.uid, {}).items():
                raw[pidx + 1] = raw.get(pidx + 1, 0.0) + ta
        if sum(raw.values()) <= 0.0:
            raw = {}
            for c in r.corners:
                t = node_at(c)
                if t is not None:
                    raw[t] = raw.get(t, 0.0) + 1.0
        tot = sum(raw.values())
        if tot <= 0.0:
            warnings.warn(f"additional mass of shell {r.uid!r}: no FE node; "
                          "ignored", UserWarning)
            continue
        nodes = {t: ms * v / tot for t, v in raw.items()}
        items.append({"kind": "plate", "m": ms, "nodes": nodes, "q": q,
                      "geom": r})
    asm.dia["addl"] = items
    return items


def _plate_J(r, q: float, x0: float, y0: float) -> float:
    """q * polar second moment (net of openings) of a horizontal slab."""
    poly = [(float(c[0]), float(c[1])) for c in r.corners]
    _a, ip = polygon_polar_moment(poly, x0, y0)
    if not r.openings:
        return q * ip
    if any(getattr(op, "polygon", None) for op in r.openings):
        return q * ip * (r.net_area / r.area if r.area > 0 else 1.0)
    for op in r.openings:
        pts = [r.map_uv(op.u0, op.v0), r.map_uv(op.u1, op.v0),
               r.map_uv(op.u1, op.v1), r.map_uv(op.u0, op.v1)]
        ip -= polygon_polar_moment([(p[0], p[1]) for p in pts], x0, y0)[1]
    return q * ip


def _lateral_node_lumps(eng, asm) -> Dict[int, float]:
    """Additional-mass lateral lumps per node (pre-relumping) — CM weights."""
    out: Dict[int, float] = {}
    for it in _addl_items(eng, asm):
        for t, v in it["nodes"].items():
            out[t] = out.get(t, 0.0) + v
    return out


# --------------------------------------------------------------------------- #
# build: masters + constraints of named stories
# --------------------------------------------------------------------------- #
def _trib(eng, asm) -> Dict[int, float]:
    if "trib" not in asm.dia:
        model = eng.model
        if getattr(model, "mass_source_mode", "weight") == "weight":
            asm.dia["trib"] = eng._tributary_masses(asm)
        else:
            asm.dia["trib"] = {}
    return asm.dia["trib"]


def build(eng, asm, tag: int, slave_master: Dict[int, int]) -> int:
    """Create the masters / rigidDiaphragm constraints of named stories.
    Returns the last node tag used."""
    model = eng.model
    names = named_stories(model)
    asm.dia["slave_master"] = slave_master
    if not names:
        return tag
    groups: Dict[Tuple[str, str], dict] = {}
    trib = _trib(eng, asm)
    addl = _lateral_node_lumps(eng, asm) if \
        model.mass_option("include_lateral") else {}
    for s in model.stories:
        if s.name not in names:
            continue
        sn = set(asm.story_nodes.get(s.name, ()))
        part = partition(eng, {t: asm.struct_coords[t] for t in sn}, s)
        explicit = s.name in model.story_masses
        rigid_tags = []
        for d in sorted(part):
            nodes = part[d]
            dtype = _dtype(model, d)
            pts = [asm.struct_coords[t] for t in nodes]
            x0, x1, y0, y1 = _bbox(pts)
            g = {"story": s.name, "name": d, "type": dtype, "nodes": nodes,
                 "bbox": (x0, x1, y0, y1), "master": None}
            if dtype == "rigid" and len(nodes) >= 2:
                w = {t: (0.0 if explicit else trib.get(t, 0.0))
                     + addl.get(t, 0.0) for t in nodes}
                ws = sum(w.values())
                if ws > 0.0:
                    cx = sum(v * asm.struct_coords[t][0]
                             for t, v in w.items()) / ws
                    cy = sum(v * asm.struct_coords[t][1]
                             for t, v in w.items()) / ws
                else:
                    cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
                elev = round(s.elevation, 6)
                tag += 1
                ops.node(tag, cx, cy, elev)
                ops.fix(tag, 0, 0, 1, 1, 1, 0)
                ops.rigidDiaphragm(3, tag, *nodes)
                for sl in nodes:
                    slave_master[sl] = tag
                asm.node_coords[tag] = (cx, cy, elev)
                asm.node_restraints[tag] = (0, 0, 1, 1, 1, 0)
                g["master"] = tag
                rigid_tags.append(tag)
            groups[(s.name, d)] = g
        if len(rigid_tags) == 1 and len(part) == 1:
            asm.masters[s.name] = rigid_tags[0]
    asm.dia["groups"] = groups
    asm.dia["named"] = set(names)
    asm.dia["masters"] = {k: g["master"] for k, g in groups.items()
                          if g["master"] is not None}
    return tag


def all_master_tags(asm) -> set:
    return set(asm.masters.values()) | set(
        (asm.dia.get("masters") or {}).values())


# --------------------------------------------------------------------------- #
# mass
# --------------------------------------------------------------------------- #
def lump_story(eng, asm, add, story, m: float, explicit: bool) -> bool:
    """Lateral story mass of a named story.  Returns False when the story is
    not named (caller keeps the legacy path)."""
    if story.name not in asm.dia.get("named", ()):
        return False
    groups = [g for (sn, _d), g in asm.dia["groups"].items()
              if sn == story.name]
    if explicit:
        areas = [max((g["bbox"][1] - g["bbox"][0])
                     * (g["bbox"][3] - g["bbox"][2]), 0.0) for g in groups]
        if sum(areas) <= 0.0:
            areas = [float(len(g["nodes"])) for g in groups]
        tot = sum(areas)
        for g, a in zip(groups, areas):
            md = m * a / tot
            _put_group(asm, add, g, md, None)
        return True
    trib = _trib(eng, asm)
    grouped = set()
    for g in groups:
        nodes = g["nodes"]
        grouped.update(nodes)
        if g["master"] is None:
            for t in nodes:
                v = trib.get(t, 0.0)
                add(t, 1, v)
                add(t, 2, v)
            continue
        md = sum(trib.get(t, 0.0) for t in nodes)
        if md <= 0.0:
            continue
        cx = sum(trib.get(t, 0.0) * asm.struct_coords[t][0]
                 for t in nodes) / md
        cy = sum(trib.get(t, 0.0) * asm.struct_coords[t][1]
                 for t in nodes) / md
        _put_group(asm, add, g, md, (cx, cy))
    for t in asm.story_nodes.get(story.name, ()):
        if t not in grouped:
            v = trib.get(t, 0.0)
            add(t, 1, v)
            add(t, 2, v)
    return True


def _put_group(asm, add, g: dict, md: float, centroid) -> None:
    if md <= 0.0:
        return
    x0, x1, y0, y1 = g["bbox"]
    a, b = x1 - x0, y1 - y0
    mt = g["master"]
    if mt is None:
        nodes = g["nodes"]
        for t in nodes:
            add(t, 1, md / len(nodes))
            add(t, 2, md / len(nodes))
        return
    mx, my = asm.node_coords[mt][0], asm.node_coords[mt][1]
    if centroid is None:
        centroid = (0.5 * (x0 + x1), 0.5 * (y0 + y1))
    d2 = (centroid[0] - mx) ** 2 + (centroid[1] - my) ** 2
    add(mt, 1, md)
    add(mt, 2, md)
    add(mt, 6, md * ((a * a + b * b) / 12.0 + d2))


def add_extra_mass(eng, asm, add, opts: dict) -> None:
    """Joint rotational inertia + frame/shell additional mass."""
    model = eng.model
    sm = asm.dia.get("slave_master") or {}
    for nm in model.nodal_masses:
        mr = (getattr(nm, "mrx", 0.0), getattr(nm, "mry", 0.0),
              getattr(nm, "mrz", 0.0))
        if not any(mr):
            continue
        t = eng._find_node(asm, nm.point)
        add(t, 4, mr[0])
        add(t, 5, mr[1])
        add(sm.get(t, t), 6, mr[2])
    if not _cd.has_additional_mass(model):
        return
    lateral = opts["include_lateral"]
    vertical = opts["include_vertical"]
    lump = opts["lump_at_stories"]
    for it in _addl_items(eng, asm):
        nodes = it["nodes"]
        if vertical:
            for t, v in nodes.items():
                add(t, 3, v)
        if not lateral:
            continue
        if not lump:
            for t, v in nodes.items():
                add(t, 1, v)
                add(t, 2, v)
            continue
        masters = {sm.get(t) for t in nodes}
        if len(masters) == 1 and None not in masters:
            mt = masters.pop()
            mx, my = asm.node_coords[mt][0], asm.node_coords[mt][1]
            ms = it["m"]
            if it["kind"] == "plate":
                J = _plate_J(it["geom"], it["q"], mx, my)
            else:
                pa, pb = it["geom"]
                xm, ym = 0.5 * (pa[0] + pb[0]), 0.5 * (pa[1] + pb[1])
                Lh2 = (pb[0] - pa[0]) ** 2 + (pb[1] - pa[1]) ** 2
                J = ms * ((xm - mx) ** 2 + (ym - my) ** 2 + Lh2 / 12.0)
            add(mt, 1, ms)
            add(mt, 2, ms)
            add(mt, 6, J)
            continue
        for t, v in nodes.items():
            mt = sm.get(t)
            if mt is None:
                add(t, 1, v)
                add(t, 2, v)
                continue
            c, mc = asm.node_coords[t], asm.node_coords[mt]
            add(mt, 1, v)
            add(mt, 2, v)
            add(mt, 6, v * ((c[0] - mc[0]) ** 2 + (c[1] - mc[1]) ** 2))


# --------------------------------------------------------------------------- #
# story forces
# --------------------------------------------------------------------------- #
def _distribute(asm, nodes: List[int], fx: float, fy: float, mz_x: float,
                mz_y: float, story: str) -> None:
    """v0.22-style mass-weighted force field + antisymmetric torsion couple."""
    w = [asm.mass_map.get((t, 1), 0.0) for t in nodes]
    if not any(v > 0.0 for v in w):
        w = [1.0] * len(nodes)
    ws = sum(w)
    xs = [asm.node_coords[t][0] for t in nodes]
    ys = [asm.node_coords[t][1] for t in nodes]
    xb = sum(a * b for a, b in zip(w, xs)) / ws
    yb = sum(a * b for a, b in zip(w, ys)) / ws
    iwy = sum(a * (y - yb) ** 2 for a, y in zip(w, ys))
    iwx = sum(a * (x - xb) ** 2 for a, x in zip(w, xs))
    mu = mz_x / iwy if (mz_x and iwy > 1e-12) else 0.0
    nu = mz_y / iwx if (mz_y and iwx > 1e-12) else 0.0
    if (mz_x and iwy <= 1e-12) or (mz_y and iwx <= 1e-12):
        warnings.warn(f"Story {story!r}: accidental torsion couple cannot be "
                      "realized on a diaphragm without plan spread; skipped",
                      UserWarning)
    for t, wi, x, y in zip(nodes, w, xs, ys):
        ops.load(t, fx * wi / ws - mu * wi * (y - yb),
                 fy * wi / ws + nu * wi * (x - xb), 0.0, 0.0, 0.0, 0.0)


def _group_weight(asm, g: dict) -> float:
    w = sum(asm.mass_map.get((t, 1), 0.0) for t in g["nodes"])
    if g["master"] is not None:
        w += asm.mass_map.get((g["master"], 1), 0.0)
    return w


def apply_story_force(eng, asm, sf, fx: float, fy: float, acc: bool,
                      ecc: float) -> bool:
    """Story force on a named story: split over its diaphragms (and free
    nodes) by lateral mass (node count when massless); each diaphragm's
    accidental torsion uses ITS plan extents.  False = not named."""
    if sf.story not in asm.dia.get("named", ()):
        return False
    groups = [g for (sn, _d), g in asm.dia["groups"].items()
              if sn == sf.story]
    grouped = {t for g in groups for t in g["nodes"]}
    free = [t for t in asm.story_nodes.get(sf.story, ())
            if t not in grouped]
    parts = list(groups)
    if free:
        pts = [asm.node_coords[t] for t in free]
        parts.append({"nodes": free, "master": None, "bbox": _bbox(pts),
                      "name": None})
    if not parts:
        raise ValueError(f"Story force on story {sf.story!r} which has no "
                         "nodes")
    w = [_group_weight(asm, g) for g in parts]
    if not any(v > 0.0 for v in w):
        w = [float(len(g["nodes"])) for g in parts]
    ws = sum(w)
    for g, wi in zip(parts, w):
        gx, gy = fx * wi / ws, fy * wi / ws
        x0, x1, y0, y1 = g["bbox"]
        mz_x = gx * ecc * (y1 - y0) if acc else 0.0
        mz_y = gy * ecc * (x1 - x0) if acc else 0.0
        if g["master"] is not None:
            ops.load(g["master"], gx, gy, 0.0, 0.0, 0.0, mz_x + mz_y)
        else:
            _distribute(asm, g["nodes"], gx, gy, mz_x, mz_y, sf.story)
    return True


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
def _group_props(asm, g: dict) -> dict:
    mt = g["master"]
    mass = 0.0
    sx = sy = 0.0
    for t in g["nodes"]:
        v = asm.mass_map.get((t, 1), 0.0)
        mass += v
        sx += v * asm.node_coords[t][0]
        sy += v * asm.node_coords[t][1]
    J = 0.0
    if mt is not None:
        v = asm.mass_map.get((mt, 1), 0.0)
        mass += v
        sx += v * asm.node_coords[mt][0]
        sy += v * asm.node_coords[mt][1]
        J = asm.mass_map.get((mt, 6), 0.0)
    x0, x1, y0, y1 = g["bbox"]
    if mass > 0.0:
        cmx, cmy = sx / mass, sy / mass
    elif mt is not None:
        cmx, cmy = asm.node_coords[mt][0], asm.node_coords[mt][1]
    else:
        cmx, cmy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    out = {"type": g["type"], "master": mt, "n_nodes": len(g["nodes"]),
           "mass": float(mass), "mass_rz": float(J),
           "cm_x": float(cmx), "cm_y": float(cmy),
           "extent_x": float(x1 - x0), "extent_y": float(y1 - y0)}
    if mt is not None:
        out["x"], out["y"] = asm.node_coords[mt][0], asm.node_coords[mt][1]
    return out


def _unit_rz(eng, mtag: int, vec) -> float:
    asm = eng._elastic_domain()
    eng._seg_span_loads = {}
    eng._seg_fef = {}
    from skyframe.engine.opensees_engine import _REUSE
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    _REUSE["loaded"] = True
    ops.load(mtag, *vec)
    eng._setup_analysis(asm)
    if ops.analyze(1) != 0:                              # pragma: no cover
        raise RuntimeError("CR unit-load solve failed (diaphragm master)")
    return float(ops.nodeDisp(mtag)[5])


def _disp_of(g: dict, nd: dict, asm) -> Optional[Tuple[float, float, float]]:
    mt = g["master"]
    if mt is not None:
        d = nd.get(mt)
        return None if d is None else (d[0], d[1], d[5])
    vals = [nd[t] for t in g["nodes"] if t in nd]
    if not vals:
        return None
    n = len(vals)
    return (sum(v[0] for v in vals) / n, sum(v[1] for v in vals) / n,
            sum(v[5] for v in vals) / n)


def _case_rows(eng, asm, cr, story_idx) -> dict:
    model = eng.model
    groups = asm.dia["groups"]
    nd = cr.node_disp
    out: Dict[str, Dict[str, dict]] = {}
    for (sn, d), g in groups.items():
        dd = _disp_of(g, nd, asm)
        if dd is None:
            continue
        ux, uy, rz = dd
        i = story_idx[sn]
        s = model.stories[i]
        h = s.height if s.height > 0 else 1.0
        if g["master"] is not None:
            px, py = asm.node_coords[g["master"]][:2]
        else:
            x0, x1, y0, y1 = g["bbox"]
            px, py = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
        bx = by = 0.0
        if i > 0:
            below = model.stories[i - 1]
            gb = groups.get((below.name, d))
            mb = (gb["master"] if gb is not None else
                  asm.masters.get(below.name))
            if mb is not None and mb in nd:
                db = nd[mb]
                mx, my = asm.node_coords[mb][:2]
                bx = db[0] - db[5] * (py - my)
                by = db[1] + db[5] * (px - mx)
            elif gb is not None:
                v = _disp_of(gb, nd, asm)
                if v is not None:
                    bx, by = v[0], v[1]
            else:
                e = cr.story.get(below.name, {})
                bx, by = e.get("ux", 0.0), e.get("uy", 0.0)
        x0, x1, y0, y1 = g["bbox"]
        row = {"ux": ux, "uy": uy, "rz": rz,
               "drift_x": (ux - bx) / h, "drift_y": (uy - by) / h,
               "tors_ratio_x": eng._tors_ratio(ux, rz, y1 - y0),
               "tors_ratio_y": eng._tors_ratio(uy, rz, x1 - x0)}
        out.setdefault(sn, {})[d] = {k: float(v) for k, v in row.items()}
    return out


def results(eng, asm, sources: Dict[str, Dict[str, object]],
            irregularity: dict) -> dict:
    """``AnalysisResults.diaphragms`` ({} for models without named stories).

    ``sources``: {"cases": {...}, "combos": {...}, "rs_cases": {...}}.
    Also raises the story-level torsional ratio of multi-diaphragm stories
    in ``irregularity`` to the max over their diaphragms."""
    if not asm.dia.get("groups"):
        return {}
    model = eng.model
    story_idx = {s.name: i for i, s in enumerate(model.stories)}
    props: Dict[str, Dict[str, dict]] = {}
    for (sn, d), g in asm.dia["groups"].items():
        props.setdefault(sn, {})[d] = _group_props(asm, g)
    # centre of rigidity per rigid master (unit-load method, as v0.8)
    for (sn, d), g in asm.dia["groups"].items():
        mt = g["master"]
        if mt is None:
            continue
        tx = _unit_rz(eng, mt, (1.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        ty = _unit_rz(eng, mt, (0.0, 1.0, 0.0, 0.0, 0.0, 0.0))
        phi = _unit_rz(eng, mt, (0.0, 0.0, 0.0, 0.0, 0.0, 1.0))
        mx, my = asm.node_coords[mt][:2]
        e = props[sn][d]
        if abs(phi) > 1e-30:
            e["cr_x"] = float(mx - ty / phi)
            e["cr_y"] = float(my + tx / phi)
            e["k_theta"] = float(1.0 / phi)
    eng._elastic_domain()
    out = {"definitions": {n: dict(v) for n, v in model.diaphragms.items()},
           "stories": props}
    for key, src in sources.items():
        rows = {}
        for name, cr in src.items():
            if cr is None or getattr(cr, "minima", None) is not None:
                continue
            rows[name] = _case_rows(eng, asm, cr, story_idx)
        if rows:
            out[key] = rows
    # story-level torsional irregularity of multi-diaphragm stories
    multi = {sn for sn, ds in props.items() if len(ds) > 1}
    for cname, by_story in irregularity.items():
        rows = (out.get("cases", {}).get(cname)
                or out.get("combos", {}).get(cname) or {})
        for sn in multi:
            if sn not in by_story or sn not in rows:
                continue
            e = by_story[sn]
            trx = max(r["tors_ratio_x"] for r in rows[sn].values())
            tr_y = max(r["tors_ratio_y"] for r in rows[sn].values())
            e["tors_ratio_x"], e["tors_ratio_y"] = trx, tr_y
            trmax = max(trx, tr_y)
            e["flag"] = ("extreme" if trmax >= 1.4 else
                         "torsional" if trmax >= 1.2 else "none")
    return out


# --------------------------------------------------------------------------- #
# model-wide P-Delta (non_iterative_mass) on multi-diaphragm stories
# --------------------------------------------------------------------------- #
def pdelta_story_springs(asm, i: int, stories, z_bot: float, h: float,
                         node_mass: Dict[int, float], zs: Dict[int, float],
                         fixed_xy: List[int], story_spring, column_strings,
                         eng) -> Tuple[Optional[list], Dict[str, float]]:
    """Per-diaphragm story springs for a story with several diaphragms.
    ``P_d = g * mass above the story bottom inside diaphragm d's plan box``;
    spring to the same-name master below (first story: nearest XY-fixed
    support), else column strings of the columns inside the box.  Returns
    (springs or None when the story is not multi-diaphragm, {d: P_d})."""
    from skyframe.core.model import G_ACCEL
    s = stories[i]
    groups = {d: g for (sn, d), g in (asm.dia.get("groups") or {}).items()
              if sn == s.name}
    if len(groups) < 2:
        return None, {}
    springs: list = []
    P_by: Dict[str, float] = {}
    for d, g in sorted(groups.items()):
        x0, x1, y0, y1 = g["bbox"]
        tol = 1e-3

        def inside(t):
            c = asm.node_coords[t]
            return (x0 - tol <= c[0] <= x1 + tol
                    and y0 - tol <= c[1] <= y1 + tol)
        P = G_ACCEL * sum(m for t, m in node_mass.items()
                          if zs[t] > z_bot + _TOL and inside(t))
        P_by[d] = P
        if P == 0.0:
            continue
        top = g["master"]
        low = None
        if top is not None:
            if i > 0:
                gb = (asm.dia["groups"].get((stories[i - 1].name, d)))
                low = (gb["master"] if gb is not None else
                       asm.masters.get(stories[i - 1].name))
            else:
                cands = [t for t in fixed_xy
                         if asm.node_coords[t][2] <= z_bot + _TOL]
                if cands:
                    mx, my = asm.node_coords[top][:2]
                    low = min(cands, key=lambda t: (
                        (asm.node_coords[t][0] - mx) ** 2
                        + (asm.node_coords[t][1] - my) ** 2, t))
        if top is not None and low is not None:
            springs.append(story_spring(low, top, P, h))
            continue
        springs.extend(column_strings(eng, asm, z_bot, s.elevation, P,
                                      box=(x0 - tol, x1 + tol, y0 - tol,
                                           y1 + tol)))
    return springs, P_by
