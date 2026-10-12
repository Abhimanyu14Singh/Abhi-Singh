"""IFC4 (ISO-10303-21 STEP text) EXPORT, dependency-free.

``export_ifc(model, include=None)`` -> ``(text, report)``.  Written:

* spatial structure: IfcProject (SI units: metre, radian, pascal, kg/m3)
  -> IfcSite -> IfcBuilding -> one IfcBuildingStorey per story level (the
  base level included; ``Elevation`` = level elevation);
* materials: IfcMaterial + IfcMaterialProperties ``Pset_MaterialMechanical``
  (YoungModulus [Pa], PoissonRatio) and ``Pset_MaterialCommon``
  (MassDensity [kg/m3]);
* PHYSICAL members: IfcColumn / IfcBeam / IfcMember (Name = uid,
  ObjectType = section name, Tag = story) with an IfcExtrudedAreaSolid of
  a parameterised profile — IfcIShapeProfileDef, IfcAsymmetricIShape-
  ProfileDef, IfcUShapeProfileDef, IfcLShapeProfileDef, IfcTShapeProfile-
  Def, IfcRectangleHollowProfileDef, IfcCircleHollowProfileDef,
  IfcCircleProfileDef, IfcRectangleProfileDef (``ProfileName`` = the
  database shape name for database sections, else the section name);
  IfcWall / IfcSlab extruded from the mid-surface polygon (openings as
  inner bounds of an IfcArbitraryProfileDefWithVoids);
* the ANALYSIS model: IfcStructuralAnalysisModel grouping
  IfcStructuralCurveMember (exact end points as IfcVertexPoint, ``Axis`` =
  local 2), IfcStructuralSurfaceMember (exact corner / opening loops,
  ``Thickness``) and IfcStructuralPointConnection supports with an
  IfcBoundaryNodeCondition.

Profile placement: the solid's local Z is member local 1 (i -> j), its
local X is -local 3 and Y = local 2 (profile depth along local 2).
Sections without an IFC parameterised profile (double angle, concrete
T / L, built-up, designer, general) are written as their b x h bounding
rectangle and listed in ``report["skipped"]``.
"""

from __future__ import annotations

import hashlib
import math
from typing import Dict, List, Optional, Tuple

__all__ = ["export_ifc", "step_real", "ifc_guid"]

_CHARS = ("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
          "_$")


def ifc_guid(seed: str) -> str:
    """Deterministic 22-character IFC GlobalId from a seed string."""
    n = int.from_bytes(hashlib.md5(seed.encode("utf-8")).digest(), "big")
    out = []
    for _ in range(22):
        out.append(_CHARS[n % 64])
        n //= 64
    return "".join(reversed(out))


def step_real(v) -> str:
    """STEP real literal (always with a decimal point)."""
    r = repr(float(v))
    if "e" in r or "E" in r:
        mant, exp = r.lower().split("e")
        if "." not in mant:
            mant += "."
        return f"{mant}E{exp}"
    if "." not in r:
        r += "."
    return r


def _s(text) -> str:
    return "'" + str(text).replace("\\", "\\\\").replace("'", "''") + "'"


class _Step:
    def __init__(self):
        self.lines: List[str] = []
        self.n = 0

    def add(self, entity: str, *args) -> str:
        self.n += 1
        self.lines.append(f"#{self.n}={entity}({','.join(args)});")
        return f"#{self.n}"


def _vec(ref: str, v) -> str:
    return "(" + ",".join(step_real(x) for x in v) + ")"


def _norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def export_ifc(model, include: Optional[dict] = None,
               revit: bool = False) -> Tuple[str, dict]:
    """``revit=True`` adds the Revit-friendly entities of
    :mod:`skyframe.io.ifc_revit` (default output unchanged)."""
    from skyframe.core import frame_axes as _fa
    from skyframe.core.model import BuildingModel
    if isinstance(model, dict):
        model = BuildingModel.from_dict(model)
    inc = {"physical": True, "analysis": True, "supports": True}
    inc.update({k: bool(v) for k, v in (include or {}).items()
                if k in inc})
    if _fa.model_convention(model) != "etabs":
        import copy
        model = copy.deepcopy(model)
        _fa.convert_model_to_etabs(model)
    m = model
    if revit:
        from skyframe.io.ifc_revit import revit_profile_name as rv_profile
    st = _Step()
    skipped: List[str] = []
    counts: Dict[str, int] = {}

    def cnt(k, n=1):
        counts[k] = counts.get(k, 0) + n

    owner = "$"
    pt_cache: Dict[Tuple[float, ...], str] = {}

    def point(p) -> str:
        key = tuple(float(c) for c in p)
        if key not in pt_cache:
            pt_cache[key] = st.add("IFCCARTESIANPOINT", _vec("", key))
        return pt_cache[key]

    def direction(v) -> str:
        return st.add("IFCDIRECTION", _vec("", v))

    def placement3d(origin, axis=None, ref=None) -> str:
        return st.add("IFCAXIS2PLACEMENT3D", point(origin),
                      direction(axis) if axis is not None else "$",
                      direction(ref) if ref is not None else "$")

    origin = placement3d((0.0, 0.0, 0.0))
    ctx = st.add("IFCGEOMETRICREPRESENTATIONCONTEXT", "$", "'Model'", "3",
                 "1.E-05", origin, "$")
    rv = None                                   # Revit additions (ifc_revit)
    if revit:
        from skyframe.io.ifc_revit import RevitIfc
        rv = RevitIfc(st, ctx, ifc_guid, _s, step_real)
    body_ctx, axis_ctx = (rv.body, rv.axis) if rv else (ctx, ctx)
    units = [st.add("IFCSIUNIT", "*", ".LENGTHUNIT.", "$", ".METRE."),
             st.add("IFCSIUNIT", "*", ".PLANEANGLEUNIT.", "$", ".RADIAN."),
             st.add("IFCSIUNIT", "*", ".PRESSUREUNIT.", "$", ".PASCAL."),
             st.add("IFCSIUNIT", "*", ".FORCEUNIT.", "$", ".NEWTON.")]
    ua = st.add("IFCUNITASSIGNMENT", "(" + ",".join(units) + ")")
    project = st.add("IFCPROJECT", _s(ifc_guid("project" + m.name)), owner,
                     _s(m.name), "$", "$", "$", "$", f"({ctx})", ua)
    site_pl = st.add("IFCLOCALPLACEMENT", "$", origin)
    site = st.add("IFCSITE", _s(ifc_guid("site" + m.name)), owner,
                  "'Site'", "$", "$", site_pl, "$", "$", ".ELEMENT.", "$",
                  "$", "$", "$", "$")
    bld = st.add("IFCBUILDING", _s(ifc_guid("bld" + m.name)), owner,
                 _s(m.name), "$", "$", site_pl, "$", "$", ".ELEMENT.", "$",
                 "$", "$")
    st.add("IFCRELAGGREGATES", _s(ifc_guid("agg1" + m.name)), owner, "$",
           "$", project, f"({site})")
    st.add("IFCRELAGGREGATES", _s(ifc_guid("agg2" + m.name)), owner, "$",
           "$", site, f"({bld})")

    # ---- storeys (base + every story) ---------------------------------- #
    levels: List[Tuple[str, float]] = []
    if m.stories:
        base = m.stories[0].elevation - m.stories[0].height
        bn = "Base"
        while any(s.name == bn for s in m.stories):
            bn += "_"
        levels.append((bn, base))
    levels += [(s.name, s.elevation) for s in m.stories]
    storey_of: Dict[str, str] = {}
    storeys = []
    for name, elev in sorted(levels, key=lambda t: t[1]):
        sp = rv.storey_placement(elev, placement3d) if rv else \
            st.add("IFCLOCALPLACEMENT", "$", origin)
        sid = st.add("IFCBUILDINGSTOREY", _s(ifc_guid("storey" + name)),
                     owner, _s(name), "$", "$", sp, "$",
                     _s(name) if rv else "$", ".ELEMENT.", step_real(elev))
        storey_of[name] = sid
        storeys.append(sid)
    if storeys:
        st.add("IFCRELAGGREGATES", _s(ifc_guid("agg3" + m.name)), owner,
               "$", "$", bld, "(" + ",".join(storeys) + ")")
    cnt("storeys", len(storeys))

    # ---- materials ------------------------------------------------------ #
    mat_of: Dict[str, str] = {}
    for mt in m.materials.values():
        mnm, mcat = rv.material(mt) if rv else (mt.name, mt.material_type)
        mid = st.add("IFCMATERIAL", _s(mnm), "$", _s(mcat))
        mat_of[mt.name] = mid
        props = [
            st.add("IFCPROPERTYSINGLEVALUE", "'YoungModulus'", "$",
                   f"IFCMODULUSOFELASTICITYMEASURE({step_real(mt.E * 1e3)})",
                   "$"),
            st.add("IFCPROPERTYSINGLEVALUE", "'PoissonRatio'", "$",
                   f"IFCPOSITIVERATIOMEASURE({step_real(mt.nu)})", "$")]
        st.add("IFCMATERIALPROPERTIES", "'Pset_MaterialMechanical'", "$",
               "(" + ",".join(props) + ")", mid)
        dens = st.add("IFCPROPERTYSINGLEVALUE", "'MassDensity'", "$",
                      "IFCMASSDENSITYMEASURE("
                      f"{step_real(mt.mass_per_volume * 1e3)})", "$")
        st.add("IFCMATERIALPROPERTIES", "'Pset_MaterialCommon'", "$",
               f"({dens})", mid)
    cnt("materials", len(m.materials))

    # ---- profiles -------------------------------------------------------- #
    prof_of: Dict[str, str] = {}
    pos2d = st.add("IFCAXIS2PLACEMENT2D", st.add("IFCCARTESIANPOINT",
                                                 "(0.,0.)"), "$")

    def profile(sec) -> str:
        if sec.name in prof_of:
            return prof_of[sec.name]
        R = step_real
        pname = _s(rv_profile(sec) if rv else (
            sec.db_ref["name"] if sec.db_ref else sec.name))
        d = {k: v for k, v in (sec.dims or {}).items()
             if not isinstance(v, str)}
        typ = sec.shape if isinstance(sec.dims, dict) else None
        g = d.get
        pid = None
        if typ == "I":
            pid = st.add("IFCISHAPEPROFILEDEF", ".AREA.", pname, pos2d,
                         R(g("bf")), R(g("d")), R(g("tw")), R(g("tf")),
                         R(g("r")) if g("r") else "$", "$", "$")
        elif typ == "I_mono":
            pid = st.add("IFCASYMMETRICISHAPEPROFILEDEF", ".AREA.", pname,
                         pos2d, R(g("bf_bot")), R(g("d")), R(g("tw")),
                         R(g("tf_bot")), "$", R(g("bf_top")),
                         R(g("tf_top")), "$", "$", "$", "$", "$")
        elif typ == "channel":
            pid = st.add("IFCUSHAPEPROFILEDEF", ".AREA.", pname, pos2d,
                         R(g("d")), R(g("bf")), R(g("tw")), R(g("tf")),
                         "$", "$", "$")
        elif typ == "angle":
            pid = st.add("IFCLSHAPEPROFILEDEF", ".AREA.", pname, pos2d,
                         R(g("d")), R(g("b")), R(g("t")),
                         R(g("r")) if g("r") else "$", "$", "$")
        elif typ == "tee":
            pid = st.add("IFCTSHAPEPROFILEDEF", ".AREA.", pname, pos2d,
                         R(g("d")), R(g("bf")), R(g("tw")), R(g("tf")),
                         "$", "$", "$", "$", "$")
        elif typ == "rect_tube":
            pid = st.add("IFCRECTANGLEHOLLOWPROFILEDEF", ".AREA.", pname,
                         pos2d, R(g("b")), R(g("d")), R(g("t")), "$",
                         R(g("r_out")) if g("r_out") else "$")
        elif typ == "round_tube":
            pid = st.add("IFCCIRCLEHOLLOWPROFILEDEF", ".AREA.", pname, pos2d,
                         R(g("D") / 2.0), R(g("t")))
        elif typ == "round_solid":
            pid = st.add("IFCCIRCLEPROFILEDEF", ".AREA.", pname, pos2d,
                         R(g("D") / 2.0))
        elif typ in ("rect_solid", "plate"):
            pid = st.add("IFCRECTANGLEPROFILEDEF", ".AREA.", pname, pos2d,
                         R(g("b")), R(g("d")))
        if pid is None:
            from skyframe.core.model import FrameSection
            b = sec.b if sec.b > 0 else math.sqrt(sec.A)
            h = sec.h if sec.h > 0 else math.sqrt(sec.A)
            pid = st.add("IFCRECTANGLEPROFILEDEF", ".AREA.", pname, pos2d,
                         R(b), R(h))
            ref = FrameSection.rectangular(sec.name, sec.material, b, h)
            if sec.shape is not None or (ref.A, ref.I33, ref.I22, ref.J) \
                    != (sec.A, sec.I33, sec.I22, sec.J):
                skipped.append(f"section {sec.name}: no IFC parameterised "
                               "profile — bounding rectangle with its "
                               "properties in Pset_ProfileMechanical")
        props = [("CrossSectionArea", "IFCAREAMEASURE", sec.A),
                 ("MomentOfInertiaY", "IFCMOMENTOFINERTIAMEASURE", sec.I33),
                 ("MomentOfInertiaZ", "IFCMOMENTOFINERTIAMEASURE", sec.I22),
                 ("TorsionalConstantX", "IFCMOMENTOFINERTIAMEASURE", sec.J)]
        if sec.As2:
            props.append(("ShearAreaZ", "IFCAREAMEASURE", sec.As2))
        if sec.As3:
            props.append(("ShearAreaY", "IFCAREAMEASURE", sec.As3))
        pv = [st.add("IFCPROPERTYSINGLEVALUE", _s(k), "$",
                     f"{t}({R(v)})", "$") for k, t, v in props]
        st.add("IFCPROFILEPROPERTIES", "'Pset_ProfileMechanical'", "$",
               "(" + ",".join(pv) + ")", pid)
        prof_of[sec.name] = pid
        return pid

    rel_mat: Dict[str, List[str]] = {}
    contained: Dict[str, List[str]] = {}
    analysis_items: List[str] = []

    def story_level(story: str, z: float) -> str:
        if story in storey_of:
            return story
        best = min(levels, key=lambda t: abs(t[1] - z)) if levels else None
        return best[0] if best else ""

    # ---- frame members ----------------------------------------------------- #
    for mem in m.members:
        sec = m.sections.get(mem.section)
        if sec is None:
            continue
        x, y, z, _v, _vert = _fa.member_local_axes(mem)
        L = mem.length
        ent = {"column": "IFCCOLUMN", "beam": "IFCBEAM"}.get(mem.kind,
                                                           "IFCMEMBER")
        pdt = {"column": ".COLUMN.", "beam": ".BEAM."}.get(mem.kind,
                                                          ".BRACE.")
        lev = story_level(mem.story, max(mem.pi[2], mem.pj[2]))
        if inc["physical"]:
            pl = st.add("IFCLOCALPLACEMENT", "$",
                        placement3d(mem.pi, x, tuple(-c for c in z)))
            solid = st.add("IFCEXTRUDEDAREASOLID", profile(sec),
                           placement3d((0.0, 0.0, 0.0)),
                           direction((0.0, 0.0, 1.0)), step_real(L))
            axis = st.add("IFCPOLYLINE", f"({point((0.0, 0.0, 0.0))},"
                          f"{point((0.0, 0.0, L))})")
            reps = [st.add("IFCSHAPEREPRESENTATION", body_ctx, "'Body'",
                           "'SweptSolid'", f"({solid})"),
                    st.add("IFCSHAPEREPRESENTATION", axis_ctx, "'Axis'",
                           "'Curve3D'", f"({axis})")]
            pds = st.add("IFCPRODUCTDEFINITIONSHAPE", "$", "$",
                         "(" + ",".join(reps) + ")")
            eid = st.add(ent, _s(ifc_guid("m" + mem.uid)), owner,
                         _s(mem.uid), "$", _s(mem.section), pl, pds,
                         _s(mem.story), pdt)
            contained.setdefault(lev, []).append(eid)
            rel_mat.setdefault(sec.material, []).append(eid)
            if rv:
                rv.member(eid, mem, sec)
        if inc["analysis"]:
            v1 = st.add("IFCVERTEXPOINT", point(mem.pi))
            v2 = st.add("IFCVERTEXPOINT", point(mem.pj))
            edge = st.add("IFCEDGE", v1, v2)
            trep = st.add("IFCTOPOLOGYREPRESENTATION", ctx, "'Reference'",
                          "'Edge'", f"({edge})")
            pds = st.add("IFCPRODUCTDEFINITIONSHAPE", "$", "$", f"({trep})")
            spl = st.add("IFCLOCALPLACEMENT", "$", origin)
            sm = st.add("IFCSTRUCTURALCURVEMEMBER",
                        _s(ifc_guid("sm" + mem.uid)), owner, _s(mem.uid),
                        _s(mem.kind), _s(mem.section), spl, pds,
                        "$", ".RIGID_JOINED_MEMBER.", direction(y))
            analysis_items.append(sm)
    cnt("frames", len(m.members))

    # ---- walls / slabs ------------------------------------------------- #
    shell_mat: Dict[str, str] = {}
    for r in m.shells:
        ss = m.shell_sections.get(r.section)
        t = ss.total_thickness if ss is not None else 0.2
        c = [tuple(float(v) for v in p) for p in r.corners]
        n = _norm(_cross(tuple(c[1][k] - c[0][k] for k in range(3)),
                         tuple(c[-1][k] - c[0][k] for k in range(3))))
        xax = _norm(tuple(c[1][k] - c[0][k] for k in range(3)))
        yax = _cross(n, xax)
        o = c[0]

        def loc(p):
            dv = tuple(p[k] - o[k] for k in range(3))
            return (sum(dv[k] * xax[k] for k in range(3)),
                    sum(dv[k] * yax[k] for k in range(3)))

        holes = []
        for op in r.openings:
            if op.polygon:
                holes.append([tuple(p) for p in op.polygon])
            else:
                holes.append([r.map_uv(op.u0, op.v0), r.map_uv(op.u1, op.v0),
                              r.map_uv(op.u1, op.v1), r.map_uv(op.u0, op.v1)])
        lev = story_level(r.story, max(p[2] for p in c))
        if inc["physical"]:
            def poly2(pts):
                ids = [st.add("IFCCARTESIANPOINT",
                              "(" + ",".join(step_real(v) for v in loc(p))
                              + ")") for p in pts]
                return st.add("IFCPOLYLINE", "(" + ",".join(ids + ids[:1])
                              + ")")
            outer = poly2(c)
            if holes:
                prof = st.add("IFCARBITRARYPROFILEDEFWITHVOIDS", ".AREA.",
                              _s(r.uid), outer,
                              "(" + ",".join(poly2(h) for h in holes) + ")")
            else:
                prof = st.add("IFCARBITRARYCLOSEDPROFILEDEF", ".AREA.",
                              _s(r.uid), outer)
            base = tuple(o[k] - n[k] * t / 2.0 for k in range(3))
            pl = st.add("IFCLOCALPLACEMENT", "$", placement3d(base, n, xax))
            solid = st.add("IFCEXTRUDEDAREASOLID", prof,
                           placement3d((0.0, 0.0, 0.0)),
                           direction((0.0, 0.0, 1.0)), step_real(t))
            rep = st.add("IFCSHAPEREPRESENTATION", body_ctx, "'Body'",
                         "'SweptSolid'", f"({solid})")
            pds = st.add("IFCPRODUCTDEFINITIONSHAPE", "$", "$", f"({rep})")
            if r.kind == "wall":
                eid = st.add("IFCWALL", _s(ifc_guid("w" + r.uid)), owner,
                             _s(r.uid), _s(r.behavior), _s(r.section), pl,
                             pds, _s(r.story), ".SHEAR.")
            else:
                eid = st.add("IFCSLAB", _s(ifc_guid("s" + r.uid)), owner,
                             _s(r.uid), _s(r.behavior), _s(r.section), pl,
                             pds, _s(r.story), ".FLOOR.")
            contained.setdefault(lev, []).append(eid)
            if ss is not None:
                rel_mat.setdefault(ss.material, []).append(eid)
            if rv:
                rv.shell(eid, r, ss)
        if inc["analysis"]:
            def loop(pts):
                return st.add("IFCPOLYLOOP", "(" + ",".join(
                    point(p) for p in pts) + ")")
            bounds = [st.add("IFCFACEOUTERBOUND", loop(c), ".T.")]
            bounds += [st.add("IFCFACEBOUND", loop(h), ".T.") for h in holes]
            plane = st.add("IFCPLANE", placement3d(o, n, xax))
            face = st.add("IFCFACESURFACE", "(" + ",".join(bounds) + ")",
                          plane, ".T.")
            trep = st.add("IFCTOPOLOGYREPRESENTATION", ctx, "'Reference'",
                          "'Face'", f"({face})")
            pds = st.add("IFCPRODUCTDEFINITIONSHAPE", "$", "$", f"({trep})")
            spl = st.add("IFCLOCALPLACEMENT", "$", origin)
            sm = st.add("IFCSTRUCTURALSURFACEMEMBER",
                        _s(ifc_guid("ss" + r.uid)), owner, _s(r.uid),
                        _s(r.kind), _s(r.section), spl, pds, "$",
                        ".SHELL.", step_real(t))
            analysis_items.append(sm)
    cnt("areas", len(m.shells))
    cnt("openings", sum(len(r.openings) for r in m.shells))

    # ---- supports -------------------------------------------------------- #
    if inc["supports"] and inc["analysis"]:
        sups: List[Tuple[Tuple[float, float, float], Tuple[int, ...]]] = []
        if m.supports:
            sups = [(tuple(s.point), tuple(s.restraints)) for s in m.supports]
        else:
            pts = {tuple(p) for mm in m.members for p in (mm.pi, mm.pj)} | \
                {tuple(c) for r in m.shells for c in r.corners}
            if pts:
                zmin = min(p[2] for p in pts)
                rr = (1, 1, 1, 1, 1, 1) if m.base_fixity == "fixed" \
                    else (1, 1, 1, 0, 0, 0)
                sups = [(p, rr) for p in sorted(pts) if abs(p[2] - zmin)
                        < 1e-9]
        for i, (p, rr) in enumerate(sups):
            b = [f"IFCBOOLEAN({'.T.' if v else '.F.'})" for v in rr]
            cond = st.add("IFCBOUNDARYNODECONDITION", "$", *b)
            vp = st.add("IFCVERTEXPOINT", point(p))
            trep = st.add("IFCTOPOLOGYREPRESENTATION", ctx, "'Reference'",
                          "'Vertex'", f"({vp})")
            pds = st.add("IFCPRODUCTDEFINITIONSHAPE", "$", "$", f"({trep})")
            spl = st.add("IFCLOCALPLACEMENT", "$", origin)
            sid = st.add("IFCSTRUCTURALPOINTCONNECTION",
                         _s(ifc_guid(f"sup{i}{p}")), owner, _s(f"SUP{i + 1}"),
                         "$", "$", spl, pds, cond, "$")
            analysis_items.append(sid)
        cnt("supports", len(sups))

    # ---- relationships -------------------------------------------------- #
    for lev, items in contained.items():
        if lev in storey_of:
            st.add("IFCRELCONTAINEDINSPATIALSTRUCTURE",
                   _s(ifc_guid("cont" + lev)), owner, "$", "$",
                   "(" + ",".join(items) + ")", storey_of[lev])
    for mat, items in rel_mat.items():
        if mat in mat_of:
            st.add("IFCRELASSOCIATESMATERIAL", _s(ifc_guid("mat" + mat)),
                   owner, "$", "$", "(" + ",".join(items) + ")",
                   mat_of[mat])
    if analysis_items:
        sam = st.add("IFCSTRUCTURALANALYSISMODEL",
                     _s(ifc_guid("sam" + m.name)), owner,
                     _s(m.name + " analysis"), "$", "$", ".LOADING_3D.",
                     "$", "$", "$", origin)
        st.add("IFCRELASSIGNSTOGROUP", _s(ifc_guid("grp" + m.name)), owner,
               "$", "$", "(" + ",".join(analysis_items) + ")", "$", sam)
        st.add("IFCRELSERVICESBUILDINGS", _s(ifc_guid("srv" + m.name)),
               owner, "$", "$", sam, f"({bld})")
    if rv:
        rv.finish(owner)
        counts.update({"revit_types": rv.counts["types"],
                       "revit_psets": rv.counts["psets"]})
    skipped.append("loads, load cases and combinations are not written "
                   "(IFC export carries geometry, sections, materials and "
                   "supports)")
    head = ["ISO-10303-21;", "HEADER;",
            "FILE_DESCRIPTION(('ViewDefinition [ReferenceView_V1.2, "
            "StructuralAnalysisView]'),'2;1');",
            f"FILE_NAME({_s(m.name + '.ifc')},'',(''),(''),"
            "'SkyFrame','SkyFrame','');",
            "FILE_SCHEMA(('IFC4'));", "ENDSEC;", "DATA;"]
    text = "\n".join(head + st.lines + ["ENDSEC;", "END-ISO-10303-21;", ""])
    report = {"format": "ifc", "schema": "IFC4", "written": counts,
              "skipped": list(dict.fromkeys(skipped)),
              "entities": st.n}
    return text, report
