"""DWG (binary AutoCAD drawing) detection for File > Import > DXF.

SkyFrame imports drawings through its DXF reader
(:func:`skyframe.io.dxf_layers.import_dxf_layers`).  A DWG -> DXF
converter is NOT bundled: LibreDWG (``dwg2dxf``) is not packaged for the
build platform (Ubuntu 24.04 has no ``libredwg-tools``) and no pip
package converts DWG without a proprietary runtime (ezdxf's ``odafc``
add-on needs the ODA File Converter, ``pydwg`` drives a running AutoCAD
over COM).  See docs/INTEROP.md "DWG drawings".

So a DWG file handed to the DXF importer is recognised by its version
signature (``AC1012`` ... ``AC1032`` in the first six bytes) and rejected
with :data:`DWG_HELP`, which tells the user how to save the drawing as
DXF from their CAD program.  CONTRACT "E2K native nonlinear records
(Wave C)" > "DWG".
"""

from __future__ import annotations

__all__ = ["DWG_HELP", "is_dwg", "check_not_dwg"]

DWG_HELP = (
    "This is a binary DWG drawing. SkyFrame imports drawings as DXF and "
    "does not bundle a DWG converter. Save the drawing as DXF from your "
    "CAD program (AutoCAD / BricsCAD / ZWCAD: SAVEAS -> 'AutoCAD DXF "
    "(*.dxf)', any version from R12 on; DraftSight / LibreCAD / FreeCAD: "
    "File > Save As / Export -> DXF), or convert it with the free ODA "
    "File Converter or LibreDWG (dwg2dxf), then use File > Import > DXF "
    "with the .dxf file.")


def is_dwg(head) -> bool:
    """True when ``head`` (text or bytes: the start of a file) carries a
    DWG version signature ``AC10xx`` (R13 .. 2018+) and is not DXF."""
    if isinstance(head, (bytes, bytearray)):
        head = bytes(head[:6]).decode("latin-1", "replace")
    s = str(head or "")[:6]
    return len(s) == 6 and s.startswith("AC10") and s[4:].isdigit()


def check_not_dwg(text) -> None:
    """Raise ``ValueError(DWG_HELP)`` for DWG content."""
    if is_dwg(text):
        raise ValueError(DWG_HELP)
