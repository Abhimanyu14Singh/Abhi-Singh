"""Built-up frame sections (v1.21): sections assembled from components.

A :class:`BuiltupSection` is a named list of COMPONENTS placed in the
section plane, each one of

* ``{"kind": "plate", "b", "t", "x", "y", "angle"}`` — a b x t rectangle
  (``b`` along the local horizontal when ``angle == 0``) centred at
  ``(x, y)``, rotated ``angle`` DEGREES counter-clockwise;
* ``{"kind": "db", "standard", "name", "x", "y", "rotation", "mirror"}`` —
  a steel-database shape (looked up through :mod:`skyframe.core.builtup_db`
  = ``steel_db`` or its stub; its ``type``/``dims`` and tabulated props
  ``tab`` are SNAPSHOT into the component on save, so a saved section never
  depends on the database again);
* ``{"kind": "param", "type", "dims", "x", "y", "rotation", "mirror"}`` — a
  parametric shape (``type`` in :data:`SHAPE_TYPES`, dims in metres).

``(x, y)`` of a shape is the centre of its UNROTATED bounding box (the
centroid of doubly-symmetric shapes); ``mirror`` flips the shape about its
local vertical axis BEFORE ``rotation`` (degrees, CCW) is applied about
that point.  Every component may carry ``"material"`` (default: the
section material).

Coordinates / axes (the convention of the existing preview): ``x`` is the
horizontal WIDTH direction, ``y`` the vertical DEPTH direction, so local 2
points along the depth (up) and local 3 along the width; ``I33 = int y^2
dA`` (major axis, horizontal) and ``I22 = int x^2 dA``, both about the
centroid.

Properties (all EXACT closed forms unless flagged)
--------------------------------------------------
Components are integrated exactly (shoelace polygons, analytic circles):

    A   = sum n_i A_i,            n_i = E_i / E_ref  (modular ratio,
                                                      E_ref = section material)
    c   = sum n_i A_i c_i / A
    Ixx = sum n_i [Ixx_i + A_i (y_i - cy)^2]           (= I33 geometric)
    Iyy = sum n_i [Iyy_i + A_i (x_i - cx)^2]           (= I22 geometric)
    Ixy = sum n_i [Ixy_i + A_i (x_i - cx)(y_i - cy)]

A component's own moments transform under rotation ``t`` as
``Ixx' = c^2 Ixx + s^2 Iyy + 2 s c Ixy``, ``Iyy' = s^2 Ixx + c^2 Iyy - 2 s c
Ixy``, ``Ixy' = s c (Iyy - Ixx) + (c^2 - s^2) Ixy``; a mirror flips the sign
of ``Ixy``.  Database shapes with TABULATED ``A/I33/I22`` use those values
(about the geometric centroid of the fillet-free outline; angles get
``|Ixy|`` from the tabulated principal values, its sign from the geometry);
``"props_from": "geometry"`` forces the fillet-free outline instead.

Principal axes: ``tan 2theta = -2 Ixy / (Ixx - Iyy)``; ``theta`` (radians,
CCW from the horizontal) is the MAJOR axis, ``I_major/minor = (Ixx+Iyy)/2
+- sqrt(((Ixx-Iyy)/2)^2 + Ixy^2)``.  ``axes: "geometric"`` (default) gives
the frame section ``I33 = Ixx``, ``I22 = Iyy`` (Ixy is NOT carried by the
frame element — use "principal" and rotate the member's local axes by
``theta`` for unsymmetric sections); ``axes: "principal"`` gives
``I33 = I_major``, ``I22 = I_minor``.

S (elastic) = I / extreme-fibre distance (both faces, ``S33 = min``); Z
(plastic) = first moment of the two equal-(transformed)-area halves about
the plastic neutral axis parallel to the chosen axis (exact polygon
clipping; circles enter as 360-gons for Z only); r = sqrt(I/A).

TORSION J (``torsion: "auto"``):

* open thin-walled parts: ``sum b t^3 / 3`` over every rectangular wall
  (tabulated J for database shapes that have one);
* closed tubes: rect tube Bredt-Batho ``4 Am^2 t / p_m``, round tube
  ``pi (D^4 - Di^4) / 32``; solids: Roark rectangle / ``pi D^4/32``;
* a single RECTANGULAR CELL formed by axis-aligned components (box girder,
  two channels toe-to-toe, ...) is detected by rasterising the components
  on their own coordinate grid: Bredt-Batho ``J = 4 Am^2 / sum(s_i/t_i)``
  on the wall mid-line (each wall thickness measured outward from the
  cell), plus the open/closed J of components that do not bound the cell
  (flange outstands of the cell walls are neglected — conservative);
* anything else (several cells, non-rectangular cells) is the documented
  APPROXIMATION ``J = sum n_i J_i`` (conservative; flagged
  ``J_method = "approx"`` with a warning).

``torsion: "open"`` forces the sum; ``torsion: "fem"`` uses the optional
``sectionproperties`` package (FEM warping analysis of the union outline:
exact J, shear centre and shear areas) when it is installed and falls back
to "auto" with a warning otherwise (it is NEVER a hard dependency).

Shear areas (approximate, documented): a rectangular wall of aspect >= 2
contributes ``A sin^2 phi`` to As2 (vertical shear) and ``A cos^2 phi`` to
As3 (``phi`` = wall direction); compact rectangles and solid rectangles
5/6 A, solid rounds 0.9 A, round tubes A/2, rect tubes their wall areas.
Shear centre: the centroid for doubly- or point-symmetric sections; the
classical mono-symmetric thin-walled rule ``y_sc = sum(I_i y_i)/sum(I_i)``
(``I_i`` = each wall's inertia about the vertical symmetry axis — exact
for thin-webbed I / tee sections, approximate otherwise) for sections of
axis-aligned plates symmetric about the vertical axis; otherwise ``None``
(use ``torsion: "fem"``).

Stacked plates (e.g. a cover plate welded to a flange) are summed as
SEPARATE open walls, which underestimates J (conservative); ``"fem"``
treats the union outline exactly.

The mirrored analysis :class:`FrameSection` (``shape = "builtup"``) gets A,
I33, I22, J (transformed) and the bounding box ``b`` (width) / ``h``
(depth); its shear areas are NOT set (the member stays Euler-Bernoulli
unless the user enables shear deformation on the frame section).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from skyframe.core.sections_designer import clip_polygon, polygon_area

__all__ = [
    "BuiltupSection", "SHAPE_TYPES", "COMPONENT_KINDS", "TEMPLATES",
    "validate_builtup_section", "resolve_components", "component_geometry",
    "section_properties", "make_frame_section", "template_section",
    "template_list", "fem_available",
]

COMPONENT_KINDS = ("plate", "db", "param")
SHAPE_TYPES = ("I", "I_mono", "channel", "angle", "double_angle", "tee",
               "rect_tube", "round_tube", "rect_solid", "round_solid",
               "plate")
AXES = ("geometric", "principal")
TORSION = ("auto", "open", "fem")

# required dims per type (aliases accepted on input, see _dims)
_REQ = {
    "I": ("d", "bf", "tf", "tw"),
    "I_mono": ("d", "bf_top", "tf_top", "bf_bot", "tf_bot", "tw"),
    "channel": ("d", "bf", "tf", "tw"),
    "angle": ("d", "b", "t"),
    "double_angle": ("d", "b", "t", "s"),
    "tee": ("d", "bf", "tf", "tw"),
    "rect_tube": ("d", "b", "t"),
    "round_tube": ("D", "t"),
    "rect_solid": ("d", "b"),
    "round_solid": ("D",),
    "plate": ("b", "t"),
}
_ALIAS = {"bft": "bf_top", "tft": "tf_top", "bfb": "bf_bot", "tfb": "tf_bot",
          "OD": "D", "h": "d"}
_TAB_KEYS = ("A", "I33", "I22", "J", "I_major", "I_minor")
_CIRCLE_N = 360          # polygonisation of circles for the plastic modulus
_TOL = 1e-12


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) \
            or not math.isfinite(v):
        raise ValueError(f"{what} must be a finite number (got {v!r})")
    return float(v)


# --------------------------------------------------------------------------- #
# the section object
# --------------------------------------------------------------------------- #
@dataclass
class BuiltupSection:
    """Built-up section (see the module docstring)."""

    name: str
    material: str
    components: List[dict] = field(default_factory=list)
    axes: str = "geometric"
    torsion: str = "auto"
    template: Optional[dict] = None
    notes: str = ""

    def to_dict(self) -> dict:
        d = {"name": self.name, "material": self.material,
             "components": [_comp_to_dict(c) for c in self.components],
             "axes": self.axes, "torsion": self.torsion}
        if self.template is not None:
            d["template"] = _jsonable(self.template)
        if self.notes:
            d["notes"] = self.notes
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "BuiltupSection":
        if not isinstance(d, dict):
            raise ValueError("built-up section must be a JSON object")
        comps = d.get("components") or []
        if not isinstance(comps, list):
            raise ValueError("built-up section: components must be a list")
        tpl = d.get("template")
        return cls(name=str(d.get("name") or ""),
                   material=str(d.get("material") or ""),
                   components=[_comp_from_dict(c) for c in comps],
                   axes=str(d.get("axes", "geometric")),
                   torsion=str(d.get("torsion", "auto")),
                   template=_jsonable(tpl) if isinstance(tpl, dict) else None,
                   notes=str(d.get("notes") or ""))


def _jsonable(v):
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return float(v) if isinstance(v, float) else v
    return str(v)


def _dims(raw) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for k, v in (raw or {}).items():
        k = _ALIAS.get(str(k), str(k))
        if isinstance(v, str):
            out[k] = v
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = float(v)
    return out


def _comp_from_dict(c) -> dict:
    if not isinstance(c, dict):
        raise ValueError("built-up component must be a JSON object")
    kind = str(c.get("kind") or "")
    out: dict = {"kind": kind}
    if kind == "plate":
        for k, dflt in (("b", None), ("t", None), ("x", 0.0), ("y", 0.0),
                        ("angle", 0.0)):
            v = c.get(k, dflt)
            out[k] = v if v is None or isinstance(v, bool) \
                or not isinstance(v, (int, float)) else float(v)
    elif kind in ("db", "param"):
        if kind == "db":
            out["standard"] = str(c.get("standard") or "")
            out["name"] = str(c.get("name") or "")
        if c.get("type") is not None:
            out["type"] = str(c.get("type"))
        if c.get("dims") is not None:
            if not isinstance(c["dims"], dict):
                raise ValueError("built-up component dims must be an object")
            out["dims"] = _dims(c["dims"])
        for k in ("x", "y", "rotation"):
            v = c.get(k, 0.0)
            out[k] = float(v) if isinstance(v, (int, float)) \
                and not isinstance(v, bool) else v
        out["mirror"] = bool(c.get("mirror", False))
        if kind == "db" and isinstance(c.get("tab"), dict):
            out["tab"] = {k: float(v) for k, v in c["tab"].items()
                          if k in _TAB_KEYS and isinstance(v, (int, float))
                          and not isinstance(v, bool) and math.isfinite(v)}
        if c.get("props_from") == "geometry":
            out["props_from"] = "geometry"
    else:
        raise ValueError(f"built-up component kind must be one of "
                         f"{COMPONENT_KINDS}, got {kind!r}")
    if c.get("material"):
        out["material"] = str(c["material"])
    if c.get("label"):
        out["label"] = str(c["label"])
    return out


def _comp_to_dict(c: dict) -> dict:
    kind = c["kind"]
    if kind == "plate":
        d = {"kind": "plate", "b": c["b"], "t": c["t"], "x": c["x"],
             "y": c["y"], "angle": c["angle"]}
    else:
        d = {"kind": kind}
        if kind == "db":
            d["standard"], d["name"] = c["standard"], c["name"]
        if "type" in c:
            d["type"] = c["type"]
        if "dims" in c:
            d["dims"] = dict(c["dims"])
        d.update({"x": c["x"], "y": c["y"], "rotation": c["rotation"],
                  "mirror": bool(c["mirror"])})
        if "tab" in c:
            d["tab"] = dict(c["tab"])
        if c.get("props_from") == "geometry":
            d["props_from"] = "geometry"
    if c.get("material"):
        d["material"] = c["material"]
    if c.get("label"):
        d["label"] = c["label"]
    return d


# --------------------------------------------------------------------------- #
# database resolution (snapshot type / dims / tabulated props)
# --------------------------------------------------------------------------- #
def _std_id(s: str) -> str:
    return str(s).strip().lower().replace("/", "_").replace(" ", "_")


def resolve_components(bs: BuiltupSection) -> BuiltupSection:
    """Fill ``type``/``dims``/``tab`` of every db component from the DB.

    Components that already carry a snapshot are left untouched (a saved
    section never changes when the database does).  Raises ``ValueError``
    for an unknown standard/shape.
    """
    from skyframe.core import builtup_db
    for c in bs.components:
        if c["kind"] != "db" or ("type" in c and "dims" in c):
            continue
        try:
            rec = builtup_db.shape(_std_id(c["standard"]), c["name"])
        except Exception as exc:
            raise ValueError(f"Built-up section {bs.name}: database shape "
                             f"{c['standard']}/{c['name']}: {exc}") from None
        c["type"] = str(rec.get("type"))
        c["dims"] = _dims(rec.get("dims"))
        props = rec.get("props") or {}
        computed = set(rec.get("computed") or [])
        tab = {k: float(props[k]) for k in _TAB_KEYS
               if isinstance(props.get(k), (int, float))
               and math.isfinite(props[k]) and k not in computed}
        if tab:
            c["tab"] = tab
    return bs


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _check_dims(tag: str, typ: str, dims: dict) -> None:
    if typ not in _REQ:
        raise ValueError(f"{tag}: shape type must be one of {SHAPE_TYPES}, "
                         f"got {typ!r}")
    for k in _REQ[typ]:
        v = dims.get(k)
        if not (isinstance(v, float) and math.isfinite(v)):
            raise ValueError(f"{tag}: dims.{k} is required (finite number)")
        if v < 0.0 or (v == 0.0 and k != "s"):
            raise ValueError(f"{tag}: dims.{k} must be > 0")
    g = dims.get
    bad = None
    if typ in ("I", "tee", "channel"):
        nf = 1 if typ == "tee" else 2
        if nf * g("tf") >= g("d"):
            bad = "flange thickness exceeds the depth"
        elif g("tw") >= g("bf"):
            bad = "web thickness exceeds the flange width"
    elif typ == "I_mono":
        if g("tf_top") + g("tf_bot") >= g("d"):
            bad = "flange thicknesses exceed the depth"
        elif g("tw") >= min(g("bf_top"), g("bf_bot")):
            bad = "web thickness exceeds a flange width"
    elif typ in ("angle", "double_angle"):
        if g("t") >= min(g("d"), g("b")):
            bad = "leg thickness exceeds a leg length"
        o = dims.get("orientation", "LLBB")
        if typ == "double_angle" and o not in ("LLBB", "SLBB"):
            bad = "orientation must be LLBB or SLBB"
    elif typ == "rect_tube":
        if 2 * g("t") >= min(g("d"), g("b")):
            bad = "wall thickness too large for the tube"
    elif typ == "round_tube":
        if 2 * g("t") >= g("D"):
            bad = "wall thickness too large for the tube"
    if bad:
        raise ValueError(f"{tag}: {bad}")


def validate_builtup_section(bs: BuiltupSection,
                             materials: Dict[str, object]) -> None:
    """Full validation against the model's material table (raises)."""
    if not bs.name:
        raise ValueError("built-up section: name is required")
    tag0 = f"Built-up section {bs.name}"
    if bs.material not in materials:
        raise ValueError(f"{tag0}: unknown material {bs.material!r}")
    if not (float(getattr(materials[bs.material], "E", 0.0)) > 0.0):
        raise ValueError(f"{tag0}: material {bs.material} needs E > 0")
    if bs.axes not in AXES:
        raise ValueError(f"{tag0}: axes must be one of {AXES}")
    if bs.torsion not in TORSION:
        raise ValueError(f"{tag0}: torsion must be one of {TORSION}")
    if not bs.components:
        raise ValueError(f"{tag0}: needs at least one component")
    for i, c in enumerate(bs.components):
        tag = f"{tag0}, component {i + 1}"
        m = c.get("material")
        if m and m not in materials:
            raise ValueError(f"{tag}: unknown material {m!r}")
        if m and not (float(getattr(materials[m], "E", 0.0)) > 0.0):
            raise ValueError(f"{tag}: material {m} needs E > 0")
        if c["kind"] == "plate":
            for k in ("b", "t", "x", "y", "angle"):
                _num(c.get(k), f"{tag}: {k}")
            if c["b"] <= 0 or c["t"] <= 0:
                raise ValueError(f"{tag}: plate b and t must be > 0")
            continue
        for k in ("x", "y", "rotation"):
            _num(c.get(k), f"{tag}: {k}")
        if c["kind"] == "db" and not (c.get("standard") and c.get("name")):
            raise ValueError(f"{tag}: database shape needs standard and name")
        if "type" not in c or "dims" not in c:
            raise ValueError(f"{tag}: shape type and dims are required "
                             "(database shape not resolved)")
        _check_dims(tag, c["type"], c["dims"])


# --------------------------------------------------------------------------- #
# geometry: local pieces of each shape type
# --------------------------------------------------------------------------- #
def _rect(x0, x1, y0, y1, sign=1.0):
    return {"t": "poly", "pts": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
            "sign": sign, "wall": True}


def _circle(cx, cy, r, sign=1.0):
    return {"t": "circle", "c": (cx, cy), "r": r, "sign": sign}


def _local_pieces(typ: str, d: dict) -> Tuple[List[dict], str]:
    """Pieces of one shape in its LOCAL frame (bbox centred at the origin)
    and its torsion class ("open" | "closed" | "solid")."""
    g = d.get
    if typ == "plate":
        b, t = g("b"), g("t")
        return [_rect(-b / 2, b / 2, -t / 2, t / 2)], "open"
    if typ == "rect_solid":
        b, h = g("b"), g("d")
        p = _rect(-b / 2, b / 2, -h / 2, h / 2)
        p["wall"] = False
        return [p], "solid"
    if typ == "round_solid":
        return [_circle(0.0, 0.0, g("D") / 2)], "solid"
    if typ == "round_tube":
        R = g("D") / 2
        return [_circle(0.0, 0.0, R), _circle(0.0, 0.0, R - g("t"), -1.0)], \
            "closed"
    if typ == "rect_tube":
        b, h, t = g("b"), g("d"), g("t")
        o = _rect(-b / 2, b / 2, -h / 2, h / 2)
        i = _rect(-b / 2 + t, b / 2 - t, -h / 2 + t, h / 2 - t, -1.0)
        o["wall"] = i["wall"] = False
        return [o, i], "closed"
    if typ == "I":
        h, bf, tf, tw = g("d"), g("bf"), g("tf"), g("tw")
        return [_rect(-bf / 2, bf / 2, h / 2 - tf, h / 2),
                _rect(-bf / 2, bf / 2, -h / 2, -h / 2 + tf),
                _rect(-tw / 2, tw / 2, -h / 2 + tf, h / 2 - tf)], "open"
    if typ == "I_mono":
        h, tw = g("d"), g("tw")
        bt, tt, bb, tb = g("bf_top"), g("tf_top"), g("bf_bot"), g("tf_bot")
        return [_rect(-bt / 2, bt / 2, h / 2 - tt, h / 2),
                _rect(-bb / 2, bb / 2, -h / 2, -h / 2 + tb),
                _rect(-tw / 2, tw / 2, -h / 2 + tb, h / 2 - tt)], "open"
    if typ == "tee":
        h, bf, tf, tw = g("d"), g("bf"), g("tf"), g("tw")
        return [_rect(-bf / 2, bf / 2, h / 2 - tf, h / 2),
                _rect(-tw / 2, tw / 2, -h / 2, h / 2 - tf)], "open"
    if typ == "channel":
        h, bf, tf, tw = g("d"), g("bf"), g("tf"), g("tw")
        x0 = -bf / 2
        return [_rect(x0, x0 + tw, -h / 2, h / 2),
                _rect(x0 + tw, bf / 2, h / 2 - tf, h / 2),
                _rect(x0 + tw, bf / 2, -h / 2, -h / 2 + tf)], "open"
    if typ == "angle":
        h, b, t = g("d"), g("b"), g("t")
        return [_rect(-b / 2, -b / 2 + t, -h / 2, h / 2),
                _rect(-b / 2 + t, b / 2, -h / 2, -h / 2 + t)], "open"
    if typ == "double_angle":
        a, bb, t, s = g("d"), g("b"), g("t"), g("s")
        o = d.get("orientation", "LLBB")
        vert, hor = (max(a, bb), min(a, bb)) if o == "LLBB" \
            else (min(a, bb), max(a, bb))
        W = 2 * hor + s
        pcs = []
        for sg in (1.0, -1.0):                 # right angle, left mirror
            xh = s / 2                         # heel x (back of the leg)
            pcs.append(_rect(*sorted((sg * xh, sg * (xh + t))),
                             -vert / 2, vert / 2))
            pcs.append(_rect(*sorted((sg * (xh + t), sg * (xh + hor))),
                             -vert / 2, -vert / 2 + t))
        del W
        return pcs, "open"
    raise ValueError(f"unknown shape type {typ!r}")


def _xf_point(p, mirror: bool, cs: float, sn: float, X: float, Y: float):
    x, y = p
    if mirror:
        x = -x
    return (x * cs - y * sn + X, x * sn + y * cs + Y)


def _poly_integrals(pts):
    """(A, Sx, Sy, Ixx_o, Iyy_o, Ixy_o) signed, about the ORIGIN.

    ``Sx = int x dA``, ``Sy = int y dA``, ``Ixx_o = int y^2 dA``,
    ``Iyy_o = int x^2 dA``, ``Ixy_o = int x y dA`` (shoelace closed forms).
    """
    A = Sx = Sy = Ixx = Iyy = Ixy = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        cr = x1 * y2 - x2 * y1
        A += cr
        Sx += (x1 + x2) * cr
        Sy += (y1 + y2) * cr
        Ixx += (y1 * y1 + y1 * y2 + y2 * y2) * cr
        Iyy += (x1 * x1 + x1 * x2 + x2 * x2) * cr
        Ixy += (x1 * y2 + 2 * x1 * y1 + 2 * x2 * y2 + x2 * y1) * cr
    return A / 2, Sx / 6, Sy / 6, Ixx / 12, Iyy / 12, Ixy / 24


def _piece_integrals(p):
    if p["t"] == "circle":
        (cx, cy), r = p["c"], p["r"]
        A = math.pi * r * r
        I0 = math.pi * r ** 4 / 4
        return (A, A * cx, A * cy, I0 + A * cy * cy, I0 + A * cx * cx,
                A * cx * cy)
    pts = p["pts"]
    out = _poly_integrals(pts)
    if out[0] < 0:
        out = tuple(-v for v in out)
    return out


def _central(pieces) -> Tuple[float, float, float, float, float, float]:
    """(A, cx, cy, Ixx, Iyy, Ixy) of signed pieces about their centroid."""
    A = Sx = Sy = Ixx = Iyy = Ixy = 0.0
    for p in pieces:
        a, sx, sy, ixx, iyy, ixy = _piece_integrals(p)
        s = p["sign"]
        A += s * a
        Sx += s * sx
        Sy += s * sy
        Ixx += s * ixx
        Iyy += s * iyy
        Ixy += s * ixy
    cx, cy = Sx / A, Sy / A
    return (A, cx, cy, Ixx - A * cy * cy, Iyy - A * cx * cx,
            Ixy - A * cx * cy)


def _rot_moments(Ixx, Iyy, Ixy, th):
    c, s = math.cos(th), math.sin(th)
    return (c * c * Ixx + s * s * Iyy + 2 * s * c * Ixy,
            s * s * Ixx + c * c * Iyy - 2 * s * c * Ixy,
            s * c * (Iyy - Ixx) + (c * c - s * s) * Ixy)


def _wall_dims(p) -> Optional[Tuple[float, float, float]]:
    """(length, thickness, sin^2 of the wall direction) of a rectangular
    wall piece (sin^2 = 1 for a vertical wall, 0 for a horizontal one)."""
    if p["t"] != "poly" or len(p["pts"]) != 4:
        return None
    (x0, y0), (x1, y1), (x2, y2) = p["pts"][0], p["pts"][1], p["pts"][2]
    e1 = math.hypot(x1 - x0, y1 - y0)
    e2 = math.hypot(x2 - x1, y2 - y1)
    if e1 >= e2:
        return e1, e2, ((y1 - y0) / e1) ** 2
    return e2, e1, ((y2 - y1) / e2) ** 2


def _roark_rect_J(b, h):
    a, c = max(b, h) / 2.0, min(b, h) / 2.0
    return a * c ** 3 * (16.0 / 3.0 - 3.36 * (c / a)
                         * (1.0 - c ** 4 / (12.0 * a ** 4)))


def component_geometry(c: dict) -> dict:
    """Resolve one component into section-frame geometry and own props.

    Returns ``{pieces, A, cx, cy, Ixx, Iyy, Ixy, J, tclass, As_v, As_h,
    bbox, rot, closed_rect}`` (raw, NOT modular-ratio weighted; moments
    about the component's own centroid in section axes).
    """
    if c["kind"] == "plate":
        typ, dims = "plate", {"b": c["b"], "t": c["t"]}
        rot_deg, mirror = c["angle"], False
    else:
        typ, dims = c["type"], c["dims"]
        rot_deg, mirror = c["rotation"], bool(c["mirror"])
    th = math.radians(rot_deg)
    cs, sn = math.cos(th), math.sin(th)
    # snap tiny trig noise so 90-degree steps stay exactly axis-aligned
    cs = 0.0 if abs(cs) < 1e-15 else cs
    sn = 0.0 if abs(sn) < 1e-15 else sn
    X, Y = float(c["x"]), float(c["y"])
    loc, tclass = _local_pieces(typ, dims)

    # own properties in the LOCAL (unmirrored, unrotated) frame
    A, lcx, lcy, Ixx, Iyy, Ixy = _central(loc)
    tab = c.get("tab") or {}
    use_tab = (c["kind"] == "db" and c.get("props_from") != "geometry"
               and all(k in tab for k in ("A", "I33", "I22")))
    if use_tab:
        A, Ixx, Iyy = tab["A"], tab["I33"], tab["I22"]
        if "I_major" in tab and "I_minor" in tab:
            q = ((tab["I_major"] - tab["I_minor"]) / 2) ** 2 \
                - ((Ixx - Iyy) / 2) ** 2
            Ixy = math.copysign(math.sqrt(max(q, 0.0)), Ixy) \
                if abs(Ixy) > 0 else 0.0
        else:
            Ixy = 0.0 if typ not in ("angle",) else Ixy
    # own torsion
    walls = [w for w in (_wall_dims(p) for p in loc
                         if p["t"] == "poly" and p.get("wall")) if w]
    if tclass == "open":
        J = sum(L * t ** 3 / 3.0 for L, t, _phi in walls)
    elif typ == "rect_tube":
        t = dims["t"]
        am = (dims["b"] - t) * (dims["d"] - t)
        J = 4 * am * am * t / (2 * ((dims["b"] - t) + (dims["d"] - t)))
    elif typ == "round_tube":
        D, t = dims["D"], dims["t"]
        J = math.pi * (D ** 4 - (D - 2 * t) ** 4) / 32.0
    elif typ == "rect_solid":
        J = _roark_rect_J(dims["b"], dims["d"])
    else:                                       # round_solid
        J = math.pi * dims["D"] ** 4 / 32.0
    if use_tab and "J" in tab:
        J = tab["J"]

    # mirror then rotate the own moments; place the centroid
    if mirror:
        lcx, Ixy = -lcx, -Ixy
    Ixx, Iyy, Ixy = _rot_moments(Ixx, Iyy, Ixy, th)
    gcx, gcy = (lcx * cs - lcy * sn + X, lcx * sn + lcy * cs + Y)

    pieces = []
    for p in loc:
        if p["t"] == "circle":
            q = dict(p)
            q["c"] = _xf_point(p["c"], mirror, cs, sn, X, Y)
        else:
            q = dict(p)
            pts = [_xf_point(v, mirror, cs, sn, X, Y) for v in p["pts"]]
            if mirror:
                pts = list(reversed(pts))
            q["pts"] = pts
        pieces.append(q)

    # shear areas (raw), section axes
    if typ == "rect_solid":
        As_v = As_h = 5.0 / 6.0 * A
    elif typ == "round_solid":
        As_v = As_h = 0.9 * A
    elif typ == "round_tube":
        As_v = As_h = 0.5 * A
    elif typ == "rect_tube":
        t = dims["t"]
        v_l, h_l = 2 * dims["d"] * t, 2 * dims["b"] * t
        As_v = v_l * cs * cs + h_l * sn * sn
        As_h = v_l * sn * sn + h_l * cs * cs
    else:
        As_v = As_h = 0.0
        geomA = sum(L * t for L, t, _ in walls) or 1.0
        scale = A / geomA                     # tabulated A -> walls
        for p in pieces:
            w = _wall_dims(p) if p.get("wall") else None
            if not w:
                continue
            L, t, s2 = w
            a = L * t * scale
            if L >= 2 * t:
                As_v += a * s2
                As_h += a * (1.0 - s2)
            else:
                As_v += 5.0 / 6.0 * a
                As_h += 5.0 / 6.0 * a
    xs, ys = [], []
    for p in pieces:
        if p["sign"] < 0:
            continue
        if p["t"] == "circle":
            xs += [p["c"][0] - p["r"], p["c"][0] + p["r"]]
            ys += [p["c"][1] - p["r"], p["c"][1] + p["r"]]
        else:
            xs += [v[0] for v in p["pts"]]
            ys += [v[1] for v in p["pts"]]
    closed_rect = None
    if typ == "rect_tube" and _axis_aligned(pieces[0]):
        closed_rect = _bounds(pieces[0])
    return {"pieces": pieces, "A": A, "cx": gcx, "cy": gcy, "Ixx": Ixx,
            "Iyy": Iyy, "Ixy": Ixy, "J": J, "tclass": tclass, "type": typ,
            "As_v": As_v, "As_h": As_h,
            "bbox": (min(xs), max(xs), min(ys), max(ys)),
            "closed_rect": closed_rect}


def _axis_aligned(p) -> bool:
    if p["t"] != "poly" or len(p["pts"]) != 4:
        return False
    pts = p["pts"]
    for i in range(4):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 4]
        if abs(x1 - x2) > 1e-12 and abs(y1 - y2) > 1e-12:
            return False
    return True


def _bounds(p):
    xs = [v[0] for v in p["pts"]]
    ys = [v[1] for v in p["pts"]]
    return (min(xs), max(xs), min(ys), max(ys))


# --------------------------------------------------------------------------- #
# closed-cell detection (rasterisation on the components' own grid)
# --------------------------------------------------------------------------- #
def _cells(geoms) -> Tuple[List[dict], List[Tuple[int, int, int, int]]]:
    """Enclosed voids of the axis-aligned rectangles.

    Returns ``(cells, rects)`` with each cell ``{x0, x1, y0, y1, rect,
    t_top, t_bot, t_left, t_right, walls: set(component idx)}`` (``rect``
    False for a non-rectangular void).
    """
    rects = []
    for ci, g in enumerate(geoms):
        if g["closed_rect"] is not None:
            rects.append((*g["closed_rect"], ci))
            continue
        for p in g["pieces"]:
            if p["sign"] > 0 and p["t"] == "poly" and _axis_aligned(p):
                rects.append((*_bounds(p), ci))
    if len(rects) < 2:
        return [], rects

    def uniq(vals):
        out = []
        for v in sorted(vals):
            if not out or v - out[-1] > 1e-9:
                out.append(v)
        return out
    xs = uniq([r[0] for r in rects] + [r[1] for r in rects])
    ys = uniq([r[2] for r in rects] + [r[3] for r in rects])
    nx, ny = len(xs) - 1, len(ys) - 1
    fill = [[False] * ny for _ in range(nx)]
    for i in range(nx):
        xm = 0.5 * (xs[i] + xs[i + 1])
        for j in range(ny):
            ym = 0.5 * (ys[j] + ys[j + 1])
            fill[i][j] = any(r[0] < xm < r[1] and r[2] < ym < r[3]
                             for r in rects)
    seen = [[False] * ny for _ in range(nx)]
    stack = [(i, j) for i in range(nx) for j in range(ny)
             if (i in (0, nx - 1) or j in (0, ny - 1)) and not fill[i][j]]
    for i, j in stack:
        seen[i][j] = True
    while stack:                                   # outside flood fill
        i, j = stack.pop()
        for a, b in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
            if 0 <= a < nx and 0 <= b < ny and not fill[a][b] \
                    and not seen[a][b]:
                seen[a][b] = True
                stack.append((a, b))
    cells = []
    for i0 in range(nx):
        for j0 in range(ny):
            if fill[i0][j0] or seen[i0][j0]:
                continue
            reg = [(i0, j0)]
            seen[i0][j0] = True
            k = 0
            while k < len(reg):
                i, j = reg[k]
                k += 1
                for a, b in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                    if 0 <= a < nx and 0 <= b < ny and not fill[a][b] \
                            and not seen[a][b]:
                        seen[a][b] = True
                        reg.append((a, b))
            ia = min(r[0] for r in reg)
            ib = max(r[0] for r in reg)
            ja = min(r[1] for r in reg)
            jb = max(r[1] for r in reg)
            cell = {"x0": xs[ia], "x1": xs[ib + 1], "y0": ys[ja],
                    "y1": ys[jb + 1],
                    "rect": len(reg) == (ib - ia + 1) * (jb - ja + 1)}
            if cell["rect"]:
                im, jm = (ia + ib) // 2, (ja + jb) // 2

                def run(i, j, di, dj):
                    tot = 0.0
                    while 0 <= i < nx and 0 <= j < ny and fill[i][j]:
                        tot += (xs[i + 1] - xs[i]) if di else \
                            (ys[j + 1] - ys[j])
                        i += di
                        j += dj
                    return tot
                cell["t_top"] = run(im, jb + 1, 0, 1)
                cell["t_bot"] = run(im, ja - 1, 0, -1)
                cell["t_right"] = run(ib + 1, jm, 1, 0)
                cell["t_left"] = run(ia - 1, jm, -1, 0)
                walls = set()
                e = 1e-9
                for r in rects:
                    x0, x1, y0, y1, ci = r
                    touch_h = (x0 < cell["x1"] - e and x1 > cell["x0"] + e
                               and (abs(y0 - cell["y1"]) < e
                                    or abs(y1 - cell["y0"]) < e))
                    touch_v = (y0 < cell["y1"] - e and y1 > cell["y0"] + e
                               and (abs(x0 - cell["x1"]) < e
                                    or abs(x1 - cell["x0"]) < e))
                    if touch_h or touch_v:
                        walls.add(ci)
                cell["walls"] = walls
            cells.append(cell)
    return cells, rects


def _overlap_area(rects) -> float:
    """Total pairwise overlap area of axis-aligned rectangles."""
    tot = 0.0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            w = min(a[1], b[1]) - max(a[0], b[0])
            h = min(a[3], b[3]) - max(a[2], b[2])
            if w > 1e-9 and h > 1e-9:
                tot += w * h
    return tot


# --------------------------------------------------------------------------- #
# plastic modulus, symmetry
# --------------------------------------------------------------------------- #
def _polys_for_plastic(geoms, ns, cx, cy, th):
    """Weighted CCW polygons in the (u, v) frame rotated by ``th`` about
    the centroid (u along the chosen 3 axis, v along 2)."""
    c, s = math.cos(th), math.sin(th)
    out = []
    for g, n in zip(geoms, ns):
        for p in g["pieces"]:
            if p["t"] == "circle":
                (px, py), r = p["c"], p["r"]
                pts = [(px + r * math.cos(2 * math.pi * k / _CIRCLE_N),
                        py + r * math.sin(2 * math.pi * k / _CIRCLE_N))
                       for k in range(_CIRCLE_N)]
            else:
                pts = p["pts"]
            uv = [((x - cx) * c + (y - cy) * s, -(x - cx) * s + (y - cy) * c)
                  for x, y in pts]
            if polygon_area(uv) < 0:
                uv.reverse()
            out.append((p["sign"] * n, [list(q) for q in uv]))
    return out


def _plastic_Z(polys) -> float:
    """Plastic modulus about the horizontal (u) axis of weighted polys."""
    def above(v0):
        A = S = 0.0
        for w, pts in polys:
            part = clip_polygon(pts, v0, "above")
            if part:
                a, _sx, sy, _a, _b, _c = _poly_integrals(part)
                A += w * a
                S += w * sy
        return A, S
    tot_A = tot_S = 0.0
    for w, pts in polys:
        a, _sx, sy, _a, _b, _c = _poly_integrals(pts)
        tot_A += w * a
        tot_S += w * sy
    vs = [q[1] for _w, pts in polys for q in pts]
    lo, hi = min(vs), max(vs)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if above(mid)[0] > 0.5 * tot_A:
            lo = mid
        else:
            hi = mid
    v0 = 0.5 * (lo + hi)
    Aa, Sa = above(v0)
    Ab, Sb = tot_A - Aa, tot_S - Sa
    return (Sa - v0 * Aa) - (Sb - v0 * Ab)


def _signature(geoms, ns, fx):
    """Canonical multiset of transformed pieces (for symmetry tests)."""
    sig = []
    for g, n in zip(geoms, ns):
        for p in g["pieces"]:
            if p["t"] == "circle":
                x, y = fx(*p["c"])
                sig.append(("c", round(x, 7), round(y, 7), round(p["r"], 7),
                            p["sign"], round(n, 9)))
            else:
                pts = sorted((round(a, 7) + 0.0, round(b, 7) + 0.0)
                             for a, b in (fx(*v) for v in p["pts"]))
                sig.append(("p", tuple(pts), p["sign"], round(n, 9)))
    return sorted(sig, key=repr)


def _shear_centre(geoms, ns, cx, cy):
    ident = _signature(geoms, ns, lambda x, y: (x, y))
    sym_v = _signature(geoms, ns, lambda x, y: (2 * cx - x, y)) == ident
    sym_h = _signature(geoms, ns, lambda x, y: (x, 2 * cy - y)) == ident
    sym_p = _signature(geoms, ns,
                       lambda x, y: (2 * cx - x, 2 * cy - y)) == ident
    if (sym_v and sym_h) or sym_p:
        return {"x": cx, "y": cy, "method": "symmetry"}
    if sym_v:
        # thin-walled mono-symmetric rule: every wall's lateral inertia
        # about the symmetry axis weights its height (exact for I / tee
        # sections with thin webs; approximate otherwise)
        num = den = 0.0
        for g, n in zip(geoms, ns):
            for p in g["pieces"]:
                if p["t"] != "poly" or not _axis_aligned(p) \
                        or p["sign"] < 0:
                    return None
                x0, x1, y0, y1 = _bounds(p)
                w, h = x1 - x0, y1 - y0
                xm = 0.5 * (x0 + x1)
                If = n * (h * w ** 3 / 12.0 + w * h * (xm - cx) ** 2)
                num += If * 0.5 * (y0 + y1)
                den += If
        if den > 0:
            return {"x": cx, "y": num / den, "method": "monosymmetric"}
    return None


# --------------------------------------------------------------------------- #
# optional FEM (sectionproperties)
# --------------------------------------------------------------------------- #
def fem_available() -> bool:
    try:
        import sectionproperties  # noqa: F401
        import shapely  # noqa: F401
        return True
    except Exception:
        return False


def _fem(geoms, mesh_frac: float = 0.002) -> dict:
    """J, shear centre and shear areas of the UNION outline (single
    material) by the sectionproperties FEM warping analysis."""
    from shapely import Polygon, Point, union_all
    from sectionproperties.analysis.section import Section
    from sectionproperties.pre.geometry import Geometry
    pos, neg = [], []
    for g in geoms:
        for p in g["pieces"]:
            shp = (Point(*p["c"]).buffer(p["r"], quad_segs=32)
                   if p["t"] == "circle" else Polygon(p["pts"]))
            (pos if p["sign"] > 0 else neg).append(shp)
    # snap to a 1e-9 m grid so touching (welded) parts fuse into one outline
    poly = union_all(pos, grid_size=1e-9)
    if neg:
        poly = poly.difference(union_all(neg, grid_size=1e-9),
                               grid_size=1e-9)
    parts = [poly] if poly.geom_type == "Polygon" else list(poly.geoms)
    out = {"J": 0.0, "As_h": 0.0, "As_v": 0.0, "A": 0.0, "sc": None,
           "parts": len(parts)}
    for part in parts:
        geom = Geometry(part)
        geom.create_mesh(mesh_sizes=[mesh_frac * poly.area])
        sec = Section(geom)
        sec.calculate_geometric_properties()
        sec.calculate_warping_properties()
        asx, asy = sec.get_as()
        out["J"] += float(sec.get_j())      # disconnected parts: J adds up
        out["As_h"] += float(asx)
        out["As_v"] += float(asy)
        out["A"] += float(sec.get_area())
        if len(parts) == 1:
            x_s, y_s = sec.get_sc()
            out["sc"] = (float(x_s), float(y_s))
    return out


# --------------------------------------------------------------------------- #
# composite properties
# --------------------------------------------------------------------------- #
def section_properties(bs: BuiltupSection,
                       materials: Dict[str, object]) -> dict:
    """Exact composite properties (see the module docstring).

    Returns a dict with ``A, cx, cy, Ixx, Iyy, Ixy, I_major, I_minor,
    theta, axes, I33, I22, J, J_method, S33, S33_top, S33_bot, S22,
    S22_left, S22_right, Z33, Z22, r33, r22, As2, As3, shear_centre,
    x0, y0, b, h, bbox, E_ref, n, cells, warnings``.
    """
    if any(c["kind"] == "db" and ("type" not in c or "dims" not in c)
           for c in bs.components):
        resolve_components(bs)
    validate_builtup_section(bs, materials)
    E_ref = float(materials[bs.material].E)
    ns = [float(materials[c.get("material") or bs.material].E) / E_ref
          for c in bs.components]
    geoms = [component_geometry(c) for c in bs.components]
    warnings: List[str] = []

    A = sum(n * g["A"] for g, n in zip(geoms, ns))
    if not A > 1e-15:
        raise ValueError(f"Built-up section {bs.name}: area must be > 0")
    cx = sum(n * g["A"] * g["cx"] for g, n in zip(geoms, ns)) / A
    cy = sum(n * g["A"] * g["cy"] for g, n in zip(geoms, ns)) / A
    Ixx = sum(n * (g["Ixx"] + g["A"] * (g["cy"] - cy) ** 2)
              for g, n in zip(geoms, ns))
    Iyy = sum(n * (g["Iyy"] + g["A"] * (g["cx"] - cx) ** 2)
              for g, n in zip(geoms, ns))
    Ixy = sum(n * (g["Ixy"] + g["A"] * (g["cx"] - cx) * (g["cy"] - cy))
              for g, n in zip(geoms, ns))
    if abs(Ixy) < 1e-12 * max(Ixx, Iyy):
        Ixy = 0.0
    avg, R = 0.5 * (Ixx + Iyy), math.hypot(0.5 * (Ixx - Iyy), Ixy)
    theta = 0.5 * math.atan2(-2.0 * Ixy, Ixx - Iyy) if (Ixy or Ixx < Iyy) \
        else 0.0
    if theta <= -0.5 * math.pi + 1e-12:          # keep theta in (-90, 90]
        theta += math.pi
    I_major, I_minor = avg + R, avg - R
    th_axes = theta if bs.axes == "principal" else 0.0
    if bs.axes == "principal":
        I33, I22 = I_major, I_minor
    else:
        I33, I22 = Ixx, Iyy

    # torsion
    cells, rects = _cells(geoms)
    own = [n * g["J"] for g, n in zip(geoms, ns)]
    J_method = "open" if all(g["tclass"] == "open" for g in geoms) \
        else "closed"
    J = sum(own)
    if bs.torsion != "open" and cells:
        if len(cells) == 1 and cells[0]["rect"]:
            c = cells[0]
            Wm = (c["x1"] - c["x0"]) + 0.5 * (c["t_left"] + c["t_right"])
            Hm = (c["y1"] - c["y0"]) + 0.5 * (c["t_top"] + c["t_bot"])
            ds_t = (Wm / c["t_top"] + Wm / c["t_bot"]
                    + Hm / c["t_left"] + Hm / c["t_right"])
            wall_ns = [ns[i] for i in c["walls"]] or [1.0]
            wA = [geoms[i]["A"] for i in c["walls"]] or [1.0]
            n_cell = sum(a * n for a, n in zip(wA, wall_ns)) / sum(wA)
            J_cell = 4.0 * (Wm * Hm) ** 2 / ds_t
            J = n_cell * J_cell + sum(own[i] for i in range(len(geoms))
                                      if i not in c["walls"])
            J_method = "closed_cell"
            c["Am"], c["J"] = Wm * Hm, J_cell
        else:
            J_method = "approx"
            warnings.append("Several or non-rectangular closed cells: J is "
                            "the conservative sum of the component J values "
                            "(use torsion 'fem' for the exact value).")
    if _overlap_area([r[:4] for r in rects]) > 1e-9 * A:
        warnings.append("Some components overlap: overlapping area is "
                        "counted twice.")

    # shear areas
    As_v = sum(n * g["As_v"] for g, n in zip(geoms, ns))
    As_h = sum(n * g["As_h"] for g, n in zip(geoms, ns))
    sc = _shear_centre(geoms, ns, cx, cy)

    if bs.torsion == "fem":
        if not fem_available():
            warnings.append("torsion 'fem' needs the optional "
                            "'sectionproperties' package (pip install "
                            "sectionproperties); used 'auto' instead.")
        elif len(set(ns)) > 1:
            warnings.append("torsion 'fem' supports one material only; "
                            "used 'auto' instead.")
        else:
            try:
                f = _fem(geoms)
                J, J_method = f["J"], "fem"
                As_v, As_h = f["As_v"], f["As_h"]
                if f["sc"] is not None:
                    sc = {"x": f["sc"][0], "y": f["sc"][1], "method": "fem"}
                else:
                    warnings.append(f"fem: {f['parts']} disconnected parts "
                                    "(J and shear areas summed; the parts "
                                    "act together only through lacing/"
                                    "battens not modelled here).")
            except Exception as exc:          # pragma: no cover - mesh errs
                warnings.append(f"fem analysis failed ({exc}); used 'auto'.")

    if bs.axes == "principal":
        cs2, sn2 = math.cos(th_axes) ** 2, math.sin(th_axes) ** 2
        As2, As3 = As_v * cs2 + As_h * sn2, As_v * sn2 + As_h * cs2
    else:
        As2, As3 = As_v, As_h

    # extreme fibres + plastic moduli in the chosen axes frame
    polys = _polys_for_plastic(geoms, ns, cx, cy, th_axes)
    us = [q[0] for w, pts in polys if w > 0 for q in pts]
    vs = [q[1] for w, pts in polys if w > 0 for q in pts]
    # circles: exact extreme fibres (not the 360-gon)
    cth, sth = math.cos(th_axes), math.sin(th_axes)
    for g in geoms:
        for p in g["pieces"]:
            if p["t"] == "circle" and p["sign"] > 0:
                x, y = p["c"][0] - cx, p["c"][1] - cy
                u, v = x * cth + y * sth, -x * sth + y * cth
                us += [u - p["r"], u + p["r"]]
                vs += [v - p["r"], v + p["r"]]
    v_top, v_bot = max(vs), min(vs)
    u_r, u_l = max(us), min(us)
    S33_top, S33_bot = I33 / v_top, I33 / -v_bot
    S22_right, S22_left = I22 / u_r, I22 / -u_l
    Z33 = _plastic_Z(polys)
    polys_t = []
    for w, pts in polys:
        q = [[p[1], p[0]] for p in pts]
        if polygon_area(q) < 0:
            q.reverse()
        polys_t.append((w, q))
    Z22 = _plastic_Z(polys_t)

    xs0 = [g["bbox"][0] for g in geoms]
    xs1 = [g["bbox"][1] for g in geoms]
    ys0 = [g["bbox"][2] for g in geoms]
    ys1 = [g["bbox"][3] for g in geoms]
    bbox = [min(xs0), max(xs1), min(ys0), max(ys1)]
    return {
        "A": A, "cx": cx, "cy": cy, "Ixx": Ixx, "Iyy": Iyy, "Ixy": Ixy,
        "I_major": I_major, "I_minor": I_minor, "theta": theta,
        "theta_deg": math.degrees(theta), "axes": bs.axes,
        "I33": I33, "I22": I22, "J": J, "J_method": J_method,
        "S33": min(S33_top, S33_bot), "S33_top": S33_top,
        "S33_bot": S33_bot, "S22": min(S22_left, S22_right),
        "S22_left": S22_left, "S22_right": S22_right,
        "Z33": Z33, "Z22": Z22,
        "r33": math.sqrt(I33 / A), "r22": math.sqrt(max(I22, 0.0) / A),
        "As2": As2, "As3": As3,
        "shear_centre": sc,
        "x0": (sc["x"] - cx) if sc else None,
        "y0": (sc["y"] - cy) if sc else None,
        "b": bbox[1] - bbox[0], "h": bbox[3] - bbox[2], "bbox": bbox,
        "E_ref": E_ref, "n": ns,
        "cells": [{k: (sorted(v) if isinstance(v, set) else v)
                   for k, v in c.items()} for c in cells],
        "warnings": warnings,
    }


def make_frame_section(bs: BuiltupSection, materials: Dict[str, object]):
    """The mirrored analysis :class:`FrameSection` (same name)."""
    from skyframe.core.model import FrameSection      # local: no cycle
    p = section_properties(bs, materials)
    return FrameSection(bs.name, bs.material, A=p["A"], I33=p["I33"],
                        I22=p["I22"], J=p["J"], b=p["b"], h=p["h"],
                        shape="builtup")


# --------------------------------------------------------------------------- #
# templates
# --------------------------------------------------------------------------- #
def _plate(b, t, x, y, angle=0.0, label=None):
    d = {"kind": "plate", "b": float(b), "t": float(t), "x": float(x),
         "y": float(y), "angle": float(angle)}
    if label:
        d["label"] = label
    return d


def _param(typ, dims, x=0.0, y=0.0, rotation=0.0, mirror=False, label=None):
    d = {"kind": "param", "type": typ,
         "dims": {k: (v if isinstance(v, str) else float(v))
                  for k, v in dims.items()},
         "x": float(x), "y": float(y), "rotation": float(rotation),
         "mirror": bool(mirror)}
    if label:
        d["label"] = label
    return d


def _db(standard, name, x=0.0, y=0.0, rotation=0.0, mirror=False,
        label=None):
    d = {"kind": "db", "standard": standard, "name": name, "x": float(x),
         "y": float(y), "rotation": float(rotation), "mirror": bool(mirror)}
    if label:
        d["label"] = label
    return d


_IN = 0.0254
TEMPLATES: Dict[str, dict] = {
    "plate_girder": {
        "label": "Plate girder (I from plates)",
        "params": {"d": 1.2, "tw": 0.012, "bf_top": 0.40, "tf_top": 0.025,
                   "bf_bot": 0.50, "tf_bot": 0.030}},
    "box_girder": {
        "label": "Box girder from plates",
        "params": {"b": 0.60, "d": 1.0, "tf": 0.025, "tw": 0.016,
                   "s": 0.50}},
    "w_cover": {
        "label": "W with cover plate(s)",
        "params": {"standard": "aisc", "name": "W14X90", "plate_b": 0.40,
                   "plate_t": 0.020, "where": "both"}},
    "double_angle": {
        "label": "Double angle (2L back-to-back)",
        "params": {"d": 6 * _IN, "b": 4 * _IN, "t": 0.5 * _IN,
                   "s": 0.375 * _IN, "orientation": "LLBB"}},
    "double_channel": {
        "label": "Double channel",
        "params": {"d": 0.30, "bf": 0.09, "tf": 0.0135, "tw": 0.008,
                   "s": 0.010, "arrangement": "back_to_back"}},
    "starred_angles": {
        "label": "Starred angles",
        "params": {"b": 0.10, "t": 0.010, "s": 0.010}},
    "w_channel_cap": {
        "label": "W + channel cap (crane beam)",
        "params": {"standard": "aisc", "name": "W24X76", "d": 0.38,
                   "bf": 0.10, "tf": 0.016, "tw": 0.0105}},
    "laced_column": {
        "label": "Laced / battened column (2 channels)",
        "params": {"d": 0.30, "bf": 0.09, "tf": 0.0135, "tw": 0.008,
                   "s": 0.20, "arrangement": "toe_out",
                   "lacing": "laced"}},
    "tube_stiffener": {
        "label": "Tube with stiffener plate",
        "params": {"tube": "rect", "d": 0.20, "b": 0.20, "t": 0.010,
                   "D": 0.2191, "plate_h": 0.15, "plate_t": 0.012,
                   "side": "bottom"}},
}


def template_list() -> List[dict]:
    return [{"id": k, "label": v["label"], "params": dict(v["params"])}
            for k, v in TEMPLATES.items()]


def _channel_pair(p, arrangement):
    """Two channels, ``s`` = clear gap between the webs (back-to-back /
    toe_out) or between the flange toes (toe-to-toe / toe_in)."""
    d, bf, s = p["d"], p["bf"], p["s"]
    dims = {"d": d, "bf": bf, "tf": p["tf"], "tw": p["tw"]}
    if arrangement in ("back_to_back", "toe_out"):
        # webs facing each other: right channel unmirrored (web on its left)
        xr = s / 2 + bf / 2
        return [_param("channel", dims, xr, 0.0, label="Channel R"),
                _param("channel", dims, -xr, 0.0, mirror=True,
                       label="Channel L")]
    if arrangement in ("toe_to_toe", "toe_in"):
        xr = s / 2 + bf / 2
        return [_param("channel", dims, xr, 0.0, mirror=True,
                       label="Channel R"),
                _param("channel", dims, -xr, 0.0, label="Channel L")]
    raise ValueError("arrangement must be back_to_back | toe_to_toe")


def template_section(tid: str, params: Optional[dict] = None,
                     name: str = "BU1", material: str = "") -> BuiltupSection:
    """Build a template section (components in SI; see TEMPLATES)."""
    if tid not in TEMPLATES:
        raise ValueError(f"unknown built-up template {tid!r}; choose from "
                         f"{sorted(TEMPLATES)}")
    p = dict(TEMPLATES[tid]["params"])
    for k, v in (params or {}).items():
        if k in p:
            p[k] = v if isinstance(v, str) else float(v)
    comps: List[dict] = []
    notes = ""
    if tid == "plate_girder":
        d, tw = p["d"], p["tw"]
        bt, tt, bb, tb = p["bf_top"], p["tf_top"], p["bf_bot"], p["tf_bot"]
        hw = d - tt - tb
        comps = [_plate(bt, tt, 0.0, d / 2 - tt / 2, label="Top flange"),
                 _plate(hw, tw, 0.0, -d / 2 + tb + hw / 2, 90.0,
                        label="Web"),
                 _plate(bb, tb, 0.0, -d / 2 + tb / 2, label="Bottom flange")]
    elif tid == "box_girder":
        b, d, tf, tw, s = p["b"], p["d"], p["tf"], p["tw"], p["s"]
        hw = d - 2 * tf
        comps = [_plate(b, tf, 0.0, d / 2 - tf / 2, label="Top flange"),
                 _plate(b, tf, 0.0, -d / 2 + tf / 2, label="Bottom flange"),
                 _plate(hw, tw, -s / 2, 0.0, 90.0, label="Web L"),
                 _plate(hw, tw, s / 2, 0.0, 90.0, label="Web R")]
    elif tid == "w_cover":
        from skyframe.core import builtup_db
        rec = builtup_db.shape(_std_id(p["standard"]), p["name"])
        dW = float(rec["dims"]["d"])
        comps = [_db(p["standard"], p["name"], label=p["name"])]
        pb, pt = p["plate_b"], p["plate_t"]
        if p["where"] in ("top", "both"):
            comps.append(_plate(pb, pt, 0.0, dW / 2 + pt / 2,
                                label="Top cover"))
        if p["where"] in ("bottom", "both"):
            comps.append(_plate(pb, pt, 0.0, -dW / 2 - pt / 2,
                                label="Bottom cover"))
        if p["where"] not in ("top", "bottom", "both"):
            raise ValueError("where must be top | bottom | both")
    elif tid == "double_angle":
        a, b, t, s = p["d"], p["b"], p["t"], p["s"]
        o = p["orientation"]
        if o not in ("LLBB", "SLBB"):
            raise ValueError("orientation must be LLBB or SLBB")
        vert, hor = (max(a, b), min(a, b)) if o == "LLBB" \
            else (min(a, b), max(a, b))
        dims = {"d": vert, "b": hor, "t": t}
        x = s / 2 + hor / 2
        comps = [_param("angle", dims, x, 0.0, label="Angle R"),
                 _param("angle", dims, -x, 0.0, mirror=True,
                        label="Angle L")]
    elif tid == "double_channel":
        comps = _channel_pair(p, p["arrangement"])
    elif tid == "starred_angles":
        b, t, s = p["b"], p["t"], p["s"]
        dims = {"d": b, "b": b, "t": t}
        # angle A: heel at (s/2, -t/2), legs up and right; B = A turned 180
        xa, ya = s / 2 + b / 2, -t / 2 + b / 2
        comps = [_param("angle", dims, xa, ya, label="Angle A"),
                 _param("angle", dims, -xa, -ya, 180.0, label="Angle B")]
    elif tid == "w_channel_cap":
        from skyframe.core import builtup_db
        rec = builtup_db.shape(_std_id(p["standard"]), p["name"])
        dW = float(rec["dims"]["d"])
        cd = {"d": p["d"], "bf": p["bf"], "tf": p["tf"], "tw": p["tw"]}
        yc = dW / 2 + p["tw"] - p["bf"] / 2
        comps = [_db(p["standard"], p["name"], label=p["name"]),
                 _param("channel", cd, 0.0, yc, -90.0, label="Cap channel")]
    elif tid == "laced_column":
        arr = {"toe_out": "back_to_back", "toe_in": "toe_to_toe"}.get(
            p["arrangement"], p["arrangement"])
        comps = _channel_pair(p, arr)
        notes = (f"{p['lacing'].capitalize()} column: properties are those "
                 "of the two chords only (lacing/battens carry shear, not "
                 "bending). The frame element assumes the chords act "
                 "compositely with NO shear flexibility of the "
                 f"{p['lacing']} system; for buckling use a modified "
                 "slenderness (e.g. AISC 360 E6) outside this analysis.")
    elif tid == "tube_stiffener":
        if p["tube"] == "round":
            D = p["D"]
            tube = _param("round_tube", {"D": D, "t": p["t"]}, label="Tube")
            half = D / 2
        else:
            tube = _param("rect_tube", {"d": p["d"], "b": p["b"],
                                        "t": p["t"]}, label="Tube")
            half = p["d"] / 2
        h, tp = p["plate_h"], p["plate_t"]
        sg = -1.0 if p["side"] == "bottom" else 1.0
        comps = [tube, _plate(h, tp, 0.0, sg * (half + h / 2), 90.0,
                              label="Stiffener")]
    bs = BuiltupSection(name=name, material=material,
                        components=[_comp_from_dict(c) for c in comps],
                        template={"id": tid, "params": p}, notes=notes)
    return resolve_components(bs)


# --------------------------------------------------------------------------- #
# model glue (BuildingModel hooks call these with one line each)
# --------------------------------------------------------------------------- #
_KEEP = ("mod_A", "mod_I33", "mod_I22", "mod_J", "As2", "As3",
         "shear_deformation", "mod_As2", "mod_As3", "mod_mass", "mod_weight")


def add_to_model(mdl, bs: BuiltupSection) -> dict:
    """Add/replace ``bs`` AND its mirrored FrameSection; returns the props.

    Resolves database snapshots, validates, keeps an existing same-named
    section's modifiers / shear settings (exactly like designer sections).
    """
    resolve_components(bs)
    validate_builtup_section(bs, mdl.materials)
    if bs.name in getattr(mdl, "designer_sections", {}):
        raise ValueError(f"Built-up section {bs.name}: the name is used by a "
                         "Section Designer section")
    props = section_properties(bs, mdl.materials)
    sec = make_frame_section(bs, mdl.materials)
    old = mdl.sections.get(bs.name)
    if old is not None:
        for key in _KEEP:
            setattr(sec, key, getattr(old, key))
    mdl._validate_section_mods(sec)
    mdl.builtup_sections[bs.name] = bs
    mdl.sections[bs.name] = sec
    return props


def remove_from_model(mdl, name: str) -> None:
    """Delete a built-up section and its mirrored FrameSection (KeyError for
    an unknown name, ValueError while a member still uses it)."""
    if name not in mdl.builtup_sections:
        raise KeyError(name)
    used = [m.uid for m in mdl.members if m.section == name]
    if used:
        raise ValueError(f"Built-up section {name}: still used by member(s) "
                         f"{used}")
    del mdl.builtup_sections[name]
    mdl.sections.pop(name, None)


def model_to_dict(mdl) -> dict:
    """``{"builtup_sections": {...}}`` only when any exist (else {})."""
    bus = getattr(mdl, "builtup_sections", None)
    if not bus:
        return {}
    return {"builtup_sections": {k: v.to_dict() for k, v in bus.items()}}


def model_from_dict(mdl, d: dict) -> None:
    raw = d.get("builtup_sections")
    if not raw:
        return
    if not isinstance(raw, dict):
        raise ValueError("builtup_sections must be an object")
    for name, bd in raw.items():
        bs = BuiltupSection.from_dict(bd)
        bs.name = bs.name or str(name)
        mdl.builtup_sections[str(name)] = bs


def validate_model(mdl) -> None:
    for bs in getattr(mdl, "builtup_sections", {}).values():
        if any(c["kind"] == "db" and ("type" not in c or "dims" not in c)
               for c in bs.components):
            resolve_components(bs)
        validate_builtup_section(bs, mdl.materials)
