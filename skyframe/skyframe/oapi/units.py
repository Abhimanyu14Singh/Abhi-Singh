"""CSI ``eUnits`` enumeration and the present-unit converter of the OAPI
facade (:mod:`skyframe.oapi`).

Every value a script passes in is in the PRESENT units (``SetPresentUnits``)
and is converted to SkyFrame SI (kN, m, deg C) on the way in; every result
is converted back on the way out, exactly like the CSI API.
"""

from __future__ import annotations

from typing import Dict, Tuple

KIP = 4.4482216152605        # kN
LBF = KIP / 1000.0
KGF = 9.80665e-3
TONF = 9.80665               # metric tonne-force (CSI "Ton")
IN = 0.0254
FT = 0.3048

#: CSI eUnits value -> (name, kN per force unit, m per length unit, temp)
EUNITS: Dict[int, Tuple[str, float, float, str]] = {
    1: ("lb_in_F", LBF, IN, "F"),
    2: ("lb_ft_F", LBF, FT, "F"),
    3: ("kip_in_F", KIP, IN, "F"),
    4: ("kip_ft_F", KIP, FT, "F"),
    5: ("kN_mm_C", 1.0, 1e-3, "C"),
    6: ("kN_m_C", 1.0, 1.0, "C"),
    7: ("kgf_mm_C", KGF, 1e-3, "C"),
    8: ("kgf_m_C", KGF, 1.0, "C"),
    9: ("N_mm_C", 1e-3, 1e-3, "C"),
    10: ("N_m_C", 1e-3, 1.0, "C"),
    11: ("Ton_mm_C", TONF, 1e-3, "C"),
    12: ("Ton_m_C", TONF, 1.0, "C"),
    13: ("kN_cm_C", 1.0, 1e-2, "C"),
    14: ("kgf_cm_C", KGF, 1e-2, "C"),
    15: ("N_cm_C", 1e-3, 1e-2, "C"),
    16: ("Ton_cm_C", TONF, 1e-2, "C"),
}
EUNIT_BY_NAME = {v[0]: k for k, v in EUNITS.items()}


class PresentUnits:
    """Converter for one eUnits value (file value * factor = SI)."""

    def __init__(self, code: int = 6):
        if code not in EUNITS:
            raise ValueError(f"unknown eUnits value {code!r}")
        self.code = code
        self.name, self.F, self.L, self.temp = EUNITS[code]

    def factor(self, q: str) -> float:
        F, L = self.F, self.L
        return {"none": 1.0, "length": L, "area": L * L, "volume": L ** 3, "inertia": L ** 4,
                "force": F, "moment": F * L, "stress": F / L ** 2,
                "line_force": F / L, "pressure": F / L ** 2,
                "unit_weight": F / L ** 3, "stiffness": F / L,
                "rot_stiffness": F * L,
                # mass = F*s^2/L  -> tonne = kN*s^2/m
                "mass": F / L, "mass_density": F / L ** 4,
                "alpha": 1.8 if self.temp == "F" else 1.0}[q]

    def si(self, v, q: str) -> float:
        return float(v) * self.factor(q)

    def out(self, v, q: str) -> float:
        k = self.factor(q)
        return float(v) / k if k != 1.0 else float(v)
