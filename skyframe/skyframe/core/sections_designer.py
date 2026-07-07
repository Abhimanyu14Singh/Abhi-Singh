"""Section Designer (v0.21): arbitrary polygon fiber sections.

A :class:`DesignerSection` is a named cross-section drawn as one or more
POLYGONS (each a closed vertex loop in the section-local ``(y, z)`` plane,
optionally flagged ``hole``) plus discrete REBAR points.  Everything is SI:
m, m^2, kPa.

Coordinate/axis convention (matches :class:`skyframe.core.model.FrameSection`):
``z`` is the section DEPTH direction and ``y`` the width direction, so

* ``I33`` (major axis, "bends under gravity") = integral of z^2 dA,
* ``I22`` (minor axis)                        = integral of y^2 dA,

both about the GROSS centroid.  For a ``b x h`` rectangle drawn with y in
[-b/2, b/2] and z in [-h/2, h/2] this reproduces ``b*h^3/12`` / ``h*b^3/12``
exactly.  ``b``/``h`` of the derived frame section are the bounding-box
extents in y / z.

All area properties are EXACT closed-form polygon (shoelace) integrals —
no numerical quadrature:

    A   = 1/2  sum (y_i z_{i+1} - y_{i+1} z_i)                (signed)
    Sy  = 1/6  sum (y_i + y_{i+1}) cr_i          (= A * Cy)
    Sz  = 1/6  sum (z_i + z_{i+1}) cr_i          (= A * Cz)
    Iyy = 1/12 sum (y_i^2 + y_i y_{i+1} + y_{i+1}^2) cr_i     (about origin)
    Izz = 1/12 sum (z_i^2 + z_i z_{i+1} + z_{i+1}^2) cr_i     (about origin)

with ``cr_i = y_i z_{i+1} - y_{i+1} z_i``.  Hole polygons SUBTRACT
(every polygon is normalized to CCW and holes enter with sign -1).

TORSION is the St. Venant polygon APPROXIMATION ``J ~ A^4 / (40 * Ip)``
with ``Ip = I33 + I22`` (exact for a circle, ~10% class accuracy for
compact solid shapes — DOCUMENTED APPROXIMATE; rebar and holes enter only
through A and Ip).

REBAR is kept SEPARATE from the gross polygon area (bars are POINT areas
laid on top of the base material — the displaced base material is NOT
deducted).  The derived frame-analysis section uses the standard
transformed-section rule with ``n = E_bar / E_base``:

    A_tr   = A_gross + sum (n_k - 1) As_k
    C_tr   = (A_gross * C_gross + sum (n_k - 1) As_k * p_k) / A_tr
    I33_tr = [I33_g + A_gross (Cz_g - Cz_tr)^2]
             + sum (n_k - 1) As_k (z_k - Cz_tr)^2      (bars: point areas,
    I22_tr =  ... same with y ...                       no own inertia)
    J_tr   = J_gross                                    (rebar ignored)

PMM interaction (:func:`pmm_surface`):

* ``base == "concrete"`` — ACI strain-compatibility sweep of the
  neutral-axis depth c with the Whitney rectangular block integrated
  EXACTLY over the polygons clipped at the block depth ``a = beta1*c``
  (:func:`clip_polygon` — Sutherland-Hodgman against a horizontal line),
  discrete rebar strains ``eps = 0.003 (c - depth)/c`` clamped at +-fy,
  displaced concrete at the bars ignored, and phi per the existing
  ``design.concrete.phi_from_strain`` — the same assumptions as
  ``design.concrete.column_interaction``, whose pure-compression /
  balanced / pure-bending points a rectangular designer section
  reproduces.  Moments are taken about the GROSS centroid; positive M =
  compression on the +depth face.  Requires at least one rebar point.
* ``base == "steel"``  — full plastic stress distribution: the section is
  clipped at a sweep of plastic-neutral-axis positions, +-Fy on the two
  sides (rebar included at its own +-fy), phi = 1.0 (NOMINAL plastic
  surface).  The P = 0 point is bisected exactly, so a b x h rectangle
  returns M = Fy * b h^2 / 4 = Fy * Zp exactly.

This module deliberately imports nothing from the rest of SkyFrame at
module level (model/design imports are function-local) so it can be used
by :mod:`skyframe.core.model` without import cycles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

__all__ = [
    "DesignerSection", "DESIGNER_BASES",
    "polygon_area", "polygon_integrals", "validate_polygon", "clip_polygon",
    "section_properties", "make_frame_section", "pmm_surface",
    "REBAR_FY_DEFAULT", "STEEL_FY_DEFAULT", "ES_BAR_DEFAULT",
]

DESIGNER_BASES = ("concrete", "steel")

REBAR_FY_DEFAULT = 420_000.0   # kPa — rebar fy when the material has none
STEEL_FY_DEFAULT = 345_000.0   # kPa — base-steel Fy fallback (A992)
ES_BAR_DEFAULT = 200_000_000.0  # kPa — rebar modulus for PMM strains
_TOL = 1e-12


# --------------------------------------------------------------------------- #
# exact polygon integrals (shoelace closed forms)
# --------------------------------------------------------------------------- #
def polygon_area(vertices) -> float:
    """Signed shoelace area (positive = counter-clockwise)."""
    a = 0.0
    n = len(vertices)
    for i in range(n):
        y1, z1 = vertices[i]
        y2, z2 = vertices[(i + 1) % n]
        a += y1 * z2 - y2 * z1
    return 0.5 * a


def polygon_integrals(vertices) -> Tuple[float, float, float, float, float]:
    """(A, Sy, Sz, Iyy, Izz) — signed exact integrals about the ORIGIN.

    ``Sy = int y dA``, ``Sz = int z dA``, ``Iyy = int y^2 dA``,
    ``Izz = int z^2 dA`` (module-docstring closed forms).
    """
    A = Sy = Sz = Iyy = Izz = 0.0
    n = len(vertices)
    for i in range(n):
        y1, z1 = vertices[i]
        y2, z2 = vertices[(i + 1) % n]
        cr = y1 * z2 - y2 * z1
        A += cr
        Sy += (y1 + y2) * cr
        Sz += (z1 + z2) * cr
        Iyy += (y1 * y1 + y1 * y2 + y2 * y2) * cr
        Izz += (z1 * z1 + z1 * z2 + z2 * z2) * cr
    return A / 2.0, Sy / 6.0, Sz / 6.0, Iyy / 12.0, Izz / 12.0


def _segments_intersect(p1, p2, p3, p4) -> bool:
    """True when the OPEN segments p1-p2 and p3-p4 properly cross.

    Shared endpoints (adjacent polygon edges) do not count; collinear
    overlap DOES count (a degenerate self-touching outline is rejected).
    """
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on_seg(a, b, c):
        return (min(a[0], b[0]) - _TOL <= c[0] <= max(a[0], b[0]) + _TOL
                and min(a[1], b[1]) - _TOL <= c[1] <= max(a[1], b[1]) + _TOL)

    d1 = orient(p3, p4, p1)
    d2 = orient(p3, p4, p2)
    d3 = orient(p1, p2, p3)
    d4 = orient(p1, p2, p4)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    # collinear overlap (any interior touch)
    for (a, b, c, d) in ((p1, p2, p3, d3), (p1, p2, p4, d4),
                         (p3, p4, p1, d1), (p3, p4, p2, d2)):
        if abs(d) <= _TOL and on_seg(a, b, c):
            return True
    return False


def validate_polygon(vertices) -> None:
    """Raise ``ValueError`` unless ``vertices`` is a usable simple polygon.

    Checks: >= 3 vertices, each a finite (y, z) pair, no repeated
    consecutive vertices, non-zero shoelace area, and NO self-intersection
    (every pair of non-adjacent edges tested for crossing — the standard
    segment-intersection sweep of the shoelace outline).
    """
    if not isinstance(vertices, (list, tuple)) or len(vertices) < 3:
        raise ValueError("polygon needs at least 3 vertices")
    pts = []
    for v in vertices:
        if (not isinstance(v, (list, tuple)) or len(v) != 2
                or not all(isinstance(c, (int, float))
                           and not isinstance(c, bool)
                           and math.isfinite(c) for c in v)):
            raise ValueError("polygon vertices must be finite [y, z] pairs")
        pts.append((float(v[0]), float(v[1])))
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        if math.dist(pts[i], pts[j]) < 1e-12:
            raise ValueError("polygon has a repeated consecutive vertex")
    if abs(polygon_area(pts)) < 1e-12:
        raise ValueError("polygon has zero area")
    for i in range(n):
        for j in range(i + 1, n):
            # skip adjacent edges (share a vertex), incl. the wrap pair
            if j == i or j == (i + 1) % n or (j + 1) % n == i:
                continue
            if _segments_intersect(pts[i], pts[(i + 1) % n],
                                   pts[j], pts[(j + 1) % n]):
                raise ValueError("polygon is self-intersecting")


def clip_polygon(vertices, z0: float, keep: str = "above") -> List[list]:
    """EXACT polygon clip against the horizontal line ``z = z0``.

    Sutherland-Hodgman against a single half-plane: ``keep="above"``
    returns the part with ``z >= z0``, ``keep="below"`` the part with
    ``z <= z0`` (edge crossings interpolated exactly; the cut chord is
    part of the result outline).  Returns ``[]`` when nothing remains.
    Orientation of the input is preserved.
    """
    if keep not in ("above", "below"):
        raise ValueError(f"keep must be above|below, got {keep!r}")
    sgn = 1.0 if keep == "above" else -1.0
    out: List[list] = []
    n = len(vertices)
    for i in range(n):
        y1, z1 = vertices[i]
        y2, z2 = vertices[(i + 1) % n]
        in1 = sgn * (z1 - z0) >= 0.0
        in2 = sgn * (z2 - z0) >= 0.0
        if in1:
            out.append([y1, z1])
        if in1 != in2:                       # edge crosses the line
            t = (z0 - z1) / (z2 - z1)
            out.append([y1 + t * (y2 - y1), z0])
    return out if len(out) >= 3 else []


# --------------------------------------------------------------------------- #
# the designer section object
# --------------------------------------------------------------------------- #
@dataclass
class DesignerSection:
    """Arbitrary polygon section (v0.21).  See the module docstring.

    ``material`` is the BASE material (its E is the reference modulus of
    the derived frame section and the transformed-rebar rule);  ``base``
    picks the PMM/fiber constitutive path ("concrete" | "steel").

    ``polygons``: ``[{"vertices": [[y, z], ...], "material": str,
    "hole": bool}, ...]`` — at least one non-hole polygon.
    ``rebar``: ``[{"y": f, "z": f, "area": f, "material": str}, ...]``.
    """

    name: str
    material: str
    base: str = "concrete"
    polygons: List[dict] = field(default_factory=list)
    rebar: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "material": self.material, "base": self.base,
            "polygons": [{"vertices": [[float(y), float(z)]
                                       for (y, z) in p["vertices"]],
                          "material": str(p.get("material", self.material)),
                          "hole": bool(p.get("hole", False))}
                         for p in self.polygons],
            "rebar": [{"y": float(r["y"]), "z": float(r["z"]),
                       "area": float(r["area"]),
                       "material": str(r["material"])}
                      for r in self.rebar],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DesignerSection":
        if not isinstance(d, dict):
            raise ValueError("designer section must be a JSON object")
        name = str(d.get("name") or "")
        ds = cls(name=name, material=str(d.get("material") or ""),
                 base=str(d.get("base", "concrete")))
        for p in d.get("polygons") or []:
            ds.polygons.append({
                "vertices": [[float(v[0]), float(v[1])]
                             for v in (p.get("vertices") or [])],
                "material": str(p.get("material", ds.material)),
                "hole": bool(p.get("hole", False))})
        for r in d.get("rebar") or []:
            ds.rebar.append({"y": float(r["y"]), "z": float(r["z"]),
                             "area": float(r["area"]),
                             "material": str(r["material"])})
        return ds

    # ---- normalized geometry: [(sign, ccw_vertices, material), ...] ----
    def signed_polygons(self) -> List[Tuple[float, List[list], str]]:
        """Polygons normalized to CCW with sign +1 (solid) / -1 (hole)."""
        out = []
        for p in self.polygons:
            verts = [[float(y), float(z)] for (y, z) in p["vertices"]]
            if polygon_area(verts) < 0.0:
                verts = list(reversed(verts))
            out.append((-1.0 if p.get("hole") else 1.0, verts,
                        str(p.get("material", self.material))))
        return out


def validate_designer_section(ds: DesignerSection,
                              materials: Dict[str, object]) -> None:
    """Full validation against the model's material table (raises)."""
    if not ds.name:
        raise ValueError("designer section: name is required")
    if ds.base not in DESIGNER_BASES:
        raise ValueError(f"Designer section {ds.name}: base must be one of "
                         f"{DESIGNER_BASES}, got {ds.base!r}")
    if ds.material not in materials:
        raise ValueError(f"Designer section {ds.name}: unknown material "
                         f"{ds.material!r}")
    if not any(not p.get("hole") for p in ds.polygons):
        raise ValueError(f"Designer section {ds.name}: needs at least one "
                         "non-hole polygon")
    for p in ds.polygons:
        try:
            validate_polygon(p.get("vertices"))
        except ValueError as exc:
            raise ValueError(f"Designer section {ds.name}: {exc}") from None
        pm = p.get("material", ds.material)
        if pm not in materials:
            raise ValueError(f"Designer section {ds.name}: polygon "
                             f"references unknown material {pm!r}")
    for r in ds.rebar:
        for key in ("y", "z", "area"):
            v = r.get(key)
            if not (isinstance(v, (int, float)) and math.isfinite(v)):
                raise ValueError(f"Designer section {ds.name}: rebar {key} "
                                 f"must be finite (got {v!r})")
        if r["area"] <= 0.0:
            raise ValueError(f"Designer section {ds.name}: rebar area must "
                             "be > 0")
        if r.get("material") not in materials:
            raise ValueError(f"Designer section {ds.name}: rebar references "
                             f"unknown material {r.get('material')!r}")
    A = sum(sgn * abs(polygon_area(v))
            for sgn, v, _m in ds.signed_polygons())
    if A <= 1e-12:
        raise ValueError(f"Designer section {ds.name}: net area must be > 0 "
                         "(holes exceed solids?)")


# --------------------------------------------------------------------------- #
# section properties
# --------------------------------------------------------------------------- #
def section_properties(ds: DesignerSection,
                       materials: Dict[str, object]) -> dict:
    """Exact gross + transformed properties (module-docstring rules).

    Returns ``{A, Cy, Cz, I33, I22, Ip, J, b, h, A_tr, Cy_tr, Cz_tr,
    I33_tr, I22_tr, As_total, n_bars}`` — gross values are pure geometry
    (holes subtract, rebar EXCLUDED); ``*_tr`` add the ``(n-1)*As``
    transformed rebar contributions with ``n = E_bar / E_base``;
    ``J = A^4/(40*Ip)`` is the documented St. Venant approximation
    (gross section only).
    """
    validate_designer_section(ds, materials)
    A = Sy = Sz = Iyy = Izz = 0.0
    ys: List[float] = []
    zs: List[float] = []
    for sgn, verts, _m in ds.signed_polygons():
        a, sy, sz, iyy, izz = polygon_integrals(verts)   # CCW: a > 0
        A += sgn * a
        Sy += sgn * sy
        Sz += sgn * sz
        Iyy += sgn * iyy
        Izz += sgn * izz
        if sgn > 0:
            ys += [v[0] for v in verts]
            zs += [v[1] for v in verts]
    Cy, Cz = Sy / A, Sz / A
    I22 = Iyy - A * Cy * Cy                  # int y^2 dA about the centroid
    I33 = Izz - A * Cz * Cz                  # int z^2 dA about the centroid
    Ip = I33 + I22
    J = A ** 4 / (40.0 * Ip)                 # St. Venant approx (documented)

    E_base = float(materials[ds.material].E)
    A_tr, Sy_tr, Sz_tr = A, A * Cy, A * Cz
    for r in ds.rebar:
        n = float(materials[r["material"]].E) / E_base
        dA = (n - 1.0) * r["area"]
        A_tr += dA
        Sy_tr += dA * r["y"]
        Sz_tr += dA * r["z"]
    Cy_tr, Cz_tr = Sy_tr / A_tr, Sz_tr / A_tr
    I33_tr = I33 + A * (Cz - Cz_tr) ** 2
    I22_tr = I22 + A * (Cy - Cy_tr) ** 2
    for r in ds.rebar:
        n = float(materials[r["material"]].E) / E_base
        dA = (n - 1.0) * r["area"]
        I33_tr += dA * (r["z"] - Cz_tr) ** 2
        I22_tr += dA * (r["y"] - Cy_tr) ** 2
    return {
        "A": A, "Cy": Cy, "Cz": Cz, "I33": I33, "I22": I22, "Ip": Ip,
        "J": J, "b": max(ys) - min(ys), "h": max(zs) - min(zs),
        "A_tr": A_tr, "Cy_tr": Cy_tr, "Cz_tr": Cz_tr,
        "I33_tr": I33_tr, "I22_tr": I22_tr,
        "As_total": sum(r["area"] for r in ds.rebar),
        "n_bars": len(ds.rebar),
    }


def make_frame_section(ds: DesignerSection, materials: Dict[str, object]):
    """Derive the analysis :class:`FrameSection` (same name) from ``ds``.

    Uses the TRANSFORMED A/I33/I22 (rebar via ``(n-1)*As``), the gross
    approximate ``J``, and the bounding-box ``b``/``h`` so the section
    plugs into the existing frame pipeline (drawing, concrete checks,
    hinges) unchanged.
    """
    from skyframe.core.model import FrameSection      # local: no cycle
    p = section_properties(ds, materials)
    return FrameSection(ds.name, ds.material, A=p["A_tr"], I33=p["I33_tr"],
                        I22=p["I22_tr"], J=p["J"], b=p["b"], h=p["h"])


# --------------------------------------------------------------------------- #
# PMM interaction surface
# --------------------------------------------------------------------------- #
def _axis_polys(ds: DesignerSection, axis: str):
    """Polygons/rebar in (u, v) coordinates with v = the DEPTH direction.

    axis "33": v = z (strong-axis bending -> M3); axis "22": v = y.
    The (u, v) swap mirrors the outline, so orientation is re-normalized.
    """
    if axis not in ("33", "22"):
        raise ValueError(f"axis must be 33|22, got {axis!r}")
    polys = []
    for sgn, verts, m in ds.signed_polygons():
        vv = (verts if axis == "33"
              else [[v[1], v[0]] for v in verts])
        if polygon_area(vv) < 0.0:
            vv = list(reversed(vv))
        polys.append((sgn, vv, m))
    bars = [((r["y"], r["z"]) if axis == "33" else (r["z"], r["y"]),
             float(r["area"]), str(r["material"])) for r in ds.rebar]
    return polys, bars


def _clip_A_Sv(polys, v0: float) -> Tuple[float, float]:
    """Net (area, first moment of depth) of the section part with v >= v0."""
    A = Sv = 0.0
    for sgn, verts, _m in polys:
        part = clip_polygon(verts, v0, "above")
        if part:
            a, _sy, sv, _iy, _iz = polygon_integrals(part)
            A += sgn * a
            Sv += sgn * sv
    return A, Sv


def pmm_surface(ds: DesignerSection, materials: Dict[str, object],
                axis: str = "33", n_sweep: int = 16) -> dict:
    """P-M interaction diagram about one axis (module docstring).

    Returns ``{"base", "axis", "points": [[phiMn, phiPn], ...],
    "detail": [{label, c, Pn, Mn, eps_t, phi, phiPn, phiMn}, ...]}``
    ordered from pure compression down to pure tension.  Concrete uses
    the ACI strain-compatibility/Whitney machinery shared with
    ``design.concrete`` (phi = phi_from_strain, 0.80/0.65 compression
    cap); steel is the NOMINAL full-plastic surface (phi = 1).
    """
    validate_designer_section(ds, materials)
    polys, bars = _axis_polys(ds, axis)
    props = section_properties(ds, materials)
    Cv = props["Cz"] if axis == "33" else props["Cy"]
    if ds.base == "steel":
        return _pmm_steel(ds, materials, polys, bars, Cv, axis, n_sweep)
    return _pmm_concrete(ds, materials, polys, bars, Cv, axis, n_sweep)


def _pmm_concrete(ds, materials, polys, bars, Cv, axis, n_sweep) -> dict:
    from skyframe.design.concrete import (EPS_CU, PHI_COMPRESSION, beta1,
                                          phi_from_strain)
    from skyframe.design.wall import fc_from_E
    if not bars:
        raise ValueError(f"Designer section {ds.name}: the concrete PMM "
                         "surface needs at least one rebar point")
    base = materials[ds.material]
    fc = getattr(base, "fc", 0.0) or fc_from_E(float(base.E))
    b1 = beta1(fc)
    # per-bar (u, v, As, fy, Es)
    binfo = []
    for (u, v), As, mname in bars:
        m = materials[mname]
        fy = getattr(m, "fy", 0.0) or REBAR_FY_DEFAULT
        binfo.append((v, As, float(fy), float(m.E) or ES_BAR_DEFAULT))
    v_top = max(v for sgn, verts, _m in polys if sgn > 0
                for (_u, v) in verts)
    v_bot = min(v for sgn, verts, _m in polys if sgn > 0
                for (_u, v) in verts)
    depth_full = v_top - v_bot
    Ag = sum(sgn * abs(polygon_area(verts)) for sgn, verts, _m in polys)
    Ast = sum(As for _v, As, _fy, _Es in binfo)
    # extreme tension bar (deepest below the compression face)
    v_ext = min(v for v, _As, _fy, _Es in binfo)
    d_t = v_top - v_ext
    fy_ext, Es_ext = next((fy, Es) for v, _As, fy, Es in binfo if v == v_ext)
    eps_ty = fy_ext / Es_ext

    def forces(c: float) -> Tuple[float, float, float]:
        a = min(b1 * c, depth_full)
        A_c, Sv_c = _clip_A_Sv(polys, v_top - a)
        Pn = 0.85 * fc * A_c
        Mn = 0.85 * fc * (Sv_c - Cv * A_c)
        for v, As, fy, Es in binfo:
            eps = EPS_CU * (c - (v_top - v)) / c
            fs = max(-fy, min(fy, Es * eps))          # +compression
            Pn += As * fs
            Mn += As * fs * (v - Cv)
        eps_t = EPS_CU * (d_t - c) / c
        return Pn, Mn, eps_t

    # 1 — pure compression: the ACI 22.4.2 tied-column closed form (the
    # same displaced-concrete/0.80-cap treatment as column_interaction)
    Pn0 = 0.85 * fc * (Ag - Ast) + sum(As * fy
                                       for _v, As, fy, _Es in binfo)
    phiPn_max = 0.80 * PHI_COMPRESSION * Pn0
    detail = [{"label": "pure compression", "c": math.inf, "Pn": 0.80 * Pn0,
               "Mn": 0.0, "eps_t": -EPS_CU, "phi": PHI_COMPRESSION,
               "phiPn": phiPn_max, "phiMn": 0.0}]

    # pure bending: bisect c in (0, d_t] for Pn = 0 (monotonic in c)
    lo, hi = 1e-6 * d_t, d_t
    Pn_lo = forces(lo)[0]
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if (Pn_lo < 0.0) == (forces(mid)[0] < 0.0):
            lo, Pn_lo = mid, forces(mid)[0]
        else:
            hi = mid
    c0 = 0.5 * (lo + hi)
    c_b = d_t * EPS_CU / (EPS_CU + eps_ty)
    labels = {round(d_t, 15): "eps_t = 0", round(c_b, 15): "balanced",
              round(c0, 15): "pure bending"}
    cs = {d_t, c_b, c0}
    for k in range(1, n_sweep):
        cs.add(c0 + (d_t - c0) * k / n_sweep)
    for c in sorted(cs, reverse=True):
        Pn, Mn, eps_t = forces(c)
        phi = phi_from_strain(eps_t, eps_ty)
        detail.append({"label": labels.get(round(c, 15), ""), "c": c,
                       "Pn": Pn, "Mn": Mn, "eps_t": eps_t, "phi": phi,
                       "phiPn": min(phi * Pn, phiPn_max),
                       "phiMn": phi * Mn})
    Pt = sum(As * fy for _v, As, fy, _Es in binfo)
    detail.append({"label": "pure tension", "c": 0.0, "Pn": -Pt, "Mn": 0.0,
                   "eps_t": math.inf, "phi": 0.9, "phiPn": -0.9 * Pt,
                   "phiMn": 0.0})
    return {"base": "concrete", "axis": axis,
            "points": [[p["phiMn"], p["phiPn"]] for p in detail],
            "detail": detail}


def _pmm_steel(ds, materials, polys, bars, Cv, axis, n_sweep) -> dict:
    base = materials[ds.material]
    Fy = getattr(base, "fy", 0.0) or STEEL_FY_DEFAULT
    v_top = max(v for sgn, verts, _m in polys if sgn > 0
                for (_u, v) in verts)
    v_bot = min(v for sgn, verts, _m in polys if sgn > 0
                for (_u, v) in verts)
    A_tot, Sv_tot = 0.0, 0.0
    for sgn, verts, _m in polys:
        a, _sy, sv, _iy, _iz = polygon_integrals(verts)
        A_tot += sgn * a
        Sv_tot += sgn * sv
    binfo = []
    for (u, v), As, mname in bars:
        m = materials[mname]
        fy = getattr(m, "fy", 0.0) or REBAR_FY_DEFAULT
        binfo.append((v, As, float(fy)))

    def forces(v0: float) -> Tuple[float, float]:
        A_a, Sv_a = _clip_A_Sv(polys, v0)     # compression side (v >= v0)
        A_b, Sv_b = A_tot - A_a, Sv_tot - Sv_a
        P = Fy * (A_a - A_b)
        M = Fy * ((Sv_a - Cv * A_a) - (Sv_b - Cv * A_b))
        for v, As, fy in binfo:
            s = fy if v >= v0 else -fy
            P += s * As
            M += s * As * (v - Cv)
        return P, M

    # exact P = 0 plastic-bending point (P is monotone decreasing in v0)
    lo, hi = v_bot, v_top
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if forces(mid)[0] > 0.0:
            lo = mid
        else:
            hi = mid
    v_pb = 0.5 * (lo + hi)
    labels = {round(v_pb, 15): "pure bending"}
    vs = {v_pb}
    for k in range(1, n_sweep):
        vs.add(v_bot + (v_top - v_bot) * k / n_sweep)
    detail = [{"label": "pure compression", "v0": v_bot,
               "Pn": forces(v_bot - 1.0)[0], "Mn": 0.0, "phi": 1.0}]
    detail[0]["phiPn"], detail[0]["phiMn"] = detail[0]["Pn"], 0.0
    for v0 in sorted(vs):                      # v0 up = P down
        P, M = forces(v0)
        detail.append({"label": labels.get(round(v0, 15), ""), "v0": v0,
                       "Pn": P, "Mn": M, "phi": 1.0, "phiPn": P, "phiMn": M})
    Pten, _ = forces(v_top + 1.0)
    detail.append({"label": "pure tension", "v0": v_top, "Pn": Pten,
                   "Mn": 0.0, "phi": 1.0, "phiPn": Pten, "phiMn": 0.0})
    return {"base": "steel", "axis": axis,
            "points": [[p["phiMn"], p["phiPn"]] for p in detail],
            "detail": detail}
