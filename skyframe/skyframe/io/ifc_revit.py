"""Revit-friendly additions to the IFC4 export (``export_ifc(...,
revit=True)``; docs/INTEROP.md "Revit workflow").

ETABS reaches Revit through IFC (or the separate CSiXRevit add-in).  With
``revit=True`` the IFC writer keeps every entity it writes today and adds
what Revit's IFC importer ("Link IFC" / "Open IFC") uses to build native
families and levels:

* ``IfcGeometricRepresentationSubContext`` ``Body`` (MODEL_VIEW) and
  ``Axis`` (GRAPH_VIEW) -- the Reference View contexts;
* levels -- each ``IfcBuildingStorey`` is placed AT its elevation (Revit
  reads the level height from the placement) and carries ``LongName``;
* family types -- one ``IfcColumnType`` / ``IfcBeamType`` /
  ``IfcMemberType`` per (kind, section) named with a Revit-recognised
  type name (``W14X90``, ``HSS6X6X1/2``, ``IPE300``, ``300 x 600mm``,
  ``450mm`` ...) and ``IfcRelDefinesByType``;
* ``Pset_ColumnCommon`` / ``Pset_BeamCommon`` / ``Pset_MemberCommon`` /
  ``Pset_WallCommon`` / ``Pset_SlabCommon`` with ``Reference`` (type
  name), ``LoadBearing`` = TRUE, ``IsExternal`` = FALSE;
* materials -- Revit-style names (``Steel ASTM A992``, ``Steel ASTM A36``,
  ``Concrete, Cast-in-Place - 30 MPa`` ...; otherwise ``<Category> -
  <name>``) with a capitalised IFC ``Category`` (Steel / Concrete / ...),
  which Revit maps to the structural material class.

The structural analysis view (IfcStructuralAnalysisModel) is unchanged,
so the same file also feeds analysis-model consumers.
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

__all__ = ["RevitIfc", "revit_profile_name", "revit_material_name",
           "material_category"]

_CATEGORY = {"steel": "Steel", "coldformed": "Steel", "rebar": "Steel",
             "tendon": "Steel", "concrete": "Concrete",
             "aluminum": "Aluminum", "masonry": "Masonry", "other": "Other"}


def _mm(v: float) -> str:
    r = round(v * 1000.0, 1)
    return str(int(r)) if r == int(r) else str(r)


def revit_profile_name(sec) -> str:
    """Type name as Revit's structural families spell it."""
    if sec.db_ref and sec.db_ref.get("name"):
        n = str(sec.db_ref["name"])
        if sec.db_ref.get("standard") == "aisc":
            n = n.upper()                 # W14X90, HSS6X6X1/2 (Revit AISC)
        return n
    dims = {k: v for k, v in (sec.dims or {}).items()
            if not isinstance(v, str)}
    if sec.shape in ("round_solid",) and dims.get("D"):
        return f"{_mm(dims['D'])}mm"
    if sec.shape == "round_tube" and dims.get("D"):
        return f"{_mm(dims['D'])} x {_mm(dims['t'])}mm"
    if sec.shape is None and sec.b > 0 and sec.h > 0:
        return f"{_mm(sec.b)} x {_mm(sec.h)}mm"
    if sec.shape in ("rect_solid", "plate") and dims:
        return f"{_mm(dims['b'])} x {_mm(dims['d'])}mm"
    return sec.name


def material_category(mat) -> str:
    return _CATEGORY.get(mat.material_type, "Other")


def revit_material_name(mat) -> str:
    cat = material_category(mat)
    if cat == "Steel" and mat.fy:
        fy = mat.fy / 1000.0                       # MPa
        if abs(fy - 344.74) < 3.0:
            return "Steel ASTM A992"
        if abs(fy - 248.21) < 3.0:
            return "Steel ASTM A36"
        return f"Steel - {round(fy)} MPa ({mat.name})"
    if cat == "Concrete" and mat.fc:
        return f"Concrete, Cast-in-Place - {round(mat.fc / 1000.0)} MPa " \
               f"({mat.name})"
    return f"{cat} - {mat.name}"


class RevitIfc:
    """Collects the Revit additions while ``export_ifc`` writes."""

    def __init__(self, st, ctx, guid, s, real):
        self.st, self.guid, self.s, self.real = st, guid, s, real
        self.body = st.add("IFCGEOMETRICREPRESENTATIONSUBCONTEXT", "'Body'",
                           "'Model'", "*", "*", "*", "*", ctx, "$",
                           ".MODEL_VIEW.", "$")
        self.axis = st.add("IFCGEOMETRICREPRESENTATIONSUBCONTEXT", "'Axis'",
                           "'Model'", "*", "*", "*", "*", ctx, "$",
                           ".GRAPH_VIEW.", "$")
        self.types: Dict[Tuple[str, str], List[str]] = {}
        self.psets: List[Tuple[str, str, str]] = []   # (eid, pset, ref)
        self.counts = {"types": 0, "psets": 0}

    # ---- storeys ------------------------------------------------------------
    def storey_placement(self, elev: float, placement3d) -> str:
        return self.st.add("IFCLOCALPLACEMENT", "$",
                           placement3d((0.0, 0.0, float(elev))))

    # ---- materials ----------------------------------------------------------
    def material(self, mat) -> Tuple[str, str]:
        return revit_material_name(mat), material_category(mat)

    # ---- elements -----------------------------------------------------------
    def member(self, eid: str, mem, sec) -> None:
        kind = {"column": "column", "beam": "beam"}.get(mem.kind, "member")
        name = revit_profile_name(sec)
        self.types.setdefault((kind, name), []).append(eid)
        pset = {"column": "Pset_ColumnCommon", "beam": "Pset_BeamCommon",
                "member": "Pset_MemberCommon"}[kind]
        self.psets.append((eid, pset, name))

    def shell(self, eid: str, region, ss) -> None:
        pset = "Pset_WallCommon" if region.kind == "wall" else \
            "Pset_SlabCommon"
        t = ss.total_thickness if ss is not None else 0.2
        ref = f"{region.section} {_mm(t)}mm"
        self.psets.append((eid, pset, ref))

    def finish(self, owner: str) -> None:
        st, s, g = self.st, self.s, self.guid
        ent = {"column": ("IFCCOLUMNTYPE", ".COLUMN."),
               "beam": ("IFCBEAMTYPE", ".BEAM."),
               "member": ("IFCMEMBERTYPE", ".BRACE.")}
        for (kind, name), items in self.types.items():
            et, pdt = ent[kind]
            tid = st.add(et, s(g(f"type{kind}{name}")), owner, s(name), "$",
                         "$", "$", "$", "$", "$", pdt)
            st.add("IFCRELDEFINESBYTYPE", s(g(f"reltype{kind}{name}")),
                   owner, "$", "$", "(" + ",".join(items) + ")", tid)
            self.counts["types"] += 1
        for eid, pset, ref in self.psets:
            props = [
                st.add("IFCPROPERTYSINGLEVALUE", "'Reference'", "$",
                       f"IFCIDENTIFIER({s(ref)})", "$"),
                st.add("IFCPROPERTYSINGLEVALUE", "'LoadBearing'", "$",
                       "IFCBOOLEAN(.T.)", "$"),
                st.add("IFCPROPERTYSINGLEVALUE", "'IsExternal'", "$",
                       "IFCBOOLEAN(.F.)", "$")]
            ps = st.add("IFCPROPERTYSET", s(g(f"pset{eid}{pset}")), owner,
                        s(pset), "$", "(" + ",".join(props) + ")")
            st.add("IFCRELDEFINESBYPROPERTIES", s(g(f"relpset{eid}")),
                   owner, "$", "$", f"({eid})", ps)
            self.counts["psets"] += 1
