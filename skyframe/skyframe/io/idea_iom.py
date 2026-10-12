"""IDEA StatiCa export through its open IOM format (IdeaRS.OpenModel XML)
-- documented subset (docs/INTEROP.md, CONTRACT "SAP2000 / SAFE / steel
interop").

``export_idea(model, results=None)`` -> ``(model_xml, results_xml,
report)``; ``export_idea_zip(...)`` -> ``(zip bytes, report)`` with
``<name>.xml`` (OpenModel) and ``<name>.results.xml`` (OpenModelResult).

OpenModel (SI base units: m, Pa, kg/m3, N):

* ``OriginSettings`` (project name, ``CrossSectionConfigurationCode``);
* ``Point3D`` -- one per frame joint; ``LineSegment3D`` (start / end
  point + ``CoordSystemByVector`` = the member LCS) + ``PolyLine3D`` /
  ``Segment3D`` -- one per frame;
* materials -- ``MatSteelEc3`` (steel: E, G, Poisson, UnitMass, fy, fu) or
  ``MatConcreteEc2`` (everything else: E, G, Poisson, UnitMass, fck);
* ``CrossSection`` -- ``CrossSectionParameter``: database shapes as
  ``RolledI`` / ``RolledChannel`` / ``RolledAngle`` / ``RolledT`` /
  ``RolledRHS`` / ``RolledCHS`` with ``UniqueName`` (e.g. ``IPE300``);
  parametric I as ``Iw`` (Width, Height, FlangeThickness, WebThickness),
  rectangles / other shapes as ``Rect`` (Width, Height of the drawing
  box) -- every section also carries ``A``, ``Iy``, ``Iz``, ``It``
  parameters (exact SkyFrame values);
* ``Element1D`` (segment, begin / end cross-section) and ``Member1D``
  (``Member1DType`` Column / Beam / Bracing; one element each);
* ``ConnectionPoint`` -- every joint where two or more members meet,
  listing its ``ConnectedMember`` s (the joints IDEA designs);
* ``LoadGroupEC`` (one per pattern kind group: Permanent / Variable /
  Accidental / Seismic), ``LoadCase`` (one per static case),
  ``CombiInputEC`` (one per additive combination, coefficients per case).

OpenModelResult: ``ResultOnMembers`` -> ``ResultOnMember`` per member with
``ResultOnSection`` at every output station (relative position 0..1)
holding one ``ResultOfInternalForces`` per static case: ``N Qy Qz Mx My
Mz`` in N / N*m in the IDEA member LCS (x = local 1, y = -local 3, z =
local 2): ``N = P, Qy = -V3, Qz = V2, Mx = T, My = -M3, Mz = M2``.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from typing import Dict, List, Optional, Tuple

__all__ = ["export_idea", "export_idea_zip", "XSI"]

XSI = "http://www.w3.org/2001/XMLSchema-instance"
XSD = "http://www.w3.org/2001/XMLSchema"
_ROLLED = {"I": "RolledI", "I_mono": "RolledI", "channel": "RolledChannel",
           "angle": "RolledAngle", "tee": "RolledT",
           "rect_tube": "RolledRHS", "round_tube": "RolledCHS"}
_GROUP = {"dead": "Permanent", "superdead": "Permanent",
          "prestress": "Permanent", "quake": "Seismic"}


def _t(v) -> str:
    r = repr(float(v))
    return r[:-2] if r.endswith(".0") else ("0" if r == "-0.0" else r)


def _sub(parent, tag, text=None, **attrib):
    e = ET.SubElement(parent, tag, attrib)
    if text is not None:
        e.text = str(text)
    return e


def _ref(parent, tag, type_name, rid):
    e = _sub(parent, tag)
    _sub(e, "TypeName", type_name)
    _sub(e, "Id", rid)
    return e


def _vec(parent, tag, v):
    e = _sub(parent, tag)
    for k, c in zip("XYZ", v):
        _sub(e, k, _t(c))
    return e


def export_idea(model, results: Optional[dict] = None
                ) -> Tuple[str, str, dict]:
    import copy
    from skyframe.core import frame_axes as _fa
    from skyframe.core.model import BuildingModel
    if isinstance(model, dict):
        model = BuildingModel.from_dict(model)
    if _fa.model_convention(model) != "etabs":
        model = copy.deepcopy(model)
        _fa.convert_model_to_etabs(model)
    m = model
    ET.register_namespace("xsi", XSI)
    root = ET.Element("OpenModel")
    root.set("xmlns:xsd", XSD)            # xmlns:xsi is declared by ET
    skipped: List[str] = []
    org = _sub(root, "OriginSettings")
    _sub(org, "CrossSectionConfigurationCode", "SkyFrame")
    _sub(org, "ProjectName", m.name)
    _sub(org, "Author", "SkyFrame")
    _sub(org, "ProjectDescription", "Exported by SkyFrame (IOM subset)")

    # ---- points ---------------------------------------------------------
    pid: Dict[Tuple[float, float, float], int] = {}
    for mem in m.members:
        for p in (mem.pi, mem.pj):
            k = tuple(float(c) for c in p)
            if k not in pid:
                pid[k] = len(pid) + 1
    pts = _sub(root, "Point3D")
    for k, i in pid.items():
        e = _sub(pts, "Point3D")
        _sub(e, "Id", i)
        _sub(e, "Name", f"N{i}")
        for a, c in zip("XYZ", k):
            _sub(e, a, _t(c))

    # ---- segments -------------------------------------------------------
    segs = _sub(root, "LineSegment3D")
    polys = _sub(root, "PolyLine3D")
    seg3 = _sub(root, "Segment3D")
    for i, mem in enumerate(m.members, start=1):
        x, y, z, _v, _vert = _fa.member_local_axes(mem)
        e = _sub(segs, "LineSegment3D")
        _sub(e, "Id", i)
        _ref(e, "StartPoint", "Point3D", pid[tuple(map(float, mem.pi))])
        _ref(e, "EndPoint", "Point3D", pid[tuple(map(float, mem.pj))])
        lcs = _sub(e, "LocalCoordinateSystem",
                   **{f"{{{XSI}}}type": "CoordSystemByVector"})
        _vec(lcs, "VecX", x)
        _vec(lcs, "VecY", tuple(-c for c in z))
        _vec(lcs, "VecZ", y)
        pl = _sub(polys, "PolyLine3D")
        _sub(pl, "Id", i)
        sg = _sub(pl, "Segments")
        _ref(sg, "ReferenceElement", "LineSegment3D", i)
        s3 = _sub(seg3, "Segment3D")
        _sub(s3, "Id", i)
        _ref(s3, "Geometry", "PolyLine3D", i)

    # ---- materials ------------------------------------------------------
    used_secs = []
    for mem in m.members:
        if mem.section not in used_secs:
            used_secs.append(mem.section)
    used_mats = []
    for s in used_secs:
        mt = m.sections[s].material
        if mt not in used_mats:
            used_mats.append(mt)
    mid: Dict[str, Tuple[str, int]] = {}
    steel_root = conc_root = None
    for i, nm in enumerate(used_mats, start=1):
        mt = m.materials[nm]
        steel = mt.material_type in ("steel", "coldformed")
        if steel:
            steel_root = steel_root if steel_root is not None else \
                _sub(root, "MatSteelEc3")
            e = _sub(steel_root, "MatSteelEc3")
            mid[nm] = ("MatSteel", i)
        else:
            conc_root = conc_root if conc_root is not None else \
                _sub(root, "MatConcreteEc2")
            e = _sub(conc_root, "MatConcreteEc2")
            mid[nm] = ("MatConcrete", i)
        _sub(e, "Id", i)
        _sub(e, "Name", nm)
        _sub(e, "E", _t(mt.E * 1e3))
        _sub(e, "G", _t(mt.G * 1e3))
        _sub(e, "Poisson", _t(mt.nu))
        _sub(e, "UnitMass", _t(mt.mass_per_volume * 1e3))
        if steel:
            _sub(e, "fy", _t((mt.fy or 0.0) * 1e3))
            _sub(e, "fu", _t((mt.fu or 0.0) * 1e3))
        else:
            _sub(e, "Fck", _t((mt.fc or 0.0) * 1e3))

    # ---- cross sections -------------------------------------------------
    sid: Dict[str, int] = {}
    css = _sub(root, "CrossSection")
    for i, nm in enumerate(used_secs, start=1):
        s = m.sections[nm]
        sid[nm] = i
        e = _sub(css, "CrossSectionParameter")
        _sub(e, "Id", i)
        _sub(e, "Name", nm)
        dims = {k: v for k, v in (s.dims or {}).items()
                if not isinstance(v, str)}
        params: List[Tuple[str, str, object]] = []
        if s.db_ref and s.shape in _ROLLED:
            typ = _ROLLED[s.shape]
            params.append(("ParameterString", "UniqueName",
                           s.db_ref["name"]))
        elif s.shape == "I" and dims:
            typ = "Iw"
            params += [("ParameterDouble", "Width", dims["bf"]),
                       ("ParameterDouble", "Height", dims["d"]),
                       ("ParameterDouble", "FlangeThickness", dims["tf"]),
                       ("ParameterDouble", "WebThickness", dims["tw"])]
        else:
            typ = "Rect"
            if s.shape is not None or not (s.b > 0 and s.h > 0):
                skipped.append(f"section {nm}: written as its b x h "
                               "rectangle (exact A / I in parameters)")
            params += [("ParameterDouble", "Width", s.b or s.A ** 0.5),
                       ("ParameterDouble", "Height", s.h or s.A ** 0.5)]
        params += [("ParameterDouble", "A", s.A),
                   ("ParameterDouble", "Iy", s.I33),
                   ("ParameterDouble", "Iz", s.I22),
                   ("ParameterDouble", "It", s.J)]
        _sub(e, "CrossSectionType", typ)
        mt = mid[s.material]
        _ref(e, "Material", mt[0], mt[1])
        _sub(e, "CrossSectionRotation", "0")
        pe = _sub(e, "Parameters")
        for kind, pname, val in params:
            p = _sub(pe, kind)
            _sub(p, "Name", pname)
            _sub(p, "Value", val if kind == "ParameterString" else _t(val))

    # ---- elements / members ----------------------------------------------
    els = _sub(root, "Element1D")
    mems = _sub(root, "Member1D")
    for i, mem in enumerate(m.members, start=1):
        e = _sub(els, "Element1D")
        _sub(e, "Id", i)
        _sub(e, "Name", f"E{mem.uid}")
        _ref(e, "Segment", "Segment3D", i)
        _ref(e, "CrossSectionBegin", "CrossSection", sid[mem.section])
        _ref(e, "CrossSectionEnd", "CrossSection", sid[mem.section])
        _sub(e, "RotationRx", _t(0.0))
        mm = _sub(mems, "Member1D")
        _sub(mm, "Id", i)
        _sub(mm, "Name", mem.uid)
        el = _sub(mm, "Elements1D")
        _ref(el, "ReferenceElement", "Element1D", i)
        _sub(mm, "Member1DType", {"column": "Column", "beam": "Beam"}.get(
            mem.kind, "Bracing"))
        if mem.releases or mem.end_releases:
            skipped.append(f"member {mem.uid}: end releases (IDEA models "
                           "the joint)")

    # ---- connection points -----------------------------------------------
    at: Dict[int, List[int]] = {}
    for i, mem in enumerate(m.members, start=1):
        for p in (mem.pi, mem.pj):
            at.setdefault(pid[tuple(map(float, p))], []).append(i)
    cps = _sub(root, "ConnectionPoint")
    ncp = 0
    for p, lst in at.items():
        if len(lst) < 2:
            continue
        ncp += 1
        e = _sub(cps, "ConnectionPoint")
        _sub(e, "Id", ncp)
        _sub(e, "Name", f"CP{ncp}")
        _ref(e, "Node", "Point3D", p)
        cm = _sub(e, "ConnectedMembers")
        for k, mi in enumerate(lst, start=1):
            c = _sub(cm, "ConnectedMember")
            _sub(c, "Id", k)
            _ref(c, "MemberId", "Member1D", mi)

    # ---- loads -------------------------------------------------------------
    groups: Dict[str, int] = {}
    gl = _sub(root, "LoadGroup")
    lcs_ = _sub(root, "LoadCase")
    cid: Dict[str, int] = {}
    for i, c in enumerate(m.cases.values(), start=1):
        kinds = {m.patterns[p].kind for p in c.patterns if p in m.patterns}
        gtype = _GROUP.get(next(iter(kinds)), "Variable") \
            if len(kinds) == 1 else "Variable"
        if gtype not in groups:
            groups[gtype] = len(groups) + 1
            g = _sub(gl, "LoadGroupEC")
            _sub(g, "Id", groups[gtype])
            _sub(g, "Name", gtype)
            _sub(g, "GroupType", gtype)
            _sub(g, "GammaQ", "1.5" if gtype == "Variable" else "1.35")
            _sub(g, "GammaGInf", "1")
            _sub(g, "GammaGSup", "1.35")
        lc = _sub(lcs_, "LoadCase")
        _sub(lc, "Id", i)
        _sub(lc, "Name", c.name)
        _sub(lc, "LoadType", "LoadCase")
        _sub(lc, "Type", "PermanentStandard" if gtype == "Permanent"
             else "VariableStatic")
        _ref(lc, "LoadGroup", "LoadGroupEC", groups[gtype])
        cid[c.name] = i
    ci = _sub(root, "CombiInput")
    ncomb = 0
    for cb in m.combos.values():
        if cb.combo_type != "add" or not all(n in cid for n in cb.cases):
            skipped.append(f"combination {cb.name}: only additive "
                           "combinations of static cases")
            continue
        ncomb += 1
        e = _sub(ci, "CombiInputEC")
        _sub(e, "Id", ncomb)
        _sub(e, "Name", cb.name)
        _sub(e, "TypeCombiEC", "ULS")
        _sub(e, "TypeCalculationCombi", "Linear")
        items = _sub(e, "Items")
        for k, (cn, f) in enumerate(cb.cases.items(), start=1):
            it = _sub(items, "CombiItem")
            _sub(it, "Id", k)
            _ref(it, "LoadCase", "LoadCase", cid[cn])
            _sub(it, "Coeff", _t(f))
    if m.shells:
        skipped.append(f"{len(m.shells)} wall / slab region(s)")

    model_xml = '<?xml version="1.0" encoding="utf-8"?>\n' + \
        ET.tostring(root, encoding="unicode")

    # ---- results ---------------------------------------------------------
    rroot = ET.Element("OpenModelResult")
    rroot.set("xmlns:xsd", XSD)
    nres = 0
    if results is not None:
        rom = _sub(_sub(rroot, "ResultOnMembers"), "ResultOnMembers")
        mlist = _sub(rom, "Members")
        cases = results.get("cases") or {}
        for i, mem in enumerate(m.members, start=1):
            st = {c: (cases[c].get("member_stations") or {}).get(mem.uid)
                  for c in cid if c in cases}
            st = {c: v for c, v in st.items() if v}
            if not st:
                continue
            rm = _sub(mlist, "ResultOnMember")
            mref = _sub(rm, "Member")
            _sub(mref, "MemberType", "Member1D")
            _sub(mref, "Id", i)
            _sub(rm, "ResultType", "InternalForces")
            rs = _sub(rm, "Results")
            first = next(iter(st.values()))
            L = first["x"][-1] or 1.0
            for k, x in enumerate(first["x"]):
                sec = _sub(rs, "ResultBase",
                           **{f"{{{XSI}}}type": "ResultOnSection"})
                _sub(sec, "AbsoluteRelative", "Relative")
                _sub(sec, "Position", _t(x / L))
                inner = _sub(sec, "Results")
                for cname, s in st.items():
                    r = _sub(inner, "SectionResultBase", **{
                        f"{{{XSI}}}type": "ResultOfInternalForces"})
                    ld = _sub(r, "Loading")
                    _sub(ld, "LoadingType", "LoadCase")
                    _sub(ld, "Id", cid[cname])
                    its = _sub(ld, "Items")
                    li = _sub(its, "ResultOfLoadingItem")
                    _sub(li, "Coefficient", "1")
                    vals = {"N": s["N"][k], "Qy": -s["V3"][k],
                            "Qz": s["V2"][k], "Mx": s["T"][k],
                            "My": -s["M3"][k], "Mz": s["M2"][k]}
                    for key, v in vals.items():
                        _sub(r, key, _t(v * 1e3))
                    nres += 1
    results_xml = '<?xml version="1.0" encoding="utf-8"?>\n' + \
        ET.tostring(rroot, encoding="unicode")
    report = {"format": "idea", "schema": "IdeaRS.OpenModel (IOM)",
              "written": {"points": len(pid), "members": len(m.members),
                          "cross_sections": len(used_secs),
                          "materials": len(used_mats),
                          "connection_points": ncp,
                          "load_cases": len(cid),
                          "combinations": ncomb,
                          "internal_force_results": nres},
              "skipped": list(dict.fromkeys(skipped))}
    if results is None:
        report["skipped"].append("load effects: no analysis results")
    return model_xml, results_xml, report


def export_idea_zip(model, results: Optional[dict] = None
                    ) -> Tuple[bytes, dict]:
    mx, rx, rep = export_idea(model, results)
    name = (getattr(model, "name", None) or (model.get("name") if
                                               isinstance(model, dict)
                                               else None) or "model")
    name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo(f"{name}.xml", (2000, 1, 1, 0, 0, 0))
        z.writestr(info, mx)
        info = zipfile.ZipInfo(f"{name}.results.xml", (2000, 1, 1, 0, 0, 0))
        z.writestr(info, rx)
    rep["files"] = [f"{name}.xml", f"{name}.results.xml"]
    return buf.getvalue(), rep
