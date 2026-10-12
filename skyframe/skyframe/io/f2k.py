"""SAFE ``.f2k`` floor export / import (CSI database tables; ETABS "Export
Story as SAFE .f2k").  CONTRACT "SAP2000 / SAFE / steel interop".

``export_f2k(model, story, units=("KN", "M"), results=None,
loads_from_above=True)`` -> ``(text, report)`` writes ONE floor:

* the slabs (shell or membrane slabs whose corners all lie on the story
  level) and beams at the level, the columns BELOW the level (``Column``
  lines from the column's bottom point, height kept) with a fixed far end;
* the floor loads: uniform area loads, frame loads on the floor beams and
  joint loads on floor points, per load pattern, plus the self-weight
  multipliers;
* with ``loads_from_above`` and analysis ``results`` (``results.to_dict()``
  of the whole building), the COLUMN REACTIONS of every column that stands
  ON the floor: the force each column above exerts on its floor point
  (minus the end force on the column, rotated to global axes), one point
  load per static case into the pattern of the same name (created as
  ``"<case>"`` when the case name is not a pattern);
* load cases (linear static) and combinations.

Floor points are written at ``Z = 0`` (SAFE's plane), columns from
``Z = -h``.  ``read_f2k(text)`` reads such a file back into a one-story
SkyFrame model (story height = the tallest column, 3 m without columns).
"""

from __future__ import annotations

import math
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from skyframe.io.csi_tables import (num, program_control, read_tables,
                                    units_field, units_from_field,
                                    write_tables, yes)
from skyframe.io.exchange_common import Units

__all__ = ["export_f2k", "read_f2k", "column_reactions", "SAFE_VERSION"]

SAFE_VERSION = "20.0.0"
_TOL = 1e-9

PATTERN_TYPES = {"dead": "Dead", "superdead": "Super Dead", "live": "Live",
                 "reducible_live": "Reducible Live", "quake": "Quake",
                 "wind": "Wind", "snow": "Snow", "other": "Other",
                 "roof_live": "Roof Live", "temperature": "Temperature"}
_PAT_BACK = {v.upper(): k for k, v in PATTERN_TYPES.items()}
COMBO_TYPES = {"add": "Linear Add", "envelope": "Envelope",
               "abs": "Abs Add", "srss": "SRSS", "range": "Range Add"}
_COMBO_BACK = {v.upper(): k for k, v in COMBO_TYPES.items()}


def _on(z, elev) -> bool:
    return abs(z - elev) <= _TOL * max(1.0, abs(elev))


def column_reactions(model, results: dict, story: str
                     ) -> Dict[str, Dict[Tuple[float, float], List[float]]]:
    """{case: {(x, y): [Fx, Fy, Fgrav, Mx, My, Mz]}} -- the action of every
    column standing on ``story``'s floor on its floor point (global axes;
    ``Fgrav`` positive downward), from the static case results."""
    from skyframe.core import frame_axes as _fa
    elev = model.story_by_name(story).elevation
    cols = []
    for mem in model.members:
        lo, hi = (mem.pi, mem.pj) if mem.pi[2] <= mem.pj[2] else \
            (mem.pj, mem.pi)
        if mem.kind == "column" and _on(lo[2], elev) and hi[2] > elev:
            cols.append((mem, 0 if lo is mem.pi else 6, lo))
    out: Dict[str, Dict[Tuple[float, float], List[float]]] = {}
    for case, cr in (results.get("cases") or {}).items():
        mf = cr.get("member_forces") or {}
        acc: Dict[Tuple[float, float], List[float]] = {}
        for mem, off, lo in cols:
            f = mf.get(mem.uid)
            if not f:
                continue
            x, y, z, _v, _vert = _fa.member_local_axes(mem)
            N, Vy, Vz, T, My, Mz = f[off:off + 6]
            F = [-(N * x[k] + Vy * y[k] + Vz * z[k]) for k in range(3)]
            M = [-(T * x[k] + My * y[k] + Mz * z[k]) for k in range(3)]
            key = (float(lo[0]), float(lo[1]))
            row = [F[0], F[1], -F[2], M[0], M[1], M[2]]
            prev = acc.get(key)
            acc[key] = row if prev is None else [a + b for a, b in
                                                zip(prev, row)]
        out[case] = acc
    return out


# =========================================================================== #
# export
# =========================================================================== #
def export_f2k(model, story: Optional[str] = None, units=("KN", "M"),
               results: Optional[dict] = None,
               loads_from_above: bool = True) -> Tuple[str, dict]:
    from skyframe.core import frame_axes as _fa
    from skyframe.core.model import BuildingModel
    if isinstance(model, dict):
        model = BuildingModel.from_dict(model)
    m = model
    if not m.stories:
        raise ValueError("the model has no stories")
    if story is None:
        story = m.stories[0].name
    st = m.story_by_name(story)
    elev = st.elevation
    u = units if isinstance(units, Units) else Units(*units)
    N = u.from_si
    skipped: List[str] = []
    T: "OrderedDict[str, List[dict]]" = OrderedDict()

    def add(table, row):
        T.setdefault(table, []).append(row)

    add("PROGRAM CONTROL", {"ProgramName": "SAFE", "Version": SAFE_VERSION,
                            "CurrUnits": units_field(u),
                            "ModelName": f"{m.name} - {story}",
                            "ConcCode": "None"})

    slabs = [r for r in m.shells if r.kind == "slab" and
             all(_on(c[2], elev) for c in r.corners)]
    beams = [mm for mm in m.members if mm.kind != "column" and
             _on(mm.pi[2], elev) and _on(mm.pj[2], elev)]
    below = []
    for mm in m.members:
        lo, hi = (mm.pi, mm.pj) if mm.pi[2] <= mm.pj[2] else (mm.pj, mm.pi)
        if mm.kind == "column" and _on(hi[2], elev) and lo[2] < elev:
            below.append((mm, lo, hi))
    if not (slabs or beams or below):
        raise ValueError(f"story {story!r} has no slab, beam or column")

    used_mats = {m.shell_sections[r.section].material for r in slabs
                 if r.section in m.shell_sections} | \
        {m.sections[b.section].material for b in beams} | \
        {m.sections[c.section].material for c, _, _ in below}
    for mt in m.materials.values():
        if mt.name not in used_mats:
            continue
        add("MATERIAL PROPERTIES 01 - GENERAL", {
            "Material": mt.name, "Type": "Concrete" if mt.material_type ==
            "concrete" else ("Steel" if mt.material_type == "steel"
                             else "Other")})
        add("MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES", {
            "Material": mt.name,
            "UnitWeight": N(mt.unit_weight, "unit_weight"),
            "UnitMass": N(mt.mass_per_volume, "mass_density"),
            "E1": N(mt.E, "stress"), "G12": N(mt.G, "stress"),
            "U12": mt.nu})
        if mt.fc is not None:
            add("MATERIAL PROPERTIES 03 - CONCRETE", {
                "Material": mt.name, "Fc": N(mt.fc, "stress"),
                "LtWtConc": bool(mt.lightweight)})

    for nm in sorted({r.section for r in slabs}):
        ss = m.shell_sections[nm]
        add("SLAB PROPERTIES 02 - SOLID SLABS", {
            "Slab": nm, "Type": "Slab", "MatProp": ss.material,
            "Thickness": N(ss.total_thickness, "length"), "Ortho": "No"})

    def frame_prop(table, key, sec):
        rect = sec.shape is None and sec.b > 0 and sec.h > 0
        row = {key: sec.name, "Type": "Rectangular" if rect else "General",
               "MatProp": sec.material, "Depth": N(sec.h, "length"),
               "Width": N(sec.b, "length"), "Area": N(sec.A, "area"),
               "I33": N(sec.I33, "inertia"), "I22": N(sec.I22, "inertia"),
               "J": N(sec.J, "inertia")}
        add(table, row)

    for nm in sorted({b.section for b in beams}):
        frame_prop("BEAM PROPERTIES 01 - GENERAL", "Beam", m.sections[nm])
    for nm in sorted({c.section for c, _, _ in below}):
        frame_prop("COLUMN PROPERTIES 01 - GENERAL", "Column",
                   m.sections[nm])

    pts: "OrderedDict[Tuple[float, float, float], str]" = OrderedDict()

    def pt(x, y, z) -> str:
        k = (float(x), float(y), float(z))
        if k not in pts:
            pts[k] = str(len(pts) + 1)
        return pts[k]

    for r in slabs:
        for c in r.corners:
            pt(c[0], c[1], 0.0)
    for b in beams:
        pt(b.pi[0], b.pi[1], 0.0)
        pt(b.pj[0], b.pj[1], 0.0)
    for c, lo, hi in below:
        pt(hi[0], hi[1], 0.0)
        pt(lo[0], lo[1], lo[2] - elev)
    above = {}
    if loads_from_above and results is not None:
        above = column_reactions(m, results, story)
        for acc in above.values():
            for (x, y) in acc:
                pt(x, y, 0.0)
    for p_ in m.patterns.values():
        for nl in p_.nodal_loads:
            if _on(nl.point[2], elev):
                pt(nl.point[0], nl.point[1], 0.0)
    for k, n in pts.items():
        add("OBJ GEOM - POINT COORDINATES", {
            "Point": n, "GlobalX": N(k[0], "length"),
            "GlobalY": N(k[1], "length"), "GlobalZ": N(k[2], "length"),
            "SpecialPt": "No"})

    for r in slabs:
        row = {"Area": r.uid, "NumPoints": len(r.corners)}
        for i, c in enumerate(r.corners):
            row[f"Point{i + 1}"] = pt(c[0], c[1], 0.0)
        row["AreaType"] = "Slab"
        add("OBJ GEOM - AREAS 01 - GENERAL", row)
        add("SLAB PROPERTY ASSIGNMENTS", {"Area": r.uid,
                                          "SlabProp": r.section})
        if r.openings:
            skipped.append(f"slab {r.uid}: {len(r.openings)} opening(s)")
    for b in beams:
        add("OBJ GEOM - LINES 01 - GENERAL", {
            "Line": b.uid, "PointI": pt(b.pi[0], b.pi[1], 0.0),
            "PointJ": pt(b.pj[0], b.pj[1], 0.0), "LineType": "Beam",
            "Length": N(b.length, "length")})
        add("BEAM PROPERTY ASSIGNMENTS", {"Line": b.uid,
                                          "BeamProp": b.section})
        if b.releases or b.end_releases:
            skipped.append(f"beam {b.uid}: end releases")
    fix = (1, 1, 1, 1, 1, 1) if m.base_fixity == "fixed" else \
        (1, 1, 1, 0, 0, 0)
    for c, lo, hi in below:
        add("OBJ GEOM - LINES 01 - GENERAL", {
            "Line": c.uid, "PointI": pt(lo[0], lo[1], lo[2] - elev),
            "PointJ": pt(hi[0], hi[1], 0.0), "LineType": "Column",
            "Length": N(c.length, "length")})
        add("COLUMN PROPERTY ASSIGNMENTS", {"Line": c.uid,
                                            "ColProp": c.section})
        add("POINT RESTRAINT ASSIGNMENTS", {
            "Point": pt(lo[0], lo[1], lo[2] - elev),
            **{d: bool(v) for d, v in zip(("UX", "UY", "UZ", "RX", "RY",
                                           "RZ"), fix)}})

    # ---- loads -----------------------------------------------------------
    slab_ids = {r.uid for r in slabs}
    beam_ids = {b.uid for b in beams}
    pats: "OrderedDict[str, Tuple[str, float]]" = OrderedDict(
        (p.name, (PATTERN_TYPES.get(p.kind, "Other"),
                  p.self_weight_factor)) for p in m.patterns.values())
    above_pat: Dict[str, str] = {}
    for case in above:
        if case not in m.cases:
            continue
        nm = case if case in pats else f"{case} (above)"
        if nm not in pats:
            first = next(iter(m.cases[case].patterns), None)
            kind = m.patterns[first].kind if first in m.patterns else "other"
            pats[nm] = (PATTERN_TYPES.get(kind, "Other"), 0.0)
        above_pat[case] = nm
    for nm, (typ, sw) in pats.items():
        add("LOAD PATTERNS", {"LoadPat": nm, "Type": typ, "SelfWtMult": sw})
    totals: Dict[str, float] = {}
    for p in m.patterns.values():
        for al in p.area_loads:
            if al.region_uid not in slab_ids:
                continue
            if al.direction != "gravity" or al.joint_pattern is not None:
                skipped.append(f"pattern {p.name}: non-gravity / "
                               f"patterned area load on {al.region_uid}")
                continue
            add("LOAD ASSIGNMENTS - SURFACE LOADS", {
                "Area": al.region_uid, "LoadPat": p.name, "Dir": "Gravity",
                "UnifLoad": N(al.q, "pressure")})
        for ml in p.all_member_loads():
            if ml.member_uid not in beam_ids:
                continue
            if ml.direction != "gravity" or ml.kind == "moment":
                skipped.append(f"pattern {p.name}: non-gravity load on "
                               f"beam {ml.member_uid}")
                continue
            if ml.kind == "point":
                add("LOAD ASSIGNMENTS - POINT LOADS ON LINES", {
                    "Line": ml.member_uid, "LoadPat": p.name,
                    "Dir": "Gravity", "DistType": "RelDist",
                    "RelDist": ml.a, "Force": N(ml.w, "force")})
                continue
            add("LOAD ASSIGNMENTS - DISTRIBUTED LOADS", {
                "Line": ml.member_uid, "LoadPat": p.name, "Dir": "Gravity",
                "DistType": "RelDist", "RelDistA": ml.a, "RelDistB": ml.b,
                "FOverLA": N(ml.w, "line_force"),
                "FOverLB": N(ml.w if ml.kind == "udl" else ml.w2,
                             "line_force")})
        for nl in p.nodal_loads:
            if not _on(nl.point[2], elev):
                continue
            add("LOAD ASSIGNMENTS - POINT LOADS", {
                "Point": pt(nl.point[0], nl.point[1], 0.0),
                "LoadPat": p.name, "Fx": N(nl.fx, "force"),
                "Fy": N(nl.fy, "force"), "Fgrav": N(-nl.fz, "force"),
                "Mx": N(nl.mx, "moment"), "My": N(nl.my, "moment"),
                "Mz": N(nl.mz, "moment")})
        if p.story_forces:
            skipped.append(f"pattern {p.name}: story forces (lateral, "
                           "not a floor load)")
    n_above = 0
    for case, acc in above.items():
        if case not in above_pat:
            continue
        tot = 0.0
        for (x, y), v in acc.items():
            add("LOAD ASSIGNMENTS - POINT LOADS", {
                "Point": pt(x, y, 0.0), "LoadPat": above_pat[case],
                "Fx": N(v[0], "force"), "Fy": N(v[1], "force"),
                "Fgrav": N(v[2], "force"), "Mx": N(v[3], "moment"),
                "My": N(v[4], "moment"), "Mz": N(v[5], "moment")})
            tot += v[2]
            n_above += 1
        totals[case] = tot
    if loads_from_above and results is None:
        skipped.append("column reactions from above: no analysis results")

    case_pat: Dict[str, Dict[str, float]] = OrderedDict()
    for c in m.cases.values():
        loads = dict(c.patterns)
        if c.name in above_pat and above_pat[c.name] not in loads:
            loads[above_pat[c.name]] = 1.0
        case_pat[c.name] = loads
    for name, loads in case_pat.items():
        add("LOAD CASES 01 - GENERAL", {"LoadCase": name,
                                        "Type": "Linear Static"})
        for p, f in loads.items():
            add("LOAD CASES 06 - LOADS APPLIED", {"LoadCase": name,
                                                  "LoadPat": p, "SF": f})
    for cb in m.combos.values():
        first = True
        for cn, f in cb.cases.items():
            if cn not in m.cases and cn not in m.combos:
                continue
            add("LOAD COMBINATIONS", {
                "Combo": cb.name,
                "Type": COMBO_TYPES.get(cb.combo_type, "Linear Add")
                if first else None,
                "Load": cn, "SF": f})
            first = False

    text = write_tables(list(T.items()),
                        f"File {m.name}-{story}.f2k exported by SkyFrame "
                        f"(SAFE {SAFE_VERSION} database tables)")
    report = {"format": "f2k", "story": story, "elevation": elev,
              "units": [u.force, u.length],
              "written": {"points": len(pts), "slabs": len(slabs),
                          "beams": len(beams), "columns": len(below),
                          "load_patterns": len(pats),
                          "column_reactions": n_above,
                          "load_cases": len(case_pat),
                          "load_combinations": len(m.combos)},
              "reaction_totals": totals,
              "tables": {k: len(v) for k, v in T.items()},
              "skipped": list(dict.fromkeys(skipped))}
    return text, report


# =========================================================================== #
# import (one-story SkyFrame model)
# =========================================================================== #
def read_f2k(text: str):
    from skyframe.core import frame_axes as _fa
    from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                     LoadCombo, LoadPattern, Material,
                                     MemberLoad, NodalLoad, PointSupport,
                                     ShellSection, Story)
    tabs = read_tables(text)
    warns: List[str] = []
    pc = program_control(tabs)
    fu = units_from_field(pc.get("CurrUnits", "KN, m, C")) or ("KN", "M")
    u = Units(fu[0], fu[1])
    S = u.to_si
    t = lambda name: tabs.get(name, [])                   # noqa: E731
    m = BuildingModel(name=pc.get("ModelName") or "SAFE import")
    _fa.as_new_model(m)
    mech = {r.get("Material"): r for r in
            t("MATERIAL PROPERTIES 02 - BASIC MECHANICAL PROPERTIES")}
    for r in t("MATERIAL PROPERTIES 01 - GENERAL"):
        nm = r.get("Material")
        mr = mech.get(nm, {})
        m.add_material(Material(
            nm, S(num(mr, "E1", 0.0), "stress") or 2.5e7,
            num(mr, "U12", 0.2), S(num(mr, "UnitWeight", 0.0),
                                   "unit_weight"),
            material_type={"CONCRETE": "concrete", "STEEL": "steel"}.get(
                str(r.get("Type", "")).upper(), "other")))
    for r in t("MATERIAL PROPERTIES 03 - CONCRETE"):
        if r.get("Material") in m.materials:
            m.materials[r["Material"]].fc = S(num(r, "Fc"), "stress")
    for r in t("SLAB PROPERTIES 02 - SOLID SLABS"):
        if r.get("MatProp") in m.materials:
            m.add_shell_section(ShellSection(r["Slab"], r["MatProp"],
                                             S(num(r, "Thickness"),
                                               "length")))
    for tab, key in (("BEAM PROPERTIES 01 - GENERAL", "Beam"),
                     ("COLUMN PROPERTIES 01 - GENERAL", "Column")):
        for r in t(tab):
            mat = r.get("MatProp")
            if mat not in m.materials:
                continue
            b, h = S(num(r, "Width"), "length"), S(num(r, "Depth"), "length")
            if str(r.get("Type", "")).lower() == "rectangular":
                sec = FrameSection.rectangular(r[key], mat, b, h)
            else:
                sec = FrameSection(r[key], mat, S(num(r, "Area"), "area"),
                                   S(num(r, "I33"), "inertia"),
                                   S(num(r, "I22"), "inertia"),
                                   S(num(r, "J"), "inertia"), b=b, h=h)
            m.add_section(sec)
    pts = {r.get("Point"): (S(num(r, "GlobalX"), "length"),
                            S(num(r, "GlobalY"), "length"),
                            S(num(r, "GlobalZ"), "length"))
           for r in t("OBJ GEOM - POINT COORDINATES")}
    lines = t("OBJ GEOM - LINES 01 - GENERAL")
    col_h = [abs(pts[r["PointJ"]][2] - pts[r["PointI"]][2]) for r in lines
             if str(r.get("LineType", "")).lower() == "column"
             and r.get("PointI") in pts and r.get("PointJ") in pts]
    h = max(col_h) if col_h else 3.0
    m.stories = [Story("Floor", h, h)]
    lift = lambda p: (p[0], p[1], p[2] + h)              # noqa: E731
    bprop = {r.get("Line"): r.get("BeamProp") for r in
             t("BEAM PROPERTY ASSIGNMENTS")}
    cprop = {r.get("Line"): r.get("ColProp") for r in
             t("COLUMN PROPERTY ASSIGNMENTS")}
    for r in lines:
        a, b = pts.get(r.get("PointI")), pts.get(r.get("PointJ"))
        is_col = str(r.get("LineType", "")).lower() == "column"
        sec = (cprop if is_col else bprop).get(r.get("Line"))
        if a is None or b is None or sec not in m.sections:
            warns.append(f"line {r.get('Line')}: missing point or section")
            continue
        m.add_member("column" if is_col else "beam", sec, lift(a), lift(b),
                     story="Floor", uid=r["Line"])
    sprop = {r.get("Area"): r.get("SlabProp") for r in
             t("SLAB PROPERTY ASSIGNMENTS")}
    for r in t("OBJ GEOM - AREAS 01 - GENERAL"):
        n = int(num(r, "NumPoints"))
        cs = [pts.get(r.get(f"Point{k + 1}")) for k in range(n)]
        sec = sprop.get(r.get("Area"))
        if any(c is None for c in cs) or sec not in m.shell_sections:
            warns.append(f"area {r.get('Area')}: missing point or section")
            continue
        corners = [lift(c) for c in cs]
        if n == 4:
            m.add_shell("slab", "shell", sec, corners, story="Floor",
                        uid=r["Area"])
        else:
            from skyframe.core.model import ShellRegion
            m.shells.append(ShellRegion(r["Area"], "slab", "shell", sec,
                                        corners, story="Floor"))
    sups = [(lift(pts[r["Point"]]), tuple(int(yes(r, d)) for d in
                                          ("UX", "UY", "UZ", "RX", "RY",
                                           "RZ")))
            for r in t("POINT RESTRAINT ASSIGNMENTS") if r.get("Point")
            in pts]
    m.supports = [PointSupport(p, rr) for p, rr in sups]
    for r in t("LOAD PATTERNS"):
        p = LoadPattern(r["LoadPat"], _PAT_BACK.get(
            str(r.get("Type", "")).upper(), "other"))
        p.self_weight_factor = num(r, "SelfWtMult")
        m.patterns[p.name] = p
    for r in t("LOAD ASSIGNMENTS - SURFACE LOADS"):
        if r.get("LoadPat") in m.patterns:
            m.patterns[r["LoadPat"]].area_loads.append(
                AreaLoad(r["Area"], S(num(r, "UnifLoad"), "pressure")))
    for r in t("LOAD ASSIGNMENTS - DISTRIBUTED LOADS"):
        if r.get("LoadPat") in m.patterns:
            w1 = S(num(r, "FOverLA"), "line_force")
            w2 = S(num(r, "FOverLB"), "line_force")
            kind = "udl" if w1 == w2 else "trapezoid"
            m.patterns[r["LoadPat"]].member_loads.append(MemberLoad(
                r["Line"], kind=kind, w=w1, w2=0.0 if kind == "udl" else w2,
                a=num(r, "RelDistA"), b=num(r, "RelDistB", 1.0)))
    for r in t("LOAD ASSIGNMENTS - POINT LOADS ON LINES"):
        if r.get("LoadPat") in m.patterns:
            m.patterns[r["LoadPat"]].member_loads.append(MemberLoad(
                r["Line"], kind="point", w=S(num(r, "Force"), "force"),
                a=num(r, "RelDist")))
    for r in t("LOAD ASSIGNMENTS - POINT LOADS"):
        if r.get("LoadPat") in m.patterns and r.get("Point") in pts:
            m.patterns[r["LoadPat"]].nodal_loads.append(NodalLoad(
                lift(pts[r["Point"]]), S(num(r, "Fx"), "force"),
                S(num(r, "Fy"), "force"), -S(num(r, "Fgrav"), "force"),
                S(num(r, "Mx"), "moment"), S(num(r, "My"), "moment"),
                S(num(r, "Mz"), "moment")))
    loads: "OrderedDict[str, Dict[str, float]]" = OrderedDict(
        (r["LoadCase"], {}) for r in t("LOAD CASES 01 - GENERAL"))
    for r in t("LOAD CASES 06 - LOADS APPLIED"):
        if r.get("LoadCase") in loads and r.get("LoadPat") in m.patterns:
            loads[r["LoadCase"]][r["LoadPat"]] = num(r, "SF", 1.0)
    for nm, pl in loads.items():
        m.add_case(nm, pl)
    combos: "OrderedDict[str, dict]" = OrderedDict()
    for r in t("LOAD COMBINATIONS"):
        c = combos.setdefault(r["Combo"], {"type": "add", "items": {}})
        if r.get("Type"):
            c["type"] = _COMBO_BACK.get(str(r["Type"]).upper(), "add")
        c["items"][r["Load"]] = num(r, "SF", 1.0)
    for nm, c in combos.items():
        m.combos[nm] = LoadCombo(nm, {k: v for k, v in c["items"].items()
                                      if k in m.cases or k in combos},
                                 c["type"])
    m.rigid_diaphragms = False
    m.num_modes = 0
    _fa.sync_members(m)
    m.validate()
    return m, warns
