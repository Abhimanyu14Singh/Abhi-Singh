"""Steel-shape lookup for built-up sections (import-guarded adapter).

The built-up section editor and :mod:`skyframe.core.builtup` look shapes up
through this module ONLY.  It codes against the v1.21 loader API of
:mod:`skyframe.core.steel_db` (``standards()``, ``families(id)``,
``shapes(id, family, query)``, ``shape(id, name)``; records
``{name, family, type, dims, props}`` in SI) and, while that module is not
installed, falls back to a tiny local STUB so built-up sections work on
their own:

* ``aisc`` / family ``W`` — the 22 W-shapes already shipped in
  :mod:`skyframe.core.sections_library` (A, Ix, Iy, J, d, bf — AISC Manual
  15th ed. Table 1-1) with the web/flange thicknesses tw/tf of
  :data:`skyframe.core.modifiers.W_SHAPE_TW_TF_IN` (same table).  ``props``
  are the TABULATED A/I33/I22/J.
* ``aisc`` / family ``L`` — a few angles whose dimensions are the
  designation itself (``L4X4X1/2`` = 4 in x 4 in legs, 1/2 in thick).  No
  tabulated properties: the built-up engine computes them from the
  fillet-free geometry (``"computed": [...]`` marks that).

Nothing here invents tabulated values.  When ``steel_db`` is merged the
stub is bypassed automatically (``SOURCE`` says which one is active).
"""

from __future__ import annotations

from typing import List, Optional

try:                                        # v1.21 steel database (parallel)
    from skyframe.core import steel_db as _sdb   # type: ignore
except Exception:                           # pragma: no cover - until merged
    _sdb = None

SOURCE = "steel_db" if _sdb is not None else "stub"

_IN = 0.0254


def _stub_records() -> List[dict]:
    from skyframe.core.modifiers import W_SHAPE_TW_TF_IN
    from skyframe.core.sections_library import (_W_SHAPES_IMPERIAL,
                                                library_properties)
    out = []
    for lab in _W_SHAPES_IMPERIAL:
        p = library_properties(lab)
        tw, tf = W_SHAPE_TW_TF_IN[lab]
        A, Ix, Iy, J, d, bf = _W_SHAPES_IMPERIAL[lab]
        out.append({
            "name": lab.upper(), "family": "W", "type": "I",
            "dims": {"d": d * _IN, "bf": bf * _IN, "tf": tf * _IN,
                     "tw": tw * _IN},
            "props": {"A": p["A"], "I33": p["I33"], "I22": p["I22"],
                      "J": p["J"]},
        })
    # angles: dims are the designation (legs x thickness, inches)
    for (d, b, t, lab) in ((3, 3, 0.25, "L3X3X1/4"), (3, 3, 0.375, "L3X3X3/8"),
                           (4, 4, 0.375, "L4X4X3/8"), (4, 4, 0.5, "L4X4X1/2"),
                           (5, 5, 0.5, "L5X5X1/2"), (6, 6, 0.5, "L6X6X1/2"),
                           (6, 4, 0.5, "L6X4X1/2"), (5, 3, 0.375, "L5X3X3/8")):
        out.append({"name": lab, "family": "L", "type": "angle",
                    "dims": {"d": d * _IN, "b": b * _IN, "t": t * _IN},
                    "props": {}, "computed": ["A", "I33", "I22", "J"]})
    return out


_STUB: Optional[List[dict]] = None


def _stub() -> List[dict]:
    global _STUB
    if _STUB is None:
        _STUB = _stub_records()
    return _STUB


def standards() -> List[dict]:
    if _sdb is not None:
        return list(_sdb.standards())
    return [{"id": "aisc", "standard": "AISC",
             "edition": "stub: sections_library W-shapes + designation angles",
             "count": len(_stub())}]


def families(standard_id: str) -> List[str]:
    if _sdb is not None:
        return list(_sdb.families(standard_id))
    _check_std(standard_id)
    seen: List[str] = []
    for r in _stub():
        if r["family"] not in seen:
            seen.append(r["family"])
    return seen


def shapes(standard_id: str, family: Optional[str] = None,
           query: Optional[str] = None) -> List[dict]:
    if _sdb is not None:
        return list(_sdb.shapes(standard_id, family=family, query=query))
    _check_std(standard_id)
    q = (query or "").strip().upper().replace(" ", "")
    return [r for r in _stub()
            if (not family or r["family"] == family)
            and (not q or q in r["name"].upper().replace(" ", ""))]


def shape(standard_id: str, name: str) -> dict:
    if _sdb is not None:
        return _sdb.shape(standard_id, name)
    _check_std(standard_id)
    key = str(name).upper().replace(" ", "")
    for r in _stub():
        if r["name"].upper().replace(" ", "") == key:
            return r
    raise KeyError(f"unknown shape {name!r} in standard {standard_id!r}")


def _check_std(standard_id: str) -> None:
    if str(standard_id).lower() != "aisc":
        raise KeyError(f"unknown steel standard {standard_id!r} (stub "
                       "database: only 'aisc')")
