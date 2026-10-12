"""Built-in material libraries (v1.21): US (ASTM/AISC/ACI), Eurocode, India
(IS), UK (BS EN / BS) and Australia/NZ (AS/NZS).

Data: ``skyframe/data/materials/<standard>.json`` (package data).  Records are
in SI base units -- ``E``/``G``/``fy``/``fu``/``fc`` Pa, ``unit_weight`` N/m^3,
``mass_density`` kg/m^3, ``alpha`` 1/K -- and every value cites its clause or
table in ``source`` / ``formula`` (see CONTRACT "v1.21 material library").

Mapping onto the existing :class:`~skyframe.core.model.Material` (kN, m,
tonne, kPa) is a pure unit conversion, no new Material fields:

=================  ===============================================
record             Material
=================  ===============================================
E, fy, fu, fc      value / 1000 (Pa -> kPa)
unit_weight        value / 1000 (N/m^3 -> kN/m^3)
mass_density       value / 1000 (kg/m^3 -> tonne/m^3)
nu, alpha          unchanged
G                  Material.G = E / 2(1 + nu) (records store the same)
kind               material_type (``cold_formed`` -> ``coldformed``)
rebar / tendon     symmetry ``uniaxial`` (everything else isotropic)
Ry                 record ``Ry`` when tabulated, else the Material default
=================  ===============================================

``stress_strain`` stays ``None`` (engine default laws, which read fc / fy).
The library is never consulted unless something imports from it, so models
that do not use it are byte-identical.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from skyframe.core.model import Material

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "materials"

STANDARD_IDS = ("astm_aci", "eurocode", "is", "bs", "as_nzs")
# Wave C hook: Chinese / Canadian / Japanese / Korean / Russian libraries
# (data/materials/{gb,csa,jis,ks,gost}.json, tools/build_wavec_materials.py)
STANDARD_IDS += ("gb", "csa", "jis", "ks", "gost")
KINDS = ("steel", "concrete", "rebar", "tendon", "aluminum", "cold_formed")
KIND_TO_TYPE = {"steel": "steel", "concrete": "concrete", "rebar": "rebar",
                "tendon": "tendon", "aluminum": "aluminum",
                "cold_formed": "coldformed"}
# display swatches: the same colours the UI stamps per material type
KIND_COLOR = {"steel": "#3b6ea5", "concrete": "#8a8f98", "rebar": "#c0603a",
              "tendon": "#9a7bce", "aluminum": "#9aa3ad",
              "cold_formed": "#5a8fa5"}
CLASH_MODES = ("suffix", "replace", "error")


@lru_cache(maxsize=None)
def _load(standard_id: str) -> dict:
    if standard_id not in STANDARD_IDS:
        raise KeyError(f"unknown material standard {standard_id!r}; "
                       f"choose from {list(STANDARD_IDS)}")
    with open(DATA_DIR / f"{standard_id}.json", encoding="utf-8") as fh:
        return json.load(fh)


def standards() -> List[dict]:
    """``[{id, standard, edition, count, kinds: {kind: n}}]``."""
    out = []
    for sid in STANDARD_IDS:
        d = _load(sid)
        kinds: Dict[str, int] = {}
        for r in d["materials"]:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        out.append({"id": sid, "standard": d["standard"],
                    "edition": d["edition"], "count": len(d["materials"]),
                    "kinds": kinds, "notes": d.get("notes", "")})
    return out


def materials(standard_id: str, kind: Optional[str] = None,
              query: Optional[str] = None) -> List[dict]:
    """Records of one standard (deep copies), optionally filtered by kind and
    a case-insensitive substring of name / group."""
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}, got {kind!r}")
    q = (query or "").strip().lower()
    out = []
    for r in _load(standard_id)["materials"]:
        if kind is not None and r["kind"] != kind:
            continue
        if q and q not in r["name"].lower() and q not in r["group"].lower():
            continue
        out.append(copy.deepcopy(r))
    return out


def material(standard_id: str, name: str) -> dict:
    """One record by its published name (``KeyError`` when unknown)."""
    for r in _load(standard_id)["materials"]:
        if r["name"] == name:
            return copy.deepcopy(r)
    raise KeyError(f"no material {name!r} in the {standard_id!r} library")


def _kpa(v):
    return None if v is None else v / 1000.0


def record_to_material(rec: dict, name: Optional[str] = None,
                       standard_id: Optional[str] = None) -> Material:
    """Map one library record onto a :class:`Material` (SI -> kN/m/tonne)."""
    kind = rec["kind"]
    std = _load(standard_id)["standard"] if standard_id else ""
    note = f"{std + ' library: ' if std else 'Library: '}{rec['name']} — {rec['source']}"
    kw = {}
    if rec.get("Ry") is not None:
        kw["Ry"] = float(rec["Ry"])
    return Material(
        name=name or rec["name"],
        E=rec["E"] / 1000.0,
        nu=float(rec["nu"]),
        unit_weight=rec["unit_weight"] / 1000.0,
        material_type=KIND_TO_TYPE[kind],
        symmetry="uniaxial" if kind in ("rebar", "tendon") else "isotropic",
        mass_density=rec["mass_density"] / 1000.0,
        alpha=float(rec["alpha"]),
        fc=_kpa(rec.get("fc")), fy=_kpa(rec.get("fy")), fu=_kpa(rec.get("fu")),
        color=KIND_COLOR[kind], notes=note, **kw)


def material_from_db(standard_id: str, name: str,
                     material_name: Optional[str] = None) -> Material:
    """A fresh :class:`Material` for library record ``name``."""
    return record_to_material(material(standard_id, name), material_name,
                              standard_id)


def listing(standard_id: str, kind: Optional[str] = None,
            query: Optional[str] = None) -> List[dict]:
    """Records plus ``material`` (the exact ``Material.to_dict()`` an import
    stores) -- the shape of ``GET /api/material_db/materials``."""
    out = []
    for r in materials(standard_id, kind, query):
        r["material"] = record_to_material(r, None, standard_id).to_dict()
        out.append(r)
    return out


def _free_name(taken, base: str) -> str:
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def import_into(model, standard_id: str, names: List[str],
                on_clash: str = "suffix",
                rename: Optional[Dict[str, str]] = None) -> List[dict]:
    """Add library materials to ``model.materials``.

    ``on_clash``: ``suffix`` (default) -> ``NAME-2``, ``NAME-3`` ...;
    ``replace`` -> overwrite the existing material (sections keep pointing at
    the name); ``error`` -> ``ValueError`` and nothing is added.  ``rename``
    maps a record name to the model name to use.  All names are resolved
    before anything is written (atomic).  Returns ``[{name, as, replaced}]``.
    """
    if on_clash not in CLASH_MODES:
        raise ValueError(f"on_clash must be one of {list(CLASH_MODES)}")
    if not isinstance(names, list) or not names:
        raise ValueError("'names' must be a non-empty list")
    rename = rename or {}
    taken = set(model.materials)
    plan = []
    for nm in names:
        rec = material(standard_id, nm)          # KeyError when unknown
        target = str(rename.get(nm) or nm)
        replaced = False
        if target in taken:
            if on_clash == "error":
                raise ValueError(f"material {target!r} already exists")
            if on_clash == "replace" and target in model.materials:
                replaced = True
            else:
                target = _free_name(taken, target)
        taken.add(target)
        plan.append((rec, target, replaced))
    mats = [record_to_material(rec, target, standard_id)
            for rec, target, _ in plan]
    for m in mats:
        model._validate_material(m)
    out = []
    for (rec, target, replaced), m in zip(plan, mats):
        model.materials[target] = m
        out.append({"name": rec["name"], "as": target, "replaced": replaced})
    return out
