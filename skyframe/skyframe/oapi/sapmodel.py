"""The ``SapModel`` object tree of the OAPI-compatible facade.

Mirrors the CSI OAPI (ETABS / SAP2000) as seen from Python through
``comtypes``:

* every method returns ``0`` on success and a non-zero code on failure;
* a method whose CSI signature has ``ByRef`` arguments returns a list
  ``[byref_1, ..., byref_n, ret]`` (``ret`` LAST, the modern comtypes
  order), so ``[Name, ret] = SapModel.FrameObj.AddByCoord(...)`` works;
* values are in the PRESENT units (``SapModel.SetPresentUnits``) and are
  stored in SkyFrame SI.

Objects are kept in a small registry (points, frames, areas, loads ...)
and turned into a :class:`skyframe.core.model.BuildingModel` by
:meth:`cSapModel.to_skyframe` when the analysis runs, so the numbers are
exactly those of the SkyFrame engine / REST API for the same model.
See ``docs/API.md`` for the supported surface and the deviations.
"""

from __future__ import annotations

import json
import math
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .units import PresentUnits

FAIL = 1
_TOL = 1e-6                       # m: coincident-point merge tolerance

# eMatType -> (SkyFrame material_type, SI defaults: E kPa, nu, alpha /C,
#              unit weight kN/m3, fy, fu, fc  [kPa])  (CSI US defaults)
_PCF = 4.4482216152605e-3 / 0.3048 ** 3            # kN/m3 per lb/ft3
_KSI = 4.4482216152605 / 0.0254 ** 2               # kPa per ksi
MAT_TYPES = {
    1: ("steel", 29000 * _KSI, 0.3, 6.5e-6 * 1.8, 490 * _PCF, 50 * _KSI,
        65 * _KSI, None),
    2: ("concrete", 3604.9965 * _KSI, 0.2, 5.5e-6 * 1.8, 150 * _PCF, None,
        None, 4 * _KSI),
    3: ("other", 29000 * _KSI, 0.3, 6.5e-6 * 1.8, 490 * _PCF, None, None,
        None),
    4: ("aluminum", 10000 * _KSI, 0.33, 1.3e-5 * 1.8, 169 * _PCF, None,
        None, None),
    5: ("coldformed", 29500 * _KSI, 0.3, 6.5e-6 * 1.8, 490 * _PCF,
        50 * _KSI, 65 * _KSI, None),
    6: ("rebar", 29000 * _KSI, 0.3, 6.5e-6 * 1.8, 490 * _PCF, 60 * _KSI,
        90 * _KSI, None),
    7: ("tendon", 28500 * _KSI, 0.3, 6.5e-6 * 1.8, 490 * _PCF, 243 * _KSI,
        270 * _KSI, None),
    8: ("masonry", 1500 * _KSI, 0.2, 5.5e-6 * 1.8, 125 * _PCF, None, None,
        None),
}
_MAT_CODE = {v[0]: k for k, v in MAT_TYPES.items()}

# eLoadPatternType -> SkyFrame pattern kind (everything else -> "other")
PATTERN_KINDS = {1: "dead", 2: "superdead", 3: "live", 4: "reducible_live",
                 5: "quake", 6: "wind", 7: "snow", 8: "other", 9: "move",
                 10: "temperature", 11: "roof_live", 13: "live",
                 18: "ice", 27: "settlement", 28: "shrinkage", 29: "creep",
                 39: "construction"}
_PATTERN_CODE = {"dead": 1, "superdead": 2, "live": 3, "reducible_live": 4,
                 "quake": 5, "wind": 6, "snow": 7, "other": 8, "move": 9,
                 "temperature": 10, "roof_live": 11, "ice": 18,
                 "settlement": 27, "shrinkage": 28, "creep": 29,
                 "construction": 39}

# eComboType -> LoadCombo.combo_type
COMBO_TYPES = {0: "add", 1: "envelope", 2: "abs", 3: "srss", 4: "range"}

# SAP2000 SetShell_1 ShellType / ETABS eShellType -> ShellSection.shell_type
_SAP_SHELL = {1: "shell_thin", 2: "shell_thick", 3: "plate_thin",
              4: "plate_thick", 5: "membrane"}
_ETABS_SHELL = {1: "shell_thin", 2: "shell_thick", 3: "membrane"}

_GLOBAL_DIR = {4: "global_x", 5: "global_y", 6: "global_z",
               7: "global_x", 8: "global_y", 9: "global_z", 10: "gravity",
               11: "gravity"}
_AREA_DIR = {1: "local_1", 2: "local_2", 3: "local_3", 4: "global_x",
             5: "global_y", 6: "global_z", 10: "gravity", 11: "gravity"}


def _r(*outs, ret: int = 0):
    """comtypes-style return: plain ``ret`` or ``[*byrefs, ret]``."""
    return [*outs, ret] if outs else ret


def _xyz(p) -> Tuple[float, float, float]:
    return (float(p[0]), float(p[1]), float(p[2]))


class _Part:
    """A sub-object (``SapModel.FrameObj`` ...) bound to its model."""

    def __init__(self, sm: "cSapModel"):
        self._sm = sm

    @property
    def _u(self) -> PresentUnits:
        return self._sm._units

    def _touch(self) -> None:
        self._sm._invalidate()


# =========================================================================== #
# File
# =========================================================================== #
class cFile(_Part):
    def NewBlank(self):
        self._sm._reset(keep_units=True)
        return 0

    def NewGridOnly(self, NumberStorys, TypicalStoryHeight,
                    BottomStoryHeight, NumberLines_X=0, NumberLines_Y=0,
                    SpacingX=0.0, SpacingY=0.0):
        """ETABS: blank model with ``NumberStorys`` stories (and a grid)."""
        self._sm._reset(keep_units=True)
        n = int(NumberStorys)
        if n < 1:
            return FAIL
        u = self._u
        names = [f"Story{i + 1}" for i in range(n)]
        heights = [u.si(BottomStoryHeight, "length")] + \
            [u.si(TypicalStoryHeight, "length")] * (n - 1)
        st = self._sm._st
        st["stories"] = []
        z = 0.0
        for nm, h in zip(names, heights):
            z += h
            st["stories"].append([nm, z, h])
        st["base_elev"] = 0.0
        st["grid"] = (int(NumberLines_X), int(NumberLines_Y),
                      u.si(SpacingX, "length"), u.si(SpacingY, "length"))
        return 0

    New2DFrame = None   # not provided (SAP2000 templates)

    def Save(self, FileName=""):
        """Writes the SkyFrame model JSON (``.skyframe.json`` content,
        whatever the extension) plus the facade registry."""
        path = FileName or self._sm._st.get("filename") or ""
        if not path:
            return FAIL
        try:
            d = self._sm.to_skyframe().to_dict()
            d["_oapi"] = self._sm._registry_dict()
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(d, fh)
        except (OSError, ValueError, TypeError, KeyError):
            return FAIL
        self._sm._st["filename"] = path
        return 0

    def OpenFile(self, FileName):
        """Opens a file written by :meth:`Save` (or any SkyFrame model
        JSON: frames, areas, restraints and loads are re-registered)."""
        try:
            with open(FileName, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return FAIL
        try:
            self._sm._load(d)
        except (ValueError, KeyError, TypeError):
            return FAIL
        self._sm._st["filename"] = FileName
        return 0

    def GetFilePath(self, FilePath=""):
        import os
        fn = self._sm._st.get("filename") or ""
        return _r(os.path.dirname(fn))


# =========================================================================== #
# Properties
# =========================================================================== #
class cPropMaterial(_Part):
    def SetMaterial(self, Name, MatType, Color=-1, Notes="", GUID=""):
        from skyframe.core.model import Material
        t = MAT_TYPES.get(int(MatType))
        if t is None or not Name:
            return FAIL
        typ, E, nu, alpha, w, fy, fu, fc = t
        self._sm._st["materials"][Name] = Material(
            Name, E, nu, w, material_type=typ, alpha=alpha, fy=fy, fu=fu,
            fc=fc, notes=str(Notes or ""))
        self._touch()
        return 0

    def AddMaterial(self, Name="", MatType=2, Region="", Standard="",
                    Grade="", UserName=""):
        nm = UserName or Name or Grade or f"Mat{len(self._sm._st['materials']) + 1}"
        ret = self.SetMaterial(nm, MatType)
        return _r(nm, ret=ret)

    def SetMPIsotropic(self, Name, E, U, A, Temp=0.0):
        m = self._sm._st["materials"].get(Name)
        if m is None:
            return FAIL
        u = self._u
        m.E = u.si(E, "stress")
        m.nu = float(U)
        m.alpha = float(A) * u.factor("alpha")
        self._touch()
        return 0

    def GetMPIsotropic(self, Name, E=0.0, U=0.0, A=0.0, G=0.0, Temp=0.0):
        m = self._sm._st["materials"].get(Name)
        if m is None:
            return _r(0.0, 0.0, 0.0, 0.0, ret=FAIL)
        u = self._u
        return _r(u.out(m.E, "stress"), m.nu,
                  (m.alpha or 0.0) / u.factor("alpha"),
                  u.out(m.G, "stress"))

    def SetWeightAndMass(self, Name, MyOption, Value, Temp=0.0):
        """MyOption 1 = weight per volume, 2 = mass per volume."""
        m = self._sm._st["materials"].get(Name)
        if m is None:
            return FAIL
        from skyframe.core.model import G_ACCEL
        if int(MyOption) == 1:
            m.unit_weight = self._u.si(Value, "unit_weight")
            m.mass_density = None
        else:
            m.mass_density = self._u.si(Value, "mass_density")
            m.unit_weight = m.mass_density * G_ACCEL
        self._touch()
        return 0

    def GetWeightAndMass(self, Name, W=0.0, M=0.0, Temp=0.0):
        m = self._sm._st["materials"].get(Name)
        if m is None:
            return _r(0.0, 0.0, ret=FAIL)
        return _r(self._u.out(m.unit_weight, "unit_weight"),
                  self._u.out(m.mass_per_volume, "mass_density"))

    def SetOSteel_1(self, Name, Fy, Fu, EFy=0.0, EFu=0.0, *args, **kw):
        m = self._sm._st["materials"].get(Name)
        if m is None:
            return FAIL
        m.fy, m.fu = self._u.si(Fy, "stress"), self._u.si(Fu, "stress")
        if EFy and Fy:
            m.Ry = float(EFy) / float(Fy)
        self._touch()
        return 0

    SetOSteel = SetOSteel_1

    def SetOConcrete_1(self, Name, Fc, IsLightweight=False, FcsFactor=1.0,
                       *args, **kw):
        m = self._sm._st["materials"].get(Name)
        if m is None:
            return FAIL
        m.fc = self._u.si(Fc, "stress")
        m.lightweight = bool(IsLightweight)
        self._touch()
        return 0

    SetOConcrete = SetOConcrete_1

    def SetORebar_1(self, Name, Fy, Fu, *args, **kw):
        return self.SetOSteel_1(Name, Fy, Fu)

    def GetNameList(self, NumberNames=0, MyName=None, MatType=0):
        names = [n for n, m in self._sm._st["materials"].items()
                 if not MatType or _MAT_CODE.get(m.material_type) == MatType]
        return _r(len(names), names)

    def Count(self, MatType=0):
        return len(self.GetNameList(0, None, MatType)[1])

    def Delete(self, Name):
        if self._sm._st["materials"].pop(Name, None) is None:
            return FAIL
        self._touch()
        return 0


class cPropFrame(_Part):
    def _put(self, sec) -> int:
        self._sm._st["frame_props"][sec.name] = sec
        self._touch()
        return 0

    def _mat_ok(self, mat) -> bool:
        return mat in self._sm._st["materials"]

    def SetRectangle(self, Name, MatProp, T3, T2, Color=-1, Notes="",
                     GUID=""):
        """T3 = depth (along local 2), T2 = width."""
        from skyframe.core.model import FrameSection
        if not self._mat_ok(MatProp) or T3 <= 0 or T2 <= 0:
            return FAIL
        u = self._u
        sec = FrameSection.rectangular(Name, MatProp, u.si(T2, "length"),
                                       u.si(T3, "length"))
        sec.As2 = sec.As3 = 5.0 / 6.0 * sec.A        # CSI shear areas
        return self._put(sec)

    def GetRectangle(self, Name, FileName="", MatProp="", T3=0.0, T2=0.0,
                     Color=-1, Notes="", GUID=""):
        s = self._sm._st["frame_props"].get(Name)
        if s is None or s.shape is not None:
            return _r("", "", 0.0, 0.0, -1, "", "", ret=FAIL)
        u = self._u
        return _r("", s.material, u.out(s.h, "length"),
                  u.out(s.b, "length"), -1, "", "")

    def SetCircle(self, Name, MatProp, T3, Color=-1, Notes="", GUID=""):
        return self._dims(Name, MatProp, "round_solid", {"D": T3})

    def SetPipe(self, Name, MatProp, T3, TW, Color=-1, Notes="", GUID=""):
        return self._dims(Name, MatProp, "round_tube", {"D": T3, "t": TW})

    def SetTube(self, Name, MatProp, T3, T2, Tf, Tw, Color=-1, Notes="",
                GUID=""):
        """Rectangular tube; SkyFrame's tube has one wall thickness, so
        ``Tf != Tw`` falls back to the exact general properties."""
        if abs(float(Tf) - float(Tw)) < 1e-12:
            return self._dims(Name, MatProp, "rect_tube",
                              {"d": T3, "b": T2, "t": Tw})
        u = self._u
        d, b = u.si(T3, "length"), u.si(T2, "length")
        tf, tw = u.si(Tf, "length"), u.si(Tw, "length")
        A = b * d - (b - 2 * tw) * (d - 2 * tf)
        I33 = (b * d ** 3 - (b - 2 * tw) * (d - 2 * tf) ** 3) / 12.0
        I22 = (d * b ** 3 - (d - 2 * tf) * (b - 2 * tw) ** 3) / 12.0
        am = (b - tw) * (d - tf)
        J = 2.0 * tw * tf * (b - tw) ** 2 * (d - tf) ** 2 / (
            b * tw + d * tf - tw ** 2 - tf ** 2) if am > 0 else 0.0
        return self._general(Name, MatProp, d, b, A, 2 * d * tw,
                             2 * b * tf, J, I22, I33)

    def SetISection(self, Name, MatProp, T3, T2, Tf, Tw, T2b, Tfb,
                    Color=-1, Notes="", GUID=""):
        if abs(float(T2) - float(T2b)) < 1e-12 and \
                abs(float(Tf) - float(Tfb)) < 1e-12:
            return self._dims(Name, MatProp, "I", {"d": T3, "bf": T2,
                                                   "tf": Tf, "tw": Tw})
        return self._dims(Name, MatProp, "I_mono",
                          {"d": T3, "bf_top": T2, "tf_top": Tf,
                           "bf_bot": T2b, "tf_bot": Tfb, "tw": Tw})

    def SetChannel(self, Name, MatProp, T3, T2, Tf, Tw, Color=-1, Notes="",
                   GUID=""):
        return self._dims(Name, MatProp, "channel",
                          {"d": T3, "bf": T2, "tf": Tf, "tw": Tw})

    def SetTee(self, Name, MatProp, T3, T2, Tf, Tw, Color=-1, Notes="",
               GUID=""):
        return self._dims(Name, MatProp, "tee",
                          {"d": T3, "bf": T2, "tf": Tf, "tw": Tw})

    def SetAngle(self, Name, MatProp, T3, T2, Tf, Tw, Color=-1, Notes="",
                 GUID=""):
        """SkyFrame angles have one leg thickness (``Tf`` is used)."""
        return self._dims(Name, MatProp, "angle",
                          {"d": T3, "b": T2, "t": Tf})

    def _dims(self, Name, MatProp, typ, dims) -> int:
        from skyframe.core.section_ext import section_from_dims
        if not self._mat_ok(MatProp):
            return FAIL
        u = self._u
        try:
            sec = section_from_dims(Name, MatProp, typ,
                                    {k: u.si(v, "length")
                                     for k, v in dims.items()})
        except (ValueError, KeyError, ZeroDivisionError):
            return FAIL
        return self._put(sec)

    def SetGeneral(self, Name, MatProp, T3, T2, Area, As2, As3, Torsion,
                   I22, I33, S22=0.0, S33=0.0, Z22=0.0, Z33=0.0, R22=0.0,
                   R33=0.0, Color=-1, Notes="", GUID=""):
        u = self._u
        return self._general(Name, MatProp, u.si(T3, "length"),
                             u.si(T2, "length"), u.si(Area, "area"),
                             u.si(As2, "area"), u.si(As3, "area"),
                             u.si(Torsion, "inertia"), u.si(I22, "inertia"),
                             u.si(I33, "inertia"))

    def _general(self, Name, MatProp, d, b, A, As2, As3, J, I22, I33) -> int:
        from skyframe.core.model import FrameSection
        if not self._mat_ok(MatProp) or A <= 0 or I22 <= 0 or I33 <= 0 \
                or J <= 0:
            return FAIL
        sec = FrameSection(Name, MatProp, A, I33, I22, J, b=b, h=d)
        sec.As2 = As2 if As2 > 0 else None
        sec.As3 = As3 if As3 > 0 else None
        return self._put(sec)

    def ImportProp(self, Name, MatProp, FileName, PropName, Color=-1,
                   Notes="", GUID=""):
        """Database shape: ``FileName`` AISC16.xml / Euro.xml /
        BSShapes2006.xml / Australia-NZ.xml / Indian.xml (any path)."""
        import os
        from skyframe.io.e2k_write import STEEL_DB_FILES
        if not self._mat_ok(MatProp):
            return FAIL
        base = os.path.basename(str(FileName).replace("\\", "/")).lower()
        std = next((k for k, v in STEEL_DB_FILES.items()
                    if v.lower() == base), None)
        stds = [std] if std else list(STEEL_DB_FILES)
        from skyframe.core.steel_db import section_from_db
        for s in stds:
            try:
                return self._put(section_from_db(s, PropName, MatProp, Name))
            except (KeyError, ValueError):
                continue
        return FAIL

    def SetModifiers(self, Name, Value):
        """[A, As2, As3, J, I22, I33, Mass, Weight]; a zero shear-area
        modifier removes the shear deformation (CSI rule)."""
        s = self._sm._st["frame_props"].get(Name)
        if s is None or len(Value) < 8:
            return _r(Value, ret=FAIL)
        v = [float(x) for x in Value]
        if any(x < 0 for x in v) or v[0] <= 0 or min(v[3:6]) <= 0:
            return _r(Value, ret=FAIL)
        s.mod_A, s.mod_J, s.mod_I22, s.mod_I33 = v[0], v[3], v[4], v[5]
        for key, mod in (("2", v[1]), ("3", v[2])):
            if mod == 0.0:
                setattr(s, "As" + key, None)
                setattr(s, "mod_As" + key, 1.0)
            else:
                setattr(s, "mod_As" + key, mod)
        s.mod_mass = v[6] if v[6] > 0 else 1.0
        s.mod_weight = v[7] if v[7] > 0 else 1.0
        self._touch()
        return _r(list(Value))

    def GetModifiers(self, Name, Value=None):
        s = self._sm._st["frame_props"].get(Name)
        if s is None:
            return _r([], ret=FAIL)
        return _r([s.mod_A, s.mod_As2, s.mod_As3, s.mod_J, s.mod_I22,
                   s.mod_I33, s.mod_mass, s.mod_weight])

    def GetSectProps(self, Name, Area=0.0, As2=0.0, As3=0.0, Torsion=0.0,
                     I22=0.0, I33=0.0, S22=0.0, S33=0.0, Z22=0.0, Z33=0.0,
                     R22=0.0, R33=0.0):
        s = self._sm._st["frame_props"].get(Name)
        if s is None:
            return _r(*([0.0] * 12), ret=FAIL)
        u = self._u
        A, I22v, I33v = s.A, s.I22, s.I33
        S22 = I22v / (s.b / 2) if s.b > 0 else 0.0
        S33 = I33v / (s.h / 2) if s.h > 0 else 0.0
        return _r(u.out(A, "area"), u.out(s.As2 or 0.0, "area"),
                  u.out(s.As3 or 0.0, "area"), u.out(s.J, "inertia"),
                  u.out(I22v, "inertia"), u.out(I33v, "inertia"),
                  u.out(S22, "volume"), u.out(S33, "volume"), 0.0, 0.0,
                  u.out(math.sqrt(I22v / A), "length"),
                  u.out(math.sqrt(I33v / A), "length"))

    def GetNameList(self, NumberNames=0, MyName=None, PropType=0):
        names = list(self._sm._st["frame_props"])
        return _r(len(names), names)

    def Count(self, PropType=0):
        return len(self._sm._st["frame_props"])

    def Delete(self, Name):
        if self._sm._st["frame_props"].pop(Name, None) is None:
            return FAIL
        self._touch()
        return 0


class cPropArea(_Part):
    def _put(self, Name, MatProp, Thickness, shell_type, kind_hint):
        from skyframe.core.model import ShellSection
        if MatProp not in self._sm._st["materials"] or Thickness <= 0:
            return FAIL
        ss = ShellSection(Name, MatProp, self._u.si(Thickness, "length"))
        ss.shell_type = shell_type
        self._sm._st["area_props"][Name] = {"section": ss,
                                            "kind": kind_hint}
        self._touch()
        return 0

    def SetShell_1(self, Name, ShellType, IncludeDrillingDOF, MatProp,
                   MatAng, Thickness, Bending=None, Color=-1, Notes="",
                   GUID=""):
        """SAP2000: 1 shell-thin, 2 shell-thick, 3 plate-thin, 4
        plate-thick, 5 membrane (6 layered is not supported)."""
        st = _SAP_SHELL.get(int(ShellType))
        if st is None:
            return FAIL
        return self._put(Name, MatProp, Thickness, st, "")

    SetShell = SetShell_1

    def SetWall(self, Name, WallPropType, ShellType, MatProp, Thickness,
                Color=-1, Notes="", GUID=""):
        st = _ETABS_SHELL.get(int(ShellType))
        if st is None:
            return FAIL
        return self._put(Name, MatProp, Thickness, st, "wall")

    def SetSlab(self, Name, SlabType, ShellType, MatProp, Thickness,
                Color=-1, Notes="", GUID=""):
        st = _ETABS_SHELL.get(int(ShellType))
        if st is None:
            return FAIL
        return self._put(Name, MatProp, Thickness, st, "slab")

    def SetModifiers(self, Name, Value):
        """[f11, f22, f12, m11, m22, m12, v13, v23, Mass, Weight]."""
        p = self._sm._st["area_props"].get(Name)
        if p is None or len(Value) < 10 or any(float(v) <= 0
                                               for v in Value[:10]):
            return _r(Value, ret=FAIL)
        ss = p["section"]
        for k, v in zip(("f11", "f22", "f12", "m11", "m22", "m12", "v13",
                         "v23", "mass", "weight"), Value):
            setattr(ss, k, float(v))
        self._touch()
        return _r(list(Value))

    def GetNameList(self, NumberNames=0, MyName=None, PropType=0):
        names = list(self._sm._st["area_props"])
        return _r(len(names), names)

    def Count(self, PropType=0):
        return len(self._sm._st["area_props"])


# =========================================================================== #
# Objects
# =========================================================================== #
class cPointObj(_Part):
    def AddCartesian(self, x, y, z, Name="", UserName="", CSys="Global",
                     MergeOff=False, MergeNumber=0):
        u = self._u
        p = (u.si(x, "length"), u.si(y, "length"), u.si(z, "length"))
        nm = self._sm._point(p, UserName or "", merge=not MergeOff)
        if nm is None:
            return _r("", ret=FAIL)
        return _r(nm)

    def GetCoordCartesian(self, Name, x=0.0, y=0.0, z=0.0, CSys="Global"):
        p = self._sm._st["points"].get(Name)
        if p is None:
            return _r(0.0, 0.0, 0.0, ret=FAIL)
        u = self._u
        return _r(*(u.out(c, "length") for c in p))

    def _names(self, Name, ItemType) -> Optional[List[str]]:
        return self._sm._targets("points", Name, ItemType)

    def SetRestraint(self, Name, Value, ItemType=0):
        names = self._names(Name, ItemType)
        if names is None or len(Value) < 6:
            return _r(Value, ret=FAIL)
        for n in names:
            v = [bool(x) for x in Value[:6]]
            if any(v):
                self._sm._st["restraints"][n] = v
            else:
                self._sm._st["restraints"].pop(n, None)
        self._touch()
        return _r(list(Value))

    def GetRestraint(self, Name, Value=None):
        if Name not in self._sm._st["points"]:
            return _r([], ret=FAIL)
        return _r(list(self._sm._st["restraints"].get(Name,
                                                      [False] * 6)))

    def DeleteRestraint(self, Name, ItemType=0):
        names = self._names(Name, ItemType)
        if names is None:
            return FAIL
        for n in names:
            self._sm._st["restraints"].pop(n, None)
        self._touch()
        return 0

    def SetSpring(self, Name, k, ItemType=0, IsLocalCSys=False,
                  Replace=False):
        names = self._names(Name, ItemType)
        if names is None or len(k) < 6:
            return _r(k, ret=FAIL)
        u = self._u
        vals = [u.si(v, "stiffness") for v in k[:3]] + \
            [u.si(v, "rot_stiffness") for v in k[3:6]]
        if any(v < 0 for v in vals):
            return _r(k, ret=FAIL)
        for n in names:
            old = self._sm._st["springs"].get(n)
            if old is not None and not Replace:
                vals = [a + b for a, b in zip(old, vals)]
            self._sm._st["springs"][n] = vals
        self._touch()
        return _r(list(k))

    def SetMass(self, Name, m, ItemType=0, IsLocalCSys=False,
                Replace=False):
        names = self._names(Name, ItemType)
        if names is None or len(m) < 6:
            return _r(m, ret=FAIL)
        u = self._u
        vals = [u.si(v, "mass") for v in m[:3]] + \
            [u.si(v, "mass") * u.factor("area") for v in m[3:6]]
        for n in names:
            old = self._sm._st["masses"].get(n)
            if old is not None and not Replace:
                vals = [a + b for a, b in zip(old, vals)]
            self._sm._st["masses"][n] = vals
        self._touch()
        return _r(list(m))

    def SetLoadForce(self, Name, LoadPat, Value, Replace=False,
                     CSys="Global", ItemType=0):
        names = self._names(Name, ItemType)
        if names is None or LoadPat not in self._sm._st["patterns"] \
                or len(Value) < 6:
            return _r(Value, ret=FAIL)
        u = self._u
        vals = [u.si(v, "force") for v in Value[:3]] + \
            [u.si(v, "moment") for v in Value[3:6]]
        loads = self._sm._st["point_loads"]
        for n in names:
            key = (n, LoadPat)
            if Replace or key not in loads:
                loads[key] = list(vals)
            else:
                loads[key] = [a + b for a, b in zip(loads[key], vals)]
        self._touch()
        return _r(list(Value))

    def DeleteLoadForce(self, Name, LoadPat, ItemType=0):
        for n in self._names(Name, ItemType) or []:
            self._sm._st["point_loads"].pop((n, LoadPat), None)
        self._touch()
        return 0

    def SetDiaphragm(self, Name, DiaphragmOption, DiaphragmName="",
                     ItemType=0):
        """Option 1 disconnect, 2 from shell, 3 defined diaphragm."""
        names = self._names(Name, ItemType)
        if names is None:
            return FAIL
        for n in names:
            if int(DiaphragmOption) == 3:
                if DiaphragmName not in self._sm._st["diaphragms"]:
                    return FAIL
                self._sm._st["point_diaph"][n] = DiaphragmName
            else:
                self._sm._st["point_diaph"].pop(n, None)
        self._touch()
        return 0

    def SetGroupAssign(self, Name, GroupName, Remove=False, ItemType=0):
        return self._sm._group_assign("points", Name, GroupName, Remove,
                                      ItemType)

    def GetNameList(self, NumberNames=0, MyName=None):
        names = list(self._sm._st["points"])
        return _r(len(names), names)

    def Count(self):
        return len(self._sm._st["points"])


class cFrameObj(_Part):
    def AddByCoord(self, xi, yi, zi, xj, yj, zj, Name="", PropName="Default",
                   UserName="", CSys="Global"):
        u = self._u
        pi = (u.si(xi, "length"), u.si(yi, "length"), u.si(zi, "length"))
        pj = (u.si(xj, "length"), u.si(yj, "length"), u.si(zj, "length"))
        if math.dist(pi, pj) < _TOL:
            return _r("", ret=FAIL)
        a = self._sm._point(pi, "")
        b = self._sm._point(pj, "")
        return self._add(a, b, PropName, UserName)

    def AddByPoint(self, Point1, Point2, Name="", PropName="Default",
                   UserName=""):
        pts = self._sm._st["points"]
        if Point1 not in pts or Point2 not in pts or Point1 == Point2:
            return _r("", ret=FAIL)
        return self._add(Point1, Point2, PropName, UserName)

    def _add(self, a, b, PropName, UserName):
        st = self._sm._st
        prop = PropName
        if prop in ("Default", "", None):
            prop = next(iter(st["frame_props"]), None)
        if prop is None or prop not in st["frame_props"]:
            return _r("", ret=FAIL)
        name = UserName or self._sm._next("frames")
        if name in st["frames"]:
            return _r("", ret=FAIL)
        st["frames"][name] = {"i": a, "j": b, "prop": prop, "angle": 0.0,
                              "rel": None}
        self._touch()
        return _r(name)

    def _names(self, Name, ItemType) -> Optional[List[str]]:
        return self._sm._targets("frames", Name, ItemType)

    def GetPoints(self, Name, Point1="", Point2=""):
        f = self._sm._st["frames"].get(Name)
        if f is None:
            return _r("", "", ret=FAIL)
        return _r(f["i"], f["j"])

    def SetSection(self, Name, PropName, ItemType=0, SVarRelStartLoc=0.0,
                   SVarTotalLength=0.0):
        names = self._names(Name, ItemType)
        if names is None or PropName not in self._sm._st["frame_props"]:
            return FAIL
        for n in names:
            self._sm._st["frames"][n]["prop"] = PropName
        self._touch()
        return 0

    def GetSection(self, Name, PropName="", SAuto=""):
        f = self._sm._st["frames"].get(Name)
        if f is None:
            return _r("", "", ret=FAIL)
        return _r(f["prop"], "")

    def SetLocalAxes(self, Name, Ang, ItemType=0):
        names = self._names(Name, ItemType)
        if names is None:
            return FAIL
        for n in names:
            self._sm._st["frames"][n]["angle"] = float(Ang)
        self._touch()
        return 0

    def GetLocalAxes(self, Name, Ang=0.0, Advanced=False):
        f = self._sm._st["frames"].get(Name)
        if f is None:
            return _r(0.0, False, ret=FAIL)
        return _r(f["angle"], False)

    def SetReleases(self, Name, ii, jj, StartValue, EndValue, ItemType=0):
        """U1 U2 U3 R1 R2 R3 (= P V2 V3 T M2 M3) releases; the values are
        partial-fixity springs (0 = free)."""
        names = self._names(Name, ItemType)
        if names is None or len(ii) < 6 or len(jj) < 6:
            return _r(ii, jj, StartValue, EndValue, ret=FAIL)
        u = self._u
        dofs = ("P", "V2", "V3", "T", "M2", "M3")

        def end(flags, vals):
            out = {}
            for k, d in enumerate(dofs):
                if flags[k]:
                    v = float(vals[k]) if vals and len(vals) > k else 0.0
                    q = "stiffness" if k < 3 else "rot_stiffness"
                    out[d] = u.si(v, q)
            return out

        rel = {"i": end(ii, StartValue), "j": end(jj, EndValue)}
        for n in names:
            self._sm._st["frames"][n]["rel"] = rel if (rel["i"] or
                                                       rel["j"]) else None
        self._touch()
        return _r(list(ii), list(jj), list(StartValue), list(EndValue))

    def SetLoadDistributed(self, Name, LoadPat, MyType, Dir, Dist1, Dist2,
                           Val1, Val2, CSys="Global", RelDist=True,
                           Replace=True, ItemType=0):
        """MyType 1 = force/length (moments are not supported); Dir 1..3
        local, 4..6 global X/Y/Z, 7..9 projected X/Y/Z, 10 gravity, 11
        projected gravity."""
        names = self._names(Name, ItemType)
        st = self._sm._st
        if names is None or LoadPat not in st["patterns"] or \
                int(MyType) != 1 or int(Dir) not in range(1, 12):
            return FAIL
        u = self._u
        for n in names:
            L = self._sm._frame_length(n)
            a, b = float(Dist1), float(Dist2)
            if not RelDist:
                a, b = u.si(a, "length") / L, u.si(b, "length") / L
            if not (0.0 <= a < b <= 1.0 + 1e-9):
                return FAIL
            key = (n, LoadPat)
            lst = st["frame_loads"].setdefault(key, [])
            if Replace:
                lst[:] = [x for x in lst if x["kind"] == "point"
                          or x["kind"] == "moment"]
            lst.append({"kind": "dist", "dir": int(Dir),
                        "w1": u.si(Val1, "line_force"),
                        "w2": u.si(Val2, "line_force"),
                        "a": a, "b": min(b, 1.0)})
        self._touch()
        return 0

    def SetLoadPoint(self, Name, LoadPat, MyType, Dir, Dist, Val,
                     CSys="Global", RelDist=True, Replace=True, ItemType=0):
        """MyType 1 = force, 2 = moment."""
        names = self._names(Name, ItemType)
        st = self._sm._st
        if names is None or LoadPat not in st["patterns"] or \
                int(MyType) not in (1, 2) or int(Dir) not in range(1, 12):
            return FAIL
        if int(MyType) == 2 and int(Dir) > 6:
            return FAIL
        u = self._u
        for n in names:
            L = self._sm._frame_length(n)
            a = float(Dist) if RelDist else u.si(Dist, "length") / L
            if not 0.0 <= a <= 1.0 + 1e-9:
                return FAIL
            key = (n, LoadPat)
            lst = st["frame_loads"].setdefault(key, [])
            if Replace:
                lst[:] = [x for x in lst if x["kind"] == "dist"]
            lst.append({"kind": "point" if int(MyType) == 1 else "moment",
                        "dir": int(Dir), "a": min(a, 1.0),
                        "w1": u.si(Val, "force" if int(MyType) == 1
                                   else "moment")})
        self._touch()
        return 0

    def DeleteLoadDistributed(self, Name, LoadPat, ItemType=0):
        for n in self._names(Name, ItemType) or []:
            lst = self._sm._st["frame_loads"].get((n, LoadPat), [])
            lst[:] = [x for x in lst if x["kind"] != "dist"]
        self._touch()
        return 0

    def DeleteLoadPoint(self, Name, LoadPat, ItemType=0):
        for n in self._names(Name, ItemType) or []:
            lst = self._sm._st["frame_loads"].get((n, LoadPat), [])
            lst[:] = [x for x in lst if x["kind"] == "dist"]
        self._touch()
        return 0

    def SetGroupAssign(self, Name, GroupName, Remove=False, ItemType=0):
        return self._sm._group_assign("frames", Name, GroupName, Remove,
                                      ItemType)

    def Delete(self, Name, ItemType=0):
        names = self._names(Name, ItemType)
        if names is None:
            return FAIL
        for n in names:
            self._sm._st["frames"].pop(n, None)
            for k in [k for k in self._sm._st["frame_loads"] if k[0] == n]:
                del self._sm._st["frame_loads"][k]
        self._touch()
        return 0

    def GetNameList(self, NumberNames=0, MyName=None):
        names = list(self._sm._st["frames"])
        return _r(len(names), names)

    def GetNameListOnStory(self, StoryName, NumberNames=0, MyName=None):
        sm = self._sm
        names = [n for n in sm._st["frames"] if sm._frame_story(n)
                 == StoryName]
        return _r(len(names), names)

    def Count(self, MyType="All"):
        return len(self._sm._st["frames"])

    def GetLabelFromName(self, Name, Label="", Story=""):
        if Name not in self._sm._st["frames"]:
            return _r("", "", ret=FAIL)
        return _r(Name, self._sm._frame_story(Name))


class cAreaObj(_Part):
    def AddByCoord(self, NumberPoints, X, Y, Z, Name="", PropName="Default",
                   UserName="", CSys="Global"):
        n = int(NumberPoints)
        if n < 3 or len(X) < n or len(Y) < n or len(Z) < n:
            return _r(X, Y, Z, "", ret=FAIL)
        u = self._u
        pts = [self._sm._point((u.si(X[k], "length"), u.si(Y[k], "length"),
                                u.si(Z[k], "length")), "")
               for k in range(n)]
        name, code = self._add(pts, PropName, UserName)
        return _r(list(X), list(Y), list(Z), name, ret=code)

    def AddByPoint(self, NumberPoints, Point, Name="", PropName="Default",
                   UserName=""):
        n = int(NumberPoints)
        pts = list(Point[:n])
        if n < 3 or any(p not in self._sm._st["points"] for p in pts):
            return _r(list(Point), "", ret=FAIL)
        name, code = self._add(pts, PropName, UserName)
        return _r(list(Point), name, ret=code)

    def _add(self, pts, PropName, UserName):
        st = self._sm._st
        prop = PropName
        if prop in ("Default", "", None):
            prop = next(iter(st["area_props"]), None)
        if prop is None or prop not in st["area_props"]:
            return ["", FAIL]
        name = UserName or self._sm._next("areas")
        if name in st["areas"]:
            return ["", FAIL]
        st["areas"][name] = {"pts": pts, "prop": prop, "mesh": 1.0}
        self._touch()
        return [name, 0]

    def _names(self, Name, ItemType):
        return self._sm._targets("areas", Name, ItemType)

    def GetPoints(self, Name, NumberPoints=0, Point=None):
        a = self._sm._st["areas"].get(Name)
        if a is None:
            return _r(0, [], ret=FAIL)
        return _r(len(a["pts"]), list(a["pts"]))

    def SetProperty(self, Name, PropName, ItemType=0):
        names = self._names(Name, ItemType)
        if names is None or PropName not in self._sm._st["area_props"]:
            return FAIL
        for n in names:
            self._sm._st["areas"][n]["prop"] = PropName
        self._touch()
        return 0

    def SetLoadUniform(self, Name, LoadPat, Value, Dir, Replace=True,
                       CSys="Global", ItemType=0):
        names = self._names(Name, ItemType)
        st = self._sm._st
        if names is None or LoadPat not in st["patterns"] or \
                int(Dir) not in _AREA_DIR:
            return FAIL
        q = self._u.si(Value, "pressure")
        for n in names:
            key = (n, LoadPat)
            lst = st["area_loads"].setdefault(key, [])
            if Replace:
                lst.clear()
            lst.append({"dir": int(Dir), "q": q})
        self._touch()
        return 0

    def SetDiaphragm(self, Name, DiaphragmName, ItemType=0):
        names = self._names(Name, ItemType)
        if names is None or (DiaphragmName and DiaphragmName not in
                             self._sm._st["diaphragms"]):
            return FAIL
        for n in names:
            if DiaphragmName:
                self._sm._st["area_diaph"][n] = DiaphragmName
            else:
                self._sm._st["area_diaph"].pop(n, None)
        self._touch()
        return 0

    def SetAutoMesh(self, Name, MeshType, n1=2, n2=2, MaxSize1=0.0,
                    MaxSize2=0.0, *args, ItemType=0, **kw):
        names = self._names(Name, ItemType)
        if names is None:
            return FAIL
        size = max(float(MaxSize1), float(MaxSize2))
        if size > 0:
            for n in names:
                self._sm._st["areas"][n]["mesh"] = self._u.si(size,
                                                              "length")
        self._touch()
        return 0

    def SetGroupAssign(self, Name, GroupName, Remove=False, ItemType=0):
        return self._sm._group_assign("areas", Name, GroupName, Remove,
                                      ItemType)

    def GetNameList(self, NumberNames=0, MyName=None):
        names = list(self._sm._st["areas"])
        return _r(len(names), names)

    def Count(self, MyType="All"):
        return len(self._sm._st["areas"])

    def Delete(self, Name, ItemType=0):
        names = self._names(Name, ItemType)
        if names is None:
            return FAIL
        for n in names:
            self._sm._st["areas"].pop(n, None)
        self._touch()
        return 0


# =========================================================================== #
# Definitions: stories, diaphragms, groups, patterns, cases, combos
# =========================================================================== #
class cStory(_Part):
    def SetStories(self, StoryNames, StoryElevations, StoryHeights,
                   IsMasterStory=None, SimilarToStory=None,
                   SpliceAbove=None, SpliceHeight=None):
        """``StoryElevations`` may carry the base elevation first (one
        more entry than ``StoryNames``)."""
        n = len(StoryNames)
        u = self._u
        el = [u.si(e, "length") for e in StoryElevations]
        hs = [u.si(h, "length") for h in StoryHeights]
        if n < 1 or len(hs) < n:
            return _r(StoryNames, StoryElevations, StoryHeights,
                      IsMasterStory, SimilarToStory, SpliceAbove,
                      SpliceHeight, ret=FAIL)
        base = el[0] if len(el) == n + 1 else (el[0] - hs[0] if el else 0.0)
        ret = self._set(base, list(StoryNames), hs[:n])
        return _r(list(StoryNames), list(StoryElevations), list(StoryHeights),
                  IsMasterStory, SimilarToStory, SpliceAbove, SpliceHeight,
                  ret=ret)

    def SetStories_2(self, BaseElevation, NumberStories, StoryNames,
                     StoryHeights, IsMasterStory=None, SimilarToStory=None,
                     SpliceAbove=None, SpliceHeight=None, color=None):
        n = int(NumberStories)
        u = self._u
        ret = self._set(u.si(BaseElevation, "length"), list(StoryNames[:n]),
                        [u.si(h, "length") for h in StoryHeights[:n]])
        return _r(list(StoryNames), list(StoryHeights), IsMasterStory,
                  SimilarToStory, SpliceAbove, SpliceHeight, color, ret=ret)

    def _set(self, base, names, heights) -> int:
        if any(h <= 0 for h in heights) or len(set(names)) != len(names):
            return FAIL
        rows, z = [], base
        for nm, h in zip(names, heights):
            z += h
            rows.append([nm, z, h])
        self._sm._st["stories"] = rows
        self._sm._st["base_elev"] = base
        self._touch()
        return 0

    def GetStories(self, NumberStories=0, StoryNames=None,
                   StoryElevations=None, StoryHeights=None,
                   IsMasterStory=None, SimilarToStory=None,
                   SpliceAbove=None, SpliceHeight=None):
        """Includes the base (first) like ETABS."""
        st = self._sm._st
        u = self._u
        rows = [["Base", st.get("base_elev", 0.0), 0.0]] + \
            list(st["stories"])
        n = len(rows)
        return _r(n, [r[0] for r in rows],
                  [u.out(r[1], "length") for r in rows],
                  [u.out(r[2], "length") for r in rows],
                  [False] * n, ["None"] * n, [False] * n, [0.0] * n)

    def GetNameList(self, NumberNames=0, MyName=None):
        names = [r[0] for r in self._sm._st["stories"]]
        return _r(len(names), names)

    def GetElevation(self, Name, Elevation=0.0):
        for r in self._sm._st["stories"]:
            if r[0] == Name:
                return _r(self._u.out(r[1], "length"))
        return _r(0.0, ret=FAIL)

    def GetHeight(self, Name, Height=0.0):
        for r in self._sm._st["stories"]:
            if r[0] == Name:
                return _r(self._u.out(r[2], "length"))
        return _r(0.0, ret=FAIL)


class cDiaphragm(_Part):
    def SetDiaphragm(self, Name, SemiRigid=False):
        self._sm._st["diaphragms"][Name] = bool(SemiRigid)
        self._touch()
        return 0

    def GetNameList(self, NumberNames=0, MyName=None):
        names = list(self._sm._st["diaphragms"])
        return _r(len(names), names)


class cGroupDef(_Part):
    def SetGroup(self, Name, color=-1, SpecifiedForSelection=True,
                 *args, **kw):
        self._sm._st["groups"].setdefault(Name, {"points": [], "frames": [],
                                                 "areas": []})
        return 0

    def GetNameList(self, NumberNames=0, MyName=None):
        names = list(self._sm._st["groups"])
        return _r(len(names), names)

    def Delete(self, Name):
        if self._sm._st["groups"].pop(Name, None) is None:
            return FAIL
        return 0


class cLoadPatterns(_Part):
    def Add(self, Name, MyType, SelfWTMultiplier=0.0, AddAnalysisCase=True):
        st = self._sm._st
        if not Name or Name in st["patterns"]:
            return FAIL
        st["patterns"][Name] = {"type": int(MyType),
                                "sw": float(SelfWTMultiplier)}
        if AddAnalysisCase and Name not in st["cases"]:
            st["cases"][Name] = {"type": "static",
                                 "loads": [("Load", Name, 1.0)]}
        self._touch()
        return 0

    def Delete(self, Name):
        if self._sm._st["patterns"].pop(Name, None) is None:
            return FAIL
        self._touch()
        return 0

    def SetSelfWTMultiplier(self, Name, Val):
        p = self._sm._st["patterns"].get(Name)
        if p is None:
            return FAIL
        p["sw"] = float(Val)
        self._touch()
        return 0

    def GetSelfWTMultiplier(self, Name, Val=0.0):
        p = self._sm._st["patterns"].get(Name)
        return _r(p["sw"]) if p else _r(0.0, ret=FAIL)

    def SetLoadType(self, Name, MyType):
        p = self._sm._st["patterns"].get(Name)
        if p is None:
            return FAIL
        p["type"] = int(MyType)
        self._touch()
        return 0

    def GetLoadType(self, Name, MyType=0):
        p = self._sm._st["patterns"].get(Name)
        return _r(p["type"]) if p else _r(0, ret=FAIL)

    def GetNameList(self, NumberNames=0, MyName=None):
        names = list(self._sm._st["patterns"])
        return _r(len(names), names)

    def Count(self):
        return len(self._sm._st["patterns"])


class _cStaticLinear(_Part):
    def SetCase(self, Name):
        st = self._sm._st
        cur = st["cases"].get(Name)
        if cur is not None and cur["type"] != "static":
            return FAIL
        if cur is None:
            st["cases"][Name] = {"type": "static", "loads": []}
        self._touch()
        return 0

    def SetLoads(self, Name, NumberLoads, LoadType, LoadName, SF):
        c = self._sm._st["cases"].get(Name)
        n = int(NumberLoads)
        if c is None or c["type"] != "static" or len(LoadName) < n:
            return _r(LoadType, LoadName, SF, ret=FAIL)
        loads = []
        for k in range(n):
            if str(LoadType[k]) != "Load" or \
                    LoadName[k] not in self._sm._st["patterns"]:
                return _r(LoadType, LoadName, SF, ret=FAIL)
            loads.append(("Load", LoadName[k], float(SF[k])))
        c["loads"] = loads
        self._touch()
        return _r(list(LoadType), list(LoadName), list(SF))

    def GetLoads(self, Name, NumberLoads=0, LoadType=None, LoadName=None,
                 SF=None):
        c = self._sm._st["cases"].get(Name)
        if c is None or c["type"] != "static":
            return _r(0, [], [], [], ret=FAIL)
        L = c["loads"]
        return _r(len(L), [x[0] for x in L], [x[1] for x in L],
                  [x[2] for x in L])


class _cModalEigen(_Part):
    def SetCase(self, Name):
        st = self._sm._st
        for k in [k for k, c in st["cases"].items() if c["type"] == "modal"]:
            if k != Name:
                del st["cases"][k]          # one modal case (SkyFrame)
        st["cases"].setdefault(Name, {"type": "modal",
                                      "modes": st.get("num_modes", 12)})
        self._touch()
        return 0

    def SetNumberModes(self, Name, MaxModes, MinModes=1):
        c = self._sm._st["cases"].get(Name)
        if c is None or c["type"] != "modal" or int(MaxModes) < 1:
            return FAIL
        c["modes"] = int(MaxModes)
        self._touch()
        return 0

    def GetNumberModes(self, Name, MaxModes=0, MinModes=0):
        c = self._sm._st["cases"].get(Name)
        if c is None or c["type"] != "modal":
            return _r(0, 0, ret=FAIL)
        return _r(c["modes"], 1)


class cLoadCases(_Part):
    def __init__(self, sm):
        super().__init__(sm)
        self.StaticLinear = _cStaticLinear(sm)
        self.ModalEigen = _cModalEigen(sm)

    def GetNameList(self, NumberNames=0, MyName=None, CaseType=0):
        names = list(self._sm._st["cases"])
        return _r(len(names), names)

    def Count(self, CaseType=0):
        return len(self._sm._st["cases"])

    def Delete(self, Name):
        if self._sm._st["cases"].pop(Name, None) is None:
            return FAIL
        self._touch()
        return 0

    def GetTypeOAPI(self, Name, CaseType=0, SubType=0):
        c = self._sm._st["cases"].get(Name)
        if c is None:
            return _r(0, 0, ret=FAIL)
        return _r(1 if c["type"] == "static" else 3, 1)


class cRespCombo(_Part):
    def Add(self, Name, ComboType):
        st = self._sm._st
        if not Name or Name in st["combos"] or int(ComboType) not in \
                COMBO_TYPES:
            return FAIL
        st["combos"][Name] = {"type": int(ComboType), "items": []}
        self._touch()
        return 0

    def SetCaseList(self, Name, CNameType, CName, SF):
        """CNameType 0 = load case, 1 = load combination."""
        st = self._sm._st
        c = st["combos"].get(Name)
        ok = c is not None and (
            (int(CNameType) == 0 and CName in st["cases"]) or
            (int(CNameType) == 1 and CName in st["combos"]
             and CName != Name))
        if not ok:
            return _r(CNameType, ret=FAIL)
        c["items"] = [x for x in c["items"] if x[1] != CName]
        c["items"].append((int(CNameType), CName, float(SF)))
        self._touch()
        return _r(CNameType)

    def GetCaseList(self, Name, NumberItems=0, CNameType=None, CName=None,
                    SF=None):
        c = self._sm._st["combos"].get(Name)
        if c is None:
            return _r(0, [], [], [], ret=FAIL)
        it = c["items"]
        return _r(len(it), [x[0] for x in it], [x[1] for x in it],
                  [x[2] for x in it])

    def Delete(self, Name):
        if self._sm._st["combos"].pop(Name, None) is None:
            return FAIL
        self._touch()
        return 0

    def GetNameList(self, NumberNames=0, MyName=None):
        names = list(self._sm._st["combos"])
        return _r(len(names), names)

    def Count(self):
        return len(self._sm._st["combos"])


class cAnalyze(_Part):
    def RunAnalysis(self):
        return self._sm._run()

    def CreateAnalysisModel(self):
        try:
            self._sm.to_skyframe()
        except (ValueError, KeyError, TypeError):
            return FAIL
        return 0

    def SetRunCaseFlag(self, Name, Run, All=False):
        st = self._sm._st
        names = list(st["cases"]) if All else [Name]
        if not All and Name not in st["cases"]:
            return FAIL
        for n in names:
            if Run:
                st["not_run"].discard(n)
            else:
                st["not_run"].add(n)
        self._touch()
        return 0

    def GetCaseStatus(self, NumberItems=0, CaseName=None, Status=None):
        """Status 1 not run, 4 finished (CSI codes)."""
        st = self._sm._st
        res = self._sm._results
        names = list(st["cases"])
        status = [4 if res is not None and n not in st["not_run"] else 1
                  for n in names]
        return _r(len(names), names, status)

    def SetActiveDOF(self, DOF):
        if len(DOF) < 6:
            return _r(DOF, ret=FAIL)
        self._sm._st["active_dof"] = [bool(x) for x in DOF[:6]]
        self._touch()
        return _r(list(DOF))

    def GetActiveDOF(self, DOF=None):
        return _r(list(self._sm._st["active_dof"]))

    def SetSolverOption_3(self, *args, **kw):
        return 0

    SetSolverOption_2 = SetSolverOption_1 = SetSolverOption_3


class cView(_Part):
    def RefreshView(self, Window=0, Zoom=True):
        return 0

    def RefreshWindow(self, Window=0):
        return 0


class cSelectObj(_Part):
    def ClearSelection(self):
        return 0

    def All(self, DeSelect=False):
        return 0
