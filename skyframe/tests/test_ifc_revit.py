"""Revit-friendly IFC export (``export_ifc(..., revit=True)``) -- CONTRACT
"SAP2000 / SAFE / steel interop" (Revit link: IFC path)."""

import re

from skyframe.core import frame_axes as _fa
from skyframe.core.builder import quick_building
from skyframe.core.model import Material, ShellSection
from skyframe.core.steel_db import section_from_db
from skyframe.io.ifc_read import read_ifc
from skyframe.io.ifc_revit import revit_material_name, revit_profile_name
from skyframe.io.ifc_write import export_ifc


def _model():
    m = _fa.as_new_model(quick_building(stories=2))
    m.add_material(Material("A992", 2.0e8, 0.3, 77.0, material_type="steel",
                            fy=344.738e3, fu=448e3))
    m.add_section(section_from_db("aisc", "W14X90", "A992", "W14X90"))
    m.members[0].section = "W14X90"
    m.add_shell_section(ShellSection("SLAB", "CONC", 0.2))
    m.add_shell("slab", "shell", "SLAB", [(0, 0, 3.2), (6, 0, 3.2),
                                          (6, 6, 3.2), (0, 6, 3.2)],
                story="Story1")
    return m


def test_default_export_unchanged():
    m = _model()
    assert export_ifc(m)[0] == export_ifc(m, revit=False)[0]
    assert "IFCCOLUMNTYPE" not in export_ifc(m)[0]


def test_revit_entities():
    m = _model()
    text, rep = export_ifc(m, revit=True)
    assert "FILE_DESCRIPTION(('ViewDefinition [ReferenceView_V1.2, " \
        "StructuralAnalysisView]')" in text
    assert text.count("IFCGEOMETRICREPRESENTATIONSUBCONTEXT") == 2
    assert "IFCSTRUCTURALANALYSISMODEL" in text
    types = re.findall(r"IFC(COLUMN|BEAM|MEMBER)TYPE\('[^']*',\$,'([^']*)'",
                       text)
    assert ("COLUMN", "W14X90") in types and ("COLUMN", "500 x 500mm") in \
        types and ("BEAM", "300 x 600mm") in types
    assert rep["written"]["revit_types"] == len(types)
    # one Pset_*Common per physical element (frames + slab)
    assert rep["written"]["revit_psets"] == len(m.members) + 1
    assert text.count("'Pset_ColumnCommon'") + text.count(
        "'Pset_BeamCommon'") == len(m.members)
    # levels: storeys placed at their elevation, with a long name
    assert re.search(r"IFCBUILDINGSTOREY\('[^']*',\$,'Story2',\$,\$,#\d+,"
                     r"\$,'Story2',\.ELEMENT\.,6\.4\);", text)
    assert "IFCCARTESIANPOINT((0.0,0.0,6.4))" in text
    assert "IFCMATERIAL('Steel ASTM A992',$,'Steel')" in text
    assert "IFCMATERIAL('Concrete - CONC',$,'Concrete')" in text


def test_revit_file_reimports_exactly():
    m = _model()
    m2, warns = read_ifc(export_ifc(m, revit=True)[0])
    assert warns == []
    assert [(x.uid, x.pi, x.pj) for x in m2.members] == \
        [(x.uid, x.pi, x.pj) for x in m.members]
    assert m2.sections["W14X90"].db_ref["name"] == "W14X90"
    assert [s.name for s in m2.stories] == ["Story1", "Story2"]


def test_names():
    m = _model()
    assert revit_profile_name(m.sections["COL"]) == "500 x 500mm"
    assert revit_profile_name(m.sections["W14X90"]) == "W14X90"
    assert revit_material_name(m.materials["A992"]) == "Steel ASTM A992"
    a36 = Material("A36", 2e8, 0.3, 77.0, material_type="steel", fy=248.2e3)
    assert revit_material_name(a36) == "Steel ASTM A36"
    c30 = Material("C30", 3e7, 0.2, 24.0, fc=30e3)
    assert revit_material_name(c30) == "Concrete, Cast-in-Place - 30 MPa (C30)"
