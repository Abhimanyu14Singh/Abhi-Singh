"""Display-unit conversion table (v1.13, persistence/presentation only).

The model and the engine are ALWAYS SI-consistent (kN, m, kPa, tonne,
degC).  ``BuildingModel.display_units`` only records the user's chosen
display set; the frontend converts with the table served by
``GET /api/units`` (:func:`units_table`).

Convention: a quantity of SI value ``v`` (in the base unit listed under
``quantities[q]["si"]``) displays as

    v * force_factor ** f * length_factor ** l

with ``(f, l)`` the quantity's force / length exponents and the factors
the set's ``factor_from_kN`` / ``factor_from_m``.  Temperatures convert
affinely (``T_F = 1.8 * T_C + 32``); temperature DIFFERENCES and thermal
coefficients scale by 1.8 / 1/1.8.
"""

from __future__ import annotations

from typing import Dict

KIP_IN_KN = 4.4482216152605        # 1 kip  = 4.4482216152605 kN
TONF_IN_KN = 9.80665               # 1 tonf = 9.80665 kN (metric tonne-force)
FT_IN_M = 0.3048                   # 1 ft   = 0.3048 m
IN_IN_M = 0.0254                   # 1 in   = 0.0254 m

# set -> (force label, factor_from_kN, length label, factor_from_m, temp)
UNIT_SETS = {
    "kN-m": ("kN", 1.0, "m", 1.0, "C"),
    "kN-mm": ("kN", 1.0, "mm", 1000.0, "C"),
    "N-mm": ("N", 1000.0, "mm", 1000.0, "C"),
    "tonf-m": ("tonf", 1.0 / TONF_IN_KN, "m", 1.0, "C"),
    "kip-ft": ("kip", 1.0 / KIP_IN_KN, "ft", 1.0 / FT_IN_M, "F"),
    "kip-in": ("kip", 1.0 / KIP_IN_KN, "in", 1.0 / IN_IN_M, "F"),
}

# quantity -> (force exponent, length exponent, SI base unit)
QUANTITIES = {
    "force": (1, 0, "kN"),
    "length": (0, 1, "m"),
    "displacement": (0, 1, "m"),
    "moment": (1, 1, "kN*m"),
    "stress": (1, -2, "kPa"),
    "modulus": (1, -2, "kPa"),
    "area_load": (1, -2, "kPa"),
    "line_load": (1, -1, "kN/m"),
    "unit_weight": (1, -3, "kN/m^3"),
    "area": (0, 2, "m^2"),
    "volume": (0, 3, "m^3"),
    "inertia": (0, 4, "m^4"),
    "section_modulus": (0, 3, "m^3"),
    "mass": (1, -1, "tonne (kN*s^2/m)"),
    "mass_density": (1, -4, "tonne/m^3 (kN*s^2/m^4)"),
    "rotational_mass": (1, 1, "tonne*m^2 (kN*m*s^2)"),
    "acceleration": (0, 1, "m/s^2"),
    "velocity": (0, 1, "m/s"),
    "translational_stiffness": (1, -1, "kN/m"),
    "rotational_stiffness": (1, 1, "kN*m/rad"),
    "line_spring": (1, -2, "kN/m/m"),
    "area_spring": (1, -3, "kN/m/m^2"),
    "rotation": (0, 0, "rad"),
    "strain": (0, 0, "-"),
    "time": (0, 0, "s"),
}


def _label(set_name: str, f: int, l: int) -> str:
    fl, _ff, ll, _lf, _t = UNIT_SETS[set_name]
    parts = []
    if f:
        parts.append(fl if f == 1 else f"{fl}^{f}")
    if l > 0:
        parts.append(ll if l == 1 else f"{ll}^{l}")
    num = "*".join(parts) if parts else "1"
    if l < 0:
        return f"{num}/{ll}" if l == -1 else f"{num}/{ll}^{-l}"
    return num if parts else ""


def factor(quantity: str, set_name: str) -> float:
    """Multiplier SI -> display for a quantity in a unit set."""
    f, l, _si = QUANTITIES[quantity]
    _fl, ff, _ll, lf, _t = UNIT_SETS[set_name]
    return ff ** f * lf ** l


def to_display(value: float, quantity: str, set_name: str) -> float:
    return value * factor(quantity, set_name)


def temperature_to_display(t_c: float, set_name: str) -> float:
    """Absolute temperature degC -> the set's scale."""
    return t_c * 1.8 + 32.0 if UNIT_SETS[set_name][4] == "F" else t_c


def units_table() -> dict:
    """``GET /api/units`` payload (see module docstring)."""
    sets: Dict[str, dict] = {}
    for name, (fl, ff, ll, lf, temp) in UNIT_SETS.items():
        sets[name] = {
            "force": [fl, ff],
            "length": [ll, lf],
            "temperature": temp,
            "labels": {q: _label(name, f, l)
                       for q, (f, l, _si) in QUANTITIES.items()
                       if (f, l) != (0, 0)},
            "factors": {q: factor(q, name) for q in QUANTITIES},
        }
    return {
        "base": {"force": "kN", "length": "m", "stress": "kPa",
                 "mass": "tonne", "temperature": "C", "time": "s"},
        "sets": sets,
        "quantities": {q: {"force": f, "length": l, "si": si}
                       for q, (f, l, si) in QUANTITIES.items()},
        "temperature": {"C": {"scale": 1.0, "offset": 0.0},
                        "F": {"scale": 1.8, "offset": 32.0}},
        "thermal_coefficient": {"C": 1.0, "F": 1.0 / 1.8},
        "constants": {"kip_kN": KIP_IN_KN, "tonf_kN": TONF_IN_KN,
                      "ft_m": FT_IN_M, "in_m": IN_IN_M},
        "default": "kN-m",
    }
