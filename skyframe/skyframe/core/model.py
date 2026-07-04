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


@dataclass
class ShellSection:
    """Shell section (maps to OpenSees ElasticMembranePlateSection).

    ``mod`` (v0.4) is a single stiffness modifier (> 0) applied by scaling
    the section modulus E.  ElasticMembranePlateSection has ONE modulus, so
    membrane and flexural stiffness scale together; independent
    membrane/flexural modifiers are deliberately not offered in v0.4 (an
    exact split is impossible with this section type).
    """

    name: str
    material: str
    thickness: float  # m
    mod: float = 1.0  # v0.4 stiffness modifier (scales E)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
@dataclass
class GridSystem:
    """Orthogonal grid: x_lines/y_lines are sorted coordinates in metres."""

    x_lines: List[float]
    y_lines: List[float]

    @property
    def x_labels(self) -> List[str]:
        return [chr(ord("A") + i) for i in range(len(self.x_lines))]

    @property
    def y_labels(self) -> List[str]:
        return [str(i + 1) for i in range(len(self.y_lines))]

    def to_dict(self) -> dict:
        return {"x_lines": self.x_lines, "y_lines": self.y_lines,
                "x_labels": self.x_labels, "y_labels": self.y_labels}


@dataclass
class Story:
    name: str
    height: float           # m (story height below this level's floor? no: height of this story)
    elevation: float = 0.0  # m, computed = top-of-story elevation

    def to_dict(self) -> dict:
        return asdict(self)


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

    @property
    def length(self) -> float:
        return math.dist(self.pi, self.pj)

    def release_tokens(self) -> set:
        """Parsed set of release tokens (subset of {"Mi", "Mj"})."""
        return {t.strip() for t in self.releases.split(",") if t.strip()}

    def to_dict(self) -> dict:
        return {"uid": self.uid, "kind": self.kind, "section": self.section,
                "pi": list(self.pi), "pj": list(self.pj), "story": self.story,
                "releases": self.releases, "angle": self.angle,
                "length": self.length}


@dataclass
class ShellRegion:
    """Planar 4-corner quad region: a wall or a slab.

    ``behavior == "shell"``    — meshed into ShellMITC4 finite elements.
    ``behavior == "membrane"`` — slabs only: never meshed; its area loads are
    distributed to the edge beams by two-way (45 degree) tributary areas.
    Corners must be planar and listed counter-clockwise.
    """

    uid: str
    kind: str                              # "wall" | "slab"
    behavior: str                          # "shell" | "membrane"
    section: str                           # ShellSection name (membrane: unused)
    corners: List[Tuple[float, float, float]]
    mesh_size: float = 1.0                 # m, target FE size (shell behavior)
    story: str = ""

    @property
    def area(self) -> float:
        return _polygon_area3d([tuple(c) for c in self.corners])

    def to_dict(self) -> dict:
        return {"uid": self.uid, "kind": self.kind, "behavior": self.behavior,
                "section": self.section,
                "corners": [list(c) for c in self.corners],
                "mesh_size": self.mesh_size, "story": self.story}


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
    kind: str = "other"  # "dead" | "live" | "quake" | "other"
    member_udls: List[MemberUDL] = field(default_factory=list)
    nodal_loads: List[NodalLoad] = field(default_factory=list)
    story_forces: List[StoryForce] = field(default_factory=list)
    member_loads: List[MemberLoad] = field(default_factory=list)
    area_loads: List[AreaLoad] = field(default_factory=list)

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
    """

    name: str
    direction: str                       # "X" | "Y"
    spectrum: List[List[float]]          # [[T, Sa(g)], ...]
    num_modes: int = 0                   # 0 = all computed modes
    combo_method: str = "CQC"            # "CQC" | "SRSS"
    damping: float = 0.05
    scale: float = 1.0

    def to_dict(self) -> dict:
        return {"name": self.name, "direction": self.direction,
                "spectrum": [[float(t), float(sa)] for t, sa in self.spectrum],
                "num_modes": self.num_modes,
                "combo_method": self.combo_method,
                "damping": self.damping, "scale": self.scale}


TH_DIRECTIONS = ("X", "Y")


@dataclass
class TimeHistoryCase:
    """Linear time-history case (v0.4): uniform ground acceleration.

    ``accel`` is the ground-acceleration record in m/s^2 sampled at constant
    spacing ``dt`` (the sample at index k applies at t = k*dt); ``scale``
    multiplies the record.  The engine integrates the elastic model with
    Newmark constant-average acceleration (gamma=1/2, beta=1/4,
    unconditionally stable) at the record dt, with Rayleigh damping fitted
    to ratio ``damping`` at modes 1 and min(3, n_modes).
    """

    name: str
    direction: str                 # "X" | "Y"
    accel: List[float]             # m/s^2, sampled at dt
    dt: float                      # s
    damping: float = 0.05          # Rayleigh target damping ratio
    scale: float = 1.0             # multiplies accel

    def to_dict(self) -> dict:
        return {"name": self.name, "direction": self.direction,
                "accel": [float(a) for a in self.accel],
                "dt": self.dt, "damping": self.damping, "scale": self.scale}


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
# The building
# --------------------------------------------------------------------------- #
@dataclass
class BuildingModel:
    name: str = "Untitled Building"
    materials: Dict[str, Material] = field(default_factory=dict)
    sections: Dict[str, FrameSection] = field(default_factory=dict)
    shell_sections: Dict[str, ShellSection] = field(default_factory=dict)
    grid: Optional[GridSystem] = None
    stories: List[Story] = field(default_factory=list)        # bottom -> top
    members: List[FrameMember] = field(default_factory=list)
    shells: List[ShellRegion] = field(default_factory=list)
    base_fixity: str = "fixed"                                 # "fixed" | "pinned"
    supports: List[PointSupport] = field(default_factory=list)
    nodal_masses: List[NodalMass] = field(default_factory=list)
    rigid_diaphragms: bool = True
    story_masses: Dict[str, float] = field(default_factory=dict)   # tonne (explicit)
    mass_from_patterns: Dict[str, float] = field(default_factory=dict)  # pattern -> factor (legacy)
    mass_source: Dict[str, float] = field(default_factory=dict)  # v0.4: pattern -> factor
    patterns: Dict[str, LoadPattern] = field(default_factory=dict)
    cases: Dict[str, LoadCase] = field(default_factory=dict)
    combos: Dict[str, LoadCombo] = field(default_factory=dict)
    rs_cases: Dict[str, ResponseSpectrumCase] = field(default_factory=dict)
    th_cases: Dict[str, TimeHistoryCase] = field(default_factory=dict)  # v0.4
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
                   releases: str = "", angle: float = 0.0) -> FrameMember:
        if section not in self.sections:
            raise ValueError(f"Unknown section {section}")
        uid = uid or f"{kind[0].upper()}{len(self.members) + 1}"
        m = FrameMember(uid, kind, section,
                        tuple(float(v) for v in pi),
                        tuple(float(v) for v in pj), story,
                        releases=releases, angle=float(angle))
        if m.length < 1e-9:
            raise ValueError(f"Member {uid} has zero length")
        if not m.release_tokens() <= {"Mi", "Mj"}:
            raise ValueError(f"Member {uid}: bad releases {releases!r} "
                             "(tokens must be 'Mi'/'Mj')")
        self.members.append(m)
        return m

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
        self.shell_sections[sec.name] = sec
        return sec

    def add_shell(self, kind: str, behavior: str, section: str,
                  corners: List[Tuple[float, float, float]],
                  mesh_size: float = 1.0, story: str = "",
                  uid: str = "") -> ShellRegion:
        """Add a wall/slab region (validates shape, planarity, references)."""
        uid = uid or f"{'W' if kind == 'wall' else 'S'}{len(self.shells) + 1}"
        region = ShellRegion(uid, kind, behavior, section,
                             [tuple(float(v) for v in c) for c in corners],
                             mesh_size=float(mesh_size), story=story)
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
                raise ValueError(f"Combo {combo.name}: unknown case {c}")

    def add_rs_case(self, name: str, direction: str,
                    spectrum: List[List[float]], num_modes: int = 0,
                    combo_method: str = "CQC", damping: float = 0.05,
                    scale: float = 1.0) -> ResponseSpectrumCase:
        rs = ResponseSpectrumCase(
            name, direction,
            [[float(t), float(sa)] for t, sa in spectrum],
            num_modes=int(num_modes), combo_method=combo_method,
            damping=float(damping), scale=float(scale))
        self._validate_rs_case(rs)
        self.rs_cases[name] = rs
        return rs

    @staticmethod
    def _validate_rs_case(rs: ResponseSpectrumCase) -> None:
        if rs.direction not in RS_DIRECTIONS:
            raise ValueError(f"RS case {rs.name}: direction must be X|Y, "
                             f"got {rs.direction!r}")
        if rs.combo_method not in RS_COMBO_METHODS:
            raise ValueError(f"RS case {rs.name}: combo_method must be "
                             f"CQC|SRSS, got {rs.combo_method!r}")
        if not rs.spectrum:
            raise ValueError(f"RS case {rs.name}: spectrum must have at "
                             "least one [T, Sa] point")
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

    def add_th_case(self, name: str, direction: str, accel: List[float],
                    dt: float, damping: float = 0.05,
                    scale: float = 1.0) -> TimeHistoryCase:
        th = TimeHistoryCase(name, direction,
                             [float(a) for a in accel], float(dt),
                             damping=float(damping), scale=float(scale))
        self._validate_th_case(th)
        self.th_cases[name] = th
        return th

    @staticmethod
    def _validate_th_case(th: TimeHistoryCase) -> None:
        if th.direction not in TH_DIRECTIONS:
            raise ValueError(f"TH case {th.name}: direction must be X|Y, "
                             f"got {th.direction!r}")
        if not th.accel:
            raise ValueError(f"TH case {th.name}: accel record is empty")
        for a in th.accel:
            if not math.isfinite(float(a)):
                raise ValueError(f"TH case {th.name}: accel values must be "
                                 "finite")
        if not (math.isfinite(th.dt) and th.dt > 0.0):
            raise ValueError(f"TH case {th.name}: dt must be > 0")
        if not 0.0 < th.damping < 1.0:
            raise ValueError(f"TH case {th.name}: damping must be in (0, 1)")
        if not math.isfinite(th.scale):
            raise ValueError(f"TH case {th.name}: scale must be finite")

    # ---------------- derived data ----------------
    def story_elevations(self) -> Dict[str, float]:
        return {s.name: s.elevation for s in self.stories}

    def plan_extents(self) -> Tuple[float, float]:
        if self.grid and self.grid.x_lines and self.grid.y_lines:
            return (self.grid.x_lines[-1] - self.grid.x_lines[0],
                    self.grid.y_lines[-1] - self.grid.y_lines[0])
        xs = [p[0] for m in self.members for p in (m.pi, m.pj)] or [0.0]
        ys = [p[1] for m in self.members for p in (m.pi, m.pj)] or [0.0]
        return (max(xs) - min(xs), max(ys) - min(ys))

    def plan_center(self) -> Tuple[float, float]:
        if self.grid and self.grid.x_lines and self.grid.y_lines:
            return ((self.grid.x_lines[0] + self.grid.x_lines[-1]) / 2.0,
                    (self.grid.y_lines[0] + self.grid.y_lines[-1]) / 2.0)
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
                    total_w += fac * al.q * region.area
                for nl in pat.nodal_loads:
                    if abs(nl.point[2] - s.elevation) < 1e-6:
                        total_w += fac * (-nl.fz)  # downward = -fz
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

    # ---------------- (de)serialisation ----------------
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "materials": {k: v.to_dict() for k, v in self.materials.items()},
            "sections": {k: v.to_dict() for k, v in self.sections.items()},
            "shell_sections": {k: v.to_dict()
                               for k, v in self.shell_sections.items()},
            "grid": self.grid.to_dict() if self.grid else None,
            "stories": [s.to_dict() for s in self.stories],
            "members": [m.to_dict() for m in self.members],
            "shells": [r.to_dict() for r in self.shells],
            "base_fixity": self.base_fixity,
            "supports": [s.to_dict() for s in self.supports],
            "nodal_masses": [m.to_dict() for m in self.nodal_masses],
            "rigid_diaphragms": self.rigid_diaphragms,
            "story_masses": self.compute_story_masses(),
            "mass_from_patterns": dict(self.mass_from_patterns),
            "mass_source": dict(self.mass_source),
            "patterns": {k: v.to_dict() for k, v in self.patterns.items()},
            "cases": {k: v.to_dict() for k, v in self.cases.items()},
            "combos": {k: v.to_dict() for k, v in self.combos.items()},
            "rs_cases": {k: v.to_dict() for k, v in self.rs_cases.items()},
            "th_cases": {k: v.to_dict() for k, v in self.th_cases.items()},
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
        for name, sd in (d.get("shell_sections") or {}).items():
            mdl.shell_sections[name] = ShellSection(
                name=sd.get("name", name), material=sd["material"],
                thickness=float(sd["thickness"]),
                mod=float(sd.get("mod", 1.0)))
        gd = d.get("grid")
        if gd:
            mdl.grid = GridSystem([float(x) for x in gd["x_lines"]],
                                  [float(y) for y in gd["y_lines"]])
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
                angle=float(md.get("angle", 0.0))))
        for rd in d.get("shells") or []:
            mdl.shells.append(ShellRegion(
                uid=rd["uid"], kind=rd["kind"], behavior=rd["behavior"],
                section=rd.get("section", ""),
                corners=[tuple(float(v) for v in c) for c in rd["corners"]],
                mesh_size=float(rd.get("mesh_size", 1.0)),
                story=rd.get("story", "")))
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
        for md in d.get("nodal_masses") or []:
            mdl.nodal_masses.append(NodalMass(
                tuple(float(v) for v in md["point"]),
                mx=float(md.get("mx", 0.0)), my=float(md.get("my", 0.0)),
                mz=float(md.get("mz", 0.0))))
        mdl.rigid_diaphragms = bool(d.get("rigid_diaphragms", True))
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
        for name, rd in (d.get("rs_cases") or {}).items():
            mdl.rs_cases[name] = ResponseSpectrumCase(
                name=rd.get("name", name), direction=rd["direction"],
                spectrum=[[float(p[0]), float(p[1])]
                          for p in rd["spectrum"]],
                num_modes=int(rd.get("num_modes", 0)),
                combo_method=rd.get("combo_method", "CQC"),
                damping=float(rd.get("damping", 0.05)),
                scale=float(rd.get("scale", 1.0)))
        for name, td in (d.get("th_cases") or {}).items():
            mdl.th_cases[name] = TimeHistoryCase(
                name=td.get("name", name), direction=td["direction"],
                accel=[float(a) for a in td["accel"]], dt=float(td["dt"]),
                damping=float(td.get("damping", 0.05)),
                scale=float(td.get("scale", 1.0)))
        mdl.num_modes = int(d.get("num_modes", 6))
        mdl.validate()
        return mdl
