"""SAP2000 ``.s2k`` text export / import (CSI database tables; the format
family of ETABS ``.e2k``).  CONTRACT "SAP2000 / SAFE / steel interop".

``export_s2k(model, units=("KN", "M"))`` -> ``(text, report)``;
``read_s2k(text)`` -> ``(model, warnings)``.

Tables written (SAP2000 v20+ names):

* ``PROGRAM CONTROL`` (``CurrUnits``), ``MATERIAL PROPERTIES 01 - GENERAL``
  / ``02 - BASIC MECHANICAL PROPERTIES`` / ``03A - STEEL DATA`` / ``03B -
  CONCRETE DATA``;
* ``FRAME SECTION PROPERTIES 01 - GENERAL`` (``Shape`` Rectangular,
  Circle, Pipe, Box/Tube, I/Wide Flange, Channel, Tee, Angle or General +
  ``t3 t2 tf tw t2b tfb`` + exact ``Area TorsConst I33 I22 AS2 AS3`` +
  the eight modifiers; database shapes add ``FromFile`` / ``DBSectName``);
* ``AREA SECTION PROPERTIES`` (Shell-Thin / Shell-Thick / Plate-Thin /
  Plate-Thick / Membrane);
* ``JOINT COORDINATES``, ``CONNECTIVITY - FRAME``, ``CONNECTIVITY -
  AREA``, ``FRAME SECTION ASSIGNMENTS``, ``FRAME LOCAL AXES ASSIGNMENTS 1
  - TYPICAL``, ``FRAME RELEASE ASSIGNMENTS 1 - GENERAL`` / ``2 - PARTIAL
  FIXITY``, ``FRAME OFFSET ALONG LENGTH ASSIGNMENTS``, ``AREA SECTION
  ASSIGNMENTS``, ``AREA AUTO MESH ASSIGNMENTS``;
* ``JOINT RESTRAINT ASSIGNMENTS``, ``JOINT SPRING ASSIGNMENTS 1 -
  UNCOUPLED``, ``JOINT ADDED MASS ASSIGNMENTS``;
* rigid story diaphragms: ``CONSTRAINT DEFINITIONS - DIAPHRAGM`` (one per
  story, named after it) + ``JOINT CONSTRAINT ASSIGNMENTS``; story forces
  and explicit story masses sit on one special joint per story
  (``~D<story>``, centroid of the story joints, SpecialJt = Yes);
* ``LOAD PATTERN DEFINITIONS``, ``LOAD CASE DEFINITIONS``, ``CASE - STATIC
  1 - LOAD ASSIGNMENTS``, ``CASE - MODAL 1 - GENERAL``, ``JOINT LOADS -
  FORCE``, ``FRAME LOADS - DISTRIBUTED``, ``FRAME LOADS - POINT``, ``AREA
  LOADS - UNIFORM``, ``COMBINATION DEFINITIONS``, ``MASS SOURCE``,
  ``GROUPS 1 - DEFINITIONS`` / ``GROUPS 2 - ASSIGNMENTS``;
* the extension tables ``SKYFRAME 01 - STORIES`` (story names / heights)
  and ``SKYFRAME 02 - SHELL TYPES`` (SkyFrame's legacy ``shell`` element,
  written Shell-Thin); SAP2000 lists them as unrecognised tables and
  continues.

Not expressible (listed in ``report["skipped"]``): openings, links, line
/ area springs, hinges, insertion points, joint offsets, nonprismatic
segments (written as their General properties), RS / TH / pushover /
staged / buckling cases, thermal loads, ground displacements, accidental
torsion.
"""

from __future__ import annotations

import copy
import math
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from skyframe.io.csi_tables import (num, program_control, read_tables,
                                    units_field, units_from_field,
                                    write_tables, yes)
from skyframe.io.exchange_common import G_SI, Units

__all__ = ["export_s2k", "read_s2k", "SAP_VERSION"]

SAP_VERSION = "24.0.0"

MAT_TYPES = {"steel": "Steel", "concrete": "Concrete", "rebar": "Rebar",
             "tendon": "Tendon", "aluminum": "Aluminum",
             "coldformed": "ColdFormed", "masonry": "Masonry",
             "other": "Other"}
_MAT_BACK = {v.upper(): k for k, v in MAT_TYPES.items()}

PATTERN_TYPES = {"dead": "DEAD", "superdead": "SUPER DEAD", "live": "LIVE",
                 "reducible_live": "REDUCE LIVE", "quake": "QUAKE",
                 "wind": "WIND", "snow": "SNOW", "other": "OTHER",
                 "move": "MOVE", "temperature": "TEMPERATURE",
                 "roof_live": "ROOF LIVE", "notional": "NOTIONAL",
                 "pattern_live": "PATTERN LIVE", "ice": "ICE",
                 "settlement": "SETTLEMENT", "shrinkage": "SHRINKAGE",
                 "creep": "CREEP", "prestress": "PRESTRESS",
                 "construction": "CONSTRUCTION"}
_PAT_BACK = {v: k for k, v in PATTERN_TYPES.items()}

SHAPES = {"I": "I/Wide Flange", "I_mono": "I/Wide Flange",
          "channel": "Channel", "tee": "Tee", "angle": "Angle",
          "double_angle": "Double Angle", "rect_tube": "Box/Tube",
          "round_tube": "Pipe", "round_solid": "Circle",
          "rect_solid": "Rectangular", "plate": "Rectangular"}

SHELL_TYPES = {"shell": "Shell-Thin", "shell_thin": "Shell-Thin",
               "shell_thick": "Shell-Thick", "plate_thin": "Plate-Thin",
               "plate_thick": "Plate-Thick", "membrane": "Membrane"}
_SHELL_BACK = {"SHELL-THIN": "shell_thin", "SHELL-THICK": "shell_thick",
               "PLATE-THIN": "plate_thin", "PLATE-THICK": "plate_thick",
               "MEMBRANE": "membrane"}

COMBO_TYPES = {"add": "Linear Add", "envelope": "Envelope",
               "abs": "Abs Add", "srss": "SRSS", "range": "Range Add"}
_COMBO_BACK = {v.upper(): k for k, v in COMBO_TYPES.items()}

FRAME_DIRS = {"gravity": ("GLOBAL", "Gravity"), "global_x": ("GLOBAL", "X"),
              "global_y": ("GLOBAL", "Y"), "global_z": ("GLOBAL", "Z"),
              "local_y": ("Local", "2")}
PROJ_DIRS = {"gravity": "Gravity Projected", "global_x": "X Projected",
             "global_y": "Y Projected", "global_z": "Z Projected"}
MOMENT_DIRS = {"local_x": ("Local", "1"), "local_1": ("Local", "1"),
               "local_y": ("Local", "2"), "local_2": ("Local", "2"),
               "local_z": ("Local", "3"), "local_3": ("Local", "3"),
               "global_x": ("GLOBAL", "X"), "global_y": ("GLOBAL", "Y"),
               "global_z": ("GLOBAL", "Z")}
AREA_DIRS = {"gravity": ("GLOBAL", "Gravity"), "global_x": ("GLOBAL", "X"),
             "global_y": ("GLOBAL", "Y"), "global_z": ("GLOBAL", "Z"),
             "local_1": ("Local", "1"), "local_2": ("Local", "2"),
             "local_3": ("Local", "3")}

_REL_DOFS = ("P", "V2", "V3", "T", "M2", "M3")
_DB_FILES = None


def _db_files():
    global _DB_FILES
    if _DB_FILES is None:
        from skyframe.io.e2k_write import STEEL_DB_FILES
        _DB_FILES = STEEL_DB_FILES
    return _DB_FILES


def _key(p) -> Tuple[float, float, float]:
    return (float(p[0]), float(p[1]), float(p[2]))


# =========================================================================== #
# export
# =========================================================================== #
def export_s2k(model, units=("KN", "M"),
               include: Optional[dict] = None) -> Tuple[str, dict]:
    from skyframe.core import frame_axes as _fa
    from skyframe.core import releases as _rel
    from skyframe.core.model import BuildingModel
    if isinstance(model, dict):
        model = BuildingModel.from_dict(model)
    if _fa.model_convention(model) != "etabs":
        model = copy.deepcopy(model)
        _fa.convert_model_to_etabs(model)
    m = model
    u = units if isinstance(units, Units) else Units(*units)
    N = u.from_si
    inc = {"loads": True, "cases": True, "combos": True, "groups": True}
    inc.update({k: bool(v) for k, v in (include or {}).items() if k in inc})
    skipped: List[str] = []
    counts: Dict[str, int] = {}
    T: "OrderedDict[str, List[dict]]" = OrderedDict()

    def add(table, row):
        T.setdefault(table, []).append(row)

    add("PROGRAM CONTROL", {"ProgramName": "SAP2000", "Version": SAP_VERSION,
                            "CurrUnits": units_field(u),
                            "ModelName": m.name})

    # ---- grid (primary orthogonal grid; drafting aid) ---------------------
    for gi, g in enumerate(m.effective_grids()):
        if gi > 0 or g.kind != "orthogonal" or g.rotation or any(g.origin):
            skipped.append(f"grid system {g.name} (only an unrotated "
                           "primary orthogonal grid is written)")
            continue
        for axis, lines, labels in (("X", g.x_lines, g.x_labels),
                                    ("Y", g.y_lines, g.y_labels)):
            for lab, v in zip(labels, lines):
                add("GRID LINES", {"CoordSys": "GLOBAL", "AxisDir": axis,
                                   "GridID": lab,
                                   "XRYZCoord": N(v, "length"),
                                   "LineType": "Primary",
                                   "BubbleLoc": "End"})

    # ---- materials -------------------------------------------------------
    for mt in m.materials.values():
        add("MATERIAL PROPERTIES 01 - GENERAL", {
            "Material": mt.name,
            "Type": MAT_TYPES.get(mt.material_type, "Other"),
            "SymType": "Isotropic" if mt.symmetry == "isotropic"
            else "Uniaxial", "TempDepend": "No", "Notes": mt.notes or None})
        row = {"Material": mt.name,
               "UnitWeight": N(mt.unit_weight, "unit_weight"),
               "UnitMass": N(mt.mass_per_volume, "mass_density"),
               "E1": N(mt.E, "stress"), "G12": N(mt.G, "stress"),
               "U12": mt.nu}
        if mt.alpha is not None:
            row["A1"] = mt.alpha
        add("MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES", row)
        if mt.fy is not None or mt.fu is not None:
            add("MATERIAL PROPERTIES 03A - STEEL DATA", {
                "Material": mt.name,
                "Fy": N(mt.fy or 0.0, "stress"),
                "Fu": N(mt.fu or 0.0, "stress"),
                "EffFy": N(mt.Ry * (mt.fy or 0.0), "stress"),
                "EffFu": N(mt.Ry * (mt.fu or 0.0), "stress")})
        if mt.fc is not None:
            add("MATERIAL PROPERTIES 03B - CONCRETE DATA", {
                "Material": mt.name, "Fc": N(mt.fc, "stress"),
                "LtWtConc": bool(mt.lightweight)})
    counts["materials"] = len(m.materials)

    # ---- frame sections --------------------------------------------------
    for s in m.sections.values():
        row = {"SectionName": s.name, "Material": s.material}
        dims = {k: v for k, v in (s.dims or {}).items()
                if not isinstance(v, str)}
        shp = SHAPES.get(s.shape) if isinstance(s.dims, dict) else None
        if s.kind != "prismatic":
            shp = None
            skipped.append(f"section {s.name}: nonprismatic segments "
                           "written as General properties")
        if shp:
            g = dims.get
            t3 = g("d", g("D", 0.0))
            t2 = g("bf", g("b", g("bf_top", g("D", 0.0))))
            row.update({"Shape": shp, "t3": N(t3, "length"),
                        "t2": N(t2, "length")})
            if s.shape in ("I", "channel", "tee"):
                row.update({"tf": N(g("tf"), "length"),
                            "tw": N(g("tw"), "length")})
            elif s.shape == "I_mono":
                row.update({"tf": N(g("tf_top"), "length"),
                            "tw": N(g("tw"), "length"),
                            "t2b": N(g("bf_bot"), "length"),
                            "tfb": N(g("tf_bot"), "length")})
            elif s.shape in ("angle", "double_angle"):
                row.update({"tf": N(g("t"), "length"),
                            "tw": N(g("t"), "length")})
                if s.shape == "double_angle":
                    row["dis"] = N(g("s", 0.0), "length")
            elif s.shape in ("rect_tube", "round_tube"):
                row.update({"tf": N(g("t"), "length"),
                            "tw": N(g("t"), "length")})
        elif s.shape is None and s.b > 0 and s.h > 0 and s.kind == \
                "prismatic":
            row.update({"Shape": "Rectangular", "t3": N(s.h, "length"),
                        "t2": N(s.b, "length")})
        else:
            row.update({"Shape": "General", "t3": N(s.h, "length"),
                        "t2": N(s.b, "length")})
        if s.db_ref and s.db_ref.get("standard") in _db_files():
            row["FromFile"] = _db_files()[s.db_ref["standard"]]
            row["DBSectName"] = s.db_ref["name"]
        row.update({
            "Area": N(s.A, "area"), "TorsConst": N(s.J, "inertia"),
            "I33": N(s.I33, "inertia"), "I22": N(s.I22, "inertia"),
            "I23": 0, "AS2": N(s.As2 or 0.0, "area"),
            "AS3": N(s.As3 or 0.0, "area"),
            "AMod": s.mod_A, "A2Mod": s.mod_As2, "A3Mod": s.mod_As3,
            "JMod": s.mod_J, "I2Mod": s.mod_I22, "I3Mod": s.mod_I33,
            "MMod": s.mod_mass, "WMod": s.mod_weight})
        add("FRAME SECTION PROPERTIES 01 - GENERAL", row)
    counts["frame_sections"] = len(m.sections)

    # ---- area sections (membrane-behaviour slabs get "<name>~M") ---------
    membrane_use = {r.section for r in m.shells if r.behavior == "membrane"}
    shell_use = {r.section for r in m.shells if r.behavior != "membrane"}

    def area_prop_row(ss, name, typ):
        row = {"Section": name, "Material": ss.material, "MatAngle": 0,
               "AreaType": "Shell", "Type": typ, "DrillDOF": "Yes",
               "Thickness": N(ss.total_thickness, "length"),
               "BendThick": N(ss.total_thickness, "length")}
        for k, f in (("F11Mod", ss.f11), ("F22Mod", ss.f22),
                     ("F12Mod", ss.f12), ("M11Mod", ss.m11),
                     ("M22Mod", ss.m22), ("M12Mod", ss.m12),
                     ("V13Mod", ss.v13), ("V23Mod", ss.v23),
                     ("MMod", ss.mass), ("WMod", ss.weight)):
            row[k] = f
        if ss.mod != 1.0:
            skipped.append(f"shell section {ss.name}: stiffness factor "
                           "'mod' (use the F/M/V modifiers)")
        return row

    for ss in m.shell_sections.values():
        if ss.layered:
            skipped.append(f"shell section {ss.name}: layered definition "
                           "written as its total thickness")
        if ss.name in shell_use or ss.name not in membrane_use:
            add("AREA SECTION PROPERTIES", area_prop_row(
                ss, ss.name, SHELL_TYPES.get(ss.shell_type, "Shell-Thin")))
            if ss.shell_type == "shell":       # SkyFrame's legacy element
                add("SKYFRAME 02 - SHELL TYPES", {"Section": ss.name,
                                                  "ShellType": "shell"})
        if ss.name in membrane_use:
            add("AREA SECTION PROPERTIES",
                area_prop_row(ss, ss.name + "~M", "Membrane"))
    counts["shell_sections"] = len(m.shell_sections)

    # ---- joints ----------------------------------------------------------
    joints: "OrderedDict[Tuple[float, float, float], str]" = OrderedDict()

    def jt(p) -> str:
        k = _key(p)
        if k not in joints:
            joints[k] = str(len(joints) + 1)
        return joints[k]

    for mem in m.members:
        jt(mem.pi)
        jt(mem.pj)
    for r in m.shells:
        for c in r.corners:
            jt(c)
    obj_joints = dict(joints)
    for sp in m.spring_supports:                 # every other joint first,
        jt(sp.point)                             # so JOINT COORDINATES is
    for nm_ in m.nodal_masses:                   # complete when written
        jt(nm_.point)
    for s_ in m.supports:
        jt(s_.point)
    for p_ in m.patterns.values():
        for nl in p_.nodal_loads:
            jt(nl.point)

    # ---- frames ----------------------------------------------------------
    for mem in m.members:
        add("CONNECTIVITY - FRAME", {"Frame": mem.uid, "JointI": jt(mem.pi),
                                     "JointJ": jt(mem.pj), "IsCurved": "No",
                                     "Length": N(mem.length, "length")})
        add("FRAME SECTION ASSIGNMENTS", {
            "Frame": mem.uid, "SectionType": "", "AutoSelect": "N.A.",
            "AnalSect": mem.section, "DesignSect": mem.section,
            "MatProp": "Default"})
        if mem.angle:
            add("FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL",
                {"Frame": mem.uid, "Angle": mem.angle,
                 "AdvanceAxes": "No"})
        eff = _rel.effective(mem)
        if eff.get("i") or eff.get("j"):
            row = {"Frame": mem.uid}
            part = {"Frame": mem.uid}
            fixity = False
            for end, sfx in (("i", "I"), ("j", "J")):
                e = eff.get(end, {})
                for d in _REL_DOFS:
                    key = ("P" if d == "P" else d) + sfx
                    row[key] = d in e
                    if d in e and e[d] > 0:
                        fixity = True
                    q = "stiffness" if d in ("P", "V2", "V3") \
                        else "rot_stiffness"
                    part[key] = N(e.get(d, 0.0), q)
            row["PartialFix"] = fixity
            add("FRAME RELEASE ASSIGNMENTS 1 - GENERAL", row)
            if fixity:
                add("FRAME RELEASE ASSIGNMENTS 2 - PARTIAL FIXITY", part)
        if mem.rigid_i or mem.rigid_j:
            add("FRAME OFFSET ALONG LENGTH ASSIGNMENTS", {
                "Frame": mem.uid, "LengthType": "User",
                "Offset1": N(mem.rigid_i, "length"),
                "Offset2": N(mem.rigid_j, "length"),
                "RigidFact": mem.rigid_factor})
        for attr, what in (("cardinal_point", "insertion point"),
                           ("joint_offsets", "joint offsets"),
                           ("curve", "curved geometry"),
                           ("end_offsets", "automatic end offsets")):
            v = getattr(mem, attr)
            if (attr == "cardinal_point" and v != 10) or \
                    (attr == "end_offsets" and v != "manual") or \
                    (attr in ("joint_offsets", "curve") and v):
                skipped.append(f"frame {mem.uid}: {what}")
        if mem.hinges not in ("none", None, []):
            skipped.append(f"frame {mem.uid}: hinges")
        if mem.axial_limit != "both":
            skipped.append(f"frame {mem.uid}: tension/compression limit")
    counts["frames"] = len(m.members)

    # ---- areas -----------------------------------------------------------
    for r in m.shells:
        row = {"Area": r.uid, "NumJoints": len(r.corners)}
        for k, c in enumerate(r.corners):
            row[f"Joint{k + 1}"] = jt(c)
        add("CONNECTIVITY - AREA", row)
        sec = r.section + ("~M" if r.behavior == "membrane" else "")
        add("AREA SECTION ASSIGNMENTS", {"Area": r.uid, "Section": sec,
                                         "MatProp": "Default"})
        add("AREA AUTO MESH ASSIGNMENTS", {
            "Area": r.uid, "MeshType": "Maximum Size",
            "MaxSize1": N(r.mesh_size, "length"),
            "MaxSize2": N(r.mesh_size, "length")})
        if r.openings:
            skipped.append(f"area {r.uid}: {len(r.openings)} opening(s)")
    counts["areas"] = len(m.shells)
    if m.links:
        skipped.append(f"{len(m.links)} link element(s)")
    if m.line_springs:
        skipped.append(f"{len(m.line_springs)} line spring(s)")

    # ---- stories, diaphragms, special joints -----------------------------
    levels = [(s.name, s.elevation) for s in m.stories]
    spec: Dict[str, str] = {}

    def story_joints(z):
        return [(k, n) for k, n in obj_joints.items()
                if abs(k[2] - z) < 1e-9]

    for s in m.stories:
        add("SKYFRAME 01 - STORIES", {"Story": s.name,
                                      "Elevation": N(s.elevation, "length"),
                                      "Height": N(s.height, "length")})
    has_sf = {sf.story for p in m.patterns.values()
              for sf in p.story_forces}
    for s in m.stories:
        rigid = m.effective_diaphragm(s.name) == "rigid"
        sj = story_joints(s.elevation)
        if not rigid or not sj:
            continue
        add("CONSTRAINT DEFINITIONS - DIAPHRAGM", {
            "Name": s.name, "CoordSys": "GLOBAL", "Axis": "Z",
            "MultiLevel": "No"})
        if s.name in has_sf or s.name in m.story_masses:
            xs = [k[0] for k, _ in sj]
            ys = [k[1] for k, _ in sj]
            c = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0,
                 s.elevation)
            name = f"~D{s.name}"
            spec[s.name] = name
            joints[("spec", s.name)] = name     # placeholder key
            sj = sj + [(c, name)]
            T.setdefault("_special", []).append((name, c))
        for _k, n in sj:
            add("JOINT CONSTRAINT ASSIGNMENTS", {
                "Joint": n, "Constraint": s.name, "Type": "Diaphragm"})
    for k, n in joints.items():
        if k[0] == "spec":
            continue
        add("JOINT COORDINATES", {
            "Joint": n, "CoordSys": "GLOBAL", "CoordType": "Cartesian",
            "XorR": N(k[0], "length"), "Y": N(k[1], "length"),
            "Z": N(k[2], "length"), "SpecialJt": "No",
            "GlobalX": N(k[0], "length"), "GlobalY": N(k[1], "length"),
            "GlobalZ": N(k[2], "length")})
    for name, c in T.pop("_special", []):
        add("JOINT COORDINATES", {
            "Joint": name, "CoordSys": "GLOBAL", "CoordType": "Cartesian",
            "XorR": N(c[0], "length"), "Y": N(c[1], "length"),
            "Z": N(c[2], "length"), "SpecialJt": "Yes",
            "GlobalX": N(c[0], "length"), "GlobalY": N(c[1], "length"),
            "GlobalZ": N(c[2], "length")})
    counts["joints"] = len(obj_joints)
    counts["diaphragms"] = len(T.get("CONSTRAINT DEFINITIONS - DIAPHRAGM",
                                     []))
    # JOINT COORDINATES must precede the connectivity tables
    T.move_to_end("JOINT COORDINATES", last=False)
    T.move_to_end("PROGRAM CONTROL", last=False)

    # ---- restraints / springs / masses -----------------------------------
    if m.supports:
        sups = [(_key(s.point), tuple(s.restraints)) for s in m.supports]
    else:
        pts = list(obj_joints)
        sups = []
        if pts:
            zmin = min(p[2] for p in pts)
            rr = (1, 1, 1, 1, 1, 1) if m.base_fixity == "fixed" \
                else (1, 1, 1, 0, 0, 0)
            sups = [(p, rr) for p in pts if abs(p[2] - zmin) < 1e-9]
    for p, rr in sups:
        if p not in joints:
            continue
        add("JOINT RESTRAINT ASSIGNMENTS", {
            "Joint": joints[p], **{d: bool(v) for d, v in zip(
                ("U1", "U2", "U3", "R1", "R2", "R3"), rr)}})
    counts["restraints"] = len(sups)
    for sp in m.spring_supports:
        if sp.property or sp.angle_deg:
            skipped.append("named / rotated point spring at "
                           f"{list(sp.point)}")
            continue
        k = sp.stiffness
        add("JOINT SPRING ASSIGNMENTS 1 - UNCOUPLED", {
            "Joint": jt(sp.point), "CoordSys": "GLOBAL",
            "U1": N(k[0], "stiffness"), "U2": N(k[1], "stiffness"),
            "U3": N(k[2], "stiffness"), "R1": N(k[3], "rot_stiffness"),
            "R2": N(k[4], "rot_stiffness"), "R3": N(k[5], "rot_stiffness")})
    for nm_ in m.nodal_masses:
        add("JOINT ADDED MASS ASSIGNMENTS", {
            "Joint": jt(nm_.point), "CoordSys": "GLOBAL",
            "Mass1": N(nm_.mx, "mass"), "Mass2": N(nm_.my, "mass"),
            "Mass3": N(nm_.mz, "mass"), "MMI1": N(nm_.mrx, "rot_mass"),
            "MMI2": N(nm_.mry, "rot_mass"), "MMI3": N(nm_.mrz, "rot_mass")})
    for sname, mass in m.story_masses.items():
        if sname in spec:
            add("JOINT ADDED MASS ASSIGNMENTS", {
                "Joint": spec[sname], "CoordSys": "GLOBAL",
                "Mass1": N(mass, "mass"), "Mass2": N(mass, "mass"),
                "Mass3": 0, "MMI1": 0, "MMI2": 0, "MMI3": 0})
        else:
            skipped.append(f"story mass of {sname} (no rigid diaphragm)")
    if m.story_masses:
        skipped.append("explicit story masses: written as translational "
                       "added mass on the diaphragm special joint (no "
                       "rotational inertia)")

    # ---- loads -----------------------------------------------------------
    if inc["loads"]:
        for p in m.patterns.values():
            if p.kind not in PATTERN_TYPES:
                skipped.append(f"pattern {p.name}: type {p.kind} written "
                               "as OTHER")
            add("LOAD PATTERN DEFINITIONS", {
                "LoadPat": p.name,
                "DesignType": PATTERN_TYPES.get(p.kind, "OTHER"),
                "SelfWtMult": p.self_weight_factor})
            for nl in p.nodal_loads:
                add("JOINT LOADS - FORCE", {
                    "Joint": jt(nl.point), "LoadPat": p.name,
                    "CoordSys": "GLOBAL", "F1": N(nl.fx, "force"),
                    "F2": N(nl.fy, "force"), "F3": N(nl.fz, "force"),
                    "M1": N(nl.mx, "moment"), "M2": N(nl.my, "moment"),
                    "M3": N(nl.mz, "moment")})
            for sf in p.story_forces:
                if sf.story not in spec:
                    skipped.append(f"pattern {p.name}: story force at "
                                   f"{sf.story} (no rigid diaphragm)")
                    continue
                add("JOINT LOADS - FORCE", {
                    "Joint": spec[sf.story], "LoadPat": p.name,
                    "CoordSys": "GLOBAL", "F1": N(sf.fx, "force"),
                    "F2": N(sf.fy, "force"), "F3": 0, "M1": 0, "M2": 0,
                    "M3": 0})
            for ml in p.all_member_loads():
                if ml.kind == "moment":
                    cs, d = MOMENT_DIRS.get(ml.direction, ("GLOBAL", "X"))
                    add("FRAME LOADS - POINT", {
                        "Frame": ml.member_uid, "LoadPat": p.name,
                        "CoordSys": cs, "Type": "Moment", "Dir": d,
                        "DistType": "RelDist", "RelDist": ml.a,
                        "Moment": N(ml.w, "moment")})
                    continue
                cs, d = FRAME_DIRS[ml.direction]
                if ml.projected:
                    d = PROJ_DIRS.get(ml.direction, d)
                if ml.kind == "point":
                    add("FRAME LOADS - POINT", {
                        "Frame": ml.member_uid, "LoadPat": p.name,
                        "CoordSys": cs, "Type": "Force", "Dir": d,
                        "DistType": "RelDist", "RelDist": ml.a,
                        "Force": N(ml.w, "force")})
                else:
                    w2 = ml.w if ml.kind == "udl" else ml.w2
                    add("FRAME LOADS - DISTRIBUTED", {
                        "Frame": ml.member_uid, "LoadPat": p.name,
                        "CoordSys": cs, "Type": "Force", "Dir": d,
                        "DistType": "RelDist", "RelDistA": ml.a,
                        "RelDistB": ml.b,
                        "FOverLA": N(ml.w, "line_force"),
                        "FOverLB": N(w2, "line_force")})
            for al in p.area_loads:
                if al.joint_pattern is not None:
                    skipped.append(f"pattern {p.name}: joint-pattern area "
                                   f"load on {al.region_uid}")
                    continue
                cs, d = AREA_DIRS[al.direction]
                if al.projected and al.direction == "gravity":
                    d = "Gravity Projected"
                add("AREA LOADS - UNIFORM", {
                    "Area": al.region_uid, "LoadPat": p.name,
                    "CoordSys": cs, "Dir": d,
                    "UnifLoad": N(al.q, "pressure")})
            if p.thermal_loads or p.shell_thermal_loads or \
                    p.joint_temperatures:
                skipped.append(f"pattern {p.name}: temperature loads")
            if p.ground_displacements:
                skipped.append(f"pattern {p.name}: ground displacements")
            if p.accidental_torsion:
                skipped.append(f"pattern {p.name}: accidental torsion")
        counts["load_patterns"] = len(m.patterns)

    # ---- mass source -----------------------------------------------------
    ms = m.effective_mass_source()
    if ms or m.mass_source_mode == "element_self_mass":
        rows = [(p, f) for p, f in ms.items()] or [("", 0.0)]
        for p, f in rows:
            add("MASS SOURCE", {
                "MassSource": "MSSSRC1",
                "Elements": m.mass_source_mode == "element_self_mass",
                "Masses": True, "Loads": bool(p), "IsDefault": True,
                "LoadPat": p or None, "Multiplier": f if p else None})

    # ---- cases / combos --------------------------------------------------
    if inc["cases"]:
        if m.num_modes:
            add("LOAD CASE DEFINITIONS", {
                "Case": "MODAL", "Type": "LinModal", "InitialCond": "Zero",
                "DesignType": "OTHER", "RunCase": True})
            add("CASE - MODAL 1 - GENERAL", {
                "Case": "MODAL", "ModeType": "Eigen",
                "MaxNumModes": m.num_modes, "MinNumModes": 1})
        for c in m.cases.values():
            geo = c.effective_geometric
            if geo != "linear":
                skipped.append(f"case {c.name}: {geo} geometric "
                               "nonlinearity (written linear)")
            add("LOAD CASE DEFINITIONS", {
                "Case": c.name, "Type": "LinStatic", "InitialCond": "Zero",
                "DesignType": "OTHER",
                "RunCase": c.name not in m.cases_not_run})
            for pat, f in c.patterns.items():
                add("CASE - STATIC 1 - LOAD ASSIGNMENTS", {
                    "Case": c.name, "LoadType": "Load pattern",
                    "LoadName": pat, "LoadSF": f})
        for kind, dct in (("response spectrum", m.rs_cases),
                          ("time history", m.th_cases),
                          ("pushover", m.pushover_cases),
                          ("staged", m.staged_cases),
                          ("buckling", m.buckling_cases)):
            for n in dct:
                skipped.append(f"{kind} case {n}")
        counts["load_cases"] = len(m.cases) + (1 if m.num_modes else 0)
    if inc["combos"]:
        for cb in m.combos.values():
            first = True
            for cn, f in cb.cases.items():
                ctype = "Response Combo" if cn in m.combos else \
                    "Linear Static"
                if cn not in m.cases and cn not in m.combos:
                    skipped.append(f"combo {cb.name}: member {cn}")
                    continue
                add("COMBINATION DEFINITIONS", {
                    "ComboName": cb.name,
                    "ComboType": COMBO_TYPES.get(cb.combo_type,
                                                 "Linear Add")
                    if first else None,
                    "AutoDesign": "No" if first else None,
                    "CaseType": ctype, "CaseName": cn, "ScaleFactor": f})
                first = False
        counts["load_combinations"] = len(m.combos)

    # ---- groups ----------------------------------------------------------
    if inc["groups"] and m.groups:
        for g, v in m.groups.items():
            add("GROUPS 1 - DEFINITIONS", {"GroupName": g,
                                           "Selection": "Yes"})
            for uid in v.get("members", []):
                add("GROUPS 2 - ASSIGNMENTS", {"GroupName": g,
                                               "ObjectType": "Frame",
                                               "ObjectLabel": uid})
            for uid in v.get("shells", []):
                add("GROUPS 2 - ASSIGNMENTS", {"GroupName": g,
                                               "ObjectType": "Area",
                                               "ObjectLabel": uid})
            for p in v.get("points", []):
                if _key(p) in joints:
                    add("GROUPS 2 - ASSIGNMENTS", {
                        "GroupName": g, "ObjectType": "Joint",
                        "ObjectLabel": joints[_key(p)]})
            if v.get("links"):
                skipped.append(f"group {g}: links")
        counts["groups"] = len(m.groups)

    header = (f"File {m.name}.s2k exported by SkyFrame (SAP2000 "
              f"{SAP_VERSION} database tables)")
    text = write_tables(list(T.items()), header)
    report = {"format": "s2k", "units": [u.force, u.length],
              "written": counts, "tables": {k: len(v) for k, v in T.items()},
              "skipped": list(dict.fromkeys(skipped))}
    return text, report


# =========================================================================== #
# import
# =========================================================================== #
def read_s2k(text: str) -> Tuple[object, List[str]]:
    from skyframe.core import frame_axes as _fa
    from skyframe.core import releases as _rel
    from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                     LoadCombo, LoadPattern, Material,
                                     MemberLoad, NodalLoad, NodalMass,
                                     PointSupport, ShellSection,
                                     SpringSupport, Story, StoryForce)
    tabs = read_tables(text)
    warns: List[str] = []
    pc = program_control(tabs)
    fu = units_from_field(pc.get("CurrUnits", "KN, m, C")) or ("KN", "M")
    try:
        u = Units(fu[0], fu[1])
    except ValueError:
        warns.append(f"unknown CurrUnits {pc.get('CurrUnits')!r}; kN-m used")
        u = Units()
    S = u.to_si
    m = BuildingModel(name=pc.get("ModelName") or "SAP2000 import")
    _fa.as_new_model(m)

    def t(name):
        return tabs.get(name, [])

    # ---- grid ------------------------------------------------------------
    gl = [r for r in t("GRID LINES")
          if str(r.get("CoordSys", "GLOBAL")).upper() == "GLOBAL"]
    gx = [S(num(r, "XRYZCoord"), "length") for r in gl
          if str(r.get("AxisDir")).upper() == "X"]
    gy = [S(num(r, "XRYZCoord"), "length") for r in gl
          if str(r.get("AxisDir")).upper() == "Y"]
    if gx and gy:
        from skyframe.core.model import GridSystem
        try:
            m.set_grid_system(GridSystem(x_lines=gx, y_lines=gy))
        except ValueError as exc:
            warns.append(f"grid lines ignored: {exc}")

    # ---- materials -------------------------------------------------------
    mech = {r.get("Material"): r for r in
            t("MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES")}
    steel = {r.get("Material"): r for r in
             t("MATERIAL PROPERTIES 03A - STEEL DATA")}
    conc = {r.get("Material"): r for r in
            t("MATERIAL PROPERTIES 03B - CONCRETE DATA")}
    gen = t("MATERIAL PROPERTIES 01 - GENERAL") or \
        [{"Material": k} for k in mech]
    for r in gen:
        nm = r.get("Material")
        if not nm:
            continue
        mr = mech.get(nm, {})
        E = S(num(mr, "E1", 0.0), "stress")
        uw = S(num(mr, "UnitWeight", 0.0), "unit_weight")
        mat = Material(nm, E if E > 0 else 2.0e8, num(mr, "U12", 0.2), uw,
                       material_type=_MAT_BACK.get(
                           str(r.get("Type", "")).upper(), "other"))
        if str(r.get("SymType", "Isotropic")).lower() == "uniaxial":
            mat.symmetry = "uniaxial"
        if "UnitMass" in mr:
            md = S(num(mr, "UnitMass"), "mass_density")
            if not math.isclose(md, uw / G_SI, rel_tol=1e-12, abs_tol=0.0):
                mat.mass_density = md
        if "A1" in mr:
            mat.alpha = num(mr, "A1")
        if r.get("Notes"):
            mat.notes = r["Notes"]
        if nm in steel:
            sr = steel[nm]
            fy, fu_ = S(num(sr, "Fy"), "stress"), S(num(sr, "Fu"), "stress")
            mat.fy = fy if fy > 0 else None
            mat.fu = fu_ if fu_ > 0 else None
            if fy > 0 and num(sr, "EffFy") > 0:
                mat.Ry = S(num(sr, "EffFy"), "stress") / fy
        if nm in conc:
            mat.fc = S(num(conc[nm], "Fc"), "stress")
            mat.lightweight = yes(conc[nm], "LtWtConc")
        m.add_material(mat)

    # ---- frame sections --------------------------------------------------
    rev_db = {v.lower(): k for k, v in _db_files().items()}
    shape_back = {"I/WIDE FLANGE": "I", "CHANNEL": "channel", "TEE": "tee",
                  "ANGLE": "angle", "DOUBLE ANGLE": "double_angle",
                  "BOX/TUBE": "rect_tube", "PIPE": "round_tube",
                  "CIRCLE": "round_solid"}
    for r in t("FRAME SECTION PROPERTIES 01 - GENERAL"):
        nm, mat = r.get("SectionName"), r.get("Material")
        if not nm or mat not in m.materials:
            warns.append(f"frame section {nm}: unknown material {mat!r}")
            continue
        L = lambda k: S(num(r, k), "length")              # noqa: E731
        A, J = S(num(r, "Area"), "area"), S(num(r, "TorsConst"), "inertia")
        I33, I22 = S(num(r, "I33"), "inertia"), S(num(r, "I22"), "inertia")
        As2 = S(num(r, "AS2"), "area") or None
        As3 = S(num(r, "AS3"), "area") or None
        want = (A, I33, I22, J, As2, As3)
        shape = str(r.get("Shape", "General")).upper()
        cand = None
        try:
            if r.get("FromFile") and r.get("DBSectName"):
                std = rev_db.get(str(r["FromFile"]).lower())
                if std:
                    from skyframe.core.steel_db import section_from_db
                    cand = section_from_db(std, r["DBSectName"], mat, nm)
            elif shape == "RECTANGULAR":
                cand = FrameSection.rectangular(nm, mat, L("t2"), L("t3"))
            elif shape in shape_back:
                from skyframe.core.section_ext import section_from_dims
                typ = shape_back[shape]
                if typ == "I" and "t2b" in r and (
                        num(r, "t2b") != num(r, "t2") or
                        num(r, "tfb") != num(r, "tf")):
                    typ, dims = "I_mono", {
                        "d": L("t3"), "bf_top": L("t2"), "tf_top": L("tf"),
                        "bf_bot": L("t2b"), "tf_bot": L("tfb"),
                        "tw": L("tw")}
                elif typ in ("I", "channel", "tee"):
                    dims = {"d": L("t3"), "bf": L("t2"), "tf": L("tf"),
                            "tw": L("tw")}
                elif typ in ("angle", "double_angle"):
                    dims = {"d": L("t3"), "b": L("t2"), "t": L("tf")}
                    if typ == "double_angle":
                        dims["s"] = L("dis")
                elif typ == "rect_tube":
                    dims = {"d": L("t3"), "b": L("t2"), "t": L("tf")}
                elif typ == "round_tube":
                    dims = {"D": L("t3"), "t": L("tf")}
                else:
                    dims = {"D": L("t3")}
                cand = section_from_dims(nm, mat, typ, dims)
        except (ValueError, KeyError, ZeroDivisionError):
            cand = None
        if cand is not None and _props_equal(cand, want):
            sec = cand
        else:
            sec = FrameSection(nm, mat, A, I33, I22, J, b=L("t2"),
                               h=L("t3"))
            sec.As2, sec.As3 = As2, As3
        for k, attr in (("AMod", "mod_A"), ("A2Mod", "mod_As2"),
                        ("A3Mod", "mod_As3"), ("JMod", "mod_J"),
                        ("I2Mod", "mod_I22"), ("I3Mod", "mod_I33"),
                        ("MMod", "mod_mass"), ("WMod", "mod_weight")):
            if k in r:
                setattr(sec, attr, num(r, k, 1.0))
        m.add_section(sec)

    # ---- area sections ---------------------------------------------------
    membrane_alias: Dict[str, str] = {}
    for r in t("AREA SECTION PROPERTIES"):
        nm, mat = r.get("Section"), r.get("Material")
        if not nm or mat not in m.materials:
            warns.append(f"area section {nm}: unknown material {mat!r}")
            continue
        typ = _SHELL_BACK.get(str(r.get("Type", "Shell-Thin")).upper(),
                              "shell_thin")
        if nm.endswith("~M") and typ == "membrane":
            membrane_alias[nm] = nm[:-2]
            if nm[:-2] in m.shell_sections:
                continue
            nm = nm[:-2]
        ss = ShellSection(nm, mat, S(num(r, "Thickness"), "length"))
        ss.shell_type = typ if not (r.get("Section", "").endswith("~M")) \
            else "shell"
        for k, attr in (("F11Mod", "f11"), ("F22Mod", "f22"),
                        ("F12Mod", "f12"), ("M11Mod", "m11"),
                        ("M22Mod", "m22"), ("M12Mod", "m12"),
                        ("V13Mod", "v13"), ("V23Mod", "v23"),
                        ("MMod", "mass"), ("WMod", "weight")):
            if k in r:
                setattr(ss, attr, num(r, k, 1.0))
        m.add_shell_section(ss)

    for r in t("SKYFRAME 02 - SHELL TYPES"):
        ss = m.shell_sections.get(r.get("Section"))
        if ss is not None and r.get("ShellType") == "shell":
            ss.shell_type = "shell"

    # ---- joints / stories ------------------------------------------------
    jpos: Dict[str, Tuple[float, float, float]] = {}
    special = set()
    for r in t("JOINT COORDINATES"):
        x = num(r, "GlobalX", num(r, "XorR"))
        y = num(r, "GlobalY", num(r, "Y"))
        z = num(r, "GlobalZ", num(r, "Z"))
        jpos[r.get("Joint")] = (S(x, "length"), S(y, "length"),
                                S(z, "length"))
        if yes(r, "SpecialJt"):
            special.add(r.get("Joint"))
    frames_c = t("CONNECTIVITY - FRAME")
    areas_c = t("CONNECTIVITY - AREA")
    obj_z = sorted({jpos[r.get(k)][2] for r in frames_c
                    for k in ("JointI", "JointJ") if r.get(k) in jpos} |
                   {jpos[v][2] for r in areas_c for k, v in r.items()
                    if k.startswith("Joint") and k != "NumJoints"
                    and v in jpos})
    st_rows = t("SKYFRAME 01 - STORIES")
    if st_rows:
        m.stories = [Story(r["Story"], S(num(r, "Height"), "length"),
                           S(num(r, "Elevation"), "length"))
                     for r in st_rows]
    elif len(obj_z) > 1:
        prev, rows = obj_z[0], []
        for k, z in enumerate(obj_z[1:]):
            rows.append(Story(f"Story{k + 1}", z - prev, z))
            prev = z
        m.stories = rows

    def story_of(z):
        for s in m.stories:
            if abs(s.elevation - z) < 1e-9 * max(1.0, abs(z)):
                return s.name
        above = [s for s in m.stories if s.elevation > z]
        return min(above, key=lambda s: s.elevation).name if above else ""

    # ---- frames ----------------------------------------------------------
    fsec = {r.get("Frame"): r.get("AnalSect") for r in
            t("FRAME SECTION ASSIGNMENTS")}
    fang = {r.get("Frame"): num(r, "Angle") for r in
            t("FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL")}
    frel = {r.get("Frame"): r for r in
            t("FRAME RELEASE ASSIGNMENTS 1 - GENERAL")}
    fpart = {r.get("Frame"): r for r in
             t("FRAME RELEASE ASSIGNMENTS 2 - PARTIAL FIXITY")}
    foff = {r.get("Frame"): r for r in
            t("FRAME OFFSET ALONG LENGTH ASSIGNMENTS")}
    for r in frames_c:
        uid = r.get("Frame")
        a, b = jpos.get(r.get("JointI")), jpos.get(r.get("JointJ"))
        sec = fsec.get(uid)
        if a is None or b is None or sec not in m.sections:
            warns.append(f"frame {uid}: missing joint or section")
            continue
        dx, dy, dz = (b[k] - a[k] for k in range(3))
        kind = "column" if math.hypot(dx, dy) < 1e-9 else (
            "beam" if abs(dz) < 1e-9 else "brace")
        o = foff.get(uid, {})
        mem = m.add_member(kind, sec, a, b, story=story_of(max(a[2], b[2])),
                           uid=uid, angle=fang.get(uid, 0.0),
                           rigid_i=S(num(o, "Offset1"), "length"),
                           rigid_j=S(num(o, "Offset2"), "length"),
                           rigid_factor=num(o, "RigidFact", 1.0))
        if uid in frel:
            rr, pp = frel[uid], fpart.get(uid, {})
            eff = {}
            for end, sfx in (("i", "I"), ("j", "J")):
                e = {}
                for d in _REL_DOFS:
                    if yes(rr, d + sfx):
                        q = "stiffness" if d in ("P", "V2", "V3") \
                            else "rot_stiffness"
                        e[d] = S(num(pp, d + sfx), q)
                if e:
                    eff[end] = e
            _rel.set_effective(mem, eff)

    # ---- areas -----------------------------------------------------------
    asec = {r.get("Area"): r.get("Section") for r in
            t("AREA SECTION ASSIGNMENTS")}
    amesh = {r.get("Area"): r for r in t("AREA AUTO MESH ASSIGNMENTS")}
    for r in areas_c:
        uid = r.get("Area")
        n = int(num(r, "NumJoints", 0)) or sum(
            1 for k in r if k.startswith("Joint") and k != "NumJoints")
        pts = [jpos.get(r.get(f"Joint{k + 1}")) for k in range(n)]
        sec = asec.get(uid)
        behavior = "shell"
        if sec in membrane_alias:
            sec, behavior = membrane_alias[sec], "membrane"
        if any(p is None for p in pts) or sec not in m.shell_sections:
            warns.append(f"area {uid}: missing joint or section")
            continue
        nrm = _normal(pts)
        kind = "wall" if abs(nrm[2]) < 1e-6 else "slab"
        mesh = S(max(num(amesh.get(uid, {}), "MaxSize1", 0.0),
                     num(amesh.get(uid, {}), "MaxSize2", 0.0)), "length")
        mesh = mesh if mesh > 0 else 1.0
        if len(pts) == 4:
            m.add_shell(kind, behavior, sec, pts, mesh_size=mesh,
                        story=story_of(max(p[2] for p in pts)), uid=uid)
        else:
            from skyframe.core.model import ShellRegion
            m.shells.append(ShellRegion(uid, kind, behavior, sec,
                                        [tuple(p) for p in pts],
                                        mesh_size=mesh,
                                        story=story_of(max(p[2]
                                                           for p in pts))))

    # ---- diaphragms / special joints -------------------------------------
    jcon: Dict[str, List[str]] = {}
    for r in t("JOINT CONSTRAINT ASSIGNMENTS"):
        if str(r.get("Type", "Diaphragm")).lower() == "diaphragm":
            jcon.setdefault(r.get("Constraint"), []).append(r.get("Joint"))
    obj_joints = {r.get(k) for r in frames_c for k in ("JointI", "JointJ")} \
        | {v for r in areas_c for k, v in r.items()
           if k.startswith("Joint") and k != "NumJoints"}
    rigid: Dict[str, bool] = {}
    special_story: Dict[str, str] = {}
    for d in t("CONSTRAINT DEFINITIONS - DIAPHRAGM"):
        nm = d.get("Name")
        js = jcon.get(nm, [])
        zs = {jpos[j][2] for j in js if j in jpos}
        st_name = nm if any(s.name == nm for s in m.stories) else (
            story_of(next(iter(zs))) if len(zs) == 1 else "")
        if not st_name:
            warns.append(f"diaphragm {nm}: joints on several levels "
                         "(ignored)")
            continue
        z = next((s.elevation for s in m.stories if s.name == st_name),
                 None)
        level = {j for j in obj_joints if j in jpos and z is not None
                 and abs(jpos[j][2] - z) < 1e-9}
        if level and level <= set(js):
            rigid[st_name] = True
        else:
            warns.append(f"diaphragm {nm}: does not cover every joint of "
                         f"{st_name} (ignored)")
        for j in js:
            if j in special:
                special_story[j] = st_name
    if m.stories:
        if all(rigid.get(s.name) for s in m.stories):
            m.rigid_diaphragms, m.diaphragm, m.story_diaphragm = \
                True, "rigid", {}
        else:
            m.story_diaphragm = {s.name: ("rigid" if rigid.get(s.name)
                                          else "none") for s in m.stories}

    # ---- restraints / springs / masses -----------------------------------
    sups = []
    for r in t("JOINT RESTRAINT ASSIGNMENTS"):
        p = jpos.get(r.get("Joint"))
        if p is not None:
            sups.append((p, tuple(int(yes(r, d)) for d in
                                  ("U1", "U2", "U3", "R1", "R2", "R3"))))
    pts_all = {jpos[j] for j in obj_joints if j in jpos}
    zmin = min((p[2] for p in pts_all), default=0.0)
    base = {p for p in pts_all if abs(p[2] - zmin) < 1e-9}
    kinds = {rr for _p, rr in sups}
    if sups and {p for p, _ in sups} == base and len(kinds) == 1 and \
            next(iter(kinds)) in ((1, 1, 1, 1, 1, 1), (1, 1, 1, 0, 0, 0)):
        m.base_fixity = "fixed" if next(iter(kinds)) == (1,) * 6 \
            else "pinned"
    else:
        m.supports = [PointSupport(p, rr) for p, rr in sups]
    for r in t("JOINT SPRING ASSIGNMENTS 1 - UNCOUPLED"):
        p = jpos.get(r.get("Joint"))
        if p is None:
            continue
        m.spring_supports.append(SpringSupport(p, [
            S(num(r, "U1"), "stiffness"), S(num(r, "U2"), "stiffness"),
            S(num(r, "U3"), "stiffness"), S(num(r, "R1"), "rot_stiffness"),
            S(num(r, "R2"), "rot_stiffness"),
            S(num(r, "R3"), "rot_stiffness")]))
    for r in t("JOINT ADDED MASS ASSIGNMENTS"):
        j = r.get("Joint")
        if j in special_story:
            m.story_masses[special_story[j]] = S(num(r, "Mass1"), "mass")
            continue
        p = jpos.get(j)
        if p is not None:
            m.nodal_masses.append(NodalMass(
                p, S(num(r, "Mass1"), "mass"), S(num(r, "Mass2"), "mass"),
                S(num(r, "Mass3"), "mass"), S(num(r, "MMI1"), "rot_mass"),
                S(num(r, "MMI2"), "rot_mass"), S(num(r, "MMI3"),
                                                 "rot_mass")))

    # ---- loads -----------------------------------------------------------
    for r in t("LOAD PATTERN DEFINITIONS"):
        nm = r.get("LoadPat")
        pat = LoadPattern(nm, _PAT_BACK.get(str(r.get("DesignType",
                                                      "OTHER")).upper(),
                                            "other"))
        pat.self_weight_factor = num(r, "SelfWtMult")
        m.patterns[nm] = pat
    for r in t("JOINT LOADS - FORCE"):
        pat = m.patterns.get(r.get("LoadPat"))
        j = r.get("Joint")
        if pat is None or j not in jpos:
            continue
        if j in special_story:
            pat.story_forces.append(StoryForce(
                special_story[j], S(num(r, "F1"), "force"),
                S(num(r, "F2"), "force")))
            continue
        pat.nodal_loads.append(NodalLoad(
            jpos[j], S(num(r, "F1"), "force"), S(num(r, "F2"), "force"),
            S(num(r, "F3"), "force"), S(num(r, "M1"), "moment"),
            S(num(r, "M2"), "moment"), S(num(r, "M3"), "moment")))
    back_dir = {("GLOBAL", "GRAVITY"): ("gravity", False),
                ("GLOBAL", "X"): ("global_x", False),
                ("GLOBAL", "Y"): ("global_y", False),
                ("GLOBAL", "Z"): ("global_z", False),
                ("LOCAL", "2"): ("local_y", False),
                ("GLOBAL", "GRAVITY PROJECTED"): ("gravity", True),
                ("GLOBAL", "X PROJECTED"): ("global_x", True),
                ("GLOBAL", "Y PROJECTED"): ("global_y", True),
                ("GLOBAL", "Z PROJECTED"): ("global_z", True)}
    uids = {mm.uid for mm in m.members}
    for r in t("FRAME LOADS - DISTRIBUTED"):
        pat = m.patterns.get(r.get("LoadPat"))
        key = (str(r.get("CoordSys", "GLOBAL")).upper(),
               str(r.get("Dir", "Gravity")).upper())
        if pat is None or r.get("Frame") not in uids or key not in back_dir:
            warns.append(f"distributed frame load on {r.get('Frame')}: "
                         f"unsupported ({key})")
            continue
        d, proj = back_dir[key]
        w1 = S(num(r, "FOverLA"), "line_force")
        w2 = S(num(r, "FOverLB"), "line_force")
        kind = "udl" if w1 == w2 else "trapezoid"
        pat.member_loads.append(MemberLoad(
            r["Frame"], kind=kind, w=w1, w2=0.0 if kind == "udl" else w2,
            a=num(r, "RelDistA"), b=num(r, "RelDistB", 1.0), direction=d,
            projected=proj))
    mom_back = {("LOCAL", "1"): "local_x", ("LOCAL", "2"): "local_y",
                ("LOCAL", "3"): "local_z", ("GLOBAL", "X"): "global_x",
                ("GLOBAL", "Y"): "global_y", ("GLOBAL", "Z"): "global_z"}
    for r in t("FRAME LOADS - POINT"):
        pat = m.patterns.get(r.get("LoadPat"))
        key = (str(r.get("CoordSys", "GLOBAL")).upper(),
               str(r.get("Dir", "Gravity")).upper())
        if pat is None or r.get("Frame") not in uids:
            continue
        if str(r.get("Type", "Force")).lower() == "moment":
            if key in mom_back:
                pat.member_loads.append(MemberLoad(
                    r["Frame"], kind="moment",
                    w=S(num(r, "Moment"), "moment"), a=num(r, "RelDist"),
                    direction=mom_back[key]))
            continue
        if key not in back_dir:
            warns.append(f"point frame load on {r.get('Frame')}: "
                         f"unsupported ({key})")
            continue
        pat.member_loads.append(MemberLoad(
            r["Frame"], kind="point", w=S(num(r, "Force"), "force"),
            a=num(r, "RelDist"), direction=back_dir[key][0]))
    area_back = {("GLOBAL", "GRAVITY"): "gravity", ("GLOBAL", "X"):
                 "global_x", ("GLOBAL", "Y"): "global_y",
                 ("GLOBAL", "Z"): "global_z", ("LOCAL", "1"): "local_1",
                 ("LOCAL", "2"): "local_2", ("LOCAL", "3"): "local_3",
                 ("GLOBAL", "GRAVITY PROJECTED"): "gravity"}
    shell_ids = {r.uid for r in m.shells}
    for r in t("AREA LOADS - UNIFORM"):
        pat = m.patterns.get(r.get("LoadPat"))
        key = (str(r.get("CoordSys", "GLOBAL")).upper(),
               str(r.get("Dir", "Gravity")).upper())
        if pat is None or r.get("Area") not in shell_ids or \
                key not in area_back:
            continue
        pat.area_loads.append(AreaLoad(
            r["Area"], S(num(r, "UnifLoad"), "pressure"),
            direction=area_back[key],
            projected=key[1] == "GRAVITY PROJECTED"))

    # ---- mass source -----------------------------------------------------
    msrc = [r for r in t("MASS SOURCE")
            if str(r.get("IsDefault", "Yes")).lower() != "no"]
    if msrc:
        if yes(msrc[0], "Elements"):
            m.mass_source_mode = "element_self_mass"
        src = {r["LoadPat"]: num(r, "Multiplier", 1.0) for r in msrc
               if r.get("LoadPat") and r["LoadPat"] in m.patterns}
        if src:
            m.mass_source = dict(src)
            m.mass_from_patterns = dict(src)

    # ---- cases / combos --------------------------------------------------
    case_defs = OrderedDict((r.get("Case"), r) for r in
                            t("LOAD CASE DEFINITIONS"))
    loads: Dict[str, Dict[str, float]] = {}
    for r in t("CASE - STATIC 1 - LOAD ASSIGNMENTS"):
        if str(r.get("LoadType", "Load pattern")).lower() != "load pattern":
            continue
        d = loads.setdefault(r.get("Case"), {})
        d[r.get("LoadName")] = d.get(r.get("LoadName"), 0.0) + \
            num(r, "LoadSF", 1.0)
    m.num_modes = 0
    for nm, r in case_defs.items():
        typ = str(r.get("Type", "")).lower()
        if typ == "linmodal":
            mr = next((x for x in t("CASE - MODAL 1 - GENERAL")
                       if x.get("Case") == nm), {})
            m.num_modes = int(num(mr, "MaxNumModes", 12))
        elif typ == "linstatic":
            pats = {p: f for p, f in loads.get(nm, {}).items()
                    if p in m.patterns}
            m.add_case(nm, pats)
            if str(r.get("RunCase", "Yes")).lower() == "no":
                m.cases_not_run.append(nm)
        else:
            warns.append(f"load case {nm}: type {r.get('Type')} not "
                         "imported")
    combos: "OrderedDict[str, dict]" = OrderedDict()
    for r in t("COMBINATION DEFINITIONS"):
        nm = r.get("ComboName")
        c = combos.setdefault(nm, {"type": "add", "items": OrderedDict()})
        if r.get("ComboType"):
            c["type"] = _COMBO_BACK.get(str(r["ComboType"]).upper(), "add")
        if r.get("CaseName"):
            c["items"][r["CaseName"]] = num(r, "ScaleFactor", 1.0)
    for nm, c in combos.items():
        items = {k: v for k, v in c["items"].items()
                 if k in m.cases or k in combos}
        m.combos[nm] = LoadCombo(nm, items, c["type"])

    # ---- groups ----------------------------------------------------------
    gdefs = [r.get("GroupName") for r in t("GROUPS 1 - DEFINITIONS")]
    gas: Dict[str, Dict[str, list]] = {g: {"members": [], "shells": [],
                                           "points": []} for g in gdefs
                                       if g and g != "ALL"}
    for r in t("GROUPS 2 - ASSIGNMENTS"):
        g = gas.get(r.get("GroupName"))
        if g is None:
            continue
        ot, lab = str(r.get("ObjectType", "")).lower(), r.get("ObjectLabel")
        if ot == "frame" and lab in uids:
            g["members"].append(lab)
        elif ot == "area" and lab in shell_ids:
            g["shells"].append(lab)
        elif ot == "joint" and lab in jpos and lab not in special:
            g["points"].append(list(jpos[lab]))
    for g, v in gas.items():
        try:
            m.add_group(g, members=v["members"], shells=v["shells"],
                        points=v["points"])
        except ValueError as exc:
            warns.append(f"group {g}: {exc}")
    _fa.sync_members(m)
    m.validate()
    return m, warns


def _props_equal(sec, want) -> bool:
    A, I33, I22, J, As2, As3 = want
    got = (sec.A, sec.I33, sec.I22, sec.J, sec.As2, sec.As3)
    for a, b in zip(got, (A, I33, I22, J, As2, As3)):
        if a is None or b is None:
            if (a or None) != (b or None):
                return False
            continue
        if not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-300):
            return False
    return True


def _normal(pts):
    a = [pts[1][k] - pts[0][k] for k in range(3)]
    b = [pts[-1][k] - pts[0][k] for k in range(3)]
    n = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
         a[0] * b[1] - a[1] * b[0])
    ln = math.sqrt(sum(c * c for c in n)) or 1.0
    return tuple(c / ln for c in n)
