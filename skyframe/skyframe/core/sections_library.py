"""Built-in steel frame-section library (v0.3): common AISC W-shapes.

Since v1.21 the values come from the AISC Shapes Database v16.0 file of
:mod:`skyframe.core.steel_db` (``skyframe/data/steel/aisc.json``), which
keeps the PUBLISHED US-customary values of every shape in its ``aisc``
block.  This module keeps its historical API and its 22-shape list:

  * A  ... in^2      (gross area)
  * Ix ... in^4      (strong-axis moment of inertia -> I33)
  * Iy ... in^4      (weak-axis moment of inertia   -> I22)
  * J  ... in^4      (torsional constant)
  * d  ... in        (overall depth  -> drawing ``h``)
  * bf ... in        (flange width   -> drawing ``b``)

converted to SI with the same exact factors as before:

  1 in           = 0.0254 m                (exact, by definition)
  1 in^2         = 0.0254^2  = 6.4516e-4          m^2   (exact)
  1 in^4         = 0.0254^4  = 4.162314256e-7     m^4   (exact)

so every result is byte-identical to the pre-v1.21 table (Manual 15th ed.
Table 1-1, whose values for these 22 shapes equal v16.0) with ONE pinned
legacy value: the old table carried W10x49 d = 9.98 in where both v15.0
and v16.0 publish 10.0 in.  ``d`` only sets the drawing / panel-zone depth
``h``; it is pinned in :data:`_LEGACY_D_IN` so saved models and results do
not change (use the database import for the published depth).
"""

from __future__ import annotations

from typing import Dict, List

from skyframe.core.model import FrameSection

_IN = 0.0254                # m per inch (exact)
_IN2 = _IN ** 2             # m^2 per in^2
_IN4 = _IN ** 4             # m^4 per in^4

#: the historical library list (light to heavy); labels keep the old "x"
_NAMES = ("W8x31", "W8x40", "W10x33", "W10x49", "W12x26", "W12x40",
          "W12x65", "W14x30", "W14x48", "W14x90", "W16x36", "W16x57",
          "W18x50", "W18x76", "W21x62", "W21x93", "W24x76", "W24x104",
          "W27x94", "W30x116", "W33x130", "W36x150")

#: legacy depth (in) kept for byte-identical results (see module doc)
_LEGACY_D_IN = {"W10x49": 9.98}


def _imperial(name: str) -> tuple:
    """(A, Ix, Iy, J, d, bf) in inches from the AISC v16.0 database."""
    from skyframe.core.steel_db import shape
    a = shape("aisc", name.upper())["aisc"]
    return (a["A"], a["Ix"], a["Iy"], a["J"],
            _LEGACY_D_IN.get(name, a["d"]), a["bf"])


_CACHE: Dict[str, tuple] = {}


def _table() -> Dict[str, tuple]:
    if not _CACHE:
        _CACHE.update({n: _imperial(n) for n in _NAMES})
    return _CACHE


def __getattr__(attr: str):
    # ``_W_SHAPES_IMPERIAL`` (used by skyframe.core.modifiers) is built
    # lazily from the database on first access.
    if attr == "_W_SHAPES_IMPERIAL":
        return _table()
    raise AttributeError(attr)


def library_names() -> List[str]:
    """Available shape labels (library order: light to heavy)."""
    return list(_NAMES)


def library_properties(name: str) -> dict:
    """SI properties dict for one shape: A [m^2], I33/I22/J [m^4], b/h [m]."""
    if name not in _NAMES:
        raise KeyError(f"Unknown library section {name!r}; available: "
                       f"{', '.join(library_names())}") from None
    A, Ix, Iy, J, d, bf = _table()[name]
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
