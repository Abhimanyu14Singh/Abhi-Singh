"""3D DXF export / import with SkyFrame layer conventions.

Export (``export_dxf(model, include=None, unit_scale=1.0)`` -> ``(text,
report)``) writes an ASCII DXF (AC1009 / R12 entities, readable by every
CAD program):

* frame members: 3D ``LINE`` on layer ``SF-<kind>-<section>`` (kind
  ``COL`` / ``BEAM`` / ``BRACE``);
* walls / slabs: ``3DFACE`` (3 or 4 corners) or a closed 3D ``POLYLINE``
  (any other corner count, or a region with openings) on layer
  ``SF-WALL-<section>`` / ``SF-SLAB-<section>`` (membrane slabs:
  ``SF-DECK-<section>``); openings as closed 3D polylines on
  ``SF-OPENING``;
* links on ``SF-LINK``; grid lines (at the base level) on ``SF-GRID``.

Layer names are sanitised (``<>/\\":;?*|=,`` and spaces -> ``_``); the
exact section name of every layer is written as a ``999`` comment
``SF-MAP <layer>=<section>`` in the header, so the import maps layers back
to section names exactly.

Import (``import_dxf_layers(text, sections_from=None, unit_scale=1.0,
layer_map=None)`` -> ``(model, warnings)``) reads LINE / 3DFACE /
POLYLINE+VERTEX / LWPOLYLINE on ``SF-*`` layers (or any layer named in
``layer_map`` ``{layer: {"kind": ..., "section": ...}}``), takes stories
from the distinct elevations (lowest = base), assigns each object to the
story at its top, and resolves sections from ``sections_from`` (a model
whose materials / frame / shell sections are copied) or creates
placeholders (0.3 x 0.6 frames, 0.2 m shells) with a warning.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from skyframe.io.dxf import _entities_region, _pairs

__all__ = ["export_dxf", "import_dxf_layers", "layer_name"]

_BAD = re.compile(r'[<>/\\":;?*|=,`\s]')
_KIND_LAYER = {"column": "COL", "beam": "BEAM", "brace": "BRACE"}
_LAYER_KIND = {"COL": "column", "BEAM": "beam", "BRACE": "brace",
               "WALL": "wall", "SLAB": "slab", "DECK": "deck",
               "OPENING": "opening", "LINK": "link", "GRID": "grid"}
_COLORS = {"COL": 1, "BEAM": 3, "BRACE": 6, "WALL": 5, "SLAB": 4,
           "DECK": 4, "OPENING": 2, "LINK": 30, "GRID": 8}


def layer_name(kind: str, section: str = "") -> str:
    base = f"SF-{kind}" + (f"-{section}" if section else "")
    return _BAD.sub("_", base)


def _num(v: float) -> str:
    r = repr(float(v))
    return r[:-2] if r.endswith(".0") else r


def export_dxf(model, include: Optional[dict] = None,
               unit_scale: float = 1.0) -> Tuple[str, dict]:
    """See the module docstring. ``unit_scale`` = drawing units per metre
    (1000 for a millimetre drawing)."""
    from skyframe.core.model import BuildingModel
    if isinstance(model, dict):
        model = BuildingModel.from_dict(model)
    inc = {"frames": True, "shells": True, "links": True, "grids": True}
    inc.update({k: bool(v) for k, v in (include or {}).items() if k in inc})
    k = float(unit_scale)
    layers: Dict[str, str] = {}             # layer -> exact section
    ents: List[str] = []
    counts: Dict[str, int] = {}
    skipped: List[str] = []

    def lay(kind, sec="") -> str:
        name = layer_name(kind, sec)
        if name in layers and layers[name] != sec:
            skipped.append(f"layer {name!r} shared by sections "
                           f"{layers[name]!r} and {sec!r} (sanitised names "
                           "collide)")
        layers.setdefault(name, sec)
        return name

    def add(*pairs) -> None:
        for code, val in pairs:
            ents.append(f"{code}\n{val}")

    def line(layer, a, b) -> None:
        add((0, "LINE"), (8, layer), (10, _num(a[0] * k)),
            (20, _num(a[1] * k)), (30, _num(a[2] * k)), (11, _num(b[0] * k)),
            (21, _num(b[1] * k)), (31, _num(b[2] * k)))

    def poly(layer, pts) -> None:
        add((0, "POLYLINE"), (8, layer), (66, 1), (10, "0"), (20, "0"),
            (30, "0"), (70, 9))
        for p in pts:
            add((0, "VERTEX"), (8, layer), (10, _num(p[0] * k)),
                (20, _num(p[1] * k)), (30, _num(p[2] * k)), (70, 32))
        add((0, "SEQEND"), (8, layer))

    if inc["frames"]:
        for m in model.members:
            line(lay(_KIND_LAYER.get(m.kind, "BRACE"), m.section), m.pi,
                 m.pj)
        counts["frames"] = len(model.members)
    if inc["shells"]:
        n_open = 0
        for r in model.shells:
            kind = "WALL" if r.kind == "wall" else (
                "DECK" if r.behavior == "membrane" else "SLAB")
            ly = lay(kind, r.section)
            c = [tuple(float(v) for v in p) for p in r.corners]
            if len(c) in (3, 4) and not r.openings:
                q = c + [c[-1]] * (4 - len(c))
                pairs = [(0, "3DFACE"), (8, ly)]
                for i, p in enumerate(q):
                    pairs += [(10 + i, _num(p[0] * k)),
                              (20 + i, _num(p[1] * k)),
                              (30 + i, _num(p[2] * k))]
                add(*pairs)
            else:
                poly(ly, c)
            for op in r.openings:
                if op.polygon:
                    pts = [tuple(p) for p in op.polygon]
                else:
                    pts = [r.map_uv(op.u0, op.v0), r.map_uv(op.u1, op.v0),
                           r.map_uv(op.u1, op.v1), r.map_uv(op.u0, op.v1)]
                poly(lay("OPENING"), pts)
                n_open += 1
        counts["areas"] = len(model.shells)
        counts["openings"] = n_open
    if inc["links"]:
        for lk in model.links:
            line(lay("LINK"), lk.pi, lk.pj)
        counts["links"] = len(model.links)
    if inc["grids"]:
        z0 = (model.stories[0].elevation - model.stories[0].height) \
            if model.stories else 0.0
        n = 0
        for g in model.effective_grids():
            for gl in g.lines_global():
                pts = gl["points"]
                for a, b in zip(pts[:-1], pts[1:]):
                    line(lay("GRID"), (a[0], a[1], z0), (b[0], b[1], z0))
                    n += 1
        counts["grid_lines"] = n
    head = ["999", "SkyFrame DXF export (layers SF-<kind>-<section>)",
            "0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009",
            "9", "$INSUNITS", "70", "6" if k == 1.0 else
            ("4" if k == 1000.0 else "0")]
    for name, sec in layers.items():
        head += ["999", f"SF-MAP {name}={sec}"]
    head += ["0", "ENDSEC", "0", "SECTION", "2", "TABLES", "0", "TABLE",
             "2", "LAYER", "70", str(len(layers))]
    for name in layers:
        kind = name.split("-")[1] if "-" in name else ""
        head += ["0", "LAYER", "2", name, "70", "0", "62",
                 str(_COLORS.get(kind, 7)), "6", "CONTINUOUS"]
    head += ["0", "ENDTAB", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    text = "\n".join(head + ents + ["0", "ENDSEC", "0", "EOF", ""])
    counts["layers"] = len(layers)
    skipped.append("loads, supports, properties and analysis data are not "
                   "written (DXF carries geometry and layer = section only)")
    return text, {"format": "dxf", "written": counts,
                  "skipped": list(dict.fromkeys(skipped)),
                  "layers": dict(layers)}


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
def _entities(text: str, warnings: List[str]):
    pairs = _pairs(text)
    region = _entities_region(pairs, warnings)
    out = []
    cur = None
    poly = None
    for code, val in region:
        if code == 0:
            if cur is not None:
                if cur[0] == "VERTEX" and poly is not None:
                    poly[2].append(cur[1])
                elif cur[0] == "POLYLINE":
                    poly = cur + ([],)
                elif cur[0] == "SEQEND":
                    if poly is not None:
                        out.append(poly)
                    poly = None
                else:
                    out.append(cur + ([],))
            cur = (val.upper(), [])
        elif cur is not None:
            cur[1].append((code, val))
    if cur is not None and cur[0] not in ("VERTEX", "SEQEND", "POLYLINE"):
        out.append(cur + ([],))
    return out


def _g(pairs, code, default=None):
    for c, v in pairs:
        if c == code:
            try:
                return float(v)
            except ValueError:
                return default
    return default


def _layer(pairs) -> str:
    for c, v in pairs:
        if c == 8:
            return v
    return "0"


def import_dxf_layers(text: str, sections_from=None, unit_scale: float = 1.0,
                      layer_map: Optional[dict] = None):
    """See the module docstring."""
    from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                     ShellSection)
    from skyframe.io.e2k_read import _containing, _opening
    warnings: List[str] = []
    s = 1.0 / float(unit_scale)
    smap: Dict[str, str] = {}
    for code, val in _pairs(text):
        if code == 999 and val.startswith("SF-MAP ") and "=" in val:
            name, sec = val[7:].split("=", 1)
            smap[name] = sec
    lmap = {str(k): v for k, v in (layer_map or {}).items()}

    def role(layer: str) -> Optional[Tuple[str, str]]:
        if layer in lmap:
            v = lmap[layer]
            return (str(v.get("kind")), str(v.get("section", "")))
        if not layer.upper().startswith("SF-"):
            return None
        parts = layer.split("-", 2)
        kind = _LAYER_KIND.get(parts[1].upper()) if len(parts) > 1 else None
        if kind is None:
            return None
        sec = smap.get(layer, parts[2] if len(parts) > 2 else "")
        return kind, sec

    frames, regions, opens, links = [], [], [], []
    ignored: Dict[str, int] = {}
    for etype, pairs, verts in _entities(text, warnings):
        ly = _layer(pairs)
        r = role(ly)
        if r is None:
            ignored[ly] = ignored.get(ly, 0) + 1
            continue
        kind, sec = r
        if etype == "LINE":
            a = [_g(pairs, 10, 0.0) * s, _g(pairs, 20, 0.0) * s,
                 _g(pairs, 30, 0.0) * s]
            b = [_g(pairs, 11, 0.0) * s, _g(pairs, 21, 0.0) * s,
                 _g(pairs, 31, 0.0) * s]
            if kind in ("column", "beam", "brace"):
                frames.append((kind, sec, a, b))
            elif kind == "link":
                links.append((a, b))
        elif etype == "3DFACE":
            pts = []
            for i in range(4):
                p = [_g(pairs, 10 + i, 0.0) * s, _g(pairs, 20 + i, 0.0) * s,
                     _g(pairs, 30 + i, 0.0) * s]
                if not pts or p != pts[-1]:
                    pts.append(p)
            if len(pts) > 3 and pts[-1] == pts[0]:
                pts.pop()
            if kind in ("wall", "slab", "deck"):
                regions.append((kind, sec, pts))
        elif etype in ("POLYLINE", "LWPOLYLINE"):
            if etype == "POLYLINE":
                pts = [[_g(v, 10, 0.0) * s, _g(v, 20, 0.0) * s,
                        _g(v, 30, 0.0) * s] for v in verts]
            else:
                z = _g(pairs, 38, 0.0) * s
                xs = [float(v) * s for c, v in pairs if c == 10]
                ys = [float(v) * s for c, v in pairs if c == 20]
                pts = [[x, y, z] for x, y in zip(xs, ys)]
            if len(pts) > 3 and pts[-1] == pts[0]:
                pts.pop()
            if kind in ("wall", "slab", "deck"):
                regions.append((kind, sec, pts))
            elif kind == "opening":
                opens.append(pts)
    for ly, n in sorted(ignored.items()):
        warnings.append(f"{n} entit(y/ies) on non-SkyFrame layer {ly!r} "
                        "ignored")

    # stories from distinct elevations (lowest = base)
    zs = sorted({round(p[2], 9) for f in frames for p in f[2:4]} |
                {round(p[2], 9) for r in regions for p in r[2]})
    src = sections_from
    mdl = BuildingModel(name="DXF Import")
    mdl.frame_axis_convention = "etabs"
    if src is not None:
        for mt in src.materials.values():
            mdl.add_material(mt)
    if not mdl.materials:
        mdl.add_material(Material("CONC", E=25e6, nu=0.2, unit_weight=24.0))
    mat0 = next(iter(mdl.materials))
    if len(zs) >= 2:
        mdl.set_stories([zs[i] - zs[i - 1] for i in range(1, len(zs))])
        base = zs[0]
        for st in mdl.stories:
            st.elevation = st.elevation + base
            for z in zs:
                if abs(z - st.elevation) < 1e-9:
                    st.elevation = z
    levels = {st.name: st.elevation for st in mdl.stories}

    def story_at(z: float) -> str:
        for name, e in levels.items():
            if abs(e - z) < 1e-9:
                return name
        above = [(e, n) for n, e in levels.items() if e > z]
        return min(above)[1] if above else ""

    def frame_sec(name: str) -> str:
        name = name or "DXF-FRAME"
        if name not in mdl.sections:
            if src is not None and name in src.sections:
                mdl.sections[name] = src.sections[name]
                if name in getattr(src, "builtup_sections", {}):
                    mdl.builtup_sections[name] = src.builtup_sections[name]
            else:
                warnings.append(f"frame section {name!r} not defined — "
                                "0.3x0.6 placeholder rectangle")
                mdl.add_section(FrameSection.rectangular(name, mat0, 0.3,
                                                         0.6))
        return name

    def shell_sec(name: str) -> str:
        name = name or "DXF-SHELL"
        if name not in mdl.shell_sections:
            if src is not None and name in src.shell_sections:
                mdl.shell_sections[name] = src.shell_sections[name]
            else:
                warnings.append(f"shell section {name!r} not defined — "
                                "0.2 m placeholder")
                mdl.add_shell_section(ShellSection(name, mat0, 0.2))
        return name

    counters: Dict[str, int] = {}

    def uid(prefix: str) -> str:
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}{counters[prefix]}"

    for kind, sec, a, b in frames:
        mdl.add_member(kind, frame_sec(sec), tuple(a), tuple(b),
                       story=story_at(max(a[2], b[2])),
                       uid=uid({"column": "C", "beam": "B"}.get(kind, "D")))
    shells = []
    for kind, sec, pts in regions:
        sd = {"uid": uid("W" if kind == "wall" else "F"),
              "kind": "wall" if kind == "wall" else "slab",
              "behavior": "membrane" if kind == "deck" else "shell",
              "section": shell_sec(sec), "corners": pts,
              "story": story_at(max(p[2] for p in pts)), "openings": []}
        shells.append(sd)
    for pts in opens:
        parent = _containing(shells, pts)
        if parent is None:
            warnings.append("opening outline outside every area; ignored")
            continue
        parent["openings"].append(_opening(parent["corners"], pts))
    from skyframe.core.model import Opening
    for sd in shells:
        mdl.add_shell(sd["kind"], sd["behavior"], sd["section"],
                      [tuple(p) for p in sd["corners"]], story=sd["story"],
                      uid=sd["uid"],
                      openings=[Opening(o["u0"], o["v0"], o["u1"], o["v1"],
                                        o.get("polygon"))
                                for o in sd["openings"]])
    for a, b in links:
        ref = None
        if src is not None:
            ref = next((lk for lk in src.links
                        if list(lk.pi) == a and list(lk.pj) == b), None)
        if ref is None:
            warnings.append(f"link {a}->{b}: no property in the source "
                            "model (DXF carries geometry only); skipped")
            continue
        mdl.add_link(tuple(a), tuple(b), stiffness=list(ref.stiffness),
                     uid=uid("L"), link_type=ref.link_type,
                     params=dict(ref.params))
    mdl.base_fixity = "fixed"
    mdl.validate()
    return mdl, warnings
