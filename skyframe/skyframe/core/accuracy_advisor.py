"""Accuracy advisor (Model > Accuracy Advisor).

CONTRACT "Mesh sensitivity study and accuracy advisor".

STATIC (no solve) review of modelling choices that affect the ACCURACY of
the analysis results.  Every advice item explains why the choice matters
and suggests a fix; ``objects`` lists the members / shells involved so the
UI can select them.  The model is never modified.

Advice item::

    {"severity": "warning" | "info", "code", "title",
     "message",        # what was found (counts / worst value)
     "explanation",    # why it affects accuracy
     "fix",            # suggested remedy
     "objects": [uid, ...],               # <= MAX_OBJECTS
     "refs": [{"type": "member"|"shell", "uid"}, ...],
     "details": [{"uid", ...metric...}, ...]}             # <= MAX_OBJECTS

Codes (thresholds = ``DEFAULTS``, overridable per request):

* ``SHELL_ASPECT_RATIO``  a meshed shell element with longest / shortest
  side > ``aspect_max`` (4);
* ``SHELL_WARPED``  a 4-node element whose warp (out-of-plane distance of
  the corners from their mean plane / sqrt(element area)) > ``warp_max``
  (0.01);
* ``SHELL_MESH_COARSE``  a meshed region with fewer than
  ``min_elements_per_span`` (4) elements across its shortest in-plane
  dimension (element size > span / 4);
* ``MEMBRANE_SLAB_BENDING``  out-of-plane bending is ignored where it
  matters: a membrane-behaviour slab with an edge that has no supporting
  beam (its load share goes to the corner joints), or a meshed shell of
  ``shell_type == "membrane"`` that carries out-of-plane loads (slab area
  loads, wall wind pressure);
* ``PLATE_THIN_ON_THICK``  a Kirchhoff (thin) element family
  (``shell_thin`` / ``plate_thin``) on a region with t / span >
  ``thick_ratio`` (1/10): transverse-shear deformation is ignored;
* ``PLATE_THICK_ON_THIN`` (info)  a Mindlin (thick) family
  (``shell_thick`` / ``plate_thick``) on a region with t / span <
  ``thin_ratio`` (1/50);
* ``RIGID_DIAPHRAGM_OPENINGS``  a rigid diaphragm (story or named) is
  assumed on a slab whose openings remove >= ``opening_ratio`` (25 %) of its
  area;
* ``CRACKED_MODIFIERS_MISSING``  concrete frame sections with
  ``mod_I33 == mod_I22 == 1`` / concrete shell sections with every bending
  factor (``mod``, ``m11``, ``m22``, ``m12``) == 1 (concrete = material type
  "concrete" and E < 60 GPa);
* ``MASS_SOURCE_MISSING``  a dynamic analysis is requested (MODAL with
  num_modes > 0 and not switched off, or any RS / TH case) but the model has
  no mass (no story / nodal / additional mass and the mass source gives
  zero);
* ``FRAME_POINT_LOAD_NO_AUTOMESH``  a frame member with no effective auto
  mesh that carries a concentrated load (``MemberLoad`` kind point /
  moment) strictly inside its span and is attached to a meshed shell.
"""

from __future__ import annotations

import math
import warnings
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from .model import MODAL_CASE, BuildingModel

Vec3 = Tuple[float, float, float]

DEFAULTS = {
    "aspect_max": 4.0,
    "warp_max": 0.01,
    "min_elements_per_span": 4.0,
    "thick_ratio": 0.1,
    "thin_ratio": 0.02,
    "opening_ratio": 0.25,
}
CONCRETE_E_MAX = 60.0e6          # kPa; above this a "concrete" material is
#                                   treated as a mislabeled steel/other
MAX_OBJECTS = 50
SEVERITIES = ("warning", "info")
CODES = ("SHELL_ASPECT_RATIO", "SHELL_WARPED", "SHELL_MESH_COARSE",
         "MEMBRANE_SLAB_BENDING", "PLATE_THIN_ON_THICK",
         "PLATE_THICK_ON_THIN", "RIGID_DIAPHRAGM_OPENINGS",
         "CRACKED_MODIFIERS_MISSING", "MASS_SOURCE_MISSING",
         "FRAME_POINT_LOAD_NO_AUTOMESH")
THIN_TYPES = ("shell_thin", "plate_thin")
THICK_TYPES = ("shell_thick", "plate_thick")
_KEY = 6


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 0 else (0.0, 0.0, 0.0)


def _key(p) -> Vec3:
    return tuple(round(float(v), _KEY) for v in p)


def _newell(pts: Sequence[Vec3]) -> Vec3:
    n = [0.0, 0.0, 0.0]
    for i, p in enumerate(pts):
        q = pts[(i + 1) % len(pts)]
        n[0] += (p[1] - q[1]) * (p[2] + q[2])
        n[1] += (p[2] - q[2]) * (p[0] + q[0])
        n[2] += (p[0] - q[0]) * (p[1] + q[1])
    return tuple(n)


def region_span(region) -> float:
    """Shortest in-plane extent of a region (m): the smaller side of its
    in-plane bounding box aligned with the first edge (4-corner
    rectangles: the shorter side)."""
    c = [tuple(map(float, p)) for p in region.corners]
    n = _unit(_newell(c))
    u = _unit(_sub(c[1], c[0]))
    v = _unit(_cross(n, u))
    us = [_dot(_sub(p, c[0]), u) for p in c]
    vs = [_dot(_sub(p, c[0]), v) for p in c]
    return min(max(us) - min(us), max(vs) - min(vs))


def element_metrics(pts: Sequence[Vec3]) -> Tuple[float, float, float]:
    """(aspect ratio, warp ratio, longest side) of one shell element.

    aspect = longest / shortest side; warp (4-node only) = max distance of
    the corners from the mean plane through their centroid (normal = the
    diagonal cross product) divided by sqrt(area)."""
    k = len(pts)
    sides = [_norm(_sub(pts[(i + 1) % k], pts[i])) for i in range(k)]
    lo = min(sides)
    aspect = math.inf if lo <= 0.0 else max(sides) / lo
    warp = 0.0
    if k == 4:
        nrm = _cross(_sub(pts[2], pts[0]), _sub(pts[3], pts[1]))
        area = 0.5 * _norm(nrm)
        if area > 0.0:
            nu = _unit(nrm)
            cen = tuple(sum(p[i] for p in pts) / 4.0 for i in range(3))
            h = max(abs(_dot(_sub(p, cen), nu)) for p in pts)
            warp = h / math.sqrt(area)
    return aspect, warp, max(sides)


# --------------------------------------------------------------------------- #
# advice records
# --------------------------------------------------------------------------- #
TEXT = {
    "SHELL_ASPECT_RATIO": (
        "warning", "Shell elements with a high aspect ratio",
        "Elongated elements stiffen artificially (shear / membrane locking) "
        "and interpolate stresses poorly along their long side, so "
        "displacements are under-estimated and moments are smeared.",
        "Set Assign > Shell > Auto Mesh divisions (n1 x n2) or a maximum "
        "element size so that the element sides are within 1:{aspect_max:g} "
        "(close to square is best)."),
    "SHELL_WARPED": (
        "warning", "Warped quadrilateral shell elements",
        "A flat 4-node element whose corners are not coplanar loses "
        "compatibility with its neighbours and mis-represents bending; "
        "results become mesh dependent.",
        "Make the region corners coplanar or split the region into planar "
        "pieces / triangles."),
    "SHELL_MESH_COARSE": (
        "warning", "Shell mesh coarse relative to the span",
        "With fewer than {min_elements_per_span:g} elements across a span "
        "the deflected shape and the moment distribution (especially peak "
        "and support moments) are not resolved.",
        "Reduce the mesh size to at most span / {min_elements_per_span:g} "
        "and confirm with Analyze > Mesh Sensitivity Study."),
    "MEMBRANE_SLAB_BENDING": (
        "warning", "Membrane slab where out-of-plane bending matters",
        "A membrane slab (or a membrane-only shell type) has no plate "
        "bending stiffness: loads are passed to supporting beams / corner "
        "joints by tributary rules, so slab deflections, slab moments and "
        "load paths through unsupported edges are not computed.",
        "Use behavior 'shell' (or shell_type shell / shell_thick) for slabs "
        "without beams on every edge, for flat plates and for any shell "
        "carrying out-of-plane loads."),
    "PLATE_THIN_ON_THICK": (
        "warning", "Thin-plate (Kirchhoff) element on a thick slab",
        "Kirchhoff elements ignore transverse-shear deformation; for "
        "t / span > {thick_ratio:g} that deformation is significant, so "
        "deflections are under-estimated and support shears redistribute.",
        "Use shell_type shell_thick (Mindlin, ASDShellQ4) or the default "
        "shell for thick slabs, walls, transfer slabs and mats."),
    "PLATE_THICK_ON_THIN": (
        "info", "Thick-plate (Mindlin) element on a very thin slab",
        "For t / span < {thin_ratio:g} the shear deformation is negligible; "
        "thick-plate elements are still valid but are more sensitive to "
        "mesh distortion (shear locking on coarse or skewed meshes).",
        "Either keep shell_thick and refine / regularise the mesh, or use "
        "shell_thin (Kirchhoff, DKGQ) for thin plates."),
    "RIGID_DIAPHRAGM_OPENINGS": (
        "warning", "Rigid diaphragm assumed on a slab with large openings",
        "A rigid diaphragm forces every slab joint to move together in "
        "plan; large openings make the real floor flexible in-plane, so "
        "lateral force distribution to walls / frames and diaphragm "
        "forces around the openings are wrong.",
        "Assign a semi-rigid diaphragm (story diaphragm 'none' + a meshed "
        "shell slab, or a named semi_rigid diaphragm) to that floor."),
    "CRACKED_MODIFIERS_MISSING": (
        "warning", "No cracked-section modifiers on concrete",
        "Gross (uncracked) concrete stiffness over-estimates the lateral "
        "and flexural stiffness: periods come out short, drifts and "
        "deflections low, and forces are distributed to the wrong members.",
        "Assign property modifiers, e.g. ACI 318 6.6.3.1.1: beams I = 0.35 "
        "Ig, columns 0.70 Ig, walls 0.35-0.70 Ig, flat slabs 0.25 Ig "
        "(frame mod_I33 / mod_I22; shell m11 / m22 / m12)."),
    "MASS_SOURCE_MISSING": (
        "warning", "Mass source missing",
        "Modal, response-spectrum and time-history analyses need mass; "
        "without it no modes are found (or only explicit masses respond), "
        "so periods and dynamic forces are meaningless.",
        "Define > Mass Source: include the self-weight / dead patterns "
        "(factor 1) and the seismic share of live load, or add story / "
        "joint masses."),
    "FRAME_POINT_LOAD_NO_AUTOMESH": (
        "warning", "Frame with span point loads attached to shells "
        "without auto mesh",
        "The member is connected to the shell only at its end joints (and "
        "at mesh nodes that happen to lie on its axis); a concentrated load "
        "in its span reaches the slab / wall only through those joints, so "
        "the frame-shell interaction and the local moments are wrong.",
        "Assign > Frame > Auto Mesh: enable 'at intermediate joints' and "
        "'at intersections' and set a maximum segment length comparable "
        "to the shell mesh size."),
}


class _Collector:
    def __init__(self, th: dict):
        self.th = th
        self.items: Dict[str, dict] = {}

    def add(self, code: str, uid: str, kind: str, detail: dict) -> None:
        it = self.items.get(code)
        if it is None:
            it = self.items[code] = {"objects": [], "refs": [],
                                     "details": [], "_n": 0}
        it["_n"] += 1
        if not uid:
            return
        if uid not in it["objects"] and len(it["objects"]) < MAX_OBJECTS:
            it["objects"].append(uid)
            it["refs"].append({"type": kind, "uid": uid})
        if len(it["details"]) < MAX_OBJECTS:
            it["details"].append({"uid": uid, **detail})

    def advice(self, messages: Dict[str, str]) -> List[dict]:
        out = []
        for code in CODES:
            it = self.items.get(code)
            if it is None:
                continue
            sev, title, expl, fix = TEXT[code]
            out.append({"severity": sev, "code": code, "title": title,
                        "message": messages.get(code, ""),
                        "explanation": expl.format(**self.th),
                        "fix": fix.format(**self.th),
                        "count": it["_n"],
                        "objects": list(it["objects"]),
                        "refs": list(it["refs"]),
                        "details": list(it["details"])})
        order = {s: k for k, s in enumerate(SEVERITIES)}
        out.sort(key=lambda a: order[a["severity"]])
        return out


def normalize_thresholds(th) -> dict:
    out = dict(DEFAULTS)
    if th is None:
        return out
    if not isinstance(th, dict):
        raise ValueError("'thresholds' must be an object")
    for k, v in th.items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown threshold {k!r}; allowed: "
                             f"{sorted(DEFAULTS)}")
        if (isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(v) or v <= 0.0):
            raise ValueError(f"threshold {k!r} must be a finite number > 0")
        out[k] = float(v)
    return out


# --------------------------------------------------------------------------- #
# individual checks
# --------------------------------------------------------------------------- #
def _mesh(model):
    from .mesh import mesh_model
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return mesh_model(model)
        except Exception:                # advisor never raises on defects
            return None


def _shell_mesh_checks(model, mesh, col: _Collector, msg: dict) -> None:
    th = col.th
    if mesh is None:
        return
    by_region: Dict[str, List[Tuple[float, float, float]]] = defaultdict(
        list)
    for q in mesh.quads:
        pts = [mesh.points[i] for i in q.nodes]
        by_region[q.region].append(element_metrics(pts))
    worst = {"SHELL_ASPECT_RATIO": 0.0, "SHELL_WARPED": 0.0,
             "SHELL_MESH_COARSE": math.inf}
    for r in model.shells:
        mets = by_region.get(r.uid)
        if not mets:
            continue
        n_bad = sum(1 for a, _, _ in mets if a > th["aspect_max"])
        if n_bad:
            amax = max(a for a, _, _ in mets)
            col.add("SHELL_ASPECT_RATIO", r.uid, "shell",
                    {"max_aspect": round(amax, 3), "n_elements": n_bad})
            worst["SHELL_ASPECT_RATIO"] = max(
                worst["SHELL_ASPECT_RATIO"], amax)
        n_warp = sum(1 for _, w, _ in mets if w > th["warp_max"])
        if n_warp:
            wmax = max(w for _, w, _ in mets)
            col.add("SHELL_WARPED", r.uid, "shell",
                    {"max_warp": round(wmax, 5), "n_elements": n_warp})
            worst["SHELL_WARPED"] = max(worst["SHELL_WARPED"], wmax)
        span = region_span(r)
        h = max(s for _, _, s in mets)
        if span > 0.0 and h > 0.0:
            across = span / h
            if across < th["min_elements_per_span"] - 1e-9:
                col.add("SHELL_MESH_COARSE", r.uid, "shell",
                        {"span": round(span, 6), "element_size": round(h, 6),
                         "elements_across": round(across, 3)})
                worst["SHELL_MESH_COARSE"] = min(
                    worst["SHELL_MESH_COARSE"], across)
    for code, txt in (
            ("SHELL_ASPECT_RATIO",
             "{n} region(s) contain elements with aspect ratio > {lim:g} "
             "(worst {w:.3g})."),
            ("SHELL_WARPED",
             "{n} region(s) contain warped elements (worst warp "
             "{w:.3g} > {lim:g})."),
            ("SHELL_MESH_COARSE",
             "{n} region(s) have fewer than {lim:g} elements across their "
             "shortest span (worst {w:.3g}).")):
        it = col.items.get(code)
        if it is not None:
            lim = {"SHELL_ASPECT_RATIO": th["aspect_max"],
                   "SHELL_WARPED": th["warp_max"],
                   "SHELL_MESH_COARSE": th["min_elements_per_span"]}[code]
            msg[code] = txt.format(n=it["_n"], lim=lim, w=worst[code])


def _loaded_out_of_plane(model, r) -> bool:
    if r.kind == "wall":
        return getattr(r, "wind_cp", None) is not None
    for pat in model.patterns.values():
        if any(al.region_uid == r.uid for al in pat.area_loads):
            return True
        if getattr(pat, "self_weight_factor", 0.0):
            return True
    return bool(getattr(r, "load_set", ""))


def _membrane_checks(model, col: _Collector, msg: dict) -> None:
    from .mesh import edge_index_scope
    with edge_index_scope(model):          # exact edge-beam pre-filter
        _membrane_checks_body(model, col, msg)


def _membrane_checks_body(model, col: _Collector, msg: dict) -> None:
    from .mesh import _members_on_edge
    from .shell_types import shell_type_of
    n_edge, n_type = 0, 0
    for r in model.shells:
        ssec = model.shell_sections.get(r.section)
        if r.behavior == "membrane" and r.kind == "slab":
            c = [tuple(map(float, p)) for p in r.corners]
            bare = []
            for i in range(len(c)):
                pa, pb = c[i], c[(i + 1) % len(c)]
                L = _norm(_sub(pb, pa))
                if L <= 0.0:
                    continue
                cover = 0.0
                for _m, s0, s1 in _members_on_edge(model, pa, pb):
                    lo, hi = max(0.0, min(s0, s1)), min(L, max(s0, s1))
                    cover += max(0.0, hi - lo)
                if cover < 0.99 * L:
                    bare.append(i)
            if bare:
                n_edge += 1
                col.add("MEMBRANE_SLAB_BENDING", r.uid, "shell",
                        {"reason": "edge_without_beam",
                         "edges": bare})
        elif (r.behavior == "shell" and ssec is not None
              and shell_type_of(ssec) == "membrane"
              and _loaded_out_of_plane(model, r)):
            n_type += 1
            col.add("MEMBRANE_SLAB_BENDING", r.uid, "shell",
                    {"reason": "membrane_type_out_of_plane_load"})
    if n_edge or n_type:
        parts = []
        if n_edge:
            parts.append(f"{n_edge} membrane slab(s) have edge(s) without "
                         "supporting beams")
        if n_type:
            parts.append(f"{n_type} membrane-type shell(s) carry "
                         "out-of-plane loads")
        msg["MEMBRANE_SLAB_BENDING"] = "; ".join(parts) + "."


def _plate_theory_checks(model, col: _Collector, msg: dict) -> None:
    from .shell_types import shell_type_of
    th = col.th
    worst_thick, worst_thin = 0.0, math.inf
    for r in model.shells:
        if r.behavior != "shell":
            continue
        ssec = model.shell_sections.get(r.section)
        if ssec is None:
            continue
        st = shell_type_of(ssec)
        span = region_span(r)
        if span <= 0.0:
            continue
        ratio = float(ssec.total_thickness) / span
        if st in THIN_TYPES and ratio > th["thick_ratio"]:
            col.add("PLATE_THIN_ON_THICK", r.uid, "shell",
                    {"shell_type": st, "t_over_span": round(ratio, 5)})
            worst_thick = max(worst_thick, ratio)
        elif st in THICK_TYPES and ratio < th["thin_ratio"]:
            col.add("PLATE_THICK_ON_THIN", r.uid, "shell",
                    {"shell_type": st, "t_over_span": round(ratio, 5)})
            worst_thin = min(worst_thin, ratio)
    it = col.items.get("PLATE_THIN_ON_THICK")
    if it:
        msg["PLATE_THIN_ON_THICK"] = (
            f"{it['_n']} region(s) use a thin-plate element with t / span "
            f"> {th['thick_ratio']:g} (worst {worst_thick:.3g}).")
    it = col.items.get("PLATE_THICK_ON_THIN")
    if it:
        msg["PLATE_THICK_ON_THIN"] = (
            f"{it['_n']} region(s) use a thick-plate element with t / span "
            f"< {th['thin_ratio']:g} (smallest {worst_thin:.3g}).")


def _region_rigid(model, r) -> bool:
    name = getattr(r, "diaphragm", "") or ""
    if name:
        d = (getattr(model, "diaphragms", None) or {}).get(name) or {}
        return d.get("type", "rigid") == "rigid"
    story = r.story
    if not story:
        z = [float(p[2]) for p in r.corners]
        if max(z) - min(z) > 1e-6:
            return False
        for s in model.stories:
            if abs(s.elevation - z[0]) < 1e-6:
                story = s.name
        if not story:
            return False
    return model.effective_diaphragm(story) == "rigid"


def _diaphragm_checks(model, col: _Collector, msg: dict) -> None:
    th = col.th
    worst = 0.0
    for r in model.shells:
        if r.kind != "slab" or not r.openings:
            continue
        try:
            area = r.area
            ratio = r.opening_area / area if area > 0.0 else 0.0
        except Exception:
            continue
        if ratio >= th["opening_ratio"] and _region_rigid(model, r):
            col.add("RIGID_DIAPHRAGM_OPENINGS", r.uid, "shell",
                    {"opening_ratio": round(ratio, 4)})
            worst = max(worst, ratio)
    it = col.items.get("RIGID_DIAPHRAGM_OPENINGS")
    if it:
        msg["RIGID_DIAPHRAGM_OPENINGS"] = (
            f"{it['_n']} rigid-diaphragm slab(s) have openings >= "
            f"{th['opening_ratio'] * 100:g}% of their area (worst "
            f"{worst * 100:.3g}%).")


def _is_concrete(model, mat_name: str) -> bool:
    mat = model.materials.get(mat_name)
    return (mat is not None
            and getattr(mat, "material_type", "concrete") == "concrete"
            and 0.0 < float(mat.E) < CONCRETE_E_MAX)


def _cracked_checks(model, col: _Collector, msg: dict) -> None:
    secs = []
    for name, sec in model.sections.items():
        if (_is_concrete(model, sec.material)
                and float(getattr(sec, "mod_I33", 1.0)) == 1.0
                and float(getattr(sec, "mod_I22", 1.0)) == 1.0):
            users = [m.uid for m in model.members if m.section == name]
            if users:
                secs.append(name)
                for u in users:
                    col.add("CRACKED_MODIFIERS_MISSING", u, "member",
                            {"section": name})
    ssecs = []
    for name, ssec in model.shell_sections.items():
        if not _is_concrete(model, ssec.material):
            continue
        if any(float(getattr(ssec, k, 1.0)) != 1.0
               for k in ("mod", "m11", "m22", "m12")):
            continue
        users = [r.uid for r in model.shells
                 if r.section == name and r.behavior == "shell"]
        if users:
            ssecs.append(name)
            for u in users:
                col.add("CRACKED_MODIFIERS_MISSING", u, "shell",
                        {"shell_section": name})
    if secs or ssecs:
        parts = []
        if secs:
            parts.append(f"frame section(s) {', '.join(secs[:10])}")
        if ssecs:
            parts.append(f"shell section(s) {', '.join(ssecs[:10])}")
        msg["CRACKED_MODIFIERS_MISSING"] = (
            "Concrete " + " and ".join(parts) + " use gross stiffness "
            "(all bending modifiers = 1).")


def dynamic_requested(model) -> bool:
    off = set(getattr(model, "cases_not_run", []) or [])
    if (model.num_modes > 0 and MODAL_CASE not in off
            and MODAL_CASE not in model.cases):
        return True
    return any(n not in off for n in list(model.rs_cases)
               + list(model.th_cases))


def total_mass(model) -> float:
    """Tonnes of translational mass the model provides (any source)."""
    tot = 0.0
    try:
        tot += sum(model.compute_story_masses().values())
    except Exception:
        pass
    for nm in model.nodal_masses:
        tot += abs(nm.mx) + abs(nm.my) + abs(nm.mz)
    for m in model.members:
        tot += float(getattr(m, "additional_mass", 0.0) or 0.0) * m.length
    for r in model.shells:
        am = float(getattr(r, "additional_mass", 0.0) or 0.0)
        if am:
            try:
                tot += am * r.net_area
            except Exception:
                tot += am
    if getattr(model, "mass_source_mode", "weight") == "element_self_mass":
        for m in model.members:
            sec = model.sections.get(m.section)
            mat = model.materials.get(sec.material) if sec else None
            if sec is not None and mat is not None:
                tot += mat.mass_per_volume * sec.A * m.length
        for r in model.shells:
            ssec = model.shell_sections.get(r.section)
            mat = model.materials.get(ssec.material) if ssec else None
            if ssec is not None and mat is not None:
                try:
                    tot += mat.mass_per_volume * ssec.total_thickness \
                        * r.net_area
                except Exception:
                    pass
    return tot


def _mass_checks(model, col: _Collector, msg: dict) -> None:
    if not dynamic_requested(model) or total_mass(model) > 0.0:
        return
    col.add("MASS_SOURCE_MISSING", "", "model", {})
    src = model.effective_mass_source()
    msg["MASS_SOURCE_MISSING"] = (
        "Dynamic analysis is requested but the model has no mass "
        + ("(no mass source defined)." if not src else
           f"(mass source {sorted(src)} gives zero mass)."))


def _frame_point_load_checks(model, mesh, col: _Collector, msg: dict
                             ) -> None:
    from . import framemesh as _fm
    loaded: Dict[str, List[float]] = defaultdict(list)
    for pat in model.patterns.values():
        for ml in pat.member_loads:
            if ml.kind in ("point", "moment") and 1e-6 < ml.a < 1 - 1e-6:
                loaded[ml.member_uid].append(float(ml.a))
    if not loaded:
        return
    shell_keys = set()
    seg_count: Dict[str, int] = {}
    if mesh is not None:
        for q in mesh.quads:
            for i in q.nodes:
                shell_keys.add(_key(mesh.points[i]))
        seg_count = {u: len(s) for u, s in mesh.segments.items()}
    else:
        for r in model.shells:
            for p in r.corners:
                shell_keys.add(_key(p))
    for m in model.members:
        if m.uid not in loaded:
            continue
        if getattr(m, "axial_limit", "both") != "both":
            continue
        if _fm.effective_auto_mesh(model, m) is not None:
            continue
        ends = sum(1 for p in (m.pi, m.pj) if _key(p) in shell_keys)
        nseg = seg_count.get(m.uid, 1)
        if ends == 0 and nseg <= 1:
            continue
        col.add("FRAME_POINT_LOAD_NO_AUTOMESH", m.uid, "member",
                {"load_positions": sorted(set(round(a, 6)
                                              for a in loaded[m.uid])),
                 "shell_joints_at_ends": ends, "analysis_segments": nseg})
    it = col.items.get("FRAME_POINT_LOAD_NO_AUTOMESH")
    if it:
        msg["FRAME_POINT_LOAD_NO_AUTOMESH"] = (
            f"{it['_n']} frame member(s) with span point loads are attached "
            "to shells but have no frame auto mesh.")


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def accuracy_advice(model: BuildingModel, thresholds=None) -> dict:
    """All accuracy advice for ``model`` (never raises on model defects).

    Returns ``{"advice": [...], "summary": {"warnings", "info", "by_code",
    "n_shells", "n_members"}, "thresholds": {...}}``."""
    th = normalize_thresholds(thresholds)
    col = _Collector(th)
    msg: Dict[str, str] = {}
    mesh = _mesh(model)
    for fn in (lambda: _shell_mesh_checks(model, mesh, col, msg),
               lambda: _membrane_checks(model, col, msg),
               lambda: _plate_theory_checks(model, col, msg),
               lambda: _diaphragm_checks(model, col, msg),
               lambda: _cracked_checks(model, col, msg),
               lambda: _mass_checks(model, col, msg),
               lambda: _frame_point_load_checks(model, mesh, col, msg)):
        try:
            fn()
        except Exception:               # one bad object never hides the rest
            continue
    advice = col.advice(msg)
    by_code = {a["code"]: a["count"] for a in advice}
    return {"advice": advice,
            "summary": {"warnings": sum(1 for a in advice
                                        if a["severity"] == "warning"),
                        "info": sum(1 for a in advice
                                    if a["severity"] == "info"),
                        "by_code": by_code,
                        "ok": not advice,
                        "n_shells": len(model.shells),
                        "n_members": len(model.members)},
            "thresholds": th}
