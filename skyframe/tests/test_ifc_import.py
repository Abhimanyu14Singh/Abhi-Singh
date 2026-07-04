"""Tests for the IFC/SPF import silo (skyframe.io.ifc).

All SPF fixtures are hand-written IN the tests via a tiny emitter — no
external files, no external IFC libraries.  The reader is GEOMETRY-ONLY
and honestly bounded; these tests pin the supported subset (storeys,
columns/beams with rectangular extruded solids, identity + Z-rotation
placements) and the warning behaviour for everything else.
"""

import json
import math

import pytest

from skyframe.core.model import BuildingModel
from skyframe.io.ifc import (
    STAR,
    StepEnum,
    StepRef,
    StepTyped,
    import_ifc,
    parse_step,
)


# --------------------------------------------------------------------------- #
# fixture emitter: minimal SPF writer
# --------------------------------------------------------------------------- #
class Spf:
    """Tiny ISO-10303-21 emitter: ``add`` returns the new instance id."""

    def __init__(self):
        self.stmts = []
        self.n = 0

    def add(self, body):
        self.n += 1
        self.stmts.append(f"#{self.n}={body};")
        return self.n

    def raw(self, stmt):
        self.stmts.append(stmt)

    def text(self):
        return ("ISO-10303-21;\nHEADER;\n"
                "FILE_DESCRIPTION((''),'2;1');\n"
                "FILE_NAME('t','2026-01-01',(''),(''),'','','');\n"
                "FILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n"
                + "\n".join(self.stmts)
                + "\nENDSEC;\nEND-ISO-10303-21;\n")

    # ---- geometry helpers ------------------------------------------- #
    def point(self, x, y, z):
        return self.add(f"IFCCARTESIANPOINT(({x},{y},{z}))")

    def direction(self, x, y, z):
        return self.add(f"IFCDIRECTION(({x},{y},{z}))")

    def a2p(self, x, y, z, refdir=None, axis=None):
        loc = self.point(x, y, z)
        ax = f"#{axis}" if axis else "$"
        rd = f"#{refdir}" if refdir else "$"
        return self.add(f"IFCAXIS2PLACEMENT3D(#{loc},{ax},{rd})")

    def placement(self, rel_to, a2p_id):
        rel = f"#{rel_to}" if rel_to else "$"
        return self.add(f"IFCLOCALPLACEMENT({rel},#{a2p_id})")

    def rect_solid_shape(self, xdim, ydim, depth, dir_xyz=(0.0, 0.0, 1.0)):
        """Extruded-rectangle body -> IFCPRODUCTDEFINITIONSHAPE id."""
        prof = self.add(f"IFCRECTANGLEPROFILEDEF(.AREA.,$,$,{xdim},{ydim})")
        pos = self.a2p(0.0, 0.0, 0.0)
        d = self.direction(*dir_xyz)
        solid = self.add(f"IFCEXTRUDEDAREASOLID(#{prof},#{pos},#{d},{depth})")
        rep = self.add(f"IFCSHAPEREPRESENTATION($,'Body','SweptSolid',"
                       f"(#{solid}))")
        return self.add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))")

    # ---- products ---------------------------------------------------- #
    def storey(self, name, elev, placement=None):
        pl = f"#{placement}" if placement else "$"
        return self.add(f"IFCBUILDINGSTOREY('gid{self.n}',$,'{name}',$,$,"
                        f"{pl},$,$,.ELEMENT.,{elev})")

    def element(self, etype, name, placement, pds):
        return self.add(f"{etype}('gid{self.n}',$,'{name}',$,$,"
                        f"#{placement},#{pds},$)")


def two_storey_fixture(extra=None):
    """2 storeys (0 m, 3 m) + 1 column (0.4x0.4 at (1,2)) + 1 beam
    (0.3x0.6 from (0,0,3) 6 m along +X)."""
    s = Spf()
    site = s.placement(None, s.a2p(0.0, 0.0, 0.0))
    s.storey("Level 1", 0.0, site)
    s.storey("Level 2", 3.0, site)
    col_pl = s.placement(site, s.a2p(1.0, 2.0, 0.0))
    s.element("IFCCOLUMN", "C1", col_pl, s.rect_solid_shape(0.4, 0.4, 3.0))
    beam_pl = s.placement(site, s.a2p(0.0, 0.0, 3.0))
    s.element("IFCBEAM", "B1", beam_pl,
              s.rect_solid_shape(0.3, 0.6, 6.0,
                                 dir_xyz=(1.0, 0.0, 0.0)))
    if extra is not None:
        extra(s)
    return s


# --------------------------------------------------------------------------- #
# STEP tokenizer
# --------------------------------------------------------------------------- #
class TestTokenizer:
    def test_roundtrip_nested_args_strings_refs(self):
        s = Spf()
        s.raw("#1=IFCFOO('it''s a ''test''',(1.,2.E-1,(#2,#3)),.STEEL.,"
              "$,*,IFCLENGTHMEASURE(3.5),-42,-1.E-2);")
        insts, warnings = parse_step(s.text())
        assert warnings == []
        inst = insts[1]
        assert inst.type == "IFCFOO"
        assert inst.args[0] == "it's a 'test'"          # '' escapes
        assert inst.args[1] == [1.0, 0.2, [StepRef(2), StepRef(3)]]
        assert inst.args[2] == StepEnum("STEEL")
        assert inst.args[3] is None                     # $
        assert inst.args[4] is STAR                     # *
        assert inst.args[5] == StepTyped("IFCLENGTHMEASURE", (3.5,))
        assert inst.args[6] == -42 and isinstance(inst.args[6], int)
        assert inst.args[7] == pytest.approx(-0.01)

    def test_string_with_semicolon_and_parens(self):
        s = Spf()
        s.raw("#1=IFCNOTE('a;b(c),d');")
        s.raw("#2=IFCNOTE('next');")
        insts, warnings = parse_step(s.text())
        assert insts[1].args == ["a;b(c),d"]
        assert insts[2].args == ["next"]
        assert warnings == []

    def test_empty_list_and_whitespace(self):
        insts, w = parse_step("DATA;\n#1 = IFCX ( ( ) , 5 , #7 );\nENDSEC;")
        assert insts[1].args == [[], 5, StepRef(7)]
        assert w == []

    def test_malformed_instance_warns_not_crashes(self):
        s = Spf()
        s.raw("#4=IFCCARTESIANPOINT((0.,0.,);")   # missing value after ','
        s.raw("#5=IFCCARTESIANPOINT((1.,2.,3.));")
        insts, warnings = parse_step(s.text())
        assert 4 not in insts
        assert insts[5].args == [[1.0, 2.0, 3.0]]
        assert any("malformed" in w for w in warnings)

    def test_duplicate_id_keeps_first_and_warns(self):
        insts, w = parse_step("DATA;#1=IFCA();#1=IFCB();ENDSEC;")
        assert insts[1].type == "IFCA"
        assert any("duplicate instance id" in x for x in w)

    def test_no_data_section_scans_all_with_warning(self):
        insts, w = parse_step("#1=IFCA(1);")
        assert insts[1].args == [1]
        assert any("no DATA section" in x for x in w)


# --------------------------------------------------------------------------- #
# import: the 2-storey column+beam fixture
# --------------------------------------------------------------------------- #
class TestImport:
    def test_stories_from_storeys(self):
        model, warnings = import_ifc(two_storey_fixture().text())
        # lowest storey (Level 1, elev 0) is the base level
        assert [s.name for s in model.stories] == ["Level 2"]
        assert model.stories[0].elevation == pytest.approx(3.0)
        assert model.stories[0].height == pytest.approx(3.0)
        assert any("base level" in w for w in warnings)

    def test_column_exact_coordinates_and_section(self):
        model, _ = import_ifc(two_storey_fixture().text())
        col = next(m for m in model.members if m.kind == "column")
        assert col.uid == "C1"
        assert col.pi == (1.0, 2.0, 0.0)
        assert col.pj == (1.0, 2.0, 3.0)
        sec = model.sections[col.section]
        assert (sec.b, sec.h) == (0.4, 0.4)
        assert col.story == "Level 2"       # nearest storey to top z = 3

    def test_beam_exact_coordinates_and_section(self):
        model, _ = import_ifc(two_storey_fixture().text())
        beam = next(m for m in model.members if m.kind == "beam")
        assert beam.uid == "B1"
        assert beam.pi == (0.0, 0.0, 3.0)
        assert beam.pj == (6.0, 0.0, 3.0)   # depth 6 along placement X
        sec = model.sections[beam.section]
        assert (sec.b, sec.h) == (0.3, 0.6)
        assert beam.story == "Level 2"

    def test_placeholder_material_documented(self):
        model, _ = import_ifc(two_storey_fixture().text())
        assert model.materials["IFC"].E == pytest.approx(25e6)
        for name in model.sections:
            assert model.sections[name].material == "IFC"

    def test_z_rotated_beam_runs_along_y(self):
        def extra(s):
            rd = s.direction(0.0, 1.0, 0.0)     # local X -> global +Y (90 deg)
            pl = s.placement(None, s.a2p(6.0, 0.0, 3.0, refdir=rd))
            s.element("IFCBEAM", "B2", pl, s.rect_solid_shape(0.3, 0.6, 6.0))
        model, warnings = import_ifc(two_storey_fixture(extra).text())
        b2 = next(m for m in model.members if m.uid == "B2")
        assert b2.pi == (6.0, 0.0, 3.0)
        assert b2.pj[0] == pytest.approx(6.0, abs=1e-9)   # 90 deg -> along Y
        assert b2.pj[1] == pytest.approx(6.0, abs=1e-9)
        assert b2.pj[2] == pytest.approx(3.0, abs=1e-12)
        assert not any("B2" in w or "rotation" in w for w in warnings)

    def test_nested_placement_composes_rotation_and_translation(self):
        # parent placement rotated 90 deg at (10, 0, 3); child offset
        # (2, 0, 0) IN THE PARENT FRAME -> global (10, 2, 3); child adds
        # no rotation, so the beam inherits the parent's 90 deg X axis.
        def extra(s):
            rd = s.direction(0.0, 1.0, 0.0)
            parent = s.placement(None, s.a2p(10.0, 0.0, 3.0, refdir=rd))
            child = s.placement(parent, s.a2p(2.0, 0.0, 0.0))
            s.element("IFCBEAM", "B3", child,
                      s.rect_solid_shape(0.3, 0.6, 4.0))
        model, _ = import_ifc(two_storey_fixture(extra).text())
        b3 = next(m for m in model.members if m.uid == "B3")
        assert b3.pi[0] == pytest.approx(10.0, abs=1e-9)
        assert b3.pi[1] == pytest.approx(2.0, abs=1e-9)
        assert b3.pi[2] == pytest.approx(3.0, abs=1e-12)
        assert b3.pj[0] == pytest.approx(10.0, abs=1e-9)  # still along +Y
        assert b3.pj[1] == pytest.approx(6.0, abs=1e-9)

    def test_grid_from_column_locations(self):
        model, _ = import_ifc(two_storey_fixture().text())
        assert model.grid is not None
        assert model.grid.x_lines == [1.0]
        assert model.grid.y_lines == [2.0]


# --------------------------------------------------------------------------- #
# warnings: everything outside the supported subset
# --------------------------------------------------------------------------- #
class TestWarnings:
    def test_non_z_rotation_warns_per_element(self):
        def extra(s):
            ax = s.direction(1.0, 0.0, 0.0)      # Axis tilted to +X: illegal
            pl = s.placement(None, s.a2p(0.0, 0.0, 3.0, axis=ax))
            s.element("IFCBEAM", "B9", pl, s.rect_solid_shape(0.3, 0.6, 2.0))
        model, warnings = import_ifc(two_storey_fixture(extra).text())
        assert any("not +Z" in w and "rotation ignored" in w
                   for w in warnings)
        b9 = next(m for m in model.members if m.uid == "B9")
        assert b9.pj == (2.0, 0.0, 3.0)          # treated as unrotated

    def test_out_of_plane_refdirection_warns(self):
        def extra(s):
            rd = s.direction(0.0, 0.7071, 0.7071)
            pl = s.placement(None, s.a2p(0.0, 0.0, 3.0, refdir=rd))
            s.element("IFCBEAM", "B9", pl, s.rect_solid_shape(0.3, 0.6, 2.0))
        _, warnings = import_ifc(two_storey_fixture(extra).text())
        assert any("not in the XY plane" in w for w in warnings)

    def test_unsupported_entities_counted(self):
        def extra(s):
            s.add("IFCWALLSTANDARDCASE('g1',$,'W1',$,$,$,$,$)")
            s.add("IFCWALLSTANDARDCASE('g2',$,'W2',$,$,$,$,$)")
            s.add("IFCSLAB('g3',$,'S1',$,$,$,$,$,.FLOOR.)")
        model, warnings = import_ifc(two_storey_fixture(extra).text())
        assert any("2 IFCWALLSTANDARDCASE" in w for w in warnings)
        assert any("1 IFCSLAB" in w for w in warnings)
        assert len(model.members) == 2           # walls/slabs not imported

    def test_non_rectangular_profile_skipped_with_warning(self):
        def extra(s):
            prof = s.add("IFCCIRCLEPROFILEDEF(.AREA.,$,$,0.3)")
            pos = s.a2p(0.0, 0.0, 0.0)
            d = s.direction(0.0, 0.0, 1.0)
            solid = s.add(f"IFCEXTRUDEDAREASOLID(#{prof},#{pos},#{d},3.0)")
            rep = s.add(f"IFCSHAPEREPRESENTATION($,'Body','SweptSolid',"
                        f"(#{solid}))")
            pds = s.add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))")
            pl = s.placement(None, s.a2p(3.0, 3.0, 0.0))
            s.element("IFCCOLUMN", "CX", pl, pds)
        model, warnings = import_ifc(two_storey_fixture(extra).text())
        assert any("IFCCIRCLEPROFILEDEF" in w and "skipped" in w
                   for w in warnings)
        assert not any(m.uid == "CX" for m in model.members)

    def test_element_without_extruded_solid_skipped(self):
        def extra(s):
            pds = s.add("IFCPRODUCTDEFINITIONSHAPE($,$,())")
            pl = s.placement(None, s.a2p(0.0, 0.0, 0.0))
            s.element("IFCCOLUMN", "CY", pl, pds)
        model, warnings = import_ifc(two_storey_fixture(extra).text())
        assert any("no IFCEXTRUDEDAREASOLID" in w for w in warnings)
        assert not any(m.uid == "CY" for m in model.members)

    def test_malformed_instance_does_not_break_import(self):
        def extra(s):
            s.raw("#900=IFCCARTESIANPOINT((0.,0.,);")   # malformed
        model, warnings = import_ifc(two_storey_fixture(extra).text())
        assert any("malformed" in w for w in warnings)
        assert len(model.members) == 2               # rest imported fine

    def test_prefixed_length_unit_warns(self):
        def extra(s):
            s.add("IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.)")
        _, warnings = import_ifc(two_storey_fixture(extra).text())
        assert any("prefixed length unit" in w and "MILLI" in w
                   for w in warnings)

    def test_ifcmember_imported_as_brace(self):
        def extra(s):
            pl = s.placement(None, s.a2p(0.0, 6.0, 3.0))
            s.element("IFCMEMBER", "M1", pl, s.rect_solid_shape(0.2, 0.2,
                                                                4.0))
        model, _ = import_ifc(two_storey_fixture(extra).text())
        m1 = next(m for m in model.members if m.uid == "M1")
        assert m1.kind == "brace"
        assert m1.pj == (4.0, 6.0, 3.0)

    def test_empty_text_warns_and_returns_empty_model(self):
        model, warnings = import_ifc("")
        assert model.members == [] and model.stories == []
        assert any("no DATA section" in w for w in warnings)


# --------------------------------------------------------------------------- #
# serialisation round trip
# --------------------------------------------------------------------------- #
def test_to_dict_json_serializable_and_roundtrips():
    model, _ = import_ifc(two_storey_fixture().text())
    d = model.to_dict()
    blob = json.dumps(d)                    # must be JSON-serializable
    model2 = BuildingModel.from_dict(json.loads(blob))
    assert model2.to_dict() == d            # exact round trip


# --------------------------------------------------------------------------- #
# engine smoke
# --------------------------------------------------------------------------- #
def box_frame_fixture():
    """2 storeys + 4 columns + 4 perimeter beams (one bay box at z = 3)."""
    s = Spf()
    site = s.placement(None, s.a2p(0.0, 0.0, 0.0))
    s.storey("Level 1", 0.0, site)
    s.storey("Level 2", 3.0, site)
    for (x, y) in [(0.0, 0.0), (6.0, 0.0), (6.0, 6.0), (0.0, 6.0)]:
        pl = s.placement(site, s.a2p(x, y, 0.0))
        s.element("IFCCOLUMN", f"C{x:g}-{y:g}", pl,
                  s.rect_solid_shape(0.4, 0.4, 3.0))
    # beams around the perimeter, oriented via Z-rotations
    for i, ((x, y), rd) in enumerate([
            ((0.0, 0.0), (1.0, 0.0, 0.0)),
            ((6.0, 0.0), (0.0, 1.0, 0.0)),
            ((6.0, 6.0), (-1.0, 0.0, 0.0)),
            ((0.0, 6.0), (0.0, -1.0, 0.0))]):
        d = s.direction(*rd)
        pl = s.placement(site, s.a2p(x, y, 3.0, refdir=d))
        s.element("IFCBEAM", f"B{i + 1}", pl,
                  s.rect_solid_shape(0.3, 0.6, 6.0))
    return s


def test_engine_modal_smoke():
    pytest.importorskip("openseespy.opensees")
    from skyframe.engine.opensees_engine import OpenSeesEngine

    model, warnings = import_ifc(box_frame_fixture().text())
    assert len(model.members) == 8
    model.story_masses = {s.name: 20.0 for s in model.stories}
    model.num_modes = 3
    modal = OpenSeesEngine(model).run_modal()
    assert len(modal.periods) >= 1
    for T in modal.periods:
        assert T > 0.0 and math.isfinite(T)
