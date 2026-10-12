"""Rhino ``.3dm`` reader -> ``skyframe.exchange/1`` payload (via the
optional ``rhino3dm`` package, ``pip install "skyframe[rhino]"``).

The payload is then previewed / applied by :mod:`skyframe.core.rhino_exchange`
exactly like one sent by the Grasshopper plugin.  What is read:

* units + absolute tolerance from the file settings (Rhino units outside
  m / mm / cm / ft / in are converted to the nearest supported unit);
* layers: full path (``Parent::Child``) and colour;
* object id = the Rhino object GUID (stable across saves); a polycurve with
  curved segments and a multi-face brep give one exchange object per
  segment / face, id ``<guid>/<k>``;
* UserText: object-attribute user strings (Rhino's "Attribute User Text"),
  falling back to geometry user strings for the same key;
* curves: line, polyline, arc (also NURBS that are exact arcs), other
  NURBS as ``nurbs`` with ``samples`` at the file tolerance (adaptive
  subdivision until the chord deviation is below the tolerance);
* breps / extrusions / surfaces: planar faces -> ``polygon`` with the
  outer loop + inner loops (holes), loop edges sampled at the tolerance;
  non-planar faces -> ``nurbs_surface`` with the render (else analysis)
  mesh stored in the file; without a stored mesh the face is skipped with
  a warning asking to save with render meshes or to use the GH link;
* meshes (triangles + quads) and points.  Block instances, SubD, point
  clouds, annotations etc. are skipped (counted in a warning).
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Dict, List, Optional, Tuple

INSTALL_HINT = ("Rhino .3dm support needs the optional 'rhino3dm' package "
                "(version 8.x): pip install \"skyframe[rhino]\"  (or: pip "
                "install \"rhino3dm>=8\")")

MAX_SAMPLES = 2000
MAX_DEPTH = 14


class RhinoUnavailable(RuntimeError):
    """rhino3dm is not installed (message = :data:`INSTALL_HINT`)."""


def _r3():
    try:
        import rhino3dm  # noqa: F401
    except ImportError:
        raise RhinoUnavailable(INSTALL_HINT) from None
    return rhino3dm


def available() -> bool:
    try:
        _r3()
        return True
    except RhinoUnavailable:
        return False


def version() -> Optional[str]:
    try:
        return getattr(_r3(), "__version__", None)
    except RhinoUnavailable:
        return None


# Rhino UnitSystem name -> (exchange unit, factor to that unit)
_UNITS = {"Millimeters": ("mm", 1.0), "Centimeters": ("cm", 1.0),
          "Meters": ("m", 1.0), "Feet": ("ft", 1.0), "Inches": ("in", 1.0),
          "Microns": ("mm", 1e-3), "Decimeters": ("cm", 10.0),
          "Dekameters": ("m", 10.0), "Hectometers": ("m", 100.0),
          "Kilometers": ("m", 1000.0), "Yards": ("ft", 3.0),
          "Miles": ("ft", 5280.0), "Mils": ("in", 1e-3),
          "Microinches": ("in", 1e-6), "Nanometers": ("mm", 1e-6),
          "Angstroms": ("mm", 1e-7)}


def session_for(path: str) -> str:
    """Stable session id of a .3dm file: hash of its absolute path."""
    ap = os.path.normcase(os.path.abspath(path))
    return "rhino3dm:" + hashlib.sha1(ap.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #
def _p(pt, f: float) -> List[float]:
    return [float(pt.X) * f, float(pt.Y) * f, float(pt.Z) * f]


def _seg_dev(a, b, m) -> float:
    ab = [b[i] - a[i] for i in range(3)]
    am = [m[i] - a[i] for i in range(3)]
    L2 = sum(x * x for x in ab)
    if L2 <= 1e-24:
        return math.sqrt(sum(x * x for x in am))
    t = max(0.0, min(1.0, sum(ab[i] * am[i] for i in range(3)) / L2))
    return math.sqrt(sum((am[i] - ab[i] * t) ** 2 for i in range(3)))


def sample_curve(crv, tol: float, f: float = 1.0) -> List[List[float]]:
    """Points along ``crv`` (in file units x ``f``) with chord deviation
    below ``tol`` (file units): adaptive midpoint subdivision, started
    from 2 sub-intervals per span (tests the 1/4 and 3/4 points too so a
    symmetric S-curve is not missed)."""
    dom = crv.Domain
    t0, t1 = float(dom.T0), float(dom.T1)
    n0 = max(4, 2 * int(getattr(crv, "SpanCount", 1) or 1))
    ts = [t0 + (t1 - t0) * k / n0 for k in range(n0 + 1)]

    def P(t):
        return _p(crv.PointAt(t), 1.0)
    out = [P(ts[0])]

    def rec(a, b, pa, pb, depth):
        if len(out) >= MAX_SAMPLES:
            out.append(pb)
            return
        m = 0.5 * (a + b)
        pm = P(m)
        q1, q3 = P(0.5 * (a + m)), P(0.5 * (m + b))
        dev = max(_seg_dev(pa, pb, pm), _seg_dev(pa, pb, q1),
                  _seg_dev(pa, pb, q3))
        if dev <= tol or depth >= MAX_DEPTH:
            out.append(pb)
            return
        rec(a, m, pa, pm, depth + 1)
        rec(m, b, pm, pb, depth + 1)
    for a, b in zip(ts[:-1], ts[1:]):
        rec(a, b, P(a), P(b), 0)
    return [[x * f for x in p] for p in out]


def _curve_geom(r3, crv, tol: float, f: float):
    """(geom, warning) of one curve (None when unsupported)."""
    closed = bool(crv.IsClosed)
    if isinstance(crv, r3.LineCurve) or (not closed and crv.IsLinear(tol)):
        return {"type": "line", "p": [_p(crv.PointAtStart, f),
                                      _p(crv.PointAtEnd, f)]}, None
    if isinstance(crv, r3.PolylineCurve):
        return {"type": "polyline",
                "p": [_p(crv.Point(i), f) for i in range(crv.PointCount)]}, \
            None
    if not closed and crv.IsArc(tol):
        arc = crv.TryGetArc(tol)
        if arc is not None and arc.IsValid:
            return {"type": "arc", "p": [_p(arc.StartPoint, f),
                                         _p(arc.MidPoint, f),
                                         _p(arc.EndPoint, f)]}, None
    if crv.IsPolyline():
        pl = crv.TryGetPolyline()
        if pl is not None and pl.Count >= 2:
            return {"type": "polyline",
                    "p": [_p(pl[i], f) for i in range(pl.Count)]}, None
    nc = crv.ToNurbsCurve()
    if nc is None:
        return None, "curve could not be converted to NURBS"
    cv, w = [], []
    for i in range(len(nc.Points)):
        q = nc.Points[i]
        wi = float(q.W) if q.W else 1.0
        cv.append([float(q.X) / wi * f, float(q.Y) / wi * f,
                   float(q.Z) / wi * f])
        w.append(wi)
    return {"type": "nurbs", "degree": int(nc.Degree), "cv": cv,
            "weights": w, "knots": [float(nc.Knots[i])
                                    for i in range(len(nc.Knots))],
            "periodic": bool(nc.IsPeriodic),
            "samples": sample_curve(nc, tol, f)}, None


def _loop_points(brep, loop, tol: float, f: float) -> List[List[float]]:
    pts: List[List[float]] = []
    for ti in range(loop.TrimCount):
        tr = loop.Trims[ti]
        ei = tr.EdgeIndex
        if ei is None or ei < 0:
            continue
        e = brep.Edges[ei]
        if e.IsLinear(tol):
            seg = [_p(e.PointAtStart, f), _p(e.PointAtEnd, f)]
        else:
            seg = sample_curve(e, tol, f)
        if tr.IsReversed:
            seg = seg[::-1]
        if pts and math.dist(pts[-1], seg[0]) <= tol * f:
            seg = seg[1:]
        pts.extend(seg)
    if len(pts) > 1 and math.dist(pts[0], pts[-1]) <= tol * f:
        pts.pop()
    return pts


def _mesh_geom(r3, mesh, f: float) -> Optional[dict]:
    if mesh is None or len(mesh.Vertices) == 0 or len(mesh.Faces) == 0:
        return None
    v = [_p(mesh.Vertices[i], f) for i in range(len(mesh.Vertices))]
    faces = []
    for i in range(len(mesh.Faces)):
        a, b, c, d = mesh.Faces[i]
        faces.append([a, b, c] if c == d else [a, b, c, d])
    return {"v": v, "f": faces}


def _brep_objects(r3, brep, tol: float, f: float, oid: str,
                  warn: List[str]) -> List[Tuple[str, str, dict]]:
    """[(id, kind, geom)] of every face of a brep."""
    out = []
    nf = len(brep.Faces)
    if nf > 1 and getattr(brep, "IsSolid", False):
        warn.append(f"{oid}: closed (solid) brep -- each face imported as "
                    "a shell; model the mid-surfaces instead")
    no_mesh = 0
    for k in range(nf):
        face = brep.Faces[k]
        fid = oid if nf == 1 else f"{oid}/{k}"
        if face.IsPlanar(tol):
            outer, holes = None, []
            for li in range(len(face.Loops)):
                lp = face.Loops[li]
                pts = _loop_points(brep, lp, tol, f)
                if len(pts) < 3:
                    continue
                if str(lp.LoopType).endswith("Outer"):
                    outer = pts
                elif str(lp.LoopType).endswith("Inner"):
                    holes.append(pts)
            if outer is None:
                warn.append(f"{fid}: planar face without an outer loop -- "
                            "skipped")
                continue
            out.append((fid, "surface", {"type": "polygon", "outer": outer,
                                         "holes": holes}))
            continue
        mesh = None
        for mt in ("Render", "Analysis", "Any"):
            mesh = face.GetMesh(getattr(r3.MeshType, mt))
            if mesh is not None and len(mesh.Faces):
                break
            mesh = None
        g = _mesh_geom(r3, mesh, f)
        if g is None:
            no_mesh += 1
            continue
        out.append((fid, "surface", {"type": "nurbs_surface", "mesh": g}))
    if no_mesh:
        warn.append(f"{oid}: {no_mesh} non-planar face(s) have no render "
                    "mesh stored in the file -- skipped.  In Rhino, save "
                    "with render meshes (File > Save As > 'Save small' "
                    "unchecked, shaded viewport once) or send the surface "
                    "through the Grasshopper link, which meshes it")
    return out


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def read_3dm(src, *, session: Optional[str] = None, doc: Optional[str] = None,
             include_hidden: bool = True) -> dict:
    """``src`` = file path or the file's bytes -> exchange payload (plus
    ``warnings``).  Raises :class:`RhinoUnavailable` without rhino3dm and
    ValueError for an unreadable file."""
    r3 = _r3()
    if isinstance(src, (bytes, bytearray)):
        model = r3.File3dm.FromByteArray(bytes(src))
        if session is None:
            session = "rhino3dm:" + hashlib.sha1(
                (doc or "upload.3dm").encode("utf-8")).hexdigest()[:16]
    else:
        path = str(src)
        if not os.path.isfile(path):
            raise ValueError(f"file not found: {path}")
        model = r3.File3dm.Read(path)
        session = session or session_for(path)
        doc = doc or os.path.basename(path)
    if model is None:
        raise ValueError("not a readable Rhino .3dm file")
    st = model.Settings
    uname = str(st.ModelUnitSystem).split(".")[-1]
    warn: List[str] = []
    unit, f = _UNITS.get(uname, (None, 1.0))
    if unit is None:
        warn.append(f"file units {uname!r} unknown -- assumed metres")
        unit, f = "m", 1.0
    tol = float(st.ModelAbsoluteTolerance or 0.0)
    if not (tol > 0 and math.isfinite(tol)):
        tol = 0.001 if unit == "m" else 0.01
        warn.append(f"file tolerance unset -- {tol:g} {uname} used")
    layers: Dict[int, dict] = {}
    for i in range(len(model.Layers)):
        ly = model.Layers[i]
        c = ly.Color
        layers[int(ly.Index)] = {
            "path": ly.FullPath or ly.Name,
            "color": "#{:02X}{:02X}{:02X}".format(int(c[0]), int(c[1]),
                                                  int(c[2])),
            "visible": bool(ly.Visible)}
    objects = []
    skipped: Dict[str, int] = {}
    for ob in model.Objects:
        geo, at = ob.Geometry, ob.Attributes
        if not include_hidden and not at.Visible:
            continue
        oid = str(at.Id)
        lay = layers.get(int(at.LayerIndex), {"path": ""})["path"]
        attrs: Dict[str, str] = {}
        try:
            for k, v in geo.GetUserStrings() or ():
                attrs[str(k)] = str(v)
        except Exception:                               # pragma: no cover
            pass
        for k, v in at.GetUserStrings() or ():
            attrs[str(k)] = str(v)

        def add(i, kind, g):
            objects.append({"id": i, "kind": kind, "layer": lay,
                            "attrs": dict(attrs), "geom": g})
        if isinstance(geo, r3.Point):
            add(oid, "point", {"type": "point", "p": _p(geo.Location, f)})
        elif isinstance(geo, r3.PolyCurve):
            segs = [geo.SegmentCurve(k) for k in range(geo.SegmentCount)]
            if all(s.IsLinear(tol) for s in segs):
                pts = [_p(segs[0].PointAtStart, f)] + [
                    _p(s.PointAtEnd, f) for s in segs]
                add(oid, "curve", {"type": "polyline", "p": pts})
            else:
                for k, s in enumerate(segs):
                    g, w = _curve_geom(r3, s, tol, f)
                    if g is None:
                        warn.append(f"{oid}/{k}: {w}")
                    else:
                        add(f"{oid}/{k}", "curve", g)
        elif isinstance(geo, r3.Curve):
            g, w = _curve_geom(r3, geo, tol, f)
            if g is None:
                warn.append(f"{oid}: {w}")
            else:
                add(oid, "curve", g)
        elif isinstance(geo, r3.Mesh):
            g = _mesh_geom(r3, geo, f)
            if g is None:
                warn.append(f"{oid}: empty mesh -- skipped")
            else:
                add(oid, "mesh", {"type": "mesh", **g})
        elif isinstance(geo, (r3.Brep, r3.Extrusion, r3.Surface)):
            if isinstance(geo, r3.Extrusion):
                brep = geo.ToBrep(False)
            elif isinstance(geo, r3.Surface):
                brep = r3.Brep.CreateFromSurface(geo)
            else:
                brep = geo
            if brep is None:
                warn.append(f"{oid}: surface could not be converted -- "
                            "skipped")
                continue
            for fid, kind, g in _brep_objects(r3, brep, tol, f, oid, warn):
                add(fid, kind, g)
        else:
            nm = type(geo).__name__
            skipped[nm] = skipped.get(nm, 0) + 1
    if skipped:
        warn.append("skipped unsupported objects: " + ", ".join(
            f"{n} x{c}" for n, c in sorted(skipped.items())))
    return {"schema": "skyframe.exchange/1",
            "source": {"app": "rhino3dm", "doc": doc or "",
                       "units": unit, "tolerance": tol * f},
            "session": session,
            "layers": [{"path": v["path"], "color": v["color"]}
                       for _k, v in sorted(layers.items())],
            "objects": objects, "warnings": warn}


def layer_table(payload: dict) -> List[dict]:
    """``[{path, color, count, kinds: {kind: n}}]`` for the mapping UI."""
    rows = {ly["path"]: {"path": ly["path"], "color": ly["color"],
                         "count": 0, "kinds": {}}
            for ly in payload.get("layers") or []}
    for o in payload.get("objects") or []:
        r = rows.setdefault(o.get("layer") or "",
                            {"path": o.get("layer") or "",
                             "color": "#808080", "count": 0, "kinds": {}})
        r["count"] += 1
        r["kinds"][o["kind"]] = r["kinds"].get(o["kind"], 0) + 1
    return list(rows.values())
