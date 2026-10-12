"""Wave C analysis-results tables (ETABS Display > Show Tables), registered
into :mod:`skyframe.core.tables` through ``register_table`` (one hook line
at the end of that module).  Pure post-processing of a solved results dict
(no re-solve) except Modal Load Participation, which runs the on-demand
engine routine when the results do not carry it (CONTRACT "Modal load
participation (on demand)").

=====================================  =====================================
key                                    ETABS table
=====================================  =====================================
joint_velocities                       Joint Velocities (relative)
joint_velocities_absolute              Joint Velocities - Absolute
joint_accelerations                    Joint Accelerations (relative)
joint_accelerations_absolute           Joint Accelerations - Absolute
assembled_joint_masses                 Assembled Joint Masses
mass_summary_by_story                  Mass Summary by Story
mass_summary_by_diaphragm              Mass Summary by Diaphragm
mass_summary_by_group                  Mass Summary by Group
material_list_by_section               Material List by Section Property
material_list_by_story                 Material List by Story
material_list_by_material              Material List by Material
story_max_avg_displacements            Story Max Over Avg Displacements
modal_load_participation               Modal Load Participation Ratios
generalized_displacements              Generalized Displacements
(element_forces_links, replaced)       + nonlinear (device) link rows
=====================================  =====================================

Time-history joint tables read ``th_cases[name].plot_functions.joints``
(the joints requested in the case's ``output_requests``): one ``Max`` and
one ``Min`` row per joint, then one ``Step`` row per sample (sample 0 =
the state after the held gravity stage, t = 0).  Absolute = relative +
ground (translations; the ground does not rotate, so RX..RZ equal the
relative values).
"""

from __future__ import annotations

import json
import math
from typing import Dict, Iterable, List, Optional, Tuple

from . import tables as _T
from . import tables_results as _TR
from . import ratio_significance as _rsig
from . import gendisp as _gd

_col = _T._col
_G = "Analysis Results"
G_JOINT_TH = f"{_G} > Joint Output > Time History"
G_MASS = f"{_G} > Structure Output > Mass Summary"
G_STORY = f"{_G} > Structure Output > Story Output"
G_MODAL = f"{_G} > Modal Results"
G_GD = f"{_G} > Structure Output > Other Output Items"
G_MAT = f"{_G} > Structure Output > Material List"   # quantity takeoff

for _k, _u in (("velocity", "m/s"), ("accel", "m/s^2"),
               ("angular_velocity", "rad/s"), ("angular_accel", "rad/s^2"),
               ("volume", "m^3"), ("mass_moi", "tonne*m^2")):
    _T.QUANTITIES.setdefault(_k, _u)

DOF6 = ("UX", "UY", "UZ", "RX", "RY", "RZ")
TOL = 1e-6


def _th_case_type(c, name: str) -> str:
    th = c.model.th_cases.get(name)
    return "NonDirHist" if th is not None and getattr(
        th, "nonlinear", False) else "LinDirHist"


def _th_pf(c):
    """(name, plot_functions) of every TH case with recorded series."""
    th_all = c.R.get("th_cases", {}) or {}
    for name, th in th_all.items():
        if not _TR._nl_keep(c, name, th_all):
            continue
        pf = th.get("plot_functions")
        if pf:
            yield name, pf


# --------------------------------------------------------------------------- #
# Joint velocities / accelerations
# --------------------------------------------------------------------------- #
def _jt_cols(trans_q: str, rot_q: str) -> List[dict]:
    return [_col("story", "Story", "text"), _col("label", "Label", "text"),
            _col("joint", "Unique Name", "id"),
            _col("case", "Output Case", "text"),
            _col("case_type", "Case Type", "text"),
            _col("step_type", "Step Type", "text"),
            _col("step", "Step Number", "id"), _col("time", "Time", "time")
            ] + [_col(k, k, trans_q) for k in DOF6[:3]] + [
            _col(k, k, rot_q) for k in DOF6[3:]]


JOINT_VEL_COLS = _jt_cols("velocity", "angular_velocity")
JOINT_ACC_COLS = _jt_cols("accel", "angular_accel")


def _joint_th_rows(c, rel_key: str, abs_key: Optional[str]) -> List[dict]:
    rows: List[dict] = []
    n_th = 0
    for name, pf in _th_pf(c):
        n_th += 1
        t = pf.get("t") or []
        ctype = _th_case_type(c, name)
        for j in pf.get("joints") or []:
            rel = j.get(rel_key) or {}
            ab = j.get(abs_key) or {} if abs_key else {}
            ser = {k: (ab.get(k) if abs_key and k in ab else rel.get(k))
                   or [] for k in DOF6}
            n = max((len(v) for v in ser.values()), default=0)
            x, y, z = (float(v) for v in j["point"])
            base = {"story": c.story_at(z), "label": c.grid_label(x, y),
                    "joint": int(j.get("node", 0)), "case": name,
                    "case_type": ctype}
            for st, fn in (("Max", max), ("Min", min)):
                rows.append({**base, "step_type": st, "step": None,
                             "time": None,
                             **{k: (fn(ser[k]) if ser[k] else None)
                                for k in DOF6}})
            for i in range(n):
                rows.append({**base, "step_type": "Step", "step": i,
                             "time": t[i] if i < len(t) else None,
                             **{k: (ser[k][i] if i < len(ser[k]) else None)
                                for k in DOF6}})
    if not rows:
        th_all = c.R.get("th_cases", {}) or {}
        if th_all:
            c.warnings.append(
                "no recorded joints: add the joints to the time-history "
                "case's Output Requests (Plot Functions) to tabulate their "
                "velocities and accelerations")
    return rows


def _t_jvel(c):
    return _joint_th_rows(c, "vel", None)


def _t_jvel_abs(c):
    return _joint_th_rows(c, "vel", "vel_abs")


def _t_jacc(c):
    return _joint_th_rows(c, "acc", None)


def _t_jacc_abs(c):
    return _joint_th_rows(c, "acc", "acc_abs")


# --------------------------------------------------------------------------- #
# Assembled joint masses / mass summaries
# --------------------------------------------------------------------------- #
MASS6 = ([_col(k, k, "mass") for k in DOF6[:3]]
         + [_col(k, k, "mass_moi") for k in DOF6[3:]])
ASM_MASS_COLS = [_col("story", "Story", "text"), _col("label", "Label", "text"),
                 _col("joint", "Unique Name", "id")] + MASS6 + [
    _col("x", "X", "length"), _col("y", "Y", "length"),
    _col("z", "Z", "length")]


def _mass_by_tag(c) -> Dict[int, List[float]]:
    out: Dict[int, List[float]] = {}
    for t, d, m in c.mass:
        if 1 <= d <= 6:
            out.setdefault(int(t), [0.0] * 6)[d - 1] += float(m)
    return out


def _t_assembled_masses(c) -> List[dict]:
    """ETABS Assembled Joint Masses: the lumped diagonal mass the analysis
    uses (engine context ``mass``), one row per massed joint (diaphragm
    masters included) plus a ``Sum`` row."""
    mb = _mass_by_tag(c)
    if not mb:
        c.warnings.append("no mass map available (no mass, or the mass "
                          "source cannot be reconstructed without the "
                          "engine context)")
        return []
    rows = []
    tot = [0.0] * 6
    for t in sorted(mb, key=lambda t: (c.nodes.get(t, (0, 0, 0))[2],
                                       c.nodes.get(t, (0, 0, 0))[0],
                                       c.nodes.get(t, (0, 0, 0))[1], t)):
        x, y, z = c.nodes.get(t, (None, None, None))
        m = mb[t]
        tot = [a + b for a, b in zip(tot, m)]
        lab = c.grid_label(x, y) if x is not None else ""
        if t in c.master_tags:
            lab = "Diaphragm master"
        rows.append({"story": c.story_at(z) if z is not None else "",
                     "label": lab, "joint": t,
                     **dict(zip(DOF6, m)), "x": x, "y": y, "z": z})
    rows.append({"story": "", "label": "Sum", "joint": None,
                 **dict(zip(DOF6, tot)), "x": None, "y": None, "z": None})
    return rows


def _story_of_z(c, z: float) -> str:
    """Story whose range (elevation below, elevation] contains z; at or
    below the base -> "Base"."""
    if z <= c.z_base + TOL:
        return "Base"
    for s in c.stories:
        if z <= s.elevation + TOL:
            return s.name
    return c.stories[-1].name if c.stories else ""


MASS_STORY_COLS = [_col("story", "Story", "text")] + MASS6


def _t_mass_story(c) -> List[dict]:
    mb = _mass_by_tag(c)
    acc: Dict[str, List[float]] = {}
    for t, m in mb.items():
        z = c.nodes.get(t, (0.0, 0.0, c.z_base))[2]
        s = _story_of_z(c, z)
        acc.setdefault(s, [0.0] * 6)
        acc[s] = [a + b for a, b in zip(acc[s], m)]
    order = [s.name for s in reversed(c.stories)] + ["Base"]
    return [{"story": s, **dict(zip(DOF6, acc[s]))} for s in order
            if s in acc]


MASS_DIA_COLS = [_col("story", "Story", "text"),
                 _col("diaphragm", "Diaphragm", "text"),
                 _col("mass_x", "Mass X", "mass"),
                 _col("mass_y", "Mass Y", "mass"),
                 _col("mmi", "Mass Moment of Inertia", "mass_moi"),
                 _col("x", "X Mass Center", "length"),
                 _col("y", "Y Mass Center", "length")]


def _t_mass_dia(c) -> List[dict]:
    """Mass lumped on each diaphragm master (ux, uy, rz), with the center
    of mass of the story / named diaphragm."""
    mb = _mass_by_tag(c)
    named = _T._named_dia(c)
    rows = []
    for s in reversed(c.stories):
        if s.name in named:
            for dn, e in sorted(named[s.name].items()):
                m = mb.get(int(e["master"])) if e.get("master") is not None \
                    else None
                if not m:
                    continue
                rows.append({"story": s.name, "diaphragm": dn,
                             "mass_x": m[0], "mass_y": m[1], "mmi": m[5],
                             "x": e.get("cm_x"), "y": e.get("cm_y")})
            continue
        mt = c.masters.get(s.name)
        m = mb.get(mt) if mt is not None else None
        if not m:
            continue
        x, y = _T._cm_xy(c, s.name)
        rows.append({"story": s.name, "diaphragm": "D1", "mass_x": m[0],
                     "mass_y": m[1], "mmi": m[5], "x": x, "y": y})
    if not rows:
        c.warnings.append("no rigid / semi-rigid diaphragm carries mass")
    return rows


def _seg_contains(a, b, p) -> bool:
    ab = [b[k] - a[k] for k in range(3)]
    ap = [p[k] - a[k] for k in range(3)]
    L2 = sum(v * v for v in ab)
    if L2 <= 0.0:
        return False
    t = sum(ab[k] * ap[k] for k in range(3)) / L2
    if t < -1e-9 or t > 1.0 + 1e-9:
        return False
    d = [ap[k] - t * ab[k] for k in range(3)]
    return math.sqrt(sum(v * v for v in d)) < 1e-6


def _poly_contains(corners, p) -> bool:
    """p in the (planar) polygon, boundary included."""
    if len(corners) < 3:
        return False
    a, b, cc = corners[0], corners[1], corners[2]
    u = [b[k] - a[k] for k in range(3)]
    v = [cc[k] - a[k] for k in range(3)]
    n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
         u[0] * v[1] - u[1] * v[0]]
    nn = math.sqrt(sum(x * x for x in n))
    if nn <= 0.0:
        return False
    if abs(sum(n[k] * (p[k] - a[k]) for k in range(3))) / nn > 1e-6:
        return False
    drop = max(range(3), key=lambda k: abs(n[k]))
    ax = [k for k in range(3) if k != drop]
    pts = [(q[ax[0]], q[ax[1]]) for q in corners]
    px, py = p[ax[0]], p[ax[1]]
    for i in range(len(pts)):                       # on an edge
        q0, q1 = pts[i], pts[(i + 1) % len(pts)]
        ex, ey = q1[0] - q0[0], q1[1] - q0[1]
        cr = ex * (py - q0[1]) - ey * (px - q0[0])
        L = math.hypot(ex, ey)
        if L > 0 and abs(cr) / L < 1e-6:
            t = (ex * (px - q0[0]) + ey * (py - q0[1])) / (L * L)
            if -1e-9 <= t <= 1 + 1e-9:
                return True
    inside = False
    for i in range(len(pts)):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % len(pts)]
        if (y0 > py) != (y1 > py):
            xc = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
            if px < xc:
                inside = not inside
    return inside


def group_joint_tags(c, gname: str) -> List[int]:
    """FE joints that belong to group ``gname``: on a group frame (ends
    and mesh nodes), inside / on a group shell, at a group link end, or at
    a group point (diaphragm masters excluded)."""
    from .groups import resolve_group
    g = resolve_group(c.model, gname)
    segs = [(m.pi, m.pj) for m in g["members"]]
    polys = [list(r.corners) for r in g["shells"]]
    pts = [tuple(p) for p in g["points"]]
    for x in g["links"]:
        pts += [tuple(x.pi), tuple(x.pj)]
    out = []
    for t, p in c.nodes.items():
        if t in c.master_tags:
            continue
        if (any(all(abs(p[k] - float(q[k])) < TOL for k in range(3))
                for q in pts)
                or any(_seg_contains(a, b, p) for a, b in segs)
                or any(_poly_contains(cs, p) for cs in polys)):
            out.append(t)
    return sorted(out)


MASS_GROUP_COLS = [_col("group", "Group", "text"),
                   _col("n_joints", "Joints", "id")] + MASS6


def _t_mass_group(c) -> List[dict]:
    mb = _mass_by_tag(c)
    rows = []
    for gname in (getattr(c.model, "groups", None) or {}):
        tags = group_joint_tags(c, gname)
        tot = [0.0] * 6
        n = 0
        for t in tags:
            if t in mb:
                n += 1
                tot = [a + b for a, b in zip(tot, mb[t])]
        rows.append({"group": gname, "n_joints": n, **dict(zip(DOF6, tot))})
    if any(c.masters.values()) and rows:
        c.warnings.append("mass lumped on a rigid-diaphragm master belongs "
                          "to no group (only joint masses are summed)")
    return rows


# --------------------------------------------------------------------------- #
# Material list / quantity takeoff
# --------------------------------------------------------------------------- #
def _takeoff_items(model) -> List[dict]:
    """One item per frame / shell object: section, material, object type,
    story, length or area, volume, weight (unit weight x volume x weight
    modifier — the same rule as the self-weight load)."""
    from .modifiers import frame_weight_mod, shell_weight_mod
    from .nonprismatic import member_area
    items = []
    for m in model.members:
        sec = model.sections.get(m.section)
        mat = model.materials.get(sec.material) if sec else None
        if sec is None or mat is None:
            continue
        L = float(m.length)
        vol = float(member_area(model, m, sec)) * L
        items.append({"kind": "frame", "section": m.section,
                      "material": sec.material,
                      "object_type": str(m.kind).title(),
                      "story": m.story or "", "length": L, "area": 0.0,
                      "volume": vol,
                      "weight": vol * float(mat.unit_weight)
                      * float(frame_weight_mod(sec))})
    for r in model.shells:
        ssec = model.shell_sections.get(r.section)
        mat = model.materials.get(ssec.material) if ssec else None
        if ssec is None or mat is None:
            continue
        A = float(r.net_area)
        vol = A * float(ssec.total_thickness)
        items.append({"kind": "shell", "section": r.section,
                      "material": ssec.material,
                      "object_type": "Floor" if r.kind == "slab"
                      else str(r.kind).title(),
                      "story": r.story or "", "length": 0.0, "area": A,
                      "volume": vol,
                      "weight": vol * float(mat.unit_weight)
                      * float(shell_weight_mod(ssec))})
    return items


TAKEOFF_TAIL = [_col("pieces", "Pieces", "id"),
                _col("length", "Length", "length"),
                _col("area", "Area", "area"), _col("volume", "Volume",
                                                   "volume"),
                _col("weight", "Weight", "force")]
MAT_SECTION_COLS = [_col("section", "Section", "text"),
                    _col("object_type", "Object Type", "text"),
                    _col("material", "Material", "text")] + TAKEOFF_TAIL
MAT_STORY_COLS = [_col("story", "Story", "text"),
                  _col("object_type", "Object Type", "text"),
                  _col("material", "Material", "text")] + TAKEOFF_TAIL
MAT_MATERIAL_COLS = [_col("material", "Material", "text"),
                     _col("object_type", "Object Type", "text")] + \
    TAKEOFF_TAIL


def _aggregate(items, keys: Tuple[str, ...]) -> List[dict]:
    acc: Dict[tuple, dict] = {}
    for it in items:
        k = tuple(it[x] for x in keys)
        e = acc.get(k)
        if e is None:
            e = acc[k] = {**{x: it[x] for x in keys}, "pieces": 0,
                          "length": 0.0, "area": 0.0, "volume": 0.0,
                          "weight": 0.0}
        e["pieces"] += 1
        for f in ("length", "area", "volume", "weight"):
            e[f] += it[f]
    return list(acc.values())


def _with_total(rows: List[dict], keys: Tuple[str, ...]) -> List[dict]:
    if not rows:
        return rows
    tot = {k: "" for k in keys}
    tot[keys[0]] = "Total"
    tot.update(pieces=sum(r["pieces"] for r in rows),
               **{f: sum(r[f] for r in rows)
                  for f in ("length", "area", "volume", "weight")})
    return rows + [tot]


def _t_mat_section(c) -> List[dict]:
    keys = ("section", "object_type", "material")
    return _with_total(_aggregate(_takeoff_items(c.model), keys), keys)


def _t_mat_story(c) -> List[dict]:
    keys = ("story", "object_type", "material")
    rows = _aggregate(_takeoff_items(c.model), keys)
    rank = {s.name: i for i, s in enumerate(c.stories)}
    rows.sort(key=lambda r: -rank.get(r["story"], -1))
    return _with_total(rows, keys)


def _t_mat_material(c) -> List[dict]:
    keys = ("material", "object_type")
    return _with_total(_aggregate(_takeoff_items(c.model), keys), keys)


# --------------------------------------------------------------------------- #
# Story max / avg displacements
# --------------------------------------------------------------------------- #
STORY_MAXAVG_COLS = [_col("story", "Story", "text"),
                     _col("case", "Output Case", "text"),
                     _col("case_type", "Case Type", "text"),
                     _col("direction", "Direction", "text"),
                     _col("max_disp", "Maximum", "length"),
                     _col("min_disp", "Minimum", "length"),
                     _col("avg_disp", "Average", "length"),
                     _col("ratio", "Ratio", "ratio"),
                     _col("label_max", "Joint (max)", "id")]


def _t_story_maxavg(c) -> List[dict]:
    """ETABS Story Max Over Avg Displacements, from ALL joints of the story
    level (any diaphragm option): ``Maximum = max_j |u_j|``, ``Minimum =
    min_j |u_j|``, ``Average = (Maximum + Minimum) / 2``, ``Ratio =
    Maximum / Average`` (ASCE 7 Table 12.3-1 torsional irregularity).  A
    ratio whose average is negligible for the case is ``None`` (n/a)."""
    joints = c.joints()
    by_story: Dict[str, List[int]] = {}
    for t in joints:
        s = c.story_at(c.nodes[t][2])
        if s and s != "Base":
            by_story.setdefault(s, []).append(t)
    rows = []
    n_na = 0
    for name, ctype, cd in c.sources():
        nd = cd.get("node_disp", {})
        thr = _rsig.case_threshold(cd.get("story", {}))
        for s in reversed(c.stories):
            tags = [t for t in by_story.get(s.name, []) if str(t) in nd]
            if not tags:
                continue
            for direction, comp in (("X", 0), ("Y", 1)):
                vals = [(abs(float(nd[str(t)][comp])), t) for t in tags]
                mx, tmax = max(vals, key=lambda v: (v[0], -v[1]))
                mn = min(v[0] for v in vals)
                avg = 0.5 * (mx + mn)
                if mx < 1e-14:
                    continue
                na = _rsig.negligible(avg, thr)
                n_na += na
                rows.append({"story": s.name, "case": name,
                             "case_type": ctype, "direction": direction,
                             "max_disp": mx, "min_disp": mn, "avg_disp": avg,
                             "ratio": None if na else (
                                 mx / avg if avg > 1e-30 else 1.0),
                             "label_max": tmax})
    if n_na:
        c.warnings.append(f"Ratio {_rsig.NA_NEGLIGIBLE_DRIFT} in {n_na} "
                          "row(s): the average displacement is negligible "
                          "for the case")
    return rows


# --------------------------------------------------------------------------- #
# Modal load participation
# --------------------------------------------------------------------------- #
MLP_COLS = [_col("case", "Case", "text"),
            _col("item_type", "Item Type", "text"),
            _col("item", "Item", "text"),
            _col("static", "Static", "percent"),
            _col("dynamic", "Dynamic", "percent")]
_MLP_CACHE: Dict[str, dict] = {}


def load_participation_for(model, results: dict) -> Optional[dict]:
    """``results.modal.load_participation`` or the on-demand engine
    computation (cached per model signature)."""
    lp = ((results or {}).get("modal") or {}).get("load_participation")
    if lp is not None:
        return lp
    if not ((results or {}).get("modal") or {}).get("periods"):
        return None
    sig = json.dumps(model.to_dict(), sort_keys=True, default=str)
    if sig not in _MLP_CACHE:
        from skyframe.engine.opensees_engine import OpenSeesEngine
        _MLP_CACHE.clear()
        _MLP_CACHE[sig] = OpenSeesEngine(model).run_load_participation()
    return _MLP_CACHE[sig]


def _t_mlp(c) -> List[dict]:
    try:
        lp = load_participation_for(c.model, c.R)
    except Exception as exc:                          # pragma: no cover
        c.warnings.append(f"modal load participation failed: {exc}")
        return []
    if not lp:
        return []
    rows = []
    for k, v in (lp.get("acceleration") or {}).items():
        rows.append({"case": "Modal", "item_type": "Acceleration",
                     "item": k, "static": v.get("static"),
                     "dynamic": v.get("dynamic")})
    for k, v in (lp.get("patterns") or {}).items():
        rows.append({"case": "Modal", "item_type": "Load Pattern",
                     "item": k, "static": v.get("static"),
                     "dynamic": v.get("dynamic")})
    return rows


# --------------------------------------------------------------------------- #
# Generalized displacements
# --------------------------------------------------------------------------- #
GD_COLS = [_col("name", "Name", "text"), _col("type", "Type", "text"),
           _col("case", "Output Case", "text"),
           _col("case_type", "Case Type", "text"),
           _col("step_type", "Step Type", "text"),
           _col("translation", "Translation", "length"),
           _col("rotation", "Rotation", "angle")]


def _gd_row(name, gd, case, ctype, step, v):
    trans = (gd.get("type", "translation") == "translation")
    return {"name": name, "type": gd.get("type", "translation").title(),
            "case": case, "case_type": ctype, "step_type": step,
            "translation": v if trans else None,
            "rotation": None if trans else v}


def _t_gendisp(c) -> List[dict]:
    """``g = sum sf * u(point, dof)`` per static case / additive combo
    (``step_type`` blank) and Max / Min over every TH case whose output
    requests record all the terms' joints."""
    gds = getattr(c.model, "generalized_displacements", None) or {}
    if not gds:
        return []
    struct = _T._structural_tags(c.R)
    rows = []
    srcs = c.sources()
    for name, gd in gds.items():
        tags = _gd.resolve_tags(c.nodes, gd, prefer=struct,
                                exclude=c.master_tags)
        if any(t is None for t in tags):
            c.warnings.append(f"{name}: a term's point is not a joint of "
                              "the analysis model")
            continue
        for case, ctype, cd in srcs:
            v = _gd.evaluate(gd, tags, cd.get("node_disp", {}))
            if v is not None:
                rows.append(_gd_row(name, gd, case, ctype, "", v))
        for case, pf in _th_pf(c):
            by_pt = {tuple(round(float(x), 6) for x in j["point"]): j
                     for j in pf.get("joints") or []}
            series = []
            for t in gd["terms"]:
                j = by_pt.get(tuple(round(float(x), 6) for x in t["point"]))
                series.append(j.get("disp") if j else None)
            s = _gd.evaluate_series(gd, series)
            if s:
                ct = _th_case_type(c, case)
                rows.append(_gd_row(name, gd, case, ct, "Max", max(s)))
                rows.append(_gd_row(name, gd, case, ct, "Min", min(s)))
    return rows


# --------------------------------------------------------------------------- #
# Element Forces - Links: nonlinear (device) link rows
# --------------------------------------------------------------------------- #
_LABEL_COL = {"P": "P", "V2": "V2", "V3": "V3", "T": "T", "M2": "M2",
              "M3": "M3", "U1": "P", "U2": "V2", "U3": "V3", "R1": "T",
              "R2": "M2", "R3": "M3", "UX": "V2", "UY": "V3", "UZ": "P"}
STATIC_DEVICE_TYPES = ("damper", "gap", "hook", "isolator")
_F6 = ("P", "V2", "V3", "T", "M2", "M3")


def _axial_def(lk, di, dj) -> float:
    """Basic deformation along element direction 1: global X for a
    zero-length element, the i->j axis otherwise (elongation > 0)."""
    e = [float(lk.pj[k]) - float(lk.pi[k]) for k in range(3)]
    L = math.sqrt(sum(v * v for v in e))
    if L < 1e-9:
        return float(dj[0]) - float(di[0])
    return sum((float(dj[k]) - float(di[k])) * e[k] / L for k in range(3))


def device_static_force(lk, di, dj) -> Optional[Dict[str, float]]:
    """Static basic forces of a device link from its solved end
    displacements (exact for a path-independent law and for monotonic
    loading of the bilinear isolator); ``None`` for unsupported types."""
    from .model import ISOLATOR_DEFAULT_KV
    t = getattr(lk, "link_type", "elastic")
    prm = getattr(lk, "params", {}) or {}
    f = {k: 0.0 for k in _F6}
    if t == "damper":
        return f                     # a viscous damper carries no static force
    if t in ("gap", "hook"):
        d = _axial_def(lk, di, dj)
        k = float(prm["k"])
        if t == "gap":
            g = float(prm["gap"])
            f["P"] = k * (d + g) if d < -g else 0.0
        else:
            s = float(prm["slack"])
            f["P"] = k * (d - s) if d > s else 0.0
        return f
    if t == "isolator":
        e = [float(lk.pj[k]) - float(lk.pi[k]) for k in range(3)]
        if math.sqrt(sum(v * v for v in e)) >= 1e-9:
            return None                       # local shear axes: TH only
        k1, k2, fy = float(prm["k1"]), float(prm["k2"]), float(prm["Fy"])
        dy = fy / k1

        def bil(d):
            if abs(d) <= dy:
                return k1 * d
            return math.copysign(fy + k2 * (abs(d) - dy), d)
        f["V2"] = bil(float(dj[0]) - float(di[0]))
        f["V3"] = bil(float(dj[1]) - float(di[1]))
        f["P"] = float(prm.get("kv", ISOLATOR_DEFAULT_KV)) * (
            float(dj[2]) - float(di[2]))
        return f
    return None


_LINK_EXTRA = [_col("step_type", "Step Type", "text"),
               _col("step", "Step Number", "id"),
               _col("time", "Time", "time")]


def _t_links_all(c) -> List[dict]:
    rows = _TR._t_link_forces(c)
    c.warnings = [w for w in c.warnings if not w.startswith("device links (")]
    links = [lk for lk in getattr(c.model, "links", []) or []
             if getattr(lk, "link_type", "elastic") != "elastic"]
    if not links:
        return rows
    th_rows: List[dict] = []
    static_ok = [lk for lk in links if device_static_force(
        lk, [0.0] * 6, [0.0] * 6) is not None]
    skipped = [lk.uid for lk in links if lk not in static_ok]
    per_case: Dict[str, Dict[str, Dict[str, float]]] = {}
    for name, ctype, cd in c.sources():
        nd = cd.get("node_disp", {})
        cb = c.model.combos.get(name) if ctype == "Combination" else None
        for lk in static_ok:
            if cb is not None:          # nonlinear: sum the factored cases
                f = {k: 0.0 for k in _F6}
                ok = True
                for cn, fac in cb.cases.items():
                    sub = per_case.get(cn, {}).get(lk.uid)
                    if sub is None:
                        ok = False
                        break
                    for k in _F6:
                        f[k] += float(fac) * sub[k]
                if not ok:
                    continue
            else:
                tags = _TR._link_tags(c, lk)
                if tags is None:
                    continue
                f = device_static_force(lk, nd.get(str(tags[0]), [0.0] * 6),
                                        nd.get(str(tags[1]), [0.0] * 6))
                per_case.setdefault(name, {})[lk.uid] = f
            z = 0.5 * (lk.pi[2] + lk.pj[2])
            st = c.story_at(z) or c.story_at(lk.pi[2])
            for loc in ("I-End", "J-End"):
                rows.append({"story": st, "link": lk.uid, "case": name,
                             "case_type": ctype, "location": loc, **f})
    luid = {lk.uid: lk for lk in links}
    for name, pf in _th_pf(c):
        ctype = _th_case_type(c, name)
        t = pf.get("t") or []
        for uid, rec in (pf.get("links") or {}).items():
            lk = luid.get(uid)
            if lk is None:
                continue
            force = rec.get("force") or {}
            cols = {}
            for lab, ser in force.items():
                col = _LABEL_COL.get(lab)
                if col and col not in cols:
                    cols[col] = ser
            n = max((len(v) for v in cols.values()), default=0)
            z = 0.5 * (lk.pi[2] + lk.pj[2])
            st = c.story_at(z) or c.story_at(lk.pi[2])
            base = {"story": st, "link": uid, "case": name,
                    "case_type": ctype, "location": "I-End"}
            for stp, fn in (("Max", max), ("Min", min)):
                th_rows.append({**base, **{k: (fn(cols[k]) if k in cols
                                               else None) for k in _F6},
                                "step_type": stp, "step": None,
                                "time": None})
            for i in range(n):
                th_rows.append({**base, **{k: (cols[k][i] if k in cols and
                                               i < len(cols[k]) else None)
                                           for k in _F6},
                                "step_type": "Step", "step": i,
                                "time": t[i] if i < len(t) else None})
    recorded = {u for _n, pf in _th_pf(c) for u in (pf.get("links") or {})}
    missing = [u for u in skipped if u not in recorded]
    if missing:
        c.warnings.append(
            "device links (" + ", ".join(missing) + ") have path-dependent "
            "or local-axis laws: their forces are tabulated from the "
            "time-history Output Requests (record the links)")
    if th_rows:
        for r in rows:
            r.setdefault("step_type", "")
            r.setdefault("step", None)
            r.setdefault("time", None)
        rows.extend(th_rows)
        c._link_extra_cols = True
    return rows


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #
TABLE_DEFS = [
    ("joint_velocities", "Joint Velocities", G_JOINT_TH, JOINT_VEL_COLS,
     _t_jvel),
    ("joint_velocities_absolute", "Joint Velocities - Absolute", G_JOINT_TH,
     JOINT_VEL_COLS, _t_jvel_abs),
    ("joint_accelerations", "Joint Accelerations", G_JOINT_TH,
     JOINT_ACC_COLS, _t_jacc),
    ("joint_accelerations_absolute", "Joint Accelerations - Absolute",
     G_JOINT_TH, JOINT_ACC_COLS, _t_jacc_abs),
    ("assembled_joint_masses", "Assembled Joint Masses", G_MASS,
     ASM_MASS_COLS, _t_assembled_masses),
    ("mass_summary_by_story", "Mass Summary by Story", G_MASS,
     MASS_STORY_COLS, _t_mass_story),
    ("mass_summary_by_diaphragm", "Mass Summary by Diaphragm", G_MASS,
     MASS_DIA_COLS, _t_mass_dia),
    ("mass_summary_by_group", "Mass Summary by Group", G_MASS,
     MASS_GROUP_COLS, _t_mass_group),
    ("story_max_avg_displacements", "Story Max Over Avg Displacements",
     G_STORY, STORY_MAXAVG_COLS, _t_story_maxavg),
    ("modal_load_participation", "Modal Load Participation Ratios",
     G_MODAL, MLP_COLS, _t_mlp),
    ("generalized_displacements", "Generalized Displacements", G_GD,
     GD_COLS, _t_gendisp),
    ("material_list_by_section", "Material List by Section Property",
     G_MAT, MAT_SECTION_COLS, _t_mat_section),
    ("material_list_by_story", "Material List by Story", G_MAT,
     MAT_STORY_COLS, _t_mat_story),
    ("material_list_by_material", "Material List by Material", G_MAT,
     MAT_MATERIAL_COLS, _t_mat_material),
]
_AFTER = {"story_max_avg_displacements": "story_stiffness",
          "modal_load_participation": "modal_direction_factors",
          "assembled_joint_masses": "joint_reactions"}


def register() -> None:
    prev = None
    for key, title, group, cols, fn in TABLE_DEFS:
        _T.register_table(key, title, group, cols, fn,
                          after=_AFTER.get(key, prev))
        prev = key
    title, group, cols, _fn = _T.TABLES["element_forces_links"]
    _T.register_table("element_forces_links", title, group, cols,
                      _t_links_all)


def decorate(key: str, cols: List[dict], rows: List[dict], c) -> List[dict]:
    """Element Forces - Links gains Step Type / Step / Time columns only
    when device-link time-history rows are present."""
    if key == "element_forces_links" and getattr(c, "_link_extra_cols",
                                                 False):
        return list(cols) + [dict(x) for x in _LINK_EXTRA]
    return cols
