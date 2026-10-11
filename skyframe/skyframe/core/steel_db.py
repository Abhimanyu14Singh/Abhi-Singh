"""Steel section databases (v1.21): loader API over ``skyframe/data/steel``.

Every ``<id>.json`` file in the package directory ``skyframe/data/steel``
is one standard (``aisc``, ``eurocode``, ``is808``, ``bs``, ``as_nzs`` ...);
files are discovered at runtime, so a database dropped in later is picked
up without code changes.  File format: CONTRACT "Steel section database
and parametric shapes" / the shared spec (``{"standard", "edition",
"source", "units", "shapes": [{"name", "family", "type", "dims",
"props", ...}]}``, SI units).

API (other modules and the HTTP layer code against exactly this)::

    standards()                               -> [{id, standard, edition, count, source}]
    families(standard_id)                     -> [family, ...]  (file order)
    shapes(standard_id, family=None, query=None) -> [record, ...]
    shape(standard_id, name)                  -> record   (KeyError if unknown)
    section_from_db(standard_id, name, material, section_name=None) -> FrameSection

Records are returned as deep copies; callers may mutate them freely.
Each file is parsed once (lazily) and cached for the process lifetime.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "steel"

#: preferred listing order; unknown ids follow alphabetically
_ORDER = ("aisc", "eurocode", "is808", "bs", "as_nzs")


def _ids() -> List[str]:
    if not DATA_DIR.is_dir():
        return []
    ids = sorted(p.stem for p in DATA_DIR.glob("*.json"))
    return ([i for i in _ORDER if i in ids]
            + [i for i in ids if i not in _ORDER])


@lru_cache(maxsize=None)
def _load(sid: str) -> dict:
    path = DATA_DIR / f"{sid}.json"
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", sid or "") or not path.is_file():
        raise KeyError(f"unknown steel database {sid!r}; available: "
                       f"{', '.join(_ids()) or '(none)'}")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    shapes = data.get("shapes") or []
    data["_index"] = {s["name"]: i for i, s in enumerate(shapes)}
    # case-insensitive / alias lookup ("W14x90", EDI names)
    alias: Dict[str, int] = {}
    for i, s in enumerate(shapes):
        for nm in (s["name"], s.get("edi")):
            if nm:
                alias.setdefault(_norm(nm), i)
    data["_alias"] = alias
    return data


def _norm(name: str) -> str:
    return re.sub(r"\s+", "", str(name)).upper()


def clear_cache() -> None:
    """Forget parsed files (tests that add a database at runtime)."""
    _load.cache_clear()


def standard_id(sid: str) -> str:
    """Canonical id for ``sid`` (case-insensitive id or standard name)."""
    ids = _ids()
    if sid in ids:
        return sid
    low = str(sid or "").strip().lower()
    for i in ids:
        if i.lower() == low:
            return i
    for i in ids:
        if str(_load(i).get("standard", "")).lower() == low:
            return i
    raise KeyError(f"unknown steel database {sid!r}; available: "
                   f"{', '.join(ids) or '(none)'}")


def standards() -> List[dict]:
    """``[{id, standard, edition, count, source}]`` of every database file."""
    out = []
    for i in _ids():
        d = _load(i)
        out.append({"id": i, "standard": d.get("standard", i),
                    "edition": d.get("edition", ""),
                    "count": len(d.get("shapes") or []),
                    "source": d.get("source", "")})
    return out


def families(standard: str) -> List[str]:
    """Families of one database in first-appearance (file) order."""
    seen: Dict[str, None] = {}
    for s in _load(standard_id(standard)).get("shapes") or []:
        seen.setdefault(s.get("family", ""), None)
    return list(seen)


def shapes(standard: str, family: Optional[str] = None,
           query: Optional[str] = None) -> List[dict]:
    """Records of one database, optionally filtered by family (exact,
    case-insensitive) and a search query (every whitespace-separated term
    must occur in the name or EDI alias, case- and space-insensitive)."""
    data = _load(standard_id(standard))
    fam = family.lower() if family else None
    terms = [_norm(t) for t in str(query or "").split() if t.strip()]
    out = []
    for s in data.get("shapes") or []:
        if fam and str(s.get("family", "")).lower() != fam:
            continue
        if terms:
            hay = _norm(s["name"]) + " " + _norm(s.get("edi") or "")
            if not all(t in hay for t in terms):
                continue
        out.append(copy.deepcopy(s))
    return out


def shape(standard: str, name: str) -> dict:
    """One record by its published name (exact first, then case/space
    insensitive and EDI alias).  KeyError when unknown."""
    data = _load(standard_id(standard))
    idx = data["_index"].get(name)
    if idx is None:
        idx = data["_alias"].get(_norm(name))
    if idx is None:
        raise KeyError(f"{data.get('standard', standard)}: unknown shape "
                       f"{name!r}")
    return copy.deepcopy(data["shapes"][idx])


def section_from_db(standard: str, name: str, material: str,
                    section_name: Optional[str] = None):
    """A ready-to-add :class:`FrameSection` for one database shape.

    Tabulated A, I33, I22, J and the shear areas As2 / As3 (formulas in
    :data:`skyframe.core.shape_props.SHEAR_AREA_FORMULAS`; setting them
    turns on Timoshenko shear deformation for the member, as ETABS does by
    default); drawing ``b`` / ``h`` = outline width / depth from the
    dimensions; ``shape`` = record type, ``dims`` = record dims (SI),
    ``db_ref = {"standard", "name"}``; angles also carry ``principal``
    ``{I_major, I_minor, alpha}`` (analysis on leg axes unless the section's
    ``use_principal`` is set - see :mod:`skyframe.core.section_ext`).
    """
    from skyframe.core.model import FrameSection
    from skyframe.core.shape_props import shape_properties
    sid = standard_id(standard)
    rec = shape(sid, name)
    p = rec["props"]
    dims = dict(rec.get("dims") or {})
    num_dims = {k: v for k, v in dims.items() if not isinstance(v, str)}
    try:
        geo = shape_properties(rec["type"], num_dims)
        b, h = geo["b"], geo["h"]
    except (ValueError, KeyError):
        b = float(num_dims.get("bf") or num_dims.get("b")
                  or num_dims.get("D") or 0.0)
        h = float(num_dims.get("d") or num_dims.get("h")
                  or num_dims.get("D") or 0.0)
    J = p.get("J")
    if J is None:                       # a database without J: thin-walled
        J = shape_properties(rec["type"], num_dims)["J"]
    sec = FrameSection(name=section_name or rec["name"], material=material,
                       A=float(p["A"]), I33=float(p["I33"]),
                       I22=float(p["I22"]), J=float(J), b=float(b),
                       h=float(h))
    if p.get("As2") is not None:
        sec.As2 = float(p["As2"])
    if p.get("As3") is not None:
        sec.As3 = float(p["As3"])
    sec.shape = rec["type"]
    sec.dims = dims
    sec.db_ref = {"standard": sid, "name": rec["name"]}
    if all(p.get(k) is not None for k in ("I_major", "I_minor", "alpha")):
        sec.principal = {k: float(p[k]) for k in ("I_major", "I_minor",
                                                  "alpha")}
    return sec


def import_sections(model, standard: str, names: List[str],
                    material: str) -> List[str]:
    """Add each named database shape to ``model`` (section name = shape
    name; an existing section of that name is left untouched).  Returns
    the names actually added.  ValueError for an unknown material,
    KeyError for an unknown shape (nothing is added in that case)."""
    if material not in model.materials:
        raise ValueError(f"unknown material {material!r}")
    secs = [section_from_db(standard, n, material) for n in names]
    added = []
    for s in secs:
        if s.name in model.sections:
            continue
        model.add_section(s)
        added.append(s.name)
    return added
