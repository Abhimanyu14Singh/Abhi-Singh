"""Per-quantity display units and decimals (Wave C, ETABS Options > Units).

CONTRACT "Wave C interface: general grids, explorer, shells with openings,
snapping, units, auto-save, model lock".

ETABS combines a consistent unit set (``BuildingModel.display_units``,
"kN-m" ... "kip-in") with per-quantity overrides and decimal places.  Two
optional model keys carry them; both are DISPLAY ONLY (the model and the
engine stay SI) and are emitted by ``to_dict`` only when non-empty, so every
existing model stays byte-identical:

* ``display_units_custom``: ``{kind: unit label}`` - ``kind`` is a quantity
  kind of ``js/units.js`` (``length``, ``force``, ``stress`` ...) and the
  label one of :func:`unit_catalog` ``[kind]`` (e.g. ``"MPa"``, ``"kip·ft"``);
* ``display_decimals``: ``{kind: int 0..10}``.

The catalogue is generated from the force / length units below exactly
like ``js/wc_unitcat.js`` (the browser checks ``GET /api/units/catalog``
against its own copy).  ``si`` is the number of SI units (kN, m, kPa, t,
s) in one display unit: ``SI = display * si`` (temperatures are affine:
``degF = 1.8 degC + 32``).
"""

from __future__ import annotations

import math
from typing import Dict, List

KIP = 4.4482216152605
LBF = KIP / 1000.0
TONF = 9.80665
KGF = 9.80665e-3
FORCE = [("N", 1e-3), ("kN", 1.0), ("kgf", KGF), ("tonf", TONF),
         ("lb", LBF), ("kip", KIP)]
LENGTH = [("mm", 1e-3), ("cm", 1e-2), ("m", 1.0), ("in", 0.0254),
          ("ft", 0.3048)]
PAIRS = [("N", "mm"), ("N", "m"), ("kN", "mm"), ("kN", "m"),
         ("kgf", "cm"), ("kgf", "m"), ("tonf", "m"), ("lb", "in"),
         ("lb", "ft"), ("kip", "in"), ("kip", "ft")]
_F = dict(FORCE)
_L = dict(LENGTH)
_SUP = {2: "²", 3: "³", 4: "⁴"}

# kind -> (force exponent, length exponent, label template) for the pair
# families; "{f}" / "{l}" are replaced by the unit names.
_PAIR_KINDS = {
    "moment": (1, 1, "{f}·{l}"),
    "line_force": (1, -1, "{f}/{l}"),
    "line_moment": (1, 0, "{f}·{l}/{l}"),
    "unit_weight": (1, -3, "{f}/{l}³"),
    "subgrade": (1, -3, "{f}/{l}³"),
    "line_spring": (1, -2, "{f}/{l}/{l}"),
    "stiffness": (1, -1, "{f}/{l}"),
    "rot_stiffness": (1, 1, "{f}·{l}/rad"),
    "damping_coeff": (1, -1, "{f}·s/{l}"),
    "mass": (1, -1, "{f}·s²/{l}"),
    "mass_density": (1, -4, "{f}·s²/{l}⁴"),
    "mass_moi": (1, 1, "{f}·s²·{l}"),
    "mass_per_length": (1, -2, "{f}·s²/{l}²"),
    "mass_per_area": (1, -3, "{f}·s²/{l}³"),
}
_LEN_KINDS = {"length": (1, ""), "dim": (1, ""), "small": (1, ""),
              "disp": (1, ""), "area": (2, "²"), "inertia": (4, "⁴"),
              "secmod": (3, "³"), "accel": (1, "/s²"),
              "velocity": (1, "/s")}
_STRESS = ["Pa", "kPa", "MPa", "GPa", "N/mm²", "kN/m²", "kN/mm²",
           "kgf/cm²", "tonf/m²", "psi", "ksi", "psf", "ksf"]
_STRESS_SI = {"Pa": 1e-3, "kPa": 1.0, "MPa": 1e3, "GPa": 1e6,
              "N/mm²": 1e-3 / 1e-6, "kN/m²": 1.0, "kN/mm²": 1.0 / 1e-6,
              "kgf/cm²": KGF / 1e-4, "tonf/m²": TONF,
              "psi": LBF / 0.0254 ** 2, "ksi": KIP / 0.0254 ** 2,
              "psf": LBF / 0.3048 ** 2, "ksf": KIP / 0.3048 ** 2}
_NAMED_MASS = {"mass": [("kg", 1e-3), ("t", 1.0)],
               "mass_density": [("kg/m³", 1e-3), ("t/m³", 1.0)],
               "mass_moi": [("kg·m²", 1e-3), ("t·m²", 1.0)],
               "mass_per_length": [("kg/m", 1e-3), ("t/m", 1.0)],
               "mass_per_area": [("kg/m²", 1e-3), ("t/m²", 1.0)]}

# every quantity kind of js/units.js (decimals may be set for any of them)
QUANTITY_KINDS = (
    "length", "dim", "small", "disp", "area", "inertia", "secmod", "force",
    "moment", "line_force", "line_moment", "pressure", "stress", "modulus",
    "unit_weight", "subgrade", "line_spring", "stiffness", "rot_stiffness",
    "damping_coeff", "mass", "mass_density", "mass_moi", "mass_per_length",
    "mass_per_area", "accel", "velocity", "temp", "temp_delta",
    "thermal_coeff", "temp_gradient", "rotation", "frequency", "period",
    "strain", "none")
MAX_DECIMALS = 10


def unit_catalog() -> Dict[str, List[dict]]:
    """``{kind: [{"label", "si"}...]}`` - the selectable units per kind."""
    out: Dict[str, List[dict]] = {}
    for kind, (p, suf) in _LEN_KINDS.items():
        out[kind] = [{"label": f"{n}{suf}", "si": f ** p} for n, f in LENGTH]
        if kind == "accel":
            out[kind].append({"label": "g", "si": 9.80665})
    out["force"] = [{"label": n, "si": f} for n, f in FORCE]
    for kind in ("pressure", "stress", "modulus"):
        out[kind] = [{"label": n, "si": _STRESS_SI[n]} for n in _STRESS]
    for kind, (a, b, tpl) in _PAIR_KINDS.items():
        rows = [{"label": n, "si": s} for n, s in _NAMED_MASS.get(kind, [])]
        seen = {r["label"] for r in rows}
        for fn, ln in PAIRS:
            lab = tpl.replace("{f}", fn).replace("{l}", ln)
            if lab in seen:
                continue
            seen.add(lab)
            rows.append({"label": lab, "si": _F[fn] ** a * _L[ln] ** b})
        out[kind] = rows
    out["temp"] = [{"label": "°C", "si": 1.0}, {"label": "°F", "si": 1.0 / 1.8}]
    out["temp_delta"] = [{"label": "°C", "si": 1.0},
                         {"label": "°F", "si": 1.0 / 1.8}]
    out["thermal_coeff"] = [{"label": "1/°C", "si": 1.0},
                            {"label": "1/°F", "si": 1.8}]
    out["rotation"] = [{"label": "rad", "si": 1.0},
                       {"label": "deg", "si": math.pi / 180.0}]
    out["period"] = [{"label": "s", "si": 1.0}, {"label": "ms", "si": 1e-3}]
    return out


_CAT = unit_catalog()


def validate(model) -> None:
    """Model hook: ValueError on an unknown kind / unit / bad decimals."""
    uc = getattr(model, "units_custom", None) or {}
    units = uc.get("units") or {}
    decs = uc.get("decimals") or {}
    if not isinstance(units, dict) or not isinstance(decs, dict):
        raise ValueError("display_units_custom / display_decimals must be "
                         "objects")
    for kind, lab in units.items():
        if kind not in _CAT:
            raise ValueError(f"display_units_custom: no unit choice for "
                             f"{kind!r} (one of {sorted(_CAT)})")
        labels = [r["label"] for r in _CAT[kind]]
        if lab not in labels:
            raise ValueError(f"display_units_custom: {lab!r} is not a {kind} "
                             f"unit (one of {labels})")
    for kind, n in decs.items():
        if kind not in QUANTITY_KINDS:
            raise ValueError(f"display_decimals: unknown quantity {kind!r}")
        if isinstance(n, bool) or not isinstance(n, int) or not (
                0 <= n <= MAX_DECIMALS):
            raise ValueError(f"display_decimals: {kind} must be an integer "
                             f"0..{MAX_DECIMALS}, got {n!r}")


def model_to_dict(model) -> dict:
    uc = getattr(model, "units_custom", None) or {}
    out = {}
    if uc.get("units"):
        out["display_units_custom"] = dict(uc["units"])
    if uc.get("decimals"):
        out["display_decimals"] = dict(uc["decimals"])
    return out


def model_from_dict(model, d: dict) -> None:
    units = d.get("display_units_custom")
    decs = d.get("display_decimals")
    uc = {}
    if units:
        if not isinstance(units, dict):
            raise ValueError("display_units_custom must be an object")
        uc["units"] = dict(units)
    if decs:
        if not isinstance(decs, dict):
            raise ValueError("display_decimals must be an object")
        uc["decimals"] = dict(decs)
    model.units_custom = uc


def to_si(kind: str, label: str, value: float) -> float:
    """Display value in unit ``label`` -> SI (tests / API helpers)."""
    for r in _CAT[kind]:
        if r["label"] == label:
            if kind == "temp" and label == "°F":
                return (value - 32.0) / 1.8
            return value * r["si"]
    raise ValueError(f"unknown {kind} unit {label!r}")
