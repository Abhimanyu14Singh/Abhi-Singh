"""Building-centric data model (the "ETABS layer").

Everything is stored in consistent kN / m / tonne / s units:
  * E, G ........ kPa (kN/m^2)
  * loads ....... kN, kN/m
  * mass ........ tonne (1 kN = 1 tonne * 1 m/s^2)
  * length ...... m

The model is deliberately solver-agnostic: `skyframe.engine` translates it
into an OpenSees domain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

G_ACCEL = 9.80665  # m/s^2
_PLANAR_TOL = 1e-6  # m


def _vsub(a: Tuple[float, float, float], b: Tuple[float, float, float]):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _vcross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _polygon_area3d(pts: List[Tuple[float, float, float]]) -> float:
    """Area of a planar 3D polygon (vector shoelace: 0.5*|sum p_i x p_i+1|)."""
    sx = sy = sz = 0.0
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        c = _vcross(a, b)
        sx += c[0]
        sy += c[1]
        sz += c[2]
    return 0.5 * math.sqrt(sx * sx + sy * sy + sz * sz)


# --------------------------------------------------------------------------- #
# Properties
# --------------------------------------------------------------------------- #
@dataclass
class Material:
    name: str
    E: float                    # kPa
    nu: float = 0.2
    unit_weight: float = 24.0   # kN/m^3 (used for self-mass if enabled)

    @property
    def G(self) -> float:
        return self.E / (2.0 * (1.0 + self.nu))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["G"] = self.G
        return d


@dataclass
class FrameSection:
    """Prismatic frame section (properties about local axes).

    I33 = major-axis ("strong") moment of inertia, bends under gravity.
    I22 = minor-axis moment of inertia.
    """

    name: str
    material: str
    A: float          # m^2
    I33: float        # m^4
    I22: float        # m^4
    J: float          # m^4
    b: float = 0.0    # m (optional, drawing only)
    h: float = 0.0    # m (optional, drawing only)
    # v0.4 stiffness modifiers (ETABS-style property multipliers, all > 0):
    mod_A: float = 1.0
    mod_I33: float = 1.0
    mod_I22: float = 1.0
    mod_J: float = 1.0

    @staticmethod
    def rectangular(name: str, material: str, b: float, h: float) -> "FrameSection":
        """Rectangular b x h section (h = depth about the major axis)."""
        A = b * h
        I33 = b * h ** 3 / 12.0
        I22 = h * b ** 3 / 12.0
        # torsion constant for a rectangle (Roark)
        a, c = max(b, h) / 2.0, min(b, h) / 2.0
        J = a * c ** 3 * (16.0 / 3.0 - 3.36 * (c / a) * (1.0 - c ** 4 / (12.0 * a ** 4)))
        return FrameSection(name, material, A, I33, I22, J, b=b, h=h)

    @staticmethod
    def from_library(name: str, material: str) -> "FrameSection":
        """Build a section from the built-in steel shape library (v0.3).

        ``name`` is an AISC label like ``"W12x26"``; properties come from
        :mod:`skyframe.core.sections_library` (SI units).
        """
        from skyframe.core.sections_library import library_section
        return library_section(name, material)

    def to_dict(self) -> dict:
        return asdict(self)


SHELL_LAYER_KINDS = ("concrete", "steel")


@dataclass
class ShellSection:
    """Shell section (maps to OpenSees ElasticMembranePlateSection).

    ``mod`` (v0.4) is a single stiffness modifier (> 0) applied by scaling
    the section modulus E.  ElasticMembranePlateSection has ONE modulus, so
    membrane and flexural stiffness scale together; independent
    membrane/flexural modifiers are deliberately not offered in v0.4 (an
    exact split is impossible with this section type).

    ``layered`` (v0.22) — nonlinear layered-shell definition (walls)::

        {"layers": [{"t": m, "material": name,
                     "kind": "concrete" | "steel",
                     "angle": 0 | 90}, ...]}      # angle: steel only

    Layers are stacked through the thickness (order irrelevant for
    membrane response).  In a NONLINEAR analysis (pushover / nonlinear
    time history) the engine builds an OpenSees ``LayeredShell`` section
    from the layers (see CONTRACT v0.22 for the exact material laws);
    every LINEAR analysis keeps the elastic section with the SUMMED layer
    thickness ``sum(t)`` (``thickness`` is ignored while ``layered`` is
    set).  ``layered is None`` keeps the exact pre-v0.22 behavior.
    """

    name: str
    material: str
    thickness: float  # m
    mod: float = 1.0  # v0.4 stiffness modifier (scales E)
    layered: Optional[dict] = None  # v0.22 nonlinear layered shell

    @property
    def total_thickness(self) -> float:
        """Elastic thickness: summed layer t when layered, else thickness."""
        if self.layered:
            return sum(float(la["t"]) for la in self.layered["layers"])
        return self.thickness

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
GRID_KINDS = ("orthogonal", "radial")
_GRID_CIRCLE_SEGMENTS = 72          # polyline resolution for radial circles


@dataclass
class GridSystem:
    """A named drafting grid (v0.14).

    Grids are DRAFTING AIDS ONLY: members store absolute global coordinates,
    so grids never touch the analysis engine.  A grid may be placed at an
    ``origin`` and ``rotation`` (degrees, CCW about the origin) and comes in
    two kinds:

    * ``kind == "orthogonal"`` (default) — ``x_lines``/``y_lines`` are the
      grid-line coordinates in the grid's LOCAL frame (metres); labels are
      ``A, B, ...`` across x and ``1, 2, ...`` up y (unchanged from v0.1).
    * ``kind == "radial"`` — ``radii`` are concentric-circle radii (m) from
      the origin and ``theta_deg`` are spoke angles (deg); the circles are
      labelled ``R1, R2, ...`` and the spokes by angle.

    The ``*_global`` helpers return drawable geometry / snap points already
    transformed into the GLOBAL frame; ``snap`` returns the nearest
    intersection within a tolerance.
    """

    x_lines: List[float] = field(default_factory=list)
    y_lines: List[float] = field(default_factory=list)
    name: str = "G1"
    origin: Tuple[float, float] = (0.0, 0.0)
    rotation: float = 0.0                       # degrees, CCW about origin
    kind: str = "orthogonal"                    # "orthogonal" | "radial"
    radii: List[float] = field(default_factory=list)      # radial only
    theta_deg: List[float] = field(default_factory=list)  # radial only

    # ---------------- labels ----------------
    @property
    def x_labels(self) -> List[str]:
        return [chr(ord("A") + i) for i in range(len(self.x_lines))]

    @property
    def y_labels(self) -> List[str]:
        return [str(i + 1) for i in range(len(self.y_lines))]

    # ---------------- geometry ----------------
    def to_global(self, lx: float, ly: float) -> Tuple[float, float]:
        """Map a LOCAL grid point (lx, ly) to GLOBAL coords.

        Rotate CCW by ``rotation`` about the grid origin, then translate:
        ``(ox + lx*c - ly*s, oy + lx*s + ly*c)`` with c/s = cos/sin(rotation).
        """
        a = math.radians(self.rotation)
        c, s = math.cos(a), math.sin(a)
        ox, oy = float(self.origin[0]), float(self.origin[1])
        return (ox + lx * c - ly * s, oy + lx * s + ly * c)

    def lines_global(self) -> List[dict]:
        """Drawable line segments in GLOBAL coords.

        Each entry is ``{"label": str, "points": [[x, y], ...]}`` — two points
        for a straight grid line / spoke, a closed polyline for a radial
        circle.
        """
        if self.kind == "radial":
            return self._radial_lines()
        lines: List[dict] = []
        xs, ys = self.x_lines, self.y_lines
        ymin = ys[0] if ys else 0.0
        ymax = ys[-1] if ys else 0.0
        xmin = xs[0] if xs else 0.0
        xmax = xs[-1] if xs else 0.0
        for lab, xv in zip(self.x_labels, xs):
            p1, p2 = self.to_global(xv, ymin), self.to_global(xv, ymax)
            lines.append({"label": lab, "points": [list(p1), list(p2)]})
        for lab, yv in zip(self.y_labels, ys):
            p1, p2 = self.to_global(xmin, yv), self.to_global(xmax, yv)
            lines.append({"label": lab, "points": [list(p1), list(p2)]})
        return lines

    def _radial_lines(self) -> List[dict]:
        lines: List[dict] = []
        n = _GRID_CIRCLE_SEGMENTS
        rmax = max(self.radii) if self.radii else 0.0
        for ri, r in enumerate(self.radii):
            pts = []
            for k in range(n + 1):
                ang = 2.0 * math.pi * k / n
                pts.append(list(self.to_global(r * math.cos(ang),
                                               r * math.sin(ang))))
            lines.append({"label": f"R{ri + 1}", "points": pts})
        for t in self.theta_deg:
            a = math.radians(t)
            p1 = self.to_global(0.0, 0.0)
            p2 = self.to_global(rmax * math.cos(a), rmax * math.sin(a))
            lines.append({"label": f"{t:g}°",
                          "points": [list(p1), list(p2)]})
        return lines

    def intersections_global(self) -> List[dict]:
        """Labelled snap points in GLOBAL coords.

        Orthogonal: every x-line x y-line crossing, label ``"A-1"``.
        Radial: every circle x spoke crossing, label ``"R1-30°"``.
        Each entry is ``{"label": str, "point": [x, y]}``.
        """
        out: List[dict] = []
        if self.kind == "radial":
            for ri, r in enumerate(self.radii):
                for t in self.theta_deg:
                    a = math.radians(t)
                    p = self.to_global(r * math.cos(a), r * math.sin(a))
                    out.append({"label": f"R{ri + 1}-{t:g}°",
                                "point": list(p)})
            return out
        for xlab, xv in zip(self.x_labels, self.x_lines):
            for ylab, yv in zip(self.y_labels, self.y_lines):
                p = self.to_global(xv, yv)
                out.append({"label": f"{xlab}-{ylab}", "point": list(p)})
        return out

    def snap(self, px: float, py: float, tol: float) -> Optional[dict]:
        """Nearest intersection ``{"label", "point"}`` within ``tol`` or None."""
        best = None
        best_d = None
        for it in self.intersections_global():
            gx, gy = it["point"]
            d = math.hypot(gx - px, gy - py)
            if best_d is None or d < best_d:
                best_d, best = d, it
        if best is not None and best_d <= tol:
            return best
        return None

    # ---------------- (de)serialisation ----------------
    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind,
                "origin": [float(self.origin[0]), float(self.origin[1])],
                "rotation": float(self.rotation),
                "x_lines": [float(x) for x in self.x_lines],
                "y_lines": [float(y) for y in self.y_lines],
                "x_labels": self.x_labels, "y_labels": self.y_labels,
                "radii": [float(r) for r in self.radii],
                "theta_deg": [float(t) for t in self.theta_deg]}

    @classmethod
    def from_dict(cls, d: dict) -> "GridSystem":
        orig = d.get("origin", (0.0, 0.0))
        return cls(
            x_lines=[float(x) for x in (d.get("x_lines") or [])],
            y_lines=[float(y) for y in (d.get("y_lines") or [])],
            name=str(d.get("name", "G1")),
            origin=(float(orig[0]), float(orig[1])),
            rotation=float(d.get("rotation", 0.0)),
            kind=str(d.get("kind", "orthogonal")),
            radii=[float(r) for r in (d.get("radii") or [])],
            theta_deg=[float(t) for t in (d.get("theta_deg") or [])])


@dataclass
class Story:
    name: str
    height: float           # m (story height below this level's floor? no: height of this story)
    elevation: float = 0.0  # m, computed = top-of-story elevation

    def to_dict(self) -> dict:
        return asdict(self)


AXIAL_LIMITS = ("both", "tension", "compression")

# v0.19 automatic plastic-hinge assignment (see FrameMember.hinges);
# v0.21 adds "fiber_pmm" (HingeRadau fiber P-M-M hinge in asce41 pushovers)
MEMBER_HINGE_OPTIONS = ("none", "auto_m3", "fiber_pmm")


@dataclass
class FrameMember:
    """A frame element between two 3D points (already snapped to grid/story)."""

    uid: str
    kind: str                       # "column" | "beam" | "brace"
    section: str
    pi: Tuple[float, float, float]  # (x, y, z) end i
    pj: Tuple[float, float, float]  # (x, y, z) end j
    story: str = ""                 # owning story name (reporting)
    releases: str = ""              # comma-sep tokens "Mi"/"Mj": moment release
    #                                 about BOTH local y & z at that end
    angle: float = 0.0              # v0.4: local-axis rotation about the
    #                                 member axis (degrees, right-hand about
    #                                 local +x); rotates the default local
    #                                 y/z triad (column orientation angle)
    # v0.9 rigid-end offsets (ETABS-style rigid-zone). ``rigid_i``/``rigid_j``
    # are the rigid-zone LENGTHS (m) at the i/j ends (e.g. half the supporting
    # column depth); ``rigid_factor`` (0..1) is the fraction of that offset
    # taken as rigid (ETABS rigid-zone factor).  The engine models the elastic
    # element over the CLEAR span L - rigid_factor*(rigid_i + rigid_j) with
    # very-stiff rigid links to the real end nodes at pi/pj.
    rigid_i: float = 0.0
    rigid_j: float = 0.0
    rigid_factor: float = 1.0
    # v0.11 Winkler elastic foundation (beam/mat on soil): when both are > 0
    # the member rests on a Winkler bed of vertical (global -Z) grounded
    # springs.  ``foundation_ks`` is the subgrade modulus (kN/m^3) and
    # ``foundation_width`` the bearing width b (m); the distributed line
    # spring modulus is ``k_line = foundation_ks * foundation_width`` (kN/m per
    # metre of length).  The engine discretizes the member and lumps a grounded
    # Z spring ``k_line * tributary_length`` at each node.
    foundation_ks: float = 0.0
    foundation_width: float = 0.0
    # v0.12 axial force limit (braces, cables, ties): a member with
    # ``axial_limit != "both"`` is modelled as a 2-force axial-only Truss that
    # carries load in ONE direction only — "tension" (tension-only: a cable /
    # tie that goes slack in compression) or "compression" (compression-only:
    # a strut that releases in tension).  "both" (default) is the ordinary
    # bending frame element.  A case that contains any non-"both" member is
    # solved NONLINEARLY (Newton).
    axial_limit: str = "both"
    # v0.19 automatic plastic hinges (ASCE 41-17): "none" (default,
    # elastic — the exact pre-v0.19 behavior) or "auto_m3" — a pushover
    # case with hinges == "asce41" inserts trilinear M3 hinge springs at
    # BOTH ends with backbones from skyframe.design.hinges (steel Table
    # 9-7.1 / concrete-beam Table 10-7).  Ignored by every other analysis.
    # v0.21 adds "fiber_pmm": in an asce41 pushover the member becomes a
    # forceBeamColumn with HingeRadau FIBER hinge sections at both ends
    # (lp = 0.5*h; full axial-biaxial interaction from the fibers);
    # acceptance criteria stay the v0.19 table rotations (CONTRACT v0.21).
    hinges: str = "none"

    @property
    def length(self) -> float:
        return math.dist(self.pi, self.pj)

    @property
    def foundation_k_line(self) -> float:
        """Distributed vertical spring modulus (kN/m per m), 0 when inactive."""
        if self.foundation_ks > 0.0 and self.foundation_width > 0.0:
            return self.foundation_ks * self.foundation_width
        return 0.0

    @property
    def rigid_offset_i(self) -> float:
        """Effective rigid-zone length at end i (rigid_factor * rigid_i)."""
        return self.rigid_factor * self.rigid_i

    @property
    def rigid_offset_j(self) -> float:
        """Effective rigid-zone length at end j (rigid_factor * rigid_j)."""
        return self.rigid_factor * self.rigid_j

    def release_tokens(self) -> set:
        """Parsed set of release tokens (subset of {"Mi", "Mj"})."""
        return {t.strip() for t in self.releases.split(",") if t.strip()}

    def to_dict(self) -> dict:
        return {"uid": self.uid, "kind": self.kind, "section": self.section,
                "pi": list(self.pi), "pj": list(self.pj), "story": self.story,
                "releases": self.releases, "angle": self.angle,
                "rigid_i": self.rigid_i, "rigid_j": self.rigid_j,
                "rigid_factor": self.rigid_factor,
                "foundation_ks": self.foundation_ks,
                "foundation_width": self.foundation_width,
                "axial_limit": self.axial_limit,
                "hinges": self.hinges,
                "length": self.length}


@dataclass
class Opening:
    """Rectangular opening in a shell region (v0.5).

    Bounds are FRACTIONS (0..1) of the region's parametric edge directions:
    ``u`` runs along the corner 0 -> 1 edge, ``v`` along corner 0 -> 3.
    Must satisfy ``0 <= u0 < u1 <= 1`` and ``0 <= v0 < v1 <= 1``.
    """

    u0: float
    v0: float
    u1: float
    v1: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ShellRegion:
    """Planar 4-corner quad region: a wall or a slab.

    ``behavior == "shell"``    — meshed into ShellMITC4 finite elements.
    ``behavior == "membrane"`` — slabs only: never meshed; its area loads are
    distributed to the edge beams by two-way (45 degree) tributary areas.
    Corners must be planar and listed counter-clockwise.

    ``openings`` (v0.5): rectangular holes in region-parametric coordinates.
    Shell behavior: the mesher snaps each opening to the nearest structured
    mesh lines and omits the elements inside (area loads then act on the net
    meshed area exactly).  Membrane behavior: the distributed load total is
    reduced uniformly by the opening area ratio (documented approximation —
    the two-way tributary SHAPE is unchanged; a warning is emitted).
    """

    uid: str
    kind: str                              # "wall" | "slab"
    behavior: str                          # "shell" | "membrane"
    section: str                           # ShellSection name (membrane: unused)
    corners: List[Tuple[float, float, float]]
    mesh_size: float = 1.0                 # m, target FE size (shell behavior)
    story: str = ""
    openings: List[Opening] = field(default_factory=list)   # v0.5
    pier: str = ""                         # v0.15 pier label (walls): a
    #   labeled wall reports per-story pier design forces P/V/M; walls with
    #   the same label are summed into one pier.  "" = no pier output
    #   (unless BuildingModel.auto_pier_walls labels the wall with its uid).
    area_spring: Optional[dict] = None     # v0.22 grounded area springs:
    #   {"kz": kN/m per m^2 (> 0), "compression_only": bool (default
    #   False)} — every mesh node of the region gets a grounded vertical
    #   spring of stiffness kz x its tributary area (compression-only via
    #   the v0.12 Elastic-with-Eneg machinery).  Shell behavior only.

    @property
    def area(self) -> float:
        return _polygon_area3d([tuple(c) for c in self.corners])

    def map_uv(self, u: float, v: float) -> Tuple[float, float, float]:
        """Bilinear map from parametric (u, v) in [0,1]^2 to 3D coordinates."""
        c = [tuple(map(float, p)) for p in self.corners]
        return tuple(
            (1 - u) * (1 - v) * c[0][k] + u * (1 - v) * c[1][k]
            + u * v * c[2][k] + (1 - u) * v * c[3][k] for k in range(3))

    @property
    def opening_area(self) -> float:
        """Exact total opening area (m^2).

        The bilinear map sends a parametric rectangle to a planar
        straight-edged quadrilateral (parametric lines map to straight
        segments), so each opening's area is the shoelace area of its four
        mapped corners.  Openings are validated non-overlapping, so the sum
        is exact.
        """
        total = 0.0
        for op in self.openings:
            pts = [self.map_uv(op.u0, op.v0), self.map_uv(op.u1, op.v0),
                   self.map_uv(op.u1, op.v1), self.map_uv(op.u0, op.v1)]
            total += _polygon_area3d(pts)
        return total

    @property
    def net_area(self) -> float:
        """Gross area minus the exact opening area (m^2)."""
        return self.area - self.opening_area

    def to_dict(self) -> dict:
        return {"uid": self.uid, "kind": self.kind, "behavior": self.behavior,
                "section": self.section,
                "corners": [list(c) for c in self.corners],
                "mesh_size": self.mesh_size, "story": self.story,
                "openings": [op.to_dict() for op in self.openings],
                "pier": self.pier,
                "area_spring": (dict(self.area_spring)
                                if self.area_spring else None)}


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
@dataclass
class MemberUDL:
    """Uniform gravity load on a member (kN/m, positive downward).

    Legacy v0.1 load; kept as a compatible alias for
    ``MemberLoad(kind="udl", direction="gravity", a=0, b=1)``.
    """
    member_uid: str
    w: float


MEMBER_LOAD_KINDS = ("udl", "point", "trapezoid")
MEMBER_LOAD_DIRECTIONS = ("gravity", "local_y", "global_x", "global_y",
                          "global_z")


@dataclass
class MemberLoad:
    """General frame-member load.

    ``kind``:
      * ``"udl"``       — uniform ``w`` (kN/m) over the fraction span [a, b]
      * ``"point"``     — concentrated ``w`` (kN) at fraction ``a``
      * ``"trapezoid"`` — linear from ``w`` (at ``a``) to ``w2`` (at ``b``), kN/m

    ``a``/``b`` are FRACTIONS (0..1) of the member length.

    ``direction``:
      * ``"gravity"``  — global -Z, positive value acts downward
      * ``"local_y"``  — along the member's local +y axis
      * ``"global_x"|"global_y"|"global_z"`` — along that global axis
    """

    member_uid: str
    kind: str = "udl"
    w: float = 0.0
    w2: float = 0.0
    a: float = 0.0
    b: float = 1.0
    direction: str = "gravity"


@dataclass
class AreaLoad:
    """Uniform area load on a shell region (kPa, positive DOWNWARD)."""
    region_uid: str
    q: float


@dataclass
class NodalLoad:
    """Load at a point (kN / kN*m, global axes)."""
    point: Tuple[float, float, float]
    fx: float = 0.0
    fy: float = 0.0
    fz: float = 0.0


@dataclass
class StoryForce:
    """Lateral force applied at a story diaphragm (kN, global axes)."""
    story: str
    fx: float = 0.0
    fy: float = 0.0


@dataclass
class PointSupport:
    """Explicit support at a point; restraints are (ux, uy, uz, rx, ry, rz).

    When a model defines explicit supports they replace the automatic
    base-level fixity."""
    point: Tuple[float, float, float]
    restraints: Tuple[int, int, int, int, int, int]

    def to_dict(self) -> dict:
        return {"point": list(self.point), "restraints": list(self.restraints)}


@dataclass
class SpringSupport:
    """Grounded point spring (foundation spring) at a point (v0.8).

    ``stiffness`` = [kx, ky, kz, krx, kry, krz] (kN/m for translations,
    kN*m/rad for rotations), acting on the GLOBAL axes.  The engine adds a
    co-located fully-fixed ground node and a zeroLength element between it
    and the real (structural) node, with one elastic uniaxial material per
    NON-ZERO stiffness entry.  The real node must be left FREE in the sprung
    DOFs (the spring provides the restraint); any base fixity on those DOFs
    is cleared.  The spring force ``-k*disp`` is reported in the case
    reactions under the real node tag, and the real node counts as a
    support.
    """

    point: Tuple[float, float, float]
    stiffness: List[float]                      # 6 entries, >= 0

    def to_dict(self) -> dict:
        return {"point": list(self.point),
                "stiffness": [float(k) for k in self.stiffness]}


@dataclass
class LineSpring:
    """Grounded distributed line spring (v0.22) — e.g. a strip footing or
    a wall base on grade.

    ``kz`` (and optional ``kx``/``ky``) are stiffnesses PER METER of line
    (kN/m per m), acting on the GLOBAL axes.  The engine discretizes the
    spring over the existing FE nodes lying on the p1 -> p2 segment
    (shell mesh nodes, frame split nodes): each node gets a grounded
    zeroLength spring of ``k x tributary length``, exactly the v0.11
    Winkler-member pattern.  Tributary lengths cover the FULL segment:
    node i owns [mid(t_{i-1}, t_i), mid(t_i, t_{i+1})] with the first
    interval starting at p1 and the last ending at p2, so
    ``sum(trib) == |p2 - p1|`` exactly.  It is an error if no FE node
    lies on the segment.

    ``compression_only`` applies to the VERTICAL spring only: kz resists
    settlement (node moving -Z) at full stiffness and uplift at the
    residual ``AXIAL_ONLY_RATIO`` (1e-6) fraction — the v0.12
    tension/compression-only machinery (``Elastic`` with ``Eneg``); the
    tiny residual keeps the system regular.  ``kx``/``ky`` are always
    linear.
    """

    p1: Tuple[float, float, float]
    p2: Tuple[float, float, float]
    kz: float = 0.0                        # kN/m per m
    kx: float = 0.0
    ky: float = 0.0
    compression_only: bool = False

    def to_dict(self) -> dict:
        return {"p1": list(self.p1), "p2": list(self.p2),
                "kz": self.kz, "kx": self.kx, "ky": self.ky,
                "compression_only": self.compression_only}


@dataclass
class ThermalLoad:
    """Uniform temperature change on a frame member (v0.8).

    ``dT`` is the temperature change in degrees Celsius (positive = rise).
    The engine turns it into a self-equilibrated axial fixed-end force
    ``N = E*A*alpha*dT`` (compression positive when restrained) applied
    through the exact member-load path; ``alpha`` is the model-wide thermal
    expansion coefficient :attr:`BuildingModel.thermal_alpha`.
    """

    member_uid: str
    dT: float = 0.0


@dataclass
class NodalMass:
    """Explicit lumped mass at a point (tonnes on ux/uy/uz)."""
    point: Tuple[float, float, float]
    mx: float = 0.0
    my: float = 0.0
    mz: float = 0.0

    def to_dict(self) -> dict:
        return {"point": list(self.point), "mx": self.mx, "my": self.my, "mz": self.mz}


@dataclass
class LoadPattern:
    """Named set of loads (like an ETABS load pattern)."""

    name: str
    kind: str = "other"  # "dead" | "live" | "quake" | "wind" | "other"
    member_udls: List[MemberUDL] = field(default_factory=list)
    nodal_loads: List[NodalLoad] = field(default_factory=list)
    story_forces: List[StoryForce] = field(default_factory=list)
    member_loads: List[MemberLoad] = field(default_factory=list)
    area_loads: List[AreaLoad] = field(default_factory=list)
    # v0.8 temperature loads: uniform member temperature changes (deg C).
    thermal_loads: List["ThermalLoad"] = field(default_factory=list)
    # v0.8 accidental torsion (ASCE 7-16 §12.8.4.2): when True, story forces
    # applied to a rigid-diaphragm master ALSO apply a story torque
    # Mz = F * ecc * B_perp (fx -> ecc*Ly, fy -> ecc*Lx) at the master's rz
    # dof.  v0.8 applies +ecc only (positive torsion); the +/- enveloping is
    # a combos-level concern.
    accidental_torsion: bool = False
    ecc: float = 0.05
    # v0.7 self-weight: ETABS-style — the pattern applies each material's real
    # self-weight (unit_weight, kN/m^3) scaled by this factor.  0.0 (default)
    # means the pattern carries no self-weight (pre-v0.7 behavior).  The
    # engine turns it into an exact global -Z member load (A*unit_weight,
    # kN/m) on every frame member and an area load (thickness*unit_weight,
    # kN/m^2) on every shell region.
    self_weight_factor: float = 0.0

    def all_member_loads(self) -> List[MemberLoad]:
        """member_loads plus legacy member_udls expressed as MemberLoads."""
        legacy = [MemberLoad(u.member_uid, kind="udl", w=u.w,
                             direction="gravity") for u in self.member_udls]
        return legacy + list(self.member_loads)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind,
            "member_udls": [asdict(u) for u in self.member_udls],
            "nodal_loads": [asdict(n) for n in self.nodal_loads],
            "story_forces": [asdict(s) for s in self.story_forces],
            "member_loads": [asdict(m) for m in self.member_loads],
            "area_loads": [asdict(a) for a in self.area_loads],
            "thermal_loads": [asdict(t) for t in self.thermal_loads],
            "accidental_torsion": self.accidental_torsion,
            "ecc": self.ecc,
            "self_weight_factor": self.self_weight_factor,
        }


@dataclass
class LoadCase:
    """Static case: scaled sum of load patterns.

    ``pdelta`` (v0.3): solve the case on the P-Delta geometric stiffness
    (OpenSees ``geomTransf('PDelta', ...)`` on every frame member).  The
    gravity state that generates the geometric stiffness is defined by
    ``pdelta_gravity`` (pattern -> factor); when ``None`` the case's own
    patterns ARE the gravity state and the case is solved in a single
    nonlinear stage.  Otherwise the engine applies the gravity state first,
    holds it constant, then applies the case's own patterns and reports the
    case's incremental (gravity-stiffened) response.
    """

    name: str
    patterns: Dict[str, float]  # pattern name -> scale factor
    pdelta: bool = False
    pdelta_gravity: Optional[Dict[str, float]] = None

    def to_dict(self) -> dict:
        return asdict(self)


RS_DIRECTIONS = ("X", "Y")
RS_COMBO_METHODS = ("CQC", "SRSS")


@dataclass
class ResponseSpectrumCase:
    """Response-spectrum analysis case (v0.3).

    ``spectrum`` is a list of ``[T, Sa]`` points, T in seconds, Sa in units
    of g.  Sa at a modal period is LINEARLY interpolated (clamped to the end
    values outside the tabulated range).  ``num_modes == 0`` means "use all
    modes the modal analysis computes".  Modal responses are combined with
    CQC (constant damping ratio ``damping``) or SRSS; all combined results
    are positive envelopes.  ``scale`` multiplies Sa.

    v0.13: when ``function`` names a :class:`SpectrumFunction` in
    ``model.spectrum_functions``, that function's ``points`` REPLACE the
    inline ``spectrum`` (the engine resolves it; a missing name is an
    error).  With ``function == ""`` the inline ``spectrum`` is used exactly
    as before (backward compatible).
    """

    name: str
    direction: str                       # "X" | "Y"
    spectrum: List[List[float]]          # [[T, Sa(g)], ...]
    num_modes: int = 0                   # 0 = all computed modes
    combo_method: str = "CQC"            # "CQC" | "SRSS"
    damping: float = 0.05
    scale: float = 1.0
    function: str = ""                   # v0.13: named spectrum_functions entry

    def to_dict(self) -> dict:
        return {"name": self.name, "direction": self.direction,
                "spectrum": [[float(t), float(sa)] for t, sa in self.spectrum],
                "num_modes": self.num_modes,
                "combo_method": self.combo_method,
                "damping": self.damping, "scale": self.scale,
                "function": self.function}


TH_DIRECTIONS = ("X", "Y")


@dataclass
class TimeHistoryCase:
    """Time-history case (v0.4 linear, v0.6 nonlinear): uniform ground accel.

    ``accel`` is the ground-acceleration record in m/s^2 sampled at constant
    spacing ``dt`` (the sample at index k applies at t = k*dt); ``scale``
    multiplies the record.  The engine integrates the model with Newmark
    constant-average acceleration (gamma=1/2, beta=1/4, unconditionally
    stable) at the record dt, with Rayleigh damping fitted to ratio
    ``damping`` at modes 1 and min(3, n_modes) of the INITIAL elastic model.

    v0.6 nonlinear fields (reuse the v0.5 pushover hinge idealization —
    see :class:`PushoverCase` for ``hinges``/``My``/``default_My``/
    ``hardening`` semantics, identical here):

    * ``nonlinear`` — insert Steel01 zeroLength rotational hinges exactly
      like a pushover case and integrate with Newton (fallback
      NewtonLineSearch) instead of the linear algorithm;
    * ``gravity`` — pattern -> factor combination applied statically FIRST
      and held constant (``loadConst``); the reported time series are the
      response PAST the gravity state;
    * ``My`` / ``default_My`` / ``hardening`` / ``hinges`` — the hinge plan
      (members with neither ``My`` entry nor ``default_My`` stay elastic).
    """

    name: str
    direction: str                 # "X" | "Y"
    accel: List[float]             # m/s^2, sampled at dt
    dt: float                      # s
    damping: float = 0.05          # Rayleigh target damping ratio
    scale: float = 1.0             # multiplies accel
    nonlinear: bool = False                                  # v0.6
    gravity: Dict[str, float] = field(default_factory=dict)  # v0.6
    hinges: str = "column_base"                              # v0.6
    My: Dict[str, float] = field(default_factory=dict)       # v0.6, kN*m
    default_My: Optional[float] = None                       # v0.6
    hardening: float = 0.02                                  # v0.6
    function: str = ""              # v0.13: named th_functions entry

    def to_dict(self) -> dict:
        return {"name": self.name, "direction": self.direction,
                "accel": [float(a) for a in self.accel],
                "dt": self.dt, "damping": self.damping, "scale": self.scale,
                "nonlinear": self.nonlinear, "gravity": dict(self.gravity),
                "hinges": self.hinges, "My": dict(self.My),
                "default_My": self.default_My, "hardening": self.hardening,
                "function": self.function}


PUSHOVER_DIRECTIONS = ("X", "Y")
PUSHOVER_HINGE_MODES = ("column_base", "all_ends")


@dataclass
class PushoverCase:
    """Nonlinear static pushover case (v0.5).

    Gravity (``gravity``: pattern -> factor) is applied first with Newton
    and held constant (``loadConst``); the lateral push is then solved
    displacement-controlled on the roof control DOF (top-story diaphragm
    master, else the topmost structural node) up to
    ``target_drift * roof elevation`` in ``steps`` equal increments.

    Hinge model (documented idealization): a zeroLength rotational spring
    (Steel01 bilinear about BOTH member bending axes, stiff elastic in
    torsion; translations tied with equalDOF) is inserted between the
    member end and a duplicated node.  ``My`` maps member uid -> hinge
    yield moment (kN*m); ``default_My`` applies to every eligible member
    without an entry; members with neither stay fully elastic (no hinge is
    inserted).  ``hinges == "column_base"`` puts the hinge at the LOWER end
    of each eligible column; ``"all_ends"`` at both ends of every eligible
    member.  Spring elastic stiffness k_theta = n * 6EI/L with n = 10 (the
    standard stiff-hinge idealization: the elastic series softening is the
    factor n/(n+1) on the member-end rotational stiffness 6EI/L); the
    Steel01 hardening ratio is b = hardening / (n + 1 - hardening*n), which
    makes the member-end SERIES post-yield/elastic stiffness ratio exactly
    ``hardening``.
    """

    name: str
    direction: str                              # "X" | "Y"
    gravity: Dict[str, float] = field(default_factory=dict)
    target_drift: float = 0.02                  # roof drift ratio
    steps: int = 100
    hinges: str = "column_base"       # "column_base" | "all_ends" | "asce41"
    My: Dict[str, float] = field(default_factory=dict)   # uid -> kN*m
    default_My: Optional[float] = None          # kN*m, eligible members
    hardening: float = 0.02                     # post-yield stiffness ratio
    # v0.19 "asce41" mode: My/default_My/hardening are IGNORED; hinges go
    # at BOTH ends of every member with FrameMember.hinges == "auto_m3",
    # with trilinear ASCE 41-17 backbones from skyframe.design.hinges.
    # hinge_params tunes the generator (keys: expected_factor — Fye/Fy,
    # default 1.1; rho / rho_prime — concrete steel ratios, defaults
    # 0.01 / 0.0; fy_bar — rebar fy in kPa, default 420000).
    hinge_params: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "direction": self.direction,
                "gravity": dict(self.gravity),
                "target_drift": self.target_drift, "steps": self.steps,
                "hinges": self.hinges, "My": dict(self.My),
                "default_My": self.default_My, "hardening": self.hardening,
                "hinge_params": dict(self.hinge_params)}


STAGED_MODES = ("per_story",)


@dataclass
class StagedCase:
    """Staged-construction (sequential gravity) case (v0.6).

    ``stages == "per_story"`` (the only v0.6 mode): the engine analyses the
    building story by story with the REBUILD-AND-ACCUMULATE method — at
    stage k a fresh model containing only stories 1..k (members, shells,
    supports, diaphragms, links) is solved under ONLY story k's gravity
    loads from ``pattern``; per-member forces and per-node incremental
    displacements are accumulated across stages (each stage's increments
    are measured in that stage's fresh geometry — geometry updating is
    ignored, the standard linear staged-analysis assumption).  Members not
    yet built in a stage simply receive no increment that stage.

    ``include_live`` (pattern -> factor) is applied at the END on the full
    structure in one unstaged increment.  The engine also solves the
    one-shot application of the same total loads internally and reports a
    comparison (see CONTRACT v0.6).
    """

    name: str
    pattern: str = "DEAD"
    stages: str = "per_story"
    include_live: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "pattern": self.pattern,
                "stages": self.stages,
                "include_live": dict(self.include_live)}


@dataclass
class BucklingCase:
    """Linear (eigenvalue) buckling case (v0.10).

    ``gravity`` maps load-pattern name -> factor: the reference gravity load
    whose critical multiplier is sought.  The engine's self-contained numpy
    buckling solver (:mod:`skyframe.core.buckling`) assembles the elastic and
    geometric stiffness of the frame, extracts member axial forces under this
    reference load, and returns the ``num_modes`` smallest positive load
    factors ``lambda`` (the critical multipliers) and their mode shapes.
    """

    name: str
    gravity: Dict[str, float] = field(default_factory=dict)
    num_modes: int = 6

    def to_dict(self) -> dict:
        return {"name": self.name, "gravity": dict(self.gravity),
                "num_modes": self.num_modes}


# v0.10 response-spectrum directional combination methods (ASCE 7 §12.5)
RS_DIRECTIONAL_METHODS = ("100_30", "SRSS")

# v0.15 advanced link types (seismic protection devices).  ``elastic`` is the
# original v0.5 6-DOF spring; the others are device links driven by
# ``LinkMember.params``:
#   damper   {cd, alpha=1.0, k=DAMPER_DEFAULT_K}   axial Maxwell viscous
#            damper along the link axis (OpenSees ViscousDamper: series
#            spring k + dashpot cd*sign(v)*|v|^alpha).  Carries NO static
#            force; adds damping in time-history cases.
#   gap      {k, gap}    compression-only contact along the link axis that
#            engages once the pair CLOSES by more than ``gap`` (m); force
#            k*(closing - gap) thereafter (ElasticPPGap, huge yield).
#   hook     {k, slack}  the tension mirror: engages after the pair OPENS
#            by more than ``slack`` (m).
#   isolator {k1, k2, Fy, kv=ISOLATOR_DEFAULT_KV}  base-isolation bearing:
#            bilinear (Steel01: initial k1, yield Fy, post-yield k2) shear
#            in BOTH horizontal directions, elastic vertical kv, rotations
#            free.  The link axis must be vertical (or zero length).
# v0.21 device library II (axis vertical or zero length, like isolator):
#   fp_isolator {R, mu, k_init=FP_DEFAULT_KINIT, kv=ISOLATOR_DEFAULT_KV,
#            P0?}  single friction pendulum: OpenSees singleFPBearing with
#            a Coulomb friction model (mu) and effective radius R (m) —
#            post-slip lateral force F = mu*N + N*d/R with N the
#            instantaneous axial load from the analysis; k_init = stick
#            (pre-slip) stiffness; elastic vertical kv on -P.  ``P0`` is
#            an OPTIONAL informational expected axial load (kN, echoed for
#            UI/hand checks; the element always reads N from the model).
#   triple_fp {R1, R2, R3, mu1, mu2, mu3, d1, d2, d3, W,
#            uy=TFP_DEFAULT_UY, kv=ISOLATOR_DEFAULT_KV, kvt=kv,
#            minFv=TFP_DEFAULT_MINFV, tol=TFP_DEFAULT_TOL}  OpenSees
#            TripleFrictionPendulum: effective radii R1 (inner) / R2 / R3
#            (outer), friction mu1..mu3, displacement capacities d1..d3
#            (m), W = vertical load for initialization (kN); the fully-
#            sliding tangent is W/(R2 + R3).
#   multilinear {points: [[d1, F1], [d2, F2], ...], kv=ISOLATOR_DEFAULT_KV}
#            uniaxialMaterial MultiLinear backbone (d strictly increasing,
#            d1 > 0) applied to BOTH horizontal shear directions of a
#            twoNodeLink (vertical axis; elastic vertical kv).  ``points``
#            is the ONE non-scalar param value (nested [d, F] list).
LINK_TYPES = ("elastic", "damper", "gap", "hook", "isolator",
              "fp_isolator", "triple_fp", "multilinear")

# required / optional param keys per advanced link type
_LINK_PARAM_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "damper": (("cd",), ("alpha", "k")),
    "gap": (("k", "gap"), ()),
    "hook": (("k", "slack"), ()),
    "isolator": (("k1", "k2", "Fy"), ("kv",)),
    "fp_isolator": (("R", "mu"), ("k_init", "kv", "P0")),
    "triple_fp": (("R1", "R2", "R3", "mu1", "mu2", "mu3",
                   "d1", "d2", "d3", "W"),
                  ("uy", "kv", "kvt", "minFv", "tol")),
    "multilinear": (("points",), ("kv",)),
}

DAMPER_DEFAULT_ALPHA = 1.0     # velocity exponent (1 = linear dashpot)
DAMPER_DEFAULT_K = 1.0e6       # kN/m, Maxwell series spring ("rigid" spring)
ISOLATOR_DEFAULT_KV = 1.0e7    # kN/m, vertical bearing stiffness
FP_DEFAULT_KINIT = 1.0e5       # kN/m, FP stick (pre-slip) shear stiffness
TFP_DEFAULT_UY = 1.0e-3        # m, TFP yield displacement (element uy)
TFP_DEFAULT_MINFV = 0.1        # kN, TFP minimum vertical force bound
TFP_DEFAULT_TOL = 1.0e-5       # TFP internal iteration tolerance
#   (uy = 1e-3 / tol = 1e-5 verified stable on openseespy 3.7.1; the much
#   tighter uy = 1e-4 + tol = 1e-10 combination stalls the element's
#   internal iteration — probed empirically, hence these defaults)


@dataclass
class LinkMember:
    """Link (spring / device) element between two points (v0.5, v0.15).

    ``link_type == "elastic"`` (default, v0.5): ``stiffness`` = [kx, ky, kz,
    krx, kry, krz] (kN/m, kN*m/rad) acting on the GLOBAL axes.  Modeled as
    an OpenSees zeroLength element with one elastic uniaxial material per
    non-zero entry — a pure spring: the element length carries no rigid-arm
    moment transfer.  Endpoints merge with existing FE nodes within 1e-6; a
    node that connects ONLY to links gets its zero-stiffness DOFs auto-
    restrained (else the system would be singular).

    v0.15 advanced types (see :data:`LINK_TYPES`): ``stiffness`` is unused
    (kept for the serialised shape) and the device is defined by ``params``.
    damper/gap/hook act AXIALLY along the link axis and need a non-zero
    length; an isolator's axis must be vertical (or zero length).  A node
    connected ONLY to advanced-type links is treated as a grounded anchor
    (fully fixed, reported as a support).

    v0.21 device library II adds ``fp_isolator`` / ``triple_fp`` /
    ``multilinear`` (see :data:`LINK_TYPES`); every param value is a
    float EXCEPT ``multilinear``'s ``points`` (a nested ``[[d, F], ...]``
    list, serialised as-is).
    """

    uid: str
    pi: Tuple[float, float, float]
    pj: Tuple[float, float, float]
    stiffness: List[float]                      # 6 entries, >= 0
    link_type: str = "elastic"                  # v0.15, one of LINK_TYPES
    params: Dict[str, object] = field(default_factory=dict)  # v0.15/v0.21

    @property
    def length(self) -> float:
        return math.dist(self.pi, self.pj)

    @staticmethod
    def coerce_params(params) -> Dict[str, object]:
        """Float-coerce a params dict, keeping ``points`` as [[d, F], ...]."""
        out: Dict[str, object] = {}
        for k, v in (params or {}).items():
            if str(k) == "points":
                out["points"] = [[float(p[0]), float(p[1])] for p in v]
            else:
                out[str(k)] = float(v)
        return out

    def to_dict(self) -> dict:
        return {"uid": self.uid, "pi": list(self.pi), "pj": list(self.pj),
                "stiffness": [float(k) for k in self.stiffness],
                "link_type": self.link_type,
                "params": self.coerce_params(self.params)}


DIAPHRAGM_OPTIONS = ("rigid", "none")

# v0.17 panel-zone (beam-column joint) modeling assumption, ETABS-style:
#   "none"     — centerline modeling (default; pre-v0.17 behavior, unchanged)
#   "rigid"    — automatic rigid end zones at every interior beam-column
#                joint: columns get max_connecting_beam_h/2 at the joint end,
#                beams get max_connecting_column_h/2 (computed at build time
#                by the engine; user-set explicit rigid_i/rigid_j win per
#                member end; the model itself is never mutated)
#   "scissors" — elastic scissors panel-zone spring (Krawinkler/Charney
#                idealization): the joint node is duplicated, beams connect
#                to the duplicate, and a rotational spring
#                K_theta = G * d_c * d_b * t_p bridges the pair (t_p = column
#                section b as the panel thickness for rectangular sections)
PANEL_ZONE_OPTIONS = ("none", "rigid", "scissors")

COMBO_TYPES = ("add", "envelope")


@dataclass
class LoadCombo:
    """Combination of load cases.

    ``combo_type`` (v0.4):
      * ``"add"``      — linear result superposition (factors applied).
      * ``"envelope"`` — per-quantity min/max over the LISTED cases, each
        case scaled by its factor first.  Envelope combos may reference
        static load cases only (no RS/TH cases, no other combos).
    """

    name: str
    cases: Dict[str, float]  # case name -> factor
    combo_type: str = "add"  # "add" | "envelope"

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# v0.13 section cuts + named function library
# --------------------------------------------------------------------------- #
SECTION_CUT_AXES = ("x", "y", "z")


@dataclass
class SectionCut:
    """A force-integration plane across the structure (v0.13).

    The cut is the plane ``axis == coord`` (``axis`` one of "x"/"y"/"z").
    The engine sums the internal forces of every FRAME member that crosses
    the plane (member spans ``coord`` along ``axis``) into a single resultant
    {FX, FY, FZ, MX, MY, MZ} in GLOBAL axes, moments taken about the cut
    centroid.  The optional ``x_range``/``y_range``/``z_range`` bounding-box
    limits restrict the cut extent: a crossing is counted only when its
    crossing point falls inside every provided ``[lo, hi]`` range.

    Sign convention (documented, ETABS-style): the resultant is the internal
    force that the material on the NEGATIVE-coordinate side of the plane
    exerts on the material on the POSITIVE-coordinate side (for a horizontal
    ``z`` cut this is the force from below supporting everything above, so a
    gravity cut reports a POSITIVE ``FZ`` equal to the weight carried).
    """

    name: str
    axis: str                                    # "x" | "y" | "z"
    coord: float
    x_range: Optional[List[float]] = None        # [lo, hi] or None
    y_range: Optional[List[float]] = None
    z_range: Optional[List[float]] = None

    def to_dict(self) -> dict:
        def rng(r):
            return None if r is None else [float(r[0]), float(r[1])]
        return {"name": self.name, "axis": self.axis,
                "coord": float(self.coord),
                "x_range": rng(self.x_range), "y_range": rng(self.y_range),
                "z_range": rng(self.z_range)}


@dataclass
class SpectrumFunction:
    """A named, reusable response-spectrum function (v0.13).

    ``points`` is a list of ``[T, Sa(g)]`` pairs (same meaning as a
    :class:`ResponseSpectrumCase` ``spectrum``); a case that names this
    function uses these points in place of its own inline spectrum.
    ``damping`` records the spectrum's associated damping ratio (metadata;
    the CQC combination uses the CASE's own ``damping``).
    """

    name: str
    points: List[List[float]]
    damping: float = 0.05

    def to_dict(self) -> dict:
        return {"name": self.name,
                "points": [[float(t), float(sa)] for t, sa in self.points],
                "damping": float(self.damping)}


@dataclass
class TimeHistoryFunction:
    """A named, reusable ground-acceleration record (v0.13).

    ``values`` is the acceleration series (m/s^2) sampled at spacing ``dt``
    (sample k at t = k*dt); a :class:`TimeHistoryCase` that names this
    function uses these values/dt in place of its own inline ``accel``/``dt``
    (the case ``scale`` still multiplies the record).
    """

    name: str
    values: List[float]
    dt: float

    def to_dict(self) -> dict:
        return {"name": self.name,
                "values": [float(v) for v in self.values],
                "dt": float(self.dt)}


# --------------------------------------------------------------------------- #
# The building
# --------------------------------------------------------------------------- #
@dataclass
class BuildingModel:
    name: str = "Untitled Building"
    materials: Dict[str, Material] = field(default_factory=dict)
    sections: Dict[str, FrameSection] = field(default_factory=dict)
    # v0.21 Section Designer: name -> DesignerSection (arbitrary polygon
    # fiber sections).  Each designer section is MIRRORED by a FrameSection
    # of the same name in ``sections`` (kept in sync by
    # ``add_designer_section`` / ``remove_designer_section``) so it plugs
    # into the existing analysis pipeline unchanged.
    designer_sections: Dict[str, "DesignerSection"] = field(
        default_factory=dict)
    shell_sections: Dict[str, ShellSection] = field(default_factory=dict)
    grid: Optional[GridSystem] = None
    # v0.14 multiple named grid systems (rotated / radial). ``grid`` stays as
    # the PRIMARY/legacy grid (plan_extents, takedown labels); ``grid_systems``
    # holds the full list.  ``effective_grids()`` reconciles the two.
    grid_systems: List[GridSystem] = field(default_factory=list)
    stories: List[Story] = field(default_factory=list)        # bottom -> top
    members: List[FrameMember] = field(default_factory=list)
    shells: List[ShellRegion] = field(default_factory=list)
    base_fixity: str = "fixed"                                 # "fixed" | "pinned"
    supports: List[PointSupport] = field(default_factory=list)
    spring_supports: List[SpringSupport] = field(default_factory=list)  # v0.8
    line_springs: List[LineSpring] = field(default_factory=list)  # v0.22
    # v0.22 auto edge constraints (the ETABS "zipper"): when True, the
    # engine ties every HANGING NODE (a node lying strictly inside another
    # shell's element edge — mesh-mismatched shell/shell interfaces and
    # frame ends landing mid-edge) to the edge's end nodes with a stiff
    # tie-beam chain along the edge (see CONTRACT v0.22 for the exact
    # sizing).  False (default) keeps every pre-v0.22 result bit-identical.
    edge_constraints: bool = False
    thermal_alpha: float = 1.2e-5           # v0.8: /degC thermal expansion
    nodal_masses: List[NodalMass] = field(default_factory=list)
    rigid_diaphragms: bool = True
    # v0.5 diaphragm option: global default + per-story overrides.
    # Effective mode per story = story_diaphragm[story] if set, else
    # ("none" if not rigid_diaphragms else diaphragm) — the legacy boolean
    # rigid_diaphragms=False keeps meaning "no diaphragms anywhere".
    # Semi-rigid IS "none" + a meshed shell slab: the slab's real membrane
    # stiffness plays the diaphragm role (no fake constraint is offered).
    diaphragm: str = "rigid"                                   # "rigid" | "none"
    story_diaphragm: Dict[str, str] = field(default_factory=dict)
    links: List[LinkMember] = field(default_factory=list)     # v0.5
    story_masses: Dict[str, float] = field(default_factory=dict)   # tonne (explicit)
    mass_from_patterns: Dict[str, float] = field(default_factory=dict)  # pattern -> factor (legacy)
    mass_source: Dict[str, float] = field(default_factory=dict)  # v0.4: pattern -> factor
    patterns: Dict[str, LoadPattern] = field(default_factory=dict)
    cases: Dict[str, LoadCase] = field(default_factory=dict)
    combos: Dict[str, LoadCombo] = field(default_factory=dict)
    rs_cases: Dict[str, ResponseSpectrumCase] = field(default_factory=dict)
    th_cases: Dict[str, TimeHistoryCase] = field(default_factory=dict)  # v0.4
    pushover_cases: Dict[str, PushoverCase] = field(default_factory=dict)  # v0.5
    staged_cases: Dict[str, StagedCase] = field(default_factory=dict)  # v0.6
    buckling_cases: Dict[str, BucklingCase] = field(default_factory=dict)  # v0.10
    # v0.10 response-spectrum directional combinations (ASCE 7 §12.5):
    #   name -> {"name_x": <RS case>, "name_y": <RS case>, "method": ...}
    rs_combos: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # v0.13 force-integration section cuts + named RS/TH function library
    section_cuts: List[SectionCut] = field(default_factory=list)
    spectrum_functions: Dict[str, SpectrumFunction] = field(
        default_factory=dict)
    th_functions: Dict[str, TimeHistoryFunction] = field(default_factory=dict)
    # v0.15 wall piers: when True the engine auto-labels every unlabeled
    # wall with its uid so it gets per-story pier P/V/M output (a wall with
    # an explicit ShellRegion.pier label always reports).
    auto_pier_walls: bool = False
    # v0.16 serviceability: beam deflection limit denominator.  The engine's
    # ``deflection_checks`` flag a beam when its max relative-to-chord
    # transverse deflection exceeds L / deflection_limit (default L/360,
    # the classic live-load floor-beam limit).  Finite and > 0.
    deflection_limit: float = 360.0
    # v0.17 panel zones: model-level beam-column joint assumption (see
    # PANEL_ZONE_OPTIONS).  "none" keeps the exact pre-v0.17 centerline
    # behavior; "rigid"/"scissors" are applied INTERNALLY by the engine at
    # build time (the model data is never mutated).
    panel_zones: str = "none"
    num_modes: int = 6

    # ---------------- convenience API ----------------
    def add_material(self, mat: Material) -> Material:
        self.materials[mat.name] = mat
        return mat

    def add_section(self, sec: FrameSection) -> FrameSection:
        if sec.material not in self.materials:
            raise ValueError(f"Section {sec.name}: unknown material {sec.material}")
        self._validate_section_mods(sec)
        self.sections[sec.name] = sec
        return sec

    # ---------------- v0.21 Section Designer ----------------
    def add_designer_section(self, ds) -> "DesignerSection":
        """Add/replace a designer section AND its mirrored FrameSection.

        Validates the polygons/rebar against the material table, computes
        the exact polygon properties, and stores a same-named
        :class:`FrameSection` (transformed A/I, approximate J, bbox b/h —
        see :mod:`skyframe.core.sections_designer`) so the section can be
        assigned to members like any other.  Preserves any existing
        same-named section's v0.4 stiffness modifiers.
        """
        from skyframe.core.sections_designer import (make_frame_section,
                                                     validate_designer_section)
        validate_designer_section(ds, self.materials)
        sec = make_frame_section(ds, self.materials)
        old = self.sections.get(ds.name)
        if old is not None:                      # keep the modifiers
            sec.mod_A, sec.mod_I33 = old.mod_A, old.mod_I33
            sec.mod_I22, sec.mod_J = old.mod_I22, old.mod_J
        self.designer_sections[ds.name] = ds
        self.sections[ds.name] = sec
        return ds

    def remove_designer_section(self, name: str) -> None:
        """Delete a designer section and its mirrored FrameSection.

        Raises ``KeyError`` for an unknown name and ``ValueError`` when a
        member still uses the section.
        """
        if name not in self.designer_sections:
            raise KeyError(name)
        used = [m.uid for m in self.members if m.section == name]
        if used:
            raise ValueError(f"Designer section {name}: still used by "
                             f"member(s) {used}")
        del self.designer_sections[name]
        self.sections.pop(name, None)

    @staticmethod
    def _validate_section_mods(sec: FrameSection) -> None:
        for key in ("mod_A", "mod_I33", "mod_I22", "mod_J"):
            v = getattr(sec, key)
            if not (isinstance(v, (int, float)) and math.isfinite(v)
                    and v > 0.0):
                raise ValueError(f"Section {sec.name}: {key} must be a "
                                 f"finite value > 0 (got {v!r})")

    def set_stories(self, heights: List[float], names: Optional[List[str]] = None) -> None:
        self.stories = []
        elev = 0.0
        for i, h in enumerate(heights):
            elev += h
            nm = names[i] if names else f"Story{i + 1}"
            self.stories.append(Story(nm, h, elev))

    def story_by_name(self, name: str) -> Story:
        for s in self.stories:
            if s.name == name:
                return s
        raise KeyError(name)

    def add_member(self, kind: str, section: str,
                   pi: Tuple[float, float, float], pj: Tuple[float, float, float],
                   story: str = "", uid: str = "",
                   releases: str = "", angle: float = 0.0,
                   rigid_i: float = 0.0, rigid_j: float = 0.0,
                   rigid_factor: float = 1.0,
                   foundation_ks: float = 0.0,
                   foundation_width: float = 0.0,
                   axial_limit: str = "both",
                   hinges: str = "none") -> FrameMember:
        if section not in self.sections:
            raise ValueError(f"Unknown section {section}")
        uid = uid or f"{kind[0].upper()}{len(self.members) + 1}"
        m = FrameMember(uid, kind, section,
                        tuple(float(v) for v in pi),
                        tuple(float(v) for v in pj), story,
                        releases=releases, angle=float(angle),
                        rigid_i=float(rigid_i), rigid_j=float(rigid_j),
                        rigid_factor=float(rigid_factor),
                        foundation_ks=float(foundation_ks),
                        foundation_width=float(foundation_width),
                        axial_limit=str(axial_limit),
                        hinges=str(hinges))
        if m.length < 1e-9:
            raise ValueError(f"Member {uid} has zero length")
        if not m.release_tokens() <= {"Mi", "Mj"}:
            raise ValueError(f"Member {uid}: bad releases {releases!r} "
                             "(tokens must be 'Mi'/'Mj')")
        self._validate_rigid_offsets(m)
        self._validate_foundation(m)
        self._validate_axial_limit(m)
        self._validate_member_hinges(m)
        self.members.append(m)
        return m

    @staticmethod
    def _validate_axial_limit(m: FrameMember) -> None:
        if m.axial_limit not in AXIAL_LIMITS:
            raise ValueError(f"Member {m.uid}: axial_limit must be one of "
                             f"{AXIAL_LIMITS}, got {m.axial_limit!r}")

    @staticmethod
    def _validate_member_hinges(m: FrameMember) -> None:
        if m.hinges not in MEMBER_HINGE_OPTIONS:
            raise ValueError(f"Member {m.uid}: hinges must be one of "
                             f"{MEMBER_HINGE_OPTIONS}, got {m.hinges!r}")

    @staticmethod
    def _validate_foundation(m: FrameMember) -> None:
        for key in ("foundation_ks", "foundation_width"):
            v = getattr(m, key)
            if not (isinstance(v, (int, float)) and math.isfinite(v)
                    and v >= 0.0):
                raise ValueError(f"Member {m.uid}: {key} must be a finite "
                                 f"value >= 0 (got {v!r})")

    @staticmethod
    def _validate_rigid_offsets(m: FrameMember) -> None:
        for key in ("rigid_i", "rigid_j", "rigid_factor"):
            v = getattr(m, key)
            if not (isinstance(v, (int, float)) and math.isfinite(v)):
                raise ValueError(f"Member {m.uid}: {key} must be finite "
                                 f"(got {v!r})")
        if m.rigid_i < 0.0 or m.rigid_j < 0.0:
            raise ValueError(f"Member {m.uid}: rigid_i/rigid_j must be >= 0")
        if not 0.0 <= m.rigid_factor <= 1.0:
            raise ValueError(f"Member {m.uid}: rigid_factor must be in [0, 1] "
                             f"(got {m.rigid_factor})")
        if m.rigid_offset_i + m.rigid_offset_j >= m.length - 1e-9:
            raise ValueError(f"Member {m.uid}: rigid offsets "
                             f"({m.rigid_offset_i + m.rigid_offset_j:.4g} m) "
                             f"leave no clear span (length {m.length:.4g} m)")

    def add_shell_section(self, sec: ShellSection) -> ShellSection:
        if sec.material not in self.materials:
            raise ValueError(f"Shell section {sec.name}: unknown material "
                             f"{sec.material}")
        if sec.thickness <= 0.0:
            raise ValueError(f"Shell section {sec.name}: thickness must be > 0")
        if not (isinstance(sec.mod, (int, float)) and math.isfinite(sec.mod)
                and sec.mod > 0.0):
            raise ValueError(f"Shell section {sec.name}: mod must be a "
                             f"finite value > 0 (got {sec.mod!r})")
        self._validate_layered(sec)
        self.shell_sections[sec.name] = sec
        return sec

    def add_shell(self, kind: str, behavior: str, section: str,
                  corners: List[Tuple[float, float, float]],
                  mesh_size: float = 1.0, story: str = "",
                  uid: str = "",
                  openings: Optional[List[Opening]] = None) -> ShellRegion:
        """Add a wall/slab region (validates shape, planarity, references)."""
        uid = uid or f"{'W' if kind == 'wall' else 'S'}{len(self.shells) + 1}"
        region = ShellRegion(uid, kind, behavior, section,
                             [tuple(float(v) for v in c) for c in corners],
                             mesh_size=float(mesh_size), story=story,
                             openings=list(openings or []))
        self._validate_shell(region)
        self.shells.append(region)
        return region

    def _validate_shell(self, region: ShellRegion) -> None:
        if region.kind not in ("wall", "slab"):
            raise ValueError(f"Shell {region.uid}: kind must be wall|slab")
        if region.behavior not in ("shell", "membrane"):
            raise ValueError(f"Shell {region.uid}: behavior must be "
                             "shell|membrane")
        if region.behavior == "membrane" and region.kind != "slab":
            raise ValueError(f"Shell {region.uid}: membrane behavior is only "
                             "supported for slabs")
        if len(region.corners) != 4:
            raise ValueError(f"Shell {region.uid}: needs exactly 4 corners")
        if region.behavior == "shell":
            if region.section not in self.shell_sections:
                raise ValueError(f"Shell {region.uid}: unknown shell section "
                                 f"{region.section}")
            if region.mesh_size <= 0.0:
                raise ValueError(f"Shell {region.uid}: mesh_size must be > 0")
        c = [tuple(map(float, p)) for p in region.corners]
        if region.area < 1e-9:
            raise ValueError(f"Shell {region.uid}: degenerate (zero area)")
        # planarity: distance of corner 3 from the plane of corners 0-1-2
        n = _vcross(_vsub(c[1], c[0]), _vsub(c[2], c[0]))
        nn = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
        if nn < 1e-12:
            raise ValueError(f"Shell {region.uid}: corners 0-1-2 are collinear")
        d = _vsub(c[3], c[0])
        dist = abs(d[0] * n[0] + d[1] * n[1] + d[2] * n[2]) / nn
        if dist > _PLANAR_TOL:
            raise ValueError(f"Shell {region.uid}: corners are not planar "
                             f"(off-plane {dist:.2e} m)")
        # v0.5 openings: valid parametric bounds, pairwise non-overlapping
        for k, op in enumerate(region.openings):
            for key in ("u0", "v0", "u1", "v1"):
                v = getattr(op, key)
                if not (isinstance(v, (int, float)) and math.isfinite(v)):
                    raise ValueError(f"Shell {region.uid}: opening {k}: "
                                     f"{key} must be a finite number")
            if not (0.0 <= op.u0 < op.u1 <= 1.0
                    and 0.0 <= op.v0 < op.v1 <= 1.0):
                raise ValueError(
                    f"Shell {region.uid}: opening {k}: bounds must satisfy "
                    f"0 <= u0 < u1 <= 1 and 0 <= v0 < v1 <= 1 "
                    f"(got u=[{op.u0}, {op.u1}], v=[{op.v0}, {op.v1}])")
        for a in range(len(region.openings)):
            for b in range(a + 1, len(region.openings)):
                oa, ob = region.openings[a], region.openings[b]
                if (oa.u0 < ob.u1 and ob.u0 < oa.u1
                        and oa.v0 < ob.v1 and ob.v0 < oa.v1):
                    raise ValueError(f"Shell {region.uid}: openings {a} and "
                                     f"{b} overlap")
        self._validate_area_spring(region)          # v0.22

    def pattern(self, name: str, kind: str = "other") -> LoadPattern:
        if name not in self.patterns:
            self.patterns[name] = LoadPattern(name, kind)
        return self.patterns[name]

    def add_case(self, name: str, patterns: Dict[str, float],
                 pdelta: bool = False,
                 pdelta_gravity: Optional[Dict[str, float]] = None) -> LoadCase:
        for p in patterns:
            if p not in self.patterns:
                raise ValueError(f"Case {name}: unknown pattern {p}")
        for p in (pdelta_gravity or {}):
            if p not in self.patterns:
                raise ValueError(f"Case {name}: pdelta_gravity references "
                                 f"unknown pattern {p}")
        c = LoadCase(name, dict(patterns), pdelta=bool(pdelta),
                     pdelta_gravity=(dict(pdelta_gravity)
                                     if pdelta_gravity else None))
        self.cases[name] = c
        return c

    def add_combo(self, name: str, cases: Dict[str, float],
                  combo_type: str = "add") -> LoadCombo:
        cb = LoadCombo(name, dict(cases), combo_type=combo_type)
        self._validate_combo(cb)
        self.combos[name] = cb
        return cb

    def _validate_combo(self, combo: LoadCombo) -> None:
        if combo.combo_type not in COMBO_TYPES:
            raise ValueError(f"Combo {combo.name}: combo_type must be "
                             f"add|envelope, got {combo.combo_type!r}")
        if combo.combo_type == "envelope" and not combo.cases:
            raise ValueError(f"Combo {combo.name}: an envelope combo needs "
                             "at least one case")
        for c in combo.cases:
            if c not in self.cases:
                if c in self.rs_cases:
                    raise ValueError(f"Combo {combo.name}: response-spectrum "
                                     f"case {c!r} cannot enter a load combo "
                                     "(v0.3)")
                if c in self.th_cases:
                    raise ValueError(f"Combo {combo.name}: time-history case "
                                     f"{c!r} cannot enter a load combo "
                                     "(v0.4)")
                if c in self.staged_cases:
                    raise ValueError(f"Combo {combo.name}: staged case "
                                     f"{c!r} cannot enter a load combo "
                                     "(v0.6)")
                raise ValueError(f"Combo {combo.name}: unknown case {c}")

    def add_rs_case(self, name: str, direction: str,
                    spectrum: Optional[List[List[float]]] = None,
                    num_modes: int = 0,
                    combo_method: str = "CQC", damping: float = 0.05,
                    scale: float = 1.0, function: str = ""
                    ) -> ResponseSpectrumCase:
        rs = ResponseSpectrumCase(
            name, direction,
            [[float(t), float(sa)] for t, sa in (spectrum or [])],
            num_modes=int(num_modes), combo_method=combo_method,
            damping=float(damping), scale=float(scale),
            function=str(function))
        self._validate_rs_case(rs)
        self.rs_cases[name] = rs
        return rs

    def _validate_rs_case(self, rs: ResponseSpectrumCase) -> None:
        if rs.direction not in RS_DIRECTIONS:
            raise ValueError(f"RS case {rs.name}: direction must be X|Y, "
                             f"got {rs.direction!r}")
        if rs.combo_method not in RS_COMBO_METHODS:
            raise ValueError(f"RS case {rs.name}: combo_method must be "
                             f"CQC|SRSS, got {rs.combo_method!r}")
        # v0.13: a case may EITHER name a spectrum_function OR carry an inline
        # spectrum; the inline spectrum is optional only when a function is set.
        if rs.function:
            if rs.function not in self.spectrum_functions:
                raise ValueError(f"RS case {rs.name}: function "
                                 f"{rs.function!r} is not a defined "
                                 "spectrum_function")
        elif not rs.spectrum:
            raise ValueError(f"RS case {rs.name}: spectrum must have at "
                             "least one [T, Sa] point (or name a function)")
        for pt in rs.spectrum:
            if len(pt) != 2:
                raise ValueError(f"RS case {rs.name}: spectrum points must "
                                 "be [T, Sa] pairs")
            t, sa = float(pt[0]), float(pt[1])
            if t < 0.0 or sa < 0.0:
                raise ValueError(f"RS case {rs.name}: spectrum values must "
                                 f"be >= 0 (got [{t}, {sa}])")
        if not 0.0 < rs.damping < 1.0:
            raise ValueError(f"RS case {rs.name}: damping must be in (0, 1)")
        if rs.num_modes < 0:
            raise ValueError(f"RS case {rs.name}: num_modes must be >= 0")

    def add_th_case(self, name: str, direction: str,
                    accel: Optional[List[float]] = None,
                    dt: float = 0.0, damping: float = 0.05,
                    scale: float = 1.0, nonlinear: bool = False,
                    gravity: Optional[Dict[str, float]] = None,
                    hinges: str = "column_base",
                    My: Optional[Dict[str, float]] = None,
                    default_My: Optional[float] = None,
                    hardening: float = 0.02,
                    function: str = "") -> TimeHistoryCase:
        th = TimeHistoryCase(
            name, direction, [float(a) for a in (accel or [])], float(dt),
            damping=float(damping), scale=float(scale),
            nonlinear=bool(nonlinear), gravity=dict(gravity or {}),
            hinges=hinges,
            My={k: float(v) for k, v in (My or {}).items()},
            default_My=(None if default_My is None else float(default_My)),
            hardening=float(hardening), function=str(function))
        self._validate_th_case(th)
        self.th_cases[name] = th
        return th

    def _validate_th_case(self, th: TimeHistoryCase) -> None:
        if th.direction not in TH_DIRECTIONS:
            raise ValueError(f"TH case {th.name}: direction must be X|Y, "
                             f"got {th.direction!r}")
        # v0.13: a case may EITHER name a th_function OR carry an inline record.
        if th.function:
            if th.function not in self.th_functions:
                raise ValueError(f"TH case {th.name}: function "
                                 f"{th.function!r} is not a defined "
                                 "th_function")
        elif not th.accel:
            raise ValueError(f"TH case {th.name}: accel record is empty "
                             "(or name a function)")
        for a in th.accel:
            if not math.isfinite(float(a)):
                raise ValueError(f"TH case {th.name}: accel values must be "
                                 "finite")
        if not th.function and not (math.isfinite(th.dt) and th.dt > 0.0):
            raise ValueError(f"TH case {th.name}: dt must be > 0")
        if not 0.0 < th.damping < 1.0:
            raise ValueError(f"TH case {th.name}: damping must be in (0, 1)")
        if not math.isfinite(th.scale):
            raise ValueError(f"TH case {th.name}: scale must be finite")
        # v0.6 nonlinear fields (mirror the pushover-case rules)
        if th.hinges not in PUSHOVER_HINGE_MODES:
            raise ValueError(f"TH case {th.name}: hinges must be "
                             f"column_base|all_ends, got {th.hinges!r}")
        if not (math.isfinite(th.hardening) and 0.0 <= th.hardening < 1.0):
            raise ValueError(f"TH case {th.name}: hardening must be in "
                             "[0, 1)")
        for p in th.gravity:
            if p not in self.patterns:
                raise ValueError(f"TH case {th.name}: gravity references "
                                 f"unknown pattern {p}")
        uids = {m.uid for m in self.members}
        for uid, my in th.My.items():
            if uid not in uids:
                raise ValueError(f"TH case {th.name}: My references "
                                 f"unknown member {uid!r}")
            if not (isinstance(my, (int, float)) and math.isfinite(my)
                    and my > 0.0):
                raise ValueError(f"TH case {th.name}: My[{uid!r}] must be "
                                 "a finite value > 0")
        if th.default_My is not None and not (
                math.isfinite(th.default_My) and th.default_My > 0.0):
            raise ValueError(f"TH case {th.name}: default_My must be a "
                             "finite value > 0 (or None)")

    # ------------------------------------------- v0.13 function library
    def add_spectrum_function(self, name: str, points: List[List[float]],
                              damping: float = 0.05) -> SpectrumFunction:
        fn = SpectrumFunction(
            name, [[float(t), float(sa)] for t, sa in points],
            damping=float(damping))
        self._validate_spectrum_function(fn)
        self.spectrum_functions[name] = fn
        return fn

    @staticmethod
    def _validate_spectrum_function(fn: SpectrumFunction) -> None:
        if not fn.points:
            raise ValueError(f"Spectrum function {fn.name}: points must have "
                             "at least one [T, Sa] pair")
        for pt in fn.points:
            if len(pt) != 2:
                raise ValueError(f"Spectrum function {fn.name}: points must "
                                 "be [T, Sa] pairs")
            t, sa = float(pt[0]), float(pt[1])
            if t < 0.0 or sa < 0.0:
                raise ValueError(f"Spectrum function {fn.name}: values must "
                                 f"be >= 0 (got [{t}, {sa}])")
        if not (math.isfinite(fn.damping) and 0.0 < fn.damping < 1.0):
            raise ValueError(f"Spectrum function {fn.name}: damping must be "
                             "in (0, 1)")

    def add_th_function(self, name: str, values: List[float],
                        dt: float) -> TimeHistoryFunction:
        fn = TimeHistoryFunction(name, [float(v) for v in values], float(dt))
        self._validate_th_function(fn)
        self.th_functions[name] = fn
        return fn

    @staticmethod
    def _validate_th_function(fn: TimeHistoryFunction) -> None:
        if not fn.values:
            raise ValueError(f"TH function {fn.name}: values record is empty")
        for v in fn.values:
            if not math.isfinite(float(v)):
                raise ValueError(f"TH function {fn.name}: values must be "
                                 "finite")
        if not (math.isfinite(fn.dt) and fn.dt > 0.0):
            raise ValueError(f"TH function {fn.name}: dt must be > 0")

    # ------------------------------------------- v0.13 section cuts
    def add_section_cut(self, name: str, axis: str, coord: float,
                        x_range: Optional[List[float]] = None,
                        y_range: Optional[List[float]] = None,
                        z_range: Optional[List[float]] = None) -> SectionCut:
        cut = SectionCut(
            str(name), str(axis), float(coord),
            x_range=(None if x_range is None
                     else [float(x_range[0]), float(x_range[1])]),
            y_range=(None if y_range is None
                     else [float(y_range[0]), float(y_range[1])]),
            z_range=(None if z_range is None
                     else [float(z_range[0]), float(z_range[1])]))
        self._validate_section_cut(cut)
        self.section_cuts.append(cut)
        return cut

    @staticmethod
    def _validate_section_cut(cut: SectionCut) -> None:
        if not cut.name:
            raise ValueError("Section cut: name is required")
        if cut.axis not in SECTION_CUT_AXES:
            raise ValueError(f"Section cut {cut.name}: axis must be x|y|z, "
                             f"got {cut.axis!r}")
        if not math.isfinite(cut.coord):
            raise ValueError(f"Section cut {cut.name}: coord must be finite")
        for key in ("x_range", "y_range", "z_range"):
            r = getattr(cut, key)
            if r is None:
                continue
            if (len(r) != 2 or not all(math.isfinite(float(v)) for v in r)
                    or float(r[0]) > float(r[1])):
                raise ValueError(f"Section cut {cut.name}: {key} must be "
                                 "[lo, hi] with lo <= hi")

    def add_staged_case(self, name: str, pattern: str = "DEAD",
                        stages: str = "per_story",
                        include_live: Optional[Dict[str, float]] = None
                        ) -> StagedCase:
        sc = StagedCase(name, pattern=pattern, stages=stages,
                        include_live={k: float(v) for k, v in
                                      (include_live or {}).items()})
        self._validate_staged_case(sc)
        self.staged_cases[name] = sc
        return sc

    def _validate_staged_case(self, sc: StagedCase) -> None:
        if sc.stages not in STAGED_MODES:
            raise ValueError(f"Staged case {sc.name}: stages must be one of "
                             f"{STAGED_MODES}, got {sc.stages!r}")
        if sc.pattern not in self.patterns:
            raise ValueError(f"Staged case {sc.name}: unknown pattern "
                             f"{sc.pattern}")
        for p, f in sc.include_live.items():
            if p not in self.patterns:
                raise ValueError(f"Staged case {sc.name}: include_live "
                                 f"references unknown pattern {p}")
            if not math.isfinite(float(f)):
                raise ValueError(f"Staged case {sc.name}: include_live "
                                 f"factor for {p} must be finite")

    def add_buckling_case(self, name: str, gravity: Dict[str, float],
                          num_modes: int = 6) -> BucklingCase:
        bc = BucklingCase(name, gravity={k: float(v)
                                         for k, v in gravity.items()},
                          num_modes=int(num_modes))
        self._validate_buckling_case(bc)
        self.buckling_cases[name] = bc
        return bc

    def _validate_buckling_case(self, bc: BucklingCase) -> None:
        if not bc.gravity:
            raise ValueError(f"Buckling case {bc.name}: gravity must name at "
                             "least one load pattern")
        for p, f in bc.gravity.items():
            if p not in self.patterns:
                raise ValueError(f"Buckling case {bc.name}: gravity references "
                                 f"unknown pattern {p}")
            if not math.isfinite(float(f)):
                raise ValueError(f"Buckling case {bc.name}: gravity factor for "
                                 f"{p} must be finite")
        if bc.num_modes < 1:
            raise ValueError(f"Buckling case {bc.name}: num_modes must be >= 1")

    def add_rs_combo(self, name: str, name_x: str, name_y: str,
                     method: str = "100_30") -> Dict[str, str]:
        combo = {"name_x": str(name_x), "name_y": str(name_y),
                 "method": str(method)}
        self._validate_rs_combo(name, combo)
        self.rs_combos[name] = combo
        return combo

    def _validate_rs_combo(self, name: str, combo: Dict[str, str]) -> None:
        if combo.get("method") not in RS_DIRECTIONAL_METHODS:
            raise ValueError(f"RS combo {name}: method must be one of "
                             f"{RS_DIRECTIONAL_METHODS}, got "
                             f"{combo.get('method')!r}")
        for key in ("name_x", "name_y"):
            rc = combo.get(key)
            if rc not in self.rs_cases:
                raise ValueError(f"RS combo {name}: {key} references unknown "
                                 f"response-spectrum case {rc!r}")

    def add_pushover_case(self, name: str, direction: str,
                          gravity: Optional[Dict[str, float]] = None,
                          target_drift: float = 0.02, steps: int = 100,
                          hinges: str = "column_base",
                          My: Optional[Dict[str, float]] = None,
                          default_My: Optional[float] = None,
                          hardening: float = 0.02,
                          hinge_params: Optional[Dict[str, float]] = None
                          ) -> PushoverCase:
        po = PushoverCase(
            name, direction, gravity=dict(gravity or {}),
            target_drift=float(target_drift), steps=int(steps),
            hinges=hinges,
            My={k: float(v) for k, v in (My or {}).items()},
            default_My=(None if default_My is None else float(default_My)),
            hardening=float(hardening),
            hinge_params={k: float(v)
                          for k, v in (hinge_params or {}).items()})
        self._validate_pushover_case(po)
        self.pushover_cases[name] = po
        return po

    def _validate_pushover_case(self, po: PushoverCase) -> None:
        if po.direction not in PUSHOVER_DIRECTIONS:
            raise ValueError(f"Pushover case {po.name}: direction must be "
                             f"X|Y, got {po.direction!r}")
        if po.hinges not in PUSHOVER_HINGE_MODES + ("asce41",):
            raise ValueError(f"Pushover case {po.name}: hinges must be "
                             f"column_base|all_ends|asce41, got "
                             f"{po.hinges!r}")
        _HP_KEYS = ("expected_factor", "rho", "rho_prime", "fy_bar")
        for k, v in po.hinge_params.items():
            if k not in _HP_KEYS:
                raise ValueError(f"Pushover case {po.name}: unknown "
                                 f"hinge_params key {k!r} (allowed: "
                                 f"{_HP_KEYS})")
            if not (isinstance(v, (int, float)) and math.isfinite(v)
                    and v > 0.0 and not isinstance(v, bool)):
                raise ValueError(f"Pushover case {po.name}: "
                                 f"hinge_params[{k!r}] must be a finite "
                                 "value > 0")
        if not (math.isfinite(po.target_drift) and po.target_drift > 0.0):
            raise ValueError(f"Pushover case {po.name}: target_drift must "
                             "be > 0")
        if po.steps < 1:
            raise ValueError(f"Pushover case {po.name}: steps must be >= 1")
        if not (math.isfinite(po.hardening) and 0.0 <= po.hardening < 1.0):
            raise ValueError(f"Pushover case {po.name}: hardening must be "
                             "in [0, 1)")
        for p in po.gravity:
            if p not in self.patterns:
                raise ValueError(f"Pushover case {po.name}: gravity "
                                 f"references unknown pattern {p}")
        uids = {m.uid for m in self.members}
        for uid, my in po.My.items():
            if uid not in uids:
                raise ValueError(f"Pushover case {po.name}: My references "
                                 f"unknown member {uid!r}")
            if not (isinstance(my, (int, float)) and math.isfinite(my)
                    and my > 0.0):
                raise ValueError(f"Pushover case {po.name}: My[{uid!r}] "
                                 "must be a finite value > 0")
        if po.default_My is not None and not (
                math.isfinite(po.default_My) and po.default_My > 0.0):
            raise ValueError(f"Pushover case {po.name}: default_My must be "
                             "a finite value > 0 (or None)")

    def add_link(self, pi: Tuple[float, float, float],
                 pj: Tuple[float, float, float],
                 stiffness: Optional[List[float]] = None, uid: str = "",
                 link_type: str = "elastic",
                 params: Optional[Dict[str, float]] = None) -> LinkMember:
        uid = uid or f"L{len(self.links) + 1}"
        if stiffness is None:
            stiffness = [0.0] * 6           # advanced types: unused
        lk = LinkMember(uid, tuple(float(v) for v in pi),
                        tuple(float(v) for v in pj),
                        [float(k) for k in stiffness],
                        link_type=str(link_type),
                        params=LinkMember.coerce_params(params))
        self._validate_link(lk)
        if any(o.uid == lk.uid for o in self.links):
            raise ValueError(f"Duplicate link uid {lk.uid!r}")
        self.links.append(lk)
        return lk

    def add_spring_support(self, point: Tuple[float, float, float],
                           stiffness: List[float]) -> SpringSupport:
        sp = SpringSupport(tuple(float(v) for v in point),
                           [float(k) for k in stiffness])
        self._validate_spring(sp)
        self.spring_supports.append(sp)
        return sp

    def add_line_spring(self, p1: Tuple[float, float, float],
                        p2: Tuple[float, float, float],
                        kz: float = 0.0, kx: float = 0.0, ky: float = 0.0,
                        compression_only: bool = False) -> LineSpring:
        ls = LineSpring(tuple(float(v) for v in p1),
                        tuple(float(v) for v in p2),
                        kz=float(kz), kx=float(kx), ky=float(ky),
                        compression_only=bool(compression_only))
        self._validate_line_spring(ls)
        self.line_springs.append(ls)
        return ls

    @staticmethod
    def _validate_line_spring(ls: LineSpring) -> None:
        for key in ("kz", "kx", "ky"):
            v = getattr(ls, key)
            if not (isinstance(v, (int, float)) and math.isfinite(v)
                    and v >= 0.0):
                raise ValueError(f"line spring: {key} must be finite and "
                                 f">= 0 (got {v!r})")
        if not (ls.kz > 0.0 or ls.kx > 0.0 or ls.ky > 0.0):
            raise ValueError("line spring: at least one of kz/kx/ky must "
                             "be > 0")
        d = _vsub(tuple(map(float, ls.p2)), tuple(map(float, ls.p1)))
        if math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2) < 1e-9:
            raise ValueError("line spring: p1 and p2 must be distinct")
        if ls.compression_only and ls.kz <= 0.0:
            raise ValueError("line spring: compression_only needs kz > 0")

    @staticmethod
    def _validate_area_spring(region: "ShellRegion") -> None:
        asp = region.area_spring
        if asp is None:
            return
        if not isinstance(asp, dict):
            raise ValueError(f"Shell {region.uid}: area_spring must be a "
                             "dict {kz, compression_only}")
        allowed = {"kz", "compression_only"}
        unknown = set(asp) - allowed
        if unknown:
            raise ValueError(f"Shell {region.uid}: unknown area_spring "
                             f"key(s) {sorted(unknown)} (allowed: "
                             f"{sorted(allowed)})")
        kz = asp.get("kz")
        if not (isinstance(kz, (int, float)) and not isinstance(kz, bool)
                and math.isfinite(kz) and kz > 0.0):
            raise ValueError(f"Shell {region.uid}: area_spring kz must be "
                             f"a finite value > 0 (got {kz!r})")
        if not isinstance(asp.get("compression_only", False), bool):
            raise ValueError(f"Shell {region.uid}: area_spring "
                             "compression_only must be a bool")
        if region.behavior != "shell":
            raise ValueError(f"Shell {region.uid}: area_spring needs shell "
                             "behavior (membrane slabs have no mesh nodes)")

    def _validate_layered(self, ssec: ShellSection) -> None:
        lay = ssec.layered
        if lay is None:
            return
        if not isinstance(lay, dict) or set(lay) != {"layers"}:
            raise ValueError(f"Shell section {ssec.name}: layered must be a "
                             "dict with the single key 'layers'")
        layers = lay["layers"]
        if not isinstance(layers, (list, tuple)) or not layers:
            raise ValueError(f"Shell section {ssec.name}: layered.layers "
                             "must be a non-empty list")
        allowed = {"t", "material", "kind", "angle"}
        for k, la in enumerate(layers):
            if not isinstance(la, dict):
                raise ValueError(f"Shell section {ssec.name}: layer {k} "
                                 "must be a dict")
            unknown = set(la) - allowed
            if unknown:
                raise ValueError(f"Shell section {ssec.name}: layer {k}: "
                                 f"unknown key(s) {sorted(unknown)} "
                                 f"(allowed: {sorted(allowed)})")
            t = la.get("t")
            if not (isinstance(t, (int, float)) and not isinstance(t, bool)
                    and math.isfinite(t) and t > 0.0):
                raise ValueError(f"Shell section {ssec.name}: layer {k}: t "
                                 f"must be a finite value > 0 (got {t!r})")
            if la.get("material") not in self.materials:
                raise ValueError(f"Shell section {ssec.name}: layer {k}: "
                                 f"unknown material {la.get('material')!r}")
            kind = la.get("kind")
            if kind not in SHELL_LAYER_KINDS:
                raise ValueError(f"Shell section {ssec.name}: layer {k}: "
                                 f"kind must be one of {SHELL_LAYER_KINDS}, "
                                 f"got {kind!r}")
            if "angle" in la:
                if kind != "steel":
                    raise ValueError(f"Shell section {ssec.name}: layer "
                                     f"{k}: angle is only valid for steel "
                                     "layers")
                if la["angle"] not in (0, 90, 0.0, 90.0):
                    raise ValueError(f"Shell section {ssec.name}: layer "
                                     f"{k}: angle must be 0 or 90 (got "
                                     f"{la['angle']!r})")

    @staticmethod
    def _validate_spring(sp: SpringSupport) -> None:
        if len(sp.stiffness) != 6:
            raise ValueError("spring stiffness needs 6 entries "
                             "[kx, ky, kz, krx, kry, krz]")
        for k in sp.stiffness:
            if not (isinstance(k, (int, float)) and math.isfinite(k)
                    and k >= 0.0):
                raise ValueError(f"spring stiffness entries must be finite "
                                 f"and >= 0 (got {k!r})")
        if not any(k > 0.0 for k in sp.stiffness):
            raise ValueError("spring: at least one stiffness entry must be > 0")

    def add_thermal_load(self, pattern: str, member_uid: str,
                         dT: float) -> ThermalLoad:
        if pattern not in self.patterns:
            raise ValueError(f"Unknown load pattern {pattern!r}")
        if member_uid not in {m.uid for m in self.members}:
            raise ValueError(f"Thermal load references unknown member "
                             f"{member_uid!r}")
        tl = ThermalLoad(member_uid, float(dT))
        self.patterns[pattern].thermal_loads.append(tl)
        return tl

    @staticmethod
    def _validate_link(lk: LinkMember) -> None:
        if len(lk.stiffness) != 6:
            raise ValueError(f"Link {lk.uid}: stiffness needs 6 entries "
                             "[kx, ky, kz, krx, kry, krz]")
        for k in lk.stiffness:
            if not (isinstance(k, (int, float)) and math.isfinite(k)
                    and k >= 0.0):
                raise ValueError(f"Link {lk.uid}: stiffness entries must be "
                                 f"finite and >= 0 (got {k!r})")
        ltype = getattr(lk, "link_type", "elastic")
        if ltype not in LINK_TYPES:
            raise ValueError(f"Link {lk.uid}: link_type must be one of "
                             f"{LINK_TYPES}, got {ltype!r}")
        if ltype == "elastic":
            if not any(k > 0.0 for k in lk.stiffness):
                raise ValueError(f"Link {lk.uid}: at least one stiffness "
                                 "entry must be > 0")
            return
        # ---- v0.15 advanced device links: validate params completeness ----
        prm = getattr(lk, "params", {}) or {}
        required, optional = _LINK_PARAM_KEYS[ltype]
        for key in required:
            if key not in prm:
                raise ValueError(f"Link {lk.uid} ({ltype}): missing required "
                                 f"param {key!r} (needs {list(required)})")
        for key, v in prm.items():
            if key not in required and key not in optional:
                raise ValueError(f"Link {lk.uid} ({ltype}): unknown param "
                                 f"{key!r} (allowed: "
                                 f"{list(required) + list(optional)})")
            if key == "points":
                continue                    # nested list, validated below
            if not (isinstance(v, (int, float)) and math.isfinite(v)):
                raise ValueError(f"Link {lk.uid} ({ltype}): param {key!r} "
                                 f"must be a finite number (got {v!r})")
        length = lk.length
        if ltype == "damper":
            if prm["cd"] <= 0.0:
                raise ValueError(f"Link {lk.uid} (damper): cd must be > 0")
            alpha = prm.get("alpha", DAMPER_DEFAULT_ALPHA)
            if not 0.0 < alpha <= 2.0:
                raise ValueError(f"Link {lk.uid} (damper): alpha must be in "
                                 f"(0, 2] (got {alpha!r})")
            if prm.get("k", DAMPER_DEFAULT_K) <= 0.0:
                raise ValueError(f"Link {lk.uid} (damper): k must be > 0")
        elif ltype in ("gap", "hook"):
            if prm["k"] <= 0.0:
                raise ValueError(f"Link {lk.uid} ({ltype}): k must be > 0")
            open_key = "gap" if ltype == "gap" else "slack"
            if prm[open_key] < 0.0:
                raise ValueError(f"Link {lk.uid} ({ltype}): {open_key} must "
                                 "be >= 0")
        elif ltype == "isolator":
            if prm["k1"] <= 0.0:
                raise ValueError(f"Link {lk.uid} (isolator): k1 must be > 0")
            if prm["k2"] < 0.0 or prm["k2"] >= prm["k1"]:
                raise ValueError(f"Link {lk.uid} (isolator): k2 must satisfy "
                                 "0 <= k2 < k1")
            if prm["Fy"] <= 0.0:
                raise ValueError(f"Link {lk.uid} (isolator): Fy must be > 0")
            if prm.get("kv", ISOLATOR_DEFAULT_KV) <= 0.0:
                raise ValueError(f"Link {lk.uid} (isolator): kv must be > 0")
        elif ltype == "fp_isolator":
            for key in ("R", "mu"):
                if prm[key] <= 0.0:
                    raise ValueError(f"Link {lk.uid} (fp_isolator): {key} "
                                     "must be > 0")
            if prm.get("k_init", FP_DEFAULT_KINIT) <= 0.0:
                raise ValueError(f"Link {lk.uid} (fp_isolator): k_init must "
                                 "be > 0")
            if prm.get("kv", ISOLATOR_DEFAULT_KV) <= 0.0:
                raise ValueError(f"Link {lk.uid} (fp_isolator): kv must "
                                 "be > 0")
            if "P0" in prm and prm["P0"] <= 0.0:
                raise ValueError(f"Link {lk.uid} (fp_isolator): P0 must "
                                 "be > 0")
        elif ltype == "triple_fp":
            for key in ("R1", "R2", "R3", "mu1", "mu2", "mu3",
                        "d1", "d2", "d3", "W"):
                if prm[key] <= 0.0:
                    raise ValueError(f"Link {lk.uid} (triple_fp): {key} "
                                     "must be > 0")
            for key, dflt in (("uy", TFP_DEFAULT_UY),
                              ("kv", ISOLATOR_DEFAULT_KV),
                              ("kvt", ISOLATOR_DEFAULT_KV),
                              ("tol", TFP_DEFAULT_TOL)):
                if prm.get(key, dflt) <= 0.0:
                    raise ValueError(f"Link {lk.uid} (triple_fp): {key} "
                                     "must be > 0")
            if prm.get("minFv", TFP_DEFAULT_MINFV) < 0.0:
                raise ValueError(f"Link {lk.uid} (triple_fp): minFv must "
                                 "be >= 0")
        else:  # multilinear
            pts = prm.get("points")
            if (not isinstance(pts, (list, tuple)) or len(pts) < 2
                    or not all(isinstance(p, (list, tuple)) and len(p) == 2
                               for p in pts)):
                raise ValueError(f"Link {lk.uid} (multilinear): points must "
                                 "be a list of at least 2 [d, F] pairs")
            for p in pts:
                if not all(isinstance(v, (int, float)) and math.isfinite(v)
                           for v in p):
                    raise ValueError(f"Link {lk.uid} (multilinear): points "
                                     "entries must be finite numbers")
            if pts[0][0] <= 0.0:
                raise ValueError(f"Link {lk.uid} (multilinear): the first "
                                 "backbone displacement must be > 0")
            for (dA, _fA), (dB, _fB) in zip(pts[:-1], pts[1:]):
                if dB <= dA:
                    raise ValueError(f"Link {lk.uid} (multilinear): backbone "
                                     "displacements must be strictly "
                                     "increasing")
            if prm.get("kv", ISOLATOR_DEFAULT_KV) <= 0.0:
                raise ValueError(f"Link {lk.uid} (multilinear): kv must "
                                 "be > 0")
        if ltype in ("isolator", "fp_isolator", "triple_fp", "multilinear"):
            dx = abs(lk.pj[0] - lk.pi[0]) + abs(lk.pj[1] - lk.pi[1])
            if length > 1e-9 and dx > 1e-6:
                raise ValueError(f"Link {lk.uid} ({ltype}): the link axis "
                                 "must be vertical (or zero length)")
        if ltype in ("damper", "gap", "hook") and length < 1e-9:
            raise ValueError(f"Link {lk.uid} ({ltype}): the link axis is "
                             "undefined at zero length — the two points "
                             "must be distinct")

    def effective_diaphragm(self, story_name: str) -> str:
        """Diaphragm mode ("rigid" | "none") that applies to a story (v0.5).

        Per-story ``story_diaphragm`` overrides win; otherwise the global
        ``diaphragm`` applies, with the legacy ``rigid_diaphragms=False``
        forcing the global default to "none"."""
        if story_name in self.story_diaphragm:
            return self.story_diaphragm[story_name]
        return self.diaphragm if self.rigid_diaphragms else "none"

    def _validate_diaphragm(self) -> None:
        if self.diaphragm not in DIAPHRAGM_OPTIONS:
            raise ValueError(f"diaphragm must be rigid|none, got "
                             f"{self.diaphragm!r}")
        story_names = {s.name for s in self.stories}
        for name, mode in self.story_diaphragm.items():
            if name not in story_names:
                raise ValueError(f"story_diaphragm references unknown story "
                                 f"{name!r}")
            if mode not in DIAPHRAGM_OPTIONS:
                raise ValueError(f"story_diaphragm[{name!r}] must be "
                                 f"rigid|none, got {mode!r}")

    # ---------------- derived data ----------------
    def story_elevations(self) -> Dict[str, float]:
        return {s.name: s.elevation for s in self.stories}

    # ---------------- grid systems (v0.14) ----------------
    def effective_grids(self) -> List[GridSystem]:
        """The active grid systems: ``grid_systems`` if any, else the legacy
        single ``grid`` wrapped in a list, else empty."""
        if self.grid_systems:
            return list(self.grid_systems)
        if self.grid is not None:
            return [self.grid]
        return []

    def all_grid_lines_global(self) -> List[dict]:
        """Union of every grid system's ``lines_global()`` (tagged by system).

        Each entry gains a ``"system"`` key naming its grid.
        """
        out: List[dict] = []
        for g in self.effective_grids():
            for ln in g.lines_global():
                out.append({"system": g.name, **ln})
        return out

    def all_intersections_global(self) -> List[dict]:
        """Union of every grid system's ``intersections_global()`` (tagged)."""
        out: List[dict] = []
        for g in self.effective_grids():
            for it in g.intersections_global():
                out.append({"system": g.name, **it})
        return out

    def snap(self, px: float, py: float, tol: float) -> Optional[dict]:
        """Nearest grid intersection across ALL grid systems within ``tol``."""
        best = None
        best_d = None
        for it in self.all_intersections_global():
            gx, gy = it["point"]
            d = math.hypot(gx - px, gy - py)
            if best_d is None or d < best_d:
                best_d, best = d, it
        if best is not None and best_d <= tol:
            return best
        return None

    def set_grid_system(self, gs: GridSystem) -> GridSystem:
        """Add ``gs`` (or replace the grid system with the same ``name``).

        Migrates a legacy single ``grid`` into ``grid_systems`` on first use;
        keeps ``grid`` pointed at the primary (first) system."""
        self._validate_grid(gs)
        if not self.grid_systems:
            self.grid_systems = self.effective_grids()
        for i, g in enumerate(self.grid_systems):
            if g.name == gs.name:
                self.grid_systems[i] = gs
                break
        else:
            self.grid_systems.append(gs)
        self.grid = self.grid_systems[0]
        return gs

    def _grid_plan_bbox(self) -> Optional[Tuple[float, float, float, float]]:
        """(minx, maxx, miny, maxy) over ALL grid systems' global line points,
        or None when no grid produces geometry."""
        xs: List[float] = []
        ys: List[float] = []
        for g in self.effective_grids():
            # Only grids that define a real 2D extent contribute (an
            # orthogonal grid needs BOTH line families; a radial grid needs
            # radii) — matching the legacy "grid OR members" fallback.
            if g.kind == "radial":
                if not g.radii:
                    continue
            elif not (g.x_lines and g.y_lines):
                continue
            for ln in g.lines_global():
                for px, py in ln["points"]:
                    xs.append(px)
                    ys.append(py)
        if not xs or not ys:
            return None
        return (min(xs), max(xs), min(ys), max(ys))

    def plan_extents(self) -> Tuple[float, float]:
        b = self._grid_plan_bbox()
        if b is not None:
            return (b[1] - b[0], b[3] - b[2])
        xs = [p[0] for m in self.members for p in (m.pi, m.pj)] or [0.0]
        ys = [p[1] for m in self.members for p in (m.pi, m.pj)] or [0.0]
        return (max(xs) - min(xs), max(ys) - min(ys))

    def plan_center(self) -> Tuple[float, float]:
        b = self._grid_plan_bbox()
        if b is not None:
            return ((b[0] + b[1]) / 2.0, (b[2] + b[3]) / 2.0)
        xs = [p[0] for m in self.members for p in (m.pi, m.pj)] or [0.0]
        ys = [p[1] for m in self.members for p in (m.pi, m.pj)] or [0.0]
        return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)

    def effective_mass_source(self) -> Dict[str, float]:
        """Pattern -> factor dict that actually feeds story masses (v0.4).

        ``mass_source`` wins when set; otherwise the legacy
        ``mass_from_patterns`` applies (backward compatibility)."""
        return self.mass_source if self.mass_source else self.mass_from_patterns

    def compute_story_masses(self) -> Dict[str, float]:
        """Story mass in tonnes: explicit masses win; otherwise derived from
        gravity load patterns via the mass source (v0.4 ``mass_source``,
        falling back to legacy ``mass_from_patterns``)."""
        masses: Dict[str, float] = {}
        source = self.effective_mass_source()
        for s in self.stories:
            if s.name in self.story_masses:
                masses[s.name] = self.story_masses[s.name]
                continue
            total_w = 0.0  # kN of gravity load on this story
            for pname, fac in source.items():
                pat = self.patterns.get(pname)
                if pat is None:
                    continue
                for udl in pat.member_udls:
                    m = self._member(udl.member_uid)
                    if m is not None and m.story == s.name and m.kind != "column":
                        total_w += fac * udl.w * m.length
                for ml in pat.member_loads:
                    if ml.direction != "gravity":
                        continue
                    m = self._member(ml.member_uid)
                    if m is None or m.story != s.name or m.kind == "column":
                        continue
                    if ml.kind == "point":
                        total_w += fac * ml.w
                    elif ml.kind == "udl":
                        total_w += fac * ml.w * (ml.b - ml.a) * m.length
                    else:  # trapezoid
                        total_w += fac * 0.5 * (ml.w + ml.w2) \
                            * (ml.b - ml.a) * m.length
                for al in pat.area_loads:
                    region = self._shell(al.region_uid)
                    if region is None or not self._region_on_story(region, s):
                        continue
                    total_w += fac * al.q * region.net_area  # v0.5: openings
                for nl in pat.nodal_loads:
                    if abs(nl.point[2] - s.elevation) < 1e-6:
                        total_w += fac * (-nl.fz)  # downward = -fz
                # v0.7: self-weight patterns contribute their real weight to
                # story mass (beams + shells on the story; columns span
                # stories and are excluded, matching the member-UDL rule).
                swf = getattr(pat, "self_weight_factor", 0.0)
                if swf:
                    for m in self.members:
                        if m.story != s.name or m.kind == "column":
                            continue
                        sec = self.sections.get(m.section)
                        mat = (self.materials.get(sec.material)
                               if sec else None)
                        if sec is not None and mat is not None:
                            total_w += (fac * swf * sec.A
                                        * mat.unit_weight * m.length)
                    for region in self.shells:
                        if not self._region_on_story(region, s):
                            continue
                        ssec = self.shell_sections.get(region.section)
                        mat = (self.materials.get(ssec.material)
                               if ssec else None)
                        if ssec is not None and mat is not None:
                            total_w += (fac * swf * ssec.thickness
                                        * mat.unit_weight * region.net_area)
            masses[s.name] = total_w / G_ACCEL
        return masses

    @staticmethod
    def _region_on_story(region: ShellRegion, story: Story) -> bool:
        """Attribute a shell region to a story (mass bookkeeping)."""
        if region.story:
            return region.story == story.name
        zs = [c[2] for c in region.corners]
        return abs(max(zs) - story.elevation) < 1e-6

    def _member(self, uid: str) -> Optional[FrameMember]:
        for m in self.members:
            if m.uid == uid:
                return m
        return None

    def _shell(self, uid: str) -> Optional[ShellRegion]:
        for r in self.shells:
            if r.uid == uid:
                return r
        return None

    # ---------------- validation ----------------
    def validate(self) -> None:
        """Cross-reference validation; raises ValueError on the first issue."""
        for sec in self.sections.values():
            if sec.material not in self.materials:
                raise ValueError(f"Section {sec.name}: unknown material "
                                 f"{sec.material}")
            self._validate_section_mods(sec)
        if self.designer_sections:
            from skyframe.core.sections_designer import \
                validate_designer_section
            for ds in self.designer_sections.values():
                validate_designer_section(ds, self.materials)
        for ssec in self.shell_sections.values():
            if ssec.material not in self.materials:
                raise ValueError(f"Shell section {ssec.name}: unknown "
                                 f"material {ssec.material}")
            if not (isinstance(ssec.mod, (int, float))
                    and math.isfinite(ssec.mod) and ssec.mod > 0.0):
                raise ValueError(f"Shell section {ssec.name}: mod must be a "
                                 f"finite value > 0 (got {ssec.mod!r})")
        uids = set()
        for m in self.members:
            if m.uid in uids:
                raise ValueError(f"Duplicate member uid {m.uid!r}")
            uids.add(m.uid)
            if m.section not in self.sections:
                raise ValueError(f"Member {m.uid}: unknown section {m.section}")
            if m.length < 1e-9:
                raise ValueError(f"Member {m.uid} has zero length")
            if not m.release_tokens() <= {"Mi", "Mj"}:
                raise ValueError(f"Member {m.uid}: bad releases "
                                 f"{m.releases!r}")
            self._validate_rigid_offsets(m)
            self._validate_foundation(m)
            self._validate_axial_limit(m)
            self._validate_member_hinges(m)
        region_uids = set()
        for r in self.shells:
            if r.uid in region_uids:
                raise ValueError(f"Duplicate shell uid {r.uid!r}")
            region_uids.add(r.uid)
            self._validate_shell(r)
        for pat in self.patterns.values():
            for u in pat.member_udls:
                if u.member_uid not in uids:
                    raise ValueError(f"Pattern {pat.name}: UDL references "
                                     f"unknown member {u.member_uid!r}")
            for ml in pat.member_loads:
                if ml.member_uid not in uids:
                    raise ValueError(f"Pattern {pat.name}: member load "
                                     f"references unknown member "
                                     f"{ml.member_uid!r}")
                if ml.kind not in MEMBER_LOAD_KINDS:
                    raise ValueError(f"Pattern {pat.name}: bad member load "
                                     f"kind {ml.kind!r}")
                if ml.direction not in MEMBER_LOAD_DIRECTIONS:
                    raise ValueError(f"Pattern {pat.name}: bad member load "
                                     f"direction {ml.direction!r}")
                if ml.kind == "point":
                    if not 0.0 <= ml.a <= 1.0:
                        raise ValueError(f"Pattern {pat.name}: point load "
                                         f"position a={ml.a} outside [0, 1]")
                elif not 0.0 <= ml.a <= ml.b <= 1.0:
                    raise ValueError(f"Pattern {pat.name}: load span "
                                     f"[{ml.a}, {ml.b}] must satisfy "
                                     "0 <= a <= b <= 1")
            for al in pat.area_loads:
                if al.region_uid not in region_uids:
                    raise ValueError(f"Pattern {pat.name}: area load "
                                     f"references unknown shell region "
                                     f"{al.region_uid!r}")
            for tl in pat.thermal_loads:
                if tl.member_uid not in uids:
                    raise ValueError(f"Pattern {pat.name}: thermal load "
                                     f"references unknown member "
                                     f"{tl.member_uid!r}")
            if not (isinstance(pat.ecc, (int, float))
                    and math.isfinite(pat.ecc) and pat.ecc >= 0.0):
                raise ValueError(f"Pattern {pat.name}: ecc must be a finite "
                                 f"value >= 0 (got {pat.ecc!r})")
        for case in self.cases.values():
            for p in case.patterns:
                if p not in self.patterns:
                    raise ValueError(f"Case {case.name}: unknown pattern {p}")
            for p in (case.pdelta_gravity or {}):
                if p not in self.patterns:
                    raise ValueError(f"Case {case.name}: pdelta_gravity "
                                     f"references unknown pattern {p}")
        for combo in self.combos.values():
            self._validate_combo(combo)
        for rs in self.rs_cases.values():
            self._validate_rs_case(rs)
        for th in self.th_cases.values():
            self._validate_th_case(th)
        for po in self.pushover_cases.values():
            self._validate_pushover_case(po)
        for sc in self.staged_cases.values():
            self._validate_staged_case(sc)
        for bc in self.buckling_cases.values():
            self._validate_buckling_case(bc)
        for cname, cb in self.rs_combos.items():
            self._validate_rs_combo(cname, cb)
        for fn in self.spectrum_functions.values():
            self._validate_spectrum_function(fn)
        for fn in self.th_functions.values():
            self._validate_th_function(fn)
        for cut in self.section_cuts:
            self._validate_section_cut(cut)
        link_uids = set()
        for lk in self.links:
            if lk.uid in link_uids:
                raise ValueError(f"Duplicate link uid {lk.uid!r}")
            link_uids.add(lk.uid)
            self._validate_link(lk)
        for sp in self.spring_supports:
            self._validate_spring(sp)
        if not (isinstance(self.thermal_alpha, (int, float))
                and math.isfinite(self.thermal_alpha)):
            raise ValueError(f"thermal_alpha must be finite (got "
                             f"{self.thermal_alpha!r})")
        if not (isinstance(self.deflection_limit, (int, float))
                and math.isfinite(self.deflection_limit)
                and self.deflection_limit > 0.0):
            raise ValueError(f"deflection_limit must be a finite value > 0 "
                             f"(got {self.deflection_limit!r})")
        if self.panel_zones not in PANEL_ZONE_OPTIONS:
            raise ValueError(f"panel_zones must be one of "
                             f"{PANEL_ZONE_OPTIONS}, got {self.panel_zones!r}")
        self._validate_diaphragm()
        for g in self.effective_grids():
            self._validate_grid(g)

    @staticmethod
    def _validate_grid(g: GridSystem) -> None:
        """Validate a grid system (v0.14)."""
        if g.kind not in GRID_KINDS:
            raise ValueError(f"Grid {g.name!r}: kind must be one of "
                             f"{GRID_KINDS}, got {g.kind!r}")
        if (not isinstance(g.origin, (tuple, list)) or len(g.origin) != 2
                or not all(isinstance(v, (int, float)) and math.isfinite(v)
                           for v in g.origin)):
            raise ValueError(f"Grid {g.name!r}: origin must be two finite "
                             "numbers (x, y)")
        if not (isinstance(g.rotation, (int, float))
                and math.isfinite(g.rotation)):
            raise ValueError(f"Grid {g.name!r}: rotation must be finite")
        if g.kind == "orthogonal":
            for key, vals in (("x_lines", g.x_lines), ("y_lines", g.y_lines)):
                if not all(isinstance(v, (int, float)) and math.isfinite(v)
                           for v in vals):
                    raise ValueError(f"Grid {g.name!r}: {key} must be finite "
                                     "numbers")
        else:  # radial
            if not g.radii:
                raise ValueError(f"Grid {g.name!r}: a radial grid needs at "
                                 "least one radius")
            for r in g.radii:
                if not (isinstance(r, (int, float)) and math.isfinite(r)
                        and r > 0.0):
                    raise ValueError(f"Grid {g.name!r}: radii must be finite "
                                     f"and > 0 (got {r!r})")
            for t in g.theta_deg:
                if not (isinstance(t, (int, float)) and math.isfinite(t)):
                    raise ValueError(f"Grid {g.name!r}: theta_deg must be "
                                     "finite numbers")

    # ---------------- (de)serialisation ----------------
    def to_dict(self) -> dict:
        _grids = self.effective_grids()
        return {
            "name": self.name,
            "materials": {k: v.to_dict() for k, v in self.materials.items()},
            "sections": {k: v.to_dict() for k, v in self.sections.items()},
            "designer_sections": {k: v.to_dict()
                                  for k, v in
                                  self.designer_sections.items()},
            "shell_sections": {k: v.to_dict()
                               for k, v in self.shell_sections.items()},
            # v0.14: emit BOTH the primary/legacy `grid` (for old readers) and
            # the full `grid_systems` list.
            "grid": (_grids[0].to_dict() if _grids else None),
            "grid_systems": [g.to_dict() for g in _grids],
            "stories": [s.to_dict() for s in self.stories],
            "members": [m.to_dict() for m in self.members],
            "shells": [r.to_dict() for r in self.shells],
            "base_fixity": self.base_fixity,
            "supports": [s.to_dict() for s in self.supports],
            "spring_supports": [s.to_dict() for s in self.spring_supports],
            "thermal_alpha": self.thermal_alpha,
            "nodal_masses": [m.to_dict() for m in self.nodal_masses],
            "rigid_diaphragms": self.rigid_diaphragms,
            "diaphragm": self.diaphragm,
            "story_diaphragm": dict(self.story_diaphragm),
            "links": [lk.to_dict() for lk in self.links],
            "story_masses": self.compute_story_masses(),
            "mass_from_patterns": dict(self.mass_from_patterns),
            "mass_source": dict(self.mass_source),
            "patterns": {k: v.to_dict() for k, v in self.patterns.items()},
            "cases": {k: v.to_dict() for k, v in self.cases.items()},
            "combos": {k: v.to_dict() for k, v in self.combos.items()},
            "rs_cases": {k: v.to_dict() for k, v in self.rs_cases.items()},
            "th_cases": {k: v.to_dict() for k, v in self.th_cases.items()},
            "pushover_cases": {k: v.to_dict()
                               for k, v in self.pushover_cases.items()},
            "staged_cases": {k: v.to_dict()
                             for k, v in self.staged_cases.items()},
            "buckling_cases": {k: v.to_dict()
                               for k, v in self.buckling_cases.items()},
            "rs_combos": {k: dict(v) for k, v in self.rs_combos.items()},
            "section_cuts": [c.to_dict() for c in self.section_cuts],
            "spectrum_functions": {k: v.to_dict() for k, v in
                                   self.spectrum_functions.items()},
            "th_functions": {k: v.to_dict()
                             for k, v in self.th_functions.items()},
            "auto_pier_walls": self.auto_pier_walls,
            "deflection_limit": self.deflection_limit,
            "panel_zones": self.panel_zones,
            "num_modes": self.num_modes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BuildingModel":
        """Rebuild a model from ``to_dict()`` output (exact round trip).

        ``story_masses`` from the dict are kept as *explicit* masses so a
        round-tripped model reports the exact same masses it was saved with.
        Derived fields (section G, member length, grid labels) are ignored.
        Raises ``ValueError``/``TypeError``/``KeyError`` on malformed input.
        """
        if not isinstance(d, dict):
            raise ValueError("model must be a JSON object")
        mdl = cls(name=str(d.get("name", "Untitled Building")))
        for name, md in (d.get("materials") or {}).items():
            mdl.materials[name] = Material(
                name=md.get("name", name), E=float(md["E"]),
                nu=float(md.get("nu", 0.2)),
                unit_weight=float(md.get("unit_weight", 24.0)))
        for name, sd in (d.get("sections") or {}).items():
            mdl.sections[name] = FrameSection(
                name=sd.get("name", name), material=sd["material"],
                A=float(sd["A"]), I33=float(sd["I33"]), I22=float(sd["I22"]),
                J=float(sd["J"]), b=float(sd.get("b", 0.0)),
                h=float(sd.get("h", 0.0)),
                mod_A=float(sd.get("mod_A", 1.0)),
                mod_I33=float(sd.get("mod_I33", 1.0)),
                mod_I22=float(sd.get("mod_I22", 1.0)),
                mod_J=float(sd.get("mod_J", 1.0)))
        if d.get("designer_sections"):
            from skyframe.core.sections_designer import DesignerSection
            for name, dd in d["designer_sections"].items():
                ds = DesignerSection.from_dict(dd)
                ds.name = ds.name or name
                mdl.designer_sections[name] = ds
        for name, sd in (d.get("shell_sections") or {}).items():
            mdl.shell_sections[name] = ShellSection(
                name=sd.get("name", name), material=sd["material"],
                thickness=float(sd["thickness"]),
                mod=float(sd.get("mod", 1.0)))
        # v0.14 grid systems: prefer the full `grid_systems` list; otherwise
        # wrap a legacy single `grid` as a one-element list.  Both `grid`
        # (primary) and `grid_systems` are populated so all readers work.
        gs_list = d.get("grid_systems")
        if gs_list:
            mdl.grid_systems = [GridSystem.from_dict(g) for g in gs_list]
            mdl.grid = mdl.grid_systems[0]
        else:
            gd = d.get("grid")
            if gd:
                g = GridSystem.from_dict(gd)
                mdl.grid = g
                mdl.grid_systems = [g]
        for sd in d.get("stories") or []:
            mdl.stories.append(Story(sd["name"], float(sd["height"]),
                                     float(sd.get("elevation", 0.0))))
        for md in d.get("members") or []:
            mdl.members.append(FrameMember(
                uid=md["uid"], kind=md["kind"], section=md["section"],
                pi=tuple(float(v) for v in md["pi"]),
                pj=tuple(float(v) for v in md["pj"]),
                story=md.get("story", ""),
                releases=md.get("releases", ""),
                angle=float(md.get("angle", 0.0)),
                rigid_i=float(md.get("rigid_i", 0.0)),
                rigid_j=float(md.get("rigid_j", 0.0)),
                rigid_factor=float(md.get("rigid_factor", 1.0)),
                foundation_ks=float(md.get("foundation_ks", 0.0)),
                foundation_width=float(md.get("foundation_width", 0.0)),
                axial_limit=str(md.get("axial_limit", "both")),
                hinges=str(md.get("hinges", "none"))))
        for rd in d.get("shells") or []:
            mdl.shells.append(ShellRegion(
                uid=rd["uid"], kind=rd["kind"], behavior=rd["behavior"],
                section=rd.get("section", ""),
                corners=[tuple(float(v) for v in c) for c in rd["corners"]],
                mesh_size=float(rd.get("mesh_size", 1.0)),
                story=rd.get("story", ""),
                openings=[Opening(float(o["u0"]), float(o["v0"]),
                                  float(o["u1"]), float(o["v1"]))
                          for o in (rd.get("openings") or [])],
                pier=str(rd.get("pier", ""))))
        mdl.base_fixity = d.get("base_fixity", "fixed")
        if mdl.base_fixity not in ("fixed", "pinned"):
            raise ValueError(f"base_fixity must be fixed|pinned, got "
                             f"{mdl.base_fixity!r}")
        for sd in d.get("supports") or []:
            r = [int(bool(v)) for v in sd["restraints"]]
            if len(r) != 6:
                raise ValueError("support restraints must have 6 entries")
            mdl.supports.append(PointSupport(
                tuple(float(v) for v in sd["point"]), tuple(r)))
        for sd in d.get("spring_supports") or []:
            mdl.spring_supports.append(SpringSupport(
                tuple(float(v) for v in sd["point"]),
                [float(k) for k in sd["stiffness"]]))
        mdl.thermal_alpha = float(d.get("thermal_alpha", 1.2e-5))
        for md in d.get("nodal_masses") or []:
            mdl.nodal_masses.append(NodalMass(
                tuple(float(v) for v in md["point"]),
                mx=float(md.get("mx", 0.0)), my=float(md.get("my", 0.0)),
                mz=float(md.get("mz", 0.0))))
        mdl.rigid_diaphragms = bool(d.get("rigid_diaphragms", True))
        # v0.5 diaphragm option; pre-v0.5 files keep the legacy boolean only
        mdl.diaphragm = str(d.get("diaphragm", "rigid"))
        mdl.story_diaphragm = {str(k): str(v) for k, v in
                               (d.get("story_diaphragm") or {}).items()}
        for ld in d.get("links") or []:
            mdl.links.append(LinkMember(
                uid=ld["uid"],
                pi=tuple(float(v) for v in ld["pi"]),
                pj=tuple(float(v) for v in ld["pj"]),
                stiffness=[float(k) for k in ld["stiffness"]],
                link_type=str(ld.get("link_type", "elastic")),
                params=LinkMember.coerce_params(ld.get("params"))))
        mdl.story_masses = {k: float(v)
                            for k, v in (d.get("story_masses") or {}).items()}
        mdl.mass_from_patterns = {
            k: float(v) for k, v in (d.get("mass_from_patterns") or {}).items()}
        # v0.4: mass_source; when absent (pre-v0.4 files) it stays empty and
        # compute_story_masses falls back to mass_from_patterns
        mdl.mass_source = {
            k: float(v) for k, v in (d.get("mass_source") or {}).items()}
        for name, pd in (d.get("patterns") or {}).items():
            pat = LoadPattern(pd.get("name", name), pd.get("kind", "other"))
            # v0.7 self-weight factor; absent (pre-v0.7 files) => 0.0
            pat.self_weight_factor = float(pd.get("self_weight_factor", 0.0))
            # v0.8 accidental torsion + thermal loads (absent = defaults)
            pat.accidental_torsion = bool(pd.get("accidental_torsion", False))
            pat.ecc = float(pd.get("ecc", 0.05))
            for t in pd.get("thermal_loads") or []:
                pat.thermal_loads.append(ThermalLoad(
                    t["member_uid"], float(t.get("dT", 0.0))))
            for u in pd.get("member_udls") or []:
                pat.member_udls.append(MemberUDL(u["member_uid"],
                                                 float(u["w"])))
            for n in pd.get("nodal_loads") or []:
                pat.nodal_loads.append(NodalLoad(
                    tuple(float(v) for v in n["point"]),
                    fx=float(n.get("fx", 0.0)), fy=float(n.get("fy", 0.0)),
                    fz=float(n.get("fz", 0.0))))
            for s in pd.get("story_forces") or []:
                pat.story_forces.append(StoryForce(
                    s["story"], fx=float(s.get("fx", 0.0)),
                    fy=float(s.get("fy", 0.0))))
            for m in pd.get("member_loads") or []:
                pat.member_loads.append(MemberLoad(
                    member_uid=m["member_uid"], kind=m.get("kind", "udl"),
                    w=float(m.get("w", 0.0)), w2=float(m.get("w2", 0.0)),
                    a=float(m.get("a", 0.0)), b=float(m.get("b", 1.0)),
                    direction=m.get("direction", "gravity")))
            for a in pd.get("area_loads") or []:
                pat.area_loads.append(AreaLoad(a["region_uid"],
                                               float(a["q"])))
            mdl.patterns[name] = pat
        for name, cd in (d.get("cases") or {}).items():
            pg = cd.get("pdelta_gravity")
            mdl.cases[name] = LoadCase(
                cd.get("name", name),
                {p: float(f) for p, f in (cd.get("patterns") or {}).items()},
                pdelta=bool(cd.get("pdelta", False)),
                pdelta_gravity=({p: float(f) for p, f in pg.items()}
                                if pg else None))
        for name, cd in (d.get("combos") or {}).items():
            mdl.combos[name] = LoadCombo(
                cd.get("name", name),
                {c: float(f) for c, f in (cd.get("cases") or {}).items()},
                combo_type=cd.get("combo_type", "add"))
        # v0.13 named function library (loaded BEFORE cases so a case that
        # references a function passes validation)
        for name, fd in (d.get("spectrum_functions") or {}).items():
            mdl.spectrum_functions[name] = SpectrumFunction(
                name=fd.get("name", name),
                points=[[float(p[0]), float(p[1])] for p in fd["points"]],
                damping=float(fd.get("damping", 0.05)))
        for name, fd in (d.get("th_functions") or {}).items():
            mdl.th_functions[name] = TimeHistoryFunction(
                name=fd.get("name", name),
                values=[float(v) for v in fd["values"]],
                dt=float(fd["dt"]))
        for name, rd in (d.get("rs_cases") or {}).items():
            mdl.rs_cases[name] = ResponseSpectrumCase(
                name=rd.get("name", name), direction=rd["direction"],
                spectrum=[[float(p[0]), float(p[1])]
                          for p in (rd.get("spectrum") or [])],
                num_modes=int(rd.get("num_modes", 0)),
                combo_method=rd.get("combo_method", "CQC"),
                damping=float(rd.get("damping", 0.05)),
                scale=float(rd.get("scale", 1.0)),
                function=str(rd.get("function", "")))
        for name, td in (d.get("th_cases") or {}).items():
            tmy = td.get("default_My")
            mdl.th_cases[name] = TimeHistoryCase(
                name=td.get("name", name), direction=td["direction"],
                accel=[float(a) for a in (td.get("accel") or [])],
                dt=float(td.get("dt", 0.0)),
                damping=float(td.get("damping", 0.05)),
                scale=float(td.get("scale", 1.0)),
                # v0.6 nonlinear fields; pre-v0.6 files stay linear
                nonlinear=bool(td.get("nonlinear", False)),
                gravity={p: float(f)
                         for p, f in (td.get("gravity") or {}).items()},
                hinges=td.get("hinges", "column_base"),
                My={u: float(v) for u, v in (td.get("My") or {}).items()},
                default_My=(None if tmy is None else float(tmy)),
                hardening=float(td.get("hardening", 0.02)),
                function=str(td.get("function", "")))
        for name, pd in (d.get("pushover_cases") or {}).items():
            dmy = pd.get("default_My")
            mdl.pushover_cases[name] = PushoverCase(
                name=pd.get("name", name), direction=pd["direction"],
                gravity={p: float(f)
                         for p, f in (pd.get("gravity") or {}).items()},
                target_drift=float(pd.get("target_drift", 0.02)),
                steps=int(pd.get("steps", 100)),
                hinges=pd.get("hinges", "column_base"),
                My={u: float(v) for u, v in (pd.get("My") or {}).items()},
                default_My=(None if dmy is None else float(dmy)),
                hardening=float(pd.get("hardening", 0.02)),
                hinge_params={k: float(v) for k, v in
                              (pd.get("hinge_params") or {}).items()})
        for name, sd in (d.get("staged_cases") or {}).items():
            mdl.staged_cases[name] = StagedCase(
                name=sd.get("name", name),
                pattern=sd.get("pattern", "DEAD"),
                stages=sd.get("stages", "per_story"),
                include_live={p: float(f) for p, f in
                              (sd.get("include_live") or {}).items()})
        for name, bd in (d.get("buckling_cases") or {}).items():
            mdl.buckling_cases[name] = BucklingCase(
                name=bd.get("name", name),
                gravity={p: float(f)
                         for p, f in (bd.get("gravity") or {}).items()},
                num_modes=int(bd.get("num_modes", 6)))
        for name, cd in (d.get("rs_combos") or {}).items():
            mdl.rs_combos[name] = {"name_x": str(cd["name_x"]),
                                   "name_y": str(cd["name_y"]),
                                   "method": str(cd.get("method", "100_30"))}

        def _rng(r):
            return None if r is None else [float(r[0]), float(r[1])]
        for cd in d.get("section_cuts") or []:
            mdl.section_cuts.append(SectionCut(
                name=cd["name"], axis=cd["axis"], coord=float(cd["coord"]),
                x_range=_rng(cd.get("x_range")),
                y_range=_rng(cd.get("y_range")),
                z_range=_rng(cd.get("z_range"))))
        mdl.auto_pier_walls = bool(d.get("auto_pier_walls", False))
        # v0.16: serviceability deflection limit (absent = pre-v0.16 default)
        mdl.deflection_limit = float(d.get("deflection_limit", 360.0))
        # v0.17: panel-zone assumption (absent = pre-v0.17 centerline model)
        mdl.panel_zones = str(d.get("panel_zones", "none"))
        mdl.num_modes = int(d.get("num_modes", 6))
        mdl.validate()
        return mdl


# --------------------------------------------------------------------------- #
# v0.10 notional loads (AISC 360 direct-analysis stability)
# --------------------------------------------------------------------------- #
NOTIONAL_DIRECTIONS = ("X", "Y")


def story_gravity_loads(model: "BuildingModel",
                        pattern_name: str) -> Dict[str, float]:
    """Total vertical gravity load (kN, downward positive) per story from one
    load pattern.

    Uses the SAME contributions as :meth:`BuildingModel.compute_story_masses`
    (beams/area/nodal loads + beam & shell self-weight on the story; columns
    are excluded, matching the member-UDL rule), but for a SINGLE pattern at
    unit factor and returned in kN (not converted to mass).
    """
    out: Dict[str, float] = {s.name: 0.0 for s in model.stories}
    pat = model.patterns.get(pattern_name)
    if pat is None:
        return out
    for s in model.stories:
        total = 0.0
        for udl in pat.member_udls:
            m = model._member(udl.member_uid)
            if m is not None and m.story == s.name and m.kind != "column":
                total += udl.w * m.length
        for ml in pat.member_loads:
            if ml.direction not in ("gravity", "global_z"):
                continue
            m = model._member(ml.member_uid)
            if m is None or m.story != s.name or m.kind == "column":
                continue
            if ml.kind == "point":
                total += ml.w
            elif ml.kind == "udl":
                total += ml.w * (ml.b - ml.a) * m.length
            else:  # trapezoid
                total += 0.5 * (ml.w + ml.w2) * (ml.b - ml.a) * m.length
        for al in pat.area_loads:
            region = model._shell(al.region_uid)
            if region is not None and model._region_on_story(region, s):
                total += al.q * region.net_area
        for nl in pat.nodal_loads:
            if abs(nl.point[2] - s.elevation) < 1e-6:
                total += -nl.fz                       # downward = -fz
        swf = getattr(pat, "self_weight_factor", 0.0)
        if swf:
            for m in model.members:
                if m.story != s.name or m.kind == "column":
                    continue
                sec = model.sections.get(m.section)
                mat = model.materials.get(sec.material) if sec else None
                if sec is not None and mat is not None:
                    total += swf * sec.A * mat.unit_weight * m.length
            for region in model.shells:
                if not model._region_on_story(region, s):
                    continue
                ssec = model.shell_sections.get(region.section)
                mat = model.materials.get(ssec.material) if ssec else None
                if ssec is not None and mat is not None:
                    total += swf * ssec.thickness * mat.unit_weight \
                        * region.net_area
        out[s.name] = total
    return out


def make_notional_pattern(model: "BuildingModel", name: str,
                          direction: str = "X", coeff: float = 0.002,
                          gravity_pattern: str = "DEAD") -> LoadPattern:
    """AISC 360 notional-load pattern (direct-analysis / stability).

    Applies a lateral story force ``Ni = coeff * W_story`` at each story,
    where ``W_story`` is the vertical gravity load tributary to that level
    from ``gravity_pattern`` (see :func:`story_gravity_loads`).  The result
    is a kind-``"notional"`` story-force :class:`LoadPattern` (``fx`` for
    direction ``"X"``, ``fy`` for ``"Y"``) stored under ``name`` (replacing
    an existing pattern) and returned.  ``sum(Ni) == coeff * W_total``.
    """
    if direction not in NOTIONAL_DIRECTIONS:
        raise ValueError(f"direction must be X|Y, got {direction!r}")
    if not (isinstance(coeff, (int, float)) and math.isfinite(coeff)):
        raise ValueError("coeff must be a finite number")
    if gravity_pattern not in model.patterns:
        raise ValueError(f"unknown gravity pattern {gravity_pattern!r}")
    loads = story_gravity_loads(model, gravity_pattern)
    pat = LoadPattern(name, "notional")
    for s in model.stories:
        Ni = coeff * loads[s.name]
        pat.story_forces.append(StoryForce(
            s.name,
            fx=Ni if direction == "X" else 0.0,
            fy=Ni if direction == "Y" else 0.0))
    model.patterns[name] = pat
    return pat


# --------------------------------------------------------------------------- #
# v0.13 Eurocode 8 elastic response spectrum (EN 1998-1 §3.2.2.2, Type 1)
# --------------------------------------------------------------------------- #
# Table 3.2 (Type 1, 5%-damped horizontal elastic spectrum): ground type ->
# (S, TB [s], TC [s], TD [s]).
EC8_TYPE1_GROUND: Dict[str, Tuple[float, float, float, float]] = {
    "A": (1.00, 0.15, 0.4, 2.0),
    "B": (1.20, 0.15, 0.5, 2.0),
    "C": (1.15, 0.20, 0.6, 2.0),
    "D": (1.35, 0.20, 0.8, 2.0),
    "E": (1.40, 0.15, 0.5, 2.0),
}


def eurocode8_damping_correction(damping: float = 0.05) -> float:
    """EC8 damping correction factor ``eta = sqrt(10/(5+xi)) >= 0.55``.

    ``damping`` is the viscous damping RATIO (0.05 = 5%); ``xi`` in the
    formula is that ratio in PERCENT (EN 1998-1 §3.2.2.2(3), eq. 3.6).  For
    5% damping ``eta == 1``.
    """
    xi = float(damping) * 100.0
    return max(math.sqrt(10.0 / (5.0 + xi)), 0.55)


def eurocode8_se(T: float, ag: float, S: float, TB: float, TC: float,
                 TD: float, eta: float) -> float:
    """EC8 elastic horizontal spectral acceleration ``Se(T)`` (EN 1998-1
    §3.2.2.2, eqs. 3.2-3.5), in the same units as ``ag``.

    * ``0 <= T <= TB``:  ``ag*S*[1 + T/TB*(eta*2.5 - 1)]``
    * ``TB <= T <= TC``: ``ag*S*eta*2.5``                  (constant-accel plateau)
    * ``TC <= T <= TD``: ``ag*S*eta*2.5*(TC/T)``           (constant velocity)
    * ``T  >= TD``:      ``ag*S*eta*2.5*(TC*TD/T^2)``       (constant displacement)
    """
    T = float(T)
    if T <= TB:
        return ag * S * (1.0 + T / TB * (eta * 2.5 - 1.0))
    if T <= TC:
        return ag * S * eta * 2.5
    if T <= TD:
        return ag * S * eta * 2.5 * (TC / T)
    return ag * S * eta * 2.5 * (TC * TD / (T * T))


def eurocode8_spectrum(ag: float, ground_type: str = "A",
                       damping: float = 0.05, T_max: float = 4.0,
                       dT: float = 0.05) -> List[List[float]]:
    """EC8 Type-1 elastic response spectrum as ``[[T, Sa], ...]`` points.

    ``ag`` is the design ground acceleration expressed in units of **g** (so
    the returned ``Sa`` values are in g, ready for a
    :class:`ResponseSpectrumCase` / :class:`SpectrumFunction`).  ``ground_type``
    selects the S/TB/TC/TD row of EN 1998-1 Table 3.2 (Type 1).  The spectrum
    is sampled on a uniform ``dT`` grid AND at every corner period (0, TB, TC,
    2*TC, TD, T_max) so the exact closed-form value is present at each corner
    (the descending branches are hyperbolic; the fine grid keeps the engine's
    linear interpolation accurate between samples).
    """
    gt = str(ground_type).upper()
    if gt not in EC8_TYPE1_GROUND:
        raise ValueError(f"EC8 ground_type must be one of "
                         f"{sorted(EC8_TYPE1_GROUND)}, got {ground_type!r}")
    if not (isinstance(ag, (int, float)) and math.isfinite(ag) and ag >= 0.0):
        raise ValueError("ag must be a finite value >= 0 (in units of g)")
    if not (math.isfinite(T_max) and T_max > 0.0):
        raise ValueError("T_max must be > 0")
    if not (math.isfinite(dT) and dT > 0.0):
        raise ValueError("dT must be > 0")
    S, TB, TC, TD = EC8_TYPE1_GROUND[gt]
    eta = eurocode8_damping_correction(damping)
    ts = set()
    t = 0.0
    while t <= T_max + 1e-12:
        ts.add(round(t, 10))
        t += dT
    for corner in (0.0, TB, TC, 2.0 * TC, TD, T_max):
        if 0.0 <= corner <= T_max + 1e-12:
            ts.add(round(float(corner), 10))
    return [[float(tt), float(eurocode8_se(tt, ag, S, TB, TC, TD, eta))]
            for tt in sorted(ts)]
