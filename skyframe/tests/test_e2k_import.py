"""Tests for the ETABS .e2k import silo (skyframe.io.e2k).

All e2k fixtures are built IN the tests via a tiny emitter — no external
files.  The reference building is a 2-story, 2x2-bay concrete frame:
3x3 column grid at x, y in {0, 6, 12} m, stories (ETABS TOP-DOWN order!)
STORY2 (h = 3.0 m) above STORY1 (h = 3.2 m), so the SkyFrame elevations
are Story1 top = 3.2 m and Story2 top = 6.2 m.
"""

import json
import math

import pytest

from skyframe.core.model import BuildingModel
from skyframe.io.e2k import import_e2k

KIP = 4.4482216152605      # kN per kip (exact)
FT = 0.3048                # m per ft (exact)


# --------------------------------------------------------------------------- #
# fixture emitter
# --------------------------------------------------------------------------- #
def grid_points(scale=1.0):
    """3x3 plan grid: point name -> (x, y) in FILE units."""
    pts = {}
    n = 1
    for iy, y in enumerate((0.0, 6.0, 12.0)):
        for ix, x in enumerate((0.0, 6.0, 12.0)):
            pts[str(n)] = (x * scale, y * scale)
            n += 1
    return pts


def beam_lines():
    """12 beam connectivities (point-name pairs) on the 3x3 grid."""
    def p(ix, iy):
        return str(1 + ix + 3 * iy)
    segs = []
    for iy in range(3):                     # x-direction beams
        for ix in range(2):
            segs.append((p(ix, iy), p(ix + 1, iy)))
    for ix in range(3):                     # y-direction beams
        for iy in range(2):
            segs.append((p(ix, iy), p(ix, iy + 1)))
    return segs


def e2k_text(scale=1.0, E=25e6, units=None, extra=()):
    """Emit the reference 2-story 2x2-bay building as e2k text.

    ``scale`` multiplies every length in the FILE (coordinates, story
    heights, section dims); ``E`` is written as-is (file force/length^2);
    ``units`` (force, length) adds a $ UNITS line; ``extra`` lines are
    appended verbatim at the end.
    """
    s = scale
    out = ["$ PROGRAM INFORMATION", '  PROGRAM  "ETABS"  VERSION "9.7.4"']
    if units is not None:
        out += ["$ UNITS", f'  UNITS  "{units[0]}"  "{units[1]}"']
    out += ["$ STORIES - IN SEQUENCE FROM TOP",      # ETABS lists TOP-DOWN
            f'  STORY "STORY2"  HEIGHT {3.0 * s}',
            f'  STORY "STORY1"  HEIGHT {3.2 * s}']
    out += ["$ POINT COORDINATES"]
    for name, (x, y) in grid_points(s).items():
        out.append(f'  POINT "{name}"  {x} {y}')
    out += ["$ MATERIAL PROPERTIES",
            f'  MATERIAL "CONC"  E {E}  U 0.2']
    out += ["$ FRAME SECTIONS",
            f'  FRAMESECTION "COL40"  MATERIAL "CONC"  '
            f'SHAPE "Concrete Rectangular"  D {0.4 * s}  B {0.4 * s}',
            f'  FRAMESECTION "BM3060"  MATERIAL "CONC"  '
            f'SHAPE "Concrete Rectangular"  D {0.6 * s}  B {0.3 * s}']
    out += ["$ LINE CONNECTIVITIES"]
    for n in range(1, 10):
        out.append(f'  LINE "C{n}"  COLUMN "{n}" "{n}"  1')
    for i, (a, b) in enumerate(beam_lines(), start=1):
        out.append(f'  LINE "B{i}"  BEAM "{a}" "{b}"  0')
    out += ["$ LINE ASSIGNS"]
    for story in ("STORY2", "STORY1"):
        for n in range(1, 10):
            out.append(f'  LINEASSIGN "C{n}" "{story}"  SECTION "COL40"')
        for i in range(1, 13):
            out.append(f'  LINEASSIGN "B{i}" "{story}"  SECTION "BM3060"')
    out += ["$ LOAD PATTERNS",
            '  LOADPATTERN "DEAD"  TYPE "Dead"  SELFWEIGHT 1',
            '  LOADPATTERN "LIVE"  TYPE "Reducible Live"  SELFWEIGHT 0',
            '  LOADPATTERN "EQX"  TYPE "Quake"  SELFWEIGHT 0']
    out += list(extra)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# stories: ETABS top-down order -> SkyFrame bottom-up elevations
# --------------------------------------------------------------------------- #
class TestStories:
    def test_story_order_heights_elevations(self):
        model, warnings = import_e2k(e2k_text())
        assert [s.name for s in model.stories] == ["STORY1", "STORY2"]
        assert [s.height for s in model.stories] == [3.2, 3.0]
        # top-down input: STORY2 (listed first) is the TOP story
        assert [s.elevation for s in model.stories] == [3.2, 6.2]
        assert warnings == []


# --------------------------------------------------------------------------- #
# members, sections, materials
# --------------------------------------------------------------------------- #
class TestMembers:
    def test_counts(self):
        model, warnings = import_e2k(e2k_text())
        cols = [m for m in model.members if m.kind == "column"]
        beams = [m for m in model.members if m.kind == "beam"]
        assert len(cols) == 18              # 9 columns x 2 stories
        assert len(beams) == 24             # 12 beams x 2 stories
        assert warnings == []
        # every uid unique and story-qualified
        assert len({m.uid for m in model.members}) == 42

    def test_column_coordinates_and_spans(self):
        model, _ = import_e2k(e2k_text())
        by_uid = {m.uid: m for m in model.members}
        # point "5" is the plan centre (6, 6)
        c5s1 = by_uid["C5-STORY1"]
        assert c5s1.pi == (6.0, 6.0, 0.0)
        assert c5s1.pj == (6.0, 6.0, 3.2)
        assert c5s1.story == "STORY1"
        c5s2 = by_uid["C5-STORY2"]
        assert c5s2.pi == (6.0, 6.0, 3.2)
        assert c5s2.pj == (6.0, 6.0, 6.2)

    def test_beam_coordinates_at_story_top(self):
        model, _ = import_e2k(e2k_text())
        by_uid = {m.uid: m for m in model.members}
        # B1 connects points "1" (0,0) and "2" (6,0)
        b1s1 = by_uid["B1-STORY1"]
        assert b1s1.pi == (0.0, 0.0, 3.2) and b1s1.pj == (6.0, 0.0, 3.2)
        b1s2 = by_uid["B1-STORY2"]
        assert b1s2.pi == (0.0, 0.0, 6.2) and b1s2.pj == (6.0, 0.0, 6.2)
        # all Story-2 beams live at z = 6.2
        zs = {m.pi[2] for m in model.members
              if m.kind == "beam" and m.story == "STORY2"}
        assert zs == {6.2}

    def test_section_d_is_depth_h_and_b_is_width(self):
        # ETABS D 0.6 B 0.3 must become SkyFrame h = 0.6, b = 0.3 —
        # a D/B swap would flip I33/I22.
        model, _ = import_e2k(e2k_text())
        bm = model.sections["BM3060"]
        assert (bm.b, bm.h) == (0.3, 0.6)
        assert bm.A == pytest.approx(0.18, rel=1e-12)
        assert bm.I33 == pytest.approx(0.3 * 0.6 ** 3 / 12.0, rel=1e-12)
        assert bm.I22 == pytest.approx(0.6 * 0.3 ** 3 / 12.0, rel=1e-12)
        assert bm.I33 > bm.I22              # depth about the major axis
        col = model.sections["COL40"]
        assert (col.b, col.h) == (0.4, 0.4)

    def test_material_and_grid(self):
        model, _ = import_e2k(e2k_text())
        assert model.materials["CONC"].E == pytest.approx(25e6)
        assert model.materials["CONC"].nu == pytest.approx(0.2)
        assert model.grid.x_lines == [0.0, 6.0, 12.0]
        assert model.grid.y_lines == [0.0, 6.0, 12.0]

    def test_patterns_imported_with_kinds_and_cases(self):
        model, _ = import_e2k(e2k_text())
        assert model.patterns["DEAD"].kind == "dead"
        assert model.patterns["LIVE"].kind == "live"
        assert model.patterns["EQX"].kind == "quake"
        for name in ("DEAD", "LIVE", "EQX"):
            assert model.cases[name].patterns == {name: 1.0}


# --------------------------------------------------------------------------- #
# units
# --------------------------------------------------------------------------- #
class TestUnits:
    def test_n_mm_file_identical_to_kn_m(self):
        # same building written in N/mm: lengths x1000, E in N/mm^2 (MPa)
        kn_m, w1 = import_e2k(e2k_text(scale=1.0, E=25e6,
                                       units=("kN", "m")))
        n_mm, w2 = import_e2k(e2k_text(scale=1000.0, E=25e3,
                                       units=("N", "mm")))
        assert w1 == w2 == []
        assert n_mm.materials["CONC"].E == pytest.approx(25e6, rel=1e-9)
        assert len(kn_m.members) == len(n_mm.members)
        for a, b in zip(kn_m.members, n_mm.members):
            assert a.uid == b.uid and a.kind == b.kind
            for i in range(3):
                assert abs(a.pi[i] - b.pi[i]) < 1e-9
                assert abs(a.pj[i] - b.pj[i]) < 1e-9
        for name in ("COL40", "BM3060"):
            sa, sb = kn_m.sections[name], n_mm.sections[name]
            assert sb.b == pytest.approx(sa.b, rel=1e-9)
            assert sb.h == pytest.approx(sa.h, rel=1e-9)
            assert sb.A == pytest.approx(sa.A, rel=1e-9)
            assert sb.I33 == pytest.approx(sa.I33, rel=1e-9)
        assert [s.elevation for s in n_mm.stories] == \
            pytest.approx([s.elevation for s in kn_m.stories], rel=1e-9)

    def test_units_kwarg_used_when_no_units_line(self):
        # no $ UNITS line: the kwarg decides
        n_mm, _ = import_e2k(e2k_text(scale=1000.0, E=25e3),
                             units=("N", "mm"))
        assert n_mm.materials["CONC"].E == pytest.approx(25e6, rel=1e-9)
        assert n_mm.story_by_name("STORY1").elevation == \
            pytest.approx(3.2, rel=1e-9)

    def test_units_line_overrides_kwarg(self):
        # file says N mm; a wrong kwarg must lose
        n_mm, _ = import_e2k(e2k_text(scale=1000.0, E=25e3,
                                      units=("N", "mm")),
                             units=("kN", "m"))
        assert n_mm.materials["CONC"].E == pytest.approx(25e6, rel=1e-9)

    def test_kip_ft_conversion(self):
        # E = 1 kip/ft^2 = 4.4482216152605/0.3048^2 kPa; lengths in ft
        model, w = import_e2k(e2k_text(scale=1.0 / FT, E=1.0,
                                       units=("kip", "ft")))
        assert w == []
        assert model.materials["CONC"].E == \
            pytest.approx(KIP / FT ** 2, rel=1e-9)
        assert model.story_by_name("STORY2").elevation == \
            pytest.approx(6.2, rel=1e-9)
        b1 = next(m for m in model.members if m.uid == "B1-STORY1")
        assert b1.pj[0] == pytest.approx(6.0, rel=1e-9)

    def test_unknown_units_warn_and_fall_back(self):
        model, w = import_e2k(e2k_text(units=("lbf", "in")))
        assert any("unsupported unit system" in x for x in w)
        assert model.materials["CONC"].E == pytest.approx(25e6)


# --------------------------------------------------------------------------- #
# robustness: unknown content -> warnings, never exceptions
# --------------------------------------------------------------------------- #
class TestWarnings:
    def test_unknown_shape_gets_default_rectangle(self):
        extra = ['$ FRAME SECTIONS',
                 '  FRAMESECTION "W18"  MATERIAL "CONC"  SHAPE "I/Wide '
                 'Flange"  D 0.45 B 0.19',
                 '$ LINE CONNECTIVITIES',
                 '  LINE "B99"  BEAM "1" "2"  0',
                 '$ LINE ASSIGNS',
                 '  LINEASSIGN "B99" "STORY1"  SECTION "W18"']
        model, w = import_e2k(e2k_text(extra=extra))
        assert any("not supported" in x and "W18" in x for x in w)
        sec = model.sections["W18"]
        assert (sec.b, sec.h) == (0.3, 0.6)          # documented default
        assert any(m.uid == "B99-STORY1" for m in model.members)

    def test_unknown_lines_collected_never_raise(self):
        extra = ['$ POINT ASSIGNS',
                 '  POINTASSIGN "1" "STORY1" DIAPH "D1"',
                 '  POINTASSIGN "2" "STORY1" DIAPH "D1"',
                 '$ STORIES',
                 '  MYSTERY "X" 1 2 3']
        model, w = import_e2k(e2k_text(extra=extra))
        # unknown SECTION -> one aggregated warning with a line count
        assert any("unparsed section $ POINT ASSIGNS" in x
                   and "2 line(s)" in x for x in w)
        # unknown line inside a KNOWN section -> per-line warning
        assert any("unrecognised line in $ STORIES" in x for x in w)
        assert len(model.members) == 42              # import unaffected

    def test_duplicate_assign_ignored_with_warning(self):
        extra = ['$ LINE ASSIGNS',
                 '  LINEASSIGN "B1" "STORY1"  SECTION "BM3060"']
        model, w = import_e2k(e2k_text(extra=extra))
        assert any("duplicate line assign" in x for x in w)
        assert len(model.members) == 42

    def test_assign_to_unknown_story_or_line_skipped(self):
        extra = ['$ LINE ASSIGNS',
                 '  LINEASSIGN "B1" "ROOF99"  SECTION "BM3060"',
                 '  LINEASSIGN "NOLINE" "STORY1"  SECTION "BM3060"']
        model, w = import_e2k(e2k_text(extra=extra))
        assert any("unknown story" in x for x in w)
        assert any("unknown line" in x for x in w)
        assert len(model.members) == 42

    def test_assign_with_unknown_section_creates_default(self):
        extra = ['$ LINE CONNECTIVITIES',
                 '  LINE "B77"  BEAM "1" "3"  0',
                 '$ LINE ASSIGNS',
                 '  LINEASSIGN "B77" "STORY1"  SECTION "GHOST"']
        model, w = import_e2k(e2k_text(extra=extra))
        assert any("unknown frame section" in x and "GHOST" in x for x in w)
        assert "GHOST" in model.sections
        assert (model.sections["GHOST"].b,
                model.sections["GHOST"].h) == (0.3, 0.6)

    def test_undefined_material_gets_default_e(self):
        extra = ['$ FRAME SECTIONS',
                 '  FRAMESECTION "SX"  MATERIAL "STEEL99"  SHAPE '
                 '"Concrete Rectangular"  D 0.5 B 0.2']
        model, w = import_e2k(e2k_text(extra=extra))
        assert any("STEEL99" in x and "default E" in x for x in w)
        assert model.materials["STEEL99"].E == pytest.approx(25e6)

    def test_column_with_differing_plan_points_warns(self):
        extra = ['$ LINE CONNECTIVITIES',
                 '  LINE "CX"  COLUMN "1" "2"  1',
                 '$ LINE ASSIGNS',
                 '  LINEASSIGN "CX" "STORY1"  SECTION "COL40"']
        model, w = import_e2k(e2k_text(extra=extra))
        assert any("differ in plan" in x for x in w)
        cx = next(m for m in model.members if m.uid == "CX-STORY1")
        assert cx.pi == (0.0, 0.0, 0.0) and cx.pj == (0.0, 0.0, 3.2)

    def test_empty_text_yields_empty_model(self):
        model, w = import_e2k("")
        assert model.members == [] and model.stories == []


# --------------------------------------------------------------------------- #
# serialisation round trip
# --------------------------------------------------------------------------- #
def test_to_dict_json_serializable_and_roundtrips():
    model, _ = import_e2k(e2k_text())
    d = model.to_dict()
    blob = json.dumps(d)                    # must be JSON-serializable
    model2 = BuildingModel.from_dict(json.loads(blob))
    assert model2.to_dict() == d            # exact round trip


# --------------------------------------------------------------------------- #
# engine smoke
# --------------------------------------------------------------------------- #
def test_engine_modal_smoke():
    pytest.importorskip("openseespy.opensees")
    from skyframe.engine.opensees_engine import OpenSeesEngine

    model, _ = import_e2k(e2k_text())
    model.story_masses = {s.name: 50.0 for s in model.stories}
    model.num_modes = 3
    modal = OpenSeesEngine(model).run_modal()
    assert len(modal.periods) >= 1
    for T in modal.periods:
        assert T > 0.0 and math.isfinite(T)
