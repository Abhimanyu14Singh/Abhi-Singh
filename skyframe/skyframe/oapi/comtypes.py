"""Drop-in stand-in for the ``comtypes`` names an ETABS / SAP2000 script
touches, so a script ports by changing ONE line::

    import comtypes.client                      # before (Windows + CSI)
    from skyframe.oapi import comtypes          # after  (SkyFrame)

``comtypes.client.CreateObject('ETABSv1.Helper')``,
``comtypes.client.GetActiveObject("CSI.ETABS.API.ETABSObject")`` and
``comtypes.gen.ETABSv1.cHelper`` / ``comtypes.gen.SAP2000v1.cHelper``
then resolve to the SkyFrame facade.
"""

from __future__ import annotations

from types import SimpleNamespace


def _helper(progid: str = ""):
    from skyframe.oapi import cHelper
    return cHelper("SAP2000" if "SAP2000" in str(progid).upper()
                   else "ETABS")


def _active(progid: str = ""):
    return _helper(progid).CreateObjectProgID(progid)


class COMError(OSError):
    """Stand-in for ``comtypes.COMError`` (never raised by the facade)."""


client = SimpleNamespace(CreateObject=_helper, GetActiveObject=_active,
                         GetModule=lambda *a, **k: None)
gen = SimpleNamespace(ETABSv1=SimpleNamespace(cHelper="ETABSv1.cHelper"),
                      SAP2000v1=SimpleNamespace(cHelper="SAP2000v1.cHelper"),
                      CSiBridge1=SimpleNamespace(
                          cHelper="CSiBridge1.cHelper"))
