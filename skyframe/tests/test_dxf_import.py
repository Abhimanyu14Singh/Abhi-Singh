"""Tests for the DXF import silo (skyframe.io.dxf).

All DXF fixtures are built IN the tests via a tiny group-code emitter —
no external files, no external DXF libraries.
"""

import json
import math

import pytest

from skyframe.core.model import BuildingModel
from skyframe.io.dxf import import_dxf, parse_dxf


# --------------------------------------------------------------------------- #
# Fixture helpers: emit valid group-code pair streams
# --------------------------------------------------------------------------- #
def gc(*pairs):
    """Flatten (code, value) pairs into DXF text lines."""
    out = []
    for code, value in pairs:
        out.append(str(code))
        out.append(str(value))
    return out


def wrap(entity_lines, newline="\n", preamble=(), postamble=()):
    """Wrap entity lines in HEADER-ish noise + SECTION/ENTITIES framing."""
    lines = []
    lines += gc(*preamble)
    lines += gc((0, "SECTION"), (2, "ENTITIES"))
    lines += entity_lines
    lines += gc((0, "ENDSEC"), (0, "EOF"))
    lines += gc(*postamble)
    return newline.join(lines) + newline


def line(x1, y1, x2, y2, layer, z1=0.0, z2=0.0):
    return gc((0, "LINE"), (8, layer),
              (10, x1), (20, y1), (30, z1),
              (11, x2), (21, y2), (31, z2))


def point(x, y, layer, z=0.0):
    return gc((0, "POINT"), (8, layer), (10, x), (20, y), (30, z))


def circle(x, y, r, layer):
    return gc((0, "CIRCLE"), (8, layer), (10, x), (20, y), (40, r))


def lwpolyline(vertices, layer, closed=False):
    pairs = [(0, "LWPOLYLINE"), (8, layer),
             (90, len(vertices)), (70, 1 if closed else 0)]
    for (x, y) in vertices:
        pairs += [(10, x), (20, y)]
    return gc(*pairs)


def plan_fixture(scale=1.0):
    """2x2 column grid + 4 perimeter beams + 1 wall + grid lines."""
    s = scale
    ents = []
    # 4 columns as POINTs
    for (x, y) in [(0, 0), (6, 0), (6, 6), (0, 6)]:
        ents += point(x * s, y * s, "COLUMNS")
    # 4 perimeter beams
    ents += line(0, 0, 6 * s, 0, "BEAMS")
    ents += line(6 * s, 0, 6 * s, 6 * s, "BEAMS")
    ents += line(6 * s, 6 * s, 0, 6 * s, "BEAMS")
    ents += line(0, 6 * s, 0, 0, "BEAMS")
    # one wall along the south edge
    ents += line(0, 0, 6 * s, 0, "WALLS")
    # grid: verticals at x=0,6 and horizontals at y=0,6
    ents += line(0, -1 * s, 0, 7 * s, "GRIDLINES")
    ents += line(6 * s, -1 * s, 6 * s, 7 * s, "GRIDLINES")
    ents += line(-1 * s, 0, 7 * s, 0, "GRIDLINES")
    ents += line(-1 * s, 6 * s, 7 * s, 6 * s, "GRIDLINES")
    return ents


IMPORT_KW = dict(stories=[3.0, 3.0], column_section="DXF-COL",
                 beam_section="DXF-BEAM")


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
class TestParser:
    def test_line_roundtrip(self):
        text = wrap(line(1.25, -2.5, 3.75, 4.0, "Beams", z1=0.5, z2=1.5))
        d = parse_dxf(text)
        assert len(d.entities) == 1
        e = d.entities[0]
        assert e.etype == "LINE"
        assert e.layer == "Beams"
        assert e.points == [(1.25, -2.5, 0.5), (3.75, 4.0, 1.5)]
        assert d.warnings == []

    def test_point_and_circle_roundtrip(self):
        text = wrap(point(0.1, 0.2, "COLS", z=0.3)
                    + circle(5.5, 6.5, 0.25, "COL-MARKS"))
        d = parse_dxf(text)
        assert [e.etype for e in d.entities] == ["POINT", "CIRCLE"]
        p, c = d.entities
        assert p.points == [(0.1, 0.2, 0.3)]
        assert p.layer == "COLS"
        assert c.points == [(5.5, 6.5, 0.0)]
        assert c.radius == 0.25
        assert c.layer == "COL-MARKS"

    def test_lwpolyline_roundtrip_open_and_closed(self):
        verts = [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0)]
        text = wrap(lwpolyline(verts, "BEAMS")
                    + lwpolyline(verts, "WALLS", closed=True))
        d = parse_dxf(text)
        assert len(d.entities) == 2
        open_pl, closed_pl = d.entities
        assert open_pl.etype == "LWPOLYLINE" and not open_pl.closed
        assert closed_pl.closed
        assert open_pl.points == [(x, y, 0.0) for x, y in verts]
        assert closed_pl.layer == "WALLS"

    def test_crlf_and_lf_parse_identically(self):
        lf = wrap(plan_fixture(), newline="\n")
        crlf = wrap(plan_fixture(), newline="\r\n")
        d1, d2 = parse_dxf(lf), parse_dxf(crlf)
        assert [e.etype for e in d1.entities] == [e.etype for e in d2.entities]
        assert [e.points for e in d1.entities] == [e.points for e in d2.entities]
        assert d1.warnings == d2.warnings == []

    def test_extra_whitespace_around_codes(self):
        text = wrap(line(0, 0, 1, 0, "BEAMS"))
        text = text.replace("\n0\n", "\n  0  \n").replace("\n10\n", "\n 10\n")
        d = parse_dxf(text)
        assert len(d.entities) == 1
        assert d.entities[0].points[0] == (0.0, 0.0, 0.0)

    def test_content_before_and_after_entities_section(self):
        text = wrap(line(0, 0, 1, 0, "BEAMS"),
                    preamble=((0, "SECTION"), (2, "HEADER"), (9, "$ACADVER"),
                              (1, "AC1027"), (0, "ENDSEC")),
                    postamble=((0, "SECTION"), (2, "OBJECTS"),
                               (0, "DICTIONARY"), (0, "ENDSEC")))
        d = parse_dxf(text)
        assert len(d.entities) == 1     # only the ENTITIES section is read
        assert d.entities[0].etype == "LINE"

    def test_unknown_entity_skipped_with_warning(self):
        text = wrap(gc((0, "ARC"), (8, "MISC"), (10, 0), (20, 0), (40, 2))
                    + line(0, 0, 1, 0, "BEAMS"))
        d = parse_dxf(text)
        assert len(d.entities) == 1
        assert d.entities[0].etype == "LINE"
        assert len(d.warnings) == 1
        assert "ARC" in d.warnings[0]

    def test_insert_ignored_with_warning(self):
        text = wrap(gc((0, "INSERT"), (8, "BLOCKS"), (2, "CHAIR"),
                       (10, 1), (20, 2)))
        d = parse_dxf(text)
        assert d.entities == []
        assert len(d.warnings) == 1
        assert "INSERT" in d.warnings[0]


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #
class TestImport:
    def test_counts_and_coordinates(self):
        model, warnings = import_dxf(wrap(plan_fixture()), **IMPORT_KW)
        cols = [m for m in model.members if m.kind == "column"]
        beams = [m for m in model.members if m.kind == "beam"]
        assert len(cols) == 8           # 4 locations x 2 stories
        assert len(beams) == 8          # 4 lines x 2 stories
        assert len(model.shells) == 2   # 1 wall x 2 stories
        assert warnings == []

        # column coordinates exact: verticals through (0,0) etc.
        c1 = [m for m in cols if m.story == "Story1" and m.pi[:2] == (0.0, 0.0)]
        assert len(c1) == 1
        assert c1[0].pi == (0.0, 0.0, 0.0) and c1[0].pj == (0.0, 0.0, 3.0)
        c2 = [m for m in cols if m.story == "Story2" and m.pi[:2] == (6.0, 6.0)]
        assert c2[0].pi == (6.0, 6.0, 3.0) and c2[0].pj == (6.0, 6.0, 6.0)

        # beams at story TOP elevations
        assert {m.pi[2] for m in beams if m.story == "Story1"} == {3.0}
        assert {m.pi[2] for m in beams if m.story == "Story2"} == {6.0}
        s1_beams = {tuple(sorted((m.pi[:2], m.pj[:2])))
                    for m in beams if m.story == "Story1"}
        assert s1_beams == {
            (((0.0, 0.0)), ((6.0, 0.0))),
            (((6.0, 0.0)), ((6.0, 6.0))),
            (((0.0, 6.0)), ((6.0, 6.0))),
            (((0.0, 0.0)), ((0.0, 6.0))),
        }

        # walls are full-story-height shell regions
        w1 = model.shells[0]
        assert w1.kind == "wall" and w1.behavior == "shell"
        assert w1.mesh_size == 1.0
        zs = sorted({c[2] for c in w1.corners})
        assert zs == [0.0, 3.0]
        w2 = model.shells[1]
        assert sorted({c[2] for c in w2.corners}) == [3.0, 6.0]

    def test_grid_extracted_sorted(self):
        model, _ = import_dxf(wrap(plan_fixture()), **IMPORT_KW)
        assert model.grid is not None
        assert model.grid.x_lines == [0.0, 6.0]
        assert model.grid.y_lines == [0.0, 6.0]

    def test_grid_derived_from_columns_when_no_grid_layer(self):
        ents = []
        for (x, y) in [(6, 0), (0, 0), (6, 6), (0, 6)]:
            ents += point(x, y, "COLUMNS")
        model, _ = import_dxf(wrap(ents), **IMPORT_KW)
        assert model.grid.x_lines == [0.0, 6.0]
        assert model.grid.y_lines == [0.0, 6.0]

    def test_sections_materials_patterns_created(self):
        model, _ = import_dxf(wrap(plan_fixture()), **IMPORT_KW,
                              wall_thickness=0.25, E=30e6)
        assert model.materials["CONC"].E == 30e6
        col = model.sections["DXF-COL"]
        assert (col.b, col.h) == (0.5, 0.5)
        beam = model.sections["DXF-BEAM"]
        assert (beam.b, beam.h) == (0.3, 0.6)
        wall = model.shell_sections["DXF-WALL"]
        assert wall.thickness == 0.25
        assert model.base_fixity == "fixed"
        assert model.patterns["DEAD"].kind == "dead"
        assert model.patterns["LIVE"].kind == "live"
        assert model.cases["DEAD"].patterns == {"DEAD": 1.0}
        assert model.cases["LIVE"].patterns == {"LIVE": 1.0}
        assert [s.name for s in model.stories] == ["Story1", "Story2"]
        assert [s.height for s in model.stories] == [3.0, 3.0]

    def test_to_dict_json_serializable_and_roundtrips(self):
        model, _ = import_dxf(wrap(plan_fixture()), **IMPORT_KW)
        d = model.to_dict()
        blob = json.dumps(d)            # must be JSON-serializable
        model2 = BuildingModel.from_dict(json.loads(blob))
        assert model2.to_dict() == d    # exact round trip

    def test_unit_scale_mm_matches_metres(self):
        m_model, _ = import_dxf(wrap(plan_fixture(scale=1.0)), **IMPORT_KW)
        mm_model, _ = import_dxf(wrap(plan_fixture(scale=1000.0)),
                                 unit_scale=0.001, **IMPORT_KW)
        assert len(mm_model.members) == len(m_model.members)
        for a, b in zip(m_model.members, mm_model.members):
            assert a.uid == b.uid and a.kind == b.kind
            for i in range(3):
                assert abs(a.pi[i] - b.pi[i]) < 1e-9
                assert abs(a.pj[i] - b.pj[i]) < 1e-9
        for ra, rb in zip(m_model.shells, mm_model.shells):
            for ca, cb in zip(ra.corners, rb.corners):
                assert max(abs(u - v) for u, v in zip(ca, cb)) < 1e-9
        assert m_model.grid.x_lines == mm_model.grid.x_lines
        assert m_model.grid.y_lines == mm_model.grid.y_lines

    def test_layer_map_override(self):
        ents = line(0, 0, 6, 0, "FRAMING") + point(0, 0, "SUPPORTS")
        model, warnings = import_dxf(
            wrap(ents), stories=[3.0], column_section="DXF-COL",
            beam_section="DXF-BEAM",
            layer_map={"framing": "beams", "SUPPORTS": "columns"})
        beams = [m for m in model.members if m.kind == "beam"]
        cols = [m for m in model.members if m.kind == "column"]
        assert len(beams) == 1
        assert beams[0].pi == (0.0, 0.0, 3.0)
        assert beams[0].pj == (6.0, 0.0, 3.0)
        assert len(cols) == 1
        assert warnings == []

    def test_dedup_and_zero_length_warnings(self):
        ents = (point(0, 0, "COLUMNS") + point(0, 0, "COLUMNS")
                + point(6, 0, "COLUMNS")
                + line(1, 1, 1, 1, "BEAMS")          # zero length
                + line(0, 0, 6, 0, "BEAMS"))
        model, warnings = import_dxf(wrap(ents), stories=[3.0],
                                     column_section="DXF-COL",
                                     beam_section="DXF-BEAM")
        cols = [m for m in model.members if m.kind == "column"]
        beams = [m for m in model.members if m.kind == "beam"]
        assert len(cols) == 2           # coincident columns merged
        assert len(beams) == 1          # zero-length line skipped
        assert any("zero-length" in w for w in warnings)
        assert any("coincident column" in w for w in warnings)

    def test_short_line_column_markers_and_circle_columns(self):
        ents = (line(0, 0, 0.005, 0, "COL-MARKS")    # short marker line
                + circle(6, 0, 0.3, "COLUMNS"))
        model, warnings = import_dxf(wrap(ents), stories=[3.0],
                                     column_section="DXF-COL",
                                     beam_section="DXF-BEAM")
        cols = [m for m in model.members if m.kind == "column"]
        assert len(cols) == 2
        xs = sorted(round(m.pi[0], 6) for m in cols)
        assert xs == [0.0025, 6.0]

    def test_non_axis_parallel_grid_line_warns(self):
        ents = (line(0, 0, 6, 6, "GRID") + line(0, -1, 0, 7, "GRID")
                + point(0, 0, "COLUMNS"))
        model, warnings = import_dxf(wrap(ents), stories=[3.0],
                                     column_section="DXF-COL",
                                     beam_section="DXF-BEAM")
        assert any("non-axis-parallel" in w for w in warnings)
        assert model.grid.x_lines == [0.0]


# --------------------------------------------------------------------------- #
# Engine smoke
# --------------------------------------------------------------------------- #
class TestEngineSmoke:
    def test_modal_analysis_runs(self):
        pytest.importorskip("openseespy.opensees")
        from skyframe.engine.opensees_engine import OpenSeesEngine

        model, warnings = import_dxf(wrap(plan_fixture()), **IMPORT_KW)
        model.story_masses = {s.name: 10.0 for s in model.stories}
        model.num_modes = 3
        modal = OpenSeesEngine(model).run_modal()
        assert len(modal.periods) >= 1
        for T in modal.periods:
            assert T > 0.0 and math.isfinite(T)
