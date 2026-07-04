"""ASCII DXF import — self-contained parser + BuildingModel builder.

No external dependencies (no ezdxf).  Reads the classic group-code pair
stream (a code line followed by a value line), extracts the ENTITIES
section and turns a 2D architectural plan into a multi-story SkyFrame
:class:`~skyframe.core.model.BuildingModel`:

* POINT / CIRCLE on a ``COL*`` layer .... column locations (vertical
  members replicated per story)
* LINE on a ``BEAM*`` layer ............ beams at every story top
* LINE on a ``WALL*`` layer ............ full-story-height shell walls
* axis-parallel LINE on a ``GRID*`` layer  grid lines (GridSystem)

Layer conventions are case-insensitive and can be overridden with a
``layer_map`` dict (layer name -> role).  Everything unexpected is
collected in a ``warnings`` list rather than raising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from skyframe.core.model import (
    BuildingModel,
    FrameSection,
    GridSystem,
    Material,
    ShellSection,
)

__all__ = ["DxfEntity", "DxfDrawing", "parse_dxf", "import_dxf"]

# entity types the parser understands (everything else -> warning)
_SUPPORTED = ("LINE", "LWPOLYLINE", "POINT", "CIRCLE")

# layer roles import_dxf knows about
_ROLES = ("columns", "beams", "walls", "grid", "ignore")

# a LINE on a column layer at most this long (after unit_scale) marks a
# column location instead of being an error
_COLUMN_LINE_TOL = 0.01   # m
_ZERO_LEN_TOL = 1e-6      # m — shorter lines are "zero length"
_SNAP = 6                 # round coordinates to 1e-6


# --------------------------------------------------------------------------- #
# Parsed drawing
# --------------------------------------------------------------------------- #
@dataclass
class DxfEntity:
    """One parsed DXF entity.

    ``points`` holds the geometry: ``[start, end]`` for LINE, the vertex
    list for LWPOLYLINE (z always 0), a single point for POINT/CIRCLE.
    """

    etype: str                                   # "LINE" | "LWPOLYLINE" | ...
    layer: str
    points: List[Tuple[float, float, float]]
    closed: bool = False                         # LWPOLYLINE flag 70 & 1
    radius: float = 0.0                          # CIRCLE only


@dataclass
class DxfDrawing:
    """Result of :func:`parse_dxf`: entities plus non-fatal warnings."""

    entities: List[DxfEntity] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def on_layer(self, layer: str) -> List[DxfEntity]:
        """Entities on ``layer`` (case-insensitive convenience)."""
        low = layer.lower()
        return [e for e in self.entities if e.layer.lower() == low]


# --------------------------------------------------------------------------- #
# Group-code tokenizer
# --------------------------------------------------------------------------- #
def _pairs(text: str) -> List[Tuple[int, str]]:
    """Split raw DXF text into (group-code, value) pairs.

    Robust to CRLF/LF line endings and stray whitespace around the code.
    Blank lines *between* pairs (where a code is expected) are skipped;
    a blank line in value position is a legitimate empty value.
    """
    lines = text.splitlines()
    out: List[Tuple[int, str]] = []
    i = 0
    n = len(lines)
    while i < n:
        code_raw = lines[i].strip()
        if not code_raw:            # blank where a code should be: skip
            i += 1
            continue
        try:
            code = int(code_raw)
        except ValueError:
            # resync: not a group code; skip this line
            i += 1
            continue
        value = lines[i + 1].strip() if i + 1 < n else ""
        out.append((code, value))
        i += 2
    return out


def _entities_region(pairs: List[Tuple[int, str]],
                     warnings: List[str]) -> List[Tuple[int, str]]:
    """Slice of ``pairs`` inside SECTION/ENTITIES ... ENDSEC framing.

    Tolerates content before/after the section.  Falls back to the whole
    stream (with a warning) when no ENTITIES section exists.
    """
    for i in range(len(pairs) - 1):
        if (pairs[i][0] == 0 and pairs[i][1].upper() == "SECTION"
                and pairs[i + 1][0] == 2
                and pairs[i + 1][1].upper() == "ENTITIES"):
            start = i + 2
            for j in range(start, len(pairs)):
                if pairs[j][0] == 0 and pairs[j][1].upper() == "ENDSEC":
                    return pairs[start:j]
            warnings.append("ENTITIES section not terminated by ENDSEC; "
                            "reading to end of file")
            return pairs[start:]
    warnings.append("no ENTITIES section found; scanning entire file")
    return pairs


def _first(ent: List[Tuple[int, str]], code: int) -> Optional[str]:
    for c, v in ent:
        if c == code:
            return v
    return None


def _float(ent: List[Tuple[int, str]], code: int,
           default: Optional[float] = None) -> Optional[float]:
    v = _first(ent, code)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        raise _BadEntity(f"group {code} value {v!r} is not a number")


class _BadEntity(Exception):
    """Malformed entity — skipped with a warning."""


def _build_entity(etype: str, ent: List[Tuple[int, str]]) -> DxfEntity:
    layer = _first(ent, 8) or "0"
    if etype == "LINE":
        x1, y1 = _float(ent, 10), _float(ent, 20)
        x2, y2 = _float(ent, 11), _float(ent, 21)
        if None in (x1, y1, x2, y2):
            raise _BadEntity("LINE missing 10/20/11/21 coordinates")
        z1 = _float(ent, 30, 0.0)
        z2 = _float(ent, 31, 0.0)
        return DxfEntity("LINE", layer, [(x1, y1, z1), (x2, y2, z2)])
    if etype == "POINT":
        x, y = _float(ent, 10), _float(ent, 20)
        if None in (x, y):
            raise _BadEntity("POINT missing 10/20 coordinates")
        return DxfEntity("POINT", layer, [(x, y, _float(ent, 30, 0.0))])
    if etype == "CIRCLE":
        x, y = _float(ent, 10), _float(ent, 20)
        if None in (x, y):
            raise _BadEntity("CIRCLE missing 10/20 center")
        return DxfEntity("CIRCLE", layer, [(x, y, _float(ent, 30, 0.0))],
                         radius=_float(ent, 40, 0.0))
    if etype == "LWPOLYLINE":
        verts: List[Tuple[float, float, float]] = []
        pending_x: Optional[float] = None
        for c, v in ent:
            if c == 10:
                if pending_x is not None:
                    raise _BadEntity("LWPOLYLINE vertex x without y")
                try:
                    pending_x = float(v)
                except ValueError:
                    raise _BadEntity(f"LWPOLYLINE bad vertex x {v!r}")
            elif c == 20:
                if pending_x is None:
                    raise _BadEntity("LWPOLYLINE vertex y without x")
                try:
                    verts.append((pending_x, float(v), 0.0))
                except ValueError:
                    raise _BadEntity(f"LWPOLYLINE bad vertex y {v!r}")
                pending_x = None
        if pending_x is not None:
            raise _BadEntity("LWPOLYLINE trailing vertex x without y")
        if not verts:
            raise _BadEntity("LWPOLYLINE has no vertices")
        flags = _first(ent, 70)
        closed = False
        if flags is not None:
            try:
                closed = bool(int(flags) & 1)
            except ValueError:
                raise _BadEntity(f"LWPOLYLINE bad flags {flags!r}")
        return DxfEntity("LWPOLYLINE", layer, verts, closed=closed)
    raise _BadEntity(f"unsupported entity type {etype}")


# --------------------------------------------------------------------------- #
# Public: parse
# --------------------------------------------------------------------------- #
def parse_dxf(text: str) -> DxfDrawing:
    """Parse ASCII DXF text into a :class:`DxfDrawing`.

    Understands LINE, LWPOLYLINE, POINT and CIRCLE in the ENTITIES
    section.  INSERT and any other entity types are skipped with a
    warning.  Never raises on malformed entities — they are skipped and
    reported in ``drawing.warnings``.
    """
    drawing = DxfDrawing()
    region = _entities_region(_pairs(text), drawing.warnings)

    # split the region into entities: each starts at a (0, <TYPE>) pair
    current_type: Optional[str] = None
    current: List[Tuple[int, str]] = []

    def _flush() -> None:
        if current_type is None:
            return
        etype = current_type.upper()
        layer = _first(current, 8) or "0"
        if etype == "INSERT":
            name = _first(current, 2) or "?"
            drawing.warnings.append(
                f"INSERT (block reference {name!r}) on layer {layer!r} "
                "ignored — explode blocks before import")
            return
        try:
            drawing.entities.append(_build_entity(etype, current))
        except _BadEntity as exc:
            drawing.warnings.append(
                f"skipped {etype} on layer {layer!r}: {exc}")

    for code, value in region:
        if code == 0:
            _flush()
            current_type, current = value, []
        elif current_type is not None:
            current.append((code, value))
        # pairs before the first entity (e.g. section noise) are ignored
    _flush()
    return drawing


# --------------------------------------------------------------------------- #
# Public: import
# --------------------------------------------------------------------------- #
def _normalize_layer_map(layer_map: Optional[dict]) -> Dict[str, str]:
    norm: Dict[str, str] = {}
    for k, v in (layer_map or {}).items():
        role = str(v).strip().lower()
        if role not in _ROLES:
            raise ValueError(f"layer_map: unknown role {v!r} for layer "
                             f"{k!r} (expected one of {_ROLES})")
        norm[str(k).strip().lower()] = role
    return norm


def _layer_role(layer: str, layer_map: Dict[str, str]) -> Optional[str]:
    low = layer.strip().lower()
    if low in layer_map:
        role = layer_map[low]
        return None if role == "ignore" else role
    if low.startswith("col"):
        return "columns"
    if low.startswith("beam"):
        return "beams"
    if low.startswith("wall"):
        return "walls"
    if low.startswith("grid"):
        return "grid"
    return None


def _segments(ent: DxfEntity) -> List[Tuple[Tuple[float, float],
                                            Tuple[float, float]]]:
    """2D segments of a LINE or LWPOLYLINE (z dropped — plan is 2D)."""
    pts = [(p[0], p[1]) for p in ent.points]
    if ent.etype == "LINE":
        return [(pts[0], pts[1])]
    segs = list(zip(pts[:-1], pts[1:]))
    if ent.closed and len(pts) > 2:
        segs.append((pts[-1], pts[0]))
    return segs


def import_dxf(
    text: str,
    *,
    stories: List[float],
    column_section: str,
    beam_section: str,
    wall_section: Optional[str] = None,
    wall_thickness: float = 0.2,
    E: float = 25e6,
    unit_scale: float = 1.0,
    layer_map: Optional[dict] = None,
) -> Tuple[BuildingModel, List[str]]:
    """Import a DXF plan into a multi-story :class:`BuildingModel`.

    The 2D plan (z ignored) is replicated for every story in ``stories``
    (story heights, bottom to top): each column location becomes one
    vertical member per story, each beam line a beam at every story top
    elevation, each wall line a full-story-height shell region.

    ``unit_scale`` multiplies all drawing coordinates (0.001 for a
    drawing in millimetres).  Coordinates are snapped to 1e-6 m.

    Returns ``(model, warnings)``.
    """
    if not stories:
        raise ValueError("stories must contain at least one story height")
    for h in stories:
        if not float(h) > 0.0:
            raise ValueError(f"story heights must be > 0 (got {h})")
    if float(unit_scale) <= 0.0:
        raise ValueError("unit_scale must be > 0")
    lmap = _normalize_layer_map(layer_map)

    drawing = parse_dxf(text)
    warnings = list(drawing.warnings)

    def snap(v: float) -> float:
        return round(float(v) * float(unit_scale), _SNAP)

    col_pts: List[Tuple[float, float]] = []
    beam_segs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    wall_segs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    grid_x: set = set()
    grid_y: set = set()
    unmapped_layers: List[str] = []

    def add_segments(ent: DxfEntity, bucket: list, what: str) -> None:
        for (x1, y1), (x2, y2) in _segments(ent):
            a = (snap(x1), snap(y1))
            b = (snap(x2), snap(y2))
            if abs(a[0] - b[0]) < _ZERO_LEN_TOL and \
               abs(a[1] - b[1]) < _ZERO_LEN_TOL:
                warnings.append(f"zero-length {what} line at "
                                f"({a[0]}, {a[1]}) on layer "
                                f"{ent.layer!r} skipped")
                continue
            bucket.append((a, b))

    for ent in drawing.entities:
        role = _layer_role(ent.layer, lmap)
        if role is None:
            low = ent.layer.lower()
            if low not in unmapped_layers:
                unmapped_layers.append(low)
            continue

        if role == "columns":
            if ent.etype in ("POINT", "CIRCLE"):
                p = ent.points[0]
                col_pts.append((snap(p[0]), snap(p[1])))
            elif ent.etype == "LINE":
                (x1, y1, _), (x2, y2, _) = ent.points
                a = (snap(x1), snap(y1))
                b = (snap(x2), snap(y2))
                length = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                if length < _COLUMN_LINE_TOL:
                    col_pts.append(((a[0] + b[0]) / 2.0,
                                    (a[1] + b[1]) / 2.0))
                else:
                    warnings.append(
                        f"LINE of length {length:g} on column layer "
                        f"{ent.layer!r} ignored (columns are POINT/CIRCLE "
                        "or short marker lines)")
            else:
                warnings.append(f"{ent.etype} on column layer "
                                f"{ent.layer!r} ignored")
        elif role == "beams":
            if ent.etype in ("LINE", "LWPOLYLINE"):
                add_segments(ent, beam_segs, "beam")
            else:
                warnings.append(f"{ent.etype} on beam layer "
                                f"{ent.layer!r} ignored (beams are LINEs)")
        elif role == "walls":
            if ent.etype in ("LINE", "LWPOLYLINE"):
                add_segments(ent, wall_segs, "wall")
            else:
                warnings.append(f"{ent.etype} on wall layer "
                                f"{ent.layer!r} ignored (walls are LINEs)")
        elif role == "grid":
            if ent.etype not in ("LINE", "LWPOLYLINE"):
                warnings.append(f"{ent.etype} on grid layer "
                                f"{ent.layer!r} ignored (grids are LINEs)")
                continue
            for (x1, y1), (x2, y2) in _segments(ent):
                a = (snap(x1), snap(y1))
                b = (snap(x2), snap(y2))
                if abs(a[0] - b[0]) < _ZERO_LEN_TOL and \
                   abs(a[1] - b[1]) < _ZERO_LEN_TOL:
                    warnings.append(f"zero-length grid line at "
                                    f"({a[0]}, {a[1]}) skipped")
                elif abs(a[0] - b[0]) < _ZERO_LEN_TOL:   # vertical -> x line
                    grid_x.add(a[0])
                elif abs(a[1] - b[1]) < _ZERO_LEN_TOL:   # horizontal -> y line
                    grid_y.add(a[1])
                else:
                    warnings.append(
                        f"non-axis-parallel grid line ({a[0]}, {a[1]}) -> "
                        f"({b[0]}, {b[1]}) on layer {ent.layer!r} ignored")

    if unmapped_layers:
        warnings.append("entities on unmapped layers ignored: "
                        + ", ".join(sorted(unmapped_layers)))

    # dedup coincident columns (order-preserving)
    seen: set = set()
    unique_cols: List[Tuple[float, float]] = []
    for p in col_pts:
        if p not in seen:
            seen.add(p)
            unique_cols.append(p)
    merged = len(col_pts) - len(unique_cols)
    if merged:
        warnings.append(f"merged {merged} coincident column location(s)")

    # ----------------------------------------------------------------- model
    mdl = BuildingModel(name="DXF Import")
    mdl.add_material(Material("CONC", E=float(E), nu=0.2, unit_weight=24.0))
    mdl.add_section(FrameSection.rectangular(column_section, "CONC", 0.5, 0.5))
    if beam_section != column_section:
        mdl.add_section(FrameSection.rectangular(beam_section, "CONC",
                                                 0.3, 0.6))
    wall_sec_name = wall_section or "DXF-WALL"
    mdl.add_shell_section(ShellSection(wall_sec_name, "CONC",
                                       float(wall_thickness)))
    mdl.set_stories([float(h) for h in stories])
    mdl.base_fixity = "fixed"

    if grid_x or grid_y:
        mdl.grid = GridSystem(sorted(grid_x), sorted(grid_y))
    elif unique_cols:
        mdl.grid = GridSystem(sorted({p[0] for p in unique_cols}),
                              sorted({p[1] for p in unique_cols}))

    n_col = n_beam = n_wall = 0
    for story in mdl.stories:
        z_top = story.elevation
        z_bot = story.elevation - story.height
        for (x, y) in unique_cols:
            n_col += 1
            mdl.add_member("column", column_section,
                           (x, y, z_bot), (x, y, z_top),
                           story=story.name, uid=f"C{n_col}")
        for (a, b) in beam_segs:
            n_beam += 1
            mdl.add_member("beam", beam_section,
                           (a[0], a[1], z_top), (b[0], b[1], z_top),
                           story=story.name, uid=f"B{n_beam}")
        for (a, b) in wall_segs:
            n_wall += 1
            mdl.add_shell("wall", "shell", wall_sec_name,
                          [(a[0], a[1], z_bot), (b[0], b[1], z_bot),
                           (b[0], b[1], z_top), (a[0], a[1], z_top)],
                          mesh_size=1.0, story=story.name, uid=f"W{n_wall}")

    mdl.pattern("DEAD", "dead")
    mdl.pattern("LIVE", "live")
    mdl.add_case("DEAD", {"DEAD": 1.0})
    mdl.add_case("LIVE", {"LIVE": 1.0})

    mdl.validate()
    return mdl, warnings
