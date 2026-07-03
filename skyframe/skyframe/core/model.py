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

    @property
    def length(self) -> float:
        return math.dist(self.pi, self.pj)

    def to_dict(self) -> dict:
        return {"uid": self.uid, "kind": self.kind, "section": self.section,
                "pi": list(self.pi), "pj": list(self.pj), "story": self.story,
                "length": self.length}


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
@dataclass
class MemberUDL:
    """Uniform gravity load on a member (kN/m, positive downward)."""
    member_uid: str
    w: float


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

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind,
            "member_udls": [asdict(u) for u in self.member_udls],
            "nodal_loads": [asdict(n) for n in self.nodal_loads],
            "story_forces": [asdict(s) for s in self.story_forces],
        }


@dataclass
class LoadCase:
    """Linear static case: scaled sum of load patterns."""

    name: str
    patterns: Dict[str, float]  # pattern name -> scale factor

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LoadCombo:
    """Linear combination of load cases (result superposition)."""

    name: str
    cases: Dict[str, float]  # case name -> factor

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
    grid: Optional[GridSystem] = None
    stories: List[Story] = field(default_factory=list)        # bottom -> top
    members: List[FrameMember] = field(default_factory=list)
    base_fixity: str = "fixed"                                 # "fixed" | "pinned"
    supports: List[PointSupport] = field(default_factory=list)
    nodal_masses: List[NodalMass] = field(default_factory=list)
    rigid_diaphragms: bool = True
    story_masses: Dict[str, float] = field(default_factory=dict)   # tonne (explicit)
    mass_from_patterns: Dict[str, float] = field(default_factory=dict)  # pattern -> factor
    patterns: Dict[str, LoadPattern] = field(default_factory=dict)
    cases: Dict[str, LoadCase] = field(default_factory=dict)
    combos: Dict[str, LoadCombo] = field(default_factory=dict)
    num_modes: int = 6

    # ---------------- convenience API ----------------
    def add_material(self, mat: Material) -> Material:
        self.materials[mat.name] = mat
        return mat

    def add_section(self, sec: FrameSection) -> FrameSection:
        if sec.material not in self.materials:
            raise ValueError(f"Section {sec.name}: unknown material {sec.material}")
        self.sections[sec.name] = sec
        return sec

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
                   story: str = "", uid: str = "") -> FrameMember:
        if section not in self.sections:
            raise ValueError(f"Unknown section {section}")
        uid = uid or f"{kind[0].upper()}{len(self.members) + 1}"
        m = FrameMember(uid, kind, section, tuple(pi), tuple(pj), story)
        if m.length < 1e-9:
            raise ValueError(f"Member {uid} has zero length")
        self.members.append(m)
        return m

    def pattern(self, name: str, kind: str = "other") -> LoadPattern:
        if name not in self.patterns:
            self.patterns[name] = LoadPattern(name, kind)
        return self.patterns[name]

    def add_case(self, name: str, patterns: Dict[str, float]) -> LoadCase:
        for p in patterns:
            if p not in self.patterns:
                raise ValueError(f"Case {name}: unknown pattern {p}")
        c = LoadCase(name, dict(patterns))
        self.cases[name] = c
        return c

    def add_combo(self, name: str, cases: Dict[str, float]) -> LoadCombo:
        for c in cases:
            if c not in self.cases:
                raise ValueError(f"Combo {name}: unknown case {c}")
        cb = LoadCombo(name, dict(cases))
        self.combos[name] = cb
        return cb

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

    def compute_story_masses(self) -> Dict[str, float]:
        """Story mass in tonnes: explicit masses win; otherwise derived from
        gravity load patterns via `mass_from_patterns` (e.g. {"DEAD": 1.0})."""
        masses: Dict[str, float] = {}
        for s in self.stories:
            if s.name in self.story_masses:
                masses[s.name] = self.story_masses[s.name]
                continue
            total_w = 0.0  # kN of gravity load on this story
            for pname, fac in self.mass_from_patterns.items():
                pat = self.patterns.get(pname)
                if pat is None:
                    continue
                for udl in pat.member_udls:
                    m = self._member(udl.member_uid)
                    if m is not None and m.story == s.name and m.kind != "column":
                        total_w += fac * udl.w * m.length
                for nl in pat.nodal_loads:
                    if abs(nl.point[2] - s.elevation) < 1e-6:
                        total_w += fac * (-nl.fz)  # downward = -fz
            masses[s.name] = total_w / G_ACCEL
        return masses

    def _member(self, uid: str) -> Optional[FrameMember]:
        for m in self.members:
            if m.uid == uid:
                return m
        return None

    # ---------------- (de)serialisation ----------------
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "materials": {k: v.to_dict() for k, v in self.materials.items()},
            "sections": {k: v.to_dict() for k, v in self.sections.items()},
            "grid": self.grid.to_dict() if self.grid else None,
            "stories": [s.to_dict() for s in self.stories],
            "members": [m.to_dict() for m in self.members],
            "base_fixity": self.base_fixity,
            "supports": [s.to_dict() for s in self.supports],
            "nodal_masses": [m.to_dict() for m in self.nodal_masses],
            "rigid_diaphragms": self.rigid_diaphragms,
            "story_masses": self.compute_story_masses(),
            "patterns": {k: v.to_dict() for k, v in self.patterns.items()},
            "cases": {k: v.to_dict() for k, v in self.cases.items()},
            "combos": {k: v.to_dict() for k, v in self.combos.items()},
            "num_modes": self.num_modes,
        }
