"""OAPI-compatible Python facade over SkyFrame (``skyframe.oapi``).

Ports ETABS / SAP2000 OAPI Python scripts with few edits: the object tree
(``SapModel.File``, ``PropMaterial``, ``PropFrame``, ``PropArea``,
``PointObj``, ``FrameObj``, ``AreaObj``, ``Story``, ``LoadPatterns``,
``LoadCases``, ``RespCombo``, ``Analyze``, ``Results`` ...), the method
names and argument order, the integer return codes and the comtypes
``[byref..., ret]`` return lists are the CSI ones.

Two ways in::

    # 1. replace only the import line of an existing script
    from skyframe.oapi import comtypes          # was: import comtypes.client
    helper = comtypes.client.CreateObject('ETABSv1.Helper')
    helper = helper.QueryInterface(comtypes.gen.ETABSv1.cHelper)
    EtabsObject = helper.CreateObjectProgID("CSI.ETABS.API.ETABSObject")
    EtabsObject.ApplicationStart()
    SapModel = EtabsObject.SapModel

    # 2. direct
    from skyframe.oapi import SapModel as new_sap_model
    SapModel = new_sap_model()

The analysis is the SkyFrame engine (``Analyze.RunAnalysis``), so results
equal the REST API's ``POST /api/analyze`` on ``SapModel.to_skyframe()``.
See ``docs/API.md`` for the supported methods and the deviations.
"""

from __future__ import annotations

from .core import VERSION, cSapModel
from .units import EUNITS, EUNIT_BY_NAME

__all__ = ["SapModel", "cSapModel", "cOAPI", "cHelper", "EUNITS",
           "EUNIT_BY_NAME", "VERSION", "comtypes"]


def SapModel(units: int = 6) -> cSapModel:
    """A fresh ``SapModel`` (present units kN-m-C unless ``units``)."""
    sm = cSapModel(units)
    sm._reset()
    return sm


class cOAPI:
    """The ``ETABSObject`` / ``SapObject`` application object."""

    def __init__(self, program: str = "ETABS"):
        self.program = program
        self.SapModel = SapModel()
        self._started = False

    def ApplicationStart(self, Units=None, Visible=True, FileName=""):
        self._started = True
        if Units is not None:
            self.SapModel.InitializeNewModel(Units)
        if FileName:
            return self.SapModel.File.OpenFile(FileName)
        return 0

    def ApplicationExit(self, FileSave=False):
        if FileSave and self.SapModel._st.get("filename"):
            self.SapModel.File.Save()
        self._started = False
        return 0

    def Hide(self):
        return 0

    def Unhide(self):
        return 0

    def Visible(self):
        return False

    def GetOAPIVersionNumber(self):
        return 1.0


class cHelper:
    """``ETABSv1.Helper`` / ``SAP2000v1.Helper``."""

    def __init__(self, program: str = "ETABS"):
        self.program = program

    def QueryInterface(self, interface=None):
        return self

    def CreateObjectProgID(self, progID: str = ""):
        return cOAPI("SAP2000" if "SAP2000" in str(progID).upper()
                     else self.program)

    def CreateObject(self, fullPath: str = ""):
        return self.CreateObjectProgID(fullPath)

    def GetObject(self, typeName: str = ""):
        return self.CreateObjectProgID(typeName)

    GetObjectProcess = GetObject


from . import comtypes  # noqa: E402  (shim needs cHelper)
