"""CIS/2 (LPM6, ISO 10303-21 STEP) READER -- the subset needed for frame
members and their sections (docs/INTEROP.md, CONTRACT "SAP2000 / SAFE /
steel interop").

``read_cis2(text, units=None, sections_from=None)`` -> ``(model,
warnings)``.

Entities read (attribute positions are matched tolerantly: the reader
takes the first attribute of the expected KIND, so the optional item
number / description slots of LPM6 may be present or not):

* analysis model -- ``NODE`` (name + a ``CARTESIAN_POINT``, optional
  ``BOUNDARY_CONDITION_LOGICAL`` restraints: six .T./.F. = fixed),
  ``ELEMENT_CURVE_SIMPLE`` (name, ``SECTION_PROFILE*`` and ``MATERIAL*``
  references), ``ELEMENT_NODE_CONNECTIVITY`` (node, element and the
  connectivity number 1 / 2 = end i / j);
* design / physical model (used when there is no analysis model) --
  ``LOCATED_PART`` -> ``AXIS2_PLACEMENT_3D`` (location, axis) and
  ``PART_PRISMATIC_SIMPLE`` (section, length): the member runs from the
  placement origin along its axis;
* sections -- ``SECTION_PROFILE_I_TYPE``, ``_CHANNEL``, ``_ANGLE``,
  ``_T_TYPE``, ``_RECTANGLE``, ``_RECTANGLE_HOLLOW``, ``_CIRCLE``,
  ``_CIRCLE_HOLLOW`` and plain ``SECTION_PROFILE``: a name found in a
  steel database (aisc, eurocode, bs, as_nzs, is808) or in
  ``sections_from`` wins; otherwise the numeric dimensions in attribute
  order (I / channel / tee: depth, width, web, flange; angle: depth,
  width, thickness; rectangle: depth, width[, wall]; circle: diameter[,
  wall]) build a parametric shape;
* units -- ``SI_UNIT(.MILLI.,.METRE.)`` = mm, ``CONVERSION_BASED_UNIT
  ('INCH' | 'FOOT')``, else metres (``units`` overrides: m, mm, in, ft).

Members are columns (vertical), beams (horizontal) or braces; stories come
from the distinct end elevations; supports from node restraints, else
fixed at the lowest level.
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

from skyframe.io.ifc import StepEnum, StepRef, StepTyped, parse_step

__all__ = ["read_cis2"]

_UNITS = {"m": 1.0, "mm": 1e-3, "in": 0.0254, "ft": 0.3048}
_STDS = ("aisc", "eurocode", "bs", "as_nzs", "is808")
_SEC_TYPES = {"SECTION_PROFILE_I_TYPE": "I", "SECTION_PROFILE_CHANNEL":
              "channel", "SECTION_PROFILE_ANGLE": "angle",
              "SECTION_PROFILE_T_TYPE": "tee",
              "SECTION_PROFILE_RECTANGLE": "rect",
              "SECTION_PROFILE_RECTANGLE_HOLLOW": "rect_tube",
              "SECTION_PROFILE_CIRCLE": "round_solid",
              "SECTION_PROFILE_CIRCLE_HOLLOW": "round_tube"}


def _detect_units(text: str) -> float:
    up = text.upper()
    if re.search(r"LENGTH_UNIT\s*\(\s*\)[^;]*SI_UNIT\s*\(\s*\.MILLI\.\s*,"
                 r"\s*\.METRE\.", up) or re.search(
                     r"SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.", up):
        return 1e-3
    if "CONVERSION_BASED_UNIT('INCH'" in up.replace(" ", ""):
        return 0.0254
    if "CONVERSION_BASED_UNIT('FOOT'" in up.replace(" ", "") or \
            "CONVERSION_BASED_UNIT('FEET'" in up.replace(" ", ""):
        return 0.3048
    return 1.0


def read_cis2(text: str, units: Optional[str] = None,
              sections_from=None) -> Tuple[object, List[str]]:
    from skyframe.core import frame_axes as _fa
    from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                     PointSupport, Story)
    inst, warns = parse_step(text)
    warns = [w for w in warns if "missing entity type" not in w]
    lf = _UNITS[units] if units in _UNITS else _detect_units(text)
    model = BuildingModel(name="CIS/2 import")
    _fa.as_new_model(model)

    def get(ref):
        return inst.get(ref.ref) if isinstance(ref, StepRef) else None

    def refs(e, prefix=None) -> List:
        out = []
        for a in e.args:
            for v in (a if isinstance(a, (list, tuple)) else [a]):
                t = get(v)
                if t is not None and (prefix is None or
                                      t.type.startswith(prefix)):
                    out.append(t)
        return out

    def strings(e) -> List[str]:
        return [a for a in e.args if isinstance(a, str)]

    def number(v) -> Optional[float]:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, StepTyped) and v.args and isinstance(
                v.args[0], (int, float)):
            return float(v.args[0])
        t = get(v)
        if t is not None and t.type.endswith("MEASURE_WITH_UNIT") and \
                t.args:
            return number(t.args[0])
        return None

    def point(e) -> Optional[Tuple[float, float, float]]:
        if e is None:
            return None
        if e.type == "CARTESIAN_POINT":
            co = next((a for a in e.args if isinstance(a, (list, tuple))),
                      None)
            if co is None:
                return None
            c = [float(v) for v in co] + [0.0] * (3 - len(co))
            return (c[0] * lf, c[1] * lf, c[2] * lf)
        for t in refs(e, "CARTESIAN_POINT"):
            return point(t)
        return None

    def direction(e) -> Optional[Tuple[float, float, float]]:
        if e is None or e.type != "DIRECTION":
            return None
        co = next((a for a in e.args if isinstance(a, (list, tuple))), None)
        if not co:
            return None
        c = [float(v) for v in co] + [0.0] * (3 - len(co))
        n = math.sqrt(sum(v * v for v in c)) or 1.0
        return tuple(v / n for v in c)

    # ---- materials / sections ------------------------------------------
    mat_name: Dict[int, str] = {}

    def material(e) -> str:
        if e is None:
            if "STEEL" not in model.materials:
                model.add_material(Material("STEEL", 2.0e8, 0.3, 77.0,
                                            material_type="steel"))
            return "STEEL"
        if e.id in mat_name:
            return mat_name[e.id]
        nm = next((s for s in strings(e) if s), f"MAT{e.id}")
        if nm not in model.materials:
            if sections_from is not None and nm in sections_from.materials:
                import copy
                model.add_material(copy.deepcopy(
                    sections_from.materials[nm]))
            else:
                model.add_material(Material(nm, 2.0e8, 0.3, 77.0,
                                            material_type="steel"))
        mat_name[e.id] = nm
        return nm

    sec_name: Dict[int, str] = {}

    def section(e, mat: str) -> Optional[str]:
        if e is None:
            return None
        if e.id in sec_name:
            return sec_name[e.id]
        names = [s for s in strings(e) if s and s != "$"]
        nm = names[0] if names else f"SEC{e.id}"
        sec = None
        for cand in names:
            if sections_from is not None and cand in sections_from.sections:
                import copy
                sec = copy.deepcopy(sections_from.sections[cand])
                if sec.material not in model.materials:
                    model.add_material(copy.deepcopy(
                        sections_from.materials[sec.material]))
                nm = cand
                break
            from skyframe.core.steel_db import section_from_db
            for std in _STDS:
                try:
                    sec = section_from_db(std, cand, mat, cand)
                    nm = cand
                    break
                except (KeyError, ValueError):
                    continue
            if sec is not None:
                break
        if sec is None:
            typ = _SEC_TYPES.get(e.type)
            vals = [v * lf for v in (number(a) for a in e.args
                                     if not isinstance(a, int)
                                     or isinstance(a, bool))
                    if v is not None and v > 0]
            try:
                sec = _param(nm, mat, typ, vals)
            except (ValueError, IndexError, ZeroDivisionError):
                sec = None
        if sec is None:
            sec = FrameSection.rectangular(nm, mat, 0.3, 0.3)
            warns.append(f"section {nm!r} ({e.type}): not recognised -- "
                         "0.3 x 0.3 m placeholder")
        while nm in model.sections and model.sections[nm].A != sec.A:
            nm += "_"
        sec.name = nm
        if nm not in model.sections:
            model.add_section(sec)
        sec_name[e.id] = nm
        return nm

    # ---- analysis model --------------------------------------------------
    members: List[Tuple[str, str, Tuple, Tuple]] = []
    restraints: Dict[Tuple[float, float, float], Tuple[int, ...]] = {}
    nodes = {i: e for i, e in inst.items() if e.type == "NODE"}
    elems = {i: e for i, e in inst.items()
             if e.type in ("ELEMENT_CURVE_SIMPLE", "ELEMENT_CURVE_COMPLEX",
                           "ELEMENT_CURVE")}
    conn: Dict[int, List[Tuple[int, int]]] = {}
    for i, e in inst.items():
        if e.type != "ELEMENT_NODE_CONNECTIVITY":
            continue
        nd = refs(e, "NODE")
        el = [t for t in refs(e, "ELEMENT")]
        ints = [a for a in e.args if isinstance(a, int)
                and not isinstance(a, bool)]
        if nd and el:
            order = ints[1] if len(ints) > 1 else (ints[0] if ints else
                                                    len(conn.get(el[0].id,
                                                                 [])) + 1)
            conn.setdefault(el[0].id, []).append((order, nd[0].id))
    for nid, ne in nodes.items():
        p = point(next(iter(refs(ne, "CARTESIAN_POINT")), None))
        bc = next(iter(refs(ne, "BOUNDARY_CONDITION")), None)
        if p is not None and bc is not None:
            flags = [a for a in bc.args if isinstance(a, StepEnum)]
            if len(flags) >= 6:
                restraints[p] = tuple(int(f.name in ("T", "TRUE"))
                                      for f in flags[:6])
    for eid, e in elems.items():
        ends = sorted(conn.get(eid, []))
        if len(ends) < 2:
            warns.append(f"element #{eid}: fewer than two connected nodes")
            continue
        pi = point(next(iter(refs(nodes[ends[0][1]], "CARTESIAN_POINT")),
                        None))
        pj = point(next(iter(refs(nodes[ends[-1][1]], "CARTESIAN_POINT")),
                        None))
        mat = material(next(iter(refs(e, "MATERIAL")), None))
        sec = section(next(iter(refs(e, "SECTION_PROFILE")), None), mat)
        if pi is None or pj is None or sec is None:
            warns.append(f"element #{eid}: missing geometry or section")
            continue
        name = next((s for s in strings(e) if s), f"E{eid}")
        members.append((name, sec, pi, pj))

    # ---- design model (when no analysis elements) ------------------------
    if not members:
        for i, e in inst.items():
            if e.type != "LOCATED_PART":
                continue
            part = next(iter(refs(e, "PART_PRISMATIC")), None)
            pl = next(iter(refs(e, "AXIS2_PLACEMENT_3D")), None)
            if pl is None:
                cs = next(iter(refs(e, "COORD_SYSTEM")), None)
                if cs is not None:
                    pl = next(iter(refs(cs, "AXIS2_PLACEMENT_3D")), None)
            if part is None or pl is None:
                warns.append(f"located part #{i}: missing part or "
                             "placement")
                continue
            org = point(next(iter(refs(pl, "CARTESIAN_POINT")), None))
            dirs = refs(pl, "DIRECTION")
            ax = direction(dirs[0]) if dirs else (0.0, 0.0, 1.0)
            lens = [number(a) for a in part.args       # skip item numbers
                    if not (isinstance(a, int) and not isinstance(a, bool))]
            lens = [v for v in lens if v is not None and v > 0]
            if org is None or not lens:
                warns.append(f"located part #{i}: no origin or length")
                continue
            L = lens[0] * lf
            mat = material(next(iter(refs(part, "MATERIAL")), None))
            sec = section(next(iter(refs(part, "SECTION_PROFILE")), None),
                          mat)
            if sec is None:
                warns.append(f"located part #{i}: no section profile")
                continue
            pj = tuple(org[k] + L * ax[k] for k in range(3))
            name = next((s for s in strings(e) if s), f"P{i}")
            members.append((name, sec, org, pj))

    if not members:
        return model, warns + ["no CIS/2 members (ELEMENT_CURVE_SIMPLE or "
                               "LOCATED_PART) found"]

    zs = sorted({round(c, 9) for _n, _s, a, b in members
                 for c in (a[2], b[2])})
    if len(zs) > 1:
        model.stories = [Story(f"Story{k}", zs[k] - zs[k - 1], zs[k])
                         for k in range(1, len(zs))]

    def story_of(z):
        for s in model.stories:
            if abs(s.elevation - z) < 1e-6:
                return s.name
        above = [s for s in model.stories if s.elevation > z]
        return min(above, key=lambda s: s.elevation).name if above else ""

    used = set()
    for name, sec, pi, pj in members:
        uid = name
        while uid in used:
            uid += "_"
        used.add(uid)
        dx, dy, dz = (pj[k] - pi[k] for k in range(3))
        kind = "column" if math.hypot(dx, dy) < 1e-9 else (
            "beam" if abs(dz) < 1e-9 else "brace")
        try:
            model.add_member(kind, sec, pi, pj,
                             story=story_of(max(pi[2], pj[2])), uid=uid)
        except ValueError as exc:
            warns.append(f"member {uid}: {exc}")
    if restraints:
        model.supports = [PointSupport(p, rr) for p, rr in
                          restraints.items()]
    _fa.sync_members(model)
    model.validate()
    return model, warns


def _param(name, mat, typ, vals):
    from skyframe.core.model import FrameSection
    from skyframe.core.section_ext import section_from_dims
    if typ in ("I", "channel", "tee"):
        d, b, tw, tf = vals[:4]
        return section_from_dims(name, mat, typ, {"d": d, "bf": b,
                                                  "tw": tw, "tf": tf})
    if typ == "angle":
        d, b, t = vals[:3]
        return section_from_dims(name, mat, "angle", {"d": d, "b": b,
                                                      "t": t})
    if typ == "rect":
        return FrameSection.rectangular(name, mat, vals[1], vals[0])
    if typ == "rect_tube":
        d, b, t = vals[:3]
        return section_from_dims(name, mat, "rect_tube", {"d": d, "b": b,
                                                          "t": t})
    if typ == "round_solid":
        return section_from_dims(name, mat, "round_solid", {"D": vals[0]})
    if typ == "round_tube":
        return section_from_dims(name, mat, "round_tube", {"D": vals[0],
                                                           "t": vals[1]})
    return None
