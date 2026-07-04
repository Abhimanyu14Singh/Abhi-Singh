"""Built-in steel frame-section library (v0.3): common AISC W-shapes.

Source values: AISC Steel Construction Manual (15th ed.) Table 1-1,
W-shape dimensions and properties, IMPERIAL units:

  * A  ... in^2      (gross area)
  * Ix ... in^4      (strong-axis moment of inertia -> I33)
  * Iy ... in^4      (weak-axis moment of inertia   -> I22)
  * J  ... in^4      (torsional constant)
  * d  ... in        (overall depth  -> drawing ``h``)
  * bf ... in        (flange width   -> drawing ``b``)

Converted here to the SkyFrame SI unit system (m-based) with exact factors:

  1 in           = 0.0254 m                (exact, by definition)
  1 in^2         = 0.0254^2  = 6.4516e-4          m^2   (exact)
  1 in^4         = 0.0254^4  = 4.162314256e-7     m^4   (exact)

The library stores IMPERIAL values (as published) and converts on access so
the conversion is auditable and exact.
"""

from __future__ import annotations

from typing import Dict, List

from skyframe.core.model import FrameSection

_IN = 0.0254                # m per inch (exact)
_IN2 = _IN ** 2             # m^2 per in^2
_IN4 = _IN ** 4             # m^4 per in^4

# label: (A [in^2], Ix [in^4], Iy [in^4], J [in^4], d [in], bf [in])
# AISC Manual 15th ed., Table 1-1.
_W_SHAPES_IMPERIAL: Dict[str, tuple] = {
    "W8x31":   (9.13,   110.0,   37.1,  0.536,  8.00,  8.00),
    "W8x40":   (11.7,   146.0,   49.1,  1.12,   8.25,  8.07),
    "W10x33":  (9.71,   171.0,   36.6,  0.583,  9.73,  7.96),
    "W10x49":  (14.4,   272.0,   93.4,  1.39,   9.98, 10.00),
    "W12x26":  (7.65,   204.0,   17.3,  0.300, 12.20,  6.49),
    "W12x40":  (11.7,   307.0,   44.1,  0.906, 11.90,  8.01),
    "W12x65":  (19.1,   533.0,  174.0,  2.18,  12.10, 12.00),
    "W14x30":  (8.85,   291.0,   19.6,  0.380, 13.80,  6.73),
    "W14x48":  (14.1,   484.0,   51.4,  1.45,  13.80,  8.03),
    "W14x90":  (26.5,   999.0,  362.0,  4.06,  14.00, 14.50),
    "W16x36":  (10.6,   448.0,   24.5,  0.545, 15.90,  6.99),
    "W16x57":  (16.8,   758.0,   43.1,  2.22,  16.40,  7.12),
    "W18x50":  (14.7,   800.0,   40.1,  1.24,  18.00,  7.50),
    "W18x76":  (22.3,  1330.0,  152.0,  2.83,  18.20, 11.00),
    "W21x62":  (18.3,  1330.0,   57.5,  1.83,  21.00,  8.24),
    "W21x93":  (27.3,  2070.0,   92.9,  6.03,  21.60,  8.42),
    "W24x76":  (22.4,  2100.0,   82.5,  2.68,  23.90,  8.99),
    "W24x104": (30.7,  3100.0,  259.0,  4.72,  24.10, 12.80),
    "W27x94":  (27.6,  3270.0,  124.0,  4.03,  26.90, 10.00),
    "W30x116": (34.2,  4930.0,  164.0,  6.43,  30.00, 10.50),
    "W33x130": (38.3,  6710.0,  218.0,  7.37,  33.10, 11.50),
    "W36x150": (44.3,  9040.0,  270.0, 10.10,  35.90, 12.00),
}


def library_names() -> List[str]:
    """Available shape labels (library order: light to heavy)."""
    return list(_W_SHAPES_IMPERIAL)


def library_properties(name: str) -> dict:
    """SI properties dict for one shape: A [m^2], I33/I22/J [m^4], b/h [m]."""
    try:
        A, Ix, Iy, J, d, bf = _W_SHAPES_IMPERIAL[name]
    except KeyError:
        raise KeyError(f"Unknown library section {name!r}; available: "
                       f"{', '.join(library_names())}") from None
    return {
        "name": name,
        "A": A * _IN2,
        "I33": Ix * _IN4,
        "I22": Iy * _IN4,
        "J": J * _IN4,
        "h": d * _IN,     # depth -> drawing h
        "b": bf * _IN,    # flange width -> drawing b
    }


def library_section(name: str, material: str) -> FrameSection:
    """A ready-to-add :class:`FrameSection` for one library shape."""
    p = library_properties(name)
    return FrameSection(name=p["name"], material=material, A=p["A"],
                        I33=p["I33"], I22=p["I22"], J=p["J"],
                        b=p["b"], h=p["h"])


def library_to_dict() -> List[dict]:
    """JSON-safe listing of the whole library (SI units)."""
    return [library_properties(n) for n in library_names()]
