"""IFC4 reader for structural exchange (counterpart of :mod:`ifc_write`).

``read_ifc(text)`` -> ``(model, warnings)``.  Reads storeys, materials
(Pset_MaterialMechanical / Pset_MaterialCommon), the analysis model
(IfcStructuralCurveMember / IfcStructuralSurfaceMember /
IfcStructuralPointConnection: exact topology) and the physical elements
(IfcColumn / IfcBeam / IfcMember / IfcWall / IfcSlab with an
IfcExtrudedAreaSolid under ANY IfcLocalPlacement chain — full 3D
rotations).  Profiles map back to sections: a ``ProfileName`` found in a
steel database (aisc, eurocode, bs, as_nzs, is808 — in that order) whose
type matches the profile becomes that database shape; otherwise the IFC
parameters become a parametric shape (``shape`` + ``dims``); a plain
rectangle becomes a rectangular section.  Elements without an analysis
member use the physical solid (axis = extrusion, mid-surface = solid
mid-plane).

The legacy geometry-only reader :func:`skyframe.io.ifc.import_ifc` is
unchanged.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from skyframe.io.ifc import StepEnum, StepRef, StepTyped, parse_step

__all__ = ["read_ifc", "PROFILE_TYPES"]

PROFILE_TYPES = {"IFCISHAPEPROFILEDEF": "I",
                 "IFCASYMMETRICISHAPEPROFILEDEF": "I_mono",
                 "IFCUSHAPEPROFILEDEF": "channel",
                 "IFCLSHAPEPROFILEDEF": "angle",
                 "IFCTSHAPEPROFILEDEF": "tee",
                 "IFCRECTANGLEHOLLOWPROFILEDEF": "rect_tube",
                 "IFCCIRCLEHOLLOWPROFILEDEF": "round_tube",
                 "IFCCIRCLEPROFILEDEF": "round_solid",
                 "IFCRECTANGLEPROFILEDEF": "rect"}
_STDS = ("aisc", "eurocode", "bs", "as_nzs", "is808")


def _f(v) -> Optional[float]:
    if isinstance(v, StepTyped) and len(v.args) == 1:
        v = v.args[0]
    return float(v) if isinstance(v, (int, float)) and \
        not isinstance(v, bool) else None


def _mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _mv(a, v):
    return [sum(a[i][k] * v[k] for k in range(3)) for i in range(3)]


def _norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return [c / n for c in v] if n > 0 else list(v)


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


class _Ctx:
    def __init__(self, insts):
        self.i = insts

    def get(self, v):
        return self.i.get(v.ref) if isinstance(v, StepRef) else None

    def coords(self, v, n=3):
        e = self.get(v)
        if e is None or not e.args or not isinstance(e.args[0], list):
            return None
        out = [_f(c) for c in e.args[0]]
        if any(c is None for c in out):
            return None
        return (out + [0.0] * n)[:n]

    def a2p(self, v):
        """(origin, R) of an IfcAxis2Placement3D (columns = local x,y,z)."""
        e = self.get(v)
        if e is None or e.type != "IFCAXIS2PLACEMENT3D":
            return [0.0, 0.0, 0.0], [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        args = e.args + [None] * 3
        o = self.coords(args[0]) or [0.0, 0.0, 0.0]
        z = _norm(self.coords(args[1]) or [0.0, 0.0, 1.0])
        xr = self.coords(args[2]) or ([1.0, 0.0, 0.0] if abs(z[0]) < 0.9
                                      else [0.0, 1.0, 0.0])
        d = sum(xr[k] * z[k] for k in range(3))
        x = _norm([xr[k] - d * z[k] for k in range(3)])
        y = _cross(z, x)
        R = [[x[i], y[i], z[i]] for i in range(3)]
        return o, R

    def placement(self, v, depth=0):
        """Global (origin, R) of an IfcLocalPlacement chain."""
        e = self.get(v)
        if e is None or e.type != "IFCLOCALPLACEMENT" or depth > 64:
            return [0.0, 0.0, 0.0], [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        po, pR = self.placement(e.args[0], depth + 1) if e.args[0] \
            is not None else ([0.0, 0.0, 0.0],
                              [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
        o, R = self.a2p(e.args[1] if len(e.args) > 1 else None)
        go = [po[k] + _mv(pR, o)[k] for k in range(3)]
        return go, _mat_mul(pR, R)


def _apply(o, R, p):
    v = _mv(R, p)
    return [o[k] + v[k] for k in range(3)]


def _clean(v: float) -> float:
    r = round(v, 9)
    return r if abs(r - v) < 1e-9 else v


def read_ifc(text: str):
    from skyframe.core.model import BuildingModel, FrameSection
    from skyframe.io.e2k_read import _base_fixity, _opening
    insts, warnings = parse_step(text)
    c = _Ctx(insts)
    by_type: Dict[str, list] = {}
    for e in sorted(insts.values(), key=lambda e: e.id):
        by_type.setdefault(e.type, []).append(e)

    def s(v) -> str:
        return v if isinstance(v, str) else ""

    d: dict = {"name": "IFC Import", "frame_axis_convention": "etabs"}
    for e in by_type.get("IFCPROJECT", []):
        if s(e.args[2]):
            d["name"] = e.args[2]

    # ---- storeys --------------------------------------------------------- #
    lv = []
    for e in by_type.get("IFCBUILDINGSTOREY", []):
        el = _f(e.args[9]) if len(e.args) > 9 else None
        if el is None:
            warnings.append(f"IFCBUILDINGSTOREY #{e.id} has no Elevation")
            continue
        lv.append((el, s(e.args[2]) or f"Storey{e.id}"))
    lv.sort()
    stories = []
    for k in range(1, len(lv)):
        stories.append({"name": lv[k][1], "height": lv[k][0] - lv[k - 1][0],
                        "elevation": lv[k][0]})
    d["stories"] = stories
    story_names = {x["name"] for x in stories}

    # ---- materials ------------------------------------------------------- #
    mats: Dict[str, dict] = {}
    mat_by_id: Dict[int, str] = {}
    for e in by_type.get("IFCMATERIAL", []):
        name = s(e.args[0]) or f"MAT{e.id}"
        mt = (s(e.args[2]) if len(e.args) > 2 else "").lower()
        from skyframe.core.model import MATERIAL_TYPES
        mats[name] = {"name": name, "E": 25e6,
                      "material_type": mt if mt in MATERIAL_TYPES
                      else ("steel" if "steel" in mt else "concrete")}
        mat_by_id[e.id] = name
    for e in by_type.get("IFCMATERIALPROPERTIES", []):
        mref = e.args[3] if len(e.args) > 3 else None
        mname = mat_by_id.get(mref.ref) if isinstance(mref, StepRef) \
            else None
        if mname is None:
            continue
        for pr in e.args[2] or []:
            p = c.get(pr)
            if p is None or len(p.args) < 3:
                continue
            val = _f(p.args[2])
            if val is None:
                continue
            if p.args[0] == "YoungModulus":
                mats[mname]["E"] = val / 1e3
            elif p.args[0] == "PoissonRatio":
                mats[mname]["nu"] = val
            elif p.args[0] == "MassDensity":
                mats[mname]["unit_weight"] = val / 1e3 * 9.80665
    elem_mat: Dict[int, str] = {}
    for e in by_type.get("IFCRELASSOCIATESMATERIAL", []):
        m_ = e.args[5] if len(e.args) > 5 else None
        name = mat_by_id.get(m_.ref) if isinstance(m_, StepRef) else None
        for r in e.args[4] or []:
            if isinstance(r, StepRef) and name:
                elem_mat[r.ref] = name
    if not mats:
        mats["IFC"] = {"name": "IFC", "E": 25e6}
    d["materials"] = mats
    default_mat = next(iter(mats))

    # ---- physical elements by name -------------------------------------- #
    phys: Dict[str, dict] = {}
    for typ in ("IFCCOLUMN", "IFCBEAM", "IFCMEMBER", "IFCWALL",
                "IFCWALLSTANDARDCASE", "IFCSLAB"):
        for e in by_type.get(typ, []):
            args = e.args + [None] * 9
            name = s(args[2]) or f"{typ[3]}{e.id}"
            solid = None
            axis_poly = None
            pds = c.get(args[6])
            if pds is not None and len(pds.args) > 2:
                for rr in pds.args[2] or []:
                    rep = c.get(rr)
                    if rep is None:
                        continue
                    for it in rep.args[3] or []:
                        ie = c.get(it)
                        if ie is not None and ie.type == \
                                "IFCEXTRUDEDAREASOLID":
                            solid = ie
                        elif ie is not None and ie.type == "IFCPOLYLINE":
                            axis_poly = ie
            phys[name] = {"type": typ, "id": e.id, "e": e,
                          "section": s(args[4]), "story": s(args[7]),
                          "desc": s(args[3]), "solid": solid,
                          "axis": axis_poly,
                          "mat": elem_mat.get(e.id, default_mat)}

    # ---- sections from profiles ----------------------------------------- #
    secs: Dict[str, dict] = {}
    pprops: Dict[int, Dict[str, float]] = {}
    for e in by_type.get("IFCPROFILEPROPERTIES", []):
        pref = e.args[3] if len(e.args) > 3 else None
        if not isinstance(pref, StepRef):
            continue
        vals = pprops.setdefault(pref.ref, {})
        for pr in e.args[2] or []:
            p = c.get(pr)
            if p is not None and len(p.args) > 2 and _f(p.args[2]) \
                    is not None:
                vals[str(p.args[0])] = _f(p.args[2])

    def section_for(ph: dict) -> Optional[str]:
        solid = ph["solid"]
        name = ph["section"]
        prof = c.get(solid.args[0]) if solid is not None else None
        if prof is None:
            return name if name in secs else None
        name = name or s(prof.args[1]) or f"IFC-P{prof.id}"
        if name in secs:
            return name
        mat = ph["mat"]
        a = prof.args + [None] * 14
        g = _f
        typ = PROFILE_TYPES.get(prof.type)
        pname = s(a[1])
        sec = None
        if typ is not None and typ != "rect" and pname:
            from skyframe.core import steel_db
            for std in _STDS:
                try:
                    rec = steel_db.shape(std, pname)
                except Exception:              # noqa: BLE001
                    continue
                if rec.get("type") == typ:
                    sec = steel_db.section_from_db(std, rec["name"], mat,
                                                   name)
                    break
        dims = None
        if sec is None and typ is not None:
            if typ == "I":
                dims = {"d": g(a[4]), "bf": g(a[3]), "tf": g(a[6]),
                        "tw": g(a[5])}
                if g(a[7]):
                    dims["r"] = g(a[7])
            elif typ == "I_mono":
                dims = {"d": g(a[4]), "bf_top": g(a[8]), "tf_top": g(a[9]),
                        "bf_bot": g(a[3]), "tf_bot": g(a[6]),
                        "tw": g(a[5])}
            elif typ in ("channel", "tee"):
                dims = {"d": g(a[3]), "bf": g(a[4]), "tw": g(a[5]),
                        "tf": g(a[6])}
            elif typ == "angle":
                dims = {"d": g(a[3]), "b": g(a[4]), "t": g(a[5])}
                if g(a[6]):
                    dims["r"] = g(a[6])
            elif typ == "rect_tube":
                dims = {"d": g(a[4]), "b": g(a[3]), "t": g(a[5])}
                if g(a[7]):
                    dims["r_out"] = g(a[7])
            elif typ == "round_tube":
                dims = {"D": 2.0 * g(a[3]), "t": g(a[4])}
            elif typ == "round_solid":
                dims = {"D": 2.0 * g(a[3])}
            elif typ == "rect":
                sec = FrameSection.rectangular(name, mat, g(a[3]), g(a[4]))
                pp = pprops.get(prof.id, {})
                vals = (pp.get("CrossSectionArea"),
                        pp.get("MomentOfInertiaY"),
                        pp.get("MomentOfInertiaZ"),
                        pp.get("TorsionalConstantX"))
                if None not in vals and vals != (sec.A, sec.I33, sec.I22,
                                                 sec.J):
                    sec = FrameSection(name, mat, *vals, b=g(a[3]),
                                       h=g(a[4]))
                    sec.As2 = pp.get("ShearAreaZ")
                    sec.As3 = pp.get("ShearAreaY")
            if dims is not None:
                try:
                    from skyframe.core.section_ext import section_from_dims
                    sec = section_from_dims(name, mat, typ, dims)
                except Exception as exc:      # noqa: BLE001
                    warnings.append(f"profile {pname!r}: {exc}")
        if sec is None:
            warnings.append(f"profile {prof.type} of {name!r} not "
                            "supported — 0.3x0.6 rectangle used")
            sec = FrameSection.rectangular(name, mat, 0.3, 0.6)
        secs[name] = sec.to_dict()
        return name

    # ---- frame members --------------------------------------------------- #
    from skyframe.core.frame_axes import frame_local_axes
    members = []
    done = set()
    for e in by_type.get("IFCSTRUCTURALCURVEMEMBER", []):
        args = e.args + [None] * 10
        name = s(args[2]) or f"M{e.id}"
        pds = c.get(args[6])
        ends = None
        if pds is not None:
            for rr in pds.args[2] or []:
                rep = c.get(rr)
                for it in (rep.args[3] if rep is not None else []) or []:
                    ed = c.get(it)
                    if ed is not None and ed.type == "IFCEDGE":
                        v1, v2 = c.get(ed.args[0]), c.get(ed.args[1])
                        if v1 is not None and v2 is not None:
                            ends = (c.coords(v1.args[0]),
                                    c.coords(v2.args[0]))
        if ends is None or None in ends:
            warnings.append(f"structural member {name!r}: no edge; skipped")
            continue
        ph = phys.get(name, {"section": s(args[4]), "solid": None,
                             "story": s(args[7]), "mat": default_mat})
        sec = section_for(ph) if ph.get("solid") is not None else \
            (s(args[4]) if s(args[4]) in secs else None)
        if sec is None:
            sec = s(args[4]) or "IFC-DEFAULT"
            if sec not in secs:
                secs[sec] = FrameSection.rectangular(
                    sec, default_mat, 0.3, 0.6).to_dict()
        kind = s(args[3]) if s(args[3]) in ("column", "beam", "brace") \
            else {"IFCCOLUMN": "column", "IFCBEAM": "beam"}.get(
                ph.get("type"), "brace")
        angle = 0.0
        yv = c.coords(args[9]) if args[9] is not None else None
        if yv is not None:
            x0, y0, z0, _v, _vt = frame_local_axes(ends[0], ends[1], 0.0,
                                                   "etabs")
            angle = _clean(math.degrees(math.atan2(
                sum(yv[k] * z0[k] for k in range(3)),
                sum(yv[k] * y0[k] for k in range(3)))))
            if abs(angle) < 1e-9:
                angle = 0.0
        story = ph.get("story", "")
        members.append({"uid": name, "kind": kind, "section": sec,
                        "pi": ends[0], "pj": ends[1],
                        "story": story if story in story_names else "",
                        "angle": angle})
        done.add(name)
    for name, ph in phys.items():
        if name in done or ph["type"] not in ("IFCCOLUMN", "IFCBEAM",
                                              "IFCMEMBER"):
            continue
        solid = ph["solid"]
        if solid is None:
            warnings.append(f"{ph['type']} {name!r}: no extruded solid")
            continue
        o, R = c.placement(ph["e"].args[5])
        so, sR = c.a2p(solid.args[1])
        go = _apply(o, R, so)
        gR = _mat_mul(R, sR)
        dirn = c.coords(solid.args[2]) or [0.0, 0.0, 1.0]
        L = _f(solid.args[3]) or 0.0
        pj = _apply(go, gR, [dirn[k] * L for k in range(3)])
        sec = section_for(ph)
        kind = {"IFCCOLUMN": "column", "IFCBEAM": "beam"}.get(ph["type"],
                                                              "brace")
        story = ph["story"]
        members.append({"uid": name, "kind": kind, "section": sec,
                        "pi": [_clean(v) for v in go],
                        "pj": [_clean(v) for v in pj],
                        "story": story if story in story_names else ""})
    d["members"] = members
    d["sections"] = secs

    # ---- shells ------------------------------------------------------------ #
    shells = []
    ssecs: Dict[str, dict] = {}
    for e in by_type.get("IFCSTRUCTURALSURFACEMEMBER", []):
        args = e.args + [None] * 10
        name = s(args[2]) or f"A{e.id}"
        t = _f(args[9]) or 0.2
        outer, holes = None, []
        pds = c.get(args[6])
        for rr in (pds.args[2] if pds is not None else []) or []:
            rep = c.get(rr)
            for it in (rep.args[3] if rep is not None else []) or []:
                fs = c.get(it)
                if fs is None or fs.type not in ("IFCFACESURFACE",
                                                 "IFCFACE"):
                    continue
                for b in fs.args[0] or []:
                    be = c.get(b)
                    lp = c.get(be.args[0]) if be is not None else None
                    if lp is None:
                        continue
                    pts = [c.coords(p) for p in lp.args[0] or []]
                    if be.type == "IFCFACEOUTERBOUND":
                        outer = pts
                    else:
                        holes.append(pts)
        if not outer:
            warnings.append(f"surface member {name!r}: no outer bound")
            continue
        ph = phys.get(name, {})
        secname = s(args[4]) or f"IFC-T{t:g}"
        mat = ph.get("mat", default_mat)
        if secname not in ssecs:
            ssecs[secname] = {"name": secname, "material": mat,
                              "thickness": t}
        kind = s(args[3]) if s(args[3]) in ("wall", "slab") else (
            "wall" if ph.get("type", "").startswith("IFCWALL") else "slab")
        story = ph.get("story", "")
        behavior = ph.get("desc") if ph.get("desc") in ("shell",
                                                        "membrane") \
            else "shell"
        shells.append({"uid": name, "kind": kind, "behavior": behavior,
                       "section": secname, "corners": outer,
                       "story": story if story in story_names else "",
                       "openings": [_opening(outer, h) for h in holes]})
        done.add(name)
    for name, ph in phys.items():
        if name in done or ph["type"] not in ("IFCWALL", "IFCSLAB",
                                              "IFCWALLSTANDARDCASE"):
            continue
        solid = ph["solid"]
        prof = c.get(solid.args[0]) if solid is not None else None
        if prof is None or prof.type not in (
                "IFCARBITRARYCLOSEDPROFILEDEF",
                "IFCARBITRARYPROFILEDEFWITHVOIDS"):
            warnings.append(f"{ph['type']} {name!r}: profile not an "
                            "arbitrary closed profile; skipped")
            continue
        t = _f(solid.args[3]) or 0.2
        o, R = c.placement(ph["e"].args[5])
        so, sR = c.a2p(solid.args[1])
        go, gR = _apply(o, R, so), _mat_mul(R, sR)

        def loop(curve):
            ce = c.get(curve)
            pts = [c.coords(p, 2) for p in (ce.args[0] if ce else [])]
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            return [[_clean(v) for v in _apply(go, gR, [p[0], p[1],
                                                        t / 2.0])]
                    for p in pts]
        outer = loop(prof.args[2])
        holes = [loop(h) for h in (prof.args[3] if len(prof.args) > 3
                                   and isinstance(prof.args[3], list)
                                   else [])]
        secname = ph["section"] or f"IFC-T{t:g}"
        if secname not in ssecs:
            ssecs[secname] = {"name": secname, "material": ph["mat"],
                              "thickness": t}
        story = ph["story"]
        shells.append({"uid": name, "kind": "wall" if "WALL" in ph["type"]
                       else "slab", "behavior": "shell", "section": secname,
                       "corners": outer,
                       "story": story if story in story_names else "",
                       "openings": [_opening(outer, h) for h in holes]})
    d["shells"] = shells
    d["shell_sections"] = ssecs

    # ---- supports --------------------------------------------------------- #
    supports = []
    for e in by_type.get("IFCSTRUCTURALPOINTCONNECTION", []):
        args = e.args + [None] * 9
        pds = c.get(args[6])
        p = None
        for rr in (pds.args[2] if pds is not None else []) or []:
            rep = c.get(rr)
            for it in (rep.args[3] if rep is not None else []) or []:
                vp = c.get(it)
                if vp is not None and vp.type == "IFCVERTEXPOINT":
                    p = c.coords(vp.args[0])
        cond = c.get(args[7])
        if p is None or cond is None:
            continue
        rr_ = []
        for v in cond.args[1:7]:
            if isinstance(v, StepTyped):
                v = v.args[0] if v.args else None
            rr_.append(1 if isinstance(v, StepEnum) and v.name == "T"
                       else 0)
        if any(rr_):
            supports.append({"point": p, "restraints": rr_})
    d["links"] = []
    _base_fixity(d, supports, members, shells, [])
    for k in ("patterns", "cases", "combos"):
        d.setdefault(k, {})
    mdl = BuildingModel.from_dict(d)
    return mdl, warnings
