"""ETABS ``.e2k`` text-file import — self-contained parser + model builder.

Reads the classic ``$``-section-based ETABS export format (a SUBSET of
it) and assembles a :class:`~skyframe.core.model.BuildingModel`:

* ``$ UNITS``                — ``UNITS "kN" "m"``: kN m / N mm / kip ft
  are recognised (overrides the ``units=`` kwarg); everything is
  converted into SkyFrame kN / m / kPa on import.
* ``$ STORIES``              — ``STORY "name" HEIGHT h``.  ETABS lists
  stories TOP-DOWN; SkyFrame stores them bottom-up with cumulative
  top-of-story elevations.
* ``$ POINT COORDINATES``    — ``POINT "n" x y``: the plan coordinates a
  line connectivity refers to (the model grid is derived from the unique
  x / y values).
* ``$ MATERIAL PROPERTIES``  — ``MATERIAL "name" E e`` (E is in the
  file's force/length^2 and converted); ``U`` is read as Poisson's ratio
  when present.
* ``$ FRAME SECTIONS``       — ``FRAMESECTION "name" MATERIAL "m" SHAPE
  "Concrete Rectangular" D d B b`` maps to
  :meth:`FrameSection.rectangular` with **D = depth = h** and
  **B = width = b**; any other SHAPE gets a default 0.3 x 0.6 rectangle
  with a warning.
* ``$ LINE CONNECTIVITIES``  — ``LINE "name" BEAM|COLUMN "p1" "p2"``.
* ``$ LINE ASSIGNS``         — ``LINEASSIGN "line" "story" SECTION "s"``
  instantiates one member per (line, story): beams at the story's top
  elevation, columns spanning from the story's bottom to its top.
  Member uids are ``"<line>-<story>"``.
* ``$ LOAD PATTERNS``        — ``LOADPATTERN "name" TYPE "Dead"``
  (``LOADCASE`` is accepted as a synonym); DEAD/SUPERDEAD map to
  ``"dead"``, LIVE/REDUCIBLE LIVE to ``"live"``, QUAKE/SEISMIC to
  ``"quake"``, anything else to ``"other"``.  Patterns are created EMPTY
  (loads are not part of the imported subset) and a matching
  single-pattern load case is added for each.

Tokenizer: quoted strings are single tokens; after the positional tokens
of each record the remainder is read as KEY value pairs.  Unknown lines,
sections, shapes and dangling references are collected in a ``warnings``
list — the importer never raises on unknown content, only on arguments
that make an import impossible (e.g. no parseable text at all is still
fine: it returns an empty model plus warnings).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from skyframe.core.model import (
    BuildingModel,
    FrameSection,
    GridSystem,
    Material,
)

__all__ = ["import_e2k"]

# default modulus for materials that are referenced but never defined
_DEFAULT_E = 25e6            # kPa (25 GPa concrete-ish)
_DEFAULT_RECT = (0.3, 0.6)   # (b, h) m for unknown section shapes

# unit systems: name (upper) -> factor to SkyFrame base unit
_FORCE_TO_KN = {"KN": 1.0, "N": 1e-3, "KIP": 4.4482216152605}
_LENGTH_TO_M = {"M": 1.0, "MM": 1e-3, "FT": 0.3048}

_RECT_SHAPES = ("CONCRETE RECTANGULAR", "RECTANGULAR")

_PATTERN_KINDS = {
    "DEAD": "dead", "SUPERDEAD": "dead", "SUPER DEAD": "dead",
    "LIVE": "live", "REDUCIBLE LIVE": "live", "REDUCE LIVE": "live",
    "QUAKE": "quake", "SEISMIC": "quake",
}


# --------------------------------------------------------------------------- #
# line tokenizer
# --------------------------------------------------------------------------- #
@dataclass
class _Tok:
    text: str
    quoted: bool = False


def _tokenize(line: str) -> List[_Tok]:
    """Split one e2k record line into tokens; quoted strings stay whole."""
    toks: List[_Tok] = []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '"':
            j = line.find('"', i + 1)
            if j < 0:                      # unterminated quote: rest of line
                toks.append(_Tok(line[i + 1:], quoted=True))
                return toks
            toks.append(_Tok(line[i + 1:j], quoted=True))
            i = j + 1
        else:
            j = i
            while j < n and not line[j].isspace():
                j += 1
            toks.append(_Tok(line[i:j]))
            i = j
    return toks


def _kv(tokens: List[_Tok], start: int) -> Dict[str, _Tok]:
    """KEY value pairs from ``tokens[start:]`` (keys are bare uppercase)."""
    out: Dict[str, _Tok] = {}
    i = start
    while i < len(tokens):
        key = tokens[i]
        if key.quoted or i + 1 >= len(tokens):
            break                          # not a key / dangling key
        out[key.text.upper()] = tokens[i + 1]
        i += 2
    return out


def _num(tok: Optional[_Tok]) -> Optional[float]:
    if tok is None:
        return None
    try:
        return float(tok.text)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# parsed records
# --------------------------------------------------------------------------- #
@dataclass
class _Parsed:
    units: Optional[Tuple[str, str]] = None          # (force, length) upper
    stories: List[Tuple[str, float]] = field(default_factory=list)  # TOP-DOWN
    points: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    lines: Dict[str, Tuple[str, str, str]] = field(default_factory=dict)
    #                                        # name -> (kind, p1, p2)
    materials: Dict[str, Dict[str, float]] = field(default_factory=dict)
    sections: Dict[str, dict] = field(default_factory=dict)
    assigns: List[Tuple[str, str, Optional[str]]] = field(default_factory=list)
    #                                        # (line, story, section|None)
    patterns: List[Tuple[str, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# canonical section keys, matched by PREFIX so real ETABS headers like
# "$ STORIES - IN SEQUENCE FROM TOP" resolve to their canonical section
_SECTIONS = ("UNITS", "STORIES", "POINT COORDINATES", "LINE CONNECTIVITIES",
             "MATERIAL PROPERTIES", "FRAME SECTIONS", "LINE ASSIGNS",
             "LOAD PATTERNS")

# standard boilerplate sections silently skipped (present in every export)
_BENIGN_SECTIONS = ("PROGRAM INFORMATION", "PROJECT INFORMATION",
                    "CONTROLS", "LOG")


def _section_key(header: str) -> str:
    up = header.strip().upper()
    for key in _SECTIONS + _BENIGN_SECTIONS:
        if up.startswith(key):
            return key
    return up


def _parse(text: str) -> _Parsed:
    p = _Parsed()
    section = ""
    skipped: Dict[str, int] = {}           # unknown section -> line count
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("$"):
            section = _section_key(line[1:])
            continue
        if section in _BENIGN_SECTIONS:
            continue
        if section not in _SECTIONS:
            skipped[section or "?"] = skipped.get(section or "?", 0) + 1
            continue
        toks = _tokenize(line)
        if not toks:
            continue
        head = toks[0].text.upper()
        try:
            _dispatch(p, section, head, toks, line)
        except (IndexError, ValueError, TypeError) as exc:
            p.warnings.append(f"skipped malformed line {line!r}: {exc}")
    for name, count in skipped.items():
        p.warnings.append(f"unparsed section $ {name} ignored "
                          f"({count} line(s))")
    return p


def _dispatch(p: _Parsed, section: str, head: str, toks: List[_Tok],
              line: str) -> None:
    if section == "UNITS":
        if head == "UNITS" and len(toks) >= 3:
            p.units = (toks[1].text.upper(), toks[2].text.upper())
        else:
            p.warnings.append(f"unrecognised UNITS line {line!r}")
    elif section == "STORIES":
        if head == "STORY" and len(toks) >= 2:
            kv = _kv(toks, 2)
            h = _num(kv.get("HEIGHT"))
            if h is None or h <= 0.0:
                p.warnings.append(f"story {toks[1].text!r} has no positive "
                                  "HEIGHT; ignored")
            else:
                p.stories.append((toks[1].text, h))
        else:
            p.warnings.append(f"unrecognised line in $ STORIES: {line!r}")
    elif section == "POINT COORDINATES":
        if head == "POINT" and len(toks) >= 4:
            x, y = _num(toks[2]), _num(toks[3])
            if x is None or y is None:
                raise ValueError("POINT coordinates are not numbers")
            p.points[toks[1].text] = (x, y)
        else:
            p.warnings.append("unrecognised line in $ POINT COORDINATES: "
                              f"{line!r}")
    elif section == "LINE CONNECTIVITIES":
        if head == "LINE" and len(toks) >= 5:
            kind = toks[2].text.upper()
            if kind not in ("BEAM", "COLUMN"):
                p.warnings.append(f"line {toks[1].text!r}: connectivity "
                                  f"kind {toks[2].text!r} not supported "
                                  "(only BEAM/COLUMN); ignored")
            else:
                p.lines[toks[1].text] = (kind, toks[3].text, toks[4].text)
        else:
            p.warnings.append("unrecognised line in $ LINE CONNECTIVITIES: "
                              f"{line!r}")
    elif section == "MATERIAL PROPERTIES":
        if head == "MATERIAL" and len(toks) >= 2:
            kv = _kv(toks, 2)
            entry = p.materials.setdefault(toks[1].text, {})
            e = _num(kv.get("E"))
            if e is not None:
                entry["E"] = e
            u = _num(kv.get("U"))
            if u is not None:
                entry["nu"] = u
        else:
            p.warnings.append("unrecognised line in $ MATERIAL PROPERTIES: "
                              f"{line!r}")
    elif section == "FRAME SECTIONS":
        if head == "FRAMESECTION" and len(toks) >= 2:
            kv = _kv(toks, 2)
            shape = kv.get("SHAPE")
            mat = kv.get("MATERIAL")
            p.sections[toks[1].text] = {
                "material": mat.text if mat is not None else None,
                "shape": shape.text if shape is not None else None,
                "D": _num(kv.get("D")), "B": _num(kv.get("B")),
            }
        else:
            p.warnings.append("unrecognised line in $ FRAME SECTIONS: "
                              f"{line!r}")
    elif section == "LINE ASSIGNS":
        if head == "LINEASSIGN" and len(toks) >= 3:
            kv = _kv(toks, 3)
            sec = kv.get("SECTION")
            p.assigns.append((toks[1].text, toks[2].text,
                              sec.text if sec is not None else None))
        else:
            p.warnings.append("unrecognised line in $ LINE ASSIGNS: "
                              f"{line!r}")
    elif section == "LOAD PATTERNS":
        if head in ("LOADPATTERN", "LOADCASE") and len(toks) >= 2:
            kv = _kv(toks, 2)
            typ = kv.get("TYPE")
            p.patterns.append((toks[1].text,
                               typ.text if typ is not None else ""))
        else:
            p.warnings.append("unrecognised line in $ LOAD PATTERNS: "
                              f"{line!r}")


# --------------------------------------------------------------------------- #
# public: import
# --------------------------------------------------------------------------- #
def import_e2k(text: str, *,
               units: Tuple[str, str] = ("kN", "m"),
               ) -> Tuple[BuildingModel, List[str]]:
    """Import ETABS ``.e2k`` text into a :class:`BuildingModel`.

    ``units`` is the (force, length) system the file is written in,
    default ``("kN", "m")``; a ``$ UNITS`` line inside the file OVERRIDES
    it.  Supported systems: kN m, N mm, kip ft (case-insensitive).  All
    quantities are converted to SkyFrame kN / m / kPa: lengths and
    section dimensions scale by the length factor, material E by
    force / length^2.

    Returns ``(model, warnings)`` — unknown lines / shapes / dangling
    references never raise, they are reported in ``warnings``.
    """
    p = _parse(text)
    warnings = list(p.warnings)

    # ---------------------------------------------------------------- units
    fu, lu = str(units[0]).upper(), str(units[1]).upper()
    if p.units is not None:
        fu, lu = p.units
    if fu not in _FORCE_TO_KN or lu not in _LENGTH_TO_M:
        warnings.append(f"unsupported unit system ({fu}, {lu}) — assuming "
                        "kN m (supported: kN m, N mm, kip ft)")
        fu, lu = "KN", "M"
    f_scale = _FORCE_TO_KN[fu]                 # file force -> kN
    l_scale = _LENGTH_TO_M[lu]                 # file length -> m
    e_scale = f_scale / l_scale ** 2           # file E -> kPa (kN/m^2)

    mdl = BuildingModel(name="E2K Import")
    mdl.base_fixity = "fixed"

    # ------------------------------------------------------------- stories
    # ETABS lists stories TOP-DOWN; reverse for SkyFrame's bottom-up order
    # and accumulate top-of-story elevations from the base up.
    story_elev: Dict[str, float] = {}
    story_height: Dict[str, float] = {}
    elev = 0.0
    heights: List[float] = []
    names: List[str] = []
    for name, h in reversed(p.stories):
        elev += h * l_scale
        heights.append(h * l_scale)
        names.append(name)
        story_elev[name] = elev
        story_height[name] = h * l_scale
    mdl.set_stories(heights, names or None)

    # ----------------------------------------------------------- materials
    def _ensure_material(name: str) -> str:
        if name not in mdl.materials:
            props = p.materials.get(name)
            if props is None or "E" not in props:
                warnings.append(f"material {name!r} has no E (or is not "
                                f"defined) — default E = {_DEFAULT_E:g} kPa"
                                " used")
                E = _DEFAULT_E
                nu = 0.2
            else:
                E = props["E"] * e_scale
                nu = props.get("nu", 0.2)
            mdl.add_material(Material(name, E=E, nu=nu))
        return name

    for name in p.materials:
        _ensure_material(name)

    # ------------------------------------------------------------ sections
    def _ensure_section(name: str, referenced_by: str = "") -> str:
        if name in mdl.sections:
            return name
        sd = p.sections.get(name)
        if sd is None:
            warnings.append(f"{referenced_by} references unknown frame "
                            f"section {name!r} — default "
                            f"{_DEFAULT_RECT[0]}x{_DEFAULT_RECT[1]} "
                            "rectangle used")
            mat = _ensure_material(next(iter(p.materials), "CONC"))
            mdl.add_section(FrameSection.rectangular(
                name, mat, *_DEFAULT_RECT))
            return name
        mat = _ensure_material(sd["material"] or "CONC")
        shape = (sd["shape"] or "").upper()
        if shape in _RECT_SHAPES and sd["D"] and sd["B"]:
            # ETABS D = section DEPTH (SkyFrame h), B = width (b)
            b = sd["B"] * l_scale
            h = sd["D"] * l_scale
        else:
            warnings.append(f"frame section {name!r}: shape "
                            f"{sd['shape']!r} not supported — default "
                            f"{_DEFAULT_RECT[0]}x{_DEFAULT_RECT[1]} "
                            "rectangle used")
            b, h = _DEFAULT_RECT
        mdl.add_section(FrameSection.rectangular(name, mat, b, h))
        return name

    for name in p.sections:
        _ensure_section(name)

    # ------------------------------------------------------------- members
    seen_uids: set = set()
    for line_name, story_name, sec_name in p.assigns:
        conn = p.lines.get(line_name)
        if conn is None:
            warnings.append(f"line assign for unknown line {line_name!r} "
                            "skipped")
            continue
        if story_name not in story_elev:
            warnings.append(f"line {line_name!r} assigned to unknown story "
                            f"{story_name!r}; skipped")
            continue
        kind, p1, p2 = conn
        if p1 not in p.points or p2 not in p.points:
            warnings.append(f"line {line_name!r} references unknown "
                            f"point(s) {p1!r}/{p2!r}; skipped")
            continue
        if sec_name is None:
            warnings.append(f"line assign {line_name!r}@{story_name!r} has "
                            "no SECTION — default section used")
            sec_name = "E2K-DEFAULT"
        sec_name = _ensure_section(
            sec_name, referenced_by=f"line assign {line_name!r}")
        x1, y1 = (v * l_scale for v in p.points[p1])
        x2, y2 = (v * l_scale for v in p.points[p2])
        z_top = story_elev[story_name]
        uid = f"{line_name}-{story_name}"
        if uid in seen_uids:
            warnings.append(f"duplicate line assign {line_name!r} @ "
                            f"{story_name!r} ignored")
            continue
        seen_uids.add(uid)
        if kind == "COLUMN":
            if (x1, y1) != (x2, y2):
                warnings.append(f"column {line_name!r}: connectivity points "
                                f"{p1!r}/{p2!r} differ in plan — point "
                                f"{p1!r} used for both ends")
            z_bot = z_top - story_height[story_name]
            mdl.add_member("column", sec_name, (x1, y1, z_bot),
                           (x1, y1, z_top), story=story_name, uid=uid)
        else:                                  # BEAM
            mdl.add_member("beam", sec_name, (x1, y1, z_top),
                           (x2, y2, z_top), story=story_name, uid=uid)

    # ---------------------------------------------------------------- grid
    if p.points:
        mdl.grid = GridSystem(
            sorted({round(x * l_scale, 9) for x, _ in p.points.values()}),
            sorted({round(y * l_scale, 9) for _, y in p.points.values()}))

    # ------------------------------------------------------------ patterns
    for name, typ in p.patterns:
        kind = _PATTERN_KINDS.get(typ.upper(), "other")
        mdl.pattern(name, kind)
        mdl.add_case(name, {name: 1.0})

    mdl.validate()
    return mdl, warnings
