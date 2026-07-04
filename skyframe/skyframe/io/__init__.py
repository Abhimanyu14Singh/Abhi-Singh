"""SkyFrame I/O: import building geometry from external file formats.

* ASCII DXF import (:mod:`skyframe.io.dxf`) — the common ETABS onboarding
  path where an architect's CAD plan (grids, columns, beams, walls) is
  turned into a multi-story analytical model.
* ETABS ``.e2k`` import (:mod:`skyframe.io.e2k`) — a subset of the classic
  ``$``-section text export (stories, points, line connectivities and
  assigns, frame sections, materials, load patterns, units).
* IFC / SPF import (:mod:`skyframe.io.ifc`) — an exploratory GEOMETRY-ONLY
  reader for ISO-10303-21 files (storeys, columns/beams/members with
  rectangular extruded sections).
"""

from skyframe.io.dxf import DxfDrawing, DxfEntity, import_dxf, parse_dxf
from skyframe.io.e2k import import_e2k
from skyframe.io.ifc import (
    STAR,
    StepEnum,
    StepInstance,
    StepRef,
    StepTyped,
    import_ifc,
    parse_step,
)

__all__ = [
    "DxfDrawing", "DxfEntity", "parse_dxf", "import_dxf",
    "import_e2k",
    "import_ifc", "parse_step", "StepInstance", "StepRef", "StepEnum",
    "StepTyped", "STAR",
]
