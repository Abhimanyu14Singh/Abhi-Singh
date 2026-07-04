"""IFC (ISO-10303-21 "SPF" / STEP physical file) GEOMETRY-ONLY reader.

Exploratory v0.6 scope, honestly bounded.  The reader parses the DATA
section of an SPF file with a proper STEP tokenizer (nested parentheses,
``'...'`` strings with ``''`` escapes, ``$`` (null), ``*`` (derived),
``#n`` references, ``.ENUM.`` values, typed values like
``IFCLENGTHMEASURE(3.)``) and extracts ONLY:

* ``IFCBUILDINGSTOREY``            — the ``Elevation`` attribute maps the
  file's levels onto SkyFrame stories (story top = storey elevation; the
  LOWEST storey is the base level).
* ``IFCCOLUMN`` / ``IFCBEAM`` / ``IFCMEMBER`` — placed via their
  ``IFCLOCALPLACEMENT`` chain (composed ``IFCAXIS2PLACEMENT3D``
  translations; rotations: IDENTITY and Z-ROTATIONS only — anything else
  warns per element and is treated as unrotated) with geometry from an
  ``IFCEXTRUDEDAREASOLID`` of an ``IFCRECTANGLEPROFILEDEF``
  (XDim/YDim -> section b/h, extrusion Depth -> member length).
  Columns become VERTICAL members (placement point + depth up the global
  Z axis); beams run HORIZONTALLY along their placement's rotated local
  X axis (start at the placement point, length = depth).  ``IFCMEMBER``
  elements are imported the same way as beams but tagged kind
  ``"brace"``.

LIMITATIONS (documented, not silently ignored):

* Geometry only — no materials, loads, rebar, or property sets.  All
  members get one placeholder material ("IFC", E = 25 GPa).
* Coordinates are read AS-IS and assumed to be in METRES; ``IFCSIUNIT``
  is not interpreted (a warning is emitted when the file declares a
  prefixed length unit, e.g. millimetres).
* Rotations other than about global Z, non-vertical column extrusions,
  and mapped/boolean/BRep geometry are not supported (per-element
  warnings; the element is skipped when no extruded rectangle exists).
* Walls, slabs, openings, plates, doors/windows, stairs, footings, etc.
  are counted and reported in the warnings — never imported.
* Members are assigned to the story whose elevation is NEAREST to the
  member's top-end z (no containment analysis).

Everything unexpected lands in the returned ``warnings`` list; malformed
instances are skipped with a warning, never an exception.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from skyframe.core.model import (
    BuildingModel,
    FrameSection,
    GridSystem,
    Material,
    Story,
)

__all__ = ["StepRef", "StepEnum", "StepTyped", "StepInstance", "STAR",
           "parse_step", "import_ifc"]

_DEFAULT_E = 25e6            # kPa placeholder material (geometry-only)
_DIR_TOL = 1e-6              # direction-component tolerance
_ELEV_TOL = 1e-6             # m, storey elevation dedup

# building-element types we knowingly do NOT import (counted warnings)
_UNSUPPORTED_ELEMENTS = (
    "IFCWALL", "IFCWALLSTANDARDCASE", "IFCSLAB", "IFCOPENINGELEMENT",
    "IFCPLATE", "IFCDOOR", "IFCWINDOW", "IFCROOF", "IFCSTAIR",
    "IFCSTAIRFLIGHT", "IFCRAMP", "IFCRAILING", "IFCFOOTING", "IFCPILE",
    "IFCCOVERING", "IFCCURTAINWALL",
)


# --------------------------------------------------------------------------- #
# STEP value model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StepRef:
    """An instance reference ``#n``."""
    ref: int


@dataclass(frozen=True)
class StepEnum:
    """An enumeration value ``.NAME.`` (booleans are ``.T.``/``.F.``)."""
    name: str


@dataclass(frozen=True)
class StepTyped:
    """A typed (select) value like ``IFCLENGTHMEASURE(3.)``."""
    type: str
    args: tuple


class _Star:
    """The ``*`` (derived attribute) token — a singleton sentinel."""

    def __repr__(self) -> str:  # pragma: no cover - debugging nicety
        return "STAR"


STAR = _Star()


@dataclass
class StepInstance:
    """One parsed ``#id=IFCTYPE(args);`` DATA-section instance."""
    id: int
    type: str                  # upper-case entity name
    args: List


class _StepError(Exception):
    """Malformed STEP content — the statement is skipped with a warning."""


# --------------------------------------------------------------------------- #
# tokenizer / recursive-descent value parser
# --------------------------------------------------------------------------- #
class _Scanner:
    def __init__(self, s: str):
        self.s = s
        self.i = 0
        self.n = len(s)

    def _skip_ws(self) -> None:
        while self.i < self.n and self.s[self.i].isspace():
            self.i += 1

    def eof(self) -> bool:
        self._skip_ws()
        return self.i >= self.n

    def peek(self) -> str:
        self._skip_ws()
        if self.i >= self.n:
            raise _StepError("unexpected end of input")
        return self.s[self.i]

    def expect(self, ch: str) -> None:
        if self.peek() != ch:
            raise _StepError(f"expected {ch!r} at position {self.i}, got "
                             f"{self.s[self.i]!r}")
        self.i += 1

    # ---- terminals -------------------------------------------------- #
    def _string(self) -> str:
        # opening quote already peeked
        self.i += 1
        out: List[str] = []
        while self.i < self.n:
            ch = self.s[self.i]
            if ch == "'":
                if self.i + 1 < self.n and self.s[self.i + 1] == "'":
                    out.append("'")          # '' escape
                    self.i += 2
                    continue
                self.i += 1
                return "".join(out)
            out.append(ch)
            self.i += 1
        raise _StepError("unterminated string")

    def _enum(self) -> StepEnum:
        self.i += 1                          # leading '.'
        j = self.s.find(".", self.i)
        if j < 0:
            raise _StepError("unterminated enumeration value")
        name = self.s[self.i:j]
        self.i = j + 1
        return StepEnum(name.upper())

    def _number(self):
        j = self.i
        if self.s[j] in "+-":
            j += 1
        digits0 = j
        while j < self.n and self.s[j].isdigit():
            j += 1
        if j == digits0:
            raise _StepError(f"bad number at position {self.i}")
        is_real = False
        if j < self.n and self.s[j] == ".":
            is_real = True
            j += 1
            while j < self.n and self.s[j].isdigit():
                j += 1
        if j < self.n and self.s[j] in "Ee":
            k = j + 1
            if k < self.n and self.s[k] in "+-":
                k += 1
            if k < self.n and self.s[k].isdigit():
                is_real = True
                j = k
                while j < self.n and self.s[j].isdigit():
                    j += 1
        text = self.s[self.i:j]
        self.i = j
        return float(text) if is_real else int(text)

    def _ident(self) -> str:
        j = self.i
        while j < self.n and (self.s[j].isalnum() or self.s[j] == "_"):
            j += 1
        name = self.s[self.i:j]
        self.i = j
        return name.upper()

    # ---- values ----------------------------------------------------- #
    def value(self):
        ch = self.peek()
        if ch == "(":
            return self.value_list()
        if ch == "'":
            return self._string()
        if ch == ".":
            return self._enum()
        if ch == "#":
            self.i += 1
            n = self._number()
            if not isinstance(n, int):
                raise _StepError("non-integer instance reference")
            return StepRef(n)
        if ch == "$":
            self.i += 1
            return None
        if ch == "*":
            self.i += 1
            return STAR
        if ch.isdigit() or ch in "+-":
            return self._number()
        if ch.isalpha() or ch == "_":
            name = self._ident()
            if not self.eof() and self.peek() == "(":
                return StepTyped(name, tuple(self.value_list()))
            raise _StepError(f"bare identifier {name!r} is not a value")
        raise _StepError(f"unexpected character {ch!r} at position {self.i}")

    def value_list(self) -> List:
        self.expect("(")
        out: List = []
        if self.peek() == ")":
            self.i += 1
            return out
        while True:
            out.append(self.value())
            ch = self.peek()
            if ch == ",":
                self.i += 1
                continue
            if ch == ")":
                self.i += 1
                return out
            raise _StepError(f"expected ',' or ')' at position {self.i}")


def _split_statements(text: str) -> List[str]:
    """Split raw STEP text into ``;``-terminated statements.

    Respects strings (a ``;`` inside ``'...'`` does not terminate) — the
    only STEP construct that may contain a raw semicolon.
    """
    out: List[str] = []
    buf: List[str] = []
    in_string = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_string:
            buf.append(ch)
            if ch == "'":
                # '' escape stays inside the string
                if i + 1 < n and text[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            buf.append(ch)
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def parse_step(text: str) -> Tuple[Dict[int, StepInstance], List[str]]:
    """Parse SPF text into ``{id: StepInstance}`` plus warnings.

    Reads the DATA section (``DATA; ... ENDSEC;``); when no DATA section
    exists the whole text is scanned with a warning.  Malformed
    statements are skipped with a warning, never an exception.
    """
    warnings: List[str] = []
    upper = text.upper()
    start = upper.find("DATA;")
    if start >= 0:
        end = upper.find("ENDSEC;", start)
        if end < 0:
            warnings.append("DATA section not terminated by ENDSEC; "
                            "reading to end of file")
            region = text[start + len("DATA;"):]
        else:
            region = text[start + len("DATA;"):end]
    else:
        warnings.append("no DATA section found; scanning entire file")
        region = text

    instances: Dict[int, StepInstance] = {}
    for stmt in _split_statements(region):
        if not stmt.startswith("#"):
            continue                        # header noise / non-instances
        try:
            sc = _Scanner(stmt)
            sc.expect("#")
            iid = sc._number()
            if not isinstance(iid, int):
                raise _StepError("instance id is not an integer")
            sc.expect("=")
            sc._skip_ws()
            etype = sc._ident()
            if not etype:
                raise _StepError("missing entity type name")
            args = sc.value_list()
            if not sc.eof():
                raise _StepError("trailing content after argument list")
            if iid in instances:
                warnings.append(f"duplicate instance id #{iid}; keeping "
                                "the first occurrence")
                continue
            instances[iid] = StepInstance(iid, etype, args)
        except _StepError as exc:
            warnings.append(f"skipped malformed instance "
                            f"{stmt[:60]!r}: {exc}")
    return instances, warnings


# --------------------------------------------------------------------------- #
# semantic helpers
# --------------------------------------------------------------------------- #
def _unwrap(v):
    """Strip a typed wrapper (IFCLENGTHMEASURE(3.) -> 3.)."""
    if isinstance(v, StepTyped) and len(v.args) == 1:
        return v.args[0]
    return v


def _as_float(v) -> Optional[float]:
    v = _unwrap(v)
    return float(v) if isinstance(v, (int, float)) else None


def _deref(insts: Dict[int, StepInstance], v) -> Optional[StepInstance]:
    if isinstance(v, StepRef):
        return insts.get(v.ref)
    return None


def _coords(insts, v, n=3) -> Optional[Tuple[float, ...]]:
    """Coordinates of an IFCCARTESIANPOINT / ratios of an IFCDIRECTION."""
    inst = _deref(insts, v)
    if inst is None or not inst.args or not isinstance(inst.args[0], list):
        return None
    vals = [_as_float(c) for c in inst.args[0]]
    if any(c is None for c in vals):
        return None
    vals = (vals + [0.0] * n)[:n]
    return tuple(vals)


def _axis2placement(insts, v, warnings: List[str],
                    ctx: str) -> Tuple[float, float, float, float]:
    """(dx, dy, dz, theta) of an IFCAXIS2PLACEMENT3D.

    Only identity and Z-rotations are supported: a non-(0,0,1) Axis or a
    RefDirection with a z component warns (per element) and contributes
    NO rotation.  ``theta`` is the angle of the local X axis in the
    global XY plane (radians).
    """
    inst = _deref(insts, v)
    if inst is None or inst.type != "IFCAXIS2PLACEMENT3D":
        if v is not None:
            warnings.append(f"{ctx}: placement is not an "
                            "IFCAXIS2PLACEMENT3D; treated as identity")
        return 0.0, 0.0, 0.0, 0.0
    args = inst.args + [None] * (3 - len(inst.args))
    loc = _coords(insts, args[0]) or (0.0, 0.0, 0.0)
    theta = 0.0
    axis = _coords(insts, args[1]) if args[1] is not None else None
    if axis is not None and (abs(axis[0]) > _DIR_TOL
                             or abs(axis[1]) > _DIR_TOL
                             or axis[2] < 1.0 - _DIR_TOL):
        warnings.append(f"{ctx}: placement Axis {axis} is not +Z — only "
                        "Z-rotations are supported; rotation ignored")
    else:
        refdir = _coords(insts, args[2]) if args[2] is not None else None
        if refdir is not None:
            if abs(refdir[2]) > _DIR_TOL:
                warnings.append(f"{ctx}: RefDirection {refdir} is not in "
                                "the XY plane — rotation ignored")
            elif abs(refdir[0]) > _DIR_TOL or abs(refdir[1]) > _DIR_TOL:
                theta = math.atan2(refdir[1], refdir[0])
    return loc[0], loc[1], loc[2], theta


def _resolve_placement(insts, v, warnings: List[str], ctx: str,
                       _depth: int = 0) -> Tuple[float, float, float, float]:
    """Global (x, y, z, theta) of an IFCLOCALPLACEMENT chain.

    Each link contributes its IFCAXIS2PLACEMENT3D translation (rotated by
    the PARENT chain's accumulated Z-rotation) and its own Z-rotation.
    """
    if v is None:
        return 0.0, 0.0, 0.0, 0.0
    if _depth > 64:
        warnings.append(f"{ctx}: placement chain too deep (cycle?); "
                        "treated as absolute")
        return 0.0, 0.0, 0.0, 0.0
    inst = _deref(insts, v)
    if inst is None or inst.type != "IFCLOCALPLACEMENT":
        warnings.append(f"{ctx}: object placement is not an "
                        "IFCLOCALPLACEMENT; treated as origin")
        return 0.0, 0.0, 0.0, 0.0
    args = inst.args + [None] * (2 - len(inst.args))
    px, py, pz, pth = _resolve_placement(insts, args[0], warnings, ctx,
                                         _depth + 1)
    dx, dy, dz, th = _axis2placement(insts, args[1], warnings, ctx)
    cos_t, sin_t = math.cos(pth), math.sin(pth)
    return (px + cos_t * dx - sin_t * dy,
            py + sin_t * dx + cos_t * dy,
            pz + dz,
            pth + th)


def _find_extruded_solid(insts, v) -> Optional[StepInstance]:
    """First IFCEXTRUDEDAREASOLID under an IFCPRODUCTDEFINITIONSHAPE."""
    pds = _deref(insts, v)
    if pds is None or pds.type != "IFCPRODUCTDEFINITIONSHAPE":
        return None
    if len(pds.args) < 3 or not isinstance(pds.args[2], list):
        return None
    for rep_ref in pds.args[2]:
        rep = _deref(insts, rep_ref)
        if rep is None or rep.type != "IFCSHAPEREPRESENTATION":
            continue
        if len(rep.args) < 4 or not isinstance(rep.args[3], list):
            continue
        for item_ref in rep.args[3]:
            item = _deref(insts, item_ref)
            if item is not None and item.type == "IFCEXTRUDEDAREASOLID":
                return item
    return None


# --------------------------------------------------------------------------- #
# public: import
# --------------------------------------------------------------------------- #
def import_ifc(text: str) -> Tuple[BuildingModel, List[str]]:
    """Import SPF (STEP physical file) IFC text into a BuildingModel.

    GEOMETRY-ONLY, exploratory scope — see the module docstring for the
    exact supported subset and the documented limitations.  Returns
    ``(model, warnings)``; parse and semantic problems are reported as
    warnings, never raised.
    """
    insts, warnings = parse_step(text)

    # ---- units sanity (documented limitation, not a conversion) ------- #
    for inst in insts.values():
        if inst.type == "IFCSIUNIT" and len(inst.args) >= 3:
            unit_type = inst.args[1]
            prefix = inst.args[2]
            if (isinstance(unit_type, StepEnum)
                    and unit_type.name == "LENGTHUNIT"
                    and isinstance(prefix, StepEnum)):
                warnings.append(
                    f"file declares a prefixed length unit (.{prefix.name}.)"
                    " — coordinates are read AS-IS and NOT scaled "
                    "(geometry-only reader limitation)")

    # ---- unsupported building elements: counted warnings -------------- #
    counts: Dict[str, int] = {}
    for inst in insts.values():
        if inst.type in _UNSUPPORTED_ELEMENTS:
            counts[inst.type] = counts.get(inst.type, 0) + 1
    for etype in sorted(counts):
        warnings.append(f"skipped {counts[etype]} {etype} instance(s) — "
                        "not supported by the geometry-only reader")

    # ---- model shell --------------------------------------------------- #
    name = "IFC Import"
    for inst in insts.values():
        if inst.type == "IFCBUILDING" and len(inst.args) > 2 \
                and isinstance(inst.args[2], str) and inst.args[2].strip():
            name = inst.args[2].strip()
            break
    mdl = BuildingModel(name=name)
    mdl.base_fixity = "fixed"
    mdl.add_material(Material("IFC", E=_DEFAULT_E, nu=0.2))

    # ---- storeys -> stories -------------------------------------------- #
    storeys: List[Tuple[float, str]] = []
    for inst in sorted((i for i in insts.values()
                        if i.type == "IFCBUILDINGSTOREY"),
                       key=lambda i: i.id):
        elev = _as_float(inst.args[9]) if len(inst.args) > 9 else None
        if elev is None:                     # tolerate truncated attributes
            elev = _as_float(inst.args[-1]) if inst.args else None
        if elev is None:
            warnings.append(f"IFCBUILDINGSTOREY #{inst.id} has no numeric "
                            "Elevation; ignored")
            continue
        sname = inst.args[2] if len(inst.args) > 2 \
            and isinstance(inst.args[2], str) and inst.args[2].strip() \
            else f"Storey#{inst.id}"
        if any(abs(elev - e) < _ELEV_TOL for e, _ in storeys):
            warnings.append(f"IFCBUILDINGSTOREY #{inst.id} duplicates "
                            f"elevation {elev:g}; ignored")
            continue
        storeys.append((elev, sname))
    storeys.sort(key=lambda t: t[0])

    # ---- members -------------------------------------------------------- #
    used_uids: set = set()

    def _uid(preferred: Optional[str], fallback: str) -> str:
        cand = preferred if preferred and preferred not in used_uids \
            else fallback
        while cand in used_uids:             # fallback collision guard
            cand += "*"
        used_uids.add(cand)
        return cand

    def _section_for(x_dim: float, y_dim: float) -> str:
        sec_name = f"IFC-R{x_dim:g}x{y_dim:g}"
        if sec_name not in mdl.sections:
            mdl.add_section(FrameSection.rectangular(sec_name, "IFC",
                                                     x_dim, y_dim))
        return sec_name

    elements = sorted((i for i in insts.values()
                       if i.type in ("IFCCOLUMN", "IFCBEAM", "IFCMEMBER")),
                      key=lambda i: i.id)
    for inst in elements:
        ctx = f"{inst.type} #{inst.id}"
        args = inst.args + [None] * (7 - len(inst.args))
        el_name = args[2] if isinstance(args[2], str) and args[2].strip() \
            else None
        x, y, z, theta = _resolve_placement(insts, args[5], warnings, ctx)
        solid = _find_extruded_solid(insts, args[6])
        if solid is None:
            warnings.append(f"{ctx}: no IFCEXTRUDEDAREASOLID representation"
                            " — skipped (geometry-only reader)")
            continue
        sargs = solid.args + [None] * (4 - len(solid.args))
        profile = _deref(insts, sargs[0])
        if profile is None or profile.type != "IFCRECTANGLEPROFILEDEF":
            ptype = profile.type if profile is not None else "?"
            warnings.append(f"{ctx}: profile {ptype} is not an "
                            "IFCRECTANGLEPROFILEDEF — skipped")
            continue
        pargs = profile.args + [None] * (5 - len(profile.args))
        x_dim = _as_float(pargs[3])
        y_dim = _as_float(pargs[4])
        depth = _as_float(sargs[3])
        if not all(v is not None and v > 0.0 for v in (x_dim, y_dim, depth)):
            warnings.append(f"{ctx}: non-positive/missing XDim/YDim/Depth "
                            "— skipped")
            continue
        # the solid's own Position adds a (rotated) offset inside the object
        ox, oy, oz, oth = _axis2placement(insts, sargs[1], warnings, ctx)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        x += cos_t * ox - sin_t * oy
        y += sin_t * ox + cos_t * oy
        z += oz
        theta += oth
        ext_dir = _coords(insts, sargs[2]) if sargs[2] is not None else None

        if inst.type == "IFCCOLUMN":
            if ext_dir is not None and (abs(ext_dir[0]) > _DIR_TOL
                                        or abs(ext_dir[1]) > _DIR_TOL):
                warnings.append(f"{ctx}: extrusion direction {ext_dir} is "
                                "not vertical — treated as +Z")
            sign = -1.0 if (ext_dir is not None
                            and ext_dir[2] < 0.0) else 1.0
            z_other = z + sign * depth
            pi = (x, y, min(z, z_other))
            pj = (x, y, max(z, z_other))
            kind = "column"
        else:                                # IFCBEAM / IFCMEMBER
            pi = (x, y, z)
            pj = (x + depth * math.cos(theta), y + depth * math.sin(theta),
                  z)
            kind = "beam" if inst.type == "IFCBEAM" else "brace"
        sec = _section_for(x_dim, y_dim)
        uid = _uid(el_name, f"{inst.type[3]}{inst.id}")
        mdl.add_member(kind, sec, pi, pj, uid=uid)

    # ---- stories: storey elevations -> Story list ----------------------- #
    if len(storeys) >= 2:
        base_elev = storeys[0][0]
        prev = base_elev
        for elev, sname in storeys[1:]:
            mdl.stories.append(Story(sname, elev - prev, elev))
            prev = elev
        warnings_note_base = storeys[0][1]
        warnings.append(f"storey {warnings_note_base!r} at elevation "
                        f"{base_elev:g} is the base level (not a story)")
    elif storeys:
        e0, sname = storeys[0]
        tops = [max(m.pi[2], m.pj[2]) for m in mdl.members]
        top = max(tops) if tops else e0
        if top > e0 + _ELEV_TOL:
            mdl.stories.append(Story(sname, top - e0, top))
            warnings.append(f"single storey {sname!r}: story top taken from"
                            f" the highest member ({top:g})")
        else:
            warnings.append("single storey with no members above it — no "
                            "stories created")
    else:
        tops = [max(m.pi[2], m.pj[2]) for m in mdl.members]
        if tops and max(tops) > _ELEV_TOL:
            mdl.stories.append(Story("Story1", max(tops), max(tops)))
            warnings.append("no IFCBUILDINGSTOREY found — one story "
                            f"created at the highest member ({max(tops):g})")
        elif insts:
            warnings.append("no IFCBUILDINGSTOREY found and no members "
                            "imported — model has no stories")

    # assign each member to the story with the NEAREST elevation to its top
    for m in mdl.members:
        if mdl.stories:
            ztop = max(m.pi[2], m.pj[2])
            m.story = min(mdl.stories,
                          key=lambda s: abs(s.elevation - ztop)).name

    # ---- grid from column plan positions ------------------------------- #
    xs = sorted({round(m.pi[0], 6) for m in mdl.members
                 if m.kind == "column"})
    ys = sorted({round(m.pi[1], 6) for m in mdl.members
                 if m.kind == "column"})
    if xs and ys:
        mdl.grid = GridSystem(xs, ys)

    mdl.validate()
    return mdl, warnings
